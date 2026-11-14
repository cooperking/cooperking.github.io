---
name: grifflix-theme
description: Build a new monthly theme for Grifflix (the family film site's full-screen, film-inspired home page scene), or rework an existing one. Use whenever John asks for next month's theme, a theme after a particular film, changes to a theme's look, lettering, colour, music or movement, or says a theme is slow, unclear or wrong on a device. Holds everything learned from the themes made so far, a map of every file a theme touches, and preview and performance tools.
---

# Building a Grifflix theme

Each month Grifflix's home page is a theme: a whole-screen scene after one film's look, which you move through a day at a
time, each day a film. John wants a new one each month. The knowledge from every theme so far is in this folder; use it, and
add to it, so nothing is learned twice.

**Read before starting** (they're long; read them fully once per theme, not from memory):
- `knowledge.md`: John's taste, the hard requirements, **67+ mistakes and the rule each set**, notes on every theme, the
  research method. The mistakes section is the most valuable thing here.
- `anatomy.md`: every file and code section a theme touches, the `THEMEFX` interface, the build, and a 12-step checklist.
  Line numbers drift; find things by name.

## The steps

### 1. The brief
Ask John only what you can't decide: the film (if not given) and the month. Check `grifflix/themes.json` and the admin page's
months: one film, one theme; no similar style or colour next to the neighbouring months (Tron and Oz were moved apart for
this). Months from December 2026 show a blank holding theme until a theme is put on them in admin.

### 2. Research (before any design)
John: "do proper research into colours and fonts of the inspired film" and "Let me know true font and then we work from
there". For the film:
- **Lettering**: the real title or credits face, from Fonts In Use, Art of the Title, Typeroom, poster scans (Wikimedia
  Commons, IMP Awards). Say how sure you are; say when it's custom lettering. Name the commercial font if it is one (John buys
  and uploads it); pick the nearest free Google font as the fallback.
- **Colour**: one bold flat ground colour from the film's own frames or design, not just its poster (the Shining's poster
  yellow was wrong). Not black unless the film is black and white. Never purple as a main colour. Ink `#000` or `#fff`,
  checked for contrast.
- **The one image everyone knows** from the film, which the theme must be built round so it's recognisable at once
  (Shining's carpet and corridor, Strangelove's War Room, Méliès's moon). If it's iconic, use real reference material, not a
  drawing (drawn sharks and line drawings were rejected).
- **Music**: the piece the film is remembered by, and a release whose 30-second preview is the famous part.
Write the findings, with sources and confidence, into the theme's `//` comment and later into knowledge.md.

### 3. Concepts as pictures: John chooses
Make two or three distinct ideas for how the month's days move through the scene (the movement must be creative, real
rather than faked, and driven by the swipe, not a played animation). Build each roughly enough to screenshot, run
`preview.js` on each, and **send John the pictures** (computer, iPhone, TV). He decides from pictures, never descriptions.

### 4. Build the chosen one
Follow anatomy.md's checklist (registry entry, fonts, `THEMEFX.<key>`, CSS, images, music, theme film). Edit only
`_grifflix/web/template.html`, then `python3 _grifflix/web/page.py`. Keep to the hard requirements in knowledge.md §2,
notably:
- obvious that each item is a calendar day (the day's calendar-page icon in the theme's style); never more posters than the
  month has days; neighbouring months' days visible as you approach; Coming soon fitting its card;
- the day's words at a fixed place, never under the bar or posters, titles clamped to fit;
- a properly designed phone layout (780×890 stage), a TV layout like the computer's (1600×900 at .8);
- crossing into the next month the instant you leave the 1st or last day, with the wipe in the next month's colours;
- the caption naming the film, on its own panel, away from the day's title;
- `CALM` (reduced motion) respected; nothing that flashes the whole screen.

### 5. Performance (every theme, before John sees it)
The Fire TV is slow, and John feels it on his iPhone too. Run `node _grifflix/themetools/perf.js <key> spiral` (spiral for
comparison); the theme must be within budget. Rules (each from a real problem):
- **Move things only with `transform` and `opacity`.** Never animate `left`, `top`, `width`, `height` or `margin` (The
  Table did, and laid the page out every frame).
- **Nothing animates constantly in a big SVG or over a large area.** A CSS animation on one part of an SVG redraws the whole
  SVG every frame (The Table's Big Board). Draw still parts once (their own SVG/canvas, `will-change: transform`), put the
  moving parts on a small layer of their own, and tick slow decorative motion with a timer a few times a second, paused when
  `document.hidden`, off under `CALM`.
- **Complex scenes on one canvas**, not hundreds of DOM shapes remade each frame; bake static layers; stamp repeated
  ornaments from a pre-drawn sprite; on the TV use dpr 1 and fewer elements; keep full resolution where it shows.
- **Rest when covered** (`COVERED()`), and stop every loop when its elements are gone.
- `filter` (blur, grayscale) on many moving elements is costly; keep such elements few, or the filter fixed.
- Never read the page's layout (`getBoundingClientRect`, `offsetWidth`, `clientWidth`) in code that runs on every step or frame,
  and never restart a CSS animation with `void el.offsetWidth`: use `el.animate()`. Each read lays the page out again (Kane's globe).
- Canvas loops: draw at dpr 1 for soft things (snow), cap at 30 frames a second (20 on a TV), skip while `document.hidden`,
  pre-draw sprites at the sizes used.

### 6. Check every screen
`node _grifflix/themetools/preview.js <key>` screenshots computer, iPhone, Fire TV and reduced motion, as opened and two
days on, and reports page errors. Look at every picture. Also check: tapping the day's film opens it; the wipe into and out
of the neighbouring months; signed in and signed out; narrow phones (320 px); the music starting and stopping (knowledge.md
§2, Music); `?still=month#month/<yyyy-mm>` sets `window.__still` (share cards).

### 7. Show John, as a draft
Commit, push to the working branch, PR, squash merge: it appears on the **draft** (`/grifflix/draft/`), never live. Send John
the screenshots. Nothing goes live until he says (admin "Update everything"). He puts the theme on its month in admin
(Themes), which also puts the theme's film on the 1st.

### 8. Rework from his feedback
Change only what he named; ask or mock up when his words are unclear (several misreadings are in knowledge.md §3).
Screenshots for every change.

### 9. Keep the knowledge (required, every time)
When the theme is settled, update this folder in the same PR:
- `knowledge.md` §4: the new theme's notes (film, idea, colour and font with sources and confidence, music, John's feedback
  in his words with dates);
- `knowledge.md` §3: each new mistake, as what went wrong → the rule now;
- `knowledge.md` §2 or this file: any new general requirement;
- `anatomy.md`: anything structural that changed;
- `perf.js`'s baseline in knowledge.md §2 (Performance) if themes were reworked.
This is how the next month's theme starts ahead.

## Tools
- `_grifflix/themetools/preview.js <key>... [--out DIR] [--day YYYY-MM-DD]`: screenshots on every screen; page errors.
- `_grifflix/themetools/perf.js <key>...`: Fire TV (6× slowed) work idle and moving, against the budget.
- Both serve the repo themselves and use the built draft: run `python3 _grifflix/web/page.py` first. Fonts John uploaded
  don't load from localhost (fallbacks show instead); the days come from `grifflix/data/days.json`.
- In the page: `?view=themes&theme=<key>` any theme; `&tv=1`, `&big=1`; admin's theme explorer previews with sound.
