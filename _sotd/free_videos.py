#!/usr/bin/env python3
"""Free ways to find a YouTube video for members' songs before YouTube's own search, which spends the searches on
John's account (John, 1 Nov 2026: "try multiple different free ways and stop when we find a match"). Used by youtube.py.

In turn, each song stopping at the first match that passes:
  1. Wikidata: songs with the Spotify track id (P2207) and a YouTube video id (P1651); one query asks 200 songs. No key.
  2. MusicBrainz: the recording, searched by title and artist, and its YouTube links. No key; one request a second.
  2b. The artist's own channel: the artist's YouTube channel from Wikidata (by their Spotify artist id) or MusicBrainz
     (its links), then the channel's uploads read 50 to a 1-unit lookup, no search, and the song matched among them
     (John, 3 Nov 2026: "other methods to get youtube results ... which don't use up my credit"). Channels are kept in
     data/yt-artists.json so each artist is looked up once.
  3. Discogs: the release's videos (its users add them), searched by artist and track. Needs the DISCOGS_TOKEN secret
     (free: discogs.com > Settings > Developers > Generate new token); without it, skipped.
Each video is checked as the search's are, 50 to a 1-unit YouTube lookup: its title and channel scored against the song
(youtube.score, GOOD at least), its length against Spotify's, and whether other sites may play it.

A song every way misses is marked (free: the day) and goes to YouTube's search, after John's songs; it's asked the free
ways again after youtube.RETRY_DAYS. The songs these didn't reach this run (MusicBrainz and Discogs take FREE_MOST a
run) are kept from the search too, so it never spends one on a song not tried here yet.
"""
import datetime, json, os, re, time, urllib.error, urllib.parse, urllib.request

UA = 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)'
WIKIDATA = 'https://query.wikidata.org/sparql'
MB = 'https://musicbrainz.org/ws/2/'
DISCOGS = 'https://api.discogs.com/'
DISCOGS_TOKEN = os.environ.get('DISCOGS_TOKEN', '').strip()
ARTISTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'yt-artists.json')
ARTIST_MB_MOST = 60     # artists a run asked of MusicBrainz (two requests each, a second apart)
CH_PAGES = 8            # pages of 50 uploads read from a channel (its newest 400)
CH_UNITS = 1500         # 1-unit reads a run for the artists' channels (of the day's 10,000)
ARTIST_RETRY_DAYS = 60  # an artist with no channel found: asked again after this
FREE_MOST = 150      # songs a run for MusicBrainz and for Discogs (about 3 and 2 minutes at their rates)
MB_GAP, DISCOGS_GAP = 1.1, 1.05
VID = re.compile(r'(?:[?&]v=|youtu\.be/|/embed/|/shorts/)([\w-]{11})')


LAST = {}   # how the last request went (for --check): status, or the error


def http(url, headers=None, data=None):
    """JSON from url, or None (not there, refused, or the connection failed after a few tries). Waits out a 429."""
    LAST.clear()
    for i in range(4):
        try:
            req = urllib.request.Request(url, data=data, headers=dict({'User-Agent': UA, 'Accept': 'application/json'}, **(headers or {})))
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            LAST['status'] = e.code
            if e.code == 429 or e.code >= 500:
                time.sleep(min(60, int(e.headers.get('Retry-After') or 5 * (i + 1))))
                continue
            return None
        except (OSError, ValueError) as e:
            LAST['error'] = str(e)[:120]
            time.sleep(3 * (i + 1))
    return None


def vid_of(url):
    m = VID.search(url or '')
    return m.group(1) if m else ''


def wikidata(songs):
    """{track: [video ids]} for the songs Wikidata has a YouTube video for."""
    out = {}
    ids = [s['id'] for s in songs]
    for k in range(0, len(ids), 200):
        vals = ' '.join('"%s"' % t for t in ids[k:k + 200] if re.fullmatch(r'[0-9A-Za-z]{22}', t))
        if not vals:
            continue
        q = 'SELECT ?sp ?yt WHERE { VALUES ?sp { %s } ?item wdt:P2207 ?sp ; wdt:P1651 ?yt . }' % vals
        j = http(WIKIDATA, {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/sparql-results+json'},
                 urllib.parse.urlencode({'query': q, 'format': 'json'}).encode())
        for b in ((j or {}).get('results') or {}).get('bindings') or []:
            sp, yt = (b.get('sp') or {}).get('value', ''), (b.get('yt') or {}).get('value', '')
            if sp and re.fullmatch(r'[\w-]{11}', yt):
                out.setdefault(sp, []).append(yt)
    return out


def load_artists():
    try:
        with open(ARTISTS, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_artists(a):
    os.makedirs(os.path.dirname(ARTISTS), exist_ok=True)
    with open(ARTISTS, 'w', encoding='utf-8') as f:
        json.dump(a, f, indent=1, sort_keys=True)


def wikidata_channels(aids):
    """{Spotify artist id: [YouTube channel ids]} from Wikidata (P1902 Spotify artist, P2397 YouTube channel)."""
    out = {}
    aids = [a for a in aids if re.fullmatch(r'[0-9A-Za-z]{22}', a)]
    for k in range(0, len(aids), 200):
        vals = ' '.join('"%s"' % a for a in aids[k:k + 200])
        q = 'SELECT ?sp ?yt WHERE { VALUES ?sp { %s } ?item wdt:P1902 ?sp ; wdt:P2397 ?yt . }' % vals
        j = http(WIKIDATA, {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/sparql-results+json'},
                 urllib.parse.urlencode({'query': q, 'format': 'json'}).encode())
        for b in ((j or {}).get('results') or {}).get('bindings') or []:
            sp, yt = (b.get('sp') or {}).get('value', ''), (b.get('yt') or {}).get('value', '')
            if sp and re.fullmatch(r'UC[\w-]{22}', yt):
                out.setdefault(sp, []).append(yt)
    return out


def mb_channel_links(aid):
    """The YouTube channel links MusicBrainz has for the artist with this Spotify id."""
    time.sleep(MB_GAP)
    j = http(MB + 'url?' + urllib.parse.urlencode({'resource': 'https://open.spotify.com/artist/' + aid, 'inc': 'artist-rels', 'fmt': 'json'}))
    mbid = next((r['artist']['id'] for r in (j or {}).get('relations') or [] if (r.get('artist') or {}).get('id')), '')
    if not mbid:
        return []
    time.sleep(MB_GAP)
    d = http(MB + 'artist/' + mbid + '?' + urllib.parse.urlencode({'inc': 'url-rels', 'fmt': 'json'}))
    return [((r.get('url') or {}).get('resource')) or '' for r in (d or {}).get('relations') or []
            if 'youtube.com/' in (((r.get('url') or {}).get('resource')) or '')]


def channel_of(link, Y, tok):
    """A channel id from a YouTube channel link (/channel/UC…, or /@handle and /user/name at 1 unit each)."""
    m = re.search(r'/channel/(UC[\w-]{22})', link)
    if m:
        return m.group(1)
    m = re.search(r'/(@[\w.-]+)', link) or re.search(r'/user/([\w.-]+)', link)
    if not m:
        return ''
    key = 'forHandle' if m.group(1).startswith('@') else 'forUsername'
    j = Y.ytapi.get('channels', {'part': 'id', key: m.group(1)}, tok, units=1)
    return ((j.get('items') or [{}])[0]).get('id', '') if '_status' not in j else ''


def artist_channels(left, Y, tok, prog):
    """{Spotify artist id: [channel ids]} for the songs' lead artists: kept ones, then Wikidata, then MusicBrainz."""
    A, today = load_artists(), datetime.date.today()

    def stale(e):
        try:
            return not e.get('ch') and (today - datetime.date.fromisoformat(e.get('checked', '2000-01-01'))).days >= ARTIST_RETRY_DAYS
        except ValueError:
            return True
    want = []
    for s in left:
        a = (s.get('aids') or [''])[0]
        if a and a not in want and (a not in A or stale(A[a])):
            want.append(a)
    if want:
        prog("Looking for the artists' own YouTube channels (Wikidata)", None, len(want), force=True)
        w = wikidata_channels(want)
        for a in want:
            A[a] = {'ch': w.get(a, []), 'checked': today.isoformat(), 'from': 'Wikidata' if a in w else ''}
        miss = [a for a in want if not A[a]['ch']][:ARTIST_MB_MOST]
        for k, a in enumerate(miss):
            prog("Looking for the artists' own YouTube channels (MusicBrainz)", k + 1, len(miss))
            try:
                chs = [c for c in (channel_of(u, Y, tok) for u in mb_channel_links(a)) if c]
            except Y.ytapi.Quota:
                break
            if chs:
                A[a] = {'ch': sorted(set(chs)), 'checked': today.isoformat(), 'from': 'MusicBrainz'}
        save_artists(A)
    return {a: e['ch'] for a, e in A.items() if e.get('ch')}


def channel_uploads(ch, Y, tok, spent):
    """The channel's newest uploads [{id, title, channel}], 50 to a 1-unit read (spent: reads so far this run)."""
    out, page = [], ''
    for _ in range(CH_PAGES):
        if spent[0] >= CH_UNITS:
            break
        params = {'part': 'snippet', 'playlistId': 'UU' + ch[2:], 'maxResults': 50}
        if page:
            params['pageToken'] = page
        j = Y.ytapi.get('playlistItems', params, tok, units=1)
        spent[0] += 1
        if '_status' in j:
            break
        for it in j.get('items') or []:
            sn = it.get('snippet') or {}
            v = (sn.get('resourceId') or {}).get('videoId')
            if v:
                out.append({'id': v, 'title': Y.unescape(sn.get('title', '')), 'channel': sn.get('channelTitle', '')})
        page = j.get('nextPageToken')
        if not page:
            break
    return out


def mb_q(x):
    return '"' + re.sub(r'([\\"])', r'\\\1', x) + '"'


def musicbrainz(s, clean):
    """The YouTube video ids MusicBrainz links to the song's recording (the best two search matches)."""
    artist = (s.get('artists') or [s.get('artist', '')])[0]
    time.sleep(MB_GAP)
    j = http(MB + 'recording?' + urllib.parse.urlencode({'query': 'recording:%s AND artist:%s' % (mb_q(clean(s['title'])), mb_q(artist)), 'limit': 5, 'fmt': 'json'}))
    out = []
    for r in [r for r in (j or {}).get('recordings') or [] if int(r.get('score') or 0) >= 90][:2]:
        time.sleep(MB_GAP)
        d = http(MB + 'recording/' + r['id'] + '?' + urllib.parse.urlencode({'inc': 'url-rels', 'fmt': 'json'}))
        for rel in (d or {}).get('relations') or []:
            v = vid_of(((rel.get('url') or {}).get('resource')) or '')
            if v and v not in out:
                out.append(v)
        if out:
            break
    return out


def discogs(s, clean):
    """The YouTube video ids on the song's releases at Discogs (the first two found)."""
    if not DISCOGS_TOKEN:
        return []
    auth = {'Authorization': 'Discogs token=' + DISCOGS_TOKEN}
    artist = (s.get('artists') or [s.get('artist', '')])[0]
    time.sleep(DISCOGS_GAP)
    j = http(DISCOGS + 'database/search?' + urllib.parse.urlencode({'type': 'release', 'artist': artist, 'track': clean(s['title']), 'per_page': 5}), auth)
    out = []
    for r in ((j or {}).get('results') or [])[:2]:
        time.sleep(DISCOGS_GAP)
        d = http(DISCOGS + 'releases/%s' % r.get('id'), auth)
        for v in (d or {}).get('videos') or []:
            x = vid_of(v.get('uri') or '')
            if x and x not in out:
                out.append(x)
        if out:
            break
    return out


def find(songs, data, tok, Y, ms=None):
    """Members' songs that need a video (Y: youtube): each found the free ways, checked and kept in data. Returns
    (found, pending): how many were found, and the songs not tried yet (kept from YouTube's search this run)."""
    today = datetime.date.today()

    def asked_lately(e):
        try:
            return (today - datetime.date.fromisoformat(e.get('free', '2000-01-01'))).days < Y.RETRY_DAYS
        except ValueError:
            return False
    todo = [s for s in songs if s.get('upl') and 'ov' not in s and (s['id'] not in data['songs'] or Y.due(data['songs'][s['id']]))
            and not asked_lately(data['songs'].get(s['id'], {}))]
    todo.sort(key=lambda s: -s['n'])
    if not todo:
        return 0, set()
    print(f"{len(todo)} members' songs to try the free ways for")
    prog = getattr(Y, 'progress', lambda *a, **k: None)
    prog('Free ways first (Wikidata, artists\u2019 channels, MusicBrainz, Discogs): ' + str(len(todo)) + ' songs', 0, len(todo), force=True)
    found = 0
    left = list(todo)

    def check(cands, via):
        """Keep each song's first candidate that passes; the rest stay in left."""
        nonlocal found, left
        want = sorted({v for vs in cands.values() for v in vs})
        info = {}
        for k in range(0, len(want), 50):
            try:
                st = Y.ytapi.get('videos', {'part': 'snippet,status,contentDetails', 'id': ','.join(want[k:k + 50])}, tok, units=1)
            except Y.ytapi.Quota:
                print("no allowance left today to check the free ways' videos")
                return False
            for it in st.get('items', []):
                info[it['id']] = it
        took = set()
        for s in left:
            for v in cands.get(s['id'], []):
                it = info.get(v)
                if not it:
                    continue
                sn, stt = it.get('snippet') or {}, it.get('status') or {}
                c = {'id': v, 'title': Y.unescape(sn.get('title', '')), 'channel': sn.get('channelTitle', '')}
                c['score'], c['why'], c['kind'] = Y.score(s, c)
                c['secs'] = Y.iso_secs((it.get('contentDetails') or {}).get('duration'))
                c['embed'] = bool(stt.get('embeddable') and stt.get('privacyStatus') == 'public')
                Y.length_off(c, (ms or {}).get(s['id']) or s.get('ms'))
                if c['score'] < Y.GOOD or not c['embed']:
                    continue
                e = {'n': s['n'], 'checked': today.isoformat(), 'from': via, 'free': today.isoformat(), 'id': v, 'title': c['title'],
                     'channel': c['channel'], 'kind': c['kind'], 'score': c['score'], 'why': c['why'], 'embed': True}
                if c['secs'] is not None:
                    e['secs'] = c['secs']
                data['songs'][s['id']] = e
                took.add(s['id']); found += 1
                print(s['n'], s['title'], '—', s['artist'], '→', via, v, '|', c['channel'], '|', c['title'], c['score'])
                prog('Checking what ' + via + ' found', None, None, s['title'] + ' — ' + s['artist'], 'found on ' + via + ': ' + c['title'])
                break
        left = [s for s in left if s['id'] not in took]
        return True

    prog('Asking Wikidata for ' + str(len(left)) + ' songs', None, len(left), force=True)
    if not check(wikidata(left), 'Wikidata'):
        return found, {s['id'] for s in left}
    # the artist's own channel: its uploads read (no search) and the song matched among them
    try:
        chans, ups, spent, cands = artist_channels(left, Y, tok, prog), {}, [0], {}
        for k, s in enumerate(left):
            chs = chans.get((s.get('aids') or [''])[0]) or []
            if not chs:
                continue
            prog("Reading the artist's channel", k + 1, len(left), s['title'] + ' — ' + s['artist'])
            pool = []
            for ch in chs[:3]:
                if ch not in ups:
                    ups[ch] = channel_uploads(ch, Y, tok, spent)
                pool += ups[ch]
            scored = []
            for c in pool:
                sc = Y.score(s, c)[0]
                if sc >= Y.GOOD:
                    scored.append((sc, c['id']))
            if scored:
                cands[s['id']] = [v for _, v in sorted(scored, reverse=True)[:3]]
            elif chs:
                prog("Reading the artist's channel", k + 1, len(left), s['title'] + ' — ' + s['artist'], "not on the artist's channel")
        print(f"artists' channels: {len(ups)} read ({spent[0]} units), {len(cands)} songs with a likely upload")
        if cands and not check(cands, "the artist's channel"):
            return found, {s['id'] for s in left}
    except Y.ytapi.Quota:
        print("no allowance left today for the artists' channels")
    # MusicBrainz and Discogs take the first FREE_MOST still left; the songs MusicBrainz misses stay at the front, so
    # Discogs reaches each of them too: those have had every way
    reached = {s['id'] for s in left[:FREE_MOST]}
    for via, fn in (('MusicBrainz', musicbrainz), ('Discogs', discogs)):
        if via == 'Discogs' and not DISCOGS_TOKEN:
            continue
        batch = left[:FREE_MOST]

        def ask(k, s, fn=fn, via=via, batch=batch):
            prog('Asking ' + via, k + 1, len(batch), s['title'] + ' — ' + s['artist'])
            return fn(s, Y.clean_title)
        if not check({s['id']: ask(k, s) for k, s in enumerate(batch)}, via):
            return found, {s['id'] for s in left}
    # every way tried and missed: YouTube's search, after John's songs
    pending = set()
    for s in left:
        if s['id'] in reached:
            e = data['songs'].get(s['id']) or {'n': s['n'], 'checked': '2000-01-01'}
            e['free'] = today.isoformat(); data['songs'][s['id']] = e
        else:
            pending.add(s['id'])
    return found, pending


if __name__ == '__main__':   # python free_videos.py --check: whether each free way answers, with a song they all know
    import sys
    if '--check' in sys.argv:
        song = {'id': '4uLU6hMCjMI75M1A2tKUQC', 'title': 'Never Gonna Give You Up', 'artist': 'Rick Astley', 'artists': ['Rick Astley']}
        plain = lambda t: re.sub(r'\s*[\(\[].*?[\)\]]', '', t).strip()
        how = lambda: ('error ' + str(LAST.get('status') or LAST.get('error'))) if LAST else 'answered'
        print('free ways check (Never Gonna Give You Up):')
        w = wikidata([song]).get(song['id'])
        print('  Wikidata:', w or 'no video for it', '(' + how() + ')')
        q = 'recording:%s AND artist:%s' % (mb_q(song['title']), mb_q(song['artist']))
        j = http(MB + 'recording?' + urllib.parse.urlencode({'query': q, 'limit': 5, 'fmt': 'json'}))
        recs = (j or {}).get('recordings') or []
        print('  MusicBrainz search:', len(recs), 'recordings, best scores', [r.get('score') for r in recs[:5]], '(' + how() + ')')
        time.sleep(MB_GAP)
        print('  MusicBrainz:', musicbrainz(song, plain) or 'no video for it', '(' + how() + ')')
        print('  Discogs:', (discogs(song, plain) or 'no video for it') if DISCOGS_TOKEN else 'no DISCOGS_TOKEN secret', '(' + how() + ')' if DISCOGS_TOKEN else '')
