#!/usr/bin/env python3
"""Whether each song's YouTube video is only a still picture (the recording over one picture, as YouTube's
"Artist - Topic" uploads are), told from the three frames YouTube itself takes of every upload, a quarter, half and three
quarters of the way through: i.ytimg.com/vi/<id>/hq1.jpg, hq2.jpg and hq3.jpg (480x360; 1.jpg to 3.jpg, 120x90, where
those are missing). Before 28 Oct 2026 the admin page went by the upload's title; John: "that's a much better way of
doing it".

    python3 _sotd/video_still.py            the videos in songs/videos.json not looked at yet
    python3 _sotd/video_still.py ID ...     only these, saying what it finds (for trying it)

Each frame is made 64x48 and grey. Two frames differ where a pixel's grey changes by more than 24 (of 255). A video is a
still picture when no two of its frames differ in more than 0.5% of their pixels, and on average by under 3: of 35
songs measured on 28 Oct 2026, the still pictures differed in 0% to 0.2% (the JPEG's own noise), lyric videos in 5% to
72%, music videos in 29% to 74%.

Kept in data/video-still.json, so each video is fetched once: v {video id: {p: 1 a still picture or 0 not, d: the most
of the pixels two frames differ in, m: the most an average pixel differs}}; a video YouTube has no frames for:
{x: why, at: the date}, tried again after a week. Adds p to each video in songs/videos.json, which the admin page's
Still picture list goes by (else by the title, k audio). Run by song-tags.yml after youtube.py (whose write_site keeps
p); video_alts.py uses check() for the other uploads it offers.
"""
import datetime, io, json, os, sys, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, 'data', 'video-still.json')
SITE = os.path.join(ROOT, 'songs', 'videos.json')
FRAMES = os.environ.get('SOTD_FRAMES_URL', 'https://i.ytimg.com/vi/')
SETS = (('hq1.jpg', 'hq2.jpg', 'hq3.jpg'), ('1.jpg', '2.jpg', '3.jpg'))
SIZE = (64, 48)
STEP = 24        # a pixel differs when its grey changes by more than this
MOST = 0.005     # a still picture: no two frames differ in more than this share of the pixels
MEAN = 3.0       # and on average by under this
RETRY_DAYS = 7
UA = 'SongOfTheDayCalendar/1.0 (+https://cooperking.github.io/songs/)'


class Missing(Exception):
    pass


def frame(url):
    from PIL import Image
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=20) as r:
            b = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Missing(url.rsplit('/', 1)[-1])
        raise
    return list(Image.open(io.BytesIO(b)).convert('L').resize(SIZE, Image.BILINEAR).tobytes())


def judge(frames):
    d = m = 0.0
    for i in range(len(frames)):
        for j in range(i + 1, len(frames)):
            diff = [abs(x - y) for x, y in zip(frames[i], frames[j])]
            d = max(d, sum(1 for x in diff if x > STEP) / len(diff))
            m = max(m, sum(diff) / len(diff))
    return {'p': 1 if d <= MOST and m < MEAN else 0, 'd': round(d, 4), 'm': round(m, 2)}


def look(vid):
    """{p, d, m}; {x} when YouTube has no frames for it; None when it couldn't be reached (tried again next run)."""
    gone = []
    for names in SETS:
        try:
            return judge([frame(FRAMES + vid + '/' + n) for n in names])
        except Missing as e:
            gone.append(str(e))
        except Exception as e:   # the network: not kept, so the next run tries again
            print('  could not fetch the frames of', vid + ':', e)
            return None
    return {'x': 'no frames (' + ', '.join(gone) + ')'}


def load():
    try:
        with open(OUT, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {'about': 'Whether each YouTube video is only a still picture, from YouTube’s own three frames of it (see '
                         '_sotd/video_still.py): p 1 still, 0 not; d the most of the pixels two frames differ in; m the most '
                         'an average pixel differs (of 255); x: no frames to look at.', 'v': {}}


def check(ids, data=None):
    """Looks at the videos among ids not looked at yet (or whose frames were missing a week ago); returns data, with
    data['changed'] set when anything new was found. data defaults to data/video-still.json."""
    data = data if data is not None else load()
    v, today = data.setdefault('v', {}), datetime.date.today()
    todo = []
    for i in dict.fromkeys(i for i in ids if i):
        r = v.get(i)
        if r and ('p' in r or (today - datetime.date.fromisoformat(r.get('at', '2000-01-01'))).days < RETRY_DAYS):
            continue
        todo.append(i)
    if todo:
        print('looking at the frames of', len(todo), 'video' + ('' if len(todo) == 1 else 's'))
    with ThreadPoolExecutor(8) as ex:
        for i, r in zip(todo, ex.map(look, todo)):
            if r is None:
                continue
            if 'x' in r:
                r['at'] = today.isoformat()
            v[i] = r
            data['changed'] = True
    return data


def save(data):
    if data.pop('changed', None):
        with open(OUT, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
            f.write('\n')


def still(vid, data):
    """1 a still picture, 0 not, None not known."""
    return ((data or {}).get('v') or {}).get(vid, {}).get('p')


def mark(videos, data=None):
    """Sets p on each of songs/videos.json's videos (written by youtube.py's write_site) from what's known."""
    data = data if data is not None else load()
    for x in videos.values():
        p = still(x.get('v'), data)
        if p is None:
            x.pop('p', None)
        else:
            x['p'] = p
    return videos


def main(args):
    if args:
        data = check(args, {'v': {}})
        for i in args:
            print(i, data['v'].get(i, 'could not be reached'))
        return
    with open(SITE, encoding='utf-8') as f:
        site = json.load(f)
    vids = site.get('videos') or {}
    data = check([x['v'] for x in vids.values()])
    save(data)
    old = json.dumps(site, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    mark(vids, data)
    new = json.dumps(site, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    if new != old:
        with open(SITE, 'w', encoding='utf-8') as f:
            f.write(new)
    n = {0: 0, 1: 0, None: 0}
    for x in vids.values():
        n[x.get('p')] += 1
    titled = sum(1 for x in vids.values() if x.get('k') == 'audio')
    print(len(vids), 'songs’ videos:', n[1], 'a still picture,', n[0], 'moving,', n[None], 'not known yet',
          '(by the title alone:', titled, 'a still picture)')
    near = sorted(((r['d'], i) for i, r in data['v'].items() if 'd' in r and MOST / 3 < r['d'] <= MOST * 4), reverse=True)
    for d, i in near:   # close to the line either way: worth a look
        print('  close to the line:', i, data['v'][i])


if __name__ == '__main__':
    main(sys.argv[1:])
