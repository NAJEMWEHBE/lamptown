"""The lighthouse in the bay (web/lighthouse.js) in a real browser (headless Chrome, as in
``test_town.py``).

It stands only in a town built with ``[live]`` on (the data has ``parts.lighthouse``) and follows
the live payload: dim when nobody works, the beam sweeping the water while an agent works, a
flash (and one bell) on a NEW wait, then a steady amber beam held on the landing by the slip,
its light running up the town's footpaths to the door that needs you; the same in red for a
failure. What the last frame drew is in ``SEEN.lighthouse``. Payloads are fed with
``lamptownLive()`` and a fresh ``at`` (``?live=0``: no polling); every name is made up.
"""

from __future__ import annotations

from pathlib import Path

from _browser import browser
from lamptown import build as B
from lamptown.config import parse_config
from test_town import FICTIONAL, NOW, ROOM_JS, run

pytestmark = browser

LIVE_TOML = FICTIONAL + '\n[live]\nstatus_dir = "status"\n'
MOVING = "?live=0&motion=1"   # the scene animates (frames in the page's virtual time)
STILL = "?live=0&t=300"       # a still scene: the beam shows where it settles
DAY = "&at=2026-03-14T12:00:00Z"
NIGHT = "&at=2026-03-14T22:00:00Z"
PLOTS = ["tower", "library", "gate", "lab", "workshop", "boathouse"]

# feed(agents): a fresh payload; until(f, cap): wait for f() up to cap ms of page time
HELP = """
const feed = (agents, at) => { lamptownLive({ spec: 'lamptown-live/1',
  at: at || new Date().toISOString(), rev: '', more: 0, agents, projects: [],
  need: agents.filter(a => a.state === 'waiting').length }); };
const ag = (id, state, plot, since) => ({ id, tool: 'codex', title: 'Agent ' + id,
  project: 'paper-kite', plot, state, since: since || '2026-03-14T21:00:00Z', parent: null,
  subagents: 0, files: 0, commits: 0, pushed: false, ci: null, private: false });
const until = async (f, cap) => {
  for (let w = 0; w < (cap || 4000) && !f(); w += 50) await wait(50);
  return !!f(); };
const OLD = () => new Date(Date.now() - 600000).toISOString();
const L = () => SEEN.lighthouse || {};
const over = (a, c) => a[0] < c[0] + c[2] && c[0] < a[0] + a[2]
  && a[1] < c[1] + c[3] && c[1] < a[1] + a[3];
// the canvas, read at native pixels: grab() all of it, at(d, x, y) the [r, g, b] of one pixel;
// without(layer, name, f): f() with one TOWN_LAYERS hook taken out, then put back
const CV = () => document.getElementById('c');
const grab = () => CV().getContext('2d').getImageData(0, 0, CV().width, CV().height).data;
const at = (d, x, y) => { const k = CV().width / W;
  const j = (Math.floor((y + 0.5) * k) * CV().width + Math.floor((x + 0.5) * k)) * 4;
  return [d[j], d[j + 1], d[j + 2]]; };
const dist = (p, q) => Math.abs(p[0] - q[0]) + Math.abs(p[1] - q[1]) + Math.abs(p[2] - q[2]);
const without = async (layer, name) => { const hooks = TOWN_LAYERS[layer];
  const i = hooks.findIndex(f => f.name === name), fn = hooks[i];
  hooks.splice(i, 1); kick(); await wait(150); const d = grab(); hooks.splice(i, 0, fn);
  return d; };
"""


def town(tmp_path: Path, live: bool = True) -> Path:
    (tmp_path / "room.js").write_text(ROOM_JS, encoding="utf-8")
    toml = LIVE_TOML if live else FICTIONAL
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    return B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW).town


def probe(page: Path, body: str, query: str = MOVING, frag: str = "") -> dict:
    return run(page, HELP + body, query=query, frag=frag)


# ----------------------------------------------------------------------------- there or not
def test_no_lighthouse_when_live_is_off(tmp_path):
    r = probe(town(tmp_path, live=False), """
      feed([ag('a', 'waiting', 'gate')]); await wait(200);
      const b = document.querySelector('.hit[data-plot="lighthouse"]');
      return { seen: L(), button: !!b, place: isPlace('lighthouse'),
               part: !!partOn('lighthouse') };""")
    assert r["seen"] == {"shown": False}
    assert r["part"] is False and r["place"] is False
    assert r["button"] is False   # no button at all


def test_lighthouse_stands_in_the_bay_with_live_on(tmp_path):
    r = probe(town(tmp_path), """
      await until(() => L().shown);
      const b = document.querySelector('.hit[data-plot="lighthouse"]');
      const box = PLACES.lighthouse.hit;
      return { seen: L(), hidden: b.hidden, label: b.getAttribute('aria-label'), box,
               clash: Object.keys(PLOT).filter(p => over(box, PLOT[p].hit)),
               corner: over(box, CORNER.hit), tip: (tipAt(LH.CX, 190) || { tip: [] }).tip,
               trees: TREES.filter(([x, y, rr]) => Math.abs(x - LH.CX) < 12 + rr && y > 155) };""")
    assert r["seen"]["shown"] is True and r["seen"]["mode"] == "idle"
    assert r["hidden"] is False and r["label"].startswith("Lighthouse")
    assert r["clash"] == [] and r["corner"] is False   # clear of every plot and the corner's link
    assert r["trees"] == []                             # the trees stepped aside
    assert r["tip"][0] == "THE LIGHTHOUSE"


def test_the_corner_box_never_covers_the_rock(tmp_path):
    # finding 6: the BUILT corner (and the app's wider "LIVE - UPDATED ...") sat over the rock
    r = probe(town(tmp_path), """
      await until(() => L().shown);
      const stamp = String(D.built || '').toUpperCase().replace(/,/g, '');
      const box = s => { const w = Math.max(textW(s), textW(CORNER.det)) + 6;
        return [W - w - 2, H - 17, w, 16]; };
      const boxes = ['LIVE', 'BUILT ' + stamp, 'LIVE - UPDATED ' + stamp].map(box);
      // every pixel the tower and its rock draw: the lighthouse's draw with and without it
      kick(); await wait(150); const a = grab(), b = await without('scene', 'tower');
      let low = 0;
      for (let y = 150; y < H; y++) for (let x = 280; x < W; x++) {
        if (dist(at(a, x, y), at(b, x, y)) > 0) low = Math.max(low, y); }
      return { boxes, low, hit: PLACES.lighthouse.hit, corner: SEEN.corner };""", query=STILL)
    assert r["corner"].startswith("BUILT")   # no payload: the wide corner
    assert r["low"] > 190                    # it drew the rock
    for bx in r["boxes"]:
        assert r["low"] < bx[1] - 1, (r["low"], bx)           # a clear row above every corner box
        hit = r["hit"]
        assert hit[1] + hit[3] <= bx[1], (hit, bx)             # and the click box too


# ----------------------------------------------------------------------------- idle / working
def test_idle_is_dim_and_still(tmp_path):
    r = probe(town(tmp_path), """
      feed([ag('a', 'ok', 'boathouse'), ag('b', 'quiet', 'lab')]); await wait(100);
      const a0 = L().angle; await wait(1500);
      const idle = L();
      feed([ag('a', 'working', 'boathouse')], OLD());   // stale: no Watcher
      await wait(300);
      return { idle, a0, stale: L() };""")
    assert r["idle"]["mode"] == "idle" and r["idle"]["phase"] == "off"
    assert r["idle"]["angle"] == r["a0"] and r["idle"]["color"] == "dim"
    assert r["idle"]["sign"] == "" and r["idle"]["flashing"] is False
    assert r["stale"]["mode"] == "idle"   # an old payload is no news: no sweep


def test_working_sweeps_the_water(tmp_path):
    r = probe(town(tmp_path), """
      feed([ag('a', 'working', 'boathouse'), ag('b', 'working', 'lab')]);
      await until(() => L().mode === 'sweep');
      const a0 = L().angle; await wait(1200);
      return { s: L(), a0, tip: (tipAt(LH.CX, 190) || { tip: [] }).tip };""")
    s = r["s"]
    assert s["mode"] == "sweep" and s["phase"] == "sweep" and s["working"] == 2
    assert s["angle"] != r["a0"]                    # it moves
    assert 2.6 <= s["angle"] <= 3.3                 # over the water (the bay, left of the rock)
    assert s["target"] is None and s["sign"] == "" and s["color"] == "white"
    assert "2 AGENTS WORKING" in r["tip"][1]


# ----------------------------------------------------------------------------- waiting
def test_new_wait_flashes_then_settles_and_rings_once(tmp_path):
    r = probe(town(tmp_path), """
      LH.TUNE.flash = 1200;
      let played = 0; SFX.bell = () => { played++; return true; };
      feed([ag('a', 'working', 'boathouse')]); await until(() => L().mode === 'sweep');
      const before = L();
      feed([ag('a', 'working', 'boathouse'), ag('b', 'waiting', 'gate')]);
      await until(() => L().flashing, 1000);
      const flash = L();
      await until(() => !L().flashing, 3000);
      const settled = L();
      const A = ag('a', 'working', 'boathouse');
      feed([A, ag('b', 'waiting', 'gate')]); await wait(300);   // the same wait again
      const again = L();
      feed([A, ag('c', 'error', 'lab'), ag('b', 'waiting', 'gate')]); await wait(300);
      const failed = L();
      feed([A, ag('b', 'working', 'gate')]); await wait(200);   // answered
      feed([A, ag('b', 'waiting', 'gate')]); await wait(200);   // waits again: new
      return { before, flash, settled, again, failed, back: L(), played, toast: RS.toast };""")
    assert r["before"]["bells"] == 0
    f = r["flash"]
    assert f["flashing"] is True and f["mode"] == "wait" and f["bells"] == 1
    s = r["settled"]
    assert s["flashing"] is False and s["color"] == "wait" and s["mode"] == "wait"
    # no news: no flash, no bell; a failure flashes but rings no bell
    assert r["again"]["bells"] == 1 and r["again"]["newAt"] == s["newAt"]
    assert r["failed"]["bells"] == 1 and r["failed"]["newAt"] > s["newAt"]
    assert r["failed"]["target"] == "gate"   # a new failure doesn't take the light off a wait
    assert r["back"]["bells"] == 2 and r["played"] == 2
    assert r["toast"] is None


def test_first_payload_flashes_only_fresh_waits(tmp_path):
    r = probe(town(tmp_path), """
      let played = 0; SFX.bell = () => { played++; return true; };
      const O = ag('old', 'waiting', 'gate');   // waiting since long before the page opened
      feed([O]); await wait(300);
      const old = L();
      feed([O, ag('new', 'waiting', 'lab', new Date().toISOString())]); await wait(300);
      return { old, now: L(), played };""")
    o = r["old"]
    assert o["bells"] == 0 and o["flashing"] is False and o["mode"] == "wait"
    assert r["now"]["bells"] == 1 and r["played"] == 1


def test_bell_needs_a_gesture(tmp_path):
    r = probe(town(tmp_path), """
      feed([]); await wait(100);
      feed([ag('b', 'waiting', 'gate')]); await wait(300);
      return { s: L(), ctx: !!SFX.ctx() };""")
    assert r["ctx"] is False                       # no click or key yet: no sound
    assert r["s"]["bells"] == 1 and r["s"]["rang"] == 0


def test_a_room_link_does_not_arm_the_bell(tmp_path):
    # finding 4: #room= makes a sound context with no gesture; the bell must stay silent and
    # uncounted until a real click or key arms it
    r = probe(town(tmp_path), """
      await until(() => RS.id === 'lighthouse', 2000);
      const ctx = !!SFX.ctx();
      feed([ag('b', 'waiting', 'gate', new Date().toISOString())]);
      await until(() => LH.S.bells > 0, 2500);
      const deep = { bells: LH.S.bells, rang: LH.S.rang, armed: SFX.armed, direct: SFX.bell() };
      dispatchEvent(new KeyboardEvent('keydown', { key: 'x' }));   // a script's key is no gesture
      const fake = SFX.armed;
      SFX.arm();                                       // what a real key or click runs
      return { ctx, deep, fake, armed: SFX.armed };""", frag="#room=lighthouse")
    assert r["ctx"] is True                        # the room made one: that alone is not enough
    d = r["deep"]
    assert d["bells"] == 1 and d["rang"] == 0 and d["direct"] is False
    assert d["armed"] is False and r["fake"] is False
    assert r["armed"] is True


def test_one_wait_beam_reaches_its_door(tmp_path):
    r = probe(town(tmp_path), """
      feed([]); await wait(100);
      feed([ag('a', 'working', 'boathouse'), ag('b', 'waiting', 'gate')]);
      await until(() => L().reached === 'gate', 4000);
      const s = L(), [px, py] = LH.STEP.gate;
      return { s, sign: s.sign, tip: (tipAt(px, py) || { tip: [] }).tip, door: PLACES.gate.door,
               ang: LH.angTo('gate') };""", query=MOVING)
    s = r["s"]
    assert s["reached"] == "gate" and s["target"] == "gate" and s["targets"] == ["gate"]
    assert abs(s["angle"] - r["ang"]) < 1e-6 and s["count"] == 1 and r["sign"] == "1 AGENT WAITS"
    assert r["tip"][0] == "TEST ROOM: 1 AGENT WAITS" and r["tip"][1] == "AGENT B (PAPER-KITE)"


def test_the_lit_way_walks_the_footpaths(tmp_path):
    # finding 1: from the landing by the slip, along the town's footpaths and the lamplighters'
    # spurs, to the door; never over a building or a name plate
    r = probe(town(tmp_path), """
      await until(() => L().shown);
      const boxes = ORDER.flatMap(p => { const [cx, y, mw] = PLOT[p].plate;
        return [['foot ' + p, PLOT[p].foot], ['plate ' + p, [cx - mw / 2, y, mw, 9]]]; });
      const inBox = (x, y, b) => x >= b[0] && x < b[0] + b[2] && y >= b[1] && y < b[1] + b[3];
      // the town's footpaths (as the background paints them) and the lamplighters' spurs
      const lanes = [];
      for (const r of ROUTES) for (let i = 1; i < r.pts.length; i++) {
        lanes.push([r.pts[i - 1], r.pts[i]]); }
      for (const p of Object.values(LAMPLIGHTERS.PLACE)) {
        let c = p.join;
        for (const s of p.spur) { lanes.push([c, [s[0], c[1]]], [[s[0], c[1]], s]); c = s; } }
      const onLane = (x, y) => lanes.some(([a, b]) =>
        x >= Math.min(a[0], b[0]) - 2 && x <= Math.max(a[0], b[0]) + 2
        && y >= Math.min(a[1], b[1]) - 2 && y <= Math.max(a[1], b[1]) + 2);
      const near = (ps, x, y) => ps.some(p => Math.abs(p[0] - x) <= 1 && Math.abs(p[1] - y) <= 1);
      const out = {};
      for (const plot of ORDER) {
        const pix = LH.pathOf(plot), [sx, sy] = LH.STEP[plot];
        const first = pix.filter(p => p[2] === 0), last = pix.filter(p => p[2] === 1);
        const hits = pix.flatMap(([x, y]) => boxes.filter(([, b]) => inBox(x, y, b)))
          .map(([n]) => n);
        out[plot] = { n: pix.length, hits: [...new Set(hits)],
          start: near(first, LH.LAND[0], LH.LAND[1]), end: near(last, sx, sy),
          lane: pix.filter(([x, y]) => onLane(x, y)).length / pix.length,
          aim: LH.angTo(plot) };
      }
      return out;""", query=STILL)
    for plot in PLOTS:
        p = r[plot]
        assert p["n"] > 10, plot
        assert p["hits"] == [], (plot, p["hits"])   # never over a building or a name plate
        assert p["start"] and p["end"], plot        # from the landing to the door
        assert p["aim"] == r["tower"]["aim"]        # the beam on the water: one landing for all
    for plot in ["tower", "library", "gate", "lab"]:   # the doors behind the boathouse
        assert r[plot]["lane"] >= 0.8, (plot, r[plot]["lane"])


def test_several_waits_light_every_door_and_nothing_swings(tmp_path):
    r = probe(town(tmp_path), """
      feed([]); await wait(100);
      feed([ag('a', 'waiting', 'tower'), ag('b', 'waiting', 'boathouse'),
            ag('c', 'waiting', 'boathouse')]);
      await until(() => (L().lit || []).length === 2, 3500);
      const angles = []; for (let i = 0; i < 6; i++) { angles.push(L().angle); await wait(150); }
      return { s: L(), angles };""")
    s = r["s"]
    assert sorted(s["lit"]) == ["boathouse", "tower"]              # each door its lit way
    assert sorted(s["targets"]) == ["boathouse", "tower"]          # one door per building
    assert len(set(r["angles"])) == 1                              # the beam holds still
    assert s["count"] == 3 and s["sign"] == "3 AGENTS WAIT"


def test_doors_are_tracked_by_plot_not_list_order(tmp_path):
    # finding 3: the same waits in another order, or another door answered: nothing moves or regrows
    r = probe(town(tmp_path), """
      feed([]); await wait(100);
      feed([ag('g', 'waiting', 'gate')]); await until(() => L().reached === 'gate', 2500);
      const ls = new Date().toISOString();
      feed([ag('g', 'waiting', 'gate'), ag('l', 'waiting', 'lab', ls)]);
      await until(() => L().reached === 'lab' && (L().lit || []).length === 2, 2500);
      const both = L();
      feed([ag('l', 'waiting', 'lab', ls), ag('g', 'waiting', 'gate')]);   // same two, other order
      await wait(250); const flipped = L();
      feed([ag('l', 'waiting', 'lab', ls)]);   // the gate answered: nothing new at the lab
      await wait(250); const answered = L();
      return { both, flipped, answered };""")
    b, f, a = r["both"], r["flipped"], r["answered"]
    assert b["target"] == "lab"                                    # the newest gets the bright way
    assert f["target"] == "lab" and f["reached"] == "lab" and f["reach"] == 1
    assert f["angle"] == b["angle"] and sorted(f["lit"]) == ["gate", "lab"]
    assert a["target"] == "lab" and a["reached"] == "lab" and a["reach"] == 1
    assert a["lit"] == ["lab"] and a["angle"] == b["angle"]


def test_still_scene_shows_every_waiting_door(tmp_path):
    # finding 5: reduced motion / ?t= showed only the first door
    r = probe(town(tmp_path), """
      feed([ag('a', 'waiting', 'lab'), ag('b', 'waiting', 'workshop')]); await wait(200);
      const first = L(); kick(); await wait(100);
      return { first, later: L() };""", query=STILL)
    for s in (r["first"], r["later"]):
        assert sorted(s["lit"]) == ["lab", "workshop"] and s["flashing"] is False
        assert s["sign"] == "2 AGENTS WAIT"


def test_the_lit_way_reads_by_day(tmp_path):
    # finding 7: by day the beam, the way and the pool all but vanished on the bright ground
    body = """
      feed([ag('b', 'waiting', 'gate')]); await until(() => L().reached === 'gate', 3000);
      kick(); await wait(150); const on = grab();
      const noSea = await without('sea', 'sea'), noPool = await without('scene', 'tower');
      // the mean colour change over the pixels the light changed (land: only land, or only water)
      const mean = (b, x0, y0, x1, y1, land) => { let s = 0, n = 0;
        for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
          if (land !== undefined && (y > shoreY(x) - 1) === land) continue;
          const d = dist(at(on, x, y), at(b, x, y)); if (d > 0) { s += d; n++; } }
        return n ? s / n : 0; };
      const [px, py] = LH.STEP.gate;
      return { phase: SEEN.phase, way: mean(noSea, 0, 0, W - 1, 196, true),
               beam: mean(noSea, 0, 196, W - 1, H - 1, false),
               pool: mean(noPool, px - 7, py - 3, px + 7, py + 3) };"""
    day = probe(town(tmp_path), body, query=STILL + DAY)
    night = probe(town(tmp_path), body, query=STILL + NIGHT)
    assert day["phase"] == "day" and night["phase"] == "night"
    assert day["way"] >= 90 and day["pool"] >= 90 and day["beam"] >= 60, day
    assert night["way"] >= 60 and night["pool"] >= 90 and night["beam"] >= 60, night


# ----------------------------------------------------------------------------- failures
def test_a_failure_is_red(tmp_path):
    r = probe(town(tmp_path), """
      feed([]); await wait(100);
      feed([ag('a', 'working', 'boathouse'), ag('c', 'error', 'workshop')]);
      await until(() => L().reached === 'workshop' && !L().flashing, 5000);
      return L();""")
    assert r["mode"] == "fail" and r["kind"] == "fail" and r["color"] == "fail"
    assert r["reached"] == "workshop" and r["sign"] == "1 AGENT FAILED" and r["failed"] == 1
    assert r["bells"] == 0


def test_the_sign_names_what_it_counts(tmp_path):
    # finding 2: the town's top-right badge says "N NEEDS YOU" for its own things; the
    # lighthouse's sign counts agents, so it says so
    r = probe(town(tmp_path), """
      const out = {};
      const sign = async (k, agents) => { feed(agents); await wait(150); out[k] = L().sign; };
      const A = ag('a', 'waiting', 'gate'), C = ag('c', 'error', 'lab');
      await sign('one', [A]);
      await sign('two', [A, ag('b', 'waiting', 'lab')]);
      await sign('fail', [C]);
      await sign('fails', [C, ag('d', 'error', 'tower')]);
      await sign('mixed', [C, A]);
      return { signs: out };""", query=STILL)
    assert r["signs"] == {"one": "1 AGENT WAITS", "two": "2 AGENTS WAIT", "fail": "1 AGENT FAILED",
                          "fails": "2 AGENTS FAILED", "mixed": "1 AGENT WAITS"}


def test_waits_come_before_failures(tmp_path):
    r = probe(town(tmp_path), """
      feed([ag('c', 'error', 'lab'), ag('b', 'waiting', 'gate')]); await wait(200);
      return L();""", query=STILL)
    assert r["mode"] == "wait" and r["targets"] == ["gate", "lab"] and r["sign"] == "1 AGENT WAITS"
    assert r["target"] == "gate" and sorted(r["lit"]) == ["gate", "lab"]


# ----------------------------------------------------------------------------- the way in
def test_click_walks_into_the_lighthouse_room(tmp_path):
    # the room itself is L9's (web/rooms/lighthouse.js); here: the click box, the door, #room=
    r = probe(town(tmp_path), """
      document.querySelector('.hit[data-plot="lighthouse"]').click();
      await until(() => RS.id === 'lighthouse', 2000);
      return { id: RS.id, plot: RS.plot, hash: location.hash, door: PLACES.lighthouse.door };""")
    assert r["id"] == "lighthouse" and r["plot"] == "lighthouse"
    assert r["hash"] == "#room=lighthouse"
    assert r["door"] == [328, 196]   # the walk-in dives through the tower's door


def test_room_link_opens_the_lighthouse_room(tmp_path):
    r = probe(town(tmp_path), """
      await until(() => RS.id === 'lighthouse', 2000);
      return { id: RS.id };""", frag="#room=lighthouse")
    assert r["id"] == "lighthouse"


def test_a_new_wait_rings_inside_a_room_too(tmp_path):
    r = probe(town(tmp_path), """
      let played = 0; SFX.bell = () => { played++; return true; };
      feed([]); await wait(100);
      document.querySelector('.hit[data-plot="gate"]').click();
      await until(() => RS.mode === 'room');
      feed([ag('b', 'waiting', 'gate')]);
      await until(() => played > 0, 2500);
      return { id: RS.id, played, bells: LH.S.bells };""")
    assert r["id"] == "tr" and r["played"] == 1 and r["bells"] == 1


def test_alerts_never_toast():
    src = (Path(B.__file__).parent / "web" / "lighthouse.js").read_text(encoding="utf-8")
    assert "toast(" not in src
