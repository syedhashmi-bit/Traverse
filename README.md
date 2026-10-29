<p align="center">
  <img width="200" height="200" alt="Traverse" src="static/img/app.png" />
</p>

<h1 align="center">Traverse</h1>

<p align="center">
  A self-hosted WireGuard VPN dashboard built with Flask — manage peers, monitor live traffic, view connection maps, block ads with Pi-hole, and control your interface from a clean dark-themed web UI.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white&labelColor=23272e" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white&labelColor=23272e" alt="Flask 3.1">
  <img src="https://img.shields.io/badge/WireGuard-VPN-2ea44f?logo=wireguard&logoColor=white&labelColor=23272e" alt="WireGuard VPN">
  <img src="https://img.shields.io/badge/Pi--hole-v6-96060C?logo=pihole&logoColor=white&labelColor=23272e" alt="Pi-hole v6">
  <img src="https://img.shields.io/badge/SQLite-WAL-003B57?logo=sqlite&logoColor=white&labelColor=23272e" alt="SQLite (WAL)">
  <img src="https://img.shields.io/badge/PWA-installable-5A0FC8?logo=pwa&logoColor=white&labelColor=23272e" alt="PWA">
  <img src="https://img.shields.io/badge/License-MIT-3DA639?labelColor=23272e" alt="License: MIT">
</p>

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
- **Connection map** — Leaflet world map with peer markers color-coded by tunnel mode (GeoIP via ipapi.co → ipwho.is → GeoJS fallback, cached 24 h per peer; keyless Esri tiles)
- **Topology view** — radial canvas diagram with animated dashed lines on active peers and a slow-rotating server ring

### Pi-hole integration (v6 API)
- DNS-level ad blocking for every VPN client (Pi-hole listens on `10.9.0.1:53` over WireGuard)
- Live blocked-count, rate, blocklist size, query count, and client count on the dashboard
- Top-blocked-domains widget (5-min refresh)
- Per-peer DNS query log on the peer detail page

### Notifications & alerts
- **Multi-channel notifications** — Email (SMTP), Telegram, and Discord at `/notifications`. Per-event toggles for 16 event types (including Google sign-ins and refused accounts). 500-row send log with success/error breakdown.
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
- **Speedtest** widget — kicks off `speedtest-cli`, keeps the last 50 results
- **Audit log** at `/audit` (+ CSV) — sign-ins with the Google account used, refused accounts, and admin actions
- **Public stats endpoint** — optional token-gated `GET /api/public-stats` (counts only) for an external status page
- **WireGuard service control** — start/stop/restart from the UI

### Auth & security
- **Sign in with Google — the only way in.** No password to leak or brute-force; two-factor is whatever the Google account enforces (turn on 2-Step Verification). Only emails in `TRAVERSE_ADMIN_EMAILS` get in, re-checked on every request
- **Hardened OIDC flow** — PKCE (S256), `state` pinned to the browser, `nonce` bound into the ID token, RS256 signature checked against Google's published keys, plus audience / issuer / expiry / verified-email checks
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
| Sign-in | Google OIDC — `PyJWT[crypto]` for ID-token verification, stdlib `urllib` for the rest |
| Notifications | Email (SMTP), Telegram, Discord — stdlib only (`smtplib`, `urllib.request`) |
| Pi-hole | v6 API integration (session token + JSON) |
| Frontend | Custom dark CSS — no JS framework, no external CDN |
| Charts | Chart.js v4 (bundled locally) |
| Map | Leaflet.js v1.9.4 (bundled locally) |
| PWA | Web App Manifest + Service Worker — installable on iOS/Android/desktop |

---

## Requirements

- **OS**: Linux VPS — Ubuntu 22.04 / 24.04, Debian 12
- **Python**: 3.10+ (the distro's `python3` is fine; CI tests 3.10 → 3.14)
- **WireGuard**: `apt install wireguard`
- **Privileges**: must run as `root` (needs access to `wg`, `wg-quick`, and `systemctl`)
- **A domain with HTTPS** for the dashboard — Google sign-in won't redirect to a bare IP or plain `http://` (certbot via the installer is fine)
- **A Google account** to create a free OAuth client in Google Cloud Console — see [Google sign-in](#google-sign-in)
- **Ports**: the WireGuard UDP port (installer default `51820`) and TCP `80`/`443` — open them in your **cloud provider's firewall** too (Hetzner, AWS security groups, Oracle, GCP…), not just on the server

---

## Installation

### Before you start

Three things the installer can't do for you — do them first and the rest is one command:

1. **Point a domain at the server.** Add a DNS **A record** (e.g. `vpn.example.com`) with your VPS's public IP and wait until `ping vpn.example.com` shows that IP. Google sign-in needs HTTPS, and HTTPS needs a domain.
2. **Open the ports in your cloud provider's firewall** — the WireGuard UDP port (default `51820`) plus TCP `80` and `443`. Missing UDP here is the #1 cause of "the VPN app says connected but nothing loads".
3. **Create a Google OAuth client** — about 5 minutes, see [Google sign-in](#google-sign-in). You can also skip it during install and add it to `.env` afterwards.

### Quick install (recommended)

On a fresh **Debian/Ubuntu** VPS, one command takes you from clone to a running dashboard:

```bash
git clone https://github.com/syedhashmi-bit/Traverse.git /var/www/traverse
cd /var/www/traverse
sudo ./install.sh
```

It installs the dependencies, creates the Python venv, generates the WireGuard server keys + `/etc/wireguard/<interface>.conf` (default `wg0`) (auto-detecting your egress interface), writes `.env` with a freshly generated `SECRET_KEY` (prompting for a domain and your Google sign-in details — see [Google sign-in](#google-sign-in)), installs the systemd service + nginx config, adds a swapfile on small boxes, and — if you give it a domain — obtains a free HTTPS certificate via certbot. It's **safe to re-run** (existing keys / `.env` / WireGuard config are kept).

Unattended example:

```bash
sudo DOMAIN=vpn.example.com RUN_CERTBOT=yes \
     TRAVERSE_GOOGLE_CLIENT_ID='….apps.googleusercontent.com' \
     TRAVERSE_GOOGLE_CLIENT_SECRET='…' \
     TRAVERSE_ADMIN_EMAILS='you@example.com' ./install.sh
```

Then open the printed URL, sign in with Google — a short guided tour shows you around — and add your first peer: scan its QR code with the WireGuard app ([iOS](https://apps.apple.com/app/wireguard/id1441195209), [Android](https://play.google.com/store/apps/details?id=com.wireguard.android), [desktop](https://www.wireguard.com/install/)). For ad-blocking, see [Pi-hole](#pi-hole-optional).

---

### Manual installation

<sub>Use this on other distros, or to understand each step. The quick installer above does all of this for you. These steps use the app's built-in defaults (`wg1`, port `51821`, `10.9.0.0/24`); the installer instead writes `wg0`, `51820` and `10.8.0.0/24` into `.env` — either works as long as `.env` and the WireGuard config agree.</sub>

### 1. Install system dependencies

```bash
apt update && apt install -y wireguard python3 python3-venv python3-pip nginx
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
- `WG_ENDPOINT` — your VPS public IP or domain name
- the four `TRAVERSE_*` sign-in variables — see [Google sign-in](#google-sign-in)

---

## Google sign-in

Sign-in is Google-only, and it needs a domain served over HTTPS (Google
won't redirect to a bare IP or plain `http://`). It's free and takes about
five minutes:

1. **Create a project.** Open [Google Cloud Console](https://console.cloud.google.com/),
   pick the project dropdown at the top → **New project** → any name (e.g.
   `traverse`) → **Create**, and make sure it's selected.
2. **Set up the consent screen.** Go to
   [Google Auth Platform](https://console.cloud.google.com/auth/overview) →
   **Get started**. App name `Traverse`, your email as support/contact
   email, **Audience: External** → **Create**.
   Then **Audience → Test users → Add users** and add every Google account
   that will sign in. (Leaving the app in *Testing* is fine for personal use —
   only listed test users can get past Google's screen anyway.)
3. **Create the client.** Go to
   [Credentials](https://console.cloud.google.com/apis/credentials) →
   **Create credentials → OAuth client ID** → Application type
   **Web application**. Under **Authorized redirect URIs** add exactly:

   ```
   https://<your-traverse-host>/login/google/callback
   ```

   **Create**, then copy the **Client ID** and **Client secret**.

   (Already have a Google Cloud project for another app? Add a new client
   there — the consent screen is shared, the client and its secret aren't.)

4. Give them to `install.sh` when it asks, or set them in `.env` and restart Traverse:

   ```bash
   TRAVERSE_PUBLIC_URL=https://<your-traverse-host>
   TRAVERSE_GOOGLE_CLIENT_ID=<client id>.apps.googleusercontent.com
   TRAVERSE_GOOGLE_CLIENT_SECRET=<client secret>
   TRAVERSE_ADMIN_EMAILS=you@example.com            # comma-separated
   ```

Only those emails can sign in; any other Google account is turned away. The
list is checked on every request, so removing an email and restarting
Traverse signs it out everywhere, even with a session still open. There are
no read-only accounts: every page can reveal peer private keys, so anyone on
the list has full access. **Settings → Sign-in (Google)** shows the current
list and the exact redirect URI the server expects. Until all four variables
are set, the login button is disabled and the startup log says what's missing.

---

## Environment Variables

All configuration lives in `.env`. Never commit this file. The **Default**
column is what the app falls back to when a variable is missing;
`install.sh` writes its own values (`wg0`, `51820`, `10.8.0.0/24`) into `.env`.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SECRET_KEY` | **yes** | — | Random 32+ char string used to sign Flask sessions. Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `TRAVERSE_PUBLIC_URL` | **yes** | — | The `https://` origin browsers use (e.g. `https://vpn.example.com`, no trailing path). Builds the Google redirect URI |
| `TRAVERSE_GOOGLE_CLIENT_ID` | **yes** | — | Google OAuth "Web application" client ID |
| `TRAVERSE_GOOGLE_CLIENT_SECRET` | **yes** | — | Its client secret |
| `TRAVERSE_ADMIN_EMAILS` | **yes** | — | Comma-separated Google account emails allowed to sign in |
| `WG_ENDPOINT` | **yes** | — | Your VPS public IP or domain (put into every peer's client config) |
| `WG_INTERFACE` | no | `wg1` | WireGuard interface name |
| `WG_PORT` | no | `51821` | UDP port WireGuard listens on |
| `WG_SUBNET` | no | `10.9.0.0/24` | VPN subnet — IPs are auto-allocated from this range |
| `WG_SERVER_VPN_IP` | no | `10.9.0.1` | Server's own VPN IP — skipped during peer IP allocation |
| `WG_DNS` | no | `1.1.1.1` | DNS server pushed to clients in their config |
| `DATABASE_PATH` | no | `database.db` | Path to the SQLite file (relative to project root) |
| `TELEGRAM_BOT_TOKEN` | no | — | Legacy Telegram bot token (used by the early-boot WG-down alerter); both must be set to enable |
| `TELEGRAM_CHAT_ID` | no | — | Legacy Telegram chat ID |
| `ALERT_INACTIVE_HOURS` | no | `0` (disabled) | Hours of inactivity before a peer triggers an alert |
| `PIHOLE_ENABLED` | no | — | `true` / `1` / `yes` / `on` enables the Pi-hole integration; anything else (including `false`) leaves it off |
| `PIHOLE_PASSWORD` | no | — | Pi-hole admin password (used for the v6 API auth dance) |
| `PIHOLE_URL` | no | `http://10.9.0.1:8088` | Pi-hole/FTL **API** base, talked to directly by the backend (no `/api` suffix — it's appended) |
| `MAX_PEERS` | no | `20` | Peer cap, clamped to 1–50 |
| `TRAVERSE_PUBLIC_STATS_TOKEN` | no | — | Bearer token for `GET /api/public-stats` (counts only). Unset → the endpoint 404s |
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

Open `http://localhost:5000` and sign in with Google (for local dev, set `TRAVERSE_PUBLIC_URL=http://localhost:5000` and register `http://localhost:5000/login/google/callback` on the Google client — Google allows plain HTTP for `localhost` only).

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

### Pi-hole (optional)

Traverse can show Pi-hole stats and hand VPN devices Pi-hole as their DNS
for network-wide ad-blocking. Pi-hole v6 is installed separately, on the
same server:

```bash
curl -sSL https://install.pi-hole.net | bash        # pick your WireGuard interface (e.g. wg0) when asked
pihole-FTL --config webserver.port 8088             # nginx already owns port 80
pihole setpassword 'choose-a-password'
ufw allow in on wg0 to any port 53                  # only if ufw is active; use your interface name
```

Then add to `.env` and `systemctl restart traverse`:

```bash
PIHOLE_ENABLED=true
PIHOLE_URL=http://127.0.0.1:8088                    # FTL API base, no /api
PIHOLE_PASSWORD=choose-a-password
PIHOLE_WEB_URL=http://<WG_SERVER_VPN_IP>:8088/admin/ # admin UI, reachable over the VPN
```

New peers then default to the server's VPN IP (where Pi-hole answers) as
DNS. Existing peers keep their DNS until you change it on the peer's page
and re-download / re-scan the config.

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

## Updating

```bash
cd /var/www/traverse
git pull
venv/bin/pip install -r requirements.txt
systemctl restart traverse
```

Database changes migrate themselves on start. Check `CHANGELOG.md` for
anything marked **breaking** first, then `journalctl -u traverse -n 30` after
the restart. Re-running `sudo ./install.sh` is also safe — it keeps your keys,
`.env` and WireGuard config.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Sign-in button greyed out | One of the four `TRAVERSE_*` sign-in vars is empty — `journalctl -u traverse -n 30` names it; **Settings → Sign-in (Google)** shows what the server expects |
| Google says `redirect_uri_mismatch` | The redirect URI on the Google client doesn't exactly match `TRAVERSE_PUBLIC_URL` + `/login/google/callback` (scheme, host, no trailing slash) |
| Google says "access blocked" / app not verified | Your account isn't a **test user** on the consent screen |
| "That Google account isn't allowed" | The email isn't in `TRAVERSE_ADMIN_EMAILS` (restart after editing `.env`) |
| Device says connected, but no handshake / nothing loads | WireGuard UDP port closed in the **cloud provider's firewall**, or `WG_ENDPOINT` isn't the server's public IP/domain |
| Handshake OK, but websites don't load | DNS: the peer's DNS server isn't reachable — use a public resolver (1.1.1.1) or check Pi-hole; also confirm `sysctl net.ipv4.ip_forward` is `1` |
| `certbot` failed | The domain's A record doesn't point at this server yet, or TCP 80 is closed — fix, then `certbot --nginx -d <domain>` |

## Uninstalling

```bash
systemctl disable --now traverse "wg-quick@wg0"      # your WG_INTERFACE
rm -f /etc/systemd/system/traverse.service /etc/nginx/sites-enabled/traverse /etc/nginx/sites-available/traverse
systemctl daemon-reload && systemctl reload nginx
rm -f /etc/wireguard/wg0.conf /etc/sysctl.d/99-traverse.conf   # removes the VPN server config + forwarding
rm -rf /var/www/traverse /var/log/traverse                     # app, database and logs
```

`server_private.key` / `server_public.key` in `/etc/wireguard/` are left
alone — delete them too if you won't reinstall (existing device configs stop
working either way).

---

## Managing the Service

```bash
# (examples use wg1 — substitute your WG_INTERFACE; the installer default is wg0)
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
├── schedules.py            # Per-peer allowed-time window helpers
├── google_auth.py          # Google OIDC: PKCE flow, ID-token checks, email allowlist
├── routes/
│   ├── auth.py             # /login, Google sign-in + callback, logout, login_required
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
│   ├── settings.py         # WG service control, backup/restore, Pi-hole, sign-in card
│   ├── audit.py            # /audit log + /audit.csv
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
│   ├── login.html, login_complete.html
│   └── 404.html, 500.html, error_public.html
├── static/
│   ├── css/{style.css, leaflet.min.css}
│   ├── js/{app.js, chart.min.js, leaflet.min.js}
│   ├── icons/              # 8 PWA icon sizes + apple-touch-icon + splash
│   ├── img/                # App logo, Leaflet markers
│   ├── manifest.json       # PWA Web App Manifest
│   ├── sw.js               # Service worker (network-first, /api/* always fresh)
│   └── favicon.ico
├── install.sh              # One-command Debian/Ubuntu installer
├── deploy/                 # systemd unit + nginx templates used by install.sh
├── tests/                  # pytest suite (wg stubbed, Google faked)
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
- **Google-only sign-in** — no password exists to phish, leak or brute-force. The OIDC code flow uses PKCE (S256), a `state` bound to the browser by a signed, path-scoped cookie (stops login CSRF), and a `nonce` bound into the ID token; the ID token's RS256 signature is checked against Google's published keys along with audience, issuer, expiry and `email_verified`
- **Email allowlist re-checked on every request** — removing an address from `TRAVERSE_ADMIN_EMAILS` and restarting ends its sessions immediately
- **Session cookie** is `Secure`, `HttpOnly`, `SameSite=Strict` and expires after 12 h. Because a Strict cookie is withheld on a redirect chain that started on accounts.google.com, the callback answers with a tiny self-navigating page instead of a 302
- **Secrets in `.env` only** — no credentials in code or the database
- **Peer cap** — `MAX_PEERS` env var (default 20, clamped to 1–50, read in `wireguard.py`), enforced server-side on every create path
- **CSRF** — the `SameSite=Strict` session cookie plus an Origin/Referer host check on every POST/PUT/PATCH/DELETE. No per-form tokens; every allowlisted account is a full admin

---

## License

MIT
