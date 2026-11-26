"""Grifflix: picture subtitles read into text (John, 26 Nov 2026: "Build the picture job, but I'll decide when and where to run it").

The English subtitles of a Blu-ray (PGS) or a DVD (VobSub) are pictures of words, each with its exact times: saved beside a film as it's
converted (a .sup or .mks), or still inside a film not yet converted. As pictures they show on few devices; read into text, they play
everywhere, and they are the disc's own translation, exactly timed, better than any Whisper makes. Here each picture is read with macOS's
own text recognition (Vision: Live Text's), a subtitle (.srt) made from them, and sent to the Mac mini, which puts it beside the film.

Runs on an Apple Silicon Mac: John's Mac (its helper starts it while John has it switched on, "On your Mac" on the admin page), or the
Mac mini itself once it's the new one. The Mac mini hands out one subtitle at a time. Needs ffmpeg (as the converter does) and, the first
time, Apple's Vision for Python, put by this in its own folder (~/Grifflix/ocr): no Homebrew, nothing else.

  python3 picsubs.py                 one job after another from the Mac mini, till none is left or it's switched off
  python3 picsubs.py FILE [INDEX] [OUT]   one subtitle file (or a film's track INDEX) read into OUT (.srt), for a look
"""
import base64, json, os, re, shutil, struct, subprocess, sys, tempfile, time, urllib.request, zlib

HOME = os.path.expanduser('~/Grifflix')
VENV = os.path.join(HOME, 'ocr')
NEXT = os.path.join(HOME, 'picsubs.next')   # (when to ask again: the helper doesn't start this before then)
UA = 'Grifflix-picsubs/1.0'   # (its own name: Cloudflare turns away Python's own)


def say(m):
    print(time.strftime('%Y-%m-%d %H:%M ') + 'picture subtitles: ' + m, flush=True)


# ---------------------------------------------------------------- a picture

class Img:
    """A greyscale picture, 0 black to 255 white."""
    def __init__(self, w, h, fill=255):
        self.w, self.h, self.px = w, h, bytearray([fill]) * (w * h)

    def png(self, scale=1, pad=24):
        """As a PNG (the words dark on white, enlarged, a margin round them: what text recognition reads best)."""
        W, H = self.w * scale + 2 * pad, self.h * scale + 2 * pad
        rows = []
        blank = b'\x00' + b'\xff' * W
        for _ in range(pad):
            rows.append(blank)
        for y in range(self.h):
            src = self.px[y * self.w:(y + 1) * self.w]
            line = bytes(v for v in src for _ in range(scale)) if scale > 1 else bytes(src)
            row = b'\x00' + b'\xff' * pad + line + b'\xff' * pad
            for _ in range(scale):
                rows.append(row)
        for _ in range(pad):
            rows.append(blank)
        raw = zlib.compress(b''.join(rows), 6)
        chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
        return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 0, 0, 0, 0)) + chunk(b'IDAT', raw) + chunk(b'IEND', b'')


def shade(y, a):
    """A pixel of a subtitle (its brightness y, how solid a): the words bright with a dark edge, on nothing, made dark on white."""
    return 255 - (y * a) // 255


# ---------------------------------------------------------------- Blu-ray (PGS, a .sup)

def pgs_rle(d, w, h):
    rows, row, i, n = [], [], 0, len(d)
    while i < n and len(rows) < h:
        b = d[i]; i += 1
        if b:
            row.append(b); continue
        if i >= n:
            break
        b2 = d[i]; i += 1
        if b2 == 0:
            rows.append(row[:w] + [0] * max(0, w - len(row))); row = []; continue
        f = b2 & 0xC0
        if f == 0x00:
            k, c = b2 & 0x3F, 0
        elif f == 0x40:
            k, c = ((b2 & 0x3F) << 8) | d[i], 0; i += 1
        elif f == 0x80:
            k, c = b2 & 0x3F, d[i]; i += 1
        else:
            k, c = ((b2 & 0x3F) << 8) | d[i], d[i + 1]; i += 2
        row.extend([c] * k)
    while len(rows) < h:
        rows.append([0] * w)
    return rows


def read_pgs(data):
    """[(start, end, Img)], from a .sup: each composition's pictures, shown from its time until the next one (or the one that clears it)."""
    out, pal, objs, i, n = [], {}, {}, 0, len(data)
    cur = None   # (start, pictures) of the one showing
    pcs = None
    while i + 13 <= n:
        if data[i:i + 2] != b'PG':
            i += 1; continue
        pts = struct.unpack('>I', data[i + 2:i + 6])[0] / 90000.0
        t, size = data[i + 10], struct.unpack('>H', data[i + 11:i + 13])[0]
        seg = data[i + 13:i + 13 + size]
        i += 13 + size
        if t == 0x14:   # palette
            for k in range(2, len(seg) - 4, 5):
                pal[seg[k]] = (seg[k + 1], seg[k + 4])   # (its brightness, how solid)
        elif t == 0x15 and len(seg) >= 4:   # a picture (perhaps in pieces)
            oid, flag = struct.unpack('>H', seg[0:2])[0], seg[3]
            if flag & 0x80:
                w, h = struct.unpack('>HH', seg[7:11])
                objs[oid] = {'w': w, 'h': h, 'd': bytearray(seg[11:])}
            elif oid in objs:
                objs[oid]['d'] += seg[4:]
        elif t == 0x16 and len(seg) >= 11:   # what's shown, and where
            nobj, comp = seg[10], []
            k = 11
            for _ in range(nobj):
                if k + 8 > len(seg):
                    break
                oid, x, y = struct.unpack('>H', seg[k:k + 2])[0], *struct.unpack('>HH', seg[k + 4:k + 8])
                cropped = seg[k + 3] & 0x80
                comp.append((oid, x, y))
                k += 16 if cropped else 8
            pcs = (pts, comp)
        elif t == 0x80 and pcs is not None:   # the end of a set: shown now (or cleared)
            pts_, comp = pcs
            pcs = None
            if cur and (comp or True):
                if pts_ > cur[0]:
                    out.append((cur[0], pts_, cur[1]))
                cur = None
            if comp:
                pics = []
                for oid, x, y in comp:
                    o = objs.get(oid)
                    if not o or not o['w'] or not o['h']:
                        continue
                    rows = pgs_rle(bytes(o['d']), o['w'], o['h'])
                    im = Img(o['w'], o['h'])
                    for yy, r in enumerate(rows):
                        base = yy * o['w']
                        for xx, c in enumerate(r[:o['w']]):
                            yv, a = pal.get(c, (0, 0))
                            im.px[base + xx] = shade(yv, a)
                    pics.append((x, y, im))
                if pics:
                    cur = (pts_, pics)
    if cur:
        out.append((cur[0], cur[0] + 5, cur[1]))
    return [(s, e, compose(p)) for s, e, p in out]


def compose(pics):
    """The pictures of one moment on one page, where each goes (two lines are often two pictures), cropped to what's there."""
    x0, y0 = min(p[0] for p in pics), min(p[1] for p in pics)
    x1, y1 = max(p[0] + p[2].w for p in pics), max(p[1] + p[2].h for p in pics)
    out = Img(x1 - x0, y1 - y0)
    for x, y, im in pics:
        for yy in range(im.h):
            o = (y - y0 + yy) * out.w + (x - x0)
            out.px[o:o + im.w] = im.px[yy * im.w:(yy + 1) * im.w]
    return trim(out)


def trim(im):
    """Cropped to where there's anything darker than white, a little margin kept."""
    rows = [y for y in range(im.h) if min(im.px[y * im.w:(y + 1) * im.w]) < 200]
    if not rows:
        return im
    cols = [x for x in range(im.w) if any(im.px[y * im.w + x] < 200 for y in range(rows[0], rows[-1] + 1, max(1, (rows[-1] - rows[0]) // 40 or 1)))]
    if not cols:
        return im
    ya, yb, xa, xb = max(0, rows[0] - 2), min(im.h, rows[-1] + 3), max(0, cols[0] - 2), min(im.w, cols[-1] + 3)
    out = Img(xb - xa, yb - ya)
    for y in range(ya, yb):
        out.px[(y - ya) * out.w:(y - ya + 1) * out.w] = im.px[y * im.w + xa:y * im.w + xb]
    return out


# ---------------------------------------------------------------- DVD (VobSub, in a Matroska file: the .mks the converter saves)

def vint(d, i, keep=False):
    b = d[i]
    ln = 1
    while ln <= 8 and not b & (0x80 >> (ln - 1)):
        ln += 1
    v = b if keep else b & (0xFF >> ln)
    for k in range(1, ln):
        v = (v << 8) | d[i + k]
    unknown = not keep and v == (1 << (7 * ln)) - 1
    return v, i + ln, unknown


def mkv_blocks(d):
    """A Matroska file's subtitle blocks: (seconds, duration or None, data), and its first track's private data (a DVD track's palette)."""
    scale, priv, out = 1000000, b'', []
    MASTER = {0x18538067, 0x1654AE6B, 0xAE, 0x1F43B675, 0xA0, 0x1549A966}

    def walk(i, end, cluster_tc):
        nonlocal scale, priv
        bg = None
        while i < end:
            try:
                eid, j, _ = vint(d, i, True)
                size, k, unk = vint(d, j)
            except IndexError:
                return
            stop = end if unk else min(end, k + size)
            if eid == 0x1F43B675:   # a cluster: its own time, then its blocks
                walk(k, stop, [0])
            elif eid in MASTER:
                if eid == 0xA0:
                    bg = {'dur': None}
                    walk_group(k, stop, cluster_tc, bg)
                else:
                    walk(k, stop, cluster_tc)
            elif eid == 0x2AD7B1:
                scale = int.from_bytes(d[k:stop], 'big') or scale
            elif eid == 0x63A2 and not priv:
                priv = bytes(d[k:stop])
            elif eid == 0xE7:
                cluster_tc[0] = int.from_bytes(d[k:stop], 'big')
            elif eid == 0xA3:   # SimpleBlock
                add(d[k:stop], cluster_tc[0], None)
            i = stop

    def walk_group(i, end, cluster_tc, bg):
        blk = None
        while i < end:
            eid, j, _ = vint(d, i, True)
            size, k, _ = vint(d, j)
            stop = min(end, k + size)
            if eid == 0xA1:
                blk = d[k:stop]
            elif eid == 0x9B:
                bg['dur'] = int.from_bytes(d[k:stop], 'big')
            i = stop
        if blk is not None:
            add(blk, cluster_tc[0], bg['dur'])

    def add(blk, ctc, dur):
        _, j, _ = vint(blk, 0)
        rel = struct.unpack('>h', blk[j:j + 2])[0]
        out.append(((ctc + rel) * scale / 1e9, dur * scale / 1e9 if dur else None, bytes(blk[j + 3:])))

    walk(0, len(d), [0])
    return out, priv


def dvd_palette(priv):
    """The 16 colours of a DVD track (its private data, as a .idx has them: "palette: 0d00ee, ..."), each as a brightness."""
    m = re.search(r'palette:\s*([0-9a-fA-F, ]+)', priv.decode('latin-1', 'replace'))
    out = []
    for c in (m.group(1).split(',') if m else []):
        c = c.strip()
        if re.match(r'^[0-9a-fA-F]{6}$', c):
            r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
            out.append((r * 299 + g * 587 + b * 114) // 1000)
    return (out + [0] * 16)[:16] if out else [0, 255, 0, 128] * 4


def read_dvd(mks):
    """[(start, end, Img)] from a DVD track in a Matroska file: each subtitle packet (a picture in two interlaced halves, four of its
    colours, how solid each is) and its own times."""
    with open(mks, 'rb') as f:
        d = f.read()
    blocks, priv = mkv_blocks(d)
    pal = dvd_palette(priv)
    out = []
    for t, dur, p in blocks:
        try:
            ev = spu(p, pal)
        except Exception:
            ev = None
        if not ev:
            continue
        start, stop, im = ev
        end = t + (stop if stop else (dur or 4.0))
        out.append((t + start, max(end, t + start + .3), im))
    return out


def spu(p, pal):
    if len(p) < 4:
        return None
    size, cofs = struct.unpack('>HH', p[0:4])
    colors, alphas, box, offs, start, stop = [0, 1, 2, 3], [0, 15, 15, 15], None, None, 0.0, None
    i, last = cofs, -1
    while i + 4 <= len(p) and i != last:
        last = i
        date, nxt = struct.unpack('>HH', p[i:i + 4])
        k = i + 4
        when = date * 1024 / 90000.0
        while k < len(p):
            c = p[k]; k += 1
            if c == 0xFF:
                break
            if c in (0x00, 0x01):
                start = when
            elif c == 0x02:
                stop = when
            elif c == 0x03:
                v = struct.unpack('>H', p[k:k + 2])[0]; k += 2
                colors = [(v >> 12) & 15, (v >> 8) & 15, (v >> 4) & 15, v & 15][::-1]
            elif c == 0x04:
                v = struct.unpack('>H', p[k:k + 2])[0]; k += 2
                alphas = [(v >> 12) & 15, (v >> 8) & 15, (v >> 4) & 15, v & 15][::-1]
            elif c == 0x05:
                b = p[k:k + 6]; k += 6
                box = ((b[0] << 4) | (b[1] >> 4), ((b[1] & 15) << 8) | b[2], (b[3] << 4) | (b[4] >> 4), ((b[4] & 15) << 8) | b[5])
            elif c == 0x06:
                offs = struct.unpack('>HH', p[k:k + 4]); k += 4
            else:
                break
        i = nxt
    if not box or not offs:
        return None
    x1, x2, y1, y2 = box
    w, h = x2 - x1 + 1, y2 - y1 + 1
    if w <= 0 or h <= 0 or w > 2000 or h > 1200:
        return None
    im = Img(w, h)
    for field in (0, 1):
        pos = [offs[field] * 2]   # (in half-bytes)
        nib = lambda: (p[pos[0] // 2] >> (4 if pos[0] % 2 == 0 else 0)) & 15 if pos[0] // 2 < len(p) else 0
        y = field
        while y < h:
            x = 0
            while x < w:
                v = nib(); pos[0] += 1
                if v < 4:
                    v = (v << 4) | nib(); pos[0] += 1
                    if v < 0x10:
                        v = (v << 4) | nib(); pos[0] += 1
                        if v < 0x40:
                            v = (v << 4) | nib(); pos[0] += 1
                run, c = v >> 2, v & 3
                if run == 0:
                    run = w - x
                shade_ = shade(pal[colors[c]], alphas[c] * 17)
                for xx in range(x, min(w, x + run)):
                    im.px[y * w + xx] = shade_
                x += run
            if pos[0] % 2:
                pos[0] += 1
            y += 2
    return start, stop, trim(im)


# ---------------------------------------------------------------- reading the words (Vision)

VISION = {}


def vision_ready():
    """Apple's Vision for Python, in its own folder the first time (nothing else touched): False where it can't be had (not a Mac)."""
    try:
        import Vision, Foundation   # noqa: F401
        VISION.update(V=Vision, F=Foundation)
        return True
    except ImportError:
        pass
    if sys.platform != 'darwin':
        return False
    py = os.path.join(VENV, 'bin', 'python3')
    if os.path.realpath(sys.executable) != os.path.realpath(py):
        if not os.path.exists(py):
            say('getting Apple’s text recognition for Python, once (a minute or two)')
            subprocess.run(['/usr/bin/python3', '-m', 'venv', VENV], check=True)
            subprocess.run([py, '-m', 'pip', 'install', '-q', '--upgrade', 'pip'], check=False)
            subprocess.run([py, '-m', 'pip', 'install', '-q', 'pyobjc-framework-Vision', 'pyobjc-framework-Quartz'], check=True)
        os.execv(py, [py, os.path.abspath(__file__)] + sys.argv[1:])
    return False


def ocr(png):
    """The words in one picture, top to bottom, as Live Text reads them (English, accurate, its own spelling checks)."""
    V, F = VISION['V'], VISION['F']
    data = F.NSData.dataWithBytes_length_(png, len(png))
    h = V.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    r = V.VNRecognizeTextRequest.alloc().init()
    r.setRecognitionLevel_(V.VNRequestTextRecognitionLevelAccurate)
    r.setUsesLanguageCorrection_(True)
    try:
        r.setRecognitionLanguages_(['en-GB', 'en-US'])
    except Exception:
        pass
    ok = h.performRequests_error_([r], None)
    lines = []
    for o in (r.results() or []):
        c = o.topCandidates_(1)
        if c and len(c):
            b = o.boundingBox()
            lines.append((-round(b.origin.y, 2), b.origin.x, str(c[0].string())))
    lines.sort()
    return '\n'.join(t for _, _, t in lines)


def tidy(t):
    """The usual slips of reading subtitles from pictures put right: a bar for I, spaces before punctuation, a lone l for I."""
    t = t.replace('|', 'I').replace('’', "'").replace('‘', "'")
    t = re.sub(r'\s+([,.!?;:])', r'\1', t)
    t = re.sub(r'(^|\s)l(\s|\'|$)', r'\1I\2', t)
    t = re.sub(r'(^|\n)\s*[-–]\s*', r'\1- ', t)
    return '\n'.join(l.strip() for l in t.splitlines() if l.strip())


def stamp(s):
    s = max(0.0, s)
    h, m, sec = int(s // 3600), int(s % 3600 // 60), s % 60
    return '%02d:%02d:%02d,%03d' % (h, m, int(sec), int(round((sec - int(sec)) * 1000)) % 1000)


def to_srt(lines):
    return ''.join('%d\n%s --> %s\n%s\n\n' % (n + 1, stamp(a), stamp(b), t) for n, (a, b, t) in enumerate(lines))


# ---------------------------------------------------------------- one subtitle

def tool(name):
    for p in (os.path.expanduser('~/bin/' + name), '/opt/homebrew/bin/' + name, '/usr/local/bin/' + name,
              '/Applications/Jellyfin.app/Contents/MacOS/' + name, shutil.which(name) or ''):
        if p and os.access(p, os.X_OK):
            return p
    return None


def events_of(src, index=None, kind=None):
    """The subtitle's pictures and times, whatever it's in: a .sup read as it is; a Blu-ray track elsewhere taken out as a .sup, a DVD
    one as a Matroska file (ffmpeg copies it, the film read once, nothing converted)."""
    ff = tool('ffmpeg')
    work = tempfile.mkdtemp(prefix='grifflix-picsubs-')
    try:
        if src.lower().endswith('.sup') and index is None:
            with open(src, 'rb') as f:
                return read_pgs(f.read())
        if not ff:
            raise RuntimeError('no ffmpeg on this Mac')
        mp = ['-map', '0:%d' % index] if index is not None else ['-map', '0:s:0']
        if kind != 'dvd':   # (a Blu-ray one tried first, unless it's known to be a DVD one)
            out = os.path.join(work, 's.sup')
            r = subprocess.run([ff, '-v', 'error', '-nostdin', '-y', '-i', src] + mp + ['-c:s', 'copy', '-f', 'sup', out], capture_output=True, timeout=3600)
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 100:
                with open(out, 'rb') as f:
                    return read_pgs(f.read())
        out = os.path.join(work, 's.mks')
        r = subprocess.run([ff, '-v', 'error', '-nostdin', '-y', '-i', src] + mp + ['-c:s', 'copy', '-f', 'matroska', out], capture_output=True, timeout=3600)
        if r.returncode != 0 or not os.path.exists(out):
            raise RuntimeError('its subtitle couldn’t be taken out (%s)' % (r.stderr or b'').decode('utf-8', 'replace').strip()[-160:])
        return read_dvd(out)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def read_one(src, index=None, kind=None, progress=None):
    """A subtitle read into text: [(start, end, text)], with how many pictures and how many gave no words."""
    evs = events_of(src, index, kind)
    lines, blank = [], 0
    for n, (a, b, im) in enumerate(evs):
        if progress and n % 50 == 0:
            progress(n, len(evs))
        sc = 3 if im.h < 60 else 2 if im.h < 140 else 1   # (a DVD's small picture enlarged: read far better)
        t = tidy(ocr(im.png(scale=sc)))
        if not t:
            blank += 1
            continue
        if lines and lines[-1][2] == t and a - lines[-1][1] < .25:   # (one line shown in two pictures, back to back: one)
            lines[-1] = (lines[-1][0], b, t)
        else:
            lines.append((a, b, t))
    return lines, len(evs), blank


# ---------------------------------------------------------------- the Mac mini's jobs

def conf():
    c = {}
    try:
        for l in open(os.path.join(HOME, 'agent.conf')):
            if '=' in l:
                k, v = l.split('=', 1)
                c[k.strip()] = v.strip()
    except OSError:
        pass
    return c.get('base', ''), c.get('key', '')


def later(secs):
    try:
        with open(NEXT, 'w') as f:
            f.write(str(int(time.time() + max(60, secs))))
    except OSError:
        pass


def call(base, key, path, body=None):
    h = {'User-Agent': UA, 'Authorization': 'Bearer ' + key}
    if body is not None:
        h['Content-Type'] = 'application/json'
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=180).read() or b'{}')


def jobs():
    base, key = conf()
    if not base or not key:
        return later(3600)
    if not vision_ready():
        say('Apple’s text recognition isn’t here (an Apple Silicon Mac is needed)')
        return later(6 * 3600)
    stop_at = time.time() + 3 * 3600
    done = 0
    while time.time() < stop_at:
        try:
            j = call(base, key, '/macagent/picnext?home=1')
        except Exception as e:
            say('the Mac mini didn’t answer (%s)' % str(e)[:120])
            return later(600)
        job = j.get('job')
        if not job:
            if done:
                say('%d read; %s' % (done, j.get('why') or 'none left'))
            return later(int(j.get('wait') or 3600))
        src = job.get('src') or ''
        body = {'key': job.get('key')}
        if not os.path.isfile(src):
            body['error'] = 'not found on this Mac'
        else:
            t0 = time.time()
            try:
                prog = lambda n, of: call(base, key, '/macagent/picprog', {'key': job.get('key'), 'step': 'reading picture %d of %d' % (n, of)})
                lines, n, blank = read_one(src, job.get('index'), job.get('kind'), prog)
                srt = to_srt(lines)
                body.update(srt64=base64.b64encode(srt.encode('utf-8')).decode(), pictures=n, blank=blank, lines=len(lines), secs=round(time.time() - t0))
                if not lines:
                    body['error'] = 'no words read from its %d pictures' % n
            except Exception as e:
                body['error'] = str(e)[:300]
        try:
            r = call(base, key, '/macagent/picdone', body)
        except Exception as e:
            say('couldn’t send %s back (%s)' % (job.get('name'), str(e)[:120]))
            return later(600)
        done += 1
        if r.get('stop'):
            say('%d read; switched off' % done)
            return later(3600)
    say('%d read; carrying on next time' % done)
    later(60)


if __name__ == '__main__':
    if len(sys.argv) > 1:   # (one, for a look: FILE [INDEX] [OUT])
        idx = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
        out = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(sys.argv[1])[0] + '.en.srt'
        if not vision_ready():
            sys.exit('Apple’s text recognition isn’t here: an Apple Silicon Mac is needed')
        lines, n, blank = read_one(sys.argv[1], idx)
        open(out, 'w', encoding='utf-8').write(to_srt(lines))
        print('%d pictures, %d lines, %d with no words: %s' % (n, len(lines), blank, out))
    else:
        try:
            os.nice(10)
        except OSError:
            pass
        jobs()
