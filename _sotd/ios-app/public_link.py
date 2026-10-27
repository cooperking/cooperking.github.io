#!/usr/bin/env python3
"""TestFlight's public link for the family's group, into the site, through the App Store Connect API (the upload key):

    python public_link.py OUT      OUT: songs/ios.json

Once Apple has approved a build for the external group (SOTD), turns on the group's public link (anyone with it can
get the app, up to TestFlight's 10,000 testers; the calendar's members still sign in with their own invitation) and
writes OUT as {"testflight": "https://testflight.apple.com/join/..."}. Invitations read it from there: an iPhone opening
one is shown the steps for the app (the Worker's invitation page). Until a build is approved it says where the builds
are with Apple's review, and changes nothing. A link that's on already is only written down.

Needs ASC_KEY_ID, ASC_ISSUER_ID and ASC_KEY_P8 in the environment, and PyJWT with cryptography.
"""
import json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

BUNDLE = 'io.github.cooperking.sotd'
GROUP = 'SOTD'
LINK_RE = re.compile(r'^https://testflight\.apple\.com/join/\w+$')


def note(level, m):
    print('::%s title=iPhone app (public link)::%s' % (level, m.replace('%', '%25').replace('\n', '%0A')), flush=True)


class Refused(Exception):
    def __init__(self, code, text):
        super().__init__('%d %s' % (code, text[:500])); self.code, self.text = code, text


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

    def write(link):
        try:
            have = json.load(open(out))
        except Exception:
            have = {}
        if have.get('testflight') == link:
            note('notice', 'The public link is on, and in %s already: %s' % (out, link))
            return 0
        have['testflight'] = link
        with open(out, 'w') as f:
            f.write(json.dumps(have) + '\n')
        note('notice', 'The public link, into %s: %s' % (out, link))
        return 0

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
    a = g['attributes']
    if a.get('publicLinkEnabled') and LINK_RE.match(a.get('publicLink') or ''):
        return write(a['publicLink'])

    # a build approved for the group: the public link can go on
    builds = [b for b in api('GET', '/v1/betaGroups/%s/builds?limit=50' % g['id']).get('data', []) if not b['attributes'].get('expired')]
    if not builds:
        note('notice', 'No build in %s yet: no public link until one is there and approved.' % a.get('name'))
        return 0
    num = {b['id']: b['attributes'].get('version') or '?' for b in builds}
    state = {}
    for bid in num:
        subs = api('GET', '/v1/betaAppReviewSubmissions?filter[build]=%s' % bid).get('data', [])
        if subs:
            state[bid] = subs[0]['attributes'].get('betaReviewState') or '?'
    if not any(v == 'APPROVED' for v in state.values()):
        said = ', '.join('build %s: %s' % (num[b], state.get(b, 'not submitted')) for b in sorted(num, key=lambda b: int(num[b]) if str(num[b]).isdigit() else 0))
        note('notice', 'No build approved by Apple yet (%s): the public link waits.' % said)
        return 0
    try:
        r = api('PATCH', '/v1/betaGroups/' + g['id'], {'data': {'type': 'betaGroups', 'id': g['id'], 'attributes': {'publicLinkEnabled': True}}})
    except Refused as e:
        note('error', 'Apple wouldn\'t turn the public link on (%d): %s' % (e.code, e.text[:300]))
        return 1
    link = ((r.get('data') or {}).get('attributes') or {}).get('publicLink') or ''
    if not LINK_RE.match(link):   # (sometimes only there a moment later)
        time.sleep(5)
        link = (api('GET', '/v1/betaGroups/' + g['id'])['data']['attributes'].get('publicLink')) or ''
    if not LINK_RE.match(link):
        note('error', 'The public link is on, but Apple didn\'t say what it is (%r): run this again.' % link[:80])
        return 1
    return write(link)


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1]))
    except Refused as e:
        note('error', 'App Store Connect refused a request: %s' % e)
        sys.exit(1)
