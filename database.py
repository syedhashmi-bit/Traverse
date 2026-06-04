import re
import sqlite3
import os
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()


# SQLite has no parameter binding for identifiers/types in ALTER TABLE, so the
# migrate loop below interpolates literal strings. These regexes are a tripwire
# in case anyone wires runtime data into that loop in the future.
_IDENT_RE     = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$')
_COLDEF_RE    = re.compile(r"^[A-Za-z0-9_'\s.()-]{1,128}$")

_DB_PATH = None

def get_db_path():
    global _DB_PATH
    if _DB_PATH is None:
        raw = os.getenv('DATABASE_PATH', 'database.db')
        if os.path.isabs(raw):
            _DB_PATH = raw
        else:
            _DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), raw)
    return _DB_PATH

@contextmanager
def get_db():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS peers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL UNIQUE,
                private_key TEXT    NOT NULL,
                public_key  TEXT    NOT NULL UNIQUE,
                preshared_key TEXT  NOT NULL,
                vpn_ip      TEXT    NOT NULL UNIQUE,
                allowed_ips TEXT    NOT NULL DEFAULT '0.0.0.0/0',
                dns         TEXT    NOT NULL DEFAULT '1.1.1.1',
                endpoint    TEXT    NOT NULL,
                enabled     INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL,
                last_handshake TEXT,
                rx_bytes    INTEGER DEFAULT 0,
                tx_bytes    INTEGER DEFAULT 0
            )
        """)
    migrate_db()


def migrate_db():
    """Idempotent — adds columns / tables introduced after initial schema."""
    with get_db() as conn:
        for col, definition in [
            ('notes',                  "TEXT NOT NULL DEFAULT ''"),
            ('device',                 "TEXT NOT NULL DEFAULT 'other'"),
            ('expires_at',             "TEXT"),
            ('config_regenerated_at',  "TEXT"),
            ('last_ping_ms',           "REAL"),
            ('last_ping_at',           "TEXT"),
            ('geo_country',            "TEXT"),
            ('geo_city',               "TEXT"),
            ('geo_lat',                "REAL"),
            ('geo_lon',                "REAL"),
            ('geo_cached_at',          "TEXT"),
            ('geo_country_code',       "TEXT"),
            ('geo_failed_at',          "TEXT"),
            ('use_pihole',             "INTEGER NOT NULL DEFAULT 1"),
            ('tunnel_mode',            "TEXT NOT NULL DEFAULT 'full'"),
            ('custom_routes',          "TEXT NOT NULL DEFAULT ''"),
            ('dns_override',           "TEXT NOT NULL DEFAULT ''"),
        ]:
            # Defence-in-depth: this loop only ever holds source-code literals,
            # but enforce a strict shape so a future contributor who mistakenly
            # threads user input here gets a hard failure instead of injection.
            if not _IDENT_RE.match(col) or not _COLDEF_RE.match(definition):
                raise ValueError(f'unsafe migrate entry: {col!r}, {definition!r}')
            try:
                conn.execute(f"ALTER TABLE peers ADD COLUMN {col} {definition}")
            except Exception:
                pass  # already exists

        conn.execute("""
            CREATE TABLE IF NOT EXISTS traffic_samples (
                peer_id  INTEGER NOT NULL,
                day      TEXT    NOT NULL,
                rx_bytes INTEGER NOT NULL DEFAULT 0,
                tx_bytes INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (peer_id, day),
                FOREIGN KEY (peer_id) REFERENCES peers(id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS connection_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id     INTEGER NOT NULL,
                event_type  TEXT    NOT NULL,
                timestamp   TEXT    NOT NULL,
                peer_vpn_ip TEXT    NOT NULL,
                FOREIGN KEY (peer_id) REFERENCES peers(id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                type       TEXT    NOT NULL,
                message    TEXT    NOT NULL,
                peer_id    INTEGER,
                severity   TEXT    NOT NULL DEFAULT 'info',
                seen       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT    NOT NULL,
                FOREIGN KEY (peer_id) REFERENCES peers(id) ON DELETE SET NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS peer_bandwidth_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id     INTEGER NOT NULL,
                rx_bytes    INTEGER NOT NULL DEFAULT 0,
                tx_bytes    INTEGER NOT NULL DEFAULT 0,
                recorded_at TEXT    NOT NULL,
                FOREIGN KEY (peer_id) REFERENCES peers(id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS peer_locations (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id       INTEGER NOT NULL,
                endpoint_ip   TEXT    NOT NULL,
                endpoint_port INTEGER,
                geo_country   TEXT,
                geo_city      TEXT,
                geo_lat       REAL,
                geo_lon       REAL,
                geo_country_code TEXT,
                first_seen_at TEXT    NOT NULL,
                last_seen_at  TEXT    NOT NULL,
                FOREIGN KEY (peer_id) REFERENCES peers(id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS port_forwards (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id       INTEGER NOT NULL,
                description   TEXT    NOT NULL DEFAULT '',
                protocol      TEXT    NOT NULL DEFAULT 'tcp',
                external_port INTEGER NOT NULL,
                internal_port INTEGER NOT NULL,
                enabled       INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT    NOT NULL,
                FOREIGN KEY (peer_id) REFERENCES peers(id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS speedtest_results (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                download_mbps REAL    NOT NULL,
                upload_mbps   REAL    NOT NULL,
                ping_ms       REAL    NOT NULL,
                server_name   TEXT    NOT NULL DEFAULT '',
                tested_at     TEXT    NOT NULL
            )
        """)

        # Single-row shared state for the in-progress speedtest. Lives in the DB
        # (not worker memory) so RUN and STATUS stay consistent across gunicorn
        # workers — otherwise a STATUS poll can land on a worker that never ran
        # the job and report result=null forever.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS speedtest_job (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                running    INTEGER NOT NULL DEFAULT 0,
                result     TEXT,
                error      TEXT,
                updated_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("INSERT OR IGNORE INTO speedtest_job (id, running) VALUES (1, 0)")

        # Global (all-peers) traffic rate timeline for the dashboard 24h/7d
        # chart. One row per poller tick (~60 s); rates are bytes/sec. Trimmed
        # to 7 days by record_traffic_sample().
        conn.execute("""
            CREATE TABLE IF NOT EXISTS traffic_timeline (
                ts      INTEGER PRIMARY KEY,
                rx_rate REAL NOT NULL DEFAULT 0,
                tx_rate REAL NOT NULL DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS notification_settings (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                channel    TEXT    NOT NULL UNIQUE,
                enabled    INTEGER NOT NULL DEFAULT 0,
                config     TEXT    NOT NULL DEFAULT '{}',
                updated_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS notification_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                channel    TEXT    NOT NULL,
                event_type TEXT    NOT NULL,
                message    TEXT    NOT NULL,
                success    INTEGER NOT NULL DEFAULT 1,
                error      TEXT    NOT NULL DEFAULT '',
                sent_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS notification_event_toggles (
                event_type TEXT PRIMARY KEY,
                enabled    INTEGER NOT NULL DEFAULT 1
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                target_type TEXT,
                target_id   INTEGER,
                target_name TEXT,
                actor_ip    TEXT,
                details     TEXT
            )
        """)

        # Per-peer schedule. One row per peer (PK on peer_id) — multi-window
        # schedules can be modeled later by promoting to a regular AUTOINCREMENT
        # PK with a peer_id FK. days_of_week is a comma-separated list of
        # weekday() ints (Mon=0..Sun=6). Times are 'HH:MM' in `timezone`.
        # When `enabled = 0`, the schedule exists but the poller leaves the
        # peer's manual state alone.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS peer_schedules (
                peer_id      INTEGER PRIMARY KEY,
                days_of_week TEXT    NOT NULL,
                enabled_from TEXT    NOT NULL,
                enabled_to   TEXT    NOT NULL,
                timezone     TEXT    NOT NULL DEFAULT 'UTC',
                enabled      INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL,
                FOREIGN KEY (peer_id) REFERENCES peers(id) ON DELETE CASCADE
            )
        """)

        # DB-backed 2FA. Single-row table (id=1) so SET-style updates are
        # always safe and there's no path to multiple admin secrets coexisting.
        # backup_codes is a JSON list of sha256 hex digests; codes are
        # single-use and removed from the list on successful consume.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS totp_settings (
                id           INTEGER PRIMARY KEY CHECK (id = 1),
                secret       TEXT    NOT NULL DEFAULT '',
                backup_codes TEXT    NOT NULL DEFAULT '[]',
                enrolled_at  TEXT,
                updated_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO totp_settings (id, secret, backup_codes) "
            "VALUES (1, '', '[]')"
        )

        # ── Indexes (idempotent) ──────────────────────────────────────────
        for _idx_sql in (
            "CREATE INDEX IF NOT EXISTS idx_peers_enabled ON peers(enabled)",
            "CREATE INDEX IF NOT EXISTS idx_peers_expires_at ON peers(expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_connection_events_peer_id ON connection_events(peer_id)",
            "CREATE INDEX IF NOT EXISTS idx_connection_events_timestamp ON connection_events(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_peer_bandwidth_snapshots_peer_id ON peer_bandwidth_snapshots(peer_id)",
            "CREATE INDEX IF NOT EXISTS idx_peer_bandwidth_snapshots_recorded_at ON peer_bandwidth_snapshots(recorded_at)",
            "CREATE INDEX IF NOT EXISTS idx_peer_bandwidth_snapshots_peer_recorded ON peer_bandwidth_snapshots(peer_id, recorded_at)",
            "CREATE INDEX IF NOT EXISTS idx_alerts_seen ON alerts(seen)",
            "CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_notification_log_sent_at ON notification_log(sent_at)",
            "CREATE INDEX IF NOT EXISTS idx_peer_locations_peer_id ON peer_locations(peer_id)",
            "CREATE INDEX IF NOT EXISTS idx_peer_locations_last_seen ON peer_locations(last_seen_at)",
            "CREATE INDEX IF NOT EXISTS idx_speedtest_tested_at ON speedtest_results(tested_at)",
            "CREATE INDEX IF NOT EXISTS idx_traffic_samples_peer_day ON traffic_samples(peer_id, day)",
            "CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts)",
            "CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)",
        ):
            try:
                conn.execute(_idx_sql)
            except Exception:
                pass

        # Seed default channel rows + event toggles on first run
        from datetime import datetime as _dt
        _now = _dt.utcnow().isoformat()
        for _ch in ('email', 'telegram', 'discord'):
            conn.execute(
                "INSERT OR IGNORE INTO notification_settings (channel, enabled, config, updated_at) "
                "VALUES (?, 0, '{}', ?)",
                (_ch, _now),
            )
        for _evt in (
            'peer_connected', 'peer_disconnected',
            'peer_inactive_long', 'peer_inactive_hours',
            'peer_expired', 'bw_anomaly', 'wg_down', 'wg_recovered',
            'pihole_down', 'pihole_recovered', 'peer_added', 'peer_deleted',
            'peer_killed', 'config_regenerated', 'psk_rotated',
            'login_success', 'login_failed',
            'totp_enrolled', 'totp_disabled', 'backup_code_used',
            'peer_schedule_applied',
        ):
            conn.execute(
                "INSERT OR IGNORE INTO notification_event_toggles (event_type, enabled) VALUES (?, 1)",
                (_evt,),
            )


def count_peers():
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM peers").fetchone()[0]


def get_all_peers():
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM peers ORDER BY created_at DESC"
        )]


def get_peer_by_id(peer_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM peers WHERE id = ?", (peer_id,)).fetchone()
        return dict(row) if row else None


def get_peer_by_name(name):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM peers WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None


def create_peer(name, private_key, public_key, preshared_key,
                vpn_ip, dns, endpoint, allowed_ips='0.0.0.0/0',
                tunnel_mode='full', custom_routes=''):
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO peers
              (name, private_key, public_key, preshared_key, vpn_ip,
               allowed_ips, dns, endpoint, enabled, created_at, updated_at,
               tunnel_mode, custom_routes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        """, (name, private_key, public_key, preshared_key, vpn_ip,
              allowed_ips, dns, endpoint, now, now, tunnel_mode, custom_routes))
        row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
        return row['id']


def update_peer_stats(public_key, last_handshake, rx_bytes, tx_bytes):
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute("""
            UPDATE peers
               SET last_handshake = ?, rx_bytes = ?, tx_bytes = ?, updated_at = ?
             WHERE public_key = ?
        """, (last_handshake, rx_bytes, tx_bytes, now, public_key))


def update_peer_notes(peer_id, notes, device):
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE peers SET notes = ?, device = ?, updated_at = ? WHERE id = ?",
            (notes, device, now, peer_id)
        )


def update_peer_tunnel(peer_id, tunnel_mode, custom_routes=''):
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE peers SET tunnel_mode = ?, custom_routes = ?, updated_at = ? WHERE id = ?",
            (tunnel_mode, custom_routes or '', now, peer_id)
        )


def update_peer_dns_override(peer_id, dns_override):
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE peers SET dns_override = ?, updated_at = ? WHERE id = ?",
            (dns_override or '', now, peer_id)
        )


def update_peer_pihole(peer_id, use_pihole):
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE peers SET use_pihole = ?, updated_at = ? WHERE id = ?",
            (1 if use_pihole else 0, now, peer_id)
        )


def set_peer_enabled(peer_id, enabled):
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE peers SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, now, peer_id)
        )


def delete_peer(peer_id):
    with get_db() as conn:
        conn.execute("DELETE FROM peers WHERE id = ?", (peer_id,))


def upsert_traffic_sample(peer_id, day, rx_bytes, tx_bytes):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO traffic_samples (peer_id, day, rx_bytes, tx_bytes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(peer_id, day) DO UPDATE SET
                rx_bytes = excluded.rx_bytes,
                tx_bytes = excluded.tx_bytes
        """, (peer_id, day, rx_bytes, tx_bytes))


def get_peer_daily_traffic(peer_id, days=30):
    """Return last N days of samples, oldest first."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT day, rx_bytes, tx_bytes
              FROM traffic_samples
             WHERE peer_id = ?
             ORDER BY day DESC
             LIMIT ?
        """, (peer_id, days)).fetchall()
    return [dict(r) for r in reversed(rows)]


# ── Expiry ────────────────────────────────────────────────────────────────────

def update_peer_expiry(peer_id, expires_at):
    """Set or clear expiry. expires_at is an ISO date string 'YYYY-MM-DD' or None."""
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE peers SET expires_at = ?, updated_at = ? WHERE id = ?",
            (expires_at or None, now, peer_id)
        )


def disable_expired_peers():
    """Disable all enabled peers whose expiry date has passed. Returns list of dicts."""
    from datetime import datetime
    today = datetime.utcnow().strftime('%Y-%m-%d')
    now   = datetime.utcnow().isoformat()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, name, public_key FROM peers
             WHERE enabled = 1
               AND expires_at IS NOT NULL
               AND expires_at <= ?
        """, (today,)).fetchall()
        disabled = [dict(r) for r in rows]
        for r in disabled:
            conn.execute(
                "UPDATE peers SET enabled = 0, updated_at = ? WHERE id = ?",
                (now, r['id'])
            )
    return disabled


def count_expired_peers():
    from datetime import datetime
    today = datetime.utcnow().strftime('%Y-%m-%d')
    with get_db() as conn:
        return conn.execute("""
            SELECT COUNT(*) FROM peers
             WHERE expires_at IS NOT NULL AND expires_at <= ?
        """, (today,)).fetchone()[0]


# ── Key regeneration ──────────────────────────────────────────────────────────

def update_peer_keys(peer_id, private_key, public_key, preshared_key):
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute("""
            UPDATE peers
               SET private_key = ?, public_key = ?, preshared_key = ?,
                   config_regenerated_at = ?, updated_at = ?
             WHERE id = ?
        """, (private_key, public_key, preshared_key, now, now, peer_id))


def rotate_peer_psk(peer_id, new_psk):
    """Replace just the preshared key. Keeps the keypair stable so the
    client's identity doesn't change — only the PSK needs to be re-synced
    on the device. Bumps config_regenerated_at so the peer-detail page
    shows that the on-disk client config is now stale."""
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute("""
            UPDATE peers
               SET preshared_key = ?, config_regenerated_at = ?, updated_at = ?
             WHERE id = ?
        """, (new_psk, now, now, peer_id))


# ── Connection events ─────────────────────────────────────────────────────────

def log_connection_event(peer_id, event_type, peer_vpn_ip):
    from datetime import datetime
    with get_db() as conn:
        conn.execute("""
            INSERT INTO connection_events (peer_id, event_type, timestamp, peer_vpn_ip)
            VALUES (?, ?, ?, ?)
        """, (peer_id, event_type, datetime.utcnow().isoformat(), peer_vpn_ip))


def get_connection_events(limit=200):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT ce.id, ce.peer_id, ce.event_type, ce.timestamp, ce.peer_vpn_ip,
                   COALESCE(p.name, '[deleted]') AS peer_name
              FROM connection_events ce
              LEFT JOIN peers p ON p.id = ce.peer_id
             ORDER BY ce.timestamp DESC
             LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_peer_connection_events(peer_id, limit=10):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, peer_id, event_type, timestamp, peer_vpn_ip
              FROM connection_events
             WHERE peer_id = ?
             ORDER BY timestamp DESC
             LIMIT ?
        """, (peer_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_recent_connection_events(seconds=70):
    """Return events whose timestamp is within the last `seconds`, with peer name/device."""
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(seconds=seconds)).isoformat()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT ce.id, ce.peer_id, ce.event_type, ce.timestamp, ce.peer_vpn_ip,
                   COALESCE(p.name, '[deleted]') AS peer_name,
                   COALESCE(p.device, 'other')   AS peer_device
              FROM connection_events ce
              LEFT JOIN peers p ON p.id = ce.peer_id
             WHERE ce.timestamp >= ?
             ORDER BY ce.id DESC
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def get_peers_last_connect_ts():
    """Return dict {peer_id: iso_timestamp} of most recent 'connected' event per peer."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT peer_id, MAX(timestamp) AS ts
              FROM connection_events
             WHERE event_type = 'connected'
             GROUP BY peer_id
        """).fetchall()
    return {r['peer_id']: r['ts'] for r in rows}


def trim_connection_events(max_rows=1000):
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM connection_events").fetchone()[0]
        if count > max_rows:
            conn.execute("""
                DELETE FROM connection_events WHERE id IN (
                    SELECT id FROM connection_events
                     ORDER BY timestamp ASC LIMIT ?
                )
            """, (count - max_rows,))


# ── Ping ──────────────────────────────────────────────────────────────────────

def update_peer_ping(peer_id, ping_ms):
    from datetime import datetime
    with get_db() as conn:
        conn.execute(
            "UPDATE peers SET last_ping_ms = ?, last_ping_at = ? WHERE id = ?",
            (ping_ms, datetime.utcnow().isoformat(), peer_id)
        )


# ── Geo cache ─────────────────────────────────────────────────────────────────

def update_peer_geo(peer_id, country, city, lat, lon, country_code=''):
    from datetime import datetime
    with get_db() as conn:
        conn.execute("""
            UPDATE peers
               SET geo_country = ?, geo_city = ?, geo_lat = ?, geo_lon = ?,
                   geo_cached_at = ?, geo_country_code = ?, geo_failed_at = NULL
             WHERE id = ?
        """, (country, city, lat, lon, datetime.utcnow().isoformat(), country_code, peer_id))


def update_peer_geo_failed(peer_id):
    from datetime import datetime
    with get_db() as conn:
        conn.execute(
            "UPDATE peers SET geo_failed_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), peer_id)
        )


# ── Alerts ────────────────────────────────────────────────────────────────────

def create_alert(type_, message, peer_id=None, severity='info'):
    """Insert alert only when no identical unseen alert already exists."""
    from datetime import datetime
    with get_db() as conn:
        existing = conn.execute("""
            SELECT id FROM alerts
             WHERE type = ? AND COALESCE(peer_id, -1) = COALESCE(?, -1) AND seen = 0
             LIMIT 1
        """, (type_, peer_id)).fetchone()
        if existing:
            return
        conn.execute("""
            INSERT INTO alerts (type, message, peer_id, severity, seen, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
        """, (type_, message, peer_id, severity, datetime.utcnow().isoformat()))


def get_all_alerts(limit=200):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT a.id, a.type, a.message, a.peer_id, a.severity, a.seen, a.created_at,
                   p.name AS peer_name
              FROM alerts a
              LEFT JOIN peers p ON p.id = a.peer_id
             ORDER BY a.created_at DESC
             LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def count_unseen_alerts():
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM alerts WHERE seen = 0").fetchone()[0]


def mark_all_alerts_seen():
    with get_db() as conn:
        conn.execute("UPDATE alerts SET seen = 1")


def dismiss_alert(alert_id):
    with get_db() as conn:
        conn.execute("UPDATE alerts SET seen = 1 WHERE id = ?", (alert_id,))


# ── Bandwidth snapshots ───────────────────────────────────────────────────────

def record_bandwidth_snapshot(peer_id, rx_bytes, tx_bytes):
    from datetime import datetime
    with get_db() as conn:
        conn.execute("""
            INSERT INTO peer_bandwidth_snapshots (peer_id, rx_bytes, tx_bytes, recorded_at)
            VALUES (?, ?, ?, ?)
        """, (peer_id, rx_bytes, tx_bytes, datetime.utcnow().isoformat()))
        count = conn.execute(
            "SELECT COUNT(*) FROM peer_bandwidth_snapshots WHERE peer_id = ?",
            (peer_id,)
        ).fetchone()[0]
        if count > 1440:
            conn.execute("""
                DELETE FROM peer_bandwidth_snapshots WHERE id IN (
                    SELECT id FROM peer_bandwidth_snapshots
                     WHERE peer_id = ? ORDER BY recorded_at ASC LIMIT ?
                )
            """, (peer_id, count - 1440))


def get_peer_bandwidth_snapshots(peer_id, limit=61):
    """Return up to `limit` snapshots oldest-first (61 → 60 rate deltas)."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, rx_bytes, tx_bytes, recorded_at
              FROM peer_bandwidth_snapshots
             WHERE peer_id = ?
             ORDER BY recorded_at DESC
             LIMIT ?
        """, (peer_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


# ── Global traffic timeline (dashboard 24h/7d chart) ──────────────────────────

def record_traffic_sample(ts, rx_rate, tx_rate, retain_days=7):
    """Append one global rate sample (bytes/sec) and trim beyond retain_days."""
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO traffic_timeline (ts, rx_rate, tx_rate) VALUES (?, ?, ?)",
            (int(ts), float(rx_rate), float(tx_rate)),
        )
        conn.execute(
            "DELETE FROM traffic_timeline WHERE ts < ?",
            (int(ts) - retain_days * 86400,),
        )


def get_traffic_timeline(since_ts):
    """Return [{ts, rx_rate, tx_rate}] at or after since_ts, oldest-first."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT ts, rx_rate, tx_rate FROM traffic_timeline WHERE ts >= ? ORDER BY ts ASC",
            (int(since_ts),),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Speedtest results ─────────────────────────────────────────────────────────

def record_speedtest(download_mbps, upload_mbps, ping_ms, server_name=''):
    from datetime import datetime
    with get_db() as conn:
        conn.execute("""
            INSERT INTO speedtest_results (download_mbps, upload_mbps, ping_ms, server_name, tested_at)
            VALUES (?, ?, ?, ?, ?)
        """, (download_mbps, upload_mbps, ping_ms, server_name, datetime.utcnow().isoformat()))
        # Keep ~50 results so the /settings sparkline has meaningful history
        # (six weeks at weekly cadence). Older runs are pruned in-place.
        conn.execute("""
            DELETE FROM speedtest_results WHERE id NOT IN (
                SELECT id FROM speedtest_results ORDER BY tested_at DESC LIMIT 50
            )
        """)


def get_speedtest_results(limit=5):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM speedtest_results ORDER BY tested_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def record_peer_location(peer_id, endpoint_ip, endpoint_port=None,
                         geo_country=None, geo_city=None,
                         geo_lat=None, geo_lon=None, geo_country_code=None):
    """Insert new location row, or update last_seen_at if (peer_id, endpoint_ip) already exists.
    Caps at 10 most-recent rows per peer."""
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM peer_locations WHERE peer_id = ? AND endpoint_ip = ?",
            (peer_id, endpoint_ip)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE peer_locations SET last_seen_at = ? WHERE id = ?",
                (now, existing['id'])
            )
            return False  # not a new location
        conn.execute("""
            INSERT INTO peer_locations
                (peer_id, endpoint_ip, endpoint_port,
                 geo_country, geo_city, geo_lat, geo_lon, geo_country_code,
                 first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (peer_id, endpoint_ip, endpoint_port,
              geo_country, geo_city, geo_lat, geo_lon, geo_country_code,
              now, now))
        # Cap to 10 records per peer (delete oldest by last_seen_at)
        count = conn.execute(
            "SELECT COUNT(*) FROM peer_locations WHERE peer_id = ?", (peer_id,)
        ).fetchone()[0]
        if count > 10:
            conn.execute("""
                DELETE FROM peer_locations WHERE id IN (
                    SELECT id FROM peer_locations WHERE peer_id = ?
                     ORDER BY last_seen_at ASC LIMIT ?
                )
            """, (peer_id, count - 10))
        return True


def get_peer_locations(peer_id, limit=5):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM peer_locations WHERE peer_id = ?
             ORDER BY last_seen_at DESC LIMIT ?
        """, (peer_id, limit)).fetchall()
    return [dict(r) for r in rows]


def count_peer_locations(peer_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM peer_locations WHERE peer_id = ?", (peer_id,)
        ).fetchone()[0]


def get_last_speedtest():
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM speedtest_results ORDER BY tested_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def set_speedtest_job(running, result=None, error=None):
    """Persist the shared speedtest job state (worker-independent)."""
    import json as _json
    from datetime import datetime
    with get_db() as conn:
        conn.execute(
            "UPDATE speedtest_job SET running=?, result=?, error=?, updated_at=? WHERE id=1",
            (1 if running else 0,
             _json.dumps(result) if result is not None else None,
             error,
             datetime.utcnow().isoformat()),
        )


def get_speedtest_job():
    """Return {running, result, error, updated_at}. A 'running' flag older than
    180 s is treated as stale (a worker died mid-test) so RUN isn't blocked."""
    import json as _json
    from datetime import datetime
    with get_db() as conn:
        row = conn.execute("SELECT * FROM speedtest_job WHERE id=1").fetchone()
    if not row:
        return {'running': False, 'result': None, 'error': None}
    running = bool(row['running'])
    if running and row['updated_at']:
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(row['updated_at'])).total_seconds()
            if age > 180:
                running = False
        except ValueError:
            pass
    result = _json.loads(row['result']) if row['result'] else None
    return {'running': running, 'result': result, 'error': row['error']}


# ── Port forwards ─────────────────────────────────────────────────────────────

def get_port_forwards(peer_id=None):
    with get_db() as conn:
        if peer_id is not None:
            rows = conn.execute("""
                SELECT pf.*, p.vpn_ip AS peer_vpn_ip, p.name AS peer_name
                  FROM port_forwards pf
                  JOIN peers p ON p.id = pf.peer_id
                 WHERE pf.peer_id = ?
                 ORDER BY pf.created_at DESC
            """, (peer_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT pf.*, p.vpn_ip AS peer_vpn_ip, p.name AS peer_name
                  FROM port_forwards pf
                  JOIN peers p ON p.id = pf.peer_id
                 ORDER BY pf.created_at DESC
            """).fetchall()
    return [dict(r) for r in rows]


def get_port_forward(rule_id):
    with get_db() as conn:
        row = conn.execute("""
            SELECT pf.*, p.vpn_ip AS peer_vpn_ip, p.name AS peer_name
              FROM port_forwards pf
              JOIN peers p ON p.id = pf.peer_id
             WHERE pf.id = ?
        """, (rule_id,)).fetchone()
    return dict(row) if row else None


def create_port_forward(peer_id, description, protocol, external_port, internal_port):
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO port_forwards
              (peer_id, description, protocol, external_port, internal_port, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
        """, (peer_id, description or '', protocol, external_port, internal_port, now))
        row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
        return row['id']


def set_port_forward_enabled(rule_id, enabled):
    with get_db() as conn:
        conn.execute(
            "UPDATE port_forwards SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, rule_id)
        )


def delete_port_forward(rule_id):
    with get_db() as conn:
        conn.execute("DELETE FROM port_forwards WHERE id = ?", (rule_id,))


# ── Notification settings ─────────────────────────────────────────────────────

def get_notification_settings():
    """Return dict {channel: {'enabled': bool, 'config': dict}}."""
    import json as _json
    with get_db() as conn:
        rows = conn.execute(
            "SELECT channel, enabled, config FROM notification_settings"
        ).fetchall()
    out = {}
    for r in rows:
        try:
            cfg = _json.loads(r['config'] or '{}')
        except Exception:
            cfg = {}
        out[r['channel']] = {'enabled': bool(r['enabled']), 'config': cfg}
    return out


def get_notification_channel(channel):
    """Return single-channel dict {'enabled': bool, 'config': dict} or None."""
    import json as _json
    with get_db() as conn:
        row = conn.execute(
            "SELECT channel, enabled, config FROM notification_settings WHERE channel = ?",
            (channel,)
        ).fetchone()
    if not row:
        return None
    try:
        cfg = _json.loads(row['config'] or '{}')
    except Exception:
        cfg = {}
    return {'enabled': bool(row['enabled']), 'config': cfg}


def update_notification_channel(channel, enabled, config_dict):
    import json as _json
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO notification_settings (channel, enabled, config, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(channel) DO UPDATE SET
                enabled    = excluded.enabled,
                config     = excluded.config,
                updated_at = excluded.updated_at
        """, (channel, 1 if enabled else 0, _json.dumps(config_dict or {}), now))


# ── Notification log ──────────────────────────────────────────────────────────

def log_notification(channel, event_type, message, success, error=''):
    from datetime import datetime
    with get_db() as conn:
        conn.execute("""
            INSERT INTO notification_log (channel, event_type, message, success, error, sent_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (channel, event_type, message, 1 if success else 0, error or '',
              datetime.utcnow().isoformat()))
        # Trim to last 500 rows
        count = conn.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0]
        if count > 500:
            conn.execute("""
                DELETE FROM notification_log WHERE id IN (
                    SELECT id FROM notification_log ORDER BY id ASC LIMIT ?
                )
            """, (count - 500,))


def get_notification_log(limit=20):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, channel, event_type, message, success, error, sent_at
              FROM notification_log
             ORDER BY id DESC
             LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def clear_notification_log():
    with get_db() as conn:
        conn.execute("DELETE FROM notification_log")


# ── Per-event toggles ────────────────────────────────────────────────────────

def get_notification_event_toggles():
    """Return dict {event_type: bool}."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT event_type, enabled FROM notification_event_toggles"
        ).fetchall()
    return {r['event_type']: bool(r['enabled']) for r in rows}


def set_notification_event_toggles(toggles):
    """Bulk update — toggles is dict {event_type: bool}."""
    with get_db() as conn:
        for evt, on in toggles.items():
            conn.execute("""
                INSERT INTO notification_event_toggles (event_type, enabled)
                VALUES (?, ?)
                ON CONFLICT(event_type) DO UPDATE SET enabled = excluded.enabled
            """, (evt, 1 if on else 0))


def is_notification_event_enabled(event_type):
    with get_db() as conn:
        row = conn.execute(
            "SELECT enabled FROM notification_event_toggles WHERE event_type = ?",
            (event_type,)
        ).fetchone()
    if not row:
        return True  # default-on for unknown events
    return bool(row['enabled'])


# ── Audit log ────────────────────────────────────────────────────────────────

def audit(action, target_type=None, target_id=None, target_name=None,
          actor_ip=None, details=None):
    """Record an admin action. Best-effort: any failure is swallowed so a
    DB hiccup never blocks the underlying operation."""
    from datetime import datetime
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO audit_log
                  (ts, action, target_type, target_id, target_name, actor_ip, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.utcnow().isoformat(),
                action, target_type, target_id, target_name, actor_ip, details,
            ))
    except Exception:
        pass


def get_audit_log(limit=200, offset=0, action_prefix=None):
    """Newest first. action_prefix filters by leading substring (e.g. 'peer.')."""
    with get_db() as conn:
        if action_prefix:
            rows = conn.execute("""
                SELECT id, ts, action, target_type, target_id, target_name,
                       actor_ip, details
                  FROM audit_log
                 WHERE action LIKE ?
              ORDER BY id DESC
                 LIMIT ? OFFSET ?
            """, (action_prefix + '%', limit, offset)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, ts, action, target_type, target_id, target_name,
                       actor_ip, details
                  FROM audit_log
              ORDER BY id DESC
                 LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def count_audit_log(action_prefix=None):
    with get_db() as conn:
        if action_prefix:
            return conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action LIKE ?",
                (action_prefix + '%',),
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]


# ── TOTP (DB-backed 2FA) ─────────────────────────────────────────────────────

def get_totp_config():
    """Return {'secret': str, 'backup_codes': [hashes], 'enrolled_at': str|None}.

    `secret` is empty string when not enrolled. Backup codes are stored as
    sha256 hex digests (one per code) so a DB leak doesn't reveal usable
    codes. The id=1 row always exists (seeded in migrate_db).
    """
    import json as _json
    with get_db() as conn:
        row = conn.execute(
            "SELECT secret, backup_codes, enrolled_at FROM totp_settings WHERE id = 1"
        ).fetchone()
    if not row:
        return {'secret': '', 'backup_codes': [], 'enrolled_at': None}
    try:
        codes = _json.loads(row['backup_codes'] or '[]')
        if not isinstance(codes, list):
            codes = []
    except Exception:
        codes = []
    return {
        'secret':       row['secret'] or '',
        'backup_codes': [c for c in codes if isinstance(c, str)],
        'enrolled_at':  row['enrolled_at'],
    }


def set_totp_config(secret, backup_code_hashes):
    """Enroll: persist secret + the hashed backup-code list. Bumps enrolled_at."""
    import json as _json
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    payload = _json.dumps(list(backup_code_hashes or []))
    with get_db() as conn:
        conn.execute("""
            UPDATE totp_settings
               SET secret = ?, backup_codes = ?, enrolled_at = ?, updated_at = ?
             WHERE id = 1
        """, (secret or '', payload, now, now))


def clear_totp_config():
    """Disable DB-backed 2FA. Keeps the row (id=1) but zeros all fields."""
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute("""
            UPDATE totp_settings
               SET secret = '', backup_codes = '[]', enrolled_at = NULL, updated_at = ?
             WHERE id = 1
        """, (now,))


def consume_backup_code(code_hash):
    """If `code_hash` is present in the stored list, remove it and return True.
    Single-use semantics: a successful consume always shrinks the list."""
    import json as _json
    from datetime import datetime
    with get_db() as conn:
        row = conn.execute(
            "SELECT backup_codes FROM totp_settings WHERE id = 1"
        ).fetchone()
        if not row:
            return False
        try:
            codes = _json.loads(row['backup_codes'] or '[]')
        except Exception:
            codes = []
        if code_hash not in codes:
            return False
        codes = [c for c in codes if c != code_hash]
        conn.execute("""
            UPDATE totp_settings
               SET backup_codes = ?, updated_at = ?
             WHERE id = 1
        """, (_json.dumps(codes), datetime.utcnow().isoformat()))
        return True


# ── Per-peer schedules ───────────────────────────────────────────────────────

def get_peer_schedule(peer_id):
    """Return the schedule row for `peer_id`, or None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT peer_id, days_of_week, enabled_from, enabled_to, "
            "       timezone, enabled, created_at, updated_at "
            "  FROM peer_schedules WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()
    return dict(row) if row else None


def set_peer_schedule(peer_id, days_of_week, enabled_from, enabled_to,
                      timezone='UTC', enabled=True):
    """Upsert a peer's schedule. `days_of_week` is either a list/tuple of
    ints (0=Mon..6=Sun) or a pre-formatted comma-separated string."""
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    if isinstance(days_of_week, (list, tuple, set)):
        dow = ','.join(str(int(d)) for d in sorted({int(d) for d in days_of_week}))
    else:
        dow = str(days_of_week)
    with get_db() as conn:
        conn.execute("""
            INSERT INTO peer_schedules
              (peer_id, days_of_week, enabled_from, enabled_to,
               timezone, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(peer_id) DO UPDATE SET
                days_of_week = excluded.days_of_week,
                enabled_from = excluded.enabled_from,
                enabled_to   = excluded.enabled_to,
                timezone     = excluded.timezone,
                enabled      = excluded.enabled,
                updated_at   = excluded.updated_at
        """, (peer_id, dow, enabled_from, enabled_to,
              timezone or 'UTC', 1 if enabled else 0, now, now))


def delete_peer_schedule(peer_id):
    with get_db() as conn:
        conn.execute("DELETE FROM peer_schedules WHERE peer_id = ?", (peer_id,))


def get_all_peer_schedules():
    """Return all schedules with peer name + public_key joined for the poller."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT ps.peer_id, ps.days_of_week, ps.enabled_from, ps.enabled_to,
                   ps.timezone, ps.enabled,
                   p.name AS peer_name, p.public_key, p.preshared_key,
                   p.vpn_ip, p.tunnel_mode, p.custom_routes, p.enabled AS peer_enabled
              FROM peer_schedules ps
              JOIN peers p ON p.id = ps.peer_id
        """).fetchall()
    return [dict(r) for r in rows]


def replace_backup_codes(backup_code_hashes):
    """Replace the stored backup-code set without touching the secret."""
    import json as _json
    from datetime import datetime
    payload = _json.dumps(list(backup_code_hashes or []))
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute("""
            UPDATE totp_settings
               SET backup_codes = ?, updated_at = ?
             WHERE id = 1
        """, (payload, now))
