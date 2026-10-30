#!/usr/bin/env python3
"""Finds a YouTube video for each song not in _sotd/data/youtube.json yet, and writes songs/videos.json for the calendar.

YouTube's own search (Data API, John's sign-in; see ytapi.py) gives up to 10 uploads for "artist title". Each is
scored: the song's title in the upload's title, the artist in its title or channel, an official channel (the
artist's own, VEVO, or YouTube's auto-generated "Artist - Topic" upload of the recording), "Official Video"; and
marked down for covers, lyrics, karaoke, live, remixes and the like that the song itself isn't. The best one wins,
and one more lookup says whether its owner lets other sites play it. The runner-up uploads are kept for John.

John's own links come first (data/links.json, from his playlist export): one lookup of 1 unit checks 50 of them
(title, channel, whether other sites may play it). His link is used unless it doesn't look like the song (scores
under LINK_OK: another song's title, or a slowed / lyrics / live version the song isn't), or the search already found
an official video where his link is an audio-only upload, or his link can't be played on other sites and the
search's can. Songs with no usable link are searched, never picking a link already judged wrong.
Each chosen video's length is fetched too (50 to a 1-unit lookup) and kept as secs, to compare with Spotify's.

Nothing is downloaded: the calendar plays the video in YouTube's own player.
YouTube allows 100 searches a day, so a long backlog takes a few days; songs not done yet wait for the next run.
Songs with no good match are tried again after 30 days. John's own picks (data/john-videos.json "videos":
{track id: video id, or "" for none}) win over everything."""
import datetime, json, os, re, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ytapi   # noqa: E402

ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, 'data', 'youtube.json')
JOHN = os.path.join(HERE, 'data', 'john-videos.json')
LINKS = os.path.join(HERE, 'data', 'links.json')
LIST = "John's list"
LINK_OK = 30      # a link scoring under this doesn't look like the song
LINK_CHECK = 3    # bump to look at every link again
LENGTH_PENALTY = 60
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
    if re.search(r'official\s+audio|\(audio\)|\[audio\]', t):
        return 'audio'   # the recording over a still picture
    if re.search(r'visuali[sz]er|lyric', t):
        return 'lyric'   # words or shapes that move: not a still picture (John, 28 Oct 2026)
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
    elif k in ('audio', 'lyric'):
        s += 8
    elif k == 'live':
        s -= 15; why.append('live')
    song_words = set(fold(song['title']).split()) | set(fold(song.get('album', '')).split())
    official = 'official channel' in why or re.search(r'\bofficial\b', ct)
    for w in WRONG:
        if w.startswith('lyric') and official:
            continue   # the artist's or label's own lyric video is the song
        if re.search(r'\b' + re.escape(w) + r'\b', ct) and not set(w.split()) <= song_words:
            s -= 45; why.append(w); break
    return s, why, k


def iso_secs(x):
    m = re.match(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$', x or '')
    if not m:
        return None
    d, h, mi, se = (int(v or 0) for v in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


def length_off(cand, ms):
    """Mark down an upload whose length is well off the song's on Spotify (a snippet, a live set, an extended mix,
    both sides of a single), unless it's an official video, which often runs long."""
    if not ms or cand.get('secs') is None or cand.get('kind') == 'video':
        return False
    diff = abs(cand['secs'] - ms / 1000)
    if diff > 40 and diff > .2 * ms / 1000:
        cand['score'] -= LENGTH_PENALTY; cand['why'] = cand.get('why', []) + ['length %d:%02d' % divmod(cand['secs'], 60)]
        return True
    return False


def due(e):
    if e.get('id'):
        return e.get('embed') is None
    if e.get('from') == LIST:
        return True   # his link can't be used: search instead
    if e.get('error') or e.get('from') == 'song.link':
        return True   # the old song.link tries all failed: do them properly
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(e.get('checked', '2000-01-01'))).days >= RETRY_DAYS
    except ValueError:
        return True


def lookup(s, tok):
    e = {'n': s['n'], 'checked': datetime.date.today().isoformat(), 'from': 'YouTube search'}
    if s.get('listId'):
        e['listId'] = s['listId']   # his link was looked at already; don't look again
    q = clean_title(s['title']) + ' ' + (s.get('artists') or [s['artist']])[0]
    j = ytapi.get('search', {'part': 'snippet', 'type': 'video', 'maxResults': 10, 'q': q, 'regionCode': 'GB'},
                  tok, units=1, search=True, priority=bool(s.get('newest')))
    if '_status' in j:
        e['error'] = j['_status']; e['detail'] = j.get('_text', '')[:200]
        return e
    cands = []
    for it in j.get('items', []):
        vid = (it.get('id') or {}).get('videoId')
        sn = it.get('snippet') or {}
        if not vid or vid == s.get('listId'):
            continue
        c = {'id': vid, 'title': unescape(sn.get('title', '')), 'channel': sn.get('channelTitle', '')}
        c['score'], c['why'], c['kind'] = score(s, c)
        cands.append(c)
    cands.sort(key=lambda c: -c['score'])
    top = cands[:5]
    if top:
        st = ytapi.get('videos', {'part': 'status,contentDetails', 'id': ','.join(c['id'] for c in top)}, tok, units=1)
        info = {it['id']: it for it in st.get('items', [])}
        for c in top:
            it = info.get(c['id'])
            if it:
                c['secs'] = iso_secs(it['contentDetails'].get('duration'))
                c['embed'] = bool(it['status'].get('embeddable') and it['status'].get('privacyStatus') == 'public')
                length_off(c, s.get('ms'))
        cands = top + cands[5:]
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
              'score': best['score'], 'why': best['why'], 'embed': best.get('embed')})
    if best.get('secs') is not None:
        e['secs'] = best['secs']
    return e


def from_links(songs, data, links, tok, ms=None):
    """Check John's links, 50 to a 1-unit lookup, and take them as each song's video unless the search did better."""
    need = [s for s in songs if links.get(s['id']) and data['songs'].get(s['id'], {}).get('from') != 'John' and (data['songs'].get(s['id'], {}).get('listId') != links[s['id']]
                                                       or data['songs'].get(s['id'], {}).get('listCheck') != LINK_CHECK)]
    ms = ms or {}
    print(len(need), "songs to take from John's links")
    today = datetime.date.today().isoformat()
    took = 0
    for i in range(0, len(need), 50):
        batch = need[i:i + 50]
        j = ytapi.get('videos', {'part': 'snippet,status,contentDetails', 'id': ','.join(links[s['id']] for s in batch), 'maxResults': 50}, tok, units=1)
        if '_status' in j:
            print('could not check the links:', j['_status'], j.get('_text', '')[:160]); return took
        found = {it['id']: it for it in j.get('items', [])}
        for s in batch:
            vid, old = links[s['id']], data['songs'].get(s['id'], {})
            it = found.get(vid)
            if not it or (it.get('status') or {}).get('privacyStatus') not in ('public', 'unlisted'):
                e = dict(old) if old.get('id') and old.get('from') == 'YouTube search' else {'n': s['n'], 'id': None, 'from': LIST}
                e.update({'listId': vid, 'listCheck': LINK_CHECK, 'checked': today, 'listNote': 'link gone or private'})
                data['songs'][s['id']] = e
                print(s['n'], s['title'], '— his link is gone or private', vid)
                continue
            sn, st = it['snippet'], it['status']
            mine = {'n': s['n'], 'checked': today, 'from': LIST, 'listId': vid, 'id': vid, 'title': unescape(sn.get('title', '')),
                    'channel': sn.get('channelTitle', ''), 'embed': bool(st.get('embeddable') and st.get('privacyStatus') == 'public')}
            mine['kind'] = kind(mine['title'], mine['channel'])
            mine['score'], mine['why'], _ = score(s, mine)
            mine['secs'] = iso_secs((it.get('contentDetails') or {}).get('duration'))
            if mine['secs'] is None:
                mine.pop('secs')
            length_off(mine, ms.get(s['id']))
            searched = old.get('searched') if old.get('from') == LIST else old
            if not (searched and searched.get('id') and searched['id'] != vid and searched.get('from', 'YouTube search') == 'YouTube search'):
                searched = old if old.get('id') and old.get('from') == 'YouTube search' and old['id'] != vid else None
            if mine['score'] < LINK_OK:   # not this song, or the wrong version of it
                e = dict(searched, n=s['n'], **{'from': 'YouTube search'}) if searched and searched.get('id') else {'n': s['n'], 'id': None, 'from': LIST}
                e.update({'listId': vid, 'listCheck': LINK_CHECK, 'checked': today, 'listNote': 'link looks wrong: ' + mine['title'] + ' | ' + mine['channel']})
                data['songs'][s['id']] = e
                print(s['n'], s['title'], '— his link looks wrong:', mine['title'], '|', mine['channel'], mine['why'])
                continue
            better = searched and ((searched.get('kind') == 'video' and mine['kind'] != 'video' and searched.get('embed'))
                                   or (searched.get('embed') and not mine['embed']))
            if better:
                e = dict(searched); e['listId'] = vid; e['from'] = 'YouTube search' 
                e['list'] = {k: mine[k] for k in ('id', 'title', 'channel', 'kind', 'embed')}
            else:
                e = mine
                if searched:
                    e['searched'] = {k: searched.get(k) for k in ('id', 'title', 'channel', 'kind', 'embed')}
            e['listCheck'] = LINK_CHECK
            data['songs'][s['id']] = e
            took += 1
            print(s['n'], s['title'], '—', s['artist'], '→', e['from'], e.get('kind'), e['id'], '|', e.get('channel'), '|', e.get('title'),
                  '' if e.get('embed') else '(owner blocks other sites)')
    return took


def johns_picks(songs, data, john, tok):
    """John's own video for a song: looked up once (title, channel, whether other sites may play it) and always used."""
    by = {s['id']: s for s in songs}
    need = [(t, v) for t, v in john.items() if v and t in by and (data['songs'].get(t, {}).get('from') != 'John' or data['songs'][t].get('id') != v)]
    for t, v in need:
        j = ytapi.get('videos', {'part': 'snippet,status', 'id': v}, tok, units=1)
        it = (j.get('items') or [None])[0]
        old = data['songs'].get(t, {})
        e = {'n': by[t]['n'], 'from': 'John', 'id': v, 'checked': datetime.date.today().isoformat()}
        for k in ('listId', 'listCheck', 'listNote'):
            if k in old:
                e[k] = old[k]
        if old.get('id') and old['id'] != v:
            e['before'] = {k: old.get(k) for k in ('id', 'title', 'channel', 'from')}
        if it:
            e.update({'title': unescape(it['snippet'].get('title', '')), 'channel': it['snippet'].get('channelTitle', ''),
                      'embed': bool(it['status'].get('embeddable') and it['status'].get('privacyStatus') == 'public')})
            e['kind'] = kind(e['title'], e['channel'])
        else:
            e['embed'] = False
        data['songs'][t] = e
        print(e['n'], by[t]['title'], "— John's pick:", v, '|', e.get('channel'), '|', e.get('title'), '' if e.get('embed') else '(not playable on other sites)')
    return len(need)


def lengths(data, tok):
    """Each chosen video's length in seconds, 50 to a 1-unit lookup."""
    need = [e for e in data['songs'].values() if e.get('id') and 'secs' not in e]
    for i in range(0, len(need), 50):
        batch = need[i:i + 50]
        j = ytapi.get('videos', {'part': 'contentDetails', 'id': ','.join(e['id'] for e in batch), 'maxResults': 50}, tok, units=1)
        got = {it['id']: it['contentDetails'].get('duration', '') for it in j.get('items', [])}
        for e in batch:
            m = re.match(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$', got.get(e['id'], ''))
            if m:
                d, h, mi, se = (int(x or 0) for x in m.groups())
                e['secs'] = ((d * 24 + h) * 60 + mi) * 60 + se
    return len(need)


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
            out[tid] = {'v': e['id'], 'k': kind(e['title'], e.get('channel')) if e.get('title') else e.get('kind', 'other')}   # (worked out again: kinds change)
            if e.get('secs'):
                out[tid]['s'] = e['secs']   # its length: the calendar tells an advert from the video by it
    for tid, v in john.items():
        e = data['songs'].get(tid, {})
        if not v:
            out.pop(tid, None)
        elif not (e.get('from') == 'John' and e.get('id') == v):
            out[tid] = {'v': v, 'k': 'video'}   # not looked up yet
        elif e.get('embed'):
            out[tid] = {'v': v, 'k': kind(e['title'], e.get('channel')) if e.get('title') else e.get('kind', 'video')}
            if e.get('secs'):
                out[tid]['s'] = e['secs']
        else:
            out.pop(tid, None)
    import video_still   # p: whether each is only a still picture, from YouTube's own frames (video_still.py, run next)
    video_still.mark(out)
    counts = {}
    for x in out.values():
        counts[x['k']] = counts.get(x['k'], 0) + 1
    print('playable on the calendar:', len(out), counts)
    site = {'about': 'YouTube video for each song (Spotify track id: v video id, k video / live / lyric / audio / other, by the title; p 1 only a still picture, 0 not, from YouTube’s own frames of it; s its length in seconds); '
                     'playlist: the YouTube playlist of them all',
            'videos': out}
    pl = os.path.join(HERE, 'data', 'youtube-playlist.json')
    if os.path.exists(pl):
        with open(pl) as f:
            url = json.load(f).get('url')
        if url:
            site['playlist'] = url
    new = json.dumps(site, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
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
    data['about'] = ("YouTube video for each song (key: Spotify track id): from John's list (data/links.json) or YouTube search; "
                     "list / searched: the one not chosen; alts: the search's runners-up.")
    tok = ytapi.token()
    if not tok:
        print('YouTube: not set up yet (no YT_* secrets)')
        write_site(data)
        return
    import urllib.request
    with urllib.request.urlopen(urllib.request.Request(SONGS_URL, headers={'User-Agent': ytapi.UA}), timeout=30) as r:
        songs = json.loads(r.read())['songs']
    links, ms = {}, {}
    if os.path.exists(LINKS):
        with open(LINKS) as f:
            lj = json.load(f)
            links, ms = lj.get('links') or {}, lj.get('ms') or {}
    john = {}
    if os.path.exists(JOHN):
        with open(JOHN) as f:
            john = (json.load(f) or {}).get('videos') or {}
    done = 0
    try:
        done += johns_picks(songs, data, john, tok)
        done += from_links(songs, data, links, tok, ms)
    except ytapi.Quota:
        print("today's YouTube allowance is used up")
    todo = [s for s in songs if s['id'] not in data['songs'] or due(data['songs'][s['id']])]
    newest = max((s['n'] for s in songs if not s.get('upl')), default=None)
    todo.sort(key=lambda s: (bool(s.get('upl')), -s['n']))   # today's song first, and it may use the searches kept back for it; members' songs after John's
    print(f'{len(todo)} songs to search YouTube for; {ytapi.ledger()["used"]} units used today')
    try:
        for s in todo:
            prev = data['songs'].get(s['id'], {})
            e = lookup(dict(s, listId=prev.get('listId'), ms=ms.get(s['id']) or s.get('ms'), newest=s['n'] == newest), tok)
            for k in ('listNote', 'listCheck'):
                if k in prev:
                    e[k] = prev[k]
            data['songs'][s['id']] = e
            done += 1
            print(s['n'], s['title'], '—', s['artist'], '→', e.get('kind', ''), e.get('id'), '|', e.get('channel', ''), '|',
                  e.get('title', ''), e.get('score', ''), '' if e.get('embed', True) else '(owner blocks other sites)',
                  ('error ' + str(e['error'])) if e.get('error') else '')
    except ytapi.Quota:
        print("today's search allowance is used up; the rest wait for tomorrow")
    try:
        done += lengths(data, tok)
    except ytapi.Quota:
        print("no allowance left today for the videos' lengths")
    if done:
        with open(OUT, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=0, separators=(',', ':'))
    left = sum(1 for s in songs if s['id'] not in data['songs'] or due(data['songs'][s['id']]))
    print(done, 'looked up this run;', left, 'still to do')
    write_site(data)


if __name__ == '__main__':
    main()
