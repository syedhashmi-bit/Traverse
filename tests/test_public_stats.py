"""Tests for the token-gated, counts-only GET /api/public-stats endpoint.

Every auth failure must be an indistinguishable 404, and a valid token must
return exactly four count fields — nothing that identifies a peer.
"""

import re
import time

import pytest

import routes.api as api


TOKEN = 'a' * 64
EXPECTED_KEYS = {'peers_total', 'peers_online', 'traffic_30d_bytes', 'generated_at'}


@pytest.fixture(autouse=True)
def _reset_public_stats_cache():
    api._public_stats_cache['ts'] = 0.0
    api._public_stats_cache['data'] = None
    yield
    api._public_stats_cache['ts'] = 0.0
    api._public_stats_cache['data'] = None


@pytest.fixture
def fake_data(monkeypatch):
    """Three peers: one online, one disabled-but-handshaking, one stale."""
    now = int(time.time())
    peers = [
        {'id': 1, 'name': 'alpha-phone', 'public_key': 'pub-a', 'enabled': 1,
         'vpn_ip': '10.99.0.2', 'last_handshake': '0'},
        {'id': 2, 'name': 'beta-laptop', 'public_key': 'pub-b', 'enabled': 0,
         'vpn_ip': '10.99.0.3', 'last_handshake': '0'},
        {'id': 3, 'name': 'gamma-tablet', 'public_key': 'pub-c', 'enabled': 1,
         'vpn_ip': '10.99.0.4', 'last_handshake': str(now - 3600)},
    ]
    live = {
        'pub-a': {'last_handshake': str(now - 10), 'rx_bytes': 1, 'tx_bytes': 2,
                  'endpoint': '203.0.113.7:51820'},
        'pub-b': {'last_handshake': str(now - 10), 'rx_bytes': 3, 'tx_bytes': 4,
                  'endpoint': '203.0.113.8:51820'},
    }
    calls = {'peers': 0}

    def _get_all_peers():
        calls['peers'] += 1
        return [dict(p) for p in peers]

    monkeypatch.setattr(api, 'get_all_peers', _get_all_peers)
    monkeypatch.setattr(api, '_live_peers', lambda: live)
    monkeypatch.setattr(api, 'get_total_traffic_bytes', lambda days=30: 123456789)
    return calls


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def test_404_without_token(client, monkeypatch, fake_data):
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', TOKEN)
    resp = client.get('/api/public-stats')
    assert resp.status_code == 404
    assert fake_data['peers'] == 0          # no data touched before auth


def test_404_with_wrong_token(client, monkeypatch, fake_data):
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', TOKEN)
    for headers in (_auth('b' * 64),
                    _auth(TOKEN[:-1]),
                    {'Authorization': f'Basic {TOKEN}'},   # wrong scheme
                    {'Authorization': TOKEN},              # no scheme
                    {'Authorization': 'Bearer '},
                    _auth('tökén')):                       # non-ASCII must not 500
        resp = client.get('/api/public-stats', headers=headers)
        assert resp.status_code == 404, headers
    assert fake_data['peers'] == 0


def test_404_when_env_var_unset_or_empty(client, monkeypatch, fake_data):
    # conftest pins it to '' — an empty Bearer value must not match it.
    assert client.get('/api/public-stats', headers=_auth('')).status_code == 404
    assert client.get('/api/public-stats', headers=_auth(TOKEN)).status_code == 404
    monkeypatch.delenv('TRAVERSE_PUBLIC_STATS_TOKEN', raising=False)
    assert client.get('/api/public-stats', headers=_auth(TOKEN)).status_code == 404


def test_404_matches_unknown_route(client, monkeypatch):
    """Auth failure must look exactly like a URL that doesn't exist."""
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', TOKEN)
    gated   = client.get('/api/public-stats')
    unknown = client.get('/api/no-such-endpoint')
    assert gated.status_code == unknown.status_code == 404
    # Bodies are identical apart from the per-request CSP nonce.
    strip = lambda b: re.sub(rb'nonce-?[=\w"\'-]*', b'', b)
    assert strip(gated.data) == strip(unknown.data)
    assert gated.headers.get('Content-Type') == unknown.headers.get('Content-Type')


def test_200_shape_with_token(client, monkeypatch, fake_data):
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', TOKEN)
    resp = client.get('/api/public-stats', headers=_auth(TOKEN))
    assert resp.status_code == 200
    assert resp.is_json
    body = resp.get_json()
    assert set(body) == EXPECTED_KEYS
    assert body['peers_total'] == 3
    # pub-a online; pub-b handshaking but disabled; pub-c stale → 1
    assert body['peers_online'] == 1
    assert body['traffic_30d_bytes'] == 123456789
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', body['generated_at'])
    for key in ('peers_total', 'peers_online', 'traffic_30d_bytes'):
        assert type(body[key]) is int
    # Nothing identifying leaks into the payload.
    raw = resp.get_data(as_text=True)
    for needle in ('alpha', 'beta', 'gamma', 'pub-', '10.99.', '203.0.113'):
        assert needle not in raw
    assert 'no-store' in resp.headers.get('Cache-Control', '')


def test_works_without_login_session(client, monkeypatch, fake_data):
    """Not behind @login_required — no redirect to /login."""
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', TOKEN)
    resp = client.get('/api/public-stats', headers=_auth(TOKEN))
    assert resp.status_code == 200
    assert 'Location' not in resp.headers


def test_traffic_null_when_aggregates_unavailable(client, monkeypatch, fake_data):
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', TOKEN)

    def _boom(days=30):
        raise RuntimeError('no such table: traffic_samples')

    monkeypatch.setattr(api, 'get_total_traffic_bytes', _boom)
    body = client.get('/api/public-stats', headers=_auth(TOKEN)).get_json()
    assert body['traffic_30d_bytes'] is None
    assert body['peers_total'] == 3


def test_payload_cached_for_60s(client, monkeypatch, fake_data):
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', TOKEN)
    first = client.get('/api/public-stats', headers=_auth(TOKEN)).get_json()
    second = client.get('/api/public-stats', headers=_auth(TOKEN)).get_json()
    assert first == second
    assert fake_data['peers'] == 1

    # Cache still requires auth on every hit.
    assert client.get('/api/public-stats').status_code == 404

    # Expire it → recomputed.
    api._public_stats_cache['ts'] -= api._PUBLIC_STATS_TTL + 1
    client.get('/api/public-stats', headers=_auth(TOKEN))
    assert fake_data['peers'] == 2


def test_online_definition_matches_api_stats(logged_in_client, monkeypatch, fake_data):
    """peers_online must equal the dashboard's live active_count."""
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', TOKEN)
    monkeypatch.setattr(api, 'upsert_traffic_sample', lambda *a, **kw: None)
    monkeypatch.setattr(api, 'get_interface_status',
                        lambda: {'running': True, 'since': None})
    dash = logged_in_client.get('/api/stats').get_json()
    pub = logged_in_client.get('/api/public-stats', headers=_auth(TOKEN)).get_json()
    assert pub['peers_online'] == dash['active_count']
    assert pub['peers_total'] == dash['peer_count']


def test_get_total_traffic_bytes_sums_recent_days(app):
    """DB helper: sums rx+tx over the last N UTC days across all peers."""
    from datetime import datetime, timedelta, timezone
    import database

    today = datetime.now(timezone.utc)
    day = lambda n: (today - timedelta(days=n)).strftime('%Y-%m-%d')
    with database.get_db() as conn:
        for pid, name in ((1, 'p1'), (2, 'p2')):
            conn.execute(
                "INSERT INTO peers (id, name, private_key, public_key, "
                "preshared_key, vpn_ip, endpoint, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, name, f'priv{pid}', f'pub{pid}', f'psk{pid}',
                 f'10.99.0.{pid + 1}', 'test.example:51820', 'now', 'now'),
            )
    database.upsert_traffic_sample(1, day(0), 100, 50)     # in window
    database.upsert_traffic_sample(2, day(29), 10, 5)      # oldest day in window
    database.upsert_traffic_sample(1, day(30), 9999, 9999)  # just outside
    assert database.get_total_traffic_bytes(days=30) == 165
    assert database.get_total_traffic_bytes(days=1) == 150


# ── Every method looks like an unknown URL ──────────────────────────────────
#
# A GET-only rule answers OPTIONS with 200 + `Allow:` and other methods with
# 405, which proves the endpoint exists. Without a valid token (and for any
# method but GET/HEAD even WITH one) it must match an unknown URL exactly.

ALL_METHODS = ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS',
               # Not in the rule's method list → werkzeug 405 → app.py's 405
               # handler must still turn these into the normal 404.
               'PROPFIND', 'MKCOL', 'TRACE')
_NONCE = re.compile(r"nonce-?[=\w\"'-]*")


def _snapshot(resp):
    """status + headers (minus Date/Content-Length, CSP nonce normalised)
    + body (nonce stripped) — everything a client could tell apart."""
    headers = sorted(
        (k, _NONCE.sub('nonce', v)) for k, v in resp.headers.items()
        if k not in ('Date', 'Content-Length')
    )
    return resp.status_code, headers, _NONCE.sub('', resp.get_data(as_text=True))


def _compare_all_methods(client, extra_headers=None):
    for method in ALL_METHODS:
        h = dict(extra_headers or {})
        gated   = client.open('/api/public-stats', method=method, headers=h)
        unknown = client.open('/api/no-such-endpoint', method=method, headers=h)
        assert gated.status_code == 404 or (
            'Origin' in h and gated.status_code == 403), (method, gated.status_code)
        assert 'Allow' not in gated.headers, method
        assert _snapshot(gated) == _snapshot(unknown), method


@pytest.mark.parametrize('headers', [
    None,                                         # no Authorization at all
    {'Authorization': 'Bearer ' + 'b' * 64},      # wrong token
    {'Authorization': 'Basic ' + TOKEN},          # wrong scheme
])
def test_every_method_without_valid_token_matches_unknown_url(
        client, monkeypatch, fake_data, headers):
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', TOKEN)
    _compare_all_methods(client, headers)
    assert fake_data['peers'] == 0


def test_every_method_matches_unknown_url_when_token_unset(client, fake_data):
    # conftest pins TRAVERSE_PUBLIC_STATS_TOKEN to '' (= disabled).
    _compare_all_methods(client, _auth(TOKEN))
    assert fake_data['peers'] == 0


def test_every_method_matches_unknown_url_when_logged_in(
        logged_in_client, monkeypatch, fake_data):
    # A logged-in admin gets the full-layout 404 for both URLs alike.
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', TOKEN)
    _compare_all_methods(logged_in_client)
    assert fake_data['peers'] == 0


def test_cross_origin_writes_match_unknown_url(client, monkeypatch, fake_data):
    # The CSRF origin check rejects both URLs the same way (403).
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', TOKEN)
    _compare_all_methods(client, {'Origin': 'https://evil.example'})


def test_valid_token_only_opens_get_and_head(client, monkeypatch, fake_data):
    monkeypatch.setenv('TRAVERSE_PUBLIC_STATS_TOKEN', TOKEN)
    for method in ALL_METHODS:
        resp = client.open('/api/public-stats', method=method,
                           headers=_auth(TOKEN))
        if method in ('GET', 'HEAD'):
            assert resp.status_code == 200, method
            assert resp.is_json, method
        else:
            unknown = client.open('/api/no-such-endpoint', method=method,
                                  headers=_auth(TOKEN))
            assert _snapshot(resp) == _snapshot(unknown), method
    assert client.head('/api/public-stats', headers=_auth(TOKEN)).data == b''
