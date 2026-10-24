#!/usr/bin/env python3
"""The app's signing key, made the same every time from a secret the repo already has (SEED, in the workflow
YT_CLIENT_SECRET), so each build installs over the last without a key of its own being kept anywhere: an EC P-256
key from HMAC-SHA256(SEED), in a certificate with fixed dates and a deterministic signature. Writes a PKCS#12
keystore (alias sotd) to the path given, protected by KS_PASS, and prints the certificate's SHA-256 fingerprint.
If SEED changes, so does the key, and the next build has to be installed fresh (uninstall the old one first)."""
import datetime, hashlib, hmac, os, sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551   # P-256's group order
seed = hmac.new(os.environ['SEED'].encode(), b'sotd-tv signing key 1', hashlib.sha256).digest()
key = ec.derive_private_key(int.from_bytes(seed, 'big') % (ORDER - 1) + 1, ec.SECP256R1())
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "John's Song of the Day")])
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
        .not_valid_before(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(2076, 1, 1, tzinfo=datetime.timezone.utc))
        .sign(key, hashes.SHA256(), ecdsa_deterministic=True))
p12 = pkcs12.serialize_key_and_certificates(b'sotd', key, cert, None,
                                            serialization.BestAvailableEncryption(os.environ['KS_PASS'].encode()))
open(sys.argv[1], 'wb').write(p12)
print('signing certificate SHA-256', cert.fingerprint(hashes.SHA256()).hex())
