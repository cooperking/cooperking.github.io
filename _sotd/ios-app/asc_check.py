#!/usr/bin/env python3
"""Checks the App Store Connect API key before a build uses it, and says on the run's page what's wrong, never the values:
each secret's shape (a Key ID is 10 letters and digits, an Issuer ID 8-4-4-4-12, the .p8 a private key), then whether
Apple takes the key (GET /v1/apps), and whether the app and its bundle ID are there. Exits 1 if Apple refuses the key.

Needs ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_P8 and APPLE_TEAM_ID in the environment, and PyJWT with cryptography.
"""
import json, os, re, sys, time, urllib.error, urllib.request

BUNDLE = 'io.github.cooperking.sotd'


def note(level, m):
    print('::%s title=iPhone app (key check)::%s' % (level, m.replace('%', '%25').replace('\n', '%0A')), flush=True)


def main():
    kid, iss, p8, team = (os.environ.get(k, '').strip() for k in ('ASC_KEY_ID', 'ASC_ISSUER_ID', 'ASC_KEY_P8', 'APPLE_TEAM_ID'))
    shape = []
    if not re.fullmatch(r'[A-Z0-9]{10}', kid):
        shape.append('ASC_KEY_ID isn\'t 10 capital letters and digits (it has %d characters)' % len(kid))
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', iss.lower()):
        shape.append('ASC_ISSUER_ID isn\'t in the form xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (it has %d characters)' % len(iss))
    if not re.fullmatch(r'[A-Z0-9]{10}', team):
        shape.append('APPLE_TEAM_ID isn\'t 10 capital letters and digits (it has %d characters)' % len(team))
    if 'BEGIN PRIVATE KEY' not in p8 or 'END PRIVATE KEY' not in p8:
        shape.append('ASC_KEY_P8 doesn\'t have the BEGIN PRIVATE KEY and END PRIVATE KEY lines (%d lines in it)' % len(p8.splitlines()))
    import jwt
    try:
        tok = jwt.encode({'iss': iss, 'iat': int(time.time()), 'exp': int(time.time()) + 600, 'aud': 'appstoreconnect-v1'},
                         p8, algorithm='ES256', headers={'kid': kid, 'typ': 'JWT'})
    except Exception as e:
        note('error', 'ASC_KEY_P8 couldn\'t be read as a private key (%s).%s' % (type(e).__name__, ('\n' + '\n'.join(shape)) if shape else ''))
        return 1

    def get(path):
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

    st, j = get('/v1/apps?filter[bundleId]=%s&fields[apps]=name,bundleId' % BUNDLE)
    if st == 0:
        note('warning', 'Couldn\'t reach Apple to check the key (%s); going on with the build.' % j['errors'][0]['detail'])
        return 0
    if st != 200:
        why = '; '.join('%s: %s' % (x.get('code', ''), x.get('detail', '')) for x in (j.get('errors') or []))[:400]
        # what can be said without the values: whether the push key's ID or .p8 is in the ASC secrets, and short
        # fingerprints (SHA-256) of the two IDs, to compare with the same fingerprints of the IDs on Apple's page
        import hashlib
        from cryptography.hazmat.primitives import serialization
        fp = lambda v: hashlib.sha256(v.encode()).hexdigest()[:8]
        def pub(t):
            try:
                k = serialization.load_pem_private_key(t.strip().encode(), None)
                return k.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            except Exception:
                return None
        akid, ap8 = os.environ.get('APNS_KEY_ID', '').strip(), os.environ.get('APNS_KEY_P8', '')
        more = ['Fingerprints: ASC_KEY_ID %s, ASC_ISSUER_ID %s.' % (fp(kid), fp(iss.lower()))]
        if akid and akid == kid:
            more.append('ASC_KEY_ID is the push key\'s ID (the same as APNS_KEY_ID).')
        if ap8 and pub(ap8) and pub(ap8) == pub(p8):
            more.append('ASC_KEY_P8 is the push key\'s .p8 (the same key as APNS_KEY_P8).')
        note('error', 'Apple refused the App Store Connect key (%d%s). It\'s the Key ID, the Issuer ID or the .p8 text: they must all be '
                      'from the same Team key on Users and Access > Integrations > App Store Connect API (not the push key).\n%s%s'
             % (st, ': ' + why if why else '', '\n'.join(more), ('\n' + '\n'.join(shape)) if shape else ''))
        return 1
    apps = [a['attributes'].get('name') for a in j.get('data', [])]
    st2, b = get('/v1/bundleIds?filter[identifier]=%s' % BUNDLE)
    ids = [x['attributes'].get('identifier') for x in b.get('data', [])] if st2 == 200 else []
    st3, c = get('/v1/certificates?limit=200')
    certs = [x['attributes'].get('certificateType') for x in c.get('data', [])] if st3 == 200 else []
    note('notice', 'Apple takes the key. App record: %s. Bundle ID %s: %s. Certificates: %s.%s'
         % (', '.join(apps) or 'none for ' + BUNDLE, BUNDLE, 'registered' if BUNDLE in ids else 'not registered (%d)' % st2,
            ', '.join(sorted(set(certs))) or 'none yet', ('\n' + '\n'.join(shape)) if shape else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
