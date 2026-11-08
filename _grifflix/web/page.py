#!/usr/bin/env python3
"""Builds the Grifflix page from its source, as Song of the Day's page.py does for the songs.

  python3 _grifflix/web/page.py          the draft: grifflix/draft/index.html (John tries it there)
  python3 _grifflix/web/page.py --live   the live page: grifflix/index.html (golive: John's Publish)
  python3 _grifflix/web/page.py --check  whether both are what the template builds (exit 1 if not)

Change the page in template.html only, never in the built files. The two copies differ only in what BUILD says
(draft or not), so what John tries on the draft is exactly what goes live. The draft shows a DRAFT tag in its bar.
"""
import hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(HERE, 'template.html')
ADMIN = os.path.join(ROOT, 'grifflix', 'admin.html')
OUT = {'draft': os.path.join(ROOT, 'grifflix', 'draft', 'index.html'), 'live': os.path.join(ROOT, 'grifflix', 'index.html')}


def build(which):
    s = open(SRC, encoding='utf-8').read()
    marker = "/*BUILD*/{ draft: false, at: '' }"
    assert s.count(marker) == 1, 'template.html: the BUILD line is missing'
    s = s.replace(marker, "/*BUILD*/{ draft: %s }" % ('true' if which == 'draft' else 'false'))
    art = os.path.join(HERE, 'collection-art.js')   # (the covers' file by what's in it, so a new drawing is fetched fresh)
    v = hashlib.sha1(open(art, 'rb').read()).hexdigest()[:10] if os.path.exists(art) else '0'
    return s.replace('/*ARTV*/', v)


def theme_list():
    """The monthly themes for the admin page's months card: THEMES (the usual order) then THEMES_LATER (parked)."""
    s = open(SRC, encoding='utf-8').read()
    out = []
    for name in ('var THEMES = [', 'var THEMES_LATER = ['):
        i = s.index(name)
        body = s[i:s.index('\n];', i)]
        for m in re.finditer(r"\{ key: '(\w+)'.*?name: '((?:[^'\\]|\\.)*)', film: '((?:[^'\\]|\\.)*)', year: (\d+).*?bg: '(#[0-9a-f]{6})', ink: '(#\w+)'", body):
            k, n, f, y, bg, ink = m.groups()
            out.append({'k': k, 'n': n.replace("\\'", "'"), 'f': f.replace("\\'", "’"), 'y': int(y), 'bg': bg, 'd': 1 if ink == '#fff' else 0})
    return out


def admin():
    a = open(ADMIN, encoding='utf-8').read()
    new = re.sub(r'/\*THEMELIST\*/.*?/\*THEMELIST END\*/', lambda m: '/*THEMELIST*/' + json.dumps(theme_list(), ensure_ascii=False, separators=(',', ':')) + '/*THEMELIST END*/', a, count=1, flags=re.S)
    if new != a:
        open(ADMIN, 'w', encoding='utf-8').write(new)


def art():
    """The collections' drawn covers, a file of their own beside the page (fetched once the page is up: 150KB)."""
    src = os.path.join(HERE, 'collection-art.js')
    out = os.path.join(ROOT, 'grifflix', 'collection-art.js')
    t = open(src, encoding='utf-8').read()
    if not os.path.exists(out) or open(out, encoding='utf-8').read() != t:
        open(out, 'w', encoding='utf-8').write(t)


def main():
    args = sys.argv[1:]
    if '--check' in args:
        bad = [w for w, p in OUT.items() if not os.path.exists(p) or open(p, encoding='utf-8').read() != build(w)]
        print('draft: %s, live: %s' % tuple('as built' if w not in bad else 'differs' for w in ('draft', 'live')))
        sys.exit(1 if 'draft' in bad else 0)   # (live differing from the template is normal: changes waiting to be published)
    which = 'live' if '--live' in args else 'draft'
    admin()
    art()
    os.makedirs(os.path.dirname(OUT[which]), exist_ok=True)
    with open(OUT[which], 'w', encoding='utf-8') as f:
        f.write(build(which))
    print('built', os.path.relpath(OUT[which], ROOT))


if __name__ == '__main__':
    main()
