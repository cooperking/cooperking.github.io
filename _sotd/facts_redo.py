#!/usr/bin/env python3
"""A song of John's already out, its facts written again (John, 1 Nov 2026: published before its facts came, it had none).

    python3 _sotd/facts_redo.py <track id>

Run by song-tags.yml when the Worker starts it with facts_redo (the Songs tab's Write facts again): puts the song in
_sotd/data/facts-redo.json, which facts_todo.py and facts_ask.py treat as needing facts though it's out, and forgets
when it was last asked for, so the job's last step starts the facts task for it now. facts_add.py takes it out of
facts-redo.json once its facts are written (they then wait on the Songs tab, as a new song's do).
"""
import datetime, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REDO = os.path.join(HERE, 'data', 'facts-redo.json')
ASKED = os.path.join(HERE, 'data', 'facts-asked.json')


def load(p):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main(tid):
    tid, _, mode = (tid or '').partition(':')   # (<track>:song: only more facts about the song, the rest kept: John, 6 Nov 2026, "more about the song would be good")
    if not re.fullmatch(r'[0-9A-Za-z]{22}', tid or ''):
        sys.exit('not a track id: %r' % tid)
    redo = load(REDO)
    # the facts it has now (waiting or live), so the new ones are none of these and not from the same pages (John, 6 Nov
    # 2026: "Most of the new facts are the same ... the source is mainly just wikipedia"); a waiting song's come off the
    # waiting list, so it's written again
    avoid, srcs, keep = [], [], None
    root = os.path.dirname(HERE)
    for path in (os.path.join(HERE, 'data', 'facts-pending.json'), os.path.join(root, 'songs', 'facts-pending.json')):
        d = load(path)
        e = (d.get('songs') or {}).pop(tid, None)
        if e is None:
            continue
        if mode in ('song', 'nf') and keep is None and path.startswith(os.path.join(HERE, 'data')):
            keep = e
        for f in (e.get('s') or []) + [f for a in (e.get('a') or {}).values() for f in (a.get('f') or [])]:
            if isinstance(f, dict) and f.get('t') and f['t'] not in avoid:
                avoid.append(f['t'])
                if f.get('u') and f['u'] not in srcs:
                    srcs.append(f['u'])
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
    live = load(os.path.join(root, 'songs', 'facts.json'))
    src = live.get('src') or []
    ls = (live.get('songs') or {}).get(tid) or {}
    for f in (ls.get('s') or []):   # (live: [text, source index])
        if isinstance(f, list) and f and f[0] not in avoid:
            avoid.append(f[0])
            if len(f) > 1 and isinstance(f[1], int) and f[1] < len(src) and isinstance(src[f[1]], dict) and src[f[1]].get('u') and src[f[1]]['u'] not in srcs:
                srcs.append(src[f[1]]['u'])
    redo[tid] = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), 'avoid': avoid[:20], 'avoidSrc': srcs[:20]}
    if mode in ('song', 'nf') and keep:
        redo[tid].update({'mode': mode, 'keep': keep})   # (nf: only its notification fact, the facts kept as they are)   # (its facts as they are, the new song facts added to them: facts_add.py)
    with open(REDO, 'w', encoding='utf-8') as f:
        json.dump(redo, f, indent=1)
    asked = load(ASKED)
    if asked.pop(tid, None) is not None:
        with open(ASKED, 'w', encoding='utf-8') as f:
            json.dump(asked, f, indent=1)
    print('facts to be written again:', tid)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '')
