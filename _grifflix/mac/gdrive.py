#!/usr/bin/env python3
"""Films from Google Drive into Grifflix, by themselves (John, 15 Nov 2026: "perhaps we could integrate google drive instead";
"delete once it's on the g-drive").

Put a film (or a download's folder) in the "Grifflix" folder of your Google Drive. The Mac mini looks every 15 minutes (the
gateway runs this), downloads each one straight onto the G-Drive in the folder the media server wants,

    Grifflix/Films.Name.2015.1080p.mkv  →  /Volumes/G-Drive/Grifflix/Movies/Films Name (2015)/Films Name (2015).mkv

checks the copy is whole (its size), and only then deletes it from Google Drive for good (not to Google's bin, which still counts
against the 15 GB). Subtitles beside a film (.srt) come with it. A film with no year in its name comes too, flagged. The
media server and the site then pick it up by themselves.

    python3 ~/Grifflix/gdrive.py setup     once: rclone put on this Mac and signed in to your Google account
    python3 ~/Grifflix/gdrive.py run       look now (the gateway does this every 15 minutes)
    python3 ~/Grifflix/gdrive.py check     what it would do, nothing changed

It uses rclone 1.63.1 (the last version that runs on macOS 10.13), kept in ~/Grifflix with its own settings.
"""
import json, os, re, shutil, subprocess, sys, time, urllib.request, zipfile

HOME = os.path.expanduser('~/Grifflix')
APP = os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix')
RCLONE = os.path.join(HOME, 'rclone')
CONF = os.path.join(HOME, 'rclone.conf')
LOG = os.path.join(HOME, 'gdrive.log')
LOCK = os.path.join(HOME, 'gdrive.lock')
REMOTE, FOLDER = 'gdrive', 'Grifflix'
VERSION = '1.63.1'
VIDEO = ('.mp4', '.mkv', '.m4v', '.mov', '.avi')
JUNK = r'(480p|576p|720p|1080p|2160p|4k|uhd|hdr|bluray|blu-ray|brrip|bdrip|webrip|web-dl|webdl|web|hdtv|dvdrip|dvd|x264|x265|h264|h265|hevc|xvid|10bit|aac|ac3|dts|ddp\S*|remux|proper|repack|extended|yify|yts)'


def log(*a):
    line = time.strftime('%Y-%m-%d %H:%M ') + ' '.join(str(x) for x in a)
    print(line, flush=True)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass


def rc(*args, check=True, capture=True):
    r = subprocess.run([RCLONE, '--config', CONF] + list(args), capture_output=capture, text=True)
    if check and r.returncode:
        raise RuntimeError('rclone %s: %s' % (args[0], (r.stderr or '').strip()[-300:]))
    return r.stdout if capture else ''


def title_of(name):
    """("Title (Year)", has a year) from a file or folder name, as grifflix-add does: the last year in it that isn't the start (titles
    with a number: "Blade Runner 2049 (2017)", "2001 A Space Odyssey 1968"), and everything after it dropped."""
    n = re.sub(r'[._]', ' ', os.path.splitext(name)[0] if name.lower().endswith(VIDEO + ('.srt',)) else name)
    best = None
    for x in re.finditer(r'[\[(]?((?:19|20)\d\d)[\])]?(?= |$)', n):
        if x.start() > 0 and int(x.group(1)) <= time.localtime().tm_year + 1 and n[:x.start()].strip(' -(['):
            best = x
    if best:
        return '%s (%s)' % (n[:best.start()].strip(' -(['), best.group(1)), True
    t = re.split(r'[ \-\[(]' + JUNK + r'\b', n, maxsplit=1, flags=re.I)[0].strip(' -')
    return (t or n).strip(), False


def films_root():
    """The films folder, from the media server (as the Mac's own scripts find it)."""
    s = json.load(open(os.path.join(APP, 'settings.json')))
    req = urllib.request.Request(s.get('jellyfin', 'http://127.0.0.1:8096') + '/Library/VirtualFolders', headers={'Authorization': 'MediaBrowser Token="%s"' % s['jf_token']})
    for v in json.loads(urllib.request.urlopen(req, timeout=30).read()):
        if (v.get('CollectionType') or '') == 'movies' and v.get('Locations'):
            return v['Locations'][0]
    raise RuntimeError('the media server has no films folder')


def setup():
    os.makedirs(HOME, exist_ok=True)
    if not os.path.exists(RCLONE):
        url = 'https://downloads.rclone.org/v%s/rclone-v%s-osx-amd64.zip' % (VERSION, VERSION)
        print('Getting rclone %s…' % VERSION)
        zp = os.path.join(HOME, 'rclone.zip')
        # (with the certificates Grifflix's other scripts use: python.org's Python has none of its own, and refused the download)
        try:
            import certifi, ssl
            ctx = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(url, context=ctx, timeout=300) as r, open(zp, 'wb') as f:
                shutil.copyfileobj(r, f)
        except Exception:
            subprocess.run(['curl', '-fsSL', url, '-o', zp], check=True)   # (or the Mac's own curl)
        with zipfile.ZipFile(zp) as z:
            name = next(n for n in z.namelist() if n.endswith('/rclone'))
            with z.open(name) as src, open(RCLONE, 'wb') as dst:
                shutil.copyfileobj(src, dst)
        os.chmod(RCLONE, 0o755)
        os.remove(zp)
    print('''
Now the sign-in, on your work Mac (it has a browser this Mac's old Safari can't match). In Terminal there, run:

  curl -fsSL https://downloads.rclone.org/rclone-current-osx-arm64.zip -o /tmp/rc.zip && unzip -oq /tmp/rc.zip -d /tmp/rc && /tmp/rc/rclone-*/rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlIn0"

A Google page opens: sign in, allow it. Terminal then shows a long code between two lines of arrows, starting {"access_token"
… Copy all of it (the {…} line) and paste it here, then press Return:
''')
    tok = input('> ').strip()
    if not (tok.startswith('{') and 'refresh_token' in tok):
        sys.exit('That isn\'t the code (it starts with {"access_token" and has "refresh_token" in it). Run setup again.')
    rc('config', 'create', REMOTE, 'drive', 'scope=drive', 'token=' + tok, '--non-interactive')
    rc('mkdir', '%s:%s' % (REMOTE, FOLDER))
    print('\nDone: signed in. Put films in the "%s" folder of your Google Drive; the Mac mini fetches them every 15 minutes.' % FOLDER)
    print('To try it now:  python3 ~/Grifflix/gdrive.py run')


def run(check=False):
    if not (os.path.exists(RCLONE) and os.path.exists(CONF)):
        sys.exit('Not set up yet: python3 ~/Grifflix/gdrive.py setup')
    try:   # (one at a time)
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        if time.time() - os.path.getmtime(LOCK) < 6 * 3600:
            return
        os.remove(LOCK)
        open(LOCK, 'w').close()
    try:
        _run(check)
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def _run(check):
    root = films_root()
    items = json.loads(rc('lsjson', '-R', '--files-only', '%s:%s' % (REMOTE, FOLDER)) or '[]')
    films = [x for x in items if x['Path'].lower().endswith(VIDEO) and 'sample' not in x['Name'].lower()]
    subs = [x for x in items if x['Path'].lower().endswith('.srt')]
    done = 0
    for f in films:
        path, size = f['Path'], int(f.get('Size') or 0)
        folder = os.path.dirname(path)
        title, year = title_of(f['Name'])
        if not year and folder:
            t2, y2 = title_of(os.path.basename(folder))
            if y2:
                title, year = t2, y2
        ext = os.path.splitext(f['Name'])[1].lower()
        dest_dir = os.path.join(root, title)
        dest = os.path.join(dest_dir, title + ext)
        if os.path.isdir(dest_dir) and any(n.lower().endswith(VIDEO) for n in os.listdir(dest_dir)):
            log('Left in Google Drive: %s ("%s" is already in Grifflix)' % (path, title))
            continue
        if check:
            log('Would add: %s → %s/%s%s' % (path, title, title + ext, '' if year else ' (no year in its name)'))
            continue
        free = shutil.disk_usage(root).free
        if free < size + 20e9:
            log('Not enough room on the drive for %s (%.0f GB free): left in Google Drive' % (path, free / 1e9))
            continue
        log('Fetching from Google Drive: %s → %s/ (%.1f GB)' % (path, title, size / 1e9))
        os.makedirs(dest_dir, exist_ok=True)
        part = dest + '.adding'
        try:
            rc('copyto', '%s:%s/%s' % (REMOTE, FOLDER, path), part, '--retries', '3')
        except Exception as e:
            log('  the download failed (%s): left in Google Drive' % e)
            for x in (part,):
                if os.path.exists(x):
                    os.remove(x)
            try:
                os.rmdir(dest_dir)
            except OSError:
                pass
            continue
        if os.path.getsize(part) != size:
            log('  the copy isn’t whole (%d of %d bytes): left in Google Drive' % (os.path.getsize(part), size))
            os.remove(part)
            continue
        os.replace(part, dest)
        # (its subtitles: beside it in Google Drive, with the film's new name)
        stem = os.path.splitext(f['Name'])[0]
        for s in subs:
            if os.path.dirname(s['Path']) != folder or (not folder and not s['Name'].startswith(stem)):
                continue
            sb = os.path.splitext(s['Name'])[0]
            lang = '.' + sb.rsplit('.', 1)[1] if re.search(r'\.[a-z]{2,3}$', sb) else ''
            try:
                rc('copyto', '%s:%s/%s' % (REMOTE, FOLDER, s['Path']), os.path.join(dest_dir, title + lang + '.srt'))
                rc('deletefile', '--drive-use-trash=false', '%s:%s/%s' % (REMOTE, FOLDER, s['Path']))
            except Exception as e:
                log('  its subtitles %s: %s' % (s['Name'], e))
        rc('deletefile', '--drive-use-trash=false', '%s:%s/%s' % (REMOTE, FOLDER, path))
        log('  done: on the G-Drive, and deleted from Google Drive' + ('' if year else '. NOTE: "%s" has no year in its name, so the media server may match the wrong film: if it does, rename its folder "%s (Year)"' % (title, title)))
        done += 1
    if done:
        rc('rmdirs', '--leave-root', '%s:%s' % (REMOTE, FOLDER), check=False)   # (download folders left empty)
        log('%d film(s) from Google Drive: Grifflix shows them within about 20 minutes' % done)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'setup':
        setup()
    elif cmd in ('run', 'check'):
        run(check=cmd == 'check')
    else:
        print(__doc__)
