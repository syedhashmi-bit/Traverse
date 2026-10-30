"""First-run guided tour: auto-starts once per Google account, and stays
replayable from the ? help panel."""

AUTOSTART = b'data-tour-autostart="1"'


def test_new_account_gets_tour_autostart(logged_in_client):
    resp = logged_in_client.get('/')
    assert resp.status_code == 200
    assert AUTOSTART in resp.data


def test_marking_seen_stops_autostart(logged_in_client):
    resp = logged_in_client.post('/api/tour/seen')
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True}
    assert AUTOSTART not in logged_in_client.get('/').data


def test_marking_seen_twice_is_harmless(logged_in_client):
    assert logged_in_client.post('/api/tour/seen').status_code == 200
    assert logged_in_client.post('/api/tour/seen').status_code == 200


def test_seen_is_per_account_and_case_insensitive(logged_in_client, monkeypatch):
    monkeypatch.setenv('TRAVERSE_ADMIN_EMAILS', 'admin@example.com,second@example.com')
    with logged_in_client.session_transaction() as sess:
        sess['email'] = 'Admin@Example.com'
    logged_in_client.post('/api/tour/seen')
    assert AUTOSTART not in logged_in_client.get('/').data

    with logged_in_client.session_transaction() as sess:
        sess['email'] = 'second@example.com'
    assert AUTOSTART in logged_in_client.get('/').data


def test_tour_stays_replayable_from_help(logged_in_client):
    logged_in_client.post('/api/tour/seen')
    page = logged_in_client.get('/').data
    assert b'id="help-tour-start"' in page
    for anchor in (b'add-peer', b'peers', b'wg-status', b'help'):
        assert b'data-tour="' + anchor + b'"' in page


def test_seen_requires_login(client):
    resp = client.post('/api/tour/seen')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


def test_seen_rejects_cross_origin(logged_in_client):
    resp = logged_in_client.post('/api/tour/seen',
                                 headers={'Origin': 'https://evil.example'})
    assert resp.status_code == 403
    assert AUTOSTART in logged_in_client.get('/').data


def test_settings_offers_tour_replay_for_phones(logged_in_client):
    # The topbar ? is hidden on phones; Settings is in the mobile bottom nav.
    page = logged_in_client.get('/settings/').data
    assert b'data-tour-start' in page
