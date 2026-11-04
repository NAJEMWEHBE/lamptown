'use strict';
/* LAMPTOWN: the pixel town. Drawn at 400x225 native pixels (kit.js), scaled to fit at a whole-number scale.
   Six fixed plots; lamptown.toml says which building (label + room type) stands on each. A plot with no
   building is a dark empty lot. Everything that moves stands for real data (TRUTH below): a quiet setup is
   a still town. The clock-driven parts are only the sky, the lights and a due bell. */
const qs = new URLSearchParams(location.search);

/* ---------- the data ----------
   Unreadable data never turns into made-up numbers: every plot goes dark and a sign says why.
   The only sample is the template preview: the unfilled template opened with ?preview=1, labelled SAMPLE. */
const SLOT = document.getElementById('town-data').textContent;
const PREVIEW = qs.get('preview') === '1' && SLOT.includes('%%TOWN_DATA%%');
const SAMPLE = (() => {
  const P0 = (id, plot, type, name, lamp, num, waiting) => ({ id, plot, type, name, label: name, lamp, num, waiting: waiting || [], links: [], configured: true });
  return { built: 'sample', builtIso: '', needs: 2, details: 'town-details.html', sample: true, title: 'Sample town', sky: {},
    plots: { tower: 'jobs', library: 'notes', gate: 'gate', lab: 'contest', workshop: 'skills', boathouse: 'projects' },
    parts: { jobs: P0('jobs', 'tower', 'jobs', 'Night jobs', 'green', '3'), notes: P0('notes', 'library', 'vault', 'Notebook', 'amber', '40', ['Two inbox notes to sort']),
      gate: P0('gate', 'gate', 'ledger', 'Tool gate', 'green', '6'), contest: P0('contest', 'lab', 'contest', 'Idea contest', 'green', '4'),
      skills: P0('skills', 'workshop', 'shelf', 'Skill shelf', 'green', '9'), projects: P0('projects', 'boathouse', 'harbour', 'Projects', 'amber', '3', ['Approve the plan']) },
    rooms: {
      notes: { state: 'waiting', notes: 40, inbox: { items: [{ title: 'Try a paper kite tail', from: 'projects' }, { title: 'Sort the moss notes' }] } },
      projects: { state: 'waiting', rows: [{ id: 'paper-kite', name: 'Paper Kite', status: 'active', state: 'waiting', progress: { done: 5, total: 9 }, busy: true }] },
      skills: { state: 'ok', rows: [{ name: 'tide-reader', state: 'ok', stage: 'installed' }, { name: 'wren-notes', state: 'working', stage: 'building' }] },
    } };
})();
/* town data comes inlined at build time, and later (a Watcher running) live through lamptownData(): the same check for both */
function checkData(d) {
  if (!d || typeof d !== 'object' || Array.isArray(d) || !d.parts || typeof d.parts !== 'object' || Array.isArray(d.parts)) throw new Error('it has no buildings table in it');
  if (!d.rooms || typeof d.rooms !== 'object') d.rooms = {};
  if (!d.plots || typeof d.plots !== 'object') d.plots = {};
  for (const [id, p] of Object.entries(d.parts)) if (p && p.plot && !d.plots[p.plot]) d.plots[p.plot] = id;
  return d;
}
let D, DATA_ERR = '';
try {
  D = checkData(JSON.parse(SLOT));
} catch (e) {
  if (PREVIEW) D = SAMPLE;
  else {
    DATA_ERR = SLOT.includes('%%TOWN_DATA%%') ? 'THIS IS THE EMPTY TEMPLATE: RUN LAMPTOWN BUILD (OR ADD ?PREVIEW=1 FOR A SAMPLE)'
      : String(e && e.message || e).toUpperCase().replace(/[{}"\\]/g, m => ({ '{': '(', '}': ')', '"': "'", '\\': '/' })[m]).slice(0, 120);   // the pixel font has no braces
    D = checkData({ built: '', needs: 0, details: 'town-details.html', parts: {}, plots: {}, rooms: {}, unreadable: DATA_ERR });
  }
}
let PT = D.parts, TITLE = '';
function setTitle() { TITLE = String(D.title || 'Lamptown'); document.title = TITLE; document.getElementById('town-h').textContent = TITLE; }
setTitle();

/* the clock: the real one; a --now build counts from its pretend build time; ?at=<ISO time> pretends (tests, screenshots) */
const AT_MS = (() => { const s = qs.get('at'); if (!s) return null; const d = new Date(s); return isNaN(d) ? null : d.getTime(); })();
const PAGE_T0 = performance.now();
const clockNow = () => (AT_MS !== null ? AT_MS : D.fixedClock && D.builtIso ? new Date(D.builtIso).getTime() : Date.now());
const nowMs = () => clockNow() + (AT_MS !== null || D.fixedClock ? performance.now() - PAGE_T0 : 0);
const isApp = () => !!window.pywebview;   // inside the desktop app: always animate, label as live
const main = document.getElementById('c').getContext('2d');
g = main;
window.onerror = (m, s, l) => { const pre = document.createElement('pre');
  pre.style.cssText = 'position:fixed;top:0;left:0;color:#f66;font:20px sans-serif;z-index:9;margin:4px';
  pre.textContent = m + ' line ' + l; document.body.appendChild(pre); };

/* ---------- the map: six fixed plots. Native pixels: click box, lamp (x, y, post), lights-off box,
   the door the camera dives through, the footprint (shadows), the lot, the name plate (centre x, y, max width) ---------- */
const PLOT = {
  tower: { native: 'jobs', hit: [6, 0, 72, 91], lamp: [42, 2, 0], dark: [27, 4, 30, 76], door: [42, 73], foot: [30, 21, 24, 58], lot: [24, 50, 36, 30], plate: [42, 81, 80], word: 'clock tower' },
  library: { native: 'vault', hit: [146, 4, 116, 72], lamp: [246, 11, 6], dark: [147, 17, 106, 55], door: [200, 61], foot: [148, 18, 104, 52], lot: [150, 34, 100, 38], plate: [200, 37, 100], word: 'domed library' },
  gate: { native: 'ledger', hit: [314, 20, 78, 54], lamp: [322, 27, 6], dark: [317, 33, 72, 40], door: [348, 67], foot: [318, 36, 70, 36], lot: [318, 42, 70, 32], plate: [352, 48, 88], word: 'gatehouse' },
  lab: { native: 'contest', hit: [24, 91, 88, 54], lamp: [34, 93, 6], side: true, dark: [29, 90, 80, 53], door: [87, 134], foot: [30, 100, 78, 42], lot: [30, 108, 78, 36], plate: [69, 116, 80], word: 'lab' },
  workshop: { native: 'shelf', hit: [264, 88, 84, 58], lamp: [339, 93, 6], dark: [267, 99, 78, 46], door: [307, 136], foot: [268, 100, 76, 44], lot: [268, 108, 76, 38], plate: [306, 116, 80], word: 'workshop' },
  boathouse: { native: 'harbour', hit: [152, 120, 94, 98], lamp: [172, 139, 6], dark: [165, 131, 78, 56], door: [204, 180], foot: [166, 146, 76, 40], lot: [166, 152, 76, 36], plate: [204, 162, 80], word: 'boathouse' },
};
const ORDER = ['tower', 'library', 'gate', 'lab', 'workshop', 'boathouse'];
/* every place a building can stand: the six plots, plus places off the plot grid (the lighthouse) added with addPlace().
   Only the six plots (ORDER) get empty lots, name plates and lamps here, and count for "every building is green". */
const PLACES = Object.assign({}, PLOT);
const bidOn = plot => { const id = D.plots[plot]; return typeof id === 'string' && PT[id] && typeof PT[id] === 'object' ? id : null; };
const partOn = plot => { const id = bidOn(plot); return id ? PT[id] : null; };
const built = plot => !!bidOn(plot);
const lampOn = plot => { const p = partOn(plot); return p ? (['green', 'amber', 'red', 'grey'].includes(p.lamp) ? p.lamp : 'grey') : null; };
const live = plot => { const l = lampOn(plot); return !!l && l !== 'grey'; };
const isPlace = plot => typeof plot === 'string' && Object.prototype.hasOwnProperty.call(PLACES, plot);
const nativeOn = plot => { const p = partOn(plot); return !!p && isPlace(plot) && p.type === PLACES[plot].native; };
const nameOn = plot => { const p = partOn(plot); return p ? String(p.name || p.label || p.id).toUpperCase() : 'EMPTY LOT'; };
const plotOf = id => (PT[id] && isPlace(PT[id].plot) ? PT[id].plot : null);   // the plot (or other place) a building stands on

/* ---------- paths (belts + footpaths) ---------- */
function mkPath(pts) {
  const segs = []; let L = 0;
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1]; const l = Math.abs(x1 - x0) + Math.abs(y1 - y0);
    segs.push({ x0, y0, x1, y1, l, s: L }); L += l;
  }
  return { pts, segs, L };
}
function at(p, s) {
  s = Math.max(0, Math.min(p.L, s));
  for (const sg of p.segs) if (s <= sg.s + sg.l) {
    const u = sg.l ? (s - sg.s) / sg.l : 0;
    return { x: sg.x0 + (sg.x1 - sg.x0) * u, y: sg.y0 + (sg.y1 - sg.y0) * u, dx: Math.sign(sg.x1 - sg.x0), dy: Math.sign(sg.y1 - sg.y0) };
  }
  const sg = p.segs[p.segs.length - 1]; return { x: sg.x1, y: sg.y1, dx: Math.sign(sg.x1 - sg.x0), dy: Math.sign(sg.y1 - sg.y0) };
}

/* footpaths from each plot to the library door (199,74): an untriaged inbox note walks one of them (see TRUTH) */
const ROUTES = [
  { id: 'tower', pts: [[56, 75], [84, 75], [84, 94], [199, 94], [199, 74]], shirt: '#c79bff' },
  { id: 'lab', pts: [[69, 100], [69, 94], [199, 94], [199, 74]], shirt: '#5fe0c0' },
  { id: 'workshop', pts: [[306, 100], [306, 94], [199, 94], [199, 74]], shirt: '#ff9a6a' },
  { id: 'gate', pts: [[318, 68], [296, 68], [296, 94], [199, 94], [199, 74]], shirt: '#7fb8ff' },
  { id: 'boathouse', pts: [[248, 150], [248, 94], [199, 94], [199, 74]], shirt: '#ff6b7a' },
].map(r => Object.assign(r, { path: mkPath(r.pts) }));
const ROUTE = Object.fromEntries(ROUTES.map(r => [r.id, r]));
const QUEUE_Y = [77, 83, 89, 95, 101, 107];   // where waiting notes stand, front first: the stem from the library door down to the fountain

/* belts: each carries real things only (see TRUTH); a belt with nothing on it stands still. It belongs to the plot it
   starts from (no building there: no belt). slot0: where the front item waits; gap: the spacing behind it; cap: items that fit */
const BELTS = {
  A: { from: 'gate', path: mkPath([[404, 26], [366, 26], [366, 128], [344, 128]]), v: 9, slot0: 42, gap: 7, cap: 4, what: 'NEW TOOLS INTO THE GATE' },
  B: { from: 'workshop', path: mkPath([[272, 144], [272, 172], [242, 172]]), v: 7, slot0: 54, gap: 7, cap: 6, what: 'ITEMS BEING BUILT OR STAGED' },
  C: { from: 'lab', path: mkPath([[108, 132], [134, 132], [134, 172], [166, 172]]), v: 7, slot0: 94, gap: 8, cap: 8, what: 'CONTEST RESULTS WAITING FOR YOU' },
};
const beltOn = k => built(BELTS[k].from);
const beltName = k => 'BELT ' + k + ': ' + BELTS[k].what + ' (' + nameOn(BELTS[k].from) + ')';

const NOTE = ['#ffd36e', '#ff8a9a', '#7fd8ff', '#b8f07a', '#d9d4ff', '#c79bff', '#ffb36b'];
function shoreY(x) { return 199 + Math.round(1.6 * Math.sin(x * 0.09) + 1.3 * Math.sin(x * 0.031 + 2)); }
const TREES = [[100, 40, 6], [121, 62, 5], [112, 18, 5], [268, 42, 5], [281, 60, 4], [300, 18, 5], [12, 40, 5], [10, 64, 4],
  [14, 128, 5], [16, 162, 6], [38, 178, 5], [84, 182, 5], [110, 166, 4], [146, 114, 4], [261, 126, 4],
  [302, 184, 6], [330, 168, 5], [360, 186, 6], [388, 162, 5], [374, 146, 4], [388, 100, 5], [392, 128, 4], [150, 136, 3]];

/* ---------- TRUTH: what is really happening, read from the rooms' data (D.rooms): once at load, again on every
   live data swap (applyData). Every moving thing in town is drawn from this; nothing happening = nothing moves.
   A plot's own animations follow its data only when the building on it has the plot's native room type
   (a custom room on a plot keeps lamp + plate). ---------- */
const STC = { ok: '#3ddc7a', waiting: '#ffb020', working: '#36d3e6', error: '#ff4a4a', missing: '#4a4e62' };   // = ST[state].lamp
const STWORD = { ok: 'RUNNING FINE', waiting: 'NEEDS YOU', working: 'RUNNING NOW', error: 'SOMETHING IS WRONG', missing: "CAN'T SEE IT" };
const SOFL = { green: 'ok', amber: 'waiting', red: 'error', grey: 'missing' };
const WALK_CAP = 6, WALK_V = 9;
function computeTruth() {
  const arr = v => (Array.isArray(v) ? v : []).filter(x => x && typeof x === 'object');
  const obj = v => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
  const str = (v, n) => String(v == null ? '' : v).replace(/\s+/g, ' ').trim().slice(0, n || 120);
  const slug = s => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  const roomOn = plot => (nativeOn(plot) ? obj(D.rooms[bidOn(plot)]) : {});
  const jobs = roomOn('tower'), vault = roomOn('library'), ledger = roomOn('gate'), contest = roomOn('lab'), shelf = roomOn('workshop'), harbour = roomOn('boathouse');
  const projects = arr(harbour.rows);
  // walkers: the vault inbox's untriaged notes, each from the building (or harbour project) it names in `from`, else the tower
  const projKeys = new Set(projects.flatMap(p => [slug(p.id), slug(p.name)]).filter(Boolean));
  const fromOf = tg => {
    const k = slug(tg); if (!k) return null;
    for (const plot of ORDER) { const p = partOn(plot); if (p && plot !== 'library' && (slug(p.id) === k || slug(p.name) === k)) return plot; }
    return projKeys.has(k) && built('boathouse') ? 'boathouse' : null;
  };
  const inbox = obj(vault.inbox);
  const notes = arr(inbox.items).filter(i => i.triaged !== true).map((i, k) => {
    const f = fromOf(i.from);
    return { n: k + 1, title: str(i.title, 90) || 'UNTITLED NOTE', target: str(i.from, 40), from: f || 'tower', known: !!f };
  });
  for (let k = notes.length; k < Math.min(40, parseInt(inbox.untriaged, 10) || 0); k++) notes.push({ n: k + 1, title: 'INBOX NOTE ' + (k + 1), target: '', from: 'tower', known: false });
  const folders = arr(vault.folders);
  const noteCount = Number.isInteger(vault.notes) ? vault.notes : folders.reduce((s, f) => s + (parseInt(f.n, 10) || 0), 0);
  // belt A: tools at the gate. Red = stopped (state error); amber tag = scanned, waiting for your call; in the scanner = working
  const lrows = arr(ledger.rows);
  const tool = r => ({ name: str(r.tool || r.name, 40) || '?', why: str(r.result, 90) });
  const gate = lrows.filter(r => r.state === 'error').map(r => Object.assign(tool(r), { red: true }))
    .concat(lrows.filter(r => r.state === 'waiting').map(tool));
  const scanning = lrows.filter(r => r.state === 'working').map(tool);
  // belt B: shelf items being built or staged; the workshop's tools: the installed ones
  const srows = arr(shelf.rows), staged = srows.some(r => 'stage' in r);
  const sk = srows.filter(s => s.stage === 'staged' || s.stage === 'building').map(s => ({ name: str(s.name, 40) || '?', kind: s.stage }));
  const installed = staged ? srows.filter(r => r.stage === 'installed').length : srows.filter(r => r.state === 'ok').length;
  // the lab: bubbles while judges are still out; belt C: unsealed entries while the result waits for your pick; trophy: a pick
  const entries = arr(contest.entries), judges = obj(contest.judges), cname = str(contest.title, 60) || 'THE CONTEST';
  const exp = parseInt(judges.expected, 10) || 0, rec = parseInt(judges.received, 10) || 0;
  const judging = entries.length && exp > 0 && rec < exp ? [{ name: cname, say: rec + ' OF ' + exp + ' JUDGES IN' }] : [];
  const pick = str(contest.pick, 60);
  let picks = [];
  if (!pick && !judging.length && contest.state === 'waiting') {
    picks = entries.filter(e => e.sealed === false).map(e => ({ name: str(e.name || e.code, 40) || '?' }));
    if (!picks.length && contest.winner) picks = [{ name: str(contest.winner, 40) }];
  }
  // the bell: a job running now; the clock hands: the soonest next run
  const jrows = arr(jobs.rows);
  const job = j => ({ id: str(j.id, 40), name: str(j.name, 40) || 'A JOB', feeds: str(j.feeds, 40) });
  const running = jrows.filter(j => j.state === 'working').map(job);
  const due = jrows.map(j => Object.assign(job(j), { at: new Date(j.next).getTime() })).filter(j => !isNaN(j.at)).sort((a, b) => a.at - b.at);
  // the shipwright: the project on the slip (the first active one with progress) is busy
  const resting = new Set(['paused', 'parked', 'done', 'closed', 'archived']);   // other status text (an agent's message) counts as active
  const hull = projects.find(p => !resting.has(p.status) && p.progress && (parseInt(p.progress.total, 10) || 0) > 0) || null;
  const ledgerState = STC[ledger.state] ? ledger.state : SOFL[lampOn('gate')] || 'missing';
  const builtPlots = ORDER.filter(built);
  return {
    notes, walkers: notes.slice(0, WALK_CAP), more: Math.max(0, notes.length - WALK_CAP), inboxDate: str(inbox.date, 16), noteCount,
    gate, scanning, items: sk, installed, picks, judging, picked: pick, running, due, reviewRunning: vault.state === 'working',
    hull: hull ? { name: str(hull.name, 40) || '?', busy: hull.busy === true, done: parseInt(hull.progress.done, 10) || 0, total: parseInt(hull.progress.total, 10) || 0 } : null,
    ledgerState, ledgerRed: lrows.filter(r => r.state === 'error').length,
    allGreen: !DATA_ERR && !D.sample && builtPlots.length > 0 && builtPlots.every(p => lampOn(p) === 'green'), builtCount: builtPlots.length,
  };
}
let TRUTH = computeTruth();
/* the bell rings while a job runs, and in the minute a job is due */
function bellNow(ms) {
  if (TRUTH.running.length) return { job: TRUTH.running[0], why: 'running' };
  for (const j of TRUTH.due) if (ms >= j.at && ms < j.at + 6e4) return { job: j, why: 'due' };
  return null;
}
function nextDue(ms) { return TRUTH.due.find(j => j.at >= ms - 6e4) || null; }

/* ---------- SKY: the real local clock. Sun times for [town] latitude / longitude (default: a mid latitude and the
   longitude of your UTC offset), the moon's phase from the mean synodic month. Computed here, no network. ---------- */
const TWI = 40 * 6e4;   // dawn and dusk: 40 min either side of sunrise and sunset
let SKYD = {}, SKY_LAT = 40, SKY_PLACE = '';
const skyLon = d0 => (Number.isFinite(SKYD.lon) ? SKYD.lon : -d0.getTimezoneOffset() / 4);
const SYNODIC = 29.530588853, NEW_MOON_2000 = Date.UTC(2000, 0, 6, 18, 14);
function moonPhase(ms) {
  const cyc = ((((ms - NEW_MOON_2000) / 864e5) / SYNODIC) % 1 + 1) % 1, illum = (1 - Math.cos(2 * Math.PI * cyc)) / 2;
  const name = cyc < 0.0339 || cyc >= 0.9661 ? 'NEW MOON' : cyc < 0.216 ? 'WAXING CRESCENT' : cyc < 0.284 ? 'FIRST QUARTER' : cyc < 0.466 ? 'WAXING GIBBOUS'
    : cyc < 0.534 ? 'FULL MOON' : cyc < 0.716 ? 'WANING GIBBOUS' : cyc < 0.784 ? 'LAST QUARTER' : 'WANING CRESCENT';
  return { cycle: cyc, age: cyc * SYNODIC, illum, waxing: cyc < 0.5, name };
}
function sunDay(ms) {   // sunrise, solar noon, sunset (ms) of the local day holding ms: NOAA's short formulas
  const d = new Date(ms), d0 = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const n = Math.round((d0 - new Date(d.getFullYear(), 0, 1)) / 864e5) + 1, gm = 2 * Math.PI / 365 * (n - 1), C = Math.cos, S = Math.sin;
  const eot = 229.18 * (0.000075 + 0.001868 * C(gm) - 0.032077 * S(gm) - 0.014615 * C(2 * gm) - 0.040849 * S(2 * gm));
  const dec = 0.006918 - 0.399912 * C(gm) + 0.070257 * S(gm) - 0.006758 * C(2 * gm) + 0.000907 * S(2 * gm) - 0.002697 * C(3 * gm) + 0.00148 * S(3 * gm);
  const lat = SKY_LAT * Math.PI / 180, c = C(90.833 * Math.PI / 180) / (C(lat) * C(dec)) - Math.tan(lat) * Math.tan(dec);
  const ha = Math.acos(Math.max(-1, Math.min(1, c))) * 180 / Math.PI, noon = 720 - 4 * skyLon(d0) - eot - d0.getTimezoneOffset();
  return { rise: d0.getTime() + (noon - 4 * ha) * 6e4, noon: d0.getTime() + noon * 6e4, set: d0.getTime() + (noon + 4 * ha) * 6e4 };
}
let SKYC = { key: -1 };
function readSky() {   // from D.sky: at load and on a live data swap
  SKYD = D.sky && typeof D.sky === 'object' ? D.sky : {};
  SKY_LAT = Number.isFinite(SKYD.lat) ? SKYD.lat : 40;
  SKY_PLACE = Number.isFinite(SKYD.lat) && Number.isFinite(SKYD.lon)
    ? Math.abs(SKYD.lat).toFixed(1) + (SKYD.lat >= 0 ? 'N ' : 'S ') + Math.abs(SKYD.lon).toFixed(1) + (SKYD.lon >= 0 ? 'E' : 'W')
    : 'APPROXIMATE: SET LATITUDE AND LONGITUDE IN LAMPTOWN.TOML';
  SKYC = { key: -1 };
}
readSky();
function skyAt(ms) {   // cached per minute
  const key = Math.floor(ms / 6e4); if (SKYC.key === key) return SKYC;
  const s = sunDay(ms), m = moonPhase(ms);
  const phase = ms < s.rise - TWI || ms >= s.set + TWI ? 'night' : ms < s.rise + TWI ? 'dawn' : ms < s.set - TWI ? 'day' : 'dusk';
  // the moon crosses the south about cycle x 24 h after the sun does (a full moon at midnight) and is up about 6 h either side
  const ha = ((((ms - s.noon) / 36e5 - m.cycle * 24) % 24) + 36) % 24 - 12;
  const hm = d => { const x = new Date(d); return pad2(x.getHours()) + ':' + pad2(x.getMinutes()); };
  SKYC = { key, ms, phase, lights: phase !== 'day', sun: s, sunU: (ms - s.rise) / (s.set - s.rise), moon: m, moonHA: ha, moonUp: Math.abs(ha) < 6,
    hhmm: hm(ms), rise: hm(s.rise), set: hm(s.set) };
  return SKYC;
}

/* ---------- static background (ground, paths, lots, shadows, trees) ---------- */
/* the ground's colours by the time of day: night is the original palette; day is daylight, not a brightened night;
   dawn and dusk sit between them (the grade in draw() warms them) */
const PAL = {
  night: { g1: '#1d2c3e', g0: '#1a2737', gs: '#253a4c', gd: '#141e2c', gp: '#2a4352', pe: '#1d1f33', pg: '#212339', pa: '#363a5c', pb: '#2e3150',
    sh0: '#5d4d6c', sh1: '#4b3f5d', sr: '#22517a', sm0: '#173c62', sm1: '#153759', sd0: '#112d4e', sd1: '#102a48',
    tk: '#3a2633', t1: '#163a3c', t2: '#1d4c4c', t3: '#276260', th: '#35786f', shadow: 'rgba(3,4,14,0.5)',
    l0: '#211d2b', l1: '#272233', l2: '#1a1723', le: '#120f19', lw: '#2a3a3a', ls: '#5a4636', lr: '#6e6450' },
  day: { g1: '#4f8a5e', g0: '#477f56', gs: '#5f9a68', gd: '#3d6e4c', gp: '#58925f', pe: '#5c5a62', pg: '#7c7a84', pa: '#a7a2a6', pb: '#97929a',
    sh0: '#dcc896', sh1: '#ccb684', sr: '#7cc4e4', sm0: '#3f94c8', sm1: '#3a8cc0', sd0: '#2f7cb4', sd1: '#2c76ac',
    tk: '#5a3a2e', t1: '#1f5a3e', t2: '#2a704a', t3: '#3a8a58', th: '#5aa46a', shadow: 'rgba(14,24,34,0.4)',
    l0: '#6e5f4c', l1: '#7a6a55', l2: '#635545', le: '#4a3f33', lw: '#5f8a4e', ls: '#8a6448', lr: '#c8b88a' },
};
PAL.dawn = PAL.dusk = Object.fromEntries(Object.keys(PAL.night).map(k => [k, k === 'shadow' ? 'rgba(8,10,24,0.45)' : mixHex(PAL.night[k], PAL.day[k], 0.42)]));
function tree(x, y, r, pal) {
  R(x - r + 2, y + r - 1, r * 2, 2, 'rgba(0,0,0,0.4)');
  R(x - 1, y + r - 2, 2, 3, pal.tk);
  disc(x, y, r + 1, OL); disc(x, y, r, pal.t1); disc(x - 1, y - 1, r - 1, pal.t2);
  if (r > 3) disc(x - 2, y - 2, r - 3, pal.t3);
  for (let i = 0; i < r * 3; i++) { const a = hash(x, y, i) * 6.28, d = hash(y, x, i) * (r - 1);
    const px = Math.round(x + Math.cos(a) * d), py = Math.round(y + Math.sin(a) * d);
    if (px + py < x + y) P(px, py, pal.th); }
}
/* an empty lot: packed earth behind four stakes and a sagging string, a few weeds, a blank sign board */
function lot(plot, pal) {
  const [x, y, w, h] = PLOT[plot].lot;
  R(x - 1, y - 1, w + 2, h + 2, pal.le);
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) { const v = hash(x + i, y + j, 21); P(x + i, y + j, v < 0.1 ? pal.l2 : v > 0.9 ? pal.l1 : pal.l0); }
  for (let i = 0; i < Math.round(w * h / 90); i++) { const wx = x + 2 + Math.floor(hash(i, x, 22) * (w - 4)), wy = y + 2 + Math.floor(hash(y, i, 23) * (h - 4));
    P(wx, wy, pal.lw); P(wx + 1, wy - 1, pal.lw); }
  const stakes = [[x + 1, y + 1], [x + w - 2, y + 1], [x + 1, y + h - 2], [x + w - 2, y + h - 2]];
  for (const [sx, sy] of stakes) { R(sx, sy - 3, 1, 4, pal.ls); P(sx, sy - 3, pal.lr); }
  for (let i = x + 2; i < x + w - 2; i += 2) { P(i, y - 1 + (Math.abs(i - x - w / 2) < w / 4 ? 1 : 0), pal.lr); P(i, y + h - 3 + (Math.abs(i - x - w / 2) < w / 4 ? 1 : 0), pal.lr); }
  const bx = x + 4, by = y + h - 12;
  R(bx + 3, by + 4, 1, 6, pal.ls); R(bx, by, 8, 5, OL); R(bx + 1, by + 1, 6, 3, pal.lr);
}
const BGS = {};
function bgFor(phase) { return BGS[phase] || (BGS[phase] = buildBG(PAL[phase] || PAL.night)); }
function buildBG(pal) {
  const c = document.createElement('canvas'); c.width = W; c.height = H; const cx = c.getContext('2d');
  const id = cx.createImageData(W, H), d = id.data, mask = new Uint8Array(W * H);
  const mr = (x0, y0, x1, y1) => { for (let y = Math.max(0, y0); y <= Math.min(H - 1, y1); y++) for (let x = Math.max(0, x0); x <= Math.min(W - 1, x1); x++) mask[y * W + x] = 1; };
  for (const r of ROUTES) for (let i = 0; i < r.pts.length - 1; i++) { const [a, b] = r.pts[i], [e, f] = r.pts[i + 1]; mr(Math.min(a, e) - 2, Math.min(b, f) - 2, Math.max(a, e) + 2, Math.max(b, f) + 2); }
  mr(197, 74, 201, 108); mr(186, 70, 213, 77);
  for (let y = 104; y <= 132; y++) for (let x = 178; x <= 222; x++) { const u = (x - 200) / 21, v = (y - 118) / 12; if (u * u + v * v <= 1) mask[y * W + x] = 1; }
  const cache = {}; const rgb = h => cache[h] || (cache[h] = hexRGB(h));
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let col; const sy = shoreY(x), h = hash(x, y, 1);
    if (y >= sy) { const dp = y - sy; col = dp < 2 ? pal.sr : dp < 8 ? (h < 0.5 ? pal.sm0 : pal.sm1) : (h < 0.5 ? pal.sd0 : pal.sd1); }
    else if (y >= sy - 3) col = h < 0.25 ? pal.sh0 : pal.sh1;
    else if (mask[y * W + x]) {
      const edge = !mask[y * W + x - 1] || !mask[y * W + x + 1] || !mask[(y - 1) * W + x] || !mask[(y + 1) * W + x];
      if (edge) col = pal.pe;
      else if (y % 3 === 2 || (x + (Math.floor(y / 3) % 2) * 2) % 4 === 3) col = pal.pg;
      else col = h < 0.3 ? pal.pa : pal.pb;
    } else {
      const v = Math.sin(x * 0.05) + Math.cos(y * 0.07) + Math.sin((x + y) * 0.03);
      col = v > 0.9 ? pal.g1 : pal.g0;
      if (h < 0.05) col = pal.gs; else if (h > 0.965) col = pal.gd;
      if (hash(x >> 2, y >> 2, 9) < 0.06 && h < 0.4) col = pal.gp;
    }
    const [r0, g0, b0] = rgb(col), k = (y * W + x) * 4; d[k] = r0; d[k + 1] = g0; d[k + 2] = b0; d[k + 3] = 255;
  }
  cx.putImageData(id, 0, 0);
  const old = g; g = cx;
  for (const plot of ORDER) {
    if (!built(plot)) { lot(plot, pal); continue; }
    const [x, y, w, h] = PLOT[plot].foot; R(x + w + 1, y + 3, 4, h, pal.shadow); R(x + 3, y + h + 1, w + 2, 3, pal.shadow);
  }
  for (const [x, y, r] of TREES) tree(x, y, r, pal);
  g = old; return c;
}

/* ---------- glow sprites ---------- */
const GL = { green: mkGlow(8, 8, '#3dff8a', 0.32), amber: mkGlow(12, 12, '#ff9a1f', 0.45), red: mkGlow(12, 12, '#ff3a3a', 0.45), warm: mkGlow(14, 6, '#ffb45a', 0.16),
  cool: mkGlow(12, 6, '#9fd4ff', 0.14), gold: mkGlow(4, 4, '#ffd36e', 0.5), lilac: mkGlow(16, 10, '#c79bff', 0.3), cyan: mkGlow(8, 6, '#6fffe0', 0.25),
  moon: mkGlow(12, 7, '#dfe8ff', 0.2), sun: mkGlow(18, 9, '#fff2b8', 0.4), low: mkGlow(18, 9, '#ff9a4a', 0.45),
  vred: mkGlow(5, 5, '#ff3a3a', 0.4), vamber: mkGlow(5, 5, '#ff9a1f', 0.4), vcyan: mkGlow(5, 5, '#6fffe0', 0.4), vgreen: mkGlow(5, 5, '#3dff8a', 0.35) };
/* town tooltips: every moving thing (and the still ones that explain the state) registers a box here each frame */
let TIPS = [], TIPS_BACK = [];   // TIPS first (small things), then TIPS_BACK (belts, the sea: big areas)
const TH = { x: -1, y: -1 };
function tipBox(box, lines, back) { (back ? TIPS_BACK : TIPS).push({ box, tip: lines }); }
/* what the last frame really drew (the town truth tests read this) */
let SEEN = {};
/* lights: windows glow at night (and at dawn and dusk) by the real clock; by day they are glass */
let LIT = true;
function lit(on, off, day) { return on ? (LIT ? '#ffcf6b' : (day || '#8fb6d2')) : off; }

/* name plate: dark enamel sign with light letters and one coloured number. An empty lot's plate is unlit. */
const NUMCOL = { green: '#8fe3ff', amber: '#ffc24a', red: '#ff7a7a', grey: '#7a7f99' };
function plate(plot) {
  const [cx, y, maxW] = PLOT[plot].plate, p = partOn(plot), lamp = lampOn(plot);
  const num = !p ? '' : lamp === 'grey' ? '-' : String(p.num || '');
  const nw = num ? textW(num) : 0, gap = num ? 7 : 0;
  const name = fitText(nameOn(plot), maxW - 8 - gap - nw), tw = textW(name);
  if (SEEN.plates) SEEN.plates[plot] = { name, num };
  const w = tw + gap + nw + 8, x = Math.max(1, Math.min(W - w - 1, Math.round(cx - w / 2)));
  R(x, y, w, 9, '#080a16');
  R(x + 1, y + 1, w - 2, 7, p ? '#161b38' : '#14141c');
  R(x + 1, y + 1, w - 2, 1, p ? '#2b336a' : '#24242e');
  if (p) { P(x + 1, y + 1, '#59629a'); P(x + w - 2, y + 1, '#59629a'); }
  text(name, x + 4, y + 2, !p ? '#6a6e84' : lamp === 'grey' ? '#a9aec8' : '#eef1ff');
  if (num) { R(x + 4 + tw + 3, y + 2, 1, 5, '#323a72'); text(num, x + 4 + tw + gap, y + 2, NUMCOL[lamp]); }
}

/* ---------- belts ---------- */
/* a belt's surface; it only moves while something real rides it (b.off: how far it has run). Cached while still. */
function drawBelt(b) {
  if (!b.cv || b.cvOff !== b.off) { b.cvOff = b.off; b.cv = layer(W, H, () => {
    const p = b.path;
    for (let i = 1; i < p.pts.length - 1; i++) { const [x, y] = p.pts[i]; R(x - 3, y - 3, 7, 7, '#3a3e5e'); }
    for (let d = 0; d <= p.L; d++) {
      const q = at(p, d), x = Math.round(q.x), y = Math.round(q.y);
      const st = (((d - b.off) % 4) + 4) % 4 < 1, surf = st ? '#262941' : '#3a3e5e';
      if (q.dy === 0) { R(x, y - 3, 1, 1, '#6a7098'); R(x, y - 2, 1, 5, surf); R(x, y + 3, 1, 1, '#14172a'); R(x, y + 4, 1, 1, 'rgba(0,0,0,0.35)'); if (x % 8 === 0) R(x, y + 4, 1, 2, '#24273d'); }
      else { R(x - 3, y, 1, 1, '#6a7098'); R(x - 2, y, 5, 1, surf); R(x + 3, y, 1, 1, '#4e5378'); R(x + 4, y, 1, 1, 'rgba(0,0,0,0.35)'); }
    }
  }); }
  g.drawImage(b.cv, 0, 0);
}
/* real items ride in once from the belt's start (one behind the other) and wait at their slots; then the belt stops.
   list: [{kind, slot, tip}] front first. Still mode: everything already at its slot. */
const T_START = 5.6;   // the frame clock's value when the page starts (OFF)
function beltState(b, list, t, still) {
  const run = still ? 1e9 : Math.max(0, (t - T_START) * b.v);
  let end = 0; b.items = [];
  list.forEach((it, k) => {
    const off = k * b.gap, s = Math.min(it.slot, run - off); end = Math.max(end, it.slot + off);
    if (s >= 0) { const q = at(b.path, s); b.items.push(Object.assign({}, it, { x: Math.round(q.x), y: Math.round(q.y) })); }
  });
  b.off = list.length ? Math.min(run, end) : 0; b.moving = list.length > 0 && run < end;
}
function crate(it) {
  const { x, y } = it;
  if (it.kind === 'orb') { // a contest result, waiting for your pick
    R(x - 1, y - 2, 3, 4, '#ffd84a'); R(x - 2, y - 1, 5, 2, '#ffd84a'); P(x - 1, y - 2, '#fff6c0'); R(x - 1, y + 2, 3, 1, '#7a6a8a');
    glow(GL.gold, x, y, 0.8);
  } else if (it.kind === 'red') { // stopped at the gate: a red crate the gate won't take
    R(x - 3, y - 4, 7, 7, OL); R(x - 2, y - 2, 5, 4, '#b8383a'); R(x - 2, y - 3, 5, 1, '#ff6a5a'); R(x - 2, y + 1, 5, 1, '#6a1618');
    P(x, y - 2, '#ffe6d8'); P(x, y, '#ffe6d8'); glow(GL.vred, x, y, 0.7);
  } else if (it.kind === 'wait' || it.kind === 'scan') { // a tool not signed off yet: grey crate (amber tag: scanned, your call)
    R(x - 2, y - 2, 5, 4, '#5e5c7c'); R(x - 2, y - 3, 5, 1, '#8482a6'); R(x - 2, y + 1, 5, 1, '#3c3a56'); P(x, y - 1, '#c9c6ee');
    if (it.kind === 'wait') P(x + 2, y - 3, '#ffb020');
  } else if (it.kind === 'building') { // still being built: an open crate, no lid yet
    R(x - 2, y - 2, 5, 4, '#73431f'); R(x - 2, y - 2, 1, 4, '#b5733d'); R(x + 2, y - 2, 1, 4, '#b5733d'); R(x - 2, y + 1, 5, 1, '#b5733d'); P(x, y - 3, '#e3a463');
  } else { // staged: a closed crate with an amber tag (waiting for you)
    R(x - 2, y - 2, 5, 4, '#b5733d'); R(x - 2, y - 3, 5, 1, '#e3a463'); R(x - 2, y + 1, 5, 1, '#73431f'); R(x, y - 2, 1, 3, '#8a5530'); P(x + 2, y - 3, '#ffb020');
  }
}
function drawItems(b, clipY) { for (const it of b.items || []) if (clipY === undefined || it.y < clipY) crate(it); }
function moreTag(x, y, n, lines) {
  const s = '+' + n, w = textW(s) + 4;
  R(x - 1, y - 1, w + 2, 9, OL); R(x, y, w, 7, '#2a1a24'); text(s, x + 2, y + 1, '#ffd59a');
  tipBox([x - 1, y - 1, w + 2, 9], lines);
}
function beltTips(b, lines) {
  for (const sg of b.path.segs) tipBox([Math.min(sg.x0, sg.x1) - 3, Math.min(sg.y0, sg.y1) - 3, Math.abs(sg.x1 - sg.x0) + 7, Math.abs(sg.y1 - sg.y0) + 7], lines, true);
}
/* the three belts from TRUTH, once per frame */
function belts(t, still) {
  const A = BELTS.A, B = BELTS.B, C = BELTS.C, T = TRUTH, gateName = nameOn('gate');
  // A: a tool being scanned sits in the scanner (77 along); the rest queue at the gate, red ones (stopped) in front
  const la = [];
  if (T.scanning.length) la.push({ kind: 'scan', slot: 77, tip: [T.scanning[0].name, 'BEING SCANNED NOW'] });
  const q = T.scanning.slice(1).map(x => ({ kind: 'wait', tip: [x.name, 'WAITING FOR THE SCANNER'] }))
    .concat(T.gate.map(x => x.red ? { kind: 'red', tip: [x.name, 'STOPPED AT THE GATE' + (x.why ? ': ' + x.why : ''), 'THE ' + gateName + ' ROOM SAYS WHAT TO DO'] }
      : { kind: 'wait', tip: [x.name, 'SCANNED: WAITING FOR YOUR CALL'] }));
  q.slice(0, A.cap).forEach((it, i) => la.push(Object.assign(it, { slot: A.slot0 - i * A.gap })));
  beltState(A, beltOn('A') ? la : [], t, still);
  // B: items being built (open crates) or staged (closed, amber tag)
  const lb = T.items.slice(0, B.cap).map((s, i) => ({ kind: s.kind === 'building' ? 'building' : 'staged', slot: B.slot0 - i * B.gap,
    tip: [s.name, s.kind === 'building' ? 'BEING BUILT NOW' : 'STAGED: WAITING FOR YOU TO CHECK AND INSTALL IT'] }));
  beltState(B, beltOn('B') ? lb : [], t, still);
  // C: one orb per contest result that waits for your pick
  const lc = T.picks.slice(0, C.cap).map((p, i) => ({ kind: 'orb', slot: C.slot0 - i * C.gap, tip: [p.name, 'RESULTS ARE IN: WAITING FOR YOUR PICK'] }));
  beltState(C, beltOn('C') ? lc : [], t, still);
  SEEN.belts = { A: A.items.map(i => i.kind), B: B.items.map(i => i.kind), C: C.items.map(i => i.kind), moving: { A: A.moving, B: B.moving, C: C.moving },
    shown: { A: beltOn('A'), B: beltOn('B'), C: beltOn('C') } };
}
function beltTipsAll() {
  const T = TRUTH, A = BELTS.A, B = BELTS.B, C = BELTS.C, nA = T.gate.length + T.scanning.length;
  for (const b of [A, B, C]) for (const it of b.items) tipBox([it.x - 3, it.y - 4, 7, 7], it.tip);
  if (beltOn('A')) {
    if (nA > A.cap + (T.scanning.length ? 1 : 0)) moreTag(371, 30, nA - A.cap - (T.scanning.length ? 1 : 0), ['MORE TOOLS AT THE GATE', 'THE ' + nameOn('gate') + ' ROOM LISTS THEM ALL']);
    const red = T.gate.filter(x => x.red).length, wait = nA - red;
    beltTips(A, [beltName('A'), nA ? [red ? red + ' STOPPED' : '', wait ? wait + ' WAITING' : ''].filter(Boolean).join(', ') : 'STOPPED: NO TOOL IS WAITING AT THE GATE']);
  }
  if (beltOn('B')) {
    if (T.items.length > B.cap) moreTag(250, 176, T.items.length - B.cap, ['MORE ITEMS BEING BUILT OR STAGED', 'THE ' + nameOn('workshop') + ' ROOM LISTS THEM ALL']);
    beltTips(B, [beltName('B'), T.items.length ? plural(T.items.length, 'ITEM') + ' ON IT' : 'STOPPED: NOTHING IS BEING BUILT OR STAGED']);
  }
  if (beltOn('C')) {
    if (T.picks.length > C.cap) moreTag(138, 176, T.picks.length - C.cap, ['MORE RESULTS WAIT FOR YOU', 'THE ' + nameOn('lab') + ' ROOM LISTS THEM ALL']);
    beltTips(C, [beltName('C'), T.picks.length ? plural(T.picks.length, 'RESULT') + ' WAITING FOR YOU' : 'STOPPED: NO RESULT WAITS FOR YOUR PICK']);
  }
}

/* ---------- buildings ---------- */
function shingles(x, y, w, h, base, seam, eave) {
  R(x, y, w, h, base);
  for (let j = 0; j < h - 2; j += 3) { R(x, y + j + 2, w, 1, seam); for (let i = ((j / 3) % 2) * 3; i < w; i += 6) P(x + i, y + j, seam); }
  R(x, y + h - 2, w, 2, eave); R(x, y, w, 1, eave);
}
/* shelves fill with the number of notes in the vault (fill 0..1) */
function shelfWindow(wx, wy, ww, wh, fill) {
  R(wx - 1, wy - 2, ww + 2, wh + 3, '#1f2548');
  R(wx, wy, ww, wh, '#3d2a38'); R(wx + 1, wy - 1, ww - 2, 1, '#3d2a38');
  for (let s = 0; s < 3; s++) { const sy = wy + 4 + s * 5; R(wx, sy, ww, 1, '#8c5c3a');
    for (let i = 0; i < ww; i++) { const h = hash(wx + i, sy, 7); if (h < fill) { const c = NOTE[Math.floor(hash(wx + i, sy, 9) * NOTE.length)]; const hh = h < fill * 0.4 ? 3 : 2; R(wx + i, sy - hh, 1, hh, c); } } }
  P(wx + ww - 2, wy, 'rgba(255,255,255,0.35)'); P(wx + ww - 3, wy + 1, 'rgba(255,255,255,0.2)');
}

/* the library (plot `library`, native room: vault) */
function drawLibrary(t, flash) {
  const x = 148, w = 104, ry = 18, rd = 16, wy = 34, wh = 36, on = live('library'), nat = nativeOn('library');
  R(x - 1, ry - 1, w + 2, rd + wh + 2, OL);
  shingles(x, ry, w, rd, '#4a3a73', '#3a2d5e', '#6654a0');
  // glass reading dome on the roof
  const cx = 200, cy = 30;
  R(cx - 13, cy - 1, 27, 3, OL); R(cx - 12, cy, 25, 2, '#8a7ab8');
  for (let j = 1; j <= 10; j++) { const hw = Math.round(Math.sqrt(110 - j * j)); R(cx - hw - 1, cy - j, hw * 2 + 3, 1, OL); }
  for (let j = 1; j <= 10; j++) { const hw = Math.round(Math.sqrt(100 - j * j));
    for (let i = -hw; i <= hw; i++) { let c = i < -3 ? '#5e9fd2' : '#3a73a6'; if (i === -6 || i === 0 || i === 6) c = '#26446e'; if (j === 4 || j === 8) c = '#26446e'; P(cx + i, cy - j, c); } }
  P(cx - 5, cy - 8, '#cfe8ff'); P(cx - 6, cy - 7, '#a8d4ff');
  // notes rising in the dome: only while the vault says a review is filing notes now (state working)
  if (on && nat && TRUTH.reviewRunning) {
    for (let i = 0; i < 6; i++) { const nx = cx - 6 + i * 2 + Math.round(Math.sin(t * 1.3 + i) * 1), ny = cy - 3 - ((i * 3 + Math.floor(t * 2)) % 5); P(nx, ny, NOTE[i % NOTE.length]); }
    SEEN.dome = true; tipBox([cx - 10, cy - 10, 21, 11], ['A REVIEW IS FILING NOTES NOW', 'NOTES RISE IN THE DOME WHILE IT RUNS; STILL WHEN IT IS DONE']);
  }
  if (on && flash > 0) glow(GL.lilac, cx, cy - 4, flash);
  // wall
  R(x, wy, w, wh, '#3a4476'); R(x, wy, w, 1, '#1d2244'); R(x, wy + wh - 3, w, 3, '#2b3360'); R(x, wy + wh - 3, w, 1, '#4c5794');
  for (const px of [149, 166, 185, 212, 231, 248]) { R(px, wy + 13, 3, wh - 16, '#4c5692'); R(px, wy + 13, 1, wh - 16, '#6470b0'); R(px - 1, wy + 12, 5, 1, '#5c68a8'); }
  const notes = nat ? TRUTH.noteCount : 0;
  const fill = on ? Math.max(0.12, Math.min(0.9, 0.12 + notes / 80)) : 0.06;
  for (const wx of [153, 170, 218, 235]) shelfWindow(wx, 50, 12, 15, fill);
  if (on && nat) tipBox([152, 46, 96, 20], [nameOn('library') + ': ' + plural(notes, 'NOTE'), 'THE SHELVES FILL AS THE VAULT GROWS'], true);
  // door
  R(191, 51, 18, 19, '#161124'); R(192, 52, 16, 18, '#5e3b2a'); R(192, 52, 16, 1, '#7a5038');
  R(194, 55, 4, 5, '#4a2d20'); R(202, 55, 4, 5, '#4a2d20'); R(194, 62, 4, 6, '#4a2d20'); R(202, 62, 4, 6, '#4a2d20');
  R(199, 52, 2, 18, lit(on, '#3a2a20', '#4a2d20')); if (on && LIT) { P(198, 60, '#ffcf6b'); P(201, 60, '#ffcf6b'); }
  R(188, 70, 24, 2, '#4d5274'); R(190, 72, 20, 1, '#3a3e5c');
}

/* the clock tower (plot `tower`, native room: jobs): the hands point at the next run; its day sits under the face */
function drawTower(t, ring, still, ms) {
  const x = 30, w = 24, cx = 42, on = live('tower');
  R(x - 1, 21, w + 2, 58, OL);
  R(x, 34, w, 44, '#5b3f63');
  for (let j = 0; j < 44; j += 3) { R(x, 34 + j + 2, w, 1, '#4a3253'); for (let i = ((j / 3) % 2) * 2; i < w; i += 5) P(x + i, 34 + j, '#4a3253'); }
  R(x, 34, 2, 44, '#6e5078'); R(x + w - 2, 34, 2, 44, '#432c4c');
  // belfry with bell
  R(x, 22, w, 12, '#4a3456'); R(x + 4, 25, w - 8, 9, '#110e20'); R(x + 5, 24, w - 10, 1, '#110e20');
  const sw = ring ? (still ? 1 : Math.round(Math.sin(t * 14) * 1.4)) : 0;   // still mode: a ringing bell shown tilted
  sprite(["..#..", ".###.", ".###.", "#####", "..#.."], cx - 2 + sw, 26, { '#': on ? '#ffcf4a' : '#8a7440' }); if (on) P(cx - 1 + sw, 27, '#fff0a8');
  R(x - 2, 33, w + 4, 2, '#7d5d86'); R(x - 2, 21, w + 4, 2, '#7d5d86'); R(x - 2, 21, w + 4, 1, '#9a7aa4');
  // spire
  for (let i = 0; i < 16; i++) { const hw = Math.round(i * 14 / 15) + 1; R(cx - hw - 1, 5 + i, hw * 2 + 2, 1, OL); }
  for (let i = 0; i < 16; i++) { const hw = Math.round(i * 14 / 15); R(cx - hw, 5 + i, hw, 1, '#4a5aa0'); R(cx, 5 + i, hw + 1, 1, '#2c3670'); if (i % 4 === 3) R(cx - hw, 5 + i, hw * 2 + 1, 1, '#232c5e'); }
  // clock face: the hands only when a next run is known
  disc(cx, 48, 8, OL); disc(cx, 48, 7, '#d6a24a'); disc(cx, 48, 6, '#c3d4f2');
  P(cx, 43, '#2a2a44'); P(cx, 53, '#2a2a44'); P(cx - 5, 48, '#2a2a44'); P(cx + 5, 48, '#2a2a44');
  const nx = on && nativeOn('tower') ? nextDue(ms) : null;
  let day = '';
  if (nx) {
    const d = new Date(nx.at), hr = d.getHours(), mn = d.getMinutes();
    const mA = (mn / 60) * 6.2832, hA = (((hr % 12) + mn / 60) / 12) * 6.2832;
    for (let k = 0; k <= 5; k++) P(cx + Math.round(Math.sin(mA) * k), 48 - Math.round(Math.cos(mA) * k), '#1a1a30');
    for (let k = 0; k <= 3; k++) P(cx + Math.round(Math.sin(hA) * k), 48 - Math.round(Math.cos(hA) * k), '#7a2a3a');
    P(cx, 48, '#1a1a30'); day = DOW[d.getDay()];
    SEEN.clock = { job: nx.name, hour: hr, minute: mn, day };
    tipBox([cx - 8, 40, 17, 17], ['THE CLOCK: NEXT RUN', nx.name.toUpperCase() + ' AT ' + day + ' ' + pad2(hr) + ':' + pad2(mn), untilText(new Date(nx.at).toISOString())]);
  }
  // door + windows, then the day plaque between the windows
  R(cx - 4, 67, 8, 11, '#161124'); R(cx - 3, 68, 6, 10, '#5e3b2a'); R(cx, 68, 1, 10, '#4a2d20');
  R(x + 3, 58, 3, 5, lit(on, '#2e2438')); R(x + w - 6, 58, 3, 5, lit(on, '#2e2438'));
  if (day) { const dw = textW(day); R(cx - (dw >> 1) - 1, 57, dw + 2, 7, OL); text(day, cx - (dw >> 1), 58, on ? '#ffe28a' : '#b8a878'); }
  // signpost
  R(20, 88, 1, 5, '#3a2d3a'); R(64, 88, 1, 5, '#3a2d3a');
}

/* the lab (plot `lab`, native room: contest) */
function drawLab(t) {
  const x = 30, w = 78, ry = 100, rd = 14, wy = 114, wh = 28, on = live('lab');
  R(x - 1, ry - 1, w + 2, rd + wh + 2, OL);
  shingles(x, ry, w, rd, '#2a5a62', '#1f474f', '#3c7f84');
  // greenhouse roof panel
  R(x + 6, ry + 2, 30, 10, '#1b3046');
  for (let j = 0; j < 9; j++) for (let i = 0; i < 29; i++) if (i % 5 !== 4 && j % 4 !== 3) P(x + 6 + i, ry + 2 + j, (i + j) % 9 < 2 ? '#8fd0e8' : '#4f8fb0');
  // chimney + rising idea bubbles
  R(x + 57, ry - 9, 8, 14, OL); R(x + 58, ry - 8, 6, 13, '#6a4a5a'); R(x + 58, ry - 8, 6, 2, '#8a6a7a');
  const judging = on && TRUTH.judging.length > 0;   // bubbles only while judges are still out
  if (judging) {
    for (let i = 0; i < 5; i++) { const ph = ((t * 0.6 + i / 5) % 1), by = ry - 10 - ph * 22, bx = x + 61 + Math.sin(ph * 6 + i) * 3;
      if (by > 2) { const c = ['#ff8ac0', '#7fe8ff', '#ffd36e', '#b8f07a', '#c79bff'][i]; P(bx, by, c); if (ph < 0.6) P(bx + 1, by, c); } }
    const j = TRUTH.judging[0], tip = ['JUDGING IN PROGRESS', j.name + ': ' + j.say, 'THE BUBBLES STOP WHEN EVERY JUDGE IS IN'];
    tipBox([x + 54, ry - 33, 14, 38], tip); tipBox([x + 5, wy + 13, 40, 13], tip); SEEN.bubbles = true;
  }
  // trophy: only once a pick is recorded (a fixed glint: nothing about it changes)
  if (on && TRUTH.picked) {
    sprite(["#######", "#.###.#", ".#####.", "..###..", "...#...", "..###..", ".#####."], x + 44, ry - 5, { '#': '#ffcc4d' });
    P(x + 46, ry - 4, '#fff2a0'); R(x + 46, ry - 5, 3, 1, '#ffe38a'); P(x + 52, ry - 7, '#fff2a0');
    tipBox([x + 43, ry - 6, 9, 9], ['TROPHY', 'YOUR PICK: ' + TRUTH.picked]); SEEN.trophy = true;
  }
  // wall
  R(x, wy, w, wh, '#33506a'); R(x, wy, w, 1, '#1c2c42'); R(x, wy + wh - 2, w, 2, '#26405a');
  // lab window with flasks
  R(x + 5, wy + 13, 40, 13, '#1a2a44'); R(x + 6, wy + 14, 38, 11, '#1d3f4e'); R(x + 6, wy + 21, 38, 1, '#5a4a5a');
  const fl = [['#7cff9a', 9], ['#ff6fb0', 16], ['#ffd36e', 23], ['#7fd8ff', 30], ['#c79bff', 37]];
  for (const [c, fx] of fl) { sprite([".g.", ".g.", "gcg", "ccc"], x + fx, wy + 17, { g: '#9fd0e0', c: on ? c : '#4a5a6a' }); }
  if (judging) for (let i = 0; i < 3; i++) { const ph = (t * 0.9 + i * 0.33) % 1; P(x + 10 + i * 14, wy + 16 - Math.round(ph * 3), '#cfffe0'); }
  R(x + 6, wy + 14, 38, 1, '#2d6070');
  // door + bulb sign
  R(x + 51, wy + 11, 12, 17, '#161124'); R(x + 52, wy + 12, 10, 16, '#5e3b2a'); R(x + 56, wy + 12, 2, 16, lit(on, '#3a2a20', '#4a2d20'));
  R(x + 67, wy + 15, 7, 7, '#1a2a44'); R(x + 68, wy + 16, 5, 5, lit(on, '#2e3a4a', '#d8d0a8')); R(x + 70, wy + 16, 1, 5, '#c98a3a'); R(x + 68, wy + 18, 5, 1, '#c98a3a');
}

const TOOLS = [
  [["sss", ".h.", ".h."], { s: '#aab4d8', h: '#a0643a' }],  // hammer
  [["s.s", "sss", ".s."], { s: '#c4cce8' }],               // wrench
  [[".s.", ".r.", ".r."], { s: '#c4cce8', r: '#e05a5a' }],  // screwdriver
  [["yyy", "yyy", "h.."], { y: '#d8dde8', h: '#a0643a' }],  // saw
  [[".t.", "t.t", ".t."], { t: '#5ad1c8' }],               // gear
  [[".y.", ".y.", ".k."], { y: '#ffcf5a', k: '#3a3a4a' }],  // brush
];
/* the workshop (plot `workshop`, native room: shelf): one tool on the shelves per installed item (27 spots; more shows a tag) */
function drawWorkshop(t) {
  const x = 268, w = 76, ry = 100, rd = 14, wy = 114, wh = 30, on = live('workshop');
  R(x - 1, ry - 1, w + 2, rd + wh + 2, OL);
  R(x, ry, w, rd, '#5a6690'); for (let i = 0; i < w; i += 2) R(x + i, ry + 1, 1, rd - 3, '#48527c');
  R(x, ry, w, 1, '#7a86b4'); R(x, ry + rd - 2, w, 2, '#7a86b4');
  R(x + 30, ry + 3, 14, 5, '#2a3050'); R(x + 31, ry + 4, 12, 3, lit(on, '#3a4060')); // skylight
  R(x, wy, w, wh, '#5a3a4c'); for (let j = 0; j < wh; j += 3) { R(x, wy + j + 2, w, 1, '#4a2f40'); for (let i = ((j / 3) % 2) * 3; i < w; i += 6) P(x + i, wy + j, '#4a2f40'); }
  R(x, wy, w, 1, '#2a1c2c');
  // open roller door with shelves
  R(x + 13, wy + 13, 52, 17, '#2a1e2a'); R(x + 14, wy + 14, 50, 16, '#211b30');
  R(x + 14, wy + 14, 50, 3, '#787d9e'); R(x + 14, wy + 15, 50, 1, '#5e6386');
  R(x + 15, wy + 21, 48, 1, '#8a6040'); R(x + 15, wy + 26, 48, 1, '#8a6040');
  const n = on && nativeOn('workshop') ? TRUTH.installed : 0;
  const rows = [wy + 18, wy + 23, wy + 27], per = Math.min(9, Math.max(1, Math.ceil(n / 3)));
  const sp = Math.min(8, Math.floor(45 / per)), x0 = x + 14 + Math.floor((50 - ((per - 1) * sp + 3)) / 2);
  for (let i = 0; i < Math.min(n, rows.length * per); i++) {
    const r = Math.floor(i / per), k = i % per, tl = TOOLS[(i * 5 + r) % TOOLS.length];
    sprite(tl[0], x0 + k * sp, rows[r], tl[1]);
  }
  if (n > rows.length * per) { R(x + 58, wy + 27, 5, 3, '#ffcf6b'); P(x + 60, wy + 27, '#2a1e2a'); }
  if (n) tipBox([x + 14, wy + 14, 50, 16], [nameOn('workshop') + ': ' + plural(n, 'ITEM') + ' INSTALLED', 'ONE TOOL ON THE SHELVES PER INSTALLED ITEM'], true);
  SEEN.tools = n;
  // belt hatches
  R(x, wy + 23, 9, 7, '#140f1e'); R(x + w - 3, wy + 10, 3, 8, '#140f1e');
}

/* the gatehouse (plot `gate`, native room: ledger) */
function drawGate(t) {
  const x = 318, w = 70, ry = 36, rd = 10, wy = 46, wh = 26, on = live('gate');
  R(x - 1, ry - 3, w + 2, rd + wh + 4, OL);
  R(x, ry, w, rd, '#3e4a70'); R(x, ry, w, 1, '#5a6894'); R(x, ry + rd - 2, w, 2, '#56628e');
  for (let i = 0; i < w; i += 8) { R(x + i, ry - 2, 5, 2, '#56628e'); R(x + i, ry - 2, 5, 1, '#7280b0'); }
  R(x + 6, ry + 2, w - 12, 5, '#323c60');
  R(x, wy, w, wh, '#4d5577'); for (let j = 0; j < wh; j += 4) { R(x, wy + j + 3, w, 1, '#3d4464'); for (let i = ((j / 4) % 2) * 4; i < w; i += 8) P(x + i, wy + j, '#3d4464'); }
  R(x, wy, w, 1, '#262c48');
  R(358, 58, 17, 14, '#262b46'); R(359, 57, 15, 1, '#262b46');
  // guard window
  R(x + 6, wy + 13, 16, 10, '#262b46'); R(x + 7, wy + 14, 14, 8, on ? (LIT ? '#e0a650' : '#7ea6c4') : '#3a3a50'); R(x + 7, wy + 14, 14, 2, lit(on, '#44445c', '#a8cce0'));
  if (on) { R(x + 12, wy + 17, 3, 5, '#2a2440'); R(x + 11, wy + 16, 5, 1, '#3a6ab0'); P(x + 13, wy + 18, '#f0b58c'); }
  // the guard's light: the gate's real state colour, steady (the lamp on the roof does the blinking)
  const vs = TRUTH.ledgerState, gc = STC[vs] || STC.missing;
  R(x + 17, wy + 17, 3, 3, '#1a1a2a'); R(x + 18, wy + 18, 1, 1, gc);
  if (vs !== 'missing') glow(GL['v' + ({ ok: 'green', waiting: 'amber', working: 'cyan', error: 'red' }[vs])], x + 18, wy + 18, 0.6);
  SEEN.guard = gc;
  const vr = TRUTH.ledgerRed;
  tipBox([x + 15, wy + 15, 7, 7], ["THE GUARD'S LIGHT: " + STWORD[vs], vs === 'error' ? (vr ? plural(vr, 'TOOL') + ' STOPPED AT THE GATE (RED CRATES ON BELT A)' : "THE GATE CAN'T READ ITS RECORDS")
    : vs === 'waiting' ? 'A SCANNED TOOL WAITS FOR YOUR CALL' : vs === 'working' ? 'A SCAN IS RUNNING' : vs === 'ok' ? 'EVERY TOOL IS SIGNED OFF' : "CAN'T SEE THE GATE'S FILES"]);
  R(x + 26, wy + 15, 8, 11, '#161124'); R(x + 27, wy + 16, 6, 10, '#5e3b2a');
}
function gateOpening(t) {
  const scan = live('gate') && TRUTH.scanning.length > 0;   // the scanner sweeps only while a scan really runs
  g.save(); g.beginPath(); g.rect(359, 59, 15, 13); g.clip();
  R(359, 59, 15, 13, '#121124'); drawBelt(BELTS.A); drawItems(BELTS.A);
  if (scan) {
    const sy = 61 + Math.round((Math.sin(t * 3) * 0.5 + 0.5) * 9);
    g.fillStyle = 'rgba(110,255,225,0.14)'; g.fillRect(360, 60, 13, 12);
    R(360, sy, 13, 1, 'rgba(140,255,230,0.85)');
    R(362, 59, 9, 1, '#9ffff0');
  }
  g.restore();
  if (scan) { glow(GL.cyan, 366, 64, 0.8); SEEN.scanner = true; tipBox([359, 59, 15, 13], ['SCANNER RUNNING', TRUTH.scanning[0].name + ' IS BEING SCANNED NOW']); }
}

/* the boathouse (plot `boathouse`, native room: harbour) */
function drawBoathouse(t) {
  const x = 166, w = 76, ry = 146, rd = 14, wy = 160, wh = 26, on = live('boathouse');
  R(x - 1, ry - 1, w + 2, rd + wh + 2, OL);
  shingles(x, ry, w, rd, '#8e3a48', '#73303c', '#ad4c5a');
  R(x + 30, ry + 2, 16, 3, '#5e2632'); R(x + 31, ry + 3, 14, 1, lit(on, '#4a2a30'));
  // pennant on its pole (scenery: the sea wind)
  R(236, 132, 1, 14, '#2a2030');
  const fw = Math.round(Math.sin(t * 4) * 1);
  sprite(["tttttt.", "tqtqtt", "ttqttt.", "ttq..", "tt."], 237, 132, { t: '#3fbfb0', q: '#d8fff8' });
  P(243 + fw, 133, '#3fbfb0');
  tipBox([235, 131, 10, 8], ['PENNANT', 'SCENERY: THE SEA WIND'], true);
  R(x, wy, w, wh, '#5c4232'); for (let i = 1; i < w; i += 4) R(x + i, wy + 1, 1, wh - 1, '#4a3428'); for (let i = 3; i < w; i += 8) R(x + i, wy + 1, 1, wh - 1, '#6c4e3a');
  R(x, wy, w, 1, '#2a1c18');
  const win = lit(on, '#3a2e2a');
  R(x + 5, wy + 14, 10, 7, '#2a1c18'); R(x + 6, wy + 15, 8, 5, win); R(x + 10, wy + 15, 1, 5, '#b07a3a');
  R(x + w - 15, wy + 14, 10, 7, '#2a1c18'); R(x + w - 14, wy + 15, 8, 5, win); R(x + w - 10, wy + 15, 1, 5, '#b07a3a');
  // boat-shed doorway
  R(187, 172, 34, 14, '#2a1c18'); R(188, 173, 32, 13, '#1b1420'); R(188, 173, 32, 2, '#2e2230');
  R(203, 173, 1, 3, '#3a2d3a'); R(202, 176, 3, 2, on ? (LIT ? '#ffd36e' : '#8a7a5a') : '#4a3a30');
}
/* the hull on the slipway is planked done/total of the way; ribs show what is left */
function drawSlip(t) {
  for (let y = 186; y < 219; y++) { const un = y >= shoreY(204);
    R(194, y, 1, 1, un ? '#29405a' : '#7a6050'); R(214, y, 1, 1, un ? '#29405a' : '#7a6050');
    if (y % 3 === 0) R(195, y, 19, 1, un ? '#1b3150' : '#3e2e30'); }
  const on = live('boathouse'), hull = on && nativeOn('boathouse') ? TRUTH.hull : null;
  const frac = hull && hull.total ? Math.max(0, Math.min(1, hull.done / hull.total)) : 0;
  const cx = 204, y0 = 179, L = 31, done = Math.round(L * frac);
  for (let j = 0; j < L; j++) {
    const hw = j < 14 ? 7 : Math.max(1, Math.round(7 * (1 - (j - 14) / 17)));
    const y = y0 + j;
    if (j < done) {
      R(cx - hw - 1, y, hw * 2 + 3, 1, OL); R(cx - hw, y, hw * 2 + 1, 1, '#a5673c');
      R(cx - hw, y, 2, 1, '#c88a52'); P(cx - 3, y, '#8a5230'); P(cx + 3, y, '#8a5230'); P(cx + hw, y, '#6e4024');
      if (j === 0) R(cx - hw, y, hw * 2 + 1, 1, '#c88a52');
    } else {
      P(cx - hw - 1, y, OL); P(cx + hw + 1, y, OL); P(cx - hw, y, '#a5673c'); P(cx + hw, y, '#a5673c'); P(cx, y, '#8a5a34');
      if (j % 3 === 0) R(cx - hw, y, hw * 2 + 1, 1, '#8a5a34');
    }
  }
  SEEN.hull = hull ? { name: hull.name, planked: done } : null;
  if (hull) tipBox([cx - 8, 179, 17, 31], [hull.name + ': THE HULL ON THE SLIP', 'PLANKED ' + hull.done + ' OF ' + hull.total + ' DONE'], true);
  if (!hull || done >= L || !hull.busy) return;
  // the shipwright: only while the project on the slip is busy
  const hy = 179 + Math.max(done, 1) - 1, up = Math.sin(t * 6) > 0;
  worker(cx + 2, hy, 1, 0, false, '#ff6b7a');
  R(cx + 4, hy - 5 - (up ? 2 : 0), 1, 3, '#a0643a'); R(cx + 4, hy - 6 - (up ? 2 : 0), 2, 1, '#aab4d8');
  if (!up) { P(cx + 6, hy - 4, '#fff2a0'); P(cx + 7, hy - 6, '#ffd36e'); }
  SEEN.shipwright = true;
  tipBox([cx - 1, hy - 9, 10, 11], ['SHIPWRIGHT AT WORK', hull.name + ' IS BUSY RIGHT NOW', 'HE PUTS THE HAMMER DOWN WHEN IT GOES QUIET']);
}

/* ---------- people ---------- */
/* one walker per untriaged inbox note: it walks from the building the note names (else from the tower) to the library
   door and waits there, holding its note, until you triage it. 0 untriaged = nobody walks. Still mode: they already wait. */
function drawWalkers(t, still) {
  const run = still ? 1e9 : Math.max(0, (t - T_START) * WALK_V), T = TRUTH, lib = nameOn('library');
  SEEN.walkers = 0; SEEN.walkerFrom = [];
  if (!live('library') || !nativeOn('library')) return;
  T.walkers.forEach((note, k) => {
    const r = ROUTE[note.from] || ROUTE.tower, qp = r.q || (r.q = {});
    const p = qp[k] || (qp[k] = mkPath(r.pts.slice(0, -1).concat([[199, QUEUE_Y[k]]])));
    const s = Math.min(p.L, run - k * 16);   // they set off one after another
    if (s < 0) return;
    const q = at(p, s), there = s >= p.L, x = Math.round(q.x), y = Math.round(q.y);
    worker(x, y + 1, there ? 1 : (q.dx || 1), there ? 0 : s / 2.5, true, r.shirt);
    SEEN.walkers++; SEEN.walkerFrom.push(note.from);
    tipBox([x - 2, y - 6, 7, 9], [note.title, 'NOTE ' + note.n + (T.inboxDate ? ' IN THE ' + T.inboxDate + ' INBOX' : ' IN THE INBOX') + ': NOT TRIAGED YET',
      there ? 'WAITING AT THE ' + lib + ' DOOR FOR YOU TO TRIAGE IT' : 'WALKING TO THE ' + lib + ', WHERE IT WAITS FOR YOU',
      note.known ? 'ABOUT ' + nameOn(note.from) : 'FROM THE INBOX']);
  });
  if (T.more && SEEN.walkers === T.walkers.length) { moreTag(205, QUEUE_Y[QUEUE_Y.length - 1] - 4, T.more, [plural(T.more, 'MORE NOTE') + ' WAITING', 'ALSO NOT TRIAGED YET: THE ' + lib + ' ROOM SHOWS THEM ALL']); SEEN.more = T.more; }
}

/* ---------- scenery ---------- */
/* the sea: calm scenery. The sky shows in it (this is a view from above): stars at night, the sun by day, the moon. */
const SEA = { night: ['#6fa8d0', '#3f7aa8', '#3b78a8', '#24547e'], dawn: ['#9fc0dc', '#5a8ab0', '#5a8ab8', '#3a6a96'], dusk: ['#a8bcd8', '#5a86ac', '#5a86b4', '#3a6690'],
  day: ['#d8f2ff', '#8fd0f0', '#6ab8e4', '#4f9ed2'] };
function water(t, sky) {
  const c = SEA[sky.phase] || SEA.night, night = sky.phase === 'night';
  for (let x = 0; x < W; x++) { const sy = shoreY(x); if (Math.sin(t * 1.6 + x * 0.35) > 0.55) P(x, sy, c[0]); else if (Math.sin(t * 1.1 + x * 0.21) > 0.7) P(x, sy + 1, c[1]); }
  for (let i = 0; i < 26; i++) { const yy = 203 + (i * 7) % 21, xx = ((hash(i, 3, 2) * W + t * (2 + (i % 3))) % (W + 10)) - 5;
    R(xx, yy, 2 + (i % 3), 1, i % 4 === 0 ? c[2] : c[3]); }
  if (night) for (let i = 0; i < 22; i++) { const xx = Math.floor(hash(i, 9, 4) * W), yy = 204 + Math.floor(hash(i, 2, 5) * 20); if (Math.sin(t * 2 + i * 1.7) > 0.1) P(xx, yy, i % 5 ? '#c8d8ff' : '#ffffff'); }
  else for (let i = 0; i < 10; i++) { const xx = Math.floor(hash(i, 9, 4) * W), yy = 206 + Math.floor(hash(i, 2, 5) * 18); if (Math.sin(t * 2 + i * 1.7) > 0.5) R(xx, yy, 2, 1, sky.phase === 'day' ? '#f4fbff' : '#ffd8b0'); }
  // warm reflection of the boathouse lamp, when the lights are on
  if (LIT && live('boathouse')) for (let j = 0; j < 4; j++) { const yy = 214 + j * 2, ww = 4 - j; R(224 + Math.round(Math.sin(t * 2 + j) * 1), yy, ww, 1, 'rgba(255,190,90,0.35)'); }
  const word = { night: 'NIGHT', dawn: 'DAWN', day: 'DAY', dusk: 'DUSK' }[sky.phase], m = sky.moon;
  tipBox([0, 199, W, 26], ['THE SEA (SCENERY)', word + ' BY YOUR CLOCK (' + sky.hhmm + '). SUNRISE ' + sky.rise + ', SUNSET ' + sky.set + ' (' + SKY_PLACE + ')',
    night ? 'STARS ON THE WATER' + (sky.moonUp && m.illum >= 0.02 ? '' : '; NO MOON NOW: ' + m.name + ', ' + Math.round(m.illum * 100) + '% LIT, ' + (sky.moonUp ? 'TOO THIN TO SEE' : 'BELOW THE HORIZON')) : 'THE MOON SHOWS AT NIGHT IN ITS REAL PHASE'], true);
}
/* the moon in its real phase (lit on the right while waxing), as seen on the water */
function moonDisc(cx, cy, r, m, t, still) {
  const k = Math.cos(2 * Math.PI * m.cycle);   // 1 new, 0 a quarter, -1 full
  for (let j = -r; j <= r; j++) {
    const hw = Math.sqrt(Math.max(0, r * r - j * j + r * 0.5)), sh = still ? 0 : Math.round(Math.sin(t * 1.4 + j * 1.1) * 0.55);
    for (let i = -Math.floor(hw); i <= Math.floor(hw); i++) {
      const xs = m.waxing ? i : -i, isLit = xs > hw * k - 0.25, rim = Math.abs(xs - hw * k) < 1;
      P(cx + i + sh, cy + j, isLit ? (rim && Math.abs(k) < 0.98 ? '#cfcdb8' : (i + j < -2 ? '#ffffff' : '#f2efd8')) : '#24304e');
    }
  }
}
function skyBody(t, sky, still) {
  const ph = sky.phase, m = sky.moon;
  if (ph === 'night' || ((ph === 'dawn' || ph === 'dusk') && sky.moonUp)) {
    SEEN.moon = { up: sky.moonUp, cycle: m.cycle, illum: m.illum, name: m.name };
    if (!sky.moonUp || m.illum < 0.02) return;   // set, or new: no moon to see
    const x = Math.round(161 + sky.moonHA / 6 * 139), y = 208, n = 3 + Math.round(9 * m.illum);
    glow(GL.moon, x, y, 0.4 + 0.6 * m.illum);
    for (let i = 0; i < n; i++) { const yy = 214 + Math.floor(hash(i, 4, 7) * 10), on = still || Math.sin(t * 2.2 + i * 2.1) > -0.2;   // its light on the ripples
      if (on) R(x - 3 + Math.floor(hash(i, 5, 7) * 6), yy, 1 + (i % 3), 1, i % 3 ? '#b8c4e4' : '#e8ecff'); }
    moonDisc(x, y, 4, m, t, still);
    SEEN.moon.x = x;
    tipBox([x - 6, y - 6, 13, 18], ['THE MOON: ' + m.name, Math.round(m.illum * 100) + '% LIT, ' + m.age.toFixed(1) + ' DAYS INTO ITS MONTH (' + (m.waxing ? 'WAXING' : 'WANING') + ')',
      'REAL PHASE AND HOUR FROM YOUR CLOCK: IT RISES ON THE LEFT AND SETS ON THE RIGHT']);
  } else {
    const u = sky.sunU; if (u < -0.04 || u > 1.04) return;
    const x = Math.round(22 + Math.max(0, Math.min(1, u)) * 278), y = 208, low = ph !== 'day';
    glow(low ? GL.low : GL.sun, x, y, 1);
    for (let i = 0; i < 12; i++) { const yy = 213 + Math.floor(hash(i, 4, 8) * 11), on = still || Math.sin(t * 2.6 + i * 1.9) > -0.3;
      if (on) R(x - 4 + Math.floor(hash(i, 5, 8) * 8), yy, 1 + (i % 3), 1, low ? (i % 2 ? '#ffb070' : '#ffe0b0') : (i % 2 ? '#fff6d0' : '#ffffff')); }
    disc(x, y, 4, low ? '#ff9a50' : '#fff2b0'); disc(x - 1, y - 1, 2, low ? '#ffd0a0' : '#ffffff');
    SEEN.sun = { x };
    tipBox([x - 6, y - 6, 13, 18], ['THE SUN, BY YOUR CLOCK (' + sky.hhmm + ')', 'SUNRISE ' + sky.rise + ', SUNSET ' + sky.set + ' (' + SKY_PLACE + '). IT CROSSES FROM LEFT TO RIGHT']);
  }
}
/* the sea serpent: two coils and a crowned head rising out of the water, only when every building is green */
function serpent(t) {
  const wl = 218;
  const bodyPix = new Map();
  const put = (x, y, c) => { if (y < wl) bodyPix.set(x + ',' + y, [x, y, c]); };
  const coil = (cx, ro, ri, bob) => {
    for (let y = wl - ro - 1; y < wl; y++) for (let x = cx - ro - 1; x <= cx + ro + 1; x++) {
      const dx = x - cx, dy = (y - wl + bob) + 0.5, d = Math.sqrt(dx * dx + dy * dy);
      if (d <= ro + 0.3 && d >= ri) { const top = dy < -ro + 2.2, left = dx < -1;
        put(x, y, top ? (left ? '#7fe6c8' : '#4fbfa6') : (left ? '#35a093' : (dx > ro - 2 ? '#1f6b69' : '#2a8a85'))); }
    }
  };
  const b1 = Math.round(Math.sin(t * 1.3) * 0.8), b2 = Math.round(Math.sin(t * 1.3 + 1.2) * 0.8);
  coil(26, 7, 3.4, b1); coil(48, 7, 3.4, b2);
  put(10, wl - 1, '#2a8a85'); put(11, wl - 1, '#2a8a85'); put(11, wl - 2, '#2a8a85'); put(12, wl - 3, '#4fbfa6'); put(12, wl - 2, '#2a8a85'); put(10, wl - 2, '#35a093');
  const hb = Math.round(Math.sin(t * 1.3 + 2.4) * 1);
  for (let i = 0; i <= 20; i++) { const u = i / 20, x = 64 + u * 12, y = wl - u * 13 + hb * u - Math.sin(u * 3.14) * 2;
    for (let j = -2; j <= 2; j++) for (let k = -2; k <= 2; k++) if (j * j + k * k <= 5) put(Math.round(x + j), Math.round(y + k), j < 0 ? '#35a093' : j > 1 ? '#1f6b69' : '#2a8a85'); }
  const hx = 76, hy = wl - 15 + hb;
  const head = ["..######...", ".#########.", "###########", "############", ".##########.", "..######...."];
  head.forEach((r, j) => { for (let i = 0; i < r.length; i++) if (r[i] === '#') put(hx - 3 + i, hy - 2 + j, j < 2 ? '#4fbfa6' : (j > 3 ? '#1f6b69' : '#2a8a85')); });
  const pix = [...bodyPix.values()];
  for (const [x, y] of pix) { if (y > wl - 1) continue; R(x - 1, y, 3, 1, OL); R(x, y - 1, 1, 3, OL); }
  for (const [x, y, c] of pix) P(x, y, c);
  sprite(["c.c.c", "ccccc"], hx - 1, hy - 4, { c: '#ffcc4d' }); P(hx + 1, hy - 4, '#fff2a0');
  R(hx + 4, hy, 2, 2, '#ffe28a'); P(hx + 5, hy, '#ffffff');
  R(hx + 5, hy + 2, 4, 1, '#174f4e');
  P(hx - 4, hy + 1, '#9ff0d8'); P(hx - 5, hy + 2, '#9ff0d8'); P(hx - 5, hy + 4, '#9ff0d8');
  glow(GL.gold, hx + 4, hy + 1, 0.6);
  const foam = (x0, x1) => { for (let x = x0; x <= x1; x++) if ((x + Math.floor(t * 3)) % 3) P(x, wl, '#9fd0f0'); };
  foam(17, 22); foam(30, 35); foam(39, 44); foam(52, 57); foam(61, 68); foam(9, 13);
}
function fountain(t) {
  const cx = 200, cy = 119;
  ellipse(cx, cy, 14, 6, OL); ellipse(cx, cy, 13, 5, '#4c5276'); ellipse(cx, cy, 11, 4, '#1f4f7c'); R(cx - 9, cy - 3, 6, 1, '#3b78a8');
  R(cx - 1, cy - 7, 3, 7, '#5a6080'); R(cx - 1, cy - 7, 1, 7, '#7a82a8');
  for (let i = 0; i < 8; i++) { const ph = (t * 1.2 + i / 8) % 1, a = (i / 8) * 6.28;
    P(cx + Math.round(Math.cos(a) * ph * 8), cy - 8 + Math.round(ph * ph * 10 - ph * 4), '#a8e0ff'); }
  for (let i = 0; i < 4; i++) if (Math.sin(t * 3 + i * 2) > 0.2) P(cx - 6 + i * 4, cy + 1, '#8fc8ee');
}
function streetLamp(x, y) {   // lit after dark by the real clock
  R(x, y, 1, 8, '#3a3e60'); R(x - 2, y - 4, 5, 4, OL); R(x - 1, y - 3, 3, 2, LIT ? '#dff0ff' : '#8a94b0'); R(x - 2, y - 5, 5, 1, '#3a3e60');
  tipBox([x - 2, y - 5, 5, 13], ['STREET LAMP', LIT ? 'LIT: IT IS DARK BY YOUR CLOCK' : 'OFF: IT IS DAY BY YOUR CLOCK'], true);
}

/* status lamp: green steady, amber blinks (needs you), red blinks fast (wrong), grey dark (can't see the source) */
const LAMPCOL = { green: ['#3ddc7a', '#d8ffe6', '#3ddc7a'], amber: ['#ffb020', '#fff0c0', '#6a4518'], red: ['#ff4a4a', '#ffd0d0', '#6a1e1e'], grey: ['#4a4e62', '#6a6e84', '#4a4e62'] };
function statusLamp(x, y, post, lamp, on) {
  if (post) R(x, y + 2, 1, post, '#20233a');
  R(x - 2, y - 2, 5, 5, OL);
  const [c, hi, off] = LAMPCOL[lamp] || LAMPCOL.grey;
  R(x - 1, y - 1, 3, 3, on ? c : off); if (on) P(x - 1, y - 1, hi);
}
const bubbleAbove = (plot, ly) => ly - 16 >= 0 && !PLOT[plot].side;   // side: the bubble would hide another plot's name plate
function bubble(lx, ly, t, col, hi, above) {
  // "!" speech bubble above a lamp that needs you (or right of it when there is no room above)
  const bob = Math.sin(t * 3) > 0 ? 0 : 1;
  const bx = above ? lx - 15 : lx + 5, by = (above ? ly - 16 : ly - 4) + bob;
  R(bx + 1, by, 11, 12, OL); R(bx, by + 1, 13, 10, OL); R(bx + 2, by + 1, 9, 10, '#f4f6ff'); R(bx + 1, by + 2, 11, 8, '#f4f6ff');
  R(bx + 5, by + 2, 3, 5, col); R(bx + 5, by + 8, 3, 2, col); R(bx + 6, by + 2, 1, 5, hi);
  if (above) { P(bx + 10, by + 12, OL); P(bx + 11, by + 13, OL); P(bx + 10, by + 11, '#f4f6ff'); P(bx + 11, by + 12, '#f4f6ff'); }
}
const LAMPWORDS = { green: 'STEADY GREEN: RUNNING FINE', amber: 'BLINKING AMBER: NEEDS YOU', red: 'BLINKING RED: SOMETHING IS WRONG', grey: "DARK: CAN'T SEE ITS FILES" };
function lamps(t, still) {
  SEEN.lamps = {};
  for (const plot of ORDER) {
    const lamp = lampOn(plot); if (!lamp) continue;   // an empty lot has no lamp
    const [x, y, post] = PLOT[plot].lamp;
    const on = still || lamp === 'green' || lamp === 'grey' ? true : lamp === 'amber' ? (t % 1.1) < 0.7 : (t % 0.5) < 0.3;
    statusLamp(x, y, post, lamp, on); SEEN.lamps[plot] = lamp;
    if (lamp === 'green') glow(GL.green, x, y, 0.8 + Math.sin(t * 2 + x) * 0.2);
    else if (lamp === 'amber' && on) glow(GL.amber, x, y, 1);
    else if (lamp === 'red' && on) glow(GL.red, x, y, 1);
    const above = bubbleAbove(plot, y);
    if (lamp === 'amber') bubble(x, y, t, '#ff9a1f', '#ffc060', above);
    else if (lamp === 'red') bubble(x, y, t, '#e03a3a', '#ff8a8a', above);
    const words = LAMPWORDS[lamp], name = nameOn(plot), inside = roomOf(bidOn(plot)) ? 'CLICK THE BUILDING TO WALK IN' : 'CLICK THE BUILDING FOR ITS CARD';
    tipBox([x - 3, y - 3, 7, 7], [name + ' LAMP', words, inside]);
    if (lamp === 'amber' || lamp === 'red') { tipBox([above ? x - 15 : x + 5, above ? y - 16 : y - 4, 14, 14], [name, words]); }
  }
}
function needSign(t, still) {
  const n = D.needs | 0; if (n <= 0) return;
  const on = still || (t % 1.1) < 0.7;
  const s = n + ' NEEDS YOU', tw = textW(s), w = tw + 18, x = W - w - 4, y = 5;
  R(x + 6, 0, 1, y, '#3a3450'); R(x + w - 7, 0, 1, y, '#3a3450');
  R(x, y, w, 13, OL); R(x + 1, y + 1, w - 2, 11, '#2a1a24'); R(x + 1, y + 1, w - 2, 1, '#4a2e30');
  R(x + 1, y + 11, w - 2, 1, on ? '#c06a1a' : '#5a3a20');
  R(x + 4, y + 4, 5, 5, OL); R(x + 5, y + 5, 3, 3, on ? '#ffb020' : '#6a4518'); if (on) { P(x + 5, y + 5, '#fff0c0'); glow(GL.amber, x + 6, y + 6, 0.7); }
  text(s, x + 12, y + 4, '#ffd59a');
  SEEN.needs = n;
  tipBox([x, y, w, 13], [s, 'EVERYTHING WAITING FOR YOU ACROSS THE TOWN (THE AMBER LAMPS)', 'THE DETAILS PAGE LISTS THEM ALL']);
}
/* rings while a job really runs (or in the minute one is due); a spark flies to the library when the job feeds it */
function bellWaves(t, bell, spark) {
  for (let k = 0; k < 3; k++) { const r = ((t * 7 - k * 5) % 15 + 15) % 15 + 5;
    const a = Math.max(0, 1 - (r - 5) / 15); g.fillStyle = 'rgba(255,211,110,' + (a * 0.85).toFixed(2) + ')';
    for (let ang = -1.0; ang <= 0.5; ang += 0.1) g.fillRect(Math.round(55 + Math.cos(ang) * r), Math.round(28 + Math.sin(ang) * r * 0.8), 1, 1);
    for (let ang = Math.PI - 0.5; ang <= Math.PI + 1.0; ang += 0.1) g.fillRect(Math.round(29 + Math.cos(ang) * r * 0.55), Math.round(28 + Math.sin(ang) * r * 0.7), 1, 1); }
  SEEN.bell = bell.why; SEEN.bellJob = bell.job.name;
  const tip = ['THE BELL IS RINGING', bell.why === 'running' ? bell.job.name.toUpperCase() + ' IS RUNNING NOW' : bell.job.name.toUpperCase() + ' IS DUE THIS MINUTE',
    'IT HANGS STILL WHEN NO JOB RUNS'];
  tipBox([22, 10, 40, 34], tip);
  if (!spark) return;
  SEEN.spark = true;
  tipBox([60, 0, 126, 26], tip.slice(0, 2).concat(['THE SPARK CARRIES IT TO THE ' + nameOn('library')]));
  // dotted signal line from the bell to the dome
  const bz = u => [(1 - u) * (1 - u) * 58 + 2 * (1 - u) * u * 125 + u * u * 186, (1 - u) * (1 - u) * 24 + 2 * (1 - u) * u * 0 + u * u * 20];
  for (let i = 2; i < 46; i++) { const [x, y] = bz(i / 48); if (i % 3 === 0) { P(x, y, 'rgba(255,211,110,0.75)'); } }
  const u = (t % 3) / 2.2;
  if (u < 1) { for (let k = 0; k < 7; k++) { const [x, y] = bz(Math.max(0, u - k * 0.02)); P(x, y, k === 0 ? '#fff6c8' : k < 3 ? '#ffd36e' : 'rgba(255,190,90,0.55)'); }
    const [x, y] = bz(u); R(x - 1, y - 1, 3, 3, '#ffd36e'); P(x, y, '#ffffff'); glow(GL.gold, x, y, 1); }
}
function fireflies(t) {   // night scenery: out only after dark by the real clock
  const F = [[110, 80], [140, 60], [262, 70], [20, 100], [120, 190], [340, 160], [160, 100], [380, 76]];
  F.forEach(([x, y], i) => { if (Math.sin(t * 1.7 + i * 2.3) > -0.1) P(x + Math.round(Math.sin(t * 0.7 + i) * 5), y + Math.round(Math.cos(t * 0.9 + i * 1.3) * 3), '#d8ff8a');
    tipBox([x - 6, y - 4, 13, 9], ['FIREFLIES', 'NIGHT SCENERY: OUT ONLY AFTER DARK BY YOUR CLOCK'], true); });
  SEEN.fireflies = true;
}
/* unreadable data (or the labelled template sample, or a town with no buildings yet): a sign says so */
function banner() {
  const sample = !!D.sample, empty = !DATA_ERR && !sample;
  const rows = sample ? ['SAMPLE: TEMPLATE PREVIEW', 'MADE-UP NUMBERS, NOT YOUR DATA']
    : empty ? ['SIX EMPTY LOTS', 'ADD A [[BUILDING]] TO LAMPTOWN.TOML TO BUILD ON ONE']
      : ["CAN'T READ THE TOWN'S DATA"].concat(wrapText(DATA_ERR, 210).slice(0, 3), ['EVERY PLOT STAYS DARK UNTIL IT CAN', 'REBUILD: LAMPTOWN BUILD']);
  const w = Math.max(...rows.map(textW)) + 14, h = rows.length * 8 + 8, x = Math.round((W - w) / 2), y = sample || empty ? 2 : 96;
  const [bg, top, c0, c1] = sample ? ['#2a2410', '#ffb020', '#ffc24a', '#ffe0a0'] : empty ? ['#161b38', '#59629a', '#ffd36e', '#c9cff0'] : ['#2a1420', '#ff4a4a', '#ff7a7a', '#ffd0d0'];
  R(x - 1, y - 1, w + 2, h + 2, OL); R(x, y, w, h, bg); R(x, y, w, 1, top);
  rows.forEach((r, i) => text(r, x + Math.round((w - textW(r)) / 2), y + 5 + i * 8, i ? c1 : c0));
  SEEN.banner = rows[0];
}
/* the live channel's state (see "live data" at the end): on = a Watcher feeds the page now (its last payload is fresh) */
/* rev: the data shown; want: the rev being loaded; bad: a rev not to load again; every: ms between polls; slow: ms between
   polls after `miss` misses in a row (no Watcher); next: the delay of the poll scheduled now */
const LIVE = { on: false, payload: null, rev: typeof D.rev === 'string' && D.rev ? D.rev : null, want: null, bad: null, every: 5000,
  slow: 30000, next: 0, miss: 0, ageT: 0, started: false, off: false, timer: 0, busy: null, seq: 0,
  polls: 0, fails: 0, loads: 0, swaps: 0 };
/* corner: LIVE while a Watcher feeds the page, else when the page was built; and the way into the details page */
const CORNER = { det: 'DETAILS >' };
function cornerBuilt() {
  if (DATA_ERR) return 'NO DATA';
  if (D.sample) return 'SAMPLE';
  if (LIVE.on) return 'LIVE';
  return (isApp() ? 'LIVE - UPDATED ' : 'BUILT ') + String(D.built || '').toUpperCase().replace(/,/g, '');
}
function corner() {
  const built = cornerBuilt(), live = built === 'LIVE', bw = textW(built) + (live ? 5 : 0), dw = textW(CORNER.det), w = Math.max(bw, dw) + 6, x = W - w - 2, y = H - 17;
  R(x, y, w, 16, 'rgba(8,10,22,0.72)');
  if (live) { R(x + w - 3 - bw, y + 3, 3, 3, '#3ddc7a'); P(x + w - 3 - bw, y + 3, '#d8ffe6'); }   // a small steady lamp: fed live
  text(built, x + w - 3 - bw + (live ? 5 : 0), y + 2, live ? '#7cf0a8' : '#9aa3d8');
  text(CORNER.det, x + w - 3 - dw, y + 9, '#ffd36e');
  CORNER.hit = [x + w - 5 - dw, y + 8, dw + 4, 7];
  SEEN.corner = built;
}
function lotTips() {
  for (const plot of ORDER) if (!built(plot)) {
    const [x, y, w, h] = PLOT[plot].lot;
    tipBox([x, y, w, h], ['EMPTY LOT', 'NO BUILDING ON THE ' + plot.toUpperCase() + ' PLOT YET', 'ADD ONE IN LAMPTOWN.TOML: PLOT = "' + plot.toUpperCase() + '"'], true);
  }
}

/* ---------- frame ---------- */
/* daylight grade over the scene (not over signs, lamps and text): night is the original look, untouched;
   dawn and dusk: a cool lavender sky-light over the land, warming to the low sun's colour towards the sea */
const GRADE = { day: 'rgba(255,244,222,0.42)',
  dawn: ['rgba(160,150,235,0.45)', 'rgba(255,180,170,0.75)'], dusk: ['rgba(140,110,220,0.45)', 'rgba(255,120,50,0.75)'] };
const GRADE_FILL = {};
function grade(ph) {
  const c = GRADE[ph]; if (!c) return;
  let f = GRADE_FILL[ph];
  if (!f) {
    if (typeof c === 'string') f = c;
    else { f = g.createLinearGradient(0, 0, 0, H); f.addColorStop(0, c[0]); f.addColorStop(0.3, c[0]); f.addColorStop(1, c[1]); }
    GRADE_FILL[ph] = f;
  }
  g.globalCompositeOperation = 'soft-light'; g.fillStyle = f; g.fillRect(0, 0, W, H); g.globalCompositeOperation = 'source-over';
}
const WARM = { library: [200, 76], lab: [86, 145], workshop: [306, 147], boathouse: [204, 188] };
function draw(t, still) {
  TIPS = []; TIPS_BACK = []; SEEN = { plates: {}, lots: ORDER.filter(p => !built(p)), live: LIVE.payload, liveOn: LIVE.on };
  const ms = nowMs(), sky = skyAt(ms), bell = live('tower') && nativeOn('tower') ? bellNow(ms) : null;
  LIT = sky.lights; SEEN.phase = sky.phase;
  g.drawImage(bgFor(sky.phase), 0, 0);
  water(t, sky); skyBody(t, sky, still);
  if (TRUTH.allGreen) {   // the sea serpent surfaces only when every building is green
    serpent(t); SEEN.serpent = true;
    tipBox([8, 196, 84, 23], ['THE SEA SERPENT', 'SURFACES ONLY WHEN EVERY BUILDING IS GREEN: ' + (TRUTH.builtCount === 1 ? 'THE ONE BUILDING IS' : 'ALL ' + TRUTH.builtCount + ' ARE') + ' FINE RIGHT NOW']);
  }
  belts(t, still);
  for (const k of ['A', 'B', 'C']) if (beltOn(k)) drawBelt(BELTS[k]);
  for (const k of ['A', 'B', 'C']) if (beltOn(k)) drawItems(BELTS[k]);
  if (built('gate')) { R(391, 20, 9, 13, OL); R(392, 21, 8, 11, '#2a2e4a'); R(392, 21, 8, 1, '#5a6090'); R(392, 23, 1, 7, '#14172a'); } // intake pipe
  const spark = !!bell && !!bell.job.feeds && bell.job.feeds === bidOn('library') && live('library');
  const u3 = (t % 3) / 2.2, flash = spark ? (u3 >= 1 ? 1 - (u3 - 1) / 0.37 : 0.25) : 0;
  if (built('tower')) drawTower(t, !!bell, still, ms);
  if (built('library')) drawLibrary(t, flash);
  if (built('gate')) drawGate(t);
  if (built('lab')) drawLab(t);
  if (built('workshop')) drawWorkshop(t);
  if (built('boathouse')) drawBoathouse(t);
  if (built('gate')) gateOpening(t);
  if (built('boathouse')) drawSlip(t);
  fountain(t);
  tipBox([186, 104, 29, 22], ['FOUNTAIN', 'SCENERY: IT ALWAYS RUNS'], true);
  // lights off over any building whose source is missing
  for (const plot of ORDER) if (built(plot) && !live(plot)) { const [x, y, w, h] = PLOT[plot].dark; R(x, y, w, h, 'rgba(6,8,20,0.42)'); }
  drawWalkers(t, still);
  grade(sky.phase);
  for (const plot of ORDER) plate(plot);
  streetLamp(128, 84); streetLamp(270, 84);
  if (LIT) {
    glow(GL.cool, 128, 92, 1); glow(GL.cool, 270, 92, 1);
    for (const [plot, [x, y]] of Object.entries(WARM)) if (live(plot)) glow(GL.warm, x, y, 1);
  }
  lamps(t, still); needSign(t, still);
  if (bell) bellWaves(t, bell, spark);
  if (sky.phase === 'night') fireflies(t);
  beltTipsAll(); lotTips();
  if (DATA_ERR || D.sample || !TRUTH.builtCount) banner();
  corner();
  townTip();
}
/* the town's tooltip: the thing under the pointer says what real thing it is */
function tipAt(x, y) { const f = tp => x >= tp.box[0] && x < tp.box[0] + tp.box[2] && y >= tp.box[1] && y < tp.box[1] + tp.box[3]; return TIPS.find(f) || TIPS_BACK.find(f) || null; }
let TIP_ON = null;
function townTip() {
  if (TH.x < 0 || RS.id || !panel.hidden) { TIP_ON = null; return; }
  TIP_ON = tipAt(TH.x, TH.y);
  if (TIP_ON) drawTip(TIP_ON.tip, TH.x, TH.y);
}

/* ---------- fit to window: integer scale in device pixels, letterbox the rest ---------- */
const stage = document.getElementById('stage'), cvs = document.getElementById('c'), hits = document.getElementById('hits');
let SCALE = 1;
function fit() {
  const dpr = window.devicePixelRatio || 1, vw = window.innerWidth, vh = window.innerHeight;
  const fitDev = Math.min(vw / W, vh / H) * dpr;
  const dev = fitDev >= 1 ? Math.floor(fitDev) : fitDev;
  SCALE = dev / dpr;
  const cw = W * SCALE, ch = H * SCALE;
  cvs.style.width = cw + 'px'; cvs.style.height = ch + 'px';
  stage.style.left = Math.round((vw - cw) / 2) + 'px'; stage.style.top = Math.round((vh - ch) / 2) + 'px';
  hits.style.transform = 'scale(' + SCALE + ')';
  if (!panel.hidden && openPlot) placePanel(openPlot);
}

/* ---------- plots as buttons + the plain-words panel ---------- */
const panel = document.getElementById('panel'), pTitle = document.getElementById('p-title'), pBody = document.getElementById('p-body'),
  pActs = document.getElementById('p-acts'), pLamp = document.getElementById('p-lamp'), pClose = document.getElementById('p-close');
let openPlot = null, opener = null;
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const safeHref = h => typeof h === 'string' && (/^(https?|file):/i.test(h) || h.startsWith(String(D.details || 'town-details.html'))) ? h : null;
// "in 3 d 11 h": counted here, not in the data, so the data only changes when something real changes.
function rel(ms) {
  const a = Math.abs(ms), d = Math.floor(a / 864e5), h = Math.floor(a % 864e5 / 36e5), m = Math.floor(a % 36e5 / 6e4);
  const t = d ? d + ' d ' + h + ' h' : (h ? h + ' h ' + m + ' min' : m + ' min');
  return ms >= 0 ? 'in ' + t : t + ' ago';
}
const nextText = () => TRUTH.due.filter(j => j.at >= nowMs() - 6e4).slice(0, 3).map(j => j.name + ' (' + rel(j.at - nowMs()) + ')').join('; ');
const WORD = { green: 'running fine', amber: 'needs you', red: 'something is wrong', grey: "can't see its files" };
function pixelLabel(str, scale, col) {
  const c = document.createElement('canvas'); c.width = textW(str) + 2; c.height = 7;
  const old = g; g = c.getContext('2d'); text(str, 1, 1, col); g = old;
  c.style.width = c.width * scale + 'px'; c.style.height = c.height * scale + 'px';
  return c;
}
function lampIcon(lamp) {
  const c = document.createElement('canvas'); c.width = 5; c.height = 5;
  const old = g; g = c.getContext('2d'); statusLamp(2, 2, 0, lamp, true); g = old;
  c.style.width = '20px'; c.style.height = '20px'; c.setAttribute('aria-hidden', 'true');
  return c;
}
const ROOMS = {};   // room type -> room (filled by registerRoom, from the room scripts after this one)
const roomOf = id => (id && PT[id] && ROOMS[PT[id].type]) || null;
function plotLabel(plot) { const p = partOn(plot); return p ? String(p.label || p.name || p.id) : 'Empty lot (' + plot + ' plot)'; }
/* one button per place, made once; syncHits() writes what stands there now (at load, once the rooms are in, on a swap) */
const HIT = {};
function hitFor(plot) {
  const [x, y, w, h] = PLACES[plot].hit, b = HIT[plot] || document.createElement('button');
  Object.assign(b.style, { left: x + 'px', top: y + 'px', width: w + 'px', height: h + 'px' });
  if (HIT[plot]) return b;
  b.className = 'hit'; b.dataset.plot = plot;
  b.addEventListener('click', e => { e.stopPropagation(); const id = bidOn(plot); if (roomOf(id)) enterRoom(id); else openPanel(plot, b); }); // a room: walk inside
  hits.insertBefore(b, hits.querySelector('.det'));   // the plots, then any other place, then the details link
  return (HIT[plot] = b);
}
function syncHits() {
  for (const plot of Object.keys(PLACES)) {
    if (!PLACES[plot].hit) continue;
    const b = hitFor(plot), p = partOn(plot), id = bidOn(plot), room = !!roomOf(id);
    if (id) b.dataset.id = id; else delete b.dataset.id;
    b.hidden = !p && !PLOT[plot];   // a place off the plot grid has no empty lot: no building there, no button
    b.setAttribute('aria-label', p ? plotLabel(plot) + (p.num && lampOn(plot) !== 'grey' ? ', ' + p.num : '') + ', ' + WORD[lampOn(plot)] + (room ? '. Press Enter to walk inside.' : '') : plotLabel(plot));
    if (room) b.removeAttribute('aria-haspopup'); else b.setAttribute('aria-haspopup', 'dialog');
  }
}
/* a place off the six plots (the lighthouse): { hit: [x, y, w, h] click box, door: [x, y] the walk-in dives to, word }.
   A building stands there when its part says plot = <name>; it opens like any plot (card or room). */
function addPlace(name, place) {
  if (typeof name !== 'string' || !/^[a-z][a-z0-9_]{0,39}$/.test(name) || isPlace(name) && PLOT[name] || !place || !Array.isArray(place.hit)) return false;
  PLACES[name] = Object.assign({ door: [place.hit[0] + (place.hit[2] >> 1), place.hit[1] + place.hit[3]], word: name }, place);
  syncHits(); return true;
}
// the details page: a path next to the town, or an http(s)/file URL; never another scheme (javascript:, data:, ...).
// The URL parser drops tabs and newlines anywhere and spaces at the ends, so the scheme is read with those gone.
const detHref = h => {
  const u = typeof h === 'string' ? h.replace(/[\x00-\x20]/g, '') : '';
  return u && (/^(https?|file):/i.test(u) || !/^[a-z][a-z0-9+.-]*:/i.test(u)) ? h : 'town-details.html';
};
const DET = document.createElement('a');   // the "Details" corner link
DET.className = 'hit det'; DET.style.display = 'block'; hits.appendChild(DET);
function syncDetails() {
  const [x, y, w, h] = CORNER.hit;
  DET.href = detHref(D.details); DET.setAttribute('aria-label', 'Details page (built ' + (D.built || '') + ')');
  Object.assign(DET.style, { left: x + 'px', top: y + 'px', width: w + 'px', height: h + 'px' });
}
syncHits(); draw(T_START, true); syncDetails();
function srText() { // for screen readers: what moves in town, in words (the same facts the animations follow)
  const T = TRUTH, s = [];
  if (DATA_ERR) s.push("The town can't read its data: " + DATA_ERR.toLowerCase() + '.');
  const lots = ORDER.filter(p => !built(p));
  if (lots.length) s.push(lots.length === 6 ? 'Every plot is an empty lot.' : 'Empty lots: ' + lots.join(', ') + '.');
  if (T.notes.length && nativeOn('library')) s.push(T.notes.length + ' untriaged note' + (T.notes.length === 1 ? '' : 's') + ' wait at ' + plotLabel('library') + '.');
  if (T.scanning.length && built('gate')) s.push('A tool is being scanned at ' + plotLabel('gate') + '.');
  if (T.gate.length && built('gate')) s.push(T.gate.length + ' tool' + (T.gate.length === 1 ? '' : 's') + ' at ' + plotLabel('gate') + ': ' + T.gate.map(x => x.name + (x.red ? ' (stopped)' : ' (waiting for your call)')).join(', ') + '.');
  if (T.items.length && built('workshop')) s.push(T.items.length + ' item' + (T.items.length === 1 ? '' : 's') + ' being built or staged.');
  if (T.picks.length && built('lab')) s.push(T.picks.length + ' contest result' + (T.picks.length === 1 ? '' : 's') + ' waiting for your pick.');
  if (T.judging.length && built('lab')) s.push('Judges are still out.');
  if (T.running.length && built('tower')) s.push(T.running.map(j => j.name).join(', ') + ' running now.');
  if (T.hull && T.hull.busy && built('boathouse')) s.push(T.hull.name + ' is busy right now.');
  if (T.allGreen) s.push('Every building is green.');
  document.getElementById('town-sr').textContent = s.length ? s.join(' ') : 'Nothing is moving in town: nothing is running and nothing waits on a belt.';
}
srText();
function openPanel(plot, from) {
  if (!isPlace(plot) || (!PLOT[plot] && !partOn(plot))) return;
  openPlot = plot; opener = from || document.querySelector('.hit[data-plot="' + plot + '"]');
  fillPanel(plot);
  panel.hidden = false;
  placePanel(plot);
  pClose.focus({ preventScroll: true });
}
function fillPanel(plot) {   // the card's words, from the data now (a live swap refills an open card)
  const p = partOn(plot);
  pTitle.replaceChildren(pixelLabel(p ? nameOn(plot) : 'EMPTY LOT', 3, p ? '#eef1ff' : '#a9aec8'));
  pTitle.setAttribute('aria-label', plotLabel(plot));
  pLamp.replaceChildren(lampIcon(p ? lampOn(plot) : 'grey'));
  const rows = [];
  if (!p) {
    rows.push(['Plot', esc(plot) + ' (the ' + esc(PLOT[plot].word) + ')']);
    rows.push(['Build here', 'Add a building to <code>lamptown.toml</code> with <code>plot = "' + esc(plot) + '"</code>, or a <code>type = "' + esc(PLOT[plot].native) + '"</code> building.']);
    pActs.innerHTML = '';
  } else {
    rows.push(['Room', esc(p.type) + ' room on the ' + esc(plot) + ' plot']);
    rows.push(['Status', '<span class="lamp-' + esc(lampOn(plot)) + '"><b>' + esc(p.status || WORD[lampOn(plot)]) + '</b></span>' + (p.result ? '<br><span class="muted">' + esc(p.result) + '</span>' : '')]);
    if (p.waiting && p.waiting.length) rows.push(['Waiting for you', p.waiting.length === 1 ? esc(p.waiting[0]) : '<ul>' + p.waiting.map(w => '<li>' + esc(w) + '</li>').join('') + '</ul>']);
    if (nativeOn(plot) && plot === 'tower' && nextText()) rows.push(['Next run', '<span id="p-next">' + esc(nextText()) + '</span>']);
    pActs.innerHTML = (p.links || []).map(l => Object.assign({}, l, { href: safeHref(l.href) })).filter(l => l.href).slice(0, 2)
      .map(l => '<a href="' + esc(l.href) + '"' + (l.label === 'Details' ? '' : ' target="_blank" rel="noopener"') + '>' + esc(l.label) + '</a>').join('');
  }
  pBody.innerHTML = rows.map(([k, v]) => '<dt>' + k + '</dt><dd>' + v + '</dd>').join('');
}
function placePanel(plot) {
  if (RS.id) { // inside a room: the card sits in the middle of the window
    panel.style.left = Math.round(Math.max(16, (window.innerWidth - panel.offsetWidth) / 2)) + 'px';
    panel.style.top = Math.round(Math.max(16, (window.innerHeight - panel.offsetHeight) / 2)) + 'px'; return;
  }
  const [x, y, w, h] = PLACES[plot].hit, r = stage.getBoundingClientRect();
  const bx = r.left + x * SCALE, by = r.top + y * SCALE, bw = w * SCALE, bh = h * SCALE;
  const pw = panel.offsetWidth, ph = panel.offsetHeight, vw = window.innerWidth, vh = window.innerHeight, m = 16;
  let left = bx + bw + m;
  if (left + pw > vw - m) left = bx - pw - m;
  if (left < m) left = Math.max(m, Math.min(vw - pw - m, bx + bw / 2 - pw / 2));
  const top = Math.max(m, Math.min(vh - ph - m, by + bh / 2 - ph / 2));
  panel.style.left = Math.round(left) + 'px'; panel.style.top = Math.round(top) + 'px';
}
function closePanel() {
  if (panel.hidden) return;
  panel.hidden = true; openPlot = null;
  if (opener) opener.focus({ preventScroll: true });
  if (location.hash.startsWith('#open=')) history.replaceState(null, '', location.pathname + location.search);
  else if (/&open=/.test(location.hash)) history.replaceState(history.state, '', location.hash.replace(/&open=[\w-]*/, ''));
}
pClose.addEventListener('click', closePanel);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { if (!panel.hidden) closePanel(); else if (RS.id) { e.preventDefault(); leaveRoom(); } return; }
  if (RS.id && panel.hidden) roomKey(e);
});
document.addEventListener('click', e => { if (!panel.hidden && !panel.contains(e.target)) closePanel(); });
window.addEventListener('resize', fit);
setInterval(() => { const n = document.getElementById('p-next'); if (n && openPlot) n.textContent = nextText(); }, 30000);

const RM = window.matchMedia('(prefers-reduced-motion: reduce)');
// ?motion=1 (and ?demo=1): animate even when the system asks for reduced motion; the app always animates
const FORCE_MOTION = () => qs.get('motion') === '1' || qs.get('demo') === '1' || isApp();

/* ======================= ROOMS: walk inside a building =======================
   A room script (inlined by `lamptown build` after this one) calls registerRoom(type, room) and draws with the kit K.
   Every building of that room type opens it with its own data (D.rooms[building id]). A building whose type has
   no room keeps its text card. The interface is documented in docs/rooms.md. */
function registerRoom(type, room) {
  if (typeof type === 'string' && /^[a-z][a-z0-9_]{0,39}$/.test(type) && room && typeof room.draw === 'function') ROOMS[type] = room;
}

/* ---------- scene: town / zooming in / room / zooming out ---------- */
const ZOOM_MS = 600, SPLIT = 0.62, ZMAX = 12;
const RS = { id: null, plot: null, room: null, data: null, mode: 'town', t0: 0, frozen: null, pushed: false,
  mx: -1, my: -1, hover: null, hoverKey: '', focus: -1, toast: null, crash: null, opener: null, backing: null };
const stillMode = () => RM.matches && !FORCE_MOTION();
const TB = document.createElement('canvas'); TB.width = W; TB.height = H;   // town / room buffers for the zoom
const RB = document.createElement('canvas'); RB.width = W; RB.height = H;
const srRoom = document.getElementById('room-sr');
function roomData(id) { const r = D.rooms && D.rooms[id]; return r && typeof r === 'object' ? r : { state: 'missing', missing: true }; }
function roomCtx(id) { return { demo: qs.get('demo') === '1', state: qs.get('state'), qs, still: stillMode(), id, part: PT[id] }; }
function announce(s) { if (srRoom) srRoom.textContent = s; }

function enterRoom(id, opts) {
  const room = roomOf(id), plot = plotOf(id); if (!room || !plot) { if (plot) openPanel(plot); return; }
  opts = opts || {};
  if (!panel.hidden) closePanel();
  if (RS.id) finishLeave(true);
  TH.x = TH.y = -1;
  RS.opener = document.querySelector('.hit[data-plot="' + plot + '"]');
  Object.assign(RS, { id, plot, room, data: roomData(id), hover: null, hoverKey: '', focus: -1, toast: null, crash: null, frozen: null });
  try { room.enter && room.enter(RS.data, roomCtx(id)); }
  catch (e) { RS.crash = e; console.error(e); }
  hits.hidden = true; stage.focus({ preventScroll: true });
  SFX.unlock(); SFX.door(true);
  RS.mode = opts.instant || stillMode() ? 'room' : 'in'; RS.t0 = performance.now();
  if (opts.history !== false) { history.pushState({ room: id }, '', location.pathname + location.search + '#room=' + id); RS.pushed = true; }
  else RS.pushed = !!(history.state && history.state.room === id);
  announce('Inside ' + plotLabel(plot) + '. Tab moves between things, Enter uses one, Escape goes back to town.');
  kick();
}
const TOWN_QS = ['motion', 't', 'at'];   // the town's own URL params; every other one belongs to a room and goes when you leave it
function leaveRoom(opts) {
  const fromHistory = !!(opts && opts.fromHistory);
  if (!RS.id || RS.mode === 'out' || RS.backing && !fromHistory) return;   // already stepping back: once is enough
  if (!panel.hidden) closePanel();
  if (!fromHistory) {
    if (RS.pushed && history.state && history.state.room === RS.id) { // our own history entry: step back (popstate leaves)
      const id = RS.backing = RS.id; history.back();
      setTimeout(() => { if (RS.id === id && RS.mode === 'room') leaveRoom({ fromHistory: true }); }, 400); // no popstate came
      return;
    }
    const q = new URLSearchParams(location.search); [...q.keys()].filter(k => !TOWN_QS.includes(k)).forEach(k => q.delete(k));
    history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q : ''));
  }
  SFX.door(false); SFX.hush();
  RS.frozen = null; RS.mode = stillMode() ? 'town' : 'out'; RS.t0 = performance.now();
  if (RS.mode === 'town') finishLeave();
  kick();
}
function finishLeave(quiet) {
  try { RS.room && RS.room.leave && RS.room.leave(); } catch (e) { console.error(e); }
  const back = RS.opener;
  Object.assign(RS, { id: null, plot: null, room: null, data: null, mode: 'town', hover: null, focus: -1, toast: null, crash: null, backing: null });
  hits.hidden = false; stage.style.cursor = '';
  if (!quiet) { announce('Back in town.'); if (back) back.focus({ preventScroll: true }); }
}
window.addEventListener('popstate', () => {
  const m = /^#room=([\w-]+)/.exec(location.hash);
  if (m && roomOf(m[1])) { if (RS.id !== m[1]) enterRoom(m[1], { history: false }); }
  else if (RS.id) leaveRoom({ fromHistory: true });
});
// for the desktop app: the hash that brings this view back after a reload
function viewHash() {
  const openId = openPlot ? bidOn(openPlot) : null;
  if (RS.id) return '#room=' + RS.id + (openId ? '&open=' + openId : '');
  return openId ? '#open=' + openId : '';
}

/* ---------- toast, tooltip, HUD (all in the pixel font, drawn on the canvas) ---------- */
function toast(msg) { RS.toast = { msg: String(msg), until: performance.now() + 2600 }; announce(msg); kick(); }
function drawTip(lines, ax, ay) {
  const rows = []; lines.forEach((l, i) => wrapText(l, 190).forEach(r => rows.push([r, i === 0])));
  if (!rows.length) return;
  const w = Math.max(...rows.map(r => textW(r[0]))) + 7, h = rows.length * 7 + 5;
  let x = ax + 7, y = ay + 9;
  if (x + w > W - 2) x = ax - w - 4; if (y + h > H - 2) y = ay - h - 3;
  x = Math.max(2, Math.min(W - w - 2, x)); y = Math.max(13, Math.min(H - h - 2, y));
  R(x - 1, y - 1, w + 2, h + 2, OL); R(x, y, w, h, '#161b38'); R(x, y, w, 1, '#59629a');
  rows.forEach(([r, first], i) => textL(r, x + 4, y + 3 + i * 7, first ? '#ffd36e' : '#eef1ff'));
}
function drawToast() {
  const T = RS.toast; if (!T) return; if (performance.now() > T.until) { RS.toast = null; return; }
  const rows = wrapText(T.msg, 300), w = Math.max(...rows.map(textW)) + 14, h = rows.length * 7 + 7, x = Math.round(W / 2 - w / 2), y = H - h - 6;
  R(x - 1, y - 1, w + 2, h + 2, OL); R(x, y, w, h, '#161b38'); R(x, y, w, 1, '#ffd36e');
  rows.forEach((r, i) => textC(r, W / 2, y + 4 + i * 7, '#ffd36e'));
}
const HUD = { town: [2, 1, 0, 10], card: [367, 1, 13, 10], mute: [383, 1, 15, 10] };
HUD.town[2] = textW('< TOWN') + 9;
function hudTargets() {
  const name = plotLabel(RS.plot);
  return [
    { id: 'hud-town', box: HUD.town, tip: ['BACK TO TOWN', 'OR PRESS ESC'], label: 'Back to town', onClick: () => leaveRoom() },
    { id: 'hud-card', box: HUD.card, tip: ['THE CARD', 'STATUS IN WORDS + THE DETAILS PAGE LINK (KEY I)'], label: name + ' card', onClick: openCard },
    { id: 'hud-mute', box: HUD.mute, tip: SFX.muted ? ['SOUND OFF', 'CLICK TO TURN IT ON (KEY M)'] : ['SOUND ON', 'CLICK TO MUTE (KEY M)'], label: SFX.muted ? 'Sound off' : 'Sound on', onClick: toggleMute },
  ];
}
function openCard() { if (RS.plot) openPanel(RS.plot, stage); }
function toggleMute() { SFX.unlock(); SFX.setMuted(!SFX.muted); toast(SFX.muted ? 'SOUND OFF (REMEMBERED)' : 'SOUND ON'); }
function btn(box, hot) { const [x, y, w, h] = box; R(x, y, w, h, OL); R(x + 1, y + 1, w - 2, h - 2, hot ? '#2b336a' : '#161b38'); R(x + 1, y + 1, w - 2, 1, hot ? '#59629a' : '#2b336a'); }
function drawHud() {
  const hot = k => RS.hover && RS.hover.box === HUD[k];
  btn(HUD.town, hot('town')); textL('< TOWN', HUD.town[0] + 5, HUD.town[1] + 3, '#ffd36e');
  // the building's name plate, as in town (the room may set its own number and state)
  const p = PT[RS.id] || {};
  const pl = Object.assign({ name: nameOn(RS.plot), num: String(p.num || ''), state: SOFL[lampOn(RS.plot)] || 'missing' }, (RS.room.plate && safe(() => RS.room.plate())) || {});
  if (RS.crash || (RS.data && RS.data.error)) Object.assign(pl, { num: '!', state: 'error' }); // the room couldn't read: red, not "nothing there"
  const num = pl.state === 'missing' ? '-' : String(pl.num || ''), nm = fitText(pl.name, 300), tw = textW(nm), w = tw + (num ? textW(num) + 7 : 0) + 8, x = HUD.town[0] + HUD.town[2] + 3, y = 1;
  R(x, y, w, 10, OL); R(x + 1, y + 1, w - 2, 8, '#161b38'); R(x + 1, y + 1, w - 2, 1, '#2b336a');
  textL(nm, x + 4, y + 3, '#eef1ff');
  if (num) { R(x + 4 + tw + 3, y + 3, 1, 5, '#323a72'); textL(num, x + 4 + tw + 7, y + 3, (ST[pl.state] || ST.ok).num); }
  // the card sign: a small "i"
  const [cx, cy] = HUD.card; btn(HUD.card, hot('card')); R(cx + 6, cy + 2, 1, 1, '#8fe3ff'); R(cx + 5, cy + 4, 2, 1, '#8fe3ff'); R(cx + 6, cy + 5, 1, 3, '#8fe3ff'); R(cx + 5, cy + 7, 3, 1, '#8fe3ff');
  // mute: a speaker with waves, or with a red cross
  const [mx, my] = HUD.mute; btn(HUD.mute, hot('mute'));
  R(mx + 3, my + 4, 2, 3, '#eef1ff'); R(mx + 5, my + 3, 1, 5, '#eef1ff'); R(mx + 6, my + 2, 1, 7, '#eef1ff');
  if (SFX.muted) { for (const [a, b] of [[8, 3], [10, 3], [9, 4], [9, 5], [9, 6], [8, 7], [10, 7]]) P(mx + a, my + b, '#ff7a7a'); }
  else { R(mx + 8, my + 4, 1, 3, '#ffd36e'); R(mx + 10, my + 3, 1, 5, '#ffd36e'); }
}
function safe(fn, dflt) { try { return fn(); } catch (e) { if (!RS.crash) { RS.crash = e; console.error(e); } return dflt; } }
function allTargets() { return RS.id ? hudTargets().concat(safe(() => RS.room.targets ? RS.room.targets() || [] : [], [])) : []; }
const inBox = (b, x, y) => x >= b[0] && x < b[0] + b[2] && y >= b[1] && y < b[1] + b[3];
function targetAt(x, y) { return allTargets().find(t => inBox(t.box, x, y)) || null; }
function roomHover(tg) { // a room's optional hover(target|null): only when the target under the pointer / Tab focus changes
  const key = tg ? (tg.id || (tg.box || []).join(',') + '|' + (tg.label || '')) : '';
  if (key === RS.hoverKey) return; RS.hoverKey = key;
  if (RS.room && RS.room.hover) safe(() => RS.room.hover(tg || null));
}
function useTarget(t) { if (!t) return; SFX.unlock(); if (t.onClick) safe(() => t.onClick()); kick(); }

/* a room's own error (its data crashed, or its draw threw): say so in the room, keep the HUD working */
function drawRoomError(msg) {
  const rows = ['THIS ROOM COULD NOT READ ITS FILES'].concat(wrapText(msg, 250).slice(0, 4)), w = 270, h = rows.length * 8 + 10, x = (W - w) / 2, y = 80;
  R(x - 1, y - 1, w + 2, h + 2, OL); R(x, y, w, h, '#2a1420'); R(x, y, w, 1, '#ff4a4a');
  rows.forEach((r, i) => textC(r, W / 2, y + 6 + i * 8, i ? '#ffd0d0' : '#ff7a7a'));
}
function drawRoomFrame(t, still) {
  R(0, 0, W, H, '#0b0e1c');
  if (!RS.crash) safe(() => RS.room.draw(g, still ? OFF : t, RS.data));
  if (RS.crash) drawRoomError(String(RS.crash && RS.crash.message || RS.crash));
  else if (RS.data && RS.data.error) drawRoomError(RS.data.error);
  drawHud();
  const tg = RS.focus >= 0 ? allTargets()[RS.focus] : null;
  if (tg) { const [x, y, w, h] = tg.box; g.strokeStyle = '#ffd36e'; g.lineWidth = 1; g.strokeRect(x - 0.5, y - 0.5, w + 1, h + 1); }
  const tipT = tg || RS.hover;
  if (tipT && tipT.tip) drawTip(tipT.tip, tg ? tg.box[0] + tg.box[2] / 2 : RS.mx, tg ? tg.box[1] + tg.box[3] / 2 : RS.my);
  drawToast();
}
function dim(a) { if (a > 0) { g.fillStyle = 'rgba(5,6,14,' + Math.min(1, a).toFixed(3) + ')'; g.fillRect(0, 0, W, H); } }
function drawZoom(p, t, still) {
  // 0..SPLIT: the camera dives through the building's door; SPLIT..1: the room opens up out of the dark
  main.imageSmoothingEnabled = false;
  if (p < SPLIT) {
    const u = p / SPLIT, e = u * u, [dx, dy] = PLACES[RS.plot].door, s = 1 + (ZMAX - 1) * e;
    const cx = dx + (W / 2 - dx) * e, cy = dy + (H / 2 - dy) * e;
    g = TB.getContext('2d'); draw(t, still); g = main;
    R(0, 0, W, H, '#05060e');
    main.drawImage(TB, Math.round(cx - dx * s), Math.round(cy - dy * s), Math.round(W * s), Math.round(H * s));
    dim(e * 1.15 - 0.1);
  } else {
    const v = (p - SPLIT) / (1 - SPLIT), e = v * (2 - v), s = 1.5 - 0.5 * e;
    g = RB.getContext('2d'); drawRoomFrame(t, still); g = main;
    R(0, 0, W, H, '#05060e');
    main.drawImage(RB, Math.round(W / 2 - W / 2 * s), Math.round(H / 2 - H / 2 * s), Math.round(W * s), Math.round(H * s));
    dim(1 - e);
  }
}
function render(t, still) {
  g = main;
  if (RS.mode === 'town' || !RS.id) { draw(t, still); return; }
  let p = 1;
  if (RS.mode !== 'room') {
    const u = RS.frozen !== null ? RS.frozen : Math.min(1, (performance.now() - RS.t0) / ZOOM_MS);
    p = RS.mode === 'in' ? u : 1 - u;
    if (u >= 1 && RS.frozen === null) {
      if (RS.mode === 'in') RS.mode = 'room';
      else { finishLeave(); g = main; draw(t, still); return; }
    }
  }
  if (p >= 1) drawRoomFrame(t, still); else drawZoom(p, t, still);
  g = main;
}

/* ---------- mouse + keyboard inside a room ---------- */
function toLogical(ev) { const r = cvs.getBoundingClientRect(); return [Math.floor((ev.clientX - r.left) / r.width * W), Math.floor((ev.clientY - r.top) / r.height * H)]; }
stage.addEventListener('mousemove', ev => {
  if (!RS.id) return;
  [RS.mx, RS.my] = toLogical(ev); RS.hover = RS.mode === 'room' ? targetAt(RS.mx, RS.my) : null; RS.focus = -1; roomHover(RS.hover);
  stage.style.cursor = RS.hover ? 'pointer' : 'default'; kick();
});
stage.addEventListener('mouseleave', () => { if (RS.id) { RS.hover = null; if (RS.focus < 0) roomHover(null); kick(); } });
// in town: the tooltip of the thing under the pointer (a still scene redraws only when that thing changes)
const tipKey = tp => (tp ? tp.tip.join('|') : '');
stage.addEventListener('mousemove', ev => {
  if (RS.id) return;
  [TH.x, TH.y] = toLogical(ev);
  if (!running && tipKey(tipAt(TH.x, TH.y)) !== tipKey(TIP_ON)) kick();
});
stage.addEventListener('mouseleave', () => { if (RS.id || TH.x < 0) return; TH.x = TH.y = -1; if (!running && TIP_ON) kick(); });
stage.addEventListener('click', ev => {
  if (!RS.id || !panel.hidden) return;          // an open card: the page's click-outside closes it
  ev.stopPropagation();
  if (RS.mode !== 'room') return;
  const [x, y] = toLogical(ev); useTarget(targetAt(x, y));
});
function roomKey(e) {
  if (RS.mode !== 'room') return;
  SFX.unlock();
  if (e.key === 'Tab') {
    const n = allTargets().length; if (!n) return; e.preventDefault();
    RS.focus = RS.focus < 0 ? (e.shiftKey ? n - 1 : 0) : (RS.focus + (e.shiftKey ? -1 : 1) + n) % n;
    RS.hover = null; const tg = allTargets()[RS.focus]; roomHover(tg); if (tg) announce(tg.label || (tg.tip || []).join('. ')); kick();
  } else if ((e.key === 'Enter' || e.key === ' ') && RS.focus >= 0) { e.preventDefault(); useTarget(allTargets()[RS.focus]); }
  else if (e.key === 'm' || e.key === 'M') toggleMute();
  else if (e.key === 'i' || e.key === 'I') openCard();
  else if (RS.room.key) { safe(() => RS.room.key(e)); kick(); }
}

/* the kit rooms draw with: kit.js's drawing half plus the town's navigation half, frozen (docs/rooms.md) */
const K = (() => {
  const k = Object.assign({}, KIT, {
    toast, nowMs,
    leave: () => leaveRoom(), card: openCard,
    open: href => { if (typeof href === 'string' && /^(https?|file):/i.test(href)) window.open(href, '_blank', 'noopener'); },
    part: id => PT[id] || null,
  });
  Object.defineProperty(k, 'still', { get: stillMode, enumerable: true });
  Object.defineProperty(k, 'demo', { get: () => qs.get('demo') === '1', enumerable: true });
  return Object.freeze(k);
})();

/* ---------- run ---------- */
const FIXED = qs.has('t') ? parseFloat(qs.get('t')) : null;   // ?t=<seconds>: freeze at that moment (screenshots)
const T0 = performance.now(), OFF = T_START;
let raf = 0, tmr = 0, lastDraw = -1e9, running = false, kickT = 0;
function frame() { const now = performance.now(); lastDraw = now; render((now - T0) / 1000 + OFF, false); }
const FRAME_MS = 50;  // 20 fps cap: plenty for pixel art, and about a third of the CPU of drawing at 60
function loop(ts) { if (ts - lastDraw >= FRAME_MS - 4) frame(); raf = requestAnimationFrame(loop); }
function start() {
  cancelAnimationFrame(raf); clearInterval(tmr); running = false;
  const still = stillMode();
  if (still || FIXED !== null) { // static scene; redrawn once a minute, as the sky, the lights and a due bell follow the clock
    render(FIXED !== null ? FIXED : OFF, still);
    tmr = setInterval(() => { if (!document.hidden && !RS.id) kick(); }, 60000);
    return;
  }
  running = true;
  raf = requestAnimationFrame(loop);
  // backup clock for when no frames are being produced (headless capture); idle while rAF runs,
  // and while the page is hidden (a minimised app window draws nothing)
  tmr = setInterval(() => { if (!document.hidden && performance.now() - lastDraw > 60) frame(); }, 33);
}
// a still scene (reduced motion, ?t=) redraws only when something changed; a busy room keeps it at 10 fps
function kick() {
  if (running || kickT) return;
  kickT = setTimeout(() => {
    kickT = 0; render(FIXED !== null ? FIXED : OFF, stillMode());
    if (RS.id && (RS.mode !== 'room' || RS.toast || (RS.room.busy && safe(() => RS.room.busy(), false)))) setTimeout(kick, 100);
  }, 0);
}
function bootRooms() { // runs after the room scripts have registered
  syncHits();   // the buttons of buildings with a room now say so
  startLive();
  const qr = qs.get('room'), hr = /^#room=([\w-]+)(?:&open=([\w-]+))?/.exec(location.hash);
  const id = qr && roomOf(qr) ? qr : hr && roomOf(hr[1]) ? hr[1] : null;
  if (!id) return;
  const z = qs.has('zoom') ? Math.max(0, Math.min(0.999, parseFloat(qs.get('zoom')) || 0)) : null; // ?zoom=0.4: freeze the walk-in there
  enterRoom(id, { instant: true, history: false });
  if (z !== null) { RS.mode = 'in'; RS.frozen = z; }
  if (hr && hr[2] && plotOf(hr[2])) openPanel(plotOf(hr[2]), stage);
  kick();
}

/* ======================= LIVE DATA: the town changes in place, never by reloading =======================
   A Watcher (the desktop app, or `lamptown watch`) writes two small scripts next to town.html:
     town-live.js   lamptownLive({spec: 'lamptown-live/1', at, rev, need, agents, projects, ...}): rewritten when anything changes
                    and at least every ~10 s even when nothing does (a heartbeat); `at` = when it was written (ISO time);
     town-data.js   lamptownData(<the town data, as inlined in this page, with the same rev>), rewritten when that data changes.
   While the page shows, it adds a script element for town-live.js every 5 s (classic scripts load from file://; fetch does
   not), and not at all while it is hidden. The URL never changes: a file:// script is read from disk on every load, and a new
   URL per load would cost memory for good (Chrome keeps about 1 KB per script URL a page has loaded). After 3 misses in a
   row (no Watcher) it looks every 30 s instead, and every 5 s again once one lands or the page shows again.
   A payload whose rev differs from the data shown loads town-data.js, which swaps the data in place: no reload, so a walk in
   progress finishes. The corner says LIVE only while the last payload is fresh (its `at` under 30 s old by the real clock):
   a Watcher that stops leaves its file behind, but no heartbeat. No Watcher: the page keeps the data inlined at build time
   and the corner says BUILT.
   The app may call lamptownPoll() right after it writes, or lamptownData(D) / lamptownLive(p) itself. ?live=0: no polling. */
const LIVE_SPEC = 'lamptown-live/1', LIVE_FRESH_MS = 30000, LIVE_MISSES = 3;
function liveScript(name, done) {   // one script element for name (always the same URL); done(loaded, n, element) when it has run (or failed)
  const s = document.createElement('script'), n = ++LIVE.seq;
  let over = false;
  const end = ok => { if (over) return; over = true; clearTimeout(cap); s.remove(); done(ok, n, s); };
  const cap = setTimeout(() => end(false), 15000);   // neither load nor error came: give up on this one
  s.onload = () => end(true); s.onerror = () => end(false);
  s.dataset.live = name; s.src = name;
  document.head.appendChild(s);
  return n;
}
// how old a payload is by the real clock (not nowMs(), which ?at= pretends); no readable `at`: never fresh
const liveAge = p => { const t = p && typeof p.at === 'string' ? Date.parse(p.at) : NaN; return isNaN(t) ? Infinity : Date.now() - t; };
function setLive(on) {   // on: a Watcher answered; the corner says LIVE only while its payload is fresh, and turns BUILT when it goes stale
  const age = liveAge(LIVE.payload);
  on = !!on && age < LIVE_FRESH_MS && age > -LIVE_FRESH_MS;   // a time far ahead is a broken clock, not a heartbeat
  clearTimeout(LIVE.ageT); LIVE.ageT = on ? setTimeout(() => setLive(LIVE.on), LIVE_FRESH_MS - age + 50) : 0;
  if (LIVE.on !== on) { LIVE.on = on; kick(); }
}
function schedulePoll() {
  clearTimeout(LIVE.timer); LIVE.timer = 0; LIVE.next = 0;
  if (LIVE.off || document.hidden) return;   // hidden: none until it shows again
  LIVE.next = LIVE.miss >= LIVE_MISSES ? LIVE.slow : LIVE.every;
  LIVE.timer = setTimeout(lamptownPoll, LIVE.next);
}
function lamptownPoll() {
  clearTimeout(LIVE.timer); LIVE.timer = 0; LIVE.next = 0;
  if (LIVE.busy) return;   // one poll at a time; it schedules the next one when it ends
  LIVE.polls++;
  LIVE.busy = liveScript('town-live.js', (ok, n, s) => {
    LIVE.busy = null;
    const fed = ok && s.dataset.got === 'live';   // it loaded, and it really called lamptownLive
    if (!ok) LIVE.fails++;
    LIVE.miss = fed ? 0 : LIVE.miss + 1;
    setLive(fed);
    schedulePoll();
  });
}
function lamptownLive(p) {
  if (!p || typeof p !== 'object' || Array.isArray(p) || p.spec !== LIVE_SPEC) { console.warn('lamptown: not a ' + LIVE_SPEC + ' payload'); return false; }
  const s = document.currentScript;
  if (s && s.dataset.live === 'town-live.js') s.dataset.got = 'live';   // the poll's own file (not the app's push)
  LIVE.payload = p;
  setLive(true);
  if (typeof p.rev === 'string' && p.rev && p.rev !== LIVE.rev && p.rev !== LIVE.want && p.rev !== LIVE.bad) loadData(p.rev);
  kick();
  return true;
}
function loadData(rev) {
  LIVE.want = rev; LIVE.loads++;
  liveScript('town-data.js', (ok, n, s) => {
    if (LIVE.want === rev) LIVE.want = null;
    // it ran but brought nothing usable, or data without a rev: don't ask for this rev again.
    // It brought another rev (the Watcher is still writing it): the next poll asks again.
    if (ok && LIVE.rev !== rev && s.dataset.got !== 'other') LIVE.bad = rev;
  });
}
/* new town data. From town-data.js (the poll) it is used only when its rev is the one the page asked for, so an older file
   that lands late can't replace newer data; data without a rev is used but says nothing about which rev is shown.
   The app's own push (no script element) is always used. */
function lamptownData(d) {
  const s = document.currentScript, file = !!(s && s.dataset.live === 'town-data.js');
  const rev = d && typeof d.rev === 'string' && d.rev ? d.rev : null;
  if (file && rev && rev !== LIVE.want) { s.dataset.got = 'other'; return false; }
  if (!applyData(d)) return false;
  if (file) s.dataset.got = rev ? 'rev' : 'norev';
  if (rev || !file) LIVE.rev = rev;
  return true;
}
/* swap the town's data in place: everything read from D is read again; the scene, an open room and an open card follow */
function applyData(d) {
  try { d = checkData(d); } catch (e) { console.warn('lamptown: new town data ignored: ' + (e && e.message || e)); return false; }
  const lotsWere = ORDER.map(built).join();
  D = d; PT = D.parts; DATA_ERR = '';
  setTitle(); readSky();
  TRUTH = computeTruth();
  if (ORDER.map(built).join() !== lotsWere) for (const k of Object.keys(BGS)) delete BGS[k];   // empty lots and shadows are in the background
  syncHits(); srText();
  LIVE.swaps++;
  if (RS.id) updateRoom();
  if (!panel.hidden && openPlot) {
    if (PLOT[openPlot] || partOn(openPlot)) { fillPanel(openPlot); placePanel(openPlot); } else closePanel();
  }
  if (!running) render(FIXED !== null ? FIXED : OFF, stillMode());   // a still scene redraws now; a moving one on its next frame
  syncDetails();
  return true;
}
/* the open room after a swap: room.update(data) if it has one; else it walks in again quietly (no door, no zoom) on the
   new data and its own view starts over. A building that went away or changed its room type sends you back to town. */
function updateRoom() {
  const id = RS.id, room = roomOf(id), plot = plotOf(id);
  if (!room || room !== RS.room || !plot) { leaveRoom(); return; }
  RS.plot = plot;
  const data = roomData(id);
  if (JSON.stringify(data) === JSON.stringify(RS.data)) return;   // nothing new in this room
  if (typeof room.update === 'function') { RS.data = data; safe(() => room.update(data)); }
  else {
    try { room.leave && room.leave(); } catch (e) { console.error(e); }
    Object.assign(RS, { data, crash: null, hover: null, hoverKey: '' });
    try { room.enter && room.enter(data, roomCtx(id)); } catch (e) { RS.crash = e; console.error(e); }
  }
  if (RS.focus >= allTargets().length) RS.focus = -1;
}
function startLive() {
  if (LIVE.started) return;
  LIVE.started = true; LIVE.off = PREVIEW || qs.get('live') === '0';
  if (LIVE.off) return;
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(LIVE.timer); LIVE.timer = 0; LIVE.next = 0; }
    else { LIVE.miss = 0; lamptownPoll(); }   // showing again: look now, and every 5 s again
  });
  if (!document.hidden) lamptownPoll();
}

RM.addEventListener && RM.addEventListener('change', start);
fit(); start();
{ const hm = /^#open=([\w-]+)/.exec(location.hash); if (hm && plotOf(hm[1])) openPanel(plotOf(hm[1])); }
