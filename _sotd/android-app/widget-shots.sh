#!/bin/bash
# Pictures of the Android widgets (from the check's build, with WidgetPreview), the Song of the Day settings, the Quick
# Settings tile, and Android's own list of the widgets. Run on the emulator by .github/workflows/widgets-check.yml.
set -uo pipefail
OUT="${OUT:-_sotd/widget-shots/android}"; mkdir -p "$OUT"
APK="$1"; DAYS="${2:-}"
PKG=io.github.cooperking.sotd
shot() { adb exec-out screencap -p > "$OUT/$1.png"; echo "shot $1"; }
adb install -r -g "$APK" | tail -1
adb shell dumpsys package $PKG | grep -E "android.permission.INTERNET" | head -1 | sed 's/^ */permission: /' | tee "$OUT/internet.txt"
adb shell am start -S -W -n $PKG/.WidgetPreview --ei page 1 ${DAYS:+--es days "$DAYS"} > /dev/null; sleep 15; shot widgets-1
adb shell am start -S -W -n $PKG/.WidgetPreview --ei page 2 ${DAYS:+--es days "$DAYS"} > /dev/null; sleep 15; shot widgets-2
adb shell am start -W -n $PKG/.TodayConfig --ei appWidgetId 7 > /dev/null; sleep 3; shot settings
adb shell input keyevent KEYCODE_BACK
adb shell settings put secure sysui_qs_tiles "custom($PKG/.SotdTile),internet,bt,flashlight,dnd,airplane"   # (its tile first)
adb shell cmd statusbar expand-settings; sleep 3
for i in 1 2 3; do adb shell input swipe 150 800 950 800 250; sleep 1; done   # (to the first page of tiles)
sleep 2; shot tile
adb shell cmd statusbar collapse
adb shell dumpsys appwidget > "$OUT/appwidget.txt" 2>&1
{ for w in TodayWidget PlayWidget CalendarWidget; do grep -q "$PKG/$PKG.$w" "$OUT/appwidget.txt" && echo "$w: Android lists it" || echo "$w: NOT listed"; done
  adb shell dumpsys package $PKG | grep -q "SotdTile" && echo "SotdTile: Android has it" || echo "SotdTile: NOT there"; } | tee "$OUT/listed.txt"
rm -f "$OUT/appwidget.txt"
adb logcat -d | grep -iE "AndroidRuntime|SotdWidget|RemoteViews|FATAL" | tail -40 > "$OUT/log.txt"
