# Song of the Day on a Fire TV

A small Android app that shows https://cooperking.github.io/songs/ full screen on a Fire TV, worked by the remote.
The page knows it's on a TV from the browser's name (`SOTDTV`) and then lays itself out 1280 wide and moves
between buttons with the arrows (`html.tv`, `window.sotdTV` in the page). The app passes on the other buttons:

- OK: opens a day or presses a button; held down on a day, adds it to My playlist (or takes it off)
- Back: closes the top pop-up; with none open, closes the app (on another page, like the advert help page, goes back)
- Play/pause: the song or video playing, or the open day's first song
- Rewind / fast-forward: the day before or after (or the month, on the calendar)
- Menu: a menu with Sign in to YouTube (Google's sign-in, in the app; with Premium the videos have no adverts;
  Sign out once signed in) and switching between the live site and the draft (remembered); it shows the build
- The first advert each time the app's open, while not signed in to YouTube: the app offers the sign-in

The screen stays on while something plays. Spotify and YouTube links open in their apps, if installed.

**Install:** on the Fire TV, Settings → My Fire TV → Developer options → Install unknown apps → Downloader: on.
In Downloader, go to `cooperking.github.io/songs/tv.apk` and install. New builds install over the old one.

**Updates:** the site itself changes in the app straight away. For the app, each build writes its number to
`songs/tv.json`; from build 13 on, the app looks there a few seconds after it opens and, if there's a newer build,
offers it (Later: asks again in a day). Install downloads it and hands it to the TV's installer, which asks to
confirm. The first time, the TV asks to let Song of the Day install apps (the same switch as for Downloader).
Checked in an emulator with `check_update.sh` (tv-app-check.yml, script: check_update.sh).

**Build:** `.github/workflows/tv-app.yml` runs `build.sh` (aapt2, javac, d8, apksigner; no Gradle) on any change
here and puts the APK at `songs/tv.apk`. It's signed with a key derived from the `YT_CLIENT_SECRET` secret
(`make_key.py`), so it's the same for every build without being stored anywhere.
