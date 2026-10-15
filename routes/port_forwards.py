import subprocess
import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort
from database import (
    get_all_peers, get_peer_by_id,
    get_port_forwards, get_port_forward,
    create_port_forward, set_port_forward_enabled, delete_port_forward,
)
from routes.auth import login_required
from wireguard import WG_INTERFACE

pf_bp = Blueprint('port_forwards', __name__)

_PROTOS = ('tcp', 'udp', 'both')


def _detect_public_iface():
    """Return the default route interface (usually eth0)."""
    try:
        r = subprocess.run(['ip', 'route'], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if line.startswith('default'):
                parts = line.split()
                if 'dev' in parts:
                    return parts[parts.index('dev') + 1]
    except Exception:
        pass
    return 'eth0'


def _persist_iptables():
    """Save iptables rules to /etc/iptables/rules.v4 if writable."""
    try:
        os.makedirs('/etc/iptables', exist_ok=True)
        with open('/etc/iptables/rules.v4', 'w') as f:
            subprocess.run(['iptables-save'], stdout=f, timeout=10, check=False)
    except Exception:
        pass


def _apply_rule(rule, remove=False):
    """Apply (or remove) iptables DNAT + FORWARD rules for a port forward."""
    iface  = _detect_public_iface()
    flag   = '-D' if remove else '-A'
    vpn_ip = rule['peer_vpn_ip']
    proto  = rule['protocol'].lower()
    ext_p  = str(rule['external_port'])
    int_p  = str(rule['internal_port'])
    protos = ['tcp', 'udp'] if proto == 'both' else [proto]
    for p in protos:
        subprocess.run([
            'iptables', '-t', 'nat', flag, 'PREROUTING',
            '-i', iface, '-p', p, '--dport', ext_p,
            '-j', 'DNAT', '--to-destination', f'{vpn_ip}:{int_p}',
        ], check=False, timeout=10)
        subprocess.run([
            'iptables', flag, 'FORWARD',
            '-i', iface, '-o', WG_INTERFACE, '-p', p, '--dport', int_p,
            '-d', vpn_ip, '-j', 'ACCEPT',
        ], check=False, timeout=10)
    _persist_iptables()


def _validate_port(val):
    try:
        p = int(val)
        return 1 <= p <= 65535
    except (TypeError, ValueError):
        return False


# ── List ──────────────────────────────────────────────────────────────────────

@pf_bp.route('/')
@login_required
def index():
    rules = get_port_forwards()
    peers = get_all_peers()
    peer_map = {p['id']: p for p in peers}
    return render_template('port_forwards/index.html', rules=rules, peers=peers, peer_map=peer_map)


# ── Create ────────────────────────────────────────────────────────────────────

@pf_bp.route('/create', methods=['POST'])
@login_required
def create():
    peer_id       = request.form.get('peer_id', '').strip()
    description   = request.form.get('description', '').strip()
    protocol      = request.form.get('protocol', 'tcp').strip()
    external_port = request.form.get('external_port', '').strip()
    internal_port = request.form.get('internal_port', '').strip()

    if not peer_id:
        flash('Peer is required.', 'error')
        return redirect(url_for('port_forwards.index'))

    try:
        peer_id = int(peer_id)
    except ValueError:
        flash('Invalid peer.', 'error')
        return redirect(url_for('port_forwards.index'))

    peer = get_peer_by_id(peer_id)
    if not peer:
        flash('Peer not found.', 'error')
        return redirect(url_for('port_forwards.index'))

    if protocol not in _PROTOS:
        protocol = 'tcp'

    if not _validate_port(external_port) or not _validate_port(internal_port):
        flash('Port numbers must be between 1 and 65535.', 'error')
        return redirect(url_for('port_forwards.index'))

    rule_id = create_port_forward(peer_id, description, protocol,
                                  int(external_port), int(internal_port))
    rule = get_port_forward(rule_id)
    if rule:
        try:
            _apply_rule(rule)
        except Exception as e:
            flash(f'Rule saved but iptables apply failed: {e}', 'warning')
            return redirect(url_for('port_forwards.index'))

    flash(f'Port forward created: {protocol.upper()} :{external_port} → {peer["vpn_ip"]}:{internal_port}', 'success')
    return redirect(url_for('port_forwards.index'))


# ── Toggle ────────────────────────────────────────────────────────────────────

@pf_bp.route('/<int:rule_id>/toggle', methods=['POST'])
@login_required
def toggle(rule_id):
    rule = get_port_forward(rule_id)
    if not rule:
        abort(404)
    new_state = not bool(rule['enabled'])
    # Apply or remove iptables rule first
    try:
        if new_state:
            _apply_rule(rule, remove=False)
        else:
            _apply_rule(rule, remove=True)
    except Exception as e:
        flash(f'iptables sync failed: {e}', 'warning')
    set_port_forward_enabled(rule_id, new_state)
    state_label = 'enabled' if new_state else 'disabled'
    flash(f'Port forward {state_label}.', 'success')
    return redirect(url_for('port_forwards.index'))


# ── Delete ────────────────────────────────────────────────────────────────────

@pf_bp.route('/<int:rule_id>/delete', methods=['POST'])
@login_required
def delete(rule_id):
    rule = get_port_forward(rule_id)
    if not rule:
        abort(404)
    if rule['enabled']:
        try:
            _apply_rule(rule, remove=True)
        except Exception as e:
            flash(f'iptables removal failed: {e}', 'warning')
    delete_port_forward(rule_id)
    flash('Port forward deleted.', 'success')
    return redirect(url_for('port_forwards.index'))
