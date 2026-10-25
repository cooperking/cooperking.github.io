# Members

Members signed in on a device hear John's own recording of each song instead of the 30-second preview, and see
his own video instead of YouTube's, wherever he has added them. Anyone else, and any day without a file, gets the
preview and YouTube as before. John can also give any day his own picture for its sharing link (the album cover until
he does).

## How it fits together

- **Files**: in a private Cloudflare R2 bucket, bound to the Worker (`sotd-playlists`) as `MEDIA`:
  `a/<track>.mp3` (the song), `v/<track>.mp4` (its video), `c/<track>` (a sharing picture). The site's repository is
  public, so none of them are in it.
- **Members page**: the Members tab of `songs/admin.html` (the part page is `songs/members-admin.html`, which only shows inside it), after the admin password.
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
- The Home Screen app keeps its own storage (an iPhone copies only cookies from Safari), so an invite opened in Safari
  doesn't sign it in. *Sign in* (the last button in the bar, everywhere but the TV) takes the members page's *Code*
  (the same one-use code as a TV's) or the invite link pasted in. Signed in, it's a small filled person: tapping it (or the
  *Signed in as* chip) opens their *Profile*: their name, their saved playlists (tap one to open it as My playlist) and
  *Sign out* (two taps; that device's downloads go too). A white *Signed in as <name>* chip under
  the month's count shows it on the front page too.
- Videos aren't kept (yet).

## Invitations

*Invite* (or *New link*) on the Members tab opens the invitation, shared like a day's song: only a link is sent, and
its preview (WhatsApp, iMessage...) is a card drawn on the members page exactly as a day's card is drawn
(`share_pages.py` card()), with "<first name> is invited" as the song, AN INVITATION as the artist, today's date, and
as the cover the picture John chose, made into an album cover (the calendar's name across the top, "<name> is invited" at the foot; *Choose picture…*, kept on that device for next time) or else the latest song's.
The Worker keeps the card and its page (`/i/<id>`, KV `invcard:` and `invpage:`, for as long as the invitation lasts);
the page sends a person on to their invitation on the calendar. *Share…* (on a phone), Messages, WhatsApp, Email and
Copy link all send that link.

## Saved playlists

A member can save My playlist (*Save this playlist* in it, with a name) and keep several. They're kept by the Worker
with their sign-in (`/members/playlists`, KV key `mpl:<member id>`), so every device they're signed in on has them.
Tapping one opens it as My playlist; changes to an open saved playlist save themselves (a device that was offline sends
its changes when it's back; one that wasn't takes the newer copy). *Close playlist* puts it away (still saved) for an
empty one; *Delete* removes it from the saved ones (its songs stay on that device). Up to 50 playlists of 500 songs.
Removing a member removes their saved playlists. Visitors keep the single My playlist on their device, as before.

## Setting it up (once)

1. Cloudflare dashboard → R2: turn R2 on (Cloudflare asks for a payment method; the first 10 GB a month are free,
   then about $0.015 per GB a month, and downloads are free). Create a bucket, e.g. `sotd-members`.
2. Workers → `sotd-playlists` → Settings → Bindings → Add → R2 bucket: variable name `MEDIA`, the bucket above.
3. The Worker's code goes in place by itself: `.github/workflows/worker.yml` runs `_sotd/worker_deploy.py`
   whenever `_sotd/worker/worker.js` changes (or by hand). It uploads only the code, keeping the bindings and
   secrets, checks the Worker still answers, and puts the previous version back if it doesn't. It needs two
   repository secrets: `CLOUDFLARE_API_TOKEN` (a Cloudflare token with Account > Workers Scripts > Edit) and
   `CLOUDFLARE_ACCOUNT_ID`.
4. Draft calendar first, then live: the members code went live on `songs/` on 25 Oct 2026 (with `songs/sw.js`).

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
