#!/bin/bash
# Builds the app. With the Apple keys (repository secrets ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_P8 and APPLE_TEAM_ID) it
# signs it for the App Store with a certificate and profile made for this build through the App Store Connect API
# (signing.py: no device needs registering, nothing is kept between builds) and sends it to App Store Connect, where it
# turns up in TestFlight a few minutes later; without them it only checks that it builds.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/ios/App"
BUILD="${BUILD_NUMBER:-1}"
if [ -z "${ASC_KEY_ID:-}" ] || [ -z "${ASC_ISSUER_ID:-}" ] || [ -z "${ASC_KEY_P8:-}" ] || [ -z "${APPLE_TEAM_ID:-}" ]; then
  echo "::warning title=iPhone app::No Apple keys yet (ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_P8, APPLE_TEAM_ID): built for the simulator only, to check it builds."
  xcodebuild -workspace App.xcworkspace -scheme App -configuration Release -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
    CODE_SIGNING_ALLOWED=NO build | tail -n 25
  exit 0
fi
# xcodebuild, its output kept; when it fails, its error lines (and its last lines) are put on the run's page, as the job's
# own log can't always be read
xcb() {
  local log="$RUNNER_TEMP/xcodebuild-$1.log"; shift
  if "$@" > "$log" 2>&1; then tail -n 40 "$log"; return 0; fi
  local errs; errs=$(grep -E 'error:|error :|Error Domain|No profiles|provisioning profile|no devices|Signing for|requires a development team|certificate' "$log" | sed 's/^[[:space:]]*//' | awk '!seen[$0]++' | head -20 | cut -c1-300 | awk 'BEGIN{ORS="%0A"} {gsub(/%/,"%25"); print}')
  echo "::error title=iPhone app (xcodebuild failed)::${errs:-no error lines}"
  echo "::error title=iPhone app (its last lines)::$(tail -n 15 "$log" | cut -c1-300 | awk 'BEGIN{ORS="%0A"} {gsub(/%/,"%25"); print}')"
  tail -n 80 "$log"
  exit 65
}
KEY="$RUNNER_TEMP/AuthKey_$ASC_KEY_ID.p8"
printf '%s\n' "$ASC_KEY_P8" > "$KEY"
AUTH=(-authenticationKeyPath "$KEY" -authenticationKeyID "$ASC_KEY_ID" -authenticationKeyIssuerID "$ASC_ISSUER_ID")

# the certificate and profile (signing.py), into a keychain of this build's own
SIGN="$RUNNER_TEMP/sign"; mkdir -p "$SIGN"
[ -x "$RUNNER_TEMP/py/bin/python" ] || { python3 -m venv "$RUNNER_TEMP/py" && "$RUNNER_TEMP/py/bin/pip" install --quiet pyjwt cryptography; }
"$RUNNER_TEMP/py/bin/python" "$HERE/signing.py" "$SIGN"
PROFILE_NAME=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['name'])" "$SIGN/out.json")
PROFILE_UUID=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['uuid'])" "$SIGN/out.json")
KC="$RUNNER_TEMP/sotd.keychain-db"; KCPW=$(uuidgen)
security create-keychain -p "$KCPW" "$KC"
security set-keychain-settings -lut 21600 "$KC"
security unlock-keychain -p "$KCPW" "$KC"
/usr/bin/openssl pkcs12 -export -inkey "$SIGN/key.pem" -in "$SIGN/cert.pem" -out "$SIGN/cert.p12" -passout pass:"$KCPW"
security import "$SIGN/cert.p12" -k "$KC" -P "$KCPW" -T /usr/bin/codesign -T /usr/bin/security -T /usr/bin/productbuild
curl -sSfo "$SIGN/wwdr.cer" https://www.apple.com/certificateauthority/AppleWWDRCAG3.cer && security import "$SIGN/wwdr.cer" -k "$KC" || true
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KCPW" "$KC" > /dev/null
security list-keychains -d user -s "$KC" $(security list-keychains -d user | tr -d '"')
trap 'security delete-keychain "$KC" 2>/dev/null || true; rm -f "$KEY"; rm -rf "$SIGN"' EXIT
echo "identities: $(security find-identity -v -p codesigning "$KC" | tail -n 1)"
for d in "$HOME/Library/MobileDevice/Provisioning Profiles" "$HOME/Library/Developer/Xcode/UserData/Provisioning Profiles"; do
  mkdir -p "$d"; cp "$SIGN/profile.mobileprovision" "$d/$PROFILE_UUID.mobileprovision"
done
# the app's target signed by hand with them (in the project, so the Pods' own targets aren't given the profile)
python3 - "$PROFILE_NAME" "$APPLE_TEAM_ID" <<'X'
import re, sys
name, team = sys.argv[1], sys.argv[2]
p = 'App.xcodeproj/project.pbxproj'
s = open(p).read()
n = 0
def fix(m):
    global n
    body = m.group(0)
    if 'PRODUCT_BUNDLE_IDENTIFIER' not in body:
        return body
    n += 1
    for k in ('CODE_SIGN_STYLE', 'DEVELOPMENT_TEAM', 'CODE_SIGN_IDENTITY', '"CODE_SIGN_IDENTITY[sdk=iphoneos*]"', 'PROVISIONING_PROFILE_SPECIFIER',
              'PROVISIONING_PROFILE', 'CODE_SIGN_ENTITLEMENTS'):
        body = re.sub(r'\n\s*' + re.escape(k) + r' = [^;]*;', '', body)
    add = ('\n\t\t\t\tCODE_SIGN_STYLE = Manual;\n\t\t\t\tDEVELOPMENT_TEAM = %s;\n\t\t\t\tCODE_SIGN_IDENTITY = "Apple Distribution";'
           '\n\t\t\t\t"CODE_SIGN_IDENTITY[sdk=iphoneos*]" = "Apple Distribution";\n\t\t\t\tPROVISIONING_PROFILE_SPECIFIER = "%s";'
           '\n\t\t\t\tCODE_SIGN_ENTITLEMENTS = App/App.entitlements;' % (team, name))
    return body.replace('buildSettings = {', 'buildSettings = {' + add, 1)
s = re.sub(r'buildSettings = \{.*?\n\t\t\t\};', fix, s, flags=re.S)
if not n:
    sys.exit('The app target\'s build settings weren\'t found in the project.')
open(p, 'w').write(s)
print('signing set on %d of the app\'s configurations' % n)
X
xcb archive xcodebuild -workspace App.xcworkspace -scheme App -configuration Release -destination 'generic/platform=iOS' -archivePath "$RUNNER_TEMP/App.xcarchive" \
  CURRENT_PROJECT_VERSION="$BUILD" MARKETING_VERSION=1.0 OTHER_CODE_SIGN_FLAGS="--keychain $KC" archive
cat > "$RUNNER_TEMP/ExportOptions.plist" <<X
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyLists-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>upload</string>
  <key>teamID</key><string>$APPLE_TEAM_ID</string>
  <key>signingStyle</key><string>manual</string>
  <key>signingCertificate</key><string>Apple Distribution</string>
  <key>provisioningProfiles</key><dict><key>io.github.cooperking.sotd</key><string>$PROFILE_NAME</string></dict>
  <key>uploadSymbols</key><true/>
  <key>manageAppVersionAndBuildNumber</key><false/>
</dict></plist>
X
xcb export xcodebuild -exportArchive -archivePath "$RUNNER_TEMP/App.xcarchive" -exportOptionsPlist "$RUNNER_TEMP/ExportOptions.plist" -exportPath "$RUNNER_TEMP/out" "${AUTH[@]}"
echo "::notice title=iPhone app::Build $BUILD sent to App Store Connect: it shows in TestFlight once Apple has processed it (usually 5 to 15 minutes)."
