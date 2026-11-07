#!/usr/bin/env python3
"""Which films and episodes stream best from the Mac mini: each one's format, from Jellyfin, sorted into how much
work the Mac has to do to play it. Reads only. Writes ~/Grifflix/streaming.csv and prints a summary.

The 2010 Mac mini (Core 2 Duo, no video chip help) can repackage and convert sound freely, but can't convert HD
video as it plays. So:
  plays as it is        MP4/M4V/MOV with H.264 video and AAC/MP3 sound: straight to any device
  repackaged (light)    H.264 in MKV or AVI, or sound needing conversion (AC3, DTS...): Jellyfin copies the
                        picture into a new wrapper; everywhere, at no real cost
  Apple and Fire TV     HEVC (H.265): iPhone, iPad, Safari, Apple TV and Fire TV play it as it is; Chrome,
                        Firefox and most Android phones would need it converted, which this Mac can't do in time
  needs converting      anything else (MPEG-4 Part 2/XviD/DivX, MPEG-2, VC-1, AV1, VP8/VP9, 10-bit H.264, 4K):
                        the Mac would have to convert the picture, which it can't do smoothly for HD; SD may cope
"""
import csv, json, os, urllib.parse, urllib.request
from collections import Counter

APP = os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix')
s = json.load(open(os.path.join(APP, 'settings.json')))


def get(p, **q):
    url = 'http://127.0.0.1:8096' + p + ('?' + urllib.parse.urlencode(q) if q else '')
    r = urllib.request.Request(url, headers={'Authorization': 'MediaBrowser Token="%s"' % s['jf_token']})
    return json.load(urllib.request.urlopen(r, timeout=120))


def kind(ms):
    container = (ms.get('Container') or '').lower()
    streams = ms.get('MediaStreams') or []
    v = next((x for x in streams if x.get('Type') == 'Video'), {})
    a = next((x for x in streams if x.get('Type') == 'Audio' and x.get('IsDefault')), None) or next((x for x in streams if x.get('Type') == 'Audio'), {})
    vc, ac = (v.get('Codec') or '?').lower(), (a.get('Codec') or 'none').lower()
    w, h = v.get('Width') or 0, v.get('Height') or 0
    bits = v.get('BitDepth') or 8
    prof = (v.get('Profile') or '').lower()
    if w > 2600 or h > 1700:
        k = '4 needs converting'
    elif vc == 'h264' and bits <= 8 and '10' not in prof:
        k = '1 plays as it is' if container.split(',')[0] in ('mp4', 'm4v', 'mov') and ac in ('aac', 'mp3') else '2 repackaged (light)'
    elif vc in ('hevc', 'h265'):
        k = '3 Apple and Fire TV'
    else:
        k = '4 needs converting'
    return k, container, vc, ac, '%dx%d' % (w, h), round((ms.get('Bitrate') or 0) / 1e6, 1)


rows, count = [], Counter()
for lib, types in (('movies', 'Movie'), ('tv', 'Episode')):
    lid = s['libraries'].get(lib)
    if not lid:
        continue
    start = 0
    while True:
        r = get('/Users/%s/Items' % s['jf_user_id'], ParentId=lid, Recursive='true', IncludeItemTypes=types,
                Fields='MediaSources,Path,ProductionYear', StartIndex=start, Limit=300, SortBy='SortName')
        for it in r.get('Items', []):
            ms = (it.get('MediaSources') or [{}])[0]
            k, container, vc, ac, size, mbps = kind(ms)
            name = it.get('Name') if types == 'Movie' else '%s S%02dE%02d %s' % (it.get('SeriesName'), it.get('ParentIndexNumber') or 0, it.get('IndexNumber') or 0, it.get('Name'))
            rows.append([lib, k[2:], name, it.get('ProductionYear') or '', container, vc, ac, size, mbps, it.get('Path') or ''])
            count[(lib, k)] += 1
        start += 300
        if start >= r.get('TotalRecordCount', 0):
            break

out = os.path.expanduser('~/Grifflix/streaming.csv')
with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['library', 'how it streams', 'title', 'year', 'container', 'video', 'sound', 'size', 'Mbps', 'path'])
    w.writerows(sorted(rows, key=lambda r: (r[0], r[1], r[2])))
for lib in ('movies', 'tv'):
    n = sum(v for (l, _), v in count.items() if l == lib)
    if not n:
        continue
    print('%s (%d):' % ('Films' if lib == 'movies' else 'TV episodes', n))
    for k in sorted(set(k for (l, k) in count if l == lib)):
        print('  %-24s %5d  (%d%%)' % (k[2:], count[(lib, k)], round(100 * count[(lib, k)] / n)))
print('\nEvery title, with its format:', out)
