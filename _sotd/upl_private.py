#!/usr/bin/env python3
"""Members' songs out of the repository and the site: the site job works on them with John's, then keeps what it found
for them (videos, moods, years, the listening, MusicBrainz's tags, the YouTube searches, which of their songs are in)
in the Worker, not in the files it commits. The Worker gives each song's video, moods and year only with its
calendar, to those who see it.

    python3 _sotd/upl_private.py load    before upl_sync.py: last run's members' part back into the files
    python3 _sotd/upl_private.py save    before the files are committed: the members' part out of them, to the Worker

load reads the Worker's /upl/data (with this job's GITHUB_TOKEN) and puts each song's entries back into FILES and
_sotd/data/upl.json, so the steps don't do again what they've done. It keeps what it read in
$RUNNER_TEMP/upl-private-base.json; without an answer it exits 3, and the job leaves members' songs out this run
(save then does nothing). save takes out of FILES every song that's on a member's playlist and not John's (this run's,
from $RUNNER_TEMP/songs-all.json, and those kept before), with _sotd/data/upl.json, and sends them to the Worker;
if the Worker doesn't take them it exits 1, so nothing is committed that run.
"""
import json, os, sys, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
URL = os.environ.get('SOTD_UPL_DATA_URL', 'https://sotd-playlists.cooperking.workers.dev/upl/data')
TEMP = os.environ.get('RUNNER_TEMP') or '/tmp'
BASE = os.path.join(TEMP, 'upl-private-base.json')
ALL = os.path.join(TEMP, 'songs-all.json')
STATE = '_sotd/data/upl.json'
UA = 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)'
COMPACT = {'ensure_ascii': False, 'indent': 0, 'separators': (',', ':')}
FILES = [   # (file, the key its songs are under, how its writer lays it out)
    ('_sotd/data/audio.json', 'songs', COMPACT),
    ('_sotd/data/musicbrainz.json', 'songs', COMPACT),
    ('_sotd/data/years.json', 'songs', COMPACT),
    ('_sotd/data/youtube.json', 'songs', COMPACT),
    ('songs/moods.json', 'songs', {'ensure_ascii': False, 'indent': 1}),
    ('songs/videos.json', 'videos', {'ensure_ascii': False, 'separators': (',', ':'), 'sort_keys': True}),
    ('songs/years.json', 'years', {'separators': (',', ':'), 'sort_keys': True}),
]


def call(method, body=None):
    tok = os.environ.get('GITHUB_TOKEN', '').strip()
    if not tok:
        raise RuntimeError('no GITHUB_TOKEN')
    req = urllib.request.Request(URL, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json', 'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404 and method == 'GET' and b'"none"' in e.read():
            return {}   # none kept yet
        raise


def read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return json.load(f)


def write(path, data, how):   # (only when it changes, ending as it did)
    p = os.path.join(ROOT, path)
    old = open(p, encoding='utf-8').read() if os.path.exists(p) else ''
    text = json.dumps(data, **how) + ('\n' if old.endswith('\n') else '')
    if text != old:
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)


def load():
    try:
        got = call('GET')
    except Exception as e:
        print('members’ songs: no answer from the Worker (' + str(e) + '): left out this run')
        sys.exit(3)
    files, n = got.get('files') or {}, 0
    for path, key, how in FILES:
        part = (files.get(path) or {}).get(key) or {}
        if not part or not os.path.exists(os.path.join(ROOT, path)):
            continue
        data = read(path)
        into = data.setdefault(key, {})
        for t, e in part.items():
            if t not in into:
                into[t] = e
                n += 1
        write(path, data, how)
    if got.get('upl'):
        write(STATE, got['upl'], {'ensure_ascii': False, 'indent': 1, 'sort_keys': True})
    with open(BASE, 'w', encoding='utf-8') as f:
        json.dump(got, f, ensure_ascii=False)
    print(f'members’ songs: {n} entries back from the Worker' if got else 'members’ songs: none kept in the Worker yet')


def save():
    if not os.path.exists(BASE):
        print('members’ songs: not loaded this run, so nothing to keep')
        return
    with open(BASE, encoding='utf-8') as f:
        base = json.load(f)
    kept = set()
    for path, key, how in FILES:
        kept |= set(((base.get('files') or {}).get(path) or {}).get(key) or {})
    state = read(STATE) if os.path.exists(os.path.join(ROOT, STATE)) else (base.get('upl') or {'playlists': {}})
    try:
        with open(ALL, encoding='utf-8') as f:
            songs = json.load(f)['songs']
        john = {s['id'] for s in songs if not s.get('upl')}
        theirs = {s['id'] for s in songs if s.get('upl')} | kept
        for rec in (state.get('playlists') or {}).values():
            theirs |= set(rec.get('in') or {}) | set(rec.get('look') or {}) | set(rec.get('added') or {})
        theirs -= john
    except (OSError, ValueError, KeyError):   # no song list this run: just those kept before (all that load put back)
        theirs = kept
    out, moved = {'files': {}, 'upl': state}, 0
    for path, key, how in FILES:
        if not os.path.exists(os.path.join(ROOT, path)):
            continue
        data = read(path)
        part = {t: e for t, e in (data.get(key) or {}).items() if t in theirs}
        for t, e in (((base.get('files') or {}).get(path) or {}).get(key) or {}).items():   # (kept before, not in the file now)
            if t in theirs and t not in part:
                part[t] = e
        out['files'][path] = {key: part}
        if any(t in theirs for t in data.get(key) or {}):
            data[key] = {t: e for t, e in data[key].items() if t not in theirs}
            moved += 1
            write(path, data, how)
    p = os.path.join(ROOT, STATE)
    if os.path.exists(p):
        os.remove(p)
    print(f'members’ songs: {len(theirs)} songs out of {moved} files and {STATE}')
    try:
        r = call('POST', out)
        print('members’ songs: kept in the Worker,', r.get('songs'), 'with a video, moods or a year')
    except Exception as e:   # (the job stops here: nothing is committed, and the next run starts from what was kept before)
        print('members’ songs: the Worker didn’t take them (' + str(e) + ')')
        sys.exit(1)


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('load', 'save'):
        sys.exit(__doc__)
    load() if sys.argv[1] == 'load' else save()
