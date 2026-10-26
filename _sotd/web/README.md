# The calendar page's source

- `template.html`: the calendar page (styles, markup, script), with `/*DATA*/` where the song list goes.
- `sw.js`: the service worker (the calendar offline, members' downloads).
- `page.py`: builds `songs/draft/` (default) or `songs/` (`--live`) from them; `--check` says whether the pages in
  `songs/` are exactly what this folder builds.

Change the page here, never in `songs/index.html` or `songs/draft/index.html`:

1. Edit `template.html` (and `sw.js`).
2. `python3 _sotd/web/page.py`, then commit and push: the draft at /songs/draft/.
3. Once John has signed the draft off: `python3 _sotd/web/page.py --live`, commit and push.

The build keeps three things from the page it replaces, because the repository's jobs keep them current there: the
song list copy in the page (the page fetches the live list from the Worker anyway), the song count in its description
(`share_pages.py`) and the icons' `?v=` (`icons.py`).

The other pages in `songs/` (`admin.html`, `members-admin.html`, the share pages) are edited where they are.

## The apps open this page

The Fire TV app, the iPhone app and the Android app all open the live calendar (`/songs/`), so a change here reaches
them the next time they open, with no new app. An app only needs a new build when its own part changes. Because an
older app can meet a newer page, and an app with a feature can meet a page without it:

- **Check before calling into an app**: the object and the method, every time (an older app may have the object but
  not a newer method), and do nothing when they aren't there.
- **Don't rename what the apps call in the page**: `sotdJoin(code)`, `sotdTV.key(k)`, `sotdTV.back()`. Add new names
  beside them instead.

What there is now:

| App | The page finds it by | The page calls | The app calls in the page |
| --- | --- | --- | --- |
| Fire TV (`_tvapp`) | `SOTDTV/` in the user agent; `window.SOTDTV` | `SOTDTV.playing(on)`; `SOTDTV.advert()` (from the app's own hook) | `sotdJoin`, `sotdTV.key`, `sotdTV.back` (each checked first) |
| iPhone (`_sotd/ios-app`) | `Capacitor.isNativePlatform()` (`NAT`) | `Capacitor.Plugins.PushNotifications` (`NPUSH`) | nothing |
| Android (`_sotd/android-app`) | its start address `?app=android-apk&build=N` (Play's copy: `app=android`); first builds: the `android-app://io.github.cooperking.sotd` referrer | nothing: it reads `songs/android.json` and offers the site's APK a newer build | nothing |

Put here on 26 Oct 2026, rebuilt from the live page; `--check` reproduced all four built files exactly. Until then the
source lived only in each chat's own workspace, so one chat's copy could fall behind another's.

## On the draft only, waiting for John's sign-off

- The Android app's update offer (the `.appupd` bar, and the "the Android app" part of the script, 26 Oct 2026). The
  live page was built without it on 26 Oct (the Fire TV bar went live then); a live build before John signs it off
  needs it taken out the same way. Remove this line when it goes live.
