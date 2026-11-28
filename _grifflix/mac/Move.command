#!/bin/zsh
# Grifflix moved from the old Mac mini to this one (John, 28 Nov 2026: "I have the new Mac mini. Let's get completely prepared so it can be
# set up very quickly"; "as efficient as possible"). Started on the NEW Mac mini by typing, in Terminal, these two lines:
#     curl -fsLo m cooperking.github.io/grifflix/mac
#     zsh m
# (no | or other keys that move about between keyboards; grifflix/mac fetches this file and runs it), signed in as the same account name as on the old one, both Macs on the home network, the
# G-Drive still on the old one. What it does, in order:
#   1. checks both Macs, asks this Mac's password once, puts Python on, and copies Grifflix's and Jellyfin's things from the old Mac mini
#      while it carries on showing films;
#   2. when John clicks Switch now: stops Grifflix on the old Mac mini, copies what changed since, and asks for the G-Drive to be moved;
#   3. brings Jellyfin's database up to date in Jellyfin's own steps (10.8.13 → 10.9.11 → 10.10.7 → 12.4: rehearsed 28 Nov 2026);
#   4. starts Jellyfin, the gateway and the tunnel here (with the Mac, by themselves), and checks the site reaches them;
#   5. server settings: never asleep, on again after a power cut, the G-Drive shared.
# Nothing on the old Mac mini is deleted: it's the backup. If anything goes wrong after step 2 it offers to start the old one again.
# Run again after stopping part way: it carries on (copies only what's new, skips what's done).
# (GM_* settings exist only for rehearsing it away from a Mac: never set on the day.)
APP="$HOME/Library/Application Support/Grifflix"
G="$HOME/Grifflix"
JFD="$HOME/.local/share/jellyfin"            # Jellyfin's data, where the old Mac mini has it (its settings are in ~/.config/jellyfin)
JFC="$HOME/.cache/jellyfin"
AGENTS="$HOME/Library/LaunchAgents"
WORK="$HOME/Library/Caches/grifflix-move"
LOG="$HOME/Library/Logs/grifflix-move.log"
PY=${GM_PY:-/Library/Frameworks/Python.framework/Versions/3.12/bin/python3}   # the same Python as the old Mac mini, at the same place
PY_PKG=https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg
JF_STEPS=(10.9.11 10.10.7)                    # Jellyfin's own upgrade path: 10.8 → 10.9 → 10.10.7 → 12 (12 takes 10.10.7 directly)
JF_NEW=12.4
JF_DMG="https://repo.jellyfin.org/files/server/macos/stable/v$JF_NEW/arm64/jellyfin_$JF_NEW-arm64.dmg"
JF_ARCHIVE=https://repo.jellyfin.org/archive/server/macos/stable
CF_TGZ=https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz
JFAPP=${GM_JFAPP:-/Applications/Jellyfin.app}
JFBIN=$JFAPP/Contents/MacOS
GD=${GM_GDRIVE:-/Volumes/G-Drive}
LBL=io.github.cooperking.grifflix
SITE=${GM_SITE:-https://media.grifflix.co.uk}
TITLE="Setting up Grifflix"
U=$(id -u)
mkdir -p "$WORK" "$APP" "$G" "$AGENTS" "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
( command -v caffeinate >/dev/null && exec caffeinate -dims -w $$ ) &   # (awake while it works: a new Mac may otherwise sleep during the copy)

note() { print -- "== $(date '+%H:%M:%S') $*"; }
box() { osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with title \"$TITLE\"" >/dev/null 2>&1; }
ask() { [ "$(osascript -e "button returned of (display dialog \"$1\" buttons {\"Not now\", \"$2\"} default button 2 with title \"$TITLE\")" 2>/dev/null)" = "$2" ]; }
fail() { print -- "!! $1"; box "$1

If you’re not sure what to do, take a photo of this screen and send it to Claude. (What it did is also written down in the file grifflix-move.log, in the Logs folder in your Library.)"; exit 1; }
fetch() { [ -s "$2" ] || { curl -fL --retry 3 -s -o "$2.part" "$1" && mv "$2.part" "$2"; }; }
mark() { touch "$WORK/done-$1"; }
done_() { [ -f "$WORK/done-$1" ]; }

note "Setting up Grifflix on this Mac mini (macOS $(sw_vers -productVersion), $(uname -m))"
[ "$(uname -m)" = arm64 ] || fail "This is for an Apple silicon Mac mini; this Mac is $(uname -m)."
[ "$(sw_vers -productVersion | cut -d. -f1)" -ge 12 ] || fail "Jellyfin $JF_NEW needs macOS 12 or later."

# ---- this Mac's password, once (kept for the rest of the run: Python's installer and the server settings need it)
print -- ""
print -- "Type this Mac mini's password (the one you sign in with) and press Return. Nothing shows as you type: that's normal."
sudo -v || fail "That wasn’t this Mac’s password. Start again: in Terminal type  zsh m  and press Return."
( while kill -0 $$ 2>/dev/null; do sudo -n true 2>/dev/null; sleep 50; done ) &

# ---- the old Mac mini, over SSH (its Remote Login is on: John signs in to it from Terminal)
OLD_HOST=$(cat "$WORK/old-host" 2>/dev/null)
OLD_HOST=$(osascript -e "text returned of (display dialog \"The old Mac mini’s address on the home network. It’s filled in: just click OK.\" default answer \"${OLD_HOST:-192.168.68.125}\" with title \"$TITLE\")" 2>/dev/null) || exit 1
print -- "$OLD_HOST" > "$WORK/old-host"
OLD="${GM_OLDUSER:-$USER}@$OLD_HOST"
SSHO=(-n -o LogLevel=ERROR -o ConnectTimeout=15 -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new -o ControlMaster=auto -o "ControlPath=/tmp/grifflix-%C" -o ControlPersist=8h)
old() { ssh "${SSHO[@]}" "$OLD" "$@"; }
print -- ""
print -- "Now the OLD Mac mini's password (your password on the old one) and Return. Again nothing shows as you type."
old true || fail "Couldn’t reach the old Mac mini at $OLD_HOST, or its password was typed wrong three times. Check it’s switched on. If macOS asked whether Terminal may find devices on your local network, click Allow, then start again: in Terminal type  zsh m  and press Return."
OLDHOME=$(old 'echo $HOME')
[ "$OLDHOME" = "$HOME" ] || fail "This Mac’s account is $HOME but the old Mac mini’s is $OLDHOME. They must be the same: make an account here called $(basename "$OLDHOME") (System Settings → Users & Groups), sign in to it, and start again."
CFGWHERE=$(old 'if [ -f ~/.local/share/jellyfin/config/system.xml ]; then echo data; elif [ -f ~/.config/jellyfin/system.xml ]; then echo xdg; else echo none; fi')
[ "$CFGWHERE" = none ] && fail "Couldn’t find Jellyfin’s settings on the old Mac mini."
note "Old Mac mini: macOS $(old 'sw_vers -productVersion'); Jellyfin's settings in $([ $CFGWHERE = data ] && print '~/.local/share/jellyfin/config' || print '~/.config/jellyfin')"
TOTAL=$(old 'du -sk ~/.local/share/jellyfin/metadata ~/.local/share/jellyfin/data ~/Grifflix "$HOME/Library/Application Support/Grifflix" 2>/dev/null | awk "{s+=\$1} END {print s}"')
note "To copy: about $(( ${TOTAL:-0} / 1048576 )).$(( ${TOTAL:-0} % 1048576 * 10 / 1048576 )) GB"

# ---- the tunnel's token (its key to Cloudflare), now, while the old Mac mini still runs: from its running tunnel, else its keychain, else asked
# (never shown or written down; kept in this Mac's keychain, as on the old one)
if ! security find-generic-password -s grifflix-tunnel -a cloudflare >/dev/null 2>&1; then
  TOKEN=$(old 'ps -axww -o command | sed -n "s/.*cloudflared[ ]tunnel.*--token \([A-Za-z0-9_=+\/-]*\).*/\1/p" | head -1' 2>/dev/null)
  [ -z "$TOKEN" ] && TOKEN=$(old 'security find-generic-password -s grifflix-tunnel -a cloudflare -w' 2>/dev/null)
  [ -z "$TOKEN" ] && TOKEN=$(osascript -e "text returned of (display dialog \"One thing is needed from Cloudflare: the tunnel’s token, a long code. On this Mac mini open Safari, sign in at dash.cloudflare.com, then Zero Trust → Networks → Tunnels → grifflix → Configure, copy the long code after --token, click in the box below and paste it (it shows as dots). Or send Claude a photo of this and wait.\" default answer \"\" with hidden answer with title \"$TITLE\")" 2>/dev/null)
  [ -n "$TOKEN" ] || fail "The tunnel needs its token. Start again when you have it: in Terminal type  zsh m  and press Return."
  security add-generic-password -U -s grifflix-tunnel -a cloudflare -w "$TOKEN" >/dev/null; TOKEN=
fi

# ---- Python (python.org's, as on the old Mac mini: nothing here needs Apple's command line tools)
if [ ! -x "$PY" ]; then
  note "Putting Python on this Mac…"
  fetch "$PY_PKG" "$WORK/python.pkg" || fail "Python didn’t download. Check the internet, then start again: in Terminal type  zsh m  and press Return."
  sudo installer -pkg "$WORK/python.pkg" -target / >/dev/null || fail "Python didn’t install. Start again: in Terminal type  zsh m  and press Return."
fi
"$PY" -m pip install --quiet --upgrade certifi 2>/dev/null || note "(certifi didn't install: the gateway may not reach the Worker)"

# ---- downloads, meanwhile: Jellyfin's steps and 12.4, cloudflared
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
old '[ -f ~/.ssh/authorized_keys ]' && PARTS+=(.ssh/authorized_keys)   # (the keys allowed to sign in over SSH: the iPhone's goes on working)
qparts() { local p; for p in "$@"; do print -rn -- "'$p' "; done; }
qxcl() { local x; for x in "${XCL[@]}"; do [ "$x" = --exclude ] && print -rn -- '--exclude ' || print -rn -- "'$x' "; done; }
progress() {   # every minute, how much has come (so a long copy never looks stuck, and one that has stopped shows it)
  local s p=-1
  while sleep 60 && kill -0 $$ 2>/dev/null; do
    s=$(du -sk "$JFD" "$G" "$APP" 2>/dev/null | awk '{s+=$1} END {print s}')
    note "  copied so far: about $(( ${s:-0} / 1048576 )).$(( ${s:-0} % 1048576 * 10 / 1048576 )) of $(( ${TOTAL:-0} / 1048576 )).$(( ${TOTAL:-0} % 1048576 * 10 / 1048576 )) GB$([ "${s:-0}" = "$p" ] && print ' (no change this minute)')"
    p=${s:-0}
  done
}
if ! done_ copy1; then
  note "Copying from the old Mac mini. This takes a while (Grifflix carries on meanwhile): leave it be until a message appears."
  progress & PG=$!
  old "touch ~/.grifflix-move-mark && cd ~ && tar -cf - $(qxcl) $(qparts "${PARTS[@]}")" | tar -C "$HOME" -xpf -
  RC=${pipestatus[1]}${pipestatus[2]}
  kill $PG 2>/dev/null
  [ "$RC" = 00 ] || fail "The copy stopped part way. Start again: in Terminal type  zsh m  and press Return (it carries on from where it was)."
  [ -f "$HOME/.ssh/authorized_keys" ] && { chmod 700 "$HOME/.ssh"; chmod 600 "$HOME/.ssh/authorized_keys"; }
  mark copy1
  note "First copy done."
fi

undo() {   # Grifflix started again on the old Mac mini (the G-Drive has to go back to it first)
  ask "Something went wrong here, so Grifflix should go back on the old Mac mini for now.

First plug the G-Drive back into the old Mac mini. Then click Start the old one." "Start the old one" || return
  for L in tunnel gateway jellyfin; do launchctl bootout gui/$U/$LBL-$L 2>/dev/null; done
  old 'A="$HOME/Library/Application Support/Grifflix"; U=$(id -u)
       [ -f "$A/tunnel.sh.moved" ] && mv "$A/tunnel.sh.moved" "$A/tunnel.sh"; [ -f "$A/grifflix-gateway.py.moved" ] && mv "$A/grifflix-gateway.py.moved" "$A/grifflix-gateway.py"
       for L in grifflix-gateway grifflix-tunnel; do launchctl enable gui/$U/io.github.cooperking.$L; launchctl bootstrap gui/$U ~/Library/LaunchAgents/io.github.cooperking.$L.plist; done
       open -g -a Jellyfin 2>/dev/null || (nohup /Applications/Jellyfin.app/Contents/MacOS/jellyfin --webdir /Applications/Jellyfin.app/Contents/Resources/jellyfin-web >/dev/null 2>&1 &)
       true'
  box "Grifflix is starting again on the old Mac mini. If the site isn’t back within five minutes, restart the old Mac mini (hold its power button for five seconds, then press it once). Then take a photo of this screen and send it to Claude."
  rm -f "$WORK/done-stopped" "$WORK/done-switched"
}
die() { done_ stopped && ! done_ running && undo; fail "$1"; }   # (the old one offered back only once it's been stopped)

# ---- 2. the switch: Grifflix stopped on the old Mac mini, what changed copied again, the G-Drive moved
if ! done_ switched; then
  ask "The copy is done. Next, Grifflix stops on the old Mac mini and moves to this one: for 15 to 30 minutes nobody can watch.

Is anybody watching a film? If so, click Not now and start again later: in Terminal type  zsh m  and press Return (it carries on from here).
If not, click Switch now." "Switch now" || { note "Not now: when nobody's watching, type  zsh m  and press Return."; exit 0; }
  note "Stopping Grifflix on the old Mac mini (its files are kept: nothing is deleted)…"
  old 'A="$HOME/Library/Application Support/Grifflix"; U=$(id -u)
       for L in grifflix-tunnel grifflix-gateway; do launchctl bootout gui/$U/io.github.cooperking.$L 2>/dev/null; launchctl disable gui/$U/io.github.cooperking.$L 2>/dev/null; done
       # (and so neither starts again there, even if launchd tries: put aside, by a name the undo puts back)
       [ -f "$A/tunnel.sh" ] && mv "$A/tunnel.sh" "$A/tunnel.sh.moved"; [ -f "$A/grifflix-gateway.py" ] && mv "$A/grifflix-gateway.py" "$A/grifflix-gateway.py.moved"
       # (each name with one letter in brackets: the same match for the programs, but not for this command itself, whose own words
       # would otherwise match and stop it part way: found rehearsing, 28 Nov 2026)
       pkill -f "cloudflared[ ]tunnel"; pkill -f "grifflix-gateway[.]py serve"; pkill -f "Grifflix/convert[.]py"; pkill -f "Grifflix/gdrive[.]py"
       pkill -f "Grifflix/kermode[.]py"; pkill -f "Grifflix[ ]Converting"; pkill -f "Grifflix/rclone[ ]"; pkill -x "Jellyfin Server"; sleep 2; pkill -x jellyfin
       sleep 8; pkill -9 -x jellyfin; true'
  mark stopped
  sleep 3
  old 'pgrep -f "cloudflared[ ]tunnel|grifflix-gateway[.]py serve" >/dev/null || pgrep -x jellyfin >/dev/null' && die "Grifflix didn’t stop on the old Mac mini."
  note "Copying what changed since (the databases, settings and Grifflix's own files, and new pictures)…"
  FRESH=(.local/share/jellyfin/data .local/share/jellyfin/root .local/share/jellyfin/plugins 'Library/Application Support/Grifflix' Grifflix)
  [ "$CFGWHERE" = xdg ] && FRESH+=(.config/jellyfin) || FRESH+=(.local/share/jellyfin/config)
  old "cd ~ && for p in $(qparts "${FRESH[@]}"); do [ -e \"\$p\" ] && printf '%s\0' \"\$p\"; done | xargs -0 tar -cf - $(qxcl)" | tar -C "$HOME" -xpf -
  [ "${pipestatus[1]}${pipestatus[2]}" = 00 ] || die "The second copy stopped part way."
  old "cd ~ && tar -cf - --newer-mtime-than ~/.grifflix-move-mark $(qxcl) .local/share/jellyfin/metadata" | tar -C "$HOME" -xpf - || note "(newer pictures didn't all come: Jellyfin fetches any missing again)"
  [ -f "$APP/tunnel.sh.moved" ] && mv "$APP/tunnel.sh.moved" "$APP/tunnel.sh"
  [ -f "$APP/grifflix-gateway.py.moved" ] && mv "$APP/grifflix-gateway.py.moved" "$APP/grifflix-gateway.py"
  rm -f "$WORK"/done-jf-*(N) "$WORK/done-plugins" "$WORK/done-running"   # (a fresh copy of the 10.8 database: brought up to date from the start)
  if [ ! -d "$GD/Grifflix" ]; then
    old 'diskutil eject /Volumes/G-Drive' >/dev/null 2>&1 && note "G-Drive ejected on the old Mac mini." || note "(the G-Drive wouldn't eject on the old Mac mini: if it's open on another Mac, eject it there first)"
    box "Now move the G-Drive (the big external drive with the films).

1. Unplug its cable from the old Mac mini.
2. Plug it into this Mac mini.
3. Click OK.

If a message asks whether Terminal may use files on a removable volume, click Allow. If Time Machine asks about the drive, click Don’t Use."
    for i in {1..360}; do [ -d "$GD/Grifflix" ] && break; sleep 5; done
    [ -d "$GD/Grifflix" ] || die "The G-Drive hasn’t appeared on this Mac mini."
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
  MNT=$(hdiutil attach -nobrowse -noverify "$WORK/jellyfin-$JF_NEW.dmg" | awk -F'\t' 'NF > 2 && $NF ~ /\// {m = $NF} END {print m}')   # (the mount point: the last column)
  SRC=$(find "$MNT" -maxdepth 2 -name Jellyfin.app | head -1)
  rm -rf "$JFAPP"; ditto "$SRC" "$JFAPP"; hdiutil detach "$MNT" >/dev/null
  [ -x "$JFBIN/jellyfin" ] || die "Jellyfin $JF_NEW didn’t copy into Applications."
fi
CFG="$JFD/config"; [ "$CFGWHERE" = xdg ] && CFG="$HOME/.config/jellyfin"
[ -f "$CFG/system.xml" ] || die "Jellyfin’s settings didn’t come across ($CFG)."
if [ -d "$JFD/plugins" ] && ! done_ plugins; then   # (10.8's own add-ons don't load on 12, which has them built in: put aside; their settings kept)
  for d in "$JFD/plugins"/*(N/); do [ "${d:t}" = configurations ] || { mkdir -p "$JFD/plugins-from-10.8"; rm -rf "$JFD/plugins-from-10.8/${d:t}"; mv "$d" "$JFD/plugins-from-10.8/"; }; done
  mark plugins
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
    jf_run "$BIN" $v || die "Jellyfin $v couldn’t bring the database up to date."
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
box "In the next minute macOS will ask whether jellyfin, and then Python, may use files on a removable volume (the G-Drive). Click Allow each time.

Without it Grifflix can’t see the films. (If it asks whether they may find devices on your local network, Allow is fine too.)"
launchctl bootstrap gui/$U "$AGENTS/$LBL-jellyfin.plist"
for i in {1..120}; do curl -s -m 3 http://127.0.0.1:8096/System/Info/Public | grep -q '"StartupWizardCompleted":true' && break; sleep 3; done
curl -s -m 3 http://127.0.0.1:8096/System/Info/Public | grep -q "\"Version\":\"$JF_NEW" || die "Jellyfin $JF_NEW didn’t start with the moved database."
note "Jellyfin $JF_NEW is running with the moved library."

# ---- 4. the gateway and the tunnel
note "cloudflared for this Mac…"
[ -s "$WORK/cloudflared.tgz" ] || die "cloudflared didn’t download."
rm -f "$APP/cloudflared"; tar -C "$APP" -xzf "$WORK/cloudflared.tgz" && chmod +x "$APP/cloudflared" || die "cloudflared wouldn’t unpack."
security find-generic-password -s grifflix-tunnel -a cloudflare >/dev/null 2>&1 || die "The tunnel’s token isn’t here."
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
"$PY" "$APP/grifflix-gateway.py" setup || die "The gateway’s set-up didn’t work."
launchctl bootout gui/$U/$LBL-gateway 2>/dev/null; launchctl bootstrap gui/$U "$AGENTS/$LBL-gateway.plist"
for i in {1..40}; do curl -s -m 3 http://127.0.0.1:8097/health | grep -q '"ok": true' && break; sleep 3; done
curl -s -m 3 http://127.0.0.1:8097/health | grep -q '"ok": true' || die "The gateway didn’t start."
launchctl bootout gui/$U/$LBL-tunnel 2>/dev/null; launchctl bootstrap gui/$U "$AGENTS/$LBL-tunnel.plist"
note "Checking the site reaches this Mac through the tunnel…"
for i in {1..40}; do curl -s -m 8 "$SITE/health" | grep -q '"ok": true' && break; sleep 4; done
curl -s -m 8 "$SITE/health" | grep -q '"ok": true' || die "The site can’t reach this Mac through the tunnel."
mark running
note "Grifflix answers at $SITE from this Mac mini."

# ---- 5. this Mac kept as a server: never asleep, back on by itself after a power cut, the G-Drive shared for John's Mac
note "Server settings…"
sudo pmset -a sleep 0 disksleep 0 powernap 0 autorestart 1 womp 1 || note "(power settings didn't take: System Settings → Energy)"
sudo defaults write /Library/Preferences/com.apple.Bluetooth BluetoothAutoSeekKeyboard -bool false 2>/dev/null   # (no screen or keyboard: no hunting for one)
sudo defaults write /Library/Preferences/com.apple.Bluetooth BluetoothAutoSeekPointingDevice -bool false 2>/dev/null
sudo sharing -a "$GD" -S G-Drive >/dev/null 2>&1 && note "G-Drive shared on the network." || note "(the G-Drive may need sharing by hand: System Settings → General → Sharing → File Sharing → +)"

N=$(curl -s -m 30 -H "Authorization: MediaBrowser Token=\"$("$PY" -c 'import json,os;print(json.load(open(os.path.expanduser("~/Library/Application Support/Grifflix/settings.json")))["jf_token"])')\"" "http://127.0.0.1:8096/Items?Recursive=true&IncludeItemTypes=Movie&Limit=0" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("TotalRecordCount","?"))' 2>/dev/null)
note "All done: Jellyfin $JF_NEW with $N films; the gateway and tunnel running here."
# ---- 6. the old Mac mini shut down (it has no screen: from here), when John says
if ask "Grifflix is running on this Mac mini ($N films). Check a film plays on your phone.

Then: shut down the old Mac mini now? It isn’t needed any more, and keeps all its files as a backup." "Shut it down"; then
  print -- ""
  print -- "Type the OLD Mac mini's password once more and press Return (nothing shows as you type)."
  ssh -t -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -o "ControlPath=/tmp/grifflix-%C" "$OLD" 'sudo shutdown -h now' \
    && note "The old Mac mini is shutting down." || note "(the old Mac mini didn't shut down: hold its power button for five seconds instead)"
fi
box "Part 3 is done. Carry on with Part 4 of the guide: move the network cable, and change one setting in the Deco app on your phone."
