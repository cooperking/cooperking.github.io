#!/bin/sh
# Song of the Day mailer: made with clasp, once. In Terminal, from this folder: sh setup.sh
# Makes the Apps Script project, pushes Code.js and appsscript.json, deploys it as a web app, writes the web app's
# address to mailer-url.txt and opens the editor (run setUp there). Works with clasp 2 or 3.
set -e
cd "$(dirname "$0")"
if [ -f .clasp.json ]; then echo "Set up already (.clasp.json is here). After a change: clasp push -f, then clasp redeploy <id>"; exit 0; fi
cp appsscript.json .appsscript.mine
clasp create --type standalone --title "Song of the Day mailer"
mv .appsscript.mine appsscript.json   # (create brings down Google's default manifest; this one makes it a web app)
clasp push -f
out=$(clasp deploy --description "Song of the Day mailer")
echo "$out"
id=$(printf '%s' "$out" | grep -oE 'AKfy[A-Za-z0-9_-]{20,}' | head -1)
if [ -z "$id" ]; then echo "Couldn't find the deployment's id in what clasp said (above)."; exit 1; fi
printf 'https://script.google.com/macros/s/%s/exec\n' "$id" > mailer-url.txt
echo
echo "MAILER_URL: $(cat mailer-url.txt)   (also in mailer-url.txt)"
echo "Now in the editor: choose setUp, press Run, and allow it to send email as you. The log shows MAILER_KEY."
clasp open-script 2>/dev/null || clasp open
