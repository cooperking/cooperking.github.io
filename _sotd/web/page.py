#!/usr/bin/env python3
"""Builds the calendar page from this folder's template.html and sw.js.

    python3 _sotd/web/page.py           the draft: songs/draft/index.html and songs/draft/sw.js
    python3 _sotd/web/page.py --live    the live calendar: songs/index.html and songs/sw.js (only once John has
                                        signed the draft off)
    python3 _sotd/web/page.py --check   whether songs/ and songs/draft/ are exactly what this folder builds

Edit template.html (the page: its styles, markup and script) and sw.js here, never the built pages in songs/.

The page carries a copy of the song list (the sotd-data script) for its first paint, then fetches the live list from
the Worker. The build keeps each page's copy as it is, and keeps what the repository's jobs keep current in it: the
song count in its description (share_pages.py) and the icons' ?v= (icons.py).
"""
import hashlib, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SONGS = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'songs')

HEAD = """<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="sotd-build" content="{build}">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="{desc}">
<meta name="theme-color" content="#ec135b">
<meta property="og:type" content="website">
<meta property="og:title" content="John’s Song of the Day">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="https://cooperking.github.io/songs/">
<meta property="og:image" content="https://cooperking.github.io/songs/card.jpg">
<meta property="og:image:type" content="image/jpeg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Covers of the latest songs on John’s Song of the Day">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" type="image/png" sizes="96x96" href="/songs/favicon-96.png?v={v}">
<link rel="icon" type="image/png" sizes="192x192" href="/songs/icon-192.png?v={v}">
<link rel="apple-touch-icon" sizes="180x180" href="/songs/apple-touch-icon.png?v={v}">
<link rel="manifest" href="/songs/manifest.webmanifest?v=4">
<meta name="apple-mobile-web-app-title" content="Song of the Day">
<style>body{{margin:0}}[hidden]{{display:none!important}}</style>
"""

DRAFT_FLAG = '''<style>
.draftflag { position: fixed; z-index: 40; left: max(16px, env(safe-area-inset-left, 0px)); bottom: max(16px, env(safe-area-inset-bottom, 0px));
  padding: 6px 12px; border-radius: 999px; background: #1b1020; color: #fffaf5; font: 600 11px/1.2 "Hanken Grotesk", Arial, sans-serif;
  letter-spacing: .14em; text-transform: uppercase; pointer-events: none; box-shadow: 0 6px 18px rgba(14, 6, 18, .3); }
html.big .draftflag { position: static; flex: none; box-shadow: none; }
</style>
<div class="draftflag" aria-hidden="true">Draft</div>
<script>/* on a TV or a computer, in the header beside the months: in the corner it covers a day */
if (document.documentElement.classList.contains('big')) { var df = document.querySelector('.draftflag'), mn = document.querySelector('.hero .monthnav'); if (df && mn) mn.parentNode.insertBefore(df, mn); }</script>
'''

PREVIEW_ONLY = '<meta name="claude:viewport-fit-sides" content="cover">\n'   # for previewing the template alone; not in the site
DATA_RE = re.compile(r'(<script id="sotd-data" type="application/json">)(.*?)(</script>)', re.S)


def kept(path):
    """What the page now in songs/ keeps: its song list, its song count and its icons' ?v=."""
    old = open(path, encoding='utf-8').read()
    return (DATA_RE.search(old).group(2), re.search(r'A song a day, \d+ so far', old).group(0),
            re.search(r'favicon-96\.png\?v=([\w-]+)', old).group(1))


def build(draft):
    target = os.path.join(SONGS, 'draft' if draft else '', 'index.html')
    blob, desc, v = kept(target)
    tpl = open(os.path.join(HERE, 'template.html'), encoding='utf-8').read()
    build = hashlib.sha1((tpl + ('draft' if draft else 'live')).encode()).hexdigest()[:12]   # the page's version: the apps reload when it changes
    assert tpl.count('/*DATA*/') == 1, 'template.html needs /*DATA*/ once'
    tpl = tpl.replace('/*DATA*/', blob).replace(PREVIEW_ONLY, '')
    cut = tpl.index('<div class="glow"')
    html = HEAD.format(desc=desc, v=v, build=build) + tpl[:cut] + '</head>\n<body>\n' + tpl[cut:] + '\n</body>\n</html>\n'
    if draft:   # not indexed, titled and flagged as a draft, its own icons, the live site's data files one level up
        html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n<meta name="robots" content="noindex, nofollow">', 1)
        html = re.sub(r'<title>([^<]*)</title>', lambda m: '<title>Draft · ' + m.group(1) + '</title>', html, count=1)
        for name in ('videos.json', 'years.json', 'moods.json'):
            assert html.count("fetch('" + name + "'") == 1, name
            html = html.replace("fetch('" + name + "'", "fetch('../" + name + "'")
        for name in ('favicon-96.png', 'icon-192.png', 'apple-touch-icon.png', 'manifest.webmanifest'):
            html = html.replace('href="/songs/' + name + '?v=', 'href="/songs/draft/' + name + '?v=')
        html = html.replace('\n</body>', '\n' + DRAFT_FLAG + '</body>', 1)
    return target, html


def main(args):
    sw = open(os.path.join(HERE, 'sw.js'), encoding='utf-8').read()
    if '--check' in args:
        ok = True
        for draft in (True, False):
            target, html = build(draft)
            swp = os.path.join(os.path.dirname(target), 'sw.js')
            for path, want in ((target, html), (swp, sw)):
                same = open(path, encoding='utf-8').read() == want
                ok = ok and same
                print(('same    ' if same else 'DIFFERS ') + os.path.relpath(path, os.path.dirname(SONGS)))
        sys.exit(0 if ok else 1)
    target, html = build('--live' not in args)
    open(target, 'w', encoding='utf-8').write(html)
    open(os.path.join(os.path.dirname(target), 'sw.js'), 'w', encoding='utf-8').write(sw)
    print('wrote', os.path.relpath(target, os.path.dirname(SONGS)), '(%d KB)' % (len(html) // 1024), 'and its sw.js')


if __name__ == '__main__':
    main(sys.argv[1:])
