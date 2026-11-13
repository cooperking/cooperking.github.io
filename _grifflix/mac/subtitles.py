"""Subtitles for Grifflix: found on OpenSubtitles, and put in time with the film itself (John, 13 Nov 2026: "one of the main
problems with plex was that they didn't synch, so I'd like something that really sorts this").

Two things make them line up:
  * the search asks first by the film file's own fingerprint (OpenSubtitles' "movie hash"): a subtitle made for that very
    release, which fits as it is; those come first, marked
  * whatever is chosen is then lined up with the film's sound: the Mac listens for where people speak (about 10 times a second
    through the whole film) and moves the subtitles until their lines fall on the speech, allowing for a different frame rate
    (25 against 23.976 and the like, which drifts further out as the film goes on) and for a part that's out by a different
    amount (a version with a different opening, say). If it can't be sure, the timing is left as it came.
A line still a touch out can be moved half a second at a time in the player, for everyone.

Only the standard library (the 2010 Mac mini's Python), and the media server's own ffmpeg to hear the film.
"""
import json, math, os, re, struct, subprocess, time, urllib.error, urllib.parse, urllib.request

API = 'https://api.opensubtitles.com/api/v1'
UA = 'Grifflix v1.0'
FPS_RATIOS = [1.0, 25 / 23.976, 23.976 / 25, 25 / 24, 24 / 25, 24 / 23.976, 23.976 / 24, 30 / 25, 25 / 30]
HZ = 10   # (the film heard 10 times a second)
LANGS = [('en', 'English'), ('fr', 'French'), ('de', 'German'), ('es', 'Spanish'), ('it', 'Italian'), ('nl', 'Dutch'),
         ('sv', 'Swedish'), ('da', 'Danish'), ('no', 'Norwegian'), ('fi', 'Finnish'), ('pl', 'Polish'), ('pt-PT', 'Portuguese'),
         ('pt-BR', 'Portuguese (Brazil)'), ('ru', 'Russian'), ('ja', 'Japanese'), ('ko', 'Korean'), ('zh-CN', 'Chinese'),
         ('ar', 'Arabic'), ('he', 'Hebrew'), ('tr', 'Turkish'), ('el', 'Greek'), ('hu', 'Hungarian'), ('cs', 'Czech'),
         ('ro', 'Romanian'), ('hi', 'Hindi'), ('th', 'Thai'), ('id', 'Indonesian'), ('uk', 'Ukrainian'), ('ga', 'Irish'), ('cy', 'Welsh')]


# ---------------------------------------------------------------- the film's fingerprint

def movie_hash(path):
    """OpenSubtitles' hash: the file's size plus its first and last 64 KB, read as 64-bit numbers, added up."""
    size = os.path.getsize(path)
    if size < 131072:
        return None
    h = size
    with open(path, 'rb') as f:
        for where in (0, size - 65536):
            f.seek(where)
            buf = f.read(65536)
            for (n,) in struct.iter_unpack('<Q', buf):
                h = (h + n) & 0xFFFFFFFFFFFFFFFF
    return '%016x' % h


# ---------------------------------------------------------------- OpenSubtitles

class OpenSubs:
    """The OpenSubtitles service, with John's own key and account (kept on the Mac in the gateway's settings)."""

    def __init__(self, cfg, save, ctx=None):
        self.cfg, self.save, self.ctx = cfg, save, ctx   # (cfg: the dict kept in settings; save(): writes it)

    def _req(self, method, path, body=None, auth=False, base=None):
        url = (base or API) + path
        h = {'Api-Key': self.cfg.get('key', ''), 'User-Agent': UA, 'Accept': 'application/json'}
        if body is not None:
            h['Content-Type'] = 'application/json'
        if auth:
            h['Authorization'] = 'Bearer ' + self.token()
        r = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=40, context=self.ctx) as res:
                return json.loads(res.read() or b'{}')
        except urllib.error.HTTPError as e:
            try:
                j = json.loads(e.read() or b'{}')
            except Exception:
                j = {}
            msg = j.get('message') or (j.get('errors') or [''])[0] or ('HTTP %d' % e.code)
            if e.code == 401 and auth and not getattr(self, '_retried', False):   # (its sign-in ran out: once more, signed in afresh)
                self._retried = True
                self.cfg.pop('token', None)
                try:
                    return self._req(method, path, body, auth, base)
                finally:
                    self._retried = False
            raise RuntimeError('OpenSubtitles: %s' % msg)

    def token(self):
        if self.cfg.get('token') and time.time() - self.cfg.get('token_at', 0) < 20 * 3600:
            return self.cfg['token']
        if not self.cfg.get('user') or not self.cfg.get('pass'):
            raise RuntimeError('OpenSubtitles isn’t set up yet: John adds its details on the admin page.')
        j = self._req('POST', '/login', {'username': self.cfg['user'], 'password': self.cfg['pass']})
        self.cfg.update(token=j.get('token'), token_at=time.time(), base=j.get('base_url') or 'api.opensubtitles.com',
                        allowed=(j.get('user') or {}).get('allowed_downloads'), remaining=(j.get('user') or {}).get('remaining_downloads'))
        self.save()
        return self.cfg['token']

    def search(self, lang, mhash=None, tmdb=None, imdb=None, query=None, year=None, parent_tmdb=None, season=None, episode=None):
        """Subtitles for a film or an episode in one language: those made for this very file first."""
        if not self.cfg.get('key'):
            raise RuntimeError('Subtitles aren’t set up yet: John adds his OpenSubtitles details on the admin page.')
        found, order = {}, []
        def ask(params):
            params = {k: str(v).lower() for k, v in params.items() if v not in (None, '')}
            qs = urllib.parse.urlencode(sorted(params.items()))   # (sorted and lower case, as the service asks, or it redirects)
            j = self._req('GET', '/subtitles?' + qs)
            for d in j.get('data') or []:
                a = d.get('attributes') or {}
                for f in a.get('files') or []:
                    fid = f.get('file_id')
                    if not fid:
                        continue
                    exact = bool(a.get('moviehash_match'))
                    if fid in found:
                        found[fid]['exact'] = found[fid]['exact'] or exact
                        continue
                    fd = a.get('feature_details') or {}
                    found[fid] = {'file_id': fid, 'name': a.get('release') or f.get('file_name') or '', 'lang': a.get('language') or lang,
                                  'exact': exact, 'downloads': a.get('download_count') or 0, 'hi': bool(a.get('hearing_impaired')),
                                  'fps': a.get('fps') or 0, 'trusted': bool(a.get('from_trusted')),
                                  'machine': bool(a.get('machine_translated') or a.get('ai_translated')),
                                  'by': ((a.get('uploader') or {}).get('name') or ''), 'title': fd.get('title') or '', 'year': fd.get('year') or ''}
                    order.append(fid)
        ep = {'parent_tmdb_id': parent_tmdb, 'season_number': season, 'episode_number': episode} if episode else {}
        if mhash:
            try:
                ask(dict({'moviehash': mhash, 'languages': lang}, **ep))
            except RuntimeError:
                pass
        if episode and parent_tmdb:
            ask(dict({'languages': lang}, **ep))
        elif tmdb or imdb:
            ask({'languages': lang, 'tmdb_id': tmdb} if tmdb else {'languages': lang, 'imdb_id': re.sub(r'\D', '', imdb or '')})
        if query and len(found) < 5:
            ask({'languages': lang, 'query': query, 'year': year})
        out = [found[f] for f in order]
        # (the surest first: made for this file, then trusted uploaders, then the most downloaded; machine translations last)
        out.sort(key=lambda x: (not x['exact'], x['machine'], not x['trusted'], -x['downloads']))
        return out[:40]

    def download(self, file_id):
        """The subtitle file's bytes (one of John's daily OpenSubtitles downloads)."""
        base = 'https://%s/api/v1' % (self.cfg.get('base') or 'api.opensubtitles.com')
        j = self._req('POST', '/download', {'file_id': int(file_id), 'sub_format': 'srt'}, auth=True, base=base)
        if j.get('remaining') is not None:
            self.cfg['remaining'] = j.get('remaining')
            self.cfg['reset'] = j.get('reset_time_utc') or j.get('reset_time')
            self.save()
        link = j.get('link')
        if not link:
            raise RuntimeError('OpenSubtitles gave no file (%s)' % (j.get('message') or 'no more downloads today?'))
        with urllib.request.urlopen(urllib.request.Request(link, headers={'User-Agent': UA}), timeout=60, context=self.ctx) as r:
            return r.read()


# ---------------------------------------------------------------- SRT

TS = re.compile(r'(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})')


def decode(raw):
    for enc in ('utf-8-sig', 'utf-16'):
        try:
            t = raw.decode(enc)
            if enc == 'utf-16' and not raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
                raise UnicodeError
            return t
        except UnicodeError:
            continue
    return raw.decode('cp1252', 'replace')


def parse_srt(text):
    """[(start, end, text)] in seconds."""
    cues = []
    for block in re.split(r'\n\s*\n', text.replace('\r\n', '\n').replace('\r', '\n')):
        lines = block.strip('\n').split('\n')
        for i, l in enumerate(lines[:3]):
            m = TS.search(l)
            if m:
                a = [int(x) for x in m.groups()]
                s = a[0] * 3600 + a[1] * 60 + a[2] + int(str(a[3]).ljust(3, '0')[:3]) / 1000
                e = a[4] * 3600 + a[5] * 60 + a[6] + int(str(a[7]).ljust(3, '0')[:3]) / 1000
                body = '\n'.join(lines[i + 1:]).strip()
                if body and e > s:
                    cues.append([s, e, body])
                break
    cues.sort(key=lambda c: c[0])
    return cues


def stamp(t):
    t = max(0.0, t)
    ms = int(round(t * 1000))
    return '%02d:%02d:%02d,%03d' % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def write_srt(cues):
    return '\n'.join('%d\n%s --> %s\n%s\n' % (i + 1, stamp(s), stamp(e), t) for i, (s, e, t) in enumerate(cues))


def shift_file(path, secs):
    """Every line earlier (secs < 0) or later, in the file itself: for everyone."""
    with open(path, 'rb') as f:
        cues = parse_srt(decode(f.read()))
    cues = [[max(0, s + secs), max(0.05, e + secs), t] for s, e, t in cues]
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(write_srt(cues))
    os.replace(tmp, path)
    return len(cues)


# ---------------------------------------------------------------- hearing where people speak

def speech(ffmpeg, video, stream=0, nice=True):
    """The film's sound as 10 loudness readings a second, in the voice range (dB), from the media server's ffmpeg."""
    af = ('highpass=f=200,lowpass=f=3400,aresample=8000,asetnsamples=n=%d:p=0,astats=metadata=1:reset=1,'
          'ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-' % (8000 // HZ))
    cmd = [ffmpeg, '-hide_banner', '-nostats', '-v', 'error', '-i', video, '-map', '0:a:%d' % stream, '-vn', '-sn', '-dn', '-af', af, '-f', 'null', '-']
    if nice and os.path.exists('/usr/sbin/taskpolicy'):
        cmd = ['/usr/sbin/taskpolicy', '-b'] + cmd
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, preexec_fn=(lambda: os.nice(10)) if nice else None)
    lv, t = [], None
    for line in p.stdout:
        if line.startswith('frame:'):
            m = re.search(r'pts_time:([\d.]+)', line)
            t = float(m.group(1)) if m else None
        elif 'RMS_level=' in line and t is not None:
            v = line.split('=', 1)[1].strip()
            i = int(round(t * HZ))
            while len(lv) < i:
                lv.append(lv[-1] if lv else -90.0)
            try:
                lv.append(max(-90.0, float(v)) if v not in ('-inf', 'inf', 'nan') else -90.0)
            except ValueError:
                lv.append(-90.0)
    p.wait()
    if len(lv) < HZ * 60:
        raise RuntimeError('couldn’t hear the film (%s)' % (p.stderr.read().strip()[-200:] or 'no sound'))
    # speech: louder than the film's quiet parts by a margin; ±1 so silence under a subtitle counts against it
    s = sorted(lv)
    floor, high = s[len(s) // 5], s[len(s) * 9 // 10]
    cut = floor + max(4.0, (high - floor) * 0.35)
    return [1.0 if v > cut else 0.0 for v in lv]


# ---------------------------------------------------------------- lining them up

def _prefix(sig):
    m = sum(sig) / len(sig)
    p = [0.0]
    for v in sig:
        p.append(p[-1] + v - m)
    return p


def _score(P, cues, scale, off):
    n, tot = len(P) - 1, 0.0
    for s, e, _ in cues:
        a = int((s * scale + off) * HZ)
        b = int((e * scale + off) * HZ)
        a = 0 if a < 0 else n if a > n else a
        b = 0 if b < 0 else n if b > n else b
        tot += P[b] - P[a]
    return tot


def align(cues, sig, max_off=240.0):
    """The best fit of the subtitles to the speech: a frame-rate ratio, a shift, and a shift of its own for any part that fits
    better so. Returns (new cues, report)."""
    if len(cues) < 20:
        return cues, {'ok': False, 'why': 'too few lines to time'}
    P = _prefix(sig)
    span = (len(sig) / HZ)
    # coarse: every ratio, a shift each second
    rows = []
    for r in FPS_RATIOS:
        if cues[-1][1] * r > span + max_off:
            continue
        for k in range(int(-max_off), int(max_off) + 1):
            rows.append((_score(P, cues, r, float(k)), r, float(k)))
    if not rows:
        return cues, {'ok': False, 'why': 'the subtitles are longer than the film'}
    scores = [x[0] for x in rows]
    mean = sum(scores) / len(scores)
    sd = math.sqrt(sum((x - mean) ** 2 for x in scores) / len(scores)) or 1.0
    rows.sort(reverse=True)
    # fine: the best few, to a tenth of a second
    best = (rows[0][0], rows[0][1], rows[0][2])
    for sc, r, k in rows[:4]:
        for d in range(-12, 13):
            o = k + d / 10.0
            v = _score(P, cues, r, o)
            if v > best[0]:
                best = (v, r, o)
    sc, r, off = best
    z = (sc - mean) / sd
    orig = _score(P, cues, 1.0, 0.0)
    report = {'ratio': round(r, 5), 'shift': round(off, 2), 'sure': round(z, 1)}
    if z < 4.5:   # (not clearly better than chance: left as it came)
        report.update(ok=False, why='couldn’t be sure of the timing, so left as it came')
        return cues, report
    out = [[s * r + off, e * r + off, t] for s, e, t in cues]
    # one big jump part-way (a version with a longer or shorter opening, or a scene more or less): the lines before and after a
    # point each given their own shift, where that fits clearly better than one shift for all
    whole = _score(P, out, 1.0, 0.0)
    def fit(ch):
        bo, bv = 0.0, _score(P, ch, 1.0, 0.0)
        for k in range(int(-max_off), int(max_off) + 1):
            v = _score(P, ch, 1.0, float(k))
            if v > bv:
                bo, bv = float(k), v
        for d in range(-12, 13):
            v = _score(P, ch, 1.0, bo + d / 10.0)
            if v > bv:
                bo, bv = bo + d / 10.0, v
        return bo, bv
    jump = None
    step = max(10, len(out) // 24)
    for i in range(step, len(out) - step + 1, step):
        lo, lv = fit(out[:i])
        ro, rv = fit(out[i:])
        if abs(lo - ro) >= 1.0 and lv + rv > whole + max(15.0, abs(whole) * 0.15) and (jump is None or lv + rv > jump[0]):
            jump = (lv + rv, i, lo, ro)
    if jump:
        _, i, lo, ro = jump
        # (the exact line where it changes: around the coarse point, the split that fits best)
        bi, bv = i, -1e18
        for j in range(max(1, i - step), min(len(out) - 1, i + step) + 1):
            v = _score(P, out[:j], 1.0, lo) + _score(P, out[j:], 1.0, ro)
            if v > bv:
                bi, bv = j, v
        for c in out[:bi]:
            c[0] += lo; c[1] += lo
        for c in out[bi:]:
            c[0] += ro; c[1] += ro
        report['jump'] = {'at': round(out[bi][0], 1), 'first': round(off + lo, 1), 'then': round(off + ro, 1)}
    # a part out by its own amount (a different opening, a scene cut or added): each stretch of about a minute and a half tried
    # within 6 seconds either side, moved only where that's clearly better
    parts, i, moved = [], 0, 0
    while i < len(out):
        j = i
        while j < len(out) and out[j][0] - out[i][0] < 90:
            j += 1
        parts.append((i, j))
        i = j
    deltas = []
    for a, b in parts:
        chunk = out[a:b]
        bd = 0.0
        if len(chunk) >= 8:
            base = _score(P, chunk, 1.0, 0.0)
            bv = base
            for d in range(-60, 61):
                v = _score(P, chunk, 1.0, d / 10.0)
                if v > bv:
                    bd, bv = d / 10.0, v
            if abs(bd) < 0.3 or bv - base < max(2.0, abs(base) * 0.2):
                bd = 0.0
        deltas.append(bd)
    for k, (a, b) in enumerate(parts):
        # (a lone stretch unlike both its neighbours, which agree with each other, is more likely chance than a cut)
        if 0 < k < len(parts) - 1 and deltas[k] and deltas[k - 1] == deltas[k + 1] == 0.0 and (b - a) < 15:
            continue
        if deltas[k]:
            for c in out[a:b]:
                c[0] += deltas[k]; c[1] += deltas[k]
            moved += 1
    report.update(ok=True, parts=moved, before=round(orig, 1), after=round(_score(P, out, 1.0, 0.0), 1))
    # (no line overlapping the next after the moves)
    for k in range(len(out) - 1):
        if out[k][1] > out[k + 1][0]:
            out[k][1] = max(out[k][0] + 0.3, out[k + 1][0] - 0.02)
    return [[max(0.0, s), max(0.05, e), t] for s, e, t in out], report


def describe(rep):
    """What was done to the timing, in a few words for the page."""
    if not rep.get('ok'):
        return rep.get('why') or 'timing left as it came'
    bits = []
    r = rep.get('ratio', 1.0)
    if abs(r - 1) > 1e-4:
        bits.append('a different frame rate allowed for')
    mv = lambda x: '%.1f s %s' % (abs(x), 'later' if x > 0 else 'earlier') if abs(x) >= 0.15 else 'left in place'
    if rep.get('jump'):
        j = rep['jump']
        m = int(j['at'] // 60)
        bits.append('the start %s, and from %s on %s' % (mv(j['first']), '%d:%02d' % (m // 60, m % 60) if m >= 60 else '%d min' % m, mv(j['then'])))
    elif abs(rep.get('shift', 0)) >= 0.15:
        bits.append('moved ' + mv(rep['shift']))
    if rep.get('parts'):
        bits.append('%d part%s set right on its own' % (rep['parts'], '' if rep['parts'] == 1 else 's'))
    return ('Timed to the film: ' + ', '.join(bits)) if bits else 'Timed to the film: already in time'
