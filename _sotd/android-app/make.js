// The Android project, from the calendar's own web manifest: a Trusted Web Activity of https://cooperking.github.io/songs/
// (Chrome, full screen, the site's notifications delegated to the app). Writes ./project. Run by .github/workflows/android.yml.
// Added to Bubblewrap's project: the shortcuts (a long press on the app: today's song, shuffle, My playlist: the
// calendar plays them from ?do=), the Home Screen widgets (widget/: Song of the Day with its settings, Play, Calendar;
// from the site's songs/today.json and songs/days.json) and the Quick Settings tile. With WIDGET_PREVIEW=1 (the
// widgets' check), also a page showing them (widget/preview).
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
  // the Home Screen widgets and the tile: their code and resources, and their entries in the app's manifest
  const path = require('path'), main = __dirname + '/project/app/src/main';
  const copy = (from, to) => { for (const f of fs.readdirSync(from)) { const a = path.join(from, f), b = path.join(to, f);
    if (fs.statSync(a).isDirectory()) { fs.mkdirSync(b, { recursive: true }); copy(a, b); } else fs.copyFileSync(a, b); } };
  fs.mkdirSync(main + '/java/io/github/cooperking/sotd', { recursive: true });
  copy(__dirname + '/widget/java', main + '/java/io/github/cooperking/sotd');
  copy(__dirname + '/widget/res', main + '/res');
  const preview = process.env.WIDGET_PREVIEW === '1';
  if (preview) copy(__dirname + '/widget/preview', main + '/java/io/github/cooperking/sotd');
  const mf = main + '/AndroidManifest.xml', m0 = fs.readFileSync(mf, 'utf8');
  if (!m0.includes('</application>')) throw new Error('The app\'s manifest has no </application>');
  // the widgets fetch the site's lists and covers themselves: the internet (Bubblewrap asks for none, as Chrome does
  // the app's own loading; without it, build 10's widget never had its song)
  const m = m0.includes('android.permission.INTERNET"') ? m0 : m0.replace(/<application\b/, '<uses-permission android:name="android.permission.INTERNET" />\n    <application');
  const widget = (cls, label, info) => `    <receiver android:name=".${cls}" android:exported="false" android:label="@string/${label}">
            <intent-filter><action android:name="android.appwidget.action.APPWIDGET_UPDATE" /></intent-filter>
            <meta-data android:name="android.appwidget.provider" android:resource="@xml/${info}" />
        </receiver>
`;
  fs.writeFileSync(mf, m.replace('</application>', widget('TodayWidget', 'widget_label', 'widget_today_info') +
    widget('PlayWidget', 'play_label', 'widget_play_info') + widget('CalendarWidget', 'calendar_label', 'widget_calendar_info') +
    `        <activity android:name=".TodayConfig" android:exported="true" android:label="@string/widget_label" android:theme="@android:style/Theme.Material.Light.Dialog">
            <intent-filter><action android:name="android.appwidget.action.APPWIDGET_CONFIGURE" /></intent-filter>
        </activity>
        <service android:name=".SotdTile" android:exported="true" android:icon="@drawable/widget_ic_note" android:label="@string/tile_label"
            android:permission="android.permission.BIND_QUICK_SETTINGS_TILE">
            <intent-filter><action android:name="android.service.quicksettings.action.QS_TILE" /></intent-filter>
        </service>
` + (preview ? `        <activity android:name=".WidgetPreview" android:exported="true" />
` : '') + `    </application>`));
  console.log('project made: version ' + tm.appVersionName + ' (' + code + ')');
})().catch(e => { console.error(e); process.exit(1); });
