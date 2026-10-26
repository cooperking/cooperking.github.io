// The pictures the admin page draws: a link's card, exactly as _sotd/share_pages.py card() draws a day's (the month's
// colours, the record half out of its sleeve, the words), and a photo made into an album cover. Used for invitations
// (members-admin.html) and for each song's own sharing picture (songs-admin.html).
window.SOTDCards = (function () {
var PALETTES = [
  ['#0c1445', '#0094de', '#8833ff', '#f700f7', '#009aa8'], ['#652bee', '#3333ff', '#cc33ff', '#3388ff', '#ff2bb8'],
  ['#11970c', '#429e00', '#00a150', '#7d9600', '#009e84'], ['#b613ec', '#fc00e7', '#8833ff', '#ff33aa', '#5533ff'],
  ['#096a42', '#00a336', '#009e91', '#00a30e', '#0097c9'], ['#ec1a13', '#fa5300', '#ff3366', '#db6e00', '#ff3399'],
  ['#1346ec', '#008ff5', '#5533ff', '#0097c9', '#8833ff'], ['#d85a06', '#c47c00', '#ff4a2e', '#b28600', '#ff3344'],
  ['#0b86d3', '#0098b0', '#3370ff', '#009e91', '#3333ff'], ['#ec135b', '#ff3344', '#ff30b3', '#ff4c29', '#fc00e7'],
  ['#09696d', '#009e77', '#0094d4', '#00a150', '#198cff'], ['#ec13c1', '#ff3399', '#dd33ff', '#ff3366', '#aa33ff']
];
function rgb(h) { return [1, 3, 5].map(function (i) { return parseInt(h.slice(i, i + 2), 16); }); }
function londonNow() {
  var o = {}; new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', year: 'numeric', month: 'numeric', day: 'numeric' }).formatToParts(new Date()).forEach(function (x) { o[x.type] = +x.value; });
  return new Date(Date.UTC(o.year, o.month - 1, o.day, 12));
}
function loadImg(src) {
  return new Promise(function (res, rej) { var im = new Image(); im.crossOrigin = 'anonymous'; im.onload = function () { res(im); }; im.onerror = rej; im.src = src; });
}
function drawCard(o) {   // o: { title, line (in capitals), when (a Date at noon UTC), cover (an image or canvas), and if
  // other words are wanted: top (in place of JOHN’S SONG OF THE DAY), date (in place of the day, written out) }
  var title = o.title, cover = o.cover;
  var W = 1200, H = 630, c = document.createElement('canvas'); c.width = W; c.height = H;
  var g = c.getContext('2d'), when = o.when || londonNow(), pal = PALETTES[when.getUTCMonth()];
  // the month's ground and glows, as the calendar's CSS draws them (share_pages.py ground())
  var img = g.createImageData(W, H), d = img.data, bg = rgb(pal[0]), vmax = Math.max(W, H);
  var layers = [[pal[3], .7, 34, 28, 8, 10], [pal[4], .85, 38, 30, 96, 102], [pal[3], 1, 52, 40, 2, 98], [pal[2], .85, 40, 32, 30, 40], [pal[1], 1, 54, 42, 92, 4]]
    .map(function (l) { return { c: rgb(l[0]), k: l[1], rx: l[2] / 100 * vmax, ry: l[3] / 100 * vmax, cx: l[4] / 100 * W, cy: l[5] / 100 * H }; });
  for (var y = 0; y < H; y++) for (var x = 0; x < W; x++) {
    var r = bg[0], gg = bg[1], b = bg[2];
    for (var i = 0; i < 5; i++) {
      var L = layers[i], dx = (x - L.cx) / L.rx, dy = (y - L.cy) / L.ry, a = Math.max(0, Math.min(1, 1 - Math.sqrt(dx * dx + dy * dy) / .72)) * L.k;
      r = r * (1 - a) + L.c[0] * a; gg = gg * (1 - a) + L.c[1] * a; b = b * (1 - a) + L.c[2] * a;
    }
    var p = (y * W + x) * 4; d[p] = r; d[p + 1] = gg; d[p + 2] = b; d[p + 3] = 255;
  }
  g.putImageData(img, 0, 0);
  // the cover, with its record half out of the sleeve
  var size = 400, cx = 70, cy = (H - size) / 2, rd = size - 16, rx = cx + size - rd / 2 + 12, ry = cy + 8;
  g.save(); g.shadowColor = 'rgba(20, 6, 20, .43)'; g.shadowBlur = 36; g.shadowOffsetX = 6; g.shadowOffsetY = 14;
  g.fillStyle = 'rgb(10, 9, 14)'; g.beginPath(); g.arc(rx + rd / 2, ry + rd / 2, rd / 2, 0, Math.PI * 2); g.fill();
  g.shadowOffsetY = 16; g.fillStyle = 'rgba(20, 6, 20, .47)'; g.fillRect(cx, cy, size, size);
  g.restore();
  g.fillStyle = 'rgb(10, 9, 14)'; g.beginPath(); g.arc(rx + rd / 2, ry + rd / 2, rd / 2, 0, Math.PI * 2); g.fill();
  g.strokeStyle = 'rgb(30, 28, 38)'; g.lineWidth = 1;
  for (var k = 8; k < rd / 2 - rd / 5; k += 3) { g.beginPath(); g.arc(rx + rd / 2, ry + rd / 2, rd / 2 - k, 0, Math.PI * 2); g.stroke(); }
  var lab = Math.floor(rd * .38);
  function square(im) { var w = im.naturalWidth || im.width, h = im.naturalHeight || im.height, sd = Math.min(w, h); return [(w - sd) / 2, (h - sd) / 2, sd, sd]; }
  if (cover) {
    var sq = square(cover);
    g.save(); g.beginPath(); g.arc(rx + rd / 2, ry + rd / 2, lab / 2, 0, Math.PI * 2); g.clip();
    g.drawImage(cover, sq[0], sq[1], sq[2], sq[3], rx + (rd - lab) / 2, ry + (rd - lab) / 2, lab, lab); g.restore();
  }
  g.fillStyle = pal[0]; g.beginPath(); g.arc(rx + rd / 2, ry + rd / 2, 4.5, 0, Math.PI * 2); g.fill();
  if (cover) { var sq2 = square(cover); g.drawImage(cover, sq2[0], sq2[1], sq2[2], sq2[3], cx, cy, size, size); }
  else { g.fillStyle = 'rgba(255, 250, 245, .14)'; g.fillRect(cx, cy, size, size); }
  // the words: as a day's
  var x0 = rx + rd + 48, width = W - 64 - x0, INK = 'rgb(255, 250, 245)';
  g.textBaseline = 'top';
  function spaced(t, x, y, f, fill, tr) { g.font = f; g.fillStyle = fill; for (var i = 0; i < t.length; i++) { g.fillText(t[i], x, y); x += g.measureText(t[i]).width + tr; } }
  function wrap(t, f) { g.font = f; var w = t.split(/\s+/), out = [], line = ''; w.forEach(function (x) { var tt = (line + ' ' + x).trim(); if (g.measureText(tt).width <= width || !line) line = tt; else { out.push(line); line = x; } }); if (line) out.push(line); return out; }
  var tsize, lines, tf;
  [68, 62, 56, 50, 45, 40, 36].some(function (sz) {
    tsize = sz; tf = '500 ' + sz + 'px Newsreader'; lines = wrap(title, tf);
    var widow = lines.length > 1 && lines[lines.length - 1].length <= 3;
    g.font = tf;
    return lines.length <= 3 && !widow && lines.every(function (l) { return g.measureText(l).width <= width; });
  });
  lines = lines.slice(0, 3);
  var lh = Math.floor(tsize * 1.08), block = 19 + 26 + lines.length * lh + 22 + 22 + 30 + 30, y0 = Math.floor((H - block) / 2);
  var top = String(o.top != null ? o.top : 'JOHN’S SONG OF THE DAY').toUpperCase(); g.font = '600 19px "Hanken Grotesk"';
  while (g.measureText(top).width + top.length * 3.2 > width && top.length > 8) top = top.slice(0, -2).replace(/\s+$/, '') + '…';
  spaced(top, x0, y0, '600 19px "Hanken Grotesk"', 'rgba(255, 250, 245, .9)', 3.2);
  y0 += 19 + 26;
  g.font = tf; g.fillStyle = INK;
  lines.forEach(function (l) { g.fillText(l, x0, y0 - tsize * .08); y0 += lh; });
  y0 += 22;
  var line = String(o.line || '').toUpperCase(); g.font = '600 22px "Hanken Grotesk"';
  while (g.measureText(line).width + line.length * 2.4 > width && line.length > 8) line = line.slice(0, -2).replace(/\s+$/, '') + '…';
  spaced(line, x0, y0, '600 22px "Hanken Grotesk"', 'rgba(255, 250, 245, .94)', 2.4);
  y0 += 22 + 30;
  g.font = 'italic 400 30px Newsreader'; g.fillStyle = 'rgba(255, 250, 245, .92)';
  var dl = o.date != null ? String(o.date) : longDate(when);
  while (g.measureText(dl).width > width && dl.length > 8) dl = dl.slice(0, -2).replace(/\s+$/, '') + '…';
  g.fillText(dl, x0, y0);
  return c.toDataURL('image/jpeg', 0.84);
}
// John's own picture made into an album cover: the photo, darkened at the foot and the head so the words read, with
// the calendar's name across the top and the person's name set like the calendar's own heading (roman, then italic)
function albumCover(im, o) {   // o: { big (roman, as big as fits), italic (under it), left and right (along the foot), top }
  var N = 640, c = document.createElement('canvas'); c.width = c.height = N;
  var g = c.getContext('2d'), w = im.naturalWidth || im.width, h = im.naturalHeight || im.height, sd = Math.min(w, h);
  g.drawImage(im, (w - sd) / 2, (h - sd) / 2, sd, sd, 0, 0, N, N);
  var foot = g.createLinearGradient(0, N * .38, 0, N);
  foot.addColorStop(0, 'rgba(12, 6, 14, 0)'); foot.addColorStop(.55, 'rgba(12, 6, 14, .45)'); foot.addColorStop(1, 'rgba(12, 6, 14, .82)');
  g.fillStyle = foot; g.fillRect(0, 0, N, N);
  var head = g.createLinearGradient(0, 0, 0, N * .26);
  head.addColorStop(0, 'rgba(12, 6, 14, .5)'); head.addColorStop(1, 'rgba(12, 6, 14, 0)');
  g.fillStyle = head; g.fillRect(0, 0, N, N * .26);
  var m = 44, ink = '#fffaf5';
  g.textBaseline = 'alphabetic'; g.fillStyle = ink;
  function tracked(t, x, y, f, tr) { g.font = f; for (var i = 0; i < t.length; i++) { g.fillText(t[i], x, y); x += g.measureText(t[i]).width + tr; } return x; }
  g.shadowColor = 'rgba(0, 0, 0, .35)'; g.shadowBlur = 12;
  tracked(String(o.top != null ? o.top : 'JOHN’S SONG OF THE DAY').toUpperCase(), m, m + 18, '600 21px "Hanken Grotesk"', 4);
  // the big words, as big as fits (two lines at most), then the italic line
  var words = o.big || '', size = 132, lines;
  for (; size >= 44; size -= 4) {
    g.font = '500 ' + size + 'px Newsreader';
    lines = []; var cur = '';
    words.split(/\s+/).forEach(function (x) { var t = (cur + ' ' + x).trim(); if (g.measureText(t).width <= N - 2 * m || !cur) cur = t; else { lines.push(cur); cur = x; } });
    if (cur) lines.push(cur);
    if (lines.length <= 2 && lines.every(function (l) { return g.measureText(l).width <= N - 2 * m; })) break;
  }
  var italic = Math.round(Math.max(40, Math.min(64, size * .5))), y = N - m - 44;
  g.font = 'italic 400 ' + italic + 'px Newsreader'; var it = o.italic || '';
  while (it.length > 4 && g.measureText(it).width > N - 2 * m) it = it.slice(0, -2).replace(/\s+$/, '') + '…';
  g.fillText(it, m + 2, y);
  g.font = '500 ' + size + 'px Newsreader';
  y -= italic * .95;
  for (var i = lines.length - 1; i >= 0; i--) { g.fillText(lines[i], m - size * .03, y); y -= size * .92; }
  g.shadowBlur = 0;
  g.fillStyle = 'rgba(255, 250, 245, .55)'; g.fillRect(m, N - m - 22, N - 2 * m, 1.5);
  g.fillStyle = 'rgba(255, 250, 245, .85)';
  tracked(String(o.left || '').toUpperCase(), m, N - m + 2, '600 15px "Hanken Grotesk"', 3.4);
  g.font = '600 15px "Hanken Grotesk"';
  var rt = String(o.right || '').toUpperCase(); tracked(rt, N - m - g.measureText(rt).width - 3.4 * rt.length, N - m + 2, '600 15px "Hanken Grotesk"', 3.4);
  return c;
}
function fontsReady() {
  if (!document.fonts) return Promise.resolve();
  return Promise.all(['500 68px Newsreader', 'italic 400 30px Newsreader', '600 19px "Hanken Grotesk"'].map(function (f) { return document.fonts.load(f); })).catch(function () {});
}
function longDate(d) { return d.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).replace(',', ''); }
// a chosen picture file as an image to draw: whatever the browser can open as it is; an iPhone's HEIC/HEIF photo, which
// Chrome and Firefox can't open, turned into a JPEG here first (vendor/heic2any.min.js, fetched only then). Recognised by
// its first bytes, not its name: some come named .jpeg.
var heicLib = null;
function heicJs() {
  if (!heicLib) heicLib = new Promise(function (res, rej) {
    if (window.heic2any) return res(window.heic2any);
    var sc = document.createElement('script'); sc.src = '/songs/vendor/heic2any.min.js';
    sc.onload = function () { if (window.heic2any) res(window.heic2any); else rej(new Error('no heic2any')); };
    sc.onerror = function () { heicLib = null; rej(new Error('heic2any not loaded')); };
    document.head.appendChild(sc);
  });
  return heicLib;
}
// Tried in turn, as a browser can refuse one way and not another: its address (blob:), the bytes decoded directly
// (createImageBitmap), the bytes as a data: address, then HEIC. Rejects with err.kind: 'read' (the file's bytes couldn't
// be read at all, e.g. a file in OneDrive or iCloud not on the computer yet), 'heic', or 'decode'; err.detail says what
// it was and what each way said, for the message.
function blobImg(b) {
  var u = URL.createObjectURL(b);
  return loadImg(u).then(function (im) { URL.revokeObjectURL(u); return im; }, function (e) { URL.revokeObjectURL(u); throw e; });
}
function fileImage(f, converting) {
  var said = [], bytes = null;
  var fail = function (way) { return function (e) { said.push(way + ': ' + ((e && (e.name && e.name !== 'Error' ? e.name + ' ' : '') + (e.message || '')) || (e && e.type) || 'failed').trim()); return null; }; };
  return f.arrayBuffer().then(function (b) { bytes = b; }, function (e) {
    var x = new Error('couldn’t be read'); x.kind = 'read'; x.detail = ((e && e.name) || '') + ' ' + ((e && e.message) || ''); throw x;
  }).then(function () {
    return blobImg(f).catch(fail('address'));
  }).then(function (im) {
    if (im || !window.createImageBitmap) return im;
    return createImageBitmap(f).then(function (bm) {
      var c = document.createElement('canvas'); c.width = bm.width; c.height = bm.height; c.getContext('2d').drawImage(bm, 0, 0);
      c.naturalWidth = bm.width; c.naturalHeight = bm.height; if (bm.close) bm.close(); return c;
    }).catch(fail('bytes'));
  }).then(function (im) {
    if (im) return im;
    return new Promise(function (res, rej) { var r = new FileReader(); r.onload = function () { res(r.result); }; r.onerror = function () { rej(r.error); }; r.readAsDataURL(f); })
      .then(loadImg).catch(fail('data'));
  }).then(function (im) {
    if (im) return im;
    var head = new Uint8Array(bytes.slice(0, 12)), t = String.fromCharCode.apply(null, head);
    var what = f.size + ' bytes, starting ' + [].map.call(head, function (n) { return (n < 16 ? '0' : '') + n.toString(16); }).join(' ');
    if (!(t.slice(4, 8) === 'ftyp' && /^(hei[cfmsx]|hev[cmsx]|mif1|msf1)$/.test(t.slice(8, 12)))) {
      var x = new Error('not a picture this browser can open'); x.kind = 'decode'; x.detail = what + '; ' + said.join('; '); throw x;
    }
    if (converting) converting();
    return heicJs().then(function (h) { return h({ blob: f, toType: 'image/jpeg', quality: 0.92 }); }).then(function (b) {
      return blobImg(Array.isArray(b) ? b[0] : b);
    }).catch(function (e) { var x = new Error('HEIC: ' + ((e && e.message) || e)); x.heic = true; x.kind = 'heic'; x.detail = what; throw x; });
  });
}
// what to tell John when fileImage fails, naming the file
function fileWhy(e, f) {
  var what = (f.name || 'it') + (f.type ? ', ' + f.type : '') + ', ' + Math.round((f.size || 0) / 1024) + ' KB';
  if (e && e.kind === 'read') return 'Couldn’t read that file from the computer (' + what + '). If it’s in OneDrive or iCloud, open it once so it’s on this Mac, then choose it again. [' + String(e.detail || '').trim() + ']';
  if (e && e.heic) return 'That HEIC photo couldn’t be turned into a JPEG (' + what + '): export it as a JPEG (Photos: File › Export) and choose that';
  return 'That picture couldn’t be opened (' + what + ') [' + ((e && e.detail) || (e && e.message) || '') + ']';
}
return { drawCard: drawCard, albumCover: albumCover, loadImg: loadImg, londonNow: londonNow, fontsReady: fontsReady, longDate: longDate, fileImage: fileImage, fileWhy: fileWhy };
})();
