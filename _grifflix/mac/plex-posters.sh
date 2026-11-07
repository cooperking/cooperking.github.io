#!/bin/bash
# Copies the posters and backgrounds Plex chose (or John uploaded to Plex) into the films' and series' own folders,
# where Jellyfin reads them as local pictures, so it needn't fetch its own (and the pictures stay the ones he knows).
# Never replaces a picture already in a folder: John's own posters (poster.jpg and the like) always win.
#   film alone in its folder:     poster.jpg, fanart.jpg
#   several films in one folder:  <video name>-poster.jpg, <video name>-fanart.jpg   (so each film gets its own)
#   series:                        poster.jpg, fanart.jpg in its folder; a season's in its Season folder (poster.jpg),
#                                  or seasonNN-poster.jpg in the series folder when its episodes aren't in one
# Reads a copy of Plex's database; changes nothing of Plex's. Checked on 7 Nov 2026 against a Plex-shaped test
# database, and Jellyfin 10.8.13 reading the names above.
#   bash plex-posters.sh        (counts what it would copy)
#   bash plex-posters.sh go     (copies)
GO="$1"
BASE=${GRIFFLIX_MEDIA:-/Volumes/G-Drive/Grifflix}; [ -d "$BASE" ] || BASE=/Volumes/G-Drive/plex
P=${PLEX_DIR:-"$HOME/Library/Application Support/Plex Media Server"}
DB="$P/Plug-in Support/Databases/com.plexapp.plugins.library.db"
[ -d "$BASE/Movies" ] || { echo "No $BASE/Movies: is the G-Drive plugged in?"; exit 1; }
[ -f "$DB" ] || { echo "No Plex database at $DB"; exit 1; }
TMP=$(mktemp -d /tmp/grifflix-posters.XXXX)
cp "$DB" "$TMP/plex.db" || exit 1
# every video on the drive, to find films the tidy-up moved (Plex knows them by their old place)
find "$BASE/Movies" "$BASE/TV" -type f ! -name '._*' \( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.m4v' -o -iname '*.avi' -o -iname '*.mov' -o -iname '*.ts' -o -iname '*.webm' -o -iname '*.wmv' -o -iname '*.mpg' \) > "$TMP/videos.txt"
PARTS='[ ._-]*([Pp][Tt]|[Pp][Aa][Rr][Tt]|[Cc][Dd]|[Dd][Ii][Ss][CcKk])[ ._-]*[0-9]+'   # (macOS's sed has no case-insensitive flag)
COPIED=0; KEPT=0; NOPIC=0; LOST=0

where_now() {   # Plex's path for a video → where it is now (or nothing)
  local f="$1"
  case "$f" in /Volumes/G-Drive/plex/*) f="$BASE/${f#/Volumes/G-Drive/plex/}";; /Volumes/G-Drive/Grifflix/*) f="$BASE/${f#/Volumes/G-Drive/Grifflix/}";; esac
  [ -f "$f" ] && { echo "$f"; return; }
  awk -v n="/$(basename "$1")" 'substr($0, length($0) - length(n) + 1) == n { print; exit }' "$TMP/videos.txt"
}
picture() {   # picture BUNDLE KIND URL [SEASON] → the file Plex keeps for it
  local b="$1" kind="$2" url="$3" id="${3##*/}" c
  case "$url" in
    http*://*) curl -fsSL -m 30 -o "$TMP/dl" "$url" 2>/dev/null && [ -s "$TMP/dl" ] && echo "$TMP/dl"; return;;
    metadata://*|upload://*) ;;
    *) return;;
  esac
  for c in "$b/Contents/_combined/$kind/$id" "$b/Uploads/$kind/$id" ${4:+"$b/Contents/_combined/seasons/$4/$kind/$id"} ${4:+"$b/Uploads/seasons/$4/$kind/$id"}; do
    [ -s "$c" ] && { echo "$c"; return; }
  done
}
ext_of() { case "$(file -b --mime-type "$1" 2>/dev/null)" in image/png) echo png;; image/webp) echo webp;; *) echo jpg;; esac; }
has_own() {   # has_own DIR NAME... : a picture by any of these names (any picture type) is there already
  local d="$1"; shift; local n e
  for n in "$@"; do for e in jpg jpeg png webp tbn JPG JPEG PNG; do [ -e "$d/$n.$e" ] && return 0; done; done; return 1
}
put() {   # put SRC DIR NAME (NAME without extension)
  local src="$1" d="$2" n="$3"
  [ -z "$src" ] && { NOPIC=$((NOPIC+1)); return; }
  local e; e=$(ext_of "$src")
  COPIED=$((COPIED+1))
  [ "$GO" = go ] && { cp "$src" "$d/$n.$e" || echo "  !! couldn't write $d/$n.$e"; }
  [ "$GO" = go ] || [ $COPIED -le 12 ] && echo "  $d/$n.$e"
}
mains_in() {   # how many films are directly in a folder (parts of one film counted once)
  ls "$1" 2>/dev/null | grep -v '^\._' | grep -iE '\.(mkv|mp4|m4v|avi|mov|ts|webm|wmv|mpg)$' | sed -E "s/$PARTS//" | sed 's/\.[^.]*$//' | sort -u | wc -l | tr -d ' '
}

echo "== Films"
sqlite3 -separator $'\x1f' "$TMP/plex.db" "select mi.hash, ifnull(mi.user_thumb_url,''), ifnull(mi.user_art_url,''), min(mp.file) from metadata_items mi join media_items m on m.metadata_item_id=mi.id join media_parts mp on mp.media_item_id=m.id where mi.metadata_type=1 group by mi.id;" > "$TMP/films.tsv"
while IFS=$'\x1f' read -r h thumb art file; do
  now=$(where_now "$file"); [ -z "$now" ] && { LOST=$((LOST+1)); continue; }
  d=$(dirname "$now"); v=$(basename "$now"); v="${v%.*}"; v=$(echo "$v" | sed -E "s/$PARTS\$//")
  b="$P/Metadata/Movies/${h:0:1}/${h:1}.bundle"
  if [ "$(mains_in "$d")" -gt 1 ]; then pn="$v-poster"; fn="$v-fanart"; own_p=("$v-poster" "$v"); own_f=("$v-fanart" "$v-backdrop")
  else pn=poster; fn=fanart; own_p=(poster folder cover default movie "$v-poster" "$v"); own_f=(fanart backdrop background "$v-fanart"); fi
  if has_own "$d" "${own_p[@]}"; then KEPT=$((KEPT+1)); else put "$(picture "$b" posters "$thumb")" "$d" "$pn"; fi
  has_own "$d" "${own_f[@]}" || put "$(picture "$b" art "$art")" "$d" "$fn"
done < "$TMP/films.tsv"

echo "== Series and seasons"
sqlite3 -separator $'\x1f' "$TMP/plex.db" "select s.hash, ifnull(s.user_thumb_url,''), ifnull(s.user_art_url,''), (select mp.file from metadata_items se join metadata_items ep on ep.parent_id=se.id join media_items m on m.metadata_item_id=ep.id join media_parts mp on mp.media_item_id=m.id where se.parent_id=s.id limit 1) from metadata_items s where s.metadata_type=2;" > "$TMP/shows.tsv"
while IFS=$'\x1f' read -r h thumb art file; do
  [ -z "$file" ] && continue
  now=$(where_now "$file"); [ -z "$now" ] && { LOST=$((LOST+1)); continue; }
  rel="${now#$BASE/TV/}"; d="$BASE/TV/${rel%%/*}"
  b="$P/Metadata/TV Shows/${h:0:1}/${h:1}.bundle"
  if has_own "$d" poster folder cover; then KEPT=$((KEPT+1)); else put "$(picture "$b" posters "$thumb")" "$d" poster; fi
  has_own "$d" fanart backdrop background || put "$(picture "$b" art "$art")" "$d" fanart
done < "$TMP/shows.tsv"
sqlite3 -separator $'\x1f' "$TMP/plex.db" "select s.hash, se.hash, se.\"index\", ifnull(se.user_thumb_url,''), (select mp.file from metadata_items ep join media_items m on m.metadata_item_id=ep.id join media_parts mp on mp.media_item_id=m.id where ep.parent_id=se.id limit 1) from metadata_items se join metadata_items s on s.id=se.parent_id where se.metadata_type=3 and se.user_thumb_url like '%://%';" > "$TMP/seasons.tsv"
while IFS=$'\x1f' read -r sh h n thumb file; do
  [ -z "$file" ] && continue
  now=$(where_now "$file"); [ -z "$now" ] && { LOST=$((LOST+1)); continue; }
  rel="${now#$BASE/TV/}"; show="$BASE/TV/${rel%%/*}"; d=$(dirname "$now")
  src=$(picture "$P/Metadata/TV Shows/${sh:0:1}/${sh:1}.bundle" posters "$thumb" "$n"); [ -z "$src" ] && src=$(picture "$P/Metadata/TV Shows/${h:0:1}/${h:1}.bundle" posters "$thumb")
  if [ "$d" != "$show" ]; then has_own "$d" poster folder cover && KEPT=$((KEPT+1)) || put "$src" "$d" poster
  else nn=$(printf 'season%02d-poster' "$n"); has_own "$show" "$nn" && KEPT=$((KEPT+1)) || put "$src" "$show" "$nn"; fi
done < "$TMP/seasons.tsv"

echo
[ "$GO" = go ] || [ $COPIED -le 12 ] || echo "  … and $((COPIED-12)) more"
echo "== $COPIED pictures $( [ "$GO" = go ] && echo copied || echo to copy) from Plex; $KEPT already had their own poster (kept); $NOPIC with no picture in Plex; $LOST no longer found on the drive."
[ "$GO" = go ] || echo "Run  bash plex-posters.sh go  to copy them."
rm -rf "$TMP"
