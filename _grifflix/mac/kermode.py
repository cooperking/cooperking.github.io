#!/usr/bin/env python3
"""Mark Kermode's introductions to the BFI's films: the new ones got, made MP4s and put on the G-Drive for Grifflix, none got twice (John,
26 Nov 2026: "I have more to get, so how can I move them and the script check, so we don't get them again").

On John's Mac, in Terminal. Once, to have it:
  curl -fsSL https://raw.githubusercontent.com/cooperking/cooperking.github.io/main/_grifflix/mac/kermode.py -o ~/Grifflix/kermode.py --create-dirs

  python3 ~/Grifflix/kermode.py URL…   the first time: in the folder the introductions are in, with the YouTube playlist(s) they're from
  python3 ~/Grifflix/kermode.py        after that, from anywhere: the same folder and playlists, only the new ones got
  python3 ~/Grifflix/kermode.py --new  only says which are new, getting nothing
  python3 ~/Grifflix/kermode.py --move only makes MP4s of what's in the folder and puts them on the G-Drive, getting nothing
  python3 ~/Grifflix/kermode.py --here this folder used from now on, not the one before

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
GD = os.environ.get('GRIFFLIX_GD') or '/Volumes/G-Drive'
DEST = os.path.join(GD, 'Grifflix', 'BFI Kermode')
MINI = '192.168.68.125'   # (the Mac mini, as convert-here.sh has it)
CODE = re.compile(r'\[([A-Za-z0-9_-]{11})\]')
VID = ('.mkv', '.webm')
HALF = re.compile(r'\.(part|ytdl|temp)(\.|$)|\.f\d+\.\w+$|^\.')

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
    for p in [os.path.join(HOME, 'bin', name), '/opt/homebrew/bin/' + name, '/usr/local/bin/' + name, shutil.which(name)] + \
             sorted(glob.glob(os.path.join(HOME, 'Library/Python/*/bin', name))):
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
    if not y:
        say('Fetching yt-dlp (once) into ~/bin …')
        y = os.path.join(HOME, 'bin', 'yt-dlp')
        if not fetch('https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos', y):
            sys.exit('Couldn’t fetch yt-dlp. Try again, or: brew install yt-dlp')
        os.chmod(y, 0o755)
    if not (tool('deno') or os.path.exists(os.path.join(HOME, '.deno', 'bin', 'deno'))):
        say('Fetching Deno (once) into ~/bin, which yt-dlp needs for YouTube …')
        arch = 'aarch64' if os.uname().machine == 'arm64' else 'x86_64'
        z = os.path.join(tempfile.mkdtemp(), 'deno.zip')
        if fetch('https://github.com/denoland/deno/releases/latest/download/deno-%s-apple-darwin.zip' % arch, z):
            subprocess.run(['unzip', '-oq', z, '-d', os.path.join(HOME, 'bin')])
            os.chmod(os.path.join(HOME, 'bin', 'deno'), 0o755)
        else:
            say('Couldn’t fetch Deno: yt-dlp may get only some videos from YouTube without it.')
    return y


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


def on_films():
    """Those already with their films on the G-Drive (the Mac mini put them in each film's Extras)."""
    say('Looking at the films on the G-Drive for introductions they have already (a minute or so) …')
    return [os.path.basename(p) for p in glob.glob(os.path.join(GD, 'Grifflix', 'Movies', '*', 'Extras', 'Mark Kermode*'))]


def drive():
    if os.path.isdir(GD):
        return True
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
        for l in (r.stdout or '').splitlines():
            i, _, t = l.partition('\t')
            if re.fullmatch(r'[A-Za-z0-9_-]{11}', i) and re.search(r'(?i)kermode', t):
                out.append((i, t))
        if r.returncode and not out:
            say('Couldn’t read %s: %s' % (u, ((r.stderr or '').strip().splitlines() or [''])[-1]))
    return out


def convert(folder, fresh):
    """Each MKV or WebM made an MP4 by its tidy name, the original put in originals."""
    ff = tool('ffmpeg')
    if not ff:
        say('No ffmpeg on this Mac: from osxexperts.net (Apple Silicon) unzip ffmpeg into ~/bin, then: xattr -d com.apple.quarantine ~/bin/ffmpeg')
        return 0
    n = 0
    for f in sorted(os.listdir(folder)):
        p = os.path.join(folder, f)
        if not f.lower().endswith(VID) or HALF.search(f) or not os.path.isfile(p):
            continue
        if p not in fresh and time.time() - os.path.getmtime(p) < 120:
            continue
        out = os.path.join(folder, tidy(os.path.splitext(f)[0])[0] + '.mp4')
        if os.path.exists(out):
            say('Already an MP4 of it: %s' % os.path.basename(out))
            continue
        info = subprocess.run([ff, '-hide_banner', '-i', p], capture_output=True, text=True).stderr or ''
        v = ['-c:v', 'copy'] if re.search(r'Video: h264', info) else ['-c:v', 'h264_videotoolbox', '-q:v', '65']
        base = [ff, '-hide_banner', '-nostdin', '-v', 'error', '-i', p, '-map', '0:v:0', '-map', '0:a:0?']
        tail = ['-tag:v', 'avc1', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-n', out]
        ok = subprocess.run(base + v + tail).returncode == 0
        if not ok:
            try:
                os.remove(out)
            except OSError:
                pass
            ok = subprocess.run(base + ['-c:v', 'libx264', '-crf', '20', '-preset', 'medium', '-pix_fmt', 'yuv420p'] + tail).returncode == 0
        if ok:
            os.makedirs(os.path.join(folder, 'originals'), exist_ok=True)
            shutil.move(p, os.path.join(folder, 'originals', f))
            say('Made: %s' % os.path.basename(out))
            fresh.add(out)
            n += 1
        else:
            try:
                os.remove(out)
            except OSError:
                pass
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
    n = 0
    for f in todo:
        src = os.path.join(folder, f)
        name = tidy(os.path.splitext(f)[0])[0] + '.mp4'
        dst = os.path.join(DEST, name)
        if os.path.exists(dst):
            say('Already in BFI Kermode, left here: %s' % f)
            continue
        tmp = os.path.join(DEST, '.copying-' + name)
        try:
            shutil.copyfile(src, tmp)
            if os.path.getsize(tmp) != os.path.getsize(src):
                raise OSError('the copy came out a different size')
            os.rename(tmp, dst)
            os.remove(src)
            n += 1
            say('On the G-Drive: %s' % name)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            say('Couldn’t put %s on the G-Drive: %s' % (f, e))
    return n


def main(a):
    os.makedirs(G, exist_ok=True)
    try:
        C = json.load(open(CONF))
    except (OSError, ValueError):
        C = {}
    urls = [x for x in a if re.match(r'https?://', x)]
    if a and a[0] in ('-h', '--help'):
        return say(__doc__)
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
    if '--move' not in a:
        if not C['urls']:
            sys.exit('Which YouTube playlist are they from? Run it again with its address after it, in quotes.')
        y = ytdlp()
        say('Reading %s …' % ', '.join(C['urls']))
        L = listing(y, C['urls'])
        new = [(i, t) for i, t in L if i not in have]
        if new:   # (any already here by its name, without its code: added to the list, not got again)
            known = {key(n) for n in names_here(folder)}
            if any(key(t) not in known for _, t in new) and os.path.isdir(GD):
                known |= {key(n) for n in on_films()}
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
            subprocess.run([y, '--download-archive', GOT, '--ignore-errors', '--no-overwrites', '--match-filters', "title~='(?i)kermode'",
                            '-S', 'vcodec:h264,res:1080,acodec:m4a', '--merge-output-format', 'mkv', '-o', '%(title)s [%(id)s].%(ext)s',
                            '--sleep-interval', '2', '--max-sleep-interval', '6', '--print-to-file', 'after_move:filepath', done] + C['urls'], env=env())
            try:
                fresh = {os.path.join(folder, os.path.basename(l.strip())) for l in open(done, encoding='utf-8') if l.strip()}
            except OSError:
                pass
            got_write(set())   # (yt-dlp's additions copied to the G-Drive's list too)
            say('Got %d.' % len(fresh))
    # 3. MP4s made; 4. put on the G-Drive
    c = convert(folder, fresh)
    m = move(folder, fresh)
    say('Done: %d made into MP4s, %d put in G-Drive › Grifflix › BFI Kermode. The Mac mini puts each with its film within 10 minutes; '
        'any it isn’t sure of are on the admin page, BFI tab.' % (c, m))


if __name__ == '__main__':
    main(sys.argv[1:])
