#!/bin/bash
# Runs the Android app (songs/android.apk) on an emulated phone with Google Play and Chrome, as someone would: opens it,
# presses Play all, turns the screen off and on. First, the build before (OLD_APK) to see the page offer this one. Saves screenshots, a screen recording, what Android says about the app's
# link to the site, and the log, to _sotd/android-app/check/. Run by .github/workflows/android-check.yml.
set -x
OUT=_sotd/android-app/check; rm -rf "$OUT"; mkdir -p "$OUT"
PKG=io.github.cooperking.sotd
shot() { adb exec-out screencap -p > "$OUT/$1.png"; }
media() { adb shell dumpsys media_session | grep -v -i bluetooth | grep -iE "package=|state=PlaybackState|metadata|description" | head -20 > "$OUT/$1.txt"; adb shell dumpsys audio | grep -iE "player piid|state:started|usage=USAGE_MEDIA" | head -10 >> "$OUT/$1.txt"; }
tap_label() {   # tap the first thing on screen whose text or description contains $1 (Chrome's page, through accessibility)
  adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1; adb pull /sdcard/ui.xml "$OUT/ui-$2.xml" >/dev/null 2>&1
  python3 - "$OUT/ui-$2.xml" "$1" <<'PY' > "$OUT/tap-$2.txt"
import re, sys
x = open(sys.argv[1], encoding='utf-8', errors='ignore').read()
for m in re.finditer(r'<node [^>]*>', x):
    n = m.group(0)
    if sys.argv[2].lower() in (re.search(r'text="([^"]*)"', n).group(1) + ' ' + re.search(r'content-desc="([^"]*)"', n).group(1)).lower():
        b = list(map(int, re.findall(r'\d+', re.search(r'bounds="([^"]*)"', n).group(1))))
        print((b[0] + b[2]) // 2, (b[1] + b[3]) // 2); break
PY
  read X Y < "$OUT/tap-$2.txt" || true
  [ -n "${X:-}" ] && adb shell input tap "$X" "$Y"
}
adb shell settings put global window_animation_scale 0
adb install -r songs/android.apk
adb shell pm grant $PKG android.permission.POST_NOTIFICATIONS || true
# Chrome without its first-run screens (as on a phone that's used Chrome before)
adb shell "echo 'chrome --disable-fre --no-default-browser-check --no-first-run' > /data/local/tmp/chrome-command-line"
adb shell am set-debug-app --persistent com.android.chrome
adb shell pm get-app-links $PKG > "$OUT/app-links-before.txt" 2>&1
adb shell screenrecord --time-limit 40 --bit-rate 2000000 /sdcard/opening.mp4 &
REC=$!
adb logcat -c
adb shell cmd package resolve-activity --brief -c android.intent.category.LAUNCHER $PKG > "$OUT/launcher.txt" 2>&1
adb shell am start -W -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -n "$(tail -1 "$OUT/launcher.txt")" > "$OUT/start.txt" 2>&1
for i in 1 2 3 4; do sleep 2; shot "0-start-$i"; done
sleep 1; shot 1-opening
sleep 14; shot 2-opened
adb shell dumpsys window | grep -E "mCurrentFocus|mFocusedApp" > "$OUT/focus.txt"
tap_label "Play every song" play
if [ ! -s "$OUT/tap-play.txt" ]; then   # Chrome's page not in the accessibility dump: Play all's place on a Pixel 6 (1080 x 2400)
  SZ=$(adb shell wm size | grep -o '[0-9]*x[0-9]*' | tail -1); W=${SZ%x*}; H=${SZ#*x}
  echo "$((W * 643 / 1000)) $((H * 261 / 1000)) (by position, screen $SZ)" > "$OUT/tap-play.txt"
  adb shell input tap $((W * 643 / 1000)) $((H * 261 / 1000))
fi
sleep 12; shot 3-playing; media 3-media-playing
wait $REC; adb pull /sdcard/opening.mp4 "$OUT/opening.mp4" >/dev/null 2>&1
adb shell input keyevent KEYCODE_SLEEP
sleep 25; media 4-media-screen-off
adb shell input keyevent KEYCODE_WAKEUP; sleep 2; adb shell wm dismiss-keyguard; sleep 3; shot 4-screen-on
adb shell pm get-app-links $PKG > "$OUT/app-links.txt" 2>&1
adb shell dumpsys package d $PKG > "$OUT/domain-verification.txt" 2>&1 || true
adb shell dumpsys package com.android.chrome | grep -m1 versionName > "$OUT/chrome-version.txt"
adb logcat -d | grep -v "cr_CronetUrl\|NetworkMonitor\|Nearby\|HeterodyneSyncer\|AiAiEcho" > "$OUT/log.txt"
offer() {   # is the page offering a newer build of the app? (Chrome's page, through accessibility)
  adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1; adb pull /sdcard/ui.xml "$OUT/ui-$1.xml" >/dev/null 2>&1
  if grep -qi "new version of the app" "$OUT/ui-$1.xml"; then echo "offered"; elif grep -qi "Song of the Day" "$OUT/ui-$1.xml"; then echo "not offered"; else echo "unknown (the page wasn't in the accessibility dump; see the screenshot)"; fi
}
# the update offer, last (so the checks above are of a first install): this build shouldn't be offered anything; the
# build before it (OLD_APK, from git; android-check.yml) should be offered this one
adb shell am force-stop $PKG; adb shell monkey -p $PKG -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
sleep 20; shot 5-this-build
echo "this build: $(offer new)" > "$OUT/update-offer.txt"
if [ -s "${OLD_APK:-}" ]; then
  # (Chrome keeps the site's storage, which now says this build is installed: cleared, as on a phone that's only had the old one)
  adb uninstall $PKG >/dev/null; adb shell pm clear com.android.chrome >/dev/null
  adb install "$OLD_APK" >/dev/null && adb shell pm grant $PKG android.permission.POST_NOTIFICATIONS || true
  adb shell monkey -p $PKG -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
  sleep 25; shot 6-previous-build
  echo "the build before: $(offer old)" >> "$OUT/update-offer.txt"
else
  echo "the build before: not tried (no earlier build in git)" >> "$OUT/update-offer.txt"
fi
cat "$OUT/update-offer.txt"
# the widget and the shortcuts (from build 10): Android's own lists of them, then a shortcut opened as the launcher would
adb install -r songs/android.apk >/dev/null 2>&1
adb shell dumpsys appwidget > "$OUT/appwidget.txt" 2>&1
adb shell dumpsys shortcut > "$OUT/shortcut-all.txt" 2>&1
{ grep -q "io.github.cooperking.sotd/.TodayWidget" "$OUT/appwidget.txt" && echo "widget: Android lists it" || echo "widget: NOT listed"
  grep -c "do=today\|do=shuffle\|do=mine" "$OUT/shortcut-all.txt" | sed 's/^/shortcut addresses found: /'
} > "$OUT/widget-shortcuts.txt"
grep -B2 -A12 "Package: $PKG" "$OUT/shortcut-all.txt" | head -80 > "$OUT/shortcuts.txt"; rm -f "$OUT/shortcut-all.txt"
adb shell am force-stop $PKG
adb shell am start -W -a android.intent.action.VIEW -d "https://cooperking.github.io/songs/?do=today" -p $PKG > "$OUT/start-shortcut.txt" 2>&1
sleep 20; shot 7-shortcut-today
cat "$OUT/widget-shortcuts.txt"
ls -la "$OUT"
