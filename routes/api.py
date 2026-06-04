import ipaddress
import re
import subprocess
import threading
import time
import urllib.parse as _uparse
import urllib.request as _ureq
import urllib.error as _uerr
import json as _json
from collections import deque
from datetime import datetime
from flask import Blueprint, jsonify, request
from database import get_all_peers, upsert_traffic_sample, get_peer_daily_traffic
from wireguard import parse_wg_show, format_bytes, is_peer_active, format_handshake, get_interface_status, WG_INTERFACE
from routes.auth import login_required
from cache_ext import cache

api_bp = Blueprint('api', __name__)

_prev_sample  = {}
_rate_history = deque(maxlen=900)   # 900 × 1 s = 15 min

_wg_cache = {'ts': 0.0, 'data': {}}
_WG_TTL   = 0.85   # seconds

_daily_buffer   = {}   # peer_id -> {day, day_start_rx, day_start_tx, last_flush}
_FLUSH_INTERVAL = 60   # seconds between DB writes


def _live_peers():
    now = time.time()
    if now - _wg_cache['ts'] < _WG_TTL:
        return _wg_cache['data']
    data = parse_wg_show()
    _wg_cache['ts']  = now
    _wg_cache['data'] = data
    return data


@api_bp.route('/api/stats')
@login_required
def stats():
    now   = time.time()
    live  = _live_peers()
    peers = get_all_peers()
    today = datetime.utcnow().strftime('%Y-%m-%d')

    peer_stats = []
    total_rx = total_tx = 0
    total_rx_rate = total_tx_rate = 0.0
    active_count = 0

    for peer in peers:
        pub       = peer['public_key']
        peer_id   = peer['id']
        rx        = peer.get('rx_bytes') or 0
        tx        = peer.get('tx_bytes') or 0
        live_info = live.get(pub, {})

        if live_info:
            rx = live_info['rx_bytes']
            tx = live_info['tx_bytes']

        total_rx += rx
        total_tx += tx

        rx_rate = tx_rate = 0.0
        if pub in _prev_sample:
            prev_ts, prev_rx, prev_tx = _prev_sample[pub]
            dt = now - prev_ts
            if dt > 0:
                rx_rate = max(0.0, (rx - prev_rx) / dt)
                tx_rate = max(0.0, (tx - prev_tx) / dt)
        _prev_sample[pub] = (now, rx, tx)
        total_rx_rate += rx_rate
        total_tx_rate += tx_rate

        last_hs = live_info.get('last_handshake') or peer.get('last_handshake')
        active  = bool(peer.get('enabled')) and is_peer_active(last_hs)
        if active:
            active_count += 1

        # ── Daily traffic sampling ─────────────────────────────────────────
        buf = _daily_buffer.get(peer_id)
        if buf is None or buf['day'] != today:
            _daily_buffer[peer_id] = {
                'day': today, 'day_start_rx': rx,
                'day_start_tx': tx, 'last_flush': 0.0,
            }
            buf = _daily_buffer[peer_id]
        else:
            # Handle WireGuard counter reset (restart)
            if rx < buf['day_start_rx']:
                buf['day_start_rx'] = rx
            if tx < buf['day_start_tx']:
                buf['day_start_tx'] = tx

        if now - buf['last_flush'] >= _FLUSH_INTERVAL:
            day_rx = max(0, rx - buf['day_start_rx'])
            day_tx = max(0, tx - buf['day_start_tx'])
            if day_rx > 0 or day_tx > 0:
                try:
                    upsert_traffic_sample(peer_id, today, day_rx, day_tx)
                except Exception:
                    pass
            buf['last_flush'] = now

        try:
            hs_ts = int(last_hs or 0)
        except (ValueError, TypeError):
            hs_ts = 0

        peer_stats.append({
            'name':               peer['name'],
            'vpn_ip':             peer['vpn_ip'],
            'rx_bytes':           rx,
            'tx_bytes':           tx,
            'rx_rate':            round(rx_rate, 1),
            'tx_rate':            round(tx_rate, 1),
            'rx_fmt':             format_bytes(rx),
            'tx_fmt':             format_bytes(tx),
            'enabled':            bool(peer.get('enabled')),
            'is_active':          active,
            'last_handshake':     format_handshake(last_hs),
            'last_handshake_ts':  hs_ts,
        })

    _rate_history.append({
        'ts':      int(now),
        'rx_rate': round(total_rx_rate, 1),
        'tx_rate': round(total_tx_rate, 1),
    })

    return jsonify({
        'peers':         peer_stats,
        'peer_count':    len(peers),
        'active_count':  active_count,
        'total_rx_fmt':  format_bytes(total_rx),
        'total_tx_fmt':  format_bytes(total_tx),
        'total_rx_rate': round(total_rx_rate, 1),
        'total_tx_rate': round(total_tx_rate, 1),
        'wg_running':    get_interface_status()['running'],
        'history':       list(_rate_history),
    })


@api_bp.route('/api/peer-history/<int:peer_id>')
@login_required
def peer_history(peer_id):
    from database import get_peer_by_id
    peer = get_peer_by_id(peer_id)
    if not peer:
        return jsonify({'error': 'not found'}), 404
    samples = get_peer_daily_traffic(peer_id, days=30)
    return jsonify({'peer_id': peer_id, 'samples': samples})


@api_bp.route('/api/peer/<int:peer_id>/ping')
@login_required
def peer_ping(peer_id):
    import re
    import subprocess
    from database import get_peer_by_id, update_peer_ping
    peer = get_peer_by_id(peer_id)
    if not peer:
        return jsonify({'error': 'not found'}), 404
    if not peer.get('enabled'):
        return jsonify({'reachable': False, 'reason': 'Peer is disabled'})
    live_info = _live_peers().get(peer['public_key'], {})
    last_hs   = live_info.get('last_handshake') or peer.get('last_handshake')
    if not is_peer_active(last_hs):
        return jsonify({'reachable': False, 'reason': 'No recent handshake — peer may be offline'})
    try:
        r = subprocess.run(
            ['ping', '-c', '4', '-W', '2', peer['vpn_ip']],
            capture_output=True, text=True, timeout=15
        )
        out = r.stdout
        loss_m = re.search(r'(\d+)% packet loss', out)
        loss   = int(loss_m.group(1)) if loss_m else 100
        rtt_m  = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms', out)
        if rtt_m:
            min_ms = float(rtt_m.group(1))
            avg_ms = float(rtt_m.group(2))
            max_ms = float(rtt_m.group(3))
            update_peer_ping(peer_id, avg_ms)
            return jsonify({'reachable': True, 'loss': loss,
                            'min': min_ms, 'avg': avg_ms, 'max': max_ms})
        return jsonify({'reachable': False,
                        'reason': f'Ping sent but no RTT in output (loss: {loss}%)', 'loss': loss})
    except subprocess.TimeoutExpired:
        return jsonify({'reachable': False, 'reason': 'Ping timed out'})
    except Exception as e:
        return jsonify({'reachable': False, 'reason': str(e)})


@api_bp.route('/api/peer/<int:peer_id>/kill', methods=['POST'])
@login_required
def peer_kill(peer_id):
    """Immediately remove peer from wg0 and disable in DB. Peer record is preserved."""
    from database import get_peer_by_id, set_peer_enabled, log_connection_event
    from wireguard import remove_peer_from_interface
    peer = get_peer_by_id(peer_id)
    if not peer:
        return jsonify({'error': 'not found'}), 404
    try:
        remove_peer_from_interface(peer['public_key'])
    except Exception as e:
        return jsonify({'error': f'wg remove failed: {e}'}), 500
    set_peer_enabled(peer_id, False)
    try:
        log_connection_event(peer_id, 'killed', peer['vpn_ip'])
    except Exception:
        pass
    try:
        from notifications import send_notification
        send_notification(
            'peer_killed',
            f'⚡ Peer *{peer["name"]}* was killed (force disconnected)',
            severity='warning',
        )
    except Exception:
        pass
    try:
        from database import audit
        xff = request.headers.get('X-Forwarded-For', '')
        ip = xff.split(',')[0].strip() if xff else (request.remote_addr or 'unknown')
        audit('peer.kill', target_type='peer', target_id=peer_id,
              target_name=peer['name'], actor_ip=ip)
    except Exception:
        pass
    return jsonify({'ok': True, 'peer_name': peer['name'], 'peer_id': peer_id})


@api_bp.route('/api/events/latest')
@login_required
def events_latest():
    """Return connection events from the last ~70 seconds (slightly > poll interval)."""
    from database import get_recent_connection_events
    return jsonify({'events': get_recent_connection_events(seconds=70)})


@api_bp.route('/api/events/recent')
@login_required
def events_recent():
    """Return the most recent N connection events for the dashboard feed widget."""
    from database import get_connection_events
    limit = request.args.get('limit', 12, type=int)
    if limit < 1:
        limit = 1
    if limit > 50:
        limit = 50
    return jsonify({'events': get_connection_events(limit=limit)})


@api_bp.route('/api/peer/<int:peer_id>/sparkline')
@login_required
def peer_sparkline(peer_id):
    """Return last 10 bytes/sec rate values for the peer (oldest→newest)."""
    from datetime import datetime as dt
    from database import get_peer_by_id, get_peer_bandwidth_snapshots
    peer = get_peer_by_id(peer_id)
    if not peer:
        return jsonify({'error': 'not found'}), 404
    snaps = get_peer_bandwidth_snapshots(peer_id, limit=11)
    values = []
    if len(snaps) >= 2:
        for i in range(1, len(snaps)):
            prev, curr = snaps[i - 1], snaps[i]
            try:
                t1 = dt.fromisoformat(prev['recorded_at'])
                t2 = dt.fromisoformat(curr['recorded_at'])
                secs = (t2 - t1).total_seconds()
                if secs <= 0:
                    continue
                rx = max(0.0, (curr['rx_bytes'] - prev['rx_bytes']) / secs)
                tx = max(0.0, (curr['tx_bytes'] - prev['tx_bytes']) / secs)
                values.append(round(rx + tx, 1))
            except Exception:
                continue
    return jsonify({'peer_id': peer_id, 'values': values})


@api_bp.route('/api/peer/<int:peer_id>/bandwidth')
@login_required
def peer_bandwidth(peer_id):
    from datetime import datetime as dt
    from database import get_peer_by_id, get_peer_bandwidth_snapshots
    peer = get_peer_by_id(peer_id)
    if not peer:
        return jsonify({'error': 'not found'}), 404
    snaps = get_peer_bandwidth_snapshots(peer_id, limit=61)
    if len(snaps) < 2:
        return jsonify({'peer_id': peer_id, 'points': []})
    points = []
    for i in range(1, len(snaps)):
        prev, curr = snaps[i - 1], snaps[i]
        try:
            t1 = dt.fromisoformat(prev['recorded_at'])
            t2 = dt.fromisoformat(curr['recorded_at'])
            secs = (t2 - t1).total_seconds()
            if secs <= 0:
                continue
            rx_rate = max(0.0, curr['rx_bytes'] - prev['rx_bytes']) / secs
            tx_rate = max(0.0, curr['tx_bytes'] - prev['tx_bytes']) / secs
            points.append({
                'ts':      curr['recorded_at'][11:16],   # HH:MM for chart label
                'rx_rate': round(rx_rate, 1),
                'tx_rate': round(tx_rate, 1),
            })
        except Exception:
            continue
    return jsonify({'peer_id': peer_id, 'points': points})


# ── Pi-hole v6 stats ─────────────────────────────────────────────────────────

_pihole_session     = {'sid': None, 'expires': 0.0}
_pihole_stats_cache = {'ts': 0.0, 'data': None}
_PIHOLE_STATS_TTL   = 55.0   # slightly under 60 s frontend poll


def _pihole_url(path):
    # PIHOLE_URL is the base of the Pi-hole web/API (may include a /pihole
    # prefix when FTL's webserver.paths.webhome is changed for reverse-proxy
    # mounting). Tolerate trailing slashes either way.
    import os
    base = os.getenv('PIHOLE_URL', 'http://10.9.0.1:8088').rstrip('/')
    return f"{base}/api/{path.lstrip('/')}"


def _pihole_logout(sid):
    """DELETE a Pi-hole v6 session to free a seat."""
    try:
        req = _ureq.Request(
            _pihole_url('auth'),
            headers={'X-FTL-SID': sid},
            method='DELETE',
        )
        _ureq.urlopen(req, timeout=3)
    except Exception:
        pass


def _pihole_auth():
    """Return a valid Pi-hole v6 session SID, refreshing if expired."""
    import os
    now = time.time()
    if _pihole_session['sid'] and now < _pihole_session['expires']:
        return _pihole_session['sid']
    # Logout old session before creating a new one to free the seat
    if _pihole_session['sid']:
        _pihole_logout(_pihole_session['sid'])
    _pihole_session['sid'] = None
    password = os.getenv('PIHOLE_PASSWORD', '')
    if not password:
        return None
    try:
        payload = _json.dumps({'password': password}).encode()
        req = _ureq.Request(
            _pihole_url('auth'),
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with _ureq.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
        sess = data.get('session', {})
        if sess.get('valid') and sess.get('sid'):
            _pihole_session['sid']     = sess['sid']
            _pihole_session['expires'] = now + sess.get('validity', 1800) - 30
            return _pihole_session['sid']
    except Exception:
        pass
    return None


def _fetch_pihole_summary():
    """Fetch Pi-hole v6 /api/stats/summary, cached for 55 s. Returns dict or None."""
    now = time.time()
    if (now - _pihole_stats_cache['ts'] < _PIHOLE_STATS_TTL
            and _pihole_stats_cache['data'] is not None):
        return _pihole_stats_cache['data']
    sid = _pihole_auth()
    if not sid:
        return None
    try:
        req = _ureq.Request(
            _pihole_url('stats/summary'),
            headers={'X-FTL-SID': sid},
        )
        with _ureq.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
        _pihole_stats_cache['ts']   = now
        _pihole_stats_cache['data'] = data
        return data
    except Exception:
        return None


@api_bp.route('/api/pihole/top-blocked')
@login_required
@cache.cached(timeout=55, key_prefix='pihole_top_blocked')
def pihole_top_blocked():
    """Return top 10 blocked domains from Pi-hole v6 API."""
    import os
    if not os.getenv('PIHOLE_ENABLED'):
        return jsonify({'ok': False})
    sid = _pihole_auth()
    if not sid:
        return jsonify({'ok': False, 'reason': 'auth failed'})
    try:
        req = _ureq.Request(
            _pihole_url('stats/top_blocked?count=10'),
            headers={'X-FTL-SID': sid},
        )
        with _ureq.urlopen(req, timeout=4) as resp:
            raw = _json.loads(resp.read())
        blocked = raw.get('blocked', raw.get('top_blocked', {}))
        if isinstance(blocked, dict):
            items = [{'domain': k, 'count': v} for k, v in blocked.items()]
        elif isinstance(blocked, list):
            items = blocked
        else:
            items = []
        items = sorted(items, key=lambda x: x.get('count', 0), reverse=True)[:10]
        return jsonify({'ok': True, 'items': items})
    except Exception as e:
        return jsonify({'ok': False, 'reason': str(e)})


@api_bp.route('/api/pihole/peer-queries/<vpn_ip>')
@login_required
def pihole_peer_queries(vpn_ip):
    """Return last 20 DNS queries for a specific VPN IP from Pi-hole v6 API."""
    import os
    if not os.getenv('PIHOLE_ENABLED'):
        return jsonify({'ok': False, 'reason': 'pihole disabled'})
    # Validate vpn_ip is a real IPv4 address before splicing into the URL,
    # otherwise a logged-in caller could inject extra Pi-hole API parameters.
    try:
        ipaddress.IPv4Address(vpn_ip)
    except (ValueError, ipaddress.AddressValueError):
        return jsonify({'ok': False, 'reason': 'invalid vpn_ip'})
    sid = _pihole_auth()
    if not sid:
        return jsonify({'ok': False, 'reason': 'auth failed'})
    try:
        req = _ureq.Request(
            _pihole_url('queries?client=' + _uparse.quote(vpn_ip, safe='') + '&length=100'),
            headers={'X-FTL-SID': sid},
        )
        with _ureq.urlopen(req, timeout=4) as resp:
            raw = _json.loads(resp.read())
        rows = raw.get('queries', raw.get('data', []))
        results = []
        for q in rows[:20]:
            results.append({
                'time':   q.get('time', 0),
                'domain': q.get('domain', ''),
                'status': q.get('status', ''),
                'type':   q.get('type', 'A'),
            })
        return jsonify({'ok': True, 'queries': results})
    except Exception as e:
        return jsonify({'ok': False, 'reason': str(e)})


@api_bp.route('/api/pihole-stats')
@login_required
def pihole_stats_api():
    import os
    if not os.getenv('PIHOLE_ENABLED'):
        return jsonify({'enabled': False})
    data = _fetch_pihole_summary()
    if data is None:
        return jsonify({'enabled': True, 'reachable': False})
    queries      = data.get('queries', {})
    clients_data = data.get('clients', {})
    gravity      = data.get('gravity', {})
    return jsonify({
        'enabled':         True,
        'reachable':       True,
        'blocked':         queries.get('blocked', 0),
        'total_queries':   queries.get('total', 0),
        'percent_blocked': round(float(queries.get('percent_blocked', 0.0)), 1),
        'domains_blocked': gravity.get('domains_being_blocked', 0),
        'unique_clients':  clients_data.get('active', 0),
    })


# ── Server health ─────────────────────────────────────────────────────────────

def _cpu_percent():
    try:
        import psutil
        return round(psutil.cpu_percent(interval=0.3), 1)
    except ImportError:
        return None


def _mem_info():
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {'percent': round(vm.percent, 1),
                'used_gb': round(vm.used / 1073741824, 2),
                'total_gb': round(vm.total / 1073741824, 2)}
    except ImportError:
        pass
    try:
        mem = {}
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':')
                mem[k.strip()] = int(v.split()[0]) * 1024
        total = mem.get('MemTotal', 0)
        avail = mem.get('MemAvailable', 0)
        used  = total - avail
        pct   = round(used / total * 100, 1) if total else 0
        return {'percent': pct,
                'used_gb': round(used / 1073741824, 2),
                'total_gb': round(total / 1073741824, 2)}
    except Exception:
        return None


def _disk_info():
    try:
        import psutil
        d = psutil.disk_usage('/')
        return {'percent': round(d.percent, 1),
                'used_gb': round(d.used / 1073741824, 2),
                'total_gb': round(d.total / 1073741824, 2)}
    except ImportError:
        import shutil
        d = shutil.disk_usage('/')
        pct = round(d.used / d.total * 100, 1) if d.total else 0
        return {'percent': pct,
                'used_gb': round(d.used / 1073741824, 2),
                'total_gb': round(d.total / 1073741824, 2)}


def _wg_uptime():
    try:
        r = subprocess.run(
            ['systemctl', 'status', f'wg-quick@{WG_INTERFACE}'],
            capture_output=True, text=True, timeout=5
        )
        m = re.search(r'Active: active \(\w+\) since (.+?)(?:;|\n)', r.stdout)
        if not m:
            return None
        since_str = m.group(1).strip()
        since_str = re.sub(r'^\w{3}\s+', '', since_str)  # strip "Thu " prefix
        from datetime import timezone
        for fmt in ['%Y-%m-%d %H:%M:%S %Z', '%Y-%m-%d %H:%M:%S']:
            try:
                dt = datetime.strptime(since_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        else:
            return None
        diff = int((datetime.now(timezone.utc) - dt).total_seconds())
        if diff < 0:
            return None
        d, rem = divmod(diff, 86400)
        h, rem = divmod(rem, 3600)
        mn     = rem // 60
        parts  = []
        if d:  parts.append(f'{d}d')
        if h or d: parts.append(f'{h}h')
        parts.append(f'{mn}m')
        return ' '.join(parts)
    except Exception:
        return None


def _pihole_status():
    """Return dict with pihole running state and blocked domain count."""
    import os
    if not os.getenv('PIHOLE_ENABLED'):
        return None
    try:
        r = subprocess.run(
            ['pihole', 'status'],
            capture_output=True, text=True, timeout=5
        )
        running = 'FTL is listening' in r.stdout or 'Pi-hole blocking is enabled' in r.stdout
        # Parse blocked count from gravity db
        blocked = None
        try:
            import sqlite3 as _sql
            with _sql.connect('/etc/pihole/gravity.db') as gc:
                row = gc.execute(
                    "SELECT COUNT(*) FROM vw_gravity"
                ).fetchone()
                if row:
                    blocked = row[0]
        except Exception:
            pass
        return {'running': running, 'blocked_domains': blocked}
    except Exception:
        return {'running': False, 'blocked_domains': None}


@api_bp.route('/api/server/health')
@login_required
@cache.cached(timeout=15, key_prefix='server_health')
def server_health():
    from database import get_last_speedtest
    from wireguard import WG_ENDPOINT
    last_st = get_last_speedtest()
    return jsonify({
        'cpu_percent':  _cpu_percent(),
        'memory':       _mem_info(),
        'disk':         _disk_info(),
        'wg_uptime':    _wg_uptime(),
        'wg_running':   get_interface_status()['running'],
        'server_ip':    WG_ENDPOINT,
        'pihole':       _pihole_status(),
        'last_speedtest': {
            'download_mbps': last_st['download_mbps'],
            'upload_mbps':   last_st['upload_mbps'],
            'ping_ms':       last_st['ping_ms'],
            'tested_at':     last_st['tested_at'],
        } if last_st else None,
    })


# ── Speedtest ─────────────────────────────────────────────────────────────────
# Job state is persisted in SQLite (see database.speedtest_job) rather than in
# module memory, so RUN and STATUS stay consistent no matter which gunicorn
# worker handles each request.

_speedtest_lock = threading.Lock()


def _run_speedtest_bg():
    import json as _json
    from database import record_speedtest, set_speedtest_job
    try:
        import shutil
        st_bin = shutil.which('speedtest-cli') or '/var/www/traverse/venv/bin/speedtest-cli'
        r = subprocess.run(
            [st_bin, '--json', '--secure'],
            capture_output=True, text=True, timeout=120
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or 'speedtest-cli failed')
        data     = _json.loads(r.stdout)
        download = round(data['download'] / 1_000_000, 1)
        upload   = round(data['upload']   / 1_000_000, 1)
        ping     = round(data['ping'], 1)
        srv      = data.get('server', {})
        srv_name = f"{srv.get('name','')}, {srv.get('country','')}".strip(', ')
        record_speedtest(download, upload, ping, srv_name)
        set_speedtest_job(False, result={'download': download, 'upload': upload,
                                          'ping': ping, 'server': srv_name})
    except Exception as exc:
        set_speedtest_job(False, error=str(exc))


@api_bp.route('/api/speedtest/run', methods=['POST'])
@login_required
def speedtest_run():
    from database import get_speedtest_job, set_speedtest_job
    with _speedtest_lock:
        if get_speedtest_job()['running']:
            return jsonify({'status': 'already_running'})
        set_speedtest_job(True)
    t = threading.Thread(target=_run_speedtest_bg, daemon=True, name='speedtest')
    t.start()
    return jsonify({'status': 'running'})


@api_bp.route('/api/speedtest/status')
@login_required
def speedtest_status():
    from database import get_speedtest_job
    return jsonify(get_speedtest_job())
