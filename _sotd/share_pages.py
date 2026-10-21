#!/usr/bin/env python3
"""Writes a small page for each day's song, so a shared link shows that song's cover, title and artist:

  songs/21-october-2026/index.html   one per day, sends the visitor on to songs/#21-october-2026
  songs/today/index.html          the latest song, sends the visitor on to songs/#today

Days follow the calendar's rule: a song's day is when it was added (London time), and when two
were added on one day the earlier one moves back to the day before.
"""
import html, json, os, urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SONGS = os.path.join(HERE, '..', 'songs')
SITE = 'https://cooperking.github.io/songs/'
SONGS_URL = os.environ.get('SOTD_SONGS_URL', 'https://sotd-playlists.cooperking.workers.dev/songs.json')
LONDON = ZoneInfo('Europe/London')


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
    return f'{d.day}-{d:%B}-{d.year}'.lower()


def long_date(d):
    return f'{d:%A} {d.day} {d:%B %Y}'


def page(d, s, target, url):
    e = lambda x: html.escape(x, quote=True)
    img = (s.get('img') or '').replace('ab67616d00001e02', 'ab67616d0000b273')   # the 640-pixel cover
    title = f"{s['title']} — {s['artist']}"
    desc = f"John's Song of the Day for {long_date(d)}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · John's Song of the Day</title>
<meta name="description" content="{e(desc)}: {e(s['title'])} by {e(s['artist'])}.">
<link rel="canonical" href="{e(url)}">
<meta property="og:type" content="music.song">
<meta property="og:site_name" content="John's Song of the Day">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(url)}">
{f'<meta property="og:image" content="{e(img)}">' if img else ''}
<meta property="og:image:width" content="640">
<meta property="og:image:height" content="640">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(desc)}">
{f'<meta name="twitter:image" content="{e(img)}">' if img else ''}
<link rel="icon" type="image/png" sizes="96x96" href="/songs/favicon-96.png?v=2">
<meta http-equiv="refresh" content="0; url={e(target)}">
<script>location.replace({json.dumps(target)});</script>
</head>
<body style="font-family: Georgia, serif; padding: 24px;">
<p><a href="{e(target)}">{e(s['title'])} by {e(s['artist'])}</a>, John's Song of the Day for {e(long_date(d))}.</p>
</body>
</html>
"""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and open(path, encoding='utf-8').read() == text:
        return False
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return True


def main():
    with urllib.request.urlopen(urllib.request.Request(SONGS_URL, headers={'User-Agent': 'SongOfTheDayCalendar/1.0'}), timeout=60) as r:
        songs = json.loads(r.read())['songs']
    dated = days(songs)
    changed = 0
    for d, s in dated:
        sl = slug(d)
        changed += write(os.path.join(SONGS, sl, 'index.html'), page(d, s, f'../#{sl}', f'{SITE}{sl}/'))
    d, s = dated[-1]
    changed += write(os.path.join(SONGS, 'today', 'index.html'), page(d, s, '../#today', f'{SITE}today/'))
    print(f'{len(dated)} day pages, {changed} written or changed; latest {d} {s["title"]}')


if __name__ == '__main__':
    main()
