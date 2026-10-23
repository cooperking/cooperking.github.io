#!/usr/bin/env python3
"""Puts the song facts John has signed off on the site, and keeps the draft's and the sign-off page's copies in step.

    python3 _sotd/facts_apply.py             fetch John's sign-offs from the Worker, apply them, rebuild the copies
    python3 _sotd/facts_apply.py --offline   only rebuild the copies from what is saved here

Files:
    songs/facts.json                 live: the facts the calendar shows (signed off by John, or from before sign-off began)
    _sotd/data/facts-pending.json    written and checked, waiting for John: each fact with its source and supporting words
    songs/facts-pending.json         the same, for the sign-off page (songs/facts-admin.html)
    songs/draft/facts.json           live with the waiting ones on top, for the draft calendar (songs/draft/)

A sign-off replaces everything live for that song and its artists with exactly what John signed: his edits, his own
facts (marked by John) and his order. Run by .github/workflows/facts.yml, which the Worker starts on each sign-off.
"""
import copy, json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIVE = os.path.join(ROOT, 'songs', 'facts.json')
PENDING = os.path.join(HERE, 'data', 'facts-pending.json')
PENDING_PUB = os.path.join(ROOT, 'songs', 'facts-pending.json')
DRAFT = os.path.join(ROOT, 'songs', 'draft', 'facts.json')
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


def main(offline):
    live = load(LIVE, {'src': [], 'songs': {}, 'artists': {}})
    pending = load(PENDING, {'songs': {}})
    pending['about'] = ('Song facts written and checked, waiting for John to sign them off on songs/facts-admin.html. '
                        'Each fact: t the fact, u and src its source, q the words in the source that back it.')
    applied = []
    if not offline:
        with urllib.request.urlopen(urllib.request.Request(SIGNED_URL, headers={'User-Agent': 'SongOfTheDayCalendar/1.0'}), timeout=30) as r:
            signed = json.loads(r.read())
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
    live = page_format(live)
    changed = [p for p, d, c in ((LIVE, live, True), (PENDING, pending, False), (PENDING_PUB, pending, True),
                                 (DRAFT, page_format(live, pending['songs']), True)) if write(p, d, c)]
    print(len(applied), 'songs signed off and put live;', len(pending['songs']), 'waiting;',
          'changed:', ', '.join(os.path.relpath(p, ROOT) for p in changed) or 'nothing')


if __name__ == '__main__':
    main('--offline' in sys.argv)
