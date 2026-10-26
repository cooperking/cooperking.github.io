// The admin pages take this month's colours (London time), as the calendar does (the same palettes as songs/index.html)
(function () {
  var PALETTES = [
    ['#0c1445', '#0094de', '#8833ff', '#f700f7', '#009aa8'], ['#652bee', '#3333ff', '#cc33ff', '#3388ff', '#ff2bb8'],
    ['#11970c', '#429e00', '#00a150', '#7d9600', '#009e84'], ['#b613ec', '#fc00e7', '#8833ff', '#ff33aa', '#5533ff'],
    ['#096a42', '#00a336', '#009e91', '#00a30e', '#0097c9'], ['#ec1a13', '#fa5300', '#ff3366', '#db6e00', '#ff3399'],
    ['#1346ec', '#008ff5', '#5533ff', '#0097c9', '#8833ff'], ['#d85a06', '#c47c00', '#ff4a2e', '#b28600', '#ff3344'],
    ['#0b86d3', '#0098b0', '#3370ff', '#009e91', '#3333ff'], ['#ec135b', '#ff3344', '#ff30b3', '#ff4c29', '#fc00e7'],
    ['#09696d', '#009e77', '#0094d4', '#00a150', '#198cff'], ['#ec13c1', '#ff3399', '#dd33ff', '#ff3366', '#aa33ff']
  ];
  var m = 0;
  try { m = +new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', month: 'numeric' }).format(new Date()) - 1; } catch (e) { m = new Date().getMonth(); }
  var p = PALETTES[m] || PALETTES[0], st = document.documentElement.style;
  st.setProperty('--bg', p[0]); st.setProperty('--g1', p[1]); st.setProperty('--g2', p[2]); st.setProperty('--g3', p[3]); st.setProperty('--g4', p[4]);
  var tc = document.querySelector('meta[name="theme-color"]'); if (tc) tc.content = p[0];
  // opened over the calendar while something plays (its player's box along the foot): the dock and toasts sit above it
  try { if (window.top !== window && window.top.document.documentElement.classList.contains('site-on')) document.documentElement.classList.add('under-player'); } catch (e) {}
})();

// Pull down to refresh, as on the calendar: on a touch screen, at the top of the page, pull and let go past the mark and
// a record spins while the page loads again (inside the admin page: just this tab, still signed in). The phone's own
// pull (and its rubber-band bounce) is off here so the two don't both happen.
(function () {
  var touchy = ('ontouchstart' in window) || navigator.maxTouchPoints > 0;
  if (!touchy || !(window.CSS && CSS.supports && CSS.supports('overscroll-behavior-y', 'none'))) return;
  var AT = 64;
  var css = '.aptr { position: fixed; left: 50%; top: 0; z-index: 60; width: 40px; height: 40px; margin-left: -20px; pointer-events: none; opacity: 0; transform: translateY(-60px); border-radius: 50%; box-shadow: 0 1px 3px rgba(14, 6, 18, .4); }'
    + '.aptr-turn { display: block; width: 40px; height: 40px; border-radius: 50%; position: relative; background: repeating-radial-gradient(circle at 50% 50%, #08070c 0 .9px, #1b1922 .9px 1.7px); }'
    + '.aptr-turn::before { content: ""; position: absolute; left: 31%; top: 31%; width: 38%; height: 38%; border-radius: 50%; background: radial-gradient(circle, var(--bg) 0 18%, transparent 19%), linear-gradient(135deg, var(--g1), var(--g3)); }'
    + '.aptr-turn::after { content: ""; position: absolute; inset: 0; border-radius: 50%; background: conic-gradient(from 20deg, rgba(255,255,255,0) 0deg, rgba(255,190,90,.22) 25deg, rgba(255,255,255,0) 55deg, rgba(255,255,255,0) 180deg, rgba(255,90,140,.16) 210deg, rgba(255,255,255,0) 240deg); -webkit-mask: radial-gradient(circle, transparent 0 34%, #000 35%); mask: radial-gradient(circle, transparent 0 34%, #000 35%); }'
    + '.aptr.spin .aptr-turn { animation: aptrspin .8s linear infinite; }'
    + '.aptr.back, .aptr-moving.back { transition: transform .28s cubic-bezier(.2, .8, .25, 1), opacity .2s ease; }'
    + '@keyframes aptrspin { to { transform: rotate(360deg); } }';
  function start() {
    var body = document.querySelector('.wrap');
    if (!body) return;   // (the password screen and the tabs' frame: nothing of its own to load)
    var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    document.documentElement.style.overscrollBehaviorY = 'none'; document.body.style.overscrollBehaviorY = 'none';
    var ptr = document.createElement('div'); ptr.className = 'aptr'; ptr.setAttribute('aria-hidden', 'true');
    ptr.innerHTML = '<span class="aptr-turn"></span>'; document.body.appendChild(ptr);
    var turn = ptr.firstChild, x0 = 0, y0 = null, pull = 0, mode = null;
    function set(p, back) {
      body.classList.add('aptr-moving'); body.classList.toggle('back', !!back); ptr.classList.toggle('back', !!back);
      body.style.transform = p ? 'translateY(' + p + 'px)' : '';
      ptr.style.transform = 'translateY(' + Math.round(Math.max(-60, p / 2 - 20 + 8)) + 'px)';
      ptr.style.opacity = String(Math.max(0, Math.min(1, (p - 4) / 24)));
      turn.style.transform = 'rotate(' + Math.round(p * (p >= AT ? 7 : 4)) + 'deg)';
    }
    document.addEventListener('touchstart', function (e) {
      y0 = null;
      if (e.touches.length !== 1 || ptr.classList.contains('spin') || (window.scrollY || document.documentElement.scrollTop) > 0) return;
      if (document.querySelector('dialog[open]') || (e.target.closest && e.target.closest('input, textarea, select, .seg, dialog'))) return;
      x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; pull = 0; mode = null;
    }, { passive: true });
    document.addEventListener('touchmove', function (e) {
      if (y0 === null) return;
      var dx = e.touches[0].clientX - x0, dy = e.touches[0].clientY - y0;
      if (!mode) {
        if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
        mode = dy > 0 && dy > Math.abs(dx) * 1.2 ? 'down' : 'other';
      }
      if (mode !== 'down') return;
      if (e.cancelable) e.preventDefault();
      pull = Math.min(120, Math.max(0, dy) * 0.5);
      set(pull, false);
    }, { passive: false });
    function end() {
      if (y0 === null) return;
      y0 = null;
      if (mode === 'down' && pull >= AT) { set(AT, true); ptr.classList.add('spin'); setTimeout(function () { location.reload(); }, 500); }
      else if (mode === 'down') set(0, true);
      mode = null;
    }
    document.addEventListener('touchend', end); document.addEventListener('touchcancel', end);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
