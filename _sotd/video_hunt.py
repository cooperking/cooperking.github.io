#!/usr/bin/env python3
"""Looks for official uploads of chosen songs on YouTube, for Claude to choose from: John asked on 23 October 2026 for
an official video for every song where one exists (the artist's or label's own upload, VEVO, or YouTube's
auto-generated "Artist - Topic" upload of the recording), the same recording as the Spotify track.
Run by .github/workflows/video-hunt.yml; the chosen ones go into data/john-videos.json, which wins.

    python _sotd/video_hunt.py --songs 12 85 ...       one YouTube search per song (song numbers), then a lookup of
                                                        every result: title, channel, length, whether other sites may play it
    python _sotd/video_hunt.py --songs 12 --video      the same, searching for "<title> <artist> official video"
    python _sotd/video_hunt.py --ids ID ID ...          only look up these uploads

Writes data/video-hunt.json: songs {track id: {n, title, artist, secs (Spotify), query, results [...]}} and
ids {video id: {...}}. Searches count against the day's 90 (data/youtube-quota.json; the last 5 are kept for today's song); lookups cost 1 unit per 50.
"""
import datetime, json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ytapi   # noqa: E402
from youtube import clean_title, iso_secs, kind, score, unescape, SONGS_URL, LINKS   # noqa: E402

OUT = os.path.join(HERE, 'data', 'video-hunt.json')


def details(ids, tok):
    out = {}
    for i in range(0, len(ids), 50):
        j = ytapi.get('videos', {'part': 'snippet,contentDetails,status', 'id': ','.join(ids[i:i + 50]), 'maxResults': 50}, tok, units=1)
        for it in j.get('items', []):
            sn, st = it.get('snippet') or {}, it.get('status') or {}
            out[it['id']] = {'title': unescape(sn.get('title', '')), 'channel': sn.get('channelTitle', ''),
                             'channelId': sn.get('channelId', ''), 'published': (sn.get('publishedAt') or '')[:10],
                             'secs': iso_secs((it.get('contentDetails') or {}).get('duration')),
                             'embed': bool(st.get('embeddable') and st.get('privacyStatus') == 'public'),
                             'desc': unescape(sn.get('description', ''))[:400]}
    return out


def main():
    args = sys.argv[1:]
    video = '--video' in args
    nums = [a for a in args[args.index('--songs') + 1:] if a.isdigit()] if '--songs' in args else []
    ids = [a for a in args[args.index('--ids') + 1:] if not a.startswith('--')] if '--ids' in args else []
    data = {'songs': {}, 'ids': {}}
    if os.path.exists(OUT):
        with open(OUT) as f:
            data = json.load(f)
    data['about'] = ("YouTube search results and upload details for choosing each song's official video (see "
                     "_sotd/video_hunt.py). secs: the length; embed: other sites may play it.")
    tok = ytapi.token()
    if not tok:
        raise SystemExit('YouTube: no YT_* secrets')
    ms = {}
    if os.path.exists(LINKS):
        with open(LINKS) as f:
            ms = json.load(f).get('ms') or {}
    today = datetime.date.today().isoformat()
    if nums:
        with urllib.request.urlopen(urllib.request.Request(SONGS_URL, headers={'User-Agent': ytapi.UA}), timeout=30) as r:
            songs = {str(s['n']): s for s in json.loads(r.read())['songs']}
        try:
            for n in nums:
                s = songs.get(n)
                if not s:
                    print('no song', n); continue
                lead = (s.get('artists') or [s['artist']])[0]
                q = clean_title(s['title']) + ' ' + lead + (' official video' if video else '')
                j = ytapi.get('search', {'part': 'snippet', 'type': 'video', 'maxResults': 15, 'q': q, 'regionCode': 'GB'},
                              tok, units=1, search=True)
                if '_status' in j:
                    print(n, 'search failed', j['_status'], j.get('_text', '')[:200]); continue
                res = []
                for it in j.get('items', []):
                    vid = (it.get('id') or {}).get('videoId')
                    if vid:
                        res.append({'id': vid})
                info = details([c['id'] for c in res], tok)
                for c in res:
                    c.update(info.get(c['id'], {}))
                    c['score'], c['why'], c['kind'] = score(s, {'title': c.get('title', ''), 'channel': c.get('channel', '')})
                res.sort(key=lambda c: -c['score'])
                e = data['songs'].setdefault(s['id'], {'n': s['n'], 'title': s['title'], 'artist': s['artist']})
                e.update({'secs': round((ms.get(s['id']) or s.get('ms') or 0) / 1000) or None, 'checked': today})
                e.setdefault('queries', {})[q] = res
                print(n, s['title'], '—', s['artist'], '| Spotify', e['secs'], 's')
                for c in res[:8]:
                    print('    %s %4ss %-5s %3d  %s  |  %s' % (c['id'], c.get('secs'), c['kind'], c['score'], c.get('title', '')[:70], c.get('channel', '')))
        except ytapi.Quota:
            print("today's search allowance is used up")
    if ids:
        for vid, d in details(ids, tok).items():
            data['ids'][vid] = dict(d, checked=today)
            print(vid, d['secs'], 's |', d['title'], '|', d['channel'], '' if d['embed'] else '(not playable on other sites)')
    with open(OUT, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=0, separators=(',', ':'))
    print(ytapi.ledger())


if __name__ == '__main__':
    main()
