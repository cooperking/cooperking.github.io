#!/bin/bash
# After `npx cap add ios`: the app's own settings, which Capacitor's template doesn't have. Run by .github/workflows/ios.yml.
#  - background audio (a song carries on with the phone locked) and remote notifications
#  - App-Bound Domains: the calendar's own sites, so its service worker (offline downloads) works in the app's browser
#  - Apple's notifications: the entitlement, and the AppDelegate handing the device's token to the push plugin
#  - Associated Domains: an invitation's link (sotd-playlists.cooperking.workers.dev/i/...) opens the app, which signs in
#    with it (the Worker serves Apple's link file; build.sh takes the entitlement out if the app's ID hasn't got it)
#  - no export-compliance question in App Store Connect (only standard HTTPS); the icon; the name under the icon
#  - the app's own native parts (native/): Chromecast (Google's Cast kit, which asks once for the local network), Siri,
#    the Home Screen widget and the notifications' pictures, put into the project by native/project.rb
#  - light status bar text, on the calendar's dark colours
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
pb "Delete :UIStatusBarStyle" 2>/dev/null || true
pb "Add :UIStatusBarStyle string UIStatusBarStyleLightContent"
# Chromecast: the iPhone's question before the app may look on the Wi-Fi, and what it looks for (Google's default receiver)
pb "Delete :NSLocalNetworkUsageDescription" 2>/dev/null || true
pb "Add :NSLocalNetworkUsageDescription string Song of the Day looks on your Wi-Fi for Chromecasts and Google speakers, so you can play songs on them."
pb "Delete :NSBonjourServices" 2>/dev/null || true
pb "Add :NSBonjourServices array"; pb "Add :NSBonjourServices:0 string _googlecast._tcp"; pb "Add :NSBonjourServices:1 string _CC1AD845._googlecast._tcp"

cat > ios/App/App/App.entitlements <<'X'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyLists-1.0.dtd">
<plist version="1.0">
<dict>
	<key>aps-environment</key>
	<string>production</string>
	<key>com.apple.developer.associated-domains</key>
	<array>
		<string>applinks:sotd-playlists.cooperking.workers.dev</string>
		<string>applinks:cooperking.github.io</string>
	</array>
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
s = re.sub(r'(didFinishLaunchingWithOptions[^{]*\{)', r'\1\n        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .default)\n        try? AVAudioSession.sharedInstance().setActive(true)\n        SotdCastPlugin.setUp()', s, count=1)
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

# the app's own native parts: copied in, put into the project, the storyboard on the app's own view controller (it adds
# the plugins), and Google's Cast kit in the Podfile (installed by `cap sync`, next)
cp native/App/*.swift ios/App/App/
mkdir -p ios/App/SotdWidget ios/App/SotdNotify
cp native/SotdWidget/* ios/App/SotdWidget/
cp native/SotdNotify/* ios/App/SotdNotify/
ruby -e "require 'xcodeproj'" 2>/dev/null || gem install --user-install --no-document xcodeproj
ruby native/project.rb
python3 - <<'Y'
p = 'ios/App/App/Base.lproj/Main.storyboard'
s = open(p).read()
a = 'customClass="CAPBridgeViewController" customModule="Capacitor"'
if a not in s and 'SOTDViewController' not in s:
    raise SystemExit("Main.storyboard: Capacitor's view controller wasn't found")
s = s.replace(a, 'customClass="SOTDViewController" customModule="App" customModuleProvider="target"')
open(p, 'w').write(s)
p = 'ios/App/Podfile'
s = open(p).read()
if 'google-cast-sdk' not in s:
    if '# Add your Pods here' in s:
        s = s.replace('# Add your Pods here', "pod 'google-cast-sdk', '~> 4.8'", 1)
    else:
        i = s.index("target 'App' do")
        j = s.index('\n', i)
        s = s[:j + 1] + "  pod 'google-cast-sdk', '~> 4.8'\n" + s[j + 1:]
    open(p, 'w').write(s)
print('storyboard and Podfile set')
Y

# iOS 15 at least: App Store Connect warns about 14 (ITMS-90068, 26 Oct 2026) and from April 2027 refuses anything lower.
# Only raised, never lowered (a later Capacitor may start higher); the Podfile changes before `cap sync` runs pod install.
sed -i '' -E "s/platform :ios, '1[0-4](\.[0-9]+)?'/platform :ios, '15.0'/" ios/App/Podfile
sed -i '' -E 's/IPHONEOS_DEPLOYMENT_TARGET = 1[0-4](\.[0-9]+)?;/IPHONEOS_DEPLOYMENT_TARGET = 15.0;/g' ios/App/App.xcodeproj/project.pbxproj
if grep -Eq "platform :ios, '1[0-4]" ios/App/Podfile || grep -Eq 'IPHONEOS_DEPLOYMENT_TARGET = 1[0-4]' ios/App/App.xcodeproj/project.pbxproj; then
  echo "::error title=iPhone app::The deployment target is still below iOS 15"; exit 1
fi
echo "iOS deployment target: $(grep -Eo "platform :ios, '[0-9.]+'" ios/App/Podfile)"
echo "customised"
