#!/usr/bin/env python3
"""Makes what the build signs the app with, through the App Store Connect API (the upload key), so nothing is kept
between builds and no device needs registering:

    python signing.py OUTDIR      writes OUTDIR/key.pem, cert.pem, profile.mobileprovision and out.json ({"name", "uuid"})

  1. the team's distribution certificates (made by earlier builds) are revoked: an account can only have a few, and
     revoking one doesn't touch builds already in TestFlight or on the App Store;
  2. a new private key and certificate request here, and an Apple Distribution certificate from it;
  3. an App Store provisioning profile for io.github.cooperking.sotd with that certificate (the earlier one deleted).

Needs ASC_KEY_ID, ASC_ISSUER_ID and ASC_KEY_P8 in the environment, and PyJWT with cryptography.
"""
import base64, json, os, re, sys, time, urllib.error, urllib.request

BUNDLE = 'io.github.cooperking.sotd'
PROFILE = 'SotD App Store (build)'


def note(level, m):
    print('::%s title=iPhone app (signing)::%s' % (level, m.replace('%', '%25').replace('\n', '%0A')), flush=True)


def main(out):
    import jwt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    kid, iss, p8 = os.environ['ASC_KEY_ID'].strip(), os.environ['ASC_ISSUER_ID'].strip(), os.environ['ASC_KEY_P8']
    tok = jwt.encode({'iss': iss, 'iat': int(time.time()), 'exp': int(time.time()) + 1100, 'aud': 'appstoreconnect-v1'},
                     p8, algorithm='ES256', headers={'kid': kid, 'typ': 'JWT'})

    def api(method, path, body=None):
        rq = urllib.request.Request('https://api.appstoreconnect.apple.com' + path, method=method,
                                    data=json.dumps(body).encode() if body is not None else None,
                                    headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(rq, timeout=60) as r:
                t = r.read()
                return json.loads(t) if t else {}
        except urllib.error.HTTPError as e:
            t = e.read().decode('utf-8', 'replace')
            raise SystemExit(note('error', '%s %s: %d %s' % (method, path.split('?')[0], e.code, t[:600])) or 1)

    # 1. earlier distribution certificates and this build's profile name
    for c in api('GET', '/v1/certificates?limit=200').get('data', []):
        if c['attributes'].get('certificateType') in ('DISTRIBUTION', 'IOS_DISTRIBUTION'):
            api('DELETE', '/v1/certificates/' + c['id'])
    for p in api('GET', '/v1/profiles?limit=200').get('data', []):
        if p['attributes'].get('name') == PROFILE:
            api('DELETE', '/v1/profiles/' + p['id'])
    # 2. a key, a request, the certificate
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'SotD build'), x509.NameAttribute(NameOID.COUNTRY_NAME, 'GB')]))
           .sign(key, hashes.SHA256()))
    cert = api('POST', '/v1/certificates', {'data': {'type': 'certificates', 'attributes': {
        'certificateType': 'DISTRIBUTION', 'csrContent': csr.public_bytes(serialization.Encoding.PEM).decode()}}})['data']
    der = base64.b64decode(cert['attributes']['certificateContent'])
    open(os.path.join(out, 'key.pem'), 'wb').write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                                                         serialization.NoEncryption()))
    open(os.path.join(out, 'cert.pem'), 'wb').write(x509.load_der_x509_certificate(der).public_bytes(serialization.Encoding.PEM))
    # 3. the profile
    bid = [b['id'] for b in api('GET', '/v1/bundleIds?filter[identifier]=%s&limit=200' % BUNDLE).get('data', [])
           if b['attributes'].get('identifier') == BUNDLE]
    if not bid:
        raise SystemExit(note('error', 'The bundle ID %s isn\'t registered.' % BUNDLE) or 1)
    prof = api('POST', '/v1/profiles', {'data': {'type': 'profiles', 'attributes': {'name': PROFILE, 'profileType': 'IOS_APP_STORE'},
                                                 'relationships': {'bundleId': {'data': {'type': 'bundleIds', 'id': bid[0]}},
                                                                   'certificates': {'data': [{'type': 'certificates', 'id': cert['id']}]}}}})['data']
    content = base64.b64decode(prof['attributes']['profileContent'])
    open(os.path.join(out, 'profile.mobileprovision'), 'wb').write(content)
    uuid = prof['attributes'].get('uuid') or re.search(rb'<key>UUID</key>\s*<string>([^<]+)</string>', content).group(1).decode()
    note('notice', 'A distribution certificate (until %s) and the App Store profile "%s" made for this build.'
         % (cert['attributes'].get('expirationDate', '?')[:10], PROFILE))
    json.dump({'name': PROFILE, 'uuid': uuid}, open(os.path.join(out, 'out.json'), 'w'))


if __name__ == '__main__':
    main(sys.argv[1])
