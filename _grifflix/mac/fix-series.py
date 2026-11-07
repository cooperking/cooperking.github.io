#!/usr/bin/env python3
"""Puts right series Jellyfin matched wrongly or without pictures, by matching them again by name and year, and takes
away empty "untitled folder"s in the TV and film folders (John, 7 Nov 2026: The Curse (2023) shown as The Curse of Oak
Island, IT: Welcome to Derry with no picture, an empty untitled folder).

  python3 fix-series.py        what it would do (nothing changes)
  python3 fix-series.py go     does it

Only Jellyfin's details for these series change (name, pictures, description); the files are not touched, and only
folders with nothing in them (but a .DS_Store) are removed.
"""
import json, os, re, sys, urllib.parse, urllib.request

GO = len(sys.argv) > 1 and sys.argv[1] == 'go'
APP = os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix')
s = json.load(open(os.path.join(APP, 'settings.json')))
JF = s.get('jellyfin', 'http://127.0.0.1:8096')

FIX = [('thecurse', 'The Curse', 2023), ('itwelcometoderry', 'IT: Welcome to Derry', 2025)]   # (its folder's name, letters only, no year)


def call(method, path, body=None, **q):
    url = JF + path + ('?' + urllib.parse.urlencode(q) if q else '')
    r = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                               headers={'Authorization': 'MediaBrowser Token="%s"' % s['jf_token'], 'Content-Type': 'application/json'})
    raw = urllib.request.urlopen(r, timeout=120).read()
    return json.loads(raw) if raw[:1] in (b'{', b'[') else None


def g(d, k):   # (Jellyfin 10 writes Name, 12 writes name)
    return d.get(k, d.get(k[:1].lower() + k[1:]))


def folder_key(path):   # 'The Curse (2023)', 'The Curse 2023', 'IT - Welcome to Derry' -> 'thecurse', 'itwelcometoderry'
    n = re.sub(r'[\(\[]?\b(19|20)\d\d\b[\)\]]?', '', os.path.basename((path or '').rstrip('/')).lower())
    return re.sub(r'[^a-z0-9]', '', n)


series = g(call('GET', '/Users/%s/Items' % s['jf_user_id'], Recursive='true', IncludeItemTypes='Series', Fields='Path,ProviderIds,ProductionYear'), 'Items') or []
done = 0
for it in series:
    key = folder_key(g(it, 'Path'))
    if ('curse' in key or 'derry' in key) and key not in [f[0] for f in FIX]:
        print('%-40s folder %r: left as it is' % (g(it, 'Name'), os.path.basename(g(it, 'Path'))))
    for start, name, year in FIX:
        if key != start:
            continue
        have = '%s (%s)' % (g(it, 'Name'), g(it, 'ProductionYear') or '?')
        found = call('POST', '/Items/RemoteSearch/Series', {'SearchInfo': {'Name': name, 'Year': year}, 'ItemId': g(it, 'Id'), 'IncludeDisabledProviders': False}) or []
        pick = [r for r in found if g(r, 'ProductionYear') == year] or found
        if not pick:
            print('%-40s folder %r: nothing found for %s (%d)' % (have, os.path.basename(g(it, 'Path')), name, year))
            break
        r = pick[0]
        print('%-40s folder %r  ->  %s (%s)%s' % (have, os.path.basename(g(it, 'Path')), g(r, 'Name'), g(r, 'ProductionYear'), '' if GO else '  (not yet: add go)'))
        if GO:
            call('POST', '/Items/RemoteSearch/Apply/%s' % g(it, 'Id'), r, ReplaceAllImages='true')
            done += 1
        break

# empty "untitled folder"s, in the libraries' folders
roots = []
for vf in call('GET', '/Library/VirtualFolders') or []:
    roots += g(vf, 'Locations') or []
gone = 0
for root in roots:
    for dirpath, dirs, files in os.walk(root, topdown=False):
        if not os.path.basename(dirpath).lower().startswith('untitled folder'):
            continue
        if [f for f in os.listdir(dirpath) if f != '.DS_Store']:
            print('kept (not empty): ' + dirpath)
            continue
        print(('removed: ' if GO else 'would remove: ') + dirpath)
        if GO:
            try:
                os.remove(os.path.join(dirpath, '.DS_Store'))
            except OSError:
                pass
            os.rmdir(dirpath)
            gone += 1

if GO:
    print('== %d series matched again (their pictures follow in a minute or two), %d empty folders removed.' % (done, gone))
    call('POST', '/Library/Refresh')
else:
    print('== Nothing changed yet. Run:  python3 fix-series.py go')
