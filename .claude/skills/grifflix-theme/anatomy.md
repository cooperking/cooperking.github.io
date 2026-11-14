# Anatomy of a Grifflix monthly theme

> Line numbers (`T:1234`) are as of 14 Nov 2026 and drift as the file changes: find things by the names given (grep), not by number.
> The scratchpad (`SP`) scripts below are gone with that session; their replacements are in `_grifflix/themetools/` (preview.js, perf.js).

Repo `/home/user/cooperking.github.io`, HEAD 29f2452. `T` = `_grifflix/web/template.html` (6657 lines). Line numbers are T's unless a file is named.
`SP` = `/tmp/claude-0/-home-user-cooperking-github-io/7d37fd45-f41a-5dd6-b619-287345e0f137/scratchpad`.

Layout of T: `<style>` 38–1914 · small head scripts 1915–1972 · body HTML ~1928–1973 · main `<script>` 1975–6655.

---

## 1. Every place a theme is defined or registered

### 1.1 The registry (JS)
| What | Where | Notes |
|---|---|---|
| `THEMES` (the 12-month default order) | T:2407–2441 | One object per line, preceded by a `//` research comment. Index 0 = Jan 2026. Current order: corridor, slices, umbrellas, code, spiral, tron, pinboard, moon, reel, oz, table, globe. |
| `THEMES_LATER` (parked themes) | T:2449–2483 | jaws, credits, bars, fold, stairs, lane, picture, hotel, bedford, autumn. Reachable via `?theme=` or admin month assignments. |
| `THEMEFONTS` (one Google Fonts CSS URL for all themes) | T:2442 | Injected as `<link id="themefonts">` at T:2840 the first time a theme is drawn. |
| `THEMEALL`, `THEMEDEF` | T:2487 | `THEMEALL = THEMES.concat(THEMES_LATER)` + `THEMEBLANK` (T:2492). |
| `BLANKFROM = '2026-12'`, `THEMEBLANK` (key `blank`) | T:2491 | Every month ≥ BLANKFROM is blank (Grifflix blue, plain posters) **unless** the admin page gives it a theme. |
| `THEMEMONTHS` (`{ 'YYYY-MM': key }`) | T:2493 | From the admin page via the Worker. |
| `themeOrder(keys)` | T:2494–2500 | Takes the admin value: an array (order) or `{order, months}`. It replaces `THEMES`. All keys must exist in THEMEALL, otherwise the default is kept. |
| Order cached on device | T:2501 (`localStorage grifflix-themes`) | Refreshed from the Worker `/g/cal` response at T:2103 (`W.themes`, or `W.themesDraft` on the draft build). |
| `themeFor(m, y)` | T:2502–2509 | Priority: `?theme=` (`THEMEQ`, T:2000) → `THEMEMONTHS[ym]` → `ym >= BLANKFROM` gives blank → `THEMES[((y-2026)*12+m) mod n]`. |
| `?theme=<key>` override | T:2000 (`THEMEQ = Q.get('theme') \|\| (tvview=table ? 'table' : '')`) | Forces one theme for every month. |
| `?still=day\|month\|site` | T:2001–2002 | Share-still mode (html.still, html.still-*). CSS at T:1403–1405. |
| Theme object fields | see THEMES | `key`, `mob` [x0,y0,x1,y1], `name`, `film`, `year`, `by`, `bg` (lower-case `#rrggbb`), `ink` (`'#000'` or `'#fff'`), `font` (CSS family list), `tw` (weight), `tt` (text-transform, default uppercase), `fs` (font-style, optional), `credit`/`creditM` (picture only, T:2473). |
| **`mob` field** | THEMES entries | **Not read anywhere in the current JS.** grep finds no reader. The phone path now draws every theme at 780×890 (`themeW/themeH`, T:2015–2016) and scales it with `themeFit` (T:3008–3024). `mob` comes from an old patch (`SP/themes5.py` built `themeFit` from `themeFor(m).mob`). Keep the field for consistency; it has no effect. |
| Theme engine object `THEMEFX[key]` | `var THEMEFX = {}` T:3140; blocks T:3534–4847 | See §2.3. |

**Format constraints (page.py parses T with a regex, `_grifflix/web/page.py:30–43`):**
- Each entry must be on **one line** starting `{ key: '<\w+>'`.
- After `key` the fields must come in this order: `name: '…', film: '…', year: N` (adjacent, in that order), then anything, then `bg: '#xxxxxx', ink: '#…'` (adjacent).
- `font: '…'`, `tw: N`, `tt: '…'` and `fs: '…'` must be on the same line.
- The list must close with `\n];`. `bg` must match `#[0-9a-f]{6}` (lower case).
- Escape an apostrophe as `\'` or use `’`.

### 1.2 Generated and companion files (from page.py)
`python3 _grifflix/web/page.py` (page.py:85–100) writes all of these, every build:
- `grifflix/draft/index.html`: T with `/*BUILD*/{ draft: true, v: '<sha1(T)[:12]>' }` (page.py:24–33; marker at T:1978) and `/*ARTV*/` replaced.
- `grifflix/draft/version.json` `{v}`. `--live` writes `grifflix/index.html` + `grifflix/version.json` instead.
- `grifflix/themes.json` (page.py:46–52): `{order: first 12 of THEMES, themes: {key: {bg, ink, name, film, year}}}` covering THEMES + THEMES_LATER. The Worker reads it (§1.10).
- `grifflix/admin.html` (page.py:55–66), in place:
  - `/*THEMELIST*/…/*THEMELIST END*/` (admin.html:39): `theme_list()` JSON `{k,n,f,y,bg,d,font,tw,tt,fs}`.
  - `/*GXOWN*/` (admin.html:40): GX bg/ink + BLANKFROM.
  - `<!--THEMEFONTS-->…<!--THEMEFONTS END-->` (admin.html:60–72): the THEMEFONTS link, plus **every T line that starts with** `@font-face { font-family: "GF-` or `@font-face { font-family: "Grifflix Display"`.
- `grifflix/collection-art.js` (copy).
- `--check` exits 1 if the draft isn't what T builds.

### 1.3 CSS hooks per theme
These are set by JS on `<html>`:
- `data-theme=<key>`: set in `themeEarly` T:2443–2446 (before data arrives) and `drawMonth` T:2838; removed in calendar view (T:2841).
- Class `table` (key==='table') or `hall` (every other theme): T:2004–2005, 2445, 2832–2833.
- Class `mob` (themes view on a phone), T:2834. Class `phone` T:2010. Class `tview` T:2835. Class `tv` T:2023. Class `big` (computer/TV, head script T:1923). Class `covered` T:3145–3148. Classes `gxown`/`gxproj`/`gxdim` (picture/blank) T:2519–2520. Class `still` T:2002.
- CSS variables from `drawMonth` T:2839: `--tfont`, `--tw`, `--ttt`, `--tfs`. From `monthTint` T:2516–2527: `--bg`, `--kink` (ink as "r, g, b"), `meta theme-color`, `localStorage grifflix-lastbg`.
- `drawTheme` wraps the scene in `<div class="th th-<key>">` (T:3345). Per-theme CSS uses `.th-<key> …` for the scene and `html[data-theme=<key>] …` for page chrome.

Per-theme CSS that exists today (grep `data-theme=` and `th-`):
- **Day words (`.hinfo`)**: generic `.hinfo` T:628 and T:630–633. Per-theme overrides, e.g. corridor 621–622 and 1452–1454, reel 667–669, picture 798–802 and 1897–1899, hotel 860, bedford 881–885, code 900–902, slices/spiral/pinboard 909–912, credits 946–949, autumn 954, globe 987 and 996–997, pinboard 1000 and 1709, moon 1365–1369, fold 1464–1466, stairs 1467–1468, lane 1472–1473, bars 1553–1558, oz 1567–1569, jaws 1590–1591, tron 1605–1606, spiral 1910–1911. Shared font rule: T:985 (`html[data-theme] .hinfo .w, .hinfo h2` use `--tfont/--tw/--ttt/--tfs`). Brand-serif fallback for picture/blank/no theme: T:1707.
- **Calendar page beside the day (`.leaf2`, built by `leafOf()` T:2957)**: base T:1441–1446. Per theme T:1447–1516 (every key has a block). More at T:1563–1564 (bars), 1576–1578 (oz), 1592–1594 (jaws), 804–806 and 861–863 (picture, hotel), 886–888 (bedford). Font rule T:1448.
- **"Coming soon" card (`.cs`)**: T:672–673, per theme 675–677 and 803.
- **Calendar pop-up (`#sheet`) in the month's colour**: generic `html[data-theme] #sheet …` T:1034–1038 and 1308–1310. No per-theme rules.
- **Logo**: not per theme. GRIFFLIX is an outline in the month's ink: `.bar-top .brand` T:1745–1747, `--gx-mark` SVG T:1742. Only Grifflix's own palette (`html.gxown`, picture/blank) recolours `.zlogo/.zstrip` (T:1758, 1851).
- **Caption and month label**: `.tinspo`/`.tmonth` T:597, 1021, 1614–1617. Text from `themeCaption()` T:3084–3096 and `tmonthText()` T:3076. `capBar()` T:3097 is the calendar pop-up's caption.

### 1.4 Fonts
- Google Fonts: add the family to `THEMEFONTS` T:2442. page.py copies it into admin.html.
- John's own fonts: an `@font-face { font-family: "GF-<key>"; font-weight: 100 900; font-display: swap; src: url("https://sotd-playlists.cooperking.workers.dev/gf/<file>.woff2") … }` line in the block at T:1430–1439. It must start at column 0 with exactly that prefix so page.py copies it to admin.
  - Files are uploaded on the admin Fonts card. The Worker route `font` (worker.js:1749–1760) stores them in R2 at `grifflix/fonts/<name>`.
  - They are served by `/gf/<name>` (worker.js:106, 2238–2245), which is refused unless the Origin/Referer matches `G_FROM`.
  - The theme's `font:` value lists `"GF-<key>"` first, then a free Google fallback.
- Other faces: `GFutura` T:562–564 and `Grifflix Display` T:1715 (`grifflix/fonts/GrifflixDisplay.woff2`).

### 1.5 TV-specific (html.tv)
- TV detection: UA `GRIFFLIXTV/` or `?tv=1` (T:1992). Viewport rewrite T:1916.
- Scale: `TVZ = .8` T:2014. themeW/H divide by TVZ (T:2015–2016), and the CSS is `html.tv.hall #cal, html.tv.table #cal { zoom: .8 }` T:1364. So a TV draws a 1600×900 computer layout, shrunk.
- Focus ring: `html.tv :focus` T:1835–1838 uses `--ks`, which `themeShow` T:3399 sets per focused day so the ring is not scaled. Per-theme TV rules: picture 740, 1843, 1908; fold 794; moon 926 and 1365; globe 1841–1842. Bar rules T:1007, 568–569; `focusin` handler T:4840–4846 (barhide/tvfront).
- Navigation: the remote's left/right reach days by focus. `themeStart` T:3379 wires `focus` → `themeTurn`. `themeStep` T:3497 moves focus.
- Covered: `COVERED()`/`coverCheck()` T:3144–3148 and `html.covered #cal { visibility:hidden }` T:992. Canvas loops must rest while covered (see snowfall T:4705).

### 1.6 Phone
- `MOB()` T:2009 (≤760px, or portrait ≤1100px; never on a TV). The scene is drawn at 780×890 (`themeW/H`). `themeFit()` T:3019–3023 scales `.thv` to the width and sets `--vk`.
- Words: `.hinfo` is placed under the scene, `html.mob .hinfo` T:1400 (top uses `--vk*890px`).
- Buttons: `.mobnav` (day/month buttons) come from `themeCaption` T:3090–3093 and are wired in `wireCaption` T:3131.
- Gestures: swipe/wheel handler T:3028–3073; arrow keys T:2814.
- Every `THEMEFX` geometry branches on `c.mob`.
- Phone overrides per theme: e.g. T:1378–1386 (table), 1383–1384, 997, 1367–1369, 1453, 1558, 1629, 1640, 1899.

### 1.7 Month-to-month transition
- `themeStep(dir, g)` T:3497–3521: stepping from the 1st or last day crosses into the next month at once. A swipe begun inside the month stops at its edge (T:3508).
- `themeWipe(dir, swap)` T:3426–3447: a `.twipe` panel in the **destination** month's `bg`/`ink` (globe forced to `#0b0b0b`, T:3434). Durations: TV 320/460 ms, otherwise 280/420 ms. With CALM, or no WAAPI, it does `swap()` then `themeFadeIn` instead (T:3427).
- `themeFadeIn` T:3448–3451. `themeMonth(dir)` T:3522–3532 (phone month buttons). `themeCross` T:3419–3422.
- `S.step` is `drawMonth`'s month stepper (T:2789–2794). It redraws via `drawMonth` → `drawTable`/`drawHall`/`drawTheme` (T:2872–2873).
- Neighbour days: `themeKeys(own)` T:3317–3325 adds 6 days of the previous and next month (`data-om="1"`) unless `fx.own`. `themeShow` hides days beyond the month's count with `.xwin` (T:3403–3405, CSS T:1587) unless `fx.whole`.

### 1.8 Theme music
- Picks: `_grifflix/theme_music.py:15–37` `PICKS = { key: (search, artist-must-contain, title-must-contain[, album-must-contain]) }`. The script searches iTunes and writes `grifflix/data/thememusic.json` `{key: {title, artist, secs, src, h}}`, where `h` = sha256(src)[:12]. It skips keys already present (theme_music.py:56–57), so delete a key to re-pick.
- Page: T:3156–3195. It fetches thememusic.json (T:3163). `themeMusic(k)` plays `WORKER/gm/<key>-<h>.m4a?src=<preview url>` at volume .5, faded in.
  - `picture` instead plays `/grifflix/audio/grifflix-theme.m4a` plus a WebAudio projector sound (T:3172–3175, 3206–3252).
  - Called from `drawMonth` T:2836.
  - It stays quiet over films, the calendar or search (`musicFilm` T:3307, poll T:3311–3316), and is held until the 3 2 1 leader ends (`musicAfterLeader` T:3197).
  - Mute: `TMUS.on` plus `localStorage grifflix-music` (T:3161, 3119–3130).
- Worker: `/gm/` `gMusicGet` worker.js:2203–2236. On the first request it downloads the clip from `audio-ssl.itunes.apple.com` only, checks it against `h`, and stores it in R2 `grifflix/music/<key>-<h>.m4a`. Range responses are supported.
- `grifflix/audio/` holds only `grifflix-theme.m4a` and `leader.m4a`. Per-theme clips are not in the repo.

### 1.9 The theme's film on the month's 1st
- `_grifflix/theme_films.py` (needs `TMDB_TOKEN`) writes `grifflix/data/themefilms.json` `{key: {id, ext:1, name, year, poster, backdrop, trailers, …}}` for every theme in `theme_list()` except picture and blank (theme_films.py:41–53).
- The admin page reads it (admin.html:1171) and puts the film on the 1st of the theme's month (`firstJob` admin.html:1172–1175; `seqAt`/`nextFirst` 1160–1168).
- `_grifflix/data_publish.py:114–118` publishes such `ext` days into days.json.
- `themeFilm(t)` T:3077–3083 links the caption to the film if the library has it (matched by name prefix and year).

### 1.10 Worker (`_sotd/worker/worker.js`)
- Emails: `gThemeAt` worker.js:1366 (same formula). `gThemeLoad` worker.js:1367–1376 reads `G_RAW + 'themes.json'` (cached 1 h) and KV `gthemes`. **It hard-codes `'2026-12'`** as the start of the blank months (1371), separately from T's BLANKFROM. `gShell` (1378) uses the theme's bg/ink.
- Admin route `themes` worker.js:1839–1862: KV `gthemes` (live) and `gthemes-draft`; `{publish:true}` promotes the draft. `/g/cal` returns `themes` and `themesDraft` (1866–1867).
- Share pages `/gs/<YYYY-MM-DD|YYYY-MM|cal>[.jpg]` `gSharePage` worker.js:2265–2300. Cards come from R2 `grifflix/cards/<key>.jpg`. The landing page uses the card's metadata `bg`/`ink`/`name`. The Worker does not draw themes itself.
- Card upload: `own('card', {date, card, bg, ink, name})` (T:5719), Worker ~2123.

### 1.11 Share cards and OG images
- Client: `brandCard()` T:5662–5695 draws a 1200×630 canvas. Only `themeFor().bg/ink` comes from the theme. The title is in Grifflix Display, and the right half shows posters or the backdrop. `dayCard` 5696, `monthCard` 5701, `calCard` 5704. `shareLink` 5713–5722 uploads a card if none exists. `shareMonth` 5727. `?cardtest` exposes `window.__g` (5725).
- Stills: `_grifflix/stills.py` (run by `.github/workflows/grifflix-stills.yml`) opens the live site signed out at 1600×840 with DPR .75. URLs: `?still=day#day/<date>`, `?still=month#month/<ym>`, `?still=site&theme=picture`. It waits for `window.__still && #cal .thstage, #cal .tring`, then fonts plus 2.5 s, screenshots, and uploads to R2 `grifflix/cards/`. In the page, `stillWords()` T:3407–3418 fills `#hinfo` or `#tcentre` and sets `window.__still`. **A new theme must render `#cal .thstage` (drawTheme does) or `.tring` and call the `info` path, or stills will time out.**

### 1.12 Admin page (`grifflix/admin.html`)
- Generated blocks: admin.html:39, 40, 60–72 (§1.2). The month colours come from `GTHEMES`/`GORDER` (admin.html:36–45).
- Themes tab: drag a theme onto a month. Saved with `g('themes', {themes, draft:true})` (admin.html:1512), and goes live with "Update everything" (`g('themes',{publish:true})` admin.html:705).
- Theme explorer preview iframe: `/grifflix/draft/?view=themes&theme=<k>` plus a format suffix (`&big=1` computer 1440×900, iPhone 390×844, iPad 820×1180, `&tv=1` 1280×720) and `&sound=1`; `&leader=1` replays the 3 2 1 (admin.html:1325–1339).

### 1.13 TV app (`_grifflix/tvapp`)
It is a WebView of the site and has no theme code. The only reference is a comment at MainActivity.java:489 (sound off on leaving the app). Theme updates reach it through `freshCheck` (§1.14).

### 1.14 Version and caching
- `BUILD.v` = sha1(T)[:12]. `freshCheck()` T:3284–3293 polls `version.json` (draft or live path) every 10 min after 5 idle minutes, and on visibility. It reloads when the version changes, except over a film, trailer, pop-up or leader.
- `dayCheck` T:3268–3275 reloads on a new day or at 8 am.
- `grifflix/sw.js` caches only `/gp/` and `/pimg/` pictures, so pages and theme images are not service-worker cached.
- Theme images under `grifflix/img/` are cache-busted by hand with `?v=N` (e.g. `kane-hand.jpg?v=2` T:990).

### 1.15 Hard-coded key checks in generic code
Grep these when adding a theme that needs special handling:
- `'table'` T:2004–2005, 2445 (separate draw path, `html.table`).
- `'corridor'` T:2873 (`drawHall` instead of `drawTheme`).
- `'picture'`/`'blank'` T:2517–2522 (`monthTint` own palette), 3170–3175 (music).
- `'globe'` T:2529, 3434, plus CSS 989–997 (page background image).
- `.th-globe` snow T:3376–3378.
- Click-through selector list T:3363 (`.zstagef, .bigmoon, .mfilm, .globe, .codepic, .reelscreen, .cposter, .tcentre h2`). A new centrepiece that should open the day's film must be added here.
- theme_films.py skips picture and blank; admin `firstJob` skips picture and blank.

---

## 2. Two representative themes

### 2.0 The interface a theme implements (`THEMEFX[key]`)
Drawn by the generic `drawTheme(t, C, n, m, mine)` T:3342–3361, then `themeStart` T:3368–3382 → `themeTurn` T:3383 → `themeShow(at)` T:3387–3406.

| Member | Called at | Purpose |
|---|---|---|
| `decor(c)` → HTML string | T:3345 | Static scenery (SVG/divs) inside `.th.th-<key>`, before the days. |
| `cell(d, it, key, c)` → HTML | T:3351 | Inner HTML of each day button. Default: `<span class="vn">Mon 3</span>`. |
| `own: true` | T:3349 | Only this month's days (no neighbour days). |
| `whole: true` | T:3405 | Don't apply `.xwin` (the theme limits its own visible days). |
| `ready(c)` | T:3375 | Once after the DOM is built. Typically `fxReset(this); this.c = c`. |
| `place(all, at, c, frontBtn)` | T:3398 | **Required.** Position every day for a fractional index `at` (it is called continuously during drags). Either set transforms directly (CSS transitions animate) or call `fxTween(this, at, ms)`, which calls `draw`. |
| `draw(at, now)` | from `fxTween` T:3906–3911 | Per-frame drawing. Return truthy to keep the rAF loop running. |
| `front(b, c, dragging)` | T:3393 | The front day changed: swap the centre picture etc. (skip flourishes while dragging). |
| `info(b)` | T:3394 | Write the day's words. Default `hallInfo` T:2958–2966 writes `#hinfo`. |
| `g(c)`/`geo(c)` | theme-internal | Geometry helper by convention. |
| `push(dx)`, `fit()`, `leader()` | picture T:4349, globe T:4792, picture T:4589 | Special hooks used by name (wheel T:3038, resize T:2529, start T:2542). |
| `swipe(c)`, `all: true` | most themes | **Not read by the engine** (no caller). Legacy. |

**Context `c`** (T:3344): `{ W, H, n (days in month), m (0–11), mob, y, lead (Mon-first weekday of the 1st) }`. On a phone W×H is 780×890. On a TV it is the window size divided by .8.

**Day buttons** (T:3350–3357): `<button class="vday …" data-day="YYYY-MM-DD" data-n="d" [data-om="1"] style="--p:url(poster 400);--b:url(backdrop 480)">`.
- Classes: `has` (a film) and `today`; `slab soon` (secret till 8 am, plus a `.cs` "Coming soon"); `empty` (owner editing, unused while SITEEDIT=false T:2771); `none soon` (no film).
- The engine adds `front` and `xwin`.
- Images come from `img()` T:2382–2394 (Worker `/gp/` copies, or TMDB for `/`-paths). Data is `S.calData.days[key]` (`name`, `year`, `mins`, `poster`, `backdrop`, `thumb`, `secret`, `land`). Every `--p` poster is preloaded (T:3374).

**Day words placement** (`hallInfo` T:2958–2966): it writes `#hinfo` with:
- `.w`: `leafOf(d)` calendar leaf plus `<span data-t="Monday">Monday</span>`;
- `.k`: "3 November 2026 · Movie of the Day";
- `h2`: the film name (via `showName`);
- `.y`: "year · N minutes";
- `.n`: a note (NOTES=false T:1999, so never shown).

Secret days show `<h2>Coming soon</h2>`. Position and size come from CSS (`.hinfo` bottom-left T:628, per-theme overrides).

**Helpers**:
- `vAt(el,x,y,s,r,o,z)` T:3150–3154: transform, opacity and z-index; pointer-events are off when o<.3.
- `near(k)` T:3155. `seeded(n)` T:3141.
- `fxTween`/`fxReset`/`clamp01` T:3892–3914.
- `snowfall(host, o)` T:4690–4726 (canvas).

**Performance measures for Fire TV**:
- Canvas DPR is 1 on TV (T:3985, 4696). Snow density is ×0.45 on TV (T:4695).
- Fewer polygon segments on TV in fold (T:3974).
- `fxTween` is a single rAF that stops when settled or detached (T:3906–3911).
- `COVERED()` hides `#cal` and pauses canvases (T:992, 4705).
- `will-change: transform` on moving cards (e.g. T:891).
- Wipe durations are tuned for TV (T:3439, 3443).
- `.tday/.vday` transitions are disabled while dragging (`#cal.drag`, T:1355); phone transitions .3 s (T:1424).
- Tested with `SP/perf3.js` (TV UA, 6× CPU throttle, fps idle/moving).

**Reduced motion**:
- `CALM = matchMedia('(prefers-reduced-motion: reduce)')` T:3891. `fxTween` uses a duration of 1 ms; `themeWipe` becomes a fade; picture, moon, stairs, lane and globe skip their flourishes (T:3747, 4036, 4105, 4281–4289, 4426, 4592, 4721, 4750, 4815).
- CSS `@media (prefers-reduced-motion: reduce)` blocks: T:587 (table board), 658 (reel stars), 757, 837, 1783, 1887 (picture).

### 2.1 `spiral` (April, Vertigo): typical `drawTheme` theme, DOM + SVG, JS-tweened
| Block | Lines | Size |
|---|---|---|
| THEMES entry (+ comment) | T:2419–2420 | 2 |
| `THEMEFX.spiral` | T:3636–3660 | 25 |
| Scene CSS `.th-spiral .vday` (170×255 cards, `will-change`), hide `.vn` unless front | T:890–892 | 3 |
| `.hinfo` sizing (left of the spiral) | T:909–911 | 3 (shared selectors) |
| Words lettering (Special Gothic Expanded One, outline) | T:1909–1911 | 3 |
| `.leaf2` (radial rings) | T:1484–1485 | 2 |
| `@font-face "GF-spiral"` (clarendon-bold) | T:1433 | 1 |
| Spiral line style (generic `.hlines`) | T:603, 652 | shared |
| Music pick | theme_music.py:20; thememusic.json `spiral` | 1 |
| Theme film | themefilms.json `spiral` | data |

Structure:
- `g(c)` gives the centre and starting radius: desktop `cx=.73W, cy=.46H, r0=min(250,.19W)`; phone `390,440,300`.
- `decor` draws an SVG `<polyline>` log spiral (`r = r0·0.83^(a/.9)`, a from −1.2 to 32, step .06) in `svg.hlines.spiral`.
- `ready` calls `fxReset`. `place` stores `all`/`c` and calls `fxTween(this, at, 900)`.
- `draw(at)` puts each day k = j−at on the spiral: angle `a0 + .9k`, radius `r0·.83^k`. Scale is `.55·.84^(k−1)` ahead, `1−.45k` for k between 0 and 1, and grows and fades for past days. Days with k>16 are hidden. All through `vAt`.
- No `info`, so `hallInfo` is used. No `front`; the front card is the day's poster (`--p`).
- About 40 lines in all, plus shared engine code.

### 2.2 `table` (November, Dr. Strangelove): special-cased path, DOM + SVG + CSS transitions
| Block | Lines | Size |
|---|---|---|
| THEMES entry (+ comment) | T:2437–2438 | 2 |
| `TABLE()`/`HALL()` predicates | T:2004–2005 | 2 |
| `themeEarly` class `table` | T:2445 | 1 |
| `drawMonth` class toggle + branch | T:2832, 2872 | 2 |
| `drawTable(C,n,m,mine)` (forces `--bg #0a0a0a`, white ink; builds `.tday` buttons via `themeKeys()`) | T:2967–2980 | 14 |
| `bigBoard()` (Big Board SVG: dotted land polygons, bomber paths, target circles, lamp panels) | T:2981–3006 | 26 |
| `tableTurn` | T:3007 | 1 |
| `themeFit` desktop branch (scale scene 104–615 into the window) | T:3012–3016 | 5 |
| `THEMEFX.table` (`whole`, `geo`, `place`, `info`) | T:3555–3598 | 44 |
| stills `#tcentre` | T:3414 | 1 |
| click-through `.tcentre h2` | T:3363, CSS 1374 | 2 |
| CSS: ring/board/lamp/animations | T:572–590 | ~19 |
| CSS: full-screen layout, `.tmonth` | T:593–598 | 5 |
| CSS: `.tring/.tday/.tcentre` | T:634–644 | 11 |
| CSS: `.tday.soon` | T:678 | 1 |
| CSS: shared `html.hall/html.table` bar/caption rules | T:1007–1027, 1076, 1191–1192, 1614–1617, 1692, 1695 | shared ~30 |
| CSS: phone | T:1378–1386, 1426–1427 | ~11 |
| CSS: title fit `--tl` | T:986, 1634–1641 | ~9 |
| CSS: `.leaf2` | T:1446, 1511, 1518 | 3 |
| CSS: TV zoom | T:1364 | 1 |
| Font | Amatic SC via THEMEFONTS (no GF-table) | — |
| Music pick | theme_music.py:26 ("We'll Meet Again") | 1 |

Structure:
- Not drawn by `drawTheme`, so there is no `.th` wrapper, no `decor`/`cell`, and `.tday` instead of `.vday`.
- `place` seats exactly the month's own days (`data-om` days hidden) at equal arc-length spacing round an ellipse. Geometry: desktop `cx=W/2, cy=418, rx=530, ry=150`; phone `390,520,352,196`.
- It sets `transform: translate(x,y) scale(.62+.38·depth + near·(fs−1))`, z-index and `filter: grayscale/brightness` (front day in colour). A CSS transition on transform animates it (seated without it the first time).
- `info` writes `#tcentre`: leaf, "Weekday · d Month", `h2` with `--tl` (title length) for clamp sizing, and year/minutes.
- Board: since 14 Nov a still SVG (`.tbstill`) and a moving one (`.tbmove`); the paths are ticked by `bigBoardTick()` (a 250 ms timer), the lights and targets blink with `tbblink` (steps). Posters move by `transform` alone (no left/top).
- About 100 lines of table-specific JS and about 60 lines of table-specific CSS.

---

## 3. Build, draft and live, preview

**Build**:
- `python3 _grifflix/web/page.py` builds the draft (`grifflix/draft/index.html`) and rewrites `grifflix/themes.json`, the admin.html blocks and collection-art.js.
- `--live` builds `grifflix/index.html`. `--check` verifies the draft.
- Edit **only** T, never the built files (page.py docstring).

**Draft and live**:
- Committed draft changes show on `/grifflix/draft/` (DRAFT tag T:2543). The draft also sees `themesDraft` (T:2103).
- John's "Update everything" on the admin page does three things: it publishes `gthemes-draft` (admin.html:705); it asks the Worker `admin {op:'publish'}` (admin.html:712); and that dispatches `.github/workflows/grifflix-golive.yml`, which runs `page.py --check` then `--live` and commits `grifflix/index.html` + `version.json` to main.
- The previous sessions' route (summaries.txt): commit → push to branch `claude/family-streaming-service-m0xu0l` → PR → squash merge → (on request) trigger `grifflix-golive.yml`. The draft goes live only with John's go-ahead.

**Preview harness** (in SP; reusable, nothing in the repo):
1. Build, then copy the draft into the static mirror: `cp grifflix/draft/index.html $SP/site3/grifflix/draft/`.
   - `site3` is a copy of `grifflix/` with local `data/` (days.json, library.json, etc.), `gf/`, `img/`, and old reference builds (`site3/grifflix/old`, from 5f982cd).
   - Earlier sessions also sed the GrifflixDisplay URL to `/gf/GrifflixDisplay.woff2`, because `/gf/` and the github.io font refuse other origins.
   - Copy any new `grifflix/img/*` and `grifflix/data/thememusic.json`/`themefilms.json` too.
2. Serve: `(cd $SP/site3 && nohup python3 -m http.server 8772 &)`. It dies often, so restart it. For range requests (audio/video) use `$SP/rangesrv.py` (port 8773, serves cwd).
3. Playwright scripts (node, `require('playwright')`):
   - `$SP/th.js <key>`: 1440×900 and 430×932 screenshots of `/grifflix/draft/?view=themes&theme=<key>` after 2× ArrowLeft → `th-<key>-d.png`/`-m.png`.
   - `$SP/allth.js $SP`: every key at 1440×900, focus 2026-11-01, logs `om` count, front day and page errors → `all-<key>.png`.
   - `$SP/tshots.js $SP key1,key2 tag`: puts the theme's `themefilms.json` film on 2026-11-09 in `site3/.../days.json` (restored after), `&big=1`, logs the `h2` font → `<tag>-<key>.png`.
   - `$SP/perf3.js key…`: TV UA, 960×540 at DPR 2, 6× CPU throttle via CDP, fps/p95 idle and moving.
   - `$SP/zmid2.js`: fake sign-in (`localStorage grifflix-member='test'` + routed `/g/me` and `/api/me`).
   - `$SP/thm.js`: TV UA flow through the calendar pop-up.
   - TV UA string: `Mozilla/5.0 (Linux; Android 9; AFTMM) Chrome/120 GRIFFLIXTV/1`.
   - Skip the 3 2 1 leader with `addInitScript(() => localStorage.setItem('grifflix-leader-at', String(Date.now())))` (3 h throttle T:4597). Older scripts set `grifflix-leader`, which is no longer read.
   - `?view=themes` also stops the leader replay (T:3297, 4593).
4. Signed out (no token) still shows themes and the calendar (`PUBLIC`, `route()` T:2669–2674). Days come from `data/days.json` plus the Worker `/g/cal` (network).
5. Demo data: `var DEMO = false;` T:1989; `Demo` T:6435. To use made-up films, rewrite the page in a Playwright route, as `$SP/sfshot.js` does: `p.route(/localhost:8773\/grifflix\/draft\/(\?.*)?$/, async r => { const res = await r.fetch(); r.fulfill({ response: res, body: (await res.text()).replace('var DEMO = false;', 'var DEMO = true;') }); })`. `?demo=1` no longer does anything (T:1988).
6. Useful query flags: `?theme=`, `?view=themes`, `?tv=1`, `?big=1`, `?sound=1`, `?leader=1`, `?still=day|month|site` (+ `#day/…`, `#month/YYYY-MM`), `?cardtest`.
   - Old patch scripts `SP/themes*.py`/`themes_patch.py` are one-off `rep()` editors of T, not previewers.
   - `curtain.py`/`anim.py` generate images (velvet curtain webp; Méliès moon webp sequence) into `grifflix/img/`.

---

## 4. Checklist: add theme X (key `x`) for month M

1. **Research** the film's colours and lettering (John's rule, T:2407 comment): one flat `bg`, `ink` `#000` or `#fff`, the nearest free Google font, and optionally John's own font file.
2. **Registry, T:2407–2483**: add a one-line entry `{ key: 'x', mob: [0, 0, 1280, 720], name: 'The …', film: '…', year: YYYY, by: '…', bg: '#rrggbb', ink: '#fff', font: '"GF-x", "Free Font", sans-serif', tw: 700, tt: 'uppercase' },` with a `//` comment above it citing the sources.
   - Put it in `THEMES_LATER` if M is assigned on the admin page (any month ≥ 2026-12 is blank unless assigned). Put it in `THEMES` only to change the default rotation; that shifts every month, because index = (y−2026)·12+m mod length.
   - Mind the page.py regex rules (§1.1).
3. **Fonts**:
   - Add the Google family to `THEMEFONTS` T:2442.
   - If John's own font is used, add `@font-face { font-family: "GF-x"; …/gf/<file>.woff2 … }` in T:1430–1439, starting at column 0. John uploads the file on the admin Fonts card.
4. **Engine**: add `THEMEFX.x = { … }` next to the others (T:3534–4847), modelled on spiral (§2.1) for JS-tweened motion or reel/umbrellas for CSS-transition motion.
   - Required: `place(all, at, c)`.
   - Optional: `decor`, `cell`, `ready`, `draw` (with `fxTween`), `front`, `info`, `own`, `whole`.
   - Branch on `c.mob` (780×890 canvas).
   - Respect `CALM`. Use `TV` to cut cost (DPR 1, fewer elements). Rest when `COVERED()`.
   - No more posters than the month has days (handled by `.xwin` unless `whole`).
   - Do not special-case the key in generic code unless needed (§1.15). If it has a clickable centrepiece, add its selector to T:3363.
5. **CSS** (inside `<style>`, near the other themes' blocks):
   - `.th-x …` scene and cards, `.th-x .vday` sizes.
   - `html[data-theme=x] .hinfo …` words (and `html.mob[data-theme=x] .hinfo` if needed).
   - `html[data-theme=x] .leaf2 / .leaf2 i / .leaf2 b` (T:1447–1516 pattern).
   - `.cs` "Coming soon" tweaks if needed. TV focus tweaks (`html.tv .th-x .vday:focus`) if the card shape needs them. `@media (prefers-reduced-motion: reduce)` for any keyframes.
6. **Images and assets**: put them in `grifflix/img/` and reference them with `?v=1`; bump `v` on change.
7. **Music**: add `'x': (search, artist, title[, album])` to `PICKS` in `_grifflix/theme_music.py:15–37`, run `python3 _grifflix/theme_music.py` (it calls iTunes), and check that `grifflix/data/thememusic.json` has `x`. The Worker fetches the clip on first play.
8. **Theme film**: run `python3 _grifflix/theme_films.py` (needs `TMDB_TOKEN`) to add `x` to `grifflix/data/themefilms.json`. The admin page then puts it on M's 1st.
9. **Build**: run `python3 _grifflix/web/page.py`. This regenerates `grifflix/draft/index.html`, `grifflix/draft/version.json`, `grifflix/themes.json` (check that `x` is there; the Worker emails read it) and `grifflix/admin.html` (THEMELIST/THEMEFONTS). Run `python3 _grifflix/web/page.py --check`.
10. **Preview**:
    - Copy to `$SP/site3/grifflix/draft/` (plus new img/data), then serve on 8772.
    - Run `node $SP/th.js x`, `node $SP/allth.js $SP` (check no regressions or errors), `node $SP/tshots.js $SP x x` and `node $SP/perf3.js x` (TV fps).
    - Check desktop 1440×900, phone 390–430 wide, TV UA 1280×720, reduced motion (`ctx = newContext({reducedMotion:'reduce'})`), and the wipe into and out of the neighbour months.
    - Check `?still=month#month/<ym>`: it must set `window.__still`.
11. **Assign month M**:
    - If M ≥ 2026-12: John drags `x` onto M on the admin page Themes tab, which saves `gthemes-draft`, then "Update everything" publishes it.
    - Otherwise, use the THEMES order (code) or the admin page.
    - If the start of blank months is ever changed, change both T:2491 `BLANKFROM` and worker.js:1371 `'2026-12'`.
12. **Ship**: commit, push to the branch, PR, squash merge. The draft is live at `/grifflix/draft/`. It goes live only with John's go-ahead (admin "Update everything" or the `grifflix-golive.yml` dispatch). The Fire TV picks it up within about 10 minutes via `version.json`. The stills workflow redraws share cards after go-live or the next morning.
