#!/bin/zsh
# Grifflix on the Mac mini: double-click to set up (or again to change the folders or the tunnel).
# Installs Jellyfin (the engine, hidden away), the Grifflix gateway and Cloudflare's tunnel, and starts them with the Mac.
cd "$(dirname "$0")"
APP="$HOME/Library/Application Support/Grifflix"
AGENTS="$HOME/Library/LaunchAgents"
say_box() { osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with title \"Grifflix\"" >/dev/null 2>&1; }
pick() { osascript -e "POSIX path of (choose folder with prompt \"$1\")" 2>/dev/null; }
ask_yes() { [ "$(osascript -e "button returned of (display dialog \"$1\" buttons {\"Skip\", \"Choose…\"} default button 2 with title \"Grifflix\")" 2>/dev/null)" = "Choose…" ]; }

echo "== Grifflix setup"
if ! /usr/bin/python3 -c 'import ssl' >/dev/null 2>&1; then
  say_box "macOS needs its free Command Line Tools first (it may offer to install them now). When that's finished, double-click Install again."
  xcode-select --install >/dev/null 2>&1; exit 1
fi

# 1. Homebrew, then Jellyfin and cloudflared
if ! command -v brew >/dev/null 2>&1; then
  [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
  [ -x /usr/local/bin/brew ] && eval "$(/usr/local/bin/brew shellenv)"
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "Installing Homebrew (it asks for your Mac password)…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || { say_box "Homebrew didn't install. Double-click Install to try again."; exit 1; }
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi
[ -d /Applications/Jellyfin.app ] || brew install --cask jellyfin || { say_box "Jellyfin didn't install. Double-click Install to try again."; exit 1; }
command -v cloudflared >/dev/null 2>&1 || brew install cloudflared || { say_box "cloudflared didn't install. Double-click Install to try again."; exit 1; }
CLOUDFLARED="$(command -v cloudflared)"

# 2. Jellyfin running, and starting with the Mac
open -g -a Jellyfin
osascript -e 'tell application "System Events" to if not (exists login item "Jellyfin") then make login item at end with properties {path:"/Applications/Jellyfin.app", hidden:true}' >/dev/null 2>&1

# 3. The folders
mkdir -p "$APP" "$AGENTS"
cp grifflix-gateway.py "$APP/grifflix-gateway.py"
ARGS=()
ask_yes "Choose your FILMS folder? (each film in its own folder, like “Paddington (2014)”)" && M=$(pick "Your films folder") && [ -n "$M" ] && ARGS+=(--movies "$M")
ask_yes "Choose your TV folder? (each series in a folder, with “Season 01” folders inside)" && T=$(pick "Your TV folder") && [ -n "$T" ] && ARGS+=(--tv "$T")
ask_yes "Choose your HOME VIDEOS folder?" && H=$(pick "Your home videos folder") && [ -n "$H" ] && ARGS+=(--home "$H")
echo "Setting up Jellyfin's libraries…"
/usr/bin/python3 "$APP/grifflix-gateway.py" setup "${ARGS[@]}" 2>&1 | tee -a "$APP/setup.log"
[ ${pipestatus[1]} -eq 0 ] || { say_box "Setting up Jellyfin didn't work: see $APP/setup.log"; exit 1; }

# 4. The gateway, always running
cat > "$AGENTS/io.github.cooperking.grifflix-gateway.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.github.cooperking.grifflix-gateway</string>
  <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>$APP/grifflix-gateway.py</string><string>serve</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$APP/gateway.log</string>
  <key>StandardErrorPath</key><string>$APP/gateway.log</string>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)/io.github.cooperking.grifflix-gateway" >/dev/null 2>&1
launchctl bootstrap "gui/$(id -u)" "$AGENTS/io.github.cooperking.grifflix-gateway.plist"

# 5. The Cloudflare tunnel: its token, from the Cloudflare dashboard (kept in the Mac's keychain, not in a file)
TOKEN=$(osascript -e 'text returned of (display dialog "Paste the Cloudflare tunnel token (Cloudflare dashboard → Zero Trust → Networks → Tunnels → your tunnel → the long code after --token). Leave empty to keep the one you gave before." default answer "" with hidden answer with title "Grifflix")' 2>/dev/null)
[ -n "$TOKEN" ] && security add-generic-password -U -s grifflix-tunnel -a cloudflare -w "$TOKEN" >/dev/null
if security find-generic-password -s grifflix-tunnel -a cloudflare >/dev/null 2>&1; then
  cat > "$APP/tunnel.sh" <<SH
#!/bin/zsh
exec "$CLOUDFLARED" tunnel --no-autoupdate run --token "\$(security find-generic-password -s grifflix-tunnel -a cloudflare -w)"
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
  say_box "Grifflix is running on this Mac. Jellyfin will spend a while fetching posters for your films. Keep the Mac from sleeping: System Settings → Energy → Prevent automatic sleeping."
else
  say_box "The gateway didn't start: see $APP/gateway.log"
fi
