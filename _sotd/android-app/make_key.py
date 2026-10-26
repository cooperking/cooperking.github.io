#!/usr/bin/env python3
"""The Android app's upload key (Google Play's "upload key"; Play then signs the app with its own), made the same every
time from a secret the repo already has (SEED, in the workflow YT_CLIENT_SECRET), as the Fire TV app's is: an RSA 2048
key (Play asks for RSA) from a random stream seeded by HMAC-SHA256(SEED). Writes a PKCS#12 keystore (alias sotd) to the
path given, protected by KS_PASS, and prints the certificate's SHA-256 fingerprint (for .well-known/assetlinks.json)."""
import datetime, hashlib, hmac, os, sys
from Crypto.PublicKey import RSA
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

seed = hmac.new(os.environ['SEED'].encode(), b'sotd-android upload key 1', hashlib.sha256).digest()
state = {'n': 0}
def stream(k):   # a deterministic byte stream: SHA-256 of the seed and a counter
    out = b''
    while len(out) < k:
        out += hashlib.sha256(seed + state['n'].to_bytes(8, 'big')).digest(); state['n'] += 1
    return out[:k]
rsa = RSA.generate(2048, randfunc=stream)
key = serialization.load_pem_private_key(rsa.export_key('PEM'), None)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "John's Song of the Day")])
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
        .not_valid_before(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(2076, 1, 1, tzinfo=datetime.timezone.utc))
        .sign(key, hashes.SHA256()))
p12 = pkcs12.serialize_key_and_certificates(b'sotd', key, cert, None, serialization.BestAvailableEncryption(os.environ['KS_PASS'].encode()))
open(sys.argv[1], 'wb').write(p12)
fp = cert.fingerprint(hashes.SHA256()).hex().upper()
print(':'.join(fp[i:i + 2] for i in range(0, len(fp), 2)))
