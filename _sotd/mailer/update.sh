#!/bin/sh
# The mailer's new version, with clasp, to the same address (nothing to change on the Worker). In Terminal, from this
# folder: sh update.sh
# Finds the project ("Song of the Day mailer") if this folder doesn't know it yet, pushes Code.js, puts the web app's
# deployment on the new version (its address stays the same), then checks it answers as version 2.
set -e
cd "$(dirname "$0")"
command -v clasp >/dev/null || { echo "clasp isn't installed: see the steps Claude gave."; exit 1; }
if [ ! -f .clasp.json ]; then
  list=$(clasp list 2>/dev/null || clasp list-scripts 2>/dev/null || true)
  id=$(printf '%s\n' "$list" | grep -i "Song of the Day mailer" | grep -oE '[A-Za-z0-9_-]{40,}' | head -1)
  if [ -z "$id" ]; then echo "Couldn't find the 'Song of the Day mailer' project in your Apps Script (clasp list said:)"; echo "$list"; exit 1; fi
  printf '{"scriptId":"%s","rootDir":"."}\n' "$id" > .clasp.json
  echo "Found the mailer: $id"
fi
clasp push -f
deps=$(clasp deployments 2>/dev/null || clasp list-deployments 2>/dev/null || true)
dep=""
[ -f mailer-url.txt ] && dep=$(grep -oE 'AKfy[A-Za-z0-9_-]{20,}' mailer-url.txt | head -1)
[ -z "$dep" ] && dep=$(printf '%s\n' "$deps" | grep -v '@HEAD' | grep -oE 'AKfy[A-Za-z0-9_-]{20,}' | head -1)
if [ -z "$dep" ]; then echo "Couldn't find the web app's deployment (clasp deployments said:)"; echo "$deps"; exit 1; fi
clasp deploy -i "$dep" -d "v2: emails say who they're from (Grifflix too)" 2>/dev/null || clasp update-deployment "$dep" -d "v2: emails say who they're from (Grifflix too)"
url="https://script.google.com/macros/s/$dep/exec"
printf '%s\n' "$url" > mailer-url.txt
echo
echo "Done: the mailer's new version is live at the same address."
echo "Within half an hour, Grifflix's admin page (Notifications and emails) says: Emails go signed Grifflix."
