import ipaddress
import json
import socket
import ssl
import time
import urllib.request
from datetime import datetime, timedelta
from flask import Blueprint, render_template, jsonify
from database import get_all_peers, update_peer_geo, update_peer_geo_failed
from wireguard import (parse_wg_show, is_peer_active, format_bytes,
                       format_handshake, WG_ENDPOINT, get_interface_status)
from routes.auth import login_required

map_bp = Blueprint('map', __name__)

_GEO_TTL_HOURS  = 24
_GEO_FAIL_HOURS = 24
_SERVER_GEO_TTL = 6 * 3600  # seconds

_server_geo_cache = {'geo': None, 'fetched_at': 0.0}


def _extract_ip(endpoint):
    """Parse IP from WireGuard endpoint string (handles IPv4 and IPv6)."""
    if not endpoint or endpoint == '(none)':
        return None
    if endpoint.startswith('['):
        return endpoint.split(']')[0][1:]
    return endpoint.rsplit(':', 1)[0]


# Keyless GeoIP providers, tried in order. ipapi.co rate-limits datacenter IPs
# aggressively (returns {"error": true, "reason": "RateLimited"}), so fall
# through to the others rather than leaving peers off the map. Each entry is
# (url template, country-name key, success predicate).
_GEO_PROVIDERS = (
    ('https://ipapi.co/{ip}/json/',               'country_name', lambda d: not d.get('error')),
    ('https://ipwho.is/{ip}',                     'country',      lambda d: d.get('success', True)),
    ('https://get.geojs.io/v1/ip/geo/{ip}.json',  'country',      lambda d: True),
)


def _geolocate_ip(ip):
    """Look up geo data, falling through _GEO_PROVIDERS. Returns dict with
    lat/lon/city/country/country_code or None."""
    # Validate ip is a real public IP before splicing it into the URL — avoids
    # any chance of path/query injection and skips unroutable addresses that
    # no provider can resolve anyway.
    try:
        addr = ipaddress.ip_address(ip)
    except (ValueError, TypeError):
        return None
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast:
        return None
    ctx = ssl.create_default_context()
    for url_tmpl, country_key, ok in _GEO_PROVIDERS:
        try:
            req = urllib.request.Request(url_tmpl.format(ip=addr),
                                         headers={'User-Agent': 'traverse-vpn/1.0'})
            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                data = json.loads(resp.read())
            lat = data.get('latitude')
            lon = data.get('longitude')
            if not ok(data) or lat is None or lon is None:
                continue
            return {
                'lat':          float(lat),
                'lon':          float(lon),
                'city':         data.get('city') or '',
                'country':      data.get(country_key) or '',
                'country_code': data.get('country_code') or '',
            }
        except Exception:
            continue
    return None


def _get_geo(peer):
    """Return cached geo dict from DB if fresh (< 24 h). Returns None on miss."""
    cached_at = peer.get('geo_cached_at')
    if peer.get('geo_lat') is not None and peer.get('geo_lon') is not None and cached_at:
        try:
            age = datetime.utcnow() - datetime.fromisoformat(cached_at)
            if age < timedelta(hours=_GEO_TTL_HOURS):
                return {
                    'lat':          peer['geo_lat'],
                    'lon':          peer['geo_lon'],
                    'city':         peer.get('geo_city') or '',
                    'country':      peer.get('geo_country') or '',
                    'country_code': peer.get('geo_country_code') or '',
                }
        except Exception:
            pass
    return None


def _geo_recently_failed(peer):
    """True if a geo lookup for this peer failed within the last 24 h."""
    failed_at = peer.get('geo_failed_at')
    if not failed_at:
        return False
    try:
        age = datetime.utcnow() - datetime.fromisoformat(failed_at)
        return age < timedelta(hours=_GEO_FAIL_HOURS)
    except Exception:
        return False


def _get_server_geo():
    """Return geo dict for the VPS server, in-memory cached for 6 hours."""
    now = time.time()
    if _server_geo_cache['geo'] and now - _server_geo_cache['fetched_at'] < _SERVER_GEO_TTL:
        return _server_geo_cache['geo']

    ip = None
    try:
        ip = socket.gethostbyname(WG_ENDPOINT)
    except Exception:
        pass

    if ip:
        geo = _geolocate_ip(ip)
        if geo:
            geo['ip'] = ip
            _server_geo_cache['geo'] = geo
            _server_geo_cache['fetched_at'] = now
            return geo

    return _server_geo_cache.get('geo')  # stale or None


@map_bp.route('/map')
@login_required
def map_view():
    return render_template('map.html')


@map_bp.route('/api/server-location')
@login_required
def server_location():
    geo        = _get_server_geo()
    wg_running = get_interface_status().get('running', False)
    return jsonify({
        'endpoint':     WG_ENDPOINT,
        'ip':           geo.get('ip', '') if geo else '',
        'wg_running':   wg_running,
        'lat':          geo['lat'] if geo else None,
        'lon':          geo['lon'] if geo else None,
        'city':         geo.get('city', '') if geo else '',
        'country':      geo.get('country', '') if geo else '',
        'country_code': geo.get('country_code', '') if geo else '',
    })


@map_bp.route('/api/peer-locations')
@login_required
def peer_locations():
    live  = parse_wg_show()
    peers = get_all_peers()
    locations = []

    for peer in peers:
        pub       = peer['public_key']
        live_info = live.get(pub, {})
        endpoint  = live_info.get('endpoint', '') or ''
        ip        = _extract_ip(endpoint)

        last_hs = live_info.get('last_handshake') or peer.get('last_handshake')
        active  = is_peer_active(last_hs) if live_info else False

        entry = {
            'name':           peer['name'],
            'vpn_ip':         peer['vpn_ip'],
            'peer_id':        peer['id'],
            'real_ip':        ip or 'unknown',
            'lat':            None,
            'lon':            None,
            'city':           peer.get('geo_city') or '',
            'country':        peer.get('geo_country') or '',
            'country_code':   peer.get('geo_country_code') or '',
            'active':         active,
            'enabled':        bool(peer.get('enabled')),
            'last_handshake': format_handshake(last_hs),
            'rx_fmt':         format_bytes(live_info.get('rx_bytes') or peer.get('rx_bytes') or 0),
            'tx_fmt':         format_bytes(live_info.get('tx_bytes') or peer.get('tx_bytes') or 0),
            'tunnel_mode':    peer.get('tunnel_mode') or 'full',
        }

        if ip:
            geo = _get_geo(peer)
            if geo:
                entry['lat']          = geo['lat']
                entry['lon']          = geo['lon']
                entry['city']         = geo['city']
                entry['country']      = geo['country']
                entry['country_code'] = geo['country_code']
            elif not _geo_recently_failed(peer):
                geo = _geolocate_ip(ip)
                if geo:
                    entry['lat']          = geo['lat']
                    entry['lon']          = geo['lon']
                    entry['city']         = geo['city']
                    entry['country']      = geo['country']
                    entry['country_code'] = geo['country_code']
                    try:
                        update_peer_geo(peer['id'], geo['country'], geo['city'],
                                        geo['lat'], geo['lon'], geo['country_code'])
                    except Exception:
                        pass
                else:
                    try:
                        update_peer_geo_failed(peer['id'])
                    except Exception:
                        pass

        locations.append(entry)

    return jsonify(locations)
