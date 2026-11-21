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

Every Criterion film its proper cover, straight from the originals in John's Google Drive ("Criterion images", put there by the old
import_requests.py from criterion.com), as they are: not padded to a poster's shape any more, as Plex needed, since Grifflix shows
a cover of any shape whole (John, 16 Nov 2026: "if we can change the display, we don't need to do this and all criterion films can
add the proper criterion image"). The originals are copied to "Criterion covers" on the G-Drive (kept there; only new ones each
time), and each film that has one gets it, replacing a padded or other poster:

  python3 ~/Grifflix/poster-add.py drive       brings the originals over, and says what it would put on which film
  python3 ~/Grifflix/poster-add.py drive go    and does it

(Replaces the old scripts/poster_add.py: John, 12 Nov 2026: "it should check and put the correct poster in".)
"""
import base64, hashlib, json, os, re, shutil, sys, unicodedata, urllib.parse, urllib.request

GO = 'go' in sys.argv[1:]
DRIVE = 'drive' in sys.argv[1:]
BASE = os.environ.get('GRIFFLIX_MEDIA') or '/Volumes/G-Drive/Grifflix'
MOVIES = os.path.join(BASE, 'Movies')
SOURCE = os.path.join(BASE, 'Criterion covers' if DRIVE else 'Criterion images')
KEEP = DRIVE   # (the Google Drive originals stay where they are, for next time; "Criterion images" ones are moved onto their films)
HOME = os.path.expanduser('~/Grifflix')
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


EXTRAS = ('other', 'extras', 'featurettes', 'behind the scenes', 'deleted scenes', 'interviews', 'trailers', 'shorts')
VIDEO = ('.mp4', '.mkv', '.m4v', '.mov', '.avi', '.ts', '.wmv', '.mpg', '.mpeg', '.webm')


def has_film(folder):   # (a film in it, not only its extras: an empty folder left behind gets no poster)
    for d, dirs, files in os.walk(folder):
        dirs[:] = [x for x in dirs if x.lower() not in EXTRAS and not x.startswith('.')]
        if any(f.lower().endswith(VIDEO) and not f.startswith('.') for f in files):
            return True
    return False


FPATH = {}   # (each film's file, by its id: a cover of its own beside it, named after it)


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
            parts = rel.split(os.sep)
            if len(parts) > 2 and parts[1].lower() in EXTRAS:   # (a film's extras, in its "Other" folder: not the film)
                continue
            out.setdefault(parts[0], []).append((it.get('Id', it.get('id')), it.get('Name', it.get('name')) or ''))   # (its folder, even from a sub-folder of it)
            FPATH[it.get('Id', it.get('id'))] = it.get('Path', it.get('path')) or ''
        start += 500
        if start >= r.get('TotalRecordCount', r.get('totalRecordCount', 0)):
            break
    return out


def from_drive():   # the originals, from John's Google Drive, with the downloader's own rclone and sign-in
    import subprocess
    rclone, conf = os.path.join(HOME, 'rclone'), os.path.join(HOME, 'rclone.conf')
    if not (os.path.exists(rclone) and os.path.exists(conf)):
        sys.exit('Google Drive isn’t set up on the Mac mini (the downloader’s rclone): run gdrive.py setup first.')
    os.makedirs(SOURCE, exist_ok=True)
    print('Bringing the Criterion covers over from Google Drive (only new ones)…')
    r = subprocess.run([rclone, '--config', conf, 'copy', 'gdrive:Criterion images', SOURCE, '--transfers', '8', '--stats-one-line', '--stats', '20s'], text=True)
    if r.returncode:
        sys.exit('Google Drive didn’t give them (rclone %d).' % r.returncode)


def main():
    if not os.path.isdir(MOVIES):
        sys.exit('No %s: is the G-Drive plugged in?' % MOVIES)
    if DRIVE and 'nofetch' not in sys.argv[1:]:   # (nofetch: only the covers already here, as when a film has just come in)
        from_drive()
    if not os.path.isdir(SOURCE):
        sys.exit('No "%s": put the posters there (named after their films’ folders) and run this again.' % SOURCE)
    folders = [f for f in os.listdir(MOVIES) if not f.startswith('.') and os.path.isdir(os.path.join(MOVIES, f)) and has_film(os.path.join(MOVIES, f))]
    exact, near, byname = {}, {}, {}
    for f in folders:
        exact.setdefault(old_key(f), []).append(f)
        near.setdefault(loose(f), []).append(f)
        m_ = re.match(r'^(.*?)\s*\((\d{4})\)\s*$', f)
        if m_:
            byname.setdefault(loose(m_.group(1)), []).append((f, int(m_.group(2))))
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
        if not hit:   # (a year out: Criterion's year and the folder's, from TMDB, often differ by one; Return to Reason, 2023 and 2024: John, 17 Nov 2026)
            m_ = re.match(r'^(.*)_(\d{4})$', name)
            if m_:
                hit = [f for f, y in byname.get(loose(m_.group(1)), []) if abs(y - int(m_.group(2))) <= 1] or None
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
            if GO and not KEEP:
                os.remove(src)
            continue
        print('  %s → %s%s' % (p, os.path.relpath(dest, BASE), ' (replacing its poster)' if os.path.exists(dest) or any(os.path.exists(o) for o in others) else ''))
        if not GO:
            done += 1; continue
        if films is not None and len(set(re.sub(r'[^a-z0-9]', '', n.lower()) for _, n in films.get(folder) or [])) > 1:
            # (a release of several films in one folder, its cover the folder's own (W. C. Fields—Six Short Films): that cover on each of its
            # films, beside each named after its file ("Pool Sharks (1915)-poster.jpg"), as the media server reads a film's own poster in a
            # shared folder: John, 21 Nov 2026, "in the rare case where box sets don't have individual covers, like WC fields - just use the
            # box set cover" ... "Wc fields films too")
            ids_ = [(i_, FPATH.get(i_)) for i_, _ in films[folder] if FPATH.get(i_)]
            outs = [(i_, os.path.splitext(fp_)[0] + ('-poster.png' if t == 'image/png' else '-poster.jpg')) for i_, fp_ in ids_]
            if outs and all(os.path.exists(d_) and same(src, d_) for _, d_ in outs):
                same_n += 1
                print('  already there: %s (its %d films)' % (folder, len(outs)))
                if GO and not KEEP:
                    os.remove(src)
                continue
            print('  %s → %s (on each of its %d films)' % (p, folder, len(outs)))
            if GO:
                for i_, d_ in outs:
                    shutil.copyfile(src, d_)
                    try:
                        jf('POST', '/Items/%s/Images/Primary' % i_, base64.b64encode(open(d_, 'rb').read()), ctype=t)
                    except Exception as e:
                        server.append('%s (%s)' % (os.path.basename(d_), e))
                if not KEEP:
                    os.remove(src)
            done += 1
            continue
        for o in others:   # (one poster per film, so the media server can't pick the old one)
            if os.path.exists(o):
                os.remove(o)
        if KEEP:
            shutil.copyfile(src, dest)
        else:
            shutil.move(src, dest)
        done += 1
        if films is not None:
            ids = films.get(folder) or []
            if not ids:
                server.append('%s (not in the media server yet)' % folder)
                continue
            if len(set(re.sub(r'[^a-z0-9]', '', n.lower()) for _, n in ids)) > 1:   # (different films in one folder, a set: not one cover for them all)
                server.append('%s (%d different films in that folder: %s)' % (folder, len(ids), ', '.join(sorted(set(n for _, n in ids)))[:200]))
                continue
            for i, _ in ids:   # (one film, maybe in more than one copy: each copy its cover)
                try:
                    jf('POST', '/Items/%s/Images/Primary' % i, base64.b64encode(open(dest, 'rb').read()), ctype=t)
                except Exception as e:
                    server.append('%s (%s)' % (folder, e))
    print('\n%s %d poster(s)%s.' % ('Put' if GO else 'Would put', done, ', %d already there' % same_n if same_n else ''))
    if KEEP and unmatched:   # (most Criterion films aren't in the library: just how many)
        print('\n%d cover(s) for films not in the library.' % len(unmatched)); unmatched = []
    for title, rows in (('No film folder matches (left in "Criterion images"):', unmatched),
                        ('More than one folder matches (left there: rename the picture to the right one):', unsure),
                        ('Not a picture, or too small (left there):', bad),
                        ('In its folder, but not put on the film in the media server (it may take it at its next scan):', server)):
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
