#!/usr/bin/env bash
# The YouTube sign-in, in a TV emulator: an advert starting (made to look so, through the view's debugging socket)
# offers it, once; Not now; Menu opens the menu; its first line opens Google's sign-in in the app; OK on the page
# brings the keyboard; Back leaves the sign-in for the songs. Nobody signs in here.
# Screenshots into _tvapp/check/.
set -uo pipefail
cd "$(dirname "$0")"
OUT=check; rm -rf "$OUT"; mkdir -p "$OUT"
n=0
shot() { n=$((n + 1)); local f; f=$(printf '%s/%02d-%s.png' "$OUT" "$n" "$1"); adb exec-out screencap -p > "$f"; echo "shot $f"; }
key() { adb shell input keyevent "$@"; sleep 1.5; }
{ echo "device: $(adb shell getprop ro.build.version.release) API $(adb shell getprop ro.build.version.sdk)"; } > "$OUT/device.txt" 2>&1
adb shell pm disable-user --user 0 com.android.vending >> "$OUT/device.txt" 2>&1
sleep 60
adb logcat -c
adb install -r ../songs/tv.apk >> "$OUT/device.txt" 2>&1
adb shell am start -W -n io.github.cooperking.sotd/.MainActivity >> "$OUT/device.txt" 2>&1
sleep 20; shot calendar
pip install --quiet websocket-client >> "$OUT/device.txt" 2>&1
adb forward tcp:9222 "localabstract:webview_devtools_remote_$(adb shell pidof io.github.cooperking.sotd | tr -d '\r')"
AD="document.documentElement.classList.add('vad'); document.dispatchEvent(new Event('sotd-ad')); 'advert'"
python3 cdp.py "$AD" >> "$OUT/device.txt" 2>&1; sleep 2; shot advert-offers-sign-in
key KEYCODE_DPAD_LEFT KEYCODE_DPAD_CENTER; sleep 1; shot not-now
python3 cdp.py "document.documentElement.classList.remove('vad'); $AD" >> "$OUT/device.txt" 2>&1; sleep 2; shot second-advert-no-offer
python3 cdp.py "document.documentElement.classList.remove('vad'); document.dispatchEvent(new Event('sotd-ad')); 'off'" >> "$OUT/device.txt" 2>&1
key KEYCODE_MENU; sleep 1; shot menu
key KEYCODE_DPAD_CENTER; sleep 12; shot google-sign-in
key KEYCODE_DPAD_CENTER; sleep 3; shot ok-on-the-page
key KEYCODE_TAB; sleep 2; shot tab
key KEYCODE_BACK; sleep 2; shot back-1
adb logcat -d -v time > "$OUT/full.txt" 2>&1
grep -E "SOTD|has died|FATAL|AndroidRuntime" "$OUT/full.txt" | grep -vE "com.google.android.gms|GmsCore" > "$OUT/log.txt"
gzip -f "$OUT/full.txt"
cat "$OUT/device.txt" "$OUT/log.txt"
ls -l "$OUT"
exit 0
