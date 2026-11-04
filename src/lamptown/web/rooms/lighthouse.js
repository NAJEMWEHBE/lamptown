/* LIGHTHOUSE: the keeper's handover book, inside the live town's lighthouse.
   Data: `lamptownHistory(<lamptown-history/1>)` (town-history.js, written by the Watcher about once a minute; this
   script defines that function and loads the file with `K.liveScript` while you are inside and the page shows),
   plus the live payload for today (`K.live()`, lamptown-live/1: only while it is fresh, as the town's LIVE corner).
   The building's own room data (D.rooms.lighthouse) only carries its state.
   History: `history_days` local days (1..31, 7 by default), today last; each day has `minutes` (1440, or 1380 / 1500 on a DST day), `agents`
   (names only: "PROJECT / THREAD TITLE", tool, plot) and `events` as [minute of the local day, agent index, kind, n|ok].
   The room:
     - an open book, one line per agent in the order they came in: the name and tool on the left page, the status
       words on the right, written by the pen as things happen (IN, PUSHED, CI GREEN / CI RED, WAITING, DONE, FAILED,
       QUIET). Counts (files, commits) are written once, when the agent finishes, in the margin. 15 lines a leaf.
     - a tab per day (day names, today marked; up to 10 show at once), the brass clock that replays the day in about a minute (quiet days
       play faster: quiet hours fold away, busy minutes group into beats), a strip of the day's work to scrub, and a
       window onto the town that lights the plots as the replay goes.
     - the closing slip: what is still open, most urgent first (waiting for you, failed, gone quiet, working). Handed
       over at the end of a replay and when you open today; past days say "open at close", never "needs you".
   A line still open from an earlier day (open at that day's close, or live since before midnight) is carried:
   "FROM MON 22:30", and its times say their day ("WAITING SINCE MON 22:30"); its wait counts from the real start.
   Everything that moves is a real event: an idle book is still. Names only: never message text, files or paths.
   Keys: P play / pause, Left / Right scrub an hour, Home / End, Up / Down the day, PageUp / PageDown a leaf
   (never with Alt / Ctrl / Meta held: those are the browser's).
   Demo (?room=lighthouse&demo=1): a fictional SAMPLE week (a busy day of 40 agents, an empty day). */
(() => {
'use strict';
const { W, H, OL, ST, R, P, disc, glow, glowSp, textW, textL, textC, fitText, label, layer, sfx } = K;

const HIST_SPEC = 'lamptown-history/1', LIVE_SPEC = 'lamptown-live/1';
const DEF_DAYS = 7, MAX_DAYS = 31, TABS_VIS = 10, PER_LEAF = 15, MAX_AGENTS = 300, MAX_EVENTS = 4000, HIST_EVERY = 60000;
const KINDS = ['start', 'files', 'commit', 'push', 'ci', 'wait', 'answer', 'fail', 'quiet', 'done', 'project'];
const PLOTS = ['tower', 'library', 'gate', 'lab', 'workshop', 'boathouse'];
const DAYN = ['SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY'];
const MONN = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

/* ---------- layout (native pixels; the HUD has the top 12) ---------- */
const BX0 = 66, BX1 = 381, BY0 = 15, BY1 = 178;              // the book
const LP0 = 69, LP1 = 206, RP0 = 211, RP1 = 378;            // left page (names), right page (words)
const ROW0 = 39, RH = 7, NAME_X = LP0 + 6, TOOL_R = LP1 - 3, WORD_X = RP0 + 3, CNT_R = RP1 - 3, CNT_W = 30;
const SX0 = 74, SW = 300, SY = 166;                          // the day strip: baseline, 11 px of bars above
const LEAF_Y = 146;
const TABX = 381, TABY = 18, TABH = 16;
const CX = 32, CY = 87, CR = 13;                             // the brass clock
const PLQ = [5, 110, 54, 17];                                // its plaque (the replay button)
const WIN = [5, 17, 55, 46];                                 // the window onto the town
const KX = 14, KY = 131;                                     // the keeper
const C = {
  wall: '#1b1d36', mortar: '#13142a', brick: '#22254a', paper: '#b4bedc', paperD: '#a2acd0', gutter: '#7f8ab4', rule: '#8e9aca',
  ink: '#1c2350', ink2: '#4a5490', fresh: '#3a5fd0', wet: '#2b3f8f', red: '#a8202a', amber: '#94560a', cyan: '#11708a',
  grey: '#6a7090', green: '#21703c', cover: '#1f3346', coverL: '#30506a', brass: '#c9a24a', brassL: '#f2d47c', brassD: '#7a5a22',
  desk: '#5a3622', deskL: '#8a5a38', deskD: '#3a2214', hl: '#c9d3ef', sel: '#e3c27a', slip: '#c4cde8', foot: '#0d0f22',
};
const LANTERN = { work: ST.working.lamp, wait: ST.waiting.lamp, fail: ST.error.lamp, quiet: ST.missing.lamp, done: ST.ok.lamp, project: '#8a92c8', none: '#2c3352' };

/* ---------- small helpers ---------- */
const str = v => (v === null || v === undefined ? '' : String(v));
const p2 = n => (n < 10 ? '0' : '') + n;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const cnt = v => (typeof v === 'number' && isFinite(v) && v > 0 ? Math.min(1e9, Math.floor(v)) : 0);
const nowP = () => performance.now();
const DRAWN = new Set();                                     // every string this frame drew (tests: names only)
function tx(s, x, y, c) { s = String(s); if (s) { DRAWN.add(s); textL(s, x, y, c); } }
function txC(s, cx, y, c) { s = String(s); if (s) { DRAWN.add(s); textC(s, cx, y, c); } }
function big(s, x, y, c) { s = String(s).toUpperCase(); DRAWN.add(s); const l = label(s, c); g.imageSmoothingEnabled = false; g.drawImage(l, Math.round(x), Math.round(y) - l.oy * 2, l.width * 2, l.height * 2); }
/* names only: a path-looking word keeps just its last part (Python already cut them; this never lets one through).
   Path-looking: a drive or a leading / \ ~ ./, any backslash, or two or more slashes. "I/O" and "CI/CD" are titles. */
const BIDI = /[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g;   // direction overrides: a name can't flip the page
const pathy = w => /^([A-Z]:|\.{1,2})?[\\/~]/.test(w) || w.includes('\\') || (w.match(/\//g) || []).length >= 2;
function cleanName(s) {
  s = str(s).toUpperCase().replace(/[\x00-\x1f\x7f]/g, ' ').replace(BIDI, '');
  const out = s.split(' / ').map(part => part.split(/\s+/).map(w => (pathy(w) ? (w.split(/[\\/]+/).filter(Boolean).pop() || '') : w))
    .filter(w => w && !/^[A-Z]:$/.test(w)).join(' ')).filter(Boolean).join(' / ');
  return [...out].slice(0, 160).join('') || 'AGENT';
}
const toolTag = t => (str(t).toUpperCase().split(/[^A-Z0-9]+/).filter(Boolean)[0] || '').slice(0, 6);
function ymd(ms) { const d = new Date(ms); return d.getFullYear() + '-' + p2(d.getMonth() + 1) + '-' + p2(d.getDate()); }
function dateInfo(date) {
  const [y, m, d] = date.split('-').map(Number), mid = new Date(y, m - 1, d).getTime(), next = new Date(y, m - 1, d + 1).getTime();
  const wd = new Date(y, m - 1, d);
  return { mid, local: Math.round((next - mid) / 6e4), dow: wd.getDay(), dom: d, mon: m - 1 };
}
function addDays(date, n) { const [y, m, d] = date.split('-').map(Number); return ymd(new Date(y, m - 1, d + n, 12).getTime()); }
/* a minute of the day as the clock read it: real minutes since midnight, so on a DST day the wall clock is asked
   (when this browser is in a zone with the same day length). Otherwise a DST day (1380 / 1500 minutes) is read as if
   the clocks changed at 02:00, the usual hour: every time after the change reads right, and none reads 24:50. */
const DST_AT = 120;
function wallOf(day, m) { const d = day.minutes - 1440; return !d || m < DST_AT ? m : m - d; }
function minuteOfWall(day, w) { const d = day.minutes - 1440; if (!d || w < DST_AT) return w; if (d < 0 && w < DST_AT - d) return null; return w + d; }
function hm(day, m) {
  m = Math.max(0, Math.round(m));
  if (day.wall) { const d = new Date(day.mid + m * 6e4); return p2(d.getHours()) + ':' + p2(d.getMinutes()); }
  const w = clamp(wallOf(day, m), 0, 1440);
  return p2(Math.floor(w / 60)) + ':' + p2(w % 60);
}
function hourAt(day, m) { // the hour (0..23.99) the clock hand shows at minute m
  if (day.wall) { const d = new Date(day.mid + m * 6e4); return d.getHours() + d.getMinutes() / 60; }
  return (wallOf(day, m) / 60) % 24;
}
function dur(m) {
  m = Math.max(0, Math.round(m)); const d = Math.floor(m / 1440), h = Math.floor(m % 1440 / 60), mm = m % 60;
  return d ? d + 'D' + (h ? ' ' + h + 'H' : '') : h ? h + 'H' + (mm ? ' ' + mm + 'M' : '') : mm + 'M';
}
const DOW3 = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
/* a real moment (ms) on a line of `day`: hh:mm on that day, else with its day: "MON 22:30" (a week or more: "28 OCT 22:30") */
function whenText(day, ms) {
  const d = new Date(ms), t = p2(d.getHours()) + ':' + p2(d.getMinutes());
  if (ymd(ms) === day.date) return t;
  return (day.mid - ms < 6 * 864e5 ? DOW3[d.getDay()] : d.getDate() + ' ' + MONN[d.getMonth()]) + ' ' + t;
}

/* ---------- data in: the history payload, the live payload ---------- */
let HIST = null;          // the newest good lamptown-history/1 (kept between visits)
let SAMPLE = false;
function parseDay(d) {
  if (!d || typeof d !== 'object' || typeof d.date !== 'string' || !/^\d{4}-\d\d-\d\d$/.test(d.date)) return null;
  const minutes = Number.isInteger(d.minutes) && d.minutes >= 60 && d.minutes <= 1560 ? d.minutes : 1440;
  const agents = (Array.isArray(d.agents) ? d.agents : []).slice(0, MAX_AGENTS).map((a, i) => {
    a = a && typeof a === 'object' ? a : {};
    return { id: str(a.id).slice(0, 80) || 'agent-' + i, name: cleanName(a.name), tool: str(a.tool).slice(0, 64), plot: PLOTS.includes(a.plot) ? a.plot : 'boathouse', ev: [] };
  });
  for (const e of (Array.isArray(d.events) ? d.events : []).slice(0, MAX_EVENTS)) {
    if (!Array.isArray(e)) continue;
    const [m, ai, k, x] = e;
    if (typeof m !== 'number' || !isFinite(m) || !Number.isInteger(ai) || !agents[ai] || !KINDS.includes(k)) continue;
    const ev = { t: clamp(Math.floor(m), 0, minutes - 1), k };
    if (k === 'files') ev.n = cnt(x);
    else if (k === 'commit') ev.n = cnt(x) || 1;
    else if (k === 'ci') ev.ok = x === 1 || x === true;
    agents[ai].ev.push(ev);
  }
  for (const a of agents) a.ev.sort((p, q) => p.t - q.t);   // stable: same-minute events keep their order
  return { date: d.date, today: !!d.today, minutes, agents };
}
function lamptownHistory(p) {
  if (!p || typeof p !== 'object' || Array.isArray(p) || p.spec !== HIST_SPEC || !Array.isArray(p.days)) { console.warn('lamptown: not a ' + HIST_SPEC + ' payload'); return false; }
  const days = {};
  for (const d of p.days.slice(-MAX_DAYS)) { const pd = parseDay(d); if (pd) days[pd.date] = pd; }
  if (SAMPLE) return true;   // the demo keeps its sample week
  HIST = { at: str(p.at), days, n: clamp(p.days.length, 1, MAX_DAYS) };   // as many tabs as the log keeps days (history_days)
  HIST_N++;
  if (ACTIVE) { rebuild(); kickNow(); }
  return true;
}
window.lamptownHistory = lamptownHistory;
let HIST_N = 0;
const nDays = () => (HIST && HIST.n >= 1 ? HIST.n : DEF_DAYS);
// the live payload only while it is fresh (a stopped Watcher's file is old news: the town's corner says BUILT then)
const livePayload = () => { const p = K.live(); return p && p.spec === LIVE_SPEC ? p : null; };
const kickNow = () => K.redraw();

/* ---------- one agent's line at a moment: a pure function of its events up to minute T ----------
   An event may carry `at` (the real moment, ms) when it began before the day did (a carried line: its minute is 0),
   and a carried line starts with {k: 'start', carry: <first known moment>, day: true when only its day is known}.
   `syn` marks those carried-in events: they are no event of this day (not counted, not replayed as news). */
function lineState(a, T) {
  const s = { started: false, inT: 0, carry: 0, carryDay: false, files: 0, commits: 0, pushed: false, ci: null, ciT: 0, end: null, endT: 0, endAt: 0,
    waiting: false, waitT: 0, waitAt: 0, project: false, projT: 0, n: 0, lastT: 0, state: 'none' };
  for (const e of a.ev) {
    if (e.t > T) break;
    s.n++; s.lastT = e.t;
    if (e.k === 'project') { if (!s.started && !s.project) { s.project = true; s.projT = e.t; } continue; }
    if (!s.started) { s.started = true; s.inT = e.t; if (e.carry) { s.carry = e.carry; s.carryDay = !!e.day; } }
    switch (e.k) {
      case 'files': s.files += e.n; if (s.end === 'QUIET') s.end = null; break;
      case 'commit': s.commits += e.n; if (s.end === 'QUIET') s.end = null; break;
      case 'push': s.pushed = true; break;
      case 'ci': s.ci = e.ok; s.ciT = e.t; break;
      case 'wait': s.waiting = true; s.waitT = e.t; s.waitAt = e.at || 0; s.end = null; break;
      case 'start': case 'answer': s.waiting = false; s.end = null; break;
      case 'fail': s.end = 'FAILED'; s.endT = e.t; s.endAt = e.at || 0; s.waiting = false; break;
      case 'quiet': s.end = 'QUIET'; s.endT = e.t; s.endAt = e.at || 0; s.waiting = false; break;
      case 'done': s.end = 'DONE'; s.endT = e.t; s.endAt = e.at || 0; s.waiting = false; break;
    }
  }
  s.state = s.waiting ? 'wait' : s.end === 'DONE' ? 'done' : s.end === 'FAILED' ? 'fail' : s.end === 'QUIET' ? 'quiet' : s.started ? 'work' : s.project ? 'project' : 'none';
  return s;
}

/* ---------- a day's model: its lines (today: with the live payload folded in), its replay map, its strip ---------- */
const LIVE_KIND = { working: 'work', waiting: 'wait', error: 'fail', quiet: 'quiet', ok: 'done' };
function liveName(a, pname) {
  if (a.private) return 'PRIVATE AGENT';
  const proj = a.project ? (pname[a.project] || str(a.project).replace(/[-_]/g, ' ')) : '';
  return cleanName([proj, str(a.title)].filter(Boolean).join(' / '));
}
/* a line still open at the close of the last earlier logged day it was in (waiting, working or gone quiet) is carried
   into `date`: {first: its first known moment (ms), close: its state at that close, closeAt: since when (ms)}. It walks
   back over the days it stayed open, to the day it started. Closed there (done, failed): it starts afresh, no carry. */
function histIndex(days) { // agent id -> [[date, its agent that day, the day's minutes, midnight]], newest day first
  const idx = new Map();
  for (const date of Object.keys(days).sort().reverse()) {
    const pd = days[date], mid = dateInfo(date).mid;
    for (const pa of pd.agents) { if (!pa.ev.length) continue; let l = idx.get(pa.id); if (!l) idx.set(pa.id, l = []); l.push([date, pa, pd.minutes, mid]); }
  }
  return idx;
}
function carriedFrom(id, date, idx) {
  let out = null;
  for (const [pdate, pa, minutes, mid] of idx.get(id) || []) {
    if (pdate >= date) continue;
    const s = lineState(pa, minutes);
    if (s.state !== 'wait' && s.state !== 'work' && s.state !== 'quiet') break;
    if (!out) out = { close: s.state, closeAt: mid + (s.state === 'wait' ? s.waitT : s.state === 'quiet' ? s.endT : s.inT) * 6e4 };
    out.first = mid + pa.ev[0].t * 6e4;
    if (pa.ev[0].k === 'start') break;   // it began that day
  }
  return out;
}
function carryIn(day, idx) {
  for (const a of day.agents) {
    const c = carriedFrom(a.id, day.date, idx); if (!c) continue;
    const pre = [{ t: 0, k: 'start', carry: c.first, syn: true }];
    if (c.close === 'wait' || c.close === 'quiet') pre.push({ t: 0, k: c.close, at: c.closeAt, syn: true });
    a.ev = pre.concat(a.ev); a.carried = true;
  }
}
/* today: what the live payload knows that the history file (written once a minute) doesn't yet. Its state, a push or
   a CI result the line doesn't have becomes an event at its `since` (or now); its totals count at the finish.
   A state that began before today (`since` before midnight) is carried in at minute 0 with its real moment. */
function mergeLive(day, live, idx) {
  const byId = new Map(day.agents.map(a => [a.id, a]));
  const pname = {};
  for (const p of Array.isArray(live.projects) ? live.projects : []) if (p && typeof p.id === 'string') pname[p.id] = str(p.name || p.id);
  for (const la of (Array.isArray(live.agents) ? live.agents : []).slice(0, 200)) {
    if (!la || typeof la !== 'object' || typeof la.id !== 'string' || !LIVE_KIND[la.state]) continue;
    const want = LIVE_KIND[la.state], since = Date.parse(la.since);
    const sm = isNaN(since) ? day.end : Math.floor((since - day.mid) / 6e4), before = sm < 0;
    let a = byId.get(la.id);
    if (!a) {
      if (want === 'done' && before) continue;   // it finished before today began: not today's
      a = { id: la.id, name: liveName(la, pname), tool: str(la.tool).slice(0, 64), plot: PLOTS.includes(la.plot) ? la.plot : 'boathouse', ev: [], live: true };
      day.agents.push(a); byId.set(la.id, a);
      const c = before ? carriedFrom(la.id, day.date, idx) : null;
      if (c) a.ev.push({ t: 0, k: 'start', carry: Math.min(c.first, since), syn: true });
      else if (before) a.ev.push({ t: 0, k: 'start', carry: since, day: true, syn: true });   // only its state's day is known
      else a.ev.push({ t: clamp(sm, 0, day.end), k: 'start' });
      if (before) a.carried = true;
    }
    const s = lineState(a, day.end), t = clamp(Math.max(sm, s.lastT), 0, day.end), syn = before && t === 0;
    if (la.pushed && !s.pushed) a.ev.push({ t, k: 'push', syn });
    if ((la.ci === 'green' || la.ci === 'red') && s.ci !== (la.ci === 'green')) a.ev.push({ t, k: 'ci', ok: la.ci === 'green', syn });
    if (s.state !== want) a.ev.push({ t, k: want === 'work' ? (s.state === 'wait' ? 'answer' : 'start') : want, at: syn ? since : 0, syn });
    a.liveFiles = cnt(la.files); a.liveCommits = cnt(la.commits);
  }
}
function buildDay(date, hd, live, isToday, idx) {
  const info = dateInfo(date), minutes = hd ? hd.minutes : info.local;
  const day = { date, today: isToday, minutes, mid: info.mid, wall: info.local === minutes, dow: info.dow, dom: info.dom, mon: info.mon,
    name: DAYN[info.dow], tab: DAYN[info.dow].slice(0, 3), logged: !!hd,
    agents: hd ? hd.agents.map(a => Object.assign({}, a, { ev: a.ev.slice() })) : [] };
  const last = day.agents.reduce((m, a) => Math.max(m, a.ev.length ? a.ev[a.ev.length - 1].t : 0), 0);
  day.end = isToday ? clamp(Math.max(Math.floor((K.nowMs() - info.mid) / 6e4), last), 0, minutes) : minutes;
  carryIn(day, idx);
  if (isToday && live) mergeLive(day, live, idx);
  day.agents = day.agents.filter(a => a.ev.length);
  day.agents.forEach((a, i) => { a.ord = i; });
  day.agents.sort((p, q) => p.ev[0].t - q.ev[0].t || p.ord - q.ord);   // the order they came in: a started line is always above an unstarted one
  day.nAgents = day.agents.filter(a => a.ev.some(e => e.k !== 'project')).length;
  day.nProjects = day.agents.length - day.nAgents;
  const evs = [];
  day.agents.forEach((a, i) => a.ev.forEach(e => { if (!e.syn) evs.push({ t: e.t, k: e.k, ok: e.ok, ai: i }); }));   // carried-in: no event of this day
  evs.sort((p, q) => p.t - q.t);
  day.evs = evs;
  buildMap(day); buildStrip(day);
  day.need = isToday ? openItems(day).W.length : 0;
  return day;
}
/* the replay: beats of grouped events, each 0.45..1.7 s, about 50 s for a busy day; the quiet time between them folds
   to 0.12..0.82 s. A quiet day is a few beats, so it simply plays faster. */
const BUD = 50, MINB = 0.45, MAXB = 1.7, END_S = 0.9;
function buildMap(day) {
  const evs = day.evs, end = Math.max(1, day.end);
  let w = 10, beats;
  for (;;) {
    beats = [];
    for (const e of evs) { const b = Math.floor(e.t / w), L = beats[beats.length - 1]; if (L && L.b === b) L.ev.push(e); else beats.push({ b, s: b * w, e: Math.min(b * w + w, end), ev: [e] }); }
    if (beats.length * MINB <= BUD || w >= 120) break;
    w = Math.round(w * 1.5);
  }
  let sw = 0;
  for (const b of beats) { b.na = new Set(b.ev.map(e => e.ai)).size; b.wt = 1 + 0.6 * (b.na - 1); sw += b.wt; }
  for (const b of beats) b.d = clamp(BUD * b.wt / sw, MINB, MAXB);
  const segs = []; let t = 0, m = 0;
  const gap = m1 => { const gm = m1 - m; if (gm <= 0) return; const d = 0.12 + 0.7 * Math.min(1, gm / 360); segs.push({ k: 'gap', t0: t, t1: t + d, m0: m, m1 }); t += d; m = m1; };
  for (const b of beats) { gap(b.s); const s0 = Math.max(m, b.s), e0 = Math.max(s0 + 0.01, b.e); segs.push({ k: 'beat', t0: t, t1: t + b.d, m0: s0, m1: e0, b }); t += b.d; m = e0; }
  gap(end);
  segs.push({ k: 'end', t0: t, t1: t + END_S, m0: Math.max(m, end), m1: Math.max(m, end) }); t += END_S;
  day.segs = segs; day.T = t; day.beatW = w; day.beats = beats;
}
function segAt(day, t) { for (const s of day.segs) if (t < s.t1) return s; return day.segs[day.segs.length - 1]; }
function dayMin(day, t) { const s = segAt(day, t), f = s.t1 > s.t0 ? clamp((t - s.t0) / (s.t1 - s.t0), 0, 1) : 1; return s.m0 + (s.m1 - s.m0) * f; }
function tOf(day, m) {
  for (const s of day.segs) if (s.k !== 'end' && m >= s.m0 && m < s.m1) return s.t0 + (m - s.m0) / (s.m1 - s.m0 || 1) * (s.t1 - s.t0);
  return m <= 0 ? 0 : day.T;
}
function buildStrip(day) { // 10-minute bins: how much happened, a failure, a wait
  const n = Math.ceil(day.minutes / 10), bins = [];
  for (let i = 0; i < n; i++) bins.push({ n: 0, f: 0, w: 0 });
  for (const e of day.evs) { const b = bins[Math.min(n - 1, Math.floor(e.t / 10))]; b.n++; if (e.k === 'fail' || (e.k === 'ci' && !e.ok)) b.f = 1; }
  for (const a of day.agents) {
    let ws = -1;
    for (const e of a.ev) { if (e.k === 'wait' && ws < 0) ws = e.t; else if (ws >= 0 && e.k !== 'files' && e.k !== 'commit' && e.k !== 'push' && e.k !== 'ci') { for (let m = ws; m < e.t; m += 10) bins[Math.min(n - 1, Math.floor(m / 10))].w = 1; ws = -1; } }
    if (ws >= 0) for (let m = ws; m <= day.end && m < day.minutes; m += 10) bins[Math.min(n - 1, Math.floor(m / 10))].w = 1;
  }
  day.bins = bins; day.maxn = Math.max(4, ...bins.map(b => b.n));
}

/* ---------- the open things at the end of a day: the slip, most urgent first ---------- */
/* when a state began, in minutes of the day (negative: before it, a carried line) and in words */
const minOf = (day, t, ms) => (ms ? (ms - day.mid) / 6e4 : t);
const atText = (day, t, ms) => (ms ? whenText(day, ms) : hm(day, t));
function openItems(day) {
  const G = { W: [], F: [], Q: [], R: [] };
  day.agents.forEach((a, i) => {
    const s = lineState(a, day.end), it = (k, what, at) => G[k].push({ i, id: a.id, k, name: a.name, what, at });
    if (s.state === 'wait') { const w0 = minOf(day, s.waitT, s.waitAt); it('W', 'WAITING ' + dur(day.end - w0), w0); }   // from when it really began
    else if (s.state === 'fail') it('F', 'FAILED ' + atText(day, s.endT, s.endAt), -minOf(day, s.endT, s.endAt));
    else if (s.ci === false && s.state !== 'project') it('F', 'CI RED ' + hm(day, s.ciT), -s.ciT);
    else if (s.state === 'quiet') it('Q', 'QUIET SINCE ' + atText(day, s.endT, s.endAt), minOf(day, s.endT, s.endAt));
    else if (s.state === 'work') it('R', 'WORKING SINCE ' + atText(day, s.inT, s.carry), minOf(day, s.inT, s.carry));
  });
  for (const k in G) G[k].sort((p, q) => p.at - q.at || p.i - q.i);   // longest wait first; newest failure first
  return G;
}
const GROUPS = [['W', 'NEEDS YOU', 'WAITING AT CLOSE', 'wait'], ['F', 'FAILED', 'FAILED', 'fail'], ['Q', 'GONE QUIET', 'GONE QUIET', 'quiet'], ['R', 'STILL WORKING', 'WORKING AT CLOSE', 'work']];
const SLIP_ROWS_MAX = 16;   // the slip's rows (titles, names, "+N MORE"), shared by its groups: one group gets them all
function slipShare(G) {
  const ks = GROUPS.map(([k]) => k).filter(k => G[k].length), n = {};
  for (const k of ks) n[k] = 1;
  const rows = () => ks.reduce((r, k) => r + 1 + n[k] + (n[k] < G[k].length ? 1 : 0), 0);
  for (let grew = true; grew;) {   // one more row for each group in turn, most urgent first, while they fit
    grew = false;
    for (const k of ks) { if (n[k] >= G[k].length) continue; n[k]++; if (rows() <= SLIP_ROWS_MAX) grew = true; else n[k]--; }
  }
  return n;
}
function slipOf(day) {
  const G = openItems(day), past = !day.today, groups = [], per = slipShare(G);
  for (const [k, now, close, st] of GROUPS) if (G[k].length) groups.push({ k, st, title: past ? close : now, items: G[k], shown: G[k].slice(0, per[k]), more: Math.max(0, G[k].length - per[k]) });
  const title = past ? 'OPEN AT CLOSE OF ' + day.name + ' ' + day.dom + ' ' + MONN[day.mon] : 'STILL OPEN, TODAY ' + hm(day, day.end);
  return { past, title, groups, items: [].concat(...groups.map(gp => gp.items)) };
}

/* ---------- the room's state ---------- */
let ACTIVE = false, DAYS = [], di = DEF_DAYS - 1, mode = 'idle', RPL = null, LINES = [], leaf = 0, leafHand = false, SLIP = null, LIVE_SEEN = null,
  histTimer = 0, drag = false, lastBeat = null, hoverId = '', STATIC = null, lastTick = 0, said = '', tab0 = 0, downAt = -1e9;
function rebuild() {
  const today = ymd(K.nowMs()), days = HIST ? HIST.days : {}, live = livePayload(), n = nDays(), idx = histIndex(days);
  LIVE_SEEN = live;
  const keepDate = DAYS[di] && DAYS[di].date;
  DAYS = [];
  for (let k = n - 1; k >= 0; k--) { const date = addDays(today, -k); DAYS.push(buildDay(date, days[date] || null, k === 0 ? live : null, k === 0, idx)); }
  const ni = keepDate ? DAYS.findIndex(d => d.date === keepDate) : -1;
  if (ni < 0) { selectDay(n - 1, true); return; }
  di = ni;
  if (mode === 'play' || mode === 'pause') return;   // a replay keeps the day it started with
  const was = LINES;
  setLines(DAYS[di]);
  carry(was);
  updateLines(DAYS[di].end, false);
  if (SLIP) SLIP.s = slipOf(DAYS[di]);
}
function setLines(day) {
  LINES = day.agents.map(a => ({ a, id: a.id, name: a.name, tool: toolTag(a.tool), runs: [], words: '', shown: 0, nshown: 0, wt: [], nt: [], counts: null, cntAt: -1e9, flash: -1e9, s: null }));
}
function carry(was) { // a rebuilt day keeps what the pen already wrote, so only the news is written again
  const old = new Map(was.map(L => [L.id, L]));
  for (const L of LINES) { const o = old.get(L.id); if (o) Object.assign(L, { runs: o.runs, words: o.words, shown: o.shown, nshown: o.nshown, wt: o.wt, nt: o.nt, counts: o.counts, cntAt: o.cntAt, flash: o.flash }); }
}
function wordsOf(day, s) {
  if (s.state === 'project') return [['NEW PROJECT ' + hm(day, s.projT), C.ink2]];
  if (!s.started) return [];
  // a carried line: from the day (and time) it was first seen, not "IN 00:00"
  const p = [[s.carry ? 'FROM ' + (s.carryDay ? whenText(day, s.carry).replace(/ [0-9]{2}:[0-9]{2}$/, '') : whenText(day, s.carry)) : 'IN ' + hm(day, s.inT), C.ink]];
  if (s.pushed && s.ci === null) p.push(['PUSHED', C.ink]);   // a CI word follows a push: it says that already (on every line alike)
  if (s.ci !== null) p.push([s.ci ? 'CI GREEN' : 'CI RED', s.ci ? C.green : C.red]);
  if (s.state === 'done') p.push(['DONE ' + atText(day, s.endT, s.endAt), C.green]);
  else if (s.state === 'fail') p.push(['FAILED ' + atText(day, s.endT, s.endAt), C.red]);
  else if (s.state === 'quiet') p.push(['QUIET SINCE ' + atText(day, s.endT, s.endAt), C.grey]);
  else if (s.state === 'wait') p.push(['WAITING SINCE ' + atText(day, s.waitT, s.waitAt), C.amber]);
  return p;
}
const WORD_W = RP1 - WORD_X - CNT_W - 4;
function fitWords(p) { // the line's words in one pen, shortened step by step until they fit the page
  const join = (parts, sep) => parts.map(x => x[0]).join(sep);
  const steps = [
    q => q,
    q => q.map(([w, c]) => [w.replace('WAITING SINCE', 'WAITING').replace('QUIET SINCE', 'QUIET'), c]),
    q => q.map(([w, c]) => [w.replace('CI GREEN', 'CI OK'), c]),
    q => q.map(([w, c]) => [w.replace(/^FROM (.+) [0-9]{2}:[0-9]{2}$/, 'FROM $1'), c]),
  ];
  let q = p, sep = ', ';
  for (const f of steps) { q = f(q); if (textW(join(q, sep)) <= WORD_W) break; }
  if (textW(join(q, sep)) > WORD_W) sep = ' ';
  const runs = []; let words = '';
  q.forEach(([w, c], i) => { if (i) { runs.push([sep, C.ink]); words += sep; } runs.push([w, c]); words += w; });
  if (textW(words) > WORD_W) { const cut = fitText(words, WORD_W); return { words: cut, runs: [[cut, C.ink]] }; }
  return { words, runs };
}
function countsOf(L, s) { if (s.state !== 'done' && s.state !== 'fail') return null; const f = Math.max(s.files, L.a.liveFiles || 0), c = Math.max(s.commits, L.a.liveCommits || 0); return { files: f, commits: c }; }
function updateLines(T, instant) {
  const day = shownDay(), t = nowP();
  for (const L of LINES) {
    const s = lineState(L.a, T); L.s = s;
    const fw = fitWords(wordsOf(day, s)), nameNow = s.started || s.project ? 1 : 0;
    if (!nameNow) { L.nshown = 0; L.nt = []; }
    if (fw.words !== L.words) {
      let k = 0; while (k < L.shown && k < fw.words.length && L.words[k] === fw.words[k]) k++;
      if (k < L.shown) { L.shown = k; L.wt.length = k; }
      if (L.words && fw.words) L.flash = t;
      L.words = fw.words; L.runs = fw.runs;
    }
    const c = countsOf(L, s), ck = c ? c.files + ',' + c.commits : '', ok = L.counts ? L.counts.files + ',' + L.counts.commits : '';
    if (ck !== ok) { L.counts = c; L.cntAt = instant ? -1e9 : t; }
    if (instant || K.still) { L.nshown = nameNow ? L.name.length : 0; L.nt = new Array(L.nshown).fill(-1e9); L.shown = L.words.length; L.wt = new Array(L.shown).fill(-1e9); L.cntAt = -1e9; }
  }
}
function pen(dt) { // the pen writes what is due: the name, then the words, faster when there is more to write
  if (K.still) return false;
  const t = nowP(); let busy = false;
  for (const L of LINES) {
    const due = (L.nshown < (L.s && (L.s.started || L.s.project) ? L.name.length : 0) ? L.name.length - L.nshown : 0) + (L.words.length - L.shown);
    if (due <= 0) continue;
    busy = true;
    let k = Math.max(1, Math.round((40 + due * 3) * dt));
    while (k > 0 && L.nshown < L.name.length && L.s && (L.s.started || L.s.project)) { L.nshown++; L.nt.push(t); k--; }
    while (k > 0 && L.shown < L.words.length && L.nshown >= L.name.length) { L.shown++; L.wt.push(t); k--; }
  }
  return busy;
}
const shownDay = () => (RPL ? RPL.day : DAYS[di]);
function curT() { return RPL ? dayMin(RPL.day, RPL.rt) : DAYS[di].end; }
function selectDay(i, quiet) {
  di = clamp(i, 0, DAYS.length - 1); mode = 'idle'; RPL = null; leaf = 0; leafHand = false; lastBeat = null;
  setLines(DAYS[di]); updateLines(DAYS[di].end, true);
  SLIP = null;
  if (DAYS[di].today) handSlip(!!quiet);   // opening today: the keeper hands over what is open now
  if (!quiet) sfx.click();
}
function handSlip(instant) { SLIP = { s: slipOf(DAYS[di]), t0: instant || K.still ? -1e9 : nowP() }; }
function startReplay(rt) {
  const day = DAYS[di];
  SLIP = null; RPL = { day, rt: clamp(rt || 0, 0, day.T), base: clamp(rt || 0, 0, day.T), from: nowP() };
  mode = 'play'; leafHand = false; lastBeat = null;
  setLines(day); updateLines(dayMin(day, RPL.rt), true); followLeaf(true);
  const ac = sfx.ctx(); if (ac) sfx.noise(ac.currentTime + 0.01, 0.12, 0.08, 1800, 'bandpass');
}
function pause() { if (mode !== 'play') return; advance(); if (!RPL || mode !== 'play') return; RPL.base = RPL.rt; mode = 'pause'; }
function resume() { if (mode !== 'pause') return; if (RPL.rt >= RPL.day.T) { startReplay(0); return; } RPL.from = nowP(); RPL.base = RPL.rt; mode = 'play'; }
function seekMin(m) { // scrub to a minute of the day (from idle: a paused replay at that moment)
  if (!RPL) { startReplay(0); mode = 'pause'; }
  const day = RPL.day; RPL.rt = RPL.base = clamp(tOf(day, clamp(m, 0, day.end)), 0, Math.max(0, day.T - END_S)); RPL.from = nowP();
  SLIP = null; updateLines(dayMin(day, RPL.rt), true); followLeaf(true);
}
function finish() {
  const date = RPL.day.date; RPL = null; mode = 'idle';
  const i = DAYS.findIndex(d => d.date === date); if (i >= 0) di = i;
  const was = LINES; setLines(DAYS[di]); carry(was); updateLines(DAYS[di].end, false);
  handSlip(false); followLeaf(true);
  const ac = sfx.ctx(); if (ac) sfx.noise(ac.currentTime + 0.01, 0.2, 0.1, 900, 'lowpass');
}
function leaves() { return Math.max(1, Math.ceil(LINES.length / PER_LEAF)); }
function followLeaf(force) { // the replay turns the leaf forward to where a new line starts, never back on its own
  if (leafHand && !force) return;
  let n = 0; for (const L of LINES) if (L.s && (L.s.started || L.s.project)) n++;
  const want = clamp(Math.floor(Math.max(0, n - 1) / PER_LEAF), 0, leaves() - 1);
  if (force) leaf = want; else if (want > leaf) leaf = want;
}
function turn(d) { const n = leaves(); if (n < 2) return; leaf = clamp(leaf + d, 0, n - 1); leafHand = true; const ac = sfx.ctx(); if (ac) sfx.noise(ac.currentTime + 0.01, 0.09, 0.07, 2600, 'bandpass'); }
/* the clock: time moves only while the replay plays */
function advance() {
  const t = nowP(), dt = clamp((t - (lastTick || t)) / 1000, 0, 0.25); lastTick = t;
  const live = livePayload();
  if (live !== LIVE_SEEN && !SAMPLE) rebuild();
  if (mode === 'play') {
    RPL.rt = RPL.base + (t - RPL.from) / 1000;
    if (RPL.rt >= RPL.day.T) { RPL.rt = RPL.day.T; finish(); }
    else {
      const T = dayMin(RPL.day, RPL.rt); updateLines(T, false); followLeaf(false);
      const sg = segAt(RPL.day, RPL.rt);
      if (sg.k === 'beat' && sg !== lastBeat) { lastBeat = sg; const ac = sfx.ctx(); if (ac) sfx.noise(ac.currentTime + 0.005, 0.035, 0.04, 3400, 'highpass'); }
    }
  }
  return pen(dt);
}

/* ---------- the demo: a made-up week (never real data) ---------- */
function rng(seed) { return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function sampleHistory() {
  const PROJ = ['PAPER KITE', 'MOSS ENGINE', 'TIDEBOOK', 'OTTER', 'PUFFIN', 'LANTERN', 'SALT ROAD', 'BRAMBLE', 'GULL POST', 'KELP', 'DRIFTWOOD', 'WREN'];
  const TIT = ['FIX SAVE BUG', 'TILE RENAME', 'CI CACHE', 'MAP IMPORT', 'SHOP PAGE', 'SOUND PASS', 'DOCS SWEEP', 'LOGIN FLOW', 'PERF PASS', 'ICON SET',
    'SEARCH BOX', 'RELEASE', 'TIDE TABLES', 'SAVE SLOTS', 'FONT SWAP', 'MENU MUSIC', 'CRASH LOG', 'SHADER FIX', 'INBOX SORT', 'NEW SKILL'];
  const SPEC = [{ n: 7, seed: 11, fates: ['done', 'fail', 'done', 'wait', 'done', 'done', 'quiet'] }, { n: 6, seed: 23 }, { n: 3, seed: 31, fates: ['done', 'quiet', 'done'] },
    { n: 0, seed: 41 }, { n: 40, seed: 53, busy: true }, { n: 8, seed: 67, fates: ['done', 'done', 'fail', 'done', 'quiet', 'done', 'wait', 'done'] },
    { n: 6, seed: 79, fates: ['done', 'wait', 'done', 'done', 'fail', 'run'] }];
  const today = ymd(K.nowMs()), days = [];
  SPEC.forEach((sp, k) => {
    const date = addDays(today, k - 6), isToday = k === 6, info = dateInfo(date), r = rng(sp.seed);
    const end = isToday ? clamp(Math.floor((K.nowMs() - info.mid) / 6e4), 0, info.local - 1) : info.local - 1, ags = [];
    for (let i = 0; i < sp.n; i++) {
      const proj = PROJ[(i * 5 + sp.seed) % PROJ.length], title = TIT[(i * 7 + sp.seed) % TIT.length];
      const fate = sp.fates ? sp.fates[i] : r() < 0.12 ? 'fail' : r() < 0.08 ? 'quiet' : r() < 0.1 ? 'wait' : 'done';
      let c = Math.round(sp.busy ? (r() < 0.55 ? 780 + r() * 120 : 450 + r() * 620) : 520 + r() * 620);
      if (isToday) c = Math.min(c, Math.max(0, end - 200));
      const ev = [[c, 'start']], nb = sp.busy ? 6 + Math.floor(r() * 10) : 2 + Math.floor(r() * 6);
      for (let b = 0; b < nb; b++) {
        c += Math.max(1, Math.round(sp.busy ? 2 + r() * 8 : 4 + r() * 30));
        ev.push([c, 'files', 1 + Math.floor(r() * (sp.busy ? 30 : 9))]);
        if (b % 3 === 2) ev.push([c + 1, 'commit', 1]);
        if (r() < (sp.busy ? 0.08 : 0.12)) { ev.push([c + 2, 'wait']); c += 6 + Math.round(r() * 60); ev.push([c, 'answer']); }
      }
      if (fate === 'quiet') ev.push([c + 40, 'quiet']);
      else if (fate === 'fail') ev.push([c + 3, 'fail']);
      else if (fate === 'wait') ev.push([c + 2, 'wait']);
      else if (fate === 'done') { c += 2; if (r() < 0.7) { ev.push([c, 'push']); if (r() < 0.6) ev.push([c + 4, 'ci', r() < 0.85 ? 1 : 0]); c += 4; } ev.push([c + 3, 'done']); }
      ags.push({ id: 'sample-' + k + '-' + i, name: proj + ' / ' + title, tool: i % 3 ? 'codex' : 'claude-code', plot: PLOTS[(i * 3 + 5) % 6], ev: ev.filter(e => e[0] <= end) });
    }
    ags.sort((p, q) => p.ev[0][0] - q.ev[0][0]);
    const events = [];
    ags.forEach((a, ai) => a.ev.forEach(e => events.push([e[0], ai, e[1]].concat(e.length > 2 ? [e[2]] : []))));
    events.sort((p, q) => p[0] - q[0]);
    days.push({ date, today: isToday, minutes: info.local, agents: ags.map(a => ({ id: a.id, name: a.name, tool: a.tool, plot: a.plot })), events });
  });
  return { spec: HIST_SPEC, at: new Date(K.nowMs()).toISOString(), days };
}

/* ---------- drawing: the still parts, once ---------- */
function brick(g2) {
  R(0, 0, W, H, C.wall);
  for (let y = 12; y < H; y += 8) { R(0, y, W, 1, C.mortar); const off = ((y / 8) | 0) % 2 ? 8 : 0; for (let x = -off; x < W; x += 16) R(x, y, 1, 8, C.mortar); }
  for (let y = 13; y < H; y += 8) for (let x = ((y / 8) | 0) % 2 ? 9 : 1; x < W; x += 16) R(x, y, 6, 1, C.brick);
}
function buildStatic() {
  STATIC = layer(W, H, () => {
    brick();
    // the window onto the bay and the town
    const [wx, wy, ww, wh] = WIN;
    R(wx - 2, wy - 2, ww + 4, wh + 4, '#3a2a22'); R(wx - 1, wy - 1, ww + 2, wh + 2, '#5a3f2c');
    R(wx, wy, ww, wh, '#0f1636'); R(wx, wy + 18, ww, 14, '#121b42');
    for (const [a, b] of [[8, 21], [15, 27], [24, 20], [37, 24], [44, 19], [57, 28], [21, 31], [35, 33]]) P(a, b, '#8fa0d0');
    disc(50, 24, 2, '#dfe6ff');
    R(wx, wy + 37, ww, wh - 37, '#163052'); for (let i = 0; i < 5; i++) R(wx + 3 + i * 11, wy + 40 + (i % 2) * 3, 5, 1, '#24507a');
    R(wx, wy + 33, ww, 4, '#232a3c'); R(wx, wy + 33, ww, 1, '#323b55');
    // the brass clock's face (the hands move)
    for (let r = 30; r > 8; r -= 6) { g.globalAlpha = 0.05; g.fillStyle = '#ffcf7a'; g.beginPath(); g.arc(CX + 0.5, CY + 0.5, r, 0, 7); g.fill(); }
    g.globalAlpha = 1;
    R(CX - 2, CY - CR - 4, 5, 3, C.brass); R(CX - 1, CY - CR - 5, 3, 1, C.brassL);
    for (let y = -CR; y <= CR; y++) for (let x = -CR; x <= CR; x++) {
      const d = Math.sqrt(x * x + y * y);
      if (d <= CR - 2.2) P(CX + x, CY + y, '#111833'); else if (d <= CR + 0.4) P(CX + x, CY + y, x + y < -6 ? C.brassL : x + y > 6 ? C.brassD : C.brass);
    }
    for (let h = 0; h < 12; h++) { const a = h / 12 * Math.PI * 2; P(Math.round(CX + Math.sin(a) * (CR - 4)), Math.round(CY - Math.cos(a) * (CR - 4)), h % 3 ? '#6a6040' : C.brass); }
    // the desk, the inkwell and the quill
    R(0, 176, W, 37, C.desk); R(0, 176, W, 2, C.deskL); R(0, 178, W, 1, C.deskD);
    for (let x = 0; x < W; x += 37) R(x, 184, 22, 1, '#4e2e1c'); for (let x = 18; x < W; x += 41) R(x, 197, 25, 1, '#4e2e1c');
    R(92, 192, 9, 8, '#141826'); R(93, 191, 7, 1, '#262c40'); R(94, 190, 5, 1, '#3a4058');
    for (let i = 0; i <= 8; i++) { P(96 + i, 189 - i, '#e6edf7'); P(97 + i, 189 - i, '#c3cede'); }
    // the book: cover, two pages, the gutter, the rules
    R(BX0, BY0, BX1 - BX0, BY1 - BY0, C.cover); R(BX0, BY0, BX1 - BX0, 1, C.coverL);
    for (const [x, y] of [[BX0, BY0], [BX1 - 3, BY0], [BX0, BY1 - 3], [BX1 - 3, BY1 - 3]]) R(x, y, 3, 3, C.brass);
    R(LP0, BY0 + 3, LP1 - LP0, BY1 - BY0 - 6, C.paper); R(RP0, BY0 + 3, RP1 - RP0, BY1 - BY0 - 6, C.paper);
    R(LP1, BY0 + 3, RP0 - LP1, BY1 - BY0 - 6, C.gutter); R(LP1 - 2, BY0 + 3, 2, BY1 - BY0 - 6, C.paperD); R(RP0, BY0 + 3, 2, BY1 - BY0 - 6, C.paperD);
    R(LP0 + 2, 34, LP1 - LP0 - 4, 1, C.ink); R(RP0 + 3, 34, RP1 - RP0 - 5, 1, C.ink); R(LP0 + 2, 36, LP1 - LP0 - 4, 1, C.rule); R(RP0 + 3, 36, RP1 - RP0 - 5, 1, C.rule);
    for (let i = 0; i < PER_LEAF; i++) { const y = ROW0 + i * RH + 6; R(LP0 + 2, y, LP1 - LP0 - 4, 1, C.rule); R(RP0 + 3, y, RP1 - RP0 - 5, 1, C.rule); }
    R(CNT_R - CNT_W - 1, ROW0 - 2, 1, PER_LEAF * RH + 1, '#99a4cc');   // the margin the counts go in
    R(SX0 - 1, SY - 13, SW + 2, 22, '#a7b1d6'); R(SX0 - 1, SY - 13, SW + 2, 1, '#97a2cc');
    // the legend on the desk
    const x = 268, y = 181; R(x, y, 129, 30, '#121427'); R(x, y, 129, 1, '#3a3f6b');
    [['work', 'WORKS'], ['wait', 'WAITS'], ['fail', 'FAILED']].forEach(([k, w], i) => { R(x + 4 + i * 42, y + 4, 3, 3, LANTERN[k]); textL(w, x + 9 + i * 42, y + 3, '#c8cee6'); });
    [['quiet', 'QUIET'], ['done', 'DONE']].forEach(([k, w], i) => { R(x + 4 + i * 42, y + 11, 3, 3, LANTERN[k]); textL(w, x + 9 + i * 42, y + 10, '#c8cee6'); });
    crate(x + 88, y + 10, '#c8a060'); textL('FILES', x + 94, y + 10, '#c8cee6');
    seal(x + 4, y + 18, '#d0605a'); textL('COMMITS, COUNTED WHEN DONE', x + 10, y + 17, '#8f98bf');
    R(x + 4, y + 25, 2, 3, '#9fb0d8'); R(x + 6, y + 23, 2, 5, '#9fb0d8'); textL('STRIP = THE DAY HOUR BY HOUR', x + 10, y + 23, '#8f98bf');
  });
}
function crate(x, y, c) { R(x, y, 5, 4, c); R(x, y, 5, 1, '#e6c88a'); R(x + 2, y + 1, 1, 3, '#7a5a30'); }
function seal(x, y, c) { R(x + 1, y, 3, 5, c); R(x, y + 1, 5, 3, c); P(x + 2, y + 2, '#f0c0b0'); }

/* ---------- drawing: what moves ---------- */
function plotLamps(day, T) { // the worst live state per plot at minute T, and the agents standing there
  const rank = { none: 0, work: 1, quiet: 2, wait: 3, fail: 4 }, out = {};
  for (const p of PLOTS) out[p] = { st: 'none', n: [] };
  LINES.forEach(L => { const s = L.s; if (!s || !s.started || s.state === 'done' || s.state === 'project') return; const o = out[L.a.plot] || out.boathouse; if (rank[s.state] > rank[o.st]) o.st = s.state; o.n.push(s.state); });
  return out;
}
function drawWindow(day, T) {
  const B = [6, 15, 24, 33, 42, 51], sil = '#0a0f26', base = WIN[1] + 33, L = plotLamps(day, T), lamp = p => LANTERN[L[p].st];
  R(B[0] + 2, base - 21, 4, 21, sil); R(B[0] + 3, base - 23, 2, 2, sil); P(B[0] + 3, base - 24, sil); R(B[0] + 3, base - 18, 2, 1, lamp('tower'));
  R(B[1], base - 10, 8, 10, sil); R(B[1] + 1, base - 12, 6, 2, sil); R(B[1] + 2, base - 14, 4, 2, sil); R(B[1] + 3, base - 7, 2, 2, lamp('library'));
  R(B[2], base - 11, 2, 11, sil); R(B[2] + 6, base - 11, 2, 11, sil); R(B[2], base - 13, 8, 2, sil); R(B[2] + 3, base - 15, 2, 2, sil); R(B[2] + 3, base - 10, 2, 1, lamp('gate'));
  R(B[3], base - 8, 8, 8, sil); R(B[3] + 1, base - 10, 6, 2, sil); R(B[3] + 2, base - 12, 4, 2, sil); R(B[3] + 3, base - 6, 2, 1, lamp('lab'));
  R(B[4], base - 9, 8, 9, sil); for (const o of [0, 3, 6]) R(B[4] + o, base - 11, 2, 2, sil); R(B[4] + 3, base - 6, 2, 2, lamp('workshop'));
  R(B[5], base - 9, 9, 9, sil); R(B[5] + 1, base - 11, 7, 2, sil); R(B[5] + 3, base - 13, 3, 2, sil); R(B[5] + 4, base - 6, 1, 2, lamp('boathouse'));
  PLOTS.forEach((p, k) => L[p].n.slice(0, 3).forEach((st, j) => { const x = B[k] + j * 3, y = base + 1; R(x, y + 1, 2, 2, LANTERN[st]); R(x, y, 2, 1, st === 'quiet' ? '#a0a6bd' : '#e9d2b4'); }));
  R(WIN[0] + 27, WIN[1], 2, 33, '#5a3f2c');
}
function hand(len, a, c) { for (let i = 0; i <= len; i++) P(Math.round(CX + Math.sin(a) * i), Math.round(CY - Math.cos(a) * i), c); }
function drawClock(day, T) {
  const h = hourAt(day, T), playing = mode === 'play';
  hand(6, (h % 12) / 12 * Math.PI * 2, C.brassL);
  hand(10, (h % 1) * Math.PI * 2, RPL ? '#ffffff' : '#c8d0e8');
  P(CX, CY, C.brassL);
  if (RPL) txC(hm(day, T), CX, CY + CR + 3, '#c8d0e8');
  const lab = playing ? ['PAUSE', 'THE REPLAY'] : mode === 'pause' ? ['PLAY ON', 'FROM HERE'] : ['REPLAY', 'THE DAY'], [px, py, pw, ph] = PLQ, hot = hoverId === 'clock';
  R(px, py, pw, ph, C.brassD); R(px + 1, py + 1, pw - 2, ph - 2, hot ? C.brassL : C.brass); R(px + 1, py + 1, pw - 2, 1, C.brassL);
  for (const [a, b] of [[2, 2], [pw - 3, 2], [2, ph - 3], [pw - 3, ph - 3]]) P(px + a, py + b, C.brassD);
  txC(lab[0], px + pw / 2, py + 3, '#3a2a10'); txC(lab[1], px + pw / 2, py + 10, '#5a4218');
}
function drawKeeper(reach) {
  const kx = KX, ky = KY;
  R(kx + 3, ky, 12, 3, '#1d2a4a'); R(kx + 1, ky + 3, 16, 1, '#15203a'); R(kx + 8, ky + 1, 2, 1, C.brass);
  R(kx + 4, ky + 4, 10, 6, '#d9a77a'); P(kx + 6, ky + 6, '#2a1a10'); P(kx + 11, ky + 6, '#2a1a10');
  R(kx + 3, ky + 8, 12, 6, '#c9cfdc'); R(kx + 7, ky + 9, 4, 1, '#8a6a55'); R(kx + 4, ky + 14, 10, 1, '#c9cfdc');
  R(kx + 1, ky + 14, 16, 30, '#2c4f6e'); R(kx + 1, ky + 14, 1, 30, '#3b6488'); for (const yy of [17, 21, 25]) P(kx + 8, ky + yy, C.brass);
  R(kx - 1, ky + 16, 2, 14, '#244461');
  if (!reach) R(kx + 17, ky + 16, 2, 14, '#244461'); else { R(kx + 17, ky + 17, reach, 3, '#244461'); R(kx + 17 + reach, ky + 17, 3, 3, '#d9a77a'); }
}
/* the tabs: one per day of the log (history_days), up to TABS_VIS at once; the window follows the day you are on.
   More than a week: each tab also says its date (two Mondays). A notch above / below: older / newer days. */
function tabWindow() {
  const n = DAYS.length, v = Math.min(TABS_VIS, n);
  tab0 = clamp(tab0, 0, n - v);
  if (di < tab0) tab0 = di; else if (di >= tab0 + v) tab0 = di - v + 1;
  return [tab0, v];
}
const TAB_DOT = '#e6edf7';   // today's mark: a plain light dot, not the amber that means "waits"
function drawTabs() {
  const [t0, v] = tabWindow(), dated = DAYS.length > DEF_DAYS;
  for (let j = 0; j < v; j++) {
    const i = t0 + j, d = DAYS[i], y = TABY + j * TABH, on = i === di && !RPL || RPL && RPL.day.date === d.date, hot = hoverId === 'tab-' + i;
    const base = d.logged || d.today ? ['#3d5a7a', '#5a4a72', '#3d6a5a', '#6a5040', '#4a3d6a', '#3d5a7a', '#5a4a72'][i % 7] : '#2c3150';
    R(TABX, y, 16, TABH - 2, on ? C.paper : hot ? '#6a78a8' : base); R(TABX, y, 16, 1, on ? '#d0dae6' : 'rgba(0,0,0,0.25)');
    if (on) R(TABX - 3, y, 3, TABH - 2, C.paper);
    const ink = on ? C.ink : d.logged || d.today ? '#d6dcef' : '#7a80a0';
    if (dated) { txC(d.tab, TABX + 8, y + 2, ink); txC(String(d.dom), TABX + 8, y + 8, ink); } else txC(d.tab, TABX + 8, y + 5, ink);
    if (d.today) R(TABX + 13, y + 1, 2, 2, on ? C.ink : TAB_DOT);
  }
  const notch = (y, up, hot) => { const c = hot ? C.brassL : C.brass; for (let k = 0; k < 3; k++) R(TABX + 8 - k, up ? y + k : y + 2 - k, k * 2 + 1, 1, c); };
  if (t0 > 0) notch(TABY - 4, true, hoverId === 'tabs-older');
  if (t0 + v < DAYS.length) notch(TABY + v * TABH, false, hoverId === 'tabs-newer');
}
function lineWet(age, dry) { return age < 0.25 ? C.fresh : age < 1.4 ? C.wet : dry; }
function drawRun(runs, n, times, x, y) { // the first n characters of the runs, each fresh character in wet ink
  const t = nowP() / 1000; let k = 0;
  for (const [s, c] of runs) {
    if (k >= n) break;
    const m = Math.min(s.length, n - k), seg = s.slice(0, m);
    let wet = false; for (let j = 0; j < m; j++) if (t - times[k + j] / 1000 < 1.4) { wet = true; break; }
    // a script the pixel font lacks (Arabic, ...) is drawn whole (the kit's fallback shapes it, right to left): never letter by letter
    if (wet && /[^\x00-\x7f]/.test(seg)) { textL(seg, x, y, lineWet(t - Math.max(...times.slice(k, k + m)) / 1000, c)); x += textW(seg) + 1; }
    else if (!wet) { if (seg.trim()) textL(seg, x, y, c); x += textW(seg) + (m ? 1 : 0); }
    else for (let j = 0; j < m; j++) { const ch = seg[j]; if (ch !== ' ') textL(ch, x, y, lineWet(t - times[k + j] / 1000, c)); x += textW(ch) + 1; }
    k += m;
  }
  return x;
}
function drawLines(day) {
  const t = nowP() / 1000, first = leaf * PER_LEAF;
  for (let r = 0; r < PER_LEAF; r++) {
    const L = LINES[first + r]; if (!L) break;
    const y = ROW0 + r * RH, hi = hoverId === 'line-' + (first + r), sel = SEL && SEL.i === first + r && t < SEL.until;
    if (sel || hi || (RPL && t - L.flash / 1000 < 0.6)) { const c = sel ? C.sel : hi ? '#c6d0ec' : C.hl; R(LP0 + 1, y - 1, LP1 - LP0 - 2, 7, c); R(RP0 + 2, y - 1, RP1 - RP0 - 4, 7, c); }
    const s = L.s; if (!s || !(s.started || s.project)) continue;
    R(LP0 + 3, y + 1, 2, 3, LANTERN[s.state] || LANTERN.none);
    const toolW = L.tool ? textW(L.tool) + 4 : 0, name = fitText(L.name, TOOL_R - NAME_X - toolW);
    DRAWN.add(L.name.length > name.length ? name : L.name);
    const nx = drawRun([[name, C.ink]], Math.min(L.nshown, name.length), L.nt, NAME_X, y);
    if (L.tool && L.nshown >= L.name.length) tx(L.tool, TOOL_R - textW(L.tool), y, C.ink2);
    if (L.words) DRAWN.add(L.words);
    const wx = drawRun(L.runs, L.shown, L.wt, WORD_X, y);
    if (!K.still && L.nshown < L.name.length) nib(nx + 1, y); else if (!K.still && L.shown < L.words.length) nib(wx + 1, y);
    if (L.counts && (L.counts.files || L.counts.commits)) {
      const fresh = t - L.cntAt / 1000, col = lineWet(fresh, C.ink);
      let x = CNT_R;
      if (L.counts.commits) { const s2 = String(L.counts.commits); x -= textW(s2); tx(s2, x, y, col); x -= 7; seal(x, y, '#a8403a'); x -= 3; }
      if (L.counts.files) { const s1 = L.counts.files > 9999 ? Math.round(L.counts.files / 1000) + 'K' : String(L.counts.files); x -= textW(s1); tx(s1, x, y, col); x -= 7; crate(x, y + 1, '#a0783a'); }
    }
  }
}
function nib(x, y) { P(x, y + 4, C.brass); P(x + 1, y + 3, C.ink); P(x + 2, y + 2, C.ink); P(x + 3, y + 1, '#e6edf7'); P(x + 4, y, '#e6edf7'); P(x + 5, y - 1, '#e6edf7'); }
let SEL = null;
function drawHeader(day, T) {
  big(day.name, LP0 + 4, 20, C.ink);
  tx(day.dom + ' ' + MONN[day.mon] + (day.today ? ', TODAY' : ''), LP0 + 6 + textW(day.name) * 2 + 4, 25, C.ink2);
  const head = day.nAgents ? K.plural(day.nAgents, 'AGENT') + (day.nProjects ? ', ' + K.plural(day.nProjects, 'NEW PROJECT') : '') + ', ' + K.plural(day.evs.length, 'EVENT') : day.logged || day.today ? 'NO AGENTS THIS DAY' : 'NOT IN THE LOG';
  tx(fitText(head, RP1 - RP0 - 8), RP0 + 4, 20, C.ink);
  const sub = mode === 'play' ? 'REPLAYING, NOW ' + hm(day, T) : mode === 'pause' ? 'PAUSED AT ' + hm(day, T) : day.today ? 'SO FAR TODAY, ' + hm(day, day.end) : 'THE WHOLE DAY';
  tx(sub, RP0 + 4, 27, RPL ? C.wet : C.ink2);
  if (!LINES.length) {
    const say = !HIST && !SAMPLE ? ['NO LOG YET.', 'IT FILLS IN WHILE', 'LAMPTOWN WATCHES.'] : day.today ? ['NOTHING YET TODAY.', 'NEW AGENTS ARE', 'WRITTEN IN AS THEY COME.'] : ['A QUIET DAY.', 'NO AGENT WORKED.'];
    say.forEach((s, i) => txC(s, (LP0 + LP1) / 2, ROW0 + 22 + i * 8, C.ink2));
  }
}
function drawLeaf() {
  const n = leaves(); if (n < 2) return;
  const s = 'LEAF ' + (leaf + 1) + ' OF ' + n, cx = (RP0 + RP1) / 2;
  tx(s, cx - textW(s) / 2, LEAF_Y, C.ink2);
  if (leaf > 0) arrow(cx - textW(s) / 2 - 8, LEAF_Y, -1, hoverId === 'leaf-prev');
  if (leaf < n - 1) arrow(cx + textW(s) / 2 + 4, LEAF_Y, 1, hoverId === 'leaf-next');
  const more = LINES.length - (leaf + 1) * PER_LEAF;
  if (more > 0) tx('+' + more + ' MORE OVER THE LEAF', LP0 + 4, LEAF_Y, C.ink2);
}
function arrow(x, y, d, hot) { const c = hot ? C.fresh : C.ink; for (let i = 0; i < 3; i++) R(d > 0 ? x + i : x + 2 - i, y + i, 1, 5 - i * 2, c); }
const stripX = (day, m) => SX0 + Math.round(m / day.minutes * SW);
function drawStrip(day, T) {
  const bins = day.bins;
  for (let i = 0; i < bins.length; i++) {
    const b = bins[i], x0 = stripX(day, i * 10), x1 = Math.max(x0 + 1, stripX(day, Math.min(day.minutes, i * 10 + 10)) - 0);
    const past = !RPL || i * 10 <= T, future = day.today && i * 10 > day.end;
    if (b.n) { const h = Math.max(1, Math.round(Math.sqrt(b.n / day.maxn) * 11)); R(x0, SY - h, x1 - x0, h, past ? (b.f ? C.red : C.ink) : '#8f9ac6'); }
    if (b.w) R(x0, SY + 1, x1 - x0, 1, past ? ST.waiting.lamp : '#c9b08a');
    if (future) R(x0, SY - 11, x1 - x0, 12, 'rgba(120,130,180,0.18)');
  }
  for (let h = 0; h <= 24; h += 3) {   // hour marks at the clock's hours (a DST day has one more or one fewer)
    const m = h === 24 ? day.minutes : day.wall ? minuteOfHour(day, h) : minuteOfWall(day, h * 60); if (m === null || m > day.minutes) continue;
    const x = stripX(day, m); R(x, SY + 2, 1, 2, C.ink2); if (h < 24 && x < SX0 + SW - 8) tx(p2(h), x + 1, SY + 4, C.ink2);
  }
  if (RPL) { const x = stripX(day, T); R(x, SY - 13, 1, 17, C.brassD); R(x - 1, SY - 14, 3, 1, C.brassD); }
  else if (day.today) { const x = stripX(day, day.end); R(x, SY - 12, 1, 15, '#5a64a8'); }
}
function minuteOfHour(day, h) { if (h === 24) return day.minutes; const d = new Date(day.mid); d.setHours(h, 0, 0, 0); const m = Math.round((d.getTime() - day.mid) / 6e4); return m >= 0 && m < day.minutes ? m : null; }
function slipBoxNow() {
  if (!SLIP) return null;
  const s = SLIP.s, rows = s.groups.length ? s.groups.reduce((n, gp) => n + 1 + gp.shown.length + (gp.more ? 1 : 0), 0) : 1;
  const w = 214, h = 30 + rows * 7 + 12, fx = 126, fy = Math.max(16, 96 - (h >> 1));
  const p = clamp((nowP() - SLIP.t0) / 700, 0, 1), e = 1 - Math.pow(1 - p, 3);
  return { x: Math.round(KX + 20 + (fx - KX - 20) * e), y: Math.round(KY + 18 + (fy - KY - 18) * e), w, h, e, fx, fy };
}
let SLIP_ROWS = [];
function drawSlip() {
  const b = slipBoxNow(); SLIP_ROWS = []; if (!b) return;
  const s = SLIP.s, { x, y, w, h } = b;
  g.globalAlpha = 0.25 + 0.75 * b.e;
  R(x + 2, y + 2, w, h, 'rgba(5,6,16,0.55)');
  R(x, y, w, h, C.slip); for (let i = 0; i < w; i += 4) { R(x + i, y - 1, 2, 1, C.slip); R(x + i + 2, y + h, 2, 1, C.slip); }
  R(x + (w >> 1) - 2, y + 2, 5, 2, C.brass);
  tx(fitText(s.title, w - 12), x + 6, y + 7, C.ink);
  R(x + 6, y + 14, w - 12, 1, C.ink2);
  let yy = y + 19;
  if (!s.groups.length) { R(x + 6, yy + 1, 3, 3, LANTERN.done); tx(s.past ? 'NOTHING WAS OPEN AT CLOSE.' : 'NOTHING OPEN. ALL QUIET.', x + 12, yy, C.ink); yy += 7; }
  for (const gp of s.groups) {
    R(x + 6, yy + 1, 3, 3, LANTERN[gp.st]); tx(gp.title, x + 12, yy, C.ink); yy += 7;
    for (const it of gp.shown) {
      const id = 'slip-' + SLIP_ROWS.length;
      if (hoverId === id) R(x + 10, yy - 1, w - 16, 7, C.sel);
      const what = it.what, room = w - 30 - textW(what) - 6;
      tx(fitText(it.name, room), x + 16, yy, C.wet); tx(what, x + w - 8 - textW(what), yy, gp.st === 'wait' ? C.amber : gp.st === 'fail' ? C.red : C.ink2);
      SLIP_ROWS.push({ id, box: [x + 10, yy - 1, w - 16, 7], it }); yy += 7;
    }
    if (gp.more) { tx('+' + gp.more + ' MORE IN THE BOOK', x + 16, yy, C.ink2); yy += 7; }
  }
  const put = 'PUT IT DOWN', pw = textW(put) + 8, hot = hoverId === 'slip-down';
  tx(s.groups.length ? 'CLICK A NAME TO WATCH IT' : '', x + 6, y + h - 9, C.ink2);
  R(x + w - pw - 5, y + h - 11, pw, 9, hot ? C.brassL : C.brass); R(x + w - pw - 5, y + h - 11, pw, 1, C.brassL); tx(put, x + w - pw - 1, y + h - 9, '#3a2a10');
  SLIP.down = [x + w - pw - 5, y + h - 11, pw, 9]; SLIP.box = [x, y, w, h];
  g.globalAlpha = 1;
}
function caption(day, T) {
  if (SAMPLE && mode === 'idle' && !SLIP) return 'SAMPLE WEEK: MADE-UP NAMES. P REPLAYS THE DAY, UP / DOWN PICKS ONE.';
  if (SLIP && mode === 'idle') return day.today ? 'THE KEEPER HANDS YOU WHAT IS STILL OPEN, MOST URGENT FIRST.' : 'WHAT WAS STILL OPEN WHEN ' + day.name + ' CLOSED.';
  if (!RPL) return 'PRESS THE CLOCK (P) TO REPLAY THE DAY. DRAG THE STRIP TO SCRUB. CLICK A LINE TO SEE ITS MOMENT.';
  const sg = segAt(day, RPL.rt);
  let c;
  if (sg.k === 'gap' && sg.m1 - sg.m0 >= 60) c = 'QUIET FROM ' + hm(day, sg.m0) + ' TO ' + hm(day, sg.m1) + ', FOLDED AWAY';
  else if (sg.k === 'beat' && sg.b.ev.length >= 7) c = 'BUSY: ' + sg.b.ev.length + ' EVENTS, ' + K.plural(sg.b.na, 'AGENT') + ' IN ' + Math.max(1, Math.round(sg.m1 - sg.m0)) + ' MIN';
  else { let e = null; for (const x of day.evs) { if (x.t > T) break; e = x; } c = e ? desc(day, e) : 'NIGHT, NOTHING YET'; }
  return (mode === 'pause' ? 'PAUSED ' : 'REPLAY ') + day.name + ' ' + hm(day, T) + '.  ' + c;
}
const KDESC = { start: 'CAME IN', files: 'CHANGED FILES', commit: 'COMMITTED', push: 'PUSHED', wait: 'IS WAITING FOR YOU', answer: 'GOT YOUR ANSWER',
  fail: 'FAILED', quiet: 'WENT QUIET', done: 'IS DONE', project: 'IS A NEW PROJECT' };
function desc(day, e) { const a = day.agents[e.ai]; if (!a) return ''; return a.name + ' ' + (e.k === 'ci' ? (e.ok ? 'CI WENT GREEN' : 'CI WENT RED') : KDESC[e.k]); }
function drawFooter(day, T) {
  R(0, 213, W, 12, C.foot); R(0, 213, W, 1, '#2c3060');
  said = fitText(caption(day, T), W - 8);
  tx(said, 4, 217, RPL ? '#e8ecf8' : '#aab3d6');
}

/* ---------- pointing: targets, the strip drag ---------- */
function momentOf(a, kind) {
  const pick = f => a.ev.filter(f);
  let e;
  if (kind === 'W') e = pick(x => x.k === 'wait').pop();
  else if (kind === 'F') e = pick(x => x.k === 'fail' || (x.k === 'ci' && !x.ok))[0];
  else if (kind === 'Q') e = pick(x => x.k === 'quiet')[0];
  else e = pick(x => x.k === 'fail' || (x.k === 'ci' && !x.ok))[0] || pick(x => x.k === 'wait')[0] || pick(x => x.k === 'quiet')[0] || a.ev[0];
  return e || a.ev[0];
}
/* watch an agent's moment: by its id, so a line clicked during a replay still finds it after a live payload rebuilt the
   day (the newest copy of the day replays; its lines may have moved) */
function jump(id, kind) {
  const day = DAYS[di], i = day.agents.findIndex(a => a.id === id), a = day.agents[i]; if (!a) return;
  const e = momentOf(a, kind); startReplay(Math.max(0, tOf(day, e.t) - 0.35));
  SEL = { i, until: nowP() / 1000 + 3 };
  leaf = Math.floor(i / PER_LEAF); leafHand = true;
}
function stripMinute(x) { const day = shownDay(); return clamp((x - SX0) / SW * day.minutes, 0, day.end); }
function playPause() { if (mode === 'play') pause(); else if (mode === 'pause') resume(); else startReplay(0); }
function targets() {
  const day = shownDay(), T = curT(), out = [];
  if (SLIP && SLIP.box) {
    SLIP_ROWS.forEach(r => out.push({ id: r.id, box: r.box, tip: [r.it.name, r.it.what, 'CLICK TO WATCH IT HAPPEN'], label: r.it.name + ', ' + r.it.what, onClick: () => { const k = r.it; SLIP = null; jump(k.id, k.k); } }));
    out.push({ id: 'slip-down', box: SLIP.down, tip: ['PUT THE SLIP DOWN', 'THE BOOK STAYS OPEN (KEY S)'], label: 'Put the slip down', onClick: () => { SLIP = null; } });
    out.push({ id: 'slip', box: SLIP.box, tip: [SLIP.s.title, SLIP.s.items.length ? K.plural(SLIP.s.items.length, 'THING') + ' OPEN' : 'NOTHING OPEN'], label: SLIP.s.title });
  }
  out.push({ id: 'clock', box: [PLQ[0], CY - CR - 5, PLQ[2], PLQ[1] + PLQ[3] - (CY - CR - 5)], tip: mode === 'play' ? ['PAUSE THE REPLAY', 'KEY P'] : ['REPLAY THE DAY', 'ABOUT A MINUTE; QUIET DAYS PLAY FASTER (KEY P)'], label: mode === 'play' ? 'Pause the replay' : 'Replay the day', onClick: playPause });
  const [t0, v] = tabWindow();
  if (t0 > 0) out.push({ id: 'tabs-older', box: [TABX + 4, TABY - 5, 9, 5], tip: ['OLDER DAYS', 'UP KEY'], label: 'Older days', onClick: () => selectDay(t0 - 1) });
  for (let j = 0; j < v; j++) {
    const i = t0 + j, d = DAYS[i];
    out.push({ id: 'tab-' + i, box: [TABX, TABY + j * TABH, 16, TABH - 2], tip: [d.name + ' ' + d.dom + ' ' + MONN[d.mon] + (d.today ? ', TODAY' : ''), d.nAgents ? K.plural(d.nAgents, 'AGENT') : 'NO AGENTS'], label: d.name + ' ' + d.dom + ' ' + MONN[d.mon] + (d.today ? ', today' : ''), onClick: () => selectDay(i) });
  }
  if (t0 + v < DAYS.length) out.push({ id: 'tabs-newer', box: [TABX + 4, TABY + v * TABH - 1, 9, 5], tip: ['NEWER DAYS', 'DOWN KEY'], label: 'Newer days', onClick: () => selectDay(t0 + v) });
  const first = leaf * PER_LEAF;
  for (let r = 0; r < PER_LEAF; r++) {
    const L = LINES[first + r]; if (!L || !L.s || !(L.s.started || L.s.project)) continue;
    const tip = [L.name, (L.a.tool ? str(L.a.tool).toUpperCase() + ', ' : '') + 'AT THE ' + str(L.a.plot).toUpperCase(), L.words];
    if (L.counts && (L.counts.files || L.counts.commits)) tip.push(K.plural(L.counts.files, 'FILE') + ' CHANGED, ' + K.plural(L.counts.commits, 'COMMIT'));
    // the replay's day and the newest copy of it are the same day when their dates are (a live payload rebuilds it)
    out.push({ id: 'line-' + (first + r), box: [LP0, ROW0 + r * RH - 1, RP1 - LP0, RH], tip, label: L.name + ': ' + L.words, onClick: () => { if (!RPL || RPL.day.date === DAYS[di].date) jump(L.id); } });
  }
  if (leaves() > 1) {
    const s = 'LEAF ' + (leaf + 1) + ' OF ' + leaves(), cx = (RP0 + RP1) / 2;
    if (leaf > 0) out.push({ id: 'leaf-prev', box: [cx - textW(s) / 2 - 10, LEAF_Y - 1, 7, 7], tip: ['THE LEAF BEFORE', 'PAGE UP'], label: 'Previous leaf', onClick: () => turn(-1) });
    if (leaf < leaves() - 1) out.push({ id: 'leaf-next', box: [cx + textW(s) / 2 + 3, LEAF_Y - 1, 7, 7], tip: ['THE NEXT LEAF', 'PAGE DOWN'], label: 'Next leaf', onClick: () => turn(1) });
  }
  out.push({ id: 'strip', box: [SX0 - 1, SY - 13, SW + 2, 22], tip: ['THE DAY, HOUR BY HOUR', 'BARS: HOW MUCH HAPPENED. RED: A FAILURE. AMBER: WAITING FOR YOU', 'CLICK OR DRAG TO SCRUB'],
    // a mouse press on the strip has scrubbed already (onDown); Enter on the focused strip plays or pauses
    label: 'Day strip: scrub the replay with the arrow keys', onClick: () => { if (nowP() - downAt > 1500) playPause(); } });
  out.push({ id: 'window', box: WIN, tip: ['THE TOWN AT ' + hm(day, T), 'A LAMP PER PLOT: WHO WORKS WHERE AS THE DAY GOES'], label: 'The town through the window' });
  return out;
}
function onDown(ev) {
  if (!ACTIVE || !DAYS.length) return;
  const [x, y] = K.pointer(ev), sb = SLIP && SLIP.box;
  if (sb && x >= sb[0] && x < sb[0] + sb[2] && y >= sb[1] && y < sb[1] + sb[3]) return;
  if (x >= SX0 - 1 && x < SX0 + SW + 1 && y >= SY - 13 && y < SY + 9) { drag = true; downAt = nowP(); seekMin(stripMinute(x)); kickNow(); }
}
function onMove(ev) { if (!drag) return; const [x] = K.pointer(ev); seekMin(stripMinute(x)); kickNow(); }
function onUp() { drag = false; }
/* town-history.js next to the page (classic scripts load from file://), through the kit: not with ?live=0 or in a
   preview (K.liveScript says 0), and not while the page is hidden (a minimised app): once again when it shows */
function loadHistory() {
  if (SAMPLE || document.hidden) return;
  K.liveScript('town-history.js', () => {});
}
function onVisible() { if (ACTIVE && !document.hidden) loadHistory(); }

/* ---------- what the tests read ---------- */
function seenNow() {
  if (!DAYS.length) return { mode: 'out' };
  advance();
  const day = shownDay(), T = curT();
  return {
    sample: SAMPLE, mode, histLoads: HIST_N,
    day: { date: day.date, today: day.today, name: day.name, minutes: day.minutes, end: day.end, agents: day.nAgents, projects: day.nProjects, events: day.evs.length, logged: day.logged },
    tabs: DAYS.map(d => d.tab + (d.today ? '*' : '')), tab: di, tabWin: tabWindow(), leaf, leaves: leaves(),
    replay: RPL ? { rt: +RPL.rt.toFixed(3), T: +RPL.day.T.toFixed(3), minute: +T.toFixed(2), clock: hm(day, T), playing: mode === 'play' } : null,
    plans: DAYS.map(d => +d.T.toFixed(2)),
    lines: LINES.map(L => ({ id: L.id, name: L.name, tool: L.tool, state: L.s ? L.s.state : 'none', words: L.s && (L.s.started || L.s.project) ? L.words : '',
      written: L.words.slice(0, L.shown), counts: L.counts && (L.counts.files || L.counts.commits) ? L.counts : null })),
    slip: SLIP ? { title: SLIP.s.title, past: SLIP.s.past, groups: SLIP.s.groups.map(gp => gp.title), rows: SLIP.s.groups.map(gp => [gp.shown.length, gp.more]), items: SLIP.s.items.map(it => ({ k: it.k, name: it.name, what: it.what })) } : null,
    caption: said, drawn: [...DRAWN],
  };
}

/* ---------- the room ---------- */
registerRoom('lighthouse', {
  enter(data, opts) {
    ACTIVE = true; SAMPLE = !!opts.demo; lastTick = 0; drag = false; hoverId = ''; SEL = null; RPL = null; mode = 'idle'; DAYS = []; LINES = []; tab0 = 0; downAt = -1e9;
    if (SAMPLE) HIST = (() => { const p = sampleHistory(), days = {}; for (const d of p.days) { const pd = parseDay(d); days[pd.date] = pd; } return { at: p.at, days, n: DEF_DAYS }; })();
    if (!STATIC) buildStatic();
    rebuild();
    loadHistory();
    clearInterval(histTimer); histTimer = setInterval(loadHistory, HIST_EVERY);
    document.addEventListener('visibilitychange', onVisible);
    const st = document.getElementById('stage');
    if (st) { st.addEventListener('mousedown', onDown); st.addEventListener('mousemove', onMove); }
    window.addEventListener('mouseup', onUp);
  },
  update() { if (ACTIVE) rebuild(); },   // new town data (or a new live payload): the book keeps its page and writes only the news
  leave() {
    ACTIVE = false; clearInterval(histTimer); histTimer = 0; drag = false;
    document.removeEventListener('visibilitychange', onVisible);
    const st = document.getElementById('stage');
    if (st) { st.removeEventListener('mousedown', onDown); st.removeEventListener('mousemove', onMove); }
    window.removeEventListener('mouseup', onUp);
    if (SAMPLE) { HIST = null; SAMPLE = false; }
    RPL = null; mode = 'idle'; SLIP = null; LINES = []; DAYS = [];
  },
  draw() {
    DRAWN.clear();
    advance();
    const day = shownDay(), T = curT();
    g.drawImage(STATIC, 0, 0);
    drawWindow(day, T);
    drawClock(day, T);
    const reach = SLIP ? Math.round(4 + 6 * (slipBoxNow().e)) : 0;
    drawKeeper(reach);
    drawTabs(); drawHeader(day, T); drawLines(day); drawLeaf(); drawStrip(day, T);
    drawSlip();
    if (SAMPLE) { R(290, 1, 74, 10, OL); R(291, 2, 72, 8, '#3a2a10'); txC('SAMPLE WEEK', 327, 3, '#ffd36e'); }
    drawFooter(day, T);
  },
  targets,
  hover(tg) { hoverId = tg ? tg.id : ''; },
  plate() { const d = DAYS[DAYS.length - 1], n = d ? d.need : 0; return n ? { num: String(n), state: 'waiting' } : { num: '' }; },
  busy() {
    if (mode === 'play' || drag) return true;
    if (SLIP && nowP() - SLIP.t0 < 800) return true;
    const t = nowP();
    return LINES.some(L => L.shown < L.words.length || (L.s && (L.s.started || L.s.project) && L.nshown < L.name.length) || t - L.cntAt < 1500 ||
      (L.wt.length && t - L.wt[L.wt.length - 1] < 1500) || (L.nt.length && t - L.nt[L.nt.length - 1] < 1500));
  },
  key(e) {
    const k = e.key;
    if (e.altKey || e.ctrlKey || e.metaKey) return;   // Alt+Left is the browser's Back (out of the room), Ctrl+P prints
    if (k === 'p' || k === 'P') playPause();
    else if (k === 'ArrowLeft' || k === 'ArrowRight') { e.preventDefault(); seekMin(curT() + (k === 'ArrowRight' ? 60 : -60) * (e.shiftKey ? 3 : 1)); }
    else if (k === 'Home') { e.preventDefault(); seekMin(0); }
    else if (k === 'End') { e.preventDefault(); if (RPL) { RPL.rt = RPL.day.T; mode = 'play'; RPL.base = RPL.day.T; RPL.from = nowP(); advance(); } else handSlip(false); }
    else if (k === 'ArrowUp' || k === 'ArrowDown') { e.preventDefault(); selectDay(di + (k === 'ArrowDown' ? 1 : -1)); }
    else if (k === 'PageUp' || k === 'PageDown') { e.preventDefault(); turn(k === 'PageDown' ? 1 : -1); }
    else if (k === 's' || k === 'S') { if (SLIP) SLIP = null; else if (!RPL) handSlip(false); }
  },
  seen: seenNow,
  // tests: scrub to a minute of the shown day (a paused replay there), as the strip does
  seek(m) { seekMin(m); },
});
})();
