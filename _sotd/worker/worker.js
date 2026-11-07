// Song of the Day — playlist maker (Cloudflare Worker)
//
// Visitors open  /p?t=<trackId>,<trackId>,...&n=<name>  (sent by the calendar's "Open in Spotify" button).
// /songs.json gives the website calendar the playlist as it is now (covers and 30-second previews included).
// The Worker checks every track is on the Song of the Day playlist, creates a playlist with them in that
// order on John's Spotify account, and redirects the visitor to it on open.spotify.com.
//
// The Worker's timer (every 10 minutes) reads the playlist from Spotify, from 06:00 until the day's song is in (see
// timerSongs); /songs.json is the list it last read. When the timer finds it changed, it starts the GitHub job (song-tags.yml) that
// finds each new song's video, year and moods, keeps the YouTube playlist in step and makes the share pages.
// Until a run has included the song for today (London time), every change to the playlist starts a run, so a
// calendar that has fallen behind catches up; once one has, the Worker doesn't look again until tomorrow.
// The same look gives new songs a mood (ReccoBeats) and genres (MusicBrainz) until the job's own are out,
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
const R2_REPO = 'cooperking/playlist-r2';             // the files' copies into R2 (its jobs on the Status tab too: John, 2 Nov 2026)
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
      if (url.pathname === '/durs') return await songDurs(url, env);   // songs' real lengths, from Spotify (the download job's check that a file is the whole song)
      if (url.pathname === '/captions.json') return new Response((await env.SOTD.get('mainCaps')) || '{}', { headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'public, max-age=30' } });   // John's captions on his own calendar
      if (url.pathname === '/upl/sync') return await uplSync(request, env);
    if (url.pathname === '/upl/data') return await uplData(request, env);
    if (url.pathname === '/upl/facts') return await uplFactsIn(request, env);
    if (url.pathname === '/upl/links') return await uplLinks(request, env);
    if (url.pathname === '/upl/ytdrop') return await uplYtDrop(request, env);
    if (url.pathname === '/upl/wanted') return await uplWanted(request, env);
    if (url.pathname === '/upl/vids') return await uplVids(request, env);
    if (url.pathname === '/upl/ytprog') return await uplYtProg(request, env);
    if (url.pathname === '/upl/pvneed') return await uplPvNeed(request, env);
    if (url.pathname === '/upl/clips') return await uplClips(request, env);
    if (url.pathname === '/upl/websearch') return await uplWebSearch(request, env);
    if (url.pathname.startsWith('/yt/')) return await ytList(request, url, env);
      if (url.pathname === '/login') return await login(url);
      if (url.pathname === '/splink') return await spLinkStart(url, env);
      if (url.pathname === '/callback') return await callback(request, url, env);
      if (url.pathname === '/members/spwaiting') {   // how many members wait to be added in Spotify's dashboard (a number only): the Mac task's cheap first look
        let n = 0, cursor;
        do { const l = await env.SOTD.list({ prefix: 'spm:', cursor }); for (const k of l.keys) { const r = await env.SOTD.get(k.name, 'json'); if (r && r.blocked && (r.email || r.wemail)) n++; } cursor = l.list_complete ? null : l.cursor; } while (cursor);
        return new Response(JSON.stringify({ waiting: n }), { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*' } });
      }
      if (url.pathname === '/p') return await makePlaylist(request, url, env);
      if (url.pathname === '/status') return await status(env);
      if (url.pathname === '/facts/signed') return await factsSigned(env);
      if (url.pathname === '/facts/signoff') return await factsSignoff(request, env);
      if (url.pathname === '/facts/video') return await factsSignoff(request, env, true);
      if (url.pathname === '/facts/picks') return new Response((await env.SOTD.get('videoPicks')) || '{}', { headers: factsHeaders('*') });
      if (url.pathname === '/facts/redo') return await factsRedo(request, env);
      if (url.pathname.startsWith('/members/')) return await members(request, url, env);
      if (url.pathname === '/push/check') return await pushCheck(request, env, ctx);
      if (url.pathname.startsWith('/m/')) return await mediaFile(request, url, env);
      if (url.pathname.startsWith('/uc/')) return await uplCoverGet(url, env);
      if (url.pathname.startsWith('/sp/')) return await spKept(request, url, env);
      if (url.pathname.startsWith('/up/')) return await uplPicGet(url, env);
      if (url.pathname === '/media/report') return await mediaReport(url, env);
      if (url.pathname === '/app.webmanifest') return appManifest(url);
      if (url.pathname.startsWith('/i/')) return await invitePage(url, env);
      if (url.pathname === '/.well-known/apple-app-site-association') return appleLinks(env);
      if (url.pathname.startsWith('/n/')) return await notePic(url, env);
      if (url.pathname === '/alexa') return await alexa(request, env, ctx);
      if (url.pathname === '/covers.json' || url.pathname.startsWith('/c/') || url.pathname.startsWith('/s/')) return await ownCover(request, url, env);
      return page('Song of the Day', '<p>This service makes Spotify playlists from the Song of the Day calendar.</p>');
    } catch (e) {
      if (e instanceof HttpError && e.extra.spotify) ctx.waitUntil(noteSpotifyWait(env));   // (Spotify said wait: kept for every request)
      if (e instanceof HttpError) return errorPage(e);
      return page('Something went wrong', '<p>The playlist could not be made. Please try again later.</p><p class="small">' + esc(e.message || String(e)) + '</p>', 500);
    }
  },
  // every 10 minutes (the Worker's cron, set by worker_deploy.py): looks for the day's new song (from 06:00 until it's
  // in; see timerSongs), and the day's song, once it's out on the live calendar, goes to the members as a notification
  // if the site job's call (/push/check) hasn't sent it already (see pushDaily)
  async scheduled(event, env, ctx) {
    ctx.waitUntil((async () => {
      const songs = await timerSongs(env, ctx);
      await pushDaily(env, songs.map(x => ({ id: x.id, a: x.a, title: x.title, artist: x.artist, img: x.img })), { t: 'timer' });
      await uplLook(env).catch(e => console.log('uplLook: ' + (e && e.message)));
    })().catch(e => console.log('scheduled: ' + (e && e.message))));
    ctx.waitUntil(appUpdates(env).catch(e => console.log('appUpdates: ' + (e && e.message))));
    ctx.waitUntil(catalogHeal(env).catch(e => console.log('catalogHeal: ' + (e && e.message))));
    ctx.waitUntil((async () => {   // once: playlists' own files that were only part of the song (the download code before its length check: John, 7 Nov 2026), taken off so they show as missing again (each playlist's record written once)
      if (!env.MEDIA || await env.SOTD.get('ownShort3')) return;
      await env.SOTD.put('ownShort3', String(Date.now()));
      const K = ["ua/hXXaGojDDcooDA/09x9v1o51dbqi5H0u7UGfp.mp3", "ua/hXXaGojDDcooDA/0K2WjMLZYr09LKwurGRYRE.mp3", "ua/hXXaGojDDcooDA/48iWbsOaBUAGzMdoSmqyg1.mp3", "ua/hXXaGojDDcooDA/6qB7YcFpeBEQa0D6QO482y.mp3", "ua/hXXaGojDDcooDA/758wEKVqfYopJIHYWdLVd4.mp3", "ua/hXXaGojDDcooDA/7lQ9vPEIo4caEIctf6bYOi.mp3", "ua/hXXaGojDDcooDA/7rPzEczIS574IgPaiPieS3.mp3", "ua/hXXaGojDDcooDA/7xsr2OppItxPB4whucYr2G.mp3", "uv/hXXaGojDDcooDA/09x9v1o51dbqi5H0u7UGfp.mp4", "uv/hXXaGojDDcooDA/0K2WjMLZYr09LKwurGRYRE.mp4", "uv/hXXaGojDDcooDA/48iWbsOaBUAGzMdoSmqyg1.mp4", "uv/hXXaGojDDcooDA/6qB7YcFpeBEQa0D6QO482y.mp4", "uv/hXXaGojDDcooDA/758wEKVqfYopJIHYWdLVd4.mp4", "uv/hXXaGojDDcooDA/7lQ9vPEIo4caEIctf6bYOi.mp4", "uv/hXXaGojDDcooDA/7rPzEczIS574IgPaiPieS3.mp4"], by = {};
      for (const key of K) { const m = /^u([av])\/([\w-]+)\/([0-9A-Za-z]{22})\.(mp3|mp4)$/.exec(key); if (m) (by[m[2]] = by[m[2]] || []).push([key, m[1], m[3]]); }
      for (const [pid, l] of Object.entries(by)) {
        const data = await uplSongs(env, pid);
        for (const [key, k, t] of l) {
          await env.MEDIA.delete(key).catch(() => {});
          if (data.files && data.files[t]) { delete data.files[t][k]; if (!Object.keys(data.files[t]).length) delete data.files[t]; }
        }
        await env.SOTD.put('upl:' + pid, JSON.stringify(data));
      }
      await env.MEDIA.put('diag/own-short-gone3.json', JSON.stringify({ at: new Date().toISOString(), deleted: K }, null, 1), { httpMetadata: { contentType: 'application/json' } });
    })().catch(e => console.log('ownShort: ' + (e && e.message))));
    ctx.waitUntil((async () => {   // once: the download job's copies that were only part of the song (a preview or a Short; John, 7 Nov 2026), deleted so they come again whole
      if (!env.MEDIA || await env.SOTD.get('shortGone1')) return;
      await env.SOTD.put('shortGone1', String(Date.now()));
      const L = {"0CAfXk7DXMnon4gLudAp7J":"av","5fHovBM24xMRzBESj76O66":"av","45yEy5WJywhJ3sDI28ajTm":"av","6TvxPS4fj4LUdjw2es4g21":"av","5AcM8eAOtusLByGW7XbRA6":"av","5jSz894ljfWE0IcHBSM39i":"av","6sTEBQYNlbv9PXkEeh4Oxs":"av","5BFWQXNGPYadZxNKxY1UfU":"av","0lnxrQAd9ZxbhBBe7d8FO8":"av","53IRnAWx13PYmoVYtemUBS":"av","6ouhsRJ7gExB7LCYjITk4Y":"av","2aI2k39nfa3KFsa4JclQzw":"av","0K2WjMLZYr09LKwurGRYRE":"av","4IQaBvlHjZrpZrIKWXuBxh":"av","67VdmhC89bZ6yK2XRys0lA":"av","254ujtd5Avf5mwBET7FASI":"av","029NqmIySn1kOY305AAhxT":"av","7EZC6E7UjZe63f1jRmkWxt":"av","0f7i3Kkr1B0OPLxezI2vnX":"av","6qB7YcFpeBEQa0D6QO482y":"av","5IdQEHgtmj9th3OkfQKhf8":"av","48iWbsOaBUAGzMdoSmqyg1":"av","7iAqvWLgZzXvH38lA06QZg":"av","6pTIIBntNSvDeUdZS3E8vz":"av","2Xr1dTzJee307rmrkt8c0g":"av","6TcgOgVzfpaXxhnokgbrWR":"av","0Uqs7ilt5kGX9NzFDWTBrP":"av","758wEKVqfYopJIHYWdLVd4":"av","6p6Xq0t9XGkVg04cXWdx8v":"av","7rPzEczIS574IgPaiPieS3":"av","2B3ArhTa6DRox1W4CJZ1dM":"av","3nhJDVdUrm6DnDW4iBfpKz":"av","5D6rk7Zr7FRy5m2fDte5fp":"av","2t77hjgJY4sC9DoX5uaVUD":"av","0s9aeZriwqyBYfxFzsd20R":"av","09x9v1o51dbqi5H0u7UGfp":"av","3Wkho6tesTh4aF80T6evqH":"av","56J7o1BuXpJH2UXqElmsEF":"av","3u7PMsVBQ1viIm8tlhotl6":"av","7hEnqdq7DwBCJxjDd7xMKP":"av","0pxNyJJiL2zZZ5GgJ4JhsQ":"av","1Ekf4LfkzpVbwoUawE9R3E":"av","5mc6EyF1OIEOhAkD0Gg9Lc":"av","5i1YsBTbgsGNyCxMaTkzjS":"av","7egTKPYxbWaaV1jEzb1rHu":"av","2Fk411Ix3qnMG8t8Qa74ZX":"av","7hPO1N8dk3vH8kCYqHhJZX":"av","1YZDkJOFT8xlAXDi8lneb3":"av","7oDDV1cal0M0RIjr4HuuJ9":"av","0rG7kZtNbwLprtijSf3zhd":"av","4ef4J3MEiAYAkhRM8Kj7eI":"av","49cx1hO8AOm5uAsd4WtaEH":"av","2GOQVqZ3uVp7LKVAY1T0mk":"av","3uS7pHvPRtJlyukzNZGpcO":"av","1xOXXYh6lTW8laxlW7JP2J":"av","6hq9bclZdXhlHx3wfPmb4i":"av","5piJiL6aRhvrKBa9YuEfS9":"av","1pX7VlGPmb1P7Mbtlnvt2p":"av","3mwvKOyMmG77zZRunnxp9E":"av","3fJprjhRxTVtOp18EHdNfu":"av","0Oroc0HXQaxs8ONgI7dLnw":"av","0fv2KH6hac06J86hBUTcSf":"a","15kLz0zNFdtsaJdXKSGmTU":"av","2SyNOQgdmhlXbyfPPiEq0r":"av","41emWS6wwkUdCAGc0POqmT":"a","7eQPDU5GY27oHiLOcvhlAM":"av","5KqldkCunQ2rWxruMEtGh0":"a","1e0hllQ23AG0QGFgezgLOq":"av","63uskN0xLezVg4281wzeQn":"av","3WqBMrDMPnbBjBZsa6YrIq":"av","7lQ9vPEIo4caEIctf6bYOi":"av","2cY7WSxFIsZQaoDNJe7Fz3":"av","0F2f7i7oIh81isg06NLv3f":"av","5jvGzaMqJ5u2uwLBRSBWlz":"av","7xsr2OppItxPB4whucYr2G":"a","7lsgvAyCRbQ8SQPFegrOrw":"av","6Yg3gGtLuZTyZ8i4zGUaJa":"av","2Kjt8us8cUqok4wyvuwNKl":"a","6pGqjFdeCbDhlHtRK3VEj7":"a","090muChvJS0dP4sQjHgmBM":"a","0X8aX9hOCK8EKCC3aUTXBf":"av","1THfgXiAHZOb1VZghDi1ih":"a","1Yvt367mJFppdyjIGiJRjX":"a","1qSmZMnXINN0T9pXBrbOnk":"a","2j0fnQgZlvjUFAnhCIY2FP":"a","3kiTOJXpAV3fR5QSSWeyeD":"a","69JQTbSruOyYPN7E96DX3o":"a","6VSEAk5nGZe5t9Duf1Fz8v":"av","676tkPoRYf10KDJIIhRRI6":"av","3MjUtNVVq3C8Fn0MP3zhXa":"av","6vkmPY29nHhybgA8S8AyGb":"a","5DhDfdsf619KwxsweNdulg":"a","0VeiWszWKMO6HkKLv3qUzp":"a","727YRTVI7pKH1uCnXnyZul":"a","4uIjNF84ZbteunNMxr4Xc0":"a","29wO9Q5ERmpRcDS2NvmjFR":"av"}, c = await catalog(env), gone = [];
      for (const [t, kinds] of Object.entries(L)) for (const k of kinds) {
        for (const key of k === 'a' ? ['a/' + t + '.mp3', 'l/' + t + '.m4a'] : ['v/' + t + '.mp4', 'h/' + t + '.mp4']) { await env.MEDIA.delete(key).catch(() => {}); gone.push(key); }
        if (c[t]) { delete c[t][k]; delete c[t]['f' + k]; delete c[t]['t' + k]; if (!Object.keys(c[t]).length) delete c[t]; }
      }
      await env.SOTD.put('mediaCatalog', JSON.stringify(c));
      await env.SOTD.put('r2want', JSON.stringify({ at: Date.now(), key: 'short' }));
      await env.MEDIA.put('diag/short-gone.json', JSON.stringify({ at: new Date().toISOString(), songs: Object.keys(L).length, deleted: gone }, null, 1), { httpMetadata: { contentType: 'application/json' } });
    })().catch(e => console.log('shortGone: ' + (e && e.message))));
    ctx.waitUntil((async () => {   // one-off: Camilla's songs with no preview, and why (6 Nov 2026)
      if (!env.MEDIA || await env.SOTD.get('diagPv2')) return;
      await env.SOTD.put('diagPv2', '1', { expirationTtl: 86400 });
      const U = await uplAll(env), all = (await env.SOTD.get('members', 'json')) || {}, pv = Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {}), cat = await catalog(env), out = [];
      for (const [pid, rec] of Object.entries(U)) {
        if (!/camilla/i.test((all[rec.o] && all[rec.o].name) || '')) continue;
        const data = await uplSongs(env, pid), songs = data.songs || [];
        const view = await uplView(env, all, rec.o, pid, rec), vp = {}; for (const y of view.songs || []) vp[y.id] = y.p || '';
        out.push({ pid, n: uplName(all, rec), st: rec.st, songs: songs.length, nopv: songs.filter(x => !vp[x.id]).map(x => ({ raw: pv[x.id] === undefined ? 'unset' : String(pv[x.id]).slice(0, 80), clip: pv['u' + x.id], id: x.id, t: x.title, a: x.artist, looked: x.id in pv, pv: pv[x.id] === undefined ? 'unset' : pv[x.id], cat: cat[x.id] || null, own: (data.files || {})[x.id] || null, sk: (data.skip || {})[x.id] || '' })) });
      }
      await env.MEDIA.put('diag/pv-camilla.json', JSON.stringify(out, null, 1), { httpMetadata: { contentType: 'application/json' } });
    })().catch(e => console.log('diagPv: ' + (e && e.message))));
    ctx.waitUntil((async () => {   // one-off: where Forced Foot Massage (C O M R) is, and its MP3-from link (6 Nov 2026)
      if (!env.MEDIA || await env.SOTD.get('diagFFM1')) return;
      await env.SOTD.put('diagFFM1', '1', { expirationTtl: 86400 });
      const U = await uplAll(env), all = (await env.SOTD.get('members', 'json')) || {}, out = [];
      for (const [pid, rec] of Object.entries(U)) {
        const data = await uplSongs(env, pid), hit = (data.songs || []).filter(x => /forced foot massage/i.test(x.title || ''));
        for (const x of hit) {
          const plan = await uplDlPlan(env, rec.o, U).catch(() => null);
          out.push({ pid, n: uplName(all, rec), id: x.id, ed: (data.ed || {})[x.id] || null, sk: (data.skip || {})[x.id] || '', files: (data.files || {})[x.id] || null, gone: uplGone(data, x), allowed: plan ? plan.allowed.has(x.id) || plan.john.has(x.id) : null, cat: (await catalog(env))[x.id] || null, twin: twinOf(await twinFiles(env), x, pid) });
        }
      }
      await env.MEDIA.put('diag/ffm.json', JSON.stringify(out, null, 1), { httpMetadata: { contentType: 'application/json' } });
    })().catch(e => console.log('diagFFM: ' + (e && e.message))));   // (every few hours: files in the bucket the catalog lost)
    ctx.waitUntil((async () => {   // John's Spotify log, copied where his read-only key reaches it (diag/splog-john.txt), to look into what his phone sent (6 Nov 2026)
      const o = await env.SOTD.get('owner'), l = o && env.MEDIA ? await env.SOTD.get('splog:' + o) : null;
      if (l && (await env.SOTD.get('splogMirror')) !== String(l.length)) { await env.MEDIA.put('diag/splog-john.txt', JSON.parse(l).join('\n'), { httpMetadata: { contentType: 'text/plain; charset=utf-8' } }); await kvSoft(env, 'splogMirror', String(l.length)); }
    })().catch(e => console.log('splog mirror: ' + (e && e.message))));   // (on its own: it needs nothing from Spotify)
    ctx.waitUntil(spKeep(env).catch(e => console.log('spKeep: ' + (e && e.message))));
    ctx.waitUntil(uplReseen(env).catch(e => console.log('uplReseen: ' + (e && e.message))));   // (once: the playlists' 'none' counts put right)
    ctx.waitUntil(mailDigests(env).catch(e => console.log('mailDigests: ' + (e && e.message))));
    ctx.waitUntil(pushDigests(env).catch(e => console.log('pushDigests: ' + (e && e.message))));
    ctx.waitUntil(weekDigests(env).catch(e => console.log('weekDigests: ' + (e && e.message))));
    ctx.waitUntil((async () => {   // a sample of the weekly email, to John once (he asked, 6 Nov 2026: "can you send a mock email to me to see?"): from his own inbox's last 7 days
      if (!mailReady(env) || await env.SOTD.get('wkSample1')) return;
      await env.SOTD.put('wkSample1', String(Date.now()), { expirationTtl: 30 * 86400 });
      const o = await env.SOTD.get('owner'), all = (await env.SOTD.get('members', 'json')) || {}, m = o && all[o];
      if (!m || !m.email) return;
      const since = Date.now() - 7 * 86400e3, box = ((await env.SOTD.get('inbox:' + o, 'json')) || []).filter(x => x.at >= since).reverse();
      const items = box.map(x => ({ c: x.s ? 'song' : x.f ? 'direct' : 'other', t: x.t || '', b: x.s ? String(x.b || '').split('\n')[0] : x.b || '', u: x.u || SITE_SONGS, i: mailImg(x.c) || mailImg(x.i) || '', at: x.at }));
      if (!items.length) return;
      const first = items.find(x => x.c === 'song' && mailImg(x.i)) || items[0], tint = mailTint(first.u, first.i), title = 'Sample: ' + digestTitle(items, true);
      await sendMail(env, m.email, title, title + '\n\n' + items.map(x => '• ' + x.t + (x.b ? '\n  ' + x.b : '') + '\n  ' + x.u).join('\n\n') + mailFoot(), 'notes', mailShell(digestHtml(items, true, tint), first.u, first.i));
    })().catch(e => console.log('wkSample: ' + (e && e.message))));   // (Monday's, for those who chose once a week)   // (and the one notification a day, for those who chose it)   // (notifications by email, once a day, for those who chose it)
    ctx.waitUntil(catalogTidy(env).catch(e => console.log('catalogTidy: ' + (e && e.message))));   // (files for songs on no calendar or playlist)   // (previews and covers copied here, a few a run)
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
  const spk = (request.headers.get('Cookie') || '').match(/(?:^|;\s*)spkce=([\w-]+)\./);
  if (spk && spk[1] === state) return spLinkDone(request, url, env);   // (a member connecting their own Spotify: its own cookie, not John's link)
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
    ? '<p>Playlists the Worker made for visitors. Each goes at the Worker’s first look at the playlist after it is ' + (KEEP_DAYS === 1 ? 'a day' : KEEP_DAYS + ' days') + ' old.</p><ul>' + waiting.map(m => '<li>' + plLine(m.name, m.n, m.id) + ', made ' + (m.at ? uk(m.at) : 'at an unknown time') + '</li>').join('') + '</ul>'
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
  await spotifyWaiting(env);   // (then spotify() asks nothing until the wait is over)
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

// Spotify can tell the Worker to wait: a 429 with Retry-After (on 28 Oct 2026, 1093 minutes). Until then the Worker asks
// it nothing (KV spotifyWait, and in memory), and the song list is the last one it read (KV songsLast, see songsJson),
// so the calendar, the site job and the Status tab carry on with that meanwhile.
let spotifyUntil = 0;
async function spotifyWaiting(env) {
  const w = await env.SOTD.get('spotifyWait', 'json').catch(() => null);
  if (w && w.until > spotifyUntil) spotifyUntil = w.until;
  return spotifyUntil > Date.now();
}
async function noteSpotifyWait(env) {   // KV spotifyWait {at: when Spotify first said wait, until}: kept after it's over too (the Status tab lists it)
  if (spotifyUntil <= Date.now()) return;
  const w = await env.SOTD.get('spotifyWait', 'json').catch(() => null);
  if (!w || w.until < spotifyUntil)
    await kvSoft(env, 'spotifyWait', JSON.stringify({ until: spotifyUntil, at: w && w.until > Date.now() - 60e3 ? w.at : Date.now() }));
}
function spotifyBusy(retryAfter) {
  return new HttpError(429, 'Spotify is busy right now.', { retryAfter, spotify: true });
}

async function spotify(token, path, method, body) {
  if (spotifyUntil > Date.now()) throw spotifyBusy(String(Math.ceil((spotifyUntil - Date.now()) / 1000)));
  const r = await fetch(path.startsWith('http') ? path : API + path, {
    method: method || 'GET',
    headers: Object.assign({ Authorization: 'Bearer ' + token }, body ? { 'Content-Type': 'application/json' } : {}),
    body: body ? JSON.stringify(body) : undefined
  });
  if (r.status === 429) {
    const secs = parseInt(r.headers.get('Retry-After') || '', 10);
    spotifyUntil = Math.max(spotifyUntil, Date.now() + (secs > 0 ? secs : 30) * 1000);
    throw spotifyBusy(r.headers.get('Retry-After'));
  }
  const j = r.status === 204 ? {} : await r.json().catch(() => ({}));
  if (!r.ok) throw new Error('Spotify ' + r.status + ': ' + ((j.error && (j.error.message || j.error)) || 'request failed'));
  return j;
}

// ---------- the playlist maker ----------

async function playlistItems(token, sp) {   // sp: a playlist's Spotify id (John's when not given)
  const out = [];
  let next = '/playlists/' + (sp || SOURCE_PLAYLIST) + '/items?limit=100';
  while (next) {
    const j = await spotify(token, next);
    for (const it of j.items || []) {
      const t = it.item || it.track;
      if (!t || !t.id) continue;
      const imgs = (t.album && t.album.images) || [];
      const img = imgs.find(i => i.width === 300) || imgs[1] || imgs[0];
      out.push({ a: it.added_at, ab: it.added_by && /^[\w.-]{1,64}$/.test(String(it.added_by.id || '')) ? it.added_by.id : undefined, id: t.id, title: t.name, artist: (t.artists || []).map(x => x.name).join(', '), artists: (t.artists || []).map(x => x.name),
        aids: (t.artists || []).map(x => x.id), album: t.album ? t.album.name : '', alid: t.album ? t.album.id : '', img: img ? img.url : '',
        rd: t.album && /^\d{4}(-\d\d){0,2}$/.test(t.album.release_date || '') ? t.album.release_date : undefined });   // rd: its album's release date (a year for it straight away: years.py)   // aids, alid: Spotify ids, for finding the song facts' sources
    }
    next = j.next;
  }
  return out;
}

async function allowedTracks(env, token, fresh) {
  const kept = await env.SOTD.get('songsLast', 'json');   // the songs on the calendar (see songsJson): no need to ask Spotify
  if (kept && kept.songs && kept.songs.length) return new Set(kept.songs.map(s => s.id).concat(await uplTracks(env)));
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
//
// Only the Worker's timer reads the playlist from Spotify (readPlaylist, from timerSongs: from 06:00 until the day's song
// is in). songs.json, for the calendar, the apps, the admin pages, the site job and the rest, is the list the timer last
// read (KV songsLast), with the newest previews and moods (John, 28 Oct 2026: a calendar visit needn't read Spotify).
// A change made to the playlist after the day's song is in shows the next morning. Only when nothing has been kept yet
// does songs.json read Spotify itself.

// ---- previews and covers kept here, not streamed from Spotify (John, 1 Nov 2026: "Once a song is fully in a calendar
// or playlist, deleting the song from Spotify should never have any effect on it... such a preview and album art always
// being downloaded and not streamed from Spotify"): songs give this Worker's /sp/p/<id>.mp3 and /sp/c/<id>.jpg, which
// serve R2's copy (sp/p/, sp/c/), fetching it from Spotify and keeping it the first time it's asked for. The timer
// copies the ones not asked for yet, a few each run (spKeep; KV spkept: which are kept). A cover asked for at another
// size than any kept is served at one that is, once Spotify has none. Copies no song uses any more are deleted by the
// timer too, 200 looked at a run. ----------
const SP_SIZES = ['ab67616d0000b273', 'ab67616d00001e02', 'ab67616d00004851'];
function keptPreview(id) { return /^u[0-9A-Za-z]{22}$/.test(id) ? LIVE_ORIGIN + '/sp/u/' + id.slice(1) + '.mp3' : /^i\d+$/.test(id) ? LIVE_ORIGIN + '/sp/i/' + id.slice(1) + '.m4a' : LIVE_ORIGIN + '/sp/p/' + id + '.mp3'; }   // (i<id>: Apple's preview, below)
// ---- Apple's preview, for a song Spotify gives none (John, 4 Nov 2026: Ride a White Swan, which Spotify won't preview,
// "I found it easily here"): Apple's public iTunes Search finds the song by its title and artist, the match checked as
// the video search's are (title, artist; not live, a cover or karaoke unless the song is), and the song keeps i<Apple's
// track id> as its preview. /sp/i/<id>.m4a serves R2's copy (sp/i/), fetched from Apple the first time it's asked for.
// Songs Spotify had none for are asked a few a timer run (ITUNES_RUN: Apple allows about 20 searches a minute), once
// (KV pvit { track: day asked }), again after ITUNES_AGAIN days. ----
const ITUNES_RUN = 8, ITUNES_AGAIN = 30;
function itFold(t) { return String(t || '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().replace(/&/g, ' and ').replace(/['\u2019`]/g, '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim(); }
function itMain(t) { return itFold(String(t || '').replace(/\s+-\s+.*$/, '').replace(/\s*[(\[].*?[)\]]/g, '')); }
async function itunesFind(x) {   // { title, artist, artists }: Apple's track id with a preview for it, '' none, or null (Apple didn't answer)
  const title = itMain(x.title), lead = itFold((x.artists && x.artists[0]) || String(x.artist || '').split(',')[0]);
  if (!title || !lead) return '';
  const odd = /\b(live|karaoke|cover|instrumental|tribute|remix|acoustic|demo)\b/;
  for (const country of ['GB', 'US']) {
    const r = await fetch('https://itunes.apple.com/search?' + new URLSearchParams({ term: title + ' ' + lead, entity: 'song', limit: '15', country })).catch(() => null);
    if (!r || !r.ok) return null;
    const j = await r.json().catch(() => null); if (!j) return null;
    const ok = (j.results || []).filter(y => y.previewUrl && y.trackId && itFold(y.artistName).includes(lead) && (' ' + itFold(y.trackName) + ' ').includes(' ' + title + ' ')
      && !(odd.test(itFold(y.trackName + ' ' + y.collectionName)) && !odd.test(itFold(x.title))));
    const full = itFold(x.title), rank = y => itFold(y.trackName) === full ? 0 : itMain(y.trackName) === title ? 1 : 2;   // (the same version first: 'MARC' version, then the song itself)
    ok.sort((a, b) => rank(a) - rank(b));
    if (ok.length) { itLinks[ok[0].trackId] = ok[0].previewUrl; return 'i' + ok[0].trackId; }
  }
  return '';
}
const itLinks = {};   // (Apple's preview links found this run: kept in KV pvurl, as Apple refuses the Worker's own lookups)
async function itLinkSave(env, add) {
  const ok = Object.entries(add || {}).filter(([k, u]) => /^\d{1,15}$/.test(k) && /^https:\/\/[\w.-]+\.(apple|mzstatic)\.com\//.test(String(u)));
  if (!ok.length) return 0;
  const m = (await env.SOTD.get('pvurl', 'json')) || {}; for (const [k, u] of ok) m[k] = u;
  await kvSoft(env, 'pvurl', JSON.stringify(m)); return ok.length;
}
async function itunesFill(env, songs) {   // the songs Spotify gave no preview: Apple's, ITUNES_RUN a run
  const pv = Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {}), asked = (await env.SOTD.get('pvit', 'json')) || {}, today = londonDay();
  const due = t => !asked[t] || (Date.parse(today) - Date.parse(asked[t])) / 864e5 >= ITUNES_AGAIN;
  const todo = [], seen = new Set();
  for (const x of songs) if (x && x.id && pv[x.id] === '-' && !seen.has(x.id) && due(x.id)) { seen.add(x.id); todo.push(x); if (todo.length >= ITUNES_RUN) break; }
  if (!todo.length) return 0;
  let n = 0;
  for (const x of todo) {
    // Spotify's own first, again: its player page sometimes gives none for a song it has one for (John, 4 Nov 2026: Parentheses)
    const sr = await fetch('https://open.spotify.com/embed/track/' + x.id).catch(() => null);
    const sm = sr && sr.ok ? (await sr.text()).match(/https:\/\/p\.scdn\.co\/mp3-preview\/([0-9a-f]{20,})/) : null;
    if (sm) { pv[x.id] = sm[1]; pvMem[x.id] = sm[1]; asked[x.id] = today; n++; continue; }
    const id = await itunesFind(x);
    if (id === null) break;   // (Apple busy: the rest next run)
    asked[x.id] = today;
    if (id) { pv[x.id] = id; pvMem[x.id] = id; n++; }
  }
  await kvSoft(env, 'pvit', JSON.stringify(asked));
  if (n) await kvSoft(env, 'previews', JSON.stringify(pv));
  await itLinkSave(env, itLinks);
  return n;
}
// every song with no preview at once, for the site's Apple previews job (apple_previews.py, its GITHUB_TOKEN; John, 4 Nov
// 2026: "Can you go through every missing preview now"): GET the songs; POST { found: { track: 'i<id>' }, asked: [tracks] }
// the web search services' counts, kept here so they're never lost with a failed save (John, 4 Nov 2026: "what assurance
// do I have that free limit won't be used up"): GET the counts; POST { take: name, most, per: 'day' | 'month' | 'ever' }
// takes one search if it's under its most (the answer says whether), so two jobs at once can't go over between them
async function uplWebSearch(request, env) {
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  if (!await ghJob(request)) return answer(401, { error: 'For the site job only.' });
  const now = new Date(), day = now.toISOString().slice(0, 10), month = day.slice(0, 7), j = (await env.SOTD.get('websearch', 'json')) || {};
  const used = (e, per) => per === 'ever' ? (e.ever || 0) : per === 'day' ? (e.day === day ? e.dn || 0 : 0) : (e.month === month ? e.used || 0 : 0);
  if (request.method === 'POST') {
    const b = await request.json().catch(() => ({})), name = String(b.take || '').slice(0, 20), most = Math.max(0, Number(b.most) || 0), per = ['day', 'month', 'ever'].includes(b.per) ? b.per : 'month';
    if (!name) return answer(400, { error: 'Which?' });
    const e = j[name] || {};
    if (used(e, per) >= most) return answer(200, { ok: false, used: used(e, per) });
    if (e.month !== month) { e.month = month; e.used = 0; }
    if (e.day !== day) { e.day = day; e.dn = 0; }
    e.used = (e.used || 0) + 1; e.dn = (e.dn || 0) + 1; e.ever = (e.ever || 0) + 1;
    j[name] = e; await env.SOTD.put('websearch', JSON.stringify(j));
    return answer(200, { ok: true, used: used(e, per) });
  }
  return answer(200, { counts: j, day, month });
}
async function uplPvNeed(request, env) {
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  if (!await ghJob(request)) return answer(401, { error: 'For the site job only.' });
  const pv = Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {});
  if (request.method === 'POST') {
    const b = await request.json().catch(() => ({})), asked = (await env.SOTD.get('pvit', 'json')) || {}, today = londonDay();
    let n = 0;
    const links = Object.assign({}, b.links || {});
    for (const [t, v0] of Object.entries(b.found || {})) {
      const v = v0 && typeof v0 === 'object' ? v0.i : v0; if (v0 && typeof v0 === 'object' && v0.u) links[String(v).slice(1)] = v0.u;
      if (ID_RE.test(t) && /^i\d{1,15}$/.test(String(v)) && (pv[t] === '-' || !(t in pv))) { pv[t] = v; pvMem[t] = v; n++; }
    }
    await itLinkSave(env, links);
    for (const t of (Array.isArray(b.asked) ? b.asked : []).concat(Object.keys(b.found || {}))) if (ID_RE.test(String(t))) asked[t] = today;
    await kvSoft(env, 'pvit', JSON.stringify(asked));
    if (n) await kvSoft(env, 'previews', JSON.stringify(pv));
    return answer(200, { ok: true, set: n });
  }
  const out = [], seen = new Set();
  const take = x => { if (x && x.id && pv[x.id] === '-' && !seen.has(x.id)) { seen.add(x.id); out.push({ id: x.id, title: x.title || '', artist: x.artist || '', artists: x.artists || [] }); } };
  for (const pid of Object.keys(await uplAll(env))) ((await uplSongs(env, pid)).songs || []).forEach(take);
  (((await env.SOTD.get('songsLast', 'json')) || {}).songs || []).forEach(take);
  const kept = (await env.SOTD.get('pvurl', 'json')) || {}, nolink = [...new Set(Object.values(pv).filter(v => /^i\d+$/.test(v)).map(v => v.slice(1)))].filter(k => !kept[k]);
  return answer(200, { songs: out, nolink });
}
// /upl/clips (POST, Authorization: Bearer ADMIN_KEY; the download job): the songs with no preview anywhere (Spotify's, its
// page, then Apple's all tried: '-' and asked of Apple) whose own MP3 or MP4 is in the bucket, each with the file to cut
// a 30-second clip from; { done: [tracks] } (their clips put at sp/u/<track>.mp3) makes each one's preview that clip
// (John, 5 Nov 2026: "can we make our own 30 second clip").
async function uplClips(request, env) {
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  const bad = await adminOk(request, env, answer); if (bad) return bad;
  const b = await request.json().catch(() => ({})), pv = Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {});
  if (Array.isArray(b.done) && b.done.length) {
    let n = 0;
    for (const t of b.done.map(String)) if (ID_RE.test(t) && (pv[t] === '-' || !(t in pv)) && env.MEDIA && await env.MEDIA.head('sp/u/' + t + '.mp3').catch(() => null)) { pv[t] = 'u' + t; pvMem[t] = pv[t]; n++; }
    if (n) await kvSoft(env, 'previews', JSON.stringify(pv));
    return answer(200, { ok: true, set: n });
  }
  const asked = (await env.SOTD.get('pvit', 'json')) || {}, c = await catalog(env), out = [], seen = new Set();
  const take = (x, files, pid) => {
    if (!x || !x.id || seen.has(x.id) || pv[x.id] !== '-' || !asked[x.id]) return;   // (only once Apple's been asked too)
    const e = c[x.id] || {}, f = files && files[x.id];
    const src = e.a ? 'l/' + x.id + '.m4a|a/' + x.id + '.mp3' : f && f.a ? 'ua/' + pid + '/' + x.id + '.mp3' : e.v ? 'v/' + x.id + '.mp4' : f && f.v ? 'uv/' + pid + '/' + x.id + '.mp4' : '';
    if (src) { seen.add(x.id); out.push({ id: x.id, src }); }
  };
  for (const pid of Object.keys(await uplAll(env))) { const d = await uplSongs(env, pid); (d.songs || []).forEach(x => take(x, d.files, pid)); }
  (((await env.SOTD.get('songsLast', 'json')) || {}).songs || []).forEach(x => take(x, null, ''));
  return answer(200, { songs: out.slice(0, 60) });
}
async function itunesServe(request, env, id) {   // /sp/i/<id>.m4a: R2's copy, or Apple's fetched and kept
  const head = { 'Content-Type': 'audio/mp4', 'Cache-Control': 'public, max-age=31536000, immutable', 'Access-Control-Allow-Origin': '*', 'Accept-Ranges': 'bytes' }, key = 'sp/i/' + id + '.m4a';
  const serve = async () => {
    if (!env.MEDIA) return null;
    const range = request.headers.get('Range'), o = await env.MEDIA.get(key, range ? { range: request.headers } : undefined).catch(() => null);
    if (!o || !o.body) return null;
    if (range && o.range && o.range.length !== undefined) { const st = o.range.offset || 0, len = o.range.length; return new Response(o.body, { status: 206, headers: Object.assign({}, head, { 'Content-Range': 'bytes ' + st + '-' + (st + len - 1) + '/' + o.size, 'Content-Length': String(len) }) }); }
    return new Response(o.body, { headers: Object.assign({}, head, { 'Content-Length': String(o.size) }) });
  };
  const had = await serve(); if (had) return had;
  let u = (((await env.SOTD.get('pvurl', 'json')) || {})[id]) || '', said = 'kept link ' + (u ? 'yes' : 'none') + '; ';   // (the link kept when it was found; else Apple's lookup, which often refuses the Worker)
  if (!u) for (const c of ['GB', 'US']) {
    const lr = await fetch('https://itunes.apple.com/lookup?id=' + id + '&country=' + c, { headers: { Accept: 'application/json' } }).catch(e => ({ ok: false, status: String(e && e.message || e) }));
    said += c + ' ' + lr.status + '; ';
    const j = lr.ok ? await lr.json().catch(() => null) : null;
    u = (j && j.results && j.results[0] && j.results[0].previewUrl) || ''; if (u) break;
  }
  const no = why => new Response('Not here (' + why + ').', { status: 404, headers: { 'Access-Control-Allow-Origin': '*' } });
  if (!u || !/^https:\/\/[\w.-]+\.(apple|mzstatic)\.com\//.test(u)) return no('lookup: ' + said + (u ? 'odd address' : 'no preview'));
  const r = await fetch(u).catch(() => null); if (!r || !r.ok) return no('preview ' + (r ? r.status : 'unreachable'));
  const b = await r.arrayBuffer(); if (!b.byteLength || b.byteLength > 5e6) return new Response('Not here.', { status: 404 });
  if (env.MEDIA) await env.MEDIA.put(key, b, { httpMetadata: { contentType: 'audio/mp4' } }).catch(() => {});
  return (await serve()) || new Response(b, { headers: head });
}
function keptCover(u) { const m = /^https:\/\/i\.scdn\.co\/image\/([0-9a-f]{40})$/.exec(u || ''); return m ? LIVE_ORIGIN + '/sp/c/' + m[1] + '.jpg' : u; }
async function spKept(request, url, env) {
  const mi = /^\/sp\/i\/(\d{1,15})\.m4a$/.exec(url.pathname); if (mi) return await itunesServe(request, env, mi[1]);
  const mu = /^\/sp\/u\/([0-9A-Za-z]{22})\.mp3$/.exec(url.pathname);   // (a 30-second clip made here, from the song's own file: no preview anywhere else)
  if (mu && env.MEDIA) {
    const range = request.headers.get('Range'), o = await env.MEDIA.get('sp/u/' + mu[1] + '.mp3', range ? { range: request.headers } : undefined).catch(() => null);
    if (!o || !o.body) return new Response('Not here.', { status: 404, headers: { 'Access-Control-Allow-Origin': '*' } });
    const h = { 'Content-Type': 'audio/mpeg', 'Cache-Control': 'public, max-age=86400', 'Access-Control-Allow-Origin': '*', 'Accept-Ranges': 'bytes' };
    if (range && o.range && o.range.length !== undefined) { const st = o.range.offset || 0, len = o.range.length; return new Response(o.body, { status: 206, headers: Object.assign(h, { 'Content-Range': 'bytes ' + st + '-' + (st + len - 1) + '/' + o.size, 'Content-Length': String(len) }) }); }
    return new Response(o.body, { headers: Object.assign(h, { 'Content-Length': String(o.size) }) });
  }
  const m = /^\/sp\/(p|c)\/([0-9a-f]{20,64})\.(mp3|jpg)$/.exec(url.pathname);
  if (!m || (m[1] === 'p') !== (m[3] === 'mp3')) return new Response('Not here.', { status: 404 });
  const kind = m[1], id = m[2], src = kind === 'p' ? 'https://p.scdn.co/mp3-preview/' + id : 'https://i.scdn.co/image/' + id;
  if (!env.MEDIA) return Response.redirect(src, 302);
  const type = kind === 'p' ? 'audio/mpeg' : 'image/jpeg', key = 'sp/' + kind + '/' + id + '.' + m[3];
  const head = { 'Content-Type': type, 'Cache-Control': 'public, max-age=31536000, immutable', 'Access-Control-Allow-Origin': '*', 'Accept-Ranges': 'bytes' };
  const serve = async k => {
    const range = request.headers.get('Range');
    const o = await env.MEDIA.get(k, range ? { range: request.headers } : undefined).catch(() => null);
    if (!o || !o.body) return null;
    if (range && o.range && o.range.length !== undefined) {
      const st = o.range.offset || 0, len = o.range.length;
      return new Response(o.body, { status: 206, headers: Object.assign({}, head, { 'Content-Range': 'bytes ' + st + '-' + (st + len - 1) + '/' + o.size, 'Content-Length': String(len) }) });
    }
    return new Response(o.body, { headers: Object.assign({}, head, { 'Content-Length': String(o.size) }) });
  };
  const had = await serve(key); if (had) return had;
  const got = await spCopy(env, kind, id);
  if (got) return (await serve(key)) || new Response(got, { headers: head });
  if (kind === 'c') for (const z of SP_SIZES) {   // (gone from Spotify: the same picture at a size that's kept)
    if (!id.startsWith(z)) continue;
    for (const z2 of SP_SIZES) if (z2 !== z) { const r = await serve('sp/c/' + z2 + id.slice(16) + '.jpg'); if (r) return r; }
  }
  return new Response('Not here.', { status: 404, headers: { 'Access-Control-Allow-Origin': '*' } });
}
async function spCopy(env, kind, id) {   // Spotify's copy into R2: its bytes, or null (not there)
  const r = await fetch(kind === 'p' ? 'https://p.scdn.co/mp3-preview/' + id : 'https://i.scdn.co/image/' + id).catch(() => null);
  if (r && kind === 'p' && [400, 403, 404, 410].includes(r.status)) await pvDead(env, id);   // (gone from Spotify, never kept: the song has no preview now)
  if (!r || !r.ok) return null;
  const b = await r.arrayBuffer();
  if (!b.byteLength || b.byteLength > 5e6) return null;
  await env.MEDIA.put('sp/' + kind + '/' + id + (kind === 'p' ? '.mp3' : '.jpg'), b, { httpMetadata: { contentType: kind === 'p' ? 'audio/mpeg' : 'image/jpeg' } }).catch(() => {});
  return b;
}
// a preview Spotify no longer has, with no copy kept here: its songs are marked as having none (previews: '-'), so the
// calendar shows them as video only and plays their video in its place (John, 1 Nov 2026: a song taken off Spotify
// kept its preview link, so the mini player "doesn't recognise it's missing - nothing happens at all when play is pressed")
async function pvDead(env, id) {
  const pv = (await env.SOTD.get('previews', 'json')) || {};
  let n = 0;
  for (const t of Object.keys(pv)) if (pv[t] === id) { pv[t] = '-'; pvMem[t] = '-'; n++; }
  if (n) await kvSoft(env, 'previews', JSON.stringify(pv));
}
const SP_KEEP_RUN = 20;   // (copies a timer run: well inside a run's requests)
async function spKeep(env) {   // the previews and covers not kept yet, from John's songs and every member's playlist; and copies no song uses, deleted
  if (!env.MEDIA) return;
  const kept = (await env.SOTD.get('spkept', 'json')) || {}, pv = Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {});
  const now = Date.now(), want = [], seen = new Set(), used = new Set();   // used: every preview id, and every cover by its picture (any size)
  const add = (k, id) => { const key = k + id, e = kept[key]; if (e !== 1 && !(e < 0 && now + e < 7 * 864e5) && !seen.has(key)) { seen.add(key); want.push([k, id]); } };   // (1 kept; -when: Spotify had none then, asked again a week on)
  let songsSeen = 0; const nopv = [], cat0 = await catalog(env), twins = {};
  const take = (x, pid, own) => {
    songsSeen++;
    if (own && (own.a || own.v) && !(cat0[x.id] && (cat0[x.id].a || cat0[x.id].v))) { const k = songTitleKey(x); if (k) { const l = twins[k] = twins[k] || []; if (l.length < 8 && !l.some(e => e.id === x.id && e.pid === pid)) l.push({ id: x.id, n: songNames(x), pid, a: own.a ? 1 : 0, v: own.v ? 1 : 0 }); } }   // (a member's own uploads count too: Gauja's Aja, John, 4 Nov 2026)
    { const c = cat0[x.id]; if (c && (c.a || c.v)) { const k = songTitleKey(x); if (k) { const l = twins[k] = twins[k] || []; if (!l.some(e => e.id === x.id) && l.length < 8) { l.push({ id: x.id, n: songNames(x) }); const sc = e => e.pid ? (e.a || 0) + (e.v || 0) - .5 : (cat0[e.id] && cat0[e.id].a ? 1 : 0) + (cat0[e.id] && cat0[e.id].v ? 1 : 0); l.sort((p, q) => sc(q) - sc(p)); } } } }   // (each title's copies with files, the most files first)
    if (pv[x.id] && pv[x.id] !== '-' && !/^[iu]/.test(pv[x.id])) { add('p', pv[x.id]); used.add('p' + pv[x.id]); }   // (Apple's are fetched when first asked for)
    if (pv[x.id] === '-') nopv.push(x);
    const m = /\/image\/([0-9a-f]{40})$/.exec(x.img || ''); if (m) { add('c', m[1]); used.add('c' + m[1].slice(16)); }
  };
  for (const pid of Object.keys(await uplAll(env))) { const d0 = await uplSongs(env, pid); (d0.songs || []).forEach(x => take(x, pid, (d0.files || {})[x.id])); }   // (members' first: theirs can't be had again)
  const last = (((await env.SOTD.get('songsLast', 'json')) || {}).songs || []);
  last.forEach(take);
  await itunesFill(env, nopv).catch(e => console.log('itunesFill: ' + (e && e.message)));
  if (songsSeen) await kvSoft(env, 'twinfiles', JSON.stringify(twins));   // (songs Spotify gives no preview: Apple's)
  let n = 0;
  for (const [k, id] of want.slice(0, SP_KEEP_RUN)) {
    const there = await env.MEDIA.head('sp/' + k + '/' + id + (k === 'p' ? '.mp3' : '.jpg')).catch(() => null);
    kept[k + id] = there || await spCopy(env, k, id) ? 1 : -now; n++;
  }
  // copies no song uses any more (its playlist deleted, or the song taken off): a page of R2's sp/ a run, on from where
  // the last stopped (KV spcursor). Only with John's songs read, so a failed read never empties it.
  if (last.length && songsSeen >= last.length && Object.keys(pv).length) {   // (and the previews read: else a song's preview copy would look unused)
    const cur = (await env.SOTD.get('spcursor')) || undefined;
    const l = await env.MEDIA.list({ prefix: 'sp/', cursor: cur, limit: 200 }).catch(() => null);
    if (l) {
      for (const o of l.objects || []) {
        const m = /^sp\/(p|c)\/([0-9a-f]+)\.(mp3|jpg)$/.exec(o.key); if (!m) continue;
        const ref = m[1] === 'p' ? 'p' + m[2] : 'c' + m[2].slice(16);
        if (used.has(ref)) continue;
        await env.MEDIA.delete(o.key).catch(() => {}); delete kept[m[1] + m[2]]; n++;
      }
      if (l.truncated && l.cursor) await env.SOTD.put('spcursor', l.cursor); else await env.SOTD.delete('spcursor');
    }
  }
  if (n) await env.SOTD.put('spkept', JSON.stringify(kept));
}

const SONG_FIELDS = ['a', 'id', 'title', 'artist', 'artists', 'aids', 'album', 'alid', 'img', 'rd'];   // (rd: the album's release date, where Spotify or Exportify gave it)
function composeSongs(items, pv, tags) {   // each song as songs.json gives it: n, the playlist's fields, p (preview), tag (moods)
  return items.map((it, i) => {
    const x = { n: i + 1 };
    for (const k of SONG_FIELDS) if (it[k] !== undefined) x[k] = it[k];
    if (pv[it.id] && pv[it.id] !== '-') x.p = keptPreview(pv[it.id]);
    if (x.img) x.img = keptCover(x.img);
    if (it.sx) x.sx = 1;   // (gone from Spotify since: kept here)
    const t = tags[it.id];
    if (t && (t.m || (t.g || []).length)) x.tag = { m: t.m, d: t.d, g: t.g };
    return x;
  });
}
async function readPlaylist(env, ctx) {   // Spotify's playlist now (the timer only); kept in KV songsLast; starts the site job when it changed
  const items = await playlistItems(await getToken(env));
  let pv = Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {});
  // a song added since the last look-up gets its preview now, so the calendar never shows it without one
  if (items.some(it => !(it.id in pv))) pv = await fillPreviews(env, items, 3, pv);
  const tags = (await env.SOTD.get('tags', 'json')) || {};
  const songs = composeSongs(items, pv, tags);
  if (items.some(it => !(it.id in pv))) ctx.waitUntil(fillPreviews(env, items, 6).catch(() => {}));
  ctx.waitUntil(afterLook(env, items).catch(e => console.log('afterLook: ' + (e && e.message))));
  const at = new Date().toISOString();
  await keepLast(env, at, songs);
  return { at, songs };
}
async function songsJson(request, env, ctx) {
  const cacheKey = new Request(new URL('/songs.json?v=4', request.url).toString());   // bump to drop cached copies after a format change
  const hit = await caches.default.match(cacheKey);
  if (hit) return hit;
  const kept = await env.SOTD.get('songsLast', 'json');
  let at, songs;
  if (kept && kept.songs && kept.songs.length) {
    const pv = Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {});
    const tags = (await env.SOTD.get('tags', 'json')) || {};
    at = kept.at; songs = composeSongs(kept.songs, pv, tags);
  } else {
    ({ at, songs } = await readPlaylist(env, ctx));   // nothing kept yet
  }
  const body = { at, songs };
  if (await spotifyWaiting(env)) body.stale = { since: at, until: new Date(spotifyUntil).toISOString() };   // (Spotify said wait)
  const res = new Response(JSON.stringify(body), { headers: {
    'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'public, max-age=600'
  } });
  ctx.waitUntil(caches.default.put(cacheKey, res.clone()));
  return res;
}
async function keepLast(env, at, songs) {   // the list as last read (written only when it changes)
  const s = JSON.stringify(songs), old = await env.SOTD.get('songsLast', 'json');
  if (old && JSON.stringify(old.songs) === s) return;
  await kvSoft(env, 'songsLast', JSON.stringify({ at, songs }));
}

// ---------- song facts John has signed off ----------

function appOrigin(request) {   // the site's own pages, and the apps showing them (John's own songs, edited on the calendar there too)
  const o = request.headers.get('Origin') || '';
  return /^(https:\/\/cooperking\.github\.io|capacitor:\/\/localhost|https?:\/\/localhost)$/.test(o) ? o : ADMIN_ORIGIN;
}
function factsHeaders(origin) {
  return { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Authorization, Content-Type, X-Device, X-App', 'Access-Control-Max-Age': '86400' };
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

// POST /facts/redo { track } (John's admin password): a song of his already out, its facts written again (John, 1 Nov 2026:
// "I published today's song before facts came through. Now I can't see any facts"): song-tags.yml with facts_redo
// puts it in _sotd/data/facts-redo.json and starts the facts task, whose facts then wait on the Songs tab as a new song's.
async function factsRedo(request, env) {
  const h = factsHeaders(appOrigin(request));
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: h });
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: h });
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  if (!env.ADMIN_KEY) return answer(503, { error: 'The ADMIN_KEY secret is not set on the Worker.' });
  const given = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  if ((!given || await sha256hex(given) !== await sha256hex(env.ADMIN_KEY)) && !await ownerSignedIn(request, env)) return answer(401, { error: 'Wrong password.' });
  const body = await request.json().catch(() => ({})), track = String((body && body.track) || '');
  if (!ID_RE.test(track)) return answer(400, { error: 'Which song?' });
  if (!env.GH_TOKEN) return answer(503, { error: 'The GH_TOKEN secret is not set.' });
  const r = await fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/' + JOB + '/dispatches', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + env.GH_TOKEN, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'SongOfTheDayWorker', 'Content-Type': 'application/json' },
    body: JSON.stringify({ ref: 'main', inputs: { facts_redo: track } })
  }).catch(e => ({ status: 0, text: async () => String(e) }));
  if (r.status !== 204) return answer(502, { error: 'GitHub answered ' + r.status + ': ' + (await r.text()).slice(0, 160) });
  return answer(200, { ok: true });
}
async function factsSignoff(request, env, videoOnly) {   // (videoOnly: /facts/video, a song's video saved on its own, its facts still waiting: John, 5 Nov 2026)
  const h = factsHeaders(appOrigin(request));
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: h });
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: h });
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  if (!env.ADMIN_KEY) return answer(503, { error: 'The ADMIN_KEY secret is not set on the Worker.' });
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const failKey = 'factsfail:' + ip + ':' + new Date().toISOString().slice(0, 13);
  const fails = Number(await env.SOTD.get(failKey)) || 0;
  if (fails >= 20) return answer(429, { error: 'Too many wrong passwords from here this hour.' });
  const given = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  if ((!given || await sha256hex(given) !== await sha256hex(env.ADMIN_KEY)) && !await ownerSignedIn(request, env)) {   // (or John signed in on the calendar)
    await env.SOTD.put(failKey, String(fails + 1), { expirationTtl: 7200 });
    return answer(401, { error: 'Wrong password.' });
  }
  const body = await request.json().catch(() => null);
  if (videoOnly) {
    const t = String((body && body.track) || ''), v = String((body && body.video) || '');
    if (!ID_RE.test(t) || !/^([\w-]{11})?$/.test(v)) return answer(400, { error: 'Which song, and which video?' });
    const picks = (await env.SOTD.get('videoPicks', 'json')) || {}, at = new Date().toISOString();
    picks[t] = { v, at }; await env.SOTD.put('videoPicks', JSON.stringify(picks));
    return answer(200, { ok: true, at, job: await startFactsJob(env, null) });
  }
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
  const job = await startFactsJob(env, at);
  return answer(200, { ok: true, signed: n, at, job });
}

async function startTvCopy(env, track, job) {   // video-tv.yml for one video just uploaded (or audio-light.yml for one MP3)
  if (!env.GH_TOKEN) return 'The GH_TOKEN secret is not set.';
  const r = await fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/' + (job || 'video-tv.yml') + '/dispatches', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + env.GH_TOKEN, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'SongOfTheDayWorker', 'Content-Type': 'application/json' },
    body: JSON.stringify({ ref: 'main', inputs: { track } })
  }).catch(e => ({ status: 0, text: async () => String(e) }));
  return r.status === 204 ? 'started' : 'GitHub answered ' + r.status;
}

async function startFactsJob(env, at) {   // at: the sign-off's time, which the job waits to see in /facts/signed
  if (!env.GH_TOKEN) return 'The GH_TOKEN secret is not set.';
  const r = await fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/' + FACTS_JOB + '/dispatches', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + env.GH_TOKEN, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'SongOfTheDayWorker', 'Content-Type': 'application/json' },
    body: JSON.stringify(at ? { ref: 'main', inputs: { at } } : { ref: 'main' })
  }).catch(e => ({ status: 0, text: async () => String(e) }));
  return r.status === 204 ? 'started' : 'GitHub answered ' + r.status + ': ' + (await r.text()).slice(0, 200);
}

// ---------- the admin page's Status tab: where each new song has got to, and how the GitHub jobs went ----------
//
// John, 28 Oct 2026: "I need a way to see if something has failed in my admin page otherwise I'm just left waiting".
// Worked out when the tab asks (admin op 'status'), nothing kept: the playlist (Spotify); the site's repository, from
// its public files on GitHub (each song's sources, the facts asked for and how the last call to the facts task went,
// the facts out on the calendar, the videos); the Worker's own start of the site job (KV job); and the latest run of
// each GitHub job, with the step a failed one stopped at. Each new song's way to the calendar is a row of steps, each
// done, waiting or failed (st: ok, wait, bad); `problems` counts the failed steps, failed jobs and a failed call to the
// facts task, less those John has marked as dealt with. The files are read as they are at main's latest commit (raw
// files under the branch's name can be five minutes old); his sign-offs come from the Worker's own record (KV
// factsSigned), so one shows as soon as he makes it.
//
// The notifications sent go with it (John, 28 Oct 2026: "can we see when push notifications were sent in Status?"): each
// song's own as its last step, and the list of the last 12 (pushLog), one some devices didn't get counted as failed.
//
// "Dealt with" (John, 28 Oct 2026): each failed thing has a key (a song's step, a job's run, a call to the facts task,
// a notification; a song's failed call shares the call's key), and op 'statusack' { key, on } keeps or drops it in KV statusAck
// ({ key: when }, each kept 30 days). A new run, call or song comes with a new key, so it shows again.
const RAW = 'https://raw.githubusercontent.com/' + REPO + '/';
const ACK_DAYS = 30;
const STALL = { job: 20 * 60e3, ask: 20 * 60e3, facts: 60 * 60e3 };   // how long each step may take before it counts as stuck
async function mainSha(env) {   // main's latest commit, so the files read are the newest
  if (env.GH_TOKEN) try {
    const r = await fetch('https://api.github.com/repos/' + REPO + '/commits/main', { headers: { Authorization: 'Bearer ' + env.GH_TOKEN,
      Accept: 'application/vnd.github.sha', 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'SongOfTheDayWorker' } });
    const sha = r.ok ? (await r.text()).trim() : '';
    if (/^[0-9a-f]{40}$/.test(sha)) return sha;
  } catch (e) {}
  return 'main';
}
async function rawJson(path, empty, ref) {
  const r = await fetch(RAW + (ref || 'main') + '/' + path + (ref && ref !== 'main' ? '' : '?t=' + Date.now()), { headers: { 'User-Agent': 'SongOfTheDayWorker' } });
  if (r.status === 404 && empty !== undefined) return empty;
  if (!r.ok) throw new Error(path + ': GitHub answered ' + r.status);
  return r.json();
}
async function ghApi(env, path, repo) {
  const r = await fetch('https://api.github.com/repos/' + (repo || REPO) + path, { headers: { Authorization: 'Bearer ' + env.GH_TOKEN,
    Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'SongOfTheDayWorker' } });
  if (!r.ok) throw new Error(repo && repo !== REPO && (r.status === 404 || r.status === 403)
    ? 'The jobs of ' + repo.split('/')[1] + ' can’t be read: the Worker’s GitHub key (GH_TOKEN) has no access to it. Give that key Actions: Read on ' + repo.split('/')[1] + ' too.'
    : 'GitHub answered ' + r.status + ' for ' + path.split('?')[0]);
  return r.json();
}
// Cloudflare's own numbers for the Status tab (John, 2 Nov 2026: "what's happening in my cloudflare?"): the last 24
// hours of the Worker (requests, errors, by how they ended; CPU), KV and R2 (operations; R2's storage), from Cloudflare's
// GraphQL analytics with a read-only token (CF_STATS_TOKEN, Account Analytics: Read; CF_ACCOUNT its account), kept 10
// minutes (KV cfstats). Each part by itself, so one Cloudflare won't give doesn't hide the others.
async function cfStats(env) {
  if (!env.CF_STATS_TOKEN || !env.CF_ACCOUNT) return { none: true };
  const had = await env.SOTD.get('cfstats', 'json').catch(() => null);
  if (had && Date.now() - had.got < 10 * 60e3) return had;
  const end = new Date(), start = new Date(end - 864e5), day = start.toISOString().slice(0, 10);
  const q = async (body) => {
    const r = await fetch('https://api.cloudflare.com/client/v4/graphql', { method: 'POST', headers: { Authorization: 'Bearer ' + env.CF_STATS_TOKEN, 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: 'query($a: String!, $s: Time!, $e: Time!, $d: Date!) { viewer { accounts(filter: { accountTag: $a }) { ' + body + ' } } }',
        variables: { a: env.CF_ACCOUNT, s: start.toISOString(), e: end.toISOString(), d: day } }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || (j.errors && j.errors.length)) throw new Error((j.errors && j.errors[0] && j.errors[0].message) || 'Cloudflare answered ' + r.status);
    return ((j.data && j.data.viewer && j.data.viewer.accounts) || [])[0] || {};
  };
  const out = { got: Date.now(), since: start.toISOString(), errs: [] };
  const part = (k, p) => p.then(v => { out[k] = v; }).catch(e => { out.errs.push(k + ': ' + (e && e.message || e)); });
  await Promise.all([
    part('worker', q('w: workersInvocationsAdaptive(limit: 50, filter: { datetime_geq: $s, datetime_leq: $e }) { sum { requests errors subrequests } quantiles { cpuTimeP50 cpuTimeP99 } dimensions { scriptName status } }').then(a => {
      const by = {}, t = { requests: 0, errors: 0, subrequests: 0, cpu50: 0, cpu99: 0, scripts: {} };
      for (const g of a.w || []) {
        const n = g.sum.requests || 0; t.requests += n; t.errors += g.sum.errors || 0; t.subrequests += g.sum.subrequests || 0;
        by[g.dimensions.status] = (by[g.dimensions.status] || 0) + n; t.scripts[g.dimensions.scriptName] = (t.scripts[g.dimensions.scriptName] || 0) + n;
        t.cpu50 = Math.max(t.cpu50, (g.quantiles && g.quantiles.cpuTimeP50) || 0); t.cpu99 = Math.max(t.cpu99, (g.quantiles && g.quantiles.cpuTimeP99) || 0);
      }
      t.by = by; return t;
    })),
    part('kv', q('k: kvOperationsAdaptiveGroups(limit: 50, filter: { datetime_geq: $s, datetime_leq: $e }) { sum { requests } dimensions { actionType } }').then(a => {
      const by = {}; for (const g of a.k || []) by[g.dimensions.actionType] = (by[g.dimensions.actionType] || 0) + (g.sum.requests || 0); return by;
    })),
    part('r2ops', q('o: r2OperationsAdaptiveGroups(limit: 100, filter: { datetime_geq: $s, datetime_leq: $e }) { sum { requests } dimensions { actionType } }').then(a => {
      const by = {}; for (const g of a.o || []) by[g.dimensions.actionType] = (by[g.dimensions.actionType] || 0) + (g.sum.requests || 0); return by;
    })),
    part('r2', q('r: r2StorageAdaptiveGroups(limit: 20, filter: { date_geq: $d }) { max { payloadSize metadataSize objectCount } dimensions { bucketName } }').then(a => {
      const by = {}; for (const g of a.r || []) { const b = by[g.dimensions.bucketName] = by[g.dimensions.bucketName] || { bytes: 0, objects: 0 }; b.bytes = Math.max(b.bytes, (g.max.payloadSize || 0) + (g.max.metadataSize || 0)); b.objects = Math.max(b.objects, g.max.objectCount || 0); } return by;
    }))]);
  await env.SOTD.put('cfstats', JSON.stringify(out)).catch(() => {});
  return out;
}
const BAD_RUN = ['failure', 'timed_out', 'startup_failure', 'action_required'];
async function jobRuns(env, repo) {   // every run of the last two days (John, 2 Nov 2026: "every time a job runs, any failures on the jobs that do run"), and each job's latest however old; a failed one with the steps it failed at (repo: another of John's, its name before each)
  const j = await ghApi(env, '/actions/runs?per_page=100', repo), pre = repo && repo !== REPO ? repo.split('/')[1] + ': ' : '';
  const seen = {}, out = [], since = Date.now() - 2 * 864e5;
  for (const r of j.workflow_runs || []) {
    const latest = !seen[r.workflow_id], at = r.run_started_at || r.created_at;
    seen[r.workflow_id] = true;
    if (!latest && Date.parse(at) < since) continue;
    const failed = r.status === 'completed' && BAD_RUN.includes(r.conclusion);
    // a problem while it's the job's latest run; an earlier failure is shown, with whether a later run worked
    out.push({ id: r.id, name: pre + r.name, repo: pre ? repo : undefined, st: r.status, res: r.conclusion || '', at, end: r.updated_at,
      url: r.html_url, event: r.event, n: r.run_number, bad: latest && failed, failed, later: !latest });
  }
  await Promise.all(out.filter(r => r.failed).slice(0, 10).map(async r => {
    try {
      const jj = await ghApi(env, '/actions/runs/' + r.id + '/jobs', repo);
      r.steps = [];
      for (const job of jj.jobs || []) for (const s of job.steps || []) if (BAD_RUN.includes(s.conclusion)) r.steps.push(s.name);
      if (!r.steps.length) r.steps = (jj.jobs || []).filter(x => BAD_RUN.includes(x.conclusion)).map(x => x.name);
    } catch (e) {}
  }));
  if (repo === R2_REPO && env.MEDIA) {   // the download job's public log says nothing (John, 4 Nov 2026): why it stopped, from its own log in the bucket
    const r = out.find(x => x.failed && !x.later);
    if (r) try {
      const o = await env.MEDIA.get('playlist-r2/last-run.log');
      if (o && Math.abs(o.uploaded.getTime() - Date.parse(r.end)) < 15 * 60e3) r.why = (await o.text()).split('\n').map(x => x.trim()).filter(x => x && !/^\+ |^(Reading|Building|Get:|Hit:|Fetched|Selecting|Preparing|Unpacking|Setting up|Processing)/.test(x)).slice(-12);
    } catch (e) {}
  }
  return out;
}
async function runHistory(env, wf, n) {   // a job's recent runs, to say which run did each step of a song
  const j = await ghApi(env, '/actions/workflows/' + wf + '/runs?per_page=' + n);
  return (j.workflow_runs || []).map(r => ({ id: r.id, n: r.run_number, name: r.name, st: r.status, res: r.conclusion || '',
    at: r.run_started_at || r.created_at, end: r.updated_at, url: r.html_url }));
}
function songSteps(it, d, now) {   // one song's way to the calendar: each step with when, and who or which run did it (by)
  const { job, runs, S, asked, task, F, V, signedNow, log, pushSent, newest, P, cat, YT, siteRuns, factsRuns } = d;
  const added = Date.parse(it.a) || 0, id = it.id, steps = [];
  const step = (k, t, st, say, extra) => steps.push(Object.assign({ k, t, st, say: say || '' }, st === 'bad' ? { key: 's:' + id + ':' + k } : {}, extra || {}));
  // which run: the one the job wrote down (runinfo.py, from 28 Oct 2026), else the run that fits by its time (guess)
  const all = siteRuns.concat(factsRuns);
  const runBy = (r, guess) => r ? { t: (r.name || r.job || 'Job on GitHub') + ', run ' + r.n, url: r.url || 'https://github.com/' + REPO + '/actions/runs/' + r.id, guess: !!guess } : null;
  const recorded = rec => rec && rec.id ? runBy(Object.assign({ name: rec.job, n: rec.n, id: rec.id }, all.find(r => r.id === rec.id) || {})) : null;
  const firstOk = (list, from, within) => list.filter(r => r.st === 'completed' && r.res === 'success' && Date.parse(r.at) >= from - 60e3
    && Date.parse(r.at) <= from + within).sort((a, b) => (a.at < b.at ? -1 : 1))[0] || null;   // (none if the right one has gone from the list)
  const during = (list, t) => list.find(r => Date.parse(r.at) <= t && t <= Date.parse(r.end) + 120e3) || null;
  const iso = t => t ? new Date(t).toISOString() : undefined;
  const siteFound = firstOk(siteRuns, added, 6 * 3600e3);
  const tags = runs.find(r => r.name === 'Song moods and genres') || null;
  const tagsRunning = !!tags && tags.st !== 'completed';
  const out = !!(F.songs && F.songs[id]) || !!(F.signed && F.signed[id]);
  const src = !!(S[id]), askedAt = asked[id] ? Date.parse(asked[id]) : 0, waiting = !!P[id];   // waiting: written and checked, not signed off
  const mine = task && (task.songs || []).includes(id) ? task : null;   // the last call to the facts task, if it was for this song
  step('added', 'In the playlist', 'ok', '', { at: it.a, by: { t: 'you, on Spotify' } });
  // 2. the site job (song-tags.yml): its video, year, moods and the sources its facts are written from
  if (src || out) {
    const rec = S[id] && S[id].run;
    step('job', 'Found by the site job', 'ok', V[id] ? 'Video found' : 'No video found yet',
      { at: (rec && rec.at) || (siteFound ? siteFound.end : undefined), by: recorded(rec) || runBy(siteFound, true) });
  }
  else {
    const jobAt = Date.parse(job.at || '') || 0;
    if (job.err && (job.tried || 0) >= added) step('job', 'Found by the site job', 'bad', 'The Worker couldn’t start the site job: ' + job.err, { at: new Date(job.tried).toISOString() });
    else if (tagsRunning) step('job', 'Found by the site job', 'wait', 'The site job is running now', { at: tags.at, by: runBy(tags) });
    else if (jobAt >= added && tags && tags.bad) step('job', 'Found by the site job', 'bad', 'The site job failed', { at: tags.end || tags.at, by: runBy(tags) });
    else if (jobAt >= added && now - jobAt > STALL.job) step('job', 'Found by the site job', 'bad', 'The site job started but didn’t save it', { at: job.at, by: runBy(tags) });
    else if (jobAt >= added) step('job', 'Found by the site job', 'wait', 'The site job has started', { at: job.at });
    else step('job', 'Found by the site job', 'wait', 'The site job starts at the Worker’s next look at the playlist (every 10 minutes from 06:00)');
  }
  // the YouTube playlist: a song joins as soon as it has a video, published or not (John, 28 Oct 2026; youtube_playlist.py)
  if (YT[id]) step('yt', 'On the YouTube playlist', 'ok', '', { at: YT[id].at || (siteFound ? siteFound.end : undefined), by: recorded(YT[id].run) || runBy(siteFound, true) });
  else if (!V[id]) step('yt', 'On the YouTube playlist', 'wait', 'When a video is found for it');
  else step('yt', 'On the YouTube playlist', 'wait', 'Goes on at the site job’s next run (YouTube allows a few a day)');
  // 3. the facts task asked for this song's facts (by the site job, at its end)
  const askBy = recorded(mine && mine.run) || runBy(askedAt ? during(siteRuns, askedAt) : null, true);
  if (out || waiting) step('ask', 'Facts task started', 'ok', '', Object.assign(askedAt ? { at: asked[id] } : {}, { by: askBy }));
  else if (askedAt) step('ask', 'Facts task started', 'ok', '', { at: asked[id], url: mine && mine.ok ? mine.session || '' : '', by: askBy });
  else if (!src) step('ask', 'Facts task started', 'wait', '');
  else if (mine && !mine.ok) step('ask', 'Facts task started', 'bad', 'It couldn’t be started: ' + mine.said,
    { at: mine.at, key: 't:' + mine.at, task: true, by: recorded(mine.run) || runBy(during(siteRuns, Date.parse(mine.at)), true) });   // (the call's own key)
  else if (tagsRunning) step('ask', 'Facts task started', 'wait', 'Asked for at the end of the site job', { by: runBy(tags) });
  else if (tags && now - Date.parse(tags.end || tags.at) > STALL.ask) step('ask', 'Facts task started', 'bad', 'The site job didn’t ask for its facts', { at: tags.end || tags.at, by: runBy(tags) });
  else step('ask', 'Facts task started', 'wait', '');
  // 4. the facts written and checked: on the Songs tab from then, for John to check (P: facts-pending.json)
  const factsBy = { t: 'the facts task (Claude)', url: mine && mine.ok && mine.session || '' };
  if (out) step('facts', 'Facts written and checked', 'ok', '', { by: factsBy });
  else if (waiting) step('facts', 'Facts written and checked', 'ok', 'Ready for you to check on the Songs tab', Object.assign(P[id].written ? { at: P[id].written } : {}, { by: factsBy }));
  else if (askedAt && now - askedAt > STALL.facts) step('facts', 'Facts written and checked', 'bad', 'Nothing back from the facts task after ' + ago(now - askedAt) + ': open its session to see why', { url: mine && mine.ok ? mine.session || '' : '' });
  else if (askedAt) step('facts', 'Facts written and checked', 'wait', 'The facts task is writing them (it usually takes a few minutes)', { by: factsBy });
  else step('facts', 'Facts written and checked', 'wait', '', { later: true });
  // 5. his own recording and video, for the members (the Songs tab's uploads: the catalog)
  const files = cat[id] || {}, missing = [!files.a && 'your MP3', !files.v && 'your video'].filter(Boolean);
  if (!missing.length) step('files', 'Your song and video for members', 'ok', '', { at: iso(Math.max(Date.parse(files.ta || '') || 0, Date.parse(files.tv || '') || 0)), by: { t: 'you' } });
  else step('files', 'Your song and video for members', 'wait', (missing.length > 1 ? 'Not up yet' : missing[0][0].toUpperCase() + missing[0].slice(1) + ' isn’t up yet')
    + (out ? ': members get the preview and YouTube’s video meanwhile' : ': add them on the Songs tab before you sign it off'));
  // 6. John's sign-off, which puts a new song on the calendar (John, 28 Oct 2026)
  const signedAt = (F.signed && typeof F.signed[id] === 'string' && F.signed[id]) || (signedNow[id] && signedNow[id].at) || '';
  if ((F.signed && F.signed[id]) || signedNow[id]) step('signed', 'Signed off by you', 'ok', '', { at: signedAt || undefined, by: { t: 'you, on the Songs tab' } });
  else step('signed', 'Signed off by you', 'wait', waiting || out ? 'Waiting for you on the Songs tab' : '', waiting || out ? {} : { later: true });
  // 7. out on the live calendar
  const outRun = signedAt ? firstOk(factsRuns, Date.parse(signedAt), 3600e3) : null;
  if (out) step('out', 'Out on the calendar', 'ok', '', { at: outRun ? outRun.end : undefined, by: runBy(outRun, true) });
  else if (signedNow[id]) step('out', 'Out on the calendar', 'wait', 'Going out now (a couple of minutes)');
  else step('out', 'Out on the calendar', 'wait', 'When you sign it off', { later: true });
  // 8. the members' notification (pushDaily: the newest song, no more than two days old, as it comes out on the live
  // calendar)
  const sentE = log.find(e => e.track === id) || log.find(e => e.kind === 'song' && e.title === it.title && e.at >= added && e.at - added < 3 * DAY);
  if (sentE || pushSent.includes(id)) step('sent', 'Sent to members', 'ok', sentE ? pushSay(sentE) : '', sentE ? { at: new Date(sentE.at).toISOString(), by: pushBy(sentE.via, all), note: sentE.id } : {});   // (note: the page lists it here, not again)
  else if (newest !== id) step('sent', 'Sent to members', 'wait', 'Not sent: a newer song came after it');
  else if ((Date.parse(londonDay()) - Date.parse(londonDay(it.a))) / DAY > 2) step('sent', 'Sent to members', 'wait', 'Not sent: more than two days old');
  else if (!out) step('sent', 'Sent to members', 'wait', 'Goes to members as it comes out on the calendar', { later: true });
  else step('sent', 'Sent to members', 'wait', 'Going to members as the calendar shows it (a minute or two)');
  return { id, n: it.n, title: it.title, artist: it.artist, added: it.a, out, steps };
}
function pushBy(via, runs) {   // who sent a notification (pushDaily's via, from 28 Oct 2026)
  if (!via) return null;
  if (via.t === 'timer') return { t: 'the Worker’s 10-minute timer' };
  if (via.t === 'you') return { t: 'you, on the Members tab' };
  if (via.t === 'member') return { t: via.name || 'a member' };
  if (via.t === 'ask') return { t: (via.name || 'someone') + ', whose invitation ran out' };
  if (via.t === 'upl') return { t: (via.name || 'a member') + ', adding a Spotify playlist' };
  if (via.t === 'spotify') return { t: (via.name || 'a member') + ', connecting Spotify' };
  const r = via.run && via.run.id ? Object.assign({ name: via.run.job || 'Song moods and genres', n: via.run.n }, (runs || []).find(x => x.id === via.run.id) || {}) : null;
  return r ? { t: r.name + ', run ' + r.n, url: r.url || 'https://github.com/' + REPO + '/actions/runs/' + via.run.id } : { t: 'the site job' };
}
function pushSay(e) {   // how a notification went
  const parts = [];
  if (e.devices) parts.push('to ' + e.sent + ' of ' + e.devices + (e.devices === 1 ? ' device' : ' devices'));
  else if (!e.old) parts.push('no devices have them on');
  if (e.failed) parts.push(e.failed + ' failed');
  if (e.gone) parts.push(e.gone + ' turned off since');
  if (e.inboxes) parts.push('into ' + e.inboxes + (e.inboxes === 1 ? ' inbox' : ' inboxes'));
  const t = parts.join(', ');
  return t ? t[0].toUpperCase() + t.slice(1) : '';
}
function ago(ms) { const m = Math.round(ms / 60e3); return m < 60 ? m + ' min' : Math.floor(m / 60) + ' h' + (m % 60 ? ' ' + (m % 60) + ' min' : ''); }
async function adminStatus(env) {
  const now = Date.now(), errors = [];
  const [job, ack, signedNow, ref, log, pushSent, cat, golive, sw, mailLog] = await Promise.all([env.SOTD.get('job', 'json'), env.SOTD.get('statusAck', 'json'),
    env.SOTD.get('factsSigned', 'json'), mainSha(env), pushHistory(env), env.SOTD.get('pushSent', 'json'), catalog(env), env.SOTD.get('golive', 'json'),
    env.SOTD.get('spotifyWait', 'json'), env.SOTD.get('mailLog', 'json')]);
  const acked = ack || {};
  const soft = (p, f) => p.catch(e => { errors.push(String(e && e.message || e)); return f; });
  let listAt = '';
  const [items, runs, sources, asked, task, F, V, P, Y, pages, waitingLive, siteRuns, factsRuns] = await Promise.all([
    soft(songsJson(new Request(LIVE_ORIGIN + '/songs.json'), env, { waitUntil: p => { if (p && p.catch) p.catch(() => {}); } })
      .then(r => r.json()).then(j => { listAt = j.at || ''; return j.songs || []; }), []),
    env.GH_TOKEN ? Promise.all([soft(jobRuns(env), []), soft(jobRuns(env, R2_REPO), [])]).then(a => a[0].concat(a[1])) : Promise.resolve([]),
    soft(rawJson('_sotd/data/sources.json', undefined, ref), { songs: {} }),
    soft(rawJson('_sotd/data/facts-asked.json', {}, ref), {}),
    soft(rawJson('_sotd/data/facts-task.json', null, ref), null),
    soft(rawJson('songs/facts.json', undefined, ref), { songs: {}, signed: {} }),
    soft(rawJson('songs/videos.json', undefined, ref), { videos: {} }),
    soft(rawJson('_sotd/data/facts-pending.json', { songs: {} }, ref), { songs: {} }),
    soft(rawJson('_sotd/data/youtube-playlist.json', {}, ref), {}),
    soft(rawJson('_sotd/data/pages.json', null, ref), null),
    env.GH_TOKEN ? soft(notLive(env), null) : Promise.resolve(null),
    env.GH_TOKEN ? soft(runHistory(env, JOB, 60), []) : Promise.resolve([]),
    env.GH_TOKEN ? soft(runHistory(env, FACTS_JOB, 30), []) : Promise.resolve([])]);
  if (!env.GH_TOKEN) errors.push('The GH_TOKEN secret is not set, so the GitHub jobs can’t be read.');
  const list = items.map((it, i) => Object.assign({ n: i + 1 }, it)).sort((a, b) => (a.a < b.a ? 1 : -1));
  const d = { job: job || {}, runs, S: sources.songs || {}, asked, task, F, V: V.videos || {}, signedNow: signedNow || {},
    log: log || [], pushSent: pushSent || [], newest: items.length ? items[items.length - 1].id : '', P: P.songs || {}, cat: cat || {}, YT: (Y && Y.added) || {},
    siteRuns: siteRuns || [], factsRuns: factsRuns || [] };
  const isOut = it => !!(F.songs && F.songs[it.id]) || !!(F.signed && F.signed[it.id]);
  const show = list.filter(it => !isOut(it)).concat(list.filter(isOut).slice(0, 2)).sort((a, b) => (a.a < b.a ? 1 : -1));
  const songs = show.map(it => songSteps(it, d, now));
  // what has failed, each once by its key (the last call to the facts task, failed, counts until a call works, even once
  // the song it was for is out, since the next song's call would fail the same way), less what John has dealt with
  const bad = {};
  const mark = (o, key) => { o.key = key; o.ack = acked[key] || null; if (!o.ack) bad[key] = true; };
  songs.forEach(x => x.steps.forEach(y => { if (y.st === 'bad') mark(y, y.key); }));
  runs.forEach(r => { if (r.bad) mark(r, 'r:' + r.id); });
  if (task && task.ok === false) mark(task, 't:' + task.at);
  if (task) task.by = pushBy(task.run ? { t: 'site', run: task.run } : null, d.siteRuns.concat(d.factsRuns));   // (the run that called it)
  // Spotify told the Worker to wait (KV spotifyWait): a problem while it lasts, listed for two days after
  let spotify = null;
  if (sw && sw.until && now - sw.until < 2 * DAY) {
    spotify = { at: new Date(sw.at || now).toISOString(), until: new Date(sw.until).toISOString(), active: sw.until > now, since: listAt };
    if (spotify.active) mark(spotify, 'w:' + (sw.at || 0));
  }
  // the draft and the live calendar against the template (pages.json, from the site job; John, 28 Oct 2026: "how are we
  // sure that draft and live don't get out of synch")
  let site = null;
  if (pages) {
    const wl = (waitingLive && waitingLive.list) || [];
    site = !pages.draft ? { st: 'bad', say: 'The draft isn’t what the template builds: it wasn’t built again after a change.', key: 'p:draft' }
      : wl.length ? { st: 'wait', say: 'The draft has changes waiting to go live:', list: wl, publish: true }   // (template changes since the last go-live: waiting, whatever pages.json says, as the job that writes it can be cancelled by the next push: John, 1 Nov 2026, "there's no option to publish")
      : pages.live ? { st: 'ok', say: 'The live calendar is the draft: nothing waiting.' }
      : { st: 'bad', say: 'The live calendar isn’t what the template builds, and nothing has changed in the template since it last went live.', key: 'p:live' };
    if (site.key) mark(site, site.key);
    site.last = (waitingLive && waitingLive.last) || null;   // when it last went live
    // Publish pressed: its run, until the draft is live (a failed run shows here, and under Jobs, and the button again).
    // Once that run has put the draft live, a change made since is a new one waiting: the press is history then, and
    // Publish is offered again (29 Oct 2026: a change at 10:58 after the 10:54 Publish showed "Gone live" with the
    // 10:54 job and no Publish button, for half an hour)
    if (golive && site.st === 'wait' && now - Date.parse(golive.at) < 30 * 60e3) {
      const run = runs.find(r => r.name === GOLIVE_RUN && Date.parse(r.at) >= Date.parse(golive.at) - 60e3) || null;
      const st = !run ? 'starting' : run.st !== 'completed' ? 'running' : run.res === 'success' ? 'done' : 'failed';
      const since = (site.list || []).some(c => Date.parse(c.at) > Date.parse(golive.at));
      if (!(st === 'done' && since)) {
        site.going = { at: golive.at, st, url: run ? run.url : '' };
        if (st !== 'failed') site.publish = false;
      }
    }
  }
  // the emails sent (codes to sign in, and tests) in the last two days; one that couldn't be sent is a problem
  const mails = (mailLog || []).filter(e => now - e.at < 2 * DAY).map(e => Object.assign({}, e, { at: new Date(e.at).toISOString() }));
  mails.forEach(e => { if (!e.ok) mark(e, 'm:' + e.id); });
  const notes = d.log.slice(0, 12).map(e => Object.assign({}, e, { say: pushSay(e), by: pushBy(e.via, d.siteRuns.concat(d.factsRuns)) }));   // the notifications sent, newest first
  notes.forEach(e => { if (e.failed) mark(e, 'n:' + e.id); });   // (one that some devices didn't get)
  const jb = d.job;
  const cloudflare = await cfStats(env).catch(e => ({ errs: [String(e && e.message || e)] }));
  await kvSoft(env, 'statusN', JSON.stringify({ n: Object.keys(bad).length, at: now }));   // (for the Admin button on John's Profile)
  return { at: new Date(now).toISOString(), problems: Object.keys(bad).length, songs, task, runs, notes, site, spotify, mails, ref, cloudflare,
    job: { at: jb.at || null, tried: jb.tried || null, err: jb.err || '' }, errors };
}
async function notLive(env) {   // the template's changes since the calendar last went live (a commit starting "Live")
  const L = await ghApi(env, '/commits?path=songs/index.html&per_page=30');
  const last = (L || []).find(c => /^Live\b/.test((c.commit && c.commit.message) || ''));
  const since = last ? last.commit.committer.date : '';
  const T = await ghApi(env, '/commits?path=_sotd/web/template.html&per_page=30' + (since ? '&since=' + encodeURIComponent(since) : ''));
  const list = (T || []).filter(c => !last || c.sha !== last.sha).map(c => ({ at: c.commit.committer.date, t: c.commit.message.split('\n')[0].slice(0, 160), url: c.html_url }))
    .filter(c => !since || c.at > since);
  return { list, last: last ? { at: since, url: last.html_url } : null };
}
// the Status tab's Publish (John, 28 Oct 2026: "Can I have a publish option on the status Draft and Live - one publish
// button for all"): the draft calendar, as it is, becomes the live one. golive.yml builds the live page from the
// template, as the draft is, so every waiting change goes live together; it won't if the draft isn't what the template
// builds (then what he tried isn't what would go live). KV golive: when he last pressed it, for the card to follow.
const GOLIVE_JOB = 'golive.yml', GOLIVE_RUN = 'Put the draft live';
async function publishDraft(env) {
  if (!env.GH_TOKEN) throw new HttpError(503, 'The GH_TOKEN secret is not set.');
  const ref = await mainSha(env);
  const [pages, nl] = await Promise.all([rawJson('_sotd/data/pages.json', null, ref), notLive(env)]);
  if (pages && !pages.draft) throw new HttpError(409, 'The draft isn’t what the template builds, so what’s on it isn’t what would go live. Nothing changed.');
  if (!nl.list.length && pages && pages.live) throw new HttpError(409, 'The live calendar is the draft already: nothing to put live.');   // (as the Status card decides: the template's changes since it last went live, which pages.json may not have caught up with: John, 1 Nov 2026)
  const say = nl.list.map(c => c.t.replace(/^Calendar \(draft\):\s*/i, '')).join('; ').slice(0, 400);
  const r = await fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/' + GOLIVE_JOB + '/dispatches', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + env.GH_TOKEN, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'SongOfTheDayWorker', 'Content-Type': 'application/json' },
    body: JSON.stringify({ ref: 'main', inputs: { say } })
  }).catch(e => ({ status: 0, text: async () => String(e) }));
  if (r.status !== 204) throw new HttpError(502, 'GitHub didn’t start it: it answered ' + r.status + ' ' + (await r.text()).slice(0, 160));
  await env.SOTD.put('golive', JSON.stringify({ at: new Date().toISOString(), n: nl.list.length }), { expirationTtl: 2 * DAY / 1000 });
}
async function statusAck(env, key, on) {   // "Dealt with" (on), or not after all
  if (!/^[srtnpwm]:[\w:.+-]{1,120}$/.test(String(key || ''))) throw new HttpError(400, 'Not something the Status tab shows.');
  const ack = (await env.SOTD.get('statusAck', 'json')) || {};
  const old = Date.now() - ACK_DAYS * DAY;
  for (const k of Object.keys(ack)) if (Date.parse(ack[k]) < old) delete ack[k];
  if (on) ack[key] = new Date().toISOString(); else delete ack[key];
  await env.SOTD.put('statusAck', JSON.stringify(ack));
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
// Which of them each member gets (John, 29 Oct 2026: "so I don't automatically turn on access to everything for
// everyone"): see { a, v } on their record, set on the Members tab (MP3s: his recordings; MP4s: his videos). Someone
// invited from then on starts with neither; members from before, with no see, keep both. /members/me signs links only
// for what they may have, and says so (see), so the calendar takes their downloads of the rest off the device; a link
// already given lasts its LINK_HOURS.
const MEMBERS_ORIGIN = 'https://cooperking.github.io';
const MEMBERS_JOIN = 'https://cooperking.github.io/songs/#join=';
const INVITE_USES = 5, INVITE_DAYS = 30, LINK_HOURS = 12, MEMBERS_MAX = 200;   // (40 until 29 Oct 2026)
function mediaFor(m) { const s = (m && m.see) || {}; return { a: s.a !== false, v: s.v !== false }; }   // which of John's files a member gets
// a TV can't open a link: John makes a short code for the member instead, typed in the Fire TV app's menu; one use,
// for TV_CODE_MINUTES
const TV_CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789', TV_CODE_MINUTES = 30;
function tvCode() { return [...crypto.getRandomValues(new Uint8Array(6))].map(b => TV_CODE_CHARS[b % TV_CODE_CHARS.length]).join(''); }
const MEDIA_KINDS = { a: { ext: 'mp3', type: 'audio/mpeg' }, v: { ext: 'mp4', type: 'video/mp4' } };
const MEDIA_KEY_RE = /^(a\/[0-9A-Za-z]{22}\.mp3|v\/[0-9A-Za-z]{22}\.mp4)$/;
const LIGHT_KEY_RE = /^l\/[0-9A-Za-z]{22}\.m4a$/;   // a song uploaded already as AAC (_sotd/convert/sotd_convert.py): put where its lighter copy goes

function randomCode(bytes) { return b64url(crypto.getRandomValues(new Uint8Array(bytes))); }

// John signed in on the calendar (the site's owner): his own songs edited there as any member's (John, 6 Nov 2026: "I should
// be able to edit everything in my own playlist just as others can theirs"): the Songs tab's saves (pictures, files,
// videos, facts, moods and genres) take his sign-in as well as the admin password, for those alone
async function ownerSignedIn(request, env) {
  const who = await memberWho(request, env).catch(() => null);
  return !!who && who.id === await env.SOTD.get('owner');
}
// GET /durs?ids=a,b (50 at most): each song's real length in ms, from Spotify, kept for good (KV durs) (John, 7 Nov 2026:
// "I just checked a song and what had been added was a 30 second version. Any song that short is suspicious")
async function songDurs(url, env) {
  const ids = [...new Set(String(url.searchParams.get('ids') || '').split(',').map(x => x.trim()).filter(x => ID_RE.test(x)))].slice(0, 50);
  const kept = (await env.SOTD.get('durs', 'json')) || {}, want = ids.filter(t => !(t in kept)), out = {};
  if (want.length) {
    const j = await spotify(await getToken(env), '/tracks?ids=' + want.join(',')).catch(() => null);
    let n = 0;
    for (const t of (j && j.tracks) || []) if (t && t.id && t.duration_ms) { kept[t.id] = t.duration_ms; n++; }
    for (const t of want.filter(t => !kept[t]).slice(0, 20)) {   // (else Spotify's player page for it, which says its length)
      const r = await fetch('https://open.spotify.com/embed/track/' + t).catch(() => null), m = r && r.ok ? (await r.text()).match(/"duration":(\d{4,8})/) : null;
      if (m) { kept[t] = Number(m[1]); n++; }
    }
    if (n) await kvSoft(env, 'durs', JSON.stringify(kept));
  }
  for (const t of ids) if (kept[t]) out[t] = kept[t];
  return new Response(JSON.stringify({ ms: out }), { headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'public, max-age=3600' } });
}
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
// the same song under another track id (a single and its album's, another release), as twins.py finds it: the title with
// brackets and " - …" taken off, and the first artist, folded, spaces not counting (John, 4 Nov 2026: Bloody Mother
// Fucking Asshole on one playlist with files, Bloody Motherfucking Asshole on another searched and downloaded again)
function songKey(x) {
  const f = t => String(t || '').replace(/['’`]/g, '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/&/g, ' and ').replace(/[^a-z0-9]+/g, '');
  const t = f(String(x.title || '').replace(/\s+-\s+.*$/, '').replace(/\s*[(\[][^)\]]*[)\]]/g, '')), a = f(((x.artists && x.artists[0]) || String(x.artist || '')).split(/,\s*/)[0]);
  return t && a ? t + '|' + a : '';
}
function songTitleKey(x) { return songKey({ title: x.title, artist: 'x' }).replace(/\|x$/, ''); }
function songNames(x) {   // every artist credited, folded ('Marc Bolan & T. Rex': marcbolan, trex)
  const f = t => String(t || '').replace(/['’`]/g, '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '');
  const out = new Set(); for (const a of (x.artists && x.artists.length ? x.artists : [x.artist || ''])) for (const p of String(a).split(/,\s*|\s+(?:&|and|x|feat\.?|featuring|with)\s+/i)) { const n = f(p); if (n) out.add(n); }
  return [...out];
}
function sameArtist(a, b) { return a.some(x => b.includes(x)) || a.some(x => b.some(y => x.length >= 4 && y.length >= 4 && (x.includes(y) || y.includes(x)))); }
function twinOf(TW, x, pid) {   // another copy of x with files (same title, an artist in common): { id, pid (a member's own uploads) }, or null
  const l = TW[songTitleKey(x)]; if (!Array.isArray(l)) return null;
  const nm = songNames(x);
  return l.find(e => !(e.id === x.id && (!e.pid || e.pid === pid)) && sameArtist(nm, e.n || [])) || null;
}
// the download job's outcome for each song it couldn't get (playlist-r2/status.json in the bucket): why, and whether it can
// (John, 4 Nov 2026: "when files have failed to download I need to know why and whether to try again or get the copy myself")
let dlMem = null;
async function dlStatus(env) {
  if (dlMem && Date.now() - dlMem.at < 60e3) return dlMem.v;
  let v = {};
  try { const o = env.MEDIA && await env.MEDIA.get('playlist-r2/status.json'); if (o) v = (await o.json()).tracks || {}; } catch (e) {}
  dlMem = { at: Date.now(), v }; return v;
}
async function twinFiles(env) {   // { title key: [{ id, n: artists }] of copies with files }, kept by the timer (spKeep)
  return (await env.SOTD.get('twinfiles', 'json')) || {};
}

// ---- files for songs on no calendar or playlist any more, deleted (John, 1 Nov 2026: "AaBbCc and Potench playlists
// have been deleted now so the songs should be deleted fully"): the MP3s and MP4s in the catalog (a/, v/, with their
// TV copies h/ and lighter copies l/), which John uploads for his songs and playlist-r2 downloads for members' (from
// the Playlist push sheet), once a song is on none of John's calendar, any member's calendar or playlist (a song its
// owner took off counts only while it's on a saved playlist) or any member's saved playlist. On the timer, CAT_TIDY_RUN
// songs a run, each said in KV catgone (the newest 200: track, title when known, when). Only once John's songs and the
// members' playlists have been read (at least CAT_TIDY_MIN of John's), so a failed read never empties it. ----
const CAT_TIDY_RUN = 40, CAT_TIDY_MIN = 50;
async function catalogTidy(env) {
  if (!env.MEDIA) return;
  const last = ((await env.SOTD.get('songsLast', 'json')) || {}).songs || [];
  if (last.length < CAT_TIDY_MIN) return;
  const used = new Set(last.map(x => x.id)), U = await uplAll(env), titles = {};
  const saved = new Set();
  let cursor;
  do {   // every member's saved playlists
    const l = await env.SOTD.list({ prefix: 'mpl:', cursor }).catch(() => null);
    if (!l) return;   // (couldn't tell: nothing deleted)
    for (const k of l.keys || []) for (const pl of (await env.SOTD.get(k.name, 'json')) || []) for (const t of pl.tracks || []) saved.add(t);
    cursor = l.list_complete ? null : l.cursor;
  } while (cursor);
  saved.forEach(t => used.add(t));
  for (const pid of Object.keys(U)) {
    const data = await uplSongs(env, pid);
    for (const x of data.songs || []) { titles[x.id] = x.title; if (!uplGone(data, x) || saved.has(x.id)) used.add(x.id); }
  }
  const c = await catalog(env), gone = Object.keys(c).filter(t => !used.has(t)).slice(0, CAT_TIDY_RUN);
  if (!gone.length) return;
  for (const t of gone) {
    for (const k of ['a/' + t + '.mp3', 'v/' + t + '.mp4', 'h/' + t + '.mp4', 'l/' + t + '.m4a']) await env.MEDIA.delete(k).catch(() => {});
    delete c[t];
  }
  await env.SOTD.put('mediaCatalog', JSON.stringify(c));
  const log = (await env.SOTD.get('catgone', 'json')) || [];
  await env.SOTD.put('catgone', JSON.stringify(gone.map(t => ({ t, n: titles[t] || '', at: Date.now() })).concat(log).slice(0, 200)));
  console.log('catalogTidy: ' + gone.length + ' songs on no calendar or playlist, their files deleted');
}
// the catalog put right from what's really in the bucket, every 3 hours (John, 6 Nov 2026: Missing listed songs the job
// had files for, and they were downloaded again): two uploads saving the catalog at once can lose one's entry (KV keeps
// the last write), so a file there that the catalog doesn't know of is added (its copy, l/ or h/, counts as it); nothing
// is taken out here. Then, once: the copies added to 'Collaborations' by the download code on 6 Nov 2026 where the job's
// own file was there all along, deleted (89, about 1.3 GB).
async function catalogHeal(env) {
  const first = !(await env.SOTD.get('catHeal2'));   // (once at once, then every half hour: John, 7 Nov 2026, "my list of missing things ... is out of date")
  if (!env.MEDIA || (!first && (Date.now() - Number((await env.SOTD.get('catHealAt')) || 0)) < 30 * 60e3)) return;
  await env.SOTD.put('catHealAt', String(Date.now())); if (first) await env.SOTD.put('catHeal2', String(Date.now()));
  const files = []; let cursor;
  do { const r = await env.MEDIA.list({ cursor, limit: 1000 }); r.objects.forEach(o => files.push({ key: o.key, size: o.size, at: o.uploaded })); cursor = r.truncated ? r.cursor : null; } while (cursor);
  const c = await catalog(env), added = [];
  for (const f of files) {
    const m = /^([avlh])\/([0-9A-Za-z]{22})\.(mp3|mp4|m4a)$/.exec(f.key); if (!m) continue;
    const k = m[1] === 'l' ? 'a' : m[1] === 'h' ? 'v' : m[1], t = m[2], e = c[t] || {};
    if (e[k]) continue;
    e[k] = f.size; e['t' + k] = new Date(f.at).toISOString(); c[t] = e; added.push(k + '/' + t);
  }
  const have = new Set(files.map(f => f.key)), dropped = [];   // (the other way: ones the catalog has that the bucket hasn't, any more)
  for (const [t, e] of Object.entries(c)) {
    if (e.a && !have.has('a/' + t + '.mp3') && !have.has('l/' + t + '.m4a')) { delete e.a; delete e.fa; delete e.ta; dropped.push('a/' + t); }
    if (e.v && !have.has('v/' + t + '.mp4') && !have.has('h/' + t + '.mp4')) { delete e.v; delete e.fv; delete e.tv; dropped.push('v/' + t); }
    if (!Object.keys(e).length) delete c[t];
  }
  if (added.length || dropped.length) { await env.SOTD.put('mediaCatalog', JSON.stringify(c)); console.log('catalogHeal: ' + added.length + ' put back, ' + dropped.length + ' gone'); }
  await kvSoft(env, 'catHealLast', JSON.stringify({ at: Date.now(), added: added.length, dropped: dropped.length, sample: added.slice(0, 20) }));
  if (added.length || dropped.length || first) await env.MEDIA.put('diag/catalog-heal.json', JSON.stringify({ at: new Date().toISOString(), files: files.length, added, dropped }, null, 1), { httpMetadata: { contentType: 'application/json' } }).catch(() => {});
  if (await env.SOTD.get('dupGone1')) return;   // (the one clear-out)
  await env.SOTD.put('dupGone1', String(Date.now()));
  const pid = 'hXXaGojDDcooDA', data = await uplSongs(env, pid), byKey = new Set(files.map(f => f.key)), gone = [];
  for (const f of files) {
    const m = /^u([av])\/hXXaGojDDcooDA\/([0-9A-Za-z]{22})\.(mp3|mp4)$/.exec(f.key); if (!m) continue;
    const k = m[1], t = m[2], day = new Date(f.at).toISOString().slice(0, 10);
    const job = k === 'a' ? byKey.has('a/' + t + '.mp3') || byKey.has('l/' + t + '.m4a') : byKey.has('v/' + t + '.mp4') || byKey.has('h/' + t + '.mp4');
    if (!job || day !== '2026-11-06') continue;
    await env.MEDIA.delete(f.key);
    if (data.files && data.files[t]) { delete data.files[t][k]; if (!Object.keys(data.files[t]).length) delete data.files[t]; }
    gone.push(f.key);
  }
  if (gone.length) await env.SOTD.put('upl:' + pid, JSON.stringify(data));
  await env.MEDIA.put('diag/dup-gone.json', JSON.stringify({ at: new Date().toISOString(), catalogAdded: added.length, deleted: gone }, null, 1), { httpMetadata: { contentType: 'application/json' } });
}
async function catalogSet(env, key, size, fp) {   // which songs have which files: a and v, with their sizes (0: gone),
  const c = await catalog(env), kind = key[0], track = key.slice(2, 24);   // and fa, fv: each file's fingerprint, so a
  const e = c[track] || {};                                                // folder upload can skip what's already there
  if (size) { e[kind] = size; e['t' + kind] = new Date().toISOString(); if (fp) e['f' + kind] = fp; else delete e['f' + kind]; }   // (t: when, for the Status tab)
  else { delete e[kind]; delete e['f' + kind]; delete e['t' + kind]; }
  if (Object.keys(e).length) c[track] = e; else delete c[track];
  await env.SOTD.put('mediaCatalog', JSON.stringify(c));
}

async function members(request, url, env) {
  const h = factsHeaders(MEMBERS_ORIGIN);
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: h });
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: h });
  const route = url.pathname.slice('/members/'.length);
  if (route === 'join') return memberJoin(request, env, answer);
  if (route === 'ask') return memberAsk(request, env, answer);
  if (route === 'signout') return memberSignout(request, env, answer);
  if (route === 'me') return memberMe(request, env, answer);
  if (route === 'playlists') return memberPlaylists(request, env, answer);
  if (route === 'now') return memberNow(request, env, answer);
  if (route === 'now/ws') return memberNowSocket(request, env);
  if (route === 'now/widget') return memberNowWidget(request, env, answer);
  if (route === 'tvcode') return memberTvCode(request, env, answer);
  if (route === 'passkey') return memberPasskeys(request, env, answer);
  if (route === 'passkey/signin') return memberPasskeySignin(request, env, answer);
  if (route === 'code') return memberMailCode(request, env, answer);
  if (route === 'code/signin') return memberMailSignin(request, env, answer);
  if (route === 'email') return memberEmail(request, env, answer);
  if (route === 'push') return memberPush(request, env, answer);
  if (route === 'inbox') return memberInbox(request, env, answer);
  if (route === 'send') return memberSend(request, env, answer);
  if (route === 'sendpic') return memberSendPic(request, env, answer);
  if (route === 'prefs') return memberPrefs(request, env, answer);
  if (route === 'captions') {   // John's captions on his own calendar (John, 6 Nov 2026: "give me the option to put it there too"): { cal } under its name, or { track, cap } on a song's page; '' none. KV mainCaps { cal, songs: { track: cap } }
    if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
    const who = await memberWho(request, env);
    if (!who || who.id !== await env.SOTD.get('owner')) return answer(403, { error: 'Only John can do that.' });
    const b = await request.json().catch(() => ({})), C = (await env.SOTD.get('mainCaps', 'json')) || {}, clean = v => String(v || '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim().slice(0, 300);
    if ('cal' in b) { const c = clean(b.cal); if (c) C.cal = c; else delete C.cal; }
    if ('cap' in b) for (const t of [b.track].concat(Array.isArray(b.tracks) ? b.tracks.slice(0, 20) : []).map(String).filter(t => ID_RE.test(t))) { C.songs = C.songs || {}; const c = clean(b.cap); if (c) C.songs[t] = c; else delete C.songs[t]; }   // (tracks: the day's others too, the same caption: John, 6 Nov 2026)
    if (b.track && ID_RE.test(String(b.track)) && ('tt' in b || 'ar' in b || 'src' in b)) {   // his song's title and artist as he wants them shown ('' Spotify's), and its MP3 from another site's link ('' none)
      C.ed = C.ed || {}; const e = C.ed[b.track] || {};
      for (const k of ['tt', 'ar']) if (k in b) { const v = String(b[k] || '').replace(/\s+/g, ' ').trim().slice(0, 200); if (v) e[k] = v; else delete e[k]; }
      if ('src' in b) { const u = String(b.src || '').trim(); if (!u) delete e.src; else if (/^https:\/\/[\w.-]+\.[a-z]{2,}\/\S{1,400}$/i.test(u) && !/youtu\.?be/i.test(u)) e.src = u; else return answer(400, { error: 'That isn’t a link to the song on another site.' }); }
      if (Object.keys(e).length) C.ed[b.track] = e; else delete C.ed[b.track];
    }
    await env.SOTD.put('mainCaps', JSON.stringify(C));
    return answer(200, Object.assign({ ok: true }, C));   // (cal, songs, ed)
  }
  if (route === 'sent') return memberSent(request, env, answer);
  if (route === 'react') return memberReact(request, env, answer);
  if (route === 'bell') return memberBell(request, env, answer);
  if (route === 'problems') {   // John only: how many things have failed or are stuck (the Status tab's count), for the Admin button on his Profile (John, 6 Nov 2026); worked out again at most every ten minutes
    const who = await memberWho(request, env);
    if (!who || who.id !== await env.SOTD.get('owner')) return answer(403, { error: 'Only John.' });
    let c = await env.SOTD.get('statusN', 'json');
    if (!c || Date.now() - (c.at || 0) > 10 * 60e3) { try { const st = await adminStatus(env); c = { n: st.problems || 0, at: Date.now() }; } catch (e) { c = c || { n: 0, at: 0 }; } }
    return answer(200, { n: c.n || 0, at: c.at || 0 });
  }
  if (route === 'heart') return memberHeart(request, env, answer);
  if (route === 'upl') return memberUpl(request, url, env, answer);
  if (route === 'uplup') return memberUplUp(request, url, env, answer);
  if (route === 'uplcover') return memberUplCover(request, url, env, answer);
  if (route === 'uplpic') return memberUplPic(request, url, env, answer);
  if (route === 'songs') return memberSongs(request, url, env, answer);
  if (route === 'spnames') return memberSpNames(request, url, env, answer);
  if (route === 'impfile') return memberImpFile(request, env, answer);
  if (route === 'impdrop') return memberImpDrop(request, url, env, answer);
  if (route === 'spotify') return memberSpotify(request, url, env, answer);
  if (!['admin', 'upload', 'cover'].includes(route)) return answer(404, { error: 'Not here.' });
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const ownerOp = route !== 'admin' || ['delete'].includes(((await request.clone().json().catch(() => ({}))) || {}).op);   // (his sign-in: files and pictures; of the admin ops, deleting a file)
  const no = ownerOp && await ownerSignedIn(request, env) ? null : await adminOk(request, env, answer);
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
  // when each device was last active (seenTouch): read only for the ops that list the members (not the Status tab's)
  const seenAt = ['list', 'people', 'invite', 'reinvite', 'email', 'owner', 'remove', 'apart', 'see'].includes(body.op) ? await seenAll(env) : {};
  // each member's Spotify, as their card shows it (John, 4 Nov 2026: "who has connected Spotify and how they are using it"), and how many of their playlists update from it
  const spInfo = {}; { let cursor; do { const l = await env.SOTD.list({ prefix: 'spm:', cursor }); for (const k of l.keys) { const r = await env.SOTD.get(k.name, 'json'); if (r) spInfo[k.name.slice(4)] = { name: r.name || '', product: r.product || '', on: r.on !== false, blocked: !!r.blocked, gone: !!r.gone, lists: /playlist-read-private/.test(r.scope || ''), linked: r.linked || 0, dev: r.dev || r.device || '' }; } cursor = l.list_complete ? null : l.cursor; } while (cursor); }
  { const U0 = await uplAll(env); for (const r of Object.values(U0)) if (r.auto && spInfo[r.o]) spInfo[r.o].auto = (spInfo[r.o].auto || 0) + 1; }
  const listing = (own = owner) => Object.entries(all).map(([id, m]) => ({ mail: m.email ? (mailMode(m) || 'off') : '', push: pushMode(m), sp: spInfo[id] || null, id, name: m.name, created: m.created, owner: id === own, email: m.email || '', emailAt: m.emailAt || null,
    devices: Object.values(m.devices || {}).filter(d => d.did).length, apps: deviceApps(m, seenAt), passkeys: (m.passkeys || []).length, nomsg: !!m.nomsg, apart: m.apart || [], see: mediaFor(m),
    seen: Math.max(0, ...Object.entries(m.devices || {}).map(([h, d]) => Math.max(seenAt[h] || 0, d.seen || 0))) || null,
    fmax: filesMax(m), fset: Number.isInteger(m.fmax), spu: m.spu || '', invite: m.invite ? Object.assign({ uses: m.invite.uses, left: Math.max(0, INVITE_USES - m.invite.uses), until: m.invite.until }, m.invite.asked ? { asked: m.invite.asked } : {}) : null }));
  switch (body.op) {
    case 'list': return answer(200, { members: listing(), catalog: await catalog(env), media: !!env.MEDIA, covers: await coversAll(env) });
    case 'people': return answer(200, { members: listing() });   // (the Apps tab: who has which app, and the emails for Google Play)
    case 'invite': {
      const name = String(body.name || '').replace(/\s+/g, ' ').trim().slice(0, 60);
      if (!name) return answer(400, { error: 'Give their name.' });
      if (Object.keys(all).length >= MEMBERS_MAX) return answer(400, { error: 'That is the most members there can be.' });
      const email = body.email ? cleanEmail(body.email) : '';
      if (body.email && !email) return answer(400, { error: 'That doesn’t look like an email address.' });
      if (email && Object.values(all).some(x => x.email === email)) return answer(400, { error: 'Another member has that email address.' });
      // his files: none unless he ticks them in the Invite box (see { a, v }); and the members they're kept apart from
      // from the start (apart: [ids], on both records), as each card's Can see and Seen by set later
      const see = body.see || {}, m = Object.assign({ name, created: Date.now(), devices: {}, see: { a: see.a === true, v: see.v === true } }, email ? { email } : {});
      const id = randomCode(6), keep = [...new Set((Array.isArray(body.apart) ? body.apart : []).map(String))].filter(k => all[k] && k !== owner);
      if (keep.length) { m.apart = keep; keep.forEach(k => { all[k].apart = (all[k].apart || []).filter(v => v !== id).concat(id); }); }
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
    case 'email': {   // a member's email address ('' takes it away): for codes by email, and Google Play's test list (the CSV)
      const m = all[body.id];
      if (!m) return answer(404, { error: 'No such member.' });
      const email = body.email ? cleanEmail(body.email) : '';
      if (body.email && !email) return answer(400, { error: 'That doesn’t look like an email address.' });
      if (email && Object.entries(all).some(([k, x]) => k !== body.id && x.email === email)) return answer(400, { error: 'Another member has that email address.' });
      if (email !== (m.email || '')) { if (email) m.email = email; else delete m.email; delete m.emailAt; }   // (emailAt: one they gave themselves)
      await save();
      return answer(200, { ok: true, members: listing() });
    }
    case 'mailtest': {   // a test email, to check the Gmail sign-in works (to a member's address)
      const m = all[body.id];
      if (!m || !m.email) return answer(400, { error: 'Give them an email address first.' });
      try { await sendMail(env, m.email, 'A test from John’s Song of the Day', 'This is a test email from John’s Song of the Day. Sign-in codes come from this address.', 'test',
        mailShell('<h1 style="margin:0 0 10px;font:500 24px/1.25 Georgia,serif;color:#1d0b1e">A test email</h1><p style="margin:0;font:15px/1.5 Helvetica,Arial,sans-serif;color:#4a3a42">This is a test from John’s Song of the Day. Sign-in codes and notifications come from this address.</p>')); }
      catch (e) { return answer(e.status || 502, { error: e.message }); }
      return answer(200, { ok: true });
    }
    case 'tvcode': {
      if (!all[body.id]) return answer(404, { error: 'No such member.' });
      const code = tvCode();
      await env.SOTD.put('tvcode:' + await sha256hex(code), body.id, { expirationTtl: TV_CODE_MINUTES * 60 });
      return answer(200, { code, minutes: TV_CODE_MINUTES, name: all[body.id].name });
    }
    case 'spfind': {   // a member's Spotify playlists, for John to see (and bring one in for them), through their own Spotify sign-in: name, songs, cover, and which are on the calendar already (John, 4 Nov 2026: "How can I see other members public playlists?")
      const m = all[body.id]; if (!m) return answer(404, { error: 'No such member.' });
      const mr = await memberSpRead(env, body.id).catch(() => null);
      if (!mr) return answer(200, { connect: true });
      const pls = await myPlaylists(mr.tok).catch(() => null); if (!pls) return answer(200, { error: 'Spotify didn’t give their playlists just now.' });
      const U = await uplAll(env), theirs = Object.entries(U).filter(([, r]) => r.o === body.id), out = [];
      for (const p of (mr.liked ? [{ id: 'liked', name: 'Liked Songs', owner: { id: mr.sid }, tracks: { total: 0 }, images: [] }] : []).concat(pls)) {
        const on = theirs.find(([, r]) => r.spid === p.id || r.fk === uplFileKey(p.name));
        out.push({ spid: p.id, name: p.name, n: ((p.tracks || p.items || {}).total) || 0, img: ((p.images || [])[0] || {}).url || '', owner: (p.owner && p.owner.display_name) || '', mine: p.id === 'liked' || !!(p.owner && p.owner.id === mr.sid), pub: p.id === 'liked' ? false : !!p.public, on: on ? { id: on[0], n: uplName(all, on[1]), auto: !!on[1].auto || !!on[1].spid } : null });
      }
      return answer(200, { user: mr.sid, lists: out });
    }
    case 'spbring': {   // one of them brought in for the member, as their own import would (it then updates itself)
      const m = all[body.id], spid = String(body.spid || ''); if (!m || !/^([0-9A-Za-z]{22}|liked)$/.test(spid)) return answer(400, { error: 'Which member and playlist?' });
      const mr = await memberSpRead(env, body.id).catch(() => null); if (!mr) return answer(400, { error: m.name + ' hasn’t let Spotify share their playlists yet.' });
      const U = await uplAll(env);
      if (Object.values(U).some(r => r.o === body.id && r.spid === spid)) return answer(400, { error: 'That one is on the calendar already.' });
      const meta = spid === 'liked' ? { name: 'Liked Songs' } : await spotify(mr.tok, '/playlists/' + spid + '?fields=name').catch(() => null), items = await spReadList(mr.tok, spid).catch(() => null);
      if (!meta || !items || !items.length) return answer(400, { error: 'Spotify won’t let us read that playlist.' });
      const songs = uplCsvSongs(items.slice(0, UPL_CSV_MOST)); if (!songs || !songs.length) return answer(400, { error: 'No songs in it.' });
      await uplCsvPreviews(env, songs).catch(() => null);
      const now = Date.now(), newest = Math.max.apply(null, songs.map(x => Date.parse(x.a)));
      const pid = randomCode(10), rec = { o: body.id, src: 'csv', sp: '', spid, n: String(meta.name || '').slice(0, 80) || 'Playlist', fk: uplFileKey(meta.name), mode: body.mode === 'list' ? 'list' : 'cal', at: now, base: Math.max(newest, 0), st: 'ok', auto: true };
      U[pid] = rec;
      await uplTake(env, pid, rec, U, songs, 'new', now, true);
      await startJob(env, null, null, 0, null, true).catch(() => {});
      return answer(200, { ok: true, id: pid, n: songs.length });
    }
    case 'spuser': {   // a member's Spotify username (John knows it: John, 4 Nov 2026), for their public playlists
      const m = all[body.id]; if (!m) return answer(404, { error: 'No such member.' });
      const u = spUserOf(body.u); if (body.u && !u) return answer(400, { error: 'That isn’t a Spotify username or profile link.' });
      if (u) m.spu = u; else delete m.spu;
      await env.SOTD.put('members', JSON.stringify(all)); await env.SOTD.delete('spmap:' + body.id);
      return answer(200, { members: listing() });
    }
    case 'fmax': {   // a member's allowance of songs with MP3s and MP4s: a number, 'none' (no limit) or 'default'
      const m = all[body.id]; if (!m) return answer(404, { error: 'No such member.' });
      if (body.n === 'default') delete m.fmax;
      else if (body.n === 'none') m.fmax = FILES_NO_LIMIT;
      else { const n = Math.floor(Number(body.n)); if (!(n >= 0 && n < FILES_NO_LIMIT)) return answer(400, { error: 'A number of songs, please.' }); m.fmax = n; }
      await env.SOTD.put('members', JSON.stringify(all));
      return answer(200, { members: listing() });
    }
    case 'owner': {   // "This is me": the member who is John (or nobody, with no id)
      if (body.id && !all[body.id]) return answer(404, { error: 'No such member.' });
      if (body.id) await env.SOTD.put('owner', body.id); else await env.SOTD.delete('owner');
      return answer(200, { ok: true, members: listing(body.id || null) });
    }
    case 'see': {   // which of John's files they get: { id, a: MP3s (his recordings), v: MP4s (his videos) }, either or both
      const m = all[body.id];
      if (!m) return answer(404, { error: 'No such member.' });
      const now = mediaFor(m);
      for (const k of ['a', 'v']) if (typeof body[k] === 'boolean') now[k] = body[k];
      m.see = now; await save();
      return answer(200, { ok: true, members: listing() });
    }
    case 'apart': {   // the members kept apart from this one (on both records): they don't see each other, and can't send to each other
      const m = all[body.id];
      if (!m) return answer(404, { error: 'No such member.' });
      if (body.id === owner) return answer(400, { error: 'You see everyone.' });
      const want = new Set((Array.isArray(body.ids) ? body.ids : []).map(String).filter(k => k !== body.id && k !== owner && all[k]));
      for (const [k, x] of Object.entries(all)) {
        if (k === body.id) continue;
        const has = (x.apart || []).filter(v => v !== body.id);
        if (want.has(k)) has.push(body.id);
        if (has.length) x.apart = has; else delete x.apart;
      }
      if (want.size) m.apart = [...want]; else delete m.apart;
      await save();
      return answer(200, { ok: true, members: listing() });
    }
    case 'remove': {
      if (!all[body.id]) return answer(404, { error: 'No such member.' });
      delete all[body.id];
      for (const x of Object.values(all)) for (const f of ['apart', 'mute']) if (x[f]) { x[f] = x[f].filter(v => v !== body.id); if (!x[f].length) delete x[f]; }   // (gone from others' lists)
      await save();
      await env.SOTD.delete('mpl:' + body.id);   // their saved playlists go too
      await pushForget(env, body.id);   // and their notifications
      await env.SOTD.delete('inbox:' + body.id);
      await env.SOTD.delete('outbox:' + body.id);   // (what they'd sent)
      await env.SOTD.delete('bell:' + body.id);   // the reactions to their messages
      await uplDropOwner(env, body.id);   // their own playlists
      await env.SOTD.delete('now:' + body.id);   // and what they were playing (and their iPhone widgets' addresses)
      if (env.NOW) await env.NOW.get(env.NOW.idFromName(body.id)).fetch('https://now/forget', { method: 'POST' }).catch(() => {});
      if (owner === body.id) await env.SOTD.delete('owner');
      return answer(200, { ok: true, members: listing(owner === body.id ? null : owner) });
    }
    case 'status': return answer(200, await adminStatus(env));   // the Status tab
    case 'publish': {   // the Status tab's Publish: the draft calendar goes live as it is (golive.yml)
      try { await publishDraft(env); } catch (e) { return answer(e.status || 500, { error: e.message }); }
      return answer(200, await adminStatus(env));
    }
    case 'statusack': {   // the Status tab's Dealt with (on: true), and its Undo
      try { await statusAck(env, body.key, body.on !== false); } catch (e) { return answer(e.status || 500, { error: e.message }); }
      return answer(200, await adminStatus(env));
    }
    case 'pushcount': return answer(200, await pushCount(env));
    case 'upls': return answer(200, { upls: await uplAdminFull(env, all, await uplAll(env)) });   // members' own playlists, for the Members tab
    case 'uplsongs': {   // every song out on members' playlists (Send a song, and the Songs tab): each with its day, its calendar, whose (oj: John's own), who sees it, what was found for it (vid, mt, yr), its facts (fx: its playlist's, written or signed off), and John's choices
      const U = await uplAll(env), out = [], owner = await env.SOTD.get('owner'), meta = (await env.SOTD.get('uplmeta', 'json')) || {};
      for (const [pid, rec] of Object.entries(U)) {
        if (rec.st !== 'ok' || !all[rec.o]) continue;
        const v = await uplView(env, all, rec.o, pid, rec), fx = (await env.SOTD.get('ufacts:' + pid, 'json')) || {};   // (as its owner sees it: those waiting for them too)
        for (const x of v.songs) {
          const m = meta[x.id] || {}, f = fx[x.id];
          out.push(Object.assign({ id: x.id, title: x.title, artist: x.artist, artists: x.artists, aids: x.aids, img: x.img || '', p: x.p || '', d: x.d, pid, t: v.t, n: v.n, mode: v.mode, sh: v.sh, o: v.o, oj: !!owner && rec.o === owner },
            x.w ? { w: 1 } : {}, x.f ? { f: 1 } : {},
            x.vid ? { vid: x.vid } : m.vid ? { vid: m.vid } : {}, 'ov' in x ? { ov: x.ov } : {}, x.mv || (x.own && x.own.v) || (x.jf && x.jf.v) ? { hv: 1 } : {}, x.ma || (x.own && x.own.a) || (x.jf && x.jf.a) ? { ha: 1 } : {}, m.mt ? { mt: m.mt } : {}, m.yr ? { yr: m.yr } : {}, f && (f.w || f.ok) ? { fx: f.w ? { st: 'wait', f: f.w } : { st: 'ok', f: f.ok, at: f.sat || 0 } } : f && f.none ? { fx: { st: 'nil' } } : {}));   // (its video as its calendar has it: found, another copy's, its owner's choice, or an MP4: John, 7 Nov 2026, "Videos definitely wrong")
        }
      }
      return answer(200, { songs: out, john: await uplJohnAll(env) });
    }
    case 'upledit': {   // John's choices for a song on members' playlists: v (a YouTube id, '' none, null theirs again), m and g (up to two each, null theirs again)
      const t = String(body.track || ''), J = await uplJohnAll(env), e = J[t] || {};
      if (!ID_RE.test(t)) return answer(400, { error: 'Which song?' });
      if ('v' in body) {
        if (body.v === null) delete e.v;
        else if (body.v === '' || /^[\w-]{11}$/.test(String(body.v))) e.v = String(body.v);
        else return answer(400, { error: 'That isn’t a YouTube video.' });
      }
      for (const [k, list] of [['m', MOOD_LIST], ['g', GENRE_LIST]]) {
        if (body[k] === null) delete e[k];
        else if (Array.isArray(body[k])) e[k] = body[k].filter(x => list.includes(x)).slice(0, 2);
      }
      if (Object.keys(e).length) J[t] = e; else delete J[t];
      await env.SOTD.put('uplJohn', JSON.stringify(J));
      if ('v' in body) await startJob(env, null, null, 0, null, true).catch(() => {});   // (their YouTube copies)
      return answer(200, { ok: true, john: J });
    }
    case 'viewas': {   // see the calendar as a member (John, 4 Nov 2026: "log in as another member, so I can see what they see"): a pass for an hour, view only
      const m = all[body.id]; if (!m) return answer(404, { error: 'No such member.' });
      const t = randomCode(32), h = await sha256hex(t);   // (a sign-in as them for an hour, as theirs: John, "it doesn't need to be view only")
      m.devices = m.devices || {}; m.devices[h] = { at: Date.now(), viewas: true, until: Date.now() + 3600e3 };
      for (const [k, d] of Object.entries(m.devices)) if (d.viewas && d.until < Date.now()) delete m.devices[k];
      await env.SOTD.put('members', JSON.stringify(all));
      return answer(200, { token: t, name: m.name, url: SITE_SONGS + '?viewas=' + t });
    }
    case 'mailmode': {   // a member's email notifications set by John, when they ask him (John, 4 Nov 2026: "turn emails back on myself if requested by a member"): all, day, song, direct or off
      const m = all[body.id]; if (!m) return answer(404, { error: 'No such member.' });
      const want = String(body.mode || ''); if (!MAIL_MODES.includes(want)) return answer(400, { error: 'Which setting?' });
      if (!m.email) return answer(400, { error: m.name + ' has no email address yet: they add it in their Profile.' });
      m.mailme = want; if (want !== 'day') await env.SOTD.delete('mdig:' + body.id); if (want !== 'week') await env.SOTD.delete('mwk:' + body.id);
      await env.SOTD.put('members', JSON.stringify(all));
      return answer(200, { members: listing() });
    }
    case 'pushmode': {   // a member's notifications set by John, when they ask him: all, day, song, direct or off
      const m = all[body.id]; if (!m) return answer(404, { error: 'No such member.' });
      const want = String(body.mode || ''); if (!MAIL_MODES.includes(want)) return answer(400, { error: 'Which setting?' });
      if (want === 'all') delete m.pushme; else m.pushme = want;
      if (want !== 'day') await env.SOTD.delete('pdig:' + body.id); if (want !== 'week') await env.SOTD.delete('pwk:' + body.id);
      await env.SOTD.put('members', JSON.stringify(all));
      return answer(200, { members: listing() });
    }
    case 'drafts': {   // the Songs tab's changes not signed off yet, the same on every device of John's (John, 6 Nov 2026: "I made some changes on my chrome app ... when I went to my iphone they weren't there"); { set: { edits, at } } keeps them, else they're given
      if (body.set && typeof body.set === 'object') {
        const t = JSON.stringify({ edits: body.set.edits || {}, at: body.set.at || {}, saved: Date.now() });
        if (t.length > 2e6) return answer(413, { error: 'Too much to keep.' });
        await env.SOTD.put('factDrafts', t);
        return answer(200, { ok: true });
      }
      return answer(200, (await env.SOTD.get('factDrafts', 'json')) || { edits: {}, at: {} });
    }
    case 'nfact': {   // the fact chosen at sign-off for a song's notification (text; '' none), kept 60 days
      const t = String(body.track || ''), f = String(body.text || '').replace(/\s+/g, ' ').trim().slice(0, 600);
      if (!ID_RE.test(t)) return answer(400, { error: 'Which song?' });
      if (f) await env.SOTD.put('nfact:' + t, f, { expirationTtl: 60 * 86400 }); else await env.SOTD.delete('nfact:' + t);
      return answer(200, { ok: true });
    }
    case 'uplsignoff': {   // a member's waiting songs signed off by John for them (items: [{ pid, track, nf: its notification's fact }]): out, and to the members as its owner chose
      const U = await uplAll(env), done = [];
      for (const it0 of (Array.isArray(body.items) ? body.items : []).slice(0, 200)) if (ID_RE.test(String(it0.track || '')) && typeof it0.nf === 'string') { const f = it0.nf.replace(/\s+/g, ' ').trim().slice(0, 600); if (f) await env.SOTD.put('nfact:' + it0.track, f, { expirationTtl: 60 * 86400 }); else await env.SOTD.delete('nfact:' + it0.track); }
      for (const it of (Array.isArray(body.items) ? body.items : []).slice(0, 200)) {
        const pid = String(it.pid || ''), rec = U[pid];
        if (!rec || rec.st !== 'ok' || !all[rec.o]) continue;
        const data = await uplSongs(env, pid), x = (data.songs || []).find(y => y.id === String(it.track || '') && !uplGone(data, y));
        if (!x || !uplWaiting(rec, data, x)) continue;
        await uplSignoffDo(env, all, pid, rec, data, x);
        done.push({ pid, track: x.id });
      }
      return answer(200, { ok: true, done });
    }
    case 'uplfacts': {   // a public playlist's song's facts, edited (and signed off: sign) by John, as its owner can
      const U = await uplAll(env), pid = String(body.pid || ''), t = String(body.track || '');
      if (!U[pid]) return answer(404, { error: 'No such playlist.' });
      const fx = (await env.SOTD.get('ufacts:' + pid, 'json')) || {}, e = fx[t];
      if (!e || !(e.w || e.ok)) return answer(404, { error: 'No facts for it yet.' });
      const was = e.w || e.ok, clean = l => (Array.isArray(l) ? l : []).map(x => String(x || '').replace(/\s+/g, ' ').trim().slice(0, 400)).filter(Boolean).slice(0, 20);
      const got = { s: clean(body.s), a: (was.a || []).map(x => ({ id: x.id, n: x.n, f: clean((body.a || {})[x.id]) })) };
      if (body.sign) { e.ok = got; delete e.w; delete e.nw; e.sat = Date.now(); e.by = 'John'; } else if (e.w) e.w = got; else e.ok = got;
      fx[t] = e; await env.SOTD.put('ufacts:' + pid, JSON.stringify(fx));
      return answer(200, { ok: true, fx: e.w ? { st: 'wait', f: e.w } : { st: 'ok', f: e.ok, at: e.sat || 0 } });
    }
    case 'uplcheck': {   // a member's playlist read from Spotify now (once John has joined it)
      const U = await uplAll(env), rec = U[body.id];
      if (!rec) return answer(404, { error: 'No such playlist.' });
      if (uplSrc(rec) === 'sp') await uplRead(env, body.id, rec, U);   // (a saved playlist's follows it; an Exportify one is its owner's uploads)
      return answer(200, { upls: await uplAdminFull(env, all, await uplAll(env)) });
    }
    case 'songdel': {   // a member song off every playlist and calendar it's on, its days left empty (pids: only those), from the Songs tab (John, 7 Nov 2026: "I should be able to delete any song")
      const t = String(body.track || ''), only = Array.isArray(body.pids) ? body.pids.map(String) : null, U = await uplAll(env);
      if (!ID_RE.test(t)) return answer(400, { error: 'Which song?' });
      let n = 0;
      for (const [pid, rec] of Object.entries(U)) {
        if (only && !only.includes(pid)) continue;
        const data = await uplSongs(env, pid), xs = (data.songs || []).filter(y => y.id === t && !uplGone(data, y));
        if (!xs.length) continue;
        data.del = data.del || {}; xs.forEach(x => { data.del[x.id] = x.a; });
        if (data.order) data.order = data.order.filter(k => k !== t);
        await uplFilesGone(env, pid, data, [t]);
        await env.SOTD.put('upl:' + pid, JSON.stringify(data));
        rec.cnt = (data.songs || []).filter(y => !uplGone(data, y)).length; n++;
      }
      if (!n) return answer(404, { error: 'It isn’t on any playlist now.' });
      await uplPut(env, U);
      await startJob(env, null, null, 0, null, true).catch(() => {});   // (their cards, and their YouTube copies)
      return answer(200, { ok: true, n });
    }
    case 'uplremove': {
      const U = await uplAll(env);
      if (!U[body.id]) return answer(404, { error: 'No such playlist.' });
      await uplDrop(env, [body.id]);
      return answer(200, { upls: await uplAdminFull(env, all, await uplAll(env)) });
    }
    case 'widgets': {   // the members' iPhone widgets (iOS 26): how many each has, and how Apple took the last push to each ({ tell: true }: each told now)
      if (!env.NOW) return answer(200, { widgets: null });
      const out = [];
      for (const [id, m] of Object.entries(all)) {
        const r = await env.NOW.get(env.NOW.idFromName(id)).fetch('https://now/widget' + (body.tell ? '/tell' : ''), { method: body.tell ? 'POST' : 'GET' });
        const j = await r.json().catch(() => ({}));
        if (j.widgets) out.push(Object.assign({ name: m.name }, j));
      }
      return answer(200, { widgets: out });
    }
    case 'push': {   // John's own notification, to everyone who has them on
      const note = noteMsg(body);
      if (note.error) return answer(400, { error: note.error });
      const to = Array.isArray(body.to) ? body.to.map(String).filter(id => all[id]) : null;   // chosen members, or everyone
      if (to && !to.length) return answer(400, { error: 'Choose who it goes to.' });
      // with his name on it, as members' are (John, 29 Oct 2026: everything says who it's from but the daily song): the
      // member who is John (This is me), else John
      const from = (owner && all[owner] && all[owner].name) || 'John', msg = note.msg;
      Object.assign(msg, { subject: msg.title, title: from + ': ' + msg.title, from });
      const r = await pushAll(env, msg, to, { kind: 'note', via: { t: 'you' }, fromId: owner && all[owner] ? owner : '', toIds: to || 'all' });
      if (owner && all[owner]) await sentAdd(env, owner, msg, r, to ? to.map(k => all[k].name) : 'all');   // (his Profile › Sent)
      return answer(200, r);
    }
    case 'splist': {   // members' Spotify (John, 2 Nov 2026): who's connected, and who Spotify wants added in its dashboard (User Management) first
      const out = []; let cursor;
      do {
        const l = await env.SOTD.list({ prefix: 'spm:', cursor });
        for (const k of l.keys) { const id = k.name.slice(4), r = await env.SOTD.get(k.name, 'json'); if (!r || !all[id]) continue;
          out.push({ id, name: all[id].name, blocked: !!r.blocked, gone: !!r.gone, on: r.on !== false, product: r.product || '', sname: r.name || '', email: r.email || r.wemail || '', linked: r.linked || 0, cid: r.cid || CLIENT_ID, asked: r.blocked ? await env.SOTD.get('spaskat:' + id, 'json') : null }); }
        cursor = l.list_complete ? null : l.cursor;
      } while (cursor);
      return answer(200, { list: out, apps: await spApps(env), per: SP_PER_APP, redirect: new URL(request.url).origin + '/callback', quota: Number(await env.SOTD.get('spquota')) || 0 });
    }
    case 'spask': {   // Admin's "Ask Claude again" (John, 2 Nov 2026: the task ran while his Mac was offline, and doesn't ask twice by itself)
      const id = String(body.id || ''), r = all[id] && await env.SOTD.get('spm:' + id, 'json');
      if (!r || !r.blocked) return answer(400, { error: 'not waiting' });
      return answer(200, { asked: await spAskAdd(env, id, r, true) });
    }
    case 'splog': {   // each member's Spotify steps (their phone's log), for the Members tab
      const out = []; let cursor;
      do {
        const l = await env.SOTD.list({ prefix: 'splog:', cursor });
        for (const k of l.keys) { const id = k.name.slice(6); const lines = (await env.SOTD.get(k.name, 'json')) || []; if (lines.length) out.push({ id, name: (all[id] && all[id].name) || id, lines }); }
        cursor = l.list_complete ? null : l.cursor;
      } while (cursor);
      if (body.clear && all[body.clear]) await env.SOTD.delete('splog:' + body.clear);
      return answer(200, { logs: out.filter(x => !(body.clear && x.id === body.clear)) });
    }
    case 'spapps': {   // the extra Spotify apps' Client IDs (John's own app is always the first)
      const ids = (Array.isArray(body.apps) ? body.apps : []).map(x => String(x).trim().toLowerCase()).filter(x => /^[0-9a-f]{32}$/.test(x) && x !== CLIENT_ID);
      const used = new Set((await spMembers(env)).map(x => x.rec.cid).filter(Boolean)), keep = [...new Set(ids)].slice(0, 24);
      const lost = [...used].filter(c => c !== CLIENT_ID && !keep.includes(c));
      if (lost.length) return answer(400, { error: 'Members are still in that app: move them first (they connect again).' });
      await env.SOTD.put('spapps', JSON.stringify(keep));
      return answer(200, { apps: [CLIENT_ID].concat(keep) });
    }
    case 'pushpic': {   // a picture for a notification (and the inbox), made small on the members page: kept in R2 at n/<id>.jpg (and a GIF as it is: picsUp)
      if (!env.MEDIA) return answer(503, { error: 'No R2 bucket is bound to the Worker as MEDIA yet.' });
      const up = await picsUp(env, new URL(request.url).origin, body);
      return answer(up.error ? 400 : 200, up);
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
      await env.SOTD.put('invpage:' + pid, JSON.stringify({ link, title: String(body.title || (m.name + ' is invited')).slice(0, 120), name: m.name, id: body.id, at }), { expirationTtl: ttl });
      return answer(200, { page: pid, url: new URL(request.url).origin + '/i/' + pid });
    }
    case 'invitemail': {   // the invitation, sent as a designed email from John's Gmail, the card and a button to open it (John, 6 Nov 2026: "the email is plain text ... can the invite look good, like everything else")
      const m = all[body.id];
      if (!m) return answer(404, { error: 'No such member.' });
      const pid = String(body.page || ''), page = /^[\w-]{16}$/.test(pid) ? await env.SOTD.get('invpage:' + pid, 'json') : null;
      if (!page) return answer(400, { error: 'Make the invitation first.' });
      const to = body.to ? cleanEmail(body.to) : (m.email || '');
      if (!to && !body.preview) return answer(400, { error: body.to ? 'That doesn’t look like an email address.' : 'No email address for them yet.' });
      if (!mailReady(env) && !body.preview) return answer(503, { error: 'Email isn’t set up yet.' });
      const origin = new URL(request.url).origin, url = origin + '/i/' + pid, img = origin + '/i/' + pid + '.jpg', first = String(m.name || '').split(' ')[0];
      const t = mailTint();
      const inner = '<h1 style="margin:0 0 10px;font:500 26px/1.25 Georgia,\'Times New Roman\',serif;color:#1d0b1e">' + mailEsc(page.title || (m.name + ' is invited')) + '</h1>'
        + '<p style="margin:0 0 18px;font:15px/1.5 Helvetica,Arial,sans-serif;color:#4a3a42">' + mailEsc((first ? first + ', you’re' : 'You’re') + ' invited to John’s Song of the Day: a song each day, with its story, on a calendar you can play from.') + '</p>'
        + '<a href="' + mailEsc(url) + '" style="display:block;margin:0 0 20px"><img src="' + mailEsc(img) + '" width="512" alt="The invitation" style="display:block;width:100%;max-width:512px;height:auto;border:0;border-radius:14px"></a>'
        + '<p style="margin:0 0 18px;text-align:center"><a href="' + mailEsc(url) + '" style="display:inline-block;padding:14px 28px;border-radius:999px;background:' + t + ';color:#ffffff;font:600 15px/1 Helvetica,Arial,sans-serif;text-decoration:none">Open your invitation</a></p>'
        + '<p style="margin:0;font:13px/1.5 Helvetica,Arial,sans-serif;color:#6b5a62">It works for ' + INVITE_DAYS + ' days, on your phone or computer.</p>';
      if (body.preview) return answer(200, { ok: true, preview: true, to, subject: page.title || 'An invitation to John’s Song of the Day', html: mailShell(inner) });   // (shown on the Members page before it's sent)
      try { await sendMail(env, to, page.title || 'An invitation to John’s Song of the Day', (first ? first + ', you’re' : 'You’re') + ' invited to John’s Song of the Day.\n\nOpen your invitation: ' + url + '\n', 'invite', mailShell(inner)); }
      catch (e) { return answer(502, { error: 'It didn’t send: ' + String(e && e.message || e).slice(0, 160) }); }
      if (body.to && !m.email && !Object.values(all).some(x => x.email === to)) { m.email = to; await env.SOTD.put('members', JSON.stringify(all)); }   // (their address kept, for codes by email)
      return answer(200, { ok: true, to });
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
      files.forEach(f => {   // a song or video whose original has been deleted (media_originals.py) is there as its copy, with
        const m = /^([lh])\/([0-9A-Za-z]{22})\.(m4a|mp4)$/.exec(f.key);   // the original's size and fingerprint kept
        if (!m) return;
        const k = m[1] === 'l' ? 'a' : 'v', t = m[2], e = (c[t] = c[t] || {}), o = was[t] || {};
        if (e[k]) return;
        e[k] = o[k] || f.size; if (o[k] && o['f' + k]) e['f' + k] = o['f' + k];
      });
      await env.SOTD.put('mediaCatalog', JSON.stringify(c));
      return answer(200, { files, catalog: c });
    }
    case 'delete': {
      if (!env.MEDIA) return answer(503, { error: 'No R2 bucket is bound to the Worker as MEDIA yet.' });
      if (!MEDIA_KEY_RE.test(String(body.key || ''))) return answer(400, { error: 'Not one of the files.' });
      await env.MEDIA.delete(body.key); await catalogSet(env, body.key, 0);
      if (body.key[0] === 'v') await env.MEDIA.delete('h/' + body.key.slice(2));   // and its TV copy
      if (body.key[0] === 'a') await env.MEDIA.delete('l/' + body.key.slice(2, 24) + '.m4a');   // and its lighter copy
      await env.SOTD.put('r2want', JSON.stringify({ at: Date.now(), key: body.key }));   // (playlist-r2 gets it again: /upl/wanted, for Playlist push)
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
  const light = LIGHT_KEY_RE.test(key);
  if (!MEDIA_KEY_RE.test(key) && !light) return answer(400, { error: 'Not one of the files (a/<track>.mp3, l/<track>.m4a or v/<track>.mp4).' });
  if (op === 'start') {
    const up = await env.MEDIA.createMultipartUpload(key, { httpMetadata: { contentType: light ? 'audio/mp4' : MEDIA_KINDS[key[0]].type } });
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
    if (light) {   // the song as it's played: in the catalog as the song, and an earlier MP3 of it replaced (no copy to make)
      await catalogSet(env, 'a/' + key.slice(2, 24) + '.mp3', obj.size, typeof body.fp === 'string' ? body.fp.slice(0, 80) : '');
      await env.MEDIA.delete('a/' + key.slice(2, 24) + '.mp3');
      return answer(200, { ok: true, key, size: obj.size });
    }
    await catalogSet(env, key, obj.size, typeof body.fp === 'string' ? body.fp.slice(0, 80) : '');
    if (key[0] === 'v') { await env.MEDIA.delete('h/' + key.slice(2)); await startTvCopy(env, key.slice(2, 24)); }   // a new video: its TV copy made again
    if (key[0] === 'a') { await env.MEDIA.delete('l/' + key.slice(2, 24) + '.m4a'); await startTvCopy(env, key.slice(2, 24), 'audio-light.yml'); }   // a new song: its lighter copy made again
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
  const um = /^\/s\/u\/([\w-]{6,24})(?:\/(\d{4}-\d{2}(?:-\d{2})?))?$/.exec(url.pathname);
  if (um) return uplSharePage(url, env, um[1], um[2] || '');
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

async function memberSongs(request, url, env, answer) {   // GET ?ids=a,b: songs from members' playlists that whoever asks sees (My playlist's and saved playlists', played anywhere)
  const who = await memberWho(request, env), all = who ? who.all : ((await env.SOTD.get('members', 'json')) || {}), me = who ? who.id : null;
  const ids = new Set(String(url.searchParams.get('ids') || '').split(',').filter(t => ID_RE.test(t)).slice(0, 200)), out = {};
  if (!ids.size) return answer(200, { songs: out });
  for (const [pid, rec] of Object.entries(await uplAll(env))) {
    if (!uplCan(all, me, rec) || !((await uplSongs(env, pid)).songs || []).some(x => ids.has(x.id))) continue;
    const v = await uplView(env, all, me, pid, rec, ids);
    for (const x of v.songs) if (ids.has(x.id) && !out[x.id]) out[x.id] = Object.assign(x, { upl: pid, ut: v.t, un: v.n, ush: v.sh });
  }
  return answer(200, { songs: out });
}
const uplCards = new Map();   // a public one's card pictures on the site (made by the site job): whether each is there, for 10 minutes
async function uplCardThere(u) {
  const k = uplCards.get(u); if (k && k.until > Date.now()) return k.ok;
  const r = await fetch(u, { method: 'HEAD' }).catch(() => null), ok = !!(r && r.ok);
  uplCards.set(u, { ok, until: Date.now() + 600e3 });
  return ok;
}
async function uplSharePage(url, env, pid, when) {   // a public member's calendar, one of its months (yyyy-mm) or days (yyyy-mm-dd), for links as John's own
  // day pages are: its card (the site job's, songs/u/<id>/…/card.jpg; the cover until it's there), its title and song, then the calendar there,
  // with the link's ?play= (and ?draft=1: the draft calendar)
  const U = await uplAll(env), rec = U[pid], all = (await env.SOTD.get('members', 'json')) || {}, site = 'https://cooperking.github.io/songs/';
  const q = new URLSearchParams(url.search), draft = q.get('draft') === '1'; q.delete('draft');
  const home = site + (draft ? 'draft/' : '') + (q.toString() ? '?' + q.toString() : '');
  // every playlist's links have a card (John, 7 Nov 2026: "Private and members-only playlists should use best quality pictures too, as I want to share it on whatsapp with members"): its name, caption and the card drawn where it was shared; the calendar itself still asks them to sign in
  if (!rec || !all[rec.o] || rec.st !== 'ok') return Response.redirect(site, 302);
  const pub = uplCan(all, null, rec), v0 = await uplView(env, all, pub ? null : rec.o, pid, rec), v = pub ? v0 : Object.assign({}, v0, { songs: v0.songs.filter(x => !x.w && !x.f) }), month = /^\d{4}-\d{2}$/.test(when) ? when : '', day = month ? '' : when;
  const s = day ? v.songs.find(x => x.d === day) : null, inMonth = month ? v.songs.filter(x => x.d.slice(0, 7) === month) : [];
  const slugD = d => d.slice(8, 10) + '-' + d.slice(5, 7) + '-' + d.slice(0, 4), slugM = m => m.slice(5, 7) + '-' + m.slice(0, 4);
  if ((day && !s) || (month && !inMonth.length) || !v.songs.length) return Response.redirect(home + '#u=' + pid, 302);
  const to = home + '#u=' + pid + (s ? '&d=' + slugD(day) : month ? '&m=' + slugM(month) : '');
  const cardUrl = site + 'u/' + pid + '/' + (s ? slugD(day) + '/' : month ? slugM(month) + '/' : '') + 'card.jpg', card = await uplCardThere(cardUrl);
  const ck = s ? day : month || 'all', drawn = (rec.cards || {})[ck], own = drawn ? LIVE_ORIGIN + '/up/' + pid + '-c-' + ck + '.jpg?v=' + drawn : '';   // (the card drawn on the page as it was shared: 1200 by 630)
  const newest = (month ? inMonth : v.songs)[(month ? inMonth : v.songs).length - 1], wide = !!own || card, img = own || (card ? cardUrl : ((s || newest).img || ''));   // (the site job's wide card, 1200 by 630, as John's own links have: WhatsApp shows it large; its own square picture only showed small: John, 7 Nov 2026)
  const e = x => esc(String(x)), n = (month ? inMonth : v.songs).length, songsN = n + (n === 1 ? ' song' : ' songs');
  const monthName = month ? new Date(month + '-15T12:00:00Z').toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' }) : '';
  // its own name and caption (John, 7 Nov 2026: "it should be correct for the playlist or calendar it's sharing ... Should say the title and the caption")
  const name = rec.cvt || v.n, cap = rec.cap || '', what = rec.mode === 'list' ? songsN : 'A song a day, ' + n + ' so far';
  const title = s ? s.title : month ? name + ': ' + monthName : name;
  const desc = s ? (s.artists && s.artists.length ? s.artists.join(', ') : s.artist) + ' · ' + name + ', ' + alexaDay(day) + ' ' + day.slice(0, 4)
    : month ? (cap ? cap + ' · ' : '') + songsN + ' for each day' : (cap ? cap + ' · ' : '') + what;
  const alt = s ? s.title + ' by ' + s.artist : month ? 'Covers of the songs for ' + monthName : 'Covers of ' + name;
  return new Response(`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${e(title)}</title>
<meta name="description" content="${e(desc)}">
<meta property="og:type" content="${s ? 'music.song' : 'website'}">
<meta property="og:site_name" content="Song of the Day">
<meta property="og:title" content="${e(title)}">
<meta property="og:description" content="${e(desc)}">
<meta property="og:url" content="${e(url.origin + url.pathname)}">
${img ? `<meta property="og:image" content="${e(img)}">
<meta property="og:image:secure_url" content="${e(img)}">
<meta property="og:image:type" content="image/jpeg">
${wide ? `<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
` : ''}<meta property="og:image:alt" content="${e(alt)}">
<meta name="twitter:image" content="${e(img)}">
` : ''}<meta name="twitter:card" content="${wide ? 'summary_large_image' : 'summary'}">
<meta name="twitter:title" content="${e(title)}">
<meta name="twitter:description" content="${e(desc)}">
<link rel="icon" type="image/png" sizes="96x96" href="https://cooperking.github.io/songs/favicon-96.png?v=2">
<link rel="icon" type="image/png" sizes="192x192" href="https://cooperking.github.io/songs/icon-192.png?v=2">
<link rel="apple-touch-icon" sizes="180x180" href="https://cooperking.github.io/songs/apple-touch-icon.png?v=2">
<script>if (!${INVITE_BOTS}.test(navigator.userAgent)) location.replace(${JSON.stringify(to)});</script>
</head>
<body style="font-family: Georgia, serif; padding: 24px;">
<p><a href="${e(to)}">${e(title)}</a>: ${e(desc)}.</p>
</body>
</html>
`, { headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });
}

// Each phone, tablet, computer or TV keeps an id of its own (X-Device, or device in a join): a sign-in there replaces
// any earlier one from the same device, and the members page counts devices, not sign-ins. A sign-in from before the ids
// counts once it has said which device it is (its next visit); old ones that never come back aren't counted, and go
// after 30 days.
function deviceId(x) { const d = String(x || ''); return /^[A-Za-z0-9]{8,40}$/.test(d) ? d : ''; }
// Which app each device is, for the Members and Apps tabs: the calendar page says (X-App on /members/me) play:<build>
// Google Play's Android app, apk:<build> the website's Android app, ios:<build> the iPhone app, tv the Fire TV app, home
// a Home Screen icon or installed web app, web a browser. What it runs on comes from its User-Agent (only the name is
// kept: iPhone, Android, Mac...). Devices that haven't said yet (the page before 28 Oct 2026) have neither.
const APP_RE = /^(play|apk|ios|tv|home|web)(?::(\d{1,5}))?$/;
function deviceOs(ua) {
  ua = String(ua || '');
  return /SOTDTV|AFT[A-Z0-9]/.test(ua) ? 'Fire TV' : /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android'
    : /CrOS/.test(ua) ? 'Chromebook' : /Macintosh|Mac OS X/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : '';
}
function deviceSays(dev, request) {   // true when it changed
  const a = APP_RE.exec(request.headers.get('X-App') || ''), os = deviceOs(request.headers.get('User-Agent'));
  let changed = false;
  if (a) {
    let v = a[0];
    if (!a[2] && dev.app && dev.app.startsWith(a[1] + ':')) v = dev.app;   // (its build not known this time: the one it said before stays)
    if (dev.app !== v) { dev.app = v; changed = true; }
  }
  if (os && dev.os !== os) { dev.os = os; changed = true; }
  return changed;
}
function deviceApps(m, seenAt = {}) {   // each counted device, the latest active first: {app, build, os, seen}
  return Object.entries(m.devices || {}).filter(([, d]) => d.did).map(([h, d]) => {
    const a = APP_RE.exec(d.app || '');
    return { app: a ? a[1] : '', build: a && a[2] ? Number(a[2]) : null, os: d.os || '', seen: Math.max(seenAt[h] || 0, d.seen || 0) || null };
  }).sort((x, y) => (y.seen || 0) - (x.seen || 0));
}
// When each device was last active: any signed-in call from it (opening the calendar or coming back to it, its inbox,
// playlists, what's playing, notifications), at most once every 5 minutes a device, in a KV key of its own:
// seen:<its sign-in's hash>, the time in the key's metadata (one list of seen: gives them all). Until 28 Oct 2026 it was
// kept in the members record, rewritten at most every 6 hours: that one value holds every member, and a visit
// rewriting it could put back a copy read before a change on the Members tab (KV keeps the last write; a read can be a
// minute old).
const SEEN_EVERY = 5 * 60e3, SEEN_DAYS = 400;
const seenMem = new Map();   // (this Worker's own last writes, so a busy device isn't written every call)
async function seenTouch(env, hash) {
  const now = Date.now();
  if (now - (seenMem.get(hash) || 0) < SEEN_EVERY) return;
  seenMem.set(hash, now);
  await kvSoft(env, 'seen:' + hash, '', { metadata: { t: now }, expirationTtl: SEEN_DAYS * 86400 });
}
async function seenAll(env) {   // { sign-in hash: when last active }
  const out = {};
  let cursor;
  try {
    do {
      const r = await env.SOTD.list({ prefix: 'seen:', cursor });
      for (const k of r.keys) if (k.metadata && k.metadata.t) out[k.name.slice(5)] = k.metadata.t;
      cursor = r.list_complete ? null : r.cursor;
    } while (cursor);
  } catch (e) { console.log('seen: list failed: ' + (e && e.message)); }
  return out;
}
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
  const did = id && all[id].devices[hash].did;
  if (id) { delete all[id].devices[hash]; await kvSoft(env, 'members', JSON.stringify(all)); }
  if (id && did && env.NOW) await env.NOW.get(env.NOW.idFromName(id)).fetch('https://now/widget', { method: 'DELETE', body: JSON.stringify({ dev: did }) }).catch(() => {});   // its iPhone widget no longer told
  return answer(200, { ok: true });
}

// whether a member's invitation has run out: { why: 'time' (past its INVITE_DAYS) or 'used' (on INVITE_USES devices), say }
function inviteGone(m) {
  const v = m && m.invite;
  if (!v) return null;
  if (v.until < Date.now()) return { why: 'time', say: 'Your invitation ran out on ' + new Date(v.until).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', timeZone: 'Europe/London' }) + '.' };
  if (v.uses >= INVITE_USES) return { why: 'used', say: 'Your invitation has been used on ' + INVITE_USES + ' devices, the most it can be.' };
  return null;
}
// POST /members/ask { code }: someone whose invitation has run out asks John for a new one. John is told in his inbox and
// on his phones (the member marked This is me), at most once in ASK_HOURS for the same invitation; the Members tab shows
// when they asked (invite.asked).
const ASK_HOURS = 12;
async function memberAsk(request, env, answer) {
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const failKey = 'joinfail:' + ip + ':' + new Date().toISOString().slice(0, 13);
  const fails = Number(await env.SOTD.get(failKey)) || 0;
  if (fails >= 20) return answer(429, { error: 'Too many tries from here this hour.' });
  const body = await request.json().catch(() => ({}));
  const all = (await env.SOTD.get('members', 'json')) || {}, hash = await sha256hex(String(body.code || ''));
  const id = Object.keys(all).find(k => all[k].invite && all[k].invite.hash === hash), m = id && all[id];
  if (!m) { await env.SOTD.put(failKey, String(fails + 1), { expirationTtl: 7200 }); return answer(404, { error: 'This invitation isn’t valid.' }); }
  const gone = inviteGone(m);
  if (!gone) return answer(400, { error: 'Your invitation still works: open it again.' });
  if (m.invite.asked && Date.now() - m.invite.asked < ASK_HOURS * 3600e3) return answer(200, { ok: true, asked: m.invite.asked, again: true });
  m.invite.asked = Date.now();
  await env.SOTD.put('members', JSON.stringify(all));
  const owner = await env.SOTD.get('owner');
  if (owner && all[owner]) {
    const msg = { title: m.name + ' asks for a new invitation', body: gone.say.replace(/^Your invitation/, 'Their invitation') + ' Send a new one from the Members tab: New link.',
      url: SITE_SONGS + 'admin.html#members', tag: 'sotd-ask-' + id };
    await pushAll(env, msg, [owner], { kind: 'ask', via: { t: 'ask', name: m.name } });
  }
  return answer(200, { ok: true, asked: m.invite.asked });
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
    if (!m) {
      await env.SOTD.put(failKey, String(fails + 1), { expirationTtl: 7200 });
      return answer(403, { error: 'This invitation isn’t valid. Ask John for a new one.' });
    }
    const gone = inviteGone(m);
    if (gone) return answer(403, Object.assign({ error: gone.say + ' Ask John for a new one.', gone: gone.why, name: m.name, until: m.invite.until }, m.invite.asked ? { asked: m.invite.asked } : {}));
  }
  const token = randomCode(32);
  if (tvKey) await env.SOTD.delete(tvKey); else m.invite.uses++;
  m.devices = m.devices || {};
  if (!m.joined) m.joined = Date.now();   // (hasJoined)
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
  if (dev.viewas && dev.until < Date.now()) { delete m.devices[hash]; await env.SOTD.put('members', JSON.stringify(all)); return answer(401, { error: 'That hour as them is over.' }); }
  const did = dev.viewas ? '' : deviceId(request.headers.get('X-Device'));   // (See as them: not one of their devices)
  let save = false;
  if (did && dev.did !== did) { dev.did = did; deviceTidy(all, hash, did); save = true; }   // says which device it is: earlier sign-ins there go
  if (deviceSays(dev, request)) save = true;   // which app it is, and what it runs on
  if (save) { dev.seen = Date.now(); await kvSoft(env, 'members', JSON.stringify(all)); }
  await seenTouch(env, hash);   // (when it was last active: its own key, not the members record)
  const exp = Math.floor(Date.now() / 1000) + LINK_HOURS * 3600, base = new URL(request.url).origin, media = {};
  const see = mediaFor(m);   // (only what John lets them have)
  if (env.MEDIA && env.ADMIN_KEY && (see.a || see.v)) {
    for (const [track, e] of Object.entries(await catalog(env))) {
      const one = {};
      for (const kind of Object.keys(MEDIA_KINDS)) if (e[kind] && see[kind]) one[kind] = base + '/m/' + kind + '/' + track + '.' + MEDIA_KINDS[kind].ext + '?e=' + exp + '&s=' + await mediaSig(env, kind, track, exp);
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
  // the members they can send notifications to (others: not those kept apart from them, nor those not taking theirs;
  // John, everyone), and the members they can see, to choose whose they take (seen: not John, whose always come)
  const ownerId = await env.SOTD.get('owner'), byName = (a, b) => a.name.localeCompare(b.name);
  // only members who have joined (signed in at least once) in either; a send to everyone goes to these others only
  const others = Object.keys(all).filter(k => k !== id && hasJoined(all[k]) && takesFrom(all, k, id, ownerId)).map(k => ({ id: k, name: all[k].name })).sort(byName);
  // every member, to share a playlist with or send a share to (John, 7 Nov 2026: "Sharing should include any members, not just those with notifications on"): off, those who've turned off messages from them
  const mem = Object.keys(all).filter(k => k !== id && !apart(all, id, k)).map(k => Object.assign({ id: k, name: all[k].name }, takesFrom(all, k, id, ownerId) ? {} : (all[k].mute || []).includes(id) ? { off: 2 } : { off: 1 })).sort(byName);
  const seen = Object.keys(all).filter(k => k !== id && k !== ownerId && hasJoined(all[k]) && !apart(all, id, k)).map(k => ({ id: k, name: all[k].name })).sort(byName);
  const bell = bellView(all, id, await env.SOTD.get('bell:' + id, 'json')).unseen;   // reactions to their messages not yet seen
  const U0 = await uplAll(env), pls = await uplMine(env, uplList(all, id, U0, await uplAdm(env, id)), U0);   // members' own playlists they can see, and their own (with the songs waiting for them)
  const fu = { n: await uplFileCount(env, id, U0), max: (await env.SOTD.get('owner')) === id || filesMax(m) >= FILES_NO_LIMIT ? 0 : filesMax(m) };
  if (fu.max) { const plan = await uplDlPlan(env, id, U0); if (plan) fu.u = plan.u; }   // (u: their songs with files, or to have them: the allowance used)   // the songs with their own MP3 or MP4, and the most (0: John's, no limit)
  return answer(200, Object.assign({ me: id, name: m.name, email: m.email || '', mailme: mailMode(m), pushme: pushMode(m), exp, media, see, mycovers, unread, bell, pls, fu, others, mem, seen, msgs: !m.nomsg,
    mute: (m.mute || []).filter(k => seen.some(x => x.id === k)) }, owner ? { owner: true } : {}));
}

// an invitation's link: a page that shows a proper card in a message (WhatsApp, iMessage...), like a day's share page,
// and sends the person on to their invitation on the calendar. Kept for as long as the invitation lasts.
// the steps for getting the phone's own app (below), for everyone (John, 27 Oct 2026); false shows them only with ?try on
// the end of an invitation's link, everyone else going straight on to the calendar
const INVITE_STEPS = true;
const PLAY_PACKAGE = 'io.github.cooperking.sotd';   // the Android app on Google Play (closed testing from 28 Oct 2026)
const PLAY_TEST = 'https://play.google.com/apps/testing/' + PLAY_PACKAGE, PLAY_APP = 'https://play.google.com/store/apps/details?id=' + PLAY_PACKAGE;
const INVITE_BOTS = /bot|crawl|spider|preview|facebookexternalhit|facebot|whatsapp|telegram|slack|discord|linkedin|pinterest|embedly|iframely|skype|viber|signal|vkshare|redditbot|applebot|google|bing/i;
async function invitePage(url, env) {
  const m = /^\/i\/([\w-]{16})(\.jpg|\.json)?$/.exec(url.pathname);
  const gone = () => new Response('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>John\'s Song of the Day</title><p style="font-family:Georgia,serif;padding:24px">This invitation has run out. Ask John for a new one, or <a href="https://cooperking.github.io/songs/">open the calendar</a>.</p>', { status: 404, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
  if (!m) return gone();
  if (m[2] === '.jpg') {
    const img = await env.SOTD.get('invcard:' + m[1], 'arrayBuffer');
    if (!img) return new Response('Not here.', { status: 404 });
    return new Response(img, { headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'public, max-age=3600' } });
  }
  const p = await env.SOTD.get('invpage:' + m[1], 'json');
  if (m[2] === '.json') {   // the iPhone app, opened by this invitation's link (Apple's universal links): where it leads
    const h = { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*' };
    return new Response(JSON.stringify(p ? { link: p.link, title: p.title, name: p.name || '' } : { error: 'This invitation has run out.' }), { status: p ? 200 : 404, headers: h });
  }
  if (!p) return gone();
  // the apps, as the site says where they are: the iPhone app's TestFlight public link (songs/ios.json, once Apple has
  // approved a build) and the Android app (songs/android.json)
  const get = u => fetch(u, { cf: { cacheTtl: 300 } }).then(r => r.ok ? r.json() : {}).catch(() => ({}));
  const [ios, android] = await Promise.all([get(SITE_SONGS + 'ios.json'), get(SITE_SONGS + 'android.json')]);
  const tf = /^https:\/\/testflight\.apple\.com\/join\/\w+$/.test(ios.testflight || '') ? ios.testflight : '';
  const apk = /^https:\/\/cooperking\.github\.io\//.test(android.apk || '') ? android.apk : '';
  // with an email address (John puts the members' emails on Google Play's test list, from the CSV on the Members and Apps
  // tabs): Google Play's steps for them on Android, with the website's app as the way round should Play not have them yet
  const who = p.id ? ((await env.SOTD.get('members', 'json')) || {})[p.id] : null, play = !!(who && who.email);
  const e = x => esc(String(x)), img = url.origin + '/i/' + m[1] + '.jpg?v=' + p.at, desc = 'John\'s Song of the Day';
  const steps = INVITE_STEPS || url.searchParams.has('try'), own = url.origin + '/i/' + m[1];
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
<style>
  :root { --fg: #fffaf5; --muted: rgba(255, 250, 245, .82); --ink: #1b1020; }
  body { margin: 0; min-height: 100vh; color: var(--fg); font: 17px/1.5 Georgia, "Times New Roman", serif;
    background: radial-gradient(60vmax 50vmax at 80% 0%, #ff4f7b, transparent 70%), radial-gradient(50vmax 50vmax at 0% 100%, #c41d8a, transparent 70%), #e2335f; }
  main { max-width: 440px; margin: 0 auto; padding: max(28px, env(safe-area-inset-top)) 18px 40px; display: none; }
  html.steps main { display: block; }
  *, *::before, *::after { box-sizing: border-box; }
  .card { width: 100%; height: auto; aspect-ratio: 1200 / 630; object-fit: cover; border-radius: 14px; box-shadow: 0 18px 50px rgba(24, 8, 24, .3); display: block; }
  h1 { font-weight: 500; font-size: 30px; line-height: 1.1; margin: 22px 0 6px; }
  p { margin: 0 0 14px; color: var(--muted); }
  ol { list-style: none; margin: 22px 0 0; padding: 0; display: grid; gap: 14px; }
  li { background: rgba(255, 255, 255, .14); border: 1px solid rgba(255, 255, 255, .24); border-radius: 16px; padding: 14px 16px; }
  li b.n { display: inline-grid; place-items: center; width: 24px; height: 24px; border-radius: 50%; background: var(--fg); color: var(--ink); font: 600 13px/1 -apple-system, system-ui, sans-serif; margin-right: 8px; }
  li p { margin: 8px 0 12px; font-size: 15px; }
  .btn { display: block; text-align: center; text-decoration: none; padding: 13px 16px; border-radius: 999px; background: var(--fg); color: var(--ink); border: 0; width: 100%;
    font: 600 13px/1 -apple-system, system-ui, sans-serif; letter-spacing: .1em; text-transform: uppercase; cursor: pointer; }
  .btn.ghost { background: transparent; color: var(--fg); border: 1px solid rgba(255, 255, 255, .6); }
  .small { font-size: 14px; margin-top: 18px; }
  .small a { color: var(--fg); }
</style>
<script>
(function () {
  var ua = navigator.userAgent, link = ${JSON.stringify(p.link)};
  if (${INVITE_BOTS}.test(ua)) return;
  var iphone = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1), android = /Android/.test(ua);
  // a phone with its own app to get: the steps; anything else, straight on to the calendar, as before
  if (${JSON.stringify(steps)} && ((iphone && ${JSON.stringify(!!tf)}) || (android && ${JSON.stringify(!!apk || play)}))) { document.documentElement.classList.add('steps', iphone ? 'iphone' : 'android'); return; }
  location.replace(link);
})();
</script>
</head>
<body>
<main>
  <img class="card" src="${e(img)}" alt="" width="1200" height="630">
  <h1>${e(p.title)}</h1>
  <p>John’s Song of the Day: a song for every day, with John’s own recordings and videos for members.</p>
  <ol id="steps"></ol>
  ${play && apk ? `<p class="small" id="orapk" hidden>Google Play says you can’t join the test? John may not have added you to it yet: <a href="${e(apk)}">get the app from the website</a> instead (Android asks to allow installing from your browser: allow it, then tap Install).</p>` : ''}
  <p class="small">Or <a href="${e(p.link)}">open the calendar here in the browser</a> instead.</p>
</main>
<script>
(function () {
  var html = document.documentElement; if (!html.classList.contains('steps')) return;
  var iphone = html.classList.contains('iphone'), link = ${JSON.stringify(p.link)}, own = ${JSON.stringify(own)};
  var steps = iphone ? [
    { t: 'Get the iPhone app', p: 'Apple’s TestFlight app comes first, then Song of the Day. Your invitation is copied as you go: when the app opens, it offers to sign you in with it.', a: ${JSON.stringify(tf)}, copy: true },
    { t: 'Open the app', p: 'If it doesn’t offer to sign you in, tap your invitation in the message again: it opens the app, signed in.' }
  ] : ${JSON.stringify(play)} ? [
    { t: 'Join the test on Google Play', p: 'Make sure Google Play on this phone is signed in to the Google account John has for you, then tap Become a tester.', a: ${JSON.stringify(PLAY_TEST)} },
    { t: 'Get the app from Google Play', p: 'If you have the app from the website already, uninstall that first: Google Play installs its own copy.', a: ${JSON.stringify(PLAY_APP)} },
    { t: 'Open your invitation', p: 'It opens in the app and signs you in.', a: link, ghost: true }
  ] : [
    { t: 'Get the Android app', p: 'Android asks to allow installing from your browser: allow it, then tap Install.', a: ${JSON.stringify(apk)} },
    { t: 'Open your invitation', p: 'It opens in the app and signs you in.', a: link, ghost: true }
  ];
  var ol = document.getElementById('steps'), orapk = document.getElementById('orapk');
  if (orapk && !iphone) orapk.hidden = false;
  steps.forEach(function (st, i) {
    var li = document.createElement('li');
    li.innerHTML = '<b class="n">' + (i + 1) + '</b>' + st.t + '<p>' + st.p + '</p>';
    if (st.a) {
      var b = document.createElement('a'); b.className = 'btn' + (st.ghost ? ' ghost' : ''); b.href = st.a; b.textContent = st.t;
      if (st.copy) b.addEventListener('click', function (ev) {   // the invitation on the clipboard first, for the app to find
        if (!navigator.clipboard || !navigator.clipboard.writeText) return;
        ev.preventDefault();
        var go = function () { location.href = st.a; };
        navigator.clipboard.writeText(own).then(go, go);   // (this page's own link: the app finds who it's for, and the invitation, from it)
      });
      li.appendChild(b);
    }
    ol.appendChild(li);
  });
})();
</script>
</body>
</html>
`, { headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });
}

// Apple's universal links: an invitation's link (/i/<id>) tapped on an iPhone with the app opens the app, which signs in
// with it (the app's entitlement: applinks:sotd-playlists.cooperking.workers.dev; its team ID is the Worker's secret)
function appleLinks(env) {
  const team = String(env.APPLE_TEAM_ID || '').trim();
  const body = { applinks: { details: team ? [{ appIDs: [team + '.io.github.cooperking.sotd'], components: [{ '/': '/i/*', comment: 'invitations' }] }] : [] } };
  return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=3600' } });
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

// ---------- passkeys: a member's own way back in ----------
//
// A signed-in member saves a passkey in their Profile: their phone's or computer's Face ID, fingerprint or screen lock,
// kept by iCloud Keychain or their Google account (which give it to their other devices). A new device, or one signed
// out, then signs in with it, without John. The passkey belongs to the calendar's site (WA_RP, cooperking.github.io);
// the Worker keeps each one's public key on the member's record (m.passkeys) and checks each sign-in's signature with
// it. Removing the member removes their passkeys too. A challenge is made here and signed (no storage), lasts 5
// minutes and is used once. WA_RP and WA_ORIGIN (vars) are for testing on this computer only.
const PASSKEYS_MAX = 10, PASSKEY_MS = 5 * 60e3;
const waRp = env => env.WA_RP || 'cooperking.github.io';
const waOrigin = env => env.WA_ORIGIN || 'https://cooperking.github.io';
function cbor(b, p = 0) {   // just enough CBOR for WebAuthn (numbers, bytes, text, arrays, maps, true/false/null): [value, next]
  const mt = b[p] >> 5, ai = b[p] & 31; p++;
  let n = ai;
  if (ai === 24) n = b[p++];
  else if (ai === 25) { n = b[p] << 8 | b[p + 1]; p += 2; }
  else if (ai === 26) { n = b[p] * 2 ** 24 + (b[p + 1] << 16 | b[p + 2] << 8 | b[p + 3]); p += 4; }
  else if (ai > 26) throw new Error('cbor');
  if (mt === 0) return [n, p];
  if (mt === 1) return [-1 - n, p];
  if (mt === 2) return [b.slice(p, p + n), p + n];
  if (mt === 3) return [new TextDecoder().decode(b.slice(p, p + n)), p + n];
  if (mt === 4) { const a = []; for (let i = 0; i < n; i++) { let v; [v, p] = cbor(b, p); a.push(v); } return [a, p]; }
  if (mt === 5) { const o = new Map(); for (let i = 0; i < n; i++) { let k, v; [k, p] = cbor(b, p); [v, p] = cbor(b, p); o.set(k, v); } return [o, p]; }
  if (mt === 7 && ai >= 20 && ai <= 22) return [ai === 21 ? true : ai === 20 ? false : null, p];
  throw new Error('cbor');
}
function authData(ad) {   // the authenticator's data: its site's hash, flags, count and (when saving) the new passkey
  if (ad.length < 37) throw new Error('short');
  const out = { rp: ad.slice(0, 32), flags: ad[32], count: ((ad[33] << 24) >>> 0) + (ad[34] << 16 | ad[35] << 8 | ad[36]) };
  if (ad[32] & 0x40) {
    const len = ad[53] << 8 | ad[54];
    out.cred = ad.slice(55, 55 + len);
    out.cose = cbor(ad, 55 + len)[0];
  }
  return out;
}
function coseJwk(k) {   // a passkey's public key (COSE) as a JWK, with the signature it makes: ES256 (most) or RS256 (Windows)
  if (!(k instanceof Map)) throw new Error('key');
  if (k.get(1) === 2 && k.get(3) === -7 && k.get(-1) === 1) return { alg: -7, jwk: { kty: 'EC', crv: 'P-256', x: b64url(k.get(-2)), y: b64url(k.get(-3)) } };
  if (k.get(1) === 3 && k.get(3) === -257) return { alg: -257, jwk: { kty: 'RSA', n: b64url(k.get(-1)), e: b64url(k.get(-2)) } };
  throw new Error('That kind of passkey isn’t one this site can use.');
}
function derSig(der) {   // an ECDSA signature as WebAuthn gives it (DER) to r||s, as WebCrypto takes it
  if (der[0] !== 0x30) throw new Error('sig');
  let p = 2;
  const int = () => {
    if (der[p++] !== 2) throw new Error('sig');
    const len = der[p++]; let v = der.slice(p, p + len); p += len;
    while (v.length > 32 && v[0] === 0) v = v.slice(1);
    if (v.length > 32) throw new Error('sig');
    const o = new Uint8Array(32); o.set(v, 32 - v.length); return o;
  };
  const r = int(), s = int(), out = new Uint8Array(64); out.set(r); out.set(s, 32); return out;
}
async function pkVerify(pk, data, sig) {
  if (pk.alg === -7) {
    const key = await crypto.subtle.importKey('jwk', pk.jwk, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
    return crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, key, derSig(sig), data);
  }
  const key = await crypto.subtle.importKey('jwk', pk.jwk, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
  return crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, sig, data);
}
async function waMac(env, msg) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode('sotd-passkey|' + env.ADMIN_KEY), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(msg))).slice(0, 16);
}
async function waChallenge(env, purpose) {   // 16 random bytes, the time, and a signature over both and what it's for
  const r = crypto.getRandomValues(new Uint8Array(16)), t = String(Date.now());
  const head = b64url(r) + '.' + t;
  return b64url(new TextEncoder().encode(head + '.' + b64url(await waMac(env, purpose + '|' + head))));
}
async function waChallengeOk(env, challenge, purpose) {   // made here, for this, in the last 5 minutes, not used before
  let s = '';
  try { s = new TextDecoder().decode(unb64url(challenge)); } catch (e) { return false; }
  const [r, t, mac] = s.split('.');
  if (!r || !t || !mac || Date.now() - Number(t) > PASSKEY_MS || Number(t) > Date.now() + 60e3) return false;
  if (b64url(await waMac(env, purpose + '|' + r + '.' + t)) !== mac) return false;
  const used = 'wa:' + await sha256hex(challenge);
  if (await env.SOTD.get(used)) return false;
  await env.SOTD.put(used, '1', { expirationTtl: 600 });
  return true;
}
function waClient(body, type, env) {   // the browser's own account of the request: what it was, for which challenge, from where
  let c;
  try { c = JSON.parse(new TextDecoder().decode(unb64url(body.clientDataJSON))); } catch (e) { return null; }
  return c && c.type === type && c.origin === waOrigin(env) && typeof c.challenge === 'string' ? c : null;
}
function passkeyList(m) { return (m.passkeys || []).map(p => ({ id: p.id, name: p.name, at: p.at, used: p.used || null, synced: !!p.synced })); }
async function newDevice(all, m, did) {   // this device signed in as the member: its token (only its hash is kept)
  const token = randomCode(32), th = await sha256hex(token);
  m.devices = m.devices || {};
  if (!m.joined) m.joined = Date.now();   // (hasJoined)
  m.devices[th] = Object.assign({ at: Date.now(), seen: Date.now() }, did ? { did } : {});
  deviceTidy(all, th, did);
  return token;
}

// a signed-in member's own passkeys. GET: { passkeys: [{ id, name, at, used, synced }] }. POST { op: 'begin' }: what
// the browser needs to make one; { op: 'save', clientDataJSON, attestationObject, name }: the new one checked and kept;
// { op: 'remove', id }: one gone.
async function memberPasskeys(request, env, answer) {
  const token = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  const hash = token ? await sha256hex(token) : '';
  const all = (await env.SOTD.get('members', 'json')) || {};
  const id = hash && Object.keys(all).find(k => all[k].devices && all[k].devices[hash]);
  if (!id) return answer(401, { error: 'Not a member (any more).' });
  const m = all[id];
  if (!env.ADMIN_KEY) return answer(503, { error: 'Passkeys aren’t set up yet.' });
  if (request.method === 'GET') return answer(200, { passkeys: passkeyList(m), rpId: waRp(env), user: b64url(new TextEncoder().encode(id)) });
  if (request.method !== 'POST') return answer(405, { error: 'Use GET or POST.' });
  const body = await request.json().catch(() => ({}));
  if (body.op === 'begin') {
    return answer(200, { options: {
      challenge: await waChallenge(env, 'save|' + id),
      rp: { id: waRp(env), name: 'John’s Song of the Day' },
      user: { id: b64url(new TextEncoder().encode(id)), name: m.name, displayName: m.name },
      pubKeyCredParams: [{ type: 'public-key', alg: -7 }, { type: 'public-key', alg: -257 }],
      authenticatorSelection: { residentKey: 'required', requireResidentKey: true, userVerification: 'preferred' },
      excludeCredentials: (m.passkeys || []).map(p => ({ type: 'public-key', id: p.id })),
      attestation: 'none', timeout: PASSKEY_MS } });
  }
  if (body.op === 'remove') {
    const before = (m.passkeys || []).length;
    m.passkeys = (m.passkeys || []).filter(p => p.id !== body.id);
    if (m.passkeys.length !== before) await env.SOTD.put('members', JSON.stringify(all));
    return answer(200, { ok: true, passkeys: passkeyList(m) });
  }
  if (body.op !== 'save') return answer(400, { error: 'Say what to do.' });
  const c = waClient(body, 'webauthn.create', env);
  if (!c || !await waChallengeOk(env, c.challenge, 'save|' + id)) return answer(400, { error: 'That took too long, or didn’t come from the calendar. Try again.' });
  let ad, key;
  try {
    const att = cbor(unb64url(body.attestationObject))[0];
    ad = authData(att.get('authData'));
    if (!ad.cred || !ad.cose) throw new Error('no passkey');
    key = coseJwk(ad.cose);
  } catch (e) { return answer(400, { error: /site can use/.test(e.message) ? e.message : 'That passkey couldn’t be read. Try again.' }); }
  if (b64url(ad.rp) !== b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(waRp(env)))) || !(ad.flags & 1))
    return answer(400, { error: 'That passkey isn’t for this site.' });
  const cid = b64url(ad.cred);
  if (Object.values(all).some(x => (x.passkeys || []).some(p => p.id === cid))) return answer(409, { error: 'That passkey is saved already.' });
  m.passkeys = m.passkeys || [];
  if (m.passkeys.length >= PASSKEYS_MAX) return answer(400, { error: 'That’s ' + PASSKEYS_MAX + ' passkeys already. Remove one first.' });
  const name = String(body.name || '').replace(/\s+/g, ' ').trim().slice(0, 40) || 'A passkey';
  m.passkeys.unshift({ id: cid, alg: key.alg, jwk: key.jwk, count: ad.count, at: Date.now(), name, synced: !!(ad.flags & 0x10) });
  await env.SOTD.put('members', JSON.stringify(all));
  return answer(200, { ok: true, passkeys: passkeyList(m) });
}

// signing in with a passkey (anyone may ask). POST { op: 'begin' }: a challenge for the browser; { op: 'finish', id,
// clientDataJSON, authenticatorData, signature, userHandle, device }: the signature checked with the passkey kept for
// that member, and this device signed in: { token, name }. A passkey no member has (removed, or its member removed):
// 404 with unknown: true, so the page can tell the phone to forget it.
async function memberPasskeySignin(request, env, answer) {
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  if (!env.ADMIN_KEY) return answer(503, { error: 'Passkeys aren’t set up yet.' });
  const body = await request.json().catch(() => ({}));
  if (body.op === 'begin') return answer(200, { options: { challenge: await waChallenge(env, 'signin'), rpId: waRp(env), userVerification: 'preferred', timeout: PASSKEY_MS } });
  if (body.op !== 'finish') return answer(400, { error: 'Say what to do.' });
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const failKey = 'joinfail:' + ip + ':' + new Date().toISOString().slice(0, 13);
  const fails = Number(await env.SOTD.get(failKey)) || 0;
  if (fails >= 20) return answer(429, { error: 'Too many tries from here this hour.' });
  const fail = async (status, error, extra) => { await env.SOTD.put(failKey, String(fails + 1), { expirationTtl: 7200 }); return answer(status, Object.assign({ error }, extra || {})); };
  const c = waClient(body, 'webauthn.get', env);
  if (!c || !await waChallengeOk(env, c.challenge, 'signin')) return fail(400, 'That took too long, or didn’t come from the calendar. Try again.');
  const all = (await env.SOTD.get('members', 'json')) || {};
  let uid = '';
  try { uid = new TextDecoder().decode(unb64url(body.userHandle)); } catch (e) {}
  const id = all[uid] && (all[uid].passkeys || []).some(p => p.id === body.id) ? uid : Object.keys(all).find(k => (all[k].passkeys || []).some(p => p.id === body.id));
  if (!id) return fail(404, 'This passkey has been taken off the calendar. Ask John for a new invitation, or use a code from your Profile on another device.', { unknown: true });
  const m = all[id], pk = m.passkeys.find(p => p.id === body.id);
  let ad, ok = false;
  try {
    const adb = unb64url(body.authenticatorData);
    ad = authData(adb);
    const cd = new Uint8Array(await crypto.subtle.digest('SHA-256', unb64url(body.clientDataJSON)));
    const data = new Uint8Array(adb.length + 32); data.set(adb); data.set(cd, adb.length);
    ok = b64url(ad.rp) === b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(waRp(env)))) && !!(ad.flags & 1) && await pkVerify(pk, data, unb64url(body.signature));
  } catch (e) { ok = false; }
  if (!ok) return fail(403, 'That passkey didn’t check out. Try again.');
  if ((pk.count || ad.count) && ad.count <= (pk.count || 0)) return fail(403, 'That passkey looks copied. Sign in with a code from your Profile on another device instead.');
  pk.count = ad.count; pk.used = Date.now();
  const token = await newDevice(all, m, deviceId(body.device));
  await env.SOTD.put('members', JSON.stringify(all));
  return answer(200, { token, name: m.name });
}

// ---------- a code by email: the way back in when a passkey won't do (John, 28 Oct 2026: "it would be good to have
// email codes to be sent as a back up in case passkeys fail") ----------
//
// John gives each member's email address on the Members tab. Sign in, then the member's email address: if it's a
// member's, a 6-digit code goes to it from John's Google account: through his Apps Script mailer (_sotd/mailer, set up
// with clasp; the Worker secrets MAILER_URL and MAILER_KEY), or else Gmail's API with his sign-in (GMAIL_CLIENT_ID,
// GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN); worker_deploy.py puts them there from the repository's secrets. The code works once, for MAIL_CODE_MINUTES, MAIL_CODE_TRIES tries; only its hash is kept (KV mailcode:<id>).
// The answer is the same whether or not the address is a member's. Each email sent, and each that couldn't be, is
// noted for the Status tab (KV mailLog, the last 20, the address shortened).
const MAIL_CODE_MINUTES = 10, MAIL_CODE_TRIES = 5, MAIL_PER_HOUR = 5, MAIL_IP_PER_HOUR = 10;
function cleanEmail(x) {
  const e = String(x || '').trim().toLowerCase();
  return e.length <= 254 && /^[^\s@<>()",;:]{1,64}@[a-z0-9.-]{1,190}\.[a-z]{2,24}$/.test(e) ? e : '';
}
function mailScript(env) { return /^https:\/\/script\.google\.com\/macros\/s\/[\w-]+\/exec$/.test(env.MAILER_URL || '') && !!env.MAILER_KEY; }
function mailReady(env) { return mailScript(env) || !!(env.GMAIL_CLIENT_ID && env.GMAIL_CLIENT_SECRET && env.GMAIL_REFRESH_TOKEN); }
function mailShort(e) { const [u, d] = String(e).split('@'); return (u || '').slice(0, 1) + '…@' + (d || ''); }
let gmailMem = null;
async function gmailToken(env) {
  if (gmailMem && gmailMem.exp > Date.now() + 60e3) return gmailMem.tok;
  const r = await fetch('https://oauth2.googleapis.com/token', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: env.GMAIL_CLIENT_ID, client_secret: env.GMAIL_CLIENT_SECRET, refresh_token: env.GMAIL_REFRESH_TOKEN, grant_type: 'refresh_token' }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.access_token) throw new HttpError(502, 'Google wouldn’t let the Worker use your Gmail: ' + (j.error_description || j.error || 'it answered ' + r.status));
  gmailMem = { tok: j.access_token, exp: Date.now() + (j.expires_in || 3600) * 1000 };
  return gmailMem.tok;
}
function b64utf8(s) { const b = new TextEncoder().encode(s); let t = ''; for (let i = 0; i < b.length; i += 0x8000) t += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000)); return btoa(t); }
async function mailNote(env, e) {
  const log = (await env.SOTD.get('mailLog', 'json')) || [];
  log.unshift(e); log.length = Math.min(log.length, 20);
  await kvSoft(env, 'mailLog', JSON.stringify(log));
}
async function sendMail(env, to, subject, text, what, html) {   // html: the formatted email (notifications), with text as its plain version   // from John's Google account (his own address on it)
  const note = { id: randomCode(6), at: Date.now(), what, to: mailShort(to) };
  try {
    if (!mailReady(env)) throw new HttpError(503, 'Emails aren’t set up yet: the Worker has no mailer.');
    if (mailScript(env)) {   // the Apps Script mailer (MailApp, as John; 100 a day): it answers { ok, left } or { error }
      const r = await fetch(env.MAILER_URL, { method: 'POST', redirect: 'follow', headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: JSON.stringify(Object.assign({ key: env.MAILER_KEY, to, subject, text }, html ? { html } : {})) });
      const j = await r.json().catch(() => null);
      if (!j || !j.ok) throw new HttpError(502, 'The Apps Script mailer didn’t send it: ' + ((j && j.error) || 'it answered ' + r.status + (r.ok ? ' (not its JSON: is it deployed for anyone?)' : '')));
      note.ok = true; note.left = j.left;
      await mailNote(env, note);
      return;
    }
    const part = (type, body) => ['Content-Type: ' + type + '; charset=UTF-8', 'Content-Transfer-Encoding: base64', '', b64utf8(body).replace(/.{76}/g, '$&\r\n')].join('\r\n');
    const bound = 'sotd' + randomCode(12);
    const raw = (html ? ['To: ' + to, 'Subject: =?UTF-8?B?' + b64utf8(subject) + '?=', 'MIME-Version: 1.0', 'Content-Type: multipart/alternative; boundary="' + bound + '"', '',
      '--' + bound, part('text/plain', text), '--' + bound, part('text/html', html), '--' + bound + '--', '']
      : ['To: ' + to, 'Subject: =?UTF-8?B?' + b64utf8(subject) + '?=', 'MIME-Version: 1.0', part('text/plain', text)]).join('\r\n');
    const send = async () => fetch('https://gmail.googleapis.com/gmail/v1/users/me/messages/send', { method: 'POST',
      headers: { Authorization: 'Bearer ' + await gmailToken(env), 'Content-Type': 'application/json' },
      body: JSON.stringify({ raw: b64utf8(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '') }) });
    let r = await send();
    if (r.status === 401) { gmailMem = null; r = await send(); }   // (its sign-in ran out early: a new one, once)
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      if (r.status === 401) gmailMem = null;
      throw new HttpError(502, 'Gmail didn’t send it: ' + ((j.error && j.error.message) || 'it answered ' + r.status));
    }
    note.ok = true;
  } catch (e) {
    note.ok = false; note.err = String(e.message || e).slice(0, 200);
    await mailNote(env, note);
    throw e;
  }
  await mailNote(env, note);
}
async function memberMailCode(request, env, answer) {   // POST { email }: a code to it, if it's a member's
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const body = await request.json().catch(() => ({}));
  const email = cleanEmail(body.email);
  if (!email) return answer(400, { error: 'That doesn’t look like an email address.' });
  if (!mailReady(env)) return answer(503, { error: 'Codes by email aren’t set up yet. Use a passkey, or ask John for a code.' });
  const hour = new Date().toISOString().slice(0, 13), ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const ipKey = 'mailip:' + ip + ':' + hour, n = Number(await env.SOTD.get(ipKey)) || 0;
  if (n >= MAIL_IP_PER_HOUR) return answer(429, { error: 'That’s a lot of codes from here this hour. Try again later.' });
  await kvSoft(env, ipKey, String(n + 1), { expirationTtl: 7200 });
  const all = (await env.SOTD.get('members', 'json')) || {};
  const id = Object.keys(all).find(k => all[k].email === email);
  if (id) {
    const key = 'mailcode:' + id, old = await env.SOTD.get(key, 'json');
    const sent = (old && old.hour === hour ? old.n : 0) || 0;
    if (old && old.sent > Date.now() - 60e3) return answer(200, { ok: true, minutes: MAIL_CODE_MINUTES });   // (one a minute: the last is on its way)
    if (sent >= MAIL_PER_HOUR) return answer(429, { error: 'That’s a lot of codes for that address this hour. Try again later.' });
    const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1e6).padStart(6, '0');
    await env.SOTD.put(key, JSON.stringify({ h: await sha256hex(id + ':' + code), until: Date.now() + MAIL_CODE_MINUTES * 60e3, tries: 0,
      sent: Date.now(), hour, n: sent + 1 }), { expirationTtl: MAIL_CODE_MINUTES * 60 + 3600 });
    try {
      await sendMail(env, email, 'Your Song of the Day code: ' + code,
        'Your code to sign in to John’s Song of the Day is ' + code + '.\n\nType it where you asked for it, within ' + MAIL_CODE_MINUTES
        + ' minutes. It works once.\n\nIf you didn’t ask for it, you can ignore this email: nobody can sign in without the code.\n', 'code',
        codeMailHtml(code, 'Type it where you asked for it, within ' + MAIL_CODE_MINUTES + ' minutes, to sign in to John’s Song of the Day. It works once.', 'If you didn’t ask for it, you can ignore this email: nobody can sign in without the code.'));
    } catch (e) { return answer(502, { error: 'The email couldn’t be sent just now. Use a passkey, or ask John for a code.' }); }
  }
  return answer(200, { ok: true, minutes: MAIL_CODE_MINUTES });
}
async function memberMailSignin(request, env, answer) {   // POST { email, code, device }: signed in, with the code from the email
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const body = await request.json().catch(() => ({}));
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const failKey = 'joinfail:' + ip + ':' + new Date().toISOString().slice(0, 13);
  const fails = Number(await env.SOTD.get(failKey)) || 0;
  if (fails >= 20) return answer(429, { error: 'Too many tries from here this hour.' });
  const fail = async (status, error) => { await env.SOTD.put(failKey, String(fails + 1), { expirationTtl: 7200 }); return answer(status, { error }); };
  const email = cleanEmail(body.email), code = String(body.code || '').replace(/\D/g, '');
  const all = (await env.SOTD.get('members', 'json')) || {};
  const id = email ? Object.keys(all).find(k => all[k].email === email) : '';
  const key = 'mailcode:' + id, rec = id ? await env.SOTD.get(key, 'json') : null;
  if (!rec || !rec.h || rec.until < Date.now() || rec.tries >= MAIL_CODE_TRIES) return fail(400, 'That code has run out. Ask for a new one.');
  if (code.length !== 6 || await sha256hex(id + ':' + code) !== rec.h) {
    rec.tries++;
    await env.SOTD.put(key, JSON.stringify(rec), { expirationTtl: 3600 });
    return fail(403, rec.tries >= MAIL_CODE_TRIES ? 'That isn’t the code, and that was the last try. Ask for a new one.' : 'That isn’t the code. Check the email and try again.');
  }
  await env.SOTD.put(key, JSON.stringify({ hour: rec.hour, n: rec.n, sent: rec.sent }), { expirationTtl: 3600 });   // (used: the hour's count stays)
  const m = all[id];
  const token = await newDevice(all, m, deviceId(body.device));
  await env.SOTD.put('members', JSON.stringify(all));
  return answer(200, { token, name: m.name });
}

// a member's own email address, from their Profile (John, 28 Oct 2026: "can users add and edit their own email
// addresses in their profile"): signed in, POST { op: 'begin', email } sends a code to the new address, to check it's
// theirs (KV mailnew:<id>: the address and the code's hash; one a minute, MAIL_PER_HOUR an hour); { op: 'confirm', code }
// saves it. A new address isn't on Google Play's test list yet: the Members tab shows John it's to be added.
async function memberEmail(request, env, answer) {
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const id = await memberFromToken(request, env);
  if (!id) return answer(401, { error: 'Not a member (any more).' });
  const body = await request.json().catch(() => ({}));
  const all = (await env.SOTD.get('members', 'json')) || {}, m = all[id];
  if (!m) return answer(401, { error: 'Not a member (any more).' });
  const key = 'mailnew:' + id, hour = new Date().toISOString().slice(0, 13);
  if (body.op === 'begin') {
    const email = cleanEmail(body.email);
    if (!email) return answer(400, { error: 'That doesn’t look like an email address.' });
    if (email === m.email) return answer(400, { error: 'That’s your address already.' });
    if (Object.entries(all).some(([k, x]) => k !== id && x.email === email)) return answer(400, { error: 'Another member has that address. Ask John.' });
    if (!mailReady(env)) return answer(503, { error: 'Emails aren’t set up yet. Ask John.' });
    const old = await env.SOTD.get(key, 'json'), sent = (old && old.hour === hour ? old.n : 0) || 0;
    if (old && old.sent > Date.now() - 60e3) return answer(429, { error: 'A code went a moment ago. Wait a minute, then ask again.' });
    if (sent >= MAIL_PER_HOUR) return answer(429, { error: 'That’s a lot of codes this hour. Try again later.' });
    const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1e6).padStart(6, '0');
    await env.SOTD.put(key, JSON.stringify({ email, h: await sha256hex(id + ':' + email + ':' + code), until: Date.now() + MAIL_CODE_MINUTES * 60e3,
      tries: 0, sent: Date.now(), hour, n: sent + 1 }), { expirationTtl: MAIL_CODE_MINUTES * 60 + 3600 });
    try {
      await sendMail(env, email, 'Your Song of the Day code: ' + code,
        'Your code to add this address to your Song of the Day profile is ' + code + '.\n\nType it in your Profile within ' + MAIL_CODE_MINUTES
        + ' minutes. Codes to sign in will come here from then on.\n\nIf you didn’t ask for it, you can ignore this email.\n', 'confirm',
        codeMailHtml(code, 'Type it in your Profile within ' + MAIL_CODE_MINUTES + ' minutes to add this address to your Song of the Day profile. Codes to sign in will come here from then on.', 'If you didn’t ask for it, you can ignore this email.'));
    } catch (e) { return answer(502, { error: 'The email couldn’t be sent just now. Try again later, or ask John.' }); }
    return answer(200, { ok: true, minutes: MAIL_CODE_MINUTES });
  }
  if (body.op === 'confirm') {
    const rec = await env.SOTD.get(key, 'json'), code = String(body.code || '').replace(/\D/g, '');
    if (!rec || !rec.h || rec.until < Date.now() || rec.tries >= MAIL_CODE_TRIES) return answer(400, { error: 'That code has run out. Ask for a new one.' });
    if (code.length !== 6 || await sha256hex(id + ':' + rec.email + ':' + code) !== rec.h) {
      rec.tries++;
      await env.SOTD.put(key, JSON.stringify(rec), { expirationTtl: 3600 });
      return answer(403, { error: rec.tries >= MAIL_CODE_TRIES ? 'That isn’t the code, and that was the last try. Ask for a new one.' : 'That isn’t the code. Check the email and try again.' });
    }
    await env.SOTD.put(key, JSON.stringify({ hour: rec.hour, n: rec.n, sent: rec.sent }), { expirationTtl: 3600 });   // (used)
    if (Object.entries(all).some(([k, x]) => k !== id && x.email === rec.email)) return answer(400, { error: 'Another member has that address now. Ask John.' });
    if (m.email !== rec.email) { m.email = rec.email; m.emailAt = new Date().toISOString(); }
    await env.SOTD.put('members', JSON.stringify(all));
    return answer(200, { ok: true, email: m.email });
  }
  return answer(400, { error: 'Say what to do.' });
}

// a member's saved playlists, kept with their sign-in so every device of theirs has them (KV key mpl:<member id>).
// GET: { lists: [{ id, name, tracks, at }] }, the newest first. POST { op: 'save', id?, name, tracks } adds one (no id) or
// replaces it, giving back { list, lists }; POST { op: 'delete', id } removes one.
const PLAYLISTS_MAX = 500, PLAYLIST_TRACKS = 500, PLAYLIST_NAME = 80;
async function memberFromToken(request, env) {
  return memberIdFor((request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, ''), env);
}
async function memberIdFor(token, env) {
  if (!token) return null;
  const hash = await sha256hex(token);
  const all = (await env.SOTD.get('members', 'json')) || {};
  let id = Object.keys(all).find(k => all[k].devices && all[k].devices[hash]) || null;
  if (id && all[id].devices[hash].viewas && all[id].devices[hash].until < Date.now()) id = null;   // (See as them: over after its hour)
  else if (id && !all[id].devices[hash].viewas) await seenTouch(env, hash);
  return id;
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
    if (i >= 0) { const [l] = lists.splice(i, 1); await env.SOTD.put(key, JSON.stringify(lists)); await uplListSaved(env, id, l, true); await uplSavedTidy(env, l.tracks || []); }
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
  const was = list.tracks || [];
  Object.assign(list, { name, tracks, at: Date.now() });
  await env.SOTD.put(key, JSON.stringify(lists));
  await uplListSaved(env, id, list);
  await uplSavedTidy(env, was.filter(t => !tracks.includes(t)));   // (songs taken off it: files kept only for it, on playlists they've gone from, deleted)
  return answer(200, { list, lists: sorted() });
}

// ---------- what a member is playing, for carrying on on another of their devices (the calendar's /members/now) ----------
// One record per member, written by the device playing only when something changes: a new song, a pause or play, a jump
// within the song, the calendar put away. Its position is where the song was when it was written (at, the Worker's
// clock), so another device works out where it has got to. POST { dev, label, start, playing, t, d, pos, q, mode }:
// start (a play begun on that device by a tap) makes it the device playing; without it, if another device has taken
// over since, nothing is written and the answer says which ({ taken: label }), so that one stops. POST { dev, stop: true }
// clears it (Stop). GET gives the record and the Worker's time.
// Kept in a Durable Object (the class SotdNow below, bound as NOW, one per member): read straight after it's written,
// from anywhere, and it tells the member's open pages the moment it changes (/members/now/ws, a WebSocket each). Without
// that binding, in KV (now:<id>, a week), as at first: KV can be a minute or more behind, and there's no telling.
function nowApply(rec, b, now) {   // a device's note against the one kept: the answer, and what to keep (put) or clear (del)
  const dev = String(b.dev || '');
  if (!/^[A-Za-z0-9]{8,40}$/.test(dev)) return { status: 400, out: { error: 'Which device?' } };
  if (rec && rec.dev !== dev && !b.start) return { status: 200, out: { taken: rec.label || 'other device', now } };
  if (b.stop) return { status: 200, out: { ok: true, now }, del: !!rec };
  if (!ID_RE.test(String(b.t || ''))) return { status: 400, out: { error: 'Which song?' } };
  const ids = x => Array.isArray(x) ? x.map(String).filter(t => ID_RE.test(t)).slice(0, 2000) : undefined;
  const q = b.q && Array.isArray(b.q.list) ? { list: ids(b.q.list), i: Math.max(0, b.q.i | 0), name: String(b.q.name || '').slice(0, 120),
    back: String(b.q.back || '').slice(0, 20), order: ids(b.q.order), sh: !!b.q.sh } : null;
  const put = { dev, label: String(b.label || 'other device').slice(0, 40), t: b.t, d: String(b.d || '').slice(0, 10), pos: Math.max(0, +b.pos || 0),
    playing: !!b.playing, mode: b.mode === 'video' ? 'video' : 'audio', q, at: now, since: rec && rec.dev === dev && !b.start ? rec.since : now };
  return { status: 200, out: { ok: true, now }, put };
}
async function memberNow(request, env, answer) {
  const id = await memberFromToken(request, env);
  if (!id) return answer(401, { error: 'Not a member (any more).' });
  if (request.method !== 'GET' && request.method !== 'POST') return answer(405, { error: 'Use GET or POST.' });
  if (env.NOW) {
    const r = await env.NOW.get(env.NOW.idFromName(id)).fetch('https://now/', { method: request.method, body: request.method === 'POST' ? await request.text() : undefined });
    return answer(r.status, await r.json());
  }
  const key = 'now:' + id, now = Date.now();
  const rec = await env.SOTD.get(key, 'json');
  if (request.method === 'GET') return answer(200, { now, rec: rec || null });
  const x = nowApply(rec, await request.json().catch(() => ({})), now);
  if (x.put) await env.SOTD.put(key, JSON.stringify(x.put), { expirationTtl: 7 * 86400 });
  else if (x.del) await env.SOTD.delete(key);
  return answer(x.status, x.out);
}
// a member's iPhone widget ("On your other device", iOS 26), reachable by Apple's pushes: POST { push, dev } (its push
// token, for that device; ten widgets a member at most, the newest), GET { widgets: how many }. Whenever what the
// widget shows changes (another device's song, a pause or play, which device, stopped), each is told to look again
// (apnsWidget). Kept with the member's note, in the Durable Object.
async function memberNowWidget(request, env, answer) {
  const id = await memberFromToken(request, env);
  if (!id) return answer(401, { error: 'Not a member (any more).' });
  if (!env.NOW) return answer(501, { error: 'Not set up here.' });
  if (request.method !== 'GET' && request.method !== 'POST') return answer(405, { error: 'Use GET or POST.' });
  const r = await env.NOW.get(env.NOW.idFromName(id)).fetch('https://now/widget', { method: request.method, body: request.method === 'POST' ? await request.text() : undefined });
  return answer(r.status, await r.json());
}
async function memberNowSocket(request, env) {   // a page's live line: the member's token as the WebSocket's second protocol
  if (!env.NOW || request.headers.get('Upgrade') !== 'websocket') return new Response('Not here.', { status: 426 });
  const prot = (request.headers.get('Sec-WebSocket-Protocol') || '').split(',').map(x => x.trim());
  const id = prot[0] === 'sotd' && prot[1] ? await memberIdFor(prot[1], env) : null;
  if (!id) return new Response('Not a member (any more).', { status: 401 });
  return env.NOW.get(env.NOW.idFromName(id)).fetch(request);
}
export class SotdNow {   // one member's note, and their open pages
  constructor(ctx, env) {
    this.ctx = ctx; this.env = env;
    try { ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair('ping', 'pong')); } catch (e) {}   // (a page's "still there?", answered without waking this)
  }
  async fetch(request) {
    const now = Date.now(), path = new URL(request.url).pathname;
    const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json' } });
    if (path === '/forget') {   // the member removed: their note and widgets gone, their open pages' lines closed
      await this.ctx.storage.deleteAll();
      for (const ws of this.ctx.getWebSockets()) { try { ws.close(1000, 'Bye'); } catch (e) {} }
      return json({ ok: true });
    }
    if (path === '/widget' || path === '/widget/tell') return this.widget(request, now, json);
    if (path === '/once') {   // (the instance named _once) the first to ask for a key gets first: true, everyone after it false
      const key = 'once:' + String((await request.json().catch(() => ({}))).key || '').slice(0, 100);
      if (await this.ctx.storage.get(key)) return json({ first: false });
      await this.ctx.storage.put(key, now);   // (nothing else reaches this object between the look and the write)
      return json({ first: true });
    }
    let rec = (await this.ctx.storage.get('rec')) || null;
    if (rec && now - rec.at > 7 * DAY) { rec = null; await this.ctx.storage.delete('rec'); }
    if (request.headers.get('Upgrade') === 'websocket') {
      const pair = new WebSocketPair();
      this.ctx.acceptWebSocket(pair[1]);
      pair[1].send(JSON.stringify({ now, rec }));
      return new Response(null, { status: 101, webSocket: pair[0], headers: { 'Sec-WebSocket-Protocol': 'sotd' } });
    }
    if (request.method === 'GET') return json({ now, rec });
    const x = nowApply(rec, await request.json().catch(() => ({})), now);
    if (x.put) await this.ctx.storage.put('rec', x.put);
    else if (x.del) await this.ctx.storage.delete('rec');
    if (x.put || x.del) {   // every open page told (the device that wrote it knows itself)
      const msg = JSON.stringify({ now, rec: x.put || null });
      for (const ws of this.ctx.getWebSockets()) { try { ws.send(msg); } catch (e) {} }
      // the iPhone widgets told to look again, when what they show has changed (not for a jump within the song)
      const shown = r => r ? [r.dev, r.t, r.playing].join('|') : '';
      if (shown(rec) !== shown(x.put || null)) await this.widgetsWant();
    }
    return json(x.out, x.status);
  }
  async widget(request, now, json) {   // a widget's push token kept (POST), forgotten (DELETE: its device signed out), counted (GET)
    if (new URL(request.url).pathname === '/widget/tell') await this.widgetsTell();   // (John's check: each told now)
    const w = (await this.ctx.storage.get('widgets')) || {};
    if (request.method === 'GET' || new URL(request.url).pathname === '/widget/tell') {   // how many, and how Apple took the last push to each (no tokens)
      return json({ widgets: Object.keys(w).length, list: Object.keys(w).map(k => ({ at: w[k].at, sent: w[k].sent || 0, last: w[k].last || null })) });
    }
    const b = await request.json().catch(() => ({}));
    const dev = String(b.dev || '');
    if (request.method === 'DELETE') {
      if (w[dev]) { delete w[dev]; await this.ctx.storage.put('widgets', w); }
      return json({ ok: true });
    }
    const push = String(b.push || '').toLowerCase();
    if (!/^[0-9a-f]{32,200}$/.test(push)) return json({ error: 'Which widget?' }, 400);
    for (const k of Object.keys(w)) if (w[k].push === push) delete w[k];
    w[/^[A-Za-z0-9]{8,40}$/.test(dev) ? dev : 'w' + push.slice(0, 12)] = { push, at: now };
    const keep = Object.fromEntries(Object.entries(w).sort((a, c) => c[1].at - a[1].at).slice(0, 10));
    await this.ctx.storage.put('widgets', keep);
    return json({ ok: true, widgets: Object.keys(keep).length });
  }
  // each change the widgets show (another device, a new song, playing, paused or stopped) told at once (waited for, up
  // to 4 seconds: work left running after the answer isn't sure to finish). Apple rations them as it sees fit.
  async widgetsWant() {
    await Promise.race([this.widgetsTell().catch(() => {}), new Promise(r => setTimeout(r, 4000))]);
  }
  async alarm() {}   // (none set now; one left from before does nothing)
  async widgetsTell() {
    const w = (await this.ctx.storage.get('widgets')) || {}, keys = Object.keys(w);
    if (!keys.length) return;
    await Promise.all(keys.map(async k => {
      const r = await apnsWidget(this.env, w[k].push).catch(e => ({ st: 0, why: String(e).slice(0, 120) }));
      if (r.st === 410) delete w[k];   // (that widget's gone: the app deleted, or a new token since)
      else { w[k].sent = (w[k].sent || 0) + 1; w[k].last = { at: Date.now(), st: r.st, why: r.why || '' }; }   // (kept for John's check)
    }));
    await this.ctx.storage.put('widgets', w);
  }
  async webSocketMessage(ws, msg) { if (msg === 'ping') ws.send('pong'); }
  async webSocketClose(ws, code) { try { ws.close(code === 1005 ? 1000 : code, 'Bye'); } catch (e) {} }
  async webSocketError(ws) { try { ws.close(1011, 'Error'); } catch (e) {} }
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
// an MP3's length, from its first frames (its Xing or Info header's frame count, else its bitrate), in seconds; 0 unknown
async function mp3Len(env, key, size) {
  const o = await env.MEDIA.get(key, { range: { offset: 0, length: Math.min(size, 256 << 10) } }).catch(() => null); if (!o) return 0;
  const b = new Uint8Array(await o.arrayBuffer());
  let i = 0;
  if (b[0] === 0x49 && b[1] === 0x44 && b[2] === 0x33) i = 10 + ((b[6] & 127) << 21 | (b[7] & 127) << 14 | (b[8] & 127) << 7 | (b[9] & 127));   // (an ID3 tag first)
  for (; i + 4 < b.length; i++) if (b[i] === 0xff && (b[i + 1] & 0xe0) === 0xe0) break;
  if (i + 4 >= b.length) return 0;
  const ver = (b[i + 1] >> 3) & 3, lay = (b[i + 1] >> 1) & 3, bri = b[i + 2] >> 4, sri = (b[i + 2] >> 2) & 3, mono = (b[i + 3] >> 6) === 3;
  if (lay !== 1 || bri === 0 || bri === 15 || sri === 3) return 0;   // (Layer III only)
  const v1 = ver === 3, sr = [[11025, 12000, 8000], [0, 0, 0], [22050, 24000, 16000], [44100, 48000, 32000]][ver][sri];
  const kbps = (v1 ? [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320] : [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160])[bri];
  const spf = v1 ? 1152 : 576, x = i + 4 + (v1 ? (mono ? 17 : 32) : (mono ? 9 : 17));
  const tag = String.fromCharCode(b[x], b[x + 1], b[x + 2], b[x + 3]);
  if ((tag === 'Xing' || tag === 'Info') && (b[x + 7] & 1)) { const frames = (b[x + 8] << 24 | b[x + 9] << 16 | b[x + 10] << 8 | b[x + 11]) >>> 0; return sr ? frames * spf / sr : 0; }
  return kbps ? (size - i) * 8 / (kbps * 1000) : 0;
}
// a song's real length in seconds (Spotify's, kept in KV durs: songDurs), 0 unknown
async function realLen(env, t) {
  const kept = (await env.SOTD.get('durs', 'json')) || {};
  if (!kept[t]) await songDurs(new URL('https://x/durs?ids=' + t), env).catch(() => null);
  return (((await env.SOTD.get('durs', 'json')) || {})[t] || 0) / 1000;
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
    const hh = await env.MEDIA.head('h/' + t + '.mp4');   // its TV copy, if it has one
    if (!(keep[t] && keep[t].size === size && !keep[t].err)) {
      const gone = hh && !(await env.MEDIA.head('v/' + t + '.mp4'));   // the original deleted (media_originals.py): the copy it was
      try { keep[t] = gone ? Object.assign(await probeMp4(env, 'h/' + t + '.mp4', hh.size), { size, copyOnly: true }) : await probeMp4(env, 'v/' + t + '.mp4', size); }
      catch (e) { keep[t] = { size, err: String(e && e.message || e).slice(0, 120) }; }
      changed = true;
    }
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
  const plain = (status, text) => new Response(text, { status, headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' } });
  const u = /^\/m\/u([av])\/([\w-]{10,20})\/([0-9A-Za-z]{22})\.(mp3|mp4)$/.exec(url.pathname);   // a member's own (ua/<pid>/<track>.mp3, uv/…mp4)
  if (u && MEDIA_KINDS[u[1]].ext === u[4]) return uplFile(request, url, env, u, plain);
  const m = /^\/m\/([av])\/([0-9A-Za-z]{22})\.(mp3|mp4)$/.exec(url.pathname);
  if (!m || MEDIA_KINDS[m[1]].ext !== m[3]) return plain(404, 'Not here.');
  if (!env.MEDIA || !env.ADMIN_KEY) return plain(503, 'Not set up.');
  if (request.method !== 'GET' && request.method !== 'HEAD') return plain(405, 'Use GET.');
  const exp = parseInt(url.searchParams.get('e'), 10), sig = url.searchParams.get('s') || '';
  if (!(exp > Date.now() / 1000) || sig !== await mediaSig(env, m[1], m[2], exp)) return plain(403, 'This link has run out or isn’t valid.');
  // a video's TV-friendly copy (h/<track>.mp4, made by video-tv.yml) wherever there is one: it plays everywhere; a song's
  // lighter copy (l/<track>.m4a, AAC, made by audio-light.yml) the same way: it streams and keeps offline in less
  const copy = m[1] === 'v' ? 'h/' + m[2] + '.mp4' : 'l/' + m[2] + '.m4a';
  let key = m[1] + '/' + m[2] + '.' + m[3], head = await env.MEDIA.head(copy), type = MEDIA_KINDS[m[1]].type;
  if (head) { key = copy; if (m[1] === 'a') type = 'audio/mp4'; } else head = await env.MEDIA.head(key);
  if (!head) return plain(404, 'Not here.');
  const size = head.size;
  // a video can change under the same address (its TV copy arriving): browsers check before using what they kept
  // (no-cache), and a piece asked for against the old file (If-Range) gets the whole new one, not a piece of it
  const hdrs = { 'Content-Type': type, 'Accept-Ranges': 'bytes', 'Cache-Control': 'private, no-cache', 'ETag': head.httpEtag,   // (a song's copy arrives under the same address too)
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
// The day's song goes out once, as it comes out on the live calendar (see pushDaily: the site job asks then, and the cron
// looks every 10 minutes in case that's lost). The very first look only notes the song. John's own messages come from
// the members page (op 'push').
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
function subHost(x) { return x.apns ? 'Apple (iPhone app)' : pushHost(x.sub.endpoint); }
function pushHost(endpoint) {   // whose push service: for John, in plain words
  const h = new URL(endpoint).host;
  return /apple\.com$/.test(h) ? 'Apple' : /googleapis\.com$/.test(h) ? 'Google' : /mozilla|services\.mozilla\.com$/.test(h) ? 'Mozilla' : /windows|notify\.windows\.com$/.test(h) ? 'Microsoft' : h;
}
// ---- the iPhone app (TestFlight): Apple's own notifications (APNs), not Web Push. The app hands over its device token
// (/members/push { apns }); each message goes to api.push.apple.com, signed with John's APNs key (the Worker secrets
// APNS_KEY_ID, APNS_KEY: the .p8's text, and APPLE_TEAM_ID, put there by worker_deploy.py from the repository secrets).
// The number on the app's icon comes with each one (and alone, when messages are read). ----
const APP_ID = 'io.github.cooperking.sotd';
let apnsJwt = null;
async function apnsAuth(env) {
  if (apnsJwt && Date.now() - apnsJwt.at < 45 * 60e3) return apnsJwt.t;
  const pem = String(env.APNS_KEY || '').replace(/-----[^-]+-----/g, '').replace(/\s+/g, '');
  const key = await crypto.subtle.importKey('pkcs8', Uint8Array.from(atob(pem), c => c.charCodeAt(0)), { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
  const head = b64url(u8(JSON.stringify({ alg: 'ES256', kid: env.APNS_KEY_ID }))), claims = b64url(u8(JSON.stringify({ iss: env.APPLE_TEAM_ID, iat: Math.floor(Date.now() / 1000) })));
  const t = head + '.' + claims + '.' + b64url(await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, key, u8(head + '.' + claims)));
  apnsJwt = { t, at: Date.now() };
  return t;
}
async function apnsOne(env, token, msg) {   // { st, why }, as pushOne
  if (!env.APNS_KEY || !env.APNS_KEY_ID || !env.APPLE_TEAM_ID) return { st: 0, why: 'The APNs key isn’t on the Worker yet' };
  const aps = msg.title ? { alert: { title: msg.title, body: msg.body || '' }, sound: 'default', 'thread-id': msg.tag && msg.tag.startsWith('sotd-day') ? 'songs' : 'notes' } : {};
  if (typeof msg.unread === 'number') aps.badge = msg.unread;
  // the picture (the day's card, else the cover): the app's notification add-on (in builds after 16) fetches it and shows it
  // with the notification; earlier builds show the words only
  const pic = msg.title ? (msg.image || msg.icon || '') : '';
  if (pic) aps['mutable-content'] = 1;
  const r = await fetch('https://api.push.apple.com/3/device/' + token, { method: 'POST', body: JSON.stringify(Object.assign({ aps }, msg.title ? { url: msg.url || SITE_SONGS, ib: msg.ib || '' } : {}, pic ? { pic } : {})), headers: {
    authorization: 'bearer ' + await apnsAuth(env), 'apns-topic': APP_ID, 'apns-push-type': 'alert', 'apns-priority': msg.title ? '10' : '5', 'apns-expiration': String(Math.floor(Date.now() / 1000) + PUSH_TTL), 'content-type': 'application/json' } });
  const why = r.ok ? '' : ((await r.text().catch(() => '')) || '').slice(0, 160);
  return { st: r.status === 200 ? 201 : (r.status === 400 && /BadDeviceToken|DeviceTokenNotForTopic/.test(why)) ? 410 : r.status, why };
}
async function apnsBadge(env, member, unread) {   // read on one device: the number on the app's icon, on all their iPhones
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {};
  await Promise.all(Object.values(subs).filter(x => x.m === member && x.apns).map(x => apnsOne(env, x.apns, { unread }).catch(() => {})));
}
// an iPhone widget told to look again (iOS 26, WidgetKit's pushes): its own token and topic, nothing shown. Apple
// delivers them as it sees fit (they're rationed). APNS_HOST only for testing (a stand-in for Apple's service).
async function apnsWidget(env, token) {   // { st, why }, as apnsOne
  if (!env.APNS_KEY || !env.APNS_KEY_ID || !env.APPLE_TEAM_ID) return { st: 0, why: 'The APNs key isn’t on the Worker yet' };
  const r = await fetch((env.APNS_HOST || 'https://api.push.apple.com') + '/3/device/' + token, { method: 'POST', body: JSON.stringify({ aps: { 'content-changed': true } }), headers: {
    authorization: 'bearer ' + await apnsAuth(env), 'apns-topic': APP_ID + '.push-type.widgets', 'apns-push-type': 'widgets',
    'apns-expiration': String(Math.floor(Date.now() / 1000) + 3600), 'content-type': 'application/json' } });
  const why = r.ok ? '' : ((await r.text().catch(() => '')) || '').slice(0, 160);
  return { st: r.status === 200 ? 201 : (r.status === 410 || (r.status === 400 && /BadDeviceToken|DeviceTokenNotForTopic/.test(why))) ? 410 : r.status, why };
}
// A newer build of an app (John, 1 Nov 2026: "Is there some way to notify users (who have each app) that there's an
// update to it"): once an hour, 09:00 to 21:00 UK time, each device with notifications on whose app is older than the
// newest build is told once (upd: the build it was told of). The website's Android app: songs/android.json; the iPhone
// app: songs/ios.json's build (the newest Apple has approved for TestFlight's testers). Google Play's app updates itself,
// the Fire TV app offers its own, and the calendar shows the same offer in the app when it opens.
async function appUpdates(env) {
  const h = londonHour(), hr = new Date().toISOString().slice(0, 13);
  if (h < 9 || h >= 21 || (await env.SOTD.get('appUpdHour')) === hr) return;
  await kvSoft(env, 'appUpdHour', hr);
  const [a, i] = await Promise.all([rawJson('songs/android.json', {}).catch(() => ({})), rawJson('songs/ios.json', {}).catch(() => ({}))]);
  const latest = { apk: Number(a.build) || 0, ios: Number(i.build) || 0 };
  if (!latest.apk && !latest.ios) return;
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {}, todo = [];
  for (const [id, s] of Object.entries(subs)) {
    const m = APP_RE.exec(s.app || ''), kind = m && m[1], b = m && m[2] ? Number(m[2]) : 0, want = latest[kind] || 0;
    if (b && want > b && (s.upd || 0) < want) todo.push([id, s, kind, want]);
  }
  if (!todo.length) return;
  const k = await vapidKeys(env);
  for (const [id, s, kind, want] of todo.slice(0, 50)) {
    const msg = { title: 'A new version of the app', body: kind === 'ios' ? 'Song of the Day ' + want + ' is ready in TestFlight. Tap to update.' : 'A new Song of the Day app for Android is ready. Tap to get it.',
      url: SITE_SONGS + '?do=update', tag: 'sotd-appupdate' };
    await pushTo(env, k, s, msg).catch(() => {});
    subs[id].upd = want;
  }
  const now = (await env.SOTD.get('pushSubs', 'json')) || {};   // (as it is now: only the told ones marked)
  for (const [id, , , want] of todo.slice(0, 50)) if (now[id]) now[id].upd = want;
  await kvSoft(env, 'pushSubs', JSON.stringify(now));
}
async function pushTo(env, k, sub, msg) { return sub.apns ? apnsOne(env, sub.apns, msg) : pushOne(k, sub.sub, msg); }

// A notification's words, link and pictures, John's and members' alike: a title (80 at most), words (300), a link to a
// page on the site (else the calendar), and a song sent as it's shared: its card as the big picture (Android and
// computers; an iPhone shows neither) and its cover beside it, from the site, Spotify or this Worker only.
function noteMsg(body) {
  const title = String(body.title || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const text = String(body.text || '').replace(/[ \t]+/g, ' ').trim().slice(0, 300);
  const link = String(body.url || '').trim();
  if (!title) return { error: 'Give it a title.' };
  if (link && (!/^https:\/\/cooperking\.github\.io\//.test(link) || link.length > 600)) return { error: 'The link has to be a page on the site (https://cooperking.github.io/…).' };
  const pic = u => { const x = String(u || ''); return /^https:\/\/(cooperking\.github\.io|i\.scdn\.co|sotd-playlists\.cooperking\.workers\.dev)\//.test(x) && x.length < 600 ? x : ''; };
  const msg = { title, body: text, url: link || SITE_SONGS, tag: 'sotd-note-' + Date.now() };
  if (pic(body.image)) msg.image = pic(body.image);
  if (pic(body.icon)) msg.icon = pic(body.icon);
  if (/^https:\/\/sotd-playlists\.cooperking\.workers\.dev\/n\/[\w-]{16}\.(gif|webp)$/.test(String(body.gif || ''))) msg.gif = body.gif;   // (the inbox's, moving: one sent up here)
  return { msg };
}
// ---------- members' own notifications (John, 28 Oct 2026: "I'd like users to have the ability to send notifications to
// other users of the app, just as I can"). Everything but the daily song says who it's from, John's own too. From the calendar (Profile › Inbox › Send, or a song's Share › Send to
// members): POST /members/send { title, text, url, image, icon, to: [member ids] | 'all' } goes into the chosen members'
// inboxes, or every other member's, and to their phones where notifications are on, as John's do. Its notification
// says who sent it ("<name>: <title>"); the inbox shows "From <name>". SEND_DAY a day each. The admin page's Status tab
// lists each, with who sent it to whom. POST /members/sendpic { pic } keeps a picture for one (made small on the
// calendar) in R2 at n/<id>.jpg, as John's. /members/me gives the other members' names to choose from (others). ----------
const SEND_DAY = 20, SENDPIC_DAY = 20;
// Who sees whom, and who takes whose notifications (John, 29 Oct 2026). John can keep two members apart on the Members
// tab ("so I can stop some people from even seeing that someone is a member"): apart on both records, so neither is in
// the other's lists and neither can send to the other. A member can turn other members' notifications off (nomsg), or
// stop them from particular members (mute, their own list). John's (the member marked This is me) always reach
// everyone and he sees everyone.
// joined: signed in at least once (their invitation taken up): joined on their record from 29 Oct 2026, the time of their
// first sign-in; before that, a device signed in, a passkey, or their invitation used
function hasJoined(m) { return !!(m && (m.joined || Object.keys(m.devices || {}).length || (m.passkeys || []).length || (m.invite && m.invite.uses > 0))); }
function apart(all, a, b) { return ((all[a] && all[a].apart) || []).includes(b) || ((all[b] && all[b].apart) || []).includes(a); }
function takesFrom(all, to, from, ownerId) {   // whether member `to` takes a notification from member `from`
  if (!all[to] || to === from) return false;
  if (from && from === ownerId) return true;
  return !apart(all, to, from) && !all[to].nomsg && !(all[to].mute || []).includes(from);
}
async function memberWho(request, env) {   // { id, all } for a signed-in member, else null
  const token = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  if (!token) return null;
  const hash = await sha256hex(token), all = (await env.SOTD.get('members', 'json')) || {};
  const id = Object.keys(all).find(k => all[k].devices && all[k].devices[hash]);
  if (!id) return null;
  const dv = all[id].devices[hash];
  if (dv.viewas) {   // John's See as them: an hour; not counted as them being here
    if (dv.until < Date.now()) { delete all[id].devices[hash]; await env.SOTD.put('members', JSON.stringify(all)); return null; }
    return { id, all, viewAs: true };
  }
  await seenTouch(env, hash);
  return { id, all };
}
async function dayCount(env, key, most) {   // how many today (UTC), counted; false once there have been most
  const k = key + ':' + new Date().toISOString().slice(0, 10), n = Number(await env.SOTD.get(k)) || 0;
  if (n >= most) return false;
  await kvSoft(env, k, String(n + 1), { expirationTtl: 2 * 86400 });
  return n + 1;
}
async function memberSend(request, env, answer) {
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const who = await memberWho(request, env);
  if (!who) return answer(401, { error: 'Not a member (any more).' });
  const { id, all } = who, body = await request.json().catch(() => ({}));
  const note = noteMsg(body);
  if (note.error) return answer(400, { error: note.error });
  // only to members who take theirs: not those kept apart from them, nor those who've turned off other members'
  // notifications or theirs (Profile › Account); John's always go
  const ownerId = await env.SOTD.get('owner'), takes = k => takesFrom(all, k, id, ownerId);
  const everyone = body.to === 'all';
  const asked = everyone ? Object.keys(all).filter(k => hasJoined(all[k])) : Array.isArray(body.to) ? [...new Set(body.to.map(String))] : [];
  const to = asked.filter(takes);
  // those who've turned off members' notifications: into their inbox all the same, and by email if they take emails, but nothing on their phones (John, 7 Nov 2026: "it should be inbox only no notifications"); not those who muted this member, nor those kept apart
  const quiet = asked.filter(k => k !== id && all[k] && !takes(k) && !apart(all, k, id) && !(all[k].mute || []).includes(id));
  to.push(...quiet);
  if (body.me && all[id] && !to.includes(id)) to.push(id);   // themselves too (John, 3 Nov 2026: "Include myself in Send"): the same notification, inbox, phone and email
  if (!to.length) return answer(400, { error: asked.some(k => k !== id && all[k] && !apart(all, id, k)) ? 'They’ve turned off messages from other members.' : 'Choose who it goes to.' });
  const n = await dayCount(env, 'sent:' + id, SEND_DAY);
  if (!n) return answer(429, { error: 'That’s ' + SEND_DAY + ' sent today, the most for a day. You can send more tomorrow.' });
  const name = all[id].name, msg = note.msg;
  Object.assign(msg, { subject: msg.title, title: name + ': ' + msg.title, from: name, tag: 'sotd-from-' + id + '-' + Date.now() });
  const r = await pushAll(env, msg, to, { kind: 'member', quiet, via: { t: 'member', id, name }, toNames: everyone ? null : to.map(k => all[k].name),
    fromId: id, toIds: everyone ? 'all' : asked.filter(k => k !== id && all[k]) });
  // their Profile › Sent: who they chose, as the calendar said when it went ("Sent to …"), not who took it
  await sentAdd(env, id, msg, r, everyone ? 'all' : asked.filter(k => k !== id && all[k] && !apart(all, id, k)).map(k => all[k].name));
  return answer(200, { ok: true, inboxes: r.inboxes, sent: r.sent, devices: r.devices, left: SEND_DAY - n });
}
// POST /members/prefs { msgs: true | false, mute: [member ids] }: whether other members can send them notifications,
// and the members whose they don't take (their Profile › Account, John 29 Oct 2026: "A user should be able to say if
// they want to receive messages from other members", "control which members they can receive messages from"): nomsg
// and mute on their record. John's still come, from the Members tab or from him on the calendar.
async function memberPrefs(request, env, answer) {
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const who = await memberWho(request, env);
  if (!who) return answer(401, { error: 'Not a member (any more).' });
  const body = await request.json().catch(() => ({}));
  const hasMsgs = typeof body.msgs === 'boolean', hasMute = Array.isArray(body.mute), hasMail = typeof body.mailme === 'string';
  if (typeof body.pushme === 'string') {   // notifications on their devices: MAIL_MODES, as emails
    const want = body.pushme || 'off';
    if (!MAIL_MODES.includes(want)) return answer(400, { error: 'Choose off, everything, once a day, once a week, the Song of the Day or messages to you.' });
    if (want === 'all') delete who.all[who.id].pushme; else who.all[who.id].pushme = want;
    if (want !== 'day') await env.SOTD.delete('pdig:' + who.id); if (want !== 'week') await env.SOTD.delete('pwk:' + who.id);
    if (!hasMsgs && !hasMute && !hasMail) { await env.SOTD.put('members', JSON.stringify(who.all)); return answer(200, { ok: true, msgs: !who.all[who.id].nomsg, mute: who.all[who.id].mute || [], mailme: mailMode(who.all[who.id]), pushme: pushMode(who.all[who.id]) }); }
  }
  if (hasMail) {   // notifications by email: MAIL_MODES (noteMail)
    const want = body.mailme || 'off';
    if (!MAIL_MODES.includes(want)) return answer(400, { error: 'Choose off, everything, once a day, once a week, the Song of the Day or messages to you.' });
    if (want !== 'off' && !who.all[who.id].email) return answer(400, { error: 'Add your email address first.' });
    who.all[who.id].mailme = want; if (want !== 'day') await env.SOTD.delete('mdig:' + who.id); if (want !== 'week') await env.SOTD.delete('mwk:' + who.id);
    if (!hasMsgs && !hasMute) { await env.SOTD.put('members', JSON.stringify(who.all)); return answer(200, { ok: true, msgs: !who.all[who.id].nomsg, mute: who.all[who.id].mute || [], mailme: mailMode(who.all[who.id]), pushme: pushMode(who.all[who.id]) }); }
  }
  if (!hasMsgs && !hasMute) return answer(400, { error: 'Say whether to take them (msgs: true or false), or whose not to (mute).' });
  const all = who.all, m = all[who.id], ownerId = await env.SOTD.get('owner');
  if (hasMsgs) { if (body.msgs) delete m.nomsg; else m.nomsg = true; }
  if (hasMute) {
    const mute = [...new Set(body.mute.map(String))].filter(k => k !== who.id && k !== ownerId && all[k] && !apart(all, who.id, k));
    if (mute.length) m.mute = mute; else delete m.mute;
  }
  await env.SOTD.put('members', JSON.stringify(all));
  return answer(200, { ok: true, msgs: !m.nomsg, mute: m.mute || [], mailme: mailMode(m), pushme: pushMode(m) });
}
async function memberSendPic(request, env, answer) {
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const who = await memberWho(request, env);
  if (!who) return answer(401, { error: 'Not a member (any more).' });
  if (!env.MEDIA) return answer(503, { error: 'Pictures can’t be sent yet.' });
  const body = await request.json().catch(() => ({}));
  const bad = picsBad(body);
  if (bad) return answer(400, { error: bad });
  if (!await dayCount(env, 'sentpic:' + who.id, SENDPIC_DAY)) return answer(429, { error: 'That’s ' + SENDPIC_DAY + ' pictures today, the most for a day.' });
  const up = await picsUp(env, new URL(request.url).origin, body);
  return answer(up.error ? 400 : 200, up);
}

// A notification's picture, sent up from the page (John's from the Members tab, op pushpic; a member's, /members/sendpic):
// { pic } a JPEG made small there, kept in R2 at n/<id>.jpg, the notification's picture; and with it, for a GIF chosen,
// { gif } the GIF as it is (5 MB at most), at n/<id>.gif, the picture in the inbox, where it moves (John, 29 Oct 2026:
// "Can gifs be sent through the messages?" "yes, in the inbox please"). A notification shows a still picture at most.
const GIF_MOST = 5 * 1024 * 1024;
function picsBad(body) {   // what's wrong with them, or ''
  const b64 = String(body.pic || '').replace(/^data:image\/jpeg;base64,/, '');
  if (!b64 || b64.length > 2.5e6 || !/^[A-Za-z0-9+/=]+$/.test(b64)) return 'The picture is missing or too big.';
  if (body.gif == null) return '';
  const webp = /^data:image\/webp;base64,/.test(String(body.gif)), g64 = String(body.gif).replace(/^data:image\/(gif|webp);base64,/, '');   // (an animated WebP too: what many sites save "GIFs" as, John, 3 Nov 2026)
  if (!g64 || !/^[A-Za-z0-9+/=]+$/.test(g64)) return 'That GIF couldn’t be read.';
  if (g64.length > Math.ceil(GIF_MOST / 3) * 4) return 'That GIF is too big (5 MB at most).';
  if (webp ? !/^UklGR/.test(g64) || atob(g64.slice(0, 16)).slice(8, 12) !== 'WEBP' : !/^R0lGODlh|^R0lGODdh/.test(g64)) return webp ? 'That isn’t a WebP picture.' : 'That isn’t a GIF.';   // (GIF89a / GIF87a; RIFF…WEBP)
  return '';
}
async function picsUp(env, origin, body) {   // { url, gif? }, or { error }
  const bad = picsBad(body);
  if (bad) return { error: bad };
  const bytes = b => { const s = atob(b), a = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) a[i] = s.charCodeAt(i); return a; };
  const pid = randomCode(12), out = { url: origin + '/n/' + pid + '.jpg' };
  await env.MEDIA.put('n/' + pid + '.jpg', bytes(String(body.pic).replace(/^data:image\/jpeg;base64,/, '')), { httpMetadata: { contentType: 'image/jpeg' } });
  if (body.gif != null) {
    const ext = /^data:image\/webp;/.test(String(body.gif)) ? 'webp' : 'gif';
    await env.MEDIA.put('n/' + pid + '.' + ext, bytes(String(body.gif).replace(/^data:image\/(gif|webp);base64,/, '')), { httpMetadata: { contentType: 'image/' + ext } });
    out.gif = origin + '/n/' + pid + '.' + ext;
  }
  return out;
}

// notifications by email too (John, 3 Nov 2026: "Can an email be sent to users when notifications are received too"):
// each member chooses in their Profile (mailme on their record; none chosen: each one): 'off' none, 'each' an email for each one, 'day' one email a
// day with them all (KV mdig:<member>, sent from 8am UK by the timer). John's mailer has a daily allowance (and sign-in
// codes need it), so each one goes as its own email up to MAIL_EACH_DAY a member and MAIL_ALL_DAY in all; the rest wait
// for that day's email.
const MAIL_EACH_DAY = 8, MAIL_ALL_DAY = 60, MAIL_DIG_MAX = 40;
// each member's choice (mailme; none chosen: all): 'off' none; 'all' each one as it comes; 'day' one email a day with them
// all; 'song' just the Song of the Day; 'direct' just what's to them (messages sent to them, reactions, hearts) (John, 3 Nov
// 2026: "more granular ... one email a day for everything. Just receive song of the day, just ... a direct message or reaction")
const MAIL_MODES = ['off', 'all', 'day', 'week', 'song', 'direct'];   // (week: one a week, Monday morning, John, 6 Nov 2026: "Could a weekly summary option be available too")
function mailMode(m) { if (!m || !m.email) return ''; const v = m.mailme === undefined || m.mailme === 'each' ? 'all' : m.mailme; return v === 'off' || !MAIL_MODES.includes(v) ? '' : v; }
function mailWants(mode, cat) { return mode === 'all' || mode === 'day' || mode === 'week' || mode === cat; }   // cat: 'song', 'direct' or 'other'
// notifications on their devices chosen apart from emails (John, 6 Nov 2026: "Some people want to receive emails but not
// notifications, and the other way around, and have all of the available options for both"): pushme on their record, the
// same choices as emails (none chosen: everything); 'day' keeps them (KV pdig:<member>) for one notification each morning
function pushMode(m) { const v = m && m.pushme; return MAIL_MODES.includes(v) ? v : 'all'; }
async function pushGate(env, all, id, cat, msg) {   // true: to their devices now; 'day': kept for the morning's one
  const mode = pushMode(all && all[id]);
  if (!mailWants(mode, cat)) return false;
  if (mode !== 'day' && mode !== 'week') return true;
  const k = (mode === 'week' ? 'pwk:' : 'pdig:') + id, had = (await env.SOTD.get(k, 'json')) || [];
  had.push({ t: String(msg.title || msg.subject || '').slice(0, 120), at: Date.now(), c: cat, i: mailImg(msg.icon) || mailImg(msg.image) || '' });
  await kvSoft(env, k, JSON.stringify(had.slice(mode === 'week' ? -100 : -40)), { expirationTtl: (mode === 'week' ? 10 : 3) * 86400 });
  return false;
}
async function pushDigests(env) {   // the morning's one notification for each member who chose once a day: from 8am UK
  if (Number(uplHM().slice(0, 2)) < 8) return;
  const done = 'pdigday:' + londonDay();
  if (await env.SOTD.get(done)) return;
  await env.SOTD.put(done, '1', { expirationTtl: 2 * 86400 });
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {}, k = await vapidKeys(env);
  let cursor;
  do {
    const l = await env.SOTD.list({ prefix: 'pdig:', cursor });
    for (const key of l.keys) {
      const id = key.name.slice(5), items = (await env.SOTD.get(key.name, 'json')) || [];
      await env.SOTD.delete(key.name);
      if (!items.length) continue;
      const unread = ((await env.SOTD.get('inbox:' + id, 'json')) || []).filter(y => !y.r).length;
      const dt = digestTitle(items, false), msg = { title: items.length === 1 ? items[0].t : dt.replace(/:.*$/, ''), body: items.length === 1 ? 'Yesterday on Song of the Day' : (dt.replace(/^[^:]*:\s*/, '').replace(/ · your day: /, '\nYour day: ') + '\n' + items.slice(-3).reverse().map(x => x.t).join(' · ')).slice(0, 200), url: SITE_SONGS, tag: 'sotd-day', unread: unread || items.length };
      await Promise.all(Object.keys(subs).filter(sid => subs[sid].m === id).map(sid => pushTo(env, k, subs[sid], msg).catch(() => {})));
    }
    cursor = l.list_complete ? null : l.cursor;
  } while (cursor);
}
function mailEsc(t) { return String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function mailImg(u) { return /^https:\/\/[^\s"'<>]+$/.test(String(u || '')) && !/\.(gif|webp)(\?|$)/i.test(u) ? String(u) : ''; }   // (an https picture: the share card, a message's picture or a cover)
// the month's colour, as the calendar's ground (its PALETTES' first colour), for the email's header and links (John, 3 Nov 2026)
const MAIL_MONTH = ['#0c1445', '#652bee', '#11970c', '#b613ec', '#096a42', '#ec1a13', '#1346ec', '#d85a06', '#0b86d3', '#ec135b', '#09696d', '#ec13c1'];
function mailMonthOf(u) {   // the month a link or picture is of: a day (#3-11-2026, &d=03-11-2026, songs/03-11-2026/, …/03-11-2026/card.jpg) or a month (songs/11-2026/)
  const t = String(u || ''), d = t.match(/(?:#|&d=|\/)(\d{1,2})-(\d{1,2})-\d{4}(?![\d-])/);
  if (d) return Number(d[2]);
  const m = t.match(/(?:#|\/)(\d{1,2})-\d{4}(?![\d-])/);
  return m ? Number(m[1]) : 0;
}
function mailTint(link, img) {   // the song's month (from its link, else its picture), else this month (John, 3 Nov 2026: "the colour of the month of the song", shared ones too)
  const n = mailMonthOf(link) || mailMonthOf(img);
  return MAIL_MONTH[(n >= 1 && n <= 12 ? n : Number(londonDay().slice(5, 7))) - 1] || '#ec135b';
}
function mailSettings() { return SITE_SONGS + '#emails'; }
function mailFoot() { return '\n\n—\nYou can update your email notification settings at ' + mailSettings(); }
function mailFootHtml(tint) { return '<p style="margin:28px 0 0;padding-top:16px;border-top:1px solid #eadfe3;font:13px/1.5 Helvetica,Arial,sans-serif;color:#7a6a72">You can update your email notification settings at <a href="' + mailEsc(mailSettings()) + '" style="color:' + (tint || mailTint()) + '">' + mailEsc(mailSettings().replace(/^https:\/\//, '')) + '</a></p>'; }
function mailShell(inner, link, img) {   // link: the song's, for its month's colour   // the email's frame: the site's pink bar, a white card, the footer
  return '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"></head><body style="margin:0;padding:0;background:#f6f0f2">'
    + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f0f2"><tr><td align="center" style="padding:24px 12px">'
    + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:18px;overflow:hidden">'
    + '<tr><td style="background:' + mailTint(link, img) + ';padding:14px 24px;font:600 12px/1 Helvetica,Arial,sans-serif;letter-spacing:.16em;text-transform:uppercase;color:#ffffff">John’s Song of the Day</td></tr>'
    + '<tr><td style="padding:24px">' + inner + mailFootHtml(mailTint(link, img)) + '</td></tr></table></td></tr></table></body></html>';
}
function mailItemHtml(x, big) {   // one notification: its picture (as shared), title, words and its link
  const img = mailImg(x.i), link = x.u || SITE_SONGS;
  return (img ? '<a href="' + mailEsc(link) + '"><img src="' + mailEsc(img) + '" alt="" width="' + (big ? 512 : 120) + '" style="display:block;width:' + (big ? '100%' : '120px') + ';max-width:100%;height:auto;border:0;border-radius:' + (big ? 14 : 10) + 'px;margin:0 0 16px"></a>' : '')
    + '<h1 style="margin:0 0 6px;font:500 ' + (big ? 26 : 19) + 'px/1.25 Georgia,\'Times New Roman\',serif;color:#1d0b1e">' + mailEsc(x.t) + '</h1>'
    + (x.b ? '<p style="margin:0 0 18px;font:15px/1.5 Helvetica,Arial,sans-serif;color:#4a3a42">' + mailEsc(x.b) + '</p>' : '')
    + (x.f ? mailFactHtml(x.f, mailTint(x.u, x.i), big) : '')
    + '<a href="' + mailEsc(link) + '" style="display:inline-block;padding:12px 22px;border-radius:999px;background:#1d0b1e;color:#ffffff;font:600 13px/1 Helvetica,Arial,sans-serif;letter-spacing:.08em;text-transform:uppercase;text-decoration:none">' + mailEsc(x.cta || 'Open it') + '</a>';
}
// the fact chosen at sign-off, in the email (John, 4 Nov 2026: "can the fact be put nicely in the emails"): a tinted card
// with a small heading, the fact in the serif, set off from the button
function mailFactHtml(f, tint, big) {
  return '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px"><tr>'
    + '<td width="4" style="width:4px;background:' + tint + ';border-radius:4px 0 0 4px"></td>'
    + '<td style="padding:' + (big ? '16px 18px' : '12px 14px') + ';background:#f8f3f5;border-radius:0 12px 12px 0">'
    + '<p style="margin:0 0 6px;font:700 11px/1 Helvetica,Arial,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:' + tint + '">Fact of the Day</p>'
    + '<p style="margin:0;font:italic ' + (big ? 17 : 15) + 'px/1.5 Georgia,\'Times New Roman\',serif;color:#2a1a24">' + mailEsc(f) + '</p></td></tr></table>';
}
function mailItem(msg) { return Object.assign({ t: msg.subject || msg.title || '', b: msg.body || '', f: msg.fact ? String(msg.fact).slice(0, 600) : undefined, u: msg.link || msg.url || SITE_SONGS, i: mailImg(msg.image) || mailImg(msg.icon), cta: msg.cta || '', at: Date.now() }, msg.html ? { h: String(msg.html).slice(0, 12000), hs: String(msg.htmlSmall || '').slice(0, 6000) } : {}); }
// a heart, by email (John, 3 Nov 2026: "include the album cover. Maybe even something a little more fun. I only want to send
// a nice looking email ever"): a heart badge, who, the cover big (or four of them, for a batch), the song, a line, the button
const HEART_LINES = ['Good taste travels fast.', 'That one landed.', 'Somebody’s humming it right now.', 'Turn it up.', 'One more reason to press play.', 'It’s officially a favourite.', 'Music to someone’s ears.', 'Consider it adored.'];
function heartMailHtml(o, small) {   // o: { who, what, songs: [{ title, artist, img }], pl, link, tint, seed }
  const t = o.tint, imgs = o.songs.map(x => mailImg(x.img)).filter(Boolean), one = o.songs.length === 1, line = HEART_LINES[Math.abs(o.seed || 0) % HEART_LINES.length];
  const cover = !imgs.length ? '' : imgs.length < 4 || one
    ? '<a href="' + mailEsc(o.link) + '"><img src="' + mailEsc(imgs[0]) + '" alt="" width="' + (small ? 140 : 360) + '" style="display:block;margin:0 auto;width:' + (small ? '140px' : '360px') + ';max-width:100%;height:auto;border:0;border-radius:' + (small ? 12 : 18) + 'px;box-shadow:0 10px 30px rgba(0,0,0,.18)"></a>'
    : '<table role="presentation" cellpadding="0" cellspacing="0" align="center" style="margin:0 auto;border-collapse:separate;border-spacing:6px"><tr>' + imgs.slice(0, 4).map((u, i) => (i === 2 ? '</tr><tr>' : '') + '<td><a href="' + mailEsc(o.link) + '"><img src="' + mailEsc(u) + '" alt="" width="' + (small ? 66 : 172) + '" style="display:block;width:' + (small ? 66 : 172) + 'px;height:auto;border:0;border-radius:12px"></a></td>').join('') + '</tr></table>';
  const songLine = one ? '<p style="margin:' + (small ? '12px' : '22px') + ' 0 2px;font:600 ' + (small ? 17 : 22) + 'px/1.25 Georgia,\'Times New Roman\',serif;color:#1d0b1e;text-align:center">' + mailEsc(o.songs[0].title) + '</p>'
      + (o.songs[0].artist ? '<p style="margin:0;font:15px/1.4 Helvetica,Arial,sans-serif;color:#6b5a62;text-align:center">' + mailEsc(o.songs[0].artist) + '</p>' : '')
    : '<p style="margin:' + (small ? '12px' : '22px') + ' 0 0;font:15px/1.5 Helvetica,Arial,sans-serif;color:#4a3a42;text-align:center">' + o.songs.slice(0, 4).map(x => '<b style="color:#1d0b1e">' + mailEsc(x.title) + '</b>' + (x.artist ? ' · ' + mailEsc(x.artist) : '')).join('<br>') + (o.songs.length > 4 ? '<br>and ' + (o.songs.length - 4) + ' more' : '') + '</p>';
  return '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
    + '<table role="presentation" cellpadding="0" cellspacing="0" align="center"><tr><td width="' + (small ? 44 : 64) + '" height="' + (small ? 44 : 64) + '" align="center" valign="middle" style="width:' + (small ? 44 : 64) + 'px;height:' + (small ? 44 : 64) + 'px;border-radius:50%;background:' + t + ';font:' + (small ? 22 : 32) + 'px/1 Arial,sans-serif;color:#ffffff">&#9829;&#65038;</td></tr></table>'
    + '<h1 style="margin:' + (small ? '10px' : '16px') + ' 0 4px;font:500 ' + (small ? 19 : 27) + 'px/1.25 Georgia,\'Times New Roman\',serif;color:#1d0b1e;text-align:center">' + mailEsc(o.who) + ' ' + mailEsc(o.what) + '</h1>'
    + (o.pl ? '<p style="margin:0 0 ' + (small ? 12 : 20) + 'px;font:13px/1.4 Helvetica,Arial,sans-serif;letter-spacing:.06em;text-transform:uppercase;color:' + t + ';text-align:center">on ' + mailEsc(o.pl) + '</p>' : '<div style="height:' + (small ? 10 : 18) + 'px"></div>')
    + cover + songLine
    + (small ? '' : '<p style="margin:22px 0 0;font:italic 16px/1.5 Georgia,serif;color:#8a7680;text-align:center">' + mailEsc(line) + '</p>')
    + '<div style="margin:' + (small ? '14px' : '24px') + ' 0 0;text-align:center"><a href="' + mailEsc(o.link) + '" style="display:inline-block;padding:13px 26px;border-radius:999px;background:' + t + ';color:#ffffff;font:600 13px/1 Helvetica,Arial,sans-serif;letter-spacing:.08em;text-transform:uppercase;text-decoration:none">' + (one ? 'Listen to it' : 'See them') + '</a></div>'
    + '</td></tr></table>';
}
// a sign-in or confirm code, by email: the code big, in the month's colour
function codeMailHtml(code, said, after) {
  const t = mailTint();
  return mailShell('<h1 style="margin:0 0 10px;font:500 24px/1.25 Georgia,\'Times New Roman\',serif;color:#1d0b1e">Your code</h1>'
    + '<p style="margin:0 0 18px;font:15px/1.5 Helvetica,Arial,sans-serif;color:#4a3a42">' + mailEsc(said) + '</p>'
    + '<p style="margin:0 0 18px;padding:18px 0;border-radius:14px;background:#f6f0f2;text-align:center;font:700 38px/1 Menlo,Consolas,monospace;letter-spacing:.3em;color:' + t + '">' + mailEsc(code) + '</p>'
    + '<p style="margin:0;font:14px/1.5 Helvetica,Arial,sans-serif;color:#6b5a62">' + mailEsc(after) + '</p>');
}
async function noteMail(env, to, msg, cat) {   // cat: 'song' (the Song of the Day), 'direct' (to them: messages, reactions, hearts) or 'other'
  if (!mailReady(env)) return;
  cat = cat || 'other';
  const all = (await env.SOTD.get('members', 'json')) || {}, ids = (to || Object.keys(all)).filter(id => all[id] && mailWants(mailMode(all[id]), cat));
  for (const id of ids) {
    const m = all[id], item = Object.assign(mailItem(msg), { c: cat }), mode = mailMode(m);
    let sent = false;
    if (mode === 'week') {   // (kept for Monday's email)
      const k = 'mwk:' + id, l = (await env.SOTD.get(k, 'json')) || [];
      l.push(Object.assign({}, item, { h: undefined })); while (l.length > 80) l.shift();
      await kvSoft(env, k, JSON.stringify(l), { expirationTtl: 10 * 86400 });
      continue;
    }
    if (mode !== 'day' && await dayCount(env, 'mailme:' + id, MAIL_EACH_DAY) && await dayCount(env, 'mailme-all', MAIL_ALL_DAY)) {
      try { await sendMail(env, m.email, item.t, (item.b ? item.b + '\n\n' : '') + (item.f ? 'Fact of the Day: ' + item.f + '\n\n' : '') + item.u + mailFoot(), 'note', mailShell(item.h || mailItemHtml(item, true), item.u, item.i)); sent = true; } catch (e) { console.log('noteMail: ' + (e && e.message)); }
    }
    if (!sent) {   // (once a day, or past today's allowance: in tomorrow morning's email)
      const k = 'mdig:' + id, l = (await env.SOTD.get(k, 'json')) || [];
      l.push(item); while (l.length > MAIL_DIG_MAX) l.shift();
      await kvSoft(env, k, JSON.stringify(l), { expirationTtl: 3 * 86400 });
    }
  }
}
// a summary email, a day's or a week's (John, 6 Nov 2026: "a weekly summary option ... nicely formatted"): its heading and
// dates, a line of counts, the songs as covers two to a row, then what was to them, then the rest
function digestHtml(items, week, tint) {   // "The week on Song of the Day": the songs, everyone's; "Your week": only what came to them (John, 6 Nov 2026)
  const fmt = t => new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'Europe/London' });
  const from = items.reduce((a, x) => Math.min(a, x.at || Date.now()), Date.now()), to = items.reduce((a, x) => Math.max(a, x.at || 0), 0) || Date.now();
  const songs = items.filter(x => x.c === 'song'), mine = items.filter(x => x.c === 'direct'), rest = items.filter(x => x.c !== 'song' && x.c !== 'direct');
  const span = week ? 'week' : 'day', when = mailEsc(fmt(from) === fmt(to) ? fmt(to) : fmt(from) + ' – ' + fmt(to));
  const big = t => '<h1 style="margin:0 0 4px;font:500 28px/1.2 Georgia,\'Times New Roman\',serif;color:#1d0b1e">' + t + '</h1>';
  const sub = t => '<p style="margin:0;font:15px/1.5 Helvetica,Arial,sans-serif;color:#4a3a42">' + mailEsc(t) + '</p>';
  const h2 = t => '<p style="margin:22px 0 12px;font:700 11px/1 Helvetica,Arial,sans-serif;letter-spacing:.16em;text-transform:uppercase;color:' + tint + '">' + t + '</p>';
  let out = '';
  if (songs.length) {
    out += big('The ' + span + ' on Song of the Day') + '<p style="margin:0 0 4px;font:14px/1.4 Helvetica,Arial,sans-serif;color:#8a7680">' + when + '</p>' + sub(digestCounts(songs, [], 0))
      + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:18px"><tr>';
    songs.slice(0, 14).forEach((x, i) => {
      const img = mailImg(x.i), link = x.u || SITE_SONGS;
      if (i && i % 2 === 0) out += '</tr><tr>';
      out += '<td width="50%" valign="top" style="padding:0 ' + (i % 2 ? '0' : '6px') + ' 16px ' + (i % 2 ? '6px' : '0') + '"><a href="' + mailEsc(link) + '" style="text-decoration:none">'
        + (img ? '<img src="' + mailEsc(img) + '" alt="" width="250" style="display:block;width:100%;max-width:250px;height:auto;border:0;border-radius:12px;margin:0 0 8px">' : '')
        + '<span style="display:block;font:600 15px/1.3 Georgia,\'Times New Roman\',serif;color:#1d0b1e">' + mailEsc(String(x.t).replace(/\s+-\s+(\d{4}\s+)?(remaster(ed)?|mono|stereo|single version|radio edit)\b.*$/i, '')) + '</span>'
        + (x.b ? '<span style="display:block;margin-top:2px;font:13px/1.4 Helvetica,Arial,sans-serif;color:#6b5a62">' + mailEsc(String(x.b).split('\n')[0].replace(/\s*·\s*(today’s|the latest) Song of the Day$/, '').slice(0, 90)) + '</span>' : '') + '</a></td>';
    });
    if (Math.min(songs.length, 14) % 2) out += '<td width="50%"></td>';
    out += '</tr></table>' + (songs.length > 14 ? '<p style="margin:0;font:14px/1.4 Helvetica,Arial,sans-serif;color:#6b5a62">and ' + (songs.length - 14) + ' more on the calendar</p>' : '');
  }
  if (mine.length || rest.length) {
    out += (songs.length ? '<div style="margin:26px 0 0;padding-top:24px;border-top:2px solid ' + tint + '"></div>' : '') + big('Your ' + span) + (songs.length ? '' : '<p style="margin:0 0 4px;font:14px/1.4 Helvetica,Arial,sans-serif;color:#8a7680">' + when + '</p>') + sub(digestCounts([], mine, rest.length));
    const list = (title, xs) => { if (!xs.length) return; out += h2(title) + xs.slice(0, 20).map((x, i) => '<div style="padding:' + (i ? '16px' : '0') + ' 0 16px;' + (i ? 'border-top:1px solid #eadfe3' : '') + '">' + (x.hs || mailItemHtml(x, false)) + '</div>').join('') + (xs.length > 20 ? '<p style="margin:0;font:14px/1.4 Helvetica,Arial,sans-serif;color:#6b5a62">and ' + (xs.length - 20) + ' more in your inbox</p>' : ''); };
    list('Messages to you', mine); list('Notifications', rest);
  }
  return out + '<div style="margin:26px 0 0"><a href="' + mailEsc(SITE_SONGS) + '" style="display:inline-block;padding:13px 26px;border-radius:999px;background:' + tint + ';color:#ffffff;font:600 13px/1 Helvetica,Arial,sans-serif;letter-spacing:.08em;text-transform:uppercase;text-decoration:none">Open the calendar</a></div>';
}
function digestCounts(songs, mine, rest) { return [songs.length ? songs.length + (songs.length === 1 ? ' new song' : ' new songs') : '', mine.length ? mine.length + (mine.length === 1 ? ' message to you' : ' messages to you') : '', rest ? rest + (rest === 1 ? ' notification' : ' notifications') : ''].filter(Boolean).join(' · '); }
function digestTitle(items, week) {   // the email's subject and the notification's title
  const songs = items.filter(x => x.c === 'song'), mine = items.filter(x => x.c === 'direct'), rest = items.length - songs.length - mine.length, span = week ? 'week' : 'day';
  if (!songs.length) return 'Your ' + span + ' on Song of the Day: ' + digestCounts([], mine, rest);
  return 'The ' + span + ' on Song of the Day: ' + digestCounts(songs, [], 0) + (mine.length || rest ? ' · your ' + span + ': ' + digestCounts([], mine, rest) : '');
}
// the week's summaries, Monday from 8am UK: by email for those who chose it (mwk:<member>), and as one notification (pwk:<member>)
async function weekDigests(env) {
  if (Number(uplHM().slice(0, 2)) < 8 || new Date().toLocaleDateString('en-GB', { weekday: 'short', timeZone: 'Europe/London' }) !== 'Mon') return;
  const done = 'wkday:' + londonDay();
  if (await env.SOTD.get(done)) return;
  await env.SOTD.put(done, '1', { expirationTtl: 3 * 86400 });
  const all = (await env.SOTD.get('members', 'json')) || {}, subs = (await env.SOTD.get('pushSubs', 'json')) || {};
  let k = null;
  for (const pre of ['mwk:', 'pwk:']) {
    let cursor;
    do {
      const l = await env.SOTD.list({ prefix: pre, cursor });
      for (const key of l.keys) {
        const id = key.name.slice(4), m = all[id], items = (await env.SOTD.get(key.name, 'json')) || [];
        await env.SOTD.delete(key.name);
        if (!m || !items.length) continue;
        const songs = items.filter(x => x.c === 'song'), title = digestTitle(items, true);
        if (pre === 'mwk:') {
          if (!mailReady(env) || mailMode(m) !== 'week') continue;
          const first = songs.find(x => mailImg(x.i)) || items[0], tint = mailTint(first.u, first.i);
          const text = title + '\n\n' + items.map(x => '• ' + x.t + (x.b ? '\n  ' + x.b : '') + '\n  ' + x.u).join('\n\n');
          try { await sendMail(env, m.email, title, text + mailFoot(), 'notes', mailShell(digestHtml(items, true, tint), first.u, first.i)); } catch (e) { console.log('weekDigests: ' + (e && e.message)); }
        } else {
          if (pushMode(m) !== 'week') continue;
          k = k || await vapidKeys(env);
          const unread = ((await env.SOTD.get('inbox:' + id, 'json')) || []).filter(y => !y.r).length;
          const pic = (songs.find(x => x.i) || {}).i || '';
          const msg = Object.assign({ title: title.replace(/:.*$/, ''), body: title.replace(/^[^:]*:\s*/, '').replace(/ · your week: /, '\nYour week: ') + (songs.length ? '\n' + songs.slice(-3).reverse().map(x => x.t).join(' · ') : ''), url: SITE_SONGS, tag: 'sotd-week', unread: unread || items.length }, pic ? { icon: pic } : {});
          await Promise.all(Object.keys(subs).filter(sid => subs[sid].m === id).map(sid => pushTo(env, k, subs[sid], msg).catch(() => {})));
        }
      }
      cursor = l.list_complete ? null : l.cursor;
    } while (cursor);
  }
}
async function mailDigests(env) {   // the day's email for each member with notifications waiting: from 8am UK, once a day
  if (!mailReady(env) || Number(uplHM().slice(0, 2)) < 8) return;
  const day = londonDay(), done = 'mdigday:' + day;
  if (await env.SOTD.get(done)) return;
  await env.SOTD.put(done, '1', { expirationTtl: 2 * 86400 });
  const all = (await env.SOTD.get('members', 'json')) || {};
  let cursor;
  do {
    const l = await env.SOTD.list({ prefix: 'mdig:', cursor });
    for (const key of l.keys) {
      const id = key.name.slice(5), m = all[id], items = (await env.SOTD.get(key.name, 'json')) || [];
      await env.SOTD.delete(key.name);
      if (!mailMode(m) || !items.length) continue;
      const text = items.map(x => '• ' + x.t + (x.b ? '\n  ' + x.b : '') + '\n  ' + x.u).join('\n\n');
      const first = items.find(x => mailMonthOf(x.u) || mailMonthOf(x.i)) || items[0], lead = first.u;   // (the first song's month)
      const html = mailShell(items.length === 1 ? items[0].h || mailItemHtml(items[0], true) : digestHtml(items, false, mailTint(lead, first.i)), lead, first.i);
      try { await sendMail(env, m.email, items.length === 1 ? items[0].t : digestTitle(items, false), text + mailFoot(), 'notes', html); }
      catch (e) { console.log('mailDigests: ' + (e && e.message)); }
    }
    cursor = l.list_complete ? null : l.cursor;
  } while (cursor);
}
async function pushAll(env, msg, to, meta) {   // to: only these members (else everyone); meta: { kind: 'song' | 'note' | 'member', track, via, toNames, fromId, toIds }
  const mailMsg = msg;   // (the email: its fact as a block of its own)
  const inMsg = msg.fact ? Object.assign({}, msg, { body: (msg.body ? msg.body + '\n' : '') + 'Fact of the Day: ' + msg.fact }) : msg;   // (the inbox: the whole fact, as the email has it: John, 6 Nov 2026, "inbox messages cut off the fact")
  if (msg.fact) msg = Object.assign({}, msg, { body: (msg.body ? msg.body + '\n' : '') + 'Fact of the Day: ' + (msg.fact.length > 160 ? msg.fact.slice(0, 157).replace(/\s+\S*$/, '') + '…' : msg.fact) });   // (a phone's lock screen: shortened)   // (phones and inboxes: the fact under the song, John, 4 Nov 2026)
  delete msg.fact; delete inMsg.fact;
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {}, k = await vapidKeys(env);
  const unread = await inboxAdd(env, to, inMsg, meta);   // into each one's inbox first (notifications on or not); their unread counts
  const m0 = meta || {}, cat = m0.kind === 'song' ? 'song' : m0.direct || (m0.kind === 'member' && (m0.toNames || (m0.via && m0.via.t === 'spotify'))) ? 'direct' : 'other';   // (to them alone: direct)
  await noteMail(env, to, Object.assign({ cta: cat === 'song' ? 'Listen' : '' }, mailMsg), cat).catch(e => console.log('noteMail: ' + (e && e.message)));   // (and by email, for those who asked)
  let sent = 0, failed = 0; const gone = [], errors = [];
  const allM = (await env.SOTD.get('members', 'json')) || {}, gate = {};
  for (const mid of new Set(Object.values(subs).map(x => x.m))) if (!to || to.includes(mid)) gate[mid] = (m0.quiet || []).includes(mid) ? false : await pushGate(env, allM, mid, cat, msg);   // (quiet: inbox and email only)   // (as each chose for their devices)
  const ids = Object.keys(subs).filter(id => (!to || to.includes(subs[id].m)) && gate[subs[id].m] === true);
  for (let i = 0; i < ids.length; i += 20) {   // twenty at a time
    await Promise.all(ids.slice(i, i + 20).map(async id => {
      try {
        const { st, why } = await pushTo(env, k, subs[id], Object.assign({ unread: unread[subs[id].m] || 1, ib: unread[''] }, msg));   // (the number on the app's icon)
        if (st >= 200 && st < 300) sent++;
        else if (st === 404 || st === 410) gone.push(id);
        else { failed++; errors.push({ to: subHost(subs[id]), status: st, why }); console.log('push ' + st + ' ' + subHost(subs[id]) + ': ' + why); }
      } catch (e) { failed++; errors.push({ to: subHost(subs[id]), status: 0, why: String(e && e.message || e).slice(0, 160) }); console.log('push: ' + (e && e.message)); }
    }));
  }
  if (gone.length) {   // read again just before writing: a device may have turned them on meanwhile
    const now = (await env.SOTD.get('pushSubs', 'json')) || {};
    gone.forEach(id => delete now[id]);
    await kvSoft(env, 'pushSubs', JSON.stringify(now));
  }
  const res = { id: unread[''], sent, gone: gone.length, failed, devices: ids.length - gone.length, errors: errors.slice(0, 5), at: Date.now(), title: msg.subject || msg.title,
    inboxes: Object.keys(unread).filter(k => k).length };   // inboxes: the members it went into (notifications on or not)
  if (!meta || (meta.kind !== 'member' && meta.kind !== 'ask' && meta.kind !== 'upl')) await kvSoft(env, 'pushLastResult', JSON.stringify(res));   // the last send of John's, for the members page
  // and the last 40, for the admin page's Status tab: when each went, to how many, and how the push services took it
  const log = await pushHistory(env);
  log.unshift({ id: unread[''], at: res.at, title: msg.subject || msg.title, kind: (meta && meta.kind) || 'note', track: (meta && meta.track) || '', via: (meta && meta.via) || null,
    toNames: meta && meta.kind === 'member' ? meta.toNames || 'everyone' : undefined,
    to: to ? to.length : 0, inboxes: res.inboxes, devices: ids.length, sent, failed, gone: gone.length, errors: errors.slice(0, 3) });
  log.length = Math.min(log.length, PUSH_LOG);
  await kvSoft(env, 'pushLog', JSON.stringify(log));
  return res;
}
// The notifications sent (KV pushLog, newest first). Before 28 Oct 2026 nothing kept them, so the first time it's read
// it's made from the members' inboxes (every message goes into each one's inbox, under one id): when, what and how many
// inboxes, and the push services' answer for the one the members page kept (pushLastResult); old: true marks those.
const PUSH_LOG = 40;
async function pushHistory(env) {
  const have = await env.SOTD.get('pushLog', 'json');
  if (have) return have;
  const all = (await env.SOTD.get('members', 'json')) || {}, last = await env.SOTD.get('pushLastResult', 'json'), byId = {};
  for (const id of Object.keys(all)) {
    for (const x of (await env.SOTD.get('inbox:' + id, 'json')) || []) {
      const e = byId[x.id] || (byId[x.id] = { id: x.id, at: x.at, title: x.t, kind: /#\d{2}-\d{2}-\d{4}$/.test(x.u || '') && /Song of the Day$/.test(x.b || '') ? 'song' : 'note', track: '', inboxes: 0, old: true });
      e.inboxes++;
    }
  }
  const log = Object.values(byId).sort((a, b) => b.at - a.at).slice(0, PUSH_LOG);
  const m = last && log.find(e => e.title === last.title && Math.abs(e.at - last.at) < 120e3);
  if (m) Object.assign(m, { devices: last.devices + (last.gone || 0), sent: last.sent, failed: last.failed, gone: last.gone || 0, errors: last.errors || [] });
  await kvSoft(env, 'pushLog', JSON.stringify(log));
  return log;
}
// ---------- each member's inbox: everything sent to them (the day's songs, John's own), newest first, read or not ----------
// Kept per member (inbox:<member>), the newest 60; reading on one device counts for all of theirs. The count of unread
// goes with each notification, for the number on the app's icon, and with /members/me.
const INBOX_MAX = 60;
// A message from someone also says who sent it (fi, their id, for a reply) and who it went to: to 'all', or the others
// chosen (to: [{ i, n }], each one's id and name), leaving out anyone kept apart from the member whose inbox it is.
async function inboxAdd(env, to, msg, meta) {
  const all = (await env.SOTD.get('members', 'json')) || {}, ids = (to || Object.keys(all)).filter(id => all[id]), out = {};
  const item = { id: randomCode(8), at: Date.now(), t: msg.subject || msg.title, b: msg.body || '', u: msg.url || '' };
  if (msg.gif || msg.image) item.i = msg.gif || msg.image; if (msg.icon) item.c = msg.icon;
  if (msg.from) item.f = msg.from;
  const m = meta || {};
  if (m.kind === 'song') item.s = 1;
  if (msg.from && m.fromId) item.fi = m.fromId;
  const toFor = id => m.toIds === 'all' ? 'all' : Array.isArray(m.toIds)
    ? m.toIds.filter(k => k !== id && all[k] && !apart(all, id, k)).map(k => ({ i: k, n: all[k].name })) : undefined;
  await Promise.all(ids.map(async id => {
    const box = (await env.SOTD.get('inbox:' + id, 'json')) || [], mine = msg.from && m.toIds ? Object.assign({}, item, { to: toFor(id) }) : item;
    box.unshift(mine); box.length = Math.min(box.length, INBOX_MAX);
    await kvSoft(env, 'inbox:' + id, JSON.stringify(box));
    out[id] = box.filter(x => !x.r).length;
  }));
  out[''] = item.id;   // (the message's own id, for marking it read from a tap on it)
  return out;
}
// /members/inbox: GET the inbox; POST { read: [ids] | 'all' }, { unread: [ids] } or { del: [ids] }; each gives it back,
// with the unread count
async function memberInbox(request, env, answer) {
  const member = await memberFromToken(request, env);
  if (!member) return answer(401, { error: 'Not a member (any more).' });
  const key = 'inbox:' + member, box = (await env.SOTD.get(key, 'json')) || [];
  if (request.method === 'POST') {
    const body = await request.json().catch(() => ({}));
    const ids = v => new Set((Array.isArray(v) ? v : []).map(String));
    let changed = false;
    if (body.read) { const want = body.read === 'all' ? null : ids(body.read); box.forEach(x => { if (!x.r && (!want || want.has(x.id))) { x.r = Date.now(); changed = true; } }); }
    if (body.unread) { const want = ids(body.unread); box.forEach(x => { if (x.r && want.has(x.id)) { delete x.r; changed = true; } }); }
    if (body.del) { const want = ids(body.del); for (let i = box.length - 1; i >= 0; i--) if (want.has(box[i].id)) { box.splice(i, 1); changed = true; } }
    if (changed) { await env.SOTD.put(key, JSON.stringify(box)); await apnsBadge(env, member, box.filter(x => !x.r).length); }
  } else if (request.method !== 'GET') return answer(405, { error: 'Use GET or POST.' });
  const items = box.map(x => !x.s && rxSong(x) ? Object.assign({}, x, { s: 1 }) : x);
  return answer(200, { items: await withReactions(env, member, items, rxCan), unread: box.filter(x => !x.r).length });
}

// ---------- reactions: an emoji on a message from someone, by anyone it went to (from their inbox). Kept per message
// (KV react:<message id>: { <member id>: { e, at } }, one emoji each); each member sees them on the message in their inbox,
// and its sender on Sent, as rx: [{ e, n: [names, "you" for their own], me }], leaving out anyone kept apart from them.
// POST /members/react { id, e } sets theirs (e '' takes it away) and gives back the message's rx. ----------
const REACTS = ['👍', '❤️', '😂', '😮', '😢', '🙏', '🎉', '🔥'], REACT_DAYS = 400;
// a message that takes reactions: one from someone, or the day's song (s; one sent before s was kept: its day's link and
// "… Song of the Day" words)
function rxSong(x) { return !!(x && !x.f && (x.s || (/#\d{1,2}-\d{1,2}-\d{4}$/.test(x.u || '') && /Song of the Day$/.test(x.b || '')))); }
function rxCan(x) { return !!(x && (x.f || rxSong(x))); }
function rxView(all, viewer, r) {
  const by = {};
  for (const [k, v] of Object.entries(r || {})) {
    if (!all[k] || !REACTS.includes(v.e) || (k !== viewer && apart(all, viewer, k))) continue;
    (by[v.e] = by[v.e] || []).push({ k, n: all[k].name, at: v.at || 0 });
  }
  return REACTS.filter(e => by[e]).map(e => ({ e, n: by[e].sort((a, b) => a.at - b.at).map(p => p.k === viewer ? 'you' : p.n), me: by[e].some(p => p.k === viewer) }));
}
async function withReactions(env, viewer, items, has) {
  const want = items.filter(has);
  if (!want.length) return items;
  const all = (await env.SOTD.get('members', 'json')) || {}, got = {};
  await Promise.all(want.map(async x => { const r = await env.SOTD.get('react:' + x.id, 'json'); if (r) got[x.id] = rxView(all, viewer, r); }));
  return items.map(x => got[x.id] && got[x.id].length ? Object.assign({}, x, { rx: got[x.id] }) : x);
}
async function memberReact(request, env, answer) {
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const who = await memberWho(request, env);
  if (!who) return answer(401, { error: 'Not a member (any more).' });
  const body = await request.json().catch(() => ({})), id = String(body.id || ''), e = String(body.e || '');
  if (e && !REACTS.includes(e)) return answer(400, { error: 'That emoji isn’t one of these: ' + REACTS.join(' ') });
  const box = (await env.SOTD.get('inbox:' + who.id, 'json')) || [];
  const x = id && box.find(y => y.id === id && rxCan(y));
  if (!x) return answer(404, { error: 'No such message in your inbox.' });
  const key = 'react:' + id, r = (await env.SOTD.get(key, 'json')) || {}, prev = r[who.id];
  const again = !!prev && Date.now() - (prev.alerted || prev.at || 0) < 10 * 60e3;   // (reacted to it in the last ten minutes: no second alert: John, 5 Nov 2026, "people retap and get several alerts")
  if (e) r[who.id] = Object.assign({ e, at: Date.now() }, again && prev.alerted ? { alerted: prev.alerted } : {}); else delete r[who.id];
  if (Object.keys(r).length) await env.SOTD.put(key, JSON.stringify(r), { expirationTtl: REACT_DAYS * 86400 }); else await env.SOTD.delete(key);
  // the bell: the sender's, or for the day's song John's (the member marked This is me)
  const ownerId = await env.SOTD.get('owner'), song = rxSong(x), to = song ? ownerId : x.fi;
  if (to && who.all[to]) {
    await bellNote(env, to, id, x.t, who.id, e, song ? x.u : '');
    if (e && !again && takesFrom(who.all, to, who.id, ownerId)) { await bellPush(env, to, who.all[who.id].name, e, x.t, id, who.id, song); r[who.id].alerted = Date.now(); await env.SOTD.put(key, JSON.stringify(r), { expirationTtl: REACT_DAYS * 86400 }); }
  }
  return answer(200, { ok: true, rx: rxView(who.all, who.id, r) });
}

// ---------- the bell: reactions to a member's own messages, and John's to the day's songs, newest first. KV
// bell:<member>: { seen, items: [{ id, k, e, at, t, u }] } (u: the day's link, for a song): one item per message and member
// reacting (a new emoji replaces theirs and comes to the top, taking it away removes it), the newest 50. GET /members/bell
// gives { items: [{ id, n, e, at, t, u }], unseen, seen }, leaving out anyone kept apart from them; POST /members/bell
// { seen: true } marks them all seen. ----------
const BELL_MAX = 50, RX_PUSH_DAY = 40;
// a reaction also goes to the bell's member's devices with notifications on, unless they've turned off other members'
// messages or the reactor's: "Dad reacted 🎉 to your message" ("… to the Song of the Day"), the message's title, the calendar's bell (#bell); one a message and
// reactor on a device (its tag); RX_PUSH_DAY a day from each reactor; not in the inbox, so the app's icon keeps its number
async function bellPush(env, to, name, e, title, msgId, from, song) {
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {}, ids = Object.keys(subs).filter(k => subs[k].m === to);
  if (!await dayCount(env, 'rxpush:' + from, RX_PUSH_DAY)) return;
  const unread = ((await env.SOTD.get('inbox:' + to, 'json')) || []).filter(y => !y.r).length;
  const msg = { title: name + ' reacted ' + e + (song ? ' to the Song of the Day' : ' to your message'), body: '“' + (title || '') + '”', url: SITE_SONGS + '#bell', tag: 'sotd-rx-' + msgId + '-' + from, unread };
  await noteMail(env, [to], msg, 'direct').catch(() => {});
  if (!ids.length || !await pushGate(env, (await env.SOTD.get('members', 'json')) || {}, to, 'direct', msg)) return;
  const k = await vapidKeys(env), gone = [];
  await Promise.all(ids.map(async id => {
    try { const { st } = await pushTo(env, k, subs[id], msg); if (st === 404 || st === 410) gone.push(id); } catch (err) { console.log('rx push: ' + (err && err.message)); }
  }));
  if (gone.length) {
    const now = (await env.SOTD.get('pushSubs', 'json')) || {};
    gone.forEach(id => delete now[id]);
    await kvSoft(env, 'pushSubs', JSON.stringify(now));
  }
}
async function bellNote(env, to, msgId, title, from, e, u) {
  if (to === from) return;
  const key = 'bell:' + to, b = (await env.SOTD.get(key, 'json')) || { seen: 0, items: [] };
  b.items = (b.items || []).filter(y => !(y.id === msgId && y.k === from));
  if (e) b.items.unshift(Object.assign({ id: msgId, k: from, e, at: Date.now(), t: title || '' }, u ? { u } : {}));
  b.items.length = Math.min(b.items.length, BELL_MAX);
  await kvSoft(env, key, JSON.stringify(b));
}
function bellView(all, member, b) {
  const seen = (b && b.seen) || 0;
  const items = ((b && b.items) || []).filter(y => all[y.k] && !apart(all, member, y.k)).map(y => Object.assign({ id: y.id, n: all[y.k].name, e: y.e, at: y.at, t: y.t }, y.u ? { u: y.u } : {}, y.h ? { h: y.h } : {}, y.k === member ? { me: 1 } : {}));
  return { items, unseen: items.filter(y => y.at > seen).length, seen };
}
// a heart on someone's song (John, 3 Nov 2026: "Could a member get a notification when someone hearts their song"): POST
// { track, upl, d } when a member adds a song to My playlist. The song's owner (the member whose calendar or playlist it's
// on; John for his own) gets it in their bell and, if they take messages from them, on their phone. Every heart (HEART_DAY a day each).
const HEART_DAY = 30;
async function memberHeart(request, env, answer) {   // POST { upl, songs: [{ track, title, d }] } (or one: { track, title, d }): the songs of one calendar (John's: no upl) added together, one reaction and one notification (John, 3 Nov 2026: "Put a heart next to any 'add 20 to my playlist' and have that as one reaction and one notification")
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const who = await memberWho(request, env);
  if (!who) return answer(401, { error: 'Not a member (any more).' });
  const body = await request.json().catch(() => ({})), pid = String(body.upl || '');
  const asked = (Array.isArray(body.songs) ? body.songs : [{ track: body.track, title: body.title, d: body.d }]).slice(0, 200)
    .map(x => ({ track: String((x && x.track) || ''), title: String((x && x.title) || '').slice(0, 120), d: /^\d{4}-\d{2}-\d{2}$/.test(String((x && x.d) || '')) ? String(x.d) : '' }))
    .filter(x => /^[0-9A-Za-z]{22}$/.test(x.track));
  if (!asked.length) return answer(400, { error: 'Which song?' });
  const ownerId = await env.SOTD.get('owner');
  let to, list, plName = '', adders = [], editors = [];
  if (pid) {
    const rec = (await uplAll(env))[pid];
    if (!rec || !uplCan(who.all, who.id, rec)) return answer(200, { ok: true });
    const have = {}; ((await uplSongs(env, pid)).songs || []).forEach(y => { have[y.id] = y; });
    to = rec.o; list = asked.filter(x => have[x.track]).map(x => Object.assign(x, { title: have[x.track].title, artist: have[x.track].artist || '', img: have[x.track].img || '', ab: have[x.track].ab || '' }));
    plName = uplName(who.all, rec); editors = (rec.cw || []).filter(k => who.all[k]);
    const sids = await spWhoIds(env, who.all);   // (who added each on Spotify, where they're a member: told it's theirs too, John, 3 Nov 2026)
    adders = [...new Set(list.map(x => sids[x.ab]).filter(k => k && who.all[k]))];
  } else {   // John's: their covers and artists from his list
    to = ownerId; const kept = ((await env.SOTD.get('songsLast', 'json')) || {}).songs || [], by = {}; kept.forEach(y => { if (y && y.id) by[y.id] = y; });
    list = asked.map(x => Object.assign(x, by[x.track] ? { title: x.title || by[x.track].title, artist: by[x.track].artist || '', img: by[x.track].img || '' } : {}));
  }
  if (!to || !who.all[to] || !list.length) return answer(200, { ok: true });   // (their own songs too: John, 3 Nov 2026, "It shouldn't matter if it's from me, I'd like a notification")
  const tell = [...new Set([to].concat(adders, editors))];   // its owner, whoever added the songs, and those who edit it with them (a collaborative playlist)
  const fresh = list;   // (every heart, however many times: John, 3 Nov 2026, "it's fine to get a notification no matter how many times someone hearts a song"; HEART_DAY a day each keeps it sane)
  if (!fresh.length || !await dayCount(env, 'heartday:' + who.id, HEART_DAY)) return answer(200, { ok: true });
  const linkOf = x => pid ? SITE_SONGS + '#u=' + pid + (x.d ? '&d=' + x.d.slice(8, 10) + '-' + x.d.slice(5, 7) + '-' + x.d.slice(0, 4) : '')
    : SITE_SONGS + (x.d ? '#' + Number(x.d.slice(8, 10)) + '-' + Number(x.d.slice(5, 7)) + '-' + x.d.slice(0, 4) : '');
  const n = fresh.length, one = n === 1, title = one ? fresh[0].title : n + ' of your songs';
  const names = fresh.slice(0, 3).map(x => '“' + (x.title || 'a song') + '”').join(', ') + (n > 3 ? ' and ' + (n - 3) + ' more' : '');
  const id = one ? 'h:' + fresh[0].track : 'hb:' + Date.now().toString(36);
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {};
  let vk = null;
  for (const r of tell) {
    const theirs = r === to || adders.includes(r), what = theirs ? (one ? ' hearted your song ❤️' : ' hearted ' + n + ' of your songs ❤️') : (one ? ' hearted a song on “' + plName + '” ❤️' : ' hearted ' + n + ' songs on “' + plName + '” ❤️');
    const key = 'bell:' + r, b = (await env.SOTD.get(key, 'json')) || { seen: 0, items: [] };
    b.items = [{ id, k: who.id, e: '❤️', h: one ? 1 : n, at: Date.now(), t: one ? title : names, u: linkOf(fresh[0]) }].concat((b.items || []).filter(y => !(y.id === id && y.k === who.id)));
    b.items.length = Math.min(b.items.length, BELL_MAX);
    await kvSoft(env, key, JSON.stringify(b));
    if (!(r === who.id || takesFrom(who.all, r, who.id, ownerId))) continue;
    const ids = Object.keys(subs).filter(k => subs[k].m === r);
    const msg = { title: (r === who.id ? 'You' : who.all[who.id].name) + what, body: one ? '“' + (title || 'Your song') + '”' : names, url: SITE_SONGS + '#bell', tag: 'sotd-heart-' + id + '-' + who.id };
    const ho = { who: r === who.id ? 'You' : who.all[who.id].name, what: what.replace(/\s*❤️$/, '').replace(/ on “.*”$/, '').trim(), songs: fresh, pl: pid ? plName : '', link: linkOf(fresh[0]), tint: mailTint(linkOf(fresh[0]), fresh[0].img), seed: Date.now() % 997 };
    await noteMail(env, [r], Object.assign({}, msg, { link: linkOf(fresh[0]), image: fresh[0].img || '', cta: 'See it', html: heartMailHtml(ho), htmlSmall: heartMailHtml(ho, true) }), 'direct').catch(() => {});
    if (ids.length && await pushGate(env, who.all, r, 'direct', msg)) {
      vk = vk || await vapidKeys(env);
      await Promise.all(ids.map(sid => pushTo(env, vk, subs[sid], msg).catch(err => console.log('heart push: ' + (err && err.message)))));
    }
  }
  return answer(200, { ok: true, n, told: tell.length });
}
async function memberBell(request, env, answer) {
  const who = await memberWho(request, env);
  if (!who) return answer(401, { error: 'Not a member (any more).' });
  const key = 'bell:' + who.id, b = (await env.SOTD.get(key, 'json')) || { seen: 0, items: [] };
  if (request.method === 'POST') {
    const body = await request.json().catch(() => ({}));
    if (body.seen) { b.seen = Date.now(); await kvSoft(env, key, JSON.stringify(b)); }
  } else if (request.method !== 'GET') return answer(405, { error: 'Use GET or POST.' });
  return answer(200, bellView(who.all, who.id, b));
}

// ---------- members' own Spotify playlists: each a calendar like John's (a song a day, by the day it was added) or a
// plain playlist. KV upls: { <pid>: { o (owner's member id), sp (Spotify id), n (Spotify's name), mode ('cal' | 'list'),
// link (the collaboration invite link they pasted), at (added), base (songs added before it are out from the start),
// st ('join': John's account can't read it yet; 'ok'; 'err'), err, read (last read), cnt } }. KV upl:<pid>: { at, songs
// (as playlistItems gives them), rel: { <track>: when its owner signed it off } }. Spotify lets John's app read only
// playlists his account owns or collaborates on, so a member turns on collaboration and sends him the invite link;
// once he has joined, the Worker reads it every UPL_EVERY from 06:00 to 23:00 (and when its owner taps Check now; until
// then it looks every UPL_JOIN_EVERY for a day, then hourly). A
// song added after it was added waits for its owner to sign it off; then it's out for everyone and goes to the
// members as a message from them. ----------
const UPL_EVERY = 30 * 60e3, UPL_JOIN_EVERY = 10 * 60e3, UPL_CHECK_GAP = 60e3, UPL_SIGNOFF_DAY = 20, UPL_LOOK_MOST = 4, UPL_PV_MOST = 60;
function uplSpId(link) {   // a playlist's Spotify id from its link (open.spotify.com/playlist/<id>…, spotify:playlist:<id>) or the id
  const m = /(?:open\.spotify\.com\/(?:intl-[\w-]+\/)?playlist\/|spotify:playlist:)([0-9A-Za-z]{22})/.exec(String(link || '')) || /^\s*([0-9A-Za-z]{22})\s*$/.exec(String(link || ''));
  return m ? m[1] : '';
}
function uplInvite(link) {   // the link as pasted, when it's Spotify's own (the invite carries its own code)
  const x = String(link || '').trim();
  return /^https:\/\/open\.spotify\.com\/[\w\-/?=&%.]+$/.test(x) && x.length <= 400 ? x : '';
}
// A member's playlist from Exportify (exportify.app: they sign in to Spotify there and export it as a CSV file, which
// the calendar reads and sends here as songs): no collaboration needed, as Exportify reads it with their own Spotify
// sign-in. A snapshot: they upload a fresh export to bring in new songs (op csvup). UPL_CSV_MOST songs at most (Spotify's own
// most for a playlist): members can have as many songs as they like; only songs with their own files are limited.
const UPL_CSV_MOST = 10000, SP_ID = /^[0-9A-Za-z]{22}$/;
function uplCsvSongs(list) {   // the songs as the page sends them, made safe: [{ a, id, title, artist, artists, aids, album, alid, img }] in the playlist's order
  if (!Array.isArray(list)) return null;
  const out = [], seen = new Set(), txt = (v, n) => String(v == null ? '' : v).replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, n);
  for (const x of list.slice(0, UPL_CSV_MOST)) {
    if (!x || !SP_ID.test(String(x.id || '')) || seen.has(x.id)) continue;
    const at = Date.parse(x.a);
    if (isNaN(at)) continue;
    const artists = (Array.isArray(x.artists) ? x.artists : []).map(a => txt(a, 120)).filter(Boolean).slice(0, 12);
    const aids = (Array.isArray(x.aids) ? x.aids : []).map(String).filter(a => SP_ID.test(a)).slice(0, 12);
    const img = /^https:\/\/i\.scdn\.co\/image\/[0-9a-f]{20,60}$/.test(String(x.img || '')) ? String(x.img).replace('ab67616d0000b273', 'ab67616d00001e02') : '';   // (the 300-pixel cover, as Spotify's reads give)
    seen.add(x.id);
    out.push({ a: new Date(at).toISOString().replace('.000Z', 'Z'), id: x.id, title: txt(x.title, 200) || 'Untitled', artist: artists.join(', ') || txt(x.artist, 300), artists, aids,
      album: txt(x.album, 200), alid: SP_ID.test(String(x.alid || '')) ? x.alid : '', img, rd: /^\d{4}(-\d\d){0,2}$/.test(String(x.rd || '')) ? String(x.rd) : undefined, ab: /^[\w.-]{1,64}$/.test(String(x.ab || '')) ? String(x.ab) : undefined });   // (ab: who added it, their Spotify user id)   // (rd: Exportify's album release date)
  }
  return out;
}
function uplFileKey(f) { return String(f || '').toLowerCase().replace(/\.csv$/, '').replace(/\s*\(\d+\)$/, '').replace(/[^\p{L}\p{N}]+/gu, '_').replace(/^_+|_+$/g, '').slice(0, 120); }   // Exportify's file for it (road_trip.csv): a fresh export's same file is the same playlist
async function uplCsvPreviews(env, list) {   // the preview Exportify gives a song (pv: p.scdn.co's id for it), kept as a looked-up one is
  const pv = Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {});
  let n = 0;
  for (const x of (Array.isArray(list) ? list : []).slice(0, UPL_CSV_MOST)) if (x && SP_ID.test(String(x.id || '')) && /^[0-9a-f]{20,64}$/.test(String(x.pv || '')) && !(pv[x.id] && pv[x.id] !== '-')) { pv[x.id] = x.pv; n++; }
  if (n) { Object.assign(pvMem, pv); await kvSoft(env, 'previews', JSON.stringify(pv)); }
}
// /members/impfile: the Exportify download a member last loaded, kept for them (John, 1 Nov 2026: "Once a zip or csv
// file is loaded in, can it stay in memory. With a way to update the file if necessary"): GET { at, lists } (404 none),
// POST { lists } keeps it in place of the last (R2 imp/<member>.json; each list: name, key, file, songs, all, by, last),
// POST { drop: true } forgets it. Import from Spotify opens on its playlists, with Update the file.
async function memberImpFile(request, env, answer) {
  const who = await memberWho(request, env);
  if (!who) return answer(401, { error: 'Not a member (any more).' });
  if (!env.MEDIA) return answer(503, { error: 'Not set up.' });
  const key = 'imp/' + who.id + '.json', gzKey = key + '.gz';   // (.gz: squeezed on the page, kept as it is: a whole download can be tens of MB as JSON: John, 3 Nov 2026, it wasn't kept)
  if (request.method === 'GET') {
    const g = await env.MEDIA.get(gzKey);
    if (g) return new Response(g.body, { headers: Object.assign(factsHeaders(MEMBERS_ORIGIN), { 'Content-Type': 'application/gzip', 'Access-Control-Expose-Headers': 'Content-Type' }) });
    const o = await env.MEDIA.get(key);
    return o ? new Response(o.body, { headers: Object.assign(factsHeaders(MEMBERS_ORIGIN), { 'Content-Type': 'application/json; charset=utf-8' }) }) : answer(404, { none: true });   // (the page's own origin may read it)
  }
  if (request.method !== 'POST') return answer(405, { error: 'Use GET or POST.' });
  if (/gzip/.test(request.headers.get('Content-Type') || '')) {
    const buf = await request.arrayBuffer();
    if (!buf.byteLength || buf.byteLength > 60e6) return answer(413, { error: 'That download is too big to keep.' });
    const u = new Uint8Array(buf, 0, 2); if (u[0] !== 0x1f || u[1] !== 0x8b) return answer(400, { error: 'Not squeezed.' });
    await env.MEDIA.put(gzKey, buf, { httpMetadata: { contentType: 'application/gzip' } }); await env.MEDIA.delete(key);
    return answer(200, { ok: true, at: Date.now() });
  }
  const text = await request.text();
  if (text.length > 30e6) return answer(413, { error: 'That download is too big to keep.' });
  let b; try { b = JSON.parse(text); } catch (e) { return answer(400, { error: 'Not JSON.' }); }
  if (b && b.drop) { await env.MEDIA.delete(key); await env.MEDIA.delete(gzKey); return answer(200, { ok: true }); }
  const lists = Array.isArray(b && b.lists) ? b.lists.slice(0, 2000).filter(l => l && typeof l.name === 'string' && Array.isArray(l.songs)) : null;
  if (!lists || !lists.length) return answer(400, { error: 'No playlists.' });
  const at = Date.now();
  await env.MEDIA.put(key, JSON.stringify({ at, lists }), { httpMetadata: { contentType: 'application/json' } }); await env.MEDIA.delete(gzKey);
  return answer(200, { ok: true, at });
}
// /members/impdrop?c=<code>: Exportify's download, caught by the Android app's own Exportify screen (John, 2 Nov 2026:
// Exportify inside the Android app too, as the iPhone's). The app is a Chrome app, so its screen can't give the file to
// the page itself: it sends it here (POST, the file as it is, X-Name its name), signed in as the member the page said,
// and the page, back from it, takes it (GET: the file once, then it's gone). R2 drop/<member>/<code>; 40 MB at most.
async function memberImpDrop(request, url, env, answer) {
  const who = await memberWho(request, env);
  if (!who) return answer(401, { error: 'Not a member (any more).' });
  if (!env.MEDIA) return answer(503, { error: 'Not set up.' });
  const c = String(url.searchParams.get('c') || '');
  if (!/^[A-Za-z0-9]{16,40}$/.test(c)) return answer(400, { error: 'Which file?' });
  const key = 'drop/' + who.id + '/' + c;
  if (request.method === 'GET') {
    const o = await env.MEDIA.get(key);
    if (!o) return answer(404, { none: true });
    const body = await new Response(o.body).arrayBuffer(); await env.MEDIA.delete(key);
    const name = (o.customMetadata && o.customMetadata.name) || 'playlists.zip';
    return new Response(body, { headers: Object.assign(factsHeaders(MEMBERS_ORIGIN), { 'Content-Type': (o.httpMetadata && o.httpMetadata.contentType) || 'application/octet-stream', 'X-Name': encodeURIComponent(name), 'Access-Control-Expose-Headers': 'X-Name' }) });
  }
  if (request.method !== 'POST') return answer(405, { error: 'Use GET or POST.' });
  const buf = await request.arrayBuffer();
  if (!buf.byteLength) return answer(400, { error: 'No file.' });
  if (buf.byteLength > 40e6) return answer(413, { error: 'That download is too big.' });
  let name = ''; try { name = decodeURIComponent(request.headers.get('X-Name') || ''); } catch (e) {}
  name = name.replace(/[\\/\u0000-\u001f]/g, '').slice(0, 120) || 'playlists.zip';
  if (!/\.(zip|csv)$/i.test(name)) name += /^PK/.test(String.fromCharCode(...new Uint8Array(buf.slice(0, 2)))) ? '.zip' : '.csv';
  const old = await env.MEDIA.list({ prefix: 'drop/' + who.id + '/' }).catch(() => ({ objects: [] }));   // (any left from before)
  for (const o of old.objects || []) await env.MEDIA.delete(o.key);
  await env.MEDIA.put(key, buf, { httpMetadata: { contentType: /\.zip$/i.test(name) ? 'application/zip' : 'text/csv' }, customMetadata: { name } });
  return answer(200, { ok: true });
}
// ---------- a member's own Spotify (John, 2 Nov 2026: "an option they set up once on their account - if they choose play
// through spotify there it will always play through spotify") ----------
// They connect once (Profile › Account): /members/spotify start gives a one-time link to /splink, which sends them to
// Spotify (PKCE, the same Spotify app and /callback as John's link, told apart by its state); back at /callback their
// sign-in is kept (KV spm:<member>: tokens, who, Premium or not, their choice). Then the calendar plays each song by
// telling their Spotify to (Spotify Connect: Premium, and Spotify open on one of their devices), from that song on
// through the ones after it, so it carries on while the page sleeps. Spotify lets an app in development mode serve the
// 25 people its owner adds in Spotify's dashboard (User Management): until John adds them, Spotify refuses and they're
// listed for him (blocked), with the Spotify email they give.
const SP_SCOPES = 'user-read-playback-state user-modify-playback-state user-read-currently-playing user-read-private user-read-email playlist-read-private playlist-read-collaborative user-library-read';   // (their playlists and Liked Songs too, to import and keep up to date: Spotify lets an app read only the playlists of whoever signed in, John, 4 Nov 2026)
// Spotify allows 5 people per app in development mode, and an account up to 25 apps (July 2026; the apps share one
// request allowance): John's own app first, then the others he adds in Admin (KV spapps: [Client ID]); each member is put
// in the app they're in already, else the first with room, and the Members tab says which app to add them to.
const SP_PER_APP = 5;
async function spApps(env) { const extra = (await env.SOTD.get('spapps', 'json')) || []; return [CLIENT_ID].concat(extra.filter(x => /^[0-9a-f]{32}$/.test(x) && x !== CLIENT_ID)); }
async function spMembers(env) {   // [{ id, rec }] of every member who has connected
  const out = []; let cursor;
  do { const l = await env.SOTD.list({ prefix: 'spm:', cursor }); for (const k of l.keys) { const r = await env.SOTD.get(k.name, 'json'); if (r) out.push({ id: k.name.slice(4), rec: r }); } cursor = l.list_complete ? null : l.cursor; } while (cursor);
  return out;
}
async function spAppFor(env, member) {   // the app this member is in, or the first with room ('' when all are full)
  const apps = await spApps(env), all = await spMembers(env), mine = all.find(x => x.id === member);
  if (mine && mine.rec.cid && apps.includes(mine.rec.cid)) return mine.rec.cid;
  const n = {}; for (const x of all) if (x.id !== member) n[x.rec.cid || CLIENT_ID] = (n[x.rec.cid || CLIENT_ID] || 0) + 1;
  return apps.find(a => (n[a] || 0) < SP_PER_APP) || '';
}
async function spLinkStart(url, env) {
  const n = String(url.searchParams.get('n') || '');
  const want = /^[\w-]{20,60}$/.test(n) ? await env.SOTD.get('spn:' + n, 'json') : null;
  if (!want) throw new HttpError(400, 'This link has run out. Go back to Song of the Day and tap Connect Spotify again.');
  const cid = await spAppFor(env, want.id);
  if (!cid) throw new HttpError(409, 'Spotify playback is full for now (Spotify lets each app have 5 people). Songs play as previews; John can make room.');
  want.cid = cid; await env.SOTD.put('spn:' + n, JSON.stringify(want), { expirationTtl: 900 });
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(48)));
  const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
  const state = 'm' + n;
  const q = new URLSearchParams({ client_id: cid, response_type: 'code', redirect_uri: url.origin + '/callback',
    code_challenge_method: 'S256', code_challenge: challenge, state, scope: SP_SCOPES });
  return new Response(null, { status: 302, headers: { Location: 'https://accounts.spotify.com/authorize?' + q,
    'Set-Cookie': 'spkce=' + state + '.' + verifier + '; Path=/callback; Max-Age=900; HttpOnly; Secure; SameSite=Lax', 'Cache-Control': 'no-store' } });
}
async function spLinkDone(request, url, env) {
  const state = url.searchParams.get('state'), n = state.slice(1), code = url.searchParams.get('code');
  const want = await env.SOTD.get('spn:' + n, 'json');
  const back = want && /^https:\/\/cooperking\.github\.io\//.test(want.back || '') ? want.back : SITE_SONGS;
  const done = (title, say, tag) => new Response('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + esc(title) + '</title>'
    + '<body style="font:17px/1.45 system-ui,sans-serif;margin:0;min-height:100vh;display:grid;place-items:center;background:#14806a;color:#fffaf5;text-align:center"><div style="padding:24px;max-width:420px">'
    + '<h1 style="font:500 30px Georgia,serif;margin:0 0 10px">' + esc(title) + '</h1><p>' + say + '</p>'
    + '<p><a href="' + esc(back + (back.includes('?') ? '&' : '?') + 'spotify=' + tag) + '" style="display:inline-block;margin-top:8px;padding:12px 22px;border-radius:999px;background:#fffaf5;color:#1b1020;text-decoration:none;font-weight:600">Back to Song of the Day</a></p>'
    + '<p style="font-size:14px;opacity:.85">In the iPhone app: tap Done at the top.</p></div></body>', { headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
      'Set-Cookie': 'spkce=; Path=/callback; Max-Age=0; HttpOnly; Secure; SameSite=Lax' } });
  if (!want) return done('That link ran out', 'Go back to Song of the Day and tap Connect Spotify again.', 'err');
  if (url.searchParams.get('error')) return done('Not connected', 'Spotify said it was cancelled. You can try again from your profile.', 'err');
  const m = (request.headers.get('Cookie') || '').match(/(?:^|;\s*)spkce=([\w-]+)\.([\w-]+)/);
  if (!m || m[1] !== state || !code) return done('That link ran out', 'Go back to Song of the Day and tap Connect Spotify again.', 'err');
  const r = await fetch('https://accounts.spotify.com/api/token', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', code, redirect_uri: url.origin + '/callback', client_id: want.cid || CLIENT_ID, code_verifier: m[2] }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.refresh_token) return done('Not connected', 'Spotify didn’t confirm it (' + esc(j.error_description || j.error || String(r.status)) + '). Try again from your profile.', 'err');
  await env.SOTD.delete('spn:' + n);
  const rec = { rt: j.refresh_token, at: j.access_token, exp: Date.now() + (j.expires_in || 3600) * 1000, linked: Date.now(), on: true, cid: want.cid || CLIENT_ID, scope: String(j.scope || '') };
  const me = await fetch(API + '/me', { headers: { Authorization: 'Bearer ' + j.access_token } });
  if (me.ok) { const u = await me.json(); Object.assign(rec, { sid: u.id, name: u.display_name || u.id, email: u.email || '', product: u.product || '' }); }
  else rec.blocked = true;   // (not added in Spotify's dashboard yet)
  await env.SOTD.put('spm:' + want.id, JSON.stringify(rec));
  if (rec.blocked) return done('Nearly there', 'Spotify needs John to add you first. Back in your profile, give the email address of your Spotify account, and it plays through Spotify once he has.', 'wait');
  if (rec.product !== 'premium') return done('Connected', 'Spotify says your account isn’t Premium, which it needs to play whole songs for another app. Songs play as previews until it is.', 'nopremium');
  return done('Connected', 'Songs play through your Spotify now (you can turn it off in your profile).', 'ok');
}
async function spToken(env, id, rec) {   // a member's Spotify access, refreshed as it runs out (PKCE: the refresh token may change)
  if (rec.at && rec.exp > Date.now() + 60e3) return rec.at;
  const r = await fetch('https://accounts.spotify.com/api/token', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'refresh_token', refresh_token: rec.rt, client_id: rec.cid || CLIENT_ID }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { if (j.error === 'invalid_grant') { rec.gone = true; await env.SOTD.put('spm:' + id, JSON.stringify(rec)); } throw new HttpError(401, 'Spotify needs connecting again.'); }
  Object.assign(rec, { at: j.access_token, exp: Date.now() + (j.expires_in || 3600) * 1000, rt: j.refresh_token || rec.rt });
  delete rec.gone;
  await env.SOTD.put('spm:' + id, JSON.stringify(rec));
  return rec.at;
}
// a member waiting to be added in Spotify's dashboard: an alert to John's phone at once, with everything to add them
// (John, 2 Nov 2026: "instant alert with all the details and links needed"; a task on his Mac can't be started from
// here). Once per member and email (KV spask:<member>); the time shown in Admin (spaskat). Tapping it opens Admin's
// Members tab, where their row has Copy email and Open app N in Spotify.
async function spAskAdd(env, id, rec, again) {
  const email = rec.email || rec.wemail || '';
  if (!email) return null;
  try {
    if (!again && (await env.SOTD.get('spask:' + id)) === email) return null;
    const all = (await env.SOTD.get('members', 'json')) || {}, owner = await env.SOTD.get('owner');
    if (!owner || !all[owner]) return null;
    const apps = await spApps(env), n = apps.indexOf(rec.cid || CLIENT_ID) + 1, name = (all[id] && all[id].name) || 'A member';
    const msg = { title: name + ' is waiting to be added to Spotify app ' + (n || '?'),
      body: 'Add ' + name + ' (' + email + ') in Spotify’s dashboard: app ' + (n || '?') + ' › User Management › Add user. Tap for the email to copy and the link to that page, or Let Claude add them.',
      url: SITE_SONGS + 'admin.html#members', tag: 'sotd-spadd-' + id };
    await pushAll(env, msg, [owner], { kind: 'member', via: { t: 'spotify', name } });
    const at = { email, at: Date.now(), ok: true, status: 200 };
    await env.SOTD.put('spask:' + id, email, { expirationTtl: 30 * 86400 });
    await env.SOTD.put('spaskat:' + id, JSON.stringify(at), { expirationTtl: 30 * 86400 });
    return at;
  } catch (e) { console.log('spAskAdd: ' + (e && e.message)); return null; }
}
async function spAddedNote(env, id) {   // the member, added, now connected: John told (his inbox and phone)
  try {
    if (!(await env.SOTD.get('spask:' + id))) return;
    await env.SOTD.delete('spask:' + id); await env.SOTD.delete('spaskat:' + id);
    const all = (await env.SOTD.get('members', 'json')) || {}, owner = await env.SOTD.get('owner');
    if (!owner || !all[owner] || !all[id]) return;
    await pushAll(env, { title: all[id].name + ' is connected to Spotify', body: 'Their songs play through their own Spotify now.', url: SITE_SONGS + 'admin.html#members', tag: 'sotd-spadd-' + id }, [owner], { kind: 'member', via: { t: 'spotify', name: all[id].name } });
  } catch (e) { console.log('spAddedNote: ' + (e && e.message)); }
}
async function spCall(env, id, rec, method, path, body) {   // { status, json, why }: why 'premium', 'nodevice', 'blocked', or ''
  const tok = await spToken(env, id, rec);
  const r = await fetch(API + path, { method, headers: Object.assign({ Authorization: 'Bearer ' + tok }, body ? { 'Content-Type': 'application/json' } : {}), body: body ? JSON.stringify(body) : undefined });
  const text = await r.text(); let json = null; try { json = text ? JSON.parse(text) : null; } catch (e) {}
  const msg = String((json && json.error && (json.error.message || json.error.reason)) || ''), reason = String((json && json.error && json.error.reason) || '');
  let why = '';
  if (r.status === 403 && (/PREMIUM/i.test(reason) || /premium/i.test(msg))) why = 'premium';
  else if (r.status === 403 && /regist|dashboard/i.test(msg)) why = 'blocked';
  else if (r.status === 404 && (/NO_ACTIVE_DEVICE/.test(reason) || /device/i.test(msg))) why = 'nodevice';
  else if (r.status === 429) { why = 'busy'; if (/QUOTA_EXCEEDED/.test(reason) || /QUOTA_EXCEEDED/.test(text)) await env.SOTD.put('spquota', String(Date.now()), { expirationTtl: 86400 }).catch(() => {}); }   // (the apps' shared allowance used up: noted for Admin)
  if (why === 'blocked' && !rec.blocked) { rec.blocked = true; await env.SOTD.put('spm:' + id, JSON.stringify(rec)); await spAskAdd(env, id, rec); }
  if (r.ok && rec.blocked && path === '/me') { delete rec.blocked; await spAddedNote(env, id); }
  return { status: r.status, json, why, msg };
}
// their Spotify devices: Spotify's list, with the one playing now added when the list leaves it out (an iPhone's Spotify
// often isn't in the list though it's playing: John, 2 Nov 2026); each { id, name, type, active, restricted, vol, canVol }
async function spDevices(env, id, rec) {
  const d = await spCall(env, id, rec, 'GET', '/me/player/devices');
  if (d.why) return { why: d.why, msg: d.msg, list: [] };
  const raw = ((d.json && d.json.devices) || []).slice();
  const p = await spCall(env, id, rec, 'GET', '/me/player').catch(() => null), now = p && p.status === 200 && p.json && p.json.device;
  if (now && now.id && p.json.is_playing && !raw.some(x => x.id === now.id)) raw.unshift(Object.assign({}, now, { is_active: true }));   // (only while it's really playing: an iPhone asleep in the background stays the 'last device' but won't play)
  return { list: raw.filter(x => x.id).map(x => ({ id: x.id, name: x.name, type: x.type, active: !!x.is_active, restricted: !!x.is_restricted, vol: typeof x.volume_percent === 'number' ? x.volume_percent : null, canVol: x.supports_volume !== false && typeof x.volume_percent === 'number' })) };
}
const SP_WHEN = ['all', 'nomp3', 'nofile'], SP_KINDS = ['phone', 'tablet', 'computer'];
async function memberSpotify(request, url, env, answer) {
  const who = await memberWho(request, env);
  if (!who) return answer(401, { error: 'Not a member (any more).' });
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const b = await request.json().catch(() => ({})), key = 'spm:' + who.id;
  let rec = await env.SOTD.get(key, 'json');
  const pub = () => rec ? { linked: true, on: rec.on !== false, when: SP_WHEN.includes(rec.when) ? rec.when : 'all', kinds: Array.isArray(rec.kinds) ? rec.kinds : SP_KINDS, blocked: !!rec.blocked, gone: !!rec.gone, name: rec.name || '', product: rec.product || '', email: rec.email || rec.wemail || '', cid: rec.cid || CLIENT_ID } : { linked: false };   // (cid: their Spotify app, for the iPhone app's toolkit)
  if (b.op === 'start') {
    if (!await spAppFor(env, who.id)) return answer(409, { error: 'Spotify playback is full for now: Spotify lets each app have 5 people. Songs play as previews.', full: true });
    const n = b64url(crypto.getRandomValues(new Uint8Array(24)));
    const back = /^https:\/\/cooperking\.github\.io\//.test(String(b.back || '')) ? String(b.back) : SITE_SONGS;
    await env.SOTD.put('spn:' + n, JSON.stringify({ id: who.id, back }), { expirationTtl: 900 });
    return answer(200, { url: new URL(request.url).origin + '/splink?n=' + n });
  }
  if (b.op === 'log') {   // the member's phone's steps with Spotify (John, 2 Nov 2026: to see them in Admin when the page can't be reached), the last 150
    const lines = (Array.isArray(b.lines) ? b.lines : []).map(x => String(x).slice(0, 300)).slice(-60);
    if (!lines.length) return answer(200, { ok: true });
    const k = 'splog:' + who.id, had = (await env.SOTD.get(k, 'json')) || [];
    await env.SOTD.put(k, JSON.stringify(had.concat(lines).slice(-150)), { expirationTtl: 14 * 86400 });
  if (env.MEDIA && (await env.SOTD.get('owner')) === who.id) await env.MEDIA.put('diag/splog-john.txt', had.concat(lines).slice(-150).join('\n'), { httpMetadata: { contentType: 'text/plain; charset=utf-8' } }).catch(() => null);   // (John's, at once, for looking into it)
    return answer(200, { ok: true });
  }
  if (!rec) return answer(200, b.op === 'status' ? { linked: false, full: !await spAppFor(env, who.id) } : pub());
  if (b.op === 'status') {
    if (rec.blocked || !rec.product) {   // (perhaps John has added them since: asked again)
      const me = await spCall(env, who.id, rec, 'GET', '/me').catch(() => null);
      if (me && me.status === 200 && me.json) { Object.assign(rec, { sid: me.json.id, name: me.json.display_name || me.json.id, email: me.json.email || rec.email || '', product: me.json.product || '' }); if (rec.blocked) await spAddedNote(env, who.id); delete rec.blocked; await env.SOTD.put(key, JSON.stringify(rec)); }
    }
    return answer(200, pub());
  }
  if (b.op === 'pref') {   // on or off; and when (every song, or only those with no MP3, or no MP3 and no video) and on which kinds of device (John, 4 Nov 2026)
    if ('on' in b) rec.on = !!b.on;
    if (SP_WHEN.includes(b.when)) rec.when = b.when;
    if (Array.isArray(b.kinds)) rec.kinds = SP_KINDS.filter(k => b.kinds.includes(k));
    await env.SOTD.put(key, JSON.stringify(rec)); return answer(200, pub());
  }
  if (b.op === 'email') { const e = String(b.email || '').trim().slice(0, 120); if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e)) return answer(400, { error: 'That isn’t an email address.' }); rec.wemail = e; await env.SOTD.put(key, JSON.stringify(rec)); if (rec.blocked) await spAskAdd(env, who.id, rec); return answer(200, pub()); }
  if (b.op === 'off') { await env.SOTD.delete(key); return answer(200, { linked: false }); }
  try {
    if (b.op === 'devices') { const d = await spDevices(env, who.id, rec); return answer(d.why ? 409 : 200, d.why ? { why: d.why, error: d.msg } : { devices: d.list }); }
    if (b.op === 'volume') { const v = Math.max(0, Math.min(100, b.vol | 0)); const r = await spCall(env, who.id, rec, 'PUT', '/me/player/volume?volume_percent=' + v + (b.device ? '&device_id=' + encodeURIComponent(String(b.device)) : '')); return answer(r.status < 300 ? 200 : 409, r.status < 300 ? { ok: true, vol: v } : { why: r.why || 'other', error: r.msg || 'That device won’t let its volume be set from here.' }); }
    if (b.op === 'play') {   // these songs, from the first, on the device chosen (else the one playing, else this phone, else any)
      const uris = (Array.isArray(b.tracks) ? b.tracks : []).map(String).filter(t => ID_RE.test(t)).slice(0, 100).map(t => 'spotify:track:' + t);
      if (!uris.length) return answer(400, { error: 'Which songs?' });
      // which device (John, 2 Nov 2026: it played on a Chrome web player he couldn't see; "can the device it's on be chosen
      // by default"): the one asked for, else the kind they're on (prefer: Smartphone on a phone, Computer on a computer),
      // the active one of that kind first; none of that kind open: asked, with what is (notHere), never played elsewhere unasked
      const d = await spDevices(env, who.id, rec);
      if (d.why) return answer(409, { why: d.why, error: d.msg });
      const list = d.list.filter(x => !x.restricted), kind = String(b.prefer || ''), brief = d.list;
      let pick = b.device ? list.find(x => x.id === String(b.device)) : null;
      if (!pick && b.device) {   // the one that played a moment ago, left out of Spotify's list (an iPhone, between songs): tried as it is
        const t = await spCall(env, who.id, rec, 'PUT', '/me/player/play?device_id=' + encodeURIComponent(String(b.device)), Object.assign({ uris }, b.ms ? { position_ms: Math.max(0, b.ms | 0) } : {}));
        if (t.status < 300) return answer(200, { ok: true, device: String(b.device), name: String(b.dname || ''), type: String(b.dtype || '') });
        if (b.picked) {   // a speaker they chose, gone from Spotify's list: woken (handed over to), else said, never played somewhere else instead
          await spCall(env, who.id, rec, 'PUT', '/me/player', { device_ids: [String(b.device)], play: false }).catch(() => null);
          await new Promise(res => setTimeout(res, 1500));
          const t2 = await spCall(env, who.id, rec, 'PUT', '/me/player/play?device_id=' + encodeURIComponent(String(b.device)), Object.assign({ uris }, b.ms ? { position_ms: Math.max(0, b.ms | 0) } : {}));
          if (t2.status < 300) return answer(200, { ok: true, device: String(b.device), name: String(b.dname || ''), type: String(b.dtype || '') });
          return answer(409, { why: 'pickgone', error: 'Spotify can’t find the speaker you chose' + (b.dname ? ' (' + String(b.dname).slice(0, 60) + ')' : '') + ': is it on?' });
        }
      }
      if (!pick && kind) pick = list.find(x => x.type === kind && x.active) || list.find(x => x.type === kind);
      if (!pick && !kind) pick = list.find(x => x.active) || list[0];
      if (!pick) return answer(409, list.length ? { why: 'notHere', devices: brief, error: 'Spotify isn’t open on this device.' } : { why: 'nodevice', error: 'Spotify isn’t open on any of your devices.' });
      const body0 = Object.assign({ uris }, b.ms ? { position_ms: Math.max(0, b.ms | 0) } : {});
      let r = await spCall(env, who.id, rec, 'PUT', '/me/player/play?device_id=' + encodeURIComponent(pick.id), body0);
      if (r.status === 404) {   // a speaker or group Spotify has let go to sleep ("Device not found": John, 5 Nov 2026, the Everywhere speaker): handed over to first, as Spotify's own app does, then asked again
        await spCall(env, who.id, rec, 'PUT', '/me/player', { device_ids: [pick.id], play: false }).catch(() => null);
        await new Promise(res => setTimeout(res, 1500));
        r = await spCall(env, who.id, rec, 'PUT', '/me/player/play?device_id=' + encodeURIComponent(pick.id), body0);
      }
      if (r.status >= 300) return answer(409, { why: r.why || 'other', error: (r.msg || 'Spotify answered ' + r.status) + (r.status === 404 ? ' (' + pick.name + ')' : '') });
      return answer(200, { ok: true, device: pick.id, name: pick.name, type: pick.type });
    }
    const simple = { pause: ['PUT', '/me/player/pause'], resume: ['PUT', '/me/player/play'], next: ['POST', '/me/player/next'], prev: ['POST', '/me/player/previous'] };
    if (simple[b.op]) { const r = await spCall(env, who.id, rec, simple[b.op][0], simple[b.op][1]); return answer(r.status < 300 ? 200 : 409, r.status < 300 ? { ok: true } : { why: r.why || 'other', error: r.msg }); }
    if (b.op === 'transfer') {   // what's playing, moved to another of their devices
      const r = await spCall(env, who.id, rec, 'PUT', '/me/player', { device_ids: [String(b.device || '')], play: true });
      return answer(r.status < 300 ? 200 : 409, r.status < 300 ? { ok: true } : { why: r.why || 'other', error: r.msg });
    }
    if (b.op === 'seek') { const r = await spCall(env, who.id, rec, 'PUT', '/me/player/seek?position_ms=' + Math.max(0, b.ms | 0)); return answer(r.status < 300 ? 200 : 409, r.status < 300 ? { ok: true } : { why: r.why || 'other', error: r.msg }); }
    if (b.op === 'state') {
      const r = await spCall(env, who.id, rec, 'GET', '/me/player?market=from_token');   // (market: a song Spotify swapped for its own country's copy says which it was, linked_from)
      if (r.status === 204 || !r.json) return answer(200, { none: true });
      if (r.status !== 200) return answer(409, { why: r.why || 'other', error: r.msg });
      const it = r.json.item || {};
      const dv = r.json.device || {};
      return answer(200, { track: it.id || '', lf: (it.linked_from && it.linked_from.id) || undefined, pos: r.json.progress_ms || 0, dur: it.duration_ms || 0, playing: !!r.json.is_playing, device: dv.name || '', dtype: dv.type || '', did: dv.id || '',
        vol: typeof dv.volume_percent === 'number' ? dv.volume_percent : null, canVol: dv.supports_volume !== false && typeof dv.volume_percent === 'number' });
    }
  } catch (e) { return answer(e instanceof HttpError ? e.status : 502, { error: String(e && e.message || e) }); }
  return answer(400, { error: 'What should it do?' });
}
// /members/spnames?ids=a,b: Spotify users' names, for Import from Spotify's "By ..." (John, 1 Nov 2026: "show who the
// playlist is by originally too as it shows all playlists followed as well as your own"): Exportify's files say which
// Spotify account added each song (its id, spotify:user:<id>), not the name. From each user's public page (its title),
// kept 30 days (KV spname:<id>, '' none); 20 at a time, for members.
async function memberSpNames(request, url, env, answer) {
  if (!await memberWho(request, env)) return answer(401, { error: 'Not a member (any more).' });
  const ids = String(url.searchParams.get('ids') || '').split(',').map(x => x.trim()).filter(x => /^[\w.\-]{1,64}$/.test(x)).slice(0, 20), out = {};
  await Promise.all(ids.map(async id => {
    const k = 'spname:' + id, had = await env.SOTD.get(k);
    if (had !== null) { out[id] = had; return; }
    let n = '';
    const r = await fetch('https://open.spotify.com/user/' + encodeURIComponent(id), { headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36', 'Accept-Language': 'en-GB' } }).catch(() => null);
    if (r && r.ok) {
      const h = await r.text(), m = /<meta property="og:title" content="([^"]{1,120})"/.exec(h) || /<title>([^<]{1,120})<\/title>/.exec(h);
      n = m ? m[1].replace(/&amp;/g, '&').replace(/&#x27;|&#39;/g, '’').replace(/&quot;/g, '"').replace(/\s*\|\s*Spotify\s*$/i, '').trim() : '';
      if (/^(spotify – web player|spotify)$/i.test(n)) n = '';
    } else if (!r || r.status >= 500 || r.status === 429) { out[id] = ''; return; }   // (not kept: asked again next time)
    out[id] = n.slice(0, 80);
    await kvSoft(env, k, out[id], { expirationTtl: 30 * 86400 });
  }));
  return answer(200, { names: out });
}
// the old way in, a Spotify playlist John's account joined as a collaborator and read hourly (src 'sp'), is gone (John,
// 3 Nov 2026: "We should get rid of that method completely ... should look the same as if it was completely the member's
// playlist"): any left become their owner's own, as an Exportify import is (src 'csv'), their songs as they are now,
// updated from an Exportify download like the rest. Spotify isn't read for them any more.
function uplRetireSp(U) {
  let n = 0;
  for (const r of Object.values(U)) {
    if (r.src === 'mpl' || r.src === 'csv') continue;
    r.src = 'csv'; r.st = 'ok'; delete r.link; delete r.err; delete r.tried; delete r.chw; n++;
  }
  return n;
}
async function uplAll(env) {
  const U = (await env.SOTD.get('upls', 'json')) || {};
  if (uplRetireSp(U)) await env.SOTD.put('upls', JSON.stringify(U));
  return U;
}
async function uplSongs(env, pid) { return (await env.SOTD.get('upl:' + pid, 'json')) || { songs: [], rel: {} }; }
// a saved playlist made a calendar or shared playlist (src 'mpl', pl: the saved playlist's id): its songs follow it.
// New ones are added as they're saved (a: then); one taken out is kept, deleted (del), so its day stays empty; one put
// back is out again on its day; data.order is the saved playlist's order (a shared playlist's).
async function uplTrackMeta(env, ids) {   // each track's fields, from John's songs or members' playlists
  const want = new Set(ids), out = {};
  if (!want.size) return out;
  const pick = x => { if (x && want.has(x.id) && !out[x.id]) { const y = {}; for (const k of SONG_FIELDS) if (k !== 'a' && x[k] !== undefined) y[k] = x[k]; out[x.id] = y; } };
  (((await env.SOTD.get('songsLast', 'json')) || {}).songs || []).forEach(pick);
  for (const pid of Object.keys(await uplAll(env))) {
    if (Object.keys(out).length >= want.size) break;
    ((await uplSongs(env, pid)).songs || []).forEach(pick);
  }
  return out;
}
async function uplListSync(env, pid, rec, l, now) {   // its songs as the saved playlist has them now (rec.chg: changed, for the site job)
  const data = await uplSongs(env, pid), want = (l.tracks || []).map(String);
  data.songs = data.songs || []; data.del = data.del || {};
  const have = new Map(data.songs.map(x => [x.id, x])), meta = await uplTrackMeta(env, want.filter(t => !have.has(t)));
  let changed = false;
  for (const t of want) {
    const x = have.get(t);
    if (x) { if (data.del[t] === x.a) { delete data.del[t]; changed = true; } continue; }
    if (!meta[t]) continue;
    const y = Object.assign(meta[t], { a: new Date(now).toISOString() });
    data.songs.push(y); have.set(t, y); changed = true;
  }
  const inList = new Set(want);
  const off = [];
  for (const x of data.songs) if (!inList.has(x.id) && data.del[x.id] !== x.a) { data.del[x.id] = x.a; off.push(x.id); changed = true; }
  if (off.length) await uplFilesGone(env, pid, data, off);   // (their files too, unless saved to a playlist elsewhere)
  const order = want.filter(t => have.has(t));
  if (JSON.stringify(order) !== JSON.stringify(data.order || [])) { data.order = order; changed = true; }
  if (changed) { data.at = new Date(now).toISOString(); await env.SOTD.put('upl:' + pid, JSON.stringify(data)); rec.chg = 1; }
  Object.assign(rec, { n: String(l.name || '').slice(0, 80), cnt: order.length, read: now });
  return changed;
}
async function uplListSaved(env, member, l, gone) {   // a saved playlist saved (or deleted: gone): its calendar with it
  const U = await uplAll(env), e = Object.entries(U).find(([, r]) => r.o === member && r.src === 'mpl' && r.pl === l.id);
  if (!e) return;
  if (gone) { await uplDrop(env, [e[0]]); return; }
  await uplListSync(env, e[0], e[1], l, Date.now());
  await uplPut(env, U);
}
// John's own choices for a song on members' playlists (the Songs tab): its video (v: a YouTube id, '' none), moods (m)
// and genres (g), on every playlist with it, over its owner's (KV uplJohn { <track>: { v, m, g } })
async function uplJohnAll(env) { return (await env.SOTD.get('uplJohn', 'json')) || {}; }
async function uplTracks(env) {   // every track on members' playlists (for Open in Spotify)
  const U = await uplAll(env), out = [];
  for (const pid of Object.keys(U)) for (const x of (await uplSongs(env, pid)).songs) out.push(x.id);
  return out;
}
function uplSrc(r) { return r.src === 'mpl' || r.src === 'csv' ? r.src : 'sp'; }   // sp: read from Spotify; mpl: a saved playlist's; csv: from an Exportify file its owner uploads
function uplWaiting(rec, data, x) { return rec.src !== 'mpl' && Date.parse(x.a) > (rec.base || 0) && !(data.rel || {})[x.id]; }   // (a saved playlist's: out as its owner adds them)
function uplGone(data, x) { const d = (data.del || {})[x.id]; return !!d && d === x.a; }   // taken off by its owner (that adding of it): its day left empty
function uplName(all, rec) { return rec.title || rec.n || ((all[rec.o] && all[rec.o].name) || 'A member') + '’s playlist'; }   // (title: its owner's; n: Spotify's)
function uplTitle(all, rec) { return rec.mode === 'list' ? uplName(all, rec) : ((all[rec.o] && all[rec.o].name) || 'A member') + '’s Song of the Day'; }   // what the calendar calls it
const UPL_SHARES = ['public', 'members', 'some', 'private'];
function uplShareOf(rec) { return UPL_SHARES.includes(rec.share) ? rec.share : 'members'; }   // who its owner shares it with: anyone, every member, the members in to, or no one
// collaborators (John, 3 Nov 2026: "Can we make playlists collaborative, so that different members can edit"): rec.cw,
// the members its owner lets edit it. They see it whoever it's shared with, and edit its songs as its owner does (each
// song's details, picture, video, files and facts; moving, taking off and signing off songs); its name, set-up, who
// sees it, who edits it and removing it stay its owner's. Their files count against its owner's allowance.
function uplCo(rec, id) { return !!(rec && id && (rec.cw || []).includes(id)); }
const UPL_RETRY_FOR = 6 * 3600e3;   // (a song John asked to be looked for again: given to the job as retry for six hours)
const UPL_CO_OPS = ['skip', 'unskip', 'jfdrop', 'edit', 'facts', 'askfacts', 'order', 'signoff', 'signoffall', 'del', 'move', 'unmove', 'place', 'dayorder', 'dltry'];
function uplCan(all, viewer, rec) {   // whether someone sees this playlist (viewer: a member's id, or none): theirs, or another's that's readable, shared with them and not kept apart
  if (!rec || !all[rec.o]) return false;
  if (viewer && rec.o === viewer) return true;
  if (rec.st !== 'ok') return false;
  if (viewer && all[viewer] && uplCo(rec, viewer) && !apart(all, viewer, rec.o)) return true;
  const sh = uplShareOf(rec);
  if (!viewer || !all[viewer]) return sh === 'public';
  if (apart(all, viewer, rec.o)) return false;
  return sh === 'public' || sh === 'members' || (sh === 'some' && (rec.to || []).includes(viewer));
}
function uplList(all, viewer, U, boss) {   // the playlists a member can switch to (/members/me pls)   // (boss: John, the site's owner: each one's set-up as its owner has it, to edit as they can: John, 7 Nov 2026)
  return Object.entries(U).filter(([, r]) => uplCan(all, viewer, r))
    .map(([pid, r]) => Object.assign({ id: pid, n: uplName(all, r), t: uplTitle(all, r), o: all[r.o].name, mode: r.mode, cnt: r.cnt || 0, sp: r.sp, src: uplSrc(r), sh: uplShareOf(r) }, r.yt ? { yt: r.yt } : {}, r.pic ? { pic: LIVE_ORIGIN + '/up/' + pid + '.jpg?v=' + r.pic } : {}, r.pcov ? { pst: 'cov' } : r.pst ? { pst: r.pst } : {}, r.cvt ? { cvt: r.cvt } : {}, r.cvb ? { cvb: r.cvb } : {}, r.art ? { art: { k: r.art.k, url: LIVE_ORIGIN + '/up/' + pid + '-art.jpg?v=' + r.art.v } } : {}, uplCo(r, viewer) ? { co: true } : {}, r.o === viewer || boss ? { mine: r.o === viewer || undefined, boss: r.o !== viewer || undefined, auto: !!r.auto, spl: r.spid ? 1 : undefined, cw: r.cw || [], pl: r.pl || '', fk: r.fk || '', adds: r.adds || [], moved: r.dd ? Object.keys(r.dd).length : 0, st: r.st, err: r.err || '', sn: r.n || '', to: uplShareOf(r) === 'some' ? r.to || [] : [], dates: r.mode === 'list' ? null : Object.assign({ how: 'added' }, r.dates || {}), send: uplSendOf(r) } : {}))
    .sort((a, b) => (b.mine ? 2 : b.co ? 1 : 0) - (a.mine ? 2 : a.co ? 1 : 0) || a.o.localeCompare(b.o) || a.n.localeCompare(b.n));
}
async function uplMine(env, pls, U) {   // their own playlists' songs waiting for them to sign off (wait: the newest 20, oldest first)
  for (const p of pls) {
    if (!(p.mine || p.co) || p.st !== 'ok' || !U[p.id]) continue;
    const rec = U[p.id], data = await uplSongs(env, p.id), days = uplDays(data.songs || [], rec);
    const w = (data.songs || []).map((x, i) => uplWaiting(rec, data, x) && !uplGone(data, x) ? { id: x.id, title: x.title, artist: x.artist, d: days[i].d, img: x.img || '' } : null).filter(Boolean);
    p.waitN = w.length; p.wait = w.slice(-50);   // (how many in all, and the newest 50 to show: John, 3 Nov 2026)
  }
  return pls;
}
function uplAdmin(all, U) {   // the Members tab: every playlist, whose, and whether John has joined it
  return Object.entries(U).map(([pid, r]) => ({ id: pid, n: uplName(all, r), o: (all[r.o] && all[r.o].name) || '?', oid: r.o, mode: r.mode, src: uplSrc(r), st: r.st, err: r.err || '', link: r.link || '',
    sp: r.sp, at: r.at, read: r.read || null, cnt: r.cnt || 0, sh: uplShareOf(r), shn: uplShareOf(r) === 'some' ? (r.to || []).length : 0, dates: r.mode === 'list' ? null : Object.assign({ how: 'added' }, r.dates || {}) })).sort((a, b) => (a.st === 'join' ? 0 : 1) - (b.st === 'join' ? 0 : 1) || b.at - a.at);
}
// the Members tab, as the calendar's Calendars page shows them (John, 1 Nov 2026: "Can my admin view of members playlists
// be like the picture view from the calendar"): each one's picture (pic: its owner's, else cv: its newest four covers),
// and, while one is still coming in (changed in the last two days, as its owner's progress shows it), its progress
// ("a similar progress only admin for currently importing playlists"): prog { n, pq, p, pn, v, vn, fa, fv, ch }
async function uplAdminFull(env, all, U) {
  const list = uplAdmin(all, U), now = Date.now();
  for (const u of list) {
    const r = U[u.id]; if (!r) continue;
    if (r.pic) u.pic = LIVE_ORIGIN + '/up/' + u.id + '.jpg?v=' + r.pic;
    const data = await uplSongs(env, u.id), cv = [];
    for (const x of (data.songs || []).slice().reverse()) { if (cv.length >= 4) break; if (uplGone(data, x)) continue; const c = keptCover(String(x.img || '')); if (c && !cv.includes(c)) cv.push(c); }
    u.cv = cv;
    if (r.st === 'ok' && all[r.o] && now - (r.ch || r.at || 0) < 2 * 864e5) {
      const v = await uplView(env, all, r.o, u.id, r).catch(() => null);
      if (v && v.prog) { const g = v.prog; u.prog = { n: g.n, pq: g.pq, p: g.p, pn: g.pn, v: g.v, vn: g.vn, fa: g.fa, fv: g.fv, ch: g.ch }; }
      u.chg = Object.assign({ at: r.ch || 0, fresh: now - (Number(r.at) || Date.parse(r.at) || 0) < 2 * 864e5 }, r.chw || {});   // (a first import, or what changed since)
    }
  }
  return list;
}
async function uplPut(env, U) { await env.SOTD.put('upls', JSON.stringify(U)); }
async function uplDrop(env, pids) {
  const U = await uplAll(env);
  const yt = pids.map(pid => U[pid] && U[pid].yt).filter(Boolean);
  if (yt.length) { const q = (await env.SOTD.get('ytdrop', 'json')) || []; await env.SOTD.put('ytdrop', JSON.stringify([...new Set(q.concat(yt))])); }   // (its YouTube copy: deleted by the site job, youtube_upl_drop.py)
  for (const pid of pids) {
    delete U[pid]; await env.SOTD.delete('upl:' + pid); await env.SOTD.delete('ufacts:' + pid);
    if (env.MEDIA) await env.MEDIA.delete('up/' + pid + '.jpg').catch(() => {});   // (its picture)
    if (env.MEDIA) for (const k of ['ua', 'uv', 'uc']) {   // its owner's own files and covers
      const l = await env.MEDIA.list({ prefix: k + '/' + pid + '/' }).catch(() => ({ objects: [] }));
      for (const o of l.objects || []) await env.MEDIA.delete(o.key);
    }
  }
  await uplPut(env, U);
}
async function uplDropOwner(env, member) {
  if (env.MEDIA) await env.MEDIA.delete('imp/' + member + '.json').catch(() => {});   // (their kept Exportify download)
  const U = await uplAll(env), mine = Object.keys(U).filter(pid => U[pid].o === member);
  if (mine.length) await uplDrop(env, mine);
}
function uplAddDays(iso, k) { const d = new Date(iso + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + k); return d.toISOString().slice(0, 10); }
function uplDays(songs, rec) {   // each song's day: one its owner moved it to (dd), else a calendar's chosen way (dates: a song a day from its anchor), else by when it was added
  const out = uplDaysBy(songs, rec), dd = rec && rec.mode !== 'list' && rec.dd;
  if (dd) for (const x of out) if (dd[x.id]) x.d = dd[x.id];
  return out;
}
function uplDaysBy(songs, rec) {
  const dt = rec && rec.mode !== 'list' && rec.dates;
  if (dt && (dt.how === 'fwd' || dt.how === 'back') && songs.length) {
    let i = songs.findIndex(x => x.id === dt.at);
    if (i < 0) i = dt.how === 'fwd' ? 0 : Math.min(songs.length, dt.n || songs.length) - 1;
    return songs.map((x, k) => Object.assign({}, x, { d: uplAddDays(dt.day, k - i) }));
  }
  // its UK added day: songs added the same day share it (John, 1 Nov 2026: "can we have the calendar icon split to show
  // multiple songs, so they keep their original day"; until then each earlier one went back a day)
  // a song Spotify gives no real date (added before it kept them: 1 January 1970): no day, in the calendar's Songs with no date (John, 3 Nov 2026: "Definitely don't put something on 1 January 1970")
  return songs.map(x => Object.assign({}, x, { d: uplDated(x) ? londonDay(x.a) : '' }));
}
let spWhoMem = null;
async function spWho(env, all) {   // Spotify user id → member's name (theirs connected here, and John's own account), kept a few minutes
  if (spWhoMem && Date.now() - spWhoMem.at < 10 * 60e3) return spWhoMem.map;
  const map = {}, ids = {}, owner = await env.SOTD.get('owner');
  if (owner && all[owner]) { map[OWNER_ID] = all[owner].name; ids[OWNER_ID] = owner; }
  for (const [id, m] of Object.entries(all)) if (m && m.spu && !map[m.spu]) { map[m.spu] = m.name; ids[m.spu] = id; }   // (the Spotify usernames John gave on the Members tab: known without them connecting: John, 7 Nov 2026)
  let cursor;
  do { const l = await env.SOTD.list({ prefix: 'spm:', cursor }); for (const k of l.keys) { const id = k.name.slice(4), r = await env.SOTD.get(k.name, 'json'); if (r && r.sid && all[id]) { map[r.sid] = all[id].name; ids[r.sid] = id; } } cursor = l.list_complete ? null : l.cursor; } while (cursor);
  spWhoMem = { at: Date.now(), map, ids };
  return map;
}
async function spWhoIds(env, all) { await spWho(env, all); return spWhoMem.ids || {}; }   // Spotify user id → member id
function uplDated(x) { const t = Date.parse(x && x.a); return !isNaN(t) && t >= Date.UTC(2000, 0, 1); }
async function uplRead(env, pid, rec, U) {   // reads it from Spotify; keeps what changed; says whether it's readable
  const was = rec.st, now = Date.now();
  let items;
  try {
    items = await playlistItems(await getToken(env), rec.sp);
  } catch (e) {
    const msg = String(e && e.message || e);
    if (/Spotify (403|404)/.test(msg)) { rec.st = was === 'ok' ? 'err' : 'join'; rec.err = was === 'ok' ? 'John’s Spotify account can’t read it any more (collaboration turned off, or the playlist deleted).' : ''; }
    else rec.err = msg.slice(0, 160);
    rec.tried = now;
    U[pid] = rec; await uplPut(env, U);
    return false;
  }
  return uplTake(env, pid, rec, U, items, was, now);
}
// a song fully in (out, not waiting for its owner) that's gone from Spotify's playlist, or from a fresh Exportify file,
// stays where it was (sx: when it went), its day and files as they were (John, 1 Nov 2026: "Once a song is fully in a
// calendar or playlist, deleting the song from Spotify should never have any effect on it"). Its owner takes it off
// with Delete. Ones still waiting for them go.
function uplKeepGone(rec, data, items, now) {
  const old = data.songs || [], inNew = new Set(items.map(x => x.id));
  const keep = new Set(old.filter(x => !inNew.has(x.id) && !uplGone(data, x) && !uplWaiting(rec, data, x)).map(x => x.id));
  if (!keep.size) return items;
  const after = new Map(); let prev = null;   // each one after the song before it that's still there (the start: null)
  for (const x of old) {
    if (inNew.has(x.id)) prev = x.id;
    else if (keep.has(x.id)) { if (!after.has(prev)) after.set(prev, []); after.get(prev).push(x.sx ? x : Object.assign({}, x, { sx: new Date(now).toISOString() })); }
  }
  const out = (after.get(null) || []).slice();
  for (const y of items) { out.push(y); if (after.has(y.id)) out.push(...after.get(y.id)); }
  return out;
}
async function uplTake(env, pid, rec, U, items, was, now, quiet) {   // its songs as read (or uploaded): keeps what changed (quiet: its owner is there, no message)
  const data = await uplSongs(env, pid), had = new Set((data.songs || []).map(x => x.id));
  // a song already on it keeps the day it came in with: Spotify's added date for it can change (taken off and put back,
  // a fresh export), and an update or John's own Spotify read never moves a song (John, 3 Nov 2026: "If we edit a playlist
  // date ... I don't want it automatically changed back via an import or my own spotify checks"). Days moved by hand (dd)
  // and the calendar's own setup (dates) are its owner's and no update touches them.
  const keptA = new Map((data.songs || []).map(x => [x.id, x.a]));   // (not one its owner took off: put back on Spotify, it's back on its new day, as before)
  items = items.map(x => keptA.get(x.id) && keptA.get(x.id) !== x.a && !(data.del || {})[x.id] ? Object.assign({}, x, { a: keptA.get(x.id) }) : x);
  items = uplKeepGone(rec, data, items, now);
  const fresh = items.filter(x => !had.has(x.id) && Date.parse(x.a) > (rec.base || 0) && !(data.rel || {})[x.id]);
  const bare = l => JSON.stringify((l || []).map(x => { const y = Object.assign({}, x); delete y.rd; return y; }));
  const changed = bare(items) !== bare(data.songs);
  if (!changed && JSON.stringify(items) !== JSON.stringify(data.songs)) { data.songs = items; await env.SOTD.put('upl:' + pid, JSON.stringify(data)); }   // (only the release dates new: kept quietly, nothing to look at again)
  if (changed) {
    const keep = new Set(items.map(x => x.id)), off = (data.songs || []).map(x => x.id).filter(t => !keep.has(t));
    data.songs = items; data.at = new Date(now).toISOString(); data.rel = data.rel || {};
    if (off.length) await uplFilesGone(env, pid, data, off);   // (songs no longer on it: their files too, unless saved to a playlist elsewhere)
    await env.SOTD.put('upl:' + pid, JSON.stringify(data));
    rec.ch = now;   // (the site job comes back hourly for a while: uplLook)
    rec.chw = { k: rec.src === 'csv' ? 'file' : rec.src === 'mpl' ? 'saved' : 'spotify', add: items.filter(x => !had.has(x.id)).length, off: off.length };   // what changed, for John's tiles (John, 3 Nov 2026: "it suddenly said it was importing again and I'm not sure why")
  }
  Object.assign(rec, { st: 'ok', err: '', read: now, tried: now, cnt: items.filter(x => !uplGone(data, x)).length });
  if (rec.dates && !rec.dates.at && items.length) Object.assign(rec.dates, rec.dates.how === 'fwd' ? { at: items[0].id, n: 1 } : { at: items[items.length - 1].id, n: items.length });   // (chosen before it could be read)
  U[pid] = rec; await uplPut(env, U);
  await fillPreviews(env, items, 6).catch(() => null);
  await tagNewSongs(env, items, 3).catch(() => {});
  if (was === 'ok' && fresh.length && !quiet) await uplTellOwner(env, pid, rec, fresh, uplDays(items, rec));
  if (changed && items.length) await startJob(env, null, null, 0, null, true).catch(() => {});   // the site job: their songs' videos, moods, years and YouTube copy
  return { fresh: fresh.length };
}
async function uplTellOwner(env, pid, rec, fresh, days) {   // a new song on their playlist: to the owner's devices, waiting for their sign-off
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {}, ids = Object.keys(subs).filter(k => subs[k].m === rec.o);
  if (!ids.length) return;
  const x = fresh[fresh.length - 1], d = ((days || []).find(y => y.id === x.id) || uplDays([x])[0]).d, slug = d.slice(8, 10) + '-' + d.slice(5, 7) + '-' + d.slice(0, 4);
  const unread = ((await env.SOTD.get('inbox:' + rec.o, 'json')) || []).filter(y => !y.r).length;
  const msg = { title: (fresh.length > 1 ? fresh.length + ' new songs wait' : '“' + x.title + '” waits') + ' for you to sign off', body: rec.mode === 'list' ? rec.n || 'Your playlist' : 'Your Song of the Day',
    url: SITE_SONGS + '#u=' + pid + '&d=' + slug, tag: 'sotd-uplnew-' + pid, unread };
  if (x.img) msg.icon = x.img;
  if (!await pushGate(env, (await env.SOTD.get('members', 'json')) || {}, rec.o, 'direct', msg)) return;
  const k = await vapidKeys(env);
  await Promise.all(ids.map(id => pushTo(env, k, subs[id], msg).catch(() => {})));
}
async function uplPreviews(env, U) {   // their songs' previews not looked up yet: UPL_PV_MOST each time, any hour
  let pv = null, left = UPL_PV_MOST;
  for (const [pid, r] of Object.entries(U)) {
    if (r.st !== 'ok' || left <= 0) continue;
    pv = pv || Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {});
    const miss = ((await uplSongs(env, pid)).songs || []).filter(x => !(x.id in pv));
    if (!miss.length) continue;
    pv = await fillPreviews(env, miss, left, pv);
    left -= Math.min(left, miss.length);
  }
}
// John's own imported playlists update by themselves (John, 3 Nov 2026: "could songs that I import not be treated in the same
// way as other exportify playlists and update automatically"): his Spotify sign-in reads his own playlists (private and
// collaborative too), so each of his Exportify imports is matched to the Spotify playlist of the same name (its file, fk,
// and the ones added to it, adds) and read every UPL_EVERY from 06:00 to 23:00, as a fresh download would bring it in:
// new songs wait for him to sign off, the rest keep their days, songs added from other playlists stay. Liked Songs needs
// another Spotify permission, so that one still comes from Exportify.
async function johnSpLists(env) {   // his Spotify playlists { key (as an Exportify file's): id }, kept six hours
  const kept = await env.SOTD.get('johnpls', 'json');
  if (kept && Date.now() - kept.at < 6 * 3600e3) return kept.map;
  const tok = await getToken(env), map = {};
  let next = '/me/playlists?limit=50';
  for (let i = 0; next && i < 20; i++) {
    const j = await spotify(tok, next);
    for (const p of j.items || []) if (p && p.id && p.name) { const k = uplFileKey(p.name); if (k && !map[k]) map[k] = p.id; }
    next = j.next ? j.next.replace('https://api.spotify.com/v1', '') : null;
  }
  await kvSoft(env, 'johnpls', JSON.stringify({ at: Date.now(), map }), { expirationTtl: 86400 });
  return map;
}
// a playlist owner's Spotify playlists { key (as an Exportify file's): Spotify id }: John's own list; a member's public
// ones (their Spotify id from connecting it here), kept a day (KV spmap:<member>); else a public playlist of the
// import's name whose owner has their first name
// a member's own Spotify sign-in, when it may read their playlists (they connected, or connected again, since it asks):
// { tok, sid }, else null. Spotify lets an app read only the playlists of the account signed in, so theirs is the way in
async function memberSpRead(env, id) {
  const sp = await env.SOTD.get('spm:' + id, 'json');
  if (!sp || sp.gone || sp.blocked || !/playlist-read-private/.test(sp.scope || '')) return null;
  return { tok: await spToken(env, id, sp), sid: sp.sid || '', liked: /user-library-read/.test(sp.scope || '') };
}
async function likedItems(token) {   // their Liked Songs, as playlistItems gives a playlist's (newest first, as Spotify keeps them)
  const out = []; let next = '/me/tracks?limit=50';
  for (let i = 0; next && i < 200; i++) {
    const j = await spotify(token, next);
    for (const it of j.items || []) {
      const t = it.track; if (!t || !t.id) continue;
      const imgs = (t.album && t.album.images) || [], img = imgs.find(x => x.width === 300) || imgs[1] || imgs[0];
      out.push({ a: it.added_at, id: t.id, title: t.name, artist: (t.artists || []).map(x => x.name).join(', '), artists: (t.artists || []).map(x => x.name), aids: (t.artists || []).map(x => x.id), album: t.album ? t.album.name : '', alid: t.album ? t.album.id : '', img: img ? img.url : '', rd: t.album && /^\d{4}(-\d\d){0,2}$/.test(t.album.release_date || '') ? t.album.release_date : undefined });
    }
    next = j.next ? j.next.replace('https://api.spotify.com/v1', '') : null;
  }
  return out.reverse();   // (oldest first, as a playlist's)
}
async function spReadList(token, spid) { return spid === 'liked' ? likedItems(token) : playlistItems(token, spid); }
async function myPlaylists(token) {   // the signed-in account's playlists (theirs, collaborative and followed)
  const pls = []; let next = '/me/playlists?limit=50';
  for (let i = 0; next && i < 10; i++) { const j = await spotify(token, next); for (const p of j.items || []) if (p && p.id && p.name) pls.push(p); next = j.next ? j.next.replace('https://api.spotify.com/v1', '') : null; }
  return pls;
}
function spUserOf(u) {   // a Spotify username from a profile link (open.spotify.com/user/<id>), a spotify:user: address, or the name itself
  const t = String(u || '').trim(), m = /(?:open\.spotify\.com\/(?:[a-z-]+\/)?user\/|spotify:user:)([^/?#\s]+)/i.exec(t);
  const id = m ? decodeURIComponent(m[1]) : t;
  return /^[\w.-]{1,64}$/.test(id) ? id : '';
}
async function uplSpMap(env, rec, ownerId, tok) {
  if (rec.o === ownerId) return await johnSpLists(env);
  const kept = await env.SOTD.get('spmap:' + rec.o, 'json');
  let map = kept && Date.now() - kept.at < 864e5 ? Object.assign({}, kept.map) : null;
  if (!map) {
    map = {};
    const mr = await memberSpRead(env, rec.o).catch(() => null);
    if (mr) { for (const p of await myPlaylists(mr.tok).catch(() => [])) { const k = uplFileKey(p.name); if (k && !map[k]) map[k] = p.id; } if (mr.liked) map.liked = map.liked || 'liked'; }
    await kvSoft(env, 'spmap:' + rec.o, JSON.stringify({ at: Date.now(), map }), { expirationTtl: 2 * 86400 });
  }
  return map;
}
const UPL_MEMBER_EVERY = 60 * 60e3;   // (members' imported playlists: read from Spotify hourly, John's every half hour)
async function uplJohnAuto(env, U, ownerId) {   // every imported playlist whose Spotify copy can be read: updated as a fresh download would (John, 4 Nov 2026: "can't you get any updated songs that way too?")
  const now = Date.now(), h = londonHour();
  if (!ownerId || h < 6 || h >= 23) return;
  const every = r => r.o === ownerId ? UPL_EVERY : UPL_MEMBER_EVERY;
  const due = Object.entries(U).filter(([, r]) => r.src === 'csv' && r.fk && now - (r.autoAt || 0) >= (r.auto === false ? 864e5 : every(r)))
    .sort((a, b) => (a[1].autoAt || 0) - (b[1].autoAt || 0)).slice(0, 2);
  if (!due.length) return;
  const tok = await getToken(env);
  for (const [pid, rec] of due) {
    rec.autoAt = now;
    const mr = rec.o === ownerId ? null : await memberSpRead(env, rec.o).catch(() => null);
    if (rec.o !== ownerId && !mr) { rec.auto = false; rec.autoErr = 'They haven’t let Spotify share their playlists (Connect Spotify in their Profile)'; continue; }
    const rt = mr ? mr.tok : tok, map = await uplSpMap(env, rec, ownerId, tok).catch(() => ({})), main = rec.spid || map[rec.fk];
    if (!main) { rec.auto = false; rec.autoErr = 'No Spotify playlist of theirs by that name'; continue; }   // (asked again a day later)
    try {
      // unchanged on Spotify since the last read (its snapshot): nothing read (every read shares one allowance with Spotify: John, 5 Nov 2026)
      const srcs = [main].concat((rec.adds || []).map(k => map[k]).filter(x => x && x !== main)), snaps = {};
      if (main !== 'liked') for (const sid of srcs) { const m = await spotify(rt, '/playlists/' + sid + '?fields=snapshot_id').catch(e => { if (e instanceof HttpError && e.extra && e.extra.spotify) throw e; return {}; }); if (m.snapshot_id) snaps[sid] = m.snapshot_id; }
      if (srcs.every(sid => snaps[sid] && rec.snaps && rec.snaps[sid] === snaps[sid])) { rec.auto = true; delete rec.autoErr; continue; }
      const items = await spReadList(rt, main), ids = new Set(items.map(x => x.id));
      const data = await uplSongs(env, pid), have = (data.songs || []).filter(x => !(data.add || {})[x.id]).map(x => x.id), same = have.filter(t => ids.has(t)).length;
      if (have.length >= 5 && same < 0.5 * Math.min(have.length, items.length)) { rec.auto = false; rec.autoErr = 'The Spotify playlist of that name isn’t this one'; continue; }   // (another playlist with the same name: never its songs)
      if (data.add) for (const x of data.songs || []) if (data.add[x.id] && !ids.has(x.id)) { items.push(x); ids.add(x.id); }   // (songs added to it from other playlists stay)
      let addNew = false;
      for (const k of rec.adds || []) {   // the playlists added to it: their new songs too
        const sid = map[k]; if (!sid || sid === main) continue;
        for (const x of await spReadList(rt, sid)) if (!ids.has(x.id)) { items.push(x); ids.add(x.id); data.add = data.add || {}; data.add[x.id] = 1; addNew = true; }
      }
      if (addNew) await env.SOTD.put('upl:' + pid, JSON.stringify(data));
      await uplTake(env, pid, rec, U, items, rec.st, now);
      rec.auto = true; delete rec.autoErr; rec.snaps = snaps;   // (kept once read and taken)
    } catch (e) { rec.auto = false; rec.autoErr = String(e && e.message || e).slice(0, 160); }
  }
  await uplPut(env, U);
}
// who added each song, for imported playlists brought in before Spotify's "added by" was kept (John, 4 Nov 2026: "I'd
// still like to see who added the song on Spotify on imported playlists"): the owner's public Spotify playlists (their
// Spotify id from connecting it here; John's own, his own list) matched by name to the import (fk, adds), read, and
// each song without one given its adder (ab). One playlist a timer run; one not found is asked again after a week.
async function uplAbFill(env, U, ownerId) {
  const now = Date.now();
  const due = Object.entries(U).filter(([, r]) => r.src === 'csv' && r.fk && !(r.abDone && (r.abDone === 1 || now - r.abDone < 7 * 864e5))).slice(0, 1);
  for (const [pid, rec] of due) {
    rec.abDone = now;
    const data = await uplSongs(env, pid), missing = (data.songs || []).filter(x => !x.ab);
    if (!missing.length) { rec.abDone = 1; continue; }
    const tok = await getToken(env), map = await uplSpMap(env, rec, ownerId, tok);
    const by = {};
    for (const k of [rec.fk].concat(rec.adds || [])) {
      if (!map[k]) continue;
      const mr2 = rec.o === ownerId ? null : await memberSpRead(env, rec.o).catch(() => null);
      const items = await spReadList(mr2 ? mr2.tok : tok, map[k]).catch(() => []);
      for (const x of items) if (x.ab && !by[x.id]) by[x.id] = x.ab;
    }
    let n = 0;
    for (const x of data.songs || []) if (!x.ab && by[x.id]) { x.ab = by[x.id]; n++; }
    if (n) await env.SOTD.put('upl:' + pid, JSON.stringify(data));
    if (n && n >= missing.length) rec.abDone = 1;
  }
  if (due.length) await uplPut(env, U);
}
async function uplLook(env) {   // the timer: playlists due a read (UPL_LOOK_MOST each time)
  const U = await uplAll(env), now = Date.now(), h = londonHour();
  const due = Object.entries(U).filter(([, r]) => uplSrc(r) === 'sp').filter(([, r]) => (r.st === 'ok' || r.st === 'err') ? (h >= 6 && h < 23 && now - (r.tried || 0) >= UPL_EVERY) : now - (r.tried || 0) >= (now - r.at < DAY ? UPL_JOIN_EVERY : 6 * UPL_JOIN_EVERY))
    .sort((a, b) => (a[1].tried || 0) - (b[1].tried || 0)).slice(0, UPL_LOOK_MOST);
  for (const [pid, r] of due) await uplRead(env, pid, r, U).catch(e => console.log('upl ' + pid + ': ' + (e && e.message)));
  await uplJohnAuto(env, U, await env.SOTD.get('owner')).catch(e => console.log('uplJohnAuto: ' + (e && e.message)));
  await uplAbFill(env, U, await env.SOTD.get('owner')).catch(e => console.log('uplAbFill: ' + (e && e.message)));
  await uplPreviews(env, U).catch(e => console.log('uplPreviews: ' + (e && e.message)));
  const today = londonDay();
  await uplAnnounce(env, U, today).catch(e => console.log('uplAnnounce: ' + (e && e.message)));
  if (Object.values(U).some(r => r.chg)) {   // a saved playlist's calendar changed since: the site job (its songs' videos, moods and years)
    for (const r of Object.values(U)) delete r.chg;
    await uplPut(env, U);
    await startJob(env, null, null, 0, null, true).catch(() => {});
  }
  const hr = new Date(now).toISOString().slice(0, 13);   // a playlist changed in the last two days: the site job hourly, to finish its songs' videos, moods and years (it takes so many a run)
  if (Object.values(U).some(r => r.st === 'ok' && r.src !== 'mpl' && now - (r.ch || 0) < 2 * DAY) && (await env.SOTD.get('uplJobHour')) !== hr && await kvSoft(env, 'uplJobHour', hr))
    await startJob(env, null, null, 0, null, true).catch(() => {});
  else if (h >= 7 && Object.values(U).some(r => r.st === 'ok') && (await env.SOTD.get('uplJobDay')) !== today && await kvSoft(env, 'uplJobDay', today))
    await startJob(env, null, null, 0, null, true).catch(() => {});   // once a day: their songs' videos, moods and years not found yet
}
// /yt/<pid>?s=<signature>: a member's playlist on YouTube, a link that stays the same as songs are added (signed with
// the Worker's ADMIN_KEY, so it can't be made from the playlist's id). Its songs as the others see them, each with its
// video as the calendar picks it (songYt: John's calendar's, else John's or its owner's choice, else the one found),
// newest day first (a playlist: in its order). Opened: YouTube's list of the first 50 out. With &f=json: { t: its name,
// songs: [{ id: track, v: video, a: artist, t: title, f: 1 still to come, w: 1 waiting for its owner }] }, every one
// with a video, to come and waiting too (Playlist push, the download scripts). An import's link goes on working after
// it's switched for another (prev).
// /upl/links (Authorization: Bearer ADMIN_KEY): { links: [{ id, t, o, link }] }, each member playlist's /yt/ link and
// its name (with its owner's after it, in brackets, when another member's has the same name).
const YT_JOHN = { at: 0, v: {} };
async function ytSig(env, pid) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode('sotd-yt|' + env.ADMIN_KEY), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return b64url(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(pid))).slice(0, 32);
}
const YR_JOHN = { at: 0, y: {} };
async function johnYears() {   // John's calendar's years (songs/years.json), read again after 10 minutes
  if (Date.now() - YR_JOHN.at > 600e3) {
    const j = await rawJson('songs/years.json', { years: {} }).catch(() => null);
    if (j) { YR_JOHN.y = j.years || {}; YR_JOHN.at = Date.now(); }
  }
  return YR_JOHN.y;
}
async function ytJohnVids() {   // John's calendar's videos (songs/videos.json), read again after 10 minutes
  if (Date.now() - YT_JOHN.at > 600e3) {
    const j = await rawJson('songs/videos.json', { videos: {} }).catch(() => null);
    if (j) { YT_JOHN.v = j.videos || {}; YT_JOHN.at = Date.now(); }
  }
  return YT_JOHN.v;
}
async function ytList(request, url, env) {
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  if (!env.ADMIN_KEY) return answer(503, { error: 'The ADMIN_KEY secret is not set on the Worker.' });
  const pid = url.pathname.slice(4), sig = url.searchParams.get('s') || '';
  if (!/^[\w-]{6,40}$/.test(pid) || sig !== await ytSig(env, pid)) return answer(404, { error: 'No such playlist.' });
  const U = await uplAll(env), all = (await env.SOTD.get('members', 'json')) || {};
  let id = pid, rec = U[pid];
  if (!rec) { const e = Object.entries(U).find(([, r]) => (r.prev || []).includes(pid)); if (e) [id, rec] = e; }   // (an import switched for another since: the one in its place, so a link kept in the Playlist push sheet goes on working)
  if (!rec || !all[rec.o]) return answer(404, { error: 'This playlist is no longer on the calendar.' });
  // f=json (Playlist push, the download scripts): every song, those still to come and those waiting for its owner too, so
  // their MP3s and MP4s are got ahead of their day (John, 1 Nov 2026: "The To Come songs aren't getting their MP3s and
  // mp4s"); each to come marked f: 1, each waiting w: 1. Opened in a browser: only the songs out, as members see them.
  const json = url.searchParams.get('f') === 'json';
  const view = await uplView(env, all, json ? rec.o : null, id, rec), J = await ytJohnVids();
  const plan = json ? await uplDlPlan(env, rec.o, U) : null;   // (beyond its owner's allowance: dl 0, played as video or preview, not downloaded)
  let songs = view.songs;
  if (rec.mode !== 'list') songs = songs.slice().sort((p, q) => p.d < q.d ? 1 : p.d > q.d ? -1 : 0);
  const out = [];
  for (const x of songs) {
    const v = (J[x.id] && J[x.id].v) || ('ov' in x ? x.ov : (x.vid && x.vid.v)) || '';
    const twinHas = x.jf && x.jf.a && x.jf.v;   // (files already, its own or another copy's of the same song: nothing to download)
    if (!v && x.osrc && json) { out.push(Object.assign({ id: x.id, v: x.osrc, src: x.osrc, a: x.artist || '', t: x.title || '' }, x.img ? { img: x.img } : {}, (twinHas || (plan && !plan.allowed.has(x.id) && !plan.john.has(x.id))) ? { dl: 0 } : {})); continue; }   // (no YouTube video: its MP3 from another site's link, for the download job)
    if (v) out.push(Object.assign({ id: x.id, v, a: x.artist || '', t: x.title || '' }, json && x.osrc ? { src: x.osrc } : {}, json && x.osrc && x.img ? { img: x.img } : {},   // (img: its cover, for a video made from it: John, 6 Nov 2026)
      json && (x.sk || '').includes('a') ? { na: 1 } : {}, json && (x.sk || '').includes('f') ? { nv: 1 } : {}, x.f ? { f: 1 } : {}, x.w ? { w: 1 } : {}, twinHas || (plan && !plan.allowed.has(x.id) && !plan.john.has(x.id)) ? { dl: 0 } : {}));
  }
  if (json) return answer(200, { t: view.n, songs: out });
  if (!out.length) return new Response('No videos on “' + view.n + '” yet.', { status: 404, headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' } });
  return new Response(null, { status: 302, headers: { 'Cache-Control': 'no-store', Location: 'https://www.youtube.com/watch_videos?video_ids=' + out.slice(0, 50).map(x => x.v).join(',') + '&title=' + encodeURIComponent(view.n) } });
}
// /upl/vids, for the site job only: members' songs' videos as soon as its YouTube step has found them, before the
// rest of its run (listening, moods, years) is done (John, 1 Nov 2026: "The YouTube playlist can be made and added to
// csv as soon as discovered, I think it's waiting for mood and years"). POST { vids: { <track>: { v, k, ... } },
// looked: [tracks looked at, found or not] }: each video into KV uplmeta (as /upl/data puts it at the end), each looked
// at into uplseen ('v'). So their /yt/ lists, the Playlist push sheet, the calendars and the progress have them now.
async function uplVids(request, env) {
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  if (!await ghJob(request)) return answer(401, { error: 'For the site job only.' });
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const b = await request.json().catch(() => ({}));
  const vids = b && typeof b.vids === 'object' ? b.vids : {}, looked = Array.isArray(b && b.looked) ? b.looked : [];
  const yrs = b && typeof b.years === 'object' ? b.years : {}, ylooked = Array.isArray(b && b.ylooked) ? b.ylooked : [];
  const meta = (await env.SOTD.get('uplmeta', 'json')) || {};
  let n = 0;
  for (const [t, v] of Object.entries(vids)) if (ID_RE.test(t) && v && typeof v.v === 'string' && (!meta[t] || !meta[t].vid || meta[t].vid.v !== v.v)) { meta[t] = Object.assign({}, meta[t] || {}, { vid: v }); n++; }
  let ny = 0;
  for (const [t, y] of Object.entries(yrs)) if (ID_RE.test(t) && Number.isInteger(y) && y > 1900 && (!meta[t] || meta[t].yr !== y)) { meta[t] = Object.assign({}, meta[t] || {}, { yr: y }); ny++; }   // (and the years, as soon as found)
  if (n || ny) await env.SOTD.put('uplmeta', JSON.stringify(meta));
  const seen = (await env.SOTD.get('uplseen', 'json')) || {};
  let m = 0;
  for (const t of looked) if (ID_RE.test(String(t)) && !(seen[t] || '').includes('v')) { seen[t] = (seen[t] || '') + 'v'; m++; }
  for (const t of (Array.isArray(b && b.unlooked) ? b.unlooked : [])) if (ID_RE.test(String(t)) && (seen[t] || '').includes('v')) { seen[t] = seen[t].replace('v', ''); if (!seen[t]) delete seen[t]; m++; }   // (only the free ways tried: still waiting for the search)
  for (const t of ylooked) if (ID_RE.test(String(t)) && !(seen[t] || '').includes('y')) { seen[t] = (seen[t] || '') + 'y'; m++; }
  if (m) await kvSoft(env, 'uplseen', JSON.stringify(seen));
  return answer(200, { ok: true, vids: n, years: ny, looked: m });
}
// /upl/wanted (Authorization: Bearer ADMIN_KEY): { at } when John last deleted one of the catalog's files on the admin
// page (KV r2want), so Playlist push starts playlist-r2 then and a song whose files were wrong gets new ones at once,
// not at its daily run (John, 1 Nov 2026: "A song was wrong and so I removed and replace the files. It won't get new
// files unless a request is put in somewhere").
async function uplWanted(request, env) {
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  const bad = await adminOk(request, env, answer);
  if (bad) return bad;
  const w = (await env.SOTD.get('r2want', 'json')) || {};
  return answer(200, { at: w.at || 0, key: w.key || '' });
}
// /upl/ytdrop, for the site job only: the YouTube copies of playlists taken off the calendar, to delete (John, 1 Nov
// 2026: "AaBbCc and Potench playlists have been deleted now so the songs should be deleted fully"). GET { ids };
// POST { done: [ids] } takes those off the list.
async function uplYtDrop(request, env) {
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  if (!await ghJob(request)) return answer(401, { error: 'For the site job only.' });
  const q = (await env.SOTD.get('ytdrop', 'json')) || [];
  if (request.method === 'GET') return answer(200, { ids: q });
  const b = await request.json().catch(() => ({})), done = new Set(Array.isArray(b.done) ? b.done.map(String) : []);
  const left = q.filter(x => !done.has(x));
  if (left.length !== q.length) await env.SOTD.put('ytdrop', JSON.stringify(left));
  return answer(200, { ok: true, left: left.length });
}
async function uplLinks(request, env) {
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  const bad = await adminOk(request, env, answer);
  if (bad) return bad;
  const U = await uplAll(env), all = (await env.SOTD.get('members', 'json')) || {};
  const recs = Object.entries(U).filter(([, r]) => all[r.o]), names = {};
  for (const [, r] of recs) { const n = uplName(all, r).toLowerCase(); names[n] = (names[n] || 0) + 1; }
  const links = [];
  for (const [pid, r] of recs) {
    const n = uplName(all, r);
    links.push({ id: pid, t: names[n.toLowerCase()] > 1 ? n + ' (' + all[r.o].name + ')' : n, o: all[r.o].name, link: LIVE_ORIGIN + '/yt/' + pid + '?s=' + await ytSig(env, pid) });
  }
  return answer(200, { links });
}
// /upl/sync, for the site job only (its own GITHUB_TOKEN, checked with GitHub: a token for this repository alone): GET
// the readable playlists with their songs (as songs.json gives them), its calendar title and YouTube copy; POST { yt: {
// <pid>: <YouTube playlist id> } } keeps the YouTube copies youtube_upl.py made (yt on the calendar's view).
const ghOk = new Map();
async function ghJob(request) {
  const tok = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  if (!tok || tok.length > 400) return false;
  const h = await sha256hex(tok), t = ghOk.get(h);
  if (t && t > Date.now()) return true;
  const r = await fetch('https://api.github.com/installation/repositories?per_page=5', { headers: { Authorization: 'Bearer ' + tok, Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'SongOfTheDayWorker' } }).catch(() => null);
  if (!r || !r.ok) return false;
  const j = await r.json().catch(() => ({}));
  const ok = j.total_count === 1 && (j.repositories || []).some(x => x.full_name === REPO);
  if (ok) ghOk.set(h, Date.now() + 3600e3);
  return ok;
}
// how the site job's video search is getting on (youtube.py's progress(), every few seconds): for John's job line (John, 3 Nov
// 2026: "what youtube.py is doing and searching for and progress"). Kept a day.
async function uplYtProg(request, env) {
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  if (request.method !== 'POST' || !await ghJob(request)) return answer(401, { error: 'For the site job only.' });
  const b = await request.json().catch(() => ({})), str = (x, n) => String(x == null ? '' : x).slice(0, n), num = x => Number.isFinite(+x) && x !== null && x !== '' ? +x : null;
  const v = { at: Date.now(), phase: str(b.phase, 200), i: num(b.i), n: num(b.n), song: str(b.song, 160), units: num(b.units), srch: num(b.srch), srchMax: num(b.srchMax),
    last: (Array.isArray(b.last) ? b.last : []).slice(0, 6).map(x => ({ s: str(x && x.s, 160), r: str(x && x.r, 200) })) };
  await kvSoft(env, 'ytprog', JSON.stringify(v), { expirationTtl: 86400 });
  return answer(200, { ok: true });
}
// a song's video looked for for good: found, or YouTube's search found none after the current free ways missed it too. One
// only the free ways missed, or an older search missed before the newer free ways (fv 2: the artist's channel) had a go,
// is still being looked for (John, 3 Nov 2026: "165 with none for videos ... searched for them before but with a different method")
function ytLookDone(e) { return !!e && (!!e.id || (e.from === 'YouTube search' && !e.error && (e.fv || 0) >= 2 && (e.sv || 0) >= 2)); }   // (sv 2: matched as youtube.py's SCORE_V 2)
async function uplReseen(env) {   // once: the 'v' marks put right from the last run's files, so the progress shows it now
  if (!env.MEDIA || await env.SOTD.get('uplReseen3')) return;
  await env.SOTD.put('uplReseen3', '1');
  const o = await env.MEDIA.get('site/upl-data.json'); if (!o) return;
  const yt = ((((await o.json()) || {}).files || {})['_sotd/data/youtube.json'] || {}).songs || {};
  const seen = (await env.SOTD.get('uplseen', 'json')) || {}; let n = 0;
  for (const [t, e] of Object.entries(yt)) if ((seen[t] || '').includes('v') && !ytLookDone(e)) { seen[t] = seen[t].replace('v', ''); if (!seen[t]) delete seen[t]; n++; }
  await kvSoft(env, 'uplseen', JSON.stringify(seen));
  console.log('uplReseen: ' + n + ' back to still being looked for');
}
async function uplSync(request, env) {
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  if (!await ghJob(request)) return answer(401, { error: 'For the site job only.' });
  const U = await uplAll(env), all = (await env.SOTD.get('members', 'json')) || {};
  if (request.method === 'POST') {
    const body = await request.json().catch(() => ({})), yt = body.yt || {};
    let n = 0;
    for (const [pid, id] of Object.entries(yt)) if (U[pid] && /^PL[\w-]{10,60}$/.test(String(id))) { U[pid].yt = String(id); n++; }
    if (n) await uplPut(env, U);
    return answer(200, { ok: true, kept: n });
  }
  const pv = Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {}), tags = (await env.SOTD.get('tags', 'json')) || {}, out = {}, J = await uplJohnAll(env), cat = await catalog(env), TW = await twinFiles(env);
  for (const [pid, rec] of Object.entries(U)) {
    if (rec.st !== 'ok' || !all[rec.o]) continue;
    const data = await uplSongs(env, pid);
    const ed = data.ed || {};   // (ov: its owner's video for a song, '' none)
    const gone = new Set((data.songs || []).filter(x => uplGone(data, x)).map(x => x.id));
    out[pid] = { title: uplTitle(all, rec), mode: rec.mode, base: rec.base || 0, yt: rec.yt || '', share: uplShareOf(rec), songs: composeSongs(data.songs || [], pv, tags).filter(x => !gone.has(x.id))
      .map(x => J[x.id] && 'v' in J[x.id] ? Object.assign(x, { ov: J[x.id].v }) : ed[x.id] && 'v' in ed[x.id] ? Object.assign(x, { ov: ed[x.id].v }) : ((data.skip || {})[x.id] || '').includes('v') ? Object.assign(x, { ov: '' }) : (data.retry || {})[x.id] > Date.now() - UPL_RETRY_FOR ? Object.assign(x, { retry: 1 }) : x)
      .map(x => { if ('ov' in x) return x; const f = (data.files || {})[x.id], tw = (cat[x.id] && cat[x.id].v) || (f && f.v) ? null : twinOf(TW, x, pid); if ((cat[x.id] && cat[x.id].v) || (f && f.v) || (tw && (tw.pid ? tw.v : cat[tw.id] && cat[tw.id].v))) x.mp4 = 1; return x; }) };   // (mp4: it has an MP4 already, its own or another copy's: no YouTube search for it: John, 5 Nov 2026)   // (its look for a video stopped by its owner: none, so the job doesn't search; retry: John's Look again, looked for again by the job, youtube.py)   // (John's video over its owner's)
    if (uplShareOf(rec) === 'public') {   // its songs out, each with its day and cover: the cards for its links (share_pages.py)
      const v = await uplView(env, all, null, pid, rec);
      out[pid].pub = { t: v.t, n: rec.cvt || v.n, cap: rec.cap || '', mode: rec.mode, songs: v.songs.map(x => ({ id: x.id, d: x.d, title: x.title, artist: x.artist, artists: x.artists, img: x.img || '' })) };
      out[pid].facts = uplFactsWanted(rec, data);   // (written by the facts task as John's are: songs added since, and those its owner asks for)
    }
  }
  return answer(200, { playlists: out });
}
// /upl/data, for the site job only (as /upl/sync): what it found for members' songs, kept here and not in the repository
// (upl_private.py): GET gives it back (R2 site/upl-data.json; 404 none yet); POST keeps it, and each song's video, moods
// and genres, and year in KV uplmeta { <track>: { vid, mt: { m, g }, yr } }, for their calendars (uplView).
async function uplData(request, env) {
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  if (!await ghJob(request)) return answer(401, { error: 'For the site job only.' });
  if (!env.MEDIA) return answer(503, { error: 'Not set up.' });
  if (request.method === 'GET') {
    const o = await env.MEDIA.get('site/upl-data.json');
    return o ? new Response(o.body, { headers: Object.assign(factsHeaders(MEMBERS_ORIGIN), { 'Content-Type': 'application/json; charset=utf-8' }) }) : answer(404, { none: true });   // (the page's own origin may read it)
  }
  if (request.method !== 'POST') return answer(405, { error: 'Use GET or POST.' });
  const text = await request.text();
  if (text.length > 40e6) return answer(413, { error: 'Too big.' });
  let b; try { b = JSON.parse(text); } catch (e) { return answer(400, { error: 'Not JSON.' }); }
  if (!b || typeof b.files !== 'object') return answer(400, { error: 'No files.' });
  await env.MEDIA.put('site/upl-data.json', text, { httpMetadata: { contentType: 'application/json' } });
  const f = b.files, meta = {}, at = t => (meta[t] = meta[t] || {});
  for (const [t, v] of Object.entries((f['songs/videos.json'] || {}).videos || {})) if (v && v.v) at(t).vid = v;
  for (const [t, x] of Object.entries((f['songs/moods.json'] || {}).songs || {})) if (x && ((x.moods || []).length || (x.genres || []).length)) at(t).mt = { m: x.moods || [], g: x.genres || [] };
  for (const [t, y] of Object.entries((f['songs/years.json'] || {}).years || {})) if (y) at(t).yr = y;
  await env.SOTD.put('uplmeta', JSON.stringify(meta));
  // which songs the job has looked at, found or not (v: a video, m: moods, y: a year), for a playlist's progress: a song
  // looked at with none found is done, not waiting (KV uplseen { <track>: 'vmy' })
  const seen = {}, mark = (o, c) => { for (const t of Object.keys(o || {})) seen[t] = (seen[t] || '') + c; };
  const ytDone = Object.fromEntries(Object.entries((f['_sotd/data/youtube.json'] || {}).songs || {}).filter(([, e]) => ytLookDone(e)));   // (a video, or the search found none: one only the free ways missed still waits)
  mark(ytDone, 'v'); mark((f['songs/moods.json'] || {}).songs, 'm'); mark((f['_sotd/data/years.json'] || {}).songs, 'y');
  await kvSoft(env, 'uplseen', JSON.stringify(seen));
  return answer(200, { ok: true, songs: Object.keys(meta).length });
}
// ---------- facts for the songs on members' public playlists: written and checked by John's facts task as his are
// (FACTS.md; their sources from the site job, about.py, for the songs this gives it), into _sotd/data/upl-facts.json,
// which the site job sends here (/upl/facts); each waits for its owner (the Edit pop-up: edit, Sign off), then shows
// on their calendar. KV ufacts:<pid> { <track>: { w: written, waiting, ok: signed off, none: none could be found, at, sat } }, each { s: [song
// facts], a: [{ id, n: the artist's name, f: [facts] }] }. ----------
const UPL_FACTS_FIRST = 10;   // a public one's newest songs from before it was added that get facts
function uplFactsWanted(rec, data) {   // a public one's songs wanting facts: added since it was added, its 10 newest from before, or asked for
  if (uplShareOf(rec) !== 'public') return [];
  const songs = (data.songs || []).filter(x => !uplGone(data, x)), base = rec.base || 0, ask = data.fask || {};
  const first = new Set(songs.filter(x => Date.parse(x.a) <= base).slice(-UPL_FACTS_FIRST).map(x => x.id));
  return songs.filter(x => Date.parse(x.a) > base || first.has(x.id) || ask[x.id]).map(x => x.id);
}
async function uplFactsIn(request, env) {   // POST the facts task's _sotd/data/upl-facts.json (the site job's token): each new one waits for its owner
  const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  if (!await ghJob(request)) return answer(401, { error: 'For the site job only.' });
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const body = await request.json().catch(() => ({})), U = await uplAll(env), by = {};
  for (const [t, e] of Object.entries(body.songs || {})) {
    const pid = String(e.upl || '');
    if (!U[pid] || !e.written) continue;
    (by[pid] = by[pid] || {})[t] = e;
  }
  let n = 0;
  for (const [pid, songs] of Object.entries(by)) {
    const kept = (await env.SOTD.get('ufacts:' + pid, 'json')) || {};
    let changed = false;
    for (const [t, e] of Object.entries(songs)) {
      if (kept[t] && kept[t].at === e.written) continue;   // (had: as its owner left it)
      const txt = l => (l || []).map(x => String((x && x.t) || x || '').trim()).filter(Boolean);
      let w = { s: txt(e.s), a: Object.entries(e.a || {}).map(([id, a]) => ({ id, n: a.name || '', f: txt(a.f) })) };
      const base = kept[t] && (kept[t].w || kept[t].ok);   // facts they have already (waiting or out, as they left them): kept, the new ones added after them, marked new (John, 3 Nov 2026: "don't overwrite any users current facts")
      let nw = [];
      if (base) {
        const norm = x => String(x).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(), had = new Set([].concat(base.s || [], ...(base.a || []).map(a => a.f || [])).map(norm));
        const fresh = l => l.filter(x => !had.has(norm(x)));
        const s2 = fresh(w.s), a2 = (base.a || []).map(a => ({ id: a.id, n: a.n, f: (a.f || []).concat(fresh(((w.a.find(b => b.id === a.id) || {}).f) || [])) }))
          .concat(w.a.filter(b => !(base.a || []).some(a => a.id === b.id)).map(b => ({ id: b.id, n: b.n, f: fresh(b.f) })));
        nw = s2.concat(...w.a.map(b => fresh(b.f)));
        w = { s: (base.s || []).concat(s2), a: a2 };
        if (!nw.length) { kept[t] = Object.assign({}, kept[t], { at: e.written }); changed = true; continue; }   // (nothing new: theirs as they are)
      }
      if (w.s.length || w.a.some(a => a.f.length)) kept[t] = Object.assign({}, kept[t] || {}, { w, at: e.written }, nw.length ? { nw: [...new Set([...(kept[t] && kept[t].nw) || [], ...nw])] } : {});
      else if (!kept[t] || !kept[t].ok) kept[t] = { none: 1, at: e.written };   // (none could be found)
      changed = true; n++;
    }
    if (changed) await env.SOTD.put('ufacts:' + pid, JSON.stringify(kept));
  }
  return answer(200, { ok: true, added: n });
}
async function uplAdm(env, viewer) { return !!viewer && (await env.SOTD.get('owner')) === viewer; }
async function uplView(env, all, viewer, pid, rec, also) {   // (also: tracks wanted even if taken off, for memberSongs: saved to a playlist elsewhere)   // a playlist as the calendar takes it: its songs (out ones; its owner, all, the waiting marked w and the days to come f)
  const site = await uplAdm(env, viewer), data = await uplSongs(env, pid), mine = !!viewer && (rec.o === viewer || uplCo(rec, viewer) || site), today = londonDay();   // (John, the site's owner: anyone's, as a collaborator, to put right what's missing: John, 4 Nov 2026, "I should be able to correct missing songs from another playlist")   // (mine: theirs to edit, its owner's or a collaborator's)
  const pv = Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {});
  const tags = (await env.SOTD.get('tags', 'json')) || {};
  const days = uplDays(data.songs || [], rec);
  const meta = (await env.SOTD.get('uplmeta', 'json')) || {}, facts = (await env.SOTD.get('ufacts:' + pid, 'json')) || {}, wanted = new Set(uplShareOf(rec) === 'public' ? uplFactsWanted(rec, data) : []);
  const cat = await catalog(env), J = await uplJohnAll(env), TW = await twinFiles(env), DLS = mine ? await dlStatus(env) : {};   // John's own (the Songs tab's): a song's that he has, its owner's of that kind aren't used
  const JV = await ytJohnVids(), JY = await johnYears();   // (a song John's calendar has too: its video and year from there, found already, never looked for again: John, 1 Nov 2026)
  const ed = data.ed || {}, files = data.files || {}, see = mine ? { a: true, v: true } : viewer && all[viewer] ? mediaFor(all[viewer]) : { a: false, v: false }, exp = Math.floor(Date.now() / 1000) + LINK_HOURS * 3600;
  const gone = new Set((data.songs || []).filter(x => uplGone(data, x) && !(also && also.has(x.id))).map(x => x.id));
  const adm = site;
  let songs = composeSongs(data.songs || [], pv, tags).map((x, i) => Object.assign(x, { d: days[i].d }, data.songs[i].ab ? { ab: data.songs[i].ab } : {}, uplWaiting(rec, data, data.songs[i]) ? { w: 1 } : {}, days[i].d > today ? { f: 1 } : {}))   // (ab: who added it on Spotify, kept by composeSongs' fields no more: John, 4 Nov 2026, "I also still can't see the spotify user who added a song")
    .filter(x => !gone.has(x.id) && (mine ? (rec.o === viewer || uplCo(rec, viewer) || !x.w) : (!x.w && !x.f)));   // (John: not those still waiting for their owner)   // (John, the site's owner: its songs still to come too, faded, as its owner sees them: John, 1 Nov 2026)
  for (const x of songs) {   // what the site job found (vid: the video, mt: moods and genres, yr: the year), its owner's choices (ov: the video, '' none; tg: moods and genres), files (ma, mv; own: theirs, with sizes) and cover (uc)
    const e = ed[x.id], f = files[x.id], m = meta[x.id];
    if (m) { if (m.vid) x.vid = m.vid; if (m.mt) x.mt = m.mt; if (m.yr) x.yr = m.yr; }
    if (!x.vid && JV[x.id] && JV[x.id].v) x.vid = JV[x.id];
    if (!x.yr && JY[x.id]) x.yr = JY[x.id];
    if (!x.yr && /^\d{4}/.test(x.rd || '') && +x.rd.slice(0, 4) > 1900) x.yr = +x.rd.slice(0, 4);   // (its album's release date, from the file or Spotify: a year the moment it's in)
    const fx = facts[x.id];   // its facts: signed off by its owner (fx), for everyone; theirs too: written and waiting (fxw), and how it stands (fxs)
    if (fx && fx.ok) x.fx = fx.ok;
    if (mine) { if (fx && fx.w) x.fxw = fx.w; if (fx && fx.w && fx.nw) x.fxn = fx.nw; x.fxs = fx && fx.w ? 'wait' : fx && fx.ok ? 'ok' : fx && fx.none ? 'nil' : wanted.has(x.id) ? 'asked' : uplShareOf(rec) === 'public' ? 'none' : 'off'; }
    if (x.ab) { const nm = (await spWho(env, all))[x.ab]; if (nm || mine || adm) x.abn = nm || x.ab; delete x.ab; }   // (the Spotify id itself not sent on)   // (who added it on Spotify: a member's name for everyone, else the Spotify username for its owner and John: John, 3 Nov 2026)   // (who added it on Spotify, for its owner's editor: a member's name where it's one of theirs: John, 3 Nov 2026)
    if (e && 'v' in e) x.ov = e.v;
    if (e && e.cap) x.cap = e.cap;   // (its caption, for everyone who sees it)
    if (e && e.src && (mine || adm)) x.osrc = e.src;   // (its MP3 from another site's link)
    if (mine && data.skip && data.skip[x.id]) x.sk = data.skip[x.id];   // (marked can't be found: p, v, a, f)
    if (e && (e.tt || e.ar)) {   // its owner's own title and artist, for everyone; theirs: Spotify's too (ot, oa), to go back to
      if (mine) { x.ot = x.title; x.oa = x.artist; }
      if (e.tt) x.title = e.tt;
      if (e.ar) { x.artist = e.ar; x.artists = [e.ar]; }
    }
    if (e && (e.m || e.g)) x.tg = { m: e.m || null, g: e.g || null };   // (null: the ones worked out)
    const jo = J[x.id];   // John's, over theirs (jo, for its owner's editor: which are his)
    if (jo) {
      if ('v' in jo) x.ov = jo.v;
      if (jo.m || jo.g) x.tg = { m: jo.m || (x.tg && x.tg.m) || null, g: jo.g || (x.tg && x.tg.g) || null };
      if (mine) x.jo = { v: 'v' in jo ? 1 : 0, m: jo.m ? 1 : 0, g: jo.g ? 1 : 0 };
    }
    let jcId = x.id, jc = cat[x.id] || {};
    let twOwn = null;
    if (!jc.a && !jc.v && !(f && (f.a || f.v))) { const tw = twinOf(TW, x, pid); if (tw && !tw.pid && cat[tw.id]) { jc = cat[tw.id]; jcId = tw.id; } else if (tw && tw.pid) twOwn = tw; }   // (another copy of the same song has them: those)
    if (!x.vid && jcId !== x.id && JV[jcId] && JV[jcId].v) x.vid = JV[jcId];   // (the other copy's video too)
    if (!x.vid && twOwn && meta[twOwn.id] && meta[twOwn.id].vid) x.vid = meta[twOwn.id].vid;
    if ((mine || adm) && (jc.a || jc.v)) x.jf = { a: jc.a || 0, v: jc.v || 0 };   // (John too, on anyone's: its Missing as its owner sees it, John, 3 Nov 2026)   // (its owner's editor: John's, with their sizes)
    if ((mine || adm) && env.ADMIN_KEY) for (const k of ['a', 'v']) if (jc[k]) x['m' + k] = LIVE_ORIGIN + '/m/' + k + '/' + jcId + '.' + MEDIA_KINDS[k].ext + '?e=' + exp + '&s=' + await mediaSig(env, k, jcId, exp);   // (John's, for its owner on their own playlist, whatever he lets them have elsewhere)
    { const ds = DLS[x.id], have = k => !!(jc[k] || (f && f[k]) || (twOwn && twOwn[k])); if (ds && !(have('a') && have('v'))) x.dlx = { k: ds.kind, how: ds.how, why: ds.why, tries: ds.tries || 1, at: ds.at }; }   // (why its files didn't come, for its Missing)
    if (twOwn && env.ADMIN_KEY) {   // (another playlist's own uploads of the same song: played from there; counted as its files)
      for (const k of ['a', 'v']) if (twOwn[k] && (mine || see[k] || adm)) x['m' + k] = LIVE_ORIGIN + '/m/u' + k + '/' + twOwn.pid + '/' + twOwn.id + '.' + MEDIA_KINDS[k].ext + '?e=' + exp + '&s=' + await mediaSig(env, 'u' + k + '/' + twOwn.pid, twOwn.id, exp);
      if (mine || adm) x.jf = { a: twOwn.a ? 1 : 0, v: twOwn.v ? 1 : 0 };
    }
    if (f) {
      for (const k of ['a', 'v']) if (f[k] && !jc[k] && (see[k] || adm) && env.ADMIN_KEY) x['m' + k] = LIVE_ORIGIN + '/m/u' + k + '/' + pid + '/' + x.id + '.' + MEDIA_KINDS[k].ext + '?e=' + exp + '&s=' + await mediaSig(env, 'u' + k + '/' + pid, x.id, exp);
      if (f.c) { x.img = LIVE_ORIGIN + '/uc/' + pid + '/' + x.id + '.jpg?v=' + f.c; x.uc = 1; }
      if (mine) x.own = { a: f.a || 0, v: f.v || 0, c: f.c ? 1 : 0 };
    }
  }
  if (rec.mode === 'list' && (data.order || []).length) {   // its owner's order; songs since, after, as Spotify has them
    const at = {}; data.order.forEach((t, i) => { at[t] = i; });
    songs = songs.filter(x => x.id in at).sort((a, b) => at[a.id] - at[b.id]).concat(songs.filter(x => !(x.id in at)));
  }
  if (rec.mode !== 'list' && rec.dord) {   // a day's songs in its owner's order (dord { day: [tracks] }; John, 3 Nov 2026: "there should be ability to reorder them too"), each in its own place among the rest
    const slots = {};
    songs.forEach((x, i) => { if (x.d && rec.dord[x.d]) (slots[x.d] = slots[x.d] || []).push(i); });
    for (const [d, idx] of Object.entries(slots)) {
      if (idx.length < 2) continue;
      const at = {}; rec.dord[d].forEach((t, i) => { at[t] = i; });
      const these = idx.map(i => songs[i]).sort((a, b) => (a.id in at ? at[a.id] : 1e6) - (b.id in at ? at[b.id] : 1e6));
      idx.forEach((i, k) => { songs[i] = these[k]; });
    }
  }
  let prog;
  if (mine) {   // (its owner: how far the calendar has got with its songs: each part found (p, v, m, y), looked at with none (pn, vn, mn, yn), and their own files (fa, fv: songs with an MP3, an MP4))
    const all2 = (data.songs || []).filter(x => !gone.has(x.id)), seen = (await env.SOTD.get('uplseen', 'json')) || {}, skip = data.skip || {};
    const off = (x, k) => (skip[x.id] || '').includes(k);   // (its owner stopped the look for it: John, 1 Nov 2026: "You should be able to cancel a search for something")
    const has = (x, k) => k === 'v' ? !!(x.vid || x.ov || x.mv || (x.own && x.own.v) || (x.jf && x.jf.v)) :   // (an MP4, its own or another copy's: a video already, never looked for: John, 5 Nov 2026)
      k === 'm' ? !!(x.mt || x.tag) : !!x.yr;
    const noneOf = k => songs.filter(x => !has(x, k) && ((seen[x.id] || '').includes(k) || (k === 'v' && x.ov === '') || off(x, k))), none = k => noneOf(k).length;
    const leftOf = k => songs.filter(x => !has(x, k) && !(seen[x.id] || '').includes(k) && !(k === 'v' && x.ov === '') && !off(x, k));
    const pvNone = x => pv[x.id] === '-' || (!(x.id in pv) && off(x, 'p')), pvLeft = x => !(x.id in pv) && !off(x, 'p');
    const ids = l => l.slice(0, 200).map(x => x.id);   // (which ones, for its owner: John, 1 Nov 2026: "can we have it say which ones they are")
    prog = { n: all2.length, pq: all2.filter(pvLeft).length, p: all2.filter(x => pv[x.id] && pv[x.id] !== '-').length, pn: all2.filter(pvNone).length,
      v: songs.filter(x => has(x, 'v')).length, vn: none('v'), m: songs.filter(x => has(x, 'm')).length, mn: none('m'), y: songs.filter(x => has(x, 'y')).length, yn: none('y'),
      which: { p: ids(all2.filter(pvNone)), v: ids(noneOf('v')), m: ids(noneOf('m')), y: ids(noneOf('y')) },
      left: { p: ids(all2.filter(pvLeft)), v: ids(leftOf('v')), m: ids(leftOf('m')), y: ids(leftOf('y')) },   // (still to look for: which, each one's look can be stopped)
      fa: songs.filter(x => x.ma || (x.own && x.own.a) || (x.jf && x.jf.a)).length, fv: songs.filter(x => x.mv || (x.own && x.own.v) || (x.jf && x.jf.v)).length, ch: rec.ch || rec.at || 0 };
  }
  return { id: pid, n: uplName(all, rec), cap: rec.cap || undefined, t: uplTitle(all, rec), o: all[rec.o].name, mine, own: !!viewer && (rec.o === viewer || site), boss: (site && rec.o !== viewer) || undefined, co: uplCo(rec, viewer) || undefined, admin: adm || undefined, mode: rec.mode, sp: rec.sp, src: uplSrc(rec), yt: rec.yt || '', st: rec.st, at: data.at || null, sh: uplShareOf(rec),
    dates: rec.mode === 'list' ? null : Object.assign({ how: 'added' }, rec.dates || {}), send: mine ? uplSendOf(rec) : undefined, prog, songs };
}
// /members/upl: GET ?id= one playlist to show; POST { op: 'add', link, mode } | { op: 'csv', name, mode, songs } (from an
// Exportify file, see uplCsvSongs) | { op: 'csvup', id, songs } (a fresh export of it) | { op: 'mode', id, mode } | { op: 'remove', id }
// | { op: 'check', id } | { op: 'signoff', id, track } | { op: 'dates', id, how: 'added' | 'fwd' | 'back', day } (a calendar:
// by the day each was added on Spotify; a song a day forwards from day, the first song on it; or back from day, the
// newest song now on it and the ones after it on the days after) | { op: 'order', id, order: [track ids] } (a playlist)
// | { op: 'send', id, on, at: 'HH:MM' } (whether its songs go to the members as messages, and when)
// | { op: 'edit', id, track, v, m, g } (a song's video, moods and genres) | { op: 'title', id, title } | { op: 'share', id,
// share, to } | { op: 'facts', id, track, s, a, sign } | { op: 'askfacts', id, track }; GET ?id= with no sign-in: a public one. /members/songs?ids=: songs from the playlists whoever asks sees
async function memberUpl(request, url, env, answer) {
  const who = await memberWho(request, env);
  if (!who && request.method === 'GET') {   // not signed in: a public one
    const U = await uplAll(env), all = (await env.SOTD.get('members', 'json')) || {}, pid = url.searchParams.get('id') || '', rec = U[pid];
    if (!uplCan(all, null, rec)) return answer(rec ? 401 : 404, { error: rec ? 'That calendar is for members.' : 'No such playlist.' });
    return answer(200, await uplView(env, all, null, pid, rec));
  }
  if (!who) return answer(401, { error: 'Not a member (any more).' });
  const { id: me, all } = who, U = await uplAll(env), BOSS = await uplAdm(env, me);
  if (request.method === 'GET') {
    const pid = url.searchParams.get('id') || '', rec = U[pid];
    if (url.searchParams.get('cards') === '1') return answer(200, { cards: await uplCardCovers(env, all, me, U) });
    if (url.searchParams.get('songs') === '1') return answer(200, { songs: await uplAllSongs(env, all, me, U) });
    if (!uplCan(all, me, rec)) return answer(404, { error: 'No such playlist.' });
    if (rec.o === me && url.searchParams.get('fill') === '1') await fillPreviews(env, ((d0 => (d0.songs || []).filter(x => !((d0.skip || {})[x.id] || '').includes('p')))(await uplSongs(env, pid))), UPL_PV_FILL, Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {})).catch(() => null);   // (its owner watching it fill: the next previews now)
    return answer(200, await uplView(env, all, me, pid, rec));
  }
  if (request.method !== 'POST') return answer(405, { error: 'Use GET or POST.' });
  const body = await request.json().catch(() => ({})), mine = async () => answer(200, { ok: true, pls: await uplMine(env, uplList(all, me, U, BOSS), U) });
  const own = pid => U[pid] && (U[pid].o === me || (BOSS && all[U[pid].o])) ? U[pid] : null;   // (theirs; John: anyone's, as its owner can: John, 7 Nov 2026)
  if (body.op === 'add') return answer(400, { error: 'Bring playlists in from an Exportify download: Import from Spotify.' });   // (the collaborator way in: retired)
  if (body.op === 'add-retired') {
    const sp = uplSpId(body.link);
    if (!sp) return answer(400, { error: 'That isn’t a Spotify playlist link. In Spotify, open the playlist, tap ••• then Share, Copy link.' });
    if (Object.values(U).some(r => r.o === me && r.sp === sp)) return answer(400, { error: 'You’ve added that playlist already.' });
    let meta;
    try { meta = await spotify(await getToken(env), '/playlists/' + sp + '?fields=name,owner(display_name)'); }
    catch (e) { return answer(400, { error: /Spotify 404/.test(String(e && e.message)) ? 'Spotify has no playlist at that link.' : 'Couldn’t reach Spotify just now. Try again in a minute.' }); }
    const pid = randomCode(10), rec = { o: me, sp, n: String(meta.name || '').slice(0, 80), mode: body.mode === 'list' ? 'list' : 'cal', link: uplInvite(body.link), at: Date.now(), base: Date.now(), st: 'join' };
    U[pid] = rec; await uplPut(env, U);
    await uplRead(env, pid, rec, U);
    if (rec.st === 'join') await uplAskJohn(env, all, me, rec);
    return answer(200, { ok: true, id: pid, st: rec.st, pls: await uplMine(env, uplList(all, me, U, BOSS), U) });
  }
  if (body.op === 'splists') {   // their Spotify playlists and Liked Songs, each with its songs, for the import picker, through their own Spotify sign-in (Spotify lets an app read only the signed-in account's playlists)
    const ownerId0 = await env.SOTD.get('owner');
    if (await spotifyWaiting(env)) return answer(200, { error: 'Spotify has asked the site to wait until ' + new Date(spotifyUntil).toLocaleTimeString('en-GB', { timeZone: 'Europe/London', hour: '2-digit', minute: '2-digit' }) + ' (too many requests)' });
    let tokErr = '';
    const mr = me === ownerId0 ? { tok: await getToken(env).catch(e => { tokErr = String(e && e.message || e).slice(0, 120); return null; }), sid: OWNER_ID, liked: false } : await memberSpRead(env, me).catch(() => null);
    if (mr && !mr.tok) return answer(200, { error: 'the site’s Spotify sign-in didn’t work: ' + tokErr });   // (John: the site's own Spotify sign-in, his)
    if (!mr) return answer(200, { connect: true });
    const lists = []; let reads = 0;
    let plErr = '';
    const pls = await myPlaylists(mr.tok).catch(e => { plErr = String(e && e.message || e).slice(0, 120); return null; });
    if (!pls) return answer(200, { error: 'Spotify didn’t give your playlists just now (' + plErr + '). Try again in a minute, or use Exportify below.' });
    const want = (mr.liked ? [{ id: 'liked', name: 'Liked Songs', owner: { id: mr.sid }, images: [], tracks: { total: 1 } }] : []).concat(pls);
    for (const p of want) {
      const total = ((p.tracks || p.items || {}).total) || 0, img = ((p.images || [])[0] || {}).url || '', mine = p.id === 'liked' || !!(p.owner && p.owner.id === mr.sid) || !!p.collaborative;
      if (!total) continue;
      const base = { spid: p.id, name: p.name, owner: (p.owner && p.owner.display_name) || '', mine, img };
      if (reads + Math.ceil(total / 100) > 8) {   // (a few read here, the rest by the page, one request each: a Worker's requests to Spotify are limited per request)
       lists.push(Object.assign(base, { total, later: true })); continue; }
      reads += Math.ceil(total / 100);
      const items = await spReadList(mr.tok, p.id).catch(() => null);
      if (items && items.length) lists.push(Object.assign(base, { songs: items.slice(0, UPL_CSV_MOST) }));
    }
    return answer(200, { user: mr.sid, lists });
  }
  if (body.op === 'splist') {   // one playlist's songs (one too big to bring with the rest)
    const id = String(body.spid || ''); if (!/^[0-9A-Za-z]{22}$/.test(id) && id !== 'liked') return answer(400, { error: 'Which playlist?' });
    const mr = me === (await env.SOTD.get('owner')) ? { tok: await getToken(env) } : await memberSpRead(env, me).catch(() => null), items = mr ? await spReadList(mr.tok, id).catch(() => null) : null;
    return items ? answer(200, { songs: items.slice(0, UPL_CSV_MOST) }) : answer(200, { error: 'Spotify won’t let us read that playlist.' });
  }
  if (body.op === 'csv' || body.op === 'csvup') await uplCsvPreviews(env, body.songs).catch(() => null);   // (any previews Exportify gave)
  if (body.op === 'csv') {   // one of their Spotify playlists from an Exportify file
    const items = uplCsvSongs(body.songs);
    if (!items || !items.length) return answer(400, { error: 'No songs in that file. Choose the file Exportify saved for your playlist.' });
    // as many as they like (John, 1 Nov 2026: an Exportify playlist said it would "take the place of" theirs; the one-at-a-time
    // rule is gone): replace (an id of theirs) still takes one's place, keeping who sees it and its notifications
    const had = body.replace ? Object.entries(U).find(([pid, r]) => pid === String(body.replace) && r.o === me && r.src === 'csv') : null;
    const keep = had ? { share: had[1].share, to: had[1].to, send: had[1].send, prev: (had[1].prev || []).concat(had[0]).slice(-10) } : {};   // (prev: the ids it had, for its /yt/ link)
    if (had) { await uplDrop(env, [had[0]]); delete U[had[0]]; }
    const now = Date.now(), newest = Math.max.apply(null, items.map(x => Date.parse(x.a)));
    const pid = randomCode(10), rec = { o: me, src: 'csv', sp: '', spid: /^([0-9A-Za-z]{22}|liked)$/.test(String(body.spid || '')) ? String(body.spid) : undefined, n: String(body.name || '').replace(/\s+/g, ' ').trim().slice(0, 80) || 'My playlist', fk: uplFileKey(body.file), mode: body.mode === 'list' ? 'list' : 'cal', at: now, base: Math.max(newest, 0), st: 'ok' };
    for (const k of Object.keys(keep)) if (keep[k] !== undefined) rec[k] = keep[k];
    U[pid] = rec;
    await uplTake(env, pid, rec, U, items, 'new', now, true);
    return answer(200, { ok: true, id: pid, st: 'ok', pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'fromlist') {   // one of their saved playlists as its own calendar or playlist
    const lists = (await env.SOTD.get('mpl:' + me, 'json')) || [], l = lists.find(x => x.id === String(body.list || ''));
    if (!l) return answer(404, { error: 'No such playlist of yours.' });
    if (Object.values(U).some(r => r.o === me && r.src === 'mpl' && r.pl === l.id)) return answer(400, { error: 'It’s on the calendar already.' });
    const pid = randomCode(10), now = Date.now(), rec = { o: me, src: 'mpl', pl: l.id, n: l.name, mode: body.mode === 'list' ? 'list' : 'cal', at: now, base: now, st: 'ok', read: now, tried: now };
    U[pid] = rec;
    await uplListSync(env, pid, rec, l, now);
    delete rec.chg; await uplPut(env, U);
    await startJob(env, null, null, 0, null, true).catch(() => {});   // the site job: its videos, moods, years and YouTube copy
    return answer(200, { ok: true, id: pid, st: 'ok', pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'jobs') {   // John: the site job finding videos, years and moods for members' playlists: running (at which step), or how its last run ended and, failed, where and why (John, 3 Nov 2026: "If it fails i'd like to know why")
    if (me !== (await env.SOTD.get('owner'))) return answer(403, { error: 'Only John can see that.' });
    const hit = await env.SOTD.get('jobsee', 'json'), yt = await env.SOTD.get('ytprog', 'json');
    if (hit && Date.now() - hit.at < 45e3) return answer(200, Object.assign({}, hit.v, { yt }));
    try {
      const runs = (await ghApi(env, '/actions/workflows/song-tags.yml/runs?per_page=5')).workflow_runs || [];
      const r = runs.find(x => x.status !== 'completed') || runs[0];
      if (!r) return answer(200, { run: null });
      const jj = await ghApi(env, '/actions/runs/' + r.id + '/jobs'), out = { st: r.status, res: r.conclusion || '', at: r.run_started_at || r.created_at, end: r.updated_at, url: r.html_url, n: r.run_number, jobs: [] };
      for (const j of jj.jobs || []) {
        const now = (j.steps || []).find(x => x.status === 'in_progress'), bad = (j.steps || []).filter(x => BAD_RUN.includes(x.conclusion)).map(x => x.name);
        const o = { name: j.name, st: j.status, res: j.conclusion || '', step: now ? now.name : '', since: now ? now.started_at : '', bad, url: j.html_url };
        if (bad.length) {   // why: the last lines of its log that say what went wrong
          try {
            const lr = await fetch('https://api.github.com/repos/' + REPO + '/actions/jobs/' + j.id + '/logs', { headers: { Authorization: 'Bearer ' + env.GH_TOKEN, Accept: 'application/vnd.github+json', 'User-Agent': 'SongOfTheDayWorker' } });
            if (lr.ok) { const lines = (await lr.text()).split('\n').map(l => l.replace(/^\S+Z\s/, '').trim()).filter(Boolean); const err = lines.filter(l => /error|fail|quota|denied|refused|exceeded|traceback|exception|not found|forbidden|timed out|429|403|500/i.test(l) && !/^##\[group\]|Post job/.test(l)); o.why = (err.length ? err : lines).slice(-6).map(l => l.slice(0, 300)); }
          } catch (e) {}
        }
        out.jobs.push(o);
      }
      const v = { run: out };
      await kvSoft(env, 'jobsee', JSON.stringify({ at: Date.now(), v }), { expirationTtl: 300 });
      return answer(200, Object.assign({}, v, { yt }));
    } catch (e) { return answer(200, { run: null, error: String(e && e.message || e) }); }
  }
  if (body.op === 'retry') {   // John's Look again, from Missing (John, 3 Nov 2026: "restart a job on any playlist that has missing items ... Only me"): its songs with no preview or video looked for again
    if (me !== (await env.SOTD.get('owner'))) return answer(403, { error: 'Only John can do that.' });
    const pid = String(body.id || ''), rec = U[pid]; if (!rec) return answer(404, { error: 'No such playlist.' });
    const data = await uplSongs(env, pid), k = ['p', 'v'].includes(body.k) ? body.k : 'pv', meta = (await env.SOTD.get('uplmeta', 'json')) || {}, J = await uplJohnAll(env), ed = data.ed || {};
    const pv = Object.assign({}, pvMem, (await env.SOTD.get('previews', 'json')) || {}), songs = (data.songs || []).filter(x => !uplGone(data, x));
    const noPv = songs.filter(x => pv[x.id] === '-' || !(x.id in pv)), noVid = songs.filter(x => !(meta[x.id] && meta[x.id].vid) && !(J[x.id] && J[x.id].v) && !(ed[x.id] && ed[x.id].v));
    let np = 0, nv = 0;
    if (k.includes('p')) { for (const x of noPv) if (pv[x.id] === '-') { delete pv[x.id]; np++; } if (np) { for (const x of noPv) delete pvMem[x.id]; await kvSoft(env, 'previews', JSON.stringify(pv)); } { const ak = (await env.SOTD.get('pvit', 'json')) || {}; let c = 0; for (const x of noPv) if (ak[x.id]) { delete ak[x.id]; c++; } if (c) await kvSoft(env, 'pvit', JSON.stringify(ak)); }   /* (Apple asked again too) */ await fillPreviews(env, noPv, UPL_PV_MOST, pv).catch(() => null); }
    if (k.includes('v') && noVid.length) {
      data.retry = data.retry || {}; const now = Date.now(); for (const x of noVid) { data.retry[x.id] = now; nv++; }
      if (data.skip) for (const x of noVid) if (data.skip[x.id]) { data.skip[x.id] = data.skip[x.id].replace('v', ''); if (!data.skip[x.id]) delete data.skip[x.id]; }
      // a "no video" left on them (its owner's or John's: Gauja's, from when its videos came from John's own YouTube playlist, John, 5 Nov 2026) taken off too, so they're searched for
      let jch = false;
      for (const x of noVid) {
        if (ed[x.id] && ed[x.id].v === '') { delete ed[x.id].v; if (!Object.keys(ed[x.id]).length) delete ed[x.id]; }
        if (J[x.id] && J[x.id].v === '') { delete J[x.id].v; if (!Object.keys(J[x.id]).length) delete J[x.id]; jch = true; }
      }
      if (data.ed) data.ed = ed;
      if (jch) await env.SOTD.put('uplJohn', JSON.stringify(J));
      await env.SOTD.put('upl:' + pid, JSON.stringify(data));
      const seen = (await env.SOTD.get('uplseen', 'json')) || {}; for (const x of noVid) if (seen[x.id]) seen[x.id] = seen[x.id].replace('v', ''); await kvSoft(env, 'uplseen', JSON.stringify(seen));
      rec.ch = now; rec.chw = { k: 'retry', n: nv }; await uplPut(env, U);
      await startJob(env, null, null, 0, null, true).catch(() => {});
    }
    return answer(200, { ok: true, previews: np, videos: nv, view: await uplView(env, all, me, pid, rec) });
  }
  const pid = String(body.id || ''), rec = own(pid) || (U[pid] && all[U[pid].o] && ((UPL_CO_OPS.includes(body.op) && uplCo(U[pid], me) && !apart(all, me, U[pid].o)) || BOSS) ? U[pid] : null);   // (John: anyone's, everything its owner can: John, 7 Nov 2026)   // (John: anyone's, as a collaborator)   // (a collaborator: its songs)
  if (!rec) return answer(404, { error: 'No such playlist of yours.' });
  if (body.op === 'collab') {   // who can edit it, its owner says: cw (member ids); the new ones told
    const cw = [...new Set((Array.isArray(body.cw) ? body.cw : []).map(String))].filter(k => all[k] && k !== rec.o && !apart(all, rec.o, k)).slice(0, 20);
    const fresh = cw.filter(k => !(rec.cw || []).includes(k));
    if (cw.length) rec.cw = cw; else delete rec.cw;
    await uplPut(env, U);
    if (fresh.length && rec.st === 'ok') await pushAll(env, { title: all[rec.o].name + ' added you to “' + uplName(all, rec) + '”', body: 'You can edit it now: its songs, their pictures, details, videos and facts. Open it and tap Edit.', url: SITE_SONGS + '#u=' + pid, tag: 'sotd-collab-' + pid }, fresh, { kind: 'member', direct: true, via: { t: 'member', name: all[me].name } }).catch(() => {});
    return answer(200, { ok: true, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: rec.st === 'ok' ? await uplView(env, all, me, pid, rec) : null });
  }
  if (rec.src === 'mpl' && ['link', 'check', 'signoff', 'signoffall', 'order', 'title', 'del'].includes(body.op)) return answer(400, { error: 'That’s done in the playlist itself.' });
  if (rec.src === 'csv' && ['link', 'check'].includes(body.op)) return answer(400, { error: 'Upload a fresh export from Exportify instead.' });
  if (body.op === 'jfdrop') {   // John: a song's downloaded MP3 (k a) or MP4 (k v) deleted, a fresh copy fetched (again) or none from now on (John, 5 Nov 2026: "the ability to delete other peoples music")
    if (!await uplAdm(env, me)) return answer(403, { error: 'Only John can do that.' });
    const t = String(body.track || ''), k = body.k === 'v' ? 'v' : 'a', data = await uplSongs(env, pid);
    if (!(data.songs || []).some(y => y.id === t)) return answer(404, { error: 'That song isn’t on this playlist.' });
    const c = await catalog(env); if (!(c[t] || {})[k] || !env.MEDIA) return answer(404, { error: 'It has no file of its own of that kind (another copy’s are left as they are).' });
    const keys = k === 'a' ? ['a/' + t + '.mp3', 'l/' + t + '.m4a'] : ['v/' + t + '.mp4', 'h/' + t + '.mp4'];
    for (const key of keys) await env.MEDIA.delete(key).catch(() => {});
    await catalogSet(env, keys[0], 0);
    const sk = k === 'a' ? 'a' : 'f';
    data.skip = data.skip || {};
    if (body.again) { if (data.skip[t]) { data.skip[t] = data.skip[t].replace(sk, ''); if (!data.skip[t]) delete data.skip[t]; } await env.SOTD.put('r2want', JSON.stringify({ at: Date.now(), key: keys[0] })); }
    else if (!(data.skip[t] || '').includes(sk)) data.skip[t] = (data.skip[t] || '') + sk;   // (none from now on: the download job leaves it)
    await env.SOTD.put('upl:' + pid, JSON.stringify(data));
    return answer(200, { ok: true, view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'unskip') {   // the songs marked can't be found (k: which), back on its Missing and looked for again
    const k = String(body.k || ''), data = await uplSongs(env, pid);
    if (!/^[pvaf]$/.test(k)) return answer(400, { error: 'Which?' });
    let n = 0;
    for (const t of Object.keys(data.skip || {})) if (data.skip[t].includes(k)) { data.skip[t] = data.skip[t].replace(k, ''); if (!data.skip[t]) delete data.skip[t]; n++; }
    if (n) await env.SOTD.put('upl:' + pid, JSON.stringify(data));
    return answer(200, { ok: true, n, view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'skip') {   // its owner stops the look for one song's preview (p), video (v), moods (m) or year (y): counted as none found
    const k = String(body.k || ''), data = await uplSongs(env, pid), t = String(body.track || '');
    if (!/^[pvmyaf]$/.test(k)) return answer(400, { error: 'Which look?' });   // (a, f: its MP3 or MP4 can't be found: off its Missing: John, 5 Nov 2026)
    if (!(data.songs || []).some(y => y.id === t)) return answer(404, { error: 'That song isn’t on this playlist.' });
    data.skip = data.skip || {};
    if (!(data.skip[t] || '').includes(k)) data.skip[t] = (data.skip[t] || '') + k;
    await env.SOTD.put('upl:' + pid, JSON.stringify(data));
    return answer(200, { ok: true, view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'del') {   // songs off its calendar or playlist (not off Spotify): their days left empty (tracks: several at once)
    const want = Array.isArray(body.tracks) ? body.tracks.slice(0, 2000).map(String) : [String(body.track || '')];
    const data = await uplSongs(env, pid), xs = (data.songs || []).filter(y => want.includes(y.id) && !uplGone(data, y));
    if (!xs.length) return answer(404, { error: want.length > 1 ? 'Those songs aren’t on this playlist.' : 'That song isn’t on this playlist.' });
    const ids = xs.map(x => x.id);
    data.del = data.del || {}; xs.forEach(x => { data.del[x.id] = x.a; });
    if (data.order) data.order = data.order.filter(t => !ids.includes(t));
    await uplFilesGone(env, pid, data, ids);   // (its files too, unless it's saved to a playlist elsewhere)
    await env.SOTD.put('upl:' + pid, JSON.stringify(data));
    rec.cnt = (data.songs || []).filter(y => !uplGone(data, y)).length; await uplPut(env, U);
    await startJob(env, null, null, 0, null, true).catch(() => {});   // (its cards, and its YouTube copy)
    return answer(200, { ok: true, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'undel') {   // songs taken off it put back on their days (from an update's "not put back")
    const want = new Set((Array.isArray(body.tracks) ? body.tracks : []).slice(0, 2000).map(String));
    const data = await uplSongs(env, pid); let n = 0;
    for (const t of want) if (data.del && data.del[t]) { delete data.del[t]; n++; }
    if (n) {
      await env.SOTD.put('upl:' + pid, JSON.stringify(data));
      rec.cnt = (data.songs || []).filter(y => !uplGone(data, y)).length; rec.ch = Date.now(); await uplPut(env, U);
      await startJob(env, null, null, 0, null, true).catch(() => {});   // (their videos and files again)
    }
    return answer(200, { ok: true, back: n, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'csvup') {   // a fresh export of it: new songs wait for them to sign off, the rest keep their days
    if (rec.src !== 'csv') return answer(400, { error: 'That one is read from Spotify by itself.' });
    const items = uplCsvSongs(body.songs);
    if (!items || !items.length) return answer(400, { error: 'No songs in that file. Choose the file Exportify saved for your playlist.' });
    if (body.file) rec.fk = uplFileKey(body.file);
    if (/^([0-9A-Za-z]{22}|liked)$/.test(String(body.spid || ''))) rec.spid = String(body.spid);   // (from Spotify itself: read from it from now on)
    const d0 = await uplSongs(env, pid);
    if (d0.add) { const ids = new Set(items.map(x => x.id)); for (const x of d0.songs || []) if (d0.add[x.id] && !ids.has(x.id)) items.push(x); }   // (songs added to it from other playlists stay)
    const r = await uplTake(env, pid, rec, U, items, rec.st, Date.now(), true);
    // songs in the download its owner took off the calendar: not put back, and said so (John, 3 Nov 2026: "it should be
    // flagged on import again, that it's not being put back"), with Put them back (op undel) on the page
    const d1 = await uplSongs(env, pid), inFile = new Set(items.map(x => x.id)), offKept = (d1.songs || []).filter(x => inFile.has(x.id) && uplGone(d1, x));
    return answer(200, { ok: true, fresh: r.fresh, cnt: rec.cnt, off: offKept.length, offSongs: offKept.slice(0, 40).map(x => ({ id: x.id, title: x.title, artist: x.artist })), pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'csvadd') {   // another playlist's songs added to this one (John, 2 Nov 2026: "choose whether to import it into a new calendar/ playlist, or add it to an existing one"): those it hasn't got, kept through its own updates (data.add)
    if (rec.src !== 'csv') return answer(400, { error: rec.src === 'sp' ? 'That one is read from Spotify by itself: add songs to it in Spotify.' : 'Add songs to that one in the playlist itself.' });
    const items = uplCsvSongs(body.songs);
    if (!items || !items.length) return answer(400, { error: 'No songs in that file. Choose the file Exportify saved for your playlist.' });
    const data = await uplSongs(env, pid), have = new Set((data.songs || []).map(x => x.id)), add = [], back = [];
    for (const x of items) {
      if (!have.has(x.id)) { add.push(x); have.add(x.id); }
      else if (data.del && data.del[x.id]) back.push(x.id);   // (one taken off before: on again, as it was asked for)
    }
    if (body.file) { const k = uplFileKey(body.file); if (k && k !== rec.fk) { rec.adds = [...new Set((rec.adds || []).concat(k))].slice(0, 30); await uplPut(env, U); } }   // (the playlists added to it, so a fresh download updates them too: John, 3 Nov 2026)
    if (!add.length && !back.length) return answer(200, { ok: true, added: 0, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) });
    data.add = data.add || {}; for (const x of add) data.add[x.id] = 1;
    for (const t of back) delete data.del[t];
    if (data.order) data.order = add.map(x => x.id).concat(data.order);   // (a playlist: the new ones first, as its newest are)
    await env.SOTD.put('upl:' + pid, JSON.stringify(data));
    await uplTake(env, pid, rec, U, (data.songs || []).concat(add), rec.st, Date.now(), true);
    return answer(200, { ok: true, added: add.length + back.length, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'mode') { rec.mode = body.mode === 'list' ? 'list' : 'cal'; delete rec.annDay; await uplPut(env, U); return answer(200, { ok: true, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) }); }
  if (body.op === 'link') {   // the collaboration invite link, sent again
    const link = uplInvite(body.link);
    if (!link || uplSpId(link) !== rec.sp) return answer(400, { error: 'That isn’t this playlist’s invite link.' });
    rec.link = link; await uplPut(env, U);
    if (rec.st !== 'ok') await uplAskJohn(env, all, me, rec);
    return mine();
  }
  if (body.op === 'remove') { await uplDrop(env, [pid]); delete U[pid]; return mine(); }
  if (body.op === 'check') {
    if (Date.now() - (rec.tried || 0) < UPL_CHECK_GAP) return answer(429, { error: 'Looked a moment ago. Try again in a minute.' });
    await uplRead(env, pid, rec, U);
    return answer(200, Object.assign({ ok: true, st: rec.st, pls: await uplMine(env, uplList(all, me, U, BOSS), U) }, rec.st === 'ok' ? { view: await uplView(env, all, me, pid, rec) } : {}));
  }
  if (body.op === 'signoffall') {   // every waiting song signed off at once (John, 3 Nov 2026: "I can't sign them all off at once"): out for everyone; one message to the members at most, for the newest, not one each
    const data = await uplSongs(env, pid), wait = (data.songs || []).filter(y => uplWaiting(rec, data, y) && !uplGone(data, y));
    if (!wait.length) return answer(200, { ok: true, n: 0, view: await uplView(env, all, me, pid, rec), pls: await uplMine(env, uplList(all, me, U, BOSS), U) });
    if (!await dayCount(env, 'uplsign:' + me, UPL_SIGNOFF_DAY)) return answer(429, { error: 'That’s ' + UPL_SIGNOFF_DAY + ' sign-offs today, the most for a day.' });
    const last = wait[wait.length - 1];
    data.rel = data.rel || {}; data.reld = data.reld || {}; data.ann = data.ann || {};
    const now = Date.now(), today = londonDay();
    for (const y of wait) if (y !== last) { data.rel[y.id] = now; data.reld[y.id] = today; data.ann[y.id] = now; }   // (out, with no message of their own)
    const r = await uplSignoffDo(env, all, pid, rec, data, last);   // (the newest: as one sign-off, its message as its owner chose)
    return answer(200, Object.assign({ ok: true, n: wait.length, sent: r.sent, view: await uplView(env, all, me, pid, rec), pls: await uplMine(env, uplList(all, me, U, BOSS), U) }, r.on ? { on: r.on, at: r.at } : {}));
  }
  if (body.op === 'signoff') {
    const data = await uplSongs(env, pid), x = (data.songs || []).find(y => y.id === String(body.track || '') && !uplGone(data, y));
    if (!x) return answer(404, { error: 'That song isn’t on this playlist.' });
    if (!uplWaiting(rec, data, x)) return answer(400, { error: 'That song is out already.' });
    if (!await dayCount(env, 'uplsign:' + me, UPL_SIGNOFF_DAY)) return answer(429, { error: 'That’s ' + UPL_SIGNOFF_DAY + ' today, the most for a day.' });
    const r = await uplSignoffDo(env, all, pid, rec, data, x);
    return answer(200, Object.assign({ ok: true, sent: r.sent, view: await uplView(env, all, me, pid, rec), pls: await uplMine(env, uplList(all, me, U, BOSS), U) }, r.on ? { on: r.on, at: r.at } : {}));
  }
  if (body.op === 'dltry') {   // a song's files asked for again: off the download job's failed list and its outcome cleared, so its next run tries
    const t = String(body.track || ''); if (!ID_RE.test(t) || !env.MEDIA) return answer(400, { error: 'Which song?' });
    const so = await env.MEDIA.get('playlist-r2/status.json'), st = so ? await so.json().catch(() => ({})) : {}, e = (st.tracks || {})[t];
    if (e) { delete st.tracks[t]; await env.MEDIA.put('playlist-r2/status.json', JSON.stringify(st), { httpMetadata: { contentType: 'application/json' } }); dlMem = null; }
    if (e && e.v) {
      const fo = await env.MEDIA.get('playlist-r2/failed.txt');
      if (fo) {
        const lines = (await fo.text()).split('\n'), keep = [];
        for (const l of lines) { if (l.trim() === 'https://www.youtube.com/watch?v=' + e.v) { if (keep.length && keep[keep.length - 1].startsWith('#')) keep.pop(); continue; } keep.push(l); }
        await env.MEDIA.put('playlist-r2/failed.txt', keep.join('\n'), { httpMetadata: { contentType: 'text/plain; charset=utf-8' } });
      }
    }
    return answer(200, { ok: true, view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'dayorder') {   // the order of one day's songs, by its owner (or a collaborator)
    if (rec.mode === 'list') return answer(400, { error: 'A playlist has no days.' });
    const day = String(body.day || ''), order = (Array.isArray(body.order) ? body.order : []).map(String).filter(t => ID_RE.test(t)).slice(0, 200);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || order.length < 2) return answer(400, { error: 'Which day, and its songs in order?' });
    rec.dord = rec.dord || {}; rec.dord[day] = order;
    const ks = Object.keys(rec.dord); if (ks.length > 400) for (const k of ks.sort().slice(0, ks.length - 400)) delete rec.dord[k];
    await uplPut(env, U);
    return answer(200, { ok: true, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'place') {   // several songs' days at once (a drag: the one moved, and those shifting a day into its gap), by its owner
    if (rec.mode === 'list') return answer(400, { error: 'A playlist has no days: make it a calendar first.' });
    const data = await uplSongs(env, pid), live = new Set((data.songs || []).filter(x => !uplGone(data, x)).map(x => x.id)), want = body.days && typeof body.days === 'object' ? body.days : {};
    const pairs = Object.entries(want).filter(([t, d]) => live.has(t) && /^\d{4}-\d{2}-\d{2}$/.test(String(d)) && !isNaN(Date.parse(d + 'T12:00:00Z')));
    if (!pairs.length) return answer(400, { error: 'Nothing to move.' });
    rec.dd = rec.dd || {};
    if (Object.keys(rec.dd).length + pairs.length > UPL_CSV_MOST) return answer(400, { error: 'That’s as many songs moved as a calendar can have.' });
    for (const [t, d] of pairs) rec.dd[t] = d;
    delete rec.annDay;
    await uplPut(env, U);
    await startJob(env, null, null, 0, null, true).catch(() => {});
    return answer(200, { ok: true, moved: pairs.length, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'move' || body.op === 'unmove') {   // a song to another day, by its owner (John, 1 Nov 2026: "an edit mode on calendar playlists where you can drag and drop to different dates"); one there already takes its day; unmove: every song back to the setup's day
    if (rec.mode === 'list') return answer(400, { error: 'A playlist has no days: make it a calendar first.' });
    if (body.op === 'unmove') { delete rec.dd; delete rec.annDay; await uplPut(env, U); return answer(200, { ok: true, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) }); }
    const day = String(body.day || ''), data = await uplSongs(env, pid);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || isNaN(Date.parse(day + 'T12:00:00Z'))) return answer(400, { error: 'Choose a day.' });
    const days = uplDays(data.songs || [], rec), live = days.filter(x => !uplGone(data, x)), x = live.find(y => y.id === String(body.track || ''));
    if (!x) return answer(404, { error: 'That song isn’t on this calendar.' });
    if (x.d === day) return answer(200, { ok: true, view: await uplView(env, all, me, pid, rec) });
    const there = live.find(y => y.d === day && y.id !== x.id);
    rec.dd = rec.dd || {};
    if (Object.keys(rec.dd).length >= UPL_CSV_MOST) return answer(400, { error: 'That’s as many songs moved as a calendar can have.' });
    rec.dd[x.id] = day;
    if (there) rec.dd[there.id] = x.d;
    delete rec.annDay;   // (today's song looked at again)
    await uplPut(env, U);
    await startJob(env, null, null, 0, null, true).catch(() => {});   // (its cards, and its YouTube list, follow)
    return answer(200, { ok: true, swapped: there ? { id: there.id, title: there.title, d: x.d } : null, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'dates') {
    if (rec.mode === 'list') return answer(400, { error: 'A playlist has no days: make it a calendar first.' });
    delete rec.dd;   // (a new setup: its days, not the ones moved by hand)
    const how = ['added', 'fwd', 'back'].includes(body.how) ? body.how : '', day = String(body.day || '');
    if (!how) return answer(400, { error: 'Say how: added, fwd or back.' });
    if (how === 'added') delete rec.dates;
    else {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || isNaN(Date.parse(day + 'T12:00:00Z'))) return answer(400, { error: 'Choose a day.' });
      const songs = (await uplSongs(env, pid)).songs || [];
      rec.dates = !songs.length ? { how, day } : how === 'fwd' ? { how, day, at: songs[0].id, n: 1 } : { how, day, at: songs[songs.length - 1].id, n: songs.length };
    }
    delete rec.annDay;   // (today's song looked at again)
    await uplPut(env, U);
    return answer(200, { ok: true, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'send') {
    const at = String(body.at || '08:00');
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(at)) return answer(400, { error: 'Choose a time.' });
    // how (John, 7 Nov 2026: "more options than the one a day at 8am"): now, as each song comes out; day, at its time on the
    // chosen days (days: 1 Monday to 7 Sunday; a skipped day's songs go on the next chosen one); week, one message of the
    // week's songs on day wd at its time. since: from when, so changing it never sends what came before
    const how = ['now', 'day', 'week', 'all'].includes(body.how) ? body.how : 'day';   // (all: once every one of who has added a new song, theirs together: John, 7 Nov 2026)
    const who = [...new Set((Array.isArray(body.who) ? body.who : []).map(String))].filter(k => all[k]).slice(0, 20);
    if (body.on && how === 'all' && !who.length) return answer(400, { error: 'Choose who adds songs.' });
    const days = [...new Set((Array.isArray(body.days) ? body.days : [1, 2, 3, 4, 5, 6, 7]).map(Number).filter(n => n >= 1 && n <= 7))].sort();
    if (body.on && how === 'day' && !days.length) return answer(400, { error: 'Choose at least one day.' });
    const wd = Number(body.wd) >= 1 && Number(body.wd) <= 7 ? Number(body.wd) : 1;
    rec.send = { on: !!body.on, at, how, days, wd, who, since: Date.now() };
    delete rec.annDay;
    await uplPut(env, U);
    return answer(200, { ok: true, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'title') {   // its title on the calendar: theirs, or '' for Spotify's name
    const rec = own(body.id); if (!rec) return answer(404, { error: 'No such playlist of yours.' });
    const t = String(body.title || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    if (t && t !== rec.n) rec.title = t; else delete rec.title;
    await uplPut(env, U);
    return answer(200, { ok: true, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: rec.st === 'ok' ? await uplView(env, all, me, body.id, rec) : null });
  }
  if (body.op === 'caption') {   // a line or two under its name on the calendar (John, 6 Nov 2026: "put a caption on a calendar page or on a song page"); '' none
    const rec = own(body.id); if (!rec) return answer(404, { error: 'No such playlist of yours.' });
    const c = String(body.cap || '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim().slice(0, 300);
    if (c) rec.cap = c; else delete rec.cap;
    await uplPut(env, U);
    return answer(200, { ok: true, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: rec.st === 'ok' ? await uplView(env, all, me, body.id, rec) : null });
  }
  if (body.op === 'share') {   // who sees it: public (anyone with its link, and every member), members (every member), some (the members in to), private (just them)
    const rec = own(body.id); if (!rec) return answer(404, { error: 'No such playlist of yours.' });
    if (!UPL_SHARES.includes(body.share)) return answer(400, { error: 'Public, members, some or private?' });
    const to = body.share === 'some' ? [...new Set((Array.isArray(body.to) ? body.to : []).map(String))].filter(k => all[k] && k !== rec.o) : [];
    if (body.share === 'some' && !to.length) return answer(400, { error: 'Choose at least one member.' });
    const was = uplShareOf(rec);
    rec.share = body.share; if (to.length) rec.to = to; else delete rec.to;
    await uplPut(env, U);
    if (was !== body.share && (was === 'public' || body.share === 'public') && rec.st === 'ok') await startJob(env, null, null, 0, null, true).catch(() => {});   // (its cards for links made, or taken off the site)
    return answer(200, { ok: true, pls: await uplMine(env, uplList(all, me, U, BOSS), U), view: rec.st === 'ok' ? await uplView(env, all, me, body.id, rec) : null });
  }
  if (body.op === 'facts') {   // a song's facts: s (about the song), a { <artist id>: [...] } (about each artist), as edited; sign: out for everyone
    const data = await uplSongs(env, pid), t = String(body.track || '');
    if (!(data.songs || []).some(y => y.id === t)) return answer(404, { error: 'That song isn’t on this playlist.' });
    const all_ = (await env.SOTD.get('ufacts:' + pid, 'json')) || {}, e = all_[t] || {};   // (none yet: their own, added in the editor, John, 3 Nov 2026)
    const was = e.w || e.ok || { s: [], a: [] }, clean = l => (Array.isArray(l) ? l : []).map(x => String(x || '').replace(/\s+/g, ' ').trim().slice(0, 400)).filter(Boolean).slice(0, 20);   // (20: theirs and new ones together)
    const got = { s: clean(body.s), a: (was.a || []).map(x => ({ id: x.id, n: x.n, f: clean(((body.a || {})[x.id])) })) };
    if (body.sign) { e.ok = got; delete e.w; delete e.nw; e.sat = Date.now(); } else if (e.w) e.w = got; else { e.ok = got; delete e.none; }
    all_[t] = e; await env.SOTD.put('ufacts:' + pid, JSON.stringify(all_));
    return answer(200, { ok: true, view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'askfacts') {   // facts for one of its older songs (a public one's), written as the new ones' are
    if (uplShareOf(rec) !== 'public') return answer(400, { error: 'Facts are written for public playlists’ songs.' });
    const data = await uplSongs(env, pid), t = String(body.track || '');
    if (!(data.songs || []).some(y => y.id === t)) return answer(404, { error: 'That song isn’t on this playlist.' });
    if (!await dayCount(env, 'uplfacts:' + me, 10)) return answer(429, { error: 'That’s 10 asked for today, the most for a day.' });
    data.fask = data.fask || {}; data.fask[t] = Date.now();
    await env.SOTD.put('upl:' + pid, JSON.stringify(data));
    await startJob(env, null, null, 0, null, true).catch(() => {});
    return answer(200, { ok: true, view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'edit') {   // a song's video (v: a YouTube id, '' none, null the one found), moods and genres (m, g: up to two each, [] none, null the ones worked out)
    const data = await uplSongs(env, pid), t = String(body.track || '');
    if (!(data.songs || []).some(y => y.id === t)) return answer(404, { error: 'That song isn’t on this playlist.' });
    data.ed = data.ed || {};
    const e = data.ed[t] || {};
    if ('v' in body) {
      if (body.v === null) delete e.v;
      else if (body.v === '' || /^[\w-]{11}$/.test(String(body.v))) e.v = String(body.v);
      else return answer(400, { error: 'That isn’t a YouTube video.' });
    }
    if ('src' in body) {   // a link of another site's (Bandcamp, SoundCloud…) for its MP3, a song YouTube hasn't got (John, 5 Nov 2026: "This is just on bandcamp"); '' or null: none
      const u = String(body.src || '').trim();
      if (!u) delete e.src;
      else if (/^https:\/\/[\w.-]+\.[a-z]{2,}\/\S{1,400}$/i.test(u) && !/youtu\.?be/i.test(u)) e.src = u;
      else return answer(400, { error: 'That isn’t a link to the song on another site.' });
    }
    let refiled = 0;
    if (body.refile && typeof body.v === 'string' && body.v && env.MEDIA) {   // a new video chosen for a song with files already: its MP3 and MP4 from the new one (asked first: John, 5 Nov 2026, "if we change videos with a file there already it probably means I want an updated file")
      const c = await catalog(env), ce = c[t] || {};   // (this song's own downloaded files only: another copy's are never touched)
      for (const [k, keys] of [['a', ['a/' + t + '.mp3', 'l/' + t + '.m4a']], ['v', ['v/' + t + '.mp4', 'h/' + t + '.mp4']]]) {
        if (!ce[k]) continue;
        for (const key of keys) await env.MEDIA.delete(key).catch(() => {});
        await catalogSet(env, keys[0], 0); refiled++;
      }
      if (refiled) await env.SOTD.put('r2want', JSON.stringify({ at: Date.now(), key: 'v/' + t + '.mp4' }));   // (the download job, at Playlist push's next look)
    }
    for (const [k, list] of [['m', MOOD_LIST], ['g', GENRE_LIST]]) {
      if (body[k] === null) delete e[k];
      else if (Array.isArray(body[k])) e[k] = body[k].filter(x => list.includes(x)).slice(0, 2);
    }
    if ('cap' in body) {   // a caption on its song page, by its owner or those editing with them (John, 6 Nov 2026); '' none
      const c = String(body.cap || '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim().slice(0, 300);
      if (c) e.cap = c; else delete e.cap;
      for (const t2 of (Array.isArray(body.capAlso) ? body.capAlso : []).map(String).slice(0, 20)) {   // the day's other songs, the same caption (John, 6 Nov 2026: "the same caption for all")
        if (t2 === t || !(data.songs || []).some(y => y.id === t2)) continue;
        const e2 = data.ed[t2] || {}; if (c) e2.cap = c; else delete e2.cap;
        if (Object.keys(e2).length) data.ed[t2] = e2; else delete data.ed[t2];
      }
    }
    for (const k of ['tt', 'ar']) {   // its title and artist as its owner wants them shown (John, 3 Nov 2026: "change details"); '' or null: Spotify's
      if (!(k in body)) continue;
      const v = String(body[k] || '').replace(/\s+/g, ' ').trim().slice(0, 200);
      if (v) e[k] = v; else delete e[k];
    }
    if (Object.keys(e).length) data.ed[t] = e; else delete data.ed[t];
    await env.SOTD.put('upl:' + pid, JSON.stringify(data));
    return answer(200, { ok: true, view: await uplView(env, all, me, pid, rec) });
  }
  if (body.op === 'order') {
    if (rec.mode !== 'list') return answer(400, { error: 'A calendar’s order is its days.' });
    const data = await uplSongs(env, pid), have = new Set((data.songs || []).map(x => x.id));
    const order = [...new Set((Array.isArray(body.order) ? body.order : []).map(String))].filter(t => have.has(t)).slice(0, 2000);
    data.order = order;
    await env.SOTD.put('upl:' + pid, JSON.stringify(data));
    return answer(200, { ok: true, n: order.length });
  }
  return answer(400, { error: 'Unknown op.' });
}
async function uplAskJohn(env, all, me, rec) {   // John: a playlist for him to join (his inbox and phone), with its invite link on the Members tab
  const owner = await env.SOTD.get('owner');
  if (!owner || !all[owner] || owner === me) return;
  const msg = { title: all[me].name + ' added a Spotify playlist for you to join', body: '“' + (rec.n || 'Their playlist') + '”: open its invite link on the Members tab, join it in Spotify, then tap I’ve joined: check.',
    url: SITE_SONGS + 'admin.html#members', tag: 'sotd-upljoin-' + rec.sp };
  await pushAll(env, msg, [owner], { kind: 'upl', via: { t: 'upl', name: all[me].name } });
}
// Messages: only when its owner chooses (send { on, at }: off unless they turn it on, at the time they choose, UK time).
// A calendar's song goes on its day; a playlist's on the day its owner signs it off; at that time, or at the sign-off
// when that's later.
function uplHM(d) { return new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(d ? new Date(d) : new Date()); }
function uplSendOf(rec) { return Object.assign({ on: false, at: '08:00', how: 'day', days: [1, 2, 3, 4, 5, 6, 7], wd: 1, who: [], since: 0 }, rec.send || {}); }
function uplDow(day) { const n = new Date(day + 'T12:00:00Z').getUTCDay(); return n === 0 ? 7 : n; }   // 1 Monday to 7 Sunday
function uplDayAdd(day, n) { const d = new Date(day + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
function uplSendNow(snd, d, today) {   // a song out on day d: whether it goes to the members as it comes out
  if (!snd.on || d > today) return false;
  if (snd.how === 'now') return true;
  if (snd.how === 'week' || snd.how === 'all') return false;
  return snd.days.includes(uplDow(today)) && (d < today || uplHM() >= snd.at);
}
function uplSongDay(rec, data, days, i) {   // the day its song goes to the members: a calendar's, its day; a playlist's, the day it was signed off (a saved playlist's: added)
  const t = data.songs[i].id;
  if (rec.mode !== 'list') return days[i].d;
  if (rec.src === 'mpl') return Date.parse(data.songs[i].a) > (rec.base || 0) ? londonDay(data.songs[i].a) : '';
  return (data.reld || {})[t] || ((data.rel || {})[t] ? londonDay(data.rel[t]) : '');
}
// ---------- a member's own for their playlist's songs: the video, moods and
// genres (ed in upl:<pid>, the op edit), their own recording and video (R2 ua/<pid>/<track>.mp3, uv/<pid>/<track>.mp4,
// up in parts as John's are: /members/uplup), heard and seen by the members John lets have his own (see on their
// records) and by them, and their own cover (uc/<pid>/<track>.jpg, made small on the page: /members/uplcover; for
// everyone who sees the playlist). files in upl:<pid>: { <track>: { a, v: sizes, c: when } }. ----------
// A member's own MP3s and MP4s go on UPL_OWN_FILES songs at most (not John: KV owner), across all their playlists (John, 1 Nov 2026: "it's
// limited to 366 songs total. So if they have a track with an mp3 they can also add an mp4"): a song counts once, with
// an MP3, an MP4 or both, on any of their playlists; taking its files off, or removing its playlist, frees it.
const UPL_OWN_FILES = 366;
// John can set a member's own allowance on his Members page (John, 3 Nov 2026: "the option to override that limit, myself
// on their member page"): m.fmax, a number of songs (FILES_NO_LIMIT: no limit); without it, UPL_OWN_FILES
const FILES_NO_LIMIT = 100000;
function filesMax(m) { const v = m && m.fmax; return Number.isInteger(v) && v >= 0 ? v : UPL_OWN_FILES; }
async function filesMaxOf(env, id) { return filesMax(((await env.SOTD.get('members', 'json')) || {})[id]); }
async function uplFileSongs(env, member, U) {   // the songs they have their own MP3 or MP4 on
  const out = new Set();
  for (const pid of Object.keys(U)) if (U[pid].o === member) for (const [t, f] of Object.entries((await uplSongs(env, pid)).files || {})) if (f.a || f.v) out.add(t);
  return out;
}
async function uplFileCount(env, member, U) { return (await uplFileSongs(env, member, U)).size; }
// which of a member's songs have MP3s and MP4s, or get them (John, 1 Nov 2026: importing more than their allowance
// "more will show as videos/ previews but not download"): UPL_OWN_FILES songs across all their playlists (not John's
// songs, which have his), those with files already (their own, or the catalog's from playlist-r2) first, then the
// newest added. The rest play as their video or preview. { u: how many that is, allowed: Set, john: Set }; null for John.
async function uplDlPlan(env, member, U) {
  if ((await env.SOTD.get('owner')) === member) return null;
  const john = new Set((((await env.SOTD.get('songsLast', 'json')) || {}).songs || []).map(x => x.id));
  const cat = await catalog(env), own = await uplFileSongs(env, member, U), at = new Map(), most = await filesMaxOf(env, member);
  for (const pid of Object.keys(U)) {
    if (U[pid].o !== member) continue;
    const d = await uplSongs(env, pid);
    for (const x of d.songs || []) if (!uplGone(d, x) && !john.has(x.id) && !at.has(x.id)) at.set(x.id, Date.parse(x.a) || 0);
  }
  const ids = [...at.keys()], has = ids.filter(t => own.has(t) || (cat[t] && (cat[t].a || cat[t].v)));
  // the rest: songs with a video to download from first (newest first), then the others, so the allowance goes on songs
  // that can have files now, not on new ones still waiting for a video (John, 3 Nov 2026: "I'd have expected her to have
  // 366 mp3 and mp4s"; 212 of her newest 366 had no video yet while 269 older ones did)
  const meta = (await env.SOTD.get('uplmeta', 'json')) || {}, JV = await ytJohnVids(), vid = t => !!((meta[t] && meta[t].vid) || (JV[t] && JV[t].v));
  const hs = new Set(has), rest = ids.filter(t => !hs.has(t)).sort((a, b) => (vid(b) - vid(a)) || (at.get(b) - at.get(a)));
  const allowed = new Set(has.concat(rest.slice(0, Math.max(0, most - has.length))));
  return { u: Math.min(most, ids.length), allowed, john };
}
function uplFilesFull(n) { return 'That’s ' + (n || UPL_OWN_FILES) + ' songs with your own MP3 or MP4, the most. Take the files off a song to add another.'; }
const UPL_FILE_MAX = { a: 80e6, v: 1500e6 }, UPL_PARTS_MAX = { a: 10, v: 40 }, UPL_COVER_MAX = 1.5e6;   // (the page sends parts of 10 MB or more, 38 at most)
function uplTooBig(kind) { return 'That’s too big: ' + (kind === 'a' ? 'an MP3' : 'an MP4') + ' can be ' + Math.round(UPL_FILE_MAX[kind] / 1e6) + ' MB at most.'; }
async function uplOwnSong(request, url, env, answer) {   // the owner's playlist and song, or the answer to give
  const who = await memberWho(request, env);
  if (!who) return { no: answer(401, { error: 'Not a member (any more).' }) };
  if (!env.MEDIA) return { no: answer(503, { error: 'Files can’t be added yet.' }) };
  const U = await uplAll(env), pid = url.searchParams.get('id') || '', rec = U[pid], track = url.searchParams.get('track') || '';
  if (!rec || !(rec.o === who.id || (uplCo(rec, who.id) && !apart(who.all, who.id, rec.o)) || await uplAdm(env, who.id))) return { no: answer(404, { error: 'No such playlist of yours.' }) };   // (its owner's, or a collaborator's)
  const data = await uplSongs(env, pid);
  if (!(data.songs || []).some(y => y.id === track)) return { no: answer(404, { error: 'That song isn’t on this playlist.' }) };
  return { who, U, pid, rec, track, data };
}
async function uplFilesSet(env, pid, track, k, v) {
  const data = await uplSongs(env, pid);
  data.files = data.files || {};
  const f = data.files[track] || {};
  if (v) f[k] = v; else delete f[k];
  if (Object.keys(f).length) data.files[track] = f; else delete data.files[track];
  await env.SOTD.put('upl:' + pid, JSON.stringify(data));
}
// a song taken off a playlist (its owner's del, or out of the saved playlist a calendar follows): its owner's MP3, MP4
// and cover for it there are deleted too, unless the song is saved to a playlist elsewhere (any member's saved
// playlists, mpl:<id>), where it goes on playing with them (memberSongs) (John, 1 Nov 2026: "When a song is deleted
// what happens to its files? They should be deleted too, unless they've been saved to a playlist elsewhere").
async function uplTracksSaved(env, tracks) {   // which of these tracks are on a member's saved playlist
  const want = new Set(tracks), out = new Set();
  if (!want.size) return out;
  let cursor;
  do {
    const l = await env.SOTD.list({ prefix: 'mpl:', cursor }).catch(() => ({ keys: [], list_complete: true }));
    for (const k of l.keys || []) for (const pl of (await env.SOTD.get(k.name, 'json')) || []) for (const t of pl.tracks || []) if (want.has(t)) out.add(t);
    cursor = l.list_complete ? null : l.cursor;
  } while (cursor && out.size < want.size);
  return out;
}
async function uplFilesGone(env, pid, data, tracks) {   // tracks just taken off pid: their files deleted unless saved elsewhere; true if data.files changed
  const files = data.files || {}, have = tracks.filter(t => files[t]);
  if (!have.length || !env.MEDIA) return false;
  const saved = await uplTracksSaved(env, have);
  let changed = false;
  for (const t of have) {
    if (saved.has(t)) continue;
    const f = files[t];
    for (const k of ['a', 'v']) if (f[k]) await env.MEDIA.delete('u' + k + '/' + pid + '/' + t + '.' + MEDIA_KINDS[k].ext).catch(() => {});
    if (f.c) await env.MEDIA.delete('uc/' + pid + '/' + t + '.jpg').catch(() => {});
    delete files[t]; changed = true;
  }
  return changed;
}
async function uplSavedTidy(env, tracks) {   // tracks just taken off a saved playlist: files kept only for them, on playlists they're gone from, deleted now
  tracks = [...new Set(tracks)]; if (!tracks.length || !env.MEDIA) return;
  for (const pid of Object.keys(await uplAll(env))) {
    const data = await uplSongs(env, pid), files = data.files || {};
    const off = tracks.filter(t => files[t] && (data.songs || []).some(x => x.id === t && uplGone(data, x)));
    if (off.length && await uplFilesGone(env, pid, data, off)) await env.SOTD.put('upl:' + pid, JSON.stringify(data));
  }
}
async function memberUplUp(request, url, env, answer) {   // ?id&track&kind=a|v&op=start | part (n) | done | abort | drop
  const o = await uplOwnSong(request, url, env, answer);
  if (o.no) return o.no;
  const kind = url.searchParams.get('kind'), op = url.searchParams.get('op'), uid = url.searchParams.get('uploadId') || '';
  if (!MEDIA_KINDS[kind]) return answer(400, { error: 'An MP3 (a) or an MP4 (v)?' });
  const key = 'u' + kind + '/' + o.pid + '/' + o.track + '.' + MEDIA_KINDS[kind].ext;
  const most = await filesMaxOf(env, o.rec.o);
  const fu = async () => ({ n: await uplFileCount(env, o.rec.o, await uplAll(env)), max: john || most >= FILES_NO_LIMIT ? 0 : most });   // (its owner's allowance, whoever adds it)   // (max 0: no limit, and the editor says nothing)
  const john = (await env.SOTD.get('owner')) === o.rec.o;   // (John's own playlists have no limit: John, 1 Nov 2026)
  const byJohn = (await env.SOTD.get('owner')) === o.who.id;   // (John adding a member's missing files, from his own download code: not held to their allowance)
  const over = async () => { if (john || byJohn) return false; const had = await uplFileSongs(env, o.rec.o, await uplAll(env)); return !had.has(o.track) && had.size >= most; };   // (a song with one of theirs already takes the other freely)
  if (op === 'drop') { await env.MEDIA.delete(key); await uplFilesSet(env, o.pid, o.track, kind, 0); return answer(200, { ok: true, fu: await fu(), view: await uplView(env, o.who.all, o.who.id, o.pid, o.rec) }); }
  if (op === 'start') {
    if ((await catalog(env))[o.track] && (await catalog(env))[o.track][kind]) return answer(400, { error: 'Already uploaded.' });   // (John's, from the Songs tab)
    if (await over()) return answer(400, { error: uplFilesFull(most) });
    return answer(200, { uploadId: (await env.MEDIA.createMultipartUpload(key, { httpMetadata: { contentType: MEDIA_KINDS[kind].type } })).uploadId });
  }
  if (!uid) return answer(400, { error: 'Which upload?' });
  const up = env.MEDIA.resumeMultipartUpload(key, uid);
  if (op === 'part') {
    const n = parseInt(url.searchParams.get('n'), 10);
    if (!(n >= 1 && n <= UPL_PARTS_MAX[kind]) || !request.body) return answer(400, { error: n > UPL_PARTS_MAX[kind] ? uplTooBig(kind) : 'Which part?' });
    const part = await up.uploadPart(n, request.body);
    return answer(200, { partNumber: part.partNumber, etag: part.etag });
  }
  if (op === 'done') {
    const body = await request.json().catch(() => ({}));
    const parts = (Array.isArray(body.parts) ? body.parts : []).map(p => ({ partNumber: Number(p.partNumber), etag: String(p.etag) }));
    if (!parts.length) return answer(400, { error: 'No parts.' });
    const obj = await up.complete(parts);
    if (obj.size > UPL_FILE_MAX[kind]) { await env.MEDIA.delete(key); return answer(400, { error: uplTooBig(kind) }); }
    {   // the whole song, not a preview or a Short (John, 7 Nov 2026: "it downloaded songs of only 30 seconds again"), whatever sent it
      const real = await realLen(env, o.track).catch(() => 0), got = real ? await (kind === 'v' ? probeMp4(env, key, obj.size).then(i => i.dur || 0) : mp3Len(env, key, obj.size)).catch(() => 0) : 0;
      const mmss = x => Math.floor(x / 60) + ':' + String(Math.round(x % 60)).padStart(2, '0');
      if (real && got && got < 0.8 * real) { await env.MEDIA.delete(key); return answer(400, { error: 'Only part of the song (' + mmss(got) + ' of ' + mmss(real) + '): not added', short: true }); }
    }
    if (await over()) { await env.MEDIA.delete(key); return answer(400, { error: uplFilesFull(most) }); }   // (others went up meanwhile)
    await uplFilesSet(env, o.pid, o.track, kind, obj.size);
    return answer(200, { ok: true, size: obj.size, fu: await fu(), view: await uplView(env, o.who.all, o.who.id, o.pid, o.rec) });
  }
  if (op === 'abort') { await up.abort(); return answer(200, { ok: true }); }
  return answer(400, { error: 'Unknown step.' });
}
async function memberUplCover(request, url, env, answer) {   // POST ?id&track { pic: a JPEG made small on the page } | ?op=drop
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const o = await uplOwnSong(request, url, env, answer);
  if (o.no) return o.no;
  const key = 'uc/' + o.pid + '/' + o.track + '.jpg';
  if (url.searchParams.get('op') === 'drop') { await env.MEDIA.delete(key); await uplFilesSet(env, o.pid, o.track, 'c', 0); }
  else {
    const body = await request.json().catch(() => ({})), b64 = String(body.pic || '').replace(/^data:image\/jpeg;base64,/, '');
    if (!b64 || b64.length > UPL_COVER_MAX * 4 / 3 || !/^[A-Za-z0-9+/=]+$/.test(b64)) return answer(400, { error: 'The picture is missing or too big.' });
    await env.MEDIA.put(key, Uint8Array.from(atob(b64), ch => ch.charCodeAt(0)), { httpMetadata: { contentType: 'image/jpeg' } });
    await uplFilesSet(env, o.pid, o.track, 'c', Date.now());
  }
  return answer(200, { ok: true, view: await uplView(env, o.who.all, o.who.id, o.pid, o.rec) });
}
// A calendar's own picture, for the Calendars page (John, 1 Nov 2026: "a click that opens a page and shows them nicely.
// Could they have an image that they choose for it, with a default if there isn't"): its owner's photo, cropped square
// on the page (640 pixels at most), in R2 up/<pid>.jpg, its time in pic on the playlist's record; without one, the page
// shows its newest covers (GET /members/upl?cards=1). POST /members/uplpic?id= { pic } | ?op=drop.
async function memberUplPic(request, url, env, answer) {
  if (request.method !== 'POST') return answer(405, { error: 'Use POST.' });
  const who = await memberWho(request, env);
  if (!who) return answer(401, { error: 'Not a member (any more).' });
  if (!env.MEDIA) return answer(503, { error: 'Pictures can’t be added yet.' });
  const U = await uplAll(env), pid = url.searchParams.get('id') || '', rec = U[pid];
  const pop = url.searchParams.get('op');
  if (pop === 'card' && rec && (uplCan(who.all, who.id, rec) || await uplAdm(env, who.id))) {   // the card for a link to it (k: all, a month yyyy-mm or a day yyyy-mm-dd), drawn on the page by whoever shares it: 1200 by 630, for WhatsApp and the rest
    const k = String(url.searchParams.get('k') || ''), body = await request.json().catch(() => ({})), b64 = String(body.pic || '').replace(/^data:image\/jpeg;base64,/, '');
    if (!/^(all|\d{4}-\d{2}|\d{4}-\d{2}-\d{2})$/.test(k)) return answer(400, { error: 'Which card?' });
    if (!b64 || b64.length > UPL_COVER_MAX * 4 / 3 || !/^[A-Za-z0-9+/=]+$/.test(b64)) return answer(400, { error: 'The picture is missing or too big.' });
    if (!await dayCount(env, 'uplcard:' + who.id, 300)) return answer(429, { error: 'That’s a lot of cards for today.' });
    await env.MEDIA.put('up/' + pid + '-c-' + k + '.jpg', Uint8Array.from(atob(b64), ch => ch.charCodeAt(0)), { httpMetadata: { contentType: 'image/jpeg' } });
    const c = rec.cards = rec.cards || {}; c[k] = Date.now();
    const ks = Object.keys(c); if (ks.length > 60) ks.sort((a, b) => c[a] - c[b]).slice(0, ks.length - 60).forEach(x => delete c[x]);
    await uplPut(env, U); return answer(200, { ok: true });
  }
  if (!rec || !(rec.o === who.id || (who.all[rec.o] && await uplAdm(env, who.id)))) return answer(404, { error: 'No such playlist of yours.' });   // (John: anyone's, as its owner: John, 7 Nov 2026)
  if (pop === 'words') {   // its own words on its cover (John, 7 Nov 2026: "the option to change the wording on a playlist cover"): t its name there, b below the rule; '' as it comes
    const body = await request.json().catch(() => ({})), cl = (v, n) => String(v || '').replace(/\s+/g, ' ').trim().slice(0, n);
    if ('t' in body) { const t = cl(body.t, 60); if (t) rec.cvt = t; else delete rec.cvt; }
    if ('b' in body) { const b = cl(body.b, 120); if (b) rec.cvb = b; else delete rec.cvb; }
    await uplPut(env, U); return answer(200, { ok: true, pls: await uplMine(env, uplList(who.all, who.id, U, await uplAdm(env, who.id)), U) });
  }
  if (pop === 'art') {   // its drawn cover, kept for every device (John, 7 Nov 2026: "Can a playlist cover be saved to all devices"): k says what it shows, so a changed one is drawn again
    const body = await request.json().catch(() => ({})), b64 = String(body.art || '').replace(/^data:image\/jpeg;base64,/, '');
    if (!b64 || b64.length > UPL_COVER_MAX * 4 / 3 || !/^[A-Za-z0-9+/=]+$/.test(b64)) return answer(400, { error: 'The picture is missing or too big.' });
    await env.MEDIA.put('up/' + pid + '-art.jpg', Uint8Array.from(atob(b64), ch => ch.charCodeAt(0)), { httpMetadata: { contentType: 'image/jpeg' } });
    rec.art = { k: String(body.k || '').slice(0, 600), v: Date.now() };
    await uplPut(env, U); return answer(200, { ok: true, pls: await uplMine(env, uplList(who.all, who.id, U, await uplAdm(env, who.id)), U) });
  }
  // drop: the album-style picture made for it (the default: John, 7 Nov 2026); covers: its newest covers
  const pst = url.searchParams.get('style');   // album, mos (all its covers small) or cov (its newest four): chosen; drop: as it comes (a collaborative one's covers, else the album style)
  if (pop === 'drop' || pop === 'covers' || pop === 'style') { await env.MEDIA.delete('up/' + pid + '.jpg'); delete rec.pic; delete rec.pcov; delete rec.pst; if (pop === 'covers') rec.pst = 'cov'; else if (pop === 'style' && ['album', 'mos', 'cov'].includes(pst)) rec.pst = pst; }
  else {
    const body = await request.json().catch(() => ({})), b64 = String(body.pic || '').replace(/^data:image\/jpeg;base64,/, '');
    if (!b64 || b64.length > UPL_COVER_MAX * 4 / 3 || !/^[A-Za-z0-9+/=]+$/.test(b64)) return answer(400, { error: 'The picture is missing or too big.' });
    await env.MEDIA.put('up/' + pid + '.jpg', Uint8Array.from(atob(b64), ch => ch.charCodeAt(0)), { httpMetadata: { contentType: 'image/jpeg' } });
    rec.pic = Date.now(); delete rec.pcov; delete rec.pst;
  }
  await uplPut(env, U);
  return answer(200, { ok: true, pls: await uplMine(env, uplList(who.all, who.id, U, await uplAdm(env, who.id)), U) });
}
async function uplPicGet(url, env) {
  const m = /^\/up\/([\w-]{6,40})\.jpg$/.exec(url.pathname);
  const o = m && env.MEDIA ? await env.MEDIA.get('up/' + m[1] + '.jpg') : null;
  if (!o) return new Response('Not here.', { status: 404 });
  return new Response(o.body, { headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'public, max-age=31536000, immutable', 'Access-Control-Allow-Origin': '*' } });
}
async function uplCardCovers(env, all, viewer, U) {   // each playlist they see: its newest four covers (out ones), for the Calendars page
  const out = {}, today = londonDay();
  for (const p of uplList(all, viewer, U)) {
    const rec = U[p.id], data = await uplSongs(env, p.id), days = uplDays(data.songs || [], rec), cv = [];
    const out1 = (data.songs || []).map((x, i) => ({ x, d: days[i].d })).filter(({ x, d }) => !uplGone(data, x) && (rec.o === viewer || (!uplWaiting(rec, data, x) && d <= today)));
    const order = rec.mode === 'list' && (data.order || []).length ? data.order.map(t => out1.find(y => y.x.id === t)).filter(Boolean) : out1.slice().sort((a, b) => a.d < b.d ? 1 : a.d > b.d ? -1 : 0);
    const allc = [], adds = {}, nm = await spWho(env, all);
    for (const { x } of order) { const c = String(x.img || ''); if (c && !allc.includes(c) && allc.length < 36) allc.push(c); if (x.ab && nm[x.ab]) adds[nm[x.ab]] = (adds[nm[x.ab]] || 0) + 1; }
    for (const c of allc) { if (cv.length >= 4) break; cv.push(c); }
    // who's in it (John, 7 Nov 2026: "Could a collaborative playlist have the names of the people"): the members who added its
    // songs, most first, and those editing it with its owner; its owner too, when there's more than one
    const who = Object.keys(adds).sort((a, b) => adds[b] - adds[a]);
    for (const k of (rec.cw || [])) if (all[k] && !who.includes(all[k].name)) who.push(all[k].name);
    if (who.length && all[rec.o] && !who.includes(all[rec.o].name)) who.push(all[rec.o].name);
    out[p.id] = Object.assign({ cv, all: allc }, who.length > 1 ? { who: who.slice(0, 12) } : {});
  }
  return out;
}
async function uplAllSongs(env, all, viewer, U) {   // every song on the playlists they see (out ones; their own, all), for the calendar's search (John, 1 Nov 2026: "Can search in the main section include all songs available to that member")
  const out = [], today = londonDay();
  for (const p of uplList(all, viewer, U)) {
    const rec = U[p.id], data = await uplSongs(env, p.id), days = uplDays(data.songs || [], rec);
    (data.songs || []).forEach((x, i) => {
      if (out.length >= 5000 || uplGone(data, x)) return;
      if (rec.o !== viewer && (uplWaiting(rec, data, x) || days[i].d > today)) return;
      out.push({ id: x.id, title: x.title, artist: x.artist, img: x.img || '', d: days[i].d, pid: p.id, n: p.n, o: p.o, mine: !!p.mine, mode: rec.mode });
    });
  }
  return out;
}
async function uplCoverGet(url, env) {   // a member's own cover: anyone with its address, as the calendar shows it
  const m = /^\/uc\/([\w-]{10,20})\/([0-9A-Za-z]{22})\.jpg$/.exec(url.pathname);
  const o = m && env.MEDIA ? await env.MEDIA.get('uc/' + m[1] + '/' + m[2] + '.jpg') : null;
  if (!o) return new Response('Not here.', { status: 404 });
  return new Response(o.body, { headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'public, max-age=31536000, immutable', 'Access-Control-Allow-Origin': '*' } });
}
async function uplFile(request, url, env, u, plain) {   // a member's own MP3 or MP4, its signed link checked as John's are; ranges for seeking
  if (!env.MEDIA || !env.ADMIN_KEY) return plain(503, 'Not set up.');
  if (request.method !== 'GET' && request.method !== 'HEAD') return plain(405, 'Use GET.');
  const exp = parseInt(url.searchParams.get('e'), 10), sig = url.searchParams.get('s') || '';
  if (!(exp > Date.now() / 1000) || sig !== await mediaSig(env, 'u' + u[1] + '/' + u[2], u[3], exp)) return plain(403, 'This link has run out or isn’t valid.');
  const key = 'u' + u[1] + '/' + u[2] + '/' + u[3] + '.' + u[4], head = await env.MEDIA.head(key);
  if (!head) return plain(404, 'Not here.');
  const size = head.size, hdrs = { 'Content-Type': MEDIA_KINDS[u[1]].type, 'Accept-Ranges': 'bytes', 'Cache-Control': 'private, no-cache', 'ETag': head.httpEtag,
    'Access-Control-Allow-Origin': MEMBERS_ORIGIN, 'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, ETag' };
  if (request.headers.get('If-None-Match') === head.httpEtag) return new Response(null, { status: 304, headers: hdrs });
  let start = 0, end = size - 1, partial = false;
  const rg = /^bytes=(\d*)-(\d*)$/.exec(request.headers.get('Range') || '');
  if (rg && (rg[1] || rg[2])) {
    if (rg[1]) { start = parseInt(rg[1], 10); if (rg[2]) end = Math.min(size - 1, parseInt(rg[2], 10)); }
    else start = Math.max(0, size - parseInt(rg[2], 10));
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
async function uplSignoffDo(env, all, pid, rec, data, x) {   // a waiting song signed off (by its owner, or John for them): out, and to the members as its owner chose
  data.rel = data.rel || {}; data.rel[x.id] = Date.now();
  data.reld = data.reld || {}; data.reld[x.id] = londonDay();   // (the day it was signed off: a playlist's song goes to the members that day)
  const today = londonDay(), snd = uplSendOf(rec), cd = uplDays(data.songs, rec).find(y => y.id === x.id).d, d = rec.mode === 'list' ? today : cd;
  const now = uplSendNow(snd, d, today);   // its time has come: now; else uplAnnounce at its time
  if (now) { data.ann = data.ann || {}; data.ann[x.id] = Date.now(); }
  await env.SOTD.put('upl:' + pid, JSON.stringify(data));
  const sent = now ? await uplSend(env, all, rec.o, pid, rec, x, cd) : null;
  if (uplShareOf(rec) === 'public') await startJob(env, null, null, 0, null, true).catch(() => {});   // (its card for links)
  return { sent, on: snd.on && !now ? d : null, at: snd.at };
}
async function uplAnnounce(env, U, today) {   // the songs whose day it is, out, to the members once, at their owner's time
  let all = null;
  const hm = uplHM();
  for (const [pid, rec] of Object.entries(U)) {
    const snd = uplSendOf(rec);
    if (rec.st === 'ok' && snd.on && snd.how === 'all') { all = all || (await env.SOTD.get('members', 'json')) || {}; await uplRound(env, all, pid, rec, snd); continue; }
    if (rec.st !== 'ok' || !snd.on || rec.annDay === today) continue;
    if (snd.how !== 'now' && hm < snd.at) continue;
    if (snd.how === 'week' ? uplDow(today) !== snd.wd : snd.how === 'day' && !snd.days.includes(uplDow(today))) continue;
    // which days' songs: today's; with days skipped or once a week, those since the last time (a week at most, and none from before it was set so)
    const sinceDay = snd.since ? londonDay(new Date(snd.since).toISOString()) : '', back = snd.how === 'week' || (snd.how === 'day' && snd.days.length < 7) ? 6 : 0;
    const from = [uplDayAdd(today, -back), sinceDay].sort().pop();
    const data = await uplSongs(env, pid), days = uplDays(data.songs || [], rec);
    data.ann = data.ann || {};
    const due = days.filter((x, i) => { const sd = uplSongDay(rec, data, days, i); return sd && sd <= today && sd >= from && !data.ann[x.id] && !uplWaiting(rec, data, data.songs[i]) && !uplGone(data, data.songs[i]); });
    rec.annDay = today; await uplPut(env, U);
    if (!due.length) continue;
    all = all || (await env.SOTD.get('members', 'json')) || {};
    if (!all[rec.o]) continue;
    for (const x of due) data.ann[x.id] = Date.now();
    await env.SOTD.put('upl:' + pid, JSON.stringify(data));
    if (snd.how === 'week') await uplSendWeek(env, all, rec.o, pid, rec, due);
    else for (const x of due) await uplSend(env, all, rec.o, pid, rec, x, x.d);
  }
}
// when each of the chosen contributors (who) has added a new song since the last time: all their new songs, in one
// message (John, 7 Nov 2026: "a new notification of when contributors ... have all uploaded a new song"). Who added a
// song is Spotify's added_by, known for members who've connected Spotify here.
async function uplRound(env, all, pid, rec, snd) {
  if (!all[rec.o]) return;
  const data = await uplSongs(env, pid), ids = await spWhoIds(env, all), since = Math.max(data.roundAt || 0, snd.since || 0);
  data.ann = data.ann || {};
  const fresh = (data.songs || []).filter(x => uplDated(x) && Date.parse(x.a) > since && !data.ann[x.id] && !uplWaiting(rec, data, x) && !uplGone(data, x) && snd.who.includes(ids[x.ab]));
  const by = {}; fresh.forEach(x => { (by[ids[x.ab]] = by[ids[x.ab]] || []).push(x); });
  if (!snd.who.every(k => (by[k] || []).length)) return;
  const now = Date.now(); data.roundAt = now; fresh.forEach(x => { data.ann[x.id] = now; });
  await env.SOTD.put('upl:' + pid, JSON.stringify(data));
  const ownerId = await env.SOTD.get('owner'), me = rec.o;
  const to = Object.keys(all).filter(k => hasJoined(all[k]) && takesFrom(all, k, me, ownerId) && uplCan(all, k, rec));
  if (!to.length) return;
  const name = all[me].name, pl = rec.cvt || uplName(all, rec), who = snd.who.map(k => all[k].name);
  const whoW = who.length > 1 ? who.slice(0, -1).join(', ') + ' and ' + who[who.length - 1] : who[0];
  const list = fresh.slice(0, 10).map(x => x.title + ' – ' + x.artist + ' (' + all[ids[x.ab]].name + ')').join('\n') + (fresh.length > 10 ? '\n…and ' + (fresh.length - 10) + ' more' : '');
  const msg = { title: pl + ': new songs from ' + whoW, subject: pl + ': new songs', body: list, from: name, url: SITE_SONGS + '#u=' + pid, tag: 'sotd-uplround-' + pid + '-' + now };
  if (fresh[fresh.length - 1].img) msg.icon = fresh[fresh.length - 1].img;
  const names = to.filter(k => k !== me).map(k => all[k].name), every = ['members', 'public'].includes(uplShareOf(rec));
  const r = await pushAll(env, msg, to, { kind: 'member', via: { t: 'member', id: me, name }, toNames: every ? null : names, fromId: me, toIds: every ? 'all' : to });
  await sentAdd(env, me, msg, r, every ? 'all' : names);
}
async function uplSendWeek(env, all, me, pid, rec, xs) {   // once a week: the week's songs in one message from its owner
  const ownerId = await env.SOTD.get('owner');
  const to = Object.keys(all).filter(k => hasJoined(all[k]) && takesFrom(all, k, me, ownerId) && uplCan(all, k, rec));
  if (!to.length || !xs.length) return { inboxes: 0, sent: 0 };
  const name = all[me].name, pl = rec.cvt || uplName(all, rec), n = xs.length;
  const list = xs.slice(0, 8).map(x => x.title + ' – ' + x.artist).join('\n') + (n > 8 ? '\n…and ' + (n - 8) + ' more' : '');
  const msg = { title: name + ': ' + n + (n === 1 ? ' song' : ' songs') + ' this week', subject: pl + ': this week', body: pl + '\n' + list, from: name, url: SITE_SONGS + '#u=' + pid, tag: 'sotd-uplwk-' + pid };
  if (xs[xs.length - 1].img) msg.icon = xs[xs.length - 1].img;
  const names = to.filter(k => k !== me).map(k => all[k].name), every = ['members', 'public'].includes(uplShareOf(rec));
  const r = await pushAll(env, msg, to, { kind: 'member', via: { t: 'member', id: me, name }, toNames: every ? null : names, fromId: me, toIds: every ? 'all' : to });
  await sentAdd(env, me, msg, r, every ? 'all' : names);
  return { inboxes: r.inboxes, sent: r.sent };
}
async function nfactOf(env, track) { return (await env.SOTD.get('nfact:' + track)) || ''; }   // the fact chosen at sign-off for a song's notification
async function uplSend(env, all, me, pid, rec, x, d) {   // a song signed off: to the members, as a message from its owner
  const ownerId = await env.SOTD.get('owner');
  const to = Object.keys(all).filter(k => hasJoined(all[k]) && takesFrom(all, k, me, ownerId) && uplCan(all, k, rec));
  if (!to.length) return { inboxes: 0, sent: 0 };
  const name = all[me].name, slug = d.slice(8, 10) + '-' + d.slice(5, 7) + '-' + d.slice(0, 4), pl = uplTitle(all, rec);
  const msg = { title: name + ': ' + x.title, subject: x.title, body: x.artist + ' · ' + pl, from: name, url: SITE_SONGS + '#u=' + pid + '&d=' + slug, tag: 'sotd-upl-' + pid + '-' + x.id };
  if (x.img) msg.icon = x.img;
  const nf = await nfactOf(env, x.id); if (nf) msg.fact = nf;
  // who it went to, by name: only those the playlist is shared with (to, above), never 'all' (John, 1 Nov 2026: his
  // Sent said everyone for a song on a playlist shared with one person; it had gone to that one alone)
  const names = to.filter(k => k !== me).map(k => all[k].name);
  const every = ['members', 'public'].includes(uplShareOf(rec));   // ('all' only when it's shared with every member)
  const r = await pushAll(env, msg, to, { kind: 'member', via: { t: 'member', id: me, name }, toNames: every ? null : names, fromId: me, toIds: every ? 'all' : to });
  await sentAdd(env, me, msg, r, every ? 'all' : names);
  return { inboxes: r.inboxes, sent: r.sent };
}

// ---------- each member's own sent notifications (John, 29 Oct 2026: "a sent message tab on a members profile, for them
// to see only their sent notifications"): outbox:<member>, the newest 60, each as it went into the others' inboxes (its
// id, words, link, pictures) with who it went to as they chose it (to: 'all' or their names then). John's from the
// Members tab go into the member who is John (This is me). GET /members/sent gives them, for the calendar's Profile ›
// Sent. None were kept before 29 Oct 2026, so the first time it's made from the Status tab's log (pushLog: which were
// theirs, when, to whom) and the inboxes they went into (words and pictures; for John's to chosen people, whose
// inboxes they're in). ----------
const SENT_MAX = 60;
async function sentList(env, member, keep = true) {   // keep: write what's made from the log (not when sentAdd writes next)
  const have = await env.SOTD.get('outbox:' + member, 'json');
  if (have) return have;
  const owner = await env.SOTD.get('owner'), log = (await env.SOTD.get('pushLog', 'json')) || [];
  const mine = log.filter(e => e.id && e.via && (e.via.t === 'member' ? e.via.id === member : e.via.t === 'you' && !!owner && owner === member));
  const box = [];
  if (mine.length) {
    const all = (await env.SOTD.get('members', 'json')) || {}, want = new Set(mine.map(e => e.id)), got = {}, who = {};
    for (const k of Object.keys(all)) {
      for (const x of (await env.SOTD.get('inbox:' + k, 'json')) || []) if (want.has(x.id)) { got[x.id] = got[x.id] || x; (who[x.id] = who[x.id] || []).push(all[k].name); }
    }
    mine.forEach(e => {
      const x = got[e.id] || {}, to = e.via.t === 'member' ? (Array.isArray(e.toNames) ? e.toNames : 'all') : e.to ? (who[e.id] || []) : 'all';
      const it = { id: e.id, at: e.at, t: x.t || e.title || '', b: x.b || '', u: x.u || '', to };
      if (x.i) it.i = x.i; if (x.c) it.c = x.c;
      box.push(it);
    });
  }
  if (keep) await kvSoft(env, 'outbox:' + member, JSON.stringify(box));   // (KV: one write a second to a key)
  return box;
}
async function sentAdd(env, member, msg, r, to) {
  if (!member || !r || !r.id) return;
  const box = (await sentList(env, member, false)).filter(x => x.id !== r.id);   // (made from the log just now: it's there already)
  const it = { id: r.id, at: r.at || Date.now(), t: msg.subject || msg.title, b: msg.body || '', u: msg.url || '', to };
  if (msg.gif || msg.image) it.i = msg.gif || msg.image; if (msg.icon) it.c = msg.icon;
  box.unshift(it); box.length = Math.min(box.length, SENT_MAX);
  await kvSoft(env, 'outbox:' + member, JSON.stringify(box));
}
async function memberSent(request, env, answer) {
  if (request.method !== 'GET') return answer(405, { error: 'Use GET.' });
  const member = await memberFromToken(request, env);
  if (!member) return answer(401, { error: 'Not a member (any more).' });
  return answer(200, { items: await withReactions(env, member, await sentFixUpl(env, member, await sentList(env, member)), () => true) });
}
// a playlist's new song sent before 1 Nov 2026 was kept as sent to 'all' whoever it went to (it only ever went to the
// members the playlist was shared with). Each such one is put right from the inboxes it's actually in: in the Sent
// list, in those inboxes, and in the Status tab's log. Once each; kept marked fx.
async function sentFixUpl(env, member, box) {
  const U = await uplAll(env);
  const bad = box.filter(x => x.to === 'all' && !x.fx && (m => m && U[m[1]] && !['members', 'public'].includes(uplShareOf(U[m[1]])))(/#u=([\w-]{10,20})&d=/.exec(x.u || '')));
  if (!bad.length) return box;
  const all = (await env.SOTD.get('members', 'json')) || {}, want = new Set(bad.map(x => x.id)), who = {};
  const boxes = {};
  for (const k of Object.keys(all)) {   // who has each in their inbox: who it went to
    const ib = boxes[k] = (await env.SOTD.get('inbox:' + k, 'json')) || [];
    for (const x of ib) if (want.has(x.id) && k !== member) (who[x.id] = who[x.id] || []).push(k);
  }
  for (const k of Object.keys(all)) {   // each inbox's copy: who else it went to (as toFor gives), not everyone
    let ch = false;
    for (const x of boxes[k]) if (want.has(x.id) && x.to === 'all') { x.to = (who[x.id] || []).filter(i => i !== k && all[i]).map(i => ({ i, n: all[i].name })); ch = true; }
    if (ch) await env.SOTD.put('inbox:' + k, JSON.stringify(boxes[k]));
  }
  for (const x of bad) { x.to = (who[x.id] || []).filter(i => all[i]).map(i => all[i].name); x.fx = 1; }
  await kvSoft(env, 'outbox:' + member, JSON.stringify(box));
  const log = (await env.SOTD.get('pushLog', 'json')) || [];
  let lc = false;
  for (const e of log) if (want.has(e.id) && e.kind === 'member') { e.toNames = (who[e.id] || []).filter(i => all[i]).map(i => all[i].name); lc = true; }
  if (lc) await kvSoft(env, 'pushLog', JSON.stringify(log));
  return box;
}

async function notePic(url, env) {   // a notification's picture: anyone with its (unguessable) address, as a phone fetches it unsigned
  const m = /^\/n\/([\w-]{16})\.(jpg|gif|webp)$/.exec(url.pathname);
  const o = m && env.MEDIA ? await env.MEDIA.get('n/' + m[1] + '.' + m[2]) : null;
  if (!o) return new Response('Not here.', { status: 404 });
  return new Response(o.body, { headers: { 'Content-Type': m[2] === 'gif' ? 'image/gif' : m[2] === 'webp' ? 'image/webp' : 'image/jpeg', 'Cache-Control': 'public, max-age=31536000, immutable', 'Access-Control-Allow-Origin': '*' } });
}

async function pushCount(env) {
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {}, all = (await env.SOTD.get('members', 'json')) || {};
  const by = {}, ids = {};
  Object.values(subs).forEach(x => { const n = (all[x.m] && all[x.m].name) || '?'; by[n] = (by[n] || 0) + 1; ids[x.m] = (ids[x.m] || 0) + 1; });
  const kinds = {};
  Object.values(subs).forEach(x => { const k = subHost(x); kinds[k] = (kinds[k] || 0) + 1; });
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
  { const t0 = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, ''), h0 = await sha256hex(t0), all0 = (await env.SOTD.get('members', 'json')) || {};
    if (all0[member] && all0[member].devices && all0[member].devices[h0] && all0[member].devices[h0].viewas) return answer(200, { ok: true, viewAs: true }); }   // (See as them: John's device keeps his own notifications)
  const body = await request.json().catch(() => ({}));
  if (body.apns) return memberApns(env, member, body, answer);
  const sub = body.sub || {}, endpoint = String(sub.endpoint || ''), keys = sub.keys || {};
  if (!/^https:\/\//.test(endpoint) || endpoint.length > 1000) return answer(400, { error: 'That isn’t a push subscription.' });
  const id = (await sha256hex(endpoint)).slice(0, 24);
  const subs = (await env.SOTD.get('pushSubs', 'json')) || {};
  if (body.off) { if (subs[id]) { delete subs[id]; await env.SOTD.put('pushSubs', JSON.stringify(subs)); } return answer(200, { on: false }); }
  if (!keys.p256dh || !keys.auth || String(keys.p256dh).length > 200 || String(keys.auth).length > 100) return answer(400, { error: 'That subscription has no keys.' });
  if (!subs[id] && Object.keys(subs).length >= PUSH_MAX) return answer(400, { error: 'Too many devices have notifications on.' });
  subs[id] = Object.assign({ m: member, sub: { endpoint, keys: { p256dh: String(keys.p256dh), auth: String(keys.auth) } }, at: Date.now() }, APP_RE.test(String(body.app || '')) ? { app: String(body.app) } : {}, subs[id] && subs[id].upd ? { upd: subs[id].upd } : {});
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
async function memberApns(env, member, body, answer) {   // the iPhone app's device token: on, off, or a test
  const token = String(body.apns || '').toLowerCase();
  if (!/^[0-9a-f]{64,200}$/.test(token)) return answer(400, { error: 'That isn’t a device token.' });
  const id = 'a' + (await sha256hex(token)).slice(0, 23), subs = (await env.SOTD.get('pushSubs', 'json')) || {};
  if (body.off) { if (subs[id]) { delete subs[id]; await env.SOTD.put('pushSubs', JSON.stringify(subs)); } return answer(200, { on: false }); }
  if (!subs[id] && Object.keys(subs).length >= PUSH_MAX) return answer(400, { error: 'Too many devices have notifications on.' });
  const app = APP_RE.test(String(body.app || '')) ? String(body.app) : '';   // (which app and build it is: for telling it of a newer build, appUpdates)
  if (!subs[id] || subs[id].m !== member || (app && subs[id].app !== app)) { subs[id] = Object.assign({ m: member, apns: token, at: Date.now() }, app ? { app } : {}, subs[id] && subs[id].m === member && subs[id].upd ? { upd: subs[id].upd } : {}); await env.SOTD.put('pushSubs', JSON.stringify(subs)); }
  if (!body.test) return answer(200, { on: true });
  const unread = ((await env.SOTD.get('inbox:' + member, 'json')) || []).filter(x => !x.r).length;
  let st = 0, why = '';
  try { ({ st, why } = await apnsOne(env, token, { title: 'Notifications are on', body: 'You’ll hear about each new Song of the Day as it goes up.', url: SITE_SONGS, tag: 'sotd-on', unread })); } catch (e) { why = String(e && e.message || e).slice(0, 160); }
  return answer(200, { on: true, test: st, why });
}

let pushDone = '';   // this Worker's own note of the last song sent (so most looks don't read KV)
// The day's song goes to the members when it comes out on the live calendar (John, 28 Oct 2026: "can't a push just be
// when a new song goes live?"; before, it waited for his MP3 and video). The site job asks as soon as the live site shows
// it (_sotd/push_live.py, /push/check), and the 10-minute cron looks too, in case that call is lost; each checks the
// live site itself (songs/today.json names the latest song out), and only the newest song, no more than two days old
// (UK time), goes, once. Says what it did.
async function liveToday() {   // the latest song the live calendar shows (written by share_pages.py with each release)
  return fetch(SITE_SONGS + 'today.json?t=' + Date.now(), { headers: { 'User-Agent': 'SongOfTheDayWorker' }, cf: { cacheTtl: 0 } })
    .then(r => r.ok ? r.json() : null).catch(() => null);
}
async function pushDaily(env, items, via) {   // via: who asked (the Status tab says): { t: 'timer' }, or { t: 'site', run }
  const last = items[items.length - 1];
  if (!last || !last.id) return 'no songs';
  if (pushDone === last.id) return 'sent already';
  const sentRaw = await env.SOTD.get('pushSent', 'json');
  if (!sentRaw) { await kvSoft(env, 'pushSent', JSON.stringify([last.id])); pushDone = last.id; return 'first look: noted, not sent'; }
  if (sentRaw.includes(last.id)) { pushDone = last.id; return 'sent already'; }
  const day = londonDay(last.a);
  if ((Date.parse(londonDay()) - Date.parse(day)) / DAY > 2) { pushDone = last.id; return 'too old to send'; }
  const live = await liveToday();
  if (!live || !(live.track ? live.track === last.id : live.title === last.title && live.date === day)) return 'not out on the calendar yet';
  // only one look sends it: two at the same moment (the cron and a visit) both found it unsent on 28 Oct 2026 and sent it
  // twice, 2 ms apart, since KV can't say who was first; the Durable Object can
  if (env.NOW) {
    const once = await env.NOW.get(env.NOW.idFromName('_once')).fetch('https://now/once', { method: 'POST', body: JSON.stringify({ key: 'push:' + last.id }) })
      .then(r => r.json()).catch(() => null);
    if (!once) return 'couldn’t check it wasn’t being sent: tried again at the next look';
    if (!once.first) { pushDone = last.id; return 'being sent by another look'; }
  }
  if (!await kvSoft(env, 'pushSent', JSON.stringify(sentRaw.concat(last.id).slice(-60)))) return 'couldn’t note it: not sent (it could go twice)';
  pushDone = last.id;
  const slug = live.slug || day.slice(8, 10) + '-' + day.slice(5, 7) + '-' + day.slice(0, 4);
  const nf = await nfactOf(env, last.id);
  const r = await pushAll(env, Object.assign({ title: last.title, body: last.artist + (day === londonDay() ? ' \u00b7 today\u2019s Song of the Day' : ' \u00b7 the latest Song of the Day'), icon: last.img || '',
    url: SITE_SONGS + '#' + slug, tag: 'sotd-day' }, live.card ? { image: live.card } : {}, nf ? { fact: nf } : {}), null, { kind: 'song', track: last.id, via: via || null });   // (the day's share card, made with the release)
  console.log('pushDaily ' + last.id + ': ' + JSON.stringify(r));
  return 'sent: ' + pushSay(Object.assign({}, r, { devices: r.devices + r.gone }));
}
// /push/check: the site job, as soon as the live site shows a new song (_sotd/push_live.py). Anyone may ask: it only ever
// sends the newest song, once it's out on the live calendar, and only once (a few asks a minute at most).
async function pushCheck(request, env, ctx) {
  const h = { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' };
  if (request.method !== 'POST') return new Response(JSON.stringify({ error: 'Use POST.' }), { status: 405, headers: h });
  const k = 'pushcheck:' + new Date().toISOString().slice(0, 16), n = Number(await env.SOTD.get(k)) || 0;
  if (n >= 6) return new Response(JSON.stringify({ error: 'Asked a lot this minute: the 10-minute look will do it.' }), { status: 429, headers: h });
  await kvSoft(env, k, String(n + 1), { expirationTtl: 120 });
  const b = await request.json().catch(() => ({})), r = b && b.run;   // the site job's run (push_live.py), for the Status tab
  const run = r && /^\d{1,15}$/.test(String(r.id)) ? { id: Number(r.id), n: Number(r.n) || 0, job: String(r.job || '').slice(0, 60) } : null;
  const res = await songsJson(new Request(LIVE_ORIGIN + '/songs.json'), env, ctx);
  const j = await res.json();
  const did = await pushDaily(env, (j.songs || []).map(x => ({ id: x.id, a: x.a, title: x.title, artist: x.artist, img: x.img })), { t: 'site', run });
  return new Response(JSON.stringify({ did }), { headers: h });
}

// ---------- Alexa: John's own Song of the Day skill ----------
// The skill (set up by John in the Alexa developer console from _sotd/alexa/, its endpoint this Worker's /alexa) plays
// his own recordings of the songs: today's first, then back a day at a time, as Play all does; a day, a song or an
// artist asked for by name; shuffled; and says what's playing, with a fact about it. Each request is checked as Amazon's
// (its signature, made with the certificate at Amazon's own address, and its time), and the first skill to call here is
// the only one ever answered (KV alexaSkill), so nobody else's skill can be pointed at John's recordings.
// What plays is said in each stream's token: <order>|<order's detail>|<loop 0/1>|<track>, the orders d (the days, newest
// first), a (one artist's) and r (shuffled, the detail its seed).
const ALEXA_CERTS = new Map();
function derAt(b, i) {
  let len = b[i + 1], p = i + 2;
  if (len & 0x80) { const n = len & 0x7f; len = 0; for (let k = 0; k < n; k++) len = len * 256 + b[p++]; }
  return { tag: b[i], start: i, cs: p, end: p + len };
}
function derKids(b, t) { const out = []; for (let p = t.cs; p < t.end;) { const k = derAt(b, p); out.push(k); p = k.end; } return out; }
function derTime(b, t) {
  const x = String.fromCharCode(...b.subarray(t.cs, t.end)), g = t.tag === 0x18;
  const y = g ? +x.slice(0, 4) : (+x.slice(0, 2) < 50 ? 2000 : 1900) + +x.slice(0, 2), o = g ? 4 : 2;
  return Date.UTC(y, +x.slice(o, o + 2) - 1, +x.slice(o + 2, o + 4), +x.slice(o + 4, o + 6), +x.slice(o + 6, o + 8), +x.slice(o + 8, o + 10) || 0);
}
async function alexaVerify(request, raw) {   // '' when it's Amazon's, else why not
  const certUrl = request.headers.get('SignatureCertChainUrl') || '', sig = request.headers.get('Signature-256') || '';
  if (!certUrl || !sig) return 'unsigned';
  let u; try { u = new URL(certUrl); } catch (e) { return 'certificate address'; }
  if (u.protocol !== 'https:' || u.hostname.toLowerCase() !== 's3.amazonaws.com' || (u.port && u.port !== '443') || !u.pathname.startsWith('/echo.api/')) return 'certificate address';
  let c = ALEXA_CERTS.get(u.href);
  if (!c) {
    const pem = await fetch(u.href).then(r => r.ok ? r.text() : '');
    const m = /-----BEGIN CERTIFICATE-----([\s\S]+?)-----END CERTIFICATE-----/.exec(pem);
    if (!m) return 'no certificate';
    const b = Uint8Array.from(atob(m[1].replace(/\s+/g, '')), ch => ch.charCodeAt(0));
    const tbs = derKids(b, derAt(b, 0))[0], k = derKids(b, tbs), o = k[0].tag === 0xa0 ? 1 : 0;
    const v = derKids(b, k[o + 3]), spki = b.slice(k[o + 5].start, k[o + 5].end);
    const san = new TextDecoder('latin1').decode(b).includes('echo-api.amazon.com');
    c = { from: derTime(b, v[0]), until: derTime(b, v[1]), san, key: await crypto.subtle.importKey('spki', spki, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']) };
    ALEXA_CERTS.set(u.href, c);
  }
  const now = Date.now();
  if (!c.san || now < c.from || now > c.until) return 'certificate';
  let ok = false;
  try { ok = await crypto.subtle.verify('RSASSA-PKCS1-v1_5', c.key, Uint8Array.from(atob(sig), ch => ch.charCodeAt(0)), raw); } catch (e) {}
  return ok ? '' : 'signature';
}
let alexaSite = null;   // the site's years and facts, kept for half an hour
async function alexaSiteData() {
  if (alexaSite && Date.now() - alexaSite.at < 30 * 60e3) return alexaSite;
  const get = p => fetch(SITE_SONGS + p).then(r => r.ok ? r.json() : {}).catch(() => ({}));
  const [y, f] = await Promise.all([get('years.json'), get('facts.json')]);
  alexaSite = { at: Date.now(), years: y.years || {}, facts: f || {} };
  return alexaSite;
}
function alexaDayBefore(iso) { const d = new Date(iso + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() - 1); return d.toISOString().slice(0, 10); }
async function alexaSongs(env, ctx) {   // the songs John has recorded, newest first, each with its day (as the calendar works them out)
  const j = await (await songsJson(new Request(LIVE_ORIGIN + '/songs.json'), env, ctx)).json();
  const list = j.songs || [];
  for (let i = list.length - 1, next = null; i >= 0; i--) { let d = londonDay(list[i].a); if (next && alexaDayBefore(next) < d) d = alexaDayBefore(next); list[i].d = d; next = d; }
  const cat = await catalog(env);
  return list.filter(x => cat[x.id] && cat[x.id].a).map(x => Object.assign(x, { hasV: !!cat[x.id].v })).reverse();
}
function fold(x) { return String(x || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/['\u2018\u2019`]/g, '').replace(/&/g, ' and ').replace(/[^a-z0-9 ]+/g, ' ').replace(/\b(the|a|an)\b/g, ' ').replace(/\s+/g, ' ').trim(); }
function fnv(x) { let h = 2166136261; for (let i = 0; i < x.length; i++) { h ^= x.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
function alexaOrder(list, mode, param) {
  if (mode === 'a') return list.filter(x => fold(x.artist).includes(param));
  if (mode === 'r') return list.slice().sort((x, y) => fnv(param + x.id) - fnv(param + y.id));
  return list;
}
function alexaToken(mode, param, loop, track) { return [mode, param, loop ? 1 : 0, track].join('|'); }
function alexaParse(token) { const p = String(token || '').split('|'); return p.length === 4 ? { mode: p[0], param: p[1], loop: p[2] === '1', track: p[3] } : null; }
async function alexaPlay(env, x, at, behaviour, offset, previous) {
  const exp = Math.floor(Date.now() / 1000) + 24 * 3600;
  const stream = { url: LIVE_ORIGIN + '/m/a/' + x.id + '.mp3?e=' + exp + '&s=' + await mediaSig(env, 'a', x.id, exp), token: alexaToken(at.mode, at.param, at.loop, x.id), offsetInMilliseconds: offset || 0 };
  if (previous) stream.expectedPreviousToken = previous;
  const art = x.img ? { sources: [{ url: x.img }] } : undefined;
  return { type: 'AudioPlayer.Play', playBehavior: behaviour, audioItem: { stream, metadata: { title: x.title, subtitle: x.artist + ' · ' + alexaDay(x.d), art, backgroundImage: art } } };
}
function alexaDay(iso) { return new Date(iso + 'T12:00:00Z').toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' }).replace(',', ''); }
function alexaSay(text, end, extra) { return Object.assign({ outputSpeech: { type: 'PlainText', text } }, end === undefined ? {} : { shouldEndSession: end }, extra || {}); }
async function alexa(request, env, ctx) {
  const reply = (response) => new Response(JSON.stringify({ version: '1.0', response: response || {} }), { headers: { 'Content-Type': 'application/json;charset=UTF-8' } });
  if (request.method !== 'POST') return new Response('The Song of the Day skill talks here.', { status: 405 });
  const raw = new Uint8Array(await request.arrayBuffer());
  const why = await alexaVerify(request, raw);
  if (why) { console.log('alexa refused: ' + why); return new Response('Not Amazon (' + why + ').', { status: 400 }); }
  let body; try { body = JSON.parse(new TextDecoder().decode(raw)); } catch (e) { return new Response('Bad request.', { status: 400 }); }
  const rq = body.request || {}, cx = body.context || {};
  if (Math.abs(Date.now() - Date.parse(rq.timestamp || 0)) > 150e3) return new Response('Too old.', { status: 400 });
  const app = (cx.System && cx.System.application && cx.System.application.applicationId) || (body.session && body.session.application && body.session.application.applicationId) || '';
  const mine = await env.SOTD.get('alexaSkill');
  if (!mine && app) await env.SOTD.put('alexaSkill', app);   // the first one: John's
  else if (mine !== app) return new Response('Not this skill.', { status: 403 });
  if (!env.MEDIA || !env.ADMIN_KEY) return reply(alexaSay('The recordings aren’t set up yet.', true));
  const type = rq.type || '', intent = (rq.intent && rq.intent.name) || '', slots = (rq.intent && rq.intent.slots) || {};
  const slot = n => (slots[n] && slots[n].value) || '';
  const playing = alexaParse(cx.AudioPlayer && cx.AudioPlayer.token), offset = (cx.AudioPlayer && cx.AudioPlayer.offsetInMilliseconds) || 0;
  const audioEvent = type.startsWith('AudioPlayer.') || type.startsWith('PlaybackController.');
  const list = await alexaSongs(env, ctx);
  if (!list.length) return reply(audioEvent ? {} : alexaSay('There are no recordings to play yet.', true));
  const now = (at) => { const ord = alexaOrder(list, at.mode, at.param); return { ord, i: ord.findIndex(x => x.id === at.track) }; };
  const step = async (dir, behaviour, quiet) => {   // the next (or previous) one in what's playing
    if (!playing) return quiet ? {} : alexaSay('Nothing’s playing. Say, play today’s song.', false);
    const { ord, i } = now(playing);
    let j = i + dir;
    if (j >= ord.length || j < 0) { if (!playing.loop || !ord.length) return quiet ? {} : alexaSay(dir > 0 ? 'That was the last one.' : 'That’s the first one.', true); j = (j + ord.length) % ord.length; }
    return { directives: [await alexaPlay(env, ord[j], playing, behaviour)], shouldEndSession: quiet ? undefined : true };
  };
  const start = async (x, at, say) => alexaSay(say, true, { directives: [await alexaPlay(env, x, Object.assign({ loop: playing ? playing.loop : false }, at, { track: x.id }), 'REPLACE_ALL')] });
  const intro = x => x.title + ', by ' + x.artist + '.';
  const current = playing && list.find(x => x.id === playing.track);

  if (type === 'AudioPlayer.PlaybackNearlyFinished') {   // the next one queued up behind it
    if (!playing) return reply({});
    const { ord, i } = now(playing); let j = i + 1;
    if (j >= ord.length) { if (!playing.loop) return reply({}); j = 0; }
    return reply({ directives: [await alexaPlay(env, ord[j], playing, 'ENQUEUE', 0, cx.AudioPlayer.token)] });
  }
  if (type === 'AudioPlayer.PlaybackFailed') { console.log('alexa playback failed: ' + JSON.stringify(rq.error || {})); return reply(await step(1, 'REPLACE_ALL', true)); }
  if (type === 'PlaybackController.NextCommandIssued') return reply(await step(1, 'REPLACE_ALL', true));
  if (type === 'PlaybackController.PreviousCommandIssued') return reply(await step(-1, 'REPLACE_ALL', true));
  if (type === 'PlaybackController.PauseCommandIssued') return reply({ directives: [{ type: 'AudioPlayer.Stop' }] });
  if (type === 'PlaybackController.PlayCommandIssued') return reply(current ? { directives: [await alexaPlay(env, current, playing, 'REPLACE_ALL', offset)] } : {});
  if (audioEvent || type === 'SessionEndedRequest' || type === 'System.ExceptionEncountered') return reply({});

  const today = list[0];
  if (type === 'LaunchRequest' || intent === 'PlayTodayIntent')
    return reply(await start(today, { mode: 'd', param: '' }, (today.d === londonDay() ? 'Today’s Song of the Day: ' : 'The latest Song of the Day: ') + intro(today)));
  switch (intent) {
    case 'PlayDateIntent': {
      const d = slot('date');
      if (!/^\d{4}-\d\d-\d\d$/.test(d)) return reply(alexaSay('Which day? Say, for example, play the song from last Monday.', false));
      const x = list.find(s => s.d === d) || list.find(s => s.d <= d);
      if (!x) return reply(alexaSay('There’s no song of the day that far back.', true));
      return reply(await start(x, { mode: 'd', param: '' }, (x.d === d ? 'The song for ' : 'There wasn’t one that day. The one before, for ') + alexaDay(x.d) + ': ' + intro(x)));
    }
    case 'PlaySongIntent': {
      const q = fold(slot('song'));
      if (!q) return reply(alexaSay('Which song?', false));
      const words = q.split(' ');
      const x = list.find(s => fold(s.title) === q) || list.find(s => fold(s.title).includes(q)) || list.find(s => words.every(w => fold(s.title + ' ' + s.artist).includes(w)));
      if (!x) return reply(alexaSay('I couldn’t find ' + slot('song') + ' among the songs of the day.', true));
      return reply(await start(x, { mode: 'd', param: '' }, intro(x)));
    }
    case 'PlayArtistIntent': {
      const q = fold(slot('artist'));
      const ord = q ? alexaOrder(list, 'a', q) : [];
      if (!ord.length) return reply(alexaSay('I couldn’t find any songs by ' + (slot('artist') || 'them') + '.', true));
      return reply(await start(ord[0], { mode: 'a', param: q }, ord.length === 1 ? 'There’s one song by ' + ord[0].artist + ': ' + ord[0].title + '.' : ord.length + ' songs by ' + ord[0].artist + ', starting with ' + ord[0].title + '.'));
    }
    case 'ShuffleIntent': {
      const seed = randomCode(4), ord = alexaOrder(list, 'r', seed);
      return reply(await start(ord[0], { mode: 'r', param: seed }, 'Shuffling all ' + list.length + ' songs. First, ' + intro(ord[0])));
    }
    case 'AMAZON.ShuffleOnIntent': case 'AMAZON.ShuffleOffIntent': case 'AMAZON.LoopOnIntent': case 'AMAZON.LoopOffIntent': {
      if (!current) return reply(alexaSay('Nothing’s playing.', true));
      const at = Object.assign({}, playing);
      if (intent === 'AMAZON.ShuffleOnIntent') { at.mode = 'r'; at.param = randomCode(4); }
      if (intent === 'AMAZON.ShuffleOffIntent') { at.mode = 'd'; at.param = ''; }
      if (intent === 'AMAZON.LoopOnIntent') at.loop = true;
      if (intent === 'AMAZON.LoopOffIntent') at.loop = false;
      return reply({ directives: [await alexaPlay(env, current, at, 'REPLACE_ALL', offset)], shouldEndSession: true });   // the same song, carrying on, its order changed
    }
    case 'AMAZON.NextIntent': return reply(await step(1, 'REPLACE_ALL'));
    case 'AMAZON.PreviousIntent': return reply(await step(-1, 'REPLACE_ALL'));
    case 'AMAZON.StartOverIntent': case 'AMAZON.RepeatIntent': return reply(current ? { directives: [await alexaPlay(env, current, playing, 'REPLACE_ALL', 0)], shouldEndSession: true } : alexaSay('Nothing’s playing.', true));
    case 'AMAZON.ResumeIntent': return reply(current ? { directives: [await alexaPlay(env, current, playing, 'REPLACE_ALL', offset)], shouldEndSession: true } : await start(today, { mode: 'd', param: '' }, 'Today’s Song of the Day: ' + intro(today)));
    case 'AMAZON.PauseIntent': case 'AMAZON.StopIntent': case 'AMAZON.CancelIntent': return reply({ directives: [{ type: 'AudioPlayer.Stop' }], shouldEndSession: true });
    case 'WhatsPlayingIntent': {
      if (!current) return reply(alexaSay('Nothing’s playing. Say, play today’s song.', false));
      const site = await alexaSiteData(), y = site.years[current.id];
      return reply(alexaSay('This is ' + current.title + ', by ' + current.artist + (y ? ', from ' + y : '') + '. It was the Song of the Day on ' + alexaDay(current.d) + '.', true));
    }
    case 'FactIntent': {
      const x = current || today, site = await alexaSiteData(), f = (site.facts.songs || {})[x.id];
      const facts = []; if (f) { (f.s || []).forEach(t => facts.push(t[0])); (f.a || []).forEach(k => { const a = (site.facts.artists || {})[k]; if (a) a.f.forEach(t => facts.push(t[0])); }); }
      if (!facts.length) return reply(alexaSay('I don’t have any facts about ' + x.title + ' yet.', true));
      return reply(alexaSay((current ? '' : 'About today’s song, ' + x.title + '. ') + facts[Math.floor(Math.random() * facts.length)], true));
    }
    case 'WatchVideoIntent': {
      const x = current || today, screen = cx.System && cx.System.device && cx.System.device.supportedInterfaces && cx.System.device.supportedInterfaces.VideoApp;
      if (!screen) return reply(alexaSay('Videos play on an Echo with a screen. On this one, here’s the song: ' + intro(x), true, { directives: [await alexaPlay(env, x, playing || { mode: 'd', param: '', loop: false }, 'REPLACE_ALL')] }));
      if (!x.hasV) return reply(alexaSay('There’s no video of ' + x.title + ' yet.', true));
      const exp = Math.floor(Date.now() / 1000) + 24 * 3600;
      return reply({ outputSpeech: { type: 'PlainText', text: x.title + ', by ' + x.artist + '.' }, directives: [{ type: 'VideoApp.Launch', videoItem: {
        source: LIVE_ORIGIN + '/m/v/' + x.id + '.mp4?e=' + exp + '&s=' + await mediaSig(env, 'v', x.id, exp), metadata: { title: x.title, subtitle: x.artist } } }] });
    }
    case 'AMAZON.HelpIntent': case 'AMAZON.FallbackIntent': case 'AMAZON.NavigateHomeIntent':
      return reply(alexaSay((intent === 'AMAZON.FallbackIntent' ? 'Sorry, I didn’t get that. ' : '') + 'You can say: play today’s song, play the song from last Friday, play songs by an artist, shuffle, what’s this song, or tell me about this song. What would you like?', false,
        { reprompt: { outputSpeech: { type: 'PlainText', text: 'Say play today’s song, or shuffle.' } } }));
  }
  return reply(alexaSay('Sorry, I can’t do that yet.', true));
}

function londonDay(d) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London' }).format(d ? new Date(d) : new Date());   // 2026-10-22
}
function londonHour(d) {
  return Number(new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: '2-digit', hourCycle: 'h23' }).format(d ? new Date(d) : new Date()));
}
// The timer's song list (John, 28 Oct 2026: "We don't need the worker's timer to run all day once a song is added -
// there is no point checking for another song again until the next day. Start at 6am and stop it after a new song is
// added"): from 06:00 UK time it reads the playlist from Spotify,
// until the list it kept (KV songsLast) has a song added today; before 06:00, and once the day's song is in, it uses
// that kept list and asks Spotify nothing. Nothing else reads the playlist from Spotify (see songsJson).
const TIMER_FROM_HOUR = 6;
async function timerSongs(env, ctx, now) {
  const kept = await env.SOTD.get('songsLast', 'json');
  const list = (kept && kept.songs) || [];
  const newest = list.reduce((m, x) => (x.a > m ? x.a : m), '');
  const today = londonDay(now), haveToday = !!newest && londonDay(newest) === today;
  if (list.length && (haveToday || londonHour(now) < TIMER_FROM_HOUR)) return list;
  try {
    return (await readPlaylist(env, ctx)).songs;
  } catch (e) {   // Spotify says wait: the kept list (for the notification), Spotify asked nothing more until the wait is over
    if (!(e instanceof HttpError && e.extra.spotify) || !list.length) throw e;
    await noteSpotifyWait(env);
    return list;
  }
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
const PV_AT_ONCE = 6, UPL_PV_FILL = 18;   // previews asked for together; and for an owner watching their playlist fill (GET ?fill=1), each time
async function fillPreviews(env, items, limit, known) {
  const pv = known || (await env.SOTD.get('previews', 'json')) || {};
  const todo = items.filter(it => !(it.id in pv)).reverse().slice(0, limit);
  let changed = false, stop = false;
  const one = async it => {   // (Spotify's embed page for the song carries its 30-second preview)
    if (stop) return;
    const r = await fetch('https://open.spotify.com/embed/track/' + it.id, { headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36', 'Accept-Language': 'en-GB' } }).catch(() => null);
    if (r && (r.status === 404 || r.status === 410 || r.status === 400)) { pv[it.id] = '-'; changed = true; return; }   // (taken off Spotify: no preview, and looked at, so a playlist's progress ends)
    if (!r || !r.ok) { stop = true; return; }   // (refused or down: the rest wait for the next look)
    const m = (await r.text()).match(/https:\/\/p\.scdn\.co\/mp3-preview\/([0-9a-f]{20,})/);
    pv[it.id] = m ? m[1] : '-';
    changed = true;
  };
  for (let i = 0; i < todo.length && !stop; i += PV_AT_ONCE) await Promise.all(todo.slice(i, i + PV_AT_ONCE).map(one));   // six at a time
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
  if (e.extra && e.extra.ro) return new Response(JSON.stringify({ error: e.message, viewAs: true }), { status: 403, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' } });
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
