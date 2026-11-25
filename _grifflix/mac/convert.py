#!/usr/bin/env python3
"""Makes every film and episode stream smoothly from the Mac mini, on the Mac mini itself, a few at a time.

The 2010 Mac mini can't convert a film's picture while someone watches (it would buffer), and very big files are hard
to send smoothly over Wi-Fi and to phones. So, ahead of time, this converts the ones that need it:
  * a format browsers can't play (XviD/DivX, MPEG-2, VC-1, AV1, VP9, 10-bit H.264...)
  * too big: bigger than 1080p (John, 7 Nov 2026: "1080p is big enough for me"), or over 20 Mbps
into H.264 at up to 1080p (quality 20, at most 8 Mbps and never more than the original), with sound that plays everywhere (AAC stereo, and the
original surround kept alongside). Files that already play as they are, and 8-bit HEVC within those limits, are left alone (10-bit HEVC too, since 16 Nov 2026).

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
SKIP = os.path.join(HOME, 'convert-skip')   # (John's Skip on the admin page: the film it's on given up now)
MINE = os.path.getmtime(os.path.abspath(__file__))   # (this version: a newer one taken up between films, not by stopping one part way)


class MacBusy(Exception):
    """John's Mac took up converting: the Mac mini's own film stopped and cleared, back at the front of its list."""


def mac_converting():
    """John's own Mac converting a film this minute (its job heard from in the last 2 minutes), or copying one it made back to the
    G-Drive: the Mac mini's own converting stops, since both use the one G-Drive over its USB 2 and each slows the other (John, 18 Nov
    2026: "is there no way to sort this out"; "the conversion should have been stopped and cleared when I started converting on my mac")."""
    try:
        J = json.load(open(os.path.join(HOME, 'convert-jobs.json')))
        return any(j.get('mac') and not j.get('ended') and ((j.get('state') in ('started', 'converting') and time.time() - (j.get('seen') or 0) < 120)
                   or (j.get('state') == 'copying' and time.time() - (j.get('seen') or 0) < 1800)) for j in J.values())
    except (OSError, ValueError, AttributeError):
        return False


def downloading():
    """A film coming in from TorBox or Google Drive this minute (gdrive.py's lock, its process alive), and not waiting for one it
    brought in to be repackaged. Repackaging older films waits for it: the G-Drive can't do both at speed (John, 15 Nov 2026,
    "Downloads first but then when downloaded it should be repackaged first with priority if necessary before moving back to downloads")."""
    try:
        if os.path.exists(os.path.join(HOME, 'fetch-waiting')):
            return False
        pid = int((open(os.path.join(HOME, 'gdrive.lock')).read().split() or ['0'])[0])
        os.kill(pid, 0)
        return pid > 0
    except (OSError, ValueError):
        return False


def _stopped(*a):   # (stopped (an update, a restart): its ffmpeg stopped too, never left running, or frozen, beside the next converter on
    raise SystemExit('stopped')   # the same film: Moonrise at 0%, twice: John, 14 and 15 Nov 2026)


signal.signal(signal.SIGTERM, _stopped)
# it works only at night, 2am to 8am (John, 8 Nov 2026: "2am until 8am each night unless it's in use"); "go now" ignores
# the hours (to try one straight away)
NIGHT = (int(os.environ.get('GRIFFLIX_FROM') or 2), int(os.environ.get('GRIFFLIX_TO') or 8))
ANYTIME = len(sys.argv) > 2 and sys.argv[2] == 'now'


def night():   # (any hour: no overnight-only rule now; it waits only while something plays or John's Mac converts: John, 20 Nov 2026)
    return True


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
        json.dump(dict(RUN, state=state, at=int(time.time()), pid=os.getpid(), selfupdate=True, remuxonly=True, **x), open(tmp, 'w'))
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


MOVED = 'its file was moved or renamed while it was being made: done again under its new name'
SHRINK = 'to make smaller'   # (a file that plays, much bigger than it need be: converted once, compactly, at the end of the queue)
REPACK = 'to repackage'   # (a film whose picture plays as it is, put in an MP4 with sound every device plays: the picture copied, not converted)


def is_hdr(v):
    tr = (g(v, 'ColorTransfer') or '').lower()
    return (g(v, 'VideoRange') or '').upper() == 'HDR' or tr in ('smpte2084', 'arib-std-b67') or 'dovi' in (g(v, 'VideoRangeType') or '').lower()


POOR = .035   # (the Library's line for a poor copy: bits of picture per pixel per frame, allowing for how efficient its format is)


# (every film made again is made HEVC (H.265), 8-bit, in an MP4 labelled hvc1 as Apple's devices need: about 60% of H.264's rate for the same
# picture, so the whole library about 40% smaller at the same picture; it plays almost everywhere, and where it doesn't the player says why.
# John, 25 Nov 2026, "My aim is to have good 1080p mp4 films in a format playable everywhere (or almost everywhere) ... as small as they can be
# without losing noticeable quality" ... "HEVC is fine, so go ahead")
HEVC_OUT = True
HE = 1.6   # (H.264 needs this many times HEVC's rate for the same picture)


def size_top(w, h):
    """What a film of this size is made at, at most (Mbps): 1080p 6, 720p 3.5, smaller 2; by its width as well as its height, so a widescreen
    1080p film (1920 x 800) counts as 1080p, not 720p (Heaven's Gate was to be made at 3.5: 25 Nov 2026)."""
    return 6 if (w >= 1700 or h >= 900) else 3.5 if (w >= 1100 or h >= 600) else 2


def worth_smaller(mbps, w, h, vc=''):
    """The one rule for whether a film is made smaller (here, the Library and John's queue alike): its rate at least one and a half times
    what it would be made at (6 Mbps at 1080p, 3.5 at 720p, 2 below), whatever its format, as it comes out H.264 at no more than that: a third
    smaller at least, and still well above a good copy; or bigger than 1080p. John, 25 Nov 2026, "I have films that are very large marked as
    good which is confusing"."""
    if w > MAX_W + 8 or h > MAX_H + 8:
        return True
    if HEVC_OUT and (vc or '').lower() == 'h264':   # (H.264 made HEVC: at most its own picture's worth, about 40% smaller, whatever its rate)
        return bool(mbps)
    return bool(mbps) and mbps >= 1.5 * size_top(w, h) / (HE if HEVC_OUT else 1)   # (HEVC already: only one well over what it would be made at)


def pic_bpp(mbps, w, h, vc):
    """How much picture a file carries for its size, as the Library judges it (admin's lbGrade): HEVC and VP9 count 1.6 times H.264,
    AV1 twice, older formats 0.6."""
    vc = (vc or '').lower()
    eff = 2.0 if vc == 'av1' else 1.6 if vc in ('hevc', 'h265', 'vp9') else 1.0 if vc == 'h264' else .6
    return mbps * 1e6 * eff / (w * h * 24) if mbps and w and h else None


def est_save(ms, gb, mins, mode):
    """About how much space (GB) making a film would save: what it would come out as (HEVC, at no more than its picture needs or the set rate,
    its sound about 0.25 Mbps) against what it is. A repackaging: none."""
    if mode == 'remux' or not gb or not mins:
        return 0
    v = next((x for x in g(ms, 'MediaStreams', []) if g(x, 'Type') == 'Video'), {})
    w, h = min(g(v, 'Width', 0) or 1920, MAX_W), min(g(v, 'Height', 0) or 1080, MAX_H)
    mbps = (g(ms, 'Bitrate') or g(v, 'BitRate') or 0) / 1e6 or gb * 8000 / (mins * 60)
    vc = (g(v, 'Codec') or '').lower()
    eff = (2.0 if vc == 'av1' else 1.6 if vc in ('hevc', 'h265', 'vp9') else 1.0 if vc == 'h264' else .6) / (HE if HEVC_OUT else 1)
    top = size_top(w, h) / (HE if HEVC_OUT else 1)
    floor = POOR * w * h * 24 * 1.3 / 1e6 / (HE if HEVC_OUT else 1)
    out = (min(top, max(floor, max(.2, mbps - .25) * eff)) + .25) * mins * 60 / 8 / 1000
    return round(max(0, gb - out), 1)


def reencode_reason(v, mbps, hdr):
    """Why a film's picture is to be made again (HEVC), or None if it stays as it is: bigger than 1080p; H.264 (made HEVC, about 40%
    smaller); HEVC well over what it would be made at; a format not every device plays. Never an HDR one (its colours: kept as it is), a
    poor copy (it would come out no better: left for replacing), or 10-bit HEVC at a fair rate (it plays: John, 16 Nov 2026)."""
    vc = (g(v, 'Codec') or '').lower()
    w, h = g(v, 'Width', 0), g(v, 'Height', 0)
    bits = g(v, 'BitDepth', 8) or 8
    prof = (g(v, 'Profile') or '').lower()
    if hdr:
        return None
    if w > MAX_W + 8 or h > MAX_H + 8:
        return 'bigger than 1080p (%dx%d)' % (w, h)
    b_ = pic_bpp(mbps, w, h, vc)
    if b_ is not None and b_ < POOR:
        return None
    if vc in ('hevc', 'h265'):
        return SHRINK + ' (%.1f Mbps)' % mbps if worth_smaller(mbps, w, h, vc) else None
    if vc == 'h264' and bits <= 8 and '10' not in prof:
        if not HEVC_OUT:
            return SHRINK + ' (%.1f Mbps)' % mbps if worth_smaller(mbps, w, h, vc) else None
        return SHRINK + ' (%.1f Mbps H.264, made HEVC)' % mbps if mbps else None
    return 'format (%s%s)' % (vc or '?', ', 10-bit' if bits > 8 or '10' in prof else '')


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
    if not mbps and g(ms, 'Size') and g(ms, 'RunTimeTicks'):   # (no rate recorded: from the file's size and length)
        mbps = g(ms, 'Size') * 8 / (g(ms, 'RunTimeTicks') / 1e7) / 1e6
    # (every MKV, and any other file that isn't an MP4, is repackaged first, the picture copied as it is, even if it's to be converted
    # later: the whole library put right quickly, then the slow conversions (John, 12 Nov 2026: "I want all MKV's repackaged before they
    # are converted, even if they also need converting later"). Once it's an MP4, the next look finds what's still to convert)
    cont0 = [c.strip() for c in (g(ms, 'Container') or '').lower().split(',') if c.strip()]
    if HEVC_OUT:   # (one whose picture is made again goes straight to it, as an MP4: repackaged first, it would be done twice; an MKV plays meanwhile,
        # repackaged as it plays, the picture untouched: 25 Nov 2026, the rule of 12 Nov (MKVs repackaged first) made when converting took hours)
        r_ = reencode_reason(v, mbps, is_hdr(v))
        if r_:
            return r_
    if cont0 and not any(c in ('mp4', 'm4v', 'mov') for c in cont0):
        return REPACK + ' (%s file)' % cont0[0].upper()
    # (an HDR picture (HDR10, Dolby Vision, HLG) is never made smaller or turned into 1080p here: its colours need converting too, which
    # this ffmpeg can't, and it came out washed out. It's kept as it is, only repackaged if need be: 18 Nov 2026, Wild at Heart)
    hdr = is_hdr(v)
    if (w > MAX_W + 8 or h > MAX_H + 8) and not hdr:
        return 'bigger than 1080p (%dx%d)' % (w, h)
    # (10-bit HEVC left as it is too, like 8-bit: every player John has plays it, and a device that can't is sent a conversion made as
    # it plays (John, 16 Nov 2026: "new films already turned to mp4, but now being converted again just because HEVC. I haven't found a
    # player that can't play HEVC"). Still repackaged if it isn't an MP4 or its sound isn't one every device plays)
    if (vc in ('hevc', 'h265')) or (vc == 'h264' and bits <= 8 and '10' not in prof):
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
        # (more than a gigabyte of soundtracks beyond the one kept (dubs, second copies; commentaries stay): repackaged to one, the
        # picture copied, a few minutes: John, 19 Nov 2026, "I only need one English soundtrack")
        la = (g(a, 'Language') or '').lower() if a else ''
        extra = [x for x in auds if x is not a and not ((g(x, 'Language') or '').lower() in (la, '', 'und') and keep_beside(None, None, g(x, 'Title') or '', g(x, 'Channels') or 2))]
        secs = (g(ms, 'RunTimeTicks') or 0) / 1e7
        # (a dub, in another language, always: John's rule since 15 Nov 2026, "I always want the original language - never the dub", which
        # the converter had read as which soundtrack plays first only, keeping the dubs: John, 19 Nov 2026, "My no dubs rule was a while ago")
        dubs = [x for x in extra if (g(x, 'Language') or '').lower() not in (la, '', 'und')]
        if dubs or (extra and sum((g(x, 'BitRate') or 640000) for x in extra) * secs / 8 / 1e9 >= 1.0):
            return REPACK + (' (%d dub%s dropped)' % (len(dubs), '' if len(dubs) == 1 else 's') if dubs else ' (%d soundtracks: one kept)' % (len(extra) + 1))
        # (a file much bigger than its picture needs: made smaller, once, at the careful setting, last of all (John, 12 Nov 2026:
        # "converted to a smaller size without losing noticeable quality", "I don't want to convert films twice"). Grifflix's own
        # conversions are all under 8 Mbps, so never made again; HEVC is already efficient, so only far bigger ones)
        if not HEVC_OUT and not hdr and worth_smaller(mbps, w, h, vc):   # (the one rule: worth_smaller)
            return SHRINK + ' (%.1f Mbps)' % mbps
        return None
    # (a copy already poor isn't converted: it would come out no better, and then be one to replace; left as it is, the Library saying replace
    # it: John, 25 Nov 2026, "we shouldn't ever be converting things and then saying we should replace them. I don't want to be making poor
    # quality versions of things")
    if HEVC_OUT:   # (its picture stays as it is: reencode_reason said so above)
        return None
    b_ = pic_bpp(mbps, w, h, vc)
    if b_ is not None and b_ < POOR:
        return None
    return 'format (%s%s)' % (vc or '?', ', 10-bit' if bits > 8 or '10' in prof else '')


def shrinks(ms):
    """A film to be repackaged whose picture is much bigger than it need be: its rate (Mbps) if so, else 0. On John's Mac it's then made
    smaller straight away, not only repackaged: at least one and a half times the converted rate (6 Mbps at 1080p, 3.5 at 720p, 2 below);
    never HEVC (H.264 needs more for the same picture), HDR (its colours), or one bigger than 1080p (converted anyway). John, 25 Nov 2026,
    "if that's happening on my mac it should say it's a full conversion job" ... "Convert only if smaller"."""
    streams = g(ms, 'MediaStreams', [])
    v = next((x for x in streams if g(x, 'Type') == 'Video'), None)
    if not v or is_hdr(v):
        return 0
    w, h = g(v, 'Width', 0), g(v, 'Height', 0)
    if w > MAX_W + 8 or h > MAX_H + 8:
        return 0
    mbps = (g(ms, 'Bitrate') or g(v, 'BitRate') or 0) / 1e6
    if not mbps and g(ms, 'Size') and g(ms, 'RunTimeTicks'):
        mbps = g(ms, 'Size') * 8 / (g(ms, 'RunTimeTicks') / 1e7) / 1e6
    return round(mbps, 1) if worth_smaller(mbps, w, h, g(v, 'Codec')) else 0   # (the one rule)


NOSHRINK_F = os.path.join(os.path.expanduser('~/Grifflix'), 'convert-noshrink.json')


def noshrink():
    """Films made smaller once that saved too little (the original kept): never made smaller again, only repackaged if they need it."""
    try:
        return json.load(open(NOSHRINK_F)) or {}
    except (OSError, ValueError):
        return {}


REREAD = set()   # (films the media server was asked to read again, this run: once each)
VIDEO = ('.mkv', '.mp4', '.m4v', '.avi', '.mov', '.ts', '.m2ts', '.webm', '.wmv', '.mpg', '.mpeg', '.flv', '.vob')


def gather():
    libs = s.get('libraries', {})
    out = []
    NS_ = noshrink()
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
                p_ = g(it, 'Path') or ''
                if not any(g(x, 'Type') == 'Video' for x in g(ms, 'MediaStreams', [])) and p_.lower().endswith(VIDEO) and os.path.exists(p_) and g(it, 'Id') not in REREAD:
                    # (no picture known: the media server read the file while it was still being copied in, and the converter couldn't
                    # tell what it needs, so it was skipped (John, 13 Nov 2026: Obsession, copied in overnight). Read again: the next look has it)
                    REREAD.add(g(it, 'Id'))
                    try:
                        call('POST', '/Items/%s/Refresh' % g(it, 'Id'), metadataRefreshMode='FullRefresh', imageRefreshMode='Default',
                             replaceAllMetadata='false', replaceAllImages='false')
                        log('  %s: the media server had no details of its file (read while being copied in?): asked to read it again' % g(it, 'Name'))
                    except Exception:
                        pass
                if w and not w.startswith(REPACK) and g(it, 'Id') in NS_:   # (made once, and it saved too little, or came out poorer: left as it is)
                    w = None
                if w and g(it, 'Path') and os.path.exists(g(it, 'Path')):
                    name = g(it, 'Name') if types == 'Movie' else '%s S%02dE%02d' % (g(it, 'SeriesName'), g(it, 'ParentIndexNumber', 0), g(it, 'IndexNumber', 0))
                    out.append({'id': g(it, 'Id'), 'name': name, 'year': g(it, 'ProductionYear', ''), 'lib': lib, 'why': w, 'mode': 'remux' if w.startswith(REPACK) else 'shrink' if w.startswith(SHRINK) else 'convert',
                                'path': g(it, 'Path'), 'mins': round((g(it, 'RunTimeTicks') or 0) / 6e8), 'rating': g(it, 'CommunityRating') or 0,
                                'gb': round(os.path.getsize(g(it, 'Path')) / 1e9, 1), 'added': g(it, 'DateCreated') or '', 'big': shrinks(ms) if w.startswith(REPACK) and g(it, 'Id') not in NS_ else 0})
                    out[-1]['save'] = est_save(ms, out[-1]['gb'], out[-1]['mins'], out[-1]['mode'])
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


def fresh(out, st):
    """Films John has just brought in, to the top, by when the converter first saw them, not by the media server's "date added":
    that's the file's creation date, which a film made or downloaded long ago keeps when it's copied in, so it went in the
    library's usual order (John, 13 Nov 2026: "I added a new mkv and it hasn't been added to the top"). A film is known by its
    file's name without the extension, so the converter's own MP4 of a film isn't new. The first time, a film counts as just
    brought in if its file arrived on the drive in the last three days (its change time: a copy can't keep that)."""
    fs = st.get('first_seen')
    seeding = fs is None
    fs = fs or {}
    made = set(os.path.splitext(d.get('to') or '')[0] for d in st.get('done', {}).values())
    t = int(time.time())
    for x in out:
        k = os.path.splitext(x['path'])[0]
        if k in fs:
            continue
        if k in made:
            fs[k] = 0
        elif seeding:
            try:
                fs[k] = int(os.stat(x['path']).st_ctime) if os.stat(x['path']).st_ctime > t - 3 * 86400 else 0   # (3 days: films moved in last week's tidy-up changed then)
            except OSError:
                fs[k] = 0
        else:
            fs[k] = t
    st['first_seen'] = fs
    try:
        save_state(st)
    except Exception:
        pass
    key = lambda x: fs.get(os.path.splitext(x['path'])[0], 0)
    newer = sorted([x for x in out if key(x) > t - 14 * 86400], key=key, reverse=True)
    for x in newer:
        x['new'] = True
    ids = set(x['id'] for x in newer)
    return newer + [x for x in out if x['id'] not in ids]


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


# the film's own language, for the sound everyone hears (the player can't change soundtrack): its production country's language
# (John, 15 Nov 2026: "if an mkv has multiple languages, how can I be sure that a repackage will do the right one?")
SAME = {'fra': 'fre', 'deu': 'ger', 'zho': 'chi', 'nld': 'dut', 'ces': 'cze', 'fas': 'per', 'ron': 'rum', 'ell': 'gre', 'isl': 'ice',
        'slk': 'slo', 'msa': 'may', 'mya': 'bur', 'sqi': 'alb', 'hye': 'arm', 'kat': 'geo', 'eus': 'baq', 'mkd': 'mac', 'cym': 'wel',
        'en': 'eng', 'fr': 'fre', 'de': 'ger', 'es': 'spa', 'it': 'ita', 'ja': 'jpn', 'ko': 'kor', 'zh': 'chi', 'pt': 'por', 'ru': 'rus'}
COUNTRY = {'United States of America': 'eng', 'United States': 'eng', 'United Kingdom': 'eng', 'Ireland': 'eng gle', 'Australia': 'eng',
           'New Zealand': 'eng', 'Canada': 'eng fre', 'France': 'fre', 'Belgium': 'fre dut', 'Switzerland': 'ger fre ita', 'Spain': 'spa cat',
           'Mexico': 'spa', 'Argentina': 'spa', 'Chile': 'spa', 'Colombia': 'spa', 'Cuba': 'spa', 'Peru': 'spa', 'Italy': 'ita', 'Germany': 'ger',
           'West Germany': 'ger', 'East Germany': 'ger', 'Austria': 'ger', 'Japan': 'jpn', 'South Korea': 'kor', 'Korea': 'kor', 'China': 'chi',
           'Hong Kong': 'chi', 'Taiwan': 'chi', 'Brazil': 'por', 'Portugal': 'por', 'Russia': 'rus', 'Soviet Union': 'rus', 'Poland': 'pol',
           'Sweden': 'swe', 'Denmark': 'dan', 'Norway': 'nor', 'Finland': 'fin', 'Iceland': 'ice', 'Netherlands': 'dut', 'Czech Republic': 'cze',
           'Czechoslovakia': 'cze slo', 'Hungary': 'hun', 'Romania': 'rum', 'Greece': 'gre', 'Turkey': 'tur', 'Iran': 'per', 'Israel': 'heb',
           'India': 'hin tam tel mal ben', 'Thailand': 'tha', 'Indonesia': 'ind', 'Philippines': 'fil tgl eng', 'Vietnam': 'vie',
           'Egypt': 'ara', 'Lebanon': 'ara fre', 'Saudi Arabia': 'ara', 'Senegal': 'fre wol', 'Ukraine': 'ukr rus', 'Georgia': 'geo'}
NAMES = {'eng': 'English', 'fre': 'French', 'ger': 'German', 'spa': 'Spanish', 'ita': 'Italian', 'jpn': 'Japanese', 'kor': 'Korean',
         'chi': 'Chinese', 'por': 'Portuguese', 'rus': 'Russian', 'pol': 'Polish', 'swe': 'Swedish', 'dan': 'Danish', 'nor': 'Norwegian',
         'fin': 'Finnish', 'dut': 'Dutch', 'cze': 'Czech', 'hun': 'Hungarian', 'gre': 'Greek', 'tur': 'Turkish', 'per': 'Persian',
         'heb': 'Hebrew', 'hin': 'Hindi', 'ara': 'Arabic', 'cat': 'Catalan', 'tha': 'Thai', 'ukr': 'Ukrainian', 'rum': 'Romanian'}


def lang(x):
    l = ((x.get('tags') or {}).get('language') or '').lower()
    return SAME.get(l, l) if l not in ('und', 'unk', 'mis', 'zxx', '') else ''


def lang_name(x):
    l = lang(x)
    return NAMES.get(l, l.upper() if l else 'unnamed')


def commentary(a):
    return bool((a.get('disposition') or {}).get('comment')) or 'comment' in ((a.get('tags') or {}).get('title') or '').lower()


FORMAT_ONLY = re.compile(r'^\s*(\W*(stereo|mono|surround|downmix(ed)?|original|main|english|eng|dolby|digital|dd|ac-?3|e-?ac-?3|aac|dts|pcm|lpcm|flac|'
                         r'2\.0|1\.0|5\.1|7\.1|ch|channels?|audio|track|\d+\s*kbps|kbps)\W*)*\s*$', re.I)


def keep_beside(a, main, title=None, chans=None):
    """Kept beside the one soundtrack: a commentary, however it's named ("Neil Jordan", "Director and cast") or unnamed: any other
    stereo or mono track in the film's own language that isn't just a stereo copy (its name only "Stereo", "2.0"…) or an audio
    description. A dub, or a second surround copy, isn't (John, 19 Nov 2026: "If it's commentary I need that too")."""
    t = (title if title is not None else ((a.get('tags') or {}).get('title') or '')).strip().lower()
    if 'comment' in t or (title is None and commentary(a)):
        return True
    if re.search(r'descri|audio desc|\bad\b|narrat|visually', t):
        return False
    if int(chans if chans is not None else (a.get('channels') or 2)) > 2:
        return False
    if t and FORMAT_ONLY.match(t):
        return False
    return True


def chosen_lang(iid):
    try:
        return (json.load(open(PRIO)).get('langs') or {}).get(iid)
    except (OSError, ValueError):
        return None


def pick_sound(audio, item, s):
    """The soundtrack everyone hears: always the film's original language, never a dub (John, 15 Nov 2026: "I always want the
    original language - never the dub"). Returns (track, why, unsure): John's own choice for this film (a language's name); else
    the one the file marks as the original; else, where its soundtracks are in more than one language, the one in the language of
    the country the film is from (the media server's details, from TMDB), when exactly one language fits. Otherwise the best
    guess (one of the country's languages, else the file's default), unsure: the original is kept and John told, to change it
    if it's wrong ("don't wait - but flag to me, and don't delete original if in doubt"). Never a commentary."""
    cand = [a for a in audio if not commentary(a)] or audio
    if not cand:
        return None, '', False
    if item.get('redo'):   # (made again with John's language: not the stereo copy made last time, as the first choice)
        cand = sorted(cand, key=lambda a: ((a.get('tags') or {}).get('title') == 'Stereo', -(a.get('channels') or 2)))
    if item.get('english'):   # (an English film whose first soundtrack was a dub: English first, John, 21 Nov 2026)
        a = next((a for a in cand if lang(a) in ('eng', 'en')), None)
        if a:
            return a, 'English, its own language', False
    mine = chosen_lang(item['id'])
    if mine and mine != 'ok':
        a = next((a for a in cand if lang_name(a) == mine), None)
        if a:
            return a, 'your choice', False
    if len(cand) == 1:
        return cand[0], '', False
    o = [a for a in cand if (a.get('disposition') or {}).get('original')]
    if o:
        return o[0], 'marked as the original', False
    d = [a for a in cand if (a.get('disposition') or {}).get('default')]
    if len(set(lang(a) or '?%d' % a['index'] for a in cand)) == 1:   # (all in one language: its surround or its stereo, not a dub)
        return (d or cand)[0], '', False
    try:
        where = g(call('GET', '/Users/%s/Items/%s' % (s['jf_user_id'], item['id']), Fields='ProductionLocations') or {}, 'ProductionLocations', []) or []
    except Exception:
        where = []
    for c in where:
        fit = [a for a in cand if lang(a) and lang(a) in (COUNTRY.get(c) or '').split()]
        if fit and len(set(lang(a) for a in fit)) == 1:
            return fit[0], 'the language of %s' % c, False
        if fit:   # (more than one of its languages, as Canada's English and French: the file's default among them, unsure)
            return ([a for a in fit if a in d] or fit)[0], 'one of the languages of %s' % c, True
    return (d or cand)[0], 'a guess: ' + ('none of its soundtracks is in the language of %s' % ', '.join(where[:2]) if where else 'its country isn’t known'), True


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
    st = info.get('streams') or []
    au = [lang_name(a) + (' commentary' if commentary(a) else '') for a in st if a.get('codec_type') == 'audio']
    sb = [lang_name(x) + ('' if x.get('codec_name') in ('subrip', 'srt', 'ass', 'ssa', 'mov_text', 'webvtt', 'text') else ' (picture)') for x in st if x.get('codec_type') == 'subtitle']
    return {'v': vc, 'w': v.get('width') or 0, 'h': v.get('height') or 0, 'mbps': round(float(fmt.get('bit_rate') or 0) / 1e6, 1), 'gb': round(gb, 1),
            'sound': list(dict.fromkeys(au)), 'subs': list(dict.fromkeys(sb))}


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

def fingerprint(path):
    """OpenSubtitles' fingerprint of a file (its size, and its first and last 64 KB added up as 64-bit numbers): what subtitles made for it
    are known by."""
    import struct
    size = os.path.getsize(path)
    if size < 131072:
        return None
    h = size
    with open(path, 'rb') as f:
        for where in (0, size - 65536):
            f.seek(where)
            for (n,) in struct.iter_unpack('<Q', f.read(65536)):
                h = (h + n) & 0xFFFFFFFFFFFFFFFF
    return '%016x' % h


def convert(item, ffmpeg, ffprobe, st):
    src = item['path']
    try:   # (its fingerprint taken now, while it's as it came: kept with the new file once made, below)
        item['_fp'], item['_size'] = fingerprint(src), os.path.getsize(src)
    except OSError:
        pass
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
    # (HDR, made into an ordinary picture, on John's Mac only: its brightness brought into the ordinary range and its wider colours into
    # ordinary ones (zscale and tonemap, Hable's curve), then 8-bit like every other film; John, 20 Nov 2026. The Mac mini's own ffmpeg can't,
    # so here it's never converted, only planned for his Mac)
    hdr_ = (v.get('color_transfer') or '') in ('smpte2084', 'arib-std-b67')
    if hdr_ and not remux:
        if not item.get('plan_only') and not item.get('premade'):   # (made on John's Mac already: only put in place here: WALL·E, 20 Nov 2026)
            raise RuntimeError('it’s HDR: converted on John’s Mac only')
        tin = 'arib-std-b67' if v.get('color_transfer') == 'arib-std-b67' else 'smpte2084'
        vf += (',zscale=tin=%s:pin=bt2020:min=bt2020nc:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,'
               'zscale=t=bt709:m=bt709:r=tv,format=yuv420p' % tin)
    cmd = [ffmpeg, '-hide_banner', '-nostdin', '-y', '-i', src, '-map', '0:%d' % v['index']]
    main_a, chose, unsure = pick_sound(audio, item, s)
    if main_a:   # (of that language's soundtracks, the fullest: its 5.1, not a stereo copy made from it before)
        same_ = [a for a in audio if not commentary(a) and lang(a) == lang(main_a)]
        main_a = max(same_ or [main_a], key=lambda a: (int(a.get('channels') or 2), a is main_a))
    if main_a:   # sound: ONE soundtrack, the film's own language, as AAC (plays everywhere), its surround kept as 5.1; and its commentaries.
        # The dubs and the second copies dropped: a new film's eight dubs were more than half of it (John, 19 Nov 2026: Disclosure Day
        # "only went down to 11gb after conversion", "I only need one English soundtrack")
        cmd += ['-map', '0:%d' % main_a['index']]
        keep = [a for a in audio if a is not main_a and (commentary(a) or (lang(a) in (lang(main_a), None, '', 'und') and keep_beside(a, main_a)))]
        for a in keep:
            cmd += ['-map', '0:%d' % a['index']]
        if len(audio) > 1:
            log('  sound: %s%s, one soundtrack%s (%d dropped)' % (lang_name(main_a), ' (%s)' % chose if chose else '', ' and %d commentar%s' % (len(keep), 'y' if len(keep) == 1 else 'ies') if keep else '', len(audio) - 1 - len(keep)))
    text_subs = [x for x in subs if x.get('codec_name') in ('subrip', 'srt', 'ass', 'ssa', 'mov_text', 'webvtt', 'text')]
    if mkv:
        cmd += ['-map', '0:s?', '-map', '0:t?']   # every subtitle (pictures too) and attached fonts, as they are
    else:
        for x in text_subs:
            cmd += ['-map', '0:%d' % x['index']]
    # (the picture's rate: never more than the original had (a small or old file isn't made big, which adds nothing), and at most 8 Mbps,
    # plenty for 1080p (John, 11 Nov 2026: "Some of the conversions are still coming out a very large file size Braveheart 8gb"))
    src_kbps = int(v.get('bit_rate') or 0) / 1000 or (float((info.get('format') or {}).get('bit_rate') or 0) / 1000 - 192 * max(1, len(audio)))
    # (compact and careful, once: x264's slow preset at quality 21, at most 6 Mbps at 1080p, 3.5 at 720p, 2 below, never more than the
    # original had: much smaller with no difference to see (John, 12 Nov 2026); slower to make, so done once and never again)
    oh = min(int(v.get('height') or 1080), MAX_H)
    ow0_ = min(int(v.get('width') or 1920), MAX_W)
    top = int(size_top(ow0_, oh) * 1000 / (HE if HEVC_OUT else 1))   # (in the made film's own terms: HEVC needs less)
    # (H.264 needs about 1.6 times the rate of HEVC or VP9 for the same picture, twice AV1's: an efficient picture held to its own rate came
    # out worse, and compact films converted on John's Mac (Days of Being Wild, Marty Supreme, the 1930s VP9 films) were graded Replace
    # after: John, 25 Nov 2026, "The Replace section of the Library shows films that the mac has converted itself")
    vc_ = (v.get('codec_name') or '').lower()
    eff_ = (2.0 if vc_ == 'av1' else 1.6 if vc_ in ('hevc', 'h265', 'vp9') else 1.0 if vc_ == 'h264' else .6) / (HE if HEVC_OUT else 1)
    # (and never below the Library's line for a good copy of its size, with a third to spare: a converted film is never one to replace)
    ih_, iw_ = int(v.get('height') or 1080), int(v.get('width') or 1920)
    ow_ = iw_ * oh / ih_ if ih_ else 1920
    floor_ = POOR * ow_ * oh * 24 * 1.3 / 1000 / (HE if HEVC_OUT else 1)
    cap = int(min(top, max(750 if HEVC_OUT else 1200, floor_, src_kbps * eff_ if src_kbps > 0 else top)))
    cmd += ['-map_metadata', '0', '-map_chapters', '0']
    if remux:   # (the picture copied as it is; HEVC labelled hvc1, as Apple's devices need, not hev1)
        cmd += ['-c:v', 'copy', '-disposition:v:0', 'default'] + (['-tag:v', 'hvc1'] if v.get('codec_name') == 'hevc' else [])
    elif HEVC_OUT:   # (HEVC, 8-bit, labelled hvc1; on John's Mac, Apple's hardware encoder at a quality: the gateway's vt_args)
        cmd += ['-c:v', 'libx265', '-preset', 'medium', '-crf', '22', '-maxrate', '%dk' % cap, '-bufsize', '%dk' % (cap * 2),
                '-profile:v', 'main', '-tag:v', 'hvc1', '-pix_fmt', 'yuv420p', '-vf', vf, '-disposition:v:0', 'default']
    else:
        cmd += ['-c:v', 'libx264', '-preset', 'slow', '-crf', '21', '-maxrate', '%dk' % cap, '-bufsize', '%dk' % (cap * 2),
                '-profile:v', 'high', '-level', '4.1', '-pix_fmt', 'yuv420p', '-vf', vf, '-disposition:v:0', 'default']
    if main_a:
        ch_ = 6 if int(main_a.get('channels') or 2) >= 6 else 2
        cmd += ['-c:a:0', 'aac', '-b:a:0', '384k' if ch_ == 6 else '192k', '-ac:a:0', str(ch_), '-metadata:s:a:0', 'title=' + ('%s 5.1' % lang_name(main_a) if ch_ == 6 else 'Stereo'), '-disposition:a:0', 'default']
        for n, a in enumerate(keep, 1):
            fits = mkv or a.get('codec_name') in ('ac3', 'eac3', 'aac')
            cmd += ['-c:a:%d' % n, 'copy'] if fits else ['-c:a:%d' % n, 'aac', '-b:a:%d' % n, '128k', '-ac:a:%d' % n, '2']
            if commentary(a):   # (still marked as a commentary, and named one if it had no name, so the players list it as one: John, 18 Nov 2026)
                cmd += ['-disposition:a:%d' % n, 'comment']
                if not ((a.get('tags') or {}).get('title') or '').strip():
                    cmd += ['-metadata:s:a:%d' % n, 'title=Commentary']
            else:
                cmd += ['-disposition:a:%d' % n, '0']
    if mkv:
        cmd += ['-c:s', 'copy', '-c:t', 'copy']
    else:
        # (the index put at the front, so it plays from the start at once, is a second pass that rewrites the whole file, showing
        # nothing: for a big film repackaged here, over this Mac's USB 2, that's an hour or more, and it was given up on twice for
        # Disclosure Day (40.6 GB). Known before it starts, so not done: the index stays at the end, and players (the media server, browsers,
        # TVs) ask for that part first, as they do for any large file. A file made smaller is small, so it keeps it: John, 18 Nov 2026)
        big = remux and os.path.getsize(src) > 10e9
        cmd += ['-c:s', 'mov_text'] + ([] if big else ['-movflags', '+faststart'])
    if item.get('english'):   # (no other language's subtitles shown by themselves over the English: Jesus of Nazareth's Bulgarian)
        cmd += ['-disposition:s', '0']
    cmd += ['-max_muxing_queue_size', '9999', tmp]

    if item.get('plan_only'):   # (for the Mac John's on, which converts it with its own hardware: what to do, not done here)
        return {'cmd': cmd[1:], 'src': src, 'tmp': tmp, 'dur': dur, 'remux': remux}
    t0 = time.time()
    if not item.get('premade'):   # (premade: made already, by that Mac, into tmp; checked and put in place here)
        log('repackaging' if remux else 'converting', item['name'], '(%s, %s min, %s GB)' % (item['why'], item['mins'], item['gb']))
        t0, paused = time.time(), False
        # (no longer at macOS's background priority for the drive: whenever anything else used it (Spotlight, a copy over the network)
        # that starved it to nothing, stuck at 0% for hours; it pauses the moment someone watches instead, checked every 5 seconds:
        # John, 14 Nov 2026, "It doesn't need to run on lowest priority if is just pauses when a movie is playing anyway")
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, preexec_fn=lambda: os.nice(15))
        err_tail, at, second = [], [0.0], [0.0]
        def read_err():
            for line in p.stderr:   # (ffmpeg's progress lines: "... time=00:41:07.12 ...")
                m = re.search(r'time=(\d+):(\d\d):(\d\d(?:\.\d+)?)', line)
                if m:
                    at[0] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                else:
                    if 'Starting second pass' in line:   # (the file rewritten so it plays at once: no progress shown, and slow on a big film)
                        second[0] = time.time()
                    err_tail.append(line)
                    del err_tail[:-30]
        threading.Thread(target=read_err, daemon=True).start()
        show = lambda why_='': now('paused' if why_ else 'converting', id=item['id'], name=item['name'], why=why_ or item['why'], gb=item['gb'], mins=item['mins'],
                                    pct=round(min(99.9, at[0] / dur * 100), 1) if dur else 0, since=int(t0))
        show()
        try:
            k, work, mark, stuck_since = 0, 0.0, (0.0, 0), 0.0   # (work: seconds converting, not paused; mark: the last sign of progress)
            STALL = int(os.environ.get('GRIFFLIX_STALL') or 1800)
            while p.poll() is None:
                time.sleep(5)
                k += 1
                # (a film that makes no progress for 30 minutes of work is given up, what ffmpeg last said kept, and the next one done:
                # Moonrise hung at 0% all day, the whole queue behind it: John, 14 Nov 2026, "It's never got above 0%")
                if not paused:
                    work += 5
                    try:
                        sz = os.path.getsize(tmp)
                    except OSError:
                        sz = 0
                    if at[0] > mark[0] or sz > mark[1] + 1000000:
                        mark, stuck_since = (at[0], sz), work
                    # (ffmpeg's last step, rewriting the whole file so it plays from the start, says nothing and grows nothing: allowed
                    # time for the film's size at 4 MB/s, the G-Drive at its slowest. Wild at Heart, 30.7 GB, was given up 30 minutes into it
                    # and would have been tried again and again: 18 Nov 2026)
                    elif work - stuck_since > (max(STALL, (item.get('gb') or 0) * 1e9 / 4e6 + 600) if second[0] else STALL):
                        p.terminate()
                        try:
                            p.wait(30)
                        except subprocess.TimeoutExpired:
                            p.kill(); p.wait()
                        if os.path.exists(tmp):
                            os.remove(tmp)
                        said = ' '.join(x.strip() for x in err_tail[-4:] if x.strip())[-300:]
                        log('  no progress in %d minutes: given up' % round((work - stuck_since) / 60) + (' (ffmpeg: %s)' % said if said else ''))
                        raise RuntimeError('no progress in %d minutes, given up' % round((work - stuck_since) / 60) + (': ' + said if said else ''))
                try:   # (John's Skip: this film given up now, the next one started)
                    skip = open(SKIP).read().strip() == item['id']
                except OSError:
                    skip = False
                if skip:
                    os.remove(SKIP)
                    if paused:
                        p.send_signal(signal.SIGCONT)
                        paused = False
                    p.terminate()
                    p.wait()
                    if os.path.exists(tmp):
                        os.remove(tmp)
                    log('  skipped by John')
                    raise RuntimeError('skipped by John')
                if k % 12 == 6:   # (every minute: films just brought in, shown as next now; one to repackage done at once, this film paused)
                    new = [f for f in (peek_new() or []) if f.get('mode') == 'remux']
                    if new and not item.get('fetched') and not item.get('redo'):
                        held = not paused
                        if held:
                            p.send_signal(signal.SIGSTOP)
                        log('  paused while %s, just brought in, is repackaged' % ', '.join(f['name'] for f in new))
                        for f in new:
                            try:
                                convert(dict(f, fetched=True, urgent=True), ffmpeg, ffprobe, st)
                                save_state(st)
                                done_new(f['path'])
                                unpin(f['id'])   # (done: off the list of what's next, where it sat looking left undone: John, 16 Nov 2026)
                            except RuntimeError as e:
                                log('  couldn’t repackage %s now (%s): it goes next' % (f['name'], str(e)[:160]))
                        if held:
                            p.send_signal(signal.SIGCONT)
                        log('  back to ' + item['name'])
                        show()
                if k % 4 == 0:   # (how far it's got: every 20 seconds)
                    show(why_ if paused else '')
                if k % 12 == 0 and not os.path.exists(src):   # (its folder renamed meanwhile (Rozmarné léto to Capricious Summer, 23 Nov 2026): stopped, done again under its new name)
                    raise RuntimeError(MOVED)
                if mac_converting():   # (stopped and cleared, not paused: John, 18 Nov 2026)
                    raise MacBusy()
                why_ = 'someone is watching' if watching() else 'a film is downloading' if item.get('mode') == 'remux' and not item.get('fetched') and not item.get('new') and not item.get('redo') and downloading() else ('it’s past %d am: on again at %d am' % (NIGHT[1], NIGHT[0]) if not night() and not item.get('urgent') else '')
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
    if item.get('mode') == 'shrink' and os.path.getsize(tmp) > os.path.getsize(src) * 0.75:   # (made smaller only if it really is: else the original stays)
        a_, b_ = os.path.getsize(src) / 1e9, os.path.getsize(tmp) / 1e9
        os.remove(tmp)
        try:   # (and remembered: never made smaller again, so it doesn't come back to the queue each week to be made for nothing)
            ns_ = noshrink()
            ns_[item['id']] = {'name': item.get('name'), 'at': int(time.time()), 'was_gb': round(a_, 1), 'made_gb': round(b_, 1)}
            json.dump(ns_, open(NOSHRINK_F, 'w'))
        except Exception:
            pass
        raise RuntimeError('made smaller, it saved too little (%.1f GB → %.1f GB): the original kept as it is' % (a_, b_))
    ninfo_ = probe(ffprobe, tmp)
    new_dur = float((ninfo_.get('format') or {}).get('duration') or 0)
    if abs(new_dur - dur) > max(3, dur * .01):
        os.remove(tmp)
        raise RuntimeError('the new file is %.0f s long, the original %.0f s: kept the original' % (new_dur, dur))
    if not remux:   # (its picture made again: never a poorer copy than it was, by the Library's measure; else the original kept, and left as it is)
        try:
            nv_ = next((x for x in ninfo_.get('streams') or [] if x.get('codec_type') == 'video'), {})
            n_mbps = os.path.getsize(tmp) * 8 / max(1.0, new_dur) / 1e6
            s_mbps = os.path.getsize(src) * 8 / max(1.0, dur) / 1e6
            nb_ = pic_bpp(n_mbps, nv_.get('width') or 0, nv_.get('height') or 0, nv_.get('codec_name'))
            sb_ = pic_bpp(s_mbps, v.get('width') or 0, v.get('height') or 0, v.get('codec_name'))
            if nb_ is not None and sb_ is not None and sb_ >= POOR and nb_ < POOR * .7:
                os.remove(tmp)
                try:
                    ns_ = noshrink()
                    ns_[item['id']] = {'name': item.get('name'), 'at': int(time.time()), 'was_mbps': round(s_mbps, 1), 'made_mbps': round(n_mbps, 1), 'poorer': True}
                    json.dump(ns_, open(NOSHRINK_F, 'w'))
                except Exception:
                    pass
                raise RuntimeError('it came out a poorer copy than it was (%.1f Mbps from %.1f): the original kept, and left as it is' % (n_mbps, s_mbps))
        except RuntimeError:
            raise
        except Exception:
            pass

    # the new one in the original's place (moved in only now, whole and checked), and the original deleted: John keeps his own backups
    # (John, 12 Nov 2026: "delete all originals automatically - I have backups so it's fine"); kept in Grifflix Originals only if
    # the setting keep_originals is on
    before = brief(info, src)
    # (picture subtitles (Blu-ray PGS, DVD VobSub), which an MP4 can't hold: each saved beside the new film as it is, a .sup or .mks named for
    # the film and its language, so the media server offers them and none is lost when the original goes; listed in picture-subs.json to be
    # turned into text as a job of its own later. One that can't be saved: the original kept, whatever the setting. John, 20 Nov 2026, "they
    # should be saved and then converted as a separate job later")
    pics = [x for x in subs if x.get('codec_name') in ('hdmv_pgs_subtitle', 'pgssub', 'dvd_subtitle', 'dvdsub', 'dvb_subtitle')]
    saved, keep_pics = [], False
    if pics and not item.get('plan_only'):
        bdst = os.path.splitext(dst)[0]
        for n, x in enumerate(pics):
            l_ = lang(x) or 'und'
            dp = x.get('disposition') or {}
            tag = '.forced' if dp.get('forced') else '.sdh' if dp.get('hearing_impaired') else ''
            ext_ = '.sup' if 'pgs' in x.get('codec_name', '') else '.mks'
            out = '%s.%s%s%s' % (bdst, l_, tag, ext_)
            if os.path.exists(out) or out in [y['file'] for y in saved]:
                out = '%s.%s%s.%d%s' % (bdst, l_, tag, n + 1, ext_)
            try:
                r_ = subprocess.run([ffmpeg, '-hide_banner', '-nostdin', '-y', '-v', 'error', '-i', src, '-map', '0:%d' % x['index'], '-c:s', 'copy', out],
                                    capture_output=True, text=True, timeout=1800)
                ok_ = r_.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 100
            except Exception:
                ok_ = False
            if ok_:
                saved.append({'file': out, 'lang': l_, 'name': lang_name(x), 'kind': 'PGS' if ext_ == '.sup' else 'VobSub', 'forced': bool(dp.get('forced')),
                              'sdh': bool(dp.get('hearing_impaired')), 'title': ((x.get('tags') or {}).get('title') or '')[:80]})
            else:
                keep_pics = True
                try:
                    os.remove(out)
                except OSError:
                    pass
        if saved:
            log('  picture subtitles saved beside it: %s' % ', '.join('%s (%s)' % (y['name'], y['kind']) for y in saved))
            try:
                pf = os.path.join(HOME, 'picture-subs.json')
                P = json.load(open(pf)) if os.path.exists(pf) else {}
                P[item['id']] = {'name': item['name'], 'year': item.get('year'), 'film': dst, 'subs': saved, 'at': int(time.time()), 'text': False}
                json.dump(P, open(pf + '.tmp', 'w'), indent=1)
                os.replace(pf + '.tmp', pf)
            except Exception as e:
                log('  (picture subtitles list: %s)' % e)
        if keep_pics:
            log('  a picture subtitle couldn’t be saved on its own: the original kept, so it isn’t lost')
    if not os.path.exists(src):   # (moved or renamed while it was being made: the new file not put where the old one was)
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(MOVED)
    if (s.get('keep_originals') or unsure or keep_pics) and not item.get('redo'):   # (unsure of the language, or a picture subtitle not saved: the original kept, whatever the setting)
        keep_at = os.path.join(os.path.dirname(root.rstrip('/')), 'Grifflix Originals', os.path.basename(root.rstrip('/')), os.path.relpath(src, root))
        os.makedirs(os.path.dirname(keep_at), exist_ok=True)
        shutil.move(src, keep_at)
    else:
        keep_at = None
        os.remove(src)
    shutil.move(tmp, dst)   # (the same drive: a rename)
    mins = (time.time() - t0) / 60
    log('  done in %d min: %.1f GB → %.1f GB; original %s' % (mins, item['gb'], os.path.getsize(dst) / 1e9, ('kept in ' + os.path.dirname(keep_at)) if keep_at else 'deleted'))
    try:   # (kept for good: John, 22 Nov 2026, "keep a log of everything")
        with open(os.path.join(HOME, 'record.jsonl'), 'a', encoding='utf-8') as f_:
            f_.write(json.dumps({'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'what': 'repackaged' if remux else 'converted', 'film': item['name'], 'id': item['id'],
                                 'from': src, 'to': dst, 'original_kept_at': keep_at, 'why': item.get('why'),
                                 'soundtrack_first': lang_name(main_a) if main_a else ''}, ensure_ascii=False) + '\n')
    except Exception:
        pass
    # (the file as it came, remembered by what subtitles made for it are known by, its fingerprint and its name: the new file has neither, and
    # subtitles made for this very copy would be lost to it: John, 22 Nov 2026, "In future we need to make sure conversion either doesn't lose
    # fingerprint or we have enough information to make sure we know it's the right subtitle")
    try:
        fpf = os.path.join(HOME, 'fingerprints.json')
        try:
            FP = json.load(open(fpf))
        except (OSError, ValueError):
            FP = {}
        prior = FP.get(src) or {}
        FP[dst] = {'hash': prior.get('hash') or item.get('_fp'), 'hashes': list(dict.fromkeys((prior.get('hashes') or []) + [h for h in (prior.get('hash'), item.get('_fp')) if h])),
                   'name': prior.get('name') or os.path.basename(src), 'size': prior.get('size') or item.get('_size'), 'film': item['name'], 'at': int(time.time())}
        json.dump(FP, open(fpf + '.tmp', 'w'), indent=1)
        os.replace(fpf + '.tmp', fpf)
    except Exception as e:
        log('  (its fingerprint not kept: %s)' % e)
    st['done'][item['id']] = {'name': item['name'], 'from': src, 'to': dst, 'kept': keep_at, 'at': int(time.time()), 'mins': round(mins),
                              'why': item['why'], 'before': before, 'after': brief(probe(ffprobe, dst), dst),
                              'chose': (lang_name(main_a) + (': ' + chose if chose else '')) if main_a and len(audio) > 1 else ''}
    try:   # (and in the history of every film made, never cut short: the Library says which Grifflix made, and from what: 25 Nov 2026)
        with open(os.path.join(HOME, 'convert-history.jsonl'), 'a', encoding='utf-8') as f_:
            f_.write(json.dumps(dict(st['done'][item['id']], id=item['id'], where='mac' if item.get('premade') else 'mini', mode=item.get('mode') or ''), ensure_ascii=False) + '\n')
    except Exception:
        pass
    if unsure:   # (told on the admin page, with each language to change it to)
        st.setdefault('ask', {})[item['id']] = {'name': item['name'], 'year': item.get('year'), 'to': dst, 'kept': keep_at, 'chose': lang_name(main_a), 'why': chose,
            'options': list(dict.fromkeys(lang_name(a) for a in audio if not commentary(a))), 'at': int(time.time())}
        log('  NOT SURE of its language: %s chosen (%s); the original kept. Change it on the admin page if it’s wrong' % (lang_name(main_a), chose))
    elif not item.get('redo'):
        st.get('ask', {}).pop(item['id'], None)
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
    ra = os.path.join(HOME, 'remap-add.json')   # (films a better download replaced (gdrive.py), with a new file name: followed the same way)
    if os.path.exists(ra):
        try:
            st.setdefault('remap', {}).update(json.load(open(ra)) or {})
            save_state(st)
            os.remove(ra)
        except (OSError, ValueError):
            pass
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

st.setdefault('ask', {})
EXTF = os.path.join(HOME, 'convert-external.json')   # (films converted on John's own Mac, put in place here: merged into this state)
CLAIMS = os.path.join(HOME, 'convert-claims.json')   # (films being converted on John's Mac this minute: left alone here)


def merge_ext():
    try:
        ext = json.load(open(EXTF))
        os.remove(EXTF)
    except (OSError, ValueError):
        return
    for k in ('done', 'failed', 'remap', 'ask'):
        st.setdefault(k, {}).update(ext.get(k) or {})
    save_state(st)


def claimed(iid):
    try:   # (queued for John's Mac: left to it: John, 18 Nov 2026)
        if any(x.get('id') == iid for x in json.load(open(os.path.join(HOME, 'mac-queue.json'))) or []):
            return True
    except (OSError, ValueError, AttributeError):
        pass
    try:
        return time.time() - float(json.load(open(CLAIMS)).get(iid, 0)) < 1800   # (renewed by the gateway with every word from John's Mac: lapses half an hour after the last)
    except (OSError, ValueError):
        return False


QF = os.path.join(HOME, 'mac-queue.json')


def queue_read():
    try:
        Q = json.load(open(QF))
    except (OSError, ValueError):
        Q = []
    return Q if isinstance(Q, list) else []


def queue_write(Q):
    tmp = QF + '.tmp'
    json.dump(Q, open(tmp, 'w'))
    os.replace(tmp, QF)


def job_claimed(iid):
    """Being made on John's Mac now (its claim renewed by the gateway with every word from it; lapsing half an hour after the last)."""
    try:
        return time.time() - float(json.load(open(CLAIMS)).get(iid, 0)) < 1800
    except (OSError, ValueError, AttributeError):
        return False


def plays_first(x):
    """Whether a film doesn't play everywhere yet, as it is: not an MP4 (an MKV, an AVI), or in a format not every device plays. Those go first
    on the queue, then the rest by the space each saves (John, 25 Nov 2026, "the aim should be at least to get everything to play first, so all
    mkvs, then we need to make sure that it is ordered by what will save the most space next")."""
    return os.path.splitext(x.get('path') or '')[1].lower() not in ('.mp4', '.m4v', '.mov') or str(x.get('why') or '').startswith('format')


def to_mac(items):
    """Everything there is to do, on the one queue, which John's Mac works through (John, 25 Nov 2026, "as the conversions don't run on my
    mac mini when my mac is on, and my mac is on most of the time, there's really no point in the queue being in two places. Queue everything
    for my mac"): films just come in, and any he pinned, at the top (after any he put first); the rest at the end, in the converter's order;
    each saying what it needs (a simple repackaging, or its picture made again). One taken off the queue by John, or passed over there, isn't
    put on again for a week. Films made since they were put on it taken off."""
    try:
        passed = set(y.get('id') for y in json.load(open(QF + '.passed')) or [] if time.time() - y.get('at', 0) < 3 * 86400)
    except (OSError, ValueError, AttributeError, TypeError):
        passed = set()
    sent = st.setdefault('tomac', {})
    try:
        pins = set(x.get('id') for x in prio()['pins'])
    except Exception:
        pins = set()
    Q = queue_read()
    have = set(y.get('id') for y in Q)
    top, end = [], []
    for x in items:
        if x.get('redo') or x['id'] in have or x['id'] in passed or time.time() - sent.get(x['id'], 0) < 7 * 86400 or not os.path.exists(x['path']):
            continue   # (a language John put right is the Mac mini's own: minutes, and only it can)
        e = dict({'id': x['id'], 'name': x['name'], 'at': int(time.time()), 'from': 'mini', 'why': (x.get('why') or '')[:160], 'kind': x.get('mode') or 'convert', 'save': x.get('save') or 0,
                  'play': plays_first(x)},
                 **({'force': True} if x.get('mode') == 'shrink' else {}), **({'big': x['big']} if x.get('mode') == 'remux' and x.get('big') else {}))
        if x['id'] in pins or x.get('fetched') or x.get('urgent') or x.get('new'):
            e['new'] = True
            top.append(e)
        else:
            end.append(e)
    Q = queue_read()   # (read again just before writing: John may have queued or taken one off meanwhile)
    n0 = len(Q)
    Q = [y for y in Q if not (y.get('from') == 'mini' and (st['done'].get(y.get('id')) or {}).get('at', 0) > (y.get('at') or 0))]   # (made since)
    # (and those the converter no longer has to do (a poor copy, left as it is for replacing: 25 Nov 2026), unless John's Mac is at it now)
    if items:
        live_ids = set(x['id'] for x in items)
        Q = [y for y in Q if y.get('from') != 'mini' or y.get('id') in live_ids or job_claimed(y.get('id') or '')]
    gone = n0 - len(Q)
    have = set(y.get('id') for y in Q)
    top = [y for y in top if y['id'] not in have]
    end = [y for y in end if y['id'] not in have]
    big_ = {x['id']: x.get('big') or 0 for x in items if x.get('mode') == 'remux' and not x.get('redo')}
    now_ = {x['id']: x for x in items if not x.get('redo')}
    fixed = 0
    for y in Q:   # (each the Mac mini put there: what it needs now (the plan changes: HEVC, 25 Nov 2026), whether it's made smaller, what it saves)
        if y.get('from') != 'mini' or y.get('id') not in now_:
            continue
        x = now_[y['id']]
        k_, w_, sv_, pl_ = x.get('mode') or 'convert', (x.get('why') or '')[:160], x.get('save') or 0, plays_first(x)
        if y.get('kind') != k_ or y.get('why') != w_ or y.get('save') != sv_ or (y.get('big') or 0) != big_.get(y['id'], 0) or y.get('play') != pl_:
            y.update(kind=k_, why=w_, save=sv_, play=pl_)
            if big_.get(y['id']):
                y['big'] = big_[y['id']]
            else:
                y.pop('big', None)
            if k_ == 'shrink':
                y['force'] = True
            fixed += 1
    moved = [dict(y, new=True) for y in Q if y.get('id') in pins and y.get('from') == 'mini' and not y.get('new')]   # (pinned since: to the top)
    if not (top or end or moved or gone or fixed):
        return []
    mids = set(y['id'] for y in moved)
    Q = [y for y in Q if y.get('id') not in mids]
    # (the backlog: first those that don't play everywhere yet (an MKV, a format not every device plays), then the rest, each in order of the
    # space it saves, the largest first; John's own, those just come in and those put first stay at the top)
    keep_ = lambda y: y.get('from') != 'mini' or y.get('new') or y.get('first')
    head_ = [y for y in Q if keep_(y)]
    tail_ = sorted([y for y in Q if not keep_(y)] + end, key=lambda y: (0 if y.get('play') else 1, -(y.get('save') or 0)))   # (those that don't play everywhere yet first)
    k = next((i for i, y in enumerate(head_) if not y.get('first')), len(head_))   # (after any John put first)
    Q = head_[:k] + top + moved + head_[k:] + tail_
    queue_write(Q)
    for y in top + end:
        sent[y['id']] = int(time.time())
    save_state(st)
    added = top + end
    if added:
        nr = sum(1 for y in added if y['kind'] == 'remux')
        log('  on the queue: %s%s' % (', '.join(y['name'] for y in added) if len(added) <= 5 else '%d films' % len(added),
                                      ' (%d to repackage, %d to convert)' % (nr, len(added) - nr) if len(added) > 5 else ''))
    return added


def mac_off():
    """John's Mac off, asleep or away from home: not heard from for ten minutes, by the gateway's note of when it last was."""
    try:
        a = json.load(open(os.path.join(HOME, 'mac-agent.json')))
    except (OSError, ValueError):
        return True
    return time.time() - float(a.get('seen') or 0) > 600 or a.get('home') is False


def mini_wait(redo=False):
    """Why the Mac mini mustn't take anything now ('' if it may): only while nothing is playing, and only while John's Mac is off (John,
    25 Nov 2026, "the only time the mac mini should then pick up jobs is if my mac is off, and then it should only be simple repackaging jobs
    while nothing is playing"); a language he put right, whenever his Mac isn't converting."""
    if watching():
        return 'someone is watching'
    if redo:
        return 'your Mac is converting' if mac_converting() else ''
    if not mac_off():
        return 'your Mac is on: it works through the queue, and the Mac mini only takes simple repackaging while your Mac is off'
    return ''


def mini_pick(rest):
    """The first simple repackaging on the queue (one the Mac mini put there), in the queue's order."""
    byid = {x['id']: x for x in rest if x.get('mode') == 'remux' and not x.get('redo')}
    for y in queue_read():
        if y.get('from') == 'mini' and y.get('kind') == 'remux' and y.get('id') in byid and not job_claimed(y['id']):
            return byid[y['id']]
    return None


def queue_take(iid):
    Q = queue_read()
    if any(y.get('id') == iid for y in Q):
        queue_write([y for y in Q if y.get('id') != iid])
    st.setdefault('tomac', {}).pop(iid, None)   # (if it can't be repackaged, it goes on again, to convert)


def queue_back(it):
    """One the Mac mini stopped part way (John's Mac came on): back at the top of the queue."""
    Q = [y for y in queue_read() if y.get('id') != it['id']]
    k = next((i for i, y in enumerate(Q) if not y.get('first')), len(Q))
    Q.insert(k, {'id': it['id'], 'name': it['name'], 'at': int(time.time()), 'from': 'mini', 'why': (it.get('why') or '')[:160], 'kind': 'remux', 'new': True})
    queue_write(Q)
    st.setdefault('tomac', {})[it['id']] = int(time.time())


SKIPWHY = {}   # (why one was passed over, said as it is, not "this or that": John, 20 Nov 2026, Wild at Heart "failed, but I don't see any message")


def shrink_item(iid, name=None, mac=False):
    """Any film, to be made smaller at the careful setting, whatever the converter would have said of it (the largest, chosen by John);
    not an HDR one (its colours would be spoilt)."""
    it = call('GET', '/Users/%s/Items/%s' % (s['jf_user_id'], iid), Fields='MediaSources,Path,ProductionYear,CommunityRating,DateCreated') or {}
    path = g(it, 'Path') or ''
    if not path or not os.path.exists(path):
        SKIPWHY[iid] = 'its file isn’t where the media server says it is' + (' (%s)' % os.path.basename(os.path.dirname(path)) if path else '') + ': nothing to convert.'
        return None
    v = next((x for x in g((g(it, 'MediaSources') or [{}])[0], 'MediaStreams') or [] if g(x, 'Type') == 'Video'), {})
    # (HDR: only on John's Mac, whose ffmpeg can turn its colours into ordinary ones (tone-mapping); this Mac mini's can't, and they came out
    # washed out: John, 20 Nov 2026, "there's no reason my mac shouldn't be able to do it ... allow me to queue on my mac and change the rule")
    rt = g(v, 'VideoRangeType') or g(v, 'VideoRange') or 'HDR'
    if is_hdr(v) and not mac:
        SKIPWHY[iid] = 'it’s HDR (%s): converted on your Mac only (queue it for your Mac).' % rt
        return None
    if is_hdr(v) and rt.upper() == 'DOVI':   # (Dolby Vision alone, with no ordinary HDR picture beneath it: its colours can't be worked out)
        SKIPWHY[iid] = 'it’s Dolby Vision only (no HDR10 picture beneath), which can’t be turned into ordinary colours: replace it with another copy.'
        return None
    # (made smaller only if it really is big for its size, allowing for how efficient its picture is: one already compact would only lose
    # picture. Marty Supreme, an efficient HEVC picture at 2.3 Mbps, was "made smaller" to H.264 at 1.6 and graded Replace: John, 25 Nov 2026,
    # "what was the cause of the Marty Supreme being made to replace? It was a very good copy before")
    ms_ = (g(it, 'MediaSources') or [{}])[0]
    w_, h_ = g(v, 'Width', 0), g(v, 'Height', 0)
    mbps_ = (g(ms_, 'Bitrate') or g(v, 'BitRate') or 0) / 1e6
    if not mbps_ and g(ms_, 'Size') and g(ms_, 'RunTimeTicks'):
        mbps_ = g(ms_, 'Size') * 8 / (g(ms_, 'RunTimeTicks') / 1e7) / 1e6
    vc_ = (g(v, 'Codec') or '').lower()
    if mbps_ and not worth_smaller(mbps_, w_, h_, vc_):   # (the one rule)
        SKIPWHY[iid] = 'it’s already compact for its size (%s, %.1f Mbps): made smaller it would only lose picture, so it’s left as it is.' % (
            {'hevc': 'HEVC', 'h265': 'HEVC', 'h264': 'H.264', 'vp9': 'VP9', 'av1': 'AV1'}.get(vc_, vc_.upper() or '?'), mbps_)
        return None
    return {'id': iid, 'name': g(it, 'Name') or name or '?', 'year': g(it, 'ProductionYear', ''), 'lib': 'movies' if g(it, 'Type') == 'Movie' else 'tv',
            'why': SHRINK + ' (chosen by you)', 'mode': 'shrink', 'path': path, 'mins': round((g(it, 'RunTimeTicks') or 0) / 6e8),
            'rating': g(it, 'CommunityRating') or 0, 'gb': round(os.path.getsize(path) / 1e9, 1), 'added': g(it, 'DateCreated') or ''}


if CMD in ('plan', 'finish'):   # (John's "Convert on this Mac", 16 Nov 2026: the plan made here, the film made there, put in place here)
    ffmpeg_, ffprobe_ = tools()
    if CMD == 'plan':
        found = [i for i in fresh(gather(), st) if i['id'] == sys.argv[2]]
        # (one queued for John's Mac: made smaller, never only repackaged, which there frees nothing and copies the whole film over the
        # network twice: Disclosure Day, 40.6 GB, at 0.6x: John, 18 Nov 2026, "I'm going to save space by converting the bigger files on my mac")
        if 'force' in sys.argv[3:] and (not found or found[0].get('mode') == 'remux') and sys.argv[2] not in noshrink():
            f = shrink_item(sys.argv[2], mac=True)
            # (HDR: made on John's Mac, its colours tone-mapped (convert); Dolby Vision alone passed over; one already compact not made smaller,
            # but still repackaged if that's what it needs)
            found = [f] if f else found
        if not found:
            print(json.dumps({'error': (SKIPWHY.get(sys.argv[2]) or 'it plays everywhere as it is: nothing to convert.')}))
            sys.exit()
        try:
            print(json.dumps(dict(convert(dict(found[0], plan_only=True, urgent=True), ffmpeg_, ffprobe_, st), item=found[0])))
        except (RuntimeError, SystemExit) as e:
            print(json.dumps({'error': str(e)}))
        sys.exit()
    it = json.loads(sys.stdin.read())
    ext = {'done': {}, 'failed': {}, 'remap': {}, 'ask': {}}
    try:
        convert(dict(it, premade=True), ffmpeg_, ffprobe_, ext)
        try:
            old = json.load(open(EXTF))
        except (OSError, ValueError):
            old = {}
        for k in ext:
            old.setdefault(k, {}).update(ext[k])
        json.dump(old, open(EXTF, 'w'))
        print(json.dumps({'ok': True}))
    except (RuntimeError, SystemExit) as e:
        print(json.dumps({'error': str(e)}))
    sys.exit()

merge_ext()


def tag_lang(path, code):
    """The film's first soundtrack named as being in this language (code: ISO 639-2), in place: copied, nothing re-encoded, the copy
    checked, then put where the film was; the media server told."""
    tmp = path + '.grifflix-tagging.mp4'
    try:
        r = subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', path, '-map', '0', '-c', 'copy', '-map_metadata', '0',
                            '-metadata:s:a:0', 'language=' + code, '-movflags', '+faststart', tmp], capture_output=True, timeout=3600)
        if r.returncode or not os.path.exists(tmp) or os.path.getsize(tmp) < os.path.getsize(path) * .98:
            raise RuntimeError((r.stderr or b'').decode('utf-8', 'replace')[-200:] or 'the copy came out short')
        os.replace(tmp, path)
        try:
            call('POST', '/Library/Media/Updated', {'Updates': [{'Path': path, 'UpdateType': 'Modified'}]})
        except Exception:
            pass
        log('Language: %s marked as %s, on John’s word' % (os.path.basename(path), NAMES.get(code, code)))
        return True
    except Exception as e:
        log('Language: couldn’t mark %s as %s: %s' % (os.path.basename(path), code, e))
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def redos():
    """Films John said are in another language: made again from the new file (every soundtrack is in it), that language first; quick,
    a repackaging. "ok": it was right, the flag taken down."""
    out, L = [], prio()['langs']
    for k, x in list(st['ask'].items()):
        c = L.get(k)
        if c == 'ok':
            st['ask'].pop(k, None)
        elif c and c.startswith('is:'):   # (John: the soundtrack it has is in this language, its name only missing: the name written on it,
            # the flag down; nothing made again: John, 19 Nov 2026, Donnie Darko "The language is English ... I should be able to say it's English")
            code = next((cd for cd, nm in NAMES.items() if nm == c[3:]), '')
            if code and os.path.exists(x.get('to') or '') and not watching() and tag_lang(x['to'], code):
                st['ask'].pop(k, None)
        elif c and c != x.get('chose') and os.path.exists(x.get('to') or ''):
            out.append({'id': k, 'name': x['name'], 'year': x.get('year'), 'path': x['to'], 'mode': 'remux', 'redo': True, 'urgent': True,
                        'why': 'your language: ' + c, 'mins': 0, 'gb': round(os.path.getsize(x['to']) / 1e9, 1)})
    save_state(st)
    # (English films heard first in a dub, the media server's list (english-first.json): each made again once, English first, its other
    # soundtracks kept: John, 21 Nov 2026, "any english language films that don't have english as first language need fixing")
    try:
        E = json.load(open(os.path.join(HOME, 'english-first.json')))
    except (OSError, ValueError):
        E = {}
    for k, x in E.items():
        p_ = x.get('path') or ''
        if k in st.setdefault('english_done', {}) or k in st['failed'] or not os.path.exists(p_):
            continue
        out.append({'id': k, 'name': x.get('name') or '', 'year': x.get('year'), 'path': p_, 'mode': 'remux', 'english': True,
                    'why': 'English first (it was %s)' % x.get('was', 'a dub'), 'mins': 0, 'gb': round(os.path.getsize(p_) / 1e9, 1)})
    return out


items = [i for i in fresh(gather(), st) if i['id'] not in st['done'] and i['id'] not in st['failed']]
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
    return {'pins': [x for x in (p.get('pins') or []) if isinstance(x, dict) and x.get('id')], 'remux_first': p.get('remux_first', True), 'langs': p.get('langs') or {}}


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
        others = [x for x in others if x.get('mode') == 'remux'] + [x for x in others if x.get('mode') not in ('remux', 'shrink')]  + \
            sorted([x for x in others if x.get('mode') == 'shrink'], key=lambda x: -x.get('gb', 0))   # (making smaller last of all, the biggest first)
    else:
        others = [x for x in others if x.get('mode') != 'shrink'] + sorted([x for x in others if x.get('mode') == 'shrink'], key=lambda x: -x.get('gb', 0))
    if downloading():   # (while a film downloads: conversions go on as before, older films' repackaging after them, the drive left to it)
        old = lambda x: x.get('mode') == 'remux' and not x.get('fetched') and not x.get('new') and not x.get('redo')   # (a film brought in lately, however it came, is never held back: John, 16 Nov 2026)
        others = [x for x in others if not old(x)] + [x for x in others if old(x)]
    return first + others


def chosen(st, have):
    """Films John chose from the largest to make smaller, whatever the converter would have said of them (John, 16 Nov 2026: "I need a
    tab for the largest files, so that I can convert them - the make smaller isn't useful in the same way"): each made smaller next,
    at the careful setting, kept only if it really is smaller."""
    out = []
    for x in prio()['pins']:
        iid = x.get('id')
        if not x.get('force') or iid in have:
            continue
        if max((st['done'].get(iid) or {}).get('at', 0), (st['failed'].get(iid) or {}).get('at', 0)) >= x.get('at', 0):
            continue
        f = shrink_item(iid, x.get('name'))
        if f:
            out.append(f)
    return out


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
    # (and those left in Grifflix Converting by a converting stopped part way (Romeo and Juliet's, 6 GB, from 12 Nov; Rozmarné léto's, 23 Nov),
    # a day old and no film from John's Mac on its way into them: John, 24 Nov 2026, La Bola Negra couldn't be copied over one of them)
    try:
        live = set()
        for j in (json.load(open(os.path.join(HOME, 'convert-jobs.json'))) or {}).values():
            if not j.get('ended') and (j.get('plan') or {}).get('tmp'):
                live.update((j['plan']['tmp'], j['plan']['tmp'] + '.uploading'))
    except (OSError, ValueError, AttributeError):
        live = None   # (not known: nothing cleared)
    gone = []
    for root in set(os.path.join(os.path.dirname(r.rstrip('/')), 'Grifflix Converting') for vf_ in call('GET', '/Library/VirtualFolders') or [] for r in g(vf_, 'Locations', [])):
        if live is None or not os.path.isdir(root):
            continue
        for f in os.listdir(root):
            fp = os.path.join(root, f)
            try:
                if '.grifflix-converting.' in f and fp not in live and time.time() - os.path.getmtime(fp) > 86400 and os.path.isfile(fp):
                    sz = os.path.getsize(fp)
                    os.remove(fp)
                    gone.append('%s (%.1f GB)' % (f, sz / 1e9))
            except OSError:
                pass
    if gone:
        log('half-made files from before cleared from Grifflix Converting: ' + ', '.join(gone))
    if n:
        log('%d half-made file%s from before taken out of the libraries' % (n, '' if n == 1 else 's'))
        try:
            if not any(g(t, 'Key') == 'RefreshLibrary' and g(t, 'State') == 'Running' for t in call('GET', '/ScheduledTasks') or []):   # (not one under way cancelled and begun again: 18 Nov 2026)
                call('POST', '/Library/Refresh')   # (Jellyfin looks again, so they go from Grifflix too)
        except Exception:
            pass
NEWF = os.path.join(HOME, 'convert-new.json')   # (films gdrive.py has just brought in, by their path)
PEEKED = set()


def peek_new():
    """While a film is being made: films just brought in, once the media server has them, pinned to go next and shown at the top of
    what's next on the admin page (John, 15 Nov 2026: "ghosts downloaded but still not showing up for repackaging")."""
    try:
        new = json.load(open(NEWF))
    except (OSError, ValueError):
        return
    want = set(x['path'] for x in new or []) - PEEKED
    if not want:
        return
    try:
        found = [i for i in fresh(gather(), st) if i['path'] in want and i['id'] not in st['done'] and i['id'] not in st['failed']]
    except Exception:
        return
    if not found:
        return
    PEEKED.update(i['path'] for i in found)
    try:
        p = json.load(open(PRIO))
    except (OSError, ValueError):
        p = {}
    ids = set(i['id'] for i in found)
    p['pins'] = [{'id': i['id'], 'name': i['name']} for i in found] + [x for x in (p.get('pins') or []) if x.get('id') not in ids]
    tmp = PRIO + '.tmp'
    json.dump(p, open(tmp, 'w'))
    os.replace(tmp, PRIO)
    nx = RUN.get('next') or []
    RUN['next'] = nx[:1] + [{'id': i['id'], 'name': i['name'], 'why': 'just brought in: next', 'gb': i['gb'], 'mins': i['mins'], 'mode': i.get('mode')} for i in found] + [x for x in nx[1:] if x['id'] not in ids]
    log('  just brought in: %s, next after this one' % ', '.join(i['name'] for i in found))
    return found


def done_new(path):
    try:
        json.dump([x for x in json.load(open(NEWF)) if x.get('path') != path], open(NEWF, 'w'))
    except (OSError, ValueError):
        pass


def brought_in(rest):
    """Films just fetched from TorBox or Google Drive: to the top, next, as soon as the media server has them (John, 15 Nov 2026, "the mkv
    films should immediately be put to the top of the list for repackaging"). Pinned, so the admin page shows them as next too."""
    try:
        new = json.load(open(NEWF))
    except (OSError, ValueError):
        return rest
    if not new:
        return rest
    want = set(x['path'] for x in new)
    try:
        found = [i for i in fresh(gather(), st) if i['path'] in want and i['id'] not in st['done'] and i['id'] not in st['failed']]
    except Exception as e:
        log('  couldn’t look for the films just brought in:', e)
        return rest
    if found:
        try:
            p = json.load(open(PRIO))
        except (OSError, ValueError):
            p = {}
        pins = [x for x in (p.get('pins') or []) if x.get('id') not in set(i['id'] for i in found)]
        p['pins'] = [{'id': i['id'], 'name': i['name']} for i in found] + pins
        tmp = PRIO + '.tmp'
        json.dump(p, open(tmp, 'w'))
        os.replace(tmp, PRIO)
        have = set(x['id'] for x in rest)
        rest = [dict(i, urgent=True, fetched=True) for i in found if i['id'] not in have] + rest
        log('%d just brought in: next (%s)' % (len(found), ', '.join(i['name'] for i in found)))
    got = set(i['path'] for i in found)
    keep = [x for x in new if x['path'] not in got and time.time() - x.get('at', 0) < 86400 and os.path.exists(x['path'])]   # (not yet in the media server: looked for again)
    try:
        json.dump(keep, open(NEWF, 'w'))
    except OSError:
        pass
    return rest


def orphans():
    """ffmpegs left by a converter that was stopped part way (frozen ones too): stopped, so the film isn't made twice at once."""
    try:
        ps = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=10).stdout
        for line in ps.splitlines():
            if 'ffmpeg' in line and '.grifflix-converting.' in line:
                pid = int(line.split()[0])
                for sg in (signal.SIGCONT, signal.SIGKILL):
                    os.kill(pid, sg)
                log('an ffmpeg left from before stopped (%d)' % pid)
    except Exception as e:
        log('couldn’t look for ffmpegs left from before:', e)


orphans()
leftovers()
log('starting: %d to convert' % len(items))
RUN['total'] = len(items)
follow_up(st)
rest, n = redos(), -1
REGATHER = [time.time()]
rest += [i for i in items if i['id'] not in set(x['id'] for x in rest)]   # (a film both made again and planned: once)
looked = time.time()
while rest:
    if time.time() - looked > 1800:   # (every half hour, a look for films brought in since: they go to the top, not to the end of the queue)
        looked = time.time()
        try:
            have = set(x['id'] for x in rest)
            add = [i for i in fresh(gather(), st) if i.get('new') and i['id'] not in have and i['id'] not in st['done'] and i['id'] not in st['failed']]
            if add:
                log('%d just brought in: to the top of the queue' % len(add))
                rest = add + rest
                RUN['total'] = RUN.get('total', 0) + len(add)
        except Exception as e:
            log('  couldn’t look for new films:', e)   # (the order looked at again before each film: a film John pins goes next, without starting anything again)
    have = set(x['id'] for x in rest)
    rest = [x for x in redos() if x['id'] not in have] + rest   # (John's language changes first: minutes each)
    merge_ext()
    rest = [x for x in rest if not ((st['done'].get(x['id']) or {}).get('at', 0) > RUN['started'])]   # (one John's Mac has just made: not made again here)
    rest = brought_in(rest)   # (one being converted on John's Mac stays in the list, passed over while his Mac is at it: below)
    try:   # (pins for films already done, or given up, taken off: they read as next, and the converter seemed to ignore them)
        for x in prio()['pins']:
            if x.get('force') and x.get('at', 0) > max((st['done'].get(x['id']) or {}).get('at', 0), (st['failed'].get(x['id']) or {}).get('at', 0)):
                continue   # (one John chose from the largest, to make smaller: wanted, even if done once before)
            if x['id'] in st['done'] or x['id'] in st['failed']:
                unpin(x['id'])
    except Exception:
        pass
    try:
        have = set(x['id'] for x in rest)
        rest = rest + chosen(st, have)
    except Exception as e:
        log('  couldn’t add the films chosen to make smaller:', e)
    rest = ordered(rest)
    rest = [x for x in rest if x.get('redo')] + [x for x in rest if not x.get('redo')]
    try:   # (a newer version put here by Update everything: taken up now, between films)
        if os.path.getmtime(os.path.abspath(__file__)) != MINE:
            log('a newer version of the converter: starting it')
            sys.stdout.flush()
            os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
    except OSError as e:
        log('couldn’t start the newer version:', e)
    # (everything on the one queue, which John's Mac works through; the Mac mini itself takes a simple repackaging from it only while his Mac
    # is off and nothing is playing, and a language he put right whenever his Mac isn't converting: John, 25 Nov 2026, "Queue everything for
    # my mac and let me choose when and where to run things" ... "the only time the mac mini should then pick up jobs is if my mac is off, and
    # then it should only be simple repackaging jobs while nothing is playing")
    try:
        to_mac([x for x in rest if not job_claimed(x['id'])])
    except Exception as e:
        log('  couldn’t put films on the queue:', e)
    RUN['left'] = {'remux': sum(1 for x in rest if x.get('mode') == 'remux'), 'convert': sum(1 for x in rest if x.get('mode') != 'remux')}
    redo_ = [x for x in rest if x.get('redo') and not job_claimed(x['id'])]
    wait_ = mini_wait(bool(redo_))
    it = None if wait_ else (redo_[0] if redo_ else mini_pick(rest))
    if not it:
        RUN['next'] = [{'id': x['id'], 'name': x['name'], 'why': x['why'], 'gb': x['gb'], 'mins': x['mins'], 'mode': x.get('mode')}
                       for x in [mini_pick(rest)] if x]   # (what the Mac mini would take next, for the admin page)
        now('waiting', why=wait_ or 'nothing on the queue is a simple repackaging it can do: the rest are for your Mac')
        if time.time() - REGATHER[0] > 1800:   # (a whole look again every half hour: films come in, and those repackaged may need converting)
            REGATHER[0] = time.time()
            try:
                have = set(x['id'] for x in rest)
                more = [i for i in fresh(gather(), st) if i['id'] not in have and i['id'] not in st['done'] and i['id'] not in st['failed']]
                if more:
                    log('%d more to do' % len(more))
                    rest = rest + more
                    RUN['total'] = RUN.get('total', 0) + len(more)
            except Exception as e:
                log('  couldn’t look for more to do:', e)
        time.sleep(90)
        continue
    rest.remove(it)
    n += 1
    RUN['n'] = n
    RUN['next'] = [{'id': it['id'], 'name': it['name'], 'why': it['why'], 'gb': it['gb'], 'mins': it['mins'], 'mode': it.get('mode')}]
    if not os.path.exists(it['path']):   # (renamed or deleted since it was listed: looked at again under its new name later)
        log('  %s isn’t where it was (renamed or deleted): skipped for now' % it['name'])
        continue
    if not it.get('redo'):
        queue_take(it['id'])   # (off the queue while the Mac mini makes it: John's Mac doesn't take it too)
    try:
        try:
            convert(it, ffmpeg, ffprobe, st)
        except MacBusy:
            log('  stopped and cleared: your Mac is converting. %s is back at the top of the queue' % it['name'])
            rest.insert(0, it)
            if not it.get('redo'):
                queue_back(it)
            continue
        if it.get('redo'):   # (made again in John's language: the flag down)
            st['ask'].pop(it['id'], None)
            save_state(st)
        if it.get('english'):
            st.setdefault('english_done', {})[it['id']] = int(time.time())
            save_state(st)
            try:   # (in the record of every change: John, 22 Nov 2026, "keep a log of everything")
                with open(os.path.join(HOME, 'record.jsonl'), 'a', encoding='utf-8') as f_:
                    f_.write(json.dumps({'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'what': 'made English first', 'film': it['name'], 'id': it['id'],
                                         'path': it['path'], 'why': it.get('why')}, ensure_ascii=False) + '\n')
            except Exception:
                pass
    except RuntimeError as e:
        if MOVED in str(e):
            log('  %s: %s' % (it['name'], MOVED))
        elif it.get('mode') == 'remux' and 'skipped by John' not in str(e):   # (a picture an MP4 can't hold as it is: converted instead, in its turn, not given up on)
            log('  couldn’t repackage', it['name'], '(%s): it will be converted instead' % str(e)[:160])
            it['mode'] = 'convert'
            it['why'] = 'format (couldn’t be repackaged as it is)'
            rest.append(it)
        else:
            log('  FAILED', it['name'], e)
            st['failed'][it['id']] = {'name': it['name'], 'why': str(e)[:300], 'path': it['path'], 'at': int(time.time())}
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
        more = [i for i in fresh(gather(), st) if i['id'] not in st['done'] and i['id'] not in st['failed'] and i['id'] != it['id']]
        if more:
            log('%d more to do (films repackaged that also need converting, or new ones)' % len(more))
            rest = more
            RUN['total'] = n + 1 + len(more)
RUN['n'] = n + 1
now('finished')
time.sleep(30)
follow_up(st)
log('all done')
