#!/usr/bin/env python3
"""Films John has more than one copy of: the best one kept, the others set aside (John, 16 Nov 2026: "can we also delete the
duplicate films, picking the best one - let me know what remains").

A copy is the same film when the media server has the same title and year for it and its length is within 8 minutes. Not one when
its folder says it's another film (the media server's match is wrong: No (2012) taken for No Country for Old Men): those are listed,
to put right with Fix match, and left alone. A version John keeps on purpose ("{edition-…}" in its name) is never touched.

The best copy: the sharpest picture (1080p over 720p over less; a few lines either way is the same), then the most detail (bitrate),
then the bigger file. The others go to "Set aside/Duplicates" on the G-Drive (not deleted: John empties it when he's happy), with their
own subtitles and pictures; a folder left with no film goes there whole, its extras first given to the kept copy's folder if that has
none. Calendars, lists, hearts and where people stopped follow the kept copy.

  python3 ~/Grifflix/duplicates.py        what it would do (nothing changes)
  python3 ~/Grifflix/duplicates.py go     does it
  python3 ~/Grifflix/duplicates.py json   the same as JSON, for the admin page's Duplicates tab
  python3 ~/Grifflix/duplicates.py go keep=<id>,<id>   only those films, each keeping the copy chosen (from the admin page)
"""
import collections, difflib, json, os, re, shutil, sys, unicodedata, urllib.parse, urllib.request

GO = 'go' in sys.argv[1:]
JSON = 'json' in sys.argv[1:]
CHOSEN = any(a.startswith('keep=') for a in sys.argv[1:])   # (the admin page's choice: only what's chosen, never the rest)
KEEP = set(x for a in sys.argv[1:] if a.startswith('keep=') for x in a[5:].split(',') if re.match(r'^[0-9a-zA-Z]{1,40}$', x))
BASE = os.environ.get('GRIFFLIX_MEDIA') or '/Volumes/G-Drive/Grifflix'
MOVIES = os.path.join(BASE, 'Movies')
ASIDE = os.path.join(BASE, 'Set aside', 'Duplicates')
APP = os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix')
EXTRAS = ('other', 'extras', 'featurettes', 'behind the scenes', 'deleted scenes', 'interviews', 'trailers', 'shorts')
VIDEO = ('.mp4', '.mkv', '.m4v', '.mov', '.avi', '.ts', '.wmv', '.mpg', '.mpeg', '.webm')
S = json.load(open(os.path.join(APP, 'settings.json')))


def jf(method, path, **q):
    u = S.get('jellyfin', 'http://127.0.0.1:8096') + path + ('?' + urllib.parse.urlencode(q) if q else '')
    raw = urllib.request.urlopen(urllib.request.Request(u, method=method, headers={'Authorization': 'MediaBrowser Token="%s"' % S['jf_token']}), timeout=300).read()
    return json.loads(raw) if raw[:1] in (b'{', b'[') else None


def g(d, k):
    return d.get(k, d.get(k[:1].lower() + k[1:]))


NUM = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split()


def key(t):
    t = unicodedata.normalize('NFKD', (t or '').replace('&', ' and ')).encode('ascii', 'ignore').decode().lower()   # ("Secrets & Lies" = "Secrets and Lies": 18 Nov 2026)
    t = re.sub(r'\b(\d{1,2})\b', lambda m: NUM[int(m.group(1))] if int(m.group(1)) <= 20 else m.group(1), t)   # (Number 17 = Number Seventeen)
    t = re.sub(r'[^a-z0-9]', '', t)
    return t[3:] if t.startswith('the') and len(t) > 7 else t


def has_film(folder):
    for d, dirs, files in os.walk(folder):
        dirs[:] = [x for x in dirs if x.lower() not in EXTRAS and not x.startswith('.')]
        if any(f.lower().endswith(VIDEO) and not f.startswith('.') for f in files):
            return True
    return False


def films():
    out, start = [], 0
    lid = (S.get('libraries') or {}).get('movies')
    while lid:
        r = jf('GET', '/Users/%s/Items' % S['jf_user_id'], ParentId=lid, Recursive='true', IncludeItemTypes='Movie',
               Fields='Path,MediaSources,ProductionYear', StartIndex=start, Limit=500)
        for it in g(r, 'Items') or []:
            p = g(it, 'Path') or ''
            rel = os.path.relpath(p, MOVIES)
            parts = rel.split(os.sep)
            if rel.startswith('..') or len(parts) < 2 or (len(parts) > 2 and parts[1].lower() in EXTRAS) or '{edition-' in p.lower():
                continue
            ms = (g(it, 'MediaSources') or [{}])[0]
            v = next((s for s in g(ms, 'MediaStreams') or [] if g(s, 'Type') == 'Video'), {})
            out.append({'id': g(it, 'Id'), 'name': g(it, 'Name') or '', 'year': g(it, 'ProductionYear'), 'path': p, 'folder': parts[0],
                        'h': g(v, 'Height') or 0, 'codec': g(v, 'Codec') or '?', 'bits': g(v, 'BitDepth') or 8,
                        'mbps': round((g(ms, 'Bitrate') or 0) / 1e6, 1), 'gb': round((g(ms, 'Size') or 0) / 1e9, 2),
                        'mins': round((g(ms, 'RunTimeTicks') or 0) / 6e8)})
        start += 500
        if start >= (g(r, 'TotalRecordCount') or 0):
            break
    return out


def score(f):
    cls = 3 if f['h'] >= 1000 else 2 if f['h'] >= 700 else 1 if f['h'] >= 500 else 0
    return (cls, f['mbps'], f['gb'])


def desc(f):
    return '%s  (%dp %s%s, %.1f GB, %s Mbps, %s min)' % (os.path.relpath(f['path'], MOVIES), f['h'], f['codec'], ' 10-bit' if f['bits'] > 8 else '', f['gb'], f['mbps'], f['mins'])


def aside(path):   # a file (and its own subtitles and pictures, which share its name) to Set aside/Duplicates/<its folder>/
    folder, base = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    dest = os.path.join(ASIDE, os.path.relpath(folder, MOVIES))
    os.makedirs(dest, exist_ok=True)
    for n in os.listdir(folder):
        if n == os.path.basename(path) or (n.startswith(base + '.') or n.startswith(base + '-')) and not n.lower().endswith(VIDEO):
            shutil.move(os.path.join(folder, n), os.path.join(dest, n))


def find():
    F = films()
    groups = collections.defaultdict(list)
    for f in F:
        groups[(key(f['name']), f['year'])].append(f)
    same, wrong = [], []
    for k, v in sorted(groups.items(), key=lambda kv: kv[1][0]['name']):
        if len(v) < 2:
            continue
        title = lambda f: key(re.sub(r'\s*\((19|20)\d\d\)\s*$', '', f['folder']))
        lengths = [f['mins'] for f in v if f['mins']]
        odd = [f for f in v if difflib.SequenceMatcher(None, title(f), k[0]).ratio() < .9]   # (its folder names another film)
        if odd or (lengths and max(lengths) - min(lengths) > 8):
            wrong.append(v)
        else:
            same.append(sorted(v, key=score, reverse=True))
    return F, same, wrong


def main():
    if not os.path.isdir(MOVIES):
        sys.exit('No %s: is the G-Drive plugged in?' % MOVIES)
    F, same, wrong = find()
    if JSON:
        cp = lambda f, best: dict({k: f[k] for k in ('id', 'name', 'year', 'folder', 'h', 'codec', 'bits', 'mbps', 'gb', 'mins')}, file=os.path.relpath(f['path'], MOVIES), best=best)
        print(json.dumps({'films': len(F), 'groups': [[cp(f, i == 0) for i, f in enumerate(v)] for v in same],
                          'wrong': [[{'name': f['name'], 'year': f['year'], 'folder': f['folder'], 'mins': f['mins']} for f in v] for v in wrong]}))
        return
    if CHOSEN:   # (John's choice on the admin page: in each group he chose, the copy he chose kept)
        chosen = []
        for v in same:
            k = next((f for f in v if f['id'] in KEEP), None)
            if k:
                chosen.append([k] + [f for f in v if f is not k])
        same = chosen
    print('%d films; %d with more than one copy.\n' % (len(F), len(same)))
    moves = {}
    for v in same:
        keep, drop = v[0], v[1:]
        print('%s (%s)\n  keep  %s' % (keep['name'], keep['year'], desc(keep)))
        for d in drop:
            print('  aside %s' % desc(d))
            moves[d['id']] = keep['id']
    if wrong:
        print('\nNot copies: the media server has these as one film, but they look like different films (the folder or the length says so).'
              ' Put each right with Fix match on its page; nothing here is moved:')
        for v in wrong:
            print('  %s (%s): %s' % (v[0]['name'], v[0]['year'], '; '.join('%s, %s min' % (f['folder'], f['mins']) for f in v)))
    if not same:
        return
    if not GO:
        return print('\nRun with go to set them aside (in "%s").' % ASIDE)
    for v in same:
        keep = v[0]
        for d in v[1:]:
            try:
                aside(d['path'])
                folder = os.path.join(MOVIES, d['folder'])
                if d['folder'] != keep['folder'] and os.path.isdir(folder) and not has_film(folder):
                    kf = os.path.join(MOVIES, keep['folder'])
                    for n in os.listdir(folder):   # (its extras to the kept copy's folder, if that has none)
                        if n.lower() in EXTRAS and os.path.isdir(os.path.join(folder, n)) and not any(x.lower() in EXTRAS for x in os.listdir(kf)):
                            if [x for x in os.listdir(os.path.join(folder, n)) if not x.startswith('.') and not x.endswith('.txt')]:
                                shutil.move(os.path.join(folder, n), os.path.join(kf, n))
                    dest = os.path.join(ASIDE, d['folder'])
                    os.makedirs(dest, exist_ok=True)
                    for n in os.listdir(folder):
                        shutil.move(os.path.join(folder, n), os.path.join(dest, n))
                    os.rmdir(folder)
                print('Set aside: %s' % os.path.relpath(d['path'], MOVIES))
            except Exception as e:
                print('Couldn’t set aside %s: %s' % (d['path'], e))
                moves.pop(d['id'], None)
    try:
        jf('POST', '/Library/Refresh')
        urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%s/local/remap' % S.get('port', 8097), data=json.dumps({'map': moves}).encode(),
                               method='POST', headers={'X-Grifflix-Secret': S['secret'], 'Content-Type': 'application/json'}), timeout=60).read()
        print('\nGrifflix follows the kept copies (calendars, lists, hearts); the media server is looking again.')
    except Exception as e:
        print('\n(Grifflix wasn’t told: %s. It catches up at its next look.)' % e)


if __name__ == '__main__':
    main()
