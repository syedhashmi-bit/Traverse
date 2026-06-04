import ipaddress
import json
import os
import re
import subprocess
from datetime import datetime
from flask import Blueprint, make_response, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv
load_dotenv()
from wireguard import (
    get_interface_status, get_server_public_key,
    WG_INTERFACE, WG_SUBNET, WG_SERVER_IP, WG_ENDPOINT, WG_PORT, WG_DNS,
)
from routes.auth import login_required

# Match the regex enforced by routes/peers.py — backup-import must apply the
# same constraints as the create form, otherwise an attacker-supplied JSON
# could land peers with names that bypass shell/log safety assumptions.
_NAME_RE = re.compile(r'^[a-zA-Z0-9_\-]{1,64}$')
# WireGuard public/preshared keys are 32 bytes base64-encoded → 44 chars ending
# with '='. Reject anything that doesn't match so we never pass garbage to wg(8).
_WG_KEY_RE = re.compile(r'^[A-Za-z0-9+/]{43}=$')

settings_bp = Blueprint('settings', __name__)

PIHOLE_ENABLED = bool(os.getenv('PIHOLE_ENABLED'))
# Backend API base (FTL, talked to directly). User-facing link is PIHOLE_WEB_URL.
PIHOLE_URL     = os.getenv('PIHOLE_URL', 'http://10.9.0.1:8088')
# Clickable admin link shown in the UI — the reverse-proxied, auth-gated path.
PIHOLE_WEB_URL = os.getenv('PIHOLE_WEB_URL', '/pihole')
PIHOLE_PASS    = os.getenv('PIHOLE_PASSWORD', '')


def _pihole_status_detail():
    """Return detailed Pi-hole status dict for the settings page."""
    if not PIHOLE_ENABLED:
        return None
    try:
        r = subprocess.run(['pihole', 'status'], capture_output=True, text=True, timeout=8)
        running  = 'FTL is listening' in r.stdout or 'Pi-hole blocking is enabled' in r.stdout
        enabled  = 'Pi-hole blocking is enabled' in r.stdout
        blocked  = None
        gravity_updated = None
        try:
            import sqlite3 as _sql
            gdb = '/etc/pihole/gravity.db'
            if os.path.exists(gdb):
                mtime = os.path.getmtime(gdb)
                gravity_updated = datetime.utcfromtimestamp(mtime).strftime('%Y-%m-%d %H:%M UTC')
                with _sql.connect(gdb) as gc:
                    row = gc.execute('SELECT COUNT(*) FROM vw_gravity').fetchone()
                    if row:
                        blocked = row[0]
        except Exception:
            pass
        return {
            'running':         running,
            'blocking_enabled': enabled,
            'blocked_domains': blocked,
            'gravity_updated': gravity_updated,
        }
    except Exception:
        return {'running': False, 'blocking_enabled': False, 'blocked_domains': None, 'gravity_updated': None}


@settings_bp.route('/')
@login_required
def index():
    status     = get_interface_status()
    server_pub = get_server_public_key()

    # Generate server-side wg0.conf snippet for reference
    server_conf_snippet = _build_server_conf_snippet(server_pub)

    totp_secret_env  = os.getenv('TOTP_SECRET', '').strip()
    try:
        from database import get_totp_config
        _totp_cfg = get_totp_config()
    except Exception:
        _totp_cfg = {'secret': '', 'backup_codes': []}
    totp_enrolled    = bool(_totp_cfg.get('secret'))
    totp_codes_left  = len(_totp_cfg.get('backup_codes') or [])
    # `totp_configured` keeps the old name so existing template logic still
    # works during the migration. True whenever 2FA is active via either path.
    totp_configured  = totp_enrolled or bool(totp_secret_env)

    from database import get_speedtest_results
    speedtest_results = get_speedtest_results(limit=30)

    return render_template(
        'settings.html',
        wg_status          = status,
        server_pub         = server_pub,
        server_conf_snippet = server_conf_snippet,
        interface          = WG_INTERFACE,
        subnet             = WG_SUBNET,
        server_ip          = WG_SERVER_IP,
        endpoint           = WG_ENDPOINT,
        wg_port            = WG_PORT,
        dns                = WG_DNS,
        totp_configured    = totp_configured,
        totp_enrolled      = totp_enrolled,
        totp_codes_left    = totp_codes_left,
        speedtest_results  = speedtest_results,
        pihole_enabled     = PIHOLE_ENABLED,
        pihole_status      = _pihole_status_detail(),
        pihole_url         = PIHOLE_WEB_URL,
        pihole_pass        = PIHOLE_PASS,
    )


@settings_bp.route('/restart', methods=['POST'])
@login_required
def restart_wg():
    try:
        r = subprocess.run(
            ['systemctl', 'restart', f'wg-quick@{WG_INTERFACE}'],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            flash('WireGuard interface restarted.', 'success')
        else:
            flash(f'Restart failed: {r.stderr}', 'error')
    except Exception as e:
        flash(f'Could not restart WireGuard: {e}', 'error')
    return redirect(url_for('settings.index'))


@settings_bp.route('/stop', methods=['POST'])
@login_required
def stop_wg():
    try:
        r = subprocess.run(
            ['systemctl', 'stop', f'wg-quick@{WG_INTERFACE}'],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            flash('WireGuard interface stopped.', 'success')
        else:
            flash(f'Stop failed: {r.stderr}', 'error')
    except Exception as e:
        flash(f'Could not stop WireGuard: {e}', 'error')
    return redirect(url_for('settings.index'))


@settings_bp.route('/start', methods=['POST'])
@login_required
def start_wg():
    try:
        r = subprocess.run(
            ['systemctl', 'start', f'wg-quick@{WG_INTERFACE}'],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            flash('WireGuard interface started.', 'success')
        else:
            flash(f'Start failed: {r.stderr}', 'error')
    except Exception as e:
        flash(f'Could not start WireGuard: {e}', 'error')
    return redirect(url_for('settings.index'))


@settings_bp.route('/pihole/enable', methods=['POST'])
@login_required
def pihole_enable():
    if not PIHOLE_ENABLED:
        flash('Pi-hole is not configured.', 'error')
        return redirect(url_for('settings.index'))
    try:
        r = subprocess.run(['pihole', 'enable'], capture_output=True, text=True, timeout=15)
        flash('Pi-hole blocking enabled.', 'success')
    except Exception as e:
        flash(f'Failed to enable Pi-hole: {e}', 'error')
    return redirect(url_for('settings.index'))


@settings_bp.route('/pihole/disable', methods=['POST'])
@login_required
def pihole_disable():
    if not PIHOLE_ENABLED:
        flash('Pi-hole is not configured.', 'error')
        return redirect(url_for('settings.index'))
    try:
        r = subprocess.run(['pihole', 'disable'], capture_output=True, text=True, timeout=15)
        flash('Pi-hole blocking disabled.', 'success')
    except Exception as e:
        flash(f'Failed to disable Pi-hole: {e}', 'error')
    return redirect(url_for('settings.index'))


_gravity_job = {'running': False, 'last_output': '', 'error': None}
_gravity_lock = __import__('threading').Lock()


def _run_gravity_bg():
    import threading
    try:
        r = subprocess.run(
            ['pihole', '-g'],
            capture_output=True, text=True, timeout=300
        )
        with _gravity_lock:
            _gravity_job.update({
                'running': False,
                'last_output': r.stdout[-2000:] if r.stdout else '',
                'error': r.stderr[-500:] if r.returncode != 0 else None,
            })
    except Exception as exc:
        with _gravity_lock:
            _gravity_job.update({'running': False, 'last_output': '', 'error': str(exc)})


@settings_bp.route('/pihole/update-gravity', methods=['POST'])
@login_required
def pihole_update_gravity():
    if not PIHOLE_ENABLED:
        flash('Pi-hole is not configured.', 'error')
        return redirect(url_for('settings.index'))
    with _gravity_lock:
        if _gravity_job['running']:
            flash('Gravity update already running.', 'warning')
            return redirect(url_for('settings.index'))
        _gravity_job['running'] = True
    import threading
    t = threading.Thread(target=_run_gravity_bg, daemon=True, name='gravity-update')
    t.start()
    flash('Gravity update started — this takes a minute. Reload the page to see the result.', 'info')
    return redirect(url_for('settings.index'))


@settings_bp.route('/backup/export')
@login_required
def backup_export():
    from datetime import datetime
    from database import get_all_peers, get_connection_events, get_all_alerts
    peers = get_all_peers()
    safe_peers = []
    for p in peers:
        pc = dict(p)
        # Never export peer private keys or pre-shared keys — backups are
        # often emailed/synced to less-trusted storage. Imported peers must
        # have keys regenerated.
        pc.pop('private_key', None)
        pc.pop('preshared_key', None)
        safe_peers.append(pc)
    data = {
        'traverse_version': '1.0',
        'exported_at':      datetime.utcnow().isoformat() + 'Z',
        'settings': {
            'WG_INTERFACE':    WG_INTERFACE,
            'WG_PORT':         WG_PORT,
            'WG_SUBNET':       WG_SUBNET,
            'WG_SERVER_VPN_IP': WG_SERVER_IP,
            'WG_ENDPOINT':     WG_ENDPOINT,
            'WG_DNS':          WG_DNS,
        },
        'peers':             safe_peers,
        'connection_events': get_connection_events(limit=10000),
        'alerts':            get_all_alerts(limit=10000),
    }
    filename = f"traverse-backup-{datetime.utcnow().strftime('%Y-%m-%d')}.json"
    resp = make_response(json.dumps(data, indent=2, default=str))
    resp.headers['Content-Type']        = 'application/json'
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


@settings_bp.route('/backup/import', methods=['POST'])
@login_required
def backup_import():
    f = request.files.get('backup_file')
    if not f or not f.filename:
        flash('No file selected.', 'error')
        return redirect(url_for('settings.index'))
    try:
        data = json.loads(f.read().decode('utf-8'))
    except Exception:
        flash('Invalid JSON backup file.', 'error')
        return redirect(url_for('settings.index'))
    if not isinstance(data.get('peers'), list):
        flash('Backup file contains no peer data.', 'error')
        return redirect(url_for('settings.index'))

    from database import get_all_peers, create_peer, update_peer_expiry, update_peer_notes
    from wireguard import add_peer_to_interface

    try:
        wg_net = ipaddress.ip_network(WG_SUBNET, strict=False)
    except ValueError:
        wg_net = None

    existing_keys = {p['public_key'] for p in get_all_peers()}
    imported = skipped = 0
    errors = []

    for p in data.get('peers', []):
        pub  = (p.get('public_key') or '').strip()
        name = (p.get('name') or '').strip()
        ip   = (p.get('vpn_ip') or '').strip()
        psk  = (p.get('preshared_key') or '').strip()
        if not (pub and name and ip):
            skipped += 1
            continue
        if pub in existing_keys:
            skipped += 1
            continue
        if not _NAME_RE.match(name):
            errors.append(f'{name}: invalid name')
            skipped += 1
            continue
        if not _WG_KEY_RE.match(pub):
            errors.append(f'{name}: invalid public key')
            skipped += 1
            continue
        if psk and not _WG_KEY_RE.match(psk):
            errors.append(f'{name}: invalid preshared key')
            skipped += 1
            continue
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            errors.append(f'{name}: invalid vpn_ip')
            skipped += 1
            continue
        if wg_net is not None and ip_obj not in wg_net:
            errors.append(f'{name}: vpn_ip outside {WG_SUBNET}')
            skipped += 1
            continue
        try:
            peer_id = create_peer(
                name          = name,
                private_key   = '',   # excluded from backup; user must regenerate config
                public_key    = pub,
                preshared_key = psk,
                vpn_ip        = ip,
                dns           = p.get('dns', WG_DNS),
                endpoint      = p.get('endpoint', WG_ENDPOINT),
                allowed_ips   = p.get('allowed_ips', '0.0.0.0/0'),
            )
            existing_keys.add(pub)
            if psk:
                try:
                    add_peer_to_interface(pub, psk, ip)
                except Exception:
                    pass
            if p.get('expires_at'):
                update_peer_expiry(peer_id, p['expires_at'])
            if p.get('notes') or p.get('device'):
                update_peer_notes(peer_id, p.get('notes', ''), p.get('device', 'other'))
            imported += 1
        except Exception as e:
            errors.append(f"{name or 'unknown'}: {e}")

    parts = [f'Imported {imported} peer(s)']
    if skipped:
        parts.append(f'{skipped} skipped (already exist / incomplete)')
    if errors:
        parts.append('Errors: ' + '; '.join(errors[:3]))
    if imported:
        parts.append('Use Regenerate Config on each peer to get new client configs.')
    flash(' — '.join(parts), 'success' if imported else 'warning')
    return redirect(url_for('settings.index'))


def _build_server_conf_snippet(server_pub):
    """Build reference server config snippet (no private key)."""
    return f"""[Interface]
# PrivateKey = <your-server-private-key>
Address = {WG_SERVER_IP}/24
ListenPort = {WG_PORT}
PostUp   = iptables -A FORWARD -i {WG_INTERFACE} -j ACCEPT; iptables -A FORWARD -o {WG_INTERFACE} -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i {WG_INTERFACE} -j ACCEPT; iptables -D FORWARD -o {WG_INTERFACE} -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# Peers are managed by traverse and added dynamically via wg set
"""
