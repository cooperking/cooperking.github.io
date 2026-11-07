#!/usr/bin/env python3
"""Builds the Grifflix page from its source, as Song of the Day's page.py does for the songs.

  python3 _grifflix/web/page.py          the draft: grifflix/draft/index.html (John tries it there)
  python3 _grifflix/web/page.py --live   the live page: grifflix/index.html (golive: John's Publish)
  python3 _grifflix/web/page.py --check  whether both are what the template builds (exit 1 if not)

Change the page in template.html only, never in the built files. The two copies differ only in what BUILD says
(draft or not), so what John tries on the draft is exactly what goes live. The draft shows a DRAFT tag in its bar.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(HERE, 'template.html')
OUT = {'draft': os.path.join(ROOT, 'grifflix', 'draft', 'index.html'), 'live': os.path.join(ROOT, 'grifflix', 'index.html')}


def build(which):
    s = open(SRC, encoding='utf-8').read()
    marker = "/*BUILD*/{ draft: false, at: '' }"
    assert s.count(marker) == 1, 'template.html: the BUILD line is missing'
    return s.replace(marker, "/*BUILD*/{ draft: %s }" % ('true' if which == 'draft' else 'false'))


def main():
    args = sys.argv[1:]
    if '--check' in args:
        bad = [w for w, p in OUT.items() if not os.path.exists(p) or open(p, encoding='utf-8').read() != build(w)]
        print('draft: %s, live: %s' % tuple('as built' if w not in bad else 'differs' for w in ('draft', 'live')))
        sys.exit(1 if 'draft' in bad else 0)   # (live differing from the template is normal: changes waiting to be published)
    which = 'live' if '--live' in args else 'draft'
    os.makedirs(os.path.dirname(OUT[which]), exist_ok=True)
    with open(OUT[which], 'w', encoding='utf-8') as f:
        f.write(build(which))
    print('built', os.path.relpath(OUT[which], ROOT))


if __name__ == '__main__':
    main()
