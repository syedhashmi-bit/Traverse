"""Regressions that only bite a fresh install (installer defaults, no
Pi-hole, a subnet other than the original host's 10.9.0.0/24)."""
import pytest

from envflags import env_flag


@pytest.mark.parametrize('raw,expected', [
    ('', False), ('false', False), ('0', False), ('no', False), ('off', False),
    ('true', True), ('1', True), ('YES', True), (' on ', True),
])
def test_env_flag_parsing(monkeypatch, raw, expected):
    # install.sh writes PIHOLE_ENABLED=false — that must mean off.
    monkeypatch.setenv('TV_TEST_FLAG', raw)
    assert env_flag('TV_TEST_FLAG') is expected


def test_installer_pihole_false_hides_pihole(logged_in_client, monkeypatch):
    monkeypatch.setenv('PIHOLE_ENABLED', 'false')
    page = logged_in_client.get('/peers/create').data
    assert b'value="pihole"' not in page


def test_new_peer_defaults_to_server_dns_without_pihole(logged_in_client):
    page = logged_in_client.get('/peers/create').data.decode()
    assert '10.9.0.1' not in page
    assert '<option value="default" selected>Server default (1.1.1.1)</option>' in page
    assert 'value="pihole"' not in page


def test_new_peer_defaults_to_pihole_at_server_vpn_ip(logged_in_client, monkeypatch):
    monkeypatch.setenv('PIHOLE_ENABLED', 'true')
    page = logged_in_client.get('/peers/create').data.decode()
    assert '10.9.0.1' not in page
    # conftest's WG_SERVER_VPN_IP — Pi-hole answers on the server's VPN IP.
    assert '<option value="pihole" selected>Pi-hole (10.99.0.1)' in page
    assert 'pihole:     "10.99.0.1"' in page


def test_peer_detail_has_no_hardcoded_pihole_ip(logged_in_client):
    logged_in_client.post('/peers/create', data={'name': 'phone1', 'device': 'phone'})
    page = logged_in_client.get('/peers/1').data.decode()
    assert 'phone1' in page
    assert '10.9.0.1' not in page
