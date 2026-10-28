"""Google sign-in — the only way into the dashboard.

Standard OIDC authorization-code flow: /login/google sends the browser to
Google with a random `state`, a `nonce` (bound into the ID token) and a PKCE
challenge; the callback swaps the code for an ID token server-to-server,
verifies its RS256 signature against Google's published keys plus its
audience / issuer / expiry / nonce, and lets the verified email in only if
it is on the .env allowlist:

  TRAVERSE_ADMIN_EMAILS   comma-separated Google account emails

Any other account — even a valid, verified Google login — is rejected.
There are no roles: every Traverse page can reveal peer private keys, so a
"read-only" login would still be full VPN access.

The per-attempt secrets (state, nonce, PKCE verifier, post-login path) ride
in a signed, short-lived cookie rather than server memory, because gunicorn
runs 2 workers and the callback can land on either one.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt

AUTH_URL      = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL     = 'https://oauth2.googleapis.com/token'
JWKS_URL      = 'https://www.googleapis.com/oauth2/v3/certs'
ISSUERS       = ('https://accounts.google.com', 'accounts.google.com')
CALLBACK_PATH = '/login/google/callback'

FLOW_TTL      = 10 * 60     # seconds a started login stays redeemable
_HTTP_TIMEOUT = 10
_JWKS_MAX_AGE = 60 * 60     # Google rotates keys over days; an hour is plenty
# Re-fetch Google's keys on an unknown `kid` (key rotation), but no more than
# once a minute so a garbage token can't make us hammer the endpoint.
_JWKS_REFETCH_MIN = 60


class GoogleAuthError(Exception):
    """A login that didn't make it. `code` is one of failed / unverified /
    not_allowed; `email` is set once Google has told us who it was."""

    def __init__(self, code, detail, email=None):
        super().__init__(detail)
        self.code = code
        self.email = email


# ── Config (env only) ─────────────────────────────────────────────────────────

def _parse_emails(raw):
    return [e.strip().lower() for e in (raw or '').split(',') if e.strip()]


def config():
    return {
        'client_id':     os.getenv('TRAVERSE_GOOGLE_CLIENT_ID', '').strip(),
        'client_secret': os.getenv('TRAVERSE_GOOGLE_CLIENT_SECRET', '').strip(),
        'public_url':    os.getenv('TRAVERSE_PUBLIC_URL', '').strip().rstrip('/'),
        'admins':        _parse_emails(os.getenv('TRAVERSE_ADMIN_EMAILS', '')),
    }


def config_problems():
    """Everything that stops sign-in from working, as human-readable lines.
    Empty list == ready."""
    c = config()
    problems = []
    if not c['client_id']:
        problems.append('TRAVERSE_GOOGLE_CLIENT_ID is unset')
    if not c['client_secret']:
        problems.append('TRAVERSE_GOOGLE_CLIENT_SECRET is unset')
    if not c['admins']:
        problems.append('TRAVERSE_ADMIN_EMAILS is empty — nobody could sign in')
    if not c['public_url'].startswith(('https://', 'http://localhost', 'http://127.0.0.1')):
        problems.append('TRAVERSE_PUBLIC_URL must be the https:// origin browsers use '
                        '(e.g. https://vpn.example.com)')
    return problems


def is_configured():
    return not config_problems()


def is_allowed(email):
    """True iff `email` is on the allowlist. Checked at login and again on
    every request, so removing an email and restarting locks it out even
    with a session cookie still in hand."""
    if not isinstance(email, str) or not email:
        return False
    return email.strip().lower() in config()['admins']


def redirect_uri():
    """Must match an "Authorized redirect URI" on the Google client exactly."""
    return config()['public_url'] + CALLBACK_PATH


# ── The two halves of the flow ────────────────────────────────────────────────

def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def begin_flow():
    """Start a login. Returns (flow, url): `flow` holds the per-attempt
    secrets the caller pins to the browser; `url` is where to send it."""
    flow = {
        'state':    secrets.token_urlsafe(32),
        'nonce':    secrets.token_urlsafe(32),
        'verifier': secrets.token_urlsafe(48),
    }
    challenge = _b64url(hashlib.sha256(flow['verifier'].encode('ascii')).digest())
    params = urllib.parse.urlencode({
        'client_id':             config()['client_id'],
        'redirect_uri':          redirect_uri(),
        'response_type':         'code',
        'scope':                 'openid email',
        'state':                 flow['state'],
        'nonce':                 flow['nonce'],
        'code_challenge':        challenge,
        'code_challenge_method': 'S256',
        # Always show the account chooser, so someone who picked the wrong
        # account (and got "not allowed") can pick another next time.
        'prompt':                'select_account',
    })
    return flow, f'{AUTH_URL}?{params}'


def state_matches(flow, state):
    expected = (flow or {}).get('state')
    if not isinstance(expected, str) or not isinstance(state, str) or not state:
        return False
    return hmac.compare_digest(expected.encode('utf-8'), state.encode('utf-8'))


def complete_flow(code, flow):
    """Code → verified, allowlisted email. Raises GoogleAuthError."""
    claims = _verify_id_token(_exchange_code(code, flow), flow['nonce'])
    email = claims.get('email')
    email = email.lower() if isinstance(email, str) else ''
    if not email or claims.get('email_verified') is not True:
        raise GoogleAuthError('unverified', 'Google account email is not verified',
                              email or None)
    if not is_allowed(email):
        raise GoogleAuthError('not_allowed',
                              f'{email} is not in TRAVERSE_ADMIN_EMAILS', email)
    return email


# ── HTTP to Google ────────────────────────────────────────────────────────────

def _http_json(url, form=None):
    """GET (or form-POST) `url` and return (status, parsed JSON or None).
    Network failures raise GoogleAuthError('failed')."""
    data = urllib.parse.urlencode(form).encode('ascii') if form is not None else None
    req = urllib.request.Request(url, data=data, headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
        e.close()
    except (urllib.error.URLError, OSError) as e:
        raise GoogleAuthError('failed', f'{url} unreachable: {e}')
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def _exchange_code(code, flow):
    c = config()
    status, body = _http_json(TOKEN_URL, {
        'code':          code,
        'client_id':     c['client_id'],
        'client_secret': c['client_secret'],
        'redirect_uri':  redirect_uri(),
        'grant_type':    'authorization_code',
        'code_verifier': flow['verifier'],
    })
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get('id_token'), str):
        why = f" ({body['error']})" if isinstance(body, dict) and body.get('error') else ''
        raise GoogleAuthError('failed', f'token endpoint returned {status}{why}')
    return body['id_token']


# ── Google's signing keys (cached per worker) ─────────────────────────────────

_jwks = {'keys': {}, 'fetched_at': 0.0}


def _refresh_jwks():
    status, body = _http_json(JWKS_URL)
    if status != 200 or not isinstance(body, dict):
        raise GoogleAuthError('failed', f'JWKS fetch returned {status}')
    keys = {}
    for jwk in body.get('keys') or []:
        if not isinstance(jwk, dict) or jwk.get('kty') != 'RSA' or not jwk.get('kid'):
            continue
        try:
            keys[jwk['kid']] = jwt.PyJWK(jwk, algorithm='RS256').key
        except jwt.PyJWTError:
            continue  # skip a malformed key rather than failing every login
    _jwks.update(keys=keys, fetched_at=time.monotonic())


def _signing_key(kid):
    age = time.monotonic() - _jwks['fetched_at']
    if not _jwks['fetched_at'] or age >= _JWKS_MAX_AGE or (
            kid not in _jwks['keys'] and age >= _JWKS_REFETCH_MIN):
        _refresh_jwks()
    return _jwks['keys'].get(kid)


def _verify_id_token(id_token, nonce):
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as e:
        raise GoogleAuthError('failed', f'ID token is not a JWT: {e}')
    if header.get('alg') != 'RS256' or not header.get('kid'):
        raise GoogleAuthError('failed', 'ID token is not an RS256 JWT')
    key = _signing_key(header['kid'])
    if key is None:
        raise GoogleAuthError('failed', f"unknown signing key {header['kid']}")
    try:
        claims = jwt.decode(
            id_token, key,
            algorithms=['RS256'],
            audience=config()['client_id'],
            issuer=ISSUERS,
            leeway=60,
            options={'require': ['exp', 'iat', 'iss', 'aud', 'sub']},
        )
    except jwt.PyJWTError as e:
        raise GoogleAuthError('failed', f'ID token rejected: {e}')
    token_nonce = claims.get('nonce')
    if not isinstance(token_nonce, str) or not hmac.compare_digest(
            token_nonce.encode('utf-8'), nonce.encode('utf-8')):
        raise GoogleAuthError('failed', 'nonce mismatch')
    return claims
