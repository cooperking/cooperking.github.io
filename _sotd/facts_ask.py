#!/usr/bin/env python3
"""Starts Claude's facts task for new songs (the routine "Song facts for new songs (on demand)", see FACTS.md), straight after
song-tags.yml has saved their sources, instead of on a timer. Called at the end of that job.

The routine is started through its API trigger: its address and token, which John made at claude.ai/code/routines and
keeps as two GitHub secrets, SOTD_FACTS_URL and SOTD_FACTS_TOKEN (the job passes them in as FACTS_TASK_URL and
FACTS_TASK_TOKEN). Without them this does nothing. A song already asked for in the
last 3 hours isn't asked for again (the task may still be writing), so a second run of the job doesn't start a second
task; FORCE=true starts it anyway (to test the link: with nothing to write, the task just says so).

Records what it asked for, and when, in _sotd/data/facts-asked.json.
"""
import datetime, json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASKED = os.path.join(HERE, 'data', 'facts-asked.json')
AGAIN_AFTER = datetime.timedelta(hours=3)


def load(p, empty):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return empty


def main():
    url, token = os.environ.get('FACTS_TASK_URL', '').strip(), os.environ.get('FACTS_TASK_TOKEN', '').strip()
    force = os.environ.get('FORCE', '').lower() == 'true'
    S = load(os.path.join(HERE, 'data', 'sources.json'), {'songs': {}})
    live = load(os.path.join(ROOT, 'songs', 'facts.json'), {'songs': {}})
    pending = load(os.path.join(HERE, 'data', 'facts-pending.json'), {'songs': {}})
    need = sorted((s['n'], tid, s['title'], s['artist']) for tid, s in S['songs'].items()
                  if tid not in live.get('songs', {}) and tid not in live.get('signed', {}) and tid not in pending['songs'])
    asked = load(ASKED, {})
    now = datetime.datetime.now(datetime.timezone.utc)
    fresh = [x for x in need if not (x[1] in asked and now - datetime.datetime.fromisoformat(asked[x[1]]) < AGAIN_AFTER)]
    print(len(need), 'songs need facts;', len(fresh), 'not asked for in the last 3 hours')
    if not fresh and not force:
        return
    if not url or not token:
        print('the facts task has no API trigger set up yet (the SOTD_FACTS_URL and SOTD_FACTS_TOKEN secrets): not started')
        return
    text = ('Started by song-tags.yml: ' + '; '.join('%d ‘%s’ by %s' % (n, t, a) for n, _, t, a in fresh) + ' need facts.'
            if fresh else 'Started by hand from song-tags.yml to test the link.')
    req = urllib.request.Request(url, data=json.dumps({'text': text}).encode(), method='POST', headers={
        'Authorization': 'Bearer ' + token, 'anthropic-version': '2023-06-01',
        'anthropic-beta': 'experimental-cc-routine-2026-04-01', 'Content-Type': 'application/json',
        'User-Agent': 'SongOfTheDayCalendar/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        print('the facts task could not be started: HTTP', e.code, e.read()[:300].decode('utf-8', 'replace'))
        return
    except Exception as e:
        print('the facts task could not be started:', e)
        return
    print('facts task started:', j.get('claude_code_session_url') or j)
    for _, tid, _, _ in fresh:
        asked[tid] = now.isoformat(timespec='seconds')
    with open(ASKED, 'w', encoding='utf-8') as f:
        json.dump(asked, f, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
