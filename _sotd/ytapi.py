"""YouTube Data API helpers shared by youtube.py (finding each song's video) and youtube_playlist.py.

The sign-in is John's, made once through Google's OAuth Playground for the Song of the Day Cloud project, and kept
in three GitHub secrets: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN.

YouTube gives the project 10,000 units a day, reset at midnight Pacific time (08:00 in London). Adding to a playlist
costs 50, reads 1. Searches cost 1 but have their own limit of 100 a day (developers.google.com/youtube/v3/
determine_quota_cost). The ledger in data/youtube-quota.json counts what today's runs have spent."""
import datetime, json, os, urllib.error, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, 'data', 'youtube-quota.json')
API = 'https://www.googleapis.com/youtube/v3/'
DAY_LIMIT = 9800          # a little under YouTube's 10,000, for the odd retry
SEARCH_LIMIT = 95         # under YouTube's 100 searches a day
UA = 'SongOfTheDayCalendar/1.0 (https://cooperking.github.io/songs/)'


class Quota(Exception):
    """YouTube's daily allowance (or ours) is used up."""


def pacific_day():
    return datetime.datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat()


def ledger():
    day = pacific_day()
    try:
        with open(LEDGER) as f:
            j = json.load(f)
    except (OSError, ValueError):
        j = {}
    if j.get('day') != day:
        j = {'day': day, 'used': 0, 'searches': 0}
    j.setdefault('searches', 0)
    return j


def spend(units, cap=DAY_LIMIT, search=False):
    """Count units (and searches) against today; raise Quota if that would pass a limit."""
    j = ledger()
    if j['used'] + units > cap or (search and j['searches'] >= SEARCH_LIMIT):
        raise Quota()
    j['used'] += units
    if search:
        j['searches'] += 1
    with open(LEDGER, 'w') as f:
        json.dump(j, f)


def full(search=False):
    """YouTube said no more today: mark the day (or just its searches) used up."""
    j = ledger()
    if search:
        j['searches'] = SEARCH_LIMIT
    else:
        j['used'] = DAY_LIMIT
    with open(LEDGER, 'w') as f:
        json.dump(j, f)


def call(method, url, body=None, token=None, form=False):
    data, headers = None, {'User-Agent': UA}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        else:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        text = e.read().decode('utf-8', 'replace')
        if e.code == 403 and ('quotaExceeded' in text or 'dailyLimitExceeded' in text):
            raise Quota()
        return {'_status': e.code, '_text': text[:300]}


def token():
    """An access token from John's sign-in, or None when the secrets aren't set."""
    env = {k: os.environ.get(k, '').strip() for k in ('YT_CLIENT_ID', 'YT_CLIENT_SECRET', 'YT_REFRESH_TOKEN')}
    if not all(env.values()):
        return None
    tok = call('POST', 'https://oauth2.googleapis.com/token', {
        'client_id': env['YT_CLIENT_ID'], 'client_secret': env['YT_CLIENT_SECRET'],
        'refresh_token': env['YT_REFRESH_TOKEN'], 'grant_type': 'refresh_token'}, form=True)
    if not tok.get('access_token'):
        raise SystemExit('YouTube sign-in failed: ' + str(tok.get('_status')) + ' ' + str(tok.get('_text')))
    return tok['access_token']


def get(path, params, tok, units=1, cap=DAY_LIMIT, search=False):
    spend(units, cap, search)
    try:
        return call('GET', API + path + '?' + urllib.parse.urlencode(params), token=tok)
    except Quota:
        full(search)
        raise
