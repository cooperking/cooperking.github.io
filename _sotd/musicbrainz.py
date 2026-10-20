#!/usr/bin/env python3
"""Looks up MusicBrainz tags for songs not in _sotd/data/musicbrainz.json yet: tags on the song's album,
its credited artists (or their releases, when the artist has none) and the recording.
MusicBrainz data is CC0. One request a second, as MusicBrainz asks."""
import json, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from genres import bucket   # noqa: E402

OUT = os.path.join(HERE, 'data', 'musicbrainz.json')
SONGS_URL = os.environ.get('SOTD_SONGS_URL', 'https://sotd-playlists.cooperking.workers.dev/songs.json')
MB = 'https://musicbrainz.org/ws/2/'
UA = {'User-Agent': 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)', 'Accept': 'application/json'}


def get(url, headers=UA):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            print('  retry', e)
            time.sleep(3 * (i + 1))
    return None


def mb(path):
    time.sleep(1.1)
    return get(MB + path + ('&' if '?' in path else '?') + 'fmt=json')


def q(x):
    return x.replace('\\', '\\\\').replace('"', '\\"')


def tags(obj):
    return [[t['name'], t.get('count', 1)] for t in (obj or {}).get('tags') or []]


def lookup(s):
    title = re.sub(r'\s+-\s+.*(remaster|version|mix|edit|mono|stereo|single|live|demo|take|recording|anniversary|bonus).*$', '',
                   s['title'], flags=re.I)
    title = re.sub(r'\s*[(\[](feat|with|ft)\.?[^)\]]*[)\]]', '', title, flags=re.I).strip()
    first = (s.get('artists') or [s['artist']])[0]
    e = {'n': s['n']}
    found = mb('recording?query=' + urllib.parse.quote(f'recording:"{q(title)}" AND artist:"{q(first)}"') + '&limit=3')
    hit = next((r for r in (found or {}).get('recordings', []) if r.get('score', 0) >= 85), None)
    album = re.sub(r'\s*[(\[][^)\]]*(remaster|deluxe|edition|expanded|version|anniversary|mono|stereo|bonus|reissue)[^)\]]*[)\]]', '',
                   s.get('album') or '', flags=re.I)
    album = re.sub(r'\s+-\s+.*(remaster|deluxe|edition|version|anniversary).*$', '', album, flags=re.I).strip()
    if album:
        rgs = mb('release-group?query=' + urllib.parse.quote(f'releasegroup:"{q(album)}" AND artist:"{q(first)}"') + '&limit=3')
        rg = next((x for x in (rgs or {}).get('release-groups', []) if x.get('score', 0) >= 90), None)
        if rg and tags(rg):
            e['album'] = tags(rg)
    artist = []
    for ac in (hit or {}).get('artist-credit', [])[:2]:
        if not isinstance(ac, dict) or 'artist' not in ac:
            continue
        a = mb('artist/' + ac['artist']['id'] + '?inc=tags')
        t = tags(a)
        if not any(bucket(n) for n, _ in t):   # artist has no usable tags: count the tags on its releases
            rgs = mb('release-group?query=' + urllib.parse.quote('arid:' + ac['artist']['id']) + '&limit=100')
            t = [x for g in (rgs or {}).get('release-groups', []) for x in tags(g)]
        artist += t
    if artist:
        e['artist'] = artist
    if hit and hit.get('tags'):
        e['recording'] = [[t['name'], 1] for t in hit['tags']]
    return e


def main():
    data = {'songs': {}}
    if os.path.exists(OUT):
        with open(OUT) as f:
            data = json.load(f)
    songs = get(SONGS_URL, {'User-Agent': UA['User-Agent']})['songs']
    todo = [s for s in songs if s['id'] not in data['songs']]
    print(f'{len(todo)} songs to look up on MusicBrainz')
    for s in todo:
        e = lookup(s)
        data['songs'][s['id']] = e
        print(s['n'], s['title'], '—', ', '.join(f'{k} {len(v)}' for k, v in e.items() if k != 'n') or 'nothing found')
    if todo:
        with open(OUT, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=0, separators=(',', ':'))


if __name__ == '__main__':
    main()
