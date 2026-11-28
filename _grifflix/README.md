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

## John's Mac mini (2010, macOS 10.13 High Sierra, 4 GB)

The newest macOS it can have is High Sierra, which the current Jellyfin, cloudflared and Claude app don't run on.
`Install.command` sees that and installs the last builds that do (checked 7 Nov 2026, each binary's minimum macOS
read from the file): **Jellyfin 10.7.7** (2021; its app's own ffmpeg, min 10.10; app min 10.13), **Python 3.12.10**
from python.org (Intel part min 10.13) with `certifi` for its certificates, **cloudflared 2023.10.0** (the last
built with Go 1.20; 2024.1 on are Go 1.21, which needs 10.15). The gateway works the same with Jellyfin 10.7.7:
tested end to end here, playback included.

What the old Mac means:
- **No converting video.** A 2010 Core 2 Duo can't convert HD in real time, and its graphics can't help. Jellyfin
  only repackages (checked: `-codec:v:0 copy -codec:a:0 copy`), which costs little, so H.264 films (most MP4s and
  MKVs) play everywhere; HEVC/H.265 and 4K files won't play in most browsers. Plex had the same limit on this Mac.
- **cloudflared 2023.10.0 is old.** Cloudflare only promises a year's support for each version; it may one day be
  turned away. If so, the gateway can be reached another way (another always-on device running a current
  cloudflared, pointed at the Mac mini's 192.168.68.125:8097).
- **No security updates for macOS.** Nothing on the Mac listens to the internet: the tunnel connects out, and only the
  gateway (members only, signed links) is reachable through it; Jellyfin stays on 127.0.0.1.
- 4 GB: run Plex or Grifflix, not both scanning at once; quit Plex once Grifflix has the films.
- It's on Wi-Fi ("Cami", 192.168.68.125); give it a fixed address in the router.

## Moving to a new Mac mini

`Move.command` does it, run on the new Mac mini (Apple silicon, macOS 12 or later): it copies Grifflix's and Jellyfin's
things from the old one over SSH while the old one carries on, and gets everything else ready meanwhile (Jellyfin 12.4 and the
in-between Jellyfins, signed for this Mac; cloudflared; the tunnel's token, read from the old one's running tunnel). Only
then, when told, it stops Grifflix there, copies what changed (Jellyfin's databases and settings afresh), waits for the
G-Drive to be moved across, brings Jellyfin's database up to date in Jellyfin's own steps (10.8.13 → 10.9.11 → 10.10.7 →
12.4), starts Jellyfin, the gateway and the tunnel on the new one, and waits until macOS lets both Jellyfin and the gateway
read the films (a film's first byte through Jellyfin; `"films": true` on the gateway's `/health`). Nothing on the old Mac
mini is deleted. If anything fails after the switch, it stops the new one's for good (not started again by a restart), ejects
the G-Drive, asks for it to go back, starts the old one's Grifflix again, waits for the site to answer, and offers to restart
the old one if it doesn't. Each password is asked once: the new Mac's own SSH key is put on the old one with the first
connection. Run again, it carries on where it stopped; after a finished move it doesn't need the old one.
Rehearsed 28 Nov 2026 with Linux builds of the same Jellyfin versions: ids, an edited and locked description, the BFI
collection, the gateway's sign-in, the site's export and HLS playing all came through 12.4 unchanged; the gateway runs on
12.4 (its newer routes from 10.9 on). The script then had an independent review, and every point was fixed and rehearsed again.

From the box to running headless (John, 28 Nov 2026: "full instruction from out of the box to up and running in as few
clicks as possible ... Mac mini will run headless"; the old one is on Ethernet through the powerline adapter):

You need, for the first hour only: a TV or monitor with HDMI, a USB keyboard and mouse; and a cable for the G-Drive that fits
the new Mac mini (USB-C, or a USB-A to USB-C adapter). The old Mac mini stays on, on the powerline, as now.

1. **First start, at the TV.** New Mac mini to power, HDMI, keyboard and mouse; not the Ethernet yet (the old one has it). In
   macOS's setup: your country; Accessibility **Not Now**; your home **Wi-Fi**; Migration Assistant **Not Now**; Apple Account
   **Set Up Later** (a server needs none, and it keeps FileVault off); Agree; the account: full name John Griffiths, account
   name exactly **johngriffiths** (the same as the old one: Jellyfin keeps full paths), the same password as the old one;
   Location Services off; time zone London; analytics off; Screen Time later; Siri off; **FileVault off** if it asks (it
   has to be, for automatic login); updates: only download automatically.
2. **Three switches, a name and a login**, in System Settings: General → Sharing → **Screen Sharing**, **File Sharing** and
   **Remote Login** on, and at the bottom Local hostname → Edit → **grifflix** (so it can be found by name before it has the
   old one's address: the old one is "Johns-Mac-mini" too); Users & Groups → **Automatically log in as** John Griffiths (its
   password once). (FileVault on after all? Privacy & Security → FileVault → Turn Off, then this.) From here on it can all be
   done by Screen Sharing from John's Mac (Finder → Go → Connect to Server → `vnc://grifflix.local`, his password there): the
   TV, keyboard and mouse can go now (John, 28 Nov 2026: "Can I set it up via screen sharing?"). Only the G-Drive's move, in
   step 3, and the Ethernet cable, in step 4, are by hand.
3. **The move.** Terminal (⌘-Space, Terminal, Return), paste and Return:

   ```
   curl -fsLo m cooperking.github.io/grifflix/mac
   zsh m
   ```

   (`grifflix/mac` fetches `_grifflix/mac/Move.command` as it is on GitHub and runs it. The step-by-step guide for the day, written
   for someone who has never used a Mac, is the Claude Doc "Setting up the new Grifflix Mac mini".)

   Then only: this Mac's password; OK (the old Mac mini's address is filled in); the old Mac mini's password (**Allow** if
   macOS asks about the local network); the tunnel's token only if it asks; the copy (tens of minutes; Grifflix carries on
   meanwhile); **Switch now** (nobody watching); move the G-Drive from the old Mac mini to this one and OK (Time Machine
   asks: **Don't Use**); **Allow** each time macOS asks whether Terminal, jellyfin or Python may use files on a removable
   volume (it waits, and asks again, until they can); play a film on your phone; **Shut it down** (the old Mac mini: its
   password once more).
4. **The network swap.** The old Mac mini off (step 3 offers it). Move the powerline adapter's Ethernet cable to the new one. Deco app →
   More → Advanced → Address Reservation: delete the old Mac mini's 192.168.68.125, then + → the new Mac mini (on Ethernet)
   → 192.168.68.125 → Save. Restart the new Mac mini (Apple menu → Restart): it comes back by itself, signed in, with Jellyfin,
   the gateway and the tunnel.
5. **Headless.** Unplug the TV, keyboard and mouse, if they're still in. From then on: Screen Sharing from John's Mac (Finder →
   Go → Connect to Server → `vnc://192.168.68.125` or `vnc://grifflix.local`), SSH as before (same address, so the iPhone's route works unchanged), the G-Drive shared at
   `smb://192.168.68.125/G-Drive` as before.

The script's log: `~/Library/Logs/grifflix-move.log`. Keep the old Mac mini off but untouched for a week or two, as the
backup (to go back: G-Drive, Ethernet cable and the address reservation back to it, turn it on, and Claude restarts its
Grifflix). Jellyfin on the new Mac mini runs from a launch agent (`io.github.cooperking.grifflix-jellyfin`) with its folders
named, not the Jellyfin app: don't open the app there (it would start a second Jellyfin). The new Mac's video chip can convert
(VideoToolbox); whether the converter should now do full conversions there, not only repackaging, is John's call afterwards.

## Starting Jellyfin afresh

A Jellyfin set up before Grifflix (John's, a test from a couple of years ago: 10.8.13, which runs on High Sierra
too) needs its login, which the installer asks for. With the login forgotten, put its settings aside and start
again (nothing is deleted; the films aren't touched):

```
osascript -e 'quit app "Jellyfin"'; sleep 5
mv ~/Library/Application\ Support/jellyfin ~/Library/Application\ Support/jellyfin-old-test
open -g -a Jellyfin
```

then double-click `Install.command` again: Jellyfin starts as new, and Grifflix sets it up.

## Setting up the Mac mini

1. Copy the `_grifflix/mac` folder to the Mac mini; double-click `Install.command`. It installs Homebrew (if
   needed), Jellyfin and cloudflared, asks for the films, TV and home videos folders, sets Jellyfin up with no
   wizard, and starts the gateway and the tunnel with the Mac.
2. Cloudflare: Zero Trust → Networks → Tunnels → Create a tunnel (Cloudflared) → name it `grifflix`; copy the token
   into the installer when it asks. Under Public Hostname, point a hostname (on a domain in the Cloudflare account)
   at `http://localhost:8097`. Under that hostname's settings turn caching off (it's video).
3. Put that hostname in `grifflix/config.json` (`"gateway": "https://…"`): John's is `https://media.grifflix.co.uk` (7 Nov 2026).
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
- Allow HEVC to be copied, not converted, for devices that play it (Safari, iPhone, Fire TV), since the old Mac
  can't convert it.
- An `id` in the Worker's `/members/me`, so two members with the same name are kept apart.
