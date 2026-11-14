#!/bin/bash
# Makes a big film smaller on a newer Mac (its own video hardware, far quicker than the Mac mini), reading it from the Mac mini's
# drive and writing the small copy straight back beside it: nothing on this Mac's Desktop (John, 14 Nov 2026). The original is
# replaced only once the new one is whole (the same length) and clearly smaller; it keeps the same name, so its place in
# collections, lists and where people stopped all stay.
#
#   shrink "/Volumes/G-Drive/Grifflix/Movies/Obsession (2025)/Obsession (2025).mp4"
#   (Finder: ⌘K, smb://192.168.68.125/G-Drive first. Typing `shrink ` then dragging the film from Finder onto Terminal fills in its path.)
#
# 1080p H.264 at 6 Mbps (as the Mac mini's own converter aims for), every soundtrack and subtitle kept. MP4, M4V and MOV films only:
# others (MKV, AVI) change their name to .mp4, which the Mac mini's converter handles: use "Make smaller next" for those.
# Afterwards, on the Mac mini:  python3 ~/Grifflix/reread.py "<film name>" all
set -u
FF="${FFMPEG:-$HOME/bin/ffmpeg}"
src="${1:-}"
[ -x "$FF" ] || { echo "No ffmpeg at $FF (osxexperts.net: ffmpeg for Apple Silicon, unzipped into ~/bin)."; exit 1; }
[ -f "$src" ] || { echo 'Which film? e.g.  shrink "/Volumes/G-Drive/Grifflix/Movies/Obsession (2025)/Obsession (2025).mp4"'; exit 1; }
case "${src##*.}" in
  mp4|MP4|m4v|M4V|mov|MOV) ;;
  *) echo "That's a .${src##*.}: its name would change to .mp4. Use \"Make smaller next\" on the film in Grifflix instead."; exit 1 ;;
esac
dir=$(dirname "$src"); base=$(basename "$src"); out="$src.shrinking"   # (not ending .mp4, so the media server never takes it for a film)
trap 'rm -f "$out"; echo; echo "Stopped: the original is untouched."; exit 1' INT TERM
secs() { "$FF" -hide_banner -i "$1" 2>&1 | sed -n 's/.*Duration: \([0-9]*\):\([0-9]*\):\([0-9.]*\).*/\1 \2 \3/p' | head -1 | awk '{printf "%d", $1*3600+$2*60+$3}'; }
gb() { stat -f %z "$1" | awk '{printf "%.1f", $1/1e9}'; }

run() {   # the conversion; $1: how the sound and subtitles go (shown as it goes: speed= is how many times faster than the film plays)
  "$FF" -hide_banner -nostdin -v error -stats -y -i "$src" -map 0:v:0 -map 0:a \
    -vf "scale=w='min(1920,iw)':h=-2" -c:v h264_videotoolbox -b:v 6M -maxrate 9M -bufsize 12M -profile:v high -pix_fmt yuv420p \
    $1 -map_metadata 0 -map_chapters 0 -movflags +faststart -f mp4 "$out"
}
echo "Making $base smaller ($(gb "$src") GB). Ctrl-C stops it; nothing is changed until it's finished."
# (subtitles and sound as they are; then without the subtitles if they won't go into an MP4; then the sound as AAC if it won't either)
run "-map 0:s? -c:a copy -c:s mov_text" || { echo "Again, without the subtitles…"; run "-c:a copy"; } || { echo "Again, with the sound as AAC…"; run "-c:a aac -b:a 256k"; }
[ -s "$out" ] || { echo "It didn't convert: the original is untouched."; rm -f "$out"; exit 1; }

a=$(secs "$src"); b=$(secs "$out")
if [ $(( a > b ? a - b : b - a )) -gt 3 ]; then
  echo "The new file is ${b}s long, the original ${a}s: kept the original, removed the new one."; rm -f "$out"; exit 1
fi
if [ "$(stat -f %z "$out")" -gt $(( $(stat -f %z "$src") * 3 / 4 )) ]; then
  echo "The new file ($(gb "$out") GB) isn't much smaller than the original ($(gb "$src") GB): kept the original."; rm -f "$out"; exit 1
fi
was=$(gb "$src"); mv -f "$out" "$src" && echo "Done: $was GB → $(gb "$src") GB, in its place.
Now on the Mac mini:  python3 ~/Grifflix/reread.py \"${base%.*}\" all"
