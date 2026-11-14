#!/bin/zsh
# Puts the films in this Mac's Movies folder into Grifflix, each in the folder the media server wants on the Mac mini's drive:
#   ~/Movies/The.End.Of.The.Tour.2015.1080p.BluRay.x264.YIFY.mp4
#     → /Volumes/G-Drive/Grifflix/Movies/The End Of The Tour (2015)/The End Of The Tour (2015).mp4
# (John, 14 Nov 2026). Subtitles beside a film (.srt) go with it. Moved, not copied: nothing stays on this Mac (John: "I want it
# moved not copied, as don't want anything kept there permanently"). The copy on the Mac mini is checked (its size) before the
# original is deleted. The media server sees new films on its own within a few minutes.
#
#   grifflix-add            put the films in now (and say what it did)
#   grifflix-add --check    only say what it would do
#   grifflix-add --install  do it by itself every 10 minutes, and whenever something new lands in Movies (while at home)
#   grifflix-add --remove   stop doing it by itself
#
# Left alone (and said why): a film with no year in its name (the media server would guess, often wrong: rename it as
# "Title (Year)"); TV episodes (S01E02); samples; a file still being copied in; a film already in Grifflix.
setopt null_glob extended_glob
SRC="${GRIFFLIX_FROM:-$HOME/Movies}"
DRIVE="/Volumes/G-Drive"
DEST="${GRIFFLIX_TO:-$DRIVE/Grifflix/Movies}"
SHARE="smb://johngriffiths@192.168.68.125/G-Drive"
LABEL="io.github.cooperking.grifflix-add"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/grifflix-add.log"
CHECK=0

case "${1:-}" in
  --install)
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
    cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/zsh</string><string>${0:A}</string><string>--quiet</string></array>
  <key>WatchPaths</key><array><string>$SRC</string></array>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
PLIST
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
    launchctl bootstrap "gui/$(id -u)" "$AGENT" && echo "On: films in $SRC go into Grifflix by themselves (every 10 minutes, and when one lands).
What it did: $LOG   ·   To stop: grifflix-add --remove"
    exit ;;
  --remove) launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null; rm -f "$AGENT"; echo "Off."; exit ;;
  --check) CHECK=1 ;;
esac
QUIET=0; [[ "${1:-}" == --quiet ]] && QUIET=1
say() { print -r -- "$(date '+%Y-%m-%d %H:%M') $*"; }

# the Mac mini's drive: connected if it isn't (with the password kept in the keychain the first time it was connected in Finder)
if [[ ! -d "$DEST" ]]; then
  osascript -e "mount volume \"$SHARE\"" >/dev/null 2>&1
  for i in {1..10}; do [[ -d "$DEST" ]] && break; sleep 1; done
  if [[ ! -d "$DEST" ]]; then
    (( QUIET )) || say "The Mac mini's drive isn't connected (not at home?). In Finder: ⌘K, $SHARE, then run this again."
    exit 0
  fi
fi

# "Title (Year)" from a file or folder name: dots and underscores to spaces, the year found, everything after it dropped
name_of() {
  local n="${1:r}" y t
  n="${n//[._]/ }"
  if [[ "$n" =~ '^(.+[^ ]) *[[(]?((19|20)[0-9][0-9])[])]?( |$)' ]]; then
    t="${match[1]}"; y="${match[2]}"
    (( y > $(date +%Y) + 1 )) && return   # (not a year: "Blade Runner 2049" with no year after it)
    t="${t%% ##}"; t="${t## ##}"; t="${t%[ \(\[-]}"; t="${t%% ##}"
    print -r -- "$t ($y)"
  fi
}

typeset -i done=0
for f in "$SRC"/*.(mp4|mkv|m4v|mov|avi)(N.) "$SRC"/*/*.(mp4|mkv|m4v|mov|avi)(N.); do
  base="${f:t}"
  [[ "$base" == (#i)*sample* ]] && continue
  [[ "$f" == "$SRC"/(TV|iMovie*|Photo Booth*|Final Cut*)/* ]] && continue
  if [[ "$base" =~ '[Ss][0-9]{1,2}[Ee][0-9]{1,2}' ]]; then say "Left: $base (a TV episode: films only)"; continue; fi
  # (still being copied in: changed in the last 2 minutes)
  if (( $(date +%s) - $(stat -f %m "$f") < 120 )); then (( QUIET )) || say "Waiting: $base (still being copied in)"; continue; fi
  title="$(name_of "$base")"
  [[ -z "$title" && "${f:h}" != "$SRC" ]] && title="$(name_of "${f:h:t}")"   # (the year may be in its folder's name)
  if [[ -z "$title" ]]; then say "Left: $base (no year in its name: rename it \"Title (Year)\")"; continue; fi
  ext="${f:e:l}"; dir="$DEST/$title"; to="$dir/$title.$ext"
  if [[ -d "$dir" ]]; then say "Left: $base (\"$title\" is already in Grifflix)"; continue; fi
  if (( CHECK )); then say "Would add: $base → $title/$title.$ext"; continue; fi
  say "Adding: $base → $title/ ($(printf %.1f $(( $(stat -f %z "$f") / 1000000000.0 ))) GB)"
  mkdir -p "$dir" && cp "$f" "$to.adding" 2>/dev/null
  if [[ $(stat -f %z "$f") != $(stat -f %z "$to.adding" 2>/dev/null) ]]; then
    say "  the copy failed: nothing changed"; rm -f "$to.adding"; rmdir "$dir" 2>/dev/null; continue
  fi
  mv "$to.adding" "$to"
  # (subtitles beside it, with the film's new name: "Film.en.srt" → "Title (Year).en.srt")
  for s in "${f:h}"/*.srt(N); do
    sb="${s:t:r}"; lang=""; [[ "$sb" == *.[a-z][a-z]([a-z])# ]] && lang=".${sb:e}"
    [[ "${f:h}" == "$SRC" && "$sb" != "${base:r}"* ]] && continue   # (loose in Movies: only this film's own)
    cp "$s" "$dir/$title$lang.srt"
    [[ "${f:h}" == "$SRC" ]] && rm -f "$s"   # (loose in Movies: gone with its film)
  done
  # the original deleted, now it's safely on the Mac mini (and its folder too if it was a download's folder)
  rm -f "$f"
  if [[ "${f:h}" != "$SRC" ]]; then   # (a download's folder: gone too once no other film is left in it; samples and extras with it)
    left=( "${f:h}"/*.(mp4|mkv|m4v|mov|avi)(N.) ); left=( ${left:#(#i)*sample*} )
    (( ${#left} )) || rm -rf "${f:h}"
  fi
  say "  done: moved to the Mac mini"
  done+=1
done
(( done )) && say "$done film(s) added: the media server sees them within a few minutes."
(( QUIET || done )) || say "Nothing new to add."
exit 0
