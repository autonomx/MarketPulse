/*
 * Omnix alert notifications (TVP-0.5c): shows a web push from the Omnix server as an operating-system notification,
 * whether or not Omnix is open, and brings Omnix forward when the notification is clicked. The push carries only the
 * alert's title, message and link, encrypted for this browser.
 */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : '' };
  }
  const title = typeof data.title === 'string' && data.title ? data.title : 'Omnix alert';
  event.waitUntil(self.registration.showNotification(title, {
    body: typeof data.body === 'string' ? data.body : '',
    icon: '/icons/omnix-trading-192.png',
    badge: '/icons/omnix-trading-192.png',
    tag: data.trigger_id ? `omnix-alert-${data.trigger_id}` : undefined,
    data: { url: typeof data.url === 'string' && data.url.startsWith('/') ? data.url : '/trading' },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL(event.notification.data && event.notification.data.url ? event.notification.data.url : '/trading', self.location.origin);
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
    const open = windows.find((client) => new URL(client.url).pathname.startsWith('/trading')) || windows[0];
    if (open) {
      open.postMessage({ type: 'omnix-alert-notification', url: target.pathname + target.search });
      return open.focus();
    }
    return self.clients.openWindow(target.href);
  }));
});
