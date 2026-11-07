# Grifflix

John's films, TV and home videos for family and friends, from his Mac mini, replacing Plex. It looks and works like
Song of the Day: a **Movie of the Day** calendar John fills, the month strip, the colour taken from the day's poster,
My list (the hearts) and playlists. Members can make **calendars of their own** (shared with the family, or just for
them) and their own playlists. Nobody but John adds films.

Page: `grifflix/` → https://cooperking.github.io/grifflix/ (demo with made-up films: `?demo=1`).

## How it fits together

```
phone / browser / TV ──► cooperking.github.io/grifflix/  (the page: GitHub Pages)
        │
        └──► https://<tunnel hostname>  ──Cloudflare Tunnel──►  Mac mini
                                                                 ├─ Grifflix gateway  127.0.0.1:8097  (_grifflix/mac/grifflix-gateway.py)
                                                                 └─ Jellyfin          127.0.0.1:8096  (never reached from outside)
```

- **Sign-in is Song of the Day's.** The page reads the member token SOTD keeps (`sotd-member` in local storage: same
  site, so the same storage) and sends it to the gateway, which checks it with the Worker's `/members/me` (kept 10
  minutes). Members are told apart by the id `/members/me` gives, if it ever does, else by their name; John is the
  member the Worker says is the owner.
- **The gateway** is the page's whole API (`/api/me`, `home`, `items`, `item`, `search`, `play`, `stop`, `progress`,
  `calendars`, `calendar`, `lists`) and passes pictures (`/img/<id>/<kind>`) and video (`/v/<id>/...`) through from
  Jellyfin. Those links carry a signed key good for 12 hours (`g=`), since a video tag can't send a sign-in header;
  HLS playlists are rewritten so every piece carries it. Jellyfin's own key never leaves the Mac.
- **Jellyfin** does the hard parts: reading the folders, fetching posters, descriptions and cast (its own TMDB access,
  no key needed), and converting video a device can't play (on the Mac's own video chip, VideoToolbox). Its own
  apps and web pages are never shown to anyone.
- **Saved things** (Movie of the Day, members' calendars, playlists, where each member stopped) are in
  `~/Library/Application Support/Grifflix/data.json` on the Mac mini.
- **Playing:** an MP4 the browser can play as it is goes straight through (`direct`); anything else as HLS, which
  Jellyfin copies into pieces without converting when it can (an H.264 MKV), converting only when it must.
  Safari and iPhones play HLS themselves; other browsers through hls.js.

## Setting up the Mac mini

1. Copy the `_grifflix/mac` folder to the Mac mini; double-click `Install.command`. It installs Homebrew (if
   needed), Jellyfin and cloudflared, asks for the films, TV and home videos folders, sets Jellyfin up with no
   wizard, and starts the gateway and the tunnel with the Mac.
2. Cloudflare: Zero Trust → Networks → Tunnels → Create a tunnel (Cloudflared) → name it `grifflix`; copy the token
   into the installer when it asks. Under Public Hostname, point a hostname (on a domain in the Cloudflare account)
   at `http://localhost:8097`. Under that hostname's settings turn caching off (it's video).
3. Put that hostname in `grifflix/config.json` (`"gateway": "https://…"`).
4. Keep the Mac awake: System Settings → Energy → Prevent automatic sleeping.

Folder names Jellyfin understands best: `Films/Paddington (2014)/Paddington (2014).mkv`,
`TV/Detectorists/Season 01/Detectorists - S01E01.mkv`; home videos any way at all.

## Trying it without a Mac

`python3 _grifflix/mac/grifflix-gateway.py setup|serve` runs anywhere with `GRIFFLIX_DIR` pointing at a scratch
folder and a Jellyfin on 127.0.0.1:8096; `test_members` in its `settings.json` (`{"token": {"name": "John",
"owner": true}}`) stands in for the Worker. The page takes `?gateway=http://127.0.0.1:8097` (kept on that device;
`?gateway=` clears it). Tested so on 7 Nov 2026 against Jellyfin 12.4: set-up, libraries, posters, Movie of the Day,
calendars, playlists, HLS playback in Chromium through hls.js, progress and stopping.

## Still to do

- Fire TV: the SOTD TV app's way (`_tvapp`), pointed at `/grifflix/`, with the remote moving between days.
- iPhone/iPad Home Screen app; Apple TV (AirPlay from the iPhone first; its own app later).
- Movie of the Day's share pages and cards, like the songs'.
- An `id` in the Worker's `/members/me`, so two members with the same name are kept apart.
