"""Grifflix: the Criterion logo check, the opening of each Criterion Collection film read on John's Mac (John, 25 Nov 2026, "Run the logo
check on your Mac": the Mac mini, slowed for heat all day, had read none since the morning).

Started by John's Mac's helper (convert-here.sh agent) when it's at home with the G-Drive connected, alongside whatever it's converting.
The Mac mini hands it a few films at a time; here each one's first 30 seconds are read from the G-Drive, two pictures a second, each made
small (32 x 18) and grey, the way the Mac mini says (it sends the ffmpeg filter itself, so the two read alike), and sent back. The Mac mini
compares them with Criterion's logo and keeps the list. At this Mac's lowest call on its processors and the drive (taskpolicy -b, nice), so
the film being converted, and everything else on it, come first. It stops when someone starts watching, and is started again later.
Only Python's standard library, and ffmpeg (the one the converter uses).
"""
import base64, json, os, subprocess, sys, time, urllib.request

HOME = os.path.expanduser('~/Grifflix')
NEXT = os.path.join(HOME, 'logo.next')   # (when to ask again: the helper doesn't start this before then)
UA = 'Grifflix-logocheck/1.0'   # (its own name: Cloudflare turns away Python's own)


def say(m):
    print(time.strftime('%Y-%m-%d %H:%M ') + 'logo check: ' + m, flush=True)


def later(secs):
    """Not asked again for a while (nothing to read, someone watching, or a problem)."""
    try:
        with open(NEXT, 'w') as f:
            f.write(str(int(time.time() + max(60, secs))))
    except OSError:
        pass


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


def ffmpeg():
    for p in (os.path.expanduser('~/bin/ffmpeg'), '/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg'):
        if os.access(p, os.X_OK):
            return p
    return None


def call(base, key, path, body=None):
    h = {'User-Agent': UA, 'Authorization': 'Bearer ' + key}
    if body is not None:
        h['Content-Type'] = 'application/json'
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=120).read() or b'{}')


def read(ff, path, secs, vf, one):
    """The film's first secs seconds as small grey pictures, one after another (raw bytes); tried plainer if a copy won't decode the quick
    way, as the Mac mini does."""
    bg = ['/usr/sbin/taskpolicy', '-b'] if os.path.exists('/usr/sbin/taskpolicy') else []
    fast = ['-skip_loop_filter', 'all', '-threads', '2']
    best = b''
    for opt in (['-hwaccel', 'videotoolbox'] + fast, fast, []):
        try:
            p = subprocess.run(bg + [ff, '-nostdin', '-v', 'error'] + opt + ['-t', str(secs), '-i', path, '-an', '-sn', '-dn', '-vf', vf,
                                                                              '-f', 'rawvideo', '-'],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=300, preexec_fn=lambda: os.nice(15))
            out = p.stdout or b''
        except subprocess.TimeoutExpired as e:   # (stuck on the drive: what it had read)
            out = e.output or b''
        except Exception:
            out = b''
        out = out[:len(out) // one * one]
        if len(out) > len(best):
            best = out
        if len(out) // one >= 20:
            break
    return best


def main():
    base, key = conf()
    ff = ffmpeg()
    if not base or not key:
        return later(3600)
    if not ff:
        say('no ffmpeg on this Mac (it goes in ~/bin, as for converting)')
        return later(3600)
    if not os.path.isdir('/Volumes/G-Drive'):
        return later(600)
    stop_at = time.time() + 3 * 3600   # (a few hours at most each time; the helper starts it again)
    n = 0
    while time.time() < stop_at:
        try:
            j = call(base, key, '/macagent/logonext?home=1&n=8')
        except Exception as e:
            say('the Mac mini didn’t answer (%s): again in ten minutes' % str(e)[:120])
            return later(600)
        films = j.get('films') or []
        if not films:
            if n:
                say('%d read; %s' % (n, j.get('why') or ('%s left' % j.get('left') if j.get('left') else 'all read')))
            return later(int(j.get('wait') or 3600))
        one = int(j.get('w') or 32) * int(j.get('h') or 18)
        for f in films:
            if not os.path.isdir('/Volumes/G-Drive'):   # (the G-Drive gone: those given back when the Mac mini's half hour is up)
                return later(600)
            path = f.get('path') or ''
            if not os.path.isfile(path):
                body = {'id': f['id'], 'missing': 1}
            else:
                t0 = time.time()
                px = read(ff, path, j.get('secs') or 30, j.get('vf'), one)
                body = {'id': f['id'], 's': f.get('s'), 'n': len(px) // one, 'took': round(time.time() - t0, 1)}
                if len(px) // one >= 20:
                    body['px64'] = base64.b64encode(px).decode()
                else:
                    body['bad'] = 1
                    say('%s: couldn’t read its start here (the Mac mini will try)' % f.get('name'))
            try:
                r = call(base, key, '/macagent/logodone', body)
            except Exception as e:
                say('couldn’t send %s back (%s): again in ten minutes' % (f.get('name'), str(e)[:120]))
                return later(600)
            n += 1
            if r.get('stop'):   # (someone's started watching: the drive left to them)
                say('%d read; someone is watching: again later' % n)
                return later(int(r.get('wait') or 600))
            time.sleep(2)   # (a rest between films: the G-Drive free for everything else)
    say('%d read; carrying on next time' % n)
    later(60)


if __name__ == '__main__':
    try:
        os.nice(10)
    except OSError:
        pass
    main()
