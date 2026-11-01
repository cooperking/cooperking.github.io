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
    if not re.fullmatch(r'[0-9A-Za-z]{22}', tid or ''):
        sys.exit('not a track id: %r' % tid)
    redo = load(REDO)
    redo[tid] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    with open(REDO, 'w', encoding='utf-8') as f:
        json.dump(redo, f, indent=1)
    asked = load(ASKED)
    if asked.pop(tid, None) is not None:
        with open(ASKED, 'w', encoding='utf-8') as f:
            json.dump(asked, f, indent=1)
    print('facts to be written again:', tid)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '')
