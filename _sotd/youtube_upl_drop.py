#!/usr/bin/env python3
"""Deletes the YouTube copies of members' playlists taken off the calendar (John, 1 Nov 2026: "AaBbCc and Potench
playlists have been deleted now so the songs should be deleted fully"). The Worker keeps their YouTube playlist ids
when a playlist is deleted (/upl/ytdrop, read with this job's GITHUB_TOKEN); each is deleted on John's channel (50
units), then taken off the Worker's list. One already gone from YouTube is taken off too.

    python3 _sotd/youtube_upl_drop.py
"""
import json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ytapi   # noqa: E402

URL = os.environ.get('SOTD_YTDROP_URL', 'https://sotd-playlists.cooperking.workers.dev/upl/ytdrop')


def worker(method, body=None):
    tok = os.environ.get('GITHUB_TOKEN', '').strip()
    req = urllib.request.Request(URL, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json', 'User-Agent': ytapi.UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    if not os.environ.get('GITHUB_TOKEN'):
        print('deleted playlists’ YouTube copies: no GITHUB_TOKEN, so none this run')
        return
    try:
        ids = worker('GET').get('ids') or []
    except Exception as e:
        print('deleted playlists’ YouTube copies: no answer from the Worker:', e)
        return
    if not ids:
        return
    tok = ytapi.token()
    if not tok:
        print('deleted playlists’ YouTube copies: no YouTube sign-in, so they wait')
        return
    done = []
    for pl in ids:
        try:
            ytapi.spend(50)
        except ytapi.Quota:
            print('no YouTube allowance left today: the rest wait')
            break
        r = ytapi.call('DELETE', 'https://www.googleapis.com/youtube/v3/playlists?id=' + pl, token=tok)
        st = r.get('_status') if isinstance(r, dict) else None
        if st in (None, 204, 404):
            done.append(pl)
            print('a deleted playlist’s YouTube copy', 'deleted:' if st != 404 else 'was gone already:', pl)
        else:
            print('couldn’t delete', pl, st, r.get('_text', '')[:120])
    if done:
        worker('POST', {'done': done})


if __name__ == '__main__':
    main()
