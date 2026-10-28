"""Regression tests for the CSRF/origin check in app.py.

The check rejects state-changing requests whose Origin/Referer hostname
doesn't match the request's own host. Last week's Origin: null bug (from
Referrer-Policy: no-referrer) made every form post 403; these tests guard
against both regressions of the fix and weakening of the check itself.
"""


def test_post_with_matching_origin_passes_csrf(client):
    """Origin from our own host → CSRF check allows it (logout runs and
    redirects, it does NOT 403)."""
    resp = client.post(
        '/logout',
        headers={'Origin': 'http://localhost'},
    )
    assert resp.status_code != 403


def test_post_with_cross_origin_origin_is_blocked(client):
    resp = client.post(
        '/logout',
        headers={'Origin': 'https://evil.example'},
    )
    assert resp.status_code == 403
    assert b'cross-origin request blocked' in resp.data


def test_post_with_cross_origin_referer_is_blocked(client):
    resp = client.post(
        '/logout',
        headers={'Referer': 'https://evil.example/login'},
    )
    assert resp.status_code == 403


def test_post_with_no_origin_or_referer_is_allowed(client):
    """SameSite=Strict + no Origin/Referer (curl-like) is allowed —
    a browser navigating cross-site wouldn't even send the cookie."""
    resp = client.post('/logout')
    assert resp.status_code != 403


def test_get_requests_skip_csrf_check(client):
    """Only POST/PUT/PATCH/DELETE go through the origin check."""
    resp = client.get(
        '/login',
        headers={'Origin': 'https://evil.example'},
    )
    assert resp.status_code == 200


def test_referrer_policy_header_does_not_break_form_posts(client):
    """Spec: Referrer-Policy: no-referrer makes Chrome send Origin: null
    on POSTs. The fix moved us to same-origin; ensure that's what ships
    so the next agent doesn't accidentally re-tighten it."""
    resp = client.get('/login')
    assert resp.headers.get('Referrer-Policy') == 'same-origin'
