#!/usr/bin/env python3
"""Whether the draft and the live calendar are exactly what template.html builds (page.py's check), kept in
_sotd/data/pages.json for the admin page's Status tab (John, 28 Oct 2026: "how are we sure that draft and live don't get
out of synch"). Run by song-tags.yml before it saves.

    draft: true   songs/draft/ is what the template builds now (else the draft wasn't rebuilt after a change)
    live:  true   songs/ is what the template builds now (else the draft has changes not yet put live, or the live
                  page was changed some other way: the Status tab tells which from the template's changes since the
                  last "Live" commit)

Written only when one of them changes, so it doesn't make a commit of its own on every run.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import page   # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), 'data', 'pages.json')


def main():
    sw = open(os.path.join(HERE, 'sw.js'), encoding='utf-8').read()
    got = {}
    for draft in (True, False):
        target, html = page.build(draft)
        swp = os.path.join(os.path.dirname(target), 'sw.js')
        same = open(target, encoding='utf-8').read() == html and open(swp, encoding='utf-8').read() == sw
        got['draft' if draft else 'live'] = same
    print('the draft is', 'what the template builds' if got['draft'] else 'NOT what the template builds', '·',
          'the live calendar is', 'the same' if got['live'] else 'different (changes waiting to go live?)')
    try:
        old = json.load(open(OUT, encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        old = None
    if old != got:
        with open(OUT, 'w', encoding='utf-8') as f:
            json.dump(got, f)
            f.write('\n')


if __name__ == '__main__':
    main()
