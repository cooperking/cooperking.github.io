#!/usr/bin/env python3
"""Adds written and checked song facts to the list waiting for John's sign-off (_sotd/data/facts-pending.json).

    python3 _sotd/facts_add.py written.json verdicts.json [more pairs...]

written.json: {"songs": {track: {"facts": [{t, u, src, q}, ...]}}, "artists": {artist id: {"name", "facts": [...]}}}
verdicts.json: {"<anything>:s:<track>:<i>" or "<anything>:a:<artist id>:<i>": {"v": "keep"|"fix"|"drop", "t": fixed text}}
from the independent check. Only facts with a keep or fix verdict are added. A song is added with its credited artists
(from _sotd/data/sources.json) and their facts; one already waiting is replaced. A song from a member's public playlist
(upl in sources.json) goes into _sotd/data/upl-facts.json instead, for its owner to check (the site job sends it to
the Worker). Then run facts_apply.py --offline.
"""
import datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PENDING = os.path.join(HERE, 'data', 'facts-pending.json')
SOURCES = os.path.join(HERE, 'data', 'sources.json')
THEIRS = os.path.join(HERE, 'data', 'upl-facts.json')


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
    theirs = json.load(open(THEIRS, encoding='utf-8')) if os.path.exists(THEIRS) else {'songs': {}}
    today = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')   # (when they came in, to the second: John, 6 Nov 2026, "put a time they came in on so I can tell")
    added = []
    rp = os.path.join(HERE, 'data', 'facts-redo.json')
    REDO_NOW = json.load(open(rp, encoding='utf-8')) if os.path.exists(rp) else {}
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
            if s.get('upl'):
                theirs['songs'][tid] = {'upl': s['upl'], 'title': s['title'], 'artist': s['artist'], 'written': today, 's': e['f'], 'a': a}
            else:
                kp = (REDO_NOW.get(tid) or {}) if isinstance(REDO_NOW.get(tid), dict) and REDO_NOW[tid].get('mode') == 'song' else {}
                if kp.get('keep'):   # (more about the song: the new song facts after the ones it had; its artist facts as they were, any new ones added)
                    k0 = kp['keep']
                    a = {k: {'name': (k0.get('a') or {}).get(k, {}).get('name') or v['name'], 'f': ((k0.get('a') or {}).get(k, {}).get('f') or []) + v['f']} for k, v in a.items()}
                    e = dict(e, f=(k0.get('s') or []) + e['f'])
                pending['songs'][tid] = {'n': s['n'], 'title': s['title'], 'artist': s['artist'], 'written': today, 's': e['f'], 'a': a}
                nf = (w.get('songs', {}).get(tid) or {}).get('nf')   # (the punchy one for its notification, checked like the rest)
                vn = next((x for k, x in v.items() if k.endswith(':nf:%s:0' % tid)), None)
                if isinstance(nf, dict) and nf.get('t') and vn and vn.get('v') in ('keep', 'fix'):
                    t = (vn.get('t') if vn['v'] == 'fix' and vn.get('t') else nf['t']).strip()
                    if len(t) <= 110:
                        pending['songs'][tid]['nf'] = {'t': t, 'u': nf.get('u', ''), 'src': nf.get('src', ''), 'q': nf.get('q', '')}
            added.append('%d %s: %d song facts, %s' % (s['n'], s['title'], len(e['f']), ', '.join('%s %d' % (x['name'], len(x['f'])) for x in a.values()))
                         + (' (a member’s: waiting for its owner)' if s.get('upl') else ''))
    with open(PENDING, 'w', encoding='utf-8') as f:
        json.dump(pending, f, ensure_ascii=False, indent=1)
    redo_p = os.path.join(HERE, 'data', 'facts-redo.json')   # (asked for again and now written: done with)
    if os.path.exists(redo_p):
        redo = json.load(open(redo_p, encoding='utf-8'))
        left = {k: v for k, v in redo.items() if k not in pending['songs']}
        if left != redo:
            with open(redo_p, 'w', encoding='utf-8') as f:
                json.dump(left, f, indent=1)
    if theirs['songs']:
        with open(THEIRS, 'w', encoding='utf-8') as f:
            json.dump(theirs, f, ensure_ascii=False, indent=1)
    print('\n'.join(added))
    print(len(added), 'songs added;', len(pending['songs']), 'waiting')


if __name__ == '__main__':
    if len(sys.argv) < 3 or len(sys.argv) % 2 == 0:
        sys.exit(__doc__)
    main(sys.argv[1:])
