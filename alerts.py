import logging
import logging.handlers
import os
import re
import ssl
import time
import threading
import urllib.request
import urllib.parse
from contextlib import contextmanager


_INTERVAL         = 60    # poll every 60 s


# ── Logging ────────────────────────────────────────────────────────────────
# The poller's outer loop deliberately swallows exceptions so a single bad
# tick doesn't kill the daemon thread (per CLAUDE.md). The cost of that
# robustness was that every failure was completely silent. This logger
# captures the section name and traceback for any swallowed exception so
# operators can actually see what's breaking. Falls back to stderr if no
# writable log destination is available (e.g. dev / CI).
_DEFAULT_LOG = '/var/log/traverse/poller.log'


def _build_logger():
    lg = logging.getLogger('traverse.poller')
    if lg.handlers:
        return lg
    lg.setLevel(logging.INFO)

    path = os.getenv('TRAVERSE_POLLER_LOG', _DEFAULT_LOG)
    handler = None
    try:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=512_000, backupCount=3,
        )
    except Exception:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s [%(name)s] %(message)s',
    ))
    lg.addHandler(handler)
    lg.propagate = False
    return lg


_log = _build_logger()


@contextmanager
def _swallow(section):
    """Log any exception under a section label, then continue.

    Replaces the silent `try: ... except Exception: pass` pattern in
    the section guards inside _check(). Section-level granularity is
    deliberate — individual notification-dispatch failures stay
    silent to avoid log spam on every tick.
    """
    try:
        yield
    except Exception:
        _log.exception('poller section %r failed', section)
_wg_was_down      = False
_last_wg_alert    = 0.0
_peer_alerted_at  = {}    # public_key -> unix ts of last alert
_peer_last_active = {}    # public_key -> bool (was active last check)
_peer_last_ip     = {}    # peer_id -> last endpoint IP seen
_pihole_was_down  = False  # tracks pi-hole up/down transition
_inactive_notified = {}    # peer_id -> last unix ts notified about long inactivity
_expired_notified  = set() # peer_ids already notified as expired
_prev_traffic     = None   # {'ts','rx','tx'} previous global totals, for rate calc


def _extract_ip_port(endpoint):
    """Parse (ip, port) from a WireGuard endpoint string. Returns (None, None) on failure."""
    if not endpoint or endpoint == '(none)':
        return None, None
    if endpoint.startswith('['):
        # IPv6: [addr]:port
        try:
            host, _, port_part = endpoint[1:].partition(']:')
            return host, int(port_part) if port_part.isdigit() else None
        except Exception:
            return None, None
    host, _, port_part = endpoint.rpartition(':')
    if host and port_part.isdigit():
        return host, int(port_part)
    return endpoint, None


def _pihole_alive():
    """Return True/False if a quick TCP probe to the Pi-hole admin URL succeeds."""
    import socket
    from urllib.parse import urlparse
    raw = os.getenv('PIHOLE_URL', 'http://10.9.0.1:8088')
    try:
        u = urlparse(raw if '://' in raw else 'http://' + raw)
        host = u.hostname or '10.9.0.1'
        port = u.port or (443 if u.scheme == 'https' else 80)
    except Exception:
        host, port = '10.9.0.1', 8088
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except Exception:
        return False


_LEGACY_TG_TOKEN_RE = re.compile(r'^\d{6,12}:[A-Za-z0-9_-]{30,80}$')


def _legacy_telegram_fallback(html_msg):
    """Best-effort Telegram send via the legacy env-var path.

    Reserved for WG state-change notifications: if the DB is broken
    or the notification settings table is empty, send_notification()
    silently drops messages — and a missing WG-down alert is exactly
    the kind of "you needed to know" event the legacy path exists for.
    Everywhere else routes through send_notification() so users get
    multi-channel delivery + per-event toggles.
    """
    token   = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id:
        return
    # Refuse a malformed token instead of splicing it into api.telegram.org —
    # an attacker who edits .env shouldn't be able to redirect the request.
    if not _LEGACY_TG_TOKEN_RE.match(token):
        return
    data = urllib.parse.urlencode({
        'chat_id': chat_id, 'text': html_msg, 'parse_mode': 'HTML',
    }).encode()
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage', data=data
    )
    try:
        urllib.request.urlopen(req, context=ctx, timeout=10)
    except Exception:
        _log.exception('legacy telegram fallback failed')


_BW_ANOMALY_MIN_RATE  = 1_048_576   # absolute floor — 1 MB/s before we flag
_BW_ANOMALY_RATIO     = 5            # current must exceed avg × this


def compute_bw_anomaly(snaps,
                       min_rate=_BW_ANOMALY_MIN_RATE,
                       ratio=_BW_ANOMALY_RATIO):
    """Pure helper: given an ordered list of bandwidth snapshots, decide
    whether the most-recent interval is anomalous compared to the prior ones.

    Each `snap` is a dict with `recorded_at` (ISO string), `rx_bytes`, `tx_bytes`.
    Returns (current_rate, avg_rate, is_anomaly).
    - current_rate / avg_rate are bytes-per-second floats; both are 0.0 when
      there isn't enough data to compute them.
    - is_anomaly is True only when current > min_rate AND current > avg * ratio,
      matching the heuristic the poller has used since 1.0.
    """
    from datetime import datetime as _dt
    if not snaps or len(snaps) < 3:
        return 0.0, 0.0, False
    rates = []
    for i in range(1, len(snaps)):
        prev, curr = snaps[i - 1], snaps[i]
        try:
            t1 = _dt.fromisoformat(prev['recorded_at'])
            t2 = _dt.fromisoformat(curr['recorded_at'])
            secs = (t2 - t1).total_seconds()
            if secs <= 0:
                continue
            total_bytes = max(0, (curr['rx_bytes'] - prev['rx_bytes']) +
                                  (curr['tx_bytes'] - prev['tx_bytes']))
            rates.append(total_bytes / secs)
        except Exception:
            continue
    if len(rates) < 2:
        return 0.0, 0.0, False
    current_rate = rates[-1]
    avg_rate     = sum(rates[:-1]) / len(rates[:-1])
    is_anomaly = (
        current_rate > min_rate and
        avg_rate > 0 and
        current_rate > avg_rate * ratio
    )
    return current_rate, avg_rate, is_anomaly


def _notify(event_type, message, severity='info', legacy_html=None):
    """Single send-path for the poller.

    Always tries send_notification() (multi-channel, DB-backed,
    honours per-event toggles). For the two state-change events that
    must never be silently lost (`wg_down`, `wg_recovered`), the
    caller passes `legacy_html=...` and we additionally fire the
    env-var Telegram path as belt-and-suspenders.
    """
    try:
        from notifications import send_notification
        send_notification(event_type, message, severity=severity)
    except Exception:
        _log.exception('send_notification(%r) failed', event_type)
    if legacy_html is not None:
        _legacy_telegram_fallback(legacy_html)


def _check():
    global _wg_was_down, _last_wg_alert

    from wireguard import get_interface_status, parse_wg_show, remove_peer_from_interface, WG_INTERFACE
    from database import (
        get_all_peers, disable_expired_peers,
        log_connection_event, trim_connection_events,
        create_alert, record_bandwidth_snapshot,
    )
    from datetime import datetime, date

    now = time.time()

    # ── WireGuard interface ────────────────────────────────────────────────
    running = get_interface_status()['running']
    if not running:
        if not _wg_was_down or (now - _last_wg_alert) >= 300:
            _wg_was_down   = True
            _last_wg_alert = now
            _notify(
                'wg_down',
                f'🚨 WireGuard ({WG_INTERFACE}) is DOWN on traverse server',
                severity='critical',
                legacy_html='🔴 <b>Traverse VPN</b>\n\nWireGuard (<code>wg0</code>) is <b>DOWN</b>.',
            )
        try:
            create_alert('wg_down', f'WireGuard {WG_INTERFACE} is not running', severity='critical')
        except Exception:
            pass
    else:
        if _wg_was_down:
            _wg_was_down   = False
            _last_wg_alert = now
            _notify(
                'wg_recovered',
                f'✅ WireGuard ({WG_INTERFACE}) is back UP',
                severity='info',
                legacy_html='🟢 <b>Traverse VPN</b>\n\nWireGuard is back <b>UP</b>.',
            )

    # ── Expire peers ───────────────────────────────────────────────────────
    with _swallow('expire_peers'):
        for p in disable_expired_peers():
            try:
                remove_peer_from_interface(p['public_key'])
            except Exception:
                pass
            try:
                from notifications import send_notification
                send_notification(
                    'peer_expired',
                    f'📅 *{p["name"]}* has expired and been disabled',
                    severity='info',
                )
            except Exception:
                pass

    # ── Apply per-peer schedules ──────────────────────────────────────────
    # Each scheduled peer either should-be-enabled or should-be-disabled
    # based on the current time in its own timezone. We flip wg0 + DB
    # only on transitions to avoid thrashing.
    with _swallow('schedules'):
        from datetime import datetime as _dt
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            ZoneInfo = None  # python <3.9 — schedule feature falls back to UTC
        from database import (
            get_all_peer_schedules, set_peer_enabled, audit as _audit,
        )
        from wireguard import add_peer_to_interface as _add_peer
        from schedules import is_within_window
        for sch in get_all_peer_schedules():
            if not sch.get('enabled'):
                continue
            tz_name = sch.get('timezone') or 'UTC'
            tz = None
            if ZoneInfo is not None:
                try:
                    tz = ZoneInfo(tz_name)
                except Exception:
                    tz = None
            now_local = _dt.now(tz) if tz is not None else _dt.utcnow()
            in_window = is_within_window(
                now_local,
                sch['days_of_week'],
                sch['enabled_from'],
                sch['enabled_to'],
            )
            desired = bool(in_window)
            current = bool(sch.get('peer_enabled'))
            if desired == current:
                continue
            set_peer_enabled(sch['peer_id'], desired)
            try:
                if desired:
                    _add_peer(
                        sch['public_key'], sch['preshared_key'], sch['vpn_ip'],
                        sch.get('tunnel_mode') or 'full',
                        sch.get('custom_routes') or '',
                    )
                else:
                    remove_peer_from_interface(sch['public_key'])
            except Exception:
                _log.exception('schedule wg0 sync failed for peer %s', sch['peer_name'])
            try:
                _audit(
                    'peer.schedule_' + ('enabled' if desired else 'disabled'),
                    target_type='peer', target_id=sch['peer_id'],
                    target_name=sch['peer_name'],
                    actor_ip='scheduler',
                )
            except Exception:
                pass
            try:
                from notifications import send_notification
                send_notification(
                    'peer_schedule_applied',
                    (f'⏰ *{sch["peer_name"]}* '
                     + ('enabled' if desired else 'disabled') +
                     ' by schedule'),
                    severity='info',
                )
            except Exception:
                pass

    live  = parse_wg_show()
    peers = get_all_peers()

    # ── Connection event tracking ──────────────────────────────────────────
    with _swallow('connection_events'):
        for peer in peers:
            pub     = peer['public_key']
            peer_id = peer['id']
            vpn_ip  = peer['vpn_ip']
            live_info = live.get(pub, {})
            last_hs   = live_info.get('last_handshake') or peer.get('last_handshake')
            try:
                hs_ts = int(last_hs or 0)
            except (ValueError, TypeError):
                hs_ts = 0
            is_active = hs_ts > 0 and (now - hs_ts) < 180
            was_active = _peer_last_active.get(pub)
            if was_active is not None:
                if is_active and not was_active:
                    log_connection_event(peer_id, 'connected', vpn_ip)
                    try:
                        from notifications import send_notification
                        send_notification(
                            'peer_connected',
                            f'✅ *{peer["name"]}* connected to traverse',
                            severity='info',
                        )
                    except Exception:
                        pass
                elif not is_active and was_active:
                    log_connection_event(peer_id, 'disconnected', vpn_ip)
                    try:
                        from notifications import send_notification
                        send_notification(
                            'peer_disconnected',
                            f'🔌 *{peer["name"]}* disconnected from traverse',
                            severity='warning',
                        )
                    except Exception:
                        pass
            _peer_last_active[pub] = is_active
        trim_connection_events(1000)

    # ── Endpoint location tracking ────────────────────────────────────────
    with _swallow('endpoint_location'):
        from database import record_peer_location
        # Lazy import to avoid circular dep with routes.map
        from routes.map import _geolocate_ip
        for peer in peers:
            peer_id   = peer['id']
            pub       = peer['public_key']
            live_info = live.get(pub, {})
            endpoint  = live_info.get('endpoint', '') or ''
            if not endpoint:
                continue
            ip, port = _extract_ip_port(endpoint)
            if not ip:
                continue
            if _peer_last_ip.get(peer_id) == ip:
                # Same IP — bump last_seen_at without geo lookup
                record_peer_location(peer_id, ip, endpoint_port=port)
                continue
            _peer_last_ip[peer_id] = ip
            # New IP for this peer — try geo lookup, but don't block on failure
            geo = _geolocate_ip(ip)
            if geo:
                record_peer_location(
                    peer_id, ip, endpoint_port=port,
                    geo_country=geo.get('country'), geo_city=geo.get('city'),
                    geo_lat=geo.get('lat'), geo_lon=geo.get('lon'),
                    geo_country_code=geo.get('country_code'),
                )
            else:
                record_peer_location(peer_id, ip, endpoint_port=port)

    # ── Bandwidth snapshots ────────────────────────────────────────────────
    with _swallow('bandwidth_snapshots'):
        for peer in peers:
            pub = peer['public_key']
            live_info = live.get(pub, {})
            rx = live_info.get('rx_bytes') if live_info else None
            tx = live_info.get('tx_bytes') if live_info else None
            if rx is None:
                rx = peer.get('rx_bytes') or 0
            if tx is None:
                tx = peer.get('tx_bytes') or 0
            record_bandwidth_snapshot(peer['id'], rx, tx)

    # ── Global traffic timeline (dashboard 24h/7d chart) ──────────────────
    with _swallow('traffic_timeline'):
        import time as _time
        from database import record_traffic_sample
        global _prev_traffic
        now_ts = int(_time.time())
        g_rx = sum(int((live.get(p['public_key']) or {}).get('rx_bytes') or 0) for p in peers)
        g_tx = sum(int((live.get(p['public_key']) or {}).get('tx_bytes') or 0) for p in peers)
        if _prev_traffic is not None:
            dt = now_ts - _prev_traffic['ts']
            if dt > 0:
                # max(0, …) clamps counter resets (wg restart / peer removal).
                rx_rate = max(0, g_rx - _prev_traffic['rx']) / dt
                tx_rate = max(0, g_tx - _prev_traffic['tx']) / dt
                record_traffic_sample(now_ts, round(rx_rate, 1), round(tx_rate, 1))
        _prev_traffic = {'ts': now_ts, 'rx': g_rx, 'tx': g_tx}

    # ── Bandwidth anomaly detection ───────────────────────────────────────
    with _swallow('bandwidth_anomaly'):
        from database import get_peer_bandwidth_snapshots
        for peer in peers:
            snaps = get_peer_bandwidth_snapshots(peer['id'], limit=12)
            current_rate, avg_rate, is_anomaly = compute_bw_anomaly(snaps)
            if is_anomaly:
                def _fmt(b):
                    if b < 1024: return f'{b:.0f} B/s'
                    if b < 1048576: return f'{b/1024:.1f} KB/s'
                    return f'{b/1048576:.2f} MB/s'
                msg = (f'{peer["name"]} unusual bandwidth: '
                       f'{_fmt(current_rate)} vs avg {_fmt(avg_rate)}')
                try:
                    from notifications import send_notification
                    send_notification(
                        'bw_anomaly',
                        f'📈 *{peer["name"]}* unusual bandwidth: {_fmt(current_rate)} (avg {_fmt(avg_rate)})',
                        severity='warning',
                    )
                except Exception:
                    pass
                # Deduplicate: skip if identical unseen alert in last 10 min
                try:
                    with __import__('sqlite3').connect(
                            __import__('os').path.join(
                                __import__('os').path.dirname(__import__('os').path.abspath(__file__)),
                                __import__('os').getenv('DATABASE_PATH', 'database.db'))) as _c:
                        _c.row_factory = __import__('sqlite3').Row
                        _c.execute("PRAGMA journal_mode=WAL")
                        cutoff = __import__('datetime').datetime.utcnow().replace(
                            microsecond=0).isoformat()
                        row = _c.execute(
                            "SELECT id FROM alerts WHERE peer_id=? AND type='bw_anomaly'"
                            " AND seen=0 AND created_at > datetime(?, '-10 minutes')",
                            (peer['id'], cutoff)
                        ).fetchone()
                        if row:
                            continue
                except Exception:
                    pass
                create_alert('bw_anomaly', msg, peer_id=peer['id'], severity='warning')

    # ── Alert conditions ───────────────────────────────────────────────────
    with _swallow('alert_conditions'):
        today_str = datetime.utcnow().strftime('%Y-%m-%d')
        today_obj = date.today()
        seven_days = 7 * 86400

        for peer in peers:
            pub = peer['public_key']
            live_info = live.get(pub, {})
            last_hs = live_info.get('last_handshake') or peer.get('last_handshake')
            try:
                hs_ts = int(last_hs or 0)
            except (ValueError, TypeError):
                hs_ts = 0

            # Inactive 7+ days
            if hs_ts > 0 and (now - hs_ts) >= seven_days:
                days_ago = int((now - hs_ts) / 86400)
                create_alert(
                    'peer_inactive', f'{peer["name"]} hasn\'t connected in {days_ago} days',
                    peer_id=peer['id'], severity='warning'
                )
                # Throttle: re-notify at most once per 24 h per peer
                last_notif = _inactive_notified.get(peer['id'], 0)
                if (now - last_notif) >= 86400:
                    _inactive_notified[peer['id']] = now
                    try:
                        from notifications import send_notification
                        send_notification(
                            'peer_inactive_long',
                            f'⚠️ *{peer["name"]}* hasn\'t connected in {days_ago} days',
                            severity='warning',
                        )
                    except Exception:
                        pass

            # Expired
            if peer.get('expires_at') and peer['expires_at'] <= today_str:
                try:
                    exp_date = date.fromisoformat(peer['expires_at'])
                    days_ago = (today_obj - exp_date).days
                    label = f'{days_ago} day{"s" if days_ago != 1 else ""} ago'
                except Exception:
                    label = peer['expires_at']
                create_alert(
                    'peer_expired', f'{peer["name"]} expired {label}',
                    peer_id=peer['id'], severity='info'
                )

    # ── Pi-hole up/down ────────────────────────────────────────────────────
    if os.getenv('PIHOLE_ENABLED', '').strip().lower() in ('1', 'true', 'yes', 'on'):
        global _pihole_was_down
        try:
            ph_running = _pihole_alive()
        except Exception:
            ph_running = None  # unknown — don't flip state
        if ph_running is False:
            if not _pihole_was_down:
                _pihole_was_down = True
                try:
                    from notifications import send_notification
                    send_notification(
                        'pihole_down',
                        '⚠️ Pi-hole is unreachable or stopped',
                        severity='warning',
                    )
                except Exception:
                    pass
        elif ph_running is True:
            if _pihole_was_down:
                _pihole_was_down = False
                try:
                    from notifications import send_notification
                    send_notification(
                        'pihole_recovered',
                        '✅ Pi-hole is back online',
                        severity='info',
                    )
                except Exception:
                    pass

    # ── Peer inactivity alerts (env-configurable threshold) ────────────────
    # ALERT_INACTIVE_HOURS sets the per-peer inactivity threshold for the
    # 'peer_inactive_hours' event (separate from the hard-coded 7-day
    # 'peer_inactive_long' event handled above). Routes through the same
    # send_notification path as the rest of the codebase, so it respects
    # per-event toggles on /notifications and reaches every enabled channel.
    inactive_hours = float(os.getenv('ALERT_INACTIVE_HOURS', '0'))
    if inactive_hours <= 0:
        return

    inactive_secs = inactive_hours * 3600
    cooldown      = 3600

    for peer in peers:
        if not peer.get('enabled'):
            continue
        pub       = peer['public_key']
        live_info = live.get(pub, {})
        last_hs   = live_info.get('last_handshake') or peer.get('last_handshake')
        try:
            hs_ts = int(last_hs or 0)
        except (ValueError, TypeError):
            continue
        if hs_ts <= 0:
            continue
        age = now - hs_ts
        if age < inactive_secs:
            continue
        if now - _peer_alerted_at.get(pub, 0) < cooldown:
            continue
        _peer_alerted_at[pub] = now
        hours_ago = int(age / 3600)
        _notify(
            'peer_inactive_hours',
            f'⚠️ *{peer["name"]}* ({peer["vpn_ip"]}) last seen {hours_ago}h ago',
            severity='warning',
        )


def _loop():
    while True:
        try:
            _check()
        except Exception:
            _log.exception('poller tick crashed (loop continues)')
        time.sleep(_INTERVAL)


def start_alerts():
    t = threading.Thread(target=_loop, daemon=True, name='traverse-alerts')
    t.start()
    _log.info('alerts poller started (interval=%ds)', _INTERVAL)
