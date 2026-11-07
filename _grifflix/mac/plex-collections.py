#!/usr/bin/env python3
"""Brings John's Plex collections into Grifflix: each one made in Jellyfin (a "collection", Jellyfin's BoxSet) with
the same films and series, its poster (the one he uploaded to Plex, or Plex's), and its description.

Reads a copy of Plex's database and Plex's pictures; changes nothing of Plex's. Safe to run again: a collection
already in Jellyfin by the same name gets any films it lacks, and keeps its poster if it has one.
  python3 plex-collections.py        what it would make (nothing changes)
  python3 plex-collections.py go     makes them

Plex keeps a collection as a metadata item (type 18: its name, summary and poster) and its films as tags of type 2
with the collection's name (checked on John's Plex, 7 Nov 2026). Films are found in Jellyfin by their file, at its
new place if the tidy-up moved it (by its name).
"""
import base64, json, os, shutil, sqlite3, sys, tempfile, urllib.parse, urllib.request

GO = len(sys.argv) > 1 and sys.argv[1] == 'go'
APP = os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix')
PLEX = os.environ.get('PLEX_DIR') or os.path.expanduser('~/Library/Application Support/Plex Media Server')
s = json.load(open(os.path.join(APP, 'settings.json')))
JF = s.get('jellyfin', 'http://127.0.0.1:8096')


def call(method, path, body=None, ctype='application/json', **q):
    url = JF + path + ('?' + urllib.parse.urlencode(q) if q else '')
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method=method, headers={'Authorization': 'MediaBrowser Token="%s"' % s['jf_token'], 'Content-Type': ctype})
    raw = urllib.request.urlopen(r, timeout=120).read()
    return json.loads(raw) if raw[:1] in (b'{', b'[') else None


# Plex's collections, their films' files, and their posters
tmp = tempfile.mkdtemp()
shutil.copy(os.path.join(PLEX, 'Plug-in Support', 'Databases', 'com.plexapp.plugins.library.db'), os.path.join(tmp, 'plex.db'))
db = sqlite3.connect(os.path.join(tmp, 'plex.db'))
cols = db.execute("select id, title, hash, ifnull(user_thumb_url,''), ifnull(summary,''), ifnull(extra_data,'') from metadata_items where metadata_type=18 order by title").fetchall()
members = {}
for tag, f in db.execute("""select t.tag, min(mp.file) from tags t join taggings g on g.tag_id=t.id
        join metadata_items mi on mi.id=g.metadata_item_id
        left join metadata_items se on mi.metadata_type=2 and se.parent_id=mi.id
        left join metadata_items ep on ep.parent_id=se.id
        join media_items m on m.metadata_item_id = case when mi.metadata_type=2 then ep.id else mi.id end
        join media_parts mp on mp.media_item_id=m.id
        where t.tag_type=2 group by t.tag, mi.id"""):
    members.setdefault(tag, []).append(f)


# Smart collections hold a rule, not a list: Plex's own address for the films, e.g. .../all?type=1&label=72873 or
# &director=1234 (John's, 7 Nov 2026). The rule's tags (labels, directors, actors, genres...) are worked out here from
# which films carry them: within one kind any of the tags (a,b), across kinds all of them.
TAG_KEYS = {'label', 'director', 'actor', 'genre', 'writer', 'producer', 'collection', 'country', 'role', 'mood', 'style'}
SKIP_KEYS = {'type', 'sort', 'includeGuids', 'includeCollections', 'includeExternalMedia'}


def files_of(item_ids):
    out = []
    for i in item_ids:
        r = db.execute("""select min(mp.file) from metadata_items mi
                left join metadata_items se on mi.metadata_type=2 and se.parent_id=mi.id
                left join metadata_items ep on ep.parent_id=se.id
                join media_items m on m.metadata_item_id = case when mi.metadata_type=2 then ep.id else mi.id end
                join media_parts mp on mp.media_item_id=m.id where mi.id=?""", (i,)).fetchone()
        if r and r[0]:
            out.append(r[0])
    return out


def smart(extra):
    """A smart collection's films' files, and any part of its rule this couldn't follow."""
    try:
        uri = json.loads(extra).get('pv:uri') or ''
    except ValueError:
        return None, []
    if '?' not in uri:
        return None, []
    q = urllib.parse.parse_qsl(uri.split('?', 1)[1], keep_blank_values=True)
    want_type = next((int(v) for k, v in q if k == 'type' and v.isdigit()), 1)
    chosen, skipped = None, []
    for k, v in q:
        key = k.rstrip('!=<>')
        if key in SKIP_KEYS:
            continue
        if key in TAG_KEYS and not k.endswith('!') and all(x.strip().isdigit() for x in v.split(',') if x.strip()):
            ids = [int(x) for x in v.split(',') if x.strip()]
            got = set(r[0] for r in db.execute('select g.metadata_item_id from taggings g join metadata_items mi on mi.id=g.metadata_item_id where mi.metadata_type=? and g.tag_id in (%s)' % ','.join('?' * len(ids)), [want_type] + ids))
            chosen = got if chosen is None else chosen & got
        elif key == 'year' and v.isdigit():
            got = set(r[0] for r in db.execute('select id from metadata_items where metadata_type=? and year %s ?' % ('>=' if k.endswith('>>') else '<=' if k.endswith('<<') else '='), (want_type, int(v))))
            chosen = got if chosen is None else chosen & got
        else:
            skipped.append('%s=%s' % (k, v))
    if chosen is None:
        return None, skipped
    return files_of(sorted(chosen)), skipped


def poster(h, url):
    if not url or '://' not in url:
        return None
    pid = url.split('/')[-1]
    b = os.path.join(PLEX, 'Metadata', 'Collections', h[0], h[1:] + '.bundle')
    for c in (os.path.join(b, 'Uploads', 'posters', pid), os.path.join(b, 'Contents', '_combined', 'posters', pid)):
        if os.path.isfile(c) and os.path.getsize(c) > 0:
            return c
    return None


# Jellyfin's films and series, by their file (films) or folder (series), and by name for the moved ones
by_path, by_name, by_dir = {}, {}, {}
for lib, typ in (('movies', 'Movie'), ('tv', 'Series')):
    lid = s['libraries'].get(lib)
    if not lid:
        continue
    for it in call('GET', '/Users/%s/Items' % s['jf_user_id'], ParentId=lid, Recursive='true', IncludeItemTypes=typ, Fields='Path').get('Items', []):
        p = it.get('Path') or ''
        by_path[p] = it['Id']
        by_name.setdefault(os.path.basename(p), it['Id'])
        if typ == 'Series':
            by_dir[os.path.basename(p.rstrip('/'))] = it['Id']   # (a series by its folder's name)
base = None
for p in by_path:
    for root in ('/Volumes/G-Drive/Grifflix/', '/Volumes/G-Drive/plex/'):
        if p.startswith(root):
            base = root
    if base:
        break


def find(f):
    now = f.replace('/Volumes/G-Drive/plex/', base or '/Volumes/G-Drive/Grifflix/')
    if now in by_path:
        return by_path[now]
    if by_name.get(os.path.basename(f)):
        return by_name[os.path.basename(f)]
    if '/TV/' in f:   # an episode: its series, by the series' folder (the first one under TV)
        return by_dir.get(f.split('/TV/', 1)[1].split('/')[0])
    return None


have = {it['Name']: it for it in (call('GET', '/Users/%s/Items' % s['jf_user_id'], Recursive='true', IncludeItemTypes='BoxSet', Fields='Overview').get('Items', []))}
made = added = pics = 0
lost = []
for cid, title, h, thumb, summary, extra in cols:
    files, skipped = members.get(title, []), []
    if not files and '"at:smart":"1"' in extra.replace(' ', ''):
        files, skipped = smart(extra)
        files = files or []
    ids = []
    for f in files:
        i = find(f)
        if i and i not in ids:
            ids.append(i)
        elif not i:
            lost.append((title, os.path.basename(f)))
    pic = poster(h, thumb)
    old = have.get(title)
    print('%-52s %3d of %3d found%s%s%s%s' % (title[:52], len(ids), len(files), ', poster' if pic else '', ', smart' if '"at:smart":"1"' in extra.replace(' ', '') else '',
          ' (already in Jellyfin)' if old else '', (' NOT FOLLOWED: ' + '; '.join(skipped)) if skipped else ''))
    if not GO or not ids:
        continue
    if old:
        bid = old['Id']
        call('POST', '/Collections/%s/Items' % bid, b'', Ids=','.join(ids))
        added += 1
    else:
        bid = call('POST', '/Collections', b'', Name=title, Ids=','.join(ids), IsLocked='true')['Id']
        made += 1
    if pic and not (old and (old.get('ImageTags') or {}).get('Primary')):
        ctype = 'image/png' if open(pic, 'rb').read(4) == b'\x89PNG' else 'image/jpeg'
        call('POST', '/Items/%s/Images/Primary' % bid, base64.b64encode(open(pic, 'rb').read()), ctype)
        pics += 1
    if summary and not (old and old.get('Overview')):
        it = call('GET', '/Users/%s/Items/%s' % (s['jf_user_id'], bid))
        it['Overview'] = summary
        call('POST', '/Items/%s' % bid, it)

print('\n%d collections in Plex.' % len(cols))
if lost:
    print('%d films not found in Grifflix (moved aside, or not on the drive any more), e.g.:' % len(lost))
    for t, f in lost[:8]:
        print('   %s: %s' % (t, f))
print(('Made %d collections, added to %d, %d posters.' % (made, added, pics)) if GO else 'Nothing changed. Run  python3 plex-collections.py go  to make them.')
shutil.rmtree(tmp)
