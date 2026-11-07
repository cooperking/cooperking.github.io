// Grifflix's service worker: its notifications (the Movie of the Day, Monday's summary), apart from Song of the Day's
// (its own scope, /grifflix/, and its own devices list on the Worker). Nothing kept offline (films stream).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', e => {
  let m = {};
  try { m = e.data ? e.data.json() : {}; } catch (err) { m = { title: 'Grifflix', body: e.data ? e.data.text() : '' }; }
  const scope = self.registration.scope;
  e.waitUntil(self.registration.showNotification(m.title || 'Grifflix', {
    body: m.body || '', icon: m.icon || scope + 'icon-192.png', image: m.image || undefined, badge: scope + 'favicon-96.png',
    tag: m.tag || 'grifflix', data: { url: m.url || scope }
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || self.registration.scope;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const here = wins.find(w => w.url.startsWith(self.registration.scope));
    if (here) {
      await here.focus().catch(() => {});
      if (here.url !== url) { try { await here.navigate(url); } catch (err) { here.postMessage({ grifflixOpen: url }); } }
      return;
    }
    await self.clients.openWindow(url);
  })());
});
