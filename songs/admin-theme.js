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
})();
