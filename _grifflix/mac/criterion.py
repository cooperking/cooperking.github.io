#!/usr/bin/env python3
"""Marks John's Criterion spreadsheet with what's in Grifflix: column E "Yes" for each film in Movies (by its folder,
"Title (Year)"), "No" for one that isn't (so a film taken out is marked again); column F whether its folder's "Other"
holds extras.

  python3 ~/Grifflix/criterion.py         what it would mark (nothing changes)
  python3 ~/Grifflix/criterion.py go      marks the sheet

The first time: it asks for the spreadsheet's id (the long code in its address, between /d/ and /edit) and keeps it in
~/Grifflix/criterion.json, and it signs in to Google once (open the link it prints, allow, then paste the address the
browser ends on, even though that page doesn't load). Its sign-in and the Google app file (client_secret_….json) stay in
the old scripts folder on the G-Drive. Needs: python3 -m pip install --user google-api-python-client google-auth-oauthlib

(Updated from scripts/criterion.py: John, 12 Nov 2026.)
"""
import json, os, pickle, re, sys, unicodedata, urllib.parse

GO = len(sys.argv) > 1 and sys.argv[1] == 'go'
BASE = os.environ.get('GRIFFLIX_MEDIA') or '/Volumes/G-Drive/Grifflix'
MOVIES = os.path.join(BASE, 'Movies')
KEYS = os.path.join(BASE, 'scripts')   # the Google sign-in (token.pickle) and app file, where the old script kept them
CONF = os.path.expanduser('~/Grifflix/criterion.json')
RANGE = 'Criterion Films!C2:F'
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


def main():
    if not os.path.isdir(MOVIES):
        sys.exit('No %s: is the G-Drive plugged in?' % MOVIES)
    folders = {}
    for f in os.scandir(MOVIES):
        if f.is_dir() and not f.name.startswith('.'):
            folders.setdefault(key(f.name), f.name)
    sid = sheet_id()
    sheet = google()
    rows = sheet.values().get(spreadsheetId=sid, range=RANGE).execute().get('values', [])
    updates, have, extras, changed = [], 0, 0, []
    for i, row in enumerate(rows, 2):
        if len(row) < 2 or not row[0].strip():
            continue
        title, year = row[0].strip(), row[1].strip()
        folder = folders.get(key('%s (%s)' % (title, year)))
        if not folder and re.match(r'^\d{4}$', year):   # (a folder a year out: release dates differ between sources)
            folder = next((folders.get(key('%s (%d)' % (title, int(year) + d))) for d in (1, -1) if folders.get(key('%s (%d)' % (title, int(year) + d)))), None)
        e = 'Yes' if folder else 'No'
        f = 'No'
        if folder:
            have += 1
            other = os.path.join(MOVIES, folder, 'Other')
            if os.path.isdir(other) and any(not x.name.startswith('.') and not x.name.endswith('.txt') for x in os.scandir(other) if x.is_file()):
                f = 'Yes'; extras += 1
        was = (row[2] if len(row) > 2 else '', row[3] if len(row) > 3 else '')
        if was != (e, f):   # (only what's changed)
            updates.append({'range': 'Criterion Films!E%d:F%d' % (i, i), 'values': [[e, f]]})
            changed.append('%s (%s): %s%s' % (title, year, 'in Grifflix' if folder else 'not in Grifflix', ', with extras' if f == 'Yes' else ''))
    print('%d Criterion films on the sheet; %d in Grifflix, %d of them with extras.' % (len(rows), have, extras))
    for c in changed[:60]:
        print('  ' + c)
    if len(changed) > 60:
        print('  … and %d more' % (len(changed) - 60))
    if not updates:
        return print('The sheet is up to date.')
    if not GO:
        return print('\n%d row(s) would change: run with go to mark them.' % len(updates))
    r = sheet.values().batchUpdate(spreadsheetId=sid, body={'valueInputOption': 'RAW', 'data': updates}).execute()
    print('\nMarked %d row(s) (%d cells).' % (len(updates), r.get('totalUpdatedCells', 0)))


if __name__ == '__main__':
    main()
