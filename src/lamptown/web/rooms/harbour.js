/* HARBOUR: the project berths, inside the boathouse.
   Data: the building's room data, `rows`, from the `projects` collector (collectors/harbour.py, one row per
   project in the settings) and/or agent-status files (`status_dir`: one berth per agent or task).
   One berth per project, under a name board:
     active   a hull on its slip, planked done/total of its TASKS.md (no TASKS.md: fully planked, no count);
              the work lamp over it is the project's state colour.
                ok        green lamp, "ALL GOOD"
                waiting   amber lamp + "!" bubble; its items are sheets on the harbourmaster's desk by the door,
                          where the bell swings. Hover the desk: the lamp flares, the bell rings and the first
                          items rise off the stack (the core moment). The agent's own items (under "Needs you",
                          starting "(agent") are a pale note pinned to the hull, never on your desk.
                working   a shipwright lays the next plank under a cyan work light (also on a boat that needs
                          you while an agent is at work on it: `busy`)
                error     its TASKS.md can't be read: greyed planks, a red distress flag, a red pulse
                missing   its folder is gone: an empty cradle under a grey dust sheet, "-"
     paused   moored in the water under a grey-blue tarp with a "PAUSED BY YOU" tag (never read inside); a
              private project is always moored like this, its tag says PRIVATE
     closed   a framed name board on the "past voyages" wall (names from the settings only)
   An agent berth (a status file, no project stage) is an active hull planked by its own progress; its
   words ("message") are in the tooltip. A paused or closed project whose folder is gone: an empty mooring
   rope / a greyed "-" board.
   Scale: berths share 308 px (active = 2 units, paused = 1); at most 10 units, past that the last bay is a
   "+N" bay (hover lists the rest). The wall holds 20 boards in two rows; past that the last reads "+N".
   Demo (?room=<id>&demo=1): fictional SAMPLE projects; &state=ok|waiting|working|error|missing (forced on
   the first active project), &boats=N extra sample paused boats, &closed=N extra sample closed boards;
   keys 1-5 switch the state. */
(() => {
'use strict';
const { W, H, OL, ST, R, P, sprite, hash, glow, glowSp, textW, textL, textC, fitText, wrapText, layer, sfx } = K;
const BAY0 = 90, BAYMAX = 398, WATER = 180, KEEL = 160, MAXU = 10, DESK = { x: 34, y: 150, w: 46 };
const FR = { x: 36, y: 34, pitch: 35, per: 10, bw: 34, bh: 13 };
const WORD = { ok: 'ALL GOOD', waiting: 'NEEDS YOU', working: 'BEING BUILT', error: "CAN'T READ", missing: '-' };
// agent berths: why a lamp is grey or red (from agent-status/1 rows)
const WHY_WORD = { quiet: 'QUIET', stopped: 'STOPPED' };
const LILAC = '#9aa3d8', WHITE = '#eef1ff', AMBER = '#ffc24a', GOLD = '#ffd36e', CYAN = '#8fe3ff', TARP = '#575c7e', PAUSE_TXT = '#a9aec8';
const ORDER = ['error', 'waiting', 'working', 'missing', 'ok'];   // worst first, as the collectors' WORST_FIRST
const STAGES = ['active', 'paused', 'closed'];
const NOW_T = () => performance.now() / 1000;
const DEMO_STATES = ['ok', 'waiting', 'working', 'error', 'missing'];

let raw = null, demo = null, M = null, LAY = null, STATIC = null, BOATS = {}, HOV = null, lit = 0, bellT = -9, timers = [], caption = '', cursor = null, lastTick = 0;
let enters = 0, updates = 0;   // for tests: a data swap updates the open room, it doesn't walk in again
let QUAY_ON = false, QUAY = null;   // live: L10 berths: the live quay is shown (the static layer was built for it); what it drew

/* ---------- data ---------- */
const S = v => String(v == null ? '' : v);
function mkProj(p, i) {
  p = p && typeof p === 'object' ? p : {};
  const st = ST[p.state] ? p.state : 'missing', agent = !('status' in p);   // a status-file berth has no project stage
  const items = a => (Array.isArray(a) ? a : []).filter(x => x && typeof x === 'object').map(x => ({ text: S(x.text), short: S(x.short || x.text), href: S(x.href) }));
  const pr = p.progress, t = pr && Number.isFinite(+pr.total) && +pr.total > 0 ? { done: Math.max(0, Math.min(+pr.done | 0, +pr.total | 0)), total: +pr.total | 0 } : null;
  const yours = items(p.needs), lead = items(p.agent_items), name = S(p.name || p.id) || 'Project ' + (i + 1);
  return { id: S(p.id || 'p' + i), name, short: S(p.short || name).toUpperCase(), board: S(p.board || p.short || name).toUpperCase(),
    status: agent ? 'active' : STAGES.includes(p.status) ? p.status : 'closed', agent, kind: S(p.kind), message: S(p.message),
    since: p.since || null, note: S(p.note), href: S(p.href), private: !!p.private,
    exists: p.exists !== false && st !== 'missing', state: st, why: S(p.why), tasks: t, tasksHref: S(p.tasks_href),
    yours, yoursN: Math.max(yours.length, +p.needs_n || 0), lead, leadN: Math.max(lead.length, +p.agent_n || 0),
    lastCommit: p.last_commit && typeof p.last_commit === 'object' ? p.last_commit : null, updated: p.updated || null, busy: !!p.busy, sample: !!p.sample };
}
function roomState(all) {
  const act = all.filter(p => p.status === 'active').map(p => p.state);   // the data's rule: an unseen active is never 'ok'
  if (act.length) return ORDER.find(s => act.includes(s));
  return all.length ? 'ok' : 'missing';
}
function model(data) {
  const ps = (Array.isArray(data && data.rows) ? data.rows : []).map(mkProj);
  return finish({ all: ps, empty: !ps.length, sample: false });
}
function finish(m) {
  m.active = m.all.filter(p => p.status === 'active'); m.paused = m.all.filter(p => p.status === 'paused'); m.closed = m.all.filter(p => p.status === 'closed');
  m.state = roomState(m.all);
  m.items = [].concat(...m.active.map(p => p.yours.map(y => ({ p, ...y }))));
  m.yoursN = m.active.reduce((s, p) => s + p.yoursN, 0); m.leadN = m.active.reduce((s, p) => s + p.leadN, 0);
  m.deskP = m.active.filter(p => p.yoursN).sort((a, b) => b.yoursN - a.yoursN)[0] || null;
  return m;
}
/* fictional sample projects for ?demo=1 */
const SAMPLE_NEEDS = ['Pick the tail colour', 'Approve the test-flight day', 'Sign off the frame drawings', 'Choose the string weight', 'Name the second kite'];
const SAMPLE = () => [
  { id: 'paper-kite', name: 'Paper Kite', short: 'KITE', status: 'active', since: '2026-02-01', note: 'Sample project.', state: 'waiting', exists: true, progress: { done: 34, total: 58 },
    needs: SAMPLE_NEEDS.map(s => ({ text: s, short: s })), needs_n: 7, agent_items: [{ text: 'Re-run the wind check', short: 'Re-run the wind check' }], agent_n: 1 },
  { id: 'tidebook', name: 'Tidebook', short: 'TIDEBOOK', status: 'active', since: '2026-01-10', note: 'Sample project.', state: 'ok', exists: true, progress: { done: 9, total: 23 } },
  { id: 'moss-engine', name: 'Moss Engine', short: 'MOSS', status: 'paused', since: '2026-02-20', note: 'Sample paused project.', state: 'ok', exists: true },
  { id: 'lantern', name: 'Lantern', short: 'LANTERN', status: 'paused', private: true, why: 'private', note: 'Sample private project.', state: 'ok', exists: true },
].concat(['OLD PIER', 'GLASS KITE', 'SHELLS', 'FOG HORN', 'MAP ROOM', 'HERONS', 'ROCK POOL', 'TIDE CLOCK', 'SALT BOOK'].map((b, i) => ({ id: 'c' + i, name: 'Sample closed ' + (i + 1) + ': ' + b.toLowerCase(), board: b, status: 'closed', since: '2025-12-0' + (i % 9 + 1), note: 'Sample.', state: 'ok', exists: true })))
  .map(p => Object.assign(p, { sample: true }));
function demoModel(st, nBoats, nClosed) {
  const m = { all: SAMPLE().map(mkProj), empty: false, sample: true };
  for (let i = 1; i <= nBoats; i++) m.all.push(mkProj({ id: 'sample-boat-' + i, name: 'Sample boat ' + i, short: 'BOAT ' + i, status: 'paused', note: 'A sample for the layout test.', state: 'ok', sample: true }, 100 + i));
  for (let i = 1; i <= nClosed; i++) m.all.push(mkProj({ id: 'sample-board-' + i, name: 'Sample board ' + i, board: 'BOARD ' + i, status: 'closed', note: 'A sample for the layout test.', state: 'ok', sample: true }, 200 + i));
  const act = m.all.filter(p => p.status === 'active');
  if (DEMO_STATES.includes(st) && act.length) {
    const first = act[0], keep = { yours: first.yours, yoursN: first.yoursN };
    for (const p of act) Object.assign(p, { state: 'ok', busy: false, yours: [], yoursN: 0 });
    if (st === 'waiting') Object.assign(first, { state: 'waiting' }, keep);
    else if (st === 'working') Object.assign(first, { state: 'working', busy: true, why: 'busy' });
    else if (st === 'error') Object.assign(first, { state: 'error', why: 'unreadable', tasks: null });
    else if (st === 'missing') Object.assign(first, { state: 'missing', exists: false, why: 'nofolder' });
  }
  return finish(m);
}

/* ---------- layout: berths share 308 px; active = 2 units, paused = 1; at most MAXU units, then a "+N" bay ---------- */
function layout() {
  const boats = M.active.concat(M.paused), units = p => (p.status === 'active' ? 2 : 1);
  let shown = boats, rest = [];
  if (boats.reduce((s, p) => s + units(p), 0) > MAXU) {
    shown = []; let u = 0;
    for (const p of boats) { if (!rest.length && u + units(p) <= MAXU - 1) { shown.push(p); u += units(p); } else rest.push(p); }
  }
  const total = shown.reduce((s, p) => s + units(p), 0) + (rest.length ? 1 : 0);
  const UNIT = total ? Math.min(77, Math.floor((BAYMAX - BAY0) / total)) : 77;
  const bays = []; let x = BAY0;
  for (const p of shown) { const w = UNIT * units(p); bays.push(mkBay(p, x, w)); x += w; }
  const more = rest.length ? { x, w: UNIT, rest } : null;
  const nC = M.closed.length, cap = FR.per * 2, boards = nC > cap ? M.closed.slice(0, cap - 1) : M.closed;
  return { bays, more, end: more ? x + UNIT : x, boards, moreC: nC - boards.length };
}
function mkBay(p, x, w) {
  const b = { p, x, w, cx: x + ((w - 2) >> 1) };
  if (p.status === 'active') {
    b.hx0 = x + 9; b.hx1 = x + w - 11; b.hl = b.hx1 - b.hx0; b.hh = Math.max(22, Math.min(46, Math.round(b.hl * 0.36))); b.top = KEEL - b.hh;
    b.cells = cells(b); b.laid = p.tasks ? Math.round(b.cells.length * p.tasks.done / p.tasks.total) : b.cells.length;
    b.lamp = [b.x + Math.round(b.w * 0.42), Math.max(97, b.top - 18)];
  }
  return b;
}
function hBot(b, x) { const t = (x - b.hx0) / b.hl; return KEEL - (t > 0.66 ? Math.round(b.hh * 0.78 * Math.pow((t - 0.66) / 0.34, 2)) : 0) - (t < 0.08 ? Math.round(5 * (0.08 - t) / 0.08) : 0); }
function hTop(b, x) { const t = (x - b.hx0) / b.hl; return b.top - (t > 0.7 ? Math.round(b.hh * 0.26 * Math.pow((t - 0.7) / 0.3, 2)) : 0); }
function cells(b) { // planks: (strake k, 12-px plank) from the keel up; the share laid = tasks done / total
  const out = [];
  for (let k = 0; k < Math.ceil(b.hh / 3) + 2; k++) { const off = (k * 5) % 12;
    for (let s = b.hx0 + 1 - off; s < b.hx1; s += 12) { const a = Math.max(b.hx0 + 1, s), c = Math.min(b.hx1 - 1, s + 11); let ok = false;
      for (let x = a; x <= c; x++) if (hBot(b, x) - 3 * k - 3 >= hTop(b, x) + 1) ok = true;
      if (ok && c >= a) out.push({ k, a, b: c }); } }
  return out;
}

/* ---------- static layer (rebuilt when the data or a demo state changes) ---------- */
function buildStatic() {
  STATIC = layer(W, H, () => {
    const dark = M.state === 'missing';
    R(0, 0, W, H, '#14122a');
    for (let x = 0; x < W; x += 6) { const sh = hash(x, 1, 3); R(x, 12, 5, 170, sh < 0.3 ? '#1d1a36' : sh < 0.7 ? '#1a1832' : '#181630'); R(x + 5, 12, 1, 170, '#100e22');
      for (let y = 14; y < 180; y += 3) if (hash(x, y, 5) < 0.12) P(x + 1 + ((y * 7) % 4), y, '#221f3e'); }
    R(0, 11, W, 4, '#2a2540'); R(0, 11, W, 1, '#3a3458'); R(0, 14, W, 1, '#0c0a18');   // roof trusses
    for (let x = 20; x < W; x += 76) for (let i = 0; i < 12; i++) { R(x + i * 2, 15 + i, 3, 1, '#262139'); R(x + 46 - i * 2, 15 + i, 3, 1, '#262139'); }
    // the hall of past voyages
    const head = M.closed.length ? 'PAST VOYAGES: ' + M.closed.length + ' CLOSED PROJECT' + (M.closed.length === 1 ? '' : 'S') : 'PAST VOYAGES: NONE YET';
    textL(head, FR.x, 25, '#8a7a5a'); R(FR.x, 31, textW(head), 1, '#3a3048');
    LAY.boards.forEach((p, i) => board(p, i, false));
    if (LAY.moreC) { const [x, y] = boardXY(FR.per * 2 - 1); R(x, y, FR.bw, FR.bh, '#2a2030'); textC('+' + LAY.moreC, x + FR.bw / 2, y + 4, LILAC); }
    // lintel, pillars, landing, door
    R(BAY0 - 2, 68, BAYMAX - BAY0 + 4, 4, '#2e2944'); R(BAY0 - 2, 68, BAYMAX - BAY0 + 4, 1, '#463e66'); R(BAY0 - 2, 71, BAYMAX - BAY0 + 4, 1, '#0c0a18');
    const pil = [BAY0 - 3].concat(LAY.bays.map(b => b.x + b.w - 2), LAY.more ? [LAY.more.x + LAY.more.w - 2] : []);
    if (!LAY.bays.length) pil.push(BAYMAX - 2);
    for (const bx of pil) { R(bx, 68, 4, 157, '#2a2238'); R(bx, 68, 1, 157, '#3e3354'); R(bx + 3, 68, 1, 157, '#140f20'); }
    for (let y = 168; y < H; y += 4) { R(0, y, BAY0 - 3, 3, y % 8 ? '#2a2236' : '#2e2540'); R(0, y + 3, BAY0 - 3, 1, '#16101e'); }
    R(0, 166, BAY0 - 3, 2, '#3e3354');
    R(5, 104, 25, 64, OL); R(6, 105, 23, 63, '#1a2a4a');
    for (let i = 0; i < 9; i++) P(8 + hash(i, 2, 1) * 19, 108 + hash(i, 3, 1) * 22, '#c9d6ff');
    R(6, 140, 23, 28, '#22314e'); R(9, 143, 5, 6, '#ffcf6b'); R(19, 147, 4, 5, '#ffcf6b'); R(6, 160, 23, 8, '#2b2f48');   // the town through the door
    R(4, 103, 27, 2, '#4a3a5a'); R(4, 103, 1, 65, '#4a3a5a'); R(30, 103, 1, 65, '#4a3a5a');
    R(3, 92, 31, 9, OL); R(4, 93, 29, 7, '#161b38'); R(4, 93, 29, 1, '#2b336a'); textL('< TOWN', 7, 94, GOLD);
    // water in every berth (one sheet when the harbour is empty)
    const spans = LAY.bays.map(b => [b.x, b.w]).concat(LAY.more ? [[LAY.more.x, LAY.more.w]] : []);
    if (!spans.length) spans.push([BAY0, BAYMAX - BAY0]);
    for (const [x, w] of spans) for (let y = WATER; y < H; y++) { const dp = y - WATER; R(x, y, w - 2, 1, dp < 2 ? '#22517a' : dp < 10 ? '#153759' : '#102a48'); }
    for (const b of LAY.bays) if (b.p.status === 'active') slip(b); else mooring(b);
    if (LAY.more) moreBay(LAY.more);
    NOTICE = null; if (!LAY.bays.length) emptyHarbour();
    // the harbourmaster's desk on the landing, the bell bracket on the first pillar
    const D = DESK; R(D.x, D.y, D.w, 3, '#7a5236'); R(D.x, D.y, D.w, 1, '#9a6a46'); R(D.x + 2, D.y + 3, D.w - 4, 12, '#4a3020'); R(D.x + 2, D.y + 3, D.w - 4, 1, '#2a1a12');
    R(D.x + 3, D.y + 15, 3, 3, '#3a2418'); R(D.x + D.w - 6, D.y + 15, 3, 3, '#3a2418');
    stack(); R(BAY0 - 12, 112, 10, 2, '#5a4a6a'); R(BAY0 - 4, 110, 2, 6, '#5a4a6a'); R(BAY0 - 9, 114, 1, 2, '#5a4a6a');
    const lx = D.x + 18; R(lx, 15, 1, 100, '#3a3050'); R(lx - 5, 115, 11, 3, '#2a4a3a'); R(lx - 4, 114, 9, 1, '#3e6a52');
    if (dark) { g.fillStyle = 'rgba(4,5,12,0.45)'; g.fillRect(0, 12, W, H - 12); }
    R(0, 214, W, 11, 'rgba(8,10,22,0.86)'); R(0, 214, W, 1, '#2b2a48');
  });
  BOATS = {};
}
function boardXY(i) { return [FR.x + (i % FR.per) * FR.pitch, FR.y + Math.floor(i / FR.per) * 16]; }
function board(p, i, hov) {
  const [x, y] = boardXY(i), gone = !p.exists;
  R(x, y, FR.bw, FR.bh, hov ? '#8a6a3a' : '#120c10'); R(x + 1, y + 1, FR.bw - 2, FR.bh - 2, gone ? '#24222c' : '#3e2c24');
  if (!gone) { R(x + 1, y + 1, FR.bw - 2, 1, '#55402e'); R(x + 1, y + FR.bh - 2, FR.bw - 2, 1, '#2a1c16'); }
  else for (let k = 0; k < FR.bw; k += 3) { P(x + k, y, '#5a5e74'); P(x + k, y + FR.bh - 1, '#5a5e74'); }
  P(x + (FR.bw >> 1), y - 1, '#8a7a6a');
  const nm = gone ? '-' : fitText(p.board || p.short || p.name, FR.bw - 3);
  textC(nm, x + FR.bw / 2, y + 4, gone ? '#7a7f99' : hov ? '#ffe28a' : M.state === 'missing' ? '#5a5048' : '#b0925a');
}
function slip(b) { // the slip: rails, trestles, gantry, the hull (or what stands for it), the caption
  const p = b.p, st = p.state;
  b.leadRow = b.leadNote = null;   // live: L10-fix: drawn again below only if they still show (the quay may have come or gone)
  R(b.x, 170, b.w - 2, 3, '#3a2a30'); R(b.x, 170, b.w - 2, 1, '#5a4248'); R(b.x, 173, b.w - 2, 7, '#1a1424');
  for (let x = b.x + 4; x < b.x + b.w - 4; x += 10) R(x, 173, 2, 14, '#2a2030');
  R(b.x, 92, b.w - 2, 3, '#3a3050'); R(b.x, 92, b.w - 2, 1, '#544a72'); R(b.lamp[0], 95, 1, b.lamp[1] - 95, '#5a5068');
  if (st === 'missing') dustSheet(b);
  else { for (const f of [0.2, 0.5, 0.8]) { const px = Math.round(b.hx0 + b.hl * f), yb = hBot(b, px); R(px - 3, yb, 7, 170 - yb, '#5a3a24'); R(px - 3, yb, 7, 1, '#7a5034'); R(px - 1, yb + 1, 1, 169 - yb, '#3e2618'); R(px - 5, 167, 11, 3, '#4a3020'); }
    hull(b, st === 'error'); }
  if (st === 'error') { const fx = b.hx0 - 3, fy = b.top - 12; R(fx, fy, 1, 22, '#8a7a6a'); sprite(['#####', '####.', '###..', '####.', '#####'], fx + 1, fy, { '#': '#ff4a4a' }); P(fx + 1, fy, '#ffd0d0'); }
  // caption in the water under the slip
  const lines = [], fit = (c, d) => c.find(s => textW(s) <= b.w - 12) || d;
  if (p.why === 'quiet') lines.push([fit(['GONE QUIET: NO WORD FOR HOURS', 'GONE QUIET', 'QUIET'], '-'), '#8a8ea8']);
  else if (p.why === 'stopped') lines.push([fit(['STOPPED RESPONDING', 'STOPPED'], '!'), ST.error.txt]);
  else if (st === 'missing') lines.push([fit(["CAN'T SEE ITS FOLDER", 'NO FOLDER'], '-'), '#8a8ea8']);
  else if (st === 'error') lines.push([fit(p.agent ? ["SOMETHING'S WRONG", 'ERROR'] : p.why === 'unreadable' ? ["CAN'T READ TASKS.MD", "CAN'T READ"] : ["CAN'T READ IT", 'ERROR'], '!'), ST.error.txt]);
  else if (p.tasks) lines.push([fit([`PLANKED = TASKS DONE: ${p.tasks.done} OF ${p.tasks.total}`, `${p.tasks.done} OF ${p.tasks.total} DONE`], `${p.tasks.done}/${p.tasks.total}`), CYAN]);
  else if (p.agent) lines.push([fit([(p.kind || 'agent').toUpperCase() + ': ' + (p.message || '-').toUpperCase(), (p.kind || 'agent').toUpperCase()], '-'), LILAC]);
  else lines.push([fit(['NO TASKS.MD: MOORED', 'NO TASKS.MD', 'NO TASKS'], '-'), LILAC]);
  // live: L10 berths: with the live quay along the front (QUAY_ON), the caption keeps its first line only (the hull's
  // planking and its pinned note still show progress and the agent's items; the hover has the numbers)
  const lead = st !== 'missing' && p.leadN && !QUAY_ON ? fit(['+' + p.leadN + ' FOR THE AGENT', '+' + p.leadN + ' AGENT'], '+' + p.leadN) : '';
  const bar = !!p.tasks && st !== 'error' && st !== 'missing' && !QUAY_ON;
  const hh = QUAY_ON ? 10 : 12 + (bar ? 4 : 0) + (lead ? 8 : 0);
  R(b.x + 3, 184, b.w - 8, hh, 'rgba(8,10,22,0.8)'); b.capY = 184 + hh;
  textL(fitText(lines[0][0], b.w - 12), b.x + 6, 186, lines[0][1]);
  let y = 193;
  if (bar) { const bw = b.w - 14, d = Math.round(bw * p.tasks.done / p.tasks.total); R(b.x + 6, y, d, 2, '#b07a44'); R(b.x + 6 + d, y, bw - d, 2, '#2a2236'); y += 4; }
  if (lead) { R(b.x + 6, y, 4, 5, '#cfd6ff'); P(b.x + 8, y, '#5fe8ff'); textL(lead, b.x + 13, y, '#b8c0f0'); b.leadRow = [b.x + 4, y - 1, b.w - 10, 7]; }
  if (st !== 'missing' && p.leadN) { const px = b.hx0 + 5, py = Math.min(b.top + 14, KEEL - 12); R(px, py, 7, 9, '#cfd6ff'); R(px + 1, py + 2, 5, 1, '#8a90b8'); R(px + 1, py + 4, 5, 1, '#8a90b8'); R(px + 1, py + 6, 4, 1, '#8a90b8'); P(px + 3, py, '#5fe8ff'); b.leadNote = [px - 1, py - 1, 9, 11]; }
}
function hull(b, err) {
  for (let x = b.hx0 + 5; x < b.hx1; x += 10) { const t = hTop(b, x), bt = hBot(b, x); R(x + 3, t + 2, 1, bt - t - 2, '#2e2030'); }
  for (let x = b.hx0 + 5; x < b.hx1; x += 10) { const t = hTop(b, x), bt = hBot(b, x); R(x, t + 1, 1, bt - t - 1, '#6a4428'); }
  const base = err ? ['#6a5a58', '#5a4c4c'] : ['#b07a44', '#9e6c3a'];
  b.cells.forEach((cl, i) => { if (i >= b.laid) return; const col = base[(cl.k + (cl.a >> 3)) % 2];
    for (let x = cl.a; x <= cl.b; x++) { const y0 = hBot(b, x) - 3 * cl.k - 3, top = hTop(b, x); if (y0 + 2 < top) continue; const yy = Math.max(top, y0);
      R(x, yy, 1, y0 + 2 - yy, col); P(x, y0 + 2, '#5e3a22'); if (y0 >= top) P(x, y0, err ? '#7a6a68' : '#c8925a'); } });
  b.cells.forEach((cl, i) => { if (i < b.laid) return; for (let x = cl.a; x <= cl.b; x++) { const y0 = hBot(b, x) - 3 * cl.k - 3; if (y0 + 2 >= hTop(b, x)) P(x, y0 + 2, '#2e2840'); } });
  for (let x = b.hx0; x <= b.hx1; x++) { if (x % 2 === 0) P(x, hTop(b, x), '#7a6a8a'); P(x, hBot(b, x), '#3e2618'); }
  R(b.hx0, hTop(b, b.hx0), 2, hBot(b, b.hx0) - hTop(b, b.hx0) + 1, '#7a5034');
  for (let y = hTop(b, b.hx1) - 3; y <= hBot(b, b.hx1); y++) P(b.hx1 + (y < hTop(b, b.hx1) ? 1 : 0), y, '#8a5a36');
  R(b.hx0 + 2, hBot(b, b.hx0 + 2) + 1, Math.max(4, b.hl - 22), 2, '#3e2618');
}
function dustSheet(b) { // missing: a grey sheet over an empty cradle, no numbers
  for (let x = b.hx0; x <= b.hx1; x++) { const t = b.top + 8 + Math.round(Math.sin(x * 0.3)), bt = 170; R(x, t, 1, bt - t, x % 9 < 1 ? '#3a3c4c' : '#44465a'); P(x, t, '#5a5c70'); }
  textC('-', (b.hx0 + b.hx1) / 2, b.top + 22, '#8a8ea8');
}
function mooring(b) { // a paused berth: the mooring ring on the pillar; the boat itself is drawn per frame (it bobs)
  const rx = b.x + b.w - 5; R(rx - 1, WATER - 13, 3, 3, '#8a7a60'); P(rx, WATER - 12, '#14122a');
  if (!b.p.exists) { for (let i = 0; i < 14; i++) P(rx - 1 - Math.round(Math.sin(i / 13 * 1.4) * 4), WATER - 10 + i, '#8a7a60');   // a rope to nothing
    const tw = textW('FOLDER GONE') + 6, tx = b.x + ((b.w - 2 - tw) >> 1);
    if (tw < b.w - 4) { R(tx, 150, tw, 9, OL); R(tx + 1, 151, tw - 2, 7, '#24263a'); textL('FOLDER GONE', tx + 3, 152, '#8a8fa8'); }
    else { R(b.cx - 5, 150, 11, 9, OL); textC('-', b.cx + 1, 152, '#8a8fa8'); } }
  const lx = b.cx; R(lx, 92, 1, 12, '#3a3050'); R(lx - 2, 104, 5, 1, '#3a3050'); R(lx - 2, 105, 5, 5, '#22202e'); R(lx - 1, 106, 3, 3, '#2e2c3a'); R(lx - 2, 110, 5, 1, '#3a3050');   // its lamp: dark
}
function moreBay(m) {
  for (let i = 0; i < Math.min(3, m.rest.length); i++) { const y = 150 + i * 9, x0 = m.x + 4, L = m.w - 12;
    R(x0, y, L, 5, i % 2 ? '#3a2a2e' : '#4a343a'); R(x0 + 2, y - 2, L - 4, 2, TARP); }
  const s = '+' + m.rest.length, w = textW(s) + 6, x = m.x + ((m.w - 2 - w) >> 1); R(x, 128, w, 9, OL); R(x + 1, 129, w - 2, 7, '#262a48'); textL(s, x + 3, 130, LILAC);
}
let NOTICE = null;
function emptyHarbour() { // no berths to show: an empty slip under a dust sheet and a plain notice
  const msg = M.empty ? 'NO PROJECTS IN THE SETTINGS YET' : 'NO ACTIVE OR PAUSED PROJECTS', sub = M.empty ? 'ADD A PROJECTS SOURCE TO LAMPTOWN.TOML' : 'THE CLOSED ONES HANG ON THE WALL';
  for (let x = 140; x <= 350; x++) { const t = 150 + Math.round(Math.sin(x * 0.3)); R(x, t, 1, 170 - t, x % 9 < 1 ? '#3a3c4c' : '#44465a'); P(x, t, '#5a5c70'); }
  const w = 220, h = 24, x = 244 - w / 2, y = 100;
  R(x - 1, y - 1, w + 2, h + 2, OL); R(x, y, w, h, '#1d1f2a'); R(x, y, w, 1, '#5a5e74');
  textC(msg, 244, y + 5, '#a9aec8'); textC(sub, 244, y + 14, '#7a7f99');
  NOTICE = [x, y, w, h];
}
function stack() { // one sheet per item waiting on you (thinner above 24, never dropped) + a rubber stamp
  const D = DESK, n = M.yoursN;
  if (n) { const pitch = n > 24 ? 0.5 : 1, h = Math.min(30, Math.ceil(n * pitch)); for (let i = 0; i < h; i++) R(D.x + 8, D.y - 1 - i, 20, 1, i % 2 ? '#d8d0c0' : '#f6f2e8');
    R(D.x + 8, D.y - h - 1, 20, 1, '#ffffff'); P(D.x + 27, D.y - h, '#c8b8a0'); R(D.x + 30, D.y - 3, 6, 3, '#9a3a3a'); R(D.x + 32, D.y - 6, 2, 3, '#5a3a24'); }
  else if (M.state !== 'missing') R(D.x + 10, D.y - 1, 16, 1, '#5a5a6a');
  R(D.x + 3, D.y + 5, D.w - 6, 9, OL);
  const lab = n ? [n + ' FOR YOU', n + ' YOURS', String(n)].find(s => textW(s) <= D.w - 8) : M.state === 'missing' ? '-' : 'ALL CLEAR';
  textC(lab, D.x + D.w / 2, D.y + 7, n ? AMBER : M.state === 'missing' ? '#6a6e84' : '#7cf0a8');
}

/* ---------- per frame ---------- */
function pausedBoat(b, t, hov) { // cached per berth (two looks: hovered or not); it bobs one pixel
  const k = b.p.id + (hov ? '|h' : '');
  if (!BOATS[k]) BOATS[k] = layer(b.w, 70, () => {
    const L = b.w - 18, x0 = 8, wl = 52, top = x => wl - 12 - (x - x0 > L * 0.75 ? Math.round(4 * Math.pow((x - x0 - L * 0.75) / (L * 0.25), 2)) : 0);
    for (let x = x0; x <= x0 + L; x++) { const tp = top(x), bt = wl - (x - x0 > L * 0.8 ? Math.round(8 * (x - x0 - L * 0.8) / (L * 0.2)) : 0); R(x, tp, 1, bt - tp, (x - x0) % 9 < 1 ? '#3a2a2e' : '#4a343a'); P(x, tp, '#6a4c50'); }
    for (let x = x0 + 2; x <= x0 + L - 3; x++) { const u = (x - x0) / L, hump = Math.round(6 * Math.sin(Math.PI * Math.min(1, u * 1.05)) + Math.sin(x * 0.7) * 0.6) + (hov ? 2 : 0), tp = top(x) - 2 - hump;
      R(x, tp, 1, top(x) + 2 - tp, (x - x0) % 8 < 1 ? '#4a4e6c' : hov ? '#6a6f96' : TARP); P(x, tp, hov ? '#8a90b8' : '#6f7498'); }
    for (let x = x0 + 8; x < x0 + L - 6; x += 12) { const tp = top(x) - 7; for (let j = 0; j < 9; j++) P(x + (j >> 2), tp + j, '#2e2a3a'); }
    for (let i = 0; i < 9 && x0 + L + 1 + i < b.w - 4; i++) P(x0 + L + 1 + i, wl - 10 + Math.round(Math.sin(i / 8 * Math.PI) * 2), '#8a7a60');
    const word = b.p.private ? 'PRIVATE' : 'PAUSED BY YOU';
    const one = textW(word) + 6 <= b.w - 6, rows = one ? [word] : b.p.private ? ['PRIV.'] : b.w >= 32 ? ['PAUSED', 'BY YOU'] : ['II'];
    const tw = Math.max(...rows.map(r => textW(r))) + 6, th = rows.length * 7 + 2, tx = (b.w - 2 - tw) >> 1, ty = 24 - th;
    R(tx + (tw >> 1), ty + th, 1, Math.max(0, top(tx + (tw >> 1)) - 6 - ty - th), '#5a5068');
    R(tx, ty, tw, th, OL); R(tx + 1, ty + 1, tw - 2, th - 2, hov ? '#3a3e66' : '#262a48'); rows.forEach((r, i) => textC(r, tx + tw / 2, ty + 2 + i * 7, hov ? '#e8eaff' : PAUSE_TXT));
    if (b.p.private) { const lx = tx - 6, ly = ty + 1; R(lx, ly + 2, 5, 4, '#8a7a60'); R(lx + 1, ly, 3, 1, '#8a7a60'); P(lx, ly + 1, '#8a7a60'); P(lx + 4, ly + 1, '#8a7a60'); P(lx + 2, ly + 3, OL); }   // a small padlock
    for (let j = 0; j < 6; j++) if (j % 2 === 0) R(x0 + 2 + j, wl + 4 + j * 2, L - 6 - j * 2, 1, 'rgba(90,96,130,0.18)');
  });
  const bob = K.still ? 0 : Math.floor(t * 0.7 + b.x) % 2;
  g.drawImage(BOATS[k], b.x, WATER - 52 + 2 + bob);
}
function nameBoards(t) {
  for (const b of LAY.bays) {
    const p = b.p, act = p.status === 'active', st = p.state, hov = HOV === 'boat:' + p.id;
    const cands = [p.name.toUpperCase(), p.short, p.short.split(' ')[0]].filter(Boolean), room = b.w - 6 - (act ? 11 : 6);
    const nm = cands.find(s => textW(s) <= room) || fitText(p.short || p.name, room);
    const w = Math.min(b.w - 6, textW(nm) + (act ? 14 : 8)), x = Math.round(b.x + (b.w - 2 - w) / 2), y = 74;
    R(x, y, w, 9, hov ? GOLD : OL); R(x + 1, y + 1, w - 2, 7, '#161b38'); R(x + 1, y + 1, w - 2, 1, '#2b336a');
    if (act) {
      const c = ST[st], blink = K.still || !(st === 'waiting' || st === 'error') || Math.sin(t * 4) > 0;
      R(x + 3, y + 3, 3, 3, c.lamp); if (blink && st !== 'missing') P(x + 3, y + 3, c.hi);
      textL(nm, x + 9, y + 2, st === 'missing' ? '#8a8ea8' : WHITE);
      const word = WHY_WORD[p.why] || WORD[st], ww = textW(word); R(x + (w >> 1) - (ww >> 1) - 2, y + 9, ww + 4, 8, OL); textC(word, x + w / 2, y + 10, c.txt);
    } else {
      textL(nm, x + 4, y + 2, p.exists ? PAUSE_TXT : '#7a7f99'); R(b.cx, y + 9, 1, 3, '#5a5068');
    }
  }
}
function lamps(t) {
  for (const b of LAY.bays) {
    const p = b.p; if (p.status !== 'active') continue;
    const st = p.state, c = ST[st], [cx, cy] = b.lamp, on = st !== 'missing';
    const blink = K.still ? 1 : st === 'error' ? (Math.sin(t * 6) > 0 ? 1 : 0.25) : st === 'waiting' ? 0.75 + 0.25 * Math.sin(t * 3) : 1;
    R(cx - 3, cy - 3, 7, 3, '#3a3050'); R(cx - 2, cy, 5, 2, on ? c.lamp : '#2a2a38');
    if (!on) continue;
    glow(glowSp('#ffcf8a', Math.min(60, b.w >> 1), 30, 0.11), cx, (cy + KEEL) >> 1, 0.9);
    glow(glowSp(c.glow, 14, 12, 0.36), cx, cy + 4, blink * 0.85);
    if (st === 'waiting') bubble(cx + 9, cy - 1, t);
    if (st === 'error') { g.fillStyle = 'rgba(150,10,24,' + (K.still ? 0.14 : 0.12 + 0.06 * Math.sin(t * 6)).toFixed(3) + ')'; g.fillRect(b.x, 92, b.w - 2, 88); }
  }
}
function bubble(lx, ly, t) { // the town's "!" (needs you)
  const bob = K.still || Math.sin(t * 3) > 0 ? 0 : 1, bx = lx - 6, by = ly - 12 + bob;
  R(bx + 1, by, 11, 12, OL); R(bx, by + 1, 13, 10, OL); R(bx + 2, by + 1, 9, 10, '#f4f6ff'); R(bx + 1, by + 2, 11, 8, '#f4f6ff');
  R(bx + 5, by + 2, 3, 5, '#ffb020'); R(bx + 5, by + 8, 3, 2, '#ffb020'); R(bx + 6, by + 2, 1, 5, '#fff0c0');
}
const atWork = p => p.status === 'active' && (p.busy || p.state === 'working') && p.state !== 'missing' && p.state !== 'error';
function shipwright(t) { // on every boat someone is at work on: he lays the next plank
  for (const b of LAY.bays) {
    const p = b.p; if (!atWork(p)) continue;
    const cl = b.cells[Math.min(b.laid, b.cells.length - 1)]; if (!cl) continue;
    const x = Math.round((cl.a + cl.b) / 2), y = hBot(b, x) - 3 * cl.k - 3, blink = K.still || Math.sin(t * 5) > 0;
    if (b.laid < b.cells.length) for (let xx = cl.a; xx <= cl.b; xx++) { const yy = hBot(b, xx) - 3 * cl.k - 3; if (yy >= hTop(b, xx)) R(xx, yy, 1, 2, blink ? '#9ff6ff' : '#3ab8d0'); }
    const fx = Math.min(x + 6, b.hx1 - 4), fy = Math.min(y - 2, KEEL - 14), up = K.still ? 1 : Math.floor(t * 3) % 2;
    R(fx - 6, fy + 9, 14, 1, '#7a5034'); R(fx - 5, fy + 10, 1, 170 - fy - 10, '#4a3020'); R(fx + 6, fy + 10, 1, 170 - fy - 10, '#4a3020');
    R(fx, fy - 1, 3, 3, '#e8b48a'); R(fx, fy + 2, 3, 4, '#2f9ab0'); R(fx, fy + 6, 1, 3, '#2a2a44'); R(fx + 2, fy + 6, 1, 3, '#2a2a44');
    if (up) { R(fx - 2, fy - 1, 2, 1, '#aab4d8'); R(fx - 1, fy, 1, 2, '#a0643a'); }
    else { R(fx - 3, fy + 2, 3, 1, '#a0643a'); R(fx - 3, fy + 1, 1, 2, '#aab4d8'); P(fx - 5, fy + 1, '#ffffff'); }
    glow(glowSp('#5fe8ff', 18, 14, 0.32), fx, fy + 2, 0.9);
  }
}
function water(t) {
  const spans = LAY.bays.map(b => [b.x, b.w, b.capY || 0]).concat(LAY.more ? [[LAY.more.x, LAY.more.w, 0]] : []);
  for (const [bx, bw, cap] of spans) for (let i = 0; i < 5; i++) { const ph = K.still ? 0.3 : (t * 0.25 + hash(i, bx, 2)) % 1, x = bx + 4 + Math.floor(hash(i, bx, 4) * (bw - 16)), y = WATER + 6 + i * 7;
    if (ph < 0.6 && y > cap) R(x + Math.round(ph * 6), y, 3, 1, '#2a5e8a'); }   // never across a caption
}
function deskAndBell(t) {
  const D = DESK, n = M.yoursN, wait = n > 0, miss = M.state === 'missing', lx = D.x + 18;
  lit += ((HOV === 'desk' && wait ? 1 : wait ? 0.55 : 0.3) - lit) * (K.still ? 1 : 0.15);
  R(lx - 2, 118, 5, 1, wait ? '#fff2c0' : miss ? '#2a2a38' : '#c8b088');
  if (!miss) { glow(glowSp('#ffb44a', 40, 30, 0.46), lx, 140, lit); if (wait) glow(glowSp('#ffd36e', 16, 10, 0.6), lx, 130, lit * 0.9); }
  const ring = NOW_T() - bellT < 1.6, sw = wait && !K.still ? Math.round(Math.sin(t * (ring ? 9 : 2.2)) * (ring ? 2 : 1)) : 0, bx = BAY0 - 9 + sw, by = 116;
  sprite(['..#..', '.###.', '.###.', '#####', '#####', '..#..'], bx - 2, by, { '#': wait ? '#ffbf3a' : miss ? '#3a3a48' : '#7a6a48' });
  if (wait) { P(bx - 1, by + 1, '#fff0a8'); glow(glowSp('#ff9a1f', 12, 12, 0.5), bx, by + 3, ring ? 1 : 0.55); }
}
let listShown = 0;
function drawList() { // the core moment: the lamp flares and the first items rise off the stack
  listShown = 0;
  if (HOV !== 'desk' || !M.items.length) return;
  const show = M.items.slice(0, 5), multi = new Set(M.items.map(i => i.p.id)).size > 1, n = M.yoursN;
  const x = 30, w = 214, hh = 12 + show.length * 7 + 12, y = 14 + (K.still ? 0 : Math.round(Math.max(0, 1 - (NOW_T() - bellT) * 4) * 6));
  R(x - 1, y - 1, w + 2, hh + 2, OL); R(x, y, w, hh, '#c88a2a'); R(x + 1, y + 1, w - 2, hh - 2, '#161b38'); R(x + 1, y + 1, w - 2, 1, '#2b336a');
  textL(n + ' WAIT ON YOU', x + 5, y + 3, AMBER);
  if (M.leadN) { const s = '+' + M.leadN + ' FOR THE AGENT'; textL(s, x + w - 5 - textW(s), y + 3, LILAC); }
  R(x + 4, y + 9, w - 8, 1, '#4a3a2a');
  show.forEach((s, i) => { R(x + 6, y + 14 + i * 7, 2, 2, AMBER); textL(fitText((multi ? s.p.short + ': ' : '') + s.short, w - 18), x + 11, y + 12 + i * 7, WHITE); });
  const where = M.deskP && M.deskP.tasksHref ? 'CLICK THE STACK FOR TASKS.MD' : 'THE CARD (KEY I) LISTS THEM ALL';
  textL(fitText(n > show.length ? 'AND ' + (n - show.length) + ' MORE. ' + where : where, w - 10), x + 5, y + hh - 8, LILAC);
  for (let j = y + hh + 2; j < 114; j += 2) P(DESK.x + 17, j, '#c88a2a');
  listShown = show.length;
}
function hoverMarks() {
  if (!HOV) return;
  if (HOV.startsWith('closed:')) { const i = LAY.boards.findIndex(p => 'closed:' + p.id === HOV); if (i >= 0) board(LAY.boards[i], i, true); }
}

/* ---------- live: L10 berths: the live quay along the front, the same berths as on the town's wharf ----------
   One cell per berth in BERTHS' order (the order they were given out), the project's boat paint on its name board,
   and who works there: every agent on it at the Harbour, as a Lamplighter with its lantern (done ones hang a green
   lantern on the cell's post). The projects sharing the overflow mooring: one "+N" cell. Names only. */
const QY = { board: 196, feet: 218, deck: 209 }, QRANK = { waiting: 0, error: 1, working: 2, quiet: 3 };
function liveView() { return typeof BERTHS === 'object' && BERTHS && !demo ? BERTHS.view() : null; }
function quayNow() { const v = liveView(); return !!(v && v.on && (v.berths.length || v.over.length)); }
function quayCells(v) {
  const list = v.berths.map(b => ({ b })).concat(v.over.length ? [{ over: v.over }] : []);
  const w = Math.min(84, Math.floor((W - 4) / Math.max(1, list.length)));
  return list.map((c, i) => Object.assign(c, { x: 2 + i * w, w }));
}
function quay() {
  QUAY = null;
  const v = liveView(); if (!v || !v.on || (!v.berths.length && !v.over.length)) return;
  const cells = quayCells(v), glows = [], shown = [];
  // the quay's deck, from just above (as the landing's floor): planks along it, their seams, the near edge
  R(0, QY.deck - 1, W, 1, OL); R(0, QY.deck, W, 1, '#8a6444');
  for (let y = QY.deck + 1; y < H; y += 3) { R(0, y, W, 2, (y >> 1) % 2 ? '#5a4030' : '#634634'); R(0, y + 2, W, 1, '#3a281e'); }
  for (let y = QY.deck + 1, k = 0; y < H; y += 3, k++) for (let x = (k * 13) % 31; x < W; x += 31) P(x, y, '#3a281e');
  for (const c of cells) {
    const { x, w } = c, bx = x + 5, bw = w - 8;
    R(x + 1, QY.board - 1, 3, QY.deck - QY.board + 2, OL); R(x + 2, QY.board, 1, QY.deck - QY.board + 1, '#7a5638');   // the cell's post
    R(bx, QY.board, bw, 9, OL); R(bx + 1, QY.board + 1, bw - 2, 7, '#161b38'); R(bx + 1, QY.board + 1, bw - 2, 1, '#2b336a');
    if (c.over) {
      const s = fitText('+' + c.over.length + ' MORE', bw - 6);
      textL(s, bx + 3, QY.board + 2, LILAC);
      c.over.slice(0, Math.floor((bw - 4) / 7)).forEach((o, i) => { const px = bx + 3 + i * 7; R(px - 1, QY.deck + 3, 7, 5, OL); R(px, QY.deck + 4, 5, 3, o.paint); });   // their paints
      shown.push({ over: c.over.map(o => o.id), x, w });
      continue;
    }
    const b = c.b;
    R(bx + 2, QY.board + 2, 6, 4, OL); R(bx + 3, QY.board + 3, 4, 2, b.paint); P(bx + 7, QY.board + 3, b.paint);   // her paint
    textL(fitText(b.name, bw - 12), bx + 10, QY.board + 2, WHITE);
    const ags = b.agents.filter(a => a.state !== 'ok').sort((p, q) => QRANK[p.state] - QRANK[q.state]);
    const fit = Math.max(1, Math.floor((w - 12) / 9)), n = ags.length > fit ? fit - 1 : ags.length, drawn = [];
    for (let i = 0; i < n; i++) { const a = ags[i], d = BERTHS.figure(x + 10 + i * 9, QY.feet, a, 1); if (d) glows.push(d); drawn.push(a.id); }
    if (ags.length > n) textL('+' + (ags.length - n), x + 7 + n * 9, QY.feet - 7, '#ffd59a');
    if (!ags.length) textL(fitText(b.facts.ok ? 'DONE HERE' : 'NO AGENT NOW', w - 10), x + 7, QY.feet - 7, b.facts.ok ? '#7ff0a8' : '#7a7f99');
    if (b.facts.ok) { R(x + 3, QY.board + 11, 3, 1, '#262636'); R(x + 3, QY.board + 12, 3, 3, '#7ff0a8'); glows.push({ x: x + 4, y: QY.board + 13, col: '#3dff8a', small: true }); }
    shown.push({ id: b.id, name: b.name, x, w, agents: drawn, more: ags.length - n, done: b.facts.ok, paint: b.paint });
  }
  for (const d of glows) glow(glowSp(d.col, d.small ? 3 : 6, d.small ? 3 : 5, 0.36), d.x, d.y);
  QUAY = { cells: shown, list: cells };
}
function quayTargets() {
  if (!QUAY) return [];
  return QUAY.list.map(c => (c.over
    ? { id: 'berth:more', box: [c.x, QY.board - 1, c.w, QY.deck - QY.board + 6], tip: [plural(c.over.length, 'MORE PROJECT') + ' SHARE THE OVERFLOW MOORING'].concat(c.over.slice(0, 6).map(o => o.name)),
      label: c.over.length + ' more live projects.', onClick: null }
    : { id: 'berth:' + c.b.id, box: [c.x, QY.board - 1, c.w, QY.deck - QY.board + 6], tip: c.b.tip, label: c.b.tip.join('. '), onClick: null }));
}

/* ---------- sound (synthesised, short, quiet) ---------- */
function bell() { const ac = sfx.ctx(); if (!ac) return; const t0 = ac.currentTime + 0.02; sfx.tone(t0, 1046, 1.1, 0.3, 'sine'); sfx.tone(t0, 1568, 0.7, 0.14, 'sine'); sfx.tone(t0, 2637, 0.25, 0.07, 'sine'); }
function flap() { const ac = sfx.ctx(); if (!ac) return; const t0 = ac.currentTime + 0.01; sfx.noise(t0, 0.18, 0.5, 260, 'bandpass'); sfx.tone(t0, 70, 0.15, 0.25, 'sine'); }
function knock() { const ac = sfx.ctx(); if (!ac) return; const t0 = ac.currentTime + 0.01; sfx.tone(t0, 220, 0.08, 0.4, 'triangle'); sfx.noise(t0, 0.05, 0.3, 1500, 'bandpass'); sfx.tone(t0 + 0.12, 200, 0.08, 0.3, 'triangle'); }
function lap() { const ac = sfx.ctx(); if (!ac) return; const t0 = ac.currentTime + 0.25; sfx.noise(t0, 0.9, 0.12, 300, 'lowpass'); sfx.noise(t0 + 0.5, 0.6, 0.08, 520, 'bandpass'); }
function tick() { const ac = sfx.ctx(); if (!ac) return; sfx.noise(ac.currentTime + 0.01, 0.03, 0.12, 2600, 'bandpass'); }

/* ---------- hover / click targets ---------- */
const when = iso => K.dayText(iso) + ' ' + K.timeText(iso).slice(4) + ' (' + K.untilText(iso) + ')';
function boatTip(p) {
  if (p.status === 'paused') {
    if (p.private) return [p.name + ': private', p.exists ? 'Moored here, never read inside.' : "Can't see its folder.", 'Private projects show paused whatever their stage.'];
    return p.exists ? [p.name + ': paused by you', 'Since ' + (p.since ? K.dayText(p.since) : '-'), p.note, 'Covered and moored. Not read while paused.'].filter(Boolean)
      : [p.name + ': paused, folder gone', "Can't see its folder. Check its path in the settings."];
  }
  const st = p.state, tasksBad = p.why === 'unreadable';
  if (p.agent) {   // a status-file berth: its own words
    const out = [p.name + ': ' + ST[st].word, p.message || ''];
    if (p.tasks) out.push('Progress: ' + p.tasks.done + ' of ' + p.tasks.total);
    if (p.yoursN) out.push('Waits on you: ' + p.yoursN);
    if (p.updated) out.push('Updated ' + when(p.updated));
    if (p.kind) out.push('From an agent-status file (' + p.kind + ')');
    return out.filter(Boolean);
  }
  const out = [p.name + ': ' + { ok: 'all good', waiting: 'needs you', working: 'being built now', error: tasksBad ? "can't read its TASKS.md" : "something's wrong", missing: "can't see its folder" }[st]];
  if (st === 'missing') return out.concat([p.why === 'nopath' ? 'The settings give it no path.' : "Its folder isn't there.", 'Check its path in the settings.']);
  if (p.note) out.push(p.note);
  if (st === 'error') return out.concat([tasksBad ? 'TASKS.md is there but could not be read.' : p.message || 'The reason was not recorded.']);
  out.push(p.tasks ? 'Tasks done: ' + p.tasks.done + ' of ' + p.tasks.total + ' (' + Math.round(100 * p.tasks.done / p.tasks.total) + '%)' : 'No TASKS.md to count.');
  out.push('Waits on you: ' + p.yoursN + (p.leadN ? '  (+' + p.leadN + ' for the agent)' : ''));
  if (p.lastCommit && p.lastCommit.when) out.push('Last commit ' + when(p.lastCommit.when) + (p.lastCommit.msg ? ': ' + p.lastCommit.msg : ''));
  if (p.busy) out.push('Being worked on: a change in the last half hour.');
  if (p.tasksHref || p.href) out.push('Click: open its ' + (p.tasksHref ? 'TASKS.md' : 'folder'));
  return out;
}
function openBoat(p) {
  knock();
  if (p.status === 'paused') { flap(); K.toast(p.private ? 'PRIVATE: NEVER READ INSIDE' : p.exists ? 'PAUSED BY YOU' + (p.since ? ' ON ' + K.dayText(p.since) : '') + '. NOT READ UNTIL YOU SAY GO.' : 'PAUSED, AND ITS FOLDER IS GONE'); return; }
  const href = p.tasksHref || p.href || (p.yours[0] && p.yours[0].href);
  if (href) { K.open(href); K.toast('OPENING ' + (p.short || p.name) + (p.tasksHref ? "'S TASKS.MD" : '')); }
  else K.toast(p.sample ? 'SAMPLE BOAT: NOTHING TO OPEN' : p.agent ? 'NOTHING TO OPEN FOR ' + p.short : "NOTHING TO OPEN: CAN'T SEE ITS FOLDER");
}
function deskTip() {
  if (!M.yoursN) return [M.state === 'missing' ? 'Nothing to show: no projects to see' : 'Nothing waits on you', M.leadN ? M.leadN + ' item(s) are the agent\'s own.' : ''].filter(Boolean);
  return null;   // the list rising off the stack is the tip
}
function targets() {
  const out = [{ id: 'door', box: [3, 92, 31, 76], tip: ['Back to town', 'or press Esc'], label: 'Door: back to town', onClick: K.leave }];
  LAY.boards.forEach((p, i) => { const [x, y] = boardXY(i);
    out.push({ id: 'closed:' + p.id, box: [x, y, FR.bw, FR.bh], tip: [p.name, (p.exists ? 'Closed' : 'Closed, folder gone') + (p.since ? ' on ' + K.dayText(p.since) : ''), p.note].filter(Boolean),
      label: p.name + ', closed project.', onClick: () => K.toast(fitText(p.name.toUpperCase(), 200) + ': A PAST VOYAGE') }); });
  if (LAY.moreC) { const [x, y] = boardXY(FR.per * 2 - 1), rest = M.closed.slice(LAY.boards.length);
    out.push({ id: 'closed:more', box: [x, y, FR.bw, FR.bh], tip: ['+' + LAY.moreC + ' more closed projects', rest.slice(0, 6).map(p => p.name).join(', ') + (rest.length > 6 ? ', ...' : '')], label: LAY.moreC + ' more closed projects.', onClick: null }); }
  const dp = M.deskP, dh = dp && (dp.tasksHref || (dp.yours[0] && dp.yours[0].href));
  out.push({ id: 'desk', box: [DESK.x - 2, 112, DESK.w + 18, 58], tip: deskTip(),
    label: M.yoursN ? M.yoursN + ' things wait on you' + (M.leadN ? ', and ' + M.leadN + ' for the agent' : '') + '. Open them.' : 'Nothing waits on you.',
    onClick: () => { if (dh) { K.open(dh); K.toast('OPENING ' + dp.short + (dp.tasksHref ? "'S TASKS.MD" : '') + ': WHAT WAITS ON YOU'); } else K.toast(M.yoursN ? (M.sample ? 'SAMPLE STACK: NOTHING TO OPEN' : 'THE CARD (KEY I) LISTS THEM') : 'NOTHING WAITS ON YOU'); } });
  for (const b of LAY.bays) { const p = b.p;
    if (b.leadNote || b.leadRow) { const tip = [p.leadN + ' for the agent, not for you'].concat(p.lead.slice(0, 2).map(l => l.short), ['The agent handles it.']);
      for (const box of [b.leadNote, b.leadRow].filter(Boolean)) out.push({ id: 'lead:' + p.id, box, tip, label: p.leadN + ' item for the agent: ' + (p.lead[0] ? p.lead[0].short : ''), onClick: null }); }
    out.push({ id: 'boat:' + p.id, box: [b.x, 74, b.w - 2, 132], tip: boatTip(p), label: boatTip(p).join('. '), onClick: () => openBoat(p) }); }
  if (LAY.more) { const r = LAY.more.rest;
    out.push({ id: 'boat:more', box: [LAY.more.x, 74, LAY.more.w - 2, 132], tip: ['+' + r.length + ' more projects'].concat(r.slice(0, 8).map(p => p.name + ' (' + (p.private ? 'private' : p.status) + ')')), label: r.length + ' more projects.', onClick: null }); }
  if (NOTICE) out.push({ id: 'notice', box: NOTICE, tip: M.empty ? ['No projects to show', 'List them in a projects source in lamptown.toml.'] : ['No active or paused projects', 'Closed projects hang on the wall above.'], label: 'Notice: no projects', onClick: null });
  return quayTargets().concat(out);   // live: L10 berths: the quay is in front of the bays
}
function hover(tg) { // the town's hover hook (pointer or Tab): the desk rings, a paused tarp flaps
  const id = tg && tg.id || null; if (id === HOV) return; HOV = id;
  if (id === 'desk' && M.yoursN) { bellT = NOW_T(); bell(); }
  else if (id && id.startsWith('boat:')) { const p = M.all.find(q => 'boat:' + q.id === id); if (p && p.status === 'paused') flap(); }
}

/* ---------- demo (?demo=1): the core moment on a timer ---------- */
const INTRO = { ok: 'EVERY BOAT IS A PROJECT: ALL ON COURSE', waiting: 'SHEETS ON THE DESK WAIT ON YOU', working: 'A SHIPWRIGHT LAYS THE NEXT PLANK',
  error: "A RED FLAG: ITS TASKS.MD CAN'T BE READ", missing: 'A DUST SHEET: ITS FOLDER IS GONE' };
function drawCursor(x, y) { const rows = ['#.....', '##....', '#.#...', '#..#..', '#...#.', '#..###', '#.#...', '##....']; sprite(rows, x + 1, y + 1, { '#': OL }); sprite(rows, x, y, { '#': '#f4f6ff' }); }
function runDemo() {
  timers.forEach(clearTimeout); timers = []; caption = ''; cursor = null; demoHov(null);
  const at = (s, f) => timers.push(setTimeout(f, s * 1000)), first = LAY.bays.find(b => b.p.status === 'active'), pz = LAY.bays.find(b => b.p.status === 'paused');
  at(0.3, () => { caption = INTRO[(demo && demo.state) || M.state] || INTRO.ok; });
  if (M.yoursN) at(1.8, () => { caption = 'HOVER THE DESK: THE FIRST THINGS WAITING ON YOU'; cursor = [DESK.x + 20, 140]; demoHov('desk'); });
  if (pz) at(4.6, () => { caption = 'A COVERED BOAT IS PAUSED: NOT READ'; cursor = [pz.cx + 2, 160]; demoHov('boat:' + pz.p.id); });
  if (first) at(7.4, () => { caption = 'HOVER A BOAT FOR ITS NUMBERS. CLICK: ITS TASKS.MD'; cursor = [first.hx0 + 30, 140]; demoHov('boat:' + first.p.id); });
  at(10.6, () => { cursor = null; demoHov(null); caption = ''; });
  at(13, runDemo);
}
let DHOV = null;
function demoHov(id) { DHOV = id; hover(id ? { id } : null); }
function drawDemoLayer() {
  if (caption) { R(2, 215, 4, 9, AMBER); textL(caption, 9, 217, '#ffd59a'); }
  if (cursor) drawCursor(cursor[0], cursor[1]);
  if (DHOV) { const tg = targets().find(o => o.id === DHOV); if (tg && tg.tip && cursor) demoTip(tg.tip, cursor[0], cursor[1]); }
}
function demoTip(lines, ax, ay) { // the demo's fake pointer has no real hover, so it draws the same tip the town would
  const rows = []; lines.forEach((l, i) => wrapText(l, 190).forEach(r => rows.push([r, i === 0])));
  const w = Math.max(...rows.map(r => textW(r[0]))) + 7, h = rows.length * 7 + 5;
  let x = ax + 7, y = ay + 9; if (x + w > W - 2) x = ax - w - 4; if (y + h > H - 2) y = ay - h - 3; x = Math.max(2, Math.min(W - w - 2, x)); y = Math.max(13, Math.min(H - h - 2, y));
  R(x - 1, y - 1, w + 2, h + 2, OL); R(x, y, w, h, '#161b38'); R(x, y, w, 1, '#59629a');
  rows.forEach(([r, f], i) => textL(r, x + 4, y + 3 + i * 7, f ? GOLD : WHITE));
}
function drawSampleBadge() {
  const s = 'SAMPLE: MADE-UP PROJECTS', sw = textW(s) + 8, x = W - sw - 4, y = 203;
  R(x, y, sw, 9, OL); R(x + 1, y + 1, sw - 2, 7, '#3a2208'); textL(s, x + 4, y + 2, '#ffd36e');
}

/* ---------- the room ---------- */
function setup() {
  const q = demo && demo.qs, n = k => Math.max(0, Math.min(40, parseInt(q && q.get(k), 10) || 0));
  M = demo ? demoModel(demo.state, n('boats'), n('closed')) : model(raw);
  NOTICE = null; LAY = layout(); STATIC = null; BOATS = {}; HOV = null; lit = M.yoursN ? 0.55 : 0.3;
}
registerRoom('harbour', {
  enter(data, opts) {
    raw = data && typeof data === 'object' ? data : { state: 'missing', missing: true };
    demo = opts.demo ? { state: DEMO_STATES.includes(opts.state) ? opts.state : '', qs: opts.qs } : null;
    QUAY_ON = quayNow(); setup(); buildStatic(); lap(); enters++;
    if (demo) runDemo();
  },
  /* new data while the room is open (a live swap): the berths are drawn again in place; the hover, the desk lamp and the
     bell keep going, no door sound. The demo shows its own sample projects, so it keeps them. */
  update(data) {
    raw = data && typeof data === 'object' ? data : { state: 'missing', missing: true };
    updates++;
    if (demo) return;
    const hov = HOV;
    M = model(raw); NOTICE = null; LAY = layout(); BOATS = {}; buildStatic();
    HOV = hov && targets().some(o => o.id === hov) ? hov : null;   // still there: still hovered
  },
  leave() { timers.forEach(clearTimeout); timers = []; STATIC = null; BOATS = {}; HOV = null; DHOV = null; caption = ''; cursor = null; },
  draw(ctx, t) {
    const q = quayNow(); if (q !== QUAY_ON) { QUAY_ON = q; STATIC = null; }   // live: L10 berths: the captions make room
    if (!STATIC) { buildStatic(); if (HOV && !targets().some(o => o.id === HOV)) HOV = null; }   // live: L10-fix: no hover on what went
    g.drawImage(STATIC, 0, 0);
    hoverMarks(); water(t);
    for (const b of LAY.bays) if (b.p.status === 'paused' && b.p.exists) pausedBoat(b, t, HOV === 'boat:' + b.p.id);
    nameBoards(t); lamps(t); shipwright(t); deskAndBell(t);
    glow(glowSp('#7fa8ff', 16, 30, 0.14), 17, 135, M.state === 'missing' ? 0.2 : 0.6);
    quay();   // live: L10 berths
    drawList();
    if (!caption && !QUAY_ON) textL(M.sample ? 'SAMPLE HARBOUR (DEMO). EACH BOAT IS A PROJECT' : 'EACH BOAT IS A PROJECT. HOVER OR CLICK ONE. ESC = BACK TO TOWN', 5, 217, LILAC);
    if (M.sample) drawSampleBadge();
    if (demo) drawDemoLayer();
    if (!K.still && LAY.bays.some(b => atWork(b.p)) && t - lastTick > 1.3) { lastTick = t; tick(); }
    if (t < lastTick) lastTick = t;
  },
  targets,
  hover,
  plate() { return { num: M.empty ? '-' : String(M.active.length), state: M.state }; },
  busy() { return (!!demo && timers.length > 0) || (HOV === 'desk' && NOW_T() - bellT < 0.4) || Math.abs(lit - (HOV === 'desk' && M.yoursN ? 1 : M.yoursN ? 0.55 : 0.3)) > 0.02; },
  key(e) {
    if (!demo) return;
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= DEMO_STATES.length) { demo.state = DEMO_STATES[n - 1]; setup(); runDemo(); }
  },
  seen() { // what the room shows now, for tests
    return { sample: M.sample, state: M.state, active: M.active.length, paused: M.paused.length, closed: M.closed.length,
      bays: LAY.bays.map(b => ({ id: b.p.id, status: b.p.status, state: b.p.state, agent: b.p.agent, private: b.p.private,
        planks: b.cells ? [b.laid, b.cells.length] : null, tarp: b.p.status === 'paused' && b.p.exists, dust: b.p.status === 'active' && b.p.state === 'missing',
        flag: b.p.status === 'active' && b.p.state === 'error', shipwright: atWork(b.p), leadNote: !!b.leadNote })),
      moreBay: LAY.more ? LAY.more.rest.length : 0, boards: LAY.boards.length, moreBoards: LAY.moreC, desk: M.yoursN, list: listShown,
      notice: !!NOTICE, hov: HOV, caption, enters, updates, quay: QUAY ? QUAY.cells : null };
  },
});
})();
