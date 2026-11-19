# Working on this site

## Grifflix: changes go to the draft, not live

John's rule (19 Nov 2026): "changes to the site go to drafts unless I say otherwise".

- Build Grifflix page changes for the draft only: `python3 _grifflix/web/page.py` (writes `grifflix/draft/index.html`).
- Do not run `python3 _grifflix/web/page.py --live` and do not commit `grifflix/index.html` or `grifflix/version.json`
  unless John says, for that change, to put it live. John checks the draft and publishes it himself (Publish on the admin page).
- An earlier general "go live" from John does not cover later changes: ask about each one.
- The same applies to other parts of the site with a draft and a live copy: change the draft, leave live to John.
