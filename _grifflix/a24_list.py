"""A24's own list of its films (a24films.com/films, "All films"), kept with the site as grifflix/a24-list.json, read afresh each week
(John, 19 Nov 2026: a weekly refresh, so a new A24 film is recognised and put in the A24 collection without anyone doing anything).

  python3 _grifflix/a24_list.py

Each film: its title (t), year (y), directors (d), A24's status (s), its address on A24's site (u) and the still A24 shows for it (img).
A list that comes back much shorter than the one kept (A24's page changed, or didn't load) is not saved: the old one stays.

Read from A24's own data, the list its films page loads as it's scrolled (api.a24films.com), every page of it: the page read by scrolling
stopped short or skipped films (145 on 19 Nov 2026 where A24 lists 188), and its stills were sometimes the film before's. The page itself
is read only if A24's data can't be (John, 23 Nov 2026: "all films from the list need adding, and any missing added to the missing list").
"""
import json
import os
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'grifflix', 'a24-list.json')

READ = r"""() => {
  const head = [...document.querySelectorAll('h1,h2,h3')].find(e => /^all films$/i.test((e.textContent || '').trim()));
  const box = head ? (head.closest('section') || head.parentElement.parentElement) : document;
  const rows = [...box.querySelectorAll('a[href^="/films/"]')].filter(a => a.querySelector('[class*="title"]'));
  let year = '';
  return rows.map(a => {
    let el = a, y = '';
    for (let k = 0; k < 8 && el && !y; k++, el = el.parentElement) {
      if (el.querySelectorAll('a[href^="/films/"]').length !== 1) continue;
      const m = [...el.querySelectorAll('*')].map(x => x.childElementCount === 0 ? x.textContent.trim() : '').find(t => /^(19|20)\d\d$/.test(t));
      if (m) y = m;
    }
    if (y) year = y;
    const tt = a.querySelector('[class*="title"]');
    const hidden = tt && tt.querySelector('[style*="clip-path"]');
    const st = a.querySelector('[class*="status"]');
    return { u: (a.getAttribute('href') || '').split('/').pop(), t: ((hidden && hidden.textContent) || (tt && tt.textContent) || '').trim(), y: +(y || year),
      d: [...a.querySelectorAll('[class*="directors"] span')].map(s => s.textContent.trim()).filter(Boolean),
      s: st ? st.innerText.trim().toLowerCase().replace(/\b\w/g, c => c.toUpperCase()) : '' };
  });
}"""

STILL = r"""() => {
  const L = [...document.querySelectorAll('img')].filter(i => { const b = i.getBoundingClientRect(); return b.width > 400 && b.left > 500 && b.bottom > 0 && b.top < innerHeight && getComputedStyle(i).opacity !== '0'; });
  const i = L[L.length - 1]; if (!i) return '';
  const m = (i.currentSrc || i.src).match(/url=([^&]+)/); return m ? decodeURIComponent(m[1]) : '';
}"""


API = 'https://api.a24films.com/api/web/v1/films/?sort=-release_date&page=%d'
MONTHS = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()


def status(f):
    """A24's word for where a film is, as its page shows it: Coming Jan 29, Now Playing, Rent Or Buy."""
    ph = (f.get('live_phase') or '').lower()
    if ph == 'trailer':
        if f.get('release_date_display_override'):
            return 'Coming ' + f['release_date_display_override']
        d = (f.get('release_date') or '')[:10]
        return 'Coming %s %d' % (MONTHS[int(d[5:7]) - 1], int(d[8:10])) if len(d) == 10 else 'Coming Soon'
    return 'Now Playing' if ph == 'theatrical' else 'Rent Or Buy'


def from_api():
    """Every film in A24's own data, page after page, as its films page has them."""
    import urllib.request
    out, n = [], 1
    while n < 40:
        req = urllib.request.Request(API % n, headers={'User-Agent': 'Mozilla/5.0 (Grifflix)', 'Accept': 'application/json', 'Origin': 'https://a24films.com'})
        d = json.loads(urllib.request.urlopen(req, timeout=60).read().decode('utf-8'))
        for f in d.get('results') or []:
            if (f.get('title_type') or 'film') != 'film' or not f.get('slug') or not f.get('display_name'):
                continue
            art = f.get('static_art') if isinstance(f.get('static_art'), dict) else {}
            out.append({'t': f['display_name'].strip(), 'y': int(f.get('year') or (f.get('release_date') or '0')[:4] or 0), 'd': list(f.get('directors') or []),
                        's': status(f), 'u': f['slug'], 'img': art.get('src') or ''})
        if not d.get('next'):
            break
        n += 1
    return out


def main():
    old = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {'films': []}
    oldimg = {f.get('u'): f.get('img') for f in old.get('films') or [] if f.get('img')}
    try:
        films = from_api()
        for f in films:
            if not f['img'] and oldimg.get(f['u']):
                f['img'] = oldimg[f['u']]
            if not f['img']:
                del f['img']
        print('A24’s own data: %d films' % len(films))
    except Exception as e:
        print('A24’s own data couldn’t be read (%s): its page read instead' % e)
        films = from_page(oldimg)
    return save(old, films)


def from_page(oldimg):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        px = os.environ.get('HTTPS_PROXY') if os.environ.get('A24_PROXY') else None   # (a test here: through its proxy)
        b = pw.chromium.launch(executable_path=os.environ.get('A24_CHROMIUM') or None, proxy={'server': px} if px else None)
        p = b.new_page(viewport={'width': 1440, 'height': 900}, ignore_https_errors=bool(px))
        p.goto('https://a24films.com/films', wait_until='domcontentloaded', timeout=90000)
        p.wait_for_timeout(6000)
        n = same = 0
        for _ in range(200):   # (the list loads as it's scrolled: on to its end)
            p.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
            p.wait_for_timeout(600)
            c = p.evaluate("() => document.querySelectorAll('a[href^=\"/films/\"]').length")
            same = same + 1 if c == n else 0
            n = c
            if same >= 8:
                break
        films = [f for f in p.evaluate(READ) if f['t'] and f['u']]
        rows = {}
        for a in p.query_selector_all('a[href^="/films/"]'):
            u = (a.get_attribute('href') or '').split('/')[-1]
            if u and u not in rows:
                rows[u] = a
        for f in films:   # (each one's still: A24 shows it as the film is pointed at)
            a = rows.get(f['u'])
            if not a:
                continue
            try:
                a.scroll_into_view_if_needed()
                a.hover()
                p.wait_for_timeout(350)
                src = p.evaluate(STILL)
            except Exception:
                src = ''
            if src or oldimg.get(f['u']):
                f['img'] = src or oldimg[f['u']]
        b.close()
    return films


def save(old, films):
    have = len(old.get('films') or [])
    if len(films) < max(60, have * .8):
        print('A24’s list came back with %d films (%d kept before): not saved.' % (len(films), have))
        return 1
    seen, out = set(), []
    for f in films:
        if f['u'] in seen:
            continue
        seen.add(f['u'])
        out.append({k: f[k] for k in ('t', 'y', 'd', 's', 'u', 'img') if k in f})
    was = {f.get('u') for f in old.get('films') or []}
    new = [f['t'] for f in out if f['u'] not in was]
    gone = [f.get('t') for f in old.get('films') or [] if f.get('u') not in seen]
    json.dump({'from': 'https://a24films.com/films', 'at': time.strftime('%Y-%m-%d'), 'films': out}, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('A24’s list: %d films%s%s' % (len(out), ('; new: ' + ', '.join(new)) if new else '', ('; no longer listed: ' + ', '.join(gone)) if gone else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
