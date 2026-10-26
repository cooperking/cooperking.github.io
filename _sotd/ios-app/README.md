# John's Song of the Day: the iPhone app (TestFlight)

The calendar (https://cooperking.github.io/songs/) inside an app, so it opens signed in and keeps playing like any music
app. Built on one of GitHub's Macs by `.github/workflows/ios.yml` (Capacitor: `package.json`, `capacitor.config.json`;
`customise.sh` adds background audio, App-Bound Domains for the offline downloads, Apple's notifications, the icon;
`build.sh` signs it and sends it to App Store Connect, with a distribution certificate and App Store profile made for
each build through the API by `signing.py`, so no device needs registering; `asc_check.py` checks both Apple keys first
and names a wrong secret on the run's page). The site itself is the app's content, so changes to the
calendar reach the app at once; only changes in this folder need a new build. A build is also made on the 1st of every
second month, so testers always have one well inside TestFlight's 90 days.

Notifications in the app are Apple's own (APNs), sent by the Worker with John's APNs key (see the Worker's
"the iPhone app" part); the website's notifications carry on as before for everyone else.

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

## Testers

- John: TestFlight → **Internal Testing** → add himself → the TestFlight app on the iPhone → Install.
- Family: TestFlight → **External Testing** → a group (*Family*) → add the build. Test Information: a description,
  an email, and **Sign-in required** with an invite link (Members page: invite *App Review*, copy its link). Apple's
  Beta App Review takes about a day the first time; then add their email addresses, or turn on the group's public link.
- In the app, the first time: Profile (top right) → Sign in → their code or invite link.
