#!/usr/bin/env python3
"""Folds John's changes from the admin page (Download or Copy) into _sotd/data/john.json.

    python3 _sotd/apply_edits.py song-of-the-day-changes-2026-10-20.json [more files...]

Each change sets that song's moods (one or two of the ten) and genres (up to two); later files win.
Pushing john.json to main reruns the GitHub Action, which rebuilds songs/moods.json with these on top.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from genres import GENRES      # noqa: E402
from make_tags import MOODS    # noqa: E402

STORE = os.path.join(HERE, 'data', 'john.json')


def main(paths):
    store = json.load(open(STORE, encoding='utf-8')) if os.path.exists(STORE) else {}
    done = 0
    for p in paths:
        for tid, e in json.load(open(p, encoding='utf-8')).get('edits', {}).items():
            moods = [m for m in (e.get('moods') or []) if m in MOODS][:2]
            genres = [g for g in (e.get('genres') or []) if g in GENRES][:2]
            if not moods:
                print('skipped (no mood):', e.get('title'))
                continue
            store[tid] = {'n': e.get('n'), 'title': e.get('title'), 'artist': e.get('artist'), 'moods': moods, 'genres': genres}
            done += 1
    with open(STORE, 'w', encoding='utf-8') as f:
        json.dump(store, f, ensure_ascii=False, indent=1)
    print(done, 'changes applied;', len(store), 'songs set by John in total')


if __name__ == '__main__':
    main(sys.argv[1:])
