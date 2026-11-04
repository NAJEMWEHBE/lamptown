'use strict';
/* LAMPTOWN: the Harbour's berths, one per live project (the live town; the Watcher's payload is LIVE.payload, spec
   lamptown-live/1, see livetown.py). Inlined by `lamptown build` after live.js; it draws through town.js's TOWN_LAYERS
   hooks and gives the Lamplighters (live.js) their spot at the Harbour through LAMPLIGHTERS.berth.
     the wharf     a timber wharf on piles along the water's edge, right of the slipway first, then left of it, built
                   only as far as berths are in use (no live projects: no wharf, the waterfront as before)
     a berth       a bollard on the wharf and the project's own boat moored in front of it (each berth in use has its own
                   paint, kept while the project is moored), its name on the water: the whole name when it fits, else a
                   short form no other tag or project name has (its own word, a cut like "PAPER K.", or "P. KITE"). The project's
                   agents at work stand on the wharf at its berth. Pushed: the boat's sail is up. CI: a pennant on its
                   mast (amber running, green, red). Hover: the project, who works there, the counts.
     a new project the payload says `new`: once, its boat is launched down the slipway, splashes in and glides to its
                   berth (a real event; calm after). Boats the page has already shown never launch (the first payload's
                   are moored), and a launch that could not play within 15 s (a room was open, the tab hidden) is
                   dropped: the boat is simply moored. A still scene (reduced motion, ?t=) launches nothing either.
     overflow      at most SLOTS.length berths; the rest share one small mooring at the far end, tagged "+N" (hover:
                   names). It sits clear of the sea serpent. When a berth frees, the oldest project there moves up: its
                   boat sails along to the berth and its agents walk over (LAMPLIGHTERS.respot), never a jump.
     visitors      agents at the Harbour with no berth (private ones, a project the payload doesn't list) stand in the
                   yard right of the boathouse, away from the wharf (with no wharf built, in live.js's own row by the
                   door, as before; they walk over when the first berth is taken).
   Who has which berth: a project keeps its berth while it is in the payload, and GRACE_MS after, so a short gap never
   reshuffles the row. A project that needs one takes a berth nobody holds first; only when there is none does it take
   the berth held longest in its grace. So the overflow is never moored beside an empty berth. Berths are given out on
   the page in arrival order (the payload's `berth` order breaks ties) and never re-sorted.
   Who gets a berth: a project with an agent at the Harbour (plot boathouse, or a plot the town doesn't have), or with no
   agent at all (a project record: code goes to the Harbour). A project whose agents all work elsewhere gets none.
   Names only on screen. The Harbour room (rooms/harbour.js) shows the same berths in the same order and paint, from
   BERTHS.view(). What the last town frame drew is in SEEN.berths. */
const BERTHS = (() => {
  // the berths in the order they are given out: each one's left end (30 px wide), right of the slipway outward, then left
  const RIGHT = 3, SLOTS = [228, 258, 288, 162, 132, 102], N = SLOTS.length, PITCH = 30;
  const OVER = 88, OVER_W = 14;   // the overflow mooring: its left end and width (clear of the serpent's head, which ends at x 86)
  const WHARF_R = 227, WHARF_L = 192, BOUND = [96, 192, 216, 318];   // the wharf's inner ends; where name tags may reach
  const DECK = 200, FEET = 204, BOAT = 214, TAG = 216, SHORE = 194;   // the deck's top edge, agents' feet, a boat's waterline, tags
  const SLIP_X = 204, SLIP_Y = 192, SPLASH = 214;         // the launch: down the boathouse's slipway into the water
  // a launch waits at most TUNE.wait ms for its turn (tests may shorten the timings)
  const GRACE_MS = 120000, TUNE = { slide: 1400, splash: 500, glide: 30, wait: 15000 };
  const PAINT = ['#b8574a', '#4a7fb0', '#c9a24a', '#5a9a62', '#8a6ab8', '#c4708e', '#4aa0a0', '#d08850'];
  const CI_COL = { running: '#ffb020', green: '#3ddc7a', red: '#ff4a4a' };
  const STATES = new Set(['working', 'waiting', 'ok', 'error', 'quiet']);
  const PLOTS = new Set(['tower', 'library', 'gate', 'lab', 'workshop', 'boathouse']);   // live.js's places (any other plot: the Harbour)
  const WORD = { working: 'WORKING', waiting: 'WAITING FOR YOU', error: 'FAILED', quiet: 'GONE QUIET', ok: 'DONE' };
  const fnv = s => { let n = 2166136261; for (const ch of String(s)) n = Math.imul(n ^ ch.charCodeAt(0), 16777619); return n >>> 0; };
  const paintOf = id => PAINT[fnv(id) % PAINT.length];   // a project's own paint, before the berths' own pick (overflow chips)
  const rightSide = s => s < RIGHT;
  const harbour = a => !(typeof a.plot === 'string' && PLOTS.has(a.plot) && a.plot !== 'boathouse');
  const atWork = a => a.state === 'working' || a.state === 'quiet';
  // --- live: L11 ci_gh --- a CI record: "CI" plus its workflow's name ("CI", "CI TESTS")
  const isCi = a => a.tool === 'ci';
  const CI_WORD = { running: 'RUNNING', green: 'GREEN', red: 'RED' };
  const ciName = a => { const t = String(a.title || '').toUpperCase().trim(); return !t ? 'CI' : /^CI\b/.test(t) ? t : 'CI ' + t; };
  // --- end L11 ---

  /* where agents stand (feet), absolute pixels. No two spots hide each other, nor the Harbour's waiting seats (y 194,
     x 131..159 and the chair at 221) or its failed spots on the spur (tests/test_harbour_berths.py checks it): side by side
     6 px apart or more; one behind another 12 px, or 7 px and a step to the side.
     A berth: two along its front, two behind (right of the slip; the 2nd left berth, over the seats' row, has four in
     front instead), then two more on the shore behind it. */
  const REL_R = [[5, 0], [20, 0], [11, -6], [26, -6], [4, -12], [18, -12]];
  const REL_L = [[25, 0], [10, 0], [19, -6], [4, -6], [4, -18], [18, -18]];
  const REL_L2 = [[24, 0], [17, 0], [10, 0], [3, 0], [10, -18], [24, -18]];
  const BERTH_SPOTS = SLOTS.map((x0, s) => (s < RIGHT ? REL_R : s === 4 ? REL_L2 : REL_L).map(([dx, dy]) => [x0 + dx, FEET + dy]));
  // the overflow mooring: two on its deck, then up the shore behind it in staggered rows (12: live.js's cap)
  const OVER_SPOTS = [[3, 0], [10, 0], [0, -12], [6, -12], [12, -12], [3, -19], [9, -19], [0, -26], [6, -26], [12, -26], [3, -33], [9, -33]]
    .map(([dx, dy]) => [OVER + dx, FEET + dy]);
  // the visitors' yard right of the boathouse, between the spur and the tree: three staggered rows of five
  const VISIT_SPOTS = [180, 173, 166].flatMap((y, r) => [0, 7, 14, 21, 28].map(dx => [257 + dx + (r === 1 ? 4 : 0), y]));
  const spotIn = (list, i) => { if (i < list.length) return list[i];   // past a list: the same spots, a step along (rare)
    const b = list[i % list.length], r = Math.floor(i / list.length); return [b[0] + 2 * r, b[1]]; };

  /* ---------- the model: which project has which berth (kept), read lazily from the live payload ---------- */
  const S = { payload: undefined, first: true, slot: new Map(), seen: new Map(), names: new Map(), paint: new Map(), shownIds: new Set(),
    overSeq: N, list: [], over: [], agents: new Map(), stand: new Map(), respot: new Set(), sail: new Map(),
    queue: [], launch: null, launchAt: null, launches: 0, moves: 0 };
  const fresh = () => (LIVE.on && LIVE.payload && typeof LIVE.payload === 'object' ? LIVE.payload : null);
  // where an agent at work at the Harbour stands: its berth, the overflow mooring, or the visitors' yard; with no wharf
  // built (no berth in use) one without a berth keeps live.js's own row by the door ('row': nothing there to clash with)
  const keyOf = a => {
    const s = typeof a.project === 'string' && S.agents.has(a.project) ? S.slot.get(a.project) : undefined;
    return s !== undefined ? (s < N ? 'p:' + a.project : 'over') : S.list.length || S.over.length ? 'visit' : 'row';
  };
  function pickPaint(id) {   // the first paint, from the project's own, that no other berth holder has
    const used = new Set(); for (const [o, s] of S.slot) if (o !== id && s < N && S.paint.has(o)) used.add(S.paint.get(o));
    const k0 = fnv(id) % PAINT.length;
    for (let k = 0; k < PAINT.length; k++) { const c = PAINT[(k0 + k) % PAINT.length]; if (!used.has(c)) return c; }
    return PAINT[k0];
  }
  const paintFor = id => S.paint.get(id) || paintOf(id);
  function sync() {
    const p = fresh();
    if (p === S.payload) return;
    S.payload = p;
    if (!p) { S.list = []; S.over = []; S.agents = new Map(); S.ci = new Map(); S.stand.clear(); S.sail.clear(); return; }   // stale: nothing drawn; berths remembered (L11: S.ci)
    const now = performance.now();
    const agents = (Array.isArray(p.agents) ? p.agents : []).filter(a => a && typeof a === 'object' && typeof a.id === 'string' && STATES.has(a.state));
    const ids = new Set();   // a project listed twice: its first entry
    const projects = (Array.isArray(p.projects) ? p.projects : []).filter(q => q && typeof q === 'object' && typeof q.id === 'string' && q.id && !ids.has(q.id) && ids.add(q.id))
      .map((q, i) => ({ id: q.id, name: String(q.name || q.id), order: Number.isFinite(q.berth) ? q.berth : 1e6 + i, isNew: q.new === true }))
      .sort((x, y) => x.order - y.order);
    const byProj = new Map();
    for (const a of agents) if (typeof a.project === 'string' && a.project) { if (!byProj.has(a.project)) byProj.set(a.project, []); byProj.get(a.project).push(a); }
    const wants = projects.filter(q => !byProj.has(q.id) || byProj.get(q.id).some(harbour));
    const live = new Set(wants.map(q => q.id));
    // --- live: L11 ci_gh --- a CI record (tool "ci", the ci_gh reader) is no agent: no figure, no count; its pennant and hover line
    S.ci = new Map(wants.map(q => [q.id, (byProj.get(q.id) || []).filter(isCi)]));
    S.agents = new Map(wants.map(q => [q.id, (byProj.get(q.id) || []).filter(a => harbour(a) && !isCi(a))]));
    // --- end L11 ---
    for (const q of wants) { S.seen.set(q.id, now); S.names.set(q.id, q.name); }
    for (const [id, t] of S.seen) if (now - t > GRACE_MS) { S.seen.delete(id); S.slot.delete(id); S.names.delete(id); S.paint.delete(id); }
    // who needs a berth: the overflow first (the oldest there first), then newcomers in the payload's order
    const holder = new Map(); for (const [id, s] of S.slot) if (s < N) holder.set(s, id);
    const need = wants.filter(q => !(S.slot.get(q.id) < N)).map((q, i) => [S.slot.has(q.id) ? S.slot.get(q.id) : Infinity, i, q])
      .sort((x, y) => x[0] - y[0] || x[1] - y[1]).map(x => x[2]);
    // the berths they may take: nobody's first; then ones held in their grace, the longest gone first
    const free = [];
    for (let s = 0; s < N; s++) if (!holder.has(s)) free.push(s);
    free.push(...[...holder].filter(([, id]) => !live.has(id)).sort((x, y) => S.seen.get(x[1]) - S.seen.get(y[1])).map(x => x[0]));
    for (const q of need) {
      const s = free.shift();
      if (s === undefined) { if (!S.slot.has(q.id)) S.slot.set(q.id, S.overSeq++); continue; }   // the overflow mooring (kept, in arrival order)
      const gone = holder.get(s); if (gone !== undefined) { S.slot.delete(gone); S.paint.delete(gone); }   // its grace gives way
      const up = S.slot.has(q.id) && !S.first;   // from the overflow mooring: it sails up, its agents walk over
      S.slot.set(q.id, s); S.paint.set(q.id, pickPaint(q.id));
      if (up) { S.sail.set(q.id, { t0: now, x0: OVER + 1 }); S.moves++; }
    }
    const row = q => ({ id: q.id, name: q.name, slot: S.slot.get(q.id) });
    S.list = wants.filter(q => S.slot.get(q.id) < N).map(row).sort((x, y) => x.slot - y.slot);
    S.over = wants.filter(q => S.slot.get(q.id) >= N).map(row).sort((x, y) => x.slot - y.slot);
    for (const q of wants) {   // a new project the page never showed: its boat is launched, once
      if (!S.first && q.isNew && !S.shownIds.has(q.id) && S.slot.get(q.id) < N) { S.launches++; S.queue.push({ id: q.id, at: now }); }
      S.shownIds.add(q.id);
    }
    S.first = false;
    // agents' own places: kept while they work at the same berth (or mooring, or yard); one whose place changed walks over
    const at = new Map(); for (const a of agents) if (harbour(a) && atWork(a)) at.set(a.id, keyOf(a));
    for (const [aid, e] of S.stand) if (at.get(aid) !== e.key) { S.stand.delete(aid); if (at.has(aid)) S.respot.add(aid); }
  }
  function spot(a) {   // LAMPLIGHTERS.berth: where an agent at work at the Harbour stands (null: live.js's own spot)
    sync();
    if (!S.payload || !a || typeof a.id !== 'string') return null;
    const k = keyOf(a);
    let e = S.stand.get(a.id);
    if (!e || e.key !== k) {
      const taken = new Set([...S.stand.values()].filter(o => o.key === k).map(o => o.i));   // numbered per berth, mooring or yard
      let i = 0; while (taken.has(i)) i++;
      S.stand.set(a.id, e = { key: k, i });
    }
    if (k === 'row') return null;   // live.js's own spot for it
    return spotIn(k === 'visit' ? VISIT_SPOTS : k === 'over' ? OVER_SPOTS : BERTH_SPOTS[S.slot.get(a.project)], e.i).slice();
  }
  function facts(id) {   // a berth's counts, from its agents
    const list = S.agents.get(id) || [], f = { working: 0, waiting: 0, error: 0, quiet: 0, ok: 0, files: 0, commits: 0, pushed: false, ci: null };
    for (const a of list) { f[a.state]++; f.files += a.files | 0; f.commits += a.commits | 0; if (a.pushed) f.pushed = true;
      if (a.ci === 'red' || (a.ci === 'running' && f.ci !== 'red') || (a.ci === 'green' && !f.ci)) f.ci = a.ci; }
    for (const a of (S.ci && S.ci.get(id)) || []) {   // --- live: L11 ci_gh --- the project's CI runs: the pennant only
      if (a.ci === 'red' || (a.ci === 'running' && f.ci !== 'red') || (a.ci === 'green' && !f.ci)) f.ci = a.ci; }
    return f;
  }
  function tipOf(id) {
    const list = S.agents.get(id) || [], f = facts(id), name = String(S.names.get(id) || id).toUpperCase();
    const out = [name + ': ITS BERTH AT THE HARBOUR', list.length ? plural(list.length, 'AGENT') + ' ON THIS PROJECT' : 'NO AGENT ON IT NOW'];
    for (const a of list.slice(0, 4)) out.push(String(a.title || 'AN AGENT') + ': ' + WORD[a.state]);
    if (list.length > 4) out.push('AND ' + (list.length - 4) + ' MORE');
    const runs = (S.ci && S.ci.get(id)) || [];   // --- live: L11 ci_gh --- each CI run by its workflow's name
    for (const a of runs.slice(0, 2)) out.push(ciName(a) + ': ' + (CI_WORD[a.ci] || WORD[a.state]));
    const c = [];
    if (f.commits) c.push(plural(f.commits, 'COMMIT'));
    if (f.pushed) c.push('PUSHED: SAIL UP');
    if (f.ci && !runs.length) c.push('CI ' + f.ci.toUpperCase());
    if (c.length) out.push(c.join(', '));
    return out;
  }

  /* ---------- what moves: the launch queue (kept only TUNE.wait) and the boats sailing up from the overflow ---------- */
  function pending(now) {
    S.queue = S.queue.filter(q => now - q.at <= TUNE.wait && S.list.some(b => b.id === q.id));
    return S.queue.map(q => q.id);
  }
  function sailPos(id, now) {   // a boat moving up from the overflow mooring: along the water to its berth
    const e = S.sail.get(id), b = e && S.list.find(q => q.id === id);
    if (!b) { S.sail.delete(id); return null; }
    const tx = SLOTS[b.slot] + 5, dur = Math.abs(tx - e.x0) / TUNE.glide * 1000, u = dur > 0 ? (now - e.t0) / dur : 1;
    if (u >= 1) { S.sail.delete(id); return null; }
    const k = u * u * (3 - 2 * u); return { x: Math.round(e.x0 + (tx - e.x0) * k), u };
  }
  const away = (now, still) => (still || !running ? new Set() : new Set([...(S.launch ? [S.launch.id] : []), ...pending(now), ...[...S.sail.keys()].filter(id => sailPos(id, now))]));

  /* ---------- drawing ---------- */
  function wharf(x0, x1) {   // a timber deck from x0 to x1 (exclusive), its piles under the front edge
    R(x0 - 1, DECK - 1, x1 - x0 + 2, 8, OL);
    for (let y = DECK; y < DECK + 6; y++) R(x0, y, x1 - x0, 1, y === DECK ? '#8a6444' : y === DECK + 5 ? '#3e2a20' : (y - DECK) % 2 ? '#6a4a32' : '#5e4230');
    for (let x = x0 + 4; x < x1; x += 9) R(x, DECK + 1, 1, 4, '#4a3426');   // plank joints
    for (let x = x0 + 2; x < x1 - 1; x += 7) { R(x, DECK + 7, 2, 3, '#2e2026'); P(x, DECK + 7, '#4a3640'); }
  }
  // a rowing boat seen from above, 20 x 8, the bow to the right: g gunwale (the project's paint), h hull, t thwarts, k keel
  const BOAT_PX = [
    '..oooooooooooo......',
    '.oggggggggggggooo...',
    'oghhthhhhthhhhgggoo.',
    'oghhtkkkktkkkkhhhggo',
    'oghhthhhhthhhhgggoo.',
    '.oggggggggggggooo...',
    '..oooooooooooo......'];
  const BOAT_DOWN = BOAT_PX[0].split('').map((_, c) => BOAT_PX.map((row, r) => BOAT_PX[BOAT_PX.length - 1 - r][c]).join(''));   // bow down
  // the overflow mooring's dinghy under a tarp, 13 x 5
  const DINGHY = ['.ooooooooo...', 'oggggggggggo.', 'oggggggggggoo', 'oggggggggggo.', '.ooooooooo...'];
  function boat(x, y, id, f) {   // y = its waterline (the bottom edge). Sail up: pushed. A pennant: CI
    const paint = paintFor(id);
    sprite(BOAT_PX, x, y - 6, { o: OL, g: paint, h: '#7a5236', t: '#4e3222', k: '#6a4630' });
    R(x + 2, y - 5, 10, 1, mixHex(paint, '#ffffff', 0.35));   // light on the near gunwale
    R(x + 1, y + 1, 18, 1, 'rgba(160,200,240,0.16)');          // a still line of water along her side
    rig(x, y, f);
  }
  function rig(x, y, f) {   // the mast, the sail (pushed), the CI pennant
    if (!f || !(f.pushed || f.ci)) return;
    R(x + 9, y - 14, 1, 11, '#c8b088');   // the mast, stepped in the middle
    if (f.pushed) for (let j = 0; j < 7; j++) { const w = j < 4 ? j + 1 : 7 - j; R(x + 10, y - 13 + j, w, 1, j % 3 === 2 ? '#d8d2c0' : '#f4f0e4'); }
    if (f.ci) { R(x + 5, y - 15, 4, 2, CI_COL[f.ci]); P(x + 4, y - 15, CI_COL[f.ci]); }
  }
  // --- live: L11 ci_gh --- a moored boat lies in front of the wharf, so its rig stands in front of the figures on it
  // (drawn again in the scene layer, after live.js's figures: the pennant of a running CI is never hidden by a cart)
  function rigs(still) {
    const gone = away(performance.now(), still);
    for (const b of S.list) if (!gone.has(b.id)) rig(SLOTS[b.slot] + 5, BOAT, facts(b.id));
  }
  // --- end L11 ---
  function sea(t, still) {   // the wharf, bollards and moored boats: under the buildings and the figures
    sync();
    const now = performance.now();
    if (S.respot.size) {   // figures whose place moved (a project moved up a berth): they walk over (instantly in a still scene)
      const ids = [...S.respot]; S.respot.clear();
      if (typeof LAMPLIGHTERS === 'object' && LAMPLIGHTERS && typeof LAMPLIGHTERS.respot === 'function') LAMPLIGHTERS.respot(ids, !!still || !running);
    }
    const right = S.list.filter(b => rightSide(b.slot)).map(b => SLOTS[b.slot]);
    const left = S.list.filter(b => !rightSide(b.slot)).map(b => SLOTS[b.slot]).concat(S.over.length ? [OVER] : []);
    if (right.length) wharf(WHARF_R, Math.max(...right) + PITCH);
    if (left.length) wharf(Math.min(...left), WHARF_L);
    const gone = away(now, still);
    for (const b of S.list) {
      const x0 = SLOTS[b.slot], bx = x0 + 2;
      R(bx - 1, DECK - 2, 3, 3, '#090a16'); P(bx, DECK - 2, '#b0a080'); P(bx, DECK - 1, '#7a6a50');   // the bollard
      if (gone.has(b.id)) continue;   // its boat is on its way (or waits for its launch)
      R(bx, DECK + 6, 1, 3, '#b8a07a');   // the mooring line
      boat(x0 + 5, BOAT, b.id, facts(b.id));
    }
    if (S.over.length) {   // the shared mooring: one dinghy under a tarp, its bollard
      R(OVER, DECK - 2, 3, 3, OL); P(OVER + 1, DECK - 2, '#b0a080'); P(OVER + 1, DECK - 1, '#7a6a50'); R(OVER + 1, DECK + 6, 1, 3, '#b8a07a');
      sprite(DINGHY, OVER + 1, BOAT - 4, { o: OL, g: '#4a4e6c' });
      R(OVER + 2, BOAT - 3, 9, 1, '#6f7498');
      for (let x = OVER + 3; x < OVER + 12; x += 3) P(x, BOAT - 2, '#8a90b8');   // the tarp's ties
    }
  }
  function launchPos(L, now) {   // where the launched boat is: down the slipway, a splash, along the water to its berth
    const b = S.list.find(q => q.id === L.id); if (!b) return null;
    const x = SLIP_X - 10, tx = SLOTS[b.slot] + 5, dt = now - L.t0, glide = Math.abs(tx - x) / TUNE.glide * 1000;
    if (dt < TUNE.slide) { const u = dt / TUNE.slide; return { x: SLIP_X - 3, y: Math.round(SLIP_Y + (SPLASH - SLIP_Y) * u * u), phase: 'slide', u }; }
    if (dt < TUNE.slide + TUNE.splash) return { x, y: SPLASH, phase: 'splash', u: (dt - TUNE.slide) / TUNE.splash };
    const g2 = dt - TUNE.slide - TUNE.splash;
    if (g2 < glide) { const u = g2 / glide, e = u * u * (3 - 2 * u); return { x: Math.round(x + (tx - x) * e), y: BOAT, phase: 'glide', u }; }
    return null;
  }
  function scene(t, still) {   // the launch, over the slipway and the water (one boat after another); boats sailing up
    sync();
    rigs(still);   // --- live: L11 ci_gh ---
    if (still || !running) { S.queue.length = 0; S.launch = S.launchAt = null; S.sail.clear(); return; }   // a still scene: the boats are simply moored
    const now = performance.now();
    for (const id of [...S.sail.keys()]) { const q = sailPos(id, now); if (q) boat(q.x, BOAT, id, null); }
    let pos = S.launch ? launchPos(S.launch, now) : null;
    while (!pos) {
      S.launch = S.launchAt = null;
      pending(now);   // (a launch that waited past TUNE.wait is dropped: that boat is simply moored)
      const q = S.queue.shift(); if (!q) return;
      S.launch = { id: q.id, t0: now }; pos = launchPos(S.launch, now);
    }
    S.launchAt = pos;
    if (pos.phase === 'slide') {   // bow first down the slipway (y: where her bow is)
      const paint = paintFor(S.launch.id);
      sprite(BOAT_DOWN, pos.x, pos.y - BOAT_DOWN.length + 1, { o: OL, g: paint, h: '#7a5236', t: '#4e3222', k: '#6a4630' });
      R(pos.x + 1, pos.y - BOAT_DOWN.length + 3, 1, 10, mixHex(paint, '#ffffff', 0.35));
    } else boat(pos.x, pos.y, S.launch.id, null);
    const k = pos.phase === 'splash' ? pos.u : pos.phase === 'glide' && pos.u < 0.2 ? 1 : -1;
    if (k >= 0) {   // the splash: rings spreading both ways, a little spray
      const r = 4 + Math.round(k * 9), cx = SLIP_X, cy = SPLASH + 2;
      for (const s of [-1, 1]) { R(cx + s * (r + 6) - 1, cy, 3, 1, '#cfe8ff'); R(cx + s * (r + 11) - 1, cy + 1, 2, 1, '#8fc0e8'); }
      if (k < 0.6) { P(cx - 3, cy - 6 - Math.round(k * 4), '#e8f6ff'); P(cx + 4, cy - 5 - Math.round(k * 3), '#e8f6ff'); P(cx, cy - 7 - Math.round(k * 5), '#ffffff'); }
    }
  }
  /* a berth's name tag: the whole name when it fits; else a short form no other tag and no other live project's name has:
     its own word (one no other name has: BOAT for Paper Boat beside Paper Kite), else a cut with a dot as the room cuts
     names (PAPER K.), never the start of another name, else its own word cut (INDEX. for Tidebook Indexer), else its
     first words cut to their initials (P. KITE). Nothing fits: no tag (hover tells). Two projects with the very same
     name: the same tag (it is the truth). */
  const words = s => s.split(/\s+/).filter(Boolean);
  function tagsFor(rooms) {   // rooms: [[berth, px]] in berth order -> Map id -> tag
    const all = S.list.concat(S.over).map(q => [q.id, String(q.name).toUpperCase().trim()]), out = new Map(), used = new Set();
    for (const [b, room] of rooms) {
      const nm = String(b.name).toUpperCase().trim(), others = all.filter(o => o[0] !== b.id).map(o => o[1]);
      const ow = new Set(others.flatMap(words)), cands = [nm];
      const own = words(nm).find(w => !ow.has(w)); if (own) cands.push(own);
      for (let k = nm.length - 1; k >= 2; k--) { const c = nm.slice(0, k).trimEnd(); if (!others.some(o => o.startsWith(c))) cands.push(c + '.'); }
      if (own) for (let k = own.length - 1; k >= 3; k--) { const c = own.slice(0, k); if (![...ow].some(x => x.startsWith(c))) cands.push(c + '.'); }   // INDEX.
      const w = words(nm); if (w.length > 1) cands.push(w.slice(0, -1).map(x => x[0] + '.').join(' ') + ' ' + w[w.length - 1]);   // P. KITE
      const fits = s => !!s && textW(s) <= room;
      let tag = cands.find(s => fits(s) && !used.has(s) && !others.includes(s)) || '';
      if (!tag && others.includes(nm) && fits(nm)) tag = nm;
      if (tag) used.add(tag);
      out.set(b.id, tag);
    }
    return out;
  }
  function lights(t, still) {   // the names on the water, the "+N" tag, the tooltips; SEEN.berths
    sync();
    const now = performance.now(), gone = away(now, still), rooms = [];
    const overW = textW('+' + S.over.length) + 4, overX = OVER + Math.round((OVER_W - overW) / 2);
    const overEnd = S.over.length ? Math.max(OVER + OVER_W, overX + overW + 1) : BOUND[0];
    for (const side of [S.list.filter(b => rightSide(b.slot)), S.list.filter(b => !rightSide(b.slot))]) {
      // a name gets half the way to each neighbour's boat (the slipway, the lighthouse rock and the "+N" bound the ends)
      const order = side.slice().sort((a, b) => SLOTS[a.slot] - SLOTS[b.slot]), right = !!side.length && rightSide(side[0].slot);
      const lo = right ? BOUND[2] : overEnd, hi = right ? BOUND[3] : BOUND[1], cxOf = b => SLOTS[b.slot] + 15;
      order.forEach((b, k) => {
        const cx = cxOf(b), prev = order[k - 1], next = order[k + 1];
        const a = prev ? (cxOf(prev) + cx) / 2 + 1 : lo, z = next ? (cxOf(next) + cx) / 2 - 1 : hi;
        rooms.push([b, 2 * Math.min(cx - a, z - cx) - 4]);
      });
    }
    rooms.sort((x, y) => x[0].slot - y[0].slot);
    const tagOf = tagsFor(rooms), shown = [], tags = [];
    for (const [b] of rooms) {
      const s = tagOf.get(b.id), launching = !!(S.launch && S.launch.id === b.id), moving = gone.has(b.id), f = facts(b.id);
      if (s && !moving) tags.push([s, SLOTS[b.slot] + 15]);
      tipBox([SLOTS[b.slot], DECK - 14, PITCH, 34], tipOf(b.id));
      shown.push({ id: b.id, name: b.name, slot: b.slot, x: SLOTS[b.slot], w: PITCH, tag: moving ? '' : s, full: s, agents: (S.agents.get(b.id) || []).map(a => a.id),
        paint: paintFor(b.id), sail: f.pushed, ci: f.ci, launching, sailing: S.sail.has(b.id) && moving, queued: moving && !launching && !S.sail.has(b.id) });
    }
    for (const [s, cx] of tags) { const w = textW(s) + 4, x = Math.round(cx - w / 2);
      R(x - 1, TAG - 1, w + 2, 9, OL); R(x, TAG, w, 7, '#14243a'); text(s, x + 2, TAG + 1, '#cfe2ff'); }
    if (S.over.length) {
      const n = S.over.length, names = S.over.slice(0, 6).map(q => String(q.name).toUpperCase()).concat(n > 6 ? ['AND ' + (n - 6) + ' MORE'] : []);
      const lines = [plural(n, 'MORE PROJECT') + ' SHARE THIS MOORING', 'AT MOST ' + N + ' BERTHS ON THE WHARF'].concat(names);
      R(overX - 1, TAG - 1, overW + 2, 9, OL); R(overX, TAG, overW, 7, '#2a1a24'); text('+' + n, overX + 2, TAG + 1, '#ffd59a');
      // the dinghy and its tag come first (the serpent's big box lies under their left edge); the deck after the figures
      TIPS.unshift({ box: [Math.min(OVER, overX - 1), BOAT - 6, Math.max(OVER_W, overX + overW + 1 - OVER), 225 - (BOAT - 6)], tip: lines });
      tipBox([OVER, DECK - 14, OVER_W, 20], lines);
    }
    SEEN.berths = { list: shown, over: S.over.map(q => q.id), overAt: S.over.length ? OVER : null, launches: S.launches, moves: S.moves,
      queue: S.queue.map(q => q.id), sail: [...S.sail.keys()].map(id => ({ id, x: (sailPos(id, now) || {}).x })).filter(q => q.x !== undefined),
      launch: S.launch && S.launchAt ? { id: S.launch.id, phase: S.launchAt.phase, x: S.launchAt.x, y: S.launchAt.y } : null };
  }

  /* ---------- for the Harbour room: the same berths, in the same order and paint ---------- */
  // a Lamplighter as live.js draws one standing: live.js's own painter and coats (LAMPLIGHTERS.paintFigure / coatOf); the
  // copy below is only a fallback for a page without them (tests/test_harbour_berths.py checks the coats agree)
  const COAT = { 'claude-code': '#8a5638', codex: '#3d7468' };
  const COATS = ['#3d5a8e', '#6a4a8a', '#7a6a3a', '#4a6a3a', '#8a3a4a', '#3a6a7a', '#7a4a6a', '#5a5a7a'];
  const GLASS = { working: '#fff0b0', waiting: '#ff9a1f', error: '#ff4a4a', quiet: '#2e3244', ok: '#7ff0a8' };
  const GLOW = { working: '#ffc860', waiting: '#ff9a1f', error: '#ff3a3a', ok: '#3dff8a' };
  const LL = () => (typeof LAMPLIGHTERS === 'object' && LAMPLIGHTERS ? LAMPLIGHTERS : {});
  const ownCoat = tool => { const t = String(tool || '').toLowerCase(); return Object.prototype.hasOwnProperty.call(COAT, t) ? COAT[t] : COATS[fnv(t) % COATS.length]; };
  const coatOf = tool => (typeof LL().coatOf === 'function' ? LL().coatOf(tool) : ownCoat(tool));
  function figure(fx, fy, a, face) {   // feet at (fx, fy); returns where its lantern glows (null: a dark lantern)
    if (typeof LL().paintFigure === 'function') return LL().paintFigure(fx, fy, a, face);
    const st = a.state, dim = st === 'quiet', coat = dim ? '#3a3d48' : ownCoat(a.tool), skin = dim ? '#8e7e70' : '#e2b48a', ox = fx - 4, top = fy - 11;
    const px = [], m = (dx, dy, w, h, c) => px.push([face > 0 ? ox + dx : ox + 8 - dx - w, top + dy, w, h, c]);
    R(fx - 3, fy + 1, 8, 1, 'rgba(0,0,0,0.4)');
    m(2, 0, 4, 1, '#241c30'); m(1, 1, 6, 1, '#241c30'); m(2, 2, 4, 2, skin); m(4, 2, 1, 1, '#2a1a1a');
    m(2, 4, 4, 1, coat); m(1, 5, 6, 3, coat); m(1, 7, 6, 1, mixHex(coat, '#000000', 0.3)); m(2, 8, 4, 1, coat);
    m(2, 9, 1, 2, '#2a2838'); m(5, 9, 1, 2, '#2a2838'); m(1, 11, 2, 1, '#151018'); m(5, 11, 2, 1, '#151018');
    const lantern = (dx, dy, col) => { m(dx + 1, dy - 1, 1, 1, '#262636'); m(dx, dy, 3, 1, '#262636'); m(dx, dy + 1, 3, 2, col); m(dx + 1, dy + 3, 1, 1, '#262636'); };
    let lx, ly;
    if (st === 'error') { for (const [dx, dy] of [[6, 6], [7, 5], [8, 4], [9, 3], [10, 2]]) m(dx, dy, 1, 1, '#8a6a46'); lantern(9, 3, GLASS.error); lx = 10; ly = 5; }
    else { for (const [dx, dy] of [[6, 6], [6, 5], [7, 4], [7, 3], [8, 2], [8, 1], [9, 0], [9, -1]]) m(dx, dy, 1, 1, '#8a6a46'); m(6, 6, 1, 1, skin); lantern(9, -1, GLASS[st] || GLASS.working); lx = 10; ly = 1; }
    for (const [x, y, w, h] of px) { R(x - 1, y, w + 2, h, OL); R(x, y - 1, w, h + 2, OL); }
    for (const [x, y, w, h, c] of px) R(x, y, w, h, c);
    return GLOW[st] ? { x: face > 0 ? ox + lx : ox + 8 - lx - 1, y: top + ly, col: GLOW[st] } : null;
  }
  function view() {
    sync();
    const berth = q => ({ id: q.id, name: String(q.name), slot: q.slot, paint: paintFor(q.id), agents: (S.agents.get(q.id) || []).slice(), facts: facts(q.id), tip: tipOf(q.id) });
    return { on: !!S.payload, berths: S.list.map(berth), over: S.over.map(berth), cap: N };
  }

  const api = { S, SLOTS, OVER, OVER_W, PITCH, FEET, SHORE, GRACE_MS, TUNE, BERTH_SPOTS, OVER_SPOTS, VISIT_SPOTS,
    sync, spot, view, paintOf, paintFor, boat, figure, coatOf, ownCoat, BOAT_PX, WORD };
  if (typeof LAMPLIGHTERS === 'object' && LAMPLIGHTERS) LAMPLIGHTERS.berth = a => spot(a);
  function guard(fn) { return (...a) => { try { fn(...a); } catch (e) { if (!S.err) { S.err = String(e && e.stack || e); console.error(e); } SEEN.berthsError = S.err; } }; }
  TOWN_LAYERS.sea.push(guard(sea)); TOWN_LAYERS.scene.push(guard(scene)); TOWN_LAYERS.lights.push(guard(lights));
  kick();   // town.js drew its first frame before this script ran: a still scene draws again now, with the layers
  return api;
})();
