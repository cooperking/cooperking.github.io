#!/usr/bin/env python3
"""Puts John's own posters (the Criterion images) on their films: into each film's folder as poster.jpg, and into the media
server straight away, so Grifflix shows them without waiting for a library scan.

The pictures go in "Criterion images" on the G-Drive (next to Movies), each named after its film's folder the way the old
script expected: the folder name in lower case, letters only, accents plain, "_" before the year,
e.g. "The Seventh Seal (1957)" → theseventhseal_1957.jpg. Close variants match too (no "_", no leading "the", .jpeg or .png,
capitals). A picture matched to its film is moved out of "Criterion images"; one that matches nothing, or more than one film,
stays there and is listed, so nothing is ever put on the wrong film.

  python3 ~/Grifflix/poster-add.py        what it would do (nothing changes)
  python3 ~/Grifflix/poster-add.py go     does it

(Replaces the old scripts/poster_add.py: John, 12 Nov 2026: "it should check and put the correct poster in".)
"""
import base64, hashlib, json, os, re, shutil, sys, unicodedata, urllib.parse, urllib.request

GO = len(sys.argv) > 1 and sys.argv[1] == 'go'
BASE = os.environ.get('GRIFFLIX_MEDIA') or '/Volumes/G-Drive/Grifflix'
MOVIES = os.path.join(BASE, 'Movies')
SOURCE = os.path.join(BASE, 'Criterion images')
APP = os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix')
PICS = ('.jpg', '.jpeg', '.png')
EXTRA = {'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'œ': 'oe', 'Œ': 'OE', 'ß': 'ss', 'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D', 'þ': 'th', 'ð': 'd'}


def plain(t):   # accents plain, as unidecode did (without needing it installed)
    t = ''.join(EXTRA.get(c, c) for c in t)
    return unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode()


def old_key(folder):   # exactly the old script's name for a folder's picture
    return re.sub(r'[^a-zA-Z0-9_]', '', re.sub(r'\(', '_(', plain(folder))).lower()


def loose(t):   # letters and numbers only, no leading "the"
    t = re.sub(r'[^a-z0-9]', '', plain(t).lower())
    return t[3:] if t.startswith('the') and len(t) > 7 else t


def kind(path):   # a real picture? (not a web page saved as .jpg, nor an empty file)
    with open(path, 'rb') as f:
        head = f.read(12)
    if head[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    return None


def same(a, b):
    h = lambda p: hashlib.sha1(open(p, 'rb').read()).hexdigest()
    return os.path.getsize(a) == os.path.getsize(b) and h(a) == h(b)


# the media server, to put each poster on its film straight away (and Grifflix's gateway, to look again)
try:
    S = json.load(open(os.path.join(APP, 'settings.json')))
except Exception:
    S = None


def jf(method, path, data=None, ctype='application/json', **q):
    url = S.get('jellyfin', 'http://127.0.0.1:8096') + path + ('?' + urllib.parse.urlencode(q) if q else '')
    r = urllib.request.Request(url, data=data, method=method, headers={'Authorization': 'MediaBrowser Token="%s"' % S['jf_token'], 'Content-Type': ctype})
    raw = urllib.request.urlopen(r, timeout=120).read()
    return json.loads(raw) if raw[:1] in (b'{', b'[') else None


def films_by_folder():   # each film folder → the media server's films in it
    out = {}
    lid = (S.get('libraries') or {}).get('movies')
    start = 0
    while lid:
        r = jf('GET', '/Users/%s/Items' % S['jf_user_id'], ParentId=lid, Recursive='true', IncludeItemTypes='Movie', Fields='Path', StartIndex=start, Limit=500)
        items = r.get('Items', r.get('items', []))
        for it in items:
            rel = os.path.relpath(it.get('Path', it.get('path')) or '/', MOVIES)
            if rel.startswith('..') or os.sep not in rel:
                continue
            out.setdefault(rel.split(os.sep)[0], []).append(it.get('Id', it.get('id')))   # (its folder, even from a sub-folder of it)
        start += 500
        if start >= r.get('TotalRecordCount', r.get('totalRecordCount', 0)):
            break
    return out


def main():
    if not os.path.isdir(MOVIES):
        sys.exit('No %s: is the G-Drive plugged in?' % MOVIES)
    if not os.path.isdir(SOURCE):
        sys.exit('No "%s": put the posters there (named after their films’ folders) and run this again.' % SOURCE)
    folders = [f for f in os.listdir(MOVIES) if not f.startswith('.') and os.path.isdir(os.path.join(MOVIES, f))]
    exact, near = {}, {}
    for f in folders:
        exact.setdefault(old_key(f), []).append(f)
        near.setdefault(loose(f), []).append(f)
    pics = sorted(p for p in os.listdir(SOURCE) if not p.startswith('.') and p.lower().endswith(PICS))
    if not pics:
        return print('No pictures in "%s".' % SOURCE)
    print('%s %d picture(s) in "%s"\n' % ('Putting' if GO else 'Would put (run with go to do it):', len(pics), SOURCE))
    films = None
    if GO and S:
        try:
            films = films_by_folder()
        except Exception as e:
            print('(the media server didn’t answer: %s. The posters go in the folders; it picks them up at its next scan)\n' % e)
    done = same_n = 0
    unmatched, unsure, bad, server = [], [], [], []
    for p in pics:
        src = os.path.join(SOURCE, p)
        name = os.path.splitext(p)[0]
        hit = exact.get(name.lower()) or exact.get(old_key(name)) or near.get(loose(name.replace('_', ' ')))
        if not hit:
            unmatched.append(p); continue
        if len(set(hit)) > 1:
            unsure.append('%s → %s' % (p, ' or '.join(sorted(set(hit))))); continue
        t = kind(src)
        if not t or os.path.getsize(src) < 5000:
            bad.append(p); continue
        folder = hit[0]
        dest = os.path.join(MOVIES, folder, 'poster.png' if t == 'image/png' else 'poster.jpg')
        others = [os.path.join(MOVIES, folder, o) for o in ('poster.jpg', 'poster.jpeg', 'poster.png', 'folder.jpg', 'folder.png') if o != os.path.basename(dest)]
        if os.path.exists(dest) and same(src, dest):
            same_n += 1
            print('  already there: %s' % folder)
            if GO:
                os.remove(src)
            continue
        print('  %s → %s%s' % (p, os.path.relpath(dest, BASE), ' (replacing its poster)' if os.path.exists(dest) or any(os.path.exists(o) for o in others) else ''))
        if not GO:
            done += 1; continue
        for o in others:   # (one poster per film, so the media server can't pick the old one)
            if os.path.exists(o):
                os.remove(o)
        shutil.move(src, dest)
        done += 1
        if films is not None:
            ids = films.get(folder) or []
            if len(ids) != 1:
                server.append('%s (%s)' % (folder, 'not in the media server yet' if not ids else '%d films in that folder' % len(ids)))
                continue
            try:
                jf('POST', '/Items/%s/Images/Primary' % ids[0], base64.b64encode(open(dest, 'rb').read()), ctype=t)
            except Exception as e:
                server.append('%s (%s)' % (folder, e))
    print('\n%s %d poster(s)%s.' % ('Put' if GO else 'Would put', done, ', %d already there' % same_n if same_n else ''))
    for title, rows in (('No film folder matches (left in "Criterion images"):', unmatched),
                        ('More than one folder matches (left there: rename the picture to the right one):', unsure),
                        ('Not a picture, or too small (left there):', bad),
                        ('In its folder, but the media server will take it at its next scan:', server)):
        if rows:
            print('\n' + title)
            for r in rows:
                print('  ' + r)
    if GO and done and S:   # Grifflix looks again, so the new posters show
        try:
            r = urllib.request.Request('http://127.0.0.1:%s/local/remap' % S.get('port', 8097), data=b'{"map": {}}', method='POST',
                                       headers={'X-Grifflix-Secret': S['secret'], 'Content-Type': 'application/json'})
            urllib.request.urlopen(r, timeout=30).read()
            print('\nGrifflix has the new posters (on the site after the next Update everything).')
        except Exception:
            pass


if __name__ == '__main__':
    main()
