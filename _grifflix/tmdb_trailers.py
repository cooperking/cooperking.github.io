#!/usr/bin/env python3
"""Each film's and series' best trailer, from TMDB (John, 9 Nov 2026: "A lot of trailers are pretty low quality ... For now
tmdb best option"). Jellyfin keeps whatever YouTube links TMDB has, in no order, and Grifflix played the first; this
asks TMDB for each title's videos and puts them best first: official, a trailer (not a teaser or a clip), the largest,
in English, made for the UK (else the US). The rest stay after it, so a trailer that won't play moves on to the next.

And each one's landscape poster (its own artwork with its title, from TMDB's backdrops), for the wide pictures in place
of Jellyfin's backdrop: grifflix/data/landscapes.json, {"movie|cabaret|1972": "/abc.jpg"} (a TMDB picture's path).

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


def match(it, have):
    """The title on TMDB (kind, id) and its trailers best first; (None, None) if it can't be told."""
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
        if have and keys and set(keys) & set(have):   # (the same film: TMDB has the trailers Jellyfin has)
            return (kind, r['id']), keys + [k for k in have if k not in keys]
        if r in same and keys:
            return (kind, r['id']), keys + [k for k in have if k not in keys]
    if same and len(same) == 1:   # (no trailers to check by: one title and year that match exactly)
        return (kind, same[0]['id']), None
    return None, None


def landscape(kind, tid):
    """Its landscape poster: the film's own artwork with its title (TMDB's backdrops in English), the best voted and
    largest; else its best picture without words (John, 9 Nov 2026: "I actually want a landscaped poster of the film - real")."""
    bs = get('%s/%d/images' % (kind, tid), include_image_language='en,null').get('backdrops') or []
    bs = [b for b in bs if b.get('file_path') and (b.get('width') or 0) >= 1280]
    titled = [b for b in bs if b.get('iso_639_1') == 'en']
    pick = sorted(titled or bs, key=lambda b: (b.get('vote_average') or 0, b.get('vote_count') or 0, b.get('width') or 0), reverse=True)
    return pick[0]['file_path'] if pick and titled else None


def main():
    L = json.load(open(os.path.join(DATA, 'library.json'), encoding='utf-8'))
    det = {}
    for f in glob.glob(os.path.join(DATA, 'd', '*.json')):
        det.update(json.load(open(f, encoding='utf-8')))
    ids = [i for i in L['movies'] + L['tv'] if isinstance(i, str) and i in L['items']]
    out, lands, changed, new = {}, {}, 0, 0

    def job(i):
        it = L['items'][i]
        have = (det.get(i) or {}).get('trailers') or []
        try:
            t, keys = match(it, have)
            return i, have, keys, (landscape(*t) if t else None)
        except Exception as e:
            print('  %s: %s' % (it.get('name'), e))
            return i, have, None, None

    with concurrent.futures.ThreadPoolExecutor(12) as ex:
        for n, (i, have, keys, land) in enumerate(ex.map(job, ids), 1):
            k = added_key(L['items'][i])
            if keys:
                out[k] = keys[:6]
                changed += bool(have) and keys[0] != have[0]
                new += not have
            if land:
                lands[k] = land
            if n % 200 == 0:
                print('%d of %d' % (n, len(ids)))
    for name, obj in (('trailers.json', out), ('landscapes.json', lands)):
        with open(os.path.join(DATA, name), 'w', encoding='utf-8') as f:
            json.dump(obj, f, separators=(',', ':'), ensure_ascii=False, sort_keys=True)
    print('%d titles from TMDB: %d with a better first trailer, %d with trailers they had none of; %d landscape posters'
          % (len(out), changed, new, len(lands)))


if __name__ == '__main__':
    main()
