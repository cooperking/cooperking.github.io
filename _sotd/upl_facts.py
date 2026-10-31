#!/usr/bin/env python3
"""Facts for the songs on members' public playlists, for the site job (see _sotd/FACTS.md and MEMBERS.md).

    python3 _sotd/upl_facts.py send                  _sotd/data/upl-facts.json (written by John's facts task) to the
                                                     Worker (/upl/facts, with this job's GITHUB_TOKEN): each new one
                                                     waits there for its owner
    python3 _sotd/upl_facts.py tidy PLAYLISTS.json   a playlist no longer public (or gone): its songs out of
                                                     _sotd/data/sources.json and _sotd/data/upl-facts.json (PLAYLISTS.json:
                                                     upl_sync.py's upl-playlists.json; without it, nothing changes)
"""
import json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get('SOTD_UPL_FACTS_URL', 'https://sotd-playlists.cooperking.workers.dev/upl/facts')
THEIRS = os.path.join(HERE, 'data', 'upl-facts.json')
SOURCES = os.path.join(HERE, 'data', 'sources.json')
UA = 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)'


def load(p, empty):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return empty


def note(text):   # a line in the log, and in the run's notes on GitHub (an annotation) when it runs there
    print(('::notice::' if os.environ.get('GITHUB_ACTIONS') else '') + text)


def send():
    theirs = load(THEIRS, {'songs': {}})
    tok = os.environ.get('GITHUB_TOKEN', '').strip()
    if not theirs['songs'] or not tok:
        print('members’ facts: none to send' if tok else 'members’ facts: no GITHUB_TOKEN')
        return
    req = urllib.request.Request(URL, data=json.dumps(theirs, ensure_ascii=False).encode(), method='POST',
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json', 'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            note('members’ facts: %d sent, %s new for their owners' % (len(theirs['songs']), json.loads(r.read()).get('added')))
    except Exception as e:
        note('members’ facts: the Worker didn’t take them (' + str(e) + '); the next run sends them again')


def tidy(path):
    plays = load(path, None)
    if plays is None:
        print('members’ facts: no playlists this run, so nothing tidied')
        return
    public = {pid for pid, pl in plays.items() if pl.get('share') == 'public'}
    theirs = load(THEIRS, {'songs': {}})
    gone = [t for t, e in theirs['songs'].items() if e.get('upl') not in public]
    if gone:
        for t in gone:
            del theirs['songs'][t]
        with open(THEIRS, 'w', encoding='utf-8') as f:
            json.dump(theirs, f, ensure_ascii=False, indent=1)
    S = load(SOURCES, None)
    out = [t for t, e in ((S or {}).get('songs') or {}).items() if e.get('upl') and e['upl'] not in public]
    if out:
        for t in out:
            del S['songs'][t]
        with open(SOURCES, 'w', encoding='utf-8') as f:
            json.dump(S, f, ensure_ascii=False, indent=0, separators=(',', ':'))
    (note if gone or out else print)(f'members’ facts: {len(gone)} written and {len(out)} sources taken out (their playlists not public)')


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == 'send':
        send()
    elif len(sys.argv) == 3 and sys.argv[1] == 'tidy':
        tidy(sys.argv[2])
    else:
        sys.exit(__doc__)
