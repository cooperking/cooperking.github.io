# Grifflix: the iPhone app (TestFlight)

The site (https://cooperking.github.io/grifflix/) inside an app: films play full screen and to AirPlay, with the ringer
switch off. Built on one of GitHub's Macs by `.github/workflows/grifflix-ios.yml` (Capacitor: `package.json`,
`capacitor.config.json`; `customise.sh` adds the name, icon, Grifflix's sites as App-Bound Domains, the audio session;
`build.sh` signs it and sends it to App Store Connect, with `signing.py`: the Apple Distribution certificate is the one
the songs' app's builds keep, shared, and an App Store profile for io.github.cooperking.grifflix). The same Apple
secrets as the songs' app. The site is the app's content, so Grifflix's changes reach the app at once (after Publish);
only changes here need a new build. A build is made on the 15th of every second month, so testers stay inside
TestFlight's 90 days.

Notifications: not in this first build (a web page inside an app can't have them); Grifflix's emails, or Grifflix
added to the Home Screen from Safari, meanwhile. Sign in: Profile > Account > Get a code on another device, then
the code in the app.

## Setting it up (once)

1. Actions > **Grifflix iPhone app** > Run workflow. The first run registers the app's ID and then stops, saying the
   app isn't in App Store Connect yet.
2. appstoreconnect.apple.com > Apps > + > **New App**: iOS, name **Grifflix** (add something if it's taken), English (UK),
   bundle ID **io.github.cooperking.grifflix**, SKU **grifflix1**.
3. Run the workflow again. About 15 minutes after it finishes the build is in App Store Connect > Grifflix > TestFlight.
4. TestFlight > Internal Testing > + a group > add yourself > the TestFlight app on the iPhone > Install. Family later:
   External Testing (Apple reviews the first build, about a day).
