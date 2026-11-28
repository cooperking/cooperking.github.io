#!/bin/zsh
# Grifflix moved from the old Mac mini to this one (John, 28 Nov 2026: "I have the new Mac mini. Let's get completely prepared so it can be
# set up very quickly"). Double-click it on the NEW Mac mini, signed in with the same account name as on the old one, both Macs on the home
# network, the G-Drive still on the old one. It:
#   1. checks both Macs, puts Python on this one, and copies Grifflix's and Jellyfin's things across while the old one carries on;
#   2. when you say so, stops Grifflix on the old Mac mini, copies what changed since, and asks you to move the G-Drive across;
#   3. brings Jellyfin's database up to date in Jellyfin's own steps (10.8.13 → 10.9.11 → 10.10.7 → 12.4: rehearsed 28 Nov 2026);
#   4. starts Jellyfin, the gateway and the tunnel here, and checks the site reaches them.
# Nothing on the old Mac mini is deleted: it's the backup. If anything goes wrong after step 2, it offers to start the old one again.
# Run again after stopping part way: it carries on (copies only what's new, skips what's done).
cd "$(dirname "$0")"
HERE=$PWD
APP="$HOME/Library/Application Support/Grifflix"
G="$HOME/Grifflix"
JFD="$HOME/.local/share/jellyfin"            # Jellyfin's data, where the old Mac mini has it (10.8 kept it here; 12.4 is told to use it)
JFC="$HOME/.cache/jellyfin"
AGENTS="$HOME/Library/LaunchAgents"
WORK="$HOME/Library/Caches/grifflix-move"
LOG="$HOME/Library/Logs/grifflix-move.log"
PY=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3   # the same Python as the old Mac mini, at the same place
PY_PKG=https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg
JF_STEPS=(10.9.11 10.10.7)                    # Jellyfin's own upgrade path: 10.8 → 10.9 → 10.10.7 → 12 (12 takes 10.10.7 directly)
JF_NEW=12.4
JF_DMG="https://repo.jellyfin.org/files/server/macos/stable/v$JF_NEW/arm64/jellyfin_$JF_NEW-arm64.dmg"
JF_ARCHIVE=https://repo.jellyfin.org/archive/server/macos/stable
CF_TGZ=https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz
JFAPP=/Applications/Jellyfin.app
JFBIN=$JFAPP/Contents/MacOS
LBL=io.github.cooperking.grifflix
SITE=https://media.grifflix.co.uk
U=$(id -u)
mkdir -p "$WORK" "$APP" "$G" "$AGENTS" "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1

note() { print -- "== $(date '+%H:%M:%S') $*"; }
box() { osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with title \"Grifflix move\"" >/dev/null 2>&1; }
ask() { [ "$(osascript -e "button returned of (display dialog \"$1\" buttons {\"Not now\", \"$2\"} default button 2 with title \"Grifflix move\")" 2>/dev/null)" = "$2" ]; }
fail() { print -- "!! $1"; box "$1  (Everything it did is in ~/Library/Logs/grifflix-move.log.)"; exit 1; }
fetch() { [ -s "$2" ] || { curl -fL --retry 3 -s -o "$2.part" "$1" && mv "$2.part" "$2"; }; }
mark() { touch "$WORK/done-$1"; }
done_() { [ -f "$WORK/done-$1" ]; }

note "Grifflix move to this Mac mini (macOS $(sw_vers -productVersion), $(uname -m))"
[ "$(uname -m)" = arm64 ] || fail "This is for an Apple silicon Mac mini; this Mac is $(uname -m)."
[ "$(sw_vers -productVersion | cut -d. -f1)" -ge 12 ] || fail "Jellyfin $JF_NEW needs macOS 12 or later."

# ---- the old Mac mini, over SSH (its Remote Login is on: John signs in to it from Terminal)
OLD_HOST=$(cat "$WORK/old-host" 2>/dev/null)
OLD_HOST=$(osascript -e "text returned of (display dialog \"The old Mac mini’s address on the home network:\" default answer \"${OLD_HOST:-192.168.68.125}\" with title \"Grifflix move\")" 2>/dev/null) || exit 1
print -- "$OLD_HOST" > "$WORK/old-host"
OLD="$USER@$OLD_HOST"
SSHO=(-o ConnectTimeout=15 -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new -o ControlMaster=auto -o "ControlPath=/tmp/grifflix-%C" -o ControlPersist=8h)
old() { ssh "${SSHO[@]}" "$OLD" "$@"; }
note "Connecting to the old Mac mini ($OLD_HOST). If Terminal asks for a password, it's your password on the old Mac mini."
old true || fail "Couldn’t reach the old Mac mini at $OLD_HOST. Check it’s on, on the same network, and Remote Login is on there (System Preferences → Sharing)."
OLDHOME=$(old 'echo $HOME')
[ "$OLDHOME" = "$HOME" ] || fail "This Mac’s account is $HOME but the old Mac mini’s is $OLDHOME. Jellyfin keeps full paths, so they must match: make an account here called $(basename "$OLDHOME") (System Settings → Users & Groups), sign in to it, and run this again."
CFGWHERE=$(old 'if [ -f ~/.local/share/jellyfin/config/system.xml ]; then echo data; elif [ -f ~/.config/jellyfin/system.xml ]; then echo xdg; else echo none; fi')
[ "$CFGWHERE" = none ] && fail "Couldn’t find Jellyfin’s settings on the old Mac mini (neither ~/.local/share/jellyfin/config nor ~/.config/jellyfin)."
note "Old Mac mini: $(old 'sw_vers -productVersion'); Jellyfin's settings in $([ $CFGWHERE = data ] && print '~/.local/share/jellyfin/config' || print '~/.config/jellyfin')"
note "Sizes there: $(old 'du -sh ~/.local/share/jellyfin/metadata ~/.local/share/jellyfin/data ~/Grifflix "$HOME/Library/Application Support/Grifflix" 2>/dev/null | tr "\n\t" "; "')"
old 'crontab -l 2>/dev/null | grep -v "^#" | grep -q . && echo "(note: the old Mac mini has crontab lines: crontab -l there)"; ls ~/Library/LaunchAgents 2>/dev/null | grep -Ev "^io.github.cooperking.grifflix-(gateway|tunnel)" | sed "s/^/(other launch agent on the old Mac mini: /;s/$/)/"' || true
[ -d /Volumes/G-Drive ] && note "(the G-Drive is plugged in here already)"

# ---- Python (python.org's, as on the old Mac mini: nothing here needs Apple's command line tools)
if [ ! -x "$PY" ]; then
  note "Installing Python 3.12 (macOS asks for this Mac's password)…"
  fetch "$PY_PKG" "$WORK/python.pkg" || fail "Python didn’t download."
  sudo installer -pkg "$WORK/python.pkg" -target / || fail "Python didn’t install."
fi
"$PY" -m pip install --quiet --upgrade certifi || note "(certifi didn't install: the gateway may not reach the Worker)"

# ---- downloads, meanwhile: Jellyfin's steps and 12.4, cloudflared
note "Downloading Jellyfin ${JF_STEPS[*]} and $JF_NEW, and cloudflared, in the background…"
DL=()
for v in $JF_STEPS; do fetch "$JF_ARCHIVE/v$v/arm64/jellyfin_$v-arm64.tar.xz" "$WORK/jellyfin-$v.tar.xz" & DL+=($!); done
fetch "$JF_DMG" "$WORK/jellyfin-$JF_NEW.dmg" & DL+=($!)
fetch "$CF_TGZ" "$WORK/cloudflared.tgz" & DL+=($!)

# ---- 1. the copy, while the old Mac mini carries on (the parts that change are copied again once it's stopped)
XCL=(--exclude Grifflix/rclone --exclude Grifflix/ytdlp --exclude Grifflix/ocr --exclude 'Grifflix/node-v*.pkg' --exclude 'Grifflix/*.tmp'
     --exclude 'Grifflix/*.lock' --exclude 'Grifflix/deno-wont-run-*' --exclude '*/__pycache__' --exclude 'Library/Application Support/Grifflix/cloudflared'
     --exclude 'Library/Application Support/Grifflix/*.pkg' --exclude 'Library/Application Support/Grifflix/*.dmg' --exclude .local/share/jellyfin/transcodes
     --exclude .local/share/jellyfin/log)
PARTS=(.local/share/jellyfin 'Library/Application Support/Grifflix' Grifflix)
[ "$CFGWHERE" = xdg ] && PARTS+=(.config/jellyfin)
qparts() { local p; for p in "$@"; do print -rn -- "'$p' "; done; }
qxcl() { local x; for x in "${XCL[@]}"; do [ "$x" = --exclude ] && print -rn -- '--exclude ' || print -rn -- "'$x' "; done; }
if ! done_ copy1; then
  note "Copying from the old Mac mini (Jellyfin's pictures are the most of it; it can take a while on Wi-Fi)…"
  old "touch ~/.grifflix-move-mark && cd ~ && tar -cf - $(qxcl) $(qparts "${PARTS[@]}")" | tar -C "$HOME" -xpf - || fail "The copy stopped part way. Run this again: it starts the copy again."
  mark copy1
  note "First copy done."
fi

undo() {   # Grifflix started again on the old Mac mini (the G-Drive has to go back to it first)
  ask "Something went wrong here. Start Grifflix again on the old Mac mini? (Move the G-Drive back to it first.)" "Start the old one" || return
  for L in grifflix-tunnel grifflix-gateway grifflix-jellyfin; do launchctl bootout gui/$U/$LBL-${L#grifflix-} 2>/dev/null; done
  old 'A="$HOME/Library/Application Support/Grifflix"; U=$(id -u)
       [ -f "$A/tunnel.sh.moved" ] && mv "$A/tunnel.sh.moved" "$A/tunnel.sh"; [ -f "$A/grifflix-gateway.py.moved" ] && mv "$A/grifflix-gateway.py.moved" "$A/grifflix-gateway.py"
       for L in grifflix-gateway grifflix-tunnel; do launchctl enable gui/$U/io.github.cooperking.$L; launchctl bootstrap gui/$U ~/Library/LaunchAgents/io.github.cooperking.$L.plist; done
       open -g -a Jellyfin 2>/dev/null || (nohup /Applications/Jellyfin.app/Contents/MacOS/jellyfin --webdir /Applications/Jellyfin.app/Contents/Resources/jellyfin-web >/dev/null 2>&1 &)
       true'
  box "Grifflix is starting again on the old Mac mini. If the site isn’t back within a few minutes, restart the old Mac mini (it all starts with it again). Then tell Claude what went wrong here: the log is ~/Library/Logs/grifflix-move.log."
  rm -f "$WORK/done-stopped" "$WORK/done-switched"
}
die() { print -- "!! $1"; done_ stopped && ! done_ running && undo; fail "$1"; }   # (the old one offered back only once it's been stopped)

# ---- 2. the switch: Grifflix stopped on the old Mac mini, what changed copied again, the G-Drive moved
if ! done_ switched; then
  ask "Ready to switch? Grifflix stops on the old Mac mini now, and is back on this one in about 15–30 minutes (Jellyfin’s database is brought up to date). Check nobody’s watching." "Switch now" || { note "Not now: run this again when ready."; exit 0; }
  note "Stopping Grifflix on the old Mac mini (its files are kept: nothing is deleted)…"
  old 'A="$HOME/Library/Application Support/Grifflix"; U=$(id -u)
       for L in grifflix-tunnel grifflix-gateway; do launchctl bootout gui/$U/io.github.cooperking.$L 2>/dev/null; launchctl disable gui/$U/io.github.cooperking.$L 2>/dev/null; done
       # (and so neither starts again there, even if launchd tries: put aside, by a name the undo puts back)
       [ -f "$A/tunnel.sh" ] && mv "$A/tunnel.sh" "$A/tunnel.sh.moved"; [ -f "$A/grifflix-gateway.py" ] && mv "$A/grifflix-gateway.py" "$A/grifflix-gateway.py.moved"
       pkill -f "cloudflared tunnel"; pkill -f "grifflix-gateway.py serve"; pkill -f Grifflix/convert.py; pkill -f Grifflix/gdrive.py; pkill -f Grifflix/kermode.py
       pkill -f "Grifflix Converting"; pkill -f "Grifflix/rclone"; pkill -x "Jellyfin Server"; sleep 2; pkill -x jellyfin; sleep 8; pkill -9 -x jellyfin; true'
  sleep 3
  mark stopped
  old 'pgrep -f "cloudflared tunnel|grifflix-gateway.py serve" >/dev/null || pgrep -x jellyfin >/dev/null' && die "Grifflix didn’t stop on the old Mac mini."

  note "Copying what changed since (the databases, settings and Grifflix's own files, and new pictures)…"
  FRESH=(.local/share/jellyfin/data .local/share/jellyfin/root .local/share/jellyfin/plugins 'Library/Application Support/Grifflix' Grifflix)
  [ "$CFGWHERE" = xdg ] && FRESH+=(.config/jellyfin) || FRESH+=(.local/share/jellyfin/config)
  old "cd ~ && for p in $(qparts "${FRESH[@]}"); do [ -e \"\$p\" ] && printf '%s\0' \"\$p\"; done | xargs -0 tar -cf - $(qxcl)" | tar -C "$HOME" -xpf - || die "The second copy stopped part way."
  old "cd ~ && tar -cf - --newer-mtime-than ~/.grifflix-move-mark $(qxcl) .local/share/jellyfin/metadata" | tar -C "$HOME" -xpf - || note "(newer pictures didn't all come: Jellyfin fetches any missing again)"
  [ -f "$APP/tunnel.sh.moved" ] && mv "$APP/tunnel.sh.moved" "$APP/tunnel.sh"
  [ -f "$APP/grifflix-gateway.py.moved" ] && mv "$APP/grifflix-gateway.py.moved" "$APP/grifflix-gateway.py"
  rm -f "$WORK"/done-jf-* "$WORK/done-plugins" "$WORK/done-running"   # (a fresh copy of the 10.8 database: brought up to date from the start)
  if [ ! -d /Volumes/G-Drive/Grifflix ]; then
    old 'diskutil eject /Volumes/G-Drive' >/dev/null 2>&1 && note "G-Drive ejected on the old Mac mini." || note "(the G-Drive wouldn't eject on the old Mac mini: if it's connected on another Mac, eject it there first)"
    box "Now move the G-Drive: unplug it from the old Mac mini and plug it into this one (a USB-C cable or adapter). Click OK; this carries on by itself once it appears."
    for i in {1..360}; do [ -d /Volumes/G-Drive/Grifflix ] && break; sleep 5; done
    [ -d /Volumes/G-Drive/Grifflix ] || die "The G-Drive hasn’t appeared here (it needs to be called G-Drive, with its Grifflix folder)."
  fi
  mark switched
fi


# ---- 3. Jellyfin 12.4, and its database brought up to date
note "Waiting for the downloads…"
for p in $DL; do wait $p; done
for v in $JF_STEPS; do [ -s "$WORK/jellyfin-$v.tar.xz" ] || die "Jellyfin $v didn’t download."; done
[ -s "$WORK/jellyfin-$JF_NEW.dmg" ] || die "Jellyfin $JF_NEW didn’t download."
if [ "$(defaults read "$JFAPP/Contents/Info" CFBundleShortVersionString 2>/dev/null)" != "$JF_NEW" ]; then
  note "Putting Jellyfin $JF_NEW in Applications…"
  MNT=$(hdiutil attach -nobrowse -noverify "$WORK/jellyfin-$JF_NEW.dmg" | tail -1 | sed 's/.*\(\/Volumes\/.*\)/\1/')
  SRC=$(find "$MNT" -maxdepth 2 -name Jellyfin.app | head -1)
  rm -rf "$JFAPP"; ditto "$SRC" "$JFAPP"; hdiutil detach "$MNT" >/dev/null
  [ -x "$JFBIN/jellyfin" ] || die "Jellyfin $JF_NEW didn’t copy into Applications."
fi
CFG="$JFD/config"; [ "$CFGWHERE" = xdg ] && CFG="$HOME/.config/jellyfin"
[ -f "$CFG/system.xml" ] || die "Jellyfin’s settings didn’t come across ($CFG)."
if [ -d "$JFD/plugins" ] && ! done_ plugins; then   # (10.8's plugins don't load on 12; the ones that matter are built in now)
  rm -rf "$JFD/plugins-from-10.8"; mv "$JFD/plugins" "$JFD/plugins-from-10.8"; mark plugins
fi
jf_run() {   # one Jellyfin started on the database till it says it's ready (its upgrade done), then stopped: $1 the server, $2 its version
  local out="$WORK/jellyfin-$2.out" pid t=0
  : > "$out"
  "$1" --datadir "$JFD" --configdir "$CFG" --cachedir "$JFC" --logdir "$JFD/log" --nowebclient --ffmpeg "$JFBIN/ffmpeg" >> "$out" 2>&1 &
  pid=$!
  while kill -0 $pid 2>/dev/null && ! grep -q 'Startup complete' "$out"; do
    sleep 5; t=$((t + 5))
    (( t % 120 == 0 )) && note "  Jellyfin $2 is still bringing the database up to date ($((t / 60)) minutes)…"
    (( t > 7200 )) && break
  done
  if grep -q 'Startup complete' "$out"; then kill $pid; wait $pid 2>/dev/null; sleep 2; return 0; fi
  kill $pid 2>/dev/null; wait $pid 2>/dev/null; tail -25 "$out"; return 1
}
launchctl bootout gui/$U/$LBL-jellyfin 2>/dev/null
pkill -x jellyfin 2>/dev/null; pkill -x "Jellyfin Server" 2>/dev/null; sleep 2
for v in $JF_STEPS $JF_NEW; do
  done_ "jf-$v" && continue
  if [ $v = $JF_NEW ]; then BIN="$JFBIN/jellyfin"; else
    rm -rf "$WORK/jf-$v"; mkdir -p "$WORK/jf-$v"; tar -C "$WORK/jf-$v" -xJf "$WORK/jellyfin-$v.tar.xz" || die "Jellyfin $v wouldn’t unpack."
    BIN="$WORK/jf-$v/jellyfin/jellyfin"
  fi
  note "Jellyfin $v: bringing the database up to date…"
  if ! jf_run "$BIN" $v; then   # (refused to start? a downloaded build may need signing for this Mac: signed here, and tried once more)
    note "  …trying once more, signed for this Mac"
    xattr -cr "$(dirname "$BIN")" 2>/dev/null
    find "$(dirname "$BIN")" -type f \( -name '*.dylib' -o -perm -u+x \) -exec codesign --force -s - {} \; 2>/dev/null
    jf_run "$BIN" $v || die "Jellyfin $v couldn’t bring the database up to date (see above). The old Mac mini still has everything as it was."
  fi
  mark "jf-$v"; note "Jellyfin $v: done."
done
note "Starting Jellyfin $JF_NEW with the Mac (its folders named, so it uses the moved ones)…"
cat > "$AGENTS/$LBL-jellyfin.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LBL-jellyfin</string>
  <key>ProgramArguments</key><array><string>$JFBIN/jellyfin</string><string>--datadir</string><string>$JFD</string><string>--configdir</string><string>$CFG</string>
    <string>--cachedir</string><string>$JFC</string><string>--logdir</string><string>$JFD/log</string><string>--webdir</string><string>$JFAPP/Contents/Resources/jellyfin-web</string>
    <string>--ffmpeg</string><string>$JFBIN/ffmpeg</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$JFD/log/launchd.log</string>
  <key>StandardErrorPath</key><string>$JFD/log/launchd.log</string>
</dict></plist>
PLIST
box "macOS will now ask whether jellyfin (and, a little later, Python) may use files on a removable volume: click Allow each time. Without it Grifflix can’t see the films on the G-Drive, and on a Mac with no screen nobody would see the question."
launchctl bootstrap gui/$U "$AGENTS/$LBL-jellyfin.plist"
for i in {1..120}; do curl -s -m 3 http://127.0.0.1:8096/System/Info/Public | grep -q '"StartupWizardCompleted":true' && break; sleep 3; done
curl -s -m 3 http://127.0.0.1:8096/System/Info/Public | grep -q "\"Version\":\"$JF_NEW" || die "Jellyfin $JF_NEW didn’t start with the moved database (its log: ~/.local/share/jellyfin/log)."
note "Jellyfin $JF_NEW is running with the moved library."

# ---- 4. the gateway and the tunnel
note "cloudflared for this Mac…"
[ -s "$WORK/cloudflared.tgz" ] || die "cloudflared didn’t download."
rm -f "$APP/cloudflared"; tar -C "$APP" -xzf "$WORK/cloudflared.tgz" && chmod +x "$APP/cloudflared" || die "cloudflared wouldn’t unpack."
if ! security find-generic-password -s grifflix-tunnel -a cloudflare >/dev/null 2>&1; then
  TOKEN=$(old 'security find-generic-password -s grifflix-tunnel -a cloudflare -w' 2>/dev/null)
  [ -z "$TOKEN" ] && TOKEN=$(osascript -e 'text returned of (display dialog "The Cloudflare tunnel’s token (the old Mac mini’s keychain wouldn’t give it over the network). Cloudflare dashboard → Zero Trust → Networks → Tunnels → grifflix → Configure → the long code after --token:" default answer "" with hidden answer with title "Grifflix move")' 2>/dev/null)
  [ -n "$TOKEN" ] || die "The tunnel needs its token."
  security add-generic-password -U -s grifflix-tunnel -a cloudflare -w "$TOKEN" >/dev/null; TOKEN=
fi
cat > "$APP/tunnel.sh" <<SH
#!/bin/zsh
exec "$APP/cloudflared" tunnel --no-autoupdate --protocol http2 run --token "\$(security find-generic-password -s grifflix-tunnel -a cloudflare -w)"
SH
chmod +x "$APP/tunnel.sh"
plist() {   # $1 label end, $2.. the program and its arguments
  local l=$1; shift
  { print '<?xml version="1.0" encoding="UTF-8"?>'
    print '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    print "<plist version=\"1.0\"><dict><key>Label</key><string>$LBL-$l</string><key>ProgramArguments</key><array>"
    local a; for a in "$@"; do print "<string>$a</string>"; done
    print "</array><key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer>"
    print "<key>StandardOutPath</key><string>$APP/$l.log</string><key>StandardErrorPath</key><string>$APP/$l.log</string></dict></plist>"
  } > "$AGENTS/$LBL-$l.plist"
}
plist gateway "$PY" "$APP/grifflix-gateway.py" serve
plist tunnel "$APP/tunnel.sh"
note "The gateway's own set-up against Jellyfin $JF_NEW (signs in again; this Mac's video chip for converting)…"
"$PY" "$APP/grifflix-gateway.py" setup || die "The gateway’s set-up didn’t work (see above)."
launchctl bootout gui/$U/$LBL-gateway 2>/dev/null; launchctl bootstrap gui/$U "$AGENTS/$LBL-gateway.plist"
for i in {1..40}; do curl -s -m 3 http://127.0.0.1:8097/health | grep -q '"ok": true' && break; sleep 3; done
curl -s -m 3 http://127.0.0.1:8097/health | grep -q '"ok": true' || die "The gateway didn’t start (its log: ~/Library/Application Support/Grifflix/gateway.log)."
launchctl bootout gui/$U/$LBL-tunnel 2>/dev/null; launchctl bootstrap gui/$U "$AGENTS/$LBL-tunnel.plist"
note "Checking the site reaches this Mac through the tunnel…"
for i in {1..40}; do curl -s -m 8 "$SITE/health" | grep -q '"ok": true' && break; sleep 4; done
curl -s -m 8 "$SITE/health" | grep -q '"ok": true' || die "The site can’t reach this Mac through the tunnel ($SITE/health)."
mark running
note "Grifflix answers at $SITE from this Mac mini."

# ---- 5. this Mac kept as a server: never asleep, back on by itself after a power cut, the G-Drive shared for John's Mac
note "Server settings (macOS asks for this Mac's password)…"
sudo pmset -a sleep 0 disksleep 0 powernap 0 autorestart 1 womp 1 || note "(power settings didn't take: System Settings → Energy)"
# (headless: no looking for a Bluetooth keyboard and mouse at each start)
sudo defaults write /Library/Preferences/com.apple.Bluetooth BluetoothAutoSeekKeyboard -bool false 2>/dev/null
sudo defaults write /Library/Preferences/com.apple.Bluetooth BluetoothAutoSeekPointingDevice -bool false 2>/dev/null
sudo sharing -a /Volumes/G-Drive -S G-Drive >/dev/null 2>&1 && note "G-Drive shared on the network." || note "(share the G-Drive by hand: System Settings → General → Sharing → File Sharing → +)"

N=$(curl -s -m 30 -H "Authorization: MediaBrowser Token=\"$("$PY" -c 'import json,os;print(json.load(open(os.path.expanduser("~/Library/Application Support/Grifflix/settings.json")))["jf_token"])')\"" "http://127.0.0.1:8096/Items?Recursive=true&IncludeItemTypes=Movie&Limit=0" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("TotalRecordCount","?"))' 2>/dev/null)
note "All done: Jellyfin $JF_NEW with $N films; the gateway and tunnel running here."
box "Grifflix is running on this Mac mini ($N films). Last steps: turn the old Mac mini off; in the router’s app (Deco: More → Advanced → Address Reservation) give this Mac mini the old one’s address, $OLD_HOST; then restart this Mac mini. Keep the old one as it is for a week or two, as the backup."
