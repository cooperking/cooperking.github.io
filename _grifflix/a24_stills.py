"""A24's own still for every film in Grifflix's A24 collection, kept with the site (grifflix/img/a24/<id>.jpg), as A24's own list of
its films shows it (John, 19 Nov 2026: "when something is added to A24 or Criterion, can we make sure the images are correct - Legend of
Ochi isn't"). Run by the Grifflix data workflow after each Update: a film newly in the A24 collection gets its still the same day.

  python3 _grifflix/a24_stills.py

grifflix/a24-list.json is A24's list (a24films.com/films): each film's title (t), year (y) and its still (img), matched to Grifflix's
films by title (its "The" and punctuation aside) and year (a year either way). Only wide pictures are kept; one already here is left.
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


def fold(x):
    x = unicodedata.normalize('NFD', str(x or '').lower())
    x = ''.join(c for c in x if not unicodedata.combining(c))
    x = re.sub(r"['’‘`´\"“”]", '', x).replace('&', ' and ')
    x = re.sub(r'^(the|a|an) ', '', x)
    return re.sub(r'[^a-z0-9]+', ' ', x).strip()


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
        f = next((f for f in byname.get(fold(it.get('name')), []) if not it.get('year') or abs(int(f['y']) - int(it['year'])) <= 1), None)
        if not f:
            continue
        url = 'https://a24films.com/_next/image?url=%s&w=1920&q=75' % urllib.parse.quote(f['img'], safe='')
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'image/jpeg'}), timeout=60) as r:
                body = r.read()
            if Image:
                im = Image.open(io.BytesIO(body))
                w, h = im.size
                if w / h < 1.3:
                    print('Not a wide picture, left: %s (%dx%d)' % (it.get('name'), w, h))
                    continue
                im.convert('RGB').save(os.path.join(OUT, iid + '.jpg'), 'JPEG', quality=82, optimize=True, progressive=True)
            else:
                open(os.path.join(OUT, iid + '.jpg'), 'wb').write(body)
            have.add(iid)
            got += 1
            print('A24 still: %s' % it.get('name'))
        except Exception as e:
            print('Couldn’t fetch the A24 still for %s: %s' % (it.get('name'), e))
    if got:
        os.makedirs(OUT, exist_ok=True)
        json.dump(sorted(have), open(idx_path, 'w'))
    print('%d new A24 still(s); %d in all.' % (got, len(have)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
