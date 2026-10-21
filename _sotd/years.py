#!/usr/bin/env python3
"""The year each song first came out, for the calendar (songs/years.json: Spotify track id -> year).

Two sources, and the earlier year wins:
  MusicBrainz  the recording's first release date (data CC0), from recordings with the song's title by the same artist;
  Spotify      the album's release date, from John's playlist export (data/spotify-dates.json), which gives the
               reissue year for remasters and compilations, so on its own it can be late.
When MusicBrainz has no recording under the song's title, it tries the title without any bracketed or " - " part
("(Bonus Track)", "- Instrumental"), then the album's first release. A song MusicBrainz doesn't know keeps Spotify's year. John's corrections (data/john-years.json: {track id: year})
win over both. MusicBrainz asks for at most one request a second."""
import datetime, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'data', 'years.json')
SPOTIFY = os.path.join(HERE, 'data', 'spotify-dates.json')
JOHN = os.path.join(HERE, 'data', 'john-years.json')
SITE = os.path.join(os.path.dirname(HERE), 'songs', 'years.json')
SONGS_URL = os.environ.get('SOTD_SONGS_URL', 'https://sotd-playlists.cooperking.workers.dev/songs.json')
MB = 'https://musicbrainz.org/ws/2/'
UA = {'User-Agent': 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)', 'Accept': 'application/json'}
RETRY_DAYS = 60
VERSION = 2   # songs looked up by an earlier version without finding a year are looked up again


def get(url):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            print('  retry', e)
            time.sleep(3 * (i + 1))
    return None


def fold(x):
    x = re.sub(r"['’`]", '', x or '')
    x = unicodedata.normalize('NFKD', x).encode('ascii', 'ignore').decode().lower().replace('&', ' and ')
    return re.sub(r'[^a-z0-9]+', ' ', x).strip()


def clean_title(t):
    t = re.sub(r'\s+-\s+.*(remaster|version|mix|edit|mono|stereo|single|live|demo|take|recording|anniversary|bonus).*$', '', t, flags=re.I)
    t = re.sub(r'\s*[(\[](feat|with|ft)\.?[^)\]]*[)\]]', '', t, flags=re.I)
    t = re.sub(r'\s*[(\[][^)\]]*(remaster|version|mono|stereo|edit|mix)[^)\]]*[)\]]', '', t, flags=re.I)
    return t.strip() or t


def base_title(t):
    """The title with every bracketed part and anything after " - " taken off."""
    t = re.sub(r'\s+-\s+.*$', '', t)
    t = re.sub(r'\s*[(\[][^)\]]*[)\]]', '', t)
    return t.strip()


def q(x):
    return x.replace('\\', '\\\\').replace('"', '\\"')


def first_year(x):
    m = re.match(r'(\d{4})', x or '')
    return int(m.group(1)) if m and 1900 < int(m.group(1)) <= datetime.date.today().year else None


def recordings(title, artist, loose):
    time.sleep(1.1)
    j = get(MB + 'recording?' + urllib.parse.urlencode({
        'query': f'recording:"{q(title)}" AND artist:"{q(artist)}"', 'limit': 25, 'fmt': 'json'}))
    if not j:
        return None
    ft, fa = fold(title), fold(artist)
    years = []
    for r in j.get('recordings', []):
        rt = base_title(r.get('title', '')) if loose else clean_title(r.get('title', ''))
        if r.get('score', 0) < 85 or fold(rt) != ft:
            continue
        names = ' '.join(fold(c.get('name') or (c.get('artist') or {}).get('name', '')) for c in r.get('artist-credit', []) if isinstance(c, dict))
        if fa not in names:
            continue
        y = first_year(r.get('first-release-date'))
        if y:
            years.append(y)
    return years


def album_year(s, artist):
    """The first release of the song's album (for a song too new, or too obscure, to have its recording listed)."""
    album = base_title(s.get('album') or '')
    if not album:
        return None
    time.sleep(1.1)
    j = get(MB + 'release-group?' + urllib.parse.urlencode({
        'query': f'releasegroup:"{q(album)}" AND artist:"{q(artist)}"', 'limit': 10, 'fmt': 'json'}))
    fa = fold(artist)
    for g in (j or {}).get('release-groups', []):
        names = ' '.join(fold(c.get('name') or (c.get('artist') or {}).get('name', '')) for c in g.get('artist-credit', []) if isinstance(c, dict))
        if g.get('score', 0) >= 90 and fold(base_title(g.get('title', ''))) == fold(album) and fa in names:
            return first_year(g.get('first-release-date'))
    return None


def mb_year(s):
    artist = (s.get('artists') or [s['artist']])[0]
    title = clean_title(s['title'])
    years = recordings(title, artist, False)
    if years is None:
        return None, 'no answer'
    if not years and base_title(title) and base_title(title) != title:
        years = recordings(base_title(title), artist, True) or []
    if years:
        return min(years), f'{len(years)} recordings'
    y = album_year(s, artist)
    return (y, 'album') if y else (None, 'not found')


def main():
    data = {'about': "Year each song first came out: the earlier of MusicBrainz's first release and Spotify's album date.", 'songs': {}}
    if os.path.exists(OUT):
        with open(OUT) as f:
            data = json.load(f)
    sp = json.load(open(SPOTIFY)).get('dates', {}) if os.path.exists(SPOTIFY) else {}
    john = json.load(open(JOHN)).get('years', {}) if os.path.exists(JOHN) else {}
    feed = get(SONGS_URL)
    if not feed:
        sys.exit('could not read the song list')
    today = datetime.date.today()
    done = 0
    for s in feed['songs']:
        e = data['songs'].get(s['id'])
        # look again: a song with no year at all (a new song MusicBrainz may list soon), one an earlier version
        # couldn't find, and every 60 days one MusicBrainz didn't know
        if e and (e.get('mb') or (e.get('sp') and e.get('v', 1) >= VERSION
                                  and (today - datetime.date.fromisoformat(e.get('checked', '2000-01-01'))).days < RETRY_DAYS)):
            continue
        mb, note = mb_year(s)
        if note == 'no answer':   # MusicBrainz didn't answer: try again next run
            continue
        spy = int(sp[s['id']][:4]) if sp.get(s['id']) else None
        e = {'n': s['n'], 'checked': today.isoformat(), 'mb': mb, 'mbNote': note, 'sp': spy, 'v': VERSION}
        data['songs'][s['id']] = e
        done += 1
        print(s['n'], s['title'], '—', s['artist'], '| MusicBrainz', mb, f'({note})', '| Spotify', spy)
    if done:
        with open(OUT, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=0, separators=(',', ':'))
    out = {}
    for t, e in data['songs'].items():
        ys = [y for y in (e.get('mb'), e.get('sp')) if y and y > 1900]   # Spotify puts 1900 for an unknown date
        if ys:
            out[t] = min(ys)
    for t, y in john.items():
        if y:
            out[t] = int(y)
    print(done, 'looked up;', len(out), 'songs with a year')
    new = json.dumps({'about': 'Year each song first came out (Spotify track id: year)', 'years': out}, separators=(',', ':'), sort_keys=True)
    if not os.path.exists(SITE) or open(SITE).read() != new:
        with open(SITE, 'w') as f:
            f.write(new)


if __name__ == '__main__':
    main()
