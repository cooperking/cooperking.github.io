#!/usr/bin/env python3
"""Writes songs/facts.json for the calendar from _sotd/data/facts.json (the checked facts, each with the source it
comes from and the words in that source that back it) and _sotd/data/sources.json (the sources' addresses).

On the site each day shows "About the song" and "About <artist>", with the Wikipedia articles (CC BY-SA 4.0) and
MusicBrainz pages the facts come from linked underneath. At most three of each."""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FACTS = os.path.join(HERE, 'data', 'facts.json')
SOURCES = os.path.join(HERE, 'data', 'sources.json')
OUT = os.path.join(ROOT, 'songs', 'facts.json')
MAX = 3


def main():
    if not os.path.exists(FACTS) or not os.path.exists(SOURCES):
        print('no facts yet'); return
    facts = json.load(open(FACTS))
    src = json.load(open(SOURCES))
    pages, artists_src, songs_src = src['pages'], src['artists'], src['songs']
    refs, index = [], {}

    def ref(name, artist_key):
        if name == 'MUSICBRAINZ':
            mb = (artists_src.get(artist_key) or {}).get('mb') or {}
            if not mb.get('url'):
                return None
            k = ('m', mb['url'])
            item = {'k': 'm', 't': artists_src[artist_key]['name'], 'u': mb['url']}
        else:
            p = pages.get(name)
            if not p:
                return None
            k = ('w', p['url'])
            item = {'k': 'w', 't': p['title'], 'u': p['url']}
        if k not in index:
            index[k] = len(refs)
            refs.append(item)
        return index[k]

    out_artists = {}
    for key, fs in facts.get('artists', {}).items():
        rows = [[f['t'], ref(f['src'], key)] for f in fs[:MAX]]
        rows = [r for r in rows if r[1] is not None]
        if rows and key in artists_src:
            out_artists[key] = {'name': artists_src[key]['name'], 'f': rows}
    out_songs = {}
    for tid, e in songs_src.items():
        fs = facts.get('songs', {}).get(tid) or []
        rows = [[f['t'], ref(f['src'], None)] for f in fs[:MAX]]
        rows = [r for r in rows if r[1] is not None]
        arts = [a for a in e.get('artists', []) if a in out_artists]
        if rows or arts:
            out_songs[tid] = {'s': rows, 'a': arts}
    data = {'about': 'A few facts about each song and its artists, written from the English Wikipedia (CC BY-SA 4.0) and '
                     'MusicBrainz (CC0), each checked against the passage it comes from. src: the sources, linked on the page.',
            'src': refs, 'songs': out_songs, 'artists': out_artists}
    with open(OUT, 'w') as f:
        f.write(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    print(len(out_songs), 'songs and', len(out_artists), 'artists with facts;', len(refs), 'sources')


if __name__ == '__main__':
    main()
