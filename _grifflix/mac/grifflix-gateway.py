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
import base64, hashlib, hmac, http.client, inspect, json, os, re, secrets, sys, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
try:   # (subtitles: found on OpenSubtitles and timed to the film; John, 13 Nov 2026)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import subtitles as SUBS
except Exception:
    SUBS = None
SUBJOBS, SUBRUN, SUBDAY = {}, threading.Lock(), {}
HEARING = {}   # (films being heard now, ahead of a choice: path -> threading.Event, set when done)   # (subtitle jobs by id; one timed at a time; each member's adds today)

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
            ('streaming-report.py', os.path.expanduser('~/Grifflix/streaming-report.py'), ''),
            ('subtitles.py', os.path.join(APP_DIR, 'subtitles.py'), 'movie_hash'),
            ('poster-add.py', os.path.expanduser('~/Grifflix/poster-add.py'), 'Criterion images'),   # (John's own posters on their films: 12 Nov 2026)
            ('criterion.py', os.path.expanduser('~/Grifflix/criterion.py'), 'Criterion Films'),
            ('criterion-covers.py', os.path.expanduser('~/Grifflix/criterion-covers.py'), 'criterion-list.html'),
            ('duplicates.py', os.path.expanduser('~/Grifflix/duplicates.py'), 'Set aside'),   # (films John has two copies of: 16 Nov 2026)   # (every Criterion cover: 16 Nov 2026)
            ('gdrive.py', os.path.expanduser('~/Grifflix/gdrive.py'), 'Google Drive')]   # (films from Google Drive, by themselves: 15 Nov 2026)   # what John's last Update sent (so the next reads only what's new)
LIBS = {'movies': ('Movies', 'movies'), 'tv': ('TV', 'tvshows'), 'home': ('Home videos', 'homevideos')}


def log(*a):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


LOOKED = {}
DUPS = {}   # (duplicates.py, from the admin page)
SYN = {}   # (Criterion's synopses being given to their films, in the background: how far it's got)
COVERS_LOCK = threading.Lock()
SCANJOB = {}
G_REF = []
CRIT = {}   # (criterion-covers.py, started from the admin page)   # (when Claude last looked)
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

    def raw(self, method, path, data=None, ctype='application/json'):
        c = self.conn(60)
        c.request(method, path, body=data, headers={'Authorization': self.auth_header(), 'Content-Type': ctype})
        r = c.getresponse()
        out = r.read()
        c.close()
        if r.status >= 400:
            raise RuntimeError('Jellyfin %s %s: %s %s' % (method, path, r.status, out[:200]))
        return out


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


def conv_why(ms):
    """What the converter will do to a film (as convert.py decides), or '' if it plays everywhere as it is."""
    ss = ms.get('MediaStreams') or []
    v = next((x for x in ss if x.get('Type') == 'Video'), {}) or {}
    au = [x for x in ss if x.get('Type') == 'Audio']
    w, h, vc = v.get('Width') or 0, v.get('Height') or 0, (v.get('Codec') or '').lower()
    bits = v.get('BitDepth') or 8
    mbps = round((ms.get('Bitrate') or v.get('BitRate') or 0) / 1e6, 1)
    if not mbps and ms.get('Size') and ms.get('RunTimeTicks'):   # (no rate recorded: from the file's size and length)
        mbps = round(ms['Size'] * 8 / (ms['RunTimeTicks'] / 1e7) / 1e6, 1)
    cont = [c for c in (ms.get('Container') or '').lower().split(',') if c]
    if cont and not any(c in ('mp4', 'm4v', 'mov') for c in cont):   # (every MKV repackaged first, the picture as it is, converted after if need be: John, 12 Nov 2026)
        return 'to repackage (%s file), the picture untouched' % cont[0].upper()
    if w > 1928 or h > 1088:
        return 'bigger than 1080p'
    if not (vc in ('hevc', 'h265') or (vc == 'h264' and bits <= 8 and '10' not in (v.get('Profile') or ''))):   # (10-bit HEVC left as it is, as convert.py since 16 Nov 2026)
        return 'format browsers can’t play' if v else ''
    if au and (au[0].get('Codec') or '').lower() not in ('aac', 'mp3'):
        return 'to repackage (%s sound), the picture untouched' % (au[0].get('Codec') or '?').upper()
    if mbps > (8.5 if h >= 900 else 5 if h >= 600 else 3) * (1.6 if vc in ('hevc', 'h265') else 1):   # (as convert.py: made smaller, once, last)
        return 'to make smaller (%s Mbps)' % mbps
    return ''


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


SPINES = {'at': 0, 'map': {}}


# (spine numbers criterion-covers missed, its folder's name too unlike Criterion's title: found by checking every Criterion film in Grifflix
# against Criterion's list, John, 18 Nov 2026, "Yes look into any mismatch")
SPINE_SEED = {'Martha Graham-Dance on Film (1959)': 406, 'A Hollis Frampton Odyssey (1966)': 607, 'Symbiopsychotaxiplasm - Take One (1968)': 360,
              'Secrets and Lies (1996)': 1070, 'Lady Snowblood 2 Love Song Of Vengeance (1974)': 791, 'By Brakhage An Anthlogy Vol 1-2 (1954)': 184,
              'Fanny and Alexander (1983)': 262, 'Fanny And Alexander (1982)': 263, 'The Making Of Fanny And Alexander (1984)': 264}


def spine_of(path, folder=False):
    """A Criterion film's spine number, by its folder (criterion-covers.py writes them from Criterion's list): John, 16 Nov 2026, "get the
    spine number from the criterion collection and be able to sort by spine number in Grifflix"."""
    f = os.path.expanduser('~/Grifflix/criterion-spines.json')
    try:
        m = os.path.getmtime(f)
        if m != SPINES['at']:
            SPINES['map'], SPINES['at'] = load(f, {}), m
    except OSError:
        return None
    if not path:
        return None
    k = os.path.basename(path.rstrip('/')) if folder else os.path.basename(os.path.dirname(path))   # (a series: its own folder)
    own = dict(SPINE_SEED, **(load(os.path.expanduser('~/Grifflix/criterion-spines-own.json'), {}) or {}))   # (set by hand, kept whatever criterion-covers matches)
    return own.get(k) or SPINES['map'].get(k)


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
    if t in ('Movie', 'Series') and spine_of(it.get('Path'), t == 'Series'):
        o['spine'] = spine_of(it.get('Path'), t == 'Series')
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
        self.plays, self.plays_lock, self.logdir, self.export_job, self.lib_job = {}, threading.Lock(), None, {}, {}   # (what's playing now, and the media server's logs: whether the Mac is converting)

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

    def remap(self, m):
        """Films (or episodes) with new ids, as their files were renamed or remade: calendars, hearts, lists and where people stopped
        follow them."""
        n = 0
        with self.store.lock:
            for c in self.store.d['calendars'].values():
                for d, i in list(c.get('days', {}).items()):
                    if i in m:
                        c['days'][d] = m[i]; n += 1
            for usr in self.store.d['users'].values():
                usr['hearts'] = [m.get(i, i) for i in usr.get('hearts', [])]
                for pl in usr.get('lists', {}).values():
                    pl['items'] = [m.get(i, i) for i in pl.get('items', [])]
                for old, new in m.items():
                    if old in usr.get('progress', {}):
                        usr['progress'][new] = usr['progress'].pop(old); n += 1
            old = self.store.d.setdefault('remap', {})   # (kept: the site's copy has the old ids until John's next Update)
            for k in list(old):
                if old[k] in m:
                    old[k] = m[old[k]]
            old.update(m)
            self.store.touch()
        self.forget()
        return n

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
            cl = next((l for l in head.splitlines() if 'ffmpeg' in l and ' -i ' in l), '')
            cl = re.sub(r'file:"[^"]*"', 'file:…', re.sub(r'https?://\S+', '…', cl))[:900]
            out.append({'id': m.group(0) if m else '', 'v': vc.group(1) if vc else '', 'a': ac.group(1) if ac else '', 'cmd': cl, 'log': n,
                        'speed': float(sp[-1]) if sp else None, 'at': st.st_mtime})
        return sorted(out, key=lambda e: -e['at'])

    def play_state(self, p, encs):
        """In words: is this film playing as it is, or being converted, and why."""
        o = {'who': p['who'], 'film': p['name'], 'device': p.get('kind') or '', 'src': p.get('srcdesc') or ''}
        if p['mode'] == 'direct':
            o.update(how='asis', say='Playing as it is: the whole file, straight from the Mac mini')
            return o
        e = next((x for x in encs if x['id'] in (p['iid'], p.get('msid'))), None)
        o['asked'] = p.get('asked', '')
        if e:
            o['cmd'], o['log'] = e.get('cmd', ''), e.get('log', '')
        if not e:
            o.update(how='wait', say='Starting…')
            return o
        sound = '' if e['a'] in ('', 'copy') else ' (the sound made stereo, which is light work)'
        if e['v'] == 'copy':
            o.update(how='asis', say='Playing as it is: the picture untouched, only repackaged' + sound)
            return o
        src = p.get('src') or {}
        plays = (src.get('v') == 'h264' and src.get('bits', 8) <= 8) or (src.get('v') in ('hevc', 'h265') and p.get('hevc') and (src.get('bits', 8) <= 8 or p.get('hevc10')))
        if plays:
            why = 'the media server chose to, though this device plays it as it is'
        elif src.get('v') not in ('h264', 'hevc', 'h265'):
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
                if u.path == '/claude/update':   # (Claude: something's just gone onto GitHub, so look now; it can only make the Mac look sooner)
                    tok = (self.headers.get('Authorization') or '').replace('Bearer ', '').strip()
                    want = g.st.get('look_key')
                    if not want or not tok or not hmac.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), want):
                        return self.send_json(403, {'error': 'Not allowed.'})
                    UPDATE_NOW.set()
                    return self.send_json(200, {'ok': True, 'have': (load(INSTALLED, {}).get('sha') or '')[:7]})
                if u.path == '/claude/ask':   # (Claude asking for one of ACTIONS, done only when John allows it on the admin page)
                    tok = (self.headers.get('Authorization') or '').replace('Bearer ', '').strip()
                    want = g.st.get('look_key')
                    if not want or not tok or not hmac.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), want):
                        return self.send_json(403, {'error': 'Not allowed.'})
                    if method != 'POST':
                        return self.send_json(200, {'can': {k: v[0] for k, v in ACTIONS.items()}, 'asked': [{k: v for k, v in x.items() if k != 'files'} for x in (load(ACTIONS_AT, []) or [])[-20:]]})
                    code, j = claude_ask(str(self._b.get('action') or ''), self._b.get('why'))
                    return self.send_json(code, j)
                if u.path.startswith('/look/'):   # (Claude, looking on John's behalf with the key John made for it: reading only)
                    tok = (self.headers.get('Authorization') or '').replace('Bearer ', '').strip()
                    want = g.st.get('look_key')
                    if not want or not tok or not hmac.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), want):
                        return self.send_json(403, {'error': 'Not allowed.'})
                    if method != 'GET':
                        return self.send_json(405, {'error': 'Looking only.'})
                    return self.look(u.path[6:].strip('/'), q)
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
                if u.path == '/macagent/next':   # (John's Mac's helper, asking every minute: the next film queued for it, as a job)
                    tok = (self.headers.get('Authorization') or '').replace('Bearer ', '').strip()
                    want = (g.st.get('macagent') or {}).get('key')
                    if not want or not tok or not hmac.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), want):
                        return self.send_json(403, {'error': 'Not allowed.'})
                    MACAGENT['seen'] = int(time.time()); MACAGENT['home'] = q.get('home') == '1'
                    if q.get('home') != '1':   # (away from home, the G-Drive out of reach: the queue kept for when it's back)
                        return self.send_json(200, {'wait': 'away'})
                    # (the plan made in the background and handed over at the next ask: planning took longer than the 20 seconds John's
                    # Mac waited, so it gave up, and the film planned for it was handed to no one: John, 18 Nov 2026, Disclosure Day
                    # "waiting for your Mac to take it up - nothing seems to be happening")
                    now = time.time()
                    for t_, j in list(CONVJOBS.items()):   # (handed out but never collected: back to the front of the queue)
                        if j.get('mac') and j.get('state') == 'waiting' and not j.get('ended') and j.get('given') and now - j['given'] > 300:
                            Q = load(MACQ, []) or []
                            if not any(x.get('id') == j['id'] for x in Q):
                                Q.insert(0, dict({'id': j['id'], 'name': j['plan']['item'].get('name') or '', 'at': int(now)}, **({'force': True} if j.get('force') else {})))
                                save(MACQ, Q)
                            j['state'], j['ended'] = 'lost', now
                            log('Queue for John’s Mac: %s never collected, back at the front' % j['plan']['item'].get('name'))
                            convjobs_save()
                    busy = [j for j in CONVJOBS.values() if j.get('mac') and j.get('state') in ('started', 'converting') and now - (j.get('seen') or j['at']) < 900]
                    if busy:
                        return self.send_json(200, {'wait': 'busy'})
                    ready = sorted([(j['at'], t_) for t_, j in CONVJOBS.items() if j.get('mac') and j.get('state') == 'waiting' and not j.get('ended') and not j.get('given')])
                    if ready:
                        t_ = ready[0][1]
                        CONVJOBS[t_]['given'] = now
                        convjobs_save()
                        return self.send_json(200, {'job': '/convjob/' + t_, 'name': CONVJOBS[t_]['plan']['item'].get('name')})
                    with MACPLAN_LOCK:
                        if MACPLAN.get('t') and MACPLAN['t'].is_alive():
                            return self.send_json(200, {'wait': 'planning', 'name': MACPLAN.get('name')})
                        Q = load(MACQ, []) or []
                        if not Q:
                            return self.send_json(200, {'wait': 'empty'})
                        x = Q.pop(0)
                        save(MACQ, Q)

                        def plan_it(x=x):
                            code, j = mac_job(x['id'], True)   # (always made smaller on John's Mac: see convert.py plan)
                            if code == 200:
                                CONVJOBS[j['token']].update(mac=True, force=True)
                                convjobs_save()
                            else:
                                log('Queue for John’s Mac: %s passed over (%s)' % (x.get('name') or x['id'], j.get('error')))
                        MACPLAN.update(t=threading.Thread(target=plan_it, daemon=True), name=x.get('name'))
                        MACPLAN['t'].start()
                    return self.send_json(200, {'wait': 'planning', 'name': x.get('name')})
                if u.path.startswith('/convjob/'):   # (John's Mac, converting a film for the Mac mini: its job, and word when it's done)
                    parts = u.path.split('/')
                    job = CONVJOBS.get(parts[2] if len(parts) > 2 else '')
                    if not job or time.time() - job['at'] > 12 * 3600:
                        return self.send_json(404, {'error': 'That job isn’t here (the Mac mini may have restarted): press Convert on this Mac again.'})
                    pl = job['plan']
                    if method == 'GET':
                        if job.get('ended'):
                            return self.send_json(404, {'error': 'That job is finished: press Convert on this Mac again.'})
                        job['state'], job['seen'] = 'started', time.time()
                        convjobs_save()
                        return self.send_json(200, {'name': pl['item'].get('name'), 'src': pl['src'], 'tmp': pl['tmp'], 'dur': pl['dur'], 'args': vt_args(pl['cmd'])})
                    body = self.body()
                    if len(parts) > 3 and parts[3] == 'progress' and body.get('stage') and 't' not in body:   # (a step on John's Mac, said: John, 17 Nov 2026)
                        job['stage'], job['seen'] = str(body.get('stage'))[:160], time.time()
                        if body.get('copying'):   # (made: copying itself to the G-Drive while John's Mac takes up the next film)
                            job['state'] = 'copying'
                        convjobs_save()
                        claim_renew(job['id'])
                        return self.send_json(200, {'ok': True})
                    if len(parts) > 3 and parts[3] == 'progress':   # (how far John's Mac has got, every few seconds: for the film's Edit panel)
                        job['t'], job['speed'], job['state'], job['seen'] = float(body.get('t') or 0), str(body.get('speed') or '')[:12], 'converting', time.time()
                        if time.time() - job.get('saved', 0) > 30:
                            job['saved'] = time.time()
                            convjobs_save()
                            claim_renew(job['id'])   # (the Mac mini keeps off it while John's Mac is at it: John, 17 Nov 2026)
                        return self.send_json(200, {'ok': True})
                    home = os.path.expanduser('~/Grifflix')
                    job['state'] = 'checking' if body.get('ok') else 'failed'
                    job['error'] = str(body.get('error') or '')[:200]
                    def finish(job=job, ok=bool(body.get('ok'))):
                        try:
                            if ok:
                                import subprocess
                                r = subprocess.run([sys.executable, os.path.join(home, 'convert.py'), 'finish', job['id']], input=json.dumps(job['plan']['item']),
                                                   capture_output=True, text=True, timeout=3600, cwd=home)
                                res = ([l for l in (r.stdout or '').splitlines() if l.startswith('{')] or ['{}'])[-1]
                                log('Converted on John’s Mac: %s: %s' % (job['plan']['item'].get('name'), res))
                                try:
                                    rj = json.loads(res)
                                except ValueError:
                                    rj = {}
                                job['state'] = 'failed' if rj.get('error') else 'done'
                                job['error'] = str(rj.get('error') or '')[:200]
                                CONV_NOW.set()
                            else:
                                log('Not converted on John’s Mac: %s (%s)' % (job['plan']['item'].get('name'), str(body.get('error') or '')[:200]))
                                try:
                                    os.remove(job['plan']['tmp'])
                                except OSError:
                                    pass
                        finally:
                            claims = load(os.path.join(home, 'convert-claims.json'), {}) or {}
                            claims.pop(job['id'], None)
                            save(os.path.join(home, 'convert-claims.json'), claims)
                            convjobs_save()
                    threading.Thread(target=finish, daemon=True).start()
                    job['ended'] = time.time()   # (kept a while, for the Edit panel to say how it went)
                    convjobs_save()
                    return self.send_json(200, {'ok': True})
                if u.path == '/local/fetched' and method == 'POST':   # gdrive.py on this Mac: a film has just come in (John, 15 Nov 2026: "can't it
                    # just be done as soon as something is finished downloading?"): the media server asked to look now, the site to follow, and
                    # the converter told once the media server has it (in a minute or two), to repackage it next
                    if not hmac.compare_digest(self.headers.get('X-Grifflix-Secret', ''), g.st['secret']):
                        return self.send_json(403, {'error': 'Not here.'})
                    try:
                        g.jf.call('POST', '/Library/Refresh')
                    except Exception as e:
                        log('Asking the media server to look:', e)
                    g.forget()
                    g.publish_soon = time.time() + 540
                    SCAN_NOW.set()
                    for t_ in (90, 240):
                        threading.Timer(t_, CONV_NOW.set).start()
                    threading.Timer(150, covers_now).start()   # (a Criterion film: its cover, once the media server has it)
                    log('Just brought in: %s: the media server asked to look' % os.path.basename(str(self.body().get('path') or ''))[:120])
                    return self.send_json(200, {'ok': True})
                if u.path == '/local/remap' and method == 'POST':   # convert.py on this Mac: a film's new file has a new id; calendars, lists and progress follow it
                    if not hmac.compare_digest(self.headers.get('X-Grifflix-Secret', ''), g.st['secret']):
                        return self.send_json(403, {'error': 'Not here.'})
                    m = {str(k): str(v) for k, v in (self.body().get('map') or {}).items() if re.match(r'^[0-9a-f]{32}$', str(k)) and re.match(r'^[0-9a-f]{32}$', str(v))}
                    return self.send_json(200, {'ok': True, 'changed': g.remap(m)})
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
            sort = {'name': 'SortName', 'added': 'DateCreated', 'year': 'PremiereDate,ProductionYear,SortName', 'rating': 'CommunityRating'}.get(q.get('sort'), 'SortName')
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
            if me.get('owner') and it.get('Type') in ('Movie', 'Episode'):   # (John, 13 Nov 2026: "a convert now option to films if I need them sooner")
                o['conv'] = conv_why(ms)
                P = load(os.path.expanduser('~/Grifflix/convert-priority.json'), {}) or {}
                o['convNext'] = any(x.get('id') == it['Id'] for x in P.get('pins') or [])
                now_ = load(os.path.expanduser('~/Grifflix/convert-now.json'), {}) or {}
                o['convNow'] = bool(now_.get('name') and now_.get('name') == it.get('Name') and now_.get('state') in ('converting', 'paused') and conv_alive(now_))
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
            if q.get('bg'):   # (made after answering, and handed over when it's ready: on the old Mac, busy repackaging films, it could
                # take longer than the 100 seconds the link to the Mac waits, and the admin page said the Mac wasn't answering: John, 12 Nov 2026)
                J = g.export_job
                with g.plays_lock:
                    if J.get('state') == 'done':   # (kept until it's taken, never thrown away: the Mac counts what it made as sent)
                        # (and kept ten minutes more once given, so a big list lost on its way, the link broken, can be asked for again:
                        # John, 17 Nov 2026, Update everything stuck at "listing the films… (9 s)" with 619 films changed)
                        code, body = J['code'], J['body']
                        if not J.get('given'):
                            J['given'] = time.time()
                        elif time.time() - J['given'] > 600:
                            g.export_job = J = {}
                        if J:
                            return self.send_json(code, body)
                    if J.get('state') not in ('making', 'done'):
                        g.export_job = J = {'state': 'making', 'at': time.time()}
                        H = type(self)
                        class Cap:   # (the export run on its own: what it would have sent kept; its other helpers, as they are)
                            def send_json(c, code, body):
                                c.out = (code, body)

                            def __getattr__(c, n):   # (the handler's own methods, bound to this stand-in; its static ones as they are)
                                a = inspect.getattr_static(H, n)
                                return a.__func__ if isinstance(a, staticmethod) else getattr(H, n).__get__(c)
                        cap = Cap()
                        qq = {k: v for k, v in q.items() if k != 'bg'}
                        def run():
                            try:
                                H.api_export(cap, me, user, qq, b)
                                code, body = getattr(cap, 'out', (500, {'error': 'Nothing was made.'}))
                            except Exception as e:
                                log('Export failed:', repr(e))
                                code, body = 500, {'error': 'The Mac couldn’t list the films (%s).' % str(e)[:120]}
                            with g.plays_lock:
                                g.export_job = {'state': 'done', 'code': code, 'body': body, 'at': time.time()}
                        threading.Thread(target=run, daemon=True).start()
                return self.send_json(202, {'making': True, 'since': int(J.get('at') or time.time())})
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
            # (what's owned, and which film carries each box set, across films and TV together: a set with a series in it (Fanny and
            # Alexander's television version) said its films were missing, each library seeing only its own: John, 18 Nov 2026)
            OWNED, SFILM, ctj_, FINFO = set(), {}, {}, {}
            snm = lambda x: x
            for key, types in (('movies', 'Movie'), ('tv', 'Series')):
                if libs.get(key):
                    raw = [i for i in shown(g.items(ParentId=libs[key], Recursive='true', IncludeItemTypes=types, SortBy='SortName', Fields=quick + ',SpecialFeatureCount').get('Items', [])) if not making(i)]
                    for o in resolve(raw, extra):
                        items[o['id']] = o
                    if True:   # (Criterion's spine numbers, kept apart from each film's remembered details, so they're always current; a series' too)
                        # (and Criterion's own director and country for it, for the Criterion pages' filters: John, 17 Nov 2026, "I would like
                        # all the same filters available to me on the criterion page in my collection too")
                        CL, TG, TS, SDONE, FINFO = {}, {}, {}, set(), {}
                        ctj_ = load(os.path.expanduser('~/Grifflix/criterion-tags.json'), {}) or {}
                        for k_, v_ in (ctj_.get('tags') or {}).items():
                            for sl_ in v_:
                                TG.setdefault(sl_, []).append(k_)
                        SMETA, SNAME = {}, {}   # (each box set's spine and cover, from Criterion's list already here: no bookmarklet needed: John, 18 Nov 2026)
                        for f_ in load(os.path.expanduser('~/Grifflix/criterion-list.json'), []) or []:
                            if '/boxsets/' in str(f_.get('link') or ''):
                                SMETA[fold(f_.get('title'))] = (str(f_.get('spine') or ''), str(f_.get('img') or ''))
                                SNAME[fold(f_.get('title'))] = str(f_.get('title') or '')   # (and its name as Criterion writes it: the bookmarklet's
                                # were lower case without punctuation: John, 18 Nov 2026, "I'm not sure why everything is lower case")
                        import difflib

                        def snm(x, _c={}):
                            if x not in _c:
                                k = fold(x)
                                m = SNAME.get(k) or next((SNAME[c] for c in difflib.get_close_matches(k, list(SNAME), 1, .75)), None)
                                _c[x] = m or x[:1].upper() + x[1:]
                            return _c[x]
                        if not ctj_.get('sets'):   # (till the bookmarklet has read each set's page: a numbered set's films are the spine numbers straight
                            # after its own, while each is one of Criterion's Collector's Sets films (The Apu Trilogy 782: 783 to 785). Right for half
                            # the numbered sets; the rest, and Eclipse's, wait for the bookmarklet: John, 18 Nov 2026, "I can't see the box set covers")
                            cs_ = set(((ctj_.get('tags') or {}).get('Collector’s Sets')) or [])
                            fsp_, ssp_ = {}, {}
                            for f_ in load(os.path.expanduser('~/Grifflix/criterion-list.json'), []) or []:
                                if str(f_.get('spine') or '').isdigit():
                                    (ssp_ if '/boxsets/' in str(f_.get('link') or '') else fsp_)[int(f_['spine'])] = str(f_.get('title') or '') if '/boxsets/' in str(f_.get('link') or '') else str(f_.get('link') or '').replace('https://www.criterion.com/films/', '')
                            guess_ = {}
                            for S_, nm_ in ssp_.items():
                                k_ = S_ + 1
                                while k_ in fsp_ and fsp_[k_] in cs_ and k_ not in ssp_:
                                    guess_.setdefault(nm_, []).append(fsp_[k_]); k_ += 1
                            ctj_ = dict(ctj_, sets=guess_)
                        for k_, v_ in (ctj_.get('sets') or {}).items():   # (the box set each film is in, Collector's Sets: John, 17 Nov 2026)
                            for sl_ in v_:
                                TS.setdefault(sl_, []).append(k_)
                        CT = {}   # (and by title and year, for the films with no spine number: Criterion Premieres, Eclipse and the rest, which had no
                        # Criterion labels at all: John, 17 Nov 2026, "the filter on criterion is saying there are no criterion premieres or eclipse")
                        for f_ in load(os.path.expanduser('~/Grifflix/criterion-list.json'), []) or []:
                            sl_ = str(f_.get('link') or '').replace('https://www.criterion.com/films/', '')
                            v_ = (str(f_.get('director') or '')[:160], str(f_.get('country') or '')[:60], TG.get(sl_, []), TS.get(sl_, []), sl_)
                            FINFO[sl_] = (str(f_.get('title') or ''), str(f_.get('year') or ''), str(f_.get('spine') or ''))
                            if str(f_.get('spine') or '').isdigit():
                                CL.setdefault(int(f_['spine']), v_)
                            if re.match(r'^\d{4}$', str(f_.get('year') or '')):
                                CT.setdefault((fold(f_.get('title')), int(f_['year'])), v_)
                        for i in raw:
                            if i['Id'] in items:
                                sp = spine_of(i.get('Path'), key == 'tv')
                                o_ = items[i['Id']]
                                for k_ in ('spine', 'cdir', 'ccty', 'ctags', 'cset', 'csetsp', 'csetimg', 'csetdesc', 'csetmiss', 'csetfeat'):
                                    o_.pop(k_, None)
                                if sp:
                                    o_['spine'] = sp
                                d_, c_, t_, s_, sl0_ = CL.get(int(sp), ('', '', [], [], '')) if sp and str(sp).isdigit() else ('', '', [], [], '')
                                if not (d_ or t_) and key == 'movies':
                                    y_ = i.get('ProductionYear') or 0
                                    d_, c_, t_, s_, sl0_ = next((CT[(fold(i.get('Name')), y_ + dy)] for dy in (0, 1, -1) if (fold(i.get('Name')), y_ + dy) in CT), ('', '', [], [], ''))
                                if sl0_:
                                    OWNED.add(sl0_)
                                for x_ in s_:
                                    SFILM.setdefault(x_, i['Id'])
                                if True:
                                    if d_:
                                        o_['cdir'] = d_
                                    if c_:
                                        o_['ccty'] = c_
                                    if t_:
                                        o_['ctags'] = t_
                                    if s_:
                                        o_['cset'] = [snm(x_) for x_ in s_]
                                        o_['csetsp'] = [str((ctj_.get('setspine') or {}).get(x_) or SMETA.get(fold(x_), ('', ''))[0] or '') for x_ in s_]   # (each box set's spine, beside its name)
                                        o_['csetimg'] = [str(SMETA.get(fold(x_), ('', ''))[1] or (ctj_.get('setimg') or {}).get(x_) or '') for x_ in s_]   # (and its cover)
                                        dsc_ = {x_: (ctj_.get('setdesc') or {}).get(x_) for x_ in s_ if x_ not in SDONE and (ctj_.get('setdesc') or {}).get(x_)}
                                        if dsc_:   # (its description, carried by one of its films only, not repeated on each)
                                            o_['csetdesc'] = {snm(k__): v__ for k__, v__ in dsc_.items()}
                                            ft_ = {snm(k__): (ctj_.get('setfeat') or {}).get(k__) for k__ in dsc_ if (ctj_.get('setfeat') or {}).get(k__)}
                                            if ft_:   # (and its special features, with it)
                                                o_['csetfeat'] = ft_
                                            SDONE.update(dsc_)
                    lists[key] = [i['Id'] for i in raw if i['Id'] in items]
                    if key == 'movies':
                        self.extras(raw, items, lists)
                    # (the extras the media server keeps as extras, in a film's Other or Extras folder: on its page too (John, 17 Nov 2026:
                    # "Extras aren't being shown on the film page"); before, only extras it had taken for films were)
                    for i in raw:
                        if not (i.get('SpecialFeatureCount') or 0) or i['Id'] not in items:
                            continue
                        try:
                            sf = g.jf.call('GET', '/Users/%s/Items/%s/SpecialFeatures' % (g.uid, i['Id'])) or []
                        except Exception as e:
                            log('Extras of %s: %s' % (i.get('Name'), e))
                            continue
                        for e in sf if isinstance(sf, list) else []:
                            try:
                                o = item_out(e)
                            except Exception:
                                continue
                            o['tags'] = pic_tags(e)
                            o['extra'], o['of'] = 1, i['Id']
                            items[e['Id']] = o
                            xs_ = items[i['Id']].setdefault('extras', [])
                            if e['Id'] not in xs_:
                                xs_.append(e['Id'])
            # (each box set's films not in Grifflix, carried by one of its films that is, once films and TV are both counted: John, 18 Nov
            # 2026, "I'll need a list of films missing from the box sets too")
            for nm_, mem_ in (ctj_.get('sets') or {}).items():
                miss_ = [FINFO.get(x_, (x_.split('-', 1)[-1].replace('-', ' ').title(), '', '')) for x_ in mem_ if x_ not in OWNED]
                if nm_ in SFILM and SFILM[nm_] in items:
                    items[SFILM[nm_]].setdefault('csetmiss', {})[snm(nm_)] = [list(m_) for m_ in miss_]
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
            if q.get('cached'):   # (the last look, at once, to show while a new one is made: John, 15 Nov 2026, "could every film's quality not
                # have to be called every time, so I can always see it in admin, and only updated")
                v = load(os.path.expanduser('~/Grifflix/library-view.json'), None)
                return self.send_json(200, v or {'films': [], 'at': 0})
            if q.get('bg'):   # (made after answering, handed over when asked again: on a busy Mac it outlasted the 100 seconds the link waits,
                # and the page said "Failed to fetch": John, 12 Nov 2026)
                with g.plays_lock:
                    J = g.lib_job
                    if J.get('state') == 'done' and time.time() - J['at'] < 600:
                        g.lib_job = {}
                        return self.send_json(J['code'], J['body'])
                    if J.get('state') != 'making' or time.time() - J.get('at', 0) > 1800:
                        g.lib_job = J = {'state': 'making', 'at': time.time()}
                        H = type(self)

                        class Cap:
                            def send_json(c, code, body):
                                c.out = (code, body)

                            def __getattr__(c, n):   # (the handler's own methods, bound to this stand-in; its static ones as they are)
                                a = inspect.getattr_static(H, n)
                                return a.__func__ if isinstance(a, staticmethod) else getattr(H, n).__get__(c)
                        cap = Cap()

                        def run():
                            try:
                                H.api_library(cap, me, user, {}, b)
                                code, body = getattr(cap, 'out', (500, {'error': 'Nothing was made.'}))
                            except Exception as e:
                                code, body = 500, {'error': 'The Mac couldn’t look at the library (%s).' % str(e)[:120]}
                            with g.plays_lock:
                                g.lib_job = {'state': 'done', 'code': code, 'body': body, 'at': time.time()}
                        threading.Thread(target=run, daemon=True).start()
                return self.send_json(202, {'making': True})
            lid = (g.st.get('libraries') or {}).get('movies')
            if not lid:
                return self.send_json(200, {'films': []})
            st = load(os.path.expanduser('~/Grifflix/convert-state.json'), {})
            made = {os.path.normcase(x.get('to') or ''): x for x in (st.get('done') or {}).values()}
            # (each film's details kept, and only those whose file changed read again (the media server's Etag): reading every film's
            # file details took minutes on the old Mac)
            cache_at = os.path.expanduser('~/Grifflix/library-cache.json')
            cache = (load(cache_at, {}) or {}).get('rows') or {}
            quick, start = [], 0
            while True:
                r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Etag,Path',
                                                                       'StartIndex': start, 'Limit': 500, 'SortBy': 'SortName', 'EnableImages': 'false'}) or {}
                quick += r.get('Items') or []
                start += 500
                if start >= (r.get('TotalRecordCount') or 0):
                    break
            need = [it['Id'] for it in quick if not it.get('Etag') or (cache.get(it['Id']) or {}).get('e') != it.get('Etag') or (cache.get(it['Id']) or {}).get('p') != it.get('Path')]
            for n in range(0, len(need), 100):
                r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Ids': ','.join(need[n:n + 100]), 'Fields': 'MediaSources,Path,ProductionYear,Etag'}) or {}
                for it in r.get('Items') or []:
                    ms = (it.get('MediaSources') or [{}])[0]
                    ss = ms.get('MediaStreams') or []
                    v = next((x for x in ss if x.get('Type') == 'Video'), {}) or {}
                    au = [x for x in ss if x.get('Type') == 'Audio']
                    # (picture subtitles (Blu-ray PGS, DVD VobSub): an MP4 can't hold them, so repackaging leaves them in the original: John, 12 Nov 2026)
                    pics = [x for x in ss if x.get('Type') == 'Subtitle' and not x.get('IsTextSubtitleStream') and not x.get('IsExternal')]
                    cache[it['Id']] = {'e': it.get('Etag'), 'p': it.get('Path'), 'w0': conv_why(ms), 'r': {
                        'id': it.get('Id'), 'n': it.get('Name'), 'y': it.get('ProductionYear') or '', 'w': v.get('Width') or 0, 'h': v.get('Height') or 0,
                        'v': (v.get('Codec') or '').lower(), 'b': v.get('BitDepth') or 8, 'mbps': round((ms.get('Bitrate') or v.get('BitRate') or 0) / 1e6, 1),
                        'c': (ms.get('Container') or '').lower().split(',')[0],
                        'ps': ', '.join(sorted(set((x.get('DisplayTitle') or x.get('Language') or '?').split(' - ')[0] for x in pics)))[:120],
                        'gb': round((ms.get('Size') or 0) / 1e9, 1), 'mins': round((it.get('RunTimeTicks') or 0) / 6e8),
                        'a': ', '.join(sorted(set('%s %s' % ((x.get('Codec') or '?').upper(), {1: 'mono', 2: 'stereo', 6: '5.1', 8: '7.1'}.get(x.get('Channels') or 0, '%sch' % (x.get('Channels') or '?'))) for x in au[:4])))}}
            ids = set(it['Id'] for it in quick)
            cache = {k: v for k, v in cache.items() if k in ids}
            rows = []
            for it in quick:
                c_ = cache.get(it['Id'])
                if not c_:
                    continue
                cv = made.get(os.path.normcase(c_.get('p') or ''))
                o = dict(c_['r'])
                # (a film only repackaged keeps what's still to do, making it smaller included: its picture is as it was
                # (John, 13 Nov 2026: Obsession, nearly 30 GB, couldn't be made smaller); one converted here is done)
                o.update({'conv': bool(cv), 'was': (cv or {}).get('before'), 'rp': bool(cv) and str(cv.get('why') or '').startswith('to repackage'),
                          'why': c_.get('w0') if not cv or str(cv.get('why') or '').startswith('to repackage') else ''})
                rows.append(o)
            out = {'films': rows, 'at': int(time.time()), 'read': len(need)}
            try:
                save(cache_at, {'rows': cache})
                save(os.path.expanduser('~/Grifflix/library-view.json'), out)
            except OSError as e:
                log('Couldn’t keep the library:', e)
            self.send_json(200, out)

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

        def api_torbox(self, me, user, q, b):
            """John's: his TorBox key (kept on the Mac only, never sent back) and whether a download is deleted from TorBox once all of it is in
            Grifflix (John, 17 Nov 2026: "can films be deleted from torbox automatically when downloaded?")."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            fp = os.path.expanduser('~/Grifflix/torbox.json')
            c = load(fp, {}) or {}
            if b:
                if 'key' in b:
                    k = str(b.get('key') or '').strip()
                    if k and not re.match(r'^[A-Za-z0-9_-]{16,128}$', k):
                        return self.send_json(400, {'error': 'That doesn’t look like a TorBox API key (Settings › API on torbox.app).'})
                    if k:
                        try:   # (checked with TorBox first)
                            req = urllib.request.Request('https://api.torbox.app/v1/api/user/me', headers={'Authorization': 'Bearer ' + k, 'User-Agent': 'Grifflix'})
                            json.loads(urllib.request.urlopen(req, timeout=30, context=tls()).read())
                        except urllib.error.HTTPError as e:
                            return self.send_json(400, {'error': 'TorBox didn’t accept that key (%d).' % e.code})
                        except Exception as e:
                            return self.send_json(502, {'error': 'TorBox didn’t answer: %s' % str(e)[:120]})
                    c['key'] = k
                if 'auto' in b:
                    c['auto'] = bool(b.get('auto'))
                save(fp, c)
                try:
                    os.chmod(fp, 0o600)
                except OSError:
                    pass
            return self.send_json(200, {'key': bool(c.get('key')), 'auto': bool(c.get('auto'))})

        def api_fetching(self, me, user, q, b):
            """John's only: films coming in from TorBox and Google Drive (gdrive.py's own account of it): the one downloading now and how far,
            those waiting and the names they'll have, and those that came (John, 15 Nov 2026: "Can we see what's running or upcoming from
            the admin page rather than running a script"). POST {look: true}: a look now."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            home = os.path.expanduser('~/Grifflix')
            setup = os.path.exists(os.path.join(home, 'gdrive.py')) and os.path.exists(os.path.join(home, 'rclone.conf'))
            # what's downloading, from the Mac's own list of what's running (not only the downloader's note, which an older version of it
            # didn't keep: John, 15 Nov 2026, "it just says 17 waiting - not anything that's happening right now")
            try:
                import subprocess
                ps = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=10).stdout
            except Exception:
                ps = ''
            live = None
            for line in ps.splitlines():
                m = re.search(r'rclone .*? copy (\S+?):(.*?) (/.*?\.adding)\.d(?: |$)', line)   # (the folder's copy, into "<file>.adding.d")
                if m:
                    live = {'remote': m.group(1), 'file': m.group(2), 'part': m.group(3)}
                    break
            running = bool(live) or any('gdrive.py' in l and ' run' in l for l in ps.splitlines())
            if b and b.get('op') in ('name', 'unname'):   # (John's name for a download, from TMDB, so it comes in right: John, 15 Nov 2026)
                fp = os.path.join(home, 'fetch-names.json')
                N = load(fp, {}) or {}
                key = str(b.get('key') or '')[:500]
                if not key:
                    return self.send_json(400, {'error': 'Which download?'})
                if b['op'] == 'unname':
                    N.pop(key, None)
                else:
                    nm = re.sub(r'[/:\\]', '-', str(b.get('name') or '')).strip()[:150]
                    yr = int(b['year']) if str(b.get('year') or '').isdigit() else None
                    if not nm:
                        return self.send_json(400, {'error': 'What’s it called?'})
                    N[key] = {'name': nm, 'year': yr, 'kind': 'series' if b.get('kind') == 'series' else 'movie', 'tmdb': str(b.get('tmdb') or '')}
                save(fp, N)
                return self.send_json(200, {'ok': True})
            if b and b.get('op') == 'upgrade':   # (a download of a film already here: replace the one here with it, or ignore it: John, 17 Nov 2026)
                fp = os.path.join(home, 'fetch-upgrades.json')
                U = load(fp, {}) or {}
                key, ch = str(b.get('key') or '')[:600], str(b.get('choice') or '')
                if not key or ch not in ('replace', 'ignore', 'undo'):
                    return self.send_json(400, {'error': 'Which download, and replace or ignore?'})
                if ch == 'undo':
                    U.pop(key, None)
                else:
                    U[key] = ch
                save(fp, U)
                log('Downloads: %s %s' % ({'replace': 'to replace the film here with', 'ignore': 'ignored', 'undo': 'asked again about'}[ch], b.get('name') or key))
                if ch == 'replace' and not running:
                    GDRIVE_NOW.set()
                return self.send_json(200, {'ok': True})
            if b and b.get('op') in ('cancel', 'uncancel'):   # (John's: a download he doesn't want, never fetched; the one coming in now stopped
                # and its part thrown away: John, 17 Nov 2026, "I need to be able to cancel downloads")
                fp = os.path.join(home, 'fetch-cancelled.json')
                key, file_ = str(b.get('key') or '')[:500], str(b.get('file') or '')[:500]
                if not (key or file_):
                    return self.send_json(400, {'error': 'Which download?'})
                C = [x for x in (load(fp, []) or []) if isinstance(x, str) and x not in (key, file_)]
                names_ = load(os.path.join(home, 'fetch-cancelled-names.json'), {}) or {}
                if b['op'] == 'cancel':
                    C = [key or file_] + C   # (a series by its folder, a film by its file)
                    names_[key or file_] = {'to': str(b.get('name') or '')[:200], 'at': int(time.time())}
                    stopped = False
                    if live and file_ and (live['file'].strip('/') == os.path.dirname(file_).strip('/') or live['file'].strip('/') == file_.strip('/')):
                        try:
                            import signal as sig
                            for line in ps.splitlines():
                                if 'rclone' in line and live['part'] in line:
                                    os.kill(int(line.split()[0]), sig.SIGTERM)
                                    stopped = True
                        except (OSError, ValueError):
                            pass
                    log('Downloads: cancelled by John: %s%s' % (b.get('name') or file_ or key, ' (stopped part way)' if stopped else ''))
                else:
                    names_.pop(key or file_, None)
                    GDRIVE_NOW.set() if not running else None
                save(fp, C[:500])
                save(os.path.join(home, 'fetch-cancelled-names.json'), {k: v for k, v in names_.items() if k in C})
                return self.send_json(200, {'ok': True})
            if b and b.get('op') in ('first', 'unfirst'):   # (John's next: a download moved to the front: John, 15 Nov 2026)
                fp = os.path.join(home, 'fetch-first.json')
                f = [x for x in (load(fp, []) or []) if isinstance(x, str) and x != str(b.get('file') or '')]
                if b['op'] == 'first' and b.get('file'):
                    f.insert(0, str(b['file'])[:500])
                save(fp, f[:50])
                return self.send_json(200, {'ok': True, 'first': f[:50]})
            if b and b.get('look'):
                if not setup:
                    return self.send_json(400, {'error': 'Google Drive and TorBox aren’t set up on the Mac mini yet.'})
                if not running:
                    GDRIVE_NOW.set()
                return self.send_json(200, {'ok': True, 'running': running})
            try:
                st = json.load(open(os.path.join(home, 'fetch-status.json')))
            except (OSError, ValueError):
                st = {}
            now = st.get('now') if running else None
            if live and (not now or now.get('part') != live['part']):   # (from what's running: its size from the list of what's waiting)
                w = next((x for x in [st.get('now') or {}] + (st.get('waiting') or []) if x.get('file') and os.path.dirname(x['file']) == live['file'].strip('/')), {})
                now = {'from': 'TorBox' if live['remote'] == 'torbox' else 'Google Drive', 'file': live['file'], 'part': live['part'],
                       'to': w.get('to') or os.path.basename(live['part'])[:-len('.adding')].rsplit('.', 1)[0], 'size': w.get('size') or int((w.get('gb') or 0) * 1e9),
                       'since': int(os.path.getctime(live['part'])) if os.path.exists(live['part']) else int(time.time()), 'ep': w.get('ep'), 'series': w.get('series')}
            if now:
                part = now.pop('part', '')
                got = 0
                for f_ in [part] + ([os.path.join(part + '.d', n) for n in os.listdir(part + '.d')] if os.path.isdir(part + '.d') else []):
                    try:   # (while it comes: in "<file>.adding.d", until it's whole. What has really been written, not the file's size: a download in
                        # several pieces makes its file nearly full size at once, and House Party said 75% for hours with nothing coming: John,
                        # 17 Nov 2026, "there's no point in it saying 75% if it's not true")
                        x_ = os.stat(f_)
                        got = max(got, x_.st_size)
                    except OSError:
                        pass
                try:   # (rclone's own count of what has come, from its log: several streams make the file nearly whole at once)
                    with open(part + '.rlog', 'rb') as rf_:
                        rf_.seek(0, 2); rf_.seek(max(0, rf_.tell() - 16384))
                        for line_ in reversed(rf_.read().decode('utf-8', 'replace').splitlines()):
                            if '"stats"' in line_:
                                got = int(json.loads(line_)['stats'].get('bytes') or 0)
                                break
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                # (its speed now, over the last minute or so, not since it began; and how long nothing has come, if it's stalled)
                now_t = time.time(); h = FETCH_SEEN.get(part)
                if not h or h['got'] > got:
                    h = FETCH_SEEN[part] = {'got': got, 'at': now_t, 'moved': now_t, 'rate': 0, 'pg': got, 'pt': now_t}
                if got > h['got']:
                    h['moved'] = now_t
                if now_t - h['pt'] >= 20:
                    h['rate'] = (got - h['pg']) * 8 / (now_t - h['pt']) / 1e6
                    h['pg'], h['pt'] = got, now_t
                h['got'] = got
                for k_ in [k_ for k_ in FETCH_SEEN if k_ != part]:
                    FETCH_SEEN.pop(k_, None)
                took = max(1, now_t - (now.get('since') or now_t))
                rate = h['rate'] if now_t - h['at'] > 30 else got * 8 / took / 1e6
                now.update(got=got, pct=round(min(99.9, 100.0 * got / now['size']), 1) if now.get('size') else 0, mbps=round(rate, 1),
                           still=int(now_t - h['moved']) if now_t - h['at'] > 90 else 0)
            waiting = [x for x in (st.get('waiting') or []) if not (now and x.get('file') == now.get('file'))]
            return self.send_json(200, {'setup': setup, 'running': running, 'now': now, 'waiting': waiting,
                                        'recent': [x for x in st.get('recent') or [] if x.get('ok')],   # (only what came: John, 15 Nov 2026) 'checked': st.get('checked'), 'next': int(GDRIVE_NEXT[0]) or None, 'error': GDRIVE_ERR[0], 'trouble': st.get('trouble') or [], 'hold': st.get('hold'),
                                        'upgrades': st.get('upgrades') or [], 'chosen': load(os.path.join(home, 'fetch-upgrades.json'), {}) or {},
                                        'cancelled': [dict(v, key=k) for k, v in (load(os.path.join(home, 'fetch-cancelled-names.json'), {}) or {}).items()],
                                        'first': load(os.path.join(home, 'fetch-first.json'), []) or [], 'names': load(os.path.join(home, 'fetch-names.json'), {}) or {}})

        def api_convert(self, me, user, q, b):
            """John's: how the conversions are going (convert.py's files in ~/Grifflix), for the admin page."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            home = os.path.expanduser('~/Grifflix')
            pf = os.path.join(home, 'convert-priority.json')   # (John's order: films to do next, and repackaging all first; convert.py reads it before each film)
            P = load(pf, {}) or {}
            P.setdefault('pins', []); P.setdefault('remux_first', True)
            op = b.get('op')
            if op == 'lang':   # (John's answer: which soundtrack is the film's own language; it goes next: John, 15 Nov 2026, "never the dub")
                iid = str(b.get('id') or '')
                lg = str(b.get('lang') or '')[:40]   # (a language's name, or "ok": it was right)
                if not re.match(r'^[0-9a-f]{32}$', iid) or not lg:
                    return self.send_json(400, {'error': 'Which film, and which language?'})
                P.setdefault('langs', {})[iid] = lg
                os.makedirs(home, exist_ok=True)
                save(pf, P)
            if op == 'skip':   # (John's: the film it's on given up, the next started)
                iid = str(b.get('id') or '')
                if not re.match(r'^[0-9a-f]{32}$', iid):
                    return self.send_json(400, {'error': 'Which film?'})
                with open(os.path.join(home, 'convert-skip'), 'w') as f:
                    f.write(iid)
            if op in ('pin', 'unpin', 'remux_first', 'up'):
                iid = g.now_id(str(b.get('id') or ''))   # (a film's id as the site knows it, maybe from before its repackaging)
                if op in ('pin', 'unpin', 'up') and not re.match(r'^[0-9a-f]{32}$', iid):
                    return self.send_json(400, {'error': 'Which film?'})
                P['pins'] = [x for x in P['pins'] if x.get('id') != iid] if op in ('unpin', 'pin', 'up') else P['pins']
                if op == 'pin':   # (force: one John chose from the largest, made smaller whatever the converter thinks of it)
                    P['pins'].append(dict({'id': iid, 'name': str(b.get('name') or '')[:120]}, **({'force': True, 'at': int(time.time())} if b.get('force') else {})))
                elif op == 'up':
                    P['pins'].insert(0, {'id': iid, 'name': str(b.get('name') or '')[:120]})
                elif op == 'remux_first':
                    P['remux_first'] = bool(b.get('on'))
                os.makedirs(home, exist_ok=True)
                save(pf, P)
            st = load(os.path.join(home, 'convert-state.json'), {})
            cur = load(os.path.join(home, 'convert-now.json'), None)
            alive = bool(conv_alive(cur))   # (still running? and really the converter)
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
            rows = sorted((dict(x, id=k) for k, x in done.items()), key=lambda x: -(x.get('at') or 0))[:60]
            self.send_json(200, {'now': cur, 'alive': alive, 'done': len(done), 'failed': [{'name': x.get('name'), 'why': x.get('why')} for x in (st.get('failed') or {}).values()][:20],
                                 'recent': [{'id': x.get('id'), 'name': x.get('name'), 'at': x.get('at'), 'mins': x.get('mins'), 'why': x.get('why'),
                                             'before': x.get('before') or look(x.get('kept')), 'after': x.get('after') or look(x.get('to'))} for x in rows],
                                 'disks': disks(),
                                 'next': [x for x in (cur or {}).get('next') or [] if x.get('id') not in done],   # (one already made, here or on John's Mac, off the list at once: John, 17 Nov 2026) 'left': (cur or {}).get('left'), 'pins': [x for x in P['pins'] if x.get('id') not in (st.get('done') or {}) and x.get('id') not in (st.get('failed') or {})], 'remux_first': P['remux_first'], 'log': tail,
                                 'ask': [dict(v, id=k, chosen=(P.get('langs') or {}).get(k)) for k, v in (st.get('ask') or {}).items()]})

        def api_cfeat(self, me, user, q, b):
            """A Criterion film's special features (read from criterion.com by John's browser), for its page."""
            iid = str(q.get('id') or '')
            if not re.match(r'^[0-9a-f]{32}$', iid):
                return self.send_json(400, {'error': 'Which film?'})
            if not CFEAT or time.time() - CFEAT.get('_at', 0) > 1800:
                CFEAT.clear()
                CFEAT.update({k: v for k, v in crit_pairs(g)})
                CFEAT['_at'] = time.time()
            sl = CFEAT.get(iid)
            F_ = (load(os.path.expanduser('~/Grifflix/criterion-features.json'), {}) or {}).get('films') or {}
            return self.send_json(200, {'features': F_.get(sl) or [] if sl else [], 'page': ('https://www.criterion.com/films/' + sl) if sl else ''})

        def api_criterionmissing(self, me, user, q, b):
            """John's only: every box set's films not in Grifflix, one row each, for a spreadsheet (John, 18 Nov 2026: "Want one list of
            every box set film you're missing?" ... "Yes")."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            return self.send_json(200, {'rows': crit_missing(g)})

        def api_bin(self, me, user, q, b):
            """John's only: the Grifflix Bin (what was deleted, kept 30 days): how much is in it, and emptied now (John, 18 Nov 2026:
            "I should have a button to do this, and know how much is in it")."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            import shutil
            bins = grifflix_bins(g)
            if b and b.get('op') == 'empty':
                n = 0
                for bn in bins:
                    for x in os.listdir(bn):
                        if x.startswith('.'):
                            continue
                        p_ = os.path.join(bn, x)
                        try:
                            shutil.rmtree(p_) if os.path.isdir(p_) and not os.path.islink(p_) else os.remove(p_)
                            n += 1
                        except OSError as e:
                            log('Grifflix Bin: couldn’t delete %s: %s' % (x, e))
                log('Grifflix Bin: emptied by John (%d)' % n)
            items, size = [], 0
            for bn in bins:
                for x in sorted(os.listdir(bn)):
                    if x.startswith('.'):
                        continue
                    p_ = os.path.join(bn, x)
                    sz = 0
                    if os.path.isdir(p_):
                        for t_, _, fs in os.walk(p_):
                            for f_ in fs:
                                try:
                                    sz += os.path.getsize(os.path.join(t_, f_))
                                except OSError:
                                    pass
                    else:
                        try:
                            sz = os.path.getsize(p_)
                        except OSError:
                            pass
                    size += sz
                    items.append({'name': x, 'size': sz})
            return self.send_json(200, {'items': items[:300], 'count': len(items), 'size': size})

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
                return self.send_json(200, {'sha': have.get('sha', ''), 'at': have.get('at', 0), 'files': have.get('files', []), 'last': load(os.path.join(APP_DIR, 'update-result.json'), None)})

            work = mac_update

            def finish(r):
                save(os.path.join(APP_DIR, 'update-result.json'), r)
                if r.get('restarting'):
                    threading.Timer(1.5, lambda: os._exit(0)).start()   # (launchd starts the new gateway)

            if b.get('bg'):   # (the work done after answering: fetching from GitHub can outlast the 100 seconds Cloudflare waits for an answer,
                # and the admin page said the Mac wasn't answering though it was: John, 12 Nov 2026. The page watches /health for the new version)
                def run():
                    try:
                        r = work()
                    except Exception as e:
                        r = {'status': 500, 'error': str(e)[:200], 'at': int(time.time())}
                    finish(r)
                threading.Thread(target=run, daemon=True).start()
                log('Update the Mac: asked from the admin page')
                return self.send_json(202, {'started': True, 'sha': have.get('sha', ''), 'now': int(time.time())})
            r = work()
            self.send_json(r.get('status', 200), r)
            finish(r)

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

        def api_scan(self, me, user, q, b):
            """John's: the media server asked to look through the drive now, and how far it's got (John, 17 Nov 2026: "an update button that
            just gets Grifflix to check its library and update"). POST: started; GET: {running, pct, done, at}. The admin page then sends
            the films to the site."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            if b and not (SCANJOB.get('t') and SCANJOB['t'].is_alive()):
                def run():
                    SCANJOB.update({'pct': 0, 'done': False, 'err': None, 'at': time.time()})
                    try:
                        g.jf.call('POST', '/Library/Refresh')
                        time.sleep(8)
                        t0 = time.time()
                        while time.time() - t0 < 1800:
                            tasks = g.jf.call('GET', '/ScheduledTasks', query={'isHidden': 'false'}) or []
                            t_ = next((x for x in tasks if x.get('Key') == 'RefreshLibrary'), None)
                            if not t_ or t_.get('State') != 'Running':
                                break
                            SCANJOB['pct'] = round(t_.get('CurrentProgressPercentage') or 0)
                            time.sleep(4)
                        g.forget()
                        SCANJOB['done'] = True
                        log('Checked the library, as John asked')
                    except Exception as e:
                        SCANJOB['err'] = str(e)[:200]
                    SCANJOB['at'] = time.time()
                SCANJOB['t'] = threading.Thread(target=run, daemon=True)
                SCANJOB['t'].start()
            return self.send_json(200, {'running': bool(SCANJOB.get('t') and SCANJOB['t'].is_alive()), 'pct': SCANJOB.get('pct', 0),
                                        'done': SCANJOB.get('done', False), 'err': SCANJOB.get('err'), 'at': SCANJOB.get('at')})

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
            many = [str(x) for x in (b.get('items') or []) if ok(str(x))][:200]   # (several at once, from the collection's own page: John, 16 Nov 2026)
            if op == 'in':
                held = [s['Id'] for s in sets if any(i['Id'] == item for i in g.items(ParentId=s['Id'], Fields='', EnableImageTypes='').get('Items', []))]   # (10.8 ignores Ids with ParentId: each one's list, checked)
                return self.send_json(200, {'collections': [{'id': s['Id'], 'name': s['Name']} for s in sets], 'in': held})
            if op in ('add', 'remove') and cid and (item or many):
                g.jf.call('POST' if op == 'add' else 'DELETE', '/Collections/%s/Items' % cid, query={'Ids': ','.join(many or [item])})
            elif op == 'describe' and cid:   # (its description, John's own)
                full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, cid))
                full['Overview'] = str(b.get('text') or '').strip()[:4000]
                full['LockedFields'] = sorted(set((full.get('LockedFields') or []) + ['Overview']))
                g.jf.call('POST', '/Items/%s' % cid, full)
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
            g.publish_soon = time.time() + 90   # (the site follows by itself)
            self.send_json(200, {'ok': True, 'id': cid})

        def api_collection(self, me, user, q, b):
            o = self.collection_out(q.get('id', ''), user['progress'], False)
            self.send_json(200 if o else 404, o or {'error': 'No such collection.'})

        def api_search(self, me, user, q, b):
            term = re.sub(r'[’‘`´]', "'", (q.get('q') or '')).strip()   # (a phone's curly apostrophe as the titles' straight one: "it’s" found It's a Wonderful Life: John, 16 Nov 2026)
            if len(term) < 2:
                return self.send_json(200, {'items': []})
            r = g.items(SearchTerm=term, Recursive='true', IncludeItemTypes='Movie,Series,Episode,Video', Limit=40)
            self.send_json(200, {'items': [item_out(i, user['progress']) for i in shown(r.get('Items', []))]})

        def api_play(self, me, user, q, b):
            g.watched()   # (first of all, before the media server is asked anything: the converter pauses, and the disk is the film's; a busy
            # converter kept the media server from answering in time, so a film never started and the converter never paused: John, 12 Nov 2026)
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
            v0c, v0b = (vs0.get('Codec') or '').lower(), vs0.get('BitDepth') or (10 if '10' in (vs0.get('Profile') or '') else 8)
            plays = (v0c == 'h264' and v0b <= 8) or (v0c in ('hevc', 'h265') and hevc)   # (the device plays the picture as it is)
            hq = {'MediaSourceId': msid, 'DeviceId': dev, 'PlaySessionId': ps, 'VideoCodec': 'h264,hevc' if hevc else 'h264', 'AudioCodec': 'aac,mp3',
                  'TranscodingMaxAudioChannels': 2, 'MaxStreamingBitrate': br, 'SegmentContainer': 'mp4', 'MinSegments': 1,
                  'BreakOnNonKeyFrames': 'True', 'RequireAvc': 'false', 'g': key}
            if plays:   # (no rate limit then, and the picture copied: with a limit, the media server converts any file whose picture has no rate of its
                # own recorded, as most MKV files haven't, though the device plays it: John, 12 Nov 2026, Rushmore converted for an iPhone that plays it)
                del hq['MaxStreamingBitrate']
                hq.update(AllowVideoStreamCopy='true', EnableAutoStreamCopy='true')
            hls = ('/v/%s/master.m3u8?' % iid) + urllib.parse.urlencode(hq)
            direct = None
            vs = [s for s in ms.get('MediaStreams', []) if s.get('Type') == 'Video']
            aus = [s for s in ms.get('MediaStreams', []) if s.get('Type') == 'Audio']
            # (the whole file, when it's an MP4 whose picture and first sound this device plays as they are: the surest way to play
            # anywhere. AC3 and E-AC3 only where the device says it plays them (Safari does, Chrome doesn't): John, 12 Nov 2026)
            snd = ('aac', 'mp3') + (('ac3', 'eac3') if q.get('ac3') == '1' else ())
            if not q.get('nodirect') and any(c in ('mp4', 'm4v', 'mov') for c in (ms.get('Container') or '').split(',')) and vs and plays \
                    and all((a.get('Codec') or '') in snd for a in aus[:1]):
                direct = '/v/%s/stream?' % iid + urllib.parse.urlencode({'Static': 'true', 'MediaSourceId': msid, 'g': key})
            subs = self.subs_out(iid, ms, key)
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
            self.send_json(200, {'hls': hls, 'direct': direct, 'hv': bool(hevc and v0c in ('hevc', 'h265')), 'subs': subs, 'ps': ps, 'device': dev,
                                 'pos': user['progress'].get(iid, {}).get('pos', 0), 'secs': round((it.get('RunTimeTicks') or 0) / 1e7)})

        def subs_out(self, iid, ms, key):
            """A film's text subtitles for the player: each with whether it's a file beside the film (which a member can move
            earlier or later, for everyone), and a mark that changes when it's moved (so no old copy is kept)."""
            msid, out = ms.get('Id') or iid, []
            for s in ms.get('MediaStreams', []):
                if s.get('Type') != 'Subtitle' or not s.get('IsTextSubtitleStream'):
                    continue
                path = s.get('Path') or ''
                ext = bool(s.get('IsExternal') and path.lower().endswith('.srt') and os.path.exists(path))
                mark = int(os.path.getmtime(path)) if ext else 0
                label = s.get('DisplayTitle') or s.get('Language') or 'Subtitles'
                clean = re.sub(r'^Grifflix\s*-\s*|\s*-\s*(SUBRIP|SRT)\b|\s*-\s*External\b', '', label, flags=re.I).strip()
                if '.Grifflix.' in os.path.basename(path):   # (one found and timed here: told apart from the film's own)
                    clean = (clean or s.get('Language') or 'Subtitles') + ' · timed to the film'
                out.append({'label': clean or label, 'mine': ext and '.Grifflix.' in os.path.basename(path),
                            'lang': s.get('Language') or '', 'sid': s['Index'], 'ext': ext,
                            'url': '/v/%s/%s/Subtitles/%d/0/Stream.vtt?g=%s%s' % (iid, msid, s['Index'], key, '&m=%d' % mark if mark else '')})
            return out

        @staticmethod
        def subs_ready():   # (subtitles.py installed after the gateway started: loaded now)
            global SUBS
            if SUBS is None:
                try:
                    import subtitles as _s
                    SUBS = _s
                except Exception:
                    pass
            return SUBS is not None

        def subs_client(self):
            o = g.st.setdefault('opensubs', {})
            return SUBS.OpenSubs(o, lambda: save(SETTINGS, g.st), tls())

        def api_tmdbfind(self, me, user, q, b):
            """John's only: a title looked up on TMDB, through the media server (films or series), for naming a download before it comes in."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            kind = 'Series' if q.get('kind') == 'series' else 'Movie'
            info = {'Name': str(q.get('q') or '').strip()[:120]}
            if str(q.get('year') or '').isdigit():
                info['Year'] = int(q['year'])
            if not info['Name']:
                return self.send_json(400, {'error': 'Which title?'})
            res = g.jf.call('POST', '/Items/RemoteSearch/' + kind, {'SearchInfo': info, 'IncludeDisabledProviders': False}) or []
            return self.send_json(200, {'results': [{'name': r.get('Name'), 'year': r.get('ProductionYear'), 'overview': (r.get('Overview') or '')[:300],
                                                     'image': r.get('ImageUrl'), 'tmdb': (r.get('ProviderIds') or {}).get('Tmdb')} for r in res[:15]]})

        def api_convertplan(self, me, user, q, b):
            """John's only: a film converted on the Mac he's using (John, 16 Nov 2026: "Can I have a way to convert using the machine I'm on
            easily": "A button on a film"). The Mac mini plans it as its own converter would (language, soundtracks, subtitles), hands that
            Mac the job by a one-time address, leaves the film alone meanwhile, and puts the result in place when it's done."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            code, j = mac_job(str(q.get('id') or ''))
            return self.send_json(code, j)

        def api_diskhealth(self, me, user, q, b):
            """John's only: the G-Drive's health, as last looked at (POST: looked at now)."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            if b or not DHEALTH:
                h = disk_health()
                DHEALTH.clear(); DHEALTH.update(h)
                save(os.path.expanduser('~/Grifflix/disk-health.json'), h)
            return self.send_json(200, DHEALTH)

        def api_backup(self, me, user, q, b):
            """John's only: the nightly backup to Google Drive, how the last went (POST: one now)."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            if b:
                BACKUP_NOW.set()
            return self.send_json(200, dict(load(BACKUP_AT, {}) or {}, running=bool(backup_running()) or bool(b), keep=BACKUP_KEEP, to=BACKUP_TO))

        def api_claudeasks(self, me, user, q, b):
            """John's only: what Claude has asked to have done on the Mac mini, and his Allow or Decline."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            if b:
                code, j = claude_answer(g, str(b.get('id') or ''), b.get('op') == 'allow')
                if code != 200:
                    return self.send_json(code, j)
            L = [{k: v for k, v in x.items() if k != 'files'} for x in (load(ACTIONS_AT, []) or [])]
            return self.send_json(200, {'asks': L[-30:], 'plex_off': bool(g.st.get('plex_off'))})

        def api_waiting(self, me, user, q, b):
            """John's only: what he's ticked off on the admin page's Waiting on you (John, 18 Nov 2026: "I could do with a waiting on you
            section in my admin, so I know what has and hasn't been done"). The list itself is grifflix/waiting.json on the site."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            at = os.path.expanduser('~/Grifflix/waiting-done.json')
            d = load(at, {}) or {}
            if b:
                iid = str(b.get('id') or '')
                if not re.match(r'^[a-z0-9-]{1,80}$', iid):
                    return self.send_json(400, {'error': 'Which?'})
                if b.get('done'):
                    d[iid] = int(time.time())
                else:
                    d.pop(iid, None)
                save(at, d)
            return self.send_json(200, {'done': d})

        def api_macqueue(self, me, user, q, b):
            """John's only: films queued for his own Mac, which takes them up by itself, one after another, wherever he queued them from
            (John, 18 Nov 2026: "Is it possible to switch on convert on my Mac remotely?" ... "Yes"). Its helper's key is made here, shown once."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            Q = load(MACQ, []) or []
            if b:
                op, iid = str(b.get('op') or ''), str(b.get('id') or '')
                if op == 'key':   # (a new key for his Mac's helper: the old one stops working)
                    k = secrets.token_urlsafe(24)
                    g.st['macagent'] = {'key': hashlib.sha256(k.encode()).hexdigest(), 'made': int(time.time())}
                    save(SETTINGS, g.st)
                    return self.send_json(200, {'key': k})
                if not re.match(r'^[0-9a-f]{32}$', iid):
                    return self.send_json(400, {'error': 'Which film?'})
                Q = [x for x in Q if x.get('id') != iid]
                old = next((x for x in (load(MACQ, []) or []) if x.get('id') == iid), {})
                ent = dict({'id': iid, 'name': str(b.get('name') or old.get('name') or '')[:200], 'at': int(time.time())},
                           **({'force': True} if b.get('force') or old.get('force') else {}))   # (one of the largest, to make smaller: John, 18 Nov 2026)
                if op == 'add':
                    Q.append(ent)
                elif op == 'first':
                    Q.insert(0, ent)
                save(MACQ, Q)
                log('Queue for John’s Mac: %s %s' % ({'add': 'added', 'first': 'put first', 'remove': 'taken off'}.get(op, op), b.get('name') or iid))
            a = g.st.get('macagent') or {}
            return self.send_json(200, {'queue': Q, 'setup': bool(a.get('key')), 'seen': MACAGENT.get('seen'), 'home': MACAGENT.get('home')})

        def api_fileinfo(self, me, user, q, b):
            """John's only: a film's file as it is, for its Edit panel (John, 16 Nov 2026: "I need to see file format details in the edit
            section too"): its name and size, container, picture, bitrate, length, soundtracks and subtitles, and what the converter would do."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            iid = str(q.get('id') or '')
            if not re.match(r'^[0-9a-f]{32}$', iid):
                return self.send_json(400, {'error': 'Which film?'})
            it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'MediaSources,Path'}) or {}
            out = []
            for ms in it.get('MediaSources') or []:
                st = ms.get('MediaStreams') or []
                v = next((x for x in st if x.get('Type') == 'Video'), {}) or {}
                out.append({
                    'file': os.path.basename(ms.get('Path') or it.get('Path') or ''), 'folder': os.path.basename(os.path.dirname(ms.get('Path') or it.get('Path') or '')),
                    'container': ms.get('Container'), 'gb': round((ms.get('Size') or 0) / 1e9, 2), 'mbps': round((ms.get('Bitrate') or 0) / 1e6, 1),
                    'mins': round((ms.get('RunTimeTicks') or 0) / 6e8),
                    'video': {'codec': v.get('Codec'), 'profile': v.get('Profile'), 'w': v.get('Width'), 'h': v.get('Height'), 'bits': v.get('BitDepth'),
                              'hdr': v.get('VideoRange') if v.get('VideoRange') not in (None, 'SDR') else None, 'fps': round(v.get('RealFrameRate') or v.get('AverageFrameRate') or 0, 3)} if v else None,
                    'audio': [{'lang': a.get('Language'), 'codec': a.get('Codec'), 'ch': a.get('Channels'), 'layout': a.get('ChannelLayout'), 'title': a.get('Title') or a.get('DisplayTitle'),
                               'default': bool(a.get('IsDefault'))} for a in st if a.get('Type') == 'Audio'],
                    'subs': [{'lang': x.get('Language'), 'codec': x.get('Codec'), 'external': bool(x.get('IsExternal')), 'forced': bool(x.get('IsForced')),
                              'title': x.get('Title') or x.get('DisplayTitle')} for x in st if x.get('Type') == 'Subtitle'],
                    'convert': conv_why(ms) or ''})
            return self.send_json(200, {'sources': out})

        def api_convertjobs(self, me, user, q, b):
            """John's only: every film being converted on his Mac now, and those finished in the last hour, for the admin page from anywhere
            (John, 17 Nov 2026: "If something is converting in the Mac I'd like to be able to see progress from anywhere I'm signed in")."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            out, now_ = [], time.time()
            # (a job that went quiet is left off once John's Mac has since done or started another, three hours after its last word, or once
            # the film has been converted since it began (John, 17 Nov 2026: Career Girls "no word from your Mac since 14:48 ... but it was
            # completed"); one finished shows as done for half an hour, then goes ("I've since updated another film via my mac and there's
            # no message"))
            done_ = (load(os.path.expanduser('~/Grifflix/convert-state.json'), {}) or {}).get('done') or {}
            last_mac = max([x.get('seen') or x['at'] for x in CONVJOBS.values() if x.get('state') not in ('waiting', 'notstarted')] or [0])
            for j in sorted(CONVJOBS.values(), key=lambda j: -j['at']):
                st_ = j.get('state') or 'waiting'
                if st_ in ('notstarted', 'lost') or (st_ in ('done', 'failed') and now_ - (j.get('ended') or j['at']) > 1800) or now_ - j['at'] > 86400:
                    continue
                if st_ in ('started', 'converting', 'copying'):
                    heard = j.get('seen') or j['at']
                    if now_ - heard > 600 and (last_mac > heard + 60 or now_ - heard > 3 * 3600 or ((done_.get(j['id']) or {}).get('at') or 0) > j['at']):
                        continue
                if st_ == 'waiting' and now_ - j['at'] > 900:
                    continue
                quiet = (st_ == 'converting' and now_ - (j.get('seen') or j['at']) > 600) or (st_ == 'started' and now_ - (j.get('seen') or j['at']) > 180)
                out.append({'id': j['id'], 'name': j['plan']['item'].get('name'), 'state': 'quiet' if quiet else st_, 't': j.get('t') or 0, 'dur': j['plan'].get('dur') or 0,
                            'speed': j.get('speed') or '', 'stage': j.get('stage') or '', 'error': j.get('error') or '', 'at': j['at'], 'seen': j.get('seen'), 'ended': j.get('ended')})
            seen_ids = set()
            return self.send_json(200, {'jobs': [x for x in out if not (x['id'] in seen_ids or seen_ids.add(x['id']))]})   # (the newest for each film)

        def api_convertjob(self, me, user, q, b):
            """John's only: how a film's conversion on his Mac is going, for its Edit panel: waiting for the Mac to start it, converting
            (how far, how fast), checking (the Mac mini putting it in place), done or failed."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            iid = str(q.get('id') or '')
            jobs = sorted((j for j in CONVJOBS.values() if j.get('id') == iid), key=lambda j: j['at'])
            if not jobs:
                return self.send_json(200, {'state': 'none'})
            j = jobs[-1]
            if (j.get('state') or 'waiting') == 'waiting' and time.time() - j['at'] > 900:   # (John's Mac never took it up: said so, and the film left to the Mac mini again)
                j['state'] = 'notstarted'
                home = os.path.expanduser('~/Grifflix')
                claims = load(os.path.join(home, 'convert-claims.json'), {}) or {}
                if claims.pop(iid, None) is not None:
                    save(os.path.join(home, 'convert-claims.json'), claims)
                log('Not started on John’s Mac: %s (set up %s)' % (j['plan']['item'].get('name'), time.strftime('%d %b %H:%M', time.localtime(j['at']))))
                convjobs_save()
            if (j.get('state') == 'converting' and time.time() - (j.get('seen') or j['at']) > 600) or (j.get('state') == 'started' and time.time() - (j.get('seen') or j['at']) > 180):
                # (no word from it: stopped, the Mac asleep, or its window waiting on a question; the film the Mac mini's again)
                home = os.path.expanduser('~/Grifflix')
                claims = load(os.path.join(home, 'convert-claims.json'), {}) or {}
                if claims.pop(iid, None) is not None:
                    save(os.path.join(home, 'convert-claims.json'), claims)
                return self.send_json(200, {'state': 'quiet', 't': j.get('t') or 0, 'dur': j['plan'].get('dur') or 0, 'at': j['at'], 'seen': j.get('seen'), 'stage': j.get('stage') or ''})
            return self.send_json(200, {'stage': j.get('stage') or '', 'state': j.get('state') or 'waiting', 't': j.get('t') or 0, 'dur': j['plan'].get('dur') or 0, 'speed': j.get('speed') or '',
                                        'error': j.get('error') or '', 'at': j['at'], 'seen': j.get('seen')})

        def api_rename(self, me, user, q, b):
            """John's only: the name a film or series shows by, kept (the media server told not to change it again): two copies of one film
            told apart, "It's a Wonderful Life (Colour)" (John, 16 Nov 2026). An empty name gives it back its own."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            iid, name = str(b.get('id') or ''), re.sub(r'\s+', ' ', str(b.get('name') or '')).strip()[:200]
            if not re.match(r'^[0-9a-f]{32}$', iid):
                return self.send_json(400, {'error': 'Which film?'})
            full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid)) or {}
            if not full:
                return self.send_json(404, {'error': 'That film isn’t there.'})
            locked = [x for x in (full.get('LockedFields') or []) if x != 'Name']
            if name:
                full['Name'] = name
                locked.append('Name')
            full['LockedFields'] = locked
            g.jf.call('POST', '/Items/%s' % iid, full)
            if not name:   # (its own name again: read afresh)
                g.jf.call('POST', '/Items/%s/Refresh' % iid, query={'metadataRefreshMode': 'FullRefresh', 'replaceAllMetadata': 'false'})
            g.forget()
            g.publish_soon = time.time() + 60
            log('Renamed by John: %s → %s' % (full.get('Name') if not name else iid, name or '(its own name)'))
            return self.send_json(200, {'ok': True, 'name': name})

        def api_images(self, me, user, q, b):
            """John's only: the pictures TMDB (and the media server's other sources) have for a film, poster or background, and one chosen
            from them used instead (John, 16 Nov 2026: "can I choose a different image easily from the page")."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            src = b or q
            iid, kind = str(src.get('id') or ''), {'poster': 'Primary', 'backdrop': 'Backdrop', 'thumb': 'Thumb'}.get(str(src.get('kind') or 'poster'), 'Primary')
            if not re.match(r'^[0-9a-f]{32}$', iid):
                return self.send_json(400, {'error': 'Which film?'})
            # (the picture it had kept first, every time, and one tap puts it back: John, 16 Nov 2026, The Freshman's Criterion poster
            # gone after a new one was chosen, with no way back)
            keep = os.path.join(APP_DIR, 'pictures-before')
            mine = lambda: sorted(n for n in (os.listdir(keep) if os.path.isdir(keep) else []) if n.startswith('%s-%s-' % (iid, kind)))
            if not b:
                if src.get('before'):
                    return self.send_json(200, {'before': len(mine())})
                local = []
                try:   # (the pictures in its own folder, small ones inline: John's own posters there were no choice before)
                    for n, full in folder_pics(g, iid)[:8]:
                        if os.path.getsize(full) <= 6e6:
                            local.append({'name': n, 'data': 'data:image/%s;base64,%s' % ('png' if n.lower().endswith('.png') else 'webp' if n.lower().endswith('.webp') else 'jpeg', base64.b64encode(open(full, 'rb').read()).decode())})
                except Exception as e:
                    log('  (its folder’s pictures couldn’t be read: %s)' % e)
                r = g.jf.call('GET', '/Items/%s/RemoteImages' % iid, query={'type': kind, 'includeAllLanguages': 'true', 'limit': 60}) or {}
                imgs = sorted(r.get('Images') or [], key=lambda x: (x.get('Language') not in ('en', None, ''), -(x.get('CommunityRating') or 0), -(x.get('VoteCount') or 0)))
                return self.send_json(200, {'images': [{'url': x.get('Url'), 'thumb': x.get('ThumbnailUrl') or x.get('Url'), 'w': x.get('Width'), 'h': x.get('Height'),
                                                        'lang': x.get('Language'), 'from': x.get('ProviderName')} for x in imgs[:60] if x.get('Url')], 'before': len(mine()), 'local': local})
            if b.get('undo'):
                L = mine()
                if not L:
                    return self.send_json(404, {'error': 'There isn’t an earlier one kept.'})
                pic = open(os.path.join(keep, L[-1]), 'rb').read()
                ct = 'image/png' if pic[:4] == b'\x89PNG' else 'image/jpeg'
                g.jf.raw('POST', '/Items/%s/Images/%s' % (iid, kind), base64.b64encode(pic), ct)
                os.remove(os.path.join(keep, L[-1]))
                g.forget()
                g.publish_soon = time.time() + 60
                log('The earlier %s put back by John for %s' % (kind.lower(), iid))
                return self.send_json(200, {'ok': True, 'before': len(L) - 1})
            url = str(b.get('url') or '')
            lp = None
            if b.get('local'):
                lp = dict(folder_pics(g, iid)).get(str(b['local']))
                if not lp:
                    return self.send_json(404, {'error': 'That picture isn’t in its folder now.'})
            elif not url.startswith('http'):
                return self.send_json(400, {'error': 'Which picture?'})
            try:   # (the one it has now, kept: the last ten for each)
                pic = g.jf.raw('GET', '/Items/%s/Images/%s' % (iid, kind))
                if pic:
                    os.makedirs(keep, exist_ok=True)
                    open(os.path.join(keep, '%s-%s-%d.%s' % (iid, kind, time.time(), 'png' if pic[:4] == b'\x89PNG' else 'jpg')), 'wb').write(pic)
                    for old in mine()[:-10]:
                        os.remove(os.path.join(keep, old))
            except Exception as e:
                log('  (its %s couldn’t be kept first: %s)' % (kind.lower(), e))
            if lp:   # (one from its folder: a copy of it given to the media server; the file itself left where it is)
                pic = open(lp, 'rb').read()
                g.jf.raw('POST', '/Items/%s/Images/%s' % (iid, kind), base64.b64encode(pic), 'image/png' if pic[:4] == b'\x89PNG' else 'image/webp' if pic[8:12] == b'WEBP' else 'image/jpeg')
            else:
                g.jf.call('POST', '/Items/%s/RemoteImages/Download' % iid, query={'type': kind, 'imageUrl': url})
            g.forget()
            g.publish_soon = time.time() + 60
            log('A new %s chosen by John for %s' % (kind.lower(), iid))
            return self.send_json(200, {'ok': True})

        def api_identify(self, me, user, q, b):
            """John's only: a film or series the media server named wrong, or didn't know, put right by choosing it from TMDB, as the media
            server's own Identify does (John, 15 Nov 2026: "I'd like to be able to do it from the app, finding with tmdb").
            GET id, q (a title), year: what TMDB has. POST {id, pick}: that one applied, its details and pictures read again."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            iid = str((b or q or {}).get('id') or '')
            if not re.match(r'^[0-9a-f]{32}$', iid):
                return self.send_json(400, {'error': 'Which film?'})
            it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid)) or {}
            kind = 'Series' if it.get('Type') == 'Series' else 'Movie'
            if not b:
                name = str(q.get('q') or it.get('Name') or '').strip()[:120]
                year = int(q['year']) if str(q.get('year') or '').isdigit() else None
                info = {'Name': name}
                if year:
                    info['Year'] = year
                res = g.jf.call('POST', '/Items/RemoteSearch/' + kind, {'SearchInfo': info, 'ItemId': iid, 'IncludeDisabledProviders': False}) or []
                out = []
                for r in res[:20]:
                    pid = r.get('ProviderIds') or {}
                    out.append({'name': r.get('Name'), 'year': r.get('ProductionYear'), 'overview': (r.get('Overview') or '')[:300],
                                'image': r.get('ImageUrl'), 'tmdb': pid.get('Tmdb'), 'imdb': pid.get('Imdb'), 'raw': r})
                return self.send_json(200, {'kind': kind, 'name': it.get('Name'), 'year': it.get('ProductionYear'), 'results': out})
            pick = (b or {}).get('pick') or {}
            if not isinstance(pick, dict) or not (pick.get('ProviderIds') or {}):
                return self.send_json(400, {'error': 'Which one?'})
            # (John's own pictures in the film's folder (his Criterion posters) kept: replacing all pictures made the media server delete
            # them, The Freshman's poster.jpg and fanart.jpg gone for good: John, 16 Nov 2026. With pictures of its own in its folder, only
            # its details are taken from the match; and every picture in its folder is copied aside first, whatever happens)
            apply_match(g, iid, pick)
            g.forget()
            g.publish_soon = time.time() + 180   # (the site's copy brought up to date by itself, once the media server has read it)
            log('Identified by John: %s → %s (%s)' % (it.get('Name'), pick.get('Name'), pick.get('ProductionYear') or '?'))
            try:   # (its folder and files given the right name too: John, 15 Nov 2026, "I want the folder names corrected too")
                did = rename_to(g, iid, pick.get('Name') or '', pick.get('ProductionYear'), pick)
            except Exception as e:
                did = 'its folder couldn’t be renamed (%s)' % e
                log('  rename:', e)
            return self.send_json(200, {'ok': True, 'name': pick.get('Name'), 'year': pick.get('ProductionYear'), 'renamed': did})

        def api_delete(self, me, user, q, b):
            """John's only: a film taken out of Grifflix and deleted from the drive for good, and the media server asked to look (John, 15
            Nov 2026: "Is is possible for me (and only me) to delete directly from Grifflix?"; no bin since 16 Nov 2026: see below). Its folder goes whole when the film is the only one in it; otherwise the
            film's own files. A TV series: its whole folder."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            iid = str((b or {}).get('id') or '')
            if not re.match(r'^[0-9a-f]{32}$', iid):
                return self.send_json(400, {'error': 'Which film?'})
            it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path'}) or {}
            path = it.get('Path') or ''
            series = it.get('Type') == 'Series'   # (a TV series too, its whole folder: John, 16 Nov 2026, "I don't have any option to Delete TV Series")
            if it.get('Type') not in ('Movie', 'Series') or not path or not os.path.exists(path):
                return self.send_json(404, {'error': 'That ' + ('series’ folder' if series else 'film’s file') + ' isn’t there.'})
            roots = [os.path.realpath(l) for v in g.jf.call('GET', '/Library/VirtualFolders') or [] if (v.get('CollectionType') or '') == ('tvshows' if series else 'movies') for l in v.get('Locations') or []]
            real = os.path.realpath(path)
            root = next((r for r in roots if real.startswith(r.rstrip('/') + '/')), None)
            if not root:
                return self.send_json(400, {'error': 'That ' + ('series isn’t in the TV folder.' if series else 'film isn’t in the films folder.')})
            if series:
                if not os.path.isdir(real) or os.path.dirname(real) != root.rstrip('/'):   # (only a series' own folder, straight inside the TV folder)
                    return self.send_json(400, {'error': 'That series isn’t in a folder of its own.'})
                what = [real]
            else:
                folder = os.path.dirname(real)
                vids = ('.mp4', '.mkv', '.m4v', '.mov', '.avi', '.ts', '.wmv', '.mpg', '.mpeg', '.webm')
                others = [n for n in os.listdir(folder) if n.lower().endswith(vids) and not n.startswith('.') and os.path.join(folder, n) != real and 'sample' not in n.lower()] if folder != root.rstrip('/') else ['(the films folder)']
                stem = os.path.splitext(os.path.basename(real))[0]
                what = [folder] if not others else [os.path.join(folder, n) for n in os.listdir(folder) if n.startswith(stem)]
            # (deleted for good, the space freed at once, not kept in a bin: John asks twice first. John, 16 Nov 2026: "I also don't want
            # what I delete to be held if I'm sure, because main reason I'd delete is to save space")
            import shutil
            freed = 0
            for w in what:
                if os.path.isdir(w):
                    for d_, _, fs in os.walk(w):
                        for f_ in fs:
                            try:
                                freed += os.path.getsize(os.path.join(d_, f_))
                            except OSError:
                                pass
                    # (a file already gone is no fault: the drive's hidden "._" companions go by themselves with their own file, and
                    # each delete stopped part way, pressed again and again: John, 16-17 Nov 2026, the log's FileNotFoundError)
                    def gone_ok(fn, path_, exc):
                        e_ = exc[1] if isinstance(exc, tuple) else exc
                        if not isinstance(e_, FileNotFoundError):
                            raise e_
                    shutil.rmtree(w, onerror=gone_ok)
                else:
                    try:
                        freed += os.path.getsize(w)
                    except OSError:
                        pass
                    try:
                        os.remove(w)
                    except FileNotFoundError:
                        pass
            try:
                g.jf.call('POST', '/Library/Refresh')
            except Exception:
                pass
            g.forget()
            g.publish_soon = time.time() + 180   # (the site follows by itself, once the media server has looked)
            log('Deleted by John, for good: %s (%s), %.1f GB freed' % (it.get('Name'), it.get('ProductionYear') or '?', freed / 1e9))
            return self.send_json(200, {'ok': True, 'name': it.get('Name'), 'gb': round(freed / 1e9, 1)})

        def api_criterion(self, me, user, q, b):
            """John's only: Criterion's covers (criterion-covers.py). POST {films: [...]}: the films the admin page read from Criterion's list
            page saved in John's browser, kept, and the covers fetched and put on their films, in the background. GET: how it's going."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            home = os.path.expanduser('~/Grifflix')
            script, out = os.path.join(home, 'criterion-covers.py'), os.path.join(home, 'criterion-covers.log')
            if not b and q.get('want') == '1':   # (the Criterion pages John's browser is to read for their synopses: criterion.com lets it, not the Mac)
                # (straight from The Criterion Collection, each film found on Criterion's list for its page: John, 16 Nov 2026, "why make it so
                # complicated? Just grab the criterion collection from the collection")
                # (then every other film on Criterion's list too, so a Criterion film that comes in later has its synopsis waiting, given
                # by itself: John, 16 Nov 2026, "we may as well get the bookmark to get all remaining criterion films into json")
                pairs, pages = crit_pairs(g), (load(os.path.join(home, 'criterion-synopses.json'), {}) or {}).get('pages', {})
                # (and each film's special features, read from the same page: a film read before they were wanted is read again: John,
                # 18 Nov 2026, "I need this for all criterion films too. I have extras for some")
                fchk = (load(os.path.join(home, 'criterion-features.json'), {}) or {}).get('checked') or {}
                need = lambda sl: not pages.get('https://www.criterion.com/films/' + sl) or sl not in fchk
                mine = sorted(set(sl for _, sl in pairs if need(sl)))
                rest = []
                for f in load(os.path.join(home, 'criterion-list.json'), []) or []:
                    sl = str(f.get('link') or '').replace('https://www.criterion.com/films/', '')
                    if sl and '/' not in sl and re.match(r'^[\w-]{3,160}$', sl) and need(sl) and sl not in mine and sl not in rest:
                        rest.append(sl)
                return self.send_json(200, {'want': mine + rest, 'mine': len(mine), 'have': len(pages), 'films': len(pairs)})
            if b and isinstance(b.get('features'), dict):   # (each film's special features, read with its synopsis)
                ff = os.path.join(home, 'criterion-features.json')
                F_ = load(ff, {}) or {}
                FF, FC = F_.setdefault('films', {}), F_.setdefault('checked', {})
                for k, v in b['features'].items():
                    k = str(k)
                    if re.match(r'^[\w-]{3,160}$', k) and isinstance(v, list):
                        L_ = [re.sub(r'\s+', ' ', str(x)).strip()[:400] for x in v if str(x).strip()][:80]
                        if L_:
                            FF[k] = L_
                        FC[k] = int(time.time())
                save(ff, F_)
                CFEAT.clear()
                log('Criterion special features: %d films read, %d with a list' % (len(b['features']), sum(1 for v in b['features'].values() if v)))
                if not isinstance(b.get('synopses'), dict):
                    return self.send_json(200, syn_state())
            if b and isinstance(b.get('synopses'), dict):   # (what John's browser read: kept at once, then each film given its synopsis in the background)
                sf = os.path.join(home, 'criterion-synopses.json')
                S_ = load(sf, {}) or {}
                pages = S_.setdefault('pages', {})
                n = 0
                for k, v in b['synopses'].items():
                    k, v = str(k), str(v or '').strip()[:6000]
                    if re.match(r'^[\w-]{3,160}$', k) and len(v) > 40:
                        pages['https://www.criterion.com/films/' + k] = v
                        n += 1
                save(sf, S_)
                log('Criterion synopses: %d from John’s browser, kept; giving them to their films' % n)
                SYN['got'] = n
                if not (SYN.get('t') and SYN['t'].is_alive()):
                    SYN['t'] = threading.Thread(target=give_synopses, args=(g,), daemon=True)
                    SYN['t'].start()
                return self.send_json(200, syn_state())
            if b and isinstance(b.get('tags'), dict) and isinstance(b['tags'].get('tags'), dict):   # (Criterion's genres and categories, read by John's browser)
                T = {str(k)[:60]: [str(x)[:160] for x in (v or []) if re.match(r'^[\w-]{3,160}$', str(x))][:3000] for k, v in b['tags']['tags'].items() if isinstance(v, list)}
                SE = {str(k)[:160]: [str(x)[:160] for x in (v or []) if re.match(r'^[\w-]{3,160}$', str(x))][:200] for k, v in (b['tags'].get('sets') or {}).items() if isinstance(v, list)}   # (each box set's films: John, 17 Nov 2026)
                SSP = {str(k)[:160]: str(v)[:6] for k, v in (b['tags'].get('setspine') or {}).items() if str(v).isdigit()}   # (each box set's own spine number)
                SIM = {str(k)[:160]: str(v)[:400] for k, v in (b['tags'].get('setimg') or {}).items() if re.match(r'^https://[\w.-]+/', str(v))}   # (each box set's own cover)
                SDE = {str(k)[:160]: str(v)[:2000] for k, v in (b['tags'].get('setdesc') or {}).items() if len(str(v or '')) > 40}   # (each box set's description: John, 18 Nov 2026)
                SFE = {str(k)[:160]: [re.sub(r'\s+', ' ', str(x)).strip()[:400] for x in v if str(x).strip()][:80] for k, v in (b['tags'].get('setfeat') or {}).items() if isinstance(v, list) and v}   # (and its special features)
                save(os.path.join(home, 'criterion-tags.json'), {'tags': T, 'sets': SE, 'setspine': SSP, 'setimg': SIM, 'setdesc': SDE, 'setfeat': SFE, 'missing': [str(x)[:120] for x in (b['tags'].get('missing') or [])][:40],
                                                                 'debug': {str(k)[:60]: str(v)[:300] for k, v in (b['tags'].get('debug') or {}).items()}, 'at': int(time.time())})
                n = sum(len(v) for v in T.values())
                log('Criterion genres: %d kinds, %d films in them, %d box sets, from John’s browser' % (len(T), n, len(SE)))
                threading.Thread(target=give_genres, args=(g,), daemon=True).start()
                return self.send_json(200, {'tagsLog': '%d of Criterion’s genres and categories kept (%d films in them)%s. The site has them in a minute or two.' % (
                    len(T), n, '; not found: ' + ', '.join(b['tags'].get('missing') or []) if b['tags'].get('missing') else '')})
            if not b and q.get('syn') == '1':   # (how the synopses are going, for the admin card)
                return self.send_json(200, syn_state())
            if b:
                films = b.get('films')
                if not isinstance(films, list) or not films:
                    return self.send_json(400, {'error': 'No films in that page: was it Criterion’s list page?'})
                if not os.path.exists(script):
                    return self.send_json(503, {'error': 'Press Update everything first (the Mac mini needs criterion-covers.py).'})
                if CRIT.get('p') and CRIT['p'].poll() is None:
                    return self.send_json(409, {'error': 'It’s already fetching covers.'})
                keep = [{k: str(f.get(k) or '')[:400] for k in ('link', 'spine', 'img', 'title', 'director', 'country', 'year')} for f in films[:5000] if isinstance(f, dict)]
                save(os.path.join(home, 'criterion-list.json'), keep)
                import subprocess
                CRIT['p'] = subprocess.Popen([sys.executable, '-u', script, 'go'],   # (-u: its report as it goes, for the admin card)
                                             stdout=open(out, 'w'), stderr=subprocess.STDOUT, cwd=home)
                CRIT['at'] = time.time()
                log('Criterion covers: %d films from John’s list, fetching' % len(keep))
            tail = ''
            try:
                tail = open(out, encoding='utf-8', errors='replace').read()[-3000:]
            except OSError:
                pass
            return self.send_json(200, {'running': bool(CRIT.get('p') and CRIT['p'].poll() is None), 'at': CRIT.get('at'), 'log': tail,
                                        'ready': os.path.exists(script)})

        def api_duplicates(self, me, user, q, b):
            """John's only: films he has more than one copy of, for the admin page's Duplicates tab (John, 16 Nov 2026: "that should be part
            of the admin page - make it a tab if there are duplicates and let me be able to select and fix there"). GET: what duplicates.py
            finds (kept for 10 minutes; ?fresh=1 looks again). POST {keep: [ids]}: in each chosen film, the copy chosen kept and the others
            set aside, in the background; GET then says how it went."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            import subprocess
            home = os.path.expanduser('~/Grifflix')
            script = os.path.join(home, 'duplicates.py')
            if not os.path.exists(script):
                return self.send_json(503, {'error': 'Press Update everything first (the Mac mini needs duplicates.py).'})
            if b:
                keep = [i for i in (b.get('keep') or []) if re.match(r'^[0-9a-f]{32}$', str(i))]
                if not keep:
                    return self.send_json(400, {'error': 'Choose the copy to keep.'})
                if DUPS.get('p') and DUPS['p'].poll() is None:
                    return self.send_json(409, {'error': 'It’s already setting some aside.'})
                DUPS['p'] = subprocess.Popen([sys.executable, '-u', script, 'go', 'keep=' + ','.join(keep)], stdout=open(os.path.join(home, 'duplicates.log'), 'w'),
                                             stderr=subprocess.STDOUT, cwd=home)
                DUPS['at'], DUPS['found'] = time.time(), None
                log('Duplicates: John chose %d to keep' % len(keep))
            running = bool(DUPS.get('p') and DUPS['p'].poll() is None)
            found = DUPS.get('found')
            if not running and (not found or q.get('fresh') == '1' or time.time() - found[0] > 600):
                r_ = subprocess.run([sys.executable, script, 'json'], capture_output=True, text=True, timeout=900, cwd=home)
                try:
                    found = (time.time(), json.loads([l for l in (r_.stdout or '').splitlines() if l.startswith('{')][-1]))
                    DUPS['found'] = found
                except (IndexError, ValueError):
                    return self.send_json(500, {'error': 'The Mac mini couldn’t look: ' + (r_.stderr or '').strip()[-200:]})
            last = ''
            try:
                last = open(os.path.join(home, 'duplicates.log'), encoding='utf-8', errors='replace').read()[-2000:]
            except OSError:
                pass
            return self.send_json(200, dict((found or (0, {}))[1], running=running, last=last, at=DUPS.get('at')))

        def api_lookkey(self, me, user, q, b):
            """John's only: a key for Claude to look at the Mac mini with (its logs, its folders, what's running), reading only, never
            changing anything (John, 16 Nov 2026: "can't you use it from here somehow?"). POST {make: true}: a new key, shown once (the Mac
            keeps only its fingerprint); POST {off: true}: no key. GET: whether there is one, and when it was last used."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            if b:
                if b.get('off'):
                    g.st.pop('look_key', None)
                    save(SETTINGS, g.st)
                    return self.send_json(200, {'on': False})
                if b.get('make'):
                    k = 'gl_' + secrets.token_urlsafe(32)
                    g.st['look_key'] = hashlib.sha256(k.encode()).hexdigest()
                    g.st['look_made'] = time.time()
                    save(SETTINGS, g.st)
                    log('A new look key made for Claude')
                    return self.send_json(200, {'on': True, 'key': k})
            return self.send_json(200, {'on': bool(g.st.get('look_key')), 'made': g.st.get('look_made'), 'used': LOOKED.get('at')})

        def look_roots(self):
            """Where looking is allowed: ~/Grifflix and the media libraries' folders and the folder they're in (the G-Drive's Grifflix, with
            "Criterion images" and the rest); never the settings folder (its keys)."""
            roots = [os.path.realpath(os.path.expanduser('~/Grifflix'))]
            BROAD = set(os.path.realpath(x) for x in ('/', '/Volumes', '/Users', os.path.expanduser('~'), '/Volumes/G-Drive'))   # (never a whole disk or home)
            try:
                for v in g.jf.call('GET', '/Library/VirtualFolders') or []:
                    for l in v.get('Locations') or []:
                        for x in (l, os.path.dirname(l.rstrip('/'))):
                            x = os.path.realpath(x)
                            if x not in roots and x not in BROAD:
                                roots.append(x)
            except Exception:
                pass
            return roots

        def look(self, what, q):
            LOOKED['at'] = time.time()
            home = os.path.expanduser('~/Grifflix')
            LOGS = {'duplicates': os.path.join(home, 'duplicates.log'), 'criterion': os.path.join(home, 'criterion-covers.log'), 'criterion-auto': os.path.join(home, 'criterion-auto.log'), 'criterion-sheet': os.path.join(home, 'criterion-sheet.log'), 'gateway': os.path.join(APP_DIR, 'gateway.log'), 'tunnel': os.path.join(APP_DIR, 'tunnel.log'), 'setup': os.path.join(APP_DIR, 'setup.log'),
                    'convert': os.path.join(home, 'convert.log'), 'convert-out': os.path.join(home, 'convert.out'), 'gdrive': os.path.join(home, 'gdrive.log'), 'backup': os.path.join(home, 'backup.out')}
            SECRETISH = re.compile(r'(rclone\.conf|token|secret|password|\.pickle$|client_secret|\.pem$|\.key$|settings\.json$)', re.I)
            def inside(p):
                rp = os.path.realpath(p)
                return any(rp == r_ or rp.startswith(r_.rstrip('/') + '/') for r_ in self.look_roots()) and not rp.startswith(os.path.realpath(APP_DIR))
            if what in ('', 'help'):
                return self.send_json(200, {'version': VERSION, 'logs': {k: (os.path.getsize(v) if os.path.exists(v) else None) for k, v in LOGS.items()}, 'roots': self.look_roots(),
                                            'use': ['/look/log?name=gateway&n=200&find=text', '/look/ls?path=/Volumes/...', '/look/read?path=~/Grifflix/x.json', '/look/ps', '/look/sys?only=hardware,usb', '/look/jf?path=/Items/<id>']})
            if what == 'log':
                f = LOGS.get(q.get('name') or '')
                if not f or not os.path.exists(f):
                    return self.send_json(404, {'error': 'No such log.', 'logs': sorted(LOGS)})
                n = max(1, min(5000, int(q.get('n') or 200)))
                find = (q.get('find') or '').lower()
                with open(f, 'rb') as fh:
                    fh.seek(0, 2); size = fh.tell(); fh.seek(max(0, size - (40 if find else 4) * 1024 * 1024))
                    lines = fh.read().decode('utf-8', 'replace').splitlines()
                if find:
                    lines = [l for l in lines if find in l.lower()]
                return self.send_json(200, {'name': q.get('name'), 'size': size, 'lines': lines[-n:]})
            if what == 'ls':
                p = os.path.expanduser(q.get('path') or '~/Grifflix')
                if not inside(p) or not os.path.isdir(p):
                    return self.send_json(403, {'error': 'Not a folder that can be looked in.', 'roots': self.look_roots()})
                out = []
                for n in sorted(os.listdir(p))[:3000]:
                    fp = os.path.join(p, n)
                    try:
                        st_ = os.stat(fp)
                        out.append({'name': n, 'dir': os.path.isdir(fp), 'size': st_.st_size, 'mtime': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st_.st_mtime))})
                    except OSError:
                        out.append({'name': n, 'error': 'unreadable'})
                return self.send_json(200, {'path': os.path.realpath(p), 'items': out})
            if what == 'read':
                p = os.path.expanduser(q.get('path') or '')
                if not inside(p) or not os.path.isfile(p) or SECRETISH.search(p):
                    return self.send_json(403, {'error': 'Not a file that can be read.'})
                if not p.lower().endswith(('.json', '.csv', '.txt', '.log', '.out', '.py', '.sh', '.srt', '.nfo', '.xml', '.plist')):
                    return self.send_json(403, {'error': 'Only text files.'})
                with open(p, 'rb') as fh:
                    data = fh.read(2 * 1024 * 1024)
                return self.send_json(200, {'path': os.path.realpath(p), 'size': os.path.getsize(p), 'text': data.decode('utf-8', 'replace')})
            if what == 'ps':
                import subprocess
                ps = subprocess.run(['ps', '-axo', 'pid,ppid,stat,%cpu,%mem,etime,command'], capture_output=True, text=True, timeout=20).stdout.splitlines()
                keep = [ps[0]] + [l for l in ps[1:] if re.search(r'python|ffmpeg|ffprobe|rclone|jellyfin|cloudflared|caffeinate', l, re.I) and 'ps -axo' not in l]
                keep = [re.sub(r'(--?(?:password|pass|token|key|secret)[= ])\S+', r'\1***', l, flags=re.I) for l in keep]
                df = subprocess.run(['df', '-h'], capture_output=True, text=True, timeout=20).stdout.splitlines()
                up = subprocess.run(['uptime'], capture_output=True, text=True, timeout=20).stdout.strip()
                return self.send_json(200, {'ps': keep, 'df': df, 'uptime': up, 'version': VERSION, 'sha': (load(INSTALLED, {}).get('sha') or '')[:7]})
            if what == 'sys':   # (the Mac mini itself, for Claude's look at the whole set-up: John, 18 Nov 2026, "can you look and do a complete
                # assessment of the set up yourself?"). A fixed list of macOS's own reports, read only; serial numbers and IDs blanked
                import subprocess
                cmds = {'macos': ['sw_vers'], 'hardware': ['system_profiler', 'SPHardwareDataType'], 'usb': ['system_profiler', 'SPUSBDataType'],
                        'thunderbolt': ['system_profiler', 'SPThunderboltDataType'], 'storage': ['system_profiler', 'SPStorageDataType'],
                        'sata': ['system_profiler', 'SPSerialATADataType'], 'disks': ['diskutil', 'list'], 'gdrive': ['diskutil', 'info', '/Volumes/G-Drive'],
                        'power': ['pmset', '-g'], 'battery_ups': ['pmset', '-g', 'ps'], 'memory': ['vm_stat'], 'swap': ['sysctl', 'vm.swapusage'],
                        'load': ['sysctl', 'vm.loadavg', 'hw.ncpu', 'hw.memsize', 'hw.model', 'machdep.cpu.brand_string'],
                        'network': ['system_profiler', 'SPNetworkDataType'], 'ethernet': ['ifconfig', 'en0'], 'wifi': ['ifconfig', 'en1'],
                        'io': ['iostat', '-d', '-c', '3', '-w', '2'], 'top': ['top', '-l', '1', '-n', '8', '-o', 'cpu', '-stats', 'pid,command,cpu,mem,state'],
                        'uptime': ['uptime'], 'software_update': ['softwareupdate', '--history'], 'timemachine': ['tmutil', 'destinationinfo']}
                want = [k for k in (q.get('only') or '').split(',') if k in cmds] or list(cmds)
                out = {}
                for k in want:
                    try:
                        r = subprocess.run(cmds[k], capture_output=True, text=True, timeout=90)
                        t = (r.stdout or '') + (('\n' + r.stderr) if r.stderr and not r.stdout else '')
                    except Exception as e:
                        t = 'couldn’t: %s' % str(e)[:100]
                    t = re.sub(r'(?im)^(\s*(?:serial number[^:]*|hardware uuid|provisioning udid|uuid|volume uuid|disk / partition uuid|ether|inet6?|router|ipv4 addresses|ipv6 addresses|subnet masks|mac address|bssid|ssid)\s*[: ]).*$', r'\1 ***', t)
                    t = re.sub(r'\b([0-9a-f]{2}:){5}[0-9a-f]{2}\b', '**:**', t, flags=re.I)
                    out[k] = t[-20000:]
                return self.send_json(200, out)
            if what == 'jf':   # (the media server's own answers, asked only; e.g. /Items/<id>, /Library/VirtualFolders)
                path = q.get('path') or ''
                if not re.match(r'^/(Items|Users/[0-9a-f]{32}/Items|Library/VirtualFolders|System/Info|Sessions|ScheduledTasks|Shows|Search/Hints)', path):
                    return self.send_json(403, {'error': 'Not a media server address that can be looked at.'})
                qq = {k[2:]: v for k, v in q.items() if k.startswith('q.')}
                return self.send_json(200, {'answer': g.jf.call('GET', path, None, qq or None)})
            return self.send_json(404, {'error': 'Nothing called that.'})

        def api_autoupdate(self, me, user, q, b):
            """The site updated by the Mac itself when new films arrive (John, 15 Nov 2026: "it needs to be automatically updated"): the
            admin page hands the Mac a sign-in of its own (one of John's, for the Mac alone), kept here; GET: whether it's on."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            if b:
                tok = str(b.get('token') or '').strip()
                if b.get('off'):
                    g.st.pop('worker_token', None)
                elif re.match(r'^[A-Za-z0-9_-]{20,200}$', tok):
                    g.st['worker_token'] = tok
                else:
                    return self.send_json(400, {'error': 'That sign-in isn’t right.'})
                save(SETTINGS, g.st)
                if g.st.get('worker_token'):   # (turned on: the site brought up to date now, with what came before it was on)
                    g.publish_soon = time.time() + 15
                    SCAN_NOW.set()
            return self.send_json(200, {'on': bool(g.st.get('worker_token')), 'last': g.st.get('auto_update_last')})

        def api_tmdbkey(self, me, user, q, b):
            """John's only: his TMDB key (an API key or read access token), kept on the Mac, so films are matched by TMDB itself, their
            lengths included. POST {key}: checked with TMDB, then kept; {off: true}: none. GET: whether there is one."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            if b:
                if b.get('off'):
                    g.st.pop('tmdb_key', None)
                else:
                    k = str(b.get('key') or '').strip()
                    if not re.match(r'^[A-Za-z0-9._-]{20,400}$', k):
                        return self.send_json(400, {'error': 'That isn’t a TMDB key.'})
                    was = g.st.get('tmdb_key')
                    g.st['tmdb_key'] = k
                    try:
                        if not tmdb(g, '/configuration'):
                            raise ValueError('no answer')
                    except Exception as e:
                        if was:
                            g.st['tmdb_key'] = was
                        else:
                            g.st.pop('tmdb_key', None)
                        return self.send_json(400, {'error': 'TMDB didn’t accept that key (%s).' % str(e)[:80]})
                    save(os.path.expanduser('~/Grifflix/match-tried.json'), {})   # (every film looked at again, with TMDB's help)
                save(SETTINGS, g.st)
                if g.st.get('tmdb_key'):   # (and now, not in six hours)
                    threading.Thread(target=auto_match, args=(g,), daemon=True).start()
            return self.send_json(200, {'on': bool(g.st.get('tmdb_key'))})

        def api_subsetup(self, me, user, q, b):
            """John's OpenSubtitles details (its API key, his account), kept here on the Mac only."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John sets this up.'})
            if not self.subs_ready():
                return self.send_json(503, {'error': 'Update the Mac first (Update everything).'})
            o = g.st.setdefault('opensubs', {})
            if b:
                for k in ('key', 'user', 'pass'):
                    if str(b.get(k) or '').strip():
                        o[k] = str(b[k]).strip()
                o.pop('token', None)
                try:
                    self.subs_client().token()
                except Exception as e:
                    save(SETTINGS, g.st)
                    return self.send_json(400, {'error': str(e)})
            return self.send_json(200, {'ready': bool(o.get('token') or (o.get('key') and o.get('user') and o.get('pass'))), 'user': o.get('user') or '',
                                        'keyset': bool(o.get('key')), 'remaining': o.get('remaining'), 'allowed': o.get('allowed')})

        def api_subs(self, me, user, q, b):
            """Subtitles: find them for a film (GET id, lang), add one (POST op=add), move one earlier or later (POST op=shift),
            and how an add is going (GET job). Any member (John, 13 Nov 2026)."""
            if not self.subs_ready():
                return self.send_json(503, {'error': 'The Mac needs updating for subtitles (John: Update everything).'})
            if q.get('job'):
                j = SUBJOBS.get(q['job'])
                if not j or j['uid'] != me['uid']:
                    return self.send_json(404, {'error': 'Not here.'})
                return self.send_json(200, {k: v for k, v in j.items() if k != 'uid' and not k.startswith('_')})
            if not b and q.get('langs'):
                return self.send_json(200, {'langs': [{'code': c, 'name': n} for c, n in SUBS.LANGS]})
            iid = g.now_id(str((b or q).get('id') or ''))
            it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path,ProviderIds,MediaSources'})
            path = it.get('Path') or ''
            if not b:   # find
                lang = re.sub(r'[^A-Za-z-]', '', q.get('lang') or 'en')[:6] or 'en'
                pid = it.get('ProviderIds') or {}
                ser = {}
                if it.get('Type') == 'Episode' and it.get('SeriesId'):
                    try:
                        ser = (g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, it['SeriesId']), query={'Fields': 'ProviderIds'}) or {}).get('ProviderIds') or {}
                    except Exception:
                        ser = {}
                try:
                    mh = SUBS.movie_hash(path) if path and os.path.exists(path) else None
                except OSError:
                    mh = None
                try:
                    if it.get('Type') == 'Episode':
                        res = self.subs_client().search(lang, mhash=mh, parent_tmdb=ser.get('Tmdb'), season=it.get('ParentIndexNumber'), episode=it.get('IndexNumber'),
                                                        query=it.get('SeriesName'), hint=os.path.basename(path))
                    else:
                        res = self.subs_client().search(lang, mhash=mh, tmdb=pid.get('Tmdb'), imdb=pid.get('Imdb'), query=it.get('Name'), year=it.get('ProductionYear'),
                                                        hint=os.path.basename(path))
                except Exception as e:
                    return self.send_json(502, {'error': str(e)})
                o = g.st.get('opensubs') or {}
                self.subs_listen(path)   # (the film heard now, while the list is looked at: a choice is then quick)
                return self.send_json(200, {'results': res, 'remaining': o.get('remaining')})
            op = b.get('op')
            key = make_key(g.st['secret'], me['uid'])
            ms = (it.get('MediaSources') or [{}])[0]
            def file_of(sid):   # (a subtitle file beside the film: its path, or '')
                st = next((x for x in ms.get('MediaStreams', []) if x.get('Index') == int(sid if sid is not None else -1) and x.get('Type') == 'Subtitle'), None)
                sp = (st or {}).get('Path') or ''
                ok = st and st.get('IsExternal') and sp.lower().endswith('.srt') and os.path.dirname(sp) == os.path.dirname(path) and os.path.exists(sp)
                return sp if ok else ''
            if op == 'shift':   # (a subtitle file beside the film, every line earlier or later, for everyone)
                sp = file_of(b.get('sid'))
                if not sp:
                    return self.send_json(400, {'error': 'Only subtitles added as files can be moved for everyone.'})
                secs = max(-30.0, min(30.0, float(b.get('secs') or 0)))
                if abs(secs) < 0.05:
                    return self.send_json(200, {'ok': True})
                n = SUBS.shift_file(sp, secs)
                log('Subtitles: %s moved %+.1f s by %s (%s)' % (os.path.basename(sp), secs, me['name'], it.get('Name')))
                return self.send_json(200, {'ok': True, 'lines': n, 'm': int(os.path.getmtime(sp))})
            if op == 'sync':   # (lined up by hand: lines tapped as they were said; one fixes a shift, two far apart a drift too)
                sp = file_of(b.get('sid'))
                if not sp:
                    return self.send_json(400, {'error': 'Only subtitles added as files can be lined up for everyone.'})
                pairs = [(int(x[0]), float(x[1])) for x in (b.get('pairs') or [])[:8] if isinstance(x, (list, tuple)) and len(x) == 2]
                try:
                    r = SUBS.sync_file(sp, pairs)
                except Exception as e:
                    return self.send_json(400, {'error': str(e)})
                log('Subtitles: %s lined up by hand by %s from %d line(s): %s' % (os.path.basename(sp), me['name'], len(pairs), r))
                return self.send_json(200, dict(r, ok=True, subs=self.subs_out(iid, ms, key)))
            if op == 'delete':   # (subtitles added here, taken off: wrong, or to try others; John only: John, 13 Nov 2026)
                if not me.get('owner'):
                    return self.send_json(403, {'error': 'Only John takes subtitles off.'})
                sp = file_of(b.get('sid'))
                if not sp or '.Grifflix.' not in os.path.basename(sp):
                    return self.send_json(400, {'error': 'Only subtitles added in Grifflix can be taken off here.'})
                os.remove(sp)
                log('Subtitles: %s taken off by %s' % (os.path.basename(sp), me['name']))
                subs, _ = self.subs_show(iid, None, key)
                return self.send_json(200, {'ok': True, 'subs': subs})
            if op == 'keep':   # (subtitles it couldn't time: added as they came, to be lined up by hand)
                j = SUBJOBS.get(str(b.get('job') or ''))
                if not j or j['uid'] != me['uid'] or not j.get('_cues'):
                    return self.send_json(404, {'error': 'Those have gone: choose them again.'})
                out = self.subs_write(path, j['_lang'], j.pop('_cues'))
                subs, pick = self.subs_show(iid, out, key)
                j.update(state='done', say='Added as they came: now line them up by hand (subtitles menu, Line up by a line)', subs=subs, pick=pick, hand=True)
                return self.send_json(200, {'ok': True, 'subs': subs, 'pick': pick})
            if op not in ('add', 'auto'):
                return self.send_json(400, {'error': 'Which?'})
            # (one chosen, or "Choose for me": the likeliest few tried in turn, the first that surely fits this copy kept: John,
            # 13 Nov 2026, "It's just guess work for me what the right cut is")
            tries = [{'file_id': int(x.get('file_id') or 0), 'exact': bool(x.get('exact')), 'name': str(x.get('name') or '')[:120]}
                     for x in ((b.get('tries') or [])[:4] if op == 'auto' else [b])]
            tries = [x for x in tries if x['file_id']]
            lang = re.sub(r'[^a-z]', '', (b.get('lang') or 'en').lower().split('-')[0])[:3] or 'en'
            if not tries or not path or not os.path.exists(path):
                return self.send_json(400, {'error': 'That film’s file isn’t there.' if tries else 'Which subtitles?'})
            day = time.strftime('%Y-%m-%d')
            n = SUBDAY.get((me['uid'], day), 0)
            if n >= 15 and not me.get('owner'):   # (OpenSubtitles allows John's account only so many a day, shared by everyone)
                return self.send_json(429, {'error': 'That’s 15 subtitles today: more tomorrow.'})
            jid = secrets.token_hex(6)
            SUBJOBS[jid] = {'uid': me['uid'], 'state': 'waiting', 'say': 'Waiting its turn…', 'at': time.time()}
            refs = [x for x in ms.get('MediaStreams', []) if x.get('Type') == 'Subtitle' and x.get('IsTextSubtitleStream')
                    and '.Grifflix.' not in os.path.basename(x.get('Path') or '')]   # (the film's own subtitles: the surest guide to timing)
            def run(job=SUBJOBS[jid]):
                with SUBRUN:
                    try:
                        ff = self.ffmpeg_path()
                        g.watched()   # (the converter waits meanwhile: the film's disk is this job's)
                        own = None    # (the film's own subtitles, read once)
                        heard = None  # (where people speak, heard once for every try)
                        guide = None  # (subtitles made for this copy in any language, fetched once)
                        best = None   # (the nearest miss, to add anyway and line up by hand)
                        shown = {}    # (the first, put on at once as it came)
                        hms = lambda x: '%d:%02d:%02d' % (x // 3600, x // 60 % 60, x % 60)
                        film_len = (it.get('RunTimeTicks') or 0) / 1e7
                        lengths = []   # (how long each tried runs, first line to last, against the film: John, 13 Nov 2026)
                        done, rep, used = None, {}, None
                        for k, cand in enumerate(tries):
                            if SUBDAY.get((me['uid'], day), 0) >= 15 and not me.get('owner'):
                                break
                            SUBDAY[(me['uid'], day)] = SUBDAY.get((me['uid'], day), 0) + 1
                            pre = 'Trying %d of %d%s: ' % (k + 1, len(tries), ' (%s)' % cand['name'] if cand['name'] else '') if len(tries) > 1 else ''
                            job.update(state='getting', say=pre + 'getting them…')
                            try:
                                cues = SUBS.parse_srt(SUBS.decode(self.subs_client().download(cand['file_id'])))
                            except Exception as e:
                                rep = {'ok': False, 'why': str(e)[:120]}
                                if 'download' in str(e).lower() or 'allowed' in str(e).lower():
                                    break
                                continue
                            if len(cues) < 3:
                                continue
                            if k == 0 and not shown:   # (on at once, as they came, while they're timed: John, 13 Nov 2026, "it takes much too long")
                                try:
                                    tmp_out = self.subs_write(path, lang, cues)
                                    s0, p0 = self.subs_show(iid, tmp_out, key)
                                    shown.update(path=tmp_out, subs=s0, pick=p0)
                                    job.update(state='shown', subs=s0, pick=p0, say='On now, as they came; being timed to the film…')
                                except Exception as e:
                                    log('Subtitles: couldn’t show them at once:', repr(e))
                            span = cues[-1][1]
                            lengths.append('%s: last line at %s' % (cand['name'] or 'number %d' % (k + 1), hms(span)))
                            pre = pre + ('its last line at %s%s; ' % (hms(span), ', the film runs %s' % hms(film_len) if film_len else '') if len(tries) > 1 else '')
                            rep = {'ok': False, 'why': 'couldn’t be sure of the timing'}
                            if own is None:
                                own = []
                                for ref in refs[:2]:
                                    try:
                                        rc = self.subs_ref(ff, path, ref)
                                        if len(rc) >= 20:
                                            own.append(rc)
                                    except Exception as e:
                                        log('Subtitles: couldn’t read the film’s own subtitles:', repr(e))
                            for rc in own:   # first against the film's own subtitles
                                job.update(state='timing', say=pre + 'timing them against the film’s own subtitles…')
                                c2, r2 = SUBS.align(cues, SUBS.cue_signal(rc, int(max(rc[-1][1], cues[-1][1] * 1.1) * SUBS.HZ) + 600))
                                if r2.get('ok'):
                                    done, rep = c2, dict(r2, by='the film’s own subtitles')
                                    break
                            if done is None:   # then by listening
                                try:
                                    if heard is None:
                                        heard = self.subs_heard(path)   # (heard before, or while the list was looked at)
                                    if heard is None:
                                        job.update(state='listening', say=pre + 'listening to the film’s dialogue (a few minutes on the Mac mini, once for this film)…')
                                        heard = self.subs_listen(path, wait=True)
                                        if heard is None:
                                            raise RuntimeError('no sound heard')
                                    job.update(state='timing', say=pre + 'timing them to the film…')
                                    c2, r2 = SUBS.align(cues, heard)
                                    rep = r2
                                    if r2.get('ok'):
                                        done = c2
                                except Exception as e:
                                    heard = heard or []
                                    rep = {'ok': False, 'why': 'couldn’t hear the film (%s)' % str(e)[:80]}
                            if done is None and cand['exact']:   # (made for this very file: their timing is already its own)
                                done, rep = cues, {'ok': True, 'exact': True}
                            if done is None:   # then against subtitles in any language made for this copy (one download, once)
                                try:
                                    if guide is None:
                                        guide = []
                                        for rfid, rlang in [x for x in self.subs_client().exact_any(SUBS.movie_hash(path)) if x[0] not in [t['file_id'] for t in tries]][:1]:
                                            rc = SUBS.parse_srt(SUBS.decode(self.subs_client().download(rfid)))
                                            if len(rc) >= 20:
                                                guide = [(rc, rlang)]
                                    for rc, rlang in guide:
                                        job.update(state='timing', say=pre + 'timing them against subtitles made for this copy…')
                                        c2, r2 = SUBS.align(cues, SUBS.cue_signal(rc, int(max(rc[-1][1], cues[-1][1] * 1.1) * SUBS.HZ) + 600))
                                        if r2.get('ok'):
                                            done, rep = c2, dict(r2, by='subtitles made for this copy (%s)' % rlang)
                                except Exception as e:
                                    guide = guide or []
                                    log('Subtitles: couldn’t use a guide made for this copy:', repr(e))
                            if done is not None:
                                used = (k, cand)
                                break
                            if best is None or rep.get('sure', 0) > best[0]:
                                best = (rep.get('sure', 0), cues, rep)
                            log('Subtitles: try %d for %s not sure: %s' % (k + 1, it.get('Name'), rep))
                        if done is None:   # (none sure: nothing saved; others to choose, or the nearest added as it came to line up by hand)
                            if shown.get('path') and os.path.exists(shown['path']):   # (the ones put on at once come off again: not confirmed)
                                try:
                                    os.remove(shown['path'])
                                    gone_subs, _ = self.subs_show(iid, None, key)
                                    job.update(subs=gone_subs, pick=None)
                                except Exception as e:
                                    log('Subtitles: couldn’t take them off again:', repr(e))
                            many = len(tries) > 1
                            job.update(state='unsure', _cues=best[1] if best else None, _lang=lang, timing=best[2] if best else rep,
                                       say=('Tried %d, and none could be confirmed to fit this copy of the film, so none has been added. ' % len(tries) if many else
                                            'Couldn’t be sure how these line up with this copy of the film, so they haven’t been added. ') +
                                           ('They may all be for a different version (a film like this one often has several).' if many else 'They may be for a different version.') +
                                           (' ' + '; '.join(lengths) + ('; the film runs %s.' % hms(film_len) if film_len else '.') if lengths else ''))
                            if not best:
                                job['_cues'] = None
                            log('Subtitles: none added for %s (%d tried)' % (it.get('Name'), len(tries)))
                            return
                        job.update(state='adding', say='Adding them to the film…')
                        out = self.subs_write(path, lang, done)
                        if shown.get('path') == out:   # (already on: the timed ones in their place, the media server already knows the file)
                            it2 = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path,MediaSources'})
                            subs = self.subs_out(iid, (it2.get('MediaSources') or [{}])[0], key)
                            pick = next((i for i, x in enumerate(subs) if x['sid'] == (shown.get('subs') or [{}])[shown['pick']].get('sid')), shown.get('pick')) if shown.get('pick') is not None else None
                        else:
                            subs, pick = self.subs_show(iid, out, key)
                        log('Subtitles: %s added by %s (%s)' % (os.path.basename(out), me['name'], SUBS.describe(rep)))
                        say = 'Made for this very copy of the film, so kept as they came' if rep.get('exact') else SUBS.describe(rep) + (' (against %s)' % rep['by'] if rep.get('by') else '')
                        if len(tries) > 1:
                            say = '%s fitted%s. %s' % (used[1]['name'] or 'Number %d' % (used[0] + 1), ' (the %s tried)' % ['first', 'second', 'third', 'fourth'][used[0]] if used[0] else '', say)
                        job.update(state='done', say=say + ('' if subs else ' (the media server is still adding them: start the film again in a minute)'),
                                   subs=subs, pick=pick, timing=rep)
                    except Exception as e:
                        log('Subtitles: couldn’t add:', repr(e))
                        job.update(state='error', say=str(e)[:200])
            threading.Thread(target=run, daemon=True).start()
            for k in [k for k, x in SUBJOBS.items() if time.time() - x['at'] > 6 * 3600]:
                del SUBJOBS[k]
            return self.send_json(200, {'job': jid})

        def subs_listen(self, path, wait=False):
            """What's heard of a film, from before or heard now (once, in the background); with wait, it waits for it."""
            h = self.subs_heard(path)
            if h is not None:
                return h
            ev = HEARING.get(path)
            if ev is None:
                ev = HEARING[path] = threading.Event()
                def run():
                    try:
                        g.watched()
                        self.subs_heard(path, SUBS.speech(self.ffmpeg_path(), path))
                    except Exception as e:
                        log('Subtitles: couldn’t hear', os.path.basename(path), repr(e))
                    finally:
                        ev.set()
                        HEARING.pop(path, None)
                threading.Thread(target=run, daemon=True).start()
            if not wait:
                return None
            ev.wait(3600)
            return self.subs_heard(path)

        def subs_heard(self, path, sig=None):
            """What was heard of a film's dialogue, kept beside the gateway's own files (about 70 KB a film), so trying other
            subtitles for it later doesn't mean listening again; made afresh if the film's file changes."""
            try:
                st = os.stat(path)
                key = hashlib.sha1(('%s|%d|%d' % (path, st.st_size, int(st.st_mtime))).encode()).hexdigest()[:20]
                d = os.path.join(APP_DIR, 'heard')
                f = os.path.join(d, key + '.bin')
                if sig is None:
                    if not os.path.exists(f):
                        return None
                    with open(f, 'rb') as fh:
                        return [x / 255.0 for x in fh.read()]
                os.makedirs(d, exist_ok=True)
                with open(f + '.tmp', 'wb') as fh:
                    fh.write(bytes(max(0, min(255, int(round(v * 255)))) for v in sig))
                os.replace(f + '.tmp', f)
            except Exception as e:
                log('Subtitles: couldn’t keep what was heard:', repr(e))
            return None

        def ffmpeg_path(self):
            ff = 'ffmpeg'
            try:
                enc = g.jf.call('GET', '/System/Configuration/encoding') or {}
                ff = enc.get('EncoderAppPath') or enc.get('EncoderAppPathDisplay') or ff
            except Exception:
                pass
            if not os.path.exists(ff):
                ff = next((c for c in ('/Applications/Jellyfin.app/Contents/MacOS/ffmpeg', '/usr/local/bin/ffmpeg', '/opt/homebrew/bin/ffmpeg') if os.path.exists(c)), 'ffmpeg')
            return ff

        def subs_ref(self, ff, path, st):
            """One of the film's own text subtitles, as lines: from its file beside the film, or out of the film itself."""
            sp = st.get('Path') or ''
            if st.get('IsExternal') and sp and os.path.exists(sp):
                with open(sp, 'rb') as f:
                    raw = f.read()
                if sp.lower().endswith('.srt'):
                    return SUBS.parse_srt(SUBS.decode(raw))
                src = sp
                cmd = [ff, '-v', 'error', '-i', src, '-f', 'srt', '-']
            else:
                cmd = [ff, '-v', 'error', '-i', path, '-map', '0:%d' % st['Index'], '-f', 'srt', '-']
            import subprocess
            out = subprocess.run(cmd, capture_output=True, timeout=900).stdout
            return SUBS.parse_srt(SUBS.decode(out))

        def subs_write(self, path, lang, cues):
            out = '%s.Grifflix.%s.srt' % (os.path.splitext(path)[0], lang)   # (one of Grifflix's per language: a new one replaces it)
            with open(out + '.tmp', 'w', encoding='utf-8') as f:
                f.write(SUBS.write_srt(cues))
            os.replace(out + '.tmp', out)
            return out

        def subs_show(self, iid, out, key):
            """The media server told of a subtitle file added or taken off; the film's subtitles once it has seen it (and which is new)."""
            g.jf.call('POST', '/Items/%s/Refresh' % iid, query={'MetadataRefreshMode': 'FullRefresh', 'ImageRefreshMode': 'Default',
                                                                'ReplaceAllMetadata': 'false', 'ReplaceAllImages': 'false'})
            subs, pick, end = [], None, time.time() + 90
            while time.time() < end:
                time.sleep(3)
                it2 = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path,MediaSources'})
                ms2 = (it2.get('MediaSources') or [{}])[0]
                paths = [x.get('Path') or '' for x in ms2.get('MediaStreams', [])]
                if (out and out in paths) or (not out and not any(p_ and not os.path.exists(p_) for p_ in paths)):
                    subs = self.subs_out(iid, ms2, key)
                    new = next((x for x in ms2.get('MediaStreams', []) if out and (x.get('Path') or '') == out), None)
                    pick = next((i for i, x in enumerate(subs) if new and x['sid'] == new.get('Index')), None)
                    break
            g.forget()
            return subs, pick

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
            term = re.sub(r'[’‘`´]', "'", (q.get('q') or '')).strip()   # (a phone's curly apostrophe as the titles' straight one: "it’s" found It's a Wonderful Life: John, 16 Nov 2026)
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
            if rest.endswith('.m3u8') or '/stream' in rest:   # (what the device asked the media server for, for Playing now's details)
                with g.plays_lock:
                    p = g.plays.get(q.get('PlaySessionId') or '') or next((x for x in g.plays.values() if x['iid'] == rest[:32] and time.time() - x['seen'] < 90), None)
                    if p is not None:
                        p['asked'] = (rest.split('/', 1)[-1] + '?' + '&'.join('%s=%s' % (k, v) for k, v in sorted(q.items()) if k not in ('g', 'api_key', 'DeviceId', 'PlaySessionId', 'MediaSourceId')))[:400]
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


DHEALTH = {}


def disk_health(vol='/Volumes/G-Drive'):
    """How the G-Drive is (John, 18 Nov 2026: "How safe are my films on there?" ... "Add the health check"): from macOS (its format, what
    it says of the drive's health) and, if smartmontools is on the Mac, the drive's own record: hours run, sectors failing or moved, its
    temperature, its own verdict. Read only: nothing on the drive is changed."""
    import subprocess, plistlib, shutil
    out = {'at': int(time.time()), 'vol': vol}
    if not os.path.isdir(vol):
        out['state'] = 'missing'
        return out
    try:
        info = plistlib.loads(subprocess.run(['diskutil', 'info', '-plist', vol], capture_output=True, timeout=60).stdout)
        out.update(fs=info.get('FilesystemUserVisibleName') or info.get('FilesystemName'), proto=info.get('BusProtocol'),
                   smart_mac=info.get('SMARTStatus'), whole=info.get('ParentWholeDisk') or info.get('DeviceIdentifier'), media=info.get('MediaName') or info.get('IORegistryEntryName'))
    except Exception as e:
        out['err'] = 'diskutil: %s' % str(e)[:120]
    u = shutil.disk_usage(vol)
    out.update(free=u.free, total=u.total)
    sc = next((x for x in ('/opt/homebrew/bin/smartctl', '/usr/local/bin/smartctl', shutil.which('smartctl') or '') if x and os.path.exists(x)), None)
    out['smartctl'] = bool(sc)
    if sc and out.get('whole'):
        for dev in (['-d', 'sat'], []):   # (through the USB or Thunderbolt bridge, as most external drives need; else as it is)
            try:
                r = subprocess.run([sc, '-a', '-j'] + dev + ['/dev/' + out['whole']], capture_output=True, text=True, timeout=90)
                j = json.loads(r.stdout or '{}')
            except Exception:
                continue
            if not j.get('smart_status') and not j.get('ata_smart_attributes'):
                continue
            at = {a.get('id'): a for a in (j.get('ata_smart_attributes') or {}).get('table', [])}
            raw = lambda i: ((at.get(i) or {}).get('raw') or {}).get('value')
            out.update(passed=(j.get('smart_status') or {}).get('passed'), hours=(j.get('power_on_time') or {}).get('hours') or raw(9),
                       realloc=raw(5), pending=raw(197), uncorrect=raw(198), temp=(j.get('temperature') or {}).get('current') or raw(194),
                       model=(j.get('model_name') or ''), serial_tail=str(j.get('serial_number') or '')[-4:])
            break
    bad = out.get('passed') is False or (out.get('realloc') or 0) > 0 or (out.get('pending') or 0) > 0 or (out.get('uncorrect') or 0) > 0 or (out.get('smart_mac') or '').lower() in ('failing',)
    warn = (out.get('temp') or 0) >= 55 or (out.get('free') or 0) < 0.03 * (out.get('total') or 1)
    out['state'] = 'bad' if bad else 'warn' if warn else 'ok' if (out.get('passed') or (out.get('smart_mac') or '').lower() == 'verified') else 'unknown'
    return out


def keep_disk_health():
    """The G-Drive looked at once a day (and at start); a drive starting to fail logged at once."""
    time.sleep(240)
    while True:
        try:
            h = disk_health()
            DHEALTH.clear(); DHEALTH.update(h)
            save(os.path.expanduser('~/Grifflix/disk-health.json'), h)
            if h.get('state') == 'bad':
                log('THE G-DRIVE IS STARTING TO FAIL: %s' % {k: h.get(k) for k in ('passed', 'realloc', 'pending', 'uncorrect', 'hours', 'smart_mac')})
        except Exception as e:
            log('G-Drive health:', str(e)[:200])
        time.sleep(24 * 3600)


# ---- the nightly backup to Google Drive (John, 18 Nov 2026: what can't be got again, "Full film list should be backed up with it.
# Scripts included. Plus grifflix files from GitHub"). Into "Grifflix Backup" on his Google Drive (never the Grifflix folder there: that's
# where films come in from, each deleted once it's on the G-Drive). Each night's copy replaces the last; anything changed or gone kept
# under Earlier/<date> for BACKUP_KEEP days. Nothing secret goes: no sign-ins, keys or tokens.
BACKUP_AT = os.path.expanduser('~/Grifflix/backup.json')
BACKUP_STAGE = os.path.expanduser('~/Grifflix-backup')   # (made here first: outside ~/Grifflix, so it isn't sent twice)
BACKUP_TO = 'Grifflix Backup'
BACKUP_KEEP = 14
BACKUP_HOUR = 3   # (each night at about 03:40, the Mac's own time)
BACKUP_NOW = threading.Event()
BACKUP_STATE = {}
# (what never goes: sign-ins, keys and tokens; the big programs it can get again; films on their way in; the Mac's own leftovers)
BACKUP_SKIP = ['rclone', 'rclone.conf', '*.conf', 'settings.json', 'torbox.json', '*secret*', '*token*', '*.pickle', '*key*', '*.pem',
               '*.pkg', '*.zip', '*.dmg', 'incoming/**', '__pycache__/**', '*.pyc', '*.prev', '*.lock', '*.tmp', '._*', '.DS_Store',
               '*.part', '*.rlog', 'watching', 'fetch-waiting', '.shas.json', 'Grifflix Converting/**', 'tunnel.log', 'setup.log', 'audio_metadata.csv']
GH_PARTS = ('grifflix/', '_grifflix/', '.claude/skills/grifflix-theme/')   # (the Grifflix files in the repository)


def backup_github(dest):
    """The Grifflix files from GitHub into dest, only those changed since last night fetched (GitHub's own fingerprint of each)."""
    def fetch(url, accept='application/vnd.github+json'):
        req = urllib.request.Request(url, headers={'User-Agent': 'Grifflix gateway', 'Accept': accept})
        with urllib.request.urlopen(req, timeout=120, context=tls()) as r:
            return r.read()
    tree = json.loads(fetch('https://api.github.com/repos/%s/git/trees/main?recursive=1' % REPO))
    want = {t['path']: t['sha'] for t in tree.get('tree') or [] if t.get('type') == 'blob' and t['path'].startswith(GH_PARTS)}
    if not want:
        raise RuntimeError('GitHub listed no Grifflix files')
    shas_at = os.path.join(dest, '.shas.json')
    had = load(shas_at, {}) or {}
    got = 0
    for p, sha in want.items():
        f = os.path.join(dest, *p.split('/'))
        if had.get(p) == sha and os.path.exists(f):
            continue
        data = fetch('https://raw.githubusercontent.com/%s/main/%s' % (REPO, urllib.parse.quote(p)), '*/*')
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f + '.tmp', 'wb') as o:
            o.write(data)
        os.replace(f + '.tmp', f)
        had[p] = sha
        got += 1
    for p in [p for p in had if p not in want]:   # (gone from GitHub: gone here too; last night's copy kept under Earlier)
        try:
            os.remove(os.path.join(dest, *p.split('/')))
        except OSError:
            pass
        had.pop(p, None)
    save(shas_at, had)
    return len(want), got


def backup_lists(g, dest):
    """The full film list (and the series), and every file on the G-Drive, as spreadsheets: what to get again if the drive were lost."""
    import csv
    os.makedirs(dest, exist_ok=True)
    cache = (load(os.path.expanduser('~/Grifflix/library-cache.json'), {}) or {}).get('rows') or {}
    items, start = [], 0
    while True:
        r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie,Series', 'Fields': 'Path,ProductionYear',
                                                               'StartIndex': start, 'Limit': 500, 'SortBy': 'SortName', 'EnableImages': 'false'}) or {}
        items += r.get('Items') or []
        start += 500
        if start >= (r.get('TotalRecordCount') or 0):
            break
    if not items:
        raise RuntimeError('the media server listed no films')
    res = lambda w, h: '' if not w else '4K' if w >= 3200 or h >= 1700 else '1080p' if w >= 1800 or h >= 1000 else '720p' if w >= 1200 or h >= 700 else 'SD'
    with open(os.path.join(dest, 'Grifflix film list.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['Kind', 'Title', 'Year', 'Size', 'Width', 'Height', 'Format', 'Mbps', 'GB', 'Minutes', 'Sound', 'Where it is'])
        for it in sorted(items, key=lambda x: (x.get('Type') != 'Movie', (x.get('Name') or '').lower())):
            d = (cache.get(it.get('Id')) or {}).get('r') or {}
            w.writerow(['Film' if it.get('Type') == 'Movie' else 'Series', it.get('Name') or '', it.get('ProductionYear') or '',
                        res(d.get('w') or 0, d.get('h') or 0), d.get('w') or '', d.get('h') or '', (d.get('v') or '').upper(), d.get('mbps') or '',
                        d.get('gb') or '', d.get('mins') or '', d.get('a') or '', it.get('Path') or ''])
    root, n = '/Volumes/G-Drive/Grifflix', 0
    if os.path.isdir(root):
        with open(os.path.join(dest, 'Every file on the G-Drive.csv'), 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['Folder', 'File', 'GB', 'Changed'])
            t_end = time.time() + 1200   # (a drive this busy: the list as far as it got in 20 minutes, not the backup held up)
            for top, dirs, files in os.walk(root):
                if time.time() > t_end:
                    w.writerow(['(the drive was too busy to list the rest tonight)', '', '', ''])
                    break
                dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != 'Grifflix Converting')
                for fn in sorted(files):
                    if fn.startswith('.'):
                        continue
                    try:
                        st = os.stat(os.path.join(top, fn))
                    except OSError:
                        continue
                    w.writerow([os.path.relpath(top, root), fn, round(st.st_size / 1e9, 2), time.strftime('%Y-%m-%d', time.localtime(st.st_mtime))])
                    n += 1
    return len(items), n


def backup_media_server(dest):
    """The media server's own settings: each collection, playlist, its settings and its libraries. Not its databases: they're the
    watch history (John, 18 Nov 2026: "I don't need to keep watch history") and what it reads from the films, made again by a scan."""
    import shutil
    base = os.path.expanduser('~/.local/share/jellyfin')
    os.makedirs(dest, exist_ok=True)
    for old in ('library.db', 'jellyfin.db', 'library.db.gz', 'jellyfin.db.gz'):   # (an earlier backup's copies)
        try:
            os.remove(os.path.join(dest, old))
        except OSError:
            pass
    for sub in ('data/collections', 'data/playlists', 'config', 'root'):
        s = os.path.join(base, *sub.split('/'))
        d = os.path.join(dest, *sub.split('/'))
        if os.path.isdir(s):
            shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(s, d, ignore=shutil.ignore_patterns('*.log', '._*', '.DS_Store'))


def audio_sheet_once():
    """Once: the audio spreadsheet (scripts/audio_metadata.csv) made a Google Sheet of its own, "Grifflix audio metadata", on John's
    Google Drive, without the cover pictures held inside it (30 MB of them); it's left out of the nightly backup (John, 18 Nov 2026:
    "Keep that audio spreadsheet separate and put in a google sheet - doesn't need normal back up")."""
    import csv, shutil, subprocess
    mk = os.path.expanduser('~/Grifflix/audio-sheet.done')
    src = '/Volumes/G-Drive/Grifflix/scripts/audio_metadata.csv'
    home = os.path.expanduser('~/Grifflix')
    rcl, conf = os.path.join(home, 'rclone'), os.path.join(home, 'rclone.conf')
    if os.path.exists(mk) or not os.path.exists(src) or not os.path.exists(conf):
        return
    csv.field_size_limit(1 << 30)
    os.makedirs(BACKUP_STAGE, exist_ok=True)
    with open(src, newline='', encoding='utf-8', errors='replace') as f:
        R = list(csv.reader(f))
    h = R[0]
    big = set(i for i in range(len(h)) if any(len(r[i]) > 2000 for r in R[1:] if i < len(r)))   # (the pictures)
    keep = [i for i in range(len(h)) if i not in big and any(i < len(r) and r[i].strip() for r in R[1:])]
    out = os.path.join(BACKUP_STAGE, 'Grifflix audio metadata.csv')
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        for r in R:
            w.writerow([(r[i] if i < len(r) else '')[:45000] for i in keep])
    cf = os.path.join(BACKUP_STAGE, '.rclone-audio.conf')
    shutil.copyfile(conf, cf); os.chmod(cf, 0o600)
    try:
        r = subprocess.run([rcl, '--config', cf, 'copyto', out, 'gdrive:Grifflix audio metadata.csv', '--drive-import-formats', 'csv', '--drive-export-formats', 'csv', '-q'],
                           capture_output=True, text=True, timeout=1800)
    finally:
        os.remove(cf)
    if r.returncode:
        log('Audio spreadsheet to a Google Sheet: couldn’t:', (r.stderr or '').strip()[-200:])
        return
    log('Audio spreadsheet: a Google Sheet now, “Grifflix audio metadata” on Google Drive (%d rows, %d columns; the cover pictures left out)' % (len(R) - 1, len(keep)))
    open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))


def backup_now(g):
    """One backup: made, sent, the old ones beyond BACKUP_KEEP days cleared. What happened, for the admin page."""
    import shutil, subprocess
    home = os.path.expanduser('~/Grifflix')
    rcl, conf = os.path.join(home, 'rclone'), os.path.join(home, 'rclone.conf')
    t0, day = time.time(), time.strftime('%Y-%m-%d')
    out = {'at': int(t0), 'ok': False, 'parts': {}}
    BACKUP_STATE.clear(); BACKUP_STATE.update({'running': True, 'started': int(t0)})
    try:
        if not (os.path.exists(rcl) and os.path.exists(conf)):
            raise RuntimeError('Google Drive isn’t set up on the Mac mini (gdrive.py setup)')
        os.makedirs(BACKUP_STAGE, exist_ok=True)
        # (its own copy of the sign-in, so this and the downloads never write the same file at once)
        cf = os.path.join(BACKUP_STAGE, '.rclone.conf')
        shutil.copyfile(conf, cf); os.chmod(cf, 0o600)
        notes = []
        try:
            n, got = backup_github(os.path.join(BACKUP_STAGE, 'GitHub'))
            notes.append('%d Grifflix files from GitHub (%d new or changed)' % (n, got))
        except Exception as e:
            notes.append('GitHub: ' + str(e)[:150])
        try:
            films, files = backup_lists(g, os.path.join(BACKUP_STAGE, 'Film list'))
            out['films'] = films
            notes.append('the list of %d films and series, %d files on the G-Drive' % (films, files))
        except Exception as e:
            notes.append('film list: ' + str(e)[:150])
        try:
            backup_media_server(os.path.join(BACKUP_STAGE, 'Media server'))
        except Exception as e:
            notes.append('media server: ' + str(e)[:150])
        parts = [('Grifflix folder', home), ('Gateway', APP_DIR), ('Scripts', '/Volumes/G-Drive/Grifflix/scripts'),
                 ('Film list', os.path.join(BACKUP_STAGE, 'Film list')), ('Media server', os.path.join(BACKUP_STAGE, 'Media server')),
                 ('GitHub', os.path.join(BACKUP_STAGE, 'GitHub'))]
        skip = sum((['--exclude', x] for x in BACKUP_SKIP), [])
        bad = []
        for name, src in parts:
            if not os.path.isdir(src):
                out['parts'][name] = 'not there'
                continue
            r = subprocess.run([rcl, '--config', cf, 'sync', src, 'gdrive:%s/Latest/%s' % (BACKUP_TO, name),
                                '--backup-dir', 'gdrive:%s/Earlier/%s/%s' % (BACKUP_TO, day, name), '--copy-links', '--checksum', '--transfers', '4',
                                '--retries', '3', '--low-level-retries', '10', '--stats-one-line', '-q'] + skip,
                               capture_output=True, text=True, timeout=6 * 3600)
            if r.returncode:
                bad.append(name)
                out['parts'][name] = 'failed: ' + (r.stderr or '').strip()[-200:]
            else:
                out['parts'][name] = 'ok'
        try:   # (how much it all is, on Google Drive)
            sz = json.loads(subprocess.run([rcl, '--config', cf, 'size', '--json', 'gdrive:%s/Latest' % BACKUP_TO], capture_output=True, text=True, timeout=900).stdout or '{}')
            out['files'], out['bytes'] = sz.get('count'), sz.get('bytes')
        except Exception:
            pass
        try:   # (the earlier ones beyond BACKUP_KEEP days cleared)
            ls = subprocess.run([rcl, '--config', cf, 'lsf', '--dirs-only', 'gdrive:%s/Earlier' % BACKUP_TO], capture_output=True, text=True, timeout=300).stdout
            cut = time.strftime('%Y-%m-%d', time.localtime(t0 - BACKUP_KEEP * 86400))
            for d in [x.strip('/') for x in ls.split()]:
                if re.match(r'^\d{4}-\d\d-\d\d$', d) and d < cut:
                    subprocess.run([rcl, '--config', cf, 'purge', 'gdrive:%s/Earlier/%s' % (BACKUP_TO, d)], capture_output=True, timeout=900)
        except Exception:
            pass
        try:
            ab = json.loads(subprocess.run([rcl, '--config', cf, 'about', '--json', 'gdrive:'], capture_output=True, text=True, timeout=300).stdout or '{}')
            out['gfree'] = ab.get('free')
        except Exception:
            pass
        out['ok'] = not bad
        out['notes'] = notes
        if bad:
            out['why'] = 'not sent: ' + ', '.join(bad)
        log('Backup to Google Drive: %s in %d min; %s' % ('done' if not bad else out['why'], round((time.time() - t0) / 60), '; '.join(notes)))
    except Exception as e:
        out['why'] = str(e)[:200]
        log('Backup to Google Drive: couldn’t:', out['why'])
    finally:
        try:
            os.remove(os.path.join(BACKUP_STAGE, '.rclone.conf'))
        except OSError:
            pass
        out['took'] = int(time.time() - t0)
        BACKUP_STATE.clear()
    last = load(BACKUP_AT, {}) or {}
    if out['ok']:
        out['last_ok'] = out['at']
    elif last.get('last_ok'):
        out['last_ok'] = last['last_ok']
    save(BACKUP_AT, out)
    return out


BACKUP_LOCK = os.path.expanduser('~/Grifflix/backup.lock')


def backup_running():
    try:
        pid = int(open(BACKUP_LOCK).read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return 0


def keep_backup(g):
    """Each night at about 03:40, and whenever the admin page's Back up now is pressed; missed nights (the Mac off) made up at the next start.
    Made by a process of its own, so a restart of the gateway (each of its updates) doesn't stop it part way: the first backup was
    started and stopped five times on 18 Nov 2026, never finishing."""
    import subprocess
    time.sleep(120)
    while True:
        try:
            last = load(BACKUP_AT, {}) or {}
            lt = time.localtime()
            due = time.time() - (last.get('at') or 0) > 26 * 3600 or (lt.tm_hour == BACKUP_HOUR and lt.tm_min >= 40 and time.time() - (last.get('at') or 0) > 6 * 3600)
            if (BACKUP_NOW.is_set() or due) and not backup_running():
                BACKUP_NOW.clear()
                out = open(os.path.expanduser('~/Grifflix/backup.out'), 'a')
                subprocess.Popen([sys.executable, os.path.abspath(__file__), 'backup'], stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                                 start_new_session=True, cwd=os.path.expanduser('~/Grifflix'))
                log('Backup to Google Drive: started')
                time.sleep(60)
        except Exception as e:
            log('Backup to Google Drive:', str(e)[:200])
        BACKUP_NOW.wait(300)


def backup_main():
    """python3 grifflix-gateway.py backup: one backup, by itself (keep_backup starts it). Only what a backup needs: not the gateway's members
    store, which only the gateway writes."""
    if backup_running():
        sys.exit('A backup is running already.')
    open(BACKUP_LOCK, 'w').write(str(os.getpid()))
    try:
        st = load(SETTINGS, {})

        class L:
            pass
        lg = L()
        lg.st, lg.jf, lg.uid = st, Jellyfin(st['jellyfin'], st['jf_token']), st['jf_user_id']
        try:
            audio_sheet_once()
        except Exception as e:
            log('Audio spreadsheet to a Google Sheet:', str(e)[:200])
        backup_now(lg)
    finally:
        try:
            os.remove(BACKUP_LOCK)
        except OSError:
            pass


# ---- what Claude may ask to have done on the Mac mini, each only once John allows it on the admin page (John, 18 Nov 2026: "can I give
# you control to do things like quit plex?" ... "yes, build the approval list - I want to use screensharing as little as possible").
# Claude can only ask, and only for what's listed here; John's Allow does it, and each one is kept with what came of it.
CFEAT = {}   # (film id: its page on criterion.com, for its special features)
ACTIONS_AT = os.path.expanduser('~/Grifflix/claude-asks.json')
ACT_LOCK = threading.Lock()
PLEX_APP = 'Plex Media Server'
SETASIDE = '/Volumes/G-Drive/Grifflix/Set aside'


def _run(cmd, t=60):
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=t)
    return r.returncode, ((r.stdout or '') + (r.stderr or '')).strip()


def _plex_running():
    return _run(['pgrep', '-x', PLEX_APP])[0] == 0


def act_plex_off(g, req):
    """Plex quit, and kept off: if it starts again (at login, or by itself), it's quit again (keep_plex_off)."""
    g.st['plex_off'] = True
    save(SETTINGS, g.st)
    _run(['osascript', '-e', 'quit app "%s"' % PLEX_APP], 30)
    for _ in range(20):
        if not _plex_running():
            break
        time.sleep(1)
    if _plex_running():
        _run(['pkill', '-x', PLEX_APP])
        time.sleep(3)
    return 'Plex quit, and kept off from now on.' if not _plex_running() else 'Plex wouldn’t quit; it’s kept off, and tried again in 5 minutes.'


def act_plex_on(g, req):
    g.st['plex_off'] = False
    save(SETTINGS, g.st)
    _run(['open', '-a', PLEX_APP], 30)
    time.sleep(5)
    return 'Plex started again, and left alone from now on.' if _plex_running() else 'Plex was let be, but didn’t start: it may need opening on the Mac mini.'


def act_jellyfin_restart(g, req):
    if someone_watching(g):
        raise RuntimeError('someone is watching: not restarted (allow it again later)')
    _run(['pkill', '-x', 'Jellyfin Server'])
    _run(['pkill', '-x', 'jellyfin'])
    time.sleep(8)
    _run(['open', '-a', 'Jellyfin'], 30)
    for _ in range(60):
        time.sleep(2)
        try:
            urllib.request.urlopen('http://127.0.0.1:8096/System/Info/Public', timeout=5).read()
            return 'The media server restarted and is answering again.'
        except Exception:
            pass
    return 'The media server was restarted but isn’t answering yet: it may still be starting.'


def act_converter_restart(g, req):
    home = os.path.expanduser('~/Grifflix')
    cur = load(os.path.join(home, 'convert-now.json'), None) or {}
    _run(['pkill', '-f', 'convert.py go'])
    time.sleep(5)
    CONV_NOW.set()   # (keep_converting starts it again at once)
    return 'The converter was stopped and starts again within a minute%s.' % ((' (it was on %s)' % cur.get('name')) if cur.get('name') else '')


def _setaside_list():
    out = []
    if os.path.isdir(SETASIDE):
        for top, dirs, files in os.walk(SETASIDE):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.startswith('.'):
                    continue
                p = os.path.join(top, f)
                try:
                    out.append((p, os.path.getsize(p)))
                except OSError:
                    pass
    return out


def act_setaside_empty(g, req):
    """Only the files listed when it was asked for, and only inside Set aside."""
    import shutil
    gone, size = 0, 0
    real = os.path.realpath(SETASIDE) + os.sep
    for p, _ in [(x['p'], x['b']) for x in (req.get('files') or [])]:
        rp = os.path.realpath(p)
        if not rp.startswith(real) or not os.path.isfile(rp):
            continue
        try:
            size += os.path.getsize(rp)
            os.remove(rp)
            gone += 1
        except OSError:
            pass
    for top, dirs, files in sorted(os.walk(SETASIDE), key=lambda x: -len(x[0])):   # (folders left empty taken away too)
        if top != SETASIDE and not [f for f in os.listdir(top) if not f.startswith('.')]:
            shutil.rmtree(top, ignore_errors=True)
    return '%d file%s deleted from Set aside, %.1f GB freed.' % (gone, '' if gone == 1 else 's', size / 1e9)


def ask_setaside(req):
    L = _setaside_list()
    if not L:
        raise RuntimeError('Set aside is empty')
    req['files'] = [{'p': p, 'b': b} for p, b in L][:2000]
    req['detail'] = '%d files, %.1f GB: %s' % (len(L), sum(b for _, b in L) / 1e9, '; '.join(os.path.relpath(p, SETASIDE) for p, _ in L[:40]) + (' …' if len(L) > 40 else ''))


def _other_dirs(g):
    """Each film's "Other" (or "Others") folder holding extras: [(film folder, its Other folder)]."""
    out = []
    for v in g.jf.call('GET', '/Library/VirtualFolders') or []:
        tv = (v.get('CollectionType') or '') == 'tvshows'   # (and each series', in its own folder or a season's: John, 18 Nov 2026, "I assume TV extras need updating to jellyfin rules too")
        if (v.get('CollectionType') or '') not in ('movies', 'tvshows'):
            continue
        for r in v.get('Locations') or []:
            try:
                films = os.listdir(r)
            except OSError:
                continue
            for f in films:
                d = os.path.join(r, f)
                if f.startswith('.') or not os.path.isdir(d):
                    continue
                try:
                    for d2 in [d] + ([os.path.join(d, x) for x in os.listdir(d) if re.match(r'^(season|series|specials)\b', x, re.I) and os.path.isdir(os.path.join(d, x))] if tv else []):
                        for x in os.listdir(d2):
                            if x.lower() in ('other', 'others') and os.path.isdir(os.path.join(d2, x)):
                                out.append((d2, os.path.join(d2, x)))
                except OSError:
                    pass
    return out


def ask_extras_rename(req):
    g = G_REF[0] if G_REF else None
    L = _other_dirs(g) if g else []
    if not L:
        raise RuntimeError('no film has an Other folder')
    req['detail'] = '%d films: %s' % (len(L), ', '.join(os.path.basename(d) for d, _ in L[:25]) + (' …' if len(L) > 25 else ''))


def act_extras_rename(g, req):
    """Each film's "Other" folder named "Extras", the name this media server knows (10.8 doesn't know "Other", Plex's name), its
    extras in it as they were; then each film asked to be read again (John, 18 Nov 2026: The Crying Game's extras not shown)."""
    import shutil
    n = 0
    for d, o in _other_dirs(g):
        x = os.path.join(d, 'Extras')
        try:
            if not os.path.exists(x):
                os.rename(o, x)
            else:
                for f in os.listdir(o):
                    if not os.path.exists(os.path.join(x, f)):
                        shutil.move(os.path.join(o, f), os.path.join(x, f))
                if not [f for f in os.listdir(o) if not f.startswith('.')]:
                    shutil.rmtree(o, ignore_errors=True)
            n += 1
        except OSError as e:
            log('Extras folder for %s: %s' % (os.path.basename(d), e))
    threading.Thread(target=extras_fix, args=(g, 2000), daemon=True).start()
    return '%d films’ Other folders are Extras now; the media server is reading each again (a few seconds a film).' % n


ACTIONS = {
    'plex-off': ('Quit Plex and keep it off', act_plex_off, None),
    'plex-on': ('Start Plex again and let it be', act_plex_on, None),
    'jellyfin-restart': ('Restart the media server (not while anyone watches)', act_jellyfin_restart, None),
    'converter-restart': ('Restart the converter (it starts its film again)', act_converter_restart, None),
    'setaside-empty': ('Empty “Set aside” on the G-Drive (deleted for good)', act_setaside_empty, ask_setaside),
    'extras-rename': ('Rename each film’s “Other” folder to “Extras”, so the media server shows its extras', act_extras_rename, ask_extras_rename),
}


def claude_ask(action, why):
    if action not in ACTIONS:
        return 400, {'error': 'Not something that can be asked for.', 'can': sorted(ACTIONS)}
    with ACT_LOCK:
        L = load(ACTIONS_AT, []) or []
        if sum(1 for x in L if x.get('state') == 'asked') >= 10:
            return 429, {'error': 'Ten are already waiting for John.'}
        if any(x.get('state') == 'asked' and x.get('action') == action for x in L):
            return 409, {'error': 'Already asked, waiting for John.'}
        req = {'id': secrets.token_hex(6), 'action': action, 'title': ACTIONS[action][0], 'why': str(why or '')[:500], 'at': int(time.time()), 'state': 'asked'}
        if ACTIONS[action][2]:
            try:
                ACTIONS[action][2](req)
            except Exception as e:
                return 400, {'error': str(e)[:200]}
        L.append(req)
        save(ACTIONS_AT, L[-100:])
    log('Claude asks: %s (%s)' % (req['title'], req['why'][:120]))
    return 200, {k: v for k, v in req.items() if k != 'files'}


def claude_answer(g, rid, allow):
    with ACT_LOCK:
        L = load(ACTIONS_AT, []) or []
        req = next((x for x in L if x.get('id') == rid), None)
        if not req or req.get('state') != 'asked':
            return 404, {'error': 'Not waiting.'}
        req['state'] = 'doing' if allow else 'declined'
        req['answered'] = int(time.time())
        save(ACTIONS_AT, L)
    if not allow:
        log('Claude asked, John declined: %s' % req['title'])
        return 200, {'ok': True}

    def run():
        try:
            res, st = ACTIONS[req['action']][1](g, req), 'done'
        except Exception as e:
            res, st = str(e)[:300], 'failed'
        with ACT_LOCK:
            L2 = load(ACTIONS_AT, []) or []
            for x in L2:
                if x.get('id') == rid:
                    x.update(state=st, result=res, finished=int(time.time()))
                    x.pop('files', None)
            save(ACTIONS_AT, L2)
        log('Claude asked, John allowed: %s: %s' % (req['title'], res))
    threading.Thread(target=run, daemon=True).start()
    return 200, {'ok': True}


XDIRS = ('other', 'extras', 'featurettes', 'behind the scenes', 'deleted scenes', 'interviews', 'scenes', 'shorts', 'clips', 'trailers', 'samples')
XVID = ('.mp4', '.mkv', '.m4v', '.mov', '.avi')


def extras_fix(g, most=60):
    """Films whose extras sit in their folder (Other, Extras, Featurettes…) but the media server has none for: it's asked to look at
    each again. A film converted or repackaged is a new file, and the media server didn't find the old one's extras for it (John,
    18 Nov 2026: "how do I see the extras on a film. The Crying Game for instance": five in its Other folder, none shown)."""
    its = []
    for key_, typ_ in (('movies', 'Movie'), ('tv', 'Series')):
        lid = (g.st.get('libraries') or {}).get(key_)
        start = 0
        while lid:
            r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': typ_, 'Fields': 'Path,SpecialFeatureCount',
                                                                   'StartIndex': start, 'Limit': 500, 'EnableImages': 'false'}) or {}
            its += r.get('Items') or []
            start += 500
            if start >= (r.get('TotalRecordCount') or 0):
                break
    asked = []
    for it in its:
        if (it.get('SpecialFeatureCount') or 0) or len(asked) >= most:
            continue
        d = (it.get('Path') or '') if it.get('Type') == 'Series' else os.path.dirname(it.get('Path') or '')
        try:
            subs = [x for x in os.listdir(d) if x.lower() in XDIRS and os.path.isdir(os.path.join(d, x))]
        except OSError:
            continue
        if not any(f.lower().endswith(XVID) and not f.startswith('.') for x in subs for f in os.listdir(os.path.join(d, x))):
            continue
        try:
            g.jf.call('POST', '/Items/%s/Refresh' % it['Id'], query={'Recursive': 'true', 'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'Default', 'ReplaceAllMetadata': 'false', 'ReplaceAllImages': 'false'})
            asked.append(it.get('Name'))
            time.sleep(2)
        except Exception as e:
            log('Extras for %s: %s' % (it.get('Name'), e))
    if asked:
        log('Extras: the media server asked to look again at %d film%s with extras it hadn’t found: %s' % (len(asked), '' if len(asked) == 1 else 's', ', '.join(asked[:12]) + (' …' if len(asked) > 12 else '')))
        g.forget()
        g.publish_soon = time.time() + 600
    return len(asked)


def keep_extras(g):
    """extras_fix 6 minutes after the start, then every 6 hours (each new conversion's extras found within the day)."""
    time.sleep(360)
    while True:
        try:
            extras_fix(g)
        except Exception as e:
            log('Extras:', str(e)[:200])
        time.sleep(6 * 3600)


def keep_plex_off(g):
    """While John has had Plex kept off: quit again if it starts (at login, or by itself), every 5 minutes."""
    time.sleep(120)
    while True:
        try:
            if g.st.get('plex_off') and _plex_running():
                _run(['osascript', '-e', 'quit app "%s"' % PLEX_APP], 30)
                time.sleep(15)
                if _plex_running():
                    _run(['pkill', '-x', PLEX_APP])
                log('Plex had started: quit again (kept off)')
        except Exception as e:
            log('Keeping Plex off:', str(e)[:150])
        time.sleep(300)


def disks():
    """Free space on the Mac's own disk and the drives its films are on, for the admin page (John, 12 Nov 2026: the Mac's own disk
    filled up unnoticed, and nothing on the Mac could write)."""
    import shutil
    out, seen = [], set()
    paths = ['/'] + ['/Volumes/' + v for v in (os.listdir('/Volumes') if os.path.isdir('/Volumes') else [])]
    for pth in paths:
        try:
            st = os.stat(pth)
            if st.st_dev in seen:
                continue
            seen.add(st.st_dev)
            u = shutil.disk_usage(pth)
            if u.total < 20e9:   # (small system volumes: not worth showing)
                continue
            out.append({'name': 'The Mac’s own disk' if pth == '/' else os.path.basename(pth), 'free': u.free, 'total': u.total})
        except OSError:
            pass
    return out


def conv_alive(cur):
    """Whether the converter that wrote convert-now.json is still running: its process number, and that number really the
    converter's. After a restart the Mac gives process numbers out again, so another program could have the old one: it looked
    alive, was never started again, and showed as stuck at 0% (John, 14 Nov 2026, "The convert process hasn't started again since we
    shut it down to move the Mac mini")."""
    try:
        pid = int((cur or {}).get('pid') or 0)
        if not pid or (cur or {}).get('state') == 'finished':
            return 0
        os.kill(pid, 0)
        import subprocess
        cmd = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, timeout=10).stdout
        return pid if 'convert.py' in cmd else 0
    except Exception:
        return 0


def site_update(g, why=''):
    """The site's copy of the films made again, as John's Update on the admin page does: the Mac's list asked of this gateway (as John's
    Mac sign-in), sent to the Worker, then the Worker asked to publish it. Returns what changed, or raises."""
    tok = g.st.get('worker_token')
    if not tok:
        raise RuntimeError('automatic updates are off (turn them on on the admin page, Updates)')
    port = int(g.st.get('port', 8097))
    r = urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%d/api/export' % port, headers={'Authorization': 'Bearer ' + tok}), timeout=3600)
    text = r.read()
    snap = json.loads(text.decode('utf-8'))
    def worker(path, body, ctype='application/json'):
        req = urllib.request.Request(WORKER + path, data=body, method='POST', headers={'Authorization': 'Bearer ' + tok, 'Content-Type': ctype, 'User-Agent': 'Grifflix gateway'})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=300, context=tls()).read().decode('utf-8') or '{}')
        except urllib.error.HTTPError as e:
            raise RuntimeError('the site said %s: %s' % (e.code, e.read()[:200].decode('utf-8', 'replace')))
    worker('/g/snapshot', text)
    eps = {e['id']: e for v in (snap.get('series') or {}).values() for e in v.get('episodes') or []}
    items, motd = snap.get('items') or {}, snap.get('motd') or {}
    days = {d: (items.get(i) or eps.get(i)) for d, i in (motd.get('days') or {}).items() if items.get(i) or eps.get(i)}
    L = snap.get('lists') or {}
    worker('/g/admin', json.dumps({'op': 'update', 'cal': {'days': days, 'notes': motd.get('notes') or {}}, 'personal': snap.get('personal'),
                                   'counts': {'movies': len(L.get('movies') or []), 'tv': len(L.get('tv') or []), 'collections': len(L.get('collections') or [])}}).encode('utf-8'))
    c = snap.get('changes') or {}
    g.st['auto_update_last'] = {'at': int(time.time()), 'why': why, 'new': c.get('new', 0), 'changed': c.get('changed', 0), 'gone': c.get('removed', 0)}
    save(SETTINGS, g.st)
    return c


CONVJOBS = {}   # (films being converted on John's Mac: their one-time addresses)
CONVJOBS_AT = os.path.expanduser('~/Grifflix/convert-jobs.json')   # (kept through a restart: the film's Edit panel can still say how it went)


MACQ = os.path.expanduser('~/Grifflix/mac-queue.json')   # (films queued for John's Mac, in his order)
MACAGENT = {}   # (when his Mac's helper last asked, and whether it was at home)


def fanny_series(g):
    """Once: Fanny and Alexander's television version, four files in a film's folder (the media server took them for one film's parts and
    showed part 1, 1 h 36), made a miniseries in the TV library, its parts its episodes in order; put in The Criterion Collection; it and the
    cinema version and The Making Of given their spine numbers, so they come together as their box set (261) (John, 18 Nov 2026: "it should
    probably treat a mini series like a tv miniseries and the files should be ordered and put in a file structure where it understands that.
    Then just added to the collection in a box set")."""
    import shutil
    mk = os.path.expanduser('~/Grifflix/fanny-series.done')
    if os.path.exists(mk):
        return
    time.sleep(150)
    try:
        roots = {v.get('CollectionType'): (v.get('Locations') or [None])[0] for v in (g.jf.call('GET', '/Library/VirtualFolders') or [])}
        mv, tv = roots.get('movies'), roots.get('tvshows')
        src = os.path.join(mv or '', 'Fanny and Alexander - Television Version(1983)')
        if not (mv and tv and os.path.isdir(src)):
            open(mk, 'w').write('nothing to do')
            return
        name = 'Fanny and Alexander (1983)'
        sdir = os.path.join(tv, name, 'Season 01')
        os.makedirs(sdir, exist_ok=True)
        parts = sorted((n for n in os.listdir(src) if re.search(r'-pt(\d+)\.(mkv|mp4|m4v|avi)$', n, re.I)), key=lambda n: int(re.search(r'-pt(\d+)\.', n, re.I).group(1)))
        moved = []
        for n in parts:
            ep = int(re.search(r'-pt(\d+)\.', n, re.I).group(1))
            to = os.path.join(sdir, '%s S01E%02d%s' % (name, ep, os.path.splitext(n)[1].lower()))
            shutil.move(os.path.join(src, n), to)
            moved.append(to)
        if os.path.exists(os.path.join(src, 'poster.jpg')):
            shutil.move(os.path.join(src, 'poster.jpg'), os.path.join(tv, name, 'poster.jpg'))
        for n in os.listdir(src):   # (what's left: the Finder's own files)
            if n.startswith('.') or n.startswith('._'):
                try:
                    os.remove(os.path.join(src, n))
                except OSError:
                    pass
        try:
            os.rmdir(src)
            os.remove(os.path.join(mv, '._Fanny and Alexander - Television Version(1983)'))
        except OSError:
            pass
        own_f = os.path.expanduser('~/Grifflix/criterion-spines-own.json')
        own = load(own_f, {}) or {}
        own.update({name: 262, 'Fanny And Alexander (1982)': 263, 'The Making Of Fanny And Alexander (1984)': 264})
        save(own_f, own)
        log('Fanny and Alexander: the television version made a miniseries, %d episodes, in %s' % (len(moved), os.path.join(tv, name)))
        try:
            g.jf.call('POST', '/Library/Media/Updated', {'Updates': [{'Path': src, 'UpdateType': 'Deleted'}, {'Path': os.path.join(tv, name), 'UpdateType': 'Created'}]})
        except Exception:
            g.jf.call('POST', '/Library/Refresh')
        box = next((x for x in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'BoxSet'}) or {}).get('Items', []) if (x.get('Name') or '').strip().lower() == 'the criterion collection'), None)
        for _ in range(30):   # (once the media server has the series: into The Criterion Collection)
            time.sleep(60)
            ser = next((x for x in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Series', 'SearchTerm': 'Fanny', 'Fields': 'Path'}) or {}).get('Items', [])
                        if (x.get('Path') or '').rstrip('/').endswith(name)), None)
            if ser:
                if box:
                    g.jf.call('POST', '/Collections/%s/Items' % box['Id'], None, {'Ids': ser['Id']})
                log('Fanny and Alexander: the miniseries in The Criterion Collection')
                break
        open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    except Exception as e:
        log('Fanny and Alexander as a miniseries:', str(e)[:200])


def mac_job(iid, force=False):
    """A film planned for John's Mac, as the Mac mini's converter would plan it, and handed over by a one-time address: (code, answer)."""
    if not re.match(r'^[0-9a-f]{32}$', iid):
        return 400, {'error': 'Which film?'}
    import subprocess
    home = os.path.expanduser('~/Grifflix')
    cur = load(os.path.join(home, 'convert-now.json'), None) or {}
    if cur.get('id') == iid and conv_alive(cur):   # (the Mac mini at it already: not both)
        return 409, {'error': 'The Mac mini is converting it itself right now (%s%%): it carries on.' % cur.get('pct', 0)}
    r = subprocess.run([sys.executable, os.path.join(home, 'convert.py'), 'plan', iid] + (['force'] if force else []), capture_output=True, text=True, timeout=600, cwd=home)
    try:
        plan = json.loads([l for l in (r.stdout or '').splitlines() if l.startswith('{')][-1])
    except (IndexError, ValueError):
        return 500, {'error': 'The Mac mini couldn’t plan it: ' + (r.stderr or '').strip()[-200:]}
    if plan.get('error'):
        return 400, {'error': plan['error']}
    tok = secrets.token_urlsafe(18)
    CONVJOBS[tok] = {'plan': plan, 'at': time.time(), 'id': iid, 'state': 'waiting'}
    convjobs_save()
    claims = load(os.path.join(home, 'convert-claims.json'), {}) or {}
    claims[iid] = time.time()
    save(os.path.join(home, 'convert-claims.json'), claims)
    log('To be converted on John’s Mac: %s' % plan['item'].get('name'))
    return 200, {'token': tok, 'name': plan['item'].get('name')}


def claim_renew(iid):
    """A film being converted on John's Mac, its claim renewed with each word from it: the Mac mini's converter leaves a claimed film alone,
    and the claim lapses only half an hour after the last word (John, 17 Nov 2026: "we need a check that the mac mini doesn't try and convert
    anything that's being converted on the mac")."""
    try:
        f = os.path.expanduser('~/Grifflix/convert-claims.json')
        c = load(f, {}) or {}
        c[iid] = time.time()
        save(f, c)
    except Exception:
        pass


def convjobs_save():
    try:
        save(CONVJOBS_AT, {k: v for k, v in CONVJOBS.items() if time.time() - v.get('at', 0) < 86400})
    except Exception:
        pass


try:
    CONVJOBS.update({k: v for k, v in (load(CONVJOBS_AT, {}) or {}).items() if time.time() - v.get('at', 0) < 86400})
except Exception:
    pass


def vt_args(cmd):
    """The Mac mini's ffmpeg plan for a film, for a Mac with Apple's video hardware: the slow careful x264 encoder swapped for the
    hardware one at the same rate, everything else (the language first, every soundtrack, the subtitles) as planned."""
    out, i, rate = [], 0, None
    if '-maxrate' in cmd:
        rate = cmd[cmd.index('-maxrate') + 1]
    while i < len(cmd):
        a = cmd[i]
        if a in ('-preset', '-crf', '-level'):
            i += 2
            continue
        if a == '-c:v' and i + 1 < len(cmd) and cmd[i + 1] == 'libx264':
            out += ['-c:v', 'h264_videotoolbox', '-b:v', rate or '6000k', '-allow_sw', '1']
            i += 2
            continue
        out.append(a)
        i += 1
    return out


SCAN_NOW = threading.Event()   # (a film just brought in: the library looked at now, not at the next 10 minutes)
MACPLAN, MACPLAN_LOCK = {}, threading.Lock()   # (the next film for John's Mac, being planned)
CONV_NOW = threading.Event()   # (films just brought in: the converter started for them now, if it isn't running)
GDRIVE_NOW = threading.Event()   # (admin's Look now: a look straight away, not at the next quarter hour)
GDRIVE_NEXT = [0]
GDRIVE_ERR = ['']   # (why the last look didn't work, for the admin page)


def folder_pics(g, iid):
    """The pictures in a film's own folder (or a series' folder), by name: [(name, path)]."""
    it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path'}) or {}
    path = it.get('Path') or ''
    folder = path if it.get('Type') == 'Series' else os.path.dirname(path)
    if not folder or not os.path.isdir(folder):
        return []
    return sorted((n, os.path.join(folder, n)) for n in os.listdir(folder) if n.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')) and not n.startswith('.'))


TMDBC = {}


def tmdb(g, path, **q):
    """TMDB's own answers, with John's key (an API key, or a read access token), kept on the Mac (settings tmdb_key)."""
    k = g.st.get('tmdb_key') or ''
    if not k:
        return None
    h = {'Accept': 'application/json', 'User-Agent': 'Grifflix'}
    if k.startswith('eyJ'):
        h['Authorization'] = 'Bearer ' + k
    else:
        q['api_key'] = k
    u = 'https://api.themoviedb.org/3' + path + '?' + urllib.parse.urlencode(q)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=30, context=tls()) as r_:
                return json.loads(r_.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 + attempt * 3)
                continue
            if e.code == 404:
                return None
            raise
    return None


def tmdb_runtime(g, tid):   # a film's running time on TMDB (kept: ~/Grifflix/tmdb-runtimes.json)
    f = os.path.expanduser('~/Grifflix/tmdb-runtimes.json')
    if not TMDBC:
        TMDBC.update(load(f, {}))
    tid = str(tid)
    if tid not in TMDBC:
        d = tmdb(g, '/movie/%s' % tid) or {}
        TMDBC[tid] = {'rt': d.get('runtime') or 0, 'title': d.get('title'), 'year': (d.get('release_date') or '')[:4]}
        TMDBC['_dirty'] = 1
        time.sleep(.05)
    return TMDBC[tid]


CRIT_GENRES = ('Action/Adventure', 'Animation', 'Avant-garde', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Fantasy', 'Film Noir', 'Horror', 'Music',
               'Musical', 'Romance', 'Samurai', 'Science Fiction', 'Shorts', 'Silent', 'Thriller', 'War', 'Western')


def give_genres(g):
    """Each film of The Criterion Collection given Criterion's own genres in place of the ones it had, kept from being changed back; one
    Criterion gives none keeps its own (John, 17 Nov 2026: "Can previous genres for the film be removed and these added instead")."""
    home = os.path.expanduser('~/Grifflix')
    T = (load(os.path.join(home, 'criterion-tags.json'), {}) or {}).get('tags') or {}
    by = {}
    for k, v in T.items():
        if k in CRIT_GENRES:
            for sl in v:
                by.setdefault(sl, []).append(k)
    if not by:
        return
    gf = os.path.join(home, 'criterion-genres-given.json')
    given = load(gf, {}) or {}
    put = 0
    try:
        for iid, sl in crit_pairs(g):
            gs = sorted(by.get(sl) or [])
            if not gs or given.get(iid) == gs:
                continue
            try:
                full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid)) or {}
                if sorted(full.get('Genres') or []) != gs:
                    full['Genres'] = gs
                    full['GenreItems'] = [{'Name': x} for x in gs]
                    full['LockedFields'] = sorted(set((full.get('LockedFields') or []) + ['Genres']))
                    g.jf.call('POST', '/Items/%s' % iid, full)
                    put += 1
                given[iid] = gs
            except Exception as e:
                log('Criterion genres for %s: %s' % (iid, e))
        save(gf, given)
    except Exception as e:
        log('Criterion genres:', e)
    if put:
        log('Criterion genres: %d films given Criterion’s own' % put)
    g.forget()
    g.publish_soon = time.time() + 120


def syn_state():
    try:
        S_ = load(os.path.expanduser('~/Grifflix/criterion-synopses.json'), {}) or {}
    except Exception:
        S_ = {}
    return {'running': bool(SYN.get('t') and SYN['t'].is_alive()), 'got': SYN.get('got'), 'done': SYN.get('done', 0), 'of': SYN.get('of', 0),
            'put': SYN.get('put', 0), 'at': SYN.get('at'), 'err': SYN.get('err'), 'pages': len(S_.get('pages') or {}), 'given': len(S_.get('given') or {})}


def give_synopses(g):
    """Each film of The Criterion Collection given the synopsis its Criterion page has (kept from being changed back), saved as it
    goes, so a restart part way loses nothing: what's left is done next time (the admin page, or every six hours). John, 16 Nov 2026:
    "I uploaded the synopsis but no way to tell if worked" (it had been done inside the upload itself, too long for it)."""
    sf = os.path.expanduser('~/Grifflix/criterion-synopses.json')
    S_ = load(sf, {}) or {}
    pages, given = S_.get('pages') or {}, S_.setdefault('given', {})
    todo = []
    try:
        pairs = crit_pairs(g)
    except Exception as e:
        SYN['err'] = 'couldn’t read The Criterion Collection (%s)' % str(e)[:150]
        return log('Criterion synopses:', e)
    for iid, sl in pairs:
        txt = pages.get('https://www.criterion.com/films/' + sl)
        if txt and given.get(iid) != hashlib.sha1(txt.encode()).hexdigest()[:12]:
            todo.append((iid, txt))
    if not todo:   # (nothing new: what the admin card says left as it was)
        return
    SYN.update({'done': 0, 'put': 0, 'err': None, 'of': len(todo), 'at': time.time()})
    try:
        for k, (iid, txt) in enumerate(todo, 1):
            try:
                full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid)) or {}
                full['Overview'] = txt
                full['LockedFields'] = sorted(set((full.get('LockedFields') or []) + ['Overview']))
                g.jf.call('POST', '/Items/%s' % iid, full)
                given[iid] = hashlib.sha1(txt.encode()).hexdigest()[:12]
                SYN['put'] += 1
            except Exception as e:
                log('Criterion synopsis for %s: %s' % (iid, e))
            SYN['done'] = k
            if k % 25 == 0:
                save(sf, S_)
        save(sf, S_)
        if SYN['put']:
            g.forget()
            g.publish_soon = time.time() + 120
            log('Criterion synopses: %d films given theirs' % SYN['put'])
    except Exception as e:
        SYN['err'] = str(e)[:200]
        log('Criterion synopses:', e)
    SYN['at'] = time.time()


def grifflix_bins(g):
    """The Grifflix Bin beside each library's drive folder (as keep_scanning empties after 30 days)."""
    out = []
    for v in g.jf.call('GET', '/Library/VirtualFolders') or []:
        for r in v.get('Locations') or []:
            bn = os.path.join(os.path.dirname(os.path.dirname(r.rstrip('/'))), 'Grifflix Bin')
            if os.path.isdir(bn) and bn not in out:
                out.append(bn)
    return out


def crit_missing(g):
    """Every box set's films not in Grifflix, from Criterion's own sets (criterion-tags.json) and list: [set, set's spine, title, year,
    spine, Criterion's page], in the sets' order."""
    home = os.path.expanduser('~/Grifflix')
    ct = load(os.path.join(home, 'criterion-tags.json'), {}) or {}
    lst = load(os.path.join(home, 'criterion-list.json'), []) or []
    info, by_spine, by_ty, set_sp, set_nm = {}, {}, {}, {}, {}
    for f in lst:
        link = str(f.get('link') or '')
        sl = link.replace('https://www.criterion.com/films/', '').replace('https://www.criterion.com/boxsets/', '')
        if '/boxsets/' in link:
            set_sp[fold(f.get('title'))] = str(f.get('spine') or '')
            set_nm[fold(f.get('title'))] = str(f.get('title') or '')   # (its name as Criterion writes it)
            continue
        info[sl] = (str(f.get('title') or ''), str(f.get('year') or ''), str(f.get('spine') or ''), link)
        if str(f.get('spine') or '').isdigit():
            by_spine.setdefault(int(f['spine']), sl)
        if re.match(r'^\d{4}$', str(f.get('year') or '')):
            by_ty.setdefault((fold(f.get('title')), int(f['year'])), sl)
    owned = set(sl for _, sl in crit_pairs(g))
    try:
        its = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie,Series', 'Fields': 'Path,ProductionYear'}).get('Items') or []
        for it in its:
            sp = spine_of(it.get('Path'), it.get('Type') == 'Series')
            if sp and str(sp).isdigit() and int(sp) in by_spine:
                owned.add(by_spine[int(sp)])
            y = it.get('ProductionYear') or 0
            for d in (0, 1, -1):
                if (fold(it.get('Name')), y + d) in by_ty:
                    owned.add(by_ty[(fold(it.get('Name')), y + d)])
                    break
    except Exception as e:
        log('Criterion missing:', e)
    rows = []
    sp_of = ct.get('setspine') or {}
    def order(kv):
        sp = str(sp_of.get(kv[0]) or set_sp.get(fold(kv[0])) or '')
        return (0 if sp.isdigit() else 1, int(sp) if sp.isdigit() else 0, kv[0])
    for nm, mem in sorted((ct.get('sets') or {}).items(), key=order):
        sp = str(sp_of.get(nm) or set_sp.get(fold(nm)) or '')
        for sl in mem:
            if sl in owned:
                continue
            t, y, fsp, link = info.get(sl, (sl.split('-', 1)[-1].replace('-', ' ').title(), '', '', 'https://www.criterion.com/films/' + sl))
            import difflib
            nm2 = set_nm.get(fold(nm)) or next((set_nm[c] for c in difflib.get_close_matches(fold(nm), list(set_nm), 1, .75)), None) or nm
            rows.append([nm2, sp, t, y, fsp, link])
    return rows


def give_countries(g):
    """Each film of The Criterion Collection given Criterion's own country (or countries) in place of the media server's, kept from being
    changed back (John, 18 Nov 2026: "Use Criterion's country on the media server too?" ... "yes")."""
    home = os.path.expanduser('~/Grifflix')
    cty = {}
    for f in load(os.path.join(home, 'criterion-list.json'), []) or []:
        sl = str(f.get('link') or '').replace('https://www.criterion.com/films/', '')
        c = [x.strip() for x in re.split(r'[,/]', str(f.get('country') or '')) if x.strip()]
        if sl and c:
            cty[sl] = c
    if not cty:
        return
    gf = os.path.join(home, 'criterion-countries-given.json')
    given = load(gf, {}) or {}
    put = 0
    try:
        for iid, sl in crit_pairs(g):
            cs = cty.get(sl)
            if not cs or given.get(iid) == cs:
                continue
            try:
                full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid)) or {}
                if (full.get('ProductionLocations') or []) != cs:
                    full['ProductionLocations'] = cs
                    full['LockedFields'] = sorted(set((full.get('LockedFields') or []) + ['ProductionLocations']))
                    g.jf.call('POST', '/Items/%s' % iid, full)
                    put += 1
                given[iid] = cs
            except Exception as e:
                log('Criterion country for %s: %s' % (iid, e))
        save(gf, given)
    except Exception as e:
        log('Criterion countries:', e)
    if put:
        log('Criterion countries: %d films given Criterion’s own' % put)
        g.forget()
        g.publish_soon = time.time() + 120


def crit_pairs(g):
    """The films of The Criterion Collection, each with its page on criterion.com (from Criterion's list John last gave: its title and
    year, a year either side allowed): [(item id, page)]."""
    lst = load(os.path.expanduser('~/Grifflix/criterion-list.json'), []) or []
    by = {}
    for f in lst:
        sl = str(f.get('link') or '').replace('https://www.criterion.com/films/', '')
        if sl and '/' not in sl and re.match(r'^\d{4}$', str(f.get('year') or '')):
            by.setdefault((fold(f.get('title')), int(f['year'])), sl)
    sets = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'BoxSet'}).get('Items') or []
    box = next((x for x in sets if (x.get('Name') or '').strip().lower() == 'the criterion collection'), None)
    if not box:
        return []
    out = []
    for it in g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': box['Id'], 'Fields': 'ProductionYear'}).get('Items') or []:
        if it.get('Type') not in ('Movie', 'Series'):
            continue
        y = it.get('ProductionYear') or 0
        sl = next((by[(fold(it.get('Name')), y + d)] for d in (0, 1, -1) if (fold(it.get('Name')), y + d) in by), None)
        if sl:
            out.append((it['Id'], sl))
    return out


def apply_match(g, iid, pick):
    """A match from TMDB put on a film, as Fix match does: John's own pictures in its folder kept (copied aside first, only its details
    taken when it has its own, and put back if lost: The Freshman's Criterion poster, 16 Nov 2026)."""
    import shutil
    own, keep = [], None
    try:
        own = [(n, f) for n, f in folder_pics(g, iid) if not re.search(r'\.(srt|nfo)$', n, re.I)]
        if own:
            keep = os.path.join(APP_DIR, 'pictures-before', 'folder-%s-%d' % (iid, time.time()))
            os.makedirs(keep, exist_ok=True)
            for n, f in own:
                shutil.copy2(f, os.path.join(keep, n))
    except Exception as e:
        log('  (its folder’s pictures couldn’t be copied aside: %s)' % e)
    g.jf.call('POST', '/Items/RemoteSearch/Apply/%s' % iid, pick, {'replaceAllImages': 'false' if own else 'true'})
    try:
        for n, f in own:
            if keep and not os.path.exists(f):
                shutil.copy2(os.path.join(keep, n), f)
                log('  put back its own %s' % n)
    except Exception as e:
        log('  (its own pictures couldn’t be put back: %s)' % e)


def fold(t):   # a title compared: letters and numbers only, accents plain, "&" as "and", numbers to twenty as words, no leading "the"
    import unicodedata
    NUM = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split()
    t = unicodedata.normalize('NFKD', (t or '').replace('&', ' and ')).encode('ascii', 'ignore').decode().lower()
    t = re.sub(r'\b(\d{1,2})\b', lambda m: NUM[int(m.group(1))] if int(m.group(1)) <= 20 else m.group(1), t)
    t = re.sub(r'[^a-z0-9]', '', t)
    return t[3:] if t.startswith('the') and len(t) > 6 else t


def auto_match(g):
    """Films the media server has taken for another film put right by themselves (John, 16 Nov 2026: "you can fix these and they should
    be fixed automatically" ... "you should be able to match better by using a tmdb check"). A film looks wrong when its folder's title
    or year plainly differ from its match, or (with John's TMDB key) when its own length is far from its match's length on TMDB. Then
    TMDB is searched for the folder's title (its year, or a year either side) and each film of that title checked for its length: the
    one within 8 minutes of the file, and only that one, is put on it. Without the key: only an exact title and year that TMDB has
    once. Never a film John renamed himself, a set of films in one folder, or a version kept on purpose ({edition-…}). Each folder at
    most once a week."""
    import difflib
    VID = ('.mp4', '.mkv', '.m4v', '.mov', '.avi', '.ts', '.wmv', '.mpg', '.mpeg', '.webm')
    tried_at = os.path.expanduser('~/Grifflix/match-tried.json')
    tried = load(tried_at, {})
    lid = (g.st.get('libraries') or {}).get('movies')
    if not lid:
        return
    items = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie',
                                                               'Fields': 'Path,ProductionYear,ProviderIds,LockedFields,MediaSources'}).get('Items') or []
    keyed = bool(g.st.get('tmdb_key'))
    fixed, unsure = [], []
    for it in items:
        p = it.get('Path') or ''
        folder = os.path.dirname(p)
        fname = os.path.basename(folder)
        m = re.match(r'^(.*?)\s*\((\d{4})\)\s*$', fname)
        if not m or '{edition-' in p.lower() or 'Name' in (it.get('LockedFields') or []) or it.get('LockData'):
            continue
        try:
            if sum(1 for n in os.listdir(folder) if n.lower().endswith(VID) and not n.startswith('.')) != 1:   # (one film in its own folder only)
                continue
        except OSError:
            continue
        title, year = m.group(1).strip(), int(m.group(2))
        ms = (it.get('MediaSources') or [{}])[0]
        mins = round((ms.get('RunTimeTicks') or it.get('RunTimeTicks') or 0) / 6e8)
        cur = (it.get('ProviderIds') or {}).get('Tmdb')
        same_name = difflib.SequenceMatcher(None, fold(title), fold(it.get('Name'))).ratio() >= .9
        same_year = not it.get('ProductionYear') or abs(int(it['ProductionYear']) - year) <= 1
        wrong_len = False
        if keyed and cur and mins > 10:
            try:
                rt = tmdb_runtime(g, cur)['rt']
                wrong_len = rt > 10 and abs(rt - mins) > 15   # (its match runs a quarter of an hour longer or shorter than the film itself)
            except Exception:
                pass
        if same_name and same_year and not wrong_len:
            continue
        if time.time() - tried.get(fname, 0) < 7 * 86400:
            continue
        tried[fname] = time.time()
        try:
            pick = None
            if keyed:   # (TMDB itself: every film of the folder's title, this year or either side, by its length)
                cands = {}
                for y in (year, year - 1, year + 1):
                    for x in (tmdb(g, '/search/movie', query=title, primary_release_year=y) or {}).get('results') or []:
                        if fold(title) in (fold(x.get('title')), fold(x.get('original_title'))):
                            cands[x['id']] = x
                close = []
                for tid, x in cands.items():
                    rt = tmdb_runtime(g, tid)['rt']
                    if mins and rt and abs(rt - mins) <= 8:
                        close.append((abs(rt - mins), tid, x))
                    elif not mins and str(year) == (x.get('release_date') or '')[:4]:
                        close.append((0, tid, x))
                close.sort(key=lambda c: c[0])
                if len(close) == 1 or (len(close) > 1 and close[1][0] - close[0][0] >= 5 and len([c for c in close if c[0] <= 3]) == 1):
                    tid, x = close[0][1], close[0][2]
                    pick = {'Name': x.get('title'), 'ProductionYear': int((x.get('release_date') or '0')[:4] or 0) or None,
                            'ProviderIds': {'Tmdb': str(tid)}, 'SearchProviderName': 'TheMovieDb'}
                elif cands:
                    unsure.append('%s (%d on TMDB, none or several of its length)' % (fname, len(cands)))
            else:   # (the media server's search: an exact title and year it has once)
                res = g.jf.call('POST', '/Items/RemoteSearch/Movie', {'SearchInfo': {'Name': title, 'Year': year}, 'ItemId': it['Id'], 'IncludeDisabledProviders': False}) or []
                hits = [x for x in res if x.get('ProductionYear') == year and fold(x.get('Name')) == fold(title) and (x.get('ProviderIds') or {}).get('Tmdb')]
                if len(hits) == 1:
                    pick = hits[0]
            if not pick or str((pick.get('ProviderIds') or {}).get('Tmdb')) == str(cur):
                continue
            apply_match(g, it['Id'], pick)
            fixed.append('%s: was %s (%s), now %s (%s)' % (fname, it.get('Name'), it.get('ProductionYear') or '?', pick.get('Name'), pick.get('ProductionYear') or '?'))
        except Exception as e:
            log('Matching again %s: %s' % (fname, e))
    save(tried_at, tried)
    if TMDBC.pop('_dirty', None):
        save(os.path.expanduser('~/Grifflix/tmdb-runtimes.json'), TMDBC)
    if fixed:
        log('Put right by their folders (%d): %s' % (len(fixed), '; '.join(fixed)[:1500]))
        g.forget()
        g.publish_soon = time.time() + 300
    if unsure:
        log('Not sure which film (left as they are, Fix match on its page): %s' % '; '.join(unsure)[:800])


def tmdb_once():
    """Once: every row of John's Criterion sheet given its TMDB link at once (John, 17 Nov 2026: "can we run as a job now"); after that only
    a new Criterion film's row, as it's added (covers_now, criterion-covers.py)."""
    home = os.path.expanduser('~/Grifflix')
    done = os.path.join(home, 'criterion-tmdb-retry.done')   # (again once, with the wider matching, for the 191 not found the first time)
    if os.path.exists(done) or not os.path.exists(os.path.join(home, 'criterion.json')):
        return
    time.sleep(90)
    try:
        import subprocess
        r = subprocess.run([sys.executable, '-u', os.path.join(home, 'criterion.py'), 'tmdb', 'go', 'retry'], capture_output=True, text=True, timeout=4 * 3600, cwd=home, stdin=subprocess.DEVNULL)
        out = (r.stdout or '') + (r.stderr or '')
        with open(os.path.join(home, 'criterion-sheet.log'), 'w') as f:
            f.write(out)
        log('Criterion sheet, every TMDB link: %s' % ' '.join(l.strip() for l in out.strip().splitlines()[-4:])[:400])
        if r.returncode == 0 and ('Added' in out or 'TMDB links: 0 to add' in out):
            open(done, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    except Exception as e:
        log('Criterion sheet, every TMDB link:', e)


def sheet_now():
    """John's Criterion sheet kept up to date by itself, every six hours: which films are in Grifflix and which have extras in "Other"
    (John, 16 Nov 2026: "the criterion collection google sheet should be updating the section when it's on Grifflix, and the Extras if
    it's got Extras in Others"). Only once criterion.py has been signed in to Google; quiet unless it changed something."""
    home = os.path.expanduser('~/Grifflix')
    cr = os.path.join(home, 'criterion.py')
    if not os.path.exists(cr) or not os.path.exists(os.path.join(home, 'criterion.json')) or not os.path.isdir(os.path.join(os.environ.get('GRIFFLIX_MEDIA') or '/Volumes/G-Drive/Grifflix', 'Movies')):
        return
    try:
        import subprocess
        r = subprocess.run([sys.executable, '-u', cr, 'go'], capture_output=True, text=True, timeout=3600, cwd=home, stdin=subprocess.DEVNULL)
        out = (r.stdout or '') + (r.stderr or '')
        with open(os.path.join(home, 'criterion-sheet.log'), 'w') as f:
            f.write(out)
        if 'Marked ' in out or 'Added ' in out or r.returncode:
            log('Criterion sheet: %s' % ' '.join(l.strip() for l in out.strip().splitlines()[-12:])[:700])
    except Exception as e:
        log('Criterion sheet: %s' % e)


def covers_now():
    """A film just come in: if it's a Criterion film, everything a Criterion film has, from the start (John, 16 Nov 2026: "a new film - the
    secret agent wasn't added to the criterion collection automatically"): its cover, its spine number and its place in The Criterion
    Collection, by criterion-covers.py with the list John last gave (its covers mostly here already, so quick). Without that list, its
    cover only (poster-add.py). Not while John's own run of it is going. Quiet unless it changed something."""
    home = os.path.expanduser('~/Grifflix')
    cc, lst, pa = os.path.join(home, 'criterion-covers.py'), os.path.join(home, 'criterion-list.json'), os.path.join(home, 'poster-add.py')
    base = os.path.join(os.environ.get('GRIFFLIX_MEDIA') or '/Volumes/G-Drive/Grifflix', 'Criterion covers')
    if not os.path.isdir(base) or (CRIT.get('p') and CRIT['p'].poll() is None):
        return
    if not COVERS_LOCK.acquire(blocking=False):   # (one at a time)
        return
    try:
        import subprocess
        if os.path.exists(cc) and os.path.exists(lst):
            r = subprocess.run([sys.executable, '-u', cc, 'go'], capture_output=True, text=True, timeout=3600, cwd=home)
            out = r.stdout or ''
            with open(os.path.join(home, 'criterion-auto.log'), 'w') as f:
                f.write(out)
            put = [l.strip() for l in out.splitlines() if ' → ' in l and 'already there' not in l]
            m = re.search(r'The Criterion Collection: (\d+) film\(s\) to put in\n((?:  .*\n)*)', out)
            col = [x.strip() for x in m.group(2).splitlines()] if m and int(m.group(1)) else []
            if col or put:
                try:   # (its synopsis and Criterion's genres, if read already)
                    if G_REF:
                        give_synopses(G_REF[0])
                        give_genres(G_REF[0])
                        give_countries(G_REF[0])
                except Exception as e:
                    log('Criterion synopses:', e)
            if put or col:
                log('Criterion, for what just came in: %s%s' % ('covers: ' + '; '.join(put)[:400] if put else '', ('; in The Criterion Collection: ' + ', '.join(col)[:300]) if col else ''))
        elif os.path.exists(pa):
            r = subprocess.run([sys.executable, pa, 'drive', 'nofetch', 'go'], capture_output=True, text=True, timeout=1800)
            put = [l.strip() for l in (r.stdout or '').splitlines() if ' → ' in l]
            if put:
                log('Criterion covers put on: %s' % '; '.join(put)[:600])
    except Exception as e:
        log('Criterion, for what just came in: %s' % e)
    finally:
        COVERS_LOCK.release()


def rename_to(g, iid, name, year, pick=None):
    """A film's (or series') folder and files renamed to its right name, "Title (Year)", after Fix match (John, 15 Nov 2026: "I want the
    folder names corrected too"). The media server then has it as a new item: once it does, calendars, hearts, lists, where people
    stopped, its collections and its date added follow it, and the match is put on it again. Only a folder that is the film's own
    (no other film in it); never over a folder already there. Returns what it did, in words."""
    VIDS = ('.mp4', '.mkv', '.m4v', '.mov', '.avi', '.ts', '.wmv', '.mpg', '.mpeg', '.webm')
    it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path,DateCreated'}) or {}
    kind, path = it.get('Type'), it.get('Path') or ''
    if kind not in ('Movie', 'Series') or not path or not os.path.exists(path):
        return 'its files weren’t found, so they keep their names'
    new = re.sub(r'[/:\\]', '-', '%s (%s)' % (name, year) if year else name).strip(' .')
    folder = path if kind == 'Series' else os.path.dirname(path)
    libs = [os.path.realpath(l).rstrip('/') for v in g.jf.call('GET', '/Library/VirtualFolders') or [] for l in v.get('Locations') or []]
    if os.path.realpath(folder).rstrip('/') in libs:
        return 'it isn’t in a folder of its own, so its files keep their names'
    if kind == 'Movie' and any(n.lower().endswith(VIDS) and os.path.join(folder, n) != path and 'sample' not in n.lower() for n in os.listdir(folder)):
        return 'its folder has other films in it, so it keeps its name'
    oldname = os.path.basename(folder.rstrip('/'))
    target = os.path.join(os.path.dirname(folder.rstrip('/')), new)
    if oldname == new:
        return ''
    if os.path.exists(target):
        return 'a folder called “%s” is there already, so its folder keeps its name' % new
    # what's there now, by path: the film, or the series and its seasons and episodes (their ids change with their paths)
    olds = {os.path.realpath(path): iid}
    if kind == 'Series':
        r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': iid, 'Recursive': 'true', 'IncludeItemTypes': 'Season,Episode', 'Fields': 'Path'}) or {}
        for x in r.get('Items') or []:
            if x.get('Path'):
                olds[os.path.realpath(x['Path'])] = x['Id']
    sets = []
    try:   # (the collections it's in: put back in them once it's a new item)
        for bs in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'BoxSet'}) or {}).get('Items') or []:
            kids = (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': bs['Id']}) or {}).get('Items') or []
            if any(k.get('Id') == iid for k in kids):
                sets.append(bs['Id'])
    except Exception:
        pass
    # the files inside named after the old folder ("Old (2020).mkv", "Old (2020) S01E02.mkv", "Old (2020).en.srt") take the new name
    moved = {}
    real_folder = os.path.realpath(folder)
    for d, _, files in os.walk(real_folder):
        for f in files:
            if f.startswith(oldname):
                os.rename(os.path.join(d, f), os.path.join(d, new + f[len(oldname):]))
                moved[os.path.join(d, f)] = os.path.join(d, new + f[len(oldname):])
    real_target = os.path.join(os.path.dirname(real_folder), new)
    os.rename(real_folder, real_target)
    want = {}
    for p_, i_ in olds.items():
        p2 = moved.get(p_, p_)
        want[real_target + p2[len(real_folder):] if p2.startswith(real_folder) else p2] = i_
    log('Renamed by John’s Fix match: %s → %s' % (oldname, new))
    try:
        g.jf.call('POST', '/Library/Refresh')
    except Exception:
        pass

    def follow():   # (once the media server has the new paths: everything moved over to the new ids, the match put on again)
        for _ in range(40):
            time.sleep(30)
            try:
                found = {}
                for typ in (('Series,Season,Episode',) if kind == 'Series' else ('Movie',)):
                    r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': typ, 'Fields': 'Path'}) or {}
                    for x in r.get('Items') or []:
                        rp = os.path.realpath(x.get('Path') or '')
                        if rp in want:
                            found[want[rp]] = x['Id']
                top = found.get(iid)
                if not top:
                    continue
                m = {o: n for o, n in found.items() if o != n}
                if pick:
                    try:
                        g.jf.call('POST', '/Items/RemoteSearch/Apply/%s' % top, pick, {'replaceAllImages': 'true'})
                    except Exception:
                        pass
                for b_ in sets:
                    try:
                        g.jf.call('POST', '/Collections/%s/Items' % b_, query={'Ids': top})
                    except Exception:
                        pass
                if it.get('DateCreated'):   # (not shown among the new films)
                    try:
                        full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, top)) or {}
                        full['DateCreated'] = it['DateCreated']
                        g.jf.call('POST', '/Items/%s' % top, full)
                    except Exception:
                        pass
                n = g.remap(m) if m else 0
                log('  %s: the media server has it under its new name; calendars, lists, hearts and places followed it (%d changed)' % (new, n))
                g.publish_soon = time.time() + 120
                return
            except Exception as e:
                log('  following the rename of %s: %s' % (new, e))
        log('  %s: the media server didn’t show its new name within 20 minutes; calendars and lists not moved' % new)
    threading.Thread(target=follow, daemon=True).start()
    return 'renamed to “%s”' % new


FETCH_SEEN = {}   # (the download in progress: how much had come when, for its speed now and whether it has stalled)


def keep_fetch_unstuck():
    """A download from TorBox or Google Drive that has taken in nothing for 15 minutes: its rclone stopped, so the fetch counts it as failed,
    goes on to the rest, and tries it again next time (John, 17 Nov 2026: House Party "stuck on 75% for a while", rclone hung since 17:35
    with 13 more behind it; gdrive.py now watches its own downloads too, but one started by an older version doesn't)."""
    import subprocess, signal as sig
    seen = {}
    while True:
        time.sleep(300)
        try:
            st = load(os.path.expanduser('~/Grifflix/fetch-status.json'), {}) or {}
            part = ((st.get('now') or {}).get('part')) or ''
            if not part:
                seen.clear()
                continue
            n = 0
            for d_ in (part, part + '.d'):
                for root_, _, files in (os.walk(d_) if os.path.isdir(d_) else [(os.path.dirname(d_), [], [os.path.basename(d_)])] if os.path.exists(d_) else []):
                    for f_ in files:
                        try:
                            x = os.stat(os.path.join(root_, f_))
                            n += x.st_size
                        except OSError:
                            pass
            try:
                with open(part + '.rlog', 'rb') as rf_:
                    rf_.seek(0, 2); rf_.seek(max(0, rf_.tell() - 16384))
                    for line_ in reversed(rf_.read().decode('utf-8', 'replace').splitlines()):
                        if '"stats"' in line_:
                            n = int(json.loads(line_)['stats'].get('bytes') or 0)
                            break
            except (OSError, ValueError, KeyError, TypeError):
                pass
            if seen.get('part') != part or seen.get('n') != n:
                seen.update(part=part, n=n, at=time.time())
                continue
            if time.time() - seen['at'] < 900:
                continue
            ps = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=10).stdout
            hung = [int(l.split()[0]) for l in ps.splitlines() if l.strip() and 'rclone' in l and ' copy ' in l and os.path.basename(part) in l]
            for p_ in hung:
                os.kill(p_, sig.SIGTERM)
            if hung:
                log('Downloads: nothing had come for %d minutes (%s, %.1f GB so far): stopped, to be tried again; the rest go on' % ((time.time() - seen['at']) // 60, os.path.basename(part).replace('.adding', ''), n / 1e9))
            seen.clear()
        except Exception as e:
            log('Downloads: couldn’t look for a stuck download:', str(e)[:200])


def keep_gdrive():
    """Films from the Grifflix folder of John's Google Drive, every 15 minutes, once gdrive.py is set up (its sign-in kept): onto the
    G-Drive, then deleted from Google Drive; keep_scanning sees them next and the site follows (John, 15 Nov 2026)."""
    script, conf = os.path.expanduser('~/Grifflix/gdrive.py'), os.path.expanduser('~/Grifflix/rclone.conf')
    # (a fetch started by an older version of gdrive.py goes on with that version's ways to the end of its list: Ghosts' episodes
    # filed as films after an update (John, 15 Nov 2026). Stopped, with its download, and done afresh by this version. Only one
    # whose lock says it's an older version (or says nothing, as the oldest did): never one this version started.)
    try:
        import subprocess, signal as sig
        lk = os.path.expanduser('~/Grifflix/gdrive.lock')
        parts = open(lk).read().split() if os.path.exists(lk) else []
        pid = int(parts[0]) if parts and parts[0].isdigit() else 0
        ver = parts[1] if len(parts) > 1 else ''
        mine = str(int(os.path.getmtime(script))) if os.path.exists(script) else ''
        ps = subprocess.run(['ps', '-axo', 'pid=,ppid=,command='], capture_output=True, text=True, timeout=10).stdout
        rows = [(int(l.split()[0]), int(l.split()[1]), l) for l in ps.splitlines() if l.strip()]
        runs = [p_ for p_, _, l in rows if 'gdrive.py' in l and ' run' in l]
        if not os.path.exists(lk) or (ver and ver == mine):
            old = []   # (nothing running, or this version's own)
        elif pid and pid in runs:
            old = []   # (an older version's: it takes up the new version itself between films, so its download in progress isn't lost)
        else:
            old = runs   # (the oldest kind of lock, with no number in it: any fetch running is that one)
        for p_ in old:
            for c, pp, l in rows:
                if pp == p_ and 'rclone' in l:   # (its download: stopped, and its half-made file removed)
                    os.kill(c, sig.SIGTERM)
                    m = re.search(r' (/.*?\.adding)(?:\.d)?(?: |$)', l)
                    if m:
                        time.sleep(2)
                        import shutil
                        shutil.rmtree(m.group(1) + '.d', ignore_errors=True)
                        try:
                            os.remove(m.group(1))
                        except OSError:
                            pass
            os.kill(p_, sig.SIGTERM)
            log('Google Drive: stopped a fetch by an older version, to do it again with this one')
        if old and os.path.exists(lk):
            os.remove(lk)
    except Exception as e:
        log('Google Drive: couldn’t look for an older fetch:', e)
    time.sleep(60)
    while True:
        try:
            if os.path.exists(script) and os.path.exists(conf):
                import subprocess
                # (in a session of its own, its output to a file: a restart of the gateway (its updates) no longer stops a download part way, as
                # it did every time, The Blade six times: John, 17 Nov 2026. If the gateway restarts, the fetch carries on and the new one waits for it)
                outp = os.path.expanduser('~/Grifflix/gdrive.out')
                with open(outp, 'w') as of_:
                    pr_ = subprocess.Popen([sys.executable, script, 'run'], stdout=of_, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, text=True)
                    try:
                        pr_.wait(timeout=6 * 3600)
                    except subprocess.TimeoutExpired:
                        pass
                out_ = open(outp, encoding='utf-8', errors='replace').read()
                r = type('R', (), {'stdout': out_, 'stderr': out_ if pr_.returncode else '', 'returncode': pr_.returncode or 0})()
                if load(os.path.expanduser('~/Grifflix/convert-new.json'), []):
                    CONV_NOW.set()
                for line in (r.stdout or '').splitlines():
                    if 'film(s) fetched' in line or 'film(s) from Google Drive' in line or 'failed' in line or 'Not enough room' in line:
                        log('Google Drive:', line[17:])
                GDRIVE_ERR[0] = ((r.stderr or r.stdout or '').strip()[-300:] or 'it stopped') if r.returncode else ''
                if r.returncode:
                    log('Google Drive: didn’t work:', (r.stderr or '').strip()[-300:])
        except Exception as e:
            log('Google Drive:', e)
            GDRIVE_ERR[0] = str(e)[-300:]
        GDRIVE_NEXT[0] = time.time() + 900
        GDRIVE_NOW.wait(900)
        GDRIVE_NOW.clear()


def mac_update(quiet=False):
    """The Mac's Grifflix files brought up to date from GitHub: the admin page's Update everything, and by itself (keep_updating)."""
    def fetch(url):
        req = urllib.request.Request(url, headers={'User-Agent': 'Grifflix gateway', 'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=60, context=tls()) as r:
            return r.read()
    try:
        try:
            sha = json.loads(fetch('https://api.github.com/repos/%s/commits/main' % REPO))['sha']
        except Exception:   # (GitHub's API refuses a home connection that has asked too often: the latest commit from its feed instead)
            feed = fetch('https://github.com/%s/commits/main.atom' % REPO).decode('utf-8', 'replace')
            sha = re.search(r'Commit/([0-9a-f]{40})', feed).group(1)
        got = {}
        for name, dest, mark in SOFTWARE:
            src = fetch('https://raw.githubusercontent.com/%s/%s/_grifflix/mac/%s' % (REPO, sha, name)).decode('utf-8')
            compile(src, name, 'exec')   # (a file that isn't whole Python stops it all)
            if mark and mark not in src:
                raise ValueError('%s isn’t what it should be' % name)
            got[name] = src
    except Exception as e:
        log('Update the Mac: couldn’t fetch:', repr(e))
        return {'status': 502, 'error': 'Couldn’t get the new files from GitHub (%s). Nothing was changed.' % str(e)[:120], 'at': int(time.time())}
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
        if old is None and name not in ('grifflix-gateway.py', 'convert.py', 'subtitles.py', 'poster-add.py', 'criterion.py', 'criterion-covers.py', 'duplicates.py', 'gdrive.py'):
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
    if 'convert.py' in changed:   # the converter running: it takes up its new file itself, between films (stopping it part way
        # through a film started that film again from 0%, its ffmpeg left running: Moonrise, John, 15 Nov 2026); one too old to
        # do that is started again as before
        cur = load(os.path.expanduser('~/Grifflix/convert-now.json'), None) or {}
        try:
            pid = conv_alive(cur)   # (only the converter itself: never another program with its old number)
            # (or one paused, holding a film back for a download, that would otherwise wait hours to take its update: John, 16 Nov 2026)
            if pid and (not cur.get('selfupdate') or (cur.get('state') == 'paused' and 'download' in str(cur.get('why') or ''))):
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
    if changed or not quiet:
        log('Update the Mac%s: %s (%s)%s' % (' (by itself)' if quiet else '', sha[:7], ', '.join(changed) or 'nothing changed', '; converter started again' if restarted else ''))
    return {'status': 200, 'sha': sha, 'changed': changed, 'converter': restarted, 'at': int(time.time()),
            'restarting': 'grifflix-gateway.py' in changed or 'subtitles.py' in changed}   # (the gateway loads subtitles.py as it starts)

def someone_watching(g):
    """Someone playing a film through Grifflix in the last five minutes."""
    try:
        if time.time() - os.path.getmtime(os.path.expanduser('~/Grifflix/watching')) < 300:
            return True
    except OSError:
        pass
    with g.plays_lock:
        return any(time.time() - (x.get('seen') or 0) < 300 for x in g.plays.values())


UPDATE_NOW = threading.Event()   # (Claude's word that something's just gone onto GitHub: looked for at once)


def keep_updating(g):
    """The Mac's own files kept up to date by themselves, every 2 minutes (was 15: John, 18 Nov 2026, "Why can't we pick it up sooner
    automatically?"), and at once when Claude says it's just put something on GitHub, from what's on GitHub: John signs off only what changes the
    site's look or what it does (John, 17 Nov 2026: "Can you put mac updates live without an update from me - I only need to sign off on
    anything that changes the look or functionality of Grifflix"). The gateway itself restarts for its new version only while no one is
    watching (a film playing goes through it); the converter takes its new file between films, as ever."""
    time.sleep(180)
    while True:
        try:
            have = load(INSTALLED, {})
            fresh = set(have.get('files') or []) != set(n for n, _, _ in SOFTWARE)   # (a new script since the last update: a second pass)
            req = urllib.request.Request('https://github.com/%s/commits/main.atom' % REPO, headers={'User-Agent': 'Grifflix gateway'})
            with urllib.request.urlopen(req, timeout=60, context=tls()) as r_:
                sha = re.search(r'Commit/([0-9a-f]{40})', r_.read().decode('utf-8', 'replace')).group(1)
            if fresh or sha != have.get('sha'):
                r = mac_update(quiet=True)
                save(os.path.join(APP_DIR, 'update-result.json'), dict(r, auto=True))
                if r.get('restarting'):
                    while someone_watching(g):
                        time.sleep(60)
                    log('Update the Mac (by itself): the gateway restarting for its new version')
                    time.sleep(1.5)
                    os._exit(0)   # (launchd starts the new gateway)
        except Exception as e:
            log('Update the Mac (by itself):', str(e)[:200])
        # (GitHub's feed of the latest change: a small page, not its API, which limits how often a home connection may ask)
        UPDATE_NOW.wait(120)
        if UPDATE_NOW.is_set():
            UPDATE_NOW.clear()
            time.sleep(20)   # (GitHub's feed a moment behind a merge)


def keep_scanning(g):
    """New films noticed: every 10 minutes the film and TV folders are looked at, and when there's a new one in them the media server
    is asked to look too. Its own watching of the folders misses films put on the drive over the network (John, 15 Nov 2026: "New
    films arent getting added": The Invite, Blue Heron and Sense and Sensibility sat on the drive unseen until it was asked)."""
    seen_at = os.path.expanduser('~/Grifflix/library-seen.json')
    publish_at, matched_at = 0, time.time() - 6 * 3600 + 600   # (films put right by their folders: 10 minutes after starting, then every 6 hours)
    time.sleep(120)
    while True:
        if time.time() - matched_at > 6 * 3600:
            matched_at = time.time()
            try:
                auto_match(g)
            except Exception as e:
                log('Putting films right by their folders:', e)
            if not (SYN.get('t') and SYN['t'].is_alive()):   # (any synopses not given yet, as after a restart part way)
                try:
                    give_synopses(g)
                except Exception as e:
                    log('Criterion synopses:', e)
        try:
            roots = [l for v in g.jf.call('GET', '/Library/VirtualFolders') or [] if (v.get('CollectionType') or '') in ('movies', 'tvshows')
                     for l in v.get('Locations') or []]
            # (every film and episode file, not only the folders at the top: a new episode in a series already there, or a film arriving in
            # a folder made earlier, by a download that failed, was never noticed: John, 15 Nov 2026, Ghosts "not even in the queue")
            VID = ('.mp4', '.mkv', '.m4v', '.mov', '.avi', '.ts', '.wmv', '.mpg', '.mpeg', '.webm')
            now_ = sorted(os.path.join(d, n) for r in roots if os.path.isdir(r) for d, ds, fs in os.walk(r)
                          for n in fs + ([] if d != r else ds) if not n.startswith('.') and (d == r or n.lower().endswith(VID)))
            before = load(seen_at, None)
            if before is not None:
                new = [x for x in now_ if x not in set(before)]
                gone = [x for x in before if x not in set(now_)]
                if gone and not new:
                    log('Gone from the library (%d): %s' % (len(gone), ', '.join(os.path.basename(x) for x in gone[:5])))
                    publish_at = publish_at or time.time() + 300
                if new:
                    g.jf.call('POST', '/Library/Refresh')
                    log('New in the library (%d): %s: the media server asked to look' % (len(new), ', '.join(os.path.basename(x) for x in new[:5]) + (' …' if len(new) > 5 else '')))
                    CONV_NOW.set()   # (the converter started for them, if it isn't running)
                    # (a Criterion film among them: its cover, spine and place in The Criterion Collection, from the list John last gave, once
                    # the media server has it; however it came in, not only by download: John, 16 Nov 2026, "I shouldn't have to reupload the
                    # criterion html every time to check if a new film is criterion")
                    threading.Timer(240, covers_now).start()
                    # (an extra put in a film's Other or Extras folder: the Criterion sheet's Extras column marked: John, 17 Nov 2026, "the job
                    # should run when extras are added")
                    XD = ('other', 'others', 'extras', 'extra', 'featurettes', 'behind the scenes', 'deleted scenes', 'interviews', 'trailers', 'shorts', 'scenes', 'bonus')
                    if any(seg.strip().lower() in XD for x in new for seg in x.split('/')[:-1]):
                        threading.Timer(300, sheet_now).start()
                    publish_at = time.time() + 540   # (the site updated once the media server has read them: about 10 minutes on)
            if getattr(g, 'publish_soon', 0) and not publish_at:   # (John put a film's details right: the site follows)
                publish_at, g.publish_soon = g.publish_soon, 0
            if publish_at and time.time() >= publish_at:
                publish_at = 0
                if g.st.get('worker_token'):
                    try:
                        c = site_update(g, 'new films')
                        log('The site updated by itself: %d new, %d changed, %d gone' % (c.get('new', 0), c.get('changed', 0), c.get('removed', 0)))
                    except Exception as e:
                        log('The site’s automatic update didn’t work:', e)
                        g.st['auto_update_last'] = {'at': int(time.time()), 'error': str(e)[-200:]}   # (shown on the admin page, not only in the log)
                        save(SETTINGS, g.st)
                        publish_at = time.time() + 900   # (tried again in a quarter of an hour)
            if now_ or before is None:
                save(seen_at, now_)
            # (the Bin: what John deleted, kept 30 days in case it was a mistake, then gone for good)
            for r in roots:
                bin_ = os.path.join(os.path.dirname(os.path.dirname(r.rstrip('/'))), 'Grifflix Bin')
                if os.path.isdir(bin_):
                    import shutil
                    for n in os.listdir(bin_):
                        m = re.match(r'^(\d{4}-\d\d-\d\d) ', n)
                        if m and time.time() - time.mktime(time.strptime(m.group(1), '%Y-%m-%d')) > 30 * 86400:
                            p_ = os.path.join(bin_, n)
                            shutil.rmtree(p_) if os.path.isdir(p_) else os.remove(p_)
                            log('Grifflix Bin: %s gone for good (30 days)' % n)
        except Exception as e:
            log('Looking for new films:', e)
        SCAN_NOW.wait(600)
        SCAN_NOW.clear()


def keep_converting():
    """The converter kept going: started again if it isn't running (after the Mac restarts, or if the restart after Update everything
    didn't take), unless it finished everything in the last 12 hours; any time the Mac isn't in use, pausing while anyone watches
    (John, 12 Nov 2026: "it said nothing was being converted right now, which is odd")."""
    home = os.path.expanduser('~/Grifflix')
    time.sleep(180)   # (not straight after starting: an Update starts the converter itself)
    while True:
        try:
            script = os.path.join(home, 'convert.py')
            cur = load(os.path.join(home, 'convert-now.json'), None) or {}
            alive = bool(conv_alive(cur))
            recently_done = cur.get('state') == 'finished' and time.time() - (cur.get('at') or 0) < 12 * 3600 and not load(os.path.join(home, 'convert-new.json'), [])   # (films just brought in: started for them)
            if os.path.exists(script) and not alive and not recently_done:
                import subprocess
                running = 'convert.py' in subprocess.run(['ps', '-axo', 'command'], capture_output=True, text=True, timeout=10).stdout
                if not running:
                    args = ['go', 'now']   # (any time it isn't in use: John, 12 Nov 2026, "That 2am 8am rule was changed to any time not in use")
                    awake = ['/usr/bin/caffeinate', '-i'] if os.path.exists('/usr/bin/caffeinate') else []
                    out = open(os.path.join(home, 'convert.out'), 'a')
                    subprocess.Popen(awake + [sys.executable, script] + args, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                                     start_new_session=True, cwd=home)
                    log('The converter wasn’t running: started again (%s)' % ' '.join(args))
        except Exception as e:
            log('Keeping the converter going:', e)
        CONV_NOW.wait(600)   # (or at once, when a film has just been brought in)
        CONV_NOW.clear()


def serve():
    g = Gateway()
    G_REF.append(g)   # (for what runs on its own: covers_now's synopses)
    port = int(g.st.get('port', 8097))
    srv = ThreadingHTTPServer(('127.0.0.1', port), make_handler(g))
    # (the jobs only once this is the gateway, its port taken: a start that fails, as one does while the old gateway still holds the
    # port during an update, starts nothing. Each such start stopped the downloads: John, 15 Nov 2026, every one "went missing")
    threading.Thread(target=keep_converting, daemon=True).start()
    threading.Thread(target=keep_scanning, args=(g,), daemon=True).start()
    threading.Thread(target=keep_updating, args=(g,), daemon=True).start()
    threading.Thread(target=tmdb_once, daemon=True).start()   # (the Criterion sheet's TMDB links, once)
    def covers_once():   # (once, after the covers learnt to allow a year out: Return to Reason's cover put on: John, 17 Nov 2026)
        mk = os.path.expanduser('~/Grifflix/covers-yearout.done')
        if os.path.exists(mk):
            return
        time.sleep(300)
        covers_now()
        open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    threading.Thread(target=covers_once, daemon=True).start()   # (the Mac's own files kept up to date by themselves: John, 17 Nov 2026)
    threading.Thread(target=fanny_series, args=(g,), daemon=True).start()
    threading.Thread(target=keep_disk_health, daemon=True).start()
    threading.Thread(target=keep_backup, args=(g,), daemon=True).start()
    threading.Thread(target=keep_plex_off, args=(g,), daemon=True).start()
    threading.Thread(target=keep_extras, args=(g,), daemon=True).start()
    try:   # (the spines set by hand, where criterion.py, for the Google sheet, reads them too)
        f_ = os.path.expanduser('~/Grifflix/criterion-spines-own.json')
        own_ = load(f_, {}) or {}
        if any(k not in own_ for k in SPINE_SEED):
            save(f_, dict(SPINE_SEED, **own_))
    except Exception:
        pass
    def countries_once():   # (once: every Criterion film given Criterion's country now, then each as it comes in)
        mk = os.path.expanduser('~/Grifflix/criterion-countries.done')
        if os.path.exists(mk):
            return
        time.sleep(240)
        give_countries(g)
        open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    threading.Thread(target=countries_once, daemon=True).start()
    try:   # (once: the film lost before the fix in /macagent/next, put back first)
        mk = os.path.expanduser('~/Grifflix/macq-lost3.done')   # (again, after the planning bug of 18 Nov 2026 passed it over, and the test run held it up)
        if not os.path.exists(mk):
            DD = '31d5f95bc1b179690baa01d3c10776cf'
            Q = load(MACQ, []) or []
            if not any(x.get('id') == DD for x in Q):
                Q.insert(0, {'id': DD, 'name': 'Disclosure Day', 'at': int(time.time()), 'force': True})
                save(MACQ, Q)
            for j in CONVJOBS.values():
                if j.get('id') == DD and j.get('state') == 'waiting':
                    j['state'], j['ended'] = 'lost', time.time()
            convjobs_save()
            open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
            log('Queue for John’s Mac: Disclosure Day, lost earlier, back at the front')
    except Exception as e:
        log('Queue for John’s Mac:', e)
    threading.Thread(target=keep_gdrive, daemon=True).start()
    threading.Thread(target=keep_fetch_unstuck, daemon=True).start()
    srv.daemon_threads = True
    try:   # (an update that restarted the gateway: said to be over, now it's back, so the admin page knows: John, 17 Nov 2026,
        # "The Mac hasn't finished updating yet" when it had)
        ur = os.path.join(APP_DIR, 'update-result.json'); r0 = load(ur, None)
        if r0 and r0.get('restarting'):
            save(ur, dict(r0, restarting=False, back=int(time.time())))
    except Exception:
        pass
    log('Grifflix gateway on 127.0.0.1:%d, Jellyfin at %s' % (port, g.st['jellyfin']))
    srv.serve_forever()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'setup':
        setup(sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == 'serve':
        serve()
    elif len(sys.argv) > 1 and sys.argv[1] == 'backup':
        backup_main()
    else:
        print(__doc__)
