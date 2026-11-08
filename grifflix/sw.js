// Grifflix's service worker: its notifications (the Movie of the Day, Monday's summary), apart from the songs' (its own
// scope, /grifflix/, and its own devices list on the Worker); and the films' pictures, kept on the device once fetched
// so they show at once (John, 8 Nov 2026: "can the pictures be held so that viewers can see them quickly in the app").
// The page's Refresh empties the picture store. Films themselves stream, never kept.
const PICS = 'gfx-pics-v1', MAX_PICS = 4000;
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if ((url.hostname === 'media.grifflix.co.uk' && url.pathname.startsWith('/pimg/')) ||
      (url.hostname === 'sotd-playlists.cooperking.workers.dev' && url.pathname.startsWith('/gp/'))) e.respondWith(pic(url.href));   // (the site's own copies, from John's Update)
});

async function pic(href) {   // a picture's address never changes (its film, its kind, its size): from here once fetched
  const c = await caches.open(PICS);
  const hit = await c.match(href);
  if (hit) return hit;
  const r = await fetch(href, { mode: 'cors', credentials: 'omit' });
  if (r.ok) {
    await c.put(href, r.clone()).catch(() => {});
    if (Math.random() < .02) trim(c);
  }
  return r;
}

async function trim(c) {   // (the oldest go once there are more than MAX_PICS)
  const keys = await c.keys();
  for (const k of keys.slice(0, Math.max(0, keys.length - MAX_PICS))) await c.delete(k);
}

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
