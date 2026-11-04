'use strict';
/* LAMPTOWN: the Lamplighters, one small figure per live agent, carrying a lantern (the live town; the Watcher's payload is
   LIVE.payload, spec lamptown-live/1, see livetown.py). Inlined by `lamptown build` after town.js; it draws through
   town.js's TOWN_LAYERS hooks (scene: the figures, graded with the town; lights: glows, tags and cards, not graded).
   The lantern carries the state:
     working   a warm steady lantern, at the building of the agent's plot
     waiting   amber: it sits on the Watcher's chair by the door, lantern set down on the step, and slumps the longer it waits
     ok        done: its lantern hangs on the hook by the door, no figure, for 15 min (then the payload drops it)
     error     failed: a red lantern, lowered; it steps out of the way onto the path
     quiet     gone quiet: standing, its lantern dark, a grey "?" over it (never mistaken for done: done has no figure)
   They walk the town's real footpaths (ROUTES in town.js, plus a short spur to each door). A state that changes while a
   figure walks waits until the walk ends (a new building reroutes it at once; its lantern still waits). Subagents (and
   theirs) follow the top one as smaller helpers: a waiting, failed or quiet helper always walks with it, working ones
   past two are counted on its card. At most CAP figures (an agent waiting for you or failed is never left out); a figure
   pushed out steps out to its path and is gone; each building's figures not drawn are one "+N" tag at the end of its own
   row. An agent's changed files ride a cart behind it with one total tag.
   Calm: nothing moves unless the payload changed. A stale payload (no Watcher) shows no figures.
   Names only on screen: title, project, tool, state, counts. No toasts. What the last frame drew is in SEEN.agents. */
const LAMPLIGHTERS = (() => {
  const CAP = 12, FOLLOW = 2, FOLLOW_MAX = 4, DONE_MS = 15 * 6e4;
  // payload values are only ever keys we own: "constructor" or "__proto__" is not a plot, a tool coat or a count
  const own = (o, k) => typeof k === 'string' && Object.prototype.hasOwnProperty.call(o, k);
  const count = v => { const n = Math.floor(Number(v)); return n > 0 ? Math.min(999999999, n) : 0; };
  const RANK = { waiting: 0, error: 1, working: 2, quiet: 3 };
  const STATES = new Set(['working', 'waiting', 'ok', 'error', 'quiet']);
  // lantern glass and its glow by state (quiet: dark, no glow)
  const GLASS = { working: '#fff0b0', waiting: '#ff9a1f', error: '#ff4a4a', quiet: '#2e3244', ok: '#7ff0a8' };
  const GLOW = { working: '#ffc860', waiting: '#ff9a1f', error: '#ff3a3a', ok: '#3dff8a' };
  // coats: picked from the tool's name; only the two built-in tools have their own (never a vendor logo or colour scheme)
  const COAT = { 'claude-code': '#8a5638', codex: '#3d7468' };
  const COATS = ['#3d5a8e', '#6a4a8a', '#7a6a3a', '#4a6a3a', '#8a3a4a', '#3a6a7a', '#7a4a6a', '#5a5a7a'];
  const HAT = '#241c30', SKIN = '#e2b48a', EYE = '#2a1a1a', TROU = '#2a2838', BOOT = '#151018', POLE = '#8a6a46', FRAME = '#262636', WOOD = '#7a5530';
  const DIMCOAT = '#3a3d48', DIMSKIN = '#8e7e70';
  const shade = (h, f) => mixHex(h, '#000000', 1 - f);
  const coatOf = tool => { const t = String(tool || '').toLowerCase(); if (own(COAT, t)) return COAT[t];
    let n = 2166136261; for (const ch of t) n = Math.imul(n ^ ch.charCodeAt(0), 16777619); return COATS[(n >>> 0) % COATS.length]; };

  /* ---------- where they stand: per plot, in native pixels; every point is a figure's feet ----------
     join: where the plot's spur leaves the town's footpaths; spur: the short way from there to the apron in front of the
     door (its last point); stand: working spots; seats: the Watcher's chair first, then more seats; out: failed spots on
     the path; hook: the bracket the done lanterns hang from, hang: where a figure stands to hang its lantern there;
     more: where the building's "+N" tag stands, at the end of its row. No two spots hide each other (a test checks it):
     side by side they are 6 px apart or more; one behind another, 12 px, or 7 px and a step to the side (its face and
     lantern show). 8 working spots (a building shows no more working figures than that), the chair and 5 stools, 4 out
     spots; past the lists, a second rank (spotIn) */
  const PLACE = {
    tower: { join: [56, 75], spur: [[60, 77]], stand: [[64, 77], [71, 77], [78, 77], [67, 70], [74, 70], [81, 70], [96, 85], [103, 85]],
      seats: [[50, 79], [36, 79], [24, 79], [17, 79], [10, 79], [20, 71]], out: [[84, 86], [92, 94], [100, 94], [108, 94]],
      hook: [33, 63], hang: [36, 79], more: [118, 85] },
    library: { join: [199, 74], spur: [], stand: [[180, 76], [173, 77], [222, 77], [229, 78], [166, 78], [236, 79], [159, 79], [243, 80]],
      seats: [[190, 76], [208, 77], [215, 77], [186, 85], [211, 85], [218, 85]], out: [[214, 94], [222, 94], [230, 94], [238, 94]],
      hook: [186, 61], hang: [186, 76], more: [254, 81] },
    gate: { join: [318, 68], spur: [[318, 77]], stand: [[356, 78], [311, 77], [304, 77], [361, 85], [289, 77], [282, 77], [275, 77], [268, 77]],
      seats: [[341, 77], [334, 78], [327, 78], [347, 87], [353, 88], [320, 86]], out: [[307, 68], [300, 68], [296, 75], [299, 84]],
      hook: [353, 60], hang: [354, 77], more: [257, 77] },
    // the Lab's failed ones step out onto its path in a zigzag (a quiet one's "?" never covers the next one's head)
    lab: { join: [114, 94], spur: [[114, 148]], stand: [[102, 148], [109, 148], [66, 148], [59, 148], [52, 148], [45, 148], [38, 148], [31, 148]],
      seats: [[80, 148], [94, 148], [73, 149], [77, 156], [84, 156], [98, 156]], out: [[114, 122], [122, 114], [114, 106], [122, 98]],
      hook: [92, 126], hang: [93, 148], more: [20, 148] },
    workshop: { join: [248, 150], spur: [[262, 151]], stand: [[284, 151], [291, 151], [317, 151], [324, 151], [331, 151], [338, 151], [345, 151], [352, 151]],
      seats: [[299, 151], [308, 151], [277, 151], [303, 158], [312, 158], [281, 158]], out: [[256, 157], [263, 157], [259, 165], [252, 165]],
      hook: [336, 133], hang: [337, 151], more: [362, 151] },
    // the Harbour row alternates across the slip; L10 gives each project its own berth instead (api.berth); its waiting
    // queue runs left along the shore (W2-left: then on, up to the cap of 12, clear of the berths' shore spots and the
    // overflow mooring: a second rank in front would stand on the wharf, and one behind the stools would hide the belt's
    // crates. W2-left-fix: a seated figure's lantern sits low on its door side, so a rank 7 px behind puts lanterns on
    // the faces in front: two seats on the grass between the bush and the belt, then one more row on the open shore
    // between the trees left of the overflow mooring; tests/test_harbour_berths.py checks no face, tree or belt is hidden)
    boathouse: { join: [248, 150], spur: [[248, 194]], stand: [[229, 195], [184, 195], [236, 195], [177, 195], [243, 196], [170, 195]],
      seats: [[221, 194], [159, 194], [152, 194], [145, 194], [138, 194], [131, 194],
        [113, 190], [123, 178], [70, 194], [63, 194], [56, 194], [49, 194]], out: [[248, 172], [251, 180], [245, 164], [248, 156]],
      hook: [183, 171], hang: [184, 195], more: [262, 196] },
  };
  for (const k in PLACE) { const p = PLACE[k]; p.apron = p.spur.length ? p.spur[p.spur.length - 1] : p.join; }
  const plotKey = p => (own(PLACE, p) ? p : 'boathouse');   // an unknown plot: the Harbour (the agents' own building)
  // past a list: a second rank in front of it, a step to the side (a neat queue, never on top of the first rank)
  const spotIn = (list, i) => { if (i < list.length) return list[i];
    const n = list.length, j = i - n, r = 1 + Math.floor(j / n), b = list[j % n]; return [b[0] + 4 * r, Math.min(197, b[1] + 7 * r)]; };
  const SQUARE = [199, 104];   // the fountain square: where a new lamplighter sets out from
  const TOWN_ORDER = ['tower', 'library', 'gate', 'lab', 'workshop', 'boathouse'];

  /* ---------- the path graph: town.js's ROUTES (every point a node; a segment split wherever another node lies on it),
     the paved stem down to the fountain square, and each plot's join point ---------- */
  const key = p => p[0] + ',' + p[1];
  const NODES = new Map(), ADJ = new Map();
  (() => {
    const add = p => { if (!NODES.has(key(p))) NODES.set(key(p), p.slice()); };
    const segs = [];
    for (const r of ROUTES) { r.pts.forEach(add); for (let i = 0; i < r.pts.length - 1; i++) segs.push([r.pts[i], r.pts[i + 1]]); }
    segs.push([[199, 94], SQUARE]); add(SQUARE);
    for (const k in PLACE) add(PLACE[k].join);
    const onSeg = (p, a, b) => (b[0] - a[0]) * (p[1] - a[1]) === (b[1] - a[1]) * (p[0] - a[0])
      && p[0] >= Math.min(a[0], b[0]) && p[0] <= Math.max(a[0], b[0]) && p[1] >= Math.min(a[1], b[1]) && p[1] <= Math.max(a[1], b[1]);
    const link = (a, b) => { const l = Math.hypot(a[0] - b[0], a[1] - b[1]); if (!l) return;
      for (const [u, v] of [[a, b], [b, a]]) { if (!ADJ.has(key(u))) ADJ.set(key(u), []); ADJ.get(key(u)).push([key(v), l]); } };
    for (const [a, b] of segs) {
      const on = [...NODES.values()].filter(p => onSeg(p, a, b)).sort((p, q) => Math.hypot(p[0] - a[0], p[1] - a[1]) - Math.hypot(q[0] - a[0], q[1] - a[1]));
      for (let i = 0; i < on.length - 1; i++) link(on[i], on[i + 1]);
    }
  })();
  function shortest(from, to) {   // node keys -> points after `from`, up to and with `to` (Dijkstra; a couple of dozen nodes)
    const dist = new Map([[from, 0]]), prev = new Map(), done = new Set();
    for (;;) {
      let u = null, best = Infinity;
      for (const [k, d] of dist) if (!done.has(k) && d < best) { best = d; u = k; }
      if (u === null || u === to) break;
      done.add(u);
      for (const [v, l] of ADJ.get(u) || []) if (!dist.has(v) || dist.get(v) > best + l) { dist.set(v, best + l); prev.set(v, u); }
    }
    if (!dist.has(to)) return [NODES.get(to)];
    const out = []; for (let u = to; u !== from; u = prev.get(u)) out.unshift(NODES.get(u));
    return out;
  }
  const nearestNode = (x, y) => { let b = null, bd = Infinity; for (const [k, p] of NODES) { const d = Math.hypot(p[0] - x, p[1] - y); if (d < bd) { bd = d; b = k; } } return b; };
  const hop = (a, b) => (a[0] === b[0] || a[1] === b[1] ? [b] : [[b[0], a[1]], b]);   // across, then up or down: no diagonals
  function chain(from, pts) { const out = []; let c = from; for (const p of pts) { out.push(...hop(c, p)); c = p; } return out; }

  /* ---------- the figures ---------- */
  const S = { figs: new Map(), last: undefined, first: true, recv: 0, atMs: 0, speed: 16, pin: null, hits: [], followAt: new Map(), drawn: [],
    root: new Map(), moreAt: {} };
  const api = { S, PLACE, NODES, CAP, spotIn, set speed(v) { S.speed = v; }, get speed() { return S.speed; },
    berth: null };   // ticket L10: a function (agent, slot) -> [x, y] or null: the agent's spot at its project's berth

  const ageMs = a => { const t = Date.parse(a && a.since); return isNaN(t) ? 0 : Math.max(0, S.atMs - t + (performance.now() - S.recv)); };
  const others = (f, plot) => [...S.figs.values()].filter(o => o !== f && o.plot === plot);
  const free = (f, kind) => { const used = new Set(others(f, f.plot).map(o => o[kind])); let i = 0; while (used.has(i)) i++; return i; };
  const urgent = a => a.state === 'waiting' || a.state === 'error';
  // how many working (or quiet) figures a building shows; the Harbour's berths (L10) hold their own
  const roomAt = plot => (plot === 'boathouse' && typeof api.berth === 'function' ? Infinity : PLACE[plot].stand.length);
  function spotFor(f) {
    const P = PLACE[f.plot];
    f.slot = f.shown === 'working' || f.shown === 'quiet' ? (f.slot >= 0 ? f.slot : free(f, 'slot')) : -1;
    f.seat = f.shown === 'waiting' ? (f.seat >= 0 ? f.seat : free(f, 'seat')) : -1;
    f.out = f.shown === 'error' ? (f.out >= 0 ? f.out : free(f, 'out')) : -1;
    if (f.shown === 'ok') return P.hang;
    if (f.seat >= 0) return spotIn(P.seats, f.seat);
    if (f.out >= 0) return spotIn(P.out, f.out);
    if (f.plot === 'boathouse' && typeof api.berth === 'function') {   // L10's berths
      try { const b = api.berth(f.a, f.slot); if (Array.isArray(b) && b.length === 2 && b.every(Number.isFinite)) return b.map(Math.round); } catch (e) { console.error(e); }
    }
    return spotIn(P.stand, f.slot);
  }
  function routeTo(f, spot) {   // the walk from where f is to spot at f.plot
    const P = PLACE[f.plot], here = [Math.round(f.x), Math.round(f.y)];
    if (f.at === f.plot) return chain(here, [spot]);
    let pts = [], from;
    if (own(PLACE, f.at)) {   // back out along its own spur first
      const Q = PLACE[f.at];
      pts = chain(here, [Q.apron].concat(Q.spur.slice(0, -1).reverse(), [Q.join]));
      from = key(Q.join);
    } else { from = nearestNode(here[0], here[1]); pts = chain(here, [NODES.get(from)]); }
    pts = pts.concat(shortest(from, key(P.join)));
    return pts.concat(chain(P.join, P.spur.concat([spot])));
  }
  const tidy = path => path.filter((p, i, a) => i === 0 || p[0] !== a[i - 1][0] || p[1] !== a[i - 1][1]);
  function place(f, instant) {   // walk (or, instant, jump) to where the shown state puts it
    const spot = spotFor(f);
    if (instant) { f.x = spot[0]; f.y = spot[1]; f.path = []; arrive(f); return; }
    f.path = tidy(routeTo(f, spot));
    if (f.path.length && f.path[0][0] === Math.round(f.x) && f.path[0][1] === Math.round(f.y)) f.path.shift();
    f.moving = f.path.length > 0;
    if (!f.moving) arrive(f);
  }
  function arrive(f) {
    if (f.leaving) { S.figs.delete(f.id); return; }   // pushed out by the cap: it reached the path and is gone
    if (!(f.want && f.want.back)) f.at = f.plot;   // (turned back on its way out: it is on the path, not at its spot)
    const P = PLACE[f.plot], door = PLOT[f.plot].door;
    f.face = f.x <= door[0] ? 1 : -1;
    if (f.out >= 0) f.face = f.x <= P.apron[0] ? 1 : -1;
    // done: the lantern goes on the hook and nobody stands there; unless the agent is at work again (it turns back)
    if (f.shown === 'ok' && !(f.want && f.want.state !== 'ok')) { S.figs.delete(f.id); return; }
    if (f.want) apply(f, false);
  }
  function apply(f, instant) {   // the newest state, once the figure stands still
    const w = f.want; f.want = null;
    // a new building: the state comes along, so it is one walk, straight to its spot there
    if (w.plot !== f.plot) { f.plot = w.plot; f.slot = f.seat = f.out = -1; f.shown = w.state; place(f, instant); return; }
    if (w.state === f.shown && !w.back) return;
    f.shown = w.state;
    // gone quiet: it stops where it is and keeps that spot (a row slot, or the out spot it stands on)
    if (f.shown === 'quiet' && f.seat < 0 && !w.back) { if (f.slot < 0 && f.out < 0) f.slot = free(f, 'slot'); return; }
    place(f, instant);
  }
  function reroute(f) {   // a new building while it walks: on from the next crossing to its spot there; the state still waits
    const k = f.path.findIndex(p => NODES.has(key(p)));
    if (k < 0) return;   // on a spur: it finishes this walk first
    const w = f.want, keep = f.shown, node = f.path[k];
    f.plot = w.plot; f.slot = f.seat = f.out = -1;
    f.shown = w.state;   // its spot is the wanted state's (a seat or slot taken now is kept for it)
    const spot = spotFor(f), P = PLACE[f.plot];
    f.shown = keep; f.at = null;
    f.path = tidy(f.path.slice(0, k + 1).concat(shortest(key(node), key(P.join)), chain(P.join, P.spur.concat([spot]))));
  }
  function leave(f, instant) {   // pushed out by the cap: it steps out onto its path and is gone there (no vanishing on the spot)
    if (f.leaving) return;
    f.leaving = true; f.want = null;
    if (instant) { S.figs.delete(f.id); return; }
    const P = PLACE[f.plot], here = [Math.round(f.x), Math.round(f.y)];
    if (f.path.length) { const k = f.path.findIndex(p => NODES.has(key(p))); if (k >= 0) f.path = f.path.slice(0, k + 1); }
    else f.path = tidy(chain(here, [P.apron].concat(P.spur.slice(0, -1).reverse(), [P.join])));
    if (f.path.length && f.path[0][0] === here[0] && f.path[0][1] === here[1]) f.path.shift();
    f.at = null; f.moving = f.path.length > 0;
    if (!f.moving) S.figs.delete(f.id);
  }
  function make(a, instant) {
    const f = { id: a.id, a, plot: plotKey(a.plot), shown: a.state, want: null, x: SQUARE[0], y: SQUARE[1], at: null,
      path: [], face: 1, walked: 0, slot: -1, seat: -1, out: -1, trail: [], leaving: false };
    const fp = S.followAt.get(a.id);   // a helper whose parent finished: it carries on from where it stood
    if (fp) { f.x = fp[0]; f.y = fp[1]; f.at = fp[2]; f.face = fp[3]; }
    S.figs.set(a.id, f);
    place(f, instant);
    return f;
  }

  /* a new payload: which agents get a figure, which state each one wants */
  function sync(p, still) {
    const agents = (p && Array.isArray(p.agents) ? p.agents : []).filter(a => a && typeof a === 'object' && typeof a.id === 'string' && STATES.has(a.state))
      .filter(a => a.tool !== 'ci');   // --- live: L11 ci_gh --- a CI run is no agent: it flies as its berth's pennant (berths.js)
    const by = new Map(agents.map(a => [a.id, a]));
    const instant = S.first || still;
    S.first = false;
    S.atMs = Date.parse(p && p.at) || Date.now(); S.recv = performance.now();
    // every helper (and a helper's helper) follows its top ancestor still at work; a parent cycle means no parent
    const rootOf = a => {
      const seen = new Set([a.id]);
      for (let r = a; ;) { const q = by.get(r.parent); if (!q || q.state === 'ok') return r.id; if (seen.has(q.id)) return a.id; seen.add(q.id); r = q; }
    };
    S.root = new Map(agents.map(a => [a.id, rootOf(a)]));
    // figures whose agent is now done keep walking to the hook; they count against the cap until they hang it
    for (const f of [...S.figs.values()]) {
      const a = by.get(f.id);
      if (a && a.state === 'ok') { f.a = a; f.leaving = false; if (f.shown !== 'ok') { f.want = { state: 'ok', plot: f.plot }; if (!f.path.length) apply(f, instant); } }
    }
    const homing = [...S.figs.values()].filter(f => by.get(f.id) && by.get(f.id).state === 'ok').length;
    const tops = agents.filter(a => a.state !== 'ok' && S.root.get(a.id) === a.id)
      .sort((x, y) => RANK[x.state] - RANK[y.state] || (S.figs.has(y.id) - S.figs.has(x.id)) || ageMs(y) - ageMs(x) || (x.id < y.id ? -1 : 1));
    // the ones waiting for you or failed always get a figure (walkers to the hook never keep them off the map); working and
    // quiet ones fill what is left of the cap, at most a building's own row each
    const chosen = new Set(tops.filter(urgent).slice(0, CAP).map(a => a.id)), room = {};
    let left = Math.max(0, CAP - homing - chosen.size);
    for (const a of tops) {
      if (left <= 0) break;
      if (urgent(a)) continue;
      const k = plotKey(a.plot);
      if ((room[k] || 0) >= roomAt(k)) continue;
      room[k] = (room[k] || 0) + 1; chosen.add(a.id); left--;
    }
    for (const f of [...S.figs.values()]) {
      const a = by.get(f.id);
      if (chosen.has(f.id) || (a && a.state === 'ok')) continue;
      if (a) leave(f, instant); else S.figs.delete(f.id);   // pushed out by the cap: it steps out; gone from the payload: gone
    }
    for (const a of tops) {
      if (!chosen.has(a.id)) continue;
      const f = S.figs.get(a.id);
      if (!f) { make(a, instant); continue; }
      f.a = a;
      const plot = plotKey(a.plot);
      if (f.leaving) {   // chosen again while it stepped out: it turns back
        f.leaving = false; f.want = { state: a.state, plot, back: true };
        if (!f.path.length) apply(f, instant); else if (plot !== f.plot && !instant) reroute(f);
      } else if (a.state !== f.shown || plot !== f.plot) {
        f.want = { state: a.state, plot };
        if (!f.path.length) apply(f, instant); else if (plot !== f.plot && !instant) reroute(f);
      } else f.want = null;
    }
    S.agents = agents; S.by = by; S.more = count(p && p.more);
    for (const [id] of S.followAt) if (!by.has(id)) S.followAt.delete(id);
  }

  function step(dt) {
    for (const f of [...S.figs.values()]) {
      if (!f.path.length) { f.moving = false; continue; }
      let budget = S.speed * dt;
      while (budget > 0 && f.path.length) {
        const [tx, ty] = f.path[0], dx = tx - f.x, dy = ty - f.y, d = Math.hypot(dx, dy);
        if (Math.abs(dx) > 0.01) f.face = dx > 0 ? 1 : -1;
        if (d <= budget) { f.x = tx; f.y = ty; budget -= d; f.walked += d; f.path.shift(); trail(f); }
        else { f.x += dx / d * budget; f.y += dy / d * budget; f.walked += budget; budget = 0; trail(f); }
      }
      f.moving = f.path.length > 0;
      if (!f.moving) arrive(f);   // the walk ended this frame: now a waiting change may happen
    }
  }
  function trail(f) {   // where it has been, newest first, one point per pixel walked (its helpers walk in its steps)
    const p = [Math.round(f.x), Math.round(f.y)], q = f.trail[0];
    if (!q || q[0] !== p[0] || q[1] !== p[1]) { f.trail.unshift(p); if (f.trail.length > 40) f.trail.length = 40; }
  }
  function settle() { for (let n = 0; n < 50; n++) { const f = [...S.figs.values()].find(o => o.path.length); if (!f) return;
    const e = f.path[f.path.length - 1]; f.x = e[0]; f.y = e[1]; f.path = []; f.moving = false; arrive(f); } }

  /* ---------- drawing ---------- */
  function painter(ox, top, f, wid) {
    const px = [];
    const m = (dx, dy, w, h, c) => px.push([f > 0 ? ox + dx : ox + wid - dx - w, top + dy, w, h, c]);
    m.ink = () => { for (const [x, y, w, h] of px) { R(x - 1, y, w + 2, h, OL); R(x, y - 1, w, h + 2, OL); } for (const [x, y, w, h, c] of px) R(x, y, w, h, c); };
    return m;
  }
  const lanternPx = (m, dx, dy, col) => { m(dx + 1, dy - 1, 1, 1, FRAME); m(dx, dy, 3, 1, FRAME); m(dx, dy + 1, 3, 2, col); m(dx + 1, dy + 3, 1, 1, FRAME); };
  const slumpOf = f => { const mins = ageMs(f.a) / 6e4; return mins < 10 ? 0 : mins < 30 ? 1 : 2; };
  function drawCart(f, fx, fy) {
    const files = count(f.a.files); if (files <= 0) return null;
    const cx = fx - f.face * 10;   // a low hand cart behind it: crates side by side, a third on top past 100 files
    R(cx - 4, fy + 1, 10, 1, 'rgba(0,0,0,0.35)');
    const k = files >= 100 ? 3 : files >= 10 ? 2 : 1, crates = [[cx - 4, fy - 6], [cx + 1, fy - 6], [cx - 2, fy - 9]].slice(0, k);
    if (k === 1) crates[0][0] = cx - 2;
    R(cx - 5, fy - 3, 12, 3, OL); R(cx - 4, fy - 2, 10, 1, '#8a6038'); R(cx - 3, fy - 1, 2, 2, '#1c1418'); R(cx + 3, fy - 1, 2, 2, '#1c1418');
    if (f.face > 0) R(cx + 6, fy - 4, Math.max(0, fx - 3 - (cx + 6)), 1, '#8a6038'); else R(fx + 4, fy - 4, Math.max(0, cx - 5 - (fx + 4)), 1, '#8a6038');
    for (const [x, y] of crates) R(x - 1, y - 1, 6, 5, OL);
    for (const [x, y] of crates) { R(x, y, 4, 3, '#b4783a'); R(x, y, 4, 1, '#d49a54'); P(x + 1, y + 1, '#7a4a20'); }
    return [cx, (k === 3 ? fy - 10 : fy - 7)];
  }
  function drawStanding(f) {
    const fx = Math.round(f.x), fy = Math.round(f.y), dir = f.face, ox = fx - 4, top = fy - 11, st = f.shown, dim = st === 'quiet';
    const cart = drawCart(f, fx, fy);
    const m = painter(ox, top, dir, 8), coat = dim ? DIMCOAT : coatOf(f.a.tool), skin = dim ? DIMSKIN : SKIN;
    const fr = f.moving ? Math.floor(f.walked / 3) % 2 : 0;
    R(fx - 3, fy + 1, 8, 1, 'rgba(0,0,0,0.4)');
    m(2, 0, 4, 1, HAT); m(1, 1, 6, 1, HAT); m(2, 2, 4, 2, skin); m(4, 2, 1, 1, EYE);
    m(2, 4, 4, 1, coat); m(1, 5, 6, 3, coat); m(1, 7, 6, 1, shade(coat, 0.7)); m(2, 8, 4, 1, coat);
    if (fr === 0) { m(2, 9, 1, 2, TROU); m(5, 9, 1, 2, TROU); m(1, 11, 2, 1, BOOT); m(5, 11, 2, 1, BOOT); } else { m(3, 9, 2, 2, TROU); m(3, 11, 3, 1, BOOT); }
    let lx, ly;
    if (st === 'error') {   // lantern lowered, pole slack: it has stopped
      for (const [dx, dy] of [[6, 6], [7, 5], [8, 4], [9, 3], [10, 2]]) m(dx, dy, 1, 1, POLE);
      lanternPx(m, 9, 3, GLASS.error); lx = 10; ly = 5;
    } else {
      for (const [dx, dy] of [[6, 6], [6, 5], [7, 4], [7, 3], [8, 2], [8, 1], [9, 0], [9, -1]]) m(dx, dy, 1, 1, POLE);
      m(6, 6, 1, 1, skin); lanternPx(m, 9, -1, GLASS[st] || GLASS.working); lx = 10; ly = 1;
    }
    m.ink();
    return { lamp: [dir > 0 ? ox + lx : ox + 8 - lx - 1, top + ly], box: [fx - 6, top - 3, 13, 15], head: [fx, top], cart };
  }
  function drawSeated(f) {
    const fx = Math.round(f.x), fy = Math.round(f.y), dir = f.face, ox = fx - 3, top = fy - 11, p = slumpOf(f), hd = p, hx = p ? 1 : 0;
    const m = painter(ox, top, dir, 7), coat = coatOf(f.a.tool);
    R(fx - 3, fy + 1, 8, 1, 'rgba(0,0,0,0.4)');
    if (f.seat === 0) { m(0, 0, 1, 11, WOOD); m(0, 0, 1, 1, shade(WOOD, 1.25)); }   // the Watcher's chair: a high back; other seats: stools
    m(0, 7, 6, 1, WOOD); m(1, 8, 1, 4, shade(WOOD, 0.7)); m(5, 8, 1, 4, shade(WOOD, 0.7));
    m(1 + hx, -2 + hd, 4, 1, HAT); m(0 + hx, -1 + hd, 6, 1, HAT); m(1 + hx, 0 + hd, 4, 2, SKIN);
    if (p < 2) m(3 + hx, 0 + hd, 1, 1, EYE); else m(3 + hx, 1 + hd, 2, 1, '#8a5a40');   // eyes shut, at the end
    m(1, 2 + hd, 4 + hx, 5 - hd, coat); m(1, 6, 4 + hx, 1, shade(coat, 0.7));
    m(1, 6, 5, 1, TROU); m(5, 7, 1, 4, TROU); m(5, 11, 2, 1, BOOT);
    if (p >= 1) m(5, 3 + hd, 1, 3, shade(coat, 0.85));   // arms hang
    // the lantern, set down on the step on the door side
    m(8, 6, 1, 1, FRAME); m(7, 7, 3, 1, FRAME); m(7, 8, 3, 2, GLASS.waiting); m(7, 10, 3, 1, FRAME);
    m.ink();
    return { lamp: [dir > 0 ? ox + 8 : ox - 2, top + 9], box: [fx - 5, top - 3, 13, 15], head: [fx + dir, top + hd - 2], slump: p };
  }
  function drawFollower(h, a, parent, i) {
    const fx = Math.round(h.x), fy = Math.round(h.y), dir = h.face, ox = fx - 2, top = fy - 7, st = a.state === 'ok' ? 'working' : a.state;
    const m = painter(ox, top, dir, 5), dim = st === 'quiet', coat = dim ? DIMCOAT : mixHex(coatOf(a.tool), '#ffffff', 0.18);
    const fr = parent.moving ? Math.floor((parent.walked + i * 2) / 2) % 2 : 0;
    R(fx - 2, fy + 1, 5, 1, 'rgba(0,0,0,0.35)');
    m(1, 0, 3, 1, HAT); m(1, 1, 3, 2, dim ? DIMSKIN : SKIN); m(3, 1, 1, 1, EYE); m(0, 3, 5, 3, coat);
    if (fr === 0) { m(1, 6, 1, 1, TROU); m(3, 6, 1, 1, TROU); m(1, 7, 1, 1, BOOT); m(3, 7, 1, 1, BOOT); } else { m(2, 6, 1, 1, TROU); m(2, 7, 2, 1, BOOT); }
    m(5, 3, 1, 1, FRAME); m(5, 4, 1, 2, GLASS[st] || GLASS.working);
    m.ink();
    return { lamp: [dir > 0 ? ox + 5 : ox - 1, top + 5], box: [fx - 3, top - 1, 8, 10], st };
  }
  function helperSpots(f, n) {   // its helpers walk in its steps; standing, they wait behind it
    const out = [], tr = f.trail;
    for (let i = 0; i < n; i++) {
      const back = 6 + 5 * i;
      if (f.seat >= 0) { out.push([Math.round(f.x) - f.face * (8 + 5 * i), Math.round(f.y) + (i % 2), -f.face]); continue; }
      const q = tr.length > back ? tr[back] : null;
      out.push(q ? [q[0], q[1] + (i % 2), f.moving ? f.face : (f.x >= q[0] ? 1 : -1)]
        : [Math.round(f.x) - f.face * back, Math.round(f.y) + (i % 2), f.face]);
    }
    return out;
  }
  // a count, short: 999, 1.2K, 12.3K, 123K, 1.2M ... 999M (rounded down: a count never reads bigger than it is)
  const fmt = n => { const u = n >= 1e6 ? [1e6, 'M'] : n >= 1000 ? [1000, 'K'] : null; if (!u) return String(n);
    const v = n / u[0]; return (v < 100 ? Math.floor(v * 10) / 10 : Math.floor(v)) + u[1]; };
  const many = (n, w) => fmt(n) + ' ' + w + (n === 1 ? '' : 'S');
  const toolName = t => String(t || 'agent').replace(/[-_]+/g, ' ').toUpperCase();
  const placeName = plot => (typeof nameOn === 'function' ? nameOn(plot) : String(plot).toUpperCase());
  function counts(a) {
    const c = [];
    const files = count(a.files), commits = count(a.commits);
    if (files) c.push(many(files, 'FILE'));
    if (commits) c.push(many(commits, 'COMMIT'));
    if (a.pushed) c.push('PUSHED');
    if (a.ci) c.push('CI ' + String(a.ci).toUpperCase());
    return c.join(', ');
  }
  function stateLine(a, shown, plot) {
    const mins = Math.floor(ageMs(a) / 6e4), where = placeName(plot);
    if (shown !== a.state && a.state !== 'ok') return 'WALKING TO THE ' + where;
    return { working: 'WORKING AT THE ' + where, waiting: 'WAITING FOR YOU: ' + mins + ' MIN', error: 'FAILED AT THE ' + where,
      quiet: 'GONE QUIET ' + mins + ' MIN AGO: NOT DONE', ok: 'DONE' }[a.state] || '';
  }
  function card(a, shown, plot, helpers) {   // helpers: the agent's helpers at work (a list)
    const who = a.project ? projectShown(a.project) + ' / ' + toolName(a.tool) : toolName(a.tool);   // W2-left: as the berths spell it
    const l = [String(a.title || 'AN AGENT'), who, stateLine(a, shown, plot)], c = counts(a);
    if (c) l.push(c);
    if (helpers && helpers.length) {
      const n = st => helpers.filter(h => h.state === st).length, w = n('waiting'), e = n('error');
      l.push(plural(helpers.length, 'HELPER') + (w ? ', ' + w + ' WAITING' : '') + (e ? ', ' + e + ' FAILED' : ''));
    }
    return l;
  }
  function tip(box, lines, id) { TIPS.unshift({ box, tip: lines }); S.hits.unshift({ box, lines, id }); }

  /* scene layer: the figures, back to front */
  function scene(t, still) {
    S.hits = []; S.marks = []; S.drawn = [];
    still = still || !running;   // no frame loop (reduced motion, a frozen ?t= scene): nobody can walk, so they are there
    const p = LIVE.payload && LIVE.on ? LIVE.payload : null;
    if (!p) { if (S.figs.size || S.last) { S.figs.clear(); S.first = true; S.last = null; S.agents = []; S.by = new Map(); S.more = 0; } }
    else if (p !== S.last) { sync(p, still); S.last = p; }   // (a sync that throws is tried again next frame)
    const now = performance.now(), dt = Math.min(0.25, Math.max(0, (now - (S.t || now)) / 1000)); S.t = now;
    if (still) settle(); else step(dt);
    const by = S.by || new Map(), agents = S.agents || [];
    // a figure's helpers: every agent at work below it, the most urgent first, then the oldest
    const kids = new Map();
    for (const a of agents) { const r = S.root.get(a.id); if (a.state !== 'ok' && r && r !== a.id) { if (!kids.has(r)) kids.set(r, []); kids.get(r).push(a); } }
    for (const l of kids.values()) l.sort((x, y) => RANK[x.state] - RANK[y.state] || ageMs(y) - ageMs(x) || (x.id < y.id ? -1 : 1));
    const ents = [], seen = [], followed = new Set(), onCard = new Set();
    for (const f of S.figs.values()) {
      // two walk with it; past two, the waiting, failed and quiet ones walk too (up to FOLLOW_MAX); working ones are on its card
      const subs = f.shown === 'ok' || f.leaving ? [] : kids.get(f.id) || [], hs = [];
      for (const a of subs) {
        if (hs.length < FOLLOW || (a.state !== 'working' && hs.length < FOLLOW_MAX)) hs.push(a);
        else if (a.state === 'working') onCard.add(a.id);
      }
      const spots = helperSpots(f, hs.length);
      hs.forEach((a, i) => { followed.add(a.id); const [x, y, face] = spots[i]; S.followAt.set(a.id, [x, y, f.at, face]);
        ents.push([y, () => { const d = drawFollower({ x, y, face }, a, f, i);
          S.marks.push({ lamp: d.lamp, st: d.st, small: true });
          tip(d.box, ['HELPER OF ' + String(f.a.title || 'AN AGENT').toUpperCase()].concat(card(a, a.state, f.plot, 0).slice(0, 3)), a.id);
          seen.push({ id: a.id, parent: f.id, small: true, state: a.state, shown: a.state, plot: f.plot, x, y, mode: f.moving ? 'walk' : 'stand' }); }]);
      });
      ents.push([f.y, () => {
        const sit = f.seat >= 0 && !f.moving && f.shown === 'waiting', d = sit ? drawSeated(f) : drawStanding(f);
        S.marks.push({ lamp: d.lamp, st: f.shown, head: d.head, quiet: f.shown === 'quiet', slump: sit ? d.slump : -1, cart: d.cart, files: count(f.a.files), id: f.id });
        tip(d.box, card(f.a, f.shown, f.plot, subs), f.id);
        if (d.cart) tip([d.cart[0] - 6, d.cart[1] - 1, 13, 15], card(f.a, f.shown, f.plot, subs), f.id);
        seen.push({ id: f.id, parent: null, small: false, state: f.shown, want: f.want ? f.want.state : null, plot: f.plot, x: Math.round(f.x), y: Math.round(f.y),
          mode: f.moving ? 'walk' : sit ? 'sit' : 'stand', seat: f.seat, chair: sit && f.seat === 0, slump: sit ? d.slump : -1, lantern: f.shown === 'quiet' ? 'dark' : f.shown,
          mark: f.shown === 'quiet' ? '?' : null, helpers: hs.map(a => a.id), files: count(f.a.files), tool: f.a.tool, coat: coatOf(f.a.tool),
          leaving: f.leaving });
      }]);
    }
    ents.sort((a, b) => a[0] - b[0]);
    for (const [, fn] of ents) fn();
    // done: lanterns on the hooks (every done agent of the payload, its figure gone), for DONE_MS
    S.hooks = {};
    for (const a of agents) if (a.state === 'ok' && !S.figs.has(a.id) && ageMs(a) < DONE_MS) { const k = plotKey(a.plot); (S.hooks[k] = S.hooks[k] || []).push(a); }
    // not drawn: one "+N" per building (a figure's working helpers past the ones that walk are on its card instead); the
    // payload's own `more` (agents the Watcher did not list) rides on the busiest building's tag
    S.hidden = agents.filter(a => a.state !== 'ok' && !S.figs.has(a.id) && !followed.has(a.id) && !onCard.has(a.id));
    const at = {};
    for (const a of S.hidden) { const k = plotKey(a.plot); at[k] = (at[k] || 0) + 1; }
    S.extraAt = null;
    if (S.more) {
      let b = null; for (const k of TOWN_ORDER) if (at[k] && (!b || at[k] > at[b])) b = k;
      b = b || 'boathouse'; at[b] = (at[b] || 0) + S.more; S.extraAt = b;
    }
    S.moreAt = at;
    S.drawn = seen;
    SEEN.agents = seen;
    SEEN.agentHooks = Object.fromEntries(Object.entries(S.hooks).map(([k, v]) => [k, v.map(a => a.id)]));
    SEEN.agentMore = S.hidden.length + (S.more || 0);
    SEEN.agentMoreAt = Object.assign({}, at);
    if (S.pin && !seen.some(e => e.id === S.pin)) S.pin = null;
  }

  /* lights layer: lantern glows, the quiet "?", the slumped "Z", cart tags, hooks, the "+N" tag and a pinned card */
  function lights() {
    const marks = S.marks || [];
    for (const k of marks) {
      const c = GLOW[k.st]; if (!c || k.quiet) continue;
      glow(glowSp(c, k.small ? 3 : k.st === 'waiting' ? 7 : 6, k.small ? 3 : k.st === 'waiting' ? 6 : 5, k.st === 'working' ? 0.32 : 0.42), k.lamp[0], k.lamp[1]);
    }
    const tags = [];
    for (const k of marks) {
      if (k.quiet && k.head) { const [x, y] = k.head; R(x - 3, y - 10, 7, 9, OL); R(x - 2, y - 9, 5, 7, '#1d2030'); text('?', x - 1, y - 8, '#8a8fa8'); }
      if (k.slump === 2 && k.head) { const [x, y] = k.head; text('Z', x + 3, y - 6, '#9fb6ff'); P(x + 7, y - 8, '#9fb6ff'); }
      if (k.cart && k.files > 0) {
        const s = fmt(k.files), w = textW(s) + 4; let x = Math.round(k.cart[0] - w / 2), y = k.cart[1] - 8;
        x = Math.max(1, Math.min(W - w - 1, x));
        for (let n = 0; n < 6 && tags.some(b => x < b[0] + b[2] + 1 && b[0] < x + w + 1 && y < b[1] + b[3] + 1 && b[1] < y + 8); n++) y -= 8;
        tags.push([x, y, w, 7]);
        R(x - 1, y - 1, w + 2, 9, OL); R(x, y, w, 7, '#2a1a24'); text(s, x + 2, y + 1, '#ffd59a');
      }
    }
    for (const [plot, list] of Object.entries(S.hooks || {})) {
      const [hx, hy] = PLACE[plot].hook, n = Math.min(3, list.length);
      R(hx - 1, hy - 1, 3 + 4 * n, 3, OL); R(hx, hy, 1 + 4 * n, 1, WOOD); R(hx, hy - 0, 1, 2, shade(WOOD, 0.7));
      for (let i = 0; i < n; i++) { const x = hx + 2 + i * 4;
        R(x - 1, hy + 1, 4, 6, OL); P(x + 0, hy + 1, FRAME); R(x - 0, hy + 2, 2, 3, GLASS.ok); R(x, hy + 5, 2, 1, FRAME);
        glow(glowSp(GLOW.ok, 3, 3, 0.3), x + 1, hy + 3); }
      if (list.length > 3) text('+' + (list.length - 3), hx + 2 + 4 * n, hy + 2, '#9fe8c0');
      tip([hx - 1, hy - 1, 6 + 4 * n, 9], [plural(list.length, 'AGENT') + ' DONE HERE: LANTERNS ON THE HOOK'].concat(list.slice(0, 4).map(a => String(a.title || 'AN AGENT'))),
        null);
    }
    for (const k of TOWN_ORDER) { const n = (S.moreAt || {})[k]; if (n > 0) moreAt(k, n); }
  }
  /* a building's "+N": a small dark tag with a grey head-and-shoulders mark (people, not the carts' file crates), standing at
     the end of its row where the next figure would stand. It changes only when the payload does. */
  function moreAt(plot, n) {
    const [sx, sy] = PLACE[plot].more, s = '+' + fmt(n), w = textW(s) + 9;
    const x = Math.max(1, Math.min(W - w - 1, Math.round(sx - w / 2))), y = sy - 8;
    R(sx - 3, sy + 1, 7, 1, 'rgba(0,0,0,0.35)');   // a footprint: one more would stand here
    R(x - 1, y - 1, w + 2, 9, OL); R(x, y, w, 7, '#2a1a24');
    R(x + 3, y + 1, 2, 2, '#7a7f99'); R(x + 2, y + 4, 4, 2, '#7a7f99');   // the mark: an unlit figure
    text(s, x + 7, y + 1, '#ffd59a');
    const extra = S.extraAt === plot ? S.more : 0, here = n - extra;
    const row = roomAt(plot), lines = [plural(n, 'MORE LAMPLIGHTER') + ' AT THE ' + placeName(plot),
      'NOT DRAWN: AT MOST ' + CAP + ' WALK THE TOWN AT ONCE' + (row < Infinity ? ', ' + row + ' WORK AT ONE BUILDING' : '')];
    if (extra) lines.push(here ? here + ' HERE, ' + plural(extra, 'MORE AGENT') + ' THE WATCHER DID NOT LIST' : plural(extra, 'AGENT') + ' THE WATCHER DID NOT LIST');
    TIPS.unshift({ box: [x - 1, y - 1, w + 2, 11], tip: lines });
  }

  /* top layer (over everything the town draws, under the hover tip): a pinned card */
  function top() {
    SEEN.agentCard = null;
    if (S.pin) { const h = S.hits.find(x => x.id === S.pin); if (h) { drawTip(h.lines, h.box[0] + 6, h.box[1]); SEEN.agentCard = h.lines; } }
  }

  /* hover is the town's own tooltip; a click (or a tap) on a figure pins its card, a click anywhere else lets it go */
  const hitAt = (x, y) => S.hits.find(h => h.id && x >= h.box[0] && x < h.box[0] + h.box[2] && y >= h.box[1] && y < h.box[1] + h.box[3]) || null;
  stage.addEventListener('click', ev => {
    if (RS.id || !panel.hidden) return;
    const [x, y] = toLogical(ev), h = hitAt(x, y);
    if (h) { ev.stopPropagation(); ev.preventDefault(); S.pin = S.pin === h.id ? null : h.id; kick(); }
    else if (S.pin) { S.pin = null; kick(); }
  }, true);
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && S.pin) { S.pin = null; kick(); } });

  // --- L10-fix: berth move --- for web/berths.js: the painter and coats (the Harbour room's quay draws the same figures),
  // and respot(ids, instant): figures at work at the Harbour whose spot moved (their project moved up from the overflow
  // mooring to a freed berth) walk there along the shore behind the wharf (instant: a still scene, they are just there)
  api.coatOf = coatOf;
  api.paintFigure = (fx, fy, a, face) => {
    const st = STATES.has(a && a.state) && a.state !== 'ok' ? a.state : 'working';
    const d = drawStanding({ x: fx, y: fy, face: face > 0 ? 1 : -1, shown: st, a: Object.assign({}, a, { files: 0 }), moving: false, walked: 0 });
    return GLOW[st] && st !== 'quiet' ? { x: d.lamp[0], y: d.lamp[1], col: GLOW[st] } : null;
  };
  api.respot = (ids, instant) => {
    let n = 0;
    for (const id of ids || []) {
      const f = S.figs.get(id);
      if (!f || f.leaving || f.plot !== 'boathouse' || f.slot < 0 || !(f.shown === 'working' || f.shown === 'quiet')) continue;
      const spot = spotFor(f), here = [Math.round(f.x), Math.round(f.y)];
      n++;
      if (instant) { f.x = spot[0]; f.y = spot[1]; f.path = []; f.moving = false; arrive(f); continue; }
      // walking there already: its walk ends at the new spot instead; standing: up to the shore, along it, down to the spot
      const keep = f.path.slice(0, -1), from = keep.length ? keep[keep.length - 1] : here, SH = 194;
      f.path = tidy(keep.concat([[from[0], SH], [spot[0], SH], spot]));
      if (f.path.length && f.path[0][0] === here[0] && f.path[0][1] === here[1]) f.path.shift();
      f.moving = f.path.length > 0;
      if (!f.moving) arrive(f);
    }
    if (n) kick();
    return n;
  };
  // --- end L10-fix ---
  function guard(fn) { return (...a) => { try { fn(...a); } catch (e) { if (!S.err) { S.err = String(e && e.stack || e); console.error(e); } SEEN.agentsError = S.err; } }; }
  TOWN_LAYERS.scene.push(guard(scene)); TOWN_LAYERS.lights.push(guard(lights));
  (TOWN_LAYERS.top = TOWN_LAYERS.top || []).push(guard(top));   // town.js draws it after its corner box (L7-fix)
  kick();   // town.js drew its first frame before this script ran: a still scene draws again now, with the layers
  return api;
})();
