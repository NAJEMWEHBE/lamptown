/* SHELF: the flag locker, inside the workshop.
   Data: the building's room data from the `skill_dirs` / `staging_dir` collectors (collectors/shelf.py).
   One rolled signal flag per installed item (a skill, plugin or tool: the data's `noun`) in a cubby with
   a brass plate (ok). A folder that can't be read hangs a torn scrap over a red plate (error). A staged
   item waits rolled in a basket on the floor under an empty cubby, with an amber VET tag (waiting). An
   item still being built is a half-sewn flag on the bench under the cyan work lamp (working). No folder:
   the locker is padlocked and dark (missing). Folders another app syncs in (a bundle manifest) sit in
   the sea chest by the mast with their count on its brass plate; hover lifts the lid.
   The flag's pattern and colours come from a hash of the name, in real signal-flag colours: identity
   only, never a status.
   Hover (or Tab to) a cubby: its flag is pulled out, runs up the halyard, unfurls and drops a tag with
   what the item does. Click: open its manifest (a torn one: its folder).
   Scale: 4 columns up to 16 cubbies, 6 up to 30, then 8; 6 rows at most, so 48 cubbies; past that the
   last cubby reads "+N MORE". One basket per column (more staged: "+N" by the last basket); five items
   on the bench (then "+N").
   Demo (?room=<id>&demo=1): fictional SAMPLE flags; &state=ok|waiting|working|error|missing,
   &items=1..80 (sample count), keys 1-5 switch the state. */
(() => {
'use strict';
const { W, H, OL, ST, R, P, sprite, disc, hash, glow, glowSp, rgba, textW, textL, textC, fitText, wrapText, layer, sfx } = K;
const FLOOR = 166;
const NOW_T = () => performance.now() / 1000;
const STAGE_STATE = { installed: 'ok', broken: 'error', staged: 'waiting', building: 'working' };
/* signal-flag cloth: real code flags use only red, white, blue, yellow and black (dark / mid / light) */
const FC = { r: ['#9a2a36', '#d8434e', '#f06a70'], w: ['#aab0c8', '#e9ecf5', '#ffffff'], b: ['#22397a', '#3456b0', '#5a7ad8'],
  y: ['#b8892a', '#f2c14e', '#ffe08a'], k: ['#0e0e18', '#1c1c2c', '#34344a'] };
const PAIRS = [['r', 'w'], ['b', 'w'], ['y', 'b'], ['y', 'r'], ['w', 'k'], ['y', 'k'], ['w', 'r'], ['b', 'y'], ['w', 'b']];
function patIdx(p, u, v) { // which of the two colours at (u, v) in 0..1
  switch (p) {
    case 0: return u < 0.5 ? 0 : 1;                                                   // vertical halves
    case 1: return v < 0.5 ? 0 : 1;                                                   // horizontal halves
    case 2: return (Math.abs(u - 0.5) < 0.11 || Math.abs(v - 0.5) < 0.15) ? 1 : 0;      // cross
    case 3: return (Math.abs(u - v) < 0.13 || Math.abs(u - (1 - v)) < 0.13) ? 1 : 0;    // saltire
    case 4: return (u < 0.2 || u > 0.8 || v < 0.24 || v > 0.76) ? 0 : 1;              // border
    case 5: return ((u < 0.5) !== (v < 0.5)) ? 1 : 0;                                 // quarters
    case 6: return (u > 1 / 3 && u < 2 / 3) ? 1 : 0;                                  // three stripes
    default: return u + v < 1 ? 0 : 1;                                                // diagonal halves
  }
}
function strHash(s) { let h = 2166136261; for (const ch of s) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }

let M = null, CAB = null, CELLS = [], BASKETS = [], BENCH = [], STATIC = null, hoist = null, hov = null;
let demo = null, timers = [], caption = '', cursor = null, demoHov = null, rawData = null, lastDraw = -1, SEEN = {};

/* ---------- data ---------- */
const str = v => (v == null ? '' : String(v));
function mkItem(s, i) {
  s = s && typeof s === 'object' ? s : {};
  const stage = STAGE_STATE[s.stage] ? s.stage : s.state === 'ok' ? 'installed' : s.state === 'waiting' ? 'staged' : s.state === 'working' ? 'building' : 'broken';
  const f = s.flag && typeof s.flag === 'object' ? s.flag : {};
  const id = str(s.id || s.name) || 'item-' + (i + 1);
  const h = strHash(id), pr = PAIRS[h % PAIRS.length];   // no flag in the data (a third-party collector): one from the name
  const ca = FC[f.a] ? f.a : pr[0], cb = FC[f.b] && f.b !== ca ? f.b : (FC[f.a] ? (ca === 'r' ? 'w' : 'r') : pr[1]);
  return { id, name: str(s.name || id).slice(0, 120), short: str(s.short || s.name || id).toUpperCase().slice(0, 12), line: str(s.line).slice(0, 300),
    href: str(s.href), stage, state: STAGE_STATE[stage], pat: Math.abs(parseInt(f.pat === undefined ? (h >>> 8) % 8 : f.pat, 10) || 0) % 8, ca, cb };
}
function sampleItem(id, short, line, stage) { return mkItem({ id, name: id, short, line, stage }, 0); }
/* fictional sample flags for ?demo=1 (never real data) */
const SAMPLE = {
  installed: [['tide-reader', 'TIDE', 'Sample: read a made-up tide table'], ['kite-knots', 'KITE', 'Sample: tie the six knots a paper kite needs'],
    ['moss-indexer', 'MOSS', 'Sample: index notes by date and topic'], ['lantern-check', 'LAMPS', 'Sample: check every lantern before dark'],
    ['paper-fold', 'BOAT', 'Sample: fold a paper boat from one sheet'], ['gull-counter', 'GULLS', 'Sample: count the gulls on the pier'],
    ['rain-gauge', 'RAIN', 'Sample: read the rain gauge each morning'], ['sail-mender', 'SAIL', 'Sample: patch a torn sail, step by step'],
    ['harbour-map', 'HARBOUR', 'Sample: draw the harbour map from notes']],
  staged: [['wren-sketch', 'WREN', 'Sample: sketch a wren in twelve pixels'], ['salt-ledger', 'SALT', 'Sample: keep the salt house ledger']],
  building: [['otter-notes', 'OTTER', 'Sample: being built, no manifest yet']],
  broken: [['old-scraps', 'SCRAPS', "Sample: can't read, no manifest in this folder"]],
};
const noun = () => (M && M.noun) || 'skill';
const NOUN = () => noun().toUpperCase();
function sayOf(m) {
  if (!m.found) return "CAN'T SEE THE " + NOUN() + 'S FOLDER';
  const n = m.installed.length;
  return { ok: n ? 'ALL ' + n + ' INSTALLED' : 'NO ' + NOUN() + 'S INSTALLED YET', waiting: m.staged.length + ' WAITING FOR YOU TO VET', working: m.building.length + ' BEING BUILT',
    error: m.broken.length + " CAN'T BE READ", missing: "CAN'T SEE THE " + NOUN() + 'S FOLDER' }[m.state];
}
function worstOf(m) {
  if (m.broken.length) return 'error'; if (m.staged.length) return 'waiting'; if (m.building.length) return 'working';
  return m.found ? 'ok' : 'missing';
}
function model(data) {
  const items = (Array.isArray(data.rows) ? data.rows : []).map(mkItem), of = k => items.filter(i => i.stage === k);
  const nn = /^[a-z][a-z -]{0,19}$/.test(str(data.noun)) ? str(data.noun) : 'skill';
  const m = { noun: nn, found: data.found !== false && !data.missing && data.state !== 'missing' || items.length > 0, crashed: !!data.error,
    folder: str(data.folder), folderHref: str(data.folderHref), stagingHref: str(data.stagingHref),
    installed: of('installed'), broken: of('broken'), staged: of('staged'), building: of('building'), sample: false,
    bundles: (Array.isArray(data.bundles) ? data.bundles : []).filter(a => a && typeof a === 'object').map(a => ({ name: str(a.name || 'synced').slice(0, 60),
      count: Number.isInteger(a.count) && a.count >= 0 ? a.count : null, names: (Array.isArray(a.names) ? a.names : []).map(str).slice(0, 40), href: str(a.href) })) };
  M = m;   // sayOf reads the noun
  m.state = ST[data.state] ? data.state : worstOf(m);
  m.say = sayOf(m);
  return m;
}
const DEMO_STATES = ['ok', 'waiting', 'working', 'error', 'missing'];
function demoModel(data, st, n) { // sample flags only, padded/cut to &items=N; the state's object gets sample flags
  const m = model({ state: 'ok', found: true, noun: data && data.noun });
  const smp = k => SAMPLE[k].map(a => sampleItem(a[0], a[1], a[2], k));
  let inst = smp('installed');
  if (n > 0) { for (let k = inst.length; k < n; k++) inst.push(sampleItem('sample-' + (k + 1), 'FLAG' + (k + 1), 'Sample flag number ' + (k + 1), 'installed')); inst = inst.slice(0, n); }
  st = DEMO_STATES.includes(st) ? st : 'ok';
  Object.assign(m, { installed: inst, broken: [], staged: [], building: [], found: true, crashed: false, sample: true,
    bundles: [{ name: 'sample sync', count: 3, names: ['RAIN-GAUGE', 'SAIL-MENDER', 'GULL-COUNTER'], href: '' }] });
  if (st === 'error') m.broken = smp('broken');
  if (st === 'waiting') m.staged = smp('staged');
  if (st === 'working') m.building = smp('building');
  if (st === 'missing') Object.assign(m, { found: false, installed: [], bundles: [] });
  [].concat(m.installed, m.broken, m.staged, m.building).forEach((it, k) => { // sample flags: no two alike
    const pr = PAIRS[k % PAIRS.length], rev = Math.floor(k / PAIRS.length) % 2;
    Object.assign(it, { ca: pr[rev], cb: pr[1 - rev], pat: (k * 5 + 1) % 8 });
  });
  m.state = st; m.say = sayOf(m);
  return m;
}

/* ---------- layout: the cubby grid grows with the count (4, 6, then 8 columns; past 48 a "+N MORE" cubby) ---------- */
function layout() {
  const front = M.found ? M.installed.concat(M.broken) : [], sh = M.found ? M.staged.length : 0;
  const base = M.found && !M.crashed ? Math.max(front.length + sh, 8) : 12;
  const cols = base <= 16 ? 4 : base <= 30 ? 6 : 8, cw = cols === 4 ? 36 : cols === 6 ? 24 : 18, ch = cols === 4 ? 26 : cols === 6 ? 22 : 20;
  const SH = Math.min(sh, cols), cap = cols * 6;               // staged holes: one per column, so each basket sits under its hole
  let list = front, more = 0;
  if (front.length + SH > cap) { // too many: hide installed flags only, so a torn one always stays in sight
    const room = cap - SH - 1, br = M.broken.slice(0, room);
    list = M.installed.slice(0, room - br.length).concat(br); more = front.length - list.length;
  }
  const shown = list.length;
  const cells = shown + (more ? 1 : 0) + SH, rows = Math.max(1, Math.ceil(Math.max(cells, Math.min(base, cap)) / cols));
  const w = cols * cw + 6, h = rows * ch + 6, x = 10, y = FLOOR - h;
  CAB = { x, y, w, h, cols, cw, ch, rows, more, extraStaged: sh - SH };
  const slots = new Array(rows * cols).fill(null);
  for (let i = 0; i < shown; i++) slots[i] = list[i];
  if (more) slots[shown] = { stage: 'more', more };
  for (let k = 0; k < SH; k++) slots[rows * cols - SH + k] = M.staged[k];
  CELLS = slots.map((it, i) => {
    const c = i % cols, r = Math.floor(i / cols), cx = x + 3 + c * cw, cy = y + 3 + r * ch, hh = Math.round(ch * 0.5);
    return { x: cx, y: cy, w: cw, h: ch, it, col: c, row: r, hx: cx + 3, hy: cy + 2, hw: cw - 6, hh, py: cy + 2 + hh + 2 };
  });
  BASKETS = CELLS.filter(c => c.it && c.it.stage === 'staged').map(c => { const bw = Math.min(20, c.w - 2); return { it: c.it, x: c.x + Math.round(c.w / 2 - bw / 2), y: FLOOR + 3, w: bw, cell: c }; });
  BENCH = M.found ? M.building.slice(0, 5) : [];
}

/* ---------- drawing helpers ---------- */
function plateFit(s, w) { s = String(s).toUpperCase(); while (s.length > 1 && textW(s) > w - 2) s = s.slice(0, -1); return s; }
function rollEnd(c, it) { // a rolled flag seen end-on in its pigeonhole: a two-colour spiral of cloth + the roll going back in
  const A = FC[it.ca], B = FC[it.cb], r = Math.max(2, Math.min(5, (c.hh >> 1) - 1, Math.floor((c.hw - 6) / 2)));
  const cx = c.hx + r + 1 + Math.floor((c.hw - (2 * r + 6)) / 2), cy = c.hy + (c.hh >> 1);
  R(cx + r + 1, cy - r, 4, r * 2 + 1, OL); disc(cx, cy, r + 1, OL); disc(cx, cy, r, A[1]);
  for (let a = 0; a < 4 * Math.PI; a += 0.12) { const rr = 0.6 + (r - 0.8) * a / (4 * Math.PI); P(cx + Math.round(Math.cos(a) * rr), cy + Math.round(Math.sin(a) * rr), B[1]); }
  P(cx - r + 1, cy - 2, A[2]); P(cx - 2, cy - r + 1, A[2]); P(cx, cy, OL);
  R(cx + r + 1, cy - r + 1, 3, r * 2 - 1, A[0]); R(cx + r + 1, cy - r + 1, 3, 1, A[1]); R(cx + r + 4, cy - r + 2, 1, r * 2 - 3, OL);
}
function emptyHole(c) { R(c.hx, c.hy, c.hw, c.hh, '#120c16'); R(c.hx, c.hy, c.hw, 2, '#0a070e'); R(c.hx, c.hy + c.hh - 1, c.hw, 1, '#2a1c24'); }
function drawFlag(x0, y0, w, h, it, t, vis, torn) { // the flag flying from the halyard, waving; vis = how much has unrolled
  const A = FC[it.ca], B = FC[it.cb], pal = [A, B], wave = K.still ? 0 : 1;
  vis = Math.max(0, Math.min(w, Math.round(vis)));
  for (let i = 0; i < vis; i++) {
    const u = i / (w - 1), ph = t * 5 - i * 0.3, off = Math.round(Math.sin(ph) * 1.6 * u * wave), sl = Math.cos(ph) * u * wave;
    const sh = sl > 0.45 ? 2 : sl < -0.45 ? 0 : 1;
    let top = 0, bot = h;
    if (torn) { const tear = u > 0.55 ? Math.floor(hash(i, 1, 5) * 4 + (u - 0.55) * 46) : 0; top = Math.min(h - 3, Math.floor(tear * 0.5)); bot = h - Math.floor(tear * 0.6); if (u > 0.82 && hash(i, 2, 5) < 0.6) continue; }
    R(x0 + i, y0 + off + top - 1, 1, bot - top + 2, OL);
    for (let j = top; j < bot; j++) {
      const ci = patIdx(it.pat, u, j / (h - 1)); let col = pal[ci][sh];
      if (torn) { col = ci ? '#6a6070' : '#8a5a60'; if (hash(i, j, 8) < 0.06) continue; }
      P(x0 + i, y0 + j + off, col);
    }
  }
  if (vis > 0 && vis < w) { // the still-rolled fly end travelling outward
    const ox = x0 + vis, off = Math.round(Math.sin(t * 5 - vis * 0.3) * 1.6 * (vis / w) * wave);
    R(ox - 1, y0 + off - 1, 5, h + 2, OL); R(ox, y0 + off, 3, h, A[1]); R(ox, y0 + off, 1, h, A[2]); R(ox + 2, y0 + off, 1, h, A[0]);
  }
}

/* ---------- the static room (rebuilt when the data or a demo state changes) ---------- */
const BENCH_X = 186, BENCH_Y = 136, BENCH_W = 92, DOOR = [286, 120, 22], MAST = 330, TOPY = 22, FX = 333, FW = 58, FH = 36;
const STARS = [[156, 4], [171, 7], [188, 3], [203, 6], [219, 4], [236, 7], [244, 3], [164, 5], [228, 5]];
function buildStatic() {
  STATIC = layer(W, H, ctx => {
    const id = ctx.createImageData(W, H), d = id.data, cache = {}, rgb = h => cache[h] || (cache[h] = K.hexRGB(h));
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {   // plum board wall, like the workshop outside; plank floor
      let col; const hh = hash(x, y, 3);
      if (y < 13) col = (x >= 150 && x < 250 && y >= 2 && y < 10) ? '#0d1330' : (y % 4 === 3 ? '#20162a' : '#2a1d34');
      else if (y < FLOOR) {
        const seam = (y - 13) % 6 === 5, vs = ((x + ((Math.floor((y - 13) / 6) % 2) * 9)) % 18) === 0;
        col = seam || vs ? '#24172a' : (hh < 0.2 ? '#33223a' : '#2e1f35');
        if (y > FLOOR - 6) col = y === FLOOR - 6 ? '#4a3040' : '#3a2636';
      } else { const fy = y - FLOOR, seam = fy % 7 === 6, vs = ((x + (Math.floor(fy / 7) * 23)) % 46) === 0; col = seam || vs ? '#1c1418' : (hh < 0.25 ? '#33262a' : '#2c2125'); }
      const [r0, g0, b0] = rgb(col), k = (y * W + x) * 4; d[k] = r0; d[k + 1] = g0; d[k + 2] = b0; d[k + 3] = 255;
    }
    ctx.putImageData(id, 0, 0);
    R(0, 12, W, 3, '#4a3456'); R(0, 12, W, 1, '#5e4470'); R(0, 15, W, 1, '#1a1222');   // rafters
    for (const bx of [148, 250]) R(bx, 0, 3, 12, '#3a2848');
    const on = M.found;
    R(166, 69, 5, 9, OL); R(167, 70, 3, 7, '#8a6a3a'); R(165, 65, 7, 5, OL); R(166, 66, 5, 3, on ? '#ffe7a0' : '#3a3444'); if (on) P(166, 66, '#ffffff');   // brass sconce
    drawCabinet(); drawBaskets(); drawBench(); drawDoor(); drawMast();
    // plain lines on the floor
    const mid = CAB.x + CAB.w / 2;
    if (M.crashed) return;
    if (!M.found) { textC("CAN'T SEE THE " + NOUN() + 'S FOLDER', mid, 194, '#b8bdd8'); return; }
    textC(M.installed.length + ' INSTALLED', mid, 192, '#9aa3d8');
    const pl = (n, w) => n + ' ' + w + (n === 1 ? '' : 'S');
    const second = M.broken.length ? [pl(M.broken.length, 'FOLDER') + " CAN'T BE READ", ST.error.txt] : M.staged.length ? [M.staged.length + ' WAITING FOR VETTING', ST.waiting.txt] : null;
    if (second) textC(second[0], mid, 200, second[1]);
    textC(M.building.length ? M.building.length + ' BEING BUILT' : 'NOTHING BEING BUILT', 232, 172, M.building.length ? ST.working.txt : '#59629a');
    if (M.bundles.length) { const s = 'SYNCED IN'; textC(s, Math.min(CH_X + CH_W / 2, W - 6 - Math.ceil(textW(s) / 2)), 172, '#9aa3d8'); }
  });
}
function drawCabinet() {
  const { x, y, w, h } = CAB, off = !M.found;
  R(x - 2, y - 4, w + 4, h + 4, OL);
  R(x - 1, y - 3, w + 2, 3, off ? '#4a4048' : '#7a5038'); R(x - 1, y - 3, w + 2, 1, off ? '#5a5058' : '#a06a48');   // cornice
  R(x, y, w, h, off ? '#3a3038' : '#5e3b2a');
  for (const c of CELLS) {
    const { hx, hy, hw, hh, py } = c, it = c.it;
    R(hx - 1, hy - 1, hw + 2, hh + 2, off ? '#2a2228' : '#4a2d20'); emptyHole(c);
    if (off) { R(hx, py, hw, 7, '#3e3e4a'); textC('-', hx + hw / 2, py + 1, '#7a7f99'); continue; }
    if (!it) { R(hx, hy, hw, hh, '#4a2f26'); for (let k = 2; k < hh; k += 3) R(hx, hy + k, hw, 1, '#3a241e'); continue; }   // unused: boarded, never a missing item
    if (it.stage === 'more') { // never cut a count: "+14", else "14"; the plate says MORE, or ">>" where that can't fit
      const fit = (opts) => opts.find(o => textW(o) <= hw) || opts[opts.length - 1];
      textC(fit(['+' + it.more, String(it.more)]), hx + hw / 2, hy + Math.max(1, (hh >> 1) - 2), '#ffd36e');
      R(hx, py, hw, 7, '#7a5420'); textC(fit(['MORE', '>>']), hx + hw / 2, py + 1, '#2a1808'); continue; }
    if (it.stage === 'installed') {
      rollEnd(c, it);
      R(hx, py, hw, 7, '#b8893a'); R(hx, py, hw, 1, '#e8c070'); R(hx, py + 6, hw, 1, '#7a5420');   // brass plate
      const nm = plateFit(it.short, hw + 1);   // up to the plate's full width; rivets only where the name leaves room
      if (textW(nm) <= hw - 8) { P(hx + 1, py + 3, '#7a5420'); P(hx + hw - 2, py + 3, '#7a5420'); }
      textC(nm, hx + hw / 2, py + 1, '#2a1808');
    } else if (it.stage === 'broken') { // red enamel plate; the torn scrap hanging out is drawn per frame
      R(hx, py, hw, 7, '#5a1a22'); R(hx, py, hw, 1, '#8a2a32'); textC(plateFit(it.short, hw + 1), hx + hw / 2, py + 1, '#ffb0b0');
    } else if (it.stage === 'staged') { // empty hole, no brass yet: its flag is in the basket below
      R(hx, py, hw, 7, '#2a1c24'); for (let k = 1; k < hw - 1; k += 2) P(hx + k, py + 3, '#4a3438');
    }
  }
  if (off) { // padlocked: doors shut over the whole grid
    R(x, y, w, h, '#3a3440'); const mid = x + (w >> 1), my = y + (h >> 1);
    for (let k = 0; k < h; k += 5) R(x, y + k, w, 1, '#2c2834');
    R(mid - 1, y, 2, h, '#1a161e'); R(x, y, w, 1, '#4a4450');
    R(mid - 5, my - 1, 10, 9, OL); R(mid - 4, my, 8, 7, '#6a6e84'); R(mid - 4, my, 8, 1, '#8a8ea4'); R(mid - 1, my + 2, 2, 3, '#2a2a3a');
    R(mid - 3, my - 5, 1, 5, '#6a6e84'); R(mid + 2, my - 5, 1, 5, '#6a6e84'); R(mid - 3, my - 6, 6, 1, '#6a6e84');
  }
}
function drawBaskets() { // a rolled flag standing in a wicker basket under its empty cubby (the VET tag swings per frame)
  BASKETS.forEach((b, i) => {
    const { it, x: bx, y: by, w: bw } = b, A = FC[it.ca], B = FC[it.cb], fx = bx + Math.round(bw / 2) - 5;
    R(fx, by - 9, 7, 12, OL); R(fx + 1, by - 8, 5, 11, A[1]); R(fx + 1, by - 8, 1, 11, A[2]); R(fx + 4, by - 8, 2, 11, B[1]); R(fx + 1, by - 8, 5, 1, '#d8c89a');
    R(fx + 5, by - 6, 5, 9, OL); R(fx + 6, by - 5, 3, 8, A[0]);
    R(bx, by, bw, 12, OL); R(bx + 1, by + 1, bw - 2, 10, '#8a6438');
    for (let j = 0; j < 10; j++) for (let k = 0; k < bw - 2; k++) if ((k + (j % 2) * 2) % 4 === 0 || j % 3 === 2) P(bx + 1 + k, by + 1 + j, '#6a4a28');
    R(bx + 1, by + 1, bw - 2, 1, '#b08a50');
    P(bx + (bw >> 1) - 2, by + 11, '#c8b48a'); P(bx + (bw >> 1) - 2, by + 12, '#c8b48a');   // the tag's string
    if (i === BASKETS.length - 1 && CAB.extraStaged > 0) textL('+' + CAB.extraStaged, bx + bw + 3, by + 3, ST.waiting.txt);
  });
}
function drawBench() {
  const x = BENCH_X, y = BENCH_Y, w = BENCH_W, bld = BENCH[0], on = !!bld;
  R(x + 46, 15, 1, 82, '#2a2236');   // hanging work lamp (cyan while something is being built)
  R(x + 38, 96, 17, 2, OL); R(x + 39, 97, 15, 5, on ? '#2a6a78' : '#3a3444'); R(x + 41, 95, 11, 2, on ? '#3a8a98' : '#4a4454'); R(x + 39, 102, 15, 1, OL);
  R(x + 43, 103, 7, 2, on ? '#bff8ff' : '#2a2632');
  if (on) { g.fillStyle = 'rgba(80,230,255,0.08)'; for (let j = 0; j < 34; j++) { const hw = 7 + j * 0.9; g.fillRect(Math.round(x + 46 - hw), 104 + j, Math.round(hw * 2), 1); } glow(glowSp('#36d3e6', 40, 30, 0.3), x + 46, 132, 1); }
  R(x - 1, y - 1, w + 2, 8, OL); R(x, y, w, 4, '#7a5038'); R(x, y, w, 1, '#a06a48'); R(x, y + 4, w, 2, '#4a2d20');
  for (const lx of [x + 3, x + w - 7]) { R(lx - 1, y + 6, 6, FLOOR - y - 5, OL); R(lx, y + 6, 4, FLOOR - y - 6, '#5e3b2a'); R(lx, y + 6, 1, FLOOR - y - 6, '#7a5038'); }
  R(x + 7, y + 20, w - 14, 2, '#4a2d20');
  if (on) { // half-sewn flag on the bench: left half sewn, right half a chalk outline (the needle works per frame)
    const fx = x + 18, fy = y - 9, fw = 44, fh = 9, A = FC[bld.ca], B = FC[bld.cb], half = 22;
    R(fx - 1, fy - 1, half + 1, fh + 2, OL);
    for (let i = 0; i < half; i++) for (let j = 0; j < fh; j++) P(fx + i, fy + j, [A, B][patIdx(bld.pat, i / (fw - 1), j / (fh - 1))][1]);
    for (let i = half; i < fw; i += 2) { P(fx + i, fy, '#cfeaf0'); P(fx + i, fy + fh - 1, '#cfeaf0'); }
    for (let j = 0; j < fh; j += 2) { P(fx + fw - 1, fy + j, '#cfeaf0'); P(fx + half - 1, fy + j, '#ffffff'); }
    R(x + 70, y - 6, 6, 6, OL); R(x + 71, y - 5, 4, 4, B[1]); R(x + 71, y - 6, 4, 1, '#c8a070'); R(x + 71, y - 1, 4, 1, '#c8a070');   // thread spool
    const nm = fitText(bld.short, 60), lw = textW(nm) + 4; R(x + w / 2 - lw / 2, y + 9, lw, 7, '#cfeaf0'); textC(nm, x + w / 2, y + 10, '#1a3a44');   // paper label
    BENCH.slice(1).forEach((b, i) => { // more being built: folded cloth on the shelf under the bench
      const bx = x + 12 + i * 17, by = y + 15, A2 = FC[b.ca], B2 = FC[b.cb];
      R(bx - 1, by - 1, 14, 6, OL); R(bx, by, 12, 4, A2[1]); R(bx, by + 2, 12, 1, B2[1]); R(bx, by, 12, 1, A2[2]);
    });
    if (M.building.length > BENCH.length) textL('+' + (M.building.length - BENCH.length), x + w - 18, y + 15, ST.working.txt);
  } else { // empty bench: tools put away, dust
    R(x + 66, y - 3, 10, 3, '#5a4a40'); R(x + 66, y - 3, 10, 1, '#7a6a58');
    for (let i = 0; i < 9; i++) P(x + 8 + i * 9, y, '#8a6a58');
  }
}
function drawDoor() {
  const [x, y, w] = DOOR;
  R(x - 2, y - 2, w + 4, FLOOR - y + 2, OL); R(x - 1, y - 1, w + 2, FLOOR - y + 1, '#4a2d20'); R(x, y, w, FLOOR - y, '#5e3b2a');
  R(x + 3, y + 3, 7, 16, '#4a2d20'); R(x + 12, y + 3, 7, 16, '#4a2d20'); R(x + 3, y + 22, 7, 20, '#4a2d20'); R(x + 12, y + 22, 7, 20, '#4a2d20');
  R(x + w - 4, y + 24, 2, 2, '#ffd36e'); R(x + 2, y - 1, w - 4, 1, 'rgba(255,211,110,0.5)');
  const s = '< TOWN', sw = textW(s) + 8, sx = Math.round(x + w / 2 - sw / 2), sy = y - 14;
  R(sx, sy, sw, 9, OL); R(sx + 1, sy + 1, sw - 2, 7, '#161b38'); R(sx + 1, sy + 1, sw - 2, 1, '#2b336a'); textL(s, sx + 4, sy + 2, '#ffd36e');
}
function drawMast() {
  R(MAST - 1, 15, 4, FLOOR - 15, OL); R(MAST, 15, 2, FLOOR - 15, '#7a5a40'); R(MAST, 15, 1, FLOOR - 15, '#9a7a58');
  R(MAST - 3, 15, 8, 4, OL); R(MAST - 2, 16, 6, 2, '#8a8aa0');                  // block under the rafter
  R(MAST + 2, 19, 1, 128, '#c8b48a'); R(MAST - 1, 19, 1, 128, '#a89470');       // halyard, up and down
  R(MAST - 3, 146, 8, 2, OL); R(MAST - 2, 146, 6, 1, '#8a8aa0');                // cleat
  R(MAST - 5, FLOOR - 2, 12, 3, OL); R(MAST - 4, FLOOR - 2, 10, 2, '#5e3b2a');
}

/* ---------- the sea chest by the mast: items another app syncs in (drawn per frame: its lid lifts on hover) ---------- */
const CH_X = 346, CH_W = 44, CHEST = { stage: 'bundle' };
function bundleCount() { const a = M.bundles; if (!a.length) return null; return a.some(x => x.count === null) ? null : a.reduce((n, x) => n + x.count, 0); }
function drawChest(open) {
  if (!M.found || !M.bundles.length) return;
  const x = CH_X, w = CH_W, by = FLOOR - 12, lift = open ? 4 : 0, cols = ['r', 'b', 'y', 'w', 'k', 'y', 'r'];
  if (open) cols.forEach((c, k) => { const fx = x + 4 + k * 5 + (k % 2), h = 5 + (k % 3); R(fx - 1, by - h - 1, 6, h + 2, OL); R(fx, by - h, 4, h + 1, FC[c][1]); R(fx, by - h, 1, h + 1, FC[c][2]); });   // rolled flags inside
  R(x - 1, by - 1, w + 2, 14, OL); R(x, by, w, 12, '#6a4228'); R(x, by + 5, w, 1, '#55331f'); R(x, by, w, 1, '#8a5a38');   // planked body
  for (const bx of [x + 4, x + w - 6]) { R(bx, by, 2, 12, '#b8893a'); R(bx, by, 1, 12, '#e8c070'); }                        // brass bands
  R(x - 3, by + 4, 2, 3, '#c8b48a'); R(x + w + 1, by + 4, 2, 3, '#c8b48a');                                                // rope handles
  if (!open) { R(x + 12, by, 3, 3, FC.r[1]); R(x + 22, by, 4, 2, FC.b[1]); R(x + 30, by, 3, 3, FC.y[1]); }               // cloth corners caught under the lid
  const n = bundleCount(), num = n === null ? '-' : String(n), pw = Math.max(14, textW(num) + 6), px = Math.round(x + w / 2 - pw / 2);
  R(px - 1, by + 3, pw + 2, 8, OL); R(px, by + 4, pw, 6, '#b8893a'); R(px, by + 4, pw, 1, '#e8c070'); textC(num, x + w / 2, by + 4, '#2a1808');   // count plate
  const ly = by - 6 - lift;   // the lid (lifted a little while hovered)
  R(x - 1, ly - 2, w + 2, 8, OL); R(x, ly, w, 5, '#7a5038'); R(x + 2, ly - 1, w - 4, 1, '#7a5038'); R(x + 1, ly, w - 2, 1, '#a06a48');
  for (const bx of [x + 4, x + w - 6]) R(bx, ly - 1, 2, 6, '#b8893a');
  if (!open) { R(x + w / 2 - 1, ly + 3, 3, 4, '#e8c070'); P(x + w / 2, ly + 5, OL); }   // hasp
}

/* ---------- the hoist: the roll leaves its cubby, runs up the halyard, unfurls, the tag drops ---------- */
const T_TOSS = 0.35, T_RISE = 1.0, T_UNF = 0.45, T_TAG = 0.3, T_ALL = T_TOSS + T_RISE + T_UNF + T_TAG, DWELL = 0.15;
const hoistable = it => it && (it.stage === 'installed' || it.stage === 'broken');
function startHoist(it) {
  if (!hoistable(it) || (hoist && hoist.it === it)) return;
  const c = CELLS.find(cc => cc.it === it); if (!c) return;
  hoist = { it, t0: NOW_T(), sx: c.x + c.w / 2, sy: c.y + Math.round(c.h * 0.35) };
  sfxHoist(it.stage === 'broken');
}
function drawHoist(t) {
  if (!hoist) return;
  const it = hoist.it, d = K.still ? T_ALL : NOW_T() - hoist.t0, A = FC[it.ca], torn = it.stage === 'broken';
  const bundle = (x, y) => { R(x - 1, y - 1, 6, 12, OL); R(x, y, 4, 10, torn ? '#8a5a60' : A[1]); R(x, y, 1, 10, torn ? '#aa7a80' : A[2]); R(x, y + 4, 4, 1, '#d8c89a'); };
  if (d < T_TOSS) { const u = d / T_TOSS; bundle(hoist.sx + (MAST + 3 - hoist.sx) * u, hoist.sy + (140 - hoist.sy) * u - Math.sin(u * Math.PI) * 24); SEEN.hoist = 'toss'; return; }
  if (d < T_TOSS + T_RISE) { const u = (d - T_TOSS) / T_RISE, e = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2; bundle(MAST + 3, 140 - (140 - TOPY) * e); SEEN.hoist = 'rise'; return; }
  const du = d - T_TOSS - T_RISE, vis = Math.min(1, du / T_UNF);
  drawFlag(FX, TOPY, FW, FH, it, t, FW * (1 - Math.pow(1 - vis, 3)), torn);
  R(FX - 1, TOPY, 1, FH, '#d8c89a');
  SEEN.hoist = 'flying';
  if (du > T_UNF * 0.7) { // the tag drops on a string from the flag's foot: the item's name and its one plain line
    const k = Math.min(1, (du - T_UNF * 0.7) / T_TAG), tw = 92, lines = wrapText(it.line || '-', tw - 8).slice(0, 4), th = 12 + lines.length * 7;
    const ax = FX + 10, tx = W - tw - 4, ty = TOPY + FH + 4, sway = K.still ? 0 : Math.round(Math.sin(t * 2) * 0.6);
    g.save(); g.beginPath(); g.rect(0, TOPY + FH + 2, W, th + 24); g.clip();
    const yy = ty - Math.round((1 - k) * (th + 10));
    R(ax, TOPY + FH + 1, 1, yy - TOPY - FH, '#c8b48a');
    R(tx - 1 + sway, yy - 1, tw + 2, th + 2, OL); R(tx + sway, yy, tw, th, '#161b38'); R(tx + sway, yy, tw, 1, torn ? '#ff4a4a' : '#2b336a');
    textL(fitText(torn ? it.name : it.name.replace(/[-_]/g, ' '), tw - 8), tx + 4 + sway, yy + 3, torn ? '#ff8a8a' : '#ffd36e');
    lines.forEach((l, i) => textL(l, tx + 4 + sway, yy + 11 + i * 7, '#dfe3ff'));
    g.restore();
    SEEN.tag = it.name;
  }
}

/* ---------- sound: synthesised, quiet ---------- */
function sfxHoist(torn) {
  const ac = sfx.ctx(), out = sfx.out(); if (!ac || !out) return;
  const t0 = ac.currentTime + 0.02, still = K.still, toss = still ? 0 : T_TOSS, rise = still ? 0 : T_RISE;
  sfx.noise(t0, 0.12, 0.25, 2200, 'bandpass');                                                   // pulled from the cubby
  if (!still) for (let i = 0; i < 9; i++) sfx.tone(t0 + toss + i * 0.11, 1500 + i * 60, 0.025, 0.06, 'square');   // the halyard block's ratchet
  const tu = t0 + toss + rise;
  sfx.noise(tu, 0.35, 0.45, torn ? 500 : 900, 'bandpass'); sfx.noise(tu + 0.12, 0.25, 0.25, torn ? 400 : 1300, 'bandpass');   // the cloth flaps open
  if (!torn) { sfx.tone(tu + 0.3, 1318.5, 0.6, 0.16, 'sine'); sfx.tone(tu + 0.3, 2637, 0.4, 0.04, 'sine'); }   // a small bell: all is well
  else { // a sour, sagging note: this one can't be read
    const o = ac.createOscillator(), gn = ac.createGain(), s = tu + 0.3; o.type = 'sawtooth';
    o.frequency.setValueAtTime(220, s); o.frequency.exponentialRampToValueAtTime(140, s + 0.35);
    gn.gain.setValueAtTime(0.0001, s); gn.gain.linearRampToValueAtTime(0.1, s + 0.02); gn.gain.exponentialRampToValueAtTime(0.0001, s + 0.38);
    o.connect(gn); gn.connect(out); o.start(s); o.stop(s + 0.42);
  }
}

/* ---------- per frame ---------- */
function statusLamp(x, y, st, on) { R(x - 3, y - 3, 7, 7, OL); R(x - 2, y - 2, 5, 5, on ? ST[st].lamp : (st === 'missing' ? ST.missing.lamp : rgba(ST[st].lamp, 0.35))); if (on && st !== 'missing') { P(x - 2, y - 2, ST[st].hi); P(x - 1, y - 2, ST[st].hi); } }
function hovered() { // what the pointer (or Tab) is on now: the town reads a target's tip only while it shows it
  if (demoHov) return demoHov;
  return live() ? hov.it : null;
}
// a hover is live while the town keeps reading its tip: read since the room's previous frame (any frame rate)
function live() { return !!hov && hov.last >= lastDraw; }
function drawRoom(t) {
  g.drawImage(STATIC, 0, 0);
  const still = K.still, lit = M.found;
  for (let i = 0; i < STARS.length; i++) if (still || Math.sin(t * 1.3 + i * 2.1) > -0.4) P(STARS[i][0], STARS[i][1], i % 3 ? '#9fb4ff' : '#ffffff');   // skylight
  glow(glowSp('#7fa8ff', 40, 10, 0.12), 200, 14, 1);
  if (lit) { glow(glowSp('#ffb45a', 70, 40, 0.16), 92, 92, 1); glow(glowSp('#ffb45a', 16, 8, 0.25), 168, 67, 1); }
  // header: lamp + one plain line under the name plate (the plate in the top bar carries the installed count)
  const st = M.state, blink = still ? true : st === 'waiting' ? (t % 1.1) < 0.7 : st === 'error' ? (t % 0.5) < 0.3 : true;
  if (!M.crashed) {
    statusLamp(14, 21, st, blink);
    if (blink && ST[st].glow) glow(glowSp(ST[st].glow, 10, 10, 0.45), 14, 21, 1);
    textL(fitText(M.say, 150), 22, 19, ST[st].txt);
  }
  // per-frame bits of the locker: the hoisted cubby is empty, torn scraps sway, the hovered cubby is outlined
  const hv = hovered();
  for (const c of CELLS) {
    const it = c.it; if (!it || !M.found) continue;
    const flying = hoist && hoist.it === it;
    if (flying) emptyHole(c);
    else if (it.stage === 'broken') {
      const sw = still ? 0 : Math.round(Math.sin(t * 2) * 0.6);
      for (let i = 0; i < c.hw - 10; i++) { const len = Math.min(c.hh + 4, 4 + Math.floor(hash(i, 3, 2) * 7) + (i > 4 && i < 8 ? -3 : 0));
        R(c.hx + 5 + i + sw, c.hy + 3, 1, len + 1, OL); R(c.hx + 5 + i + sw, c.hy + 3, 1, len, (i % 5 < 2) ? '#8a5a60' : '#6a6070'); }
    }
    if (hv === it) { const { hx, hy, hw, hh } = c; R(hx - 1, hy - 1, hw + 2, 1, '#ffd36e'); R(hx - 1, hy + hh, hw + 2, 1, '#ffd36e'); R(hx - 1, hy - 1, 1, hh + 2, '#ffd36e'); R(hx + hw, hy - 1, 1, hh + 2, '#ffd36e'); }
  }
  for (const b of BASKETS) { // the amber VET tag on its string
    const sw = still ? 0 : Math.round(Math.sin(t * 2.2 + b.x) * 0.6), tx = b.x + (b.w >> 1) - 7 + sw, ty = b.y + 13;
    R(tx - 1, ty, 15, 9, OL); R(tx, ty + 1, 13, 7, ST.waiting.lamp); R(tx, ty + 1, 13, 1, '#ffd36e'); textL('VET', tx + 1, ty + 2, '#3a2208');
    if (hv === b.it) R(b.x - 1, b.y - 10, b.w + 2, 1, '#ffd36e');
  }
  if (BENCH.length) { // the needle working along the seam
    const fx = BENCH_X + 18, fy = BENCH_Y - 9, sx = fx + 22 + (still ? 1 : Math.floor(t * 4) % 3), up = !still && Math.sin(t * 8) > 0;
    R(sx, fy + 3 - (up ? 3 : 0), 1, 5, '#e8eef8'); P(sx, fy + 2 - (up ? 3 : 0), '#9aa0b8');
  }
  if (!hoist && lit && !M.crashed && CELLS.some(c => hoistable(c.it))) textC('HOVER A CUBBY', MAST + 34, 60, '#59629a');
  drawChest(hv === CHEST);
  drawHoist(t);
  if (st === 'error' && blink && !still) glow(glowSp('#ff3a3a', 60, 40, 0.10), 84, 110, 1);   // the room's red pulse
  if (!M.found) { // dark: no folder to light
    g.fillStyle = 'rgba(6,8,20,0.5)'; g.fillRect(0, 12, W, H - 12);
    textC("CAN'T SEE THE " + NOUN() + 'S FOLDER', CAB.x + CAB.w / 2, 194, '#b8bdd8'); textC('-', MAST + 30, 60, '#59629a');
    statusLamp(14, 21, 'missing', true); textL(fitText(M.say, 150), 22, 19, ST.missing.txt);
  }
  if (M.sample) { const s = 'SAMPLE: MADE-UP FLAGS', sw = textW(s) + 8, y = H - 13; R(4, y, sw, 9, OL); R(5, y + 1, sw - 2, 7, '#3a2208'); textL(s, 8, y + 2, '#ffd36e'); }
}

/* what the last frame showed (read by the tests: every moving or lit thing follows the data) */
function snapshot() {
  const cubbies = { installed: 0, broken: 0, staged: 0, more: 0, empty: 0 };
  for (const c of CELLS) { if (!c.it) cubbies.empty++; else cubbies[c.it.stage === 'more' ? 'more' : c.it.stage]++; }
  return Object.assign({ state: M.state, say: M.say, sample: M.sample, found: M.found, padlocked: !M.found, cols: CAB.cols, rows: CAB.rows,
    cubbies, more: CAB.more, baskets: BASKETS.length, extraStaged: CAB.extraStaged, bench: BENCH.length, benchMore: Math.max(0, M.building.length - BENCH.length),
    workLamp: BENCH.length > 0, chest: M.found && M.bundles.length > 0, chestCount: bundleCount(), hoisting: hoist ? hoist.it.name : null }, SEEN);
}

/* ---------- hover / click targets ---------- */
const WORD = { installed: 'installed', broken: "can't read", staged: 'staged, waiting for you to vet', building: 'being built' };
function tipOf(it) {
  const mf = 'its manifest';
  const more = it.href ? { installed: 'Click: open ' + mf, broken: 'Click: open its folder', staged: 'Click: open ' + mf + ' in staging', building: 'Click: open its staging folder' }[it.stage] : (M.sample ? 'Sample flag (demo)' : '');
  return [it.name + ': ' + WORD[it.stage], it.line].concat(more ? [more] : []).filter(Boolean);
}
function openItem(it) {
  sfx.click();
  if (!it.href) { K.toast(M.sample ? 'SAMPLE FLAG: NOTHING TO OPEN' : 'NO FILE FOR ' + it.short); return; }
  K.open(it.href);
  K.toast('OPENING ' + it.short + (it.stage === 'broken' || it.stage === 'building' ? "'S FOLDER" : "'S MANIFEST"));
}
function watched(tg, it, tip) { // the town reads `tip` every frame it shows this target (hover or Tab): that is when a flag goes up
  Object.defineProperty(tg, 'tip', { enumerable: true, get() {
    const n = NOW_T();
    if (!hov || hov.it !== it) hov = { it, since: n, last: n }; else hov.last = n;
    return tip();
  } });
  return tg;
}
function itemTarget(it, box) {
  return watched({ id: 'item-' + it.stage + '-' + it.id, box, label: it.name + ', ' + WORD[it.stage] + '. ' + it.line + '. Open it.', onClick: () => { startHoist(it); openItem(it); } }, it, () => tipOf(it));
}
function chestTarget() {
  const n = bundleCount(), names = [].concat(...M.bundles.map(a => a.names)), k = 5, cnt = n === null ? "can't count them" : n + ' ' + noun() + (n === 1 ? '' : 's');
  const list = names.length ? names.slice(0, k).join(', ') + (names.length > k ? ', +' + (names.length - k) + ' more' : '') : '';
  const href = (M.bundles.find(a => a.href) || {}).href || '';
  return watched({ id: 'chest', box: [CH_X - 4, FLOOR - 24, CH_W + 8, 30], label: 'Sea chest: ' + cnt + ' synced in by another app. Open the folder.',
    onClick: () => { sfx.click(); if (href) { K.open(href); K.toast('OPENING THE SYNCED FOLDER'); } else K.toast(M.sample ? 'SAMPLE CHEST: NOTHING TO OPEN' : 'NO FOLDER TO OPEN'); } },
  CHEST, () => ['Synced in: ' + cnt, list, 'Another app keeps these up to date: shown as they are', href ? 'Click: open the folder' : ''].filter(Boolean));
}
function targets() {
  const out = [];
  if (!M.found) {
    out.push({ id: 'padlock', box: [CAB.x, CAB.y, CAB.w, CAB.h], tip: ["The " + noun() + "s folder: can't see it", M.folder || 'No folder set up', 'Nothing to show until it is back.'], label: 'The locker is padlocked: the folder can\'t be found.', onClick: () => K.toast(NOUN() + 'S FOLDER NOT FOUND. NOTHING TO OPEN.') });
  } else {
    for (const c of CELLS) {
      const it = c.it; if (!it || it.stage === 'staged') continue;
      if (it.stage === 'more') { out.push({ id: 'more', box: [c.x, c.y, c.w, c.h], tip: ['+' + it.more + ' more installed ' + noun() + 's', 'The locker shows ' + (CELLS.filter(cc => cc.it && cc.it.stage !== 'more').length) + '.', M.folderHref ? 'Click: open the folder' : ''].filter(Boolean), label: it.more + ' more. Open the folder.', onClick: () => { if (M.folderHref) { sfx.click(); K.open(M.folderHref); K.toast('OPENING THE FOLDER'); } } }); continue; }
      out.push(itemTarget(it, [c.x, c.y, c.w, c.h]));
    }
    for (const b of BASKETS) out.push(itemTarget(b.it, [b.x - 1, FLOOR - 7, b.w + 2, 32]));
    BENCH.forEach((it, i) => out.push(itemTarget(it, i ? [BENCH_X + 11 + (i - 1) * 17, BENCH_Y + 14, 15, 7] : [BENCH_X + 16, BENCH_Y - 11, 48, 28])));
    if (M.bundles.length) out.push(chestTarget());
    if (hoist && (K.still || NOW_T() - hoist.t0 > T_TOSS + T_RISE)) out.push(itemTarget(hoist.it, [FX - 2, TOPY - 2, FW + 4, FH + 4]));
  }
  const [dx, dy, dw] = DOOR;
  out.push({ id: 'door', box: [dx - 6, dy - 15, dw + 12, FLOOR - dy + 15], tip: ['Back to town', 'or press Esc'], label: 'Door: back to town', onClick: K.leave });
  return out;
}

/* ---------- demo (?demo=1): the core moment on a timer, for screenshots and first-time viewers ---------- */
const INTRO = { ok: 'EVERY ITEM IS A ROLLED FLAG IN ITS CUBBY', waiting: 'NEW FLAGS WAIT IN BASKETS: VET THEM', working: 'A NEW FLAG IS BEING SEWN ON THE BENCH',
  error: "A TORN FLAG: THIS FOLDER CAN'T BE READ", missing: 'THE LOCKER IS PADLOCKED: NO FOLDER' };
function drawCursor(x, y) {
  const rows = ['#.....', '##....', '#.#...', '#..#..', '#...#.', '#..###', '#.#...', '##....'];
  sprite(rows, x + 1, y + 1, { '#': OL }); sprite(rows, x, y, { '#': '#f4f6ff' });
}
function demoSeq() {
  const inst = M.installed.filter(i => CELLS.some(c => c.it === i));
  let seq = ['kite-knots', 'lantern-check', 'tide-reader', 'harbour-map'].map(id => inst.find(i => i.id === id)).filter(Boolean);
  if (seq.length < 2) seq = inst.slice(0, 4);
  if (M.state === 'error' && M.broken.length) seq = [M.broken[0]].concat(seq.slice(0, 2));
  return seq;
}
function runDemo() {
  timers.forEach(clearTimeout); timers = []; demoHov = null; cursor = null; caption = '';
  const at = (s, f) => timers.push(setTimeout(f, Math.max(0, s * 1000)));
  const st = M.state, obj = !M.found ? [CAB.x + CAB.w / 2, CAB.y + CAB.h / 2] : st === 'waiting' && BASKETS.length ? [BASKETS[0].x + 10, FLOOR + 8]
    : st === 'working' && BENCH.length ? [BENCH_X + 40, BENCH_Y - 4] : null;
  at(0.4, () => { caption = INTRO[st] || ''; if (obj) cursor = [obj[0] + 6, obj[1] + 4]; });
  const seq = demoSeq(); if (!seq.length) return;
  // timed so a 7 s headless screenshot shows the flag up with its tag
  const step = (k, s) => {
    const it = seq[k % seq.length], c = CELLS.find(cc => cc.it === it); if (!c) return;
    at(s, () => { cursor = [c.x + c.w - 9, c.y + 7]; demoHov = it; hoist = null; startHoist(it); caption = 'HOVER A CUBBY: ITS FLAG GOES UP'; });
    at(s + T_TOSS + T_RISE, () => { caption = it.stage === 'broken' ? 'TORN: NOTHING TO READ IN THIS FOLDER' : 'IT UNFURLS: WHAT IT DOES IS ON THE TAG'; });
    at(s + 1.2, () => { cursor = null; demoHov = null; });
    if (k < 11) at(s + 6.5, () => step(k + 1, 0));
  };
  step(0, 2.6);
}
function drawDemoLayer() {
  if (caption) { const cw = textW(caption) + 12, x = Math.round(232 - cw / 2); R(x - 1, 180, cw + 2, 13, OL); R(x, 181, cw, 11, '#161b38'); R(x, 181, cw, 1, '#ffd36e'); textL(caption, x + 6, 184, '#ffd36e'); }
  if (cursor) drawCursor(cursor[0], cursor[1]);
}

/* ---------- the room ---------- */
function setup() {
  const n = demo ? Math.max(0, Math.min(80, parseInt(demo.qs.get('items'), 10) || 0)) : 0;
  M = demo ? demoModel(rawData, demo.state, n) : model(rawData);
  layout(); STATIC = null; hoist = null; hov = null; demoHov = null; lastDraw = -1; SEEN = {};
}
registerRoom('shelf', {
  enter(data, opts) {
    rawData = data && typeof data === 'object' ? data : { state: 'missing', missing: true };
    demo = opts.demo ? { state: DEMO_STATES.includes(opts.state) ? opts.state : 'ok', qs: opts.qs } : null;
    setup(); buildStatic();
    if (demo) runDemo();
  },
  leave() { timers.forEach(clearTimeout); timers = []; hoist = null; hov = null; demoHov = null; STATIC = null; caption = ''; cursor = null; },
  draw(ctx, t) {
    if (!STATIC) buildStatic();
    const n = NOW_T();
    SEEN = {};
    if (hov && !live()) hov = null;   // the pointer left: coming back starts a new dwell
    const hv = !demo && hov && n - hov.since >= DWELL ? hov.it : null;   // a short dwell: sweeping the pointer across doesn't fire every flag
    if (hv && hoistable(hv)) startHoist(hv);
    drawRoom(t);
    lastDraw = n;
    if (demo) drawDemoLayer();
  },
  targets,
  hover() {},
  plate() { return { num: M.found ? String(M.installed.length) : '-', state: M.state }; },
  busy() { // a still (reduced-motion) scene keeps redrawing while a hover waits out its dwell, or the demo runs
    const n = NOW_T();
    return (live() && hoistable(hov.it) && !(hoist && hoist.it === hov.it)) || (live() && hov.it === CHEST && n - hov.since < 0.3) || (timers.length > 0 && !!demo) || (!!hoist && !K.still && n - hoist.t0 < T_ALL);
  },
  key(e) {
    if (!demo) return;
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= DEMO_STATES.length) { demo.state = DEMO_STATES[n - 1]; setup(); runDemo(); }
  },
  seen: () => (M ? snapshot() : null),
});
})();
