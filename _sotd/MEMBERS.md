# Members: John's family

Family members signed in on a device hear John's own recording of each song instead of the 30-second preview, and see
his own video instead of YouTube's, wherever he has added them. Anyone else, and any day without a file, gets the
preview and YouTube as before. John can also give any day his own picture for its sharing link (the album cover until
he does).

## How it fits together

- **Files**: in a private Cloudflare R2 bucket, bound to the Worker (`sotd-playlists`) as `MEDIA`:
  `a/<track>.mp3` (the song), `v/<track>.mp4` (its video), `c/<track>` (a sharing picture). The site's repository is
  public, so none of them are in it.
- **Members page**: `songs/members-admin.html`, with the admin password (the same one as the facts sign-off page).
  - Invite someone: it gives a link to the calendar ending `#join=…`; John sends it to them.
  - Opening the link signs that device in, up to 5 devices within 30 days. *New link* makes a fresh one; *Remove* signs
    all their devices out.
  - Add files one by one, or choose a folder: each file is matched to its day by its name, best as
    `Artist - Title.mp3`. A name ending in the Spotify track id (`…<22 characters>.mp3`) matches exactly.
  - *Picture* sets a day's sharing picture; song-tags.yml then remakes that day's card (`share_pages.py`).
- **The calendar** (`template.html`):
  - The device keeps its member token (`sotd-member` in local storage) and asks the Worker's `/members/me` for links to
    the files, signed for 12 hours.
  - The whole song plays through the same audio player, with all its facts as captions (a preview gets two).
  - The own video plays in the browser's own player in the video's box, worked through the same calls as YouTube's
    player (`ownPlayer`), so full screen, the facts, Play all and the remote work the same.
  - Days without a file fall back per song: no own MP3 means the preview; no own MP4 means YouTube.
- **The Worker** (`worker/worker.js`, the members section) keeps only hashes of invite codes and tokens. It serves the
  files in ranges (phones need that), and refuses a link that has been changed or has run out.

## Offline (songs only)

A member can keep John's recordings on a device and play them with no connection:
- *Download* (the arrow) on a day, next to a month's Share, or next to My playlist. A tick once it's on the device; a dot
  on its day in the calendar. Tap the tick to remove it; *Remove downloads* under My playlist removes them all.
- With no connection the calendar still opens (the last copy of the page, its data and the covers); days without a
  download are greyed, and one tapped says it needs a connection.
- How: a service worker (`songs/sw.js`, `songs/draft/sw.js`) keeps the page, its data and the covers, and the songs
  the page puts in its store; it plays them in ranges (an iPhone won't otherwise). The Worker's file links allow the
  page to fetch them (CORS).
- Downloads are cleared if the member is removed. Best from the Home Screen icon on an iPhone: Safari can clear a
  website's stored data once it goes unvisited for a week or so; the Home Screen app is kept apart from that.
- Videos aren't kept (yet).

## Setting it up (once)

1. Cloudflare dashboard → R2: turn R2 on (Cloudflare asks for a payment method; the first 10 GB a month are free,
   then about $0.015 per GB a month, and downloads are free). Create a bucket, e.g. `sotd-members`.
2. Workers → `sotd-playlists` → Settings → Bindings → Add → R2 bucket: variable name `MEDIA`, the bucket above.
3. Deploy the Worker with this repository's `_sotd/worker/worker.js`.
4. Draft calendar first: the members code is on `songs/draft/` until John signs it off.

## Adding the real files later

Members page → *Choose folder…* → the folder with the MP3s and MP4s → check each match (fix any with its menu) →
*Upload*. Files up to any size go up in 50 MB parts; one that's already there is replaced. Videos should be H.264 MP4
(what phones, computers and the Fire TV all play).

## Tested

With fake files (`_sotd` doesn't keep them), against the Worker run locally with its storage in memory:
- the Worker's members part (26 checks);
- the members page: invite, folder matching and upload;
- the draft calendar:
  - as a member: whole song, own video, full screen, and falling back where a file is missing;
  - as a visitor;
  - with a wrong token;
  - once removed;
  - offline: downloading a day and a month, the calendar and a downloaded song (seeking too) with no connection, a day
    not downloaded, downloads gone once removed.

Not yet on a real iPhone or Fire TV.
