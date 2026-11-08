// node render-futura.js OUT PX [W H] [params]: the Futura icon (futura.html) at PX pixels wide; W x H its layout size (512 x 512 by default)
const { chromium } = require('playwright'), path = require('path');
(async () => { const [out, px, W = 512, H = 512, params = ''] = process.argv.slice(2); const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: +W, height: +H }, deviceScaleFactor: (+px) / (+W) });
  await p.goto('file://' + path.join(__dirname, 'futura.html') + '?w=' + W + 'px&h=' + H + 'px&' + params); await p.waitForSelector('body[data-ready]'); await p.waitForTimeout(200);
  await (await p.$('#i')).screenshot({ path: out }); await b.close(); })();
