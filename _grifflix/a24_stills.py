"""A24's own still for every film in Grifflix's A24 collection, kept with the site (grifflix/img/a24/<id>.jpg), as A24's own list of
its films shows it (John, 19 Nov 2026: "when something is added to A24 or Criterion, can we make sure the images are correct - Legend of
Ochi isn't"). Run by the Grifflix data workflow after each Update: a film newly in the A24 collection gets its still the same day.

  python3 _grifflix/a24_stills.py

grifflix/a24-list.json is A24's list (a24films.com/films): each film's title (t), year (y) and its still (img), matched to Grifflix's
films by title (its "The" and punctuation aside) and year (a year either way). A wide picture first (from A24's list, else from the film's
own page at A24); with none, A24's own picture all the same, whatever its shape. One already here is left.
"""
import io
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'grifflix', 'img', 'a24')


ROMAN = {'ii': 2, 'iii': 3, 'iv': 4, 'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'xi': 11, 'xii': 12, 'xiii': 13, 'xiv': 14, 'xv': 15, 'xvi': 16, 'xvii': 17, 'xviii': 18, 'xix': 19, 'xx': 20}


def fold(x):
    x = unicodedata.normalize('NFD', str(x or '').lower())
    x = ''.join(c for c in x if not unicodedata.combining(c))
    x = re.sub(r"['’‘`´\"“”]", '', x).replace('&', ' and ')
    x = re.sub(r'\b(xviii|viii|xiii|xvii|iii|vii|xii|xiv|xvi|xix|ii|iv|vi|ix|xi|xv|xx)\b', lambda m: str(ROMAN[m.group(1)]), x)   # (Ne Zha II = Ne Zha 2)
    x = re.sub(r'^(the|a|an) ', '', x)
    return re.sub(r'[^a-z0-9]+', ' ', x).strip()


# (a film John has by another name than A24's list gives it: 1%, A24's Outlaws: John, 26 Nov 2026)
AKA = {'1%': 'Outlaws'}


def page_pictures(slug):
    """The wide pictures on a film's own page at A24 (its data, as the page loads it), the likeliest first: the still from its trailer with
    no words on it, then the pictures of its notes; for a film whose picture in A24's list is its poster or a square (Y2K, Babygirl): John,
    26 Nov 2026, "Recent films added in to A24 don't include the images from the page correctly"."""
    try:
        with urllib.request.urlopen(urllib.request.Request('https://api.a24films.com/api/web/v1/films/%s/' % urllib.parse.quote(slug), headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}), timeout=60) as r:
            d = json.loads(r.read().decode('utf-8'))
    except Exception:
        return []
    out = [((d.get('primary_trailer') or {}).get('static_art') or {}).get('src'), (d.get('static_art') or {}).get('src'), (d.get('mobile_static_art') or {}).get('src'),
           (d.get('promotional_image') or {}).get('src') if isinstance(d.get('promotional_image'), dict) else None]
    for n in (((d.get('module_stream') or {}).get('refs') or {}).get('notes') or {}).values():
        if isinstance(n, dict):
            out.append((n.get('hero_image') or {}).get('src'))
    return [u for u in dict.fromkeys(out) if u and re.search(r'\.(jpe?g|png|webp)$', u, re.I)]


def main():
    lib = json.load(open(os.path.join(ROOT, 'grifflix', 'data', 'library.json'), encoding='utf-8'))
    a24 = json.load(open(os.path.join(ROOT, 'grifflix', 'a24-list.json'), encoding='utf-8'))
    items = lib.get('items') or {}
    coll = next((k for k, v in items.items() if v.get('type') == 'collection' and (v.get('name') or '').strip().lower() == 'a24'), None)
    if not coll:
        print('No A24 collection.')
        return 0
    idx_path = os.path.join(OUT, 'index.json')
    have = set(json.load(open(idx_path)) if os.path.exists(idx_path) else [])
    byname = {}
    for f in a24.get('films') or []:
        if f.get('img'):
            byname.setdefault(fold(f['t']), []).append(f)
    try:
        from PIL import Image
    except ImportError:
        Image = None
    got = 0
    for iid in (lib.get('sets') or {}).get(coll, []):
        it = items.get(iid) or {}
        if iid in have and os.path.exists(os.path.join(OUT, iid + '.jpg')):
            continue
        nm_ = AKA.get(it.get('name'), it.get('name'))
        f = next((f for f in byname.get(fold(nm_), []) if not it.get('year') or nm_ != it.get('name') or abs(int(f['y']) - int(it['year'])) <= 1), None)
        if not f:
            continue
        # (the picture A24's list gives it, and if that's a poster or a square, the wide ones on the film's own page)
        tried, kept = [], False
        for src in [f['img']] + (page_pictures(f['u']) if f.get('u') else []):
            if src in tried:
                continue
            tried.append(src)
            url = 'https://a24films.com/_next/image?url=%s&w=1920&q=75' % urllib.parse.quote(src, safe='')
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'image/jpeg'}), timeout=60) as r:
                    body = r.read()
                if Image:
                    im = Image.open(io.BytesIO(body))
                    w, h = im.size
                    if w / h < 1.3:
                        continue
                    im.convert('RGB').save(os.path.join(OUT, iid + '.jpg'), 'JPEG', quality=82, optimize=True, progressive=True)
                else:
                    if src != f['img']:
                        continue
                    open(os.path.join(OUT, iid + '.jpg'), 'wb').write(body)
                have.add(iid)
                got += 1
                kept = True
                print('A24 still: %s%s' % (it.get('name'), '' if src == f['img'] else ' (from its own page)'))
                break
            except Exception as e:
                print('Couldn’t fetch an A24 picture for %s: %s' % (it.get('name'), e))
        if not kept and f.get('img'):   # (no wide one anywhere at A24: A24's own picture all the same, its poster or square, whatever its
            # shape, shown whole: John, 26 Nov 2026, "A24's picture should be used, no matter the size")
            url = 'https://a24films.com/_next/image?url=%s&w=1920&q=75' % urllib.parse.quote(f['img'], safe='')
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'image/jpeg'}), timeout=60) as r:
                    body = r.read()
                if Image:
                    Image.open(io.BytesIO(body)).convert('RGB').save(os.path.join(OUT, iid + '.jpg'), 'JPEG', quality=82, optimize=True, progressive=True)
                else:
                    open(os.path.join(OUT, iid + '.jpg'), 'wb').write(body)
                have.add(iid)
                got += 1
                print('A24 picture (not wide, shown whole): %s' % it.get('name'))
            except Exception as e:
                print('Couldn’t fetch A24’s picture for %s: %s' % (it.get('name'), e))
    if got:
        os.makedirs(OUT, exist_ok=True)
        json.dump(sorted(have), open(idx_path, 'w'))
    if Image:   # (those not wide, listed: the A24 page's opening cut, full screen, uses only the wide ones)
        tall = []
        for iid in sorted(have):
            try:
                w, h = Image.open(os.path.join(OUT, iid + '.jpg')).size
                if w / h < 1.3:
                    tall.append(iid)
            except Exception:
                pass
        json.dump(tall, open(os.path.join(OUT, 'tall.json'), 'w'))
    print('%d new A24 still(s); %d in all.' % (got, len(have)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
