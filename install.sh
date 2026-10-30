#!/usr/bin/env bash
#
# Traverse — guided installer for Debian/Ubuntu.
# Takes a fresh box from `git clone` to a running dashboard. Asks how you'll
# reach the dashboard (your domain / a free sslip.io hostname / local-only via
# SSH tunnel), whether to add Pi-hole, and — only if you want — WireGuard
# details; shows a summary, and changes nothing until you confirm.
# Safe to re-run: existing keys / .env / <iface>.conf are kept, never
# clobbered; the systemd unit + nginx config are re-rendered each run.
#
#   sudo ./install.sh
#   sudo DRY_RUN=1 ./install.sh      # ask the questions, print the plan, change nothing
#
# Unattended (pre-set any answer as an env var; no terminal = defaults), e.g.:
#   sudo ACCESS_MODE=domain DOMAIN=vpn.example.com INSTALL_PIHOLE=yes \
#        TRAVERSE_GOOGLE_CLIENT_ID='….apps.googleusercontent.com' \
#        TRAVERSE_GOOGLE_CLIENT_SECRET='…' TRAVERSE_ADMIN_EMAILS='you@example.com' ./install.sh
#
#   ACCESS_MODE     domain | sslip | local     (DOMAIN set → domain; else local)
#   INSTALL_PIHOLE  yes | no                   (default no)
#   WG_INTERFACE / WG_PORT / WG_SUBNET         (defaults wg0 / 51820 / 10.8.0.0/24)
#   RUN_CERTBOT, ADD_SWAP                      yes | no
#
set -euo pipefail

c_g=$'\e[32m'; c_y=$'\e[33m'; c_r=$'\e[31m'; c_b=$'\e[36m'; c_d=$'\e[2m'; c_h=$'\e[1m'; c_0=$'\e[0m'
info() { printf '%s==>%s %s\n' "$c_b" "$c_0" "$*"; }
ok()   { printf '%s  ✓%s %s\n' "$c_g" "$c_0" "$*"; }
warn() { printf '%s  !%s %s\n' "$c_y" "$c_0" "$*"; }
die()  { printf '%s  ✗ %s%s\n' "$c_r" "$*" "$c_0" >&2; exit 1; }
hdr()  { printf '\n%s%s%s\n' "$c_h" "$*" "$c_0"; }
is_ip(){ [[ $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; }
TTY=false; [[ -t 0 ]] && TTY=true
DRY_RUN="${DRY_RUN:-}"

# ── preflight ────────────────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$PROJECT_DIR/app.py" && -f "$PROJECT_DIR/requirements.txt" ]] || die "Run from the traverse repo root (app.py not found in $PROJECT_DIR)."
if [[ -z $DRY_RUN ]]; then
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run as root:  sudo ./install.sh"
  command -v apt-get >/dev/null 2>&1 || die "This installer targets Debian/Ubuntu (apt). Other distros: see the manual steps in README.md."
fi

# ── prompt helpers (env override → prompt → default) ─────────────────────────
ask() { # ask VAR "prompt" "default"
  local __var=$1 __prompt=$2 __def=${3:-} __ans
  [[ -n ${!__var:-} ]] && return
  if $TTY; then
    read -rp "$(printf '%s?%s %s%s: ' "$c_b" "$c_0" "$__prompt" "${__def:+ [$__def]}")" __ans || true
    printf -v "$__var" '%s' "${__ans:-$__def}"
  else
    printf -v "$__var" '%s' "$__def"
  fi
}
yesno() { # yesno VAR "prompt" y|n  → VAR=yes|no
  local __var=$1 __prompt=$2 __def=$3 __ans __v=${!1:-}
  case ${__v,,} in
    y|yes|true|1|on)  printf -v "$__var" yes; return ;;
    n|no|false|0|off) printf -v "$__var" no;  return ;;
  esac
  __ans=$__def
  if $TTY; then
    read -rp "$(printf '%s?%s %s [%s]: ' "$c_b" "$c_0" "$__prompt" "$([[ $__def == y ]] && echo Y/n || echo y/N)")" __ans || true
    __ans=${__ans:-$__def}
  fi
  [[ ${__ans,,} == y* ]] && printf -v "$__var" yes || printf -v "$__var" no
}
envval() { grep -E "^$1=" "$PROJECT_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
set_env() { # set_env KEY VALUE — replace or append in .env
  local f="$PROJECT_DIR/.env"
  if grep -qE "^$1=" "$f"; then
    local tmp; tmp="$(mktemp)"
    awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k{print k"="v; next} {print}' "$f" > "$tmp"
    cat "$tmp" > "$f"; rm -f "$tmp"
  else
    printf '%s=%s\n' "$1" "$2" >> "$f"
  fi
}

PUBLIC_IP="$(curl -4 -fsS --max-time 6 https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || true)"
EGRESS_IF="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'dev \K\S+' | head -1 || true)"
[[ -n ${EGRESS_IF:-} ]] || EGRESS_IF=eth0
HAVE_ENV=false; [[ -f "$PROJECT_DIR/.env" ]] && HAVE_ENV=true

echo
printf '%s  Traverse installer%s  %s%s%s\n' "$c_h" "$c_0" "$c_d" "$PROJECT_DIR" "$c_0"
[[ -n $DRY_RUN ]] && warn "DRY RUN — questions only; nothing on this server will change."

# ── 1. How will you open the dashboard? ──────────────────────────────────────
if [[ -z ${ACCESS_MODE:-} ]]; then
  if [[ -n ${DOMAIN:-} ]]; then
    ACCESS_MODE=domain
  elif $TTY; then
    SSLIP_HOST="${PUBLIC_IP//./-}.sslip.io"
    hdr "How will you open the dashboard?"
    echo "  1) My own domain (HTTPS)                    ${c_d}← recommended${c_0}"
    echo "  2) No domain: free ${SSLIP_HOST} hostname (HTTPS)"
    echo "  3) Local only: through an SSH tunnel, nothing exposed to the internet"
    read -rp "$(printf '%s?%s Choose 1-3 [1]: ' "$c_b" "$c_0")" a || true
    case ${a:-1} in 2) ACCESS_MODE=sslip ;; 3) ACCESS_MODE=local ;; *) ACCESS_MODE=domain ;; esac
  else
    ACCESS_MODE=local   # unattended with no DOMAIN: expose nothing
  fi
fi
case $ACCESS_MODE in
  domain)
    while [[ -z ${DOMAIN:-} ]] || is_ip "$DOMAIN"; do
      $TTY || die "ACCESS_MODE=domain needs DOMAIN=your.domain"
      [[ -n ${DOMAIN:-} ]] && warn "That's an IP address — Google sign-in needs a domain name (or pick option 2)."
      DOMAIN=""
      echo "  ${c_d}Point a DNS A record for it at this server (${PUBLIC_IP:-its public IP}) first.${c_0}"
      ask DOMAIN "Your domain (e.g. vpn.example.com)" ""
    done
    DEFAULT_URL="https://${DOMAIN}" ;;
  sslip)
    is_ip "${PUBLIC_IP:-}" || die "Couldn't detect this server's public IPv4 address, which the sslip.io hostname is built from. Use option 1 or 3."
    DOMAIN="${PUBLIC_IP//./-}.sslip.io"
    RUN_CERTBOT=yes     # sslip only makes sense with HTTPS
    DEFAULT_URL="https://${DOMAIN}" ;;
  local)
    DOMAIN=""
    DEFAULT_URL="http://localhost:5000" ;;
  *) die "ACCESS_MODE must be domain, sslip or local (got '$ACCESS_MODE')" ;;
esac
TRAVERSE_PUBLIC_URL="${TRAVERSE_PUBLIC_URL:-$DEFAULT_URL}"

# ── 2. Google sign-in ────────────────────────────────────────────────────────
if $HAVE_ENV; then
  hdr "Google sign-in"
  echo "  Existing .env found — keeping its sign-in settings."
  existing_url="$(envval TRAVERSE_PUBLIC_URL)"
  if [[ -n $existing_url && $existing_url != "$TRAVERSE_PUBLIC_URL" ]]; then
    warn ".env has TRAVERSE_PUBLIC_URL=$existing_url, but this setup is $TRAVERSE_PUBLIC_URL."
    warn "Update it (and the Google redirect URI) in .env if you're switching how you reach the dashboard."
  fi
else
  hdr "Google sign-in  ${c_d}(about 5 minutes; step-by-step: README → \"Google sign-in\")${c_0}"
  echo "  1. https://console.cloud.google.com/auth/overview — set up the consent screen"
  echo "     (Audience: External; add your Google account under Audience → Test users)"
  echo "  2. https://console.cloud.google.com/apis/credentials → Create credentials →"
  echo "     OAuth client ID → Web application, Authorized redirect URI:"
  printf '       %s%s/login/google/callback%s\n' "$c_g" "$TRAVERSE_PUBLIC_URL" "$c_0"
  echo "  ${c_d}Leave these blank to finish later in .env.${c_0}"
  ask TRAVERSE_GOOGLE_CLIENT_ID "Google OAuth client ID" ""
  if [[ -z ${TRAVERSE_GOOGLE_CLIENT_SECRET:-} && -n $TRAVERSE_GOOGLE_CLIENT_ID ]] && $TTY; then
    read -rsp "$(printf '%s?%s Google OAuth client secret (hidden): ' "$c_b" "$c_0")" TRAVERSE_GOOGLE_CLIENT_SECRET || true; echo
  fi
  ask TRAVERSE_ADMIN_EMAILS "Google account email(s) allowed to sign in (comma-separated)" ""
fi

# ── 3. Pi-hole ───────────────────────────────────────────────────────────────
hdr "Extras"
PIHOLE_PRESENT=false; command -v pihole >/dev/null 2>&1 && PIHOLE_PRESENT=true
yesno INSTALL_PIHOLE "Install Pi-hole ad-blocking for VPN devices too?" n
if [[ $INSTALL_PIHOLE == yes ]] && $PIHOLE_PRESENT; then
  warn "Pi-hole is already installed here — not touching it. Connect it by hand: README → \"Pi-hole\"."
  INSTALL_PIHOLE=no
fi

# ── 4. WireGuard (sensible defaults unless asked) ────────────────────────────
WG_DEFAULTS=(wg0 51820 10.8.0.0/24)
if [[ -z ${WG_INTERFACE:-}${WG_PORT:-}${WG_SUBNET:-} ]]; then
  yesno WG_ADVANCED "Advanced WireGuard settings? (No = ${WG_DEFAULTS[0]}, port ${WG_DEFAULTS[1]}/udp, ${WG_DEFAULTS[2]})" n
  if [[ $WG_ADVANCED == yes ]]; then
    ask WG_INTERFACE "WireGuard interface" "${WG_DEFAULTS[0]}"
    ask WG_PORT      "WireGuard UDP port"  "${WG_DEFAULTS[1]}"
    ask WG_SUBNET    "WireGuard subnet"    "${WG_DEFAULTS[2]}"
  fi
fi
WG_INTERFACE="${WG_INTERFACE:-${WG_DEFAULTS[0]}}"
WG_PORT="${WG_PORT:-${WG_DEFAULTS[1]}}"
WG_SUBNET="${WG_SUBNET:-${WG_DEFAULTS[2]}}"
[[ $WG_SUBNET =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]] || die "WG_SUBNET must look like 10.8.0.0/24 (got '$WG_SUBNET')"
[[ $WG_PORT =~ ^[0-9]+$ ]] || die "WG_PORT must be a number (got '$WG_PORT')"

SUBNET_BASE="${WG_SUBNET%/*}"; SUBNET_PREFIX="${WG_SUBNET#*/}"
WG_SERVER_VPN_IP="${SUBNET_BASE%.*}.1"
ENDPOINT="${DOMAIN:-$PUBLIC_IP}"     # what devices dial: the domain, else the IP
[[ -n $ENDPOINT ]] || die "Could not determine this server's public IP — set DOMAIN."
if [[ $ACCESS_MODE != local && -n $DOMAIN ]]; then
  yesno RUN_CERTBOT "Get a free HTTPS certificate for $DOMAIN (Let's Encrypt)?" y
else
  RUN_CERTBOT=no
fi
PIHOLE_PASSWORD_NEW=""
[[ $INSTALL_PIHOLE == yes ]] && PIHOLE_PASSWORD_NEW="$(od -An -N10 -tx1 /dev/urandom | tr -d ' \n')"
if [[ $ACCESS_MODE == local ]]; then PIHOLE_WEB="http://localhost:8088/admin/"; else PIHOLE_WEB="/pihole"; fi

# ── summary + confirm ────────────────────────────────────────────────────────
case $ACCESS_MODE in
  domain) mode_txt="your domain — ${TRAVERSE_PUBLIC_URL}" ;;
  sslip)  mode_txt="free hostname — ${TRAVERSE_PUBLIC_URL}" ;;
  local)  mode_txt="local only — ${TRAVERSE_PUBLIC_URL} via SSH tunnel" ;;
esac
signin_txt="${TRAVERSE_ADMIN_EMAILS:-not set yet (fill in .env later)}"
$HAVE_ENV && signin_txt="kept from existing .env"
hdr "Here's the plan"
printf '  %-14s %s\n' "Dashboard"  "$mode_txt"
printf '  %-14s %s\n' "Sign-in"    "Google — $signin_txt"
printf '  %-14s %s\n' "HTTPS"      "$([[ $RUN_CERTBOT == yes ]] && echo "Let's Encrypt certificate for $DOMAIN" || { [[ $ACCESS_MODE == local ]] && echo "not needed (the SSH tunnel is encrypted)" || echo "no"; })"
printf '  %-14s %s\n' "WireGuard"  "$WG_INTERFACE, ${ENDPOINT}:${WG_PORT}/udp, subnet $WG_SUBNET"
printf '  %-14s %s\n' "Pi-hole"    "$([[ $INSTALL_PIHOLE == yes ]] && echo "install — VPN devices use it for DNS (${WG_SERVER_VPN_IP})" || echo "no")"
printf '  %-14s %s\n' "Installs"   "wireguard, python venv$([[ $ACCESS_MODE != local ]] && echo ", nginx")$([[ $RUN_CERTBOT == yes ]] && echo ", certbot")$([[ $INSTALL_PIHOLE == yes ]] && echo ", Pi-hole")"
echo
if [[ -n $DRY_RUN ]]; then
  ok "Dry run complete — nothing was changed. Run without DRY_RUN=1 to install."
  exit 0
fi
if $TTY; then
  yesno PROCEED "Go ahead and install?" y
  [[ $PROCEED == yes ]] || { warn "Cancelled — nothing was changed."; exit 0; }
fi

# ── optional swap first on small boxes (pip + Pi-hole can OOM on <1GB) ───────
MEM_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
if [[ ${MEM_MB:-9999} -lt 2048 ]] && ! swapon --show 2>/dev/null | grep -q .; then
  yesno ADD_SWAP "RAM is ${MEM_MB}MB with no swap — add a 2GB swapfile?" y
  if [[ $ADD_SWAP == yes ]]; then
    info "Adding 2GB swapfile…"
    fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile; mkswap /swapfile >/dev/null; swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    ok "2GB swap enabled"
  fi
fi

# ── system packages ──────────────────────────────────────────────────────────
info "Installing system packages…"
export DEBIAN_FRONTEND=noninteractive
PKGS=(wireguard wireguard-tools python3 python3-venv python3-pip iptables curl ca-certificates)
[[ $ACCESS_MODE != local ]] && PKGS+=(nginx)
apt-get update -qq
apt-get install -y -qq "${PKGS[@]}" >/dev/null
ok "packages installed"

# ── python virtualenv ────────────────────────────────────────────────────────
info "Setting up Python virtualenv…"
[[ -d "$PROJECT_DIR/venv" ]] || python3 -m venv "$PROJECT_DIR/venv"
"$PROJECT_DIR/venv/bin/pip" install -q --upgrade pip
"$PROJECT_DIR/venv/bin/pip" install -q -r "$PROJECT_DIR/requirements.txt"
ok "venv ready"

# ── WireGuard server keys + interface config ─────────────────────────────────
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
umask 022
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-traverse.conf
sysctl -q --system >/dev/null 2>&1 || true
systemctl enable "wg-quick@${WG_INTERFACE}" >/dev/null 2>&1 || true
systemctl restart "wg-quick@${WG_INTERFACE}"
ok "WireGuard up on ${WG_SERVER_VPN_IP}:${WG_PORT}/udp"

# ── Pi-hole (optional) ───────────────────────────────────────────────────────
# Pi-hole's installer only runs without dialogs when a config already exists,
# so seed a minimal pihole.toml (FTL fills in every other default):
#   DNS answers only on the WireGuard interface (never an open resolver);
#   the web UI/API listen on loopback:8088 (nginx owns 80/443) and, behind a
#   domain, sit under the Traverse login at /pihole — same as upstream prod.
if [[ $INSTALL_PIHOLE == yes ]]; then
  info "Installing Pi-hole (takes a few minutes)…"
  install -d -m 755 /etc/pihole
  {
    echo '[dns]'
    echo '  upstreams = ["1.1.1.1", "1.0.0.1"]'
    echo "  interface = \"${WG_INTERFACE}\""
    echo '  listeningMode = "SINGLE"'
    echo '[webserver]'
    echo '  port = "127.0.0.1:8088"'
    if [[ $ACCESS_MODE != local ]]; then
      echo '[webserver.paths]'
      echo '  webhome = "/admin/"'
      echo '  prefix = "/pihole"'
    fi
  } > /etc/pihole/pihole.toml
  if curl -fsSL https://install.pi-hole.net | bash /dev/stdin --unattended >/var/log/traverse-pihole-install.log 2>&1 \
     && command -v pihole >/dev/null 2>&1; then
    pihole setpassword "$PIHOLE_PASSWORD_NEW" >/dev/null
    ok "Pi-hole installed (log: /var/log/traverse-pihole-install.log)"
  else
    warn "Pi-hole install failed — see /var/log/traverse-pihole-install.log. Continuing without it."
    INSTALL_PIHOLE=no
  fi
fi

# ── .env ─────────────────────────────────────────────────────────────────────
if $HAVE_ENV; then
  warn ".env already exists — keeping your config (delete it to regenerate)"
else
  info "Writing .env (generating SECRET_KEY)…"
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  umask 077
  cat > "$PROJECT_DIR/.env" <<EOF
# Generated by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ) — access mode: ${ACCESS_MODE}
SECRET_KEY=${SECRET_KEY}
PORT=5000
DATABASE_PATH=${PROJECT_DIR}/database.db

# Google sign-in — see README "Google sign-in". Redirect URI:
#   <TRAVERSE_PUBLIC_URL>/login/google/callback
TRAVERSE_PUBLIC_URL=${TRAVERSE_PUBLIC_URL}
TRAVERSE_GOOGLE_CLIENT_ID=${TRAVERSE_GOOGLE_CLIENT_ID:-}
TRAVERSE_GOOGLE_CLIENT_SECRET=${TRAVERSE_GOOGLE_CLIENT_SECRET:-}
TRAVERSE_ADMIN_EMAILS=${TRAVERSE_ADMIN_EMAILS:-}

WG_INTERFACE=${WG_INTERFACE}
WG_PORT=${WG_PORT}
WG_SUBNET=${WG_SUBNET}
WG_SERVER_VPN_IP=${WG_SERVER_VPN_IP}
WG_ENDPOINT=${ENDPOINT}
WG_DNS=1.1.1.1

# Pi-hole integration (optional) — see README "Pi-hole".
# PIHOLE_URL is the FTL API base (no /api).
PIHOLE_ENABLED=false
# PIHOLE_URL=http://127.0.0.1:8088
# PIHOLE_PASSWORD=
# PIHOLE_WEB_URL=/pihole
EOF
  chmod 600 "$PROJECT_DIR/.env"
  umask 022
  ok "wrote $PROJECT_DIR/.env"
fi
if [[ $INSTALL_PIHOLE == yes ]]; then
  set_env PIHOLE_ENABLED  true
  set_env PIHOLE_URL      http://127.0.0.1:8088
  set_env PIHOLE_PASSWORD "$PIHOLE_PASSWORD_NEW"
  set_env PIHOLE_WEB_URL  "$PIHOLE_WEB"
  set_env WG_DNS          "$WG_SERVER_VPN_IP"
  ok "connected Pi-hole in .env (new peers get ad-blocking DNS)"
fi

# ── systemd service ──────────────────────────────────────────────────────────
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

# ── nginx (not in local-only mode: gunicorn stays on 127.0.0.1:5000) ─────────
if [[ $ACCESS_MODE != local ]]; then
  info "Configuring nginx (server_name $ENDPOINT)…"
  if [[ $INSTALL_PIHOLE == yes || $(envval PIHOLE_WEB_URL) == /pihole* && $(envval PIHOLE_ENABLED) == true ]]; then
    pihole_filter='/__PIHOLE_\(BEGIN\|END\)__/d'
  else
    pihole_filter='/__PIHOLE_BEGIN__/,/__PIHOLE_END__/d'
  fi
  sed -e "s|__DOMAIN__|${ENDPOINT}|g" -e "$pihole_filter" "$PROJECT_DIR/deploy/nginx.conf" \
      > /etc/nginx/sites-available/traverse
  ln -sf /etc/nginx/sites-available/traverse /etc/nginx/sites-enabled/traverse
  [[ -e /etc/nginx/sites-enabled/default ]] && rm -f /etc/nginx/sites-enabled/default
  nginx -t >/dev/null 2>&1 || die "nginx config test failed (run: nginx -t)"
  systemctl reload nginx
  ok "nginx configured"
fi

# ── firewall (only if ufw is active) ─────────────────────────────────────────
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  info "Adding ufw rules…"
  rules=(22/tcp "${WG_PORT}/udp")
  [[ $ACCESS_MODE != local ]] && rules+=(80/tcp 443/tcp)
  for r in "${rules[@]}"; do ufw allow "$r" >/dev/null 2>&1 || true; done
  [[ $INSTALL_PIHOLE == yes ]] && ufw allow in on "$WG_INTERFACE" to any port 53 >/dev/null 2>&1 || true
  ok "ufw: allowed ${rules[*]}$([[ $INSTALL_PIHOLE == yes ]] && echo ", DNS on $WG_INTERFACE")"
fi

# ── Let's Encrypt HTTPS ──────────────────────────────────────────────────────
if [[ $RUN_CERTBOT == yes ]]; then
  # certbot's HTTP-01 check fails unless the domain already points here —
  # say so up front instead of leaving the user with a cryptic failure.
  RESOLVED="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}')"
  if [[ -z $RESOLVED ]]; then
    warn "$DOMAIN doesn't resolve yet — add a DNS A record pointing it at ${PUBLIC_IP}."
  elif [[ -n $PUBLIC_IP && $RESOLVED != "$PUBLIC_IP" ]]; then
    warn "$DOMAIN resolves to $RESOLVED, but this server is ${PUBLIC_IP} — fix the DNS A record."
  fi
  info "Installing certbot + issuing certificate…"
  apt-get install -y -qq certbot python3-certbot-nginx >/dev/null
  if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect \
             --register-unsafely-without-email >/dev/null 2>&1; then
    ok "HTTPS enabled for $DOMAIN"
  else
    warn "certbot failed (DNS not pointed at this box yet? port 80 blocked?) — site is still up on HTTP,"
    warn "and Google sign-in won't work until HTTPS does. Retry: certbot --nginx -d $DOMAIN"
  fi
fi

# ── done ─────────────────────────────────────────────────────────────────────
hdr "✓ Traverse is installed and running"
if [[ $ACCESS_MODE == local ]]; then
  tunnel="ssh -L 5000:127.0.0.1:5000"
  [[ $INSTALL_PIHOLE == yes ]] && tunnel+=" -L 8088:127.0.0.1:8088"
  echo "  The dashboard is only reachable from this server. From your computer, run:"
  printf '      %s%s root@%s%s\n' "$c_g" "$tunnel" "${PUBLIC_IP:-<server-ip>}" "$c_0"
  printf '  then open  %shttp://localhost:5000/%s  (keep the ssh session open while you use it)\n' "$c_g" "$c_0"
else
  scheme=http; [[ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]] && scheme=https
  printf '  %sDashboard%s  %s://%s/\n' "$c_g" "$c_0" "$scheme" "$ENDPOINT"
  [[ $scheme == http ]] && warn "Serving over HTTP — Google sign-in needs HTTPS. Fix DNS, then: certbot --nginx -d ${DOMAIN:-<domain>}"
fi
if [[ -n $(envval TRAVERSE_PUBLIC_URL) && -n $(envval TRAVERSE_GOOGLE_CLIENT_ID) &&
      -n $(envval TRAVERSE_GOOGLE_CLIENT_SECRET) && -n $(envval TRAVERSE_ADMIN_EMAILS) ]]; then
  printf '  %sSign in%s    with Google as %s\n' "$c_g" "$c_0" "$(envval TRAVERSE_ADMIN_EMAILS)"
else
  warn "Google sign-in isn't configured yet — set the TRAVERSE_* vars in $PROJECT_DIR/.env, then: systemctl restart traverse"
  warn "Redirect URI to register: $(envval TRAVERSE_PUBLIC_URL)/login/google/callback"
fi
if [[ $INSTALL_PIHOLE == yes ]]; then
  printf '  %sPi-hole%s    admin at %s — password: %s\n' "$c_g" "$c_0" \
    "$([[ $ACCESS_MODE == local ]] && echo "$PIHOLE_WEB" || echo "<dashboard>/pihole/")" "$PIHOLE_PASSWORD_NEW"
fi
echo
warn "Cloud firewall: if your provider has one (Hetzner, AWS security groups, Oracle, GCP…),"
warn "allow ${WG_PORT}/udp$([[ $ACCESS_MODE != local ]] && echo " + 80,443/tcp") there too, or devices will connect but never handshake."
info "Next: sign in, follow the short tour, and add your first device. Handy commands:"
echo "    systemctl status traverse wg-quick@${WG_INTERFACE}$([[ $ACCESS_MODE != local ]] && echo " nginx")"
echo "    journalctl -u traverse -f"
echo
