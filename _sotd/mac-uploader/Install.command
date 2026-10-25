#!/bin/zsh
# SOTD Uploader: puts each new or changed MP3 and MP4 in your SOTD folder on the Song of the Day site by itself.
# Double-click to set up (or to change the folder or password). It runs quietly in the background from then on.
cd "$(dirname "$0")"
APP="$HOME/Library/Application Support/SOTD Uploader"
AGENT="$HOME/Library/LaunchAgents/io.github.cooperking.sotd-uploader.plist"
WORKER="https://sotd-playlists.cooperking.workers.dev"
say_box() { osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with title \"SOTD Uploader\"" >/dev/null 2>&1; }

if ! /usr/bin/python3 -c 'import ssl' >/dev/null 2>&1; then
  say_box "macOS needs its free Command Line Tools first (it may offer to install them now). When that's finished, double-click Install again."
  xcode-select --install >/dev/null 2>&1
  exit 1
fi

FOLDER=$(osascript -e 'POSIX path of (choose folder with prompt "Choose your SOTD folder (the one the new MP3s and MP4s go into)")' 2>/dev/null)
[ -z "$FOLDER" ] && exit 1
KEY=$(osascript -e 'text returned of (display dialog "The admin page password:" default answer "" with hidden answer with title "SOTD Uploader")' 2>/dev/null)
[ -z "$KEY" ] && exit 1
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$WORKER/members/admin" -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{"op":"list"}')
if [ "$CODE" != "200" ]; then say_box "That password didn't work (the site said $CODE). Double-click Install to try again."; exit 1; fi

mkdir -p "$APP" "$HOME/Library/LaunchAgents"
cp sotd-uploader.py "$APP/sotd-uploader.py"
/usr/bin/python3 -c 'import json,sys; json.dump({"folder": sys.argv[1]}, open(sys.argv[2], "w"))' "$FOLDER" "$APP/settings.json"
security add-generic-password -U -s sotd-uploader -a admin -w "$KEY" >/dev/null

cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.github.cooperking.sotd-uploader</string>
  <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>$APP/sotd-uploader.py</string></array>
  <key>WatchPaths</key><array><string>$FOLDER</string></array>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$APP/launchd.log</string>
  <key>StandardErrorPath</key><string>$APP/launchd.log</string>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)/io.github.cooperking.sotd-uploader" >/dev/null 2>&1
launchctl bootstrap "gui/$(id -u)" "$AGENT"
say_box "Set up. New and changed MP3s and MP4s in $FOLDER now go up by themselves (when the folder changes, and every 15 minutes), and you'll get a notification when they do. What it's done is in ~/Library/Application Support/SOTD Uploader/uploader.log."
