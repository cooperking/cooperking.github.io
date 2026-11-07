#!/bin/bash
# Tidies John's G-Drive library for Jellyfin, once, before Grifflix first scans it (moving files later would make
# Jellyfin see them as new, and Grifflix would lose its picks and progress for them).
# Worked out on 7 Nov 2026 from the drive's listing and checked against Jellyfin 10.8.13 with the same names:
# - films that were filed inside another film's folder, or inside its Other (extras) folder, or a download's own folder
# - folders without a year (or with "(1080)" where the year goes), and loose files at the top of Movies and TV
# - TV: seasons kept as separate series, a series filed inside another, and "Other" (which Jellyfin shows as stray
#   episodes in a series; "Extras" it keeps out of the way)
# - exact duplicates (same size to the byte, found in the listing): moved to "Set aside" (beside Movies and TV, where
#   Jellyfin doesn't look), keeping their paths, to be checked and deleted by hand (about 100 GB)
# Only moves and renames; nothing is deleted. Run with no word first to see what it would do; then "go".
#   bash tidy-library.sh        (shows the moves)
#   bash tidy-library.sh go     (does them)
GO="$1"
BASE=${TIDY_BASE:-/Volumes/G-Drive/Grifflix}; [ -d "$BASE" ] || BASE=/Volumes/G-Drive/plex
cd "$BASE" || { echo "No G-Drive/Grifflix (or plex) folder: is the drive plugged in?"; exit 1; }
N=0; SKIP=0
m() {   # m SOURCE TARGET   (a TARGET ending in / is a folder to put SOURCE in, keeping its name)
  local src="$1" dst="$2" target
  case "$dst" in */) target="$dst$(basename "$src")";; *) target="$dst";; esac
  if [ ! -e "$src" ]; then echo "  -  not there (done already?): $src"; SKIP=$((SKIP+1)); return; fi
  if [ -e "$target" ]; then echo "  -  already there, left alone: $target"; SKIP=$((SKIP+1)); return; fi
  echo "  $src"; echo "     → $target"; N=$((N+1))
  if [ "$GO" = go ]; then mkdir -p "$(dirname "$target")" && mv "$src" "$target" || echo "     !! that move failed"; fi
}
[ "$GO" = go ] && echo "== Moving (in $BASE)" || echo "== What would move (in $BASE). Nothing changes until you run: bash tidy-library.sh go"

echo; echo "-- Films filed in another film's folder"
m "Movies/Shoah (1985)/8.Femmes.2002.FRENCH.1080p.Bluray.x264.AC3-HazMatt.mkv" "Movies/8 Women (2002)/"
m "Movies/Shoah (1985)/A.Complete.Unknown.2024.1080p.WEBRip.x264.AAC5.1-[YTS.MX].mp4" "Movies/A Complete Unknown (2024)/"
m "Movies/Shoah (1985)/When.Fall.Is.Coming.2024.1080p.BluRay.x264.AAC5.1-[YTS.MX].mp4" "Movies/When Fall Is Coming (2024)/"
m "Movies/7th Heaven (1927)/Super.Mario.Bros.1993.1080p.BluRay.x265-RARBG.mp4" "Movies/Super Mario Bros. (1993)/"

echo; echo "-- Films in their own Other (extras) folder, or in a download's folder: up one level"
m "Movies/Chan Is Missing (1982)/Other/Chan.Is.Missing.1982.1080p.BluRay.x264.AAC-[YTS.MX].mp4" "Movies/Chan Is Missing (1982)/"
m "Movies/Gummo (1997)/Other/Gummo.1997.Criterion.1080p.BluRay.x265.HEVC.FLAC-SARTRE.mkv" "Movies/Gummo (1997)/"
m "Movies/La Promesse (1996)/Other/La.Promesse.1996.Criterion.1080p.BluRay.x265.HEVC.AAC-SARTRE.mkv" "Movies/La Promesse (1996)/"
m "Movies/Murder! (1930)/Other/Murder!.1930.1080p.BluRay.x264-[YTS.LT].mp4" "Movies/Murder! (1930)/"
m "Movies/Sweeney Todd - The Demon Barber of Fleet Street (2007)/Other/Sweeney Todd - The Demon Barber of Fleet Street (2007) (1080p BluRay x265 afm72).mkv" "Movies/Sweeney Todd - The Demon Barber of Fleet Street (2007)/"
m "Movies/The Boy and The Heron (2023)/Other/The.Boy.and.The.Heron.2023.aka.Kimitachi.wa.do.ikiru.ka.HDCAM.1080p.x264.mkv" "Movies/The Boy and The Heron (2023)/"
m "Movies/The Quiet Man (1952)/Other/The.Quiet.Man.1952.1080p.BluRay.x265-RARBG.mp4" "Movies/The Quiet Man (1952)/"
m "Movies/Young Frankenstein (1974)/Other/Young.Frankenstein.1974.1080p.BRrip.x264.YIFY.mp4" "Movies/Young Frankenstein (1974)/"
m "Movies/Pillow Talk (1959)/Other/Pillow Talk  (Comedy 1959)  Rock Hudson & Doris Day  (BR)/Pillow Talk  (Comedy 1959)  Rock Hudson, Doris Day & Tony Randall  (BR).mp4" "Movies/Pillow Talk (1959)/Pillow Talk (1959).mp4"
m "Movies/Hot Frosty (2024)/www.Torrenting.com - Hot.Frosty.2024.1080p.WEBRip.DD5.1.x264-LAMA/Hot.Frosty.2024.1080p.WEBRip.DD5.1.x264-LAMA.mkv" "Movies/Hot Frosty (2024)/Hot Frosty (2024).mkv"
m "Movies/No Country for Old Men (2007)/No.Country.for.Old.Men.2007.1080p.BluRay.H264.AAC-LAMA[TGx]/No.Country.for.Old.Men.2007.1080p.BluRay.H264.AAC-LAMA.mp4" "Movies/No Country for Old Men (2007)/"

echo; echo "-- Folders without their year"
m "Movies/Alps (1080)/Alps (1080).mkv" "Movies/Alps (1080)/Alps (2011).mkv"
m "Movies/Alps (1080)" "Movies/Alps (2011)"
m "Movies/The White Ribbon (1080)/The White Ribbon (1080).mp4" "Movies/The White Ribbon (1080)/The White Ribbon (2009).mp4"
m "Movies/The White Ribbon (1080)" "Movies/The White Ribbon (2009)"
m "Movies/Only Two Can Play" "Movies/Only Two Can Play (1962)"
m "Movies/The Trouble With Harry" "Movies/The Trouble With Harry (1955)"
m "Movies/Young And Innocent" "Movies/Young And Innocent (1937)"
m "Movies/Illegally Yours (1988) " "Movies/Illegally Yours (1988)"

echo; echo "-- Loose films at the top of Movies: each into its own folder"
m "Movies/A.History.Of.Violence.2005.1080p.BluRay.x265-RARBG.mp4" "Movies/A History Of Violence (2005)/"
m "Movies/Brigsby.Bear.2017.1080p.BluRay.x264-[YTS.AG].mp4" "Movies/Brigsby Bear (2017)/"
m "Movies/Friendship.2024.1080p.WEBRip.x264.AAC5.1-[YTS.MX].mp4" "Movies/Friendship (2024)/"
m "Movies/Janet.Planet.2024.1080p.WEBRip.x265-KONTRAST.mp4" "Movies/Janet Planet (2024)/"
m "Movies/Maurice.1987.1080p.BluRay.x265-RARBG.mp4" "Movies/Maurice (1987)/"
m "Movies/Mountainhead.2025.1080p.WEBRip.x264.AAC5.1-[YTS.MX].mp4" "Movies/Mountainhead (2025)/"
m "Movies/Sick.Of.Myself.2022.1080p.BluRay.x264.AAC5.1-[YTS.MX].mp4" "Movies/Sick Of Myself (2022)/"
m "Movies/Sinners.2025.1080p.WEBRip.x264.AAC5.1-[YTS.MX].mp4" "Movies/Sinners (2025)/"
m "Movies/Strange.Darling.2023.1080p.WEBRip.x264.AAC5.1-[YTS.MX].mp4" "Movies/Strange Darling (2023)/"
m "Movies/The.Legend.Of.Ochi.2025.1080p.WEBRip.x264.AAC5.1-[YTS.MX].mp4" "Movies/The Legend Of Ochi (2025)/"
m "Movies/Tron.1982.1080p.BluRay.x265-RARBG.mp4" "Movies/Tron (1982)/"
m "Movies/Tron.Legacy.2010.RERIP.PROPER.1080p.BluRay.H264.AAC-LAMA.mp4" "Movies/Tron Legacy (2010)/"
m "Movies/Welcome.to.Sarajevo.1997.1080p.BluRay.x265-RARBG.mp4" "Movies/Welcome to Sarajevo (1997)/"

echo; echo "-- TV: seasons kept as separate series, a series inside another, loose episodes"
for s in 1 2 3; do
  for d in TV/Physical\ S0$s\ COMPLETE*; do m "$d" "TV/Physical (2021)/Season $s"; done
  m "TV/The White Lotus S0$s" "TV/The White Lotus (2021)/Season $s"
done
m "TV/Succession Season 1 Complete 720p WEB x264 [i_c]" "TV/Succession (2018)/Season 1"
m "TV/Alfred Hitchcock Presents (1955-1962)/A Man on the Inside S01 COMPLETE (1080)" "TV/A Man on the Inside (2024)/Season 1"
m "TV/True Detective Season 1  (1080)" "TV/True Detective (2014)"
m "TV/Billy Joel And So It Goes 2025)" "TV/Billy Joel - And So It Goes (2025)"
m "TV/Tales From The Crypt S01-S07" "TV/Tales From The Crypt (1989)"
for f in TV/Heated.Rivalry.S01E0*; do case "$f" in *'*'*) ;; *) m "$f" "TV/Heated Rivalry (2025)/Season 1/";; esac; done

echo; echo "-- TV: Other → Extras (so the extras aren't shown as episodes)"
for s in "Berlin Alexanderplatz (1980)" "Fishing with John (1992)" "Nighty Night (2004)" "Ripping Yarns (1976)" "The Hitchhikers Guide to the Galaxy" "True Detective (2014)"; do
  if [ "$GO" != go ] && [ "$s" = "True Detective (2014)" ] && [ ! -d "TV/$s" ]; then m "TV/True Detective Season 1  (1080)/Other" "TV/True Detective (2014)/Extras"; continue; fi
  m "TV/$s/Other" "TV/$s/Extras"
done

echo; echo "-- Exact duplicates (another copy of each stays where it belongs): to Set aside, to delete when you're happy"
a() { m "$1" "Set aside/$1"; }
a "Movies/The Sewen Year Itch (1955)"
a "Movies/Dodes'ka-den (1970)/West.Is.West.2010.1080p.WEBRip.x264.AAC5.1-[YTS.MX].mp4"
a "Movies/The Learning Tree (1968)"
a "Movies/Three Documentaries (1962)/Paul Robeson Tribute To An Artist (1979).mp4"
a "TV/The Sopranos/Season 3/IT.Welcome.to.Derry.S01E02.1080p.HEVC.x265-MeGusta[EZTVx.to].mkv"
a "TV/The Box of Delights/The Sopranos - The Complete Series (Season 1, 2, 3, 4, 5 & 6) + Extras.mkv"
a "Movies/Jesus of Nazareth (1977)"
a "Movies/Naqoyqatsi (2002)/Other/Naqoyqatsi (2002) Criterion (1080p BluRay x265 Silence).mkv"
a "Movies/The Brood (1979)/Other/Crimes of the Future (1970) - Early Film Directed by David Cronenberg.mkv"
a "Movies/Fanny and Alexander - Television Version(1983)/Scenes"
for p in 1 2 3 4 5 6; do a "Movies/Scenes from a Marriage (1973)/Other/Scenes from a Marriage (1973)-pt$p.mkv"; done

echo; echo "== $N to move$( [ $SKIP -gt 0 ] && echo ", $SKIP left alone")$( [ "$GO" = go ] && echo ': done' || echo '. Run  bash tidy-library.sh go  to do it.')"
