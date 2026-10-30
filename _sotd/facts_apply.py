#!/usr/bin/env python3
"""Puts the song facts John has signed off on the site, and keeps the draft's and the sign-off page's copies in step.

    python3 _sotd/facts_apply.py             fetch John's sign-offs from the Worker, apply them, rebuild the copies
    python3 _sotd/facts_apply.py --offline   only rebuild the copies from what is saved here
    python3 _sotd/facts_apply.py --offline --promote TRACK...|all
                                             put waiting facts live without John's sign-off (only when he has said so:
                                             on 23 Oct 2026 he asked for the checked rewrite of every existing song to go
                                             straight live)

Files:
    songs/facts.json                 live: the facts the calendar shows, signed off by John (or from before sign-off
                                     began); a new song comes out on the calendar when he signs it off (released.py)
    _sotd/data/facts-pending.json    written and checked, waiting for John: each fact with its source and supporting words
    songs/facts-pending.json         the same, for the sign-off page (the Songs tab of songs/admin.html)
    songs/draft/facts.json           live with the waiting ones on top, for the draft calendar (songs/draft/): only for
                                     songs already out, so the draft holds a new song back as the live calendar does
    _sotd/data/facts-quotes.json     each written fact's supporting words from its source (fact text: words), kept when
    songs/facts-quotes.json          it goes live, for the sign-off page's "Supporting words"
A sign-off can also carry the day's moods, genres and YouTube video as John left them: where they differ from what the
calendar shows, they go into _sotd/data/john.json (moods, genres) and _sotd/data/john-videos.json (video), which win
over everything, and the job (facts.yml) then starts song-tags.yml to rebuild the calendar's moods and videos.

A sign-off replaces everything live for that song and its artists with exactly what John signed: his edits, his own
facts (marked by John) and his order. Run by .github/workflows/facts.yml, which the Worker starts on each sign-off.
"""
import copy, json, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIVE = os.path.join(ROOT, 'songs', 'facts.json')
PENDING = os.path.join(HERE, 'data', 'facts-pending.json')
PENDING_PUB = os.path.join(ROOT, 'songs', 'facts-pending.json')
DRAFT = os.path.join(ROOT, 'songs', 'draft', 'facts.json')
QUOTES = os.path.join(HERE, 'data', 'facts-quotes.json')
QUOTES_PUB = os.path.join(ROOT, 'songs', 'facts-quotes.json')
JOHN_TAGS = os.path.join(HERE, 'data', 'john.json')
JOHN_VIDEOS = os.path.join(HERE, 'data', 'john-videos.json')
MOODS_PUB = os.path.join(ROOT, 'songs', 'moods.json')
VIDEOS_PUB = os.path.join(ROOT, 'songs', 'videos.json')
SIGNED_URL = os.environ.get('SOTD_FACTS_URL', 'https://sotd-playlists.cooperking.workers.dev/facts/signed')
ABOUT = ('Facts about each song and its artists, written from Wikipedia (CC BY-SA 4.0), MusicBrainz (CC0), the artists\' and '
         'labels\' own pages and interviews and articles in the music press, each checked against its source, and signed off '
         'by John (who may have edited them or added his own). src: the sources; signed: when John signed each song off.')
JOHN = {'k': 'j', 't': 'John Griffiths', 'u': ''}


def load(path, empty):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(empty)


def write(path, data, compact=True):
    text = json.dumps(data, ensure_ascii=False, separators=(',', ':')) if compact else json.dumps(data, ensure_ascii=False, indent=1)
    old = open(path, encoding='utf-8').read() if os.path.exists(path) else None
    if old == text:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return True


class Sources:
    """The src list: one entry per source address (John's own facts share one), referred to by index."""
    def __init__(self):
        self.list, self.ix = [], {}

    def add(self, k, t, u):
        key = u or ('john' if k == 'j' else t)
        if key not in self.ix:
            self.ix[key] = len(self.list)
            self.list.append({'k': k, 't': t, 'u': u})
        return self.ix[key]

    def fact(self, f):   # a fact as signed or pending: {t, u, src} or {t, by: 'John'}
        u = f.get('u') or ''
        if not u:
            return [f['t'], self.add(**JOHN)]
        k = 'w' if 'wikipedia.org' in u else 'm' if 'musicbrainz.org' in u else 'x'
        return [f['t'], self.add(k, f.get('src') or u, u)]


def page_format(live, extra_songs=None):
    """The calendar's format, with the src list rebuilt so it holds only what's used; extra_songs (pending) go on top."""
    old = live.get('src', [])
    src = Sources()
    out = {'about': ABOUT, 'src': None, 'songs': {}, 'artists': {}, 'signed': dict(live.get('signed', {}))}
    for tid, e in live.get('songs', {}).items():
        out['songs'][tid] = {'s': [[t, src.add(**old[i])] for t, i in e.get('s', [])], 'a': list(e.get('a', []))}
    for k, e in live.get('artists', {}).items():
        out['artists'][k] = {'name': e.get('name', ''), 'f': [[t, src.add(**old[i])] for t, i in e.get('f', [])]}
    for tid, e in (extra_songs or {}).items():
        out['songs'][tid] = {'s': [src.fact(f) for f in e.get('s', [])], 'a': list(e.get('a', {}).keys())}
        for k, v in e.get('a', {}).items():
            out['artists'][k] = {'name': v.get('name', ''), 'f': [src.fact(f) for f in v.get('f', [])]}
    out['src'] = src.list
    return out


def tags_and_videos(news):
    """The moods, genres and video John signed off with, where they differ from what the calendar shows now."""
    shown = load(MOODS_PUB, {'songs': {}}).get('songs', {})
    vids = load(VIDEOS_PUB, {'videos': {}}).get('videos', {})
    john = load(JOHN_TAGS, {})
    jv = load(JOHN_VIDEOS, {'videos': {}, 'notes': {}})
    jv.setdefault('videos', {}); jv.setdefault('notes', {})
    for tid, e in news.items():
        day = (e.get('at') or '')[:10]
        cur = shown.get(tid, {})
        moods, genres = e.get('moods'), e.get('genres')
        if (moods and moods != cur.get('moods')) or (genres is not None and genres != cur.get('genres')):
            rec = john.get(tid) or {'n': cur.get('n'), 'title': cur.get('title'), 'artist': cur.get('artist')}
            if moods:
                rec['moods'] = moods
            if genres is not None:
                rec['genres'] = genres
            rec['note'] = 'John, on the sign-off page (%s)' % day
            john[tid] = rec
        if 'video' in e and e['video'] != (vids.get(tid) or {}).get('v', ''):
            jv['videos'][tid] = e['video']
            jv['notes'][tid] = '%s: John, on the sign-off page (%s)' % (cur.get('title') or tid, day)
    write(JOHN_TAGS, john, False)
    write(JOHN_VIDEOS, jv, False)


def main(offline, promote=()):
    live = load(LIVE, {'src': [], 'songs': {}, 'artists': {}})
    pending = load(PENDING, {'songs': {}})
    pending['about'] = ('Song facts written and checked, waiting for John to sign them off on the Songs tab of songs/admin.html. '
                        'Each fact: t the fact, u and src its source, q the words in the source that back it.')
    applied = []
    quotes = load(QUOTES, {})
    for e in pending['songs'].values():   # keep every waiting fact's supporting words, to show once it's live too
        for f in e.get('s', []) + [f for a in e.get('a', {}).values() for f in a.get('f', [])]:
            if f.get('q'):
                quotes[f['t']] = f['q']
    if not offline:
        want = os.environ.get('SOTD_FACTS_AT', '').strip()   # the sign-off that started this job
        for tries in range(13):   # until /facts/signed shows that sign-off: up to 2 minutes, 10 seconds apart
            with urllib.request.urlopen(urllib.request.Request(SIGNED_URL, headers={'User-Agent': 'SongOfTheDayCalendar/1.0'}), timeout=30) as r:
                signed = json.loads(r.read())
            if not want or any(str(e.get('at', '')) >= want for e in signed.values()):
                break
            print('The sign-off of', want, 'isn’t in /facts/signed yet; looking again in 10 seconds')
            time.sleep(10)
        else:
            print('The sign-off of', want, 'never showed in /facts/signed; putting live what it has')
        done = live.setdefault('signed', {})
        news = {tid: e for tid, e in signed.items() if done.get(tid) != e.get('at')}
        if news:
            # signed facts become live: the song's facts and each of its artists' facts, exactly as John left them
            merged = page_format(live, {tid: {'s': e.get('s', []), 'a': e.get('a', {})} for tid, e in news.items()})
            for tid, e in news.items():
                if not e.get('a') and tid in live.get('songs', {}):   # a sign-off without artists keeps the song's artist list
                    merged['songs'][tid]['a'] = live['songs'][tid].get('a', [])
                merged['signed'][tid] = e.get('at')
                pending['songs'].pop(tid, None)
                applied.append(tid)
            live = merged
            tags_and_videos(news)
    if promote:
        ids = list(pending['songs']) if 'all' in promote else [t for t in promote if t in pending['songs']]
        if ids:
            live = page_format(live, {t: pending['songs'][t] for t in ids})
            for t in ids:
                pending['songs'].pop(t)
            print(len(ids), 'waiting songs put live without sign-off')
    # a new song's checked facts wait for John: he checks them, adds his own recording and video for the members, and his
    # sign-off puts them live, and with them the song's day (released.py; John, 28 Oct 2026: "the site shouldn't publish
    # automatically for new song of the day as i'd like to check the facts and add videos and songs myself for members").
    # From 24 to 28 Oct they went live as soon as they were checked. The draft holds them back too (John, 26 Oct 2026:
    # the live calendar and the draft exactly the same): it has the waiting facts only for songs already out.
    live = page_format(live)
    changed = [p for p, d, c in ((LIVE, live, True), (PENDING, pending, False), (PENDING_PUB, pending, True),
                                 (DRAFT, page_format(live, {t: e for t, e in pending['songs'].items() if t in live.get('songs', {}) or t in live.get('signed', {})}), True),
                                 (QUOTES, quotes, False),
                                 (QUOTES_PUB, quotes, True)) if write(p, d, c)]
    print(len(applied), 'songs signed off and put live;', len(pending['songs']), 'waiting;',
          'changed:', ', '.join(os.path.relpath(p, ROOT) for p in changed) or 'nothing')


if __name__ == '__main__':
    args = sys.argv[1:]
    main('--offline' in args, args[args.index('--promote') + 1:] if '--promote' in args else ())
