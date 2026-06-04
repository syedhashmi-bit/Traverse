import os
import time
from dotenv import load_dotenv
load_dotenv()
from flask import Blueprint, render_template
from database import get_all_peers, update_peer_stats, count_expired_peers, get_all_alerts
from wireguard import (
    get_interface_status, parse_wg_show,
    is_peer_active, format_bytes, format_handshake,
    format_handshake_short, WG_INTERFACE, WG_SUBNET, WG_ENDPOINT, WG_PORT,
    MAX_PEERS,
)
from routes.auth import login_required

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@login_required
def index():
    status   = get_interface_status()
    live     = parse_wg_show()
    peers    = get_all_peers()

    total_rx     = 0
    total_tx     = 0
    active_count = 0

    for peer in peers:
        pub = peer['public_key']
        if pub in live:
            hs  = live[pub]['last_handshake']
            rx  = live[pub]['rx_bytes']
            tx  = live[pub]['tx_bytes']
            update_peer_stats(pub, hs, rx, tx)
            peer['last_handshake_raw']   = hs
            peer['last_handshake']       = format_handshake(hs)
            peer['last_handshake_short'] = format_handshake_short(hs)
            peer['rx_bytes']             = rx
            peer['tx_bytes']             = tx
            if peer.get('enabled') and is_peer_active(hs):
                active_count += 1
        else:
            raw = peer.get('last_handshake') or '0'
            peer['last_handshake_raw']   = raw
            peer['last_handshake']       = format_handshake(raw)
            peer['last_handshake_short'] = format_handshake_short(raw)

        total_rx += peer.get('rx_bytes') or 0
        total_tx += peer.get('tx_bytes') or 0

    return render_template(
        'dashboard.html',
        wg_status      = status,
        peers          = peers,
        total_peers    = len(peers),
        max_peers      = MAX_PEERS,
        active_peers   = active_count,
        total_rx       = format_bytes(total_rx),
        total_tx       = format_bytes(total_tx),
        interface      = WG_INTERFACE,
        subnet         = WG_SUBNET,
        endpoint       = WG_ENDPOINT,
        wg_port        = WG_PORT,
        format_bytes   = format_bytes,
        expired_peers  = count_expired_peers(),
        pihole_enabled = bool(os.getenv('PIHOLE_ENABLED')),
        pihole_url     = os.getenv('PIHOLE_WEB_URL', '/pihole'),
        recent_alerts  = [a for a in get_all_alerts(limit=10) if not a['seen']][:3],
    )
