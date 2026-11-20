#!/usr/bin/env python3
"""Criterion's covers, all of them, kept up to date: the Grifflix version of the old scripts/import_requests.py (John, 16 Nov 2026:
"do I have a script that adds the posters to google drive?" ... "yes" to a Grifflix version).

criterion.com asks every visitor that isn't a person's browser to wait for a check (Cloudflare), so the list itself comes from John's
own browser: Criterion's list (https://www.criterion.com/shop/browse/list?sort=spine_number), File → Save Page As (Web Page, HTML
only), then that file chosen on the admin page's Criterion card (or put here as ~/Grifflix/criterion-list.html). Its covers themselves
come from Criterion's picture store, which anyone can fetch. Then:

  * each cover not had yet is downloaded, as it is (not padded), into "Criterion covers" on the G-Drive, named as the old script did
    (the title in lower case, letters and numbers only, "_" and the year: thefreshman_1925.jpg);
  * the new ones are copied to John's Google Drive "Criterion images" too (with the downloader's own Google Drive connection);
  * new films are added to the foot of the "Criterion Films" sheet, if the Google sheet tools are set up (criterion.py's sign-in),
    without clearing it (the old script cleared it, and with it the "On Grifflix" and "Extras" columns);
  * every film of John's on Criterion's list is put in "The Criterion Collection" (a film Criterion has just added included);
  * and every Criterion film in Grifflix is given its cover (poster-add.py drive go).

  python3 ~/Grifflix/criterion-covers.py [list.html]        what it would do (nothing changes)
  python3 ~/Grifflix/criterion-covers.py [list.html] go     does it
"""
import base64, hashlib, html.parser, json, shutil, os, re, subprocess, sys, time, unicodedata, urllib.parse, urllib.request

ARGS = sys.argv[1:]
GO = 'go' in ARGS
WANT = 'want' in ARGS       # (the Criterion pages of John's films without a synopsis yet, as JSON, for the admin page's list)
SYNONLY = 'synopses' in ARGS   # (only the synopses, from the file John's browser saved: nothing fetched)
HOME = os.path.expanduser('~/Grifflix')
# (the admin page sends the films it read from the saved page, as criterion-list.json; a saved page itself works too)
LIST = next((a for a in ARGS if a.lower().endswith(('.html', '.htm', '.json'))), None) or next(
    (p for p in (os.path.join(HOME, 'criterion-list.json'), os.path.join(HOME, 'criterion-list.html')) if os.path.exists(p)), os.path.join(HOME, 'criterion-list.html'))
BASE = os.environ.get('GRIFFLIX_MEDIA') or '/Volumes/G-Drive/Grifflix'
COVERS = os.path.join(BASE, 'Criterion covers')
RCLONE, CONF = os.path.join(HOME, 'rclone'), os.path.join(HOME, 'rclone.conf')
EXTRA = {'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'œ': 'oe', 'Œ': 'OE', 'ß': 'ss', 'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D', 'þ': 'th', 'ð': 'd'}
UA = 'Mozilla/5.0 (Grifflix; one family\'s film library)'


def plain(t):
    t = ''.join(EXTRA.get(c, c) for c in t)
    return unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode()


def name_of(title, year):   # exactly the old script's file name
    t = re.sub(r'[^a-zA-Z0-9]', '', plain(title)).lower()
    y = re.sub(r'[^a-zA-Z0-9]', '', plain(year)).lower()
    return '%s_%s.jpg' % (t, y) if y else '%s.jpg' % t


class Rows(html.parser.HTMLParser):
    """Criterion's list: each <tr class="gridFilm" data-href=…> with its cells g-spine, g-img (its <img>), g-title, g-director,
    g-country, g-year."""
    def __init__(self):
        super().__init__()
        self.films, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get('class') or '').split()
        if tag == 'tr' and 'gridFilm' in cls:
            self.row = {'link': a.get('data-href') or '', 'spine': '', 'img': '', 'title': '', 'director': '', 'country': '', 'year': ''}
        elif self.row is not None and tag == 'td':
            self.cell = next((c[2:] for c in cls if c.startswith('g-')), None)
        elif self.row is not None and tag == 'img' and self.cell == 'img':
            self.row['img'] = a.get('src') or a.get('data-src') or ''

    def handle_endtag(self, tag):
        if tag == 'td':
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            r = {k: re.sub(r'\s+', ' ', v).strip().rstrip(',') for k, v in self.row.items()}
            if r['title']:
                self.films.append(r)
            self.row = None

    def handle_data(self, d):
        if self.row is not None and self.cell in ('spine', 'title', 'director', 'country', 'year'):
            self.row[self.cell] += d


def big(url):   # the cover at its largest, as the old script asked for
    return re.sub(r'_(thumb|small|thumbnail)\.(jpg|png)$', r'_large.\2', url)   # (a PNG's too: The Celebration's, 16 Nov 2026)


def fetch(url):
    # (with the certificates Grifflix's other scripts use: python.org's Python on the Mac mini has none of its own, and every download
    # was refused: John, 16 Nov 2026, "Downloaded 0 cover(s)"; else the Mac's own curl)
    try:
        import certifi, ssl
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = None
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=60, context=ctx).read()
    except Exception:
        return subprocess.run(['curl', '-fsSL', '-A', UA, '--max-time', '60', url], capture_output=True, check=True).stdout


def sheet_rows(films):
    """The films not on the sheet yet, added at its foot (nothing cleared); only if criterion.py's Google sign-in is set up."""
    try:
        sys.path.insert(0, HOME)
        import importlib.util
        spec = importlib.util.spec_from_file_location('criterion', os.path.join(HOME, 'criterion.py'))
        cr = importlib.util.module_from_spec(spec)
        sys.argv = [sys.argv[0]]   # (criterion.py reads its own arguments when loaded)
        spec.loader.exec_module(cr)
    except SystemExit as e:
        return print('\nThe sheet: not updated (%s).' % e)
    except Exception as e:
        return print('\nThe sheet: not updated (%s).' % e)
    if not os.path.exists(os.path.join(cr.KEYS, 'token.pickle')) or not os.path.exists(cr.CONF):
        return print('\nThe sheet: not updated (run criterion.py once first, to sign in to Google).')
    try:
        sid, sheet = cr.sheet_id(), cr.google()
    except SystemExit as e:
        return print('\nThe sheet: not updated (%s).' % e)
    rows = sheet.values().get(spreadsheetId=sid, range='Criterion Films!C2:D').execute().get('values', [])
    have = set(cr.key('%s %s' % (r[0], r[1] if len(r) > 1 else '')) for r in rows if r)
    new = [f for f in films if cr.key('%s %s' % (f['title'], f['year'])) not in have]
    if not new:
        print('\nThe sheet: every film on it already.')
        return mark(cr, sid, sheet)
    print('\nThe sheet: %d film(s) to add at its foot%s' % (len(new), '' if GO else ' (run with go)'))
    for f in new[:30]:
        print('  %s %s (%s)' % (f['spine'] or '—', f['title'], f['year']))
    if not GO:
        return
    vals = [[f['spine'], '=IMAGE("%s")' % f['img'] if f['img'] else '', f['title'], f['year'], '', '', f['director'], f['country'],
             '=HYPERLINK("%s", "Link")' % f['link'] if f['link'] else ''] for f in new]
    sheet.values().append(spreadsheetId=sid, range='Criterion Films!A:I', valueInputOption='USER_ENTERED', insertDataOption='INSERT_ROWS',
                          body={'values': vals}).execute()
    print('  added')
    mark(cr, sid, sheet)


def mark(cr, sid, sheet):   # (which are in Grifflix, and which have extras, marked on the sheet: criterion.py's own marking)
    print('\nThe sheet\'s "in Grifflix" and "extras":')
    try:
        cr.mark(sid, sheet, GO)
    except Exception as e:
        print('  not marked (%s)' % e)


REPLACED = []   # (covers that had come as small previews, fetched again in full)
IN_LIB = {}   # (the film folders on Criterion's list → their cover's file name)
PARTS = {}    # (a film's parts' folders → its cover: poster-add.py can't match those by name, so put on here)
SPINES = {}   # (folder → spine, kept for beyond(): a TV series' spine too)
APPLIED = os.path.join(HOME, 'criterion-applied.json')   # (which cover each set, series and part was given, so it's not given again every time)


def collect():
    """Every film of John's that's on Criterion's list put in "The Criterion Collection" (John, 16 Nov 2026: "occasionally I already have
    films that have been added to the criterion collection. They need the poster added and it should be added to the collection. The
    Shop Around the Corner is a recent example"). Only added, never taken out."""
    try:
        S = json.load(open(os.path.join(os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix'), 'settings.json')))
        def jf(method, path, **q):
            u = S.get('jellyfin', 'http://127.0.0.1:8096') + path + ('?' + urllib.parse.urlencode(q) if q else '')
            raw = urllib.request.urlopen(urllib.request.Request(u, method=method, headers={'Authorization': 'MediaBrowser Token="%s"' % S['jf_token']}), timeout=120).read()
            return json.loads(raw) if raw[:1] in (b'{', b'[') else None
        g = lambda d, k: d.get(k, d.get(k[:1].lower() + k[1:]))
        sets = g(jf('GET', '/Users/%s/Items' % S['jf_user_id'], Recursive='true', IncludeItemTypes='BoxSet'), 'Items') or []
        box = next((b for b in sets if (g(b, 'Name') or '').strip().lower() == 'the criterion collection'), None)
        if not box:
            return print('\nThe Criterion Collection: no collection by that name in Grifflix.')
        inbox = g(jf('GET', '/Users/%s/Items' % S['jf_user_id'], ParentId=g(box, 'Id'), Fields='Path'), 'Items') or []
        held = set(g(i, 'Id') for i in inbox)
        # (a film's extras, in its "Other" folder, aren't films: the media server lists them as films, and the first run put 13 of them in
        # the collection, Ace in the Hole's interviews and Crash's trailers: taken out again)
        EXTRA_DIRS = ('other', 'extras', 'featurettes', 'behind the scenes', 'deleted scenes', 'interviews', 'trailers', 'shorts')
        isextra = lambda p: any(seg.lower() in EXTRA_DIRS for seg in (p or '').split('/')[:-1])
        out_ = [i for i in inbox if isextra(g(i, 'Path'))]
        if out_:
            print('\nThe Criterion Collection: %d extra(s) to take out%s: %s' % (len(out_), '' if GO else ' (run with go)', ', '.join(g(i, 'Name') or '?' for i in out_[:20])))
            if GO:
                jf('DELETE', '/Collections/%s/Items' % g(box, 'Id'), Ids=','.join(g(i, 'Id') for i in out_))
        movies, lid, start, by = os.path.join(BASE, 'Movies'), (S.get('libraries') or {}).get('movies'), 0, {}
        while lid:
            r = jf('GET', '/Users/%s/Items' % S['jf_user_id'], ParentId=lid, Recursive='true', IncludeItemTypes='Movie', Fields='Path', StartIndex=start, Limit=500)
            for it in g(r, 'Items') or []:
                rel = os.path.relpath(g(it, 'Path') or '/', movies)
                parts = rel.split(os.sep)
                if not rel.startswith('..') and len(parts) >= 2 and not (len(parts) > 2 and parts[1].lower() in EXTRA_DIRS):   # (the film, not its extras)
                    by.setdefault(parts[0], []).append((g(it, 'Id'), g(it, 'Name')))
            start += 500
            if start >= (g(r, 'TotalRecordCount') or 0):
                break
        # (only a film with its proper Criterion cover here goes in: John, 16 Nov 2026, "it's fine if they don't have a spine number but
        # they must have a proper criterion image to go in the collection")
        real = lambda c: any(os.path.exists(os.path.join(COVERS, x)) and os.path.getsize(os.path.join(COVERS, x)) > 5000 for x in (c, c[:-4] + '.png'))
        nocover = sorted(f for f, c in IN_LIB.items() if not real(c) and any(i not in held for i, _ in by.get(f, [])))
        add = [(i, n) for f in sorted(IN_LIB) if real(IN_LIB[f]) for i, n in by.get(f, []) if i not in held]
        print('\nThe Criterion Collection: %d film(s) to put in%s' % (len(add), '' if GO or not add else ' (run with go)'))
        for _, n in add[:40]:
            print('  ' + (n or '?'))
        if nocover:
            print('  (not put in, no Criterion cover yet: %s)' % ', '.join(nocover[:20]))
        if GO and add:
            for n in range(0, len(add), 50):
                jf('POST', '/Collections/%s/Items' % g(box, 'Id'), Ids=','.join(i for i, _ in add[n:n + 50]))
            try:   # (Grifflix looks again)
                urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%s/local/remap' % S.get('port', 8097), data=b'{"map": {}}', method='POST',
                                       headers={'X-Grifflix-Secret': S['secret'], 'Content-Type': 'application/json'}), timeout=30).read()
            except Exception:
                pass
    except Exception as e:
        print('\nThe Criterion Collection: not changed (%s).' % e)


def has_film(folder):   # (as poster-add.py: a film in it, not only extras)
    for d, dirs, files in os.walk(folder):
        dirs[:] = [x for x in dirs if x.lower() not in ('other', 'extras', 'featurettes', 'trailers', 'interviews', 'shorts') and not x.startswith('.')]
        if any(f.lower().endswith(('.mp4', '.mkv', '.m4v', '.mov', '.avi', '.ts', '.wmv', '.mpg', '.mpeg', '.webm')) and not f.startswith('.') for f in files):
            return True
    return False


SYN = os.path.join(HOME, 'criterion-synopses.json')   # (each film's Criterion synopsis, by its Criterion page, and which films were given it)


def synopsis_of(page):
    """A Criterion film page's own synopsis: its summary paragraphs, else its description."""
    import html as H
    m = re.search(r'<div[^>]*class="[^"]*product-summary[^"]*"[^>]*>(.*?)</div>', page, re.S | re.I)
    txt = ''
    if m:
        paras = [re.sub(r'\s+', ' ', H.unescape(re.sub(r'<[^>]+>', '', p_))).strip() for p_ in re.findall(r'<p[^>]*>(.*?)</p>', m.group(1), re.S | re.I)]
        txt = '\n\n'.join(x for x in paras if x)
    if not txt:
        m = re.search(r'<meta[^>]+(?:property="og:description"|name="description")[^>]+content="([^"]*)"', page, re.I) or \
            re.search(r'<meta[^>]+content="([^"]*)"[^>]+(?:property="og:description"|name="description")', page, re.I)
        txt = re.sub(r'\s+', ' ', H.unescape(m.group(1))).strip() if m else ''
    return txt if len(txt) > 40 else ''


def synopses(films):
    """Each Criterion film in Grifflix given Criterion's own synopsis, from its page on criterion.com, in place of the media server's
    (John, 16 Nov 2026: "Can the criterion films synopsis be replaced by the criterion page's own synopsis for the films"); kept from
    being changed back (the media server's Overview locked). Each page read once, gently. criterion.com puts some visitors behind a
    check (Cloudflare's "Just a moment…"): if it does so to the Mac mini, this stops at once and says so; it never tries to get past it."""
    try:
        Sx = json.load(open(os.path.join(os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix'), 'settings.json')))
        def jf(method, path, body=None, **q):
            u = Sx.get('jellyfin', 'http://127.0.0.1:8096') + path + ('?' + urllib.parse.urlencode(q) if q else '')
            raw = urllib.request.urlopen(urllib.request.Request(u, data=json.dumps(body).encode() if body is not None else None, method=method,
                                         headers={'Authorization': 'MediaBrowser Token="%s"' % Sx['jf_token'], 'Content-Type': 'application/json'}), timeout=120).read()
            return json.loads(raw) if raw[:1] in (b'{', b'[') else None
        g = lambda d, k_: d.get(k_, d.get(k_[:1].lower() + k_[1:]))
        try:
            S_ = json.load(open(SYN))
        except Exception:
            S_ = {}
        pages, given = S_.setdefault('pages', {}), S_.setdefault('given', {})
        want, byc = {}, {}   # (folder → its Criterion page; each cover's folders, looked up once, not every film against every folder)
        for folder, cover in IN_LIB.items():
            byc.setdefault(cover, []).append(folder)
        for f in films:
            if f.get('link', '').startswith('https://www.criterion.com/films/'):
                for folder in byc.get(name_of(f['title'], f['year']), []):
                    want.setdefault(folder, f['link'])
        if not want:
            return
        movies, uid, by = os.path.join(BASE, 'Movies'), Sx['jf_user_id'], {}
        r_ = jf('GET', '/Users/%s/Items' % uid, ParentId=(Sx.get('libraries') or {}).get('movies'), Recursive='true', IncludeItemTypes='Movie', Fields='Path')
        for it in g(r_, 'Items') or []:
            rel = os.path.relpath(g(it, 'Path') or '/', movies).split(os.sep)
            if len(rel) >= 2 and not (len(rel) > 2 and rel[1].lower() in ('other', 'extras', 'featurettes', 'trailers', 'interviews', 'shorts')):
                by.setdefault(rel[0], []).append(g(it, 'Id'))
        todo = [(fo, l) for fo, l in sorted(want.items()) if l not in pages]
        if WANT:
            print(json.dumps({'want': sorted(set(l.replace('https://www.criterion.com/films/', '') for fo, l in want.items() if not pages.get(l))), 'have': sum(1 for l in set(want.values()) if pages.get(l))}))
            return
        if SYNONLY:
            todo = []
        print('\nCriterion’s synopses: %d film(s) to read from criterion.com%s' % (len(todo), '' if GO else ' (run with go)'))
        blocked, fails = False, 0
        if GO:
            for n, (fo, link) in enumerate(todo):
                try:
                    page = fetch(link).decode('utf-8', 'replace')
                    fails = 0
                except Exception:
                    fails += 1
                    if fails >= 3:   # (three refused in a row: it isn't letting the Mac mini in)
                        blocked = True
                        break
                    time.sleep(3)
                    continue
                if 'Just a moment' in page[:3000] or 'challenges.cloudflare.com' in page[:6000]:
                    blocked = True
                    break
                pages[link] = synopsis_of(page)
                if (n + 1) % 50 == 0:
                    print('  %d of %d…' % (n + 1, len(todo)), flush=True)
                    with open(SYN + '.tmp', 'w') as fh:
                        json.dump(S_, fh)
                    os.replace(SYN + '.tmp', SYN)
                time.sleep(1.5)   # (gently: one page every second and a half)
            if blocked:
                print('  criterion.com asked the Mac mini to prove it’s a person (its “Just a moment…” check), so it stopped there: the synopses it has are used.')
        put = 0
        for fo, link in want.items():
            txt = pages.get(link)
            if not txt:
                continue
            for iid in by.get(fo, []):
                sig = hashlib.sha1(txt.encode()).hexdigest()[:12]
                if given.get(iid) == sig:
                    continue
                if GO:
                    full = jf('GET', '/Users/%s/Items/%s' % (uid, iid)) or {}
                    if not full:
                        continue
                    full['Overview'] = txt
                    full['LockedFields'] = sorted(set((g(full, 'LockedFields') or []) + ['Overview']))
                    jf('POST', '/Items/%s' % iid, full)
                    given[iid] = sig
                put += 1
        print('  %d film(s) given Criterion’s synopsis%s.' % (put, '' if GO else ' (run with go)'))
        if GO:
            with open(SYN + '.tmp', 'w') as fh:
                json.dump(S_, fh)
            os.replace(SYN + '.tmp', SYN)
            if put:
                try:
                    urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%s/local/remap' % Sx.get('port', 8097), data=b'{"map": {}}', method='POST',
                                           headers={'X-Grifflix-Secret': Sx['secret'], 'Content-Type': 'application/json'}), timeout=30).read()
                except Exception:
                    pass
    except Exception as e:
        print('\nCriterion’s synopses: not done (%s).' % e)


def cover_path(c):
    for x in (c, c[:-4] + '.png'):
        p = os.path.join(COVERS, x)
        if os.path.exists(p) and os.path.getsize(p) > 5000:
            return p
    return None


def beyond(films):
    """What poster-add.py and collect() can't do by a film's folder name (John, 16 Nov 2026: "Do the fix to the script for anything where
    it's a bit more complicated, such as sets and part one and part two - tv series are in the TV folder but are in the criterion
    collection collection"):
      * a set Grifflix has as a box set ("Martha Graham: Dance on Film"), by its name: the set its cover, and in The Criterion Collection;
      * a TV series on Criterion's list ("Tanner '88", "Fishing with John"), by its name and year: its cover (and its folder's poster.jpg),
        its spine number, and in The Criterion Collection;
      * a film in parts ("Che" as Che Part One and Che Part Two): each part its cover.
    Only with a proper Criterion cover; each given once (criterion-applied.json)."""
    try:
        Sx = json.load(open(os.path.join(os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix'), 'settings.json')))
        def jf(method, path, data=None, ctype='application/json', **q):
            u = Sx.get('jellyfin', 'http://127.0.0.1:8096') + path + ('?' + urllib.parse.urlencode(q) if q else '')
            raw = urllib.request.urlopen(urllib.request.Request(u, data=data, method=method, headers={'Authorization': 'MediaBrowser Token="%s"' % Sx['jf_token'],
                                         'Content-Type': ctype}), timeout=120).read()
            return json.loads(raw) if raw[:1] in (b'{', b'[') else None
        g = lambda d, k_: d.get(k_, d.get(k_[:1].lower() + k_[1:]))
        k = lambda t: (lambda x: x[3:] if x.startswith('the') and len(x) > 7 else x)(re.sub(r'[^a-z0-9]', '', plain(t or '').lower()))
        uid = Sx['jf_user_id']
        try:
            done = json.load(open(APPLIED))
        except Exception:
            done = {}
        sets = g(jf('GET', '/Users/%s/Items' % uid, Recursive='true', IncludeItemTypes='BoxSet'), 'Items') or []
        box = next((b for b in sets if (g(b, 'Name') or '').strip().lower() == 'the criterion collection'), None)
        held = set(g(i, 'Id') for i in g(jf('GET', '/Users/%s/Items' % uid, ParentId=g(box, 'Id')), 'Items') or []) if box else set()
        series = g(jf('GET', '/Users/%s/Items' % uid, Recursive='true', IncludeItemTypes='Series', Fields='Path,ProductionYear'), 'Items') or []
        bysets = {k(g(b, 'Name')): b for b in sets if box is None or g(b, 'Id') != g(box, 'Id')}
        byser = {}
        for x in series:
            byser.setdefault(k(re.sub(r'\s*\((19|20)\d\d\)\s*$', '', g(x, 'Name') or '')), []).append(x)
        todo = []   # (what, item, cover, folder for its poster.jpg, spine)
        for f in films:
            c = name_of(f['title'], f['year'])
            b = bysets.get(k(f['title']))
            if b:
                todo.append(('set', b, c, None, None))
            # (a series: by a film's own entry, never a box set's, which has no year: Fanny and Alexander's set cover went on its TV version;
            # and a "…: Television Version" entry is the series' own: John, 18 Nov 2026)
            if '/boxsets/' in (f.get('link') or ''):
                continue
            tv = re.sub(r'\s*[:(\[-]\s*(the\s+)?(television|tv)(\s+(version|cut|series))?\s*[)\]]?\s*$', '', f['title'], flags=re.I)
            for x in byser.get(k(tv), []):
                yr = g(x, 'ProductionYear')
                if tv == f['title'] and any(k(tv) == k(o['title'].split(':')[0]) and re.search(r'(television|tv)\b', o['title'], re.I) for o in films if o is not f):
                    continue   # (the film of the same name, when Criterion has the series as its own entry)
                if yr and re.match(r'^\d{4}$', f['year']) and abs(int(yr) - int(f['year'])) <= 1:
                    todo.append(('series', x, c, g(x, 'Path'), re.sub(r'[^0-9]', '', f['spine'])))
        movies = os.path.join(BASE, 'Movies')
        if PARTS:
            r_ = jf('GET', '/Users/%s/Items' % uid, ParentId=(Sx.get('libraries') or {}).get('movies'), Recursive='true', IncludeItemTypes='Movie', Fields='Path')
            for it in g(r_, 'Items') or []:
                rel = os.path.relpath(g(it, 'Path') or '/', movies).split(os.sep)
                if len(rel) == 2 and rel[0] in PARTS:   # (the part itself, not its extras)
                    todo.append(('part', it, PARTS[rel[0]], os.path.join(movies, rel[0]), None))
        spines_add, put, add, nocover = {}, [], [], []
        for what, it, c, folder, sp in todo:
            pth, iid, nm = cover_path(c), g(it, 'Id'), g(it, 'Name')
            if not pth:
                nocover.append(nm)
                continue
            if what == 'series' and sp and folder:
                spines_add[os.path.basename(folder.rstrip('/'))] = int(sp)
            if what in ('set', 'series') and box and iid not in held:
                add.append((iid, nm))
            sig = '%s %d' % (os.path.basename(pth), os.path.getsize(pth))
            if done.get(iid) == sig:
                continue
            put.append('%s (%s)' % (nm, what))
            if GO:
                pic = open(pth, 'rb').read()
                if folder and what in ('series', 'part'):
                    dest = os.path.join(folder, 'poster.png' if pth.endswith('.png') else 'poster.jpg')
                    for o in ('poster.jpg', 'poster.jpeg', 'poster.png', 'folder.jpg', 'folder.png'):
                        if os.path.join(folder, o) != dest and os.path.exists(os.path.join(folder, o)):
                            os.remove(os.path.join(folder, o))
                    shutil.copyfile(pth, dest)
                jf('POST', '/Items/%s/Images/Primary' % iid, base64.b64encode(pic), 'image/png' if pth.endswith('.png') else 'image/jpeg')
                done[iid] = sig
        print('\nSets, TV series and films in parts: %d cover(s) to put on%s' % (len(put), '' if GO or not put else ' (run with go)'))
        for x in put[:40]:
            print('  ' + x)
        if add:
            print('  in The Criterion Collection too: %s' % ', '.join(n for _, n in add))
        if nocover:
            print('  (no Criterion cover yet: %s)' % ', '.join(sorted(set(nocover))[:20]))
        if GO:
            if add:
                jf('POST', '/Collections/%s/Items' % g(box, 'Id'), Ids=','.join(i for i, _ in add))
            with open(APPLIED + '.tmp', 'w') as fh:
                json.dump(done, fh)
            os.replace(APPLIED + '.tmp', APPLIED)
            if spines_add:   # (a series' spine number, beside the films')
                dest = os.path.join(HOME, 'criterion-spines.json')
                try:
                    allsp = json.load(open(dest))
                except Exception:
                    allsp = {}
                allsp.update(spines_add)
                with open(dest + '.tmp', 'w') as fh:
                    json.dump(allsp, fh, indent=1, sort_keys=True)
                os.replace(dest + '.tmp', dest)
            if put or add:
                try:
                    urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%s/local/remap' % Sx.get('port', 8097), data=b'{"map": {}}', method='POST',
                                           headers={'X-Grifflix-Secret': Sx['secret'], 'Content-Type': 'application/json'}), timeout=30).read()
                except Exception:
                    pass
    except Exception as e:
        print('\nSets, TV series and films in parts: not done (%s).' % e)


def spines(films):
    """Each Criterion film in Grifflix its spine number, by its folder (a year out allowed, as criterion.py): ~/Grifflix/criterion-spines.json,
    which Grifflix's gateway reads, for the site's Spine number sort (John, 16 Nov 2026)."""
    movies = os.path.join(BASE, 'Movies')
    k = lambda t: (lambda x: x[3:] if x.startswith('the') and len(x) > 7 else x)(re.sub(r'[^a-z0-9]', '', plain(t).lower()))
    folders = {}
    for f in os.listdir(movies):
        if not f.startswith('.') and os.path.isdir(os.path.join(movies, f)) and has_film(os.path.join(movies, f)):   # (not a folder left empty)
            folders.setdefault(k(f), f)
    parts_index = []   # (folders whose names say part, volume or chapter, with their years: the only ones a film in parts can be)
    for fk, fn in folders.items():
        if re.search(r'(part|vol|chapter)', fk):
            y = re.search(r'\((\d{4})\)\s*$', fn)
            parts_index.append((fk, fn, int(y.group(1)) if y else 0))
    # (and by its TMDB number, where its folder is named otherwise: a film fixed to its English title, The Beloved Child, or I Play at Being a
    # Married Woman, is Criterion's L’enfant aimé ou Je joue à être une femme mariée: John, 20 Nov 2026)
    TF = {}
    try:
        TC = json.load(open(os.path.expanduser('~/Grifflix/criterion-tmdb.json')))
        Sx = json.load(open(os.path.join(os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix'), 'settings.json')))
        lid, start = (Sx.get('libraries') or {}).get('movies'), 0
        while lid:
            u = Sx.get('jellyfin', 'http://127.0.0.1:8096') + '/Users/%s/Items?' % Sx['jf_user_id'] + urllib.parse.urlencode({'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path,ProviderIds', 'StartIndex': start, 'Limit': 500})
            r = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={'Authorization': 'MediaBrowser Token="%s"' % Sx['jf_token']}), timeout=120).read())
            for it in r.get('Items') or []:
                tid, rel = str((it.get('ProviderIds') or {}).get('Tmdb') or ''), os.path.relpath(it.get('Path') or '/', movies).split(os.sep)
                if tid and len(rel) >= 2 and not rel[0].startswith('..') and rel[0] in folders.values():
                    TF.setdefault(tid, rel[0])
            start += 500
            if start >= (r.get('TotalRecordCount') or 0):
                break
    except Exception:
        TC = {}
    tmdb_of = lambda f: str((TC.get('%s|%s' % (re.sub(r'[^a-zA-Z0-9]', '', plain(f['title'])).lower(), re.sub(r'[^0-9]', '', f['year'] or ''))) or [0])[0] or '')
    out = {}
    for f in films:
        if not f['title']:
            continue
        hit = folders.get(k('%s (%s)' % (f['title'], f['year']))) or TF.get(tmdb_of(f))
        if not hit and re.match(r'^\d{4}$', f['year']):
            hit = next((folders[k('%s (%d)' % (f['title'], int(f['year']) + d))] for d in (1, -1) if k('%s (%d)' % (f['title'], int(f['year']) + d)) in folders), None)
        hits = [hit] if hit else []
        if not hit and re.match(r'^\d{4}$', f['year']):   # (a film in parts, its folders "Che Part One (2008)", "Che Part Two (2009)": each part the cover)
            tk = k(f['title'])   # (once per film: it had been worked out again for every folder, two million times, minutes on the old Mac)
            if len(tk) >= 3:
                for pre in (tk + 'part', tk + 'vol', tk + 'chapter'):
                    for fk, fn, fy in parts_index:
                        if fk.startswith(pre) and fy and abs(fy - int(f['year'])) <= 2:
                            hits.append(fn)
                            PARTS[fn] = name_of(f['title'], f['year'])
        if not hits:
            continue
        hit = hits[0]
        for h in hits[1:]:
            IN_LIB.setdefault(h, name_of(f['title'], f['year']))
            sp = re.sub(r'[^0-9]', '', f['spine'])
            if sp:
                out[h] = int(sp)
        IN_LIB.setdefault(hit, name_of(f['title'], f['year']))   # (for the collection, with or without a spine yet)
        sp = re.sub(r'[^0-9]', '', f['spine'])
        if sp and (hit not in out or int(sp) < out[hit]):   # (a film in more than one release: its first spine)
            out[hit] = int(sp)
    SPINES.update(out)
    if WANT:
        return
    print('\nSpine numbers: %d Criterion films in Grifflix%s' % (len(out), '' if GO else ' (run with go to keep them)'))
    if GO:
        dest = os.path.join(HOME, 'criterion-spines.json')
        with open(dest + '.tmp', 'w') as fh:
            json.dump(out, fh, indent=1, sort_keys=True)
        os.replace(dest + '.tmp', dest)


def main():
    if not os.path.exists(LIST):
        sys.exit('No Criterion list: save https://www.criterion.com/shop/browse/list?sort=spine_number from your browser (File → Save Page As, '
                 'Web Page, HTML only) and choose it on the admin page’s Criterion card, or put it here as %s.' % LIST)
    if LIST.endswith('.json'):
        films = [{k: str(f.get(k) or '').strip() for k in ('link', 'spine', 'img', 'title', 'director', 'country', 'year')} for f in json.load(open(LIST))]
        films = [f for f in films if f['img'] and f['title']]
    else:
        p = Rows()
        p.feed(open(LIST, encoding='utf-8', errors='replace').read())
        films = [f for f in p.films if f['img']]
    if not films:
        sys.exit('That page has no films in it: was it Criterion’s list page, saved once it had loaded?')
    listed = films   # (spine numbers need no covers: kept from any list)
    if not any(f['img'].startswith('http') for f in films):
        spines(listed)   # (saved as "Web Page, Complete": the covers' addresses made into the browser's own copies)
        sys.exit('The covers’ addresses aren’t in that page (it was saved as “Web Page, Complete”): save it again as “Web Page, HTML Only” (Safari: “Page Source”).')
    if WANT or SYNONLY:   # (no covers: only which films, and their synopses)
        spines(listed)
        synopses(listed)
        return
    films = [f for f in films if f['img'].startswith('http')]
    if not os.path.isdir(BASE):
        sys.exit('No %s: is the G-Drive plugged in?' % BASE)
    os.makedirs(COVERS, exist_ok=True)
    have = set(os.listdir(COVERS))
    # (a cover that came as Criterion's small preview, not the picture itself, fetched again: under 60 KB, once)
    small_at = os.path.join(HOME, 'criterion-small-ok.json')
    try:
        small_ok = set(json.load(open(small_at)))
    except Exception:
        small_ok = set()
    small = set(n for n in have if n not in small_ok and os.path.getsize(os.path.join(COVERS, n)) < 60000)
    have -= small
    new = [f for f in films if name_of(f['title'], f['year']) not in have and name_of(f['title'], f['year'])[:-4] + '.png' not in have]
    print('%d films on Criterion’s list; %d covers had already, %d new.' % (len(films), len(films) - len(new), len(new)))
    for f in new[:40]:
        print('  %s (%s) → %s' % (f['title'], f['year'], name_of(f['title'], f['year'])))
    if len(new) > 40:
        print('  … and %d more' % (len(new) - 40))
    got, bad = 0, []
    if GO:
        for f in new:
            dest = os.path.join(COVERS, name_of(f['title'], f['year']))
            pic = None
            for u in (big(f['img']), f['img']):
                try:
                    pic = fetch(u)
                    if (pic[:3] == b'\xff\xd8\xff' or pic[:8] == b'\x89PNG\r\n\x1a\n') and len(pic) > 5000:   # (a few are PNGs: The Celebration)
                        break
                    pic = None
                except Exception:
                    pic = None
            if not pic:
                bad.append(f['title'])
                continue
            if pic[:4] == b'\x89PNG':
                dest = dest[:-4] + '.png'
            again = os.path.basename(dest) in small
            if again and len(pic) < 60000:   # (it really is small: kept, and not fetched again)
                small_ok.add(os.path.basename(dest))
                continue
            if again:
                REPLACED.append(os.path.basename(dest))
            with open(dest + '.part', 'wb') as fh:
                fh.write(pic)
            os.replace(dest + '.part', dest)
            got += 1
            if got % 100 == 0:
                print('  %d of %d…' % (got, len(new)), flush=True)
            time.sleep(.3)   # (gently)
        print('\nDownloaded %d cover(s).%s' % (got, (' Not got: ' + ', '.join(bad[:20])) if bad else ''))
        with open(small_at, 'w') as fh:
            json.dump(sorted(small_ok), fh)
        if REPLACED and os.path.exists(RCLONE) and os.path.exists(CONF):   # (Google Drive's small copy replaced with the real one)
            for n in REPLACED:
                subprocess.run([RCLONE, '--config', CONF, 'copyto', os.path.join(COVERS, n), 'gdrive:Criterion images/' + n], capture_output=True)
            print('Replaced the small preview with the real cover: %s' % ', '.join(REPLACED))
        if got and os.path.exists(RCLONE) and os.path.exists(CONF):   # (into Google Drive's "Criterion images" too, only those not there)
            r = subprocess.run([RCLONE, '--config', CONF, 'copy', COVERS, 'gdrive:Criterion images', '--ignore-existing', '--transfers', '8'],
                               capture_output=True, text=True)
            print('Google Drive “Criterion images”: %s' % ('up to date' if r.returncode == 0 else 'not copied (%s)' % (r.stderr or '').strip()[-200:]))
    spines(listed)
    collect()
    beyond(films)
    synopses(films)
    sheet_rows(films)
    # every Criterion film in Grifflix its cover (the ones already here, and the new)
    print('\nPutting the covers on their films…' if GO else '\nWhat poster-add.py would put on the films:')
    subprocess.run([sys.executable, os.path.join(HOME, 'poster-add.py'), 'drive', 'nofetch'] + (['go'] if GO else []))


if __name__ == '__main__':
    main()
