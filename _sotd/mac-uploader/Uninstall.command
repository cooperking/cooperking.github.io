#!/bin/zsh
# Stops the SOTD Uploader and takes it off this Mac (your SOTD folder and what's on the site are left as they are).
launchctl bootout "gui/$(id -u)/io.github.cooperking.sotd-uploader" >/dev/null 2>&1
rm -f "$HOME/Library/LaunchAgents/io.github.cooperking.sotd-uploader.plist"
rm -rf "$HOME/Library/Application Support/SOTD Uploader"
security delete-generic-password -s sotd-uploader -a admin >/dev/null 2>&1
osascript -e 'display dialog "SOTD Uploader is off and removed." buttons {"OK"} default button 1 with title "SOTD Uploader"' >/dev/null 2>&1
