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

*Add Member* (or *New link*) on the Members tab opens the invitation, shared like a day's song: only a link is sent, and
its preview (WhatsApp, iMessage...) is a card drawn on the members page exactly as a day's card is drawn
(`share_pages.py` card()), with "<first name> is invited" as the song, AN INVITATION as the artist, today's date, and
as the cover the picture John chose, made into an album cover (the calendar's name across the top, "<name> is invited" at the foot; *Choose picture…*, kept on that device for next time) or else the latest song's.
The Worker keeps the card and its page (`/i/<id>`, KV `invcard:` and `invpage:`, for as long as the invitation lasts);
the page sends a person on to their invitation on the calendar. *Share…* (on a phone), Messages, WhatsApp, Email and
Copy link all send that link.

An invitation works on up to 5 devices within 30 days (`INVITE_USES`, `INVITE_DAYS`; the 12 hours, `LINK_HOURS`, are
the links to John's MP3s and MP4s, not invitations). Once it has run out (30 Oct 2026), opening it on the calendar says
so in a pop-up ("Your invitation has run out", when, or that it's been used on 5 devices) with *Ask John for a new one*:
`POST /members/ask { code }` puts "<name> asks for a new invitation" into John's inbox and onto his phones (the member
marked *This is me*), at most once in 12 hours (`ASK_HOURS`), and notes when on their record (`invite.asked`). The
Members tab shows "Invitation ran out" or "Asks for a new invitation" on a member not signed in anywhere, and on each
member "invitation: N more devices until …", "invitation ran out on …" or "invitation used on all its devices";
*New link* sends a new one (and clears the asking). Tests: `invite_gone_t.mjs`, `invite_gone_page_t.py`.

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

## Who gets John's files

Each member's card on the Members tab has *Can see* MP3s (John's recordings) and MP4s (his videos), each ticked or
not (29 Oct 2026; `see { a, v }` on their record, admin op `see`). The Invite box has the same Can see and Seen by,
set as they're invited (`invite` with `see` and `apart`). Someone invited with neither ticked starts with neither;
members from before keep both. `/members/me` signs links only for what they may have and says which (`see`), and the
calendar then takes their downloads of the rest off that device (`dlPrune`, only on the Worker's word). A link already
given lasts its 12 hours (`LINK_HOURS`). Up to 200 members (40 until then). The Alexa skill is separate: it plays John's
MP3s and videos to whoever has the skill. Tests: `see_t.mjs` (the Worker), `prune_t.py` (the draft), `members_t.py`.

## A new member from John's contacts

The Members tab's Invite box has *From Contacts…* (29 Oct 2026): Apple's contact picker in the iPhone app (its own
plugin, `SotdContacts`: one person from build 22, several from build 24; the app sees only the people chosen), Chrome's
Contact Picker on Android (several), and anywhere else *From a contact card…*: .vcf files chosen or dropped on the
Invite box (Contacts on a Mac makes one when people are dragged out of it; Google Contacts: Export › vCard). One person
fills in the Invite box's name and email (more than one email: it asks which). Several people (same day): a card
for each under the Invite box, in place of its own fields, each with its name, email (their other addresses a tap
away), Can see and Seen by to change; *Add Member* adds one, *Add all* adds them in turn (one at a time: each changes
the members' list), and each invitation is then sent from its own card (adding sends nothing). Anyone whose email is already a member's is left out (and
named), as is anyone already in the list; at most 50 waiting at once. Tests: `contacts_t.py`, `contacts_many_t.py`.

## Email addresses, codes by email, and Google Play's testers

John gives a member's email address on the Members tab (or with *Add Member*); it's on their record (`email`, lower case,
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

## Members' own messages

A member can send a message to other members, as John does from the Members tab (John, 28 Oct 2026; called a
notification until 30 Oct 2026): from their Profile's Inbox (*Send a message*), or a song's, a month's or the calendar's *Share* › *Members*, which attaches
that link, its card as the picture and the song's cover. To everyone else, or the members they choose (`/members/me`
gives the other members' names: `others`); a title, words and a picture of their own (made small on the page, sent up
first: `/members/sendpic`, R2 `n/<id>.jpg`). `/members/send` checks it as John's are checked (`noteMsg`: a title, a link
on the site, pictures from the site, Spotify or the Worker) and puts it into their inboxes and on their phones where
notifications are on (`pushAll`), the notification's title "<name>: <title>", the inbox "From <name>". A song sent is as John sends one from the Members tab (John, 29 Oct 2026): its picture the site's card, John's own where
he has given the day one, their photo made into the album cover on the song's card, their photo as it is, or none; and
the words on the card (top line, title, second line, date) theirs to change. A card with other words or their photo is
drawn on the page with `songs/admin-cards.js` (fetched only then), and sent up as their own picture. A member can turn other members' notifications off (Profile › Account › Notifications from other members;
`/members/prefs { msgs }`, `nomsg` on their record): they're then left out of others' lists and sends, and the Members
tab says so. With them on, they can untick particular members whose they don't want (`mute` on their record): left out
of those members' lists and sends. John can keep two members apart on the Members tab (*Seen by … Change*; `apart` on
both records, admin op `apart`): neither sees the other in their lists (to send to, or to choose whose they take) and
neither can send to the other; a send by id to a member kept apart says only "Choose who it goes to." Removing a member
takes them off others' `apart` and `mute`. John's still reach them, from the Members tab or from him on the calendar (the member marked *This is
me*). The Profile's Account tab was called Sign-in until 29 Oct 2026. Everything but the daily song says who it's from (John, 29 Oct 2026): John's notes from the Members tab too, with
the name of the member marked as him (*This is me*), else John. 20 a day each,
and 20 pictures (KV `sent:<id>:<date>`, `sentpic:<id>:<date>`). The admin page's Status tab lists each: "a member's note to
Ann and Dad · by Mum". Not on the TV. Tests: `send_t.mjs` (the Worker), `send_page_t.py` (the draft), `status_page_t.py`.

Only members who have joined (signed in at least once: `joined` on their record from 29 Oct 2026, the time of their
first sign-in; before that a device, a passkey or their invitation used) are offered to send to (`others`) or to choose
whose to take (`seen`) (John, 29 Oct 2026). A member's send to everyone goes only to these: members who have joined,
not kept apart from them, and taking theirs (30 Oct 2026); someone who joins later doesn't get it. John's to everyone from
the Members tab still goes into every member's inbox. Tests: `joined_t.mjs`, `bell_t.mjs`.

A message from someone (a member's, or John's from the Members tab) says in the inbox who it went to as well as who
from ("From Dad · to you and Ann", "to everyone"): `fi` the sender's id and `to` ('all', or the others chosen as
`{ i, n }`, leaving out anyone kept apart from that inbox's member), from 30 Oct 2026. Each has *Reply*, in the list and
in the message opened: the send dialog as "A reply", its title "Re: <title>", to the same people (the sender and the
others, or everyone), as far as the member can send to them, changeable with Everyone or Choose. The Inbox and Sent
show 5 at a time, compact (two lines of words at most, the picture small beside them; the whole message opens from
one with no link), with *Show more* for 5 more. Tests: `reply_t.mjs` (the Worker), `reply_page_t.py` (the draft).

Each member sees their own sent notifications on their Profile's *Sent* tab (John, 29 Oct 2026; draft first): newest
first, each with who they sent it to as they chose them ("To Dad and Ann", "To everyone"; not who took it) and when;
tapped, it opens its link, as in the inbox. KV `outbox:<id>`, the newest 60, written by `/members/send` (and, for
John's from the Members tab, into the member marked *This is me*); `GET /members/sent` gives them. None were kept before
then, so the first read makes it from the Status tab's log (`pushLog`) and the inboxes the messages went into. The tab
shows for members with someone to send to, or who have sent some. A message with no link of its own (just the
calendar) tapped in the Inbox or Sent opens in a pop-up over the Profile (all its words, its whole picture, a GIF
moving), and closing it marks it read (John, 29 Oct 2026); a day's opens that day on the page it's tapped on. The
Inbox's and Sent's pictures show whole, not cropped.
Tests: `sent_t.mjs` (the Worker), `sent_page_t.py`, `inbox_link_t.py` (the draft).

A GIF chosen as a message's picture (John's on the Members tab, a member's in Send; 29 Oct 2026, "yes, in the inbox
please") goes up as it is with the still JPEG made on the page (`/members/sendpic` or op `pushpic` `{ pic, gif }`: R2
`n/<id>.jpg` and `n/<id>.gif`, the GIF 5 MB at most and checked to be one; one picture for the day's count). The
notification carries both (`image` the still, `gif` the GIF, only one of the Worker's own `n/*.gif`): the inbox and
Sent show the GIF, moving; the notification itself shows the still (Android, computers) or no picture (iPhone). A GIF
over 5 MB goes as the still. Tests: `gif_t.mjs` (the Worker), `gif_page_t.py` (the draft and the Members tab).

Reactions (John, 30 Oct 2026; draft first): anyone a message from someone went to can react to it with one emoji
(👍 ❤️ 😂 😮 😢 🙏 🎉 🔥), from the face button beside *Reply* in the Inbox or in the message opened; the same emoji again
takes theirs away, another changes it. Each message shows a chip per emoji with how many ("❤️ 2"); tapping a chip says
who ("❤️ Dad and you"). The sender sees them on Sent. Anyone kept apart from the person looking is left out of the
count and the names. KV `react:<message id>` `{ <member id>: { e, at } }`, kept 400 days; `POST /members/react
{ id, e }` (`e` '' takes it away; only for a message in their own inbox from someone, not the day's song); the inbox
and Sent give each message's `rx` `[{ e, n, me }]`. The day's song takes reactions too (John, 30 Oct 2026: "as the song message of the day technically doesn't come from me,
can it still get emojis?"): marked `s` in each inbox (one sent before then: its day's link and "… Song of the Day"
words), everyone it went to sees them, and they go on John's bell (the member marked *This is me*) as "Dad reacted ❤️ to
the Song of the Day", tapped: that day. A message's reactions take a line of their own above its actions; with none, the
face sits beside *Reply*. Tests: `react_t.mjs` (the Worker), `react_page_t.py` (the draft).

The bell (John, 30 Oct 2026; draft first): reactions to a member's own messages. A bell with the count not yet seen
shows on the Profile button ("Signed in as …"), and a bell in the Profile's top corner (with that count) opens the list,
newest first: "🎉 Dad reacted to your message", the message's title and when, new ones marked. Opening it marks them all
seen; one tapped opens that message from Sent. KV `bell:<member>` `{ seen, items: [{ id, k, e, at, t }] }`, one item per
message and member reacting (a change replaces theirs and counts as new, taking it away removes it), the newest 50;
written by `/members/react` for the message's sender (`fi`). `GET /members/bell` gives `{ items: [{ id, n, e, at, t }],
unseen, seen }`, leaving out anyone kept apart; `POST /members/bell { seen: true }`; `/members/me` gives `bell`, the count
not yet seen. A reaction also goes as a notification to the sender's devices with notifications on (not when it's
taken away, nor when they've turned off other members' messages or muted the reactor; John's always go): "Dad reacted 🎉
to your message", the message's title as its words, opening the calendar at `#bell` (the Profile and its bell). It isn't
put in the inbox, so the app's icon keeps its count; one per message and reactor on a device (tag `sotd-rx-<id>-<member>`);
at most 40 a day from one member (`rxpush:<member>:<day>`). Tests: `bell_t.mjs` (the Worker), `bell_page_t.py` (the draft).

## Members' own Spotify playlists

A member can add their own Spotify playlists (John, 30 Oct 2026: "users to have the ability to add their own spotify
playlists which run exactly the same way as mine does"), up to 5, each a calendar like John's (a song on each day it
was added, the same rule for missed days) or a plain playlist (its songs, newest first). A calendar is called
"<their name>’s Song of the Day" on the calendar (John, 30 Oct 2026), with its title above the month; a plain playlist
goes by its title. Its title is Spotify's name for it until its owner changes it (John, 31 Oct 2026: "default to the
spotify name when imported but let them change it"; Profile › Playlists › *Title*, *Use Spotify's* to go back;
`title` on its record, `n` Spotify's); its YouTube copy is renamed with it. Phases 1
(the Worker, the draft calendar, the Members tab) and 2 (their songs' videos, moods, years and YouTube copies) are in;
still to come: facts, written for new songs and on request for older ones, checked and signed off by their owner.

- **Reading them.** Spotify lets John's app read the songs only of playlists his account owns or collaborates on
  (Spotify's February 2026 rules for Development Mode apps; for anyone else's, even public, only its name). So the
  member turns on collaboration and pastes the invite link (Profile › Playlists › *Add a Spotify playlist*, then
  *Calendar* or *Playlist*); John gets a message in his inbox, opens the link from the Members tab's *Members' Spotify
  playlists* signed in to Spotify as cooperking, joins it, and taps *I've joined: check*. The Worker also looks every
  10 minutes for its first day, then hourly. Once readable it's read every 30 minutes from 06:00 to 23:00, and when its
  owner taps *Check now* (once a minute at most).
- **A calendar's days** (John, 30 Oct 2026; Profile › Playlists › *Days*, and straight after adding one): by the day
  each song is added on Spotify (John's rule, a day with no song taking the next); forwards from a day (the first song on
  it, then a song a day); or back from a day (the newest song now on it, each one before a day earlier). Either of the
  last two keeps its anchor (the first, or the newest then: `dates { how, day, at, n }` on its record), so new songs
  follow on a day each. A day still to come is hidden on the calendar (its owner sees it in the pop-up, "to come"). Moving the anchor looks at today's song again.
- **Messages** (John, 30 Oct 2026: "Songs shouldn’t automatically go out as a message unless the user chooses and
  should be able to choose the time too"; Profile › Playlists › *Messages*): off unless its owner turns it on, at the
  time they choose (UK time; `send { on, at }` on its record). On: a calendar's song goes to the members on its day, a
  playlist's on the day its owner signs it off, at that time, or at the sign-off when that's later; once each (`ann`
  in `upl:<id>`), as a message from its owner, to those who take theirs. The Worker's 10-minute look sends them.
- **In the My playlist pop-up** (John, 31 Oct 2026: "spotify playlists should be treated in most ways like another
  playlist - you need to be able to get to them from the playlists badge on another playlist, and open them to edit
  them in the same way, as a popup to reorder and edit - editing should be done from the popup, not the page shown to
  other users"): their own Spotify playlists are among their playlists' badges there (A–Z with the saved ones), and
  open there from a badge or Profile › Playlists › *Open*: all its songs (the waiting ones, marked, with *Sign off*;
  a calendar's days to come, "to come"; each with its day for a calendar), the pencil on each (its video, moods,
  files and cover), *Play*, *Open in Spotify*, YouTube, and its settings (*Show* its calendar as the others see it,
  *Title*, *Who sees it*, *Days*, *Messages*, the kind, *Check now*, *Remove*); *Back to My playlist* (or another
  badge) leaves it. The calendar and a plain playlist's list show it as the others see it, for its owner too: no
  waiting songs or days to come, no Sign off or Edit. A link to a waiting song (its owner's notification) opens it in
  the pop-up.
- **A plain playlist's order** (John, 30 Oct 2026: "orderable in exactly the same way as a normal playlist"): in the
  pop-up, as My playlist: its owner moves them by the handle (or the up and down arrow keys) or orders them with My
  playlist's buttons (First day, Song A–Z, Artist A–Z, Oldest, each the other way on a second tap; Shuffle), saved for
  everyone (`order` in `upl:<id>`); songs added on Spotify since go after, as Spotify has them. On the page it's
  listed in that order; a song tapped plays on through it from there. On a computer it fills the calendar's place.
  Its YouTube copy keeps Spotify's order.
- **Who sees them** (John, 31 Oct 2026: "Members should be able to choose, public, private, or choose members to share
  with in playlist set up"; asked straight after adding one, and Profile › Playlists › *Who sees it*; `share` and `to`
  on its record): *Public* (every member, and anyone with its link, signed in or not), *Every member* (the first
  setting, and the one before 31 Oct), *Members you choose* (ticked; `to`), or *Private* (just its owner). Never anyone
  kept apart from its owner, and only once John has joined it; its owner from the start. Its messages go only to those
  who see it. The calendar's title (top left) chooses whose calendar shows: John's, or a member's (the address then has
  `#u=<id>`, `#u=<id>&d=<day>` for a day, `#u=<id>&m=<mm-yyyy>` for a month). On another's calendar the Spotify and
  YouTube links go to their playlist and its copy, and playing My playlist goes back to John's calendar.
- **Hearts** (John, 31 Oct 2026: "You should be able to favourite songs and add to your own playlists"): on members'
  calendars as on John's (each song, and a month's at once). A member's song in My playlist or a saved playlist is kept
  on the device with what's needed to show and play it on any calendar (`sotd-songx`: its calendar too), brought up to
  date whenever its calendar shows, and fetched from the Worker (`/members/songs?ids=`, only from the playlists the
  person sees) for a saved playlist opened on another device. Played from My playlist on John's calendar, it opens on
  its own (its day, its owner's video and cover, and its own address, `#u=<id>&d=<day>`).
- **Sharing** (John, 31 Oct 2026: "Sharing options should be exactly the same as the main playlist"): Share on a
  member's calendar, its month and each of its days, as on John's: Video or Preview (Song for an owner's own MP3), the
  picture, WhatsApp, Messages, Email, Copy link, Copy picture, More, Send to members. As its owner chose: public, the
  Worker's pages (`/s/u/<id>`, `/s/u/<id>/<yyyy-mm>`, `/s/u/<id>/<yyyy-mm-dd>`, with `?play=` passed on, and `?draft=1`
  from the draft), each with its card, as John's day pages: the site job draws them (`share_pages.py`,
  `songs/u/<id>/…/card.jpg`, from `/upl/sync`'s `pub`, with the calendar's name at the top), and until a new day's is
  drawn the page and the share sheet show the cover; the site job runs when a public one's song is signed off or it's
  made public or not public (a playlist no longer public loses its cards). Every member or chosen members: the
  calendar's own link, which opens for those who see it, the share sheet saying so; for chosen members, Send to members
  only to them and only by its owner. Private: no Share. A song sent to the members has its card drawn on the page,
  with its calendar's name. Someone not signed in opening a public one's link sees it, with the title's chooser to go
  back to John's; one that isn't public says "That calendar is for members." and shows John's.
- **The Profile's Playlists** (John, 31 Oct 2026: "can there not be a distinction between Spotify Playlists and
  Playlists on the user profile - just list them all, alphabetically"): one list, A–Z, of those saved from My playlist
  (*Open*) and their Spotify ones (*Open*: the My playlist pop-up; *Title*, *Who sees it*, *Days*, *Messages*, the
  kind, *Check now*, *Remove*), then *Add a Spotify playlist*.
- **New songs.** The songs on it when it's added are out from the start; each song added after waits for its owner,
  who is told on their phone ("“…” waits for you to sign off"). They sign it off in any of three places (John, 30 Oct
  2026: "I can’t see how a user would sign off a song"): Profile › Playlists, which opens first when something waits
  (a count on its tab), lists each playlist's waiting songs with *Sign off* and *Open* (the pop-up, at it); in the
  pop-up it's marked, with *Sign off*. (`wait` on `pls`: the newest 20.) Signed off, it's
  out for everyone, and goes to the members as a message from them only as they've chosen (Messages, below; "From
  Mum"; reactions and replies as any message; on their Sent too). 20 sign-offs a day at most.
- **Kept.** KV `upls` (each playlist: owner, Spotify id and name, calendar or playlist, invite link, when added, whether
  readable, last read, count) and `upl:<id>` (its songs as last read, and when each was signed off). Previews and moods
  come the same way as John's (KV `previews`, `tags`): 6 previews with each read, and the Worker's 10-minute look takes
  30 more of any not looked up yet, at any hour. *Open in Spotify* takes their songs too. Removing a member
  removes their playlists; *Remove* (theirs, or John's on the Members tab with two taps) takes one off the calendar,
  not off Spotify.
- **Videos, moods, years, YouTube copies** (the site job, song-tags.yml): `_sotd/upl_sync.py` takes in each playlist's
  10 newest songs for its YouTube copy when it's first seen, then 5 more a day (newest first), and every song added after
  it was added straight away (John, 30 Oct 2026: "It should add their last ten songs in the playlist to a new YouTube
  playlist after that 5 a day or less if there isn't capacity"); kept in `_sotd/data/upl.json` (`in`). Videos, moods and
  years are found for all its songs: those in, and 40 more a run, newest first (`look`; John, 31 Oct 2026: "youtube or
  previews aren't being added"). The steps finding videos (youtube.py, within its 95 searches a day),
  listening to previews, Last.fm, MusicBrainz, years and moods (make_tags.py) then work through John's songs and those
  (numbered from 100001, marked `upl`); John's songs keep their order and the searches kept for his newest, and moods
  are measured on John's songs alone, so his don't change. `_sotd/youtube_upl.py` (after John's YouTube playlist) makes
  each one's unlisted YouTube copy on the same channel, titled as the calendar calls it, and adds the songs taken in that
  have a video, in order, stopping with 1,000 of the day's units left for John's; the calendar's *On YouTube* goes to it.
  None of it for members' songs is kept in this repository or on the site (John, 31 Oct 2026: "Why does any of that
  have to be public at all?"): `_sotd/upl_private.py load` puts last run's part for their songs back into the files
  before the steps (from the Worker's `/upl/data`, R2 `site/upl-data.json`), and `save` takes every song on a member's
  playlist that isn't John's out of them again before they're committed (`_sotd/data/audio.json`, `musicbrainz.json`,
  `years.json`, `youtube.json`, `songs/moods.json`, `songs/videos.json`, `songs/years.json`, and `_sotd/data/upl.json`
  whole) and sends it there. The Worker keeps each song's video, moods and genres, and year (KV `uplmeta`) and gives
  them with its calendar (`vid`, `mt`, `yr` on each song), to those who see it; without an answer from the Worker the
  job leaves their songs out that run. Only a public playlist's cards are on the site (`songs/u/`). Tests:
  `upl_private_t.py`, `upl_data_t.mjs`.
  The site job gets the playlists from the Worker's `/upl/sync` with its own GITHUB_TOKEN (the Worker checks with GitHub
  that it's a token for this repository alone), and sends back the YouTube copies. The Worker starts the site job when a
  member's playlist changes, and once a day from 07:00 for the day's 5 more. Tests: `upl_py_t.py`.
- **Their own for each song** (John, 31 Oct 2026: "there doesn't seem to be anywhere to see the youtube links, the mp3s
  moods or anything else that I get in my option on the admin page"): its owner taps the pencil on the song in the
  pop-up, any time, before or after sign-off. *Its video*: the one found on YouTube, a YouTube
  link of theirs, or none (`ed { <track>: { v } }` in `upl:<id>`; `''` none). *Moods* and *Genres*: up to two each, or
  back to the ones worked out (`m`, `g`; `[]` none). These are kept with *Save*. *Your own files*: an MP3 (80 MB at most)
  and an MP4 (1.5 GB at most), up in 10 MB parts or more (R2 `ua/<id>/<track>.mp3`, `uv/<id>/<track>.mp4`), playing
  in place of the preview and the YouTube video for them and for the members John lets have his own (the MP3 and MP4
  ticks on their Members card), by links signed as his are (`/m/ua/…`, `/m/uv/…`, 12 hours). *Its cover*: a picture,
  cropped square to 640 pixels on the page (R2 `uc/<id>/<track>.jpg`, served at `/uc/…` for everyone who sees the
  calendar). The files and the cover go up as soon as they're chosen, and *Take off* asks first. Sizes in `files` in
  `upl:<id>`. On their calendar each song shows its owner's choices; downloads are for John's calendar only. Their
  video goes into the YouTube copy (one changed since has its old one taken out; with none, taken out) and the site job
  doesn't search for it. Removing the playlist (or the member) removes its files.
- **Facts, for public playlists' songs** (John, 31 Oct 2026, asked which: "Public playlists only"): written and checked
  by John's facts task as his are (`_sotd/FACTS.md`), for the songs on a playlist set to Public that were added after it
  was added to the calendar, the 10 newest from before that (John, 31 Oct 2026, offered the same 10 its
  YouTube copy starts with: "let's do that"), and any other when its owner taps *Ask for facts* in its editor (10 a day; `fask` in
  `upl:<id>`). The Worker's
  `/upl/sync` lists them (`facts`); `_sotd/upl_sync.py` writes them with John's songs into `songs-facts.json`, which
  about.py gathers sources for (each marked `upl`), so `facts_todo.py` and `facts_ask.py` take each once. `facts_add.py`
  puts them in `_sotd/data/upl-facts.json`, not John's waiting list, and the site job sends that to the Worker
  (`_sotd/upl_facts.py send`, `/upl/facts`), where each waits for its owner (KV `ufacts:<id>`). The editor's *Its facts*
  shows them: change any, take any out, then *Sign off the facts*; the pop-up's row says *facts wait for you* until
  then. Signed off, they show on that calendar for everyone who sees it (`fx` on the song): captions while it plays,
  listed under it when it can't play; still its owner's to change (*Save*). One with none found says so. A playlist
  that stops being public gets no more, and the site job takes its songs out of `_sotd/data/sources.json` and
  `upl-facts.json` before committing (`upl_facts.py tidy`); those signed off before stay on its calendar. Tests:
  `upl_facts_t.mjs` (the Worker), `upl_facts_py_t.py` (the site job), `upl_facts_page_t.py` (the draft).
- Worker: `/members/upl` (GET `?id=`, with no sign-in for a public one; POST ops `add`, `mode`, `link`, `check`, `remove`,
  `signoff`, `dates`, `order`, `send`, `edit`, `title`, `share`, `facts`, `askfacts`), `/upl/sync`, `/upl/data` and `/upl/facts`
  (the site job's), `/members/songs?ids=`, `/s/u/<id>[/<month or day>]` (a public
  one's link pages), `/members/uplup` (`?id&track&kind=a|v&op=start|part|done|abort|drop`), `/members/uplcover` (`?id&track`,
  `{ pic }`, or `&op=drop`), `pls` on `/members/me`, admin ops `upls`, `uplcheck`, `uplremove`. Tests: `upl_t.mjs` (the
  Worker), `upl_page_t.py` (the draft), `upl_pv_t.mjs` (previews), `upl_admin_t.py` (the Members tab), `upl_dates_t.mjs` (days and order),
  `upl_order_t.py` (the draft's order and Days), `upl_share_t.mjs` and `upl_share_page_t.py` (titles, who sees them,
  sharing, the Profile's one list), `share_upl_t.py` (their cards), `upl_fav_t.py` (hearts and My playlist), `upl_pop_t.py` (the pop-up), `upl_wait_t.py` (where they sign off), `mine_drag_t.py` (My playlist's
  dragging, shared with them), `upl_edit_t.mjs` and `upl_edit_page_t.py` (their own for each song).

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
