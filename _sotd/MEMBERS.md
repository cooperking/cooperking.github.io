# Members

Members signed in on a device hear John's own recording of each song instead of the 30-second preview, and see
his own video instead of YouTube's, wherever he has added them. Anyone else, and any day without a file, gets the
preview and YouTube as before. John can also give any day his own picture for its sharing link (the album cover until
he does).

## How it fits together

- **Files**: in a private Cloudflare R2 bucket, bound to the Worker (`sotd-playlists`) as `MEDIA`:
  `a/<track>.mp3` (the song), `v/<track>.mp4` (its video), `c/<track>` (a sharing picture). The site's repository is
  public, so none of them are in it.
- **Members page**: the Members tab of `songs/admin.html` (`songs/members-admin.html`), after the admin password: inviting,
  codes and removing people; and *This is me* on John's own member: the Worker keeps which member is John (KV `owner`)
  and tells only that member's devices (`/members/me` gives `owner: true`), whose Profile then has the Admin button
  (as does any device that has opened the admin page with its password). Each song's own recording, video and sharing picture are added on the Songs tab
  (`songs/songs-admin.html`), with the rest of that song.
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

## A day's own covers (members only)

On the Songs tab, under a song's *Members only*: *Add your cover* (then *Add another cover*), and *Change* on each.
John chooses a photo; it's made into an album cover as an invitation's is (the song's title, the artist in italic, its
number and day along the foot), and the day's card is drawn with it, shown before it's saved. *Who sees it*: all
members (the default) or the members he picks; a cover picked for a member wins over one for all members, and anyone
with neither sees the album cover. Members see their cover in place of the album's everywhere on the calendar (the
month, the lists, the day and its record while it plays), and their shares of the day go out as the Worker's
`/s/<track>/<id>` page with that card (they can pick *Album cover* instead in the share panel); everyone else's
shares, and the site's own day cards, keep the album cover. *Remove this cover* takes one away. KV `covers`
({ <track>: { list: [{ id, at, who, share }] } }), R2 `c/<track>/<id>` (the cover) and `sc/<track>/<id>` (the card);
`/members/me` gives each member the covers they see (`mycovers`).

## Signing in another device

A member's Profile has *Get a code*: the same one-use six-letter code John can make on the Members tab (30 minutes,
`/members/tvcode`, a few an hour at most), to type on a Fire TV (☰ → Sign in with a code) or in the Home Screen app
(Sign in at the top). *How to add the Fire TV app* links to `songs/tv/`, the steps for Downloader and the app.

## Passkeys

A member saves a passkey in their Profile (*Save a passkey*: Face ID, a fingerprint or the device's lock; iCloud
Keychain or their Google account keeps it and gives it to their other devices). On a new device, or after signing out,
*Sign in* → *Use a passkey* signs them in without John. The passkey belongs to `cooperking.github.io` (the draft and the
live calendar alike). The Worker keeps each one's public key on the member's record (`passkeys`: id, key, count, name,
when saved and last used; at most 10), checks each sign-in's signature with it (`/members/passkey`, signed in: list,
save, remove; `/members/passkey/signin`: anyone), and makes its challenges itself, signed with a key from `ADMIN_KEY`,
good for 5 minutes and once (KV `wa:<hash>`). Removing a passkey, or the member, stops it; the page then asks the
phone or computer to delete it (`signalAllAcceptedCredentials`, `signalUnknownCredential`, where the browser has them).
A device that joins from an invitation is offered one once, a moment later (*Save a passkey?*), when the member
has none yet; if something else is showing then, at its next visit; the notifications question waits for another visit.
Not on the Fire TV. The iPhone app offers them from build 21, the first with `webcredentials:cooperking.github.io` in
its Associated Domains (the site's `.well-known/apple-app-site-association` names the app for it). The members page
shows how many each member has. Tests: `pk_worker_t.mjs` (the Worker, with ES256 and RS256 authenticators made in
Node) `pk_page_t.py` and `pk_offer_t.py` (the draft, with Chromium's virtual authenticator).

## Email addresses, codes by email, and Google Play's testers

John gives a member's email address on the Members tab (or with *Invite*); it's on their record (`email`, lower case,
one member each). *Sign in* → their email address sends a 6-digit code to it from John's Google account
(`/members/code`; the same answer for any address), typed in the next box (`/members/code/signin`): once, within 10
minutes, 5 tries, 5 codes an address and 10 asks a place an hour; only its hash is kept (KV `mailcode:<id>`). The
Profile's passkeys part names the address. The emails go through John's Apps Script mailer (`_sotd/mailer`, a web app
run as him with MailApp: 100 a day; set up with clasp, its address and key in the secrets `MAILER_URL` and `MAILER_KEY`),
or else Gmail's API with his sign-in (`GMAIL_REFRESH_TOKEN`). Each email sent is noted for the Status tab (KV
`mailLog`); one that couldn't be sent counts there. *Send a test email* on the Members tab tries it.

Google Play's closed test takes its testers from its email list in Play Console, *Song of the Day testers* (John's
choice, 28 Oct 2026; until then the track used a Google Group of the same name, `sotd-testers`). Play's API can't read
or write email lists (it takes Google Groups only), so John adds the members: *Download the emails (CSV)*, one a line,
on the Members and Apps tabs, then the track's Testers › Email lists › the list › Upload CSV file › Save changes
(*Copy the emails*, comma-separated, is for its Add email addresses box). The Apps tab links straight to the track's
Testers and has the steps. For a member with an email address, the invitation's page gives Android Google Play's steps: join the test
(`play.google.com/apps/testing/io.github.cooperking.sotd`), get the app from Play, open the invitation; under them,
should Play not have them yet, the website's app instead. Tests: `mail_t.mjs` (the Worker), `gs_t.js` (the mailer),
`members_t.py` (the Members and Apps tabs), `signin_t.py` (the draft).

## Which app each device uses

Each visit, the calendar says which app it is (`X-App` on `/members/me`): `play:<build>` Google Play's Android app,
`apk:<build>` the website's, `ios:<build>` the iPhone app (its build from Capacitor's App plugin, kept on the phone),
`tv` the Fire TV app, `home` a Home Screen icon or installed web app, `web` a browser. The Worker keeps it on the
device's sign-in (`app`), with what the device runs on from its User-Agent (`os`: only the name, iPhone, Android,
Mac...). The Members tab lists each member's devices that way; the Apps tab lists who has each app, and flags a website
Android app older than `songs/android.json`'s build. Devices say once the calendar with this (from 28 Oct 2026) is
live; until then they show what they run on only.

When each device was last active: any signed-in call from it (opening the calendar or coming back to it, its inbox,
playlists, what's playing, notifications) writes the time to a KV key of its own, `seen:<sign-in hash>` (in the key's
metadata; at most once every 5 minutes a device; gone after 400 days unused). The Members and Apps tabs show it with the
time (today 21:55). Until 28 Oct 2026 it was in the members record, rewritten at most every 6 hours, since that one
value holds every member and a visit rewriting it could put back a copy read before a change on the Members tab.

## Members' own notifications

A member can send a notification to other members, as John does from the Members tab (John, 28 Oct 2026): from their
Profile's Inbox (*Send a notification*), or a song's, a month's or the calendar's *Share* › *Members*, which attaches
that link, its card as the picture and the song's cover. To everyone else, or the members they choose (`/members/me`
gives the other members' names: `others`); a title, words and a picture of their own (made small on the page, sent up
first: `/members/sendpic`, R2 `n/<id>.jpg`). `/members/send` checks it as John's are checked (`noteMsg`: a title, a link
on the site, pictures from the site, Spotify or the Worker) and puts it into their inboxes and on their phones where
notifications are on (`pushAll`), the notification's title "<name>: <title>", the inbox "From <name>". A song sent is as John sends one from the Members tab (John, 29 Oct 2026): its picture the site's card, John's own where
he has given the day one, their photo made into the album cover on the song's card, their photo as it is, or none; and
the words on the card (top line, title, second line, date) theirs to change. A card with other words or their photo is
drawn on the page with `songs/admin-cards.js` (fetched only then), and sent up as their own picture. A member can turn other members' notifications off (Profile › Account › Notifications from other members;
`/members/prefs { msgs }`, `nomsg` on their record): they're then left out of others' lists and sends, and the Members
tab says so. John's still reach them, from the Members tab or from him on the calendar (the member marked *This is
me*). The Profile's Account tab was called Sign-in until 29 Oct 2026. Everything but the daily song says who it's from (John, 29 Oct 2026): John's notes from the Members tab too, with
the name of the member marked as him (*This is me*), else John. 20 a day each,
and 20 pictures (KV `sent:<id>:<date>`, `sentpic:<id>:<date>`). The admin page's Status tab lists each: "a member's note to
Ann and Dad · by Mum". Not on the TV. Tests: `send_t.mjs` (the Worker), `send_page_t.py` (the draft), `status_page_t.py`.

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

Songs tab of the admin page → *Upload a folder of songs…* → the folder with the MP3s and MP4s → check each match (fix any with its menu) →
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

## TV-friendly copies of John's videos

Many uploaded videos are AV1 or VP9 (YouTube's formats), some 4K, and glitch on a Fire TV Stick. `.github/workflows/video-tv.yml`
(`_sotd/video_tv.py`) makes an H.264 copy (1080p at most, about 4–5 Mbps, faststart) of each that needs one at `h/<track>.mp4`
in the R2 bucket; the Worker plays the copy wherever there is one. Each copy is checked before it goes up: H.264, with sound if the
original has sound, and as long as it within 0.5 s; one that isn't stays off, and the original plays. It runs after each video upload (the Worker starts it for
that video), once a day, and by hand (all of them). It never changes or deletes an original (see *Deleting the originals*).

It needs four repository secrets: `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` (an R2 API token: Cloudflare dashboard,
R2, Manage R2 API Tokens, Create API token, Object Read & Write, applied to the bucket), `R2_BUCKET` (the bucket's name), and
`CLOUDFLARE_ACCOUNT_ID` (already there).

## Lighter copies of John's songs

The uploaded MP3s are VBR at about 255 kbps, 48 kHz (`_sotd/data/audio-report.json`, from `audio-report.yml`, by hand).
`.github/workflows/audio-light.yml` (`_sotd/audio_light.py`) makes an AAC copy at 192 kbps of each at `l/<track>.m4a`, about
a quarter smaller, so songs stream and keep offline in less; the Worker plays the copy wherever there is one (as
`audio/mp4`), and drops it when a new MP3 is uploaded or the MP3 is deleted. Each copy is checked first: its length within
0.2 s of the MP3's, and smaller than it (a song already under 192 kbps keeps its MP3 only). It runs after each MP3 upload
(the Worker starts it for that song), once a day, and by hand (all of them), with the same secrets as the TV copies. It
never changes or deletes an MP3 (see *Deleting the originals*).

## Converting before uploading

`_sotd/convert/sotd_convert.py FOLDER` (on the Mac, with ffmpeg) writes `FOLDER/converted/`: songs as AAC 192 kbps `.m4a`,
videos that need it as the TV's H.264 MP4, with the same settings and checks as the copies made after an upload; files
already fine are copied as they are. Uploaded on the Songs tab, a `.m4a` goes straight to `l/<track>.m4a` (the song as it's
played, replacing any MP3 of it), and an H.264 video needs no TV copy, so nothing is made and nothing needs deleting.

## Deleting the originals

Once a song or video has its copy, the Worker plays the copy and the original isn't played. `.github/workflows/media-originals.yml`
(`_sotd/media_originals.py`), by hand only, deletes those originals to save room in the bucket (R2 includes 10 GB a month).
Run with the box empty it only says what it would delete; with `delete` typed in the box it deletes them. What it did shows
at the top of the run's page. An original goes only if its copy is newer than it, opens as AAC (a song) or H.264 with sound
(a video), and is as long as it (0.2 s for a song, 0.5 s for a video); one without a copy, or uploaded again during the run,
is kept. The Worker's catalog keeps each deleted original's size and fingerprint, so the calendar plays the copy, the Songs
tab shows the file as there, and a folder upload still knows it's already up. Uploading a song or video again puts a new
original up and makes a new copy; run this again afterwards. A deleted original can't be got back from the bucket.
