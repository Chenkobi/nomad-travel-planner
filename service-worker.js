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
