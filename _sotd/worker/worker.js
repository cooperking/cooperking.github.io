// Song of the Day — playlist maker (Cloudflare Worker)
//
// Visitors open  /p?t=<trackId>,<trackId>,...&n=<name>  (sent by the calendar's "Open in Spotify" button).
// /songs.json gives the website calendar the playlist as it is now (covers and 30-second previews included).
// The Worker checks every track is on the Song of the Day playlist, creates a playlist with them in that
// order on John's Spotify account, and redirects the visitor to it on open.spotify.com.
//
// No timers: everything follows from someone opening the calendar. When the Worker reads the playlist for
// /songs.json (at most every 10 minutes) and finds it changed, it starts the GitHub job (song-tags.yml) that
// finds each new song's video, year and moods, keeps the YouTube playlist in step and makes the share pages.
// Until a run has included the song for today (London time), every change to the playlist starts a run, so a
// calendar that has fallen behind catches up; once one has, the Worker doesn't look again until tomorrow.
// The same visit gives new songs a mood (ReccoBeats) and genres (MusicBrainz) until the job's own are out,
// and once a day removes the playlists made for visitors from John's library once they are KEEP_DAYS old.
// Removing one makes its link stop working, but a visitor already playing it carries on to the next songs
// (tested 23 Oct 2026) unless their Spotify app restarts; a day covers one listen.
//
// Only John ever signs in to Spotify (via /login once, then again every 6 months when Spotify expires the
// login), so the Development Mode limit of 5 users does not apply to visitors.
//
// Song facts: John signs each day off on songs/facts-admin.html (its facts, moods, genres and video), which posts it
// to /facts/signoff with his admin password; the Worker keeps it (KV key factsSigned) and starts the GitHub job
// facts.yml, which puts it on the site. /facts/signed gives them back to that job.
//
// Members (John and his family): John's own recordings of the songs and their videos, kept private in R2 and played
// on the calendar instead of the preview and YouTube by anyone signed in (see the members section below).
//
// Needs one KV namespace bound as SOTD, an R2 bucket bound as MEDIA (for the members' files; without it the members
// part says it's not set up), and two secrets: GH_TOKEN, a fine-grained GitHub token for
// cooperking/cooperking.github.io only, with Actions: Read and write; and ADMIN_KEY, the password John types into
// the facts sign-off page and the members page. Everything else is set below.

const CLIENT_ID = '21a0fa3c6c09482ea57271dbdbef1c1f';   // Spotify app "Song of the Day" (public identifier)
const OWNER_ID = 'cooperking';                          // only this Spotify account may link the Worker
const SOURCE_PLAYLIST = '4zyegiBCapCQm8VWYYHhGo';         // John's Song of the Day playlist
const MAKE_PUBLIC = false;                              // private: opens from its link, kept off John's profile
const KEEP_DAYS = 1;                                    // clean-up removes playlists older than this
const DEFAULT_NAME = 'Song of the Day picks';
const MAX_NAME = 80;
const MAX_TRACKS = 300;
const PER_IP_PER_HOUR = 10;                             // new playlists per visitor per hour
const SCOPES = 'playlist-modify-public playlist-modify-private playlist-read-private';
const API = 'https://api.spotify.com/v1';
const ID_RE = /^[0-9A-Za-z]{22}$/;
const DAY = 864e5;
const REPO = 'cooperking/cooperking.github.io';         // the website, and the job that runs after a change
const JOB = 'song-tags.yml';
const FACTS_JOB = 'facts.yml';                          // puts signed-off facts on the site
const ADMIN_ORIGIN = 'https://cooperking.github.io';     // the sign-off page's address
const FACTS_MAX = { items: 400, facts: 12, text: 400 };
const MOOD_LIST = ['Joyful', 'Uplifting', 'Energetic', 'Playful', 'Mellow', 'Dreamy', 'Romantic', 'Bittersweet', 'Melancholy', 'Dark'];
const GENRE_LIST = ['Soul & R&B', 'Rock', 'Indie', 'Folk', 'Pop', 'Electronic', 'Hip-hop', 'Country', 'World'];

class HttpError extends Error {
  constructor(status, message, extra) { super(message); this.status = status; this.extra = extra || {}; }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    try {
      if (url.pathname === '/songs.json') return await songsJson(request, env, ctx);
      if (url.pathname === '/login') return await login(url);
      if (url.pathname === '/callback') return await callback(request, url, env);
      if (url.pathname === '/p') return await makePlaylist(request, url, env);
      if (url.pathname === '/status') return await status(env);
      if (url.pathname === '/facts/signed') return await factsSigned(env);
      if (url.pathname === '/facts/signoff') return await factsSignoff(request, env);
      if (url.pathname.startsWith('/members/')) return await members(request, url, env);
      if (url.pathname.startsWith('/m/')) return await mediaFile(request, url, env);
      if (url.pathname === '/media/report') return await mediaReport(url, env);
      if (url.pathname === '/app.webmanifest') return appManifest(url);
      if (url.pathname.startsWith('/i/')) return await invitePage(url, env);
      if (url.pathname === '/covers.json' || url.pathname.startsWith('/c/') || url.pathname.startsWith('/s/')) return await ownCover(request, url, env);
      return page('Song of the Day', '<p>This service makes Spotify playlists from the Song of the Day calendar.</p>');
    } catch (e) {
      if (e instanceof HttpError) return errorPage(e);
      return page('Something went wrong', '<p>The playlist could not be made. Please try again later.</p><p class="small">' + esc(e.message || String(e)) + '</p>', 500);
    }
  },
  // every 10 minutes (the Worker's cron, set by worker_deploy.py): a new song on the playlist today goes out as a
  // notification to the members who've turned them on (see the notifications section)
  async scheduled(event, env, ctx) {
    ctx.waitUntil((async () => {
      const res = await songsJson(new Request(LIVE_ORIGIN + '/songs.json'), env, ctx);
      const j = await res.json();
      await pushDaily(env, (j.songs || []).map(x => ({ id: x.id, a: x.a, title: x.title, artist: x.artist, img: x.img })));
    })().catch(e => console.log('scheduled: ' + (e && e.message))));
  }
};

// ---------- linking John's Spotify account ----------

// The PKCE verifier waits in a short-lived cookie on this address (nothing is written to KV until
// Spotify confirms it is John signing in).
async function login(url) {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(48)));
  const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
  const state = b64url(crypto.getRandomValues(new Uint8Array(18)));
  const q = new URLSearchParams({
    client_id: CLIENT_ID, response_type: 'code', redirect_uri: url.origin + '/callback',
    code_challenge_method: 'S256', code_challenge: challenge, state, scope: SCOPES, show_dialog: 'true'
  });
  return new Response(null, { status: 302, headers: {
    Location: 'https://accounts.spotify.com/authorize?' + q,
    'Set-Cookie': 'pkce=' + state + '.' + verifier + '; Path=/callback; Max-Age=900; HttpOnly; Secure; SameSite=Lax',
    'Cache-Control': 'no-store'
  } });
}

async function callback(request, url, env) {
  const code = url.searchParams.get('code'), state = url.searchParams.get('state');
  if (url.searchParams.get('error')) throw new HttpError(400, 'Spotify sign-in was cancelled (' + url.searchParams.get('error') + '). Start again at /login.');
  if (!code || !state) throw new HttpError(400, 'Spotify did not send a sign-in code. Start again at /login.');
  const m = (request.headers.get('Cookie') || '').match(/(?:^|;\s*)pkce=([\w-]+)\.([\w-]+)/);
  if (!m || m[1] !== state) throw new HttpError(400, 'This sign-in link has expired. Start again at /login.');
  const verifier = m[2];
  const r = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', code, redirect_uri: url.origin + '/callback', client_id: CLIENT_ID, code_verifier: verifier })
  });
  const j = await r.json();
  if (!r.ok) throw new HttpError(400, 'Spotify refused the sign-in: ' + (j.error_description || j.error || r.status));
  const me = await (await fetch(API + '/me', { headers: { Authorization: 'Bearer ' + j.access_token } })).json();
  if (!me || me.id !== OWNER_ID) throw new HttpError(403, 'Only the owner of the Song of the Day playlist can link this service.');
  await env.SOTD.put('auth', JSON.stringify({
    refresh_token: j.refresh_token, access_token: j.access_token, expires_at: Date.now() + j.expires_in * 1000,
    linked_at: new Date().toISOString(), owner: me.id
  }));
  await env.SOTD.delete('allowed');
  return page('Linked', '<p>Linked to Spotify as <b>' + esc(me.id) + '</b>. The calendar can now make playlists.</p><p class="small">Spotify asks for this again after 6 months: open /login on this address to renew it.</p>');
}

async function status(env) {
  const auth = await env.SOTD.get('auth', 'json');
  const last = await env.SOTD.get('cleanup', 'json');
  let html = auth
    ? '<p>Linked as <b>' + esc(auth.owner) + '</b> since ' + esc(auth.linked_at.slice(0, 10)) + '.</p><p>Renew by <b>' + new Date(new Date(auth.linked_at).getTime() + 180 * DAY).toISOString().slice(0, 10) + '</b> (open /login).</p>'
    : '<p>Not linked to Spotify yet. Open /login on this address.</p>';
  html += '<p class="small">Times are UK time.</p>';
  const runs = (last && last.runs) || [];
  html += runs.length
    ? '<h2>Clean-ups</h2><p>Newest first. Each removes the visitor playlists older than ' + (KEEP_DAYS === 1 ? 'a day' : KEEP_DAYS + ' days') + '.</p><ul>' + runs.map(r => '<li>' + uk(r.at) + ': removed ' + r.removed + ', ' + r.waiting + ' still to go' + (r.error ? '. Error: ' + esc(r.error) : '') + '</li>').join('') + '</ul>'
    : '<p>No clean-up has run yet.</p>';

  // the Worker's own records: the only playlists it will ever remove
  const waiting = [];
  let cursor;
  do {
    const lst = await env.SOTD.list({ prefix: 'pl:', cursor });
    for (const k of lst.keys) waiting.push(k.metadata || {});
    cursor = lst.list_complete ? null : lst.cursor;
  } while (cursor);
  waiting.sort((x, y) => (y.at || 0) - (x.at || 0));
  html += '<h2>Waiting to be removed</h2>' + (waiting.length
    ? '<p>Playlists the Worker made for visitors. Each goes at the first calendar visit after it is ' + (KEEP_DAYS === 1 ? 'a day' : KEEP_DAYS + ' days') + ' old.</p><ul>' + waiting.map(m => '<li>' + plLine(m.name, m.n, m.id) + ', made ' + (m.at ? uk(m.at) : 'at an unknown time') + '</li>').join('') + '</ul>'
    : '<p>None.</p>');

  const removed = (await env.SOTD.get('removed', 'json')) || [];
  html += '<h2>Removed</h2>' + (removed.length
    ? '<p>Newest first, the last ' + removed.length + '.</p><ul>' + removed.map(r => '<li>' + uk(r.at) + ': ' + plLine(r.name, r.n, null) + (r.made ? ', made ' + uk(r.made) : '') + (r.refused ? ' (Spotify said it was already gone)' : '') + '</li>').join('') + '</ul>'
    : '<p>None yet.</p>');

  const job = (await env.SOTD.get('job', 'json')) || {};
  html += '<h2>GitHub job</h2><p>' + (job.at ? 'Last started ' + uk(job.at) + ', when the playlist had ' + job.songs + ' songs.' : 'Not started from here yet.')
    + (!env.GH_TOKEN ? ' The GH_TOKEN secret is not set.' : job.err ? ' Last try failed: ' + esc(job.err) : '') + '</p>';
  return page(auth ? 'Status' : 'Not linked', html);
}
function uk(t) {   // a time as UK time, e.g. 23 Oct 13:19
  try { return esc(new Date(t).toLocaleString('en-GB', { timeZone: 'Europe/London', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).replace(',', '')); }
  catch (e) { return esc(String(t)); }
}
function plLine(name, n, id) {
  const label = name ? '“' + esc(name) + '”' : 'name not recorded';
  return (id ? '<a href="https://open.spotify.com/playlist/' + esc(id) + '">' + label + '</a>' : label) + (n ? ', ' + n + (n === 1 ? ' song' : ' songs') : '');
}

// A write to KV that mustn't stop the answer if it fails (on the free plan, after the day's 1,000 writes, every write
// fails until 00:00 UTC): the song list, a member's sign-in check and the like carry on without it.
async function kvSoft(env, key, value, opts) {
  try { await env.SOTD.put(key, value, opts); return true; } catch (e) { console.log('KV write skipped (' + key + '): ' + (e && e.message)); return false; }
}
// Spotify's sign-in, also kept here between requests, for when KV can't take the new one (see kvSoft): Spotify may
// replace the refresh token each time, and losing the newest would mean signing in again
let authMem = null;
async function getToken(env) {
  let auth = await env.SOTD.get('auth', 'json');
  if (authMem && (!auth || (authMem.expires_at || 0) > (auth.expires_at || 0))) auth = authMem;
  if (!auth) throw new HttpError(503, 'The playlist maker is not set up yet.');
  if (auth.access_token && auth.expires_at > Date.now() + 60000) return auth.access_token;
  const r = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'refresh_token', refresh_token: auth.refresh_token, client_id: CLIENT_ID })
  });
  const j = await r.json();
  if (!r.ok) {
    // Another request may have just refreshed (PKCE refresh tokens can change on every refresh).
    const again = await env.SOTD.get('auth', 'json');
    if (again && again.refresh_token !== auth.refresh_token && again.expires_at > Date.now() + 60000) return again.access_token;
    if (j.error === 'invalid_grant') throw new HttpError(503, 'The playlist maker needs its Spotify link renewed.');
    throw new Error('Spotify sign-in refresh failed: ' + (j.error_description || j.error || r.status));
  }
  const next = Object.assign({}, auth, {
    access_token: j.access_token, expires_at: Date.now() + j.expires_in * 1000,
    refresh_token: j.refresh_token || auth.refresh_token
  });
  authMem = next;
  await kvSoft(env, 'auth', JSON.stringify(next));
  return next.access_token;
}

async function spotify(token, path, method, body) {
  const r = await fetch(path.startsWith('http') ? path : API + path, {
    method: method || 'GET',
    headers: Object.assign({ Authorization: 'Bearer ' + token }, body ? { 'Content-Type': 'application/json' } : {}),
    body: body ? JSON.stringify(body) : undefined
  });
  if (r.status === 429) throw new HttpError(429, 'Spotify is busy right now.', { retryAfter: r.headers.get('Retry-After') });
  const j = r.status === 204 ? {} : await r.json().catch(() => ({}));
  if (!r.ok) throw new Error('Spotify ' + r.status + ': ' + ((j.error && (j.error.message || j.error)) || 'request failed'));
  return j;
}

// ---------- the playlist maker ----------

async function playlistItems(token) {
  const out = [];
  let next = '/playlists/' + SOURCE_PLAYLIST + '/items?limit=100';
  while (next) {
    const j = await spotify(token, next);
    for (const it of j.items || []) {
      const t = it.item || it.track;
      if (!t || !t.id) continue;
      const imgs = (t.album && t.album.images) || [];
      const img = imgs.find(i => i.width === 300) || imgs[1] || imgs[0];
      out.push({ a: it.added_at, id: t.id, title: t.name, artist: (t.artists || []).map(x => x.name).join(', '), artists: (t.artists || []).map(x => x.name),
        aids: (t.artists || []).map(x => x.id), album: t.album ? t.album.name : '', alid: t.album ? t.album.id : '', img: img ? img.url : '' });   // aids, alid: Spotify ids, for finding the song facts' sources
    }
    next = j.next;
  }
  return out;
}

async function allowedTracks(env, token, fresh) {
  const cached = fresh ? null : await env.SOTD.get('allowed', 'json');
  if (cached && cached.at > Date.now() - 6 * 3600e3) return new Set(cached.ids);
  const ids = (await playlistItems(token)).map(i => i.id);
  await kvSoft(env, 'allowed', JSON.stringify({ at: Date.now(), ids }));
  return new Set(ids);
}

function cleanName(s) {
  return Array.from(String(s || '').replace(/[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]/g, ' ').replace(/\s+/g, ' ').trim())
    .slice(0, MAX_NAME).join('').trim();
}

async function makePlaylist(request, url, env) {
  const raw = (url.searchParams.get('t') || '').split(',').map(s => s.trim()).filter(Boolean);
  if (!raw.length) throw new HttpError(400, 'No songs were chosen. Heart some songs on the calendar first.');
  if (raw.length > MAX_TRACKS) throw new HttpError(400, 'That is more songs than the calendar has.');
  if (!raw.every(id => ID_RE.test(id))) throw new HttpError(400, 'That link is not a Song of the Day playlist link.');
  const ids = [...new Set(raw)];
  const name = cleanName(url.searchParams.get('n')) || DEFAULT_NAME;

  const token = await getToken(env);
  let allowed = await allowedTracks(env, token);
  if (!ids.every(id => allowed.has(id))) allowed = await allowedTracks(env, token, true);   // a song added since the last check
  if (!ids.every(id => allowed.has(id))) throw new HttpError(400, 'Some of those songs are not on the Song of the Day calendar.');

  const key = 'pl:' + await sha256hex(name + '\n' + ids.join(','));
  const existing = await env.SOTD.get(key);
  if (existing) return Response.redirect('https://open.spotify.com/playlist/' + existing, 302);

  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const rk = 'rl:' + ip + ':' + Math.floor(Date.now() / 3600e3);
  const used = parseInt(await env.SOTD.get(rk) || '0', 10);
  if (used >= PER_IP_PER_HOUR) throw new HttpError(429, 'You have made a lot of playlists in the last hour.', { retryAfter: '3600' });
  await env.SOTD.put(rk, String(used + 1), { expirationTtl: 3700 });

  const pl = await spotify(token, '/me/playlists', 'POST', {
    name,
    description: 'This playlist is removed after a day. To keep the songs, tap ••• then Add to other playlist, then New playlist.',
    public: MAKE_PUBLIC
  });
  for (let i = 0; i < ids.length; i += 100) {
    await spotify(token, '/playlists/' + pl.id + '/items', 'POST', { uris: ids.slice(i, i + 100).map(id => 'spotify:track:' + id) });
  }
  await env.SOTD.put(key, pl.id, { metadata: { id: pl.id, at: Date.now(), name, n: ids.length } });
  return Response.redirect('https://open.spotify.com/playlist/' + pl.id, 302);
}

// ---------- the playlist as it is now, for the website calendar ----------

async function songsJson(request, env, ctx) {
  const cacheKey = new Request(new URL('/songs.json?v=3', request.url).toString());   // bump to drop cached copies after a format change
  const hit = await caches.default.match(cacheKey);
  if (hit) return hit;
  const token = await getToken(env);
  const items = await playlistItems(token);
  let pv = Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {});
  // a song added since the last look-up gets its preview now, so the calendar never shows it without one
  if (items.some(it => !(it.id in pv))) pv = await fillPreviews(env, items, 3, pv);
  const tags = (await env.SOTD.get('tags', 'json')) || {};
  const songs = items.map((it, i) => Object.assign({ n: i + 1 }, it,
    pv[it.id] && pv[it.id] !== '-' ? { p: 'https://p.scdn.co/mp3-preview/' + pv[it.id] } : {},
    tags[it.id] && (tags[it.id].m || (tags[it.id].g || []).length) ? { tag: { m: tags[it.id].m, d: tags[it.id].d, g: tags[it.id].g } } : {}));
  if (items.some(it => !(it.id in pv))) ctx.waitUntil(fillPreviews(env, items, 6).catch(() => {}));
  ctx.waitUntil(afterLook(env, items).catch(e => console.log('afterLook: ' + (e && e.message))));
  ctx.waitUntil(pushDaily(env, items).catch(e => console.log('pushDaily: ' + (e && e.message))));   // (the cron does this too)
  const res = new Response(JSON.stringify({ at: new Date().toISOString(), songs }), { headers: {
    'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'public, max-age=600'
  } });
  ctx.waitUntil(caches.default.put(cacheKey, res.clone()));
  return res;
}

// ---------- song facts John has signed off ----------

function factsHeaders(origin) {
  return { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Authorization, Content-Type, X-Device', 'Access-Control-Max-Age': '86400' };
}

async function factsSigned(env) {
  return new Response((await env.SOTD.get('factsSigned')) || '{}', { headers: factsHeaders('*') });
}

// one fact as John left it: its text, and the source it came from (none for one he wrote himself)
function cleanFact(f) {
  const t = String((f && f.t) || '').replace(/\s+/g, ' ').trim().slice(0, FACTS_MAX.text);
  if (!t) return null;
  const u = /^https?:\/\/\S+$/.test(String(f.u || '')) ? String(f.u).slice(0, 500) : '';
  return u ? { t, u, src: String(f.src || '').slice(0, 200) } : { t, by: 'John' };
}

async function factsSignoff(request, env) {
  const h = factsHeaders(ADMIN_ORIGIN);
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: h });
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: h });
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  if (!env.ADMIN_KEY) return answer(503, { error: 'The ADMIN_KEY secret is not set on the Worker.' });
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const failKey = 'factsfail:' + ip + ':' + new Date().toISOString().slice(0, 13);
  const fails = Number(await env.SOTD.get(failKey)) || 0;
  if (fails >= 20) return answer(429, { error: 'Too many wrong passwords from here this hour.' });
  const given = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  if (!given || await sha256hex(given) !== await sha256hex(env.ADMIN_KEY)) {
    await env.SOTD.put(failKey, String(fails + 1), { expirationTtl: 7200 });
    return answer(401, { error: 'Wrong password.' });
  }
  const body = await request.json().catch(() => null);
  const items = body && Array.isArray(body.items) ? body.items.slice(0, FACTS_MAX.items) : null;
  if (!items) return answer(400, { error: 'Nothing to sign off.' });
  if (!items.length) return answer(200, { ok: true, signed: 0 });   // the page checking its password
  const signed = (await env.SOTD.get('factsSigned', 'json')) || {};
  const at = new Date().toISOString();
  let n = 0;
  for (const it of items) {
    if (!it || !ID_RE.test(String(it.track || ''))) continue;
    const s = (Array.isArray(it.s) ? it.s : []).slice(0, FACTS_MAX.facts).map(cleanFact).filter(Boolean);
    const a = {};
    for (const [k, v] of Object.entries(it.a || {})) {
      if (!ID_RE.test(k) || !v) continue;
      a[k] = { name: String(v.name || '').slice(0, 200), f: (Array.isArray(v.f) ? v.f : []).slice(0, FACTS_MAX.facts).map(cleanFact).filter(Boolean) };
    }
    const rec = { at, s, a };
    // the day's moods (one or two of the ten), genres (up to two of the nine) and YouTube video, as John left them
    if (Array.isArray(it.moods)) rec.moods = it.moods.filter(m => MOOD_LIST.includes(m)).slice(0, 2);
    if (Array.isArray(it.genres)) rec.genres = it.genres.filter(g => GENRE_LIST.includes(g)).slice(0, 2);
    if (typeof it.video === 'string' && /^([\w-]{11})?$/.test(it.video)) rec.video = it.video;
    signed[it.track] = rec;
    n++;
  }
  await env.SOTD.put('factsSigned', JSON.stringify(signed));
  const job = await startFactsJob(env);
  return answer(200, { ok: true, signed: n, at, job });
}

async function startTvCopy(env, track) {   // video-tv.yml for one video, just uploaded
  if (!env.GH_TOKEN) return 'The GH_TOKEN secret is not set.';
  const r = await fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/video-tv.yml/dispatches', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + env.GH_TOKEN, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'SongOfTheDayWorker', 'Content-Type': 'application/json' },
    body: JSON.stringify({ ref: 'main', inputs: { track } })
  }).catch(e => ({ status: 0, text: async () => String(e) }));
  return r.status === 204 ? 'started' : 'GitHub answered ' + r.status;
}

async function startFactsJob(env) {
  if (!env.GH_TOKEN) return 'The GH_TOKEN secret is not set.';
  const r = await fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/' + FACTS_JOB + '/dispatches', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + env.GH_TOKEN, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'SongOfTheDayWorker', 'Content-Type': 'application/json' },
    body: JSON.stringify({ ref: 'main' })
  }).catch(e => ({ status: 0, text: async () => String(e) }));
  return r.status === 204 ? 'started' : 'GitHub answered ' + r.status + ': ' + (await r.text()).slice(0, 200);
}

// ---------- members: John and his family ----------
//
// John's own recordings: a/<track>.mp3 (the song) and v/<track>.mp4 (its video) in the R2 bucket MEDIA. He invites
// someone on songs/members-admin.html (with ADMIN_KEY): that gives a link to the calendar ending #join=<code>, which he
// sends them. Opening it signs that device in (up to INVITE_USES devices, within INVITE_DAYS): the page gets its own
// token for the device, which it keeps. Removing someone stops all their devices at once. A member's page asks
// /members/me for links to the files, signed for LINK_HOURS (a phone's audio and video players can't send a token, so
// the files at /m/... are checked by their link's signature alone). Anyone else, and any song without its own file,
// gets the preview and YouTube as before. Only hashes of invite codes and tokens are kept.
const MEMBERS_ORIGIN = 'https://cooperking.github.io';
const MEMBERS_JOIN = 'https://cooperking.github.io/songs/#join=';
const INVITE_USES = 5, INVITE_DAYS = 30, LINK_HOURS = 12, MEMBERS_MAX = 40;
// a TV can't open a link: John makes a short code for the member instead, typed in the Fire TV app's menu; one use,
// for TV_CODE_MINUTES
const TV_CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789', TV_CODE_MINUTES = 30;
function tvCode() { return [...crypto.getRandomValues(new Uint8Array(6))].map(b => TV_CODE_CHARS[b % TV_CODE_CHARS.length]).join(''); }
const MEDIA_KINDS = { a: { ext: 'mp3', type: 'audio/mpeg' }, v: { ext: 'mp4', type: 'video/mp4' } };
const MEDIA_KEY_RE = /^(a\/[0-9A-Za-z]{22}\.mp3|v\/[0-9A-Za-z]{22}\.mp4)$/;

function randomCode(bytes) { return b64url(crypto.getRandomValues(new Uint8Array(bytes))); }

async function adminOk(request, env, answer) {   // null if the admin password is right, else the answer to give
  if (!env.ADMIN_KEY) return answer(503, { error: 'The ADMIN_KEY secret is not set on the Worker.' });
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const failKey = 'factsfail:' + ip + ':' + new Date().toISOString().slice(0, 13);
  const fails = Number(await env.SOTD.get(failKey)) || 0;
  if (fails >= 20) return answer(429, { error: 'Too many wrong passwords from here this hour.' });
  const given = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  if (!given || await sha256hex(given) !== await sha256hex(env.ADMIN_KEY)) {
    await env.SOTD.put(failKey, String(fails + 1), { expirationTtl: 7200 });
    return answer(401, { error: 'Wrong password.' });
  }
  return null;
}

async function mediaSig(env, kind, track, exp) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode('sotd-media|' + env.ADMIN_KEY), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return b64url(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(kind + '/' + track + '/' + exp))).slice(0, 32);
}

async function catalog(env) { return (await env.SOTD.get('mediaCatalog', 'json')) || {}; }

async function catalogSet(env, key, size, fp) {   // which songs have which files: a and v, with their sizes (0: gone),
  const c = await catalog(env), kind = key[0], track = key.slice(2, 24);   // and fa, fv: each file's fingerprint, so a
  const e = c[track] || {};                                                // folder upload can skip what's already there
  if (size) { e[kind] = size; if (fp) e['f' + kind] = fp; else delete e['f' + kind]; } else { delete e[kind]; delete e['f' + kind]; }
  if (Object.keys(e).length) c[track] = e; else delete c[track];
  await env.SOTD.put('mediaCatalog', JSON.stringify(c));
}

async function members(request, url, env) {
  const h = factsHeaders(MEMBERS_ORIGIN);
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: h });
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: h });
  const route = url.pathname.slice('/members/'.length);
  if (route === 'join') return memberJoin(request, env, answer);
  if (route === 'signout') return memberSignout(request, env, answer);
  if (route === 'me') return memberMe(request, env, answer);
  if (route === 'playlists') return memberPlaylists(request, env, answer);
  if (route === 'tvcode') return memberTvCode(request, env, answer);
  if (route === 'push') return memberPush(request, env, answer);
  if (route === 'inbox') return memberInbox(request, env, answer);
  if (!['admin', 'upload', 'cover'].includes(route)) return answer(404, { error: 'Not here.' });
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const no = await adminOk(request, env, answer);
  if (no) return no;
  if (route === 'upload') return memberUpload(request, url, env, answer);
  if (route === 'cover') return coverSet(request, url, env, answer);
  const body = await request.json().catch(() => ({}));
  const all = (await env.SOTD.get('members', 'json')) || {};
  const save = () => env.SOTD.put('members', JSON.stringify(all));
  const owner = await env.SOTD.get('owner');   // the member who is John: only that member's devices are told so
  const invite = async (m) => {
    const code = randomCode(18);
    m.invite = { hash: await sha256hex(code), uses: 0, until: Date.now() + INVITE_DAYS * DAY };
    return MEMBERS_JOIN + code;
  };
  const listing = (own = owner) => Object.entries(all).map(([id, m]) => ({ id, name: m.name, created: m.created, owner: id === own,
    devices: Object.values(m.devices || {}).filter(d => d.did).length, seen: Math.max(0, ...Object.values(m.devices || {}).map(d => d.seen || 0)) || null,
    invite: m.invite ? { uses: m.invite.uses, left: Math.max(0, INVITE_USES - m.invite.uses), until: m.invite.until } : null }));
  switch (body.op) {
    case 'list': return answer(200, { members: listing(), catalog: await catalog(env), media: !!env.MEDIA, covers: await coversAll(env) });
    case 'invite': {
      const name = String(body.name || '').replace(/\s+/g, ' ').trim().slice(0, 60);
      if (!name) return answer(400, { error: 'Give their name.' });
      if (Object.keys(all).length >= MEMBERS_MAX) return answer(400, { error: 'That is the most members there can be.' });
      const id = randomCode(6), m = { name, created: Date.now(), devices: {} };
      const link = await invite(m);
      all[id] = m; await save();
      return answer(200, { id, link, members: listing() });
    }
    case 'reinvite': {
      const m = all[body.id];
      if (!m) return answer(404, { error: 'No such member.' });
      const link = await invite(m); await save();
      return answer(200, { id: body.id, link, members: listing() });
    }
    case 'tvcode': {
      if (!all[body.id]) return answer(404, { error: 'No such member.' });
      const code = tvCode();
      await env.SOTD.put('tvcode:' + await sha256hex(code), body.id, { expirationTtl: TV_CODE_MINUTES * 60 });
      return answer(200, { code, minutes: TV_CODE_MINUTES, name: all[body.id].name });
    }
    case 'owner': {   // "This is me": the member who is John (or nobody, with no id)
      if (body.id && !all[body.id]) return answer(404, { error: 'No such member.' });
      if (body.id) await env.SOTD.put('owner', body.id); else await env.SOTD.delete('owner');
      return answer(200, { ok: true, members: listing(body.id || null) });
    }
    case 'remove': {
      if (!all[body.id]) return answer(404, { error: 'No such member.' });
      delete all[body.id]; await save();
      await env.SOTD.delete('mpl:' + body.id);   // their saved playlists go too
      await pushForget(env, body.id);   // and their notifications
      await env.SOTD.delete('inbox:' + body.id);
      if (owner === body.id) await env.SOTD.delete('owner');
      return answer(200, { ok: true, members: listing(owner === body.id ? null : owner) });
    }
    case 'pushcount': return answer(200, await pushCount(env));
    case 'push': {   // John's own notification, to everyone who has them on
      const title = String(body.title || '').replace(/\s+/g, ' ').trim().slice(0, 80);
      const text = String(body.text || '').replace(/[ \t]+/g, ' ').trim().slice(0, 300);
      const link = String(body.url || '').trim();
      if (!title) return answer(400, { error: 'Give it a title.' });
      if (link && !/^https:\/\/cooperking\.github\.io\//.test(link)) return answer(400, { error: 'The link has to be a page on the site (https://cooperking.github.io/…).' });
      const to = Array.isArray(body.to) ? body.to.map(String).filter(id => all[id]) : null;   // chosen members, or everyone
      if (to && !to.length) return answer(400, { error: 'Choose who it goes to.' });
      // a song sent as it's shared: its card as the big picture (Android and computers; an iPhone shows neither) and
      // its cover beside it, from the site or Spotify only
      const pic = u => { const x = String(u || ''); return /^https:\/\/(cooperking\.github\.io|i\.scdn\.co|sotd-playlists\.cooperking\.workers\.dev)\//.test(x) && x.length < 600 ? x : ''; };
      const msg = { title, body: text, url: link || SITE_SONGS, tag: 'sotd-note-' + Date.now() };
      if (pic(body.image)) msg.image = pic(body.image);
      if (pic(body.icon)) msg.icon = pic(body.icon);
      const r = await pushAll(env, msg, to);
      return answer(200, r);
    }
    case 'invitecard': {   // the invitation's link-preview page and picture (drawn on the members page)
      const m = all[body.id];
      if (!m) return answer(404, { error: 'No such member.' });
      const link = String(body.link || '');
      if (!link.startsWith(MEMBERS_JOIN)) return answer(400, { error: 'That isn’t an invitation link.' });
      const b64 = String(body.card || '').replace(/^data:image\/jpeg;base64,/, '');
      if (!b64 || b64.length > 2.5e6) return answer(400, { error: 'The picture is missing or too big.' });
      const bytes = Uint8Array.from(atob(b64), ch => ch.charCodeAt(0));
      const pid = /^[\w-]{16}$/.test(String(body.page || '')) && await env.SOTD.get('invpage:' + body.page) ? body.page : randomCode(12);
      const ttl = INVITE_DAYS * 86400, at = Date.now();
      await env.SOTD.put('invcard:' + pid, bytes, { expirationTtl: ttl });
      await env.SOTD.put('invpage:' + pid, JSON.stringify({ link, title: String(body.title || (m.name + ' is invited')).slice(0, 120), at }), { expirationTtl: ttl });
      return answer(200, { page: pid, url: new URL(request.url).origin + '/i/' + pid });
    }
    case 'files': {
      if (!env.MEDIA) return answer(503, { error: 'No R2 bucket is bound to the Worker as MEDIA yet.' });
      const files = [];
      let cursor;
      do {
        const r = await env.MEDIA.list({ cursor, limit: 1000 });
        r.objects.forEach(o => files.push({ key: o.key, size: o.size, at: o.uploaded }));
        cursor = r.truncated ? r.cursor : null;
      } while (cursor);
      const c = {}, was = await catalog(env);   // the catalog again from what's really there (fingerprints kept where the size is the same)
      files.forEach(f => { if (MEDIA_KEY_RE.test(f.key)) { const t = f.key.slice(2, 24), k = f.key[0], e = (c[t] = c[t] || {}); e[k] = f.size; const o = was[t] || {}; if (o[k] === f.size && o['f' + k]) e['f' + k] = o['f' + k]; } });
      await env.SOTD.put('mediaCatalog', JSON.stringify(c));
      return answer(200, { files, catalog: c });
    }
    case 'delete': {
      if (!env.MEDIA) return answer(503, { error: 'No R2 bucket is bound to the Worker as MEDIA yet.' });
      if (!MEDIA_KEY_RE.test(String(body.key || ''))) return answer(400, { error: 'Not one of the files.' });
      await env.MEDIA.delete(body.key); await catalogSet(env, body.key, 0);
      if (body.key[0] === 'v') await env.MEDIA.delete('h/' + body.key.slice(2));   // and its TV copy
      return answer(200, { ok: true });
    }
  }
  return answer(400, { error: 'Unknown request.' });
}

// a file up in parts (a video can be bigger than the Worker takes in one go): start, then each part, then done
async function memberUpload(request, url, env, answer) {
  if (!env.MEDIA) return answer(503, { error: 'No R2 bucket is bound to the Worker as MEDIA yet.' });
  const key = url.searchParams.get('key') || '', op = url.searchParams.get('op'), id = url.searchParams.get('uploadId') || '';
  if (op === 'mark') {   // files already up (from before fingerprints), each the same size as the one picked: their
    // fingerprints noted, all in one write ({items: [{key, size, fp}]})
    const body = await request.json().catch(() => ({})), c = await catalog(env), done = [];
    for (const it of (Array.isArray(body.items) ? body.items : []).slice(0, 2000)) {
      const k = String(it.key || '');
      if (!MEDIA_KEY_RE.test(k) || typeof it.fp !== 'string') continue;
      const e = c[k.slice(2, 24)];
      if (!e || e[k[0]] !== Number(it.size)) continue;
      e['f' + k[0]] = it.fp.slice(0, 80); done.push(k);
    }
    if (done.length) await env.SOTD.put('mediaCatalog', JSON.stringify(c));
    return answer(200, { ok: true, marked: done });
  }
  if (!MEDIA_KEY_RE.test(key)) return answer(400, { error: 'Not one of the files (a/<track>.mp3 or v/<track>.mp4).' });
  if (op === 'start') {
    const up = await env.MEDIA.createMultipartUpload(key, { httpMetadata: { contentType: MEDIA_KINDS[key[0]].type } });
    return answer(200, { uploadId: up.uploadId });
  }
  if (!id) return answer(400, { error: 'Which upload?' });
  const up = env.MEDIA.resumeMultipartUpload(key, id);
  if (op === 'part') {
    const n = parseInt(url.searchParams.get('n'), 10);
    if (!(n >= 1 && n <= 10000) || !request.body) return answer(400, { error: 'Which part?' });
    const part = await up.uploadPart(n, request.body);
    return answer(200, { partNumber: part.partNumber, etag: part.etag });
  }
  if (op === 'done') {
    const body = await request.json().catch(() => ({}));
    const parts = (Array.isArray(body.parts) ? body.parts : []).map(p => ({ partNumber: Number(p.partNumber), etag: String(p.etag) }));
    if (!parts.length) return answer(400, { error: 'No parts.' });
    const obj = await up.complete(parts);
    await catalogSet(env, key, obj.size, typeof body.fp === 'string' ? body.fp.slice(0, 80) : '');
    if (key[0] === 'v') { await env.MEDIA.delete('h/' + key.slice(2)); await startTvCopy(env, key.slice(2, 24)); }   // a new video: its TV copy made again
    return answer(200, { ok: true, key, size: obj.size });
  }
  if (op === 'abort') { await up.abort(); return answer(200, { ok: true }); }
  return answer(400, { error: 'Unknown step.' });
}

// John's own picture for a day's sharing link (songs/<day>/ and its card), instead of the album cover: kept in R2 as
// c/<track> (public: link previews fetch it), listed at /covers.json; each change starts song-tags.yml, whose
// share_pages.py remakes that day's card from it. Taking it away puts the album cover back.
const COVER_TYPES = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp' }, COVER_MAX = 15e6;

// John's own covers for a day: several, each seen by all members or by the members he picks (one given to a member
// wins over one for all). KV covers: { <track>: { list: [{ id, at, who: 'all' | [member ids], share: { at, title, desc,
// slug } }] } }; R2 c/<track>/<id> the cover, sc/<track>/<id> the day's card with it ('0' is the one from before
// there could be several: c/<track>, sc/<track>).
function coverList(c) { return !c ? [] : Array.isArray(c.list) ? c.list : [{ id: '0', at: c.at, who: 'all', share: c.share || null }]; }
async function coversAll(env) {
  const raw = (await env.SOTD.get('covers', 'json')) || {}, out = {};
  for (const [t, c] of Object.entries(raw)) { const list = coverList(c); if (list.length) out[t] = { list }; }
  return out;
}
function coverKey(track, id, card) { return (card ? 'sc/' : 'c/') + track + (id === '0' ? '' : '/' + id); }
function coverFor(list, memberId) { return list.find(x => Array.isArray(x.who) && x.who.includes(memberId)) || list.find(x => x.who === 'all') || null; }

async function coverSet(request, url, env, answer) {
  if (!env.MEDIA) return answer(503, { error: 'No R2 bucket is bound to the Worker as MEDIA yet.' });
  const track = url.searchParams.get('track') || '', op = url.searchParams.get('op') || '', id = url.searchParams.get('id') || '';
  if (!ID_RE.test(track)) return answer(400, { error: 'Which song?' });
  const covers = await coversAll(env), list = (covers[track] || { list: [] }).list, x = list.find(c => c.id === id);
  if (op && !x) return answer(404, { error: 'No such cover.' });
  if (op === 'remove') {
    await env.MEDIA.delete(coverKey(track, id)); await env.MEDIA.delete(coverKey(track, id, true));
    list.splice(list.indexOf(x), 1);
  } else if (op === 'card') {   // the card a member's share of this day shows with this cover, and its words
    const body = await request.json().catch(() => ({}));
    const b64 = String(body.card || '').replace(/^data:image\/jpeg;base64,/, '');
    if (!b64 || b64.length > 2.5e6) return answer(400, { error: 'The card is missing or too big.' });
    await env.MEDIA.put(coverKey(track, id, true), Uint8Array.from(atob(b64), ch => ch.charCodeAt(0)), { httpMetadata: { contentType: 'image/jpeg' } });
    const t = v => String(v || '').slice(0, 200);
    x.share = { at: new Date().toISOString(), title: t(body.title), desc: t(body.desc), slug: /^\d\d-\d\d-\d{4}$/.test(body.slug || '') ? body.slug : '' };
  } else if (op === 'who') {   // who sees it: 'all', or some members
    const body = await request.json().catch(() => ({}));
    const all = (await env.SOTD.get('members', 'json')) || {};
    x.who = body.who === 'all' ? 'all' : (Array.isArray(body.who) ? body.who : []).filter(m => all[m]).slice(0, MEMBERS_MAX);
  } else if (op) return answer(400, { error: 'Say what to do.' });
  else {   // a cover: a new one (for all members), or a new picture for one there already (&id=)
    const type = (request.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
    if (!COVER_TYPES[type]) return answer(400, { error: 'A JPEG, PNG or WebP picture, please.' });
    const bytes = await request.arrayBuffer();
    if (!bytes.byteLength || bytes.byteLength > COVER_MAX) return answer(400, { error: 'The picture is empty or bigger than 15 MB.' });
    let c = list.find(v => v.id === id);
    if (!c) { if (list.length >= 12) return answer(400, { error: 'That’s the most covers a day can have.' }); c = { id: randomCode(6), who: 'all' }; list.push(c); }
    await env.MEDIA.put(coverKey(track, c.id), bytes, { httpMetadata: { contentType: type } });
    c.at = new Date().toISOString();
    covers[track] = { list };
    await env.SOTD.put('covers', JSON.stringify(covers));
    return answer(200, { ok: true, id: c.id, covers });
  }
  if (list.length) covers[track] = { list }; else delete covers[track];
  await env.SOTD.put('covers', JSON.stringify(covers));
  return answer(200, { ok: true, covers });   // (members only: the site's own cards keep the album cover)
}

async function ownCover(request, url, env) {
  const h = { 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'public, max-age=300' };
  if (url.pathname === '/covers.json') return new Response('{}', { headers: Object.assign({ 'Content-Type': 'application/json; charset=utf-8' }, h) });   // (members are told theirs by /members/me)
  const sm = /^\/s\/([0-9A-Za-z]{22})(?:\/([\w-]{8}))?(\.jpg)?$/.exec(url.pathname);
  if (sm) return memberShare(url, env, sm[1], sm[2] || '0', !!sm[3]);
  const m = /^\/c\/([0-9A-Za-z]{22})(?:\/([\w-]{8}))?$/.exec(url.pathname);
  if (!m || !env.MEDIA) return new Response('Not here.', { status: 404 });
  const obj = await env.MEDIA.get(coverKey(m[1], m[2] || '0'));
  if (!obj) return new Response('Not here.', { status: 404 });
  return new Response(obj.body, { headers: Object.assign({ 'Content-Type': (obj.httpMetadata && obj.httpMetadata.contentType) || 'image/jpeg' }, h) });
}

async function memberShare(url, env, track, id, image) {
  const covers = await coversAll(env), c = ((covers[track] || {}).list || []).find(x => x.id === id);
  const day = 'https://cooperking.github.io/songs/' + (c && c.share && c.share.slug ? c.share.slug + '/' : '');
  if (image) {
    const obj = c && c.share && env.MEDIA ? await env.MEDIA.get(coverKey(track, id, true)) : null;
    if (!obj) return Response.redirect(day + 'card.jpg', 302);
    return new Response(obj.body, { headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'public, max-age=3600' } });
  }
  const to = day + url.search;
  if (!c || !c.share) return Response.redirect(to, 302);
  const e = x => esc(String(x)), img = url.origin + '/s/' + track + (id === '0' ? '' : '/' + id) + '.jpg?v=' + encodeURIComponent(c.share.at);
  return new Response(`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${e(c.share.title)} · John's Song of the Day</title>
<meta name="description" content="${e(c.share.desc)}">
<meta property="og:type" content="music.song">
<meta property="og:site_name" content="John's Song of the Day">
<meta property="og:title" content="${e(c.share.title)}">
<meta property="og:description" content="${e(c.share.desc)}">
<meta property="og:url" content="${e(url.origin + url.pathname)}">
<meta property="og:image" content="${e(img)}">
<meta property="og:image:secure_url" content="${e(img)}">
<meta property="og:image:type" content="image/jpeg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="${e(c.share.title)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="${e(img)}">
<link rel="icon" type="image/png" sizes="96x96" href="https://cooperking.github.io/songs/favicon-96.png?v=2">
<link rel="apple-touch-icon" sizes="180x180" href="https://cooperking.github.io/songs/apple-touch-icon.png?v=2">
<script>if (!${INVITE_BOTS}.test(navigator.userAgent)) location.replace(${JSON.stringify(to)});</script>
</head>
<body style="font-family: Georgia, serif; padding: 24px;">
<p><a href="${e(to)}">${e(c.share.title)}</a>: ${e(c.share.desc)}.</p>
</body>
</html>
`, { headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });
}

// Each phone, tablet, computer or TV keeps an id of its own (X-Device, or device in a join): a sign-in there replaces
// any earlier one from the same device, and the members page counts devices, not sign-ins. A sign-in from before the ids
// counts once it has said which device it is (its next visit); old ones that never come back aren't counted, and go
// after 30 days.
function deviceId(x) { const d = String(x || ''); return /^[A-Za-z0-9]{8,40}$/.test(d) ? d : ''; }
function deviceTidy(all, keepHash, did) {   // other sign-ins from this device go; long-gone ones without an id too
  let changed = false;
  for (const m of Object.values(all)) {
    for (const [h, d] of Object.entries(m.devices || {})) {
      if (h === keepHash) continue;
      if ((did && d.did === did) || (!d.did && Date.now() - (d.seen || d.at || 0) > 30 * 864e5)) { delete m.devices[h]; changed = true; }
    }
  }
  return changed;
}
async function memberSignout(request, env, answer) {   // this device's sign-in, gone from the members page too
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const token = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  if (!token) return answer(200, { ok: true });
  const hash = await sha256hex(token), all = (await env.SOTD.get('members', 'json')) || {};
  const id = Object.keys(all).find(k => all[k].devices && all[k].devices[hash]);
  if (id) { delete all[id].devices[hash]; await kvSoft(env, 'members', JSON.stringify(all)); }
  return answer(200, { ok: true });
}

async function memberJoin(request, env, answer) {
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const failKey = 'joinfail:' + ip + ':' + new Date().toISOString().slice(0, 13);
  const fails = Number(await env.SOTD.get(failKey)) || 0;
  if (fails >= 20) return answer(429, { error: 'Too many tries from here this hour.' });
  const body = await request.json().catch(() => ({}));
  const all = (await env.SOTD.get('members', 'json')) || {};
  const given = String(body.code || '');
  const short = given.toUpperCase().replace(/[^A-Z0-9]/g, '');
  let id, m, tvKey = null;
  if (short.length === 6) {   // a TV code: one use
    tvKey = 'tvcode:' + await sha256hex(short);
    id = await env.SOTD.get(tvKey);
    m = id && all[id];
    if (!m) {
      await env.SOTD.put(failKey, String(fails + 1), { expirationTtl: 7200 });
      return answer(403, { error: 'That code isn’t right, or it has run out. Get a new one from your Profile, or ask John.' });
    }
  } else {
    const hash = await sha256hex(given);
    id = Object.keys(all).find(k => all[k].invite && all[k].invite.hash === hash);
    m = id && all[id];
    if (!m || m.invite.until < Date.now() || m.invite.uses >= INVITE_USES) {
      if (!m) await env.SOTD.put(failKey, String(fails + 1), { expirationTtl: 7200 });
      return answer(403, { error: m ? 'This invite has run out. Ask John for a new one.' : 'This invite isn’t valid. Ask John for a new one.' });
    }
  }
  const token = randomCode(32);
  if (tvKey) await env.SOTD.delete(tvKey); else m.invite.uses++;
  m.devices = m.devices || {};
  const th = await sha256hex(token), did = deviceId(body.device);
  m.devices[th] = Object.assign({ at: Date.now(), seen: Date.now() }, did ? { did } : {});
  deviceTidy(all, th, did);
  await env.SOTD.put('members', JSON.stringify(all));
  return answer(200, { token, name: m.name });
}

async function memberMe(request, env, answer) {
  if (request.method !== 'GET') return answer(405, { error: 'Use GET.' });
  const token = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  if (!token) return answer(401, { error: 'Not signed in.' });
  const hash = await sha256hex(token);
  const all = (await env.SOTD.get('members', 'json')) || {};
  const id = Object.keys(all).find(k => all[k].devices && all[k].devices[hash]);
  if (!id) return answer(401, { error: 'Not a member (any more).' });
  const m = all[id], dev = m.devices[hash];
  const did = deviceId(request.headers.get('X-Device'));
  let save = Date.now() - (dev.seen || 0) > 6 * 3600e3;
  if (save) dev.seen = Date.now();
  if (did && dev.did !== did) { dev.did = did; dev.seen = Date.now(); deviceTidy(all, hash, did); save = true; }   // says which device it is: earlier sign-ins there go
  if (save) await kvSoft(env, 'members', JSON.stringify(all));
  const exp = Math.floor(Date.now() / 1000) + LINK_HOURS * 3600, base = new URL(request.url).origin, media = {};
  if (env.MEDIA && env.ADMIN_KEY) {
    for (const [track, e] of Object.entries(await catalog(env))) {
      const one = {};
      for (const kind of Object.keys(MEDIA_KINDS)) if (e[kind]) one[kind] = base + '/m/' + kind + '/' + track + '.' + MEDIA_KINDS[kind].ext + '?e=' + exp + '&s=' + await mediaSig(env, kind, track, exp);
      media[track] = one;
    }
  }
  const owner = (await env.SOTD.get('owner')) === id;   // John's own devices: the calendar shows them the way to the admin page
  const mycovers = {};   // for each day with John's own covers, the one this member sees (one given to them, else one for all)
  for (const [track, c] of Object.entries(await coversAll(env))) {
    const x = coverFor(c.list, id);
    if (x) mycovers[track] = x.share ? { id: x.id, at: x.at, share: x.share.at } : { id: x.id, at: x.at };
  }
  const unread = ((await env.SOTD.get('inbox:' + id, 'json')) || []).filter(x => !x.r).length;   // the Profile's inbox: not yet read
  return answer(200, Object.assign({ name: m.name, exp, media, mycovers, unread }, owner ? { owner: true } : {}));
}

// an invitation's link: a page that shows a proper card in a message (WhatsApp, iMessage...), like a day's share page,
// and sends the person on to their invitation on the calendar. Kept for as long as the invitation lasts.
const INVITE_BOTS = /bot|crawl|spider|preview|facebookexternalhit|facebot|whatsapp|telegram|slack|discord|linkedin|pinterest|embedly|iframely|skype|viber|signal|vkshare|redditbot|applebot|google|bing/i;
async function invitePage(url, env) {
  const m = /^\/i\/([\w-]{16})(\.jpg)?$/.exec(url.pathname);
  const gone = () => new Response('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>John\'s Song of the Day</title><p style="font-family:Georgia,serif;padding:24px">This invitation has run out. Ask John for a new one, or <a href="https://cooperking.github.io/songs/">open the calendar</a>.</p>', { status: 404, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
  if (!m) return gone();
  if (m[2]) {
    const img = await env.SOTD.get('invcard:' + m[1], 'arrayBuffer');
    if (!img) return new Response('Not here.', { status: 404 });
    return new Response(img, { headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'public, max-age=3600' } });
  }
  const p = await env.SOTD.get('invpage:' + m[1], 'json');
  if (!p) return gone();
  const e = x => esc(String(x)), img = url.origin + '/i/' + m[1] + '.jpg?v=' + p.at, desc = 'John\'s Song of the Day';
  return new Response(`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${e(p.title)} · John's Song of the Day</title>
<meta name="robots" content="noindex">
<meta name="description" content="${e(desc)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="John's Song of the Day">
<meta property="og:title" content="${e(p.title)}">
<meta property="og:description" content="${e(desc)}">
<meta property="og:url" content="${e(url.origin + url.pathname)}">
<meta property="og:image" content="${e(img)}">
<meta property="og:image:secure_url" content="${e(img)}">
<meta property="og:image:type" content="image/jpeg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="${e(p.title)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${e(p.title)}">
<meta name="twitter:image" content="${e(img)}">
<link rel="icon" type="image/png" sizes="96x96" href="https://cooperking.github.io/songs/favicon-96.png?v=2">
<link rel="apple-touch-icon" sizes="180x180" href="https://cooperking.github.io/songs/apple-touch-icon.png?v=2">
<script>if (!${INVITE_BOTS}.test(navigator.userAgent)) location.replace(${JSON.stringify(p.link)});</script>
</head>
<body style="font-family: Georgia, serif; padding: 24px;">
<p><a href="${e(p.link)}">${e(p.title)}</a>: John's Song of the Day.</p>
</body>
</html>
`, { headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });
}

// a member signing in another of their own devices (a Fire TV, a Home Screen icon): the same one-use code John can
// make for them, from their own Profile (a few an hour at most)
async function memberTvCode(request, env, answer) {
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const id = await memberFromToken(request, env);
  if (!id) return answer(401, { error: 'Not a member (any more).' });
  const rk = 'tvcodes:' + id + ':' + new Date().toISOString().slice(0, 13), n = Number(await env.SOTD.get(rk)) || 0;
  if (n >= 30) return answer(429, { error: 'That’s a lot of codes this hour. Try again in a little while.' });
  await env.SOTD.put(rk, String(n + 1), { expirationTtl: 7200 });
  const code = tvCode();
  await env.SOTD.put('tvcode:' + await sha256hex(code), id, { expirationTtl: TV_CODE_MINUTES * 60 });
  return answer(200, { code, minutes: TV_CODE_MINUTES });
}

// a member's saved playlists, kept with their sign-in so every device of theirs has them (KV key mpl:<member id>).
// GET: { lists: [{ id, name, tracks, at }] }, the newest first. POST { op: 'save', id?, name, tracks } adds one (no id) or
// replaces it, giving back { list, lists }; POST { op: 'delete', id } removes one.
const PLAYLISTS_MAX = 50, PLAYLIST_TRACKS = 500, PLAYLIST_NAME = 80;
async function memberFromToken(request, env) {
  const token = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  if (!token) return null;
  const hash = await sha256hex(token);
  const all = (await env.SOTD.get('members', 'json')) || {};
  return Object.keys(all).find(k => all[k].devices && all[k].devices[hash]) || null;
}
async function memberPlaylists(request, env, answer) {
  const id = await memberFromToken(request, env);
  if (!id) return answer(401, { error: 'Not a member (any more).' });
  const key = 'mpl:' + id;
  const lists = (await env.SOTD.get(key, 'json')) || [];
  const sorted = () => lists.slice().sort((a, b) => b.at - a.at);
  if (request.method === 'GET') return answer(200, { lists: sorted() });
  if (request.method !== 'POST') return answer(405, { error: 'Use GET or POST.' });
  const body = await request.json().catch(() => ({}));
  if (body.op === 'delete') {
    const i = lists.findIndex(l => l.id === body.id);
    if (i >= 0) { lists.splice(i, 1); await env.SOTD.put(key, JSON.stringify(lists)); }
    return answer(200, { ok: true, lists: sorted() });
  }
  if (body.op !== 'save') return answer(400, { error: 'Say save or delete.' });
  const name = String(body.name || '').replace(/\s+/g, ' ').trim().slice(0, PLAYLIST_NAME) || 'My playlist';
  const tracks = [...new Set((Array.isArray(body.tracks) ? body.tracks : []).map(String).filter(t => ID_RE.test(t)))].slice(0, PLAYLIST_TRACKS);
  let list = body.id ? lists.find(l => l.id === body.id) : null;
  if (!list) {
    if (lists.length >= PLAYLISTS_MAX) return answer(400, { error: 'That’s the most playlists you can keep (' + PLAYLISTS_MAX + '). Delete one first.' });
    list = { id: randomCode(9) }; lists.push(list);
  }
  Object.assign(list, { name, tracks, at: Date.now() });
  await env.SOTD.put(key, JSON.stringify(lists));
  return answer(200, { list, lists: sorted() });
}

// a member's file, by a signed link: in ranges, as audio and video players ask (a phone's won't play without)
// ---------- the calendar's app manifest, with a one-use sign-in code in its start address: an iPhone's Home Screen app
// opens at the manifest's start address (not the page's), and keeps its own storage apart from Safari, so this is how
// it opens signed in. The calendar points its manifest here while it offers the app. ----------
function appManifest(url) {
  const code = /^[A-Za-z0-9]{6}$/.test(url.searchParams.get('c') || '') ? url.searchParams.get('c') : '';
  const path = url.searchParams.get('p') === '/songs/draft/' ? '/songs/draft/' : '/songs/';
  // the icons' ?v= as icons.py sets it each month (m + year and month, London)
  const ym = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit' }).formatToParts(new Date());
  const site = 'https://cooperking.github.io', v = '?v=m' + ym.find(x => x.type === 'year').value + ym.find(x => x.type === 'month').value;
  const m = { name: 'John’s Song of the Day', short_name: 'Song of the Day', id: site + '/songs/',
    start_url: site + path + (code ? '#join=' + code : ''), scope: site + '/songs/', display: 'standalone',
    background_color: '#ec1a13', theme_color: '#ec1a13',
    icons: [{ src: site + '/songs/icon-192.png' + v, sizes: '192x192', type: 'image/png', purpose: 'any' },
            { src: site + '/songs/icon-512.png' + v, sizes: '512x512', type: 'image/png', purpose: 'any' },
            { src: site + '/songs/icon-maskable-512.png' + v, sizes: '512x512', type: 'image/png', purpose: 'maskable' }] };
  return new Response(JSON.stringify(m), { headers: { 'Content-Type': 'application/manifest+json; charset=utf-8', 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*' } });
}

// ---------- what John's videos are, for playing on a TV: read from each MP4's own header (no download) ----------
// /media/report?from=0&n=30 gives, for each video: size, length, width x height, codec (avc1 is H.264; hvc1/hev1 is
// HEVC), H.264 profile and level, frame rate, bitrate, and whether its index comes first (faststart). Kept in KV
// (mediaProbe) so each file is read once, until it changes. Technical details only, no files or links.
function boxes(dv, start, end) {   // the boxes in dv[start, end): [{ type, at, size, body }]
  const out = [];
  let o = start;
  while (o + 8 <= end) {
    let size = dv.getUint32(o), hdr = 8;
    const type = String.fromCharCode(dv.getUint8(o + 4), dv.getUint8(o + 5), dv.getUint8(o + 6), dv.getUint8(o + 7));
    if (size === 1 && o + 16 <= end) { size = Number(dv.getBigUint64(o + 8)); hdr = 16; }
    else if (size === 0) size = end - o;
    if (size < hdr) break;
    out.push({ type, at: o, size, body: o + hdr, end: Math.min(end, o + size) });
    o += size;
  }
  return out;
}
function child(dv, b, type) { return boxes(dv, b.body, b.end).find(x => x.type === type); }
function moovInfo(dv, moov) {
  const info = {};
  const mvhd = child(dv, moov, 'mvhd');
  if (mvhd) { const v = dv.getUint8(mvhd.body); const ts = dv.getUint32(mvhd.body + (v ? 20 : 12)); const du = v ? Number(dv.getBigUint64(mvhd.body + 24)) : dv.getUint32(mvhd.body + 16); info.dur = ts ? Math.round(du / ts * 10) / 10 : 0; }
  for (const trak of boxes(dv, moov.body, moov.end).filter(x => x.type === 'trak')) {
    const mdia = child(dv, trak, 'mdia'); if (!mdia) continue;
    const hdlr = child(dv, mdia, 'hdlr'); const kind = hdlr ? String.fromCharCode(...[8, 9, 10, 11].map(i => dv.getUint8(hdlr.body + i))) : '';
    const mdhd = child(dv, mdia, 'mdhd');
    let ts = 0, du = 0;
    if (mdhd) { const v = dv.getUint8(mdhd.body); ts = dv.getUint32(mdhd.body + (v ? 20 : 12)); du = v ? Number(dv.getBigUint64(mdhd.body + 24)) : dv.getUint32(mdhd.body + 16); }
    const stbl = (() => { const minf = child(dv, mdia, 'minf'); return minf && child(dv, minf, 'stbl'); })();
    const stsd = stbl && child(dv, stbl, 'stsd');
    const entry = stsd && boxes(dv, stsd.body + 8, stsd.end)[0];
    if (kind === 'vide' && entry) {
      info.codec = entry.type;
      const tkhd = child(dv, trak, 'tkhd');
      if (tkhd) { info.w = dv.getUint32(tkhd.end - 8) >>> 16; info.h = dv.getUint32(tkhd.end - 4) >>> 16; }
      const cfg = boxes(dv, entry.body + 78, entry.end).find(x => x.type === 'avcC' || x.type === 'hvcC');
      if (cfg && cfg.type === 'avcC') { info.profile = dv.getUint8(cfg.body + 1); info.level = dv.getUint8(cfg.body + 3) / 10; }
      if (cfg && cfg.type === 'hvcC') { info.profile = dv.getUint8(cfg.body + 1) & 31; info.level = dv.getUint8(cfg.body + 12) / 30; }
      const stsz = stbl && child(dv, stbl, 'stsz');
      if (stsz && ts && du) info.fps = Math.round(dv.getUint32(stsz.body + 8) / (du / ts) * 100) / 100;
    }
    if (kind === 'soun' && entry) info.audio = entry.type;
  }
  return info;
}
async function probeMp4(env, key, size) {
  const read = async (offset, length) => { const o = await env.MEDIA.get(key, { range: { offset, length: Math.min(length, size - offset) } }); return o ? new DataView(await o.arrayBuffer()) : null; };
  const order = [];
  let off = 0, info = null, steps = 0;
  while (off < size && steps++ < 12) {   // the top-level boxes, one header at a time (mdat is skipped, not read)
    const h = await read(off, 16); if (!h || h.byteLength < 8) break;
    const b = boxes(h, 0, h.byteLength)[0]; if (!b) break;
    let len = b.size;
    if (h.getUint32(0) === 1) len = Number(h.getBigUint64(8)); else if (h.getUint32(0) === 0) len = size - off;
    order.push(b.type);
    if (b.type === 'moov') {
      const m = await read(off, Math.min(len, 32 << 20)); if (!m) break;
      info = moovInfo(m, boxes(m, 0, m.byteLength)[0]);
    }
    off += len;
  }
  info = info || {};
  info.size = size;
  info.fast = order.indexOf('moov') >= 0 && (order.indexOf('mdat') < 0 || order.indexOf('moov') < order.indexOf('mdat'));
  if (info.dur) info.kbps = Math.round(size * 8 / info.dur / 1000);
  return info;
}
async function mediaReport(url, env) {
  const h = { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*' };
  if (!env.MEDIA) return new Response('{}', { headers: h });
  const cat = await catalog(env), keep = (await env.SOTD.get('mediaProbe', 'json')) || {};
  const tracks = Object.keys(cat).filter(t => cat[t].v).sort();
  const from = Math.max(0, parseInt(url.searchParams.get('from'), 10) || 0), n = Math.min(60, Math.max(1, parseInt(url.searchParams.get('n'), 10) || 30));
  let changed = false;
  for (const t of tracks.slice(from, from + n)) {
    const size = cat[t].v;
    if (!(keep[t] && keep[t].size === size && !keep[t].err)) {
      try { keep[t] = await probeMp4(env, 'v/' + t + '.mp4', size); } catch (e) { keep[t] = { size, err: String(e && e.message || e).slice(0, 120) }; }
      changed = true;
    }
    const hh = await env.MEDIA.head('h/' + t + '.mp4');   // its TV copy too, if it has one
    if (!hh) { if (keep['h:' + t]) { delete keep['h:' + t]; changed = true; } continue; }
    if (keep['h:' + t] && keep['h:' + t].size === hh.size && !keep['h:' + t].err) continue;
    try { keep['h:' + t] = await probeMp4(env, 'h/' + t + '.mp4', hh.size); } catch (e) { keep['h:' + t] = { size: hh.size, err: String(e && e.message || e).slice(0, 120) }; }
    changed = true;
  }
  if (changed) await kvSoft(env, 'mediaProbe', JSON.stringify(keep));
  const videos = {}; tracks.forEach(t => { if (keep[t] && keep[t].size === cat[t].v) videos[t] = Object.assign({}, keep[t], keep['h:' + t] ? { tv: keep['h:' + t] } : {}); });
  return new Response(JSON.stringify({ total: tracks.length, done: Object.keys(videos).length, next: from + n < tracks.length ? from + n : null, videos }), { headers: h });
}

async function mediaFile(request, url, env) {
  const m = /^\/m\/([av])\/([0-9A-Za-z]{22})\.(mp3|mp4)$/.exec(url.pathname);
  const plain = (status, text) => new Response(text, { status, headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' } });
  if (!m || MEDIA_KINDS[m[1]].ext !== m[3]) return plain(404, 'Not here.');
  if (!env.MEDIA || !env.ADMIN_KEY) return plain(503, 'Not set up.');
  if (request.method !== 'GET' && request.method !== 'HEAD') return plain(405, 'Use GET.');
  const exp = parseInt(url.searchParams.get('e'), 10), sig = url.searchParams.get('s') || '';
  if (!(exp > Date.now() / 1000) || sig !== await mediaSig(env, m[1], m[2], exp)) return plain(403, 'This link has run out or isn’t valid.');
  // a video's TV-friendly copy (h/<track>.mp4, made by video-tv.yml) wherever there is one: it plays everywhere
  let key = m[1] + '/' + m[2] + '.' + m[3], head = m[1] === 'v' ? await env.MEDIA.head('h/' + m[2] + '.mp4') : null;
  if (head) key = 'h/' + m[2] + '.mp4'; else head = await env.MEDIA.head(key);
  if (!head) return plain(404, 'Not here.');
  const size = head.size, type = MEDIA_KINDS[m[1]].type;
  // a video can change under the same address (its TV copy arriving): browsers check before using what they kept
  // (no-cache), and a piece asked for against the old file (If-Range) gets the whole new one, not a piece of it
  const hdrs = { 'Content-Type': type, 'Accept-Ranges': 'bytes', 'Cache-Control': m[1] === 'v' ? 'private, no-cache' : 'private, max-age=3600', 'ETag': head.httpEtag,
    // the calendar fetches a song to keep it on the device (a member's download)
    'Access-Control-Allow-Origin': MEMBERS_ORIGIN, 'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, ETag' };
  if (request.headers.get('If-None-Match') === head.httpEtag) return new Response(null, { status: 304, headers: hdrs });
  let start = 0, end = size - 1, partial = false;
  const ifr = request.headers.get('If-Range');
  const rg = ifr && ifr !== head.httpEtag ? null : /^bytes=(\d*)-(\d*)$/.exec(request.headers.get('Range') || '');
  if (rg && (rg[1] || rg[2])) {
    if (rg[1]) { start = parseInt(rg[1], 10); if (rg[2]) end = Math.min(size - 1, parseInt(rg[2], 10)); }
    else { start = Math.max(0, size - parseInt(rg[2], 10)); }
    if (start >= size || start > end) return new Response(null, { status: 416, headers: Object.assign(hdrs, { 'Content-Range': 'bytes */' + size }) });
    partial = true;
  }
  hdrs['Content-Length'] = String(end - start + 1);
  if (partial) hdrs['Content-Range'] = 'bytes ' + start + '-' + end + '/' + size;
  if (request.method === 'HEAD') return new Response(null, { status: partial ? 206 : 200, headers: hdrs });
  const obj = await env.MEDIA.get(key, partial ? { range: { offset: start, length: end - start + 1 } } : {});
  if (!obj) return plain(404, 'Not here.');
  return new Response(obj.body, { status: partial ? 206 : 200, headers: hdrs });
}

// After each look at the playlist: start the GitHub job if the playlist has changed since the job last started;
// once a day, clean up old visitor playlists and tag songs the published tags don't have yet.
let jobDay = '';   // the London day this copy of the Worker saw a run include that day's song: nothing more to do

// ---------- notifications (Web Push) ----------
// Members turn them on in their Profile: the phone asks permission, then hands the calendar a subscription (an address
// at Apple's, Google's or Mozilla's push service, and two keys), which the calendar sends here (/members/push) with the
// member's sign-in. Kept in KV under pushSubs, one per device, with the member it belongs to; one the push service says
// is gone (404/410) is dropped. Each message is encrypted for that device (RFC 8291, aes128gcm) and signed with this
// Worker's own VAPID key (RFC 8292), made the first time it's needed and kept in KV (vapid); the calendar fetches the
// public half. A new key would end every subscription, so it's never replaced.
// The day's song goes out once, when it's the newest on the playlist and John's MP3 and video for it are both up (the
// cron looks every 10 minutes, and so does every fresh look at the playlist for songs.json). The very first look only
// notes the song. John's own messages come from the members page (op 'push').
const LIVE_ORIGIN = 'https://sotd-playlists.cooperking.workers.dev';
const SITE_SONGS = 'https://cooperking.github.io/songs/';
const PUSH_MAX = 300, PUSH_TTL = 20 * 3600;
const u8 = s => new TextEncoder().encode(s);
function cat(...parts) { const n = parts.reduce((a, p) => a + p.length, 0), out = new Uint8Array(n); let i = 0; for (const p of parts) { out.set(p, i); i += p.length; } return out; }
function unb64url(s) { const b = atob(String(s).replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((String(s).length + 3) % 4)); return Uint8Array.from(b, c => c.charCodeAt(0)); }
async function vapidKeys(env) {
  let k = await env.SOTD.get('vapid', 'json');
  if (k && k.jwk && k.pub) return k;
  const kp = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
  k = { jwk: await crypto.subtle.exportKey('jwk', kp.privateKey), pub: b64url(await crypto.subtle.exportKey('raw', kp.publicKey)), at: Date.now() };
  await env.SOTD.put('vapid', JSON.stringify(k));
  return k;
}
async function vapidAuth(k, endpoint) {   // the Authorization header for one push service
  const head = b64url(u8(JSON.stringify({ typ: 'JWT', alg: 'ES256' })));
  const claims = b64url(u8(JSON.stringify({ aud: new URL(endpoint).origin, exp: Math.floor(Date.now() / 1000) + 50 * 60, sub: SITE_SONGS })));
  const key = await crypto.subtle.importKey('jwk', k.jwk, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, key, u8(head + '.' + claims));
  return 'vapid t=' + head + '.' + claims + '.' + b64url(sig) + ', k=' + k.pub;
}
async function hkdf(salt, ikm, info, bytes) {
  const k = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt, info }, k, bytes * 8));
}
async function pushEncrypt(keys, plain) {   // RFC 8291: one aes128gcm record
  const uaPub = unb64url(keys.p256dh), auth = unb64url(keys.auth);
  const mine = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
  const asPub = new Uint8Array(await crypto.subtle.exportKey('raw', mine.publicKey));
  const theirs = await crypto.subtle.importKey('raw', uaPub, { name: 'ECDH', namedCurve: 'P-256' }, false, []);
  const shared = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: theirs }, mine.privateKey, 256));
  const ikm = await hkdf(auth, shared, cat(u8('WebPush: info\0'), uaPub, asPub), 32);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const cek = await hkdf(salt, ikm, u8('Content-Encoding: aes128gcm\0'), 16);
  const nonce = await hkdf(salt, ikm, u8('Content-Encoding: nonce\0'), 12);
  const key = await crypto.subtle.importKey('raw', cek, 'AES-GCM', false, ['encrypt']);
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce }, key, cat(plain, new Uint8Array([2]))));
  const rs = new Uint8Array(4); new DataView(rs.buffer).setUint32(0, 4096);
  return cat(salt, rs, new Uint8Array([asPub.length]), asPub, ct);
}
async function pushOne(k, sub, msg) {   // the push service's answer: { st: 201 sent, 404/410 gone; why: its reason when it says no }
  const r = await fetch(sub.endpoint, { method: 'POST', body: await pushEncrypt(sub.keys, u8(JSON.stringify(msg))), headers: {
    Authorization: await vapidAuth(k, sub.endpoint), 'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream',
    TTL: String(PUSH_TTL), Urgency: 'normal' } });
  return { st: r.status, why: r.ok ? '' : ((await r.text().catch(() => '')) || '').replace(/\s+/g, ' ').slice(0, 160) };
}
function pushHost(endpoint) {   // whose push service: for John, in plain words
  const h = new URL(endpoint).host;
  return /apple\.com$/.test(h) ? 'Apple' : /googleapis\.com$/.test(h) ? 'Google' : /mozilla|services\.mozilla\.com$/.test(h) ? 'Mozilla' : /windows|notify\.windows\.com$/.test(h) ? 'Microsoft' : h;
}
async function pushAll(env, msg, to) {   // to: only these members (else everyone)
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {}, k = await vapidKeys(env);
  const unread = await inboxAdd(env, to, msg);   // into each one's inbox first (notifications on or not); their unread counts
  let sent = 0, failed = 0; const gone = [], errors = [];
  const ids = Object.keys(subs).filter(id => !to || to.includes(subs[id].m));
  for (let i = 0; i < ids.length; i += 20) {   // twenty at a time
    await Promise.all(ids.slice(i, i + 20).map(async id => {
      try {
        const { st, why } = await pushOne(k, subs[id].sub, Object.assign({ unread: unread[subs[id].m] || 1, ib: unread[''] }, msg));   // (the number on the app's icon)
        if (st >= 200 && st < 300) sent++;
        else if (st === 404 || st === 410) gone.push(id);
        else { failed++; errors.push({ to: pushHost(subs[id].sub.endpoint), status: st, why }); console.log('push ' + st + ' ' + pushHost(subs[id].sub.endpoint) + ': ' + why); }
      } catch (e) { failed++; errors.push({ to: pushHost(subs[id].sub.endpoint), status: 0, why: String(e && e.message || e).slice(0, 160) }); console.log('push: ' + (e && e.message)); }
    }));
  }
  if (gone.length) {   // read again just before writing: a device may have turned them on meanwhile
    const now = (await env.SOTD.get('pushSubs', 'json')) || {};
    gone.forEach(id => delete now[id]);
    await kvSoft(env, 'pushSubs', JSON.stringify(now));
  }
  const res = { sent, gone: gone.length, failed, devices: ids.length - gone.length, errors: errors.slice(0, 5), at: Date.now(), title: msg.title };
  await kvSoft(env, 'pushLastResult', JSON.stringify(res));   // the last send, for the members page
  return res;
}
// ---------- each member's inbox: everything sent to them (the day's songs, John's own), newest first, read or not ----------
// Kept per member (inbox:<member>), the newest 60; reading on one device counts for all of theirs. The count of unread
// goes with each notification, for the number on the app's icon, and with /members/me.
const INBOX_MAX = 60;
async function inboxAdd(env, to, msg) {
  const all = (await env.SOTD.get('members', 'json')) || {}, ids = (to || Object.keys(all)).filter(id => all[id]), out = {};
  const item = { id: randomCode(8), at: Date.now(), t: msg.title, b: msg.body || '', u: msg.url || '' };
  if (msg.image) item.i = msg.image; if (msg.icon) item.c = msg.icon;
  await Promise.all(ids.map(async id => {
    const box = (await env.SOTD.get('inbox:' + id, 'json')) || [];
    box.unshift(item); box.length = Math.min(box.length, INBOX_MAX);
    await kvSoft(env, 'inbox:' + id, JSON.stringify(box));
    out[id] = box.filter(x => !x.r).length;
  }));
  out[''] = item.id;   // (the message's own id, for marking it read from a tap on it)
  return out;
}
// /members/inbox: GET the inbox; POST { read: [ids] } or { read: 'all' } marks them read; either way the unread count
async function memberInbox(request, env, answer) {
  const member = await memberFromToken(request, env);
  if (!member) return answer(401, { error: 'Not a member (any more).' });
  const key = 'inbox:' + member, box = (await env.SOTD.get(key, 'json')) || [];
  if (request.method === 'POST') {
    const body = await request.json().catch(() => ({}));
    const want = body.read === 'all' ? null : new Set((Array.isArray(body.read) ? body.read : []).map(String));
    let changed = false;
    box.forEach(x => { if (!x.r && (!want || want.has(x.id))) { x.r = Date.now(); changed = true; } });
    if (changed) await env.SOTD.put(key, JSON.stringify(box));
  } else if (request.method !== 'GET') return answer(405, { error: 'Use GET or POST.' });
  return answer(200, { items: box, unread: box.filter(x => !x.r).length });
}

async function pushCount(env) {
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {}, all = (await env.SOTD.get('members', 'json')) || {};
  const by = {}, ids = {};
  Object.values(subs).forEach(x => { const n = (all[x.m] && all[x.m].name) || '?'; by[n] = (by[n] || 0) + 1; ids[x.m] = (ids[x.m] || 0) + 1; });
  const kinds = {};
  Object.values(subs).forEach(x => { const k = pushHost(x.sub.endpoint); kinds[k] = (kinds[k] || 0) + 1; });
  return { devices: Object.keys(subs).length, members: by, ids, kinds, last: await env.SOTD.get('pushLastResult', 'json') };   // ids: devices with them on, by member id
}
async function pushForget(env, member) {
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {};
  const ids = Object.keys(subs).filter(id => subs[id].m === member);
  if (!ids.length) return;
  ids.forEach(id => delete subs[id]);
  await kvSoft(env, 'pushSubs', JSON.stringify(subs));
}
// /members/push: GET the public key; POST { sub } to turn them on for this device, { sub, off: true } to turn them off
async function memberPush(request, env, answer) {
  if (request.method === 'GET') return answer(200, { key: (await vapidKeys(env)).pub });
  if (request.method !== 'POST') return answer(405, { error: 'Use GET or POST.' });
  const member = await memberFromToken(request, env);
  if (!member) return answer(401, { error: 'Not a member (any more).' });
  const body = await request.json().catch(() => ({}));
  const sub = body.sub || {}, endpoint = String(sub.endpoint || ''), keys = sub.keys || {};
  if (!/^https:\/\//.test(endpoint) || endpoint.length > 1000) return answer(400, { error: 'That isn’t a push subscription.' });
  const id = (await sha256hex(endpoint)).slice(0, 24);
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {};
  if (body.off) { if (subs[id]) { delete subs[id]; await env.SOTD.put('pushSubs', JSON.stringify(subs)); } return answer(200, { on: false }); }
  if (!keys.p256dh || !keys.auth || String(keys.p256dh).length > 200 || String(keys.auth).length > 100) return answer(400, { error: 'That subscription has no keys.' });
  if (!subs[id] && Object.keys(subs).length >= PUSH_MAX) return answer(400, { error: 'Too many devices have notifications on.' });
  subs[id] = { m: member, sub: { endpoint, keys: { p256dh: String(keys.p256dh), auth: String(keys.auth) } }, at: Date.now() };
  await env.SOTD.put('pushSubs', JSON.stringify(subs));
  if (body.test) {   // the first one, so they know it works (and if the push service says no, the calendar says why)
    let st = 0, why = '';
    const unread = ((await env.SOTD.get('inbox:' + member, 'json')) || []).filter(x => !x.r).length;   // (not in the inbox: the icon keeps its number)
    try { ({ st, why } = await pushOne(await vapidKeys(env), subs[id].sub, { title: 'Notifications are on', body: 'You’ll hear about each new Song of the Day as it goes up.', url: SITE_SONGS, tag: 'sotd-on', unread })); }
    catch (e) { why = String(e && e.message || e).slice(0, 160); }
    const res = { sent: st >= 200 && st < 300 ? 1 : 0, failed: st >= 200 && st < 300 ? 0 : 1, gone: 0, errors: st >= 200 && st < 300 ? [] : [{ to: pushHost(endpoint), status: st, why }], at: Date.now(), title: 'Notifications are on (test)' };
    await kvSoft(env, 'pushLastResult', JSON.stringify(res));
    return answer(200, { on: true, test: st, why });
  }
  return answer(200, { on: true });
}
let pushDone = '';   // this Worker's own note of the last song sent (so most looks don't read KV)
// the day's song goes out once both of John's files for it are up (his MP3 and his video), while it's still the newest
// song and no more than two days old (UK time); until then each look (every 10 minutes) checks again
async function pushDaily(env, items) {
  const last = items[items.length - 1];
  if (!last || !last.id || pushDone === last.id) return;
  const sentRaw = await env.SOTD.get('pushSent', 'json');
  if (!sentRaw) { await kvSoft(env, 'pushSent', JSON.stringify([last.id])); pushDone = last.id; return; }   // the very first look: noted, not sent
  if (sentRaw.includes(last.id)) { pushDone = last.id; return; }
  const day = londonDay(last.a);
  if ((Date.parse(londonDay()) - Date.parse(day)) / DAY > 2) { pushDone = last.id; return; }   // too old to be news
  const files = (await catalog(env))[last.id] || {};
  if (!files.a || !files.v) return;   // waiting for the MP3 and the video
  if (!await kvSoft(env, 'pushSent', JSON.stringify(sentRaw.concat(last.id).slice(-60)))) return;   // can't note it: don't send (it could go twice)
  pushDone = last.id;
  const slug = day.slice(8, 10) + '-' + day.slice(5, 7) + '-' + day.slice(0, 4), card = SITE_SONGS + slug + '/card.jpg';
  const hasCard = await fetch(card, { method: 'HEAD' }).then(r => r.ok, () => false);   // the day's share card, once the site has made it
  const r = await pushAll(env, Object.assign({ title: last.title, body: last.artist + (day === londonDay() ? ' \u00b7 today\u2019s Song of the Day' : ' \u00b7 the latest Song of the Day'), icon: last.img || '',
    url: SITE_SONGS + '#' + slug, tag: 'sotd-day' }, hasCard ? { image: card } : {}));
  console.log('pushDaily ' + last.id + ': ' + JSON.stringify(r));
}

function londonDay(d) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London' }).format(d ? new Date(d) : new Date());   // 2026-10-22
}

async function afterLook(env, items) {
  const today = londonDay();
  if (jobDay === today) return;
  const job = (await env.SOTD.get('job', 'json')) || {};
  const chores = [];
  if ((await env.SOTD.get('choresDay')) !== today && await kvSoft(env, 'choresDay', today)) {   // the first look of the day
    chores.push(cleanup(env), tagNewSongs(env, items, 3));
  }
  if (job.at && londonDay(job.at) === today && job.newest === today) {
    jobDay = today;   // today's song has been through the job
  } else if (items.length) {
    const sig = await sha256hex(items.map(i => i.id).join(','));
    // a start GitHub refused is tried again after 10 minutes
    if (job.sig !== sig && !(job.err && job.tried > Date.now() - 600e3)) {
      const newest = londonDay(items.reduce((m, it) => (it.a > m ? it.a : m), items[0].a));   // the day the latest song was added
      chores.push(startJob(env, sig, job, items.length, newest));
      if (chores.length === 1) chores.push(tagNewSongs(env, items, 3));   // a new song: tag it now
    }
  }
  await Promise.all(chores.map(p => p.catch(() => {})));
}

async function startJob(env, sig, job, songs, newest, justRun) {
  if (justRun) {   // a run for another reason (a new picture for a sharing link): the playlist's record stays as it is
    if (!env.GH_TOKEN) return 'The GH_TOKEN secret is not set.';
    const r = await fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/' + JOB + '/dispatches', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + env.GH_TOKEN, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'SongOfTheDayWorker', 'Content-Type': 'application/json' },
      body: JSON.stringify({ ref: 'main' })
    }).catch(e => ({ status: 0, text: async () => String(e) }));
    return r.status === 204 ? 'started' : 'GitHub answered ' + r.status + ': ' + (await r.text()).slice(0, 200);
  }
  const rec = Object.assign({}, job, { tried: Date.now() });
  await env.SOTD.put('job', JSON.stringify(rec));   // so a second visit right now doesn't start it twice
  if (!env.GH_TOKEN) {
    rec.err = 'The GH_TOKEN secret is not set.';
  } else {
    const r = await fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/' + JOB + '/dispatches', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + env.GH_TOKEN, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'SongOfTheDayWorker', 'Content-Type': 'application/json' },
      body: JSON.stringify({ ref: 'main' })
    }).catch(e => ({ status: 0, text: async () => String(e) }));
    if (r.status === 204) {
      Object.assign(rec, { sig, at: new Date().toISOString(), songs, newest });
      if (newest === londonDay()) jobDay = newest;   // today's song is in: no more looking today
      delete rec.err;
    } else {
      rec.err = 'GitHub answered ' + r.status + ': ' + (await r.text()).slice(0, 200);
    }
  }
  await env.SOTD.put('job', JSON.stringify(rec));
}

// The Web API no longer gives preview links, but each song's Spotify embed page carries one.
// Looks up at most `limit` songs per run, newest first; '-' marks a song with no preview.
const pvMem = {};   // previews found, kept between requests too (for when KV can't take them: see kvSoft)
async function fillPreviews(env, items, limit, known) {
  const pv = known || (await env.SOTD.get('previews', 'json')) || {};
  const todo = items.filter(it => !(it.id in pv)).reverse().slice(0, limit);
  let changed = false;
  for (const it of todo) {
    const r = await fetch('https://open.spotify.com/embed/track/' + it.id, { headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36', 'Accept-Language': 'en-GB' } });
    if (!r.ok) break;
    const m = (await r.text()).match(/https:\/\/p\.scdn\.co\/mp3-preview\/([0-9a-f]{20,})/);
    pv[it.id] = m ? m[1] : '-';
    changed = true;
  }
  if (changed) { Object.assign(pvMem, pv); await kvSoft(env, 'previews', JSON.stringify(pv)); }
  return pv;
}

// ---------- mood and genres for songs added since tags.json was made ----------

const TAGS_URL = 'https://cooperking.github.io/songs/tags.json';
const MB = 'https://musicbrainz.org/ws/2/';
const MB_HEADERS = { 'User-Agent': 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)', Accept: 'application/json' };
const GENRE_EXACT = {"acid rock": "Rock", "acoustic": "Folk", "adult contemporary": "Pop", "afrobeat": "World", "afropop": "World", "alt country": "Country", "alt-country": "Country", "alternative": "Indie", "alternative country": "Country", "alternative dance": "Electronic", "alternative hip hop": "Hip-hop", "alternative pop": "Indie", "alternative rock": "Indie", "ambient": "Electronic", "american folk": "Folk", "americana": "Folk", "anti-folk": "Folk", "aor": "Pop", "arabic": "World", "art pop": "Pop", "art punk": "Indie", "art rock": "Rock", "avant-garde pop": "Pop", "baroque pop": "Pop", "beat music": "Rock", "bedroom pop": "Indie", "blue-eyed soul": "Soul & R&B", "bluegrass": "Country", "blues": "Soul & R&B", "blues rock": "Rock", "boogie": "Soul & R&B", "bossa nova": "World", "breakbeat": "Electronic", "brill building": "Pop", "british blues": "Rock", "british folk": "Folk", "british rhythm & blues": "Rock", "britpop": "Indie", "bubblegum": "Pop", "chamber folk": "Folk", "chamber pop": "Pop", "chanson": "World", "chillwave": "Indie", "city pop": "Pop", "classic rock": "Rock", "conscious hip hop": "Hip-hop", "contemporary folk": "Folk", "contemporary r&b": "Soul & R&B", "cosmic american music": "Country", "country": "Country", "country pop": "Country", "country rock": "Country", "cumbia": "World", "dance": "Electronic", "dance-pop": "Pop", "dance-punk": "Indie", "dark wave": "Electronic", "darkwave": "Electronic", "disco": "Soul & R&B", "doo-wop": "Soul & R&B", "downtempo": "Electronic", "dream pop": "Indie", "drum and bass": "Electronic", "dub": "World", "dubstep": "Electronic", "easy listening": "Pop", "ebm": "Electronic", "edm": "Electronic", "electro": "Electronic", "electro house": "Electronic", "electronic": "Electronic", "electronica": "Electronic", "electropop": "Pop", "emo": "Indie", "ethio-jazz": "World", "exotica": "World", "folk": "Folk", "folk pop": "Folk", "folk rock": "Folk", "folktronica": "Folk", "freak folk": "Folk", "french electro": "Electronic", "french house": "Electronic", "french pop": "World", "funk": "Soul & R&B", "funk pop": "Pop", "future bass": "Electronic", "garage rock": "Rock", "garage rock revival": "Indie", "glam rock": "Rock", "gospel": "Soul & R&B", "grunge": "Rock", "hard rock": "Rock", "hardcore": "Rock", "hardcore punk": "Rock", "heartland rock": "Rock", "heavy metal": "Rock", "highlife": "World", "hip hop": "Hip-hop", "hip-hop": "Hip-hop", "honky tonk": "Country", "house": "Electronic", "hypnagogic pop": "Pop", "idm": "Electronic", "indie": "Indie", "indie folk": "Folk", "indie pop": "Indie", "indie rock": "Indie", "indietronica": "Indie", "instrumental pop": "Pop", "italo-disco": "Electronic", "jam band": "Rock", "jangle pop": "Indie", "jazz pop": "Pop", "jazz-funk": "Soul & R&B", "krautrock": "Rock", "kwaito": "World", "latin": "World", "lo-fi": "Indie", "lo-fi indie": "Indie", "lounge": "Pop", "mainstream rock": "Rock", "mambo": "World", "marabi": "World", "math rock": "Indie", "merseybeat": "Rock", "metal": "Rock", "mod": "Rock", "motown": "Soul & R&B", "mpb": "World", "neo soul": "Soul & R&B", "neo-psychedelia": "Indie", "new rave": "Electronic", "new romantic": "Pop", "new wave": "Rock", "noise pop": "Indie", "noise rock": "Indie", "northern soul": "Soul & R&B", "nu-disco": "Electronic", "outlaw country": "Country", "plunderphonics": "Electronic", "pop": "Pop", "pop rock": "Pop", "pop soul": "Pop", "post-hardcore": "Rock", "post-punk": "Rock", "post-punk 2k": "Indie", "post-punk revival": "Indie", "post-rock": "Indie", "power pop": "Pop", "progressive pop": "Pop", "progressive rock": "Rock", "proto-punk": "Rock", "psychedelic folk": "Folk", "psychedelic pop": "Pop", "psychedelic rock": "Rock", "psychedelic soul": "Soul & R&B", "pub rock": "Rock", "punk": "Rock", "punk rock": "Rock", "quiet storm": "Soul & R&B", "r&b": "Soul & R&B", "rap": "Hip-hop", "rave": "Electronic", "raï": "World", "reggae": "World", "rhythm & blues": "Soul & R&B", "rock": "Rock", "rock and roll": "Rock", "rockabilly": "Rock", "roots rock": "Rock", "samba": "World", "shoegaze": "Indie", "singer-songwriter": "Folk", "singer/songwriter": "Folk", "ska": "World", "slacker rock": "Indie", "slowcore": "Indie", "soft rock": "Pop", "sophisti-pop": "Pop", "soul": "Soul & R&B", "soul jazz": "Soul & R&B", "southern rock": "Rock", "space rock": "Rock", "sunshine pop": "Pop", "surf rock": "Rock", "synth-pop": "Pop", "synthpop": "Pop", "synthwave": "Electronic", "techno": "Electronic", "township bubblegum": "World", "traditional pop": "Pop", "trip hop": "Electronic", "tropicália": "World", "twee pop": "Indie", "vocal jazz": "Pop", "yacht rock": "Pop", "zouk": "World"};
const GENRE_IGNORE = new Set(["avant-garde", "blues", "classic pop and rock", "comedy", "experimental", "film soundtrack", "leftfield", "non-music", "pop/rock", "rock and indie", "seen live", "session", "singer-songwriter", "singer/songwriter", "soundtrack"]);
const GENRE_WORDS = [["hip hop", "Hip-hop"], ["rap", "Hip-hop"], ["folk", "Folk"], ["country", "Country"], ["soul", "Soul & R&B"], ["funk", "Soul & R&B"], ["r&b", "Soul & R&B"], ["blues", "Soul & R&B"], ["disco", "Soul & R&B"], ["house", "Electronic"], ["techno", "Electronic"], ["electro", "Electronic"], ["ambient", "Electronic"], ["indie", "Indie"], ["punk", "Rock"], ["rock", "Rock"], ["pop", "Pop"]];

function genreBucket(name) {
  const n = name.toLowerCase().trim();
  if (GENRE_IGNORE.has(n)) return null;
  if (GENRE_EXACT[n]) return GENRE_EXACT[n];
  for (const [w, b] of GENRE_WORDS) if (new RegExp('\\b' + w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).test(n)) return b;
  return null;
}
function moodFrom(e, v) { return e >= 0.5 ? (v >= 0.5 ? 'Upbeat' : 'Brooding') : (v >= 0.5 ? 'Mellow' : 'Wistful'); }
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function mbGet(path) {
  await sleep(1100);   // MusicBrainz allows one request a second
  const r = await fetch(MB + path + (path.includes('?') ? '&' : '?') + 'fmt=json', { headers: MB_HEADERS });
  return r.ok ? r.json() : null;
}

async function tagNewSongs(env, items, limit) {
  const known = await fetch(TAGS_URL, { cf: { cacheTtl: 300 } }).then(r => r.ok ? r.json() : null).catch(() => null);
  if (!known) return;   // without the published list every song would look new
  const kv = (await env.SOTD.get('tags', 'json')) || {};
  const todo = items.filter(it => !(known.songs || {})[it.id] && !kv[it.id]).reverse().slice(0, limit);
  if (!todo.length) return;
  const rb = await fetch('https://api.reccobeats.com/v1/audio-features?ids=' + todo.map(t => t.id).join(','))
    .then(r => r.ok ? r.json() : null).catch(() => null);
  const feats = {};
  for (const f of (rb && rb.content) || []) { const m = (f.href || '').match(/track\/(\w+)/); if (m) feats[m[1]] = f; }
  for (const it of todo) {
    const t = { at: Date.now(), g: [] };
    const f = feats[it.id];
    if (f) { t.m = moodFrom(f.energy, f.valence); t.d = f.danceability >= 0.7 ? 1 : 0; }
    const title = it.title.replace(/\s+-\s+.*(remaster|version|mix|edit|mono|stereo|single|live|demo|take|recording|anniversary|bonus).*$/i, '')
      .replace(/\s*[\(\[](feat|with|ft)\.?[^\)\]]*[\)\]]/ig, '').trim();
    const lq = x => x.replace(/[\\"]/g, '\\$&');
    const found = await mbGet('recording?query=' + encodeURIComponent('recording:"' + lq(title) + '" AND artist:"' + lq((it.artists || [it.artist])[0]) + '"') + '&limit=3');
    const hit = found && (found.recordings || []).find(r => r.score >= 85);
    // genres: MusicBrainz tags on the song's album (counts most), its artists and the recording
    const share = pairs => {
      const w = {};
      for (const g of pairs) { if (g.count !== undefined && g.count <= 0) continue; const b = genreBucket(g.name); if (b) w[b] = (w[b] || 0) + Math.max(1, g.count || 1); }
      const tot = Object.values(w).reduce((x, y) => x + y, 0);
      return tot ? Object.fromEntries(Object.entries(w).map(([b, x]) => [b, x / tot])) : null;
    };
    const parts = [];
    const album = (it.album || '').replace(/\s*[\(\[][^\)\]]*(remaster|deluxe|edition|expanded|version|anniversary|mono|stereo|bonus|reissue)[^\)\]]*[\)\]]/ig, '')
      .replace(/\s+-\s+.*(remaster|deluxe|edition|version|anniversary).*$/i, '').trim();
    if (album) {
      const rgs = await mbGet('release-group?query=' + encodeURIComponent('releasegroup:"' + lq(album) + '" AND artist:"' + lq((it.artists || [it.artist])[0]) + '"') + '&limit=3');
      const rg = rgs && (rgs['release-groups'] || []).find(x => x.score >= 90);
      parts.push([3, share((rg && rg.tags) || [])]);
    }
    let artistTags = [];
    for (const ac of hit ? (hit['artist-credit'] || []).slice(0, 2) : []) {
      const a = await mbGet('artist/' + ac.artist.id + '?inc=tags');
      let tags = (a && (a.tags || a.genres)) || [];
      if (!tags.some(g => genreBucket(g.name))) {
        // artist has no usable tags: count the tags on its releases instead
        const rg = await mbGet('release-group?query=' + encodeURIComponent('arid:' + ac.artist.id) + '&limit=100');
        tags = ((rg && rg['release-groups']) || []).flatMap(x => x.tags || []);
      }
      artistTags = artistTags.concat(tags);
    }
    parts.push([2, share(artistTags)]);
    parts.push([1, share(((hit && hit.tags) || []).map(g => ({ name: g.name, count: 1 })))]);
    const w = {};
    let wsum = 0;
    for (const [weight, d] of parts) {
      if (!d) continue;
      wsum += weight;
      for (const [b, x] of Object.entries(d)) w[b] = (w[b] || 0) + weight * x;
    }
    t.g = Object.entries(w).sort((x, y) => y[1] - x[1]).filter(([, x]) => wsum && x / wsum >= 0.25).slice(0, 2).map(([b]) => b);
    kv[it.id] = t;
  }
  await env.SOTD.put('tags', JSON.stringify(kv));
}

// ---------- daily clean-up (on the first look of the day) ----------

async function cleanup(env) {
  const cutoff = Date.now() - KEEP_DAYS * DAY;
  const due = [];
  let waiting = 0, cursor;
  do {
    const lst = await env.SOTD.list({ prefix: 'pl:', cursor });
    for (const k of lst.keys) {
      const md = k.metadata || {};
      if (md.at && md.at > cutoff) { waiting++; continue; }
      due.push({ key: k.name, id: md.id || await env.SOTD.get(k.name), name: md.name, n: md.n, made: md.at });
    }
    cursor = lst.list_complete ? null : lst.cursor;
  } while (cursor);

  let removed = 0, done = 0, error = null;
  const log = [];
  try {
    if (due.length) {
      const token = await getToken(env);
      for (const d of due) {   // playlists recorded before names were kept: ask Spotify (fails if it's already gone)
        if (d.name || !d.id) continue;
        try { d.name = (await spotify(token, '/playlists/' + d.id + '?fields=name')).name; } catch (e) {}
      }
      for (let i = 0; i < due.length; i += 40) {
        const chunk = due.slice(i, i + 40), batch = chunk.filter(d => d.id);
        if (batch.length) {
          try { await spotify(token, '/me/library?uris=' + batch.map(d => 'spotify:playlist:' + d.id).join(','), 'DELETE'); }
          catch (e) {
            // One playlist Spotify won't take (already gone, say) mustn't hold up the rest: try them one at a time,
            // and drop any it refuses outright (4xx); anything else is left for tomorrow.
            if (!/^Spotify 4/.test(e.message || '')) throw e;
            for (const d of batch) {
              try { await spotify(token, '/me/library?uris=spotify:playlist:' + d.id, 'DELETE'); }
              catch (e2) { if (!/^Spotify 4/.test(e2.message || '')) throw e2; d.refused = (e2.message || '').slice(0, 60); }
            }
          }
        }
        for (const d of chunk) {
          await env.SOTD.delete(d.key);
          log.push({ at: new Date().toISOString(), id: d.id || null, name: d.name || null, n: d.n || null, made: d.made ? new Date(d.made).toISOString() : null, refused: d.refused || null });
        }
        removed += batch.length; done += chunk.length;
      }
    }
  } catch (e) {
    error = e.message || String(e);
  }
  const runs = ((await env.SOTD.get('cleanup', 'json')) || {}).runs || [];
  runs.unshift({ at: new Date().toISOString(), removed, waiting: waiting + due.length - done, error });
  if (log.length) {
    const kept = ((await env.SOTD.get('removed', 'json')) || []);
    await env.SOTD.put('removed', JSON.stringify(log.reverse().concat(kept).slice(0, 100)));
  }
  await env.SOTD.put('cleanup', JSON.stringify({ runs: runs.slice(0, 5) }));
}

// ---------- helpers ----------

function errorPage(e) {
  if (e.status === 429) {
    const mins = Math.max(1, Math.round((parseInt(e.extra.retryAfter || '600', 10)) / 60));
    return page('Try again later', '<p>' + esc(e.message) + ' Please try again in about ' + mins + (mins === 1 ? ' minute' : ' minutes') + '.</p>', 429);
  }
  return page(e.status === 503 ? 'Not available right now' : 'Can’t make that playlist', '<p>' + esc(e.message) + '</p>', e.status);
}

function page(title, body, status) {
  const html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>' + esc(title) + '</title><style>' +
    'body{margin:0;min-height:100vh;display:grid;place-items:center;background:#ec135b;color:#fffaf5;font:17px/1.5 Georgia,serif;padding:24px}' +
    'main{max-width:34em;background:#fff;color:#1b1020;border-radius:18px;padding:28px 26px;box-shadow:0 20px 60px rgba(0,0,0,.25)}' +
    'h1{margin:0 0 10px;font-weight:500;font-size:30px;line-height:1.1}.small{font:13px/1.5 system-ui,sans-serif;color:#6b5a6e}' +
    'h2{margin:22px 0 4px;font:600 12px/1.4 system-ui,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:#6b5a6e}ul{padding-left:1.1em;margin:6px 0}li{margin:4px 0;font-size:15px}a{color:#c2104b}' +
    '</style></head><body><main><h1>' + esc(title) + '</h1>' + body + '</main></body></html>';
  return new Response(html, { status: status || 200, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });
}

function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]); }
function b64url(buf) { return btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }
async function sha256hex(s) { return [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)))].map(b => b.toString(16).padStart(2, '0')).join(''); }
