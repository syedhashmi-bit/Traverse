/* Traverse — client-side helpers */

// ── Theme toggle ──────────────────────────────────────────────────────────────
(function () {
  const STORAGE_KEY = 'traverse-theme';
  function applyTheme(theme) {
    document.body.classList.toggle('theme-light', theme === 'light');
    document.body.classList.toggle('theme-dark',  theme !== 'light');
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = theme === 'light' ? '☀' : '☾';
    // Tell canvas-drawing code (charts, topology) to re-read CSS vars
    // and redraw. Avoids forcing a full page reload on toggle.
    try {
      window.dispatchEvent(new CustomEvent('traverse:themechange', {
        detail: { theme },
      }));
    } catch (_) { /* IE / very old browsers */ }
  }
  const saved = localStorage.getItem(STORAGE_KEY) || 'dark';
  applyTheme(saved);
  document.addEventListener('DOMContentLoaded', () => {
    applyTheme(localStorage.getItem(STORAGE_KEY) || 'dark');
    const btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.addEventListener('click', () => {
        const next = document.body.classList.contains('theme-light') ? 'dark' : 'light';
        localStorage.setItem(STORAGE_KEY, next);
        applyTheme(next);
      });
    }
  });
})();

// ── Chart palette listener ─────────────────────────────────────────────────
// window._tvPalette / window._tvCharts are defined in base.html in a
// non-deferred inline script so pages can call them at parse time
// before this (deferred) module has loaded. We just react to theme
// changes here and push the new colours into every registered chart.
window.addEventListener('traverse:themechange', () => {
  if (typeof Chart === 'undefined') return;
  const p = window._tvPalette();
  Chart.defaults.color       = p.textMuted;
  Chart.defaults.borderColor = p.borderLine;
  (window._tvCharts || []).forEach(ch => {
    if (ch.options.plugins && ch.options.plugins.tooltip) {
      const tt = ch.options.plugins.tooltip;
      tt.backgroundColor = p.tooltipBg;
      tt.titleColor      = p.tooltipFg;
      tt.bodyColor       = p.tooltipFg;
      tt.borderColor     = p.tooltipBorder;
    }
    if (ch.options.scales) {
      ['x', 'y'].forEach(k => {
        const sc = ch.options.scales[k];
        if (sc && sc.grid)  sc.grid.color = p.gridLine;
        if (sc && sc.title && sc.title.color) sc.title.color = p.textDim;
      });
    }
    ch.update('none');
  });
});

// Copy-to-clipboard
function copyText(text, btn) {
  const isIcon = btn && btn.classList.contains('copy-icon-btn');
  const orig   = btn.textContent;
  function feedback() {
    if (isIcon) {
      btn.textContent = '✓';
      btn.classList.add('copied');
      setTimeout(() => {
        btn.textContent = orig || '⎘';
        btn.classList.remove('copied');
      }, 2000);
    } else {
      btn.textContent = 'Copied!';
      btn.style.background = 'var(--success)';
      btn.style.color = '#fff';
      setTimeout(() => {
        btn.textContent = orig;
        btn.style.background = '';
        btn.style.color = '';
      }, 1800);
    }
  }
  function fallback() {
    const el = document.createElement('textarea');
    el.value = text;
    el.style.position = 'fixed';
    el.style.opacity = '0';
    document.body.appendChild(el);
    el.select();
    document.execCommand('copy');
    document.body.removeChild(el);
    feedback();
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(feedback).catch(fallback);
  } else {
    fallback();
  }
}

// Confirm delete forms
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-confirm]').forEach(el => {
    el.addEventListener('submit', e => {
      if (!confirm(el.dataset.confirm)) {
        e.preventDefault();
      }
    });
  });

  // Auto-dismiss flash messages after 5s
  setTimeout(() => {
    document.querySelectorAll('.flash').forEach(f => {
      f.style.transition = 'opacity 0.5s';
      f.style.opacity = '0';
      setTimeout(() => f.remove(), 500);
    });
  }, 5000);

  // Mobile sidebar hamburger
  const hamburger = document.getElementById('hamburger');
  const overlay   = document.getElementById('sidebar-overlay');
  if (hamburger) {
    hamburger.addEventListener('click', () => document.body.classList.toggle('sidebar-open'));
  }
  if (overlay) {
    overlay.addEventListener('click', () => document.body.classList.remove('sidebar-open'));
  }
  document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', () => document.body.classList.remove('sidebar-open'));
  });
});

// ── Page exit fade ────────────────────────────────────────────────────
// Intercepts internal link clicks and fades the content out before the
// browser navigates, so the perceptual cut between pages is replaced
// with a brief cross-fade (paired with .page-content's enter animation).
// Browsers that support the View Transitions API will already get a
// native cross-fade via @view-transition in CSS — we skip the manual
// fade there to avoid double-animating.
(function () {
  if (typeof window === 'undefined') return;

  const NATIVE_VT = 'startViewTransition' in document;
  // If the user prefers reduced motion, do nothing — CSS already kills
  // the entrance animation.
  const reduceMotion = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (NATIVE_VT || reduceMotion) return;

  // Pageshow fires on every navigation, including bfcache restore. Wipe
  // the navigating class so a "back" button doesn't leave the page faded.
  window.addEventListener('pageshow', () => {
    document.body.classList.remove('tv-navigating');
  });

  function shouldIntercept(a, ev) {
    if (!a || !a.href) return false;
    if (ev.defaultPrevented) return false;
    if (ev.button !== 0) return false;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return false;
    if (a.target && a.target !== '' && a.target !== '_self') return false;
    if (a.hasAttribute('download')) return false;
    if (a.dataset.noTransition === 'true') return false;
    let url;
    try { url = new URL(a.href, location.href); } catch (_) { return false; }
    if (url.origin !== location.origin) return false;
    // Same-page hash anchors don't navigate — leave them alone.
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return false;
    // mailto / tel / etc.
    if (!/^https?:$/.test(url.protocol)) return false;
    return true;
  }

  document.addEventListener('click', (ev) => {
    const a = ev.target && ev.target.closest && ev.target.closest('a');
    if (!shouldIntercept(a, ev)) return;
    ev.preventDefault();
    document.body.classList.add('tv-navigating');
    // 180ms matches the CSS .tv-navigating transition; add a small safety
    // margin so the fade is visible before the browser commits the nav.
    setTimeout(() => { window.location.href = a.href; }, 170);
  });

  // Forms that POST to a new page — fade out before submit.
  document.addEventListener('submit', (ev) => {
    const f = ev.target;
    if (!f || !(f instanceof HTMLFormElement)) return;
    if (f.dataset.noTransition === 'true') return;
    if (f.target && f.target !== '' && f.target !== '_self') return;
    // Skip AJAX-handled forms (those preventDefault inside their own handler;
    // we run on the same event bubble so we can detect this).
    // Use a microtask to let other listeners call preventDefault first.
    Promise.resolve().then(() => {
      if (ev.defaultPrevented) return;
      document.body.classList.add('tv-navigating');
    });
  });
})();

// ── Sound alert toggle (Web Audio API tones) ──────────────────────
(function () {
  const KEY = 'traverse-sound-enabled';
  let enabled = localStorage.getItem(KEY) === '1';
  let audioCtx = null;

  function ensureCtx() {
    if (audioCtx) return audioCtx;
    try {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) return null;
      audioCtx = new Ctor();
    } catch (e) { return null; }
    return audioCtx;
  }
  function tone(freq) {
    const ctx = ensureCtx();
    if (!ctx) return;
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    const osc  = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.1, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.3);
  }
  function updateBtn() {
    const btn = document.getElementById('sound-toggle');
    if (!btn) return;
    btn.textContent = enabled ? '🔔' : '🔕';
    btn.title = enabled ? 'Sound alerts: on (click to mute)' : 'Sound alerts: off (click to enable)';
  }

  window.traverseSound = {
    play(eventType) {
      if (!enabled) return;
      if (!('Notification' in window) || Notification.permission !== 'granted') return;
      tone(eventType === 'connected' ? 880 : 440);
    },
    isEnabled() { return enabled; },
    set(on) {
      enabled = !!on;
      localStorage.setItem(KEY, enabled ? '1' : '0');
      updateBtn();
    },
  };

  document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('sound-toggle');
    if (!btn) return;
    updateBtn();
    btn.addEventListener('click', () => {
      const next = !enabled;
      window.traverseSound.set(next);
      if (next) {
        // First click after enabling — unlock audio with a tiny test tone
        const ctx = ensureCtx();
        if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
        tone(880);
      }
    });
  });
})();

// ── Toast notifications (window.toast) ─────────────────────────────────
(function () {
  function getContainer() {
    let el = document.getElementById('toast-container');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toast-container';
      document.body.appendChild(el);
    }
    return el;
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  const ICONS = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };

  window.toast = function (message, type, opts) {
    type = type || 'info';
    opts = opts || {};
    const ttl = opts.ttl == null ? 4000 : opts.ttl;
    const container = getContainer();
    const node = document.createElement('div');
    node.className = 'toast toast-' + type;
    node.setAttribute('role', type === 'error' ? 'alert' : 'status');
    node.innerHTML =
      '<span class="toast-icon">' + (ICONS[type] || ICONS.info) + '</span>' +
      '<div class="toast-body">' + escapeHtml(message) + '</div>' +
      '<button type="button" class="toast-close" aria-label="Close">×</button>';

    const dismiss = () => {
      if (node._gone) return;
      node._gone = true;
      node.classList.add('toast-leaving');
      setTimeout(() => { if (node.parentNode) node.parentNode.removeChild(node); }, 220);
    };
    node.querySelector('.toast-close').addEventListener('click', dismiss);

    container.appendChild(node);

    if (ttl > 0) setTimeout(dismiss, ttl);
    return { dismiss: dismiss, el: node };
  };
})();

// ── Top loading bar (window.tvProgress) ────────────────────────────────
(function () {
  function getBar() {
    let bar = document.getElementById('tv-loading-bar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'tv-loading-bar';
      document.body.appendChild(bar);
    }
    return bar;
  }

  let pending = 0;
  let trickleTimer = null;
  let progress = 0;

  function trickle() {
    if (pending <= 0) return;
    const inc = (1 - progress) * 0.06;
    progress = Math.min(0.95, progress + inc);
    setBar(progress);
    trickleTimer = setTimeout(trickle, 220);
  }

  function setBar(frac) {
    const bar = getBar();
    bar.style.width = (frac * 100).toFixed(2) + 'vw';
  }

  window.tvProgress = {
    start() {
      pending++;
      const bar = getBar();
      bar.classList.remove('tv-loading-done');
      bar.classList.add('tv-loading-active');
      if (pending === 1) {
        progress = 0.08;
        setBar(progress);
        if (trickleTimer) clearTimeout(trickleTimer);
        trickleTimer = setTimeout(trickle, 220);
      }
    },
    done() {
      pending = Math.max(0, pending - 1);
      if (pending === 0) {
        if (trickleTimer) { clearTimeout(trickleTimer); trickleTimer = null; }
        progress = 1;
        setBar(progress);
        const bar = getBar();
        bar.classList.add('tv-loading-done');
        setTimeout(() => {
          if (pending === 0) {
            bar.classList.remove('tv-loading-active', 'tv-loading-done');
            bar.style.width = '0';
            progress = 0;
          }
        }, 600);
      }
    },
  };

  // Wrap fetch to show progress for non-trivial calls. Skip /api/stats and
  // similar 1s pollers so the bar isn't constantly active.
  const origFetch = window.fetch;
  if (typeof origFetch === 'function' && !window._tvFetchWrapped) {
    window._tvFetchWrapped = true;
    const SKIP = /\/api\/(stats|server\/health|pihole-stats|peer\/\d+\/sparkline|peer\/\d+\/ping|events\/latest|notifications\/status|pihole\/top-blocked|peer-locations|server-location)\b/;
    window.fetch = function (input, init) {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      const skip = SKIP.test(url);
      if (!skip) window.tvProgress.start();
      const p = origFetch.apply(this, arguments);
      if (!skip) p.finally(() => window.tvProgress.done());
      return p;
    };
  }
})();

// ── Confirm modal (window.confirmDialog) ────────────────────────────────
(function () {
  window.confirmDialog = function (opts) {
    opts = opts || {};
    const title = opts.title || 'Are you sure?';
    const body  = opts.body  || '';
    const yes   = opts.confirmLabel || 'Confirm';
    const no    = opts.cancelLabel  || 'Cancel';
    const danger = !!opts.danger;

    return new Promise(resolve => {
      const overlay = document.createElement('div');
      overlay.className = 'tv-modal-overlay';
      overlay.innerHTML =
        '<div class="tv-modal" role="dialog" aria-modal="true">' +
          '<div class="tv-modal-hdr"></div>' +
          '<div class="tv-modal-body"></div>' +
          '<div class="tv-modal-actions">' +
            '<button type="button" class="btn btn-secondary tv-modal-cancel"></button>' +
            '<button type="button" class="btn ' + (danger ? 'btn-danger' : 'btn-primary') + ' tv-modal-confirm"></button>' +
          '</div>' +
        '</div>';
      overlay.querySelector('.tv-modal-hdr').textContent = title;
      overlay.querySelector('.tv-modal-body').textContent = body;
      overlay.querySelector('.tv-modal-cancel').textContent = no;
      overlay.querySelector('.tv-modal-confirm').textContent = yes;

      const prevFocus  = document.activeElement;
      const cancelBtn  = overlay.querySelector('.tv-modal-cancel');
      const confirmBtn = overlay.querySelector('.tv-modal-confirm');
      const inertTargets = [];

      function cleanup(result) {
        document.removeEventListener('keydown', onKey);
        inertTargets.forEach(el => { el.inert = false; });
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
        // Restore focus to whatever opened the dialog (keyboard a11y).
        if (prevFocus && typeof prevFocus.focus === 'function') {
          try { prevFocus.focus(); } catch (_) {}
        }
        resolve(result);
      }
      function onKey(e) {
        if (e.key === 'Escape') { cleanup(false); return; }
        if (e.key === 'Enter')  { cleanup(true);  return; }
        if (e.key === 'Tab') {
          // Trap focus between the two buttons so Tab can't escape the dialog.
          e.preventDefault();
          (document.activeElement === confirmBtn ? cancelBtn : confirmBtn).focus();
        }
      }

      cancelBtn.addEventListener('click', () => cleanup(false));
      confirmBtn.addEventListener('click', () => cleanup(true));
      overlay.addEventListener('click', e => { if (e.target === overlay) cleanup(false); });
      document.addEventListener('keydown', onKey);

      document.body.appendChild(overlay);
      // Make the rest of the page inert while the dialog is open.
      if ('inert' in HTMLElement.prototype) {
        Array.prototype.forEach.call(document.body.children, function (ch) {
          if (ch !== overlay && !ch.inert) { ch.inert = true; inertTargets.push(ch); }
        });
      }
      // Focus the confirm button shortly so Enter works and focus is inside.
      setTimeout(() => confirmBtn.focus(), 50);
    });
  };

  // Auto-upgrade existing data-confirm forms once DOM ready (defer so the
  // legacy DOMContentLoaded handler that uses native confirm() runs first
  // and we can replace its behavior).
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('form[data-confirm]').forEach(form => {
      // Replace default-prevent with promise-based confirm
      // Remove all prior submit listeners by cloning is too aggressive — instead,
      // mark the form so the legacy handler in this file knows to skip.
      form.dataset.confirmUpgraded = '1';
      form.addEventListener('submit', function (e) {
        if (form._tvProceed) return; // allow second submit after confirm
        e.preventDefault();
        e.stopImmediatePropagation();
        const danger = form.classList.contains('danger') ||
                       /delete|remove|kill|stop|reset/i.test(form.dataset.confirm || '');
        window.confirmDialog({
          title: form.dataset.confirmTitle || 'Are you sure?',
          body:  form.dataset.confirm,
          confirmLabel: form.dataset.confirmLabel || (danger ? 'Confirm' : 'Continue'),
          danger: danger,
        }).then(ok => {
          if (!ok) return;
          form._tvProceed = true;
          form.submit();
        });
      }, true); // capture, runs before the legacy native-confirm handler
    });
  });
})();

// ── Keyboard shortcuts ─────────────────────────────────────────────────
(function () {
  function isTyping(e) {
    const t = e.target;
    if (!t) return false;
    if (t.isContentEditable) return true;
    const tag = (t.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
    return false;
  }

  // Mac uses Cmd, others use Ctrl
  function isMod(e) { return e.metaKey || e.ctrlKey; }

  let waitingForG = false;
  let gTimeout = null;

  document.addEventListener('keydown', e => {
    // Cmd/Ctrl+K — command palette
    if (isMod(e) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      if (window.tvCmdPalette) window.tvCmdPalette.toggle();
      return;
    }

    // Skip when typing in inputs (except for Escape, handled per-component)
    if (isTyping(e)) return;
    if (e.altKey || e.metaKey || e.ctrlKey) return;

    // 'g' starts a sequence: g d, g p, g m, g a, g s, g h, g l, g n, g t
    if (e.key === 'g' || e.key === 'G') {
      waitingForG = true;
      if (gTimeout) clearTimeout(gTimeout);
      gTimeout = setTimeout(() => { waitingForG = false; }, 1000);
      return;
    }
    if (waitingForG) {
      const map = {
        d: '/',                  h: '/',
        p: '/peers/',
        m: '/map',
        a: '/alerts',
        s: '/settings/',
        l: '/logs',
        n: '/notifications',
        t: '/topology',
        y: '/history',
        f: '/port-forwards/',
      };
      const dest = map[(e.key || '').toLowerCase()];
      if (dest) {
        e.preventDefault();
        window.location.href = dest;
      }
      waitingForG = false;
      if (gTimeout) clearTimeout(gTimeout);
      return;
    }

    // ?  — open help
    if (e.key === '?') {
      const btn = document.getElementById('help-toggle');
      if (btn) { e.preventDefault(); btn.click(); }
      return;
    }

    // / — focus first search input on the page if any
    if (e.key === '/') {
      const input = document.getElementById('peer-search') ||
                    document.querySelector('input[type="search"]');
      if (input) {
        e.preventDefault();
        input.focus();
        input.select && input.select();
      }
      return;
    }

    // n — new peer (only on peers list / dashboard)
    if (e.key === 'n' || e.key === 'N') {
      const path = window.location.pathname;
      if (path === '/' || path.startsWith('/peers')) {
        e.preventDefault();
        window.location.href = '/peers/wizard';
      }
      return;
    }
  });
})();

// ── Command palette (Cmd/Ctrl+K) ───────────────────────────────────────
(function () {
  let overlay = null;
  let listEl  = null;
  let inputEl = null;
  let activeIdx = 0;
  let visibleItems = [];

  // Static command catalogue. Could be extended to include peer names from
  // the page once we have a peer list endpoint with cheap queries.
  const COMMANDS = [
    { id: 'goto-dashboard', label: 'Go to Dashboard',     icon: '⌂', url: '/',                  hint: 'g d' },
    { id: 'goto-peers',     label: 'Go to All Peers',     icon: '◈', url: '/peers/',            hint: 'g p' },
    { id: 'goto-add',       label: 'Add Peer',            icon: '+', url: '/peers/wizard',      hint: 'n'   },
    { id: 'goto-map',       label: 'Open Map',            icon: '🌍', url: '/map',              hint: 'g m' },
    { id: 'goto-topology',  label: 'Open Topology',       icon: '⌬', url: '/topology',         hint: 'g t' },
    { id: 'goto-alerts',    label: 'Open Alerts',         icon: '⚑', url: '/alerts',           hint: 'g a' },
    { id: 'goto-history',   label: 'Connection History',  icon: '◷', url: '/history',          hint: 'g y' },
    { id: 'goto-logs',      label: 'System Logs',         icon: '≡', url: '/logs',             hint: 'g l' },
    { id: 'goto-notif',     label: 'Notifications',       icon: '🔔', url: '/notifications',   hint: 'g n' },
    { id: 'goto-portfwd',   label: 'Port Forwards',       icon: '⇄', url: '/port-forwards/',   hint: 'g f' },
    { id: 'goto-settings',  label: 'Settings',            icon: '⚙', url: '/settings/',        hint: 'g s' },
    { id: 'goto-about',     label: 'About / Changelog',   icon: 'ℹ', url: '/about',            hint: ''    },
    { id: 'theme-toggle',   label: 'Toggle Theme',        icon: '☼', action: () => document.getElementById('theme-toggle')?.click(), hint: '' },
    { id: 'help',           label: 'Show Help',           icon: '?', action: () => document.getElementById('help-toggle')?.click(), hint: '?' },
    { id: 'logout',         label: 'Sign Out',            icon: '⎋', url: '/logout',           hint: ''    },
  ];

  function ensureOverlay() {
    if (overlay) return;
    overlay = document.createElement('div');
    overlay.className = 'cmd-palette-overlay';
    overlay.style.display = 'none';
    overlay.innerHTML =
      '<div class="cmd-palette" role="dialog" aria-modal="true" aria-label="Command palette">' +
        '<input class="cmd-palette-input" type="text" placeholder="Type a command or page…" autocomplete="off" spellcheck="false">' +
        '<ul class="cmd-palette-list" role="listbox"></ul>' +
      '</div>';
    document.body.appendChild(overlay);

    inputEl = overlay.querySelector('.cmd-palette-input');
    listEl  = overlay.querySelector('.cmd-palette-list');

    inputEl.addEventListener('input', renderList);
    inputEl.addEventListener('keydown', onInputKey);
    overlay.addEventListener('click', e => { if (e.target === overlay) hide(); });
  }

  function renderList() {
    const q = (inputEl.value || '').trim().toLowerCase();
    visibleItems = !q ? COMMANDS.slice() :
      COMMANDS.filter(c => c.label.toLowerCase().includes(q));
    activeIdx = 0;
    if (visibleItems.length === 0) {
      listEl.innerHTML = '<li class="cmd-palette-empty">No matches</li>';
      return;
    }
    listEl.innerHTML = visibleItems.map((c, i) =>
      '<li class="cmd-palette-item' + (i === activeIdx ? ' active' : '') + '" data-idx="' + i + '">' +
        '<span class="cmd-palette-icon">' + c.icon + '</span>' +
        '<span class="cmd-palette-label">' + c.label + '</span>' +
        (c.hint ? '<span class="cmd-palette-hint">' + c.hint + '</span>' : '') +
      '</li>'
    ).join('');
    Array.from(listEl.children).forEach(li => {
      li.addEventListener('click', () => execItem(parseInt(li.dataset.idx, 10)));
      li.addEventListener('mousemove', () => setActive(parseInt(li.dataset.idx, 10)));
    });
  }

  function setActive(i) {
    if (i < 0 || i >= visibleItems.length) return;
    activeIdx = i;
    Array.from(listEl.children).forEach((li, idx) => {
      li.classList.toggle('active', idx === i);
    });
  }

  function execItem(i) {
    const c = visibleItems[i];
    if (!c) return;
    hide();
    if (c.action) c.action();
    else if (c.url) window.location.href = c.url;
  }

  function onInputKey(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(Math.min(visibleItems.length - 1, activeIdx + 1)); scrollActive(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(0, activeIdx - 1)); scrollActive(); }
    else if (e.key === 'Enter') { e.preventDefault(); execItem(activeIdx); }
    else if (e.key === 'Escape') { e.preventDefault(); hide(); }
  }

  function scrollActive() {
    const li = listEl.children[activeIdx];
    if (li && li.scrollIntoView) li.scrollIntoView({ block: 'nearest' });
  }

  function show() {
    ensureOverlay();
    overlay.style.display = 'flex';
    inputEl.value = '';
    renderList();
    setTimeout(() => inputEl.focus(), 30);
  }

  function hide() {
    if (!overlay) return;
    overlay.style.display = 'none';
  }

  window.tvCmdPalette = {
    show, hide,
    toggle() { if (overlay && overlay.style.display === 'flex') hide(); else show(); },
  };
})();

// ── Generic data-attribute action handlers ─────────────────────────
// Replaces inline onclick="..." handlers so the CSP can drop
// 'unsafe-inline' from script-src. Delegated from document so it
// works on elements injected after page-load too.
(function () {
  function openModal(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = 'flex';
  }
  function closeModal(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = 'none';
  }
  function copyFromTarget(target, btn) {
    var el = document.getElementById(target);
    if (!el) return;
    var text = (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')
      ? el.value : el.textContent;
    copyText(text, btn);
  }
  function togglePasswordReveal(target, btn) {
    var el = document.getElementById(target);
    if (!el) return;
    var revealed = el.type === 'text';
    el.type = revealed ? 'password' : 'text';
    btn.textContent = revealed ? 'Show' : 'Hide';
  }

  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || t.nodeType !== 1) return;

    var openId = t.getAttribute('data-modal-open');
    if (openId) { e.preventDefault(); openModal(openId); return; }

    var closeId = t.getAttribute('data-modal-close');
    if (closeId) { e.preventDefault(); closeModal(closeId); return; }

    var copyTarget = t.getAttribute('data-copy-target');
    if (copyTarget) { e.preventDefault(); copyFromTarget(copyTarget, t); return; }

    var copyText2 = t.getAttribute('data-copy-text');
    if (copyText2 !== null) { e.preventDefault(); copyText(copyText2, t); return; }

    var reveal = t.getAttribute('data-toggle-password');
    if (reveal) { e.preventDefault(); togglePasswordReveal(reveal, t); return; }

    if (t.hasAttribute('data-reload-page')) {
      e.preventDefault(); location.reload(); return;
    }
  });
})();

// ── Browser push notifications + sound hook ───────────────────────
(function () {
  // Only run on authenticated pages (sidebar present)
  if (!document.querySelector('aside.sidebar')) return;
  if (!('Notification' in window)) return;

  const PERM_ASKED_KEY = 'traverse-notify-asked';
  const DEVICE_ICONS   = {phone:'📱', laptop:'💻', desktop:'🖥️', tablet:'📟', router:'🖧', other:'◈'};

  // Ask permission once, after a small delay to feel less intrusive
  function maybeAskPermission() {
    if (Notification.permission !== 'default') return;
    if (localStorage.getItem(PERM_ASKED_KEY)) return;
    setTimeout(() => {
      if (Notification.permission !== 'default') return;
      Notification.requestPermission().finally(() => {
        localStorage.setItem(PERM_ASKED_KEY, '1');
      });
    }, 4000);
  }
  maybeAskPermission();

  let lastEventId = null;

  function fire(ev) {
    const icon = DEVICE_ICONS[ev.peer_device] || '◈';
    const verb = ev.event_type === 'connected' ? 'connected' : 'disconnected';
    const body = `${icon} ${ev.peer_name} ${verb}`;
    if (Notification.permission === 'granted') {
      try {
        new Notification('traverse', {
          body: body,
          icon: '/static/img/app.png',
          tag:  'traverse-evt-' + ev.id,
        });
      } catch (e) { /* swallow — some browsers throw on rapid spam */ }
    }
    // Sound (only if F7 toggle enabled and notif permission granted)
    if (window.traverseSound && typeof window.traverseSound.play === 'function') {
      window.traverseSound.play(ev.event_type);
    }
  }

  function poll() {
    fetch('/api/events/latest', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d || !Array.isArray(d.events)) return;
        const events = d.events;
        if (lastEventId === null) {
          // First poll — set baseline; don't replay history as fresh notifications
          lastEventId = events.length > 0 ? events[0].id : 0;
          return;
        }
        const fresh = events.filter(e => e.id > lastEventId);
        if (fresh.length === 0) return;
        // events come newest-first; fire in chronological order
        fresh.slice().reverse().forEach(fire);
        lastEventId = events[0].id;
      })
      .catch(() => {});
  }
  poll();
  setInterval(poll, 60000);
})();

// ── Stat-value count-up animation ──────────────────────────────────────
// Targets `.stat-card-value`, `.noc-val`, and `[data-tv-count]` whose
// textContent parses as an integer (peer counts, etc). Ticks from 0 to
// the final value over ~700ms on first paint. Skipped under reduced
// motion or when the value is non-numeric (e.g. "10.8.0.0/24", "● RUN").
(function () {
  if (typeof window === 'undefined') return;
  const reduceMotion = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduceMotion) return;

  function isIntegerText(s) {
    if (s == null) return false;
    const t = String(s).trim();
    return /^-?\d+$/.test(t);
  }

  function animate(el, target) {
    const start = performance.now();
    // Magnitude-aware duration: small counts settle fast (~420ms for single
    // digits), large ones feel deliberate, capped at 1100ms for 6-figure values.
    const mag = Math.log10(Math.max(2, Math.abs(target)));
    const dur = Math.min(1100, Math.max(350, 350 + mag * 220));
    function tick(now) {
      const t = Math.min(1, (now - start) / dur);
      // ease-out cubic
      const e = 1 - Math.pow(1 - t, 3);
      const v = Math.round(target * e);
      el.textContent = String(v);
      if (t < 1) requestAnimationFrame(tick);
      else el.textContent = String(target);
    }
    requestAnimationFrame(tick);
  }

  document.addEventListener('DOMContentLoaded', () => {
    const nodes = document.querySelectorAll(
      '.stat-card-value, .noc-val, [data-tv-count]'
    );
    nodes.forEach(el => {
      if (el.dataset.tvCounted === '1') return;
      const txt = el.textContent;
      if (!isIntegerText(txt)) return;
      const n = parseInt(txt, 10);
      // Skip trivially-small numbers — animating "0 → 1" is silly.
      if (!Number.isFinite(n) || Math.abs(n) < 2) return;
      el.dataset.tvCounted = '1';
      animate(el, n);
    });
  });
})();

// ── Cursor-aware card sheen ────────────────────────────────────────────
// Sets `--mx` / `--my` CSS custom properties on .stat-card (and any
// .card.tv-sheen / .noc-panel.tv-sheen) so the ::after radial gradient
// follows the pointer. Pure aesthetic; no-op on touch devices.
(function () {
  if (typeof window === 'undefined') return;
  const COARSE = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
  if (COARSE) return;

  function bind(el) {
    if (el._tvSheen) return;
    el._tvSheen = true;
    el.addEventListener('pointermove', (ev) => {
      const r = el.getBoundingClientRect();
      const x = ((ev.clientX - r.left) / r.width) * 100;
      const y = ((ev.clientY - r.top) / r.height) * 100;
      el.style.setProperty('--mx', x.toFixed(1) + '%');
      el.style.setProperty('--my', y.toFixed(1) + '%');
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.stat-card, .card.tv-sheen, .noc-panel.tv-sheen').forEach(bind);
  });
})();
