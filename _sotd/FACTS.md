# Song facts: how they're written, checked and signed off

The calendar (cooperking.github.io/songs/) shows short facts about each song and its artists as captions over the
song's video or 30-second preview: one at a time, under a heading (the song's title for a song fact, the artist's name
for an artist fact). John signs every song's facts off before they go live.

- `songs/facts.json`: live. Only `_sotd/facts_apply.py` changes it, when John signs off.
- `_sotd/data/facts-pending.json`: written and checked, waiting for John. Shown on the draft calendar and on the sign-off
  page, `songs/facts-admin.html`, where he edits, removes, reorders, adds his own and signs off. The Cloudflare Worker
  keeps his sign-offs and starts `.github/workflows/facts.yml`, which runs `facts_apply.py` to put them live.
- `_sotd/data/sources.json`: what the facts are written from first: the English Wikipedia articles on each song, album
  and artist, and MusicBrainz's details on each artist. The job song-tags.yml adds each new song's (`_sotd/about.py`).

## The scheduled task: facts for new songs

Every few hours, a scheduled session does this. Nothing it does reaches the live site.

1. `git pull` in the repository (cooperking/cooperking.github.io).
2. `python3 _sotd/facts_todo.py <work folder>`. If it says 0 songs need facts, stop: nothing to report. A song only
   appears once its sources are saved (the job song-tags.yml runs a little after each new song).
3. Write the facts for each song in `<work folder>/todo.md`, following **Writing** below, into
   `<work folder>/written.json`. Read the saved pages it lists first, then search the web.
4. Have them checked by someone who didn't write them: start a subagent with the **Checking** section below, the path
   of `written.json` and the work folder (for the saved pages), and have it write `<work folder>/verdicts.json`. Don't
   check your own facts.
5. `python3 _sotd/facts_add.py <work folder>/written.json <work folder>/verdicts.json`, then
   `python3 _sotd/facts_apply.py --offline`.
6. Commit `_sotd/data/facts-pending.json`, `songs/facts-pending.json` and `songs/draft/facts.json` ("Song facts written
   for <song>: waiting for John's sign-off"), pull with rebase and push.
7. Tell John in one or two lines: which songs now wait at https://cooperking.github.io/songs/facts-admin.html, with how
   many facts each, and any that fell short and why.

Never edit `songs/facts.json` or anything else on the live site; never sign anything off.

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
