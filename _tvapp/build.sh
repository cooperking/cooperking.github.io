#!/usr/bin/env bash
# Builds the Fire TV app with the Android SDK's own tools (no Gradle, no libraries): aapt2 for the manifest and
# pictures, javac + d8 for the code, apksigner to sign it. Run from this folder on a machine with ANDROID_HOME set
# (GitHub's runners have it). Needs VERSION_CODE, and KEYSTORE (a PKCS#12 file, alias sotd; see make_key.py) with
# KS_PASS; without a keystore the APK is signed with a throwaway key, which won't install over one signed with the real key.
set -euo pipefail
cd "$(dirname "$0")"
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
BT="$(ls -d "$SDK"/build-tools/* | sort -V | tail -1)"
PLATFORM="$(ls -d "$SDK"/platforms/android-* | sort -V | tail -1)"
JAR="$PLATFORM/android.jar"
TARGET="$(basename "$PLATFORM" | sed 's/android-//')"; [ "$TARGET" -gt 34 ] 2>/dev/null && TARGET=34   # Fire OS is Android 11 at most
VERSION_CODE="${VERSION_CODE:-1}"
echo "build-tools $(basename "$BT"), platform $TARGET, version $VERSION_CODE"
rm -rf build && mkdir -p build/gen build/classes build/dex

"$BT/aapt2" compile --dir res -o build/res.zip
"$BT/aapt2" link -o build/base.apk -I "$JAR" --manifest AndroidManifest.xml build/res.zip \
  --min-sdk-version 22 --target-sdk-version "$TARGET" \
  --version-code "$VERSION_CODE" --version-name "1.$VERSION_CODE" --java build/gen

javac -source 8 -target 8 -bootclasspath "$JAR" -classpath "$JAR" -Xlint:-options -d build/classes \
  $(find src build/gen -name '*.java')
"$BT/d8" --min-api 22 --lib "$JAR" --output build/dex $(find build/classes -name '*.class')

cp build/base.apk build/unsigned.apk
(cd build/dex && zip -q -u ../unsigned.apk classes.dex)
"$BT/zipalign" -p -f 4 build/unsigned.apk build/aligned.apk

if [ -z "${KEYSTORE:-}" ]; then
  echo "no keystore: signing with a throwaway key"
  KEYSTORE=build/throwaway.p12; export KS_PASS=throwaway
  keytool -genkeypair -storetype PKCS12 -keystore "$KEYSTORE" -storepass "$KS_PASS" -keypass "$KS_PASS" -alias sotd \
    -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Song of the Day" >/dev/null
fi
"$BT/apksigner" sign --ks "$KEYSTORE" --ks-type PKCS12 --ks-pass env:KS_PASS --ks-key-alias sotd --out build/sotd-tv.apk build/aligned.apk
"$BT/apksigner" verify --print-certs build/sotd-tv.apk > build/verify.txt && head -3 build/verify.txt
"$BT/aapt2" dump badging build/sotd-tv.apk > build/badging.txt; grep -E "^package|sdkVersion|targetSdk|leanback|application-label:|banner|launchable" build/badging.txt || true
ls -l build/sotd-tv.apk
