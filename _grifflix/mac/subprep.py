"""Grifflix: English subtitles made ready ahead of time for the foreign-language films, timed by what is actually said (John, 20 Nov
2026: "I want to do it accurately and just have subtitles ready, so pre-prepare" ... "I prefer to use the whisper option rather than the
current method").

Run on John's Mac by its helper (convert-here.sh agent), one film at a time, when it has no film to convert. The Mac mini hands it a
job: the film's place on the G-Drive, its length, and a few English subtitles from OpenSubtitles (the likeliest first). Here:
  * four samples of the film's sound (its opening, a third and two thirds in, and near the end, six minutes each) are put into English words by
    whisper.cpp (its translation, the film's own language heard), with their times;
  * each subtitle's lines are matched to those words (the words they share, names and nouns above all), and from the matches the
    subtitle's timing is worked out: how far out it is, and whether it runs at another speed (25 against 23.976 frames a second);
  * where the three samples agree, the whole subtitle is moved by that; where they don't (a version with a different cut), each part by
    its own;
  * the subtitle matching best is sent back, said whether its timing is sure; the Mac mini saves it beside the film.
Only Python's standard library; whisper.cpp (whisper-cli) and ffmpeg as installed by `convert-here.sh subs-setup`.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request

HOME = os.path.expanduser('~/Grifflix')
MODEL = os.path.join(HOME, 'whisper', 'ggml-medium.bin')
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
    cmd = [wcli, '-m', MODEL, '-f', wav, '-osrt', '-of', out, '-l', 'auto', '-np']
    if translate:
        cmd.append('-tr')
    subprocess.run(cmd, check=True, timeout=3600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    segs = [(a + t0, b + t0, words(t)) for a, b, t in parse_srt(open(out + '.srt', encoding='utf-8', errors='replace').read())]
    return [s for s in segs if s[2]]


def matches(segs, cues):
    """Each heard line and the subtitle line most like it: (film time, subtitle time, likeness)."""
    cw = [(c[0], words(c[2])) for c in cues]
    out = []
    for s0, s1, sw in segs:
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
    for t0 in starts:
        heard.append((t0, transcribe(ff, wcli, film, t0, SAMPLE, job.get('translate', True), work)))
    results, cues_all = [], []
    for cand_i, cand in enumerate(job.get('cands') or []):
        cues = parse_srt(cand.get('srt') or '')
        cues_all.append(cues)
        extra = {}
        if len(cues) < 20:
            continue
        per = [(t0, matches(segs, cues)) for t0, segs in heard]
        bypos = dict(per)
        allp = [p for _, ps in per for p in ps]
        n, r, b, miss = fit(allp)
        wins = []   # (each sample's own offset, at the speed found)
        for t0, ps in per:
            wn, _, wb, wmiss = fit(ps, [r]) if ps else (0, r, b, 99)
            wins.append((t0, wn, wb, wmiss))
        good = [w for w in wins if w[1] >= 3]
        agree = all(abs(w[2] - b) <= .7 for w in good)
        sure = n >= 12 and len(good) >= min(2, len(wins)) and miss <= .6
        if agree or len(good) < 2:
            moved = [[r * a + b, r * e + b, t] for a, e, t in cues]
            how = 'moved %+.1f s%s' % (b, '' if r == 1 else ', at %.3fx speed' % r)
        else:   # (a different cut: each part of the film by its own offset; where one part ends found by more, shorter samples between
            # the two that differ, halving the gap each time till it's within a few minutes)
            good.sort()
            bounds = []
            for k in range(len(good) - 1):
                lo, hi, ob = good[k][0] + SAMPLE, good[k + 1][0], good[k][2]
                if abs(good[k + 1][2] - ob) <= .7:
                    bounds.append((hi - ob) / r)
                    continue
                nb = good[k + 1][2]
                def split(ps):   # (a sample with lines on both sides of the change: just where it is)
                    old = [f for f, c, _ in ps if abs(f - r * c - ob) <= .7]
                    new = [f for f, c, _ in ps if abs(f - r * c - nb) <= .7]
                    return (max(old), min(new)) if len(old) >= 2 and len(new) >= 2 and max(old) < min(new) else None
                sp = split(bypos.get(good[k][0], [])) or split(bypos.get(good[k + 1][0], []))
                if sp:
                    bounds.append(((sp[0] + sp[1]) / 2 - ob) / r)
                    continue
                tries = 0
                while hi - lo > 150 and tries < 8:
                    tries += 1
                    mid = (lo + hi) / 2 - 60
                    key = round(mid)
                    if key not in extra:
                        extra[key] = matches(transcribe(ff, wcli, film, mid, 120, job.get('translate', True), work), cues_all[cand_i])
                    ps = extra[key]
                    if len(ps) < 2:
                        break
                    sp = split(ps)
                    if sp:   # (the change inside this sample: just where, from its lines)
                        lo, hi = sp
                        break
                    if len([1 for f, c, _ in ps if abs(f - r * c - ob) <= .7]) >= len([1 for f, c, _ in ps if abs(f - r * c - nb) <= .7]):
                        lo = mid + 120
                    else:
                        hi = mid
                bounds.append(((lo + hi) / 2 - good[k][2]) / r)   # (where it changes, in the subtitle's own time)
            def off(a):
                for k, x in enumerate(bounds):
                    if a < x:
                        return good[k][2]
                return good[-1][2]
            moved = [[r * a + off(a), r * e + off(a), t] for a, e, t in cues]
            how = 'moved by part (' + ', '.join('%+.1f s' % w[2] for w in good) + ')%s' % ('' if r == 1 else ', at %.3fx speed' % r)
            sure = sure and all(w[3] <= .6 for w in good)
        results.append({'name': cand.get('name') or '', 'n': n, 'miss': round(miss, 2), 'sure': bool(sure), 'how': how, 'srt': write_srt(moved),
                        'samples': [[round(w[0]), w[1], round(w[2], 2)] for w in wins]})
    results.sort(key=lambda x: (x['sure'], x['n'], -x['miss']), reverse=True)
    return results


def main():
    job_file, base, key = sys.argv[1], sys.argv[2], sys.argv[3]
    job = json.load(open(job_file))
    say = lambda body: urllib.request.urlopen(urllib.request.Request(base + '/macagent/subdone', data=json.dumps(dict(body, id=job['id'])).encode(),
                                              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}), timeout=120).read()
    ff, wcli = tool('ffmpeg'), tool('whisper-cli', 'whisper-cpp', 'main')
    if not ff or not wcli or not os.path.exists(MODEL):
        say({'ok': False, 'setup': True, 'error': 'Whisper isn’t set up on this Mac yet (admin page, Subtitles made ready: the setup command)'})
        return
    if not os.path.exists(job['path']):
        say({'ok': False, 'error': 'the film isn’t where the Mac mini said (is the G-Drive connected?)'})
        return
    work = tempfile.mkdtemp(prefix='grifflix-subs-')
    t0 = time.time()
    try:
        res = prepare(job, ff, wcli, work)
    except Exception as e:
        say({'ok': False, 'error': str(e)[:300]})
        return
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not res or res[0]['n'] < 4:
        say({'ok': False, 'error': 'none of the subtitles matched what’s said', 'tried': [[x['name'], x['n']] for x in res]})
        return
    best = res[0]
    print('%s: %s (%d lines matched, typically %.1f s out before)%s' % (job.get('name'), best['how'], best['n'], best['miss'], '' if best['sure'] else ': NOT SURE'))
    say({'ok': True, 'srt': best['srt'], 'sure': best['sure'], 'how': best['how'], 'n': best['n'], 'miss': best['miss'], 'chose': best['name'],
         'samples': best['samples'], 'mins': round((time.time() - t0) / 60, 1), 'tried': [[x['name'], x['n'], x['sure']] for x in res]})


if __name__ == '__main__':
    main()
