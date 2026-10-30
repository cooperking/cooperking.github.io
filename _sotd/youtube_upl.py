#!/usr/bin/env python3
"""A YouTube playlist for each member's Spotify playlist on the calendar, on the same channel as John's, unlisted,
titled as the calendar calls it ("Mum's Song of the Day", or a plain playlist's own name).

    python3 _sotd/youtube_upl.py PLAYLISTS.json

PLAYLISTS.json is upl_sync.py's upl-playlists.json: each playlist's songs in order and the ones taken in so far (its 10
newest first, then 5 more a day, and each new song straight away). Each song taken in that has a video (the calendar's,
songs/videos.json) goes in, in the playlist's order, 50 units each. John's playlist goes first (its step runs before
this), and this stops with RESERVE units of the day left, so what doesn't fit waits for the next run. What's in each is
kept in _sotd/data/upl.json (yt: the YouTube playlist, added: each song's video and playlist item), and the YouTube
playlists are sent to the Worker (/upl/sync, with this job's GITHUB_TOKEN) for the calendar's On YouTube.
"""
import datetime, json, os, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ytapi   # noqa: E402
from ytapi import API, Quota   # noqa: E402

STATE = os.path.join(HERE, 'data', 'upl.json')
SITE = os.path.join(os.path.dirname(HERE), 'songs', 'videos.json')
JOHN = os.path.join(HERE, 'data', 'john-videos.json')
SYNC_URL = os.environ.get('SOTD_UPL_URL', 'https://sotd-playlists.cooperking.workers.dev/upl/sync')
RESERVE = 1000
ABOUT = 'From a member’s Spotify playlist on the Song of the Day calendar, in the same order: https://cooperking.github.io/songs/'


def call(method, url, body=None, token=None, units=1):
    ytapi.spend(units, cap=ytapi.DAY_LIMIT - RESERVE if units > 1 else ytapi.READ_LIMIT)   # (over that: Quota, the day left as it is for John's)
    try:
        return ytapi.call(method, url, body, token=token)
    except Quota:
        ytapi.full()
        raise


def load(path, empty):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return empty


def videos():
    match = {t: v['v'] for t, v in (load(SITE, {}).get('videos') or {}).items()}
    for t, v in ((load(JOHN, {}) or {}).get('videos') or {}).items():
        if v:
            match[t] = v
        else:
            match.pop(t, None)
    return match


def tell_worker(made):
    tok = os.environ.get('GITHUB_TOKEN', '').strip()
    if not tok or not made:
        return
    req = urllib.request.Request(SYNC_URL, data=json.dumps({'yt': made}).encode(), method='POST',
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json', 'User-Agent': ytapi.UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print('the Worker keeps', json.loads(r.read()).get('kept'), 'YouTube copies')
    except Exception as e:
        print('could not tell the Worker the YouTube copies:', e)


def main(path):
    plays = load(path, {})
    if not plays:
        print('members’ playlists: none to copy to YouTube')
        return
    token = ytapi.token()
    if not token:
        print('members’ YouTube copies: not set up (no YT_* secrets)')
        return
    state = load(STATE, {'playlists': {}})
    match = videos()
    made = {}
    try:
        for pid, pl in plays.items():
            rec = state['playlists'].setdefault(pid, {})
            if rec.get('yt'):   # still there? (it may have been deleted on YouTube)
                j = call('GET', API + 'playlists?' + urllib.parse.urlencode({'part': 'id', 'id': rec['yt']}), token=token)
                if '_status' not in j and not j.get('items'):
                    print(pl['title'] + ': its YouTube copy has gone; making a new one')
                    rec.pop('yt'); rec['added'] = {}
            if not rec.get('yt'):
                j = call('POST', API + 'playlists?part=snippet,status', {'snippet': {'title': pl['title'][:150], 'description': ABOUT},
                         'status': {'privacyStatus': 'unlisted'}}, token=token, units=50)
                if not j.get('id'):
                    print(pl['title'] + ': could not make its YouTube copy:', j.get('_status'), j.get('_text', '')[:160])
                    continue
                rec['yt'], rec['added'] = j['id'], {}
                print(pl['title'] + ': made its YouTube copy (unlisted) https://www.youtube.com/playlist?list=' + j['id'])
            made[pid] = rec['yt']
            added = rec.setdefault('added', {})
            wanted = set(pl.get('in') or [])
            order = [x['id'] for x in pl['songs']]
            adds = 0
            for pos, t in enumerate(order):
                vid = match.get(t)
                if t not in wanted or not vid or (added.get(t) or {}).get('v') == vid:
                    continue
                at = sum(1 for x in order[:pos] if x in added)   # in the playlist's order: just after the songs before it already in
                j = call('POST', API + 'playlistItems?part=snippet', {'snippet': {'playlistId': rec['yt'], 'position': at,
                         'resourceId': {'kind': 'youtube#video', 'videoId': vid}}}, token=token, units=50)
                if j.get('id'):
                    added[t] = {'v': vid, 'item': j['id'], 'at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}
                    adds += 1
                else:
                    print(pl['title'] + ': could not add', t, vid, j.get('_status'), j.get('_text', '')[:160])
            waiting = sum(1 for t in order if t in wanted and match.get(t) and t not in added)
            print(pl['title'] + ':', adds, 'added this run,', len(added), 'in its YouTube copy' + (', ' + str(waiting) + ' waiting' if waiting else ''))
    except Quota:
        print('the day’s YouTube allowance for members’ copies is used up (', RESERVE, 'units kept for John’s ); the rest wait for the next run')
    with open(STATE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    tell_worker(made)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
