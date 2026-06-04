# Changelog

## [1.13.1] — 2026-06-04 (CI fix — PSK temp-dir fallback)

### Fixed
- **`add_peer_to_interface` no longer requires `/etc/wireguard/` to exist.** The
  1.11.1 AppArmor fix hardcoded the preshared-key temp file to `/etc/wireguard/`,
  which crashed (`FileNotFoundError`) anywhere that directory is absent — notably
  the GitHub Actions runner, so CI had been red since 1.11.1. It now uses
  `/etc/wireguard/` only when it exists and is writable (production, where the
  `wg` AppArmor profile requires it) and falls back to the default temp dir
  otherwise (CI / tests / dev, where `wg` is stubbed). 175 tests pass; CI green.

## [1.13.0] — 2026-06-04 (Traffic chart: 24h / 7-day history + lighter refresh)

The dashboard traffic chart can now show longer trends, and the chart polls
less aggressively to reduce load on the (2 GB) VPS.

### Added
- **Range toggle on the traffic chart — `15 MIN` / `24 H` / `7 D`.** The live
  15-minute view is unchanged; the two new ranges show persisted
  traffic-rate trends that survive restarts.
- **Persisted global traffic timeline.** The 60-second poller now records the
  all-peers RX/TX rate (bytes/sec) to a new `traffic_timeline` table, retained
  for 7 days. Counter resets (wg restart / peer removal) are clamped to 0.
- **`GET /api/traffic-history?range=live|24h|7d`** — `live` serves the in-memory
  15-min deque (1 s resolution); `24h` is bucket-averaged to 10-min points
  (~144), `7d` to 1-hour points (~168), so the chart stays readable.

### Changed
- **The traffic chart now refreshes every 15 s** (down from 1 s) and is
  decoupled from the main stats poll. The stat cards, live speed numbers, and
  peer table still update every 1 s — only the chart slowed down.

### Implementation
- `database.py` — `traffic_timeline` table + `record_traffic_sample` (7-day
  trim) + `get_traffic_timeline`.
- `alerts.py` — poller sums per-peer totals, computes the global rate from the
  previous tick, records a sample each tick.
- `routes/api.py` — `/api/traffic-history` with per-range bucketing.
- `templates/dashboard.html` — range-toggle buttons; the chart moved to its own
  15 s `/api/traffic-history` poller (range-aware time axis, NOW/PEAK/AVG over
  the selected window); removed from the 1 s `/api/stats` handler.
- `static/css/style.css` — `.chart-range-toggle` segmented pills.

### Notes
- `24h` / `7d` fill in over time — the first points appear within ~2 minutes,
  a full day after 24 h, a full week after 7 days.

### Verified
- 175 tests pass; endpoint returns all three ranges; poller writes samples
  (confirmed 60 s apart); inline JS lints clean; headless screenshot confirms
  the toggle renders.

## [1.12.0] — 2026-06-04 (Sleek polish + restructure pass)

A refinement pass on an already-mature UI — consistency, legibility, a11y, and
touch ergonomics. No behavioural or data-model changes. All CSS is append-only
(source order wins, per the longstanding convention).

### Foundation
- **Spacing + radius scale tokens** (`--space-1..6`, `--r-xs..pill`) added to
  `:root`, aliasing the existing values so nothing shifts — a base for
  consistent rhythm in new rules.
- **Form states** — inputs/selects/textareas now have proper `:disabled`
  (muted, `not-allowed`) and invalid styling (danger-tinted ring). Invalid is
  scoped to `:user-invalid` / `[aria-invalid]` so pristine create/wizard forms
  never flag red on load.
- **Light-theme gap fills** — stat-card icon backgrounds, flash-message text,
  `.code-block` colour, and the Leaflet map background now have light-theme
  overrides (scoped to `.theme-light`; dark theme unchanged).

### Dashboard
- **NOC bars** read more clearly — labels lifted from `--text-dim` to
  `--text-muted`, a touch more vertical air, and the `│` glyph separators
  replaced with soft gradient hairlines (desktop).
- **Count-up** now scales its duration with magnitude — small counts settle
  fast (~420 ms), six-figure values animate deliberately (capped 1100 ms),
  instead of a flat 700 ms for everything.

### Consistency
- **Tunnel-mode badges** (VPN Only / Split) promoted from duplicated inline
  styles in `peers/list` + `peers/detail` to reusable `.badge-cyan` /
  `.badge-amber` classes (identical look, single source of truth).

### Accessibility & modals
- **`confirmDialog` hardened** — focus is trapped between the dialog buttons,
  restored to the trigger on close, and the rest of the page is set `inert`
  while open.
- **Peer-detail modals consolidated** — the hand-rolled Regenerate / Rotate-PSK
  / Remove-schedule modals are now inline `data-confirm` forms using the shared
  (now-hardened) confirm dialog, so they inherit the focus-trap a11y and drop
  ~60 lines of bespoke markup.

### Touch / mobile
- **Topbar icon buttons** (help / theme / sound / sign-out) get a 44×44 hit box
  on touch devices (`pointer: coarse`).
- **Peers table scroll affordance** — soft edge shadows reveal when the
  horizontally-scrollable table has more columns off-screen.

### Files
```
static/css/style.css   (~110 lines appended)
static/js/app.js        (count-up duration; confirmDialog focus-trap/inert)
templates/dashboard.html (NOC — no glyph change needed; CSS-only separators)
templates/peers/list.html, peers/detail.html (badge classes; modal→data-confirm)
VERSION → 1.12.0
```

### Verified
- 175 tests pass; CSS brace-balanced; all routes render 200; service restarts clean.
- Headless-Chromium screenshots (login + authed dashboard/detail/peers via a
  base-href trick — Playwright/CDP still won't run on this box) confirm layout.

## [1.11.1] — 2026-06-04 (Post-migration fixes — VPN, Pi-hole, speedtest, stale refs)

The VPS was migrated to a new host: WireGuard server moved from `wg0`/`10.8.0.0/24`/`51820`
to **`wg1`/`10.9.0.0/24`/`51821`**, Pi-hole/FTL to **`10.9.0.1:8088`**, and the venv was
rebuilt on Python 3.14. This release fixes everything the migration broke and sweeps up the
hardcoded old values left behind. All values are env-driven (`.env` is canonical).

### Fixed — VPN data plane
- **Peers never reached the live interface.** `wg` is now AppArmor-confined and may only read
  preshared-key files from `/etc/wireguard/`. `add_peer_to_interface` wrote the temp PSK to
  `/tmp`, so every `wg set … preshared-key` failed with `fopen: Permission denied` — the peer
  was saved to the DB but never added to the interface (no handshake, no traffic). The temp PSK
  file is now created under `/etc/wireguard/`.
- **Connected but no internet (DNS).** Peers created with "use Pi-hole" had `DNS = 10.8.0.1`
  baked in from a hardcoded `PIHOLE_DNS` constant — the old Pi-hole IP, which on the new host
  routes into the dead wg0 tunnel. `PIHOLE_DNS` is now `os.getenv('WG_SERVER_VPN_IP', …)`.

### Fixed — wrong-interface / stale references
- **Dashboard** NOC panel showed a hardcoded `wg0`; now renders the actual `WG_INTERFACE`.
- **/logs** WireGuard panel tailed `wg-quick@wg0`; now tails `wg-quick@{WG_INTERFACE}`.
- **Speedtest** never finished: (a) `speedtest-cli` was missing after the venv rebuild
  (it was never pinned) — now in `requirements.txt` along with `psutil` and `gunicorn`;
  (b) job state lived in a per-worker module global, so RUN and STATUS hit different gunicorn
  workers — moved to a single-row SQLite table `speedtest_job` (worker-independent, with a
  180 s staleness guard).
- Module-default fallbacks and user-facing copy updated off the old `wg0`/`10.8.0.1`/`51820`
  values (`wireguard.py`, `alerts.py`, `app.py`, peer templates, README).

### Added
- **`PIHOLE_WEB_URL`** env var — the user-facing Pi-hole admin link (the reverse-proxied,
  auth-gated `/pihole` path), separate from `PIHOLE_URL` (the direct FTL API base the backend
  talks to). Dashboard bar, sidebar link, and settings page use it.

### Notes
- `.gitignore` now also excludes `*.bak` / `.env.bak*` backups.
- No schema-breaking changes; the 175-test suite passes.

## [1.11.0] — 2026-06-03 (Portable Pi-hole integration + auth-gated reverse proxy)

Shaken out of a VPS migration that exposed a hardcoded dependency. Two
related changes that make the Pi-hole integration portable and let the
Pi-hole admin UI (or any internal service) be mounted behind the
Traverse login.

### Fixed
- **Pi-hole API URL is now env-driven.** Five call sites in `routes/api.py` hardcoded `http://10.8.0.1:8080/api/...`, so the dashboard's Pi-hole panel silently broke on any host where Pi-hole didn't live at that exact address. All five now route through a `_pihole_url()` helper that reads `PIHOLE_URL`, tolerates an optional `/pihole` web prefix (for reverse-proxy mounting), and is forgiving about trailing slashes.

### Added
- **`/_internal/auth-check` endpoint** — returns `200` when the caller has a valid Traverse session, `401` otherwise, and never redirects (so it's usable as an nginx `auth_request` target). This lets nginx gate reverse-proxied subpaths behind the existing dashboard login.
- **Pi-hole admin mountable behind Traverse auth.** With FTL's `webserver.paths.webhome` set to a `/pihole/` prefix and an nginx `location /pihole/ { auth_request /_internal/auth-check; ... }`, the full Pi-hole UI can be reached at `https://<host>/pihole` — accessible without being on the VPN, yet invisible to anyone not authenticated against Traverse. Two-tier by design: Traverse session gate first, Pi-hole's own login second.

## [1.10.0] — 2026-05-19 (UI Polish — Seamless Transitions + Aesthetic Pass)

A non-functional release: no behavioural or data-model changes, no new
routes. The goal was to take the existing dark theme from "functional"
to "feels deliberate" — coherent motion, a consistent visual rhythm,
and a few touches of glass / gradient / blur that read as modern
without being noisy.

### Motion — unified tokens + page transitions
- **Motion design tokens** (`--ease-out`, `--ease-in-out`, `--ease-spring`, `--dur-instant|fast|base|slow`) replace the ad-hoc `0.1 / 0.12 / 0.15 / 0.18 / 0.2 s` sprinkle that had accumulated across `style.css`. Every new transition reads from the same scale so timing feels consistent across the app.
- **Cross-fade page transitions** — `body.tv-navigating` fades the current view out before the browser commits to the next URL, and the new `.page-content` animation eases it in. Internal anchor clicks are intercepted so the perceptual cut between routes is replaced with a brief cross-fade; external / new-tab / modified-click / cross-origin / `download` / hash-only / `data-no-transition` links are passed through untouched.
- **View Transitions API opt-in** (`@view-transition { navigation: auto; }`) — Chromium-based browsers get a native cross-fade for free; the JS-driven path detects `document.startViewTransition` and bows out so there's no double-animating.
- **`bfcache` safety** — a `pageshow` listener wipes `tv-navigating` on back / forward so the page doesn't restore with a faded body.

### Aesthetic — glass, gradient, ambient depth
- **Glass topbar + bottom-nav** — `backdrop-filter: saturate(140%) blur(14px)` over a translucent surface. Content scrolls visibly underneath instead of cutting against a flat panel.
- **Ambient body gradient** — fixed `body::before` paints two faint purple radials (top-left + bottom-right) plus a centre vignette. Cards now float against texture instead of pure black.
- **Active nav indicator** — the `border-left: 3px solid var(--accent)` on `.nav-link.active` is replaced with a `::before` pseudo that springs in (`scaleY 0 → 1`, `--ease-spring`) and uses a purple → violet linear gradient with an accent-tinted glow.
- **Hairline dividers** — the topbar bottom edge, sidebar right edge, card headers, and `<thead>` row use a horizontal gradient `linear-gradient(transparent, var(--border), transparent)` so seams fade at the edges instead of running pillar-to-pillar.
- **Bottom-nav SVG icons** — replaced `⬡ ◈ 🌍 ⚑ ⚙` with the same SVG set the sidebar uses (`stroke="currentColor" width="22"`). The mobile bottom-nav now matches the desktop sidebar 1:1.
- **Topbar WireGuard status pill** — `WG UP` / `WG DOWN` chip with a coloured dot and an accent-tinted background. Collapses to just the dot below 540 px so the topbar stays compact on mobile.
- **Active bottom-nav top accent** — 2 px gradient line `transparent → accent → transparent` with a glow.

### Micro-interactions
- **Cursor-aware card sheen** — `.stat-card` (and any `.card.tv-sheen` / `.noc-panel.tv-sheen`) has an `::after` radial gradient that follows the pointer via `--mx` / `--my` custom properties set in a `pointermove` handler. Coarse-pointer (touch) devices skip the JS so battery isn't drained on phones.
- **Stat-value count-up** — `.stat-card-value`, `.noc-val`, and `[data-tv-count]` whose text parses as an integer animate from `0 → final` over 700 ms with an `ease-out` cubic. Non-numeric values (subnet, `● RUN`, etc.) are skipped automatically.
- **Button micro-depth** — `.btn` lifts 1 px on `:hover`, settles back with a 0.97 scale on `:active`. Primary / success / danger / warning variants get a colour-tinted shadow on hover (e.g. `box-shadow: 0 6px 18px -6px rgba(124, 106, 247, 0.55)` for primary). `[disabled]` buttons never lift.
- **Staggered grid entrance** — `.stats-grid > *` and `.noc-bar .noc-stat` fade-and-rise in 40 ms apart up to the 7th item, so a page load reads as a wave instead of a slam. Auto-clamps after the 8th item to avoid runaway delays on large grids.
- **Stat / card lift on hover** — 2 px translateY plus a soft 8 px shadow.
- **Bottom-nav active icon** — translates up 1 px and scales 1.06× on the active tab.

### Focus + accessibility
- **Coherent `:focus-visible` ring** — `outline: 2px solid color-mix(in srgb, var(--accent) 75%, transparent)` with a 4 px accent-tinted box-shadow halo. `outline: none` on `:focus` (non-visible) so mouse users don't see it; keyboard users still do. Inputs keep their own ring tuned to match.
- **`prefers-reduced-motion: reduce` master switch** — collapses every animation and transition (including `scroll-behavior`) to ~0 ms. The page-exit fade and the count-up animator both bail out at the JS layer too.

### Typography rhythm
- **Numeric typography** — `font-variant-numeric: tabular-nums` applied to every stat-value / monospace class so digit columns line up. `font-feature-settings: "cv11", "ss01", "ss03"` enables prettier `1` / `a` / `l` glyphs on Inter where supported.
- **Heading tracking** — `.page-title`, `.card-title`, `.help-title`, `.tv-modal-hdr` get `letter-spacing: -0.012em` for the slightly tighter display feel.
- **Uppercase label rhythm** — `.stat-card-label`, `.noc-lbl`, `thead th`, `.filter-chip-group-label` use `letter-spacing: 0.075em` + `font-weight: 600` for consistent caps.
- **`text-rendering: optimizeLegibility` + antialiasing** — applied globally on `body`.

### Bug fix
- **`/about` CSP error** — the changelog was rendered with `{{ changelog | safe | replace('\n', '<br>') }}`, which let literal `<script>` text inside backticks (added by 1.7.0 / 1.8.0 entries describing CSP work) be parsed as real `<script>` tags before the existing nonce-bearing JS replaced the innerHTML. Chrome's CSP blocked the orphans and surfaced two console errors per page load. Fix: drop the server-rendered fallback — let the existing JS produce the only `innerHTML` write.

### Implementation
- **Files added**: none.
- **`static/css/style.css`** — ~430 lines appended at the end across two polish blocks. Per the longstanding "never edit earlier CSS rules" guideline, all new rules are appended so source order makes them win.
- **`static/js/app.js`** — three additions: the page-exit fade interceptor (handles `<a>` clicks + form submits, with `View-Transitions` / reduce-motion bail-outs), the count-up animator (DOMContentLoaded scan + `requestAnimationFrame` cubic-ease), and the cursor-sheen `pointermove` setter (skipped on coarse-pointer devices).
- **`templates/base.html`** — added the topbar `.topbar-wg-pill`; replaced the five `<span class="icon">…</span>` glyphs in `.bottom-nav` with `<svg class="icon-svg">…</svg>` matching the sidebar set.
- **`templates/about.html`** — emptied the `#changelog-content` server-rendered block (the JS owns the render).

### Verified
- **Pytest** — `175 passed in ~14 s` (no behavioural changes; suite unchanged).
- **Playwright headless** — logged in (password + TOTP), navigated 6 pages × desktop + mobile viewports = 12 screenshots, asserted via `getComputedStyle`:
  - `.topbar` backdrop-filter: `saturate(1.4) blur(14px)` ✓
  - `.bottom-nav` backdrop-filter: `saturate(1.4) blur(14px)` ✓
  - `body::before` background: 3-layer radial gradient ✓
  - `.topbar-wg-pill` present, text `"WG UP"` ✓
  - 5 of 5 `.bottom-nav-item .icon-svg`; no leftover `.icon` emoji span ✓
  - `.nav-link.active::before` background: `linear-gradient(rgb(124, 106, 247), rgb(183, 148, 244))` ✓
  - CSS tokens `--ease-out`, `--dur-base`, `--t-md` all resolved ✓
- **Console** — 0 errors, 0 warnings across all 6 pages × 2 viewports.

### Suite total
**175 tests** (unchanged). UI polish has no test surface beyond the static-asset gzip / cache-header checks already covered.

### Files modified
```
static/css/style.css   (~430 lines appended)
static/js/app.js       (~150 lines added: page-exit fade, count-up, sheen)
templates/base.html    (topbar WG pill + bottom-nav SVGs)
templates/about.html   (drop server-rendered changelog block)
VERSION                → 1.10.0
CHANGELOG.md
```

---

## [1.9.0] — 2026-05-12 (Per-Peer Schedules)

### Added
- **Per-peer scheduled enable/disable** — each peer can have an "allowed window" (days + time-of-day in an IANA timezone). The poller flips the peer's `enabled` flag and the wg0 state every minute based on whether the current local time is inside or outside the window. Useful for time-of-day rules like a kid's laptop disabled after 22:00. Windows that cross midnight (e.g. `22:00–07:00`) are supported — the "previous day's window" continues to apply until the end-time the next morning.
- **New "Schedule" card** on the peer detail page with day checkboxes, `<input type="time">` start/end pickers, an IANA timezone field backed by a `<datalist>` of common zones (UTC, Europe/Berlin, Europe/London, America/New_York, Asia/Karachi, Asia/Dubai, Asia/Singapore, Asia/Tokyo, Australia/Sydney, etc.), an "active" toggle (so you can pause a schedule without deleting it), and a confirm-modal removal.
- **New routes**: `POST /peers/<id>/schedule` (upsert with validation) and `POST /peers/<id>/schedule/delete`. Both gated by `@login_required`, both write to the audit log (`peer.schedule_saved`, `peer.schedule_deleted`).
- **New audit / notification events**: `peer.schedule_enabled` and `peer.schedule_disabled` rows when the poller flips state, plus a `peer_schedule_applied` notification fired on each transition. The event is seeded in `notification_event_toggles` so it shows up on `/notifications` automatically.

### Implementation
- **New top-level module `schedules.py`** — pure helpers (`parse_days`, `format_days`, `is_within_window`). Side-effect free; the same function the poller calls is what the tests exercise.
- **New DB table `peer_schedules`** — `peer_id INTEGER PRIMARY KEY, days_of_week TEXT, enabled_from TEXT, enabled_to TEXT, timezone TEXT, enabled INTEGER, ...` with `ON DELETE CASCADE` so deleting a peer drops their schedule. Single row per peer; the PK enforces one-schedule-per-peer at the schema level.
- **`alerts.py` poller section** — new `'schedules'` `_swallow` block. Only flips wg0/DB on transitions (avoids thrashing every tick). Timezone resolution via `zoneinfo.ZoneInfo`, falls back to UTC if the saved name doesn't resolve.

### Tests
- **`tests/test_schedules.py` — 35 cases**
  - `parse_days` / `format_days`: csv/list/set inputs, garbage rejection, `'Weekdays'` / `'Weekends'` / `'Every day'` special-cases.
  - `is_within_window`: same-day window inclusive/exclusive boundaries, wrong-day rejection, midnight-crossing with today / yesterday selected, neither-day case, equal-times → never-in-window, malformed input.
  - DB helpers: upsert round-trip, replacement semantics, delete, CASCADE on peer delete, `get_all_peer_schedules` joining peer fields.
  - Routes: login gate, persistence + audit, no-days rejection, garbage-time rejection, equal-times rejection, bogus-timezone rejection, delete + audit, paused-schedule flag persistence.
  - Poller integration: out-of-window → wg0 remove + DB flip, in-window from disabled → wg0 add + DB flip, paused schedule leaves state alone (fails the test if remove/add is called).

### Suite total
**140 → 175 tests** (+35). Suite runs in ~14 s.

### Files added
```
schedules.py
tests/test_schedules.py
```

### Files modified
```
alerts.py, database.py
routes/peers.py
templates/peers/detail.html
VERSION → 1.9.0
```

---

## [1.8.0] — 2026-05-12 (UI 2FA, CSP Tightening, Speedtest Sparkline, Coverage)

### Security
- **2FA enrolment in the UI** — `/settings/security` now hosts a real enrol / disable / regenerate-codes flow. New routes blueprint (`routes/security.py`) drives a three-step enrolment: visit `/settings/security/totp/enroll` (fresh secret stashed in session) → confirm the 6-digit code → land on the one-shot backup-codes page. 10 single-use backup codes are generated per enrol, stored only as sha256 hex digests, and consumable at `/login/verify` in place of a TOTP code (alphanumeric input branches to the backup-code path; 6-digit numeric branches to TOTP). Disable + regenerate gate on a password re-prompt so a session left open can't lose 2FA without the password.
- **Legacy `TOTP_SECRET` env path is preserved** — `_get_totp_secret()` consults the DB first, falls back to env, so existing setups keep working until the operator migrates via the new "Migrate to UI 2FA" prompt in `/settings`.
- **New events**: `totp_enrolled`, `totp_disabled`, `backup_code_used` (seeded in `notification_event_toggles`, fired through the normal `send_notification` path).
- **CSP tightening — drop `'unsafe-inline'` from `script-src`**. Every request now gets a fresh `g.csp_nonce = secrets.token_urlsafe(18)`; the CSP header is rebuilt with `script-src 'self' 'nonce-{nonce}'`, and every inline `<script>` block in the template tree (~20 across 14 files) now carries `nonce="{{ csp_nonce }}"`. Naked `<script>` tags can no longer execute — an XSS payload that smuggles JS into the page is denied by the browser instead of running with admin privileges. `style-src` keeps `'unsafe-inline'` because inline `style="..."` attributes are pervasive in the admin views and the XSS risk from style is much lower.
- **Inline event handlers eliminated** — `onclick="..."`, `onchange="..."`, `onsubmit="..."` were the last bit blocking the nonce-only CSP. ~20 handlers across `peers/detail.html`, `port_forwards/index.html`, `settings.html`, and `offline.html` are converted to `data-modal-open` / `data-modal-close` / `data-copy-target` / `data-copy-text` / `data-toggle-password` / `data-reload-page` attributes, dispatched by a single delegated listener in `static/js/app.js`.

### Added
- **Speedtest history sparkline** on `/settings` — replaces the recent-results table with a dual-line `<canvas>` (download + upload) over the last 30 runs. Retention bumped 5 → 50 in `record_speedtest`. Theme-aware: re-reads CSS variables on `traverse:themechange`.

### Reliability & quality
- **Bandwidth-anomaly maths extracted into `alerts.compute_bw_anomaly(snaps, min_rate, ratio)`** — pure function (no DB, no globals) so the heuristic is testable in isolation. The poller now calls it directly; behavior is unchanged (still 1 MB/s floor × 5 ratio over ~12 snapshots).
- **Pytest suite — 91 → 140** (+49 tests). New files:
  - `tests/test_bw_anomaly.py` (8) — too-few-snaps, flat traffic, above-floor-but-not-spike, clear spike, spike-below-floor, zero-interval, counter-reset, custom thresholds.
  - `tests/test_pihole_client.py` (8) — `_pihole_auth` happy path + SID cache reuse, no-password short-circuit, invalid-session response, network error, validity-window expiry; `_fetch_pihole_summary` happy path + 55 s cache, auth-failure null, fetch-error null.
  - `tests/test_wireguard.py` (20) — `_effective_allowed_ips` per-mode behaviour, `is_peer_active` time bands, `_safe_conf_value` sanitisation, `generate_client_config` injection containment + DNS override + full-tunnel route, `format_bytes` / `format_handshake_short` unit boundaries, `parse_wg_show` empty/short/`(none)`-endpoint cases, `generate_keypair` chaining, `add_peer_to_interface` PSK-via-temp-file (PSK never on cmdline), `remove_peer_from_interface` shape, `_run` raise-on-nonzero.
  - `tests/test_totp_ui_enroll.py` (9) — enrol login gate, start QR render, wrong-code rejection, correct-code persistence, backup-codes one-shot display, disable gates on password, regenerate-codes gates on password and replaces, backup code authenticates at login (single-use), 6-digit numeric input routes to TOTP path (won't consume a numeric backup code).
- **`test_alerts_logging.py` made order-independent** — clears the `traverse.poller` logger's handlers before reload so a prior test's import doesn't leave stale `RotatingFileHandler` attached to the named singleton.

### Database
- New table `totp_settings(id PRIMARY KEY CHECK(id=1), secret, backup_codes, enrolled_at, updated_at)` — seeded as a single empty row on first run.

### Files added
```
routes/security.py
templates/security/{index, enroll, backup_codes}.html
tests/{test_bw_anomaly, test_pihole_client, test_wireguard, test_totp_ui_enroll}.py
```

### Files modified
```
app.py, alerts.py, database.py
routes/{auth, settings}.py
static/js/app.js
templates/{base, dashboard, topology, settings, notifications, logs, audit,
           about, offline, map}.html
templates/peers/{create, detail, list, wizard}.html
templates/port_forwards/index.html
VERSION → 1.8.0
```

---

## [1.7.0] — 2026-05-11 (Security Hardening, Audit Log, Testing & CI)

### Security
- **MIT LICENSE** added.
- **Required `SECRET_KEY`** — no insecure default; refuse to boot without it.
- **Hardened session cookies** — `Secure`, `HttpOnly`, `SameSite=Strict`, 12 h lifetime; `session.permanent = True` so the lifetime actually applies.
- **Security headers** — CSP, HSTS, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` (relaxed from `no-referrer` so Chrome doesn't send `Origin: null` on form POSTs), and `Cache-Control: no-store` on non-static responses.
- **Origin / Referer CSRF defence** — `before_request` check on state-changing methods, hostname-only (port-agnostic) compare, accepts `X-Forwarded-Host` + optional `SERVER_NAME`. Stacks on top of `SameSite=Strict`.
- **Brute-force throttle** on `/login` and `/login/verify` — per-IP, 5 fails / 15 min, exponential lockout.
- **TOTP setup gate** — `/totp-setup` now requires a full login (no longer reachable with `totp_pending` alone, so a half-authed attacker can't read the seed).
- **POST-only `/logout`** — sign-out anchor in `base.html` converted to a form POST.
- **XSS fix on peer detail** — Pi-hole query log fields HTML-escaped before `innerHTML`. The domain originates upstream, so a malicious lookup from a peer could otherwise inject script into the admin page.
- **Config download injection guard** — `wireguard.py` sanitises endpoint / DNS / custom-routes so `\n` / `[` can't smuggle extra `[Interface]` / `[Peer]` sections into downloaded `.conf` files.
- **Backup tightening** — `backup_export` now also strips `preshared_key`; `backup_import` validates name regex, public/preshared key format, and that `vpn_ip` falls inside `WG_SUBNET`.
- **SSRF / proxy-input hardening** — `/api/peer/<id>/pihole-queries` validates `vpn_ip` is IPv4 and URL-quotes before splicing into the Pi-hole URL; `routes/map.py` rejects private/loopback/link-local/multicast before hitting `ipapi.co`; `notifications.py` enforces a Telegram token regex and allowlists Discord webhook hosts.
- **Config download cache headers** — `.conf` and QR PNG endpoints now return `Cache-Control: no-store, private` + `Pragma: no-cache`. Both responses contain the peer private key.
- **Secret scrubbing in notification log** — passwords, bearer tokens, Telegram bot tokens, and Discord webhook IDs/tokens are scrubbed from error strings before they land in `notification_log.error` (smtplib echoes credentials in exception text).
- **Migration tripwire** — `database.py` ident / column-definition allowlist regexes around the `migrate_db` ALTER TABLE loop. Today it only takes source-code literals, but if a future contributor threads user input through it, the tripwire raises.
- **Legacy Telegram token regex** — `alerts.py` `_legacy_telegram_fallback` validates `TELEGRAM_BOT_TOKEN` format before splicing into the api.telegram.org URL.

### Added
- **Audit log** — append-only `audit_log` table; `/audit` page (paginated, All / Peers / Auth / Settings filter chips) and `/audit.csv` export. Hooked sites: login success/failure, logout, peer create / delete / enable / disable / bulk variants (tagged `via=bulk`) / regenerate / PSK rotate / kill. Best-effort writes that never block the underlying operation.
- **Per-peer preshared-key rotation** — `POST /peers/<id>/rotate-psk` replaces only the PSK so the peer's tunnel identity (keypair) is preserved. Re-syncs wg0 and bumps `config_regenerated_at` so the detail page can surface staleness. Detail page gets a 🔑 Rotate PSK button next to Regenerate Config.
- **Structured logging for the poller** — `alerts.py` now writes to `/var/log/traverse/poller.log` (rotating; overridable via `TRAVERSE_POLLER_LOG`, falls back to stderr). New `_swallow(section)` context manager logs the section name + traceback before silently continuing — the catch-all robustness from `CLAUDE.md` stays, but failures are no longer invisible.
- **ROADMAP.md** — captures near-term polish, reliability work, security follow-ups, feature ideas, and explicit non-goals.
- **`MAX_PEERS` is env-configurable** — read from `MAX_PEERS` env (default 20), clamped to the project's 50-peer hard ceiling; garbage falls back to default. `routes/peers.py` and `routes/dashboard.py` import the shared constant from `wireguard.py` instead of redefining it.

### Testing & CI
- **Pytest suite — 0 → 91 tests** across `tests/test_{auth,csrf,peers,backup,max_peers,totp,notifications,bulk_peers,port_forwards,alerts_poller,alerts_logging,alerts_notify,audit,psk_rotation}.py`. `conftest.py` pins env vars to deterministic values, runs each test on a fresh on-disk SQLite DB in `tmp_path`, stubs the WireGuard CLI on both the `wireguard` module and the route modules that imported by name, and silences the poller thread. `requirements-dev.txt` adds pytest. `pytest.ini` wires `testpaths`.
- **GitHub Actions CI** — `.github/workflows/ci.yml` runs on every push to `main` and on PRs: `py_compile` over every tracked Python file, `pytest -q`, then a factory smoke test (`create_app() → GET /` should redirect to `/login`). Python 3.12, pip-cached on `requirements-dev.txt`. No deploy automation — production stays git pull + systemctl restart.

### Refactor
- **Single Telegram code path** — `_notify(event_type, message, severity, legacy_html=None)` is now the only send-path inside `alerts.py`. Always goes through `notifications.send_notification()`; only when `legacy_html` is passed does it ALSO fire the env-var Telegram fallback (renamed `_legacy_telegram_fallback` to make the boundary explicit — the escape hatch for "DB is broken and WG just died" early-boot). Eliminates duplicate Telegram notifications for users with both env + DB Telegram configured. WG-state blocks collapse from ~20 lines each to one `_notify()` call.
- **Inactivity alert routed through notifications module** — env-driven `ALERT_INACTIVE_HOURS` path now fires the new `peer_inactive_hours` event (seeded in `migrate_db`, added to `EVENT_LABELS`) and goes through `send_notification`, so it respects `/notifications` toggles and reaches every enabled channel instead of being Telegram-only.

### Light theme finish
- **CSS variable gaps closed** — append-only overrides at the end of `style.css` for `.install-banner` / `.install-banner.ios-tip` (was a literal dark gradient), `.topology-grid-bg` dots, `.toggle-switch` slider thumb, `.code-block` / `.code-wrap`, and the `.danger-zone` tint.
- **Theme-aware Chart.js** — `window._tvPalette()` reads colors from resolved CSS variables; pages push their Chart instances onto `window._tvCharts` at construction; a single `traverse:themechange` listener updates tooltip + grid + axis colors in place. No reload needed on toggle.
- **Theme-aware topology canvas** — peer-name labels and endpoint hostname under the server node now resolve from `--text` / `--text-dim` (previously hardcoded `#e2e8f0` / `#94a3b8`, invisible on light). Theme toggle dispatches `traverse:themechange` so the canvas redraws.

### Polish
- **Logo redesign** — cleaner compass + winding S-path motif on a dark squircle, brand-purple gradient (`#7c6af7`). All PWA icon sizes, apple-touch-icon, multi-res favicon, splash screen, and `app.png` regenerated from the new SVG so every surface is consistent. Source SVGs (`logo.svg`, `logo-mark.svg`) added for re-rendering.
- **README hero** repointed to the in-repo `static/img/app.png` so GitHub renders the new compass mark.
- Dead first `.num-flash` CSS rule + `num-in` keyframe removed (the later definition was already overriding it).

### Fixed
- **CSRF/origin port mismatch** — origin check compared full netloc (`host:port`), so an `Origin: https://host:443` vs `request.host: host` returned 403 on every POST, breaking login. Now compares hostnames only.
- **Referrer-Policy: no-referrer broke login** — Chrome sends `Origin: null` on form POSTs under `no-referrer`, which the new CSRF check rejected. Relaxed to `same-origin`.
- **Dashboard tiles + chart blank when `app.js` is deferred** — the light-theme refactor added inline `<script>` calls to `window._tvPalette()` in content blocks. With `defer` on `app.js` (added in 1.5.0), content scripts ran first and hit `ReferenceError`, halting the rest of the JS on the page — CPU/RAM/DISK tiles and the live-traffic chart stopped populating. `_tvPalette()` and `_tvCharts` are now defined in a non-deferred inline script in `base.html` before `{% block content %}`; `app.js` slimmed to the theme-change listener.

### Database
- New table `audit_log(id, ts, action, target_type, target_id, target_name, actor_ip, details)` with indexes on `ts` and `action`.
- Seeded events extended with `psk_rotated` and `peer_inactive_hours` in `notification_event_toggles`.

### Files added
```
LICENSE
ROADMAP.md
.github/workflows/ci.yml
pytest.ini
requirements-dev.txt
routes/audit.py
templates/audit.html
static/img/{logo.svg, logo-mark.svg}
tests/{__init__.py, conftest.py, test_auth.py, test_csrf.py, test_peers.py,
       test_backup.py, test_max_peers.py, test_totp.py, test_notifications.py,
       test_bulk_peers.py, test_port_forwards.py, test_alerts_poller.py,
       test_alerts_logging.py, test_alerts_notify.py, test_audit.py,
       test_psk_rotation.py}
```

### Files modified
```
app.py, alerts.py, database.py, notifications.py, wireguard.py
routes/{api, auth, map, peers, settings}.py
templates/{base, dashboard, topology, peers/detail}.html
static/{css/style.css, js/app.js}
static/{favicon.ico, img/app.png, icons/*.png}
README.md, .env.example
VERSION → 1.7.0
```

+2866 / −124 across 56 files.

---

## [1.6.0] — 2026-05-09 (UI Primitives, Sortable Peers, Bulk Actions, CSV)

### Added
- **Toast notifications** — top-right slide-in toasts with success/error/warning/info variants, click-to-close, auto-dismiss after 4s. Mobile-responsive. `window.toast(msg, type, opts)`.
- **Confirm modal** — promise-based styled modal replacing every native `confirm()` dialog. Keyboard support (Esc cancels, Enter confirms). Capture-phase form handler auto-upgrades every existing `[data-confirm]` form without per-form changes. `window.confirmDialog({title, body, confirmLabel, danger})`.
- **Top loading bar** — 2px accent gradient at the top of the viewport during fetch activity. Auto-wraps `fetch()` globally; skip-list excludes 1-second pollers (`/api/stats`, `/api/server/health`, etc.) so it isn't perpetually active. `window.tvProgress.start() / .done()`.
- **Command palette** — `Cmd/Ctrl+K` opens a fuzzy-search palette with 15 commands (page navigation + theme toggle + help + sign out). Arrow-key navigation, Enter selects, Escape closes.
- **Keyboard shortcuts** — `?` opens help, `/` focuses search, `n` opens "new peer" wizard, sequence navigation: `g d` Dashboard, `g p` Peers, `g m` Map, `g a` Alerts, `g t` Topology, `g y` History, `g l` Logs, `g n` Notifications, `g f` Port Forwards, `g s` Settings.
- **Sortable peer table** — 10 sortable columns (id, name, device, IP, tunnel, status, last seen, RX/TX, expires, created). Click header to toggle asc/desc; arrow indicator; sort state persisted to `localStorage`. IPs zero-padded for correct numeric ordering.
- **Filter chips on `/peers/`** — three dimensions (Tunnel × Status × Device). Within-dimension OR, across-dimension AND, combinable with text search. Click a chip to toggle; "Clear filters" resets all.
- **Bulk actions on `/peers/`** — checkbox column with master checkbox respecting visible rows (indeterminate state included). Floating action bar exposes Disable/Enable/Delete with confirmation modal. Backend endpoints `POST /peers/bulk-disable | bulk-enable | bulk-delete` accept comma-separated IDs, sync wg0, and fire per-peer notifications.
- **CSV export** on `/peers/`, `/history`, `/alerts`, `/notifications`. Peer export strips `private_key` and `preshared_key` server-side as defense in depth. Capped at 2000 / 2000 / 2000 / 500 rows.
- **Real-time event feed** on the dashboard right column — polls `/api/events/recent` every 30 s, renders connect/disconnect/kill events with color-coded dots and relative timestamps. Respects the global pause toggle.
- **Skeleton states** for the rest of the live-loading panels — server health (CPU/RAM/disk/uptime/speed), Pi-hole bar (blocked/rate/blocklist/queries/clients), and the NOC PIHOLE indicator now shimmer until their `/api/server/health` and `/api/pihole-stats` fetches resolve.
- **Topology server-node ring** — DOM overlay with the existing `.topology-server-ring` CSS animation (slow-rotating dashed border) is now wired and visible on the topology page. Hidden when no peers are configured.

### Changed
- **Friendly empty states** — `/history`, `/alerts`, and the notifications log section now use `.empty-state-friendly` (large icon + heading + sub-text + optional CTA) matching the rest of the app.
- **Help overlay** — now lists every keyboard shortcut.
- **`/peers/` row Kill action** — now uses the new modal + toast flow instead of native `confirm()` + `alert()`.
- **SW cache name bumped `traverse-v2 → traverse-v3`** — installed PWA clients re-precache fresh `style.css` / `app.js` on next activation.

### Light theme
- ~150 lines of additional overrides covering: tables, search input, help overlay, form labels, badges, last-seen colors, NOC alert rows, quick nav, action buttons, install banner, sidebar nav, bottom nav, toasts, confirm modal, command palette, filter chips, skeleton, kbd, event feed, bulk action bar, sortable headers.

### Database
No schema changes. All new endpoints query existing tables.

### Files Modified
```
routes/{peers, api, history, alerts, notifications}.py
static/{css/style.css, js/app.js, sw.js}
templates/{base, dashboard, peers/list, history, alerts, notifications, topology}.html
```
+1758 / -136 lines across 15 files.

---

## [1.5.0] — 2026-05-08 (Performance + Polish)

### Performance
- **Flask response caching** via `flask-caching` — `/api/server/health` cached 15s (avoids re-running `psutil.cpu_percent(interval=0.3)`, `systemctl status wg-quick@wg0`, `pihole status`, `gravity.db` query on every poll); `/api/pihole/top-blocked` cached 55s
- **Database indexes** — `peers.enabled`, `peers.expires_at`, `connection_events.{peer_id,timestamp}`, `peer_bandwidth_snapshots.{peer_id,recorded_at}` (composite + singles), `alerts.{seen,created_at}`, `notification_log.sent_at`, `peer_locations.{peer_id,last_seen_at}`, `speedtest_results.tested_at`, `traffic_samples (peer_id, day)` — created in `init_db()` via `IF NOT EXISTS`
- **Nginx gzip** — `gzip on; gzip_comp_level 6` for HTML/CSS/JS/JSON/SVG. Result: `style.css` 72.5 KB → 13.2 KB on the wire (82% smaller); login HTML 3.1 KB → 1.1 KB (64%)
- **Nginx static caching** — `/static/` returns `Cache-Control: public, immutable, max-age=604800`; `access_log off` (no log churn for fonts/icons/CSS)
- **JS deferred** — `app.js` now uses `defer` so it does not block parsing
- **Poller audit** — `alerts.py` already runs on a 60s tick (not a tight loop); single tick covers WG show + bandwidth + pi-hole probe within ~1s; no stagger needed

### Visual polish
- **Skeleton shimmer** utility (`.skeleton`) for placeholder loading states
- **Stat bars** — padding tightened by ~20%, accent border-bottom separator between bars
- **Number flash** — chart NOW/PEAK/AVG values now flash accent → normal over 0.32s on change
- **Online dot pulse** — peers table green dots pulse at 2.2s
- **Alt rows + hover** — peer table rows alternate at `rgba(255,255,255,0.018)`, hover at `rgba(124,106,247,0.06)`; action chevrons fade in on row hover only
- **Right panel** — left-accent border bar on each section
- **Form focus rings** — consistent accent border + 3px halo on every input/textarea/select focus
- **Touch feedback** — `transform: scale(0.97)` active state on every button + nav item
- **Toggle switches** — proper `.toggle-switch` CSS-only component (44×24, accent ON, focus ring)
- **Status banner** — peer detail page now shows green/red banner at the top reflecting enabled state
- **Topology** — radial dot grid background pattern; CSS for slow-rotating server ring + active peer pulse glow available
- **Map** — `.map-vignette` wrapper applies a subtle inner box-shadow for depth at the edges
- **Bottom nav** — 2px accent line on top of the active tab (mobile)
- **Pi-hole grid** — `.ph-blocked-warn` (amber) / `.ph-blocked-zero` (green) classes for state-aware blocked count
- **Mobile font floor** — any `font-size:9px/10px` inline style auto-bumped to 11px on ≤768px
- **Page transition** — fade-in trimmed from 0.15s → 0.1s on mobile
- **Scrollbars** — 6px thumb at `rgba(255,255,255,0.12)`, hover `0.22`, transparent track — applied universally
- **Border-radius consistency** — 6px on `.btn`/action buttons, 999px (pill) on badges, 4px on inputs

### Added
- `cache_ext.py` — shared `Cache` instance (SimpleCache, 30s default)
- `flask-caching>=2.4.0` in `requirements.txt`

### Files Modified
- `app.py` — `cache.init_app(app)` after Flask app creation
- `database.py` — index creation block in `migrate_db()`
- `routes/api.py` — `@cache.cached` on `/api/server/health` (15s) + `/api/pihole/top-blocked` (55s)
- `templates/base.html` — `defer` on `app.js`
- `templates/peers/detail.html` — peer status banner at top
- `templates/topology.html` — `.topology-grid-bg` class on canvas card
- `templates/map.html` — `.map-vignette` wrapper on map card
- `static/css/style.css` — appended polish block (~250 lines)
- `/etc/nginx/sites-available/traverse` — gzip directives, `location /static/` with 7d immutable caching
- `VERSION` → 1.5.0

### Measurements
| Asset                | Before  | After (gzip) | Reduction |
|----------------------|--------:|-------------:|----------:|
| style.css            | 72.5 KB |     13.2 KB  | 82%       |
| login HTML           |  3.1 KB |      1.1 KB  | 64%       |
| Repeat-visit static  | network |  browser     | 100%      |

Cold load (`GET /` → 302 to `/login`): ~78 ms → ~50–65 ms (latency-bound; gains compound across the asset graph on first authenticated visit, where chart.min.js (200 KB → ~58 KB) and leaflet.min.js (147 KB → ~42 KB) compress).

---

## [1.4.0] — 2026-05-08 (Progressive Web App)

### Added
- **Installable PWA** — traverse can now be installed as a standalone app on iOS Safari, Android Chrome, and desktop Chromium browsers
- **Web App Manifest** at `/manifest.json` — eight icon sizes (72→512), maskable variants for Android adaptive icons, three app shortcuts (All Peers, Add Peer, Alerts)
- **Service worker** at `/sw.js` — precaches the app shell on install, runtime network-first with cache fallback, falls back to `/offline` page when network is unreachable. API calls (`/api/*`) bypass the cache so live data is always fresh.
- **Offline page** at `/offline` — standalone dark page with traverse logo, retry button, and auto-reload when the browser fires `online`
- **iOS PWA support** — apple-touch-icon, apple-touch-startup-image (1242×2688 splash with centered logo), `apple-mobile-web-app-capable`/`-status-bar-style`/`-title` meta tags
- **Install banner** — slim top bar that surfaces on Android/desktop Chrome via `beforeinstallprompt`; dismissable, remembers dismissal via localStorage, hides automatically once installed
- **iOS install tip** — static "Add to Home Screen" tooltip for iOS Safari (which doesn't fire `beforeinstallprompt`); separate dismissal key
- **Push notification handler** in the SW (server-side VAPID wiring is future work; the listener is in place)

### Files Added
- `static/manifest.json`
- `static/sw.js`
- `static/icons/` — `icon-{72,96,128,144,152,192,384,512}.png`, `apple-touch-icon.png`, `splash-1242x2688.png`
- `templates/offline.html`
- `routes/pwa.py` — new blueprint with `/manifest.json`, `/sw.js`, `/offline` (all public, no `login_required`)

### Files Modified
- `app.py` — registered `pwa_bp`
- `templates/base.html` — PWA meta tags in `<head>`, install banner + iOS tip elements, SW registration script, install prompt controllers
- `static/css/style.css` — `.install-banner` styles + slide-down animation + mobile breakpoint
- `VERSION` → 1.4.0

### Implementation notes
- `sw.js` is served with `Service-Worker-Allowed: /` so the SW can scope to the entire origin even though the file lives under `/static/` on disk
- `sw.js` is served `no-cache, no-store, must-revalidate` so updates propagate immediately; the SW versions its own asset cache via `CACHE_NAME` (currently `traverse-v1`)
- `/` is intentionally not in the precache list — it 302s to `/login` for unauthenticated visits, so precaching it would store the redirect or login page
- The original brief specified `/static/css/main.css` in the SW precache list; the actual CSS file is `style.css`, so the precache list was corrected to point at the real file
- All 10 generated icons composite correctly on Android adaptive backgrounds — the source `app.png` is RGB and gets `convert('RGBA')` before resize

---

## [1.3.0] — 2026-05-08 (Notifications)

### Added
- **Notifications page** at `/notifications` — manage Email (SMTP), Telegram, and Discord channels in one place
  - Each channel has its own enable toggle, configuration form, "Send Test" button (synchronous, shows ✅/❌ inline), and Save button
  - Telegram and Discord sections include collapsible setup instructions
  - Per-event toggles let you choose which of 15 event types fire notifications (peer_connected, peer_disconnected, peer_inactive_long, peer_expired, bw_anomaly, wg_down, wg_recovered, pihole_down, pihole_recovered, peer_added, peer_deleted, peer_killed, config_regenerated, login_success, login_failed)
  - Recent attempts log shows last 20 sends (timestamp, channel icon, event type, message snippet, ✅/❌ status, expandable error detail); "Clear log" button
  - Sidebar bell icon with green dot when at least one channel is enabled and minimally configured
- **Multi-channel notification dispatch** — every wired event fires on all enabled channels simultaneously, in a background thread (never blocks request handlers or the poller)
- **Wired event hooks**:
  - `alerts.py` poller: peer connect/disconnect (handshake transition), peer expired, traffic anomaly, peer inactive 7+ days (24 h throttle), WireGuard up/down, Pi-hole up/down (TCP probe to admin URL)
  - `routes/peers.py`: peer added (form + wizard), peer deleted, config regenerated
  - `routes/api.py`: peer killed
  - `routes/auth.py`: login success (post-TOTP), failed login attempt (wrong password or wrong TOTP code) — includes client IP

### Database
- New table `notification_settings(id, channel UNIQUE, enabled, config JSON, updated_at)` — seeded on first run with email/telegram/discord rows
- New table `notification_log(id, channel, event_type, message, success, error, sent_at)` — auto-trimmed to 500 most-recent rows
- New table `notification_event_toggles(event_type PRIMARY KEY, enabled)` — seeded with all 15 events default-on

### Implementation notes
- `notifications.py` (new module) uses stdlib only — `smtplib`, `urllib.request`, `email.mime`, `json`. No new pip dependencies.
- All sends wrapped in try/except; failures are logged but never crash the app.
- `.env` gains `NOTIFY_EMAIL_*`, `NOTIFY_TELEGRAM_*`, `NOTIFY_DISCORD_WEBHOOK` keys (also editable from the UI).

---

## [1.2.0] — 2026-05-07 (Batch 3 — Split Tunneling, DNS Override, Port Forwarding, Map Colours)

### Added
- **Peer tunnel mode** — three modes per peer: Full Tunnel (0.0.0.0/0), VPN Only (subnet only), Split Tunnel (subnet + custom CIDRs)
  - New `tunnel_mode` and `custom_routes` columns on `peers` table
  - `AllowedIPs` in client config computed from tunnel mode; server-side `wg set allowed-ips` remains `vpn_ip/32`
  - Create form: tunnel selector + JS-revealed custom CIDR input for split mode
  - Edit form on peer detail: tunnel mode + custom routes
  - Peers list: FULL / VPN / SPLIT badges per peer
  - Peer detail: Tunnel Mode row + effective AllowedIPs display
- **DNS override per peer** — full DNS selector replacing the binary Pi-hole toggle
  - Presets: Pi-hole (10.8.0.1), Cloudflare (1.1.1.1, 1.0.0.1), Google (8.8.8.8, 8.8.4.4), Quad9 (9.9.9.9), Custom
  - `dns_override` column; `generate_client_config()` uses override if set, else falls back to `dns` field
  - Selector on both create and edit forms
- **Port forwarding rules** — DNAT iptables rules forwarding public VPS ports to VPN peers
  - New `port_forwards` table; new blueprint at `/port-forwards/`
  - Create, toggle, delete via `/port-forwards/<id>/toggle|delete`
  - Rules applied with `iptables` and persisted to `/etc/iptables/rules.v4`
  - Security warning in UI; sidebar nav link (⇄)
  - Per-peer Port Forwards section on peer detail page
- **Map tunnel mode colours** — active peer markers, polylines, and right-panel dots are coloured by tunnel mode
  - Full tunnel: green; VPN only: cyan; Split: amber
  - Legend updated with all three modes; summary table adds Tunnel column
  - `tunnel_mode` included in `/api/peer-locations` response

### Database
- New columns on `peers`: `tunnel_mode TEXT DEFAULT 'full'`, `custom_routes TEXT DEFAULT ''`, `dns_override TEXT DEFAULT ''`
- New table `port_forwards(id, peer_id, description, protocol, external_port, internal_port, enabled, created_at)`

---

## [1.1.0] — 2026-05-07 (Batch 2 — UX & Notifications)

### Added
- Clickable peer rows on `/peers` — entire row navigates to detail (View button removed; QR retained; row click ignores nested links/buttons/forms)
- Copy-to-clipboard icon button next to VPN IP on peer detail page (✓ feedback for 2 s)
- Client-side peer search/filter (matches name, VPN IP, device type) with × clear and "🔍 No peers match" empty row
- Mini RX+TX sparklines per peer row, drawn on a `<canvas>` (no library) — green for active peers, grey for inactive, flat baseline if no data
- Session timer column on `/peers` showing duration since last `connected` event (e.g. `2h 14m`); refreshes every 60 s client-side
- Browser push notifications for peer connect/disconnect — `Notification.requestPermission()` asked once (state in `localStorage`); `/api/events/latest` polled every 60 s
- Sound alert toggle (🔔 / 🔕) in topbar — Web Audio API tones (880 Hz on connect, 440 Hz on disconnect); only plays when sound enabled AND notifications granted
- Kill button per peer (⚡) on detail page and each row — `POST /api/peer/<id>/kill` removes from wg0 and sets `enabled = 0`
- Peer location history (`peer_locations` table) — endpoint IP changes recorded with geo lookup; last 5 shown on detail page with flag emoji, city/country, masked IP, first/last seen
- Smooth `fadeIn` page transitions (150 ms CSS, respects `prefers-reduced-motion`)
- Help overlay (`?` topbar button) — tips, page index, quick stats, version; closes on outside click or Escape
- Friendly empty states on `/peers` and dashboard when no peers exist (large 🔒, "No peers yet", "Add your first device to get started", prominent + Add Peer CTA)

### Changed
- Live traffic chart on dashboard rebuilt — NOW / PEAK / AVG header stats, pulsing `● LIVE` dot (turns amber `● PAUSED` when paused), Chart.js v4 responsive gradients via `chartArea`, RX colour switched to cyan `#22d3ee`
- Speedtest now reports 1 decimal place instead of 2
- WG SHOW panel: `overflow-x: hidden` + `max-width: 100%` so long public keys can't blow the column

### Fixed
- `list_peers()` raw-timestamp bug — `last_handshake` was being overwritten with a formatted string before `_last_seen()` parsed it (always returned `never`)
- Pi-hole `api_seats_exceeded` — `_pihole_logout()` now `DELETE`s the old session before creating a new one; `max_sessions = 64` in `pihole.toml`
- `is_peer_active` threshold raised 180 → 300 s to avoid false-inactive flips during the ~170 s WireGuard rekey window
- Removed the duplicate Pi-hole `● ACTIVE` indicator on the dashboard (it was rendered both in the NOC bar and the Pi-hole bar)

### Database
- New table `peer_locations(id, peer_id, endpoint_ip, endpoint_port, geo_country, geo_city, geo_lat, geo_lon, geo_country_code, first_seen_at, last_seen_at)` — capped at 10 rows per peer (oldest by `last_seen_at` evicted)
- `connection_events.event_type` now includes `killed` (logged when `POST /api/peer/<id>/kill` succeeds)

### API
- `POST /api/peer/<id>/kill` — disconnect & disable a peer; preserves the DB record
- `GET  /api/peer/<id>/sparkline` — returns up to 10 RX+TX rate values from `peer_bandwidth_snapshots`
- `GET  /api/events/latest` — connection events from the last 70 s (drives the notification poller)

---

## [1.0.0] — 2026-05-07

### Added
- Full WireGuard VPN dashboard with peer CRUD (create, view, edit, delete, enable/disable)
- Auto-assigned VPN IPs from configurable subnet (10.8.0.0/24)
- WireGuard keypair generation (private, public, preshared keys) via `wg` CLI
- Client `.conf` file download and inline QR code on peer detail page
- Live traffic dashboard: 1-second polling, Chart.js RX/TX waveform, RAF-animated speed numbers
- NOC-style status bar (peers, active count, total RX/TX, WireGuard state)
- Server health bar: CPU, RAM, disk, WG uptime, VPS speed
- Pi-hole v6 integration: DNS blocking for all VPN clients
- Pi-hole status bar on dashboard: blocked count, rate, blocklist size, query count, client count
- Pi-hole DNS query log per peer on peer detail page
- Top blocked domains widget on dashboard (5-minute refresh)
- Connection map with Leaflet.js, peer geolocation via ipapi.co
- Background poller (60 s): WG down detection, peer connect/disconnect events, expired peer auto-disable, bandwidth anomaly alerts
- Telegram alert notifications (optional, via TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)
- TOTP 2FA support (optional, via TOTP_SECRET in .env)
- Traffic history chart per peer (30-day daily RX/TX)
- Speedtest widget (speedtest-cli integration)
- Backup / restore (JSON export/import of all peers and events)
- Per-peer device type (phone/laptop/desktop/tablet/router/other) with emoji icons
- Per-peer notes and optional expiry date with auto-disable
- Per-peer Pi-hole DNS toggle (use Pi-hole or fall back to 1.1.1.1)
- Connection history log (/history)
- Alerts page with severity badges (/alerts)
- Dark/light theme toggle (localStorage persistent)
- Dashboard auto-refresh pause/resume toggle
- Last-seen indicator on peers list (colour-coded relative time)
- One-page peer setup wizard with config preview step (/peers/wizard)
- Network topology diagram with Canvas — clickable peer nodes (/topology)
- System log viewer with live mode + client-side filter (/logs)
- Changelog and about page (/about)
- Settings page: WireGuard control, Pi-hole control, backup/restore, speedtest history, TOTP setup
