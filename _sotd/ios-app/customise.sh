#!/bin/bash
# After `npx cap add ios`: the app's own settings, which Capacitor's template doesn't have. Run by .github/workflows/ios.yml.
#  - background audio (a song carries on with the phone locked) and remote notifications
#  - App-Bound Domains: the calendar's own sites, so its service worker (offline downloads) works in the app's browser
#  - Apple's notifications: the entitlement, and the AppDelegate handing the device's token to the push plugin
#  - no export-compliance question in App Store Connect (only standard HTTPS); the icon; the name under the icon
set -euo pipefail
cd "$(dirname "$0")"
P=ios/App/App/Info.plist
pb() { /usr/libexec/PlistBuddy -c "$1" "$P"; }
pb "Delete :UIBackgroundModes" 2>/dev/null || true
pb "Add :UIBackgroundModes array"; pb "Add :UIBackgroundModes:0 string audio"; pb "Add :UIBackgroundModes:1 string remote-notification"
pb "Delete :WKAppBoundDomains" 2>/dev/null || true
pb "Add :WKAppBoundDomains array"
i=0; for d in cooperking.github.io sotd-playlists.cooperking.workers.dev www.youtube.com youtube.com m.youtube.com; do pb "Add :WKAppBoundDomains:$i string $d"; i=$((i+1)); done
pb "Delete :ITSAppUsesNonExemptEncryption" 2>/dev/null || true
pb "Add :ITSAppUsesNonExemptEncryption bool false"
pb "Set :CFBundleDisplayName Song of the Day" 2>/dev/null || pb "Add :CFBundleDisplayName string Song of the Day"

cat > ios/App/App/App.entitlements <<'X'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyLists-1.0.dtd">
<plist version="1.0">
<dict>
	<key>aps-environment</key>
	<string>production</string>
</dict>
</plist>
X

python3 - <<'X'
import re
p = 'ios/App/App/AppDelegate.swift'
s = open(p).read()
if 'import AVFoundation' not in s:
    s = s.replace('import UIKit', 'import UIKit\nimport AVFoundation', 1)
# the audio session: playback, so it plays with the ringer switch off and carries on in the background
s = re.sub(r'(didFinishLaunchingWithOptions[^{]*\{)', r'\1\n        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .default)\n        try? AVAudioSession.sharedInstance().setActive(true)', s, count=1)
if 'didRegisterForRemoteNotificationsWithDeviceToken' not in s:
    i = s.rstrip().rfind('}')
    s = s[:i] + '''
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        NotificationCenter.default.post(name: .capacitorDidRegisterForRemoteNotifications, object: deviceToken)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        NotificationCenter.default.post(name: .capacitorDidFailToRegisterForRemoteNotifications, object: error)
    }
}
'''
open(p, 'w').write(s)
print(open(p).read()[:1200])
X

cp icon-1024.png ios/App/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png

# iOS 15 at least: App Store Connect warns about 14 (ITMS-90068, 26 Oct 2026) and from April 2027 refuses anything lower.
# Only raised, never lowered (a later Capacitor may start higher); the Podfile changes before `cap sync` runs pod install.
sed -i '' -E "s/platform :ios, '1[0-4](\.[0-9]+)?'/platform :ios, '15.0'/" ios/App/Podfile
sed -i '' -E 's/IPHONEOS_DEPLOYMENT_TARGET = 1[0-4](\.[0-9]+)?;/IPHONEOS_DEPLOYMENT_TARGET = 15.0;/g' ios/App/App.xcodeproj/project.pbxproj
if grep -Eq "platform :ios, '1[0-4]" ios/App/Podfile || grep -Eq 'IPHONEOS_DEPLOYMENT_TARGET = 1[0-4]' ios/App/App.xcodeproj/project.pbxproj; then
  echo "::error title=iPhone app::The deployment target is still below iOS 15"; exit 1
fi
echo "iOS deployment target: $(grep -Eo "platform :ios, '[0-9.]+'" ios/App/Podfile)"
echo "customised"
