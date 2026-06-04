<p align="center">
  <img width="200" height="200" alt="Traverse" src="static/img/app.png" />
</p>

### Traverse

A self-hosted WireGuard VPN dashboard built with Flask. Manage peers, monitor live traffic, view connection maps, and control your WireGuard interface — all from a clean dark-themed web UI.

<img width="1633" height="1145" alt="image" src="https://github.com/user-attachments/assets/abe368e0-691f-458d-b5eb-eff73840fdff" />

<img width="1628" height="1110" alt="image" src="https://github.com/user-attachments/assets/4be98729-2577-4475-895c-56c4db91f40d" />

---

## Features

### Peer management
- **CRUD + wizard** — create peers via a guided wizard or a direct form; auto-allocated VPN IPs (default cap 20 peers, easy to raise)
- **Three tunnel modes** per peer — Full Tunnel (`0.0.0.0/0`), VPN Only (subnet only), or Split Tunnel (subnet + custom CIDRs)
- **Per-peer DNS override** — pick from Pi-hole / Cloudflare / Google / Quad9 / custom; client config reflects the choice
- **Config download & inline QR** — `.conf` files plus an embedded QR you can scan on the spot
- **Peer expiry** — set `expires_at`; expired peers are auto-disabled
- **Per-peer notes, device type, and location history**
- **Kill button** — force-disconnect a peer from `wg1` and disable in one click
- **Sortable / filterable peer table** — 10 sortable columns, filter chips for tunnel × status × device, combinable text search
- **Bulk actions** — multi-select with master checkbox, then bulk Disable/Enable/Delete

### Live monitoring
- **1-second polling** with a 15-minute scrolling Chart.js RX/TX waveform; RAF-eased speed numbers (60 fps)
- **NOC dashboard** — peer counts, total RX/TX, WireGuard status, server health (CPU/RAM/disk/uptime), Pi-hole status (blocked/rate/blocklist/queries/clients), VPS speedtest
- **Real-time event feed** — connect/disconnect/kill stream on the dashboard, polls every 30 s
- **Per-peer 24-hour bandwidth chart and 30-day daily aggregates**
- **Sparkline canvases** per peer row showing recent throughput
- **Connection map** — Leaflet world map with peer markers color-coded by tunnel mode (GeoIP via ipapi.co, 1-hour cache)
- **Topology view** — radial canvas diagram with animated dashed lines on active peers and a slow-rotating server ring

### Pi-hole integration (v6 API)
- DNS-level ad blocking for every VPN client (Pi-hole listens on `10.9.0.1:53` over WireGuard)
- Live blocked-count, rate, blocklist size, query count, and client count on the dashboard
- Top-blocked-domains widget (5-min refresh)
- Per-peer DNS query log on the peer detail page

### Notifications & alerts
- **Multi-channel notifications** — Email (SMTP), Telegram, and Discord at `/notifications`. Per-event toggles for 15 event types. 500-row send log with success/error breakdown.
- **Alert feed** at `/alerts` — severity-coded; mark-seen / dismiss
- **Connection history log** at `/history`
- **Browser push + sound alerts** — opt-in; Web Audio tones on connect/disconnect

### Quality-of-life
- **Toast notifications** for fetch-driven actions
- **Styled confirm modal** replacing every native `confirm()` dialog
- **Top loading bar** during navigation / fetch activity
- **Command palette** — `Cmd/Ctrl+K` for fuzzy navigation across the app
- **Keyboard shortcuts** — `?` help, `/` search, `n` new peer, sequence shortcuts (`g d`, `g p`, …)
- **CSV export** on every list page (peers, history, alerts, notifications)
- **Skeleton states** while live values load
- **Friendly empty states** with iconography and CTAs

### UI polish (v1.10.0)
- **Cross-fade page transitions** — internal navigation fades the current view out before the next page commits; uses the native View Transitions API on Chromium, falls back to a JS-driven fade elsewhere
- **Glass topbar + bottom-nav** — translucent surface with `backdrop-filter: blur(14px)`; content scrolls visibly underneath
- **Ambient body gradient** — fixed purple radial mesh so cards float against texture instead of pure black
- **Cursor-aware card sheen** — stat cards have a subtle radial highlight that follows the pointer
- **Number count-up** — stat values tick from 0 to the final number on first render (skipped under reduced motion)
- **Coherent motion tokens** — every transition reads from the same `--ease-out` / `--dur-*` scale so timing feels consistent across the app
- **`prefers-reduced-motion: reduce`** — fully respected; collapses every animation and the cross-fade interceptor bails out at the JS layer too

### Operations
- **Port forwarding** — DNAT iptables rules at `/port-forwards/`; persisted to `/etc/iptables/rules.v4`
- **System log viewer** at `/logs` (live tail with filter for traverse + WireGuard)
- **Backup/restore** — JSON export of all peers/events (private keys stripped)
- **Speedtest** widget — kicks off `speedtest-cli`, persists last 5 results
- **WireGuard service control** — start/stop/restart from the UI

### Auth & security
- **Session-based login** with timing-safe `hmac.compare_digest`
- **TOTP 2FA** (Google Authenticator / Authy) — optional, set `TOTP_SECRET`
- **All secrets live in `.env`** — gitignored from commit 1; private keys never appear on the peer list

### Performance
- **Flask-Caching** — 15-second cache on `/api/server/health`, 55-second on Pi-hole stats and top-blocked
- **Database indexes** on hot columns (peers.enabled, connection_events.timestamp, etc.)
- **Nginx gzip + 7-day immutable `/static/` caching** — `style.css` 72.5 KB → 13.2 KB on the wire (82% smaller)

### PWA
- **Installable** on iOS Safari, Android Chrome, and desktop Chromium browsers
- **Offline page** with auto-reload when network returns
- Service worker network-first with cache fallback; API calls always fresh

### Frontend principles
- **No CDN dependencies** — Chart.js, Leaflet.js, all icons and assets are bundled locally
- **No JS framework** — vanilla JS only, custom dark CSS

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.12 |
| Framework | Flask 3.1 (Blueprints) |
| Database | SQLite (WAL mode) via stdlib `sqlite3` — no ORM |
| Caching | Flask-Caching (SimpleCache) |
| WireGuard | `wg` / `wg-quick` CLI (wireguard-tools) |
| QR codes | `qrcode[pil]` + Pillow |
| 2FA | `pyotp` (TOTP / RFC 6238) |
| Notifications | Email (SMTP), Telegram, Discord — stdlib only (`smtplib`, `urllib.request`) |
| Pi-hole | v6 API integration (session token + JSON) |
| Frontend | Custom dark CSS — no JS framework, no external CDN |
| Charts | Chart.js v4 (bundled locally) |
| Map | Leaflet.js v1.9.4 (bundled locally) |
| PWA | Web App Manifest + Service Worker — installable on iOS/Android/desktop |

---

## Requirements

- **OS**: Linux VPS — tested on Ubuntu 22.04 / Debian 12
- **Python**: 3.12+
- **WireGuard**: `apt install wireguard`
- **Privileges**: must run as `root` (needs access to `wg`, `wg-quick`, and `systemctl`)
- **Ports**: UDP `51821` open for WireGuard; TCP `443`/`80` open for the web UI (if using nginx + HTTPS)

---

## Installation

### 1. Install system dependencies

```bash
apt update && apt install -y wireguard python3.12 python3.12-venv python3-pip nginx
```

### 2. Clone the repository

```bash
git clone https://github.com/syedhashmi-bit/traverse.git /var/www/traverse
cd /var/www/traverse
```

### 3. Create a Python virtualenv and install packages

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Generate WireGuard server keys

```bash
wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key
chmod 600 /etc/wireguard/server_private.key
chmod 644 /etc/wireguard/server_public.key
```

### 5. Create the WireGuard server config

```bash
nano /etc/wireguard/wg1.conf
```

```ini
[Interface]
Address    = 10.9.0.1/24
ListenPort = 51821
PrivateKey = <contents of /etc/wireguard/server_private.key>
PostUp     = iptables -t nat -A POSTROUTING -s 10.9.0.0/24 -o eth0 -j MASQUERADE
PostDown   = iptables -t nat -D POSTROUTING -s 10.9.0.0/24 -o eth0 -j MASQUERADE
```

> Replace `eth0` with your actual outbound network interface (`ip route | grep default` shows it).

```bash
chmod 600 /etc/wireguard/wg1.conf
```

### 6. Enable IP forwarding

```bash
echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf
sysctl -p
```

### 7. Start WireGuard

```bash
systemctl enable --now wg-quick@wg1
wg show wg1   # confirm it's running
```

### 8. Configure the application

```bash
cp .env.example .env
nano .env
```

Fill in every required variable — see the [Environment Variables](#environment-variables) table below. At minimum set:

- `SECRET_KEY` — generate one with `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `ADMIN_PASSWORD` — change from the default
- `WG_ENDPOINT` — your VPS public IP or domain name

---

## Environment Variables

All configuration lives in `.env`. Never commit this file.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SECRET_KEY` | **yes** | — | Random 32+ char string used to sign Flask sessions. Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_USERNAME` | **yes** | `admin` | Dashboard login username |
| `ADMIN_PASSWORD` | **yes** | — | Dashboard login password — **change this** |
| `WG_ENDPOINT` | **yes** | — | Your VPS public IP or domain (put into every peer's client config) |
| `WG_INTERFACE` | no | `wg1` | WireGuard interface name |
| `WG_PORT` | no | `51821` | UDP port WireGuard listens on |
| `WG_SUBNET` | no | `10.9.0.0/24` | VPN subnet — IPs are auto-allocated from this range |
| `WG_SERVER_VPN_IP` | no | `10.9.0.1` | Server's own VPN IP — skipped during peer IP allocation |
| `WG_DNS` | no | `1.1.1.1` | DNS server pushed to clients in their config |
| `DATABASE_PATH` | no | `database.db` | Path to the SQLite file (relative to project root) |
| `TOTP_SECRET` | no | — | Enables TOTP 2FA. Generate: `python3 -c "import pyotp; print(pyotp.random_base32())"` then scan the QR at `/totp-setup` |
| `TELEGRAM_BOT_TOKEN` | no | — | Legacy Telegram bot token (used by the early-boot WG-down alerter); both must be set to enable |
| `TELEGRAM_CHAT_ID` | no | — | Legacy Telegram chat ID |
| `ALERT_INACTIVE_HOURS` | no | `0` (disabled) | Hours of inactivity before a peer triggers an alert |
| `PIHOLE_ENABLED` | no | — | Set to `true` / `1` / `yes` to enable the Pi-hole integration |
| `PIHOLE_PASSWORD` | no | — | Pi-hole admin password (used for the v6 API auth dance) |
| `PIHOLE_URL` | no | `http://10.9.0.1:8088` | Pi-hole/FTL **API** base, talked to directly by the backend (no `/api` suffix — it's appended) |
| `PIHOLE_WEB_URL` | no | `/pihole` | User-facing admin **link** in the UI (the reverse-proxied, auth-gated path). Relative `/pihole` resolves to the current host |

> **Notifications module** (Email, Telegram, Discord at `/notifications`) — the canonical config store is the database (edit via the UI). `.env` keys (`NOTIFY_EMAIL_*`, `NOTIFY_TELEGRAM_*`, `NOTIFY_DISCORD_WEBHOOK`) are bootstrap fallbacks only. See `.env.example` for the full list.

---

## Running the App

### Development

```bash
cd /var/www/traverse
source venv/bin/activate
python app.py   # listens on 0.0.0.0:5000
```

Open `http://localhost:5000` — log in with your `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

### Production (systemd + gunicorn + nginx)

**a) Create the log directory**

```bash
mkdir -p /var/log/traverse
```

**b) Create the systemd service**

```bash
nano /etc/systemd/system/traverse.service
```

```ini
[Unit]
Description=Traverse WireGuard VPN Dashboard
After=network.target

[Service]
Type=notify
User=root
Group=root
WorkingDirectory=/var/www/traverse
EnvironmentFile=/var/www/traverse/.env
ExecStart=/var/www/traverse/venv/bin/gunicorn \
    --workers 2 \
    --bind 127.0.0.1:5000 \
    --timeout 60 \
    --access-logfile /var/log/traverse/access.log \
    --error-logfile /var/log/traverse/error.log \
    --capture-output \
    "app:create_app()"
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now traverse
systemctl status traverse
```

**c) Configure nginx**

```bash
nano /etc/nginx/sites-available/traverse
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass            http://127.0.0.1:5000;
        proxy_set_header      Host              $host;
        proxy_set_header      X-Real-IP         $remote_addr;
        proxy_set_header      X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header      X-Forwarded-Proto $scheme;
        proxy_read_timeout    60s;
        proxy_connect_timeout 10s;
        proxy_buffering       off;
    }
}
```

```bash
ln -sf /etc/nginx/sites-available/traverse /etc/nginx/sites-enabled/traverse
nginx -t && systemctl reload nginx
```

**d) Issue a free SSL certificate with Certbot**

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d your-domain.com
```

Certbot will automatically configure HTTPS and set up auto-renewal.

---

## Optional Features

### TOTP Two-Factor Authentication

1. Generate a secret and add it to `.env`:
   ```bash
   python3 -c "import pyotp; print(pyotp.random_base32())"
   # → add TOTP_SECRET=<output> to .env
   ```
2. Restart the app: `systemctl restart traverse`
3. Open `/totp-setup` in your browser — scan the QR code with Google Authenticator or Authy
4. From now on, login requires username + password + 6-digit TOTP code

### Telegram Alerts

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token
2. Get your chat ID (message [@userinfobot](https://t.me/userinfobot))
3. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-your-token
   TELEGRAM_CHAT_ID=your-chat-id
   ALERT_INACTIVE_HOURS=24
   ```
4. Restart: `systemctl restart traverse`

You'll receive Telegram messages when:
- A peer connects or reconnects
- A peer goes inactive for longer than `ALERT_INACTIVE_HOURS`
- The WireGuard interface goes down

---

## Managing the Service

```bash
# Status of all three services
systemctl status traverse nginx wg-quick@wg1

# Restart after config/code changes
systemctl restart traverse

# Reload nginx after config changes (zero-downtime)
nginx -t && systemctl reload nginx

# Restart WireGuard (drops all VPN connections briefly)
systemctl restart wg-quick@wg1

# Live app logs
journalctl -u traverse -f

# Access and error logs
tail -f /var/log/traverse/access.log
tail -f /var/log/traverse/error.log

# WireGuard peer status
wg show wg1

# Check SSL certificate expiry
openssl x509 -in /etc/letsencrypt/live/your-domain.com/fullchain.pem -noout -dates

# Force SSL renewal (auto-renewal is configured by Certbot)
certbot renew --force-renewal
```

---

## Project Structure

```
traverse/
├── app.py                  # Flask app factory, blueprints, error handlers, context processor
├── cache_ext.py            # Shared Flask-Caching SimpleCache instance
├── database.py             # SQLite init + idempotent migrate_db() + all DB helpers
├── wireguard.py            # wg CLI wrappers, key gen, IP allocation, config gen
├── alerts.py               # Background daemon thread (60s tick): WG up/down,
│                           # peer connect/disconnect, bandwidth anomaly, expiry,
│                           # Pi-hole liveness, location tracking
├── notifications.py        # send_notification() + Email/Telegram/Discord senders
├── routes/
│   ├── auth.py             # /login, /login/verify, /totp-setup, login_required
│   ├── dashboard.py        # GET / — NOC dashboard
│   ├── peers.py            # Peer CRUD + wizard + bulk actions + CSV export
│   ├── api.py              # All JSON endpoints (stats, health, pihole, ping, etc.)
│   ├── map.py              # /map + peer geo locations
│   ├── topology.py         # /topology canvas
│   ├── alerts.py           # Alert feed, mark-seen, dismiss, CSV export
│   ├── history.py          # Connection event log + CSV export
│   ├── notifications.py    # Channel config + per-event toggles + send log + CSV
│   ├── port_forwards.py    # DNAT rules CRUD
│   ├── logs.py             # System log tail (traverse + WireGuard)
│   ├── about.py            # Version + changelog page
│   ├── settings.py         # WG service control, backup/restore, Pi-hole controls
│   └── pwa.py              # /manifest.json, /sw.js, /offline (public, no auth)
├── templates/
│   ├── base.html           # Sidebar + topbar + bottom nav + help overlay +
│   │                       # toast container + loading bar
│   ├── dashboard.html      # NOC layout, live chart, peers table, right panel,
│   │                       # event feed
│   ├── peers/{list,detail,create,wizard,qr}.html
│   ├── port_forwards/index.html
│   ├── notifications.html, settings.html, map.html, topology.html,
│   ├── alerts.html, history.html, logs.html, about.html, offline.html
│   ├── login.html, totp_{setup,verify}.html
│   └── 404.html, 500.html
├── static/
│   ├── css/{style.css, leaflet.min.css}
│   ├── js/{app.js, chart.min.js, leaflet.min.js}
│   ├── icons/              # 8 PWA icon sizes + apple-touch-icon + splash
│   ├── img/                # App logo, Leaflet markers
│   ├── manifest.json       # PWA Web App Manifest
│   ├── sw.js               # Service worker (network-first, /api/* always fresh)
│   └── favicon.ico
├── .env.example            # Environment variable template (safe to commit)
├── requirements.txt        # Python dependencies
├── VERSION                 # Plain text — current version
├── CHANGELOG.md            # Per-version release notes
└── database.db             # SQLite file — created automatically on first run
```

---

## Security Notes

- **Private keys** are shown only on the peer detail page and in downloaded `.conf` files — never in the peer list or any API response
- **No shell injection** — all `subprocess` calls use list form (`shell=False`)
- **Peer name validation** — enforced against `^[a-zA-Z0-9_\-]{1,64}$`
- **Preshared keys** are written to a temp file during `wg set` to keep them off the process argument list
- **Credential comparison** uses `hmac.compare_digest` (timing-safe; prevents timing oracle attacks)
- **Session expiry** — `session.permanent = False` so sessions die when the browser closes
- **Secrets in `.env` only** — no credentials in code or the database
- **20-peer cap** (constant `MAX_PEERS = 20` in `routes/peers.py`) — enforced server-side in the create route. Tune to fit your subnet.
- **CSRF** — single-user / session-only deployment. CSRF tokens not currently in place; consider `flask-wtf` if multi-user.

---

## License

MIT
