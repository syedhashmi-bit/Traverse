"""Google sign-in: login page, both legs of the OIDC flow, the allowlist
re-check on every request, and logout. Google itself is the `fake_google`
fixture in conftest — the real token / ID-token verification path runs."""

from urllib.parse import parse_qs, urlparse

import pytest


def _flow_cookie(client):
    return client.get_cookie('traverse_oauth', path='/login/google')


def _session(client):
    with client.session_transaction() as sess:
        return dict(sess)


def _error_of(resp):
    assert resp.status_code == 302, resp.status_code
    loc = urlparse(resp.headers['Location'])
    assert loc.path == '/login'
    return parse_qs(loc.query).get('error', [None])[0]


# ── Login page / gating ──────────────────────────────────────────────────────

def test_unauthenticated_root_redirects_to_login(client):
    resp = client.get('/')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


def test_login_page_offers_google_only(client):
    html = client.get('/login').get_data(as_text=True)
    assert 'Sign in with Google' in html
    assert 'href="/login/google"' in html
    assert 'type="password"' not in html


def test_login_page_carries_next_into_google_link(client):
    html = client.get('/login?next=/peers/').get_data(as_text=True)
    assert 'href="/login/google?next=/peers/"' in html


def test_login_page_explains_error_codes(client):
    html = client.get('/login?error=not_allowed').get_data(as_text=True)
    assert 'isn’t allowed to sign in' in html
    # Unknown codes fall back to the generic message, never echo the input.
    html = client.get('/login?error=<script>').get_data(as_text=True)
    assert 'Sign-in failed' in html and '<script>' not in html


def test_logged_in_user_skips_login_page(logged_in_client):
    resp = logged_in_client.get('/login')
    assert resp.status_code == 302
    assert resp.headers['Location'] == '/'


@pytest.mark.parametrize('var', ['TRAVERSE_GOOGLE_CLIENT_ID', 'TRAVERSE_GOOGLE_CLIENT_SECRET',
                                 'TRAVERSE_PUBLIC_URL', 'TRAVERSE_ADMIN_EMAILS'])
def test_unconfigured_google_cannot_start(client, monkeypatch, var):
    monkeypatch.setenv(var, '')
    html = client.get('/login').get_data(as_text=True)
    assert 'aria-disabled="true"' in html
    assert _error_of(client.get('/login/google')) == 'not_configured'
    assert _flow_cookie(client) is None


def test_plain_http_public_url_is_rejected(monkeypatch):
    import google_auth
    monkeypatch.setenv('TRAVERSE_PUBLIC_URL', 'http://vpn.example.com')
    assert not google_auth.is_configured()
    assert any('TRAVERSE_PUBLIC_URL' in p for p in google_auth.config_problems())


# ── Leg 1: /login/google ─────────────────────────────────────────────────────

def test_start_redirects_to_google_with_pkce_state_and_nonce(client, fake_google):
    resp, q = fake_google.start(client)
    assert resp.headers['Location'].startswith('https://accounts.google.com/o/oauth2/v2/auth?')
    assert q['client_id'] == 'test-client.apps.googleusercontent.com'
    assert q['redirect_uri'] == 'https://traverse.test/login/google/callback'
    assert q['response_type'] == 'code'
    assert q['scope'] == 'openid email'
    assert q['code_challenge_method'] == 'S256'
    assert q['prompt'] == 'select_account'
    assert len(q['state']) >= 40 and len(q['nonce']) >= 40
    assert len(q['code_challenge']) == 43
    # PKCE verifier and nonce stay with the browser, not in the URL.
    cookie = _flow_cookie(client)
    assert cookie is not None and cookie.http_only and cookie.same_site == 'Lax'
    assert cookie.path == '/login/google'


def test_each_start_gets_fresh_secrets(client, fake_google):
    _, a = fake_google.start(client)
    _, b = fake_google.start(client)
    assert a['state'] != b['state'] and a['nonce'] != b['nonce']
    assert a['code_challenge'] != b['code_challenge']


# ── Leg 2: /login/google/callback ────────────────────────────────────────────

def test_successful_login(client, fake_google):
    resp = fake_google.login(client)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Same-site hop (meta refresh), not a 302 — see google_callback.
    assert '<meta http-equiv="refresh" content="0;url=/">' in html
    sess = _session(client)
    assert sess['logged_in'] is True and sess['email'] == 'admin@example.com'
    assert _flow_cookie(client) is None           # one-shot
    assert client.get('/').status_code == 200
    assert fake_google.calls == {'jwks': 1, 'token': 1}


def test_login_email_match_is_case_insensitive(client, fake_google, monkeypatch):
    monkeypatch.setenv('TRAVERSE_ADMIN_EMAILS', ' Other@Example.com , ADMIN@example.COM ')
    fake_google.login(client, email='Admin@Example.com')
    assert _session(client)['email'] == 'admin@example.com'
    assert client.get('/').status_code == 200


def test_login_returns_to_next(client, fake_google):
    resp = fake_google.login(client, next_url='/peers/')
    assert 'content="0;url=/peers/"' in resp.get_data(as_text=True)


@pytest.mark.parametrize('evil', ['//evil.example', '/\\evil.example',
                                  'https://evil.example', '/\x00x'])
def test_login_ignores_offsite_next(client, fake_google, evil):
    resp = fake_google.login(client, next_url=evil)
    assert 'content="0;url=/"' in resp.get_data(as_text=True)


def test_not_allowlisted_account_is_refused(client, fake_google):
    resp = fake_google.login(client, email='stranger@gmail.com')
    assert _error_of(resp) == 'not_allowed'
    assert not _session(client).get('logged_in')
    assert _flow_cookie(client) is None


def test_unverified_email_is_refused(client, fake_google):
    resp = fake_google.login(client, claims={'email_verified': False})
    assert _error_of(resp) == 'unverified'
    assert not _session(client).get('logged_in')


@pytest.mark.parametrize('extra', [
    {'rogue': True},                                       # signed by a key Google never published
    {'kid': 'unknown-kid'},                                # unknown signing key
    {'claims': {'aud': 'someone-elses-client'}},           # token minted for another app
    {'claims': {'iss': 'https://evil.example'}},           # wrong issuer
    {'claims': {'nonce': 'replayed-nonce'}},               # token from another login
    {'claims': {'exp': 1000, 'iat': 900}},                 # expired
    {'drop': ['nonce']},
    {'drop': ['exp']},
])
def test_bad_id_tokens_are_rejected(client, fake_google, extra):
    resp = fake_google.login(client, **extra)
    assert _error_of(resp) == 'failed'
    assert not _session(client).get('logged_in')


def test_hs256_token_is_rejected(client, fake_google, monkeypatch):
    """Algorithm confusion: an HMAC-"signed" token must not pass as RS256."""
    import jwt
    import google_auth
    real = fake_google.http_json

    def http_json(url, form=None):
        status, body = real(url, form)
        if url == google_auth.TOKEN_URL and status == 200:
            claims = jwt.decode(body['id_token'], options={'verify_signature': False})
            body['id_token'] = jwt.encode(claims, 'x' * 32, algorithm='HS256',
                                          headers={'kid': 'test-kid'})
        return status, body
    monkeypatch.setattr(google_auth, '_http_json', http_json)
    assert _error_of(fake_google.login(client)) == 'failed'


def test_token_endpoint_error_fails_cleanly(client, fake_google):
    _, q = fake_google.start(client)
    resp = client.get('/login/google/callback',
                      query_string={'code': 'not-base64-json!', 'state': q['state']})
    assert _error_of(resp) == 'failed'


def test_pkce_verifier_must_match(client, fake_google):
    _, q = fake_google.start(client)
    code = fake_google.make_code(q, challenge='A' * 43)
    resp = client.get('/login/google/callback', query_string={'code': code, 'state': q['state']})
    assert _error_of(resp) == 'failed'


def test_google_unreachable_fails_cleanly(client, fake_google, monkeypatch):
    import google_auth

    def down(url, form=None):
        raise google_auth.GoogleAuthError('failed', 'unreachable')
    _, q = fake_google.start(client)
    monkeypatch.setattr(google_auth, '_http_json', down)
    resp = client.get('/login/google/callback',
                      query_string={'code': fake_google.make_code(q), 'state': q['state']})
    assert _error_of(resp) == 'failed'


def test_user_cancel_at_google(client, fake_google):
    _, q = fake_google.start(client)
    resp = client.get('/login/google/callback',
                      query_string={'error': 'access_denied', 'state': q['state']})
    assert _error_of(resp) == 'denied'
    assert fake_google.calls['token'] == 0


def test_state_mismatch_is_rejected(client, fake_google):
    _, q = fake_google.start(client)
    resp = client.get('/login/google/callback',
                      query_string={'code': fake_google.make_code(q), 'state': 'forged'})
    assert _error_of(resp) == 'expired'
    assert fake_google.calls['token'] == 0


def test_callback_without_flow_cookie_is_rejected(client, fake_google):
    """Login CSRF: an attacker's own code+state, replayed into a victim's
    browser that never started a flow, must not log the victim in."""
    attacker = client.application.test_client()
    _, q = fake_google.start(attacker)
    resp = client.get('/login/google/callback',
                      query_string={'code': fake_google.make_code(q), 'state': q['state']})
    assert _error_of(resp) == 'expired'
    assert not _session(client).get('logged_in')
    assert fake_google.calls['token'] == 0


def test_tampered_flow_cookie_is_rejected(client, fake_google):
    _, q = fake_google.start(client)
    client.set_cookie('traverse_oauth', _flow_cookie(client).value[:-2] + 'xx',
                      path='/login/google')
    resp = client.get('/login/google/callback',
                      query_string={'code': fake_google.make_code(q), 'state': q['state']})
    assert _error_of(resp) == 'expired'


def test_stale_flow_cookie_is_rejected(client, fake_google, monkeypatch):
    import google_auth
    _, q = fake_google.start(client)
    monkeypatch.setattr(google_auth, 'FLOW_TTL', -1)
    resp = client.get('/login/google/callback',
                      query_string={'code': fake_google.make_code(q), 'state': q['state']})
    assert _error_of(resp) == 'expired'


def test_callback_cannot_be_replayed(client, fake_google):
    _, q = fake_google.start(client)
    args = {'code': fake_google.make_code(q), 'state': q['state']}
    assert client.get('/login/google/callback', query_string=args).status_code == 200
    client.post('/logout')
    assert _error_of(client.get('/login/google/callback', query_string=args)) == 'expired'


def test_jwks_is_cached_across_logins(app, fake_google):
    for _ in range(3):
        fake_google.login(app.test_client())
    assert fake_google.calls == {'jwks': 1, 'token': 3}


# ── Allowlist re-check on every request ──────────────────────────────────────

def test_removed_email_is_signed_out_on_next_request(client, fake_google, monkeypatch):
    fake_google.login(client)
    assert client.get('/').status_code == 200
    monkeypatch.setenv('TRAVERSE_ADMIN_EMAILS', 'someone-else@example.com')
    resp = client.get('/')
    assert resp.status_code == 302 and '/login' in resp.headers['Location']
    assert not _session(client).get('logged_in')


def test_password_era_session_is_retired(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True              # no email: pre-Google cookie
    resp = client.get('/')
    assert resp.status_code == 302 and '/login' in resp.headers['Location']


def test_nginx_auth_check(client, fake_google, monkeypatch):
    assert client.get('/_internal/auth-check').status_code == 401
    fake_google.login(client)
    assert client.get('/_internal/auth-check').status_code == 200
    monkeypatch.setenv('TRAVERSE_ADMIN_EMAILS', '')
    assert client.get('/_internal/auth-check').status_code == 401


# ── Logout ───────────────────────────────────────────────────────────────────

def test_logout_clears_session(logged_in_client):
    resp = logged_in_client.post(
        '/logout',
        headers={'Origin': 'http://localhost', 'Referer': 'http://localhost/'},
    )
    assert resp.status_code == 302
    assert not _session(logged_in_client).get('logged_in')
    assert logged_in_client.get('/').status_code == 302


def test_dashboard_accessible_when_logged_in(logged_in_client):
    assert logged_in_client.get('/').status_code == 200


# ── Settings card ────────────────────────────────────────────────────────────

def test_settings_shows_allowlist_and_redirect_uri(logged_in_client):
    html = logged_in_client.get('/settings/').get_data(as_text=True)
    assert 'Sign-in (Google)' in html
    assert 'admin@example.com' in html
    assert 'https://traverse.test/login/google/callback' in html


def test_startup_names_missing_signin_config(monkeypatch, caplog):
    import alerts
    monkeypatch.setattr(alerts, 'start_alerts', lambda: None)
    monkeypatch.setenv('TRAVERSE_GOOGLE_CLIENT_SECRET', '')
    monkeypatch.setenv('TRAVERSE_ADMIN_EMAILS', '')
    from app import create_app
    with caplog.at_level('WARNING'):
        create_app()
    msgs = [r.getMessage() for r in caplog.records]
    assert any('TRAVERSE_GOOGLE_CLIENT_SECRET is unset' in m for m in msgs), msgs
    assert any('TRAVERSE_ADMIN_EMAILS is empty' in m for m in msgs), msgs
    assert not any('TRAVERSE_GOOGLE_CLIENT_ID' in m for m in msgs), msgs


def test_static_files_stay_cacheable(logged_in_client):
    """The allowlist check must not read the session for /static/ — that
    would add `Vary: Cookie` and defeat nginx's immutable caching."""
    resp = logged_in_client.get('/static/css/style.css')
    assert resp.status_code == 200
    assert 'Cookie' not in resp.headers.get('Vary', '')
    # ...while normal pages still get the check (and the Vary).
    assert 'Cookie' in logged_in_client.get('/').headers.get('Vary', '')
