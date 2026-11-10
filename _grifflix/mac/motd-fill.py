#!/usr/bin/env python3
"""Fills the Movie of the Day calendar from 1 December 2025 up to today with films from John's collections (John,
7 Nov 2026: "nearly a year of film choices - up until today - choose from the collections - include all of Dad's
favourites - and the best rated from the other collections, plenty of oscar winners ... All should be a feature film,
in English").

  * every one of Dad's favourites, whatever its language or length (the one exception to the rules below)
  * plenty of Oscar winners (from collections named for the Oscars / Academy Awards): about two in five days
  * the rest: the best rated in the other collections (its rating, and the critics' where there is one)
  * feature films only: 70 minutes or more, never a film from inside a box set (those are short films)
  * in English (all but Dad's favourites): the film's sound marked English, or unmarked and made in an English-speaking country
  * each film once, on random days through the year; Christmas films around Christmas; a day already chosen is kept

  python3 motd-fill.py        the list, day by day (nothing changes): ~/Grifflix/motd-plan.csv
  python3 motd-fill.py go     puts them on the calendar
  python3 motd-fill.py --to 2026-12-31 (go)   on to the end of the year too (days already chosen kept, no film twice)
  python3 motd-fill.py --redo --to 2027-12-31 (go)   every day from 1 Dec 2025 dealt again, from scratch
  python3 motd-fill.py --unrepeat (go)   a film on more than one day still to come (a month's theme film put on its 1st
                                         when it was already on another day): the other days given new films, the 1st kept
                                         (John, 9 Nov 2026: "The playlist shouldn't have repeated films")
  python3 motd-fill.py --unrepeat --all --keep 2026-05-01,2026-08-01 (go)   the days gone by too, and the days given (the
                                         months' theme 1sts) kept first: the admin page runs this through the gateway when the
                                         themes are saved and on Update everything (John, 10 Nov 2026: "there are still repeats
                                         in the films - I thought moving the themes about was meant to get rid of them?")

The whole calendar is read from this Mac's own copy (the days still to come too), so no film is put on a second day.
"""
import csv, datetime, json, os, random, re, sys, urllib.parse, urllib.request

APP = os.environ.get('GRIFFLIX_DIR') or os.path.expanduser('~/Library/Application Support/Grifflix')
HOME = os.path.expanduser('~/Grifflix')
s = json.load(open(os.path.join(APP, 'settings.json')))
JF = s.get('jellyfin', 'http://127.0.0.1:8096')
GW = 'http://127.0.0.1:%s' % s.get('port', 8097)
GO = 'go' in sys.argv[1:]
UNREPEAT = '--unrepeat' in sys.argv[1:]
ALL = '--all' in sys.argv[1:]   # (with --unrepeat: the days gone by too, not only those still to come)
KEEP = set(next((sys.argv[k + 1] for k, a in enumerate(sys.argv[:-1]) if a == '--keep'), '').split(',')) - {''}
REDO = '--redo' in sys.argv[1:]   # deal every day from 1 December 2025 to --to again, from scratch (the days already chosen too)
START = datetime.date(2025, 12, 1)
TODAY = datetime.date.today()
# --to YYYY-MM-DD: on to that day (John, 8 Nov 2026: the rest of the year laid out too); otherwise up to today
END = next((datetime.date.fromisoformat(sys.argv[k + 1]) for k, a in enumerate(sys.argv[:-1]) if a == '--to'), TODAY)
MIN_MINS = int(os.environ.get('GRIFFLIX_MIN_MINS') or 70)
ENGLISH_PLACES = {'united states of america', 'united states', 'usa', 'us', 'united kingdom', 'uk', 'great britain', 'england',
                  'scotland', 'wales', 'ireland', 'canada', 'australia', 'new zealand'}
os.makedirs(HOME, exist_ok=True)


def call(method, path, body=None, **q):
    url = JF + path + ('?' + urllib.parse.urlencode(q) if q else '')
    r = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                               headers={'Authorization': 'MediaBrowser Token="%s"' % s['jf_token'], 'Content-Type': 'application/json'})
    raw = urllib.request.urlopen(r, timeout=300).read()
    return json.loads(raw) if raw[:1] in (b'{', b'[') else None


def g(d, k, default=None):
    v = d.get(k, d.get(k[:1].lower() + k[1:]))
    return default if v is None else v


def kids(pid, **q):
    return g(call('GET', '/Users/%s/Items' % s['jf_user_id'], ParentId=pid, **q), 'Items', [])


# the collections, and which are box sets (a collection inside another)
sets = g(call('GET', '/Users/%s/Items' % s['jf_user_id'], Recursive='true', IncludeItemTypes='BoxSet', SortBy='SortName'), 'Items', [])
held = {g(c, 'Id'): kids(g(c, 'Id'), Fields='') for c in sets}
boxsets = {g(i, 'Id') for c in held.values() for i in c if g(i, 'Type') == 'BoxSet'}
in_box = {g(i, 'Id') for b in boxsets for i in held.get(b, [])}
name_of = {g(c, 'Id'): g(c, 'Name') for c in sets}
dads = [c for c in sets if re.search(r'\bdad', g(c, 'Name'), re.I)]
oscars = [c for c in sets if re.search(r'oscar|academy award|best picture', g(c, 'Name'), re.I)]
others = [c for c in sets if c not in dads and c not in oscars and g(c, 'Id') not in boxsets]
print('Dad’s favourites:', ', '.join(g(c, 'Name') for c in dads) or 'NONE FOUND (a collection with “Dad” in its name)')
print('Oscar collections:', ', '.join(g(c, 'Name') for c in oscars) or 'none found (a collection with Oscar or Academy Award in its name)')

# every film in a collection, with what's needed to choose
films = {}
where = {}
for c in sets:
    for i in held[g(c, 'Id')]:
        if g(i, 'Type') == 'Movie':
            where.setdefault(g(i, 'Id'), set()).add(g(c, 'Id'))
ids = list(where)
for n in range(0, len(ids), 100):
    r = call('GET', '/Users/%s/Items' % s['jf_user_id'], Ids=','.join(ids[n:n + 100]),
             Fields='MediaStreams,ProductionLocations,Genres,Tags,ProductionYear,CommunityRating,CriticRating,OriginalTitle')
    for it in g(r, 'Items', []):
        films[g(it, 'Id')] = it


def english(it):
    audio = [x for x in g(it, 'MediaStreams', []) if g(x, 'Type') == 'Audio']
    lang = (g(audio[0], 'Language') or '').lower() if audio else ''
    if lang in ('eng', 'en', 'english'):
        return True
    if lang and lang not in ('und', 'unk', 'mis', 'zxx'):
        return False
    places = {p.lower() for p in g(it, 'ProductionLocations', [])}
    return bool(places & ENGLISH_PLACES)


def feature(it):
    mins = (g(it, 'RunTimeTicks') or 0) / 6e8
    genres = {x.lower() for x in g(it, 'Genres', [])}
    return mins >= MIN_MINS and g(it, 'Id') not in in_box and 'short' not in genres


def score(it):
    c = g(it, 'CommunityRating') or 0
    k = (g(it, 'CriticRating') or 0) / 10
    return (c + k) / 2 if c and k else (c or k)


def christmas(it):
    text = ' '.join([g(it, 'Name') or ''] + g(it, 'Genres', []) + g(it, 'Tags', [])).lower()
    return 'christmas' in text or 'xmas' in text


days = [START + datetime.timedelta(n) for n in range((END - START).days + 1)]
cal = {}
try:   # (the whole calendar, the days still to come too: the gateway's own file on this Mac)
    cal = json.load(open(os.path.join(APP, 'data.json')))['calendars']['motd'].get('days') or {}
except Exception:
    try:
        cal = json.load(urllib.request.urlopen(urllib.request.Request(GW + '/api/public/calendar?id=motd'), timeout=60)).get('days') or {}
    except Exception:
        pass
cal = {d: (v.get('id') if isinstance(v, dict) else v) for d, v in cal.items()}
if REDO:
    cal = {}   # (all dealt again: the films already on the calendar can come again, on new days)
taken = {d for d in cal}
already = {(v.get('id') if isinstance(v, dict) else v) for v in cal.values()}
open_days = [d for d in days if d.isoformat() not in taken]
if UNREPEAT:   # the days still to come whose film is on another day too: given new films (a 1st's kept: its month's theme film)
    def who(i):
        it = films.get(i) or {}
        return ('%s|%s' % (str(g(it, 'Name') or '').lower(), g(it, 'ProductionYear', ''))) if it else i
    seen = {}
    for d in sorted(cal):
        seen.setdefault(who(cal[d]), []).append(d)
    again = []
    for k, ds in seen.items():
        if len(ds) < 2:
            continue
        # (kept: the 1st of the month whose theme it is, else a 1st, else the first day it was on)
        keep = next((d for d in ds if d in KEEP), None) or next((d for d in ds if d.endswith('-01')), ds[0])
        again += [d for d in ds if d != keep and (ALL or d > TODAY.isoformat())]
    open_days = sorted(datetime.date.fromisoformat(d) for d in again)
    print('%d days %shave a film that is on another day too.' % (len(open_days), '' if ALL else 'still to come '))

dad_ids = {g(x, 'Id') for c in dads for x in held[g(c, 'Id')]}
# Dad's favourites: every one, whatever its language or length (John, 7 Nov 2026: "the exception to all English language
# rule should be Dad's collection - all of those"); the rest English feature films
ok = {i: it for i, it in films.items() if i not in already and (i in dad_ids or (feature(it) and english(it)))}
skipped_dads = []
pick, why = [], {}


def take(ids, label, n=None):
    for i in sorted(ids, key=lambda i: -score(ok[i])):
        if n is not None and n <= 0:
            break
        if i in why:
            continue
        pick.append(i); why[i] = label
        if n is not None:
            n -= 1


take([g(x, 'Id') for c in dads for x in held[g(c, 'Id')] if g(x, 'Id') in ok], 'Dad’s favourites')
take([i for i in ok if where[i] & {g(c, 'Id') for c in oscars}], 'Oscar winner', n=max(0, round(len(open_days) * .4)))
take([i for i in ok if where[i] & {g(c, 'Id') for c in others}], 'best rated', n=max(0, len(open_days) - len(pick)))
pick = pick[:len(open_days)]

# the days: Christmas films 18-25 December; everything else, Dad's favourites too, on random days
rnd = random.Random(TODAY.toordinal() if REDO else 2025)   # (a new deal each day it's redone; the same between looking and go)
xmas = [i for i in pick if christmas(ok[i])]
rest = [i for i in pick if i not in xmas]
rnd.shuffle(rest)
dad = [i for i in rest if why[i] == 'Dad’s favourites']
rest = [i for i in rest if why[i] != 'Dad’s favourites']
plan = {}
xmas_days = [d for d in open_days if d.month == 12 and 18 <= d.day <= 25]
for d, i in zip(xmas_days, xmas):
    plan[d] = i
free = [d for d in open_days if d not in plan]
xmas = xmas[len(xmas_days):]
rest = rest + xmas
for d, i in zip(rnd.sample(free, min(len(dad), len(free))), dad):   # Dad's favourites on random days through the year (John, 7 Nov 2026)
    plan[d] = i
for d in [d for d in free if d not in plan]:
    if rest:
        plan[d] = rest.pop(0)

with open(os.path.join(HOME, 'motd-plan.csv'), 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['date', 'film', 'year', 'why', 'rating', 'collections'])
    for d in sorted(plan):
        it = ok[plan[d]]
        w.writerow([d.isoformat(), g(it, 'Name'), g(it, 'ProductionYear', ''), why[plan[d]], round(score(it), 1), '; '.join(sorted(name_of[c] for c in where[plan[d]]))])
from collections import Counter
cnt = Counter(why[i] for i in plan.values())
print('%d days (%s to %s), %d already chosen and kept. Planned: %d — %s.' % (len(days), START, END, len(taken), len(plan),
      ', '.join('%d %s' % (v, k) for k, v in cnt.items())))
if len(plan) < len(open_days):
    print('%d days left empty: not enough English feature films in the collections.' % (len(open_days) - len(plan)))
if skipped_dads:
    print('Dad’s favourites left out (not English, or under %d minutes): %s' % (MIN_MINS, '; '.join(g(x, 'Name') for x in skipped_dads)))
for d in ([] if UNREPEAT else sorted(plan)[-7:]):   # (not the films for days still a surprise)
    print('  %s  %s (%s)' % (d.strftime('%a %d %b'), g(ok[plan[d]], 'Name'), g(ok[plan[d]], 'ProductionYear', '')))
print('Day by day: ~/Grifflix/motd-plan.csv')
if not GO:
    print('Nothing changed yet. To put them on the calendar:  python3 motd-fill.py go' + (' --redo' if REDO else '') + (' --to %s' % END if END != TODAY else ''))
    sys.exit()
if UNREPEAT:   # (each of those days replaced on its own, the rest of the calendar as it is)
    n = 0
    for d, i in sorted(plan.items()):
        r = urllib.request.Request(GW + '/local/calendar', data=json.dumps({'days': {d.isoformat(): i}, 'replace_from': d.isoformat(), 'replace_to': d.isoformat()}).encode(),
                                   method='POST', headers={'X-Grifflix-Secret': s['secret'], 'Content-Type': 'application/json'})
        n += json.load(urllib.request.urlopen(r, timeout=60)).get('added', 0)
    print('%d repeated day%s given a new film. Press Update on the admin page to put them on the site.' % (n, '' if n == 1 else 's'))
    sys.exit()
body = {'days': {d.isoformat(): i for d, i in plan.items()}}
if REDO:
    body.update(replace_from=START.isoformat(), replace_to=END.isoformat())
r = urllib.request.Request(GW + '/local/calendar', data=json.dumps(body).encode(), method='POST',
                           headers={'X-Grifflix-Secret': s['secret'], 'Content-Type': 'application/json'})
res = json.load(urllib.request.urlopen(r, timeout=60))
if REDO:
    print('Dealt again: %d days on the calendar (%d were there before).' % (res.get('added', 0), res.get('replaced', 0)))
    sys.exit()
print('On the calendar: %d day%s added%s.' % (res.get('added', 0), '' if res.get('added') == 1 else 's', (', %d kept as they were' % res['kept']) if res.get('kept') else ''))
