#!/usr/bin/env python3
"""Tells the Worker when a new song is out on the live calendar, so its notification goes to the members then (John,
28 Oct 2026: "can't a push just be when a new song goes live?"). Run at the end of song-tags.yml, after it has pushed.

The run's own songs/today.json names the latest song out (share_pages.py writes it with each release). This waits, up to
8 minutes, for the live site's copy to name the same song (GitHub Pages takes a minute or two to publish), then asks the
Worker (/push/check), which looks at the live site for itself and sends the newest song once. A song already sent comes
back "sent already". If the site isn't showing it in time, or the Worker can't be reached, the Worker's own 10-minute
look sends it instead, so this never fails the job.
"""
import json, os, sys, time, urllib.request
import runinfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.environ.get('SITE_TODAY', 'https://cooperking.github.io/songs/today.json')
WORKER = os.environ.get('PUSH_CHECK', 'https://sotd-playlists.cooperking.workers.dev/push/check')
WAIT, EVERY = int(os.environ.get('PUSH_WAIT', '480')), int(os.environ.get('PUSH_EVERY', '20'))


def get(url):
    req = urllib.request.Request(url + ('&' if '?' in url else '?') + 't=%d' % time.time(), headers={'User-Agent': 'SongOfTheDayCalendar/1.0', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    try:
        with open(os.path.join(ROOT, 'songs', 'today.json'), encoding='utf-8') as f:
            mine = json.load(f)
    except FileNotFoundError:
        print('no songs/today.json'); return 0
    track = mine.get('track')
    if not track:
        print('songs/today.json names no track yet'); return 0
    end = time.time() + WAIT
    while True:
        try:
            live = get(SITE)
        except Exception as e:
            live = {}; print('the live site:', e)
        if live.get('track') == track:
            break
        if time.time() > end:
            print('the live site doesn’t show %s (%s) after %d s: the Worker’s 10-minute look will send it' % (mine.get('title'), track, WAIT)); return 0
        time.sleep(EVERY)
    print('out on the live calendar:', mine.get('title') or track)
    try:
        req = urllib.request.Request(WORKER, data=json.dumps({'run': runinfo.this_run()}).encode(), method='POST', headers={'Content-Type': 'application/json', 'User-Agent': 'SongOfTheDayCalendar/1.0'})
        with urllib.request.urlopen(req, timeout=60) as r:
            print('the Worker:', json.loads(r.read()).get('did'))
    except Exception as e:
        print('the Worker couldn’t be asked (its 10-minute look will send it):', e)
    return 0


if __name__ == '__main__':
    sys.exit(main())
