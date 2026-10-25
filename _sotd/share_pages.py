#!/usr/bin/env python3
"""Writes a small page and a picture for each day's song, so a shared link (WhatsApp, iMessage...) shows
a proper card: the cover with its record, the title, the artist and the date, on that month's colours.

  songs/21-10-2026/index.html   one per day, sends the visitor on to songs/#21-10-2026
  songs/21-10-2026/card.jpg     1200 x 630 picture for link previews
  songs/today/index.html        the latest song, sends the visitor on to songs/#today
  songs/10-2026/index.html      one per month, with card.jpg: that month's covers
  songs/card.jpg                the whole calendar's picture (the site's own link preview uses it)

Days follow the calendar's rule: a song's day is when it was added (London time), and when two
were added on one day the earlier one moves back to the day before.
"""
import hashlib, html, io, json, os, re, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from released import released   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SONGS = os.path.join(HERE, '..', 'songs')
FONTS = os.path.join(HERE, 'fonts')
SITE = 'https://cooperking.github.io/songs/'
SONGS_URL = os.environ.get('SOTD_SONGS_URL', 'https://sotd-playlists.cooperking.workers.dev/songs.json')
# John's own pictures for sharing links, set on songs/members-admin.html (the Worker keeps them): a day with one gets
# it on its card instead of the album cover
OWN_URL = os.environ.get('SOTD_OWN_URL', 'https://sotd-playlists.cooperking.workers.dev/')
COVERS = os.environ.get('SOTD_COVERS')   # a local folder of covers by image id, for testing without the network
LONDON = ZoneInfo('Europe/London')
CARD_VERSION = '1'   # bump to redraw every day's card
GRID_VERSION = '2'   # bump to redraw the month and whole-calendar cards
W, H = 1200, 630
INK = (255, 250, 245)

# the calendar's month colours: ground, then glows (top right, centre, bottom left, bottom right)
PALETTES = [
    ('#0c1445', '#0094de', '#8833ff', '#f700f7', '#009aa8'), ('#652bee', '#3333ff', '#cc33ff', '#3388ff', '#ff2bb8'),
    ('#11970c', '#429e00', '#00a150', '#7d9600', '#009e84'), ('#b613ec', '#fc00e7', '#8833ff', '#ff33aa', '#5533ff'),
    ('#096a42', '#00a336', '#009e91', '#00a30e', '#0097c9'), ('#ec1a13', '#fa5300', '#ff3366', '#db6e00', '#ff3399'),
    ('#1346ec', '#008ff5', '#5533ff', '#0097c9', '#8833ff'), ('#d85a06', '#c47c00', '#ff4a2e', '#b28600', '#ff3344'),
    ('#0b86d3', '#0098b0', '#3370ff', '#009e91', '#3333ff'), ('#ec135b', '#ff3344', '#ff30b3', '#ff4c29', '#fc00e7'),
    ('#09696d', '#009e77', '#0094d4', '#00a150', '#198cff'), ('#ec13c1', '#ff3399', '#dd33ff', '#ff3366', '#aa33ff'),
]


def days(songs):
    out, nxt = [], None
    for s in reversed(songs):
        d = datetime.fromisoformat(s['a'].replace('Z', '+00:00')).astimezone(LONDON).date()
        if nxt and nxt - timedelta(days=1) < d:
            d = nxt - timedelta(days=1)
        out.append((d, s))
        nxt = d
    return list(reversed(out))


def slug(d):
    return f'{d.day:02d}-{d.month:02d}-{d.year}'


def long_date(d):
    return f'{d:%A} {d.day} {d:%B %Y}'


def clean_title(t):
    t = re.sub(r'\s+-\s+.*(remaster|version|mix|edit|mono|stereo|single|live|demo|take|recording|anniversary|bonus).*$', '', t, flags=re.I)
    t = re.sub(r'\s*[(\[](feat|with|ft)\.?[^)\]]*[)\]]', '', t, flags=re.I)
    t = re.sub(r'\s*\[[^\]]*\]', '', t)
    return t.strip() or t


def artists_of(s):
    a = s.get('artists') or [x.strip() for x in s['artist'].split(',')]
    if len(a) <= 2:
        return ' & '.join(a)
    return f'{a[0]}, {a[1]} & {len(a) - 2} more'


# ---------- the picture ----------

def hexrgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def ground(month):
    """The month's ground and glows, drawn the way the calendar's CSS draws them."""
    import numpy as np
    from PIL import Image
    bg, g1, g2, g3, g4 = (np.array(hexrgb(c), float) for c in PALETTES[month])
    img = np.ones((H, W, 3)) * bg
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    vmax = max(W, H)
    # (colour, strength, width, height (vmax), centre x, y (%)), painted bottom layer first
    for col, k, rx, ry, cx, cy in [(g3, .7, 34, 28, 8, 10), (g4, .85, 38, 30, 96, 102), (g3, 1, 52, 40, 2, 98),
                                   (g2, .85, 40, 32, 30, 40), (g1, 1, 54, 42, 92, 4)]:
        d = np.sqrt(((xx - cx / 100 * W) / (rx / 100 * vmax)) ** 2 + ((yy - cy / 100 * H) / (ry / 100 * vmax)) ** 2)
        a = (np.clip(1 - d / .72, 0, 1) * k)[..., None]
        img = img * (1 - a) + col * a
    return Image.fromarray(img.clip(0, 255).astype('uint8'), 'RGB')


def font(name, size, weight=None, opsz=None):
    from PIL import ImageFont
    f = ImageFont.truetype(os.path.join(FONTS, name), size)
    if weight is not None:
        axes = f.get_variation_axes()
        vals = []
        for ax in axes:
            n = ax['name'] if isinstance(ax['name'], str) else ax['name'].decode()
            vals.append(weight if n == 'Weight' else (opsz or size) if n == 'Optical Size' else ax['default'])
        f.set_variation_by_axes(vals)
    return f


def wrap(draw, text, f, width):
    words, lines, line = text.split(), [], ''
    for w in words:
        t = (line + ' ' + w).strip()
        if draw.textlength(t, font=f) <= width or not line:
            line = t
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def spaced(draw, xy, text, f, fill, tracking):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill)
        x += draw.textlength(ch, font=f) + tracking
    return x


def card(d, s, cover):
    from PIL import Image, ImageDraw, ImageFilter
    img = ground(d.month - 1).convert('RGBA')
    size, cx, cy = 400, 70, (H - 400) // 2
    art = Image.open(io.BytesIO(cover)).convert('RGB').resize((size, size), Image.LANCZOS)
    # the record half out of its sleeve, as in the calendar's day view
    rd = size - 16
    rec = Image.new('RGBA', (rd, rd), (0, 0, 0, 0))
    r = ImageDraw.Draw(rec)
    r.ellipse((0, 0, rd - 1, rd - 1), fill=(10, 9, 14, 255))
    for i in range(8, rd // 2 - rd // 5, 3):
        r.ellipse((i, i, rd - 1 - i, rd - 1 - i), outline=(30, 28, 38, 255), width=1)
    lab = int(rd * .38)
    mask = Image.new('L', (lab, lab), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, lab - 1, lab - 1), fill=255)
    rec.paste(art.resize((lab, lab), Image.LANCZOS), ((rd - lab) // 2, (rd - lab) // 2), mask)
    hole = 9
    r.ellipse((rd // 2 - hole // 2, rd // 2 - hole // 2, rd // 2 + hole // 2, rd // 2 + hole // 2), fill=hexrgb(PALETTES[d.month - 1][0]) + (255,))
    shadow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    rx, ry = cx + size - rd // 2 + 12, cy + 8
    sd.ellipse((rx + 6, ry + 14, rx + rd + 6, ry + rd + 14), fill=(20, 6, 20, 110))
    sd.rectangle((cx + 6, cy + 16, cx + size + 6, cy + size + 16), fill=(20, 6, 20, 120))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(18)))
    img.alpha_composite(rec, (rx, ry))
    img.paste(art, (cx, cy))

    dr = ImageDraw.Draw(img)
    x0, x1 = rx + rd + 48, W - 64
    width = x1 - x0
    small = font('HankenGrotesk.ttf', 19, weight=600)
    title = clean_title(s['title'])
    for tsize in (68, 62, 56, 50, 45, 40, 36):
        tf = font('Newsreader.ttf', tsize, weight=500, opsz=72)
        lines = wrap(dr, title, tf, width)
        # no line left with a single short word, unless the title is one word
        widow = len(lines) > 1 and len(lines[-1]) <= 3
        if len(lines) <= 3 and not widow and all(dr.textlength(l, font=tf) <= width for l in lines):
            break
    if len(lines) > 3:
        lines = lines[:3]
        while dr.textlength(lines[2] + '…', font=tf) > width and ' ' in lines[2]:
            lines[2] = lines[2].rsplit(' ', 1)[0]
        lines[2] += '…'
    af = font('HankenGrotesk.ttf', 22, weight=600)
    artist = artists_of(s).upper()
    while dr.textlength(artist, font=af) + len(artist) * 2.4 > width and len(artist) > 8:
        artist = artist[:-2].rstrip() + '…' if not artist.endswith('…') else artist[:-2].rstrip() + '…'
    df = font('Newsreader-Italic.ttf', 30, weight=400, opsz=30)
    lh = int(tsize * 1.08)
    block = 19 + 26 + len(lines) * lh + 22 + 22 + 30 + 30
    y = (H - block) // 2
    spaced(dr, (x0, y), "JOHN'S SONG OF THE DAY", small, INK + (230,), 3.2)
    y += 19 + 26
    for l in lines:
        dr.text((x0, y), l, font=tf, fill=INK)
        y += lh
    y += 22
    spaced(dr, (x0, y), artist, af, INK + (240,), 2.4)
    y += 22 + 30
    dr.text((x0, y), long_date(d), font=df, fill=INK + (235,))
    out = io.BytesIO()
    img.convert('RGB').save(out, 'JPEG', quality=84, optimize=True, progressive=True)
    return out.getvalue()


def own_pictures():
    """{track: when it was set} for the days John has given his own picture (none if the Worker can't be reached)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(OWN_URL + 'covers.json', headers={'User-Agent': 'SongOfTheDayCalendar/1.0'}), timeout=30) as r:
            return {t: e.get('at', '') for t, e in json.loads(r.read()).items()}
    except Exception as ex:
        print('own pictures: could not get the list:', ex)
        return {}


def own_picture(track):
    """John's picture for a day, cut to a square from its middle, as JPEG bytes (None if it can't be had)."""
    from PIL import Image
    try:
        with urllib.request.urlopen(urllib.request.Request(OWN_URL + 'c/' + track, headers={'User-Agent': 'SongOfTheDayCalendar/1.0'}), timeout=60) as r:
            im = Image.open(io.BytesIO(r.read()))
        im = im.convert('RGB')
        side = min(im.size)
        im = im.crop(((im.width - side) // 2, (im.height - side) // 2, (im.width + side) // 2, (im.height + side) // 2))
        out = io.BytesIO()
        im.resize((640, 640), Image.LANCZOS).save(out, 'JPEG', quality=90)
        return out.getvalue()
    except Exception as ex:
        print('own picture for', track, 'could not be had:', ex)
        return None


def cover_bytes(s, small=False):
    url = (s.get('img') or '').replace('ab67616d00001e02', 'ab67616d0000b273')
    if small:
        url = url.replace('ab67616d0000b273', 'ab67616d00001e02')
    if COVERS:
        p = os.path.join(COVERS, url.rsplit('/', 1)[-1][-24:] + '.jpg')
        return open(p, 'rb').read() if os.path.exists(p) else None
    if not url:
        return None
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'SongOfTheDayCalendar/1.0'}), timeout=30) as r:
        return r.read()


GRIDS = [(12, 4, 3), (9, 3, 3), (6, 3, 2), (4, 2, 2), (3, 3, 1), (2, 2, 1), (1, 1, 1)]   # (covers shown, columns, rows): always a full rectangle


def pick(covers, k):
    """k covers spread evenly across the list, first and last included."""
    if len(covers) <= k:
        return covers[:k]
    return [covers[round(i * (len(covers) - 1) / (k - 1))] for i in range(k)] if k > 1 else covers[:1]


def grid_card(month, roman, italic, kicker, foot, covers):
    """A month or the whole calendar: the name on the left (set like the calendar's own heading: roman then
    italic), a full rectangle of covers on the right, the two blocks sharing a top and bottom edge."""
    from PIL import Image, ImageDraw, ImageFilter
    img = ground(month).convert('RGBA')
    k, cols, rows = next(g for g in GRIDS if len(covers) >= g[0])
    covers = pick(covers, k)
    gap, right, top_bot = 14, 64, 72
    cell = min((H - 2 * top_bot - (rows - 1) * gap) // rows, (560 - (cols - 1) * gap) // cols)
    gw, gh = cols * cell + (cols - 1) * gap, rows * cell + (rows - 1) * gap
    ox, oy = W - right - gw, (H - gh) // 2
    shadow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    radius = max(4, cell // 30)
    mask = Image.new('L', (cell, cell), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, cell - 1, cell - 1), radius=radius, fill=255)
    for i in range(len(covers)):
        x, y = ox + (i % cols) * (cell + gap), oy + (i // cols) * (cell + gap)
        sd.rounded_rectangle((x, y + 8, x + cell, y + cell + 8), radius=radius, fill=(20, 6, 20, 80))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(10)))
    for i, c in enumerate(covers):
        x, y = ox + (i % cols) * (cell + gap), oy + (i // cols) * (cell + gap)
        try:
            img.paste(Image.open(io.BytesIO(c)).convert('RGB').resize((cell, cell), Image.LANCZOS), (x, y), mask)
        except Exception:
            ImageDraw.Draw(img).rounded_rectangle((x, y, x + cell, y + cell), radius=radius, fill=(255, 255, 255, 40))

    dr = ImageDraw.Draw(img)
    x0, width = 64, ox - 64 - 48
    small = font('HankenGrotesk.ttf', 18, weight=600)
    for tsize in (112, 100, 90, 80, 72, 64, 56):
        rf = font('Newsreader.ttf', tsize, weight=400, opsz=72)
        itf = font('Newsreader-Italic.ttf', tsize, weight=400, opsz=72)
        if dr.textlength(roman, font=rf) <= width and dr.textlength(italic, font=itf) <= width:
            break
    # kicker on the grid's top edge, foot on its bottom edge, the name centred between
    spaced(dr, (x0, oy - 4), kicker, small, INK + (225,), 3.4)
    fb = dr.textbbox((0, 0), foot, font=small)
    spaced(dr, (x0, oy + gh - (fb[3] - fb[1]) - 2), foot, small, INK + (225,), 3.4)
    lh = int(tsize * 1.0)
    rb = dr.textbbox((0, 0), roman, font=rf)
    ib = dr.textbbox((0, 0), italic, font=itf)
    block = (rb[3] - rb[1]) + (lh - (rb[3] - rb[1])) + (ib[3] - ib[1])
    y = oy + (gh - block) // 2 - rb[1]
    dr.text((x0, y), roman, font=rf, fill=INK)
    dr.text((x0, y + lh), italic, font=itf, fill=INK)
    out = io.BytesIO()
    img.convert('RGB').save(out, 'JPEG', quality=86, optimize=True, progressive=True)
    return out.getvalue()


def refresh_card(path, stamp, make):
    """Draws a card only when what it shows has changed; returns whether it exists."""
    sp = os.path.join(os.path.dirname(path), '.' + os.path.basename(path) + '.stamp')
    if os.path.exists(path) and os.path.exists(sp) and open(sp).read().strip() == stamp:
        return True
    try:
        write(path, make())
        write(sp, stamp + '\n')
        return True
    except Exception as ex:
        print('card failed for', path, ex)
        return os.path.exists(path)


# ---------- the page ----------

# link-preview fetchers (WhatsApp, iMessage, Facebook, Slack...) must stay on this page to read its tags
BOTS = r'bot|crawl|spider|preview|facebookexternalhit|facebot|whatsapp|telegram|slack|discord|linkedin|pinterest|embedly|iframely|skype|viber|signal|vkshare|redditbot|applebot|google|bing'


def redirect(target):   # passes the link's query on (?play=video / ?play=preview says how the song opens)
    base, _, frag = target.partition('#')
    to = json.dumps(base) + ' + location.search' + (' + ' + json.dumps('#' + frag) if frag else '')
    return f"if (!/{BOTS}/i.test(navigator.userAgent)) location.replace({to});"


def plain_page(title, desc, target, url, image, theme, alt):
    e = lambda x: html.escape(x, quote=True)
    img_tags = (f'<meta property="og:image" content="{e(image)}">\n<meta property="og:image:secure_url" content="{e(image)}">\n<meta property="og:image:type" content="image/jpeg">\n'
                f'<meta property="og:image:width" content="{W}">\n<meta property="og:image:height" content="{H}">\n'
                f'<meta property="og:image:alt" content="{e(alt)}">\n<meta name="twitter:image" content="{e(image)}">\n') if image else ''
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · John's Song of the Day</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{e(url)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="John's Song of the Day">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(url)}">
{img_tags}<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="{theme}">
<link rel="icon" type="image/png" sizes="96x96" href="https://cooperking.github.io/songs/favicon-96.png?v=2">
<link rel="icon" type="image/png" sizes="192x192" href="https://cooperking.github.io/songs/icon-192.png?v=2">
<link rel="apple-touch-icon" sizes="180x180" href="https://cooperking.github.io/songs/apple-touch-icon.png?v=2">
<script>{redirect(target)}</script>
</head>
<body style="font-family: Georgia, serif; padding: 24px;">
<p><a href="{e(target)}">{e(title)}</a>: {e(desc)}</p>
</body>
</html>
"""

def page(d, s, target, url, image):
    e = lambda x: html.escape(x, quote=True)
    title = clean_title(s['title'])
    desc = f"{artists_of(s)} · John's Song of the Day, {long_date(d)}"
    img_tags = (f'<meta property="og:image" content="{e(image)}">\n<meta property="og:image:secure_url" content="{e(image)}">\n<meta property="og:image:type" content="image/jpeg">\n'
                f'<meta property="og:image:width" content="{W}">\n<meta property="og:image:height" content="{H}">\n'
                f'<meta property="og:image:alt" content="{e(title)} by {e(s["artist"])}">\n'
                f'<meta name="twitter:image" content="{e(image)}">\n') if image else ''
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · John's Song of the Day</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{e(url)}">
<meta property="og:type" content="music.song">
<meta property="og:site_name" content="John's Song of the Day">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(url)}">
{img_tags}<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(desc)}">
<meta name="theme-color" content="{PALETTES[d.month - 1][0]}">
<link rel="icon" type="image/png" sizes="96x96" href="https://cooperking.github.io/songs/favicon-96.png?v=2">
<link rel="icon" type="image/png" sizes="192x192" href="https://cooperking.github.io/songs/icon-192.png?v=2">
<link rel="apple-touch-icon" sizes="180x180" href="https://cooperking.github.io/songs/apple-touch-icon.png?v=2">
<script>{redirect(target)}</script>
</head>
<body style="font-family: Georgia, serif; padding: 24px;">
<p><a href="{e(target)}">{e(title)} by {e(s['artist'])}</a>, John's Song of the Day for {e(long_date(d))}.</p>
</body>
</html>
"""


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = 'wb' if isinstance(data, bytes) else 'w'
    if os.path.exists(path):
        with open(path, 'rb' if mode == 'wb' else 'r', **({} if mode == 'wb' else {'encoding': 'utf-8'})) as f:
            if f.read() == data:
                return False
    with open(path, mode, **({} if mode == 'wb' else {'encoding': 'utf-8'})) as f:
        f.write(data)
    return True


def main():
    with urllib.request.urlopen(urllib.request.Request(SONGS_URL, headers={'User-Agent': 'SongOfTheDayCalendar/1.0'}), timeout=60) as r:
        songs = json.loads(r.read())['songs']
    out = released()   # days follow the whole playlist; only the songs out on the calendar get pages (released.py)
    dated = [(d, s) for d, s in days(songs) if out is None or s['id'] in out]
    own = {}   # John's own pictures are for members' shares only now (the Worker's /s/ pages): the site's cards keep the album cover
    pages = cards = 0
    for d, s in dated:
        sl = slug(d)
        folder = os.path.join(SONGS, sl)
        # redraw the card only when the song on that day, its cover (or John's own picture) or the design changes
        stamp = hashlib.sha1(f"{CARD_VERSION} {s['id']} {s.get('img')} {s['title']} {s['artist']} {own.get(s['id'], '')}".encode()).hexdigest()[:12]
        stamp_path = os.path.join(folder, '.card')
        has_card = os.path.exists(os.path.join(folder, 'card.jpg'))
        if not has_card or not os.path.exists(stamp_path) or open(stamp_path).read().strip() != stamp:
            try:
                c = (own_picture(s['id']) if s['id'] in own else None) or cover_bytes(s)
                if c:
                    cards += write(os.path.join(folder, 'card.jpg'), card(d, s, c))
                    write(stamp_path, stamp + '\n')
                    has_card = True
            except Exception as ex:
                print('card failed for', sl, s['title'], ex)
        image = f'{SITE}{sl}/card.jpg' if has_card else ''
        pages += write(os.path.join(folder, 'index.html'), page(d, s, f'../#{sl}', f'{SITE}{sl}/', image))
    d, s = dated[-1]
    sl = slug(d)
    image = f'{SITE}{sl}/card.jpg' if os.path.exists(os.path.join(SONGS, sl, 'card.jpg')) else ''
    pages += write(os.path.join(SONGS, 'today', 'index.html'), page(d, s, '../#today', f'{SITE}today/', image))
    print(f'{len(dated)} days: {pages} pages and {cards} cards written or changed; latest {d} {s["title"]}')

    # months: a page and a card of that month's covers
    small = {}
    def covers(items):
        out = []
        for _, x in items:
            if x['id'] not in small:
                try:
                    small[x['id']] = cover_bytes(x, small=True)
                except Exception:
                    small[x['id']] = None
            if small[x['id']]:
                out.append(small[x['id']])
        return out
    by_month = {}
    for d, s in dated:
        by_month.setdefault((d.year, d.month), []).append((d, s))
    for (y, m), items in sorted(by_month.items()):
        ms = f'{m:02d}-{y}'
        name = f"{items[0][0]:%B} {y}"
        count = f"{len(items)} {'song' if len(items) == 1 else 'songs'}"
        stamp = hashlib.sha1((GRID_VERSION + ' ' + ' '.join(x['id'] + str(x.get('img')) for _, x in items)).encode()).hexdigest()[:12]
        path = os.path.join(SONGS, ms, 'card.jpg')
        ok = refresh_card(path, stamp, lambda: grid_card(m - 1, f"{items[0][0]:%B}", str(y), "JOHN'S SONG OF THE DAY", count.upper(), covers(items)))
        image = f'{SITE}{ms}/card.jpg' if ok else ''
        pages += write(os.path.join(SONGS, ms, 'index.html'),
                       plain_page(name, f"{count} for each day", f'../#{ms}', f'{SITE}{ms}/',
                                  image, PALETTES[m - 1][0], f"Covers of the songs for {name}"))

    # the whole calendar: its newest covers
    latest = dated[-12:][::-1]
    first = dated[0][0]
    stamp = hashlib.sha1((GRID_VERSION + f' {len(dated)} ' + ' '.join(x['id'] for _, x in latest)).encode()).hexdigest()[:12]
    refresh_card(os.path.join(SONGS, 'card.jpg'), stamp,
                 lambda: grid_card(dated[-1][0].month - 1, "John's Song", 'of the Day', 'A SONG A DAY',
                                   f'{len(dated)} SO FAR', covers(latest)))
    print(f'{len(by_month)} month pages; calendar card {stamp}')

    # the calendar's own link preview says how many songs there are so far
    site = os.path.join(SONGS, 'index.html')
    if os.path.exists(site):
        page_html = open(site, encoding='utf-8').read()
        desc = f'A song a day, {len(dated)} so far'
        new_html = re.sub(r'(<meta (?:name="description"|property="og:description") content=")[^"]*(">)', lambda m: m.group(1) + desc + m.group(2), page_html)
        if write(site, new_html):
            print('calendar description:', desc)


if __name__ == '__main__':
    main()
