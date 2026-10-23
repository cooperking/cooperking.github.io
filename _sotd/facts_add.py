#!/usr/bin/env python3
"""Adds written and checked song facts to the list waiting for John's sign-off (_sotd/data/facts-pending.json).

    python3 _sotd/facts_add.py written.json verdicts.json [more pairs...]

written.json: {"songs": {track: {"facts": [{t, u, src, q}, ...]}}, "artists": {artist id: {"name", "facts": [...]}}}
verdicts.json: {"<anything>:s:<track>:<i>" or "<anything>:a:<artist id>:<i>": {"v": "keep"|"fix"|"drop", "t": fixed text}}
from the independent check. Only facts with a keep or fix verdict are added. A song is added with its credited artists
(from _sotd/data/sources.json) and their facts; one already waiting is replaced. Then run facts_apply.py --offline.
"""
import datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PENDING = os.path.join(HERE, 'data', 'facts-pending.json')
SOURCES = os.path.join(HERE, 'data', 'sources.json')


def checked(table, kind, verdicts):
    out = {}
    for key, e in table.items():
        kept = []
        for i, f in enumerate(e.get('facts', [])):
            v = next((v for k, v in verdicts.items() if k.endswith(':%s:%s:%d' % (kind, key, i))), None)
            if not v or v.get('v') not in ('keep', 'fix'):
                continue
            t = (v.get('t') if v['v'] == 'fix' and v.get('t') else f['t']).strip()
            kept.append({'t': t, 'u': f.get('u', ''), 'src': f.get('src', ''), 'q': f.get('q', '')})
        out[key] = {'name': e.get('name', ''), 'f': kept}
    return out


def main(args):
    S = json.load(open(SOURCES, encoding='utf-8'))
    pending = json.load(open(PENDING, encoding='utf-8')) if os.path.exists(PENDING) else {'songs': {}}
    today = datetime.date.today().isoformat()
    added = []
    for wpath, vpath in zip(args[0::2], args[1::2]):
        w, v = json.load(open(wpath, encoding='utf-8')), json.load(open(vpath, encoding='utf-8'))
        songs, artists = checked(w.get('songs', {}), 's', v), checked(w.get('artists', {}), 'a', v)
        for tid, e in songs.items():
            s = S['songs'].get(tid)
            if not s:
                print('not in sources.json, skipped:', tid)
                continue
            a = {k: {'name': (artists.get(k) or {}).get('name') or S['artists'].get(k, {}).get('name', ''),
                     'f': (artists.get(k) or {}).get('f', [])} for k in s['artists']}
            pending['songs'][tid] = {'n': s['n'], 'title': s['title'], 'artist': s['artist'], 'written': today, 's': e['f'], 'a': a}
            added.append('%d %s: %d song facts, %s' % (s['n'], s['title'], len(e['f']), ', '.join('%s %d' % (x['name'], len(x['f'])) for x in a.values())))
    with open(PENDING, 'w', encoding='utf-8') as f:
        json.dump(pending, f, ensure_ascii=False, indent=1)
    print('\n'.join(added))
    print(len(added), 'songs added;', len(pending['songs']), 'waiting')


if __name__ == '__main__':
    if len(sys.argv) < 3 or len(sys.argv) % 2 == 0:
        sys.exit(__doc__)
    main(sys.argv[1:])
