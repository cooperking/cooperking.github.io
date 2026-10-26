// Song of the Day: the calendar with no connection, and members' downloaded songs.
// The page registers this from its own folder (songs/ or songs/draft/), so it looks after that folder's page. It keeps:
//   sotd-shell   the page, its data, the playlist and the fonts: the network first, the last copy when there's none
//   sotd-covers  album covers: once fetched, from here (a cover's address never changes)
//   sotd-songs   members' downloaded songs, put there by the page at <folder>__offline/<track>.mp3; served from here,
//                in ranges as phones ask for them (an iPhone won't play a song otherwise)
const SHELL = 'sotd-shell-v1', COVERS = 'sotd-covers-v1', SONGS = 'sotd-songs';
const KEEP = [SHELL, COVERS, SONGS];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL).then(c => c.add(new Request(self.registration.scope, { cache: 'reload' }))).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => /^sotd-/.test(k) && !KEEP.includes(k)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin === location.origin && url.pathname.includes('/__offline/')) { e.respondWith(song(req, url)); return; }
  if (url.hostname === 'i.scdn.co') { e.respondWith(cover(req)); return; }
  const home = new URL(self.registration.scope).pathname;
  if (req.mode === 'navigate' && url.origin === location.origin && (url.pathname === home || url.pathname === home + 'index.html')) {
    e.respondWith(fresh(req, new Request(self.registration.scope))); return;   // the calendar itself (not the day pages or others here)
  }
  const data = (url.origin === location.origin && /\.(json|webmanifest)$/.test(url.pathname))
    || (/\.workers\.dev$/.test(url.hostname) && url.pathname === '/songs.json')
    || url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com';
  if (data) e.respondWith(fresh(req, new Request(url.origin === location.origin ? url.origin + url.pathname : url.href)));   // the site's own data whatever's after its address
});

// the network first; the last copy kept when there's no connection (or the network fails)
async function fresh(req, key) {
  const c = await caches.open(SHELL);
  try {
    const r = await fetch(req);
    if (r.ok || r.type === 'opaque') c.put(key, r.clone()).catch(() => {});
    return r;
  } catch (err) {
    const hit = await c.match(key);
    if (hit) return hit;
    throw err;
  }
}

async function cover(req) {
  const c = await caches.open(COVERS);
  const hit = await c.match(req.url);
  if (hit) return hit;
  try {
    const r = await fetch(req.url, { mode: 'cors' });   // Spotify's covers allow it: kept whole, not as an opaque copy
    if (r.ok) c.put(req.url, r.clone()).catch(() => {});
    return r;
  } catch (err) {
    return fetch(req);
  }
}

async function song(req, url) {
  const c = await caches.open(SONGS);
  const hit = await c.match(url.origin + url.pathname);
  if (!hit) return new Response('Not downloaded.', { status: 404 });
  const blob = await hit.blob(), size = blob.size, type = hit.headers.get('Content-Type') || 'audio/mpeg';
  const h = { 'Content-Type': type, 'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store' };
  const m = /^bytes=(\d*)-(\d*)$/.exec(req.headers.get('Range') || '');
  if (!m || (!m[1] && !m[2])) return new Response(blob, { status: 200, headers: Object.assign({ 'Content-Length': String(size) }, h) });
  let start, end;
  if (m[1]) { start = +m[1]; end = m[2] ? Math.min(+m[2], size - 1) : size - 1; } else { start = Math.max(0, size - +m[2]); end = size - 1; }
  if (start >= size || start > end) return new Response(null, { status: 416, headers: Object.assign({ 'Content-Range': 'bytes */' + size }, h) });
  return new Response(blob.slice(start, end + 1, type), { status: 206,
    headers: Object.assign({ 'Content-Length': String(end - start + 1), 'Content-Range': 'bytes ' + start + '-' + end + '/' + size }, h) });
}

// ---- notifications (members who've turned them on): each new Song of the Day, and John's own messages. The Worker
// sends { title, body, icon, url, tag }; a tap opens the calendar at that day (or the page given), in the window
// already open if there is one ----
self.addEventListener('push', e => {
  let m = {};
  try { m = e.data ? e.data.json() : {}; } catch (err) { m = { title: 'Song of the Day', body: e.data ? e.data.text() : '' }; }
  const scope = self.registration.scope;
  e.waitUntil(self.registration.showNotification(m.title || 'Song of the Day', {
    body: m.body || '', icon: m.icon || scope + 'icon-192.png',
    tag: m.tag || 'sotd', data: { url: m.url || scope }
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || self.registration.scope;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const here = wins.find(w => w.url.split('#')[0] === url.split('#')[0]) || wins.find(w => w.url.startsWith(self.registration.scope));
    if (here) {
      await here.focus().catch(() => {});
      if (here.url !== url) { try { await here.navigate(url); } catch (err) { here.postMessage({ sotdOpen: url }); } }
      return;
    }
    await self.clients.openWindow(url);
  })());
});
