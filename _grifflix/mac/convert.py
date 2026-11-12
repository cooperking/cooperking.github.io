#!/usr/bin/env python3
"""Makes every film and episode stream smoothly from the Mac mini, on the Mac mini itself, a few at a time.

The 2010 Mac mini can't convert a film's picture while someone watches (it would buffer), and very big files are hard
to send smoothly over Wi-Fi and to phones. So, ahead of time, this converts the ones that need it:
  * a format browsers can't play (XviD/DivX, MPEG-2, VC-1, AV1, VP9, 10-bit H.264...)
  * too big: bigger than 1080p (John, 7 Nov 2026: "1080p is big enough for me"), or over 20 Mbps
into H.264 at up to 1080p (quality 20, at most 8 Mbps and never more than the original), with sound that plays everywhere (AAC stereo, and the
original surround kept alongside). Files that already play as they are, and 8-bit HEVC within those limits, are left alone (10-bit HEVC is converted).

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
PRIO = os.path.join(HOME, 'convert-priority.json')   # John's say in the order, from the admin page: films pinned to go next, and repackaging all first
NOW = os.path.join(HOME, 'convert-now.json')   # what it's doing this minute, for the admin page (through the gateway)
RUN = {'n': 0, 'total': 0, 'started': int(time.time()), 'args': sys.argv[1:]}   # (args: so the admin page's Update the Mac starts it again the same way)
MAX_W, MAX_H, MAX_MBPS = 1920, 1080, 20
CMD = sys.argv[1] if len(sys.argv) > 1 else ''
# it works only at night, 2am to 8am (John, 8 Nov 2026: "2am until 8am each night unless it's in use"); "go now" ignores
# the hours (to try one straight away)
NIGHT = (int(os.environ.get('GRIFFLIX_FROM') or 2), int(os.environ.get('GRIFFLIX_TO') or 8))
ANYTIME = len(sys.argv) > 2 and sys.argv[2] == 'now'


def night():
    return ANYTIME or NIGHT[0] <= time.localtime().tm_hour < NIGHT[1]


def _stopped(signum, frame):   # (stopped, as Update everything does: tidied up like Ctrl-C, its ffmpeg ended and the half-made file removed,
    raise KeyboardInterrupt     # not left behind frozen; John, 12 Nov 2026)


signal.signal(signal.SIGTERM, _stopped)   # (not SIGHUP: started with nohup, it must outlive the SSH session)
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

def load_json(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}


REPACK = 'to repackage'   # (a film whose picture plays as it is, put in an MP4 with sound every device plays: the picture copied, not converted)


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
    # (every MKV, and any other file that isn't an MP4, is repackaged first, the picture copied as it is, even if it's to be converted
    # later: the whole library put right quickly, then the slow conversions (John, 12 Nov 2026: "I want all MKV's repackaged before they
    # are converted, even if they also need converting later"). Once it's an MP4, the next look finds what's still to convert)
    cont0 = [c.strip() for c in (g(ms, 'Container') or '').lower().split(',') if c.strip()]
    if cont0 and not any(c in ('mp4', 'm4v', 'mov') for c in cont0):
        return REPACK + ' (%s file)' % cont0[0].upper()
    if w > MAX_W + 8 or h > MAX_H + 8:
        return 'bigger than 1080p (%dx%d)' % (w, h)
    if mbps > MAX_MBPS:
        return 'too big (%.0f Mbps)' % mbps
    if (vc == 'h264' or vc in ('hevc', 'h265')) and bits <= 8 and '10' not in prof:
        # (the picture plays as it is (8-bit H.264; 8-bit HEVC on the devices that play HEVC; 10-bit HEVC, which many browsers and TV
        # sticks can't, is converted: John, 11 Nov 2026, Rushmore). It must also be in an MP4 with sound every device plays, or a device
        # can only have it through the media server's repackaging as it plays, which devices often fail on (John, 12 Nov 2026: "Playing
        # as it is" but not playing): then it's repackaged here, the picture copied untouched, which takes minutes, not hours)
        cont = [c.strip() for c in (g(ms, 'Container') or '').lower().split(',') if c.strip()]
        auds = [x for x in streams if g(x, 'Type') == 'Audio']
        a = next((x for x in auds if g(x, 'IsDefault')), auds[0] if auds else None)
        if not any(c in ('mp4', 'm4v', 'mov') for c in cont):
            return REPACK + ' (%s file)' % ((cont[0] if cont else '?').upper())
        if a and (g(a, 'Codec') or '').lower() not in ('aac', 'mp3'):
            return REPACK + ' (%s sound)' % ((g(a, 'Codec') or '?').upper())
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
                     Fields='MediaSources,Path,ProductionYear,CommunityRating,DateCreated', StartIndex=start, Limit=300, SortBy='SortName')
            for it in g(r, 'Items', []):
                ms = (g(it, 'MediaSources') or [{}])[0]
                w = why(ms)
                if w and g(it, 'Path') and os.path.exists(g(it, 'Path')):
                    name = g(it, 'Name') if types == 'Movie' else '%s S%02dE%02d' % (g(it, 'SeriesName'), g(it, 'ParentIndexNumber', 0), g(it, 'IndexNumber', 0))
                    out.append({'id': g(it, 'Id'), 'name': name, 'year': g(it, 'ProductionYear', ''), 'lib': lib, 'why': w, 'mode': 'remux' if w.startswith(REPACK) else 'convert',
                                'path': g(it, 'Path'), 'mins': round((g(it, 'RunTimeTicks') or 0) / 6e8), 'rating': g(it, 'CommunityRating') or 0,
                                'gb': round(os.path.getsize(g(it, 'Path')) / 1e9, 1), 'added': g(it, 'DateCreated') or ''})
            start += 300
            if start >= g(r, 'TotalRecordCount', 0):
                break
    # (films added in the last fortnight first, newest first: they're the ones about to be watched, and until they're converted the
    # Mac mini has to convert them as they play, which it can't keep up with (John, 11 Nov 2026: "my latest film got stuck on 40
    # seconds"); then the best-rated films, then episodes)
    # (first of all, Movie of the Day's films from today for three weeks, in their order: everyone watches those (John, 11 Nov 2026:
    # Rushmore, the day's film, stuck at 40 seconds))
    # (every Movie of the Day film before the rest of the library: today's and those to come in date order, then the past ones, latest
    # first; with repackaging first, theirs are the first MKVs done (John, 12 Nov 2026: "prioritise mkv repackaging on the films of the day first"))
    soon, past = {}, {}
    try:
        cal = (load_json(os.path.join(APP, 'data.json')).get('calendars') or {}).get('motd') or {}
        today = time.strftime('%Y-%m-%d')
        for d, i in sorted((cal.get('days') or {}).items()):
            if d >= today and i not in soon:
                soon[i] = d
        for d, i in sorted((cal.get('days') or {}).items(), reverse=True):
            if d < today and i not in soon and i not in past:
                past[i] = d
    except Exception:
        pass
    cut = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(time.time() - 14 * 86400))
    # (in order of how near its day is to today, either way, the coming one first at the same distance: today's, tomorrow's, yesterday's,
    # and so on, so the past ones aren't all left until every one to come is done (John, 12 Nov 2026: "it should prioritise any on the list"))
    t0 = time.mktime(time.strptime(today, '%Y-%m-%d'))
    def near(x):
        d = soon.get(x['id']) or past.get(x['id'])
        k = round((time.mktime(time.strptime(d, '%Y-%m-%d')) - t0) / 86400)
        return (abs(k), k < 0)
    first = sorted([x for x in out if (x['id'] in soon or x['id'] in past) and x['added'] < cut], key=near)
    soon = dict(soon, **{k: v for k, v in past.items() if k not in soon})   # (out of the groups below)
    new = sorted([x for x in out if x['added'] >= cut], key=lambda x: x['added'], reverse=True)
    rest = sorted([x for x in out if x['id'] not in soon and x['added'] < cut], key=lambda x: (x['lib'] != 'movies', -x['rating'], x['name']))
    # (the quick ones, repackaging, before the slow ones in each group: a few minutes each, and then they play everywhere)
    new.sort(key=lambda x: x.get('mode') != 'remux'); rest.sort(key=lambda x: x.get('mode') != 'remux')
    tomorrow, today = time.strftime('%Y-%m-%d', time.localtime(time.time() + 86400)), time.strftime('%Y-%m-%d')
    for x in first:
        x['why'] += ' · Movie of the Day ' + soon[x['id']]
        x['urgent'] = today <= soon[x['id']] <= tomorrow   # (today's and tomorrow's: converted at once, any hour, not waiting for the night)
    for x in new:
        x['new'] = True
    # (films John has just brought in come first of all, newest first: John, 12 Nov 2026, "any new movie I bring in - these should go
    # to the top of the queue"; then Movie of the Day's, then the rest)
    out[:] = new + [x for x in first if x not in new] + rest
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


def brief(info, path):
    """A file in a few words, for the admin page: its picture's format and size, its rate and how big it is (John, 11 Nov 2026:
    "what was the original and what was it converted to?")"""
    v = next((x for x in info.get('streams') or [] if x.get('codec_type') == 'video' and not (x.get('disposition') or {}).get('attached_pic')), {})
    fmt = info.get('format') or {}
    vc = (v.get('codec_name') or '?').replace('h264', 'H.264').replace('hevc', 'HEVC').replace('mpeg4', 'MPEG-4 (XviD/DivX)').replace('mpeg2video', 'MPEG-2').replace('vc1', 'VC-1')
    if '10' in (v.get('pix_fmt') or '') or '10' in (v.get('profile') or ''):
        vc += ' 10-bit'
    try:
        gb = os.path.getsize(path) / 1e9
    except OSError:
        gb = 0
    return {'v': vc, 'w': v.get('width') or 0, 'h': v.get('height') or 0, 'mbps': round(float(fmt.get('bit_rate') or 0) / 1e6, 1), 'gb': round(gb, 1)}


def watching():
    try:   # (someone watching through Grifflix: the gateway's note, kept fresh every 10 seconds of a film; the media server never lists them)
        if time.time() - os.path.getmtime(os.path.join(HOME, 'watching')) < 75:
            return True
    except OSError:
        pass
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
    # (always an MP4: the one kind of file every browser, phone and TV plays whole; an .mkv no browser does. A new name, so Jellyfin makes it
    # a new item, and calendars, lists, progress and collections follow it (the remap below): John, 12 Nov 2026)
    mkv = False
    dst = base + '.mp4'
    pf = (v.get('pix_fmt') or '')
    remux = item.get('mode') == 'remux'   # (the picture as it is, whatever it is: converted later if it needs to be)
    # the new file is made outside the libraries ("Grifflix Converting", beside them on the same drive), so Jellyfin
    # doesn't list it as a new film while it's being made (John, 8 Nov 2026: Back to the Future showed as newest at 11%)
    roots = [l for vf_ in call('GET', '/Library/VirtualFolders') or [] for l in g(vf_, 'Locations', [])]
    root = next((r for r in roots if src.startswith(r.rstrip('/') + '/')), os.path.dirname(src))
    work = os.path.join(os.path.dirname(root.rstrip('/')), 'Grifflix Converting')
    os.makedirs(work, exist_ok=True)
    tmp = os.path.join(work, os.path.basename(base) + '.grifflix-converting' + ('.mkv' if mkv else '.mp4'))
    # room: the new file (at most about the original's size) and the original kept
    sysfree = shutil.disk_usage('/').free   # (the Mac's own disk too: full, nothing on the Mac can write, and films stop playing: John, 12 Nov 2026)
    if sysfree < 5e9:
        raise SystemExit('The Mac’s own disk is nearly full (%.1f GB free): stopped, so it doesn’t fill. Free some space on it, then it carries on by itself.' % (sysfree / 1e9))
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
    # (the picture's rate: never more than the original had (a small or old file isn't made big, which adds nothing), and at most 8 Mbps,
    # plenty for 1080p (John, 11 Nov 2026: "Some of the conversions are still coming out a very large file size Braveheart 8gb"))
    src_kbps = int(v.get('bit_rate') or 0) / 1000 or (float((info.get('format') or {}).get('bit_rate') or 0) / 1000 - 192 * max(1, len(audio)))
    cap = int(max(1500, min(8000, src_kbps * 1.05 if src_kbps > 0 else 8000)))
    cmd += ['-map_metadata', '0', '-map_chapters', '0']
    if remux:   # (the picture copied as it is; HEVC labelled hvc1, as Apple's devices need, not hev1)
        cmd += ['-c:v', 'copy', '-disposition:v:0', 'default'] + (['-tag:v', 'hvc1'] if v.get('codec_name') == 'hevc' else [])
    else:
        cmd += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-maxrate', '%dk' % cap, '-bufsize', '%dk' % (cap * 2),
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

    log('repackaging' if remux else 'converting', item['name'], '(%s, %s min, %s GB)' % (item['why'], item['mins'], item['gb']))
    t0, paused = time.time(), False
    if os.path.exists('/usr/sbin/taskpolicy'):   # (macOS's background priority: its reading and writing give way to the media server and
        cmd = ['/usr/sbin/taskpolicy', '-b'] + cmd   # the films being watched, not only its processor time: John, 12 Nov 2026, a film wouldn't start)
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
            why_ = 'someone is watching' if watching() else ('it’s past %d am: on again at %d am' % (NIGHT[1], NIGHT[0]) if not night() and not item.get('urgent') else '')
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

    # the original out of the library, kept; the new one in its place (moved in only now, whole and checked)
    keep_at = os.path.join(os.path.dirname(root.rstrip('/')), 'Grifflix Originals', os.path.basename(root.rstrip('/')), os.path.relpath(src, root))
    os.makedirs(os.path.dirname(keep_at), exist_ok=True)
    shutil.move(src, keep_at)
    shutil.move(tmp, dst)   # (the same drive: a rename)
    mins = (time.time() - t0) / 60
    log('  done in %d min: %.1f GB → %.1f GB; original kept in %s' % (mins, item['gb'], os.path.getsize(dst) / 1e9, os.path.dirname(keep_at)))
    st['done'][item['id']] = {'name': item['name'], 'from': src, 'to': dst, 'kept': keep_at, 'at': int(time.time()), 'mins': round(mins),
                              'why': item['why'], 'before': brief(info, keep_at), 'after': brief(probe(ffprobe, dst), dst)}
    if dst != src:   # a new name: Jellyfin will make it a new item; noted, so everything follows it once it's seen
        try:   # (when it was first added: the new item keeps it, so a converted film isn't "new")
            added = g(call('GET', '/Users/%s/Items/%s' % (s['jf_user_id'], item['id'])) or {}, 'DateCreated')
        except Exception:
            added = None
        st['remap'][item['id']] = {'to': dst, 'sets': sets_holding(item['id']), 'added': added}
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
        if x.get('added'):   # its first "added" date back, so it doesn't show among the new films (John, 8 Nov 2026)
            try:
                full = call('GET', '/Users/%s/Items/%s' % (s['jf_user_id'], new))
                full['DateCreated'] = x['added']
                call('POST', '/Items/%s' % new, full)
            except Exception as e:
                log('  couldn’t keep its date added:', e)
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

def prio():
    try:
        p = json.load(open(PRIO))
    except (OSError, ValueError):
        p = {}
    return {'pins': [x for x in (p.get('pins') or []) if isinstance(x, dict) and x.get('id')], 'remux_first': p.get('remux_first', True)}


def ordered(rest):
    """What's left, in John's order: the films he pinned, in his order; then (unless he's turned it off) every repackaging before
    any conversion, as they're quick and put the library right first (John, 12 Nov 2026: "repackage all mkv files first and then go
    to conversions - let me prioritise too"); otherwise as gathered (Movie of the Day's films, new ones, then the best-rated)."""
    P = prio()
    pin = [x['id'] for x in P['pins']]
    first = sorted([x for x in rest if x['id'] in pin], key=lambda x: pin.index(x['id']))
    # (every repackaging before any conversion, new films' too: within each, as gathered, so films just brought in first, then Movie of
    # the Day's, then the rest: John, 12 Nov 2026, "repackaged only first, no conversions until all repackaging is complete")
    others = [x for x in rest if x['id'] not in pin]
    if P['remux_first']:
        others = [x for x in others if x.get('mode') == 'remux'] + [x for x in others if x.get('mode') != 'remux']
    return first + others


def unpin(iid):
    try:
        p = json.load(open(PRIO))
        p['pins'] = [x for x in (p.get('pins') or []) if x.get('id') != iid]
        tmp = PRIO + '.tmp'
        json.dump(p, open(tmp, 'w'))
        os.replace(tmp, PRIO)
    except (OSError, ValueError):
        pass


ffmpeg, ffprobe = tools()
if 'libx264' not in subprocess.run([ffmpeg, '-hide_banner', '-encoders'], capture_output=True, text=True).stdout:
    sys.exit('This ffmpeg has no H.264 encoder. Tell Claude.')
def leftovers():
    """Half-made files an earlier version left inside the libraries (it made them beside the film): removed, so they
    don't show as films. Only files named *.grifflix-converting.*, which nothing else makes."""
    n = 0
    for root in [l for vf_ in call('GET', '/Library/VirtualFolders') or [] for l in g(vf_, 'Locations', [])]:
        for d, _, files in os.walk(root):
            for f in files:
                if '.grifflix-converting.' in f:
                    try:
                        os.remove(os.path.join(d, f))
                        n += 1
                    except OSError:
                        pass
    if n:
        log('%d half-made file%s from before taken out of the libraries' % (n, '' if n == 1 else 's'))
        try:
            call('POST', '/Library/Refresh')   # (Jellyfin looks again, so they go from Grifflix too)
        except Exception:
            pass
leftovers()
log('starting: %d to convert' % len(items))
RUN['total'] = len(items)
follow_up(st)
rest, n = list(items), -1
looked = time.time()
while rest:
    if time.time() - looked > 1800:   # (every half hour, a look for films brought in since: they go to the top, not to the end of the queue)
        looked = time.time()
        try:
            have = set(x['id'] for x in rest)
            add = [i for i in gather() if i.get('new') and i['id'] not in have and i['id'] not in st['done'] and i['id'] not in st['failed']]
            if add:
                log('%d just brought in: to the top of the queue' % len(add))
                rest = add + rest
                RUN['total'] = RUN.get('total', 0) + len(add)
        except Exception as e:
            log('  couldn’t look for new films:', e)   # (the order looked at again before each film: a film John pins goes next, without starting anything again)
    rest = ordered(rest)
    it = rest.pop(0)
    n += 1
    RUN['n'] = n
    RUN['next'] = [{'id': x['id'], 'name': x['name'], 'why': x['why'], 'gb': x['gb'], 'mins': x['mins'], 'mode': x.get('mode')} for x in [it] + rest[:11]]   # (what's coming, for the admin page)
    RUN['left'] = {'remux': sum(1 for x in [it] + rest if x.get('mode') == 'remux'), 'convert': sum(1 for x in [it] + rest if x.get('mode') != 'remux')}
    if not night() and not it.get('urgent'):   # (today's or tomorrow's Movie of the Day doesn't wait for the night: John, 11 Nov 2026, Rushmore on the Fire Stick)
        log('waiting for %d am' % NIGHT[0])
        while not night():
            now('waiting', why='waiting for %d am' % NIGHT[0])
            time.sleep(60)
    try:
        convert(it, ffmpeg, ffprobe, st)
    except RuntimeError as e:
        if it.get('mode') == 'remux':   # (a picture an MP4 can't hold as it is: converted instead, in its turn, not given up on)
            log('  couldn’t repackage', it['name'], '(%s): it will be converted instead' % str(e)[:160])
            it['mode'] = 'convert'
            it['why'] = 'format (couldn’t be repackaged as it is)'
            rest.append(it)
        else:
            log('  FAILED', it['name'], e)
            st['failed'][it['id']] = {'name': it['name'], 'why': str(e)[:300], 'path': it['path']}
            save_state(st)
    unpin(it['id'])   # (done, or failed: off John's list)
    for _ in range(9):   # (each film's follow-up straight away, once Jellyfin has the new file: its date added, collections, lists)
        if not st['remap']:
            break
        time.sleep(10)
        follow_up(st)
    if not rest:   # (the queue done: a look again, as the films just repackaged may still need converting, now as MP4s, under their new ids)
        for _ in range(9):
            if not st['remap']:
                break
            time.sleep(20)
            follow_up(st)
        time.sleep(60)
        more = [i for i in gather() if i['id'] not in st['done'] and i['id'] not in st['failed'] and i['id'] != it['id']]
        if more:
            log('%d more to do (films repackaged that also need converting, or new ones)' % len(more))
            rest = more
            RUN['total'] = n + 1 + len(more)
RUN['n'] = n + 1
now('finished')
time.sleep(30)
follow_up(st)
log('all done')
