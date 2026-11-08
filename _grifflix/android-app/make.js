// The Android project, from Grifflix's own web manifest: a Trusted Web Activity of https://cooperking.github.io/grifflix/
// (Chrome, full screen, the site's notifications delegated to the app). Writes ./project. Run by
// .github/workflows/grifflix-android.yml. The phone's app (io.github.cooperking.grifflix.app), apart from the Fire TV's
// (io.github.cooperking.grifflix, _grifflix/tvapp).
const { TwaManifest, TwaGenerator, ConsoleLog } = require('@bubblewrap/core');
(async () => {
  const tm = await TwaManifest.fromWebManifest(process.env.MANIFEST_URL || 'https://cooperking.github.io/grifflix/manifest.webmanifest');
  tm.host = 'cooperking.github.io';
  const code = Number(process.env.VERSION_CODE) || 1;
  Object.assign(tm, {
    packageId: 'io.github.cooperking.grifflix.app',
    name: 'Grifflix', launcherName: 'Grifflix',
    startUrl: process.env.START_URL || '/grifflix/?app=android', enableNotifications: true, fallbackType: 'customtabs',
    appVersionCode: code, appVersionName: '1.' + code,
    signingKey: { path: process.env.KEYSTORE || 'upload.p12', alias: 'grifflix' },
    enableSiteSettingsShortcut: true,   // (the helper library looks for its activity on every start: without it the app can't open)
    minSdkVersion: 24,
    orientation: 'any',   // (films turn to landscape)
  });
  await new TwaGenerator().createTwaProject(__dirname + '/project', tm, new ConsoleLog('android'));
  // no shrinking (as the songs' app: R8 renames classes the app finds by name)
  const fs = require('fs'), g = __dirname + '/project/app/build.gradle';
  fs.writeFileSync(g, fs.readFileSync(g, 'utf8').replace(/minifyEnabled\s+true/g, 'minifyEnabled false').replace(/shrinkResources\s+true/g, 'shrinkResources false'));
  console.log('project made: version ' + tm.appVersionName + ' (' + code + ')');
})().catch(e => { console.error(e); process.exit(1); });
