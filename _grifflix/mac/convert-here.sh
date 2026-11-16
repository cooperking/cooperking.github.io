#!/bin/zsh
# Grifflix: a film converted on this Mac, with its own video hardware, far quicker than the Mac mini (John, 16 Nov 2026: "Can I have a way
# to convert using the machine I'm on easily" ... "A button on a film").
#
# Once, to set it up (it makes a small app, "Grifflix Convert", that the film page's "Convert on this Mac" button opens):
#   curl -fsSL https://raw.githubusercontent.com/cooperking/cooperking.github.io/main/_grifflix/mac/convert-here.sh -o ~/Grifflix/convert-here.sh --create-dirs && zsh ~/Grifflix/convert-here.sh setup
#
# Then, on a film in Grifflix (on this Mac): Convert on this Mac. A Terminal window shows how far it's got. The Mac mini planned it (the
# film's own language first, every soundtrack, the subtitles), and checks it and puts it in place when this Mac has made it; the Mac mini's
# own converter leaves that film alone meanwhile. This Mac reads and writes the film on the G-Drive over the network (it connects to it).
# ffmpeg: ~/bin/ffmpeg (osxexperts.net, for Apple Silicon), else one on the PATH (Homebrew).
set -u
HERE="$HOME/Grifflix"
APP="$HOME/Applications/Grifflix Convert.app"
MINI="192.168.68.125"

if [[ "${1:-}" == "setup" ]]; then
  mkdir -p "$HOME/Applications" "$HERE"
  cp -f "$0" "$HERE/convert-here.sh" 2>/dev/null
  rm -rf "$APP"
  osacompile -o "$APP" \
    -e 'on open location u' \
    -e '  tell application "Terminal"' \
    -e '    activate' \
    -e '    do script "curl -fsSL https://raw.githubusercontent.com/cooperking/cooperking.github.io/main/_grifflix/mac/convert-here.sh -o ~/Grifflix/convert-here.sh.new && mv ~/Grifflix/convert-here.sh.new ~/Grifflix/convert-here.sh; zsh ~/Grifflix/convert-here.sh " & quoted form of u' \
    -e '  end tell' \
    -e 'end open location' || { echo "Couldn't make the app."; exit 1; }
  P="$APP/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string uk.co.grifflix.convert" "$P" 2>/dev/null || /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier uk.co.grifflix.convert" "$P"
  /usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes array" "$P" \
    -c "Add :CFBundleURLTypes:0 dict" \
    -c "Add :CFBundleURLTypes:0:CFBundleURLName string Grifflix Convert" \
    -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" \
    -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string grifflix-convert"
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
  FF=$( [[ -x "$HOME/bin/ffmpeg" ]] && echo "$HOME/bin/ffmpeg" || command -v ffmpeg )
  echo "Set up. On a film in Grifflix on this Mac: Convert on this Mac."
  [[ -z "$FF" ]] && echo "One more thing: no ffmpeg here yet. From osxexperts.net (Apple Silicon) unzip ffmpeg into ~/bin, then: xattr -d com.apple.quarantine ~/bin/ffmpeg"
  exit 0
fi

u="${1:-}"; u="${u#grifflix-convert://job?u=}"
url=$(osascript -l JavaScript -e "decodeURIComponent('${u//\'/}')")
FF=$( [[ -x "$HOME/bin/ffmpeg" ]] && echo "$HOME/bin/ffmpeg" || command -v ffmpeg )
[[ -z "$FF" ]] && { echo "No ffmpeg on this Mac: from osxexperts.net (Apple Silicon) unzip ffmpeg into ~/bin."; exit 1; }
job=$(mktemp); trap 'rm -f "$job"' EXIT
curl -fsS "$url" -o "$job" || { echo "The Mac mini didn't give the job (press Convert on this Mac again)."; exit 1; }
field() { osascript -l JavaScript -e "var j = JSON.parse($.NSString.stringWithContentsOfFileEncodingError('$job', 4, null).js); j['$1']"; }
name=$(field name); src=$(field src); tmp=$(field tmp)
args=("${(@f)$(osascript -l JavaScript -e "JSON.parse($.NSString.stringWithContentsOfFileEncodingError('$job', 4, null).js).args.join('\n')")}")
echo "Converting $name on this Mac."

# the G-Drive: connected if it isn't (a password box may come up the first time)
if [[ ! -d /Volumes/G-Drive ]]; then
  echo "Connecting to the G-Drive…"; open "smb://$MINI/G-Drive"
  for i in {1..40}; do [[ -d /Volumes/G-Drive ]] && break; sleep 1; done
  [[ -d /Volumes/G-Drive ]] || { echo "Couldn't reach the G-Drive."; curl -fsS -X POST -H 'Content-Type: application/json' -d '{"ok":false,"error":"no G-Drive"}' "$url/done" >/dev/null; exit 1; }
fi
[[ -f "$src" ]] || { echo "The film isn't where the Mac mini said: $src"; curl -fsS -X POST -H 'Content-Type: application/json' -d '{"ok":false,"error":"not found"}' "$url/done" >/dev/null; exit 1; }

# the plan as the Mac mini made it, -stats for how far it's got, and every few seconds the Mac mini told how far (the film's Edit panel
# shows it: John, 16 Nov 2026, "I'd like to see the progress"); Ctrl-C stops it and the Mac mini goes back to it later
prog=$(mktemp)
( while sleep 4; do
    t=$(grep '^out_time_us=' "$prog" 2>/dev/null | tail -1 | cut -d= -f2); sp=$(grep '^speed=' "$prog" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' ')
    [[ "$t" == <-> ]] && curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d "{\"t\":$((t / 1000000)),\"speed\":\"$sp\"}" "$url/progress" >/dev/null 2>&1
  done ) &
reporter=$!
trap 'kill $reporter 2>/dev/null; rm -f "$prog"; echo; echo "Stopped."; curl -fsS -X POST -H "Content-Type: application/json" -d "{\"ok\":false,\"error\":\"stopped\"}" "$url/done" >/dev/null; exit 1' INT TERM
if "$FF" -v error -stats -progress "$prog" "${args[@]}"; then
  kill $reporter 2>/dev/null; rm -f "$prog"
  echo "Made. The Mac mini is checking it and putting it in place."
  curl -fsS -X POST -H 'Content-Type: application/json' -d '{"ok":true}' "$url/done" >/dev/null && echo "Done: Grifflix shows the new copy within a few minutes. This window can be closed."
else
  kill $reporter 2>/dev/null; rm -f "$prog"
  echo "It didn't convert."; curl -fsS -X POST -H 'Content-Type: application/json' -d '{"ok":false,"error":"ffmpeg failed"}' "$url/done" >/dev/null
fi
