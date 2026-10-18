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
    monkeypatch.setenv('ADMIN_USERNAME', 'admin')
    monkeypatch.setenv('ADMIN_PASSWORD', 'hunter2')
    monkeypatch.setenv('WG_INTERFACE', 'wg-test')
    monkeypatch.setenv('WG_SUBNET', '10.99.0.0/24')
    monkeypatch.setenv('WG_SERVER_VPN_IP', '10.99.0.1')
    monkeypatch.setenv('WG_ENDPOINT', 'test.example')
    monkeypatch.setenv('WG_PORT', '51820')
    monkeypatch.setenv('WG_DNS', '1.1.1.1')
    # Set to empty so module-level load_dotenv() in route files doesn't
    # repopulate these from the real .env after the test resets them.
    monkeypatch.setenv('TOTP_SECRET', '')
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
    return client
