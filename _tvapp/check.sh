#!/usr/bin/env bash
# The app (songs/tv.apk) in a TV emulator, worked with remote keys; a screenshot after each step into _tvapp/check/
# (see tv-app-check.yml). It opens the live site, which has TV mode.
set -uo pipefail
cd "$(dirname "$0")"
OUT=check; rm -rf "$OUT"; mkdir -p "$OUT"
n=0
shot() { n=$((n + 1)); local f; f=$(printf '%s/%02d-%s.png' "$OUT" "$n" "$1"); adb exec-out screencap -p > "$f"; echo "shot $f"; }
key() { adb shell input keyevent "$@"; sleep 1.2; }
{
  echo "device: $(adb shell getprop ro.build.version.release) API $(adb shell getprop ro.build.version.sdk)"
  echo "webview: $(adb shell dumpsys webviewupdate | grep -m1 'Current WebView package')"
  adb shell wm size; adb shell wm density
} > "$OUT/device.txt" 2>&1
# a fresh emulator updates Google's apps for its first minutes, which can take a browser view's process down with it
# (a Fire TV doesn't have them): no Play Store updates, and a minute to settle
adb shell pm disable-user --user 0 com.android.vending >> "$OUT/device.txt" 2>&1
sleep 60
adb logcat -c
adb install -r ../songs/tv.apk >> "$OUT/device.txt" 2>&1
adb shell am start -W -n io.github.cooperking.sotd/.MainActivity >> "$OUT/device.txt" 2>&1
sleep 20; shot calendar
adb shell input keyevent --longpress KEYCODE_DPAD_CENTER; sleep 1; shot hold-adds-to-my-playlist
key KEYCODE_DPAD_CENTER; sleep 8; shot ok-opens-day-full-screen
key KEYCODE_DPAD_DOWN; sleep 1; shot down-brings-the-buttons
sleep 25; shot video-30s
key KEYCODE_BACK; sleep 2; shot back-puts-the-buttons-away
key KEYCODE_BACK; sleep 3; shot back-closes-day
key KEYCODE_BACK; sleep 3; shot back-closes-app
adb shell dumpsys activity activities | grep -E "mResumedActivity|topResumedActivity" >> "$OUT/device.txt" 2>&1
adb logcat -d -v time > "$OUT/full.txt" 2>&1
grep -E "SOTD|sotd|cooperking|has died|FATAL|AndroidRuntime" "$OUT/full.txt" | grep -vE "com.google.android.gms|GmsCore" > "$OUT/log.txt"
gzip -f "$OUT/full.txt"
ls -l "$OUT"
exit 0
