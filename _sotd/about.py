#!/usr/bin/env python3
"""Collects what the song facts on the calendar are written from, into _sotd/data/sources.json.

For each song not looked up yet: the English Wikipedia articles on the song, its album and its artists, and
MusicBrainz's details on each artist (person or group, where from, when started, members).
Articles are found through Wikidata by the song's, album's and artists' Spotify ids; an artist Wikidata
doesn't know by Spotify id is looked up on MusicBrainz (by its Spotify link, else by exact name, marked as such),
and a song or album by its title among that artist's works. Nothing is guessed: no match, no article.

Wikipedia text is CC BY-SA 4.0: each page keeps its address and revision, and the calendar credits it.
MusicBrainz and Wikidata data are CC0. One MusicBrainz request a second, as MusicBrainz asks."""
import datetime, json, os, re, sys, time, urllib.parse, urllib.request
import runinfo

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'data', 'sources.json')
SONGS_URL = os.environ.get('SOTD_SONGS_URL', 'https://sotd-playlists.cooperking.workers.dev/songs.json')
UA = 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)'
WIKI = 'https://en.wikipedia.org/w/api.php'
WD = 'https://www.wikidata.org/w/api.php'
SPARQL = 'https://query.wikidata.org/sparql'
MB = 'https://musicbrainz.org/ws/2/'
VERSION = 1      # bump to look every song up again
CAP = {'artist': 12000, 'song': 10000, 'album': 6000}
DROP = re.compile(r'^(references|external links|notes|further reading|see also|discography|track listing|personnel|'
                  r'charts?|weekly charts|year-end charts|certifications|sales and certifications|release history|'
                  r'filmography|bibliography|awards and nominations|accolades|footnotes|citations|sources|'
                  r'credits and personnel|videography|tours|concert tours|members|band members|former members|timeline)\b', re.I)
SONG_TYPES = 'wd:Q7366 wd:Q134556 wd:Q2188189 wd:Q105543609'
ALBUM_TYPES = 'wd:Q482994 wd:Q208569 wd:Q169930 wd:Q209939 wd:Q222910 wd:Q24672043 wd:Q1242743'


def fetch(url, data=None, headers=None, tries=4):
    h = {'User-Agent': UA, 'Accept': 'application/json'}
    h.update(headers or {})
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            print('  retry', e.code, url[:90])
        except Exception as e:
            print('  retry', e, url[:90])
        time.sleep(3 * (i + 1))
    return None


def mb(path):
    time.sleep(1.1)
    return fetch(MB + path + ('&' if '?' in path else '?') + 'fmt=json')


def sparql(q):
    time.sleep(0.5)
    j = fetch(SPARQL, data=urllib.parse.urlencode({'query': q, 'format': 'json'}).encode(),
              headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/sparql-results+json'})
    return (j or {}).get('results', {}).get('bindings', [])


def val(b, k):
    return b[k]['value'] if k in b else None


def qid(uri):
    return uri.rsplit('/', 1)[-1] if uri else None


def title_of(article):   # https://en.wikipedia.org/wiki/Yard_Act -> Yard Act
    return urllib.parse.unquote(article.rsplit('/wiki/', 1)[-1]).replace('_', ' ') if article else None


def by_spotify(prop, ids):
    """Wikidata items (and English articles) for Spotify ids under one property."""
    out = {}
    ids = sorted(set(i for i in ids if i))
    for k in range(0, len(ids), 80):
        vals = ' '.join('"%s"' % i for i in ids[k:k + 80])
        for b in sparql('SELECT ?id ?item ?article ?mbid WHERE { VALUES ?id { %s } ?item wdt:%s ?id . '
                        'OPTIONAL { ?item wdt:P434 ?mbid } '
                        'OPTIONAL { ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> } }' % (vals, prop)):
            out.setdefault(val(b, 'id'), {'qid': qid(val(b, 'item')), 'title': title_of(val(b, 'article')), 'mbid': val(b, 'mbid')})
    return out


def enwiki(qids):
    """English article titles for Wikidata items."""
    out, qids = {}, [q for q in qids if q]
    for k in range(0, len(qids), 50):
        j = fetch(WD + '?' + urllib.parse.urlencode({'action': 'wbgetentities', 'ids': '|'.join(qids[k:k + 50]),
                                                     'props': 'sitelinks', 'sitefilter': 'enwiki', 'format': 'json'}))
        for q, e in ((j or {}).get('entities') or {}).items():
            t = ((e.get('sitelinks') or {}).get('enwiki') or {}).get('title')
            if t:
                out[q] = t
    return out


def clean_title(t):
    t = re.sub(r'\s+-\s+.*(remaster|version|mix|edit|mono|stereo|single|live|demo|take|recording|anniversary|bonus|from).*$', '', t, flags=re.I)
    t = re.sub(r'\s*[(\[](feat|with|ft|from)\.?[^)\]]*[)\]]', '', t, flags=re.I)
    t = re.sub(r'\s*[(\[][^)\]]*(remaster|deluxe|edition|expanded|version|anniversary|mono|stereo|bonus|reissue)[^)\]]*[)\]]', '', t, flags=re.I)
    return t.strip()


def work_by(artist_q, title, types):
    """A song or album with this exact title (any case) by this artist, with an English article."""
    if not artist_q or not title:
        return None
    lit = json.dumps(title.lower())
    rows = sparql('SELECT ?item ?article WHERE { VALUES ?t { %s } ?item wdt:P175 wd:%s ; wdt:P31 ?t ; rdfs:label ?l . '
                  'FILTER(LANG(?l) = "en" && LCASE(STR(?l)) = %s) '
                  '?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> } LIMIT 3' % (types, artist_q, lit))
    return title_of(val(rows[0], 'article')) if len(rows) == 1 else None


def mb_artist(mbid):
    a = mb('artist/' + mbid + '?inc=url-rels+artist-rels')
    if not a or not a.get('id'):
        return None, None
    wd = next((qid(r['url']['resource']) for r in a.get('relations', []) if r.get('type') == 'wikidata' and r.get('url')), None)
    members = []
    for r in a.get('relations', []):
        if r.get('type') == 'member of band' and r.get('direction') == 'backward' and r.get('artist'):
            members.append({'name': r['artist']['name'], 'from': r.get('begin'), 'to': r.get('end'), 'ended': r.get('ended'),
                            'roles': r.get('attributes') or []})
    ls = a.get('life-span') or {}
    info = {k: v for k, v in {
        'mbid': a['id'], 'name': a.get('name'), 'type': a.get('type'), 'gender': a.get('gender'),
        'country': (a.get('area') or {}).get('name'), 'beginArea': (a.get('begin-area') or {}).get('name'),
        'begin': ls.get('begin'), 'end': ls.get('end'), 'ended': ls.get('ended'),
        'disambiguation': a.get('disambiguation') or None, 'members': members or None,
        'url': 'https://musicbrainz.org/artist/' + a['id']}.items() if v not in (None, '', [])}
    return info, wd


def mb_find(spotify_id, name):
    """MusicBrainz artist for a Spotify artist: by its Spotify link, else one artist with exactly this name."""
    u = mb('url?resource=' + urllib.parse.quote('https://open.spotify.com/artist/' + spotify_id, safe='') + '&inc=artist-rels')
    for r in (u or {}).get('relations', []):
        if r.get('artist'):
            return r['artist']['id'], 'spotify link'
    found = mb('artist?query=' + urllib.parse.quote('artist:"%s"' % name.replace('"', '\\"')) + '&limit=5')
    exact = [x for x in (found or {}).get('artists', []) if x.get('score', 0) >= 95 and x.get('name', '').lower() == name.lower()]
    if len(exact) == 1:
        return exact[0]['id'], 'name'
    return None, None


def page(title, kind, song_title=None):
    """Plain text of an English Wikipedia article, trimmed to the parts facts come from."""
    j = fetch(WIKI + '?' + urllib.parse.urlencode({'action': 'query', 'prop': 'extracts|revisions|pageprops', 'rvprop': 'ids',
                                                   'explaintext': 1, 'exsectionformat': 'wiki', 'redirects': 1,
                                                   'titles': title, 'format': 'json', 'formatversion': 2}))
    pages = ((j or {}).get('query') or {}).get('pages') or []
    if not pages or pages[0].get('missing') or 'disambiguation' in (pages[0].get('pageprops') or {}):
        return None
    p = pages[0]
    text = p.get('extract') or ''
    parts = re.split(r'\n(?===+ [^=]+ =+\n)', '\n' + text)
    keep, dropping = [], False
    for part in parts:
        head = re.match(r'\n?(=+) ([^=]+?) =+\n', part)
        level, name = (len(head.group(1)), head.group(2).strip()) if head else (0, '')
        if level == 2:
            dropping = bool(DROP.match(name))
        if dropping or (name and DROP.match(name)):
            continue   # a dropped section takes its subsections with it
        body = part.strip()
        if not body or re.fullmatch(r'=+ [^=]+ =+', body):
            continue
        if kind == 'album' and keep and song_title and song_title.lower() not in body.lower() \
                and not re.match(r'(background|recording|composition|production|release|music)', name, re.I):
            continue   # an album: its opening, how it was made and released, and whatever mentions the song
        keep.append(body)
    text = '\n\n'.join(keep)
    if len(text) > CAP[kind]:
        text = text[:CAP[kind]].rsplit('\n', 1)[0] + '\n[…]'
    return {'title': p['title'], 'url': 'https://en.wikipedia.org/wiki/' + urllib.parse.quote(p['title'].replace(' ', '_')),
            'rev': (p.get('revisions') or [{}])[0].get('revid'), 'text': text}


def main():
    data = {'songs': {}, 'artists': {}, 'pages': {}}
    if os.path.exists(OUT):
        with open(OUT) as f:
            data = json.load(f)
    data['about'] = ('What the song facts are written from. pages: English Wikipedia articles, CC BY-SA 4.0, '
                     'trimmed (each with its address and revision). artists[].mb: MusicBrainz (CC0). Made by _sotd/about.py.')
    songs = fetch(SONGS_URL)['songs']
    if not all('aids' in s for s in songs):
        sys.exit('songs.json has no Spotify artist ids yet (the Worker adds them)')
    todo = [s for s in songs if (data['songs'].get(s['id']) or {}).get('v') != VERSION]
    print(len(todo), 'songs to find sources for')
    if not todo:
        return
    today = datetime.date.today().isoformat()

    # Wikidata, by Spotify id: the songs, albums and artists it knows
    wd_track = by_spotify('P2207', [s['id'] for s in todo])
    wd_album = by_spotify('P2205', [s.get('alid') for s in todo])
    new_artists = sorted({a for s in todo for a in s['aids'][:2] if a not in data['artists']})
    wd_artist = by_spotify('P1902', new_artists)
    print(len(wd_track), 'songs,', len(wd_album), 'albums,', len(wd_artist), 'of', len(new_artists), 'new artists known to Wikidata by Spotify id')

    names = {}
    for s in todo:
        for a, n in zip(s['aids'][:2], s['artists'][:2]):
            names[a] = n
    for a in new_artists:
        w = wd_artist.get(a) or {}
        mbid, how = w.get('mbid'), 'wikidata' if w else None
        if not mbid:
            mbid, how2 = mb_find(a, names[a])
            how = how or how2
        info, mbq = mb_artist(mbid) if mbid else (None, None)
        q = w.get('qid') or mbq
        title = w.get('title') or (enwiki([q]).get(q) if q else None)
        e = {'name': names[a], 'qid': q, 'found': how, 'wiki': None, 'checked': today}
        if info:
            e['mb'] = info
        if title and title not in data['pages']:
            pg = page(title, 'artist')
            if pg:
                data['pages'][pg['title']] = pg
                title = pg['title']
            else:
                title = None
        e['wiki'] = title if title in data['pages'] else None
        data['artists'][a] = e
        print(' artist', names[a], '—', how or 'not found', '|', e['wiki'] or 'no article')

    for s in todo:
        t, al = clean_title(s['title']), clean_title(s.get('album') or '')
        lead = next((data['artists'][a].get('qid') for a in s['aids'][:1] if a in data['artists']), None)
        song_t = (wd_track.get(s['id']) or {}).get('title') or work_by(lead, t, SONG_TYPES)
        album_t = (wd_album.get(s.get('alid')) or {}).get('title') or (work_by(lead, al, ALBUM_TYPES) if al and al.lower() != t.lower() else None)
        e = {'v': VERSION, 'n': s['n'], 'title': s['title'], 'artist': s['artist'], 'album': s.get('album'),
             'artists': s['aids'][:2], 'song': None, 'albumPage': None, 'checked': today}
        if song_t:
            pg = data['pages'].get(song_t) or page(song_t, 'song')
            if pg:
                data['pages'][pg['title']] = pg
                e['song'] = pg['title']
        if album_t and album_t != e['song']:
            pg = page(album_t, 'album', t)
            if pg:
                data['pages'].setdefault(pg['title'], pg)
                e['albumPage'] = pg['title']
        e['run'] = (data['songs'].get(s['id']) or {}).get('run') or runinfo.this_run()   # the run that first found it (Status tab)
        if not e['run']:
            del e['run']
        data['songs'][s['id']] = e
        print(s['n'], s['title'], '—', s['artist'], '| song:', e['song'] or '-', '| album:', e['albumPage'] or '-')

    with open(OUT, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=0, separators=(',', ':'))
    have = sum(1 for e in data['songs'].values() if e['song'] or e['albumPage'] or any((data['artists'].get(a) or {}).get('wiki') for a in e['artists']))
    print(have, 'of', len(data['songs']), 'songs have at least one Wikipedia article')


if __name__ == '__main__':
    main()
