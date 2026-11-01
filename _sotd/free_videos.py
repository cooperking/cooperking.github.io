#!/usr/bin/env python3
"""Free ways to find a YouTube video for members' songs before YouTube's own search, which spends the searches on
John's account (John, 1 Nov 2026: "try multiple different free ways and stop when we find a match"). Used by youtube.py.

In turn, each song stopping at the first match that passes:
  1. Wikidata: songs with the Spotify track id (P2207) and a YouTube video id (P1651); one query asks 200 songs. No key.
  2. MusicBrainz: the recording, searched by title and artist, and its YouTube links. No key; one request a second.
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
                break
        left = [s for s in left if s['id'] not in took]
        return True

    if not check(wikidata(left), 'Wikidata'):
        return found, {s['id'] for s in left}
    # MusicBrainz and Discogs take the first FREE_MOST still left; the songs MusicBrainz misses stay at the front, so
    # Discogs reaches each of them too: those have had every way
    reached = {s['id'] for s in left[:FREE_MOST]}
    for via, fn in (('MusicBrainz', musicbrainz), ('Discogs', discogs)):
        if via == 'Discogs' and not DISCOGS_TOKEN:
            continue
        if not check({s['id']: fn(s, Y.clean_title) for s in left[:FREE_MOST]}, via):
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
