# Song facts: how they're written, checked and signed off

The calendar (cooperking.github.io/songs/) shows short facts about each song and its artists as captions over the
song's video or 30-second preview: one at a time, under a heading (the song's title for a song fact, the artist's name
for an artist fact). A new song's facts are written and checked, then wait for John: he checks and edits them, adds
his own recording and video for the members, and signs it off on the Songs tab, which puts it out on the live calendar
and sends it to the members' phones (John, 28 Oct 2026: "the site shouldn't publish automatically for new song of the
day as i'd like to check the facts and add videos and songs myself for members"). The draft calendar holds it back the
same way. The YouTube playlist doesn't wait: the song joins it as soon as it has a video. (From 24 to 28 Oct 2026 the
checked facts went live straight away.)

- `songs/facts.json`: live. Only `_sotd/facts_apply.py` changes it, with what John signs off (a new song's sign-off
  brings it out on the live calendar and the share pages: `_sotd/released.py`).
- `_sotd/data/facts-pending.json`: written and checked, waiting for John's sign-off (not live meanwhile). Shown on the
  sign-off page (the Songs tab of `songs/admin.html`, behind the admin password; the part page
  is `songs/songs-admin.html`, which only shows inside it), where he edits, removes, reorders, adds his own and
  signs off. The Cloudflare Worker keeps his sign-offs and starts `.github/workflows/facts.yml`, which runs
  `facts_apply.py` to put them live. (The draft calendar shows waiting facts only for songs already out, as rewrites.)
  A new song whose facts haven't reached the site yet shows "No facts yet" there, and the Songs tab looks for them every
  20 seconds and puts them on its card as they come (into John's changes too, his moods, genres and video kept); signing
  off a new song with no facts asks once more (John, 30 Oct 2026: signed off 10 seconds before the facts reached the
  site, it came out with none, and the written ones were dropped; they were put back as waiting).
- `_sotd/data/sources.json`: what the facts are written from first: the English Wikipedia articles on each song, album
  and artist, and MusicBrainz's details on each artist. The job song-tags.yml adds each new song's (`_sotd/about.py`).

## The facts task: facts for new songs

Claude's routine "Song facts for new songs (on demand)" (claude.ai/code/routines) does this. It has no timer: song-tags.yml starts it through the routine's API
trigger at the end of the run that saves a new song's sources (`_sotd/facts_ask.py`; the secrets SOTD_FACTS_URL and
SOTD_FACTS_TOKEN hold its address and token). The new song stays off the live calendar until this has run. How each
call went is kept in `_sotd/data/facts-task.json` (started, with the task's session; or not, with what came back), and a
call that fails fails that step of the job, so the run shows as failed. The admin page's Status tab
(`songs/status-admin.html`, from the Worker's admin op `status`) shows each new song's steps to the calendar, that call,
and the latest run of each GitHub job, with a count of what has failed or is stuck on its tab. It checks again when
opened, every minute while open and after a sign-off; *Dealt with* on a failed thing stops it counting (KV statusAck,
30 days), and anything new that fails shows again. The notifications sent are there too: each song's as its last step (sent, or waiting for it to
come out on the calendar), and the last 12 (KV pushLog, kept by `pushAll`), with how many devices took each. When the task's token stops
working ("OAuth access token has been revoked": 28 Oct 2026, after John changed his Google password), make a new one on
the task's page and put it in the SOTD_FACTS_TOKEN secret; Run on the task's page writes the songs waiting meanwhile.

A new song's notification goes to the members as it comes out on the live calendar: the site job's last step
(`_sotd/push_live.py`) waits for the live site's `songs/today.json` to name it, then asks the Worker (`/push/check`),
which checks the live site itself and sends it once (the Worker's 10-minute look does the same, in case that call is
lost). Before 28 Oct 2026 it waited for John's MP3 and video instead.

1. `git pull` in the repository (cooperking/cooperking.github.io).
2. `python3 _sotd/facts_todo.py <work folder>`. If it says 0 songs need facts, stop: nothing to report. A song only
   appears once its sources are saved (the job song-tags.yml runs a little after each new song).
3. Write the facts for each song in `<work folder>/todo.md`, following **Writing** below, into
   `<work folder>/written.json`. Read the saved pages it lists first, then search the web.
4. Have them checked by someone who didn't write them: start a subagent with the **Checking** section below, the path
   of `written.json` and the work folder (for the saved pages), and have it write `<work folder>/verdicts.json`. Don't
   check your own facts.
5. `python3 _sotd/facts_add.py <work folder>/written.json <work folder>/verdicts.json`, then
   `python3 _sotd/facts_apply.py --offline` (which puts them on the Songs tab, waiting for John; not live, and not on
   the draft calendar either).
6. Commit `songs/facts.json`, `_sotd/data/facts-pending.json`, `songs/facts-pending.json`, `songs/draft/facts.json`,
   `_sotd/data/facts-quotes.json` and `songs/facts-quotes.json` ("Song facts for <song>: written, waiting for John's
   sign-off"), pull with rebase and push. (The song stays off the calendar and the share pages until John signs it off;
   his sign-off starts song-tags.yml, which makes its share page and sends the notification.)
7. Tell John in one or two lines: which songs now wait for his check and sign-off on the Songs tab of
   https://cooperking.github.io/songs/admin.html, with how many facts each, and any that fell short and why.

Never edit `songs/facts.json` by hand or anything else on the live site; never run `facts_apply.py --promote`; never
sign anything off.

## Writing

For every song: 4 facts about the song (at least 3, up to 5 if they're all good). For every credited artist: 4 facts
(at least 3, up to 5). Only where the sources really don't exist (an unknown artist with no articles, interviews or
label pages) give fewer, and say in `note` what you searched.

Song facts are about the song: how and where it was written and recorded, who played on it, what it samples or
borrows, what happened when it came out, who covered or sampled it, where it has been used, stories behind it. Artist
facts are about the artist's life and career. Don't repeat information between a song's facts and its artist's facts,
or between facts.

**Interesting.** Prefer the surprising and specific: an odd recording circumstance, an accident, a bet, a strange job,
a nickname and where it came from, an unexpected use, a record broken, who turned it down, what it was nearly called,
a famous fan, a number that startles. Avoid the generic: where someone was born, which label released what, "is an
American singer-songwriter", "received positive reviews", chart positions on their own unless remarkable.

**Clear.** Each fact is read on its own, under its heading, with nothing around it.
- The first time a fact mentions any person, use their full name ("Al Green and Mabon ‘Teenie’ Hodges wrote it...",
  never "Green and Hodges" or "Teenie Hodges"). After that, in the same fact, a surname or pronoun is fine.
- An artist fact is shown under the artist's name, so it doesn't repeat it: it says he, she or they (or "the band")
  for the artist ("He followed his mentor Joe ‘King’ Oliver to Chicago..."). Use the artist's name only where a pronoun
  would be unclear, for instance after another person has been mentioned.
- A song fact is shown under the song's title, so it names the artist ("Al Green and Mabon ‘Teenie’ Hodges wrote
  it..."), and never uses he, she or they for anyone it hasn't named. Every other musician gets their full name.
- "It" for the song is fine in a song fact. Name albums, bands and other songs in full.
- Song titles in single curly quotes: ‘Hey, Girl’. Album titles plain. British spelling and dates (13 October 2025).
- One sentence, two at most. 30 words at most; aim for 15 to 28.

**True.** Every fact fully supported by its source, nothing added from memory. Sources: the saved Wikipedia pages;
Wikipedia and MusicBrainz pages you read; the artist's or label's own site or Bandcamp page; interviews and articles in
established outlets (the Guardian, Pitchfork, NME, Rolling Stone, Stereogum, The Quietus, NPR, BBC, Billboard,
AllMusic, Uncut, Mojo, Paste, Consequence, The FADER, Line of Best Fit, Clash, local newspapers and the like). Not
user-written sites: no Genius annotations, Songfacts, Reddit, fan wikis, Last.fm wiki text, YouTube comments or
AI-written content farms. `q` is the exact words from the source that back the fact (up to 50 words), copied, not
reworded; `u` is the address of the page actually read.

**Never** quote or paraphrase lyrics; include allegations of crimes, health details or how anyone died (that someone
died, and when, is fine where it matters); or gossip about relationships unless it's the documented story of the song.

Web searches are limited per session; if one is refused, work from the saved pages and try again later.

`written.json`:

```json
{"songs": {"<track id>": {"facts": [{"t": "the fact", "u": "https://...", "src": "page title", "q": "exact words"}], "note": "only if short"}},
 "artists": {"<artist id>": {"name": "Artist", "facts": [ ... ], "note": "..."}}}
```

Order each list best first: the first song fact and the first artist fact are the two shown in the preview.

## Checking

You're checking facts someone else wrote, independently, as a careful fact-checker and editor; don't assume they're
right. For each fact in `written.json`, read its source: a saved page (in the work folder's `pages/`), or fetch the
address (WebFetch, asking for the relevant sentences verbatim; if it refuses an address, WebSearch for the page and
fetch the result). Then decide:

1. True to its source: every name, date, number, place and claim is on the page. Nothing added. Narrow or drop
   anything that goes further than the page; drop it if the page can't be reached or doesn't back it.
2. Clear on its own: full name at first mention of anyone other than the artist; under an artist's heading the artist
   is he, she or they (not their name, unless a pronoun would be unclear); a song fact names the artist and uses no
   pronoun for anyone it hasn't named.
3. Fit to show: 30 words at most; no lyrics; no allegations, health details or how someone died; no gossip; not a
   near-repeat of another fact for the same song or artist.

Write `verdicts.json`: for the fact at index i of a song's list, the key is `new:s:<track id>:<i>`; for an artist's,
`new:a:<artist id>:<i>`. Value: `{"v": "keep"}`, `{"v": "fix", "t": "corrected text", "why": "..."}` (substance right
and backed, but a name, pronoun, claim or length needs fixing; the fix itself must be backed) or
`{"v": "drop", "why": "..."}`. Every fact needs a verdict.
