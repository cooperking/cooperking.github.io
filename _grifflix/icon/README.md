# Grifflix's icon

**Now (8 Nov 2026): `futura.html`.** GRIFFLIX in Futura Bold, spaced as on the site, with the twelve monthly themes'
colours under it, on black: on a sideloaded Fire Stick the square icon is shown in a wider space, so its black melts
into the black around it (John's choice). Futura isn't on GitHub: copy `futura-bold-webfont.woff2` (John's, uploaded in
admin's Fonts card) into `fonts/` (git-ignored), then `node render-futura.js OUT PX [W H] [params]` (params: `fs` the
name's size, `cw`/`ch`/`cg` the colours' width, height and gap, `t` other words, e.g. `t=G` for the favicon).

The earlier clapperboard, kept for reference:

A clapperboard on Song of the Day's June colours (`june-bg.png`: `_sotd/icons.py` ground(5)), GRIFFLIX in Bebas
Neue with "Mostly films, some TV" in Oswald, each sized to fill the board (chosen by John, 7 Nov 2026, after
comparing eight fonts at Home Screen size: condensed lettering stays tallest in the space).

- `final.html`: the icon (512 square, drawn larger by the render's scale); `final-maskable.html`: smaller in the
  middle, for Android's masks; `final-banner.html`: the Fire TV banner: on black, the board in the June gradient with white lettering and amber stripes (John chose it from bright-1..4 and june-1..3, 7 Nov 2026).
- `render.js FILE OUT SIZE [r] [H W]`: draws one (Playwright; r rounds the corners, for the favicon).

The sizes in use: `grifflix/icon-1024.png`, `icon-512.png`, `icon-192.png`, `apple-touch-icon.png` (180),
`favicon-96.png`, `icon-maskable-512.png`; the Fire TV app's `res/mipmap-*/ic_launcher.png` and
`res/drawable-*/banner.png`.
