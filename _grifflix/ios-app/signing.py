#!/usr/bin/env python3
"""What Grifflix's iPhone app is signed with, through the App Store Connect API (the upload key), so no device needs
registering. Made from the songs' app's own (_sotd/ios-app/signing.py), with Grifflix's app ID and profile:

    python signing.py OUTDIR      writes OUTDIR/key.pem, cert.pem, profile.mobileprovision and out.json
                                  ({"name", "uuid", "links", "profiles": {bundle ID: {"name", "uuid", "file"}}})

  1. the certificate: the same Apple Distribution certificate the songs' app's builds keep (in the R2 bucket,
     ci/ios-signing.bin, encrypted with a key from the upload key's .p8), so the two apps share one and the account's
     few are never used up; a new one only when it's gone or has under 30 days left (then kept for both);
  2. the app's ID (io.github.cooperking.grifflix), registered the first time if it isn't yet;
  3. an App Store provisioning profile for it with that certificate: the one there if active, else a new one.

Needs ASC_KEY_ID, ASC_ISSUER_ID and ASC_KEY_P8, and R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET and
CLOUDFLARE_ACCOUNT_ID; PyJWT, cryptography, boto3.
"""
import base64, datetime, hashlib, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

BUNDLE = 'io.github.cooperking.grifflix'
PROFILE = 'Grifflix App Store (build)'
EXTRAS = {}
KEPT = 'ci/ios-signing.bin'   # (the songs' app's certificate, shared: the same key and the same encryption)


def note(level, m):
    print('::%s title=iPhone app (signing)::%s' % (level, m.replace('%', '%25').replace('\n', '%0A')), flush=True)


class Refused(Exception):
    def __init__(self, code, text):
        super().__init__('%d %s' % (code, text[:600])); self.code, self.text = code, text


def store():
    """(get, put) for the kept certificate in R2, or (None, None) without the R2 secrets."""
    kid, sec, bucket, acct = (os.environ.get(k, '').strip() for k in ('R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET', 'CLOUDFLARE_ACCOUNT_ID'))
    if not (kid and sec and bucket and acct):
        return None, None
    import boto3
    from botocore.config import Config
    s3 = boto3.client('s3', endpoint_url=os.environ.get('R2_ENDPOINT') or 'https://%s.r2.cloudflarestorage.com' % acct,
                      aws_access_key_id=kid, aws_secret_access_key=sec, region_name='auto', config=Config(signature_version='s3v4'))

    def get():
        try:
            return s3.get_object(Bucket=bucket, Key=KEPT)['Body'].read()
        except Exception:
            return None

    def put(b):
        s3.put_object(Bucket=bucket, Key=KEPT, Body=b, ContentType='application/octet-stream')
    return get, put


def seal(p8, data, open_=False):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    k = AESGCM(hashlib.sha256(b'sotd-ios-signing|' + p8.strip().encode()).digest())
    if open_:
        return k.decrypt(data[:12], data[12:], None)
    n = os.urandom(12)
    return n + k.encrypt(n, data, None)


def main(out):
    import jwt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    kid, iss, p8 = os.environ['ASC_KEY_ID'].strip(), os.environ['ASC_ISSUER_ID'].strip(), os.environ['ASC_KEY_P8']
    tok = jwt.encode({'iss': iss, 'iat': int(time.time()), 'exp': int(time.time()) + 1100, 'aud': 'appstoreconnect-v1'},
                     p8, algorithm='ES256', headers={'kid': kid, 'typ': 'JWT'})

    def api(method, path, body=None, soft=False):
        rq = urllib.request.Request('https://api.appstoreconnect.apple.com' + path, method=method,
                                    data=json.dumps(body).encode() if body is not None else None,
                                    headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(rq, timeout=60) as r:
                t = r.read()
                return json.loads(t) if t else {}
        except urllib.error.HTTPError as e:
            t = e.read().decode('utf-8', 'replace')
            if soft:
                raise Refused(e.code, t)
            raise SystemExit(note('error', '%s %s: %d %s' % (method, path.split('?')[0], e.code, t[:600])) or 1)

    now = datetime.datetime.now(datetime.timezone.utc)
    days_left = lambda a: (datetime.datetime.fromisoformat(a['expirationDate'].replace('Z', '+00:00')) - now).days if a.get('expirationDate') else 0
    get, put = store()

    # 1. the certificate: the kept one, or a new one
    cert = key_pem = cert_pem = None
    kept = get() if get else None
    if kept:
        try:
            k = json.loads(seal(p8, kept, open_=True))
            c = api('GET', '/v1/certificates/' + k['id'], soft=True)['data']
            if days_left(c['attributes']) >= 30:
                cert, key_pem, cert_pem = c, k['key'].encode(), k['cert'].encode()
        except Refused:
            pass   # (revoked, or gone: a new one)
        except Exception as e:
            note('warning', 'The kept certificate couldn\'t be read (%s): making a new one.' % type(e).__name__)
    made = False
    if not cert:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        csr = (x509.CertificateSigningRequestBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'SotD build'), x509.NameAttribute(NameOID.COUNTRY_NAME, 'GB')]))
               .sign(key, hashes.SHA256()))
        body = {'data': {'type': 'certificates', 'attributes': {'certificateType': 'DISTRIBUTION',
                                                               'csrContent': csr.public_bytes(serialization.Encoding.PEM).decode()}}}
        try:
            cert = api('POST', '/v1/certificates', body, soft=True)['data']
        except Refused as e:   # the account's limit: the oldest distribution certificate goes, once
            old = sorted((c for c in api('GET', '/v1/certificates?limit=200').get('data', [])
                          if c['attributes'].get('certificateType') in ('DISTRIBUTION', 'IOS_DISTRIBUTION')),
                         key=lambda c: c['attributes'].get('expirationDate') or '')
            if not old:
                raise SystemExit(note('error', 'Apple refused a new distribution certificate: %s' % e) or 1)
            api('DELETE', '/v1/certificates/' + old[0]['id'])
            note('warning', 'Apple allows only a few distribution certificates: the oldest (until %s) was revoked to make room.'
                 % (old[0]['attributes'].get('expirationDate') or '?')[:10])
            cert = api('POST', '/v1/certificates', body)['data']
        key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
        cert_pem = x509.load_der_x509_certificate(base64.b64decode(cert['attributes']['certificateContent'])).public_bytes(serialization.Encoding.PEM)
        made = True
        if put:
            try:
                put(seal(p8, json.dumps({'id': cert['id'], 'key': key_pem.decode(), 'cert': cert_pem.decode()}).encode()))
            except Exception as e:
                put = None
                note('warning', 'The new certificate couldn\'t be kept in R2 (%s): the next build makes another.' % type(e).__name__)
    open(os.path.join(out, 'key.pem'), 'wb').write(key_pem)
    open(os.path.join(out, 'cert.pem'), 'wb').write(cert_pem)

    # 2. the app's ID: registered the first time
    bid = [b['id'] for b in api('GET', '/v1/bundleIds?filter[identifier]=%s&limit=200' % BUNDLE).get('data', [])
           if b['attributes'].get('identifier') == BUNDLE]
    if not bid:
        bid = [api('POST', '/v1/bundleIds', {'data': {'type': 'bundleIds', 'attributes': {'identifier': BUNDLE, 'name': 'Grifflix', 'platform': 'IOS'}}})['data']['id']]
        note('notice', 'The app ID %s registered.' % BUNDLE)
    ids, links, switched, wpush, wswitched = {BUNDLE: bid[0]}, False, False, False, False

    # 4. each one's profile: the one there, if it has this certificate and is active (the app's: carrying Associated
    # Domains too), else a new one
    profiles = {}
    for b, name in [(BUNDLE, PROFILE)] + [(b, n) for b, (n, _) in EXTRAS.items()]:
        prof = None
        for p in api('GET', '/v1/profiles?filter[name]=%s&include=certificates&limit=50' % urllib.parse.quote(name)).get('data', []):
            if p['attributes'].get('name') != name:
                continue
            certs = [c['id'] for c in ((p.get('relationships') or {}).get('certificates') or {}).get('data') or []]
            has = p['attributes'].get('profileContent') or ''
            if (not prof and not (switched and b == BUNDLE) and not (wswitched and b == BUNDLE + '.widget')
                    and p['attributes'].get('profileState') == 'ACTIVE' and cert['id'] in certs and has
                    and (b != BUNDLE or not links or b'com.apple.developer.associated-domains' in base64.b64decode(has))
                    and (b != BUNDLE + '.widget' or not wpush or b'aps-environment' in base64.b64decode(has))):
                prof = p
            else:
                api('DELETE', '/v1/profiles/' + p['id'])
        if not prof:
            prof = api('POST', '/v1/profiles', {'data': {'type': 'profiles', 'attributes': {'name': name, 'profileType': 'IOS_APP_STORE'},
                                                         'relationships': {'bundleId': {'data': {'type': 'bundleIds', 'id': ids[b]}},
                                                                           'certificates': {'data': [{'type': 'certificates', 'id': cert['id']}]}}}})['data']
        content = base64.b64decode(prof['attributes']['profileContent'])
        file = 'profile.mobileprovision' if b == BUNDLE else 'profile-%s.mobileprovision' % b.rsplit('.', 1)[-1]
        open(os.path.join(out, file), 'wb').write(content)
        uuid = prof['attributes'].get('uuid') or re.search(rb'<key>UUID</key>\s*<string>([^<]+)</string>', content).group(1).decode()
        profiles[b] = {'name': name, 'uuid': uuid, 'file': file}
    note('notice', '%s distribution certificate (until %s)%s, and the App Store profiles: %s.'
         % ('A new' if made else 'The kept', (cert['attributes'].get('expirationDate') or '?')[:10],
            '' if not made else (', kept for the next builds' if put else ' (not kept: no R2 secrets)'), ', '.join('"%s"' % p['name'] for p in profiles.values())))
    json.dump({'name': PROFILE, 'uuid': profiles[BUNDLE]['uuid'], 'links': links, 'widgetPush': wpush, 'profiles': profiles}, open(os.path.join(out, 'out.json'), 'w'))

if __name__ == '__main__':
    main(sys.argv[1])
