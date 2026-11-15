#!/usr/bin/env python3
"""Films from Google Drive (and TorBox) into Grifflix, by themselves (John, 15 Nov 2026: "perhaps we could integrate google drive instead";
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

A Google page opens: sign in, allow it. Terminal then shows a long code between "Paste the following into your remote
machine --->" and "<---End paste" (it starts eyJ… or {"access_token"…). Copy all of that one line, paste it here, press Return:
''')
    tok = input('> ').strip()
    if tok.startswith('eyJ'):   # (newer rclone gives it encoded, a long line starting eyJ: the same sign-in inside)
        import base64
        try:
            d = json.loads(base64.urlsafe_b64decode(tok + '=' * (-len(tok) % 4)).decode('utf-8'))
            tok = d.get('token') if isinstance(d, dict) and d.get('token') else json.dumps(d)
            if not isinstance(tok, str):
                tok = json.dumps(tok)
        except Exception:
            pass
    if not (tok.startswith('{') and 'refresh_token' in tok):
        sys.exit('That isn\'t the code: the one long line between "--->" and "<---End paste". Run setup again.')
    rc('config', 'create', REMOTE, 'drive', 'scope=drive', 'token=' + tok, '--non-interactive')
    rc('mkdir', '%s:%s' % (REMOTE, FOLDER))
    print('\nDone: signed in. Put films in the "%s" folder of your Google Drive; the Mac mini fetches them every 15 minutes.' % FOLDER)
    print('To try it now:  python3 ~/Grifflix/gdrive.py run')


def run(check=False):
    if not (os.path.exists(RCLONE) and os.path.exists(CONF)):
        sys.exit('Not set up yet: python3 ~/Grifflix/gdrive.py setup')
    if check:   # (a look changes nothing: always done, even while a fetch runs)
        return _run(True)
    try:   # (one at a time)
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        if time.time() - os.path.getmtime(LOCK) < 6 * 3600:
            if sys.stdout.isatty():
                print('A fetch is already running (it was started %d minutes ago). What it\'s doing: tail ~/Grifflix/gdrive.log' % ((time.time() - os.path.getmtime(LOCK)) // 60))
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


# where films come from: Google Drive's Grifflix folder (each deleted once it's on the G-Drive), and TorBox if it's set up in rclone
# (as "torbox": fetched once and remembered, not deleted: its connection is read-only) (John, 15 Nov 2026: TorBox copied to Google Drive
# through the Mac mini, then fetched again: "ok do it that way", straight onto the G-Drive)
SOURCES = [{'remote': REMOTE, 'path': FOLDER, 'name': 'Google Drive', 'delete': True},
           {'remote': 'torbox', 'path': '', 'name': 'TorBox', 'delete': False}]
SEEN = os.path.join(HOME, 'fetched.json')


def _run(check):
    root = films_root()
    have = set(x.strip().rstrip(':') for x in rc('listremotes').split())
    try:
        seen = json.load(open(SEEN))
    except (OSError, ValueError):
        seen = {}
    done = 0
    for src in SOURCES:
        if src['remote'] not in have:
            continue
        loc = lambda p, src=src: '%s:%s' % (src['remote'], (src['path'] + '/' + p) if src['path'] else p)
        try:
            items = json.loads(rc('lsjson', '-R', '--files-only', loc('').rstrip('/')) or '[]')
        except Exception as e:
            log('%s: couldn’t look (%s)' % (src['name'], e))
            continue
        films = [x for x in items if x['Path'].lower().endswith(VIDEO) and 'sample' not in x['Name'].lower()]
        subs = [x for x in items if x['Path'].lower().endswith('.srt')]
        for f in films:
            path, size = f['Path'], int(f.get('Size') or 0)
            key = '%s:%s|%d' % (src['remote'], path, size)
            if not src['delete'] and key in seen:
                continue   # (fetched before: TorBox keeps its copy)
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
                if key not in seen:
                    log('Left in %s: %s ("%s" is already in Grifflix)' % (src['name'], path, title))
                    if not check:
                        seen[key] = int(time.time())
                continue
            if check:
                log('Would add from %s: %s → %s/%s%s' % (src['name'], path, title, title + ext, '' if year else ' (no year in its name)'))
                continue
            free = shutil.disk_usage(root).free
            if free < size + 20e9:
                log('Not enough room on the drive for %s (%.0f GB free): left in %s' % (path, free / 1e9, src['name']))
                continue
            log('Fetching from %s: %s → %s/ (%.1f GB)' % (src['name'], path, title, size / 1e9))
            os.makedirs(dest_dir, exist_ok=True)
            part = dest + '.adding'
            try:
                rc('copyto', loc(path), part, '--retries', '3')
            except Exception as e:
                log('  the download failed (%s): left in %s' % (e, src['name']))
                if os.path.exists(part):
                    os.remove(part)
                try:
                    os.rmdir(dest_dir)
                except OSError:
                    pass
                continue
            if os.path.getsize(part) != size:
                log('  the copy isn’t whole (%d of %d bytes): left in %s' % (os.path.getsize(part), size, src['name']))
                os.remove(part)
                continue
            os.replace(part, dest)
            # (its subtitles: beside it, with the film's new name)
            stem = os.path.splitext(f['Name'])[0]
            for s_ in subs:
                if os.path.dirname(s_['Path']) != folder or (not folder and not s_['Name'].startswith(stem)):
                    continue
                sb = os.path.splitext(s_['Name'])[0]
                lang = '.' + sb.rsplit('.', 1)[1] if re.search(r'\.[a-z]{2,3}$', sb) else ''
                try:
                    rc('copyto', loc(s_['Path']), os.path.join(dest_dir, title + lang + '.srt'))
                    if src['delete']:
                        rc('deletefile', '--drive-use-trash=false', loc(s_['Path']))
                except Exception as e:
                    log('  its subtitles %s: %s' % (s_['Name'], e))
            if src['delete']:
                rc('deletefile', '--drive-use-trash=false', loc(path))
            seen[key] = int(time.time())
            log('  done: on the G-Drive' + (', and deleted from ' + src['name'] if src['delete'] else '') +
                ('' if year else '. NOTE: "%s" has no year in its name, so the media server may match the wrong film: if it does, rename its folder "%s (Year)"' % (title, title)))
            done += 1
            if src['delete']:
                rc('rmdirs', '--leave-root', loc('').rstrip('/'), check=False)   # (download folders left empty)
        try:
            tmp = SEEN + '.tmp'
            json.dump(seen, open(tmp, 'w'))
            os.replace(tmp, SEEN)
        except OSError:
            pass
    if done:
        log('%d film(s) fetched: Grifflix shows them within about 20 minutes' % done)
    elif check and sys.stdout.isatty():
        print('Nothing new to fetch.')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'setup':
        setup()
    elif cmd in ('run', 'check'):
        run(check=cmd == 'check')
    else:
        print(__doc__)
