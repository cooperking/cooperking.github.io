"""BFI Player's own picture for every film in Grifflix's BFI collection, kept with the site (grifflix/img/bfi/<id>.jpg), as BFI Player's own
page for the film has it (John, 26 Nov 2026: "Can each film's background and picture be the same as the site"). The BFI page's grid and
each BFI film's own page use it. Run by the Grifflix data workflow after each Update, as the A24 stills are: a film newly in the BFI
collection gets its picture the same day.

  python3 _grifflix/bfi_stills.py

Each film looked for on BFI Player: first among the films its Kermode Introduces page lists (each with its page and its picture), else at
the address BFI Player gives a film's page, made from its title and year (subscription, rentals or free; the year, or one either way),
taken only when that page's own title is the film's. The picture is its 1200×675 one. One already here is left; one not found is tried
again after a week. grifflix/img/bfi/index.json: {"have": {id: its page}, "miss": {id: when last tried}, "pre": {TMDB number: {page, title,
year}}, "premiss": {TMDB number: when last tried}}.

And ahead: the films Mark Kermode introduces that John has named from TMDB but hasn't yet (grifflix/data/bfiwant.json, from the Mac mini),
each picture got now, while BFI Player has the film (its films come and go), kept as grifflix/img/bfi/pre/<TMDB number>.jpg, and used as the
film's own the day it comes into the BFI collection: John, 26 Nov 2026, "can we prefetch any confirmed films on the list's images".
"""
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'grifflix', 'img', 'bfi')
BASE = 'https://player.bfi.org.uk'
LIST = BASE + '/subscription/kermode-introduces'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
# (a film BFI Player has by another name, or as one of its parts: its page there, as John found it, 26 Nov 2026)
PAGES = {'shoah': BASE + '/subscription/film/watch-shoah-first-era-1985-online',
         'lovers on the bridge': BASE + '/subscription/film/watch-les-amants-du-pont-neuf-1991-online',
         'one false move': BASE + '/subscription/film/watch-one-false-move-1990-online',
         'celebration': BASE + '/subscription/film/watch-festen-1998-online',
         'mulholland drive': BASE + '/rentals/film/watch-mulholland-dr-2001-online'}
ROMAN = {'ii': 2, 'iii': 3, 'iv': 4, 'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'xi': 11, 'xii': 12, 'xiii': 13, 'xiv': 14, 'xv': 15, 'xvi': 16, 'xvii': 17, 'xviii': 18, 'xix': 19, 'xx': 20}


def fold(x):
    x = unicodedata.normalize('NFD', html.unescape(str(x or '')).lower())
    x = ''.join(c for c in x if not unicodedata.combining(c))
    x = re.sub(r"['’‘`´\"“”]", '', x).replace('&', ' and ')
    x = re.sub(r'\b(xviii|viii|xiii|xvii|iii|vii|xii|xiv|xvi|xix|ii|iv|vi|ix|xi|xv|xx)\b', lambda m: str(ROMAN[m.group(1)]), x)
    x = re.sub(r'^(the|a|an) ', '', x)
    return re.sub(r'[^a-z0-9]+', ' ', x).strip()


def slug(t):   # (as BFI Player makes a page's address: "Out of the Blue" is out-of-the-blue)
    t = unicodedata.normalize('NFD', html.unescape(str(t or '')).lower())
    t = ''.join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"['’‘`´]", '', t).replace('&', 'and')
    return re.sub(r'[^a-z0-9]+', '-', t).strip('-')


def get(url, binary=False):
    time.sleep(.6)   # (gently: one at a time, a little apart)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en-GB,en;q=0.9'})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        return data if binary else data.decode('utf-8', 'replace')


def cards(page):
    """The films a BFI Player page lists: [(title, year, page, picture)]."""
    out = []
    for m in re.finditer(r'<a\s+data-component-id="nuplayer:film_card"\s+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
        href, body = m.group(1), m.group(2)
        im = re.search(r'data-img-1200="([^"]+)"', body)
        t = re.search(r'field--name-title[^>]*>([^<]+)<', body)
        y = re.search(r'-(\d{4})-online', href)
        if t and im:
            out.append((html.unescape(t.group(1).strip()), int(y.group(1)) if y else 0, BASE + href, BASE + html.unescape(im.group(1))))
    return out


def given_page(url):
    """A page John named for a film: its picture, whatever its title there."""
    try:
        p = get(url)
    except Exception:
        return None, None
    og = re.search(r'<meta property="og:image" content="([^"]+)"', p)
    return (url, html.unescape(og.group(1))) if og else (None, None)


def own_page(name, year):
    """The film's own page at BFI Player, by the address it would have, if its title there is the film's: (page, picture)."""
    for sect in ('subscription', 'rentals', 'free'):
        for y in (year, year - 1, year + 1) if year else (0,):
            url = '%s/%s/film/watch-%s%s-online' % (BASE, sect, slug(name), '-%d' % y if y else '')
            try:
                p = get(url)
            except urllib.error.HTTPError:
                continue
            except Exception:
                continue
            h1 = re.search(r'<h1[^>]*>(.*?)</h1>', p, re.S)
            og = re.search(r'<meta property="og:image" content="([^"]+)"', p)
            if h1 and og and fold(re.sub(r'<[^>]+>', '', h1.group(1))) == fold(name):
                return url, html.unescape(og.group(1))
    return None, None


def save(data, to):
    """Kept as the A24 stills are: 1200 across at most, JPEG at 82, so the BFI page's grid comes quickly (as BFI Player sends it, a picture is
    half a megabyte or more)."""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert('RGB')
        if im.width > 1200:
            im = im.resize((1200, round(im.height * 1200 / im.width)), Image.LANCZOS)
        im.save(to, 'JPEG', quality=82, optimize=True, progressive=True)
    except ImportError:
        open(to, 'wb').write(data)


def shrink_all():   # (those kept before this made them smaller)
    import importlib.util
    if not importlib.util.find_spec('PIL'):
        return
    for n in os.listdir(OUT):
        p = os.path.join(OUT, n)
        if n.endswith('.jpg') and os.path.getsize(p) > 400000:
            save(open(p, 'rb').read(), p)


def find(L, name, year):
    """A film's page and picture on BFI Player: among the Kermode Introduces page's, else at the page John named for it, else at its own
    address: (page, picture), or (None, None)."""
    c = next((x for x in L if fold(x[0]) == fold(name) and (not year or not x[1] or abs(x[1] - year) <= 1)), None)
    if c:
        return c[2], c[3]
    if fold(name) in PAGES:
        return given_page(PAGES[fold(name)])
    return own_page(name, year)


def fetch(pic, to):
    data = get(pic, binary=True)
    if len(data) < 5000 or data[:2] != b'\xff\xd8':
        raise ValueError('not a picture')
    save(data, to)


def main():
    lib = json.load(open(os.path.join(ROOT, 'grifflix', 'data', 'library.json')))
    bfi = next((c for c in lib.get('collections') or [] if (lib['items'].get(c) or {}).get('name', '').lower() == 'bfi'), None)
    films = [lib['items'][i] for i in lib['sets'].get(bfi) or [] if i in lib['items']] if bfi else []
    try:
        want = json.load(open(os.path.join(ROOT, 'grifflix', 'data', 'bfiwant.json'))).get('films') or []
    except (OSError, ValueError):
        want = []
    os.makedirs(os.path.join(OUT, 'pre'), exist_ok=True)
    ix_f = os.path.join(OUT, 'index.json')
    try:
        ix = json.load(open(ix_f))
    except (OSError, ValueError):
        ix = {}
    have, miss, pre, premiss = ix.get('have') or {}, ix.get('miss') or {}, ix.get('pre') or {}, ix.get('premiss') or {}
    keep = lambda: json.dump({'have': have, 'miss': miss, 'pre': pre, 'premiss': premiss}, open(ix_f, 'w'), indent=0, sort_keys=True)
    shrink_all()
    # (a film newly in the BFI collection whose picture was got ahead: that one, at once)
    used = []
    for f in films:
        if f['id'] in have and os.path.exists(os.path.join(OUT, f['id'] + '.jpg')):
            continue
        y = int(f.get('year') or 0)
        t = next((k for k, v in pre.items() if fold(v.get('title')) == fold(f.get('name')) and (not y or not v.get('year') or abs(int(v['year']) - y) <= 1)
                  and os.path.exists(os.path.join(OUT, 'pre', k + '.jpg'))), None)
        if t:
            os.replace(os.path.join(OUT, 'pre', t + '.jpg'), os.path.join(OUT, f['id'] + '.jpg'))
            have[f['id']] = pre.pop(t).get('page') or ''
            miss.pop(f['id'], None)
            used.append(f.get('name') or '')
    if used:
        print('BFI pictures got ahead, now used: %s' % ', '.join(used)[:600])
    todo = [f for f in films if not (f['id'] in have and os.path.exists(os.path.join(OUT, f['id'] + '.jpg')))
            and (time.time() - (miss.get(f['id']) or 0) > 7 * 86400 or fold(f.get('name')) in PAGES)]
    names = set(fold(f.get('name')) for f in films)
    ahead = [w for w in want if w.get('tmdb') and w.get('title') and fold(w['title']) not in names
             and not (str(w['tmdb']) in pre and os.path.exists(os.path.join(OUT, 'pre', str(w['tmdb']) + '.jpg')))
             and time.time() - (premiss.get(str(w['tmdb'])) or 0) > 7 * 86400]
    if not todo and not ahead:
        keep()
        print('BFI pictures: none to get (%d here, %d got ahead)' % (len(have), len(pre)))
        return 0
    try:
        L = cards(get(LIST))
    except Exception as e:
        keep()
        print('BFI Player didn’t answer: %s' % e)
        return 0
    print('BFI Player lists %d films; %d of Grifflix’s BFI films to find, %d to get ahead' % (len(L), len(todo), len(ahead)))
    got, none = [], []
    for f in todo:
        name, year = f.get('name') or '', int(f.get('year') or 0)
        page, pic = find(L, name, year)
        if not pic:
            miss[f['id']] = int(time.time())
            none.append(name)
            continue
        try:
            fetch(pic, os.path.join(OUT, f['id'] + '.jpg'))
            have[f['id']] = page
            miss.pop(f['id'], None)
            got.append(name)
        except Exception as e:
            miss[f['id']] = int(time.time())
            none.append('%s (%s)' % (name, e))
    aget, anone = [], []
    for w in ahead:   # (the films John has named from TMDB, not here yet: their pictures now, while BFI Player has them)
        t, name, year = str(w['tmdb']), w['title'], int(w.get('year') or 0) if str(w.get('year') or '').isdigit() else 0
        page, pic = find(L, name, year)
        if not pic:
            premiss[t] = int(time.time())
            anone.append(name)
            continue
        try:
            fetch(pic, os.path.join(OUT, 'pre', t + '.jpg'))
            pre[t] = {'page': page, 'title': name, 'year': year or ''}
            premiss.pop(t, None)
            aget.append(name)
        except Exception as e:
            premiss[t] = int(time.time())
            anone.append('%s (%s)' % (name, e))
    keep()
    print('BFI pictures: %d got (%s); %d not on BFI Player (%s)' % (len(got), ', '.join(got)[:600], len(none), ', '.join(none)[:600]))
    if ahead:
        print('BFI pictures got ahead: %d (%s); %d not on BFI Player (%s)' % (len(aget), ', '.join(aget)[:600], len(anone), ', '.join(anone)[:600]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
