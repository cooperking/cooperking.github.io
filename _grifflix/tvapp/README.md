# Grifflix on a Fire TV

A small Android app that shows https://cooperking.github.io/grifflix/ full screen on a Fire TV, worked by the remote.
Made from the Song of the Day TV app (`_tvapp`), without its YouTube sign-in and adverts. Its own package
(`io.github.cooperking.grifflix`) and its own signing key (`make_key.py`: from the same secret as the songs app, with a
different label), so the two apps sit side by side.

The page knows it's on a TV from the browser's name (`GRIFFLIXTV/1`; `?tv=1` tries it in a browser) and then:
- the arrows move to the nearest button in that direction (left and right along the row), OK presses it;
- in the player: OK or play/pause pauses, left and right go back 10 s and on 30 s, up or down shows Back.

The app passes on what the page can't hear: Back (`grifflixTV.back()`: the player, the top pop-up, Browse or My list
back to the calendar; with nothing to close, the app closes), play/pause, rewind and fast-forward (`grifflixTV.key`),
and Menu: Sign in with a code (the 6-letter code from a Song of the Day Profile; the app has storage of its own, so
it signs in once), Check for a new version, Reload. The screen stays on while a film plays (`GRIFFLIXTV.playing`).

**Install:** on the Fire TV, Settings → My Fire TV → Developer options → Install unknown apps → Downloader: on. In
Downloader, go to `cooperking.github.io/grifflix/tv.apk`. New builds install over the old one; the app offers them
itself (`grifflix/tv.json`).

**Build:** `.github/workflows/grifflix-tv.yml` runs `build.sh` on any change here (on main) and puts the APK at
`grifflix/tv.apk`.
