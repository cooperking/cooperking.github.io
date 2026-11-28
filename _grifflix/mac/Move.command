#!/bin/zsh
# Grifflix moved from the old Mac mini to the new one, with as little of John's time as possible (John, 28 Nov 2026: "I have the new Mac
# mini. Let's get completely prepared so it can be set up very quickly"; "Anything you can set up in a script, you should, including the
# new Mac mini set up"). Started on the NEW Mac mini, once macOS's own first questions are answered, by typing in Terminal:
#     curl -fsLo m cooperking.github.io/grifflix/mac
#     zsh m
# (grifflix/mac fetches this file and runs it). The Macs: the OLD Mac mini (2010, running Grifflix now, with the G-Drive and the network
# cable from the powerline adapter); the NEW Mac mini (this one); John's work Mac only to see this one's screen if it has to move away
# from the TV. What John does: one password, typed once in a box (the same on both Mac minis: Part 1 of the guide chooses it so);
# Allow on macOS's questions, all asked at the start while he's at the screen; Switch now; two cables moved. The rest is done here:
#   1. this Mac set up as a server: its name (grifflix), Remote Login, Screen Sharing, File Sharing, signing itself in, never asleep, on
#      again after a power cut, no Time Machine questions; it carries on by itself if it's restarted part way;
#   2. the old Mac mini found on the network and signed in to with that password (this Mac's own key put there, so never again); its SSH
#      identity copied, so the iPhone's SSH app sees the same Mac; the tunnel's token taken from it; Jellyfin 12.4 installed; macOS's
#      questions asked; Grifflix and Jellyfin copied while the old one carries on showing films; everything else made ready;
#   3. at Switch now: Grifflix stopped on the old Mac mini, what changed copied again, the G-Drive ejected and the old one switched off;
#      John moves the G-Drive's cable and the network cable across;
#   4. Jellyfin's database brought up to date in Jellyfin's own steps (10.8.13 → 10.9.11 → 10.10.7 → 12.4: rehearsed 28 Nov 2026);
#      Jellyfin, the gateway and the tunnel started here (with the Mac, by themselves); waited for till both can read the films and the
#      site reaches them;
#   5. this Mac given the old one's address (192.168.68.125, which the work Mac's grifflix-add, convert-here and the iPhone's route use)
#      and the G-Drive shared on the network.
# Nothing on the old Mac mini is deleted: it's the backup. If anything goes wrong after the switch, this one's Grifflix is stopped for good
# and the old one's started again (once its cables are back and it's switched on). Run again after stopping part way: it carries on.
# Checked three ways, 28 Nov 2026: read through; rehearsed end to end on Linux (a pretend old Mac mini with Jellyfin 10.8.13, the gateway
# and Grifflix's files, macOS's own commands stood in for); and read by an independent reviewer against macOS and the old Mac's tools.
# (GM_* settings exist only for that rehearsal: never set on the day.)
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
ADDR=${GM_ADDR:-192.168.68.125}               # the Mac mini's address at home: the old one's now, this one's at the end
OLDSSH=${GM_OLDSSH:-/etc/ssh}; NEWSSH=${GM_NEWSSH:-/etc/ssh}
TITLE="Setting up Grifflix"
SELF=${0:A}
U=$(id -u)
mkdir -p "$WORK" "$APP" "$G" "$AGENTS" "$(dirname "$LOG")" "$HOME/.ssh"
exec > >(tee -a "$LOG") 2>&1
( command -v caffeinate >/dev/null && exec caffeinate -dims -w $$ ) &   # (awake while it works: a new Mac may otherwise sleep during the copy)

note() { print -- "== $(date '+%H:%M:%S') $*"; }
speak() { ( command say "$1" >/dev/null 2>&1 & ) }   # (out loud too, at the moments John's needed: he may be in the next room)
# (each box brought to the front: from Terminal one can otherwise open behind it and look stuck; in a try, so it can never stop the box)
ACT=(-e 'try' -e 'activate' -e 'end try')
box() { osascript "${ACT[@]}" -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with title \"$TITLE\"" >/dev/null 2>&1; }
ask() { [ "$(osascript "${ACT[@]}" -e "button returned of (display dialog \"$1\" buttons {\"Not now\", \"$2\"} default button 2 with title \"$TITLE\")" 2>/dev/null)" = "$2" ]; }
answer() { osascript "${ACT[@]}" -e "text returned of (display dialog \"$1\" default answer \"$2\" with title \"$TITLE\" $3)" 2>/dev/null; }
# (a box that goes by itself once what it asks for has happened: autobox shows it, endbox takes it away)
autobox() { osascript "${ACT[@]}" -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with title \"$TITLE\" giving up after 3600" >/dev/null 2>&1 & ABOX=$!; }
endbox() { kill $ABOX 2>/dev/null; }
fail() { print -- "!! $1"; box "$1

If you’re not sure what to do, take a photo of this screen and send it to Claude. (What it did is also written down in the file grifflix-move.log, in the Logs folder in your Library.)"; exit 1; }
AGAIN="Start again: in Terminal type  zsh m  and press Return (it carries on from where it was)."
fetch() { [ -s "$2" ] || { curl -fL --retry 3 -s -o "$2.part$$" "$1" && mv "$2.part$$" "$2"; }; }   # (its own part name: one left running by an earlier go can't mix in)
mark() { touch "$WORK/done-$1"; }
done_() { [ -f "$WORK/done-$1" ]; }
plistto() {   # $1 the file, $2 the label's end, $3 KeepAlive true or false, $4.. the program and its arguments
  local f=$1 l=$2 k=$3; shift 3
  { print '<?xml version="1.0" encoding="UTF-8"?>'
    print '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    print "<plist version=\"1.0\"><dict><key>Label</key><string>$LBL-$l</string><key>ProgramArguments</key><array>"
    local a; for a in "$@"; do a=${a//&/&amp;}; a=${a//</&lt;}; print "<string>${a//>/&gt;}</string>"; done
    print "</array><key>RunAtLoad</key><true/><key>KeepAlive</key><$k/><key>ThrottleInterval</key><integer>10</integer>"
    print "<key>StandardOutPath</key><string>${OUT:-/dev/null}</string><key>StandardErrorPath</key><string>${OUT:-/dev/null}</string></dict></plist>"
  } > "$f"
}

# (one at a time: started again by hand while it's already running, the second just says so)
if [ -f "$WORK/pid" ] && [ "$(cat "$WORK/pid")" != $$ ] && ps -p "$(cat "$WORK/pid")" -o command= 2>/dev/null | grep -q Move; then
  print -- "The set-up is already running in another Terminal window: look for it."; exit 0
fi
print -- $$ > "$WORK/pid"

note "Setting up Grifflix on this Mac mini (macOS $(sw_vers -productVersion), $(uname -m))"
[ "$(uname -m)" = arm64 ] || fail "This is for an Apple silicon Mac mini; this Mac is $(uname -m)."
[ "$(sw_vers -productVersion | cut -d. -f1)" -ge 12 ] || fail "Jellyfin $JF_NEW needs macOS 12 or later."

# ---- the password, typed once in a box (the same on both Mac minis). Kept in this Mac's keychain until the set-up is done, so a restart
# part way needs nothing typed; taken away at the end
PWS=grifflix-setup
kc() { security find-generic-password -s $PWS -a "$1" -w 2>/dev/null; }
kcput() { security add-generic-password -U -s $PWS -a "$1" -w "$2" >/dev/null 2>&1; }
pwok() { printf '%s\n' "$1" | sudo -S -p '' -v >/dev/null 2>&1; }
PW=$(kc this)
if [ -z "$PW" ] || ! pwok "$PW"; then
  PW=""; q="Type your password, then click OK: the one you chose for this Mac mini (the same as the old Mac mini’s). It shows as dots."
  for n in 1 2 3; do
    P=$(answer "$q" "" "with hidden answer") || exit 1
    pwok "$P" && { PW=$P; break; }
    q="That isn’t this Mac mini’s password. Try again (capital letters count), then click OK."
  done
  [ -n "$PW" ] || fail "That wasn’t this Mac mini’s password. $AGAIN"
  kcput this "$PW"
fi
( while kill -0 $$ 2>/dev/null; do printf '%s\n' "$PW" | sudo -S -p '' -v >/dev/null 2>&1; sleep 50; done ) &

# (started again by itself if this Mac restarts before the end, e.g. moved to the old one's place: Terminal opens with it at sign-in)
chmod +x "$SELF" 2>/dev/null
[ -f "$AGENTS/$LBL-setup.plist" ] || plistto "$AGENTS/$LBL-setup.plist" setup false /usr/bin/open -a Terminal "$SELF"

# ---- 1. this Mac set up as a server
MISS=()
listening() { nc -z -G 3 127.0.0.1 "$1" >/dev/null 2>&1; }
if ! done_ server; then
  note "Setting this Mac mini up as a server (its name, sharing, signing itself in, never asleep)…"
  sudo scutil --set ComputerName Grifflix; sudo scutil --set LocalHostName grifflix   # (vnc://grifflix.local from the work Mac)
  sudo pmset -a sleep 0 disksleep 0 powernap 0 autorestart 1 womp 1 >/dev/null || note "(power settings didn't take: System Settings → Energy)"
  sudo defaults write /Library/Preferences/com.apple.Bluetooth BluetoothAutoSeekKeyboard -bool false 2>/dev/null   # (no keyboard: no hunting for one)
  sudo defaults write /Library/Preferences/com.apple.Bluetooth BluetoothAutoSeekPointingDevice -bool false 2>/dev/null
  sudo defaults write /Library/Preferences/com.apple.TimeMachine DoNotOfferNewDisksForBackup -bool true 2>/dev/null   # (no question when the G-Drive comes)
  sudo systemsetup -setremotelogin on >/dev/null 2>&1                            # Remote Login (SSH: the iPhone's way in)
  listening 22 || sudo launchctl load -w /System/Library/LaunchDaemons/ssh.plist >/dev/null 2>&1   # (systemsetup can't on newer macOS)
  sudo launchctl load -w /System/Library/LaunchDaemons/com.apple.screensharing.plist >/dev/null 2>&1   # Screen Sharing (the work Mac's view)
  sudo launchctl load -w /System/Library/LaunchDaemons/com.apple.smbd.plist >/dev/null 2>&1           # File Sharing (the G-Drive's share)
  if fdesetup status 2>/dev/null | grep -q 'FileVault is On'; then   # (FileVault on, it can't sign itself in: off)
    note "Turning FileVault off (so this Mac signs itself in after a power cut)…"
    p=${PW//&/&amp;}; p=${p//</&lt;}; p=${p//>/&gt;}
    print -- "<?xml version=\"1.0\" encoding=\"UTF-8\"?><plist version=\"1.0\"><dict><key>Username</key><string>$USER</string><key>Password</key><string>$p</string></dict></plist>" \
      | sudo fdesetup disable -inputplist >/dev/null 2>&1; p=
  fi
  sudo sysadminctl -autologin set -userName "$USER" -password "$PW" >/dev/null 2>&1   # (signs itself in: Grifflix starts with no one there)
  sleep 2
  listening 22 || MISS+=("Remote Login")
  listening 5900 || MISS+=("Screen Sharing")
  if [ ${#MISS} -gt 0 ]; then   # (what didn't switch on by itself, asked for now, while John's at the screen)
    box "Please switch on ${(j: and :)MISS} by hand (it wouldn’t switch on by itself): click the Apple logo at the top left, System Settings, General, Sharing, and click the switch next to ${(j: and next to :)MISS}. Then click OK here."
  fi
  if [ "$(defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null)" != "$USER" ]; then
    box "Please set one thing by hand: click the Apple logo at the top left, System Settings, Users & Groups, and next to Automatically log in as choose your name (it asks for your password). If that menu is grey, FileVault is still switching off: wait five minutes and try again. Then click OK here. (So this Mac mini signs itself in after a power cut.)"
  fi
  MISS=()
  mark server
fi

# ---- 2. the old Mac mini: found on the network by itself, signed in to with the password; this Mac's own key put there, so a second go,
# or a dropped connection, never needs it again
MOVED=""; done_ running && MOVED=1   # (already moved, and run again: the old Mac mini, off by now, isn't needed)
SSHK=(-o LogLevel=ERROR -o ConnectTimeout=15 -o ServerAliveInterval=15 -o StrictHostKeyChecking=accept-new -o NumberOfPasswordPrompts=1 -o ControlMaster=auto -o "ControlPath=/tmp/grifflix-%C" -o ControlPersist=8h)
SSHO=(-n "${SSHK[@]}")
old() { ssh "${SSHO[@]}" "$OLD" "$@"; }
keyok() { ssh -n -o BatchMode=yes "${SSHK[@]}" "$OLD" true 2>/dev/null; }   # (in without a password: this Mac's key)
oldsudo() { printf '%s\n' "$OLDPW" | ssh "${SSHK[@]}" "$OLD" "sudo -S -p '' $1"; }   # (a command as the old Mac's administrator)
reach() { nc -z -G 5 "$1" 22 >/dev/null 2>&1; }
print -r -- '#!/bin/sh
printf "%s\n" "$GRIFFLIX_PW"' > "$WORK/askpass"; chmod 700 "$WORK/askpass"
login() {   # signed in with a password given here, not typed in Terminal; this Mac's key put on the old one
  GRIFFLIX_PW="$1" SSH_ASKPASS="$WORK/askpass" SSH_ASKPASS_REQUIRE=force DISPLAY=:0 ssh -o ControlMaster=no -o ControlPath=none "${SSHK[@]}" "$OLD" \
    'mkdir -p ~/.ssh && chmod 700 ~/.ssh && k=$(cat) && { grep -qxF "$k" ~/.ssh/authorized_keys 2>/dev/null || echo "$k" >> ~/.ssh/authorized_keys; } && chmod 600 ~/.ssh/authorized_keys; true' \
    < "$HOME/.ssh/id_ed25519.pub" 2>/dev/null
}
OLD_HOST=$(cat "$WORK/old-host" 2>/dev/null)
OLDPW=$(kc old); OLDPW=${OLDPW:-$PW}
if [ -n "$MOVED" ]; then
  OLD="${GM_OLDUSER:-$USER}@$OLD_HOST"; CFGWHERE=$(cat "$WORK/cfg-where")
else
  note "Looking for the old Mac mini on the home network (if macOS asks whether Terminal may find devices on your local network, click Allow)…"
  H=""
  for t in {1..3}; do
    for i in {1..18}; do
      for h in $OLD_HOST $ADDR Johns-Mac-mini.local; do reach "$h" && { H=$h; break 3; }; done
      sleep 5
    done
    (( t < 3 )) && OLD_HOST=$(answer "Couldn’t find the old Mac mini on the home network. Check its light is on and its network cable is in. Then click OK to look again (or, if you know its address, type it in first)." "${OLD_HOST:-$ADDR}") || exit 1
  done
  [ -n "$H" ] || fail "Couldn’t find the old Mac mini on the home network. Check it’s switched on and its network cable is in. $AGAIN"
  OLD_HOST=$H; print -- "$OLD_HOST" > "$WORK/old-host"
  OLD="${GM_OLDUSER:-$USER}@$OLD_HOST"
  [ -f "$HOME/.ssh/id_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519" </dev/null >/dev/null
  if ! keyok; then
    if ! login "$OLDPW"; then
      q="The old Mac mini’s password isn’t the same as this one’s. Type the old Mac mini’s password, then click OK."
      for n in 1 2 3; do
        P=$(answer "$q" "" "with hidden answer") || exit 1
        login "$P" && { OLDPW=$P; kcput old "$P"; break; }
        q="That isn’t the old Mac mini’s password either. Try again (capital letters count), then click OK."
      done
    fi
    keyok || fail "Couldn’t sign in to the old Mac mini at $OLD_HOST. $AGAIN"
  fi
  [ "$(old 'uname -m')" = arm64 ] && fail "That was this Mac mini answering, not the old one. Check the old Mac mini is switched on, then $AGAIN"
  OLDHOME=$(old 'echo $HOME')
  [ "$OLDHOME" = "$HOME" ] || fail "This Mac’s account is $HOME but the old Mac mini’s is $OLDHOME. They must be the same: make an account here called $(basename "$OLDHOME") (System Settings → Users & Groups), sign in to it, and start again."
  CFGWHERE=$(old 'if [ -f ~/.local/share/jellyfin/config/system.xml ]; then echo data; elif [ -f ~/.config/jellyfin/system.xml ]; then echo xdg; else echo none; fi')
  [ "$CFGWHERE" = none ] && fail "Couldn’t find Jellyfin’s settings on the old Mac mini."
  print -- "$CFGWHERE" > "$WORK/cfg-where"
fi
[ "$CFGWHERE" = xdg ] && CFG="$HOME/.config/jellyfin" || CFG="$JFD/config"
gb() { print -n "$(( ${1:-0} / 1048576 )).$(( ${1:-0} % 1048576 * 10 / 1048576 ))"; }
if [ -z "$MOVED" ]; then
note "Old Mac mini: macOS $(old 'sw_vers -productVersion'); Jellyfin's settings in ${CFG/#$HOME/~}"
TOTAL=$(old 'du -sk ~/.local/share/jellyfin/metadata ~/.local/share/jellyfin/data ~/Grifflix "$HOME/Library/Application Support/Grifflix" 2>/dev/null | awk "{s+=\$1} END {print s}"')
done_ copy1 || note "To copy: about $(gb $TOTAL) GB"

# (its SSH identity, so the iPhone's SSH app and the work Mac see the same Mac at the same address, with no warning)
if ! done_ hostkeys; then
  oldsudo "sh -c 'cd $OLDSSH && tar -cf - \$(ls ssh_host_rsa_key ssh_host_rsa_key.pub ssh_host_ecdsa_key ssh_host_ecdsa_key.pub ssh_host_ed25519_key ssh_host_ed25519_key.pub 2>/dev/null)'" 2>/dev/null \
    | sudo tar -C "$NEWSSH" -xpf - 2>/dev/null
  [ "${pipestatus[1]}${pipestatus[2]}" = 00 ] && note "The old Mac mini's SSH identity copied." || note "(the old Mac mini's SSH identity didn't come: the iPhone's SSH app will say this Mac's identity changed)"
  mark hostkeys
fi

# ---- the tunnel's token (its key to Cloudflare), while the old Mac mini still runs: from its running tunnel, else its keychain, else asked
# (never shown or written down; kept in this Mac's keychain, as on the old one)
if ! security find-generic-password -s grifflix-tunnel -a cloudflare >/dev/null 2>&1; then
  TOKEN=$(old 'ps -axww -o command | sed -n "s/.*cloudflared[ ]tunnel.*--token \([A-Za-z0-9_=+\/-]*\).*/\1/p" | head -1' 2>/dev/null)
  [ -z "$TOKEN" ] && TOKEN=$(old 'security find-generic-password -s grifflix-tunnel -a cloudflare -w' 2>/dev/null)
  [ -z "$TOKEN" ] && TOKEN=$(answer "One thing is needed from Cloudflare: the tunnel’s token, a long code. On this Mac mini open Safari, sign in at dash.cloudflare.com, then Zero Trust → Networks → Tunnels → grifflix → Configure, copy the long code after --token, click in the box below and paste it (it shows as dots). Or send Claude a photo of this and wait." "" "with hidden answer")
  [ -n "$TOKEN" ] || fail "The tunnel needs its token. Start again when you have it: in Terminal type  zsh m  and press Return."
  security add-generic-password -U -s grifflix-tunnel -a cloudflare -w "$TOKEN" >/dev/null; TOKEN=
fi
fi

# ---- Python (python.org's, as on the old Mac mini: nothing here needs Apple's command line tools)
if [ ! -x "$PY" ]; then
  note "Putting Python on this Mac…"
  fetch "$PY_PKG" "$WORK/python.pkg" || fail "Python didn’t download. Check the internet. $AGAIN"
  sudo installer -pkg "$WORK/python.pkg" -target / >/dev/null || fail "Python didn’t install. $AGAIN"
fi
"$PY" -m pip install --quiet --upgrade certifi 2>/dev/null || note "(certifi didn't install: the gateway may not reach the Worker)"

# ---- downloads, meanwhile: Jellyfin 12.4 first (it goes in now, while John's at the screen), then Jellyfin's steps and cloudflared
DL=()
if ! done_ prepared; then
  fetch "$JF_DMG" "$WORK/jellyfin-$JF_NEW.dmg" & DMGP=$!
  for v in $JF_STEPS; do fetch "$JF_ARCHIVE/v$v/arm64/jellyfin_$v-arm64.tar.xz" "$WORK/jellyfin-$v.tar.xz" & DL+=($!); done
  fetch "$CF_TGZ" "$WORK/cloudflared.tgz" & DL+=($!)
fi
jfin() {   # Jellyfin 12.4 from its disk image into Applications (yes: agrees to a licence, should it ever have one, not waiting)
  local mnt src
  mnt=$(yes | hdiutil attach -nobrowse -noverify "$WORK/jellyfin-$JF_NEW.dmg" | awk -F'\t' 'NF > 2 && $NF ~ /\// {m = $NF} END {print m}')   # (the mount point: the last column)
  src=$(find "$mnt" -maxdepth 2 -name Jellyfin.app 2>/dev/null | head -1)
  [ -n "$src" ] && { rm -rf "$JFAPP"; ditto "$src" "$JFAPP"; }
  hdiutil detach "$mnt" >/dev/null 2>&1
  [ -x "$JFBIN/jellyfin" ]
}
if [ "$(defaults read "$JFAPP/Contents/Info" CFBundleShortVersionString 2>/dev/null)" != "$JF_NEW" ]; then
  note "Fetching Jellyfin $JF_NEW (a minute or two)…"
  [ -n "$DMGP" ] && wait $DMGP 2>/dev/null || fetch "$JF_DMG" "$WORK/jellyfin-$JF_NEW.dmg"
  [ -s "$WORK/jellyfin-$JF_NEW.dmg" ] || fail "Jellyfin $JF_NEW didn’t download. Check the internet. $AGAIN"
  note "Putting Jellyfin $JF_NEW in Applications (if macOS asks whether Terminal may use files on a removable volume, click Allow)…"
  if ! jfin; then
    box "If macOS asked whether Terminal may use files on a removable volume, click Allow on that message (it may be behind this window), then click OK here."
    jfin || { rm -f "$WORK/jellyfin-$JF_NEW.dmg"; fail "Jellyfin $JF_NEW didn’t go into Applications (its download may be damaged: it’s fetched again). $AGAIN"; }
  fi
fi

# ---- macOS's questions, asked now, while John's at the screen: whether Terminal, Python and jellyfin may use files on a removable volume.
# Asked on a small disk of its own (macOS asks once per program, for every removable volume, the G-Drive too); Python and jellyfin each
# started as the Mac will start them, so the answer is theirs. Where macOS doesn't ask for this disk, it asks when the G-Drive comes
probe() { local l=$1; shift; plistto "$WORK/$l.plist" "$l" false "$@"; launchctl bootout gui/$U/$LBL-$l 2>/dev/null; launchctl bootstrap gui/$U "$WORK/$l.plist" 2>/dev/null; }
if ! done_ allowed; then
  note "Asking macOS's questions now, while you're here (click Allow on each)…"
  rm -f "$WORK/check.dmg"; hdiutil create -quiet -size 4m -fs HFS+ -volname "Grifflix check" "$WORK/check.dmg" >/dev/null 2>&1
  CHK=$(hdiutil attach -nobrowse "$WORK/check.dmg" 2>/dev/null | awk -F'\t' 'NF > 2 && $NF ~ /\// {m = $NF} END {print m}')
  if [ -d "$CHK" ]; then
    rm -rf "$WORK/probe"; mkdir -p "$WORK/probe"
    pyprobe() { probe pyprobe "$PY" -c "open('$CHK/python', 'w').write('ok')"; }
    jfprobe() { probe jfprobe "$JFBIN/jellyfin" --datadir "$WORK/probe/data" --configdir "$WORK/probe/config" --cachedir "$WORK/probe/cache" --logdir "$CHK/jellyfin" --nowebclient --ffmpeg "$JFBIN/ffmpeg"; }
    allowed() { touch "$CHK/terminal" 2>/dev/null; [ -f "$CHK/terminal" ] && [ -f "$CHK/python" ] && [ -d "$CHK/jellyfin" ]; }
    pyprobe; jfprobe
    for i in {1..24}; do allowed && break; sleep 5; done
    n=0
    while ! allowed && (( n < 2 )); do
      n=$((n + 1))
      box "macOS is asking whether Terminal, Python or jellyfin may use files on a removable volume. Look for its messages (one may be behind this window) and click Allow on each. Then click OK here."
      [ -f "$CHK/python" ] || pyprobe; [ -d "$CHK/jellyfin" ] || jfprobe
      for i in {1..12}; do allowed && break; sleep 5; done
    done
    launchctl bootout gui/$U/$LBL-jfprobe 2>/dev/null; launchctl bootout gui/$U/$LBL-pyprobe 2>/dev/null; sleep 2
    hdiutil detach "$CHK" -force >/dev/null 2>&1
  fi
  rm -rf "$WORK/check.dmg" "$WORK/probe" "$WORK"/*probe.plist(N)
  mark allowed
fi

# ---- the copy, while the old Mac mini carries on (the parts that change are copied again once it's stopped)
XCL=(--exclude Grifflix/rclone --exclude Grifflix/ytdlp --exclude Grifflix/ocr --exclude 'Grifflix/node-v*.pkg' --exclude 'Grifflix/*.tmp'
     --exclude 'Grifflix/*.lock' --exclude 'Grifflix/deno-wont-run-*' --exclude '*/__pycache__' --exclude 'Library/Application Support/Grifflix/cloudflared'
     --exclude 'Library/Application Support/Grifflix/*.pkg' --exclude 'Library/Application Support/Grifflix/*.dmg' --exclude .local/share/jellyfin/transcodes
     --exclude .local/share/jellyfin/log)
qparts() { local p; for p in "$@"; do print -rn -- "'$p' "; done; }
qxcl() { local x; for x in "${XCL[@]}"; do [ "$x" = --exclude ] && print -rn -- '--exclude ' || print -rn -- "'$x' "; done; }
progress() {   # every minute, how much has come (so a long copy never looks stuck, and one that has stopped shows it)
  local s p=-1
  while sleep 60 && kill -0 $$ 2>/dev/null; do
    s=$(du -sk "$JFD" "$G" "$APP" 2>/dev/null | awk '{s+=$1} END {print s}')
    note "  copied so far: about $(gb $s) of $(gb $TOTAL) GB$([ "${s:-0}" = "$p" ] && print ' (no change this minute)')"
    p=${s:-0}
  done
}
if ! done_ copy1; then
  PARTS=(.local/share/jellyfin 'Library/Application Support/Grifflix' Grifflix)
  [ "$CFGWHERE" = xdg ] && PARTS+=(.config/jellyfin)
  old '[ -f ~/.ssh/authorized_keys ]' && PARTS+=(.ssh/authorized_keys)   # (the keys allowed to sign in over SSH: the iPhone's goes on working)
  note "Copying from the old Mac mini. This takes 10 to 30 minutes, and films carry on meanwhile: you can leave it. It says out loud when it's ready."
  progress & PG=$!
  old "touch ~/.grifflix-move-mark && cd ~ && tar -cf - $(qxcl) $(qparts "${PARTS[@]}")" | tar -C "$HOME" -xpf -
  RS=(${pipestatus[@]})
  kill $PG 2>/dev/null
  # (the old Mac's tar says 1 when a file went while it was copying, as Grifflix's own do while it runs: fine, all that changes is copied again)
  [ "${RS[2]}" = 0 ] && [ "${RS[1]}" -le 1 ] || fail "The copy stopped part way. $AGAIN"
  [ -f "$HOME/.ssh/authorized_keys" ] && { chmod 700 "$HOME/.ssh"; chmod 600 "$HOME/.ssh/authorized_keys"; }
  mark copy1
  note "First copy done."
fi

# ---- everything else made ready now, while films still play: anything that fails here stops before the old Mac mini is touched
if ! done_ prepared; then
  note "Getting the rest ready (films still play meanwhile)…"
  for p in $DL; do wait $p 2>/dev/null; done
  for v in $JF_STEPS; do [ -s "$WORK/jellyfin-$v.tar.xz" ] || fail "Jellyfin $v didn’t download. Check the internet. $AGAIN"; done
  [ -s "$WORK/cloudflared.tgz" ] || fail "cloudflared didn’t download. Check the internet. $AGAIN"
  for v in $JF_STEPS; do   # (the in-between Jellyfins, unpacked and signed for this Mac: 10.10.7's comes unsigned, which Apple silicon won't run)
    rm -rf "$WORK/jf-$v"; mkdir -p "$WORK/jf-$v"
    tar -C "$WORK/jf-$v" -xJf "$WORK/jellyfin-$v.tar.xz" || { rm -f "$WORK/jellyfin-$v.tar.xz"; fail "Jellyfin $v didn’t download properly (it’s fetched again). Check the internet. $AGAIN"; }
    xattr -cr "$WORK/jf-$v" 2>/dev/null
    find "$WORK/jf-$v" -type f \( -name '*.dylib' -o -name jellyfin -o -name createdump \) -exec codesign --force -s - {} \; 2>/dev/null   # (its programs: checked in both, 28 Nov 2026)
  done
  rm -f "$WORK/cloudflared"; tar -C "$WORK" -xzf "$WORK/cloudflared.tgz" && chmod +x "$WORK/cloudflared" || { rm -f "$WORK/cloudflared.tgz"; fail "cloudflared didn’t download properly (it’s fetched again). Check the internet. $AGAIN"; }
  mark prepared
fi

undo() {   # Grifflix started again on the old Mac mini; this one's own stopped for good (not started again by a restart)
  local L here=""
  for L in tunnel gateway jellyfin; do
    launchctl bootout gui/$U/$LBL-$L 2>/dev/null; launchctl disable gui/$U/$LBL-$L 2>/dev/null
    [ -f "$AGENTS/$LBL-$L.plist" ] && mv -f "$AGENTS/$LBL-$L.plist" "$AGENTS/$LBL-$L.plist.off"
  done
  [ -d "$GD/Grifflix" ] && here=1
  [ -n "$here" ] && { diskutil eject "$GD" >/dev/null 2>&1 || diskutil unmount force "$GD" >/dev/null 2>&1; }
  speak "Something went wrong. Grifflix is going back on the old Mac mini."
  ask "Something went wrong here, so Grifflix goes back on the old Mac mini for now.

1. $([ -n "$here" ] && print 'Move the G-Drive’s cable back from this Mac mini to the old one, and the network cable too.' || print 'Make sure the G-Drive’s cable and the network cable are in the old Mac mini (move them back from this one if you moved them).')
2. If the old Mac mini’s light is off, press its power button.

Then click Start the old one." "Start the old one" || return
  note "Waiting for the old Mac mini to answer (it takes a couple of minutes to start)…"
  for i in {1..60}; do keyok && break; sleep 5; done
  keyok || { box "The old Mac mini isn’t answering. Check its light is on and the network cable is in it. Then send Claude a photo of this screen."; return; }
  # (Grifflix there needs it signed in, which it does by itself after a restart; if it's waiting at its sign-in screen, the work Mac signs it in)
  for i in {1..24}; do old 'launchctl print gui/$(id -u) >/dev/null 2>&1' && break; sleep 5; done
  old 'launchctl print gui/$(id -u) >/dev/null 2>&1' || box "The old Mac mini is waiting for someone to sign in. On your work Mac: Finder → Go → Connect to Server, type  vnc://$OLD_HOST  and click Connect, then sign in to the old Mac mini with its password. Then click OK here."
  old 'A="$HOME/Library/Application Support/Grifflix"; U=$(id -u)
       [ -f "$A/tunnel.sh.moved" ] && mv "$A/tunnel.sh.moved" "$A/tunnel.sh"; [ -f "$A/grifflix-gateway.py.moved" ] && mv "$A/grifflix-gateway.py.moved" "$A/grifflix-gateway.py"
       for L in grifflix-gateway grifflix-tunnel; do launchctl enable gui/$U/io.github.cooperking.$L; launchctl bootout gui/$U/io.github.cooperking.$L 2>/dev/null; launchctl bootstrap gui/$U ~/Library/LaunchAgents/io.github.cooperking.$L.plist; done
       pgrep -x "Jellyfin Server" >/dev/null || open -g -a Jellyfin 2>/dev/null || (nohup /Applications/Jellyfin.app/Contents/MacOS/jellyfin --webdir /Applications/Jellyfin.app/Contents/Resources/jellyfin-web >/dev/null 2>&1 &)
       true'
  rm -f "$WORK/done-stopped" "$WORK/done-switched"
  note "Waiting for Grifflix to answer again from the old Mac mini…"
  for i in {1..45}; do curl -s -m 8 "$SITE/health" | grep -q '"ok": true' && break; sleep 4; done
  if ! curl -s -m 8 "$SITE/health" | grep -q '"ok": true' && ask "Grifflix hasn’t come back yet. Restart the old Mac mini?" "Restart it"; then
    oldsudo 'shutdown -r now' >/dev/null 2>&1
  fi
  box "Grifflix is going back on the old Mac mini: films should play again within five minutes. Send Claude a photo of the Terminal window, so it can see what went wrong here."
}
die() { done_ stopped && ! done_ running && undo; fail "$1"; }   # (the old one offered back only once it's been stopped)

# ---- 3. the switch: Grifflix stopped on the old Mac mini, what changed copied again, the old one switched off, two cables moved
if ! done_ switched; then
  speak "Grifflix is ready to switch over."
  C=$(osascript "${ACT[@]}" -e "button returned of (display dialog \"Everything is ready. Next, Grifflix moves from the old Mac mini to this one: for 15 to 30 minutes nobody can watch.

Is anybody watching a film? Click Not now, and later in Terminal type  zsh m  and press Return.
Is this Mac mini somewhere else, not next to the old one? Click Move it first.
Otherwise, click Switch now.\" buttons {\"Not now\", \"Move it first\", \"Switch now\"} default button 3 with title \"$TITLE\")" 2>/dev/null)
  case $C in
    "Switch now") ;;
    "Move it first")
      box "This Mac mini now switches itself off. Then:

1. Unplug it and take it to the old Mac mini.
2. Plug in its power cable, and nothing else yet.
3. Press its power button, on the back.

In a couple of minutes it carries on by itself. To see its screen from your work Mac: Finder → Go → Connect to Server, type  vnc://grifflix.local  and click Connect, then sign in with your password."
      note "Switching off, to be moved. Switched on again, it carries on by itself."
      sudo shutdown -h now >/dev/null 2>&1; exit 0 ;;
    *) note "Not now: when nobody's watching, type  zsh m  and press Return."; exit 0 ;;
  esac
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
  # (Jellyfin's databases and settings taken afresh, not laid over the first copy: its side files from while it ran, or a later Jellyfin's
  # from an earlier go, could otherwise be mixed in with them)
  AGAIN_UP=""; [ -n "$(print -l "$WORK"/done-jf-*(N))" ] && AGAIN_UP=1   # (an earlier go's upgrade ran here: it may have moved pictures about)
  rm -rf "$JFD/data" "$JFD/root" "$JFD/plugins" "$JFD/plugins-from-10.8" "$CFG" "$JFC"
  FRESH=(.local/share/jellyfin/data .local/share/jellyfin/root .local/share/jellyfin/plugins 'Library/Application Support/Grifflix' Grifflix)
  [ "$CFGWHERE" = xdg ] && FRESH+=(.config/jellyfin) || FRESH+=(.local/share/jellyfin/config)
  old "cd ~ && set -- && for p in $(qparts "${FRESH[@]}"); do [ -e \"\$p\" ] && set -- \"\$@\" \"\$p\"; done; tar -cf - $(qxcl) \"\$@\"" | tar -C "$HOME" -xpf -
  RS=(${pipestatus[@]})
  [ "${RS[2]}" = 0 ] && [ "${RS[1]}" -le 1 ] || die "The second copy stopped part way."
  for f in library.db jellyfin.db; do [ -s "$JFD/data/$f" ] || die "Jellyfin’s database ($f) didn’t come across."; done
  # (the pictures: those new since the first copy; all of them again after an earlier go's upgrade)
  old "cd ~ && tar -cf - $([ -z "$AGAIN_UP" ] && print -- '--newer-mtime-than ~/.grifflix-move-mark') $(qxcl) .local/share/jellyfin/metadata" | tar -C "$HOME" -xpf - || note "(newer pictures didn't all come: Jellyfin fetches any missing again)"
  [ -f "$APP/tunnel.sh.moved" ] && mv "$APP/tunnel.sh.moved" "$APP/tunnel.sh"
  [ -f "$APP/grifflix-gateway.py.moved" ] && mv "$APP/grifflix-gateway.py.moved" "$APP/grifflix-gateway.py"
  rm -f "$WORK"/done-jf-*(N) "$WORK/done-plugins" "$WORK/done-running"   # (a fresh copy of the 10.8 database: brought up to date from the start)
  if [ ! -d "$GD/Grifflix" ]; then
    old 'diskutil eject /Volumes/G-Drive >/dev/null 2>&1 || diskutil unmount force /Volumes/G-Drive >/dev/null 2>&1' && EJ=1 || EJ=""
    note "Switching the old Mac mini off…"
    ( oldsudo 'shutdown -h now' >/dev/null 2>&1 & )
    sleep 5; for i in {1..40}; do ping -c 1 -t 2 "$OLD_HOST" >/dev/null 2>&1 || break; sleep 2; done
    if ping -c 1 -t 2 "$OLD_HOST" >/dev/null 2>&1; then
      FIRST="First switch the old Mac mini off: hold its power button for five seconds, until its light goes out.$([ -z "$EJ" ] && print ' (If your work Mac shows G-Drive in a Finder window, click the ⏏ next to it before that.)')"
    else
      note "The old Mac mini has switched off."; FIRST="The old Mac mini has switched itself off."
    fi
    speak "Now move the two cables from the old Mac mini to this one."
    autobox "$FIRST Now move two cables from the old Mac mini to this one:

1. The G-Drive’s cable (from the big drive with the films: not its power cable).
2. The network cable (from the powerline adapter: the plug with a little clip).

This message goes away by itself once the G-Drive is in. If macOS asks whether Terminal, jellyfin or Python may use files on a removable volume, click Allow."
    for i in {1..360}; do [ -d "$GD/Grifflix" ] && break; sleep 5; done
    endbox
    [ -d "$GD/Grifflix" ] || die "The G-Drive hasn’t appeared on this Mac mini."
    note "The G-Drive is here."
  fi
  mark switched
fi

# ---- 4. Jellyfin's database brought up to date
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
# (the films readable from here first: the in-between Jellyfins run from Terminal, so read with Terminal's say-so, and one that can't
# see the films drops them from collections: found rehearsing, 28 Nov 2026)
tread() { [ -n "$(ls -A "$GD/Grifflix" 2>/dev/null | head -1)" ]; }
if ! done_ "jf-$JF_NEW"; then
  for i in {1..12}; do tread && break; sleep 5; done
  n=0
  while ! tread; do
    n=$((n + 1))
    (( n > 3 )) && die "This Mac mini can’t read the films on the G-Drive."
    box "This Mac mini can see the G-Drive but not yet the films on it: macOS is waiting for you to click Allow. Look for a message asking whether Terminal may use files on a removable volume (it may be behind this window), click Allow, then click OK here.

If you clicked Don’t Allow by mistake: Apple logo → System Settings → Privacy & Security → Files & Folders, and turn on Removable Volumes under Terminal. Then click OK here."
    for i in {1..6}; do tread && break; sleep 5; done
  done
fi
for v in $JF_STEPS $JF_NEW; do
  done_ "jf-$v" && continue
  [ $v = $JF_NEW ] && BIN="$JFBIN/jellyfin" || BIN="$WORK/jf-$v/jellyfin/jellyfin"
  note "Jellyfin $v: bringing the database up to date…"
  jf_run "$BIN" $v || die "Jellyfin $v couldn’t bring the database up to date."
  mark "jf-$v"; note "Jellyfin $v: done."
done

# ---- Jellyfin, the gateway and the tunnel started with the Mac; both able to read the films; the site reaching them
note "Starting Jellyfin $JF_NEW, the gateway and the tunnel (with the Mac, by themselves from now on)…"
plistto "$AGENTS/$LBL-jellyfin.plist" jellyfin true "$JFBIN/jellyfin" --datadir "$JFD" --configdir "$CFG" --cachedir "$JFC" --logdir "$JFD/log" \
  --webdir "$JFAPP/Contents/Resources/jellyfin-web" --ffmpeg "$JFBIN/ffmpeg"
rm -f "$AGENTS"/$LBL-*.plist.off(N)
rm -f "$APP/cloudflared"; cp "$WORK/cloudflared" "$APP/cloudflared"; chmod +x "$APP/cloudflared"
security find-generic-password -s grifflix-tunnel -a cloudflare >/dev/null 2>&1 || die "The tunnel’s token isn’t here."
cat > "$APP/tunnel.sh" <<SH
#!/bin/zsh
exec "$APP/cloudflared" tunnel --no-autoupdate --protocol http2 run --token "\$(security find-generic-password -s grifflix-tunnel -a cloudflare -w)"
SH
chmod +x "$APP/tunnel.sh"
OUT="$APP/gateway.log" plistto "$AGENTS/$LBL-gateway.plist" gateway true "$PY" "$APP/grifflix-gateway.py" serve
OUT="$APP/tunnel.log" plistto "$AGENTS/$LBL-tunnel.plist" tunnel true "$APP/tunnel.sh"
start() { launchctl bootout gui/$U/$LBL-$1 2>/dev/null; launchctl enable gui/$U/$LBL-$1 2>/dev/null; launchctl bootstrap gui/$U "$AGENTS/$LBL-$1.plist"; }
start jellyfin
for i in {1..120}; do curl -s -m 3 http://127.0.0.1:8096/System/Info/Public | grep -q '"StartupWizardCompleted":true' && break; sleep 3; done
curl -s -m 3 http://127.0.0.1:8096/System/Info/Public | grep -q "\"Version\":\"$JF_NEW" || die "Jellyfin $JF_NEW didn’t start with the moved database."
note "Jellyfin $JF_NEW is running with the moved library. The gateway's own set-up (signs in again; this Mac's video chip for converting)…"
"$PY" "$APP/grifflix-gateway.py" setup || die "The gateway’s set-up didn’t work."
start gateway
for i in {1..40}; do curl -s -m 3 http://127.0.0.1:8097/health | grep -q '"ok": true' && break; sleep 3; done
curl -s -m 3 http://127.0.0.1:8097/health | grep -q '"ok": true' || die "The gateway didn’t start."
JFREAD='import json, os, urllib.request
s = json.load(open(os.path.expanduser("~/Library/Application Support/Grifflix/settings.json")))
H = {"Authorization": "MediaBrowser Token=\"%s\"" % s["jf_token"]}
def get(p, **h): return urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8096" + p, headers=dict(H, **h)), timeout=20)
ok = False
for i in json.load(get("/Items?Recursive=true&IncludeItemTypes=Movie&Limit=5"))["Items"]:
    try: ok = get("/Videos/%s/stream?static=true" % i["Id"], Range="bytes=0-0").status in (200, 206)
    except Exception: pass
    if ok: break
print("ok" if ok else "no")'
readable() {   # jellyfin (the first byte of a film) and the gateway (its own look at the films' folders) can both read the G-Drive
  local h; h=$(curl -s -m 5 http://127.0.0.1:8097/health)
  case $h in *'"films": true'*) ;; *'"films"'*) return 1 ;; esac   # (a gateway from before it said: Jellyfin's word taken)
  [ "$("$PY" -c "$JFREAD" 2>/dev/null)" = ok ]
}
for i in {1..30}; do readable && break; sleep 4; done
n=0
while ! readable; do
  n=$((n + 1))
  (( n > 3 )) && fail "Jellyfin or Python still can’t use the G-Drive. Click the Apple logo, System Settings, Privacy & Security, Files & Folders, and turn on Removable Volumes under jellyfin and under Python. Then in Terminal type  zsh m  and press Return."
  (( n == 1 )) && speak "Grifflix needs you to click Allow."
  box "Grifflix can’t see the films yet: macOS is waiting for you to click Allow. Look for a message asking whether jellyfin or Python may use files on a removable volume (it may be behind this window), click Allow, then click OK here.

If you clicked Don’t Allow by mistake: Apple logo → System Settings → Privacy & Security → Files & Folders, and turn on Removable Volumes under jellyfin and Python. Then click OK here."
  for i in {1..15}; do readable && break; sleep 4; done
done
note "Jellyfin and the gateway can both read the films."
start tunnel
note "Checking the site reaches this Mac through the tunnel…"
for i in {1..40}; do curl -s -m 8 "$SITE/health" | grep -q '"ok": true' && break; sleep 4; done
curl -s -m 8 "$SITE/health" | grep -q '"ok": true' || die "The site can’t reach this Mac through the tunnel."
mark running
note "Grifflix answers at $SITE from this Mac mini."

# ---- 5. this Mac given the old one's address on its network cable, and the G-Drive shared (the work Mac's grifflix-add, convert-here and
# the iPhone's route all use 192.168.68.125). Set here, not in the Deco app: the old Mac mini's reservation keeps the address from anyone
# else, and it's off. Undone again by itself if the network doesn't work with it
setaddr() {
  local dev svc mask router dns i
  dev=$(networksetup -listallhardwareports 2>/dev/null | awk '/^Hardware Port: Ethernet$/ {getline; print $2; exit}')
  [ -n "$dev" ] || return 1
  [ "$(ipconfig getifaddr "$dev" 2>/dev/null)" = "$ADDR" ] && return 0
  svc=$(networksetup -listnetworkserviceorder 2>/dev/null | awk -v d="(Hardware Port: Ethernet, Device: $dev)" '/^\([0-9*]+\) / {n = $0; sub(/^\([0-9*]+\) /, "", n)} index($0, d) {print n; exit}')
  [ -n "$svc" ] || return 1
  for i in {1..12}; do ipconfig getifaddr "$dev" >/dev/null 2>&1 && break; sleep 5; done
  if ! ipconfig getifaddr "$dev" >/dev/null 2>&1; then
    speak "One last thing: the network cable."
    autobox "One last thing: plug the network cable from the powerline adapter (the plug with a little clip) into this Mac mini, in the socket on its back. This message goes away by itself."
    for i in {1..360}; do ipconfig getifaddr "$dev" >/dev/null 2>&1 && break; sleep 5; done
    endbox
    ipconfig getifaddr "$dev" >/dev/null 2>&1 || return 1
  fi
  mask=$(ipconfig getoption "$dev" subnet_mask 2>/dev/null); router=$(ipconfig getoption "$dev" router 2>/dev/null); dns=$(ipconfig getoption "$dev" domain_name_server 2>/dev/null)
  [ "$("$PY" -c 'import ipaddress, sys; print(ipaddress.ip_address(sys.argv[1]) in ipaddress.ip_network(sys.argv[2] + "/" + sys.argv[3], strict=False))' "$ADDR" "$router" "$mask" 2>/dev/null)" = True ] || return 1
  ping -c 1 -t 2 "$ADDR" >/dev/null 2>&1 && return 1   # (something else has it: the old Mac mini still on?)
  sudo networksetup -setmanual "$svc" "$ADDR" "$mask" "$router" && sudo networksetup -setdnsservers "$svc" ${dns:-$router}
  for i in {1..24}; do [ "$(ipconfig getifaddr "$dev" 2>/dev/null)" = "$ADDR" ] && curl -s -m 8 "$SITE/health" | grep -q '"ok": true' && return 0; sleep 5; done
  sudo networksetup -setdhcp "$svc"; sudo networksetup -setdnsservers "$svc" Empty   # (back as it was)
  return 1
}
if setaddr; then note "This Mac mini is at $ADDR, as the old one was."
else MISS+=(address); note "(the address wasn't changed: this Mac mini isn't at $ADDR yet)"; fi
sharing -l 2>/dev/null | grep -q "$GD" || sudo sharing -a "$GD" -S G-Drive >/dev/null 2>&1
sharing -l 2>/dev/null | grep -q "$GD" && listening 445 && note "G-Drive shared on the network." || MISS+=(share)

# (the set-up's own things taken away: its start-again item, the passwords it kept, its helper)
launchctl bootout gui/$U/$LBL-setup 2>/dev/null; rm -f "$AGENTS/$LBL-setup.plist" "$WORK/askpass"
security delete-generic-password -s $PWS -a this >/dev/null 2>&1; security delete-generic-password -s $PWS -a old >/dev/null 2>&1

N=$("$PY" -c 'import json, os, urllib.request
s = json.load(open(os.path.expanduser("~/Library/Application Support/Grifflix/settings.json")))
r = urllib.request.Request("http://127.0.0.1:8096/Items?Recursive=true&IncludeItemTypes=Movie&Limit=0", headers={"Authorization": "MediaBrowser Token=\"%s\"" % s["jf_token"]})
print(json.load(urllib.request.urlopen(r, timeout=30)).get("TotalRecordCount", "?"))' 2>/dev/null)
note "All done: Jellyfin $JF_NEW with $N films; the gateway and tunnel running here."
LEFT=""
(( ${MISS[(I)address]} )) && LEFT+="

One thing it couldn’t do by itself: give this Mac mini the old one’s address. On your phone, in the Deco app: More → Advanced → Address Reservation; swipe the old Mac mini’s 192.168.68.125 to the left and tap Delete; tap +, choose this Mac mini (grifflix), type 192.168.68.125 and tap Save; then restart this Mac mini (Apple logo → Restart)."
(( ${MISS[(I)share]} )) && LEFT+="

One thing it couldn’t do by itself: share the G-Drive on the network. Apple logo → System Settings → General → Sharing → File Sharing on, then ⓘ next to it, + under Shared Folders, and choose G-Drive."
speak "Grifflix is running on the new Mac mini."
box "Grifflix is running on this Mac mini, with $N films.

You can now unplug the TV (or monitor), the keyboard and the mouse from it. If you like, play a film on your phone. The old Mac mini stays switched off: keep it as it is for a few weeks, as the backup.$LEFT"
rm -f "$WORK/pid"
