"""Audit log: helper writes rows, admin actions wire it, /audit renders."""


def _csrf_hdrs(path='/'):
    return {'Origin': 'http://localhost', 'Referer': f'http://localhost{path}'}


def test_audit_helper_persists(app):
    """database.audit() writes a row visible via get_audit_log."""
    with app.app_context():
        from database import audit, get_audit_log
        audit('test.demo', target_type='peer', target_id=42,
              target_name='demo', actor_ip='10.0.0.1', details='hello')
        rows = get_audit_log(limit=10)
        assert any(r['action'] == 'test.demo' and r['target_id'] == 42
                   for r in rows)


def test_login_success_writes_audit_row(client, fake_google):
    fake_google.login(client)
    from database import get_audit_log
    rows = get_audit_log(action_prefix='auth.')
    assert any(r['action'] == 'auth.login_success' and
               r['details'] == 'google:admin@example.com' for r in rows)


def test_login_failure_writes_audit_row(client, fake_google):
    fake_google.login(client, email='stranger@gmail.com')
    from database import get_audit_log
    rows = get_audit_log(action_prefix='auth.')
    assert any(r['action'] == 'auth.login_failed' and
               r['details'] == 'google:not_allowed stranger@gmail.com' for r in rows)


def test_stray_callback_hit_writes_no_audit_row(client):
    """No flow cookie → not a sign-in attempt; don't let bots fill the log."""
    client.get('/login/google/callback?code=x&state=y')
    from database import get_audit_log
    assert get_audit_log(action_prefix='auth.') == []


def test_peer_create_writes_audit_row(logged_in_client):
    logged_in_client.post(
        '/peers/create',
        data={'name': 'audit-test', 'dns': '1.1.1.1', 'endpoint': 'test.example',
              'tunnel_mode': 'full', 'device': 'laptop'},
        headers=_csrf_hdrs('/peers/create'),
    )
    from database import get_audit_log
    rows = get_audit_log(action_prefix='peer.')
    assert any(r['action'] == 'peer.create' and r['target_name'] == 'audit-test'
               for r in rows)


def test_peer_delete_writes_audit_row(logged_in_client):
    logged_in_client.post(
        '/peers/create',
        data={'name': 'doomed', 'dns': '1.1.1.1', 'endpoint': 'test.example',
              'tunnel_mode': 'full', 'device': 'laptop'},
        headers=_csrf_hdrs('/peers/create'),
    )
    from database import get_peer_by_name, get_audit_log
    peer = get_peer_by_name('doomed')
    logged_in_client.post(
        f'/peers/{peer["id"]}/delete',
        headers=_csrf_hdrs(f'/peers/{peer["id"]}'),
    )
    rows = get_audit_log(action_prefix='peer.delete')
    assert any(r['target_name'] == 'doomed' for r in rows)


def test_audit_page_renders(logged_in_client):
    from database import audit
    audit('peer.demo', target_name='demo', actor_ip='10.0.0.1')
    resp = logged_in_client.get('/audit')
    assert resp.status_code == 200
    assert b'peer.demo' in resp.data


def test_audit_csv_export(logged_in_client):
    from database import audit
    audit('peer.csv_demo', target_name='csv-peer', actor_ip='10.0.0.2')
    resp = logged_in_client.get('/audit.csv')
    assert resp.status_code == 200
    assert b'peer.csv_demo' in resp.data
    assert b'csv-peer' in resp.data
    assert resp.headers['Content-Type'].startswith('text/csv')


def test_audit_page_requires_login(client):
    resp = client.get('/audit')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


def test_audit_prefix_filter(logged_in_client):
    from database import audit
    audit('peer.foo', target_name='p')
    audit('auth.bar', actor_ip='1.1.1.1')
    resp = logged_in_client.get('/audit?prefix=auth.')
    assert resp.status_code == 200
    assert b'auth.bar' in resp.data
    assert b'peer.foo' not in resp.data
