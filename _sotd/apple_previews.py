#!/usr/bin/env python3
"""Every song Spotify gives no preview, looked for in Apple's public iTunes Search in one go (John, 4 Nov 2026: "Can you
go through every missing preview now"). The Worker does a few a timer run by itself; this goes through all of them.

    python3 _sotd/apple_previews.py

Gets the songs from the Worker's /upl/pvneed (this job's GITHUB_TOKEN), matches each as the Worker's itunesFind does
(the song's title and lead artist in Apple's; not live, a cover, karaoke and the like unless the song is; the same
version first), and sends what it found every 25 songs. Apple allows about 20 searches a minute, so it waits GAP seconds
between them, and longer when Apple says slow down.
"""
import json, os, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request

URL = os.environ.get('SOTD_WORKER', 'https://sotd-playlists.cooperking.workers.dev') + '/upl/pvneed'
UA = 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)'
GAP = 3.3
ODD = re.compile(r'\b(live|karaoke|cover|instrumental|tribute|remix|acoustic|demo)\b')


def fold(t):
    t = ''.join(c for c in unicodedata.normalize('NFKD', t or '') if not unicodedata.combining(c)).lower()
    t = re.sub(r"['’`]", '', t.replace('&', ' and '))
    return re.sub(r'[\W_]+', ' ', t).strip()


def main_title(t):
    return fold(re.sub(r'\s*[(\[].*?[)\]]', '', re.sub(r'\s+-\s+.*$', '', t or '')))


def worker(body=None):
    tok = os.environ.get('GITHUB_TOKEN', '').strip()
    req = urllib.request.Request(URL, data=json.dumps(body).encode() if body is not None else None, method='POST' if body is not None else 'GET',
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json', 'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def search(term, country):
    url = 'https://itunes.apple.com/search?' + urllib.parse.urlencode({'term': term, 'entity': 'song', 'limit': 15, 'country': country})
    for wait in (0, 30, 90, 180):
        time.sleep(wait or GAP)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=30) as r:
                return json.loads(r.read()).get('results') or []
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 503):
                print('  Apple says slow down; waiting', flush=True)
                continue
            return []
        except (OSError, ValueError):
            continue
    return None


def find(s):
    title, lead = main_title(s['title']), fold((s.get('artists') or [s.get('artist', '').split(',')[0]])[0])
    if not title or not lead:
        return ''
    full = fold(s['title'])
    for country in ('GB', 'US'):
        res = search(title + ' ' + lead, country)
        if res is None:
            return None
        ok = [y for y in res if y.get('previewUrl') and y.get('trackId') and lead in fold(y.get('artistName', ''))
              and (' ' + title + ' ') in (' ' + fold(y.get('trackName', '')) + ' ')
              and not (ODD.search(fold(y.get('trackName', '') + ' ' + y.get('collectionName', ''))) and not ODD.search(fold(s['title'])))]
        ok.sort(key=lambda y: 0 if fold(y['trackName']) == full else 1 if main_title(y['trackName']) == title else 2)
        if ok:
            return 'i%d' % ok[0]['trackId']
    return ''


def main():
    if not os.environ.get('GITHUB_TOKEN'):
        print('no GITHUB_TOKEN'); sys.exit(1)
    songs = worker()['songs']
    print(len(songs), 'songs with no preview from Spotify', flush=True)
    found, asked, got = {}, [], 0
    for k, s in enumerate(songs, 1):
        r = find(s)
        if r is None:
            print('Apple stopped answering; the rest wait for the Worker to do a few a run', flush=True)
            break
        if r:
            found[s['id']] = r; got += 1
            print(k, s['title'], '—', s['artist'], '→', r, flush=True)
        else:
            asked.append(s['id'])
        if len(found) + len(asked) >= 25:
            worker({'found': found, 'asked': asked}); found, asked = {}, []
    if found or asked:
        worker({'found': found, 'asked': asked})
    print(got, 'of', len(songs), 'now have Apple’s preview', flush=True)


if __name__ == '__main__':
    main()
