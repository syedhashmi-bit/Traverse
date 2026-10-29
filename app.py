import os
import secrets
from datetime import timedelta
from flask import Flask, g, render_template, request, session
from dotenv import load_dotenv

load_dotenv()


def create_app():
    app = Flask(__name__)

    # Fail fast if SECRET_KEY isn't set — a hardcoded fallback would let
    # anyone forge session cookies and bypass auth entirely.
    secret = os.getenv('SECRET_KEY')
    if not secret or secret == 'changeme-use-a-strong-random-secret':
        raise RuntimeError(
            'SECRET_KEY is required (set a strong random value in .env). '
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    app.secret_key = secret

    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Strict',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )

    from cache_ext import cache
    cache.init_app(app)

    from database import init_db, disable_expired_peers
    init_db()

    # Disable any peers that expired while the server was offline
    try:
        from wireguard import remove_peer_from_interface
        for p in disable_expired_peers():
            try:
                remove_peer_from_interface(p['public_key'])
            except Exception:
                pass
    except Exception:
        pass

    from routes.auth          import auth_bp
    from routes.dashboard     import dashboard_bp
    from routes.peers         import peers_bp
    from routes.settings      import settings_bp
    from routes.api           import api_bp
    from routes.map           import map_bp
    from routes.history       import history_bp
    from routes.alerts        import alerts_bp
    from routes.topology      import topology_bp
    from routes.logs          import logs_bp
    from routes.about         import about_bp
    from routes.port_forwards import pf_bp
    from routes.notifications import notifications_bp
    from routes.pwa           import pwa_bp
    from routes.audit         import audit_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(peers_bp,         url_prefix='/peers')
    app.register_blueprint(settings_bp,      url_prefix='/settings')
    app.register_blueprint(api_bp)
    app.register_blueprint(map_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(alerts_bp)
    app.register_blueprint(topology_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(about_bp)
    app.register_blueprint(pf_bp,            url_prefix='/port-forwards')
    app.register_blueprint(notifications_bp)
    app.register_blueprint(pwa_bp)
    app.register_blueprint(audit_bp)

    # Sign-in is Google-only. Missing config doesn't stop the boot (the
    # WireGuard poller and alerts still matter), but nobody can log in, so
    # say exactly what's missing.
    import google_auth
    for problem in google_auth.config_problems():
        app.logger.warning('Google sign-in not configured: %s', problem)

    from alerts import start_alerts
    start_alerts()

    # Read version once at startup
    _version = 'dev'
    try:
        _version_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'VERSION')
        if os.path.exists(_version_path):
            with open(_version_path) as _f:
                _version = _f.read().strip() or 'dev'
    except Exception:
        pass

    from routes.auth import enforce_allowlist
    app.before_request(enforce_allowlist)

    @app.before_request
    def _set_csp_nonce():
        # Fresh, unguessable nonce per request — both the CSP header and
        # every inline <script nonce="{{ csp_nonce }}"> reference this.
        # Dropping 'unsafe-inline' from script-src means any script that
        # doesn't carry this nonce simply won't execute, which is the
        # XSS-mitigation we want.
        g.csp_nonce = secrets.token_urlsafe(18)

    @app.context_processor
    def _inject_csp_nonce():
        return {'csp_nonce': getattr(g, 'csp_nonce', '')}

    @app.context_processor
    def inject_globals():
        # App chrome data (VPN subnet + endpoint, WireGuard up/down, peer and
        # unseen-alert counts, Pi-hole link) is for logged-in admins only.
        # Anonymous renders — /login, /offline, the public
        # 404/500 pages — get none of it, and none of it is computed (no
        # `wg`/systemctl call, no DB count) for an unauthenticated request.
        # Only base.html uses these keys, and base.html is never rendered
        # for an anonymous visitor (see _error_page below).
        if not session.get('logged_in'):
            return {}
        from wireguard import get_interface_status
        from database import count_unseen_alerts, count_peers
        try:
            unseen = count_unseen_alerts()
        except Exception:
            unseen = 0
        try:
            peer_total = count_peers()
        except Exception:
            peer_total = 0
        try:
            from database import has_seen_tour
            show_tour = not has_seen_tour(session.get('email'))
        except Exception:
            show_tour = False
        try:
            from notifications import is_any_channel_active
            notif_active = is_any_channel_active()
        except Exception:
            notif_active = False
        return {
            'wg_running':              get_interface_status()['running'],
            'unseen_alert_count':      unseen,
            'app_version':             _version,
            'app_peer_total':          peer_total,
            'app_wg_subnet':           os.getenv('WG_SUBNET', '10.9.0.0/24'),
            'app_wg_endpoint':         os.getenv('WG_ENDPOINT', ''),
            'app_pihole_url':          os.getenv('PIHOLE_WEB_URL', '/pihole'),
            'app_pihole_enabled':      bool(os.getenv('PIHOLE_ENABLED')),
            'notifications_active':    notif_active,
            'show_tour':               show_tour,
        }

    @app.before_request
    def csrf_origin_check():
        # Defence-in-depth on top of SameSite=Strict cookies: reject any
        # state-changing request whose Origin/Referer hostname doesn't
        # match this host. Compare hostnames only (ignore port) so default
        # ports and proxy hops don't trigger false positives.
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return
        from urllib.parse import urlparse

        # Build the set of hostnames we consider "us". request.host honors
        # X-Forwarded-Host when ProxyFix is enabled; otherwise it's the
        # value nginx forwarded as Host. SERVER_NAME is the configured
        # canonical host (if set).
        def _host_only(value: str) -> str:
            return (value or '').split(':', 1)[0].lower()

        allowed = {_host_only(request.host)}
        for h in (request.headers.get('X-Forwarded-Host', ''),
                  os.getenv('SERVER_NAME', '')):
            if h:
                allowed.add(_host_only(h))
        allowed.discard('')

        origin  = request.headers.get('Origin', '')
        referer = request.headers.get('Referer', '')
        if not (origin or referer):
            # No Origin/Referer at all — SameSite=Strict still blocks the
            # cross-site case in modern browsers, so allow.
            return
        for src in (origin, referer):
            if not src:
                continue
            try:
                hostname = (urlparse(src).hostname or '').lower()
            except Exception:
                hostname = ''
            if hostname and hostname in allowed:
                return
        return ('Forbidden: cross-origin request blocked', 403)

    @app.after_request
    def set_security_headers(resp):
        resp.headers.setdefault('X-Frame-Options', 'DENY')
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        # "same-origin" hides the Referer from cross-origin destinations
        # but keeps Origin/Referer intact for our own form posts. The
        # stricter "no-referrer" causes Chrome to send Origin: null on
        # POSTs, which breaks the CSRF origin check below.
        resp.headers.setdefault('Referrer-Policy', 'same-origin')
        resp.headers.setdefault(
            'Strict-Transport-Security',
            'max-age=31536000; includeSubDomains',
        )
        # script-src: 'self' + per-request nonce only. No 'unsafe-inline' →
        # any inline script must carry the matching nonce or the browser
        # refuses to execute it. style-src keeps 'unsafe-inline' because
        # the project relies on inline `style="..."` attributes in dense
        # admin views, and the XSS risk from style is far lower than scripts.
        nonce = getattr(g, 'csp_nonce', '')
        resp.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; "
            # Map tiles come from Esri (server.arcgisonline.com), with OSM
            # tiles as the fallback. Without these the Leaflet map renders
            # grey tiles forever.
            "img-src 'self' data: blob: https://server.arcgisonline.com "
            "https://*.openstreetmap.org; "
            "style-src 'self' 'unsafe-inline'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        # Don't let browsers/proxies cache authenticated HTML — peer detail
        # pages render private keys, and shared devices could leak them.
        if not request.path.startswith('/static/'):
            resp.headers.setdefault(
                'Cache-Control', 'no-store, no-cache, must-revalidate'
            )
        return resp

    def _error_page(code, title, message):
        # Logged-in admins keep the full layout (404.html / 500.html extend
        # base.html). Anonymous visitors get a bare standalone page: the
        # base.html chrome would show the VPN subnet, WireGuard state and
        # peer/alert counts to anyone who requests a missing URL.
        if session.get('logged_in'):
            return render_template(f'{code}.html'), code
        return render_template('error_public.html', code=code,
                               title=title, message=message), code

    @app.errorhandler(404)
    def not_found(e):
        return _error_page(404, 'Not Found', 'Page not found.')

    @app.errorhandler(405)
    def method_not_allowed(e):
        # /api/public-stats must be indistinguishable from an unknown URL on
        # every method. Its rule already accepts the common ones and 404s
        # them itself; this catches the rest (PROPFIND, MKCOL, ...), which
        # werkzeug would otherwise answer with 405 + an `Allow:` header.
        from routes.api import PUBLIC_STATS_PATH
        if request.path == PUBLIC_STATS_PATH:
            return not_found(e)
        return e

    @app.errorhandler(500)
    def server_error(e):
        return _error_page(500, 'Server Error', 'Something went wrong.')

    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
