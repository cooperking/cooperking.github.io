# Working on this site

## Grifflix: changes go to the draft, not live

John's rule (19 Nov 2026): "changes to the site go to drafts unless I say otherwise".

- Build Grifflix page changes for the draft only: `python3 _grifflix/web/page.py` (writes `grifflix/draft/index.html`).
- Do not run `python3 _grifflix/web/page.py --live` and do not commit `grifflix/index.html` or `grifflix/version.json`
  unless John says, for that change, to put it live. John checks the draft and publishes it himself (Publish on the admin page).
- An earlier general "go live" from John does not cover later changes: ask about each one.
- The same applies to other parts of the site with a draft and a live copy: change the draft, leave live to John.

## If the site ever moves to a new repository

John's wish (26 Nov 2026): start the new repository from the current files only, without the old history (the old history still holds,
for 22 Nov 2026 00:26–10:26, the AFI Catalog text that was then in `grifflix/data/d/*.json` and `library.json`). Don't rewrite this
repository's history for it.
