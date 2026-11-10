const { chromium } = require('playwright'); const path = require('path');
(async () => { const b = await chromium.launch(); const R = path.join(__dirname, '..', '..');
 const jobs = [ // [out, kind, px, transparent]
  ['grifflix/icon-1024.png','sq',1024], ['grifflix/icon-512.png','sq',512], ['grifflix/icon-192.png','sq',192], ['grifflix/apple-touch-icon.png','sq',180],
  ['grifflix/favicon-96.png','round',96,true], ['grifflix/icon-maskable-512.png','mask',512], ['_grifflix/ios-app/icon-1024.png','sq',1024],
  ...['mdpi','hdpi','xhdpi','xxhdpi','xxxhdpi'].map(d => ['_grifflix/tvapp/res/mipmap-' + d + '/ic_launcher.png','full',512]),
  ...['mdpi','hdpi','xhdpi','xxhdpi','xxxhdpi'].map(d => ['_grifflix/tvapp/res/drawable-' + d + '/banner.png','banner',1280]) ];
 for (const [out, k, px, clear] of jobs) {
  const W = k === 'banner' ? 1280 : 512, H = k === 'banner' ? 720 : 512;
  const p = await b.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: px / W });
  await p.goto('file://' + path.join(__dirname, 'brand.html') + '?k=' + k); await p.waitForSelector('body[data-ready]'); await p.waitForTimeout(150);
  await (await p.$('#i')).screenshot({ path: path.join(R, out), omitBackground: !!clear }); await p.close(); }
 await b.close(); })();
