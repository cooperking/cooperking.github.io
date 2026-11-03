#!/usr/bin/env python3
"""Members' songs' videos (and years) to the Worker as soon as the YouTube and years steps have found them (John, 1 Nov 2026: "The YouTube
playlist can be made and added to csv as soon as discovered, I think it's waiting for mood and years"). The rest of
what the job finds for them (moods, years, the listening) still goes at the end (upl_private.py save), with these again.

    python3 _sotd/upl_vids.py

Reads $RUNNER_TEMP/songs-all.json (upl_sync.py's list: members' songs have upl), songs/videos.json (each song's video,
as youtube.py wrote it) and _sotd/data/youtube.json (the songs looked at, found or not), and POSTs the members' songs'
{ vids, looked } to the Worker's /upl/vids with this job's GITHUB_TOKEN. Then each playlist's /yt/ list, the Playlist
push sheet and the calendars have them. Without the list, the token or an answer it does nothing (the end of the run
sends them anyway).
"""
import json, os, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
URL = os.environ.get('SOTD_UPL_VIDS_URL', 'https://sotd-playlists.cooperking.workers.dev/upl/vids')
ALL = os.path.join(os.environ.get('RUNNER_TEMP') or '/tmp', 'songs-all.json')
UA = 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)'


def load(path, key):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f).get(key) or {}
    except (OSError, ValueError):
        return {}


def main():
    tok = os.environ.get('GITHUB_TOKEN', '').strip()
    songs = load(ALL, 'songs')
    if not tok or not songs:
        print('members’ videos: not sent now (no token or song list); the end of the run sends them')
        return
    theirs = {s['id'] for s in songs if s.get('upl')} - {s['id'] for s in songs if not s.get('upl')}
    videos = load(os.path.join(ROOT, 'songs', 'videos.json'), 'videos')
    looked = load(os.path.join(HERE, 'data', 'youtube.json'), 'songs')
    years = load(os.path.join(ROOT, 'songs', 'years.json'), 'years')
    ylooked = load(os.path.join(HERE, 'data', 'years.json'), 'songs')
    # looked at for good: a video, or YouTube's search found none. One only the free ways missed still waits for the
    # search, so it isn't "none" yet (John, 3 Nov 2026: "165 with none for videos")
    done = lambda e: isinstance(e, dict) and (e.get('id') or (e.get('from') == 'YouTube search' and not e.get('error')))
    body = {'vids': {t: v for t, v in videos.items() if t in theirs and isinstance(v, dict) and v.get('v')},
            'looked': sorted(t for t in looked if t in theirs and done(looked[t])),
            'unlooked': sorted(t for t in looked if t in theirs and not done(looked[t]) and t not in videos),
            'years': {t: y for t, y in years.items() if t in theirs and y},
            'ylooked': sorted(t for t in set(ylooked) | set(years) if t in theirs)}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method='POST',
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json', 'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read())
        print(f"members’ videos: {len(body['vids'])} sent now ({j.get('vids')} new), {len(body['looked'])} looked at; years: {len(body['years'])} ({j.get('years')} new)")
    except Exception as e:
        print('members’ videos: the Worker didn’t take them now (' + str(e) + '); the end of the run sends them')


if __name__ == '__main__':
    main()
