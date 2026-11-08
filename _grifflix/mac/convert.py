#!/usr/bin/env python3
"""Makes every film and episode stream smoothly from the Mac mini, on the Mac mini itself, a few at a time.

The 2010 Mac mini can't convert a film's picture while someone watches (it would buffer), and very big files are hard
to send smoothly over Wi-Fi and to phones. So, ahead of time, this converts the ones that need it:
  * a format browsers can't play (XviD/DivX, MPEG-2, VC-1, AV1, VP9, 10-bit H.264...)
  * too big: bigger than 1080p (John, 7 Nov 2026: "1080p is big enough for me"), or over 20 Mbps
into H.264 at up to 1080p (quality 19, at most about 12 Mbps), with sound that plays everywhere (AAC stereo, and the
original surround kept alongside). Files that already play as they are, and HEVC within those limits, are left alone.

Safe:
  * it pauses whenever someone is watching (streaming comes first), and runs at low priority
  * each new file is checked (its length) before anything is moved; the original goes to "Grifflix Originals" on the
    same drive (outside the libraries), kept until you delete it
  * a film that gets a new file name (an .avi becoming .mp4) keeps its place in collections, calendars, lists and
    watching progress
  * stop it any time (Ctrl-C, or closing the window): the film it was on is simply done again next time

  python3 convert.py            what it would convert, and roughly how long (nothing changes)
  python3 convert.py go         converts, best-rated films first, then episodes, between 2am and 8am each night (it
                                waits for 2am, pauses a film at 8am and carries on at 2am); stops when all are done
  python3 convert.py go now     the same, at any hour
  python3 convert.py status     how far it has got
Overnight, through SSH, so it keeps going after you disconnect:
  nohup caffeinate -i python3 ~/Grifflix/convert.py go > ~/Grifflix/convert.out 2>&1 &
"""
import csv, json, os, re, shutil, signal, subprocess, sys, threading, time, urllib.parse, urllib.request

APP = os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix')
HOME = os.path.expanduser('~/Grifflix')
s = json.load(open(os.path.join(APP, 'settings.json')))
JF = s.get('jellyfin', 'http://127.0.0.1:8096')
GW = 'http://127.0.0.1:%s' % s.get('port', 8097)
STATE = os.path.join(HOME, 'convert-state.json')
LOG = os.path.join(HOME, 'convert.log')
NOW = os.path.join(HOME, 'convert-now.json')   # what it's doing this minute, for the admin page (through the gateway)
RUN = {'n': 0, 'total': 0, 'started': int(time.time())}
MAX_W, MAX_H, MAX_MBPS = 1920, 1080, 20
CMD = sys.argv[1] if len(sys.argv) > 1 else ''
# it works only at night, 2am to 8am (John, 8 Nov 2026: "2am until 8am each night unless it's in use"); "go now" ignores
# the hours (to try one straight away)
NIGHT = (int(os.environ.get('GRIFFLIX_FROM') or 2), int(os.environ.get('GRIFFLIX_TO') or 8))
ANYTIME = len(sys.argv) > 2 and sys.argv[2] == 'now'


def night():
    return ANYTIME or NIGHT[0] <= time.localtime().tm_hour < NIGHT[1]
os.makedirs(HOME, exist_ok=True)


def call(method, path, body=None, **q):
    url = JF + path + ('?' + urllib.parse.urlencode(q) if q else '')
    r = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                               headers={'Authorization': 'MediaBrowser Token="%s"' % s['jf_token'], 'Content-Type': 'application/json'})
    raw = urllib.request.urlopen(r, timeout=300).read()
    return json.loads(raw) if raw[:1] in (b'{', b'[') else None


def g(d, k, default=None):   # (Jellyfin 10 writes Name, 12 writes name)
    v = d.get(k, d.get(k[:1].lower() + k[1:]))
    return default if v is None else v


def log(*a):
    line = time.strftime('%Y-%m-%d %H:%M ') + ' '.join(str(x) for x in a)
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def now(state, **x):
    """The admin page's Conversions card: converting (how far), paused (why), waiting for the night, or finished."""
    try:
        tmp = NOW + '.tmp'
        json.dump(dict(RUN, state=state, at=int(time.time()), pid=os.getpid(), **x), open(tmp, 'w'))
        os.replace(tmp, NOW)
    except OSError:
        pass


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {'done': {}, 'failed': {}, 'remap': {}}


def save_state(st):
    tmp = STATE + '.tmp'
    json.dump(st, open(tmp, 'w'), indent=1)
    os.replace(tmp, STATE)


# ---------------------------------------------------------------- which need it

def why(ms):
    streams = g(ms, 'MediaStreams', [])
    v = next((x for x in streams if g(x, 'Type') == 'Video'), None)
    if not v:
        return None
    vc = (g(v, 'Codec') or '').lower()
    w, h = g(v, 'Width', 0), g(v, 'Height', 0)
    bits = g(v, 'BitDepth', 8) or 8
    prof = (g(v, 'Profile') or '').lower()
    mbps = (g(ms, 'Bitrate') or g(v, 'BitRate') or 0) / 1e6
    if w > MAX_W + 8 or h > MAX_H + 8:
        return 'bigger than 1080p (%dx%d)' % (w, h)
    if mbps > MAX_MBPS:
        return 'too big (%.0f Mbps)' % mbps
    if vc == 'h264' and bits <= 8 and '10' not in prof:
        return None
    if vc in ('hevc', 'h265'):
        return None
    return 'format (%s%s)' % (vc or '?', ', 10-bit' if bits > 8 or '10' in prof else '')


def gather():
    libs = s.get('libraries', {})
    out = []
    for lib, types in (('movies', 'Movie'), ('tv', 'Episode')):
        if not libs.get(lib):
            continue
        start = 0
        while True:
            r = call('GET', '/Users/%s/Items' % s['jf_user_id'], ParentId=libs[lib], Recursive='true', IncludeItemTypes=types,
                     Fields='MediaSources,Path,ProductionYear,CommunityRating', StartIndex=start, Limit=300, SortBy='SortName')
            for it in g(r, 'Items', []):
                ms = (g(it, 'MediaSources') or [{}])[0]
                w = why(ms)
                if w and g(it, 'Path') and os.path.exists(g(it, 'Path')):
                    name = g(it, 'Name') if types == 'Movie' else '%s S%02dE%02d' % (g(it, 'SeriesName'), g(it, 'ParentIndexNumber', 0), g(it, 'IndexNumber', 0))
                    out.append({'id': g(it, 'Id'), 'name': name, 'year': g(it, 'ProductionYear', ''), 'lib': lib, 'why': w,
                                'path': g(it, 'Path'), 'mins': round((g(it, 'RunTimeTicks') or 0) / 6e8), 'rating': g(it, 'CommunityRating') or 0,
                                'gb': round(os.path.getsize(g(it, 'Path')) / 1e9, 1)})
            start += 300
            if start >= g(r, 'TotalRecordCount', 0):
                break
    out.sort(key=lambda x: (x['lib'] != 'movies', -x['rating'], x['name']))
    return out


# ---------------------------------------------------------------- the tools

def tools():
    cands = []
    try:
        enc = call('GET', '/System/Configuration/encoding') or {}
        cands += [g(enc, 'EncoderAppPath'), g(enc, 'EncoderAppPathDisplay')]
    except Exception:
        pass
    cands += ['/Applications/Jellyfin.app/Contents/MacOS/ffmpeg', '/usr/local/bin/ffmpeg', '/opt/homebrew/bin/ffmpeg', shutil.which('ffmpeg')]
    for c in cands:
        if c and os.path.exists(c):
            probe = os.path.join(os.path.dirname(c), 'ffprobe')
            if os.path.exists(probe):
                return c, probe
    sys.exit('No ffmpeg found (Jellyfin’s own, normally in Jellyfin.app). Tell Claude.')


def probe(ffprobe, path):
    r = subprocess.run([ffprobe, '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', path], capture_output=True, text=True)
    return json.loads(r.stdout or '{}')


def watching():
    try:
        return any(g(x, 'NowPlayingItem') for x in call('GET', '/Sessions') or [])
    except Exception:
        return False


# ---------------------------------------------------------------- one file

def convert(item, ffmpeg, ffprobe, st):
    src = item['path']
    info = probe(ffprobe, src)
    dur = float((info.get('format') or {}).get('duration') or 0)
    streams = info.get('streams') or []
    audio = [x for x in streams if x.get('codec_type') == 'audio']
    subs = [x for x in streams if x.get('codec_type') == 'subtitle']
    v = next((x for x in streams if x.get('codec_type') == 'video' and not (x.get('disposition') or {}).get('attached_pic')), None)
    if not v or not dur:
        raise RuntimeError('couldn’t read it')
    base, ext = os.path.splitext(src)
    mkv = ext.lower() == '.mkv'   # (an .mkv stays .mkv: the same name, so Jellyfin keeps it as the same film)
    dst = base + ('.mkv' if mkv else '.mp4')
    tmp = base + '.grifflix-converting' + ('.mkv' if mkv else '.mp4')
    # room: the new file (at most about the original's size) and the original kept
    free = shutil.disk_usage(os.path.dirname(src)).free
    if free < os.path.getsize(src) * 1.2 + 5e9:
        raise SystemExit('The drive is nearly full (%.0f GB free). Delete some of “Grifflix Originals” once you’ve checked them, then run again.' % (free / 1e9))

    vf = "scale=w='min(%d,iw)':h='min(%d,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2" % (MAX_W, MAX_H)   # (only ever smaller, never enlarged)
    cmd = [ffmpeg, '-hide_banner', '-nostdin', '-y', '-i', src, '-map', '0:%d' % v['index']]
    main_a = next((a for a in audio if (a.get('disposition') or {}).get('default')), audio[0] if audio else None)
    if main_a:   # sound: AAC stereo first (plays everywhere), then the original (surround) as it is, where the file can hold it
        cmd += ['-map', '0:%d' % main_a['index']]
        keep = [a for a in ([main_a] + [a for a in audio if a is not main_a]) if mkv or a.get('codec_name') in ('ac3', 'eac3', 'aac')]
        for a in keep:
            cmd += ['-map', '0:%d' % a['index']]
    text_subs = [x for x in subs if x.get('codec_name') in ('subrip', 'srt', 'ass', 'ssa', 'mov_text', 'webvtt', 'text')]
    if mkv:
        cmd += ['-map', '0:s?', '-map', '0:t?']   # every subtitle (pictures too) and attached fonts, as they are
    else:
        for x in text_subs:
            cmd += ['-map', '0:%d' % x['index']]
    cmd += ['-map_metadata', '0', '-map_chapters', '0',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '19', '-maxrate', '12M', '-bufsize', '24M',
            '-profile:v', 'high', '-level', '4.1', '-pix_fmt', 'yuv420p', '-vf', vf, '-disposition:v:0', 'default']
    if main_a:
        cmd += ['-c:a:0', 'aac', '-b:a:0', '192k', '-ac:a:0', '2', '-metadata:s:a:0', 'title=Stereo', '-disposition:a:0', 'default']
        for n in range(1, len(keep) + 1):
            cmd += ['-c:a:%d' % n, 'copy', '-disposition:a:%d' % n, '0']
    if mkv:
        cmd += ['-c:s', 'copy', '-c:t', 'copy']
    else:
        cmd += ['-c:s', 'mov_text', '-movflags', '+faststart']
    cmd += ['-max_muxing_queue_size', '9999', tmp]

    log('converting', item['name'], '(%s, %s min, %s GB)' % (item['why'], item['mins'], item['gb']))
    t0, paused = time.time(), False
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, preexec_fn=lambda: os.nice(15))
    err_tail, at = [], [0.0]
    def read_err():
        for line in p.stderr:   # (ffmpeg's progress lines: "... time=00:41:07.12 ...")
            m = re.search(r'time=(\d+):(\d\d):(\d\d(?:\.\d+)?)', line)
            if m:
                at[0] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            else:
                err_tail.append(line)
                del err_tail[:-30]
    threading.Thread(target=read_err, daemon=True).start()
    show = lambda why_='': now('paused' if why_ else 'converting', name=item['name'], why=why_ or item['why'], gb=item['gb'], mins=item['mins'],
                                pct=round(min(99.9, at[0] / dur * 100), 1) if dur else 0, since=int(t0))
    show()
    try:
        while p.poll() is None:
            time.sleep(20)
            show(why_ if paused else '')
            why_ = 'someone is watching' if watching() else ('it’s past %d am: on again at %d am' % (NIGHT[1], NIGHT[0]) if not night() else '')
            if why_ and not paused:
                p.send_signal(signal.SIGSTOP); paused = True; log('  paused: ' + why_)
            elif not why_ and paused:
                p.send_signal(signal.SIGCONT); paused = False; log('  carrying on')
    except BaseException:
        if paused:
            p.send_signal(signal.SIGCONT)
        p.terminate(); p.wait()
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    if p.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError('ffmpeg: ' + ''.join(err_tail[-3:]).strip()[-300:])
    new_dur = float((probe(ffprobe, tmp).get('format') or {}).get('duration') or 0)
    if abs(new_dur - dur) > max(3, dur * .01):
        os.remove(tmp)
        raise RuntimeError('the new file is %.0f s long, the original %.0f s: kept the original' % (new_dur, dur))

    # the original out of the library, kept; the new one in its place
    roots = [l for vf_ in call('GET', '/Library/VirtualFolders') or [] for l in g(vf_, 'Locations', [])]
    root = next((r for r in roots if src.startswith(r.rstrip('/') + '/')), os.path.dirname(src))
    keep_at = os.path.join(os.path.dirname(root.rstrip('/')), 'Grifflix Originals', os.path.basename(root.rstrip('/')), os.path.relpath(src, root))
    os.makedirs(os.path.dirname(keep_at), exist_ok=True)
    shutil.move(src, keep_at)
    os.replace(tmp, dst)
    mins = (time.time() - t0) / 60
    log('  done in %d min: %.1f GB → %.1f GB; original kept in %s' % (mins, item['gb'], os.path.getsize(dst) / 1e9, os.path.dirname(keep_at)))
    st['done'][item['id']] = {'name': item['name'], 'from': src, 'to': dst, 'kept': keep_at, 'at': int(time.time()), 'mins': round(mins)}
    if dst != src:   # a new name: Jellyfin will make it a new item; noted, so everything follows it once it's seen
        st['remap'][item['id']] = {'to': dst, 'sets': sets_holding(item['id'])}
    save_state(st)
    try:
        call('POST', '/Library/Media/Updated', {'Updates': [{'Path': src, 'UpdateType': 'Deleted'}, {'Path': dst, 'UpdateType': 'Created' if dst != src else 'Modified'}]})
    except Exception:
        pass


SETS = None


def sets_holding(iid):
    global SETS
    if SETS is None:
        SETS = {}
        for b in g(call('GET', '/Users/%s/Items' % s['jf_user_id'], Recursive='true', IncludeItemTypes='BoxSet'), 'Items', []):
            for i in g(call('GET', '/Users/%s/Items' % s['jf_user_id'], ParentId=g(b, 'Id')), 'Items', []):
                SETS.setdefault(g(i, 'Id'), []).append(g(b, 'Id'))
    return SETS.get(iid, [])


def follow_up(st):
    """Films with a new file name: once Jellyfin has them as new items, put them back in their collections, and move
    calendars, lists and progress over to them."""
    if not st['remap']:
        return
    by_path = {}
    for lib in ('movies', 'tv'):
        lid = s.get('libraries', {}).get(lib)
        if not lid:
            continue
        start = 0
        while True:
            r = call('GET', '/Users/%s/Items' % s['jf_user_id'], ParentId=lid, Recursive='true', IncludeItemTypes='Movie,Episode', Fields='Path', StartIndex=start, Limit=500)
            for it in g(r, 'Items', []):
                by_path[g(it, 'Path')] = g(it, 'Id')
            start += 500
            if start >= g(r, 'TotalRecordCount', 0):
                break
    m = {}
    for old, x in list(st['remap'].items()):
        new = by_path.get(x['to'])
        if not new:
            continue
        for b in x.get('sets', []):
            try:
                call('POST', '/Collections/%s/Items' % b, Ids=new)
            except Exception:
                pass
        m[old] = new
    if m:
        try:
            r = urllib.request.Request(GW + '/local/remap', data=json.dumps({'map': m}).encode(), method='POST',
                                       headers={'X-Grifflix-Secret': s['secret'], 'Content-Type': 'application/json'})
            urllib.request.urlopen(r, timeout=60).read()
            for old in m:
                del st['remap'][old]
            log('%d with new names: back in their collections, calendars and lists' % len(m))
        except Exception as e:
            log('couldn’t reach the gateway to move calendars and lists over (tried again next time):', e)
        save_state(st)


# ---------------------------------------------------------------- run

st = load_state()
if CMD == 'status':
    d = st['done']
    print('%d converted, %d failed, %d waiting for Jellyfin to see their new name.' % (len(d), len(st['failed']), len(st['remap'])))
    for k, x in sorted(d.items(), key=lambda kv: -kv[1]['at'])[:15]:
        print('  %s  %s (%d min)' % (time.strftime('%d %b %H:%M', time.localtime(x['at'])), x['name'], x['mins']))
    for k, x in st['failed'].items():
        print('  FAILED  %s: %s' % (x['name'], x['why']))
    sys.exit()

items = [i for i in gather() if i['id'] not in st['done'] and i['id'] not in st['failed']]
if CMD != 'go':
    with open(os.path.join(HOME, 'convert-plan.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['library', 'title', 'year', 'why', 'minutes', 'GB', 'path'])
        for i in items:
            w.writerow([i['lib'], i['name'], i['year'], i['why'], i['mins'], i['gb'], i['path']])
    from collections import Counter
    c = Counter((i['lib'], i['why'].split(' (')[0]) for i in items)
    for (lib, why_), n in sorted(c.items()):
        print('  %-11s %-24s %4d' % ('films' if lib == 'movies' else 'episodes', why_, n))
    mins = sum(i['mins'] for i in items)
    print('%d to convert: %d hours of film. On this Mac roughly %d–%d days if it runs all the time (less while people watch).'
          % (len(items), mins / 60, mins * 1.2 / 60 / 24, mins * 2.5 / 60 / 24))
    print('Each one, with why: ~/Grifflix/convert-plan.csv\nTo start:  nohup caffeinate -i python3 ~/Grifflix/convert.py go > ~/Grifflix/convert.out 2>&1 &')
    sys.exit()

ffmpeg, ffprobe = tools()
if 'libx264' not in subprocess.run([ffmpeg, '-hide_banner', '-encoders'], capture_output=True, text=True).stdout:
    sys.exit('This ffmpeg has no H.264 encoder. Tell Claude.')
log('starting: %d to convert' % len(items))
RUN['total'] = len(items)
follow_up(st)
for n, it in enumerate(items):
    RUN['n'] = n
    if not night():
        log('waiting for %d am' % NIGHT[0])
        while not night():
            now('waiting', why='waiting for %d am' % NIGHT[0])
            time.sleep(60)
    try:
        convert(it, ffmpeg, ffprobe, st)
    except RuntimeError as e:
        log('  FAILED', it['name'], e)
        st['failed'][it['id']] = {'name': it['name'], 'why': str(e)[:300], 'path': it['path']}
        save_state(st)
    if n % 5 == 4:
        follow_up(st)
RUN['n'] = len(items)
now('finished')
time.sleep(30)
follow_up(st)
log('all done')
