// TRIPY service worker: push notifications + offline app shell.
// Trip data itself is already kept on the device (localStorage) by the page,
// so caching the page and its static assets is enough to open TRIPY abroad
// without a data connection. API calls are never cached here.
const SHELL_CACHE = 'tripy-shell-v1';
const SHELL_URLS = ['/', '/manifest.webmanifest', '/tripy-icon.png', '/assets/rain-window.jpg'];
const NAVIGATION_TIMEOUT_MS = 4000;

self.addEventListener('install', event => {
  event.waitUntil(caches.open(SHELL_CACHE).then(cache => cache.addAll(SHELL_URLS)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(key => key.startsWith('tripy-shell-') && key !== SHELL_CACHE).map(key => caches.delete(key))))
    .then(() => self.clients.claim()));
});

function networkWithTimeout(request, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), timeoutMs);
    fetch(request).then(response => { clearTimeout(timer); resolve(response); }, error => { clearTimeout(timer); reject(error); });
  });
}

async function pageResponse(request) {
  const cache = await caches.open(SHELL_CACHE);
  const network = fetch(request).then(response => {
    if (response.ok) cache.put('/', response.clone());
    return response;
  });
  try {
    return await Promise.race([network, new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), NAVIGATION_TIMEOUT_MS))]);
  } catch (_) {
    const cached = await cache.match('/');
    if (cached) { network.catch(() => {}); return cached; }
    return network; // nothing cached yet: wait for the network after all
  }
}

async function assetResponse(request) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await networkWithTimeout(request, 15000);
  if (response.ok) cache.put(request, response.clone());
  return response;
}

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  if (request.mode === 'navigate' && url.pathname === '/') {
    event.respondWith(pageResponse(request));
  } else if (/^\/(assets\/.+|tripy-icon\.png|manifest\.webmanifest)$/.test(url.pathname)) {
    event.respondWith(assetResponse(request));
  }
});

self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { data = { body: event.data ? event.data.text() : '' }; }
  const title = data.title || 'TRIPY';
  const options = {
    body: data.body || 'התקבל עדכון חדש',
    icon: '/tripy-icon.png',
    badge: '/tripy-icon.png',
    tag: data.tag || 'tripy-booking',
    data: { url: data.url || '/' },
    vibrate: [100, 50, 100]
  };
  event.waitUntil(self.registration.showNotification(title, options));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url || '/';
  event.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    for (const client of list) if ('focus' in client) { client.navigate(url); return client.focus(); }
    return clients.openWindow(url);
  }));
});
