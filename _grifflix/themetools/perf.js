#!/usr/bin/env node
/* How hard a theme makes a Fire TV work, against the budget every theme must meet (John, 14 Nov 2026: The Table felt heavy on
   the iPhone and was visibly slower on the Fire TV; measured, it did 9x the idle work of the others). Run on the built draft.
     node _grifflix/themetools/perf.js <key> [<key>...]        e.g.  node _grifflix/themetools/perf.js newkey spiral table
   The Fire TV's browser, slowed 6x. Idle: 4 seconds untouched. Moving: six steps to the next day. Budgets:
     idle main-thread work    <= 0.15 s per 4 s   (constant animations cost this every second the theme is open; the old Table
                                                  did 1.2 s, the quiet themes 0.01-0.03)
     layouts while moving     <= 2 per step       (more means something moves by left/top/width/height: use transform)
     work while moving        <= 3.5 s for six steps (times are slowed 6x with the CPU; the smooth themes 0.9-3.1)
     frames while moving      >= 50 a second, the slowest 1 in 20 within 34 ms */
const { chromium } = require('playwright'); const { serve, TVUA, SKIPLEADER } = require('./serve');
const keys = process.argv.slice(2);
(async () => {
  if (!keys.length) { console.log('Which theme? e.g. node _grifflix/themetools/perf.js table'); process.exit(1); }
  const srv = await serve(); const b = await chromium.launch(); let over = 0;
  try {
    for (const k of keys) {
      const ctx = await b.newContext({ viewport: { width: 960, height: 540 }, deviceScaleFactor: 2, userAgent: TVUA }); await ctx.addInitScript(SKIPLEADER);
      const p = await ctx.newPage(); const cdp = await ctx.newCDPSession(p); await cdp.send('Performance.enable'); await cdp.send('Emulation.setCPUThrottlingRate', { rate: 6 });
      p.on('pageerror', e => console.log('  FAULT', k, e.message));
      await p.goto(srv.url + '/grifflix/draft/?view=themes&tv=1&theme=' + k); await p.waitForTimeout(9000);
      const m = async () => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(x => [x.name, x.value]));
      let a = await m(); await p.waitForTimeout(4000); let c = await m();
      const idle = c.TaskDuration - a.TaskDuration;
      const fr = p.evaluate(() => new Promise(res => { const t = []; let last = performance.now(); const t0 = last; (function f(n) { t.push(n - last); last = n; if (n - t0 < 3600) requestAnimationFrame(f); else { t.shift(); t.sort((x, y) => x - y); res({ fps: Math.round(t.length / 3.6), p95: Math.round(t[Math.floor(t.length * .95)]) }); } })(last); }));
      a = await m(); for (let i = 0; i < 6; i++) { await p.keyboard.press('ArrowLeft'); await p.waitForTimeout(600); } c = await m(); const f = await fr;
      const mv = c.TaskDuration - a.TaskDuration, lay = c.LayoutCount - a.LayoutCount;
      const res = [['idle work', idle.toFixed(3) + ' s', idle <= .15], ['layouts moving', String(lay), lay <= 12], ['work moving', mv.toFixed(3) + ' s', mv <= 3.5], ['frames moving', f.fps + '/s, p95 ' + f.p95 + ' ms', f.fps >= 50 && f.p95 <= 34]];
      const bad = res.filter(r => !r[2]); over += bad.length;
      console.log(k.padEnd(10), res.map(r => r[0] + ' ' + r[1] + (r[2] ? '' : ' OVER')).join(' · '));
      await ctx.close();
    }
  } finally { await b.close(); srv.stop(); }
  console.log(over ? '\nOver budget: see the rules in .claude/skills/grifflix-theme/SKILL.md (Performance).' : '\nWithin budget.');
  process.exit(over ? 1 : 0);
})();
