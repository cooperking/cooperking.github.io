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

    def search(self, lang, mhash=None, tmdb=None, imdb=None, query=None, year=None, parent_tmdb=None, season=None, episode=None, hint=''):
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
                                  'kind': (fd.get('feature_type') or '').lower(), 'tmdb': fd.get('tmdb_id') or fd.get('parent_tmdb_id') or '',
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
        # (only this film's: a title search also finds others with the word in them, a TV episode among them (John, 13 Nov
        # 2026: "Medium S01E15" for Fantasia). Kept: made for this file; the same film by its TMDB number; or, with no number,
        # the same kind (film or episode) and a title that matches)
        flat = lambda t: re.sub(r'[^a-z0-9]', '', (t or '').lower())
        want_kind = 'episode' if episode else 'movie'
        def ours(x):   # (made for this file too: a wrongly tagged upload can carry this file's fingerprint and another film's lines)
            if tmdb and x.get('tmdb') and str(x['tmdb']) != str(tmdb) and not episode:
                return False
            if x.get('kind') and x['kind'] != want_kind and x['kind'] != 'tvshow':
                return False
            q_ = flat(query)
            return not q_ or not x.get('title') or q_ in flat(x['title']) or flat(x['title']) in q_
        out = [x for x in out if ours(x)]
        for x in out:   # (how like this copy each one's release is: the same disc, picture size, encoding or group, or the same cut)
            x['like'] = likeness(hint, x['name'])
        # (the likeliest to fit first: made for this file, then those like this copy, then trusted uploaders and the most
        # downloaded; machine translations last)
        # (the obvious ones first: John, 13 Nov 2026, "subtitles don't work if they don't select the obvious best ones". Made for this
        # file; then the same release (source and group); then by how many have used them, a little for likeness and trust)
        out.sort(key=lambda x: (not x['exact'], x['like'] < 7, x['machine'],
                                -(math.log10(1 + x['downloads']) + 0.15 * x['like'] + (0.5 if x['trusted'] else 0))))
        return out[:40]

    def exact_any(self, mhash):
        """Subtitles in any language made for this very file (by its fingerprint): their timing fits it whatever the words, so
        they can time others. The most downloaded first."""
        if not mhash:
            return []
        j = self._req('GET', '/subtitles?' + urllib.parse.urlencode({'moviehash': mhash}))
        out = []
        for d in j.get('data') or []:
            a = d.get('attributes') or {}
            if not a.get('moviehash_match') or a.get('machine_translated') or a.get('ai_translated'):
                continue
            for f in a.get('files') or []:
                if f.get('file_id'):
                    out.append((a.get('download_count') or 0, f['file_id'], a.get('language') or ''))
        return [(fid, lang) for _, fid, lang in sorted(out, reverse=True)]

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


SOURCES = {'bluray': 'bluray', 'blu-ray': 'bluray', 'bdrip': 'bluray', 'brrip': 'bluray', 'bdremux': 'bluray', 'remux': 'bluray',
           'web': 'web', 'webrip': 'web', 'webdl': 'web', 'web-dl': 'web', 'amzn': 'web', 'nf': 'web', 'dsnp': 'web',
           'hdtv': 'tv', 'dvdrip': 'dvd', 'dvd': 'dvd', 'dvdscr': 'dvd', 'hddvd': 'hddvd'}
CUTS = ('extended', 'theatrical', 'directors', 'director', 'uncut', 'remastered', 'roadshow', 'restored', 'criterion', 'final', 'special', 'imax', 'unrated')


def _marks(name):
    name = re.sub(r'\.(mkv|mp4|m4v|avi|srt|sub|ass)$', '', (name or '').strip(), flags=re.I)
    w = re.findall(r'[a-z0-9]+(?:-[a-z0-9]+)?', name.lower().replace('.', ' ').replace('_', ' '))
    res = next((x for x in w if re.fullmatch(r'(2160|1080|720|576|480)p', x)), '')
    src = next((SOURCES[x] for x in w if x in SOURCES), '')
    enc = next(('265' if x in ('x265', 'h265', 'hevc') else '264' if x in ('x264', 'h264', 'avc') else 'xvid' for x in w if x in ('x265', 'h265', 'hevc', 'x264', 'h264', 'avc', 'xvid', 'divx')), '')
    grp = re.findall(r'-([a-z0-9]{2,15})\s*$', re.sub(r'\.(mkv|mp4|avi|srt)$', '', (name or '').lower().strip()))
    grp = grp[0] if grp else (w[-1] if w and w[-1] not in SOURCES and not re.fullmatch(r'\d+p?|x26[45]|h26[45]|hevc|avc', w[-1]) else '')
    cut = set(x for x in w if x in CUTS)
    return res, src, enc, grp, cut


def likeness(mine, theirs):
    """How like this copy a subtitle's release is (0 to 10): the same source matters most (a Blu-ray and a DVD of a film are
    often different cuts, or different speeds), then the same release group, picture size and encoding, and the same cut."""
    if not mine or not theirs:
        return 0
    a, b = _marks(mine), _marks(theirs)
    n = 0
    if a[1] and a[1] == b[1]:
        n += 4
    elif a[1] and b[1]:
        n -= 2
    if a[3] and a[3] == b[3]:
        n += 3
    if a[0] and a[0] == b[0]:
        n += 1
    if a[2] and a[2] == b[2]:
        n += 1
    if a[4] or b[4]:
        n += 1 if a[4] & b[4] else -1
    return max(0, min(10, n))


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
                g_ = m.groups()   # (milliseconds as written: "055" is 55 ms, and "5" or "50" is 500 ms)
                ms = lambda x: int(x.ljust(3, '0')[:3]) / 1000
                s = int(g_[0]) * 3600 + int(g_[1]) * 60 + int(g_[2]) + ms(g_[3])
                e = int(g_[4]) * 3600 + int(g_[5]) * 60 + int(g_[6]) + ms(g_[7])
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

def _channels(ffmpeg, video, stream):
    """The sound's channel count and layout (5.1, stereo...), from ffprobe beside ffmpeg."""
    probe = os.path.join(os.path.dirname(ffmpeg), 'ffprobe') if os.path.dirname(ffmpeg) else 'ffprobe'
    try:
        out = subprocess.run([probe, '-v', 'error', '-select_streams', 'a:%d' % stream, '-show_entries', 'stream=channels,channel_layout',
                              '-of', 'json', video], capture_output=True, text=True, timeout=60).stdout
        st = (json.loads(out or '{}').get('streams') or [{}])[0]
        return int(st.get('channels') or 0), st.get('channel_layout') or ''
    except Exception:
        return 0, ''


SUB = 5   # (each tenth of a second heard as 5 slices, for how speech rises and falls with each syllable)


def _levels(ffmpeg, video, stream, mix, nice, perchannel):
    """Loudness in the voice range, 50 times a second, of each channel of a mix (pan filter): [[dB, ...] per channel]."""
    n = mix.count('|c')
    base = mix + ',highpass=f=250,lowpass=f=3200,aresample=8000,asetnsamples=n=%d:p=0,' % (8000 // (HZ * SUB))
    if perchannel:
        af = base + 'astats=metadata=1:reset=1:measure_perchannel=RMS_level:measure_overall=none,ametadata=mode=print:file=-'
    else:
        af = base + 'astats=metadata=1:reset=1,ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-'
    cmd = [ffmpeg, '-hide_banner', '-nostats', '-v', 'error', '-i', video, '-map', '0:a:%d' % stream, '-vn', '-sn', '-dn', '-af', af, '-f', 'null', '-']
    if nice and os.path.exists('/usr/sbin/taskpolicy'):
        cmd = ['/usr/sbin/taskpolicy', '-b'] + cmd
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, preexec_fn=(lambda: os.nice(10)) if nice else None)
    chans, t = [[] for _ in range(n if perchannel else 1)], None
    rate = HZ * SUB
    for line in p.stdout:
        if line.startswith('frame:'):
            m = re.search(r'pts_time:([\d.]+)', line)
            t = float(m.group(1)) if m else None
            continue
        m = re.match(r'lavfi\.astats\.(\d+|Overall)\.RMS_level=(.*)', line.strip())
        if not m or t is None:
            continue
        c = 0 if m.group(1) == 'Overall' else int(m.group(1)) - 1
        if c >= len(chans):
            continue
        v = m.group(2).strip()
        try:
            v = max(-90.0, float(v)) if v not in ('-inf', 'inf', 'nan') else -90.0
        except ValueError:
            v = -90.0
        L, i = chans[c], int(round(t * rate))
        while len(L) < i:
            L.append(L[-1] if L else -90.0)
        L.append(v)
    p.wait()
    err = p.stderr.read().strip()[-200:]
    return chans, err


def speech(ffmpeg, video, stream=0, nice=True):
    """Where people speak in the film, 10 readings a second (0 to 1). Speech, not just sound (John, 13 Nov 2026: "It's important it
    works on difficult cases", Fantasia's few spoken words under a full orchestra):
      * the dialogue: in 5.1 or 7.1 the centre channel, in stereo what's in the middle of both
      * how much it stands out from the rest: the centre against the front pair, the middle against what's to either side;
        narration sits alone in the middle while music spreads across every channel
      * how it rises and falls: speech changes with each syllable, several times a second; music holds its notes
      * each half-minute against its own background, so a loud score or a quiet film doesn't fool it."""
    ch, layout = _channels(ffmpeg, video, stream)
    if ch >= 3 and ('5.1' in layout or '7.1' in layout or '6.1' in layout or 'FC' in layout or ch >= 6):
        mix = 'pan=stereo|c0=FC|c1=0.5*FL+0.5*FR'      # (the dialogue, and the front pair beside it)
    elif ch == 2:
        mix = 'pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0-0.5*c1'   # (the middle, and what's to either side)
    else:
        mix = 'pan=mono|c0=c0'
    lv = None
    two = mix.startswith('pan=stereo')
    try:
        chans, err = _levels(ffmpeg, video, stream, mix, nice, perchannel=two)
        if len(chans[0]) >= HZ * SUB * 60 and (not two or len(chans) > 1 and len(chans[1]) >= HZ * SUB * 60):
            lv = chans
    except Exception as e:
        err = str(e)
    if lv is None and two:   # (an ffmpeg without per-channel measures: each heard on its own)
        a, err = _levels(ffmpeg, video, stream, 'pan=mono|c0=' + mix.split('|c0=')[1].split('|c1=')[0], nice, False)
        b2, _ = _levels(ffmpeg, video, stream, 'pan=mono|c0=' + mix.split('|c1=')[1], nice, False)
        if len(a[0]) >= HZ * SUB * 60:
            lv = [a[0], b2[0] if len(b2[0]) >= HZ * SUB * 60 else []]
    if lv is None:   # (only the whole sound)
        a, err = _levels(ffmpeg, video, stream, 'pan=mono|c0=c0', nice, False)
        lv = [a[0], []] if len(a[0]) >= HZ * SUB * 60 else None
    if lv is None:
        raise RuntimeError('couldn’t hear the film (%s)' % (err or 'no sound'))
    main, other = lv[0], (lv[1] if len(lv) > 1 else [])
    n = len(main) // SUB
    # per tenth of a second: level; how much the loudness varies over half a second around it (a voice comes and goes with every
    # syllable, a held chord doesn't); how fast it changes; and how much it stands out from the rest of the mix
    lev, spr, mod, pro = [], [], [], []
    for k in range(n):
        seg = main[k * SUB:(k + 1) * SUB]
        lev.append(sum(seg) / SUB)
        win = main[max(0, k * SUB - 2 * SUB):k * SUB + 3 * SUB]
        mu = sum(win) / len(win)
        spr.append(math.sqrt(sum((x - mu) ** 2 for x in win) / len(win)))
        prev = main[k * SUB - 1] if k else seg[0]
        mod.append(sum(abs(x - y) for x, y in zip(seg, [prev] + seg[:-1])) / SUB)
        if other and len(other) >= (k + 1) * SUB:
            o = other[k * SUB:(k + 1) * SUB]
            pro.append(sum(seg) / SUB - sum(o) / SUB)
    def smooth(x, w):   # (a running mean over w readings, centred)
        out, acc = [], 0.0
        for i, v in enumerate(x):
            acc += v
            if i >= w:
                acc -= x[i - w]
            out.append(acc / min(i + 1, w))
        h = w // 2
        return out[h:] + [out[-1]] * h
    mod = smooth(mod, 5)
    if pro:
        pro = smooth(pro, 3)
    def norm(x):   # (against the film as a whole: its middle 0, its top tenth 1)
        s2 = sorted(x)
        md, sp = s2[len(s2) // 2], max(1e-6, s2[len(s2) * 9 // 10] - s2[len(s2) // 2])
        return [0.0 if v < md else 1.0 if v > md + sp else (v - md) / sp for v in x]
    spr, mod = norm(spr), norm(mod)
    pro = norm(pro) if pro else []
    blk = 30 * HZ
    floors = []
    for k in range(0, n, blk):   # (near-silence left out: each half-minute's own quiet)
        part = sorted(lev[max(0, k - blk):k + 2 * blk])
        floors.append(part[len(part) // 10])
    out = []
    for i in range(n):
        gate = (lev[i] - floors[i // blk] - 1.0) / 4.0
        gate = 0.0 if gate < 0 else 1.0 if gate > 1 else gate
        f = 0.5 * spr[i] + 0.5 * mod[i]
        out.append(gate * (0.75 * f + 0.25 * pro[i] if pro else f))
    return out


def cue_signal(cues, n):
    """Subtitles as the same 10-a-second readings: 1 while a line shows (for timing against the film's own subtitles)."""
    sig = [0.0] * n
    for s, e, _ in cues:
        for i in range(max(0, int(s * HZ)), min(n, int(e * HZ) + 1)):
            sig[i] = 1.0
    return sig


def fit_pairs(pairs):
    """A ratio and shift from lines placed by hand: [(time in the file, when it's said in the film)], the latest last. One line: a
    shift; two or more far enough apart: the drift too (snapped to a usual frame-rate difference when it's near one). Lines that
    don't agree with each other (a wrong tap, or a version with a cut): the latest one, as a shift."""
    latest = pairs[-1]
    pairs = sorted(pairs)
    if len(pairs) == 1 or pairs[-1][0] - pairs[0][0] < 300:
        return 1.0, latest[1] - latest[0]
    n = len(pairs)
    mt, mf = sum(t for t, _ in pairs) / n, sum(f for _, f in pairs) / n
    r = sum((t - mt) * (f - mf) for t, f in pairs) / (sum((t - mt) ** 2 for t, _ in pairs) or 1)
    near = min(FPS_RATIOS, key=lambda x: abs(x - r))
    if abs(near - r) < 0.002:
        r = near
    if not 0.94 < r < 1.06:   # (no film runs that much faster or slower: the taps disagree)
        return 1.0, latest[1] - latest[0]
    return r, mf - r * mt


def sync_file(path, pairs):
    """The file's lines put where they were heard: pairs of (line number, when it's said in the film). One tap moves them all;
    taps that agree with one steady drift set that; taps that don't (a film in parts, each out by its own amount, like
    Fantasia's versions: John, 13 Nov 2026) each set the lines from there to the next tap."""
    with open(path, 'rb') as f:
        cues = parse_srt(decode(f.read()))
    pts = sorted((cues[i][0], float(t)) for i, t in pairs if 0 <= i < len(cues))
    if not pts:
        raise RuntimeError('which line?')
    r, off = fit_pairs([(cues[i][0], float(t)) for i, t in pairs if 0 <= i < len(cues)])
    worst = max(abs(t * r + off - f) for t, f in pts)
    if len(pts) == 1 or worst < 1.0:
        out = [[max(0.0, s * r + off), max(0.05, e * r + off), t] for s, e, t in cues]
        how = {'ratio': round(r, 5), 'shift': round(off, 2), 'parts': 1}
    else:   # (in parts: each tapped line's shift from it until the next tapped line; before the first, the first's)
        out = []
        for s_, e_, t_ in cues:
            d = pts[0][1] - pts[0][0]
            for t0, f0 in pts:
                if t0 <= s_ + 1e-6:
                    d = f0 - t0
            out.append([max(0.0, s_ + d), max(0.05, e_ + d), t_])
        for k in range(len(out) - 1):   # (no line running into the next where two parts meet)
            if out[k][1] > out[k + 1][0]:
                out[k][1] = max(out[k][0] + 0.3, out[k + 1][0] - 0.02)
        how = {'ratio': 1.0, 'shift': round(pts[0][1] - pts[0][0], 2), 'parts': len(pts)}
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(write_srt(out))
    os.replace(tmp, path)
    return dict(how, lines=len(cues))


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
    # coarse: a shift each second, on at most 400 of the lines (spread through the film), the plain speed first and the other
    # frame rates only if that isn't clearly it (quick on the old Mac mini: John, 13 Nov 2026, "it takes much too long")
    few = cues if len(cues) <= 400 else [cues[int(i * len(cues) / 400)] for i in range(400)]
    rows = []
    for r in [1.0] + [x for x in FPS_RATIOS if x != 1.0]:
        if cues[-1][1] * r > span + max_off:
            continue
        for k in range(int(-max_off), int(max_off) + 1):
            rows.append((_score(P, few, r, float(k)), r, float(k)))
        if r == 1.0 and len(rows) > 50:
            sc_ = [x[0] for x in rows]
            m_ = sum(sc_) / len(sc_)
            sd_ = math.sqrt(sum((x - m_) ** 2 for x in sc_) / len(sc_)) or 1.0
            if (max(sc_) - m_) / sd_ >= 10:   # (plainly a shift: the other speeds needn't be tried)
                break
    if not rows:
        return cues, {'ok': False, 'why': 'the subtitles are longer than the film'}
    scores = [x[0] for x in rows]
    mean = sum(scores) / len(scores)
    sd = math.sqrt(sum((x - mean) ** 2 for x in scores) / len(scores)) or 1.0
    rows.sort(reverse=True)
    # fine: the best few, to a tenth of a second (on all the lines; the score's measure from the sample's spread)
    best = (-1e18, rows[0][1], rows[0][2])
    for sc, r, k in rows[:4]:
        for d in range(-12, 13):
            o = k + d / 10.0
            v = _score(P, cues, r, o)
            if v > best[0]:
                best = (v, r, o)
    sc, r, off = best
    z = (_score(P, few, r, off) - mean) / sd
    orig = _score(P, cues, 1.0, 0.0)
    report = {'ratio': round(r, 5), 'shift': round(off, 2), 'sure': round(z, 1)}
    common = min(abs(r - x) for x in (1.0, 25 / 23.976, 23.976 / 25)) < 1e-4
    if z < (6.0 if common else 7.5):   # (not clearly better than chance: left as it came; a rarer frame-rate change must be surer still)
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
    # (searched for only where some five minutes of the film fit a shift of their own clearly better: quick to check)
    odd = z < 12
    if not odd:
        i0 = 0
        while i0 < len(out) and not odd:
            i1 = i0
            while i1 < len(out) and out[i1][0] - out[i0][0] < 300:
                i1 += 1
            ch = out[i0:i1]
            if len(ch) >= 8:
                base = _score(P, ch, 1.0, 0.0)
                bv = max(_score(P, ch, 1.0, float(k)) for k in range(int(-max_off), int(max_off) + 1) if abs(k) > 2)
                if bv > base + max(8.0, abs(base) * 0.3):
                    odd = True
            i0 = i1
    for i in (range(step, len(out) - step + 1, step) if odd else ()):
        lo, lv = fit(out[:i])
        ro, rv = fit(out[i:])
        # (clearly better for the part that moves: a short part of a long film counts too)
        part = min(_score(P, out[:i], 1.0, 0.0), _score(P, out[i:], 1.0, 0.0), key=abs)
        if abs(lo - ro) >= 1.0 and lv + rv > whole + max(15.0, abs(whole) * 0.03, abs(part) * 0.3) and (jump is None or lv + rv > jump[0]):
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
