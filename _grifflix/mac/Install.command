#!/bin/zsh
# Grifflix on the Mac mini: double-click to set up (or again to change the folders or the tunnel).
# Installs Jellyfin (the engine, hidden away), the Grifflix gateway and Cloudflare's tunnel, and starts them with the Mac.
#
# Two ways, by the Mac's macOS:
# - macOS 12 or later: the current Jellyfin and cloudflared, from Homebrew.
# - older (John's 2010 Mac mini, High Sierra 10.13): the last builds that still run there, fetched directly:
#   Jellyfin 10.7.7 (its app, with its own ffmpeg), Python 3.12.10 from python.org (High Sierra has no Python 3),
#   cloudflared 2023.10.0 (the last built with Go 1.20; later ones need 10.15).
# Moving to a newer Mac later: run this there, then copy ~/Library/Application Support/Grifflix/data.json across
# (Movie of the Day, everyone's calendars, playlists and where they stopped).
cd "$(dirname "$0")"
APP="$HOME/Library/Application Support/Grifflix"
AGENTS="$HOME/Library/LaunchAgents"
OLD_JELLYFIN=https://repo.jellyfin.org/archive/server/macos/stable/10.7.7/installer/Jellyfin_10.7.7.dmg
OLD_PYTHON=https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg
OLD_CLOUDFLARED=https://github.com/cloudflare/cloudflared/releases/download/2023.10.0/cloudflared-darwin-amd64.tgz
say_box() { osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with title \"Grifflix\"" >/dev/null 2>&1; }
pick() { osascript -e "POSIX path of (choose folder with prompt \"$1\")" 2>/dev/null; }
ask_yes() { [ "$(osascript -e "button returned of (display dialog \"$1\" buttons {\"Skip\", \"Choose…\"} default button 2 with title \"Grifflix\")" 2>/dev/null)" = "Choose…" ]; }
fail() { say_box "$1"; exit 1; }

MAJOR=$(sw_vers -productVersion | cut -d. -f1); MINOR=$(sw_vers -productVersion | cut -d. -f2)
[ "$MAJOR" -eq 10 ] && OLD=1 || { [ "$MAJOR" -lt 12 ] && OLD=1 || OLD=0; }
echo "== Grifflix setup (macOS $(sw_vers -productVersion)$([ $OLD = 1 ] && echo ', the older-Mac way'))"
mkdir -p "$APP" "$AGENTS"

if [ $OLD = 1 ]; then
  [ "$MAJOR" -eq 10 ] && [ "$MINOR" -lt 13 ] && fail "Grifflix needs macOS 10.13 (High Sierra) or later."
  # Python 3 from python.org, and certificates for it
  PY=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3
  if [ ! -x "$PY" ]; then
    echo "Installing Python 3.12 (it asks for your Mac password)…"
    curl -fL --retry 3 -o "$APP/python.pkg" "$OLD_PYTHON" || fail "Python didn't download. Double-click Install to try again."
    sudo installer -pkg "$APP/python.pkg" -target / || fail "Python didn't install. Double-click Install to try again."
    rm -f "$APP/python.pkg"
  fi
  "$PY" -m pip install --quiet --upgrade certifi || echo "(certifi didn't install: the gateway may not reach the Worker)"
  # Jellyfin 10.7.7
  if [ ! -d /Applications/Jellyfin.app ]; then
    echo "Installing Jellyfin 10.7.7…"
    curl -fL --retry 3 -o "$APP/jellyfin.dmg" "$OLD_JELLYFIN" || fail "Jellyfin didn't download. Double-click Install to try again."
    MNT=$(hdiutil attach -nobrowse -noverify "$APP/jellyfin.dmg" | tail -1 | sed 's/.*\(\/Volumes\/.*\)/\1/')
    SRC=$(find "$MNT" -maxdepth 2 -name Jellyfin.app | head -1)
    ditto "$SRC" /Applications/Jellyfin.app || { hdiutil detach "$MNT" >/dev/null; fail "Jellyfin didn't copy into Applications."; }
    hdiutil detach "$MNT" >/dev/null; rm -f "$APP/jellyfin.dmg"
  fi
  # cloudflared 2023.10.0
  CLOUDFLARED="$APP/cloudflared"
  if [ ! -x "$CLOUDFLARED" ]; then
    curl -fL --retry 3 -o "$APP/cf.tgz" "$OLD_CLOUDFLARED" && tar xzf "$APP/cf.tgz" -C "$APP" && rm -f "$APP/cf.tgz" || fail "cloudflared didn't download."
    chmod +x "$CLOUDFLARED"
  fi
else
  PY=/usr/bin/python3
  if ! "$PY" -c 'import ssl' >/dev/null 2>&1; then
    say_box "macOS needs its free Command Line Tools first (it may offer to install them now). When that's finished, double-click Install again."
    xcode-select --install >/dev/null 2>&1; exit 1
  fi
  if ! command -v brew >/dev/null 2>&1; then
    [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
    [ -x /usr/local/bin/brew ] && eval "$(/usr/local/bin/brew shellenv)"
  fi
  if ! command -v brew >/dev/null 2>&1; then
    echo "Installing Homebrew (it asks for your Mac password)…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || fail "Homebrew didn't install. Double-click Install to try again."
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
  fi
  [ -d /Applications/Jellyfin.app ] || brew install --cask jellyfin || fail "Jellyfin didn't install. Double-click Install to try again."
  command -v cloudflared >/dev/null 2>&1 || brew install cloudflared || fail "cloudflared didn't install. Double-click Install to try again."
  CLOUDFLARED="$(command -v cloudflared)"
fi

# Jellyfin running, and starting with the Mac
open -g -a /Applications/Jellyfin.app
osascript -e 'tell application "System Events" to if not (exists login item "Jellyfin") then make login item at end with properties {path:"/Applications/Jellyfin.app", hidden:true}' >/dev/null 2>&1

# The folders
cp grifflix-gateway.py "$APP/grifflix-gateway.py"
ARGS=()
ask_yes "Choose your FILMS folder? (each film in its own folder, like “Paddington (2014)”)" && M=$(pick "Your films folder") && [ -n "$M" ] && ARGS+=(--movies "$M")
ask_yes "Choose your TV folder? (each series in a folder, with “Season 01” folders inside)" && T=$(pick "Your TV folder") && [ -n "$T" ] && ARGS+=(--tv "$T")
ask_yes "Choose your HOME VIDEOS folder?" && H=$(pick "Your home videos folder") && [ -n "$H" ] && ARGS+=(--home "$H")
echo "Setting up Jellyfin's libraries…"
"$PY" "$APP/grifflix-gateway.py" setup "${ARGS[@]}" 2>&1 | tee -a "$APP/setup.log"
RC=${pipestatus[1]}
while [ $RC -eq 3 ] || [ $RC -eq 4 ]; do   # Jellyfin was set up before Grifflix (3), or that login was wrong (4): its own login
  JU=$(osascript -e 'text returned of (display dialog "Jellyfin was set up on this Mac before. Its username (the one you sign in to Jellyfin with):" default answer "" with title "Grifflix")' 2>/dev/null) || fail "Grifflix needs that Jellyfin login. (Forgotten it? See the read-me: Starting Jellyfin afresh.)"
  JP=$(osascript -e 'text returned of (display dialog "Its password:" default answer "" with hidden answer with title "Grifflix")' 2>/dev/null) || exit 1
  JF_PASSWORD="$JP" "$PY" "$APP/grifflix-gateway.py" setup --jf-user "$JU" "${ARGS[@]}" 2>&1 | tee -a "$APP/setup.log"
  RC=${pipestatus[1]}
done
[ $RC -eq 0 ] || fail "Setting up Jellyfin didn't work: see $APP/setup.log"

# The gateway, always running
cat > "$AGENTS/io.github.cooperking.grifflix-gateway.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.github.cooperking.grifflix-gateway</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$APP/grifflix-gateway.py</string><string>serve</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$APP/gateway.log</string>
  <key>StandardErrorPath</key><string>$APP/gateway.log</string>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)/io.github.cooperking.grifflix-gateway" >/dev/null 2>&1
launchctl bootstrap "gui/$(id -u)" "$AGENTS/io.github.cooperking.grifflix-gateway.plist"

# The Cloudflare tunnel: its token, from the Cloudflare dashboard (kept in the Mac's keychain, not in a file)
TOKEN=$(osascript -e 'text returned of (display dialog "Paste the Cloudflare tunnel token (Cloudflare dashboard → Zero Trust → Networks → Tunnels → your tunnel → the long code after --token). Leave empty to skip, or to keep the one you gave before." default answer "" with hidden answer with title "Grifflix")' 2>/dev/null)
[ -n "$TOKEN" ] && security add-generic-password -U -s grifflix-tunnel -a cloudflare -w "$TOKEN" >/dev/null
if security find-generic-password -s grifflix-tunnel -a cloudflare >/dev/null 2>&1; then
  # (HTTP/2 to Cloudflare, not QUIC: on the powerline route QUIC kept timing out and films stopped after their first piece: John, 14 Nov 2026)
  cat > "$APP/tunnel.sh" <<SH
#!/bin/zsh
exec "$CLOUDFLARED" tunnel --no-autoupdate --protocol http2 run --token "\$(security find-generic-password -s grifflix-tunnel -a cloudflare -w)"
SH
  chmod +x "$APP/tunnel.sh"
  cat > "$AGENTS/io.github.cooperking.grifflix-tunnel.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.github.cooperking.grifflix-tunnel</string>
  <key>ProgramArguments</key><array><string>$APP/tunnel.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$APP/tunnel.log</string>
  <key>StandardErrorPath</key><string>$APP/tunnel.log</string>
</dict></plist>
PLIST
  launchctl bootout "gui/$(id -u)/io.github.cooperking.grifflix-tunnel" >/dev/null 2>&1
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/io.github.cooperking.grifflix-tunnel.plist"
fi

sleep 3
if curl -s -m 5 http://127.0.0.1:8097/health | grep -q ok; then
  say_box "Grifflix is running on this Mac. Jellyfin will spend a while fetching posters for your films. Keep the Mac from sleeping: System Preferences → Energy Saver → Prevent computer from sleeping automatically."
else
  say_box "The gateway didn't start: see $APP/gateway.log"
fi
