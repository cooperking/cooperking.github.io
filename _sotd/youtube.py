#!/usr/bin/env python3
"""Finds a YouTube video for each song not in _sotd/data/youtube.json yet, and writes songs/videos.json for the calendar.

YouTube's own search (Data API, John's sign-in; see ytapi.py) gives up to 10 uploads for "artist title". Each is
scored: the song's title in the upload's title, the artist in its title or channel, an official channel (the
artist's own, VEVO, or YouTube's auto-generated "Artist - Topic" upload of the recording), "Official Video"; and
marked down for covers, lyrics, karaoke, live, remixes and the like that the song itself isn't. The best one wins,
and one more lookup says whether its owner lets other sites play it. The runner-up uploads are kept for John.

Nothing is downloaded: the calendar plays the video in YouTube's own player.
Each search costs 100 of YouTube's 10,000 units a day, so a long backlog takes a few days; songs not done yet wait
for the next run. Songs with no good match are tried again after 30 days. John's own picks (data/john.json
"videos": {track id: video id, or "" for none}) win over the search."""
import datetime, json, os, re, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ytapi   # noqa: E402

ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, 'data', 'youtube.json')
JOHN = os.path.join(HERE, 'data', 'john.json')
SITE = os.path.join(ROOT, 'songs', 'videos.json')
SONGS_URL = os.environ.get('SOTD_SONGS_URL', 'https://sotd-playlists.cooperking.workers.dev/songs.json')
RETRY_DAYS = 30
GOOD = 55    # lowest score taken as the song


def fold(x):
    x = re.sub(r"['\u2019`]", '', x or '')   # straight and curly apostrophes alike
    x = unicodedata.normalize('NFKD', x).encode('ascii', 'ignore').decode().lower()
    x = x.replace('&', ' and ')
    return re.sub(r'[^a-z0-9]+', ' ', x).strip()


def clean_title(t):
    t = re.sub(r'\s+-\s+.*(remaster|version|mix|edit|mono|stereo|single|live|demo|take|recording|anniversary|bonus).*$', '', t, flags=re.I)
    t = re.sub(r'\s*[(\[](feat|with|ft)\.?[^)\]]*[)\]]', '', t, flags=re.I)
    t = re.sub(r'\s*\[[^\]]*\]', '', t)
    return t.strip() or t


WRONG = ['cover', 'karaoke', 'lyrics', 'lyric', 'reaction', 'slowed', 'sped up', 'reverb', 'remix', 'instrumental',
         'nightcore', '8d', 'tutorial', 'lesson', 'how to play', 'chords', 'tab', 'bass boosted', 'mashup', 'tribute',
         'full album', 'playlist', 'mix 20', 'hour', 'loop']


def kind(title, channel):
    t = (title or '').lower()
    if (channel or '').endswith(' - Topic'):
        return 'audio'   # YouTube's own upload of the recording, over the album cover
    if re.search(r'official\s+(music\s+)?video|music\s+video|\(video\)|\[video\]|official\s+film|official\s+hd\s+video', t):
        return 'video'
    if re.search(r'official\s+audio|\(audio\)|\[audio\]|visuali[sz]er|lyric', t):
        return 'audio'
    if re.search(r'\blive\b|session|performance|kexp|tiny desk', t):
        return 'live'
    return 'other'


def score(song, cand):
    title, artists = clean_title(song['title']), song.get('artists') or [song['artist']]
    ft, fa = fold(title), [fold(a) for a in artists]
    ct, cc = fold(cand['title']), fold(cand['channel'])
    cc_plain = re.sub(r' topic$', '', cc).replace(' ', '')
    s, why = 0, []
    words = [w for w in ft.split() if len(w) > 1] or ft.split()
    if ft and re.search(r'\b' + re.escape(ft) + r'\b', ct):
        s += 40; why.append('title')
    elif words and sum(w in ct.split() for w in words) / len(words) >= .75:
        s += 25; why.append('most of the title')
    else:
        s -= 40; why.append('not the title')
    lead = fa[0]
    if lead and (re.search(r'\b' + re.escape(lead) + r'\b', ct) or lead.replace(' ', '') in cc.replace(' ', '')):
        s += 25; why.append('artist')
    if lead and (cc_plain in (lead.replace(' ', ''), lead.replace(' ', '') + 'vevo', lead.replace(' ', '') + 'official')
                 or cc_plain.startswith(lead.replace(' ', ''))):
        s += 30; why.append('official channel')
    elif cc.endswith(' topic'):
        s += 5
    k = kind(cand['title'], cand['channel'])
    if k == 'video':
        s += 20; why.append('official video')
    elif k == 'audio':
        s += 8
    elif k == 'live':
        s -= 15; why.append('live')
    song_words = set(fold(song['title']).split()) | set(fold(song.get('album', '')).split())
    for w in WRONG:
        if re.search(r'\b' + re.escape(w) + r'\b', ct) and not set(w.split()) <= song_words:
            s -= 45; why.append(w); break
    return s, why, k


def due(e):
    if e.get('id'):
        return e.get('embed') is None
    if e.get('error') or e.get('from') == 'song.link':
        return True   # the old song.link tries all failed: do them properly
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(e.get('checked', '2000-01-01'))).days >= RETRY_DAYS
    except ValueError:
        return True


def lookup(s, tok):
    e = {'n': s['n'], 'checked': datetime.date.today().isoformat(), 'from': 'YouTube search'}
    q = clean_title(s['title']) + ' ' + (s.get('artists') or [s['artist']])[0]
    j = ytapi.get('search', {'part': 'snippet', 'type': 'video', 'maxResults': 10, 'q': q, 'regionCode': 'GB'},
                  tok, units=100, cap=ytapi.SEARCH_SHARE)
    if '_status' in j:
        e['error'] = j['_status']; e['detail'] = j.get('_text', '')[:200]
        return e
    cands = []
    for it in j.get('items', []):
        vid = (it.get('id') or {}).get('videoId')
        sn = it.get('snippet') or {}
        if not vid:
            continue
        c = {'id': vid, 'title': unescape(sn.get('title', '')), 'channel': sn.get('channelTitle', '')}
        c['score'], c['why'], c['kind'] = score(s, c)
        cands.append(c)
    cands.sort(key=lambda c: -c['score'])
    e['alts'] = [{k: c[k] for k in ('id', 'title', 'channel', 'score')} for c in cands[:4]]
    best = cands[0] if cands and cands[0]['score'] >= GOOD else None
    if not best:
        e['id'] = None
        return e
    # an official video close behind an audio-only upload: show the video
    if best['kind'] != 'video':
        v = next((c for c in cands[1:4] if c['kind'] == 'video' and c['score'] >= best['score'] - 25 and c['score'] >= GOOD), None)
        if v:
            best = v
    e.update({'id': best['id'], 'title': best['title'], 'channel': best['channel'], 'kind': best['kind'],
              'score': best['score'], 'why': best['why']})
    st = ytapi.get('videos', {'part': 'status', 'id': best['id']}, tok, units=1)
    item = (st.get('items') or [None])[0]
    e['embed'] = None if '_status' in st else bool(item and item['status'].get('embeddable') and item['status'].get('privacyStatus') == 'public')
    return e


def unescape(x):
    import html
    return html.unescape(x)


def write_site(data):
    john = {}
    if os.path.exists(JOHN):
        with open(JOHN) as f:
            john = (json.load(f) or {}).get('videos') or {}
    out = {}
    for tid, e in data['songs'].items():
        if e.get('id') and e.get('embed'):
            out[tid] = {'v': e['id'], 'k': e.get('kind', 'other')}
    for tid, v in john.items():
        if v:
            out[tid] = {'v': v, 'k': 'video', 'j': 1}
        else:
            out.pop(tid, None)
    counts = {}
    for x in out.values():
        counts[x['k']] = counts.get(x['k'], 0) + 1
    print('playable on the calendar:', len(out), counts)
    new = json.dumps({'about': 'YouTube video for each song (Spotify track id: v video id, k video / live / audio / other)',
                      'videos': out}, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    old = open(SITE).read() if os.path.exists(SITE) else ''
    if new != old:
        with open(SITE, 'w') as f:
            f.write(new)


def main():
    data = {'about': 'YouTube match for each song (key: Spotify track id), from YouTube search; alts are the runners-up.',
            'songs': {}}
    if os.path.exists(OUT):
        with open(OUT) as f:
            data = json.load(f)
    data['about'] = 'YouTube match for each song (key: Spotify track id), from YouTube search; alts are the runners-up.'
    tok = ytapi.token()
    if not tok:
        print('YouTube: not set up yet (no YT_* secrets)')
        write_site(data)
        return
    import urllib.request
    with urllib.request.urlopen(urllib.request.Request(SONGS_URL, headers={'User-Agent': ytapi.UA}), timeout=30) as r:
        songs = json.loads(r.read())['songs']
    todo = [s for s in songs if s['id'] not in data['songs'] or due(data['songs'][s['id']])]
    print(f'{len(todo)} songs to find on YouTube; {ytapi.ledger()["used"]} units used today')
    done = 0
    try:
        for s in todo:
            e = lookup(s, tok)
            data['songs'][s['id']] = e
            done += 1
            print(s['n'], s['title'], '—', s['artist'], '→', e.get('kind', ''), e.get('id'), '|', e.get('channel', ''), '|',
                  e.get('title', ''), e.get('score', ''), '' if e.get('embed', True) else '(owner blocks other sites)',
                  ('error ' + str(e['error'])) if e.get('error') else '')
    except ytapi.Quota:
        print("today's search allowance is used up; the rest wait for tomorrow")
    if done:
        with open(OUT, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=0, separators=(',', ':'))
    left = sum(1 for s in songs if s['id'] not in data['songs'] or due(data['songs'][s['id']]))
    print(done, 'looked up this run;', left, 'still to do')
    write_site(data)


if __name__ == '__main__':
    main()
