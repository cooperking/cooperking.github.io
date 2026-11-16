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
  * and every Criterion film in Grifflix is given its cover (poster-add.py drive go).

  python3 ~/Grifflix/criterion-covers.py [list.html]        what it would do (nothing changes)
  python3 ~/Grifflix/criterion-covers.py [list.html] go     does it
"""
import html.parser, json, os, re, subprocess, sys, time, unicodedata, urllib.request

ARGS = sys.argv[1:]
GO = 'go' in ARGS
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
    return re.sub(r'_(thumb|small|thumbnail)\.jpg$', '_large.jpg', url)


def fetch(url):
    r = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=60)
    return r.read()


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
    sid, sheet = cr.sheet_id(), cr.google()
    rows = sheet.values().get(spreadsheetId=sid, range='Criterion Films!C2:D').execute().get('values', [])
    have = set(cr.key('%s %s' % (r[0], r[1] if len(r) > 1 else '')) for r in rows if r)
    new = [f for f in films if cr.key('%s %s' % (f['title'], f['year'])) not in have]
    if not new:
        return print('\nThe sheet: every film on it already.')
    print('\nThe sheet: %d film(s) to add at its foot%s' % (len(new), '' if GO else ' (run with go)'))
    for f in new[:30]:
        print('  %s %s (%s)' % (f['spine'] or '—', f['title'], f['year']))
    if not GO:
        return
    vals = [[f['spine'], '=IMAGE("%s")' % f['img'] if f['img'] else '', f['title'], f['year'], '', '', f['director'], f['country'],
             '=HYPERLINK("%s", "Link")' % f['link'] if f['link'] else ''] for f in new]
    sheet.values().append(spreadsheetId=sid, range='Criterion Films!A:I', valueInputOption='USER_ENTERED', insertDataOption='INSERT_ROWS',
                          body={'values': vals}).execute()
    print('  added (criterion.py go marks which are in Grifflix)')


def spines(films):
    """Each Criterion film in Grifflix its spine number, by its folder (a year out allowed, as criterion.py): ~/Grifflix/criterion-spines.json,
    which Grifflix's gateway reads, for the site's Spine number sort (John, 16 Nov 2026)."""
    movies = os.path.join(BASE, 'Movies')
    k = lambda t: (lambda x: x[3:] if x.startswith('the') and len(x) > 7 else x)(re.sub(r'[^a-z0-9]', '', plain(t).lower()))
    folders = {}
    for f in os.listdir(movies):
        if not f.startswith('.') and os.path.isdir(os.path.join(movies, f)):
            folders.setdefault(k(f), f)
    out = {}
    for f in films:
        sp = re.sub(r'[^0-9]', '', f['spine'])
        if not sp or not f['title']:
            continue
        hit = folders.get(k('%s (%s)' % (f['title'], f['year'])))
        if not hit and re.match(r'^\d{4}$', f['year']):
            hit = next((folders[k('%s (%d)' % (f['title'], int(f['year']) + d))] for d in (1, -1) if k('%s (%d)' % (f['title'], int(f['year']) + d)) in folders), None)
        if hit and (hit not in out or int(sp) < out[hit]):   # (a film in more than one release: its first spine)
            out[hit] = int(sp)
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
    if not os.path.isdir(BASE):
        sys.exit('No %s: is the G-Drive plugged in?' % BASE)
    os.makedirs(COVERS, exist_ok=True)
    have = set(os.listdir(COVERS))
    new = [f for f in films if name_of(f['title'], f['year']) not in have]
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
                    if pic[:3] == b'\xff\xd8\xff' and len(pic) > 5000:
                        break
                    pic = None
                except Exception:
                    pic = None
            if not pic:
                bad.append(f['title'])
                continue
            with open(dest + '.part', 'wb') as fh:
                fh.write(pic)
            os.replace(dest + '.part', dest)
            got += 1
            time.sleep(.3)   # (gently)
        print('\nDownloaded %d cover(s).%s' % (got, (' Not got: ' + ', '.join(bad[:20])) if bad else ''))
        if got and os.path.exists(RCLONE) and os.path.exists(CONF):   # (into Google Drive's "Criterion images" too, only those not there)
            r = subprocess.run([RCLONE, '--config', CONF, 'copy', COVERS, 'gdrive:Criterion images', '--ignore-existing', '--transfers', '8'],
                               capture_output=True, text=True)
            print('Google Drive “Criterion images”: %s' % ('up to date' if r.returncode == 0 else 'not copied (%s)' % (r.stderr or '').strip()[-200:]))
    spines(films)
    sheet_rows(films)
    # every Criterion film in Grifflix its cover (the ones already here, and the new)
    print('\nPutting the covers on their films…' if GO else '\nWhat poster-add.py would put on the films:')
    subprocess.run([sys.executable, os.path.join(HOME, 'poster-add.py'), 'drive', 'nofetch'] + (['go'] if GO else []))


if __name__ == '__main__':
    main()
