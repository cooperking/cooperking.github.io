#!/bin/bash
# Builds the app. With the Apple keys (repository secrets ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_P8 and APPLE_TEAM_ID) it
# signs it (Xcode's automatic, cloud-managed signing, through the App Store Connect API key) and sends it to App Store
# Connect, where it turns up in TestFlight a few minutes later; without them it only checks that it builds.
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
# the key checked first, with Apple's API, so a wrong secret is named on the run's page (never its value)
python3 -m venv "$RUNNER_TEMP/py" && "$RUNNER_TEMP/py/bin/pip" install --quiet pyjwt cryptography \
  && "$RUNNER_TEMP/py/bin/python" "$HERE/asc_check.py"
KEY="$RUNNER_TEMP/AuthKey_$ASC_KEY_ID.p8"
printf '%s\n' "$ASC_KEY_P8" > "$KEY"
AUTH=(-allowProvisioningUpdates -authenticationKeyPath "$KEY" -authenticationKeyID "$ASC_KEY_ID" -authenticationKeyIssuerID "$ASC_ISSUER_ID")
xcb archive xcodebuild -workspace App.xcworkspace -scheme App -configuration Release -destination 'generic/platform=iOS' -archivePath "$RUNNER_TEMP/App.xcarchive" \
  DEVELOPMENT_TEAM="$APPLE_TEAM_ID" CODE_SIGN_STYLE=Automatic CODE_SIGN_ENTITLEMENTS=App/App.entitlements \
  PRODUCT_BUNDLE_IDENTIFIER=io.github.cooperking.sotd CURRENT_PROJECT_VERSION="$BUILD" MARKETING_VERSION=1.0 \
  "${AUTH[@]}" archive
cat > "$RUNNER_TEMP/ExportOptions.plist" <<X
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyLists-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>upload</string>
  <key>teamID</key><string>$APPLE_TEAM_ID</string>
  <key>signingStyle</key><string>automatic</string>
  <key>uploadSymbols</key><true/>
  <key>manageAppVersionAndBuildNumber</key><false/>
</dict></plist>
X
xcb export xcodebuild -exportArchive -archivePath "$RUNNER_TEMP/App.xcarchive" -exportOptionsPlist "$RUNNER_TEMP/ExportOptions.plist" -exportPath "$RUNNER_TEMP/out" "${AUTH[@]}"
rm -f "$KEY"
echo "::notice title=iPhone app::Build $BUILD sent to App Store Connect: it shows in TestFlight once Apple has processed it (usually 5 to 15 minutes)."
