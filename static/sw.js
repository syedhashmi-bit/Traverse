// traverse service worker — precaches the app shell, network-first for HTML,
// cache-then-network for static assets, falls back to /offline when the
// network is unreachable. API calls (`/api/*`) are never cached.

const CACHE_NAME = 'traverse-v5';

// Real app shell. NOTE: do NOT include '/' here — for unauthenticated visits
// it 302→/login, and authenticated state is per-session, so precaching it
// would either store a redirect or capture the login page. Runtime caching
// handles the document on first hit.
const STATIC_ASSETS = [
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/img/app.png',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png',
  '/offline'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // Only handle same-origin requests. Intercepting cross-origin fetches
  // (e.g. CARTO map tiles) forces them through fetch(req), which returns
  // an opaque response — recent Chrome versions can fail to paint these
  // into <img> tags, breaking the map. Let the browser handle them natively.
  if (url.origin !== self.location.origin) return;

  // Never cache API calls — they must always be fresh.
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith(
    fetch(req)
      .then(response => {
        // Opportunistically cache successful static assets for offline use.
        if (response.ok && url.pathname.startsWith('/static/')) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(req, clone));
        }
        return response;
      })
      .catch(() =>
        caches.match(req).then(cached => cached || caches.match('/offline'))
      )
  );
});

// Push notifications (server-side wiring is future work; the handler is in
// place so a future VAPID push from the alerts pipeline just works).
self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) {}
  const title = data.title || 'traverse';
  const options = {
    body:  data.body  || '',
    icon:  '/static/icons/icon-192x192.png',
    badge: '/static/icons/icon-72x72.png',
    tag:   data.tag   || 'traverse-notification',
    data:  { url: data.url || '/' }
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(clients.openWindow(url));
});
