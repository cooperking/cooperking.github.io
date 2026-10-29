# John's Song of the Day: the iPhone app (TestFlight)

The calendar (https://cooperking.github.io/songs/) inside an app, so it opens signed in and keeps playing like any music
app. Built on one of GitHub's Macs by `.github/workflows/ios.yml` (Capacitor: `package.json`, `capacitor.config.json`;
`customise.sh` adds background audio, App-Bound Domains for the offline downloads, Apple's notifications, the icon;
`build.sh` signs it and sends it to App Store Connect, with a distribution certificate and App Store profile from the
API by `signing.py`, so no device needs registering (the certificate is kept, encrypted, in the R2 bucket for later
builds, as revoking one can invalidate builds already uploaded); `asc_check.py` checks both Apple keys first
and names a wrong secret on the run's page). The site itself is the app's content, so changes to the
calendar reach the app at once; only changes in this folder need a new build. A build is also made on the 1st of every
second month, so testers always have one well inside TestFlight's 90 days.

Adding a member from John's contacts (build 22 on): the Members tab's *From Contacts…* calls the app's own plugin
(`native/App/SotdContacts.swift`, `SotdContacts.pick()`), which shows Apple's contact picker and gives back the
person's name and email addresses. The picker runs outside the app, so the app sees only the contact chosen and asks
for no permission.

Notifications in the app are Apple's own (APNs), sent by the Worker with John's APNs key (see the Worker's
"the iPhone app" part); the website's notifications carry on as before for everyone else.

Invitations open the app: an invitation's link (`sotd-playlists.cooperking.workers.dev/i/<id>`) tapped on an iPhone with
the app opens the app, which signs in with it (Apple's universal links: the Associated Domains entitlement in
`customise.sh`, switched on for the app's ID by `signing.py`, and Apple's link file, which the Worker serves; the
calendar listens with the App plugin). Without the app, the invitation's page shows an iPhone the steps for it
(TestFlight's public link) and copies the invitation as they go; the first time the app opens not signed in, it offers
to sign in with what was copied (the Clipboard plugin; iOS asks once whether it may paste). The public link goes into
`songs/ios.json` by itself once Apple approves a build (`public_link.py`, run hourly by `.github/workflows/ios-link.yml`
until it's there).

## Setting it up (once)

1. Apple Developer Program (developer.apple.com/programs/enroll), as an individual: $99 a year; Apple can take a day or two.
2. developer.apple.com/account → Certificates, Identifiers & Profiles → **Identifiers** → + → App IDs → App:
   Bundle ID (explicit) `io.github.cooperking.sotd`, description *Song of the Day*, tick **Push Notifications** → Register.
3. Same place → **Keys** → +: name *SotD push*, tick **Apple Push Notifications service (APNs)** → Register →
   **Download** the .p8 (only once) and note its **Key ID**. The **Team ID** is under Membership details.
4. appstoreconnect.apple.com → Apps → + **New App**: iOS, name *John's Song of the Day* (it has to be unique across the
   App Store: add something if it's taken), English (UK), the bundle ID above, SKU `sotd1`.
5. App Store Connect → Users and Access → **Integrations** → App Store Connect API → Team Keys → +: name *GitHub*,
   access **Admin** → Generate → **Download** the .p8 (only once); note its **Key ID** and the **Issuer ID** above the list.
6. github.com/cooperking/cooperking.github.io → Settings → Secrets and variables → Actions → **New repository secret**,
   six of them: `APPLE_TEAM_ID`, `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8` (the whole text of the API key's .p8,
   opened in TextEdit or Notepad), `APNS_KEY_ID`, `APNS_KEY_P8` (the whole text of the APNs key's .p8).
7. Run Actions → **Worker** (puts the APNs key on the Worker) and Actions → **iPhone app** (the build). About 15 minutes
   after it finishes, the build is in App Store Connect → TestFlight.

## Each new build to the testers by itself

After each upload the workflow's second job (`testflight.py`, on Linux) waits until Apple has processed the build, gives
it the What to Test notes, adds it to the external group (the family's, SOTD) and submits it for Beta App Review; the
internal group gets it straight away. To do that for a build already uploaded: Actions > iPhone app > Run workflow,
with its number in "testflight_only" (no new build is made).

## Testers

- John: TestFlight → **Internal Testing** → add himself → the TestFlight app on the iPhone → Install.
- Family: TestFlight → **External Testing** → a group (*Family*) → add the build. Test Information: a description,
  an email, and **Sign-in required** with an invite link (Members page: invite *App Review*, copy its link). Apple's
  Beta App Review takes about a day the first time; then add their email addresses. The group's public link is turned
  on by `ios-link.yml` once a build is approved, and invitations offer it.
- In the app, the first time: it offers to sign in with the invitation they came from; or Profile (top right) → Sign in →
  their code or invite link.
