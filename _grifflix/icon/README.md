# Grifflix's icon

A clapperboard on Song of the Day's June colours (`june-bg.png`: `_sotd/icons.py` ground(5)), GRIFFLIX in Bebas
Neue with "Mostly films, some TV" in Oswald, each sized to fill the board (chosen by John, 7 Nov 2026, after
comparing eight fonts at Home Screen size: condensed lettering stays tallest in the space).

- `final.html`: the icon (512 square, drawn larger by the render's scale); `final-maskable.html`: smaller in the
  middle, for Android's masks; `final-banner.html`: the Fire TV banner: on black, the board in the June gradient with white lettering and amber stripes (John chose it from bright-1..4 and june-1..3, 7 Nov 2026).
- `render.js FILE OUT SIZE [r] [H W]`: draws one (Playwright; r rounds the corners, for the favicon).

The sizes in use: `grifflix/icon-1024.png`, `icon-512.png`, `icon-192.png`, `apple-touch-icon.png` (180),
`favicon-96.png`, `icon-maskable-512.png`; the Fire TV app's `res/mipmap-*/ic_launcher.png` and
`res/drawable-*/banner.png`.
