#!/usr/bin/env bash
# The app in a TV emulator, worked with remote keys; a screenshot after each step into _tvapp/check/ (see
# tv-app-check.yml). Starts on the live site, then Menu switches to the draft.
set -uo pipefail
cd "$(dirname "$0")"
OUT=check; rm -rf "$OUT"; mkdir -p "$OUT"
n=0
shot() { n=$((n + 1)); local f; f=$(printf '%s/%02d-%s.png' "$OUT" "$n" "$1"); adb exec-out screencap -p > "$f"; echo "shot $f"; }
key() { adb shell input keyevent "$@"; sleep 1.2; }
{
  echo "device: $(adb shell getprop ro.build.version.release) API $(adb shell getprop ro.build.version.sdk)"
  echo "webview: $(adb shell dumpsys webviewupdate | grep -m1 'Current WebView package' )"
  adb shell wm size; adb shell wm density
} > "$OUT/device.txt" 2>&1
# a fresh emulator updates Google's apps for its first minutes, and that can take a browser view's process down
# with it (seen: "depends on provider ...gms FontsProvider in dying proc"), which a Fire TV doesn't have; so
# no Play Store updates, and a minute to settle
adb shell pm disable-user --user 0 com.android.vending >> "$OUT/device.txt" 2>&1
sleep 60
adb logcat -c
adb install -r ../songs/tv.apk >> "$OUT/device.txt" 2>&1
adb shell am start -W -n io.github.cooperking.sotd/.MainActivity >> "$OUT/device.txt" 2>&1
for t in 3 8 15; do sleep 5; echo "after ~${t}s: pid $(adb shell pidof io.github.cooperking.sotd) top $(adb shell dumpsys activity activities | grep -m1 topResumedActivity)" >> "$OUT/device.txt"; done
shot start
sleep 10; shot live
echo "app pid after start: $(adb shell pidof io.github.cooperking.sotd)" >> "$OUT/device.txt"
key KEYCODE_MENU; sleep 20; shot draft
echo "app pid on the draft: $(adb shell pidof io.github.cooperking.sotd)" >> "$OUT/device.txt"
# the draft starts on today, the latest song
key KEYCODE_DPAD_LEFT; shot left-day-before
adb shell input keyevent --longpress KEYCODE_DPAD_CENTER; sleep 1; shot hold-adds-to-my-playlist
key KEYCODE_DPAD_CENTER; sleep 5; shot ok-opens-day
key KEYCODE_DPAD_DOWN KEYCODE_DPAD_DOWN KEYCODE_DPAD_DOWN; shot down-to-favourite
key KEYCODE_DPAD_RIGHT; sleep 4; shot right-next-day
key KEYCODE_BACK; sleep 3; shot back-closes-day
key KEYCODE_DPAD_UP KEYCODE_DPAD_UP; shot up-up-to-bar
key KEYCODE_BACK; sleep 3; shot back-closes-app
adb shell dumpsys activity activities | grep -m1 topResumedActivity)" >> "$OUT/device.txt"; done
shot start
sleep 10; shot live
echo "app pid after start: $(adb shell pidof io.github.cooperking.sotd)" >> "$OUT/device.txt"
key KEYCODE_MENU; sleep 20; shot draft
echo "app pid on the draft: $(adb shell pidof io.github.cooperking.sotd)" >> "$OUT/device.txt"
# the draft starts on today, the latest song
key KEYCODE_DPAD_LEFT; shot left-day-before
key KEYCODE_DPAD_CENTER; sleep 5; shot ok-opens-day
key KEYCODE_MEDIA_PLAY_PAUSE; sleep 3; shot playpause
key KEYCODE_MEDIA_FAST_FORWARD; sleep 5; shot fastforward-next-day
key KEYCODE_DPAD_RIGHT; shot right-to-video
key KEYCODE_DPAD_CENTER; sleep 30; shot video-30s
sleep 30; shot video-60s
key KEYCODE_BACK; sleep 3; shot back-closes-day
key KEYCODE_DPAD_UP KEYCODE_DPAD_UP KEYCODE_DPAD_UP; shot up-up-up
key KEYCODE_BACK; sleep 3; shot back-closes-app
adb shell dumpsys activity activities | grep -m1 topResumedActivity)" >> "$OUT/device.txt"; done
shot start
sleep 10; shot live
echo "app pid after start: $(adb shell pidof io.github.cooperking.sotd)" >> "$OUT/device.txt"
key KEYCODE_MENU; sleep 20; shot draft
echo "app pid on the draft: $(adb shell pidof io.github.cooperking.sotd)" >> "$OUT/device.txt"
key KEYCODE_DPAD_RIGHT KEYCODE_DPAD_RIGHT; shot right-right
key KEYCODE_DPAD_DOWN; shot down
key KEYCODE_DPAD_CENTER; sleep 4; shot ok-day
key KEYCODE_MEDIA_PLAY_PAUSE; sleep 3; shot playpause
key KEYCODE_MEDIA_FAST_FORWARD; sleep 4; shot fastforward
key KEYCODE_DPAD_RIGHT; shot right-to-video
key KEYCODE_DPAD_CENTER; sleep 25; shot video
key KEYCODE_BACK; sleep 3; shot back
key KEYCODE_DPAD_UP KEYCODE_DPAD_UP KEYCODE_DPAD_UP; shot up-up-up
key KEYCODE_BACK; sleep 3; shot back-closes-app
adb shell dumpsys activity activities | grep -E "mResumedActivity|topResumedActivity" >> "$OUT/device.txt" 2>&1
adb logcat -d -v time > "$OUT/full.txt" 2>&1
grep -E "SOTD|sotd|cooperking|lowmemorykiller|lmkd|has died|Killing|FATAL|AndroidRuntime|DEBUG|chromium|cr_" "$OUT/full.txt" | grep -vE "com.google.android.gms|GmsCore|katniss|mediashell" > "$OUT/log.txt"
gzip -f "$OUT/full.txt"
ls -l "$OUT"
exit 0
