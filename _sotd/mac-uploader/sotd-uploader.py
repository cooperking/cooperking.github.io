#!/usr/bin/env python3
"""SOTD Uploader: watches John's SOTD folder on his Mac and puts each new or changed MP3 and MP4 on the site, as the
admin page's folder upload does (matched to its day by its name, sent through the Worker, which then makes the video's
TV copy). Runs in the background (launchd: when the folder changes, and every 15 minutes), started by Install.command.

    python3 sotd-uploader.py            one look at the folder (what launchd runs)
    python3 sotd-uploader.py --dry      say what it would send, send nothing

Settings: ~/Library/Application Support/SOTD Uploader/settings.json ({"folder": ...}); the admin password in the
login keychain (service "sotd-uploader"). What it has seen: state.json beside the settings; what it did: uploader.log.
A file is only sent when its name matches one song clearly (as "Artist - Title", "Title Artist" or the title alone);
anything it can't place is left, and said once in a notification, to upload by hand on the admin page.
"""
import datetime, hashlib, json, os, re, subprocess, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request

WORKER = os.environ.get('SOTD_WORKER', 'https://sotd-playlists.cooperking.workers.dev')   # (others only for testing)
HOME = os.environ.get('SOTD_HOME') or os.path.expanduser('~/Library/Application Support/SOTD Uploader')
SETTINGS, STATE, LOG = (os.path.join(HOME, n) for n in ('settings.json', 'state.json', 'uploader.log'))
PART = 50 * 1024 * 1024
UA = 'SOTD-Uploader/1.0 (Mac)'
DRY = '--dry' in sys.argv


def log(msg):
    line = '%s  %s' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), msg)
    print(line)
    try:
        os.makedirs(HOME, exist_ok=True)
        with open(LOG, 'a') as f:
            f.write(line + '\n')
        if os.path.getsize(LOG) > 1_000_000:   # keep it small
            lines = open(LOG).readlines()[-2000:]
            open(LOG, 'w').writelines(lines)
    except OSError:
        pass


def notify(title, text):
    if DRY:
        return
    t = text.replace('\\', '\\\\').replace('"', '\\"')
    try:
        subprocess.run(['osascript', '-e', 'display notification "%s" with title "%s"' % (t, title.replace('"', ''))], capture_output=True)
    except OSError:
        pass


def admin_key():
    if os.environ.get('SOTD_ADMIN_KEY'):
        return os.environ['SOTD_ADMIN_KEY']
    r = subprocess.run(['security', 'find-generic-password', '-s', 'sotd-uploader', '-a', 'admin', '-w'], capture_output=True, text=True)
    return r.stdout.strip()


def call(path, body=None, raw=None, method='POST', key=''):
    headers = {'User-Agent': UA}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    data = raw if raw is not None else (json.dumps(body or {}).encode() if method == 'POST' else None)
    if raw is None and method == 'POST':
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(WORKER + path, data=data, method=method, headers=headers)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            try:
                j = json.loads(e.read() or b'{}')
            except ValueError:
                j = {}
            if e.code in (401, 403, 400, 404):
                j['status'] = e.code
                return j
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(5 * (attempt + 1))
    return {'error': 'no answer from the site'}


# ---- matching a file's name to its song: the same as the admin page's folder upload ----
def norm(x):
    x = unicodedata.normalize('NFKD', str(x))
    x = ''.join(c for c in x if not unicodedata.combining(c)).lower()
    x = re.sub(r'\.(mp3|mp4|m4v|m4a)$', '', x)
    x = re.sub(r'[\(\[][^\)\]]*(remaster|version|edit|mix|live|official|video|audio|lyric|hd|4k|bonus|mono|stereo|single|demo|from)[^\)\]]*[\)\]]', ' ', x)
    x = re.sub(r'\s[-–—]\s[^-–—]*(remaster|version|edit|mix|live|bonus|mono|stereo|single|demo)[^-–—]*$', ' ', x)
    x = re.sub(r'\b(feat|ft|featuring)\b.*$', ' ', x)
    x = re.sub(r"['’‘`]", '', x).replace('&', ' and ')
    x = re.sub(r'[^a-z0-9]+', ' ', x)
    x = re.sub(r'\b(the|a|an)\b', ' ', x)
    return re.sub(r'\s+', ' ', x).strip()


def words(x):
    return [w for w in norm(x).split(' ') if w]


def sim(x, y):
    a, b = words(x), words(y)
    if not a or not b:
        return 0
    if a == b:
        return 1
    pool, common = {}, 0
    for w in b:
        pool[w] = pool.get(w, 0) + 1
    for w in a:
        if pool.get(w):
            pool[w] -= 1
            common += 1
    return 2 * common / (len(a) + len(b))


def score(name, s):
    base = re.sub(r'^\s*\d{1,3}(\s*[-._)]\s*|\s+)', '', re.sub(r'\.(mp3|mp4|m4v|m4a)$', '', name, flags=re.I))
    best = sim(base, s['title'])
    fw, tw = words(base), words(s['title'])
    for i in range(0, len(fw) - len(tw) + 1 if tw else 0):
        if fw[i:i + len(tw)] != tw:
            continue
        rest = ' '.join(fw[:i] + fw[i + len(tw):])
        if rest:
            best = max(best, 0.6 + sim(rest, s['artist']) * 0.4)
    best = max(best, sim(base, s['title'] + ' ' + s['artist']) * 0.95)
    parts = re.split(r'\s+[-–—]\s+', base)
    for k in range(1, len(parts)):
        l, r = ' - '.join(parts[:k]), ' - '.join(parts[k:])
        best = max(best, sim(r, s['title']) * 0.6 + sim(l, s['artist']) * 0.4, sim(l, s['title']) * 0.6 + sim(r, s['artist']) * 0.4)
    return best


def match(name, songs):
    m = re.search(r'([0-9A-Za-z]{22})\.(mp3|mp4|m4v)$', name)
    if m:
        for s in songs:
            if s['id'] == m.group(1):
                return s, True
    best, bs, near = None, 0, 0
    for s in songs:
        sc = score(name, s)
        if sc > bs:
            near, bs, best = bs, sc, s
        elif sc > near:
            near = sc
    return best, bool(best) and bs >= 0.85 and bs - near > 0.05


def fingerprint(path, size):   # the admin page's: size and SHA-256 (the whole file, or a big one's start, middle and end)
    B = 4 << 20
    h = hashlib.sha256(str(size).encode())
    with open(path, 'rb') as f:
        if size <= 64 << 20:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        else:
            for off, n in ((0, B), (size // 2 - B // 2, B), (size - B, B)):
                f.seek(off)
                h.update(f.read(n))
    return '%d:%s' % (size, h.digest()[:16].hex())


def upload(path, kind, track, size, fp, key):
    k = '%s/%s.%s' % (kind, track, 'mp3' if kind == 'a' else 'mp4')
    j = call('/members/upload?op=start&key=' + k, key=key)
    up = j.get('uploadId')
    if not up:
        return j.get('error') or 'couldn’t start'
    parts = []
    with open(path, 'rb') as f:
        n = 1
        while True:
            chunk = f.read(PART)
            if not chunk:
                break
            r = call('/members/upload?op=part&key=%s&uploadId=%s&n=%d' % (k, urllib.parse.quote(up), n), raw=chunk, key=key)
            if not r.get('etag'):
                call('/members/upload?op=abort&key=%s&uploadId=%s' % (k, urllib.parse.quote(up)), key=key)
                return r.get('error') or 'a part didn’t go'
            parts.append({'partNumber': r['partNumber'], 'etag': r['etag']})
            n += 1
    r = call('/members/upload?op=done&key=%s&uploadId=%s' % (k, urllib.parse.quote(up)), {'parts': parts, 'fp': fp}, key=key)
    return '' if r.get('ok') else (r.get('error') or 'couldn’t finish')


def main():
    try:
        settings = json.load(open(SETTINGS))
    except (OSError, ValueError):
        log('Not set up: run Install.command first.')
        return
    folder, key = os.path.expanduser(settings.get('folder', '')), admin_key()
    if not key:
        log('No admin password in the keychain: run Install.command again.')
        return
    try:
        state = json.load(open(STATE))
    except (OSError, ValueError):
        state = {'files': {}, 'told': {}}
    try:
        names = []
        if not os.path.isdir(folder):
            log('The folder isn’t there: %s' % folder)
            return
        def stop(e):
            raise e
        for root, dirs, files in os.walk(folder, onerror=stop):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            names += [os.path.join(root, f) for f in files if not f.startswith('.') and re.search(r'\.(mp3|mp4|m4v)$', f, re.I)]
    except PermissionError:
        notify('SOTD Uploader', 'macOS won’t let it read the SOTD folder: see the Read me.')
        log('Permission denied reading %s (System Settings > Privacy & Security > Full Disk Access: add python3)' % folder)
        return
    if not os.path.isdir(folder):
        log('The folder isn’t there: %s' % folder)
        return
    # a file still being written (exported, downloaded or copied) waits until it has stopped changing for a minute
    now = time.time()
    ready = [p for p in names if now - os.path.getmtime(p) > 60]
    if not ready:
        return
    songs_j = call('/songs.json', method='GET')
    songs = songs_j.get('songs') or []
    if not songs:
        log('Couldn’t get the song list: %s' % songs_j.get('error'))
        return
    cat = call('/members/admin', {'op': 'list'}, key=key)
    if cat.get('status') == 401:
        notify('SOTD Uploader', 'The admin password was refused: run Install.command again.')
        log('Admin password refused.')
        return
    catalog = cat.get('catalog')
    if catalog is None:
        log('Couldn’t read what’s up already: %s' % cat.get('error'))
        return
    sent, unsure, failed, marks = [], [], [], []
    for p in sorted(ready):
        st = os.stat(p)
        rec = state['files'].get(p)
        if rec and rec.get('size') == st.st_size and rec.get('mtime') == st.st_mtime and rec.get('done'):
            continue   # seen as it is, and dealt with
        name = os.path.basename(p)
        kind = 'a' if name.lower().endswith('.mp3') else 'v'
        s, sure = match(name, songs)
        if not sure:   # (today's song may not be on the playlist yet: said only after 6 hours of not matching)
            first = (rec or {}).get('first') or now
            if not state['told'].get(p) and now - first > 6 * 3600:
                unsure.append(name)
                state['told'][p] = now
            state['files'][p] = {'size': st.st_size, 'mtime': st.st_mtime, 'done': False, 'first': first}
            continue
        fp = fingerprint(p, st.st_size)
        e = catalog.get(s['id']) or {}
        if e.get('f' + kind) == fp or (not e.get('f' + kind) and e.get(kind) == st.st_size):
            if not e.get('f' + kind):
                marks.append({'key': '%s/%s.%s' % (kind, s['id'], 'mp3' if kind == 'a' else 'mp4'), 'size': st.st_size, 'fp': fp})
            state['files'][p] = {'size': st.st_size, 'mtime': st.st_mtime, 'done': True, 'track': s['id']}
            continue
        what = '%s — %s (%s)' % (s['title'], s['artist'], 'song' if kind == 'a' else 'video')
        if DRY:
            log('would send %s as %s' % (name, what))
            continue
        log('sending %s as %s, %.0f MB' % (name, what, st.st_size / 1e6))
        err = upload(p, kind, s['id'], st.st_size, fp, key)
        if err:
            failed.append(name)
            log('  failed: %s' % err)
            continue
        sent.append(what)
        state['files'][p] = {'size': st.st_size, 'mtime': st.st_mtime, 'done': True, 'track': s['id']}
    if marks and not DRY:
        call('/members/upload?op=mark', {'items': marks}, key=key)
    if not DRY:
        os.makedirs(HOME, exist_ok=True)
        json.dump(state, open(STATE, 'w'))
    if sent:
        notify('Song of the Day', 'Uploaded: ' + '; '.join(sent))
    if unsure:
        log('not sure which song: %s' % ', '.join(unsure))
        notify('SOTD Uploader', 'Not sure which song: %s. Name it “Artist - Title”, or upload it on the admin page.' % ', '.join(unsure[:3]) + (' …' if len(unsure) > 3 else ''))
    if failed:
        notify('SOTD Uploader', 'Couldn’t upload: %s. It’ll try again.' % ', '.join(failed[:3]))


if __name__ == '__main__':
    main()
