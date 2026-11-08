#!/usr/bin/env python3
"""Grifflix's own copy of its films, TV and Movie of the Day, as the songs' site keeps its calendar (songs/days.json):
files on the site, so the page needn't ask the Mac mini for any of it (John, 8 Nov 2026: "the calendar should not load
from the Mac once it's been added ... Only function should be playing and getting an update from the Mac").

John's Update (the admin page) sends the Mac's export to the Worker, which keeps it in R2 (grifflix/snapshot.json) and
starts this (grifflix-data.yml). It writes:
  grifflix/data/library.json   every film, series and collection (without their longer details)
  grifflix/data/d/<xx>.json    the longer details (overview, cast, trailers), by the first two letters of the id
  grifflix/data/s/<id>.json    a series' seasons and episodes
  grifflix/data/days.json      Movie of the Day: only the days already shown (each from 8am on its day, London time);
                               the Worker keeps the rest, and shows each on its day until this catches up
and (pictures) copies each picture not yet kept (or changed) from the Mac to R2 (grifflix/p/), where the Worker
serves them (/gp/): the Mac must be on for that, as it is when John presses Update.

  data_publish.py full       after an Update: all the files (then committed)
  data_publish.py pictures   then the pictures not yet copied
  data_publish.py days       each morning (the Worker starts it from 8am): days.json only
"""
import concurrent.futures, datetime, hashlib, json, os, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'grifflix', 'data')
MAC = 'https://media.grifflix.co.uk'
KINDS = {'Primary': [400], 'Backdrop': [480, 1100], 'Thumb': [480, 1100]}
BIG = ('overview', 'tagline', 'cast', 'directors', 'trailers')   # (the details, kept apart from the list)


def r2():
    import boto3
    from botocore.config import Config
    acct = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '').strip()
    return boto3.client('s3', endpoint_url=os.environ.get('R2_ENDPOINT', '').strip() or 'https://%s.r2.cloudflarestorage.com' % acct,
                        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'].strip(), aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'].strip(),
                        region_name='auto', config=Config(signature_version='s3v4', retries={'max_attempts': 5}))


def london_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo('Europe/London'))
    except Exception:
        return datetime.datetime.utcnow()


def revealed(day, now):
    today = now.strftime('%Y-%m-%d')
    return day < today or (day == today and now.hour >= 8)


def write(path, obj):
    """Written only when it changed (so a run with nothing new commits nothing)."""
    raw = json.dumps(obj, separators=(',', ':'), ensure_ascii=False, sort_keys=True)
    full = os.path.join(OUT, path)
    try:
        with open(full, encoding='utf-8') as f:
            if f.read() == raw:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8') as f:
        f.write(raw)
    return True


def light(it):
    return {k: v for k, v in it.items() if k not in BIG}


def build(snap, now, days_only=False):
    items, series = snap.get('items', {}), snap.get('series', {})
    eps = {e['id']: e for v in series.values() for e in v.get('episodes', [])}
    changed = 0
    # Movie of the Day: the days shown so far, each with its film (an episode too)
    motd = snap.get('motd', {})
    days, notes = {}, {}
    for d, i in sorted((motd.get('days') or {}).items()):
        it = items.get(i) or eps.get(i)
        if it and revealed(d, now):
            days[d] = dict(light(it), overview=(it.get('overview') or '')[:400]) if it.get('overview') else light(it)
            if (motd.get('notes') or {}).get(d):
                notes[d] = motd['notes'][d]
    changed += write('days.json', {'at': snap.get('at'), 'days': days, 'notes': notes})
    if days_only:
        return changed
    L = snap.get('lists', {})
    changed += write('library.json', {'at': snap.get('at'), 'items': {k: light(v) for k, v in items.items()},
                                      'movies': L.get('movies', []), 'tv': L.get('tv', []), 'collections': L.get('collections', []),
                                      'sets': snap.get('sets', {})})
    buckets = {}
    for k, v in items.items():
        det = {f: v[f] for f in BIG if v.get(f)}
        if det:
            buckets.setdefault(k[:2], {})[k] = det
    for b in ['%02x' % n for n in range(256)]:
        changed += write('d/%s.json' % b, buckets.get(b, {}))
    keep = set()
    for sid, v in series.items():
        if sid:
            keep.add(sid + '.json')
            changed += write('s/%s.json' % sid, v)
    sdir = os.path.join(OUT, 's')
    for f in os.listdir(sdir) if os.path.isdir(sdir) else []:
        if f not in keep:   # (a series no longer on the Mac)
            os.remove(os.path.join(sdir, f))
            changed += 1
    return changed


def wanted(snap):
    """Every picture the site shows: {(id, Kind): tag}."""
    out = {}
    every = list(snap.get('items', {}).values()) + [e for v in snap.get('series', {}).values() for e in v.get('episodes', [])]
    for it in every:
        t = it.get('tags') or {}
        for field, kind in (('poster', 'Primary'), ('backdrop', 'Backdrop'), ('thumb', 'Thumb')):
            if it.get(field):
                out[(it[field], kind)] = t.get(field, '') or out.get((it[field], kind), '')
    return out


def pictures(s3, bucket, snap):
    try:
        have = json.loads(s3.get_object(Bucket=bucket, Key='grifflix/p/manifest.json')['Body'].read())
    except Exception:
        have = {}
    todo = [(i, k, t) for (i, k), t in wanted(snap).items() if have.get(i + '/' + k) != (t or '-')]
    print('pictures: %d wanted, %d to copy' % (len(wanted(snap)), len(todo)), flush=True)

    def one(job):
        i, k, t = job
        for w in KINDS[k]:
            url = '%s/pimg/%s/%s.jpg?maxWidth=%d&quality=82' % (MAC, i, k, w)
            req = urllib.request.Request(url, headers={'User-Agent': 'Grifflix site copy'})
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
            if len(body) < 200:
                raise ValueError('too small')
            s3.put_object(Bucket=bucket, Key='grifflix/p/%s/%s-%d.jpg' % (i, k, w), Body=body, ContentType='image/jpeg')
        return i + '/' + k, t or '-'
    done = fails = 0
    with concurrent.futures.ThreadPoolExecutor(4) as ex:   # (4 at once: the old Mac resizes each)
        for n, f in enumerate(concurrent.futures.as_completed([ex.submit(one, j) for j in todo])):
            try:
                key, tag = f.result()
                have[key] = tag
                done += 1
            except Exception as e:
                fails += 1
                if fails <= 10:
                    print('  a picture failed:', repr(e)[:160], flush=True)
            if n and n % 250 == 0:
                print('  %d of %d' % (n, len(todo)), flush=True)
                s3.put_object(Bucket=bucket, Key='grifflix/p/manifest.json', Body=json.dumps(have).encode(), ContentType='application/json')
    s3.put_object(Bucket=bucket, Key='grifflix/p/manifest.json', Body=json.dumps(have).encode(), ContentType='application/json')
    print('pictures: %d copied, %d failed (they show from the Mac until the next Update)' % (done, fails), flush=True)


def main():
    mode = (sys.argv[1:] or ['full'])[0]
    s3, bucket = r2(), os.environ['R2_BUCKET'].strip()
    snap = json.loads(s3.get_object(Bucket=bucket, Key='grifflix/snapshot.json')['Body'].read())
    now = london_now()
    if mode == 'pictures':
        return pictures(s3, bucket, snap)
    n = build(snap, now, days_only=(mode == 'days'))
    print('%s: %d files changed; %d films, %d series, %d collections' % (mode, n, len(snap.get('lists', {}).get('movies', [])),
          len(snap.get('lists', {}).get('tv', [])), len(snap.get('lists', {}).get('collections', []))), flush=True)


if __name__ == '__main__':
    main()
