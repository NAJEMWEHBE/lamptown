'use strict';
/* Lamptown kit: the shared pixel toolkit the town and every room draw with.
   Loaded before town.js as a plain script, so everything here is a page-level global (one pixel world).
   town.js freezes the public part into K (documented in docs/rooms.md) and adds navigation (leave, card, ...).
   Everything draws into the current 2D context `g` (town.js points it at the screen or at an offscreen layer). */

const W = 400, H = 225;            // native pixels; the page scales them up by a whole number
const OL = '#090a16';               // the outline colour every sprite shares
let g = null;                       // the context the primitives draw into (set by town.js)

/* ---------- primitives ---------- */
function R(x, y, w, h, c) { g.fillStyle = c; g.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h)); }
function P(x, y, c) { g.fillStyle = c; g.fillRect(Math.round(x), Math.round(y), 1, 1); }
function hash(x, y, s) {
  let n = (Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263) + Math.imul((s | 0) + 1, 1442695041)) | 0;
  n = Math.imul(n ^ (n >>> 13), 1274126177); n ^= n >>> 16; return (n >>> 0) / 4294967296;
}
function sprite(rows, x, y, map) {
  for (let j = 0; j < rows.length; j++) { const r = rows[j];
    for (let i = 0; i < r.length; i++) { const c = map[r[i]]; if (c) P(x + i, y + j, c); } }
}
function disc(cx, cy, r, c) { for (let j = -r; j <= r; j++) { const hw = Math.round(Math.sqrt(r * r - j * j)); R(cx - hw, cy + j, hw * 2 + 1, 1, c); } }
function ellipse(cx, cy, rx, ry, c) { for (let j = -ry; j <= ry; j++) { const hw = Math.round(rx * Math.sqrt(Math.max(0, 1 - (j * j) / (ry * ry + 0.5)))); R(cx - hw, cy + j, hw * 2 + 1, 1, c); } }
function hexRGB(h) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]; }
function mixHex(a, b, u) { const A = hexRGB(a), B = hexRGB(b); return '#' + A.map((v, i) => Math.round(v + (B[i] - v) * u).toString(16).padStart(2, '0')).join(''); }
function rgba(h, a) { const [r, gg, b] = hexRGB(h); return 'rgba(' + r + ',' + gg + ',' + b + ',' + a + ')'; }
function pad2(n) { return (n < 10 ? '0' : '') + n; }
const plural = (n, w) => n + ' ' + w + (n === 1 ? '' : 'S');

/* ---------- hand-drawn 3x5 pixel font ---------- */
const FONT = {
  A:[".#.","#.#","###","#.#","#.#"], B:["##.","#.#","##.","#.#","##."], C:[".##","#..","#..","#..",".##"],
  D:["##.","#.#","#.#","#.#","##."], E:["###","#..","##.","#..","###"], F:["###","#..","##.","#..","#.."],
  G:[".##","#..","#.#","#.#",".##"], H:["#.#","#.#","###","#.#","#.#"], I:["###",".#.",".#.",".#.","###"],
  J:["..#","..#","..#","#.#",".#."], K:["#.#","#.#","##.","#.#","#.#"], L:["#..","#..","#..","#..","###"],
  M:["#...#","##.##","#.#.#","#...#","#...#"], N:["#..#","##.#","#.##","#..#","#..#"], O:[".#.","#.#","#.#","#.#",".#."],
  P:["##.","#.#","##.","#..","#.."], Q:[".#.","#.#","#.#","##.",".##"], R:["##.","#.#","##.","#.#","#.#"], S:[".##","#..",".#.","..#","##."],
  T:["###",".#.",".#.",".#.",".#."], U:["#.#","#.#","#.#","#.#","###"], V:["#.#","#.#","#.#","#.#",".#."],
  W:["#...#","#...#","#.#.#","##.##","#...#"], X:["#.#","#.#",".#.","#.#","#.#"], Y:["#.#","#.#",".#.",".#.",".#."], Z:["###","..#",".#.","#..","###"],
  0:["###","#.#","#.#","#.#","###"], 1:[".#.","##.",".#.",".#.","###"], 2:["##.","..#",".#.","#..","###"],
  3:["##.","..#",".#.","..#","##."], 4:["#.#","#.#","###","..#","..#"], 5:["###","#..","##.","..#","##."],
  6:[".##","#..","###","#.#","###"], 7:["###","..#",".#.",".#.",".#."], 8:["###","#.#","###","#.#","###"],
  9:["###","#.#","###","..#","##."], "!":["#","#","#",".","#"], "/":["..#","..#",".#.","#..","#.."],
  "-":["...","...","###","...","..."], ":":[".","#",".","#","."], ">":["#..",".#.","..#",".#.","#.."],
  "<":["..#",".#.","#..",".#.","..#"], ".":[".",".",".",".","#"], ",":[".",".",".","#","#"], "'":["#","#",".",".","."],
  "?":["##.","..#",".#.","...",".#."], "+":["...",".#.","###",".#.","..."], "(":[".#","#.","#.","#.",".#"], ")":["#.",".#",".#",".#","#."],
  "%":["#.#","..#",".#.","#..","#.#"], "=":["...","###","...","###","..."], "_":["...","...","...","...","###"], "&":[".#.","#.#",".#.","#.#",".##"], '"':["#.#","#.#","...","...","..."],
  "[":["##","#.","#.","#.","##"], "]":["##",".#",".#",".#","##"], "#":["#.#","###","#.#","###","#.#"],
  " ":["..","..","..","..",".."]
};
FONT['–'] = FONT['—'] = FONT['-'];
// ONE pixel font for every word on screen: upper case only; a character it lacks prints as a space
function textW(s) { let w = 0; for (const ch of String(s).toUpperCase()) w += (FONT[ch] ? FONT[ch][0].length : 2) + 1; return Math.max(0, w - 1); }
function text(s, x, y, c) { for (const ch of String(s).toUpperCase()) { const gl = FONT[ch] || FONT[' ']; sprite(gl, x, y, { '#': c }); x += gl[0].length + 1; } }

/* ---------- glow sprites (pixel-stepped, additive) ---------- */
function mkGlow(rx, ry, col, a) {
  const c = document.createElement('canvas'); c.width = rx * 2 + 1; c.height = ry * 2 + 1; const x = c.getContext('2d');
  const [r0, g0, b0] = hexRGB(col);
  for (let j = -ry; j <= ry; j++) for (let i = -rx; i <= rx; i++) {
    const dd = Math.sqrt((i * i) / (rx * rx) + (j * j) / (ry * ry)); if (dd > 1) continue;
    const lvl = dd < 0.3 ? 3 : dd < 0.62 ? 2 : 1;
    x.fillStyle = 'rgba(' + r0 + ',' + g0 + ',' + b0 + ',' + (a * lvl / 3).toFixed(3) + ')'; x.fillRect(i + rx, j + ry, 1, 1);
  }
  return c;
}
function glow(sp, x, y, k) { g.globalCompositeOperation = 'lighter'; g.globalAlpha = k === undefined ? 1 : k;
  g.drawImage(sp, Math.round(x) - (sp.width >> 1), Math.round(y) - (sp.height >> 1)); g.globalAlpha = 1; g.globalCompositeOperation = 'source-over'; }
const GLC = {};
function glowSp(col, rx, ry, a) { const k = col + rx + ',' + ry + ',' + a; return GLC[k] || (GLC[k] = mkGlow(rx, ry, col, a)); }

/* ---------- offscreen caches: build a static layer once, blit it every frame ---------- */
function layer(w, h, fn) {
  const c = document.createElement('canvas'); c.width = w; c.height = h; const old = g; g = c.getContext('2d');
  try { fn(g); } finally { g = old; } return c;
}
const LBL = new Map();
function label(s, col) { // cached pixel text (one canvas per string + colour)
  s = String(s).toUpperCase(); const k = col + '|' + s; let c = LBL.get(k);
  if (!c) { if (LBL.size > 600) LBL.clear(); c = layer(Math.max(1, textW(s)), 5, () => text(s, 0, 0, col)); LBL.set(k, c); }
  return c;
}
function textL(s, x, y, col) { g.drawImage(label(s, col), Math.round(x), Math.round(y)); }
function textC(s, cx, y, col) { textL(s, cx - textW(s) / 2, y, col); }
function fitText(s, maxW) { s = String(s).toUpperCase(); if (textW(s) <= maxW) return s; while (s.length > 1 && textW(s + '.') > maxW) s = s.slice(0, -1); return s.trimEnd() + '.'; }
function wrapText(s, maxW) { // words onto lines no wider than maxW pixels
  const out = []; let line = '';
  for (const w of String(s).toUpperCase().split(/\s+/).filter(Boolean)) {
    const tryL = line ? line + ' ' + w : w;
    if (textW(tryL) <= maxW) line = tryL; else { if (line) out.push(line); line = fitText(w, maxW); }
  }
  if (line) out.push(line); return out;
}

/* ---------- the five states every room uses: ok green / waiting amber / working cyan / error red / missing grey "-" ---------- */
const ST = Object.freeze({
  ok: { lamp: '#3ddc7a', hi: '#d8ffe6', txt: '#7ff0a8', num: '#8fe3ff', glow: '#3dff8a', shaft: '#9fe8c0', word: 'running fine' },
  waiting: { lamp: '#ffb020', hi: '#fff0c0', txt: '#ffc24a', num: '#ffc24a', glow: '#ff9a1f', shaft: '#ffb84a', word: 'needs you' },
  working: { lamp: '#36d3e6', hi: '#d8fbff', txt: '#8fe3ff', num: '#8fe3ff', glow: '#6fffe0', shaft: '#6fe6ff', word: 'running now' },
  error: { lamp: '#ff4a4a', hi: '#ffd0d0', txt: '#ff7a7a', num: '#ff7a7a', glow: '#ff3a3a', shaft: '#ff5a5a', word: 'something is wrong' },
  missing: { lamp: '#4a4e62', hi: '#6a6e84', txt: '#8a8fa8', num: '#7a7f99', glow: null, shaft: null, word: "can't see it" },
});

/* ---------- people: a 3x7 worker in a hard hat (carry: holding a note) ---------- */
function worker(x, y, dir, phase, carry, shirt) {
  x = Math.round(x); y = Math.round(y);
  const step = Math.floor(phase) % 2;
  const rows = [".h.", "hhh", "fff", "sss", "sss", step ? "l.l" : ".l.", step ? "l.l" : ".l."];
  const map = { h: '#ffcf3a', f: '#f0b58c', s: shirt, l: '#2a2d4a' };
  const pix = [];
  rows.forEach((r, j) => { for (let i = 0; i < 3; i++) if (r[i] !== '.') pix.push([x - 1 + i, y - 6 + j, map[r[i]]]); });
  if (carry) { const nx = dir < 0 ? x - 4 : x + 2;
    for (let j = 0; j < 3; j++) for (let i = 0; i < 3; i++) pix.push([nx + i, y - 4 + j, (j === 1 && i > 0) ? '#7fa8e0' : '#f4f6ff']); }
  R(x - 2, y + 1, 5, 1, 'rgba(0,0,0,0.45)');
  for (const [px, py] of pix) { R(px - 1, py, 3, 1, OL); R(px, py - 1, 1, 3, OL); }
  for (const [px, py, c] of pix) P(px, py, c);
  P(x - 1, y - 3, 'rgba(255,255,255,0.18)');
}

/* ---------- dates: the data holds absolute times only; words like "in 3 d" are counted here (nowMs: town.js) ---------- */
const DOW = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'], MON = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
function toDate(iso) { if (!iso) return null; const d = new Date(/^\d{4}-\d\d-\d\d$/.test(iso) ? iso + 'T12:00:00' : iso); return isNaN(d) ? null : d; }
function dayText(iso) { const d = toDate(iso); return d ? DOW[d.getDay()] + ' ' + d.getDate() + ' ' + MON[d.getMonth()] : '-'; }   // TUE 10 NOV
function timeText(iso) { const d = toDate(iso); return d ? DOW[d.getDay()] + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()) : '-'; } // TUE 07:15
function untilText(iso) { // IN 3D 21H / IN 5H 10M / IN 12M / DUE NOW; 3D 2H AGO for the past
  const d = toDate(iso); if (!d) return '-';
  const ms = d.getTime() - nowMs(), a = Math.abs(ms), dd = Math.floor(a / 864e5), h = Math.floor(a % 864e5 / 36e5), m = Math.floor(a % 36e5 / 6e4);
  if (a < 6e4) return 'DUE NOW';
  const s = dd ? dd + 'D ' + h + 'H' : h ? h + 'H ' + m + 'M' : m + 'M';
  return ms > 0 ? 'IN ' + s : s + ' AGO';
}

/* ---------- sound: WebAudio, synthesised, quiet. Starts on the first click or key (browsers allow nothing sooner).
   On by default; the mute choice is remembered. Only rooms make sound; the town is silent. ---------- */
const SFX = (() => {
  const VOL = 0.16, KEY = 'lamptown-room-muted';
  const inRoom = () => !!(typeof RS === 'object' && RS && RS.id);
  let ac = null, master = null, bus = null, muted = false;
  try { muted = localStorage.getItem(KEY) === '1'; } catch (e) { /* storage blocked: default on */ }
  function unlock() {
    if (!ac) {
      try {
        ac = new (window.AudioContext || window.webkitAudioContext)();
        master = ac.createGain(); master.gain.value = muted ? 0 : VOL;
        const comp = ac.createDynamicsCompressor(); master.connect(comp); comp.connect(ac.destination);
      } catch (e) { ac = null; }
    }
    if (ac && ac.state === 'suspended' && !muted) ac.resume().catch(() => {});
    return ac;
  }
  function out() { if (!ac) return null; if (!bus) { bus = ac.createGain(); bus.connect(master); } return bus; }
  function rest() { if (ac && ac.state === 'running') ac.suspend().catch(() => {}); } // no audio thread while nothing can play
  function hush() { // leaving a room: fade whatever still plays, start a fresh bus for the next room, then rest
    const b = bus; bus = null;
    try { if (b) b.gain.setTargetAtTime(0, ac.currentTime, 0.04); } catch (e) { /* closed */ }
    setTimeout(() => { try { if (b) b.disconnect(); } catch (e) { /* gone */ } if (!inRoom()) rest(); }, 400);
  }
  // hidden (a minimised app): stop the audio thread; back in view inside a room: run again
  document.addEventListener('visibilitychange', () => { if (document.hidden) rest(); else if (ac && inRoom()) unlock(); });
  function ctx() { return ac && !muted ? ac : null; }   // null: no sound now (muted, or no gesture yet)
  function setMuted(m) {
    muted = !!m; try { localStorage.setItem(KEY, muted ? '1' : '0'); } catch (e) { /* not remembered */ }
    if (master) master.gain.setTargetAtTime(muted ? 0 : VOL, ac.currentTime, 0.02);
    if (muted) setTimeout(() => { if (muted) rest(); }, 120); else if (ac && inRoom() && !document.hidden) ac.resume().catch(() => {});
  }
  function tone(t0, f, d, vol, type) {
    if (!ctx()) return; const o = ac.createOscillator(), gn = ac.createGain();
    o.type = type || 'sine'; o.frequency.setValueAtTime(f, t0);
    gn.gain.setValueAtTime(0.0001, t0); gn.gain.linearRampToValueAtTime(vol, t0 + 0.01); gn.gain.exponentialRampToValueAtTime(0.0001, t0 + d);
    o.connect(gn); gn.connect(out()); o.start(t0); o.stop(t0 + d + 0.05);
  }
  function noise(t0, d, vol, freq, type) {
    if (!ctx()) return; const n = Math.max(1, Math.floor(ac.sampleRate * d)), buf = ac.createBuffer(1, n, ac.sampleRate), ch = buf.getChannelData(0);
    for (let i = 0; i < n; i++) ch[i] = Math.random() * 2 - 1;
    const s = ac.createBufferSource(), fl = ac.createBiquadFilter(), gn = ac.createGain(); s.buffer = buf;
    fl.type = type || 'bandpass'; fl.frequency.value = freq; fl.Q.value = 0.9;
    gn.gain.setValueAtTime(vol, t0); gn.gain.exponentialRampToValueAtTime(0.0001, t0 + d);
    s.connect(fl); fl.connect(gn); gn.connect(out()); s.start(t0);
  }
  function door(opening) { // a wooden latch and a soft thud
    if (!ctx()) return; const t = ac.currentTime + 0.02;
    noise(t, 0.05, 0.35, 2400, 'highpass'); tone(t + 0.04, opening ? 110 : 90, 0.22, 0.5, 'triangle'); noise(t + 0.04, 0.18, 0.25, 300, 'lowpass');
  }
  function click() { if (!ctx()) return; tone(ac.currentTime + 0.01, 1300, 0.05, 0.12, 'square'); }
  return { unlock, ctx, out, hush, setMuted, tone, noise, door, click, get muted() { return muted; } };
})();

/* the drawing half of K (town.js adds the navigation half and freezes it) */
const KIT = {
  W, H, OL, ST, R, P, sprite, disc, ellipse, hash, hexRGB, mixHex, rgba, glow, glowSp, mkGlow,
  text, textW, textL, textC, fitText, wrapText, label, layer, worker, plural,
  sfx: SFX, dayText, timeText, untilText,
};
