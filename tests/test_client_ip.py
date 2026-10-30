"""client_ip(): the audit log and alerts must record the real visitor.

nginx forwards X-Forwarded-For as "<whatever the browser sent>, <real IP>",
so the first entry is attacker-controlled. These used to read it.
"""
import pytest

from routes.auth import client_ip

NGINX = {'REMOTE_ADDR': '127.0.0.1'}


def _csrf_hdrs(path='/'):
    return {'Origin': 'http://localhost', 'Referer': f'http://localhost{path}'}


@pytest.mark.parametrize('remote, xff, expected', [
    ('127.0.0.1', '6.6.6.6, 203.0.113.9', '203.0.113.9'),  # forged first hop
    ('127.0.0.1', '203.0.113.9', '203.0.113.9'),
    ('::1', '6.6.6.6, 2001:db8::9', '2001:db8::9'),
    ('127.0.0.1', None, '127.0.0.1'),
    ('192.168.1.50', '6.6.6.6', '192.168.1.50'),           # not from nginx: ignore the header
])
def test_client_ip(app, remote, xff, expected):
    headers = {'X-Forwarded-For': xff} if xff else {}
    with app.test_request_context('/', headers=headers, environ_base={'REMOTE_ADDR': remote}):
        assert client_ip() == expected


def test_forged_header_does_not_reach_the_audit_log(logged_in_client):
    forged = {'X-Forwarded-For': '6.6.6.6, 203.0.113.9'}
    logged_in_client.post(
        '/peers/create',
        data={'name': 'ip-test', 'dns': '1.1.1.1', 'endpoint': 'test.example',
              'tunnel_mode': 'full', 'device': 'laptop'},
        headers={**_csrf_hdrs('/peers/create'), **forged}, environ_base=NGINX,
    )
    from database import get_peer_by_name, get_audit_log
    peer = get_peer_by_name('ip-test')
    logged_in_client.post(f'/api/peer/{peer["id"]}/kill',
                          headers={**_csrf_hdrs('/peers'), **forged}, environ_base=NGINX)
    logged_in_client.post('/logout', headers={**_csrf_hdrs('/'), **forged}, environ_base=NGINX)

    rows = {r['action']: r['actor_ip'] for r in get_audit_log()}
    assert rows['peer.create'] == '203.0.113.9'
    assert rows['peer.kill'] == '203.0.113.9'
    assert rows['auth.logout'] == '203.0.113.9'
