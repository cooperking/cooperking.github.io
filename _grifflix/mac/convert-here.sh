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
# (a copy to the G-Drive started on its own, in a session of its own: started from the helper's minute job, launchd stopped it with
# that job, so from 13:26 on 18 Nov 2026 no film made on John's Mac reached the G-Drive ("Have the films converted on my Mac not come on yet?"))
detach() { /usr/bin/perl -MPOSIX -e 'POSIX::setsid(); open(STDIN, "<", "/dev/null"); exec @ARGV or die "exec: $!"' -- "$@" >>"$HERE/agent.log" 2>&1 & disown; }
# (how this Mac's side is going, sent to the Mac mini for the admin page and Claude: its log's end, the films waiting in out/, any copy
# running: John, 18 Nov 2026, "I am trying to do it without the Mac so I can't go to the Mac")
report() {
  local b k
  b=$(sed -n 's/^base=//p' "$HERE/agent.conf" 2>/dev/null); k=$(sed -n 's/^key=//p' "$HERE/agent.conf" 2>/dev/null)
  [[ -n "$b" && -n "$k" ]] || return 0
  { echo "== $(date '+%Y-%m-%d %H:%M:%S') $(scutil --get ComputerName 2>/dev/null)"; echo "== G-Drive: $( [[ -d /Volumes/G-Drive ]] && echo mounted || echo NOT mounted)"; df -h "$HERE" 2>/dev/null | tail -1
    echo "== G-Drive connection"; mount | grep -i 'g-drive' | sed -E 's#//[^@]*@#//(user)@#'; ls -ldeO "/Volumes/G-Drive/Grifflix/Grifflix Converting" 2>&1 | head -5
    t_="/Volumes/G-Drive/Grifflix/Grifflix Converting/.grifflix-write-test-$$"; if ( : > "$t_" ) 2>/dev/null; then echo "write test: OK"; rm -f "$t_"; else echo "write test: REFUSED ($( ( : > "$t_" ) 2>&1 ))"; fi
    t_="/Volumes/G-Drive/Grifflix/.grifflix-write-test-$$"; if ( : > "$t_" ) 2>/dev/null; then echo "write test in Grifflix/: OK"; rm -f "$t_"; else echo "write test in Grifflix/: REFUSED"; fi
    echo "== out/"; ls -la "$HERE/out" "$HERE/out/pending" 2>&1 | tail -40
    echo "== pending records"; for r in "$HERE/out/pending/"*.job(N); do echo "-- ${r:t}"; cat "$r"; done
    echo "== running"; ps -axo pid,etime,command | grep -E "[c]opyback|[c]p /Users|[f]fmpeg" | cut -c1-220
    echo "== agent.log"; grep -av 'frame=\|^size=\|speed=' "$HERE/agent.log" 2>/dev/null | tail -40 | cut -c1-300; } | curl -fsS -m 20 -X POST -H "Authorization: Bearer $k" -H 'Content-Type: text/plain; charset=utf-8' --data-binary @- "$b/macagent/report" >/dev/null 2>&1
}
# (a film sent to the Mac mini, which writes it to the G-Drive itself: macOS won't let this helper write to a network drive. Over the home
# network first, else out through Grifflix's own address, in pieces, carrying on from where it got to: John, 19 Nov 2026)
upload() {
  local lt="$1" url="$2" b k t total have host mb r skip
  b=${url%/convjob/*}; t=${url:t}; k=$(sed -n 's/^key=//p' "$HERE/agent.conf" 2>/dev/null)
  total=$(stat -f %z "$lt" 2>/dev/null) || return 1
  for host in "http://$MINI:8098" "$b"; do
    have=$(curl -fsS -m 20 --connect-timeout 5 -H "Authorization: Bearer $k" "$host/macagent/upload?job=$t&total=$total" 2>/dev/null | sed -n 's/.*"size": *\([0-9]*\).*/\1/p')
    [[ -n "$have" ]] || continue
    [[ "$host" == http://* ]] && mb=200 || mb=48
    while (( have < total )); do
      skip=$(( have / 1048576 ))
      r=$(dd if="$lt" bs=1048576 skip=$skip count=$mb 2>/dev/null | curl -fsS -m 1800 -X PUT -H "Authorization: Bearer $k" -H 'Content-Type: application/octet-stream' --data-binary @- "$host/macagent/upload?job=$t&off=$(( skip * 1048576 ))&total=$total" 2>/dev/null) || break
      have=$(printf '%s' "$r" | sed -n 's/.*"size": *\([0-9]*\).*/\1/p'); [[ -n "$have" ]] || break
    done
    (( have >= total )) && { echo "$(date '+%Y-%m-%d %H:%M') ${lt:t}: sent to the Mac mini (${host%%:*})"; return 0; }
  done
  return 1
}

if [[ "${1:-}" == "setup" ]]; then
  mkdir -p "$HOME/Applications" "$HERE"
  cp -f "$0" "$HERE/convert-here.sh" 2>/dev/null
  rm -rf "$APP"
  # (the job written to a small file and that file opened in Terminal: opening a file needs no permission, where working Terminal itself
  # (do script) needs macOS's "control Terminal" permission, and without it the button opened nothing: John, 17 Nov 2026)
  AS=$(mktemp -t grifflix).applescript
  cat > "$AS" <<'APPLESCRIPT'
on open location u
	set f to (POSIX path of (path to temporary items)) & "grifflix-convert.command"
	set cmd to "mkdir -p ~/Grifflix && curl -fsSL https://raw.githubusercontent.com/cooperking/cooperking.github.io/main/_grifflix/mac/convert-here.sh -o ~/Grifflix/convert-here.sh.new && mv ~/Grifflix/convert-here.sh.new ~/Grifflix/convert-here.sh; zsh ~/Grifflix/convert-here.sh " & quoted form of u
	do shell script "printf '%s\\n' '#!/bin/zsh' " & quoted form of cmd & " > " & quoted form of f & " && chmod +x " & quoted form of f & " && open -a Terminal " & quoted form of f
end open location
APPLESCRIPT
  osacompile -o "$APP" "$AS" || { echo "Couldn't make the app."; exit 1; }
  rm -f "$AS"
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

# Films queued from anywhere, taken up by this Mac by itself (John, 18 Nov 2026: "Is it possible to switch on convert on my Mac remotely?"):
# a small helper, set up once, asks the Mac mini every minute for the next film queued for this Mac, while it's at home (the G-Drive is
# on the home network), and converts it in the background, as the button's Terminal window would; its progress is on the admin page.
AGENT_PLIST="$HOME/Library/LaunchAgents/uk.co.grifflix.convertagent.plist"
if [[ "${1:-}" == "agent-setup" ]]; then
  base="${2:-}"; key="${3:-}"
  [[ "$base" == https://* && -n "$key" ]] || { echo "Grifflix: the setup command wasn't whole: copy it again from the admin page."; exit 1; }
  mkdir -p "$HERE" "$HOME/Library/LaunchAgents"
  cp -f "$0" "$HERE/convert-here.sh" 2>/dev/null
  umask 077; printf 'base=%s\nkey=%s\n' "$base" "$key" > "$HERE/agent.conf"
  cat > "$AGENT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>uk.co.grifflix.convertagent</string>
  <key>ProgramArguments</key><array><string>/bin/zsh</string><string>$HERE/convert-here.sh</string><string>agent</string></array>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>AbandonProcessGroup</key><true/>
  <key>StandardOutPath</key><string>$HERE/agent.log</string>
  <key>StandardErrorPath</key><string>$HERE/agent.log</string>
</dict></plist>
PLIST
  launchctl unload "$AGENT_PLIST" 2>/dev/null; launchctl load -w "$AGENT_PLIST"
  echo "Set up: this Mac now takes the films you queue for it, while it's on and at home. Its log: ~/Grifflix/agent.log"
  exit 0
fi
if [[ "${1:-}" == "agent-off" ]]; then
  launchctl unload -w "$AGENT_PLIST" 2>/dev/null; rm -f "$AGENT_PLIST" "$HERE/agent.conf"; echo "The helper is off."; exit 0
fi
if [[ "${1:-}" == "subs-setup" ]]; then   # (once: whisper.cpp and its model, for English subtitles made ready here: John, 20 Nov 2026)
  mkdir -p "$HERE/whisper"
  BREW=$(command -v brew || ls /opt/homebrew/bin/brew /usr/local/bin/brew 2>/dev/null | head -1)
  if [[ -z "$BREW" ]]; then
    # (no Homebrew, and no administrator rights needed: faster-whisper, Whisper in Python, in its own folder here; ffmpeg as the converter
    # already has it: John, 21 Nov 2026, "we need a way without homebrew")
    FF=$( [[ -x "$HOME/bin/ffmpeg" ]] && echo "$HOME/bin/ffmpeg" || command -v ffmpeg )
    [[ -n "$FF" ]] || { echo "Grifflix: ffmpeg is needed first: from osxexperts.net (Apple Silicon) unzip ffmpeg into ~/bin, then: xattr -d com.apple.quarantine ~/bin/ffmpeg"; exit 1; }
    echo "Grifflix: putting faster-whisper in ~/Grifflix/fw (no Homebrew, nothing system-wide)…"
    [[ -x "$HERE/fw/bin/python3" ]] || /usr/bin/python3 -m venv "$HERE/fw" || exit 1
    "$HERE/fw/bin/python3" -m pip install -q --upgrade pip >/dev/null 2>&1
    "$HERE/fw/bin/python3" -m pip install -q faster-whisper || exit 1
    # (and Argos Translate, offline, for subtitles in another language, translated and compared before Whisper listens)
    "$HERE/fw/bin/python3" -m pip install -q argostranslate "spacy<3.8; python_version<'3.10'" || echo "Grifflix: Argos Translate did not install now; it tries again when first needed."
    echo "Grifflix: getting Whisper's model (about 500 MB, once)…"
    "$HERE/fw/bin/python3" -c "from faster_whisper import WhisperModel; WhisperModel('${GRIFFLIX_FW_MODEL:-small}', device='cpu', compute_type='int8')" || exit 1
    touch "$HERE/whisper/fw-ready"
    echo "Grifflix: ready. This Mac makes English subtitles ready whenever it has no film to convert."
    exit 0
  fi
  if ! command -v whisper-cli >/dev/null 2>&1 && [[ ! -x /opt/homebrew/bin/whisper-cli && ! -x /usr/local/bin/whisper-cli ]]; then
    echo "Grifflix: installing whisper.cpp (Homebrew)…"; "$BREW" install whisper-cpp || exit 1
  fi
  command -v ffmpeg >/dev/null 2>&1 || [[ -x /opt/homebrew/bin/ffmpeg || -x /usr/local/bin/ffmpeg || -x "$HOME/bin/ffmpeg" ]] || { BREW=$(command -v brew || ls /opt/homebrew/bin/brew /usr/local/bin/brew 2>/dev/null | head -1); "$BREW" install ffmpeg || exit 1; }
  if [[ ! -s "$HERE/whisper/ggml-medium.bin" ]]; then
    echo "Grifflix: getting Whisper's model (1.5 GB, once)…"
    curl -fL --progress-bar https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin -o "$HERE/whisper/ggml-medium.bin.part" && mv "$HERE/whisper/ggml-medium.bin.part" "$HERE/whisper/ggml-medium.bin" || exit 1
  fi
  echo "Grifflix: ready. This Mac makes English subtitles ready whenever it has no film to convert."
  exit 0
fi
if [[ "${1:-}" == "copyback" ]]; then
  # (a converted film to the G-Drive, kept on this Mac until it's there and the Mac mini has been told: tried again by itself, when this
  # Mac is home, if the network went: John, 18 Nov 2026, "what happens if my mac leaves the network while a conversion is running?")
  lt="$2"; tmp="$3"; url="$4"; pend="$HERE/out/pending"; rec="$pend/${lt:t}.job"; mkdir -p "$pend"
  if [[ -f "$rec.lock" ]] && [[ "$(cat "$rec.lock" 2>/dev/null)" != "$$" ]] && kill -0 "$(cat "$rec.lock" 2>/dev/null)" 2>/dev/null; then exit 0; fi
  echo $$ > "$rec.lock"; trap 'rm -f "$rec.lock"' EXIT
  [[ -f "$rec" ]] || printf '%s\n%s\n%s\n' "$lt" "$tmp" "$url" > "$rec"
  if ! grep -qx copied "$rec" && [[ ! -f "$lt" ]]; then rm -f "$rec"; echo "$(date '+%Y-%m-%d %H:%M') ${lt:t}: no longer on this Mac"; exit 1; fi
  if ! grep -qx copied "$rec"; then
    # (one at a time, oldest first, each waiting its turn and saying so: three copying at once over the one network to the one USB 2 drive
    # slowed them all: John, 18 Nov 2026, "Films from my Mac also need to be in the queue to transfer")
    tl="$pend/transfer.lock"; said=""; sm=$(stat -f %m "$HERE/convert-here.sh" 2>/dev/null)
    while true; do
      # (a newer helper while waiting: taken up at once, so a fix reaches copies already in the queue)
      [[ -n "$sm" && "$(stat -f %m "$HERE/convert-here.sh" 2>/dev/null)" != "$sm" ]] && exec /bin/zsh "$HERE/convert-here.sh" copyback "$lt" "$tmp" "$url"
      ahead=0
      for o in "$pend/"*.job(N); do
        [[ "$o" == "$rec" ]] && continue
        grep -qx copied "$o" && continue
        # (only one that's being copied, or waiting with its own copy alive: one nobody's working on doesn't hold the rest up, as Short Cuts
        # did all evening on 18 Nov 2026; it's taken up again by itself)
        [[ -f "$o.lock" ]] && kill -0 "$(cat "$o.lock" 2>/dev/null)" 2>/dev/null || continue
        [[ "$o" -ot "$rec" ]] && ahead=$((ahead + 1))
      done
      busy=0; [[ -f "$tl" ]] && [[ "$(cat "$tl" 2>/dev/null)" != "$$" ]] && kill -0 "$(cat "$tl" 2>/dev/null)" 2>/dev/null && busy=1
      (( ahead == 0 && busy == 0 )) && break
      say="waiting to copy to the G-Drive ($(( ahead > 0 ? ahead : 1 )) ahead of it)"
      [[ "$say" != "$said" ]] && echo "$(date '+%Y-%m-%d %H:%M') ${lt:t}: $say"
      said="$say"
      curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d "{\"stage\":\"$say\",\"copying\":1}" "$url/progress" >/dev/null 2>&1
      sleep 30
    done
    echo $$ > "$tl"; trap 'rm -f "$rec.lock" "$tl"' EXIT
    curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d '{"stage":"copying to the G-Drive","copying":1}' "$url/progress" >/dev/null 2>&1
    # (why a copy didn't work, said to the Mac mini, so the admin page shows it: John, 18 Nov 2026, "It should have by now so something must be wrong")
    why=""
    if [[ ! -f "$lt" ]]; then why="the film isn't on this Mac any more"
    elif [[ ! -d "${tmp:h}" ]]; then why="this Mac can't see the G-Drive's folder ${tmp:h}"
    # (the film's contents only: macOS's cp also copies a file's extended attributes, which the network share refuses: "Operation not permitted",
    # every film from John's Mac refused on 18 Nov 2026)
    else why=$( { /bin/cat "$lt" > "$tmp.copying"; } 2>&1 ) && why=$(mv -f "$tmp.copying" "$tmp" 2>&1) && why=""
      [[ -z "$why" && ! -f "$tmp" ]] && why="the copy didn't reach the G-Drive"
      [[ -z "$why" && "$(stat -f %z "$tmp" 2>/dev/null)" != "$(stat -f %z "$lt" 2>/dev/null)" ]] && { why="the copy on the G-Drive isn't the whole film"; rm -f "$tmp"; }; fi
    [[ -n "$why" ]] && upload "$lt" "$url" && why=""   # (not allowed onto the drive from here: sent to the Mac mini instead)
    if [[ -z "$why" ]]; then
      echo copied >> "$rec"
    else
      rm -f "$tmp.copying" 2>/dev/null
      why=${why//$'\n'/ }; why=${why//\"/\'}; why=${why//$HOME\/Grifflix\/out\//}; why=${why//\/Volumes\/G-Drive\/Grifflix\/Grifflix Converting\//}; why=${why:0:240}
      curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d "{\"stage\":\"couldn't copy to the G-Drive: $why (tried again between films)\",\"copying\":1}" "$url/progress" >/dev/null 2>&1
      echo "$(date '+%Y-%m-%d %H:%M') couldn't copy ${lt:t} to the G-Drive ($why): kept on this Mac, tried again in 2 minutes"
      report
      # (tried again by itself every 2 minutes for 3 hours, not only between films (none start while one converts)), the next in line let go meanwhile
      rm -f "$tl"; n_=$(( ${COPYTRY:-0} + 1 )); (( n_ > 90 )) && exit 1
      sleep 120
      # (the newest helper from GitHub before trying again, so a fix reaches a copy within minutes, not only when a film starts)
      curl -fsSL -m 60 https://raw.githubusercontent.com/cooperking/cooperking.github.io/main/_grifflix/mac/convert-here.sh -o "$HERE/convert-here.sh.new.$$" 2>/dev/null && zsh -n "$HERE/convert-here.sh.new.$$" && mv -f "$HERE/convert-here.sh.new.$$" "$HERE/convert-here.sh"; rm -f "$HERE/convert-here.sh.new.$$"
      COPYTRY=$n_ exec /bin/zsh "$HERE/convert-here.sh" copyback "$lt" "$tmp" "$url"
    fi
  fi
  code=$(curl -sS -m 30 -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{"ok":true}' "$url/done")
  if [[ "$code" == 2* ]]; then rm -f "$lt" "$rec"; echo "$(date '+%Y-%m-%d %H:%M') ${lt:t}: on the G-Drive, the Mac mini told"
  elif [[ "$code" == 404 ]]; then rm -f "$lt" "$rec" "$tmp"; echo "$(date '+%Y-%m-%d %H:%M') ${lt:t}: the Mac mini no longer has that job; it will plan the film again"
  else echo "$(date '+%Y-%m-%d %H:%M') ${lt:t}: on the G-Drive; the Mac mini not told yet (tried again)"; exit 1; fi
  exit 0
fi
if [[ "${1:-}" == "agent" ]]; then
  [[ -f "$HERE/agent.conf" ]] || exit 0
  base=$(sed -n 's/^base=//p' "$HERE/agent.conf"); key=$(sed -n 's/^key=//p' "$HERE/agent.conf")
  lock="$HERE/agent.lock"
  home=0; ping -c 1 -t 2 "$MINI" >/dev/null 2>&1 && home=1   # (at home: the Mac mini answers on the home network)
  report
  if [[ "$home" == 1 ]]; then   # (films made here the Mac mini is still waiting for, left in out/ with no record of where they go: recorded)
    mkdir -p "$HERE/out/pending"
    curl -fsS -m 30 -H "Authorization: Bearer $key" "$base/macagent/copying" 2>/dev/null | while IFS=$'\t' read -r bn tmp jurl; do
      [[ -n "$bn" && -f "$HERE/out/$bn" && ! -f "$HERE/out/pending/$bn.job" ]] && printf '%s\n%s\n%s\n' "$HERE/out/$bn" "$tmp" "$base$jurl" > "$HERE/out/pending/$bn.job" && echo "$(date '+%Y-%m-%d %H:%M') $bn: made here earlier, copied over now"
    done
  fi
  if [[ "$home" == 1 ]] && ls "$HERE/out/pending/"*.job >/dev/null 2>&1; then   # (films converted but not yet on the G-Drive: again now it's home)
    [[ -d /Volumes/G-Drive ]] || { open "smb://$MINI/G-Drive"; for i in {1..60}; do [[ -d /Volumes/G-Drive ]] && break; sleep 1; done; }
    for r in "$HERE/out/pending/"*.job; do
      a=("${(@f)$(cat "$r")}")
      detach /usr/bin/caffeinate -i /bin/zsh "$HERE/convert-here.sh" copyback "${a[1]}" "${a[2]}" "${a[3]}"
    done
  fi
  if [[ -f "$lock" ]] && kill -0 "$(cat "$lock" 2>/dev/null)" 2>/dev/null; then exit 0; fi   # (one film at a time; copies over go on all the same)
  ans=$(curl -fsS -m 90 -H "Authorization: Bearer $key" "$base/macagent/next?home=$home") || exit 0
  [[ "$home" == 1 ]] || exit 0
  job=$(printf '%s' "$ans" | /usr/bin/sed -n 's/.*"job": *"\([^"]*\)".*/\1/p')
  if [[ -z "$job" ]]; then
    # (no film to convert: English subtitles made ready for a foreign-language film, timed here by what's said (subprep.py, with
    # whisper.cpp): John, 20 Nov 2026, "pre-prepare" ... "I prefer to use the whisper option")
    [[ -f "$HERE/whisper/ggml-medium.bin" || -f "$HERE/whisper/fw-ready" ]] || exit 0
    sj="$HERE/subjob.json"
    curl -fsS -m 300 -H "Authorization: Bearer $key" "$base/macagent/subnext?home=$home" -o "$sj" || exit 0
    grep -q '"subjob"' "$sj" || exit 0
    echo $$ > "$lock"; trap 'rm -f "$lock" "$sj" "$sj.1"' EXIT
    [[ -d /Volumes/G-Drive ]] || { open "smb://$MINI/G-Drive"; for i in {1..60}; do [[ -d /Volumes/G-Drive ]] && break; sleep 1; done; }
    curl -fsSL -m 60 https://raw.githubusercontent.com/cooperking/cooperking.github.io/main/_grifflix/mac/subprep.py -o "$HERE/subprep.py.new" && mv "$HERE/subprep.py.new" "$HERE/subprep.py"
    /usr/bin/python3 -c 'import json,sys; json.dump(json.load(open(sys.argv[1]))["subjob"], open(sys.argv[2], "w"))' "$sj" "$sj.1" || exit 0
    # (its key read from agent.conf by subprep.py itself, not shown on the command line where any program can see it; and stopped after three
    # hours, so one that hangs can't hold this Mac up for good: John, 22 Nov 2026)
    PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/bin:$PATH" /usr/bin/caffeinate -i /usr/bin/python3 "$HERE/subprep.py" "$sj.1" "$base" - &
    sp_=$!; ( sleep 10800; kill "$sp_" 2>/dev/null ) & wd_=$!
    wait "$sp_"; kill "$wd_" 2>/dev/null
    echo "$(date '+%Y-%m-%d %H:%M') subtitles made ready ($?)"
    exit 0
  fi
  echo $$ > "$lock"; trap 'rm -f "$lock"' EXIT
  echo "$(date '+%Y-%m-%d %H:%M') taking up $job"
  # (its newest self from GitHub first, as the button's app does)
  curl -fsSL -m 60 https://raw.githubusercontent.com/cooperking/cooperking.github.io/main/_grifflix/mac/convert-here.sh -o "$HERE/convert-here.sh.new" && mv "$HERE/convert-here.sh.new" "$HERE/convert-here.sh"
  /usr/bin/caffeinate -i /bin/zsh "$HERE/convert-here.sh" "$base$job"
  echo "$(date '+%Y-%m-%d %H:%M') finished ($?)"
  exit 0
fi

echo "Grifflix: starting…"
u="${1:-}"; u="${u#grifflix-convert://job?u=}"
url=$(osascript -l JavaScript -e "decodeURIComponent('${u//\'/}')")
[[ "$url" == https://* ]] || { echo "Grifflix: this window wasn't given a job (press Convert on this Mac on the film again)."; exit 1; }
# (whatever stops it here, the Mac mini is told why, and the film's Edit panel says so: John, 17 Nov 2026, "a pop up to open a terminal
# window started ... but nothing then happened on the mac")
fail() { echo "Grifflix: $1"; curl -fsS -m 20 -X POST -H 'Content-Type: application/json' -d "{\"ok\":false,\"error\":\"$2\"}" "$url/done" >/dev/null 2>&1; exit 1; }
FF=$( [[ -x "$HOME/bin/ffmpeg" ]] && echo "$HOME/bin/ffmpeg" || command -v ffmpeg )
[[ -z "$FF" ]] && fail "No ffmpeg on this Mac yet. From osxexperts.net (Apple Silicon: the ffmpeg download) unzip it into ~/bin, then in Terminal: xattr -d com.apple.quarantine ~/bin/ffmpeg. Then press Convert on this Mac again." "no ffmpeg on this Mac (see the admin page, Conversions)"
"$FF" -version >/dev/null 2>&1 || fail "ffmpeg is here ($FF) but won't run: in Terminal, xattr -d com.apple.quarantine $FF, then press Convert on this Mac again." "ffmpeg on this Mac won't run (macOS blocked it)"
job=$(mktemp); trap 'rm -f "$job"' EXIT
curl -fsS "$url" -o "$job" || { echo "Grifflix: the Mac mini didn't give the job (press Convert on this Mac again)."; exit 1; }
field() { osascript -l JavaScript -e "var j = JSON.parse($.NSString.stringWithContentsOfFileEncodingError('$job', 4, null).js); j['$1']"; }
name=$(field name); src=$(field src); tmp=$(field tmp); dur=$(field dur); dur=${dur%.*}
args=("${(@f)$(osascript -l JavaScript -e "JSON.parse($.NSString.stringWithContentsOfFileEncodingError('$job', 4, null).js).args.join('\n')")}")
# (an HDR film: its colours made ordinary by ffmpeg's zscale and tonemap; an ffmpeg without them would make it washed out, so it stops and says
# so: John, 20 Nov 2026)
if [[ "${args[*]}" == *zscale* ]] && ! "$FF" -hide_banner -filters 2>/dev/null | grep -q ' zscale '; then
  fail "This film is HDR, and this Mac's ffmpeg can't turn HDR colours into ordinary ones (it has no zscale). Get the full ffmpeg from osxexperts.net into ~/bin (or: brew install ffmpeg), then queue it again." "HDR: this Mac's ffmpeg has no zscale (get the osxexperts.net ffmpeg, or brew install ffmpeg)"
fi
# (a film being made smaller is made at a quality, not a set rate (John, 25 Nov 2026, "go with your recommendation"): tried on a moment of
# test picture first, and if this Mac's encoder won't take a quality, made at the set rate instead, as before)
if [[ "${args[*]}" == *-q:v* ]]; then
  qv=${args[$(( ${args[(i)-q:v]} + 1 ))]}
  mr=(); (( ${args[(i)-maxrate]} <= ${#args} )) && mr=(-maxrate ${args[$(( ${args[(i)-maxrate]} + 1 ))]} -bufsize ${args[$(( ${args[(i)-bufsize]} + 1 ))]})
  if ! "$FF" -hide_banner -nostdin -v error -f lavfi -i testsrc2=size=1280x720:rate=24 -t 2 -c:v h264_videotoolbox -q:v "$qv" -allow_sw 1 "${mr[@]}" -f null - 2>/dev/null; then
    echo "Grifflix: this Mac's encoder won't make it at a quality: at the set rate instead."
    args=("${(@f)$(osascript -l JavaScript -e "JSON.parse($.NSString.stringWithContentsOfFileEncodingError('$job', 4, null).js).args_rate.join('\n')")}")
  else
    echo "Grifflix: made smaller at quality $qv (as small as its picture allows)."
  fi
fi
echo "Converting $name on this Mac."
# (each step told to the Mac mini, so the film's Edit panel says where it's got to, and a stall shows: John, 17 Nov 2026, "it got stuck and I
# shut the terminal - nothing is happening", "it still says converting")
stage() { echo "Grifflix: $1"; curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d "{\"stage\":\"$1\"}" "$url/progress" >/dev/null 2>&1; }

# the G-Drive: connected if it isn't (a password box may come up the first time)
if [[ ! -d /Volumes/G-Drive ]]; then
  stage "connecting to the G-Drive (a box may ask for the Mac mini's sign-in: it can be behind other windows)"; open "smb://$MINI/G-Drive"
  for i in {1..120}; do [[ -d /Volumes/G-Drive ]] && break; sleep 1; done
  [[ -d /Volumes/G-Drive ]] || { echo "Couldn't reach the G-Drive."; curl -fsS -X POST -H 'Content-Type: application/json' -d '{"ok":false,"error":"no G-Drive"}' "$url/done" >/dev/null; exit 1; }
fi
stage "the G-Drive is connected: starting to convert"
[[ -f "$src" ]] || { echo "The film isn't where the Mac mini said: $src"; curl -fsS -X POST -H 'Content-Type: application/json' -d '{"ok":false,"error":"not found"}' "$url/done" >/dev/null; exit 1; }

# the plan as the Mac mini made it, -stats for how far it's got, and every few seconds the Mac mini told how far (the film's Edit panel
# shows it: John, 16 Nov 2026, "I'd like to see the progress"); Ctrl-C stops it and the Mac mini goes back to it later
prog=$(mktemp)
# (made on this Mac's own disk, then copied to the G-Drive once, by itself, while the next film starts: the last step, rewriting the file so
# it plays at once, ran over the network and held the next film up: John, 18 Nov 2026, "If the ffmpeg work is done, it should be able to
# start the next one while a move is working")
out="$HERE/out"; mkdir -p "$out"; lt="$out/${tmp:t}"; rm -f "$lt"
args[-1]="$lt"
# (at the end, ffmpeg writes the file over again so it plays from the start at once: minutes over the network, with nothing moving on screen;
# said so, here and on the admin page: John, 17 Nov 2026, "this seems to have stalled but I can't tell")
( fin=0; rc=0; while sleep 4; do (( ++rc % 75 == 1 )) && report   # (every 5 minutes while it converts)
   
    t=$(grep '^out_time_us=' "$prog" 2>/dev/null | tail -1 | cut -d= -f2); sp=$(grep '^speed=' "$prog" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' ')
    if (( ! fin )) && { grep -q '^progress=end' "$prog" 2>/dev/null || { [[ "$t" == <-> && "$dur" == <-> ]] && (( dur > 0 && t / 1000000 >= dur - 8 )); }; }; then
      fin=1; echo; echo "Grifflix: the film is converted. Putting the file together on this Mac (a minute or so)."
      curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d '{"stage":"finishing: putting the file together on your Mac (a minute or so)"}' "$url/progress" >/dev/null 2>&1
    fi
    [[ "$t" == <-> ]] && ans=$(curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d "{\"t\":$((t / 1000000)),\"speed\":\"$sp\"}" "$url/progress" 2>/dev/null)
    # (cancelled from the admin page: ffmpeg stopped, its part-made file deleted: John, 22 Nov 2026, "how can I cancel converts on my mac")
    if [[ "$ans" == *'"cancel"'* ]]; then : > "$lt.cancel"; pkill -TERM -P $$ -f ffmpeg 2>/dev/null; fi   # (its own ffmpeg: a film's name has brackets a pattern would misread)
  done ) &
reporter=$!
trap 'kill $reporter 2>/dev/null; rm -f "$prog"; echo; echo "Stopped."; curl -fsS -X POST -H "Content-Type: application/json" -d "{\"ok\":false,\"error\":\"stopped\"}" "$url/done" >/dev/null; exit 1' INT TERM HUP
# (ffmpeg's own words kept as well as shown, so a failure says why on the Mac mini and the admin page, not just "ffmpeg failed": John, 20 Nov
# 2026, "ffmpeg failed on Pat Garrett and Billy the Kid, do we know why?")
errf=$(mktemp)
if "$FF" -nostdin -y -v error -stats -progress "$prog" "${args[@]}" 2> >(tee "$errf" >&2); then
  rm -f "$errf"
  kill $reporter 2>/dev/null
  # (made only so far: the film read over the network, which went a moment, and ffmpeg took that for its end (La Bola Negra, 2 h 30 of 2 h 39,
  # 23 Nov 2026): not copied over, and made again: John, 24 Nov 2026, "we need to make sure that never happens")
  te=$(grep '^out_time_us=' "$prog" 2>/dev/null | tail -1 | cut -d= -f2); rm -f "$prog"
  if [[ "$te" == <-> && "$dur" == <-> ]] && (( dur > 0 && te / 1000000 < dur - (dur / 100 > 8 ? dur / 100 : 8) )); then
    got=$(( te / 1000000 )); rm -f "$lt"; trap - INT TERM HUP
    echo "It stopped at $(( got / 60 )) of $(( dur / 60 )) minutes (the G-Drive's connection went a moment?): not copied, made again."
    curl -fsS -m 20 -X POST -H 'Content-Type: application/json' -d "{\"ok\":false,\"error\":\"stopped at $(( got / 60 )) of $(( dur / 60 )) min\",\"short\":$got}" "$url/done" >/dev/null 2>&1
    exit 0
  fi
  trap - INT TERM HUP
  echo "Made. Copying it to the G-Drive by itself (this window can be closed); the Mac mini then checks it and puts it in place."
  curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d '{"stage":"copying to the G-Drive","copying":1}' "$url/progress" >/dev/null 2>&1
  # (the copy on its own, kept awake, so this Mac takes up the next film meanwhile; the Mac mini told when it's there, or why not)
  detach /usr/bin/caffeinate -i /bin/zsh "$HERE/convert-here.sh" copyback "$lt" "$tmp" "$url"
else
  kill $reporter 2>/dev/null; rm -f "$prog" "$lt"
  if [[ -f "$lt.cancel" ]]; then
    rm -f "$lt.cancel" "$errf"; echo "Grifflix: cancelled from the admin page. Nothing was changed."
    curl -fsS -m 20 -X POST -H 'Content-Type: application/json' -d '{"ok":false,"error":"cancelled"}' "$url/done" >/dev/null 2>&1
  elif [[ ! -f "$src" ]]; then   # (the G-Drive gone part-way: the network, or the lid closed): back in the queue, from the start
    echo "The G-Drive went part-way through: the film goes back in the queue."; curl -fsS -m 20 -X POST -H 'Content-Type: application/json' -d '{"ok":false,"error":"lost the G-Drive","again":1}' "$url/done" >/dev/null 2>&1
  else
    sleep 1   # (ffmpeg's words written out first)
    why=$(tr '\r' '\n' < "$errf" | grep -v '^frame=\|^size=\|^ *$' | tail -3 | tr '\n' ' ' | sed 's/[\\"]//g; s/[[:cntrl:]]//g' | cut -c1-300); rm -f "$errf"
    echo "It didn't convert: ${why:-no reason given}"; curl -fsS -X POST -H 'Content-Type: application/json' -d "{\"ok\":false,\"error\":\"ffmpeg failed${why:+: $why}\"}" "$url/done" >/dev/null
  fi
fi
