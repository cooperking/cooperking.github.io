// Song of the Day: the calendar with no connection, and members' downloaded songs.
// The page registers this from its own folder (songs/ or songs/draft/), so it looks after that folder's page. It keeps:
//   sotd-shell   the page, its data, the playlist and the fonts: the network first, the last copy when there's none
//   sotd-covers  album covers: once fetched, from here (a cover's address never changes)
//   sotd-songs   members' downloaded songs, put there by the page at <folder>__offline/<track>.mp3; served from here,
//                in ranges as phones ask for them (an iPhone won't play a song otherwise)
const SHELL = 'sotd-shell-v1', COVERS = 'sotd-covers-v1', SONGS = 'sotd-songs', BADGE = 'sotd-badge';
const KEEP = [SHELL, COVERS, SONGS, BADGE, 'sotd-art', 'sotd-shared'];   // (sotd-art: the page's drawn playlist covers)

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
  if (req.method === 'POST' && new URL(req.url).pathname === new URL(self.registration.scope).pathname + 'share-target') { e.respondWith(shared(req)); return; }
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin === location.origin && url.pathname.includes('/__offline/')) { e.respondWith(song(req, url)); return; }
  if (url.hostname === 'i.scdn.co' || (/\.workers\.dev$/.test(url.hostname) && url.pathname.startsWith('/sp/c/'))) { e.respondWith(cover(req)); return; }   // (covers: Spotify's, or the copies the Worker keeps)
  const home = new URL(self.registration.scope).pathname;
  if (req.mode === 'navigate' && url.origin === location.origin && (url.pathname === home || url.pathname === home + 'index.html')) {
    e.respondWith(fresh(req, new Request(self.registration.scope), 1500)); return;   // the calendar itself (not the day pages or others here): its kept copy if the network is slow (John, 7 Nov 2026: the iPhone app reloading to a blank screen)
  }
  const data = (url.origin === location.origin && /\.(json|webmanifest)$/.test(url.pathname))
    || (/\.workers\.dev$/.test(url.hostname) && url.pathname === '/songs.json')
    || url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com';
  if (data) e.respondWith(fresh(req, new Request(url.origin === location.origin ? url.origin + url.pathname : url.href)));   // the site's own data whatever's after its address
});

// the network first; the last copy kept when there's no connection (or the network fails); with wait, the kept copy
// as soon as the network's taken that long (the fresh one still kept, for next time and the page's Refresh)
async function fresh(req, key, wait) {
  const c = await caches.open(SHELL);
  const net = fetch(req).then(r => { if (r.ok || r.type === 'opaque') c.put(key, r.clone()).catch(() => {}); return r; });
  if (wait) {
    const hit = await c.match(key);
    if (hit) {
      const late = new Promise(res => setTimeout(() => res(null), wait));
      const first = await Promise.race([net.catch(() => null), late]);
      if (first) return first;
      net.catch(() => {});   // (still kept when it comes)
      return hit;
    }
  }
  try {
    return await net;
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
  e.waitUntil(Promise.all([typeof m.unread === 'number' ? badge(null, m.unread) : badge(1), self.registration.showNotification(m.title || 'Song of the Day', {
    body: m.body || '', icon: m.icon || scope + 'icon-192.png', image: m.image || undefined,   // image: Android's big picture (an iPhone shows none)
    tag: m.tag || 'sotd', data: { url: m.url || scope, ib: m.ib || '' }
  })]));
});

// the number on the app's icon (an iPhone's Home Screen app, and installed apps on a computer): the member's unread
// messages in their Profile's inbox, as the Worker counts them with each notification; the page sets it again as they're read
async function badge(add, to) {   // add one, or (to) the member's unread count as the Worker gives it
  try {
    const c = await caches.open(BADGE), r = await c.match('n');
    const n = typeof to === 'number' ? Math.max(0, to) : add ? (r ? Number(await r.text()) || 0 : 0) + add : 0;
    if (n) await c.put('n', new Response(String(n))); else await c.delete('n');
    if (self.navigator.setAppBadge) await (n ? self.navigator.setAppBadge(n) : self.navigator.clearAppBadge()).catch(() => {});
  } catch (err) {}
}

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || self.registration.scope, ib = e.notification.data && e.notification.data.ib;
  if (ib) e.waitUntil(readLater(ib));
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

// a tapped notification counts as read: noted here for the page, which tells the Worker (it has the member's sign-in)
async function readLater(id) {
  try {
    const c = await caches.open(BADGE), r = await c.match('read'), list = r ? JSON.parse(await r.text()) : [];
    if (!list.includes(id)) list.push(id);
    await c.put('read', new Response(JSON.stringify(list.slice(-50))));
  } catch (err) {}
}

// A file shared to the app (Android's share sheet: the manifest's share_target): an Exportify .zip or .csv, kept for the
// page, which then opens with ?shared=1 and takes it into Import from Spotify.
async function shared(req) {
  try {
    const f = (await req.formData()).get('file');
    if (f && f.size) {
      const c = await caches.open('sotd-shared');
      await c.put('/songs/shared-file', new Response(f, { headers: { 'Content-Type': f.type || 'application/octet-stream', 'X-Name': encodeURIComponent(f.name || 'playlists.zip') } }));
    }
  } catch (x) {}
  return Response.redirect(self.registration.scope + '?shared=1', 303);
}
