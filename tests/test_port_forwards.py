"""Port forwards CRUD. iptables is stubbed — we're only testing the
DB/route layer, not the live netfilter sync."""

import pytest


@pytest.fixture(autouse=True)
def _stub_iptables(monkeypatch):
    """No real iptables / ip route in the test env."""
    import routes.port_forwards as pf
    # Keep a handle on the real function for the iptables-argv test below.
    monkeypatch.setattr(pf, '_real_apply_rule', pf._apply_rule, raising=False)
    monkeypatch.setattr(pf, '_apply_rule', lambda *a, **kw: None)
    monkeypatch.setattr(pf, '_detect_public_iface', lambda: 'eth0')
    monkeypatch.setattr(pf, '_persist_iptables', lambda: None)


def _hdrs(path='/port-forwards/'):
    return {'Origin': 'http://localhost', 'Referer': f'http://localhost{path}'}


def _make_peer(client, name='pf-peer'):
    client.post(
        '/peers/create',
        data={'name': name, 'dns': '1.1.1.1', 'endpoint': 'test.example',
              'tunnel_mode': 'full', 'device': 'laptop'},
        headers={'Origin': 'http://localhost',
                 'Referer': 'http://localhost/peers/create'},
    )
    from database import get_peer_by_name
    return get_peer_by_name(name)


# ── List page ────────────────────────────────────────────────────────────────

def test_list_page_renders_when_logged_in(logged_in_client):
    resp = logged_in_client.get('/port-forwards/')
    assert resp.status_code == 200


def test_list_page_requires_login(client):
    resp = client.get('/port-forwards/')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


# ── Create ───────────────────────────────────────────────────────────────────

def test_create_valid_rule_persists(logged_in_client):
    peer = _make_peer(logged_in_client)
    resp = logged_in_client.post(
        '/port-forwards/create',
        data={'peer_id': peer['id'], 'description': 'ssh',
              'protocol': 'tcp', 'external_port': '2200', 'internal_port': '22'},
        headers=_hdrs(),
    )
    assert resp.status_code == 302
    from database import get_port_forwards
    rules = get_port_forwards()
    assert len(rules) == 1
    assert rules[0]['peer_id'] == peer['id']
    assert rules[0]['external_port'] == 2200
    assert rules[0]['internal_port'] == 22
    assert rules[0]['protocol'] == 'tcp'


def test_create_rejects_missing_peer(logged_in_client):
    resp = logged_in_client.post(
        '/port-forwards/create',
        data={'peer_id': '', 'protocol': 'tcp',
              'external_port': '80', 'internal_port': '80'},
        headers=_hdrs(),
    )
    assert resp.status_code == 302  # redirect with flash
    from database import get_port_forwards
    assert get_port_forwards() == []


def test_create_rejects_out_of_range_port(logged_in_client):
    peer = _make_peer(logged_in_client)
    for bad in ('0', '65536', '-1', 'abc'):
        logged_in_client.post(
            '/port-forwards/create',
            data={'peer_id': peer['id'], 'protocol': 'tcp',
                  'external_port': bad, 'internal_port': '22'},
            headers=_hdrs(),
        )
    from database import get_port_forwards
    assert get_port_forwards() == []


def test_create_normalizes_unknown_protocol(logged_in_client):
    """Unknown protocol falls back to 'tcp' rather than 400ing."""
    peer = _make_peer(logged_in_client)
    logged_in_client.post(
        '/port-forwards/create',
        data={'peer_id': peer['id'], 'protocol': 'sctp-or-something',
              'external_port': '8080', 'internal_port': '8080'},
        headers=_hdrs(),
    )
    from database import get_port_forwards
    rules = get_port_forwards()
    assert rules and rules[0]['protocol'] == 'tcp'


# ── Toggle ───────────────────────────────────────────────────────────────────

def test_toggle_flips_enabled_state(logged_in_client):
    peer = _make_peer(logged_in_client)
    logged_in_client.post(
        '/port-forwards/create',
        data={'peer_id': peer['id'], 'protocol': 'tcp',
              'external_port': '443', 'internal_port': '443'},
        headers=_hdrs(),
    )
    from database import get_port_forwards, get_port_forward
    rule = get_port_forwards()[0]
    assert rule['enabled'] == 1

    logged_in_client.post(f'/port-forwards/{rule["id"]}/toggle', headers=_hdrs())
    assert get_port_forward(rule['id'])['enabled'] == 0

    logged_in_client.post(f'/port-forwards/{rule["id"]}/toggle', headers=_hdrs())
    assert get_port_forward(rule['id'])['enabled'] == 1


def test_toggle_404_on_missing_rule(logged_in_client):
    resp = logged_in_client.post('/port-forwards/99999/toggle', headers=_hdrs())
    assert resp.status_code == 404


# ── Delete ───────────────────────────────────────────────────────────────────

def test_delete_removes_rule(logged_in_client):
    peer = _make_peer(logged_in_client)
    logged_in_client.post(
        '/port-forwards/create',
        data={'peer_id': peer['id'], 'protocol': 'udp',
              'external_port': '53', 'internal_port': '53'},
        headers=_hdrs(),
    )
    from database import get_port_forwards
    rule = get_port_forwards()[0]
    logged_in_client.post(f'/port-forwards/{rule["id"]}/delete', headers=_hdrs())
    assert get_port_forwards() == []


def test_delete_404_on_missing_rule(logged_in_client):
    resp = logged_in_client.post('/port-forwards/99999/delete', headers=_hdrs())
    assert resp.status_code == 404


# ── iptables rule shape ──────────────────────────────────────────────────────

def test_forward_rule_targets_configured_wg_interface(monkeypatch):
    """FORWARD -o must follow WG_INTERFACE, not a hardcoded wg0 (on the
    migrated box wg0 is an unrelated client tunnel)."""
    import routes.port_forwards as pf
    calls = []
    monkeypatch.setattr(pf, 'WG_INTERFACE', 'wgtest')
    monkeypatch.setattr(pf.subprocess, 'run', lambda cmd, **kw: calls.append(cmd))
    pf._real_apply_rule({'peer_vpn_ip': '10.9.0.2', 'protocol': 'tcp',
                         'external_port': 2200, 'internal_port': 22})
    fwd = [c for c in calls if 'FORWARD' in c]
    assert fwd and fwd[0][fwd[0].index('-o') + 1] == 'wgtest'
