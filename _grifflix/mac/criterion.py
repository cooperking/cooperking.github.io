#!/usr/bin/env python3
"""Marks John's Criterion spreadsheet with what's in Grifflix: column E "Yes" for each film in Movies (by its spine number, or
its folder, "Title (Year)"), "No" for one that isn't (so a film taken out is marked again); column F whether its folder's
"Other" holds extras. Grifflix runs it by itself when a film comes in and every six hours (John, 16 Nov 2026: "the criterion
collection google sheet should be updating the section when it's on Grifflix, and the Extras if it's got Extras in Others").

  python3 ~/Grifflix/criterion.py         what it would mark (nothing changes)
  python3 ~/Grifflix/criterion.py go      marks the sheet

The first time: it asks for the spreadsheet's id (the long code in its address, between /d/ and /edit) and keeps it in
~/Grifflix/criterion.json, and it signs in to Google once (open the link it prints, allow, then paste the address the
browser ends on, even though that page doesn't load). Its sign-in and the Google app file (client_secret_….json) stay in
the old scripts folder on the G-Drive. Needs: python3 -m pip install --user google-api-python-client google-auth-oauthlib

(Updated from scripts/criterion.py: John, 12 Nov 2026.)
"""
import json, os, pickle, re, sys, unicodedata, urllib.parse

GO = 'go' in sys.argv[1:]
BASE = os.environ.get('GRIFFLIX_MEDIA') or '/Volumes/G-Drive/Grifflix'
MOVIES = os.path.join(BASE, 'Movies')
TV = os.path.join(BASE, 'TV')   # (a series, too: Criterion lists them as titles, Fanny and Alexander's television version, Dekalog: John, 18 Nov 2026,
# "the google spreadsheet Criterion script ... also isn't picking up TV properly")
KEYS = os.path.join(BASE, 'scripts')   # the Google sign-in (token.pickle) and app file, where the old script kept them
CONF = os.path.expanduser('~/Grifflix/criterion.json')
RANGE = 'Criterion Films!A2:F'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
EXTRA = {'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'œ': 'oe', 'Œ': 'OE', 'ß': 'ss', 'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D', 'þ': 'th', 'ð': 'd'}

try:
    from google.oauth2.credentials import Credentials  # noqa: F401
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
except ImportError:
    sys.exit('First: python3 -m pip install --user google-api-python-client google-auth-oauthlib')


def key(t):   # letters and numbers only, accents plain, lower case (as the old script matched)
    t = ''.join(EXTRA.get(c, c) for c in t)
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode().lower())


def sheet_id():
    try:
        c = json.load(open(CONF))
    except Exception:
        c = {}
    if not c.get('sheet'):
        v = input('The Criterion spreadsheet’s address (or just its id): ').strip()
        m = re.search(r'/d/([\w-]{20,})', v) or re.match(r'^([\w-]{20,})$', v)
        if not m:
            sys.exit('That isn’t a Google Sheets address.')
        c['sheet'] = m.group(1)
        os.makedirs(os.path.dirname(CONF), exist_ok=True)
        json.dump(c, open(CONF, 'w'))
    return c['sheet']


def google():
    tok = os.path.join(KEYS, 'token.pickle')
    creds = None
    if os.path.exists(tok):
        with open(tok, 'rb') as f:
            creds = pickle.load(f)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:   # (Google ends sign-ins it hasn't seen used for a while, or for apps still "in testing" after 7 days)
            print('Google’s sign-in had run out (%s): signing in again.' % str(e)[:80])
            creds = None
    if not creds or not creds.valid:
        if not sys.stdin or not sys.stdin.isatty():   # (run by Grifflix itself, with no one to sign in: said, not waited on)
            sys.exit('Google’s sign-in for the sheet needs doing again: run python3 ~/Grifflix/criterion.py on the Mac mini once.')
        secret = sorted(f for f in os.listdir(KEYS) if f.startswith('client_secret') and f.endswith('.json')) if os.path.isdir(KEYS) else []
        if not secret:
            sys.exit('No client_secret_….json in %s (the Google app file the old script used).' % KEYS)
        flow = InstalledAppFlow.from_client_secrets_file(os.path.join(KEYS, secret[0]), SCOPES)
        flow.redirect_uri = 'http://localhost'
        url, _ = flow.authorization_url(prompt='consent', access_type='offline')
        print('\nOpen this, choose your Google account and allow:\n\n%s\n' % url)
        got = input('Then paste the address the browser ends on (it starts http://localhost, the page won’t load): ').strip()
        code = urllib.parse.parse_qs(urllib.parse.urlparse(got).query).get('code', [got])[0]   # (the whole address, or just its code)
        flow.fetch_token(code=code)
        creds = flow.credentials
    with open(tok, 'wb') as f:
        pickle.dump(creds, f)
    return build('sheets', 'v4', credentials=creds, cache_discovery=False).spreadsheets()


APP = os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix')
TMDB_AT = os.path.expanduser('~/Grifflix/criterion-tmdb.json')   # (each film's TMDB number, found once: title|year -> [id, kind], or [0, when tried])


def tmdb_get(path, **q):
    """TMDB's answer, with the key John gave Grifflix (kept with the Mac's settings)."""
    import ssl, urllib.request
    try:
        k = json.load(open(os.path.join(APP, 'settings.json'))).get('tmdb_key') or ''
    except Exception:
        k = ''
    if not k:
        return None
    h = {'Accept': 'application/json', 'User-Agent': 'Grifflix'}
    if k.startswith('eyJ'):
        h['Authorization'] = 'Bearer ' + k
    else:
        q['api_key'] = k
    u = 'https://api.themoviedb.org/3' + path + '?' + urllib.parse.urlencode(q)
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=30, context=ctx) as r:
                return json.loads(r.read())
        except Exception as e:
            if getattr(e, 'code', 0) == 429:
                import time as t_
                t_.sleep(2 + attempt * 3)
                continue
            if getattr(e, 'code', 0) == 404:
                return None
            raise
    return None


def tmdb_find(title, year):
    """A Criterion film's TMDB page: the film of that title from that year (a year either side), else the series; then wider: the title
    without its "(… version)" or "set" words, any year within five, a near spelling; a box set as TMDB's collection. None if none fits
    (John, 17 Nov 2026: 191 left without a link, box sets, Small Axe's films, a re-release's year)."""
    import difflib
    y = int(year) if re.match(r'^\d{4}$', str(year or '')) else None
    want = key(title)
    KINDS = (('movie', '/search/movie', 'primary_release_year', 'title', 'release_date'), ('tv', '/search/tv', 'first_air_date_year', 'name', 'first_air_date'))
    def look(t, wide):
        w_ = key(t)
        for kind, path, yk, tk, dk in KINDS:
            for yy in ([y, y - 1, y + 1] if y and not wide else [None]):
                q = {'query': t, 'include_adult': 'false'}
                if yy:
                    q[yk] = yy
                res = (tmdb_get(path, **q) or {}).get('results') or []
                for r in res[:10]:
                    ry = int((r.get(dk) or '0')[:4] or 0)
                    names = (key(r.get(tk) or ''), key(r.get('original_' + tk) or ''))
                    near = max(difflib.SequenceMatcher(None, w_, n).ratio() for n in names) if w_ else 0
                    ok_year = not y or not ry or abs(ry - y) <= (5 if wide else 1)
                    if ok_year and (w_ in names or (wide and near >= .9 and y and ry and abs(ry - y) <= 1)):
                        return r['id'], kind
                if not wide and yy == y and y and len(res) == 1 and int((res[0].get(dk) or '0')[:4] or 0) == y:   # (one answer for that title and year)
                    return res[0]['id'], kind
        return None
    hit = look(title, False)
    if hit:
        return hit
    short = re.sub(r'\s*[\(\[][^)\]]*(version|cut|edition|restored|remastered)[^)\]]*[\)\]]', '', title, flags=re.I).strip()
    hit = look(title, True) or (look(short, True) if short != title else None)
    if hit:
        return hit
    if y:   # (the very title, years apart: a film made one year and shown years later, Criterion giving the year it was made and TMDB the year
        # it came out (The Men Who Tread on the Tiger's Tail, 1945 and 1952; Ivan the Terrible, Part II, 1946 and 1958): only if it's the one
        # film of that title within fifteen years: John, 23 Nov 2026)
        for kind, path, yk, tk, dk in KINDS:
            res = (tmdb_get(path, query=title, include_adult='false') or {}).get('results') or []
            same = [r for r in res[:20] if want in (key(r.get(tk) or ''), key(r.get('original_' + tk) or ''))
                    and int((r.get(dk) or '0')[:4] or 0) and abs(int((r.get(dk) or '0')[:4]) - y) <= 15]
            if len(same) == 1:
                return same[0]['id'], kind
    if not y or re.search(r'\b(trilogy|films|collection|complete|essential|masterpieces|set|cinema project|stories|by)\b', title, re.I):   # (a box set: TMDB's collection of it, close enough)
        res = (tmdb_get('/search/collection', query=re.sub(r'\b(the complete|the essential|films? by|two films|three films|trilogy)\b', '', title, flags=re.I).strip() or title) or {}).get('results') or []
        best = max(res[:8], key=lambda r: difflib.SequenceMatcher(None, want, key(r.get('name') or '')).ratio(), default=None)
        if best and difflib.SequenceMatcher(None, want, key(best.get('name') or '')).ratio() >= .6:
            return best['id'], 'collection'
    return None


def tmdb_links(sid, sheet, rows, go, most=600, retry=False):
    """Column J: each film's TMDB page, a link (John, 17 Nov 2026: "Can my Criterion Releases spreadsheet be updated with the tmdb
    link?"). Only rows without one; each film looked up once (and one TMDB didn't have, again after a month)."""
    import time as t_
    try:
        C = json.load(open(TMDB_AT))
    except Exception:
        C = {}
    head = sheet.values().get(spreadsheetId=sid, range='Criterion Films!J1').execute().get('values', [])
    ups, found, missing, asked = [], 0, 0, 0
    if not head or not head[0] or not str(head[0][0]).strip():
        ups.append({'range': 'Criterion Films!J1', 'values': [['TMDB']]})
    for i, row in enumerate(rows, 2):
        row = row + [''] * (10 - len(row))
        title, year = row[2].strip(), row[3].strip()
        if not title or row[9].strip():
            continue
        k_ = '%s|%s' % (key(title), year)
        c = C.get(k_)
        if c is None or (not c[0] and (retry or t_.time() - (c[1] or 0) > 30 * 86400)):
            if asked >= most:   # (at most so many looked up at once: the rest next time)
                continue
            asked += 1
            try:
                f = tmdb_find(title, year)
            except Exception as e:
                print('  TMDB: %s (%s): %s' % (title, year, str(e)[:80]))
                continue
            C[k_] = [f[0], f[1]] if f else [0, int(t_.time())]
            c = C[k_]
        if c[0]:
            ups.append({'range': 'Criterion Films!J%d' % i, 'values': [['=HYPERLINK("https://www.themoviedb.org/%s/%d", "TMDB")' % (c[1], c[0])]]})
            found += 1
        else:
            missing += 1
    try:
        json.dump(C, open(TMDB_AT, 'w'))
    except OSError:
        pass
    print('TMDB links: %d to add%s.' % (found, ', %d not found on TMDB' % missing if missing else ''))
    if ups and go:
        for n in range(0, len(ups), 500):
            sheet.values().batchUpdate(spreadsheetId=sid, body={'valueInputOption': 'USER_ENTERED', 'data': ups[n:n + 500]}).execute()
        print('Added %d TMDB link(s).' % found)
    return found


XD = ('other', 'others', 'extras', 'featurettes', 'behind the scenes', 'deleted scenes', 'interviews', 'trailers', 'shorts', 'scenes', 'clips')


def has_extras(folder):   # (its "Extras" folder (once "Other", Plex's name), or another the media server knows, holding anything but notes)
    for n in os.listdir(folder):
        if n.lower() in XD and os.path.isdir(os.path.join(folder, n)):
            for d, dirs, files in os.walk(os.path.join(folder, n)):
                if any(not x.startswith('.') and not x.endswith('.txt') for x in files):
                    return True
    return False


def mark(sid, sheet, go):
    """Columns E and F of every row brought up to date: E "Yes" when the film is in Grifflix (by its spine number, as
    criterion-covers.py found it, or by its folder's name), F "Yes" when it has extras in "Other". Only what's changed is written.
    Returns the number of rows changed."""
    folders = {}
    for root in (MOVIES, TV):
        if os.path.isdir(root):
            for f in os.scandir(root):
                if f.is_dir() and not f.name.startswith('.'):
                    folders.setdefault(key(f.name), f.path)
    by_spine = {}
    sp_ = {}
    for fn in ('criterion-spines.json', 'criterion-spines-own.json'):   # (the hand-set ones over the matched)
        try:
            sp_.update(json.load(open(os.path.expanduser('~/Grifflix/' + fn))))
        except Exception:
            pass
    for fo, sp in sp_.items():
        for root in (MOVIES, TV):
            if os.path.isdir(os.path.join(root, fo)):
                by_spine.setdefault(str(sp), []).append(os.path.join(root, fo))
    rows = sheet.values().get(spreadsheetId=sid, range=RANGE).execute().get('values', [])
    try:   # (column J: TMDB's page for each film)
        rj = sheet.values().get(spreadsheetId=sid, range='Criterion Films!A2:J').execute().get('values', [])
        tmdb_links(sid, sheet, rj, go)
    except Exception as e:
        print('TMDB links: not added (%s)' % str(e)[:120])
    updates, have, extras, changed = [], 0, 0, []
    for i, row in enumerate(rows, 2):
        row = row + [''] * (6 - len(row))
        spine, title, year = row[0].strip(), row[2].strip(), row[3].strip()
        if not title:
            continue
        found = list(by_spine.get(spine, [])) if re.match(r'^\d+$', spine) else []
        if not found:
            folder = folders.get(key('%s (%s)' % (title, year)))
            if not folder and re.match(r'^\d{4}$', year):   # (a folder a year out: release dates differ between sources)
                folder = next((folders.get(key('%s (%d)' % (title, int(year) + d))) for d in (1, -1) if folders.get(key('%s (%d)' % (title, int(year) + d)))), None)
            found = [folder] if folder else []
        e = 'Yes' if found else 'No'
        f = 'Yes' if any(has_extras(fo) or any(has_extras(os.path.join(fo, x)) for x in os.listdir(fo) if re.match(r'^(season|series)\b', x, re.I) and os.path.isdir(os.path.join(fo, x))) for fo in found) else 'No'
        have += bool(found); extras += f == 'Yes'
        if (row[4].strip(), row[5].strip()) != (e, f):   # (only what's changed)
            updates.append({'range': 'Criterion Films!E%d:F%d' % (i, i), 'values': [[e, f]]})
            changed.append('%s (%s): %s%s' % (title, year, 'in Grifflix' if found else 'not in Grifflix', ', with extras' if f == 'Yes' else ''))
    print('%d Criterion films on the sheet; %d in Grifflix, %d of them with extras.' % (len(rows), have, extras))
    for c in changed[:60]:
        print('  ' + c)
    if len(changed) > 60:
        print('  … and %d more' % (len(changed) - 60))
    if not updates:
        print('The sheet is up to date.')
        return 0
    if not go:
        print('\n%d row(s) would change: run with go to mark them.' % len(updates))
        return 0
    r = sheet.values().batchUpdate(spreadsheetId=sid, body={'valueInputOption': 'RAW', 'data': updates}).execute()
    print('\nMarked %d row(s) (%d cells).' % (len(updates), r.get('totalUpdatedCells', 0)))
    return len(updates)


def missing_sheet(rows_file):
    """python3 criterion.py missing <rows.json>: the box set films not in Grifflix put in a Google Sheet of their own, made the first time
    and brought up to date after (John, 18 Nov 2026: "can it actually create or update a google sheet that I open"). Prints its address."""
    rows = json.load(open(rows_file))
    try:
        c = json.load(open(CONF))
    except Exception:
        c = {}
    sheet = google()
    sid = c.get('missing_sheet')
    if sid:
        try:
            sheet.get(spreadsheetId=sid, fields='spreadsheetId').execute()
        except Exception:
            sid = None   # (deleted in Drive: a new one)
    if not sid:
        r = sheet.create(body={'properties': {'title': 'Grifflix: Criterion box set films not in Grifflix'},
                               'sheets': [{'properties': {'title': 'Missing', 'gridProperties': {'frozenRowCount': 1}}}]}).execute()
        sid = r['spreadsheetId']
        c['missing_sheet'] = sid
        json.dump(c, open(CONF, 'w'))
    sheet.values().clear(spreadsheetId=sid, range='Missing!A:Z').execute()
    sheet.values().update(spreadsheetId=sid, range='Missing!A1', valueInputOption='USER_ENTERED',
                          body={'values': [['Box set', 'Set spine', 'Film', 'Year', 'Spine', 'Criterion page']] + rows}).execute()
    print(json.dumps({'url': 'https://docs.google.com/spreadsheets/d/%s/edit' % sid, 'rows': len(rows)}))


def main():
    if len(sys.argv) > 2 and sys.argv[1] == 'missing':
        return missing_sheet(sys.argv[2])
    if not os.path.isdir(MOVIES):
        sys.exit('No %s: is the G-Drive plugged in?' % MOVIES)
    if 'tmdb' in sys.argv[1:]:   # (every row's TMDB link at once: python3 criterion.py tmdb go)
        sid, sheet = sheet_id(), google()
        rj = sheet.values().get(spreadsheetId=sid, range='Criterion Films!A2:J').execute().get('values', [])
        tmdb_links(sid, sheet, rj, 'go' in sys.argv[1:], most=100000, retry='retry' in sys.argv[1:])
        return
    mark(sheet_id(), google(), GO)


if __name__ == '__main__':
    main()
