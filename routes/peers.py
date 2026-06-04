import io
import os
import re
from dotenv import load_dotenv
load_dotenv()
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, send_file, abort, jsonify,
)
import qrcode
from database import (
    get_all_peers, get_peer_by_id, get_peer_by_name,
    create_peer, set_peer_enabled, delete_peer, count_peers,
    update_peer_notes, update_peer_expiry, update_peer_keys,
    rotate_peer_psk,
    get_peer_connection_events, update_peer_pihole,
    get_peers_last_connect_ts,
    get_peer_locations, count_peer_locations,
    update_peer_tunnel, update_peer_dns_override,
    get_port_forwards, audit,
    get_peer_schedule, set_peer_schedule, delete_peer_schedule,
)
from wireguard import (
    generate_keypair, generate_preshared_key,
    get_next_vpn_ip, get_server_public_key,
    add_peer_to_interface, remove_peer_from_interface,
    generate_client_config, format_bytes, format_handshake,
    is_peer_active, _effective_allowed_ips,
    WG_ENDPOINT, WG_DNS, WG_PORT, MAX_PEERS,
)
from routes.auth import login_required

PIHOLE_ENABLED = bool(os.getenv('PIHOLE_ENABLED'))
# Pi-hole runs on the VPN server, so its DNS address is the server's VPN IP.
# Env-driven so it tracks WG_SERVER_VPN_IP after a host/subnet migration
# (was hardcoded '10.8.0.1', which broke DNS once Pi-hole moved to 10.9.0.1).
PIHOLE_DNS     = os.getenv('WG_SERVER_VPN_IP', '10.9.0.1')
FALLBACK_DNS   = '1.1.1.1'

peers_bp = Blueprint('peers', __name__)

_NAME_RE = re.compile(r'^[a-zA-Z0-9_\-]{1,64}$')
_DEVICES = {'phone', 'laptop', 'desktop', 'tablet', 'router', 'other'}


def _safe_name(name):
    return bool(_NAME_RE.match(name))


def _actor_ip():
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.remote_addr or 'unknown'


# ── List ─────────────────────────────────────────────────────────────────────

def _last_seen(hs_raw):
    """Return (label, css_class) from raw Unix timestamp string."""
    import time as _time
    try:
        ts = int(hs_raw or 0)
    except (ValueError, TypeError):
        ts = 0
    if not ts:
        return ('never', 'seen-never')
    age = int(_time.time()) - ts
    if age < 0:
        age = 0
    if age < 300:  # 5 min — matches is_peer_active threshold
        return ('● online', 'seen-online')
    if age < 3600:
        return (f'{age // 60}m ago', 'seen-recent')
    if age < 86400:
        return (f'{age // 3600}h ago', 'seen-today')
    if age < 604800:
        return (f'{age // 86400}d ago', 'seen-week')
    return (f'{age // 86400}d ago', 'seen-old')


@peers_bp.route('/')
@login_required
def list_peers():
    from datetime import datetime
    today = datetime.utcnow().strftime('%Y-%m-%d')
    peers = get_all_peers()
    last_connects = get_peers_last_connect_ts()
    for p in peers:
        raw_hs = p.get('last_handshake')  # raw Unix timestamp string — save BEFORE formatting
        p['last_handshake_raw'] = raw_hs or ''
        p['last_handshake'] = format_handshake(raw_hs)
        p['rx_fmt']         = format_bytes(p.get('rx_bytes') or 0)
        p['tx_fmt']         = format_bytes(p.get('tx_bytes') or 0)
        p['is_expired']     = bool(p.get('expires_at') and p['expires_at'] <= today)
        p['is_active']      = bool(p.get('enabled')) and is_peer_active(raw_hs)
        p['session_start']  = last_connects.get(p['id']) if p['is_active'] else None
        p['last_seen_label'], p['last_seen_cls'] = _last_seen(raw_hs)
    return render_template('peers/list.html', peers=peers)


# ── Create ───────────────────────────────────────────────────────────────────

@peers_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'GET':
        current_count = count_peers()
        try:
            next_ip = get_next_vpn_ip()
        except ValueError:
            next_ip = 'subnet exhausted'
        return render_template(
            'peers/create.html',
            next_ip       = next_ip,
            endpoint      = WG_ENDPOINT,
            dns           = WG_DNS,
            current_count = current_count,
            max_peers     = MAX_PEERS,
        )

    current_count = count_peers()
    if current_count >= MAX_PEERS:
        flash(f'Peer limit reached ({MAX_PEERS} max). Delete an existing peer to add a new one.', 'error')
        return redirect(url_for('peers.create'))

    name          = request.form.get('name', '').strip()
    dns           = request.form.get('dns', WG_DNS).strip()
    endpoint      = request.form.get('endpoint', WG_ENDPOINT).strip()
    tunnel_mode   = request.form.get('tunnel_mode', 'full').strip()
    custom_routes = request.form.get('custom_routes', '').strip()
    dns_override  = request.form.get('dns_override', '').strip()

    if tunnel_mode not in ('full', 'vpn_only', 'split'):
        tunnel_mode = 'full'

    if not _safe_name(name):
        flash('Peer name must be 1–64 alphanumeric/dash/underscore characters.', 'error')
        return redirect(url_for('peers.create'))

    if get_peer_by_name(name):
        flash(f'A peer named "{name}" already exists.', 'error')
        return redirect(url_for('peers.create'))

    try:
        vpn_ip = get_next_vpn_ip()
    except ValueError as e:
        flash(str(e), 'error')
        return redirect(url_for('peers.create'))

    try:
        priv, pub, psk = generate_keypair()
    except Exception as e:
        flash(f'Key generation failed: {e}', 'error')
        return redirect(url_for('peers.create'))

    peer_id = create_peer(
        name=name, private_key=priv, public_key=pub,
        preshared_key=psk, vpn_ip=vpn_ip,
        dns=dns, endpoint=endpoint,
        tunnel_mode=tunnel_mode, custom_routes=custom_routes,
    )

    notes      = request.form.get('notes', '').strip()
    device     = request.form.get('device', 'other').strip()
    expires_at = request.form.get('expires_at', '').strip() or None
    if device not in _DEVICES:
        device = 'other'
    update_peer_notes(peer_id, notes, device)
    if expires_at:
        update_peer_expiry(peer_id, expires_at)
    if dns_override:
        update_peer_dns_override(peer_id, dns_override)

    # Try to apply to live interface (non-fatal if WG not running)
    try:
        add_peer_to_interface(pub, psk, vpn_ip, tunnel_mode, custom_routes)
    except Exception as e:
        flash(f'Peer saved but could not add to live interface: {e}', 'warning')

    try:
        from notifications import send_notification
        send_notification('peer_added', f'➕ New peer added: *{name}*', severity='info')
    except Exception:
        pass

    audit('peer.create', target_type='peer', target_id=peer_id,
          target_name=name, actor_ip=_actor_ip(),
          details=f'tunnel={tunnel_mode} ip={vpn_ip}')

    flash(f'Peer "{name}" created at {vpn_ip}.', 'success')
    return redirect(url_for('peers.detail', peer_id=peer_id))


# ── Wizard ───────────────────────────────────────────────────────────────────

@peers_bp.route('/wizard')
@login_required
def wizard():
    current_count = count_peers()
    try:
        next_ip = get_next_vpn_ip()
    except ValueError:
        next_ip = 'subnet exhausted'
    return render_template(
        'peers/wizard.html',
        next_ip       = next_ip,
        endpoint      = WG_ENDPOINT,
        dns           = WG_DNS,
        current_count = current_count,
        max_peers     = MAX_PEERS,
    )


@peers_bp.route('/api/preview', methods=['POST'])
@login_required
def api_preview():
    """Generate keys + config preview WITHOUT saving to DB."""
    data          = request.get_json(silent=True) or {}
    name          = data.get('name', '').strip()
    dns           = data.get('dns', WG_DNS).strip()
    endpoint      = data.get('endpoint', WG_ENDPOINT).strip()
    device        = data.get('device', 'other').strip()
    notes         = data.get('notes', '').strip()
    expires       = data.get('expires_at', '').strip()
    tunnel_mode   = data.get('tunnel_mode', 'full').strip()
    custom_routes = data.get('custom_routes', '').strip()
    dns_override  = data.get('dns_override', '').strip()
    if tunnel_mode not in ('full', 'vpn_only', 'split'):
        tunnel_mode = 'full'

    if not _safe_name(name):
        return jsonify({'error': 'Invalid peer name — 1–64 alphanumeric/dash/underscore chars.'}), 400
    if get_peer_by_name(name):
        return jsonify({'error': f'A peer named "{name}" already exists.'}), 409
    if count_peers() >= MAX_PEERS:
        return jsonify({'error': f'Peer limit reached ({MAX_PEERS} max).'}), 429

    try:
        vpn_ip = get_next_vpn_ip()
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    try:
        priv, pub, psk = generate_keypair()
    except Exception as e:
        return jsonify({'error': f'Key generation failed: {e}'}), 500

    server_pub = get_server_public_key()
    fake_peer = {
        'name': name, 'private_key': priv, 'public_key': pub,
        'preshared_key': psk, 'vpn_ip': vpn_ip,
        'dns': dns, 'dns_override': dns_override,
        'endpoint': endpoint, 'enabled': 1,
        'tunnel_mode': tunnel_mode, 'custom_routes': custom_routes,
    }
    config_text = generate_client_config(fake_peer, server_pub)

    return jsonify({
        'name':          name,
        'vpn_ip':        vpn_ip,
        'private_key':   priv,
        'public_key':    pub,
        'psk':           psk,
        'config':        config_text,
        'device':        device,
        'notes':         notes,
        'expires_at':    expires,
        'dns':           dns,
        'dns_override':  dns_override,
        'endpoint':      endpoint,
        'tunnel_mode':   tunnel_mode,
        'custom_routes': custom_routes,
    })


@peers_bp.route('/api/create', methods=['POST'])
@login_required
def api_create():
    """Create a peer from wizard — accepts JSON with pre-generated keys."""
    data          = request.get_json(silent=True) or {}
    name          = data.get('name', '').strip()
    priv          = data.get('private_key', '').strip()
    pub           = data.get('public_key', '').strip()
    psk           = data.get('psk', '').strip()
    vpn_ip        = data.get('vpn_ip', '').strip()
    dns           = data.get('dns', WG_DNS).strip()
    endpoint      = data.get('endpoint', WG_ENDPOINT).strip()
    device        = data.get('device', 'other').strip()
    notes         = data.get('notes', '').strip()
    expires_at    = data.get('expires_at', '').strip() or None
    tunnel_mode   = data.get('tunnel_mode', 'full').strip()
    custom_routes = data.get('custom_routes', '').strip()
    dns_override  = data.get('dns_override', '').strip()

    if not all([name, priv, pub, psk, vpn_ip]):
        return jsonify({'error': 'Missing required fields.'}), 400
    if not _safe_name(name):
        return jsonify({'error': 'Invalid peer name.'}), 400
    if get_peer_by_name(name):
        return jsonify({'error': f'Peer "{name}" already exists.'}), 409
    if count_peers() >= MAX_PEERS:
        return jsonify({'error': f'Peer limit reached ({MAX_PEERS} max).'}), 429
    if device not in _DEVICES:
        device = 'other'
    if tunnel_mode not in ('full', 'vpn_only', 'split'):
        tunnel_mode = 'full'

    peer_id = create_peer(name=name, private_key=priv, public_key=pub,
                          preshared_key=psk, vpn_ip=vpn_ip, dns=dns, endpoint=endpoint,
                          tunnel_mode=tunnel_mode, custom_routes=custom_routes)
    update_peer_notes(peer_id, notes, device)
    if expires_at:
        update_peer_expiry(peer_id, expires_at)
    if dns_override:
        update_peer_dns_override(peer_id, dns_override)
    try:
        add_peer_to_interface(pub, psk, vpn_ip, tunnel_mode, custom_routes)
    except Exception:
        pass

    try:
        from notifications import send_notification
        send_notification('peer_added', f'➕ New peer added: *{name}*', severity='info')
    except Exception:
        pass

    audit('peer.create', target_type='peer', target_id=peer_id,
          target_name=name, actor_ip=_actor_ip(),
          details=f'tunnel={tunnel_mode} ip={vpn_ip} via=wizard')

    return jsonify({'ok': True, 'peer_id': peer_id, 'redirect': url_for('peers.detail', peer_id=peer_id)})


# ── Detail ───────────────────────────────────────────────────────────────────

@peers_bp.route('/<int:peer_id>')
@login_required
def detail(peer_id):
    from datetime import datetime
    peer = get_peer_by_id(peer_id)
    if not peer:
        abort(404)
    server_pub  = get_server_public_key()
    config_text = generate_client_config(peer, server_pub or '(server-key-unavailable)')
    peer['last_handshake'] = format_handshake(peer.get('last_handshake'))
    peer['rx_fmt']         = format_bytes(peer.get('rx_bytes') or 0)
    peer['tx_fmt']         = format_bytes(peer.get('tx_bytes') or 0)
    today = datetime.utcnow().strftime('%Y-%m-%d')
    peer['is_expired'] = bool(peer.get('expires_at') and peer['expires_at'] <= today)
    events = get_peer_connection_events(peer_id, limit=10)

    locations       = get_peer_locations(peer_id, limit=5)
    location_total  = count_peer_locations(peer_id)
    # Helpers for template
    def _flag(cc):
        if not cc or len(cc) != 2:
            return '🌍'
        return ''.join(chr(ord(c.upper()) + 127397) for c in cc)
    def _mask(ip):
        if not ip:
            return ''
        if ':' in ip:
            parts = ip.split(':')
            return ':'.join(parts[:3]) + ':x:x:x:x:x' if len(parts) >= 3 else ip
        parts = ip.split('.')
        if len(parts) == 4:
            return f'{parts[0]}.{parts[1]}.x.x'
        return ip
    for loc in locations:
        loc['flag']      = _flag(loc.get('geo_country_code'))
        loc['masked_ip'] = _mask(loc.get('endpoint_ip'))

    tunnel_mode   = peer.get('tunnel_mode') or 'full'
    custom_routes = peer.get('custom_routes') or ''
    effective_ips = _effective_allowed_ips(peer['vpn_ip'], tunnel_mode, custom_routes)
    port_fwds     = get_port_forwards(peer_id)

    schedule = get_peer_schedule(peer_id)
    if schedule:
        from schedules import parse_days, format_days
        schedule['day_set']  = parse_days(schedule['days_of_week'])
        schedule['day_label'] = format_days(schedule['days_of_week'])

    return render_template(
        'peers/detail.html',
        peer            = peer,
        server_pub      = server_pub,
        config_text     = config_text,
        wg_port         = WG_PORT,
        events          = events,
        pihole_enabled  = PIHOLE_ENABLED,
        locations       = locations,
        location_total  = location_total,
        effective_ips   = effective_ips,
        port_fwds       = port_fwds,
        schedule        = schedule,
    )


# ── Config download ───────────────────────────────────────────────────────────

@peers_bp.route('/<int:peer_id>/config')
@login_required
def download_config(peer_id):
    peer = get_peer_by_id(peer_id)
    if not peer:
        abort(404)
    server_pub  = get_server_public_key()
    config_text = generate_client_config(peer, server_pub or '(server-key-unavailable)')
    buf = io.BytesIO(config_text.encode('utf-8'))
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', peer['name']) or 'peer'
    resp = send_file(
        buf,
        mimetype='text/plain',
        as_attachment=True,
        download_name=f'{safe_name}.conf',
    )
    # Config contains the peer private key — never let it sit in a proxy or
    # browser cache. Override Flask's send_file default max-age explicitly.
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
    resp.headers['Pragma']        = 'no-cache'
    return resp


# ── QR code page ──────────────────────────────────────────────────────────────

@peers_bp.route('/<int:peer_id>/qr')
@login_required
def qr_page(peer_id):
    peer = get_peer_by_id(peer_id)
    if not peer:
        abort(404)
    return render_template('peers/qr.html', peer=peer)


@peers_bp.route('/<int:peer_id>/qr.png')
@login_required
def qr_image(peer_id):
    peer = get_peer_by_id(peer_id)
    if not peer:
        abort(404)
    server_pub  = get_server_public_key()
    config_text = generate_client_config(peer, server_pub or '(server-key-unavailable)')
    img = qrcode.make(config_text)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    resp = send_file(buf, mimetype='image/png')
    # The QR encodes the peer private key — same caching rules as the .conf.
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
    resp.headers['Pragma']        = 'no-cache'
    return resp


# ── Edit notes / device ──────────────────────────────────────────────────────

@peers_bp.route('/<int:peer_id>/edit', methods=['POST'])
@login_required
def edit(peer_id):
    peer = get_peer_by_id(peer_id)
    if not peer:
        abort(404)
    notes         = request.form.get('notes', '').strip()
    device        = request.form.get('device', 'other').strip()
    expires_at    = request.form.get('expires_at', '').strip() or None
    tunnel_mode   = request.form.get('tunnel_mode', peer.get('tunnel_mode') or 'full').strip()
    custom_routes = request.form.get('custom_routes', '').strip()
    dns_override  = request.form.get('dns_override', '').strip()
    if device not in _DEVICES:
        device = 'other'
    if tunnel_mode not in ('full', 'vpn_only', 'split'):
        tunnel_mode = 'full'
    update_peer_notes(peer_id, notes, device)
    update_peer_expiry(peer_id, expires_at)
    update_peer_tunnel(peer_id, tunnel_mode, custom_routes)
    update_peer_dns_override(peer_id, dns_override)
    # Re-apply allowed-ips to live wg0 if peer is enabled
    if peer.get('enabled'):
        try:
            add_peer_to_interface(
                peer['public_key'], peer['preshared_key'], peer['vpn_ip'],
                tunnel_mode, custom_routes
            )
        except Exception:
            pass
    flash('Peer updated.', 'success')
    return redirect(url_for('peers.detail', peer_id=peer_id))


# ── Regenerate config ─────────────────────────────────────────────────────────

@peers_bp.route('/<int:peer_id>/regenerate', methods=['POST'])
@login_required
def regenerate(peer_id):
    peer = get_peer_by_id(peer_id)
    if not peer:
        abort(404)
    old_pubkey = peer['public_key']
    try:
        priv, pub, psk = generate_keypair()
    except Exception as e:
        flash(f'Key generation failed: {e}', 'error')
        return redirect(url_for('peers.detail', peer_id=peer_id))
    update_peer_keys(peer_id, priv, pub, psk)
    try:
        remove_peer_from_interface(old_pubkey)
        add_peer_to_interface(pub, psk, peer['vpn_ip'],
                              peer.get('tunnel_mode') or 'full',
                              peer.get('custom_routes') or '')
    except Exception as e:
        flash(f'Keys updated but WireGuard sync failed: {e}', 'warning')
    try:
        from notifications import send_notification
        send_notification('config_regenerated',
                          f'🔄 Config regenerated for *{peer["name"]}*',
                          severity='info')
    except Exception:
        pass
    audit('peer.regenerate', target_type='peer', target_id=peer_id,
          target_name=peer['name'], actor_ip=_actor_ip())

    flash('Config regenerated. The old config will no longer work — download or scan the new one below.', 'success')
    return redirect(url_for('peers.detail', peer_id=peer_id))


# ── Rotate preshared key (PSK only) ──────────────────────────────────────────

@peers_bp.route('/<int:peer_id>/rotate-psk', methods=['POST'])
@login_required
def rotate_psk(peer_id):
    """Generate a fresh preshared key, persist it, and re-sync wg0.

    The peer's keypair is left alone so its identity on the tunnel
    doesn't change — only the symmetric PSK is replaced. The client
    must download or scan the new config; the old one will fail
    until they do.
    """
    peer = get_peer_by_id(peer_id)
    if not peer:
        abort(404)
    try:
        new_psk = generate_preshared_key()
    except Exception as e:
        flash(f'Preshared key generation failed: {e}', 'error')
        return redirect(url_for('peers.detail', peer_id=peer_id))

    rotate_peer_psk(peer_id, new_psk)

    if peer.get('enabled'):
        try:
            # wg set <iface> peer <pub> preshared-key — applied by
            # re-adding with the same pubkey + new psk.
            add_peer_to_interface(
                peer['public_key'], new_psk, peer['vpn_ip'],
                peer.get('tunnel_mode') or 'full',
                peer.get('custom_routes') or '',
            )
        except Exception as e:
            flash(f'PSK rotated in DB but live wg0 sync failed: {e}', 'warning')

    try:
        from notifications import send_notification
        send_notification(
            'psk_rotated',
            f'🔑 Preshared key rotated for *{peer["name"]}*',
            severity='info',
        )
    except Exception:
        pass

    audit('peer.psk_rotate', target_type='peer', target_id=peer_id,
          target_name=peer['name'], actor_ip=_actor_ip())

    flash(
        'Preshared key rotated. The old config will no longer connect — '
        'download or scan the new one below and apply it to the client.',
        'success',
    )
    return redirect(url_for('peers.detail', peer_id=peer_id))


# ── Per-peer schedule ─────────────────────────────────────────────────────────

# Conservative input validation: HH:MM with leading zero, 0..23 / 0..59.
_HM_RE = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')
# IANA tz names are restricted to a small character set; we just gate the
# obvious junk before zoneinfo gets the string.
_TZ_RE = re.compile(r'^[A-Za-z0-9_+\-/]{1,64}$')


@peers_bp.route('/<int:peer_id>/schedule', methods=['POST'])
@login_required
def schedule_save(peer_id):
    peer = get_peer_by_id(peer_id)
    if not peer:
        abort(404)

    days_raw = request.form.getlist('days')
    days = []
    for d in days_raw:
        try:
            v = int(d)
        except (TypeError, ValueError):
            continue
        if 0 <= v <= 6:
            days.append(v)
    if not days:
        flash('Pick at least one day for the schedule.', 'error')
        return redirect(url_for('peers.detail', peer_id=peer_id) + '#schedule')

    enabled_from = (request.form.get('enabled_from') or '').strip()
    enabled_to   = (request.form.get('enabled_to')   or '').strip()
    if not _HM_RE.match(enabled_from) or not _HM_RE.match(enabled_to):
        flash('Times must look like HH:MM (24-hour).', 'error')
        return redirect(url_for('peers.detail', peer_id=peer_id) + '#schedule')
    if enabled_from == enabled_to:
        flash('Start and end time can\'t be the same.', 'error')
        return redirect(url_for('peers.detail', peer_id=peer_id) + '#schedule')

    tz = (request.form.get('timezone') or 'UTC').strip() or 'UTC'
    if not _TZ_RE.match(tz):
        flash('Invalid timezone name.', 'error')
        return redirect(url_for('peers.detail', peer_id=peer_id) + '#schedule')
    # Round-trip through zoneinfo so a typo doesn't get persisted.
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except Exception:
        flash(f'Unknown timezone: {tz}', 'error')
        return redirect(url_for('peers.detail', peer_id=peer_id) + '#schedule')

    is_enabled = bool(request.form.get('schedule_enabled'))
    set_peer_schedule(peer_id, days, enabled_from, enabled_to,
                      timezone=tz, enabled=is_enabled)
    audit('peer.schedule_saved', target_type='peer', target_id=peer_id,
          target_name=peer['name'], actor_ip=_actor_ip(),
          details=f'{enabled_from}-{enabled_to} {tz} '
                  f'days={",".join(str(d) for d in sorted(days))} '
                  f'enabled={is_enabled}')
    flash('Schedule saved.', 'success')
    return redirect(url_for('peers.detail', peer_id=peer_id) + '#schedule')


@peers_bp.route('/<int:peer_id>/schedule/delete', methods=['POST'])
@login_required
def schedule_delete(peer_id):
    peer = get_peer_by_id(peer_id)
    if not peer:
        abort(404)
    delete_peer_schedule(peer_id)
    audit('peer.schedule_deleted', target_type='peer', target_id=peer_id,
          target_name=peer['name'], actor_ip=_actor_ip())
    flash('Schedule removed — peer state is back under manual control.', 'success')
    return redirect(url_for('peers.detail', peer_id=peer_id) + '#schedule')


# ── Toggle enable/disable ─────────────────────────────────────────────────────

@peers_bp.route('/<int:peer_id>/toggle', methods=['POST'])
@login_required
def toggle(peer_id):
    peer = get_peer_by_id(peer_id)
    if not peer:
        abort(404)

    new_state = not bool(peer['enabled'])
    set_peer_enabled(peer_id, new_state)

    try:
        if new_state:
            add_peer_to_interface(
                peer['public_key'], peer['preshared_key'], peer['vpn_ip'],
                peer.get('tunnel_mode') or 'full',
                peer.get('custom_routes') or '',
            )
            flash(f'Peer "{peer["name"]}" enabled.', 'success')
        else:
            remove_peer_from_interface(peer['public_key'])
            flash(f'Peer "{peer["name"]}" disabled.', 'success')
    except Exception as e:
        flash(f'DB updated but WireGuard sync failed: {e}', 'warning')

    audit('peer.enable' if new_state else 'peer.disable',
          target_type='peer', target_id=peer_id,
          target_name=peer['name'], actor_ip=_actor_ip())

    return redirect(url_for('peers.detail', peer_id=peer_id))


# ── Delete ────────────────────────────────────────────────────────────────────

@peers_bp.route('/<int:peer_id>/delete', methods=['POST'])
@login_required
def delete(peer_id):
    peer = get_peer_by_id(peer_id)
    if not peer:
        abort(404)

    try:
        remove_peer_from_interface(peer['public_key'])
    except Exception as e:
        flash(f'Could not remove from live interface: {e}', 'warning')

    peer_name = peer['name']
    delete_peer(peer_id)
    try:
        from notifications import send_notification
        send_notification('peer_deleted', f'🗑️ Peer deleted: *{peer_name}*', severity='info')
    except Exception:
        pass
    audit('peer.delete', target_type='peer', target_id=peer_id,
          target_name=peer_name, actor_ip=_actor_ip())
    flash(f'Peer "{peer_name}" deleted.', 'success')
    return redirect(url_for('peers.list_peers'))


# ── Bulk actions ──────────────────────────────────────────────────────────────

def _parse_bulk_ids():
    """Parse a comma-separated 'ids' field or repeated 'ids' values."""
    raw = request.form.getlist('ids')
    if not raw:
        single = request.form.get('ids', '')
        raw = [single] if single else []
    out = []
    for chunk in raw:
        for token in (chunk or '').split(','):
            token = token.strip()
            if token.isdigit():
                out.append(int(token))
    # Dedupe preserving order
    seen = set()
    return [i for i in out if not (i in seen or seen.add(i))]


@peers_bp.route('/bulk-disable', methods=['POST'])
@login_required
def bulk_disable():
    ids = _parse_bulk_ids()
    if not ids:
        return jsonify({'ok': False, 'error': 'No peer ids provided.'}), 400
    n = 0
    actor = _actor_ip()
    for pid in ids:
        peer = get_peer_by_id(pid)
        if not peer or not peer.get('enabled'):
            continue
        set_peer_enabled(pid, False)
        try:
            remove_peer_from_interface(peer['public_key'])
        except Exception:
            pass
        audit('peer.disable', target_type='peer', target_id=pid,
              target_name=peer['name'], actor_ip=actor, details='via=bulk')
        n += 1
    return jsonify({'ok': True, 'count': n})


@peers_bp.route('/bulk-enable', methods=['POST'])
@login_required
def bulk_enable():
    ids = _parse_bulk_ids()
    if not ids:
        return jsonify({'ok': False, 'error': 'No peer ids provided.'}), 400
    n = 0
    actor = _actor_ip()
    for pid in ids:
        peer = get_peer_by_id(pid)
        if not peer or peer.get('enabled'):
            continue
        set_peer_enabled(pid, True)
        try:
            add_peer_to_interface(
                peer['public_key'], peer['preshared_key'], peer['vpn_ip'],
                peer.get('tunnel_mode') or 'full',
                peer.get('custom_routes') or '',
            )
        except Exception:
            pass
        audit('peer.enable', target_type='peer', target_id=pid,
              target_name=peer['name'], actor_ip=actor, details='via=bulk')
        n += 1
    return jsonify({'ok': True, 'count': n})


@peers_bp.route('/bulk-delete', methods=['POST'])
@login_required
def bulk_delete():
    ids = _parse_bulk_ids()
    if not ids:
        return jsonify({'ok': False, 'error': 'No peer ids provided.'}), 400
    n = 0
    actor = _actor_ip()
    for pid in ids:
        peer = get_peer_by_id(pid)
        if not peer:
            continue
        try:
            remove_peer_from_interface(peer['public_key'])
        except Exception:
            pass
        peer_name = peer['name']
        delete_peer(pid)
        try:
            from notifications import send_notification
            send_notification('peer_deleted', f'🗑️ Peer deleted: *{peer_name}*', severity='info')
        except Exception:
            pass
        audit('peer.delete', target_type='peer', target_id=pid,
              target_name=peer_name, actor_ip=actor, details='via=bulk')
        n += 1
    return jsonify({'ok': True, 'count': n})


# ── CSV export ────────────────────────────────────────────────────────────────

@peers_bp.route('/export.csv')
@login_required
def export_csv():
    """Export peers as CSV — never includes private_key or preshared_key."""
    import csv
    from datetime import datetime
    peers = get_all_peers()
    today = datetime.utcnow().strftime('%Y-%m-%d')
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        'id', 'name', 'device', 'vpn_ip', 'public_key', 'dns', 'dns_override',
        'tunnel_mode', 'custom_routes', 'enabled', 'is_active',
        'rx_bytes', 'tx_bytes', 'last_handshake', 'expires_at',
        'created_at', 'updated_at', 'notes',
    ])
    for p in peers:
        raw_hs = p.get('last_handshake')
        active = bool(p.get('enabled')) and is_peer_active(raw_hs)
        w.writerow([
            p.get('id'),
            p.get('name'),
            p.get('device') or 'other',
            p.get('vpn_ip'),
            p.get('public_key'),
            p.get('dns'),
            p.get('dns_override') or '',
            p.get('tunnel_mode') or 'full',
            p.get('custom_routes') or '',
            1 if p.get('enabled') else 0,
            1 if active else 0,
            p.get('rx_bytes') or 0,
            p.get('tx_bytes') or 0,
            raw_hs or '',
            p.get('expires_at') or '',
            p.get('created_at') or '',
            p.get('updated_at') or '',
            (p.get('notes') or '').replace('\r', ' ').replace('\n', ' '),
        ])
    payload = buf.getvalue().encode('utf-8')
    fname = f'traverse-peers-{today}.csv'
    return send_file(
        io.BytesIO(payload),
        mimetype='text/csv',
        as_attachment=True,
        download_name=fname,
    )


# ── Pi-hole DNS toggle ────────────────────────────────────────────────────────

@peers_bp.route('/<int:peer_id>/toggle-pihole', methods=['POST'])
@login_required
def toggle_pihole(peer_id):
    if not PIHOLE_ENABLED:
        abort(404)
    peer = get_peer_by_id(peer_id)
    if not peer:
        abort(404)
    new_state = not bool(peer.get('use_pihole', 1))
    update_peer_pihole(peer_id, new_state)
    # Also update the dns field so existing config downloads reflect the change
    from database import get_db
    from datetime import datetime
    new_dns = PIHOLE_DNS if new_state else FALLBACK_DNS
    with get_db() as conn:
        conn.execute(
            "UPDATE peers SET dns = ?, updated_at = ? WHERE id = ?",
            (new_dns, datetime.utcnow().isoformat(), peer_id)
        )
    state_label = 'enabled' if new_state else 'disabled'
    flash(f'Ad blocking (Pi-hole DNS) {state_label} for "{peer["name"]}".', 'success')
    return redirect(url_for('peers.detail', peer_id=peer_id))
