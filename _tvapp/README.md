# Song of the Day on a Fire TV

A small Android app that shows https://cooperking.github.io/songs/ full screen on a Fire TV, worked by the remote.
The page knows it's on a TV from the browser's name (`SOTDTV`) and then lays itself out 1280 wide and moves
between buttons with the arrows (`html.tv`, `window.sotdTV` in the page). The app passes on the other buttons:

- Back: closes the top pop-up; with none open, closes the app (on another page, like the advert help page, goes back)
- Play/pause: the song or video playing, or the open day's first song
- Rewind / fast-forward: the day before or after (or the month, on the calendar)
- Menu: switches between the live site and the draft (remembered)

The screen stays on while something plays. Spotify and YouTube links open in their apps, if installed.

**Install:** on the Fire TV, Settings → My Fire TV → Developer options → Install unknown apps → Downloader: on.
In Downloader, go to `cooperking.github.io/songs/tv.apk` and install. New builds install over the old one.

**Build:** `.github/workflows/tv-app.yml` runs `build.sh` (aapt2, javac, d8, apksigner; no Gradle) on any change
here and puts the APK at `songs/tv.apk`. It's signed with a key derived from the `YT_CLIENT_SECRET` secret
(`make_key.py`), so it's the same for every build without being stored anywhere.
