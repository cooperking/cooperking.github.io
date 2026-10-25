#!/usr/bin/env python3
"""Other YouTube uploads for each song whose video is only a still picture, for the admin page's Songs tab to offer
(John, 25 Oct 2026: "a list of possible alternatives" on the Still picture list).

    python3 _sotd/video_alts.py

Reads what the YouTube searches have already found (data/video-hunt.json, data/youtube.json) and writes
songs/video-alts.json: {songs: {track: [{v, t: title, c: channel, s: seconds, k: kind}, ...]}}, best first, up to 8 a
song. Kinds: video (a music video or promo), tv (a TV appearance), other, live, lyric, audio (another still picture).
Uploads that other sites can't play, that look like another song, or are covers, remixes and the like are left out.
Run by song-tags.yml after youtube.py, so it follows each day's videos.
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, 'songs', 'video-alts.json')

REAL = re.compile(r'music video|official video|\(video\)|\[video\]|promo|video clip|\bclip\b|official hd|\bvideo\b', re.I)
TV = re.compile(r'top of the pops|totp|toppop|beat.?club|soul train|bandstand|whistle test|ready steady|musikladen|'
                r'shindig|hullabaloo|midnight special|ed sullivan|\btv\b|television|chic-a-go-go|solid gold|countdown', re.I)
LIVE = re.compile(r'\blive\b|session|kexp|tiny desk|concert|performance', re.I)
LYRIC = re.compile(r'lyric|letra|legendado|tradu|subtitul|sub\.|visuali[sz]', re.I)
NOT_IT = re.compile(r'\bcover\b|remix|slowed|sped up|reverb|karaoke|instrumental|8d audio|nightcore|react|lesson|'
                    r'tutorial|how to play|guitar|piano|drum cover|bass cover|full album|extended', re.I)
ORDER = {'video': 0, 'tv': 1, 'other': 2, 'live': 3, 'lyric': 4, 'audio': 5}


def load(p, empty):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return empty


def fold(x):
    return re.sub(r'[^a-z0-9]+', ' ', re.sub(r'^the ', '', (x or '').lower().strip())).strip()


def kind_of(title, channel):
    if (channel or '').endswith(' - Topic') or re.search(r'official audio|\(audio\)|\[audio\]', title, re.I):
        return 'audio'
    if LYRIC.search(title):
        return 'lyric'
    if TV.search(title):
        return 'tv'
    if REAL.search(title):
        return 'video'
    if LIVE.search(title):
        return 'live'
    return 'other'


def main():
    vids = load(os.path.join(ROOT, 'songs', 'videos.json'), {}).get('videos', {})
    hunt = load(os.path.join(HERE, 'data', 'video-hunt.json'), {}).get('songs', {})
    found = load(os.path.join(HERE, 'data', 'youtube.json'), {}).get('songs', {})
    ms = load(os.path.join(HERE, 'data', 'links.json'), {}).get('ms', {})
    moods = load(os.path.join(ROOT, 'songs', 'moods.json'), {}).get('songs', {})
    out = {}
    for track, v in vids.items():
        if v.get('k') != 'audio':
            continue
        song = hunt.get(track, {})
        title = re.sub(r'\s*[-(\[].*$', '', song.get('title') or '').strip().lower()
        names = [fold(a) for a in re.split(r',|&| and | feat\.? | ft\.? ', song.get('artist') or moods.get(track, {}).get('artist') or '') if fold(a)]
        cands = {}
        for res in (song.get('queries') or {}).values():
            for r in res:
                cands.setdefault(r['id'], r)
        s = found.get(track) or {}
        for r in [s] + [s.get('searched') or {}]:
            if r.get('id'):
                cands.setdefault(r['id'], r)
        spot = (ms.get(track) or 0) / 1000 or song.get('secs') or 0
        rows = []
        for vid, r in cands.items():
            t, c = r.get('title') or '', r.get('channel') or ''
            if vid == v.get('v') or r.get('embed') is False or NOT_IT.search(t):
                continue
            if (r.get('score') or 0) < 40 and not (title and title in t.lower()):
                continue   # looks like another song
            if names and not any(n in fold(t + ' ' + c) for n in names):
                continue   # not the artist's (another artist's version)
            secs = r.get('secs') or 0
            if spot and secs and (secs < spot * 0.6 or secs > spot * 1.6):
                continue   # a snippet, or something much longer than the song
            k = kind_of(t, c)
            rows.append({'v': vid, 't': t[:120], 'c': c[:60], 's': secs, 'k': k,
                         '_o': (ORDER[k], abs(secs - spot) if spot and secs else 99, -(r.get('score') or 0))})
        rows.sort(key=lambda x: x['_o'])
        if rows:
            out[track] = [{k: x[k] for k in ('v', 't', 'c', 's', 'k')} for x in rows[:8]]
    data = {'about': 'Other YouTube uploads for songs whose video is only a still picture, best first (see _sotd/video_alts.py). '
                     'k: video, tv, other, live, lyric or audio; s: length in seconds.', 'songs': out}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    print(len(out), 'still-picture songs with other uploads to offer')


if __name__ == '__main__':
    main()
