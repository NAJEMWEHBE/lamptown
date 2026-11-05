/* CONTEST: the blinded trial.
   Data: the building's room data from `contest_json` (collectors/contest.py): `contests`,
   newest first. The newest is on the bench; older ones are crates on the pallet.
   - One coded bottle per sealed entry; every bottle and tag looks the same (codes only) until the key is
     revealed. The liquid is the entry's score (the scores, or round-2 wins, scaled to the top one; else the
     round-1 mean rank turned round, the best entry full). The legend card on the wall says which.
   - Shelves: top = entries out in round 1 (empty vials; sealed vials while judging), middle = the scored
     entries that are not finalists, bench = the finalists. The judges' winner is the fullest bottle and wears
     a small blue rosette; your pick is a separate thing: the warm lamp.
   - Placebo: the control turns to white glass with a PLACEBO tag once unblinded.
   - Waiting, step 1: an amber sealed envelope "UNBLIND?" on the bench (click it, or U: names on hover, the
     placebo shows; remembered on this screen only, nothing is written). Step 2: amber "PICK ONE" tags on the
     finalists until a pick is recorded.
   - Picked (the core moment): the pick's tag turns round to its name under the warm lamp; it plays on entry.
     A recorded decision that picks none of them: the envelope holds a "NONE" card, the lamp stays off.
   - Working: the judges' lamps, one per judge, drip until each is in. Error: a red notice says which file.
   - Older contests are crates on the pallet (hover: name, date, pick; click: put it on the bench).
   Scale: top shelf 24 vials (then "+N"), middle shelf 5 tagged bottles or 15 small ones (then "+N"),
   bench 4 finalists, 6 crates (then "+N").
   Demo (?room=<id>&demo=1): a fictional SAMPLE contest, the whole moment on a timer. &state=ok|waiting|pick|
   working|error|missing (keys 1-6) shows one state still; &entries=N pads the sample; &contest=N picks one. */
(() => {
'use strict';
const { W, H, OL, ST, R, P, sprite, hash, glow, glowSp, textW, textL, textC, fitText, wrapText, layer, sfx } = K;
const NOW_T = () => performance.now() / 1000;
const FORCE = ['ok', 'waiting', 'pick', 'working', 'error', 'missing'];
const WORDS = { ok: 'PICKED', waiting: 'NEEDS YOU', working: 'JUDGING', error: "CAN'T READ", missing: 'NOT SET UP' };
const SEEN_KEY = 'lamptown-contest-unblinded';
const UNBLIND_S = 2.0, TURN_S = 1.2;
const PAPER = ['#b9c3dc', '#1a1f3a', '#8a94b2'], GOLD = ['#ffd36e', '#3a1e10', '#c98a3a'], AMBER = ['#ffb020', '#3a1e10', '#c06a1a'];
const GLASS = {
  glass: { e: '#1b3346', e2: '#152839', hi: 'rgba(170,220,255,0.35)', l: '#ff5c8a', s: '#ffb3c9', ld: '#c43a68' },
  white: { e: '#b4bed2', e2: '#98a2b8', hi: 'rgba(255,255,255,0.7)', l: '#f3b6cb', s: '#ffffff', ld: '#d48ea8' },
  dust: { e: '#1d2a38', e2: '#17212e', hi: 'rgba(160,190,220,0.18)', l: '#ff5c8a', s: '#ffb3c9', ld: '#c43a68' },
  whitedust: { e: '#5c6478', e2: '#4c5466', hi: 'rgba(220,230,250,0.3)', l: '#f3b6cb', s: '#ffffff', ld: '#d48ea8' },
};
const BENCH_X0 = 85, BENCH_W = 248, MID = [100, 218], TOPS = [130, 172];

let SPR = [], VI = 0, B = null, LAY = null, STATIC = null, STATIC_SIG = '', demo = null, forced = null, timers = [];
let anim = null, caption = '', cursor = null, sample = false, seen = new Set(), RAW = null, BID = '', SEEN = {};

/* ---------- data ---------- */
const str = v => (v == null ? '' : String(v));
const num = v => (typeof v === 'number' && isFinite(v) ? v : null);
function normCard(c) {
  c = c && typeof c === 'object' ? c : {};
  const fill = num(c.fill) === null ? null : Math.max(0, Math.min(1, c.fill));
  return { code: str(c.code).toUpperCase().slice(0, 8) || '?', name: str(c.name || c.code).slice(0, 120), control: !!c.control, fill,
    mean: num(c.mean), place: c.place | 0, ranks: Array.isArray(c.ranks) ? c.ranks.map(Number).slice(0, 40) : [],
    score: num(c.score), inR2: !!c.inR2, finalist: !!c.finalist, wild: !!c.wild,
    built: c.built && typeof c.built === 'object' ? { href: str(c.built.href), shot: str(c.built.shot) } : null, pick: !!c.pick, first: !!c.first };
}
function normContest(s) {
  s = s && typeof s === 'object' ? s : {};
  const st = ST[s.state] ? s.state : 'missing', j = s.judges && typeof s.judges === 'object' ? s.judges : {};
  const pick = s.pick && typeof s.pick === 'object' ? { code: s.pick.code ? str(s.pick.code).toUpperCase() : null, name: str(s.pick.name), text: str(s.pick.text),
    note: str(s.pick.note), href: str(s.pick.href) } : null;
  return { id: str(s.name || s.title).slice(0, 120), name: str(s.name).slice(0, 120), title: str(s.title || s.name || 'contest').slice(0, 120), date: str(s.date),
    href: str(s.href), state: st, step: s.step === 'pick' ? 'pick' : st === 'waiting' ? 'unblind' : null, say: str(s.say).slice(0, 120), error: str(s.error).slice(0, 300),
    why: str(s.why), rounds: s.rounds | 0, measure: ['r2wins', 'score', 'rank'].includes(s.measure) ? s.measure : '', full: num(s.full), of: num(s.of),
    count: s.count | 0, more: s.more | 0, judges: { r1: j.r1 | 0, r1of: j.r1of | 0, r2: j.r2 | 0, r2of: j.r2of | 0 }, revealed: !!s.revealed, pick,
    finalistsBuilt: !!s.finalistsBuilt, cards: (Array.isArray(s.cards) ? s.cards : []).slice(0, 200).map(normCard), pickWords: str(s.pickWords).slice(0, 120),
    hrefs: Object.assign({ key: '', cards: '', result: '', tally: '' }, s.hrefs && typeof s.hrefs === 'object' ? s.hrefs : {}) };
}

/* the sample contest for ?demo=1: made-up lighthouse names, clearly marked SAMPLE (never real data) */
const SAMPLE_R2 = [['C14', 'Salt Halo', 26, 3.33], ['C09', "Keeper's Lamp", 20, 1.33], ['C05', 'Gannet Light', 18, 4.33], ['C03', 'Long Beam', 12, 4.0],
  ['C08', 'Fog Bell', 10, 2.67, 1], ['C01', 'North Eye', 4, 6.67]];
const SAMPLE_R1 = [['C13', 'Cold Glass', 8], ['C19', 'Tern Watch', 8.67], ['C16', 'Night Ladder', 8.67], ['C11', 'Wick and Wave', 9.33],
  ['C17', 'Pilot Ring', 11.67], ['C02', 'Salt Stair', 12], ['C04', 'Shoal Lantern', 12.67], ['C12', 'Grey Gull', 14], ['C06', 'Tide Clock', 15.33],
  ['C10', 'Rock Candle', 15.33], ['C07', 'Mist Keeper', 16], ['C15', 'Harbour Eye', 17.33], ['C18', 'Last Light', 18.67]];
function sampleContest(n) {
  const cards = [];
  SAMPLE_R2.forEach(([code, name, score, mean, ctl], i) => cards.push({ code, name, score, mean, control: !!ctl, fill: score / 26, inR2: true, place: i + 1,
    finalist: code === 'C14' || code === 'C09', first: i === 0, built: code === 'C14' || code === 'C09' ? { href: '', shot: '' } : null }));
  SAMPLE_R1.forEach(([code, name, mean], i) => cards.push({ code, name, mean, fill: null, place: 7 + i,
    finalist: code === 'C17', wild: code === 'C17', built: code === 'C17' ? { href: '', shot: '' } : null }));
  for (let i = cards.length; i < n; i++) {   // &entries=N: more sample entries (half scored), to see the layout scale
    const r2 = i % 2 === 0;
    cards.push({ code: 'C' + String(i + 1).padStart(2, '0'), name: 'Sample entry ' + (i + 1), mean: 9 + i / 3, fill: r2 ? Math.max(0.05, 0.6 - i / 120) : null,
      score: r2 ? Math.max(1, Math.round(15 - i / 8)) : null, inR2: r2, place: i + 1, control: false });
  }
  return { name: 'sample', title: 'SAMPLE: LIGHTHOUSE NAMES', date: '2026-03-12', state: 'waiting', step: 'unblind', rounds: 2, measure: 'r2wins', full: 26, of: 30,
    judges: { r1: 3, r1of: 3, r2: 3, r2of: 3 }, revealed: false, pick: null, finalistsBuilt: true, cards: cards.slice(0, Math.max(n, 19)), count: Math.max(n, 19) };
}
function sampleData(qs) {
  const n = Math.max(3, Math.min(160, parseInt(qs && qs.get('entries'), 10) || 19));
  return { state: 'waiting', count: 3, contests: [sampleContest(n),
    { name: 'sample-older', title: 'Sample older contest', date: '2026-02-20', state: 'ok', cards: [], pickWords: 'Sample pick' },
    { name: 'sample-unpicked', title: 'Sample unpicked contest', date: '2026-02-08', state: 'waiting', cards: [], pickWords: '' }] };
}

/* &state= (and demo keys 1-6): the contest on the bench in that state, from its own entries */
function force(b, st) {
  if (!st) return;
  const finals = b.cards.filter(c => c.finalist);
  if (st === 'ok') {
    if (!b.pick || !b.pick.code) { const c = finals[0] || b.cards[0]; if (c) { b.cards.forEach(x => { x.pick = x === c; }); b.pick = { code: c.code, name: c.name, text: '', note: '', href: '' }; } }
    Object.assign(b, { state: 'ok', step: null, revealed: true });
  } else if (st === 'waiting' || st === 'pick') {
    b.cards.forEach(c => { c.pick = false; });
    Object.assign(b, { state: 'waiting', step: st === 'pick' ? 'pick' : 'unblind', revealed: st === 'pick', pick: null });
    if (!b.cards.some(c => c.finalist)) b.cards.slice(0, 2).forEach(c => { c.finalist = true; });
  } else if (st === 'working') {
    b.cards.forEach(c => { Object.assign(c, { fill: null, score: null, inR2: false, finalist: false, wild: false, pick: false, first: false }); });
    Object.assign(b, { state: 'working', step: null, why: 'r1', revealed: false, pick: null, rounds: 0,
      judges: { r1: 1, r1of: Math.max(3, b.judges.r1of), r2: 0, r2of: 0 }, say: 'JUDGES ARE STILL RANKING' });
  } else if (st === 'error') {
    Object.assign(b, { state: 'error', step: null, pick: null, rounds: 0, cards: [], say: "CAN'T READ THE SCORES",
      error: 'Shown by state=error in the address: this is how an unreadable contest file looks. Nothing is guessed.' });
  }
}

function setup(data, opts) {
  sample = !!opts.demo;
  const raw = sample ? sampleData(opts.qs) : data && typeof data === 'object' ? data : {};
  RAW = raw;
  SPR = (Array.isArray(raw.contests) ? raw.contests : []).slice(0, 40).map(normContest);
  forced = sample && FORCE.includes(opts.state) ? opts.state : null;   // &state= only with &demo=1: the real room never pretends
  if (forced === 'missing') SPR = [];
  const want = parseInt(opts.qs && opts.qs.get('contest'), 10);
  VI = want >= 0 && want < SPR.length ? want : 0;
  try { const a = JSON.parse(localStorage.getItem(SEEN_KEY) || '[]'); seen = new Set(Array.isArray(a) ? a : []); } catch (e) { seen = new Set(); }
  if (sample) seen = new Set();
  showContest(false);
}
function showContest(sound) {
  B = SPR[VI] || null;
  if (B && forced && VI === 0) force(B, forced);
  LAY = B ? layout(B) : null; STATIC = null; anim = null;
  if (B && B.state === 'ok' && B.pick && B.pick.code) startAnim('turn', 0.35);
  if (sound) play('wood');
}
const seenKey = () => BID + '|' + (B ? B.id + '|' + B.date : '');
/* the step the contest is at: unblinded here (remembered on this screen) counts as revealed */
function step() { if (!B || B.state !== 'waiting') return null; return B.step === 'pick' || seen.has(seenKey()) ? 'pick' : 'unblind'; }
function revealed() { return !!B && (B.revealed || B.state === 'ok' || step() === 'pick'); }
function animK(kind) { if (!anim || anim.kind !== kind) return null; if (K.still) return 1; return Math.max(0, Math.min(1, (NOW_T() - anim.t0) / anim.dur)); }
function startAnim(kind, delay) {
  anim = { kind, t0: NOW_T() + (delay || 0), dur: kind === 'unblind' ? UNBLIND_S : TURN_S };
  if (!sfx.ctx()) return;
  const d = Math.max(0, delay || 0) * 1000;
  if (kind === 'unblind') { play('paper', d); play('tick', d + 700); play('chime', d + 1300); }
  else { play('lamp', d + 100); play('chime', d + 650); }
}

/* ---------- layout ---------- */
function bottleGeo(cx, base, bw, bh, nw, nh) {
  const sh = Math.max(2, Math.round((bw - nw) / 4)), total = nh + sh + bh;
  return { cx, base, bw, bh, nw, nh, sh, total, top: base - total };
}
function tagWidth(s) { return textW(s) + 6; }
function layout(b) {
  const L = { finals: [], mid: [], tops: [], midMore: [], topMore: [], lampX: 190, tagMax: 40 };
  if (!b || ['missing', 'error'].includes(b.state) && !b.cards.length) return L;
  const cards = b.cards;
  let finals = cards.filter(c => c.finalist).sort((a, z) => a.place - z.place);
  const extraF = finals.slice(4); finals = finals.slice(0, 4);
  let pi = finals.findIndex(c => c.pick); if (pi < 0) pi = finals.findIndex(c => c.first);
  if (pi >= 0) { const [pc] = finals.splice(pi, 1); finals.splice(Math.floor(finals.length / 2), 0, pc); }   // the pick (else the judges' first) sits in the middle, under the lamp
  const nf = finals.length, pitch = Math.min(96, BENCH_W / Math.max(1, nf)), groupW = pitch * (nf - 1) + 66;
  L.tagMax = Math.max(18, Math.min(64, Math.floor(pitch - 30)));
  L.finals = finals.map((c, i) => ({ c, g: bottleGeo(Math.round(BENCH_X0 + BENCH_W / 2 - groupW / 2 + 11 + pitch * i), 168, 22, 28, 8, 5), big: true }));
  const pk = L.finals.find(f => f.c.pick); if (pk) L.lampX = pk.g.cx;
  else if (L.finals.length) L.lampX = Math.round(L.finals.reduce((a, f) => a + f.g.cx, 0) / L.finals.length);
  // middle shelf: scored entries that are not on the bench; the top shelf: the rest (out in round 1, or not scored yet)
  const rest = cards.filter(c => !finals.includes(c)).sort((a, z) => a.place - z.place);
  const scored = b.state === 'working' ? [] : rest.filter(c => c.fill !== null || extraF.includes(c));
  const unscored = rest.filter(c => !scored.includes(c));
  const tagW = c => tagWidth(c.control && revealed() ? 'PLACEBO' : c.code);
  const bigW = list => list.reduce((a, c) => a + 16 + 3 + tagW(c) + 3, 0);
  let mid = scored;
  const big = mid.length <= 5 && bigW(mid) <= MID[1];
  if (!big) { const cap = Math.floor((MID[1] - 4) / 14); if (mid.length > cap) { L.midMore = mid.slice(cap - 1); mid = mid.slice(0, cap - 1); } }
  if (big) {
    const gap = Math.round((MID[1] - bigW(mid)) / (mid.length + 1));
    let x = MID[0] + gap;
    L.mid = mid.map(c => { const cx = x + 8, o = { c, g: bottleGeo(cx, 111, 16, 24, 6, 5), big: true, tw: tagW(c) }; x += 16 + 3 + o.tw + 3 + gap; return o; });
  } else {
    const slots = mid.length + (L.midMore.length ? 1 : 0), pitchM = Math.min(24, (MID[1] - 4) / Math.max(1, slots));
    const x0 = MID[0] + 2 + Math.round((MID[1] - 4 - pitchM * slots) / 2);
    L.mid = mid.map((c, i) => ({ c, g: bottleGeo(Math.round(x0 + pitchM * i + pitchM / 2), 111, 10, 18, 4, 4), big: false }));
    if (L.midMore.length) L.midMoreAt = Math.round(x0 + pitchM * mid.length + pitchM / 2);
  }
  let tops = unscored; const tcap = 24;
  if (tops.length > tcap) { L.topMore = tops.slice(tcap - 1); tops = tops.slice(0, tcap - 1); }
  const n = tops.length + (L.topMore.length ? 1 : 0), tp = n > 12 ? 7 : 14, tx0 = TOPS[0] + 12 + Math.round((TOPS[1] - 24 - tp * (n - 1)) / 2);
  L.tops = tops.map((c, i) => ({ c, g: bottleGeo(tx0 + i * tp, 57, tp < 14 ? 5 : 6, 9, 2, 2), big: false }));
  if (L.topMore.length) L.topMoreAt = tx0 + tops.length * tp;
  return L;
}

/* ---------- drawing helpers ---------- */
function drawBottle(G, kind, fill) {
  const p = GLASS[kind], { cx, base, bw, bh, nw, nh, sh, total, top } = G;
  const liqH = fill == null ? 0 : Math.max(fill > 0 ? 1 : 0, Math.round(fill * (bh - 2))), liqTop = base - 1 - liqH;
  const rowW = j => j < nh ? nw : j < nh + sh ? nw + 2 * Math.round(((bw - nw) / 2) * (j - nh + 1) / (sh + 1)) : (j === total - 1 ? bw - 2 : bw);
  R(cx - (nw >> 1) - 1, top - 1, nw + 2, 1, OL);
  for (let j = 0; j < total; j++) {
    const w = rowW(j), y = top + j, x = cx - (w >> 1), wet = j >= nh && y > liqTop;
    R(x - 1, y, w + 2, 1, OL);
    R(x, y, w, 1, wet ? (y === liqTop + 1 ? p.s : p.l) : p.e);
    if (w > 4) R(x + w - 2, y, 2, 1, wet ? p.ld : p.e2);
    if (w > 5 && j >= nh) P(x + 1, y, p.hi);
  }
  R(cx - (bw >> 1) + 1, base, bw - 2, 1, OL);
  const cw = Math.max(2, nw - 2); R(cx - (cw >> 1) - 1, top - 4, cw + 2, 4, OL); R(cx - (cw >> 1), top - 3, cw, 3, '#a0643a'); R(cx - (cw >> 1), top - 3, cw, 1, '#c88a52');
  return liqH;
}
function bubbles(G, fill, t, seed) { // a slow bubble and a glint on the surface (per frame, two pixels)
  const liqH = fill == null ? 0 : Math.round(fill * (G.bh - 2)); if (liqH <= 4 || K.still) return;
  if (!(B && B.state === 'working')) t = 0;   // 0.2.1: the bubbles rise only while the judges rank (an idle room is still)
  const ph = (t * 0.5 + seed * 0.37) % 1, bx = G.cx - (G.bw >> 1) + 2 + Math.floor(hash(seed, 3, 1) * (G.bw - 5));
  P(bx, G.base - 2 - Math.floor(ph * (liqH - 2)), '#ffb3c9');
  P(G.cx - (G.bw >> 1) + 1 + Math.floor(((t * 0.6 + seed * 0.13) % 1) * (G.bw - 2)), G.base - liqH, '#ffffff');
}
const FACES = new Map();
function tagFace(lines, w, h, pal) {
  const k = lines.join('/') + '|' + w + '|' + h + '|' + pal[0]; let c = FACES.get(k); if (c) return c;
  if (FACES.size > 200) FACES.clear();
  c = layer(w, h, () => { R(0, 0, w, h, OL); R(1, 1, w - 2, h - 2, pal[0]); R(1, h - 2, w - 2, 1, pal[2]); P(1, 1, pal[2]);
    const y0 = Math.round((h - (lines.length * 7 - 2)) / 2); lines.forEach((l, i) => textC(l, w / 2, y0 + i * 7, pal[1])); });
  FACES.set(k, c); return c;
}
function line(x0, y0, x1, y1, c) { const n = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0)) || 1; for (let i = 0; i <= n; i++) P(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n, c); }
function tag(ax, ay, tcx, ty, back, front, turn) { // turn 0..1: 0 shows the back (code), 1 the front (name); turning = a horizontal squash
  line(ax, ay, tcx, ty - 1, '#cfd6e8');
  const a = turn * Math.PI, sx = Math.cos(a), face = sx >= 0 ? back : front, vw = Math.max(1, Math.round(face.width * Math.abs(sx)));
  g.imageSmoothingEnabled = false; g.drawImage(face, Math.round(tcx - vw / 2), ty, vw, face.height); P(tcx, ty, '#cfd6e8');
}
function nameLines(name, maxW) { // the pick's name for its tag: words (and hyphen parts) onto up to 3 lines
  const words = String(name).toUpperCase().replace(/-/g, '- ').split(/\s+/).filter(Boolean);
  const out = []; let cur = '';
  for (const w of words) { const tryL = cur ? (cur.endsWith('-') ? cur + w : cur + ' ' + w) : w; if (textW(tryL) <= maxW || !cur) cur = tryL; else { out.push(cur); cur = w; } }
  if (cur) out.push(cur);
  return out.slice(0, 3).map(l => fitText(l, maxW));
}
function rosette(x, y) { // the judges' first place: a blue rosette with tails
  sprite(['.ooo.', 'obbbo', 'obwbo', 'obbbo', '.ooo.', '.b.b.', 'b...b'], x, y, { o: OL, b: '#4f8fe0', w: '#ffffff' }); P(x + 1, y + 1, '#9fc8ff'); }
function star(x, y) { sprite(['..#..', '.###.', '#####', '.###.', '.#.#.'], x, y, { '#': '#ffd36e' }); }
function shelf(x, y, w) { R(x, y, w, 3, OL); R(x + 1, y, w - 2, 2, '#8c5c3a'); R(x + 1, y, w - 2, 1, '#b07a4a'); R(x + 4, y + 3, 2, 4, '#5e3b22'); R(x + w - 6, y + 3, 2, 4, '#5e3b22'); }

/* ---------- the static layer: the room, the shelves, every resting bottle and tag ---------- */
function drawRoomBG() {
  R(0, 0, W, 150, '#16283a');
  for (let y = 14; y < 128; y += 8) for (let x = ((y / 8) % 2) * 4; x < W; x += 8) {
    const h = hash(x, y, 4); R(x, y, 7, 7, h < 0.08 ? '#1d3348' : h > 0.94 ? '#132232' : '#182c3f'); P(x, y, 'rgba(160,210,255,0.08)'); }
  for (let y = 14; y < 128; y += 8) R(0, y + 7, W, 1, '#0f1d2b');
  R(0, 128, W, 22, '#1a2236'); R(0, 128, W, 2, '#2c3858'); R(0, 130, W, 1, '#0f1424');
  for (let x = 4; x < W; x += 16) R(x, 133, 12, 14, '#1e2840');
  R(0, 11, W, 3, '#0f1424'); R(0, 13, W, 1, '#2c3858');
  for (let y = 150; y < H; y++) { const row = Math.floor((y - 150) / 6), off = (row % 2) * 10;
    for (let x = 0; x < W; x += 10) { const cell = Math.floor((x + off) / 20) % 2; R(x, y, 10, 1, (y - 150) % 6 === 0 ? '#0e101c' : cell ? '#1c1f33' : '#22263c'); } }
  R(0, 150, W, 2, '#0a0c16');
  const px = 24, py = 46;   // porthole: the harbour at night
  for (let j = -10; j <= 10; j++) { const hw = Math.round(Math.sqrt(100 - j * j)); R(px - hw, py + j, hw * 2 + 1, 1, OL); }
  for (let j = -9; j <= 9; j++) { const hw = Math.round(Math.sqrt(81 - j * j)); R(px - hw, py + j, hw * 2 + 1, 1, '#8a6a3a'); }
  for (let j = -7; j <= 7; j++) { const hw = Math.round(Math.sqrt(49 - j * j)); R(px - hw, py + j, hw * 2 + 1, 1, j > 3 ? '#112d4e' : '#0b1430'); }
  P(px - 4, py - 3, '#ffffff'); P(px + 3, py - 5, '#cfe8ff'); P(px + 5, py - 1, '#9fc8f0'); R(px - 7, py + 3, 15, 1, '#3b78a8');
  R(8, 98, 30, 52, OL); R(10, 100, 26, 50, '#5e3b2a'); R(10, 100, 26, 1, '#7a5038');   // the door back to town
  R(13, 104, 9, 18, '#4a2d20'); R(24, 104, 9, 18, '#4a2d20'); R(13, 126, 9, 20, '#4a2d20'); R(24, 126, 9, 20, '#4a2d20'); R(32, 126, 2, 3, '#ffcf6b');
  K.doorSign(9, 88, { pad: 3, inset: 2, rim: null });
  for (let i = 0; i < 3; i++) R(346, 196 + i * 2, 46, 1, i === 0 ? '#8a5a34' : '#5e3b22');   // the pallet for older contests
  R(345, 195, 48, 1, OL); R(346, 201, 4, 4, '#4a2d20'); R(367, 201, 4, 4, '#4a2d20'); R(388, 201, 4, 4, '#4a2d20');
  for (let x = 348; x < 392; x += 7) R(x, 193, 5, 2, '#8a5a34');
}
function drawBench() {
  R(84, 165, 250, 12, OL); R(85, 166, 248, 9, '#4a5070'); R(85, 166, 248, 2, '#7a82a8'); R(85, 174, 248, 1, '#2a2e48');
  R(85, 176, 248, 8, '#3a2a34'); R(85, 176, 248, 1, '#5e4252'); R(85, 183, 248, 1, OL);
  for (const lx of [92, 322]) { R(lx - 1, 184, 6, 26, OL); R(lx, 184, 4, 25, '#2e2230'); R(lx, 184, 1, 25, '#4a3a4a'); }
}
function sigNow() { return [VI, revealed() ? 1 : 0, step(), B ? B.state : '-', placeboWhite() ? 1 : 0].join('|'); }
function placeboWhite() { const k = animK('unblind'); return k === null ? revealed() : k > 0.45; }
function titleText(s) { return fitText(s ? (VI ? 'OLDER: ' : 'CONTEST: ') + s.title + (s.date ? ', ' + K.dayText(s.date).slice(4) : '') : 'NO CONTEST HAS RUN YET', 232); }
function buildStatic() {
  STATIC_SIG = sigNow();
  STATIC = layer(W, H, () => {
    drawRoomBG();
    const b = B, st = b ? b.state : 'missing';
    if (b && st !== 'missing') R(LAY.lampX, 14, 1, 101, '#2a2e48');   // the lamp's cord, behind everything
    const sub = titleText(b);
    textC(sub, 201, 16, OL); textC(sub, 200, 15, b ? '#9aa3d8' : '#7a7f99');
    if (!b || st === 'missing') { drawShelvesEmpty(); drawBench(); drawCrates(); return; }
    glow(glowSp('#9fd4ff', 40, 10, 0.08), 200, 44, 1);
    if (LAY.tops.length || st === 'working') shelf(TOPS[0], 58, TOPS[1]);
    shelf(MID[0], 112, MID[1]);
    drawBench();
    const white = placeboWhite();
    LAY.tops.forEach(o => { drawBottle(o.g, o.c.control && white ? (o.c.fill == null ? 'whitedust' : 'white') : (o.c.fill == null ? 'dust' : 'glass'), o.c.fill); if (o.c.wild) star(o.g.cx - 2, o.g.top - 11); if (o.c.first) rosette(o.g.cx - 2, o.g.top - 12); });
    if (LAY.topMore.length) moreSign(LAY.topMoreAt, 46, LAY.topMore.length);
    const nTop = LAY.tops.length + LAY.topMore.length;
    const topText = st === 'working' ? (b.count || nTop) + ' SEALED ENTRIES' : nTop ? nTop + (b.rounds === 2 ? ' OUT IN ROUND 1' : ' NOT SCORED') : '';
    if (topText) { textC(topText, 217, 66, OL); textC(topText, 216, 65, '#9aa3d8'); }
    else if (b.rounds === 1 && b.measure === 'rank') { const t1 = 'ROUND 1 ONLY: ' + b.count + ' ENTRIES, ' + b.judges.r1 + ' JUDGES'; textC(t1, 217, 51, OL); textC(t1, 216, 50, '#9aa3d8'); }
    LAY.mid.forEach(o => {
      const c = o.c, pl = c.control && white;
      drawBottle(o.g, pl ? 'white' : 'glass', c.fill);
      if (c.wild) star(o.g.cx - 2, o.g.base - 12);
      if (c.first) rosette(o.g.cx + (o.g.bw >> 1) - 1, o.g.base - 8);
      if (o.big) tag(o.g.cx + 3, o.g.top + 1, o.g.cx + 11 + (o.tw >> 1), o.g.top + 8, tagFace([pl ? 'PLACEBO' : c.code], o.tw, 9, pl ? ['#e8ecf6', '#3a3e5e', PAPER[2]] : PAPER), null, 0);
      else { textC(c.code, o.g.cx + 1, o.g.top - 10, OL); textC(c.code, o.g.cx, o.g.top - 11, pl ? '#e8ecf6' : '#b9c3dc'); }
    });
    if (LAY.midMore.length) moreSign(LAY.midMoreAt, 98, LAY.midMore.length);
    LAY.finals.forEach(o => {
      const c = o.c; drawBottle(o.g, c.control && white ? 'white' : 'glass', c.fill);
      if (c.wild) star(o.g.cx - 2, o.g.base - 18);
      if (c.first) rosette(o.g.cx + (o.g.bw >> 1) - 2, o.g.base - 9);
      if (!c.pick) { const tw = Math.max(25, tagWidth(c.code)); tag(o.g.cx + 4, o.g.top + 1, o.g.cx + 13 + (tw >> 1), o.g.top + 9, tagFace([c.code], tw, 9, PAPER), null, 0); }
    });
    if (b.rounds) drawLegend(b);
    const ft = LAY.finals.length ? (b.finalistsBuilt ? 'FINALISTS: BUILT AND TRIED' : 'TOP ' + LAY.finals.length + ': THE FINALISTS')
      : st === 'working' ? 'NO FINALISTS YET' : st === 'error' ? 'FINALISTS UNKNOWN' : '';
    if (ft) { textC(fitText(ft, 236), 209, 200, OL); textC(fitText(ft, 236), 208, 199, st === 'working' ? '#8fe3ff' : st === 'error' ? '#ff9a9a' : '#9aa3d8'); }
    drawCrates();
  });
}
function moreSign(cx, y, n) { const s = '+' + n, w = textW(s) + 6; R(cx - (w >> 1), y, w, 9, OL); R(cx - (w >> 1) + 1, y + 1, w - 2, 7, '#161b38'); textC(s, cx, y + 2, '#cfd6e8'); }
function legendWords(b) {
  if (b.measure === 'r2wins') return ['= WINS', 'ROUND 2', 'FULL=' + b.full];
  if (b.measure === 'score') return ['= SCORE', 'FINAL', 'FULL=' + b.full];
  return ['= RANK', 'ROUND 1', 'FULL=1ST'];
}
function drawLegend(b) {
  const lx = 46, ly = 66; R(lx, ly, 48, 34, OL); R(lx + 1, ly + 1, 46, 32, '#161b38'); R(lx + 1, ly + 1, 46, 1, '#2b336a'); P(lx + 23, ly - 1, '#ffd36e');
  drawBottle(bottleGeo(lx + 8, ly + 27, 6, 12, 2, 3), 'glass', 0.6);
  const [a, c, d] = legendWords(b);
  textL('LIQUID', lx + 15, ly + 4, '#eef1ff'); textL(a, lx + 15, ly + 11, '#ff9ab8');
  textL(c, lx + 15, ly + 18, '#9aa3d8'); textL(fitText(d, 32), lx + 15, ly + 25, '#9aa3d8');
}
function drawShelvesEmpty() {
  shelf(TOPS[0], 58, TOPS[1]); shelf(MID[0], 112, MID[1]);
  for (let i = 0; i < 40; i++) P(132 + Math.floor(hash(i, 1, 3) * 168), 57 - Math.floor(hash(i, 2, 3) * 2), '#3a3e52');
  for (let i = 0; i < 40; i++) P(102 + Math.floor(hash(i, 4, 3) * 214), 111 - Math.floor(hash(i, 5, 3) * 2), '#3a3e52');
}
function crateList() { return SPR.map((s, i) => ({ s, i })).filter(o => o.i !== VI); }
function crateBoxes() {
  const list = crateList(), shown = list.slice(0, list.length > 6 ? 5 : 6);
  return { list, shown, boxes: shown.map((o, k) => [348 + (k % 3) * 15, 182 - Math.floor(k / 3) * 11, 14, 10]), more: list.length - shown.length };
}
function drawCrates() {
  const { list, shown, boxes, more } = crateBoxes();
  shown.forEach((o, k) => { const [x, y, w, h] = boxes[k];
    R(x, y, w, h, OL); R(x + 1, y + 1, w - 2, h - 2, '#8a5a34'); R(x + 1, y + 1, w - 2, 1, '#b07a4a'); R(x + 1, y + 4, w - 2, 1, '#5e3b22');
    line(x + 1, y + 1, x + w - 2, y + h - 2, '#6e4628');
    const c = ST[o.s.state] || ST.missing; R(x + 4, y + 3, 6, 4, OL); R(x + 5, y + 4, 4, 2, o.s.state === 'waiting' || o.s.state === 'error' ? c.lamp : '#e8ecf6');
    if (o.i === 0) P(x + w - 3, y + 2, '#ffd36e');
  });
  if (more > 0) moreSign(348 + 2 * 15 + 7, 171, more);
  const lbl = list.length ? (VI ? 'OTHER' : list.length + ' OLDER') : 'NO OLDER', l2 = list.length === 1 ? 'CONTEST' : 'CONTESTS', y0 = list.length > 3 ? 158 : 169;
  textC(lbl, 370, y0, OL); textC(lbl, 369, y0 - 1, '#9aa3d8'); textC(l2, 370, y0 + 7, OL); textC(l2, 369, y0 + 6, '#9aa3d8');
}

/* ---------- per frame ---------- */
function drawStatus(t) {
  const st = B ? B.state : 'missing', c = ST[st], blink = st === 'ok' || st === 'missing' || K.still ? true : st === 'error' ? (t % 0.5) < 0.3 : (t % 1.1) < 0.7;
  R(330, 14, 9, 9, OL); R(331, 15, 7, 7, blink ? c.lamp : '#2a2e44'); if (blink && st !== 'missing') { P(332, 16, c.hi); P(333, 16, c.hi); P(332, 17, c.hi); }
  if (blink && c.glow) glow(glowSp(c.glow, 10, 9, st === 'ok' ? 0.3 : 0.45), 334, 18, 0.9);
  const word = st === 'ok' && B.pick && !B.pick.code ? 'DECIDED' : WORDS[st];
  textL(word, 342, 16, st === 'missing' ? '#7a7f99' : c.txt);
  SEEN.word = word;
}
function lampK() { // 0 = off, 1 = fully on (the pick lamp)
  if (!B || B.state !== 'ok' || !B.pick || !B.pick.code) return 0;
  const k = animK('turn'); if (k === null) return 1;
  return Math.max(0, Math.min(1, k / 0.35));
}
function drawLamp(t) {
  const lx = LAY.lampX, on = lampK(), flick = on > 0 && on < 1 ? (Math.floor(t * 14) % 3 === 0 ? 0.3 : 1) : 1, k = on * flick;
  R(lx - 5, 115, 11, 2, '#4a5070'); R(lx - 7, 117, 15, 4, OL); R(lx - 6, 117, 13, 3, '#5e6690'); R(lx - 6, 117, 13, 1, '#8a92b8');
  R(lx - 2, 121, 5, 2, on > 0 ? '#fff0c0' : '#3a3e5a');
  SEEN.lamp = on;
  if (k > 0) {
    g.globalAlpha = 0.10 * k; g.fillStyle = '#ffc46a';
    for (let y = 123; y < 166; y += 1) { const hw = 6 + (y - 123) * 0.5; g.fillRect(Math.round(lx - hw), y, Math.round(hw * 2 + 1), 1); }
    g.globalAlpha = 1; glow(glowSp('#ffb45a', 30, 24, 0.3), lx, 144, k); glow(glowSp('#ffc46a', 44, 7, 0.22), lx, 168, k);
    const td = B && B.state === 'working' ? t : 0;   // 0.2.1: the dust in its light drifts only while the judges rank
    if (!K.still) for (let i = 0; i < 4; i++) { const ph = (td * 0.15 + i * 0.25) % 1; P(lx - 10 + ((i * 7) % 20), 126 + Math.floor(ph * 38), 'rgba(255,230,170,' + (0.5 * k).toFixed(2) + ')'); }
  }
}
function drawPickTag() {
  const o = LAY.finals.find(f => f.c.pick); if (!o) return;
  const lines = nameLines(o.c.name, LAY.tagMax), w = Math.max(25, tagWidth(o.c.code), Math.max(...lines.map(textW)) + 6), h = lines.length * 7 + 3;
  const k = animK('turn'), turn = k === null ? 1 : Math.max(0, Math.min(1, (k - 0.25) / 0.6));
  tag(o.g.cx + 4, o.g.top + 1, o.g.cx + 13 + (w >> 1), o.g.top + 9, tagFace([o.c.code], w, h, PAPER), tagFace(lines, w, h, GOLD), turn);
  if (turn > 0.3 && turn < 0.7 && !K.still) { P(o.g.cx + 13 + w, o.g.top + 6, '#fff2a0'); P(o.g.cx + 10 + w, o.g.top + 12 + h, '#fff2a0'); }
  SEEN.pickTag = turn > 0.5 ? lines : [o.c.code];
}
function drawPickOne(t) { // waiting, step 2: an amber PICK ONE tag on every finalist
  const on = K.still || Math.sin(t * 3.2) > -0.4;
  LAY.finals.forEach(o => {
    const x = o.g.cx + 33, y = o.g.top + 19;
    R(x, o.g.top + 18, 1, 2, '#cfd6e8');
    const f = tagFace(['PICK ONE'], textW('PICK ONE') + 6, 9, AMBER); g.drawImage(f, Math.round(x - f.width / 2), y + 1);
    if (on) glow(glowSp('#ff9a1f', 14, 7, 0.35), x, y + 5, 0.8);
  });
  SEEN.pickOne = LAY.finals.length;
}
function envState() { // 'sealed' | 'opening' | 'open' (the card shows ? / PICK / NONE)
  if (!B || B.state !== 'waiting' && B.state !== 'ok') return null;
  const k = animK('unblind'); if (k !== null && k < 1) return 'opening';
  return step() === 'unblind' ? 'sealed' : 'open';
}
function envCard() { return B.state === 'ok' ? (B.pick && B.pick.code ? 'PICK' : 'NONE') : '?'; }
function drawEnvelope(t) {
  const es = envState(); if (!es) return;
  const x = 172, y = 176, w = 36, h = 15;
  SEEN.envelope = es === 'open' ? 'open:' + envCard() : es;
  if (es === 'open') {
    R(x, y + 4, w, h - 4, OL); R(x + 1, y + 5, w - 2, h - 6, '#a86a1a'); R(x + 1, y + 5, w - 2, 1, '#d08a2a');
    const card = envCard();
    R(x + 4, y + 1, w - 8, 9, OL); R(x + 5, y + 2, w - 10, 7, '#ffe7b0'); textC(card, x + w / 2, y + 3, card === '?' ? '#c06a1a' : '#3a1e10');
    return;
  }
  const k = es === 'opening' ? Math.min(1, animK('unblind') / 0.3) : 0;
  const pulse = es === 'sealed' ? (K.still ? 0.9 : 0.65 + 0.35 * Math.sin(t * 4)) : 0.4;
  glow(glowSp('#ff9a1f', 16, 12, 0.5), x + w / 2, y + h / 2, pulse); glow(glowSp('#ff9a1f', 16, 12, 0.5), x + w / 2, y + h / 2 - 6, pulse * 0.6);
  R(x, y, w, h, OL); R(x + 1, y + 1, w - 2, h - 2, '#ffb020'); R(x + 1, y + h - 2, w - 2, 1, '#c06a1a'); R(x + 1, y + 1, w - 2, 1, '#ffd27a');
  const fh = Math.round(6 * (1 - 2 * k));
  for (let i = 0; i < w - 2; i++) { const d = Math.abs(i - (w - 3) / 2) / ((w - 3) / 2), yy = Math.round(fh * (1 - d));
    if (fh >= 0) P(x + 1 + i, y + 1 + yy, '#c06a1a'); else { R(x + 1 + i, y + 1 + yy, 1, -yy, '#e09a2a'); P(x + 1 + i, y + 1 + yy, OL); } }
  if (k < 0.5) { R(x + 16, y + 6, 4, 3, '#b8312a'); P(x + 17, y + 7, '#e05a4a'); }
  textC('UNBLIND?', x + w / 2, y + h - 7, '#3a1e10');
  if (k > 0.3) { const cy = y - Math.round(6 * Math.min(1, (k - 0.3) / 0.5)); R(x + 7, cy, w - 14, 5, OL); R(x + 8, cy + 1, w - 16, 3, '#ffe7b0'); }
}
function judgesShown() {
  const r2 = B.why === 'r2', n = Math.max(1, Math.min(6, r2 ? B.judges.r2of : B.judges.r1of)), done = Math.min(n, r2 ? B.judges.r2 : B.judges.r1);
  return { r2, n, done };
}
function drawWorking(t) {
  const { n, done } = judgesShown(), x0 = 210 - n * 15, y = 74;
  R(x0 - 8, y, n * 30 + 2, 3, OL); R(x0 - 7, y + 1, n * 30, 1, '#3a6a7a');
  for (let i = 0; i < n; i++) {
    const cx = x0 + 7 + i * 30, fin = i < done;
    R(cx - 2, y + 3, 5, 4, OL); R(cx - 1, y + 3, 3, 3, fin ? '#3ddc7a' : '#4fe0ff');
    textC(String.fromCharCode(65 + i), cx + 1, y + 9, fin ? '#7ff0a8' : '#8fe3ff');
    if (!fin) { const ph = K.still ? 0.5 : (t * 1.4 + i * 0.27) % 1; P(cx, y + 16 + Math.floor(ph * 18), ph < 0.85 ? '#9ff6ff' : '#4fe0ff'); glow(glowSp('#6fffe0', 12, 9, 0.3), cx, y + 6, 0.6); }
  }
  const all = B.judges.r1of > 6 || B.judges.r2of > 6 ? ' (' + (B.why === 'r2' ? B.judges.r2 + ' OF ' + B.judges.r2of : B.judges.r1 + ' OF ' + B.judges.r1of) + ')' : '';
  const s = (B.say || 'JUDGES ARE STILL RANKING') + (B.why === 'cards' ? '' : ': ' + done + ' OF ' + n + ' DONE' + all);
  textC(fitText(s, 220), 210, 101, OL); textC(fitText(s, 220), 209, 100, '#8fe3ff');
  SEEN.judges = B.why === 'cards' ? null : [done, n]; SEEN.say = s;
}
function drawError(t) {
  const bx = 120, by = 74, bw = 178, rows = wrapText(B.error || B.say || "CAN'T READ IT", bw - 30).slice(0, 3), bh = 16 + rows.length * 7;
  R(bx, by, bw, bh, OL); R(bx + 1, by + 1, bw - 2, bh - 2, '#5a1e24'); R(bx + 1, by + 1, bw - 2, 1, '#ff7a7a');
  if (K.still || (t % 0.5) < 0.3) glow(glowSp('#ff3a3a', 14, 12, 0.45), bx + 13, by + 11, 0.6);
  sprite(['.###.', '#...#', '...#.', '..#..', '.....', '..#..'], bx + 8, by + 5, { '#': '#ffffff' });
  textL(fitText(B.say || "CAN'T READ IT", bw - 28), bx + 20, by + 5, '#ffd0d0');
  rows.forEach((r, i) => textL(r, bx + 20, by + 13 + i * 7, '#ff9a9a'));
  SEEN.say = B.say || "CAN'T READ IT";
}
function drawMissingNote() {
  g.fillStyle = 'rgba(6,8,20,0.5)'; g.fillRect(0, 14, W, H - 14);
  textC('NO CONTEST ON THE BENCH', 209, 140, OL); textC('NO CONTEST ON THE BENCH', 208, 139, '#8a8fa8');
  const s = B ? 'NO ENTRIES YET' : 'NO CONTEST FILE FOUND YET';
  textC(s, 209, 148, OL); textC(s, 208, 147, '#6a6e84');
  SEEN.say = s;
}
function drawSampleBadge() {
  const w = Math.max(textW('SAMPLE'), textW('MADE UP')) + 8, x = 42, y = 106;   // on the wall under the legend, clear of the caption
  R(x, y, w, 17, OL); R(x + 1, y + 1, w - 2, 15, '#3a2a10'); R(x + 1, y + 1, w - 2, 1, '#ffd36e');
  textC('SAMPLE', x + w / 2, y + 3, '#ffd36e'); textC('MADE UP', x + w / 2, y + 10, '#ffd36e');
}

/* ---------- sound ---------- */
function play(kind, delayMs) {
  if (delayMs) { timers.push(setTimeout(() => play(kind), delayMs)); return; }
  const ac = sfx.ctx(); if (!ac || !sfx.out()) return; const t = ac.currentTime + 0.02;
  if (kind === 'enter') { sfx.tone(t, 1760, 0.3, 0.06, 'sine'); sfx.tone(t + 0.07, 2349, 0.35, 0.05, 'sine'); }
  else if (kind === 'clink') { sfx.tone(t, 2637, 0.18, 0.08, 'sine'); sfx.tone(t + 0.02, 3520, 0.12, 0.04, 'sine'); }
  else if (kind === 'paper') { sfx.noise(t, 0.12, 0.25, 2400); sfx.noise(t + 0.13, 0.18, 0.2, 1600); }
  else if (kind === 'tick') sfx.tone(t, 1200, 0.05, 0.05, 'square');
  else if (kind === 'lamp') { sfx.tone(t, 110, 0.08, 0.08, 'square'); sfx.noise(t, 0.04, 0.12, 900); }
  else if (kind === 'chime') { sfx.tone(t, 659.3, 0.9, 0.12, 'triangle'); sfx.tone(t + 0.12, 880, 1.1, 0.1, 'triangle'); sfx.tone(t + 0.24, 1318.5, 1.2, 0.05, 'sine'); }
  else if (kind === 'wood') { sfx.noise(t, 0.1, 0.2, 400, 'lowpass'); sfx.tone(t + 0.02, 180, 0.12, 0.08, 'triangle'); }
  else if (kind === 'error') { sfx.tone(t, 220, 0.12, 0.08, 'square'); sfx.tone(t + 0.16, 196, 0.16, 0.08, 'square'); }
  else if (kind === 'drip') { sfx.tone(t, 1900, 0.06, 0.05, 'sine'); sfx.tone(t + 0.05, 1400, 0.08, 0.04, 'sine'); }
}

/* ---------- actions ---------- */
function openHref(href, what) { if (href && !sample) { K.open(href); K.toast('OPENING ' + what); } else K.toast(sample ? 'SAMPLE: NOTHING TO OPEN' : 'NO FILE FOR ' + what + ' YET'); }
function unblind() {
  if (!B || B.state !== 'waiting' || step() !== 'unblind' || anim && anim.kind === 'unblind' && animK('unblind') < 1) return;
  startAnim('unblind', 0);
  seen.add(seenKey()); LAY = layout(B); STATIC = null;
  if (!sample) try { localStorage.setItem(SEEN_KEY, JSON.stringify([...seen].slice(-60))); } catch (e) { /* not remembered */ }
  K.toast(sample ? 'SAMPLE: UNBLINDED' : 'UNBLINDED ON THIS SCREEN: NAMES ON HOVER. NOTHING IS WRITTEN');
}
/* a viewer's own click ends the scripted demo, so the script never redoes (or undoes) what they did */
function takeOver() { if (!demo) return; timers.forEach(clearTimeout); timers = []; cursor = null; caption = ''; }
function clickFinal(o) {
  const c = o.c, picking = B.state === 'waiting' && step() === 'pick'; play('clink');
  if (sample && picking) { takeOver(); recordSamplePick(c); return; }
  if (picking) K.toast('RECORD YOUR PICK IN THE CONTEST FILE: ' + c.code);
  if (c.built && c.built.href && !sample) { K.open(c.built.href); if (!picking) K.toast('OPENING THE BUILD OF ' + c.code); }
  else if (!picking) openHref(B.hrefs.result || B.hrefs.tally, c.code);
}
function recordSamplePick(c) { // demo only: the sample contest records a pick, so the moment can be seen
  B.cards.forEach(x => { x.pick = x === c; }); B.pick = { code: c.code, name: c.name, text: '', note: '', href: '' };
  B.state = 'ok'; B.revealed = true; LAY = layout(B); STATIC = null; startAnim('turn', 0.1);
  K.toast('SAMPLE PICK: ' + c.name.toUpperCase());
}
function toCrate(i) {
  VI = i; showContest(true);
  K.toast(VI ? 'ON THE BENCH: ' + SPR[VI].title.toUpperCase() : 'BACK TO THE NEWEST CONTEST');
}

/* ---------- hover / click targets ---------- */
function cardWord(c) { return revealed() ? c.code + ': ' + c.name : c.code + ' (NAME SEALED)'; }
function placeboLine(c) {
  const beat = B.cards.filter(x => !x.control && x.place > c.place).length;
  const base = 'PLACEBO: THE CONTROL. THE SAME BRIEF, ANSWERED THE PLAIN WAY, SO THE CONTEST HAD SOMETHING REAL TO BEAT.';
  if (c.first) return base + ' IT CAME FIRST: FAIR AND SQUARE, THE ENTRIES DID NOT BEAT IT THIS TIME.';
  return base + (beat ? ' IT STILL OUTSCORED ' + beat + ' ENTR' + (beat === 1 ? 'Y' : 'IES') + '. GOOD SHOW.' : ' IT DID ITS JOB: IT GAVE THE OTHERS A BAR TO CLEAR.');
}
function scoreLine(c) {
  if (B.state === 'working') return 'SEALED: NO SCORE YET';
  const of = B.of ? ' OF ' + B.of : '';
  if (B.measure === 'r2wins' || B.measure === 'score') {
    const unit = B.measure === 'r2wins' ? ' ROUND 2 WINS' : ' POINTS';
    if (c.score != null) return c.score + of + unit;
    return (B.rounds === 2 ? 'OUT IN ROUND 1' + (c.mean != null ? ' (MEAN RANK ' + c.mean + ')' : '') : 'NO SCORE') + ': SO NO LIQUID';
  }
  return c.mean != null ? 'ROUND 1: MEAN RANK ' + c.mean + ' OF ' + B.count + (c.ranks.length ? ' (RANKS ' + c.ranks.join(', ') + ')' : '') : 'NOT SCORED';
}
function cardTip(c, fin) {
  const tip = [cardWord(c), scoreLine(c)];
  if (c.first) tip.push(c.pick ? "THE JUDGES' FIRST PLACE, AND YOUR PICK" : "THE JUDGES' FIRST PLACE (BLUE ROSETTE)");
  else if (c.pick) tip.push('YOUR PICK (THE JUDGES PLACED IT ' + ordinal(c.place) + ')');
  if (c.wild) tip.push('WILD CARD (GOLD STAR): KEPT FROM ROUND 1' + (B.rounds === 2 && c.score == null ? '. NOT IN ROUND 2, SO THE BOTTLE IS EMPTY' : ''));
  if (c.control && revealed()) tip.push(placeboLine(c));
  if (c.pick && B.pick && B.pick.note) tip.push(B.pick.note);
  if (fin) tip.push(c.built ? 'FINALIST: BUILT AND TRIED' + (c.built.href && !sample ? '. CLICK: OPEN ITS BUILD' : '') : 'FINALIST');
  if (fin && B.state === 'waiting' && step() === 'pick') tip.push(sample ? 'CLICK: PICK THIS ONE (SAMPLE)' : 'PICK ONE: RECORD IT IN THE CONTEST FILE');
  return tip;
}
function ordinal(n) { return n + (n % 10 === 1 && n % 100 !== 11 ? 'ST' : n % 10 === 2 && n % 100 !== 12 ? 'ND' : n % 10 === 3 && n % 100 !== 13 ? 'RD' : 'TH'); }
function geoBox(G, extra) { return [G.cx - (G.bw >> 1) - 1, G.top - 4, G.bw + 2 + (extra || 0), G.total + 5]; }
function legendTip(b) {
  const keyLine = 'EMPTY = OUT IN ROUND 1. BLUE ROSETTE = THE JUDGES\' FIRST. GOLD STAR = WILD CARD.';
  if (b.measure === 'r2wins') return ['HOW TO READ A BOTTLE', 'LIQUID = ROUND 2 WINS' + (b.of ? ' (OUT OF ' + b.of + ' HEAD-TO-HEADS)' : '') + '. A FULL BOTTLE IS THE TOP SCORE, ' + b.full + '.', keyLine];
  if (b.measure === 'score') return ['HOW TO READ A BOTTLE', 'LIQUID = THE SCORE' + (b.of ? ' (OUT OF ' + b.of + ')' : '') + '. A FULL BOTTLE IS THE TOP SCORE, ' + b.full + '.', keyLine.replace('OUT IN ROUND 1', 'NO SCORE')];
  return ['HOW TO READ A BOTTLE', 'LIQUID = THE ROUND 1 SCORE: EACH ENTRY\'S MEAN RANK FROM ' + b.judges.r1 + ' JUDGES, TURNED ROUND SO FIRST IS HIGH, SCALED SO THE BEST ENTRY IS FULL.', 'BLUE ROSETTE = THE JUDGES\' FIRST. GOLD STAR = WILD CARD.'];
}
function targets() {
  const out = [], b = B;
  out.push({ id: 'door', box: [8, 86, 30, 64], tip: ['BACK TO TOWN', 'OR PRESS ESC'], label: 'Door: back to town', onClick: K.leave });
  if (!b) { out.push({ id: 'empty', box: [84, 40, 250, 150], tip: ['NOT SET UP', 'NO CONTEST FILE WAS FOUND, SO THE SHELVES ARE EMPTY.'], label: 'Empty shelves: no contest yet', onClick: null }); addCrates(out); return out; }
  const sw = Math.min(232, textW(titleText(b)) + 12);
  out.push({ id: 'title', box: [200 - (sw >> 1), 13, sw, 9], tip: [b.title, (b.date ? K.dayText(b.date) + '. ' : '') + (sample ? 'SAMPLE CONTEST (MADE UP)' : b.name ? 'FROM: ' + b.name : ''), b.count + ' ENTRIES', sample ? '' : 'CLICK: OPEN THE CONTEST FILE'].filter(Boolean),
    label: 'Contest: ' + b.title, onClick: () => openHref(b.hrefs.result || b.href, 'THE CONTEST FILE') });
  const stTip = { ok: b.pick && b.pick.code ? 'PICKED: ' + b.pick.name : 'DECIDED: NONE OF THESE' + (b.pick && b.pick.text ? '. ' + b.pick.text : ''),
    waiting: step() === 'unblind' ? 'THE SCORES ARE IN AND SEALED. OPEN THE AMBER ENVELOPE.' : 'UNBLINDED. PICK ONE OF THE FINALISTS.',
    working: b.why === 'cards' ? 'THE ENTRIES ARE STILL BEING WRITTEN.' : 'THE JUDGES ARE STILL RANKING. NO SCORES YET.', error: b.error || "A FILE CAN'T BE READ. NOTHING IS GUESSED.", missing: 'NO ENTRIES HERE YET.' }[b.state];
  out.push({ id: 'status', box: [328, 13, 70, 11], tip: ['ROOM STATUS', stTip], label: 'Status: ' + stTip, onClick: null });
  if (b.state === 'missing') out.push({ id: 'empty', box: [84, 40, 250, 150], tip: [b.title, 'NO ENTRIES HERE YET.'], label: 'Empty bench', onClick: null });
  if (b.rounds) out.push({ id: 'legend', box: [46, 66, 48, 34], tip: legendTip(b), label: 'Legend: how to read a bottle', onClick: null });
  if (b.state === 'working' && b.why !== 'cards') { const { n, done, r2 } = judgesShown(); out.push({ id: 'judges', box: [210 - n * 15 - 8, 72, n * 30 + 2, 36], tip: ['THE JUDGES', (r2 ? 'ROUND 2: ' : 'ROUND 1: ') + done + ' OF ' + n + ' IN.', 'A LAMP DRIPS UNTIL ITS JUDGE IS IN.'], label: 'Judges: ' + done + ' of ' + n + ' in', onClick: null }); }
  LAY.tops.forEach((o, i) => out.push({ id: 'top-' + i, box: geoBox(o.g), tip: cardTip(o.c, false), label: cardWord(o.c), onClick: () => { play('clink'); openHref(b.hrefs.tally || b.hrefs.cards, 'THE TALLY'); } }));
  if (LAY.topMore.length) out.push({ id: 'top-more', box: [LAY.topMoreAt - 8, 44, 16, 13], tip: ['+' + LAY.topMore.length + ' MORE', LAY.topMore.slice(0, 12).map(c => c.code).join(' ') + (LAY.topMore.length > 12 ? ' ...' : '')], label: LAY.topMore.length + ' more entries', onClick: () => openHref(b.hrefs.tally || b.hrefs.cards, 'THE TALLY') });
  LAY.mid.forEach((o, i) => out.push({ id: 'mid-' + i, box: geoBox(o.g, o.big ? o.tw + 4 : 0), tip: cardTip(o.c, false), label: cardWord(o.c), onClick: () => { play('clink'); openHref(b.hrefs.tally || b.hrefs.result, 'THE TALLY'); } }));
  if (LAY.midMore.length) out.push({ id: 'mid-more', box: [LAY.midMoreAt - 8, 96, 16, 13], tip: ['+' + LAY.midMore.length + ' MORE', LAY.midMore.slice(0, 12).map(c => c.code).join(' ') + (LAY.midMore.length > 12 ? ' ...' : '')], label: LAY.midMore.length + ' more entries', onClick: () => openHref(b.hrefs.tally, 'THE TALLY') });
  LAY.finals.forEach((o, i) => out.push({ id: 'final-' + i, box: geoBox(o.g, 13 + Math.max(25, LAY.tagMax)), tip: cardTip(o.c, true), label: cardWord(o.c) + ', finalist', onClick: () => clickFinal(o) }));
  const es = envState();
  if (es) out.push({ id: 'envelope', box: [172, 176, 36, 15], tip: es === 'sealed' ? ['UNBLIND?', 'THE SCORES ARE IN, AND EVERY BOTTLE IS STILL SEALED. CLICK (OR U) TO OPEN THE ENVELOPE: NAMES SHOW ON HOVER AND THE PLACEBO STEPS FORWARD.', 'ON THIS SCREEN ONLY: NOTHING IS WRITTEN.']
    : b.state === 'ok' ? (b.pick && b.pick.code ? ['ENVELOPE OPENED', 'PICKED: ' + b.pick.name, sample ? '' : 'CLICK: OPEN WHERE THE PICK IS RECORDED'].filter(Boolean) : ['DECIDED: NONE OF THESE', b.pick && b.pick.text || '', b.pick && b.pick.note || ''].filter(Boolean))
    : ['UNBLINDED', 'NO PICK RECORDED YET. THE AMBER TAGS WAIT FOR YOUR CHOICE.', sample ? '' : 'CLICK: OPEN THE KEY'].filter(Boolean),
    label: es === 'sealed' ? 'Envelope: unblind the results' : 'Envelope: opened', onClick: () => es === 'sealed' ? (takeOver(), unblind()) : b.state === 'ok' ? openHref(b.pick && b.pick.href || b.hrefs.result, 'THE RESULT') : openHref(b.hrefs.key, 'THE KEY') });
  if (b.state !== 'missing' && b.state !== 'error') out.push({ id: 'lamp', box: [LAY.lampX - 7, 114, 15, 9], tip: ['THE PICK LAMP', b.state === 'ok' && b.pick && b.pick.code ? 'LIT: IT SHINES ON THE ENTRY YOU PICKED.' : b.state === 'ok' ? 'OFF: YOU PICKED NONE OF THESE.' : "OFF: NOTHING PICKED YET. THE JUDGES' WINNER IS NOT YOUR PICK UNTIL YOU SAY SO."], label: 'The pick lamp', onClick: null });
  if (b.state === 'error') out.push({ id: 'error', box: [120, 74, 178, 37], tip: [b.say || "CAN'T READ IT", b.error, sample ? '' : 'CLICK: OPEN THE FILE'].filter(Boolean), label: 'Error: ' + b.error, onClick: () => openHref(b.hrefs.tally || b.href, 'THE FILE') });
  addCrates(out);
  return out;
}
function addCrates(out) {
  const { shown, boxes, more, list } = crateBoxes();
  shown.forEach((o, k) => out.push({ id: 'crate-' + k, box: boxes[k], tip: [o.s.title, (o.s.date ? K.dayText(o.s.date) : '-') + (o.i === 0 ? ' (NEWEST)' : ''),
    o.s.state === 'waiting' ? 'WAITING FOR YOUR PICK' : o.s.pickWords ? 'PICK: ' + o.s.pickWords : o.s.state === 'error' ? "CAN'T READ IT" : o.s.state === 'working' ? 'STILL BEING JUDGED' : 'NO PICK', 'CLICK: PUT IT ON THE BENCH'],
    label: 'Crate: ' + o.s.title, onClick: () => toCrate(o.i) }));
  if (more > 0) out.push({ id: 'crates-more', box: [372, 170, 16, 11], tip: ['+' + more + ' MORE CONTESTS', list.slice(shown.length, shown.length + 6).map(o => o.s.title).join(', ')], label: more + ' more contests', onClick: () => toCrate(list[shown.length].i) });
  if (!list.length) out.push({ id: 'pallet', box: [344, 164, 50, 44], tip: ['OLDER CONTESTS', 'EACH FINISHED CONTEST IS PACKED INTO A CRATE HERE. NONE YET.'], label: 'No older contests', onClick: null });
}

/* ---------- demo: the core moment on a timer ---------- */
const CAPTION = { ok: "THE PICK'S LABEL TURNS ROUND UNDER THE WARM LAMP", waiting: 'SEALED: THE AMBER ENVELOPE ASKS: UNBLIND?', pick: 'UNBLINDED: AMBER TAGS ASK YOU TO PICK ONE',
  working: 'THE JUDGES ARE STILL RANKING', error: "A CONTEST FILE CAN'T BE READ: NOTHING IS GUESSED", missing: 'NO CONTEST HAS RUN YET' };
function runDemo() {
  timers.forEach(clearTimeout); timers = [];
  const at = (s, f) => timers.push(setTimeout(f, s * 1000));
  if (forced) { caption = CAPTION[forced] || ''; return; }
  if (!B) return;
  const env = [190, 183], pk = () => LAY.finals.find(f => f.c.code === 'C14') || LAY.finals[0];
  at(0.2, () => { caption = '1. SEALED BOTTLES. LIQUID = ROUND 2 WINS'; });
  at(1.0, () => { caption = '2. THE AMBER ENVELOPE WAITS FOR YOU'; cursor = [env[0] + 30, env[1] + 20, false]; });
  at(1.4, () => { cursor = [env[0] + 8, env[1] + 6, false]; });
  at(1.7, () => { cursor = [env[0], env[1], true]; unblind(); caption = '3. UNBLINDED: THE PLACEBO STEPS FORWARD'; });
  at(2.0, () => { cursor = null; });
  at(3.9, () => { caption = "4. PICK ONE: YOUR CALL, NOT THE JUDGES'"; const o = pk(); if (o) cursor = [o.g.cx + 20, o.g.top + 30, false]; });
  at(4.4, () => { const o = pk(); if (o) cursor = [o.g.cx + 4, o.g.top + 16, false]; });
  at(4.7, () => { const o = pk(); if (o && B.state === 'waiting') { cursor = [o.g.cx + 2, o.g.top + 14, true]; play('clink'); recordSamplePick(o.c); } caption = "5. THE PICK'S LABEL TURNS ROUND UNDER THE LAMP"; });
  at(5.0, () => { cursor = null; });
  at(6.2, () => { const p = B.pick, c = B.cards.find(x => x.pick); caption = p && c ? 'PICKED: ' + p.name.toUpperCase() + ', ' + c.score + ' OF ' + B.of + ' WINS' : ''; });
  at(14, () => { setup(null, { demo: true, qs: demo.qs, state: null }); runDemo(); });
}
function drawDemoLayer() {
  if (caption) { const cw = textW(caption) + 12, x = Math.round(206 - cw / 2); R(x - 1, 211, cw + 2, 13, OL); R(x, 212, cw, 11, '#161b38'); R(x, 212, cw, 1, '#ffd36e'); textL(caption, x + 6, 215, '#ffd36e'); }
  if (cursor) K.cursor(cursor[0], cursor[1], cursor[2], false);
}

/* ---------- the room ---------- */
function roomState() {
  if (forced) return forced === 'pick' ? 'waiting' : forced;
  if (sample) return B ? B.state : 'missing';
  return ST[RAW && RAW.state] ? RAW.state : SPR.length ? SPR[0].state : 'missing';
}
registerRoom('contest', {
  enter(data, opts) {
    demo = opts.demo ? { qs: opts.qs } : null; anim = null; caption = ''; cursor = null; BID = str(opts.id);
    setup(data, opts);
    const st = roomState();
    timers.push(setTimeout(() => play(st === 'error' ? 'error' : st === 'working' ? 'drip' : 'enter'), 350));
    if (demo) runDemo();
  },
  leave() { timers.forEach(clearTimeout); timers = []; anim = null; STATIC = null; caption = ''; cursor = null; demo = null; },
  draw(ctx, t) {
    SEEN = { lamp: 0, pickTag: null, pickOne: 0, envelope: null, judges: null, say: '' };
    if (!STATIC || STATIC_SIG !== sigNow()) buildStatic();
    g.drawImage(STATIC, 0, 0);
    drawStatus(t);
    if (!B || B.state === 'missing') { drawMissingNote(); if (sample) drawSampleBadge(); if (demo) drawDemoLayer(); return; }
    if (B.state === 'working') drawWorking(t);
    else if (B.state === 'error') drawError(t);
    drawLamp(t);
    LAY.mid.forEach((o, i) => bubbles(o.g, o.c.fill, t, i * 7 + 3));
    LAY.finals.forEach((o, i) => bubbles(o.g, o.c.fill, t, i * 11 + 5));
    drawPickTag();
    if (B.state === 'waiting' && step() === 'pick' && !(anim && anim.kind === 'unblind' && animK('unblind') < 1)) drawPickOne(t);
    drawEnvelope(t);
    if (anim && animK(anim.kind) >= 1) anim = null;
    if (sample) drawSampleBadge();
    if (demo) drawDemoLayer();
  },
  targets,
  hover() {},
  plate() { // the newest contest's entry count (as on the town's plate); the room's state
    const n = forced === 'missing' ? 0 : sample ? (SPR[0] ? SPR[0].count : 0) : RAW && RAW.num != null ? RAW.num : SPR[0] ? SPR[0].count : 0;
    return { num: String(n), state: roomState() };
  },
  busy() { const k = anim ? animK(anim.kind) : null; return (k !== null && k < 1) || (!!demo && timers.length > 0); },
  key(e) {
    const n = parseInt(e.key, 10);
    if (e.key === 'u' || e.key === 'U') { if (B && step() === 'unblind') takeOver(); unblind(); }
    else if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && SPR.length > 1) { e.preventDefault(); toCrate((VI + (e.key === 'ArrowRight' ? 1 : SPR.length - 1)) % SPR.length); }
    else if (demo && n >= 1 && n <= FORCE.length) {   // demo only: the real room never pretends
      timers.forEach(clearTimeout); timers = []; anim = null; cursor = null;
      setup(null, { demo: true, qs: demo.qs, state: FORCE[n - 1] }); runDemo();
      K.toast('SAMPLE: ' + FORCE[n - 1].toUpperCase());   // (a key alone doesn't redraw a still scene)
    }
  },
  seen() {
    const c = B, white = !!c && placeboWhite(), L = LAY || { finals: [], mid: [], tops: [], midMore: [], topMore: [] };
    return Object.assign({
      state: roomState(), step: step(), sample, caption, title: c ? c.title : '', onBench: VI, contests: SPR.length,
      finals: L.finals.map(o => o.c.code), mid: L.mid.length, midMore: L.midMore.length, tops: L.tops.length, topMore: L.topMore.length,
      rosettes: [...L.finals, ...L.mid, ...L.tops].filter(o => o.c.first).map(o => o.c.code),
      stars: [...L.finals, ...L.mid, ...L.tops].filter(o => o.c.wild).map(o => o.c.code),
      placebo: white ? [...L.finals, ...L.mid, ...L.tops].filter(o => o.c.control).map(o => o.c.code) : [],
      legend: c && c.rounds ? legendWords(c) : null, crates: crateBoxes().shown.length, cratesMore: Math.max(0, crateBoxes().more),
      revealed: revealed(),
    }, SEEN);
  },
});
})();
