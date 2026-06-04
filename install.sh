#!/usr/bin/env bash
#
# Traverse — one-command installer for Debian/Ubuntu.
# Takes a fresh box from `git clone` to a running dashboard (HTTPS if a domain
# is given). Safe to re-run: existing keys / .env / wg0.conf are kept, never
# clobbered; the systemd unit + nginx config are re-rendered each run.
#
#   sudo ./install.sh
#
# Unattended (pre-set any prompt as an env var), e.g.:
#   sudo DOMAIN=vpn.example.com ADMIN_PASSWORD='s3cret' RUN_CERTBOT=yes ./install.sh
#
set -euo pipefail

c_g=$'\e[32m'; c_y=$'\e[33m'; c_r=$'\e[31m'; c_b=$'\e[36m'; c_0=$'\e[0m'
info() { printf '%s==>%s %s\n' "$c_b" "$c_0" "$*"; }
ok()   { printf '%s  ✓%s %s\n' "$c_g" "$c_0" "$*"; }
warn() { printf '%s  !%s %s\n' "$c_y" "$c_0" "$*"; }
die()  { printf '%s  ✗ %s%s\n' "$c_r" "$*" "$c_0" >&2; exit 1; }
is_ip(){ [[ $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; }

# ── preflight ────────────────────────────────────────────────────────────────
[[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run as root:  sudo ./install.sh"
command -v apt-get >/dev/null 2>&1 || die "This installer targets Debian/Ubuntu (apt). Other distros: see the manual steps in README.md."
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$PROJECT_DIR/app.py" && -f "$PROJECT_DIR/requirements.txt" ]] || die "Run from the traverse repo root (app.py not found in $PROJECT_DIR)."

# ── gather config (env override → prompt → default) ──────────────────────────
ask() { # ask VAR "prompt" "default"
  local __var=$1 __prompt=$2 __def=${3:-} __ans
  [[ -n ${!__var:-} ]] && return
  if [[ -t 0 ]]; then
    read -rp "$(printf '%s?%s %s%s: ' "$c_b" "$c_0" "$__prompt" "${__def:+ [$__def]}")" __ans || true
    printf -v "$__var" '%s' "${__ans:-$__def}"
  else
    printf -v "$__var" '%s' "$__def"
  fi
}

PUBLIC_IP="$(curl -fsS --max-time 6 https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
EGRESS_IF="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'dev \K\S+' | head -1 || true)"
[[ -n ${EGRESS_IF:-} ]] || EGRESS_IF=eth0

echo
info "Traverse installer  —  $PROJECT_DIR"
ask DOMAIN         "Domain for the dashboard (blank = use IP, HTTP only)" ""
ask ADMIN_USERNAME "Admin username" "admin"
if [[ -z ${ADMIN_PASSWORD:-} ]]; then
  if [[ -t 0 ]]; then
    read -rsp "$(printf '%s?%s Admin password: ' "$c_b" "$c_0")" ADMIN_PASSWORD; echo
    [[ -n $ADMIN_PASSWORD ]] || die "Admin password cannot be empty."
  else
    die "ADMIN_PASSWORD must be set when running without a TTY."
  fi
fi
ask WG_INTERFACE "WireGuard interface" "wg0"
ask WG_PORT      "WireGuard UDP port"  "51820"
ask WG_SUBNET    "WireGuard subnet"    "10.8.0.0/24"

SUBNET_BASE="${WG_SUBNET%/*}"; SUBNET_PREFIX="${WG_SUBNET#*/}"
WG_SERVER_VPN_IP="${SUBNET_BASE%.*}.1"
ENDPOINT="${DOMAIN:-$PUBLIC_IP}"
[[ -n $ENDPOINT ]] || die "Could not determine a domain or public IP — set DOMAIN."

# ── 1. system packages ───────────────────────────────────────────────────────
info "Installing system packages…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq wireguard wireguard-tools python3 python3-venv python3-pip \
                       nginx iptables curl ca-certificates >/dev/null
ok "packages installed"

# ── 2. python virtualenv ─────────────────────────────────────────────────────
info "Setting up Python virtualenv…"
[[ -d "$PROJECT_DIR/venv" ]] || python3 -m venv "$PROJECT_DIR/venv"
"$PROJECT_DIR/venv/bin/pip" install -q --upgrade pip
"$PROJECT_DIR/venv/bin/pip" install -q -r "$PROJECT_DIR/requirements.txt"
ok "venv ready"

# ── 3. WireGuard server keys + interface config ──────────────────────────────
info "Configuring WireGuard ($WG_INTERFACE, $WG_SUBNET, egress $EGRESS_IF)…"
install -d -m 700 /etc/wireguard
umask 077
if [[ ! -f /etc/wireguard/server_private.key ]]; then
  wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key
  ok "generated server keypair"
else
  warn "server keys already exist — keeping them"
fi
SERVER_PRIV="$(cat /etc/wireguard/server_private.key)"
WG_CONF="/etc/wireguard/${WG_INTERFACE}.conf"
if [[ ! -f "$WG_CONF" ]]; then
  cat > "$WG_CONF" <<EOF
[Interface]
Address    = ${WG_SERVER_VPN_IP}/${SUBNET_PREFIX}
ListenPort = ${WG_PORT}
PrivateKey = ${SERVER_PRIV}
PostUp     = iptables -A FORWARD -i ${WG_INTERFACE} -j ACCEPT; iptables -A FORWARD -o ${WG_INTERFACE} -j ACCEPT; iptables -t nat -A POSTROUTING -s ${WG_SUBNET} -o ${EGRESS_IF} -j MASQUERADE
PostDown   = iptables -D FORWARD -i ${WG_INTERFACE} -j ACCEPT; iptables -D FORWARD -o ${WG_INTERFACE} -j ACCEPT; iptables -t nat -D POSTROUTING -s ${WG_SUBNET} -o ${EGRESS_IF} -j MASQUERADE
EOF
  chmod 600 "$WG_CONF"
  ok "wrote $WG_CONF"
else
  warn "$WG_CONF already exists — keeping it"
fi
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-traverse.conf
sysctl -q --system >/dev/null 2>&1 || true
systemctl enable "wg-quick@${WG_INTERFACE}" >/dev/null 2>&1 || true
systemctl restart "wg-quick@${WG_INTERFACE}"
ok "WireGuard up on ${WG_SERVER_VPN_IP}:${WG_PORT}/udp"

# ── 4. .env ──────────────────────────────────────────────────────────────────
if [[ -f "$PROJECT_DIR/.env" ]]; then
  warn ".env already exists — keeping your config (delete it to regenerate)"
else
  info "Writing .env (generating SECRET_KEY)…"
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  umask 077
  cat > "$PROJECT_DIR/.env" <<EOF
# Generated by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
SECRET_KEY=${SECRET_KEY}
PORT=5000
DATABASE_PATH=${PROJECT_DIR}/database.db

ADMIN_USERNAME=${ADMIN_USERNAME}
ADMIN_PASSWORD=${ADMIN_PASSWORD}

WG_INTERFACE=${WG_INTERFACE}
WG_PORT=${WG_PORT}
WG_SUBNET=${WG_SUBNET}
WG_SERVER_VPN_IP=${WG_SERVER_VPN_IP}
WG_ENDPOINT=${ENDPOINT}
WG_DNS=1.1.1.1

# Pi-hole integration (optional). Install Pi-hole separately, then set these
# and flip PIHOLE_ENABLED to true. PIHOLE_URL is the FTL API base (no /api).
PIHOLE_ENABLED=false
# PIHOLE_URL=http://${WG_SERVER_VPN_IP}:8080
# PIHOLE_PASSWORD=
# PIHOLE_WEB_URL=/pihole
EOF
  chmod 600 "$PROJECT_DIR/.env"
  ok "wrote $PROJECT_DIR/.env"
fi

# ── 5. systemd service ───────────────────────────────────────────────────────
info "Installing systemd service…"
install -d -m 755 /var/log/traverse
sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g" "$PROJECT_DIR/deploy/traverse.service" \
    > /etc/systemd/system/traverse.service
systemctl daemon-reload
systemctl enable traverse >/dev/null 2>&1 || true
systemctl restart traverse
sleep 2
if systemctl is-active --quiet traverse; then
  ok "traverse service running"
else
  journalctl -u traverse -n 20 --no-pager || true
  die "traverse failed to start (log above)"
fi

# ── 6. nginx ─────────────────────────────────────────────────────────────────
info "Configuring nginx (server_name $ENDPOINT)…"
sed "s|__DOMAIN__|${ENDPOINT}|g" "$PROJECT_DIR/deploy/nginx.conf" \
    > /etc/nginx/sites-available/traverse
ln -sf /etc/nginx/sites-available/traverse /etc/nginx/sites-enabled/traverse
[[ -e /etc/nginx/sites-enabled/default ]] && rm -f /etc/nginx/sites-enabled/default
nginx -t >/dev/null 2>&1 || die "nginx config test failed (run: nginx -t)"
systemctl reload nginx
ok "nginx configured"

# ── 7. firewall (only if ufw is active) ──────────────────────────────────────
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  info "Adding ufw rules…"
  for r in 22/tcp 80/tcp 443/tcp "${WG_PORT}/udp"; do ufw allow "$r" >/dev/null 2>&1 || true; done
  ok "ufw: allowed 22, 80, 443, ${WG_PORT}/udp"
fi

# ── 8. optional swap on small boxes (<2GB, none configured) ───────────────────
MEM_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
if [[ ${MEM_MB:-9999} -lt 2048 ]] && ! swapon --show 2>/dev/null | grep -q .; then
  do_swap="${ADD_SWAP:-ask}"
  if [[ $do_swap == ask && -t 0 ]]; then
    read -rp "$(printf '%s?%s RAM is %sMB with no swap — add a 2GB swapfile? [Y/n]: ' "$c_b" "$c_0" "$MEM_MB")" a || true
    [[ ${a,,} == n* ]] && do_swap=no || do_swap=yes
  fi
  if [[ $do_swap == yes ]]; then
    info "Adding 2GB swapfile…"
    fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile; mkswap /swapfile >/dev/null; swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    ok "2GB swap enabled"
  fi
fi

# ── 9. optional Let's Encrypt HTTPS (needs a real domain) ────────────────────
if [[ -n $DOMAIN ]] && ! is_ip "$DOMAIN"; then
  do_cb="${RUN_CERTBOT:-ask}"
  if [[ $do_cb == ask && -t 0 ]]; then
    read -rp "$(printf '%s?%s Get a free HTTPS cert for %s via certbot now? [Y/n]: ' "$c_b" "$c_0" "$DOMAIN")" a || true
    [[ ${a,,} == n* ]] && do_cb=no || do_cb=yes
  fi
  if [[ $do_cb == yes ]]; then
    info "Installing certbot + issuing certificate…"
    apt-get install -y -qq certbot python3-certbot-nginx >/dev/null
    if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect \
               --register-unsafely-without-email >/dev/null 2>&1; then
      ok "HTTPS enabled for $DOMAIN"
    else
      warn "certbot failed (DNS not pointed at this box yet?) — site is still up on HTTP. Retry later: certbot --nginx -d $DOMAIN"
    fi
  fi
fi

# ── done ─────────────────────────────────────────────────────────────────────
scheme=http
[[ -n $DOMAIN ]] && ! is_ip "$DOMAIN" && [[ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]] && scheme=https
echo
ok "Traverse is installed and running."
printf '   %sDashboard%s  %s://%s/\n' "$c_g" "$c_0" "$scheme" "$ENDPOINT"
printf '   %sLogin%s      %s  (the password you set)\n' "$c_g" "$c_0" "$ADMIN_USERNAME"
echo
info "Next: open the dashboard and add your first peer. Handy commands:"
echo "    systemctl status traverse wg-quick@${WG_INTERFACE} nginx"
echo "    journalctl -u traverse -f"
[[ $scheme == http ]] && warn "Serving over HTTP. For HTTPS, point a domain at ${PUBLIC_IP} and re-run (or: certbot --nginx -d <domain>)."
echo
