#!/bin/bash
# After `npx cap add ios`: Grifflix's own settings, which Capacitor's template doesn't have. Run by
# .github/workflows/grifflix-ios.yml.
#  - the name under the icon, and the icon (Grifflix's clapperboard)
#  - App-Bound Domains: Grifflix's own sites (the page, the Mac mini, the members' Worker, the trailers), so its service
#    worker (pictures kept on the phone) works in the app's browser
#  - a film's sound plays with the ringer switch off, and carries on with the screen locked or in AirPlay
#  - no export-compliance question in App Store Connect (only standard HTTPS); light status bar text; iOS 15 at least
set -euo pipefail
cd "$(dirname "$0")"
P=ios/App/App/Info.plist
pb() { /usr/libexec/PlistBuddy -c "$1" "$P"; }
pb "Set :CFBundleDisplayName Grifflix" 2>/dev/null || pb "Add :CFBundleDisplayName string Grifflix"
pb "Delete :WKAppBoundDomains" 2>/dev/null || true
pb "Add :WKAppBoundDomains array"
i=0; for d in cooperking.github.io media.grifflix.co.uk sotd-playlists.cooperking.workers.dev www.youtube-nocookie.com; do pb "Add :WKAppBoundDomains:$i string $d"; i=$((i+1)); done
pb "Delete :UIBackgroundModes" 2>/dev/null || true
pb "Add :UIBackgroundModes array"; pb "Add :UIBackgroundModes:0 string audio"
pb "Delete :ITSAppUsesNonExemptEncryption" 2>/dev/null || true
pb "Add :ITSAppUsesNonExemptEncryption bool false"
pb "Delete :UIStatusBarStyle" 2>/dev/null || true
pb "Add :UIStatusBarStyle string UIStatusBarStyleLightContent"
# films turn the phone sideways: every orientation on the iPhone too
pb "Delete :UISupportedInterfaceOrientations" 2>/dev/null || true
pb "Add :UISupportedInterfaceOrientations array"
i=0; for o in UIInterfaceOrientationPortrait UIInterfaceOrientationLandscapeLeft UIInterfaceOrientationLandscapeRight; do pb "Add :UISupportedInterfaceOrientations:$i string $o"; i=$((i+1)); done

python3 - <<'X'
p = 'ios/App/App/AppDelegate.swift'
s = open(p).read()
if 'import AVFoundation' not in s:
    s = s.replace('import UIKit', 'import UIKit\nimport AVFoundation', 1)
import re
# the audio session: playback, so a film's sound plays with the ringer switch off, and to AirPlay
s = re.sub(r'(didFinishLaunchingWithOptions[^{]*\{)', r'\1\n        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .moviePlayback)\n        try? AVAudioSession.sharedInstance().setActive(true)', s, count=1)
open(p, 'w').write(s)
print('AppDelegate: audio session set')
X

cp icon-1024.png ios/App/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png

# iOS 15 at least (App Store Connect warns about 14, and from April 2027 refuses anything lower)
sed -i '' -E "s/platform :ios, '1[0-4](\.[0-9]+)?'/platform :ios, '15.0'/" ios/App/Podfile
sed -i '' -E 's/IPHONEOS_DEPLOYMENT_TARGET = 1[0-4](\.[0-9]+)?;/IPHONEOS_DEPLOYMENT_TARGET = 15.0;/g' ios/App/App.xcodeproj/project.pbxproj
echo "customised"
