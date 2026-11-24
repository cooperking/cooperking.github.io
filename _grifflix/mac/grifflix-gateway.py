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
ORIGINS = ['https://cooperking.github.io', 'https://grifflix.co.uk', 'https://admin.grifflix.co.uk']   # (Grifflix on its own address too: John, 9 Nov 2026)
KEY_HOURS = 12            # how long a picture or video link works
ME_CACHE_S = 600          # how long a member check with the Worker is trusted
VERSION = '1'

FIELDS = ('Overview,Genres,OfficialRating,CommunityRating,ProductionYear,PremiereDate,RunTimeTicks,ImageTags,'
          'BackdropImageTags,ParentBackdropImageTags,SeriesPrimaryImageTag,ChildCount,RecursiveItemCount,MediaSources,'
          'People,Taglines,Studios,ProductionLocations,DateCreated,Width,Height,RemoteTrailers,Path')
# lists (rows, Browse, collections, calendars) need only this much: the old Mac answers far faster without each film's
# files and cast (a film's own pop-up asks for everything)
LIST_FIELDS = ('Overview,Genres,OfficialRating,CommunityRating,ProductionYear,PremiereDate,RunTimeTicks,ImageTags,'
               'BackdropImageTags,ParentBackdropImageTags,SeriesPrimaryImageTag,ChildCount,RecursiveItemCount,DateCreated,Path')
EXPORT_CACHE = os.path.join(APP_DIR, 'export-cache.json')
# what John has said goes inside which collection, each as ONE entry: box sets (several short films shown as one) and
# series. Put right at the start of every Update, so it's always so, whatever was run before (John, 8 Nov 2026: Frampton
# "should have been done, as it's been there from the start"). A box set inside another collection isn't listed on its own.
ARRANGE = {'The Criterion Collection': {
    'sets': [],   # (the box sets once kept here as collections, taken apart by John's Allow, as Criterion's other box sets are: 18 Nov 2026)
    'series': ['The Underground Railroad', 'Tanner 88', 'Fishing with John']}}
# Collections for John alone: on his own page and his admin page, never in the site's files, to members or to visitors (John, 19 Nov
# 2026, "put everything not listed on the website in a new collection A24 CHECKS, keep it hidden apart from to me")
OWNER_ONLY_SETS = ('a24 checks', 'to check')   # (and To check: films whose file is in doubt, John's alone: John, 23 Nov 2026, "can you put ones you doubt in a to check collection")


def owner_only_set(name):
    return (name or '').strip().lower() in OWNER_ONLY_SETS


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


def record(what, **kw):
    """Kept for good, one line each, in ~/Grifflix/record.jsonl: every change made to John's films, subtitles and their details, what
    it was and why (John, 22 Nov 2026, "keep a log of everything"). Never stops what it's recording."""
    try:
        with open(os.path.expanduser('~/Grifflix/record.jsonl'), 'a', encoding='utf-8') as f_:
            f_.write(json.dumps(dict({'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'what': what}, **kw), ensure_ascii=False) + '\n')
    except Exception:
        pass


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
    tmp = '%s.%d.tmp' % (path, threading.get_ident())   # (each thread its own: two saving the same file at once, the subtitles list, lost one: 22 Nov 2026)
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

    def call(self, method, path, body=None, query=None, timeout=30):
        if query:
            path += ('&' if '?' in path else '?') + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        c = self.conn(timeout)
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


LANGS3 = {'eng': 'English', 'fre': 'French', 'fra': 'French', 'ger': 'German', 'deu': 'German', 'ita': 'Italian', 'spa': 'Spanish', 'jpn': 'Japanese',
          'chi': 'Chinese', 'zho': 'Chinese', 'kor': 'Korean', 'rus': 'Russian', 'swe': 'Swedish', 'dan': 'Danish', 'nor': 'Norwegian', 'dut': 'Dutch',
          'nld': 'Dutch', 'por': 'Portuguese', 'pol': 'Polish', 'cze': 'Czech', 'ces': 'Czech', 'hun': 'Hungarian', 'fin': 'Finnish', 'gre': 'Greek',
          'ell': 'Greek', 'hin': 'Hindi', 'ara': 'Arabic', 'heb': 'Hebrew', 'tur': 'Turkish', 'per': 'Persian', 'fas': 'Persian', 'ice': 'Icelandic'}


def sound_list(aus):
    """A film's soundtracks for the player's Sound menu: each by its name (a commentary's own, such as "Commentary with the director") or
    language, with stereo or surround; the converter's two of the same language (stereo first, the original after) told apart that way."""
    out = []
    for a in aus:
        t = (a.get('Title') or '').strip()
        lang = LANGS3.get((a.get('Language') or '').lower(), (a.get('Language') or '').upper() if a.get('Language') not in (None, '', 'und') else '')
        com = 'comment' in t.lower()
        ch = int(a.get('Channels') or 0)
        name = t if t and t.lower() not in ('stereo', 'surround', 'mono') else (lang or 'Soundtrack %d' % (len(out) + 1))
        if com and lang and lang != 'English' and lang.lower() not in name.lower():
            name += ' (%s)' % lang
        out.append({'i': a.get('Index'), 'name': name, 'ch': '' if com else 'mono' if ch == 1 else 'stereo' if ch == 2 else '%d.1' % (ch - 1) if ch > 2 else '',
                    'com': com})
    return out


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
              'By Brakhage - An Anthology, Volume One (2003)': 184, 'By Brakhage - An Anthology, Volume Two (2010)': 517,
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
    if it.get('ProductionLocations'):   # (where it was made: an American film's AFI Catalog link, 21 Nov 2026)
        o['countries'] = it['ProductionLocations'][:4]
    if it.get('Studios'):   # (its production companies, from TMDB: John, 21 Nov 2026, "We also need production companies")
        o['studios'] = [x.get('Name') for x in it['Studios'] if x.get('Name')][:4]
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
        try:   # (and its subtitles' record, and the files kept for it (the ones tried, what was heard), under its new id)
            with SP_LOCK:
                P_ = load(SUBPREP, {}) or {}
                ch_ = False
                for o_, n_ in m.items():
                    if o_ in P_ and n_ not in P_:
                        P_[n_] = P_.pop(o_); ch_ = True
                if ch_:
                    save(SUBPREP, P_)
            for d_ in ('subprep-cands', 'subprep-evidence', 'subprep-ref'):
                dd_ = os.path.expanduser('~/Grifflix/' + d_)
                for f_ in (os.listdir(dd_) if os.path.isdir(dd_) else []):
                    if f_[:32] in m and re.match(r'^[0-9a-f]{32}', f_):
                        os.replace(os.path.join(dd_, f_), os.path.join(dd_, m[f_[:32]] + f_[32:]))
        except Exception as e:
            log('Subtitles, following new ids: %s' % e)
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
            # (and AFI's own site, for the AFI bookmark's list only: /afibm/list)
            if o and (o in g.origins or re.match(r'^http://(localhost|127\.0\.0\.1)(:\d+)?$', o) or (o == 'https://catalog.afi.com' and self.path.startswith('/afibm/'))):
                self.send_header('Access-Control-Allow-Origin', o)
                self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type, Range')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Expose-Headers', 'Content-Length, Content-Range')
                self.send_header('Access-Control-Max-Age', '86400')

        def send_json(self, status, obj):
            if getattr(self, '_subnext', False):   # (what John's Mac was given when it asked for subtitles work, or why nothing: logged when it changes)
                self._subnext = False
                try:
                    said_ = ('given: %s%s' % ((obj.get('subjob') or {}).get('name'), ' (translation)' if (obj.get('subjob') or {}).get('translate_only') else '')) if isinstance(obj, dict) and obj.get('subjob') \
                        else ('nothing: %s' % (obj.get('wait') or obj.get('error') or 'nothing to do') if isinstance(obj, dict) else str(status))
                    SUBASK.update(at=int(time.time()), said=said_)
                    if said_ != SUBASK.get('logged') or time.time() - SUBASK.get('logged_at', 0) > 1800:
                        SUBASK.update(logged=said_, logged_at=int(time.time()))
                        log('Subtitles: John’s Mac asked for work: %s' % said_)
                except Exception:
                    pass
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
            self._raw = raw   # (as sent: John's Mac's report is plain text)
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

        def do_PUT(self):   # (a film from John's Mac, a piece at a time: /macagent/upload)
            self.route('PUT')

        def route(self, method):
            self.take_body(method)
            u = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            if getattr(self.server, 'lan', False) and u.path != '/macagent/upload':   # (the home network's door: John's Mac's films only)
                return self.send_json(404, {'error': 'Not here.'})
            try:
                if u.path == '/afibm/list':
                    # (the AFI bookmark asks for the films to find and the pages to read when it's clicked, so an old bookmark never runs
                    # on the list it was made with: John, 22 Nov 2026, "the AFI bookmarklet first listed only four again". Its key opens
                    # only this list)
                    want = str(g.st.get('afibm_key') or '')
                    if not want or not hmac.compare_digest(str(q.get('k') or ''), want):
                        return self.send_json(403, {'error': 'Not allowed.'})
                    return self.api_afi({'owner': True}, None, {}, None)
                if u.path == '/macagent/upload':
                    # (a film made on John's Mac, sent here and written to the G-Drive by this Mac, as macOS on his Mac won't let its
                    # helper write to a network drive ("operation not permitted"): John, 19 Nov 2026, "Any luck on the films?")
                    tok = (self.headers.get('Authorization') or '').replace('Bearer ', '').strip()
                    want = (g.st.get('macagent') or {}).get('key')
                    if not want or not tok or not hmac.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), want):
                        return self.send_json(403, {'error': 'Not allowed.'})
                    job = CONVJOBS.get(str(q.get('job') or ''))
                    if not job or not job.get('mac'):
                        return self.send_json(404, {'error': 'No such job.'})
                    dst = job['plan']['tmp'] + '.uploading'
                    cur = os.path.getsize(dst) if os.path.exists(dst) else 0
                    if method == 'GET':
                        tmp_ = job['plan']['tmp']
                        if os.path.exists(tmp_):
                            # (a file there from before this job (the Mac mini's own half-made one, from when it converted: La Bola Negra's, 96 MB of
                            # 21 Nov) isn't this film from John's Mac: taken for its start, every piece sent was refused, and the film stayed on his
                            # Mac: John, 24 Nov 2026, "made, but couldn't copy to the G-Drive". Cleared, and the film sent from the start)
                            sz_, want_ = os.path.getsize(tmp_), int(q.get('total') or 0) if str(q.get('total') or '').isdigit() else 0
                            if job.get('got') or (want_ and sz_ == want_) or (not want_ and os.path.getmtime(tmp_) >= job['at']):
                                return self.send_json(200, {'size': sz_, 'whole': True})
                            try:
                                os.remove(tmp_)
                                log('From John’s Mac: %s: a half-made file from before (%.2f GB) where it goes, cleared' % (job['plan']['item'].get('name'), sz_ / 1e9))
                            except OSError as e:
                                return self.send_json(409, {'error': 'A file from before is where it goes, and can’t be cleared: %s' % e})
                        if cur and not job.get('up'):   # (part of a film sent for an earlier job (converted again since: other bytes) never carried on from)
                            try:
                                os.remove(dst)
                                log('From John’s Mac: %s: part of an earlier sending (%.2f GB) cleared, sent from the start' % (job['plan']['item'].get('name'), cur / 1e9))
                                cur = 0
                            except OSError as e:
                                return self.send_json(409, {'error': 'Part of an earlier sending can’t be cleared: %s' % e})
                        return self.send_json(200, {'size': cur - cur % 1048576})   # (from a whole MiB, to carry on from)
                    if method != 'PUT':
                        return self.send_json(405, {'error': 'PUT or GET.'})
                    try:
                        off, total, n = int(q.get('off') or 0), int(q.get('total') or 0), int(self.headers.get('Content-Length') or -1)
                    except ValueError:
                        return self.send_json(400, {'error': 'Numbers.'})
                    if n < 0 or n > 300 * 1048576 or off > cur or total <= 0 or off + n > total:
                        self.close_connection = True
                        return self.send_json(409, {'error': 'Not from there.', 'size': cur - cur % 1048576})
                    if not job.get('up'):   # (this job's own sending from here on: only this one's carried on from)
                        if off:
                            self.close_connection = True
                            return self.send_json(409, {'error': 'Not from there.', 'size': 0})
                        job['up'] = True
                    with open(dst, 'r+b' if (os.path.exists(dst) and off) else 'wb') as f_:
                        f_.seek(off)
                        f_.truncate(off)
                        left = n
                        while left > 0:
                            b_ = self.rfile.read(min(left, 1048576))
                            if not b_:
                                break
                            f_.write(b_)
                            left -= len(b_)
                    size = os.path.getsize(dst)
                    job['seen'], job['state'] = time.time(), 'copying'
                    job['stage'] = 'copying to the Mac mini: %d%% of %.1f GB' % (size * 100 // total, total / 1e9)
                    if size >= total:
                        os.replace(dst, job['plan']['tmp'])
                        job['got'] = True
                        job['stage'] = 'on the G-Drive: the Mac mini checking it'
                        log('From John’s Mac: %s, %.1f GB, on the G-Drive' % (job['plan']['item'].get('name'), total / 1e9))
                    convjobs_save()
                    return self.send_json(200, {'size': size, 'whole': size >= total})
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
                    code, j = claude_ask(str(self._b.get('action') or ''), self._b.get('why'), self._b.get('args'))
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
                if u.path == '/macagent/report' and method == 'POST':   # (John's Mac's own account of itself, for the admin page and Claude)
                    tok = (self.headers.get('Authorization') or '').replace('Bearer ', '').strip()
                    want = (g.st.get('macagent') or {}).get('key')
                    if not want or not tok or not hmac.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), want):
                        return self.send_json(403, {'error': 'Not allowed.'})
                    raw_ = getattr(self, '_raw', None) or b''
                    with open(os.path.expanduser('~/Grifflix/mac-report.txt'), 'wb') as f_:
                        f_.write(raw_[:200000])
                    MACAGENT['report'] = int(time.time())
                    # (any word from John's Mac is its checking in: while it converts it doesn't ask for a film, and the admin page said "off or
                    # asleep" with it converting: John, 24 Nov 2026, "Why so long? My mac has been on the whole time")
                    MACAGENT['seen'] = int(time.time())
                    return self.send_json(200, {'ok': True})
                if u.path == '/macagent/copying':   # (John's Mac's helper: the films it made that the Mac mini is still waiting for, to copy over)
                    tok = (self.headers.get('Authorization') or '').replace('Bearer ', '').strip()
                    want = (g.st.get('macagent') or {}).get('key')
                    if not want or not tok or not hmac.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), want):
                        return self.send_json(403, {'error': 'Not allowed.'})
                    rows = ['%s\t%s\t%s' % (os.path.basename(j['plan']['tmp']), j['plan']['tmp'], '/convjob/' + t_)
                            for t_, j in CONVJOBS.items() if j.get('mac') and j.get('state') == 'copying' and not j.get('ended')]
                    body_ = ('\n'.join(rows) + '\n').encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Content-Length', str(len(body_)))
                    self.end_headers()
                    self.wfile.write(body_)
                    return
                if u.path == '/macagent/subnext':
                    self._subnext = True
                if u.path in ('/macagent/subnext', '/macagent/subdone', '/macagent/subprog', '/macagent/subfile'):   # (John's Mac's helper: English subtitles made ready, a film at a time,
                    # timed there by what's said (subprep.py): John, 20 Nov 2026, "pre-prepare" ... "I prefer to use the whisper option")
                    tok = (self.headers.get('Authorization') or '').replace('Bearer ', '').strip()
                    want = (g.st.get('macagent') or {}).get('key')
                    if not want or not tok or not hmac.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), want):
                        return self.send_json(403, {'error': 'Not allowed.'})
                    with SP_LOCK:   # (one at a time: a word of progress saved over "done" left films looking still on John's Mac: Sound of Falling, 22 Nov 2026)
                        P = load(SUBPREP, {}) or {}
                        body = self.body()
                        if u.path == '/macagent/subfile':   # (one more subtitle, asked for by John's Mac when none so far fits exactly)
                            if not self.subs_ready() or not re.match(r'^(\d+|sd:/?[\w./#-]+)$', str(q.get('file') or '')):
                                return self.send_json(400, {'error': 'Which?'})
                            o_ = g.st.get('opensubs') or {}
                            if o_.get('remaining') is not None and int(o_.get('remaining') or 0) < 3:
                                return self.send_json(429, {'error': 'OpenSubtitles downloads used up for today'})
                            try:
                                return self.send_json(200, {'srt': SUBS.decode(self.subs_client().download(q['file'] if str(q['file']).startswith('sd:') else int(q['file'])))})
                            except Exception as ex:
                                return self.send_json(502, {'error': str(ex)[:200]})
                        if u.path == '/macagent/subprog' and method == 'POST':   # (what John's Mac is doing now, for the admin page: 21 Nov 2026)
                            e = P.get(str(body.get('id') or ''))
                            if e and e.get('state') == 'given':   # (only while it's still being done there)
                                e.update(step=str(body.get('step') or '')[:200], stepat=int(time.time()))
                                save(SUBPREP, P)
                            return self.send_json(200, {'ok': True})
                        if u.path == '/macagent/subdone' and method == 'POST':
                            iid = str(body.get('id') or '')
                            e = P.get(iid)
                            if not e:
                                return self.send_json(404, {'error': 'Not a film waiting for subtitles.'})
                            if body.get('setup'):   # (Whisper not on John's Mac yet: the film back to waiting, the admin page says so)
                                e.update(state='waiting', note='')
                                P['_setup'] = {'need': True, 'at': int(time.time())}
                                save(SUBPREP, P)
                                return self.send_json(200, {'ok': True})
                            P.pop('_setup', None)
                            if body.get('srt64') and not body.get('srt'):   # (sent packed: Cloudflare refused it as text)
                                try:
                                    body['srt'] = base64.b64decode(str(body['srt64'])).decode('utf-8', 'replace')
                                except Exception:
                                    pass
                            tb_ = body.get('translated') if isinstance(body.get('translated'), dict) else None
                            if tb_ and re.match(r'^[0-9a-f]{32}$', iid):   # (the subtitle made for this copy in another language, translated on John's Mac:
                                # kept for John to take as the film's English subtitles if he finds it good enough: John, 22 Nov 2026)
                                try:
                                    t_ = base64.b64decode(str(tb_.get('srt64') or '')).decode('utf-8', 'replace')
                                    cs_ = SUBS.parse_srt(t_)
                                    if len(cs_) >= 20:
                                        fn_ = os.path.join(os.path.expanduser('~/Grifflix/subprep-cands'), '%s.tr.srt' % iid)
                                        os.makedirs(os.path.dirname(fn_), exist_ok=True)
                                        with open(fn_, 'w', encoding='utf-8') as f_:
                                            f_.write(t_)
                                        e['translated'] = {'lang': str(tb_.get('lang') or '')[:12], 'n': len(cs_),
                                                           'sample': [[round(a_, 1), str(x_)[:120]] for a_, _, x_ in cs_[len(cs_) // 4:len(cs_) // 4 + 8]]}
                                except Exception:
                                    pass
                            if body.get('translate_only'):   # (only translated, for the comparison: the film's subtitles as they were)
                                e['tr_tried'] = int(time.time())
                                e.pop('tr_given', None)
                                e.pop('step', None)
                                save(SUBPREP, P)
                                log('Subtitles for %s: the %s subtitle that timed them translated on John’s Mac, to compare' % (e.get('name'), e.get('ref_lang')) if e.get('translated') else
                                    'Subtitles for %s: the %s subtitle couldn’t be translated on John’s Mac' % (e.get('name'), e.get('ref_lang')))
                                return self.send_json(200, {'ok': True})
                            if isinstance(body.get('evidence'), list) and re.match(r'^[0-9a-f]{32}$', iid):   # (what Whisper heard and matched, for the admin page)
                                try:
                                    save(os.path.join(os.path.expanduser('~/Grifflix/subprep-evidence'), iid + '.json'), {'at': int(time.time()), 'parts': body['evidence'][:8]})
                                except Exception:
                                    pass
                            if e.get('verifying'):   # (the subtitles it had, checked against what's said)
                                e.pop('verifying', None)
                                mv_ = re.search(r'moved ([+-][\d.]+) s', str(body.get('how') or ''))
                                off_ = float(mv_.group(1)) if mv_ else 0.0
                                sp_ = 'speed' in str(body.get('how') or '')
                                if body.get('ok') and abs(off_) < .6 and not sp_:
                                    e.update(state='verified', note='checked against what’s said: in time (%s lines matched)' % body.get('n'), n=body.get('n'), at=int(time.time()),
                                             samples=body.get('samples'), file=e.get('vfile'))
                                    record('subtitles checked', film=e.get('name'), id=iid, file=e.get('vfile'), result='in time', lines=body.get('n'))
                                elif body.get('ok') and body.get('srt'):
                                    it_ = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path'}) or {}
                                    out_ = self.subs_write(it_.get('Path') or '', 'en', SUBS.parse_srt(str(body['srt'])))
                                    old_ = e.get('vfile') or ''
                                    import shutil
                                    if old_ and os.path.abspath(old_) != os.path.abspath(out_) and os.path.exists(old_):
                                        dest_ = os.path.join(SETASIDE, 'Subtitles checked')
                                        os.makedirs(dest_, exist_ok=True)
                                        shutil.move(old_, os.path.join(dest_, time.strftime('%Y%m%d-%H%M ') + os.path.basename(old_)))
                                    g.jf.call('POST', '/Items/%s/Refresh' % iid, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
                                    e.update(state='done', how='verified-moved', note='its subtitles from before were out: ' + str(body.get('how') or ''), n=body.get('n'),
                                             file=out_, at=int(time.time()), sure=bool(body.get('sure')), samples=body.get('samples'))
                                    record('subtitles put right', film=e.get('name'), id=iid, was=old_, now=out_, how=body.get('how'))
                                else:   # (didn't fit at all: looked for others in the usual way, its own kept meanwhile)
                                    e.update(state='waiting', note='its subtitles from before didn’t fit what’s said: others looked for', old_vfile=e.get('vfile'), at=int(time.time()))
                                    record('subtitles didn’t fit', film=e.get('name'), id=iid, file=e.get('vfile'))
                                save(SUBPREP, P)
                                return self.send_json(200, {'ok': True})
                            if body.get('ok') and body.get('srt') and self.subs_ready():
                                try:
                                    it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path'}) or {}
                                    cues = SUBS.parse_srt(str(body['srt']))
                                    out = self.subs_write(it.get('Path') or e.get('path'), 'en', cues)
                                    g.jf.call('POST', '/Items/%s/Refresh' % iid, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
                                    e.update(state='done' if body.get('sure') else 'check', note=str(body.get('how') or '')[:200], chose=str(body.get('chose') or '')[:200],
                                             n=int(body.get('n') or 0), miss=body.get('miss'), file=out, at=int(time.time()), mins=body.get('mins'),
                                             # (all it found, for the admin page: each subtitle tried and how well it fitted, each part of the film
                                             # listened to and the timing there: John, 21 Nov 2026, "I still need to know what the fix chose to do")
                                             tried=[[str(x[0])[:160], int(x[1] or 0), bool(x[2])] for x in (body.get('tried') or []) if isinstance(x, list) and len(x) >= 3][:12],
                                             samples=[[float(x[0]), int(x[1] or 0), float(x[2])] for x in (body.get('samples') or []) if isinstance(x, list) and len(x) >= 3][:12],
                                             sure=bool(body.get('sure')))
                                    e.pop('ref_only', None); e.pop('prev', None)
                                    ov_ = e.pop('old_vfile', None)   # (the subtitles it had before, that didn't fit: set aside, not deleted)
                                    if ov_ and os.path.exists(ov_) and os.path.abspath(ov_) != os.path.abspath(out):
                                        import shutil
                                        os.makedirs(os.path.join(SETASIDE, 'Subtitles checked'), exist_ok=True)
                                        shutil.move(ov_, os.path.join(SETASIDE, 'Subtitles checked', time.strftime('%Y%m%d-%H%M ') + os.path.basename(ov_)))
                                        record('subtitles set aside', film=e.get('name'), id=iid, moved_from=ov_, why='replaced by ones that fit')
                                    if str(body.get('how') or '').startswith('by the '):   # (timed by a translated subtitle made for this copy, not by Whisper)
                                        e['how'] = 'translated'
                                        lr_ = re.match(r'by the (\S+) subtitle', str(body.get('how') or ''))
                                        if lr_ and not e.get('ref_lang'):
                                            e['ref_lang'] = lr_.group(1)
                                    if body.get('machine'):   # (no English fitted: one in another language that fits this copy, translated on John's Mac)
                                        e['how'], e['ref_lang'] = 'machine', str(body['machine'])[:12]
                                        # (and English lined up by that one's timing looked for next, to take its place if one lines up)
                                        threading.Thread(target=lambda i_=iid: (time.sleep(5), english_by_foreign(g, i_)), daemon=True).start()
                                    chk(e, 'whisper', 'Done on your Mac: %s, %s lines matched what’s said%s' % (str(body.get('chose') or '')[:60], body.get('n'), '' if body.get('sure') else ' (not enough to be sure: to check)'))
                                    if e.get('givenfile'):   # (the ones John gave, timed by Whisper)
                                        e['how'], e['cur'] = 'given', 'g'
                                        e.pop('givenfile', None)
                                    log('Subtitles made ready on John’s Mac: %s: %s (%s lines matched)%s' % (e.get('name'), e['note'], e['n'], '' if body.get('sure') else ': to check'))
                                    record('subtitles saved', film=e.get('name'), id=iid, file=out, how='timed by Whisper on John’s Mac: ' + e['note'], chose=e.get('chose'),
                                           lines_matched=e['n'], sure=bool(body.get('sure')), tried=e.get('tried'))
                                except Exception as ex:
                                    e.update(state='failed', note=('couldn’t be saved: %s' % ex)[:200], at=int(time.time()))
                            elif e.get('ref_only') and isinstance(e.get('prev'), dict):   # (translated and compared, didn't line up: its Whisper subtitles kept)
                                e.update(e.pop('prev'))
                                e.pop('ref_only', None)
                                log('Subtitles for %s: the translated subtitle didn’t line up; those timed by Whisper kept' % e.get('name'))
                            else:
                                e.update(state='nomatch' if 'matched' in str(body.get('error') or '') else 'failed', note=str(body.get('error') or 'it didn’t work')[:200], at=int(time.time()),
                                         tried=[[str(x[0])[:160], int(x[1] or 0)] for x in (body.get('tried') or []) if isinstance(x, list) and len(x) >= 2][:12])
                                log('Subtitles not made ready for %s: %s' % (e.get('name'), e['note']))
                                chk(e, 'whisper', 'On your Mac: %s' % e['note'])
                                record('subtitles not made', film=e.get('name'), id=iid, why=e['note'], tried=body.get('tried'))
                                # (the likeliest three kept, timed as they'd be, for John to look at and choose one himself: John, 22 Nov 2026)
                                kept_ = []
                                for i_, c_ in enumerate((body.get('cands') or [])[:3]):
                                    try:
                                        t_ = base64.b64decode(str(c_.get('srt64') or '')).decode('utf-8', 'replace')
                                        if not re.match(r'^[0-9a-f]{32}$', iid) or not t_:
                                            continue
                                        fn_ = os.path.join(os.path.expanduser('~/Grifflix/subprep-cands'), '%s.%d.srt' % (iid, i_))
                                        os.makedirs(os.path.dirname(fn_), exist_ok=True)
                                        with open(fn_, 'w', encoding='utf-8') as f_:
                                            f_.write(t_)
                                        if isinstance(c_.get('evidence'), list):   # (its lines beside what was heard, to compare)
                                            save(os.path.join(os.path.expanduser('~/Grifflix/subprep-evidence'), '%s.%d.json' % (iid, i_)), {'at': int(time.time()), 'parts': c_['evidence'][:8]})
                                        kept_.append({'i': i_, 'name': str(c_.get('name') or '')[:160], 'n': int(c_.get('n') or 0), 'how': str(c_.get('how') or '')[:120],
                                                      'miss': c_.get('miss'), 'samples': c_.get('samples') if isinstance(c_.get('samples'), list) else []})
                                    except Exception:
                                        pass
                                e['cands'] = kept_
                                e.pop('givenfile', None)   # (John's, kept to use as it is: not handed out again by itself)
                                # (Whisper heard next to no speech in any part listened to: a film with little or no dialogue, not a failure, and not
                                # tried again and again: John, 22 Nov 2026, "Robot Dreams is a virtually dialogue free film, so failing")
                                ss_ = [p_ for c_ in kept_ for p_ in (c_.get('samples') or [])]
                                known_ = bool(ss_) and all(isinstance(p_, list) and len(p_) > 3 and p_[3] is not None for p_ in ss_)
                                heard_ = [sum(int(p_[3]) for p_ in (c_.get('samples') or [])) for c_ in kept_] if known_ else []
                                if heard_ and max(heard_) <= 4:
                                    e.update(state='nodialogue', note='Whisper heard little or no speech in the four parts listened to (%d lines in all): little or no dialogue' % max(heard_))
                                    log('Subtitles for %s: little or no dialogue (Whisper heard %d lines in four parts): none needed' % (e.get('name'), max(heard_)))
                                    record('subtitles not needed', film=e.get('name'), id=iid, why=e['note'])
                            save(SUBPREP, P)
                            return self.send_json(200, {'ok': True})
                        if P.get('_paused') or q.get('home') != '1' or not self.subs_ready():
                            return self.send_json(200, {'wait': 'paused' if P.get('_paused') else 'away'})
                        # (no blanket stop on a remembered count of downloads left: it went stale, and held every job back, translations and John's own
                        # order too: John, 22 Nov 2026, "the mac is ignoring my ordering". Each step stops by itself when they're really used up)
                        now_ = time.time()
                        # (any left as on John's Mac with no word from it for half an hour: finished there if its subtitles were saved since, else
                        # back to waiting: Sound of Falling showed "part 3 of 4" four hours after it was done)
                        for k_, x_ in P.items():
                            if k_.startswith('_') or not isinstance(x_, dict) or x_.get('state') != 'given' or now_ - max(x_.get('stepat') or 0, x_.get('at') or 0) < 1800:
                                continue
                            try:
                                p_ = (g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, k_), query={'Fields': 'Path'}) or {}).get('Path') or ''
                            except Exception:
                                p_ = ''
                            f_ = '%s.Grifflix.en.srt' % os.path.splitext(p_)[0] if p_ else ''
                            x_.pop('step', None); x_.pop('verifying', None)
                            if f_ and os.path.exists(f_) and os.path.getmtime(f_) > (x_.get('at') or 0):
                                x_.update(state='done', file=f_, at=int(os.path.getmtime(f_)), note=x_.get('note') or 'timed on John’s Mac (its record was lost: the subtitles kept)')
                            else:
                                x_.update(state='waiting', note='')
                            log('Subtitles for %s: no word from John’s Mac for half an hour: %s' % (x_.get('name'), 'its subtitles were saved, so done' if x_['state'] == 'done' else 'back to waiting'))
                        # (first, a quick one: the subtitle in another language a film's English was timed by, translated, so the comparison shows what's
                        # said in English: John, 22 Nov 2026, "fallen angels needed translations otherwise impossible for me to judge - that should
                        # always be done for a film where the subtitle used as a basis is not english")
                        prio_ = any(isinstance(v_, dict) and v_.get('prio') and v_.get('state') in ('waiting', 'verify') for v_ in P.values())   # (one John put next: before any translation)
                        for iid, e in ([] if prio_ else sorted(P.items(), key=lambda kv: -((kv[1].get('tr_asked') or 0) if isinstance(kv[1], dict) else 0))):   # (those John asked for first)
                            if iid.startswith('_') or not isinstance(e, dict) or not re.match(r'^[0-9a-f]{32}$', iid):
                                continue
                            if e.get('state') not in ('done', 'check') and not e.get('tr_asked'):
                                continue
                            rf_ = e.get('ref_file') or ''
                            if not rf_ or not os.path.exists(rf_) or str(e.get('ref_lang') or '').lower()[:2] in ('', 'en'):
                                continue
                            if os.path.exists(os.path.join(os.path.expanduser('~/Grifflix/subprep-cands'), '%s.tr.srt' % iid)):
                                continue
                            if now_ - (e.get('tr_tried') or 0) < 7 * 86400 or now_ - (e.get('tr_given') or 0) < 3600:
                                continue
                            e['tr_given'] = int(now_)
                            save(SUBPREP, P)
                            return self.send_json(200, {'subjob': {'id': iid, 'name': e.get('name'), 'path': '', 'dur': 0, 'translate_only': True, 'cands': [], 'more': [],
                                                                   'lang': e.get('lang') or '', 'ref': {'lang': e.get('ref_lang'), 'srt': open(rf_, encoding='utf-8', errors='replace').read()}}})
                        for iid, e in sorted(((k, v) for k, v in P.items() if not k.startswith('_') and isinstance(v, dict)), key=lambda kv: (not kv[1].get('prio'), kv[1].get('state') == 'verify', kv[1].get('order', 0))):
                            if e.get('state') == 'given' and now_ - e.get('at', 0) < 3 * 3600:
                                continue
                            if e.get('state') == 'verify':   # (subtitles from before, checked as they are: only once none is waiting)
                                vf_ = e.get('vfile') or ''
                                if not os.path.exists(vf_):
                                    # (the film itself gone from under that id (its folder renamed: a new id, with its own entry): this one taken off,
                                    # not left under Not found: John, 24 Nov 2026, Totally F***ed Up there twice, once "not found")
                                    try:
                                        there_ = bool(jf_has(g, iid).get('Id'))
                                    except Exception:
                                        there_ = True
                                    if not there_:
                                        P.pop(iid, None)
                                        save(SUBPREP, P)
                                        continue
                                    e.update(state='gone', note='its subtitles aren’t there now', at=int(now_))
                                    continue
                                it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path'}) or {}
                                if not it.get('Path') or not os.path.exists(it['Path']):
                                    continue
                                try:
                                    srt_ = SUBS.decode(open(vf_, 'rb').read())
                                except OSError:
                                    continue
                                e.update(state='given', verifying=True, at=int(now_), note='')
                                e.pop('prio', None)
                                save(SUBPREP, P)
                                return self.send_json(200, {'subjob': {'id': iid, 'name': it.get('Name'), 'path': it['Path'], 'dur': (it.get('RunTimeTicks') or 0) / 1e7,
                                                                       'translate': bool(e.get('translate')), 'lang': e.get('lang') or '', 'verify': True, 'more': [],
                                                                       'cands': [{'name': 'the subtitles it has (%s)' % os.path.basename(vf_), 'srt': srt_}]}})
                            if e.get('state') not in ('waiting', 'given'):
                                continue
                            try:
                                it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path,ProviderIds,OriginalTitle'}) or {}
                            except Exception:
                                it = {}
                            path = it.get('Path') or ''
                            if not path or not os.path.exists(path):
                                e.update(state='failed', note='its file isn’t there', at=int(now_))
                                continue
                            ep_fill(g, e, iid)   # (an episode: its series' IMDb number and own title, for the searches)
                            gf_ = e.get('givenfile') or ''
                            if gf_ and os.path.exists(gf_):   # (the subtitles John gave: only those, timed to the film by Whisper on his Mac)
                                e.update(state='given', at=int(now_), note='')
                                save(SUBPREP, P)
                                return self.send_json(200, {'subjob': {'id': iid, 'name': it.get('Name'), 'path': path, 'dur': (it.get('RunTimeTicks') or 0) / 1e7,
                                                                       'translate': True, 'lang': e.get('lang') or '', 'more': [],
                                                                       'cands': [{'name': e.get('givenname') or 'the subtitles you gave', 'srt': open(gf_, encoding='utf-8', errors='replace').read()}]}})
                            pid = it.get('ProviderIds') or {}
                            try:
                                mh = SUBS.movie_hash(path)
                            except OSError:
                                mh = None
                            try:
                                org_ = film_origin(path)   # (the release it was downloaded as: subtitles made for it first)
                                if e.get('kind') == 'episode':
                                    res = self.subs_client().search('en', mhash=mh, query=e.get('series'), parent_tmdb=e.get('series_tmdb'), parent_imdb=e.get('series_imdb'), alt=[e.get('series_orig')], season=e.get('season'),
                                                                    episode=e.get('episode'), hint=org_ or os.path.basename(path), also=True)
                                else:
                                    res = self.subs_client().search('en', mhash=mh, tmdb=pid.get('Tmdb'), imdb=pid.get('Imdb'), alt=[it.get('OriginalTitle')], query=it.get('Name'),
                                                                    year=it.get('ProductionYear'), hint=org_ or os.path.basename(path), also=True)
                            except Exception as ex:
                                e.update(note=('OpenSubtitles: %s' % ex)[:200])
                                save(SUBPREP, P)
                                return self.send_json(200, {'wait': 'OpenSubtitles didn’t answer'})
                            if self.subs_client().out_of_downloads():   # (the day's downloads used up: no film taken up, none marked, till the reset)
                                return self.send_json(200, {'wait': 'OpenSubtitles downloads used up for today'})
                            res = [x for x in res if not x.get('machine') and x.get('kind') in (('', 'episode', 'tvshow') if e.get('kind') == 'episode' else ('', 'movie'))]
                            ex_ = [x for x in res if x.get('exact')]
                            if not ex_:
                                chk(e, 'exact', 'None of the %d English subtitles on OpenSubtitles is made for this very file (by its fingerprint)' % len(res))
                            if ex_:   # (made for this very file: taken as it is, no Whisper needed: John, 22 Nov 2026, "get ones with exact timing")
                                try:
                                    cues_ = SUBS.parse_srt(SUBS.decode(self.subs_client().download(ex_[0]['file_id'])))
                                    if len(cues_) >= 20:
                                        out_ = self.subs_write(path, 'en', cues_)
                                        g.jf.call('POST', '/Items/%s/Refresh' % iid, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
                                        e.update(state='done', note='made for this very file (OpenSubtitles, by its fingerprint): its timing as it is',
                                                 chose=ex_[0].get('name') or '', file=out_, at=int(now_), sure=True, n=len(cues_), how='exact')
                                        log('Subtitles made for the very file: %s (%d lines)' % (e.get('name'), len(cues_)))
                                        record('subtitles saved', film=e.get('name'), id=iid, file=out_, how='made for this very file (OpenSubtitles fingerprint)',
                                               opensubtitles_file=ex_[0].get('file_id'), release=ex_[0].get('name'), lines=len(cues_))
                                        save(SUBPREP, P)
                                        continue
                                except Exception as ex:
                                    log('Subtitles made for the very file, %s: %s' % (e.get('name'), str(ex)[:160]))
                            if not e.get('name_tried'):   # (English named for this very release: used as it is, no listening)
                                e['name_tried'] = int(now_)
                                try:
                                    if name_sub(g, self.subs_client(), e, iid, path, it, res):
                                        save(SUBPREP, P)
                                        continue
                                except Exception as ex:
                                    log('Subtitles named for the release, %s: %s' % (e.get('name'), str(ex)[:160]))
                                if self.subs_client().out_of_downloads():
                                    e.pop('name_tried', None)
                                    save(SUBPREP, P)
                                    return self.send_json(200, {'wait': 'OpenSubtitles downloads used up for today'})
                            if e.get('state') == 'waiting' and e.get('kind') != 'episode' and not e.get('givenfile') and silent_film(g, iid, it):
                                e.update(state='nodialogue', note=SILENT_NOTE, at=int(now_))   # (a silent film: not listened to on John's Mac)
                                chk(e, 'whisper', 'Not needed: a silent film, no speech to time subtitles by')
                                save(SUBPREP, P)
                                continue
                            if not e.get('ref_tried2'):   # (a subtitle made for this copy in another language: the English timed by its lines, no Whisper)
                                e['ref_tried2'] = int(now_)
                                try:
                                    if ref_sub(g, self.subs_client(), e, iid, path, it):
                                        e.pop('ref_only', None); e.pop('prev', None)   # (timed by it here: nothing left for his Mac to try)
                                        save(SUBPREP, P)
                                        continue
                                except Exception as ex:
                                    log('Subtitles timed by another language, %s: %s' % (e.get('name'), str(ex)[:160]))
                                if self.subs_client().out_of_downloads():
                                    e.pop('ref_tried2', None)
                                    save(SUBPREP, P)
                                    return self.send_json(200, {'wait': 'OpenSubtitles downloads used up for today'})
                            # (by the files themselves found nothing: when people speak tried on the Mac mini next, only for these; Whisper after that:
                            # John, 22 Nov 2026, "I don't think you should be needing to do audio checks very often")
                            if e.get('state') == 'waiting' and not e.get('speech_tried') and SPEECH['ok'] and not (e.get('givenfile') or e.get('ref_only')):
                                save(SUBPREP, P)
                                continue
                            # (the film's own length first: a subtitle named for a cut of its length before others, one named for a longer or shorter
                            # cut last: Andrei Rublev is the 183-minute version, and "BluRay 3hrs 02mn 59s" was there among the 205-minute ones but
                            # not tried first: John, 22 Nov 2026, "it's clearly the criterion copy and the 183 minute version")
                            dmin_ = (it.get('RunTimeTicks') or 0) / 6e8
                            def lenfit_(nm):
                                nm = str(nm or '').lower()
                                m1 = re.search(r'(\d)\s*h(?:rs?|ours?)?[\s.:]*(\d{1,2})\s*m', nm)
                                m2 = re.search(r'\b(\d{2,3})\s*min', nm)
                                mm_ = int(m1.group(1)) * 60 + int(m1.group(2)) if m1 else int(m2.group(1)) if m2 else None
                                if not mm_ or not dmin_:
                                    return 0
                                return 2 if abs(mm_ - dmin_) <= 4 else -3 if abs(mm_ - dmin_) > 8 else 0
                            if dmin_:
                                res = sorted(res, key=lambda x: (not x.get('exact'), -lenfit_(x.get('name'))))   # (stable: OpenSubtitles' own order otherwise)
                            more_ = [{'file_id': x['file_id'], 'name': x.get('name') or ''} for x in res[3:10]]   # (the next ones, fetched by John's Mac one at a time
                            # only if none of the first fits exactly, without a change of speed: John, 21 Nov 2026, "Better to get the right one exactly before fixing")
                            cands, longer_, last_ = [], [], -1
                            for k_, x in enumerate(res[:8]):
                                if len(cands) >= 3:
                                    break
                                last_ = k_
                                try:
                                    t_ = SUBS.decode(self.subs_client().download(x['file_id']))
                                    c_ = SUBS.parse_srt(t_)
                                    if dmin_ and c_ and c_[-1][1] > dmin_ * 60 + 300:   # (lines running on past the film's end: made for a longer cut)
                                        longer_.append(x.get('name') or '')
                                        continue
                                    cands.append({'name': x.get('name') or '', 'exact': bool(x.get('exact')), 'srt': t_})
                                except Exception as ex:
                                    if 'allowed' in str(ex).lower() or 'download' in str(ex).lower():
                                        save(SUBPREP, P)
                                        return self.send_json(200, {'wait': 'OpenSubtitles downloads used up for today'})
                            more_ = [{'file_id': x['file_id'], 'name': x.get('name') or ''} for x in res[last_ + 1:last_ + 8]]   # (the next ones after those taken)
                            # (and those in other languages, the film's own first, for John's Mac to try by when their lines are said if no English
                            # fits: the English lined up by the one that fits, or else it translated there. Fetched only if needed. John, 23 Nov
                            # 2026, "if we can get a subtitle at all, we'd be better off translating it than using whisper")
                            fmore_ = []
                            try:
                                if e.get('kind') == 'episode':
                                    A_ = self.subs_client().search('', query=e.get('series'), parent_tmdb=e.get('series_tmdb'), parent_imdb=e.get('series_imdb'), alt=[e.get('series_orig')], season=e.get('season'), episode=e.get('episode'), hint=org_ or os.path.basename(path), also=True)
                                else:
                                    A_ = self.subs_client().search('', tmdb=pid.get('Tmdb'), imdb=pid.get('Imdb'), alt=[it.get('OriginalTitle')], query=it.get('Name'), year=it.get('ProductionYear'), hint=org_ or os.path.basename(path), also=True)
                                own_ = {'fre': 'fr', 'fra': 'fr', 'ger': 'de', 'deu': 'de', 'spa': 'es', 'ita': 'it', 'jpn': 'ja', 'swe': 'sv', 'rus': 'ru', 'por': 'pt', 'chi': 'zh', 'zho': 'zh', 'kor': 'ko',
                                        'pol': 'pl', 'dut': 'nl', 'nld': 'nl', 'dan': 'da', 'nor': 'no', 'fin': 'fi', 'hun': 'hu', 'cze': 'cs', 'ces': 'cs', 'gre': 'el', 'ell': 'el', 'tur': 'tr',
                                        'per': 'fa', 'fas': 'fa', 'ara': 'ar', 'heb': 'he', 'hin': 'hi', 'rum': 'ro', 'ron': 'ro', 'srp': 'sr', 'hrv': 'hr', 'ben': 'bn'}.get(str(e.get('lang') or '').lower(), str(e.get('lang') or '').lower()[:2])
                                lg_ = lambda x: re.split(r'[-_]', str(x.get('lang') or '').lower())[0]
                                fo_ = [x for x in A_ if x.get('file_id') and not x.get('machine') and lg_(x) not in ('en', 'eng', '')]
                                fo_.sort(key=lambda x: (lg_(x) != own_, not x.get('samename'), -lenfit_(x.get('name')), -(x.get('like') or 0), -(x.get('downloads') or 0)))
                                seen_ = set()
                                for x in fo_:   # (one a language, the four likeliest)
                                    if lg_(x) in seen_ and len(fo_) > 4:
                                        continue
                                    seen_.add(lg_(x))
                                    fmore_.append({'file_id': x['file_id'], 'name': x.get('name') or '', 'lang': lg_(x)})
                                    if len(fmore_) >= 4:
                                        break
                            except Exception:
                                fmore_ = []
                            if not cands or e.get('tried'):   # (SubtitleCat's original uploads, the last place looked: English to time,
                                # others to time and translate on John's Mac)
                                for x in subtitlecat_for(g, e, iid, it, path):
                                    c_ = SUBS.parse_srt(x['srt'])
                                    if dmin_ and c_ and c_[-1][1] > dmin_ * 60 + 300:
                                        longer_.append(x['name'])
                                        continue
                                    if x['lang'] == 'en' and len(cands) < 4:
                                        cands.append({'name': x['name'], 'exact': False, 'srt': x['srt']})
                                    elif x['lang'] and x['lang'] != 'en' and len(fmore_) < 6:
                                        fmore_.append({'name': x['name'], 'lang': x['lang'], 'srt': x['srt']})
                            has_ref_ = bool(e.get('ref_file') and os.path.exists(e['ref_file']))
                            if not cands and not fmore_ and not has_ref_:
                                e.update(state='nomatch' if longer_ else 'none', at=int(now_),
                                         note=('only English subtitles made for a longer cut (%s)' % ', '.join(longer_[:3]))[:200] if longer_ else 'no subtitles on OpenSubtitles%s or SubtitleCat in English or any other language' % (', Subdl' if SUBS.SUBDL.get('key') else ''))
                                continue
                            e.pop('prio', None)
                            if not cands and has_ref_ and e.get('ref_sure'):
                                chk(e, 'whisper', 'Given to your Mac at %s: no English subtitles, so the %s one made for this copy translated into English there (no listening)' % (time.strftime('%H:%M'), e.get('ref_lang')))
                            elif not cands:
                                chk(e, 'whisper', 'Given to your Mac at %s: no English subtitles, so Whisper listens to four parts to find which of %d in other languages fits by when its lines are said, and that one is translated into English there' % (time.strftime('%H:%M'), len(fmore_) + has_ref_))
                            else:
                                chk(e, 'whisper', 'Given to your Mac at %s: Whisper listens to four parts and tries %d English subtitle%s against what’s said%s' % (time.strftime('%H:%M'), len(cands), '' if len(cands) == 1 else 's',
                                    '; if none fits, %d in other languages by when their lines are said, the one that fits translated' % (len(fmore_) + has_ref_) if fmore_ or has_ref_ else ''))
                            e.update(state='given', at=int(now_), note='', origin=org_ or '')
                            save(SUBPREP, P)
                            return self.send_json(200, {'subjob': {'id': iid, 'name': it.get('Name'), 'path': path, 'dur': (it.get('RunTimeTicks') or 0) / 1e7,
                                                                   'translate': True, 'cands': cands, 'more': more_, 'lang': e.get('lang') or '',
                                                                   # (and the one in another language made for this copy, if any, for John's Mac to
                                                                   # translate and compare with the English before listening with Whisper)
                                                                   'ref_only': bool(e.get('ref_only')), 'ref_sure': bool(e.get('ref_sure')), 'fmore': fmore_,
                                                                   'ref': ({'lang': e.get('ref_lang'), 'srt': open(e['ref_file'], encoding='utf-8').read()}
                                                                           if e.get('ref_file') and os.path.exists(e['ref_file']) else None)}})
                        save(SUBPREP, P)
                        return self.send_json(200, {'wait': 'none waiting'})
                if u.path == '/macagent/next':   # (John's Mac's helper, asking every minute: the next film queued for it, as a job)
                    tok = (self.headers.get('Authorization') or '').replace('Bearer ', '').strip()
                    want = (g.st.get('macagent') or {}).get('key')
                    if not want or not tok or not hmac.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), want):
                        return self.send_json(403, {'error': 'Not allowed.'})
                    MACAGENT['seen'] = int(time.time()); MACAGENT['home'] = q.get('home') == '1'
                    # (that it has ever checked in, kept over restarts: admin's "Set up your Mac" to-do stayed open after every restart while
                    # his Mac was off: John, 20 Nov 2026, "This has been there for a while, but I sorted this a while ago")
                    ma_ = g.st.get('macagent') or {}
                    if ma_.get('key') and not ma_.get('worked'):
                        ma_['worked'] = int(time.time()); g.st['macagent'] = ma_; save(SETTINGS, g.st)
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
                    # (a film John's Mac went quiet on, home again now: the network went, or the lid closed. Back at the front of its queue,
                    # from the start; one made and waiting to be copied over is left three days, the Mac keeping it to copy when it can:
                    # John, 18 Nov 2026, "what happens if my mac leaves the network while a conversion is running?")
                    for t_, j in list(CONVJOBS.items()):
                        quiet_ = now - (j.get('seen') or j['at'])
                        if j.get('mac') and not j.get('ended') and ((j.get('state') in ('started', 'converting') and quiet_ > 1200) or (j.get('state') == 'copying' and quiet_ > 3 * 86400)):
                            macq_again(j, now, 'no word from John’s Mac since %s' % time.strftime('%H:%M', time.localtime(j.get('seen') or j['at'])))
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
                        save(MACQ + '.planning', x)   # (kept till it's a job: a restart meanwhile puts it back, not loses it: John, 20 Nov 2026,
                        # "I queued films for my mac but nothing happened": Wild at Heart and News from Home taken off the queue and gone)

                        def plan_it(x=x):
                            try:
                                code, j = mac_job(x['id'], True)   # (always made smaller on John's Mac: see convert.py plan)
                            except Exception as e:
                                code, j = 0, {'error': 'it couldn’t be planned: %s' % str(e)[:200]}
                            try:
                                os.remove(MACQ + '.planning')
                            except OSError:
                                pass
                            if code == 200:
                                CONVJOBS[j['token']].update(mac=True, force=True)
                                convjobs_save()
                            else:
                                log('Queue for John’s Mac: %s passed over (%s)' % (x.get('name') or x['id'], j.get('error')))
                                # (kept, with why, for the queue's card: it went from the queue with nothing said there: John, 20 Nov 2026,
                                # "Looks like Wild at Heart failed, but I don't see any message")
                                P_ = [y for y in (load(MACQ + '.passed', []) or []) if y.get('id') != x['id'] and time.time() - y.get('at', 0) < 3 * 86400]
                                save(MACQ + '.passed', ([{'id': x['id'], 'name': x.get('name') or '', 'why': str(j.get('error') or 'it couldn’t be planned')[:300], 'at': int(time.time())}] + P_)[:20])
                        MACPLAN.update(t=threading.Thread(target=plan_it, daemon=True), name=x.get('name'))
                        MACPLAN['t'].start()
                    return self.send_json(200, {'wait': 'planning', 'name': x.get('name')})
                if u.path.startswith('/convjob/'):   # (John's Mac, converting a film for the Mac mini: its job, and word when it's done)
                    parts = u.path.split('/')
                    job = CONVJOBS.get(parts[2] if len(parts) > 2 else '')
                    if not job or time.time() - job['at'] > (4 * 86400 if method == 'POST' else 12 * 3600):   # (a film made, copied over when John's Mac is home again)
                        return self.send_json(404, {'error': 'That job isn’t here (the Mac mini may have restarted): press Convert on this Mac again.'})
                    pl = job['plan']
                    if method == 'GET':
                        if job.get('ended') or job.get('cancel'):
                            return self.send_json(404, {'error': 'That job is cancelled or finished.' if job.get('cancel') or job.get('state') == 'cancelled' else 'That job is finished: press Convert on this Mac again.'})
                        job['state'], job['seen'] = 'started', time.time()
                        convjobs_save()
                        return self.send_json(200, {'name': pl['item'].get('name'), 'src': pl['src'], 'tmp': pl['tmp'], 'dur': pl['dur'], 'args': vt_args(pl['cmd'])})
                    body = self.body()
                    if job.get('state') == 'lost' and job.get('again') and len(parts) > 3 and parts[3] == 'progress':   # (not lost after all: on again)
                        Q = [x for x in (load(MACQ, []) or []) if x.get('id') != job['id']]
                        save(MACQ, Q); job.pop('ended', None); job.pop('again', None)
                        log('Queue for John’s Mac: %s going again on John’s Mac, out of the queue' % pl['item'].get('name'))
                    if len(parts) > 3 and parts[3] == 'progress' and body.get('stage') and 't' not in body:   # (a step on John's Mac, said: John, 17 Nov 2026)
                        job['stage'], job['seen'] = str(body.get('stage'))[:160], time.time()
                        if body.get('copying'):   # (made: copying itself to the G-Drive while John's Mac takes up the next film)
                            job['state'] = 'copying'
                        convjobs_save()
                        if job.get('cancel'):
                            return self.send_json(200, {'ok': True, 'cancel': True})
                        claim_renew(job['id'])
                        return self.send_json(200, {'ok': True})
                    if len(parts) > 3 and parts[3] == 'progress' and job.get('cancel'):   # (John cancelled it: John's Mac told to stop)
                        job['seen'] = time.time()
                        return self.send_json(200, {'ok': True, 'cancel': True})
                    if len(parts) > 3 and parts[3] == 'progress':   # (how far John's Mac has got, every few seconds: for the film's Edit panel)
                        job['t'], job['speed'], job['state'], job['seen'] = float(body.get('t') or 0), str(body.get('speed') or '')[:12], 'converting', time.time()
                        MACAGENT['seen'], MACAGENT['home'] = int(time.time()), True   # (converting: on, and reaching the G-Drive)
                        if time.time() - job.get('saved', 0) > 30:
                            job['saved'] = time.time()
                            convjobs_save()
                            claim_renew(job['id'])   # (the Mac mini keeps off it while John's Mac is at it: John, 17 Nov 2026)
                        return self.send_json(200, {'ok': True})
                    home = os.path.expanduser('~/Grifflix')
                    job['state'] = 'checking' if body.get('ok') else 'cancelled' if job.get('cancel') else 'failed'
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
                                m_ = re.match(r'the new file is (\d+) s long, the original (\d+) s', str(rj.get('error') or ''))
                                if m_ and int(m_.group(1)) < int(m_.group(2)):   # (made short: read only so far, the network gone a moment: made again)
                                    short_again(job, int(m_.group(1)), int(m_.group(2)))
                                CONV_NOW.set()
                            else:
                                log('Not converted on John’s Mac: %s (%s)' % (job['plan']['item'].get('name'), str(body.get('error') or '')[:200]))
                                if body.get('short') and not job.get('cancel'):   # (John's Mac read only part of it: made again, unless it stops there each time)
                                    short_again(job, int(body.get('short') or 0), int(job['plan'].get('dur') or 0))
                                elif body.get('again') and not job.get('cancel'):   # (the G-Drive went part-way: back at the front of its queue)
                                    macq_again(job, time.time(), str(body.get('error') or ''))
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
                        jf_look(g, [str(self.body().get('path') or '')])
                    except Exception as e:
                        log('Asking the media server to look:', e)
                    g.forget()
                    g.publish_soon = time.time() + 540
                    SCAN_NOW.set()
                    for t_ in (90, 240):
                        threading.Timer(t_, CONV_NOW.set).start()
                    new_follow_up([str(self.body().get('path') or '')])   # (its cover, A24, its distributor: once films stop coming in, not for each)
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
            o.update(afi_of(str((it.get('ProviderIds') or {}).get('Imdb') or '')))   # (AFI's details, for members only: never in the site's own files)
            if me.get('owner') and it.get('Type') in ('Movie', 'Episode'):   # (John, 13 Nov 2026: "a convert now option to films if I need them sooner")
                o['conv'] = conv_why(ms)
                cr_ = (load(os.path.join(CRL, 'result.json'), {}) or {}).get(it['Id'])   # (Criterion's own release or not: John's alone)
                if cr_ and cr_.get('v') in ('yes', 'no', 'unread'):
                    o['crel'] = {'v': cr_['v'], 'how': cr_.get('how') or '', 'by': cr_.get('by') or ''}
                P = load(os.path.expanduser('~/Grifflix/convert-priority.json'), {}) or {}
                o['convNext'] = any(x.get('id') == it['Id'] for x in P.get('pins') or [])
                now_ = load(os.path.expanduser('~/Grifflix/convert-now.json'), {}) or {}
                o['convNow'] = bool(now_.get('name') and now_.get('name') == it.get('Name') and now_.get('state') in ('converting', 'paused') and conv_alive(now_))
            self.send_json(200, o)

        # collections (John's, from Plex: Jellyfin's BoxSets), for members and visitors alike
        def collections_out(self, public, owner=False):
            r = g.items(Recursive='true', IncludeItemTypes='BoxSet', SortBy='SortName')
            sets = [s for s in r.get('Items', []) if owner or not owner_only_set(s.get('Name'))]   # (John's own: his alone)
            # a box set inside another collection (Martha Graham in the Criterion Collection) shows only there, as one
            # entry, not in the list of collections too (John, 7 Nov 2026)
            inside = set(i['Id'] for c in sets for i in g.items(ParentId=c['Id'], Fields='', EnableImageTypes='').get('Items', []) if i.get('Type') == 'BoxSet')
            return [item_out(i) for i in sets if i['Id'] not in inside and (not public or g.is_public_set(i['Id']))]

        def collection_out(self, iid, prog, public, owner=False):
            c = g.one(iid)
            if not c or c.get('Type') != 'BoxSet' or (owner_only_set(c.get('Name')) and not owner):
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
                    if J.get('state') == 'done' and not J.get('given') and time.time() - (J.get('at') or 0) > 900:
                        # (one made long ago and never taken, the admin page closed before it came: thrown away and made afresh, so an
                        # old list isn't put on the site over a newer one: 19 Nov 2026, A24 put back to 175 by a list made before the
                        # move to A24 CHECKS and taken 40 minutes later. The films it read stay remembered, so nothing is lost)
                        log('Update: a list made %d minutes ago and never taken, made afresh' % ((time.time() - (J.get('at') or 0)) // 60))
                        g.export_job = J = {}
                    if J.get('state') == 'done':   # (kept until it's taken: the Mac counts what it made as sent)
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
                        CTMDB_ = load(os.path.expanduser('~/Grifflix/criterion-tmdb.json'), {}) or {}
                        CTT = {}
                        CLL_ = {}   # (every film of each spine: some share one, Ivan the Terrible Parts I and II both 88)
                        CT = {}   # (and by title and year, for the films with no spine number: Criterion Premieres, Eclipse and the rest, which had no
                        # Criterion labels at all: John, 17 Nov 2026, "the filter on criterion is saying there are no criterion premieres or eclipse")
                        for f_ in load(os.path.expanduser('~/Grifflix/criterion-list.json'), []) or []:
                            sl_ = str(f_.get('link') or '').replace('https://www.criterion.com/films/', '')
                            v_ = (str(f_.get('director') or '')[:160], str(f_.get('country') or '')[:60], TG.get(sl_, []), TS.get(sl_, []), sl_)
                            FINFO[sl_] = (str(f_.get('title') or ''), str(f_.get('year') or ''), str(f_.get('spine') or ''))
                            if str(f_.get('spine') or '').isdigit():
                                CL.setdefault(int(f_['spine']), v_)
                                CLL_.setdefault(int(f_['spine']), []).append((fold(f_.get('title')), str(f_.get('year') or ''), v_))
                            if re.match(r'^\d{4}$', str(f_.get('year') or '')):
                                CT.setdefault((fold(f_.get('title')), int(f_['year'])), v_)
                                # (and by either of its names, when Criterion gives two: "Non, Je Ne Regrette Rien (No Regret)" is No Regret too: John,
                                # 20 Nov 2026, "I also just got the missing films from that set but I can't see them at all")
                                for alt_ in getattr(re.match(r'^(.*?)\s*\(([^()]{3,})\)\s*$', str(f_.get('title') or '')), 'groups', lambda: ())():
                                    if alt_ and fold(alt_):
                                        CT.setdefault((fold(alt_), int(f_['year'])), v_)
                            # (and by its TMDB number, for a film named otherwise here: Criterion's L’enfant aimé ou Je joue à être une femme mariée
                            # is The Beloved Child, or I Play at Being a Married Woman: John, 20 Nov 2026)
                            pk_ = re.sub(r'[^a-zA-Z0-9]', '', __import__('unicodedata').normalize('NFKD', str(f_.get('title') or '')).encode('ascii', 'ignore').decode()).lower() + '|' + re.sub(r'[^0-9]', '', str(f_.get('year') or ''))
                            tid_ = str((CTMDB_.get(pk_) or [0])[0] or '')
                            if tid_:
                                CTT.setdefault(tid_, v_)
                        # (an anthology: one Criterion release of several films, kept in one folder named after it (A Hollis Frampton Odyssey,
                        # Martha Graham: Dance on Film, Three Documentaries): its films grouped as a box set under the release, with its spine
                        # and cover: John, 18 Nov 2026, "the same may be true of other unusual criterion collection items")
                        FOLDN_ = __import__("collections").Counter(os.path.dirname(i.get('Path') or '') for i in raw) if key == 'movies' else {}
                        FOLDP_ = __import__("collections").Counter(os.path.dirname(os.path.dirname(i.get('Path') or '')) for i in raw) if key == 'movies' else {}   # (and by the folder above)
                        ANTH_, HALF_ = {}, {}
                        # (the box sets once kept as collections, by each film's folder, as they were when they were taken apart: John,
                        # 18 Nov 2026, "Remove them from normal collections when they were as the others do")
                        CGFIX_ = load(os.path.expanduser('~/Grifflix/criterion-genres-fixed.json'), {}) or {}
                        SETF_ = {f__: n__ for n__, fs__ in (load(os.path.expanduser('~/Grifflix/criterion-set-members.json'), {}) or {}).items() for f__ in fs__}
                        for f_ in load(os.path.expanduser('~/Grifflix/criterion-list.json'), []) or []:
                            if '/films/' in str(f_.get('link') or ''):
                                ANTH_.setdefault(fold(f_.get('title')), f_)
                                # (a release of two films named for both, "Jimi Plays Monterey & Shake! Otis at Monterey": each film, kept on its own,
                                # one of it: John, 21 Nov 2026, "Can the Monterey pop box set be dealt with and marked as complete")
                                if ' & ' in str(f_.get('title') or ''):
                                    for h_ in str(f_['title']).split(' & '):
                                        HALF_.setdefault(fold(h_), f_)
                        for i in raw:
                            if i['Id'] in items:
                                sp = spine_of(i.get('Path'), key == 'tv')
                                o_ = items[i['Id']]
                                for k_ in ('spine', 'cdir', 'ccty', 'ctags', 'cset', 'csetsp', 'csetimg', 'csetfimg', 'csetdesc', 'csetmiss', 'csetfeat'):
                                    o_.pop(k_, None)
                                if sp:
                                    o_['spine'] = sp
                                d_, c_, t_, s_, sl0_ = CL.get(int(sp), ('', '', [], [], '')) if sp and str(sp).isdigit() else ('', '', [], [], '')
                                if sp and str(sp).isdigit() and len(CLL_.get(int(sp)) or []) > 1:
                                    # (a spine two films share: the one of this film's name, or else its year, not the first listed: Part I was taken
                                    # for Part II and shown missing from its box set: John, 22 Nov 2026, "Ivan the terrible is in the collection but it
                                    # still says it's missing")
                                    fn2_ = fold(re.sub(r'\s*\((19|20)\d\d\)\s*$', '', os.path.basename(os.path.dirname(i.get('Path') or '')).strip()))
                                    y2_ = i.get('ProductionYear') or 0
                                    cs_ = CLL_[int(sp)]
                                    pk2_ = next((c for c in cs_ if c[0] in (fold(i.get('Name')), fn2_)), None) or next((c for c in cs_ if c[1].isdigit() and abs(int(c[1]) - y2_) <= 1), None)
                                    if pk2_:
                                        d_, c_, t_, s_, sl0_ = pk2_[2]
                                if not (d_ or t_) and key == 'movies':
                                    y_ = i.get('ProductionYear') or 0
                                    fn_ = fold(re.sub(r'\s*\((19|20)\d\d\)\s*$', '', os.path.basename(os.path.dirname(i.get('Path') or '')).strip()))   # (its folder's name too, where the media server named it otherwise: Anthem taken for "Echo Anthem")
                                    d_, c_, t_, s_, sl0_ = next((CT[(n_, y_ + dy)] for n_ in (fold(i.get('Name')), fn_) for dy in (0, 1, -1) if (n_, y_ + dy) in CT), None) or CTT.get(str((i.get('ProviderIds') or {}).get('Tmdb') or '-')) or ('', '', [], [], '')
                                if not (d_ or t_) and key == 'tv':
                                    # (a series by its name and year, as a film is (Agnès de ci de là Varda, one of Criterion's films, in The Complete
                                    # Films of Agnès Varda); or a series that is a box set of Criterion's whole, its episodes Criterion's films (Small
                                    # Axe: Mangrove, Lovers Rock…): in that set, and its films all held: John, 22 Nov 2026, "hasn't been added")
                                    y_ = i.get('ProductionYear') or 0
                                    sn_ = fold(re.sub(r'\s*\((19|20)\d\d\)\s*$', '', i.get('Name') or ''))
                                    # (or by its folder's name, where TMDB names it otherwise: "Agnès Varda: From Here to There" is Criterion's Agnès
                                    # de ci de là Varda, once it had its IMDb number: John, 22 Nov 2026)
                                    sf_ = fold(re.sub(r'\s*\((19|20)\d\d\)\s*$', '', os.path.basename((i.get('Path') or '').rstrip('/'))))
                                    d_, c_, t_, s_, sl0_ = next((CT[(n_, y_ + dy)] for n_ in (sn_, sf_) for dy in (0, 1, -1) if (n_, y_ + dy) in CT), None) or ('', '', [], [], '')
                                    if not (d_ or t_) and sf_ in SNAME:
                                        sn_ = sf_
                                    if not (d_ or t_) and sn_ in SNAME:
                                        bx_ = SNAME[sn_]
                                        mem_ = next((v_ for k_, v_ in (ctj_.get('sets') or {}).items() if fold(k_) == sn_), [])
                                        if mem_:
                                            s_ = [bx_]
                                            t_ = sorted(set(x_ for m_ in mem_ for x_ in TG.get(m_, [])))
                                            OWNED.update(mem_)
                                            d_ = next((str(f_.get('director') or '')[:160] for f_ in load(os.path.expanduser('~/Grifflix/criterion-list.json'), []) or []
                                                       if str(f_.get('link') or '').endswith('/' + mem_[0])), '')
                                            if not sp and str(SMETA.get(sn_, ('',))[0]).isdigit():
                                                sp = int(SMETA[sn_][0])
                                                o_['spine'] = sp
                                # (Criterion's own year for its films, shown whatever TMDB dates it: Clean, Shaven is 1993 at Criterion, 1995 on
                                # TMDB: John, 20 Nov 2026, "keep criterion film years". Only for the film's own Criterion listing, not a set's or an
                                # anthology's, whose year isn't each film's)
                                cy_ = FINFO.get(sl0_, ('', '', ''))[1] if sl0_ else ''
                                if re.match(r'^\d{4}$', str(cy_)) and o_.get('year') and int(cy_) != o_.get('year'):
                                    o_['ytmdb'] = o_['year']
                                    o_['year'] = int(cy_)
                                fd_ = os.path.dirname(i.get('Path') or '')
                                af_ = ANTH_.get(fold(re.sub(r'\s*\((19|20)\d\d\)\s*$', '', os.path.basename(fd_)))) if key == 'movies' and FOLDN_.get(fd_, 0) > 1 else None
                                if not af_ and key == 'movies':   # (or its films each in a folder of their own inside the release's: "Jimi Plays Monterey &
                                    # Shake! Otis at Monterey (1986)/Jimi Plays Monterey (1987)/…": John, 21 Nov 2026, "have both but are separate files")
                                    up_ = os.path.dirname(fd_)
                                    af_ = ANTH_.get(fold(re.sub(r'\s*\((19|20)\d\d\)\s*$', '', os.path.basename(up_)))) if FOLDP_.get(up_, 0) > 1 and os.path.basename(fd_).lower() not in XDIRS else None   # (not a film's own extras folder)
                                if not af_ and key == 'movies' and not sl0_:   # (one of a release's two films, in a folder of its own)
                                    af_ = HALF_.get(fold(i.get('Name'))) or HALF_.get(fold(re.sub(r'\s*\((19|20)\d\d\)\s*$', '', os.path.basename(fd_))))
                                    if af_ and not (str(af_.get('year') or '').isdigit() and abs(int(af_['year']) - (i.get('ProductionYear') or 0)) <= 2):
                                        af_ = None   # (only of its own years: not another film of the same name)
                                if af_ and fold(af_.get('title')) != fold(i.get('Name')):
                                    an_ = str(af_.get('title') or '')
                                    SMETA.setdefault(fold(an_), (str(af_.get('spine') or ''), str(af_.get('img') or '')))
                                    SNAME.setdefault(fold(an_), an_)
                                    s_ = list(s_) + ([an_] if an_ not in s_ else [])
                                    if not sp and str(af_.get('spine') or '').isdigit():
                                        sp = int(af_['spine'])
                                        o_['spine'] = sp
                                    d_ = d_ or str(af_.get('director') or '')[:160]
                                    c_ = c_ or str(af_.get('country') or '')[:60]
                                    sl0_ = sl0_ or str(af_.get('link') or '').replace('https://www.criterion.com/films/', '')
                                sm_ = SETF_.get(i.get('Path') if key == 'tv' else fd_)
                                if sm_ and sm_ not in s_:
                                    f2_ = ANTH_.get(fold(sm_)) or {}
                                    if f2_:
                                        SMETA.setdefault(fold(sm_), (str(f2_.get('spine') or ''), str(f2_.get('img') or '')))
                                    SNAME.setdefault(fold(sm_), sm_)
                                    s_ = list(s_) + [sm_]
                                    if not sp and str(f2_.get('spine') or '').isdigit():
                                        sp = int(f2_['spine'])
                                        o_['spine'] = sp
                                    d_ = d_ or str(f2_.get('director') or '')[:160]
                                    c_ = c_ or str(f2_.get('country') or '')[:60]
                                if sl0_:
                                    OWNED.add(sl0_)
                                for x_ in s_:
                                    SFILM.setdefault(x_, i['Id'])
                                if True:
                                    if d_:
                                        o_['cdir'] = d_
                                    if c_:
                                        o_['ccty'] = c_
                                    if t_ and sl0_ in CGFIX_:   # (its own genres as Criterion lists it, not its whole box set's (give_genres))
                                        t_ = [x_ for x_ in t_ if x_ not in CRIT_GENRES] + CGFIX_[sl0_]
                                    elif t_ and s_:   # (one only ever shown by Criterion in its box set: the set's genres and languages aren't its
                                        # own (Agnès de ci de là Varda, a documentary series, had the whole Varda set's Comedy, Fantasy, Musical and
                                        # English Language): its own genres where it has them, else none: John, 22 Nov 2026)
                                        own_ = [x_ for x_ in (o_.get('genres') or []) if x_ in CRIT_GENRES]
                                        t_ = [x_ for x_ in t_ if x_ not in CRIT_GENRES and not x_.endswith(' Language')] + own_
                                    if t_:
                                        o_['ctags'] = t_
                                    if s_:   # (each box set once: two spellings of one set's name (its ’ and ') made it twice, each film counted twice in it:
                                        # John, 20 Nov 2026, "The signifyin works of Marlon Riggs isn't showing up properly")
                                        s_ = [x_ for n_, x_ in enumerate(s_) if snm(x_) not in [snm(y_) for y_ in s_[:n_]]]
                                        o_['cset'] = [snm(x_) for x_ in s_]
                                        o_['csetsp'] = [str((ctj_.get('setspine') or {}).get(x_) or SMETA.get(fold(x_), ('', ''))[0] or '') for x_ in s_]   # (each box set's spine, beside its name)
                                        o_['csetimg'] = [str(SMETA.get(fold(x_), ('', ''))[1] or (ctj_.get('setimg') or {}).get(x_) or '') for x_ in s_]   # (and its cover)
                                        if any(((ctj_.get('setfimg') or {}).get(x_) or {}).get(sl0_) for x_ in s_):   # (and its own cover as it is in each set: John, 20 Nov 2026)
                                            o_['csetfimg'] = [str(((ctj_.get('setfimg') or {}).get(x_) or {}).get(sl0_) or '') for x_ in s_]
                                        dsc_ = {x_: (ctj_.get('setdesc') or {}).get(x_) for x_ in s_ if x_ not in SDONE and (ctj_.get('setdesc') or {}).get(x_)}
                                        if dsc_:   # (its description, carried by one of its films only, not repeated on each)
                                            o_['csetdesc'] = {snm(k__): v__ for k__, v__ in dsc_.items()}
                                            ft_ = {snm(k__): (ctj_.get('setfeat') or {}).get(k__) for k__ in dsc_ if (ctj_.get('setfeat') or {}).get(k__)}
                                            if ft_:   # (and its special features, with it)
                                                o_['csetfeat'] = ft_
                                            SDONE.update(dsc_)
                    # (a film's second disc, " - part2" beside its " - part1": part of that film, not a film of its own, though the media
                    # server lists it alone till its next whole look: By Brakhage, Volume Two, 18 Nov 2026)
                    for i in raw:
                        m__ = re.match(r'^(.*?)[ _.-]*(?:part|pt|cd|dis[ck])[ _.-]*([2-9])(\.\w+)$', os.path.basename(i.get('Path') or ''), re.I)
                        if m__ and i['Id'] in items and any(re.match(re.escape(m__.group(1)) + r'[ _.-]*(?:part|pt|cd|dis[ck])[ _.-]*1\.', os.path.basename(x.get('Path') or ''), re.I)
                                                            and os.path.dirname(x.get('Path') or '') == os.path.dirname(i.get('Path') or '') for x in raw):
                            items.pop(i['Id'], None)
                    lists[key] = [i['Id'] for i in raw if i['Id'] in items]
                    if key == 'movies':   # (who released each film, and whose library it's in now: John, 21 Nov 2026)
                        try:
                            dist_tags(g, items)
                        except Exception as e:
                            log('Distributors on the site: %s' % str(e)[:200])
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
                elif miss_ and len(miss_) == len(mem_):   # (a box set none of whose films is here yet, for the admin page's list: John,
                    # 19 Nov 2026, "Can it also list box sets not started")
                    sp_ = str((ctj_.get('setspine') or {}).get(nm_) or (SMETA.get(fold(snm(nm_)), ('', ''))[0] if 'SMETA' in dir() else '') or '')
                    lists.setdefault('csetsnone', []).append({'n': snm(nm_), 'sp': sp_, 'f': [list(m_) for m_ in miss_]})
            sets = [s for s in g.items(Recursive='true', IncludeItemTypes='BoxSet', SortBy='SortName', Fields=quick).get('Items', []) if not owner_only_set(s.get('Name'))]   # (John's own collections: not in the site's files, which anyone can read)
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
            # (a box set kept inside The Criterion Collection (By Brakhage, Hollis Frampton, Scorsese Shorts…): its films listed there one by
            # one, each with the set as its box set, as Criterion's other box sets are, and the set itself not an entry of its own: John,
            # 18 Nov 2026, "So long as they are treated as other box sets in the criterion collection they don't need to be separate
            # grifflix collections")
            crit_ = next((c for c in sets if (c.get('Name') or '').strip().lower() == 'the criterion collection'), None)
            if crit_:
                cl_ = {}
                for f_ in load(os.path.expanduser('~/Grifflix/criterion-list.json'), []) or []:
                    cl_.setdefault(fold(f_.get('title')), f_)
                new_ = []
                for m_ in members.get(crit_['Id'], []):
                    if (items.get(m_) or {}).get('type') != 'collection':
                        new_.append(m_)
                        continue
                    nm_ = items[m_].get('name') or ''
                    f_ = cl_.get(fold(nm_)) or {}
                    for k_ in members.get(m_, []):
                        o_ = items.get(k_)
                        if not o_:
                            continue
                        if nm_ not in (o_.get('cset') or []):
                            o_['cset'] = (o_.get('cset') or []) + [nm_]
                            o_['csetsp'] = (o_.get('csetsp') or [''] * (len(o_['cset']) - 1)) + [str(f_.get('spine') or '')]
                            o_['csetimg'] = (o_.get('csetimg') or [''] * (len(o_['cset']) - 1)) + [str(f_.get('img') or '')]
                        if not o_.get('spine') and str(f_.get('spine') or '').isdigit() and '/films/' in str(f_.get('link') or ''):
                            o_['spine'] = int(f_['spine'])
                        for kk_, vv_ in (('cdir', 'director'), ('ccty', 'country')):
                            if not o_.get(kk_) and f_.get(vv_):
                                o_[kk_] = str(f_[vv_])[:160]
                        if k_ not in new_:
                            new_.append(k_)
                members[crit_['Id']] = list(dict.fromkeys(new_))
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
            need = [it['Id'] for it in quick if not it.get('Etag') or (cache.get(it['Id']) or {}).get('e') != it.get('Etag') or (cache.get(it['Id']) or {}).get('p') != it.get('Path')
                    or 'hdrt' not in ((cache.get(it['Id']) or {}).get('r') or {})]   # (read once more for which HDR it is)
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
                        # (HDR, as the converter judges it (convert.py is_hdr): it can't be made smaller here without spoiling its colours, so it's
                        # one to replace: John, 20 Nov 2026, Wild at Heart, 4K HDR10, passed over: "any like that, that the server won't be able to
                        # handle should be immediately flagged to replace with the reason, as they have been added in error")
                        # (Dolby Vision alone, no HDR10 beneath ("DOVI"), told apart: that one can't be converted, the rest can on John's Mac)
                        'hdrt': ('Dolby Vision only' if (v.get('VideoRangeType') or '').upper() == 'DOVI' else 'Dolby Vision' if 'dovi' in (v.get('VideoRangeType') or '').lower() else (v.get('VideoRangeType') or 'HDR'))
                               if ((v.get('VideoRange') or '').upper() == 'HDR' or (v.get('ColorTransfer') or '').lower() in ('smpte2084', 'arib-std-b67')
                                   or 'dovi' in (v.get('VideoRangeType') or '').lower()) else '',
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
            if b and b.get('op') == 'cxclear':   # (the Cancelled list cleared: still cancelled, never fetched, only not listed: John, 21 Nov
                # 2026, "I don't need to see cancelled requests always. Let me clear them")
                nf_ = os.path.join(home, 'fetch-cancelled-names.json')
                names_ = load(nf_, {}) or {}
                for v_ in names_.values():
                    if isinstance(v_, dict):
                        v_['hid'] = 1
                save(nf_, names_)
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
            if b and b.get('peek'):   # (what's new at TorBox and Google Drive, looked at now, any time, a download going on or not: gdrive.py's
                # look-only check, its own list kept apart; the download in progress goes on: John, 21 Nov 2026, "Can I have a check for
                # upcoming downloads option on Torbox")
                if not setup:
                    return self.send_json(400, {'error': 'Google Drive and TorBox aren’t set up on the Mac mini yet.'})
                pr = PEEK.get('p')
                if not (pr and pr.poll() is None):
                    import subprocess
                    PEEK['p'] = subprocess.Popen([sys.executable, os.path.join(home, 'gdrive.py'), 'check'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=home)
                    PEEK['at'] = time.time()
                return self.send_json(200, {'ok': True, 'peeking': True})
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
            # (one John cancelled gone from the list at once, not at the downloader's next look: John, 20 Nov 2026, "I just cancelled a download
            # but it's still listed")
            cx_ = set(x for x in (load(os.path.join(home, 'fetch-cancelled.json'), []) or []) if isinstance(x, str))
            pk = st.get('peek') or {}
            if pk.get('at') and pk['at'] > (st.get('checked') or 0):   # (a look-only check since the run's own: what it found, added)
                have_ = set(x.get('file') for x in st.get('waiting') or [])
                st['waiting'] = (st.get('waiting') or []) + [dict(x, new=1) for x in pk.get('waiting') or [] if x.get('file') not in have_]
                st['attorbox'] = pk.get('attorbox') or []
                hu_ = set(x.get('key') for x in st.get('upgrades') or [])
                st['upgrades'] = (st.get('upgrades') or []) + [x for x in pk.get('upgrades') or [] if x.get('key') not in hu_]
            # (each download's Criterion title, by TMDB's number for it, whatever it's called: "Il vangelo secondo Matteo" is The Gospel
            # According to Matthew, so it's off the admin page's films to get: John, 21 Nov 2026)
            ctt_ = crit_by_tmdb()
            nm_ = load(os.path.join(home, 'fetch-names.json'), {}) or {}
            for x_ in (st.get('waiting') or []) + (st.get('recent') or []) + ([now] if now else []):
                t_ = str(x_.get('tid') or (nm_.get(x_.get('key') or '') or nm_.get(x_.get('file') or '') or {}).get('tmdb') or '')
                if t_ and t_ in ctt_:
                    x_['crit'] = ctt_[t_]
            waiting = [x for x in (st.get('waiting') or []) if not (now and x.get('file') == now.get('file'))
                       and not (x.get('key') in cx_ or x.get('file') in cx_ or (x.get('file') or '').strip('/') in cx_)]
            return self.send_json(200, {'setup': setup, 'running': running, 'now': now, 'waiting': waiting,
                                        'recent': [x for x in st.get('recent') or [] if x.get('ok')],   # (only what came: John, 15 Nov 2026) 'checked': st.get('checked'), 'next': int(GDRIVE_NEXT[0]) or None, 'error': GDRIVE_ERR[0], 'trouble': st.get('trouble') or [], 'hold': st.get('hold'),
                                        'attorbox': st.get('attorbox') or [],   # (still coming in at TorBox: 20 Nov 2026)
                                        'peeked': pk.get('at') or None, 'peeking': bool(PEEK.get('p') and PEEK['p'].poll() is None),
                                        'hold': st.get('hold'),   # (the film it waits on to be repackaged: admin says so, not "nothing has come": 20 Nov 2026)
                                        'upgrades': st.get('upgrades') or [], 'chosen': load(os.path.join(home, 'fetch-upgrades.json'), {}) or {},
                                        'cancelled': [dict(v, key=k) for k, v in (load(os.path.join(home, 'fetch-cancelled-names.json'), {}) or {}).items() if isinstance(v, dict) and not v.get('hid')],
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

        def api_sound(self, me, user, q, b):
            """A film's soundtracks and commentaries, listed on its page (John, 18 Nov 2026: "Audio tracks need to be listed on a titles page too")."""
            iid = str(q.get('id') or '')
            if not re.match(r'^[0-9a-f]{32}$', iid):
                return self.send_json(400, {'error': 'Which film?'})
            it = g.one(g.now_id(iid))
            ms = (it.get('MediaSources') or [{}])[0]
            return self.send_json(200, {'audio': sound_list([s for s in ms.get('MediaStreams', []) if s.get('Type') == 'Audio'])})

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
            conf = load(os.path.expanduser('~/Grifflix/criterion.json'), {}) or {}
            links = {'criterion': ('https://docs.google.com/spreadsheets/d/%s/edit' % conf['sheet']) if conf.get('sheet') else '',
                     'missing': ('https://docs.google.com/spreadsheets/d/%s/edit' % conf['missing_sheet']) if conf.get('missing_sheet') else ''}
            if q.get('links'):   # (the Google Sheets, to open from the Criterion tab: John, 18 Nov 2026)
                return self.send_json(200, dict(links, sheetjob=MSHEET.get('state'), sheeterr=MSHEET.get('error'), sheetat=MSHEET.get('at')))
            if b and b.get('op') == 'sheet':   # (made or brought up to date in Google Sheets, in the background: a minute at most)
                if MSHEET.get('state') != 'making':
                    MSHEET.clear(); MSHEET.update(state='making', at=int(time.time()))

                    def mk():
                        import subprocess, tempfile
                        try:
                            rows = crit_missing(g)
                            f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
                            json.dump(rows, f); f.close()
                            r = subprocess.run([sys.executable, os.path.expanduser('~/Grifflix/criterion.py'), 'missing', f.name], capture_output=True, text=True, timeout=300,
                                               cwd=os.path.expanduser('~/Grifflix'), stdin=subprocess.DEVNULL)
                            os.remove(f.name)
                            out = ([l for l in (r.stdout or '').splitlines() if l.startswith('{')] or [''])[-1]
                            if not out:
                                raise RuntimeError(((r.stdout or '') + (r.stderr or '')).strip()[-200:] or 'it didn’t say why')
                            MSHEET.update(state='done', at=int(time.time()), **json.loads(out))
                            log('Criterion box set films missing: the Google Sheet brought up to date (%s rows)' % MSHEET.get('rows'))
                        except Exception as e:
                            MSHEET.update(state='failed', error=str(e)[:200], at=int(time.time()))
                    threading.Thread(target=mk, daemon=True).start()
                return self.send_json(200, dict(links, sheetjob='making'))
            if q.get('films'):   # (and Criterion's films not in any box set that Grifflix hasn't: John, 24 Nov 2026; kept ready, not worked out each time)
                kept = load(CMF_AT, None)
                if not kept or q.get('fresh'):
                    crit_missing_now()
                    kept = load(CMF_AT, None) or {}
                return self.send_json(200, {'films': kept.get('films') or [], 'at': kept.get('at')})
            return self.send_json(200, dict(links, rows=crit_missing(g)))

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
                return self.send_json(200, {'collections': self.collections_out(False, me.get('owner'))})
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
            o = self.collection_out(q.get('id', ''), user['progress'], False, me.get('owner'))
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
            vs = [s for s in ms.get('MediaStreams', []) if s.get('Type') == 'Video']
            aus = [s for s in ms.get('MediaStreams', []) if s.get('Type') == 'Audio']
            # (a soundtrack chosen in the player, such as a commentary: the stream with that one, as the whole file plays only its first:
            # John, 18 Nov 2026, "can mp4s play extra tracks like this then? How do we choose them?")
            aud = str(q.get('audio') or '')
            pick = next((a for a in aus if str(a.get('Index')) == aud), None) if aud.isdigit() else None
            if pick and aus and pick is not aus[0]:
                hq['AudioStreamIndex'] = pick['Index']
            else:
                pick = None
            hls = ('/v/%s/master.m3u8?' % iid) + urllib.parse.urlencode(hq)
            direct = None
            # (the whole file, when it's an MP4 whose picture and first sound this device plays as they are: the surest way to play
            # anywhere. AC3 and E-AC3 only where the device says it plays them (Safari does, Chrome doesn't): John, 12 Nov 2026)
            snd = ('aac', 'mp3') + (('ac3', 'eac3') if q.get('ac3') == '1' else ())
            if not pick and not q.get('nodirect') and any(c in ('mp4', 'm4v', 'mov') for c in (ms.get('Container') or '').split(',')) and vs and plays \
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
                                 'audio': sound_list(aus), 'aud': pick['Index'] if pick else (aus[0].get('Index') if aus else None),
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
            SUBS.SUBDL['key'] = (g.st.get('subdl') or {}).get('key') or ''   # (Subdl too, when John has given its key: a second place to look)
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

        def api_afi(self, me, user, q, b):
            """John's only: AFI Catalog pages found by his own browser on AFI's site (the admin page's bookmark), for any film
            that Wikidata and Wikipedia gave none for (John, 21 Nov 2026, "A bookmarklet would be the best solution"). GET: those films, for
            the bookmark; POST {afi: {imdb: AFI's number}}: kept with the rest (afi-ids.json), the site updated."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            af = os.path.expanduser('~/Grifflix/afi-ids.json')
            A = load(af, {}) or {}
            got = 0
            if b and isinstance(b.get('afi'), dict):
                for k, v in b['afi'].items():
                    if re.match(r'^tt\d{6,10}$', str(k)) and re.match(r'^\d{1,7}$', str(v)):
                        A[str(k)] = {'id': str(v), 'at': int(time.time()), 'w': 1, 'from': 'afi-bookmark'}
                        got += 1
                save(af, A)
                g.forget()
                g.publish_soon = time.time() + 60
                log('AFI Catalog pages: %d films, by John’s browser' % got)
            if b and isinstance(b.get('none'), dict):   # (what AFI's search showed for each film it didn't find: kept to look into misses)
                nf_ = os.path.expanduser('~/Grifflix/afi-notfound.json')
                NF = load(nf_, {}) or {}
                for k, v in list(b['none'].items())[:3000]:
                    if re.match(r'^tt\d{6,10}$', str(k)) and isinstance(v, dict):
                        NF[str(k)] = {'saw': [str(x)[:200] for x in (v.get('saw') or [])[:5]], 'q': [str(x)[:200] for x in (v.get('q') or [])[:3]], 'at': int(time.time())}
                save(nf_, NF)
                record('AFI pages linked', films=got, by='John’s browser (the bookmark)')
            adf = os.path.expanduser('~/Grifflix/afi-details.json')
            AD = load(adf, {}) or {}
            if b and isinstance(b.get('det'), dict):   # (each film's AFI page as read, and what's sorted from it)
                n_ = 0
                for k, v in b['det'].items():
                    if re.match(r'^tt\d{6,10}$', str(k)) and isinstance(v, dict) and v.get('x') and 'NARROW RESULTS' not in str(v['x'])[:3000]:
                        x_ = str(v['x'])[:120000]
                        AD[str(k)] = {'id': str(v.get('id') or ''), 'x': x_, 'f': afi_parse(x_), 'at': int(time.time())}
                        n_ += 1
                save(adf, AD)
                g.forget()
                g.publish_soon = time.time() + 120
                log('AFI Catalog details: %d films, by John’s browser' % n_)
                record('AFI details read', films=n_, by='John’s browser (the bookmark)')
            lid = (g.st.get('libraries') or {}).get('movies')
            out, start, linked, need, pri = [], 0, 0, [], {}
            while lid:
                r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'ProviderIds,ProductionLocations,OriginalTitle',
                                                                       'StartIndex': start, 'Limit': 500, 'EnableImages': 'false'}, timeout=120) or {}
                for it in r.get('Items') or []:
                    t = str((it.get('ProviderIds') or {}).get('Imdb') or '')
                    y = int(it.get('ProductionYear') or 0)
                    us = not it.get('ProductionLocations') or any('united states' in str(c).lower() for c in it['ProductionLocations'])
                    # (every film, of any year or country: AFI has later films and some made elsewhere, Chariots of Fire: John, 21 Nov 2026,
                    # "There are later films than 1993 in the afi catalog so the search should be widened"; American films to 1993 asked first)
                    if not re.match(r'^tt\d{6,10}$', t):
                        continue
                    if (A.get(t) or {}).get('id'):
                        linked += 1
                        if t not in AD or 'NARROW RESULTS' in (AD[t].get('x') or '')[:3000]:   # (or what's kept is AFI's search page, not its)
                            need.append([t, A[t]['id']])
                    else:
                        out.append([t, it.get('Name') or '', y] + ([it['OriginalTitle']] if it.get('OriginalTitle') and it['OriginalTitle'] != it.get('Name') else []))
                        pri[t] = 0 if us and 1893 <= y <= 1993 else 1 if us else 2
                start += 500
                if start >= (r.get('TotalRecordCount') or 0):
                    break
            out.sort(key=lambda x: (pri.get(x[0], 2), x[2]))
            if not g.st.get('afibm_key'):
                g.st['afibm_key'] = secrets.token_urlsafe(18)
                save(SETTINGS, g.st)
            return self.send_json(200, {'missing': out, 'linked': linked, 'got': got, 'need': need, 'read': len(AD), 'bm': g.st['afibm_key'],
                                        'ask': [x[0] for x in out if pri.get(x[0]) == 0]})

        def api_distributors(self, me, user, q, b):
            """John's only: each film's US distributor, as Box Office Mojo gives it, read by his own browser (the admin page's bookmark) and
            kept here (John, 20 Nov 2026, "can we find a database that has the info?" ... "make a bookmarklet for this"): GET, the films' IMDb
            numbers for the bookmark and what's known so far; POST {dist: {imdb: distributor}}, kept (added to what was kept)."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            at = os.path.expanduser('~/Grifflix/distributors.json')
            D = load(at, {}) or {}
            if b and isinstance(b.get('dist'), dict):
                # (everything the bookmark read from each film's page kept beside it, in distributors-full.json: its summary, takings and
                # tables, so it needn't be read again: John, 20 Nov 2026)
                fa = os.path.expanduser('~/Grifflix/distributors-full.json')
                F = load(fa, {}) or {}
                for k, v in b['dist'].items():
                    if not re.match(r'^tt\d{6,10}$', str(k)):
                        continue
                    if isinstance(v, dict):
                        F[str(k)] = v
                        v = ((v.get('f') or {}).get('Domestic Distributor') or '')
                    D[str(k)] = re.sub(r'\s+', ' ', str(v or '')).strip()[:120]
                save(at, D)
                save(fa, F)
                log('Distributors: %d films from Box Office Mojo, by John’s browser' % len(b['dist']))
            libs = g.st.get('libraries', {})
            films = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': libs.get('movies'), 'Recursive': 'true', 'IncludeItemTypes': 'Movie',
                                                                    'Fields': 'ProviderIds,ProductionYear'}).get('Items', []) if libs.get('movies') else []
            by = {}
            for f in films:
                t = str((f.get('ProviderIds') or {}).get('Imdb') or '')
                if re.match(r'^tt\d{6,10}$', t):
                    by.setdefault(t, []).append([f.get('Name') or '', f.get('ProductionYear') or '', f['Id']])
            groups = {}
            for t, fs in by.items():
                if D.get(t):
                    groups.setdefault(D[t], []).extend(fs)
            out = sorted(([k, len(v), sorted(v, key=lambda x: (str(x[0]).lower()))] for k, v in groups.items()), key=lambda x: (-x[1], x[0].lower()))
            return self.send_json(200, {'ids': sorted(by), 'checked': sum(1 for t in by if t in D), 'found': sum(1 for t in by if D.get(t)),
                                        'total': len(by), 'noimdb': len(films) - sum(len(v) for v in by.values()), 'dist': out})

        def api_picsubs(self, me, user, q, b):
            """John's only: films whose picture subtitles were saved beside them when converted (convert.py, picture-subs.json), waiting to be
            turned into text as a job of their own (John, 20 Nov 2026)."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            P = load(os.path.expanduser('~/Grifflix/picture-subs.json'), {}) or {}
            L = sorted(({'id': k, 'name': v.get('name'), 'year': v.get('year'), 'text': bool(v.get('text')), 'at': v.get('at'),
                         'subs': [{'name': x.get('name'), 'kind': x.get('kind'), 'forced': x.get('forced'), 'sdh': x.get('sdh')} for x in v.get('subs') or []]}
                        for k, v in P.items()), key=lambda x: -(x.get('at') or 0))
            return self.send_json(200, {'films': L, 'waiting': sum(1 for x in L if not x['text'])})

        def api_unmatched(self, me, user, q, b):
            """John's only: the films with no IMDb number (never matched on TMDB, or matched to a TMDB film without one), with what the
            automatic matching found for each (match-unmatched.json), for the admin page's list to search and fix by hand (John, 20 Nov
            2026, "I need an unmatched list in admin where I can search and fix")."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            lid = (g.st.get('libraries') or {}).get('movies')
            if not lid:
                return self.send_json(200, {'films': []})
            T = load(os.path.expanduser('~/Grifflix/match-unmatched.json'), {}) or {}
            out, start = [], 0
            while True:
                r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path,ProviderIds,ProductionYear',
                                                                       'StartIndex': start, 'Limit': 500, 'SortBy': 'SortName', 'EnableImages': 'false'}, timeout=120) or {}
                for it in r.get('Items') or []:
                    pid = it.get('ProviderIds') or {}
                    if pid.get('Imdb'):
                        continue
                    t = T.get(it['Id']) if isinstance(T.get(it['Id']), dict) else {}
                    p_ = it.get('Path') or ''
                    out.append({'id': it['Id'], 'name': it.get('Name'), 'year': it.get('ProductionYear') or '', 'tmdb': pid.get('Tmdb') or '',
                                'folder': os.path.basename(os.path.dirname(p_)), 'file': os.path.basename(p_), 'mins': round((it.get('RunTimeTicks') or 0) / 6e8),
                                'note': t.get('note') or '', 'saw': t.get('saw') or [], 'tried': t.get('at') or 0})
                start += 500
                if start >= (r.get('TotalRecordCount') or 0):
                    break
            return self.send_json(200, {'films': out})

        def api_subprep(self, me, user, q, b):
            if b:   # (John's changes written one at a time with John's Mac's)
                with SP_LOCK:
                    return self.api_subprep_(me, user, q, b)
            return self.api_subprep_(me, user, q, b)

        def api_subprep_(self, me, user, q, b):
            """John's only: the English subtitles being made ready on his Mac: how many, those to check, those none could be found for;
            POST op pause, resume, look (the list looked over now) or again (one film tried again)."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            P = load(SUBPREP, {}) or {}
            if re.match(r'^[0-9a-f]{32}$', str(q.get('evidence') or '')):   # (one film's: what Whisper heard, and the subtitle lines matched to it;
                # with i, one of the likeliest three turned down)
                i_ = str(q.get('i') or '')
                ev_ = load(os.path.join(os.path.expanduser('~/Grifflix/subprep-evidence'), q['evidence'] + ('.%s' % i_ if re.match(r'^[0-2]$', i_) else '') + '.json'), None)
                return self.send_json(200, ev_ or {'parts': []})
            if re.match(r'^[0-9a-f]{32}$', str(q.get('search') or '')):   # (English subtitles for one film on OpenSubtitles, for John to give one)
                iid_ = q['search']
                it_ = jf_has(g, iid_, 'Path,ProviderIds,OriginalTitle')
                if not it_.get('Id'):   # (a film no longer under this id (renamed since: it has its own entry now): taken off, and said so)
                    if P.pop(iid_, None) is not None:
                        save(SUBPREP, P)
                    return self.send_json(410, {'error': 'That film isn’t in the library under this entry any more (renamed or replaced since): it’s been taken off the list. Reload the page.'})
                pid_ = it_.get('ProviderIds') or {}
                e_ = P.get(iid_) or {}
                try:
                    if e_.get('kind') == 'episode':
                        R_ = self.subs_client().search('en', query=e_.get('series'), parent_tmdb=e_.get('series_tmdb'), parent_imdb=e_.get('series_imdb'), alt=[e_.get('series_orig')], season=e_.get('season'), episode=e_.get('episode'), hint=os.path.basename(it_.get('Path') or ''), also=True)
                    else:
                        R_ = self.subs_client().search('en', tmdb=pid_.get('Tmdb'), imdb=pid_.get('Imdb'), alt=[it_.get('OriginalTitle')], query=it_.get('Name'), year=it_.get('ProductionYear'), hint=os.path.basename(it_.get('Path') or ''), also=True)
                except Exception as ex:
                    return self.send_json(502, {'error': str(ex)[:200]})
                return self.send_json(200, {'mins': round((it_.get('RunTimeTicks') or 0) / 6e8), 'results': [{k: x.get(k) for k in ('file_id', 'name', 'downloads', 'exact', 'like', 'hi', 'machine', 'fps', 'source')} for x in R_[:40]]})
            if re.match(r'^[0-9a-f]{32}$', str(q.get('compare') or '')):
                return self.send_json(200, subs_compare(g, P, q['compare'], str(q.get('with') or '')))
            if b:
                op = str(b.get('op') or '')
                if op in ('pause', 'resume'):
                    if op == 'pause':
                        P['_paused'] = int(time.time())
                    else:
                        P.pop('_paused', None)
                    save(SUBPREP, P)
                elif op == 'look':
                    threading.Thread(target=subprep_scan, daemon=True).start()
                elif op == 'redoall':   # (every film done so far, done again; its subtitles set aside, not deleted, so the wrong ones stop showing
                    # meanwhile: John, 21 Nov 2026, "every subtitle done so far needs doing again, as even confident checks seem very off")
                    import shutil
                    dest_ = os.path.join(SETASIDE, 'Subtitles redone')
                    n_ = 0
                    for k_, v_ in P.items():
                        if k_.startswith('_') or not isinstance(v_, dict) or v_.get('state') not in ('done', 'check'):
                            continue
                        f_ = str(v_.get('file') or '')
                        if f_.endswith('.srt') and '.Grifflix.' in os.path.basename(f_) and os.path.exists(f_):
                            try:
                                os.makedirs(dest_, exist_ok=True)
                                to_ = os.path.join(dest_, time.strftime('%Y%m%d-%H%M ') + os.path.basename(f_))
                                shutil.move(f_, to_)
                                record('subtitles set aside', film=v_.get('name'), id=k_, moved_from=f_, moved_to=to_, why='John asked for all to be done again')
                                try:
                                    g.jf.call('POST', '/Items/%s/Refresh' % k_, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
                                except Exception:
                                    pass
                            except OSError as e_:
                                log('Subtitles to redo: %s not set aside: %s' % (v_.get('name'), e_))
                        v_.update(state='waiting', note='', redo=int(time.time()))
                        for x_ in ('tried', 'samples', 'chose', 'n', 'miss', 'mins', 'file', 'sure', 'step', 'stepat'):
                            v_.pop(x_, None)
                        n_ += 1
                    save(SUBPREP, P)
                    log('Subtitles: %d films to do again, John asked; their subtitles in Set aside › Subtitles redone' % n_)
                elif op == 'translated' and str(b.get('id') or '') in P:   # (John takes the translated subtitle made for this copy)
                    iid_ = str(b['id'])
                    e_ = P[iid_]
                    fn_ = os.path.join(os.path.expanduser('~/Grifflix/subprep-cands'), '%s.tr.srt' % iid_)
                    if not re.match(r'^[0-9a-f]{32}$', iid_) or not os.path.exists(fn_):
                        return self.send_json(400, {'error': 'That translation isn’t kept any more.'})
                    it_ = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid_), query={'Fields': 'Path'}) or {}
                    if not it_.get('Path'):
                        return self.send_json(400, {'error': 'The film isn’t there.'})
                    out_ = self.subs_write(it_['Path'], 'en', SUBS.parse_srt(open(fn_, encoding='utf-8').read()))
                    g.jf.call('POST', '/Items/%s/Refresh' % iid_, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
                    lg_ = (e_.get('translated') or {}).get('lang') or ''
                    e_.update(state='done', note='the %s subtitle made for this copy, machine-translated (Argos), chosen by John' % lg_, chose='translated from %s' % lg_,
                              file=out_, at=int(time.time()), sure=True, how='machine-translated')
                    save(SUBPREP, P)
                    log('Subtitles: the translated %s one taken for %s, John chose' % (lg_, e_.get('name')))
                    record('subtitles saved', film=e_.get('name'), id=iid_, file=out_, how='the %s subtitle made for this copy, machine-translated, chosen by John' % lg_)
                elif op == 'choose' and str(b.get('id') or '') in P:   # (John's own choice of a subtitle turned down: saved as it'd be timed)
                    iid_, i_ = str(b['id']), str(b.get('i') if b.get('i') is not None else 0)
                    e_ = P[iid_]
                    if not re.match(r'^r?\d$|^g$', i_):
                        return self.send_json(400, {'error': 'Which one?'})
                    c_ = next((c for c in (e_.get('cands') or []) + (e_.get('rcands') or []) if str(c.get('i')) == i_), None)
                    if i_ == 'g':
                        c_ = {'i': 'g', 'name': e_.get('givenname') or 'the ones you gave', 'how': 'given by John', 'n': 0}
                    fn_ = os.path.join(os.path.expanduser('~/Grifflix/subprep-cands'), '%s.%s.srt' % (iid_, i_))
                    if not c_ or not os.path.exists(fn_) or not re.match(r'^[0-9a-f]{32}$', iid_):
                        return self.send_json(400, {'error': 'That subtitle isn’t kept any more: Try again.'})
                    it_ = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid_), query={'Fields': 'Path'}) or {}
                    if not it_.get('Path'):
                        return self.send_json(400, {'error': 'The film isn’t there.'})
                    cues_ = SUBS.parse_srt(open(fn_, encoding='utf-8').read())
                    out_ = self.subs_write(it_['Path'], 'en', cues_)
                    g.jf.call('POST', '/Items/%s/Refresh' % iid_, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
                    e_.update(state='done', note='chosen by John: %s (%d lines agreeing)' % (c_.get('how'), c_.get('n') or 0), chose=c_.get('name'), file=out_, at=int(time.time()),
                              n=c_.get('n'), sure=True, how='chosen', samples=c_.get('samples'), cur=i_, chosen_from=e_.get('how') if e_.get('how') != 'chosen' else e_.get('chosen_from'))
                    save(SUBPREP, P)
                    log('Subtitles chosen by John: %s: %s' % (e_.get('name'), c_.get('name')))
                    record('subtitles saved', film=e_.get('name'), id=iid_, file=out_, how='chosen by John from those turned down: ' + str(c_.get('how')), chose=c_.get('name'))
                elif op == 'give' and str(b.get('id') or '') in P:
                    # (the right subtitles, given by John: chosen from OpenSubtitles' list or a file of his own; used as they are, or timed to
                    # the film: by the subtitle made for this copy if there is one, else by Whisper on his Mac with these alone: John, 22 Nov 2026,
                    # "I also need a way to give the correct subtitles to work with in the admin")
                    iid_ = str(b['id'])
                    e_ = P[iid_]
                    if not re.match(r'^[0-9a-f]{32}$', iid_):
                        return self.send_json(400, {'error': 'Which film?'})
                    try:
                        if b.get('srt'):
                            txt_ = str(b['srt'])[:900000]
                            nm_ = str(b.get('name') or 'a file you gave')[:160]
                        else:
                            fi_ = str(b.get('file_id') or '0')
                            txt_ = SUBS.decode(self.subs_client().download(fi_ if fi_.startswith('sd:') else int(fi_)))
                            nm_ = str(b.get('name') or 'chosen by you')[:160]
                        cues_ = SUBS.parse_srt(txt_)
                    except Exception as ex:
                        return self.send_json(502, {'error': str(ex)[:200]})
                    if len(cues_) < 5:
                        return self.send_json(400, {'error': 'Those subtitles have hardly any lines: is it an .srt file?'})
                    gf_ = os.path.join(os.path.expanduser('~/Grifflix/subprep-cands'), '%s.g.srt' % iid_)
                    os.makedirs(os.path.dirname(gf_), exist_ok=True)
                    with open(gf_, 'w', encoding='utf-8') as f_:
                        f_.write(SUBS.write_srt(cues_))
                    it_ = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid_), query={'Fields': 'Path'}) or {}
                    if not it_.get('Path'):
                        return self.send_json(400, {'error': 'The film isn’t there.'})
                    e_['givenname'] = nm_
                    if b.get('mode') == 'asis':
                        out_ = self.subs_write(it_['Path'], 'en', cues_)
                        e_.update(state='done', how='given', cur='g', note='given by John, used as they are', chose=nm_, file=out_, at=int(time.time()), n=len(cues_), sure=True)
                        e_.pop('givenfile', None)
                        said_ = 'used as they are'
                    else:
                        ref_ = SUBS.parse_srt(open(e_['ref_file'], encoding='utf-8', errors='replace').read()) if e_.get('ref_file') and os.path.exists(e_['ref_file']) else []
                        sh_ = 0
                        if len(ref_) >= 50:
                            sh_, hit_, outc_, how_ = SUBS.align_to_ref(ref_, cues_)
                        if sh_ >= .45:
                            with open(os.path.join(os.path.dirname(gf_), '%s.g.orig.srt' % iid_), 'w', encoding='utf-8') as f_:
                                f_.write(SUBS.write_srt(cues_))
                            with open(gf_, 'w', encoding='utf-8') as f_:
                                f_.write(SUBS.write_srt(outc_))
                            out_ = self.subs_write(it_['Path'], 'en', outc_)
                            e_.update(state='done' if sh_ >= .6 else 'check', how='given', cur='g', note='given by John, %s (the %s subtitle’s)' % (how_, e_.get('ref_lang') or ''),
                                      chose=nm_, file=out_, at=int(time.time()), n=hit_, sure=sh_ >= .6)
                            e_.pop('givenfile', None)
                            said_ = 'timed by the subtitle made for this copy'
                        else:
                            e_.update(state='waiting', givenfile=gf_, order=-2, note='given by John: to be timed by Whisper on your Mac')
                            said_ = 'to be timed by Whisper on your Mac, next'
                    for x_ in ('ref_only', 'prev'):
                        e_.pop(x_, None)
                    try:
                        g.jf.call('POST', '/Items/%s/Refresh' % iid_, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
                    except Exception:
                        pass
                    save(SUBPREP, P)
                    log('Subtitles given by John for %s: %s, %s' % (e_.get('name'), nm_, said_))
                    record('subtitles given', film=e_.get('name'), id=iid_, name=nm_, how=said_)
                elif op == 'giveref' and str(b.get('id') or '') in P:
                    # (a subtitle in another language John uploads, made for his copy: the English timed by it on the Mac mini, and it translated on
                    # his Mac and compared, the translation kept to see and use: John, 22 Nov 2026, "I also need to be able to upload a subtitle and
                    # translate it and try that")
                    iid_ = str(b['id'])
                    e_ = P[iid_]
                    lg_ = re.sub(r'[^a-z]', '', str(b.get('lang') or '').lower())[:3]
                    if not re.match(r'^[0-9a-f]{32}$', iid_) or not lg_:
                        return self.send_json(400, {'error': 'Which language is it in?'})
                    cues_ = SUBS.parse_srt(str(b.get('srt') or '')[:900000])
                    if len(cues_) < 50:
                        return self.send_json(400, {'error': 'That has only %d lines: is it the whole film’s .srt file?' % len(cues_)})
                    rf_ = os.path.join(os.path.expanduser('~/Grifflix/subprep-ref'), '%s.%s.srt' % (iid_, lg_))
                    os.makedirs(os.path.dirname(rf_), exist_ok=True)
                    with open(rf_, 'w', encoding='utf-8') as f_:
                        f_.write(SUBS.write_srt(cues_))
                    try:   # (an old translation isn't this one's)
                        os.remove(os.path.join(os.path.expanduser('~/Grifflix/subprep-cands'), '%s.tr.srt' % iid_))
                    except OSError:
                        pass
                    prev_ = e_.get('prev') if e_.get('ref_only') else {k: e_.get(k) for k in ('state', 'note', 'chose', 'file', 'n', 'miss', 'sure', 'samples', 'tried', 'mins', 'at', 'how', 'cur')}
                    for x_ in ('givenfile', 'ref_tried2', 'translated'):
                        e_.pop(x_, None)
                    e_.update(ref_file=rf_, ref_lang=lg_, ref_given=str(b.get('name') or 'your file')[:160], ref_only=True, prev=prev_, state='waiting', order=-2,
                              note='your %s subtitle: to be tried, and translated on your Mac' % lg_)
                    save(SUBPREP, P)
                    log('Subtitles: John gave a %s subtitle for %s, to time the English by and translate' % (lg_, e_.get('name')))
                    record('subtitle in another language given', film=e_.get('name'), id=iid_, lang=lg_, name=e_['ref_given'])
                elif op == 'confirm' and str(b.get('id') or '') in P:   # (John has looked, and its timing is right: done)
                    e_ = P[str(b['id'])]
                    if e_.get('state') == 'check':
                        e_.update(state='done', sure=True, confirmed=int(time.time()))
                        save(SUBPREP, P)
                        log('Subtitles for %s: confirmed by John' % e_.get('name'))
                        record('subtitles confirmed', film=e_.get('name'), id=str(b['id']), by='John')
                elif op in ('first', 'last') and str(b.get('id') or '') in P:   # (John's order for the queue: this one next, or after all the rest)
                    os_ = [x_.get('order', 0) for k_, x_ in P.items() if not k_.startswith('_') and isinstance(x_, dict)] or [0]
                    e_ = P[str(b['id'])]
                    e_['order'] = (min(os_) - 1) if op == 'first' else (max(os_) + 1)
                    if op == 'first':   # (and straight to its file checks, then the audio check and Whisper if need be, without waiting on others)
                        e_['prio'] = int(time.time())
                    save(SUBPREP, P)
                    log('Subtitles: %s %s, John asked' % (e_.get('name'), 'next' if op == 'first' else 'last'))
                elif op in ('translate', 'translateall'):   # (John asks for the subtitle a film was timed by translated, now: one, or every one not yet)
                    n_ = 0
                    for k_, x_ in P.items():
                        if k_.startswith('_') or not isinstance(x_, dict) or (op == 'translate' and k_ != str(b.get('id') or '')):
                            continue
                        if not (x_.get('ref_file') and os.path.exists(x_['ref_file'])) or str(x_.get('ref_lang') or '').lower()[:2] in ('', 'en'):
                            continue
                        if os.path.exists(os.path.join(os.path.expanduser('~/Grifflix/subprep-cands'), '%s.tr.srt' % k_)):
                            continue
                        x_.pop('tr_tried', None); x_.pop('tr_given', None)
                        x_['tr_asked'] = int(time.time())
                        n_ += 1
                    save(SUBPREP, P)
                    log('Subtitles: %d to be translated on John’s Mac, John asked' % n_)
                elif op == 'nodialogue' and str(b.get('id') or '') in P:   # (John: no subtitles needed for this one)
                    P[str(b['id'])].update(state='nodialogue', note='no subtitles needed: said by John', at=int(time.time()))
                    save(SUBPREP, P)
                    log('Subtitles: none needed for %s, John said' % P[str(b['id'])].get('name'))
                elif op == 'again' and str(b.get('id') or '') in P:
                    for x_ in ('exact_tried', 'ref_tried', 'ref_tried2', 'name_tried', 'checks'):   # (every check done afresh, and said what it found)
                        P[str(b['id'])].pop(x_, None)
                    P[str(b['id'])].update(state='waiting', note='')
                    save(SUBPREP, P)
            F = [dict(v, id=k) for k, v in P.items() if not k.startswith('_')]
            cnt = {}
            for x in F:
                cnt[x.get('state')] = cnt.get(x.get('state'), 0) + 1
            pick = lambda st_: sorted(({k: x.get(k) for k in ('id', 'name', 'year', 'lang', 'note', 'chose', 'at', 'n')} for x in F if x.get('state') in st_),
                                      key=lambda x: -(x.get('at') or 0))
            # (the film being done now, and since when, for the admin page: John, 21 Nov 2026, "I'd like to be able to see in admin if anything
            # is happening")
            now_ = sorted(({k: x.get(k) for k in ('id', 'name', 'year', 'lang', 'at', 'step', 'stepat')} for x in F if x.get('state') == 'given'), key=lambda x: -(x.get('at') or 0))
            LNM_ = {'zh': 'Chinese', 'ja': 'Japanese', 'fr': 'French', 'es': 'Spanish', 'it': 'Italian', 'de': 'German', 'ru': 'Russian', 'pt': 'Portuguese', 'ar': 'Arabic', 'ro': 'Romanian',
                    'sv': 'Swedish', 'el': 'Greek', 'ko': 'Korean', 'pl': 'Polish', 'tr': 'Turkish', 'he': 'Hebrew', 'fi': 'Finnish', 'nl': 'Dutch', 'sr': 'Serbian', 'fa': 'Persian', 'vi': 'Vietnamese'}
            for x in F:   # (and a translation being done on John's Mac: shown as what it's doing now, not "nothing": John, 22 Nov 2026)
                if x.get('tr_given') and time.time() - x['tr_given'] < 1800 and not os.path.exists(os.path.join(os.path.expanduser('~/Grifflix/subprep-cands'), '%s.tr.srt' % x['id'])):
                    lg_ = str(x.get('ref_lang') or '').lower()[:2]
                    now_.insert(0, {'id': x['id'], 'name': x.get('name'), 'year': x.get('year'), 'lang': x.get('lang'), 'at': x['tr_given'],
                                    'step': x.get('step') or 'Translating the %s subtitle it was timed by, to compare' % LNM_.get(lg_, lg_), 'stepat': x.get('stepat') or x['tr_given']})
            # (and each film tried lately, however it went, with what it found: John, 21 Nov 2026, "I want more details than that")
            recent_ = sorted(({k: x.get(k) for k in ('id', 'name', 'year', 'lang', 'state', 'note', 'chose', 'n', 'miss', 'mins', 'at', 'step', 'stepat', 'tried', 'samples', 'sure', 'file', 'origin')}
                              for x in F if x.get('state') not in ('waiting',)), key=lambda x: -max(x.get('at') or 0, x.get('stepat') or 0))[:25]
            os_ = g.st.get('opensubs') or {}
            dl_ = {'left': os_.get('remaining'), 'reset': os_.get('reset'), 'out': self.subs_client().out_of_downloads() if os_.get('key') else False}
            return self.send_json(200, {'mac': {'asked': SUBASK.get('at'), 'said': SUBASK.get('said'), 'seen': MACAGENT.get('seen'), 'home': MACAGENT.get('home')}, 'dl': dl_, 'counts': cnt, 'total': len(F), 'paused': bool(P.get('_paused')), 'setup': bool((P.get('_setup') or {}).get('need')), 'now': now_[:3], 'recent': recent_,
                                        'check': pick(('check',)), 'none': pick(('none', 'nomatch', 'failed')), 'done': pick(('done',))[:30],
                                        # (every film, for the admin page's tabs: to do, added as they were, corrected (and how), to check, not
                                        # found: John, 22 Nov 2026, "I need a tab of added, corrected (with details), to do etc. Tidy")
                                        'all': [{k: x.get(k) for k in ('id', 'name', 'year', 'lang', 'state', 'note', 'chose', 'n', 'miss', 'mins', 'at', 'step', 'stepat',
                                                                       'tried', 'samples', 'sure', 'file', 'origin', 'how', 'kind', 'series', 'cands', 'translated', 'ref_only', 'ref_lang', 'vfile', 'verifying', 'old_vfile',
                                                                       'rcands', 'cur', 'chosen_from', 'ref_file', 'givenfile', 'givenname', 'ref_given',
                                                                       'speech_tried', 'speech_note', 'ref_byspeech', 'tr_asked', 'tr_tried', 'order', 'confirmed', 'checks', 'exact_tried', 'ref_tried2', 'name_tried')}
                                               for x in F], 'speech_now': SPEECH.get('now'),
                                        # (which have a subtitle in another language to translate, and which have it translated: for John's Translate buttons)
                                        'trable': [x['id'] for x in F if x.get('ref_file') and str(x.get('ref_lang') or '').lower()[:2] not in ('', 'en') and os.path.exists(x['ref_file'])],
                                        'trdone': [x['id'] for x in F if os.path.exists(os.path.join(os.path.expanduser('~/Grifflix/subprep-cands'), '%s.tr.srt' % x['id']))]})

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
            # (a film being made on John's Mac now isn't on its queue too, nor queued again while it's made: The Last Black Man in San Francisco
            # was converting and at the bottom of the queue at once: John, 24 Nov 2026, "the queue isn't clear")
            live_ = {}
            for j_ in sorted(CONVJOBS.values(), key=lambda j_: j_.get('at', 0)):
                if j_.get('mac') and not j_.get('ended') and j_.get('state') in ('waiting', 'started', 'converting', 'copying', 'checking'):
                    live_[j_['id']] = {'id': j_['id'], 'name': j_['plan']['item'].get('name') or '', 'state': j_.get('state'), 't': j_.get('t') or 0,
                                       'dur': j_['plan'].get('dur') or 0, 'stage': str(j_.get('stage') or '')[:160]}
            if any(x.get('id') in live_ for x in Q):
                Q = [x for x in Q if x.get('id') not in live_]
                save(MACQ, Q)
            if b and str(b.get('op') or '') in ('add', 'first') and str(b.get('id') or '') in live_:
                return self.send_json(409, {'error': 'It’s being made on your Mac now (%s).' % live_[str(b.get('id'))]['state'], 'queue': Q, 'now': list(live_.values())})
            if b:
                op, iid = str(b.get('op') or ''), str(b.get('id') or '')
                if op == 'key':   # (a new key for his Mac's helper: the old one stops working)
                    k = secrets.token_urlsafe(24)
                    g.st['macagent'] = {'key': hashlib.sha256(k.encode()).hexdigest(), 'made': int(time.time())}
                    save(SETTINGS, g.st)
                    return self.send_json(200, {'key': k})
                if not re.match(r'^[0-9a-f]{32}$', iid):
                    return self.send_json(400, {'error': 'Which film?'})
                if op == 'clearpassed':   # (the passed-over list, seen and put away)
                    save(MACQ + '.passed', [])
                    return self.send_json(200, {'queue': Q, 'passed': []})
                Q = [x for x in Q if x.get('id') != iid]
                if op in ('add', 'first'):   # (queued again: off the passed-over list)
                    save(MACQ + '.passed', [y for y in (load(MACQ + '.passed', []) or []) if y.get('id') != iid])
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
            P_ = [y for y in (load(MACQ + '.passed', []) or []) if time.time() - y.get('at', 0) < 3 * 86400]
            return self.send_json(200, {'queue': Q, 'now': list(live_.values()), 'passed': P_, 'setup': bool(a.get('key')), 'worked': bool(a.get('key') and (a.get('worked') or MACAGENT.get('seen') or any(v.get('mac') for v in list(CONVJOBS.values())))), 'seen': MACAGENT.get('seen'), 'home': MACAGENT.get('home')})

        def api_critrel(self, me, user, q, b):
            """John's only: which of the Criterion Collection's films are Criterion's own release (its logo at the start), the list and how
            far the looking has got; POST {id, v: yes|no|''}: his own word on one (or taken back)."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            rf = os.path.join(CRL, 'result.json')
            R = load(rf, {}) or {}
            gf = os.path.join(CRL, 'given.json')
            if q.get('pics'):   # (a video's pictures, to choose which are the logo: John, 24 Nov 2026, "the main logo starts halfway through")
                src_ = str(q.get('pics'))
                return self.send_json(200, {'src': src_, 'pics': [{'t': x.get('t'), 'px': x.get('px'), 'off': bool(x.get('off'))} for x in load(gf, []) or [] if x.get('src') == src_]})
            if b and b.get('op') in ('logo', 'logodel', 'logos', 'logouse'):   # (his own pictures of the logo: given, or one taken back)
                G_ = load(gf, []) or []
                if b['op'] == 'logouse':   # (which of a video's pictures are the logo: those he's chosen)
                    on_ = set(round(float(t_), 1) for t_ in (b.get('on') or []) if isinstance(t_, (int, float)))
                    for x in G_:
                        if x.get('src') == str(b.get('src') or ''):
                            x['off'] = round(float(x.get('t') or 0), 1) not in on_
                elif b['op'] == 'logos':   # (a video's pictures, some hundreds at a time)
                    src = str(b.get('src') or 'video')[:80]
                    for x in (b.get('pics') or [])[:400]:
                        try:
                            px = base64.b64decode(str(x.get('px') or ''))
                        except Exception:
                            continue
                        if len(px) == CRL_W * CRL_H and crl_vec(px) is not None:
                            G_.append({'src': src, 't': x.get('t'), 'px': base64.b64encode(px).decode(), 'at': int(time.time())})
                elif b['op'] == 'logodel' and b.get('src'):
                    G_ = [x for x in G_ if x.get('src') != str(b['src'])]
                elif b['op'] == 'logo':
                    try:
                        px = base64.b64decode(str(b.get('px') or ''))
                    except Exception:
                        px = b''
                    if len(px) != CRL_W * CRL_H or crl_vec(px) is None:
                        return self.send_json(400, {'error': 'That picture couldn’t be used (all one colour?).'})
                    G_.append({'name': str(b.get('name') or 'picture')[:80], 'px': base64.b64encode(px).decode(), 'prev': str(b.get('prev') or '')[:60000], 'at': int(time.time())})
                else:
                    n_ = b.get('n')
                    if isinstance(n_, int) and 0 <= n_ < len(G_):
                        G_.pop(n_)
                os.makedirs(CRL, exist_ok=True)
                save(gf, G_)
                CRL_STATE['logokey'] = None   # (learnt again with them)
                if CRL_STATE.get('films'):
                    threading.Thread(target=crl_classify, args=(g,), daemon=True).start()
                record('Criterion logo pictures given by John', count=len(G_))
            if b and b.get('op') not in ('logo', 'logodel', 'logos', 'logouse'):
                iid, v = str(b.get('id') or ''), str(b.get('v') or '')
                if iid not in R:
                    return self.send_json(404, {'error': 'That film isn’t on the list.'})
                if v in ('yes', 'no', 'channel'):
                    R[iid].update(v=v, how={'yes': 'you said so', 'no': 'you said it isn’t', 'channel': 'you said it’s from the Criterion Channel'}[v], by='John')
                else:
                    R[iid].pop('by', None); R[iid].update(v='todo', how='looked at again soon')
                save(rf, R)
                record('Criterion release, said by John', film=R[iid].get('name'), id=iid, v=v or 'taken back')
                if CRL_STATE.get('films') and not CRL_STATE.get('running'):
                    threading.Thread(target=crl_classify, args=(g,), daemon=True).start()
            G2_ = load(gf, []) or []
            return self.send_json(200, {'films': [dict(e, id=k) for k, e in R.items()], 'progress': {k: CRL_STATE.get(k) for k in ('done', 'of', 'running', 'paused', 'logos', 'known', 'mine', 'chans')},
                                        'vidon': {s_: sum(1 for x in G2_ if x.get('src') == s_ and not x.get('off')) for s_ in set(x.get('src') for x in G2_ if x.get('src'))},
                                        'given': [{'name': x.get('name'), 'prev': x.get('prev'), 'at': x.get('at'), 'n': n_} for n_, x in enumerate(load(gf, []) or []) if not x.get('src')],
                                        'videos': sorted(set(x.get('src') for x in load(gf, []) or [] if x.get('src'))),
                                        'vidn': {s_: sum(1 for x in load(gf, []) or [] if x.get('src') == s_) for s_ in set(x.get('src') for x in load(gf, []) or [] if x.get('src'))}})

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
            # (where it is in John's Mac's queue, or being made there: its Edit panel says so, and offers Take off / Next on my Mac:
            # John, 24 Nov 2026, "I can queue things for my mac but not easily cancel things from the queue")
            Q_ = [x.get('id') for x in (load(MACQ, []) or [])]
            busy_ = next((j.get('state') for j in CONVJOBS.values() if j.get('id') == iid and j.get('mac') and not j.get('ended') and j.get('state') in ('waiting', 'started', 'converting', 'copying', 'checking')), None)
            return self.send_json(200, {'sources': out, 'macq': {'at': Q_.index(iid) + 1 if iid in Q_ else 0, 'of': len(Q_), 'job': busy_}})

        def api_convertjobs(self, me, user, q, b):
            """John's only: every film being converted on his Mac now, and those finished in the last hour, for the admin page from anywhere
            (John, 17 Nov 2026: "If something is converting in the Mac I'd like to be able to see progress from anywhere I'm signed in")."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            if b and b.get('cancel'):
                # (a film's conversion on John's Mac stopped: one not yet taken up, ended here; one converting, told to stop at its next word,
                # which is every few seconds, and its part-made file deleted there: John, 22 Nov 2026, "how can I cancel converts on my mac")
                j = CONVJOBS.get(str(b['cancel']))
                if not j or not j.get('mac') or j.get('ended'):
                    return self.send_json(404, {'error': 'That one isn’t converting on your Mac now.'})
                nm_ = j['plan']['item'].get('name')
                if (j.get('state') or 'waiting') == 'waiting':
                    j['state'], j['ended'], j['error'] = 'cancelled', time.time(), 'cancelled'
                elif j.get('state') in ('started', 'converting'):
                    j['cancel'] = time.time()
                else:
                    return self.send_json(409, {'error': 'It’s made already: it’s being copied over or put in place.'})
                claims = load(os.path.expanduser('~/Grifflix/convert-claims.json'), {}) or {}
                if claims.pop(j['id'], None) is not None:
                    save(os.path.expanduser('~/Grifflix/convert-claims.json'), claims)
                convjobs_save()
                log('Cancelled on John’s Mac, John asked: %s' % nm_)
                record('conversion cancelled', film=nm_, id=j['id'], where='John’s Mac', by='John')
            out, now_ = [], time.time()
            # (a job that went quiet is left off once John's Mac has since done or started another, three hours after its last word, or once
            # the film has been converted since it began (John, 17 Nov 2026: Career Girls "no word from your Mac since 14:48 ... but it was
            # completed"); one finished shows as done for half an hour, then goes ("I've since updated another film via my mac and there's
            # no message"))
            done_ = (load(os.path.expanduser('~/Grifflix/convert-state.json'), {}) or {}).get('done') or {}
            last_mac = max([x.get('seen') or x['at'] for x in CONVJOBS.values() if x.get('state') not in ('waiting', 'notstarted')] or [0])
            for j in sorted(CONVJOBS.values(), key=lambda j: -j['at']):
                st_ = j.get('state') or 'waiting'
                if st_ == 'lost' and j.get('again') and now_ - (j.get('ended') or j['at']) < 1800:
                    st_ = 'again'   # (back in John's Mac's queue, said why, for half an hour)
                if j.get('cancel') and not j.get('ended'):
                    st_ = 'cancelling'
                if st_ in ('notstarted', 'lost') or (st_ in ('done', 'failed', 'cancelled') and now_ - (j.get('ended') or j['at']) > 1800) or now_ - j['at'] > 86400:
                    continue
                if st_ in ('started', 'converting', 'copying'):
                    heard = j.get('seen') or j['at']
                    if now_ - heard > 600 and (last_mac > heard + 60 or now_ - heard > 3 * 3600 or ((done_.get(j['id']) or {}).get('at') or 0) > j['at']):
                        continue
                if st_ == 'waiting' and now_ - j['at'] > 900:
                    continue
                quiet = (st_ == 'converting' and now_ - (j.get('seen') or j['at']) > 600) or (st_ == 'started' and now_ - (j.get('seen') or j['at']) > 180)
                out.append({'id': j['id'], 'tok': next((k_ for k_, v_ in CONVJOBS.items() if v_ is j), ''), 'name': j['plan']['item'].get('name'), 'state': 'quiet' if quiet and st_ != 'cancelling' else st_, 't': j.get('t') or 0, 'dur': j['plan'].get('dur') or 0,
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
                name = str(q.get('q') or it.get('Name') or '').strip()[:300]
                year = int(q['year']) if str(q.get('year') or '').isdigit() else None
                # (a TMDB or IMDb link, or just its number, pasted in: that film itself (John, 20 Nov 2026: "if I could paste that in it
                # would be fine"))
                mt = re.search(r'themoviedb\.org/(?:movie|tv)/(\d+)', name) or re.match(r'^\s*(\d{1,8})\s*$', name)
                mi = re.search(r'\b(tt\d{6,10})\b', name)
                if mt or mi:
                    info = {'Name': '', 'ProviderIds': {'Tmdb': mt.group(1)} if mt else {'Imdb': mi.group(1)}}
                else:
                    info = {'Name': name}
                    if year:
                        info['Year'] = year
                res = g.jf.call('POST', '/Items/RemoteSearch/' + kind, {'SearchInfo': info, 'ItemId': iid, 'IncludeDisabledProviders': False}, timeout=60) or []
                # (and TMDB searched more loosely where that finds little: its words one at a time, the closest titles first, so one
                # letter out still finds it: TMDB spells it "The File on Thelma Jordon", the folder "Jordan": John, 20 Nov 2026)
                if not (mt or mi) and len(res) < 3 and kind == 'Movie' and g.st.get('tmdb_key'):
                    import difflib
                    have = set(str((r.get('ProviderIds') or {}).get('Tmdb') or '') for r in res)
                    words = sorted(set(w for w in re.findall(r'[^\W\d_]{4,}', name) if fold(w) not in ('with', 'from', 'that', 'this', 'what', 'when')), key=len, reverse=True)[:3]
                    found = {}
                    for w in words:
                        for y in ([year, None] if year else [None]):
                            q_ = {'query': w}
                            if y:
                                q_['year'] = y
                            for x in (tmdb(g, '/search/movie', **q_) or {}).get('results') or []:
                                found.setdefault(x['id'], x)
                    sc = sorted(found.values(), key=lambda x: -max(difflib.SequenceMatcher(None, fold(name), fold(x.get(k))).ratio() for k in ('title', 'original_title')))
                    for x in sc[:12]:
                        if str(x['id']) in have:
                            continue
                        if max(difflib.SequenceMatcher(None, fold(name), fold(x.get(k))).ratio() for k in ('title', 'original_title')) < .6:
                            break
                        res.append({'Name': x.get('title'), 'ProductionYear': int((x.get('release_date') or '0')[:4] or 0) or None, 'Overview': x.get('overview'),
                                    'ImageUrl': ('https://image.tmdb.org/t/p/w185' + x['poster_path']) if x.get('poster_path') else None,
                                    'ProviderIds': {'Tmdb': str(x['id'])}, 'SearchProviderName': 'TheMovieDb'})
                out = []
                for r in res[:20]:
                    pid = r.get('ProviderIds') or {}
                    out.append({'name': r.get('Name'), 'year': r.get('ProductionYear'), 'overview': (r.get('Overview') or '')[:300],
                                'image': r.get('ImageUrl'), 'tmdb': pid.get('Tmdb'), 'imdb': pid.get('Imdb'), 'raw': r})
                # (an IMDb number TMDB has no film for (World on a Wire, a TV mini-series there): offered to be kept on it as it is)
                return self.send_json(200, {'kind': kind, 'name': it.get('Name'), 'year': it.get('ProductionYear'), 'results': out,
                                            'imdbOnly': mi.group(1) if mi and not out else None})
            if re.match(r'^tt\d{6,10}$', str((b or {}).get('imdb') or '')):
                # (John's own IMDb number put on a film TMDB has no film for, its name and year as he gives them, kept: John, 20 Nov 2026,
                # World on a Wire, "https://www.imdb.com/title/tt0070904/ world on a wire is this")
                full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid)) or {}
                pid = dict(full.get('ProviderIds') or {})
                pid['Imdb'] = b['imdb']
                full['ProviderIds'] = pid
                nm = re.sub(r'\s+', ' ', str(b.get('name') or '')).strip()[:200]
                if nm:
                    full['Name'] = nm
                if str(b.get('year') or '').isdigit():
                    full['ProductionYear'] = int(b['year'])
                full['LockedFields'] = sorted(set((full.get('LockedFields') or []) + ['Name']))
                g.jf.call('POST', '/Items/%s' % iid, full)
                g.forget()
                g.publish_soon = time.time() + 180
                after_fixes()
                log('IMDb number given by John: %s → %s' % (full.get('Name'), b['imdb']))
                return self.send_json(200, {'ok': True, 'name': full.get('Name'), 'year': full.get('ProductionYear'), 'imdb': b['imdb']})
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
                did = rename_to(g, iid, pick.get('Name') or '', crit_year(pick) or pick.get('ProductionYear'), pick)   # (Criterion's year, for its films)
            except Exception as e:
                did = 'its folder couldn’t be renamed (%s)' % e
                log('  rename:', e)
            # (and its collections looked at again under its right name: A24, The Criterion Collection and its box set, its cover: John,
            # 20 Nov 2026, "If I correct a film through tmdb, does it then automatically correct folder structure and collections it should be in?")
            after_fixes()
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
                        L_ = [re.sub(r'\s+', ' ', str(x)).strip()[:4000] for x in v if str(x).strip()][:200]   # (a set's whole list of films in one line: 18 Nov 2026)
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
                FT = {str(k)[:60]: [str(x)[:160] for x in (v or []) if re.match(r'^[\w-]{3,160}$', str(x))][:3000] for k, v in (b['tags'].get('ftags') or {}).items() if isinstance(v, list)}   # (the films each list showed themselves, not through a box set: 20 Nov 2026)
                SE = {str(k)[:160]: [str(x)[:160] for x in (v or []) if re.match(r'^[\w-]{3,160}$', str(x))][:200] for k, v in (b['tags'].get('sets') or {}).items() if isinstance(v, list)}   # (each box set's films: John, 17 Nov 2026)
                SSP = {str(k)[:160]: str(v)[:6] for k, v in (b['tags'].get('setspine') or {}).items() if str(v).isdigit()}   # (each box set's own spine number)
                SIM = {str(k)[:160]: str(v)[:400] for k, v in (b['tags'].get('setimg') or {}).items() if re.match(r'^https://[\w.-]+/', str(v))}   # (each box set's own cover)
                SDE = {str(k)[:160]: str(v)[:2000] for k, v in (b['tags'].get('setdesc') or {}).items() if len(str(v or '')) > 40}   # (each box set's description: John, 18 Nov 2026)
                SFE = {str(k)[:160]: [re.sub(r'\s+', ' ', str(x)).strip()[:4000] for x in v if str(x).strip()][:200] for k, v in (b['tags'].get('setfeat') or {}).items() if isinstance(v, list) and v}   # (and its special features)
                SFI = {str(k)[:160]: {str(a)[:120]: str(u)[:400] for a, u in v.items() if re.match(r'^https?://', str(u))} for k, v in (b['tags'].get('setfimg') or {}).items() if isinstance(v, dict)}   # (each film's cover as it is in the set: 20 Nov 2026)
                save(os.path.join(home, 'criterion-tags.json'), {'tags': T, 'ftags': FT, 'sets': SE, 'setspine': SSP, 'setimg': SIM, 'setfimg': SFI, 'setdesc': SDE, 'setfeat': SFE, 'missing': [str(x)[:120] for x in (b['tags'].get('missing') or [])][:40],
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

        def api_setaside(self, me, user, q, b):
            """John's only: what's kept in Set aside on the G-Drive (the copies set aside, and older folders), each with its size, for the admin page,
            and any of it deleted for good from there (John, 20 Nov 2026: "I should have a delete option on any folder where copies are kept in
            admin"). GET: the folders. POST {delete: [their paths]} or {all: true}: deleted, only ever inside Set aside."""
            if not me.get('owner'):
                return self.send_json(403, {'error': 'Only John.'})
            import shutil
            base = os.path.realpath(SETASIDE)
            def size(p_):
                if os.path.isfile(p_):
                    return os.path.getsize(p_)
                t_ = 0
                for r_, ds_, fs_ in os.walk(p_):
                    for f_ in fs_:
                        try:
                            t_ += os.path.getsize(os.path.join(r_, f_))
                        except OSError:
                            pass
                return t_
            def entries():
                out_ = []
                if not os.path.isdir(base):
                    return out_
                for top_ in sorted(os.listdir(base)):
                    tp_ = os.path.join(base, top_)
                    if top_.startswith('.'):
                        continue
                    kids_ = [k_ for k_ in sorted(os.listdir(tp_)) if not k_.startswith('.')] if os.path.isdir(tp_) and top_ in ('Duplicates', 'Movies', 'TV') else None
                    if kids_ is None:
                        out_.append({'p': top_, 'in': '', 'name': top_, 'gb': round(size(tp_) / 1e9, 2), 'at': int(os.path.getmtime(tp_))})
                    for k_ in kids_ or []:
                        kp_ = os.path.join(tp_, k_)
                        out_.append({'p': top_ + '/' + k_, 'in': top_, 'name': k_, 'gb': round(size(kp_) / 1e9, 2), 'at': int(os.path.getmtime(kp_))})
                return out_
            if b:
                L_ = entries()
                want_ = [e_['p'] for e_ in L_] if b.get('all') else [str(x_) for x_ in (b.get('delete') or [])][:500]
                gone_, freed_ = [], 0
                for rel_ in want_:
                    rp_ = os.path.realpath(os.path.join(base, rel_))
                    if not rp_.startswith(base + os.sep) or not os.path.exists(rp_):
                        continue
                    try:
                        sz_ = size(rp_)
                        shutil.rmtree(rp_) if os.path.isdir(rp_) and not os.path.islink(rp_) else os.remove(rp_)
                        freed_ += sz_
                        gone_.append(rel_)
                    except OSError as e_:
                        log('Set aside: couldn’t delete %s: %s' % (rel_, e_))
                for d_ in ('Duplicates', 'Movies', 'TV'):   # (the folders that hold them kept, empty)
                    os.makedirs(os.path.join(base, d_), exist_ok=True) if os.path.isdir(base) else None
                log('Set aside: John deleted %d (%.1f GB): %s' % (len(gone_), freed_ / 1e9, ', '.join(gone_)[:400]))
                return self.send_json(200, {'deleted': gone_, 'gb': round(freed_ / 1e9, 1), 'items': entries()})
            return self.send_json(200, {'items': entries()})

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
                try:   # (and the kept answer, out of date now)
                    os.remove(os.path.join(home, 'duplicates-found.json'))
                except OSError:
                    pass
                log('Duplicates: John chose %d to keep' % len(keep))
            running = bool(DUPS.get('p') and DUPS['p'].poll() is None)
            # (one look at a time, in the background, its answer kept on disk through a restart: each visit to the admin page had started a
            # look over the whole drive and waited for it; with the Mac busy, they outlasted the page and piled up, nine at once, and the media
            # server stopped answering: 21 Nov 2026)
            ff_ = os.path.join(home, 'duplicates-found.json')
            found = DUPS.get('found')
            if not found:
                fd_ = load(ff_, None)
                if isinstance(fd_, dict) and fd_.get('at'):
                    found = DUPS['found'] = (fd_['at'], fd_.get('r') or {})
            scan_ = DUPS.get('scan')
            if scan_ and scan_.poll() is not None:   # (a look just ended: its answer kept)
                try:
                    out_ = open(os.path.join(home, 'duplicates-scan.out'), encoding='utf-8', errors='replace').read()
                    r_ = json.loads([l for l in out_.splitlines() if l.startswith('{')][-1])
                    found = DUPS['found'] = (time.time(), r_)
                    save(ff_, {'at': found[0], 'r': r_})
                except (OSError, IndexError, ValueError):
                    pass
                scan_ = DUPS['scan'] = None
            scanning = bool(scan_)
            if not running and not scanning and (not found or q.get('fresh') == '1' or time.time() - found[0] > 600):
                DUPS['scan'] = subprocess.Popen([sys.executable, script, 'json'], stdout=open(os.path.join(home, 'duplicates-scan.out'), 'w'),
                                                stderr=subprocess.DEVNULL, cwd=home)
                scanning = True
            last = ''
            try:   # (what the last setting aside did, for an hour after it, not ever after: John, 20 Nov 2026, "Not sure why it still shows this")
                lf_ = os.path.join(home, 'duplicates.log')
                if running or time.time() - os.path.getmtime(lf_) < 3600:
                    last = open(lf_, encoding='utf-8', errors='replace').read()[-2000:]
            except OSError:
                pass
            return self.send_json(200, dict((found or (0, {}))[1], running=running, last=last, at=DUPS.get('at'), scanning=scanning, looked=int((found or (0,))[0])))

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
                with open(p, 'rb') as fh:   # (from a point in it too, for a file larger than one look: &off=bytes)
                    fh.seek(max(0, int(q.get('off') or 0)) if str(q.get('off') or '0').isdigit() else 0)
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
            if what == 'icloud':   # (whether the Mac is signed in to iCloud, and to what; never the account's name: John, 19 Nov 2026, "make sure
                # that I'm not connected to icloud - I shouldn't be")
                import subprocess
                out = {}
                try:
                    r = subprocess.run(['defaults', 'read', 'MobileMeAccounts', 'Accounts'], capture_output=True, text=True, timeout=20)
                    txt = r.stdout or ''
                    out['signed_in'] = 'AccountID' in txt
                    out['services_on'] = sorted(set(m.group(1) for m in re.finditer(r'Name = (\w+);[^}]*?Enabled = 1', txt, re.S)))
                except Exception as e:
                    out['error'] = str(e)[:100]
                md = os.path.expanduser('~/Library/Mobile Documents')
                out['icloud_drive_folder'] = os.path.isdir(md) and len(os.listdir(md)) if os.path.isdir(md) else 0
                return self.send_json(200, out)
            if what == 'du':   # (how much room each folder takes on the Mac's own disk, sizes only, nothing changed: John, 19 Nov 2026, "I only use
                # the mac mini for grifflix, so happy to clear out space - do a check through")
                import subprocess
                path = os.path.realpath(os.path.expanduser(q.get('path') or '~'))
                if not (path == '/' or path == os.path.expanduser('~') or path.startswith(os.path.expanduser('~') + '/') or path.split('/')[1:2] in (['Applications'], ['Library'], ['private'], ['Users'])):
                    return self.send_json(403, {'error': 'Not a folder that can be measured.'})
                try:
                    r = subprocess.run(['du', '-k', '-x', '-d', '1', path], capture_output=True, text=True, timeout=600)
                except subprocess.TimeoutExpired:
                    return self.send_json(504, {'error': 'Took too long to measure.'})
                rows = []
                for line in (r.stdout or '').splitlines():
                    k, _, f = line.partition('\t')
                    if k.strip().isdigit():
                        rows.append({'path': f, 'gb': round(int(k) / 1e6, 2)})
                rows.sort(key=lambda x: -x['gb'])
                snaps = ''
                try:
                    snaps = subprocess.run(['tmutil', 'listlocalsnapshots', '/'], capture_output=True, text=True, timeout=30).stdout.strip()[:2000]
                except Exception:
                    pass
                return self.send_json(200, {'path': path, 'folders': rows[:40], 'snapshots': snaps, 'unreadable': len([l for l in (r.stderr or '').splitlines() if 'denied' in l.lower() or 'not permitted' in l.lower()])})
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
            if b and str(b.get('subdl') or '').strip():   # (Subdl's API key, from his own Subdl account: tried before it's kept)
                k_ = str(b['subdl']).strip()
                try:
                    SUBS.SubDL(k_, tls()).search('en', imdb='tt0111161')
                except Exception as e:
                    return self.send_json(400, {'error': 'Subdl didn’t take that key: %s' % str(e)[:160]})
                g.st['subdl'] = {'key': k_, 'at': int(time.time())}
                SUBS.SUBDL['key'] = k_
                save(SETTINGS, g.st)
                log('Subtitles: Subdl set up, a second place to look')
                subs_none_again('Subdl too')
                threading.Thread(target=english_by_foreign_all, args=(g, 5), daemon=True).start()
                return self.send_json(200, {'ready': bool(o.get('token') or (o.get('key') and o.get('user') and o.get('pass'))), 'user': o.get('user') or '',
                                            'keyset': bool(o.get('key')), 'remaining': o.get('remaining'), 'allowed': o.get('allowed'), 'subdl': True})
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
                                        'keyset': bool(o.get('key')), 'remaining': o.get('remaining'), 'allowed': o.get('allowed'), 'subdl': bool((g.st.get('subdl') or {}).get('key'))})

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
                        res = self.subs_client().search(lang, mhash=mh, tmdb=pid.get('Tmdb'), imdb=pid.get('Imdb'), alt=[it.get('OriginalTitle')], query=it.get('Name'), year=it.get('ProductionYear'),
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
            if op == 'plain':   # (one chosen from the list, added as it is and on at once: no timing it as it plays: John, 22 Nov 2026, "the way
                # we tried to synch the subtitles as it played before should be stopped - go back to a normal choose or select way")
                fid = int(b.get('file_id') or 0)
                lang = re.sub(r'[^a-z]', '', (b.get('lang') or 'en').lower().split('-')[0])[:3] or 'en'
                if not fid or not path or not os.path.exists(path):
                    return self.send_json(400, {'error': 'That film’s file isn’t there.' if fid else 'Which subtitles?'})
                day = time.strftime('%Y-%m-%d')
                if SUBDAY.get((me['uid'], day), 0) >= 15 and not me.get('owner'):
                    return self.send_json(429, {'error': 'That’s 15 subtitles today: more tomorrow.'})
                try:
                    cues = SUBS.parse_srt(SUBS.decode(self.subs_client().download(fid)))
                except Exception as e:
                    return self.send_json(502, {'error': str(e)[:200]})
                if len(cues) < 5:
                    return self.send_json(502, {'error': 'Those subtitles came empty: choose others.'})
                SUBDAY[(me['uid'], day)] = SUBDAY.get((me['uid'], day), 0) + 1
                out = self.subs_write(path, lang, cues)
                subs, pick = self.subs_show(iid, out, key)
                log('Subtitles: %s chosen by %s for %s, added as they are' % (str(b.get('name') or fid)[:80], me['name'], it.get('Name')))
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
MSHEET = {}   # (the missing box set films' Google Sheet, being made)
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


BRAK_SRC = '/Volumes/G-Drive/Grifflix/Movies/By Brakhage An Anthlogy Vol 1-2 (1954)'
# (each file in the one Brakhage folder: its own folder and name, and its spine (Criterion's: Volume One 184, Volume Two 517; the films
# from Volume One with it): John, 18 Nov 2026, "By Brakhage isn't being treated correctly by the criterion collection")
BRAK_PLAN = [
    (r'^By\.Brakhage\.An\.Anthology\.Vol\.1\.', 'By Brakhage - An Anthology, Volume One (2003)', '', 184),
    (r'^By\.Brakhage\.An\.Anthology\.Vol\.2\.Disc\.1\.', 'By Brakhage - An Anthology, Volume Two (2010)', ' - part1', 517),
    (r'^By\.Brakhage\.An\.Anthology\.Vol\.2\.Disc\.2\.', 'By Brakhage - An Anthology, Volume Two (2010)', ' - part2', 517),
    (r'^Dog\.Star\.Man\.Prelude\.', 'Prelude - Dog Star Man (1961)', '', 184),
    (r'^Dog\.Star\.Man\.Part\.I\.(?!I)', 'Dog Star Man - Part I (1962)', '', 184),
    (r'^Dog\.Star\.Man\.Part\.II\.(?!I)', 'Dog Star Man - Part II (1963)', '', 184),
    (r'^Dog\.Star\.Man\.Part\.III\.', 'Dog Star Man - Part III (1964)', '', 184),
    (r'^Dog\.Star\.Man\.Part\.IV\.', 'Dog Star Man - Part IV (1964)', '', 184),
    (r'^Wedlock\.House\.An\.Intercourse\.', 'Wedlock House - An Intercourse (1959)', '', 184),
    (r'^The\.Act\.of\.Seeing\.with\.Ones\.Own\.Eyes\.', "The Act of Seeing with One's Own Eyes (1971)", '', 184),
]


def _brak_moves():
    """[(from, to)] for every file in the Brakhage folder, by BRAK_PLAN: its film as the folder's name (a part's with " - part1"), its
    pictures as poster.jpg and fanart.jpg."""
    out = []
    for n in sorted(os.listdir(BRAK_SRC)):
        if n.startswith('.') or not os.path.isfile(os.path.join(BRAK_SRC, n)):
            continue
        for rx, folder, part, _sp in BRAK_PLAN:
            if re.match(rx, n):
                m = re.search(r'-(poster|fanart)\.(jpg|png)$', n)
                if m:
                    if part and part != ' - part1':
                        break   # (one picture each: the first part's)
                    to = '%s.%s' % (m.group(1), m.group(2))
                else:
                    to = folder + part + os.path.splitext(n)[1]
                out.append((os.path.join(BRAK_SRC, n), os.path.join(os.path.dirname(BRAK_SRC), folder, to)))
                break
    return out


def ask_brakhage_split(req):
    if not os.path.isdir(BRAK_SRC):
        raise RuntimeError('the By Brakhage folder isn’t there (done already?)')
    mv = _brak_moves()
    if not mv:
        raise RuntimeError('nothing in the By Brakhage folder to move')
    for a, b in mv:
        if os.path.exists(b):
            raise RuntimeError('%s is there already' % b)
    req['detail'] = '%d files into %d folders: %s. Nothing deleted: what’s left of the old folder goes to Set aside.' % (
        len(mv), len(set(os.path.dirname(b) for _, b in mv)), ', '.join(sorted(set(os.path.basename(os.path.dirname(b)) for _, b in mv))))


def act_brakhage_split(g, req):
    import shutil
    mv = _brak_moves()
    # (what the old films were in, and their ids by file, to carry over to the new ones once the media server has them)
    olds = {}
    for it in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie,Video', 'Fields': 'Path'}) or {}).get('Items', []):
        if (it.get('Path') or '').startswith(BRAK_SRC + '/'):
            olds[it['Path']] = it['Id']
    sets = [b for b in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'BoxSet'}) or {}).get('Items', [])]
    member = {}
    for b in sets:
        for i in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': b['Id']}) or {}).get('Items', []):
            if i['Id'] in olds.values():
                member.setdefault(b['Id'], set()).add(i['Id'])
    in_any = [b for b, m in member.items()]   # (each set any of them was in: every new film goes in it)
    done = []
    for a, b in mv:
        os.makedirs(os.path.dirname(b), exist_ok=True)
        os.rename(a, b)
        done.append((a, b))
    own_f = os.path.expanduser('~/Grifflix/criterion-spines-own.json')
    own = load(own_f, {}) or {}
    for _rx, folder, _p, sp in BRAK_PLAN:
        own[folder] = sp
    own.pop(os.path.basename(BRAK_SRC), None)
    save(own_f, own)
    os.makedirs(SETASIDE, exist_ok=True)
    left = os.path.join(SETASIDE, os.path.basename(BRAK_SRC))
    if os.path.exists(left):
        left += ' (%d)' % time.time()
    shutil.move(BRAK_SRC, left)
    folders = sorted(set(os.path.dirname(b) for _, b in done))
    try:
        g.jf.call('POST', '/Library/Media/Updated', {'Updates': [{'Path': BRAK_SRC, 'UpdateType': 'Deleted'}] + [{'Path': f, 'UpdateType': 'Created'} for f in folders]})
    except Exception:
        g.jf.call('POST', '/Library/Refresh')
    newpath = dict(done)

    def follow():   # (the new films, once the media server has them: in the same collections, and calendars, lists and progress moved over)
        for _ in range(60):
            time.sleep(30)
            by = {it.get('Path'): it['Id'] for it in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path'}) or {}).get('Items', []) if any((it.get('Path') or '').startswith(f + '/') for f in folders)}
            if len(by) >= len(folders):
                break
        for b_ in in_any:
            try:
                g.jf.call('POST', '/Collections/%s/Items' % b_, query={'Ids': ','.join(by.values())})
            except Exception as e:
                log('By Brakhage: into a collection: %s' % e)
        m = {}
        for p_, i_ in olds.items():
            n_ = newpath.get(p_)
            if n_:
                n2 = by.get(n_) or by.get(re.sub(r' - part\d+(\.\w+)$', r' - part1\1', n_))
                if n2:
                    m[i_] = n2
        if m:
            g.remap(m)
        g.forget()
        g.publish_soon = time.time() + 120
        log('By Brakhage: %d films in their own folders, in %d collection(s); %d moved over in calendars and lists' % (len(by), len(in_any), len(m)))
    threading.Thread(target=follow, daemon=True).start()
    return '%d files moved into %d folders (%s); the old folder’s leftovers are in Set aside. The media server is reading them; the collections follow in a few minutes.' % (
        len(done), len(folders), ', '.join(os.path.basename(f) for f in folders))


def _crit_sets(g):
    """The box sets kept as collections inside The Criterion Collection: (criterion collection, [(set, [its films and series])])."""
    q = lambda **k: (g.jf.call('GET', '/Users/%s/Items' % g.uid, query=k) or {}).get('Items', [])
    allsets = q(Recursive='true', IncludeItemTypes='BoxSet')
    crit = next((b for b in allsets if (b.get('Name') or '').strip().lower() == 'the criterion collection'), None)
    if not crit:
        return None, []
    out = []
    for b in q(ParentId=crit['Id']):
        if b.get('Type') == 'BoxSet':
            out.append((b, [k for k in q(ParentId=b['Id'], Fields='Path') if k.get('Type') in ('Movie', 'Series', 'Video')]))
    return crit, out


def ask_crit_flatten(req):
    g = G_REF[0] if G_REF else None
    crit, L = _crit_sets(g) if g else (None, [])
    if not L:
        raise RuntimeError('no box set is kept as a collection inside The Criterion Collection now')
    req['detail'] = '%d collections: %s. Their films go into The Criterion Collection itself first, each keeping its box set; the films aren’t touched. Then missing backdrops are fetched for the Criterion films without one.' % (
        len(L), '; '.join('%s (%d films)' % (b['Name'], len(k)) for b, k in L))


def act_crit_flatten(g, req):
    """Each box set kept as a collection inside The Criterion Collection taken apart, as Criterion's other box sets are: its films (and
    any others in the same folder, as Process Red was left out of A Hollis Frampton Odyssey) put in The Criterion Collection itself,
    the set they're in kept by folder for the site, then the collection deleted (the films stay). Then backdrops fetched for Criterion
    films with none, their own pictures kept (John, 18 Nov 2026, "Make sure all art work is in place")."""
    crit, L = _crit_sets(g)
    if not crit:
        return 'No Criterion Collection found.'
    mf = os.path.expanduser('~/Grifflix/criterion-set-members.json')
    mem = load(mf, {}) or {}
    q = lambda **k: (g.jf.call('GET', '/Users/%s/Items' % g.uid, query=k) or {}).get('Items', [])
    movies = q(Recursive='true', IncludeItemTypes='Movie', Fields='Path')
    gone, put = [], 0
    for b, kids in L:
        folders = sorted(set(k.get('Path') if k.get('Type') == 'Series' else os.path.dirname(k.get('Path') or '') for k in kids if k.get('Path')))
        ids = [k['Id'] for k in kids] + [m['Id'] for m in movies if os.path.dirname(m.get('Path') or '') in folders and m['Id'] not in [k['Id'] for k in kids]]
        mem[b['Name']] = sorted(set(mem.get(b['Name'], [])) | set(folders))
        save(mf, mem)
        g.jf.call('POST', '/Collections/%s/Items' % crit['Id'], query={'Ids': ','.join(ids)})
        put += len(ids)
        g.jf.call('DELETE', '/Items/%s' % b['Id'])
        gone.append('%s (%d)' % (b['Name'], len(ids)))
    for nm in list(ARRANGE.get('The Criterion Collection', {}).get('sets', [])):
        ARRANGE['The Criterion Collection']['sets'].remove(nm)   # (not put back as a collection by the next Update)
    def backdrops():
        n = 0
        for it in q(ParentId=crit['Id'], Fields='BackdropImageTags'):
            if it.get('Type') in ('Movie', 'Series') and not it.get('BackdropImageTags'):
                try:
                    g.jf.call('POST', '/Items/%s/Refresh' % it['Id'], query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'FullRefresh', 'ReplaceAllMetadata': 'false', 'ReplaceAllImages': 'false'})
                    n += 1
                    time.sleep(3)
                except Exception as e:
                    log('Backdrop for %s: %s' % (it.get('Name'), e))
        log('Criterion: the media server asked for backdrops for %d film(s) without one' % n)
        g.forget()
        g.publish_soon = time.time() + 300
    threading.Thread(target=backdrops, daemon=True).start()
    g.forget()
    g.publish_soon = time.time() + 300
    log('Criterion: box sets taken apart: %s' % ', '.join(gone))
    return '%d collections taken apart (%s): %d films now in The Criterion Collection itself, each keeping its box set. Backdrops are being fetched for Criterion films without one; the site follows in a few minutes.' % (len(gone), ', '.join(gone), put)


def ask_collection_add(req):
    """Films put in one of the collections, on John's Allow: which films, by name, shown on the request (John, 19 Nov 2026: "use tmdb to
    find what films are by A24 - there's already a collection, and add any missing")."""
    g = G_REF[0] if G_REF else None
    a = req.get('args') or {}
    cid, ids = str(a.get('collection') or ''), [str(x) for x in (a.get('ids') or []) if re.match(r'^[0-9a-f]{32}$', str(x))][:200]
    if not g or not re.match(r'^[0-9a-f]{32}$', cid) or not ids:
        raise RuntimeError('which collection, and which films?')
    c = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, cid)) or {}
    if c.get('Type') != 'BoxSet':
        raise RuntimeError('no such collection')
    names = []
    for i in ids:
        it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, i)) or {}
        if it.get('Type') not in ('Movie', 'Series'):
            raise RuntimeError('not a film or series: %s' % i)
        names.append('%s (%s)' % (it.get('Name'), it.get('ProductionYear') or '?'))
    req['title'] = 'Put %d film%s in the %s collection' % (len(ids), '' if len(ids) == 1 else 's', c.get('Name'))
    req['detail'] = ', '.join(names)
    req['args'] = {'collection': cid, 'ids': ids, 'name': c.get('Name')}


def act_collection_add(g, req):
    a = req.get('args') or {}
    g.jf.call('POST', '/Collections/%s/Items' % a['collection'], query={'Ids': ','.join(a['ids'])})
    g.forget()
    g.publish_soon = time.time() + 120
    log('Put in %s, on John’s Allow: %s' % (a.get('name'), req.get('detail', '')[:300]))
    return '%d films are in %s now; the site follows in a few minutes.' % (len(a['ids']), a.get('name'))


def ask_collection_move(req):
    """Films taken out of one collection and put in another (made if it isn't there yet), on John's Allow, each film named on the
    request (John, 19 Nov 2026: "put everything not listed on the website in a new collection A24 CHECKS ... and remove them from the main
    A24 list")."""
    g = G_REF[0] if G_REF else None
    a = req.get('args') or {}
    src, to = str(a.get('from') or ''), re.sub(r'\s+', ' ', str(a.get('to') or '')).strip()[:80]
    ids = list(dict.fromkeys(str(x) for x in (a.get('ids') or []) if re.match(r'^[0-9a-f]{32}$', str(x))))[:200]
    if not g or not re.match(r'^[0-9a-f]{32}$', src) or not to or not ids:
        raise RuntimeError('from which collection, to which, and which films?')
    c = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, src)) or {}
    if c.get('Type') != 'BoxSet':
        raise RuntimeError('no such collection')
    held = {i['Id']: i for i in g.items(ParentId=src, Fields='', EnableImageTypes='').get('Items', [])}
    gone = [i for i in ids if i not in held]
    if gone:
        raise RuntimeError('%d of those films aren’t in %s' % (len(gone), c.get('Name')))
    names = sorted('%s (%s)' % (held[i].get('Name'), held[i].get('ProductionYear') or '?') for i in ids)
    req['title'] = 'Move %d film%s from %s to %s%s' % (len(ids), '' if len(ids) == 1 else 's', c.get('Name'), to, ' (yours alone)' if owner_only_set(to) else '')
    req['detail'] = ', '.join(names)
    req['args'] = {'from': src, 'to': to, 'ids': ids, 'name': c.get('Name')}


def act_collection_move(g, req):
    a = req.get('args') or {}
    sets = g.items(Recursive='true', IncludeItemTypes='BoxSet', Fields='').get('Items', [])
    dest = next((s for s in sets if (s.get('Name') or '').strip().lower() == a['to'].lower()), None)
    if dest:
        g.jf.call('POST', '/Collections/%s/Items' % dest['Id'], query={'Ids': ','.join(a['ids'])})
    else:
        dest = g.jf.call('POST', '/Collections', query={'Name': a['to'], 'IsLocked': 'true', 'Ids': ','.join(a['ids'])}) or {}
    g.jf.call('DELETE', '/Collections/%s/Items' % a['from'], query={'Ids': ','.join(a['ids'])})
    g.forget()
    g.publish_soon = time.time() + 120
    log('Moved from %s to %s, on John’s Allow: %s' % (a.get('name'), a['to'], req.get('detail', '')[:300]))
    return '%d films moved from %s to %s%s; the site follows in a few minutes.' % (len(a['ids']), a.get('name'), a['to'], ' (only you see it)' if owner_only_set(a['to']) else '')


TIDY_HOME = os.path.expanduser('~')
TIDY_GONE = ['Desktop/00008150-000974A60238C01C', 'Desktop/whatsapp-export']   # (an old iPhone backup and a WhatsApp export, from iCloud: not needed)
TIDY_APPS = ['/Applications/Atom.app', '/Applications/MakeMKV.app', '/Applications/NordVPN.app', '/Applications/Plex Media Server.app']
TIDY_PLEX = os.path.join(TIDY_HOME, 'Library/Application Support/Plex Media Server')


def _du(p):
    import subprocess
    try:
        r = subprocess.run(['du', '-sk', p], capture_output=True, text=True, timeout=300)
        return int((r.stdout.split() or ['0'])[0]) * 1024
    except Exception:
        return 0


def ask_disk_tidy(req):
    """Room made on the Mac mini's own disk, on John's Allow (John, 19 Nov 2026: "iphone backup and whatsapp ... can be deleted completely..
    Plex can go, so long as nothing useful from the library could not be used. Old apps can go. Empty cache"): what goes, and how big."""
    import shutil
    parts = []
    for rel in TIDY_GONE:
        p = os.path.join(TIDY_HOME, rel)
        if os.path.exists(p):
            parts.append('%s (%.1f GB)' % (rel, _du(p) / 1e9))
    if os.path.isdir(TIDY_PLEX):
        parts.append('Plex’s library data, all of it (%.1f GB)' % (_du(TIDY_PLEX) / 1e9))
    for a in TIDY_APPS:
        if os.path.exists(a):
            parts.append('%s (%.1f GB)' % (os.path.basename(a), _du(a) / 1e9))
    parts.append('the caches in ~/Library/Caches (%.1f GB)' % (_du(os.path.join(TIDY_HOME, 'Library/Caches')) / 1e9))
    req['title'] = 'Make room on the Mac mini’s own disk'
    req['detail'] = 'Deleted for good: ' + '; '.join(parts)


def act_disk_tidy(g, req):
    import shutil
    free0, done, kept = shutil.disk_usage('/').free, [], []
    for rel in TIDY_GONE:
        p = os.path.join(TIDY_HOME, rel)
        if os.path.exists(p):
            shutil.rmtree(p, ignore_errors=True)
            (kept if os.path.exists(p) else done).append(rel.split('/')[-1])
    if os.path.isdir(TIDY_PLEX):   # (Plex quit and kept off, then all of it gone: nothing from it wanted: John, 19 Nov 2026, "don't need watch
        # history kept from plex - probably don't need any of it now")
        try:
            act_plex_off(g, req)
        except Exception:
            pass
        shutil.rmtree(TIDY_PLEX, ignore_errors=True)
        (kept if os.path.exists(TIDY_PLEX) else done).append('Plex’s library data')
    for a in TIDY_APPS:
        if os.path.exists(a):
            shutil.rmtree(a, ignore_errors=True)
            (kept if os.path.exists(a) else done).append(os.path.basename(a))
    cache = os.path.join(TIDY_HOME, 'Library/Caches')
    for n in os.listdir(cache) if os.path.isdir(cache) else []:
        x = os.path.join(cache, n)
        try:
            shutil.rmtree(x) if os.path.isdir(x) and not os.path.islink(x) else os.remove(x)
        except Exception:
            pass
    done.append('the caches')
    freed = (shutil.disk_usage('/').free - free0) / 1e9
    log('Tidy, on John’s Allow: %s%s; %.1f GB made' % (', '.join(done), (' (not: ' + ', '.join(kept) + ')') if kept else '', freed))
    return '%.1f GB made on the Mac mini’s own disk (%.0f GB free now). Gone: %s.%s' % (freed, shutil.disk_usage('/').free / 1e9, ', '.join(done), (' Couldn’t remove: ' + ', '.join(kept) + '.') if kept else '')


# (W. C. Fields—Six Short Films: Criterion's release came as one file of all six shorts, so it read as one film, unmatched, and not as
# its box set. Cut at the black gaps between them, each short its own file in the release's folder, as Criterion's other anthologies
# are kept (A Hollis Frampton Odyssey): John, 20 Nov 2026, "needs adding correctly to the criterion collection with box sets". The
# order is the disc's, as John gave it; the lengths the films' own, to find each gap near where it should be.)
FIELDS_DIR = '/Volumes/G-Drive/Grifflix/Movies/W. C. Fields—Six Short Films (1933)'
FIELDS_SHORTS = [('Pool Sharks', 1915, 11), ('The Golf Specialist', 1930, 21), ('The Dentist', 1932, 21), ('The Fatal Glass of Beer', 1933, 20),
                 ('The Pharmacist', 1933, 19), ('The Barber Shop', 1933, 21)]   # (the disc's order, from DVD Movie Guide's review, which John found: 11 min, the rest 19-22)
FIELDS_CUTS = os.path.expanduser('~/Grifflix/fields-cuts.json')
FIELDS_BUSY = []


def _ffbin(name='ffmpeg'):
    for c in ('/Applications/Jellyfin.app/Contents/MacOS/' + name, '/opt/homebrew/bin/' + name, '/usr/local/bin/' + name):
        if os.path.exists(c):
            return c
    return name


def _fields_src():
    if not os.path.isdir(FIELDS_DIR):
        return None
    v = [n for n in os.listdir(FIELDS_DIR) if n.lower().endswith(('.avi', '.mkv', '.mp4', '.m4v')) and not n.startswith('.')]
    return os.path.join(FIELDS_DIR, v[0]) if len(v) == 1 else None


def _fields_measure(src):
    """The file's black stretches and silences (ffmpeg's blackdetect and silencedetect), kept in fields-cuts.json."""
    try:   # (only the minutes round each join, small: the whole film took over 20 minutes on the busy Mac, John, 20 Nov 2026, "this is too slow")
        rc, out = _run([_ffbin(), '-hide_banner', '-i', src], t=60)
        dm = re.search(r'Duration:\s*(\d+):(\d+):([\d.]+)', out)
        dur = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3)) if dm else 0
        scale = dur / (sum(x[2] for x in FIELDS_SHORTS) * 60.0) if dur else 1
        black, sil, at = [], [], 0.0
        for _n, _y, mins in FIELDS_SHORTS[:-1]:
            at += mins * 60 * scale
            w0 = max(0.0, at - 330)
            rc, out = _run([_ffbin(), '-hide_banner', '-nostats', '-ss', '%.1f' % w0, '-t', '660', '-i', src, '-vf', 'scale=160:-2,blackdetect=d=0.3:pix_th=0.12',
                            '-af', 'silencedetect=n=-42dB:d=0.6', '-f', 'null', '-'], t=900)
            for a_, b_ in re.findall(r'black_start:([\d.]+)\s+black_end:([\d.]+)', out):
                black.append([w0 + float(a_), w0 + float(b_)])
            st = None
            for k, v in re.findall(r'silence_(start|end):\s*([\d.]+)', out):
                if k == 'start':
                    st = float(v)
                elif st is not None:
                    sil.append([w0 + st, w0 + float(v)])
                    st = None
        save(FIELDS_CUTS, {'src': src, 'size': os.path.getsize(src), 'dur': dur, 'black': black, 'silence': sil, 'at': int(time.time())})
        log('W. C. Fields: measured, %d black stretches, %d silences, %.0f min' % (len(black), len(sil), dur / 60))
    except Exception as e:
        log('W. C. Fields: couldn’t measure: %s' % e)
    finally:
        FIELDS_BUSY.clear()


def _fields_plan():
    """[(name, year, start, end)] from the measured gaps: for each join, the longest black stretch (longer still where it's silent too)
    within four minutes of where the films' lengths put it."""
    src = _fields_src()
    if not src:
        raise RuntimeError('the W. C. Fields folder doesn’t hold one film file now (done already?)')
    m = load(FIELDS_CUTS, {}) or {}
    if m.get('src') != src or m.get('size') != os.path.getsize(src):
        if not FIELDS_BUSY:
            FIELDS_BUSY.append(1)
            threading.Thread(target=_fields_measure, args=(src,), daemon=True).start()
        raise RuntimeError('measuring the file’s black gaps now (a few minutes): ask again then')
    dur, black, sil = m['dur'], m['black'], m['silence']
    if not dur:
        raise RuntimeError('couldn’t read the file’s length')
    scale = dur / (sum(x[2] for x in FIELDS_SHORTS) * 60.0)
    cuts, at, prev = [], 0.0, 0.0
    for name, y, mins in FIELDS_SHORTS[:-1]:
        at += mins * 60 * scale
        best = None
        for a, b in black:
            mid = (a + b) / 2
            if mid <= prev + 300 or abs(mid - at) > 240:
                continue
            quiet = sum(max(0, min(b, s2) - max(a, s1)) for s1, s2 in sil)
            sc = (b - a) + quiet - abs(mid - at) / 120.0
            if not best or sc > best[0]:
                best = (sc, mid, b - a, quiet)
        if not best:
            raise RuntimeError('no gap found after %s, near %d:%02d' % (name, at // 60, at % 60))
        cuts.append(best[1])
        prev = best[1]
        at = best[1]
    edges = [0.0] + cuts + [dur]
    return src, [(n, y, edges[k], edges[k + 1]) for k, (n, y, _m) in enumerate(FIELDS_SHORTS)]


def ask_fields_split(req):
    src, plan = _fields_plan()
    mmss = lambda t: '%d:%02d' % (t // 60, t % 60)
    req['detail'] = 'Cut at the black gaps into six films in its own folder, the picture and sound copied as they are: ' + '; '.join(
        '%s (%s) %s–%s, %d min' % (n, y, mmss(a), mmss(b), round((b - a) / 60)) for n, y, a, b in plan) + \
        '. The whole file and its poster go to Set aside (nothing deleted). It then shows as Criterion’s box set, spine 79.'


def act_fields_split(g, req):
    import shutil
    src, plan = _fields_plan()
    old = next((it['Id'] for it in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path'}) or {}).get('Items', [])
                if it.get('Path') == src), None)
    made = []
    for n, y, a, b in plan:
        out = os.path.join(FIELDS_DIR, '%s (%d).mkv' % (n, y))
        tmp = out + '.part.mkv'
        rc, msg = _run([_ffbin(), '-hide_banner', '-nostats', '-y', '-ss', '%.3f' % a, '-i', src, '-t', '%.3f' % (b - a), '-map', '0:v:0', '-map', '0:a?',
                        '-c', 'copy', '-fflags', '+genpts', tmp], t=1800)
        if rc != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 1e6:
            for f in made + [tmp]:
                if os.path.exists(f):
                    os.remove(f)
            raise RuntimeError('cutting %s failed: %s' % (n, msg[-200:]))
        os.rename(tmp, out)
        made.append(out)
    os.makedirs(SETASIDE, exist_ok=True)
    keep = os.path.join(SETASIDE, 'W. C. Fields (whole file)')
    os.makedirs(keep, exist_ok=True)
    shutil.move(src, os.path.join(keep, os.path.basename(src)))
    for x in ('poster.jpg', 'fanart.jpg', 'folder.jpg'):
        if os.path.exists(os.path.join(FIELDS_DIR, x)):
            shutil.move(os.path.join(FIELDS_DIR, x), os.path.join(keep, x))
    try:
        g.jf.call('POST', '/Library/Media/Updated', {'Updates': [{'Path': src, 'UpdateType': 'Deleted'}] + [{'Path': f, 'UpdateType': 'Created'} for f in made]})
    except Exception:
        g.jf.call('POST', '/Library/Refresh')

    def follow():   # (once the media server has them: a calendar day of the whole file goes to the first short)
        for _ in range(40):
            time.sleep(30)
            by = {it.get('Path'): it['Id'] for it in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path'}) or {}).get('Items', [])
                  if (it.get('Path') or '').startswith(FIELDS_DIR + '/')}
            if len(by) >= len(made):
                break
        if old and by.get(made[0]):
            g.remap({old: by[made[0]]})
        g.forget()
        g.publish_soon = time.time() + 120
        log('W. C. Fields: %d of %d shorts read by the media server' % (len(by), len(made)))
    threading.Thread(target=follow, daemon=True).start()
    return 'Six films made: %s. The whole file and its poster are in Set aside.' % ', '.join(os.path.basename(f) for f in made)


# (Scenes from a Marriage (1973): Bergman's six-part TV series, kept as one film in six parts ("-pt1" to "-pt6") in the films folder, so the
# media server played only the first and TMDB had no film for it. Moved to the TV folder as a series of six episodes, as Fanny and
# Alexander is: John, 20 Nov 2026, "scenes from a marriage may be just one part of the tv series" ... "yes move it")
SFAM_SRC = '/Volumes/G-Drive/Grifflix/Movies/Scenes from a Marriage (1973)'
SFAM_DST = '/Volumes/G-Drive/Grifflix/TV/Scenes from a Marriage (1973)'


def _sfam_moves():
    if not os.path.isdir(SFAM_SRC):
        return []
    mv = []
    for n in sorted(os.listdir(SFAM_SRC)):
        if n.startswith('.'):
            continue
        m = re.search(r'-pt(\d)\.(\w+)$', n)
        if m:
            mv.append((os.path.join(SFAM_SRC, n), os.path.join(SFAM_DST, 'Season 01', 'Scenes from a Marriage (1973) S01E%02d.%s' % (int(m.group(1)), m.group(2)))))
        else:   # (its poster, background and extras: the series' own)
            mv.append((os.path.join(SFAM_SRC, n), os.path.join(SFAM_DST, n)))
    return mv


def ask_sfam_move(req):
    mv = _sfam_moves()
    eps = [b for a, b in mv if 'Season 01' in b]
    if len(eps) != 6:
        raise RuntimeError('expected its six parts in %s, found %d' % (SFAM_SRC, len(eps)))
    for a, b in mv:
        if os.path.exists(b):
            raise RuntimeError('%s is there already' % b)
    req['detail'] = 'Its six parts moved to TV/Scenes from a Marriage (1973)/Season 01 as episodes 1 to 6 (%s … %s), its poster, background and Extras with them. Nothing deleted; the files are only moved and renamed. The media server then reads it as the 1973 TV series, and it stays in The Criterion Collection.' % (
        os.path.basename(eps[0]), os.path.basename(eps[-1]))


def act_sfam_move(g, req):
    import shutil
    mv = _sfam_moves()
    old = next((it['Id'] for it in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path'}, timeout=120) or {}).get('Items', [])
                if (it.get('Path') or '').startswith(SFAM_SRC + '/')), None)
    os.makedirs(os.path.join(SFAM_DST, 'Season 01'), exist_ok=True)
    for a, b in mv:
        shutil.move(a, b)
    try:   # (the old folder gone, only the Finder's hidden files left in it)
        for n in os.listdir(SFAM_SRC):
            if n == '.DS_Store' or n.startswith('._'):
                os.remove(os.path.join(SFAM_SRC, n))
        os.rmdir(SFAM_SRC)
    except OSError:
        pass
    try:
        g.jf.call('POST', '/Library/Media/Updated', {'Updates': [{'Path': SFAM_SRC, 'UpdateType': 'Deleted'}, {'Path': SFAM_DST, 'UpdateType': 'Created'}]})
    except Exception:
        g.jf.call('POST', '/Library/Refresh')

    def follow():   # (once the media server has the series: a calendar day or list of the old film goes to it)
        new = None
        for _ in range(40):
            time.sleep(30)
            new = next((it['Id'] for it in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Series', 'Fields': 'Path'}, timeout=120) or {}).get('Items', [])
                        if (it.get('Path') or '') == SFAM_DST), None)
            if new:
                break
        if old and new:
            g.remap({old: new})
        g.forget()
        g.publish_soon = time.time() + 120
        log('Scenes from a Marriage: %s' % ('a TV series now' if new else 'moved, the media server hasn’t read it yet'))
    threading.Thread(target=follow, daemon=True).start()
    return 'Moved: %d files into TV/Scenes from a Marriage (1973), its six parts as episodes 1 to 6.' % len(mv)


def ask_set_imdb(req):
    a = req.get('args') or {}
    g = G_REF[0] if G_REF else None
    if not (g and re.match(r'^[0-9a-f]{32}$', str(a.get('id') or '')) and re.match(r'^tt\d{6,10}$', str(a.get('imdb') or ''))):
        raise RuntimeError('which film, and which IMDb number?')
    it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, a['id'])) or {}
    if not it:
        raise RuntimeError('that film isn’t there')
    req['detail'] = '%s (%s): IMDb number %s put on it; its name, year and pictures stay as they are.' % (it.get('Name'), it.get('ProductionYear') or '?', a['imdb'])


def act_set_imdb(g, req):
    """An IMDb number TMDB has no film for put on a film, as the Unmatched card's "Keep it as it is" does (World on a Wire, 20 Nov 2026)."""
    a = req.get('args') or {}
    full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, a['id'])) or {}
    pid = dict(full.get('ProviderIds') or {})
    pid['Imdb'] = a['imdb']
    full['ProviderIds'] = pid
    full['LockedFields'] = sorted(set((full.get('LockedFields') or []) + ['Name']))
    g.jf.call('POST', '/Items/%s' % a['id'], full)
    g.forget()
    g.publish_soon = time.time() + 60
    after_fixes()
    return '%s now has %s.' % (full.get('Name'), a['imdb'])


def ask_restore_aside(req):
    """A film put back from Set aside/Duplicates into the films folder (Demon Pond (1979), set aside as a copy with no other copy left: John,
    21 Nov 2026, "Demon Pond (1979) was set aside but not sure how or why")."""
    n = str((req.get('args') or {}).get('name') or '')
    src = os.path.join(SETASIDE, 'Duplicates', n)
    if not n or '/' in n or n.startswith('.') or not os.path.isdir(src):
        raise RuntimeError('nothing called that in Set aside › Duplicates')
    files = [x for x in os.listdir(src) if not x.startswith('.')]
    req['detail'] = '%s: %s moved back to Movies/%s, beside what’s left there (its poster, background and extras). Nothing deleted.' % (n, ', '.join(files)[:300], n)


def act_restore_aside(g, req):
    import shutil
    n = str((req.get('args') or {}).get('name') or '')
    src, dst = os.path.join(SETASIDE, 'Duplicates', n), os.path.join('/Volumes/G-Drive/Grifflix/Movies', n)
    os.makedirs(dst, exist_ok=True)
    moved = []
    for x in os.listdir(src):
        if x.startswith('.'):
            continue
        if os.path.exists(os.path.join(dst, x)):
            raise RuntimeError('%s is in Movies/%s already' % (x, n))
        shutil.move(os.path.join(src, x), os.path.join(dst, x))
        moved.append(x)
    try:
        for x in os.listdir(src):
            os.remove(os.path.join(src, x))
        os.rmdir(src)
    except OSError:
        pass
    try:
        g.jf.call('POST', '/Library/Media/Updated', {'Updates': [{'Path': dst, 'UpdateType': 'Modified'}]})
    except Exception:
        g.jf.call('POST', '/Library/Refresh')
    g.forget()
    g.publish_soon = time.time() + 600
    return 'Put back in Movies/%s: %s.' % (n, ', '.join(moved))


ACTIONS = {
    'plex-off': ('Quit Plex and keep it off', act_plex_off, None),
    'plex-on': ('Start Plex again and let it be', act_plex_on, None),
    'jellyfin-restart': ('Restart the media server (not while anyone watches)', act_jellyfin_restart, None),
    'converter-restart': ('Restart the converter (it starts its film again)', act_converter_restart, None),
    'setaside-empty': ('Empty “Set aside” on the G-Drive (deleted for good)', act_setaside_empty, ask_setaside),
    'extras-rename': ('Rename each film’s “Other” folder to “Extras”, so the media server shows its extras', act_extras_rename, ask_extras_rename),
    'criterion-sets-flatten': ('Take apart the box sets kept as collections inside The Criterion Collection, so they’re like its other box sets (the films stay)', act_crit_flatten, ask_crit_flatten),
    'collection-add': ('Put films in a collection', act_collection_add, ask_collection_add),
    'collection-move': ('Move films from one collection to another', act_collection_move, ask_collection_move),
    'disk-tidy': ('Make room on the Mac mini’s own disk (old backups, Plex, old apps, caches)', act_disk_tidy, ask_disk_tidy),
    'restore-aside': ('Put a film back from Set aside › Duplicates', act_restore_aside, ask_restore_aside),
    'set-imdb': ('Put an IMDb number on a film TMDB has no film for', act_set_imdb, ask_set_imdb),
    'sfam-move': ('Move Scenes from a Marriage (1973) to TV, its six parts as the series’ six episodes', act_sfam_move, ask_sfam_move),
    'fields-split': ('Cut W. C. Fields—Six Short Films into its six shorts, so it shows as its Criterion box set', act_fields_split, ask_fields_split),
    'brakhage-split': ('Give each By Brakhage film its own folder (Volume One, Volume Two and the seven films from Volume One)', act_brakhage_split, ask_brakhage_split),
}


ACT_RUNNING = set()   # (what John allowed, being done now: the gateway doesn't restart for an update until it's finished)


def acts_interrupted():
    """At the start: anything left 'doing' was stopped by the gateway stopping, so it goes back to waiting on John's Allow (each action is
    safe to do again: what's already done is passed over)."""
    with ACT_LOCK:
        L = load(ACTIONS_AT, []) or []
        back = [x for x in L if x.get('state') == 'doing']
        for x in back:
            x['state'] = 'asked'
            x['why'] = ('Stopped partway when the gateway restarted; asked again. ' + (x.get('why') or ''))[:500]
            x.pop('answered', None)
            if ACTIONS.get(x.get('action'), (0, 0, None))[2]:
                try:
                    ACTIONS[x['action']][2](x)   # (what's left, measured afresh)
                except Exception:
                    pass
        if back:
            save(ACTIONS_AT, L)
    for x in back:
        log('Claude asks again (stopped partway by a restart): %s' % x.get('title'))


def claude_ask(action, why, args=None):
    if action not in ACTIONS:
        return 400, {'error': 'Not something that can be asked for.', 'can': sorted(ACTIONS)}
    with ACT_LOCK:
        L = load(ACTIONS_AT, []) or []
        if sum(1 for x in L if x.get('state') == 'asked') >= 10:
            return 429, {'error': 'Ten are already waiting for John.'}
        if any(x.get('state') == 'asked' and x.get('action') == action for x in L):
            return 409, {'error': 'Already asked, waiting for John.'}
        req = {'id': secrets.token_hex(6), 'action': action, 'title': ACTIONS[action][0], 'why': str(why or '')[:500], 'at': int(time.time()), 'state': 'asked'}
        if isinstance(args, dict):   # (what it's for, checked by its own ask step: a collection and its films, say)
            req['args'] = {str(k)[:40]: v for k, v in list(args.items())[:10]}
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
        ACT_RUNNING.add(rid)
        try:
            res, st = ACTIONS[req['action']][1](g, req), 'done'
        except Exception as e:
            res, st = str(e)[:300], 'failed'
        finally:
            ACT_RUNNING.discard(rid)
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
    # (each film asked about rests a day, so the next ones get their turn: the same first 60 were asked every time and those after them,
    # The Crying Game among them, never were: John, 18 Nov 2026, "I still can't see Extras")
    af = os.path.expanduser('~/Grifflix/extras-asked.json')
    had = {k: v for k, v in (load(af, {}) or {}).items() if time.time() - v < 86400}
    for it in its:
        if (it.get('SpecialFeatureCount') or 0) or len(asked) >= most or it['Id'] in had:
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
            had[it['Id']] = time.time()
            time.sleep(2)
        except Exception as e:
            log('Extras for %s: %s' % (it.get('Name'), e))
    save(af, had)
    if asked:
        log('Extras: the media server asked to look again at %d film%s with extras it hadn’t found: %s' % (len(asked), '' if len(asked) == 1 else 's', ', '.join(asked[:12]) + (' …' if len(asked) > 12 else '')))
        g.forget()
        g.publish_soon = time.time() + 600
    return len(asked)


def keep_extras(g):
    """extras_fix 6 minutes after the start, then every half hour while there are films to ask about, but not while the media server is
    looking at the whole library (which finds them itself), else every 6 hours."""
    time.sleep(360)
    while True:
        n_ = 0
        try:
            if not any(t.get('Key') == 'RefreshLibrary' and t.get('State') == 'Running' for t in g.jf.call('GET', '/ScheduledTasks') or []):
                n_ = extras_fix(g)
            else:
                n_ = 1   # (looked at again in half an hour)
        except Exception as e:
            log('Extras:', str(e)[:200])
        time.sleep(1800 if n_ else 6 * 3600)


def hashes_for(path):
    """The file's fingerprint for OpenSubtitles, and the one it had as it came, remembered by the converter when it made this file
    (fingerprints.json): subtitles made for the copy as downloaded are known by that (John, 22 Nov 2026, "I suspect there is a mismatch in
    finding"). Originals are deleted once converted, so that record is the only way back to them."""
    out = []
    try:
        out.append(SUBS.movie_hash(path))
    except OSError:
        pass
    fp_ = (load(os.path.expanduser('~/Grifflix/fingerprints.json'), {}) or {}).get(path) or {}
    out += [h for h in [fp_.get('hash')] + list(fp_.get('hashes') or []) if h]
    return [h for h in dict.fromkeys(out) if h]


def chk(e, step, said):
    """What one of the checks for a film found, kept on it to show on the admin page: John, 22 Nov 2026, "many say waiting for whisper but
    I can't see what checks have been done to prove that we need to get to this step"."""
    e.setdefault('checks', {})[step] = {'at': int(time.time()), 'r': str(said)[:300]}


def exact_sub(g, cl, e, iid, path):
    """An English subtitle made for this very file (OpenSubtitles knows it by the file's fingerprint), its timing right as it is: taken and
    saved beside it, no Whisper needed (John, 22 Nov 2026, "I have open sub pro so no problem to get them - get ones with exact timing").
    True if saved."""
    hs_ = hashes_for(path)
    ids = [fid for mh in hs_ for fid, lang in cl.exact_any(mh) if str(lang).lower() in ('en', 'eng')]
    if not ids:
        chk(e, 'exact', 'No English subtitle on OpenSubtitles matches its fingerprint (%d fingerprint%s looked up: the file as it is%s)' % (len(hs_), '' if len(hs_) == 1 else 's', ', and as it was before converting' if len(hs_) > 1 else ''))
    for fid in ids[:2]:
        cues = SUBS.parse_srt(SUBS.decode(cl.download(fid)))
        if len(cues) < 20:
            continue
        out = '%s.Grifflix.en.srt' % os.path.splitext(path)[0]
        with open(out + '.tmp', 'w', encoding='utf-8') as f:
            f.write(SUBS.write_srt(cues))
        os.replace(out + '.tmp', out)
        try:
            g.jf.call('POST', '/Items/%s/Refresh' % iid, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
        except Exception:
            pass
        chk(e, 'exact', 'Found: an English subtitle made for this very file (by its fingerprint), used as it is')
        e.update(state='done', note='made for this very file (OpenSubtitles, by its fingerprint): its timing as it is', chose='OpenSubtitles file %s' % fid,
                 file=out, at=int(time.time()), sure=True, n=len(cues), how='exact')
        log('Subtitles made for the very file: %s (%d lines)' % (e.get('name'), len(cues)))
        record('subtitles saved', film=e.get('name'), id=iid, file=out, how='made for this very file (OpenSubtitles fingerprint)', opensubtitles_file=fid, lines=len(cues))
        return True
    return False


def own_foreign_sub(g, iid, path):
    """A text subtitle in a language not English that the file has in it, or that's beside it: (its language, its lines), or None."""
    try:
        it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'MediaStreams'}) or {}
    except Exception:
        return None
    import subprocess
    ff = next((c for c in ('/Applications/Jellyfin.app/Contents/MacOS/ffmpeg', '/usr/local/bin/ffmpeg', '/opt/homebrew/bin/ffmpeg') if os.path.exists(c)), 'ffmpeg')
    for x in it.get('MediaStreams') or []:
        lg = str(x.get('Language') or '').lower()
        if x.get('Type') != 'Subtitle' or not x.get('IsTextSubtitleStream') or not lg or lg in ('eng', 'en', 'und') or x.get('IsForced'):
            continue
        try:
            if x.get('IsExternal') and x.get('Path'):
                cues = SUBS.parse_srt(SUBS.decode(open(x['Path'], 'rb').read()))
            else:
                out = subprocess.run([ff, '-v', 'error', '-i', path, '-map', '0:%d' % x['Index'], '-f', 'srt', '-'], capture_output=True, timeout=900).stdout
                cues = SUBS.parse_srt(SUBS.decode(out))
        except Exception:
            continue
        if len(cues) >= 50:
            return (lg, cues)
    return None


def subs_compare(g, P, iid, w=''):
    """One film's English subtitles set beside what's said, for the admin page (John, 22 Nov 2026, "showing a clear comparison or the
    ability to choose and see - this should be on anything adjusted"): in four parts of the film, each line said (by the subtitle made for
    this copy, in its language, with your Mac's English of it where there is one; or by what Whisper heard), the English line that showed
    there before it was put right, and the one that shows now. w: one of the others to see instead of the one in use."""
    e = P.get(iid) or {}
    cdir = os.path.expanduser('~/Grifflix/subprep-cands')
    rd = lambda f: SUBS.parse_srt(open(f, encoding='utf-8', errors='replace').read()) if f and os.path.exists(f) else []
    w = w if re.match(r'^r?\d$|^tr$|^g$', w or '') else ''
    cur = str(e.get('cur') or '')
    sel = w or cur
    out = {'with': sel, 'cur': cur, 'versions': []}
    # (the versions there are to see: the one in use, the others tried for its copy, the likeliest Whisper turned down, the machine translation)
    for c in e.get('rcands') or []:
        out['versions'].append({'i': c['i'], 'name': c.get('name') or '', 'n': c.get('n'), 'share': c.get('share'), 'kind': 'ref'})
    for c in e.get('cands') or []:
        out['versions'].append({'i': str(c.get('i')), 'name': c.get('name') or '', 'n': c.get('n'), 'kind': 'whisper'})
    if os.path.exists(os.path.join(cdir, '%s.g.srt' % iid)):
        out['versions'].append({'i': 'g', 'name': e.get('givenname') or 'the ones you gave', 'kind': 'given'})
    if os.path.exists(os.path.join(cdir, '%s.tr.srt' % iid)):
        out['versions'].append({'i': 'tr', 'name': 'machine translation of the %s subtitle made for this copy' % ((e.get('translated') or {}).get('lang') or e.get('ref_lang') or ''), 'kind': 'tr'})
    # (Whisper's evidence, where there's no subtitle made for this copy to compare by)
    ref = rd(e.get('ref_file'))
    if not ref:
        ev = load(os.path.join(os.path.expanduser('~/Grifflix/subprep-evidence'), iid + ('.%s' % w if re.match(r'^\d$', w) else '') + '.json'), None) or {}
        out['by'] = 'whisper'
        out['parts'] = [{'from': p.get('from'), 'rows': [{'t': r[0], 'said': r[1], 'now': r[3] if len(r) > 2 else '', 'was_t': r[2] if len(r) > 2 else None, 'now_t': r[4] if len(r) > 2 else None,
                                                          'ok': bool(r[5]) if len(r) > 5 else None} for r in p.get('rows') or []]} for p in ev.get('parts') or []]
        if not out['parts'] and e.get('how') == 'exact':
            out['by'] = 'exact'
        return out
    out['by'] = 'ref'
    out['lang'] = e.get('ref_lang') or ''
    tr = rd(os.path.join(cdir, '%s.tr.srt' % iid))
    if w == 'tr':
        now, was = tr, []
    elif sel and re.match(r'^r\d$|^g$', sel):
        now, was = rd(os.path.join(cdir, '%s.%s.srt' % (iid, sel))), rd(os.path.join(cdir, '%s.%s.orig.srt' % (iid, sel)))
    elif sel and re.match(r'^\d$', sel):
        now, was = rd(os.path.join(cdir, '%s.%s.srt' % (iid, sel))), []
    else:
        now, was = rd(e.get('file')), []
    if not now:
        now = rd(e.get('file'))
    import bisect
    def at(cs, t):   # (the line showing at t, if any)
        if not cs:
            return None
        k = bisect.bisect_right([c[0] for c in cs], t) - 1
        return cs[k] if k >= 0 and cs[k][0] <= t <= cs[k][1] + .3 else None
    end = ref[-1][1] if ref else 0
    parts = []
    # (each English line as it shows, beside what's said then: the information matched, nothing else: John, 22 Nov 2026, "I just need the
    # information matched")
    ov = lambda a, b: min(a[1], b[1]) - max(a[0], b[0]) > .15
    for fr in (.12, .37, .62, .87):   # (four parts, three minutes each)
        t0 = end * fr
        R = [c for c in ref if t0 <= c[0] < t0 + 180]
        N = [c for c in now if t0 <= c[0] < t0 + 180]
        T = [c for c in tr if t0 - 5 <= c[0] < t0 + 185] if w != 'tr' else []
        rows, used = [], set()
        for c in N:
            m_ = [k for k, r in enumerate(R) if ov(c, r)]
            used.update(m_)
            tl = [x[2] for x in T if ov(c, x)]
            rows.append({'t': round(c[0], 1), 'en': c[2], 'said': ' / '.join(R[k][2] for k in m_), 'said_en': ' / '.join(dict.fromkeys(tl)), 'ok': bool(m_)})
        for k, r in enumerate(R):   # (what's said with no English line at all)
            if k not in used:
                tl = [x[2] for x in T if ov(r, x)]
                rows.append({'t': round(r[0], 1), 'en': '', 'said': r[2], 'said_en': ' / '.join(dict.fromkeys(tl)), 'ok': False})
        rows.sort(key=lambda x: x['t'])
        if rows:
            parts.append({'from': round(t0), 'rows': rows[:40]})
    out['parts'] = parts
    return out


SUBASK = {}   # (when John's Mac last asked for subtitles work, and what it was told)
SPEECH = {'ok': False, 'now': None}   # (off: on real films no subtitle stood out by it (Mandabi, Jour de fête, 22 Nov 2026: about 2 against 2 for the next
# best, where the test films gave 9), so it only held films back before Whisper; the files' own checks first, then Whisper)
SP_LOCK = threading.RLock()   # (the subtitles' record written by one at a time)


def subprep_commit(before, after):
    """The subtitles' record written back by something that took a while (the library scan, a look-over): only what it changed, over the
    record as it is now, so what John's Mac or the admin page did meanwhile isn't saved over (a film handed to John's Mac at 22:50 lost,
    the scan saving its older copy over it: John, 22 Nov 2026)."""
    with SP_LOCK:
        cur = load(SUBPREP, {}) or {}
        for k in set(before) | set(after):
            b, a = before.get(k), after.get(k)
            if b == a:
                continue
            if a is None:
                cur.pop(k, None)
            elif b is None or not isinstance(a, dict) or not isinstance(cur.get(k), dict):
                cur[k] = a
            else:
                for f in set(b) | set(a):
                    if b.get(f) != a.get(f):
                        if f in a:
                            cur[k][f] = a[f]
                        else:
                            cur[k].pop(f, None)
        save(SUBPREP, cur)


def speech_try(g, cl, e, iid, path, it):
    """Before Whisper (the last resort): when people speak in the film found (whatever they say), then (1) subtitles in other languages
    tried against it, any whose lines start when people speak in this copy taken as made for it, and the English timed by it; else (2)
    the English ones lined up by it directly. True if subtitles were saved; None to try again later (downloads used up). John, 22 Nov 2026,
    "whisper should be the rarest case - better to find other subtitles in other languages first - whisper only as a last resort"."""
    if not e.get('name_tried'):   # (English named for this very release first: no listening at all if there is one)
        e['name_tried'] = int(time.time())
        SPEECH['now'] = '%s: looking for English named for this very release' % e.get('name')
        if name_sub(g, cl, e, iid, path, it):
            return True
        if cl.out_of_downloads():
            e.pop('name_tried', None)
            return None
    ff = next((c for c in ('/Applications/Jellyfin.app/Contents/MacOS/ffmpeg', '/usr/local/bin/ffmpeg', '/opt/homebrew/bin/ffmpeg') if os.path.exists(c)), 'ffmpeg')
    cf = os.path.join(os.path.expanduser('~/Grifflix/speech'), iid + '.json')
    sz = os.path.getsize(path)
    c_ = load(cf, None) or {}
    if c_.get('size') == sz and c_.get('m') and c_.get('k'):
        mask, n, known = int(c_['m'], 16), int(c_['n']), int(c_['k'], 16)
    else:
        SPEECH['now'] = '%s: listening for when people speak' % e.get('name')
        lv_ = SUBS.speech_levels_parts(path, ff, (it.get('RunTimeTicks') or 0) / 1e7)
        mask, n = SUBS.speech_mask(lv_)
        known = SUBS.known_mask(lv_)
        if not n or not known:
            e['speech_note'] = 'its sound couldn’t be read'
            return False
        os.makedirs(os.path.dirname(cf), exist_ok=True)
        save(cf, {'size': sz, 'n': n, 'm': '%x' % mask, 'k': '%x' % known, 'at': int(time.time())})
    pid = it.get('ProviderIds') or {}
    org = film_origin(path) or os.path.splitext(str(((load(os.path.expanduser('~/Grifflix/fingerprints.json'), {}) or {}).get(path) or {}).get('name') or ''))[0] or None
    hint = org or os.path.splitext(os.path.basename(path))[0]
    def look(lang):
        if e.get('kind') == 'episode':
            return cl.search(lang, query=e.get('series'), parent_tmdb=e.get('series_tmdb'), parent_imdb=e.get('series_imdb'), alt=[e.get('series_orig')], season=e.get('season'), episode=e.get('episode'), hint=hint)
        return cl.search(lang, tmdb=pid.get('Tmdb'), imdb=pid.get('Imdb'), alt=[it.get('OriginalTitle')], query=it.get('Name'), year=it.get('ProductionYear'), hint=hint)
    said = []
    # (1) in other languages: one made for this copy times the English
    if not (e.get('ref_file') and os.path.exists(e.get('ref_file') or '')):
        try:
            fo = [x for x in look('') if not x.get('machine') and str(x.get('lang') or '').lower() not in ('en', 'eng')]
        except Exception as ex:
            fo = []
            said.append('other languages: %s' % str(ex)[:80])
        fo.sort(key=lambda x: (not x.get('samename'), -(x.get('like') or 0), -(x.get('downloads') or 0)))
        for x in fo[:6]:
            SPEECH['now'] = '%s: trying the %s subtitle %s' % (e.get('name'), x.get('lang'), x.get('name') or '')
            try:
                cues = SUBS.parse_srt(SUBS.decode(cl.download(x['file_id'])))
            except Exception:
                if cl.out_of_downloads():
                    return None
                continue
            if len(cues) < 50:
                continue
            f = SUBS.speech_fit(cues, mask, n, ratios=[1.0], span=300, known=known)
            said.append('%s %s: z %s/%s at %+.1f s' % (x.get('lang'), (x.get('name') or '')[:40], f and f['z'], f and f['second'], (f or {}).get('offset', 0)))
            if f and f['z'] >= 5 and f['z'] >= 3 * max(f['second'], .5) and abs(f['offset']) <= .4:
                rf = os.path.join(os.path.expanduser('~/Grifflix/subprep-ref'), '%s.%s.srt' % (iid, x.get('lang') or 'xx'))
                os.makedirs(os.path.dirname(rf), exist_ok=True)
                with open(rf, 'w', encoding='utf-8') as fh:
                    fh.write(SUBS.write_srt(cues))
                e.update(ref_file=rf, ref_lang=str(x.get('lang') or ''), ref_byspeech=(x.get('name') or '')[:160])
                log('Subtitles for %s: the %s subtitle “%s” starts its lines when people speak in this copy: made for it' % (e.get('name'), x.get('lang'), x.get('name')))
                break
    if e.get('ref_byspeech') and e.get('ref_file') and os.path.exists(e['ref_file']):
        e.pop('ref_tried2', None)
        if ref_sub(g, cl, e, iid, path, it):
            e['note'] = str(e.get('note') or '') + ' (that subtitle found by when people speak)'
            e['speech_note'] = '; '.join(said)[:600]
            return True
        if cl.out_of_downloads():
            return None
    # (2) the English ones lined up by when people speak
    try:
        en = [x for x in look('en') if not x.get('machine')]
    except Exception as ex:
        en = []
        said.append('English: %s' % str(ex)[:80])
    dmin = (it.get('RunTimeTicks') or 0) / 6e8
    def lenfit(nm):
        nm = str(nm or '').lower()
        m1 = re.search(r'(\d)\s*h(?:rs?|ours?)?[\s.:]*(\d{1,2})\s*m', nm)
        m2 = re.search(r'\b(\d{2,3})\s*min', nm)
        mm = int(m1.group(1)) * 60 + int(m1.group(2)) if m1 else int(m2.group(1)) if m2 else None
        return 0 if not mm or not dmin else 2 if abs(mm - dmin) <= 4 else -3 if abs(mm - dmin) > 8 else 0
    en.sort(key=lambda x: -lenfit(x.get('name')))   # (stable: OpenSubtitles' own order otherwise)
    for x in en[:4]:
        SPEECH['now'] = '%s: lining up %s by when people speak' % (e.get('name'), x.get('name') or '')
        try:
            cues = SUBS.parse_srt(SUBS.decode(cl.download(x['file_id'])))
        except Exception:
            if cl.out_of_downloads():
                return None
            continue
        if len(cues) < 50 or (dmin and cues[-1][1] > dmin * 60 + 300):
            continue
        f = SUBS.speech_fit(cues, mask, n, known=known)
        said.append('en %s: z %s/%s, %+.1f s at %s×, %d%% on speech' % ((x.get('name') or '')[:40], f and f['z'], f and f['second'], (f or {}).get('offset', 0), round((f or {}).get('ratio', 1), 4), 100 * (f or {}).get('frac', 0)))
        if f and f['z'] >= 8 and f['z'] >= 3.5 * max(f['second'], .5) and f['frac'] >= .6:
            out = SUBS.shift_cues(cues, f['ratio'], f['offset'])
            fn = '%s.Grifflix.en.srt' % os.path.splitext(path)[0]
            with open(fn + '.tmp', 'w', encoding='utf-8') as fh:
                fh.write(SUBS.write_srt(out))
            os.replace(fn + '.tmp', fn)
            try:
                g.jf.call('POST', '/Items/%s/Refresh' % iid, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
            except Exception:
                pass
            sp = '' if abs(f['ratio'] - 1) < 1e-4 else ' at %.4fx speed' % f['ratio']
            e.update(state='done', how='by speech', chose=x.get('name') or '', file=fn, at=int(time.time()), n=len(out), sure=True,
                     note='moved %+.1f s%s: lined up by when people speak (%d%% of its lines on speech)' % (f['offset'], sp, round(100 * f['frac'])))
            e['speech_note'] = '; '.join(said)[:600]
            log('Subtitles for %s: lined up by when people speak: %s' % (e.get('name'), e['note']))
            record('subtitles saved', film=e.get('name'), id=iid, file=fn, how='lined up by when people speak', chose=x.get('name'), fit=f)
            return True
    e['speech_note'] = '; '.join(said)[:600]
    return False


def keep_file_checks(g):
    """Each film waiting for English subtitles has its files checked here on the Mac mini as soon as it's waiting: an English subtitle made
    for this very file, one named for its release, one in another language made for this copy. Not left until John's Mac asks for its next
    film, so the checks show, and only a film none of them found waits for Whisper: John, 22 Nov 2026, "the whisper message is either
    wrong or the checks to whisper just aren't showing"."""
    time.sleep(90)
    while True:
        did = False
        try:
            if not (g.st.get('opensubs') or {}).get('key'):
                time.sleep(3600)
                continue
            cl = SUBS.OpenSubs(g.st.setdefault('opensubs', {}), lambda: save(SETTINGS, g.st), tls())
            if cl.out_of_downloads():
                time.sleep(900)
                continue
            P = load(SUBPREP, {}) or {}
            todo = sorted(((k, v) for k, v in P.items() if not k.startswith('_') and isinstance(v, dict) and v.get('state') == 'waiting'
                           and not v.get('givenfile') and not v.get('ref_only') and not (v.get('exact_tried') and v.get('name_tried') and v.get('ref_tried2'))),
                          key=lambda kv: (not kv[1].get('prio'), kv[1].get('order', 0)))
            for iid, e in todo[:1]:
                did = True
                it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path,ProviderIds,OriginalTitle'}) or {}
                path = it.get('Path') or ''
                e0 = __import__('copy').deepcopy(e)
                e = __import__('copy').deepcopy(e)
                ep_fill(g, e, iid)
                ok = False
                if path and os.path.exists(path):
                    try:
                        if not e.get('exact_tried'):
                            ok = exact_sub(g, cl, e, iid, path)
                            e['exact_tried'] = int(time.time())
                        if not ok and not e.get('name_tried'):
                            ok = name_sub(g, cl, e, iid, path, it)
                            e['name_tried'] = int(time.time())
                        if not ok and e.get('kind') != 'episode' and silent_film(g, iid, it):   # (a silent film: nothing to time any other by)
                            e.update(state='nodialogue', note=SILENT_NOTE, at=int(time.time()))
                            e['ref_tried2'] = int(time.time())
                            chk(e, 'ref', 'A silent film: no subtitle can be timed by its speech, so no more looked for')
                            ok = True
                        if not ok and not e.get('ref_tried2'):
                            ok = ref_sub(g, cl, e, iid, path, it)
                            e['ref_tried2'] = int(time.time())
                    except Exception as ex:
                        if cl.out_of_downloads():
                            break
                        log('Subtitles, the files’ checks for %s: %s' % (e.get('name'), str(ex)[:160]))
                        e['exact_tried'] = e.get('exact_tried') or int(time.time()); e['name_tried'] = e.get('name_tried') or int(time.time()); e['ref_tried2'] = e.get('ref_tried2') or int(time.time())
                else:
                    e['exact_tried'] = e['name_tried'] = e['ref_tried2'] = int(time.time())
                    chk(e, 'exact', 'Its file isn’t there to check')
                with SP_LOCK:
                    Q = load(SUBPREP, {}) or {}
                    if isinstance(Q.get(iid), dict) and Q[iid].get('state') == 'waiting':   # (unless something else has taken it meanwhile: only what the checks changed)
                        subprep_commit({iid: e0}, {iid: e})
        except Exception as ex:
            log('Subtitles, the files’ checks: %s' % str(ex)[:200])
        time.sleep(5 if did else 120)


def subs_none_again(why):
    """Those with no subtitles found anywhere tried again, now there's somewhere new to look."""
    try:
        with SP_LOCK:
            P_ = load(SUBPREP, {}) or {}
            n_ = 0
            for k_, e_ in P_.items():
                if isinstance(e_, dict) and e_.get('state') == 'none':
                    e_.update(state='waiting', note='to be tried again: %s' % why)
                    n_ += 1
            if n_:
                save(SUBPREP, P_)
        log('Subtitles: %d with none found anywhere to be tried again (%s)' % (n_, why))
    except Exception as ex:
        log('Subtitles, tried again: %s' % ex)


def subs_by_timing_again(g):
    """Once: the films turned down because the words of what Whisper heard didn't match the subtitles' looked at again by when their lines
    are said, from what was heard and the likeliest three kept for each: one that fits the whole film so saved (in To check unless it's
    clear), no listening again. John, 23 Nov 2026, "the chances of the wording being exactly right on the timing is very very small": Jour
    de fête's subtitle, a different translation from Whisper's, lined up through the whole film and was turned down."""
    mk = os.path.expanduser('~/Grifflix/subs-timing.done')
    if os.path.exists(mk):
        return
    time.sleep(120)
    cdir, edir, n_ = os.path.expanduser('~/Grifflix/subprep-cands'), os.path.expanduser('~/Grifflix/subprep-evidence'), 0
    try:
        P = load(SUBPREP, {}) or {}
        for iid, e in list(P.items()):
            if not re.match(r'^[0-9a-f]{32}$', iid) or not isinstance(e, dict) or e.get('state') != 'nomatch' or not e.get('cands'):
                continue
            best = None
            for c in e.get('cands') or []:
                fn, ev = os.path.join(cdir, '%s.%s.srt' % (iid, c.get('i'))), load(os.path.join(edir, '%s.%s.json' % (iid, c.get('i'))), None)
                if not isinstance(ev, dict) or not os.path.exists(fn):
                    continue
                heard = [[float(r[0]) for r in (p_.get('rows') or []) if isinstance(r, list) and len(r) > 1 and str(r[1]).strip()] for p_ in ev.get('parts') or []]
                cues = SUBS.parse_srt(open(fn, encoding='utf-8', errors='replace').read())
                ok, sure, h, z, pz = SUBS.onset_fit(heard, sorted(c_[0] for c_ in cues))
                if ok and (not best or z > best[3]):
                    best = (c, cues, sure, z, h, sum(len(x) for x in heard))
            if not best:
                continue
            c, cues, sure, z, h, nh = best
            it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path'}) or {}
            path = it.get('Path') or ''
            if not path or not os.path.exists(path):
                continue
            out = '%s.Grifflix.en.srt' % os.path.splitext(path)[0]
            with SP_LOCK:   # (only if nothing else has taken it up meanwhile)
                e0 = (load(SUBPREP, {}) or {}).get(iid)
                if not isinstance(e0, dict) or e0.get('state') != 'nomatch':
                    continue
                with open(out + '.tmp', 'w', encoding='utf-8') as f:
                    f.write(SUBS.write_srt(cues))
                os.replace(out + '.tmp', out)
                e1 = __import__('copy').deepcopy(e0)
                say_ = '%s, by when its lines are said (%d of %d heard lines begin with one of its lines)' % (c.get('how') or 'as timed', h, nh)
                e1.update(state='done' if sure else 'check', note=say_[:200], chose=c.get('name') or '', file=out, at=int(time.time()), n=h, sure=bool(sure),
                          cur=str(c.get('i')), samples=c.get('samples'), timed=True)
                e1.pop('how', None)
                chk(e1, 'whisper', 'Done on the Mac mini, looked at again by when its lines are said, not their words: %s fits the whole film%s' % (c.get('name') or 'one', '' if sure else ' (to check)'))
                subprep_commit({iid: e0}, {iid: e1})
            g.jf.call('POST', '/Items/%s/Refresh' % iid, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
            n_ += 1
            log('Subtitles for %s: by when its lines are said, %s fits the whole film (%s)' % (e.get('name'), c.get('name'), 'done' if sure else 'to check'))
            record('subtitles saved', film=e.get('name'), id=iid, file=out, how='turned down by its words, fitting by when its lines are said', chose=c.get('name'), lines=h, sure=bool(sure))
        open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
        log('Subtitles: those turned down looked at again by when their lines are said: %d now fit' % n_)
    except Exception as ex:
        log('Subtitles, looked at again by when their lines are said: %s' % str(ex)[:200])


def keep_speech_first(g):
    """Each film waiting for English subtitles tried by when people speak, before any goes to Whisper on John's Mac (off while SPEECH['ok']
    is False: see there)."""
    if not SPEECH['ok']:
        return
    time.sleep(150)
    while True:
        did = False
        try:
            if not (g.st.get('opensubs') or {}).get('key'):
                SPEECH['ok'] = False
                time.sleep(3600)
                continue
            cl = SUBS.OpenSubs(g.st.setdefault('opensubs', {}), lambda: save(SETTINGS, g.st), tls())
            if cl.out_of_downloads():
                time.sleep(900)
                continue
            P = load(SUBPREP, {}) or {}
            todo = sorted(((k, v) for k, v in P.items() if not k.startswith('_') and isinstance(v, dict) and v.get('state') == 'waiting' and not v.get('speech_tried')
                           and not v.get('givenfile') and not v.get('ref_only') and v.get('ref_tried2')), key=lambda kv: kv[1].get('order', 0))
            for iid, e in todo[:1]:
                did = True
                it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path,ProviderIds,OriginalTitle'}) or {}
                path = it.get('Path') or ''
                e = dict(e)
                ok = None
                if path and os.path.exists(path):
                    try:
                        ok = speech_try(g, cl, e, iid, path, it)
                    except Exception as ex:
                        ok = False
                        e['speech_note'] = 'couldn’t: %s' % str(ex)[:160]
                        log('Subtitles by when people speak, %s: %s' % (e.get('name'), str(ex)[:160]))
                if ok is None:   # (downloads used up: again after the reset)
                    break
                e['speech_tried'] = int(time.time())
                with SP_LOCK:
                    Q = load(SUBPREP, {}) or {}
                    if isinstance(Q.get(iid), dict) and Q[iid].get('state') == 'waiting':   # (unless something else has taken it meanwhile)
                        Q[iid] = e
                        save(SUBPREP, Q)
                if not ok:
                    log('Subtitles for %s: none lined up by when people speak; on to Whisper (%s)' % (e.get('name'), str(e.get('speech_note') or '')[:200]))
        except Exception as ex:
            log('Subtitles by when people speak: %s' % str(ex)[:200])
        SPEECH['now'] = None
        time.sleep(20 if did else 300)


def name_sub(g, cl, e, iid, path, it, res=None):
    """English subtitles uploaded under the very name this copy was downloaded as (or of the same release: its source and group the same),
    and of the film's length: made for this release, so used as they are, with no listening and no timing (John, 22 Nov 2026, "The vast
    majority of the time you should be able to find the film subtitle without it"). True if saved."""
    pid = it.get('ProviderIds') or {}
    org = film_origin(path) or os.path.splitext(str(((load(os.path.expanduser('~/Grifflix/fingerprints.json'), {}) or {}).get(path) or {}).get('name') or ''))[0] or None
    hint = org or os.path.splitext(os.path.basename(path))[0]
    if res is None:
        if e.get('kind') == 'episode':
            res = cl.search('en', query=e.get('series'), parent_tmdb=e.get('series_tmdb'), parent_imdb=e.get('series_imdb'), alt=[e.get('series_orig')], season=e.get('season'), episode=e.get('episode'), hint=hint, also=True)
        else:
            res = cl.search('en', tmdb=pid.get('Tmdb'), imdb=pid.get('Imdb'), alt=[it.get('OriginalTitle')], query=it.get('Name'), year=it.get('ProductionYear'), hint=hint, also=True)
    dur = (it.get('RunTimeTicks') or 0) / 1e7
    same_ = [y for y in res if not y.get('machine') and not y.get('exact') and (y.get('samename') or (y.get('like') or 0) >= 8)]
    rel_ = bool(org) or bool(re.search(r'(?i)\b(1080p|720p|2160p|480p|bluray|brrip|bdrip|remux|web-?dl|webrip|dvdrip|x26[45]|h\.?26[45]|hevc|xvid|yts|rarbg)\b', hint))
    if not rel_:
        chk(e, 'name', 'Its download name isn’t known (converted before names were kept), so none could be matched by it: %d English on OpenSubtitles' % len(res))
    elif not same_:
        chk(e, 'name', 'None of the %d English subtitles on OpenSubtitles%s is named for its release (“%s”)' % (len(res), ' and Subdl' if SUBS.SUBDL.get('key') else '', hint[:80]))
    tried_ = []
    for x in same_[:3]:
        try:
            cues = SUBS.parse_srt(SUBS.decode(cl.download(x['file_id'])))
        except Exception:
            if cl.out_of_downloads():
                raise
            continue
        if len(cues) < 50:
            continue
        if dur and (cues[-1][1] > dur + 120 or cues[-1][1] < dur * .8):   # (its last line past the film's end, or long before it: another cut)
            tried_.append('“%s” runs %d min against the film’s %d: another cut' % ((x.get('name') or '')[:60], cues[-1][1] / 60, dur / 60))
            continue
        fn = '%s.Grifflix.en.srt' % os.path.splitext(path)[0]
        with open(fn + '.tmp', 'w', encoding='utf-8') as f:
            f.write(SUBS.write_srt(cues))
        os.replace(fn + '.tmp', fn)
        try:
            g.jf.call('POST', '/Items/%s/Refresh' % iid, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
        except Exception:
            pass
        chk(e, 'name', 'Found: “%s”, named for its release, used as it is' % (x.get('name') or '')[:80])
        e.update(state='done', how='by name', note='named for this very release (%s): used as it is' % ('the same name' if x.get('samename') else 'the same source and group'),
                 chose=x.get('name') or '', file=fn, at=int(time.time()), n=len(cues), sure=True)
        log('Subtitles for %s: English named for this very release, used as they are: %s' % (e.get('name'), x.get('name')))
        record('subtitles saved', film=e.get('name'), id=iid, file=fn, how='named for this very release', chose=x.get('name'), opensubtitles_file=x.get('file_id'), source=x.get('source') or 'OpenSubtitles')
        return True
    if same_:
        chk(e, 'name', '%d named for its release, none usable: %s' % (len(same_), '; '.join(tried_) or 'they couldn’t be read'))
    return False


def ref_sub(g, cl, e, iid, path, it):
    """No English made for this very file, but one in another language is: its lines' times used to time the English ones found for
    the film (John, 22 Nov 2026, "If there are subtitles in any language from that file but no English they will be useful for timing
    the English subtitles"). True if saved."""
    # (first, one this very file has in it already, or beside it, in another language: timed for this copy by definition (Io Capitano's
    # Italian): John, 22 Nov 2026, "if it has an inbuilt sub in the wrong language, then that should be used for the argos work")
    own_ref = None
    if (e.get('ref_given') or e.get('ref_byspeech')) and e.get('ref_file') and os.path.exists(e['ref_file']):   # (one John uploaded, or one found to start its lines when people speak in this copy: that, before any other)
        rc_ = SUBS.parse_srt(open(e['ref_file'], encoding='utf-8', errors='replace').read())
        if len(rc_) >= 50:
            own_ref = (e.get('ref_lang') or '?', rc_)
    sure_ = False   # (its timing surely this copy's: in the file itself, made for it by its fingerprint, or uploaded under its very name)
    if not own_ref:
        own_ref = own_foreign_sub(g, iid, path)
        sure_ = bool(own_ref)
    refs = [] if own_ref else [(fid, lang) for mh in hashes_for(path) for fid, lang in cl.exact_any(mh) if str(lang).lower() not in ('en', 'eng')]
    sure_ = sure_ or bool(refs)
    pid = it.get('ProviderIds') or {}
    org = film_origin(path) or os.path.splitext(str(((load(os.path.expanduser('~/Grifflix/fingerprints.json'), {}) or {}).get(path) or {}).get('name') or ''))[0] or None
    hint = org or os.path.splitext(os.path.basename(path))[0]
    relname = bool(org) or bool(re.search(r'(?i)\b(1080p|720p|2160p|480p|bluray|brrip|bdrip|remux|web-?dl|webrip|dvdrip|x26[45]|h\.?26[45]|hevc|xvid|yts|rarbg)\b', hint))
    if not refs and not own_ref and relname:   # (the file's own name when it's still a download's, not only one on record)
        # (or one in any language uploaded under the very name this copy was downloaded as: made for it too, whatever its language: John,
        # 22 Nov 2026, "Efforts should be made to find matching foreign subs and timing before whisper is used at all")
        try:
            if e.get('kind') == 'episode':
                A_ = cl.search('', query=e.get('series'), parent_tmdb=e.get('series_tmdb'), parent_imdb=e.get('series_imdb'), alt=[e.get('series_orig')], season=e.get('season'), episode=e.get('episode'), hint=hint, also=True)
            else:
                A_ = cl.search('', tmdb=pid.get('Tmdb'), imdb=pid.get('Imdb'), alt=[it.get('OriginalTitle')], query=it.get('Name'), year=it.get('ProductionYear'), hint=hint, also=True)
            # (uploaded under this copy's very name; or else of the same release, its source and group the same, as like as can be: each one
            # still lined up with the English below, so one that isn't this copy's timing is passed over)
            fo_ = [x for x in A_ if not x.get('machine') and str(x.get('lang') or '').lower() not in ('en', 'eng')]
            fo_.sort(key=lambda x: (not x.get('samename'), -(x.get('like') or 0)))
            refs = [(x['file_id'], x.get('lang') or '?') for x in fo_ if x.get('samename') or (x.get('like') or 0) >= 7][:3]
            sure_ = bool(fo_ and fo_[0].get('samename'))
        except Exception:
            refs = []
    src_ = ('the %s subtitle you gave' if e.get('ref_given') else 'the %s subtitle in the file itself') if own_ref else 'a %s subtitle made for this copy'
    if own_ref:
        refs, ref = [('own', own_ref[0])], own_ref[1]
    else:
        if not refs:
            chk(e, 'ref', 'No subtitle in another language made for this copy: none in the file itself, none on OpenSubtitles by its fingerprint' + (', nor named for its release' if relname else ' (and its download name isn’t known)'))
            return False
        ref = []
        for fid, lang in refs[:2]:
            ref = SUBS.parse_srt(SUBS.decode(cl.download(fid)))
            if len(ref) >= 50:
                break
    if len(ref) < 50:
        chk(e, 'ref', '%s was found, but too short to time by (%d lines)' % ((src_ % refs[0][1]).capitalize(), len(ref)))
        return False
    try:   # (kept, to see how the English lined up with it, and for John's Mac to translate and compare if they don't line up by their timing)
        save_txt = os.path.join(os.path.expanduser('~/Grifflix/subprep-ref'), iid + '.%s.srt' % refs[0][1])
        os.makedirs(os.path.dirname(save_txt), exist_ok=True)
        with open(save_txt, 'w', encoding='utf-8') as f_:
            f_.write(SUBS.write_srt(ref))
        e['ref_file'], e['ref_lang'], e['ref_sure'] = save_txt, str(refs[0][1]), bool(sure_)
    except Exception:
        pass
    if e.get('kind') == 'episode':
        L = cl.search('en', query=e.get('series'), parent_tmdb=e.get('series_tmdb'), parent_imdb=e.get('series_imdb'), alt=[e.get('series_orig')], season=e.get('season'), episode=e.get('episode'), hint=hint, also=True)
    else:
        L = cl.search('en', tmdb=pid.get('Tmdb'), imdb=pid.get('Imdb'), alt=[it.get('OriginalTitle')], query=it.get('Name'), year=it.get('ProductionYear'), hint=hint, also=True)
    L = [x for x in L if not x.get('machine')][:6]
    best = None
    # (each English one tried kept, as it came and as timed, to compare and to choose instead: John, 22 Nov 2026, "showing a clear comparison
    # or the ability to choose and see - this should be on anything adjusted, not just rejected")
    cdir_ = os.path.expanduser('~/Grifflix/subprep-cands')
    rc_ = []
    for k_, x in enumerate(L):
        try:
            cues = SUBS.parse_srt(SUBS.decode(cl.download(x['file_id'])))
        except Exception:
            if cl.out_of_downloads():
                raise
            continue
        share, hit, out, how = SUBS.align_to_ref(ref, cues)
        try:
            os.makedirs(cdir_, exist_ok=True)
            for sfx_, cc_ in (('r%d.srt' % k_, out), ('r%d.orig.srt' % k_, cues)):
                with open(os.path.join(cdir_, '%s.%s' % (iid, sfx_)), 'w', encoding='utf-8') as f_:
                    f_.write(SUBS.write_srt(cc_))
            rc_.append({'i': 'r%d' % k_, 'name': x.get('name') or '', 'share': round(share, 3), 'n': hit, 'how': how})
        except Exception:
            pass
        if not best or share > best[0]:
            best = (share, hit, out, how, x, 'r%d' % k_)
        if share >= .8:
            break
    e['rcands'] = sorted(rc_, key=lambda c: -c['share'])
    if not best or best[0] < .45 or best[1] < 60:
        e['ref_note'] = 'a subtitle made for this file in %s, but no English one lined up with it (best %d%%)' % (refs[0][1], round(100 * best[0]) if best else 0)
        chk(e, 'ref', 'Found %s, but none of the %d English lined up with it (best %d%% of lines, at least 45%% needed): to be translated and compared on your Mac' % (src_ % refs[0][1], len(L), round(100 * best[0]) if best else 0))
        return False
    chk(e, 'ref', 'Found %s: the English “%s” lined up with it, %d%% of its lines' % (src_ % refs[0][1], (best[4].get('name') or '')[:60], round(100 * best[0])))
    share, hit, out, how, x, ci_ = best
    e['cur'] = ci_   # (which of those is the one in use)
    fn = '%s.Grifflix.en.srt' % os.path.splitext(path)[0]
    with open(fn + '.tmp', 'w', encoding='utf-8') as f:
        f.write(SUBS.write_srt(out))
    os.replace(fn + '.tmp', fn)
    try:
        g.jf.call('POST', '/Items/%s/Refresh' % iid, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
    except Exception:
        pass
    e.update(state='done' if share >= .6 else 'check', note='%s (the %s subtitle’s)' % (how, refs[0][1]),
             chose=x.get('name') or '', file=fn, at=int(time.time()), sure=share >= .6, n=hit, how='by reference', ref_v=3)
    log('Subtitles timed by a %s subtitle made for the very file: %s (%d%% of lines on its lines)' % (refs[0][1], e.get('name'), round(100 * share)))
    record('subtitles saved', film=e.get('name'), id=iid, file=fn, how=e['note'], english_from=x.get('name'), opensubtitles_file=x.get('file_id'),
           timed_by_language=refs[0][1], lines_on_reference=hit, share=round(share, 3))
    return True


SILENT_AT = os.path.expanduser('~/Grifflix/silent-films.json')   # (TMDB's word on each film, kept: tmdb number -> true or false)


def silent_film(g, iid, it=None):
    """Whether a film is a silent one: Criterion calls it Silent, its genres or tags in the media server say so, TMDB tags it a silent film,
    or it's from 1927 or before. A silent film has no speech to time subtitles by: only one made for its very file, or named for its very
    release, can be taken; else none is needed. John, 23 Nov 2026, "we should also have a way of knowing that a silent film won't have
    subtitles"."""
    try:
        if 'Silent' in ((load(os.path.expanduser('~/Grifflix/criterion-genres-given.json'), {}) or {}).get(iid) or []):
            return True
        full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Genres,Tags,ProviderIds,ProductionYear'}) or {}
        if any(re.search(r'\bsilent\b', str(x), re.I) for x in (full.get('Genres') or []) + (full.get('Tags') or [])):
            return True
        if 1888 < int(full.get('ProductionYear') or (it or {}).get('ProductionYear') or 0) <= 1927:
            return True
        tid = str((full.get('ProviderIds') or {}).get('Tmdb') or '')
        if tid and full.get('Type') == 'Movie':
            S = load(SILENT_AT, {}) or {}
            if tid not in S:
                kw = tmdb(g, '/movie/%s/keywords' % tid)
                if kw is None:
                    return False
                S[tid] = any(re.search(r'\bsilent (film|movie)\b', str(k.get('name') or ''), re.I) for k in kw.get('keywords') or [])
                save(SILENT_AT, S)
            return bool(S[tid])
    except Exception:
        pass
    return False


SILENT_NOTE = 'a silent film: no speech to time subtitles by, and none made for this very file or named for its release'


def ep_fill(g, e, iid, cache=None):
    """An episode's series' IMDb number and own title (Scener ur ett äktenskap), kept on its record, for the subtitle searches: by its
    TMDB number alone OpenSubtitles had none for Bergman's Scenes from a Marriage (John, 23 Nov 2026). True if it changed."""
    if e.get('kind') != 'episode' or ('series_imdb' in e and 'series_orig' in e):
        return False
    try:
        x = ((g.jf.call('GET', '/Items', query={'Ids': iid, 'Fields': 'SeriesId'}) or {}).get('Items') or [{}])[0]
        sid = x.get('SeriesId') or ''
        if cache is not None and sid in cache:
            sr = cache[sid]
        else:
            sr = ((g.jf.call('GET', '/Items', query={'Ids': sid, 'Fields': 'ProviderIds,OriginalTitle'}) or {}).get('Items') or [{}])[0] if sid else {}
            if cache is not None:
                cache[sid] = sr
        e['series_imdb'] = (sr.get('ProviderIds') or {}).get('Imdb') or ''
        ot = sr.get('OriginalTitle') or ''
        e['series_orig'] = ot if ot and ot != sr.get('Name') else ''
        return True
    except Exception:
        return False


def ep_fill_all(g):
    """Each episode's record given its series' IMDb number and own title; and, once, the episodes turned down or with none found looked
    at again from the start, since their searches went by the series' TMDB number alone (and another show's episode was taken for one)."""
    time.sleep(60)
    try:
        cache = {}
        P = load(SUBPREP, {}) or {}
        for iid, e in list(P.items()):
            if isinstance(e, dict) and e.get('kind') == 'episode' and not ('series_imdb' in e and 'series_orig' in e):
                e1 = __import__('copy').deepcopy(e)
                if ep_fill(g, e1, iid, cache):
                    subprep_commit({iid: e}, {iid: e1})
        mk = os.path.expanduser('~/Grifflix/subs-episodes.done')
        if not os.path.exists(mk):
            n = 0
            with SP_LOCK:
                P = load(SUBPREP, {}) or {}
                for iid, e in P.items():
                    if isinstance(e, dict) and e.get('kind') == 'episode' and e.get('state') in ('nomatch', 'none'):
                        e.update(state='waiting', note='to be tried again: episodes looked for by their series\u2019 IMDb number and own title too')
                        for k in ('exact_tried', 'name_tried', 'ref_tried', 'ref_tried2', 'checks', 'tried', 'cands'):
                            e.pop(k, None)
                        n += 1
                save(SUBPREP, P)
            open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
            log('Subtitles: %d episodes looked at again from the start, by their series\u2019 IMDb numbers and own titles too' % n)
    except Exception as ex:
        log('Subtitles, the episodes\u2019 series: %s' % str(ex)[:200])


def subtitlecat_for(g, e, iid, it, path, want=None):
    """SubtitleCat's original uploads for a film or episode, the last place looked: [{name, lang, srt}] (want 'en': English only). Its
    own machine translations never taken: John, 23 Nov 2026, "I'd rather we took the original upload from subtitlecat and checked and
    translated it ourself if necessary"."""
    try:
        sc = SUBS.SubtitleCat(tls())
        if e.get('kind') == 'episode':
            R = sc.search([e.get('series'), e.get('series_orig'), os.path.basename(os.path.dirname(os.path.dirname(path)))], season=e.get('season'), episode=e.get('episode'),
                          year=e.get('year') or (re.findall(r'\((\d{4})\)', os.path.basename(os.path.dirname(os.path.dirname(path)))) or [None])[0])
        else:
            ot = (g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'OriginalTitle'}) or {}).get('OriginalTitle')
            R = sc.search([it.get('Name'), ot, os.path.basename(os.path.dirname(path))], year=it.get('ProductionYear'))
        out, seen = [], set()
        for x in R[:6]:
            o = sc.original(x['page'])
            if not o or len(SUBS.parse_srt(o[0])) < 50 or (want and o[1] != want):
                continue
            k = (o[1], len(o[0]) // 500)   # (the same upload twice under two names: once)
            if k in seen:
                continue
            seen.add(k)
            out.append({'name': 'SubtitleCat: %s' % x['name'], 'lang': o[1] or '', 'srt': o[0]})
        log('SubtitleCat, %s: %d original upload%s%s' % (e.get('name'), len(out), '' if len(out) == 1 else 's', ' (%s)' % ', '.join(sorted(set(x['lang'] or '?' for x in out))) if out else ''))
        return out
    except Exception as ex:
        log('SubtitleCat, %s: %s' % (e.get('name'), str(ex)[:160]))
        return []


def english_by_foreign(g, iid):
    """A film whose subtitles were machine-translated from one in another language that fits this copy: English ones (OpenSubtitles,
    Subdl) lined up by that one's timing, line by line (each English line set on the line it lands on: no words needed), and one that
    lines up taken instead of the machine's translation (John, 23 Nov 2026, "before machine translation, has it not been possible to
    find a subtitle that now matches the line-up based on the korean sub?"). True if one was."""
    P = load(SUBPREP, {}) or {}
    e = P.get(iid)
    if not isinstance(e, dict) or e.get('how') != 'machine' or not e.get('file') or not os.path.exists(e['file']):
        return False
    ref = SUBS.parse_srt(open(e['file'], encoding='utf-8', errors='replace').read())   # (the translation keeps the foreign one's own times)
    lang = e.get('ref_lang') or '?'
    if len(ref) < 50:
        return False
    it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path,ProviderIds,OriginalTitle'}) or {}
    path = it.get('Path') or ''
    if not path or not os.path.exists(path):
        return False
    pid = it.get('ProviderIds') or {}
    SUBS.SUBDL['key'] = (g.st.get('subdl') or {}).get('key') or ''
    cl = SUBS.OpenSubs(g.st.setdefault('opensubs', {}), lambda: save(SETTINGS, g.st), tls())
    hint = film_origin(path) or os.path.basename(path)
    if e.get('kind') == 'episode':
        L = cl.search('en', query=e.get('series'), parent_tmdb=e.get('series_tmdb'), parent_imdb=e.get('series_imdb'), alt=[e.get('series_orig')], season=e.get('season'), episode=e.get('episode'), hint=hint, also=True)
    else:
        L = cl.search('en', tmdb=pid.get('Tmdb'), imdb=pid.get('Imdb'), alt=[it.get('OriginalTitle')], query=it.get('Name'), year=it.get('ProductionYear'), hint=hint, also=True)
    L = [x for x in L if not x.get('machine')][:6]
    best = None
    for x in L:
        try:
            cues = SUBS.parse_srt(SUBS.decode(cl.download(x['file_id'])))
        except Exception:
            if cl.out_of_downloads():
                break
            continue
        share, hit, out, how = SUBS.align_to_ref(ref, cues)
        if not best or share > best[0]:
            best = (share, hit, out, how, x)
        if share >= .8:
            break
    if not best or best[0] < .45 or best[1] < 60:   # (then SubtitleCat's English uploads, the last place looked)
        for x in subtitlecat_for(g, e, iid, it, path, want='en'):
            share, hit, out, how = SUBS.align_to_ref(ref, SUBS.parse_srt(x['srt']))
            L.append(x)
            if not best or share > best[0]:
                best = (share, hit, out, how, x)
    if not best or best[0] < .45 or best[1] < 60:
        log('Subtitles for %s: none of %d English lined up with the %s subtitle that fits%s: its machine translation kept' % (e.get('name'), len(L), lang, ' (best %d%% of lines)' % round(100 * best[0]) if best else ''))
        return False
    share, hit, out, how, x = best
    with SP_LOCK:   # (only if it's still the machine's translation)
        e0 = (load(SUBPREP, {}) or {}).get(iid)
        if not isinstance(e0, dict) or e0.get('how') != 'machine':
            return False
        fn = e0['file']
        with open(fn + '.tmp', 'w', encoding='utf-8') as f:
            f.write(SUBS.write_srt(out))
        os.replace(fn + '.tmp', fn)
        e1 = __import__('copy').deepcopy(e0)
        e1.update(state='done' if share >= .6 else 'check', note='%s (the %s subtitle’s)' % (how, lang), chose=x.get('name') or '', at=int(time.time()), sure=share >= .6,
                  n=hit, how='by reference', ref_v=3, machine_before=True)
        chk(e1, 'ref', 'Found: the English “%s” lined up with the %s subtitle that fits this copy, %d%% of its lines (instead of a machine translation)' % ((x.get('name') or '')[:60], lang, round(100 * share)))
        subprep_commit({iid: e0}, {iid: e1})
    try:
        g.jf.call('POST', '/Items/%s/Refresh' % iid, query={'MetadataRefreshMode': 'Default', 'ImageRefreshMode': 'None'})
    except Exception:
        pass
    log('Subtitles for %s: English “%s” lined up with the %s subtitle that fits (%d%% of lines on its lines), instead of its machine translation' % (e.get('name'), x.get('name'), lang, round(100 * share)))
    record('subtitles saved', film=e.get('name'), id=iid, file=fn, how='English lined up with the %s subtitle that fits this copy, instead of its machine translation' % lang,
           english_from=x.get('name'), source=x.get('source') or 'OpenSubtitles', share=round(share, 3), lines_on_reference=hit)
    return True


def english_by_foreign_all(g, wait=0):
    """Each film machine-translated so far looked at again for English to line up by the subtitle that fits (once Subdl is there too)."""
    time.sleep(wait)
    try:
        for iid, e in list((load(SUBPREP, {}) or {}).items()):
            if isinstance(e, dict) and e.get('how') == 'machine':
                english_by_foreign(g, iid)
    except Exception as ex:
        log('Subtitles, English lined up by the subtitle that fits: %s' % str(ex)[:200])


def subs_exact_all():
    """Each film and episode waiting for English subtitles given the one made for its very file, where OpenSubtitles has one."""
    g = G_REF[0] if G_REF else None
    if not g or not (g.st.get('opensubs') or {}).get('key'):
        return
    cl = SUBS.OpenSubs(g.st.setdefault('opensubs', {}), lambda: save(SETTINGS, g.st), tls())
    P = load(SUBPREP, {}) or {}
    n = 0
    if cl.out_of_downloads():
        log('Subtitles: OpenSubtitles downloads used up for today; looked over once they reset')
        return
    for iid, e in list(P.items()):
        redo_ = isinstance(e, dict) and e.get('how') == 'by reference' and e.get('ref_v') != 3   # (those timed before lines between were moved by their neighbours, or before
        # the English ones tried were kept to compare and choose from: again)
        if iid.startswith('_') or not isinstance(e, dict) or e.get('givenfile') or e.get('how') == 'given' or ((e.get('state') not in ('waiting', 'none', 'nomatch', 'failed') or e.get('ref_tried2')) and not redo_):
            continue   # (nor one John gave subtitles for: his stand)
        try:
            it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path,ProviderIds,OriginalTitle'}) or {}
            path = it.get('Path') or ''
            if path and os.path.exists(path):
                Q = load(SUBPREP, {}) or {}   # (saved as it goes, over what John's Mac has done meanwhile)
                Q0_ = __import__('copy').deepcopy(Q)
                q = Q.get(iid)
                if isinstance(q, dict) and (q.get('state') in ('waiting', 'none', 'nomatch', 'failed') or redo_):
                    ok = (not redo_ and exact_sub(g, cl, q, iid, path)) or (not redo_ and not q.get('name_tried') and name_sub(g, cl, q, iid, path, it)) or ref_sub(g, cl, q, iid, path, it)
                    if not redo_:
                        q['name_tried'] = q.get('name_tried') or int(time.time())
                    if cl.out_of_downloads():   # (the day's downloads gone part-way: not counted as tried; the rest after the reset)
                        log('Subtitles: OpenSubtitles downloads used up for today; the rest of the look-over after they reset')
                        return
                    q['exact_tried'] = q['ref_tried'] = q['ref_tried2'] = int(time.time())
                    subprep_commit(Q0_, Q)
                    n += bool(ok)
        except Exception as ex:
            log('Subtitles made for the very file, %s: %s' % (e.get('name'), str(ex)[:160]))
        time.sleep(1.5)
    log('Subtitles made for the very file: %d films and episodes given theirs' % n)
    # (and those timed by Whisper before a subtitle in another language made for the copy was looked for, looked at again: timed by its lines
    # here, or else, if there is one, translated on John's Mac and compared, their Whisper subtitles kept meanwhile and kept if that doesn't
    # line up: John, 22 Nov 2026, "there are a lot of 'what whisper hears' in the admin")
    n2 = q2 = 0
    for iid, e in list((load(SUBPREP, {}) or {}).items()):
        if iid.startswith('_') or not isinstance(e, dict) or e.get('state') not in ('done', 'check') or e.get('how') or e.get('rechecked2'):
            continue
        try:
            it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path,ProviderIds,OriginalTitle'}) or {}
            path = it.get('Path') or ''
            Q = load(SUBPREP, {}) or {}
            Q0_ = __import__('copy').deepcopy(Q)
            q = Q.get(iid)
            if not (path and os.path.exists(path) and isinstance(q, dict) and q.get('state') in ('done', 'check') and not q.get('how')):
                continue
            prev = {k: q.get(k) for k in ('state', 'note', 'chose', 'file', 'n', 'miss', 'sure', 'samples', 'tried', 'mins', 'at')}
            if cl.out_of_downloads():
                break
            q['rechecked2'] = int(time.time())
            if exact_sub(g, cl, q, iid, path) or ref_sub(g, cl, q, iid, path, it):
                n2 += 1
            elif cl.out_of_downloads():   # (not looked at after all: again after the reset)
                q.pop('rechecked2', None)
                subprep_commit(Q0_, Q)
                break
            elif q.get('ref_file') and os.path.exists(q['ref_file']):
                q.update(state='waiting', ref_only=True, prev=prev, order=-1)
                q2 += 1
            subprep_commit(Q0_, Q)
        except Exception as ex:
            log('Subtitles looked at again, %s: %s' % (e.get('name'), str(ex)[:160]))
        time.sleep(1.5)
    if n2 or q2:
        log('Subtitles timed by Whisper, looked at again: %d timed by a subtitle made for the copy; %d for your Mac to translate and compare' % (n2, q2))


def subs_namecheck():
    """Once (until ~/Grifflix/subs-namecheck.json is there): for each film and episode waiting for English subtitles, OpenSubtitles
    asked (searches only, none of John's downloads used) which of its English subtitles carry the very name the copy was downloaded as,
    and which were made for this very file: John, 21 Nov 2026, "how many subs can we find that use the same file name as the original?")"""
    out_f = os.path.expanduser('~/Grifflix/subs-namecheck.json')
    g = G_REF[0] if G_REF else None
    if not g or os.path.exists(out_f) or not (g.st.get('opensubs') or {}).get('key'):
        return
    P = load(SUBPREP, {}) or {}
    cl = SUBS.OpenSubs(g.st.setdefault('opensubs', {}), lambda: save(SETTINGS, g.st), tls())
    rel_ = re.compile(r'(?i)\b(1080p|720p|2160p|480p|576p|bluray|blu-ray|brrip|bdrip|remux|web-?dl|webrip|dvdrip|hdtv|x26[45]|h\.?26[45]|hevc|xvid|divx|yts|rarbg)\b')
    res, done_ = {}, 0
    for iid, e in list(P.items()):
        if iid.startswith('_') or not isinstance(e, dict) or e.get('state') not in ('waiting', 'none', 'nomatch', 'failed'):
            continue
        try:
            it = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid), query={'Fields': 'Path,ProviderIds,OriginalTitle'}) or {}
            path = it.get('Path') or ''
            org = film_origin(path)
            name = org or os.path.splitext(os.path.basename(path))[0]
            r_ = {'name': e.get('name'), 'year': e.get('year'), 'release': name, 'from': 'record' if org else ('file name' if rel_.search(name) else ''), 'found': 0, 'same': [], 'exact': 0}
            if r_['from']:
                pid = it.get('ProviderIds') or {}
                try:
                    mh = SUBS.movie_hash(path)
                except OSError:
                    mh = None
                if e.get('kind') == 'episode':
                    L = cl.search('en', mhash=mh, query=e.get('series'), parent_tmdb=e.get('series_tmdb'), parent_imdb=e.get('series_imdb'), alt=[e.get('series_orig')], season=e.get('season'), episode=e.get('episode'), hint=name)
                else:
                    L = cl.search('en', mhash=mh, tmdb=pid.get('Tmdb'), imdb=pid.get('Imdb'), alt=[it.get('OriginalTitle')], query=it.get('Name'), year=it.get('ProductionYear'), hint=name)
                L = [x for x in L if not x.get('machine')]
                r_.update(found=len(L), same=[x.get('name') or x.get('fname') for x in L if x.get('samename')][:5], exact=sum(1 for x in L if x.get('exact')))
            res[iid] = r_
        except Exception as ex:
            res[iid] = {'name': e.get('name'), 'error': str(ex)[:160]}
        done_ += 1
        if done_ % 20 == 0:
            save(out_f + '.part', res)
        time.sleep(1.5)
    save(out_f, {'at': int(time.time()), 'films': res})
    try:
        os.remove(out_f + '.part')
    except OSError:
        pass
    log('Subtitles named as the download: %d of %d looked up' % (sum(1 for v in res.values() if v.get('same')), len(res)))


SUBSCAN = threading.Lock()
RE_EN = {}


def reads_english(path):
    """Whether a subtitle file reads as English: its commonest words an eighth of its words or more (kept by the file's time)."""
    try:
        m = os.path.getmtime(path)
        if RE_EN.get(path, (None,))[0] == m:
            return RE_EN[path][1]
        with open(path, 'rb') as fh:
            t = fh.read(40000).decode('utf-8', 'replace').lower()
    except (OSError, TypeError):
        return False
    w = re.findall(r"[a-z']+", re.sub(r'\d+:\d+:\d+[,.]\d+', ' ', t))
    common = set('the and you that is it to of what not this have was are for with my your me he she we they'.split())
    ok = len(w) >= 200 and sum(1 for z in w if z in common) / len(w) >= .12
    RE_EN[path] = (m, ok)
    return ok


def subprep_scan():
    """Run at once only (the gateway starting, new films, John's Look now, the three-hourly round)."""
    if not SUBSCAN.acquire(blocking=False):
        return
    try:
        subprep_scan_()
    finally:
        SUBSCAN.release()


def subprep_scan_():
    """The foreign-language films with no English subtitles that read as text (in the file, or beside it), added to the list for John's
    Mac to make ready (subprep.py); those with them now, or gone, taken off (John, 20 Nov 2026: foreign-language films, English)."""
    g = G_REF[0] if G_REF else None
    if not g:
        return
    libs = g.st.get('libraries', {})
    if not libs.get('movies'):
        return
    P = load(SUBPREP, {}) or {}
    import copy
    P0_ = copy.deepcopy(P)   # (as it was: only what this changes written back)
    if P.get('_v') not in (5,):   # (once: those found to have "no English subtitles" while subtitles of the right film were thrown away for being under
        # its own title, tried again: 21 Nov 2026; v3: those turned down by the too-strict
        # whole-film check, tried again)
        for k_, v_ in P.items():
            if not k_.startswith('_') and isinstance(v_, dict) and v_.get('state') in ('none', 'nomatch', 'failed'):
                v_.update(state='waiting', note='')
        P['_v'] = 5   # (v5: those turned down by a part with few lines heard, The 400 Blows' opening, tried again: 22 Nov 2026)
    films, start = [], 0
    while True:
        r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': libs['movies'], 'Recursive': 'true', 'IncludeItemTypes': 'Movie',
                                                              'Fields': 'MediaStreams,ProductionYear,ProviderIds,Path', 'StartIndex': start, 'Limit': 200}) or {}
        films += r.get('Items') or []
        start += 200
        if start >= (r.get('TotalRecordCount') or 0):
            break
    # (TV's episodes too, each by its series' own language: John, 21 Nov 2026, "is tv included in this check?" ... "yes")
    stmdb = {}
    if libs.get('tv'):
        try:
            for x in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': libs['tv'], 'Recursive': 'true', 'IncludeItemTypes': 'Series',
                                                                         'Fields': 'ProviderIds'}) or {}).get('Items') or []:
                stmdb[x['Id']] = str((x.get('ProviderIds') or {}).get('Tmdb') or '')
            start = 0
            while True:
                r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': libs['tv'], 'Recursive': 'true', 'IncludeItemTypes': 'Episode',
                                                                      'Fields': 'MediaStreams,ProductionYear,ProviderIds,Path', 'StartIndex': start, 'Limit': 200}) or {}
                films += r.get('Items') or []
                start += 200
                if start >= (r.get('TotalRecordCount') or 0):
                    break
        except Exception as e:
            log('Subtitles to make ready (TV):', str(e)[:200])
    eng = ('eng', 'en', 'english')
    # (a film whose sound isn't marked with its language, nearly half of them, by the language TMDB gives as its own: they were all left
    # off, as if English (John, 21 Nov 2026, "I feel like I must have more films than that with no subs"). Asked once each, kept)
    lf = os.path.expanduser('~/Grifflix/tmdb-langs.json')
    TL = load(lf, {}) or {}
    asked, seen, added, tally, efix = 0, set(), 0, {}, {}
    for f in films:
        st = f.get('MediaStreams') or []
        au = [x for x in st if x.get('Type') == 'Audio']
        main = next((x for x in au if x.get('IsDefault')), au[0] if au else None)
        al = ((main or {}).get('Language') or '').lower()
        en_au = [x for x in au if (x.get('Language') or '').lower() in eng and 'comment' not in (x.get('Title') or '').lower()]
        ep_ = f.get('Type') == 'Episode'
        tid = ('tv:' + stmdb.get(f.get('SeriesId'), '') if stmdb.get(f.get('SeriesId')) else '') if ep_ else str((f.get('ProviderIds') or {}).get('Tmdb') or '')
        if al and al not in eng and al not in ('und', 'unk', 'zxx', 'mis') and en_au and tid:
            # (a film heard first in another language with an English soundtrack too: its own language asked of TMDB, an English film
            # heard dubbed put right by the converter, English first: John, 21 Nov 2026, "any english language films that don't have
            # english as first language need fixing": Beverly Hills Cop in Bulgarian, Gandhi in Italian)
            if tid not in TL and asked < 1500:
                try:
                    TL[tid] = ((tmdb(g, ('/tv/' + tid[3:]) if tid.startswith('tv:') else ('/movie/' + tid)) or {}).get('original_language') or '').lower()
                except Exception:
                    pass
                asked += 1
            if TL.get(tid) == 'en':
                efix[f['Id']] = {'name': ('%s S%s E%s' % (f.get('SeriesName'), f.get('ParentIndexNumber'), f.get('IndexNumber'))) if ep_ else f.get('Name'), 'year': f.get('ProductionYear') or '', 'path': f.get('Path') or '', 'was': al}
                continue
        if not al or al in ('und', 'unk', 'zxx', 'mis'):
            if tid and tid not in TL and asked < 1500:
                try:
                    TL[tid] = ((tmdb(g, ('/tv/' + tid[3:]) if tid.startswith('tv:') else ('/movie/' + tid)) or {}).get('original_language') or '').lower()
                except Exception:
                    pass   # (asked again next time)
                asked += 1
                if asked % 100 == 0:
                    save(lf, TL)
            al = (TL.get(tid) or '') if tid else ''
        has_t = any(x.get('Type') == 'Subtitle' and x.get('IsTextSubtitleStream') and (x.get('Language') or '').lower() in eng for x in st)
        has_p = any(x.get('Type') == 'Subtitle' and not x.get('IsForced') and 'forced' not in (x.get('Title') or '').lower()
                    and (x.get('Language') or '').lower() in eng for x in st)
        if not (has_t or has_p):   # (a subtitle file beside it with no language in its name, "Stolen Kisses (1968).srt": read, English if it reads so;
            # 26 of the 29 such were: John, 21 Nov 2026, "I need a list of foreign films with truly no subtitles"), or English picture
            # subtitles saved beside it when converted (Gomorrah's .eng.sup), which the media server doesn't list
            has_t = any(x.get('Type') == 'Subtitle' and x.get('IsExternal') and x.get('IsTextSubtitleStream') and not x.get('Language')
                        and reads_english(x.get('Path')) for x in st)
            d_ = os.path.dirname(f.get('Path') or '')
            try:
                has_p = bool(d_) and any(re.search(r'\.(eng|en)(\.\d+)?\.(sup|mks)$', n_, re.I) for n_ in os.listdir(d_))
            except OSError:
                pass
        # (English subtitles beside it from before any of this (Grifflix's first subtitles, or ones that came with a download): checked against
        # what's said, on John's Mac when it has nothing else to do, and put right or replaced if they don't fit: John, 22 Nov 2026, "it's
        # quite likely that there are films with srt subtitles that aren't right from previous attempts before any method. We need to check those")
        ext_ = [x for x in st if x.get('Type') == 'Subtitle' and x.get('IsExternal') and x.get('IsTextSubtitleStream') and x.get('Path')
                and ((x.get('Language') or '').lower() in eng or (not x.get('Language') and reads_english(x.get('Path'))))]
        if ext_ and f['Id'] not in P:
            fo_ = bool(al) and al not in eng and al not in ('und', 'unk', 'zxx', 'mis', 'xx')
            P[f['Id']] = {'name': ('%s S%s E%s' % (f.get('SeriesName'), f.get('ParentIndexNumber'), f.get('IndexNumber'))) if ep_ else f.get('Name'),
                          'year': f.get('ProductionYear') or '', 'lang': al if fo_ else 'en', 'state': 'verify', 'vfile': ext_[0]['Path'], 'translate': fo_,
                          'order': 1000000 + len(P), 'at': int(time.time())}
            record('subtitles to check', film=P[f['Id']]['name'], id=f['Id'], file=ext_[0]['Path'], why='English subtitles from before, not yet checked against what’s said')
        if al and al not in eng and al not in ('und', 'unk', 'zxx', 'mis', 'xx'):
            k_ = ('tv ' if ep_ else '') + ('text' if has_t else 'picture' if has_p else 'none')
            tally[k_] = tally.get(k_, 0) + 1
        if not al or al in eng or al in ('und', 'unk', 'zxx', 'mis', 'xx'):
            continue   # (English, or not known: not on the list)
        # (English picture subtitles of its own count too, not only the forced ones: their text is to be read from them, exactly timed, when
        # converted (convert.py pulls them out beside it, picture-subs.json), not made by Whisper: 101 of the 147 listed had them, Wild
        # Strawberries among them: John, 21 Nov 2026, "I'm not sure why Wild Strawberries was even in the list")
        has = has_t or has_p
        iid = f['Id']
        seen.add(iid)
        e = P.get(iid)
        if has:
            if e and e.get('state') in ('waiting', 'none', 'failed', 'nomatch'):
                P.pop(iid, None)
            continue
        if not e:
            P[iid] = {'name': f.get('Name'), 'year': f.get('ProductionYear') or '', 'lang': al, 'state': 'waiting', 'order': len(P), 'at': int(time.time())}
            record('subtitles to make', film=f.get('Name'), id=iid, lang=al, why='in a language not English, with no English subtitles')
            if ep_:   # (an episode: found on OpenSubtitles by its series and its number)
                P[iid].update(name='%s S%d E%d' % (f.get('SeriesName') or '', f.get('ParentIndexNumber') or 0, f.get('IndexNumber') or 0), kind='episode',
                              series_tmdb=stmdb.get(f.get('SeriesId'), ''), season=f.get('ParentIndexNumber'), episode=f.get('IndexNumber'), series=f.get('SeriesName') or '')
            added += 1
    for k in [k for k in P if not k.startswith('_') and k not in seen and P[k].get('state') in ('waiting', 'none', 'failed', 'nomatch')]:
        P.pop(k, None)
    subprep_commit(P0_, P)
    if asked:
        save(lf, TL)
    save(os.path.expanduser('~/Grifflix/foreign-films.json'), dict(tally, at=int(time.time()), films=len(films)))
    old_ = load(os.path.expanduser('~/Grifflix/english-first.json'), {}) or {}
    for k_, v_ in efix.items():
        if k_ not in old_:
            record('to be made English first', film=v_.get('name'), id=k_, path=v_.get('path'), first_soundtrack_was=v_.get('was'))
    save(os.path.expanduser('~/Grifflix/english-first.json'), efix)   # (for the converter: English made the first soundtrack)   # (how many in a language not English, and with which English subtitles)
    if added:
        log('Subtitles to make ready: %d more foreign-language films with no English subtitles' % added)


def finish_again():
    """A film made on John's Mac whose putting-in-place here was stopped by the rule kept for the Mac mini's own converting ("it's HDR:
    converted on John's Mac only"), its made file still waiting: put in place now (WALL·E, 20 Nov 2026, "it was converted on my mac")."""
    import subprocess
    home = os.path.expanduser('~/Grifflix')
    for t_, job in list(CONVJOBS.items()):
        try:
            tmp = (job.get('plan') or {}).get('tmp') or ''
            if job.get('state') != 'failed' or 'Mac only' not in str(job.get('error') or '') or not tmp or not os.path.exists(tmp):
                continue
            job['state'], job['error'] = 'checking', ''
            convjobs_save()
            r = subprocess.run([sys.executable, os.path.join(home, 'convert.py'), 'finish', job['id']], input=json.dumps(job['plan']['item']),
                               capture_output=True, text=True, timeout=3600, cwd=home)
            res = ([l for l in (r.stdout or '').splitlines() if l.startswith('{')] or ['{}'])[-1]
            try:
                rj = json.loads(res)
            except ValueError:
                rj = {}
            job['state'] = 'failed' if rj.get('error') else 'done'
            job['error'] = str(rj.get('error') or '')[:200]
            convjobs_save()
            log('Converted on John’s Mac, put in place now: %s: %s' % (job['plan']['item'].get('name'), res))
            CONV_NOW.set()
        except Exception as e:
            log('Converted on John’s Mac, putting in place: %s' % str(e)[:200])


def keep_covers():
    """Every Criterion film's cover put on without waiting for a download to set it off: 3 minutes after the gateway starts, then every
    hour (John, 20 Nov 2026: "we should have the posters for all the films ready to go"; The Beloved Child, fixed to its English title, waited
    for the next film to come in). Criterion's covers are all here already, so it's quick; one at a time, as ever (covers_now)."""
    time.sleep(180)
    try:
        finish_again()
    except Exception as e:
        log('Converted on John’s Mac, putting in place:', str(e)[:200])
    while True:
        try:
            covers_now()
        except Exception as e:
            log('Criterion covers (on the hour):', str(e)[:200])
        try:   # (films never matched, found on TMDB, each hour (a film at most once a week): John, 20 Nov 2026)
            if G_REF:
                match_unmatched(G_REF[0])
        except Exception as e:
            log('Matching the never-matched:', str(e)[:200])
        try:   # (the foreign-language films needing English subtitles, looked for each hour: John, 20 Nov 2026)
            subprep_scan()
        except Exception as e:
            log('Subtitles to make ready:', str(e)[:200])
        try:   # (Criterion's genres too, as its own lists give them, each hour: John, 20 Nov 2026)
            if G_REF:
                give_genres(G_REF[0])
        except Exception as e:
            log('Criterion genres (on the hour):', str(e)[:200])
        time.sleep(3 * 3600)   # (every three hours, not each hour: new films set it off themselves: 21 Nov 2026)


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


WALKC = {}   # (folder → (its date, what's in it), for files_under)


def files_under(root, vid):
    """As os.walk would give them: at the top, everything; below, the films and episodes. A folder whose date hasn't changed since last
    time isn't read again (only its date looked at), so a look over the library is a moment's work, not minutes of the G-Drive."""
    out = []

    def go(d, top):
        try:
            m = os.stat(d).st_mtime_ns
        except OSError:
            return
        c = WALKC.get(d)
        if not c or c[0] != m:
            try:
                c = WALKC[d] = (m, [(e.name, e.is_dir(follow_symlinks=False)) for e in os.scandir(d)])
            except OSError:
                return
        for n, isd in c[1]:
            p = os.path.join(d, n)
            if n.startswith('.'):   # (not listed, but looked inside, as os.walk did)
                if isd:
                    go(p, False)
                continue
            if top or (not isd and n.lower().endswith(vid)):
                out.append(p)
            if isd:
                go(p, False)
    go(root, True)
    return out


DL_NAMES = {'m': 0, 'd': {}}


def film_origin(path):
    """The name a film had when it was downloaded ("Medea.1969.1080p.CRIT.WEB-DL.AAC2.0.H.264-BLOOM"), from the downloader's own log of
    each one it brought in, for its folder: which release it is tells which subtitles fit it (John, 21 Nov 2026, "if you have the original
    file names it would probably be useful for finding the subtitles"). None if it didn't come that way."""
    f = os.path.expanduser('~/Grifflix/gdrive.log')
    try:
        m = os.path.getmtime(f)
    except OSError:
        return None
    if m != DL_NAMES['m']:
        d = {}
        try:
            for line in open(f, encoding='utf-8', errors='replace'):
                mm = re.search(r'Fetching from [^:]+: (.+?) → (.+?)/ \(', line)
                if mm:
                    src = mm.group(1).replace('／', '/').split('/')[-1].strip()
                    d[mm.group(2).split('/')[0].strip()] = os.path.splitext(src)[0] if re.search(r'\.[A-Za-z0-9]{2,4}$', src) else src
        except OSError:
            pass
        DL_NAMES.update(m=m, d=d)
    rel = os.path.relpath(str(path or ''), os.path.join(os.environ.get('GRIFFLIX_MEDIA') or '/Volumes/G-Drive/Grifflix', 'Movies'))
    got = DL_NAMES['d'].get(rel.split(os.sep)[0]) if not rel.startswith('..') else None
    if got:
        return got
    # (or the name it had before the converter made it again, kept in its record, where that was a download's name: The Boy and the Heron's
    # "The.Boy.and.The.Heron.2023.aka.Kimitachi.wa.do.ikiru.ka.HDCAM.1080p.x264": John, 21 Nov 2026, "which we have a record of the original name for")
    try:
        cs = load(os.path.expanduser('~/Grifflix/convert-state.json'), {}) or {}
        folder = os.path.dirname(str(path or ''))
        for v in (cs.get('done') or {}).values():
            for p_ in (v.get('from'), v.get('kept')):
                if p_ and os.path.dirname(v.get('to') or '') == folder and re.search(r'(?i)\b(1080p|720p|2160p|bluray|brrip|bdrip|web-?dl|webrip|dvdrip|x26[45]|h\.?26[45]|hevc|xvid|yts|rarbg)\b', os.path.basename(p_)):
                    return os.path.splitext(os.path.basename(p_))[0]
    except Exception:
        pass
    return None


CRIT_TMDB = {'m': 0, 'd': {}}


def crit_by_tmdb():
    """TMDB's number for each Criterion film → its title and year as Criterion gives them (criterion-tmdb.json and the list), read again
    only when either changes."""
    home = os.path.expanduser('~/Grifflix')
    fa, fb = os.path.join(home, 'criterion-tmdb.json'), os.path.join(home, 'criterion-list.json')
    try:
        m = os.path.getmtime(fa) + os.path.getmtime(fb)
    except OSError:
        return {}
    if m != CRIT_TMDB['m']:
        T = load(fa, {}) or {}
        out = {}
        for f in load(fb, []) or []:
            if '/films/' not in str(f.get('link') or ''):
                continue
            pk = re.sub(r'[^a-zA-Z0-9]', '', __import__('unicodedata').normalize('NFKD', str(f.get('title') or '')).encode('ascii', 'ignore').decode()).lower() + '|' + re.sub(r'[^0-9]', '', str(f.get('year') or ''))
            tid = str((T.get(pk) or [0])[0] or '')
            if tid and tid != '0':
                out.setdefault(tid, '%s (%s)' % (f.get('title'), f.get('year')) if f.get('year') else str(f.get('title')))
        CRIT_TMDB.update(m=m, d=out)
    return CRIT_TMDB['d']


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


MACQ = os.path.expanduser('~/Grifflix/mac-queue.json')
SUBPREP = os.path.expanduser('~/Grifflix/subprep.json')   # (the foreign-language films whose English subtitles are made ready on John's Mac)   # (films queued for John's Mac, in his order)
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
    try:   # (a half-made file left where it will go, from the Mac mini's own converting before: cleared, or it's taken for John's film)
        if plan.get('tmp') and os.path.exists(plan['tmp']):
            os.remove(plan['tmp'])
            log('To be converted on John’s Mac: %s: a half-made file from before cleared' % plan['item'].get('name'))
    except OSError:
        pass
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


def jf_look(g, paths):
    """The media server told of these files or folders only, not the whole library looked at again: each whole look cancelled the one
    under way and started it from nothing, so the re-reading of 1624 films' Extras folders never got to the end while films kept
    arriving (John, 18 Nov 2026, "Get everything up to date and working"). Only with no paths: the whole library, and not while it's
    already being looked at."""
    folders = []
    for p_ in paths or []:
        if p_:
            f_ = p_ if os.path.isdir(p_) else os.path.dirname(p_)
            if f_ and f_ not in folders:
                folders.append(f_)
    if folders:
        try:
            g.jf.call('POST', '/Library/Media/Updated', {'Updates': [{'Path': f_, 'UpdateType': 'Created'} for f_ in folders[:300]]})
            return
        except Exception as e:
            log('The media server told of %d folder(s): %s; it looks at the whole library instead' % (len(folders), e))
    try:
        if any(t.get('Key') == 'RefreshLibrary' and t.get('State') == 'Running' for t in g.jf.call('GET', '/ScheduledTasks') or []):
            return
    except Exception:
        pass
    g.jf.call('POST', '/Library/Refresh')


def macq_again(j, now, why):
    """A film John's Mac didn't finish through no fault of its own, back at the front of its queue (once: its job marked lost)."""
    Q = load(MACQ, []) or []
    if not any(x.get('id') == j['id'] for x in Q):
        Q.insert(0, dict({'id': j['id'], 'name': j['plan']['item'].get('name') or '', 'at': int(now)}, **({'force': True} if j.get('force') else {})))
        save(MACQ, Q)
    j['state'], j['ended'], j['again'], j['error'] = 'lost', now, True, why[:200]
    claims = load(os.path.expanduser('~/Grifflix/convert-claims.json'), {}) or {}
    if claims.pop(j['id'], None) is not None:
        save(os.path.expanduser('~/Grifflix/convert-claims.json'), claims)
    log('Queue for John’s Mac: %s back at the front (%s)' % (j['plan']['item'].get('name'), why))
    convjobs_save()


def short_again(job, got, want):
    """A film made on John's Mac shorter than it is (La Bola Negra: 2 h 30 of 2 h 39, John's Mac reading it over the network, which went a
    moment and ffmpeg took for its end: John, 24 Nov 2026, "we need to make sure that never happens"): back at the front of its queue, made
    again. Stopped at the same place twice (within a minute), the file itself is damaged there: not made again, and said so."""
    f = os.path.expanduser('~/Grifflix/convert-short.json')
    S = load(f, {}) or {}
    name, iid = job['plan']['item'].get('name') or '?', job['id']
    was = [x for x in S.get(iid) or [] if time.time() - x.get('at', 0) < 30 * 86400]
    hm = lambda t: '%d:%02d' % (t // 3600, t % 3600 // 60)
    if any(abs(x.get('got', 0) - got) <= 60 for x in was) or len(was) >= 3:
        job['state'], job['error'] = 'failed', ('stopped at %s of %s again: the film’s file seems damaged there, so it’s left as it is: replace it with another copy'
                                                % (hm(got), hm(want)))[:200]
        log('Converted on John’s Mac: %s: %s' % (name, job['error']))
        try:
            record('conversion stopped at the same place twice', film=name, id=iid, at_s=got, of_s=want)
        except Exception:
            pass
    else:
        S[iid] = was + [{'got': got, 'want': want, 'at': int(time.time())}]
        save(f, S)
        macq_again(job, time.time(), 'made %s of %s only (its reading stopped early: the network?): made again' % (hm(got), hm(want)))
    convjobs_save()


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
PEEK = {}   # (admin's Check TorBox: the look-only check running, if one is)
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
        d = tmdb(g, '/movie/%s' % tid)
        if d is None:   # (no answer: not kept, so asked again another time)
            return {'rt': 0, 'title': None, 'year': ''}
        TMDBC[tid] = {'rt': d.get('runtime') or 0, 'title': d.get('title'), 'year': (d.get('release_date') or '')[:4]}
        TMDBC['_dirty'] = 1
        time.sleep(.25)
    return TMDBC[tid]


CRIT_GENRES = ('Action/Adventure', 'Animation', 'Avant-garde', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Fantasy', 'Film Noir', 'Horror', 'Music',
               'Musical', 'Romance', 'Samurai', 'Science Fiction', 'Shorts', 'Silent', 'Thriller', 'War', 'Western')


def give_genres(g):
    """Each film of The Criterion Collection given Criterion's own genres in place of the ones it had, kept from being changed back; one
    Criterion gives none keeps its own (John, 17 Nov 2026: "Can previous genres for the film be removed and these added instead")."""
    home = os.path.expanduser('~/Grifflix')
    tj = load(os.path.join(home, 'criterion-tags.json'), {}) or {}
    T, F = tj.get('tags') or {}, tj.get('ftags') or {}
    # (a genre from Criterion's own list of it, the film shown there itself; a box set shown there gave every film in it that genre (2001: A
    # Space Odyssey a comedy, from Dr. Strangelove beside it in the Kubrick set: John, 20 Nov 2026, "is 2001 a space odyssey really listed as
    # a comedy?" ... "I want the criterion ones"): a film only in a set, never shown itself, keeps its set's)
    by, byd = {}, {}
    for k, v in T.items():
        if k in CRIT_GENRES:
            for sl in v:
                by.setdefault(sl, []).append(k)
    for k, v in F.items():
        if k in CRIT_GENRES:
            for sl in v:
                byd.setdefault(sl, []).append(k)
    if F:
        by = dict(by, **byd)
        # (a film Criterion only lists inside a box set: not given the set's genres; its own, from TMDB: John, 22 Nov 2026, "Re-read the
        # genres of films Criterion only lists inside a box set?" ... "yes")
        by = {sl: v for sl, v in by.items() if sl in byd}
    if not by:
        return
    gf = os.path.join(home, 'criterion-genres-given.json')
    given = load(gf, {}) or {}
    fixf = os.path.join(home, 'criterion-genres-fixed.json')
    fixed = {}
    put = 0
    try:
        for iid, sl in crit_pairs(g):
            if F and sl not in byd and given.get(iid) and not given.get('_own:' + iid):   # (given its set's before: its own read from TMDB, once)
                try:
                    full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid)) or {}
                    tid = str((full.get('ProviderIds') or {}).get('Tmdb') or '')
                    own = [x_.get('name') for x_ in ((tmdb(g, '/movie/' + tid) or {}).get('genres') or [])] if tid else []
                    own = [x_ for x_ in own if x_]
                    if own and sorted(full.get('Genres') or []) != sorted(own):
                        record('genres put back', film=full.get('Name'), id=iid, was=full.get('Genres'), now=own, why='its box set’s, not its own: TMDB’s')
                        full['Genres'] = own
                        full['GenreItems'] = [{'Name': x_} for x_ in own]
                        full['LockedFields'] = sorted(set((full.get('LockedFields') or []) + ['Genres']))
                        g.jf.call('POST', '/Items/%s' % iid, full)
                        put += 1
                    given.pop(iid, None)
                    given['_own:' + iid] = own or ['?']
                except Exception as e:
                    log('Its own genres for %s: %s' % (iid, e))
                continue
            gs = sorted(by.get(sl) or [])
            if F and sl in byd:   # (the site's Criterion filters as these too)
                fixed[sl] = gs
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
    save(fixf, fixed)
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
    owned = crit_owned(g, by_spine, by_ty)
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


def crit_missing_films(g):
    """Criterion's films on its own list that aren't in Grifflix and aren't in any of its box sets (those are listed with their sets): one
    row each, [spine, title, year, director, country, page], by spine (John, 24 Nov 2026, "can I get a list of what I'm missing from the
    criterion collection that isn't in the box sets too")."""
    home = os.path.expanduser('~/Grifflix')
    ct = load(os.path.join(home, 'criterion-tags.json'), {}) or {}
    lst = load(os.path.join(home, 'criterion-list.json'), []) or []
    in_sets = set(sl for mem in (ct.get('sets') or {}).values() for sl in mem)
    films, by_spine, by_ty = {}, {}, {}
    for f in lst:
        link = str(f.get('link') or '')
        if '/films/' not in link:
            continue
        sl = link.replace('https://www.criterion.com/films/', '')
        films[sl] = f
        if str(f.get('spine') or '').isdigit():
            by_spine.setdefault(int(f['spine']), sl)
        if re.match(r'^\d{4}$', str(f.get('year') or '')):
            by_ty.setdefault((fold(f.get('title')), int(f['year'])), sl)
    owned = crit_owned(g, by_spine, by_ty)
    # (an entry that is itself a set of films (Scorsese Shorts, The Kennedy Films of Robert Drew & Associates), had here as its films: not
    # missing (John, 24 Nov 2026, "the list is also including box sets, such as The Kennedy Films and Scorsese shorts that I already have"))
    have_sets = set(fold(nm) for nm, mem in (ct.get('sets') or {}).items() if any(sl in owned for sl in mem))
    try:
        have_sets.update(fold(x.get('Name')) for x in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'BoxSet'}, timeout=60) or {}).get('Items') or [])
    except Exception:
        pass
    rows = [[str(f.get('spine') or ''), str(f.get('title') or ''), str(f.get('year') or ''), str(f.get('director') or ''), str(f.get('country') or ''), str(f.get('link') or '')]
            for sl, f in films.items() if sl not in owned and sl not in in_sets and fold(f.get('title')) not in have_sets]
    rows.sort(key=lambda r: (0, int(r[0])) if r[0].isdigit() else (1, 0))
    return rows


CMF_AT = os.path.expanduser('~/Grifflix/criterion-missing-films.json')


def crit_missing_now():
    """The list kept ready, worked out after the library changes and when the gateway starts, so the admin page has it at once (John, 24 Nov
    2026, "it shouldn't have to load from the library each time")."""
    g = G_REF[0] if G_REF else None
    if not g:
        return
    try:
        rows = crit_missing_films(g)
        save(CMF_AT, {'at': int(time.time()), 'films': rows})
    except Exception as e:
        log('Criterion films missing: %s' % str(e)[:160])


def crit_owned(g, by_spine, by_ty):
    """The Criterion pages of the films Grifflix has: by Criterion's list matched to them, by TMDB number, by spine in a folder's name, and
    by title and year (a year either side)."""
    owned = set(sl for _, sl in crit_pairs(g))
    try:
        its = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie,Series', 'Fields': 'Path,ProductionYear,ProviderIds'}, timeout=180).get('Items') or []
        by_t = crit_tmdb_slugs()
        for it in its:
            if str((it.get('ProviderIds') or {}).get('Tmdb') or '') in by_t:   # (by its TMDB number, whatever year it has here)
                owned.add(by_t[str(it['ProviderIds']['Tmdb'])])
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
    return owned


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


def crit_tmdb_slugs():
    """TMDB's number for each Criterion film → its page's name on criterion.com (criterion-tmdb.json and the list): a film known by its
    number whatever year it has in Grifflix (The Men Who Tread on the Tiger's Tail is Criterion's 1945 and TMDB's 1952: John, 23 Nov 2026)."""
    home = os.path.expanduser('~/Grifflix')
    T = load(os.path.join(home, 'criterion-tmdb.json'), {}) or {}
    out = {}
    for f in load(os.path.join(home, 'criterion-list.json'), []) or []:
        sl = str(f.get('link') or '').replace('https://www.criterion.com/films/', '')
        if not sl or '/' in sl:
            continue
        pk = re.sub(r'[^a-zA-Z0-9]', '', __import__('unicodedata').normalize('NFKD', str(f.get('title') or '')).encode('ascii', 'ignore').decode()).lower() + '|' + re.sub(r'[^0-9]', '', str(f.get('year') or ''))
        tid = str((T.get(pk) or [0])[0] or '')
        if tid and tid != '0':
            out.setdefault(tid, sl)
    return out


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
    out, by_t = [], crit_tmdb_slugs()
    for it in g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': box['Id'], 'Fields': 'ProductionYear,ProviderIds'}).get('Items') or []:
        if it.get('Type') not in ('Movie', 'Series'):
            continue
        y = it.get('ProductionYear') or 0
        sl = next((by[(fold(it.get('Name')), y + d)] for d in (0, 1, -1) if (fold(it.get('Name')), y + d) in by), None) or \
            by_t.get(str((it.get('ProviderIds') or {}).get('Tmdb') or ''))   # (or by its TMDB number, its year another)
        if sl:
            out.append((it['Id'], sl))
    return out


def crit_year(pick):
    """Criterion's own year for a film chosen on TMDB, if it's one of Criterion's (its TMDB number in criterion-tmdb.json), else None:
    its folder named with Criterion's year, as the site shows it (John, 20 Nov 2026, "keep criterion film years")."""
    try:
        tid = str((pick.get('ProviderIds') or {}).get('Tmdb') or '')
        if not tid:
            return None
        for k, v in (load(os.path.expanduser('~/Grifflix/criterion-tmdb.json'), {}) or {}).items():
            if str((v or [0])[0]) == tid:
                y = k.rsplit('|', 1)[-1]
                return int(y) if re.match(r'^\d{4}$', y) else None
        # (or by its title and a year near TMDB's: Criterion's own link to TMDB can be wrong, Clean, Shaven's was)
        py = int(pick.get('ProductionYear') or 0)
        for f in load(os.path.expanduser('~/Grifflix/criterion-list.json'), []) or []:
            if '/films/' in str(f.get('link') or '') and fold(f.get('title')) == fold(pick.get('Name')) and re.match(r'^\d{4}$', str(f.get('year') or '')) \
                    and (not py or abs(int(f['year']) - py) <= 3):
                return int(f['year'])
        # (or the very title years apart, the one Criterion film of that title: made one year and shown later (The Men Who Tread on the
        # Tiger's Tail, Criterion's 1945, TMDB's 1952): John, 23 Nov 2026)
        same = [f for f in load(os.path.expanduser('~/Grifflix/criterion-list.json'), []) or [] if '/films/' in str(f.get('link') or '')
                and fold(f.get('title')) == fold(pick.get('Name')) and re.match(r'^\d{4}$', str(f.get('year') or ''))]
        if len(same) == 1 and py and abs(int(same[0]['year']) - py) <= 15:
            return int(same[0]['year'])
    except Exception:
        pass
    return None

AFTER_FIX = [None]
NEW_FU = {'t': None, 'first': 0, 'folders': set()}


def new_follow_up(paths=()):
    """What follows a film coming in (its Criterion cover and place, A24, its distributor) done once films stop coming in: ten minutes after
    the last, or half an hour after the first of a run of them, not three jobs for each. 30 films came in on 21 Nov 2026 and each set the
    covers going over the whole drive: 42 runs that day, the media server too busy to answer (John, 21 Nov 2026, "The converter shouldn't be
    putting that much pressure on the server")."""
    now_ = time.time()
    for p_ in paths or ():   # (each one's own folder in Movies, for the covers)
        rel_ = os.path.relpath(str(p_), os.path.join(os.environ.get('GRIFFLIX_MEDIA') or '/Volumes/G-Drive/Grifflix', 'Movies'))
        if not rel_.startswith('..') and rel_ != '.':
            NEW_FU['folders'].add(rel_.split(os.sep)[0])
    if NEW_FU['t']:
        NEW_FU['t'].cancel()
    else:
        NEW_FU['first'] = now_
    wait_ = max(150, min(600, NEW_FU['first'] + 1800 - now_))

    def run():
        NEW_FU['t'] = None
        only_, NEW_FU['folders'] = NEW_FU['folders'], set()
        for f in (lambda: covers_now(only_ or None), a24_now, bom_now, subprep_scan, set_covers_now, crit_missing_now):   # (new foreign films on the subtitles list then, not hours later)
            try:
                f()
            except Exception as e:
                log('After new films: %s' % e)
            time.sleep(30)
    NEW_FU['t'] = threading.Timer(wait_, run)
    NEW_FU['t'].daemon = True
    NEW_FU['t'].start()


def after_fixes():
    """What follows John fixing a film (the library looked over, its covers, A24, distributors) done once, five minutes after his last fix,
    not after each: one fix after another set the media server reading the whole drive again and again, and the Mac crawled (John, 20 Nov
    2026, "it's running incredibly slowly again", fixing the unmatched list)."""
    if AFTER_FIX[0]:
        AFTER_FIX[0].cancel()

    def run():
        AFTER_FIX[0] = None
        SCAN_NOW.set()
        for f in (covers_now, a24_now, bom_now, set_covers_now, crit_missing_now):
            try:
                time.sleep(60)
                f()
            except Exception as e:
                log('After the fixes: %s' % e)
    AFTER_FIX[0] = threading.Timer(300, run)
    AFTER_FIX[0].daemon = True
    AFTER_FIX[0].start()


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


ROMAN = {'ii': '2', 'iii': '3', 'iv': '4', 'vi': '6', 'vii': '7', 'viii': '8', 'ix': '9', 'xi': '11', 'xii': '12', 'xiii': '13', 'xiv': '14', 'xv': '15', 'xvi': '16', 'xvii': '17', 'xviii': '18', 'xix': '19', 'xx': '20'}


def fold(t):   # a title compared: letters and numbers only, accents plain, "&" as "and", numbers to twenty as words, no leading "the"
    import unicodedata
    NUM = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split()
    t = unicodedata.normalize('NFKD', (t or '').replace('&', ' and ')).encode('ascii', 'ignore').decode().lower()
    t = re.sub(r'\b(xviii|viii|xiii|xvii|iii|vii|xii|xiv|xvi|xix|ii|iv|vi|ix|xi|xv|xx)\b', lambda m: ROMAN[m.group(1)], t)   # (Ne Zha II = Ne Zha 2: John, 20 Nov 2026; II to XX, not a lone I, V or X)
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
    if not tried.get('_v2'):   # (once: every film looked at afresh, with titles that begin with the folder's now counted too)
        tried = {'_v2': 1}
    lid = (g.st.get('libraries') or {}).get('movies')
    if not lid:
        return
    # (a page at a time, and each film's own running time, not its files' full details: all 2,500 at once, with those, timed out every run
    # from 22 Nov 2026, so nothing was put right for a day; and L'Argent's file, another film half an hour longer, was never looked at again)
    items, start = [], 0
    while True:
        r_ = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'StartIndex': start, 'Limit': 300,
                                                              'Fields': 'Path,ProductionYear,ProviderIds,LockedFields'}, timeout=180) or {}
        items += r_.get('Items') or []
        start += 300
        if start >= (r_.get('TotalRecordCount') or 0) or not r_.get('Items'):
            break
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
                        # (the folder's title, or one that begins with it, its length deciding: the folder "Birdman (2014)" is Birdman or (The
                        # Unexpected Virtue of Ignorance), 119 minutes, and was put on The Birdman of that year, 76: 16 Nov 2026)
                        if any(fold(title) == t_ or (len(fold(title)) >= 5 and t_.startswith(fold(title))) for t_ in (fold(x.get('title')), fold(x.get('original_title')))):
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


def match_unmatched(g):
    """Films the media server never matched (no IMDb number: no TMDB match, or a wrong one), found on TMDB by themselves (John, 20 Nov 2026,
    "yes, do a full automatic match"): its title from its folder, or its file where a folder holds several films (Silent Night, Deadly
    Night's six), the clutter off (a year, "a.k.a.", "DDC", "01 ", "- Horror"); TMDB searched for it and any other title it gives, its year
    and a year either side; put on only when one film fits by title and year and, where both are known, by its length (within 10 minutes).
    The rest listed (match-unmatched.json and the log) for Fix match by hand. Each film tried at most once a week."""
    import difflib, unicodedata
    if not g.st.get('tmdb_key'):
        return
    lid = (g.st.get('libraries') or {}).get('movies')
    if not lid:
        return
    tried_at = os.path.expanduser('~/Grifflix/match-unmatched.json')
    T = load(tried_at, {}) or {}
    if T.get('_v') != 3:   # (the first run's "not found"s were TMDB asking it to slow down; v3: accented names and searches without a year)
        T = {k: v for k, v in T.items() if isinstance(v, dict) and v.get('done')}
        T['_v'] = 3
    items = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie',
                                                               'Fields': 'Path,ProductionYear,ProviderIds,LockedFields'}, timeout=180).get('Items') or []   # (no file details, and longer: it timed out on the busy Mac)
    VID = ('.mp4', '.mkv', '.m4v', '.mov', '.avi', '.ts', '.wmv', '.mpg', '.mpeg', '.webm')
    fixed, unsure = [], []
    for it in items:
        pid = it.get('ProviderIds') or {}
        if pid.get('Imdb') or it.get('LockData') or 'Name' in (it.get('LockedFields') or []):
            continue
        iid, p = it['Id'], it.get('Path') or ''
        if time.time() - (T.get(iid) or {}).get('at', 0) < 7 * 86400:
            continue
        folder = os.path.dirname(p)
        try:
            many = sum(1 for n in os.listdir(folder) if n.lower().endswith(VID) and not n.startswith('.')) > 1
        except OSError:
            continue
        raw = os.path.splitext(os.path.basename(p))[0] if many else os.path.basename(folder)
        raw = unicodedata.normalize('NFC', raw)   # (the Mac's file names spell é as e + accent, which TMDB's search doesn't find: La Jetée)
        ym = re.search(r'\((\d{4})\)', raw) or re.search(r'\b(19\d\d|20\d\d)\b', raw)
        year = int(ym.group(1)) if ym else int(it.get('ProductionYear') or 0)
        t = re.sub(r'\((\d{4})\)|\[[^\]]*\]', ' ', raw)
        t = re.sub(r'^\s*\d{1,2}[\s.\-]+', '', t)                                  # ("01 Silent Night…")
        t = re.sub(r'\s+-\s+(horror|comedy|drama|thriller)\b.*$', '', t, flags=re.I)      # ("- Horror 1984 Eng Subs 1080p")
        t = re.sub(r'\b(eng(lish)? subs?|subbed|\d{3,4}p|[hx]\.?26[45]|hevc|web-?dl|webrip|bluray|brrip|dvdrip|mp4|mkv)\b', ' ', t, flags=re.I)
        if ym:
            t = re.sub(r'\b%s\b' % ym.group(1), ' ', t)
        t = re.sub(r'\b(DDC|director.?s cut|extended|remastered|criterion)\b', ' ', t, flags=re.I)
        t = t.replace('\u2014', ' ').replace('_', ' ')
        names = []
        for part in re.split(r'\s*\(?\b(?:a\.?\s?k\.?\s?a\.?)\b\s*', t, flags=re.I):
            part = re.sub(r'[()]', ' ', part)
            part = re.sub(r'\s+', ' ', part).strip(' -,.')
            if len(part) >= 2:
                names.append(part)
        if ' - ' in t and not many and not re.match(r'\s*part\b', t.split(' - ', 1)[1], re.I):   # ("Les Blank - Always for Pleasure": its title after a name)
            names.append(re.sub(r'\s+', ' ', t.split(' - ', 1)[1]).strip(' -,.'))
        names = list(dict.fromkeys(n for n in names if n))
        ms = (it.get('MediaSources') or [{}])[0]
        mins = round((ms.get('RunTimeTicks') or it.get('RunTimeTicks') or 0) / 6e8)
        cands, failed, near = {}, False, []
        try:
            for n in names:
                for y in ([year, year - 1, year + 1, None] if year else [None]):   # (and with no year: TMDB's year is its first release anywhere)
                    q_ = {'query': n}
                    if y:
                        q_['year'] = y
                    time.sleep(.4)   # (gently: TMDB turned away the first run's quick searches, and they read as not found)
                    r_ = tmdb(g, '/search/movie', **q_)
                    if r_ is None:
                        failed = True
                        continue
                    for x in r_.get('results') or []:
                        cands[x['id']] = x
            scored = []
            for tid, x in cands.items():
                sim = max(difflib.SequenceMatcher(None, fold(n), fold(x.get(k))).ratio() for n in names for k in ('title', 'original_title'))
                xy = int((x.get('release_date') or '0')[:4] or 0)
                if sim < .85 or (year and xy and abs(xy - year) > 1):
                    continue
                rt = tmdb_runtime(g, tid)['rt'] if mins else 0
                if mins and rt and abs(rt - mins) > 10:
                    near.append('%s (%s, %d min; this file %d min)' % (x.get('title'), xy or '?', rt, mins))
                    continue
                scored.append((sim - (abs(xy - year) * .05 if year and xy else 0) - (abs(rt - mins) / 200 if mins and rt else 0), tid, x))
            scored.sort(key=lambda c: -c[0])
        except Exception as e:
            log('Matching %s: %s' % (raw, e))
            continue
        if failed and not scored:   # (TMDB didn't answer: tried again next time, not marked as not found)
            continue
        T[iid] = {'at': int(time.time()), 'name': raw}
        if scored and (len(scored) == 1 or scored[0][0] - scored[1][0] >= .08):
            sc, tid, x = scored[0]
            pick = {'Name': x.get('title'), 'ProductionYear': int((x.get('release_date') or '0')[:4] or 0) or None,
                    'ProviderIds': {'Tmdb': str(tid)}, 'SearchProviderName': 'TheMovieDb'}
            if str(pid.get('Tmdb') or '') == str(tid):
                T[iid]['note'] = 'already this TMDB film (it has no IMDb number there)'
                continue
            try:
                apply_match(g, iid, pick)
                T[iid].update(done=True, to='%s (%s)' % (pick['Name'], pick['ProductionYear'] or '?'), tmdb=tid)
                fixed.append('%s → %s (%s)' % (raw, pick['Name'], pick['ProductionYear'] or '?'))
            except Exception as e:
                T[iid]['note'] = 'couldn’t be put on: %s' % str(e)[:120]
        else:
            seen = sorted(cands.values(), key=lambda x: -(x.get('popularity') or 0))[:4]
            T[iid]['q'] = names
            T[iid]['saw'] = ['%s (%s)' % (x.get('title'), (x.get('release_date') or '')[:4]) for x in seen]
            T[iid]['note'] = ('found, but a different length: ' + '; '.join(near[:3])) if near and not scored else 'not found on TMDB' if not scored else 'several could be it: ' + '; '.join('%s (%s)' % (c[2].get('title'), (c[2].get('release_date') or '')[:4]) for c in scored[:3])
            unsure.append('%s (%s)' % (raw, T[iid]['note']))
    save(tried_at, T)
    if TMDBC.pop('_dirty', None):
        save(os.path.expanduser('~/Grifflix/tmdb-runtimes.json'), TMDBC)
    if fixed:
        log('Matched on TMDB, never matched before (%d): %s' % (len(fixed), '; '.join(fixed)[:2500]))
        threading.Timer(600, bom_now).start()   # (now with IMDb numbers: their distributors looked up too, once the media server has them)
        g.forget()
        g.publish_soon = time.time() + 300
    if unsure:
        log('Never matched, and not sure on TMDB (left for Fix match): %s' % '; '.join(unsure)[:2000])


def tmdb_once():
    """Once: every row of John's Criterion sheet given its TMDB link at once (John, 17 Nov 2026: "can we run as a job now"); after that only
    a new Criterion film's row, as it's added (covers_now, criterion-covers.py)."""
    home = os.path.expanduser('~/Grifflix')
    done = os.path.join(home, 'criterion-tmdb-retry2.done')   # (again once, with the wider matching, for the 191 not found the first time; and
    # again with the very title years apart (The Men Who Tread on the Tiger's Tail, Criterion's 1945, TMDB's 1952): John, 23 Nov 2026)
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
            if 'Added' in out:   # (films newly known by their TMDB numbers given their covers, spines and places in the collection)
                covers_now()
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


def _txt(x):
    import html as H
    x = re.sub(r'<[^>]+>', ' ', x)
    x = H.unescape(x).replace('See full company information', '').replace('See more details at IMDbPro', '')
    return re.sub(r'\s+', ' ', x).strip()


def bom_read(page):
    import html as H
    """A Box Office Mojo title page as the bookmark keeps it: {t: title, f: {label: value}, tb: [[row cells]...] per table}."""
    f = {}
    for chunk in re.split(r'<div[^>]*class="a-section a-spacing-none[^"]*"[^>]*>', page)[1:]:
        chunk = re.split(r'<div[\s>]|</div>', chunk)[0]
        chunk = re.sub(r'<svg.*?</svg>|<script.*?</script>|<style.*?</style>', ' ', chunk, flags=re.S)
        bits = [b for b in (H.unescape(x).strip() for x in re.split(r'<[^>]+>', chunk)) if b and b not in ('See full company information', 'See more details at IMDbPro')]
        if len(bits) < 2:
            continue
        if bits[-1].startswith('$') and len(bits) <= 5:   # (takings: "Domestic (20.4%)" and its sum)
            k, v = re.sub(r'\s*\(\s*', ' (', re.sub(r'\s*\)', ')', ' '.join(bits[:-1]))), bits[-1]
        else:
            k, v = bits[0], ' · '.join(bits[1:]) if bits[0] == 'Genres' else ' '.join(bits[1:])
        k, v = re.sub(r'\s+', ' ', k).strip(), re.sub(r'\s+', ' ', v).strip()
        if k.endswith(':') or '\u00a9' in v or 'Calendar' in v or k == 'IMDbPro':   # (the page's own menus and footer)
            continue
        if re.match(r'^\(\d{4}\) ', v):   # (its title, year and description)
            k, v = 'About', v[7:]
        if k and v and len(k) < 60 and k not in f:
            f[k] = v[:600]
    tabs = []
    for tm in re.finditer(r'<table[^>]*>(.*?)</table>', page, re.S):
        rows = []
        for rm in re.finditer(r'<tr[^>]*>(.*?)</tr>', tm.group(1), re.S):
            cells = []
            for cm in re.finditer(r'<t[hd][^>]*>(.*?)</t[hd]>', rm.group(1), re.S):
                v = _txt(cm.group(1))[:160]
                a = re.search(r'href="(/(?:release|releasegroup)/[^"?]+)', cm.group(1))
                cells.append(v + (' <%s>' % a.group(1) if a else ''))
            if cells:
                rows.append(cells)
        if rows:
            tabs.append(rows[:80])
    h1 = re.search(r'<h1[^>]*>(.*?)</h1>', page, re.S)
    return {'t': _txt(h1.group(1))[:200] if h1 else '', 'f': f, 'tb': tabs[:6]}


BOM_LOCK = threading.Lock()
# (a film with no cinema distributor that went straight to a streaming service: that service is its distributor, as Wikidata gives it, and
# only from when the service made its own films (Netflix's 1940s films are its licences, not its own): John, 20 Nov 2026, "if a film has
# no distributor and is streaming only, then the streaming platform counts as the distributor")
STREAMERS = [(r'^netflix$', 'Netflix', 2015), (r'^apple tv\+?$|^apple tv plus$|^apple original films$', 'Apple TV+', 2019),
             (r'^amazon prime video$|^prime video$|^amazon studios$|^amazon mgm studios$', 'Prime Video', 2015), (r'^hulu$', 'Hulu', 2017),
             (r'^disney\+$', 'Disney+', 2019), (r'^hbo max$|^max$|^hbo$|^hbo films$', 'HBO Max', 2019), (r'^paramount\+$', 'Paramount+', 2020),
             (r'^peacock$', 'Peacock', 2020)]


def streamers_of(ids):
    """Each film's streaming service, from Wikidata's "distributed by": {imdb: service}, for those with exactly one, or Hulu over Disney+."""
    out = {}
    for n in range(0, len(ids), 200):
        q = ('SELECT ?imdb ?distLabel WHERE { VALUES ?imdb { %s } ?film wdt:P345 ?imdb . ?film wdt:P750 ?dist . '
             'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }') % ' '.join('"%s"' % x for x in ids[n:n + 200])
        try:
            req = urllib.request.Request('https://query.wikidata.org/sparql?' + urllib.parse.urlencode({'query': q}),
                                         headers={'Accept': 'application/sparql-results+json', 'User-Agent': 'Grifflix/1.0 (family film library)'})
            with urllib.request.urlopen(req, timeout=120, context=tls()) as r_:
                rows = json.loads(r_.read()).get('results', {}).get('bindings', [])
        except Exception:
            continue
        got = {}
        for b in rows:
            d = b['distLabel']['value'].strip().lower()
            for pat, name, since in STREAMERS:
                if re.search(pat, d):
                    got.setdefault(b['imdb']['value'], set()).add(name)
        for t, names in got.items():
            if len(names) == 1:
                out[t] = names.pop()
            elif names == {'Hulu', 'Disney+'}:
                out[t] = 'Hulu'
        time.sleep(1)
    return out


IMDB_Q = ('query($id:ID!){ title(id:$id){ releaseYear{year} countriesOfOrigin{countries{text}} companyCredits(first:100, filter:{categories:["distribution"]}){ edges{ node{ '
          'company{ companyText{text} } countries{ text } yearsInvolved{ year } attributes{ text } } } } } }')
IMDB_HOME = ('dvd', 'vhs', 'blu-ray', 'laserdisc', 'video', 'tv', 'streaming', 'digital', 'betamax', 'syndication', 'internet', 'on demand', 'airing',
             'premiere', '4k', 'uhd', 'cable', 'broadcast')


def imdb_distributor(t):
    """Who first released a film in American cinemas, from IMDb's company credits (its own data service, as its pages use): the earliest
    US theatrical release that isn't a re-release; else the earliest US release that isn't TV or home video; else the earliest cinema release
    in its own country, named with the country. Returns (name, [its US releases, for the record])."""
    req = urllib.request.Request('https://caching.graphql.imdb.com/', data=json.dumps({'query': IMDB_Q, 'variables': {'id': t}}).encode(),
                                 headers={'content-type': 'application/json', 'x-imdb-client-name': 'imdb-web-next', 'User-Agent': 'Mozilla/5.0 (Macintosh) Grifflix'})
    with urllib.request.urlopen(req, timeout=30, context=tls()) as r_:
        T = (json.loads(r_.read()).get('data') or {}).get('title') or {}
    E = []
    for e in ((T.get('companyCredits') or {}).get('edges') or []):
        n = e.get('node') or {}
        E.append({'n': ((n.get('company') or {}).get('companyText') or {}).get('text') or '', 'c': [c.get('text') for c in n.get('countries') or []],
                  'y': (n.get('yearsInvolved') or {}).get('year') or 9999, 'a': ' '.join((x.get('text') or '') for x in n.get('attributes') or []).lower()})
    E = [e for e in E if e['n'] and 're-release' not in e['a']]
    first = lambda L: min(L, key=lambda e: e['y'])['n'] if L else ''
    us = [e for e in E if 'United States' in e['c']]
    rec = ['%s (%s%s)' % (e['n'], '' if e['y'] == 9999 else e['y'], ', ' + e['a'] if e['a'] else '') for e in sorted(us, key=lambda e: e['y'])]
    who = first([e for e in us if 'theatrical' in e['a']]) or first([e for e in us if not any(h in e['a'] for h in IMDB_HOME)])
    if not who:
        home = [c.get('text') for c in ((T.get('countriesOfOrigin') or {}).get('countries') or [])][:1]
        if home:
            w = first([e for e in E if home[0] in e['c'] and 'theatrical' in e['a']])
            who = '%s (%s)' % (w, home[0]) if w else ''
    if not who:   # (never in cinemas: the service or network that first showed it, in the US or worldwide, when it was new (Amazon Prime Video,
        # HBO, CBS), not a disc or a streaming service years on: John, 21 Nov 2026, "go with your suggestion")
        fy = ((T.get('releaseYear') or {}).get('year')) or 0
        shown = [e for e in E if ('United States' in e['c'] or 'World-wide' in e['c']) and any(k in e['a'] for k in ('tv', 'vod', 'streaming', 'internet', 'airing'))
                 and not any(k in e['a'] for k in ('dvd', 'vhs', 'blu-ray', 'laserdisc', 'betamax')) and (not fy or e['y'] <= fy + 1)]
        who = first(shown)
    return re.sub(r'\s+', ' ', who).strip()[:120], rec


AFI_LABELS = [('genre', r'genres?'), ('subj', r'subjects? \(major\)'), ('subjm', r'subjects? \(minor\)'), ('prod', r'production compan(?:y|ies)'),
              ('dist', r'distribution compan(?:y|ies)'), ('rel', r'release date'), ('prem', r'premiere information'), ('mins', r'duration(?: \(in mins\))?'),
              ('country', r'countr(?:y|ies)'), ('lang', r'languages?')]


def afi_parse(x):
    """An AFI Catalog film page, as John's browser saved its words (one block a line, each link's words between ‖ marks), sorted: its genre,
    subjects major and minor, production and distribution companies, release, length, country, language, and its History (John, 21 Nov
    2026, "Can we also get production company and distributors and the history info")."""
    L = [l.strip() for l in str(x or '').split('\n')]
    out = {}
    lab = re.compile(r'^(%s)\s*:\s*(.*)$' % '|'.join('(?P<%s>%s)' % (k, rx) for k, rx in AFI_LABELS), re.I)
    # (and the credits table's own headings, in capitals with no colon: its companies, the distributor among them (none was found from the
    # labels alone: John, 22 Nov 2026))
    head = re.compile(r'^(?:(?P<prod>PRODUCTION COMPAN(?:Y|IES))|(?P<dist>DISTRIBUTION COMPAN(?:Y|IES)))()$')
    link = lambda l: bool(re.match(r'^(‖[^‖]+‖[\s,;]*)+$', l))
    i = 0
    while i < len(L):
        m = lab.match(L[i])
        h_ = None if m else head.match(L[i])
        if not m and not h_:
            i += 1
            continue
        if m:
            k = next(k for k, _ in AFI_LABELS if m.group(k))
            val = [m.group(len(AFI_LABELS) + 2) or '']
        else:
            k = 'prod' if h_.group('prod') else 'dist'
            val = ['']
        j = i + 1
        # (its words: each of a list's names on a line of its own, with a blank line between, all taken (only the first was, Way Down
        # East's one subject of five); words that aren't links, the one line)
        while j < len(L) and len(val) < 40:
            l_ = L[j]
            if not l_:
                j += 1
                continue
            if lab.match(l_) or head.match(l_) or re.match(r'^[A-Z][A-Z &/()\-]{2,}$', l_):
                break
            if link(l_):
                val.append(l_)
                j += 1
                continue
            if not ''.join(val).strip():
                val.append(l_)
                j += 1
            break
        v = ' '.join(val)
        items = [t.strip(' ,;') for t in re.findall(r'‖([^‖]+)‖', v) if t.strip(' ,;')]
        if not items:
            items = [t.strip() for t in re.split(r'\s*[;,]\s*', re.sub(r'‖', '', v)) if t.strip()]
        if items and k in ('rel', 'prem', 'mins'):
            out.setdefault(k, re.sub(r'\s+', ' ', v.replace('‖', ' ')).strip()[:300])
        elif items:   # (a list met twice, in the summary and in the credits: the names of both, once each)
            out[k] = list(dict.fromkeys((out.get(k) or []) + items))[:30]
        i = j
    # (its History: the words under the heading History, to the next heading)
    for n, l in enumerate(L):
        if re.match(r'^history$', l.replace('‖', ''), re.I):
            h = []
            for l2 in L[n + 1:]:
                c = l2.replace('‖', '')
                if h and (re.match(r'^[A-Z][A-Z &/()\-]{2,}$', c) or re.match(r'^(cast|crew|credits|full credits|details|genre|subject|notes|production compan(y|ies)|'
                                                                                         r'distribution compan(y|ies)|source citation|physical properties|songs?|music|related titles?|'
                                                                                         r'production text|summary|synopsis|watch|where to watch)\s*:?$', c, re.I)):
                    break
                if c:
                    h.append(c)
            h = '\n\n'.join(h).strip()
            # (AFI shows the History shortened, then "More", "Less" and the whole of it: the whole only, not twice: 22 Nov 2026)
            m_ = re.search(r'\n\s*More\s*\n\s*Less\s*\n', h)
            if m_:
                h = h[m_.end():].strip()
            h = re.sub(r'\n\s*(More|Less)\s*$', '', h).strip()
            if len(h) > 80:
                out['hist'] = h[:20000]
            break
    return out


AFI_C = {'m': None, 'A': {}, 'AD': {}, 'f': {}}


def afi_of(imdb):
    """A film's AFI Catalog details (its page's number, genre, subjects, companies, History), for a member's own look at it: kept off the
    site's public files, which anyone can read (John, 22 Nov 2026, "Can any afi information be for members only" ... "Yes")."""
    if not re.match(r'^tt\d{6,10}$', imdb or ''):
        return {}
    try:
        af, adf = os.path.expanduser('~/Grifflix/afi-ids.json'), os.path.expanduser('~/Grifflix/afi-details.json')
        m = (os.path.getmtime(af) if os.path.exists(af) else 0, os.path.getmtime(adf) if os.path.exists(adf) else 0)
        if AFI_C['m'] != m:
            AFI_C.update(m=m, A=load(af, {}) or {}, AD=load(adf, {}) or {}, f={})
        out = {}
        a = (AFI_C['A'].get(imdb) or {}).get('id')
        if a:
            out['afi'] = str(a)
        if imdb not in AFI_C['f']:
            x = (AFI_C['AD'].get(imdb) or {}).get('x')
            AFI_C['f'][imdb] = afi_parse(x) if x else {}
        f_ = AFI_C['f'][imdb]
        for k_, src in (('afig', 'genre'), ('afisub', 'subj'), ('afisubm', 'subjm'), ('afiprod', 'prod'), ('afidist', 'dist'), ('afih', 'hist')):
            if f_.get(src):
                out[k_] = f_[src]
        return out
    except Exception:
        return {}


DLIBS = {'at': 0, 'd': {}}


def dist_tags(g, items):
    """Each film's original distributor (distributors.json: Box Office Mojo, IMDb, its streaming service) as 'dist', and the distributors
    whose libraries hold it now as 'libs', from their own film lists (grifflix/distributor-libraries.json, kept with the site): by title and
    a year within one; a list with no years (Sony Pictures Classics, Strand) only for its own releases, so another film of the name isn't
    taken for it (John, 21 Nov 2026: "Add it")."""
    import unicodedata
    if time.time() - DLIBS['at'] > 3600:
        try:
            with urllib.request.urlopen(urllib.request.Request('https://cooperking.github.io/grifflix/distributor-libraries.json', headers={'User-Agent': 'Grifflix gateway'}), timeout=60, context=tls()) as r_:
                DLIBS['d'] = json.loads(r_.read())
            DLIBS['at'] = time.time()
        except Exception as e:
            log('Distributor libraries: %s' % e)
    D = load(os.path.expanduser('~/Grifflix/distributors.json'), {}) or {}
    ROM = {'ii': '2', 'iii': '3', 'iv': '4', 'vi': '6', 'vii': '7', 'viii': '8', 'ix': '9'}

    def key(t):
        t = unicodedata.normalize('NFKD', str(t or '').replace('&', ' and ')).encode('ascii', 'ignore').decode().lower()
        t = re.sub(r'\s*\((19|20)\d\d\)\s*$', '', t)
        t = re.sub(r'^(.*), (the|a|an)$', r'\2 \1', t)
        t = re.sub(r'\b(ii|iii|iv|vi|vii|viii|ix)\b', lambda m: ROM[m.group(1)], t)
        t = re.sub(r'[^a-z0-9 ]', ' ', t).strip()
        return re.sub(r'\s+', '', re.sub(r'^(the|a|an) ', '', t))
    lid = (g.st.get('libraries') or {}).get('movies')
    mine, start = [], 0
    while lid:
        r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'ProviderIds,OriginalTitle',
                                                               'StartIndex': start, 'Limit': 500, 'EnableImages': 'false'}, timeout=120) or {}
        mine += r.get('Items') or []
        start += 500
        if start >= (r.get('TotalRecordCount') or 0):
            break
    by, imdb = {}, {}
    for it in mine:
        imdb[it['Id']] = str((it.get('ProviderIds') or {}).get('Imdb') or '')
        for k in {key(it.get('Name')), key(it.get('OriginalTitle'))} - {''}:
            by.setdefault(k, []).append(it)
    libs = {}
    for dn, L in (DLIBS['d'] or {}).items():
        w = [x for x in dn.lower().split() if x != 'the'][0]
        for title, year in L:
            c = by.get(key(title), [])
            if year:
                c = [x for x in c if not x.get('ProductionYear') or abs(int(x['ProductionYear']) - int(year)) <= 1]
            elif len(c) > 1:
                c = []
            if not year:
                c = [x for x in c if w in (D.get(imdb.get(x['Id'])) or '').lower()]
            for x in c:
                if dn not in libs.setdefault(x['Id'], []):
                    libs[x['Id']].append(dn)
    # (each film's own page in the AFI Catalog, by AFI's number for it from Wikidata (AFI itself turns automated readers away); each film
    # asked once, one with none again after a month: John, 21 Nov 2026, "link to the actual page not the search")
    af = os.path.expanduser('~/Grifflix/afi-ids.json')
    A = load(af, {}) or {}
    ask = [t for t in set(imdb.values()) if re.match(r'^tt\d{6,10}$', t) and (t not in A or (not A[t].get('id') and (not A[t].get('w') or time.time() - A[t].get('at', 0) > 30 * 86400)))]
    wp = {}
    for n in range(0, len(ask), 300):
        q = ('SELECT ?imdb ?afi ?art WHERE { VALUES ?imdb { %s } ?f wdt:P345 ?imdb . OPTIONAL { ?f wdt:P3593 ?afi } '
             'OPTIONAL { ?art schema:about ?f ; schema:isPartOf <https://en.wikipedia.org/> } }') % ' '.join('"%s"' % x for x in ask[n:n + 300])
        try:
            req = urllib.request.Request('https://query.wikidata.org/sparql?' + urllib.parse.urlencode({'query': q}),
                                         headers={'Accept': 'application/sparql-results+json', 'User-Agent': 'Grifflix/1.0 (family film library)'})
            with urllib.request.urlopen(req, timeout=120, context=tls()) as r_:
                rows = json.loads(r_.read()).get('results', {}).get('bindings', [])
        except Exception:
            break
        got = {b['imdb']['value']: b['afi']['value'] for b in rows if 'afi' in b}
        for b in rows:
            if 'art' in b and b['imdb']['value'] not in got:
                wp[urllib.parse.unquote(b['art']['value'].split('/wiki/', 1)[-1]).replace('_', ' ')] = b['imdb']['value']
        for t in ask[n:n + 300]:
            A[t] = {'id': got.get(t, ''), 'at': int(time.time()), 'w': 1}   # (w: Wikipedia looked at too)
        time.sleep(1)
    # (and where Wikidata hasn't AFI's number, the film's English Wikipedia article's own link to its AFI page (its External links), read
    # from Wikipedia's API, gently: John, 21 Nov 2026, "There must be other sources that have the links")
    titles = list(wp)
    for n in range(0, len(titles), 20):
        try:
            req = urllib.request.Request('https://en.wikipedia.org/w/api.php?' + urllib.parse.urlencode(
                {'action': 'query', 'format': 'json', 'prop': 'extlinks', 'titles': '|'.join(titles[n:n + 20]), 'ellimit': '500', 'redirects': 1, 'maxlag': 5}),
                headers={'User-Agent': 'Grifflix/1.0 (family film library)'})
            with urllib.request.urlopen(req, timeout=60, context=tls()) as r_:
                d_ = json.loads(r_.read()).get('query') or {}
        except Exception:   # (asked to slow down: the rest another time)
            for t_ in titles[n:]:
                A.pop(wp[t_], None)
            break
        back = {x['to']: x['from'] for x in (d_.get('redirects') or []) + (d_.get('normalized') or [])}
        for pg in (d_.get('pages') or {}).values():
            t_ = pg.get('title')
            t_ = back.get(t_, t_)
            t_ = back.get(t_, t_)
            for l_ in pg.get('extlinks') or []:
                m_ = re.search(r'afi\.com/.*?(?:moviedetails/|[?&]movie=)(\d+)', l_.get('*') or l_.get('url') or '', re.I)
                if m_ and wp.get(t_):
                    A[wp[t_]] = {'id': m_.group(1), 'at': int(time.time()), 'from': 'wikipedia'}
                    break
        time.sleep(3)
    if ask:
        save(af, A)
    AD = load(os.path.expanduser('~/Grifflix/afi-details.json'), {}) or {}
    for iid, o in items.items():
        o.pop('dist', None)
        o.pop('libs', None)
        o.pop('afi', None)
        if (A.get(imdb.get(iid) or '-') or {}).get('id'):
            o['afi'] = A[imdb[iid]]['id']
        for k_ in ('afig', 'afisub', 'afisubm', 'afiprod', 'afidist', 'afih'):
            o.pop(k_, None)
        ad_ = AD.get(imdb.get(iid) or '-') or {}
        f_ = afi_parse(ad_['x']) if ad_.get('x') else (ad_.get('f') or {})   # (read again each time, so a better reading reaches every page kept)
        for k_, src in (('afig', 'genre'), ('afisub', 'subj'), ('afisubm', 'subjm'), ('afiprod', 'prod'), ('afidist', 'dist'), ('afih', 'hist')):
            if f_.get(src):
                o[k_] = f_[src]
        d = D.get(imdb.get(iid) or '-') or ''
        if d:
            o['dist'] = d
        l_ = [n for n in libs.get(iid, []) if [x for x in n.lower().split() if x != 'the'][0] not in d.lower()]   # (not its own release again)
        if l_:
            o['libs'] = l_


def bom_now():
    """Each film come in (or newly matched, so with its IMDb number now) looked up on Box Office Mojo by the Mac mini itself: its US
    distributor and everything else on its page, kept with what John's bookmark read (distributors.json, distributors-full.json): John, 20
    Nov 2026, "an automatic box office mojo check for any new films coming in, including the ones not matched" ... "just when films come
    in". Only films not looked up before; one page every two seconds."""
    g = G_REF[0] if G_REF else None
    if not g or not BOM_LOCK.acquire(blocking=False):
        return
    try:
        lid = (g.st.get('libraries') or {}).get('movies')
        if not lid:
            return
        at, fa = os.path.expanduser('~/Grifflix/distributors.json'), os.path.expanduser('~/Grifflix/distributors-full.json')
        D = load(at, {}) or {}
        films = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'ProviderIds'}).get('Items') or []
        todo = []
        for f in films:
            t = str((f.get('ProviderIds') or {}).get('Imdb') or '')
            if re.match(r'^tt\d{6,10}$', t) and t not in D and t not in todo:
                todo.append((t, f.get('Name')))
        F = load(fa, {}) or {}
        got = []
        for t, name in todo[:150]:
            try:
                req = urllib.request.Request('https://www.boxofficemojo.com/title/%s/' % t, headers={'User-Agent': 'Mozilla/5.0 (Macintosh) Grifflix'})
                with urllib.request.urlopen(req, timeout=40, context=tls()) as r_:
                    v = bom_read(r_.read().decode('utf-8', 'replace'))
            except urllib.error.HTTPError as e:
                v = {'t': '', 'f': {}, 'tb': [], 'none': e.code}
                if e.code in (429, 503):
                    break
            except Exception:
                continue
            F[t] = v
            D[t] = re.sub(r'\s+', ' ', (v.get('f') or {}).get('Domestic Distributor') or '').strip()[:120]
            got.append('%s: %s' % (name, D[t] or 'none'))
            time.sleep(2)
        # (none on Box Office Mojo, which hardly knows films before 1980: IMDb's own record of who released it (its company credits), each
        # film asked once: John, 21 Nov 2026, "we need to sort out the distribution work")
        fromimdb = []
        for t in [t for t, v in D.items() if not v and re.match(r'^tt\d{6,10}$', t) and not (F.get(t) or {}).get('im2')][:1200]:   # (im2: asked again with TV and streaming)
            try:
                who, us = imdb_distributor(t)
            except Exception as e:
                if '429' in str(e) or '503' in str(e):
                    break
                continue
            F.setdefault(t, {'t': '', 'f': {}, 'tb': []})['im'] = F[t]['im2'] = 1
            if us:
                F[t]['imdb'] = us[:12]
            if who:
                D[t] = who
                F[t].setdefault('f', {})['IMDb distributor'] = who
                fromimdb.append(who)
            if len(fromimdb) % 50 == 49:
                save(at, D)
                save(fa, F)
            time.sleep(.5)
        if fromimdb:
            log('IMDb, for films Box Office Mojo had no distributor for (%d): %s' % (len(fromimdb), ', '.join('%s %d' % (k, fromimdb.count(k)) for k in sorted(set(fromimdb), key=lambda k: -fromimdb.count(k))[:15])))
        # (no cinema distributor: its streaming service, if it went straight to one, from Wikidata; each film asked once)
        years = {str((f.get('ProviderIds') or {}).get('Imdb') or ''): int(f.get('ProductionYear') or 0) for f in films}
        ask = [t for t, v in D.items() if not v and t in years and not (F.get(t) or {}).get('ws')]
        streamed = []
        if ask:
            S_ = streamers_of(ask)
            for t in ask:
                F.setdefault(t, {'t': '', 'f': {}, 'tb': []})['ws'] = 1
                svc = S_.get(t)
                since = next((x[2] for x in STREAMERS if x[1] == svc), 9999)
                if svc and years.get(t, 0) >= since:
                    D[t] = svc
                    F[t].setdefault('f', {})['Streaming only'] = svc
                    streamed.append(svc)
        save(at, D)
        save(fa, F)
        if got:
            log('Box Office Mojo, for what came in (%d): %s' % (len(got), '; '.join(got)[:1200]))
        if streamed:
            log('Streaming only, the service as its distributor (%d): %s' % (len(streamed), ', '.join('%s %d' % (k, streamed.count(k)) for k in sorted(set(streamed)))))
    except Exception as e:
        log('Box Office Mojo, for what came in: %s' % str(e)[:200])
    finally:
        BOM_LOCK.release()


# ---------------------------------------------------------------- which of the Criterion Collection's films are Criterion's own release
# (John, 24 Nov 2026: "Quite a few of my criterion collection films may not actually be the criterion release - I can only tell by seeing the
# logo at the start play - is there a way to get a record of what are and what aren't" ... "mark it on the film, but only I should be able to
# see it - and I'd like a list"). The start of each film looked at, as he does: its first 30 seconds, two pictures a second, each made a
# small fingerprint (16 x 16, how light changes across it). Criterion's logo is learnt from the films whose own file names say Criterion
# (the frames most of them share, and the films outside the Criterion Collection don't), then looked for in every one.
CRL = os.path.expanduser('~/Grifflix/critlogo')
CRL_SECS = 30   # (the logo, where there is one, within the first 15 seconds: 30 to be safe: John, 24 Nov 2026)
# (each picture kept small (32 x 18, grey) and compared as it is, not as a rough outline: the outlines took one studio's logo on a dark
# ground for another's, Intimate Relations' for Criterion's, and missed a seventh of the real ones: 24 Nov 2026)
# (read again (3): the pictures between others were left undecoded to be quicker, and some films' openings came out smeared (Exotica's): 24 Nov;
# (4): each kept its own shape, its black bars put back, as on the screen: a widescreen film kept without them had the logo's ring squeezed
# tall (Short Cuts, Carlos, Tess), a squarer one stretched wide (Touki Bouki, Odd Man Out, Black Orpheus, M), .7 like it, not .85 to .9)
CRL_W, CRL_H, CRL_V = 32, 18, 4
CRL_VF = ("scale=w='if(gte(dar,16/9),320,round(90*dar)*2)':h='if(gte(dar,16/9),round(160/dar)*2,180)':flags=area,"
          "pad=320:180:(ow-iw)/2:(oh-ih)/2,scale=32:18:flags=area,format=gray")   # (fitted ten times the size first: the ring not shifted by half a picture's dot)
CRL_T = float(os.environ.get('GRIFFLIX_CRL_T') or .9)   # (how alike two pictures must be, 1 being the same)
# (against John's own pictures of the logo: the ring of Criterion's logo, as it plays in his films (Homicide, Secrets & Lies), .85 to .9 like
# them, and nothing else in his logo history video above .7; in two of a film's pictures at least, the logo being on the screen for seconds)
CRL_TG = float(os.environ.get('GRIFFLIX_CRL_TG') or .8)
CRL_HITS = 3   # (three: Eddington has a small ring of its own, 23 seconds in, like it in two pictures; the logo is in twenty)
CRL_CH = .85   # (the Criterion Channel's opening, learnt from a film of his that has it: as alike as this)
CRL_SHARE, CRL_NEG = .2, 2   # (a logo picture: in a fifth of the films named Criterion at least, and in fewer than 2 films outside)
CRL_STATE = {}
CRL_NAME = re.compile(r'(?i)(criterion|(^|[._ \-\[(])CC([._ \-\])]|$))')


def crl_vec(b):
    """A picture's pixels made comparable: its own average taken away and its contrast evened (a flat or black one: None)."""
    n = len(b)
    m = sum(b) / float(n)
    d = [v - m for v in b]
    ss = sum(x * x for x in d)
    if ss / n < 49:   # (black, or one flat colour: everywhere, says nothing)
        return None
    k = ss ** .5
    return [x / k for x in d]


def crl_small(v):
    """The same picture at half the size, for a quick first look before the full comparison."""
    out = []
    for r in range(0, CRL_H, 2):
        for c in range(0, CRL_W, 2):
            out.append(v[r * CRL_W + c] + v[r * CRL_W + c + 1] + v[(r + 1) * CRL_W + c] + v[(r + 1) * CRL_W + c + 1])
    k = sum(x * x for x in out) ** .5 or 1
    return [x / k for x in out]


def crl_alike(a, b):
    """How alike two pictures are, 1 the same, 0 nothing alike (their correlation)."""
    import operator
    return sum(map(operator.mul, a, b))


def crl_match(fv, logo):
    """Whether a picture (its full and small forms) is one of the logo's (each logo picture with how alike it must be, CRL_T if not said)."""
    v, sm = fv
    for l in logo:
        t_ = l[5] if len(l) > 5 else CRL_T
        if crl_alike(sm, l[1]) >= t_ - .05 and crl_alike(v, l[0]) >= t_:
            return True
    return False


def crl_ffmpeg(g):
    cands = []
    try:
        enc = g.jf.call('GET', '/System/Configuration/encoding') or {}
        cands += [enc.get('EncoderAppPath'), enc.get('EncoderAppPathDisplay')]
    except Exception:
        pass
    import shutil
    cands += ['/Applications/Jellyfin.app/Contents/MacOS/ffmpeg', '/usr/local/bin/ffmpeg', '/opt/homebrew/bin/ffmpeg', shutil.which('ffmpeg')]
    return next((c for c in cands if c and os.path.exists(c)), None)


def crl_frames(ff, path, logo=None):
    """The film's first CRL_SECS seconds, two pictures a second, each kept small and grey: [[seconds, base64], ...], flat ones left out; None
    if unread. With the logo known, it stops reading the moment it sees it (John, 24 Nov 2026, "it also can stop reading once the logo is
    recognised")."""
    import subprocess
    sz = CRL_W * CRL_H
    # (the smoothing filter left out: quicker, and nothing a picture 32 across shows; each tried plainer if a copy won't have it. The pictures
    # between others (B-frames) were left undecoded too, and in some films those are what the next ones are made from: Exotica's opening came
    # out smeared, its logo in one picture of twenty: 24 Nov 2026)
    fast = ['-skip_loop_filter', 'all']
    for opt in (['-hwaccel', 'videotoolbox'] + fast, fast, []):
        out, n, hit, seen = [], 0, False, 0
        try:
            p = subprocess.Popen([ff, '-nostdin', '-v', 'error'] + opt + ['-t', str(CRL_SECS), '-i', path, '-an', '-sn', '-dn', '-vf',
                                  'fps=2,' + CRL_VF, '-f', 'rawvideo', '-'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception:
            continue
        t0 = time.time()
        try:
            while time.time() - t0 < 600:
                px = p.stdout.read(sz)
                if len(px) < sz:
                    break
                n += 1
                v = crl_vec(px)
                if v is None:
                    continue
                out.append([round((n - 1) / 2.0, 1), base64.b64encode(px).decode()])
                if logo and crl_match((v, crl_small(v)), logo):
                    seen += 1
                    if seen >= CRL_HITS:
                        hit = True
                        break
        finally:
            try:
                p.kill()
            except Exception:
                pass
            p.wait()
        if hit or n >= 20:
            return out
    return None


CRL_IN = os.path.join(os.environ.get('GRIFFLIX_MEDIA') or '/Volumes/G-Drive/Grifflix', 'Criterion logo')


def crl_videos_in(ff):
    """A video of the logo put in the G-Drive's "Criterion logo" folder (one the browser can't play, an MKV in Safari): read here, two
    pictures a second, the flat and repeated ones left out, and given as his pictures of the logo (John, 24 Nov 2026, "sorry it was actually an
    mkv"). Each once (again if it changes). True if any were added."""
    import subprocess
    try:
        os.makedirs(CRL_IN, exist_ok=True)
        names = [n for n in os.listdir(CRL_IN) if not n.startswith('.') and os.path.splitext(n)[1].lower() in ('.mkv', '.mp4', '.m4v', '.mov', '.webm', '.avi', '.ts')]
    except OSError:
        return False
    df = os.path.join(CRL, 'videos-read.json')
    done = load(df, {}) or {}
    added = False
    for n in names:
        fp = os.path.join(CRL_IN, n)
        try:
            key = '%d' % os.path.getsize(fp)
            if done.get(n) == key or time.time() - os.path.getmtime(fp) < 120:   # (read already, or still being copied in)
                continue
        except OSError:
            continue
        sz = CRL_W * CRL_H
        try:
            r = subprocess.run([ff, '-nostdin', '-v', 'error', '-i', fp, '-an', '-sn', '-dn', '-vf', 'fps=2,' + CRL_VF,
                                '-f', 'rawvideo', '-'], capture_output=True, timeout=1800)
        except Exception as e:
            log('Criterion logo video %s: %s' % (n, str(e)[:120]))
            continue
        raw, pics, last = r.stdout or b'', [], None
        for k in range(len(raw) // sz):
            px = raw[k * sz:(k + 1) * sz]
            v = crl_vec(px)
            if v is None or (last is not None and crl_alike(v, last) >= .97):
                continue
            last = v
            pics.append({'src': n, 't': k / 2.0, 'px': base64.b64encode(px).decode(), 'at': int(time.time())})
        G_ = [x for x in (load(os.path.join(CRL, 'given.json'), []) or []) if x.get('src') != n] + pics
        save(os.path.join(CRL, 'given.json'), G_)
        done[n] = key
        save(df, done)
        log('Criterion logo video %s: %d pictures of it given' % (n, len(pics)))
        record('Criterion logo pictures from a video', video=n, pictures=len(pics))
        added = added or bool(pics)
    return added


def crl_hashes():
    """Every film's fingerprints, each kept in a file of its own (critlogo/h/<id>.json), so each is saved the moment it's looked at."""
    H, d = {}, os.path.join(CRL, 'h')
    try:
        for n in os.listdir(d):
            if n.endswith('.json'):
                v = load(os.path.join(d, n), None)
                if isinstance(v, dict):
                    H[n[:-5]] = v
    except OSError:
        pass
    return H


def crl_names(path):
    """What a film's file has been called: its own name now, and what it came in as (the record of every change)."""
    names = [os.path.basename(path or ''), os.path.basename(os.path.dirname(path or ''))]
    return names


def crl_vectors(H, ids):
    """Each film's pictures made comparable: {id: [(seconds, (full, small)), ...]}."""
    out = {}
    for i in ids:
        fr = ((H.get(i) or {}).get('f') or []) if (H.get(i) or {}).get('v') == CRL_V else []
        L = []
        for t, b in fr:
            v = crl_vec(base64.b64decode(b))
            if v is not None:
                L.append((t, (v, crl_small(v))))
        out[i] = L
    return out


def crl_learn(H, pos, neg):
    """Criterion's logo: the pictures shared by at least a fifth of the films whose names say Criterion (and those John has said are), and by
    fewer than two films outside the Criterion Collection: [(full, small, how many share it, film, seconds), ...]."""
    V = crl_vectors(H, [i for i in pos + neg if (H.get(i) or {}).get('f')])
    P = {i: [x for x in V[i] if x[0] <= 25] for i in pos if V.get(i)}
    N = [V[i] for i in neg if V.get(i)]
    # (John's own pictures of the logo, each version of it (from his logo history video): each used as it is, and the pictures in his films
    # most like it learnt too, as they look in a film (John, 24 Nov 2026, "I could have give you the pictures to learn from if you'd have said"))
    given, vid = [], []
    for gp in load(os.path.join(CRL, 'given.json'), []) or []:
        if gp.get('off'):   # (one of a video's he's left out: John, 24 Nov 2026, "the main logo starts halfway through so that's the one to learn")
            continue
        try:
            v = crl_vec(base64.b64decode(gp.get('px') or ''))
        except Exception:
            v = None
        if v is not None:
            (vid if gp.get('src') else given).append((v, crl_small(v), 0, 'given: ' + str(gp.get('src') or gp.get('name') or ''), gp.get('t') or 0, CRL_TG))
    # (the pictures of his logo history video (John, 24 Nov 2026, "I need to load an mp4 of the logos history"): each a picture of the logo,
    # unless films outside the Criterion Collection have it too (a title card, a black screen with words))
    for vv in vid:
        if not any(crl_alike(vv[0], l[0]) >= .97 for l in given) and sum(1 for fs in N if any(crl_match(x[1], [vv]) for x in fs)) < CRL_NEG:
            given.append(vv)
    from_given = []
    for gv in [x for x in given if not str(x[3]).startswith('given: ') or x[4] == 0][:40]:
        for i_, fs in P.items():
            best = max(fs, key=lambda x: crl_alike(x[1][0], gv[0]), default=None)
            if best and crl_alike(best[1][0], gv[0]) >= .75:
                from_given.append((best[1][0], best[1][1], 0, i_, best[0]))
    if len(P) < 4:
        return given + from_given
    need = max(2, int(round(len(P) * CRL_SHARE)))
    cand, ids = [], list(P)
    for a in ids:
        for t, fv in P[a]:
            sup = 1 + sum(1 for b in ids if b != a and any(crl_match(fv, [(x[1][0], x[1][1])]) for x in P[b]))
            if sup >= need:
                cand.append((sup, a, t, fv))
    logo = list(given)
    for fg in from_given:   # (those found by his pictures: kept unless films outside the collection have them too)
        if not any(crl_alike(fg[0], l[0]) >= .97 for l in logo) and sum(1 for fs in N if any(crl_match(x[1], [fg]) for x in fs)) < CRL_NEG:
            logo.append(fg)
    for sup, a, t, fv in sorted(cand, key=lambda x: -x[0]):
        if any(crl_alike(fv[0], l[0]) >= .97 for l in logo):
            continue
        if sum(1 for fs in N if any(crl_match(x[1], [fv]) for x in fs)) >= CRL_NEG:
            continue   # (in films outside the Criterion Collection too: a studio's logo, a warning, not Criterion's)
        logo.append((fv[0], fv[1], sup, a, t))
        if len(logo) >= 200:
            break
    return logo


def crl_chan(H, ids, logo, neg):
    """The Criterion Channel's own opening (a film from the Channel, not a disc: John, 24 Nov 2026, "Lynch/Oz logo is from the Criterion
    Channel, so that should be noted"): learnt from the films he's said are from it, the first scene of each (from its first picture until the
    picture changes to something else, a distributor's logo, 12 seconds at most), less Criterion's own logo and what films outside have."""
    V = crl_vectors(H, [i for i in ids if (H.get(i) or {}).get('f')])
    N = [x for x in crl_vectors(H, [i for i in neg if (H.get(i) or {}).get('f')]).values()]
    out = []
    for i in ids:
        prev, t0 = None, None
        for t, fv in V.get(i) or []:
            t0 = t if t0 is None else t0
            if t - t0 > 12 or (prev is not None and crl_alike(fv[0], prev) < .5):
                break
            prev = fv[0]
            if crl_match(fv, logo) or any(crl_alike(fv[0], c[0]) >= .97 for c in out):
                continue
            c = (fv[0], fv[1], 0, i, t, CRL_CH)
            if sum(1 for fs in N if any(crl_match(x[1], [c]) for x in fs)) < 1:
                out.append(c)
    return out


def crl_classify(g):
    """Each film of the Criterion Collection looked at: Criterion's logo found at its start, or its name says Criterion, or neither."""
    H = {k: v for k, v in crl_hashes().items() if v.get('v') == CRL_V}   # (those read the old way: not looked at yet, till read again)
    R = load(os.path.join(CRL, 'result.json'), {}) or {}
    films = CRL_STATE.get('films') or {}
    # (Lynch/Oz: from the Criterion Channel, John said: 24 Nov 2026)
    lo_ = os.path.join(CRL, 'lynch-oz-channel.done')
    if '00716627af6057ededf9ddd4710415b9' in R and not os.path.exists(lo_):
        R['00716627af6057ededf9ddd4710415b9'].update(v='channel', how='you said it’s from the Criterion Channel', by='John')
        save(lo_, {'at': int(time.time())})
    pos = [i for i, f in films.items() if f.get('crit') and any(CRL_NAME.search(n or '') for n in crl_names(f.get('path')))]
    neg = [i for i, f in films.items() if not f.get('crit')]
    # (and those John has said are, or aren't: each version of the logo over the years (John, 24 Nov 2026, "The Criterion Collection Logo
    # History") learnt from a film of his that has it)
    yes_ = [i for i, e in R.items() if e.get('by') == 'John' and e.get('v') == 'yes' and i in films]
    neg += [i for i, e in R.items() if e.get('by') == 'John' and e.get('v') == 'no' and i in films and i not in neg]
    chan = [i for i, e in R.items() if e.get('by') == 'John' and e.get('v') == 'channel' and i in films]
    # (his own pictures of the logo, once given, are what's looked for, and the films he's said are: no longer what the films named Criterion
    # share, which may be the Criterion Channel's opening (a "CRITERION" copy of Lynch/Oz has it) or Janus Films' logo)
    mine = sum(1 for x in load(os.path.join(CRL, 'given.json'), []) or [] if not x.get('off'))
    pos = yes_ if mine else pos + [i for i in yes_ if i not in pos]
    key = '%s|%s|%s|%d' % (sorted(i for i in pos if (H.get(i) or {}).get('f')), sorted(i for i in neg if (H.get(i) or {}).get('f')), sorted(i for i in chan if (H.get(i) or {}).get('f')), mine)
    if CRL_STATE.get('logokey') != key:   # (learnt again only when what it's learnt from has changed)
        CRL_STATE['logo'], CRL_STATE['logokey'] = crl_learn(H, pos, neg), key
        CRL_STATE['chan'] = crl_chan(H, chan, CRL_STATE['logo'], neg)
        try:   # (the logo's pictures kept, to be looked at)
            save(os.path.join(CRL, 'logo.json'), [{'pic': base64.b64encode(bytes(max(0, min(255, int(128 + 600 * x))) for x in l[0])).decode(), 'share': l[2],
                                                    'film': (films.get(l[3]) or {}).get('name'), 't': l[4]} for l in CRL_STATE['logo']])
        except Exception:
            pass
    logo = CRL_STATE['logo']
    V = crl_vectors(H, [i for i, f in films.items() if f.get('crit')])
    out = {}
    for i, f in films.items():
        if not f.get('crit'):
            continue
        old = R.get(i) or {}
        e = {'name': f.get('name'), 'year': f.get('year'), 'path': f.get('path')}
        if old.get('by') == 'John':   # (John's own word on it stands)
            e.update(v=old.get('v'), how=old.get('how'), by='John')
        else:
            fr = (H.get(i) or {}).get('f')
            hits = [t for t, fv in (V.get(i) or []) if t <= CRL_SECS and crl_match(fv, logo)]
            ch_ = CRL_STATE.get('chan') or []
            chits = [t for t, fv in (V.get(i) or []) if t <= CRL_SECS and crl_match(fv, ch_)] if ch_ and len(hits) < CRL_HITS else []
            named = any(CRL_NAME.search(n or '') for n in crl_names(f.get('path')))
            if len(hits) >= CRL_HITS:
                e.update(v='yes', how='Criterion’s logo at %d:%02d' % (int(hits[0]) // 60, int(hits[0]) % 60))
            elif len(chits) >= CRL_HITS:
                e.update(v='channel', how='the Criterion Channel’s opening at %d:%02d' % (int(chits[0]) // 60, int(chits[0]) % 60))
            elif named:
                e.update(v='yes', how='its file’s name says Criterion (no logo seen at its start)')
            elif fr is None and i in H:
                e.update(v='unread', how='its start couldn’t be read')
            elif i not in H:
                e.update(v='todo', how='not looked at yet')
            else:
                e.update(v='no', how='no Criterion logo in its first %d seconds' % CRL_SECS)
        out[i] = e
    save(os.path.join(CRL, 'result.json'), out)
    CRL_STATE['logos'], CRL_STATE['known'], CRL_STATE['mine'], CRL_STATE['chans'] = len(logo), len(pos), mine, len(CRL_STATE.get('chan') or [])
    n = {}
    for e in out.values():
        n[e['v']] = n.get(e['v'], 0) + 1
    log('Criterion releases: %d films; Criterion’s logo %s (%d picture%s of it), the Channel’s opening %d; %s' % (
        len(out), ('from %d of your pictures' % mine) if mine else ('learnt from %d named Criterion' % len(pos)), len(logo), '' if len(logo) == 1 else 's',
        len(CRL_STATE.get('chan') or []), ', '.join('%s %d' % (k, v) for k, v in sorted(n.items()))))
    return out


def crl_scan(g):
    """In the background, when nobody's watching: each Criterion Collection film's start looked at once (again only if its file changes),
    and some films outside it, for what isn't Criterion's; the record made again as it goes."""
    time.sleep(180)   # (after the gateway's own start-up work; was 15 minutes, and each update's restart put it back)
    os.makedirs(os.path.join(CRL, 'h'), exist_ok=True)
    # (of his logo history video, only the main logo, the ring that makes the C (2:11 to 2:53 of it, less the words at 2:40): John, 24 Nov
    # 2026, "the main logo starts halfway through so that's the one to learn"; he can choose others on the admin page)
    ml_ = os.path.join(CRL, 'main-logo-1.done')
    if not os.path.exists(ml_):
        try:
            G_ = load(os.path.join(CRL, 'given.json'), []) or []
            for x in G_:
                if 'Logo History' in str(x.get('src') or ''):
                    t_ = float(x.get('t') or 0)
                    x['off'] = not (131.5 <= t_ <= 173.5 and t_ != 160)
            save(os.path.join(CRL, 'given.json'), G_)
            save(ml_, {'at': int(time.time()), 'on': sum(1 for x in G_ if x.get('src') and not x.get('off'))})
            log('Criterion logo: of the logo history video, the main logo’s %d pictures used' % sum(1 for x in G_ if x.get('src') and not x.get('off')))
        except Exception as e:
            log('Criterion logo: %s' % str(e)[:160])
    while True:
        try:
            ff = crl_ffmpeg(g)
            if not ff:
                log('Criterion releases: no ffmpeg found'); return
            sets = (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'BoxSet'}, timeout=180) or {}).get('Items') or []
            crit = next((x for x in sets if (x.get('Name') or '').strip().lower() == 'the criterion collection'), None)
            if not crit:
                time.sleep(3600); continue
            films = {}
            for x in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': crit['Id'], 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path,ProductionYear'}, timeout=180) or {}).get('Items') or []:
                films[x['Id']] = {'name': x.get('Name'), 'year': x.get('ProductionYear'), 'path': x.get('Path') or '', 'crit': True}
            lid = (g.st.get('libraries') or {}).get('movies')
            if lid:   # (and 150 outside it, the same ones each time, for the pictures every film has)
                allm = (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path,ProductionYear'}, timeout=180) or {}).get('Items') or []
                for x in sorted((x for x in allm if x['Id'] not in films), key=lambda x: hashlib.md5(x['Id'].encode()).hexdigest())[:150]:
                    films[x['Id']] = {'name': x.get('Name'), 'year': x.get('ProductionYear'), 'path': x.get('Path') or '', 'crit': False}
            CRL_STATE['films'] = films
            if crl_videos_in(ff):
                CRL_STATE['logokey'] = None
            crl_classify(g)   # (what it knows, at once, and the logo learnt before reading: not after the first hundred)
            H = crl_hashes()
            todo = []
            for i, f in films.items():
                try:
                    sz = os.path.getsize(f['path'])
                except OSError:
                    continue
                if (H.get(i) or {}).get('s') != sz or (H.get(i) or {}).get('v') != CRL_V:   # (those named Criterion first, then those outside: the logo learnt early)
                    named = f['crit'] and any(CRL_NAME.search(n or '') for n in crl_names(f['path']))
                    todo.append((0 if named else 1 if not f['crit'] else 2, i, sz))
            todo.sort()
            first2 = next((t[1] for t in todo if t[0] == 2), None)
            CRL_STATE.update(of=len(films), done=len(films) - len(todo), running=bool(todo))
            n_ = 0
            for _, i, sz in todo:
                while someone_watching(g):
                    CRL_STATE['paused'] = 'someone is watching'
                    time.sleep(60)
                CRL_STATE['paused'] = ''
                fr = crl_frames(ff, films[i]['path'], (CRL_STATE.get('logo') or []) + (CRL_STATE.get('chan') or []))
                H[i] = {'s': sz, 'f': fr, 'at': int(time.time()), 'v': CRL_V}
                save(os.path.join(CRL, 'h', i + '.json'), H[i])   # (at once: a restart loses nothing)
                n_ += 1
                CRL_STATE['done'] = CRL_STATE.get('done', 0) + 1
                if n_ % 20 == 0 and crl_videos_in(ff):   # (a video of the logo put in its folder meanwhile: learnt from now)
                    CRL_STATE['logokey'] = None
                    crl_classify(g)
                if n_ % 100 == 0 or i == first2 or (not CRL_STATE.get('logo') and i == todo[-1][1]):
                    crl_classify(g)   # (every hundred, and once the logo can be learnt, before the rest)
            CRL_STATE['running'] = False
            crl_classify(g)
        except Exception as e:
            # (the media server slow to answer (just after a restart, while it's busy): tried again in ten minutes, not the next morning)
            log('Criterion releases: %s: trying again in ten minutes' % str(e)[:200])
            CRL_STATE['running'] = False
            time.sleep(600)
            continue
        for _ in range(36):   # (films come in: looked at again a few times a day; a video of the logo looked for every ten minutes meanwhile)
            time.sleep(600)
            try:
                ff_ = crl_ffmpeg(g)
                if ff_ and crl_videos_in(ff_) and CRL_STATE.get('films'):
                    CRL_STATE['logokey'] = None
                    crl_classify(g)
            except Exception as e:
                log('Criterion logo video: %s' % str(e)[:160])


def set_covers_now():
    """A Criterion film released only in a box set that gives it no cover of its own (the Frampton films, the W. C. Fields shorts, Scorsese's
    shorts): the set's Criterion cover made its own poster, in its folder and in the media server, so it has it everywhere, not only in The
    Criterion Collection's view (John, 21 Nov 2026, "just use the box set cover"; 24 Nov 2026, "Manual of Arms is part of the Frampton
    collection. Doesn't have right image"). The site's own list says which; the poster before kept in pictures-before."""
    g = G_REF[0] if G_REF else None
    if not g:
        return
    import shutil
    try:
        with urllib.request.urlopen(urllib.request.Request('https://cooperking.github.io/grifflix/data/library.json', headers={'User-Agent': 'Grifflix gateway'}), timeout=120, context=tls()) as r_:
            items = (json.loads(r_.read().decode('utf-8')).get('items') or {})
    except Exception as e:
        log('Box set covers: the site’s list couldn’t be read: %s' % str(e)[:160])
        return
    df = os.path.expanduser('~/Grifflix/set-covers.json')
    done = load(df, {}) or {}
    put = []
    for iid, i in items.items():
        c, sp, im, f = i.get('cset') or [], i.get('csetsp') or [], i.get('csetimg') or [], i.get('csetfimg') or []
        url = next((im[n] for n in range(len(c)) if n < len(im) and im[n] and not (n < len(f) and f[n]) and i.get('spine')
                    and str(sp[n] if n < len(sp) else '') == str(i.get('spine'))), '')
        if not url or done.get(iid) == url:
            continue
        url = re.sub(r'_(small|thumbnail)\.', '_large.', url)
        try:
            it = jf_has(g, iid)
            pth = it.get('Path') or ''
            if not pth or not os.path.exists(pth) or os.path.realpath(os.path.dirname(pth)) in [os.path.realpath(l) for v in g.jf.call('GET', '/Library/VirtualFolders') or [] for l in v.get('Locations') or []]:
                continue   # (not in a folder of its own: a poster there would be every loose film's)
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Grifflix)'}), timeout=60, context=tls()) as r_:
                pic = r_.read()
            if len(pic) < 5000 or pic[:2] != b'\xff\xd8':
                continue
            dest = os.path.join(os.path.dirname(pth), 'poster.jpg')
            if os.path.exists(dest) and os.path.getsize(dest) == len(pic):
                done[iid] = url
                continue
            keep = os.path.join(APP_DIR, 'pictures-before', 'setcover-%s-%d' % (iid, time.time()))
            for o in ('poster.jpg', 'poster.png', 'folder.jpg', 'cover.jpg'):
                if os.path.exists(os.path.join(os.path.dirname(pth), o)):
                    os.makedirs(keep, exist_ok=True)
                    shutil.move(os.path.join(os.path.dirname(pth), o), os.path.join(keep, o))
            with open(dest, 'wb') as f_:
                f_.write(pic)
            g.jf.raw('POST', '/Items/%s/Images/Primary' % iid, base64.b64encode(pic), 'image/jpeg')
            done[iid] = url
            put.append('%s (%s)' % (i.get('name'), c[0] if c else '?'))
            record('box set cover put on', film=i.get('name'), id=iid, set=', '.join(c), picture=url)
        except Exception as e:
            log('Box set cover, %s: %s' % (i.get('name'), str(e)[:160]))
    save(df, done)
    if put:
        log('Box set covers made their films’ posters: %s' % '; '.join(put)[:1500])
        g.forget()
        g.publish_soon = time.time() + 120


def a24_now():
    """A film in A24's own list (a24films.com/films, kept with the site as a24-list.json), put in the A24 collection when it comes in,
    as a Criterion film goes in The Criterion Collection (John, 19 Nov 2026: "Misty Green and Tony should have been added to the A24
    collection correctly but haven't been yet"). Matched by title and year (a year either way). One in A24 CHECKS that A24 lists now is
    moved back to A24: A24 CHECKS holds only those A24 doesn't list (John, 23 Nov 2026, "all films from the list need adding": Tusk, Life
    After Beth and 39 more, put in A24 CHECKS on 19 Nov when A24's list as read then hadn't them)."""
    g = G_REF[0] if G_REF else None
    if not g:
        return
    try:
        with urllib.request.urlopen(urllib.request.Request('https://cooperking.github.io/grifflix/a24-list.json', headers={'User-Agent': 'Grifflix gateway'}), timeout=60, context=tls()) as r_:
            films = json.loads(r_.read().decode('utf-8')).get('films') or []
        sets = g.items(Recursive='true', IncludeItemTypes='BoxSet', Fields='').get('Items', [])
        a24 = next((x for x in sets if (x.get('Name') or '').strip().lower() == 'a24'), None)
        if not a24 or not films:
            return
        held, checks = set(), {}
        chk = next((x for x in sets if (x.get('Name') or '').strip().lower() == 'a24 checks'), None)
        for x in sets:
            if (x.get('Name') or '').strip().lower() in ('a24', 'a24 checks'):
                ids_ = [i['Id'] for i in g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': x['Id'], 'Fields': ''}).get('Items', [])]
                if x is chk:
                    checks = dict.fromkeys(ids_)
                else:
                    held.update(ids_)
        want = {}
        for f in films:
            want.setdefault(fold(f.get('t')), []).append(int(f.get('y') or 0))
        libs = g.st.get('libraries', {})
        if not libs.get('movies'):
            return
        movies = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': libs['movies'], 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'ProductionYear'}).get('Items', [])
        # (its name, or the part after a colon: "Oasis: Supersonic" is A24's Supersonic)
        names_ = lambda m: [fold(m.get('Name'))] + ([fold((m.get('Name') or '').split(':', 1)[1])] if ':' in (m.get('Name') or '') else [])
        add = [m for m in movies if m['Id'] not in held and any(not m.get('ProductionYear') or abs(y - int(m['ProductionYear'])) <= 1 for n_ in names_(m) for y in want.get(n_, []))]
        back = [m for m in add if m['Id'] in checks]
        if back and chk:   # (A24 lists it now: out of A24 CHECKS, into A24)
            g.jf.call('DELETE', '/Collections/%s/Items' % chk['Id'], query={'Ids': ','.join(m['Id'] for m in back)})
            log('A24: back from A24 CHECKS, A24 lists them now: %s' % ', '.join('%s (%s)' % (m.get('Name'), m.get('ProductionYear') or '?') for m in back)[:1500])
        if add:
            g.jf.call('POST', '/Collections/%s/Items' % a24['Id'], query={'Ids': ','.join(m['Id'] for m in add)})
            g.forget()
            g.publish_soon = time.time() + 120
            log('A24, for what came in: put in the A24 collection: %s' % ', '.join('%s (%s)' % (m.get('Name'), m.get('ProductionYear') or '?') for m in add)[:600])
    except Exception as e:
        log('A24, for what came in: %s' % e)


IDS_GIVEN = {   # (a film or series the media server had no details for, its IMDb number given by John)
    '7cf4dda6bea6adc9e3d8d38e10a0e1dd': 'tt2125012',   # (Agnès de ci de là Varda: John, 22 Nov 2026, its IMDb page)
}


def ids_given():
    """Each in IDS_GIVEN given its IMDb number and TMDB's, and its details read again from them (its genres, its overview), once."""
    g = G_REF[0] if G_REF else None
    if not g:
        return
    df = os.path.expanduser('~/Grifflix/ids-given.json')
    done = load(df, {}) or {}
    for iid, imdb in IDS_GIVEN.items():
        if done.get(iid) == imdb:
            continue
        try:
            full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, iid)) or {}
            if not full:
                continue
            f_ = tmdb(g, '/find/' + imdb, external_source='imdb_id') or {}
            hit = (f_.get('tv_results') if full.get('Type') == 'Series' else f_.get('movie_results')) or f_.get('tv_results') or f_.get('movie_results') or []
            pid = dict(full.get('ProviderIds') or {}, Imdb=imdb)
            if hit:
                pid['Tmdb'] = str(hit[0].get('id'))
            full['ProviderIds'] = pid
            g.jf.call('POST', '/Items/%s' % iid, full)
            g.jf.call('POST', '/Items/%s/Refresh' % iid, query={'MetadataRefreshMode': 'FullRefresh', 'ImageRefreshMode': 'Default',
                                                               'ReplaceAllMetadata': 'true', 'ReplaceAllImages': 'false'})
            done[iid] = imdb
            save(df, done)
            log('Its IMDb number given: %s → %s%s' % (full.get('Name'), imdb, ' (TMDB %s)' % pid['Tmdb'] if hit else ''))
            record('identified', film=full.get('Name'), id=iid, imdb=imdb, tmdb=pid.get('Tmdb'), by='John (its IMDb page)')
            g.publish_soon = time.time() + 300
        except Exception as e:
            log('Its IMDb number, %s: %s' % (iid, str(e)[:160]))


POSTERS_TRIED = {}


SMALL_AXE = {1: 'Mangrove', 2: 'Lovers Rock', 3: 'Red, White and Blue', 4: 'Alex Wheatle', 5: 'Education'}


def small_axe_films(g):
    """Once: Small Axe's five episodes made five films in Movies, "Mangrove (2020)/Mangrove (2020).mp4" and so on, as Criterion has them:
    five films, each with its own cover, in its Small Axe box set (John, 22 Nov 2026, "each of the small axe films are treated as film in
    the criterion collection but tv - should they be moved to film folders separately?" ... "yes, go ahead"). Each keeps its details as
    the episode had them (title, year, description, IMDb number), held so the media server doesn't match it to some other film of the
    name: TMDB hasn't most of them as films. Calendars, hearts, lists and where people stopped follow them; the emptied series folder goes
    to Set aside."""
    import shutil
    from xml.sax.saxutils import escape as xe
    mk = os.path.expanduser('~/Grifflix/small-axe-films.done')
    if os.path.exists(mk):
        return
    time.sleep(200)
    try:
        sid = '12c2cbeb5dc8058e843139de045f9e0f'
        ser = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, sid), query={'Fields': 'Path'}) or {}
        eps = (g.jf.call('GET', '/Shows/%s/Episodes' % sid, query={'UserId': g.uid, 'Fields': 'Path,Overview,ProviderIds,DateCreated,PremiereDate'}) or {}).get('Items') or []
        open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
        if ser.get('Name') != 'Small Axe' or not eps:
            log('Small Axe: the series wasn’t found to make into films (done already?)')
            return
        movies = next((l for v in g.jf.call('GET', '/Library/VirtualFolders') or [] if v.get('CollectionType') == 'movies' for l in v.get('Locations') or []), None)
        if not movies or not os.path.isdir(movies):
            log('Small Axe: the Movies folder wasn’t there; left as it is')
            return
        want, done_ = {}, []
        for e in eps:
            t = SMALL_AXE.get(e.get('IndexNumber'))
            src = e.get('Path') or ''
            if not t or e.get('ParentIndexNumber') not in (1, None) or not os.path.exists(src):
                continue
            new = '%s (2020)' % t
            fold = os.path.join(movies, new)
            if os.path.exists(fold):
                log('Small Axe: %s is there already; that episode left as it is' % new)
                continue
            os.makedirs(fold)
            imdb = (e.get('ProviderIds') or {}).get('Imdb') or ''
            with open(os.path.join(fold, 'movie.nfo'), 'w', encoding='utf-8') as f_:   # (its details there before its file, so they're what's read)
                f_.write('<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<movie>\n  <title>%s</title>\n  <year>2020</year>\n'
                         '  <premiered>%s</premiered>\n  <plot>%s</plot>\n  <director>Steve McQueen</director>\n  <imdbid>%s</imdbid>\n'
                         '  <uniqueid type="imdb" default="true">%s</uniqueid>\n</movie>\n'
                         % (xe(t), (e.get('PremiereDate') or '2020-01-01')[:10], xe(e.get('Overview') or ''), xe(imdb), xe(imdb)))
            stem = os.path.splitext(os.path.basename(src))[0]
            d_ = os.path.dirname(src)
            for n in sorted(os.listdir(d_)):   # (the film, and its subtitles and the like beside it, named after it)
                if n.startswith(stem):
                    shutil.move(os.path.join(d_, n), os.path.join(fold, new + n[len(stem):]))
            want[os.path.realpath(os.path.join(fold, new + os.path.splitext(src)[1]))] = e
            done_.append(new)
        if not want:
            return
        log('Small Axe made films, as John said: %s' % ', '.join(done_))
        record('moved', change='Small Axe’s episodes made films in Movies', films=done_, by='John’s say-so', why='Criterion has them as five films in its Small Axe box set')
        VIDS = ('.mp4', '.mkv', '.m4v', '.mov', '.avi', '.ts', '.webm')
        sp = ser.get('Path') or ''
        if sp and os.path.isdir(sp) and not any(n.lower().endswith(VIDS) for _, _, fs in os.walk(sp) for n in fs):
            try:
                os.makedirs(SETASIDE, exist_ok=True)
                shutil.move(sp, os.path.join(SETASIDE, 'Small Axe (2020) (TV, its films now in Movies)'))
            except Exception as e:
                log('  Small Axe: its emptied TV folder stays (%s)' % e)
        try:
            g.jf.call('POST', '/Library/Refresh')
        except Exception:
            pass
        m, got = {}, {}
        for _ in range(40):   # (once the media server has them as films: their details put on and held, everything moved over)
            time.sleep(30)
            r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path'}, timeout=120) or {}
            for x in r.get('Items') or []:
                rp = os.path.realpath(x.get('Path') or '')
                if rp in want and rp not in got:
                    got[rp] = x['Id']
            if len(got) == len(want):
                break
        for rp, nid in got.items():
            e = want[rp]
            try:
                full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, nid)) or {}
                full.update(Name=SMALL_AXE[e['IndexNumber']], ProductionYear=2020, Overview=e.get('Overview') or full.get('Overview'),
                            ProviderIds={'Imdb': (e.get('ProviderIds') or {}).get('Imdb') or ''}, LockData=True)
                if e.get('PremiereDate'):
                    full['PremiereDate'] = e['PremiereDate']
                if e.get('DateCreated'):   # (not shown among the new films)
                    full['DateCreated'] = e['DateCreated']
                g.jf.call('POST', '/Items/%s' % nid, full)
            except Exception as x_:
                log('  Small Axe, %s: its details: %s' % (SMALL_AXE.get(e.get('IndexNumber')), x_))
            m[e['Id']] = nid
            if e.get('IndexNumber') == 1:
                m[sid] = nid   # (the series itself, wherever it was: Mangrove, its first)
        n = g.remap(m) if m else 0
        log('  Small Axe: %d of %d films seen by the media server; calendars, lists, hearts and places followed them (%d changed)' % (len(got), len(want), n))
        covers_now(only={os.path.basename(os.path.dirname(rp)) for rp in want})
        time.sleep(120)
        posters_on(only={os.path.basename(os.path.dirname(rp)) for rp in want})
        g.forget()
        g.publish_soon = time.time() + 180
    except Exception as e:
        log('Small Axe: %s' % e)


def posters_on(only=None):
    """Each film whose folder has its poster (poster.jpg or .png) but the media server shows no picture for it: that poster put on it.
    A Criterion cover put in a film's folder just after Fix match renamed it was never taken up (Totally F***ed Up, John, 22 Nov 2026,
    "hasn't picked up its criterion images"). only: those folders' names; else every film missing its picture."""
    g = G_REF[0] if G_REF else None
    lid = ((g.st.get('libraries') or {}).get('movies')) if g else None
    if not lid:
        return 0
    n, start = 0, 0
    while True:
        r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path',
                                                              'StartIndex': start, 'Limit': 500, 'EnableImages': 'true'}, timeout=120) or {}
        for it in r.get('Items') or []:
            pth = it.get('Path') or ''
            fdir_ = os.path.dirname(pth)
            if not pth or (only is not None and os.path.basename(fdir_) not in only) or (it.get('ImageTags') or {}).get('Primary'):
                continue
            pic = next((os.path.join(fdir_, o) for o in ('poster.jpg', 'poster.png') if os.path.exists(os.path.join(fdir_, o))), None)
            if not pic:
                continue
            # (not over and over when the media server won't take it up: Totally F***ed Up's put on 13 times one day, never shown)
            POSTERS_TRIED[pic] = POSTERS_TRIED.get(pic, 0) + 1
            if POSTERS_TRIED[pic] > 3:
                if POSTERS_TRIED[pic] == 4:
                    log('Its poster, %s: put on 3 times and the media server still shows none; not tried again until the Mac restarts' % it.get('Name'))
                continue
            try:
                with open(pic, 'rb') as f_:
                    g.jf.raw('POST', '/Items/%s/Images/Primary' % it['Id'], base64.b64encode(f_.read()), 'image/png' if pic.endswith('.png') else 'image/jpeg')
                n += 1
                log('Its poster put on: %s' % it.get('Name'))
                record('poster put on', film=it.get('Name'), id=it['Id'], picture=pic, why='its folder had it, the media server showed none')
            except Exception as e:
                log('Its poster, %s: %s' % (it.get('Name'), str(e)[:160]))
        start += 500
        if start >= (r.get('TotalRecordCount') or 0):
            break
    if n:
        g.publish_soon = time.time() + 120
    return n


def covers_now(only=None):
    """(only: the Movies folders of the films just come in: theirs alone looked at, not every cover and folder on the drive: John, 21 Nov 2026,
    "The criterion cover run should be very simple and quick?")
    A film just come in: if it's a Criterion film, everything a Criterion film has, from the start (John, 16 Nov 2026: "a new film - the
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
            env_ = dict(os.environ, GRIFFLIX_ONLY=json.dumps(sorted(only))) if only else None
            r = subprocess.run([sys.executable, '-u', cc, 'go'], capture_output=True, text=True, timeout=3600, cwd=home, env=env_)
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
            if put:   # (and made sure each is on its film in the media server, a minute on, once it has looked)
                folders_ = set(m_.group(1) for m_ in (re.search(r'Movies/([^/]+)/', l) for l in put) if m_)
                threading.Timer(60, lambda: posters_on(folders_)).start()
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
    # (the Mac's hidden "._" companions of a film copied from another drive aren't films: East Is East's kept its folder misnamed, 23 Nov 2026)
    if kind == 'Movie' and any(n.lower().endswith(VIDS) and not n.startswith('.') and os.path.join(folder, n) != path and 'sample' not in n.lower() for n in os.listdir(folder)):
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
                # (a film looked for by its name, not the whole library listed: that timed out every time on 23 Nov 2026, and the 15 folders
                # put right that day had nothing follow them; a series a page at a time)
                if kind == 'Series':
                    xs, st_ = [], 0
                    while True:
                        r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Series,Season,Episode', 'Fields': 'Path', 'StartIndex': st_, 'Limit': 500}, timeout=180) or {}
                        xs += r.get('Items') or []
                        st_ += 500
                        if st_ >= (r.get('TotalRecordCount') or 0) or not r.get('Items'):
                            break
                else:
                    xs = (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path', 'SearchTerm': name, 'Limit': 50}, timeout=90) or {}).get('Items') or []
                for x in xs:
                    rp = os.path.realpath(x.get('Path') or '')
                    if rp in want:
                        found[want[rp]] = x['Id']
                top = found.get(iid)
                if not top:
                    continue
                m = {o: n for o, n in found.items() if o != n}
                if pick:
                    try:   # (its own pictures in its folder (Criterion's covers) kept: apply_match takes only the details then)
                        apply_match(g, top, pick)
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


def loose_to_folders(g, quiet=False):
    """Films put straight into the Movies folder, with no folder of their own, each given one, named "Title (Year)" (the film it's matched
    to, Criterion's year for its films), any files beside it of the same name (its subtitles) moved in with it. Only once a quarter of an
    hour has passed since it last changed (not one still being copied in), and never over a folder already there. John, 23 Nov 2026, "some
    films were added but folders are wrong - can you correct?": Chafed Elbows, No More Excuses and The Joke (1969 ) sat loose."""
    VIDS = ('.mp4', '.mkv', '.m4v', '.mov', '.avi', '.ts', '.wmv', '.mpg', '.mpeg', '.webm')
    roots = [l for v in g.jf.call('GET', '/Library/VirtualFolders') or [] if (v.get('CollectionType') or '') == 'movies' for l in v.get('Locations') or []]
    done = []
    for root in roots:
        try:
            names = [n for n in os.listdir(root) if not n.startswith('.') and n.lower().endswith(VIDS) and os.path.isfile(os.path.join(root, n))]
        except OSError:
            continue
        if not names:
            continue
        byp = {}
        try:
            r_ = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path,ProviderIds,ProductionYear'}, timeout=180) or {}
            byp = {os.path.realpath(x.get('Path') or ''): x for x in r_.get('Items') or []}
        except Exception:
            pass
        for n in names:
            src = os.path.join(root, n)
            try:
                if time.time() - os.path.getmtime(src) < 900:
                    continue
            except OSError:
                continue
            stem, ext = os.path.splitext(n)
            it = byp.get(os.path.realpath(src)) or {}
            tid = str((it.get('ProviderIds') or {}).get('Tmdb') or '')
            if tid and it.get('Name'):
                title, year = it['Name'], crit_year({'Name': it['Name'], 'ProductionYear': it.get('ProductionYear'), 'ProviderIds': {'Tmdb': tid}}) or it.get('ProductionYear')
            else:   # (not matched: its own name, "Title (Year)" or a release's "Title.Year.…")
                m_ = re.match(r'^(.*?)[\s.]*\(\s*((?:19|20)\d\d)\s*\)', stem) or re.match(r'^(.*?)[\s.]+((?:19|20)\d\d)\b', stem)
                title, year = (m_.group(1).replace('.', ' ').strip(), m_.group(2)) if m_ else (stem.strip(), None)
                if not year:   # (no year in its name (Babo 73.mp4, 23 Nov 2026): TMDB's, when only one film has that very title)
                    try:
                        key_ = lambda t: re.sub(r'[^a-z0-9]', '', (t or '').lower())
                        same = [x for x in (tmdb(g, '/search/movie', query=title) or {}).get('results') or []
                                if key_(title) in (key_(x.get('title')), key_(x.get('original_title'))) and (x.get('release_date') or '')[:4]]
                        if len(same) == 1:
                            year = same[0]['release_date'][:4]
                    except Exception:
                        pass
            new = re.sub(r'[/:\\]', '-', '%s (%s)' % (title, year) if year else title).strip(' .')
            dest = os.path.join(root, new)
            if os.path.exists(dest):
                if not quiet:
                    log('Loose in Movies, left as it is: %s (a folder “%s” is there already)' % (n, new))
                continue
            try:
                os.makedirs(dest)
                for side in os.listdir(root):   # (it, and its subtitles and the like named as it is)
                    sp = os.path.join(root, side)
                    if os.path.isfile(sp) and (side == n or (side.startswith(stem + '.') and not side.lower().endswith(VIDS))):
                        os.rename(sp, os.path.join(dest, new + side[len(stem):]))
                done.append('%s → %s/' % (n, new))
            except OSError as e:
                log('Loose in Movies, %s: %s' % (n, e))
    if done:
        log('Films put in folders of their own: %s' % '; '.join(done)[:1500])
        try:
            g.jf.call('POST', '/Library/Refresh')
        except Exception:
            pass
    return done


TO_CHECK = [   # (each film's folder, and why it's in doubt: from the lengths of all 2,512 films against their matches, 23 Nov 2026)
    ("L'Argent (1983)", "the file is another film: the Korean Money (2019), 115 minutes; Bresson's runs 85"),
    ('East in East (1999)', 'the file is East Is East (1999), 97 minutes, taken for East/West (121); the folder is misspelt too'),
    ('Outlaws (2017)', 'the file runs 94 minutes, taken for the Korean The Outlaws (121): another Outlaws?'),
    ('Weekend (2011)', 'the file runs 97 minutes (Andrew Haigh’s?), taken for a Weekend of 121'),
    ("The Men Who Tread on the Tiger's Tail (1952)", 'a second, small copy (0.3 GB) of the one in its 1945 folder: Duplicates, to set aside'),
    ('Una meravigliosa domenica (1948)', 'a second copy of One Wonderful Sunday (1947): Duplicates, to set aside'),
    ('The Heiress (1949)', 'the file runs 40 minutes of 115: only part of it?'),
    ('The Ring (1927)', 'the file runs 52 minutes of 116: only part of it?'),
    ('Rich And Strange (1931)', 'the file runs 54 minutes of 83: only part of it?'),
    ("David Byrne's American Utopia (2020)", 'the file runs 55 minutes of 105: only part of it?'),
    ('The Farmers Wife (1928)', 'the file runs 59 minutes of 112: only part of it?'),
    ('The Joyless Street (1925)', 'the file runs 61 minutes of 155: a short version, or only part?'),
    ('Christ Stopped At Eboli (1979)', 'the file runs 62 minutes; the film runs 150 (220 on TV): one part only?'),
    ('The Brood (1979)', 'the file runs 63 minutes of 92: only part of it?'),
    ('The Inland Sea (1991)', 'the file runs 29 minutes of 56: only part of it?'),
    ('My Voyage to Italy (1999)', 'the file runs 118 minutes of 246: one of its two parts?'),
    ('By Brakhage - An Anthology, Volume One (2003)', 'the file runs 111 minutes of 243: one of its discs?'),
    ('Not a Pretty Picture (1976)', 'the file runs 103 minutes against 83: another film, or another cut?'),
    ('Police Story 2 (1988)', 'the file runs 122 minutes against 105: another cut?'),
    ('Porgy & Bess (1959)', 'the file runs 115 minutes against 138: shortened?'),
    ('Stage Door Canteen (1943)', 'the file runs 112 minutes against 132: shortened?'),
    ('Juno And The Paycock (1930)', 'the file runs 76 minutes against 94: shortened?'),
    ('The First Omen (2024)', 'the file runs 92 minutes against 119: only part of it?'),
]


def silent_once(g):
    """Once: the films waiting for subtitles, turned down or with none found, that are silent films, put in Not needed (Limite, Joyless
    Street, La chambre...)."""
    mk = os.path.expanduser('~/Grifflix/silent-once.done')
    if os.path.exists(mk):
        return
    time.sleep(150)
    try:
        P = load(SUBPREP, {}) or {}
        names = []
        for iid, e in list(P.items()):
            if isinstance(e, dict) and e.get('kind') != 'episode' and e.get('state') in ('waiting', 'nomatch', 'none') and not e.get('givenfile') and silent_film(g, iid):
                e1 = __import__('copy').deepcopy(e)
                e1.update(state='nodialogue', note=SILENT_NOTE, at=int(time.time()))
                chk(e1, 'whisper', 'Not needed: a silent film, no speech to time subtitles by')
                with SP_LOCK:
                    if ((load(SUBPREP, {}) or {}).get(iid) or {}).get('state') == e.get('state'):
                        subprep_commit({iid: e}, {iid: e1})
                        names.append(e.get('name'))
        log('Subtitles: silent films put in Not needed: %s' % (', '.join(str(x) for x in names) or 'none'))
        open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    except Exception as ex:
        log('Subtitles, silent films: %s' % str(ex)[:200])


def to_check_once(g):
    """Once: a To check collection, John's alone, with each film whose file is in doubt (the wrong film, a second copy, a file far shorter
    or longer than its film), its description saying why for each: John, 23 Nov 2026, "can you put ones you doubt in a to check
    collection". Taken out of it, a film stays out."""
    mk = os.path.expanduser('~/Grifflix/to-check-1.done')
    if os.path.exists(mk):
        return
    time.sleep(200)
    import unicodedata
    nf = lambda t: unicodedata.normalize('NFC', t or '')
    try:
        movies = [l for v in g.jf.call('GET', '/Library/VirtualFolders') or [] if (v.get('CollectionType') or '') == 'movies' for l in v.get('Locations') or []]
        items, start = [], 0
        while True:
            r_ = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path', 'StartIndex': start, 'Limit': 300}, timeout=180) or {}
            items += r_.get('Items') or []
            start += 300
            if start >= (r_.get('TotalRecordCount') or 0) or not r_.get('Items'):
                break
        by_folder = {}
        for it in items:
            p_ = it.get('Path') or ''
            for root in movies:
                if p_.startswith(root.rstrip('/') + '/'):
                    by_folder.setdefault(nf(p_[len(root.rstrip('/')) + 1:].split('/')[0]), it)
        ids, lines, missing = [], [], []
        for fo, why in TO_CHECK:
            it = by_folder.get(nf(fo))
            if not it:
                missing.append(fo)
                continue
            ids.append(it['Id'])
            lines.append('%s: %s.' % (it.get('Name') or fo, why))
        sets = (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'BoxSet'}) or {}).get('Items') or []
        box = next((x for x in sets if (x.get('Name') or '').strip().lower() == 'to check'), None)
        if box:
            g.jf.call('POST', '/Collections/%s/Items' % box['Id'], query={'Ids': ','.join(ids)})
            cid = box['Id']
        else:
            cid = ((g.jf.call('POST', '/Collections', query={'Name': 'To check', 'IsLocked': 'true', 'Ids': ','.join(ids)}) or {}).get('Id') or '')
        if cid:
            full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, cid)) or {}
            full['Overview'] = ('Films whose file is in doubt, only you see this: the wrong film, a second copy, or a file far shorter or longer than '
                                'its film. Take each out once it’s put right.\n\n' + '\n'.join(lines))[:4000]
            full['LockedFields'] = sorted(set((full.get('LockedFields') or []) + ['Overview']))
            g.jf.call('POST', '/Items/%s' % cid, full)
        log('To check (only John sees it): %d films put in%s' % (len(ids), '; not found: ' + ', '.join(missing) if missing else ''))
        g.forget()
        g.publish_soon = time.time() + 120
        open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    except Exception as e:
        log('To check: %s' % str(e)[:200])


def folders_fix2_once(g):
    """Once: The Most Beautiful's folder, named "Akira Kurosawa - The Most Beautiful (1944)", and Babo 73's, given a folder without its year
    (it came in loose, before the media server had it), renamed to the film, through rename_to, so all that's kept for it follows (John,
    23 Nov 2026, "A folder is called Akira Kurosawa - The Most Beautiful (1944) for some reason. Babo 73 has been added but needs putting in
    the right folder"). Not while the converter is at either."""
    mk = os.path.expanduser('~/Grifflix/folders-fix-2.done')
    if os.path.exists(mk):
        return
    time.sleep(420)
    import unicodedata
    nf = lambda t: unicodedata.normalize('NFC', t or '')
    said = []
    for folder, term, name, year in (('Akira Kurosawa - The Most Beautiful (1944)', 'Beautiful', 'The Most Beautiful', 1944), ('Babo 73', 'Babo', 'Babo 73', 1964)):
        try:
            xs = (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path', 'SearchTerm': term, 'Limit': 50}, timeout=90) or {}).get('Items') or []
            x = next((x for x in xs if nf(os.path.basename(os.path.dirname(x.get('Path') or ''))) == nf(folder)), None)
            if not x:
                said.append('%s: not found' % folder)
                continue
            for _ in range(240):   # (the converter at it: waited for, up to two hours)
                cur = load(os.path.expanduser('~/Grifflix/convert-now.json'), None) or {}
                if not (cur.get('id') == x['Id'] and cur.get('state') in ('converting', 'paused') and conv_alive(cur)):
                    break
                time.sleep(30)
            said.append('%s → %s (%s): %s' % (folder, name, year, rename_to(g, x['Id'], name, year)))
        except Exception as e:
            said.append('%s: %s' % (folder, str(e)[:160]))
    log('Folders put right: ' + '; '.join(said))
    open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))


def jf_has(g, iid, fields='Path'):
    """A film or episode by its id, {} if the media server no longer has it: asked for by id it answers 500, "Error processing request",
    for one it hasn't (Totally F***ed Up's id from before its folder was renamed, 24 Nov 2026), so it's asked for in a list of one."""
    r_ = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Ids': iid, 'Fields': fields}) or {}
    return (r_.get('Items') or [{}])[0]


def subprep_gone_once(g):
    """Once: entries on the subtitles list for films no longer under that id (their folder renamed since, the film a new item with its own
    entry), taken off: they showed under Not found (John, 24 Nov 2026: Totally F***ed Up, renamed on 22 Nov)."""
    mk = os.path.expanduser('~/Grifflix/subprep-gone-2.done')   # (again: the first looked by id, which the media server answers with an error)
    if os.path.exists(mk):
        return
    time.sleep(240)
    try:
        P = load(SUBPREP, {}) or {}
        off = []
        for iid, e in list(P.items()):
            if iid.startswith('_') or not isinstance(e, dict) or e.get('state') not in ('gone', 'none', 'nomatch', 'failed', 'waiting', 'check'):
                continue
            try:
                it = jf_has(g, iid)
            except Exception:
                continue
            if not it.get('Id'):
                off.append('%s (%s)' % (e.get('name'), e.get('state')))
                P.pop(iid, None)
        if off:
            save(SUBPREP, P)
        log('Subtitles: %d on the list for a film no longer there under its id, taken off%s' % (len(off), (': ' + ', '.join(off))[:800] if off else ''))
        open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    except Exception as ex:
        log('Subtitles, films no longer there: %s' % str(ex)[:200])


def nineteen84_once(g):
    """Once: the film in "1984 (1984)", Michael Radford's (Criterion's 1984), matched to Class of 1984 (1982): matched to TMDB's Nineteen
    Eighty-Four, and named 1984 as Criterion and its folder name it (John, 24 Nov 2026, "1984 is called class of 1984 for some reason")."""
    mk = os.path.expanduser('~/Grifflix/nineteen84.done')
    if os.path.exists(mk):
        return
    time.sleep(360)
    try:
        xs = (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path', 'SearchTerm': '1984', 'Limit': 100}, timeout=90) or {}).get('Items') or []
        x = next((x for x in xs if os.path.basename(os.path.dirname(x.get('Path') or '')) == '1984 (1984)'), None)
        if not x:
            log('1984: its folder “1984 (1984)” isn’t in the library')
            open(mk, 'w').write('not found')
            return
        hit = next((r for r in (tmdb(g, '/search/movie', query='Nineteen Eighty-Four', year='1984') or {}).get('results') or []
                    if (r.get('release_date') or '').startswith('1984') and fold(r.get('title')) in (fold('Nineteen Eighty-Four'), fold('1984'))), None)
        if not hit:
            log('1984: TMDB didn’t find Nineteen Eighty-Four (1984): left for now')
            return
        apply_match(g, x['Id'], {'Name': '1984', 'ProductionYear': 1984, 'ProviderIds': {'Tmdb': str(hit['id'])}, 'SearchProviderName': 'TheMovieDb'})
        time.sleep(20)
        full = g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, x['Id'])) or {}
        if full.get('Name') != '1984':
            full['Name'] = '1984'
            full['LockedFields'] = sorted(set((full.get('LockedFields') or []) + ['Name']))
            g.jf.call('POST', '/Items/%s' % x['Id'], full)
        log('1984: matched to TMDB %s (Nineteen Eighty-Four, 1984), named 1984' % hit['id'])
        record('rematched', film='1984', id=x['Id'], tmdb=str(hit['id']), was='Class of 1984 (1982)', by='John (24 Nov 2026)')
        g.forget()
        g.publish_soon = time.time() + 300
        open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    except Exception as e:
        log('1984: %s' % str(e)[:200])


def east_is_east_once(g):
    """Once: the film in "East in East (1999)", matched to East/West (Régis Wargnier's), which is East Is East (1999), its file says so: its
    match put right, its folder named for it, and out of To check (John, 23 Nov 2026, "East/ West should be East is East")."""
    mk = os.path.expanduser('~/Grifflix/east-is-east-2.done')   # (again: its folder kept its name the first time, a hidden "._" file taken for another film)
    if os.path.exists(mk):
        return
    time.sleep(480)
    try:
        xs = (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path', 'SearchTerm': 'East', 'Limit': 100}, timeout=90) or {}).get('Items') or []
        x = next((x for x in xs if os.path.basename(os.path.dirname(x.get('Path') or '')) == 'East in East (1999)'), None)
        if not x:
            log('East Is East: its folder “East in East (1999)” isn’t in the library')
            open(mk, 'w').write('not found')
            return
        hit = next((r for r in (tmdb(g, '/search/movie', query='East Is East', year='1999') or {}).get('results') or []
                    if fold(r.get('title')) == fold('East Is East') and (r.get('release_date') or '').startswith('1999')), None)
        if not hit:
            log('East Is East: TMDB didn’t find East Is East (1999): left for now')
            return
        pick = {'Name': hit['title'], 'ProductionYear': 1999, 'ProviderIds': {'Tmdb': str(hit['id'])}, 'SearchProviderName': 'TheMovieDb'}
        box = next((b for b in (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'BoxSet'}) or {}).get('Items') or []
                    if (b.get('Name') or '').strip().lower() == 'to check'), None)
        if box:   # (out of To check first: rename_to puts it back in the collections it's in)
            g.jf.call('DELETE', '/Collections/%s/Items' % box['Id'], query={'Ids': x['Id']})
        if str(((g.jf.call('GET', '/Users/%s/Items/%s' % (g.uid, x['Id'])) or {}).get('ProviderIds') or {}).get('Tmdb') or '') != str(hit['id']):
            apply_match(g, x['Id'], pick)
            time.sleep(20)
        said = rename_to(g, x['Id'], hit['title'], 1999, pick)
        log('East Is East: matched (TMDB %s) and its folder %s; out of To check' % (hit['id'], said or 'already right'))
        g.forget()
        g.publish_soon = time.time() + 300
        open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    except Exception as e:
        log('East Is East: %s' % str(e)[:200])


def renames_follow_once(g):
    """Once: the 15 folders put right on 23 Nov 2026, whose following timed out (the media server's whole library asked for at once):
    each found under its new name, its match put on again (Criterion's years kept: The Men Who Tread on the Tiger's Tail (1945) is TMDB's
    1952 film), and calendars, hearts, lists, places and its subtitles moved from its old id."""
    mk = os.path.expanduser('~/Grifflix/renames-follow-1.done')
    if os.path.exists(mk):
        return
    time.sleep(300)
    R = [["e9348c6ce3b57fc717bd8ba4568fae1d", "The Men Who Tread on the Tiger's Tail (1945)", "36916"], ["1fcd85f99539e639f656878921941125", "Sanshiro Sugata (1943)", "35022"],
         ["52f5ac7c2dabe5e342febf5f930f3dd4", "Capricious Summer (1968)", "47189"], ["a39c2880ed82daaa57420fa4507620a3", "Wings (1966)", "50013"],
         ["60a000be80dc1ce1f5393cea66844c22", "Viridiana (1961)", "4497"], ["a81c49f0f8739977f47381f6b49690c7", "Prisoners of the Land (1939)", "335367"],
         ["82e413928ef659d463bbca142ef7744d", "Muna Moto (1975)", "257803"], ["de2fc1e7bf9704fea619aca4f968fdc8", "Fårö Document (1970)", "304081"],
         ["f80207400f816990952fae96a73e90a2", "The Golden Coach (1953)", "56167"], ["18c2dfd6c3703e66291708bc24a284bf", "Dry Summer (1964)", "58897"],
         ["0bf773a3e78e069556eaa979f4662734", "Europa '51 (1952)", "41464"], ["5aca41f0b1f230407078364aa78eec03", "Pigsty (1969)", "48688"],
         ["79f8aaf4aee707a6b82584fa5f0fc08a", "Love Meetings (1964)", "42733"], ["7ec901f6ff3c1c044dce735496e0540d", "The Gospel According to St. Matthew (1964)", "24192"],
         ["9aae15ea1f4c0f1a7da6a549ba28773c", "Uncle Yanco (1968)", "277677"]]
    import unicodedata
    nf = lambda t: unicodedata.normalize('NFC', t or '')
    m, said = {}, []
    for old, folder, tid in R:
        title = re.sub(r'\s*\(\d{4}\)$', '', folder)
        try:
            xs = (g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path,ProductionYear', 'SearchTerm': title, 'Limit': 50}, timeout=90) or {}).get('Items') or []
            x = next((x for x in xs if nf(os.path.basename(os.path.dirname(x.get('Path') or ''))) == nf(folder)), None)
            if not x:
                said.append('%s: not found under its new name yet' % folder)
                continue
            d_ = tmdb(g, '/movie/%s' % tid) or {}
            apply_match(g, x['Id'], {'Name': d_.get('title') or title, 'ProductionYear': int((d_.get('release_date') or '0')[:4] or 0) or None,
                                     'ProviderIds': {'Tmdb': tid}, 'SearchProviderName': 'TheMovieDb'})
            if x['Id'] != old:
                m[old] = x['Id']
            said.append(folder)
            time.sleep(5)
        except Exception as e:
            said.append('%s: %s' % (folder, str(e)[:100]))
    n = g.remap(m) if m else 0
    log('The folders put right on 23 Nov followed: %s (%d calendars, hearts and places moved)' % ('; '.join(said)[:2500], n))
    g.publish_soon = time.time() + 300
    if not any('not found' in x for x in said):
        open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))


def folders_fix_once(g):
    """Once: the folders of films come in on 21-23 Nov named otherwise than the film they are (their own language's title, a Criterion film
    without Criterion's year, one with no year) renamed to it, each through rename_to, so its files, collections, calendars, hearts and
    places follow it, its subtitles too. John, 23 Nov 2026, "some films were added but folders are wrong - can you correct?". Left as they
    are: Totally Fucked Up (named so on purpose), Ivan the Terrible, Part II (Criterion's name), and those whose folder has Criterion's own
    title (Le Jour Se Leve, Redes, A Report on the Party and Guests)."""
    mk = os.path.expanduser('~/Grifflix/folders-fix-1.done')
    if os.path.exists(mk):
        return
    time.sleep(240)
    FIX = {"The Men Who Tread on Tiger's Tails": ("The Men Who Tread on the Tiger's Tail", '36916'),
           'Sugata Sanshiro (1943)': None, 'Rozmarné léto (1967)': None, 'Krylya (1966)': None, 'Viridiana (1962)': None,
           'Prisioneros de la tierra (1939)': None, 'The Child Of Another (1975)': None, 'Faro Document (1970)': None,
           'The Golden Coach (1952)': None, 'Dry Summer (1963)': None, "Europe '51 (1952)": None, 'Porcile (1969)': None,
           'Love Meetings (1965)': None, 'Il vangelo secondo Matteo (1965)': None, 'Uncle Yanco (1967)': None}
    import unicodedata
    nf = lambda t: unicodedata.normalize('NFC', t or '')
    said = []
    try:
        loose_to_folders(g)
        movies = [l for v in g.jf.call('GET', '/Library/VirtualFolders') or [] if (v.get('CollectionType') or '') == 'movies' for l in v.get('Locations') or []]
        items, start = [], 0
        while True:
            r_ = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path,ProviderIds,ProductionYear', 'StartIndex': start, 'Limit': 300}, timeout=180) or {}
            items += r_.get('Items') or []
            start += 300
            if start >= (r_.get('TotalRecordCount') or 0) or not r_.get('Items'):
                break
        by_folder = {}
        for it in items:
            p_ = it.get('Path') or ''
            for root in movies:
                if p_.startswith(root.rstrip('/') + '/'):
                    by_folder.setdefault(nf(p_[len(root.rstrip('/')) + 1:].split('/')[0]), it)
        for fo, over in FIX.items():
            it = by_folder.get(nf(fo))
            if not it:
                said.append('%s: not found' % fo)
                continue
            tid = over[1] if over else str((it.get('ProviderIds') or {}).get('Tmdb') or '')
            name = over[0] if over else it.get('Name')
            if not tid or not name:
                said.append('%s: not matched, left' % fo)
                continue
            pick = {'Name': name, 'ProductionYear': it.get('ProductionYear'), 'ProviderIds': {'Tmdb': tid}, 'SearchProviderName': 'TheMovieDb'}
            if over:   # (its year from TMDB, for one not matched yet)
                d_ = tmdb(g, '/movie/%s' % tid) or {}
                pick['ProductionYear'] = int((d_.get('release_date') or '0')[:4] or 0) or None
                try:
                    apply_match(g, it['Id'], pick)
                except Exception as e:
                    log('  %s: its match: %s' % (fo, e))
            year = crit_year(pick) or pick.get('ProductionYear')
            try:
                said.append('%s: %s' % (fo, rename_to(g, it['Id'], name, year, pick) or 'already right'))
            except Exception as e:
                said.append('%s: %s' % (fo, str(e)[:120]))
            time.sleep(90)   # (one at a time: each followed by the media server before the next)
        log('Folders put right (John asked, 23 Nov): %s' % '; '.join(said)[:3000])
    except Exception as e:
        log('Folders put right: %s' % str(e)[:200])
    open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))


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
                    while someone_watching(g) or ACT_RUNNING or (MACPLAN.get('t') and MACPLAN['t'].is_alive()):   # (nor while a film's being planned for John's Mac)   # (nor while something John allowed is being done: a restart 19 s after
                        time.sleep(60)                             # his Allow stopped the disk tidy halfway, 19 Nov 2026)
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
    # (a site update already due kept across a restart (the Mac's own updates restart it): two of John's Allows on 18 Nov 2026 were
    # never shown, each update due forgotten when the Mac updated itself minutes later ("By Brakhage still not showing up properly"))
    pub_f = os.path.expanduser('~/Grifflix/site-update-due.json')
    publish_at, matched_at = float(load(pub_f, 0) or 0), time.time() - 6 * 3600 + 600   # (films put right by their folders: 10 minutes after starting, then every 6 hours)
    if publish_at:
        publish_at = max(publish_at, time.time() + 120)
    elif not os.path.exists(pub_f):   # (the first start with this: one update, for what was due and lost)
        publish_at = time.time() + 300
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
        try:   # (a film put straight into the Movies folder given a folder of its own: John, 23 Nov 2026)
            loose_to_folders(g, quiet=True)
        except Exception as e:
            log('Loose in Movies: %s' % e)
        try:
            roots = [l for v in g.jf.call('GET', '/Library/VirtualFolders') or [] if (v.get('CollectionType') or '') in ('movies', 'tvshows')
                     for l in v.get('Locations') or []]
            # (every film and episode file, not only the folders at the top: a new episode in a series already there, or a film arriving in
            # a folder made earlier, by a download that failed, was never noticed: John, 15 Nov 2026, Ghosts "not even in the queue")
            VID = ('.mp4', '.mkv', '.m4v', '.mov', '.avi', '.ts', '.wmv', '.mpg', '.mpeg', '.webm')
            # (each folder read again only when its date has changed, not the whole drive every time: a folder's date changes when anything
            # is added to it, so a film in a folder already there and a new episode in a season are still found: John, 21 Nov 2026, "Why does
            # it need to check every folder on the g-drive")
            now_ = sorted(x for r in roots if os.path.isdir(r) for x in files_under(r, VID))
            before = load(seen_at, None)
            if before is not None:
                new = [x for x in now_ if x not in set(before)]
                gone = [x for x in before if x not in set(now_)]
                if gone and not new:
                    log('Gone from the library (%d): %s' % (len(gone), ', '.join(os.path.basename(x) for x in gone[:5])))
                    publish_at = publish_at or time.time() + 300
                if new:
                    jf_look(g, new)
                    log('New in the library (%d): %s: the media server asked to look' % (len(new), ', '.join(os.path.basename(x) for x in new[:5]) + (' …' if len(new) > 5 else '')))
                    CONV_NOW.set()   # (the converter started for them, if it isn't running)
                    # (a Criterion film among them: its cover, spine and place in The Criterion Collection, from the list John last gave, once
                    # the media server has it; however it came in, not only by download: John, 16 Nov 2026, "I shouldn't have to reupload the
                    # criterion html every time to check if a new film is criterion")
                    new_follow_up(new)   # (its cover, A24 (John, 19 Nov 2026), its distributor (20 Nov 2026): once films stop coming in)
                    # (an extra put in a film's Other or Extras folder: the Criterion sheet's Extras column marked: John, 17 Nov 2026, "the job
                    # should run when extras are added")
                    XD = ('other', 'others', 'extras', 'extra', 'featurettes', 'behind the scenes', 'deleted scenes', 'interviews', 'trailers', 'shorts', 'scenes', 'bonus')
                    if any(seg.strip().lower() in XD for x in new for seg in x.split('/')[:-1]):
                        threading.Timer(300, sheet_now).start()
                    # (the site updated once the media server has read them, about 10 minutes on, but not put back by each film after: films
                    # came every few minutes on 21 Nov 2026 and the update, put back each time, never ran from 10:47; those after it, the next)
                    publish_at = publish_at or time.time() + 540
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
            if float(load(pub_f, 0) or 0) != publish_at:
                save(pub_f, publish_at)
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
            # (a converter from before the Mac mini only repackaged, making a film's picture again itself (hours here): stopped, its half-made
            # file cleared, and started again below, the new one handing the film to John's Mac: John, 23 Nov 2026, "the mac mini shouldn't be
            # taking on conversion jobs like this", Rozmarné léto, 4K made 1080p, 23% after nine hours)
            if alive and not cur.get('remuxonly') and cur.get('state') == 'converting' and 'go' in [str(a) for a in cur.get('args') or []] \
                    and re.match(r'(bigger than 1080p|format \(|to make smaller)', str(cur.get('why') or '')):
                pid_ = conv_alive(cur)
                os.kill(pid_, 15)
                for _ in range(60):
                    time.sleep(1)
                    if not conv_alive(cur):
                        break
                log('The converter: %s, being converted on the Mac mini itself (%s, %s%%), stopped: the Mac mini only repackages now, and the '
                    'new converter hands it to John’s Mac' % (cur.get('name'), cur.get('why'), cur.get('pct', 0)))
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
    if SUBS:
        SUBS.SUBDL['key'] = (g.st.get('subdl') or {}).get('key') or ''   # (Subdl, a second place to look for subtitles, with John's key)
        SUBS.SUBDL['log'] = log
    G_REF.append(g)   # (for what runs on its own: covers_now's synopses)
    # (looks for duplicates left running by the gateway before this one (they only read the drive): stopped, so they don't pile up and starve
    # the media server; nine at once on 21 Nov 2026, load 85, and the site couldn't update)
    try:
        import subprocess, signal
        for line_ in subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=20).stdout.splitlines():
            pid_, _, cmd_ = line_.strip().partition(' ')
            if 'duplicates.py json' in cmd_ and pid_.isdigit() and int(pid_) != os.getpid():
                os.kill(int(pid_), signal.SIGTERM)
                log('Stopped a look for duplicates left running (%s)' % pid_)
    except Exception as e:
        log('Looking for duplicates left running:', str(e)[:120])
    port = int(g.st.get('port', 8097))
    srv = ThreadingHTTPServer(('127.0.0.1', port), make_handler(g))
    try:   # (a film being planned for John's Mac when the gateway stopped: back at the front of its queue)
        px_ = load(MACQ + '.planning', None)
        if px_ and px_.get('id'):
            Q_ = load(MACQ, []) or []
            if not any(x_.get('id') == px_['id'] for x_ in Q_):
                save(MACQ, [px_] + Q_)
                log('Queue for John’s Mac: %s back at the front (the gateway stopped while it was being planned)' % (px_.get('name') or px_['id']))
            os.remove(MACQ + '.planning')
    except Exception as e:
        log('Queue for John’s Mac:', str(e)[:200])
    try:   # (only once the port is this gateway's: an old one still running may be doing what John allowed)
        acts_interrupted()
    except Exception as e:
        log('Interrupted asks:', str(e)[:200])
    try:   # (the home network's door, one port up: only John's Mac's films, with its key; a film over the home network, not out and back)
        lan = ThreadingHTTPServer(('0.0.0.0', port + 1), make_handler(g))
        lan.lan = True
        lan.daemon_threads = True
        threading.Thread(target=lan.serve_forever, daemon=True).start()
    except OSError as e:
        log('The home network door (port %d) didn’t open:' % (port + 1), e)
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
    threading.Thread(target=covers_once, daemon=True).start()
    threading.Timer(420, bom_now).start()   # (any film come in while it was off: its distributor, or streaming service: 20 Nov 2026)
    threading.Timer(200, a24_now).start()
    threading.Timer(420, set_covers_now).start()
    threading.Timer(300, crit_missing_now).start()   # (the Criterion films missing, kept ready: 24 Nov 2026)
    threading.Thread(target=crl_scan, args=(g,), daemon=True).start()   # (which Criterion films are Criterion's own release: 24 Nov 2026)   # (and the box set covers on the films released only in a set: 24 Nov 2026)   # (and once each start: any A24 film come in before this, Tony and Misty Green, put in)   # (the Mac's own files kept up to date by themselves: John, 17 Nov 2026)
    threading.Thread(target=fanny_series, args=(g,), daemon=True).start()
    threading.Thread(target=keep_disk_health, daemon=True).start()
    threading.Thread(target=keep_backup, args=(g,), daemon=True).start()
    threading.Thread(target=keep_plex_off, args=(g,), daemon=True).start()
    threading.Thread(target=keep_extras, args=(g,), daemon=True).start()
    threading.Thread(target=keep_covers, daemon=True).start()   # (every Criterion film its cover, hourly)
    # (the subtitles list looked over a minute after starting, not first in the three-hourly round behind the covers: John, 21 Nov 2026,
    # "there shouldn't be a need for a three hour check")
    threading.Thread(target=lambda: (time.sleep(60), subprep_scan(), subs_exact_all(), subs_namecheck()), daemon=True).start()
    threading.Thread(target=lambda: (time.sleep(240), posters_on()), daemon=True).start()   # (any film whose poster's in its folder but not on it)
    threading.Thread(target=lambda: (time.sleep(90), ids_given()), daemon=True).start()
    mk2_ = os.path.expanduser('~/Grifflix/tv-criterion-3.done')
    if not os.path.exists(mk2_):   # (once: the site updated, for the TV series now in Criterion's box sets: 22 Nov 2026)
        try:
            open(mk2_, 'w').write(str(int(time.time())))
            g.publish_soon = time.time() + 300
        except OSError:
            pass
    def tfu_rename():
        """Once: Totally F***ed Up's folder and file renamed Totally Fucked Up (1993), the media server never taking up a cover in a folder
        whose name has asterisks in it (it had it, listed among its pictures, but never gave it the film: John, 22 Nov 2026, "yes, rename it
        to Totally Fucked Up"). By Fix match's own renaming, so calendars, hearts, lists and its box set follow it; its title stays."""
        mk = os.path.expanduser('~/Grifflix/tfu-rename.done')
        if os.path.exists(mk):
            return
        time.sleep(150)
        lid = (g.st.get('libraries') or {}).get('movies')
        r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path,ProviderIds',
                                                              'SearchTerm': 'Totally'}, timeout=120) or {}
        it = next((x for x in r.get('Items') or [] if '*' in os.path.dirname(x.get('Path') or '') and (x.get('ProviderIds') or {}).get('Imdb') == 'tt0108366'), None)
        open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
        if not it:
            log('Totally F***ed Up: its folder wasn’t found to rename (already renamed?)')
            return
        pick = {'Name': it.get('Name') or 'Totally F***ed Up', 'ProductionYear': it.get('ProductionYear') or 1994, 'ProviderIds': it.get('ProviderIds'), 'SearchProviderName': 'TheMovieDb'}
        try:   # (no match given to the renaming, which would put it on replacing every picture: Apply as Fix match does, its cover kept)
            did = rename_to(g, it['Id'], 'Totally Fucked Up', 1993)
        except Exception as e:
            did = 'not renamed (%s)' % e
        log('Totally F***ed Up, renamed as John said: %s' % (did or 'done'))
        record('renamed', film='Totally F***ed Up', to='Totally Fucked Up (1993)', why='the media server never took up a cover in a folder named with asterisks', by='John’s say-so', result=did)
        new = os.path.join(os.path.dirname(os.path.dirname(it['Path'])), 'Totally Fucked Up (1993)')
        for _ in range(40):   # (once the media server has it at its new place: its match put on again, its cover on it)
            time.sleep(30)
            r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path', 'SearchTerm': 'Totally'}, timeout=120) or {}
            x = next((y for y in r.get('Items') or [] if os.path.dirname(y.get('Path') or '') == new), None)
            if x:
                try:
                    apply_match(g, x['Id'], pick)
                except Exception as e:
                    log('  Totally Fucked Up: its match: %s' % e)
                time.sleep(60)
                log('  Totally Fucked Up: its cover: %s' % ('on' if posters_on(only={'Totally Fucked Up (1993)'}) else 'on already, or none in its folder'))
                return
    threading.Thread(target=tfu_rename, daemon=True).start()
    try:   # (translations that failed for a language code Argos didn't know then ("ea": Spanish), tried again now)
        with SP_LOCK:
            P_ = load(SUBPREP, {}) or {}
            n_ = 0
            for k_, e_ in P_.items():
                if isinstance(e_, dict) and e_.get('tr_tried') and str(e_.get('ref_lang') or '').lower()[:2] in ('ea', 'sp', 'pm', 'zt', 'me', 'sy'):
                    e_.pop('tr_tried', None); e_.pop('tr_given', None); n_ += 1
            if n_:
                save(SUBPREP, P_)
    except Exception:
        pass
    threading.Thread(target=keep_speech_first, args=(g,), daemon=True).start()
    threading.Thread(target=subs_by_timing_again, args=(g,), daemon=True).start()
    threading.Thread(target=ep_fill_all, args=(g,), daemon=True).start()
    threading.Thread(target=folders_fix_once, args=(g,), daemon=True).start()
    threading.Thread(target=folders_fix2_once, args=(g,), daemon=True).start()
    threading.Thread(target=east_is_east_once, args=(g,), daemon=True).start()
    threading.Thread(target=nineteen84_once, args=(g,), daemon=True).start()   # (once: 1984, matched to Class of 1984: 24 Nov 2026)
    threading.Thread(target=subprep_gone_once, args=(g,), daemon=True).start()   # (once: subtitle entries for films renamed since, taken off: 24 Nov 2026)
    try:   # (once: La Bola Negra, made 2 h 30 of 2 h 39 on John's Mac on 23 Nov and turned down, made again: 24 Nov 2026)
        mk_ = os.path.expanduser('~/Grifflix/labolanegra-again.done')
        if not os.path.exists(mk_):
            j_ = next((j for j in sorted(CONVJOBS.values(), key=lambda j: -j.get('at', 0)) if j.get('id') == '99909cfc3934c33ceed2b0ca75afba83'), None)
            if j_ and j_.get('state') == 'failed':
                short_again(j_, 9001, 9562)
            open(mk_, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    except Exception as e:
        log('La Bola Negra again: %s' % e)   # (once: East/West made East Is East, 23 Nov 2026)   # (once: The Most Beautiful's and Babo 73's folders, 23 Nov 2026)   # (once: the folders John asked to have put right, 23 Nov 2026)
    threading.Thread(target=renames_follow_once, args=(g,), daemon=True).start()   # (and those followed, as they weren't then)
    threading.Thread(target=to_check_once, args=(g,), daemon=True).start()
    threading.Thread(target=silent_once, args=(g,), daemon=True).start()   # (once: silent films put in Not needed)   # (once: the films in doubt in a To check collection, John's alone)   # (each episode's series' IMDb number and own title, for the searches)   # (once: those turned down by their words, looked at again by when their lines are said)
    threading.Thread(target=keep_file_checks, args=(g,), daemon=True).start()   # (each waiting film's files checked here as soon as it's waiting)   # (other subtitles timed by when people speak, before Whisper)
    try:   # (films already turned down where Whisper heard next to no speech anywhere (Robot Dreams): little or no dialogue, not tried again)
        P_ = load(SUBPREP, {}) or {}
        ch_ = 0
        for k_, e_ in P_.items():
            if isinstance(e_, dict) and e_.get('state') == 'nomatch' and e_.get('cands'):
                ss_ = [p_ for c_ in e_['cands'] for p_ in (c_.get('samples') or [])]
                if not ss_ or not all(isinstance(p_, list) and len(p_) > 3 and p_[3] is not None for p_ in ss_):   # (only where how much was heard is known)
                    continue
                h_ = [sum(int(p_[3]) for p_ in (c_.get('samples') or [])) for c_ in e_['cands']]
                if max(h_) <= 4:
                    e_.update(state='nodialogue', note='Whisper heard little or no speech in the four parts listened to (%d lines in all): little or no dialogue' % max(h_))
                    ch_ += 1
                    log('Subtitles for %s: little or no dialogue: none needed' % e_.get('name'))
        if ch_:
            save(SUBPREP, P_)
    except Exception as e:
        log('Subtitles, films with no dialogue: %s' % e)
    try:   # (once: those turned down tried again, the subtitles of the film's own length first now (Andrei Rublev): each file kept, so no downloads spent again)
        mk_l = os.path.expanduser('~/Grifflix/subs-lenfit.done')
        if not os.path.exists(mk_l):
            P_ = load(SUBPREP, {}) or {}
            n_l = 0
            for k_, e_ in P_.items():
                if isinstance(e_, dict) and e_.get('state') == 'nomatch':
                    e_.update(state='waiting', note='to be tried again: the subtitles of its own length first')
                    n_l += 1
            save(SUBPREP, P_)
            open(mk_l, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
            log('Subtitles: %d turned down to be tried again, those of the film’s own length first' % n_l)
    except Exception as e:
        log('Subtitles, tried again by length: %s' % e)

    try:   # (once: those with no English subtitles anywhere tried again, now that one in another language that fits can be translated)
        mk_f = os.path.expanduser('~/Grifflix/subs-foreign.done')
        if not os.path.exists(mk_f):
            P_ = load(SUBPREP, {}) or {}
            n_f = 0
            for k_, e_ in P_.items():
                if isinstance(e_, dict) and e_.get('state') == 'none':
                    e_.update(state='waiting', note='to be tried again: subtitles in other languages, the one that fits translated')
                    n_f += 1
            save(SUBPREP, P_)
            open(mk_f, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
            log('Subtitles: %d with no English subtitles to be tried again, with those in other languages' % n_f)
    except Exception as e:
        log('Subtitles, tried again in other languages: %s' % e)

    if not os.path.exists(os.path.expanduser('~/Grifflix/subs-machine-en3.done')):   # (once more: those machine-translated looked at again for English,
        # SubtitleCat's now read in full (its .ass files, its look-alike letters) and an episode's parts found however named: Fanny and Alexander's
        # TV version's English PART3 and PART4: John, 23 Nov 2026)
        threading.Thread(target=english_by_foreign_all, args=(g, 200), daemon=True).start()
        open(os.path.expanduser('~/Grifflix/subs-machine-en3.done'), 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    if not os.path.exists(os.path.expanduser('~/Grifflix/subs-films.done')):   # (once: films searched by their IMDb numbers and own titles too
        # now: those turned down looked at again from the start, and those machine-translated looked at again for English: John, 23 Nov
        # 2026, "we need to check what else it missed this way as I can immediately see the correct subtitles are there on open subs")
        try:
            with SP_LOCK:
                P_ = load(SUBPREP, {}) or {}
                n_f = []
                for k_, e_ in P_.items():
                    if isinstance(e_, dict) and e_.get('state') in ('nomatch', 'none'):
                        e_.update(state='waiting', note='to be tried again: looked for by its IMDb number and own title too')
                        for f_ in ('exact_tried', 'name_tried', 'ref_tried', 'ref_tried2', 'checks', 'tried', 'cands'):
                            e_.pop(f_, None)
                        n_f.append(e_.get('name'))
                save(SUBPREP, P_)
            log('Subtitles: looked at again from the start, by IMDb numbers and own titles too: %s' % (', '.join(str(x) for x in n_f) or 'none'))
        except Exception as e:
            log('Subtitles, looked at again: %s' % e)
        threading.Thread(target=english_by_foreign_all, args=(g, 150), daemon=True).start()
        open(os.path.expanduser('~/Grifflix/subs-films.done'), 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    if not os.path.exists(os.path.expanduser('~/Grifflix/subs-subtitlecat.done')):   # (once: SubtitleCat a new place to look: those with none, and those
        # machine-translated, tried again (the Varda episodes, and Varda by Agnès, whose English is there): John, 23 Nov 2026)
        subs_none_again('SubtitleCat too')
        threading.Thread(target=english_by_foreign_all, args=(g, 120), daemon=True).start()
        open(os.path.expanduser('~/Grifflix/subs-subtitlecat.done'), 'w').write(time.strftime('%Y-%m-%d %H:%M'))
        open(os.path.expanduser('~/Grifflix/subs-machine-en.done'), 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    if not os.path.exists(os.path.expanduser('~/Grifflix/subs-machine-en.done')):   # (once: those machine-translated so far, English looked for)
        threading.Thread(target=english_by_foreign_all, args=(g, 90), daemon=True).start()
        open(os.path.expanduser('~/Grifflix/subs-machine-en.done'), 'w').write(time.strftime('%Y-%m-%d %H:%M'))
    if (g.st.get('subdl') or {}).get('key') and not os.path.exists(os.path.expanduser('~/Grifflix/subs-subdl.done')):   # (once: Subdl set up after
        # those with none anywhere were last looked at: John, 23 Nov 2026, the Varda episodes marked none at 09:18, his key added at 09:39)
        subs_none_again('Subdl too')
        open(os.path.expanduser('~/Grifflix/subs-subdl.done'), 'w').write(time.strftime('%Y-%m-%d %H:%M'))

    def tfu_match():
        """Totally Fucked Up's match put back, if it hasn't it: the Mac restarted for an update two minutes after renaming it, before it was
        (22 Nov 2026). Its title, Totally F***ed Up, and its details from TMDB; its own cover in its folder kept."""
        time.sleep(240)
        try:
            lid = (g.st.get('libraries') or {}).get('movies')
            r = g.jf.call('GET', '/Users/%s/Items' % g.uid, query={'ParentId': lid, 'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'Fields': 'Path,ProviderIds',
                                                                  'SearchTerm': 'Totally'}, timeout=120) or {}
            x = next((y for y in r.get('Items') or [] if os.path.basename(os.path.dirname(y.get('Path') or '')) == 'Totally Fucked Up (1993)'), None)
            if x and not (x.get('ProviderIds') or {}).get('Imdb'):
                apply_match(g, x['Id'], {'Name': 'Totally F***ed Up', 'ProductionYear': 1994, 'ProviderIds': {'Tmdb': '33560', 'Imdb': 'tt0108366'}, 'SearchProviderName': 'TheMovieDb'})
                g.forget()
                g.publish_soon = time.time() + 180
                log('Totally Fucked Up: its match put back (Totally F***ed Up, TMDB 33560)')
        except Exception as e:
            log('Totally Fucked Up, its match: %s' % e)
    threading.Thread(target=tfu_match, daemon=True).start()
    threading.Thread(target=small_axe_films, args=(g,), daemon=True).start()
    mk_ = os.path.expanduser('~/Grifflix/afi-reread.done')
    if not os.path.exists(mk_):   # (once: the site updated, so every AFI page kept is read again by the better reading: 22 Nov 2026)
        try:
            open(mk_, 'w').write(str(int(time.time())))
            g.publish_soon = time.time() + 300
        except OSError:
            pass
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
    def brak_into_criterion():   # (once: the By Brakhage films put in The Criterion Collection, which the move John allowed was to do but a
        # restart cut off (18 Nov 2026, "By Brakhage still not showing up properly"))
        mk = os.path.expanduser('~/Grifflix/brakhage-criterion.done')
        if os.path.exists(mk):
            return
        time.sleep(90)
        try:
            q = lambda **k: (g.jf.call('GET', '/Users/%s/Items' % g.uid, query=k) or {}).get('Items', [])
            crit = next((b for b in q(Recursive='true', IncludeItemTypes='BoxSet') if (b.get('Name') or '').strip().lower() == 'the criterion collection'), None)
            movies_dir = os.path.dirname(BRAK_SRC)
            want = {os.path.join(movies_dir, f) for _rx, f, _p, _s in BRAK_PLAN}
            ids = [m['Id'] for m in q(Recursive='true', IncludeItemTypes='Movie', Fields='Path') if os.path.dirname(m.get('Path') or '') in want and not re.search(r' - part[2-9]\.\w+$', m.get('Path') or '')]
            if crit and ids:
                g.jf.call('POST', '/Collections/%s/Items' % crit['Id'], query={'Ids': ','.join(ids)})
                log('By Brakhage: %d films put in The Criterion Collection' % len(ids))
                g.forget()
                g.publish_soon = time.time() + 120
                open(mk, 'w').write(time.strftime('%Y-%m-%d %H:%M'))
        except Exception as e:
            log('By Brakhage into The Criterion Collection:', e)
    threading.Thread(target=brak_into_criterion, daemon=True).start()
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
