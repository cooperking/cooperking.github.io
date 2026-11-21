"""Grifflix: English subtitles made ready ahead of time for the foreign-language films, timed by what is actually said (John, 20 Nov
2026: "I want to do it accurately and just have subtitles ready, so pre-prepare" ... "I prefer to use the whisper option rather than the
current method").

Run on John's Mac by its helper (convert-here.sh agent), one film at a time, when it has no film to convert. The Mac mini hands it a
job: the film's place on the G-Drive, its length, and a few English subtitles from OpenSubtitles (the likeliest first). Here:
  * four samples of the film's sound (its opening, a third and two thirds in, and near the end, six minutes each) are put into English words by
    whisper.cpp (its translation, the film's own language heard), with their times;
  * each subtitle's lines are matched to those words (the words they share, names and nouns above all), and from the matches the
    subtitle's timing is worked out: how far out it is, and whether it runs at another speed (25 against 23.976 frames a second);
  * one timing for the whole film, its speed and its offset, which every part listened to must agree with; a subtitle that doesn't fit
    the whole film is passed over for the next, and none fitting is a fail (not cut into parts: a different cut has different subtitles);
  * the subtitle matching best is sent back, said whether its timing is sure; the Mac mini saves it beside the film.
Only Python's standard library; whisper.cpp (whisper-cli) and ffmpeg as installed by `convert-here.sh subs-setup`.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request

HOME = os.path.expanduser('~/Grifflix')
MODEL = os.path.join(HOME, 'whisper', 'ggml-medium.bin')
# (or, with no Homebrew on John's Mac, faster-whisper: Whisper in Python, put in ~/Grifflix/fw by `convert-here.sh subs-setup` with no
# administrator rights needed: John, 21 Nov 2026, "we need a way without homebrew")
# (its own name on what it sends: Cloudflare, in front of the Mac mini, turns away Python's own ("Python-urllib") with 403, and every film's
# subtitles were lost: Io Capitano, Samurai I: John, 21 Nov 2026)
UA = 'Grifflix-subprep/1.0'
FW_PY = os.path.join(HOME, 'fw', 'bin', 'python3')
FW_MODEL = os.environ.get('GRIFFLIX_FW_MODEL') or 'small'
FW_RUN = r'''
import sys
from faster_whisper import WhisperModel
wav, out, model, translate = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == '1'
import wave, numpy as np
w = wave.open(wav, 'rb'); audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0; w.close()   # (16 kHz mono, as made: not read by its own reader, whose versions differ)
m = WhisperModel(model, device='cpu', compute_type='int8')
segs, info = m.transcribe(audio, task='translate' if translate else 'transcribe', vad_filter=True)
def ts(t):
    t = max(0.0, t); h = int(t // 3600); mi = int(t % 3600 // 60); se = t % 60
    return '%02d:%02d:%06.3f' % (h, mi, se)
with open(out, 'w', encoding='utf-8') as f:
    for n, s in enumerate(segs, 1):
        f.write('%d\n%s --> %s\n%s\n\n' % (n, ts(s.start).replace('.', ','), ts(s.end).replace('.', ','), s.text.strip()))
'''
RATIOS = [1.0, 25 / 23.976, 23.976 / 25, 25 / 24, 24 / 25, 24 / 23.976, 23.976 / 24]
SAMPLE = 360   # (seconds of sound in each sample)
STOP = set('''the and that this with have from they what your you are was were for not but his her him she our out who why how all
any can its it's i'm you're don't didn't that's there their them then than when will would could should been being just like know
get got here come came going want okay yes yeah well now one two very much more some about into over only also too really right
there's what's let's i'll we're they're isn't aren't wasn't can't won't has had did does said say see look make made take let
tell told think thought good back way still even never ever always these those because where which while again off down mr mrs'''.split())


def tool(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
        for d in ('/opt/homebrew/bin', '/usr/local/bin', os.path.expanduser('~/bin'), os.path.join(HOME, 'bin')):
            if os.access(os.path.join(d, n), os.X_OK):
                return os.path.join(d, n)
    return None


def stamp_in(s):
    h, m, rest = s.strip().replace('.', ',').split(':')
    sec, ms = (rest.split(',') + ['0'])[:2]
    return int(h) * 3600 + int(m) * 60 + int(sec) + int((ms + '000')[:3]) / 1000


def stamp_out(t):
    t = max(0.0, t)
    ms = int(round(t * 1000))
    return '%02d:%02d:%02d,%03d' % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def parse_srt(text):
    cues = []
    for block in re.split(r'\r?\n\s*\r?\n', text.replace('﻿', '')):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        i = next((n for n, l in enumerate(lines) if '-->' in l), None)
        if i is None:
            continue
        try:
            a, b = lines[i].split('-->')
            cues.append([stamp_in(a), stamp_in(b.split()[0]), '\n'.join(lines[i + 1:])])
        except (ValueError, IndexError):
            continue
    return cues


def write_srt(cues):
    return '\n'.join('%d\n%s --> %s\n%s\n' % (n + 1, stamp_out(a), stamp_out(b), t) for n, (a, b, t) in enumerate(cues))


def words(t):
    t = re.sub(r'<[^>]+>|\{[^}]+\}|\[[^\]]*\]|\([^)]*\)', ' ', t.lower()).replace('’', "'")
    return set(w for w in re.findall(r"[a-z0-9][a-z0-9']+", t) if (len(w) >= 3 or w.isdigit()) and w not in STOP)


def transcribe(ff, wcli, film, t0, length, translate, work):
    """Six minutes of the film's sound from t0, put into words by whisper.cpp: [(start, end, words)] in the film's own time."""
    wav = os.path.join(work, 'w.wav')
    subprocess.run([ff, '-hide_banner', '-nostdin', '-y', '-v', 'error', '-ss', '%.2f' % t0, '-t', str(length), '-i', film, '-vn', '-ac', '1',
                    '-ar', '16000', '-c:a', 'pcm_s16le', wav], check=True, timeout=900)
    out = os.path.join(work, 'w')
    if wcli == 'fw':   # (faster-whisper, in its own Python)
        subprocess.run([FW_PY, '-c', FW_RUN, wav, out + '.srt', FW_MODEL, '1' if translate else '0'], check=True, timeout=3600,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        cmd = [wcli, '-m', MODEL, '-f', wav, '-osrt', '-of', out, '-l', 'auto', '-np']
        if translate:
            cmd.append('-tr')
        subprocess.run(cmd, check=True, timeout=3600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    segs = [(a + t0, b + t0, words(t), t) for a, b, t in parse_srt(open(out + '.srt', encoding='utf-8', errors='replace').read())]   # (its words, and as heard)
    return [s for s in segs if s[2]]


def matches(segs, cues):
    """Each heard line and the subtitle line most like it: (film time, subtitle time, likeness)."""
    cw = [(c[0], words(c[2])) for c in cues]
    out = []
    for sg in segs:
        s0, s1, sw = sg[0], sg[1], sg[2]
        best = None
        for ct, w in cw:
            if not w:
                continue
            shared = sw & w
            if len(shared) >= 2 or (len(shared) == 1 and len(next(iter(shared))) >= 6 and min(len(sw), len(w)) <= 2):
                sim = len(shared) / min(len(sw), len(w))
                if sim >= .5 and (not best or sim > best[2]):
                    best = (s0, ct, sim)
        if best:
            out.append(best)
    return out


def fit(pairs, ratios=RATIOS, width=1.2):
    """The speed and offset most matches agree on: (matches agreeing, speed, offset, typical miss in seconds)."""
    best = (0, 1.0, 0.0, 99.0)
    for r in ratios:
        offs = sorted(f - r * c for f, c, _ in pairs)
        j = 0
        for i in range(len(offs)):
            while offs[i] - offs[j] > width:
                j += 1
            n = i - j + 1
            if n > best[0]:
                mid = offs[j:i + 1]
                b = mid[len(mid) // 2]
                miss = sorted(abs(x - b) for x in mid)[len(mid) // 2]
                best = (n, r, b, miss)
    return best


PROG = {}   # (where to say how it's going: set by main)


def prog(step):
    """What it's doing now, told to the Mac mini for the admin page (John, 21 Nov 2026, "I want more details than that"); never stops it."""
    print(step, flush=True)
    try:
        urllib.request.urlopen(urllib.request.Request(PROG['base'] + '/macagent/subprog', data=json.dumps({'id': PROG['id'], 'step': step}).encode(),
                                                      headers={'Content-Type': 'application/json', 'User-Agent': UA, 'Authorization': 'Bearer ' + PROG['key']}), timeout=30).read()
    except Exception:
        pass


def prepare(job, ff, wcli, work):
    film, dur = job['path'], float(job.get('dur') or 0)
    if not dur:
        p = subprocess.run([ff, '-hide_banner', '-i', film], capture_output=True, text=True)
        m = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', p.stderr)
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
    if dur < 600:
        starts = [0.0]
    else:
        starts = [0.0] + [max(0, min(dur - SAMPLE, dur * f - SAMPLE / 2)) for f in (.33, .62, .9)]   # (the opening too: versions often differ there)
    heard = []
    for k_, t0 in enumerate(starts):
        prog('Listening with Whisper: part %d of %d (from %d:%02d)' % (k_ + 1, len(starts), int(t0 // 3600), int(t0 % 3600 // 60)))
        heard.append((t0, transcribe(ff, wcli, film, t0, SAMPLE, job.get('translate', True), work)))
    prog('Matching %d subtitle file%s to what was heard (%d lines heard)' % (len(job.get('cands') or []), '' if len(job.get('cands') or []) == 1 else 's', sum(len(s_) for _, s_ in heard)))
    results, cues_all = [], []
    def judge(cues, cand_i, cand, ratios):   # (one subtitle at the given speeds: its one timing for the whole film, and whether every part agrees)
        per = [(t0, matches(segs, cues)) for t0, segs in heard]
        allp = [p for _, ps in per for p in ps]
        n, r, b, miss = fit(allp, ratios)
        inl = [(f, c) for f, c, _ in allp if abs(f - (r * c + b)) <= 1.0]
        if len(inl) >= 8:   # (the speed and offset fine-tuned on the lines that agree: a film a fraction faster drifts a second over its length)
            mc = sum(c for _, c in inl) / len(inl); mf = sum(f for f, _ in inl) / len(inl)
            vc = sum((c - mc) ** 2 for _, c in inl)
            if vc > 0:
                r2 = sum((c - mc) * (f - mf) for f, c in inl) / vc
                if abs(r2 / r - 1) < .003:
                    r, b = r2, mf - r2 * mc
                    res_ = sorted(abs(f - (r * c + b)) for f, c, _ in allp)
                    n = sum(1 for x in res_ if x <= .7)
                    miss = sorted(abs(f - (r * c + b)) for f, c in inl)[len(inl) // 2]
        wins = []   # (each part: its lines that agree with the whole film's timing, and how far out they are)
        for t0, ps in per:
            ok = [abs(f - (r * c + b)) for f, c, _ in ps if abs(f - (r * c + b)) <= .7]
            wins.append((t0, len(ok), round(sorted(ok)[len(ok) // 2], 2) if ok else 99, len(ps)))
        heardp = [w for w in wins if w[3] >= 3]   # (parts where enough was said to judge)
        fits = len(heardp) >= min(2, len(wins)) and all(w[1] >= max(2, w[3] * .3) for w in heardp)
        sure = fits and n >= 12 and miss <= .6
        moved = [[r * a + b, r * e + b, t] for a, e, t in cues]
        how = 'moved %+.1f s%s' % (b, '' if abs(r - 1) < .00005 else ', at %.4fx speed' % r)
        return {'_ci': cand_i, '_r': r, '_b': b, 'fits': bool(fits), 'exact': abs(r - 1) < .003, 'name': cand.get('name') or '', 'n': n, 'miss': round(miss, 2), 'sure': bool(sure), 'how': how, 'srt': write_srt(moved),
                'samples': [[round(w[0]), w[1], w[2]] for w in wins]}

    # (each subtitle first as it is, at its own speed: the right one fits with only an offset. Only if none of them, nor the next ones fetched
    # one at a time, fits so, the best fitted by a change of speed (made for another copy): John, 21 Nov 2026, "before retiming for frame rate,
    # you need to go try and find the right sub. Better to get the right one exactly before fixing")
    pool = list(job.get('cands') or [])
    more = list(job.get('more') or [])
    k = 0
    while k < len(pool):
        cand = pool[k]
        cues = parse_srt(cand.get('srt') or '')
        cues_all.append(cues)
        k += 1
        if len(cues) < 20:
            continue
        ex_ = judge(cues, len(cues_all) - 1, cand, [1.0])
        if ex_['fits']:
            results.append(ex_)
            if ex_['sure']:
                break   # (found: this one, as it is)
        else:
            results.append(judge(cues, len(cues_all) - 1, cand, RATIOS))
        if k >= len(pool) and more and not any(x['fits'] and x['exact'] for x in results):
            nx = more.pop(0)
            prog('None fits exactly yet: trying another subtitle (%s)' % (nx.get('name') or nx.get('file_id')))
            try:
                req = urllib.request.Request(PROG['base'] + '/macagent/subfile?file=%s' % nx['file_id'], headers={'User-Agent': UA, 'Authorization': 'Bearer ' + PROG['key']})
                srt = json.loads(urllib.request.urlopen(req, timeout=120).read() or b'{}').get('srt') or ''
                if srt:
                    pool.append({'name': nx.get('name') or '', 'srt': srt})
            except Exception as e_:
                prog('Couldn’t get another subtitle: %s' % str(e_)[:100])
                more = []
    results.sort(key=lambda x: (x['fits'], x['exact'], x['sure'], x['n'], -x['miss']), reverse=True)
    if results:   # (what was heard and matched, for the admin page: John, 21 Nov 2026, "I should be able to see what whisper took as the timing,
        # the translation, the matching lines")
        best = results[0]
        cues = cues_all[best['_ci']]
        ev = []
        for t0, segs in heard:
            o_ = best['_b']
            rows = []
            for sg in segs[:60]:
                bi = None
                for ci, c in enumerate(cues):
                    w = words(c[2])
                    sh = sg[2] & w
                    if w and (len(sh) >= 2 or (len(sh) == 1 and len(next(iter(sh))) >= 6 and min(len(sg[2]), len(w)) <= 2)):
                        sim = len(sh) / min(len(sg[2]), len(w))
                        if sim >= .5 and (not bi or sim > bi[1]):
                            bi = (ci, sim)
                row = [round(sg[0], 1), sg[3].strip()[:160]]
                if bi:
                    c = cues[bi[0]]
                    mv = best['_r'] * c[0] + o_
                    row += [round(c[0], 1), re.sub(r'<[^>]+>', '', c[2]).replace('\n', ' ').strip()[:160], round(mv, 1), abs(sg[0] - mv) <= .7]
                rows.append(row)
            ev.append({'from': round(t0), 'rows': rows})
        best['evidence'] = ev
    for x in results:
        for k in ('_ci', '_r', '_b'):
            x.pop(k, None)
    return results


def main():
    job_file, base, key = sys.argv[1], sys.argv[2], sys.argv[3]
    job = json.load(open(job_file))
    PROG.update(base=base, key=key, id=job['id'])
    say = lambda body: urllib.request.urlopen(urllib.request.Request(base + '/macagent/subdone', data=json.dumps(dict(body, id=job['id'])).encode(),
                                              headers={'Content-Type': 'application/json', 'User-Agent': UA, 'Authorization': 'Bearer ' + key}), timeout=120).read()
    ff, wcli = tool('ffmpeg'), tool('whisper-cli', 'whisper-cpp', 'main')
    if not (wcli and os.path.exists(MODEL)) and os.access(FW_PY, os.X_OK):
        wcli = 'fw'
    if not ff or not wcli or (wcli != 'fw' and not os.path.exists(MODEL)):
        say({'ok': False, 'setup': True, 'error': 'Whisper isn’t set up on this Mac yet (admin page, Subtitles made ready: the setup command)'})
        return
    if not os.path.exists(job['path']):
        say({'ok': False, 'error': 'the film isn’t where the Mac mini said (is the G-Drive connected?)'})
        return
    work = tempfile.mkdtemp(prefix='grifflix-subs-')
    prog('Started on your Mac (%s)' % ('faster-whisper' if wcli == 'fw' else 'whisper.cpp'))
    t0 = time.time()
    try:
        res = prepare(job, ff, wcli, work)
    except Exception as e:
        say({'ok': False, 'error': str(e)[:300]})
        return
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not res or not res[0].get('fits') or res[0]['n'] < 4:   # (none fitting the whole film: a fail, nothing saved)
        say({'ok': False, 'error': 'none of the subtitles matched what’s said through the whole film', 'tried': [[x['name'], x['n']] for x in res]})
        return
    best = res[0]
    prog('Sending the subtitles back (%s, %d lines matched)' % (best['how'], best['n']))
    print('%s: %s (%d lines matched, typically %.1f s out before)%s' % (job.get('name'), best['how'], best['n'], best['miss'], '' if best['sure'] else ': NOT SURE'))
    # (sent packed, base64, not as text: Cloudflare in front of the Mac mini refused a whole film's dialogue as plain text, 403: Io Capitano,
    # John, 21 Nov 2026)
    import base64
    say({'ok': True, 'evidence': best.get('evidence') or [], 'srt64': base64.b64encode(best['srt'].encode('utf-8')).decode(), 'sure': best['sure'], 'how': best['how'], 'n': best['n'], 'miss': best['miss'], 'chose': best['name'],
         'samples': best['samples'], 'mins': round((time.time() - t0) / 60, 1), 'tried': [[x['name'], x['n'], x['sure']] for x in res]})


if __name__ == '__main__':
    try:
        main()
    except Exception as e_:   # (said on the admin page too, not only in this Mac's log)
        prog('Stopped: %s' % str(e_)[:160])
        raise
