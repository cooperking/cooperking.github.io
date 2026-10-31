#!/usr/bin/env python3
"""Members' own Spotify playlists, for the site job: which of their songs this run takes in for their YouTube copies,
and the song list that the steps finding videos, moods and years work through (John's songs, then theirs).

    python3 _sotd/upl_sync.py OUT.json

Reads the playlists the calendar can read from the Worker (/upl/sync, with this job's GITHUB_TOKEN). A playlist's songs
come in a few at a time: its 10 newest when it's first seen, then 5 more a day (newest first), and each song added to it
after it was added to the calendar straight away; youtube_upl.py adds those to its YouTube copy. Videos, moods and years
are found for all its songs: those in, and the rest LOOK a run (newest first). What's in and what's looked at are kept in
_sotd/data/upl.json (for each playlist, in and look: each song's track id and the day; youtube_upl.py keeps its YouTube
copy there too).

Writes OUT.json: John's songs.json, then the songs in or looked at from members' playlists that aren't his (numbered
from 100001, with upl: the playlist's id), and beside it upl-playlists.json (each playlist's title, kind, songs in order
and which are in) for youtube_upl.py, and songs-facts.json: John's songs and the songs on members' public playlists
that want facts (facts: new since it was added, or asked for by its owner), for about.py's sources and so John's facts
task. Without the token, or an answer from the Worker, OUT.json is John's songs alone (and neither of the others).
"""
import datetime, json, os, sys, urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'data', 'upl.json')
SONGS_URL = os.environ.get('SOTD_JOHN_SONGS_URL', 'https://sotd-playlists.cooperking.workers.dev/songs.json')
SYNC_URL = os.environ.get('SOTD_UPL_URL', 'https://sotd-playlists.cooperking.workers.dev/upl/sync')
FIRST, DAILY, LOOK, BASE_N = 10, 5, 40, 100000
UA = {'User-Agent': 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)'}


def get(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=dict(UA, **(headers or {}))), timeout=60) as r:
        return json.loads(r.read())


def load(path, empty):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return empty


def added_ms(iso):
    return int(datetime.datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp() * 1000)


def take_in(rec, pl, today):
    """The playlist's songs to have in now: kept in rec['in'] (track id: the day it came in)."""
    songs, base = pl['songs'], pl.get('base') or 0
    first = 'in' not in rec
    rec.setdefault('in', {})
    rec.setdefault('days', {})
    came = []
    for x in songs:   # added after it was added to the calendar: in straight away
        if x['id'] not in rec['in'] and added_ms(x['a']) > base:
            rec['in'][x['id']] = today
            came.append(x['id'])
    older = [x for x in reversed(songs) if added_ms(x['a']) <= base and x['id'] not in rec['in']]   # newest first
    room = FIRST if first else max(0, DAILY - rec['days'].get(today, 0))
    for x in older[:room]:
        rec['in'][x['id']] = today
        came.append(x['id'])
    if first:
        rec['days'][today] = DAILY   # (the first 10 are that day's)
    elif older[:room]:
        rec['days'][today] = rec['days'].get(today, 0) + len(older[:room])
    rec['days'] = {d: n for d, n in rec['days'].items() if d >= (datetime.date.fromisoformat(today) - datetime.timedelta(days=7)).isoformat()}
    return came


def look_at(rec, pl, today):
    """The playlist's songs not in yet whose videos, moods and years are found from this run: LOOK more, newest first;
    kept in rec['look'] (track id: the day)."""
    rec.setdefault('look', {})
    rest = [x for x in reversed(pl['songs']) if x['id'] not in rec['in'] and x['id'] not in rec['look']][:LOOK]
    for x in rest:
        rec['look'][x['id']] = today
    return len(rest)


def main(out):
    john = get(SONGS_URL)
    state = load(STATE, {'playlists': {}})
    plays = {}
    tok = os.environ.get('GITHUB_TOKEN', '').strip()
    if tok:
        try:
            plays = get(SYNC_URL, {'Authorization': 'Bearer ' + tok}).get('playlists') or {}
        except Exception as e:   # the members' songs wait for the next run; John's go on
            print('members’ playlists: no answer from the Worker:', e)
            plays = None
    else:
        print('members’ playlists: no GITHUB_TOKEN, so none this run')
        plays = None
    today = datetime.datetime.now(ZoneInfo('Europe/London')).date().isoformat()
    songs = list(john['songs'])
    have = {s['id'] for s in songs}
    n = BASE_N
    if plays is not None:
        for pid in [p for p in state['playlists'] if p not in plays]:
            print('a member’s playlist gone from the calendar:', pid, '(its YouTube copy stays on YouTube:', state['playlists'][pid].get('yt', 'none') + ')')
            del state['playlists'][pid]
        for pid, pl in plays.items():
            rec = state['playlists'].setdefault(pid, {})
            came = take_in(rec, pl, today)
            looked = look_at(rec, pl, today)
            print(pl['title'] + ':', len(pl['songs']), 'songs,', len(rec['in']), 'in its YouTube copy so far' + (', ' + str(len(came)) + ' new this run' if came else '')
                  + (', ' + str(looked) + ' more to find videos, moods and years for' if looked else ''))
            for x in pl['songs']:
                if (x['id'] in rec['in'] or x['id'] in rec['look']) and x['id'] not in have:
                    n += 1
                    songs.append(dict(x, n=n, upl=pid))
                    have.add(x['id'])
        with open(STATE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(dict(john, songs=songs), f, ensure_ascii=False)
    if plays is not None:   # the songs on public playlists wanting facts, with John's (about.py's list)
        fs, have = list(john['songs']), {s['id'] for s in john['songs']}
        n = BASE_N
        for pid, pl in plays.items():
            want = set(pl.get('facts') or [])
            for x in pl['songs']:
                if x['id'] in want and x['id'] not in have:
                    n += 1
                    fs.append(dict(x, n=n, upl=pid))
                    have.add(x['id'])
        with open(os.path.join(os.path.dirname(os.path.abspath(out)), 'songs-facts.json'), 'w', encoding='utf-8') as f:
            json.dump(dict(john, songs=fs), f, ensure_ascii=False)
    if plays is not None:   # (none: the steps after leave members' YouTube copies and cards as they are)
        with open(os.path.join(os.path.dirname(os.path.abspath(out)), 'upl-playlists.json'), 'w', encoding='utf-8') as f:
            json.dump({pid: dict(pl, **{'in': sorted(state['playlists'][pid]['in'])}) for pid, pl in plays.items()}, f, ensure_ascii=False)
    print(len(john['songs']), 'of John’s songs,', len(songs) - len(john['songs']), 'from members’ playlists')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
