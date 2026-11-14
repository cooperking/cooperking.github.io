// A throwaway web server for the repo (the page's absolute /grifflix/... paths need the repo root), on a free port.
// Fonts and calendar data from the Worker refuse localhost, so fallbacks show and the days come from grifflix/data/days.json.
const { spawn } = require('child_process'); const path = require('path'); const net = require('net');
const ROOT = path.resolve(__dirname, '..', '..');
async function freePort() { return new Promise(res => { const s = net.createServer(); s.listen(0, () => { const p = s.address().port; s.close(() => res(p)); }); }); }
async function serve() {
  const port = await freePort();
  const py = spawn('python3', ['-m', 'http.server', String(port), '--bind', '127.0.0.1'], { cwd: ROOT, stdio: 'ignore' });
  for (let i = 0; i < 50; i++) { const ok = await new Promise(r => { const c = net.connect(port, '127.0.0.1', () => { c.end(); r(true); }); c.on('error', () => r(false)); }); if (ok) break; await new Promise(r => setTimeout(r, 100)); }
  return { url: 'http://127.0.0.1:' + port, stop: () => py.kill() };
}
const TVUA = 'Mozilla/5.0 (Linux; Android 9; AFTSSS) AppleWebKit/537.36 Chrome/120 Safari/537.36 GRIFFLIXTV/1.0';
const SKIPLEADER = () => { try { localStorage.setItem('grifflix-leader-at', String(Date.now())); } catch (e) {} };   // (no 3 2 1)
module.exports = { serve, TVUA, SKIPLEADER, ROOT };
