#!/usr/bin/env python3
"""Checks John's two Apple keys before a build uses them, and says on the run's page what's wrong, never the values.

The App Store Connect (upload) key: ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_P8. Each secret's shape is checked (a Key ID is 10
letters and digits, an Issuer ID 8-4-4-4-12, a .p8 a private key), then Apple's API is asked (GET /v1/apps) with it; if
Apple says no, the other pairings of the two Key IDs and two .p8s are tried, so swapped secrets are named.
The push (APNs) key: APNS_KEY_ID, APNS_KEY_P8, with APPLE_TEAM_ID: a notification to a made-up device is sent to Apple's
push service; "bad device token" means Apple took the key, "invalid provider token" that it didn't.
Exits 1 if Apple refuses the upload key. Needs PyJWT with cryptography, and curl (HTTP/2) for the push check.
"""
import hashlib, json, os, re, subprocess, sys, time, urllib.error, urllib.request

BUNDLE = 'io.github.cooperking.sotd'


def note(level, m):
    print('::%s title=iPhone app (key check)::%s' % (level, m.replace('%', '%25').replace('\n', '%0A')), flush=True)


def env(k):
    return os.environ.get(k, '').strip()


def token(kid, iss, p8, aud=True):
    import jwt
    claims = {'iss': iss, 'iat': int(time.time())}
    if aud:
        claims.update({'exp': int(time.time()) + 600, 'aud': 'appstoreconnect-v1'})
    return jwt.encode(claims, p8, algorithm='ES256', headers={'kid': kid, 'typ': 'JWT'} if aud else {'kid': kid})


def asc(tok, path):
    rq = urllib.request.Request('https://api.appstoreconnect.apple.com' + path, headers={'Authorization': 'Bearer ' + tok})
    try:
        with urllib.request.urlopen(rq, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}
    except Exception as e:   # (not Apple saying no: the network)
        return 0, {'errors': [{'code': 'NETWORK', 'detail': str(e)[:200]}]}


def push_check(kid, team, p8):
    """'yes' if Apple's push service takes the key, 'no: <reason>' if not, '' if it couldn't be asked."""
    try:
        tok = token(kid, team, p8, aud=False)
    except Exception as e:
        return 'no: the .p8 isn\'t a private key (%s)' % type(e).__name__
    try:
        out = subprocess.run(['curl', '-s', '--http2', '-o', '-', '-w', '\n%{http_code}', '-X', 'POST',
                              '-H', 'authorization: bearer ' + tok, '-H', 'apns-topic: ' + BUNDLE, '-H', 'apns-push-type: alert',
                              '-d', '{"aps":{"alert":"key check"}}', 'https://api.push.apple.com/3/device/' + '0' * 64],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ''
    body, _, code = out.rpartition('\n')
    try:
        reason = json.loads(body or '{}').get('reason', '')
    except Exception:
        reason = ''
    if reason in ('BadDeviceToken', 'DeviceTokenNotForTopic', 'Unregistered'):
        return 'yes'
    return 'no: %s %s' % (code, reason) if code else ''


def main():
    kid, iss, p8, team = env('ASC_KEY_ID'), env('ASC_ISSUER_ID'), os.environ.get('ASC_KEY_P8', ''), env('APPLE_TEAM_ID')
    akid, ap8 = env('APNS_KEY_ID'), os.environ.get('APNS_KEY_P8', '')
    if not (kid and iss and p8 and team):
        return 0   # (no keys yet: the build checks it builds, for the simulator)
    shape = []
    for name, v in (('ASC_KEY_ID', kid), ('APNS_KEY_ID', akid), ('APPLE_TEAM_ID', team)):
        if v and not re.fullmatch(r'[A-Z0-9]{10}', v):
            shape.append('%s isn\'t 10 capital letters and digits (it has %d characters)' % (name, len(v)))
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', iss.lower()):
        shape.append('ASC_ISSUER_ID isn\'t in the form xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (it has %d characters)' % len(iss))
    for name, v in (('ASC_KEY_P8', p8), ('APNS_KEY_P8', ap8)):
        if v and ('BEGIN PRIVATE KEY' not in v or 'END PRIVATE KEY' not in v):
            shape.append('%s doesn\'t have the BEGIN PRIVATE KEY and END PRIVATE KEY lines (%d lines in it)' % (name, len(v.splitlines())))
    fp = lambda v: hashlib.sha256(v.encode()).hexdigest()[:8]
    prints = 'Fingerprints: ASC_KEY_ID %s, APNS_KEY_ID %s, ASC_ISSUER_ID %s.' % (fp(kid), fp(akid), fp(iss.lower()))

    # the push key (its result is told either way; it doesn't stop the build)
    if akid and ap8 and team:
        said = push_check(akid, team, ap8)
        if said == 'yes':
            note('notice', 'Push key: Apple\'s push service takes APNS_KEY_ID with APNS_KEY_P8.')
        elif said:
            other = [n for n, k, p in (('ASC_KEY_ID with APNS_KEY_P8', kid, ap8), ('APNS_KEY_ID with ASC_KEY_P8', akid, p8),
                                       ('ASC_KEY_ID with ASC_KEY_P8', kid, p8)) if push_check(k, team, p) == 'yes']
            note('warning', 'Push key: Apple\'s push service refused APNS_KEY_ID with APNS_KEY_P8 (%s).%s' % (
                said[4:], ' It takes ' + other[0] + ': that\'s the push key.' if other else ''))

    # the upload key
    try:
        tok = token(kid, iss, p8)
    except Exception as e:
        note('error', 'ASC_KEY_P8 couldn\'t be read as a private key (%s).\n%s' % (type(e).__name__, '\n'.join(shape)))
        return 1
    st, j = asc(tok, '/v1/apps?filter[bundleId]=%s&fields[apps]=name,bundleId' % BUNDLE)
    if st == 0:
        note('warning', 'Couldn\'t reach Apple to check the upload key (%s); going on with the build.' % j['errors'][0]['detail'])
        return 0
    if st != 200:
        why = '; '.join('%s: %s' % (x.get('code', ''), x.get('detail', '')) for x in (j.get('errors') or []))[:300]
        works = []
        for name, k, p in (('ASC_KEY_ID with APNS_KEY_P8', kid, ap8), ('APNS_KEY_ID with ASC_KEY_P8', akid, p8),
                           ('APNS_KEY_ID with APNS_KEY_P8', akid, ap8)):
            if k and p:
                try:
                    if asc(token(k, iss, p), '/v1/apps?limit=1')[0] == 200:
                        works.append(name)
                except Exception:
                    pass
        fix = ('Apple does take %s as the upload key: so %s.' % (works[0], {
            'ASC_KEY_ID with APNS_KEY_P8': 'the two .p8s are swapped (ASC_KEY_P8 should have APNS_KEY_P8\'s text, and the other way round)',
            'APNS_KEY_ID with ASC_KEY_P8': 'the two Key IDs are swapped',
            'APNS_KEY_ID with APNS_KEY_P8': 'the upload key went into the APNS secrets and the push key into the ASC ones'}[works[0]])
            if works else 'No pairing of the two Key IDs and two .p8s works, so the upload key\'s .p8 isn\'t in either secret '
                         '(the file is AuthKey_<its Key ID>.p8), or the Issuer ID is wrong.')
        note('error', 'Apple refused the upload key (%d%s).\n%s\n%s%s' % (st, ': ' + why if why else '', fix, prints,
                                                                         ('\n' + '\n'.join(shape)) if shape else ''))
        return 1
    apps = [a['attributes'].get('name') for a in j.get('data', [])]
    st2, b = asc(tok, '/v1/bundleIds?filter[identifier]=%s' % BUNDLE)
    ids = [x['attributes'].get('identifier') for x in b.get('data', [])] if st2 == 200 else []
    st3, c = asc(tok, '/v1/certificates?limit=200')
    certs = [x['attributes'].get('certificateType') for x in c.get('data', [])] if st3 == 200 else []
    note('notice', 'Upload key: Apple takes it. App record: %s. Bundle ID %s: %s. Certificates: %s.%s'
         % (', '.join(apps) or 'none for ' + BUNDLE, BUNDLE, 'registered' if BUNDLE in ids else 'not registered (%d)' % st2,
            ', '.join(sorted(set(certs))) or 'none yet', ('\n' + '\n'.join(shape)) if shape else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
