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


def get(url):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 403, 404):
                return json.loads(e.read() or b'{}')
            time.sleep(3 * (i + 1))
        except Exception:
            time.sleep(3 * (i + 1))
    return None


def call(method, **params):
    time.sleep(0.25)   # Last.fm asks for no more than five requests a second
    j = get(API + '?' + urllib.parse.urlencode(dict(method=method, api_key=KEY, format='json', autocorrect=1, **params)))
    if not j or 'error' in j:
        if j and j.get('error') in (10, 26):   # invalid or suspended key
            sys.exit('Last.fm refused the API key: ' + j.get('message', ''))
        return []
    tags = (j.get('toptags') or {}).get('tag') or []
    if isinstance(tags, dict):
        tags = [tags]
    return [[t['name'].lower().strip(), int(t.get('count') or 0)] for t in tags if int(t.get('count') or 0) > 0][:40]


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
    todo = [s for s in songs if now - cache.get(s['id'], {}).get('at', 0) > MAX_AGE]
    print(f'{len(todo)} songs to fetch listener tags for')
    artists = {}
    for s in todo:
        artist = (s.get('artists') or [s['artist']])[0]
        track = call('track.getTopTags', artist=artist, track=clean_title(s['title']))
        if artist not in artists:
            artists[artist] = call('artist.getTopTags', artist=artist)
        cache[s['id']] = {'at': now, 'track': track, 'artist': artists[artist]}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, 'w') as f:
        json.dump(cache, f)
    have = sum(1 for s in songs if cache.get(s['id'], {}).get('track'))
    print(f'{have} of {len(songs)} songs have track tags')


if __name__ == '__main__':
    main()
