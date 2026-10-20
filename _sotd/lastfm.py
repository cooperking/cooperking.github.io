#!/usr/bin/env python3
"""Fetches Last.fm listener tags for each song (track tags, plus the artist's tags for genres) into a cache
outside the repo (~/.cache/lastfm/tags.json, kept between runs by GitHub's Actions cache).

The raw tags are never committed or published: make_tags.py only uses them to weigh up moods and genres.
Needs the LASTFM_API_KEY secret; without it this does nothing. Tags are refreshed after 60 days.
"""
import json, os, re, sys, time, urllib.parse, urllib.request

KEY = os.environ.get('LASTFM_API_KEY', '').strip()
CACHE = os.path.expanduser(os.environ.get('LASTFM_CACHE', '~/.cache/lastfm/tags.json'))
SONGS_URL = os.environ.get('SOTD_SONGS_URL', 'https://sotd-playlists.cooperking.workers.dev/songs.json')
API = 'https://ws.audioscrobbler.com/2.0/'
UA = {'User-Agent': 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)'}
MAX_AGE = 60 * 86400


ERRORS = {}
COUNTS = {}


def get(url):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b'{}')
            except ValueError:
                body = {}
            if body.get('error') not in (8, 11, 16, 29):   # only these are worth retrying
                return body or {'error': 'http %d' % e.code}
            ERRORS['retry %s' % body.get('error')] = ERRORS.get('retry %s' % body.get('error'), 0) + 1
            time.sleep(10 * (i + 1))
        except Exception as e:
            ERRORS['network'] = ERRORS.get('network', 0) + 1
            time.sleep(5 * (i + 1))
    return None


def call(method, **params):
    """Top tags as [[name, count]], or None when Last.fm couldn't answer (try again next run)."""
    time.sleep(0.5)   # Last.fm asks for no more than five requests a second; stay well under
    j = get(API + '?' + urllib.parse.urlencode(dict(method=method, api_key=KEY, format='json', autocorrect=1, **params)))
    if j is None:
        ERRORS['gave up'] = ERRORS.get('gave up', 0) + 1
        return None
    if 'error' in j:
        code = j.get('error')
        if code in (10, 26):   # invalid or suspended key
            sys.exit('Last.fm refused the API key: ' + str(j.get('message', '')))
        ERRORS['error %s' % code] = ERRORS.get('error %s' % code, 0) + 1
        return [] if code == 6 else None   # 6 = not found on Last.fm
    if method == 'track.getInfo':   # top tags without counts: weigh them by rank
        tags = ((j.get('track') or {}).get('toptags') or {}).get('tag') or []
        if isinstance(tags, dict):
            tags = [tags]
        return [[t['name'].lower().strip(), max(20, 100 - 20 * i)] for i, t in enumerate(tags)]
    tags = (j.get('toptags') or {}).get('tag') or []
    if isinstance(tags, dict):
        tags = [tags]
    return [[t['name'].lower().strip(), int(t.get('count') or 0)] for t in tags if int(t.get('count') or 0) > 0][:40]


def track_tags(artist, title):
    tags = call('track.getTopTags', artist=artist, track=title)
    if tags:
        COUNTS['from getTopTags'] = COUNTS.get('from getTopTags', 0) + 1
        return tags
    info = call('track.getInfo', artist=artist, track=title)   # getTopTags often comes back empty
    if info:
        COUNTS['from getInfo'] = COUNTS.get('from getInfo', 0) + 1
        return info
    return None if tags is None and info is None else []


def clean_title(t):
    t = re.sub(r'\s+-\s+.*(remaster|version|mix|edit|mono|stereo|single|live|demo|take|recording|anniversary|bonus).*$', '', t, flags=re.I)
    return re.sub(r'\s*[(\[](feat|with|ft)\.?[^)\]]*[)\]]', '', t, flags=re.I).strip()


def main():
    if not KEY:
        print('No LASTFM_API_KEY secret yet; skipping listener tags.')
        return
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            cache = json.load(f)
    with urllib.request.urlopen(urllib.request.Request(SONGS_URL, headers=UA), timeout=60) as r:
        songs = json.loads(r.read())['songs']
    now = time.time()
    # refresh tags after 60 days, or after 14 when Last.fm had none for the song
    todo = [s for s in songs if now - cache.get(s['id'], {}).get('at', 0) > (MAX_AGE if cache.get(s['id'], {}).get('track') else MAX_AGE / 4)]
    print(f'{len(todo)} songs to fetch listener tags for')
    artists = {}
    for s in todo:
        artist = (s.get('artists') or [s['artist']])[0]
        track = track_tags(artist, clean_title(s['title']))
        if artist not in artists:
            artists[artist] = call('artist.getTopTags', artist=artist)
        if track is None or artists[artist] is None:
            continue   # Last.fm didn't answer: leave it for the next run
        cache[s['id']] = {'at': now, 'track': track, 'artist': artists[artist]}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, 'w') as f:
        json.dump(cache, f)
    probe = {}
    for artist, title in (('Louis Armstrong', 'What A Wonderful World'), ('Warren Zevon', 'Werewolves of London'), ('Cher', 'Believe')):
        time.sleep(0.5)
        a = get(API + '?' + urllib.parse.urlencode(dict(method='track.getTopTags', api_key=KEY, format='json', artist=artist, track=title))) or {}
        time.sleep(0.5)
        b = get(API + '?' + urllib.parse.urlencode(dict(method='track.getInfo', api_key=KEY, format='json', artist=artist, track=title))) or {}
        tt = (a.get('toptags') or {}).get('tag')
        it = ((b.get('track') or {}).get('toptags') or {}).get('tag')
        probe[title] = {'getTopTags': {'keys': sorted(a), 'error': a.get('error'), 'tags': len(tt) if isinstance(tt, list) else type(tt).__name__},
                        'getInfo': {'keys': sorted(b), 'error': b.get('error'), 'found': bool(b.get('track')),
                                    'trackKeys': sorted((b.get('track') or {})), 'tags': len(it) if isinstance(it, list) else type(it).__name__}}
    status = {
        'probe': probe,
        'about': 'How the last Last.fm fetch went (counts only; the tags themselves stay out of the repo).',
        'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
        'songs': len(songs), 'fetched': len(todo), 'cached': sum(1 for s in songs if s['id'] in cache),
        'withTrackTags': sum(1 for s in songs if cache.get(s['id'], {}).get('track')),
        'songNumbersWithTrackTags': [s['n'] for s in songs if cache.get(s['id'], {}).get('track')],
        'withArtistTags': sum(1 for s in songs if cache.get(s['id'], {}).get('artist')),
        'trackTagsFrom': COUNTS,
        'problems': ERRORS,
    }
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'lastfm-status.json'), 'w') as f:
        json.dump(status, f, indent=1)
    print(status)


if __name__ == '__main__':
    main()
