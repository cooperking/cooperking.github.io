#!/usr/bin/env python3
"""Grifflix gateway: the only thing on the Mac mini the internet reaches (through the Cloudflare Tunnel).

It sits in front of Jellyfin (which stays on 127.0.0.1) and:
- lets in Grifflix's members only: each request's sign-in is checked with the Worker's /g/me (Grifflix's own members);
- gives the Grifflix page its own small API (/api/...): the library, Movie of the Day, members' own calendars
  and playlists, and where each member stopped watching;
- passes pictures (/img/...) and video (/v/...) through from Jellyfin, with a short-lived signed key in the link
  (a video tag or an iPhone can't send a sign-in header), so Jellyfin's own key never leaves this Mac.

Python 3 standard library only, so it runs on the Mac's own /usr/bin/python3.

  grifflix-gateway.py setup --movies DIR --tv DIR --home DIR   Jellyfin's first-run setup, done for you
  grifflix-gateway.py serve                                     the gateway (the LaunchAgent runs this)
"""
import base64, hashlib, hmac, http.client, json, os, re, secrets, sys, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix')
SETTINGS = os.path.join(APP_DIR, 'settings.json')
DATA = os.path.join(APP_DIR, 'data.json')
WORKER = 'https://sotd-playlists.cooperking.workers.dev'
ORIGINS = ['https://cooperking.github.io', 'https://grifflix.co.uk']   # (Grifflix on its own address too: John, 9 Nov 2026)
KEY_HOURS = 12            # how long a picture or video link works
ME_CACHE_S = 600          # how long a member check with the Worker is trusted
VERSION = '1'

FIELDS = ('Overview,Genres,OfficialRating,CommunityRating,ProductionYear,PremiereDate,RunTimeTicks,ImageTags,'
          'BackdropImageTags,ParentBackdropImageTags,SeriesPrimaryImageTag,ChildCount,RecursiveItemCount,MediaSources,'
          'People,Taglines,Studios,DateCreated,Width,Height,RemoteTrailers,Path')
# lists (rows, Browse, collections, calendars) need only this much: the old Mac answers far faster without each film's
# files and cast (a film's own pop-up asks for everything)
LIST_FIELDS = ('Overview,Genres,OfficialRating,CommunityRating,ProductionYear,PremiereDate,RunTimeTicks,ImageTags,'
               'BackdropImageTags,ParentBackdropImageTags,SeriesPrimaryImageTag,ChildCount,RecursiveItemCount,DateCreated,Path')
EXPORT_CACHE = os.path.join(APP_DIR, 'export-cache.json')
# what John has said goes inside which collection, each as ONE entry: box sets (several short films shown as one) and
# series. Put right at the start of every Update, so it's always so, whatever was run before (John, 8 Nov 2026: Frampton
# "should have been done, as it's been there from the start"). A box set inside another collection isn't listed on its own.
ARRANGE = {'The Criterion Collection': {
    'sets': ['A Hollis Frampton Odyssey', 'By Brakhage: An Anthology, Volumes One and Two', 'Martha Graham: Dance on Film', 'Scorsese Shorts',
             'Symbiopsychotaxiplasm: Two Takes by William Greaves', 'The Kennedy Films of Robert Drew & Associates', 'The Signifyin’ Works of Marlon Riggs'],
    'series': ['The Underground Railroad', 'Tanner 88', 'Fishing with John']}}
INSTALLED = os.path.join(APP_DIR, 'installed.json')   # which commit of the Mac's Grifflix files this is (the admin page's Update the Mac)
REPO = 'cooperking/cooperking.github.io'
# the Mac's Grifflix files, where each is kept: (in the repository's _grifflix/mac/, on this Mac, what must be in it)
SOFTWARE = [('grifflix-gateway.py', os.path.join(APP_DIR, 'grifflix-gateway.py'), 'Grifflix gateway'),
            ('convert.py', os.path.expanduser('~/Grifflix/convert.py'), 'convert'),
            ('motd-fill.py', os.path.expanduser('~/Grifflix/motd-fill.py'), 'Movie of the Day'),
            ('fix-series.py', os.path.expanduser('~/Grifflix/fix-series.py'), ''),
            ('plex-collections.py', os.path.expanduser('~/Grifflix/plex-collections.py'), ''),
            ('streaming-report.py', os.path.expanduser('~/Grifflix/streaming-report.py'), '')]   # what John's last Update sent (so the next reads only what's new)
LIBS = {'movies': ('Movies', 'movies'), 'tv': ('TV', 'tvshows'), 'home': ('Home videos', 'homevideos')}


def log(*a):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


CONV_SEEN = {}   # (files convert.py made or kept, looked at once: path → what they are)
# Play on the TV (John, 11 Nov 2026: "Is it possible for the fire stick to receive a cast film ... Build that"): each member's Fire
# TVs with Grifflix open, kept in memory (a TV that's gone is forgotten), and what their phones ask them to do
TVS = {}   # uid → dev → {name, seen, state, at, q}
TVC = threading.Condition()


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def tls():
    """Python from python.org (an old Mac's) has no certificates of its own until certifi is installed."""
    import ssl
    if os.environ.get('SSL_CERT_FILE'):   # (a certificate list chosen for this machine)
        return ssl.create_default_context(cafile=os.environ['SSL_CERT_FILE'])
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# ---------------------------------------------------------------- Jellyfin

class Jellyfin:
    def __init__(self, base, token=''):
        u = urllib.parse.urlparse(base)
        self.host, self.port, self.token = u.hostname, u.port or 8096, token

    def auth_header(self):
        h = 'MediaBrowser Client="Grifflix", Device="Grifflix gateway", DeviceId="grifflix-gateway", Version="%s"' % VERSION
        return h + (', Token="%s"' % self.token if self.token else '')

    def conn(self, timeout=30):
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def call(self, method, path, body=None, query=None):
        if query:
            path += ('&' if '?' in path else '?') + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        c = self.conn()
        data = json.dumps(body).encode() if body is not None else None
        c.request(method, path, body=data, headers={'Authorization': self.auth_header(), 'Content-Type': 'application/json'})
        r = c.getresponse()
        raw = r.read()
        c.close()
        if r.status >= 400:
            raise RuntimeError('Jellyfin %s %s: %s %s' % (method, path, r.status, raw[:200]))
        return pascal(json.loads(raw)) if raw and raw[:1] in b'[{' else None


def pascal(o):
    """Jellyfin 10 answers in PascalCase, newer ones in camelCase: everything here reads PascalCase."""
    if isinstance(o, list):
        return [pascal(v) for v in o]
    if isinstance(o, dict):
        return {(k[:1].upper() + k[1:] if k and k[0].islower() else k): (pascal(v) if k not in ('ImageTags', 'imageTags') else v) for k, v in o.items()}
    return o


# ---------------------------------------------------------------- setup

def wait_for_jellyfin(jf, secs=180):
    end = time.time() + secs
    while time.time() < end:
        try:
            info = jf.call('GET', '/System/Info/Public')
            if info:
                return info
        except Exception:
            pass
        time.sleep(3)
    sys.exit('Jellyfin did not answer on %s:%s.' % (jf.host, jf.port))


def setup(args):
    opts = dict(zip(args[::2], args[1::2]))
    st = load(SETTINGS, {})
    st.setdefault('jellyfin', 'http://127.0.0.1:8096')
    st.setdefault('port', 8097)
    st.setdefault('secret', secrets.token_hex(32))
    jf = Jellyfin(st['jellyfin'])
    info = wait_for_jellyfin(jf)
    log('Jellyfin', info.get('Version'), 'is up')
    if not info.get('StartupWizardCompleted'):
        jf.call('POST', '/Startup/Configuration', {'UICulture': 'en-GB', 'MetadataCountryCode': 'GB', 'PreferredMetadataLanguage': 'en'})
        jf.call('GET', '/Startup/User')   # (makes the first user)
        st['jf_password'] = secrets.token_urlsafe(18)
        jf.call('POST', '/Startup/User', {'Name': 'grifflix', 'Password': st['jf_password']})
        jf.call('POST', '/Startup/RemoteAccess', {'EnableRemoteAccess': False, 'EnableAutomaticPortMapping': False})
        jf.call('POST', '/Startup/Complete')
        log('Jellyfin first-run setup done (its own user: grifflix)')
    if opts.get('--jf-user') and os.environ.get('JF_PASSWORD'):   # a Jellyfin set up before Grifflix: its own login
        st['jf_user'], st['jf_password'] = opts['--jf-user'], os.environ['JF_PASSWORD']
    if not st.get('jf_password'):
        log('Jellyfin was set up before Grifflix: run again with --jf-user NAME and JF_PASSWORD set (its login).')
        sys.exit(3)
    try:
        a = jf.call('POST', '/Users/AuthenticateByName', {'Username': st.get('jf_user', 'grifflix'), 'Pw': st['jf_password']})
    except RuntimeError as e:
        log('Jellyfin turned that login down:', e)
        sys.exit(4)
    st['jf_token'], st['jf_user_id'] = a['AccessToken'], a['User']['Id']
    jf.token = st['jf_token']
    # Each of Grifflix's three shelves is a Jellyfin library of the right kind: one already there is used (the one
    # holding the folder chosen, else the first), the folder added to it if it hasn't it; else one is made.
    lib_ids = dict(st.get('libraries') or {})
    for key, (name, ctype) in LIBS.items():
        have = [v for v in (jf.call('GET', '/Library/VirtualFolders') or []) if (v.get('CollectionType') or '') == ctype]
        path = opts.get('--' + key)
        path = os.path.abspath(os.path.expanduser(path)) if path else None
        lib = next((v for v in have if path and path in (v.get('Locations') or [])), None) \
            or next((v for v in have if v.get('ItemId') == lib_ids.get(key)), None) or (have[0] if have else None)
        if lib and path and path not in (lib.get('Locations') or []):
            jf.call('POST', '/Library/VirtualFolders/Paths', {'Name': lib['Name'], 'PathInfo': {'Path': path}}, {'refreshLibrary': 'true'})
        if not lib and path:
            jf.call('POST', '/Library/VirtualFolders', {'LibraryOptions': {'EnableRealtimeMonitor': True, 'PathInfos': [{'Path': path}]}},
                    {'name': name, 'collectionType': ctype, 'refreshLibrary': 'true'})
            lib = next((v for v in (jf.call('GET', '/Library/VirtualFolders') or []) if v['Name'] == name), None)
        if lib:
            lib_ids[key] = lib['ItemId']
            log('Library %s (Jellyfin’s “%s”): %s' % (key, lib['Name'], ', '.join((lib.get('Locations') or []) + ([path] if path and path not in (lib.get('Locations') or []) else []))))
    try:   # the Mac's own video chip for converting (VideoToolbox): Apple silicon, or --hwaccel yes on an Intel Mac that has
        # it (2012 on). The 2010 Mac mini's chip can't, and converting is left to the processor (and mostly avoided).
        import platform
        enc = jf.call('GET', '/System/Configuration/encoding')
        if sys.platform == 'darwin' and (platform.machine() == 'arm64' or opts.get('--hwaccel') == 'yes') and enc.get('HardwareAccelerationType') != 'videotoolbox':
            enc['HardwareAccelerationType'] = 'videotoolbox'
            enc['EnableHardwareEncoding'] = True
            jf.call('POST', '/System/Configuration/encoding', enc)
            log('Hardware converting on (VideoToolbox)')
    except Exception as e:
        log('Could not set hardware converting:', e)
    st['libraries'] = lib_ids
    save(SETTINGS, st)
    log('Saved', SETTINGS)


# ---------------------------------------------------------------- the gateway's own store

class Store:
    """Members' calendars, playlists and progress, in one JSON file (a family's worth fits easily)."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        self.d = load(path, {})
        self.d.setdefault('users', {})
        self.d.setdefault('calendars', {})
        self.d['calendars'].setdefault('motd', {'name': 'Movie of the Day', 'owner': 'owner', 'days': {}, 'notes': {}, 'shared': True})
        self.dirty = False
        threading.Thread(target=self._saver, daemon=True).start()

    def _saver(self):
        while True:
            time.sleep(5)
            if self.dirty:
                with self.lock:
                    self.dirty = False
                    save(self.path, self.d)

    def user(self, uid, name):
        u = self.d['users'].setdefault(uid, {'lists': {}, 'progress': {}, 'hearts': []})
        u['name'] = name
        return u

    def touch(self):
        self.dirty = True


# ---------------------------------------------------------------- signed links

def b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip('=')


def make_key(secret, uid):
    exp = int(time.time()) + KEY_HOURS * 3600
    body = '%s.%d' % (b64(uid.encode()), exp)
    return body + '.' + b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()[:18])


def check_key(secret, key):
    try:
        u, exp, sig = key.split('.')
        body = u + '.' + exp
        if not hmac.compare_digest(sig, b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()[:18])):
            return None
        if int(exp) < time.time():
            return None
        return base64.urlsafe_b64decode(u + '=' * (-len(u) % 4)).decode()
    except Exception:
        return None


# ---------------------------------------------------------------- shaping Jellyfin's items for the page

def ticks_to_min(t):
    return round((t or 0) / 600000000) or None


def london_now():
    try:
        from zoneinfo import ZoneInfo
        import datetime
        return datetime.datetime.now(ZoneInfo('Europe/London'))
    except Exception:
        import datetime
        return datetime.datetime.now()


def revealed(day):
    """A Movie of the Day is a surprise until 8am (UK) on its day, when the alert goes out (John, 8 Nov 2026: "I don't
    want to see the film on that day until I get an alert at 8am - unless I override it in admin")."""
    now = london_now()
    today = now.strftime('%Y-%m-%d')
    return day < today or (day == today and now.hour >= 8)


def shown(items):   # a series with nothing in it (an empty folder, e.g. "untitled folder") isn't shown anywhere
    return [i for i in items if not (i.get('Type') == 'Series' and not i.get('ChildCount') and not i.get('RecursiveItemCount'))]


def item_out(it, prog=None):
    t = it.get('Type')
    o = {'id': it['Id'], 'name': it.get('Name'), 'type': {'Movie': 'movie', 'Series': 'series', 'Season': 'season',
         'Episode': 'episode', 'Video': 'video', 'Photo': 'photo', 'Folder': 'folder', 'PhotoAlbum': 'folder',
         'CollectionFolder': 'folder', 'BoxSet': 'collection'}.get(t, (t or '').lower())}
    for src, dst in (('ProductionYear', 'year'), ('Overview', 'overview'), ('OfficialRating', 'cert'),
                     ('CommunityRating', 'rating'), ('SeriesName', 'series'), ('SeriesId', 'seriesId'),
                     ('SeasonId', 'seasonId'), ('ParentIndexNumber', 'season'), ('IndexNumber', 'ep'),
                     ('ChildCount', 'children'), ('PremiereDate', 'premiere'), ('DateCreated', 'added')):
        if it.get(src) not in (None, '', []):
            o[dst] = it[src]
    if it.get('Genres'):
        o['genres'] = it['Genres'][:4]
    if it.get('Taglines'):
        o['tagline'] = it['Taglines'][0]
    if it.get('RunTimeTicks'):
        o['mins'] = ticks_to_min(it['RunTimeTicks'])
        o['secs'] = round(it['RunTimeTicks'] / 1e7)
    tags = it.get('ImageTags') or {}
    if 'Primary' in tags:
        o['poster'] = it['Id']
    elif it.get('SeriesPrimaryImageTag') and it.get('SeriesId'):
        o['poster'] = it['SeriesId']
    if 'Thumb' in tags:
        o['thumb'] = it['Id']
    if it.get('BackdropImageTags'):
        o['backdrop'] = it['Id']
    elif it.get('ParentBackdropItemId') and it.get('ParentBackdropImageTags'):
        o['backdrop'] = it['ParentBackdropItemId']
    if it.get('People'):
        o['cast'] = [p['Name'] for p in it['People'] if p.get('Type') == 'Actor'][:6]
        o['directors'] = [p['Name'] for p in it['People'] if p.get('Type') == 'Director'][:2]
    yt = []
    for t in it.get('RemoteTrailers') or []:   # trailers Jellyfin found (TMDB's, on YouTube): played from YouTube, not the Mac
        m = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([\w-]{11})', t.get('Url') or '')
        if m and m.group(1) not in yt:
            yt.append(m.group(1))
    if yt:
        o['trailers'] = yt[:3]
    if prog and it['Id'] in prog:
        o['pos'] = prog[it['Id']].get('pos')
    return o


def pic_tags(it):
    """Which version of each of an item's pictures it has (Jellyfin's image tags), so the site's copy of a picture is
    made again only when the picture changes: {'poster': tag, 'backdrop': tag, 'thumb': tag}, matching item_out's ids."""
    tags, t = it.get('ImageTags') or {}, {}
    if 'Primary' in tags:
        t['poster'] = tags['Primary']
    elif it.get('SeriesPrimaryImageTag'):
        t['poster'] = it['SeriesPrimaryImageTag']
    if 'Thumb' in tags:
        t['thumb'] = tags['Thumb']
    if it.get('BackdropImageTags'):
        t['backdrop'] = it['BackdropImageTags'][0]
    elif it.get('ParentBackdropImageTags'):
        t['backdrop'] = it['ParentBackdropImageTags'][0]
    return {k: v[:12] for k, v in t.items()}


# ---------------------------------------------------------------- the server

class Gateway:
    def __init__(self):
        self.st = load(SETTINGS, {})
        if not self.st.get('jf_token'):
            sys.exit('Not set up yet: run  grifflix-gateway.py setup --movies DIR --tv DIR --home DIR')
        self.jf = Jellyfin(self.st['jellyfin'], self.st['jf_token'])
        self.uid = self.st['jf_user_id']
        self.store = Store(DATA)
        self.me_cache, self.me_lock = {}, threading.Lock()
        self.origins = ORIGINS + self.st.get('extra_origins', [])
        self.pub_cache, self.home_paths, self.home_at = {}, [], 0
        self.list_cache = {}
        self.plays, self.plays_lock, self.logdir = {}, threading.Lock(), None   # (what's playing now, and the media server's logs: whether the Mac is converting)

    def is_public_set(self, iid):
        """A collection is for anyone to look at when anything in it is."""
        if ('set', iid) not in self.pub_cache:
            r = self.items(ParentId=iid, Limit=50, Fields='Path')
            self.pub_cache[('set', iid)] = any(self.is_public(i['Id'], i.get('Path')) for i in r.get('Items', []))
        return self.pub_cache[('set', iid)]

    def is_public(self, iid, path=None):
        """Films and TV are for anyone to look at (their pictures and trailers); home videos only for members. A list's
        item brings its own path (no asking Jellyfin again for each: 956 one by one took the Criterion Collection 48 s)."""
        if iid in self.pub_cache:
            return self.pub_cache[iid]
        if time.time() - self.home_at > 600:
            try:
                self.home_paths = [l for v in self.jf.call('GET', '/Library/VirtualFolders') or [] if (v.get('CollectionType') or '') == 'homevideos'
                                   for l in v.get('Locations') or []]
                self.home_at = time.time()
            except Exception:
                return False
        if not self.home_paths:   # no home videos library (John has none): everything is films and TV, for anyone to look at
            return True
        try:
            path = path or (self.one(iid) or {}).get('Path') or ''
        except Exception:
            return False
        ok = bool(path) and not any(path == h or path.startswith(h.rstrip('/') + '/') for h in self.home_paths)
        if len(self.pub_cache) > 20000:
            self.pub_cache.clear()
        self.pub_cache[iid] = ok
        return ok

    # --- who's asking
    def member(self, token):
        if not token:
            return None
        h = hashlib.sha256(token.encode()).hexdigest()
        with self.me_lock:
            hit = self.me_cache.get(h)
        if hit and hit[0] > time.time():
            return hit[1]
        test = self.st.get('test_members', {}).get(token)   # (for trying it out with no Worker: settings test_members)
        if test:
            me = test
        else:
            try:
                u = urllib.parse.urlparse(WORKER)
                c = http.client.HTTPSConnection(u.hostname, timeout=15, context=tls())
                c.request('GET', '/g/me', headers={'Authorization': 'Bearer ' + token, 'User-Agent': 'Grifflix gateway'})   # (Grifflix's own members, on the Worker: apart from Song of the Day's)
                r = c.getresponse()
                body = r.read()
                c.close()
                if r.status != 200:
                    with self.me_lock:
                        self.me_cache[h] = (time.time() + 60, None)
                    return None
                j = json.loads(body)
                me = {'name': j.get('name') or 'Member', 'owner': bool(j.get('owner')), 'id': j.get('id')}
            except Exception as e:
                log('Worker check failed:', e)
                return hit[1] if hit else None   # (the Worker out of reach: someone checked before keeps their way in)
        me['uid'] = 'owner' if me.get('owner') else ('g:' + me['id'] if me.get('id') else 'n:' + me['name'].strip().lower())
        with self.me_lock:
            self.me_cache[h] = (time.time() + ME_CACHE_S, me)
        return me

    # --- Jellyfin helpers
    def items(self, **q):
        """Jellyfin's lists, kept for up to 6 hours: the old Mac takes seconds to list 2,000 films, and they rarely
        change (John, 7 Nov 2026: "is it necessary to load them fresh like that every time?"). Forgotten at once by the
        page's Refresh button, when a collection is changed here, or files are converted."""
        q.setdefault('Fields', LIST_FIELDS)
        q.setdefault('EnableImageTypes', 'Primary,Backdrop,Thumb')
        key = json.dumps(q, sort_keys=True, default=str)
        hit = self.list_cache.get(key)
        if hit and time.time() - hit[0] < 6 * 3600:
            return hit[1]
        r = self.jf.call('GET', '/Users/%s/Items' % self.uid, query=q) or {}
        if len(self.list_cache) > 400:
            self.list_cache.clear()
        self.list_cache[key] = (time.time(), r)
        return r

    def now_id(self, iid):
        """A film's id now: convert.py's new file has a new one, and the site's copy keeps the old until John's Update."""
        return (self.store.d.get('remap') or {}).get(iid, iid)

    def watched(self):
        """A note for convert.py that someone is watching through Grifflix (the media server's own list of who's watching never shows
        Grifflix's films, so the converter didn't pause for them: John, 12 Nov 2026, a film stalling at 00:30 while it converted)."""
        try:
            f = os.path.expanduser('~/Grifflix/watching')
            with open(f, 'a'):
                pass
            os.utime(f, None)
        except Exception:
            pass

    def encodes(self):
        """What the media server's converter (ffmpeg) is doing now, from its own logs: for each film being streamed, whether the
        picture and sound are copied as they are or converted (and to what), and how fast against the film (1x keeps up)
        (John, 12 Nov 2026: "How do I know a film isn't being converted by the Mac mini?")."""
        if self.logdir is None:
            try:
                self.logdir = (self.jf.call('GET', '/System/Info') or {}).get('LogPath') or ''
            except Exception:
                self.logdir = ''
            if not self.logdir:
                for d in ('~/.local/share/jellyfin/log', '~/Library/Application Support/jellyfin/log', '~/.config/jellyfin/log'):
                    if os.path.isdir(os.path.expanduser(d)):
                        self.logdir = os.path.expanduser(d)
                        break
        out, now = [], time.time()
        try:
            names = os.listdir(self.logdir) if self.logdir else []
        except Exception:
            names = []
        for n in names:
            if not n.startswith('FFmpeg.') or 'Transcode' not in n and 'Remux' not in n and 'DirectStream' not in n:
                continue
            f = os.path.join(self.logdir, n)
            try:
                st = os.stat(f)
                if now - st.st_mtime > 40:   # (finished, or stopped)
                    continue
                with open(f, 'rb') as fh:
                    head = fh.read(24000).decode('utf-8', 'replace')
                    fh.seek(max(0, st.st_size - 6000))
                    tail = fh.read().decode('utf-8', 'replace')
            except Exception:
                continue
            m = re.search(r'[0-9a-f]{32}', n)
            vc = re.search(r'-(?:codec|c):v(?::0)?\s+(\S+)', head)
            ac = re.search(r'-(?:codec|c):a(?::0)?\s+(\S+)', head)
            sp = re.findall(r'speed=\s*([\d.]+)x', tail)
            out.append({'id': m.group(0) if m else '', 'v': vc.group(1) if vc else '', 'a': ac.group(1) if ac else '',
                        'speed': float(sp[-1]) if sp else None, 'at': st.st_mtime})
        return sorted(out, key=lambda e: -e['at'])

    def play_state(self, p, encs):
        """In words: is this film playing as it is, or being converted, and why."""
        o = {'who': p['who'], 'film': p['name'], 'device': p.get('kind') or '', 'src': p.get('srcdesc') or ''}
        if p['mode'] == 'direct':
            o.update(how='asis', say='Playing as it is: the whole file, straight from the Mac mini')
            return o
        e = next((x for x in encs if x['id'] in (p['iid'], p.get('msid'))), None)
        if not e:
            o.update(how='wait', say='Starting…')
            return o
        sound = '' if e['a'] in ('', 'copy') else ' (the sound made stereo, which is light work)'
        if e['v'] == 'copy':
            o.update(how='asis', say='Playing as it is: the picture untouched, only repackaged' + sound)
            return o
        src = p.get('src') or {}
        if src.get('v') not in ('h264', 'hevc', 'h265'):
            why = 'its format (%s) isn’t one devices play' % (src.get('v') or '?').upper()
        elif src.get('v') in ('hevc', 'h265') and not p.get('hevc'):
            why = 'this device can’t play HEVC'
        elif src.get('v') in ('hevc', 'h265') and src.get('bits', 8) > 8 and not p.get('hevc10'):
            why = 'this device can’t play 10-bit HEVC'
        elif src.get('bits', 8) > 8:
            why = '10-bit H.264, which devices can’t play'
        elif src.get('mbps') and src['mbps'] * 1e6 > p.get('br', 2e7):
            why = 'it’s over this device’s rate (%s Mbps)' % src['mbps']
        else:
            why = 'the media server chose to'
        enc = {'libx264': 'H.264', 'h264_videotoolbox': 'H.264 (with the Mac’s graphics chip)', 'libx265': 'HEVC'}.get(e['v'], e['v'])
        o.update(how='conv', why=why, speed=e['speed'],
                 say='Converted by the Mac mini as it plays, to %s: %s' % (enc, why) + sound)
        return o

    def forget(self):
        self.list_cache.clear()
        self.pub_cache.clear()

    def one(self, iid):
        return self.jf.call('GET', '/Users/%s/Items/%s' % (self.uid, iid), query={'Fields': FIELDS})

    def summaries(self, ids, prog):
        ids = [i for i in ids if i]
        if not ids:
            return {}
        return {it['Id']: item_out(it, prog) for it in self.by_ids(ids)}

    def by_ids(self, ids):   # 100 at a time: a year of Movie of the Day in one address is too long for Jellyfin
        ids = sorted(set(i for i in ids if i))
        out = []
        for n in range(0, len(ids), 100):
            out += self.items(Ids=','.join(ids[n:n + 100])).get('Items', [])
        return out


def make_handler(g):
    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'Grifflix'

        def log_message(self, fmt, *a):
            pass

        # --- answering
        def cors(self):
            o = self.headers.get('Origin')
            if o and (o in g.origins or re.match(r'^http://(localhost|127\.0\.0\.1)(:\d+)?$', o)):
                self.send_header('Access-Control-Allow-Origin', o)
                self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type, Range')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Expose-Headers', 'Content-Length, Content-Range')
                self.send_header('Access-Control-Max-Age', '86400')

        def send_json(self, status, obj):
            raw = json.dumps(obj).encode()
            self.send_response(status)
            self.cors()
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self):
            self.send_response(204)
            self.cors()
            self.send_header('Content-Length', '0')
            self.end_headers()

        def body(self):
            return getattr(self, '_b', None) or {}

        def take_body(self, method):
            """A POST's body read in full as the request arrives, before anything can refuse it: one refused unread left its
            body on the connection, and the next request on it came out garbled (John, 10 Nov 2026: "yes, fix that")."""
            self._b = {}
            if method != 'POST':
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = -1
            if n < 0 or n > 1_000_000:   # (too big, or no length given: not read, and the connection closed after the answer)
                self.close_connection = True
                return
            raw = self.rfile.read(n) if n else b''
            try:
                v = json.loads(raw or b'{}')
                self._b = v if isinstance(v, dict) else {}
            except ValueError:
                self._b = {}

        def who(self):
            tok = (self.headers.get('Authorization') or '').replace('Bearer ', '').strip()
            return g.member(tok)

        def do_GET(self):
            self.route('GET')

        def do_HEAD(self):
            self.route('HEAD')

        def do_POST(self):
            self.route('POST')

        def route(self, method):
            self.take_body(method)
            u = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            try:
                if u.path == '/health':
                    return self.send_json(200, {'ok': True, 'version': VERSION, 'sha': (load(INSTALLED, {}).get('sha') or '')[:7]})
                if u.path.startswith('/dl/'):   # a film's file, to keep on a phone or computer (while John allows it)
                    uid = check_key(g.st['secret'], q.pop('g', ''))
                    if not uid:
                        return self.send_json(403, {'error': 'That link has run out. Reload the page.'})
                    if not g.store.d.get('settings', {}).get('downloads'):
                        return self.send_json(403, {'error': 'Downloads are off just now.'})
                    return self.download(u.path[4:], method)
                if u.path.startswith('/img/') or u.path.startswith('/v/'):
                    uid = check_key(g.st['secret'], q.pop('g', ''))
                    if not uid:
                        return self.send_json(403, {'error': 'That link has run out. Reload the page.'})
                    if u.path.startswith('/img/'):
                        return self.image(u.path[5:], q)
                    return self.video(u.path[3:], q, method)
                if u.path.startswith('/pimg/'):   # pictures of films and TV, for anyone (Cloudflare keeps copies: .jpg, public)
                    m = re.match(r'^/pimg/([0-9a-f]{32})/(Primary|Backdrop|Thumb)\.jpg$', u.path)
                    if not m or not (g.is_public(m.group(1)) or g.is_public_set(m.group(1))):
                        return self.send_json(404, {'error': 'Not here.'})
                    return self.image(m.group(1) + '/' + m.group(2), q, cache='public, max-age=604800')
                if u.path.startswith('/api/public/'):   # anyone: Movie of the Day, the films and TV, trailers; not playing
                    fn = getattr(self, 'pub_' + u.path[12:].replace('/', '_'), None)
                    if not fn or method != 'GET':
                        return self.send_json(404, {'error': 'Not here.'})
                    return fn(q)
                if u.path == '/local/remap' and method == 'POST':   # convert.py on this Mac: a film's new file has a new id; calendars, lists and progress follow it
                    if not hmac.compare_digest(self.headers.get('X-Grifflix-Secret', ''), g.st['secret']):
                        return self.send_json(403, {'error': 'Not here.'})
                    m = {str(k): str(v) for k, v in (self.body().get('map') or {}).items() if re.match(r'^[0-9a-f]{32}$', str(k)) and re.match(r'^[0-9a-f]{32}$', str(v))}
                    n = 0
                    with g.store.lock:
                        for c in g.store.d['calendars'].values():
                            for d, i in list(c.get('days', {}).items()):
                                if i in m:
                                    c['days'][d] = m[i]; n += 1
                        for usr in g.store.d['users'].values():
                            usr['hearts'] = [m.get(i, i) for i in usr.get('hearts', [])]
                            for pl in usr.get('lists', {}).values():
                                pl['items'] = [m.get(i, i) for i in pl.get('items', [])]
                            for old, new in m.items():
                                if old in usr.get('progress', {}):
                                    usr['progress'][new] = usr['progress'].pop(old); n += 1
                        old = g.store.d.setdefault('remap', {})   # (kept: the site's copy has the old ids until John's next Update)
                        for k in list(old):
                            if old[k] in m:
                                old[k] = m[old[k]]
                        old.update(m)
                        g.store.touch()
                    g.forget()
                    return self.send_json(200, {'ok': True, 'changed': n})
                if u.path == '/local/calendar' and method == 'POST':   # motd-fill.py on this Mac: many Movie of the Day days at once (empty days only)
                    if not hmac.compare_digest(self.headers.get('X-Grifflix-Secret', ''), g.st['secret']):
                        return self.send_json(403, {'error': 'Not here.'})
                    body = self.body()
                    days = {str(d): str(i) for d, i in (body.get('days') or {}).items() if re.match(r'^\d{4}-\d\d-\d\d$', str(d)) and re.match(r'^[0-9a-f]{32}$', str(i))}
                    rf, rt = str(body.get('replace_from') or ''), str(body.get('replace_to') or '')
                    with g.store.lock:
                        c = g.store.d['calendars']['motd']
                        if rf and rt:   # dealt again: the days in that range replaced (John, 8 Nov 2026: "randomise all choices so far again")
                            old = len([d for d in c['days'] if rf <= d <= rt])
                            c['days'] = {d: i for d, i in c['days'].items() if not (rf <= d <= rt)}
                            c['days'].update(days)
                            g.store.touch()
                            return self.send_json(200, {'ok': True, 'added': len(days), 'replaced': old})
                        new = {d: i for d, i in days.items() if d not in c['days']}
                        c['days'].update(new)
                        g.store.touch()
                    return self.send_json(200, {'ok': True, 'added': len(new), 'kept': len(days) - len(new)})
                if not u.path.startswith('/api/'):
                    return self.send_json(404, {'error': 'Not here.'})
                me = self.who()
                if not me:
                    return self.send_json(401, {'error': 'Sign in to Grifflix first.'})
                fn = getattr(self, 'api_' + u.path[5:].replace('/', '_'), None)
                if not fn:
                    return self.send_json(404, {'error': 'Not here.'})
                with g.store.lock:
                    user = g.store.user(me['uid'], me['name'])
                return fn(me, user, q, self.body() if method == 'POST' else {})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                log('Error on', self.path.split('?')[0], repr(e))
                try:
                    self.send_json(502, {'error': 'The Mac mini had a problem with that.'})
                except Exception:
                    pass

        # --- the API
        def api_me(self, me, user, q, b):
            libs = {}
            for key, lid in g.st.get('libraries', {}).items():
                try:
                    libs[key] = g.items(ParentId=lid, Recursive='true', IncludeItemTypes={'movies': 'Movie', 'tv': 'Series', 'home': 'Video,Photo'}[key], Limit=0).get('TotalRecordCount', 0)
                except Exception:
                    libs[key] = 0
            self.send_json(200, {'name': me['name'], 'owner': me['owner'], 'uid': me['uid'], 'key': make_key(g.st['secret'], me['uid']),
                                 'keyHours': KEY_HOURS, 'libraries': libs, 'downloads': bool(g.store.d.get('settings', {}).get('downloads'))})

        def api_home(self, me, user, q, b):
            prog = user['progress']
            going = sorted(((v.get('at', 0), k) for k, v in prog.items() if v.get('pos', 0) > 60 and not v.get('done')), reverse=True)[:20]
            S = g.summaries([k for _, k in going], prog)
            rows = {'continue': [S[k] for _, k in going if k in S]}
            for key, lid in g.st.get('libraries', {}).items():
                types = {'movies': 'Movie', 'tv': 'Series', 'home': 'Video'}[key]
                r = g.items(ParentId=lid, Recursive='true', IncludeItemTypes=types, SortBy='DateCreated', SortOrder='Descending', Limit=24)
                rows[key] = [item_out(i, prog) for i in shown(r.get('Items', []))]
            self.send_json(200, rows)

        def api_items(self, me, user, q, b):
            key = q.get('lib', 'movies')
            lid = q.get('parent') or g.st.get('libraries', {}).get(key)
            if not lid:
                return self.send_json(200, {'items': [], 'total': 0})
            sort = {'name': 'SortName', 'added': 'DateCreated', 'year': 'ProductionYear,SortName', 'rating': 'CommunityRating'}.get(q.get('sort'), 'SortName')
            order = 'Ascending' if q.get('sort', 'name') == 'name' else 'Descending'
            if q.get('parent'):   # a folder of home videos and photos, as it is on the disk
                r = g.items(ParentId=lid, SortBy='IsFolder,SortName', SortOrder='Descending,Ascending', StartIndex=q.get('start', 0), Limit=q.get('limit', 200))
            else:
                types = {'movies': 'Movie', 'tv': 'Series', 'home': 'Video,Photo'}[key]
                r = g.items(ParentId=lid, Recursive='true', IncludeItemTypes=types, SortBy=sort, SortOrder=order,
                            Genres=q.get('genre'), StartIndex=q.get('start', 0), Limit=q.get('limit', 200))
            its = shown(r.get('Items', []))
            self.send_json(200, {'items': [item_out(i, user['progress']) for i in its], 'total': r.get('TotalRecordCount', 0) - (len(r.get('Items', [])) - len(its))})

        def api_item(self, me, user, q, b):
            it = g.one(g.now_id(q['id']))
            o = item_out(it, user['progress'])
            o['id'] = q['id']   # (as the site knows it)
            if it.get('Type') == 'Series':
                seasons = g.jf.call('GET', '/Shows/%s/Seasons' % it['Id'], query={'UserId': g.uid, 'Fields': 'Overview'}) or {}
                eps = g.jf.call('GET', '/Shows/%s/Episodes' % it['Id'], query={'UserId': g.uid, 'Fields': 'Overview,RunTimeTicks'}) or {}
                o['seasons'] = [item_out(s) for s in seasons.get('Items', [])]
                o['episodes'] = [item_out(e, user['progress']) for e in eps.get('Items', [])]
                nxt = next((e for e in o['episodes'] if not user['progress'].get(e['id'], {}).get('done')), None)
                if nxt:
                    o['next'] = nxt['id']
            ms = (it.get('MediaSources') or [{}])[0]
            o['subs'] = [{'index': s['Index'], 'lang': s.get('Language') or '', 'title': s.get('DisplayTitle') or s.get('Language') or 'Subtitles'}
                         for s in ms.get('MediaStreams', []) if s.get('Type') == 'Subtitle' and s.get('IsTextSubtitleStream')]
            o['hearted'] = o['id'] in user['hearts']
            self.send_json(200, o)

        # collections (John's, from Plex: Jellyfin's BoxSets), for members and visitors alike
        def collections_out(self, public):
            r = g.items(Recursive='true', IncludeItemTypes='BoxSet', SortBy='SortName')
            sets = r.get('Items', [])
            # a box set inside another collection (Martha Graham in the Criterion Collection) shows only there, as one
            # entry, not in the list of collections too (John, 7 Nov 2026)
            inside = set(i['Id'] for c in sets for i in g.items(ParentId=c['Id'], Fields='', EnableImageTypes='').get('Items', []) if i.get('Type') == 'BoxSet')
            return [item_out(i) for i in sets if i['Id'] not in inside and (not public or g.is_public_set(i['Id']))]

        def collection_out(self, iid, prog, public):
            c = g.one(iid)
            if not c or c.get('Type') != 'BoxSet':
                return None
            o = item_out(c)
            r = g.items(ParentId=iid, SortBy='ProductionYear,SortName')
            o['items'] = [item_out(i, prog) for i in r.get('Items', []) if not public or g.is_public(i['Id'], i.get('Path'))]
            return o

        def api_settings(self, me, user, q, b):   # John's: show him the films still to come (the admin page's switch)
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            st = g.store.d.setdefault('settings', {})
            if b:
                with g.store.lock:
                    st['peek'] = bool(b.get('peek'))
                    g.store.touch()
            self.send_json(200, {'peek': bool(st.get('peek'))})

        def api_export(self, me, user, q, b):
            """John's Update (the admin page): everything the site shows, for it to keep as its own files, as the songs'
            site keeps its calendar (John, 8 Nov 2026: "the calendar should not load from the Mac once it's been added";
            the Mac is then only for playing). Films and TV only: no home videos. The films still to come are in it
            (the Worker keeps them, and shows each at 8am on its day): only the days already revealed go on the site."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            # Only what's new or changed is read in full: the Mac remembers what it sent last time (each item's Jellyfin
            # Etag, which changes with its details or pictures). John, 8 Nov 2026: "almost every image and collection will
            # be the same; a few more films or series might be added". ?full=1 reads everything again.
            g.forget()
            arranged = self.arrange()
            extra = LIST_FIELDS + ',People,Taglines,RemoteTrailers'
            ep_fields = 'Overview,RunTimeTicks,ImageTags,ParentBackdropImageTags,SeriesPrimaryImageTag,DateCreated,PremiereDate,ProductionYear'
            quick = 'Etag,ChildCount,RecursiveItemCount,Path,ProviderIds'
            making = lambda i: '.grifflix-converting.' in (i.get('Path') or '')   # (a film being converted: not until it's ready)
            libs = g.st.get('libraries', {})
            old = {} if q.get('full') == '1' else (load(EXPORT_CACHE, {}).get('items') or {})
            kept, count = {}, {'new': 0, 'changed': 0, 'read': 0}
            items, lists = {}, {}

            def resolve(raw, fields, slim=False):
                """raw: the quick list (ids and Etags). Each one's full entry: remembered, or read now if new or changed."""
                need = [i['Id'] for i in raw if not i.get('Etag') or (old.get(i['Id']) or {}).get('e') != i.get('Etag')]
                full = {}
                for n in range(0, len(need), 100):
                    for it in g.items(Ids=','.join(need[n:n + 100]), Fields=fields).get('Items', []):
                        full[it['Id']] = it
                out = []
                for i in raw:
                    if i['Id'] in full:
                        o = item_out(full[i['Id']])
                        if slim:
                            o.pop('cast', None)
                            o.pop('directors', None)
                        o['tags'] = pic_tags(full[i['Id']])
                        count['new' if i['Id'] not in old else 'changed'] += 1
                        count['read'] += 1
                    elif i['Id'] in old:
                        o = old[i['Id']]['o']
                    else:
                        continue
                    kept[i['Id']] = {'e': i.get('Etag'), 'o': o}
                    out.append(o)
                return out
            for key, types in (('movies', 'Movie'), ('tv', 'Series')):
                if libs.get(key):
                    raw = [i for i in shown(g.items(ParentId=libs[key], Recursive='true', IncludeItemTypes=types, SortBy='SortName', Fields=quick).get('Items', [])) if not making(i)]
                    for o in resolve(raw, extra):
                        items[o['id']] = o
                    lists[key] = [i['Id'] for i in raw if i['Id'] in items]
                    if key == 'movies':
                        self.extras(raw, items, lists)
            sets = g.items(Recursive='true', IncludeItemTypes='BoxSet', SortBy='SortName', Fields=quick).get('Items', [])
            for o in resolve(sets, extra):
                items[o['id']] = o
            inside, members = set(), {}
            for c in sets:
                kids = [k for k in g.items(ParentId=c['Id'], SortBy='ProductionYear,SortName', Fields=quick).get('Items', []) if not making(k)]
                for o in resolve([k for k in kids if k['Id'] not in items], extra):
                    items[o['id']] = o
                members[c['Id']] = [k['Id'] for k in kids if k['Id'] in items]
                inside.update(k['Id'] for k in kids if k.get('Type') == 'BoxSet')
            lists['collections'] = [c['Id'] for c in sets if c['Id'] not in inside]
            series = {}
            if libs.get('tv'):
                eps = [x for x in g.items(ParentId=libs['tv'], Recursive='true', IncludeItemTypes='Episode', SortBy='SortName', Fields=quick).get('Items', []) if x.get('SeriesId') and not making(x)]
                ses = [x for x in g.items(ParentId=libs['tv'], Recursive='true', IncludeItemTypes='Season', Fields=quick).get('Items', []) if x.get('SeriesId')]
                sid = {x['Id']: x['SeriesId'] for x in eps + ses}   # (an episode Jellyfin hasn't put in a series: not shown)
                for o in resolve(ses, 'Overview,ImageTags', slim=True):
                    series.setdefault(sid[o['id']], {'seasons': [], 'episodes': []})['seasons'].append(o)
                for o in sorted(resolve(eps, ep_fields, slim=True), key=lambda e: (e.get('season') or 0, e.get('ep') or 0)):
                    series.setdefault(sid[o['id']], {'seasons': [], 'episodes': []})['episodes'].append(o)
                for v in series.values():
                    v['seasons'].sort(key=lambda s: s.get('ep') or 0)
            c = g.store.d['calendars']['motd']
            days = dict(c.get('days', {}))
            have = set(items) | set(e['id'] for v in series.values() for e in v['episodes'])
            ext = {i: x for i, x in c.get('ext', {}).items() if i in set(days.values())}
            missing = [i for i in set(days.values()) if i and i not in have and i not in ext]
            raw = [it for n in range(0, len(missing), 100) for it in g.items(Ids=','.join(missing[n:n + 100]), Fields=quick).get('Items', [])]
            for o in resolve(raw, ep_fields, slim=True):   # (a calendar day's film no longer in a library list)
                items[o['id']] = o
            # members' own things kept here so far (My list, playlists, their calendars): the Worker keeps them from now on
            eps_by = {e['id']: e for v in series.values() for e in v['episodes']}
            users = g.store.d.get('users', {})
            cals = {k: v for k, v in g.store.d['calendars'].items() if k != 'motd'}
            ids = set(i for u in users.values() for i in u.get('hearts', []) + [x for pl in u.get('lists', {}).values() for x in pl.get('items', [])])
            ids |= set(i for c2 in cals.values() for i in c2.get('days', {}).values())
            extra_items = {}
            for it in g.by_ids([i for i in ids if i not in items and i not in eps_by]):
                o = item_out(it)
                o['tags'] = pic_tags(it)
                extra_items[it['Id']] = o
            snap = lambda i: items.get(i) or eps_by.get(i) or extra_items.get(i)
            light = lambda o: {k: v for k, v in o.items() if k not in ('overview', 'cast', 'directors', 'trailers', 'tagline', 'genres')}
            personal = {'users': {uid: {'hearts': [light(snap(i)) for i in u.get('hearts', []) if snap(i)],
                                        'lists': {lid: {'name': pl.get('name'), 'items': [light(snap(i)) for i in pl.get('items', []) if snap(i)]} for lid, pl in u.get('lists', {}).items()}}
                                  for uid, u in users.items()},
                        'calendars': {cid: {'name': c2.get('name'), 'owner': c2.get('owner'), 'shared': c2.get('shared', True), 'notes': c2.get('notes', {}),
                                            'days': {d: light(snap(i)) for d, i in c2.get('days', {}).items() if snap(i)}} for cid, c2 in cals.items()}}
            count['removed'] = len([k for k in old if k not in kept])
            count['arranged'] = arranged
            try:
                save(EXPORT_CACHE, {'at': int(time.time()), 'items': kept})
            except OSError as e:
                log('Couldn’t keep the export:', e)
            log('Update: %d new, %d changed, %d gone (%d read in full)' % (count['new'], count['changed'], count['removed'], count['read']))
            self.send_json(200, {'v': 1, 'at': int(time.time()), 'items': items, 'lists': lists, 'sets': members, 'series': series, 'changes': count, 'personal': personal,
                                 'motd': {'days': days, 'notes': dict(c.get('notes', {})), 'ext': ext}})

        @staticmethod
        def extras(raw, items, lists):
            """A disc's extras that Jellyfin took for films (a featurette, an interview, a file named as it came: no film's
            details found for it, and no year or a file's name): not films in the lists or the search, but the extras of
            the film beside them in the same folder (or the folder above), played from that film's page (John, 11 Nov
            2026: "It should only search within actual films. And features/extras should be viewable from the film page")."""
            def ided(i):
                p = i.get('ProviderIds') or {}
                return any(p.get(k) for k in ('Tmdb', 'Imdb', 'Tvdb'))
            def filey(n):
                return ' ' not in n and bool(re.search(r'\w[._]\w|-[A-Za-z0-9]+$', n))
            words = lambda n: set(w for w in re.split(r'[^a-z0-9]+', (n or '').lower()) if len(w) > 2)
            for i in raw:   # (each time from scratch: what was kept last time isn't changed)
                if i['Id'] in items:
                    items[i['Id']] = {k: v for k, v in items[i['Id']].items() if k not in ('extra', 'of', 'extras')}
            films = [i for i in raw if i['Id'] in items and ided(i)]
            by_dir = {}
            for f in films:
                by_dir.setdefault(os.path.dirname(f.get('Path') or ''), []).append(f)
            for e in raw:
                o = items.get(e['Id'])
                if not o or ided(e) or not (not o.get('year') or filey(e.get('Name') or '')):
                    continue
                o['extra'] = 1
                d, parent = os.path.dirname(e.get('Path') or ''), None
                for _ in range(3):   # (its own folder, then up to two above: an Extras folder inside the film's)
                    near = by_dir.get(d) or []
                    if near:
                        parent = max(near, key=lambda f: len(words(f.get('Name')) & (words(e.get('Name')) | words(e.get('Path')))))
                        break
                    up = os.path.dirname(d)
                    if not up or up == d:
                        break
                    d = up
                if parent:
                    o['of'] = parent['Id']
                    items[parent['Id']].setdefault('extras', []).append(e['Id'])
            lists['movies'] = [i for i in lists.get('movies', []) if not items.get(i, {}).get('extra')]

        def api_library(self, me, user, q, b):
            """John's: every film as its file is, for the admin page's Library (John, 11 Nov 2026: "I need a place in my admin to see my
            library, so I can see what is good quality and what needs replacing"): its picture, format, rate, size and sound, whether
            it's been converted, and why it's waiting to be."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            lid = (g.st.get('libraries') or {}).get('movies')
            if not lid:
                return self.send_json(200, {'films': []})
            st = load(os.path.expanduser('~/Grifflix/convert-state.json'), {})
            made = {os.path.normcase(x.get('to') or ''): x for x in (st.get('done') or {}).values()}
            rows, start = [], 0
            while True:
                r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'MediaSources,Path,ProductionYear',
                                                                       'StartIndex': start, 'Limit': 300, 'SortBy': 'SortName', 'EnableImageTypes': 'Primary'}) or {}
                for it in r.get('Items') or []:
                    ms = (it.get('MediaSources') or [{}])[0]
                    ss = ms.get('MediaStreams') or []
                    v = next((x for x in ss if x.get('Type') == 'Video'), {}) or {}
                    au = [x for x in ss if x.get('Type') == 'Audio']
                    w, h, vc = v.get('Width') or 0, v.get('Height') or 0, (v.get('Codec') or '').lower()
                    bits = v.get('BitDepth') or 8
                    mbps = round((ms.get('Bitrate') or v.get('BitRate') or 0) / 1e6, 1)
                    why = ''
                    if w > 1928 or h > 1088:
                        why = 'bigger than 1080p'
                    elif mbps > 20:
                        why = 'over 20 Mbps'
                    elif not ((vc in ('hevc', 'h265') and bits <= 8 and '10' not in (v.get('Profile') or '')) or (vc == 'h264' and bits <= 8 and '10' not in (v.get('Profile') or ''))):
                        why = 'format browsers can’t play'
                    cv = made.get(os.path.normcase(it.get('Path') or ''))
                    rows.append({'id': it.get('Id'), 'n': it.get('Name'), 'y': it.get('ProductionYear') or '', 'w': w, 'h': h, 'v': vc, 'b': bits, 'mbps': mbps,
                                 'gb': round((ms.get('Size') or 0) / 1e9, 1), 'mins': round((it.get('RunTimeTicks') or 0) / 6e8),
                                 'a': ', '.join(sorted(set('%s %s' % ((x.get('Codec') or '?').upper(), {1: 'mono', 2: 'stereo', 6: '5.1', 8: '7.1'}.get(x.get('Channels') or 0, '%sch' % (x.get('Channels') or '?'))) for x in au[:4]))),
                                 'conv': bool(cv), 'was': (cv or {}).get('before'), 'why': '' if cv else why})
                start += 300
                if start >= (r.get('TotalRecordCount') or 0):
                    break
            self.send_json(200, {'films': rows})

        def api_households(self, me, user, q, b):
            """John's: who lives together (John, 11 Nov 2026: "Camilla and I share a house but the tv is signed in as me"): everyone in a
            household sees and works each other's TVs. GET lists them; POST {households: [{name, members: [uid]}]} keeps them."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            if b:
                hh = []
                for h in (b.get('households') or [])[:50]:
                    ms = sorted(set(str(m) for m in (h.get('members') or []) if re.match(r'^(owner|g:[\w-]{1,64}|n:.{1,60})$', str(m))))
                    if ms:
                        hid = str(h.get('id') or '') if re.match(r'^h[0-9a-f]{8}$', str(h.get('id') or '')) else 'h' + secrets.token_hex(4)
                        hh.append({'id': hid, 'name': str(h.get('name') or 'Home').strip()[:40] or 'Home', 'members': ms})
                with g.store.lock:
                    g.store.d['households'] = hh
                    g.store.touch()
            return self.send_json(200, {'households': g.store.d.get('households') or []})

        def api_household(self, me, user, q, b):
            """A member's own household (John, 11 Nov 2026: "Build household codes"): who's in it; a code (30 minutes, once) for someone
            they live with to join; joining with a code (leaving any other); leaving. John's admin page still sees and changes them all."""
            uid, op = me['uid'], str(b.get('op') or '')
            with g.store.lock:
                hh = g.store.d.setdefault('households', [])
                for h in hh:
                    h.setdefault('id', 'h' + secrets.token_hex(4))
                codes = g.store.d.setdefault('hhcodes', {})
                for c in [c for c, x in codes.items() if x.get('until', 0) < time.time()]:
                    codes.pop(c, None)
                mine = next((h for h in hh if uid in h.get('members', [])), None)
                out = {}
                if op == 'code':
                    if not mine:
                        mine = {'id': 'h' + secrets.token_hex(4), 'name': (me.get('name') or 'My').split(' ')[0] + '’s household', 'members': [uid]}
                        hh.append(mine)
                    code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
                    codes[code] = {'hid': mine['id'], 'until': time.time() + 1800, 'by': uid}
                    out = {'code': code, 'minutes': 30}
                elif op == 'join':
                    c = codes.pop(re.sub(r'[^A-Z0-9]', '', str(b.get('code') or '').upper()), None)
                    to = c and next((h for h in hh if h['id'] == c['hid']), None)
                    if not to:
                        g.store.touch()
                        return self.send_json(404, {'error': 'That code isn’t right, or it’s over 30 minutes old: ask for a new one.'})
                    for h in hh:
                        if uid in h['members'] and h is not to:
                            h['members'].remove(uid)
                    if uid not in to['members']:
                        to['members'].append(uid)
                    mine = to
                elif op == 'leave' and mine:
                    mine['members'].remove(uid)
                    mine = None
                elif op == 'rename' and mine:
                    mine['name'] = str(b.get('name') or '').strip()[:40] or mine['name']
                g.store.d['households'] = [h for h in hh if h.get('members')]
                if op:
                    g.store.touch()
                users = g.store.d.get('users') or {}
                if mine:
                    out['household'] = {'name': mine['name'], 'members': [(users.get(m) or {}).get('name') or ('John' if m == 'owner' else 'A member') for m in mine['members'] if m != uid]}
                else:
                    out['household'] = None
            return self.send_json(200, out)

        def housemates(self, uid):   # (the member and everyone in a household with them)
            out = {uid}
            for h in g.store.d.get('households') or []:
                if uid in h.get('members', []):
                    out.update(h['members'])
            return out

        def api_tvhere(self, me, user, q, b):
            """A Fire TV with Grifflix open: says it's there (and what it's playing), and waits up to 20 seconds for its phone's word."""
            dev = str(b.get('dev') or '')
            if not re.match(r'^[\w-]{6,40}$', dev):
                return self.send_json(400, {'error': 'Not here.'})
            wait = max(0.0, min(25.0, float(b.get('wait') or 0)))
            with TVC:
                t = TVS.setdefault(me['uid'], {}).setdefault(dev, {'q': []})
                t['name'], t['seen'] = str(b.get('name') or 'Fire TV')[:40], time.time()
                if 'state' in b:
                    t['state'], t['at'] = (b['state'] if isinstance(b['state'], dict) else None), time.time()
                end = time.time() + wait
                while wait and not t['q'] and time.time() < end:
                    TVC.wait(max(.1, end - time.time()))
                    t['seen'] = time.time()
                cmds, t['q'] = t['q'], []
            return self.send_json(200, {'cmds': cmds})

        def api_tvs(self, me, user, q, b):
            """The member's Fire TVs with Grifflix open just now, and what each is playing."""
            now = time.time()
            mates = self.housemates(me['uid'])   # (the household's TVs too: John, 11 Nov 2026)
            with TVC:
                tvs = [{'dev': d, 'name': t.get('name'), 'state': t.get('state'), 'ago': round(now - t.get('at', now))}
                       for u in mates for d, t in (TVS.get(u) or {}).items() if now - t.get('seen', 0) < 45]
            return self.send_json(200, {'tvs': tvs})

        def api_tvsend(self, me, user, q, b):
            """A phone's word to one of its member's Fire TVs: play a film (from a place), pause, carry on, go to, jump, stop."""
            dev, cmd = str(b.get('dev') or ''), str(b.get('cmd') or '')
            if cmd not in ('play', 'pause', 'resume', 'seek', 'skip', 'stop'):
                return self.send_json(400, {'error': 'Not here.'})
            c = {'cmd': cmd, 'at': time.time()}
            if cmd == 'play':
                if not re.match(r'^[0-9a-f]{32}$', str(b.get('id') or '')):
                    return self.send_json(400, {'error': 'Not here.'})
                c['id'] = g.now_id(str(b['id']))
            for k in ('pos', 'to', 's'):
                if k in b:
                    try:
                        c[k] = float(b[k])
                    except (TypeError, ValueError):
                        pass
            mates = self.housemates(me['uid'])
            with TVC:
                t = next((TVS[u][dev] for u in mates if dev in (TVS.get(u) or {})), None)
                if not t or time.time() - t.get('seen', 0) > 45:
                    return self.send_json(404, {'error': 'That TV isn’t on Grifflix just now: open Grifflix on it.'})
                t['q'] = (t['q'] + [c])[-10:]
                TVC.notify_all()
            return self.send_json(200, {'ok': True})

        def api_convert(self, me, user, q, b):
            """John's: how the conversions are going (convert.py's files in ~/Grifflix), for the admin page."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            home = os.path.expanduser('~/Grifflix')
            st = load(os.path.join(home, 'convert-state.json'), {})
            cur = load(os.path.join(home, 'convert-now.json'), None)
            alive = False
            if cur:
                try:
                    os.kill(int(cur.get('pid') or 0), 0)   # (still running?)
                    alive = cur.get('state') != 'finished'
                except (OSError, ValueError):
                    alive = False
            tail = []
            try:
                with open(os.path.join(home, 'convert.log'), 'rb') as f:
                    f.seek(0, 2)
                    f.seek(max(0, f.tell() - 6000))
                    tail = f.read().decode('utf-8', 'replace').splitlines()[-8:]
            except OSError:
                pass
            if not cur and tail:   # (a converter started before it wrote its progress: whether it's running, from the processes)
                try:
                    import subprocess
                    alive = 'convert.py' in subprocess.run(['ps', '-axo', 'command'], capture_output=True, text=True, timeout=5).stdout
                except Exception:
                    pass
            done = st.get('done') or {}
            # (each film's original and what it became, so John can see what the converting did: John, 11 Nov 2026: "what was the
            # original and what was it converted to?"; for films done before convert.py noted it, looked at now, once)
            def look(path):
                if not path or not os.path.exists(path):
                    return None
                if path in CONV_SEEN:
                    return CONV_SEEN[path]
                import shutil
                fp = next((c for c in ['/Applications/Jellyfin.app/Contents/MacOS/ffprobe', '/usr/local/bin/ffprobe', '/opt/homebrew/bin/ffprobe', shutil.which('ffprobe')] if c and os.path.exists(c)), None)
                if not fp:
                    return None
                try:
                    import subprocess
                    info = json.loads(subprocess.run([fp, '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', path], capture_output=True, text=True, timeout=20).stdout or '{}')
                except Exception:
                    return None
                v = next((x for x in info.get('streams') or [] if x.get('codec_type') == 'video' and not (x.get('disposition') or {}).get('attached_pic')), {})
                vc = (v.get('codec_name') or '?').replace('h264', 'H.264').replace('hevc', 'HEVC').replace('mpeg4', 'MPEG-4 (XviD/DivX)').replace('mpeg2video', 'MPEG-2').replace('vc1', 'VC-1')
                if '10' in (v.get('pix_fmt') or '') or '10' in (v.get('profile') or ''):
                    vc += ' 10-bit'
                CONV_SEEN[path] = {'v': vc, 'w': v.get('width') or 0, 'h': v.get('height') or 0, 'mbps': round(float((info.get('format') or {}).get('bit_rate') or 0) / 1e6, 1), 'gb': round(os.path.getsize(path) / 1e9, 1)}
                return CONV_SEEN[path]
            rows = sorted(done.values(), key=lambda x: -(x.get('at') or 0))[:60]
            self.send_json(200, {'now': cur, 'alive': alive, 'done': len(done), 'failed': [{'name': x.get('name'), 'why': x.get('why')} for x in (st.get('failed') or {}).values()][:20],
                                 'recent': [{'name': x.get('name'), 'at': x.get('at'), 'mins': x.get('mins'), 'why': x.get('why'),
                                             'before': x.get('before') or look(x.get('kept')), 'after': x.get('after') or look(x.get('to'))} for x in rows],
                                 'next': (cur or {}).get('next') or [], 'log': tail})

        def api_originals(self, me, user, q, b):
            """John's: the originals convert.py kept in "Grifflix Originals" (what they take, the drive's free space), and
            deleting them from the admin page (John, 8 Nov 2026: "can I delete from the app"). Only files convert.py moved
            there, and only inside a "Grifflix Originals" folder, can be deleted."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            st = load(os.path.expanduser('~/Grifflix/convert-state.json'), {})

            def safe(path):
                real = os.path.realpath(path or '')
                return '/Grifflix Originals/' in real and os.path.isfile(real) and real
            kept = []
            for iid, x in (st.get('done') or {}).items():
                real = safe(x.get('kept'))
                if real:
                    kept.append({'id': iid, 'name': x.get('name'), 'at': x.get('at'), 'size': os.path.getsize(real), 'path': real})
            if b.get('op') == 'delete':
                want = set(b.get('ids') or []) if not b.get('all') else set(k['id'] for k in kept)
                gone, freed = 0, 0
                for k in kept:
                    if k['id'] in want:
                        try:
                            os.remove(k['path'])
                            gone += 1
                            freed += k['size']
                            d = os.path.dirname(k['path'])   # (its now-empty folders, up to Grifflix Originals)
                            while '/Grifflix Originals/' in d + '/' and os.path.basename(d) != 'Grifflix Originals' and not os.listdir(d):
                                os.rmdir(d)
                                d = os.path.dirname(d)
                        except OSError as e:
                            log('Couldn’t delete an original:', e)
                log('Deleted %d originals (%.1f GB)' % (gone, freed / 1e9))
                kept = [k for k in kept if os.path.isfile(k['path'])]
            drive = os.path.dirname(kept[0]['path']) if kept else None
            if not drive:
                roots = [l for v in g.jf.call('GET', '/Library/VirtualFolders') or [] for l in v.get('Locations') or []]
                drive = roots[0] if roots else os.path.expanduser('~')
            try:
                import shutil
                du = shutil.disk_usage(drive)
                free, total = du.free, du.total
            except OSError:
                free = total = 0
            self.send_json(200, {'originals': [{k2: v for k2, v in k.items() if k2 != 'path'} for k in sorted(kept, key=lambda k: -(k['at'] or 0))],
                                 'size': sum(k['size'] for k in kept), 'free': free, 'total': total})

        def api_software(self, me, user, q, b):
            """John's: the Mac's Grifflix files brought up to date from GitHub (the admin page's Update the Mac; John,
            8 Nov 2026, instead of Terminal commands). Each file is checked (it must be Python, and what it should be)
            before any is replaced; the old ones are kept beside them (.prev). The converter, if it was running, starts
            again with its new file; then the gateway restarts (launchd starts it again at once)."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            have = load(INSTALLED, {})
            if b.get('op') != 'update':
                return self.send_json(200, {'sha': have.get('sha', ''), 'at': have.get('at', 0), 'files': have.get('files', [])})

            def fetch(url):
                req = urllib.request.Request(url, headers={'User-Agent': 'Grifflix gateway', 'Accept': 'application/vnd.github+json'})
                with urllib.request.urlopen(req, timeout=60, context=tls()) as r:
                    return r.read()
            try:
                sha = json.loads(fetch('https://api.github.com/repos/%s/commits/main' % REPO))['sha']
                got = {}
                for name, dest, mark in SOFTWARE:
                    src = fetch('https://raw.githubusercontent.com/%s/%s/_grifflix/mac/%s' % (REPO, sha, name)).decode('utf-8')
                    compile(src, name, 'exec')   # (a file that isn't whole Python stops it all)
                    if mark and mark not in src:
                        raise ValueError('%s isn’t what it should be' % name)
                    got[name] = src
            except Exception as e:
                log('Update the Mac: couldn’t fetch:', repr(e))
                return self.send_json(502, {'error': 'Couldn’t get the new files from GitHub (%s). Nothing was changed.' % str(e)[:120]})
            changed = []
            for name, dest, mark in SOFTWARE:
                old = None
                try:
                    with open(dest, encoding='utf-8') as f:
                        old = f.read()
                except OSError:
                    pass
                if old == got[name]:
                    continue
                if old is None and name not in ('grifflix-gateway.py', 'convert.py'):
                    continue   # (a script John hasn't got on this Mac stays away)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if old is not None:
                    with open(dest + '.prev', 'w', encoding='utf-8') as f:
                        f.write(old)
                with open(dest + '.new', 'w', encoding='utf-8') as f:
                    f.write(got[name])
                os.replace(dest + '.new', dest)
                changed.append(name)
            save(INSTALLED, {'sha': sha, 'at': int(time.time()), 'files': [n for n, _, _ in SOFTWARE]})
            restarted = False
            if 'convert.py' in changed:   # the converter running: started again with its new file, as it was started
                cur = load(os.path.expanduser('~/Grifflix/convert-now.json'), None) or {}
                try:
                    pid = int(cur.get('pid') or 0)
                    if pid and cur.get('state') != 'finished':
                        os.kill(pid, 0)
                        os.kill(pid, 15)
                        time.sleep(3)
                        import subprocess
                        args = cur.get('args') or ['go', 'now']
                        out = open(os.path.expanduser('~/Grifflix/convert.out'), 'a')
                        awake = ['/usr/bin/caffeinate', '-i'] if os.path.exists('/usr/bin/caffeinate') else []   # (the Mac kept awake while it converts)
                        subprocess.Popen(awake + [sys.executable, os.path.expanduser('~/Grifflix/convert.py')] + [str(a) for a in args],
                                         stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True, cwd=os.path.expanduser('~/Grifflix'))
                        restarted = True
                except (OSError, ValueError) as e:
                    log('Update the Mac: the converter couldn’t be started again:', e)
            log('Update the Mac: %s (%s)%s' % (sha[:7], ', '.join(changed) or 'nothing changed', '; converter started again' if restarted else ''))
            self.send_json(200, {'sha': sha, 'changed': changed, 'converter': restarted, 'restarting': 'grifflix-gateway.py' in changed})
            if 'grifflix-gateway.py' in changed:
                threading.Timer(1.5, lambda: os._exit(0)).start()   # (launchd starts the new gateway)

        def arrange(self):
            """ARRANGE made so in Jellyfin: each named box set and series inside its collection. Returns what it put in."""
            key = lambda t: re.sub(r'[^a-z0-9]', '', re.sub(r'^the\s+', '', re.sub(r'\s*[\(\[](19|20)\d\d[\)\]]\s*$', '', (t or '').strip().lower()).replace('’', "'")))
            try:
                sets = {key(i['Name']): i for i in g.items(Recursive='true', IncludeItemTypes='BoxSet', Fields='').get('Items', [])}
                series = {key(i['Name']): i for i in g.items(Recursive='true', IncludeItemTypes='Series', Fields='').get('Items', [])}
            except Exception as e:
                log('Arranging the collections: couldn’t ask Jellyfin:', e)
                return []
            put = []
            for cname, want in ARRANGE.items():
                c = sets.get(key(cname))
                if not c:
                    log('Arranging the collections: no collection called', cname)
                    continue
                held = set(i['Id'] for i in g.items(ParentId=c['Id'], Fields='', EnableImageTypes='').get('Items', []))
                for name, pool in [(n, sets) for n in want.get('sets', [])] + [(n, series) for n in want.get('series', [])]:
                    it = pool.get(key(name))
                    if not it:
                        log('Arranging the collections: not found:', name)
                    elif it['Id'] not in held and it['Id'] != c['Id']:
                        g.jf.call('POST', '/Collections/%s/Items' % c['Id'], query={'Ids': it['Id']})
                        put.append('%s in %s' % (it['Name'], c['Name']))
            if put:
                log('Arranging the collections:', '; '.join(put))
                g.forget()
            return put

        def api_refresh(self, me, user, q, b):   # the page's Refresh button: look again for new films and changes
            g.forget()
            self.send_json(200, {'ok': True})

        def api_collections(self, me, user, q, b):
            if not b:
                return self.send_json(200, {'collections': self.collections_out(False)})
            # John's own changes, made here (Grifflix keeps its collections in Jellyfin, nothing from Plex):
            # in (which hold an item), add, remove, create, rename, delete
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John changes the collections.'})
            op, cid, item = b.get('op'), str(b.get('id') or ''), str(b.get('item') or '')
            ok = lambda x: re.match(r'^[0-9a-f]{32}$', x)
            sets = g.items(Recursive='true', IncludeItemTypes='BoxSet', SortBy='SortName', Fields='').get('Items', [])
            if cid and not any(s['Id'] == cid for s in sets):
                return self.send_json(404, {'error': 'No such collection.'})
            if item and not ok(item):
                return self.send_json(400, {'error': 'Which film?'})
            if op == 'in':
                held = [s['Id'] for s in sets if any(i['Id'] == item for i in g.items(ParentId=s['Id'], Fields='', EnableImageTypes='').get('Items', []))]   # (10.8 ignores Ids with ParentId: each one's list, checked)
                return self.send_json(200, {'collections': [{'id': s['Id'], 'name': s['Name']} for s in sets], 'in': held})
            if op in ('add', 'remove') and cid and item:
                g.jf.call('POST' if op == 'add' else 'DELETE', '/Collections/%s/Items' % cid, query={'Ids': item})
            elif op == 'create':
                name = re.sub(r'\s+', ' ', str(b.get('name') or '')).strip()[:80]
                if not name:
                    return self.send_json(400, {'error': 'Its name?'})
                if any(s['Name'].lower() == name.lower() for s in sets):
                    return self.send_json(409, {'error': 'There’s a collection called that already.'})
                r = g.jf.call('POST', '/Collections', query=dict({'Name': name, 'IsLocked': 'true'}, **({'Ids': item} if item else {})))
                cid = (r or {}).get('Id', '')
            elif op == 'rename' and cid:
                name = re.sub(r'\s+', ' ', str(b.get('name') or '')).strip()[:80]
                if not name:
                    return self.send_json(400, {'error': 'Its name?'})
                full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, cid))
                full['Name'] = name
                full['LockedFields'] = sorted(set((full.get('LockedFields') or []) + ['Name']))
                g.jf.call('POST', '/Items/%s' % cid, full)
            elif op == 'delete' and cid:   # the collection only: its films stay
                g.jf.call('DELETE', '/Items/%s' % cid)
            else:
                return self.send_json(400, {'error': 'What should happen?'})
            g.forget()
            self.send_json(200, {'ok': True, 'id': cid})

        def api_collection(self, me, user, q, b):
            o = self.collection_out(q.get('id', ''), user['progress'], False)
            self.send_json(200 if o else 404, o or {'error': 'No such collection.'})

        def api_search(self, me, user, q, b):
            term = (q.get('q') or '').strip()
            if len(term) < 2:
                return self.send_json(200, {'items': []})
            r = g.items(SearchTerm=term, Recursive='true', IncludeItemTypes='Movie,Series,Episode,Video', Limit=40)
            self.send_json(200, {'items': [item_out(i, user['progress']) for i in shown(r.get('Items', []))]})

        def api_play(self, me, user, q, b):
            iid = g.now_id(q['id'])
            ps = secrets.token_hex(8)
            key = make_key(g.st['secret'], me['uid'])
            dev = 'grifflix-' + hashlib.sha1(me['uid'].encode()).hexdigest()[:10]
            it = g.one(iid)
            ms = (it.get('MediaSources') or [{}])[0]
            msid = ms.get('Id') or iid
            br = int(q.get('br') or 20000000)
            hevc = q.get('hevc') == '1'   # the device plays HEVC itself (the page asks it): copied, not converted
            vs0 = next((x for x in ms.get('MediaStreams', []) if x.get('Type') == 'Video'), {}) or {}
            if hevc and ((vs0.get('BitDepth') or 8) > 8 or '10' in (vs0.get('Profile') or '')) and q.get('hevc10') != '1':
                hevc = False   # (a 10-bit HEVC film to a device that plays only 8-bit HEVC: it couldn't show it; John, 11 Nov 2026: Rushmore)
            hls = ('/v/%s/master.m3u8?' % iid) + urllib.parse.urlencode({
                'MediaSourceId': msid, 'DeviceId': dev, 'PlaySessionId': ps, 'VideoCodec': 'h264,hevc' if hevc else 'h264', 'AudioCodec': 'aac,mp3',
                'TranscodingMaxAudioChannels': 2, 'MaxStreamingBitrate': br, 'SegmentContainer': 'mp4', 'MinSegments': 1,
                'BreakOnNonKeyFrames': 'True', 'RequireAvc': 'false', 'g': key})
            direct = None
            vs = [s for s in ms.get('MediaStreams', []) if s.get('Type') == 'Video']
            aus = [s for s in ms.get('MediaStreams', []) if s.get('Type') == 'Audio']
            if (ms.get('Container') or '') in ('mp4', 'm4v', 'mov') and vs and (vs[0].get('Codec') or '') in (('h264', 'hevc') if hevc else ('h264',)) \
                    and all((a.get('Codec') or '') in ('aac', 'mp3', 'ac3', 'eac3') for a in aus[:1]):
                direct = '/v/%s/stream?' % iid + urllib.parse.urlencode({'Static': 'true', 'MediaSourceId': msid, 'g': key})
            subs = [{'label': s.get('DisplayTitle') or s.get('Language') or 'Subtitles', 'lang': s.get('Language') or '',
                     'url': '/v/%s/%s/Subtitles/%d/0/Stream.vtt?g=%s' % (iid, msid, s['Index'], key)}
                    for s in ms.get('MediaStreams', []) if s.get('Type') == 'Subtitle' and s.get('IsTextSubtitleStream')]
            v0 = vs0 or {}
            srcd = {'v': (v0.get('Codec') or '').lower(), 'bits': v0.get('BitDepth') or (10 if '10' in (v0.get('Profile') or '') else 8),
                    'mbps': round((ms.get('Bitrate') or v0.get('BitRate') or 0) / 1e6, 1)}
            with g.plays_lock:   # (what's playing, for "Playing now": kept while its progress comes in)
                for k in [k for k, x in g.plays.items() if time.time() - x['seen'] > 120 or (x['uid'] == me['uid'] and x.get('kind') == q.get('kind'))]:
                    del g.plays[k]
                g.plays[ps] = {'uid': me['uid'], 'who': me['name'], 'iid': iid, 'msid': msid, 'name': it.get('Name') or '', 'mode': 'direct' if direct else 'stream',
                               'src': srcd, 'srcdesc': '%s%s%s' % ({'hevc': 'HEVC', 'h264': 'H.264'}.get(srcd['v'], srcd['v'].upper()), ' 10-bit' if srcd['bits'] > 8 else '', ', %s Mbps' % srcd['mbps'] if srcd['mbps'] else ''),
                               'hevc': hevc, 'hevc10': q.get('hevc10') == '1', 'br': br, 'kind': (q.get('kind') or '')[:40], 'at': time.time(), 'seen': time.time()}
            g.watched()
            self.send_json(200, {'hls': hls, 'direct': direct, 'subs': subs, 'ps': ps, 'device': dev,
                                 'pos': user['progress'].get(iid, {}).get('pos', 0), 'secs': round((it.get('RunTimeTicks') or 0) / 1e7)})

        def api_playing(self, me, user, q, b):
            """Whether a film is playing as it is or being converted by the Mac mini: a member's own (ps), or, for John, everything
            playing now (the admin page's Playing now)."""
            encs = g.encodes()
            with g.plays_lock:
                live = {k: dict(x) for k, x in g.plays.items() if time.time() - x['seen'] < 90}
            if q.get('ps'):
                p = live.get(q['ps'])
                if not p or (p['uid'] != me['uid'] and not me.get('owner')):
                    return self.send_json(200, {'how': ''})
                return self.send_json(200, g.play_state(p, encs))
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            rows = [g.play_state(p, encs) for p in sorted(live.values(), key=lambda x: -x['at'])]
            known = set(x for p in live.values() for x in (p['iid'], p.get('msid')))
            others = [e for e in encs if e['id'] not in known]   # (converting for something Grifflix didn't start: the media server's own app)
            self.send_json(200, {'playing': rows, 'other': len(others)})

        def api_download(self, me, user, q, b):
            """A member's link to keep a film on their phone or computer (John, 8 Nov 2026: "Can members be allowed to download
            films to their device"), while John allows downloads (admin); its size, so they know."""
            if not g.store.d.get('settings', {}).get('downloads'):
                return self.send_json(403, {'error': 'Downloads are off just now.'})
            iid = g.now_id(q.get('id', ''))
            if not re.match(r'^[0-9a-f]{32}$', iid):
                return self.send_json(400, {'error': 'Which film?'})
            it = g.one(iid)
            ms = (it.get('MediaSources') or [{}])[0]
            self.send_json(200, {'url': '/dl/%s?g=%s' % (iid, make_key(g.st['secret'], me['uid'])), 'bytes': ms.get('Size') or 0,
                                 'container': (ms.get('Container') or '').split(',')[0]})

        def api_settings(self, me, user, q, b):   # John's: whether members may download films
            st = g.store.d.setdefault('settings', {})
            if b:
                if not me.get('owner'):
                    return self.send_json(403, {'error': 'Only John.'})
                with g.store.lock:
                    if 'downloads' in b:
                        st['downloads'] = bool(b['downloads'])
                    g.store.touch()
            self.send_json(200, {'downloads': bool(st.get('downloads'))})

        def api_stop(self, me, user, q, b):
            with g.plays_lock:
                g.plays.pop(str(b.get('ps') or ''), None)
            try:
                g.jf.call('DELETE', '/Videos/ActiveEncodings', query={'DeviceId': b.get('device'), 'PlaySessionId': b.get('ps')})
            except Exception:
                pass
            self.send_json(200, {'ok': True})

        def api_progress(self, me, user, q, b):
            iid, pos, secs = g.now_id(str(b.get('id', ''))), float(b.get('pos') or 0), float(b.get('secs') or 0)
            if not re.match(r'^[0-9a-f]{32}$', iid):
                return self.send_json(400, {'error': 'Which?'})
            with g.plays_lock:
                for x in g.plays.values():
                    if x['uid'] == me['uid'] and x['iid'] == iid:
                        x['seen'] = time.time()
            g.watched()
            with g.store.lock:
                done = bool(secs and pos > secs * 0.92)
                user['progress'][iid] = {'pos': 0 if done else round(pos), 'at': int(time.time()), 'done': done}
                if len(user['progress']) > 2000:
                    for k, _ in sorted(user['progress'].items(), key=lambda kv: kv[1].get('at', 0))[:200]:
                        del user['progress'][k]
                g.store.touch()
            self.send_json(200, {'ok': True, 'done': done})

        # calendars: Movie of the Day (John's) and members' own
        def cal_out(self, cid, c, me, user, full=True):
            o = {'id': cid, 'name': c.get('name'), 'ownerName': c.get('ownerName') or 'John', 'mine': c.get('owner') == me['uid'],
                 'shared': c.get('shared', True), 'count': len(c.get('days', {}))}
            days, notes = c.get('days', {}), c.get('notes', {})
            if cid == 'motd':   # the days still to come: hidden (John sees that they're chosen, not what; or all, if he's switched that on)
                peek = me.get('owner') and g.store.d.get('settings', {}).get('peek')
                if not peek:
                    secret = [d for d in days if not revealed(d)]
                    days = {d: i for d, i in days.items() if revealed(d)}
                    notes = {d: n for d, n in notes.items() if revealed(d)}
                    o['count'] = len(days)
            if full:
                ext = c.get('ext', {})
                S = g.summaries([i for i in days.values() if i not in ext], user['progress'])
                S.update({i: ext[i] for i in days.values() if i in ext})
                o['days'] = {d: S[i] for d, i in days.items() if i in S}
                o['notes'] = notes
                if cid == 'motd' and me.get('owner') and not peek:
                    for d in secret:
                        o['days'][d] = {'secret': True}
            return o

        def api_unrepeat(self, me, user, q, b):
            """John's: no film on Movie of the Day twice, the days gone by too. Each film on more than one day keeps the
            1st of the month whose theme it is (the admin page sends those, keep), and its other days get films not yet on
            the calendar, by motd-fill.py's own rules (John, 10 Nov 2026: "there are still repeats in the films - I thought
            moving the themes about was meant to get rid of them?"). Update everything then puts them on the site."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            keep = [str(d) for d in (b.get('keep') or []) if re.match(r'^\d{4}-\d\d-01$', str(d))]
            script = os.path.expanduser('~/Grifflix/motd-fill.py')
            if not os.path.exists(script):
                return self.send_json(404, {'error': 'Movie of the Day’s file isn’t on the Mac yet.'})
            import subprocess
            try:
                r = subprocess.run([sys.executable, script, '--unrepeat', '--all', '--keep', ','.join(keep), 'go'], capture_output=True,
                                   text=True, timeout=900, stdin=subprocess.DEVNULL, cwd=os.path.expanduser('~/Grifflix'))
            except subprocess.TimeoutExpired:
                return self.send_json(504, {'error': 'Finding the repeated films took too long.'})
            out = (r.stdout or '') + (r.stderr or '')
            m = re.search(r'(\d+) repeated day', out)
            if r.returncode != 0 and not m:
                log('Unrepeat failed:', out[-800:])
                return self.send_json(500, {'error': 'The Mac couldn’t sort out the repeated films.'})
            n = int(m.group(1)) if m else 0
            log('Unrepeat: %d day%s given a new film' % (n, '' if n == 1 else 's'))
            self.send_json(200, {'ok': True, 'fixed': n})

        def api_calendars(self, me, user, q, b):
            out = []
            for cid, c in g.store.d['calendars'].items():
                if cid == 'motd' or c.get('owner') == me['uid'] or c.get('shared'):
                    out.append(self.cal_out(cid, c, me, user, full=False))
            self.send_json(200, {'calendars': out})

        def api_calendar(self, me, user, q, b):
            C = g.store.d['calendars']
            if b:
                op = b.get('op')
                with g.store.lock:
                    if op == 'create':
                        cid = secrets.token_hex(5)
                        C[cid] = {'name': str(b.get('name') or 'My calendar')[:60], 'owner': me['uid'], 'ownerName': me['name'],
                                  'days': {}, 'notes': {}, 'shared': bool(b.get('shared', True))}
                        g.store.touch()
                        return self.send_json(200, {'id': cid})
                    cid = b.get('id')
                    c = C.get(cid)
                    if not c or c.get('owner') != me['uid']:
                        return self.send_json(403, {'error': 'Only its owner can change that calendar.'})
                    if op == 'set':
                        d = str(b.get('date', ''))
                        if not re.match(r'^\d{4}-\d{2}-\d{2}$', d):
                            return self.send_json(400, {'error': 'Which day?'})
                        if b.get('item'):
                            c['days'][d] = str(b['item'])
                            x = b.get('ext')   # (a film Grifflix hasn't, a theme's film: kept with its details, its trailer played: John, 9 Nov 2026)
                            if isinstance(x, dict) and x.get('id') == c['days'][d] and re.match(r'^[0-9a-f]{32}$', c['days'][d]):
                                keep = ('id', 'type', 'name', 'year', 'mins', 'cert', 'overview', 'poster', 'backdrop', 'trailers', 'tmdb')
                                c.setdefault('ext', {})[c['days'][d]] = dict({k: x[k] for k in keep if k in x}, ext=1)
                        if 'note' in b:
                            n = str(b.get('note') or '').strip()[:500]
                            if n:
                                c['notes'][d] = n
                            else:
                                c['notes'].pop(d, None)
                    elif op == 'clear':
                        c['days'].pop(str(b.get('date')), None)
                        c['notes'].pop(str(b.get('date')), None)
                    elif op == 'rename':
                        c['name'] = str(b.get('name') or c['name'])[:60]
                    elif op == 'share':
                        c['shared'] = bool(b.get('shared'))
                    elif op == 'delete' and cid != 'motd':
                        del C[cid]
                        g.store.touch()
                        return self.send_json(200, {'ok': True})
                    g.store.touch()
                return self.send_json(200, self.cal_out(cid, c, me, user))
            cid = q.get('id', 'motd')
            c = C.get(cid)
            if not c or not (cid == 'motd' or c.get('shared') or c.get('owner') == me['uid']):
                return self.send_json(404, {'error': 'No such calendar.'})
            self.send_json(200, self.cal_out(cid, c, me, user))

        # My list (the hearts) and playlists
        def api_lists(self, me, user, q, b):
            if b:
                op, L = b.get('op'), user['lists']
                with g.store.lock:
                    if op == 'heart':
                        iid = str(b.get('item'))
                        if iid in user['hearts']:
                            user['hearts'].remove(iid)
                        else:
                            user['hearts'].insert(0, iid)
                    elif op == 'create':
                        lid = secrets.token_hex(5)
                        L[lid] = {'name': str(b.get('name') or 'New playlist')[:60], 'items': [str(i) for i in b.get('items', [])][:500]}
                    elif b.get('id') in L:
                        pl = L[b['id']]
                        if op == 'add' and str(b.get('item')) not in pl['items']:
                            pl['items'].append(str(b['item']))
                        elif op == 'remove' and str(b.get('item')) in pl['items']:
                            pl['items'].remove(str(b['item']))
                        elif op == 'rename':
                            pl['name'] = str(b.get('name') or pl['name'])[:60]
                        elif op == 'order':
                            pl['items'] = [i for i in map(str, b.get('items', [])) if i in pl['items']]
                        elif op == 'delete':
                            del L[b['id']]
                    g.store.touch()
            S = g.summaries(user['hearts'] + [i for pl in user['lists'].values() for i in pl['items']], user['progress'])
            self.send_json(200, {'hearts': [S[i] for i in user['hearts'] if i in S],
                                 'lists': [{'id': k, 'name': v['name'], 'items': [S[i] for i in v['items'] if i in S]} for k, v in user['lists'].items()]})

        # --- pictures and video, passed through
        # --- the public API (no sign-in): read only, films and TV only
        def pub_home(self, q):
            rows = {}
            for key in ('movies', 'tv'):
                lid = g.st.get('libraries', {}).get(key)
                if lid:
                    r = g.items(ParentId=lid, Recursive='true', IncludeItemTypes={'movies': 'Movie', 'tv': 'Series'}[key], SortBy='DateCreated', SortOrder='Descending', Limit=24)
                    rows[key] = [item_out(i) for i in shown(r.get('Items', []))]
            self.send_json(200, rows)

        def pub_items(self, q):
            key = q.get('lib', 'movies')
            if key not in ('movies', 'tv'):
                return self.send_json(403, {'error': 'Sign in to see those.'})
            q = dict(q)
            q.pop('parent', None)
            return self.api_items({'uid': ''}, {'progress': {}}, q, {})

        def pub_item(self, q):
            if not g.is_public(q.get('id', '')):
                return self.send_json(404, {'error': 'Not here.'})
            return self.api_item({'uid': ''}, {'progress': {}, 'hearts': []}, q, {})

        def pub_search(self, q):
            term = (q.get('q') or '').strip()
            if len(term) < 2:
                return self.send_json(200, {'items': []})
            r = g.items(SearchTerm=term, Recursive='true', IncludeItemTypes='Movie,Series,Episode', Limit=40)
            self.send_json(200, {'items': [item_out(i) for i in shown(r.get('Items', [])) if g.is_public(i['Id'], i.get('Path'))]})

        def pub_collections(self, q):
            self.send_json(200, {'collections': self.collections_out(True)})

        def pub_collection(self, q):
            o = self.collection_out(q.get('id', ''), {}, True)
            self.send_json(200 if o else 404, o or {'error': 'No such collection.'})

        def pub_calendar(self, q):   # Movie of the Day only (members' own calendars are theirs)
            c = g.store.d['calendars']['motd']
            days = {d: i for d, i in c.get('days', {}).items() if revealed(d)}   # (only those already revealed: the rest are a surprise)
            ids = sorted(set(i for i in days.values() if i))
            raw = g.by_ids(ids)
            S = {it['Id']: item_out(it) for it in raw if g.is_public(it['Id'], it.get('Path'))}   # (each one's path from the one list)
            self.send_json(200, {'id': 'motd', 'name': c.get('name'), 'ownerName': 'John', 'mine': False, 'shared': True,
                                 'count': len(days), 'notes': {d: n for d, n in c.get('notes', {}).items() if revealed(d)},
                                 'days': {d: S[i] for d, i in days.items() if i in S}})

        def image(self, rest, q, cache='private, max-age=86400'):
            m = re.match(r'^([0-9a-f]{32})(?:/(Primary|Backdrop|Thumb|Logo))?$', rest)
            if not m:
                return self.send_json(404, {'error': 'Not here.'})
            path = '/Items/%s/Images/%s' % (m.group(1), m.group(2) or 'Primary')
            qs = {k: v for k, v in q.items() if k in ('maxWidth', 'maxHeight', 'quality', 'fillWidth', 'fillHeight')}
            qs.setdefault('quality', '85')
            self.passthrough('GET', path + '?' + urllib.parse.urlencode(qs), cache=cache)

        def download(self, iid, method):   # the film's own file, named for it (Jellyfin's Download), so the phone or computer keeps it as a file
            if not re.match(r'^[0-9a-f]{32}$', iid):
                return self.send_json(404, {'error': 'Not here.'})
            iid = g.now_id(iid)
            it = g.one(iid)
            ms = (it.get('MediaSources') or [{}])[0]
            ext = (ms.get('Container') or 'mp4').split(',')[0]
            name = re.sub(r'[^\w .()&,!\'-]+', '', '%s (%s)' % (it.get('Name') or 'Film', it.get('ProductionYear') or '')).strip() or 'Film'
            self.dl_name = '%s.%s' % (name, ext)
            self.passthrough(method, '/Items/%s/Download' % iid)

        def video(self, rest, q, method):
            if not re.match(r'^[0-9a-f]{32}/[\w./-]+$', rest) or '..' in rest:
                return self.send_json(404, {'error': 'Not here.'})
            self.passthrough(method, '/Videos/' + rest + ('?' + urllib.parse.urlencode(q) if q else ''), m3u8key=self.path)

        def passthrough(self, method, path, cache='no-store', m3u8key=None):
            c = g.jf.conn(timeout=120)
            hdr = {'Authorization': g.jf.auth_header()}
            if self.headers.get('Range'):
                hdr['Range'] = self.headers['Range']
            c.request('GET' if method == 'HEAD' else method, path, headers=hdr)
            r = c.getresponse()
            ctype = r.getheader('Content-Type') or 'application/octet-stream'
            if 'mpegurl' in ctype.lower() or path.split('?')[0].endswith('.m3u8'):
                key = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(m3u8key or '').query)).get('g', '')
                text = r.read().decode('utf-8', 'replace')
                c.close()
                raw = self.sign_playlist(text, key).encode()
                self.send_response(r.status)
                self.cors()
                self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                if method != 'HEAD':
                    self.wfile.write(raw)
                return
            self.send_response(r.status)
            self.cors()
            for h in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'Last-Modified', 'ETag'):
                if r.getheader(h):
                    self.send_header(h, r.getheader(h))
            if getattr(self, 'dl_name', None):   # (a download: kept as a file, by the film's name)
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(self.dl_name))
                self.dl_name = None
            self.send_header('Cache-Control', cache)
            if not r.getheader('Content-Length'):
                self.send_header('Connection', 'close')
                self.close_connection = True
            self.end_headers()
            try:
                if method != 'HEAD':
                    while True:
                        chunk = r.read(256 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            finally:
                c.close()

        @staticmethod
        def sign_playlist(text, key):
            """Every link in an HLS playlist gets the signed key (Jellyfin's links are relative to /Videos/<id>/)."""
            def add(uri):
                if not uri or uri.startswith('#'):
                    return uri
                uri = re.sub(r'([?&])(api_key|ApiKey)=[^&]*&?', r'\1', uri).rstrip('&?')
                return uri + ('&' if '?' in uri else '?') + 'g=' + key
            out = []
            for line in text.splitlines():
                if line.startswith('#'):
                    line = re.sub(r'URI="([^"]+)"', lambda m: 'URI="%s"' % add(m.group(1)), line)
                    out.append(line)
                else:
                    out.append(add(line.strip()))
            return '\n'.join(out) + '\n'

    return H


def serve():
    g = Gateway()
    port = int(g.st.get('port', 8097))
    srv = ThreadingHTTPServer(('127.0.0.1', port), make_handler(g))
    srv.daemon_threads = True
    log('Grifflix gateway on 127.0.0.1:%d, Jellyfin at %s' % (port, g.st['jellyfin']))
    srv.serve_forever()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'setup':
        setup(sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == 'serve':
        serve()
    else:
        print(__doc__)
