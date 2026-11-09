"""Each month's theme its film's music: a 30-second preview of the piece the film is remembered by (John, 9 Nov 2026:
"Can we get thirty second sound clips with each theme ... previews ... downloaded").

Finds each piece's preview on Apple's iTunes Search (Spotify's previews are no longer given out) and writes
grifflix/data/thememusic.json: { key: { title, artist, src, h } }. The page asks the Worker for /gm/<key>-<h>.m4a; the
Worker downloads the preview once from Apple into Grifflix's own storage (R2) and serves it from there, so the clips stay
off GitHub. h is the first 12 hex of the preview URL's sha256, so a stored clip always matches its source.

Run: python3 _grifflix/theme_music.py
"""
import hashlib, json, os, sys, time, urllib.error, urllib.parse, urllib.request

# theme: (search, the artist the result must name, the title it must contain[, the album it must be from]): each the piece the
# film is best known by, short where possible, so its 30 seconds are the part everyone knows (John, 9 Nov 2026)
PICKS = {
    'corridor': ('The Shining Main Title Wendy Carlos', 'carlos', 'main title'),
    'slices': ("Yumeji's Theme Shigeru Umebayashi", 'umebayashi', 'yumeji'),
    'umbrellas': ('Les Parapluies de Cherbourg Michel Legrand', 'legrand', 'parapluies'),
    'code': ('Clubbed to Death Rob Dougan', 'robert d', 'clubbed to death', ''),   # (the film's best-known piece)
    'spiral': ('Vertigo Prelude Herrmann', 'herrmann', 'vertigo: prelude', 'hitchcock: the great'),   # (the Prelude alone, Herrmann conducting: the 30 seconds can't miss it)
    'tron': ('Tron Main Title Wendy Carlos', 'carlos', 'theme from tron', ''),
    'pinboard': ('Hurdy Gurdy Man Donovan', 'donovan', 'hurdy gurdy'),
    'moon': ('Hugo Howard Shore', 'shore', 'the thief', 'hugo (original'),
    'reel': ('Cinema Paradiso Ennio Morricone', 'morricone', 'cinema paradiso'),
    'oz': ('Over the Rainbow Judy Garland Wizard of Oz', 'garland', 'over the rainbow', 'wizard of oz'),
    'table': ("We'll Meet Again Vera Lynn", 'lynn', 'meet again'),
    'globe': ('Citizen Kane Herrmann Prelude', 'herrmann', 'prelude', 'music from the 1941 motion picture'),   # (the original 1941 recording: Xanadu, Rosebud, as the film opens)
    'jaws': ('Jaws Main Title 2025 Mix John Williams', 'williams', 'main title', 'remastered'),   # (57 seconds: the shark's two notes from the start)
    'credits': ('Alphaville Paul Misraki', 'misraki', ''),
    'bars': ('Psycho The Murder Bernard Herrmann', 'herrmann', 'the murder', 'psycho'),   # (the shower: the strings everyone knows)
    'fold': ('Time Hans Zimmer Inception', 'zimmer', 'time'),
    'stairs': ('Sunset Boulevard Main Title Franz Waxman', 'waxman', ''),
    'lane': ('Tumbling Tumbleweeds Sons of the Pioneers', 'sons of the pioneers', 'tumbling tumbleweeds', 'rca country legends'),   # (the film's opening, the tumbleweed)
    'hotel': ('Mr. Moustafa Alexandre Desplat', 'desplat', 'moustafa'),
    'bedford': ("It's a Wonderful Life Main Title Dimitri Tiomkin", 'tiomkin', ''),
    'autumn': ('Late Autumn Takanobu Saito Ozu', 'saito', ''),
}


def search(q):
    u = 'https://itunes.apple.com/search?' + urllib.parse.urlencode({'term': q, 'media': 'music', 'entity': 'song', 'limit': 25, 'country': 'GB'})
    for t in (0, 20, 40, 80):   # (Apple's search refuses for a while if asked too fast)
        time.sleep(t)
        try:
            with urllib.request.urlopen(u, timeout=20) as r:
                return json.load(r).get('results', [])
        except urllib.error.HTTPError as e:
            if e.code != 403:
                raise
    raise SystemExit('Apple search keeps refusing; try again later')


def main():
    p = os.path.join(os.path.dirname(__file__), '..', 'grifflix', 'data', 'thememusic.json')
    out = json.load(open(p)) if os.path.exists(p) else {}
    for k, pk in PICKS.items():
        q, who, title, alb = (pk + ('',))[:4]
        if k in out:
            continue
        hit = None
        for r in search(q):
            if not r.get('previewUrl'):
                continue
            tn, cn = (r.get('trackName') or '').lower(), (r.get('collectionName') or '').lower()
            if who in (r.get('artistName') or '').lower() and title in tn and alb in cn and 'live' not in tn and 'interview' not in tn and 'north by' not in tn:
                hit = r
                break
        if not hit:
            print('none:', k, q, file=sys.stderr)
            time.sleep(4)
            continue
        src = hit['previewUrl']
        out[k] = {'title': hit['trackName'], 'artist': hit['artistName'], 'secs': round(hit.get('trackTimeMillis', 0) / 1000), 'src': src, 'h': hashlib.sha256(src.encode()).hexdigest()[:12]}
        print(k, '|', hit['trackName'], '|', hit['artistName'], '|', hit.get('collectionName'))
        json.dump(out, open(p, 'w'), indent=1, ensure_ascii=False, sort_keys=True)
        time.sleep(4)
    json.dump(out, open(p, 'w'), indent=1, ensure_ascii=False, sort_keys=True)
    print(len(out), 'of', len(PICKS))


if __name__ == '__main__':
    main()
