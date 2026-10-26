#!/bin/bash
# Runs the Android app (songs/android.apk) on an emulated phone with Google Play and Chrome, as someone would: opens it,
# presses Play all, turns the screen off and on. Saves screenshots, a screen recording, what Android says about the app's
# link to the site, and the log, to _sotd/android-app/check/. Run by .github/workflows/android-check.yml.
set -x
OUT=_sotd/android-app/check; rm -rf "$OUT"; mkdir -p "$OUT"
PKG=io.github.cooperking.sotd
shot() { adb exec-out screencap -p > "$OUT/$1.png"; }
media() { adb shell dumpsys media_session | grep -iE "package=|state=PlaybackState|metadata" | head -12 > "$OUT/$1.txt"; }
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
sleep 12; shot 3-playing; media 3-media-playing
wait $REC; adb pull /sdcard/opening.mp4 "$OUT/opening.mp4" >/dev/null 2>&1
adb shell input keyevent KEYCODE_SLEEP
sleep 25; media 4-media-screen-off
adb shell input keyevent KEYCODE_WAKEUP; sleep 2; adb shell wm dismiss-keyguard; sleep 3; shot 4-screen-on
adb shell pm get-app-links $PKG > "$OUT/app-links.txt" 2>&1
adb shell dumpsys package d $PKG > "$OUT/domain-verification.txt" 2>&1 || true
adb shell dumpsys package com.android.chrome | grep -m1 versionName > "$OUT/chrome-version.txt"
adb logcat -d | grep -v "cr_CronetUrl\|NetworkMonitor\|Nearby\|HeterodyneSyncer\|AiAiEcho" > "$OUT/log.txt"
ls -la "$OUT"
