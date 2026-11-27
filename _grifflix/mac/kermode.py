#!/usr/bin/env python3
"""Mark Kermode's introductions to the BFI's films: the new ones got, made MP4s and put on the G-Drive for Grifflix, none got twice (John,
26 Nov 2026: "I have more to get, so how can I move them and the script check, so we don't get them again").

On John's Mac, in Terminal. Once, to have it:
  curl -fsSL https://raw.githubusercontent.com/cooperking/cooperking.github.io/main/_grifflix/mac/kermode.py -o ~/Grifflix/kermode.py --create-dirs

  python3 ~/Grifflix/kermode.py URL…   the first time: in the folder the introductions are in, with the YouTube playlist(s) they're from
  python3 ~/Grifflix/kermode.py        after that, from anywhere: the same folder and playlists, only the new ones got
  python3 ~/Grifflix/kermode.py --new  only says which are new, getting nothing
  python3 ~/Grifflix/kermode.py --move only makes MP4s of what's in the folder and puts them on the G-Drive, getting nothing
  python3 ~/Grifflix/kermode.py --make only makes MP4s of what's in the folder, to drag into BFI Kermode in Finder yourself (John, 26 Nov
                                       2026: "moving the files is extremely slow. I can just move them myself in finder")
  python3 ~/Grifflix/kermode.py --here this folder used from now on, not the one before
  python3 ~/Grifflix/kermode.py --install    it runs by itself from now on, in the background, every 6 hours (and now): new ones got, made
                                             and put on the G-Drive when you're at home, with a notification (John, 26 Nov 2026: "I would
                                             like that Kermode playlist checked and automatically downloaded, in a similar way to the song
                                             of the day script"); what it did in ~/Grifflix/kermode.log
  python3 ~/Grifflix/kermode.py --uninstall  not by itself any more

On the Mac mini, the gateway runs it by itself once a week (KERMODE_HOST=mini), not John's Mac (John, 26 Nov 2026: "I would like the
automatic check done on the mac mini and not use my mac - my mac is just for now"; "once a week for checking is enough"): yt-dlp in its
own folder there (~/Grifflix/ytdlp), and the MP4s straight into BFI Kermode beside it. Once it does, John's Mac stops its own (--install).

What it does, in order:
 1. What's been got already: every YouTube code in a file name in the folder (its originals folder too), kept in ~/Grifflix/kermode-got.txt
    and on the G-Drive (BFI Kermode/.got.txt), so nothing is got twice, from this Mac or another. Any in the playlist whose name is one
    already here, in BFI Kermode, or with its film on the G-Drive, is added to it too, not got again.
 2. The new ones got with yt-dlp (only videos with Kermode in the name, even from a whole channel), each named as YouTube has it, with its
    code, so it's known next time.
 3. Each MKV or WebM made an MP4 named "Mark Kermode introduces Title (Year)": the picture copied if it's H.264 already, else made by the
    Mac's own encoder; the sound AAC. The original put in the originals folder (keep or delete it, as you like).
 4. The MP4s put in G-Drive › Grifflix › BFI Kermode (copied, checked, then taken from here). The Mac mini puts each with its film within
    10 minutes; those it isn't sure of wait on the admin page's BFI tab.
Files still arriving (changed in the last 2 minutes, or half-downloaded) are left for next time.
"""
import glob, json, os, re, shutil, subprocess, sys, tempfile, time, unicodedata

HOME = os.path.expanduser('~')
G = os.path.join(HOME, 'Grifflix')
CONF = os.path.join(G, 'kermode.json')
GOT = os.path.join(G, 'kermode-got.txt')
NAMES = os.path.join(G, 'kermode-names.txt')   # (the name of every one put on the G-Drive, so one the playlist has under another code isn't got again)
GD = os.environ.get('GRIFFLIX_GD') or '/Volumes/G-Drive'
DEST = os.path.join(GD, 'Grifflix', 'BFI Kermode')
MINI = '192.168.68.125'   # (the Mac mini, as convert-here.sh has it)
CODE = re.compile(r'\[([A-Za-z0-9_-]{11})\]')
VID = ('.mkv', '.webm')
HALF = re.compile(r'\.(part|ytdl|temp)(\.|$)|\.f\d+\.\w+$|^\.')
AUTO = '--auto' in sys.argv   # (run by launchd: quiet, polite to YouTube, never asking anything)
ON_MINI = os.environ.get('KERMODE_HOST') == 'mini'   # (run by the gateway on the Mac mini, the G-Drive its own)
VENV = os.path.join(G, 'ytdlp')   # (the Mac mini's yt-dlp, in a folder of its own: pip's, so it runs on its older macOS)
LABEL = 'io.github.cooperking.grifflix-kermode'
AGENT = os.path.join(HOME, 'Library', 'LaunchAgents', LABEL + '.plist')
LOGF = os.path.join(G, 'kermode.log')
LOCK = os.path.join(G, 'kermode.lock')
SRC = 'https://raw.githubusercontent.com/cooperking/cooperking.github.io/main/_grifflix/mac/kermode.py'
PER_RUN = 40   # (by itself, at most this many got each time: YouTube stopped a run of hundreds for an hour, 26 Nov 2026)

ROMAN = {'ii': '2', 'iii': '3', 'iv': '4', 'vi': '6', 'vii': '7', 'viii': '8', 'ix': '9', 'xi': '11', 'xii': '12', 'xiii': '13', 'xiv': '14', 'xv': '15', 'xvi': '16', 'xvii': '17', 'xviii': '18', 'xix': '19', 'xx': '20'}


def fold(t):   # (as the Mac mini compares titles: letters and numbers only, accents plain, "&" as "and", numbers to twenty as words, no leading "the")
    NUM = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split()
    t = unicodedata.normalize('NFKD', (t or '').replace('&', ' and ')).encode('ascii', 'ignore').decode().lower()
    t = re.sub(r'\b(xviii|viii|xiii|xvii|iii|vii|xii|xiv|xvi|xix|ii|iv|vi|ix|xi|xv|xx)\b', lambda m: ROMAN[m.group(1)], t)
    t = re.sub(r'\b(\d{1,2})\b', lambda m: NUM[int(m.group(1))] if int(m.group(1)) <= 20 else m.group(1), t)
    t = re.sub(r'[^a-z0-9]', '', t)
    return t[3:] if t.startswith('the') and len(t) > 6 else t


def tidy(stem):
    """"Mark Kermode introduces Title (Year)", as the Mac mini names them (its bfi_tidy): (name, title, year)."""
    t = unicodedata.normalize('NFC', stem or '')
    t = re.sub(r'(?i)(\.(mp4|m4v|mkv|webm|mov))+$', '', t)
    t = re.sub(r'\s*\[[A-Za-z0-9_-]{11}\]\s*$', '', t)
    t = re.sub(r'(?i)\s*[｜|]\s*bfi\b.*$', '', t)
    for a, b in (('＂', '"'), ('：', ':'), ('？', '?'), ('／', '/'), ('＊', '*'), ('＜', '<'), ('＞', '>'), ('⧸', '/'), ('’', "'"), ('‘', "'"), ('“', '"'), ('”', '"')):
        t = t.replace(a, b)
    t = re.sub(r'[_]+', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip(' -–—.')
    m = re.match(r'(?i)^(.+?)\s*[-–—:,]?\s*\b(?:introduced|presented|reviewed) by (?:mark )?kermode$', t)
    if m:
        t = m.group(1)
    else:
        m = re.match(r"(?i)^(?:mark\s+)?kermode(?:'s)?\s*(?:introduces|introducing|introduction to|intro to|introduction|reviews|review of|review|on|presents|talks about|discusses)?\s*[:\-–—]?\s+(.*)$", t)
        if m:
            t = m.group(1)
    title = t
    y, yr = None, int(time.strftime('%Y')) + 1
    mm = re.search(r'\s*[\(\[]?\s*((?:18|19|20)\d\d)\s*[\)\]]?\s*$', title)
    if mm and 1880 <= int(mm.group(1)) <= yr and title[:mm.start()].strip(' -–—,'):
        y, title = int(mm.group(1)), title[:mm.start()].strip(' -–—,')
    title = re.sub(r'\s+', ' ', title.strip(' "\'')).strip()
    title = re.sub(r'\s+([,:;!?])', r'\1', title)
    title = re.sub(r'(?<=\w)\(', ' (', title)
    safe = title.replace(': ', ' - ').replace(':', '-').replace('"', "'")
    safe = re.sub(r'[?*<>]', '', safe)
    safe = re.sub(r'\s+', ' ', re.sub(r'[/\\|]', '-', safe)).strip(' .')
    return 'Mark Kermode introduces %s%s' % (safe, ' (%d)' % y if y else ''), title, y


def key(name):
    _, title, year = tidy(name)
    return fold(title), year


def tool(name):
    for p in [os.path.join(VENV, 'bin', name), os.path.join(HOME, 'bin', name), '/opt/homebrew/bin/' + name, '/usr/local/bin/' + name, shutil.which(name)] + \
             sorted(glob.glob(os.path.join(HOME, 'Library/Python/*/bin', name))) + \
             (['/Applications/Jellyfin.app/Contents/MacOS/' + name] if name in ('ffmpeg', 'ffprobe') else []):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def say(*a):
    print(*a, flush=True)


def fetch(url, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    return subprocess.run(['curl', '-fL', '--progress-bar', '-o', dst + '.part', url]).returncode == 0 and not os.rename(dst + '.part', dst)


def ytdlp():
    """yt-dlp, and Deno, which it needs for YouTube now: the Mac's own if it has them, else each fetched once into ~/bin (one file each, from
    their own GitHub pages; no administrator rights)."""
    y = tool('yt-dlp')
    if ON_MINI:   # (always its own, pip's, in a folder of its own, kept up to date: another yt-dlp found on the Mac mini read nothing of the
        # playlist, 27 Nov 2026; and the standalone one needs a newer macOS than the 2010 Mac mini's)
        y = os.path.join(VENV, 'bin', 'yt-dlp')
        if not (os.path.isfile(y) and os.access(y, os.X_OK)):
            say('Putting yt-dlp on the Mac mini (once), in %s …' % VENV)
            subprocess.run([sys.executable, '-m', 'venv', VENV])
            pip = [os.path.join(VENV, 'bin', 'python3'), '-m', 'pip', 'install', '-q', '-U']
            if subprocess.run(pip + ['yt-dlp[default]']).returncode:
                subprocess.run(pip + ['yt-dlp'])
            if not (os.path.isfile(y) and os.access(y, os.X_OK)):
                sys.exit('Couldn’t put yt-dlp on the Mac mini')
    if not y:
        say('Fetching yt-dlp (once) into ~/bin …')
        y = os.path.join(HOME, 'bin', 'yt-dlp')
        if not fetch('https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos', y):
            sys.exit('Couldn’t fetch yt-dlp. Try again, or: brew install yt-dlp')
        os.chmod(y, 0o755)
    nod = os.path.join(G, 'deno-wont-run-%s' % os.uname().machine)   # (tried, and this Mac's macOS too old for it: not fetched again for a month)
    if not deno() and not (os.path.exists(nod) and time.time() - os.path.getmtime(nod) < 30 * 86400):
        say('Fetching Deno (once) into ~/bin, which yt-dlp needs for YouTube …')
        arch = 'aarch64' if os.uname().machine == 'arm64' else 'x86_64'
        z = os.path.join(tempfile.mkdtemp(), 'deno.zip')
        if fetch('https://github.com/denoland/deno/releases/latest/download/deno-%s-apple-darwin.zip' % arch, z):
            subprocess.run(['unzip', '-oq', z, '-d', os.path.join(HOME, 'bin')])
            os.chmod(os.path.join(HOME, 'bin', 'deno'), 0o755)
            if not deno():
                open(nod, 'w').write(time.strftime('%Y-%m-%d'))
                say('Deno won’t run on this Mac’s macOS: yt-dlp goes on without it (it may get fewer of YouTube’s formats).')
        else:
            say('Couldn’t fetch Deno: yt-dlp may get only some videos from YouTube without it.')
    return y


def deno():
    """Deno's path, if it's here and runs (the 2010 Mac mini's macOS may be too old for it)."""
    for d in (tool('deno'), os.path.join(HOME, '.deno', 'bin', 'deno')):
        if d and os.path.exists(d):
            try:
                if subprocess.run([d, '--version'], capture_output=True, timeout=30).returncode == 0:
                    return d
            except Exception:
                pass
    return None


def keep_fresh(y):
    """yt-dlp brought up to date once a day: YouTube changes its player often, and an older yt-dlp can't solve its puzzles ("n challenge
    solving failed ... Some formats may be missing": John, 26 Nov 2026). One from Homebrew or pip is updated the way it came, said so."""
    stamp = os.path.join(G, 'ytdlp-checked')
    try:
        if time.time() - os.path.getmtime(stamp) < 86400:
            return
    except OSError:
        pass
    try:
        if y.startswith(VENV + '/'):   # (the Mac mini's: pip's own update)
            r = subprocess.run([os.path.join(VENV, 'bin', 'python3'), '-m', 'pip', 'install', '-q', '-U', 'yt-dlp[default]'], capture_output=True, text=True, timeout=600)
            out = 'updated yt-dlp to the newest' if r.returncode == 0 else (r.stderr or '')[-300:]
        else:
            r = subprocess.run([y, '-U'], capture_output=True, text=True, env=env(), timeout=300)
            out = ((r.stdout or '') + (r.stderr or '')).strip()
    except Exception as e:
        out = str(e)
    open(stamp, 'w').write(out[-500:])
    if re.search(r'(?i)updated yt-dlp to|updating to', out):
        say('yt-dlp brought up to date.')
    elif re.search(r'(?i)\bpip\b|pypi', out):
        say('yt-dlp came with pip: to bring it up to date, in Terminal: python3 -m pip install -U "yt-dlp[default]"')
    elif re.search(r'(?i)package manager|homebrew|brew', out):
        say('yt-dlp came with Homebrew: to bring it up to date, in Terminal: brew upgrade yt-dlp')


def solver(y):
    """The newest puzzle solver fetched by yt-dlp itself from its own GitHub, where this yt-dlp can (2025.11 on), so a YouTube change
    between yt-dlp's releases doesn't stop it; and Deno named outright, where it can be, so it's found however this is started ("No
    supported JavaScript runtime could be found": John, 26 Nov 2026)."""
    try:
        h = subprocess.run([y, '--help'], capture_output=True, text=True, env=env(), timeout=60).stdout or ''
    except Exception:
        h = ''
    d = deno()
    return (['--remote-components', 'ejs:github'] if '--remote-components' in h else []) + \
           (['--js-runtimes', 'deno:' + d] if d and '--js-runtimes' in h else [])


def env():
    e = dict(os.environ)
    e['PATH'] = ':'.join([os.path.join(HOME, 'bin'), os.path.join(HOME, '.deno', 'bin'), '/opt/homebrew/bin', '/usr/local/bin', e.get('PATH', '')])
    return e


def got_read():
    have = set()
    for f in (GOT, os.path.join(DEST, '.got.txt')):
        try:
            for l in open(f, encoding='utf-8'):
                p = l.split()
                if len(p) == 2:
                    have.add(p[1])
        except OSError:
            pass
    return have


def got_write(ids):
    """The codes got, here and on the G-Drive (where it's there), both kept whole."""
    have = got_read() | set(ids)
    text = ''.join('youtube %s\n' % i for i in sorted(have))
    for f in (GOT, os.path.join(DEST, '.got.txt')):
        if f.startswith(GD) and not os.path.isdir(DEST):
            continue
        try:
            os.makedirs(os.path.dirname(f), exist_ok=True)
            open(f + '.new', 'w', encoding='utf-8').write(text)
            os.replace(f + '.new', f)
        except OSError as e:
            say('Couldn’t keep the list in %s: %s' % (f, e))
    return have


def names_here(folder):
    """Every introduction's name here: in the folder, its originals, and BFI Kermode on the G-Drive."""
    out = []
    for d in (folder, os.path.join(folder, 'originals'), DEST):
        try:
            out += [n for n in os.listdir(d) if n.lower().endswith(('.mp4',) + VID) and not n.startswith('.')]
        except OSError:
            pass
    return out


def names_read():
    """The names of all those put on the G-Drive before, from this Mac or another (looking through every film's folder over the network
    took too long: John, 26 Nov 2026)."""
    out = set()
    for f in (NAMES, os.path.join(DEST, '.names.txt')):
        try:
            out |= {l.strip() for l in open(f, encoding='utf-8') if l.strip()}
        except OSError:
            pass
    return out


def names_add(names):
    have = names_read() | set(names)
    text = ''.join('%s\n' % n for n in sorted(have))
    for f in (NAMES, os.path.join(DEST, '.names.txt')):
        if f.startswith(GD) and not os.path.isdir(DEST):
            continue
        try:
            open(f + '.new', 'w', encoding='utf-8').write(text)
            os.replace(f + '.new', f)
        except OSError:
            pass


def good_url(u):
    """A whole address: not one with "…" in it for the playlist's code, as in an example (John, 26 Nov 2026)."""
    return bool(re.match(r'https?://\S+$', u)) and '…' not in u and '...' not in u and \
        (not re.search(r'[?&]list=', u) or bool(re.search(r'[?&]list=[A-Za-z0-9_-]{10,}', u)))


def secs(ff, path):
    """How long a video is, from ffmpeg (None if it can't be read: one stopped part way has no index)."""
    r = subprocess.run([ff, '-hide_banner', '-i', path], capture_output=True, text=True).stderr or ''
    m = re.search(r'Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)', r)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m and 'moov atom not found' not in r else None


def whole(ff, out, src):
    """An MP4 made before is whole: it can be read, and it's as long as what it was made from, give or take two seconds."""
    a = secs(ff, out)
    if a is None:
        return False
    b = secs(ff, src) if src and os.path.exists(src) else None
    return b is None or a >= b - 2


def at_home():
    """The Mac mini answers here (its file sharing): at home, not at work, where asking for the G-Drive would only put up a box."""
    import socket
    try:
        socket.create_connection((MINI, 445), timeout=3).close()
        return True
    except OSError:
        return False


def drive():
    if os.path.isdir(GD):
        return True
    if AUTO and not at_home():
        return False
    say('Connecting to the G-Drive (a box may ask for the Mac mini’s sign-in) …')
    subprocess.run(['open', 'smb://%s/G-Drive' % MINI])
    for _ in range(120):
        if os.path.isdir(GD):
            return True
        time.sleep(1)
    return False


def listing(y, urls):
    """The playlists' videos with Kermode in the name: [(code, title)]."""
    out = []
    for u in urls:
        r = subprocess.run([y, '--flat-playlist', '--ignore-errors', '--print', '%(id)s\t%(title)s', u], capture_output=True, text=True, env=env())
        n = 0
        for l in (r.stdout or '').splitlines():
            i, _, t = l.partition('\t')
            if re.fullmatch(r'[A-Za-z0-9_-]{11}', i):
                n += 1
                if re.search(r'(?i)kermode', t):
                    out.append((i, t))
        if not n:   # (nothing read at all, whatever yt-dlp said of it: which yt-dlp, and what it said, for the log)
            try:
                v = subprocess.run([y, '--version'], capture_output=True, text=True, env=env(), timeout=60).stdout.strip()
            except Exception as e:
                v = str(e)
            said = [l for l in (r.stderr or '').strip().splitlines() if l.strip()][-3:]
            say('Couldn’t read %s with %s (%s), which said: %s' % (u, y, v or 'no version', ' / '.join(said) or 'nothing'))
    return out


def convert(folder, fresh):
    """Each MKV or WebM made an MP4 by its tidy name, the original put in originals."""
    ff = tool('ffmpeg')
    if not ff:
        say('No ffmpeg on this Mac: from osxexperts.net (Apple Silicon) unzip ffmpeg into ~/bin, then: xattr -d com.apple.quarantine ~/bin/ffmpeg')
        return 0
    n = 0
    for f in os.listdir(folder):   # (ones a stop part way left: made again)
        if f.startswith('.making-'):
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass
    for f in sorted(os.listdir(folder)):
        p = os.path.join(folder, f)
        if not f.lower().endswith(VID) or HALF.search(f) or not os.path.isfile(p):
            continue
        if p not in fresh and time.time() - os.path.getmtime(p) < 120:
            continue
        out = os.path.join(folder, tidy(os.path.splitext(f)[0])[0] + '.mp4')
        if os.path.exists(out):
            if whole(ff, out, p):   # (made before: the original put away)
                os.makedirs(os.path.join(folder, 'originals'), exist_ok=True)
                shutil.move(p, os.path.join(folder, 'originals', f))
                say('Already made: %s' % os.path.basename(out))
                continue
            os.remove(out)   # (stopped part way, by Ctrl-C or the Mac sleeping: made again)
            say('Making again (it was stopped part way): %s' % os.path.basename(out))
        tmp = os.path.join(folder, '.making-' + os.path.basename(out))
        info = subprocess.run([ff, '-hide_banner', '-i', p], capture_output=True, text=True).stderr or ''
        v = ['-c:v', 'copy'] if re.search(r'Video: h264', info) else ['-c:v', 'h264_videotoolbox', '-q:v', '65']
        base = [ff, '-hide_banner', '-nostdin', '-v', 'error', '-i', p, '-map', '0:v:0', '-map', '0:a:0?']
        tail = ['-tag:v', 'avc1', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-f', 'mp4', '-y', tmp]
        try:   # (made under a hidden name, and named only when whole: one stopped part way is never taken for finished)
            ok = subprocess.run(base + v + tail).returncode == 0
            if not ok:
                ok = subprocess.run(base + ['-c:v', 'libx264', '-crf', '20', '-preset', 'medium', '-pix_fmt', 'yuv420p'] + tail).returncode == 0
            if ok:
                os.rename(tmp, out)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        if ok:
            os.makedirs(os.path.join(folder, 'originals'), exist_ok=True)
            shutil.move(p, os.path.join(folder, 'originals', f))
            say('Made: %s' % os.path.basename(out))
            fresh.add(out)
            n += 1
        else:
            say('Couldn’t make an MP4 of: %s' % f)
    return n


def move(folder, fresh):
    """The MP4s to G-Drive › Grifflix › BFI Kermode: copied under a hidden name, checked, named, then taken from here."""
    todo = [f for f in sorted(os.listdir(folder)) if f.lower().endswith('.mp4') and not HALF.search(f)
            and (os.path.join(folder, f) in fresh or time.time() - os.path.getmtime(os.path.join(folder, f)) >= 120)]
    if not todo:
        return 0
    if not drive():
        say('The G-Drive isn’t connected: in Finder, Go › Connect to Server, smb://%s/G-Drive, then run this again. The MP4s stay here till then.' % MINI)
        return 0
    os.makedirs(DEST, exist_ok=True)
    for f in os.listdir(DEST):   # (copies a stop part way left)
        if f.startswith('.copying-'):
            try:
                os.remove(os.path.join(DEST, f))
            except OSError:
                pass
    ff = tool('ffmpeg')
    n = 0
    say('Putting %d on the G-Drive, one at a time (about a minute each while the Mac mini is busy) …' % len(todo))
    for k, f in enumerate(todo, 1):
        src = os.path.join(folder, f)
        name = tidy(os.path.splitext(f)[0])[0] + '.mp4'
        dst = os.path.join(DEST, name)
        if os.path.exists(dst):
            say('Already in BFI Kermode, left here: %s' % f)
            continue
        src_ = next((os.path.join(folder, x) for x in os.listdir(folder) if x.lower().endswith(VID) and tidy(os.path.splitext(x)[0])[0] + '.mp4' == name), None)
        if src_ or ff and secs(ff, src) is None:   # (its original still waiting to be made into it, or unreadable: stopped part way)
            say('Not whole yet, left here: %s' % f)
            continue
        tmp = os.path.join(DEST, '.copying-' + name)
        try:
            shutil.copyfile(src, tmp)
            if os.path.getsize(tmp) != os.path.getsize(src):
                raise OSError('the copy came out a different size')
            os.rename(tmp, dst)
            os.remove(src)
            names_add([os.path.splitext(name)[0]])
            n += 1
            say('On the G-Drive (%d of %d): %s' % (k, len(todo), name))
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            say('Couldn’t put %s on the G-Drive: %s' % (f, e))
    return n


def notify(text):
    if AUTO and not ON_MINI:
        try:
            subprocess.run(['osascript', '-e', 'display notification "%s" with title "Grifflix: Mark Kermode"' % text.replace('"', "'")], capture_output=True)
        except OSError:
            pass


def install():
    """By itself from now on: a LaunchAgent, as the Song of the Day uploader has, every 6 hours and now."""
    try:
        C = json.load(open(CONF))
    except (OSError, ValueError):
        C = {}
    if not C.get('folder') or not C.get('urls'):
        sys.exit('Run it once by hand first (in the folder, with the playlist’s address), so it knows where and what. Then --install.')
    me = os.path.join(G, 'kermode.py')
    if os.path.realpath(os.path.abspath(__file__)) != os.path.realpath(me):
        shutil.copyfile(os.path.abspath(__file__), me)
    os.makedirs(os.path.dirname(AGENT), exist_ok=True)
    open(AGENT, 'w').write('''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>%s</string>
  <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>%s</string><string>--auto</string></array>
  <key>StartInterval</key><integer>21600</integer>
  <key>RunAtLoad</key><true/>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>%s</string>
  <key>StandardErrorPath</key><string>%s</string>
</dict></plist>
''' % (LABEL, me, LOGF, LOGF))
    uid = str(os.getuid())
    subprocess.run(['launchctl', 'bootout', 'gui/%s/%s' % (uid, LABEL)], capture_output=True)
    r = subprocess.run(['launchctl', 'bootstrap', 'gui/' + uid, AGENT], capture_output=True, text=True)
    if r.returncode:
        sys.exit('Couldn’t set it going: %s' % (r.stderr or '').strip())
    say('Set up. Every 6 hours, and now, your Mac looks at the playlist, gets any new introductions (%d at most each time, so YouTube '
        'doesn’t stop it), makes the MP4s and puts them on the G-Drive when you’re at home, with a notification when any go on. '
        'What it does: %s. To stop it: python3 ~/Grifflix/kermode.py --uninstall' % (PER_RUN, LOGF))


def uninstall():
    try:
        subprocess.run(['launchctl', 'bootout', 'gui/%s/%s' % (os.getuid(), LABEL)], capture_output=True)
    except OSError:
        pass
    try:
        os.remove(AGENT)
    except OSError:
        pass
    say('Stopped: it runs only when you run it now.')


def auto_start():
    """By itself: one at a time (a run of hundreds outlasts 6 hours), the newest of this script first, the log kept small."""
    try:
        pid = int(open(LOCK).read().strip() or 0)
        if pid and pid != os.getpid():
            os.kill(pid, 0)
            sys.exit(0)   # (one still running)
    except (OSError, ValueError):
        pass
    open(LOCK, 'w').write(str(os.getpid()))
    try:
        if os.path.getsize(LOGF) > 3e6:
            t = open(LOGF, encoding='utf-8', errors='replace').readlines()[-5000:]
            open(LOGF, 'w', encoding='utf-8').writelines(t)
    except OSError:
        pass
    say('==== %s' % time.strftime('%Y-%m-%d %H:%M'))
    if not ON_MINI and os.path.exists(os.path.join(DEST, '.mini-gets')):   # (the Mac mini gets them now: this Mac's own stopped)
        say('The Mac mini gets them now: this Mac stops doing it by itself.')
        uninstall()
        try:
            os.remove(LOCK)
        except OSError:
            pass
        sys.exit(0)
    if not ON_MINI and not os.environ.get('KERMODE_FRESH'):   # (the newest version, from GitHub, taken up before each run; the Mac mini's
        # is kept up to date by the gateway)
        try:
            new = subprocess.run(['curl', '-fsSL', '-m', '60', SRC], capture_output=True).stdout
            me = os.path.abspath(__file__)
            if new and new.startswith(b'#!') and new != open(me, 'rb').read():
                open(me + '.new', 'wb').write(new)
                os.replace(me + '.new', me)
                say('A newer version: carrying on with it')
                os.remove(LOCK)
                os.execve(sys.executable, [sys.executable, me] + sys.argv[1:], dict(os.environ, KERMODE_FRESH='1'))
        except OSError:
            pass


def main(a):
    os.makedirs(G, exist_ok=True)
    if '--install' in a:
        return install()
    if '--uninstall' in a:
        return uninstall()
    if AUTO:
        auto_start()
    try:
        C = json.load(open(CONF))
    except (OSError, ValueError):
        C = {}
    urls = [x for x in a if re.match(r'https?://', x)]
    if a and a[0] in ('-h', '--help'):
        return say(__doc__)
    bad = [u for u in urls if not good_url(u)]
    if bad:
        sys.exit('That isn’t a whole address: %s. Copy the playlist’s address from your browser’s address bar (it ends with list= and a long code), '
                 'and put it after this in quotes.' % bad[0])
    gone = [u for u in C.get('urls') or [] if not good_url(u)]
    if gone:   # (one kept before that isn't a whole address: forgotten)
        say('Forgotten (not a whole address): %s' % ', '.join(gone))
        C['urls'] = [u for u in C['urls'] if good_url(u)]
    if AUTO and not (C.get('folder') and C.get('urls')):
        sys.exit('Not set up: run it once by hand first')
    if '--here' in a or not C.get('folder'):
        if os.path.realpath(os.getcwd()) == os.path.realpath(HOME):
            sys.exit('First, in Terminal: cd (with a space), drag the folder the introductions are in onto the window, press Return. Then this again.')
        C['folder'] = os.getcwd()
    C['urls'] = list(dict.fromkeys((C.get('urls') or []) + urls))
    json.dump(C, open(CONF, 'w'), indent=1)
    folder = C['folder']
    if not os.path.isdir(folder):
        sys.exit('The folder %s isn’t there: cd into the one they’re in now and run this with --here.' % folder)
    say('Folder: %s' % folder)
    os.chdir(folder)
    drive()

    # 1. what's been got: the codes in the names here, and those kept
    ids = set()
    for d in (folder, os.path.join(folder, 'originals')):
        try:
            ids |= {m.group(1) for n in os.listdir(d) for m in [CODE.search(n)] if m}
        except OSError:
            pass
    have = got_write(ids)
    fresh = set()
    unread = False
    if '--move' not in a and '--make' not in a:
        if not C['urls']:
            sys.exit('Which YouTube playlist are they from? Run it again with its address after it, in quotes.')
        y = ytdlp()
        say('Reading %s …' % ', '.join(C['urls']))
        L = listing(y, C['urls'])
        unread = not L   # (an error, not "nothing new": the playlist always has them; said last, so it's looked at again soon, 27 Nov 2026)
        new = [(i, t) for i, t in L if i not in have]
        if new:   # (any already here by its name, without its code: added to the list, not got again)
            known = {key(n) for n in names_here(folder)} | {key(n) for n in names_read()}
            already = [(i, t) for i, t in new if key(t) in known]
            if already:
                have = got_write({i for i, _ in already})
                say('%d already here by name, added to the list of those got.' % len(already))
            new = [(i, t) for i, t in new if i not in have]
        say('%d in the playlist%s with Kermode in the name; %d got before; %d new.' % (len(L), '' if len(C['urls']) == 1 else 's', len(L) - len(new), len(new)))
        for _, t in new:
            say('  new: %s' % t)
        if '--new' in a:
            return
        if new:
            done = os.path.join(tempfile.mkdtemp(), 'done.txt')
            keep_fresh(y)
            # (gently: yt-dlp's own "-t sleep" pauses, as YouTube asked when it stopped a run of hundreds for an hour, 26 Nov 2026; and by
            # itself, at most PER_RUN each time, the rest next time)
            subprocess.run([y] + solver(y) + ['--download-archive', GOT, '--ignore-errors', '--no-overwrites', '--match-filters', "title~='(?i)kermode'",
                            '-S', 'vcodec:h264,res:1080,acodec:m4a', '--merge-output-format', 'mkv', '-o', '%(title)s [%(id)s].%(ext)s',
                            '--sleep-requests', '0.75', '--sleep-interval', '10', '--max-sleep-interval', '20', '--print-to-file', 'after_move:filepath', done]
                           + (['--max-downloads', str(PER_RUN), '--no-progress'] if AUTO else []) + C['urls'], env=env())
            try:
                fresh = {os.path.join(folder, os.path.basename(l.strip())) for l in open(done, encoding='utf-8') if l.strip()}
            except OSError:
                pass
            got_write(set())   # (yt-dlp's additions copied to the G-Drive's list too)
            say('Got %d.' % len(fresh))
    # 3. MP4s made; 4. put on the G-Drive
    c = convert(folder, fresh)
    if '--make' in a:
        left = [f for f in os.listdir(folder) if f.lower().endswith('.mp4') and not f.startswith('.')]
        return say('Done: %d made into MP4s. %d MP4s are in %s, ready to drag into G-Drive › Grifflix › BFI Kermode.' % (c, len(left), folder))
    m = move(folder, fresh)
    say('Done: %d made into MP4s, %d put in G-Drive › Grifflix › BFI Kermode. The Mac mini puts each with its film within 10 minutes; '
        'any it isn’t sure of are on the admin page, BFI tab.' % (c, m))
    if m:
        notify('%d introduction%s put on the G-Drive: each goes with its film within 10 minutes' % (m, '' if m == 1 else 's'))
    left = [f for f in os.listdir(folder) if f.lower().endswith('.mp4') and not f.startswith('.')]
    if AUTO and left and not os.path.isdir(GD):
        say('%d waiting here for the G-Drive (you’re not at home): next time' % len(left))
    if unread:
        sys.exit('Read nothing with Kermode in the name from the playlist: see above.')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        print('\nStopped. Run it again to carry on: nothing half-made is kept or moved.')
        sys.exit(130)
    finally:
        if AUTO:
            try:
                if open(LOCK).read().strip() == str(os.getpid()):
                    os.remove(LOCK)
            except OSError:
                pass
