#!/usr/bin/env python3
"""Hands a build that's just gone up to the testers, through the App Store Connect API (the upload key):

    python testflight.py BUILD      BUILD: the build number (CFBundleVersion), e.g. 14

  1. waits until Apple has processed it (usually 5 to 15 minutes; up to WAIT_MINUTES, default 45);
  2. gives it the What to Test notes (WHAT_TO_TEST below, in English (U.K.)), unless it has some already;
  3. adds it to every external group (the family's: SOTD) that hasn't got it;
  4. submits it for Beta App Review, which external testers need (the first build took about a day; later ones are
     usually quicker), unless it's been submitted already.
The internal group (Song Testers) gets every build by itself. Each step says what it did on the run's page; a step
that's done already is left as it is, so running this again for the same build is safe.

Needs ASC_KEY_ID, ASC_ISSUER_ID and ASC_KEY_P8 in the environment, and PyJWT with cryptography.
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request

BUNDLE = 'io.github.cooperking.sotd'
VERSION = '1.0'
LOCALE = 'en-GB'
WHAT_TO_TEST = ("Open a day and play its song and its video. Sign in under Profile with your code or invite link, then check you "
                "hear John's own recording. Lock the screen while a song plays: it should carry on. Turn notifications on in "
                "Profile. Download a song, switch on flight mode, and play it.")


def note(level, m):
    print('::%s title=iPhone app (TestFlight)::%s' % (level, m.replace('%', '%25').replace('\n', '%0A')), flush=True)


class Refused(Exception):
    def __init__(self, code, text):
        super().__init__('%d %s' % (code, text[:500])); self.code, self.text = code, text


def main(build):
    import jwt
    kid, iss, p8 = (os.environ.get(k, '') for k in ('ASC_KEY_ID', 'ASC_ISSUER_ID', 'ASC_KEY_P8'))
    if not (kid.strip() and iss.strip() and p8.strip()):
        note('notice', 'No Apple keys: nothing to hand to the testers.')
        return 0
    made = [0, None]

    def token():   # (a token lasts 20 minutes at most; a new one every 15)
        if time.time() - made[0] > 900:
            made[0] = time.time()
            made[1] = jwt.encode({'iss': iss.strip(), 'iat': int(made[0]), 'exp': int(made[0]) + 1100, 'aud': 'appstoreconnect-v1'},
                                 p8, algorithm='ES256', headers={'kid': kid.strip(), 'typ': 'JWT'})
        return made[1]

    def api(method, path, body=None):
        rq = urllib.request.Request('https://api.appstoreconnect.apple.com' + path, method=method,
                                    data=json.dumps(body).encode() if body is not None else None,
                                    headers={'Authorization': 'Bearer ' + token(), 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(rq, timeout=60) as r:
                t = r.read()
                return json.loads(t) if t else {}
        except urllib.error.HTTPError as e:
            raise Refused(e.code, e.read().decode('utf-8', 'replace'))

    q = urllib.parse.quote
    apps = api('GET', '/v1/apps?filter[bundleId]=%s' % q(BUNDLE)).get('data', [])
    app = next((a['id'] for a in apps if a['attributes'].get('bundleId') == BUNDLE), None)
    if not app:
        note('error', 'No app record for %s in App Store Connect.' % BUNDLE)
        return 1

    # 1. processed
    wait, b = float(os.environ.get('WAIT_MINUTES', '45')) * 60, None
    start = time.time()
    while True:
        found = api('GET', '/v1/builds?filter[app]=%s&filter[version]=%s&filter[preReleaseVersion.version]=%s&limit=5'
                    % (app, q(str(build)), q(VERSION))).get('data', [])
        b = found[0] if found else None
        state = b and b['attributes'].get('processingState')
        if state == 'VALID':
            break
        if state in ('FAILED', 'INVALID'):
            note('error', 'Apple couldn\'t process build %s (%s): see the email Apple sent.' % (build, state))
            return 1
        if b and b['attributes'].get('expired'):
            note('error', 'Build %s has expired.' % build)
            return 1
        if time.time() - start > wait:
            note('error', 'Build %s still isn\'t processed after %d minutes (%s): run this again later.' % (build, wait // 60, state or 'not there yet'))
            return 1
        time.sleep(60)
    bid = b['id']
    done = ['processed after %d min' % ((time.time() - start) // 60)]

    # 2. What to Test
    locs = api('GET', '/v1/builds/%s/betaBuildLocalizations' % bid).get('data', [])
    mine = next((l for l in locs if l['attributes'].get('locale') == LOCALE), None)
    if not mine:
        api('POST', '/v1/betaBuildLocalizations', {'data': {'type': 'betaBuildLocalizations', 'attributes': {'locale': LOCALE, 'whatsNew': WHAT_TO_TEST},
                                                            'relationships': {'build': {'data': {'type': 'builds', 'id': bid}}}}})
        done.append('What to Test added')
    elif not (mine['attributes'].get('whatsNew') or '').strip():
        api('PATCH', '/v1/betaBuildLocalizations/' + mine['id'], {'data': {'type': 'betaBuildLocalizations', 'id': mine['id'], 'attributes': {'whatsNew': WHAT_TO_TEST}}})
        done.append('What to Test added')
    else:
        done.append('What to Test already there')

    # 3. the external groups
    groups = [g for g in api('GET', '/v1/apps/%s/betaGroups?limit=50' % app).get('data', []) if not g['attributes'].get('isInternalGroup')]
    if not groups:
        note('warning', 'No external group yet (TestFlight > External Testing > +): build %s is for the internal group only.' % build)
    for g in groups:
        have = [x['id'] for x in api('GET', '/v1/betaGroups/%s/relationships/builds?limit=200' % g['id']).get('data', [])]
        if bid in have:
            done.append('already in %s' % g['attributes'].get('name'))
        else:
            api('POST', '/v1/betaGroups/%s/relationships/builds' % g['id'], {'data': [{'type': 'builds', 'id': bid}]})
            done.append('added to %s' % g['attributes'].get('name'))

    # 4. Beta App Review (for the external groups)
    if groups:
        sub = api('GET', '/v1/betaAppReviewSubmissions?filter[build]=%s' % bid).get('data', [])
        if sub:
            done.append('review: %s' % sub[0]['attributes'].get('betaReviewState', 'submitted'))
        else:
            try:
                r = api('POST', '/v1/betaAppReviewSubmissions', {'data': {'type': 'betaAppReviewSubmissions',
                                                                          'relationships': {'build': {'data': {'type': 'builds', 'id': bid}}}}})
                done.append('submitted for Beta App Review (%s)' % (r.get('data', {}).get('attributes', {}).get('betaReviewState') or 'waiting'))
            except Refused as e:
                why = '; '.join(x.get('detail') or x.get('title') or '' for x in (json.loads(e.text or '{}').get('errors') or []))[:300] if e.text.startswith('{') else e.text[:300]
                note('warning', 'Build %s couldn\'t be submitted for Beta App Review (%d): %s. The hourly "iPhone app, public link" job '
                     'submits the newest build once the one before is through.' % (build, e.code, why.rstrip('.')))
    note('notice', 'Build %s: %s.' % (build, ', '.join(done)))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1]))
    except Refused as e:
        note('error', 'App Store Connect refused a request: %s' % e)
        sys.exit(1)
