#!/usr/bin/env python3
"""Finds a YouTube video for each song not in _sotd/data/youtube.json yet, and writes songs/videos.json for the calendar.

song.link (Odesli) matches the Spotify track to its YouTube upload. YouTube's oEmbed then gives the upload's title and
channel, and answers 401 when the owner doesn't let other sites play it. Nothing is downloaded: the calendar plays
the video in YouTube's own player.

song.link allows 10 requests a minute without a key, so a full first run takes about half an hour.
Songs with no match are tried again after 14 days. John's own picks (data/john.json "videos") win over the match."""
import datetime, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, 'data', 'youtube.json')
JOHN = os.path.join(HERE, 'data', 'john.json')
SITE = os.path.join(ROOT, 'songs', 'videos.json')
SONGS_URL = os.environ.get('SOTD_SONGS_URL', 'https://sotd-playlists.cooperking.workers.dev/songs.json')
UA = 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)'
RETRY_DAYS = 14
TIME_LIMIT = 45 * 60   # stop and save after this; the next run carries on
START = time.time()


def fetch(url, tries=4):
    """JSON from url; None for 400/401/403/404 (no such thing, or not allowed); waits out 429."""
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print('  rate limited, waiting a minute'); time.sleep(61); continue
            if e.code in (400, 401, 403, 404):
                return {'_status': e.code}
            print('  retry', e.code); time.sleep(5 * (i + 1))
        except Exception as e:
            print('  retry', e); time.sleep(5 * (i + 1))
    return None


def kind(title, channel):
    t = (title or '').lower()
    if (channel or '').endswith(' - Topic'):
        return 'audio'   # YouTube's own upload of the recording, over the album cover
    if re.search(r'official\s+(music\s+)?video|music\s+video|\(video\)|\[video\]|official\s+film', t):
        return 'video'
    if re.search(r'official\s+audio|\(audio\)|\[audio\]|visuali[sz]er|lyric', t):
        return 'audio'
    if re.search(r'\blive\b|session|performance|kexp|tiny desk', t):
        return 'live'
    return 'other'


def lookup(s):
    e = {'n': s['n'], 'checked': datetime.date.today().isoformat(), 'from': 'song.link'}
    time.sleep(6.5)
    j = fetch('https://api.song.link/v1-alpha.1/links?' + urllib.parse.urlencode(
        {'url': 'https://open.spotify.com/track/' + s['id'], 'userCountry': 'GB', 'songIfSingle': 'true'}))
    if not j or '_status' in j:
        e['error'] = (j or {}).get('_status', 'no answer')
        return e
    yt = (j.get('linksByPlatform') or {}).get('youtube') or (j.get('linksByPlatform') or {}).get('youtubeMusic')
    if not yt:
        e['id'] = None
        return e
    uid = yt.get('entityUniqueId', '')
    vid = uid.split('::', 1)[1] if '::' in uid else (urllib.parse.parse_qs(urllib.parse.urlparse(yt['url']).query).get('v') or [None])[0]
    e['id'] = vid
    ent = (j.get('entitiesByUniqueId') or {}).get(uid) or {}
    e['matched'] = [ent.get('title'), ent.get('artistName')]
    time.sleep(0.5)
    o = fetch('https://www.youtube.com/oembed?' + urllib.parse.urlencode({'url': 'https://www.youtube.com/watch?v=' + vid, 'format': 'json'}))
    if not o:
        e['embed'] = None
    elif '_status' in o:
        e['embed'] = False
        e['status'] = o['_status']   # 401: the owner doesn't allow it on other sites; 404: gone or private
    else:
        e['embed'] = True
        e['title'], e['channel'] = o.get('title'), o.get('author_name')
    e['kind'] = kind(e.get('title') or ent.get('title'), e.get('channel') or ent.get('artistName'))
    return e


def due(e):
    if e.get('id') and e.get('embed'):
        return False
    if e.get('id') and e.get('embed') is None:
        return True   # oEmbed didn't answer last time
    try:   # no match, or not playable on other sites: try again now and then
        return (datetime.date.today() - datetime.date.fromisoformat(e.get('checked', '2000-01-01'))).days >= RETRY_DAYS
    except ValueError:
        return True


def main():
    data = {'about': 'YouTube match for each song (key: Spotify track id), from song.link, checked with YouTube oEmbed.',
            'songs': {}}
    if os.path.exists(OUT):
        with open(OUT) as f:
            data = json.load(f)
    feed = fetch(SONGS_URL)
    if not feed or 'songs' not in feed:
        sys.exit('could not read the song list')
    songs = feed['songs']
    todo = [s for s in songs if s['id'] not in data['songs'] or due(data['songs'][s['id']])]
    print(f'{len(todo)} songs to match on YouTube')
    done = 0
    for s in todo:
        if time.time() - START > TIME_LIMIT:
            print('time limit: the rest wait for the next run'); break
        e = lookup(s)
        data['songs'][s['id']] = e
        done += 1
        print(s['n'], s['title'], '—', s['artist'], '→', e.get('kind', ''), e.get('id'), e.get('channel', ''), e.get('title', ''),
              '' if e.get('embed', True) else f"(not playable here: {e.get('status', e.get('error'))})")
    if done:
        with open(OUT, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=0, separators=(',', ':'))

    john = {}
    if os.path.exists(JOHN):
        with open(JOHN) as f:
            john = (json.load(f) or {}).get('videos') or {}
    out = {}
    for tid, e in data['songs'].items():
        if e.get('id') and e.get('embed'):
            out[tid] = {'v': e['id'], 'k': e.get('kind', 'other')}
    for tid, v in john.items():   # John's picks: a video id, or "" for none
        if v:
            out[tid] = {'v': v, 'k': 'video', 'j': 1}
        else:
            out.pop(tid, None)
    counts = {}
    for x in out.values():
        counts[x['k']] = counts.get(x['k'], 0) + 1
    print('playable on the calendar:', len(out), counts)
    new = json.dumps({'about': 'YouTube video for each song (Spotify track id → v: video id, k: video / live / audio / other)',
                      'videos': out}, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    old = open(SITE).read() if os.path.exists(SITE) else ''
    if new != old:
        with open(SITE, 'w') as f:
            f.write(new)


if __name__ == '__main__':
    main()
