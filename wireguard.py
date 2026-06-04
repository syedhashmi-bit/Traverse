import subprocess
import os
import ipaddress
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Defaults are fallbacks only — .env is canonical. Kept aligned with the
# current deployment (post-migration) so an unset var can't silently revert to
# the old wg0/10.8.0.0/24/51820 topology.
WG_INTERFACE  = os.getenv('WG_INTERFACE',    'wg1')
WG_SUBNET     = os.getenv('WG_SUBNET',       '10.9.0.0/24')
WG_SERVER_IP  = os.getenv('WG_SERVER_VPN_IP', '10.9.0.1')
WG_ENDPOINT   = os.getenv('WG_ENDPOINT',     'your-server-ip')
WG_PORT       = os.getenv('WG_PORT',         '51821')
WG_DNS        = os.getenv('WG_DNS',          '1.1.1.1')

# Peer cap. Honour MAX_PEERS env var (default 20), clamp to the project's
# 50-peer hard ceiling. Any non-integer value falls back to the default.
_MAX_PEERS_HARD_CAP = 50
try:
    _max_peers_env = int(os.getenv('MAX_PEERS', '20'))
except (TypeError, ValueError):
    _max_peers_env = 20
MAX_PEERS = max(1, min(_max_peers_env, _MAX_PEERS_HARD_CAP))


# ── Key generation ──────────────────────────────────────────────────────────

def _run(cmd, stdin=None, check=True, timeout=10):
    result = subprocess.run(
        cmd, input=stdin, capture_output=True, text=True, timeout=timeout
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Command failed: {cmd}")
    return result.stdout.strip()

def generate_private_key():
    return _run(['wg', 'genkey'])

def generate_public_key(private_key):
    return _run(['wg', 'pubkey'], stdin=private_key)

def generate_preshared_key():
    return _run(['wg', 'genpsk'])

def generate_keypair():
    priv = generate_private_key()
    pub  = generate_public_key(priv)
    psk  = generate_preshared_key()
    return priv, pub, psk


# ── IP allocation ────────────────────────────────────────────────────────────

def get_next_vpn_ip():
    from database import get_db
    network   = ipaddress.IPv4Network(WG_SUBNET, strict=False)
    server_ip = ipaddress.IPv4Address(WG_SERVER_IP)
    hosts     = [str(h) for h in network.hosts() if h != server_ip]

    with get_db() as conn:
        used = {r[0] for r in conn.execute("SELECT vpn_ip FROM peers")}

    for ip in hosts:
        if ip not in used:
            return ip
    raise ValueError("No available VPN IPs — subnet exhausted")


# ── Server info ──────────────────────────────────────────────────────────────

def get_server_public_key():
    try:
        return _run(['wg', 'show', WG_INTERFACE, 'public-key'], check=False) or None
    except Exception:
        return None

def get_interface_status():
    try:
        r = subprocess.run(
            ['wg', 'show', WG_INTERFACE],
            capture_output=True, text=True, timeout=10
        )
        return {'running': r.returncode == 0, 'output': r.stdout}
    except Exception:
        return {'running': False, 'output': ''}


# ── Live peer management ─────────────────────────────────────────────────────

def _effective_allowed_ips(vpn_ip, tunnel_mode, custom_routes=''):
    """Return the client-side AllowedIPs string for a given tunnel mode.
    Used in client config generation only — not for wg set server-side."""
    if tunnel_mode == 'vpn_only':
        return WG_SUBNET
    if tunnel_mode == 'split':
        parts = [WG_SUBNET]
        for cidr in (custom_routes or '').split(','):
            cidr = cidr.strip()
            if cidr:
                parts.append(cidr)
        return ', '.join(parts)
    # default: full tunnel
    return '0.0.0.0/0, ::/0'


def add_peer_to_interface(public_key, preshared_key, vpn_ip,
                          tunnel_mode='full', custom_routes=''):
    """Add peer to running WireGuard interface via wg set.
    Server-side allowed-ips is always vpn_ip/32 — tunnel mode is client-side only."""
    import tempfile, os as _os
    # The preshared key is passed via a temp file (keeps it out of argv / proc
    # cmdline). It MUST live under /etc/wireguard/: the wg(8) AppArmor profile
    # only permits reading PSK files from there — a file in /tmp yields
    # "fopen: Permission denied" and the peer never reaches the live interface.
    with tempfile.NamedTemporaryFile(mode='w', suffix='.psk', dir='/etc/wireguard', delete=False) as f:
        f.write(preshared_key)
        psk_path = f.name
    try:
        _run([
            'wg', 'set', WG_INTERFACE,
            'peer', public_key,
            'preshared-key', psk_path,
            'allowed-ips', f'{vpn_ip}/32',
        ])
        _save_wg_config()
    finally:
        _os.unlink(psk_path)

def remove_peer_from_interface(public_key):
    """Remove peer from running WireGuard interface."""
    _run(['wg', 'set', WG_INTERFACE, 'peer', public_key, 'remove'], check=False)
    _save_wg_config()

def _save_wg_config():
    subprocess.run(
        ['wg-quick', 'save', WG_INTERFACE],
        capture_output=True, timeout=10
    )


# ── Statistics ───────────────────────────────────────────────────────────────

def parse_wg_show():
    """
    Returns dict keyed by public_key with last_handshake, rx_bytes, tx_bytes.
    Uses `wg show <iface> dump` which outputs TSV:
      interface: private_key public_key listen_port fwmark
      peer:      public_key  psk        endpoint allowed_ips last_handshake rx_bytes tx_bytes persistent_keepalive
    """
    try:
        out = _run(['wg', 'show', WG_INTERFACE, 'dump'], check=False)
        if not out:
            return {}
        peers = {}
        lines = out.strip().split('\n')
        for line in lines[1:]:   # first line is the interface row
            parts = line.split('\t')
            if len(parts) < 8:
                continue
            pub_key  = parts[0]
            endpoint = parts[2] if parts[2] != '(none)' else ''
            last_hs  = parts[4]
            rx       = int(parts[5]) if parts[5].isdigit() else 0
            tx       = int(parts[6]) if parts[6].isdigit() else 0
            peers[pub_key] = {
                'endpoint':       endpoint,
                'last_handshake': last_hs,
                'rx_bytes':       rx,
                'tx_bytes':       tx,
            }
        return peers
    except Exception:
        return {}

def is_peer_active(last_handshake_ts, threshold=300):
    """True if last handshake was within threshold seconds (default 5 min)."""
    if not last_handshake_ts or last_handshake_ts == '0':
        return False
    try:
        return (time.time() - int(last_handshake_ts)) < threshold
    except (ValueError, TypeError):
        return False


# ── Config generation ─────────────────────────────────────────────────────────

def _safe_conf_value(s, allowed_extra=''):
    """Strip anything that could break out of a WireGuard config value into a
    new section/key. Allows a-z A-Z 0-9 . : / , - = + plus characters in
    `allowed_extra`. Anything else (including newlines, brackets, '#') is
    dropped — this is the last line of defence behind form validation."""
    if not s:
        return ''
    base = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:/,-=+ '
    allowed = set(base + allowed_extra)
    return ''.join(c for c in s if c in allowed).strip()


def generate_client_config(peer, server_public_key):
    raw_endpoint = peer.get('endpoint') or WG_ENDPOINT
    raw_dns      = peer.get('dns_override') or peer.get('dns') or WG_DNS
    tunnel_mode   = peer.get('tunnel_mode') or 'full'
    raw_custom    = peer.get('custom_routes') or ''
    # Sanitize anything a user can influence so a crafted value can't smuggle
    # extra [Interface]/[Peer] sections into the downloaded client config.
    endpoint      = _safe_conf_value(raw_endpoint)
    dns           = _safe_conf_value(raw_dns)
    custom_routes = _safe_conf_value(raw_custom)
    allowed_ips   = _effective_allowed_ips(peer['vpn_ip'], tunnel_mode, custom_routes)
    # Keys are validated upstream (regex in routes/settings.py + wg-generated
    # in routes/peers.py); still strip whitespace/newlines defensively.
    priv = (peer['private_key'] or '').strip()
    psk  = (peer['preshared_key'] or '').strip()
    spk  = (server_public_key or '').strip()
    return (
        f"[Interface]\n"
        f"PrivateKey = {priv}\n"
        f"Address = {peer['vpn_ip']}/32\n"
        f"DNS = {dns}\n"
        f"\n"
        f"[Peer]\n"
        f"PublicKey = {spk}\n"
        f"PresharedKey = {psk}\n"
        f"Endpoint = {endpoint}:{WG_PORT}\n"
        f"AllowedIPs = {allowed_ips}\n"
        f"PersistentKeepalive = 25\n"
    )


# ── Formatting helpers ────────────────────────────────────────────────────────

def format_bytes(b):
    b = int(b or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if b < 1024:
            return f"{b:.1f} {unit}" if unit != 'B' else f"{b} B"
        b /= 1024
    return f"{b:.1f} PB"

def format_handshake(ts):
    if not ts or ts == '0':
        return 'Never'
    try:
        return datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%M:%S UTC')
    except (ValueError, OSError):
        return 'Unknown'

def format_handshake_short(ts):
    """Compact relative time for dense tables: '2m', '1h', '3d', 'Never'."""
    if not ts or ts == '0':
        return 'Never'
    try:
        age = int(time.time() - int(ts))
        if age < 0:      return 'now'
        if age < 60:     return f'{age}s'
        if age < 3600:   return f'{age // 60}m'
        if age < 86400:  return f'{age // 3600}h'
        return f'{age // 86400}d'
    except (ValueError, OSError):
        return '?'
