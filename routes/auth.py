from functools import wraps

from flask import (
    Blueprint, current_app, render_template, request, redirect,
    url_for, session,
)
from itsdangerous import BadSignature, URLSafeTimedSerializer

import google_auth

auth_bp = Blueprint('auth', __name__)

# Holds the in-flight login (state, nonce, PKCE verifier, post-login path)
# between /login/google and its callback. Signed with SECRET_KEY, scoped to
# that path so it never rides along on normal traffic, and SameSite=Lax so
# the browser still sends it on Google's top-level redirect back to us.
_FLOW_COOKIE      = 'traverse_oauth'
_FLOW_COOKIE_PATH = '/login/google'
_FLOW_SALT        = 'traverse.google-flow'

# ?error= codes the login page knows how to explain. Anything else → 'failed'.
_LOGIN_ERRORS = {
    'not_configured': 'Google sign-in isn’t set up on this server yet.',
    'denied':         'Sign-in was cancelled.',
    'expired':        'That sign-in attempt expired. Please try again.',
    'unverified':     'That Google account’s email address isn’t verified.',
    'not_allowed':    'That Google account isn’t allowed to sign in.',
    'failed':         'Sign-in failed. Please try again.',
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_next(target):
    """Accept only relative paths — blocks open-redirect attacks. Browsers
    read `\\` as `/`, so `/\\evil.example` would be `//evil.example`."""
    if (target and target.startswith('/') and not target.startswith('//')
            and '\\' not in target and target.isprintable()):
        return target
    return url_for('dashboard.index')


# gunicorn listens on 127.0.0.1, so nginx on this box is the only hop whose
# X-Forwarded-For we believe.
_TRUSTED_PROXIES = ('127.0.0.1', '::1')


def client_ip():
    """The visitor's address, for audit rows, logs and alerts.

    nginx's $proxy_add_x_forwarded_for *appends* the address it saw, so only
    the LAST entry is real — anything before it is whatever the browser sent
    (taking the first entry let anyone write a fake IP into the audit log).
    The header is only read when the request came from nginx itself."""
    peer = request.remote_addr or ''
    if peer in _TRUSTED_PROXIES:
        hops = [h.strip() for h in request.headers.get('X-Forwarded-For', '').split(',')]
        if hops[-1]:
            return hops[-1]
    return peer or 'unknown'


def _flow_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt=_FLOW_SALT)


def _notify_login_success(email):
    try:
        from notifications import send_notification
        send_notification(
            'login_success',
            f'🔐 {email} signed into traverse dashboard (IP `{client_ip()}`)',
            severity='info',
        )
    except Exception:
        pass


def _notify_login_failed(email):
    try:
        from notifications import send_notification
        send_notification(
            'login_failed',
            f'🚫 Google account {email} was refused sign-in to traverse '
            f'(IP `{client_ip()}`)',
            severity='warning',
        )
    except Exception:
        pass


def enforce_allowlist():
    """App-wide before_request: drop any session whose email is no longer on
    TRAVERSE_ADMIN_EMAILS (or never had one — password-era sessions). Every
    `session.get('logged_in')` check downstream then sees an anonymous
    visitor, so removing an email and restarting takes effect immediately.
    Static files are skipped: touching the session adds `Vary: Cookie`, which
    would defeat nginx's `immutable` caching of /static/."""
    if request.path.startswith('/static/'):
        return
    if session.get('logged_in') and not google_auth.is_allowed(session.get('email')):
        session.clear()


# ── Decorator ─────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth.login', next=request.path))
        return f(*args, **kwargs)
    return decorated


# ── Login page ────────────────────────────────────────────────────────────────

@auth_bp.route('/login')
def login():
    if session.get('logged_in'):
        return redirect(url_for('dashboard.index'))
    code = request.args.get('error', '')
    error = _LOGIN_ERRORS.get(code, _LOGIN_ERRORS['failed']) if code else None
    return render_template(
        'login.html',
        error      = error,
        next_url   = request.args.get('next', ''),
        configured = google_auth.is_configured(),
    )


# ── Google sign-in: leg 1 ─────────────────────────────────────────────────────

@auth_bp.route('/login/google')
def google_start():
    # A plain GET link, not a form POST: CSP `form-action 'self'` would block
    # a form whose response redirects to accounts.google.com.
    if not google_auth.is_configured():
        return redirect(url_for('auth.login', error='not_configured'))
    flow, url = google_auth.begin_flow()
    flow['next'] = _safe_next(request.args.get('next'))
    resp = redirect(url)
    resp.set_cookie(
        _FLOW_COOKIE, _flow_serializer().dumps(flow),
        max_age  = google_auth.FLOW_TTL,
        path     = _FLOW_COOKIE_PATH,
        secure   = current_app.config['SESSION_COOKIE_SECURE'],
        httponly = True,
        samesite = 'Lax',
    )
    return resp


# ── Google sign-in: leg 2 ─────────────────────────────────────────────────────

@auth_bp.route('/login/google/callback')
def google_callback():
    """Google sends the browser back here with ?code&state (or ?error).
    Success → session + a hop to the dashboard; anything else → /login?error=."""
    ip = client_ip()
    try:
        flow = _flow_serializer().loads(
            request.cookies.get(_FLOW_COOKIE, ''), max_age=google_auth.FLOW_TTL)
    except BadSignature:   # also covers SignatureExpired
        flow = None

    def fail(reason, detail, email=None):
        current_app.logger.warning(
            'google sign-in failed (%s) from %s: %s', reason, ip, detail)
        # A callback hit with no flow cookie of ours isn't a sign-in attempt
        # from a browser that started one, so it doesn't earn an audit row.
        if flow is not None:
            try:
                from database import audit
                audit('auth.login_failed', actor_ip=ip,
                      details=f'google:{reason}' + (f' {email}' if email else ''))
            except Exception:
                pass
        # Only alert when a real Google identity was refused — those take a
        # completed Google login each, so they can't be used to spam alerts.
        if email:
            _notify_login_failed(email)
        resp = redirect(url_for('auth.login', error=reason))
        resp.delete_cookie(_FLOW_COOKIE, path=_FLOW_COOKIE_PATH)
        return resp

    if flow is None:
        return fail('expired', 'missing, tampered or expired flow cookie')
    # The user backed out on Google's side (access_denied) or Google refused.
    if request.args.get('error'):
        return fail('denied', request.args.get('error'))
    # State must match the cookie this browser got in google_start — that's
    # what stops someone completing *their* login in *your* browser.
    if not google_auth.state_matches(flow, request.args.get('state')):
        return fail('expired', 'state mismatch')
    code = request.args.get('code', '')
    if not code:
        return fail('failed', 'missing code')
    if not google_auth.is_configured():
        return fail('not_configured', 'config changed mid-login')

    try:
        email = google_auth.complete_flow(code, flow)
    except google_auth.GoogleAuthError as e:
        return fail(e.code, str(e), e.email)

    session.clear()
    session.permanent = True
    session['logged_in'] = True
    session['email'] = email
    _notify_login_success(email)
    try:
        from database import audit
        audit('auth.login_success', actor_ip=ip, details=f'google:{email}')
    except Exception:
        pass
    # Not a 302: the session cookie is SameSite=Strict, and a redirect chain
    # that started on accounts.google.com is cross-site all the way down, so
    # the browser would withhold the brand-new cookie and bounce us back to
    # /login. A page that navigates itself starts a fresh same-site request.
    resp = current_app.make_response(render_template(
        'login_complete.html', next_url=_safe_next(flow.get('next'))))
    resp.delete_cookie(_FLOW_COOKIE, path=_FLOW_COOKIE_PATH)
    return resp


# ── Internal: session check for nginx auth_request ────────────────────────────

@auth_bp.route('/_internal/auth-check')
def auth_check():
    # 200 if the caller has a valid Traverse session, 401 otherwise. Used by
    # nginx `auth_request` to gate reverse-proxied subpaths (e.g. /pihole/)
    # behind the Traverse login. Never redirects — auth_request needs a clean
    # status code, not a 302.
    if session.get('logged_in'):
        return '', 200
    return '', 401


# ── Logout ────────────────────────────────────────────────────────────────────

@auth_bp.route('/logout', methods=['POST'])
def logout():
    try:
        from database import audit
        audit('auth.logout', actor_ip=client_ip(),
              details=f"google:{session['email']}" if session.get('email') else None)
    except Exception:
        pass
    session.clear()
    return redirect(url_for('auth.login'))
