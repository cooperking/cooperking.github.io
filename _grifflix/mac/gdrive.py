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
    python3 ~/Grifflix/gdrive.py tidy      folders fetched with a website's or squashed name put right
    python3 ~/Grifflix/gdrive.py again ghosts   ones fetched before, whose names have this in them, fetched again next time

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
    # (a folder and its file squashed into one name with a lookalike slash, "Folder／File.mkv", as a copy through Google Drive leaves
    # them: the file's own part only; and a website's name in front, "www.UIndex.org - Hamlet …", dropped: John, 15 Nov 2026)
    name = re.split(r'[／/\\]', name)[-1]
    # (only a real website's name: in brackets, starting www, or followed by a dash. "The.Christophers" and "The Secret Agent" had
    # lost "The Ch" and "The Se" as if they were websites: John, 15 Nov 2026)
    tld = r'(?:org|com|net|to|me|cc|tv|io|ws|in|co|uk|nl|se|si|sx|ch|bz)'
    name = re.sub(r'^\s*(?:\[\s*(?:www[ .])?[a-z0-9-]+[ .]' + tld + r'\s*\]|www[ .][a-z0-9-]+[ .]' + tld + r'\b|[a-z0-9-]+[ .]' + tld + r'(?=\s+[-–—]))\s*[-–—]*\s*', '', name, flags=re.I)
    n = re.sub(r'[._]', ' ', os.path.splitext(name)[0] if name.lower().endswith(VIDEO + ('.srt',)) else name)
    m = re.match(r'^\s*[\[(]?((?:19|20)\d\d)[\])]?\s*[-–—]\s*(\S.*)$', n)   # (the year first: "(1974) - Starduct")
    if m:
        return '%s (%s)' % (m.group(2).strip(), m.group(1)), True
    best = None
    for x in re.finditer(r'[\[(]?((?:19|20)\d\d)[\])]?(?= |$)', n):
        if x.start() > 0 and int(x.group(1)) <= time.localtime().tm_year + 1 and n[:x.start()].strip(' -(['):
            best = x
    if best:
        before = n[:best.start()].strip(' -([')
        # (a television strand's name first, the film's own after the year: "BBC Arena 2026 Kim Novak's Vertigo" is Kim Novak's
        # Vertigo. "BBC Arena will never be the title of a film": John, 15 Nov 2026)
        if re.match(r'^(bbc|itv|ch4|channel 4|channel4|c4|more4|sky( arts| documentaries)?|pbs|arte|nat ?geo|national geographic|cnn|hbo|abc|cbs|nbc|rte|s4c|stv)\b', before, re.I):
            after = re.split(r'(?:^|[ \-\[(])' + JUNK + r'\b', n[best.end():], maxsplit=1, flags=re.I)[0].strip(' -)]')
            after = re.sub(r'\s+(MVGroup|org|com)\b.*$', '', after, flags=re.I).strip(' -')
            if re.search(r'[A-Za-z]{2}', after):
                return '%s (%s)' % (after, best.group(1)), True
        return '%s (%s)' % (before, best.group(1)), True
    t = re.split(r'[ \-\[(]' + JUNK + r'\b', n, maxsplit=1, flags=re.I)[0].strip(' -')
    return (t or n).strip(), False


def roots():
    """The media server's films and TV folders."""
    s = json.load(open(os.path.join(APP, 'settings.json')))
    req = urllib.request.Request(s.get('jellyfin', 'http://127.0.0.1:8096') + '/Library/VirtualFolders', headers={'Authorization': 'MediaBrowser Token="%s"' % s['jf_token']})
    out = {}
    for v in json.loads(urllib.request.urlopen(req, timeout=30).read()):
        k = v.get('CollectionType') or ''
        if k in ('movies', 'tvshows') and v.get('Locations') and k not in out:
            out[k] = v['Locations'][0]
    return out


# (a download's extras, in a folder of their own beside the film: each into the film's "Other" folder, not taken for a film: John,
# 16 Nov 2026, Birth's "Making Birth", "Trailer" and the rest, "these need to be automatically added as Extras in the Birth folder")
XDIRS = ('extras', 'extra', 'featurettes', 'featurette', 'behind the scenes', 'deleted scenes', 'interviews', 'trailers', 'shorts', 'other', 'others',
         'bonus', 'bonus features', 'special features', 'supplements')


def extra_of(path):
    """The film's folder in the download, if this file is one of its extras (in an extras folder below it); else None."""
    parts = path.split('/')
    for i in range(1, len(parts) - 1):
        if parts[i].strip().lower() in XDIRS:
            return '/'.join(parts[:i])
    return None


EP = re.compile(r'(?:^|[ ._\-\[(])[Ss](\d{1,2})[ ._\-]?[Ee](\d{1,3})(?:[ ._\-]?[Ee]\d{1,3})?(?=$|[ ._\-\])])')
NAMES = os.path.join(HOME, 'fetch-names.json')   # (John's: the right name for a download, found on TMDB from the admin page: John, 15 Nov 2026,
                                                 # "I should be able to use TMDB in the upcoming so it gets added correctly")


def names():
    try:
        return json.load(open(NAMES))
    except (OSError, ValueError):
        return {}


TMDBC = os.path.join(HOME, 'fetch-tmdb.json')   # (what TMDB said for each download's name, so it's asked once)


def tmdb_name(title, year, series):
    """A download's name checked on TMDB (through the media server): its exact title when TMDB has one close to it, from the same
    year (a year either side for a film released late in a year). "(1974) - Starduct" came in as Starduct, the download's own
    misspelling (John, 15 Nov 2026: "why was Stardust 1974 renamed Starduct? Very odd"). None when nothing fits well enough."""
    import difflib
    name = re.sub(r'\s*\(\d{4}\)$', '', title).strip()
    y = int(year) if year else None
    s_ = json.load(open(os.path.join(APP, 'settings.json')))
    body = json.dumps({'SearchInfo': dict({'Name': name}, **({'Year': y} if y else {})), 'IncludeDisabledProviders': False}).encode()
    req = urllib.request.Request(s_.get('jellyfin', 'http://127.0.0.1:8096') + '/Items/RemoteSearch/' + ('Series' if series else 'Movie'), data=body, method='POST',
                                 headers={'Authorization': 'MediaBrowser Token="%s"' % s_['jf_token'], 'Content-Type': 'application/json'})
    res = json.loads(urllib.request.urlopen(req, timeout=30).read() or b'[]') or []
    plain = lambda t: re.sub(r'[^a-z0-9]+', ' ', (t or '').lower().replace('&', 'and')).strip()
    best = None
    for r in res[:8]:
        ry = r.get('ProductionYear')
        if y and ry and abs(int(ry) - y) > 1:
            continue
        a_, b_ = plain(name).replace(' ', ''), plain(r.get('Name')).replace(' ', '')
        # (the same name but for punctuation, spaces and capitals: "Dead Mans Wire" is Dead Man's Wire. A letter different is another
        # film, or a misspelling ("Hamlet" isn't Hamnet), so not taken: flagged on the admin page, to be named there)
        sc = 1.0 if a_ == b_ else difflib.SequenceMatcher(None, a_, b_).ratio()
        if not y and r is not res[0]:   # (no year to go by: only TMDB's first answer, and only when it's the same name)
            continue
        if sc >= .97 and (not best or sc > best[0]):
            best = (sc, r.get('Name'), ry or y)
    return {'name': best[1], 'year': best[2]} if best else None


def rlog_bytes(part):
    """How much rclone has brought so far, from the last count in its log (part + '.rlog'), or None."""
    try:
        with open(part + '.rlog', 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16384))
            tail = f.read().decode('utf-8', 'replace').splitlines()
        for line in reversed(tail):
            if '"stats"' in line:
                return int(json.loads(line)['stats'].get('bytes') or 0)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def fetch(loc, path, part, show=False):
    """One file from TorBox or Google Drive to `part`, as a copy of its folder limited to that one file (by a list, so names with
    [brackets] are taken as they are). Not "copyto" of the file itself: TorBox's WebDAV doesn't answer for a single file, so rclone took
    it for an empty folder, copied nothing and said it had worked; every TorBox download "went missing" (John, 15 Nov 2026). Returns
    what rclone said, for the log, when the file didn't come."""
    folder, name = os.path.split(path)
    tmpd, lst = part + '.d', part + '.list'
    os.makedirs(tmpd, exist_ok=True)
    with open(lst, 'w', encoding='utf-8') as f:
        f.write(name + '\n')
    # (a connection that goes quiet is given up on after 2 minutes, rclone's own way; and a download that takes in nothing for 10 minutes is
    # stopped and started again by the watchdog below, three times, then left for next time so the rest come: John, 17 Nov 2026, House
    # Party "stuck on 75% for a while": rclone hung at 17:35 with 13 more behind it)
    args = [RCLONE, '--config', CONF, 'copy', loc(folder) if folder else loc('').rstrip('/'), tmpd, '--files-from', lst, '--retries', '3',
            '--timeout', '2m', '--contimeout', '30s', '--low-level-retries', '10',
            # (several streams at once: TorBox gives each only about 7 Mbps, so one stream alone was slow (John, 17 Nov 2026, "Why is downloading
            # so slow?"). How far it has got is then rclone's own count, in its log beside the file, not the file's size, which several streams
            # make nearly whole at once (House Party "75%" with nothing coming))
            '--multi-thread-streams', '16', '--multi-thread-cutoff', '32M', '--buffer-size', '64M', '--use-json-log', '--log-level', 'NOTICE', '--stats', '10s', '--stats-log-level', 'NOTICE', '--log-file', part + '.rlog']   # (its folder listed, as works, not the one file looked up)

    def written():   # (what has come so far: rclone's own count, from its log; else what's on the disk)
        b_ = rlog_bytes(part)
        if b_ is not None:
            return b_
        n = 0
        for root_, _, files in os.walk(tmpd):
            for f_ in files:
                try:
                    st = os.stat(os.path.join(root_, f_))
                    n += st.st_size
                except OSError:
                    pass
        return n

    def watched(extra, quiet):
        for attempt in range(3):
            pr = subprocess.Popen(args + extra, stdout=None if not quiet else subprocess.DEVNULL, stderr=subprocess.PIPE if quiet else None, text=True)
            errs = []
            if quiet:
                import threading
                threading.Thread(target=lambda: errs.append(pr.stderr.read()), daemon=True).start()
            last, still = -1, time.time()
            while pr.poll() is None:
                time.sleep(30)
                w = written()
                if w != last:
                    last, still = w, time.time()
                elif time.time() - still > 600:
                    log('  nothing came for 10 minutes (%.1f GB so far): starting it again%s' % (w / 1e9, '' if attempt < 2 else ' (the last try)'))
                    pr.terminate()
                    try:
                        pr.wait(30)
                    except subprocess.TimeoutExpired:
                        pr.kill()
                        pr.wait()
                    shutil.rmtree(tmpd, ignore_errors=True)
                    os.makedirs(tmpd, exist_ok=True)
                    break
            else:
                time.sleep(.5)
                said_ = ('\n'.join(e for e in errs if e) or '').strip()
                if pr.returncode and not said_:
                    try:
                        said_ = '\n'.join(json.loads(l_).get('msg', '') for l_ in open(part + '.rlog', encoding='utf-8', errors='replace').read().splitlines()[-6:] if '"error"' in l_ or '"level":"error"' in l_)
                    except (OSError, ValueError):
                        pass
                return pr.returncode, said_[-400:]
        return 1, 'nothing came for 10 minutes, three times over'
    try:
        if show:
            rcode, said = watched(['--progress', '--stats-one-line'], False)
            said = said or ''
        else:
            rcode, said = watched(['-v'], True)
        r = type('R', (), {'returncode': rcode})()
        got = os.path.join(tmpd, name)
        if os.path.exists(got):
            os.replace(got, part)
        elif r.returncode:
            raise RuntimeError('rclone: %s' % (said or 'it stopped (%d)' % r.returncode))
        return said
    finally:
        for x_ in (lst, part + '.rlog'):
            try:
                os.remove(x_)
            except OSError:
                pass
        shutil.rmtree(tmpd, ignore_errors=True)


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


def lock_alive():
    """Whether the fetch that holds the lock is still running (an old lock with no number in it: by its age)."""
    try:
        pid = int((open(LOCK).read().split() or ['0'])[0])
    except (OSError, ValueError):
        pid = 0
    if not pid:   # (a lock from an older version: alive while any other fetch is running, however long it takes)
        try:
            ps = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=10).stdout
            return any('gdrive.py' in l and ' run' in l and int(l.split()[0]) != os.getpid() for l in ps.splitlines() if l.strip())
        except Exception:
            return os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 6 * 3600
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def run(check=False):
    if not (os.path.exists(RCLONE) and os.path.exists(CONF)):
        sys.exit('Not set up yet: python3 ~/Grifflix/gdrive.py setup')
    if check:   # (a look changes nothing: always done, even while a fetch runs)
        return _run(True)
    try:   # (one at a time: the lock holds the fetch's own number, so one stopped part way, as Hamlet's was, never blocks the rest)
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if lock_alive():
            if sys.stdout.isatty():
                print('A fetch is already running (it was started %d minutes ago). What it\'s doing: tail ~/Grifflix/gdrive.log' % ((time.time() - os.path.getmtime(LOCK)) // 60))
            return
        os.remove(LOCK)
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, ('%d %d' % (os.getpid(), int(os.path.getmtime(os.path.abspath(__file__))))).encode())   # (its number, and which version it is)
    os.close(fd)
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
MINE = os.path.getmtime(os.path.abspath(__file__))   # (this version: a newer one, put here by Update everything, taken up between films)
STATUS = os.path.join(HOME, 'fetch-status.json')
FIRST = os.path.join(HOME, 'fetch-first.json')   # (John's: the downloads he wants next, from the admin page: John, 15 Nov 2026, "Can I prioritise what's coming in next from torbox?")


CANCEL = os.path.join(HOME, 'fetch-cancelled.json')   # (John's: downloads he doesn't want, from the admin page, never fetched: John, 17 Nov 2026, "I need to be able to cancel downloads")


def cancelled():
    try:
        return set(str(x) for x in json.load(open(CANCEL)))
    except (OSError, ValueError, TypeError):
        return set()


def firsts():
    try:
        return [str(x) for x in json.load(open(FIRST))]
    except (OSError, ValueError, TypeError):
        return []


def in_order(plan):
    f = firsts()
    at = lambda p: f.index(p['path']) if p['path'] in f else f.index(p['nkey']) if p.get('nkey') in f else -1
    return sorted(plan, key=lambda p: at(p) if at(p) >= 0 else len(f) + plan.index(p) + (len(plan) if p.get('extra') else 0))   # (extras after the films)   # (for the admin page: what's coming in, how far, and what came: John, 15 Nov 2026)


def status_get():
    try:
        return json.load(open(STATUS))
    except (OSError, ValueError):
        return {}


def status(**kw):
    try:
        st = json.load(open(STATUS))
    except (OSError, ValueError):
        st = {}
    if 'came' in kw:   # (one more arrived: kept, newest first, the last 30; failures only in the log: John, 15 Nov 2026, "get rid of the Didn't come messages")
        c_ = kw.pop('came')
        st['recent'] = ([c_] if c_.get('ok') else []) + [x for x in st.get('recent', []) if x.get('ok')][:30]
    st.update(kw)
    try:
        tmp = STATUS + '.tmp'
        json.dump(st, open(tmp, 'w'))
        os.replace(tmp, STATUS)
    except OSError:
        pass


def wait_entry(p):
    return {'from': p['src']['name'], 'file': p['path'], 'key': p['nkey'], 'to': p['title'], 'gb': round(p['size'] / 1e9, 1), 'noyear': not p['year'],
            'series': p['series'], 'ep': p['ep'], 'named': p['named'], 'tmdb': p.get('tmdb'), 'extra': p.get('extra')}


def conv_running():
    try:
        pid = int(json.load(open(os.path.join(HOME, 'convert-now.json'))).get('pid') or 0)
        os.kill(pid, 0)
        return pid > 0
    except (OSError, ValueError, TypeError):
        return False


def wait_repack(dest, title):
    """The film just brought in repackaged before the next download (the drive does one at a time well): waited for, at most 45 minutes,
    or 6 if the converter isn't running by then."""
    flag = os.path.join(HOME, 'fetch-waiting')
    open(flag, 'w').write(title)
    status(hold=title)
    t0 = time.time()
    try:
        while os.path.exists(dest) and time.time() - t0 < 2700:
            if time.time() - t0 > 360 and not conv_running():
                break
            time.sleep(15)
        log('  %s: %s; on to the next' % (title, 'repackaged' if not os.path.exists(dest) else 'not repackaged yet (it will be)'))
    finally:
        status(hold=None)
        try:
            os.remove(flag)
        except OSError:
            pass


def _run(check):
    R = roots()
    root = R.get('movies')
    if not root:
        raise RuntimeError('the media server has no films folder')
    N = names()
    try:
        TC = json.load(open(TMDBC))
    except (OSError, ValueError):
        TC = {}
    have = set(x.strip().rstrip(':') for x in rc('listremotes').split())
    try:
        seen = json.load(open(SEEN))
    except (OSError, ValueError):
        seen = {}
    if not check:   # (what fetches stopped part way left behind, thrown away: this one holds the lock, so none is coming in now. The Blade's
        # five tries left 30 GB on the G-Drive: John, 17 Nov 2026)
        for top in [root] + ([R['tvshows']] if R.get('tvshows') else []):
            for dp, dns, fns in os.walk(top):
                for d_ in [d_ for d_ in dns if d_.endswith('.adding.d')]:
                    shutil.rmtree(os.path.join(dp, d_), ignore_errors=True)
                    dns.remove(d_)
                    log('  thrown away: a part-download left behind (%s)' % d_[:-len('.adding.d')])
                for f_ in fns:
                    if f_.endswith(('.adding', '.adding.list', '.adding.rlog')):
                        try:
                            os.remove(os.path.join(dp, f_))
                        except OSError:
                            pass
                if dp != top and not os.listdir(dp) and os.path.dirname(dp) == top:
                    try:
                        os.rmdir(dp)
                    except OSError:
                        pass
    done, plan, trouble = 0, [], []
    CX = cancelled()
    for src in SOURCES:
        if src['remote'] not in have:
            continue
        loc = lambda p, src=src: '%s:%s' % (src['remote'], (src['path'] + '/' + p) if src['path'] else p)
        try:
            items = json.loads(rc('lsjson', '-R', '--files-only', loc('').rstrip('/')) or '[]')
        except Exception as e:
            log('%s: couldn’t look (%s)' % (src['name'], e))
            trouble.append('Couldn’t look in %s: %s' % (src['name'], str(e)[-200:]))   # (shown on the admin page, not "nothing waiting")
            continue
        films = [x for x in items if x['Path'].lower().endswith(VIDEO) and 'sample' not in x['Name'].lower()]
        subs = [x for x in items if x['Path'].lower().endswith('.srt')]
        xs = [f for f in films if extra_of(f['Path'])]
        films = [f for f in films if not extra_of(f['Path'])]
        for f in films + xs:
            path, size = f['Path'], max(0, int(f.get('Size') or 0))
            key = '%s:%s|%d' % (src['remote'], path, size)
            if not src['delete'] and key in seen:
                continue   # (fetched before: TorBox keeps its copy)
            xf = extra_of(path)
            if xf is not None:   # (an extra: named after its film, the film in that folder of the download, as that is named)
                film = next((x for x in films if os.path.dirname(x['Path']) == xf), None)
                if film:
                    f = dict(f, xname=f['Name'], Name=film['Name'], Path=film['Path'])
                else:
                    f = dict(f, xname=f['Name'], Name=os.path.basename(xf) + '.mkv', Path=xf + '/' + os.path.basename(xf) + '.mkv')
            folder = os.path.dirname(f['Path'])
            ext = os.path.splitext(f.get('xname') or f['Name'])[1].lower()
            m = None if xf is not None else EP.search(os.path.splitext(f['Name'])[0])
            nkey = folder if (m and folder) else f['Path']   # (an extra: its film's, so the film's own name, if given, is its too)   # (a series' episodes named together: the folder they came in)
            if CX & {path, f['Path'], nkey}:
                continue   # (cancelled by John: left where it is)
            mine = N.get(nkey) or N.get(f['Path'])
            if mine:
                title, year = '%s (%s)' % (mine['name'], mine['year']) if mine.get('year') else mine['name'], bool(mine.get('year'))
            elif m:   # (an episode: the show's name before "S01E02", from the file or else its folder)
                title, year = title_of(f['Name'][:m.start()] or os.path.basename(folder))
                if not year and folder:
                    fm = EP.search(os.path.basename(folder)) or re.search(r'(?:^|[ ._-])(S\d{1,2}|Season)', os.path.basename(folder), re.I)
                    t2, y2 = title_of(os.path.basename(folder)[:fm.start()] if fm else os.path.basename(folder))
                    if y2:
                        title, year = t2, y2
            else:
                title, year = title_of(f['Name'])
                if not year and folder:
                    t2, y2 = title_of(os.path.basename(folder))
                    if y2:
                        title, year = t2, y2
            series = False if xf is not None else (mine.get('kind') == 'series') if mine else bool(m)
            if series and not R.get('tvshows'):
                series = False
            tm = None
            if not mine:   # (checked on TMDB: its exact title, when TMDB has one close, from the same year)
                ck = '%s|%s|%s' % (title, year, series)
                if ck not in TC:
                    try:
                        TC[ck] = tmdb_name(title, re.search(r'\((\d{4})\)$', title).group(1) if year else None, series)
                    except Exception as e:
                        log('  TMDB didn’t answer for %s: %s' % (title, e))
                tm = TC.get(ck)
                if tm:
                    t2 = re.sub(r'[/:\\]', '-', '%s (%s)' % (tm['name'], tm['year']) if tm.get('year') else tm['name']).strip(' .')
                    if t2 != title:
                        if check:
                            log('  TMDB: %s is %s' % (title, t2))
                        title, year = t2, bool(tm.get('year'))
            if series:   # (the TV library, as the media server wants it: Show (Year)/Season 01/Show (Year) S01E02.mkv)
                sn, en = (int(m.group(1)), int(m.group(2))) if m else (1, 0)
                dest_dir = os.path.join(R['tvshows'], title, 'Season %02d' % sn)
                epname = '%s S%02dE%02d' % (title, sn, en) if m else title
                dest = os.path.join(dest_dir, epname + ext)
            elif xf is not None:   # (beside its film, in Other, as itself)
                dest_dir = os.path.join(root, title)
                dest = os.path.join(dest_dir, 'Other', re.sub(r'[/:\\]', '-', f['xname']))
            else:
                dest_dir = os.path.join(root, title)
                dest = os.path.join(dest_dir, title + ext)
            if (os.path.exists(dest) if series or xf is not None else os.path.isdir(dest_dir) and any(n.lower().endswith(VIDEO) for n in os.listdir(dest_dir))):
                if key not in seen:
                    log('Left in %s: %s ("%s" is already in Grifflix)' % (src['name'], path, title))
                    if not check:
                        seen[key] = int(time.time())
                continue
            if check and (os.path.exists(dest + '.adding') or os.path.exists(dest + '.adding.d')):
                continue   # (coming in now)
            plan.append(dict(src=src, loc=loc, path=path, size=size, key=key, folder=folder, title=title, year=year, ext=ext,
                             dest_dir=dest_dir, dest=dest, name=f['Name'], subs=subs, series=series, nkey=nkey, named=bool(mine), extra=f.get('xname'),
                             ep='S%02dE%02d' % (int(m.group(1)), int(m.group(2))) if m else '', tmdb=bool(tm) or bool(mine)))
    try:
        json.dump(TC, open(TMDBC, 'w'))
    except OSError:
        pass
    plan = in_order(plan)
    free0 = shutil.disk_usage(root).free
    status(checked=int(time.time()), trouble=trouble, waiting=[dict(wait_entry(p), noroom=free0 < p['size'] + 20e9) for p in plan], **({} if check else {'now': None}))
    while plan:
        try:   # (a newer version since this fetch began: finish here, between films, and start again as it; never a download thrown
               # away part way: Donnie Darko, 9.4 GB, began again after Update everything: John, 15 Nov 2026, "Downloads seem to have slowed")
            if os.path.getmtime(os.path.abspath(__file__)) != MINE and not check:
                log('A newer version of this: carrying on with it')
                os.remove(LOCK)
                sys.stdout.flush()
                os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
        except OSError:
            pass
        cx = cancelled()   # (one cancelled while another came in: not fetched)
        plan = [q for q in plan if not cx & {q['path'], q['nkey']}]
        if not plan:
            break
        plan = in_order(plan)   # (John's next, looked at again before each: one he chooses mid-download goes after this one)
        p = plan.pop(0)
        if not check:
            status(waiting=[dict(wait_entry(q), noroom=shutil.disk_usage(root).free < q['size'] + 20e9) for q in plan])
        src, loc, path, size, key, folder, title, year, ext, dest_dir, dest, subs = (p[k] for k in
            ('src', 'loc', 'path', 'size', 'key', 'folder', 'title', 'year', 'ext', 'dest_dir', 'dest', 'subs'))
        if check:
            log('Would add from %s: %s → %s/%s%s' % (src['name'], path, title, 'Other/' + os.path.basename(dest) if p.get('extra') else title + ext, '' if year else ' (no year in its name)'))
            continue
        if p.get('extra'):
            if os.path.exists(dest):
                continue
            if not (os.path.isdir(dest_dir) and any(n.lower().endswith(VIDEO) for n in os.listdir(dest_dir))):
                log('  %s: waiting for %s itself to come first' % (p['extra'], title))
                continue
        elif (os.path.exists(dest) if p['series'] else os.path.isdir(dest_dir) and any(n.lower().endswith(VIDEO) for n in os.listdir(dest_dir))):
            continue   # (the same film twice: from both places)
        try:   # (another fetch writing it this minute: left to that one)
            if time.time() - max(os.path.getmtime(x) for x in (dest + '.adding', dest + '.adding.d') if os.path.exists(x)) < 300:
                log('  %s is being fetched already: left to that' % title)
                continue
        except (OSError, ValueError):
            pass
        free = shutil.disk_usage(root).free
        if free < size + 20e9:
            log('Not enough room on the drive for %s (%.0f GB free): left in %s' % (path, free / 1e9, src['name']))
            continue
        log('Fetching from %s: %s → %s/%s (%.1f GB)' % (src['name'], path, title, 'Other/' if p.get('extra') else '', size / 1e9))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        part = dest + '.adding'
        status(now={'from': src['name'], 'file': path, 'to': title, 'extra': p.get('extra'), 'size': size, 'part': part, 'since': int(time.time())})
        came = lambda ok, why='', title=title + (' · extra: ' + os.path.splitext(p['extra'])[0] if p.get('extra') else ''), src=src, size=size: status(now=None, came={'to': title, 'from': src['name'], 'gb': round(size / 1e9, 1), 'at': int(time.time()), 'ok': ok, 'why': why})
        said = ''
        try:   # (in Terminal: how far, how fast, how long to go, as it downloads)
            said = fetch(loc, path, part, show=sys.stdout.isatty())
        except Exception as e:
            log('  the download failed (%s): left in %s' % (e, src['name']))
            came(False, 'the download failed: left in %s' % src['name'])
            if os.path.exists(part):
                os.remove(part)
            try:
                os.rmdir(dest_dir)
            except OSError:
                pass
            continue
        try:
            got = os.path.getsize(part)
        except OSError:   # (gone: another fetch took it; Ghosts, 15 Nov 2026, two at once just after an update)
            log('  %s: it didn’t come (rclone said: %s): left in %s, tried again next time' % (title, said.replace('\n', ' / ')[-300:] or 'nothing', src['name']))
            came(False, 'it didn’t come: ' + (said.splitlines()[-1][-160:] if said else 'tried again next time'))
            continue
        if size and got != size or not got:
            log('  the copy isn’t whole (%d of %d bytes): left in %s' % (got, size, src['name']))
            came(False, 'the copy wasn’t whole: left in %s' % src['name'])
            os.remove(part)
            continue
        os.replace(part, dest)
        if p.get('extra'):   # (an extra: nothing more to do, the media server finds it in Other)
            seen[key] = int(time.time())
            log('  done: %s in %s/Other' % (p['extra'], title))
            came(True)
            try:
                tmp = SEEN + '.tmp'
                json.dump(seen, open(tmp, 'w'))
                os.replace(tmp, SEEN)
            except OSError:
                pass
            continue
        # (its subtitles: beside it, with the film's new name)
        stem = os.path.splitext(p['name'])[0]
        for s_ in subs:
            if os.path.dirname(s_['Path']) != folder or ((not folder or p['series']) and not s_['Name'].startswith(stem)):
                continue
            sb = os.path.splitext(s_['Name'])[0]
            lang = '.' + sb.rsplit('.', 1)[1] if re.search(r'\.[a-z]{2,3}$', sb) else ''
            try:
                sp = os.path.splitext(dest)[0] + lang + '.srt'
                fetch(loc, s_['Path'], sp + '.adding')
                if os.path.exists(sp + '.adding'):
                    os.replace(sp + '.adding', sp)
                if src['delete']:
                    rc('deletefile', '--drive-use-trash=false', loc(s_['Path']))
            except Exception as e:
                log('  its subtitles %s: %s' % (s_['Name'], e))
        if src['delete']:
            rc('deletefile', '--drive-use-trash=false', loc(path))
        seen[key] = int(time.time())
        log('  done: on the G-Drive' + (', and deleted from ' + src['name'] if src['delete'] else '') +
            ('' if year else '. NOTE: "%s" has no year in its name, so the media server may match the wrong film: if it does, Fix match on it in Grifflix' % title))
        done += 1
        came(True, '' if year else 'no year in its name: check it’s the right film')
        try:   # (the Mac told at once: the media server looks now, the converter takes it next: John, 15 Nov 2026)
            s_ = json.load(open(os.path.join(APP, 'settings.json')))
            urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%s/local/fetched' % s_.get('port', 8097), data=json.dumps({'path': dest}).encode(),
                                   method='POST', headers={'X-Grifflix-Secret': s_.get('secret', ''), 'Content-Type': 'application/json'}), timeout=30).read()
        except Exception:
            pass   # (the gateway's own look, every 10 minutes, finds it anyway)
        if ext not in ('.mp4', '.m4v'):   # (an MKV and the like: the converter told now, to repackage it next: John, 15 Nov 2026,
            try:                          # "the mkv films should immediately be put to the top of the list for repackaging")
                nf = os.path.join(HOME, 'convert-new.json')
                try:
                    have = json.load(open(nf))
                except (OSError, ValueError):
                    have = []
                json.dump(have + [{'path': dest, 'name': title, 'at': int(time.time())}], open(nf, 'w'))
            except OSError:
                pass
        if ext not in ('.mp4', '.m4v') and not check:   # (repackaged before the next download: John, 15 Nov 2026, "when downloaded it should be repackaged first")
            wait_repack(dest, title)
        if src['delete']:
            rc('rmdirs', '--leave-root', loc('').rstrip('/'), check=False)   # (download folders left empty)
        try:
            tmp = SEEN + '.tmp'
            json.dump(seen, open(tmp, 'w'))
            os.replace(tmp, SEEN)
        except OSError:
            pass
    if not check:
        status(now=None)
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


def tidy():
    """Folders already fetched with a squashed or website name ("www UIndex org - Hamlet …／Hamlet (2026)") renamed as they should be,
    with the film's files inside them."""
    root = films_root()
    n = 0
    for d in sorted(os.listdir(root)):
        if not ('／' in d or re.match(r'^\s*(\[\s*)?(www[ .])?[a-z0-9-]+[ .](org|com|net|to|me|cc|tv|io|ws|in|co|uk|nl|se|si|sx|ch|bz)\b', d, re.I)):
            continue
        t, _ = title_of(d)
        src, dst = os.path.join(root, d), os.path.join(root, t)
        if not os.path.isdir(src) or not t or t == d:
            continue
        if os.path.exists(dst):
            print('Left: %s ("%s" is there already)' % (d, t))
            continue
        for f in os.listdir(src):
            if f.startswith(d):
                os.rename(os.path.join(src, f), os.path.join(src, t + f[len(d):]))
        os.rename(src, dst)
        print('Renamed: %s → %s' % (d, t))
        n += 1
    print('%d folder(s) put right.' % n if n else 'Nothing to put right.')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'setup':
        setup()
    elif cmd in ('run', 'check'):
        run(check=cmd == 'check')
    elif cmd == 'tidy':
        tidy()
    elif cmd == 'again' and len(sys.argv) > 2:   # (fetched before, wrongly: fetched again next time, e.g. an episode that went in as a film)
        w = ' '.join(sys.argv[2:]).lower().replace(' ', '.')
        try:
            seen = json.load(open(SEEN))
        except (OSError, ValueError):
            seen = {}
        gone = [k for k in seen if w in k.lower().replace(' ', '.')]
        for k in gone:
            seen.pop(k)
        json.dump(seen, open(SEEN, 'w'))
        print('%d to fetch again next time%s' % (len(gone), ''.join('\n  ' + k.split('|')[0] for k in gone)))
    else:
        print(__doc__)
