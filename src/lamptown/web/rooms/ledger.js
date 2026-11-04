/* LEDGER: the two-signature register, inside the gate.
   Data: the building's room data from the `scan_glob` collector (collectors/ledger.py): one row per tool,
   newest first. A clerk's desk under a lamp; the open ledger holds 5 rows a page (click the page corner,
   or use the arrow keys, to turn). Each row needs two signatures:
     1. the scanner's stamp (its verdict, shape-coded: SAFE box + tick, CAUTION box + triangle, DO NOT
        octagon + cross), its risk as an ink bar out of 100 with the number, its flags as dots (6 dots at
        most, then the number says it);
     2. yours, in the same hand every time, with your call (install / skip / park) from the decisions file.
   Both in: the row is ruled closed in green. The cupboard is the installed folder (one bottle per
   installed item; items another app syncs in sit in a crate), locked twice: SCAN and YOU.
   States, each an object, not a tint:
     ok        green double rules, both keys turned
     waiting   an amber "WAITS FOR YOU" box in the row, its bottle waits on the desk, the YOU lock is shut
     working   a cyan sweep along the row being scanned, the SCAN key jiggles
     error     the row glows red with "!", the SCAN lock is forced, its bottle has a red tag
     missing   no scans: the lamp is off, the ledger is blank, the locks have no keys
   Grey rows never raise the state: home-made with no scan, scanned with no recorded call ("-", NO RECORD).
   Scale: 5 rows a page with a page count and bookmarks (8 at most) for pages that need you; 24 bottles
   (22 beside the crate), then "+N"; 2 bottles waiting on the desk, then "+N".
   Demo (?room=<id>&demo=1): fictional SAMPLE rows only. Your signature goes in, the row is ruled, the
   lock turns, the bottle goes in (+1). &state=ok|waiting|working|error|missing|norecord (keys 1-6) shows
   one state still; &rows=N pads the sample ledger; &page=N opens a page. */
(() => {
'use strict';
const { W, H, OL, ST, R, P, sprite, disc, hash, glow, glowSp, text, textW, textL, textC, fitText, layer, sfx } = K;
const NOW_T = () => performance.now() / 1000;
const PER_PAGE = 5, ROW0 = 121, PITCH = 14;
const INK = '#1c2350', HEAD = '#4a5490', RULE = '#8e9aca', PAPER = '#b4bedc', GREY_INK = '#6a7090', RED_INK = '#a8202a', CY_INK = '#11708a';
const COL = { name: 58, risk: 140, num: 166, flags: 178, stamp: 205, you: 254, word: 286, tick: 337 };
const NAME_W = 78, BAR = 21;
const KIND_STATE = { signed: 'ok', waiting: 'waiting', scanning: 'working', noscan: 'error', unreadable: 'error', 'skipped-in': 'error',
  homemade: 'missing', norecord: 'missing', unscanned: 'missing' };
const VERDICT = { SAFE: { word: 'SAFE', shape: 'safe', ink: '#2f4fb0' }, CAUTION: { word: 'CAUTION', shape: 'caution', ink: '#6a3aa8' },
  DO_NOT_INSTALL: { word: 'DO NOT', shape: 'block', ink: '#7a2448' } };
const CALL_INK = { install: INK, skip: '#5a3a6a', park: '#3a5a7a' };
const DEMO_STATES = ['ok', 'waiting', 'working', 'error', 'missing', 'norecord'];
const GLASS = [['#5ad1c8', '#8ff0e8'], ['#c79bff', '#e2ccff'], ['#ff8a9a', '#ffc4cc'], ['#7fd8ff', '#c4efff'], ['#b8f07a', '#e0ffc0'], ['#ffb36b', '#ffd9b0']];

let DATA = null, QS = null, ROWS = [], page = 0, flip = null, PAGES = {}, STATIC = null, WIN = null, demo = null, timers = [], lastE = -1, CUR_A = null, SEEN = {};

/* ---------- data ---------- */
const str = (v, d) => (v === null || v === undefined ? (d || '') : String(v));
const num = v => (typeof v === 'number' && isFinite(v) ? v : null);
const ISO = /^\d{4}-\d\d-\d\d[T ]\d\d:[0-9]{2}(:[0-9]{2}(\.\d+)?)?(Z|[+-]\d\d:?[0-9]{2})$/i;
const isoOr = v => (typeof v === 'string' && ISO.test(v) ? v : null);   // absolute times only
function mkRow(r) {
  r = r && typeof r === 'object' ? r : {};
  const kind = KIND_STATE[r.kind] ? r.kind : 'norecord';
  const s = r.scan && typeof r.scan === 'object' ? r.scan : null;
  const d = r.decision && typeof r.decision === 'object' && ['install', 'skip', 'park'].includes(r.decision.call) ? r.decision : null;
  return { tool: str(r.tool || r.name, 'tool').slice(0, 120), kind, state: KIND_STATE[kind], origin: ['own', 'third-party'].includes(r.origin) ? r.origin : 'unknown',
    installed: !!r.installed, scans: num(r.scans) || 0, when: isoOr(r.when), href: str(r.href),
    history: Array.isArray(r.history) ? r.history.filter(h => h && typeof h === 'object').slice(-4) : [],
    scan: s ? { verdict: s.verdict ? str(s.verdict).slice(0, 40) : null, word: str(s.word || s.verdict).slice(0, 40), severity: str(s.severity).slice(0, 20),
      risk: num(s.risk), flags: num(s.flags), when: isoOr(s.when), file: str(s.file).slice(0, 120), href: str(s.href), why: str(s.why).slice(0, 80),
      scanner: str(s.scanner).slice(0, 40) } : null,
    decision: d ? { call: d.call, date: /^\d{4}-\d\d-\d\d$/.test(str(d.date)) ? d.date : null, note: str(d.note).slice(0, 120) } : null };
}
/* fictional sample rows for ?demo=1 (never real data) */
const SAMPLE = ['kite-knots', 'tide-reader', 'gull-counter', 'harbour-lights', 'sail-mender', 'moss-indexer', 'lantern-check', 'paper-fold',
  'rope-ladder', 'otter-notes', 'wren-sketch', 'rain-gauge', 'salt-ledger', 'buoy-bell', 'net-mender', 'chart-table', 'pier-clock', 'crab-trap',
  'shell-sorter', 'tea-timer'];
function sampleScan(i, verdict) {
  const v = verdict || ['SAFE', 'CAUTION', 'SAFE', 'DO_NOT_INSTALL'][i % 4], risk = { SAFE: 3 + (i * 5) % 11, CAUTION: 24 + (i * 7) % 19, DO_NOT_INSTALL: 58 + (i * 5) % 31 }[v];
  return { verdict: v, word: v === 'DO_NOT_INSTALL' ? 'DO NOT INSTALL' : v, severity: risk > 50 ? 'HIGH' : risk > 20 ? 'MEDIUM' : 'LOW', risk,
    flags: v === 'SAFE' ? i % 3 : 2 + (i * 3) % 9, when: null, file: 'sample-scan.json', href: '', why: '', scanner: 'Sample scanner' };
}
function sampleRow(i, when) {
  const sc = sampleScan(i), skip = i % 3 === 2 || sc.verdict === 'DO_NOT_INSTALL';
  sc.when = when;
  return { tool: SAMPLE[i % SAMPLE.length] + (i >= SAMPLE.length ? '-' + (1 + Math.floor(i / SAMPLE.length)) : ''), kind: 'signed', state: 'ok',
    origin: 'third-party', installed: !skip, scans: 1, when, href: '', history: [], scan: sc,
    decision: { call: skip ? 'skip' : 'install', date: when ? when.slice(0, 10) : null, note: 'Sample row' } };
}
function isoAgo(days) { return new Date(K.nowMs() - days * 864e5).toISOString().slice(0, 16) + 'Z'; }
function setKind(r, kind) { r.kind = kind; r.state = KIND_STATE[kind]; return r; }
function sampleRows(st, n) { // the demo: a made-up ledger; the state goes to ONE row at the top of page 1
  const rows = [];
  for (let i = 0; i < Math.max(7, n); i++) rows.push(sampleRow(i, isoAgo(1 + i * 2)));
  rows.push(setKind({ tool: 'moss-indexer-2', origin: 'own', installed: true, scans: 0, when: isoAgo(40), href: '', history: [], scan: null, decision: null }, 'homemade'));
  const top = rows[0]; top.when = isoAgo(0); top.scan.when = top.when;
  if (!st || st === 'waiting') { top.decision = null; top.installed = false; top.origin = 'third-party'; setKind(top, 'waiting'); top.scan = sampleScan(5, 'CAUTION'); top.scan.when = top.when; }
  else if (st === 'working') rows.unshift({ tool: 'fog-horn', kind: 'scanning', state: 'working', origin: 'third-party', installed: false, scans: 1,
    when: isoAgo(0), href: '', history: [], scan: { verdict: null, word: '', file: 'fog-horn.json', href: '', when: isoAgo(0), why: '' }, decision: null });
  else if (st === 'error') { top.scan = null; top.scans = 0; top.installed = true; top.decision = null; setKind(top, 'noscan'); }
  else if (st === 'norecord') rows.forEach(r => { if (r.scan && r.scan.verdict) { r.decision = null; r.installed = true; setKind(r, 'norecord'); } });
  else if (st === 'missing') rows.length = 0;
  return rows;
}
function setup() {
  if (demo) {
    const n = Math.max(0, Math.min(200, parseInt(demo.qs.get('rows'), 10) || 0));
    ROWS = sampleRows(demo.state, n);
  } else ROWS = (DATA && Array.isArray(DATA.rows) ? DATA.rows : []).map(mkRow);
  const pg = parseInt(QS && QS.get('page'), 10);
  page = Math.max(0, Math.min(pages() - 1, (pg || 1) - 1));
  flip = null; PAGES = {}; STATIC = null;
}
const pages = () => Math.max(1, Math.ceil(ROWS.length / PER_PAGE));
const pageOf = i => Math.floor(i / PER_PAGE);
const has = k => ROWS.some(r => r.kind === k);
const firstOf = pred => ROWS.findIndex(pred);
const live = () => ROWS.filter(r => r.state !== 'missing');
function decErr() { return !demo && DATA && DATA.decisions && DATA.decisions.error ? String(DATA.decisions.error) : ''; }
function decFile() { const f = !demo && DATA && DATA.decisions && DATA.decisions.file; return f ? String(f).toUpperCase().slice(0, 40) : 'YOUR DECISIONS FILE'; }
function noun() { const n = DATA && /^[a-z][a-z -]{0,19}$/.test(str(DATA.noun)) ? DATA.noun : 'skill'; return n; }
function roomState() {
  if (decErr()) return 'error';
  const L = live().map(r => r.state);
  return ['error', 'waiting', 'working', 'ok'].find(s => L.includes(s)) || 'missing';
}
function installedRows() { return ROWS.filter(r => r.installed); }
function synced() { return demo ? 6 : Math.max(0, num(DATA && DATA.synced) || 0); }

/* ---------- names ---------- */
function shortName(s, maxW) { // drop trailing words before cutting letters: HARBOUR-LIGHTS-PLUS -> HARBOUR-LIGHTS
  s = String(s).toUpperCase(); if (textW(s) <= maxW) return s;
  const parts = s.split('-'); while (parts.length > 1 && textW(parts.join('-')) > maxW) parts.pop();
  const r = parts.join('-'); return textW(r) <= maxW ? r : fitText(r, maxW);
}
const plural = (n, w) => n + ' ' + w + (n === 1 ? '' : 'S');

/* ---------- the notice board: what the gate says today ---------- */
function notice() {
  const st = roomState(), nm = i => shortName(ROWS[i].tool, 80);
  if (decErr()) return [st, "CAN'T READ YOUR DECISIONS", 'FIX ' + decFile()];
  if (st === 'error') {
    const i = firstOf(r => r.state === 'error'), r = ROWS[i], n = ROWS.filter(x => x.kind === r.kind).length;
    if (r.kind === 'noscan') return [st, n === 1 ? '1 TOOL GOT IN UNSCANNED' : n + ' TOOLS GOT IN UNSCANNED', nm(i) + ': NO SCAN'];
    if (r.kind === 'unreadable') return [st, plural(n, 'SCAN FILE') + " CAN'T BE READ", nm(i)];
    return [st, n === 1 ? 'A SKIPPED TOOL IS INSTALLED' : n + ' SKIPPED TOOLS INSTALLED', nm(i)];
  }
  if (st === 'waiting') { const i = firstOf(r => r.kind === 'waiting'), n = ROWS.filter(r => r.kind === 'waiting').length;
    return [st, n === 1 ? '1 TOOL WAITS FOR YOUR READ' : n + ' TOOLS WAIT FOR YOUR READ', nm(i) + ': ' + (ROWS[i].scan.word || '').replace('DO NOT INSTALL', 'DO NOT')]; }
  if (st === 'working') return [st, 'SCANNING ' + nm(firstOf(r => r.kind === 'scanning')), 'YOUR TURN COMES AFTER'];
  const signed = ROWS.filter(r => r.kind === 'signed').length, home = ROWS.filter(r => r.kind === 'homemade').length, nor = ROWS.filter(r => r.kind === 'norecord').length;
  if (st === 'ok') return [st, signed === 1 ? '1 SCAN SIGNED TWICE' : 'ALL ' + signed + ' SCANS SIGNED TWICE',
    nor ? plural(nor, 'CALL') + ' NOT RECORDED' : home ? home + ' HOME-MADE, NO SCAN' : 'NOTHING WAITS FOR YOU'];
  if (!ROWS.length) return [st, 'NO SCANS FOUND -', 'THE GATE IS NOT SET UP'];
  return [st, plural(ROWS.length, 'TOOL') + ', YOUR CALLS -', 'DECISIONS NOT RECORDED'];
}

/* ---------- static room (wall, window frame, door, notice board, cupboard body, desk, ledger cover) ---------- */
function buildStatic() {
  STATIC = layer(W, H, () => {
    R(0, 0, W, 106, '#232946');   // wall: dark blue stone, the gate's own cool mood
    for (let row = 0; row < 14; row++) { const y = 8 + row * 7, off = (row % 2) * 9;
      R(0, y, W, 1, '#1a1f38');
      for (let x = -off; x < W; x += 18) { R(x, y, 1, 7, '#1a1f38'); const h = hash(x, row, 3);
        if (h < 0.22) R(x + 1, y + 1, 17, 6, '#272e50'); else if (h > 0.86) R(x + 1, y + 1, 17, 6, '#20253f');
        if (h > 0.5 && h < 0.56) P(x + 4, y + 3, '#2e3660'); } }
    R(0, 0, W, 12, '#2a1c26'); R(0, 10, W, 2, '#1a1018'); R(0, 0, W, 1, '#3a2832');   // ceiling beam (the HUD sits on it)
    for (let x = 20; x < W; x += 60) { R(x, 0, 6, 13, '#2a1c26'); R(x, 12, 6, 1, '#1a1018'); }
    R(5, 16, 34, 92, OL); R(7, 18, 30, 90, '#4a2e22');   // door back to town
    for (let x = 9; x < 37; x += 6) R(x, 18, 1, 90, '#3a2218');
    R(7, 18, 30, 2, '#5e3b2a'); R(31, 60, 3, 3, '#c9963a'); P(31, 60, '#ffd36e');
    R(9, 30, 26, 11, OL); R(10, 31, 24, 9, '#161b38'); R(10, 31, 24, 1, '#2b336a'); text('<TOWN', 12, 33, '#ffd36e');
    R(15, 24, 1, 6, '#3a3450'); R(29, 24, 1, 6, '#3a3450');
    R(68, 16, 84, 58, OL); R(66, 73, 88, 4, '#3a2a30'); R(66, 73, 88, 1, '#5e4450');   // window frame + sill
    R(158, 18, 136, 48, OL); R(160, 20, 132, 44, '#1b2236'); R(160, 20, 132, 1, '#2a3450');   // slate notice board
    for (let i = 0; i < 40; i++) P(161 + hash(i, 1, 7) * 130, 21 + hash(i, 2, 7) * 42, '#212a42');
    R(157, 17, 138, 1, '#5e3b2a'); R(157, 66, 138, 2, '#5e3b2a'); R(157, 17, 1, 51, '#5e3b2a'); R(294, 17, 1, 51, '#5e3b2a');
    text('SCANNER SIGNS FIRST, YOU SECOND', 164, 55, '#59629a');
    R(172, 72, 108, 30, OL); R(173, 73, 106, 28, '#aeb8d8'); R(173, 73, 106, 1, '#c4cce8'); R(173, 100, 106, 1, '#8e9aca');   // key slip
    R(225, 70, 3, 3, '#c9963a');
    R(176, 78, 12, 3, INK); for (let x = 188; x < 194; x += 2) P(x, 79, RULE); text('= RISK OUT OF 100', 198, 77, INK);
    for (let i = 0; i < 3; i++) R(177 + i * 3, 86, 2, 2, '#5a3a8a'); text('5', 187, 85, '#5a3a8a'); text('= FLAGS TO READ', 198, 85, INK);
    R(176, 94, 18, 1, '#23a35f'); R(176, 96, 18, 1, '#23a35f'); text('= SIGNED TWICE', 198, 93, INK);
    R(299, 13, 92, 95, OL); R(300, 14, 90, 94, '#4a2f40'); R(300, 14, 90, 2, '#6a4458'); R(300, 14, 2, 94, '#5a3a4c');   // cupboard
    R(306, 18, 78, 39, '#141830'); R(306, 18, 78, 1, '#0c0f20');
    for (const sy of [30, 43, 56]) { R(306, sy, 78, 1, '#8a6040'); R(306, sy + 1, 78, 1, '#5a3a28'); }
    R(305, 61, 40, 44, '#3d2636'); R(346, 61, 39, 44, '#3d2636'); R(345, 61, 1, 44, OL);
    R(308, 64, 34, 38, '#452c3d'); R(349, 64, 33, 38, '#452c3d'); R(308, 64, 34, 1, '#5a3a4c'); R(349, 64, 33, 1, '#5a3a4c');
    R(0, 106, W, 94, '#5e3b2a'); R(0, 106, W, 1, '#8a5a3a'); R(0, 107, W, 1, '#7a5038');   // desk
    for (let y = 112; y < 200; y += 7) { R(0, y, W, 1, '#4f3022'); for (let x = (y * 7) % 37; x < W; x += 41 + (y % 5) * 3) R(x, y - 6, 1, 6, '#53331f'); }
    for (let i = 0; i < 160; i++) P(hash(i, 4, 1) * W, 108 + hash(i, 5, 1) * 92, hash(i, 6, 1) < 0.5 ? '#6a4430' : '#55351f');
    R(0, 200, W, 8, '#3a2418'); R(0, 200, W, 1, '#24160f'); R(0, 207, W, 1, '#1a100a');
    R(0, 208, W, 17, '#080a16'); R(0, 208, W, 1, '#161b38');   // floor strip (captions)
    R(50, 108, 300, 91, OL); R(51, 109, 298, 89, '#3a2440');   // ledger: leather cover, page stack, two pages
    R(53, 194, 294, 2, '#8e98bc'); R(53, 196, 294, 1, '#6e78a0');
    R(54, 110, 145, 85, PAPER); R(201, 110, 145, 85, PAPER);
    R(194, 110, 5, 85, '#a3adcf'); R(198, 110, 1, 85, '#8e98bc'); R(201, 110, 4, 85, '#a3adcf'); R(201, 110, 1, 85, '#8e98bc');
    R(199, 109, 2, 87, '#2a1830');
    R(352, 148, 18, 14, OL); R(353, 150, 16, 12, '#1a1d34'); R(355, 147, 12, 4, OL); R(356, 148, 10, 3, '#2a3050'); R(354, 152, 2, 6, '#4a5490'); P(358, 149, '#0a0c1c');   // inkwell
    R(354, 174, 20, 9, OL); R(355, 175, 18, 6, '#a0643a'); R(355, 175, 18, 1, '#c88a52'); R(359, 166, 10, 9, OL); R(360, 167, 8, 8, '#6a4430'); R(360, 167, 8, 1, '#8a5a3a');
    R(355, 181, 18, 2, '#4a3ab0');   // the scanner's stamp block
    R(18, 114, 26, 6, OL); R(19, 115, 24, 4, '#b8862e'); R(19, 115, 24, 1, '#ffd36e');   // desk lamp
    for (let k = 0; k < 40; k++) { const x = 30 + k * 0.15, y = 115 - k; P(x, y, '#9a7028'); P(x + 1, y, '#d9a548'); }
    R(34, 74, 4, 4, OL); P(35, 75, '#ffd36e'); R(36, 76, 14, 1, '#9a7028');
    for (let j = 0; j < 11; j++) { const w = 6 + j * 2, x0 = 56 - (w >> 1); R(x0 - 1, 71 + j, w + 2, 1, OL); R(x0, 71 + j, w, 1, j < 2 ? '#3a4a8a' : '#2a3670'); P(x0, 71 + j, '#5a6ab0'); P(x0 + 1, 71 + j, '#4a5aa0'); }
    R(54, 69, 4, 2, OL); R(55, 69, 2, 1, '#d9a548'); R(44, 82, 25, 2, OL); R(45, 82, 23, 1, '#b8862e');
  });
  WIN = layer(80, 54, () => {   // the window's still part: sky, far shore, sea
    R(0, 0, 80, 54, '#11173a'); R(0, 0, 80, 14, '#0e1330');
    for (let x = 0; x < 80; x++) { const h = 3 + Math.round(2 * Math.sin((x + 70) * 0.2) + 2 * hash((x + 70) >> 2, 0, 5)); R(x, 36 - h, 1, h, '#0a0d22'); }
    for (let i = 0; i < 7; i++) P(4 + i * 11 + hash(i, 3, 1) * 4, 33 - hash(i, 4, 1) * 2, '#ffd36e');
    R(0, 36, 80, 18, '#112d4e');
    disc(62, 12, 4, '#dfe8ff'); P(60, 10, '#ffffff'); P(63, 13, '#c3d0f0');
  });
}
function windowView(t) {
  g.drawImage(WIN, 70, 18);
  const still = K.still;
  for (let i = 0; i < 18; i++) { const x = 71 + hash(i, 1, 3) * 78, y = 19 + hash(i, 2, 3) * 26; if (still || Math.sin(t * 1.3 + i * 2.1) > -0.3) P(x, y, i % 4 ? '#9aa3d8' : '#eef1ff'); }
  glow(glowSp('#cfe0ff', 9, 9, 0.25), 132, 30);
  for (let i = 0; i < 12; i++) { const y = 56 + (i * 5) % 15, x = 70 + ((hash(i, 3, 2) * 80 + (still ? 0 : t) * (2 + i % 3)) % 76); R(x, y, 2 + i % 3, 1, i % 4 ? '#24547e' : '#3b78a8'); }
  for (let j = 0; j < 6; j++) if (still || Math.sin(t * 2 + j) > -0.2) R(130 + Math.round(Math.sin(still ? j : t + j) * 2), 57 + j * 2, 4 - (j >> 1), 1, '#9fc8f0');
  R(109, 18, 2, 54, '#3a2a30'); R(70, 44, 80, 2, '#3a2a30');
}

/* ---------- ledger marks ---------- */
const SIG = (() => { const pts = [], seen = new Set();   // your signature: always the same hand
  const add = (x, y) => { x = Math.round(x); y = Math.round(y); const k = x + ',' + y; if (!seen.has(k)) { seen.add(k); pts.push([x, y]); } };
  for (let i = 0; i <= 260; i++) { const s = i / 260; add(s * 27 + Math.sin(s * 31) * 1.6, Math.sin(s * 21 + 1) * 2.6 - (s < 0.12 ? (0.12 - s) * 20 : 0)); }
  for (let i = 0; i <= 40; i++) { const s = i / 40; add(27 - s * 24, 4 + Math.sin(s * 3) * 0.6); }
  return pts; })();
function drawSig(x, y, p, col) { const n = Math.floor(SIG.length * p); for (let i = 0; i < n; i++) P(x + SIG[i][0], y + SIG[i][1], col); return n ? [x + SIG[n - 1][0], y + SIG[n - 1][1]] : [x, y]; }
const MARK = { safe: ['....#', '...#.', '#.#..', '.#...'], caution: ['..#..', '.#.#.', '.#.#.', '#...#', '#####'], block: ['#...#', '.#.#.', '..#..', '.#.#.', '#...#'] };
function stamp(x, y, word, ink, shape, seed, dashed) { // a rubber stamp: worn border, the shape mark, the word
  const mark = MARK[shape] ? 7 : 0, w = textW(word) + mark + 6, h = 11, x0 = x - 2, y0 = y + 1;
  for (let i = 0; i < w; i++) {
    const on = k => dashed ? i % 3 !== 2 : hash(i, seed, k) > 0.1;
    if (on(11)) P(x0 + i, y0, ink); if (on(12)) P(x0 + i, y0 + h - 1, ink);
  }
  for (let j = 0; j < h; j++) { const on = k => dashed ? j % 3 !== 2 : hash(j, seed, k) > 0.1; if (on(13)) P(x0, y0 + j, ink); if (on(14)) P(x0 + w - 1, y0 + j, ink); }
  if (shape === 'safe') for (const [a, b] of [[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1]]) P(x0 + a, y0 + b, PAPER);   // round corners
  if (shape === 'block') for (const [a, b, da, db] of [[0, 0, 1, 1], [w - 1, 0, -1, 1], [0, h - 1, 1, -1], [w - 1, h - 1, -1, -1]]) {   // octagon
    P(x0 + a, y0 + b, PAPER); P(x0 + a + da, y0 + b, PAPER); P(x0 + a, y0 + b + db, PAPER); P(x0 + a + da, y0 + b + db, ink); }
  if (MARK[shape]) sprite(MARK[shape], x0 + 2, y0 + (shape === 'safe' ? 4 : 3), { '#': ink });
  text(word, x0 + 3 + mark, y + 4, ink);
  for (let k = 0; k < 4; k++) P(x0 + 2 + hash(k, seed, 15) * (w - 4), y0 + 2 + hash(k, seed, 16) * (h - 4), PAPER);   // worn ink
  return w;
}
function rule(y, p, col, dbl) { const L = Math.round(288 * p);
  for (let x = 0; x < L; x++) { const xx = 56 + x + (x > 141 ? 7 : 0); if (xx > 343) break; if (dbl || x % 2 === 0) P(xx, y + 12, col); if (dbl) P(xx, y + 13, col); } }
const TICK = ['....#', '...#.', '#.#..', '.#...'];
function stampWord(s) { return VERDICT[s.verdict] || { word: fitText(s.word || s.verdict, 34), shape: '', ink: '#4a4a6a' }; }

/* one ledger row; A (optional) = the demo's signing progress for this row */
function drawRow(r, y, i, A) {
  const ty = y + 4, grey = r.state === 'missing', err = r.state === 'error', scanning = r.kind === 'scanning';
  const ink = err ? RED_INK : scanning ? CY_INK : grey ? GREY_INK : INK;
  if (err) { g.fillStyle = 'rgba(255,74,74,0.22)'; g.fillRect(55, y, 144, 13); g.fillRect(201, y, 144, 13); }
  if (scanning) { g.fillStyle = 'rgba(54,211,230,0.14)'; g.fillRect(55, y, 144, 13); g.fillRect(201, y, 144, 13); }
  text(shortName(r.tool, NAME_W), COL.name, ty, ink);
  const s = r.scan;
  if (s && s.verdict) {
    for (let x = COL.risk; x < COL.risk + BAR; x += 2) P(x, ty + 2, RULE);   // risk: an ink bar out of 100, then the number (right-aligned)
    const sc = s.risk, len = sc == null ? 0 : Math.round(Math.max(0, Math.min(100, sc)) / 100 * BAR), ns = sc == null ? '-' : String(Math.round(sc));
    if (len) { R(COL.risk, ty + 1, len, 3, grey ? GREY_INK : INK); if (len < BAR) P(COL.risk + len, ty + 3, '#3a4480'); }
    text(ns, 175 - textW(ns), ty, grey ? GREY_INK : INK);
    const f = s.flags;   // flags: up to 6 dots, then the number
    if (f == null) text('-', COL.flags, ty, GREY_INK);
    else if (f === 0) text('0', COL.flags, ty, '#7a84b4');
    else { for (let k = 0; k < Math.min(6, f); k++) R(COL.flags + (k % 3) * 3, y + 3 + Math.floor(k / 3) * 4, 2, 2, '#5a3a8a');
      text(f > 99 ? '99+' : String(f), COL.flags + 10, ty, '#5a3a8a'); }
    const v = stampWord(s);
    stamp(COL.stamp, y, v.word, grey ? '#5a6090' : v.ink, v.shape, i + hashName(r.tool));
  } else if (scanning) {
    for (let k = 0; k < 3; k++) P(COL.risk + 2 + k * 3, ty + 4, CY_INK);
    text('-', COL.flags, ty, CY_INK); text('SCANNING', COL.stamp - 1, ty, CY_INK);
  } else if (r.kind === 'unreadable') {
    text('-', COL.risk, ty, RED_INK); text('-', COL.flags, ty, RED_INK); stamp(COL.stamp, y, 'UNREADABLE', RED_INK, '', i, true);
  } else {
    text('-', COL.risk, ty, err ? RED_INK : GREY_INK); text('-', COL.flags, ty, err ? RED_INK : GREY_INK);
    if (err) stamp(COL.stamp, y, 'NO SCAN', RED_INK, '', i, true);
    else stamp(COL.stamp, y, r.kind === 'homemade' ? 'HOME-MADE' : 'NO SCAN', '#7a80a0', '', i, true);
  }
  // the second signature: yours
  const sx = COL.you, d = r.decision;
  if (A || (d && r.kind !== 'waiting')) {
    const sp = A ? A.sig : 1, wp = A ? A.word : 1, call = A ? 'install' : d.call;
    drawSig(sx, y + 6, sp, grey ? '#4a5078' : '#1a1f48');
    if (wp > 0) { const word = call.toUpperCase(); g.save(); g.beginPath(); g.rect(COL.word, y, (textW(word) + 1) * wp, 13); g.clip(); text(word, COL.word, ty, grey ? GREY_INK : CALL_INK[call]); g.restore(); }
  } else if (r.kind === 'waiting') {
    R(sx - 2, y + 1, 80, 1, '#c07a20'); R(sx - 2, y + 11, 80, 1, '#c07a20'); R(sx - 2, y + 1, 1, 11, '#c07a20'); R(sx + 77, y + 1, 1, 11, '#c07a20');
    g.fillStyle = 'rgba(255,176,32,0.18)'; g.fillRect(sx - 1, y + 2, 78, 9); text('WAITS FOR YOU', sx + 12, ty, '#8a4a00');
  } else if (r.kind === 'noscan') text('INSTALLED', sx, ty, RED_INK);
  else if (r.kind === 'skipped-in') text('SKIPPED, IN', sx, ty, RED_INK);
  else if (scanning) text('YOUR TURN NEXT', sx, ty, '#3a7a8a');
  else { text('-', sx + 2, ty, '#7a80a0'); text('NO RECORD', sx + 10, ty, '#7a80a0'); }
  // closing: green double rule + tick when both are in; one grey dotted line when only one is
  const rp = A ? A.rule : r.kind === 'signed' ? 1 : 0;
  if (rp > 0) { rule(y, rp, '#23a35f', true); if (rp >= 1) sprite(TICK, COL.tick, ty, { '#': '#23a35f' }); }
  else if (grey && (d || (s && s.verdict))) rule(y, 1, '#8e94b4', false);
}
function hashName(s) { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) | 0; return Math.abs(h) % 97; }
function pageLayer(p, skip) { // a cached picture of one spread (the demo's signing row is drawn live, so it is left out)
  const key = p + '|' + skip; if (PAGES[key]) return PAGES[key];
  const dark = !ROWS.length;
  return (PAGES[key] = layer(W, H, () => {
    R(54, 110, 145, 85, PAPER); R(201, 110, 145, 85, PAPER);
    R(194, 110, 5, 85, '#a3adcf'); R(198, 110, 1, 85, '#8e98bc'); R(201, 110, 4, 85, '#a3adcf'); R(201, 110, 1, 85, '#8e98bc');
    text('TOOL', COL.name, 113, HEAD); text('RISK', COL.risk, 113, HEAD); text('FLAGS', COL.flags, 113, HEAD);
    text('SCANNER', COL.stamp, 113, HEAD); text('YOU', COL.you, 113, HEAD);
    R(56, 120, 141, 1, '#6e7ab0'); R(203, 120, 141, 1, '#6e7ab0');
    for (let i = 0; i < PER_PAGE; i++) { const y = ROW0 + i * PITCH + 13; R(56, y, 138, 1, RULE); R(205, y, 139, 1, RULE); }
    for (const x of [137, 176]) R(x, 112, 1, 82, '#9aa6d2'); R(251, 112, 1, 82, '#9aa6d2');
    if (dark) { for (const x of [COL.name, COL.risk, COL.flags, COL.stamp, COL.you]) text('-', x, ROW0 + 4, '#7a80a0'); text('NO SCANS ON FILE', 82, ROW0 + 18, '#7a80a0'); }
    for (let k = 0; k < PER_PAGE; k++) { const i = p * PER_PAGE + k; if (i >= ROWS.length) break; if (i !== skip) drawRow(ROWS[i], ROW0 + k * PITCH, i, null); }
    const n = pages();
    if (n > 1) { const s = 'PAGE ' + (p + 1) + ' OF ' + n; text(s, 200 - (textW(s) >> 1), 201, '#c9a878'); }
  }));
}
function curl(x, y, left) { // the turned-up page corner you click
  const c = '#dfe4f4', e = '#7a84b4';
  for (let j = 0; j < 7; j++) for (let i = 0; i <= j; i++) { const px = left ? x + i : x + 6 - i; P(px, y + j, i === j ? e : c); }
  for (let j = 0; j < 7; j++) P(left ? x - 1 : x + 7, y + j, '#3a2440');
  for (let i = 0; i < 7; i++) P(x + i, y + 7, '#3a2440');
}

/* ---------- cupboard: one bottle per installed item, a crate for what another app syncs in, two locks ---------- */
function bottle(x, y, i, tag) {
  const [c, hi] = GLASS[(i * 7 + 3) % GLASS.length];
  sprite(['..k..', '.ggg.', 'ggggg', 'gLLLg', 'gLLLg', 'ggggg', '.ggg.'], x, y, { k: '#a0643a', g: c, L: '#dfe4f6' });
  P(x + 1, y + 2, hi); P(x + 1, y + 5, hi);
  if (tag) { P(x + 4, y + 1, tag); R(x + 5, y + 1, 2, 3, tag); }
}
function cupboard(t, A) {
  const crate = synced() > 0, inst = installedRows(), slots = crate ? 22 : 24, shelves = [30, 43, 56];
  const list = A && A.pop >= 0 && !inst.includes(A.row) ? [A.row].concat(inst) : inst;   // demo: the signed bottle goes in first
  const over = list.length > slots ? list.length - (slots - 1) : 0, shown = over ? slots - 1 : list.length;
  for (let i = 0; i < shown; i++) {
    const r = list[i], sh = Math.floor(i / 8), k = i % 8, x = 311 + k * 9, y = shelves[sh] - 7;
    const tag = r.state === 'error' ? '#ff4a4a' : r.kind === 'homemade' ? '#9aa0b8' : null;
    bottle(x, y, hashName(r.tool), tag);
    if (r.state === 'error' && lampOn('error', t)) glow(glowSp('#ff3a3a', 10, 10, 0.45), x + 5, y + 2, 0.9);
    if (A && r === A.row && A.pop >= 0 && A.pop < 2.2) { const kk = 1 - A.pop / 2.2, gs = glowSp('#ffd36e', 5, 5, 0.6); glow(gs, x + 2, y + 3, kk); glow(gs, x + 2, y + 3, kk);
      const s = Math.floor(A.pop * 6) % 2, star = (sx, sy, c) => { P(sx, sy, '#ffffff'); P(sx - 1, sy, c); P(sx + 1, sy, c); P(sx, sy - 1, c); P(sx, sy + 1, c); };
      star(x - 2 + s, y - 1, '#ffd36e'); if (s) star(x + 7, y + 1, '#ffd36e'); else star(x + 6, y + 5, '#fff2a0');
      const ry = Math.round(y - 4 - A.pop * 3); R(x + 8, ry - 1, textW('+1') + 2, 7, OL); textL('+1', x + 9, ry, '#ffd36e'); }
  }
  if (over) { const i = slots - 1, x = 311 + (i % 8) * 9, y = shelves[Math.floor(i / 8)] - 6; textL('+' + over, x - 1, y + 1, '#ffd36e'); }
  if (crate) { R(365, 47, 17, 9, OL); R(366, 48, 15, 8, '#7a5038'); R(366, 48, 15, 1, '#a07048'); R(366, 52, 15, 1, '#5e3b2a'); textL('SYNC', 366, 50, '#ffe0a8'); }
  if (!ROWS.length && !inst.length) textL('-', 342, 36, '#4a4e62');
  g.fillStyle = 'rgba(160,190,255,0.06)'; g.fillRect(306, 18, 78, 39); P(308, 20, 'rgba(255,255,255,0.3)'); P(309, 21, 'rgba(255,255,255,0.18)');
  SEEN.bottles = shown; SEEN.bottlesMore = over; SEEN.crate = crate;
}
function lampOn(st, t) { if (K.still) return true; return st === 'waiting' ? (t % 1.1) < 0.7 : st === 'error' ? (t % 0.9) < 0.6 : true; }
const LAMP_OFF = { waiting: '#6a4518', error: '#6a1e1e', working: '#1d5a66', ok: '#1e4a2e' };   // a blinking lamp's dim phase keeps its colour
function statusLamp(x, y, st, on) { const c = ST[st] || ST.missing; R(x - 2, y - 2, 5, 5, OL); R(x - 1, y - 1, 3, 3, on || st === 'missing' ? c.lamp : LAMP_OFF[st] || c.lamp); if (on && st !== 'missing') P(x - 1, y - 1, c.hi); }
function lock(x, y, a, hasKey, st, on, label, broken) { // a = 0 locked (bow up) .. 1 turned (bow right)
  R(x - 4, y - 6, 9, 13, OL); R(x - 3, y - 5, 7, 11, broken ? '#6a5a3a' : '#b8862e'); R(x - 3, y - 5, 7, 1, broken ? '#8a7a5a' : '#ffd36e');
  R(x - 3, y - 5, 1, 11, broken ? '#7a6a4a' : '#d9a548'); R(x + 3, y - 5, 1, 11, '#7a5520');
  P(x, y - 1, '#160c14'); R(x, y, 1, 3, '#160c14');
  if (broken) { P(x - 2, y - 3, '#ff6a6a'); P(x - 1, y - 2, '#ff6a6a'); P(x + 1, y + 2, '#ff6a6a'); P(x + 2, y + 3, '#ff6a6a'); R(x - 5, y + 6, 3, 1, '#b8862e'); P(x - 6, y + 7, '#b8862e'); }
  if (hasKey) { const ang = a * Math.PI / 2, dx = Math.sin(ang), dy = -Math.cos(ang);
    for (let k = 1; k <= 6; k++) P(x + dx * k, y + dy * k, '#e8c060');
    const bx = Math.round(x + dx * 9), by = Math.round(y + dy * 9);
    disc(bx, by, 3, OL); disc(bx, by, 2, '#e8c060'); P(bx, by, '#3d2636'); P(bx - 1, by - 1, '#fff2a0'); }
  else if (!broken) textL('-', x - 1, y - 12, '#9aa0b8');
  statusLamp(x, y + 11, st, on);
  if (on && ST[st] && ST[st].glow) glow(glowSp(ST[st].glow, 10, 10, 0.4), x, y + 11, 0.8);
  textC(label, x, y + 16, '#9aa3d8');
}
function locks(t, A) {
  const [sa, sb] = lockStates();
  const a = { a: 1, key: true, st: sa, broken: false }, b = { a: 1, key: true, st: sb, broken: false };
  if (sa === 'error') Object.assign(a, { key: false, broken: true });
  else if (sa === 'working') a.a = K.still ? 0.1 : 0.12 * (Math.sin(t * 9) * 0.5 + 0.5);
  else if (sa === 'missing') a.key = false;
  if (sb === 'error') Object.assign(b, { key: false, broken: true });
  else if (sb === 'waiting') b.a = 0;
  else if (sb === 'missing') b.key = false;
  if (A) { b.a = A.lock; b.st = A.lock >= 1 ? 'ok' : 'waiting'; b.key = true; }
  lock(325, 75, a.a, a.key, a.st, lampOn(a.st, t), 'SCAN', a.broken);
  lock(365, 75, b.a, b.key, b.st, lampOn(b.st, t), 'YOU', b.broken);
  SEEN.locks = { scan: a.broken ? 'forced' : !a.key ? 'no key' : a.a >= 1 ? 'turned' : 'turning', you: b.broken ? 'forced' : !b.key ? 'no key' : b.a >= 1 ? 'turned' : 'shut' };
}
function deskBottles(t, A) { // tools waiting at the door: scanned, not signed by you, not installed
  const w = ROWS.filter(r => r.kind === 'waiting' && !(A && r === A.row && A.pop >= 0));
  w.slice(0, 2).forEach((r, k) => { const x = 380 + k * 9, y = 152;
    R(x - 1, y + 7, 7, 1, '#3a2418'); bottle(x, y, hashName(r.tool), null);
    const on = lampOn('waiting', t); R(x + 4, y - 3, 1, 3, '#c9b48a'); R(x + 3, y - 6, 4, 4, on ? '#ffb020' : '#c07a20');
    if (on) glow(glowSp('#ff9a1f', 8, 8, 0.4), x + 5, y - 4, 0.8); });
  if (w.length > 2) textL('+' + (w.length - 2), 381, 162, '#ffc24a');
  SEEN.deskBottles = Math.min(2, w.length); SEEN.deskMore = Math.max(0, w.length - 2);
}

/* ---------- the quill and the demo timeline (a pure function of time: headless virtual time works) ---------- */
const NIB0 = [360, 150];
function quill(nx, ny) {
  for (let k = 0; k < 24; k++) { const x = nx + k * 0.55, y = ny - k; P(x, y, k < 3 ? '#20263a' : '#d8dcf0');
    if (k > 6) { const w = Math.min(4, (k - 5) * 0.6) * (k > 20 ? (24 - k) / 4 : 1); R(x - w, y, w, 1, '#c4c8e8'); R(x + 1, y, w * 0.8, 1, '#eef1ff'); } }
}
function deskLamp(on) {
  R(51, 83, 10, 2, on ? '#fff3c0' : '#4a4a5a');
  if (on) glow(glowSp('#ffd36e', 16, 10, 0.35), 56, 87, 1);
}
const TL = { quillOut: 2.0, sign: 2.6, word: 4.2, back: 4.6, rule: 4.7, lock: 5.7, pop: 6.4, end: 8.2, loop: 11.5 };
function demoAnim() {
  if (!demo || demo.state || !ROWS[0] || ROWS[0].kind !== 'waiting') return null;
  const e = (NOW_T() - demo.t0) % TL.loop, cl = (a, b) => Math.max(0, Math.min(1, (e - a) / (b - a)));
  return { e, row: ROWS[0], sig: cl(TL.sign, TL.word), word: cl(TL.word, TL.back), rule: cl(TL.rule, TL.rule + 0.8), lock: cl(TL.lock, TL.lock + 0.6),
    pop: e >= TL.pop ? e - TL.pop : -1, quillOut: cl(TL.quillOut, TL.sign), quillBack: cl(TL.back, TL.back + 0.5), active: e >= TL.quillOut && e < TL.back + 0.5,
    signing: e >= TL.sign, closed: e >= TL.rule + 0.8 };
}
function demoCaption(e) {
  if (e < TL.quillOut) return ['WAITING: THE SECOND SIGNATURE IS EMPTY', 'waiting'];
  if (e < TL.back) return ['YOU READ THE FLAGS AND SIGN', 'waiting'];
  if (e < TL.lock) return ['THE ROW IS RULED CLOSED IN GREEN', 'ok'];
  if (e < TL.pop) return ['THE SECOND LOCK TURNS', 'ok'];
  if (e < TL.end) return [shortName(ROWS[0].tool, 200) + ' GOES IN THE CUPBOARD', 'ok'];
  return ['BOTH SIGNATURES IN', 'ok'];
}
function demoSounds(e) {
  if (e < lastE) lastE = -1;
  const cross = v => lastE < v && e >= v;
  if (cross(0.05)) snd('waiting'); if (cross(TL.sign)) snd('scratch'); if (cross(TL.rule)) snd('rule'); if (cross(TL.lock + 0.4)) snd('lock'); if (cross(TL.pop)) snd('pop');
  lastE = e;
}
const STATE_CAP = { ok: 'OK: EVERY ROW SIGNED TWICE', waiting: 'WAITING: THE SECOND SIGNATURE IS EMPTY', working: 'WORKING: A SCAN IS RUNNING',
  error: 'ERROR: A TOOL IS INSTALLED WITH NO SCAN', missing: 'MISSING: NO SCANS, LAMP OFF', norecord: 'NO RECORD: YOUR CALLS SHOW -, NOT AMBER' };

/* ---------- sound: synthesised, short, quiet ---------- */
function snd(kind) {
  const ac = sfx.ctx(); if (!ac) return; const t = ac.currentTime + 0.02, T = sfx.tone, N = sfx.noise;
  if (kind === 'page') { N(t, 0.16, 0.22, 3800, 'highpass'); N(t + 0.12, 0.1, 0.18, 500, 'lowpass'); }
  else if (kind === 'scratch') for (let k = 0; k < 9; k++) N(t + k * 0.17, 0.05, 0.16, 3200 + (k % 3) * 600, 'bandpass');
  else if (kind === 'rule' || kind === 'ok') { T(t, 660, 0.25, 0.16, 'triangle'); T(t + 0.12, 990, 0.4, 0.13, 'triangle'); }
  else if (kind === 'lock') { N(t, 0.03, 0.35, 5000, 'highpass'); T(t + 0.05, 140, 0.12, 0.18, 'square'); N(t + 0.16, 0.04, 0.28, 1800, 'bandpass'); }
  else if (kind === 'pop') { T(t, 1320, 0.18, 0.13); T(t + 0.08, 1760, 0.25, 0.11); }
  else if (kind === 'waiting') { T(t, 520, 0.14, 0.16); T(t + 0.16, 440, 0.22, 0.14); }
  else if (kind === 'working') { T(t, 700, 0.1, 0.08); T(t + 0.1, 900, 0.1, 0.08); T(t + 0.2, 1150, 0.16, 0.08); }
  else if (kind === 'error') { T(t, 110, 0.25, 0.16, 'sawtooth'); T(t + 0.27, 104, 0.25, 0.14, 'sawtooth'); }
  else if (kind === 'missing') T(t, 330, 0.3, 0.08);
  else if (kind === 'stamp') { N(t, 0.06, 0.35, 420, 'lowpass'); T(t, 90, 0.12, 0.2, 'triangle'); }
}

/* ---------- page turning ---------- */
function turn(to, quiet) {
  to = Math.max(0, Math.min(pages() - 1, to));
  if (to === page) return false;
  if (!K.still) flip = { from: page, to, t0: NOW_T() };
  page = to; if (!quiet) snd('page');
  return true;
}
function drawSpread(A) {
  const skip = A ? 0 : -1;
  if (!flip) { g.drawImage(pageLayer(page, page === 0 ? skip : -1), 0, 0); return; }
  const u = Math.min(1, (NOW_T() - flip.t0) / 0.36), fwd = flip.to > flip.from;
  const oldL = pageLayer(flip.from, flip.from === 0 ? skip : -1), newL = pageLayer(flip.to, flip.to === 0 ? skip : -1);
  // the page that stays put on each side, then the turning leaf: its front shrinks into the gutter, its back opens out
  const L = [54, 110, 145, 85], Rr = [201, 110, 145, 85], blit = (img, r) => g.drawImage(img, r[0], r[1], r[2], r[3], r[0], r[1], r[2], r[3]);
  blit(fwd ? oldL : newL, L); blit(fwd ? newL : oldL, Rr);
  if (u < 0.5) { const w = Math.max(1, Math.round(145 * (1 - u * 2))), src = fwd ? Rr : L;
    g.drawImage(oldL, src[0], src[1], src[2], src[3], fwd ? 201 : 199 - w, 110, w, 85); }
  else { const w = Math.max(1, Math.round(145 * (u * 2 - 1))), src = fwd ? L : Rr;
    g.drawImage(newL, src[0], src[1], src[2], src[3], fwd ? 199 - w : 201, 110, w, 85); }
  g.fillStyle = 'rgba(20,10,30,' + (0.25 * Math.sin(u * Math.PI)).toFixed(3) + ')'; g.fillRect(fwd ? 150 : 201, 110, 49, 85);
  if (u >= 1) flip = null;
}
function ribbons() { // a bookmark under the ledger for each page that holds something for you (red / amber / cyan)
  const out = [], n = pages();
  for (let p = 0; p < n && out.length < 8; p++) {
    const rs = ROWS.slice(p * PER_PAGE, p * PER_PAGE + PER_PAGE).filter(r => !(CUR_A && CUR_A.closed && r === CUR_A.row)), st = ['error', 'waiting', 'working'].find(s => rs.some(r => r.state === s));
    if (st) out.push({ p, st, box: [62 + out.length * 12, 196, 8, 12] });
  }
  return out;
}
function drawRibbons(t) {
  const rbs = ribbons();
  for (const rb of rbs) { const [x, y] = rb.box, c = ST[rb.st], cur = rb.p === page;
    R(x - 1, y, 10, 11, OL); R(x, y, 8, 10, cur ? c.lamp : c.glow || c.lamp); R(x, y, 8, 1, c.hi); P(x + 3, y + 10, OL); P(x + 4, y + 10, OL);
    P(x + 3, y + 9, OL); P(x + 4, y + 9, OL);
    textL(rb.p + 1 > 9 ? '+' : String(rb.p + 1), x + 3, y + 2, '#1a0e10');
    if (rb.st === 'error' && lampOn('error', t)) glow(glowSp('#ff3a3a', 8, 8, 0.35), x + 4, y + 5, 0.7); }
  SEEN.ribbons = rbs.map(rb => [rb.p + 1, rb.st]);
}

/* ---------- per frame ---------- */
function draw(t) {
  if (!STATIC) buildStatic();
  SEEN = {};
  const A = CUR_A = demoAnim(), dark = !ROWS.length;
  if (A) demoSounds(A.e);
  g.drawImage(STATIC, 0, 0);
  windowView(t);
  // the notice board (in the demo it switches to ok once the row is ruled)
  let nz = notice();
  if (A && A.closed) { const live2 = ROWS.slice(1).filter(r => r.state !== 'missing').map(r => r.state);
    const s = ['error', 'waiting', 'working'].find(x => live2.includes(x));
    if (!s) nz = ['ok', 'ALL ' + ROWS.filter(r => r.scan && r.scan.verdict && r.state !== 'missing').length + ' SCANS SIGNED TWICE', 'NOTHING WAITS FOR YOU']; }
  const on = lampOn(nz[0], t);
  statusLamp(166, 29, nz[0], on); if (on && ST[nz[0]].glow) glow(glowSp(ST[nz[0]].glow, 12, 12, 0.45), 166, 29, 0.9);
  textL(fitText(nz[1], 118), 172, 27, ST[nz[0]].txt); textL(fitText(nz[2], 118), 172, 38, '#9aa3d8');
  // the ledger
  drawSpread(A);
  const vis = [];
  if (!flip) {   // what moves on the open spread: amber box blink, red "!", the scan sweep
    for (let k = 0; k < PER_PAGE; k++) { const i = page * PER_PAGE + k, r = ROWS[i]; if (!r) break; const y = ROW0 + k * PITCH;
      vis.push([r.tool, r.kind]);
      if (A && r === A.row) { drawRow(r, y, i, A.signing ? A : null); if (!A.signing) liveBits(r, y, t); continue; }
      liveBits(r, y, t); }
    if (pages() > 1) { if (page < pages() - 1) curl(338, 187, false); if (page > 0) curl(55, 187, true); }
  }
  drawRibbons(t);
  cupboard(t, A);
  locks(t, A);
  deskBottles(t, A);
  let nib = NIB0;   // the quill: in the demo it flies to the row, signs, writes the call, comes back
  if (A && A.active && page === 0) {
    const tx = COL.you, ty = ROW0 + 6;
    if (A.sig <= 0) { const u = A.quillOut; nib = [NIB0[0] + (tx - NIB0[0]) * u, NIB0[1] + (ty - NIB0[1]) * u - Math.sin(u * Math.PI) * 14]; }
    else if (A.word <= 0) { const n = Math.max(1, Math.floor(SIG.length * A.sig)); nib = [tx + SIG[n - 1][0], ty + SIG[n - 1][1]]; }
    else if (A.quillBack <= 0) nib = [COL.word + textW('INSTALL') * A.word, ty + 2];
    else { const u = A.quillBack, from = [COL.word + textW('INSTALL'), ty + 2]; nib = [from[0] + (NIB0[0] - from[0]) * u, from[1] + (NIB0[1] - from[1]) * u - Math.sin(u * Math.PI) * 10]; }
  }
  quill(nib[0], nib[1]);
  deskLamp(!dark);
  if (!dark) { glow(glowSp('#ffb45a', 120, 52, 0.16), 150, 140, 1);   // warm pool on the ledger, dust in the beam
    if (!K.still) for (let i = 0; i < 9; i++) { const ph = (t * 0.05 + hash(i, 1, 9)) % 1, x = 70 + hash(i, 2, 9) * 120 + Math.sin(t * 0.4 + i) * 4, y = 88 + ph * 22;
      if (Math.sin(t * 1.1 + i * 1.7) > 0) P(x, y, 'rgba(255,226,160,0.55)'); } }
  else { g.fillStyle = 'rgba(4,6,16,0.5)'; g.fillRect(0, 12, W, 196);
    for (let i = 0; i < 60; i++) P(56 + hash(i, 1, 21) * 288, 112 + hash(i, 2, 21) * 80, 'rgba(160,160,180,0.25)'); }
  // the floor strip: the demo's caption, else a hint
  let cap = null, cst = 'ok';
  if (A) [cap, cst] = demoCaption(A.e);
  else if (demo && demo.state) { cap = STATE_CAP[demo.state]; cst = demo.state === 'norecord' ? 'missing' : demo.state; }
  if (cap) { const cw = textW(cap); statusLamp(200 - (cw >> 1) - 7, 216, cst, true); textL(cap, 200 - (cw >> 1), 214, '#eef1ff'); }
  else textC(pages() > 1 ? 'HOVER A ROW. CLICK THE PAGE CORNER TO TURN.' : 'HOVER A ROW. CLICK IT TO OPEN ITS SCAN.', 200, 214, '#59629a');
  if (demo) { const s = 'SAMPLE', sw = textW(s) + 6; R(W - sw - 5, 211, sw + 2, 9, OL); R(W - sw - 4, 212, sw, 7, '#3a2208'); textL(s, W - sw - 1, 213, '#ffd36e'); }
  Object.assign(SEEN, { notice: nz, lamp: !dark, page: page + 1, pages: pages(), flipping: !!flip, rows: vis, caption: cap, sample: !!demo,
    signing: A ? (A.closed ? 'closed' : A.signing ? 'signing' : 'waiting') : null });
}
function liveBits(r, y, t) {
  const ty = y + 4;
  if (r.kind === 'waiting') { const on = lampOn('waiting', t), c = on ? '#ffb020' : '#c07a20', sx = COL.you;
    R(sx - 2, y + 1, 80, 1, c); R(sx - 2, y + 11, 80, 1, c); R(sx - 2, y + 1, 1, 11, c); R(sx + 77, y + 1, 1, 11, c);
    if (on) glow(glowSp('#ff9a1f', 46, 12, 0.28), sx + 38, y + 6, 1); }
  else if (r.state === 'error') { if (lampOn('error', t)) { textL('!', COL.tick + 2, ty, '#d02a2a'); glow(glowSp('#ff3a3a', 8, 8, 0.4), COL.tick + 2, ty + 2, 0.8); } }
  else if (r.kind === 'scanning') { const sx = 56 + ((K.still ? 1 : t) * 70) % 290; g.fillStyle = 'rgba(80,240,255,0.45)'; g.fillRect(sx < 197 ? sx : sx + 6, y + 1, 5, 11);
    for (let k = 0; k < 3; k++) if (Math.floor((K.still ? 0 : t) * 4) % 3 === k) P(COL.risk + 2 + k * 3, ty + 4, '#36d3e6'); }
}

/* ---------- hover / click targets ---------- */
function scanLine(r) {
  const s = r.scan, who = s && s.scanner ? s.scanner : 'Scanner';
  if (r.kind === 'scanning') return who + ': scanning now (' + (s && s.file ? s.file : 'scan file') + ' is still being written).';
  if (r.kind === 'unreadable') return who + ": the newest scan file can't be read (" + (s && s.file ? s.file + ', ' : '') + (s && s.why ? s.why : 'unreadable') + ').';
  if (!s || !s.verdict) return r.kind === 'homemade' ? 'Scanner: no scan on file. Home-made here, so grey, not red.' : 'Scanner: no scan on file.';
  return who + ': ' + (s.word || s.verdict) + ', risk ' + (s.risk == null ? '-' : s.risk) + ' of 100' + (s.severity ? ' (' + s.severity.toLowerCase() + ')' : '') + ', ' +
    plural(s.flags == null ? 0 : s.flags, 'flag').toLowerCase() + (s.when ? '. ' + K.dayText(s.when) + ' ' + K.timeText(s.when).slice(4) : '') + '.';
}
function historyLine(r) {
  if (r.history.length < 2) return '';
  const w = h => (h.verdict === 'DO_NOT_INSTALL' ? 'do not' : String(h.verdict || '-').toLowerCase()) + ' ' + (num(h.risk) == null ? '-' : h.risk);
  return r.scans + ' scans: ' + r.history.map(w).join(' > ') + '.';
}
function youLine(r) {
  const d = r.decision;
  if (r.kind === 'waiting') return 'You: waits for your read. Read the ' + plural(r.scan && r.scan.flags || 0, 'flagged line').toLowerCase() + ', then record your call.';
  if (r.kind === 'scanning') return 'You: your turn comes after the scan.';
  if (!d) return r.kind === 'noscan' ? 'You: no call on record, yet it is installed.' : 'You: - no record. Your call is not written anywhere the room can read.';
  // the owner writes the note: the pixel font has no ; or " so they become , and '
  const note = d.note ? String(d.note).replace(/;/g, ',').replace(/"/g, "'") : '';
  return 'You: ' + d.call + (d.date ? ', ' + K.dayText(d.date) : '') + (note ? '. ' + note : '') + '.';
}
function rowTips(r) {
  const who = r.origin === 'own' ? 'home-made' : r.origin === 'third-party' ? 'third-party' : 'origin unknown';
  const head = r.tool + ' (' + who + (r.installed ? ', installed' : r.kind === 'waiting' ? ', waiting outside' : ', not installed') + ')';
  const warn = { noscan: 'Installed with no scan on file. Scan it, or find its scan.', unreadable: 'Fix or re-run the scan.',
    'skipped-in': 'You skipped it, yet it is installed.' }[r.kind];
  return [head, scanLine(r), historyLine(r), youLine(r), warn || ''].filter(Boolean);
}
function openRow(r) {
  const s = r.scan;
  if (demo) K.toast('SAMPLE ROW: NOTHING TO OPEN');
  else if (s && s.href) { K.open(s.href); K.toast('OPENING ' + fitText(s.file || 'THE SCAN', 220)); }
  else if (r.href) { K.open(r.href); K.toast('OPENING ' + fitText(shortName(r.tool, 200), 200) + "'S FOLDER"); }
  else K.toast('NO SCAN FILE FOR ' + shortName(r.tool, 200));
  snd(r.state === 'missing' ? 'missing' : r.kind === 'signed' ? 'stamp' : r.state);
}
function openDecisions(r) {
  const href = !demo && DATA && DATA.decisions && DATA.decisions.href;
  if (href) { K.open(href); K.toast(r.kind === 'waiting' ? 'WRITE YOUR CALL FOR ' + shortName(r.tool, 120) + ' IN ' + decFile() : 'OPENING ' + decFile()); }
  else K.toast(demo ? 'SAMPLE: YOUR CALLS LIVE IN YOUR DECISIONS FILE' : 'NO DECISIONS FILE YET: SET DECISIONS_FILE');
  snd(r.kind === 'waiting' ? 'waiting' : 'stamp');
}
function targets() {
  const out = [], nz = notice(), N = noun();
  const att = firstOf(r => r.state === nz[0] && nz[0] !== 'ok' && nz[0] !== 'missing');
  out.push({ id: 'notice', box: [158, 17, 137, 51], tip: ['The gate today', nz[1].toLowerCase() + '.', nz[2].toLowerCase() + '.'].concat(att >= 0 ? ['Click: turn to that row.'] : []),
    label: 'Notice board: ' + nz[1].toLowerCase(),
    onClick: () => { if (att >= 0) { turn(pageOf(att)); K.toast('PAGE ' + (pageOf(att) + 1) + ': ' + shortName(ROWS[att].tool, 200)); } else { const v = !demo && DATA && DATA.sources && DATA.sources.scans; if (v) { K.open(v); K.toast('OPENING THE SCANS FOLDER'); } } } });
  if (pages() > 1) {
    if (page < pages() - 1) out.push({ id: 'next', box: [330, 182, 18, 15], tip: ['Next page', 'Page ' + (page + 1) + ' of ' + pages() + ', newest first.'], label: 'Turn to the next page', onClick: () => turn(page + 1) });
    if (page > 0) out.push({ id: 'prev', box: [52, 182, 18, 15], tip: ['Back a page', 'Page ' + (page + 1) + ' of ' + pages() + '.'], label: 'Turn back a page', onClick: () => turn(page - 1) });
  }
  for (const rb of ribbons()) out.push({ id: 'ribbon-' + (rb.p + 1), box: rb.box, tip: ['Bookmark: page ' + (rb.p + 1), { error: 'Something is wrong on this page.', waiting: 'A tool waits for your read on this page.', working: 'A scan is running on this page.' }[rb.st]],
    label: 'Bookmark to page ' + (rb.p + 1), onClick: () => turn(rb.p) });
  for (let k = 0; k < PER_PAGE; k++) { const i = page * PER_PAGE + k, r = ROWS[i]; if (!r) break; const y = ROW0 + k * PITCH, tips = rowTips(r);
    out.push({ id: 'scan-' + i, box: [55, y, 196, 13], tip: tips.concat([demo ? 'Sample row (demo)' : r.scan && r.scan.href ? 'Click: open the scan.' : 'Click: open its folder.']), label: r.tool + ': ' + scanLine(r), onClick: () => openRow(r) });
    out.push({ id: 'you-' + i, box: [252, y, 94, 13], tip: tips.concat([demo ? 'Sample row (demo)' : 'Click: open your decisions file.']), label: r.tool + ': ' + youLine(r), onClick: () => openDecisions(r) }); }
  const inst = installedRows(), own = inst.filter(r => r.origin === 'own').length, bad = inst.filter(r => r.state === 'error').length;
  out.push({ id: 'cupboard', box: [300, 14, 90, 44], tip: ['The cupboard: what is installed', plural(inst.length, N).toLowerCase() + ' inside: ' + own + ' home-made, ' + (inst.length - own) + ' from outside.'].concat(
    synced() ? ['The SYNC crate: ' + synced() + ' ' + N + (synced() === 1 ? '' : 's') + ' another app syncs in. Managed there, not vetted here.'] : [], bad ? ['A red-tagged bottle got in without a good scan.'] : [], demo ? [] : ['Click: open the folder.']),
    label: 'Cupboard: ' + inst.length + ' installed', onClick: () => { const s = DATA && DATA.sources && DATA.sources.installed; if (s && !demo) { K.open(s); K.toast('OPENING THE INSTALLED FOLDER'); } else K.toast(demo ? 'SAMPLE CUPBOARD' : 'NO INSTALLED FOLDER SET UP'); } });
  const [a, b] = lockStates();
  out.push({ id: 'lock-scan', box: [306, 60, 39, 45], tip: ['First lock: the scanner', { ok: 'Turned: every tool in the cupboard has a scan, or is home-made.', working: 'Turning: a scan is running now.', error: 'Forced: something got past the scanner.', missing: 'No scans found: -.' }[a]], label: 'Scanner lock: ' + a, onClick: null });
  out.push({ id: 'lock-you', box: [346, 60, 39, 45], tip: ['Second lock: you', { ok: 'Turned: nothing waits for your read.', waiting: 'Shut: a scanned tool waits for your call.', error: "Forced: your decisions file can't be read.", missing: 'No calls recorded anywhere: -.' }[b]], label: 'Your lock: ' + b, onClick: null });
  out.push({ id: 'key', box: [172, 72, 108, 30], tip: ['How to read the ledger', 'Ink bar = risk out of 100, with the number. Dots = flags the scanner raised (6 dots at most, the number counts them all).',
    'Green double rule = both signatures in. Grey line = one signature only.'], label: 'Key to the ledger', onClick: null });
  out.push({ id: 'stamp', box: [352, 164, 24, 20], tip: ["The scanner's stamp", 'The scanner signs first: SAFE (box + tick), CAUTION (box + triangle) or DO NOT INSTALL (octagon + cross).'], label: "Scanner's stamp", onClick: () => snd('stamp') });
  out.push({ id: 'pen', box: [352, 124, 26, 38], tip: ['Your pen', 'The second signature is always yours: your calls in your decisions file.'], label: 'Your pen', onClick: () => openDecisions({ kind: '' }) });
  if (ROWS.some(r => r.kind === 'waiting')) out.push({ id: 'desk', box: [377, 145, 20, 16], tip: ['Waiting at the door', 'Scanned, not installed, your call not written yet.'], label: 'Tools waiting at the door', onClick: () => { const i = firstOf(r => r.kind === 'waiting'); turn(pageOf(i)); } });
  out.push({ id: 'lamp', box: [14, 66, 60, 54], tip: ['The desk lamp', ROWS.length ? 'Lit while the gate has scans to read.' : 'Off: there is nothing to read yet.'], label: 'Desk lamp', onClick: null });
  out.push({ id: 'door', box: [5, 16, 34, 92], tip: ['Door back to town', 'or press Esc'], label: 'Door: back to town', onClick: K.leave });
  return out;
}
function lockStates() {
  const a = has('noscan') || has('unreadable') || has('skipped-in') ? 'error' : has('scanning') ? 'working' : ROWS.some(r => r.scan && r.scan.verdict) ? 'ok' : 'missing';
  const b = decErr() ? 'error' : has('waiting') ? 'waiting' : ROWS.some(r => r.decision) ? 'ok' : 'missing';
  return [a, b];
}

/* ---------- the room ---------- */
registerRoom('ledger', {
  enter(data, opts) {
    DATA = data && typeof data === 'object' ? data : {}; QS = opts.qs || null;
    demo = opts.demo ? { state: DEMO_STATES.includes(opts.state) ? opts.state : '', qs: opts.qs, t0: NOW_T() } : null;
    lastE = -1; setup(); buildStatic();
    const st = demo && demo.state ? demo.state : roomState();
    timers.push(setTimeout(() => snd(st === 'norecord' ? 'missing' : st), 700));   // the room's state, heard once after the door
  },
  leave() { timers.forEach(clearTimeout); timers = []; flip = null; PAGES = {}; STATIC = null; WIN = null; demo = null; CUR_A = null; },
  draw(ctx, t) { draw(t); },
  targets,
  hover() {},
  plate() { return { num: String(ROWS.length), state: demo && demo.state === 'norecord' ? 'missing' : roomState() }; },
  busy() { return !!flip || !!demo; },
  key(e) {
    // the toast says where you are (and redraws a still scene: a key alone doesn't)
    const to = e.key === 'ArrowRight' || e.key === 'PageDown' ? page + 1 : e.key === 'ArrowLeft' || e.key === 'PageUp' ? page - 1 : null;
    if (to !== null) { if (turn(to)) { e.preventDefault(); K.toast('PAGE ' + (page + 1) + ' OF ' + pages()); } return; }
    if (!demo) return;
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= DEMO_STATES.length) { demo.state = DEMO_STATES[n - 1]; demo.t0 = NOW_T(); setup(); snd(demo.state === 'norecord' ? 'missing' : demo.state); }
  },
  seen: () => Object.assign({ state: roomState() }, SEEN),
});
})();
