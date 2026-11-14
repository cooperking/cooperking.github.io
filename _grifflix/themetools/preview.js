#!/usr/bin/env node
/* Screenshots of a theme on every screen it must work on, from the built draft (run `python3 _grifflix/web/page.py` first).
     node _grifflix/themetools/preview.js <key> [<key>...] [--out DIR] [--day YYYY-MM-DD]
   For each key: computer 1440x900, iPhone 390x844, Fire TV (its browser, 960x540 at 2x), computer with motion turned down,
   each as it opens and again two days on (the move between days), and the phone two days on. Page errors are printed:
   any is a fault. Pictures go to DIR (default: the scratchpad's theme-shots, or ./theme-shots). Look at every one. */
const { chromium } = require('playwright'); const fs = require('fs'); const path = require('path');
const { serve, TVUA, SKIPLEADER } = require('./serve');
const args = process.argv.slice(2), keys = args.filter((a, i) => !a.startsWith('--') && !(args[i - 1] || '').startsWith('--'));
const opt = n => { const i = args.indexOf('--' + n); return i >= 0 ? args[i + 1] : null; };
const out = path.resolve(opt('out') || process.env.THEME_SHOTS || 'theme-shots'); fs.mkdirSync(out, { recursive: true });
const SCREENS = [
  ['computer', { viewport: { width: 1440, height: 900 } }, '&big=1'],
  ['iphone', { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }, ''],
  ['tv', { viewport: { width: 960, height: 540 }, deviceScaleFactor: 2, userAgent: TVUA }, '&tv=1'],
  ['calm', { viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' }, '&big=1'],
];
(async () => {
  if (!keys.length) { console.log('Which theme? e.g. node _grifflix/themetools/preview.js table'); process.exit(1); }
  const srv = await serve(); const b = await chromium.launch(); let faults = 0;
  try {
    for (const k of keys) for (const [name, o, q] of SCREENS) {
      const ctx = await b.newContext(o); await ctx.addInitScript(SKIPLEADER); const p = await ctx.newPage();
      p.on('pageerror', e => { faults++; console.log('  FAULT', k, name, e.message); });
      await p.goto(srv.url + '/grifflix/draft/?view=themes&theme=' + k + q + (opt('day') ? '#day/' + opt('day') : '')); await p.waitForTimeout(6000);
      const theme = await p.evaluate(() => document.documentElement.dataset.theme);
      if (theme !== k) { faults++; console.log('  FAULT', k, name, 'drew', theme, 'not', k); }
      await p.screenshot({ path: path.join(out, k + '-' + name + '.png') });
      if (name !== 'calm') {
        for (let i = 0; i < 2; i++) { await p.keyboard.press('ArrowLeft'); await p.waitForTimeout(700); }
        const day = await p.evaluate(() => { const f = document.querySelector('#cal button.front, #cal .tday.has:focus'); return f && f.dataset.day; });
        console.log(k.padEnd(10), name.padEnd(8), 'two days on:', day || '(no front day found)');
        await p.waitForTimeout(800); await p.screenshot({ path: path.join(out, k + '-' + name + '-moved.png') });
      }
      console.log(k.padEnd(10), name.padEnd(8), 'ok');
      await ctx.close();
    }
  } finally { await b.close(); srv.stop(); }
  console.log('\n' + (faults ? faults + ' fault(s): fix before showing John.' : 'No page errors.') + '\nPictures: ' + out);
  process.exit(faults ? 1 : 0);
})();
