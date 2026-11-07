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
import base64, json, os, shutil, sqlite3, sys, tempfile, time, unicodedata, urllib.parse, urllib.request


def nf(x):   # accents written either way (macOS's file names often split them: é as e + ´) compare the same
    return unicodedata.normalize('NFC', x or '')

GO = len(sys.argv) > 1 and sys.argv[1] == 'go'
CHUNK = int(os.environ.get("GRIFFLIX_CHUNK") or 50)
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
    """A smart collection's films' files, Plex's own count, and any part of its rule this couldn't follow.
    Plex's advanced rules come in order: push=1 opens a group, or=1 / and=1 says how its parts combine, pop=1 closes
    it (Cassavetes: push, director=…, or, title==A Constant Forge, pop). key! is "not"; title=x contains, title==x is."""
    try:
        d = json.loads(extra)
    except ValueError:
        return None, [], None
    uri, plex_n = d.get('pv:uri') or '', d.get('at:childCount')
    if '?' not in uri:
        return None, [], plex_n
    q = urllib.parse.parse_qsl(uri.split('?', 1)[1], keep_blank_values=True)
    t = next((int(v) for k, v in q if k == 'type' and v.isdigit()), 1)
    every = set(r[0] for r in db.execute('select id from metadata_items where metadata_type=?', (t,)))
    cols_of = set(r[1] for r in db.execute('pragma table_info(metadata_items)'))
    skipped = []

    def leaf(k, v):
        neg = k.endswith('!')
        key = k.rstrip('!')
        got = None
        if key in TAG_KEYS and all(x.strip().isdigit() for x in v.split(',') if x.strip()):
            ids = [int(x) for x in v.split(',') if x.strip()]
            got = set(r[0] for r in db.execute('select metadata_item_id from taggings where tag_id in (%s)' % ','.join('?' * len(ids)), ids)) & every
        elif key == 'title':
            exact = v.startswith('=')
            v2 = v[1:] if exact else v
            got = set(r[0] for r in db.execute('select id from metadata_items where metadata_type=? and ' + ('lower(title)=lower(?)' if exact else "lower(title) like '%'||lower(?)||'%'"), (t, v2)))
        elif key == 'contentRating' and 'content_rating' in cols_of:
            vals = [x for x in v.split(',') if x]
            got = set(r[0] for r in db.execute('select id from metadata_items where metadata_type=? and content_rating in (%s)' % ','.join('?' * len(vals)), [t] + vals))
        elif key.rstrip('<>') == 'year' and v.isdigit():
            op = '>=' if key.endswith('>>') else '<=' if key.endswith('<<') else '='
            got = set(r[0] for r in db.execute('select id from metadata_items where metadata_type=? and year %s ?' % op, (t, int(v))))
        if got is None:
            return None
        return every - got if neg else got

    def combine(g):
        if not g['sets']:
            return None
        out = set(g['sets'][0])
        for x in g['sets'][1:]:
            out = out | x if g['op'] == 'or' else out & x
        return out

    stack = [{'op': 'and', 'sets': []}]
    for k, v in q:
        if k in SKIP_KEYS:
            continue
        if k == 'push':
            stack.append({'op': 'and', 'sets': []})
        elif k == 'pop' and len(stack) > 1:
            g = combine(stack.pop())
            if g is not None:
                stack[-1]['sets'].append(g)
        elif k in ('or', 'and'):
            stack[-1]['op'] = k
        else:
            if '=' in k and not v:   # (title==x arrives whole as the name when its = signs were encoded)
                k, v = k.split('=', 1)
            if k.endswith('='):   # (or as title= and =x: John's Cassavetes)
                k, v = k[:-1], '=' + v
            x = leaf(k, v)
            if x is None:
                skipped.append('%s=%s' % (k, v))
            else:
                stack[-1]['sets'].append(x)
    while len(stack) > 1:
        g = combine(stack.pop())
        if g is not None:
            stack[-1]['sets'].append(g)
    chosen = combine(stack[0])
    if chosen is None:
        return None, skipped, plex_n
    return files_of(sorted(chosen)), skipped, plex_n


PLEX_TY = {nf(f): (nf(t), y) for f, t, y in db.execute('''select mp.file, mi.title, mi.year from metadata_items mi
        join media_items m on m.metadata_item_id=mi.id join media_parts mp on mp.media_item_id=m.id where mi.metadata_type in (1, 4)''')}


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
by_path, by_name, by_dir, by_title = {}, {}, {}, {}
for lib, typ in (('movies', 'Movie'), ('tv', 'Series')):
    lid = s['libraries'].get(lib)
    if not lid:
        continue
    for it in call('GET', '/Users/%s/Items' % s['jf_user_id'], ParentId=lid, Recursive='true', IncludeItemTypes=typ, Fields='Path,ProductionYear').get('Items', []):
        p = nf(it.get('Path'))
        if typ == 'Movie':
            by_title.setdefault((nf(it.get('Name')).lower(), it.get('ProductionYear')), it['Id'])
        by_path[p] = it['Id']
        by_name.setdefault(os.path.basename(p), it['Id'])
        if typ == 'Series':
            by_dir[nf(os.path.basename(p.rstrip('/')))] = it['Id']   # (a series by its folder's name)
base = None
for p in by_path:
    for root in ('/Volumes/G-Drive/Grifflix/', '/Volumes/G-Drive/plex/'):
        if p.startswith(root):
            base = root
    if base:
        break


def find(f):
    f = nf(f)
    now = f.replace('/Volumes/G-Drive/plex/', base or '/Volumes/G-Drive/Grifflix/')
    if now in by_path:
        return by_path[now]
    if by_name.get(os.path.basename(f)):
        return by_name[os.path.basename(f)]
    if '/TV/' in f:   # an episode: its series, by the series' folder (the first one under TV)
        return by_dir.get(f.split('/TV/', 1)[1].split('/')[0])
    t, y = PLEX_TY.get(f, (None, None))   # renamed by the tidy-up: by its title and year, as Plex knew them
    return by_title.get(((t or '').lower(), y)) if t else None


# Box sets: collections John made in Plex only to show a Criterion set (several short films) as one thing. Each is made
# first, then stands as ONE entry in every other collection that holds its films (in place of the films), as he asked
# (7 Nov 2026: "can they now be put inside the collection as one item each").
SETS = ['A Hollis Frampton Odyssey', 'By Brakhage: An Anthology, Volumes One and Two', 'Martha Graham: Dance on Film',
        'Scorsese Shorts', 'Symbiopsychotaxiplasm: Two Takes by William Greaves', 'The Kennedy Films of Robert Drew & Associates',
        'The Signifyin’ Works of Marlon Riggs']
# and every box set is in the Criterion Collection, whether or not Plex had its films there (John, 7 Nov 2026: "By
# Brakhage: An Anthology, Volumes One and Two should also be a box set in the Criterion")
ALSO_IN = {'Criterion Collection': SETS}
cols.sort(key=lambda c: (c[1] not in SETS, c[1].lower()))   # (the sets first)
set_id = {}   # a box set's name → its collection in Jellyfin (or, before it's made, 'set:' + its name)
set_of = {}   # a film in a set → the set's collection (or, before it's made, the set's name)
have = {it['Name']: it for it in (call('GET', '/Users/%s/Items' % s['jf_user_id'], Recursive='true', IncludeItemTypes='BoxSet', Fields='Overview').get('Items', []))}
made = added = pics = 0
lost = []
for cid, title, h, thumb, summary, extra in cols:
    files, skipped, plex_n = members.get(title, []), [], None
    if not files and '"at:smart":"1"' in extra.replace(' ', ''):
        files, skipped, plex_n = smart(extra)
        files = files or []
    ids = []
    for f in files:
        i = find(f)
        if i and i not in ids:
            ids.append(i)
        elif not i:
            lost.append((title, os.path.basename(f)))
    in_set = 0
    if title not in SETS:   # films of a box set: the set, once, in their place
        swapped = []
        for i in ids:
            j = set_of.get(i, i)
            in_set += j != i
            if j not in swapped:
                swapped.append(j)
        ids = swapped
    put = []
    for st in ALSO_IN.get(title, []):
        j = set_id.get(st)
        if j and j not in ids and not (GO and j.startswith('set:')):
            ids.append(j); put.append(st)
    pic = poster(h, thumb)
    old = have.get(title)
    if title in SETS:
        sid = set_id[title] = old['Id'] if old else 'set:' + title
        for i in ids:
            set_of[i] = sid
    print('%-52s %3d of %3d found%s%s%s%s' % (title[:52], len(ids), len(files), ', poster' if pic else '', (', smart (Plex had %s)' % plex_n) if '"at:smart":"1"' in extra.replace(' ', '') else '',
          ' (already in Jellyfin)' if old else '', (' NOT FOLLOWED: ' + '; '.join(skipped)) if skipped else ''))
    if title in SETS:
        print('   a box set: one entry in the other collections')
    elif in_set:
        print('   %d of its films are in box sets, shown as the sets' % in_set)
    if put:
        print('   with the box sets: ' + '; '.join(put))
    if not GO or not ids:
        continue
    batches = [ids[i:i + CHUNK] for i in range(0, len(ids), CHUNK)]   # (50 at a time: Criterion USA's 502 in one go was too long an address)
    if old:
        bid = old['Id']
        added += 1
    else:
        bid = call('POST', '/Collections', b'', Name=title, Ids=','.join(batches.pop(0)), IsLocked='true')['Id']
        made += 1
    if title in SETS:
        set_id[title] = bid
        for i in list(set_of):
            if set_of[i] == 'set:' + title:
                set_of[i] = bid
    for chunk in batches:
        call('POST', '/Collections/%s/Items' % bid, b'', Ids=','.join(chunk))
    for _ in range(3):   # (Jellyfin can drop one of several adds made in quick succession: what it holds, checked, and any missing added again)
        time.sleep(1.5)
        held = set(i['Id'] for i in call('GET', '/Users/%s/Items' % s['jf_user_id'], ParentId=bid, Fields='').get('Items', []))
        missing = [i for i in ids if i not in held]
        loose = [i for i in held if title not in SETS and i in set_of]   # a set's film still on its own here (from before): out
        if loose:
            call('DELETE', '/Collections/%s/Items' % bid, Ids=','.join(loose))
        if not missing:
            break
        for i in range(0, len(missing), CHUNK):
            call('POST', '/Collections/%s/Items' % bid, b'', Ids=','.join(missing[i:i + CHUNK]))
    else:
        print('   (%d of its films wouldn’t go in: run it again later)' % len(missing))
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
