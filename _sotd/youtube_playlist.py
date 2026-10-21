#!/usr/bin/env python3
"""Keeps a YouTube playlist in step with the Spotify playlist: every song with a YouTube match (data/youtube.json,
or John's own pick in data/john-videos.json "videos"), in the order of the days.

It acts for the YouTube channel John picked when he signed in once through Google's OAuth Playground; the
sign-in lives in three GitHub secrets (YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN). Without them it does nothing.
A new sign-in for a different channel starts a new playlist there; the old one is left for John to delete.
On a channel named for Song of the Day the playlist is public; on any other (John's own, for testing) it's unlisted.

Each added song costs 50 of YouTube's daily units, shared with the searches (see ytapi.py), so a long backlog
fills over a few days. What's been added is kept in data/youtube-playlist.json.

Each run first reads what is actually in the playlist (1 unit per 50 songs), so songs added by hand on YouTube count as
added, and anything that isn't one of the songs' videos (a video since replaced, or a song in twice) is taken out."""
import json, os, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ytapi   # noqa: E402
from ytapi import API, Quota   # noqa: E402

OUT = os.path.join(HERE, 'data', 'youtube-playlist.json')
MATCH = os.path.join(HERE, 'data', 'youtube.json')
JOHN = os.path.join(HERE, 'data', 'john-videos.json')
SONGS_URL = os.environ.get('SOTD_SONGS_URL', 'https://sotd-playlists.cooperking.workers.dev/songs.json')
TITLE = "John's Song of the Day"
ABOUT = ("A song for every day since 10 February 2026, in the same order as the Spotify playlist. "
         "The calendar, with a preview of each song: https://cooperking.github.io/songs/")


def call(method, url, body=None, token=None, units=1):
    ytapi.spend(units)
    try:
        return ytapi.call(method, url, body, token=token)
    except Quota:
        ytapi.full()
        raise


def main():
    token = ytapi.token()
    if not token:
        print('YouTube playlist: not set up yet (no YT_* secrets)')
        return

    state = {'playlist': None, 'added': {}}
    if os.path.exists(OUT):
        with open(OUT) as f:
            state = json.load(f)
    match = {}
    if os.path.exists(MATCH):
        with open(MATCH) as f:
            match = {t: e['id'] for t, e in json.load(f)['songs'].items() if e.get('id')}
    if os.path.exists(JOHN):
        with open(JOHN) as f:
            for t, v in ((json.load(f) or {}).get('videos') or {}).items():
                if v:
                    match[t] = v
                else:
                    match.pop(t, None)
    with urllib.request.urlopen(urllib.request.Request(SONGS_URL, headers={'User-Agent': 'SongOfTheDayCalendar/1.0'}), timeout=30) as r:
        songs = json.loads(r.read())['songs']

    changed = False
    try:
        me = (call('GET', API + 'channels?part=snippet&mine=true', token=token).get('items') or [{}])[0]
        if not me.get('id'):
            sys.exit('could not see which YouTube channel the sign-in is for')
        name = me['snippet'].get('title', '')
        print('signed in as the YouTube channel', name)
        if state.get('channel') and state['channel'] != me['id']:
            print('a different channel from before (' + state.get('channelName', '') + '): starting a new playlist here;',
                  'the old one stays at', state.get('url'))
            state = {'playlist': None, 'added': {}, 'before': state.get('url')}
            changed = True
        if state.get('channel') != me['id'] or state.get('channelName') != name:
            state['channel'], state['channelName'] = me['id'], name
            changed = True
        if state.get('playlist'):   # still there? (it may have been deleted on YouTube)
            j = call('GET', API + 'playlists?' + urllib.parse.urlencode({'part': 'id', 'id': state['playlist']}), token=token)
            if not j.get('items'):
                print('the playlist has gone; making a new one')
                state.update({'playlist': None, 'added': {}})
                changed = True
        if not state.get('playlist'):
            privacy = 'public' if 'song of the day' in name.lower() else 'unlisted'
            j = call('POST', API + 'playlists?part=snippet,status', {
                'snippet': {'title': TITLE, 'description': ABOUT}, 'status': {'privacyStatus': privacy}}, token=token, units=50)
            if not j.get('id'):
                sys.exit('could not make the playlist: ' + str(j))
            state['playlist'] = j['id']
            changed = True
            print('made the playlist (' + privacy + ')', 'https://www.youtube.com/playlist?list=' + j['id'])

        # what's really in the playlist now
        items, page = [], None
        while True:
            q = {'part': 'snippet', 'playlistId': state['playlist'], 'maxResults': 50}
            if page:
                q['pageToken'] = page
            j = call('GET', API + 'playlistItems?' + urllib.parse.urlencode(q), token=token)
            if '_status' in j:
                sys.exit('could not read the playlist: ' + str(j.get('_status')) + ' ' + j.get('_text', '')[:160])
            items += [(it['id'], it['snippet']['resourceId'].get('videoId')) for it in j.get('items', [])]
            page = j.get('nextPageToken')
            if not page:
                break
        spare = {}
        for item, vid in items:
            spare.setdefault(vid, []).append(item)
        added = {}
        for s in songs:
            vid = match.get(s['id'])
            if vid and spare.get(vid):
                added[s['id']] = {'v': vid, 'item': spare[vid].pop(0)}
        if added != state['added']:
            state['added'] = added
            changed = True
        extra = [i for left in spare.values() for i in left]
        print(len(items), 'in the playlist;', len(added), 'are songs;', len(extra), 'to take out')
        for item in extra:   # replaced videos and repeats
            r = call('DELETE', API + 'playlistItems?' + urllib.parse.urlencode({'id': item}), token=token, units=50)
            print('took out', item, r.get('_status', 'ok'))

        adds = 0
        for pos, s in enumerate(songs):
            vid = match.get(s['id'])
            have = state['added'].get(s['id'])
            if not vid or (have and have['v'] == vid):
                continue
            if have:   # a different video picked since: take the old one out
                call('DELETE', API + 'playlistItems?' + urllib.parse.urlencode({'id': have['item']}), token=token, units=50)
                state['added'].pop(s['id'])
                changed = True
            # in day order: just after the songs before it that are already in
            at = sum(1 for x in songs[:pos] if x['id'] in state['added'])
            j = call('POST', API + 'playlistItems?part=snippet', {'snippet': {
                'playlistId': state['playlist'], 'position': at,
                'resourceId': {'kind': 'youtube#video', 'videoId': vid}}}, token=token, units=50)
            adds += 1
            if j.get('id'):
                state['added'][s['id']] = {'v': vid, 'item': j['id']}
                changed = True
                print('added', s['n'], s['title'], '—', s['artist'])
            else:
                print('could not add', s['n'], s['title'], vid, j.get('_status'), j.get('_text', '')[:160])
    except Quota:
        print("today's YouTube allowance is used up; the rest go in tomorrow")
    if changed:
        state['url'] = 'https://www.youtube.com/playlist?list=' + state['playlist']
        with open(OUT, 'w') as f:
            json.dump(state, f, ensure_ascii=False, indent=0, separators=(',', ':'))
    print(len(state['added']), 'songs in the playlist:', state.get('url', ''))


if __name__ == '__main__':
    main()
