#!/usr/bin/env python3
"""Each film's and series' best trailer, from TMDB (John, 9 Nov 2026: "A lot of trailers are pretty low quality ... For now
tmdb best option"). Jellyfin keeps whatever YouTube links TMDB has, in no order, and Grifflix played the first; this
asks TMDB for each title's videos and puts them best first: official, a trailer (not a teaser or a clip), the largest,
in English, made for the UK (else the US). The rest stay after it, so a trailer that won't play moves on to the next.

Writes grifflix/data/trailers.json, by title and year (as added.json: a film converted on the Mac keeps its trailers):
  {"movie|cabaret|1972": ["key1", "key2", ...], ...}
data_publish.py puts these in place of Jellyfin's on each Update. Run again now and then for new films:
  python3 _grifflix/tmdb_trailers.py          (with a TMDB read access token: TMDB_TOKEN, or a proxy that adds it)
"""
import concurrent.futures, glob, json, os, re, sys, unicodedata, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'grifflix', 'data')
API = 'https://api.themoviedb.org/3/'
TOKEN = os.environ.get('TMDB_TOKEN', 'proxy-injected')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_publish import added_key   # noqa: E402  (the same key as the dates added)


def get(path, **q):
    u = API + path + ('?' + urllib.parse.urlencode(q) if q else '')
    for _ in range(4):
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, headers={'Authorization': 'Bearer ' + TOKEN, 'Accept': 'application/json'}), timeout=20)
            return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            if e.code != 429:
                raise
        except Exception:
            pass
        import time
        time.sleep(2)
    return {}


def norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower()
    s = s.replace('&', 'and')
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def score(v):
    t = {'Trailer': 50, 'Teaser': 12}.get(v.get('type'), 0)
    return (t + (100 if v.get('official') else 0) + {2160: 34, 1080: 30, 720: 15}.get(v.get('size') or 0, 0)
            + (10 if v.get('iso_639_1') == 'en' else 0) + {'GB': 8, 'US': 5}.get(v.get('iso_3166_1'), 0),
            v.get('published_at') or '')


def best(videos):
    vs = [v for v in videos if v.get('site') == 'YouTube' and v.get('key') and v.get('type') in ('Trailer', 'Teaser')]
    return [v['key'] for v in sorted(vs, key=score, reverse=True)]


def one(it, have):
    kind = 'tv' if it.get('type') == 'series' else 'movie'
    name, year = it.get('name') or '', it.get('year')
    q = {'query': name}
    if year:
        q['first_air_date_year' if kind == 'tv' else 'year'] = year
    res = (get('search/' + kind, **q).get('results') or [])[:5]
    want = norm(name)
    same = [r for r in res if want in (norm(r.get('title') or r.get('name')), norm(r.get('original_title') or r.get('original_name')))]
    for r in (same or res[:2]):
        keys = best(get('%s/%d/videos' % (kind, r['id'])).get('results') or [])
        if not keys:
            continue
        if have and set(keys) & set(have):   # (the same film: TMDB has the trailers Jellyfin has)
            return keys + [k for k in have if k not in keys]
        if r in same and not have:   # (no trailer before: a title and year that match exactly)
            return keys
        if r in same and have:
            return keys + [k for k in have if k not in keys]
    return None


def main():
    L = json.load(open(os.path.join(DATA, 'library.json'), encoding='utf-8'))
    det = {}
    for f in glob.glob(os.path.join(DATA, 'd', '*.json')):
        det.update(json.load(open(f, encoding='utf-8')))
    ids = [i for i in L['movies'] + L['tv'] if isinstance(i, str) and i in L['items']]
    out, changed, new = {}, 0, 0

    def job(i):
        it = L['items'][i]
        have = (det.get(i) or {}).get('trailers') or []
        try:
            return i, have, one(it, have)
        except Exception as e:
            print('  %s: %s' % (it.get('name'), e))
            return i, have, None

    with concurrent.futures.ThreadPoolExecutor(12) as ex:
        for n, (i, have, keys) in enumerate(ex.map(job, ids), 1):
            if keys:
                out[added_key(L['items'][i])] = keys[:6]
                changed += bool(have) and keys[0] != have[0]
                new += not have
            if n % 200 == 0:
                print('%d of %d' % (n, len(ids)))
    with open(os.path.join(DATA, 'trailers.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',', ':'), ensure_ascii=False, sort_keys=True)
    print('%d titles from TMDB: %d with a better first trailer, %d with trailers they had none of' % (len(out), changed, new))


if __name__ == '__main__':
    main()
