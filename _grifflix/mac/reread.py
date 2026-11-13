#!/usr/bin/env python3
"""Has the media server read a film's file again: for a film it read while the file was still being copied in, so it knows
nothing of its picture and sound (and the converter can't tell what it needs). (John, 13 Nov 2026: Obsession.)

  python3 reread.py Obsession          the films matching "Obsession": read again those it has no details of
  python3 reread.py Obsession all      every film matching, read again
"""
import json, os, sys, urllib.parse, urllib.request

APP = os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix')
s = json.load(open(os.path.join(APP, 'settings.json')))


def jf(method, path, **q):
    url = s.get('jellyfin', 'http://127.0.0.1:8096') + path + ('?' + urllib.parse.urlencode(q) if q else '')
    r = urllib.request.Request(url, method=method, headers={'Authorization': 'MediaBrowser Token="%s"' % s['jf_token']})
    raw = urllib.request.urlopen(r, timeout=60).read()
    return json.loads(raw) if raw[:1] in (b'{', b'[') else None


if len(sys.argv) < 2:
    sys.exit('Which film? e.g.  python3 reread.py Obsession')
term, every = sys.argv[1], len(sys.argv) > 2 and sys.argv[2] == 'all'
r = jf('GET', '/Users/%s/Items' % s['jf_user_id'], SearchTerm=term, IncludeItemTypes='Movie,Episode', Recursive='true', Fields='Path,MediaSources')
items = r.get('Items', r.get('items', []))
if not items:
    sys.exit('No film matching "%s" in the media server.' % term)
for it in items:
    ms = (it.get('MediaSources') or [{}])[0]
    n = len(ms.get('MediaStreams') or [])
    print('%s (%s): %d streams, %s' % (it.get('Name'), it.get('ProductionYear', '?'), n, it.get('Path')))
    if n == 0 or every:
        jf('POST', '/Items/%s/Refresh' % it['Id'], metadataRefreshMode='FullRefresh', imageRefreshMode='Default',
           replaceAllMetadata='false', replaceAllImages='false')
        print('   asked the media server to read it again: give it a minute')
