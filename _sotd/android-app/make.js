// The Android project, from the calendar's own web manifest: a Trusted Web Activity of https://cooperking.github.io/songs/
// (Chrome, full screen, the site's notifications delegated to the app). Writes ./project. Run by .github/workflows/android.yml.
// Added to Bubblewrap's project: the shortcuts (a long press on the app: today's song, shuffle, My playlist: the
// calendar plays them from ?do=) and the Home Screen widget (widget/: TodayWidget, from the site's songs/today.json).
const { TwaManifest, TwaGenerator, ConsoleLog } = require('@bubblewrap/core');
const { ShortcutInfo } = require('@bubblewrap/core/dist/lib/ShortcutInfo');
const SITE = 'https://cooperking.github.io/songs/', ICONS = process.env.ICON_BASE || SITE + 'shortcuts/';   // (ICON_BASE: a local copy, for testing)
(async () => {
  const tm = await TwaManifest.fromWebManifest(process.env.MANIFEST_URL || 'https://cooperking.github.io/songs/manifest.webmanifest');
  tm.host = 'cooperking.github.io';   // (the site's, even when made from a copy of its manifest)
  const code = Number(process.env.VERSION_CODE) || 1;
  Object.assign(tm, {
    packageId: 'io.github.cooperking.sotd',
    name: 'John’s Song of the Day', launcherName: 'Song of the Day',
    // the start address says which copy this is (android.yml: Play's app=android, the site's app=android-apk) and its
    // build, so the page can offer the site's copy a newer build (Play updates its own)
    startUrl: process.env.START_URL || '/songs/', enableNotifications: true, fallbackType: 'customtabs',
    appVersionCode: code, appVersionName: '1.' + code,
    signingKey: { path: process.env.KEYSTORE || 'upload.p12', alias: 'sotd' },
    enableSiteSettingsShortcut: true,   // (the helper library looks for its activity on every start: without it the app can't open)
    minSdkVersion: 24,   // (Android 7: the browser helper library needs it)
    shortcuts: [
      new ShortcutInfo('Play today’s song', 'Today’s song', SITE + '?do=today', ICONS + 'today.png'),
      new ShortcutInfo('Shuffle the songs', 'Shuffle', SITE + '?do=shuffle', ICONS + 'shuffle.png'),
      new ShortcutInfo('Play my playlist', 'My playlist', SITE + '?do=mine', ICONS + 'mine.png'),
    ],
  });
  await new TwaGenerator().createTwaProject(__dirname + '/project', tm, new ConsoleLog('android'));
  // no shrinking: R8 renames the classes the app finds by name (its notification service), and it then can't start
  // ("Component class R.u does not exist", 26 Oct 2026 on an emulated Pixel); the app is small either way
  const fs = require('fs'), g = __dirname + '/project/app/build.gradle';
  fs.writeFileSync(g, fs.readFileSync(g, 'utf8').replace(/minifyEnabled\s+true/g, 'minifyEnabled false').replace(/shrinkResources\s+true/g, 'shrinkResources false'));
  // the Home Screen widget: its code and resources, and its receiver in the app's manifest
  const path = require('path'), main = __dirname + '/project/app/src/main';
  const copy = (from, to) => { for (const f of fs.readdirSync(from)) { const a = path.join(from, f), b = path.join(to, f);
    if (fs.statSync(a).isDirectory()) { fs.mkdirSync(b, { recursive: true }); copy(a, b); } else fs.copyFileSync(a, b); } };
  fs.mkdirSync(main + '/java/io/github/cooperking/sotd', { recursive: true });
  copy(__dirname + '/widget/java', main + '/java/io/github/cooperking/sotd');
  copy(__dirname + '/widget/res', main + '/res');
  const mf = main + '/AndroidManifest.xml', m = fs.readFileSync(mf, 'utf8');
  if (!m.includes('</application>')) throw new Error('The app\'s manifest has no </application>');
  fs.writeFileSync(mf, m.replace('</application>', `    <receiver android:name=".TodayWidget" android:exported="false" android:label="@string/widget_label">
            <intent-filter><action android:name="android.appwidget.action.APPWIDGET_UPDATE" /></intent-filter>
            <meta-data android:name="android.appwidget.provider" android:resource="@xml/widget_today_info" />
        </receiver>
    </application>`));
  console.log('project made: version ' + tm.appVersionName + ' (' + code + ')');
})().catch(e => { console.error(e); process.exit(1); });
