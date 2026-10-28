"""Error pages and the inject_globals context processor must not leak app
data (VPN subnet/endpoint, WireGuard state, peer/alert counts) to anonymous
visitors. Logged-in admins keep the full layout.
"""

import pytest
from flask import template_rendered

import database


# conftest pins WG_SUBNET=10.99.0.0/24 and WG_ENDPOINT=test.example; the
# rest are markers only base.html's chrome produces.
LEAK_NEEDLES = (
    '10.99.', 'test.example', 'VPN subnet', 'Server endpoint',
    # WireGuard state strings (the login footer's static "WireGuard VPN
    # Dashboard" branding is fine — it carries no state).
    'WireGuard up', 'WireGuard down', 'WireGuard running', 'WireGuard stopped',
    'WG UP', 'WG DOWN', 'help-v', 'nav-badge', 'sidebar',
    'topbar-wg-pill', 'status-dot',
)
CHROME_KEYS = ('wg_running', 'unseen_alert_count', 'app_peer_total',
               'app_wg_subnet', 'app_wg_endpoint', 'app_pihole_url',
               'app_pihole_enabled', 'notifications_active', 'app_version')


@pytest.fixture
def seeded(app):
    """Non-zero peer + unseen-alert counts so the chrome would show them."""
    for i in (2, 3, 4):
        database.create_peer(f'peer{i}', f'priv{i}', f'pub{i}', f'psk{i}',
                             f'10.99.0.{i}', '1.1.1.1', 'test.example')
    database.create_alert('wg_down', 'WireGuard interface wg-test is DOWN',
                          severity='critical')
    database.create_alert('peer_connected', 'peer2 connected')
    return app


@pytest.fixture
def chrome_calls(monkeypatch):
    """Count every call inject_globals would make to build the chrome."""
    import notifications
    import wireguard
    calls = []

    def _spy(name, ret):
        def _fn(*a, **kw):
            calls.append(name)
            return ret
        return _fn

    monkeypatch.setattr(wireguard, 'get_interface_status',
                        _spy('wg', {'running': False, 'since': None}))
    monkeypatch.setattr(database, 'count_peers', _spy('peers', 3))
    monkeypatch.setattr(database, 'count_unseen_alerts', _spy('alerts', 2))
    monkeypatch.setattr(notifications, 'is_any_channel_active',
                        _spy('notif', False))
    return calls


@pytest.fixture
def rendered(app):
    """Capture (template name, context) for every render_template call."""
    seen = []

    def _record(sender, template, context, **extra):
        seen.append((template.name, dict(context)))

    template_rendered.connect(_record, app)
    yield seen
    template_rendered.disconnect(_record, app)


def _assert_no_leak(html):
    for needle in LEAK_NEEDLES:
        assert needle not in html, needle


def test_anonymous_404_is_standalone_and_leaks_nothing(
        client, seeded, chrome_calls, rendered):
    resp = client.get('/definitely-not-a-page-7f3a')
    assert resp.status_code == 404
    html = resp.get_data(as_text=True)
    assert '404' in html and 'Page not found.' in html
    _assert_no_leak(html)
    assert [name for name, _ in rendered] == ['error_public.html']
    ctx = rendered[0][1]
    for key in CHROME_KEYS:
        assert key not in ctx, key
    assert chrome_calls == []          # nothing even computed


def test_anonymous_404_for_api_and_nested_paths(client, seeded, chrome_calls):
    for path in ('/api/nope', '/peers/no/such/deep/path', '/static-ish/x/y'):
        resp = client.get(path)
        assert resp.status_code == 404, path
        _assert_no_leak(resp.get_data(as_text=True))
    assert chrome_calls == []


@pytest.mark.parametrize('path', ['/login', '/offline'])
def test_public_pages_get_no_chrome_context(
        client, seeded, chrome_calls, rendered, path):
    resp = client.get(path)
    assert resp.status_code == 200
    _assert_no_leak(resp.get_data(as_text=True))
    assert rendered, 'expected a template render'
    for _, ctx in rendered:
        for key in CHROME_KEYS:
            assert key not in ctx, (path, key)
    assert chrome_calls == []


@pytest.mark.parametrize('sess_data', [
    # Password-era session: logged in, but no Google email attached.
    {'logged_in': True},
    # Email removed from TRAVERSE_ADMIN_EMAILS since this session started.
    {'logged_in': True, 'email': 'removed@example.com'},
])
def test_session_off_the_allowlist_is_anonymous(
        client, seeded, chrome_calls, rendered, sess_data):
    with client.session_transaction() as sess:
        sess.update(sess_data)
    resp = client.get('/no-such-page')
    assert resp.status_code == 404
    _assert_no_leak(resp.get_data(as_text=True))
    for _, ctx in rendered:
        for key in CHROME_KEYS:
            assert key not in ctx, key
    assert chrome_calls == []


def test_anonymous_500_is_standalone(app, seeded, chrome_calls):
    app.config['PROPAGATE_EXCEPTIONS'] = False

    @app.route('/_test_boom')
    def _boom():
        raise RuntimeError('boom')

    resp = app.test_client().get('/_test_boom')
    assert resp.status_code == 500
    html = resp.get_data(as_text=True)
    assert '500' in html
    _assert_no_leak(html)
    assert chrome_calls == []


def test_logged_in_404_keeps_full_layout(logged_in_client, seeded, chrome_calls):
    """Positive control: the needles above are real chrome markers."""
    resp = logged_in_client.get('/definitely-not-a-page-7f3a')
    assert resp.status_code == 404
    html = resp.get_data(as_text=True)
    assert 'Page not found.' in html
    assert '10.99.0.0/24' in html          # VPN subnet in the help overlay
    assert 'sidebar' in html and 'WG DOWN' in html
    assert 'nav-badge' in html             # 2 unseen alerts
    assert {'wg', 'peers', 'alerts'} <= set(chrome_calls)
