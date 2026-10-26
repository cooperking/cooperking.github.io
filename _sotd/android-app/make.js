// The Android project, from the calendar's own web manifest: a Trusted Web Activity of https://cooperking.github.io/songs/
// (Chrome, full screen, the site's notifications delegated to the app). Writes ./project. Run by .github/workflows/android.yml.
const { TwaManifest, TwaGenerator, ConsoleLog } = require('@bubblewrap/core');
(async () => {
  const tm = await TwaManifest.fromWebManifest(process.env.MANIFEST_URL || 'https://cooperking.github.io/songs/manifest.webmanifest');
  tm.host = 'cooperking.github.io';   // (the site's, even when made from a copy of its manifest)
  const code = Number(process.env.VERSION_CODE) || 1;
  Object.assign(tm, {
    packageId: 'io.github.cooperking.sotd',
    name: 'John’s Song of the Day', launcherName: 'Song of the Day',
    startUrl: '/songs/', enableNotifications: true, fallbackType: 'customtabs',
    appVersionCode: code, appVersionName: '1.' + code,
    signingKey: { path: process.env.KEYSTORE || 'upload.p12', alias: 'sotd' },
    enableSiteSettingsShortcut: false,
    minSdkVersion: 24,   // (Android 7: the browser helper library needs it)
  });
  await new TwaGenerator().createTwaProject(__dirname + '/project', tm, new ConsoleLog('android'));
  console.log('project made: version ' + tm.appVersionName + ' (' + code + ')');
})().catch(e => { console.error(e); process.exit(1); });
