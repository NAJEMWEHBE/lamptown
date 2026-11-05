/* VAULT: the salt house, inside the library.
   Data: the building's room data from the `markdown_vault` collector (collectors/vault.py), read-only.
   - Drying lines: one line per vault folder (a shelf), one salted fish per note (hover: its name, click:
     open the folder). A line holds about 32 fish; past that every line ties its fish into bundles
     (1 BUNDLE = 5, 10, 20 ... NOTES, one unit for the whole wall so lines still compare), and the key sign
     under the beam says so. Up to 7 lines; more folders fold into one "+N MORE" line (the kept line
     always keeps its own).
   - The wet tray: the newest inbox note, one fresh fish per idea in it (5 slots, then "+N"), each with a
     tag: green tick = keep, red cross = drop, amber "?" = not marked yet. The age belongs to the whole
     tray: the ice melts, the water dulls, flies gather and the sign says "N DAYS OLD".
   - The cat: its belly is the dropped ideas (the dropped folder plus drops marked in this inbox; it stops
     growing after 6, the number under it keeps counting).
   - The slate: last and next review. Lantern = the room state (ok green: all sorted; waiting amber: ideas
     to sort; working cyan: the review is running, the brine barrel bubbles; error red: the review missed
     its run, red chalk across the slate; missing dark: no review on record, or no vault at all = empty
     pegs, no cat, an empty basket).
   Core moment: an idea marked drop slides off the tray and the cat eats it. With real data it plays once,
   the first time the room sees that real drop (remembered in localStorage). ?demo=1 replays it with
   SAMPLE ideas that say so, never as a real drop.
   Demo (?room=<id>&demo=1): fictional SAMPLE folders and ideas; &state=ok|waiting|working|error|missing
   (keys 1-5), &age=N days, &ideas=N sample ideas (1-12), &scale=N (N times the notes), &folders=N. */
(() => {
'use strict';
const { W, H, OL, ST, R, P, sprite, disc, ellipse, hash, mixHex, glow, glowSp, textW, textL, textC, fitText, layer, sfx, plural } = K;
const FLOOR = 181, DAY = 864e5, NOW_T = () => performance.now() / 1000;
const LX1 = 255, FSTEP = 6, BSTEP = 8;
let BW = 52, LX0 = 59, FX0 = 65, CAP = 32, BCAP = 24;   // board width, line start, fish start; 32 fish or 24 bundles a line (fewer with wide boards)
const MAX_LINES = 7;
const TRAY = { x: 270, y: 103, w: 126, slots: 5, sx: k => 275 + k * 24, fy: 106 };
const CAT = { cx: 230, floor: FLOOR };
const DOOR = [7, 148, 28, 33];
const SLATE = [296, 14, 100, 60];
const LANT = [281, 46];
const SIGN = [275, 79];
const BARREL = [312, 140, 24];
const SEEN_KEY = 'lamptown-vault-eaten';
const STATES = ['ok', 'waiting', 'working', 'error', 'missing'];
const COLS = [['#e8b46a', '#9a6a3a'], ['#a8c0ea', '#5c6e9a'], ['#6fd6c8', '#2f8a80'], ['#f0a0c0', '#a05a7a'], ['#c4a4ff', '#7a5cb8'],
  ['#e0d890', '#8a8240'], ['#ffb0a0', '#a8604e'], ['#a0e0ff', '#4a88a8']];
const KEPT_COL = ['#86e8a8', '#3a9a60'];
const SAY = { missed: 'MISSED ITS RUN', running: 'REVIEW RUNNING', never: 'NO REVIEW YET', nosource: 'VAULT NOT FOUND' };

const isoDay = ms => { const d = new Date(ms); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };
function dayStart(iso) { // local midnight of an ISO date or time
  if (!iso) return NaN; const d = new Date(/^\d{4}-\d\d-\d\d$/.test(iso) ? iso + 'T00:00:00' : iso);
  return isNaN(d) ? NaN : new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}
const daysSince = iso => { const a = dayStart(iso); return isNaN(a) ? 0 : Math.max(0, Math.round((dayStart(isoDay(K.nowMs())) - a) / DAY)); };

/* ---------- the view model: real data, or the demo's sample ---------- */
let RAW = null, V = null, LINES = [], UNIT = 1, STATIC = null, demo = null, timers = [], caption = '', roomId = '';
let STIR = false, ideas = [], fly = null, queue = [], gulpT = -9, belly = { from: 0, to: 0, t0: -9 }, lastDrip = 0;
const FISH = new Map(), TRAYF = new Map();

const SAMPLE_FOLDERS = [['field-notes', 'FIELD NOTES', 13], ['kites', 'KITES', 24], ['tides', 'TIDES', 16], ['recipes', 'RECIPES', 4],
  ['letters', 'LETTERS', 5]];
const SAMPLE_MORE = [['moss', 'MOSS'], ['herons', 'HERONS'], ['maps', 'MAPS'], ['shells', 'SHELLS'], ['knots', 'KNOTS'], ['lanterns', 'LANTERNS']];
const SAMPLE_NOTES = ['heron at dawn', 'low tide walk', 'kite string knots', 'fog bank', 'nettle soup', 'tail ribbons', 'paper kite frame', 'moss calendar'];
const SAMPLE_IDEAS = ['Tail ribbons for the paper kite', 'A moss calendar', 'Tide tables as a game board', "Otter's lighthouse sketch",
  'A heron-shaped weather vane', 'Glass floats for the pier', 'A kite that reads the wind', 'Seaweed paper', 'Fog horn tunes',
  'A map of every rock pool', 'Shell abacus', 'Lantern made of a jar'];
function sampleIdeas(n) {
  return Array.from({ length: n }, (_, i) => ({ n: i + 1, id: 'sample#' + (i + 1), title: 'Sample: ' + SAMPLE_IDEAS[i % SAMPLE_IDEAS.length], from: '', mark: null, reason: '' }));
}
function normFolder(f, i) {
  f = f && typeof f === 'object' ? f : {};
  const kept = !!f.kept, [col, dk] = kept ? KEPT_COL : f.more ? ['#b8b4c8', '#6a6680'] : COLS[i % COLS.length];
  return { key: String(f.key || ''), label: String(f.label || f.key || '').toUpperCase(), col, dk, kept,
    n: Math.max(0, parseInt(f.n, 10) || 0), notes: Array.isArray(f.notes) ? f.notes.map(String) : [], href: String(f.href || '') };
}
function mkView(data, opts) {
  const d = data && typeof data === 'object' ? data : {};
  const v = { state: ST[d.state] ? d.state : 'missing', why: String(d.why || ''), label: String(d.label || ''),
    folders: (Array.isArray(d.folders) ? d.folders : []).map(normFolder), notes: parseInt(d.notes, 10) || 0,
    inbox: d.inbox && typeof d.inbox === 'object' ? d.inbox : null, dropped: d.dropped && typeof d.dropped === 'object' ? d.dropped : { n: 0, names: [] },
    last: d.last_review || null, lastHref: String(d.last_review_href || ''), next: d.next_review || null, when: String(d.review_when || ''),
    logHref: String(d.log_href || ''), sample: false, scale: 1, vault: String(d.vault_name || '') };
  v.dropped = { n: parseInt(v.dropped.n, 10) || 0, names: Array.isArray(v.dropped.names) ? v.dropped.names.map(String) : [], href: String(v.dropped.href || '') };
  if (v.state === 'missing' && v.why !== 'never') v.why = v.why || 'nosource';
  if (!opts.demo) return v;
  // demo: every folder, note and idea is a fictional sample, and the room says so
  const q = opts.qs;
  v.folders = SAMPLE_FOLDERS.map(([key, label, n], i) => normFolder({ key, label, n, notes: SAMPLE_NOTES }, i));
  const want = Math.max(0, Math.min(14, parseInt(q.get('folders'), 10) || 0));
  for (let i = 0; v.folders.length < want - 1 && i < SAMPLE_MORE.length * 2; i++) {
    const [key, label] = SAMPLE_MORE[i % SAMPLE_MORE.length];
    v.folders.push(normFolder({ key: key + (i >= SAMPLE_MORE.length ? '-2' : ''), label, n: 3 + (i * 7) % 12, notes: SAMPLE_NOTES }, v.folders.length));
  }
  v.folders.push(normFolder({ key: 'kept', label: 'KEPT', kept: true, n: 2, notes: ['tail ribbons', 'kite tail'] }, 0));
  v.scale = Math.max(1, Math.min(40, parseInt(q.get('scale'), 10) || 1));
  if (v.scale > 1) v.folders.forEach(f => { f.n *= v.scale; });
  v.notes = v.folders.filter(f => !f.kept).reduce((a, f) => a + f.n, 0);
  v.sample = true; v.vault = 'SAMPLE NOTEBOOK'; v.when = 'Tuesdays 07:15';
  v.lastHref = v.logHref = ''; v.dropped = { n: 2, names: ['glass kite', 'moss paint'], href: '' };
  v.inbox = { date: isoDay(K.nowMs() - (q.has('age') ? Math.max(0, parseInt(q.get('age'), 10) || 0) : 1) * DAY), href: '', file: 'SAMPLE INBOX',
    items: sampleIdeas(Math.max(1, Math.min(12, parseInt(q.get('ideas'), 10) || 3))) };
  v.next = isoDay(K.nowMs() + 3 * DAY) + 'T07:15:00';
  force(v, demo.state);
  if (!q.has('age') && v.last) v.inbox.date = isoDay(dayStart(v.last));
  return v;
}
function force(v, st) {   // the demo's forced state (sample values, the slate says so in its tooltip)
  if (!STATES.includes(st)) st = 'ok';
  v.state = st;
  v.why = { ok: 'ok', waiting: 'inbox', working: 'running', error: 'missed', missing: 'nosource' }[st];
  v.last = st === 'error' ? isoDay(K.nowMs() - 10 * DAY) + 'T07:15:00' : st === 'missing' ? null : isoDay(K.nowMs() - DAY) + 'T07:15:00';
}
const gone = () => V.state === 'missing' && V.why === 'nosource';   // no vault folder at all
const say = st => SAY[V.why] || (st === 'ok' ? 'ALL SORTED' : ST[st].word.toUpperCase());

/* ---------- ideas on the tray ---------- */
function loadSeen() { try { const a = JSON.parse(localStorage.getItem(SEEN_KEY) || '[]'); return Array.isArray(a) ? a : []; } catch (e) { return []; } }
function saveSeen(id) { try { const a = loadSeen().filter(x => x !== id); a.push(id); localStorage.setItem(SEEN_KEY, JSON.stringify(a.slice(-200))); } catch (e) { /* not remembered */ } }
function setupIdeas() {
  fly = null; queue = []; gulpT = -9;
  const raw = V.inbox && Array.isArray(V.inbox.items) ? V.inbox.items : [];
  ideas = gone() ? [] : raw.map((d, i) => ({ n: parseInt(d.n, 10) || i + 1, id: String(d.id || i), title: String(d.title || 'Idea ' + (i + 1)),
    from: String(d.from || ''), mark: d.mark === 'keep' || d.mark === 'drop' ? d.mark : null, reason: String(d.reason || ''), slot: i, gone: false, markT: -9 }));
  if (V.sample) {   // the replay starts from the moment the review left fresh ideas; a settled state starts settled
    if (demo.state === 'ok' || demo.state === 'error') ideas.forEach(d => { d.mark = 'keep'; });
    if (demo.state === 'waiting' || demo.state === 'working') ideas.forEach((d, i) => { d.mark = i === 0 ? 'keep' : null; });
  } else {
    const seen = loadSeen();
    for (const d of ideas) if (d.mark === 'drop') { const k = roomId + ':' + d.id; if (seen.includes(k) || K.still) { d.gone = true; saveSeen(k); } else queue.push(d); }
  }
  const n = V.dropped.n + ideas.filter(d => d.gone).length;
  belly = { from: n, to: n, t0: -9 };
}
const untriaged = () => ideas.filter(d => !d.gone && !d.mark).length;
function roomState() {   // the replay moves the state with its marks; real data says it outright
  if (!V.sample || !(demo.state === 'ok' || demo.state === 'waiting')) return V.state;
  return untriaged() ? 'waiting' : 'ok';
}
const ageDays = () => (V.inbox && V.inbox.date ? daysSince(V.inbox.date) : 0);
function bellyNow() {
  const u = Math.min(1, Math.max(0, (NOW_T() - belly.t0) / 0.5));
  const e = u === 1 ? 1 : 1 + 2.2 * Math.pow(u - 1, 3) + 1.2 * Math.pow(u - 1, 2);   // ease out, a little overshoot
  return belly.from + (belly.to - belly.from) * e;
}
function markIdea(i, m) { const d = ideas[i]; if (!d) return; d.mark = m; d.markT = NOW_T(); play(m === 'keep' ? 'keep' : 'drop'); }
function slideIdea(d, done) { if (!d) return; fly = { idea: d, t0: NOW_T(), dur: 1.1, done }; play('slide'); }
function eatNext() {   // a REAL drop the room hasn't shown yet: it slides to the cat once, then it is remembered
  const d = queue.shift(); if (!d) return;
  slideIdea(d, () => { saveSeen(roomId + ':' + d.id); K.toast('IDEA ' + d.n + ' DROPPED: THE CAT ATE IT'); if (queue.length) timers.push(setTimeout(eatNext, 700)); });
}

/* ---------- lines: layout (folders past 7 fold into one "+N MORE" line) ---------- */
function mkLines() {
  let fs = V.folders.slice();
  if (fs.length > MAX_LINES) {
    const keptI = fs.findIndex(f => f.kept), kept = keptI >= 0 ? fs.splice(keptI, 1)[0] : null;
    const room = MAX_LINES - 1 - (kept ? 1 : 0), shown = fs.slice(0, room), rest = fs.slice(room);
    const more = normFolder({ key: rest.map(f => f.key).join(', '), label: '+' + rest.length + ' MORE', more: true, n: rest.reduce((a, f) => a + f.n, 0) }, 0);
    more.notes = [].concat(...rest.map(f => f.notes.map(x => f.label.toLowerCase() + ': ' + x))); more.more = rest;
    fs = shown.concat([more], kept ? [kept] : []);
  }
  BW = Math.min(72, Math.max(52, ...fs.map(f => 8 + textW(f.label) + 4 + textW(String(f.n)) + 3)));   // a 4-digit count widens every board
  LX0 = BW + 7; FX0 = LX0 + 6; CAP = Math.floor((LX1 - 3 - FX0) / FSTEP) + 1; BCAP = Math.floor((LX1 - 4 - FX0) / BSTEP) + 1;
  const sp = fs.length <= 1 ? 20 : Math.min(20, Math.floor(102 / (fs.length - 1)));
  LINES = fs.map((f, i) => Object.assign({}, f, { ly: 33 + i * sp }));
  let u = 1; const mx = Math.max(0, ...LINES.map(f => f.n));
  while (Math.ceil(mx / u) > (u === 1 ? CAP : BCAP)) u = u === 1 ? 5 : u * 2;
  UNIT = u;
}
const lineY = (ly, x) => ly + Math.round(2 * Math.sin(Math.PI * (x - LX0) / (LX1 - LX0)));

/* ---------- fish sprites (cached: one small canvas per colour, kind and sway) ---------- */
function hangFish(x, y, col, dk, small, dx, salt) {   // hung by the tail, head down
  P(x, y, '#c9a070');
  const M = mixHex(col, '#ffffff', 0.3), map = { l: col, m: M, d: dk, s: salt ? '#f4f2ff' : M, e: dk };
  const rows = small ? ['d.d', '.d.', 'lmd', 'lmd', '.m.'] : ['d...d', '.d.d.', '..d..', '.lmd.', 'llmdd', '.lmd.', '.lsd.', '.lmd.', '.lmd.', '..e..'];
  const ox = small ? x - 1 : x - 2;
  rows.forEach((r, j) => { const sh = j >= 3 ? dx : 0; for (let i = 0; i < r.length; i++) { const c = map[r[i]]; if (c) P(ox + i + sh, y + 1 + j, c); } });
  if (!small) P(x + 1 + dx, y + 9, OL);
}
function fishSp(col, dk, kind, dx) {
  const k = col + dk + kind + dx; let c = FISH.get(k);
  if (!c) {
    c = layer(13, 13, () => {
      if (kind === 'bundle') {   // three fish tied at the tails: reads as "many"
        const bc = mixHex(col, '#1b1730', 0.45), bd = mixHex(dk, '#1b1730', 0.45);
        hangFish(4, 1, bc, bd, false, dx, false); hangFish(8, 1, bc, bd, false, dx, false); hangFish(6, 0, col, dk, false, dx, true); R(4, 2, 5, 1, '#c9a070');
      } else hangFish(6, 0, col, dk, kind === 'small', dx, true);
    });
    FISH.set(k, c);
  }
  return c;
}
function trayFish(x, y, age) {   // a fresh fish lying on the tray; it dulls with the days
  const k = Math.min(7, age); let c = TRAYF.get(k);
  if (!c) {
    c = layer(19, 9, () => {
      const u = k / 7, ox = 1, oy = 1;
      const top = mixHex('#b8c8ff', '#7a7a62', u), mid = mixHex('#e4ecff', '#a09c80', u), bel = mixHex('#f4f6ff', '#b8b498', u), dk = mixHex('#6a5ab0', '#4a4a3a', u);
      for (let i = 0; i < 14; i++) {
        const v = (i - 6.5) / 7, hh = Math.max(1, Math.round(3.2 * Math.sqrt(Math.max(0, 1 - v * v)))), cx = ox + i;
        P(cx, oy + 3 - hh - 1, OL); P(cx, oy + 3 + hh + 1, OL);
        for (let j = -hh; j <= hh; j++) P(cx, oy + 3 + j, j < -1 ? top : j < 1 ? mid : bel);
        if (u < 0.5 && i > 3 && i < 12 && i % 3) P(cx, oy + 3 - hh + 1, mixHex('#ffffff', mid, u * 2));   // wet sheen, gone by day 4
      }
      P(ox - 1, oy + 3, OL); P(ox - 1, oy + 2, OL); P(ox - 1, oy + 4, OL);
      R(ox + 5, oy + 1, 1, 5, dk);
      for (const [i, j] of [[14, 2], [14, 3], [14, 4], [15, 1], [15, 2], [15, 4], [15, 5], [16, 0], [16, 1], [16, 5], [16, 6], [17, 0], [17, 6]]) P(ox + i, oy + j, dk);
      P(ox + 17, oy + 1, OL); P(ox + 17, oy + 5, OL);
      const eye = k <= 1 ? '#10101c' : k <= 3 ? '#4a5068' : k <= 5 ? '#9a9ab0' : '#d8d6c8';   // the eye clouds
      R(ox + 2, oy + 2, 2, 2, eye); if (k <= 3) P(ox + 2, oy + 2, '#ffffff');
      P(ox, oy + 4, dk);
    });
    TRAYF.set(k, c);
  }
  g.drawImage(c, Math.round(x) - 1, Math.round(y) - 1);
}

/* ---------- the static layer: walls, skylight, beam, lines + boards, floor, door, table, slate board ---------- */
function buildStatic() {
  const none = gone();
  STATIC = layer(W, H, () => {
    R(0, 0, W, FLOOR, '#1b1730');
    for (let x = 0; x < W; x += 9) {   // plank walls
      R(x, 0, 8, FLOOR, (x / 9) % 2 ? '#1d1934' : '#1a162e'); R(x + 8, 0, 1, FLOOR, '#120f22');
      for (let y = 4; y < FLOOR; y += 23 + ((x * 7) % 11)) if (hash(x, y, 3) < 0.35) { P(x + 3, y, '#141126'); P(x + 4, y, '#141126'); P(x + 3, y + 1, '#221d3e'); }
      for (let y = 0; y < FLOOR; y++) if (hash(x, y, 5) < 0.04) P(x + 1 + Math.floor(hash(y, x, 6) * 6), y, '#221e3c');
    }
    for (let x = 0; x < W; x++) { const h = 3 + Math.round(2 * Math.sin(x * 0.11) + hash(x, 1, 8) * 2); for (let j = 0; j < h; j++) if (hash(x, j, 9) < 0.5 - j * 0.08) P(x, FLOOR - 1 - j, '#3a3656'); }   // salt tide mark
    R(149, 0, 74, 14, '#3a2a2a'); R(151, 1, 70, 11, '#0c1230');   // skylight, night sky, thin moon
    for (let i = 0; i < 70; i++) for (let j = 0; j < 11; j++) if (hash(i, j, 21) < 0.04) P(151 + i, 1 + j, '#5a6aa8');
    disc(206, 5, 3, '#e8ecff'); disc(207, 4, 2, '#0c1230'); R(174, 1, 2, 11, '#3a2a2a'); R(198, 1, 2, 11, '#3a2a2a');
    for (let y = 14; y < FLOOR; y++) { g.fillStyle = 'rgba(170,180,255,' + (0.045 * (1 - (y - 14) / (FLOOR - 14)) + 0.012).toFixed(3) + ')'; g.fillRect(151 + Math.round((y - 14) * 0.25), y, 70, 1); }   // moonbeam
    R(0, 15, 264, 6, '#3d2b2c'); R(0, 15, 264, 1, '#5a3e34'); R(0, 20, 264, 1, '#1a1220');   // roof beam and posts
    for (let x = 6; x < 264; x += 14) P(x, 17, '#2a1c20');
    R(0, 15, 4, FLOOR - 15, '#33242a'); R(3, 15, 1, FLOOR - 15, '#1a1220');
    R(257, 15, 6, FLOOR - 15, '#3a2a2c'); R(257, 15, 1, FLOOR - 15, '#5a3e34'); R(262, 15, 1, FLOOR - 15, '#1a1220');
    R(0, FLOOR, W, H - FLOOR, '#251d2e');   // floor boards and spilled salt
    for (let y = FLOOR; y < H; y += 6) { R(0, y, W, 1, '#1a1422'); for (let x = (y * 7) % 37; x < W; x += 37 + (y % 5) * 3) R(x, y, 1, 6, '#1a1422'); }
    R(0, FLOOR, W, 1, '#3a2e44');
    for (let y = FLOOR + 1; y < H; y++) for (let x = 0; x < W; x++) if (hash(x, y, 31) < 0.012) P(x, y, '#4a4266');
    for (const [hx, hw] of [[120, 9], [140, 6], [372, 7]]) for (let j = 0; j < hw / 2; j++) R(hx - hw + j * 2, FLOOR - j, (hw - j * 2) * 2, 1, j % 2 ? '#bdb6dc' : '#d6d0ee');   // salt heaps (scenery)
    for (const [sx, sy] of [[160, 160], [182, 164]]) {   // salt sacks (scenery)
      ellipse(sx + 9, sy + 10, 9, 10, OL); ellipse(sx + 9, sy + 10, 8, 9, '#6a5a4a'); ellipse(sx + 7, sy + 8, 4, 5, '#7e6c58');
      R(sx + 5, sy - 1, 8, 2, '#4a3c30'); R(sx + 4, sy + 9, 11, 7, '#5a4c3e'); textL('SALT', sx + 3, sy + 10, '#c8beac');
    }
    const [dx, dy, dw, dh] = DOOR;   // the door back to town
    R(dx, dy, dw, dh, OL); R(dx + 1, dy + 1, dw - 2, dh - 1, '#4a2f24'); R(dx + 1, dy + 1, dw - 2, 1, '#6a4634');
    for (let x = dx + 5; x < dx + dw - 1; x += 6) R(x, dy + 2, 1, dh - 2, '#3a241c');
    R(dx + 22, dy + 16, 2, 3, '#ffd36e');
    K.doorSign(dx + 2, dy + 5, { pad: 6, inset: 3, fill: '#2b336a', rim: null });
    R(266, 117, 132, 5, OL); R(267, 117, 130, 3, '#6a4a36'); R(267, 117, 130, 1, '#8a6448'); R(267, 120, 130, 1, '#3a2620');   // table
    R(270, 122, 4, FLOOR - 122, '#4a3428'); R(390, 122, 4, FLOOR - 122, '#4a3428'); R(273, 122, 1, FLOOR - 122, '#2a1c18'); R(393, 122, 1, FLOOR - 122, '#2a1c18');
    R(270, 160, 124, 2, '#3a2a22');
    const [x, y, w, h] = SLATE;   // the slate's wooden frame and chalky board
    R(x - 1, y - 1, w + 2, h + 2, OL); R(x, y, w, h, '#6a4a34'); R(x, y, w, 1, '#8a6448');
    R(x + 3, y + 3, w - 6, h - 6, '#1a2824');
    for (let i = 0; i < 60; i++) P(x + 4 + Math.floor(hash(i, 1, 40) * (w - 8)), y + 4 + Math.floor(hash(i, 2, 40) * (h - 8)), '#24362f');
    R(x + 6, y + 45, w - 12, 1, '#2e4038');
    drawLinesStatic(none);
  });
}
const GONE_BOX = [8, 50, 240, 30];
function drawLinesStatic(none) {
  for (const f of LINES) {
    const ly = f.ly;
    R(LX0 - 1, ly - 1, 2, 2, '#8a8aa8'); R(LX1, ly - 1, 2, 2, '#8a8aa8');
    for (let x = LX0; x <= LX1; x++) P(x, lineY(ly, x), (x % 4) ? '#8a7a62' : '#6a5a4a');
    const cnt = none ? '-' : String(f.n), cw = textW(cnt), lab = fitText(f.label, BW + 1 - cw - 3 - 8);   // the board: plain words
    R(4, ly - 5, BW, 11, OL); R(5, ly - 4, BW - 2, 9, f.more ? '#2a2630' : '#2a2140'); R(5, ly - 4, BW - 2, 1, '#3e3260');
    textL(lab, 8, ly - 2, none ? '#6a6e84' : '#d8dcf8'); textL(cnt, BW + 1 - cw, ly - 2, none ? '#6a6e84' : f.col);
    if (none || !f.n) for (let k = 0; k < 6; k++) { const x = FX0 + k * 29; P(x, lineY(ly, x) + 1, '#4a4a5a'); P(x, lineY(ly, x) + 2, '#3a3a4a'); }   // empty pegs
  }
  if (none && !LINES.length) {   // no vault: no lines, just the truth on a chalked board
    const [x, y, w, h] = GONE_BOX, name = V.vault ? fitText('LOOKED FOR: ' + V.vault.toUpperCase(), w - 8) : 'NO VAULT FOLDER IN THE SETTINGS';
    R(x, y, w, h, OL); R(x + 1, y + 1, w - 2, h - 2, '#2a2140'); R(x + 1, y + 1, w - 2, 1, '#3e3260');
    textL('VAULT NOT FOUND', x + 4, y + 4, '#ff8a8a');
    textL(name, x + 4, y + 12, '#8a8fa8');
    textL('NO NOTES TO HANG UNTIL IT IS BACK', x + 4, y + 20, '#6a6e84');
  }
  if (none || !LINES.length) return;
  const key = UNIT === 1 ? '1 FISH = 1 NOTE' : '1 BUNDLE = ' + UNIT + ' NOTES', small = UNIT === 1 ? '' : 'SMALL FISH = THE REST';   // what one fish means
  const [kx, ky, kw, kh] = keyBox();
  R(kx, ky, kw, kh, OL); R(kx + 1, ky + 1, kw - 2, kh - 2, '#3a2a22'); R(kx + 1, ky + 1, kw - 2, 1, '#5a4434');   // a wooden board
  textL(key, kx + 4, ky + 3, UNIT === 1 ? '#e8dcc0' : '#ffc24a'); if (small) textL(small, kx + 4, ky + 9, '#c8a070');
  if (V.scale > 1) { const s = 'SCALE TEST: ' + V.scale + 'X, NOT REAL'; R(FX0 - 1, 23, textW(s) + 6, 9, OL); R(FX0, 24, textW(s) + 4, 7, '#3a1a20'); textL(s, FX0 + 2, 25, '#ff8a8a'); }
}
function keyBox() { const key = UNIT === 1 ? '1 FISH = 1 NOTE' : '1 BUNDLE = ' + UNIT + ' NOTES', kw = Math.max(textW(key), UNIT === 1 ? 0 : textW('SMALL FISH = THE REST')) + 8; return [41, 152, kw, UNIT === 1 ? 10 : 16]; }

/* ---------- per frame: fish on the lines ---------- */
function fishSpots(f) {   // where each fish of a line hangs: [{x, y, k, small, bundle}]
  const out = [], full = Math.floor(f.n / UNIT), rem = f.n % UNIT, n = full + (rem ? 1 : 0);
  for (let k = 0; k < n; k++) { const x = FX0 + 1 + k * (UNIT === 1 ? FSTEP : BSTEP); out.push({ x, y: lineY(f.ly, x), k, small: k >= full, bundle: UNIT > 1 && k < full }); }
  return out;
}
function drawFish(t) {
  if (gone()) return;
  LINES.forEach((f, li) => {
    for (const s of fishSpots(f)) {
      const dx = K.still || !STIR ? 0 : Math.sin(t * 1.6 - s.x * 0.05 + li * 0.7) > 0.94 ? 1 : 0;   // a breath of wind along the line (only while the review runs)
      g.drawImage(fishSp(f.col, f.dk, s.bundle ? 'bundle' : s.small ? 'small' : 'fish', dx), s.x - 6, s.y);
    }
  });
}

/* ---------- the wet tray: the newest inbox ---------- */
function tag(x, y, mark, t, mt) {
  R(x + 2, y - 2, 1, 2, '#c8b890');
  const pop = NOW_T() - mt < 0.25 ? -1 : 0;
  const col = mark === 'keep' ? '#2f9a5a' : mark === 'drop' ? '#b83030' : '#c87a10', rim = mark === 'keep' ? '#7dffb0' : mark === 'drop' ? '#ff8a8a' : '#ffd070';
  R(x - 1, y + pop, 8, 8, OL); R(x, y + 1 + pop, 6, 6, col); R(x, y + 1 + pop, 6, 1, rim);
  if (mark === 'keep') sprite(['....#', '...#.', '#.#..', '.#...'], x + 1, y + 2 + pop, { '#': '#eaffef' });
  else if (mark === 'drop') sprite(['#..#', '.##.', '.##.', '#..#'], x + 1, y + 2 + pop, { '#': '#ffe8e8' });
  else sprite(['##.', '..#', '.#.', '...', '.#.'], x + 2, y + 2 + pop, { '#': K.still || Math.sin(t * 5) > -0.3 ? '#2a1a08' : '#6a4a18' });
}
let SEEN = {};
function drawTray(t) {
  const age = ageDays(), none = !V.inbox || gone();
  R(TRAY.x - 1, TRAY.y - 1, TRAY.w + 2, 15, OL); R(TRAY.x, TRAY.y, TRAY.w, 14, '#6a7090'); R(TRAY.x, TRAY.y, TRAY.w, 1, '#a8b0d0');
  R(TRAY.x + 2, TRAY.y + 2, TRAY.w - 4, 10, none ? '#2a2c3a' : mixHex('#2e4c70', '#3a3a44', Math.min(1, age / 7)));
  const tg = STIR ? t : 0;   // the glints on the brine run only while the review runs
  if (!none && !K.still) for (let i = 0; i < 9; i++) { const x = TRAY.x + 4 + ((i * 37 + Math.floor(tg * 3) * 7) % (TRAY.w - 8)), y = TRAY.y + 3 + (i * 5) % 8; if (Math.sin(tg * 2 + i) > 0.4) P(x, y, age < 4 ? '#7ab0e0' : '#5a6070'); }
  const ice = none ? 0 : Math.max(0, 6 - age), ICE = [[273, 110], [296, 104], [319, 110], [344, 104], [367, 110], [389, 105]];   // 6 cubes on day 0, none by day 6
  for (let k = 0; k < ice; k++) { const [x, y] = ICE[k]; R(x, y, 4, 3, '#cfeaff'); P(x, y, '#ffffff'); R(x, y + 2, 4, 1, '#8ab8e0'); }
  if (!none) for (let k = ice; k < 6; k++) { const [x, y] = ICE[k]; R(x, y + 2, 4, 1, '#3a5a80'); }
  let onTray = 0;
  for (const d of ideas) {
    if (d.gone || d.slot >= TRAY.slots || (fly && fly.idea === d)) continue;
    const x = TRAY.sx(d.slot); trayFish(x, TRAY.fy, age); tag(x + 6, 124, d.mark, t, d.markT); onTray++;
  }
  const vis = ideas.filter(d => !d.gone), extra = Math.max(0, vis.length - TRAY.slots);
  if (extra) { const s = '+' + extra; R(394 - textW(s) - 4, 96, textW(s) + 4, 7, OL); textL(s, 394 - textW(s) - 2, 97, '#ffd36e'); }
  const flies = none ? 0 : Math.min(4, Math.floor(age / 2));   // flies come to old fish: one per 2 days
  const buzz = !K.still && untriaged() > 0;   // 0.2.1: they buzz while old ideas wait to be sorted; sorted, they rest
  for (let k = 0; k < flies; k++) { const a = (buzz ? t : 1) * (3 + k) + k * 1.7, fx = 300 + k * 22 + Math.cos(a) * 7, fy = 98 + Math.sin(a * 1.3) * 3; P(fx, fy, '#141018'); if (buzz && Math.sin(t * 30 + k) > 0) P(fx, fy - 1, '#8a8aa8'); }
  const [sx, sy] = SIGN;   // the age sign: the tray's age in plain words
  const s1 = none ? (gone() ? 'NO INBOX' : 'NO INBOX YET') : (V.sample ? 'SAMPLE ' : 'INBOX ') + K.dayText(V.inbox.date).slice(4), s2 = none ? '' : age + (age === 1 ? ' DAY OLD' : ' DAYS OLD');
  const sw = Math.max(textW(s1), textW(s2)) + 8, sh = s2 ? 16 : 10;
  R(sx, sy, sw, sh, OL); R(sx + 1, sy + 1, sw - 2, sh - 2, '#3a2a22'); R(sx + 1, sy + 1, sw - 2, 1, '#5a4434');
  textL(s1, sx + 5, sy + 3, none ? '#7a7f99' : V.sample ? '#d8b0ff' : '#e8dcc0'); if (s2) textL(s2, sx + 5, sy + 9, age >= 5 ? '#ffc24a' : '#b8c8e8');
  const st = roomState();
  if (!none && st !== 'working' && st !== 'error') { const n = untriaged(), w = n ? n + ' TO SORT' : 'ALL SORTED'; textL(w, 394 - textW(w), 134, n ? '#ffc24a' : '#7dffb0'); }
  Object.assign(SEEN, { tray: onTray, trayMore: extra, ice, flies, age });
}
function signBox() { const none = !V.inbox; return [SIGN[0], SIGN[1], 70, none ? 10 : 16]; }
function drawFly() {
  if (!fly) return;
  const u = Math.min(1, (NOW_T() - fly.t0) / fly.dur), x0 = TRAY.sx(fly.idea.slot), y0 = TRAY.fy, [mx, my] = catMouth();
  let x, y;   // slides off the front rim, then hops in an arc to the cat
  if (u < 0.35) { const v = u / 0.35; x = x0; y = y0 + v * v * 8; }
  else { const v = (u - 0.35) / 0.65; x = x0 + (mx - 10 - x0) * v; y = (y0 + 8) + (my - 3 - (y0 + 8)) * v - Math.sin(v * Math.PI) * 22; }
  trayFish(x, y, ageDays());
  if (u >= 1) {
    const f = fly; f.idea.gone = true; fly = null; gulpT = NOW_T();
    const n = Math.round(belly.to); belly = { from: n, to: n + 1, t0: NOW_T() }; play('gulp');
    if (f.done) f.done();
  }
}

/* ---------- the cat: its belly is the dropped ideas ---------- */
function catGeom() { const n = Math.min(6, bellyNow()); return { rx: Math.round(6 + 2.5 * n), ry: Math.round(6 + 0.9 * n) }; }
function catMouth() { const { rx, ry } = catGeom(), by = CAT.floor - ry; return [CAT.cx + Math.max(4, Math.round(rx * 0.55)) + 5, by - ry - 2]; }
function droppedNow() { return Math.round(belly.to); }
function drawCat(t) {
  if (gone()) {   // an empty basket
    ellipse(CAT.cx, CAT.floor - 2, 12, 3, OL); ellipse(CAT.cx, CAT.floor - 2, 11, 2, '#3a2e2a'); textC('-', CAT.cx, 185, '#6a6e84'); return;
  }
  const { rx, ry } = catGeom(), bx = CAT.cx, by = CAT.floor - ry, body = '#3e3656', lite = '#5a5078', tummy = '#8a7ea8';
  const eating = !!fly || NOW_T() - gulpT < 0.7, tw = K.still || !eating ? 0 : Math.round(Math.sin(t * 1.5) * 1.5);   // the tail flicks only while it eats
  for (let i = 0; i < 9; i++) { const a = i / 8, xx = bx - rx - Math.round(a * 4), yy = CAT.floor - 2 - Math.round(a * a * 10) + (i > 6 ? tw : 0); P(xx - 1, yy, OL); P(xx, yy, body); }
  ellipse(bx, by, rx + 1, ry + 1, OL); ellipse(bx, by, rx, ry, body); ellipse(bx - 1, by - 2, Math.max(2, rx - 3), Math.max(2, ry - 3), lite);
  ellipse(bx + Math.round(rx * 0.3), by + Math.round(ry * 0.35), Math.max(2, Math.round(rx * 0.55)), Math.max(1, Math.round(ry * 0.5)), tummy);   // the belly
  const lx = bx + Math.max(3, Math.round(rx * 0.5));
  R(lx - 1, by + 1, 2, CAT.floor - by - 1, body); R(lx + 2, by + 2, 2, CAT.floor - by - 2, body); P(lx - 1, CAT.floor - 1, lite); P(lx + 2, CAT.floor - 1, lite);
  const gulping = NOW_T() - gulpT < 0.7, reach = fly && (NOW_T() - fly.t0) / fly.dur > 0.55;
  const hx = bx + Math.max(4, Math.round(rx * 0.55)) + 1, hy = by - ry - 2 + (gulping ? 1 : 0) - (reach ? 1 : 0);
  disc(hx, hy, 5, OL); disc(hx, hy, 4, body); disc(hx - 1, hy - 1, 2, lite);
  sprite(['#..', '##.', '###'], hx - 4, hy - 7, { '#': OL }); sprite(['.', '#', '#'], hx - 3, hy - 6, { '#': body });
  sprite(['..#', '.##', '###'], hx + 1, hy - 7, { '#': OL }); sprite(['.', '#', '#'], hx + 2, hy - 6, { '#': body });
  P(hx - 3, hy - 5, '#c08aa8'); P(hx + 2, hy - 5, '#c08aa8');
  const blink = !K.still && eating && (t % 4) < 0.15;
  if (gulping) { sprite(['#.#', '.#.'], hx - 2, hy - 1, { '#': '#d8ff8a' }); sprite(['#.#', '.#.'], hx + 2, hy - 1, { '#': '#d8ff8a' }); }
  else if (!blink) { P(hx - 1, hy - 1, '#d8ff8a'); P(hx + 2, hy - 1, '#d8ff8a'); }
  P(hx + 4, hy + 1, '#f0a0b8');
  if (reach) R(hx + 3, hy + 2, 2, 2, '#1a0a14');
  P(hx + 5, hy, '#8a8aa8'); P(hx + 6, hy - 1, '#8a8aa8'); P(hx + 5, hy + 2, '#8a8aa8'); P(hx + 6, hy + 3, '#8a8aa8');
  textC('DROPPED ' + droppedNow(), bx + 2, 185, V.sample ? '#d8b0ff' : '#c0b4e0');
}
function catBox() { const { rx, ry } = catGeom(), by = CAT.floor - ry; return [CAT.cx - rx - 6, by - ry - 9, rx * 2 + 18, CAT.floor - (by - ry - 9) + 10]; }

/* ---------- brine barrel: bubbles while the review runs ---------- */
function drawBarrel(t) {
  const [x, y, w] = BARREL, h = FLOOR - y, on = roomState() === 'working';
  R(x - 1, y - 1, w + 2, h + 1, OL); R(x, y, w, h, '#5a3e2c');
  for (let i = 2; i < w; i += 5) R(x + i, y + 1, 1, h - 1, '#4a3222');
  R(x, y + 6, w, 2, '#7a7a8a'); R(x, y + h - 7, w, 2, '#7a7a8a');
  R(x + 1, y, w - 2, 3, on ? '#2fb8c8' : '#1e2a36');
  if (on) {
    for (let i = 0; i < 5; i++) { const ph = ((K.still ? 0.4 : t) * 1.4 + i / 5) % 1, bx = x + 3 + ((i * 7) % (w - 6)), by = y - Math.round(ph * 14); P(bx + Math.round(Math.sin(ph * 6 + i)), by, ph < 0.7 ? '#9ffff0' : '#4fd8e0'); }
    glow(glowSp('#6fffe0', 14, 8, 0.35), x + w / 2, y, K.still ? 1 : 0.9 + Math.sin(t * 4) * 0.1);
  }
}

/* ---------- the slate: last and next review ---------- */
function drawSlate(t) {
  const [x, y, w] = SLATE, st = roomState(), none = gone(), dim = '#8fa8a0', chalk = none ? '#6a6e84' : '#e8eef0';
  textL('LAST REVIEW', x + 7, y + 6, dim);
  const ld = V.last ? K.dayText(V.last) : '-'; textL(ld, x + 7, y + 13, chalk);
  if (V.last) { const lx = x + 9 + textW(ld); if (st === 'error') sprite(['#.#', '.#.', '#.#'], lx + 1, y + 14, { '#': '#ff6a6a' }); else sprite(['....#', '...#.', '#.#..', '.#...'], lx, y + 13, { '#': '#7dffb0' }); }
  if (st === 'error' && V.last) {   // the missed run: red chalk across the old date, and how old it is
    for (let i = 0; i < textW(ld) + 4; i++) P(x + 5 + i, y + 18 - Math.round(i * 0.05), '#ff5a5a');
    textL(daysSince(V.last) + ' DAYS AGO', x + 7, y + 20, '#ff8a8a');
  }
  textL('NEXT REVIEW', x + 7, y + 24 + (st === 'error' ? 3 : 0), dim);
  if (st !== 'error') {
    textL(none || !V.next ? '-' : K.dayText(V.next) + ' ' + K.timeText(V.next).slice(4), x + 7, y + 30, chalk);
    if (!none && V.next) textL(K.untilText(V.next), x + 7, y + 38, dim);
  } else textL(V.next ? K.dayText(V.next) + ' ' + K.timeText(V.next).slice(4) : '-', x + 7, y + 34, chalk);
  let word = say(st);
  if (st === 'waiting') { const u = untriaged(); word = u + (u === 1 ? ' IDEA TO SORT' : ' IDEAS TO SORT'); }
  if (st === 'ok') word = 'ALL SORTED';
  if (st === 'working' && !K.still) word += '...'.slice(0, 1 + Math.floor(t * 2) % 3);
  if (!(st === 'error' && !K.still && (t % 0.6) > 0.35)) textL(fitText(word, w - 12), x + 7, y + 50, none || st === 'missing' ? '#8a8fa8' : ST[st].txt);
}

/* ---------- the lantern: the room's state ---------- */
function drawLantern(t) {
  const st = roomState(), S = ST[st], [x, y] = LANT;
  R(x, 12, 1, y - 12, '#4a4a5a'); for (let j = 14; j < y; j += 3) P(x, j, '#6a6a7a');
  const on = st === 'missing' ? false : K.still ? true : st === 'waiting' ? (t % 1.1) < 0.75 : st === 'error' ? (t % 0.5) < 0.3 : true;
  R(x - 3, y - 3, 7, 2, OL); R(x - 1, y - 5, 3, 2, OL);
  R(x - 7, y - 1, 15, 3, OL); R(x - 6, y, 13, 1, '#4a4a5e');
  R(x - 7, y + 2, 15, 17, OL); R(x - 6, y + 3, 13, 15, '#2a2a3a');
  R(x - 5, y + 4, 11, 13, on ? S.lamp : mixHex(S.lamp, '#101018', st === 'missing' ? 0.6 : 0.35)); if (on) { R(x - 4, y + 5, 2, 5, S.hi); disc(x, y + 11, 2, S.hi); }
  R(x, y + 3, 1, 15, '#2a2a3a'); R(x - 6, y + 10, 13, 1, '#2a2a3a');
  R(x - 6, y + 19, 13, 2, OL); R(x - 3, y + 21, 7, 1, OL);
  if (on && S.glow) { glow(glowSp(S.glow, 64, 46, 0.26), x, y + 26, st === 'ok' ? 0.85 : 1); glow(glowSp(S.glow, 12, 12, 0.6), x, y + 10, 1); }
}

/* ---------- sound: synthesised, short, quiet ---------- */
function sweep(ac, out, t0, f0, f1, d, vol, type) {
  const o = ac.createOscillator(), gn = ac.createGain(); o.type = type; o.frequency.setValueAtTime(f0, t0); o.frequency.exponentialRampToValueAtTime(f1, t0 + d);
  gn.gain.setValueAtTime(0.0001, t0); gn.gain.exponentialRampToValueAtTime(vol, t0 + 0.01); gn.gain.exponentialRampToValueAtTime(0.0001, t0 + d);
  o.connect(gn); gn.connect(out); o.start(t0); o.stop(t0 + d + 0.03);
}
function play(k) {
  const ac = sfx.ctx(), out = sfx.out(); if (!ac || !out) return;
  const t = ac.currentTime + 0.02;
  if (k === 'enter') { sfx.noise(t, 0.4, 0.12, 260, 'lowpass'); sweep(ac, out, t + 0.3, 1500, 600, 0.12, 0.12, 'sine'); }
  else if (k === 'drip') sweep(ac, out, t, 1700, 700, 0.07, 0.06, 'sine');
  else if (k === 'keep') { sfx.tone(t, 880, 0.05, 0.08, 'square'); sfx.tone(t + 0.06, 1320, 0.08, 0.08, 'square'); }
  else if (k === 'drop') { sfx.noise(t, 0.08, 0.15, 2200, 'bandpass'); sweep(ac, out, t + 0.02, 300, 200, 0.1, 0.06, 'square'); }
  else if (k === 'slide') { sfx.noise(t, 0.5, 0.12, 1000, 'bandpass'); sweep(ac, out, t + 0.55, 420, 640, 0.16, 0.12, 'triangle'); sweep(ac, out, t + 0.7, 640, 430, 0.2, 0.12, 'triangle'); }
  else if (k === 'gulp') { sweep(ac, out, t, 220, 80, 0.14, 0.27, 'sine'); sweep(ac, out, t + 0.18, 500, 380, 0.25, 0.06, 'triangle'); }
}

/* ---------- demo: the core moment as a SAMPLE replay ---------- */
const CAP_STATE = { working: 'THE REVIEW IS RUNNING: THE BRINE BUBBLES', error: 'MISSED ITS RUN: RED LANTERN, RED CHALK ON THE SLATE',
  missing: 'NO VAULT: EMPTY PEGS, NO CAT, DARK LANTERN' };
function runDemo() {
  timers.forEach(clearTimeout); timers = []; caption = '';
  const st = demo.state, at = (ms, cap, fn) => timers.push(setTimeout(() => { caption = cap; if (fn) fn(); }, ms));
  if (st !== 'ok' && st !== 'waiting') { at(300, CAP_STATE[st]); return; }
  setupIdeas(); ideas.forEach(d => { d.mark = null; });
  const n = ideas.length, di = Math.min(n, TRAY.slots) - 1;   // the drop: the last fish on the tray
  at(0, n + ' FRESH ' + (n === 1 ? 'IDEA WAITS' : 'IDEAS WAIT') + ' ON THE WET TRAY');
  at(1200, 'IDEA 1 MARKED KEEP: GREEN TICK', () => markIdea(0, 'keep'));
  if (st === 'ok' && n > 2) at(2200, n > 3 ? 'THE OTHERS MARKED KEEP' : 'IDEA 2 MARKED KEEP', () => { for (let i = 1; i < n; i++) if (i !== di) markIdea(i, 'keep'); });
  else if (st === 'waiting' && n > 2) at(2200, 'IDEA 2 STILL WAITS FOR YOU');
  if (di > 0) {
    at(3200, 'IDEA ' + (di + 1) + ' MARKED DROP: RED CROSS', () => markIdea(di, 'drop'));
    at(4000, 'IT SLIDES OFF THE TRAY TO THE CAT', () => slideIdea(ideas[di]));
    at(5200, 'THE CAT ATE IT: ITS BELLY GROWS');
  }
  at(6200, st === 'ok' ? 'ALL SORTED: THE LANTERN TURNS GREEN' : 'STILL TO SORT: THE LANTERN STAYS AMBER');
  timers.push(setTimeout(runDemo, 11000));
}
function drawCaption() {
  const tg = demo.state === 'ok' || demo.state === 'waiting' ? 'SAMPLE REPLAY: NOT A REAL DROP' : 'SAMPLE: MADE-UP NOTEBOOK';
  const w = caption ? textW(caption) + 12 : 0, x = caption ? Math.max(4, Math.round(130 - w / 2)) : 44, y = 196;
  if (caption) { R(x, y, w, 13, OL); R(x + 1, y + 1, w - 2, 11, '#2a1a3a'); R(x + 1, y + 1, w - 2, 1, '#c48dff'); textL(caption, x + 6, y + 4, '#f0e8ff'); }
  K.sampleBadge(tg, x, y - 9, { h: 8, pad: 6, fill: '#2a1a3a' });
}

/* ---------- hover / click targets ---------- */
function openIt(href, what) { if (href) { sfx.click(); K.open(href); K.toast('OPENING ' + what); } else K.toast(V.sample ? 'SAMPLE: NOTHING TO OPEN' : 'NO FILE FOR ' + what + ' YET'); }
function openInbox() { openIt(V.inbox && V.inbox.href, V.inbox ? 'THE INBOX ' + K.dayText(V.inbox.date).slice(4) : 'THE INBOX'); }
const markWord = m => m === 'keep' ? 'Marked keep (green tick)' : m === 'drop' ? 'Marked drop (red cross)' : 'Not marked yet: keep or drop?';
function targets() {
  const out = [], none = gone(), age = ageDays(), st = roomState(), click = 'Click: open the inbox.';
  if (V.inbox && !none) {
    const left = ideas.filter(d => !d.gone).length;
    const tip = [(V.sample ? 'Sample inbox (demo, made up)' : 'Inbox ' + K.dayText(V.inbox.date).slice(4)), plural(left, 'idea').toLowerCase() + ' on the tray, ' + untriaged() + ' to sort',
      age + (age === 1 ? ' day' : ' days') + ' old: ' + Math.max(0, 6 - age) + ' of 6 ice cubes left', V.inbox.href ? click : ''].filter(Boolean);
    ideas.forEach(d => {
      if (d.gone || d.slot >= TRAY.slots) return;
      const x = TRAY.sx(d.slot), t2 = ['Idea ' + d.n + ': ' + d.title, markWord(d.mark) + (d.reason ? ': ' + d.reason : ''), (d.from ? 'From ' + d.from + '. ' : '') + (V.inbox.href ? click : '')].filter(Boolean);
      out.push({ id: 'idea-' + d.n, box: [x - 1, TRAY.fy - 1, 20, 9], tip: t2, label: 'Idea ' + d.n + ', ' + d.title + '. ' + markWord(d.mark) + '. Open the inbox.', onClick: openInbox });
      out.push({ id: 'tag-' + d.n, box: [x + 5, 121, 8, 10], tip: t2, label: '', onClick: openInbox });
    });
    const extra = Math.max(0, left - TRAY.slots);
    if (extra) out.push({ id: 'tray-more', box: [394 - textW('+' + extra) - 4, 96, textW('+' + extra) + 4, 7], tip: ['+' + extra + ' more ideas', 'Five fit on the tray; the inbox holds them all.'], label: '', onClick: openInbox });
    out.push({ id: 'tray', box: [TRAY.x - 2, TRAY.y - 2, TRAY.w + 4, 16], tip, label: 'The wet tray: the newest inbox. ' + untriaged() + ' to sort. Open the inbox.', onClick: openInbox });
    out.push({ id: 'sign', box: signBox(), tip: [(V.sample ? 'Sample inbox, ' : 'Inbox file, ') + K.dayText(V.inbox.date), 'The ice melts, the fish dull and flies come as the days pass.', V.inbox.href ? click : ''].filter(Boolean), label: '', onClick: openInbox });
  } else out.push({ id: 'tray', box: [TRAY.x - 2, TRAY.y - 2, TRAY.w + 4, 16], tip: [none ? 'No inbox: can\'t see the vault' : 'No inbox note yet', 'The review leaves its new ideas here.'], label: 'The wet tray: no inbox yet', onClick: null });
  const lastWord = V.last ? K.dayText(V.last) : 'none on record';
  out.push({ id: 'slate', box: SLATE.slice(), tip: ['The review', 'Last: ' + lastWord + (st === 'error' ? ' (missed its run)' : ''),
    'Next: ' + (V.next ? K.dayText(V.next) + ' ' + K.timeText(V.next).slice(4) + ' (' + K.untilText(V.next) + ')' : '-'), V.when ? 'Runs ' + V.when : 'No review schedule set',
    V.sample ? 'Demo: a sample state, not your notes.' : (V.lastHref ? 'Click: open the last review.' : '')].filter(Boolean),
    label: 'Slate: last review ' + lastWord + '. Open the last review.', onClick: () => openIt(V.lastHref, 'THE LAST REVIEW') });
  out.push({ id: 'lantern', box: [LANT[0] - 8, LANT[1] - 5, 17, 27], tip: ['Lantern: ' + { ok: 'green, all sorted', waiting: 'amber, ideas wait for you', working: 'cyan, the review is running',
    error: 'red, the review missed its run', missing: none ? 'dark, can\'t see the vault' : 'dark, no review on record yet' }[st]], label: 'Lantern, ' + ST[st].word, onClick: null });
  if (!none) {
    const n = droppedNow(), pend = ideas.filter(d => d.gone).length, names = V.dropped.names.slice(0, 4);
    out.push({ id: 'cat', box: catBox(), tip: ['The cat: ' + plural(n, 'dropped idea').toLowerCase(), V.dropped.n + ' in the dropped folder' + (pend ? ', ' + pend + ' marked drop in this inbox' : '')]
      .concat(names, V.sample && pend ? ['(the replay\'s drop is a sample, not real)'] : [], ['Its belly grows one step per drop, up to 6.', V.dropped.href ? 'Click: open the dropped folder.' : '']).filter(Boolean),
      label: 'The cat: ' + n + ' dropped ideas. Open the dropped folder.', onClick: () => openIt(V.dropped.href, 'THE DROPPED FOLDER') });
  }
  out.push({ id: 'barrel', box: [BARREL[0], BARREL[1] - 4, BARREL[2], FLOOR - BARREL[1] + 4], tip: st === 'working' ? ['Brine barrel bubbling', 'The review is running now.'] : ['Brine barrel: still', 'It bubbles only while the review runs.'].concat(V.logHref ? ['Click: open the review log.'] : []),
    label: '', onClick: V.logHref ? () => openIt(V.logHref, 'THE REVIEW LOG') : null });
  out.push({ id: 'door', box: DOOR.slice(), tip: ['Back to town', 'or press Esc'], label: 'Door: back to town', onClick: K.leave });
  if (none && !LINES.length) out.push({ id: 'gone', box: GONE_BOX, tip: ['Vault not found', V.vault ? 'Looked for the folder ' + V.vault : 'The settings name no vault folder.', 'No notes hang here until the folder is back.'], label: 'Vault not found', onClick: null });
  if (!none && LINES.length) out.push({ id: 'key', box: keyBox(), tip: UNIT === 1 ? ['Every fish on a line is one note file.'] : ['Too many notes to hang one by one', 'Each tied bundle is ' + UNIT + ' notes; a small fish holds the rest of a line.'], label: '', onClick: null });
  LINES.forEach(f => {
    const where = f.more ? f.more.length + ' more folders' : f.key === '.' ? 'Loose notes (the vault\'s top folder)' : f.kept ? 'Kept ideas' : f.key + '/';
    const tip = [where, none ? 'Can\'t see this folder.' : plural(f.n, 'note').toLowerCase() + (UNIT > 1 ? ' (1 bundle = ' + UNIT + ' notes)' : ', one salted fish each')]
      .concat(f.more ? f.more.map(m => m.label.toLowerCase() + ': ' + m.n).slice(0, 6) : [], V.scale > 1 ? ['Scale test: ' + V.scale + 'x the sample count, not real.'] : [], f.href ? ['Click: open the folder.'] : []);
    out.push({ id: 'line-' + f.key, box: [4, f.ly - 5, BW, 11], tip, label: f.label + ' line, ' + f.n + ' notes. Open the folder.', onClick: f.href ? () => openIt(f.href, f.label) : null });
  });
  if (!none) LINES.forEach(f => {
    const rn = f.notes.length;
    for (const s of fishSpots(f)) {
      let tip;
      if (UNIT === 1) { const nm = rn ? f.notes[s.k % rn] : ''; tip = [nm || 'A note', 'In ' + (f.more ? 'one of ' + f.more.length + ' folders' : f.label.toLowerCase())]; }
      else { const a = s.k * UNIT, b = Math.min(f.n, a + UNIT); tip = [(b - a) + ' notes in ' + (f.more ? 'more folders' : f.label.toLowerCase()), rn ? Array.from({ length: Math.min(4, b - a) }, (_, i) => f.notes[(a + i) % rn]).join(', ') : '']; }
      if (V.scale > 1 || (rn && rn < f.n)) tip.push(V.scale > 1 ? 'Scale test: names repeat.' : 'Not every name is listed.');
      if (f.href && !f.more) tip.push('Click: open the folder.');
      out.push({ id: 'fish-' + f.key + '-' + s.k, box: [s.x - (s.bundle ? 4 : 2), s.y, s.bundle ? 9 : 5, 12], tip: tip.filter(Boolean), label: '', onClick: f.href && !f.more ? () => openIt(f.href, f.label) : null });
    }
  });
  return out;
}

/* ---------- the room ---------- */
function setup(data, opts) {
  V = mkView(data, opts); mkLines(); setupIdeas(); STATIC = null; caption = '';
}
registerRoom('vault', {
  enter(data, opts) {
    demo = opts.demo ? { state: STATES.includes(opts.state) ? opts.state : 'ok', qs: opts.qs } : null;
    roomId = String(opts.id || '');
    RAW = data; setup(data, opts); buildStatic();
    timers.push(setTimeout(() => play('enter'), 350));
    if (demo) runDemo();
    else if (queue.length) timers.push(setTimeout(eatNext, 1400));   // a real drop the room hasn't shown yet
  },
  leave() { RAW = null; V = null; timers.forEach(clearTimeout); timers = []; fly = null; queue = []; caption = ''; STATIC = null; FISH.clear(); TRAYF.clear(); },
  draw(ctx, t) {
    if (!V) return;
    if (!STATIC) buildStatic();
    g.drawImage(STATIC, 0, 0);
    STIR = roomState() === 'working';   // 0.2.1: an idle room is still; the wind, the brine and the stars stir only while the review runs
    if (!K.still) { const ts = STIR ? t : 0; for (let i = 0; i < 5; i++) if (Math.sin(ts * 1.3 + i * 2.1) > 0.5) P(156 + i * 13, 3 + (i * 5) % 8, '#e8ecff'); }   // skylight stars
    glow(glowSp('#b8c4ff', 20, 8, 0.12), 186, 20, 1);
    drawFish(t); drawLantern(t); drawSlate(t); drawBarrel(t); drawTray(t); drawCat(t); drawFly();
    const st = roomState();
    if (gone()) { g.fillStyle = 'rgba(6,8,20,0.55)'; g.fillRect(0, 12, W, H - 12); }
    if (st === 'error') { g.fillStyle = 'rgba(120,10,20,' + (0.06 + (K.still ? 0 : 0.05 * ((t % 0.5) < 0.3 ? 1 : 0))).toFixed(3) + ')'; g.fillRect(0, 12, W, H - 12); }
    if (st === 'working') { g.fillStyle = 'rgba(40,200,220,0.04)'; g.fillRect(0, 12, W, H - 12); }
    if (!gone()) glow(glowSp('#ffb45a', 30, 14, 0.1), 200, 200, 0.8);
    if (demo) drawCaption();
    if (!K.still && V.state !== 'missing' && NOW_T() - lastDrip > 3.5) { lastDrip = NOW_T(); if (Math.random() < 0.5) play('drip'); }   // brine dripping
  },
  targets,
  plate() { return { num: V && !gone() ? String(V.notes) : '-', state: V ? roomState() : 'missing' }; },
  busy() { return !!fly || queue.length > 0 || NOW_T() - gulpT < 0.8 || NOW_T() - belly.t0 < 0.6 || (!!demo && timers.length > 0); },
  key(e) {
    if (!demo) return;
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= STATES.length) { demo.state = STATES[n - 1]; setup(RAW, { demo: true, qs: demo.qs }); buildStatic(); runDemo(); }
  },
  seen() { // what the room shows now, for tests
    if (!V) return {};
    return Object.assign({}, SEEN, { sample: V.sample, state: roomState(), why: V.why, gone: gone(), unit: UNIT,
      lines: LINES.map(f => ({ key: f.key, label: f.label, n: f.n, fish: gone() ? 0 : fishSpots(f).length, more: f.more ? f.more.length : 0, kept: f.kept })),
      ideas: ideas.map(d => ({ n: d.n, mark: d.mark, gone: d.gone })), untriaged: untriaged(), dropped: droppedNow(),
      flying: fly ? fly.idea.n : null, barrel: roomState() === 'working', cat: !gone(), caption });
  },
});
})();
