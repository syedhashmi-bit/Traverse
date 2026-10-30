"""install.sh's access modes, as the app sees them: "local only" installs
reach the dashboard at http://localhost through an SSH tunnel, everything
else is https behind nginx."""
import pytest


def _cookie_secure(monkeypatch, public_url):
    # Build the app the way conftest's `app` fixture does (no alert poller
    # thread, no wg CLI), just with this test's env in place first.
    import alerts
    from tests.conftest import _stub_wg
    monkeypatch.setattr(alerts, 'start_alerts', lambda: None)
    _stub_wg(monkeypatch)
    monkeypatch.setenv('TRAVERSE_PUBLIC_URL', public_url)
    from app import create_app
    return create_app().config['SESSION_COOKIE_SECURE']


@pytest.mark.parametrize('url', ['http://localhost:5000', 'http://127.0.0.1:5000',
                                 'HTTP://LOCALHOST:5000'])
def test_local_only_install_drops_secure_flag(monkeypatch, url):
    # Safari won't keep a Secure cookie on http://localhost; the tunnel
    # already encrypts the hop.
    assert _cookie_secure(monkeypatch, url) is False


@pytest.mark.parametrize('url', ['https://vpn.example.com', 'https://1-2-3-4.sslip.io',
                                 '', 'http://vpn.example.com',
                                 'http://localhost.evil.example'])
def test_everything_else_keeps_secure_flag(monkeypatch, url):
    # Plain-http public hosts are refused by google_auth anyway; the cookie
    # must not loosen for them.
    assert _cookie_secure(monkeypatch, url) is True


@pytest.mark.parametrize('url,ok', [
    ('http://localhost:5000', True), ('http://127.0.0.1:5000', True),
    ('https://vpn.example.com', True),
    ('http://localhost.evil.example', False), ('http://127.0.0.1.evil.example', False),
    ('http://vpn.example.com', False),
])
def test_public_url_validation(monkeypatch, url, ok):
    import google_auth
    monkeypatch.setenv('TRAVERSE_PUBLIC_URL', url)
    assert (not any('TRAVERSE_PUBLIC_URL' in p for p in google_auth.config_problems())) is ok
