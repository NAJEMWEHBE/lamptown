/* JOBS: the trumpeter's room, inside the clock tower.
   Data: the building's room data, `rows` (one per scheduled job) from the `log_glob` collector
   (collectors/jobs.py) or agent-status files (`kind = "job"`).
   One window + one trumpet per job. The state reads from the lantern over each window and the light it
   throws on the floor, not from the trumpet:
     ok = an open window on a night sea, the trumpet on its hook;
     waiting = the same, with the "!" bubble over it;
     working = the trumpeter stands at that window and keeps playing soft notes (no trumpet on the hook);
     error = the trumpet lies dented on the floor among torn music (a missed run: on the hook, red lantern);
     missing (no run on record) = boarded shutters, cobweb, an empty hook.
   Click a window: the trumpeter climbs up and plays that job's last result:
     ok = the full call; waiting = a call that ends on a question; working = soft repeated notes;
     failed = two notes, then it cracks; missed / never ran = silence and a shrug.
   Click a slate (or the plate under a window): open that job's log.
   The clock shows the real time; the plaque names the next call, flagged in that job's banner colour.
   Scale: 1-2 jobs = two big arches with chalk slates; 3-8 = a row of narrower windows with name plates;
   past 8, a chalk board by the clock reads "+N MORE" (its tooltip lists them, worst first).
   Demo (?room=<id>&demo=1): fictional SAMPLE jobs; &state=ok|waiting|working|error|missing|missed,
   &jobs=1..12 (sample count), keys 1-6 switch the state. */
(() => {
'use strict';
const { W, H, OL, ST, R, P, sprite, disc, hash, glow, glowSp, rgba, textW, textL, textC, fitText, layer, sfx } = K;
const FLOOR = 188;
const DOOR = [362, 146, 28, 42];
const MAX_WIN = 8;
const PALETTE = ['#c48dff', '#ff9a6a', '#7fd8ff', '#b8f07a', '#ffd36e', '#ff8a9a', '#5fe0c0', '#d9d4ff'];
const SAY = { ok: 'RAN FINE', waiting: 'NEEDS YOU', working: 'RUNNING NOW', error: 'LAST RUN FAILED', missing: 'NO RUN ON RECORD' };
const WHY_SAY = { missed: 'MISSED ITS RUN', stopped: 'STOPPED HALF-WAY', nosource: "CAN'T SEE ITS LOGS", unreadable: "CAN'T READ ITS LOG" };
const WORST = ['error', 'waiting', 'working', 'missing', 'ok'];
const NOW_T = () => performance.now() / 1000;

let JOBS = [], EXTRA = [], LAY = null, STATIC = null, action = null, pending = null, particles = [], demo = null, timers = [], sample = false, total = 0;

/* ---------- data ---------- */
function shade(hex, k) { return '#' + K.hexRGB(hex).map(v => Math.max(0, Math.min(255, Math.round(v * k))).toString(16).padStart(2, '0')).join(''); }
const str = v => (v === null || v === undefined ? '' : String(v));
function mkJob(j, i) {
  j = j && typeof j === 'object' ? j : {};
  const col = /^#[0-9a-f]{6}$/i.test(j.colour || '') ? j.colour : PALETTE[i % PALETTE.length];
  const state = ST[j.state] ? j.state : 'missing', name = str(j.name || j.id) || 'Job ' + (i + 1), why = str(j.why);
  const short = str(j.short || name).toUpperCase(), when = str(j.when || j.schedule);
  return { id: str(j.id) || 'job-' + i, name, short, tag: short.split(/\s+/)[0] || 'JOB',
    when, whenShort: str(j.when_short || when).toUpperCase(), feeds: str(j.feeds),
    next: j.next || null, last: j.last || null, result: str(j.result || j.message), state, why,
    say: (state === 'missing' && why === 'nosource') || state === 'error' ? WHY_SAY[why] || SAY[state] : SAY[state],
    href: str(j.href), banner: col, bannerD: shade(col, 0.72) };
}
const SAMPLE_JOBS = [ // fictional jobs for ?demo=1: [name, short, weekday, hour, result]
  ['Kite photo backup', 'KITE BACKUP', 0, 3, '62 files copied, 0 failed'],
  ['Tidebook index', 'TIDE INDEX', 3, 8, 'Chapter 9 of 9 indexed'],
  ['Moss digest', 'MOSS DIGEST', 6, 21, 'Digest written for Wren'],
  ['Idea box sort', 'IDEA BOX', 1, 10, '17 ideas read, 0 new'],
  ['Lantern log trim', 'LOG TRIM', 5, 18, 'Old logs trimmed, 4 MB freed'],
  ['Pier photo cleanup', 'PIER PHOTOS', 4, 22, 'Approved frames tidied, 120 MB freed'],
  ['Otter letter', 'OTTER LETTER', 2, 9, 'Letter sent to Otter'],
  ['Heron count', 'HERON COUNT', 0, 6, '14 herons counted'],
  ['Fog bank check', 'FOG CHECK', 1, 5, 'No fog on the tide road'],
  ['Kite string test', 'STRING TEST', 3, 15, '6 knots held'],
  ['Shell sorting', 'SHELLS', 4, 11, '31 shells sorted'],
  ['Map redraw', 'MAP REDRAW', 6, 7, 'Map redrawn, 2 new paths'],
];
function sampleJob(i) {
  const [name, short, dow, hour, res] = SAMPLE_JOBS[i % SAMPLE_JOBS.length], now = new Date(K.nowMs());
  const next = new Date(now); next.setHours(hour, 0, 0, 0); next.setDate(next.getDate() + ((dow - now.getDay() + 7) % 7)); if (next <= now) next.setDate(next.getDate() + 7);
  const last = new Date(next.getTime() - 7 * 864e5);
  const days = ['Sundays', 'Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays'], hh = String(hour).padStart(2, '0') + ':00';
  return { id: 'sample-' + i, name, short, when: days[dow] + ' ' + hh, when_short: 'EVERY ' + days[dow].slice(0, 3).toUpperCase() + ' ' + hh,
    next: next.toISOString(), last: last.toISOString(), result: res, state: 'ok', why: 'ok' };
}
const DEMO_STATES = ['ok', 'waiting', 'working', 'error', 'missing', 'missed'];
function force(jobs, st) { // a demo state goes to ONE job (the one it makes sense for); the others run fine
  if (!jobs.length) return;
  for (const j of jobs) Object.assign(j, { state: 'ok', why: 'ok', say: SAY.ok });
  const j = jobs[st === 'waiting' || st === 'working' ? 0 : jobs.length - 1];
  if (st === 'waiting') Object.assign(j, { state: 'waiting', why: 'waiting', say: '3 IDEAS WAIT FOR YOU', result: '3 ideas to sort' });
  else if (st === 'working') Object.assign(j, { state: 'working', why: 'running', say: SAY.working, result: 'Running now: its log has no done line yet.' });
  else if (st === 'error') Object.assign(j, { state: 'error', why: 'failed', say: SAY.error, result: '17 checked, 1 failed' });
  else if (st === 'missed') Object.assign(j, { state: 'error', why: 'missed', say: WHY_SAY.missed, result: 'Missed its run: the newest log is 9 days old.' });
  else if (st === 'missing') Object.assign(j, { state: 'missing', why: 'never', say: SAY.missing, last: null, result: 'No run on record yet.' });
}
const callOf = j => (j.state === 'missing' || j.why === 'missed') ? 'missing' : j.state;   // which call he plays
const fallen = j => j.state === 'error' && j.why !== 'missed';                           // a failed run drops the trumpet
const worstOf = list => WORST.find(s => list.some(j => j.state === s)) || 'missing';

/* ---------- layout: 1-2 jobs = two big arches; 3-8 = a row of windows ---------- */
function wideWin(k) {
  const m = x => k ? 400 - x : x, mr = (x, w) => k ? 400 - x - w : x, ox = mr(112, 51), ow = 51;
  return { k, ox, ow, cx: ox + (ow - 1) / 2, top: 41, bot: 150, archY: 66, r: 25, side: k ? -1 : 1, compact: false,
    slate: [mr(4, 80), 62, 80, 50], plate: null, hook: [m(95), 60], lantern: [k ? ox - 3 : ox + ow + 2, 62],
    crates: [[mr(165, 22), 172, 22, 16], [mr(165, 14), 160, 14, 12]], floorT: [k ? m(95) - 10 : m(95) - 8, 183],
    play: { x: m(150), y: 150, dir: k ? 1 : -1 }, path: [[m(191), FLOOR], [m(178), 172], [m(171), 160], [m(158), 150], [m(150), 150]] };
}
function layout(n) {
  if (n <= 2) {
    const w = [wideWin(0), wideWin(1)];
    return { wins: w.slice(0, Math.max(n, 0)), spare: n < 2 ? w.slice(n) : [], home: { x: 200, y: FLOOR }, plaque: [170, 68, 60, 27], more: null, candle: true };
  }
  n = Math.min(n, MAX_WIN);
  const x0 = 8, x1 = 354, sw = (x1 - x0) / n;
  let ow = Math.max(25, Math.min(45, Math.floor(sw) - 12)); if (ow % 2 === 0) ow--;
  const wins = [];
  for (let i = 0; i < n; i++) {
    const sl = x0 + sw * i, ox = Math.round(sl + (sw - ow) / 2), cx = ox + (ow - 1) / 2, r = (ow - 1) / 2, top = 76, bot = 148;
    const side = cx < 200 ? 1 : -1, c = Math.round(cx), px = Math.round(cx + 8 * side);
    wins.push({ k: side < 0 ? 1 : 0, ox, ow, cx, top, bot, archY: Math.round(top + r), r, side, compact: true,
      slate: null, plate: [Math.round(sl) + 2, bot + 7, Math.floor(sw) - 4, 9], hook: [c - 4 * side, Math.round(top + r) - 4],
      lantern: [c, top - 7], crates: [[c - 7, 176, 14, 12]], floorT: [side > 0 ? c + 9 : c - 24, 184],
      play: { x: px, y: bot, dir: -side }, path: [[px + 12 * side, FLOOR], [c + 2 * side, 176], [px, bot]] });
  }
  // the stool goes in the gap between two windows nearest the middle
  let hx = 200, best = 1e9; for (let k = 1; k < n; k++) { const b = x0 + sw * k; if (Math.abs(b - 200) < best) { best = Math.abs(b - 200); hx = Math.round(b); } }
  return { wins, spare: [], home: { x: hx, y: FLOOR }, plaque: [236, 22, 60, 27], more: [300, 24, 56, 23], candle: false };
}

/* ---------- static layer (rebuilt when the data or a demo state changes) ---------- */
function buildStatic() {
  STATIC = layer(W, H, () => {
    for (let y = 0; y < FLOOR; y++) for (let x = 0; x < W; x++) {   // brick wall: plum, darker than the tower outside
      const row = Math.floor(y / 4), off = (row % 2) * 4; let c;
      if (y % 4 === 3 || (x + off) % 8 === 7) c = '#24192c';
      else { const h = hash(Math.floor((x + off) / 8), row, 3); c = h < 0.33 ? '#3a2944' : h < 0.66 ? '#33253e' : '#2f2238'; if (hash(x, y, 5) < 0.03) c = '#40304c'; }
      const dx = (x - 200) / 210, dy = (y - 100) / 140, d = dx * dx + dy * dy;   // darker toward the corners
      if (d > 0.75 && hash(x, y, 11) < (d - 0.75) * 1.6) c = '#1e1526';
      P(x, y, c);
    }
    R(0, 0, W, 10, '#1f1720'); R(0, 9, W, 2, OL);                      // ceiling beams (the HUD sits on them)
    for (let x = 0; x < W; x += 40) { R(x + 2, 0, 3, 9, '#3a2a2c'); R(x + 2, 0, 1, 9, '#4e3a36'); }
    R(0, 6, W, 3, '#3d2b2a'); R(0, 6, W, 1, '#5a4038');
    R(0, FLOOR, W, H - FLOOR, '#3d2a30'); R(0, FLOOR, W, 1, '#1a1018'); R(0, FLOOR + 1, W, 1, '#5a3e40');   // floor boards
    for (let y = FLOOR + 2; y < H; y++) for (let x = 0; x < W; x++) {
      const plank = Math.floor((y - FLOOR - 2) / 6);
      if ((y - FLOOR - 2) % 6 === 5 || (x + plank * 37) % 53 === 0) P(x, y, '#2a1c22'); else if (hash(x, y, 2) < 0.06) P(x, y, '#46303a');
    }
    R(0, FLOOR - 3, W, 3, '#24192c'); R(0, FLOOR - 3, W, 1, '#3e2c48');   // skirting
    LAY.wins.forEach((wn, i) => drawWindowStatic(wn, JOBS[i]));
    LAY.spare.forEach(wn => drawWindowStatic(wn, null));
    if (LAY.candle) { R(190, 128, 20, 3, '#5e3b2a'); R(190, 128, 20, 1, '#7a5038'); R(193, 131, 2, 3, '#4a2d20'); R(205, 131, 2, 3, '#4a2d20');
      R(198, 121, 4, 7, '#e8dcc8'); R(198, 121, 1, 7, '#fff6e0'); R(199, 120, 1, 1, '#2a2020'); }
    const sx = LAY.home.x - 9;                                         // the trumpeter's stool
    R(sx, 176, 18, 3, OL); R(sx + 1, 176, 16, 2, '#7a5038'); R(sx + 1, 176, 16, 1, '#9a6a48');
    R(sx + 2, 178, 2, 10, '#4a2d20'); R(sx + 14, 178, 2, 10, '#4a2d20'); R(sx + 4, 183, 10, 1, '#4a2d20');
    if (LAY.more && EXTRA.length) drawMore();
    const [dx, dy, dw, dh] = DOOR;                                     // the door back to town
    R(dx - 2, dy - 2, dw + 4, dh + 2, OL); R(dx - 1, dy - 1, dw + 2, dh + 1, '#5b3f63'); R(dx, dy, dw, dh, '#5e3b2a');
    for (let i = 4; i < dw; i += 5) R(dx + i, dy, 1, dh, '#4a2d20');
    R(dx, dy + 8, dw, 1, '#3a2218'); R(dx, dy + dh - 9, dw, 1, '#3a2218'); R(dx + dw - 6, dy + 20, 2, 2, '#ffcf4a');
    const sw = textW('< TOWN') + 8, sx2 = Math.round(dx + dw / 2 - sw / 2);
    R(sx2, dy - 13, sw, 9, OL); R(sx2 + 1, dy - 12, sw - 2, 7, '#161b38'); R(sx2 + 1, dy - 12, sw - 2, 1, '#2b336a');
    textL('< TOWN', sx2 + 4, dy - 11, '#ffd36e');
  });
}
function drawMore() { // the chalk board for the jobs past the eighth window
  const [x, y, w, h] = LAY.more, st = worstOf(EXTRA);
  R(x + w / 2 - 1, y - 6, 2, 6, '#3a2d3a'); P(x + w / 2, y - 7, '#8a7a9a');
  R(x - 1, y - 1, w + 2, h + 2, OL); R(x, y, w, h, '#7a5038'); R(x, y, w, 1, '#a07048'); R(x + 2, y + 2, w - 4, h - 4, '#232838');
  for (let i = 0; i < 10; i++) P(x + 3 + Math.floor(hash(i, x, 1) * (w - 6)), y + 3 + Math.floor(hash(x, i, 2) * (h - 6)), '#2c3246');
  textC('+' + EXTRA.length + ' MORE', x + w / 2, y + 5, '#e8ecf6');
  const n = EXTRA.filter(j => j.state === st).length, word = { ok: 'ALL RAN FINE', waiting: 'NEED YOU', working: 'RUNNING', error: 'FAILED', missing: 'NEVER RAN' }[st];
  textC(fitText(st === 'ok' ? word : n + ' ' + (n === 1 && st === 'waiting' ? 'NEEDS YOU' : word), w - 6), x + w / 2, y + 13, st === 'ok' ? '#b9c0d8' : ST[st].txt);
  shape(x + w - 6, y + 6, st);
}
function drawWindowStatic(wn, job) {
  const { ox, ow, top, bot, archY, r, cx } = wn, st = job ? job.state : 'missing', shut = !job || st === 'missing';
  const inArch = (x, y) => y >= archY ? (x >= ox && x < ox + ow && y < bot) : ((x - cx) ** 2 + (y - archY) ** 2 <= r * r);
  for (let y = top - 4; y < bot + 1; y++) for (let x = ox - 4; x < ox + ow + 4; x++) {   // stone frame
    if (inArch(x, y)) continue;
    if (!(y >= archY ? true : ((x - cx) ** 2 + (y - archY) ** 2 <= (r + 4) ** 2))) continue;
    const edge = !(y >= archY ? (x > ox - 4 && x < ox + ow + 3) : ((x - cx) ** 2 + (y - archY) ** 2 <= (r + 3) ** 2));
    P(x, y, edge ? OL : (hash(x >> 1, y >> 1, 4) < 0.5 ? '#6e5078' : '#5b3f63'));
  }
  for (let y = top; y < bot; y++) for (let x = ox; x < ox + ow; x++) {                  // the opening
    if (!inArch(x, y)) continue; let c;
    if (shut) { const plank = Math.floor((x - ox) / 6); c = (x - ox) % 6 === 5 ? '#160f16' : (hash(plank, y >> 2, 8) < 0.5 ? '#2a1d24' : '#251a20'); if (Math.abs(x - cx) < 1) c = '#120b12'; }
    else {
      const sea = bot - 16;
      if (y < sea) { const v = (y - top) / (sea - top); c = v < 0.3 ? '#0f1630' : v < 0.65 ? '#131c3c' : '#18244a'; if (hash(x, y, 7) < 0.012) c = '#cfd8ff'; else if (hash(x, y, 9) < 0.02) c = '#5a6aa8'; }
      else if (y === sea) c = '#2a3a6a';
      else { c = y - sea < 4 ? '#173c62' : (hash(x, y, 1) < 0.5 ? '#112d4e' : '#102a48'); if ((x * 7 + y * 3) % 23 === 0) c = '#22517a'; }
      if (x < ox + 3 && y > archY) c = '#2a1f34'; if (x > ox + ow - 4 && y > archY) c = '#20172a';
    }
    P(x, y, c);
  }
  if (!shut) {
    for (let i = 0; i < Math.max(3, ow >> 3); i++) P(ox + 5 + Math.floor(hash(i, ox, 3) * (ow - 10)), bot - 17, '#ffd36e');   // far-shore lights
    if (wn.k === 1 && !wn.compact) { disc(ox + ow - 14, top + 16, 5, '#e8ecf6'); disc(ox + ow - 12, top + 15, 4, '#d0d8f0'); P(ox + ow - 16, top + 14, '#b8c0dc'); }
    R(ox, archY + 18, ow, 1, '#1a1424'); R(Math.round(cx), top + 2, 1, bot - top - 2, '#1a1424');    // iron cross bars
  } else {
    const wy = archY - 6, rr = Math.min(13, ow >> 1);   // cobweb + latch on the closed shutters
    for (const an of [0.25, 0.75, 1.2]) for (let k = 0; k < rr; k++) P(ox + Math.round(Math.cos(an) * k), wy + Math.round(Math.sin(an) * k), '#7a7f99');
    for (const q of [4, 8, 12]) if (q < rr) for (let an = 0.25; an <= 1.2; an += 0.08) P(ox + Math.round(Math.cos(an) * q * 0.92), wy + Math.round(Math.sin(an) * q * 0.92), '#55596e');
    R(Math.round(cx) - 2, archY + 20, 5, 4, '#3a2a20'); R(Math.round(cx) - 1, archY + 21, 3, 2, '#5a4a40');
  }
  R(ox - 6, bot, ow + 12, 6, OL); R(ox - 5, bot, ow + 10, 5, '#7d5d86'); R(ox - 5, bot, ow + 10, 1, '#9a7aa4'); R(ox - 5, bot + 4, ow + 10, 1, '#4a3253');   // sill
  if (shut) for (let x = ox - 4; x < ox + ow + 4; x++) if (hash(x, 3, 6) < 0.55) P(x, bot, '#a9aec8');
  for (const [x, y, w, h] of wn.crates) {                                              // crates: the steps up
    R(x - 1, y - 1, w + 2, h + 1, OL); R(x, y, w, h, '#7a5038'); R(x, y, w, 1, '#a07048');
    R(x, y + h - 1, w, 1, '#4a2d20'); R(x, y, 1, h, '#8a5c40'); R(x + w - 1, y, 1, h, '#4a2d20');
    for (let i = 1; i < h - 1; i++) P(x + Math.round(i * (w - 2) / (h - 2)), y + i, '#5e3b2a');
    if (shut) for (let i = 0; i < w; i++) if (hash(x + i, y, 2) < 0.6) P(x + i, y, '#9aa0b8');
  }
  if (!job) return;
  if (!shut) {                                                                         // light pool on the floor, in the state's colour
    const col = ST[st].shaft, a = st === 'ok' ? 0.07 : 0.12, s = wn.side;
    g.globalCompositeOperation = 'lighter';
    for (let y = FLOOR; y < H - 6; y++) { const u = (y - bot - 6) / (H - bot - 12), l = ox + 4 - u * 18 * s - u * 4, rr = ox + ow - 4 - u * 18 * s + u * 4; R(l, y, rr - l, 1, rgba(col, (a * (1 - u * 0.6)).toFixed(3))); }
    g.globalCompositeOperation = 'source-over';
  }
  if (wn.slate) drawSlate(wn, job); else drawPlate(wn, job);
}
function shape(x, y, st) { // tiny status mark, so colour is never the only cue
  const c = ST[st].lamp;
  if (st === 'ok') R(x - 1, y - 1, 3, 3, c);
  else if (st === 'waiting') { P(x, y - 2, c); R(x, y - 1, 1, 2, c); P(x, y + 2, c); }
  else if (st === 'working') { P(x - 1, y - 1, c); P(x + 1, y + 1, c); P(x + 1, y - 1, '#2a6a78'); P(x - 1, y + 1, '#2a6a78'); }
  else if (st === 'error') { P(x - 1, y - 1, c); P(x + 1, y - 1, c); P(x, y, c); P(x - 1, y + 1, c); P(x + 1, y + 1, c); }
  else R(x - 1, y, 3, 1, '#8a8fa8');
}
function drawSlate(wn, job) { // chalk slate with the job's facts
  const [sx, sy, sw, sh] = wn.slate, st = job.state, shut = st === 'missing', c = ST[st];
  R(sx - 1, sy - 1, sw + 2, sh + 2, OL); R(sx, sy, sw, sh, '#7a5038'); R(sx, sy, sw, 1, '#a07048'); R(sx, sy + sh - 1, sw, 1, '#4a2d20');
  R(sx + 2, sy + 2, sw - 4, sh - 4, shut ? '#1d1f2a' : '#232838');
  for (let i = 0; i < 18; i++) P(sx + 3 + Math.floor(hash(i, sx, 1) * (sw - 6)), sy + 3 + Math.floor(hash(sx, i, 2) * (sh - 6)), shut ? '#25283a' : '#2c3246');
  const chalk = shut ? '#7a7f99' : '#e8ecf6', chalk2 = shut ? '#5a5e74' : '#b9c0d8', tw = sw - 16;
  const name = fitText(job.short, tw); textL(name, sx + 5, sy + 5, chalk); R(sx + 5, sy + 11, textW(name), 1, shut ? '#3a3e52' : '#5a6078');
  textL(fitText(job.whenShort || 'NO SCHEDULE', sw - 10), sx + 5, sy + 15, chalk2);
  textL(fitText('NEXT ' + (job.next ? K.dayText(job.next) : '-'), sw - 10), sx + 5, sy + 23, chalk2);
  textL(fitText(st === 'working' ? 'NOW RUNNING' : 'LAST ' + (job.last ? K.dayText(job.last) : '-'), sw - 10), sx + 5, sy + 31, chalk2);
  textL(fitText(job.result || '-', sw - 10), sx + 5, sy + 39, st === 'ok' || st === 'waiting' || job.why === 'missed' ? chalk : c.txt);
  const line = fitText(job.say, 110), lw = textW(line) + 9, lx = Math.round(sx + sw / 2 - lw / 2);   // the plain line under the slate
  R(lx - 1, sy + sh + 4, lw + 2, 9, OL); R(lx, sy + sh + 5, lw, 7, '#161b38'); textL(line, lx + 7, sy + sh + 6, c.txt); shape(lx + 2, sy + sh + 8, st);
}
function drawPlate(wn, job) { // name plate under the sill (3+ jobs) and the next run in chalk
  const [px, py, pw, ph] = wn.plate, st = job.state, c = ST[st], room = pw - 11;
  R(px, py, pw, ph, OL); R(px + 1, py + 1, pw - 2, ph - 2, '#161b38'); R(px + 1, py + 1, pw - 2, 1, '#2b336a');
  shape(px + 5, py + 4, st);
  const nm = textW(job.short) <= room ? job.short : (job.tag && textW(job.tag) <= room ? job.tag : fitText(job.short, room));
  textL(nm, px + 9, py + 2, st === 'missing' ? '#a9aec8' : '#eef1ff');
  textC(job.next ? K.dayText(job.next) : '-', px + pw / 2, py + ph + 2, st === 'missing' ? '#5a5e74' : '#9aa3d8');
  R(px + 2, py + ph - 2, pw - 4, 1, c.lamp);
}

/* ---------- trumpets, the trumpeter ---------- */
const BR = '#ffcf4a', BRD = '#c08a2a', BRL = '#fff0a8';
function hookTrumpet(x, y, job, t) {
  R(x - 1, y - 1, 3, 2, '#8a7a9a'); P(x, y - 2, '#b0a4c0'); R(x, y + 1, 1, 2, '#3a2d3a');
  const parts = [[-1, 3, 3, 2, BRD], [0, 5, 2, 17, BR], [-2, 9, 6, 6, BRD], [-1, 22, 4, 1, BR], [-2, 23, 6, 1, BR], [-3, 24, 8, 1, BR], [-4, 25, 10, 2, BRL]];
  for (const [dx, dy, w, h] of parts) R(x + dx - 1, y + dy - 1, w + 2, h + 2, OL);
  for (const [dx, dy, w, h, c] of parts) R(x + dx, y + dy, w, h, c);
  R(x, y + 5, 1, 17, BRL); R(x - 1, y + 10, 1, 4, BR); R(x + 2, y + 10, 1, 4, BR); R(x - 4, y + 26, 10, 1, BRD);
  const right = x < 200, bx = x + (right ? 3 : -11), sway = K.still ? 0 : Math.round(Math.sin(t * 1.3 + x) * 0.6);   // the job's banner
  R(bx - 1, y + 4, 11, 13, OL); R(bx, y + 5, 9, 11, job.banner); R(bx, y + 14, 9, 1, job.bannerD);
  for (let i = 0; i < 9; i += 2) P(bx + i + sway, y + 16, job.bannerD);
  R(bx + 3, y + 7, 3, 1, '#fff6e0'); R(bx + 4, y + 7, 1, 5, '#fff6e0'); R(bx + 3, y + 11, 3, 1, '#fff6e0');
}
function emptyHook(x, y) {
  R(x - 1, y - 1, 3, 2, '#5a5266'); P(x, y - 2, '#6a627a');
  for (let j = 3; j < 27; j++) for (let i = -4; i <= 5; i++) if (hash(x + i, y + j, 4) < 0.18) P(x + i, y + j, '#4a3e56');
  for (let i = -4; i < 6; i++) if (hash(i, y, 6) < 0.6) P(x + i, y + 28, '#6a6e84');
}
function floorTrumpet(x, y, papers) { // fallen, dented, torn music around it
  const parts = [[0, 0, 12, 2, BR], [3, -2, 4, 2, BRD], [12, -2, 1, 6, BR], [13, -3, 2, 8, BRL]];
  for (const [dx, dy, w, h] of parts) R(x + dx - 1, y + dy - 1, w + 2, h + 2, OL);
  for (const [dx, dy, w, h, c] of parts) R(x + dx, y + dy, w, h, c);
  R(x, y, 12, 1, BRL); P(x + 8, y + 1, '#8a5a1a'); P(x + 9, y, '#8a5a1a');
  if (papers) for (const [px, py, w, h, rot] of [[-12, 0, 6, 5, 0], [-5, 3, 4, 3, 1], [17, 1, 5, 4, 0], [22, -2, 3, 3, 1]]) {
    R(x + px - 1, y + py - 1, w + 2, h + 2, OL); R(x + px, y + py, w, h, '#d9d4ff');
    for (let k = 1; k < h; k += 2) R(x + px, y + py + k, w, 1, '#8a8fb0');
    P(x + px + w - 1, y + py, OL); if (rot) P(x + px, y + py + h - 1, OL); P(x + px + 1, y + py + 1, '#2a2a44');
  }
}
const TP = { coat: '#c79bff', coatD: '#9a6fd6', pants: '#3a3e5e', shoe: '#1a1424', face: '#f0c09a', eye: '#1a1a30', hat: '#2b336a', hatD: '#1d2244', gold: '#ffcf4a', plume: '#ff6b7a', collar: '#e8ecf6' };
function drawTrumpeter(x, y, dir, pose, ph, opt) {
  opt = opt || {};
  const parts = [], add = (dx, dy, w, h, c) => parts.push([dx, dy, w, h, c]);
  const sit = pose === 'sit', walk = pose === 'walk', play = pose === 'play' || pose === 'hold', shrug = pose === 'shrug', b = sit ? 4 : 0;
  if (sit) { add(-2, -10, 6, 2, TP.pants); add(3, -8, 2, 6, TP.pants); add(3, -2, 4, 1, TP.shoe); add(-1, -10, 6, 2, TP.pants); }
  else {
    const k = walk ? (Math.floor(ph * 8) % 2) : -1;
    add(-3, -5 - (k === 0 ? 1 : 0), 2, 4, TP.pants); add(1, -5 - (k === 1 ? 1 : 0), 2, 4, TP.pants);
    add(-3, -1 - (k === 0 ? 1 : 0), 3, 1, TP.shoe); add(1, -1 - (k === 1 ? 1 : 0), 3, 1, TP.shoe);
  }
  add(-4, -13 + b, 8, 8, TP.coat); add(-4, -13 + b, 2, 8, TP.coatD); add(-4, -6 + b, 8, 1, TP.coatD);
  add(-4, -9 + b, 8, 1, '#3a2d3a'); add(0, -9 + b, 1, 1, TP.gold); add(1, -12 + b, 1, 1, TP.gold); add(1, -7 + b, 1, 1, TP.gold);
  add(-3, -14 + b, 6, 1, TP.collar); add(-3, -19 + b, 6, 5, TP.face); add(-3, -18 + b, 1, 3, '#5a3a2a'); add(3, -16 + b, 1, 1, TP.face);
  if (sit) add(0, -17 + b, 2, 1, TP.eye); else add(1, -17 + b, 1, 1, TP.eye);
  if (opt.puff) add(1, -16 + b, 3, 2, '#ffb0a0');
  add(-3, -23 + b, 6, 3, TP.hat); add(-5, -20 + b, 10, 1, TP.hatD); add(-3, -21 + b, 6, 1, TP.gold); add(-5, -25 + b, 2, 3, TP.plume);
  if (play) {
    const lift = pose === 'hold' ? 2 : 0;
    add(-1, -13 + b, 3, 2, TP.coatD); add(1, -15 + b + lift, 3, 3, TP.coatD); add(4, -16 + b + lift, 2, 2, TP.face);
    add(3, -16 + b + lift, 9, 1, BR); add(5, -15 + b + lift, 5, 1, BRD); add(6, -18 + b + lift, 1, 2, BRD); add(8, -18 + b + lift, 1, 2, BRD);
    add(12, -17 + b + lift, 1, 3, BR); add(13, -18 + b + lift, 1, 5, BR); add(14, -19 + b + lift, 1, 7, BRL);
  } else if (shrug) {
    add(-6, -15, 2, 4, TP.coatD); add(4, -15, 2, 4, TP.coatD); add(-6, -16, 2, 1, TP.face); add(4, -16, 2, 1, TP.face);
  } else { const sw = walk ? (Math.floor(ph * 8) % 2 ? 1 : -1) : 0; add(-1 + sw, -12 + b, 2, 6, TP.coatD); add(-1 + sw, -6 + b, 2, 1, TP.face); }
  const X = (dx, w) => dir === 1 ? x + dx : x - dx - w + 1, ol = opt.rim || OL;
  for (const [dx, dy, w, h] of parts) R(X(dx, w) - 1, y + dy - 1, w + 2, h + 2, ol);
  for (const [dx, dy, w, h, c] of parts) R(X(dx, w), y + dy, w, h, c);
  return { bellX: x + dir * 14, bellY: y - 16 + b };
}
function bubble(lx, ly, t, ch, col) { // the town's "!" bubble (needs you); "?" for a silent call
  const bob = K.still || Math.sin(t * 3) > 0 ? 0 : 1, bx = lx - 6, by = ly - 14 + bob;
  R(bx + 1, by, 11, 12, OL); R(bx, by + 1, 13, 10, OL); R(bx + 2, by + 1, 9, 10, '#f4f6ff'); R(bx + 1, by + 2, 11, 8, '#f4f6ff');
  if (ch === '!') { R(bx + 5, by + 2, 3, 5, col); R(bx + 5, by + 8, 3, 2, col); R(bx + 6, by + 2, 1, 5, '#fff0c0'); glow(glowSp('#ff9a1f', 12, 12, 0.45), bx + 6, by + 6); }
  else if (ch === 'ok') R(bx + 5, by + 4, 3, 3, col);
  else textL(ch, bx + 5, by + 3, col);
}

/* ---------- actions: walk to a window, step up, play, come back ---------- */
function restPose() {
  const hw = JOBS.findIndex(j => j.state === 'working');
  if (hw >= 0 && LAY.wins[hw]) { const p = LAY.wins[hw].play; return { x: p.x, y: p.y, dir: p.dir, pose: 'hold', at: hw }; }
  return { x: LAY.home.x, y: LAY.home.y, dir: 1, pose: 'sit', at: -1 };
}
function plan(wi) {
  const rp = restPose(); let pts = [[rp.x, rp.y]];
  if (rp.at !== wi) { if (rp.at >= 0) pts = pts.concat(LAY.wins[rp.at].path.slice().reverse().slice(1)); pts = pts.concat(LAY.wins[wi].path); }
  const segs = []; let t = 0;
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1], hop = a[1] !== b[1];
    const d = K.still ? 0.001 : hop ? 0.32 : Math.max(0.15, Math.hypot(b[0] - a[0], b[1] - a[1]) / 34);   // reduced motion: no walking
    segs.push({ a, b, hop, t0: t, d }); t += d;
  }
  return { segs, dur: t };
}
function posOnPlan(p, t, reverse) {
  if (reverse) t = p.dur - t;
  for (const s of p.segs) if (t <= s.t0 + s.d) {
    const u = Math.max(0, Math.min(1, (t - s.t0) / s.d));
    let dir = Math.sign(s.b[0] - s.a[0]) || 1; if (reverse) dir = -dir;
    return { x: s.a[0] + (s.b[0] - s.a[0]) * u, y: s.a[1] + (s.b[1] - s.a[1]) * u - (s.hop ? Math.sin(u * Math.PI) * 6 : 0), dir, moving: true, hop: s.hop };
  }
  const rp = restPose(), last = p.segs.length ? p.segs[p.segs.length - 1].b : [rp.x, rp.y];
  return { x: last[0], y: last[1], dir: 1, moving: false };
}
function startAction(wi) {
  if (!JOBS[wi]) return;
  if (action) { pending = wi; return; }
  const job = JOBS[wi], key = callOf(job), go = plan(wi), call = CALLS[key];
  action = { wi, key, job, t0: NOW_T(), go, raise: 0.3, playD: call.dur, back: plan(wi), played: false, noteIdx: 0, total: go.dur + 0.3 + call.dur + 0.6 + go.dur };
}

/* ---------- sound: synthesised brass ---------- */
const NF = { G4: 392, A4: 440, C5: 523.25, D5: 587.33, E5: 659.25, F5: 698.46, G5: 783.99, C6: 1046.5 };
function brass(ac, out, t0, f, d, vol, crack) {
  const o1 = ac.createOscillator(), o2 = ac.createOscillator(), fl = ac.createBiquadFilter(), gn = ac.createGain();
  o1.type = 'sawtooth'; o2.type = 'square';
  o1.frequency.setValueAtTime(f * 0.97, t0); o1.frequency.linearRampToValueAtTime(f, t0 + 0.035);
  o2.frequency.setValueAtTime(f * 0.97 * 1.004, t0); o2.frequency.linearRampToValueAtTime(f * 1.004, t0 + 0.035);
  if (d > 0.3 && !crack) { const lfo = ac.createOscillator(), lg = ac.createGain(); lfo.frequency.value = 5.5; lg.gain.setValueAtTime(0, t0); lg.gain.linearRampToValueAtTime(f * 0.008, t0 + 0.3);
    lfo.connect(lg); lg.connect(o1.frequency); lg.connect(o2.frequency); lfo.start(t0); lfo.stop(t0 + d + 0.05); }
  fl.type = 'lowpass'; fl.Q.value = 1.2;
  fl.frequency.setValueAtTime(500, t0); fl.frequency.linearRampToValueAtTime(f * 5, t0 + 0.04); fl.frequency.exponentialRampToValueAtTime(f * 2.6, t0 + Math.max(0.08, d));
  gn.gain.setValueAtTime(0.0001, t0); gn.gain.linearRampToValueAtTime(vol, t0 + 0.025); gn.gain.linearRampToValueAtTime(vol * 0.75, t0 + 0.09);
  gn.gain.setValueAtTime(vol * 0.75, t0 + Math.max(0.1, d - 0.05)); gn.gain.linearRampToValueAtTime(0.0001, t0 + d);
  if (crack) { // the lip splits (a sour second voice), the pitch sags, a raspy splat
    const o3 = ac.createOscillator(), g3 = ac.createGain(); o3.type = 'sawtooth'; o3.frequency.setValueAtTime(f * 1.07, t0 + 0.07);
    o3.frequency.exponentialRampToValueAtTime(f * 0.55, t0 + d); g3.gain.setValueAtTime(0, t0); g3.gain.setValueAtTime(vol * 0.6, t0 + 0.07);
    g3.gain.linearRampToValueAtTime(0.0001, t0 + d); o3.connect(g3); g3.connect(fl); o3.start(t0); o3.stop(t0 + d + 0.05);
    o1.frequency.setValueAtTime(f * 1.03, t0 + 0.07); o1.frequency.exponentialRampToValueAtTime(f * 0.6, t0 + d);
    o2.frequency.setValueAtTime(f * 1.1, t0 + 0.07); o2.frequency.exponentialRampToValueAtTime(f * 0.5, t0 + d);
    fl.frequency.setValueAtTime(f * 7, t0 + 0.07); fl.Q.value = 4;
    sfx.noise(t0 + 0.06, 0.12, 0.5, 1800, 'bandpass');
  }
  o1.connect(fl); o2.connect(fl); fl.connect(gn); gn.connect(out);
  o1.start(t0); o2.start(t0); o1.stop(t0 + d + 0.05); o2.stop(t0 + d + 0.05);
}
// each call is a list of [note, length]; the picture reads the same list, so notes seen = notes heard
const CALLS = {
  ok: { notes: [['G4', .14], ['C5', .14], ['E5', .14], ['G5', .38], ['E5', .14], ['G5', .14], ['C6', .4], ['G5', .2], ['C6', .9]] },
  waiting: { notes: [['G4', .14], ['C5', .14], ['E5', .14], ['G5', .38], ['F5', .2], ['D5', 1.0]] },   // ends unresolved: over to you
  working: { notes: [['G4', .2], ['G4', .2], ['G4', .5]], soft: true },                                 // still going
  error: { notes: [['G4', .16], ['C5', .16], ['E5', .7, 'crack']] },                                   // two notes, then it cracks
  missing: { notes: [], silent: true, extra: 1.6 },                                                      // no call at all
};
for (const k in CALLS) { const c = CALLS[k]; let t = 0; c.at = c.notes.map(n => { const a = t; t += n[1] + 0.03; return a; }); c.dur = t + (c.extra || 0.2); }
function playCall(key) {
  const ac = sfx.ctx(), out = sfx.out(); if (!ac || !out) return;
  const c = CALLS[key], t0 = ac.currentTime + 0.05, vol = c.soft ? 0.25 : 0.55;
  if (c.silent) { sfx.noise(t0, 1.4, 0.05, 420, 'bandpass'); return; }   // only the wind through the shutters
  c.notes.forEach((n, i) => brass(ac, out, t0 + c.at[i], NF[n[0]], n[1], vol, n[2] === 'crack'));
  if (key === 'error') for (let i = 0; i < 3; i++) sfx.noise(t0 + c.at[2] + 0.75 + i * 0.09, 0.07, 0.35, 3500, 'highpass');   // paper tearing
}

/* ---------- per frame ---------- */
const NOTE_SP = ['..##', '..#.', '..#.', '###.', '##..'], Q_SP = ['##.', '..#', '.#.', '...', '.#.'];
function spawnNote(x, y, dir, col, question) { if (!K.still) particles.push({ kind: question ? 'q' : 'note', x, y, vx: dir * (6 + Math.random() * 4), vy: -10 - Math.random() * 5, born: NOW_T(), life: 2.6, col, ph: Math.random() * 6 }); }
function crackBurst(x, y) {
  particles.push({ kind: 'crack', x, y, vx: 0, vy: 0, born: NOW_T(), life: 0.6 });
  if (!K.still) for (let i = 0; i < 5; i++) particles.push({ kind: 'paper', x: x - 2 + Math.random() * 4, y: y - 4, vx: (Math.random() - 0.5) * 30, vy: -18 - Math.random() * 12, born: NOW_T() + 0.7, life: 2.4, ph: Math.random() * 6 });
}
function drawParticles() {
  const t = NOW_T(); particles = particles.filter(p => t - p.born < p.life);
  for (const p of particles) {
    const a = t - p.born; if (a < 0) continue;
    const x = p.x + p.vx * a + Math.sin(a * 3 + p.ph) * 2;
    if (p.kind === 'note' || p.kind === 'q') {
      const y = p.y + p.vy * a; sprite(p.kind === 'q' ? Q_SP : NOTE_SP, Math.round(x), Math.round(y), { '#': a > p.life * 0.7 ? '#6a6e94' : p.col });
      if (a < p.life * 0.6) glow(glowSp(p.col, 4, 4, 0.35), x + 2, y + 2);
    } else if (p.kind === 'crack') {
      const r1 = 7 + (a < 0.3 ? 2 : 0);
      for (let i = 0; i < 8; i++) { const aa = i / 8 * 6.2832; for (let r = 3; r <= r1; r++) P(p.x + Math.round(Math.cos(aa) * r), p.y + Math.round(Math.sin(aa) * r), r > r1 - 2 ? '#ff4a4a' : '#ffe0e0'); }
      glow(glowSp('#ff3a3a', 14, 14, 0.5), p.x, p.y);
    } else {
      const y = Math.min(FLOOR - 2, p.y + p.vy * a + 22 * a * a), xx = p.x + p.vx * a + Math.sin(a * 5 + p.ph) * 3;
      R(xx - 1, y - 1, 5, 5, OL); R(xx, y, 3, 3, '#d9d4ff'); P(xx + 1, y + 1, '#2a2a44');
    }
  }
}
function drawTrumpeterNow(t) {
  const rest = restPose();
  if (!action) {
    if (rest.pose === 'hold') {   // a job is running: he stands at its window and keeps it going
      glow(glowSp('#6fffe0', 18, 22, 0.5), rest.x, rest.y - 12);
      drawTrumpeter(rest.x, rest.y, rest.dir, 'hold', t, { rim: '#36d3e6' });
      if (!K.still && Math.floor(t * 2) % 3 === 0 && Math.random() < 0.15) spawnNote(rest.x + rest.dir * 14, rest.y - 15, rest.dir, '#6fe6ff');
    } else {
      drawTrumpeter(rest.x, rest.y, 1, 'sit', t);
      const zz = K.still ? 0.3 : (t * 0.6) % 1; textL('Z', rest.x + 5 + Math.round(zz * 4), 158 - Math.round(zz * 10), zz < 0.8 ? '#9aa3d8' : '#4a4e72');
    }
    return;
  }
  const A = action, e = NOW_T() - A.t0, wn = LAY.wins[A.wi], call = CALLS[A.key];
  if (!wn) { action = null; return; }
  const tGo = A.go.dur, tRaise = tGo + A.raise, tPlay = tRaise + A.playD, tLower = tPlay + 0.6, pp = wn.play;
  if (e < tGo) { A.phase = 'go'; const p = posOnPlan(A.go, e, false); drawTrumpeter(p.x, p.y, p.dir, p.moving && !p.hop ? 'walk' : 'stand', e); }
  else if (e < tPlay) {
    A.phase = 'play';
    if (!A.played && e >= tRaise) { A.played = true; playCall(A.key); }
    if (call.silent) { drawTrumpeter(pp.x, pp.y, pp.dir, 'shrug', e); bubble(pp.x, pp.y - 26, t, '?', '#4a4e62'); return; }
    const pl = e - tRaise; let on = false, ni = -1;
    if (pl >= 0) call.notes.forEach((n, i) => { if (pl >= call.at[i] && pl < call.at[i] + n[1]) { on = true; ni = i; } });
    const cyan = A.key === 'working';
    if (cyan) glow(glowSp('#6fffe0', 14, 16, 0.35), pp.x, pp.y - 12);
    const bell = drawTrumpeter(pp.x, pp.y, pp.dir, e < tRaise ? 'hold' : 'play', e, { puff: on, rim: cyan ? '#36d3e6' : null });
    if (ni >= 0 && ni >= A.noteIdx) {
      A.noteIdx = ni + 1; const n = call.notes[ni], lastQ = A.key === 'waiting' && ni === call.notes.length - 1;
      if (n[2] === 'crack') crackBurst(bell.bellX, bell.bellY);
      else spawnNote(bell.bellX, bell.bellY, pp.dir, cyan ? '#6fe6ff' : lastQ ? '#ffc24a' : '#ffd36e', lastQ);
    }
    if (on) glow(glowSp('#ffd36e', 6, 6, 0.5), bell.bellX, bell.bellY);
  } else if (e < tLower) {
    A.phase = 'lower';
    if (A.key === 'working') { action = null; return drawTrumpeterNow(t); }
    drawTrumpeter(pp.x, pp.y, pp.dir, A.key === 'error' || A.key === 'missing' ? 'shrug' : 'stand', e);
    if (A.key === 'ok') bubble(pp.x, pp.y - 26, t, 'ok', '#3ddc7a');
  } else if (e < A.total) { A.phase = 'away'; const p = posOnPlan(A.back, e - tLower, true); drawTrumpeter(p.x, p.y, p.dir, p.moving && !p.hop ? 'walk' : 'stand', e); }
  else { action = null; if (pending !== null) { const p = pending; pending = null; startAction(p); } return drawTrumpeterNow(t); }
}
function gear(cx, cy, r, a, c, hi) {
  disc(cx, cy, r - 2, c);
  for (let i = 0; i < 10; i++) { const aa = a + i / 10 * 6.2832; R(cx + Math.round(Math.cos(aa) * (r - 1)) - 1, cy + Math.round(Math.sin(aa) * (r - 1)) - 1, 3, 3, c); }
  disc(cx, cy, Math.max(2, r - 5), '#2f2238'); disc(cx, cy, 2, hi);
  for (let i = 0; i < 4; i++) { const aa = a + i / 4 * 6.2832; for (let k = 2; k < r - 4; k++) P(cx + Math.round(Math.cos(aa) * k), cy + Math.round(Math.sin(aa) * k), c); }
}
let FACE = null;
const atMs = iso => { const d = new Date(iso); return isNaN(d) ? NaN : d.getTime(); };
function nextJob() { // the soonest next run of every job, shown or past the eighth window
  let best = null, bt = Infinity;
  for (const j of JOBS.concat(EXTRA)) { const ms = j.next ? atMs(j.next) : NaN; if (!isNaN(ms) && ms < bt) { bt = ms; best = j; } }
  return best;
}
function drawClock(t) {
  const cx = 200, cy = 42, now = new Date(K.nowMs());
  gear(176, 32, 11, t * 0.25, '#4a3a2a', '#6a5238'); gear(226, 55, 7, -t * 0.4, '#4a3a2a', '#6a5238');   // the works (mechanism, not data)
  glow(glowSp('#ffd36e', 30, 26, 0.2), cx, cy);
  if (!FACE) FACE = layer(43, 43, () => {   // the dial is static: cache it
    const c = 21; disc(c, c, 21, OL); disc(c, c, 20, '#d6a24a'); disc(c, c, 18, '#a87a30'); disc(c, c, 17, '#e9dcb8'); disc(c - 3, c - 3, 10, '#f3ead0');
    for (let h = 0; h < 12; h++) { const a = h / 12 * 6.2832; for (let r = h % 3 ? 15 : 13; r <= 16; r++) P(c + Math.round(Math.sin(a) * r), c - Math.round(Math.cos(a) * r), '#3a2d3a'); }
  });
  g.drawImage(FACE, cx - 21, cy - 21);
  const m = now.getMinutes() + now.getSeconds() / 60, hh = now.getHours() % 12 + m / 60, mA = m / 60 * 6.2832, hA = hh / 12 * 6.2832;   // the real time
  for (let k = 0; k <= 13; k++) P(cx + Math.round(Math.sin(mA) * k), cy - Math.round(Math.cos(mA) * k), '#1a1a30');
  for (let k = 0; k <= 8; k++) { const x = cx + Math.round(Math.sin(hA) * k), y = cy - Math.round(Math.cos(hA) * k); R(x, y, 2, 1, '#7a2a3a'); }
  R(cx - 1, cy - 1, 3, 3, '#1a1a30');
  const [px, py, pw, ph] = LAY.plaque, nx = nextJob(), pc = px + pw / 2;   // the next call, flagged in that job's banner colour
  R(px - 1, py - 1, pw + 2, ph + 2, OL); R(px, py, pw, ph, '#161b38'); R(px, py, pw, 1, '#2b336a');
  textC('NEXT CALL', pc, py + 3, '#9aa3d8');
  if (!nx) { textC('-', pc, py + 11, '#7a7f99'); return; }
  const l2 = K.timeText(nx.next); textC(l2, pc + 4, py + 11, '#ffd36e'); textC(K.untilText(nx.next), pc, py + 19, '#eef1ff');
  const bx = Math.round(pc + 4 - textW(l2) / 2 - 7); R(bx - 1, py + 10, 6, 7, OL); R(bx, py + 11, 4, 5, nx.banner); R(bx, py + 15, 4, 1, nx.bannerD);
}
function drawDust(t) {
  LAY.wins.forEach((wn, i) => {
    const st = JOBS[i].state, n = st === 'missing' ? 7 : 5;
    for (let k = 0; k < n; k++) {
      const sx = wn.ox + 4 + hash(k, i, 1) * (wn.ow - 8), sy = wn.compact ? 186 + hash(i, k, 2) * 12 : 160 + hash(i, k, 2) * 26;
      const x = sx + Math.sin(t * 0.3 + k) * 3, y = sy + (st === 'missing' ? Math.sin(t * 0.2 + k) : ((t * 2 + k * 7) % 30 - 15) * (wn.compact ? 0.5 : 1));
      P(x, y, st === 'missing' ? '#55596e' : rgba(ST[st].shaft, 0.6));
    }
  });
}

/* ---------- demo (?demo=1): the core moment on a timer, for screenshots and first-time viewers ---------- */
let caption = '', cursor = null;
function drawCursor(x, y, down) {
  const rows = ['#.....', '##....', '#.#...', '#..#..', '#...#.', '#..###', '#.#...', '##....'];
  sprite(rows, x + 1, y + 1, { '#': OL }); sprite(rows, x, y, { '#': down ? '#ffd36e' : '#f4f6ff' });
  if (down) { R(x - 4, y - 4, 2, 1, '#ffd36e'); R(x + 4, y - 4, 2, 1, '#ffd36e'); R(x - 5, y, 1, 1, '#ffd36e'); }
}
const CAPTION = { ok: 'HE PLAYS THE FULL CALL: LAST RUN OK', waiting: 'THE CALL ENDS ON A QUESTION: OVER TO YOU', working: 'STILL PLAYING: THE JOB IS RUNNING',
  error: 'TWO NOTES, THEN IT CRACKS: LAST RUN FAILED', missing: 'NO CALL: NO RUN ON RECORD' };
function runDemo() {
  timers.forEach(clearTimeout); timers = [];
  if (!JOBS.length) return;
  const st = demo.state, at = (s, f) => timers.push(setTimeout(f, Math.max(0, s * 1000)));
  let ti = st === 'waiting' || st === 'working' ? 0 : st === 'error' || st === 'missing' || st === 'missed' ? JOBS.length - 1 : JOBS.findIndex(j => j.state === 'ok');
  if (ti < 0) ti = 0;
  const wn = LAY.wins[ti], job = JOBS[ti], key = callOf(job), go = plan(ti).dur;
  // timed so that about 6.6 s after entering (a 7 s headless screenshot) shows the telling moment
  const moment = { ok: 2.0, waiting: 1.3, working: 0.3, error: 0.55, missing: 0.8 }[key], click = Math.max(1.4, 6.6 - go - 0.3 - moment);
  const tx = Math.round(wn.cx), ty = Math.round((wn.top + wn.bot) / 2);
  at(click - 1.2, () => { caption = 'CLICK A WINDOW'; cursor = [tx + 30, ty + 30, false]; });
  at(click - 0.6, () => { cursor = [tx + 12, ty + 12, false]; });
  at(click, () => { cursor = [tx, ty, true]; startAction(ti); caption = 'THE TRUMPETER STEPS UP'; });
  at(click + 0.4, () => { cursor = null; });
  at(click + go + 0.25, () => { caption = job.why === 'missed' ? 'NO CALL: IT MISSED ITS RUN' : CAPTION[key]; });
  at(click + go * 2 + 0.3 + CALLS[key].dur + 1.2, () => { caption = ''; });
  at(16, runDemo);
}
function drawDemoLayer() {
  if (caption) { const cw = textW(caption) + 12, x = Math.round(200 - cw / 2); R(x - 1, 196, cw + 2, 13, OL); R(x, 197, cw, 11, '#161b38'); R(x, 197, cw, 1, '#ffd36e'); textL(caption, x + 6, 200, '#ffd36e'); }
  if (cursor) drawCursor(cursor[0], cursor[1], cursor[2]);
}
function drawSampleBadge() {
  const s = 'SAMPLE: MADE-UP JOBS', sw = textW(s) + 8, y = H - 13;
  R(4, y, sw, 9, OL); R(5, y + 1, sw - 2, 7, '#3a2208'); textL(s, 8, y + 2, '#ffd36e');
}

/* ---------- hover / click targets ---------- */
function jobWord(j) { return ({ missed: 'missed its run', stopped: 'stopped half-way' })[j.why] || { ok: 'ran fine', waiting: 'needs you', working: 'running now', error: 'last run failed', missing: 'no run on record' }[j.state]; }
function feedsName(j) { const p = j.feeds && K.part(j.feeds); return p ? String(p.label || p.name || j.feeds) : j.feeds; }
function jobTip(j) {
  return [j.name + ': ' + jobWord(j), 'Runs ' + (j.when || '(no schedule set)'),
    'Next ' + (j.next ? K.dayText(j.next) + ' ' + K.timeText(j.next).slice(4) + ' (' + K.untilText(j.next) + ')' : '-'),
    'Last ' + (j.state === 'working' ? 'running now' : j.last ? K.dayText(j.last) + ' ' + K.timeText(j.last).slice(4) : '-'),
    j.result || '', j.feeds ? 'Feeds ' + feedsName(j) : ''].filter(Boolean);
}
function openJob(j) {
  sfx.click();
  if (j.href) { K.open(j.href); K.toast('OPENING ' + j.short + (j.state === 'waiting' ? ': WHAT WAITS FOR YOU' : "'S LOG")); }
  else K.toast(sample ? 'SAMPLE JOB: NOTHING TO OPEN' : 'NO FILE FOR ' + j.short + ' YET');
}
function callJob(i) {
  const j = JOBS[i]; startAction(i);
  if (j.state === 'missing') K.toast('NO CALL: ' + j.short + ' HAS NO RUN ON RECORD');
  else if (j.why === 'missed') K.toast('NO CALL: ' + j.short + ' MISSED ITS RUN');
}
function moreTip() {
  const order = j => WORST.indexOf(j.state), list = EXTRA.slice().sort((a, b) => order(a) - order(b));
  const lines = list.slice(0, 6).map(j => j.name + ': ' + jobWord(j));
  if (list.length > 6) lines.push('and ' + (list.length - 6) + ' more');
  return ['+' + EXTRA.length + ' more job' + (EXTRA.length === 1 ? '' : 's') + ' (eight windows fit)'].concat(lines, ['The card (key I) lists every one.']);
}
function targets() {
  const out = [];
  LAY.wins.forEach((wn, i) => {
    const j = JOBS[i], tip = jobTip(j);
    const info = wn.slate || wn.plate, more = j.href ? 'Click: open its ' + (j.state === 'waiting' ? 'file' : 'log') + '.' : '';
    out.push({ id: 'info-' + j.id, box: info, tip: tip.concat(more ? [more] : []), label: j.name + ', ' + jobWord(j) + '. Open its file.', onClick: () => openJob(j) });
    if (wn.plate) out.push({ id: 'next-' + j.id, box: [wn.plate[0], wn.plate[1] + 9, wn.plate[2], 7], tip: tip.concat(more ? [more] : []), label: '', onClick: () => openJob(j) });
    const l = Math.min(wn.ox - 6, wn.hook[0] - 6), r = Math.max(wn.ox + wn.ow + 6, wn.hook[0] + 12);   // the window and its hook
    const box = wn.compact ? [wn.ox - 5, wn.top - 12, wn.ow + 10, wn.bot - wn.top + 18] : [l, wn.top - 4, r - l, wn.bot - wn.top + 10];
    out.push({ id: 'win-' + j.id, box, tip: tip.concat(['Click: hear its call.']), label: j.name + ' window: hear its call', onClick: () => callJob(i) });
  });
  if (LAY.more && EXTRA.length) out.push({ id: 'more', box: LAY.more, tip: moreTip(), label: EXTRA.length + ' more jobs. Open the card.', onClick: K.card });
  const nx = nextJob(), nextTip = nx ? ['Next call: ' + nx.name, K.dayText(nx.next) + ' ' + K.timeText(nx.next).slice(4) + ' (' + K.untilText(nx.next) + ')', 'The flag matches that job\'s trumpet banner.'] : ['No job has a next run'];
  out.push({ id: 'clock', box: [178, 20, 45, 45], tip: ['The clock: the time now'].concat(nextTip), label: 'Clock: next call', onClick: null });
  out.push({ id: 'plaque', box: LAY.plaque, tip: nextTip, label: '', onClick: null });
  const rp = restPose();
  out.push({ id: 'trumpeter', box: [rp.x - 8, rp.y - 28, 16, 28], tip: ['The trumpeter', rp.pose === 'hold' ? 'Playing on while ' + JOBS[rp.at].name + ' runs.' : 'Click a window and he plays that job\'s last result.'], label: '', onClick: () => K.toast('CLICK A WINDOW TO SEND HIM UP') });
  out.push({ id: 'door', box: [DOOR[0] - 6, DOOR[1] - 14, DOOR[2] + 12, DOOR[3] + 14], tip: ['Back to town', 'or press Esc'], label: 'Door: back to town', onClick: K.leave });
  return out.filter(o => o.box);
}

/* ---------- the room ---------- */
function setup(data, opts) {
  let raw = Array.isArray(data && data.rows) ? data.rows : [];
  sample = false;
  if (opts.demo) {
    const n = Math.max(1, Math.min(12, parseInt(opts.qs.get('jobs'), 10) || 3));
    raw = Array.from({ length: n }, (_, i) => sampleJob(i)); sample = true;
  }
  const all = raw.map(mkJob);
  total = all.length;
  JOBS = all.slice(0, MAX_WIN); EXTRA = all.slice(MAX_WIN);
  if (sample && DEMO_STATES.includes(demo.state)) force(JOBS, demo.state);
  LAY = layout(JOBS.length); STATIC = null; action = null; pending = null; particles = [];
}
function trumpetOf(j, i) {
  if (j.state === 'missing') return 'none';
  if (fallen(j)) return 'floor';
  if (j.state === 'working' || (action && action.wi === i && action.phase !== 'away')) return 'played';
  return 'hook';
}
registerRoom('jobs', {
  enter(data, opts) {
    demo = opts.demo ? { state: opts.state || '' } : null;
    setup(data, opts); buildStatic();
    if (demo) runDemo();
  },
  leave() { timers.forEach(clearTimeout); timers = []; action = null; pending = null; particles = []; STATIC = null; caption = ''; cursor = null; },
  draw(ctx, t) {
    if (!STATIC) buildStatic();
    g.drawImage(STATIC, 0, 0);
    drawClock(t);
    if (LAY.candle) { const fl = K.still ? 0 : Math.floor(t * 7) % 3; R(199, 117 + (fl === 1 ? 1 : 0), 1, 3, '#ffd36e'); P(199, 116 + (fl === 2 ? 1 : 0), '#fff6c0'); glow(glowSp('#ffb45a', 22, 16, 0.22), 199, 124, 0.9 + fl * 0.05); }
    JOBS.forEach((job, i) => {   // lanterns, floor light, the trumpet (or its absence)
      const wn = LAY.wins[i], st = job.state, c = ST[st], [lx, ly] = wn.lantern;
      R(lx - 1, ly - 6, 3, 1, '#3a2d3a'); R(lx, ly - 5, 1, 2, '#3a2d3a');
      R(lx - 2, ly - 3, 5, 7, OL); R(lx - 1, ly - 2, 3, 5, st === 'missing' ? '#3a3e52' : c.lamp); if (st !== 'missing') P(lx - 1, ly - 2, c.hi);
      const fl = K.still ? 1 : st === 'error' ? (Math.sin(t * 9) > 0.2 ? 1 : 0.55) : 0.85 + Math.sin(t * 3 + i) * 0.1;
      if (c.glow) { glow(glowSp(c.glow, 15, 13, st === 'ok' ? 0.4 : 0.55), lx, ly, fl); glow(glowSp(c.glow, Math.min(40, wn.ow - 6), 10, st === 'ok' ? 0.12 : 0.24), wn.cx - 10 * wn.side, 200, fl); }
      if (fallen(job)) glow(glowSp('#ff3a3a', 18, 8, 0.35), wn.floorT[0] + 7, 183, fl);
      if (wn.slate) { const [sx, sy, sw] = wn.slate; R(sx + sw - 8, sy + 3, 5, 5, OL); R(sx + sw - 7, sy + 4, 3, 3, c.lamp); if (c.glow) { P(sx + sw - 7, sy + 4, c.hi); glow(glowSp(c.glow, 6, 6, 0.4), sx + sw - 6, sy + 5); } }
      const busy = action && action.wi === i && action.phase !== 'away' && action.key !== 'missing', [hx, hy] = wn.hook;
      if (st === 'missing') { if (!wn.compact) emptyHook(hx, hy); }
      else if (fallen(job)) { R(hx - 1, hy - 1, 3, 2, '#8a7a9a'); if (!busy) floorTrumpet(wn.floorT[0], wn.floorT[1], !wn.compact); }
      else if (!(busy || st === 'working')) hookTrumpet(hx, hy, job, t);
      else { R(hx - 1, hy - 1, 3, 2, '#8a7a9a'); P(hx, hy - 2, '#b0a4c0'); }
      if (st === 'waiting') bubble(wn.compact ? Math.round(wn.cx) + 8 : hx + (wn.k ? -4 : 4), wn.compact ? wn.top - 2 : hy - 4, t, '!', '#ffb020');
    });
    drawTrumpeterNow(t);
    drawParticles();
    if (!K.still) drawDust(t);
    if (sample) drawSampleBadge();
    if (demo) drawDemoLayer();
  },
  targets,
  plate() { // the count is every job; the state is the worst job's (a job with no run on record beats ok)
    return { num: String(total), state: total ? worstOf(JOBS.concat(EXTRA)) : 'missing' };
  },
  busy() { return !!action || particles.length > 0 || (timers.length > 0 && !!demo); },
  key(e) {
    if (!demo) return;
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= DEMO_STATES.length) { demo.state = DEMO_STATES[n - 1]; force(JOBS, demo.state); LAY = layout(JOBS.length); STATIC = null; action = null; pending = null; particles = []; runDemo(); }
  },
  seen() { // what the room shows now, for tests: one entry per window, the "+N" board, the trumpeter
    const rp = restPose(), nx = nextJob();
    return { sample, total, shown: JOBS.length, more: EXTRA.length, compact: JOBS.length > 2,
      windows: JOBS.map((j, i) => ({ id: j.id, state: j.state, why: j.why, shut: j.state === 'missing', trumpet: trumpetOf(j, i),
        bubble: j.state === 'waiting', say: j.say })),
      trumpeter: action ? { at: action.wi, phase: action.phase || 'go', call: action.key } : { at: rp.at, pose: rp.pose },
      nextCall: nx ? nx.id : null, caption };
  },
});
})();
