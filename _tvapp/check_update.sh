#!/usr/bin/env bash
# The update check, in a TV emulator: an old build (version 1, built by tv-app-check.yml with the real key) opens,
# finds the newest build in songs/tv.json, offers it; Install asks for the TV's permission the first time (given here
# with adb, as the TV's switch would), downloads it and hands it to the installer; the installer's Update is pressed;
# the installed version is then the newest. Screenshots into _tvapp/check/.
set -uo pipefail
cd "$(dirname "$0")"
OUT=check; rm -rf "$OUT"; mkdir -p "$OUT"
n=0
shot() { n=$((n + 1)); local f; f=$(printf '%s/%02d-%s.png' "$OUT" "$n" "$1"); adb exec-out screencap -p > "$f"; echo "shot $f"; }
key() { adb shell input keyevent "$@"; sleep 1.5; }
ver() { adb shell dumpsys package io.github.cooperking.sotd | grep -m1 -o 'versionCode=[0-9]*'; }
{ echo "device: $(adb shell getprop ro.build.version.release) API $(adb shell getprop ro.build.version.sdk)"; } > "$OUT/device.txt" 2>&1
adb shell pm disable-user --user 0 com.android.vending >> "$OUT/device.txt" 2>&1
sleep 60
adb logcat -c
adb install -r build/sotd-tv.apk >> "$OUT/device.txt" 2>&1
echo "installed: $(ver) (newest on the site: $(curl -s https://cooperking.github.io/songs/tv.json))" >> "$OUT/device.txt"
adb shell am start -W -n io.github.cooperking.sotd/.MainActivity >> "$OUT/device.txt" 2>&1
sleep 25; shot offered
key KEYCODE_DPAD_RIGHT KEYCODE_DPAD_CENTER; sleep 4; shot install-pressed
echo "may install before: $(adb shell appops get io.github.cooperking.sotd REQUEST_INSTALL_PACKAGES)" >> "$OUT/device.txt"
adb shell appops set io.github.cooperking.sotd REQUEST_INSTALL_PACKAGES allow
key KEYCODE_BACK; sleep 15; shot installer
key KEYCODE_DPAD_RIGHT KEYCODE_DPAD_CENTER; sleep 25; shot after-update
echo "after: $(ver)" >> "$OUT/device.txt"
adb logcat -d -v time > "$OUT/full.txt" 2>&1
grep -E "SOTD|sotd|cooperking|PackageInstaller|has died|FATAL|AndroidRuntime" "$OUT/full.txt" | grep -vE "com.google.android.gms|GmsCore" > "$OUT/log.txt"
gzip -f "$OUT/full.txt"
cat "$OUT/device.txt"
ls -l "$OUT"
exit 0
