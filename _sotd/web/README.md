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

Put here on 26 Oct 2026, rebuilt from the live page; `--check` reproduced all four built files exactly. Until then the
source lived only in each chat's own workspace, so one chat's copy could fall behind another's.
