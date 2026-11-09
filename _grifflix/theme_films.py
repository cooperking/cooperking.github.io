#!/usr/bin/env python3
"""Each monthly theme's own film, from TMDB, for its month's 1st even when Grifflix hasn't the film (John, 9 Nov 2026:
"Adding a theme to a month should automatically add its film to the calendar on the first of that month - it should be
possible to add it even if the film isn't there - as it should just play the trailer").

Writes grifflix/data/themefilms.json, by theme:
  {"hotel": {"id": "<32 hex>", "ext": 1, "type": "movie", "name": ..., "year": ..., "mins": ..., "overview": ...,
             "poster": "/tmdb-path.jpg", "backdrop": "/tmdb-path.jpg", "trailers": ["youtube key", ...], "tmdb": 120467}, ...}
The id is made from the TMDB id (the same each time), so the Mac's calendar keeps it; the pictures are TMDB's own (the page
shows a picture path starting "/" from TMDB). The admin page puts these on the 1sts when Grifflix hasn't the film itself.
Run again when a theme is added:  python3 _grifflix/theme_films.py   (with a TMDB token: TMDB_TOKEN, or a proxy that adds it)
"""
import hashlib, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'web'))
from tmdb_trailers import get, norm, best, landscape   # noqa: E402
from page import theme_list   # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), 'grifflix', 'data', 'themefilms.json')


def film(name, year):
    res = (get('search/movie', query=name, year=year).get('results') or []) or (get('search/movie', query=name).get('results') or [])
    same = [r for r in res if norm(r.get('title')) == norm(name) or norm(r.get('original_title')) == norm(name)]
    r = (same or res or [None])[0]
    if not r:
        return None
    d = get('movie/%d' % r['id'], append_to_response='videos,release_dates')
    keys = best((d.get('videos') or {}).get('results') or [])
    cert = ''
    for c in (d.get('release_dates') or {}).get('results') or []:
        if c.get('iso_3166_1') == 'GB':
            cert = next((x.get('certification') for x in c.get('release_dates') or [] if x.get('certification')), '') or cert
    land = landscape('movie', r['id'])
    return {'id': hashlib.md5(('tmdb-movie-%d' % r['id']).encode()).hexdigest(), 'ext': 1, 'type': 'movie', 'tmdb': r['id'],
            'name': d.get('title') or name, 'year': int((d.get('release_date') or str(year))[:4] or year), 'mins': d.get('runtime') or None,
            'cert': cert or None, 'overview': (d.get('overview') or '')[:400], 'poster': d.get('poster_path'),
            'backdrop': land or d.get('backdrop_path'), 'trailers': keys[:6]}


def main():
    out = {}
    for t in theme_list():
        if not t.get('f') or not t.get('y') or t['k'] in ('picture', 'blank'):
            continue
        f = film(t['f'].replace('’', "'"), t['y'])
        if f:
            out[t['k']] = {k: v for k, v in f.items() if v not in (None, '', [])}
            print('%-10s %s (%s): %d trailers' % (t['k'], f['name'], f['year'], len(f['trailers'])))
        else:
            print('%-10s not found: %s' % (t['k'], t['f']))
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


if __name__ == '__main__':
    main()
