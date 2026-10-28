"""Shared pytest fixtures.

Each test gets a fresh on-disk SQLite DB in a tmp dir, a Flask test client
with the WireGuard CLI and alert poller stubbed out, and a logged-in
session ready for routes behind @login_required.
"""

import os
import sys
import sqlite3
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Pin env vars to deterministic values and route the DB to tmp_path.

    autouse so every test gets it without having to ask. Runs before
    `app` so that load_dotenv() + module-level reads see the test values.
    """
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setenv('SECRET_KEY', 'test-secret-key-not-for-prod-' + 'x' * 16)
    # Google sign-in: a complete config so /login/google works; tests that
    # need it unset override these. Set explicitly so the real .env (found by
    # load_dotenv() walking up from the repo) can't leak in.
    monkeypatch.setenv('TRAVERSE_GOOGLE_CLIENT_ID', 'test-client.apps.googleusercontent.com')
    monkeypatch.setenv('TRAVERSE_GOOGLE_CLIENT_SECRET', 'test-client-secret')
    monkeypatch.setenv('TRAVERSE_PUBLIC_URL', 'https://traverse.test')
    monkeypatch.setenv('TRAVERSE_ADMIN_EMAILS', 'admin@example.com')
    monkeypatch.setenv('WG_INTERFACE', 'wg-test')
    monkeypatch.setenv('WG_SUBNET', '10.99.0.0/24')
    monkeypatch.setenv('WG_SERVER_VPN_IP', '10.99.0.1')
    monkeypatch.setenv('WG_ENDPOINT', 'test.example')
    monkeypatch.setenv('WG_PORT', '51820')
    monkeypatch.setenv('WG_DNS', '1.1.1.1')
    # Set to empty so module-level load_dotenv() in route files doesn't
    # repopulate these from the real .env after the test resets them.
    monkeypatch.setenv('PIHOLE_ENABLED', '')
    monkeypatch.setenv('MAX_PEERS', '')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '')
    monkeypatch.setenv('NOTIFY_DISCORD_WEBHOOK', '')
    # Empty == unset for /api/public-stats (→ 404); tests that need a
    # token set their own. Keeps the real .env token out of the suite.
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', '')

    # Force database module to re-resolve the path for every test.
    import database
    database._DB_PATH = None

    # Google's signing keys are cached per process; start every test cold.
    import google_auth
    monkeypatch.setattr(google_auth, '_jwks', {'keys': {}, 'fetched_at': 0.0})

    yield


def _stub_wg(monkeypatch):
    """Patch both wireguard.* and the local bindings in modules that did
    `from wireguard import X` at import time. Without the per-module
    patches the routes still hit the real `wg` CLI."""
    import wireguard

    _ip_counter = {'n': 1}
    def _next_ip():
        _ip_counter['n'] += 1
        return f'10.99.0.{_ip_counter["n"]}'

    def _keypair():
        return ('priv-' + os.urandom(4).hex(),
                'pub-' + os.urandom(4).hex(),
                'psk-' + os.urandom(4).hex())

    stubs = {
        'generate_keypair':         _keypair,
        'add_peer_to_interface':    lambda *a, **kw: None,
        'remove_peer_from_interface': lambda *a, **kw: None,
        'get_server_public_key':    lambda: 'server-pub-key-stub',
        'parse_wg_show':            lambda: {},
        'get_interface_status':     lambda: {'running': False, 'since': None},
        'get_next_vpn_ip':          _next_ip,
    }

    for name, fn in stubs.items():
        monkeypatch.setattr(wireguard, name, fn, raising=False)

    # Patch local bindings inside route modules that imported these names.
    import routes.peers as peers_route
    import routes.dashboard as dashboard_route
    for name, fn in stubs.items():
        if hasattr(peers_route, name):
            monkeypatch.setattr(peers_route, name, fn, raising=False)
        if hasattr(dashboard_route, name):
            monkeypatch.setattr(dashboard_route, name, fn, raising=False)


@pytest.fixture
def app(monkeypatch):
    """Build a fresh Flask app for each test with side effects stubbed."""
    # Don't let the alerts daemon thread start during tests.
    import alerts
    monkeypatch.setattr(alerts, 'start_alerts', lambda: None)

    # Reload wireguard so MAX_PEERS reflects whatever the test env set.
    import importlib, wireguard
    importlib.reload(wireguard)
    # routes.peers + routes.dashboard hold their own MAX_PEERS binding
    # (from-import); refresh those too.
    import routes.peers, routes.dashboard
    importlib.reload(routes.peers)
    importlib.reload(routes.dashboard)

    _stub_wg(monkeypatch)

    from app import create_app
    flask_app = create_app()
    flask_app.config['TESTING'] = True
    # SameSite=Strict + secure cookies prevent test_client from carrying
    # the session over HTTP. Loosen for tests only.
    flask_app.config['SESSION_COOKIE_SECURE'] = False
    flask_app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in_client(client):
    """Test client with a valid logged-in session."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['email'] = 'admin@example.com'
    return client


# ── Fake Google ──────────────────────────────────────────────────────────────
# Stands in for Google's token endpoint and JWKS so the real verification path
# in google_auth runs end to end. The test encodes what the "user" did at
# Google into the authorization `code`; the token endpoint checks client
# creds, redirect_uri and PKCE like Google does, then mints an RS256 ID token.

_GOOGLE_KEYS = {}


def _google_keys():
    if not _GOOGLE_KEYS:
        from cryptography.hazmat.primitives.asymmetric import rsa
        _GOOGLE_KEYS['real'] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        # A key Google never published — tokens signed with it must fail.
        _GOOGLE_KEYS['rogue'] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _GOOGLE_KEYS


class FakeGoogle:
    def __init__(self):
        import jwt
        keys = _google_keys()
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(keys['real'].public_key(), as_dict=True)
        jwk.update(kid='test-kid', alg='RS256', use='sig')
        self.jwks = {'keys': [jwk]}
        self.calls = {'jwks': 0, 'token': 0}

    def http_json(self, url, form=None):
        import base64, hashlib, json, os, time
        import jwt
        import google_auth
        if url == google_auth.JWKS_URL:
            self.calls['jwks'] += 1
            return 200, self.jwks
        assert url == google_auth.TOKEN_URL, url
        self.calls['token'] += 1
        if (form['client_id'] != os.environ['TRAVERSE_GOOGLE_CLIENT_ID'] or
                form['client_secret'] != os.environ['TRAVERSE_GOOGLE_CLIENT_SECRET']):
            return 401, {'error': 'invalid_client'}
        if form['grant_type'] != 'authorization_code':
            return 400, {'error': 'unsupported_grant_type'}
        try:
            c = json.loads(base64.urlsafe_b64decode(form['code'] + '=='))
        except ValueError:
            return 400, {'error': 'invalid_grant'}
        if form['redirect_uri'] != c['redirect_uri']:
            return 400, {'error': 'redirect_uri_mismatch'}
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(form['code_verifier'].encode()).digest()).rstrip(b'=').decode()
        if challenge != c['challenge']:
            return 400, {'error': 'invalid_grant'}
        now = int(time.time())
        claims = {
            'iss': 'https://accounts.google.com',
            'aud': os.environ['TRAVERSE_GOOGLE_CLIENT_ID'],
            'sub': '1093847561234',
            'email': c['email'],
            'email_verified': True,
            'nonce': c['nonce'],
            'iat': now,
            'exp': now + 3600,
        }
        claims.update(c.get('claims') or {})
        for k in c.get('drop') or ():
            claims.pop(k, None)
        key = _google_keys()['rogue' if c.get('rogue') else 'real']
        token = jwt.encode(claims, key, algorithm=c.get('alg', 'RS256'),
                           headers={'kid': c.get('kid', 'test-kid')})
        return 200, {'id_token': token, 'access_token': 'unused'}

    @staticmethod
    def start(client, next_url=None):
        """GET /login/google; return the Google authorize URL's query."""
        from urllib.parse import urlparse, parse_qs
        resp = client.get('/login/google',
                          query_string={'next': next_url} if next_url else None)
        assert resp.status_code == 302, resp.status_code
        q = {k: v[0] for k, v in parse_qs(urlparse(resp.headers['Location']).query).items()}
        return resp, q

    @staticmethod
    def make_code(q, email='admin@example.com', **extra):
        import base64, json
        payload = {'email': email, 'nonce': q['nonce'], 'challenge': q['code_challenge'],
                   'redirect_uri': q['redirect_uri'], **extra}
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=').decode()

    def login(self, client, email='admin@example.com', next_url=None, **extra):
        """Run the whole browser side of a sign-in; return the callback response."""
        _, q = self.start(client, next_url)
        code = self.make_code(q, email, **extra)
        return client.get('/login/google/callback', query_string={'code': code, 'state': q['state']})


@pytest.fixture
def fake_google(monkeypatch):
    import google_auth
    fg = FakeGoogle()
    monkeypatch.setattr(google_auth, '_http_json', fg.http_json)
    return fg
