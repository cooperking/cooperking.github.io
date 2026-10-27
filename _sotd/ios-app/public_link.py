#!/usr/bin/env python3
"""The family's TestFlight group, looked after hourly through the App Store Connect API (the upload key):

    python public_link.py OUT      OUT: songs/ios.json

  1. the newest build in the external group (SOTD) goes to Beta App Review once nothing else is there (Apple takes one
     build at a time; testflight.py's own submission is refused while an earlier build is in review);
  2. once Apple has approved a build for the group, its public link is turned on (anyone with it can get the app, up to
     TestFlight's 10,000 testers; the calendar's members still sign in with their own invitation) and OUT is written as
     {"testflight": "https://testflight.apple.com/join/..."}. Invitations read it from there: an iPhone opening one is
     shown the steps for the app (the Worker's invitation page).
It says where the builds are with Apple's review. With GITHUB_OUTPUT set, it writes done=true there once the link is in
OUT and the newest build has been submitted: nothing is left for it to do.

Needs ASC_KEY_ID, ASC_ISSUER_ID and ASC_KEY_P8 in the environment, and PyJWT with cryptography.
"""
import json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

BUNDLE = 'io.github.cooperking.sotd'
GROUP = 'SOTD'
LINK_RE = re.compile(r'^https://testflight\.apple\.com/join/\w+$')
BUSY = ('WAITING_FOR_REVIEW', 'IN_REVIEW')


def note(level, m):
    print('::%s title=iPhone app (public link)::%s' % (level, m.replace('%', '%25').replace('\n', '%0A')), flush=True)


class Refused(Exception):
    def __init__(self, code, text):
        super().__init__('%d %s' % (code, text[:500])); self.code, self.text = code, text


def why(e):
    try:
        return '; '.join(x.get('detail') or x.get('title') or '' for x in json.loads(e.text).get('errors') or [])[:300]
    except Exception:
        return e.text[:300]


def main(out):
    import jwt
    kid, iss, p8 = (os.environ.get(k, '') for k in ('ASC_KEY_ID', 'ASC_ISSUER_ID', 'ASC_KEY_P8'))
    if not (kid.strip() and iss.strip() and p8.strip()):
        note('notice', 'No Apple keys: nothing to do.')
        return 0
    tok = jwt.encode({'iss': iss.strip(), 'iat': int(time.time()), 'exp': int(time.time()) + 1100, 'aud': 'appstoreconnect-v1'},
                     p8, algorithm='ES256', headers={'kid': kid.strip(), 'typ': 'JWT'})

    def api(method, path, body=None):
        rq = urllib.request.Request('https://api.appstoreconnect.apple.com' + path, method=method,
                                    data=json.dumps(body).encode() if body is not None else None,
                                    headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(rq, timeout=60) as r:
                t = r.read()
                return json.loads(t) if t else {}
        except urllib.error.HTTPError as e:
            raise Refused(e.code, e.read().decode('utf-8', 'replace'))

    said = []
    apps = api('GET', '/v1/apps?filter[bundleId]=%s' % urllib.parse.quote(BUNDLE)).get('data', [])
    app = next((a['id'] for a in apps if a['attributes'].get('bundleId') == BUNDLE), None)
    if not app:
        note('error', 'No app record for %s in App Store Connect.' % BUNDLE)
        return 1
    ext = [g for g in api('GET', '/v1/apps/%s/betaGroups?limit=50' % app).get('data', []) if not g['attributes'].get('isInternalGroup')]
    g = next((g for g in ext if g['attributes'].get('name') == GROUP), ext[0] if ext else None)
    if not g:
        note('warning', 'No external group yet (TestFlight > External Testing > +).')
        return 0

    # where the group's builds are with Apple's review
    builds = [b for b in api('GET', '/v1/betaGroups/%s/builds?limit=50' % g['id']).get('data', []) if not b['attributes'].get('expired')]
    num = {b['id']: b['attributes'].get('version') or '?' for b in builds}
    order = sorted(num, key=lambda b: int(num[b]) if str(num[b]).isdigit() else 0)
    state = {}
    for bid in order:
        subs = api('GET', '/v1/betaAppReviewSubmissions?filter[build]=%s' % bid).get('data', [])
        if subs:
            state[bid] = subs[0]['attributes'].get('betaReviewState') or '?'

    # 1. the newest build to Beta App Review, once nothing else is there
    newest = order[-1] if order else None
    if newest and newest not in state:
        if any(v in BUSY for v in state.values()):
            said.append('build %s waits to go to review' % num[newest])
        else:
            try:
                r = api('POST', '/v1/betaAppReviewSubmissions', {'data': {'type': 'betaAppReviewSubmissions',
                                                                          'relationships': {'build': {'data': {'type': 'builds', 'id': newest}}}}})
                state[newest] = (r.get('data') or {}).get('attributes', {}).get('betaReviewState') or 'WAITING_FOR_REVIEW'
                said.append('build %s submitted for Beta App Review' % num[newest])
            except Refused as e:
                note('warning', 'Build %s couldn\'t be submitted for Beta App Review (%d): %s' % (num[newest], e.code, why(e)))
    said.append(', '.join('build %s: %s' % (num[b], state.get(b, 'not submitted')) for b in order) or 'no build in %s yet' % GROUP)

    # 2. the public link, once a build is approved
    a = g['attributes']
    link = a.get('publicLink') or '' if a.get('publicLinkEnabled') else ''
    if not LINK_RE.match(link) and any(v == 'APPROVED' for v in state.values()):
        try:
            r = api('PATCH', '/v1/betaGroups/' + g['id'], {'data': {'type': 'betaGroups', 'id': g['id'], 'attributes': {'publicLinkEnabled': True}}})
            link = ((r.get('data') or {}).get('attributes') or {}).get('publicLink') or ''
            if not LINK_RE.match(link):   # (sometimes only there a moment later)
                time.sleep(5)
                link = api('GET', '/v1/betaGroups/' + g['id'])['data']['attributes'].get('publicLink') or ''
            if LINK_RE.match(link):
                said.append('the public link turned on')
            else:
                note('error', 'The public link is on, but Apple didn\'t say what it is (%r): it\'s looked at again next hour.' % link[:80])
        except Refused as e:
            note('error', 'Apple wouldn\'t turn the public link on (%d): %s' % (e.code, why(e)))
    if LINK_RE.match(link):
        try:
            have = json.load(open(out))
        except Exception:
            have = {}
        if have.get('testflight') != link:
            have['testflight'] = link
            with open(out, 'w') as f:
                f.write(json.dumps(have) + '\n')
            said.append('the public link put in %s: %s' % (out, link))
        else:
            said.append('the public link is in %s' % out)
    else:
        said.append('no public link until a build is approved')
    note('notice', '; '.join(said) + '.')

    done = LINK_RE.match(link) and (not newest or newest in state)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write('done=%s\n' % ('true' if done else 'false'))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1]))
    except Refused as e:
        note('error', 'App Store Connect refused a request: %s' % e)
        sys.exit(1)
