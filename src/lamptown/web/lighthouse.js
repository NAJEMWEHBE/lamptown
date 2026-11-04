'use strict';
/* LAMPTOWN: the lighthouse in the bay (the live town's own place; its room, the keeper's book, is web/rooms/lighthouse.js).
   Inlined by `lamptown build` after town.js; it draws through town.js's TOWN_LAYERS hooks, and only when the town data has
   parts.lighthouse ([live] on). It follows the live payload (LIVE.payload, while it is fresh):
     nobody working          dim fixed light, nothing moves
     an agent working        the beam sweeps slowly over the water
     an agent waiting        a NEW wait flashes the lantern (and rings the bell once), then it settles to a steady amber;
                             the beam holds on the landing by the slip, and from there its light runs up the town's real
                             footpaths (the ones the lamplighters walk) to the door that needs you, ending in a pool of light.
                             Several doors: each gets its lit way; the newest the bright one, the others dimmer (nothing
                             swings between them). The sign carries the count ("2 AGENTS WAIT").
     an agent failed         the same in red (a new failure flashes, no bell)
   No toasts. What the last frame drew is in SEEN.lighthouse (tests). */
const LH = (() => {
  const PLACE = 'lighthouse';
  const CX = 328, LY = 172;             // the tower's axis and the lantern
  const OX = CX, OY = 201;              // the beam's origin on the water plane: the tower's foot
  const HIT = [CX - 15, 160, 32, 47];   // the click box: tower + rock (clear of the plots and the corner's box and link)
  const BOX = [CX - 17, 160, 35, 48];   // everything the tower and its rock draw (the corner box below must stay clear)
  // timing: flash (ms) after a new wait or failure, flashOn (ms) per on/off; slew (rad/s); sweep: the pan's pace;
  // reach (s): a lit way grows from the shore to the door; fresh (ms): on the first payload a page sees, a wait younger than
  // this still counts as new. Tests may shorten them (LH.TUNE).
  const TUNE = { flash: 3000, flashOn: 300, slew: 2.4, sweep: 0.35, reach: 0.7, fresh: 15000 };
  // the ground just in front of each plot's door: where its lit way ends in a pool of light
  const STEP = { tower: [60, 77], library: [200, 74], gate: [348, 74], lab: [87, 145], workshop: [307, 147], boathouse: [204, 189] };
  const COL = { sweep: '#fff0c8', wait: '#ffb020', fail: '#ff4a4a' };
  const DAY = { sweep: '#fff8e0', wait: '#c86a08', fail: '#b8282c' };   // by day: darker, laid over the bright ground
  const BM = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];   // 4x4 ordered dither
  const dith = (x, y) => BM[(y & 3) * 4 + (x & 3)] / 16;
  const adiff = (a, b) => { let d = (a - b) % (2 * Math.PI); if (d > Math.PI) d -= 2 * Math.PI; if (d < -Math.PI) d += 2 * Math.PI; return d; };

  /* ---------- the way the light walks: it comes ashore by the slip (the boathouse's spur), then follows the town's
     footpaths (ROUTES in town.js) and each plot's spur to its door: the same roads the lamplighters walk ---------- */
  const LAND = [248, shoreY(248)];      // where the held beam comes ashore
  const AIM = Math.atan2(LAND[1] + 2 - OY, LAND[0] - OX);   // the held beam: just off the landing, whichever door it lights
  const LAND_D = Math.hypot(LAND[0] - OX, LAND[1] + 2 - OY);
  const angTo = () => AIM;
  // what the light never crosses: every plot's building and its name plate (at its widest)
  const BLOCK = ORDER.flatMap(p => { const f = PLOT[p].foot, [cx, y, mw] = PLOT[p].plate; return [f, [cx - (mw >> 1) - 1, y - 1, mw + 2, 11]]; });
  const blocked = (x, y) => BLOCK.some(([bx, by, bw, bh]) => x >= bx && x < bx + bw && y >= by && y < by + bh);
  const key = p => p[0] + ',' + p[1];
  let G = null;
  function graph() {   // ROUTES as a graph: each point a node, a segment split wherever another node lies on it
    if (G) return G;
    const N = new Map(), A = new Map(), segs = [], add = p => { if (!N.has(key(p))) N.set(key(p), p.slice()); };
    for (const r of ROUTES) { r.pts.forEach(add); for (let i = 1; i < r.pts.length; i++) segs.push([r.pts[i - 1], r.pts[i]]); }
    for (const p of ORDER) add(way(p).join);
    const onSeg = (p, a, b) => (b[0] - a[0]) * (p[1] - a[1]) === (b[1] - a[1]) * (p[0] - a[0])
      && p[0] >= Math.min(a[0], b[0]) && p[0] <= Math.max(a[0], b[0]) && p[1] >= Math.min(a[1], b[1]) && p[1] <= Math.max(a[1], b[1]);
    const link = (a, b) => { for (const [u, v] of [[a, b], [b, a]]) { if (!A.has(key(u))) A.set(key(u), []); A.get(key(u)).push([key(v), Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1])]); } };
    for (const [a, b] of segs) {
      const on = [...N.values()].filter(p => onSeg(p, a, b)).sort((p, q) => Math.abs(p[0] - a[0]) + Math.abs(p[1] - a[1]) - Math.abs(q[0] - a[0]) - Math.abs(q[1] - a[1]));
      for (let i = 1; i < on.length; i++) link(on[i - 1], on[i]);
    }
    return (G = { N, A });
  }
  function shortest(from, to) {   // node keys -> the points after `from`, up to and with `to` (Dijkstra; a couple of dozen nodes)
    const { N, A } = graph(), dist = new Map([[from, 0]]), prev = new Map(), done = new Set();
    for (;;) {
      let u = null, best = Infinity;
      for (const [k, d] of dist) if (!done.has(k) && d < best) { best = d; u = k; }
      if (u === null || u === to) break;
      done.add(u);
      for (const [v, l] of A.get(u) || []) if (!dist.has(v) || dist.get(v) > best + l) { dist.set(v, best + l); prev.set(v, u); }
    }
    if (!dist.has(to)) return [N.get(to)];
    const out = []; for (let u = to; u !== from; u = prev.get(u)) out.unshift(N.get(u));
    return out;
  }
  // a plot's join (where its spur leaves the footpaths) and spur: the lamplighters' own (live.js), read when first needed;
  // without them, the footpath point nearest the door
  function way(plot) {
    let P = null;
    try { P = typeof LAMPLIGHTERS === 'object' && LAMPLIGHTERS && LAMPLIGHTERS.PLACE && LAMPLIGHTERS.PLACE[plot]; } catch (e) { P = null; }
    const ok = p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite);
    if (P && ok(P.join) && Array.isArray(P.spur) && P.spur.every(ok)) return { join: P.join, spur: P.spur, apron: P.spur.length ? P.spur[P.spur.length - 1] : P.join };
    const [sx, sy] = STEP[plot]; let b = null, bd = Infinity;
    for (const r of ROUTES) for (const p of r.pts) { const d = Math.abs(p[0] - sx) + Math.abs(p[1] - sy); if (d < bd) { bd = d; b = p; } }
    return { join: b, spur: [], apron: b };
  }
  const hop = (a, b) => (a[0] === b[0] || a[1] === b[1] ? [b] : [[b[0], a[1]], b]);   // across, then up or down: no diagonals
  const chain = (from, pts) => { const out = []; let c = from; for (const p of pts) { out.push(...hop(c, p)); c = p; } return out; };
  function routeOf(plot) {   // the corner points of the lit way, from the landing to the door
    const B = way('boathouse'), T = way(plot);
    if (plot === 'boathouse') return [LAND].concat(chain(LAND, [B.apron, STEP[plot]]));
    const up = [B.apron].concat(B.spur.slice(0, -1).reverse(), [B.join]);   // the boathouse's spur, walked back from the slip
    let pts = [LAND].concat(chain(LAND, up));
    pts = pts.concat(shortest(key(B.join), key(T.join)));
    pts = pts.concat(chain(pts[pts.length - 1], T.spur.concat([STEP[plot]])));
    return pts.filter((p, i, a) => i === 0 || p[0] !== a[i - 1][0] || p[1] !== a[i - 1][1]);
  }
  // the lit way's pixels, once per plot: [x, y, u (0 at the shore .. 1 at the door), edge (0 the dense middle, 1 a side)]
  const PATHS = {};
  function pathOf(plot) {
    if (PATHS[plot]) return PATHS[plot];
    const pts = routeOf(plot), seen = new Set(), pix = [];
    let total = 0; for (let i = 1; i < pts.length; i++) total += Math.max(Math.abs(pts[i][0] - pts[i - 1][0]), Math.abs(pts[i][1] - pts[i - 1][1]));
    let s = 0;
    for (let i = 1; i < pts.length; i++) {
      const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], n = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0)), across = Math.abs(x1 - x0) >= Math.abs(y1 - y0);
      for (let k = 0; k <= n; k++) {
        const cx = Math.round(x0 + (x1 - x0) * k / n), cy = Math.round(y0 + (y1 - y0) * k / n);
        for (let w = -1; w <= 1; w++) {   // three pixels wide: a dense middle, dithered sides
          const x = across ? cx : cx + w, y = across ? cy + w : cy, kk = y * W + x;
          if (x < 0 || y < 0 || x >= W || y >= H || seen.has(kk) || y > shoreY(x) || blocked(x, y)) continue;
          seen.add(kk); pix.push([x, y, total ? (s + k) / total : 1, w ? 1 : 0]);
        }
      }
      s += n;
    }
    return (PATHS[plot] = pix);
  }

  // the water's pixels, once (the shore never moves): position, angle and distance from the tower's foot
  let WP = null;
  function waterPix() {
    if (WP) return WP;
    const X = [], Y = [], A = [], Dd = [];
    for (let x = 0; x < W; x++) for (let y = shoreY(x) + 1; y < H; y++) { X.push(x); Y.push(y); A.push(Math.atan2(y - OY, x - OX)); Dd.push(Math.hypot(x - OX, y - OY)); }
    return (WP = { X: Int16Array.from(X), Y: Int16Array.from(Y), A: Float32Array.from(A), D: Float32Array.from(Dd), n: X.length });
  }

  /* ---------- state: what the live payload says, and what changed since the last look. Doors are tracked by plot, never
     by their place in the list: the same waits in another order, or another door answered, move nothing ---------- */
  const S = { payload: undefined, mode: 'off', targets: [], waits: [], fails: [], working: 0, known: null, knownFail: null,
    flashAt: -1e9, flashCol: 'wait', bells: 0, rang: 0, ang: -Math.PI / 2, on: null, phase: 'off', lastT: null, reach: new Map(),
    prefer: null, lit: [] };
  const shown = () => !!partOn(PLACE);
  const sinceOf = a => { const t = Date.parse(a && a.since); return isNaN(t) ? Infinity : t; };
  const target = () => S.targets.find(x => x.plot === S.on) || null;
  function read(now) {   // the payload -> waits, failures, working; new waits flash and ring
    const p = LIVE.on ? LIVE.payload : null;   // a stale payload (the Watcher stopped) is no news: dim light
    if (p === S.payload) return;
    S.payload = p;
    // no payload yet (or a stale one): forget what was known, so the next fresh payload judges its waits by their age
    if (!p) { S.known = S.knownFail = null; S.waits = []; S.fails = []; S.targets = []; S.working = 0; S.mode = 'idle'; S.on = null; S.reach.clear(); return; }
    const first = S.known === null;
    const agents = Array.isArray(p.agents) ? p.agents.filter(a => a && typeof a === 'object') : [];
    const pick = st => agents.filter(a => a.state === st && typeof a.plot === 'string' && STEP[a.plot]);
    S.waits = pick('waiting'); S.fails = pick('error'); S.working = agents.filter(a => a.state === 'working').length;
    // one door per building, in a steady order: waits before failures, then the longest waiting, then the town's plot order
    const doors = (list, kind) => { const m = new Map(); for (const a of list) { if (!m.has(a.plot)) m.set(a.plot, []); m.get(a.plot).push(a); }
      return [...m].map(([plot, as]) => ({ plot, kind, agents: as.sort((a, b) => sinceOf(a) - sinceOf(b) || String(a.id).localeCompare(String(b.id))),
        since: Math.min(...as.map(sinceOf)) })).sort((a, b) => (a.since - b.since) || ORDER.indexOf(a.plot) - ORDER.indexOf(b.plot)); };
    const wd = doors(S.waits, 'wait'), wset = new Set(wd.map(x => x.plot));
    S.targets = wd.concat(doors(S.fails.filter(a => !wset.has(a.plot)), 'fail'));
    for (const plot of [...S.reach.keys()]) if (!S.targets.some(x => x.plot === plot)) S.reach.delete(plot);   // a door let go
    const ids = list => new Set(list.map(a => String(a.id)));
    const young = a => Date.now() - sinceOf(a) < TUNE.fresh;
    const fresh = (list, known) => list.filter(a => (known ? !known.has(String(a.id)) : young(a)));
    const newWaits = fresh(S.waits, first ? null : S.known), newFails = fresh(S.fails, first ? null : S.knownFail);
    S.known = ids(S.waits); S.knownFail = ids(S.fails);
    const newest = list => list.reduce((b, a) => (sinceOf(a) >= sinceOf(b) ? a : b)).plot;
    if (newWaits.length || newFails.length) {
      S.flashAt = now; S.flashCol = newWaits.length ? 'wait' : 'fail';
      S.prefer = newWaits.length ? newest(newWaits) : newest(newFails);   // the bright way goes to the newest
    }
    if (newWaits.length) { S.bells++; try { if (typeof SFX.bell === 'function' && SFX.bell()) S.rang++; } catch (e) { /* no sound: fine */ } }
    // the bright way: a waiting door while any waits (the sign counts those), else a failed one; it stays on its door
    // until that door is let go or a newer one comes
    const pool = S.targets.filter(x => x.kind === (S.waits.length ? 'wait' : 'fail'));
    if (S.prefer !== null && pool.some(x => x.plot === S.prefer)) S.on = S.prefer;
    else if (!pool.some(x => x.plot === S.on)) S.on = pool.length ? pool[0].plot : null;
    S.prefer = null;
    S.mode = S.targets.length ? (S.waits.length ? 'wait' : 'fail') : S.working ? 'sweep' : 'idle';
  }
  function step(t, still) {   // the beam: sweep, or slew to the landing and hold there while the ways grow
    still = still || !running;   // a still scene (reduced motion, ?t=) never advances: show every door's way, settled
    const dt = S.lastT === null ? 0 : Math.max(0, Math.min(0.25, t - S.lastT)); S.lastT = t;
    if (S.targets.length) {
      if (still) { S.ang = AIM; S.phase = 'hold'; for (const x of S.targets) S.reach.set(x.plot, 1); return; }
      if (S.phase !== 'hold') {
        const d = adiff(AIM, S.ang), st = TUNE.slew * dt;
        if (Math.abs(d) <= st) { S.ang = AIM; S.phase = 'hold'; } else S.ang += Math.sign(d) * st;
      } else {
        S.ang = AIM;
        for (const x of S.targets) S.reach.set(x.plot, Math.min(1, (S.reach.get(x.plot) || 0) + dt / TUNE.reach));
      }
    } else if (S.mode === 'sweep') {   // pans slowly over the long bay to the left, out to sea and back (never over the land)
      if (S.phase !== 'sweep') { S.phase = 'sweep'; S.sweepT = 0; }
      S.sweepT += dt; S.ang = still ? 3.0 : 2.97 + 0.3 * Math.sin(S.sweepT * TUNE.sweep);
    }
    else { S.phase = 'off'; }
  }
  const flashing = now => now - S.flashAt < TUNE.flash;
  function lantern(now, t, still) {   // the lantern's colour and glow now
    if (S.mode === 'idle' || S.mode === 'off') return { c: '#8a7444', glow: 0.25, r: 'dim' };
    if (S.mode === 'sweep') return { c: '#fff0c8', glow: 0.8, r: 'white' };
    const tg = target(), col = tg ? tg.kind : S.waits.length ? 'wait' : 'fail';
    if (!still && running && flashing(now)) {
      const on = Math.floor((now - S.flashAt) / TUNE.flashOn) % 2 === 0, fc = S.flashCol;
      return on ? { c: fc === 'wait' ? '#fff0c0' : '#ffd0d0', glow: 1.6, r: fc, flash: true } : { c: '#3a2a10', glow: 0, r: fc, flash: true };
    }
    const breathe = still || !running ? 1 : 0.85 + 0.15 * Math.sin(t * 1.05);   // settled: steady, a slow breath (no blinking)
    return { c: COL[col], glow: breathe, r: col };
  }

  /* ---------- drawing ---------- */
  const tint = (kind, sky) => (sky.phase === 'day' ? DAY : COL)[kind];
  const ways = () => { const tg = target(); return tg ? [tg].concat(S.targets.filter(x => x !== tg)) : S.targets.slice(); };   // the bright one first
  function sea(t, still, sky) {   // the beam on the water, and the lit ways over the land (under the buildings)
    if (!shown()) return;
    place();
    const now = performance.now();
    read(now); step(t, still);
    if (S.phase === 'off') return;
    const day = sky.phase === 'day', k = sky.phase === 'night' ? 1 : 0.75, hold = S.phase === 'hold', hw = hold ? 0.075 : 0.11;
    const wp = waterPix(), lv = day ? [0, 0.34, 0.54, 0.78] : [0, 0.22 * k, 0.38 * k, 0.58 * k], buckets = [[], [], [], []], far = hold ? LAND_D + 3 : Infinity;
    for (let i = 0; i < wp.n; i++) {   // the held beam ends at the landing (it runs along the shore: never on past it)
      const df = Math.abs(adiff(wp.A[i], S.ang)); if (df >= hw || wp.D[i] > far) continue;
      const I = (1 - df / hw) * Math.max(0.25, 1.1 - wp.D[i] / 260), l = Math.min(3, Math.floor(I * 3 + dith(wp.X[i], wp.Y[i])));
      if (l > 0) buckets[l].push(i);
    }
    const tg = target();
    g.fillStyle = COL[S.mode === 'sweep' ? 'sweep' : tg ? tg.kind : 'wait'];   // on the water a bright glint, by day too
    for (let l = 1; l <= 3; l++) { g.globalAlpha = lv[l]; for (const i of buckets[l]) g.fillRect(wp.X[i], wp.Y[i], 1, 1); }
    g.globalAlpha = 1;
    if (!hold || !S.targets.length) return;
    // each door's way, growing from the shore: the bright one first, the others dimmer and never over it
    const done = new Set();
    for (const x of ways()) {
      const bright = x === tg, reach = S.reach.get(x.plot) || 0;
      g.fillStyle = tint(x.kind, sky); g.globalAlpha = day ? (bright ? 0.6 : 0.32) : (bright ? 0.32 : 0.16);
      for (const [px, py, u, edge] of pathOf(x.plot)) {
        if (u > reach) continue;
        const kk = py * W + px; if (done.has(kk)) continue;
        if (dith(px, py) < (edge ? (day ? 0.6 : 0.45) : (day ? 1 : 0.8))) { done.add(kk); g.fillRect(px, py, 1, 1); }
      }
    }
    g.globalAlpha = 1;
  }
  function pools(sky) {   // the pool of light at each lit door (on top of the doorstep, under people; never on a building or sign)
    const day = sky.phase === 'day'; S.lit = [];
    if (S.phase !== 'hold') return;
    const tg = target();
    g.globalCompositeOperation = day ? 'source-over' : 'lighter';
    for (const x of ways()) {
      if ((S.reach.get(x.plot) || 0) < 1) continue;
      const [px, py] = STEP[x.plot], f = x === tg ? 1 : 0.5;
      g.fillStyle = tint(x.kind, sky);
      for (let j = -3; j <= 3; j++) for (let i = -7; i <= 7; i++) {
        const e = i * i / 49 + j * j / 9, X = px + i, Y = py + j; if (e > 1 || blocked(X, Y)) continue;
        if (dith(X, Y) < (e < 0.45 ? 0.75 : 0.45)) { g.globalAlpha = (e < 0.45 ? (day ? 0.5 : 0.32) : (day ? 0.32 : 0.2)) * f; g.fillRect(X, Y, 1, 1); }
      }
      S.lit.push(x.plot);
    }
    g.globalAlpha = 1; g.globalCompositeOperation = 'source-over';
  }
  function tower(t, still, sky) {   // the rock, the tower, the gallery and the lantern room (graded with the scene)
    if (!shown()) return;
    const now = performance.now(), L = lantern(now, t, still);
    pools(sky);
    // the rock, with the sea foaming at its foot (all above the corner box at the bottom right)
    ellipse(CX, 202, 16, 4, OL); ellipse(CX, 202, 15, 3, '#2e3150'); ellipse(CX - 2, 201, 11, 2, '#3a3e5c'); ellipse(CX - 4, 200, 6, 1, '#4c5276');
    P(CX + 9, 199, '#4c5276'); P(CX - 12, 202, '#4c5276'); R(CX + 6, 204, 5, 1, '#24273d');
    for (let x = CX - 15; x <= CX + 15; x++) if ((x * 7 + Math.floor((still ? 0 : t) * 2.5)) % 5 === 0) P(x, 205 + (x & 1), '#9fd0f0');
    // the tower: white with two red bands, lit from the left like the rest of the town
    const top = 178, bot = 199;
    for (let y = top; y <= bot; y++) {
      const w = 8 + Math.round((y - top) / (bot - top) * 2), x0 = CX - (w >> 1), band = (y >= 182 && y <= 185) || (y >= 190 && y <= 193);
      R(x0 - 1, y, w + 2, 1, OL);
      R(x0, y, w, 1, band ? '#8e3a48' : '#c9cfe0'); R(x0 + w - 3, y, 3, 1, band ? '#73303c' : '#9aa0b8'); P(x0, y, band ? '#ad4c5a' : '#e8ecf8');
    }
    R(CX - 1, 194, 3, 6, OL); R(CX, 195, 1, 5, '#5e3b2a');   // door
    R(CX + 1, 187, 1, 2, LIT ? '#ffcf6b' : '#3a3e5c');       // window
    // gallery and rail
    R(CX - 7, 176, 15, 3, OL); R(CX - 6, 177, 13, 1, '#4c5276'); R(CX - 6, 176, 13, 1, '#6a7098');
    for (let x = CX - 6; x <= CX + 6; x += 2) P(x, 175, '#20233a');
    // lantern room: the light itself, mullions, cap and finial
    R(CX - 4, 168, 9, 8, OL); R(CX - 3, 169, 7, 6, L.c); R(CX, 169, 1, 6, '#20233a'); P(CX - 3, 169, L.r === 'dim' ? '#a89060' : '#ffffff');
    for (let j = 0; j < 5; j++) { const hw = 4 - j; R(CX - hw - 1, 167 - j, hw * 2 + 3, 1, OL); R(CX - hw, 167 - j, hw * 2 + 1, 1, j ? '#8e3a48' : '#73303c'); }
    P(CX - 1, 165, '#ad4c5a'); R(CX, 161, 1, 2, '#c9cfe0');
    S.lantern = L;
  }
  const waitWords = n => (n === 1 ? '1 AGENT WAITS' : n + ' AGENTS WAIT');
  const failWords = n => (n === 1 ? '1 AGENT FAILED' : n + ' AGENTS FAILED');
  function lights(t, still, sky) {   // the lantern's glow, the count sign and the tooltips (not graded)
    if (!shown()) { SEEN.lighthouse = { shown: false }; return; }
    const L = S.lantern || lantern(performance.now(), t, still);
    if (L.glow > 0) {
      const gc = L.r === 'wait' ? 'amber' : L.r === 'fail' ? 'red' : null;
      if (gc) glow(GL[gc], CX, LY, Math.min(1, L.glow));
      else glow(glowSp(L.r === 'dim' ? '#ffb45a' : '#fff0c8', L.r === 'dim' ? 5 : 9, L.r === 'dim' ? 4 : 7, L.r === 'dim' ? 0.3 : 0.45), CX, LY, 1);
      if (L.flash && L.glow > 1) glow(glowSp(L.r === 'wait' ? '#ffd27a' : '#ff8a7a', 18, 14, 0.4), CX, LY, 1);
    }
    // the count sign hangs from the gallery and names what it counts (the town's own "NEEDS YOU" badge counts other things):
    // waiting agents first, else failed ones
    const nw = S.waits.length, nf = S.fails.length;
    let sign = '';
    if (nw || nf) {
      sign = nw ? waitWords(nw) : failWords(nf);
      const s = fitText(sign, W - CX - 15), w = textW(s) + 8, x = Math.max(CX + 6, Math.min(CX + 9, W - 1 - w)), y = 180;
      R(CX + 7, 177, Math.max(1, x + 3 - CX - 7), 1, '#20233a'); P(x + 2, 178, '#5a6078'); P(x + 2, 179, '#5a6078'); P(x + w - 3, 178, '#5a6078'); P(x + w - 3, 179, '#5a6078');
      R(x + 1, 177, w - 3, 1, '#20233a');
      R(x, y, w, 9, OL); R(x + 1, y + 1, w - 2, 7, nw ? '#2a1a24' : '#2a1420'); R(x + 1, y + 1, w - 2, 1, nw ? '#4a2e30' : '#5a2430');
      textL(s, x + 4, y + 2, nw ? '#ffd59a' : '#ff9a9a');
      tipBox([x, y, w, 9], [sign, nw ? 'THE LIGHT RUNS UP THE PATHS TO EACH DOOR WHERE ONE WAITS FOR YOU' : 'THE RED LIGHT RUNS UP THE PATHS TO EACH DOOR WHERE ONE FAILED']);
    }
    // tooltips: the lighthouse, and the pool of light at each lit door
    const tg = target(), names = list => list.map(a => (String(a.title || 'AN AGENT') + (a.project ? ' (' + a.project + ')' : '')).toUpperCase());
    for (const x of S.targets) if (S.lit.includes(x.plot)) {
      const [px, py] = STEP[x.plot];
      tipBox([px - 7, py - 3, 15, 7], [nameOn(x.plot) + ': ' + (x.kind === 'wait' ? waitWords(x.agents.length) : failWords(x.agents.length))].concat(names(x.agents).slice(0, 4)));
    }
    const words = S.mode === 'wait' ? sign + ': THE LIGHT RUNS TO ' + S.targets.filter(x => x.kind === 'wait').map(x => nameOn(x.plot)).join(', ')
      : S.mode === 'fail' ? sign + ': THE RED LIGHT RUNS TO ' + S.targets.map(x => nameOn(x.plot)).join(', ')
        : S.mode === 'sweep' ? 'THE BEAM SWEEPS THE WATER: ' + plural(S.working, 'AGENT') + ' WORKING'
          : LIVE.on ? 'DIM: NO AGENT IS WORKING' : "DIM: NO LIVE WATCHER, SO IT CAN'T SEE THE AGENTS";
    tipBox(HIT, ['THE LIGHTHOUSE', words, roomOf(bidOn(PLACE)) ? 'CLICK IT TO GO IN' : 'CLICK IT FOR ITS CARD']);
    const hold = S.phase === 'hold';
    SEEN.lighthouse = { shown: true, mode: S.mode, phase: S.phase, target: tg && hold ? tg.plot : null, reached: tg && S.lit.includes(tg.plot) ? tg.plot : null,
      lit: S.lit.slice(), targets: S.targets.map(x => x.plot), kind: tg ? tg.kind : null, color: L.r, count: nw, failed: nf, sign,
      flashing: !!L.flash, newAt: S.flashAt, bells: S.bells, rang: S.rang, angle: S.ang, working: S.working, reach: tg ? S.reach.get(tg.plot) || 0 : 0,
      palette: sky.phase === 'day' ? 'day' : 'night' };
  }

  // the bell may only sound after a real gesture (browser rule): the first trusted click or key in town arms the sound
  // (kit.js SFX.armed). A `#room=` link opening a room on load is no gesture: until a click or key, no bell is heard or counted.
  function arm(e) { if (!shown() || (e && e.isTrusted === false)) return; try { if (SFX.arm) SFX.arm(); } catch (err) { /* no sound */ } removeEventListener('pointerdown', arm, true); removeEventListener('keydown', arm, true); }
  addEventListener('pointerdown', arm, true); addEventListener('keydown', arm, true);

  // inside a room the town isn't drawn: still look at the payload once a second, so a new wait rings there too
  setInterval(() => { if (!document.hidden && RS.id && shown()) read(performance.now()); }, 1000);

  // the place (its click box, door, #room=) only once a lighthouse stands there: a town built without [live] has no
  // lighthouse button at all, and new town data that brings one (a swap in place) adds it on the next frame
  function place() { if (!isPlace(PLACE) && shown()) addPlace(PLACE, { hit: HIT, door: [CX, 196], word: 'lighthouse' }); }
  place();
  TOWN_LAYERS.sea.push(sea); TOWN_LAYERS.scene.push(tower); TOWN_LAYERS.lights.push(lights);
  return { S, STEP, HIT, BOX, CX, OX, OY, LAND, BLOCK, angTo, routeOf, pathOf, TUNE };
})();
