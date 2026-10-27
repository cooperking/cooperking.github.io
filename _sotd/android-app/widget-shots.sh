#!/bin/bash
# Pictures of the Android widgets (from the check's build, with WidgetPreview), the Song of the Day settings, the Quick
# Settings tile, and Android's own list of the widgets. Run on the emulator by .github/workflows/widgets-check.yml.
set -uo pipefail
OUT="${OUT:-_sotd/widget-shots/android}"; mkdir -p "$OUT"
APK="$1"; DAYS="${2:-}"
PKG=io.github.cooperking.sotd
shot() { adb exec-out screencap -p > "$OUT/$1.png"; echo "shot $1"; }
adb install -r -g "$APK" | tail -1
adb shell am start -W -n $PKG/.WidgetPreview --ei page 1 ${DAYS:+--es days "$DAYS"} > /dev/null; sleep 12; shot widgets-1
adb shell am start -W -n $PKG/.WidgetPreview --ei page 2 ${DAYS:+--es days "$DAYS"} > /dev/null; sleep 10; shot widgets-2
adb shell am start -W -n $PKG/.TodayConfig --ei appWidgetId 7 > /dev/null; sleep 3; shot settings
adb shell input keyevent KEYCODE_BACK
adb shell cmd statusbar add-tile $PKG/.SotdTile 2>&1 | head -2
adb shell cmd statusbar expand-settings; sleep 3; shot tile
adb shell cmd statusbar collapse
adb shell dumpsys appwidget > "$OUT/appwidget.txt" 2>&1
{ for w in TodayWidget PlayWidget CalendarWidget; do grep -q "$PKG/$PKG.$w" "$OUT/appwidget.txt" && echo "$w: Android lists it" || echo "$w: NOT listed"; done
  adb shell dumpsys package $PKG | grep -q "SotdTile" && echo "SotdTile: Android has it" || echo "SotdTile: NOT there"; } | tee "$OUT/listed.txt"
rm -f "$OUT/appwidget.txt"
adb logcat -d | grep -iE "AndroidRuntime|sotd|RemoteViews" | tail -40 > "$OUT/log.txt"
