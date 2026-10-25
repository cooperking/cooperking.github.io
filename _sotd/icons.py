#!/usr/bin/env python3
"""The calendar's app icons in this month's colours, as the calendar itself changes colour each month.

    python3 _sotd/icons.py            this month's (London time), only if the month has changed since last time
    python3 _sotd/icons.py --month 6  that month's (1-12), whatever's there
    python3 _sotd/icons.py --all DIR  all twelve months into DIR, to look at

Draws the sleeve and record icon (JOHN'S Song of the Day, A SONG A DAY) on the month's ground and glows, then writes
songs/favicon-96.png, icon-192.png, icon-512.png, icon-maskable-512.png (the design inside Android's safe circle) and
apple-touch-icon.png, and moves their ?v= on in songs/manifest.webmanifest and both calendars' <link> tags so browsers
fetch them again. The browser tab's icon and an Android phone's installed app follow (Chrome checks now and then, and
may ask before changing it); an iPhone's Home Screen icon is kept as it was added. Run by song-tags.yml every time.
"""
import datetime, os, re, sys
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SONGS = os.path.join(ROOT, 'songs')
sys.path.insert(0, HERE)
from share_pages import PALETTES, hexrgb, font, spaced   # noqa: E402

INK = (255, 250, 245)
STAMP = os.path.join(SONGS, 'icons-month.txt')


def ground(month, w, h):
    """The month's ground and glows, as the calendar's CSS paints them (share_pages.ground, any size)."""
    import numpy as np
    from PIL import Image
    bg, g1, g2, g3, g4 = (np.array(hexrgb(c), float) for c in PALETTES[month])
    img = np.ones((h, w, 3)) * bg
    yy, xx = np.mgrid[0:h, 0:w].astype(float)
    vmax = max(w, h)
    for col, k, rx, ry, cx, cy in [(g3, .7, 34, 28, 8, 10), (g4, .85, 38, 30, 96, 102), (g3, 1, 52, 40, 2, 98),
                                   (g2, .85, 40, 32, 30, 40), (g1, 1, 54, 42, 92, 4)]:
        d = np.sqrt(((xx - cx / 100 * w) / (rx / 100 * vmax)) ** 2 + ((yy - cy / 100 * h) / (ry / 100 * vmax)) ** 2)
        a = (np.clip(1 - d / .72, 0, 1) * k)[..., None]
        img = img * (1 - a) + col * a
    return Image.fromarray(img.clip(0, 255).astype('uint8'), 'RGB')


def icon(month, S=1024, inner=1.0):
    """The icon at S pixels; inner < 1 draws the design smaller in the middle (Android's maskable safe zone)."""
    from PIL import Image, ImageDraw, ImageFilter
    img = ground(month, S, S).convert('RGBA')
    k = S * inner / 512   # the design is laid out on 512
    ox = oy = S * (1 - inner) / 2
    P = lambda v: int(round(v * k))   # noqa: E731
    sx, sy, sw = ox + P(49), oy + P(108), P(285)   # the sleeve: a square, rounded
    rd = P(268)   # the record, half out to the right
    rx, ry = int(sx + sw - rd // 2 + P(8)), int(sy + (sw - rd) // 2)
    # shadows
    sh = Image.new('RGBA', (S, S), (0, 0, 0, 0)); sd = ImageDraw.Draw(sh)
    sd.ellipse((rx + P(4), ry + P(10), rx + rd + P(4), ry + rd + P(10)), fill=(20, 6, 20, 120))
    sd.rounded_rectangle((sx + P(4), sy + P(12), sx + sw + P(4), sy + sw + P(12)), P(8), fill=(20, 6, 20, 140))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(P(14))))
    # the record: black, grooves, a gleam, a label in the month's colour
    rec = Image.new('RGBA', (rd, rd), (0, 0, 0, 0)); r = ImageDraw.Draw(rec)
    r.ellipse((0, 0, rd - 1, rd - 1), fill=(14, 12, 18, 255), outline=(150, 145, 150, 255), width=max(1, P(1.6)))
    for i in range(P(10), rd // 2 - P(48), max(2, P(5))):
        r.ellipse((i, i, rd - 1 - i, rd - 1 - i), outline=(40, 37, 48, 255), width=max(1, P(1)))
    gl = Image.new('L', (rd, rd), 0); gd = ImageDraw.Draw(gl)
    gd.pieslice((0, 0, rd - 1, rd - 1), 300, 330, fill=60); gd.pieslice((0, 0, rd - 1, rd - 1), 120, 150, fill=40)
    rec = Image.composite(Image.new('RGBA', (rd, rd), (255, 240, 220, 255)), rec, Image.eval(gl.filter(ImageFilter.GaussianBlur(P(6))), lambda v: v // 2))
    mask = Image.new('L', (rd, rd), 0); ImageDraw.Draw(mask).ellipse((0, 0, rd - 1, rd - 1), fill=255)
    rec.putalpha(mask)
    lab = int(rd * .36)
    lg = ground(month, lab, lab).convert('RGBA')
    lm = Image.new('L', (lab, lab), 0); ImageDraw.Draw(lm).ellipse((0, 0, lab - 1, lab - 1), fill=255)
    rec.paste(lg, ((rd - lab) // 2, (rd - lab) // 2), lm)
    img.alpha_composite(rec, (rx, ry))
    # the sleeve: the month's colours again, a little darker, rounded
    sl = ground(month, sw, sw).convert('RGBA')
    sm = Image.new('L', (sw, sw), 0); ImageDraw.Draw(sm).rounded_rectangle((0, 0, sw - 1, sw - 1), P(8), fill=255)
    img.paste(sl, (int(sx), int(sy)), sm)
    # the words
    d = ImageDraw.Draw(img)
    x0 = sx + P(24)
    spaced(d, (x0, sy + P(36)), 'JOHN’S', font('HankenGrotesk.ttf', P(18), weight=500), INK, P(2.4))
    d.text((x0 - P(2), sy + P(58)), 'Song of', font=font('Newsreader.ttf', P(55), weight=500, opsz=72), fill=INK)
    d.text((x0 - P(2), sy + P(124)), 'the Day', font=font('Newsreader-Italic.ttf', P(55), weight=400, opsz=72), fill=INK)
    d.line((x0, sy + P(225), sx + sw - P(26), sy + P(225)), fill=INK + (200,), width=max(1, P(1.2)))
    spaced(d, (x0, sy + P(238)), 'A SONG A DAY', font('HankenGrotesk.ttf', P(13), weight=600), INK + (235,), P(2))
    return img.convert('RGB')


def write_set(month):
    from PIL import Image
    big, mask = icon(month, 1024), icon(month, 1024, inner=.8)
    for d in (SONGS, os.path.join(SONGS, 'draft')):   # the live calendar's, and the draft's own copies
        if not os.path.isdir(d):
            continue
        for name, px in (('icon-512.png', 512), ('icon-192.png', 192), ('apple-touch-icon.png', 180), ('favicon-96.png', 96)):
            big.resize((px, px), Image.LANCZOS).save(os.path.join(d, name), optimize=True)
        mask.resize((512, 512), Image.LANCZOS).save(os.path.join(d, 'icon-maskable-512.png'), optimize=True)


def bump(v, month):   # the icons' ?v= in the manifest and both calendars, so they're fetched again; the app's colours
    for path in (os.path.join(SONGS, 'manifest.webmanifest'), os.path.join(SONGS, 'draft', 'manifest.webmanifest'),
                 os.path.join(SONGS, 'index.html'), os.path.join(SONGS, 'draft', 'index.html')):
        if not os.path.exists(path):
            continue
        s = open(path, encoding='utf-8').read()
        t = re.sub(r'((?:favicon-96|icon-192|icon-512|icon-maskable-512|apple-touch-icon)\.png)\?v=[\w-]+', r'\1?v=' + v, s)
        if path.endswith('.webmanifest'):
            t = re.sub(r'"(background_color|theme_color)": "#[0-9a-fA-F]{6}"', lambda m: '"%s": "%s"' % (m.group(1), PALETTES[month][0]), t)
        if t != s:
            open(path, 'w', encoding='utf-8').write(t)


def main(args):
    if '--all' in args:
        out = args[args.index('--all') + 1]
        os.makedirs(out, exist_ok=True)
        for m in range(12):
            icon(m, 512).save(os.path.join(out, 'icon-%02d.png' % (m + 1)))
        return
    now = datetime.datetime.now(ZoneInfo('Europe/London'))
    month = int(args[args.index('--month') + 1]) if '--month' in args else now.month
    tag = '%d-%02d' % (now.year, month)
    if '--month' not in args and os.path.exists(STAMP) and open(STAMP).read().strip() == tag:
        return
    write_set(month - 1)
    bump('m' + tag.replace('-', ''), month - 1)
    open(STAMP, 'w').write(tag + '\n')
    print('icons: %s' % tag)


if __name__ == '__main__':
    main(sys.argv[1:])
