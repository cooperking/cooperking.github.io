#!/usr/bin/env python3
"""Puts right series Jellyfin matched wrongly or without pictures, by matching them again by name and year, and takes
away empty "untitled folder"s in the TV and film folders (John, 7 Nov 2026: The Curse (2023) shown as The Curse of Oak
Island, IT: Welcome to Derry with no picture, an empty untitled folder).

  python3 fix-series.py        what it would do (nothing changes)
  python3 fix-series.py go     does it

Only Jellyfin's details for these series change (name, pictures, description); the files are not touched, and only
folders with nothing in them (but a .DS_Store) are removed.
"""
import json, os, re, shutil, sys, urllib.parse, urllib.request

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


series = g(call('GET', '/Users/%s/Items' % s['jf_user_id'], Recursive='true', IncludeItemTypes='Series', Fields='Path,ProviderIds,ProductionYear,ImageTags'), 'Items') or []
done = 0


def wrong(it, key):
    """Which of FIX this series should be, if Jellyfin has it wrong: matched to another series (The Curse shown as The
    Curse of Oak Island, whatever its folder's called), or without its picture."""
    name = (g(it, 'Name') or '').lower()
    if ('oak island' in name and 'oakisland' not in key) or (key == 'thecurse' and (name != 'the curse' or g(it, 'ProductionYear') != 2023)):
        return FIX[0]
    if ('derry' in key or 'derry' in name) and (key == 'itwelcometoderry' or not (g(it, 'ImageTags') or {}).get('Primary') or 'welcome to derry' not in name):
        return FIX[1]
    return None


for it in series:
    key = folder_key(g(it, 'Path'))
    nkey = re.sub(r'[^a-z0-9]', '', (g(it, 'Name') or '').lower())
    if key and nkey and not (key.startswith(nkey[:8]) or nkey.startswith(key[:8])):   # (for you to see: named differently from its folder)
        print('%-40s its folder is %r' % (g(it, 'Name'), os.path.basename(g(it, 'Path'))))
    fix = wrong(it, key)
    if not fix:
        continue
    start, name, year = fix
    have = '%s (%s)' % (g(it, 'Name'), g(it, 'ProductionYear') or '?')
    found = call('POST', '/Items/RemoteSearch/Series', {'SearchInfo': {'Name': name, 'Year': year}, 'ItemId': g(it, 'Id'), 'IncludeDisabledProviders': False}) or []
    pick = [r for r in found if g(r, 'ProductionYear') == year and (g(r, 'Name') or '').lower().replace(':', '') == name.lower().replace(':', '')] or \
           [r for r in found if g(r, 'ProductionYear') == year] or found
    if not pick:
        print('%-40s folder %r: nothing found for %s (%d)' % (have, os.path.basename(g(it, 'Path')), name, year))
        continue
    r = pick[0]
    print('%-40s folder %r  ->  %s (%s)%s' % (have, os.path.basename(g(it, 'Path')), g(r, 'Name'), g(r, 'ProductionYear'), '' if GO else '  (not yet: add go)'))
    if GO:
        call('POST', '/Items/RemoteSearch/Apply/%s' % g(it, 'Id'), r, ReplaceAllImages='true')
        done += 1

# empty "untitled folder"s, in the libraries' folders
roots = []
for vf in call('GET', '/Library/VirtualFolders') or []:
    roots += g(vf, 'Locations') or []
gone = 0
for root in roots:
    for dirpath, dirs, files in os.walk(root, topdown=False):
        if not os.path.basename(dirpath).lower().startswith('untitled folder'):
            continue
        inside = os.listdir(dirpath)
        if [f for f in inside if not f.startswith('.') and f != 'Icon\r']:   # (only the Mac's hidden files: still empty)
            print('kept (not empty): %s  (has: %s)' % (dirpath, ', '.join(inside[:5])))
            continue
        print(('removed: ' if GO else 'would remove: ') + dirpath)
        if GO:
            for f in inside:
                p = os.path.join(dirpath, f)
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            os.rmdir(dirpath)
            gone += 1


if GO:
    print('== %d series matched again (their pictures follow in a minute or two), %d empty folders removed.' % (done, gone))
    call('POST', '/Library/Refresh')
else:
    print('== Nothing changed yet. Run:  python3 fix-series.py go')
