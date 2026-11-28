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

`Move.command` does it, run on the new Mac mini (Apple silicon, macOS 12 or later) once macOS's own first questions are
answered (John, 28 Nov 2026: "as quick as possible (in terms of user time, not time where things are running). Anything you
can set up in a script, you should, including the new Mac mini set up. Make sure every Mac used is clearly defined"). The
step-by-step guide for the day, written for someone who has never used a Mac, is the Claude Doc "Setting up the new Grifflix
Mac mini". The Macs: **the old Mac mini** (2010, running Grifflix, with the G-Drive and the powerline adapter's network
cable); **the new Mac mini**; **John's work Mac**, which shows the new one's screen by Screen Sharing once the TV is
unplugged, about 20 minutes in (John, 28 Nov 2026: "I want it to be headless as soon as possible").

What John does, about 15 minutes in all: macOS's first questions (account `johngriffiths`, the old Mac mini's password);
in Terminal `curl -fsLo m cooperking.github.io/grifflix/mac` then `zsh m`; the password once, in a box; **Allow** on
macOS's questions; the TV, keyboard and mouse unplugged straight after (the new one carried to the old one first if it isn't
next to it); later, from the work Mac, **Switch now**; the G-Drive's cable and the network cable moved from the old Mac mini
to the new one. The script, in order:

1. Sets the new Mac mini up as a server: named `grifflix` (`vnc://grifflix.local`), Remote Login and File Sharing on (Screen
   Sharing asked for by hand, in System Settings: switched on by `launchctl` it refused connections, "Screen Sharing is not
   permitted", found on the day), signing itself in (`sysadminctl -autologin`; FileVault turned off if it was on), never asleep, on again after
   a power cut, Time Machine not offering the G-Drive. What won't switch on by itself it asks for by hand, there and then.
   It starts itself again if the Mac restarts before the end (a launch agent opening it in Terminal; the password kept in
   the keychain until the end).
2. Finds the old Mac mini (192.168.68.125, else `Johns-Mac-mini.local`), signs in with the same password (SSH_ASKPASS; a
   second box if the old one's differs) and puts the new Mac's own SSH key there. Copies the old one's SSH host keys, so the
   iPhone's SSH app and the work Mac see the same identity. Takes the tunnel's token from the old one's running tunnel.
   Installs Jellyfin 12.4 and asks macOS's removable-volume questions for Terminal, Python and jellyfin straight away, on a
   small disk image of its own, with Python and jellyfin started by launchd as they will be (where macOS doesn't ask for a
   disk image, it asks when the G-Drive comes). Copies Grifflix and Jellyfin while the old one carries on, and makes the
   rest ready (the in-between Jellyfins, signed; cloudflared). Before the copy, with nothing more needed at the screen, it
   says the TV can go and asks whether it's next to the old Mac mini: if not (**Move it now**), it switches itself off to
   be carried over, and carries on by itself when switched on again.
3. At **Switch now**: stops Grifflix on the old Mac mini, copies what changed (Jellyfin's databases and settings afresh),
   ejects the G-Drive and switches the old Mac mini off, so both its cables move in one go.
4. Brings Jellyfin's database up to date in Jellyfin's own steps (10.8.13 → 10.9.11 → 10.10.7 → 12.4), once the films can
   be read from here (an in-between Jellyfin that can't read them empties the collections); starts Jellyfin, the gateway
   and the tunnel; waits until both can read the films (a film's first byte through Jellyfin; `"films": true` on the
   gateway's `/health`) and the site answers.
5. Gives the new Mac mini the old one's address, 192.168.68.125, on its network cable (`networksetup -setmanual`, with the
   mask, router and DNS from its DHCP lease), which `grifflix-add.sh`, `convert-here.sh`, `shrink.sh`, `kermode.py` off the
   mini and the iPhone's route (`mac-warp`) all use: no Deco app step (the old Mac mini's reservation keeps the address from
   anyone else). Put back as it was if the network doesn't work with it, and then the last box gives the Deco steps instead.
   Shares the G-Drive. Takes its own launch agent and the kept passwords away.

Nothing on the old Mac mini is deleted: it's the backup. If anything fails after the switch, the new one's Grifflix is
stopped for good (not started again by a restart), the G-Drive ejected, and John asked to move the two cables back and
switch the old one on; then the old one's Grifflix is started again, and a restart offered if the site doesn't answer. Run
again, it carries on where it stopped; after a finished move it doesn't need the old one. The site's tunnel goes to
`http://localhost:8097` (read from cloudflared's log, 28 Nov 2026), so it follows whichever Mac runs the tunnel.

Rehearsed 28 Nov 2026 on Linux with real builds of the same Jellyfin versions and the real gateway, macOS's own commands
stood in for: ids, an edited and locked description, the BFI collection, the gateway's sign-in, the site's export and HLS
playing all came through 12.4 unchanged. Every scenario passed: the full move; the old Mac mini's password different; a
mistyped password; Move it now; macOS's questions answered late, or refused at first; the network cable forgotten;
settings that won't switch on by themselves; a failure after the switch with the undo and a retry; a second go after a
finished move; Not now; a broken download. The script had an independent review before that, and every point was fixed.

The script's log: `~/Library/Logs/grifflix-move.log`. Keep the old Mac mini off but untouched for a week or two, as the
backup (to go back: shut the new one down; the G-Drive and the network cable back to the old one; switch it on; Claude
restarts its Grifflix). Jellyfin on the new Mac mini runs from a launch agent (`io.github.cooperking.grifflix-jellyfin`)
with its folders named, not the Jellyfin app: don't open the app there (it would start a second Jellyfin). The new Mac's
video chip can convert (VideoToolbox); whether the converter should now do full conversions there, not only repackaging,
is John's call afterwards. The Mac's screen in the iPhone's browser (`mac-ssh` with "screen") needs a VNC password set
again on the new one.

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
