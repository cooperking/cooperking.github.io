#!/bin/zsh
# Stops the Grifflix gateway and tunnel and takes them off this Mac. Jellyfin, your films and Grifflix's saved
# calendars and playlists (~/Library/Application Support/Grifflix/data.json) are left as they are.
for L in grifflix-gateway grifflix-tunnel; do
  launchctl bootout "gui/$(id -u)/io.github.cooperking.$L" >/dev/null 2>&1
  rm -f "$HOME/Library/LaunchAgents/io.github.cooperking.$L.plist"
done
security delete-generic-password -s grifflix-tunnel -a cloudflare >/dev/null 2>&1
osascript -e 'display dialog "Grifflix’s gateway and tunnel are off. (Jellyfin is still installed: drag it to the Bin to remove it.)" buttons {"OK"} default button 1 with title "Grifflix"' >/dev/null 2>&1
