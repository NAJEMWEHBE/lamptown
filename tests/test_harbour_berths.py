"""The Harbour's berths (web/berths.js) in a real browser (headless Chrome, as in ``test_town.py``).

One berth per live project along the waterfront, kept while the project is live (and a grace after,
so a short gap never reshuffles the row); a new project's boat is launched once; past the last berth
the rest share one "+N" mooring; the agents at work stand at their own project's berth (through
``LAMPLIGHTERS.berth``); the Harbour room shows the same berths. The probes feed payloads with
``lamptownLive`` (``?live=0``: no polling) and read ``SEEN.berths`` / ``SEEN.agents`` / the room's
``seen().quay``. Every name here is made up.
"""

from __future__ import annotations

from importlib import resources

from _browser import browser
from test_town import FICTIONAL, build_town, run

pytestmark = browser

STILL = "?live=0&t=300"
MOTION = "?live=0&motion=1"
ROOM = "agents"  # the fictional town's harbour

HELP = """
const until = async (f, cap) => {
  for (let w = 0; w < (cap || 4000) && !f(); w += 30) await wait(30);
  return !!f();
};
const ago = m => new Date(Date.now() - m * 6e4).toISOString();
const A = (id, project, state, o) => Object.assign({
  id, tool: 'codex', title: 'Agent ' + id, project, plot: 'boathouse', state: state || 'working',
  since: ago(1), parent: null, subagents: 0, files: 0, commits: 0, pushed: false, ci: null,
  private: false,
}, o || {});
const PR = (id, i, isNew) => ({
  id, name: id.replace(/-/g, ' ').toUpperCase(), berth: i, new: !!isNew });
let N = 0;   // each feed is a new payload object, as the app pushes them
const feed = (agents, projects) => { N++; lamptownLive({
  spec: 'lamptown-live/1', at: new Date().toISOString(), rev: String(N),
  need: agents.filter(a => a.state === 'waiting').length, more: 0, agents,
  projects: projects.map((p, i) => typeof p === 'string' ? PR(p, i) : p) }); };
const B = () => SEEN.berths || null;
const ids = () => (B() ? B().list.map(b => b.id) : null);
const slots = () => Object.fromEntries((B() ? B().list : []).map(b => [b.id, b.slot]));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
"""

NAMES = ["paper-kite", "tide-api", "moss-engine", "kite-game", "orchard", "brass-bot",
         "heron", "salt-book"]


def probe(tmp_path, body: str, query: str = STILL, frag: str = "") -> dict:
    return run(build_town(tmp_path, FICTIONAL), HELP + body, query, frag)


def test_the_page_inlines_berths_js_once_after_live_js(tmp_path):
    town = build_town(tmp_path, FICTIONAL).read_text(encoding="utf-8")
    assert town.count('<script data-src="berths.js">') == 1
    assert town.index('data-src="live.js"') < town.index('data-src="berths.js"')
    src = resources.files("lamptown.web").joinpath("berths.js").read_text(encoding="utf-8")
    assert "toast(" not in src  # alerts are never toasts
    html = resources.files("lamptown.web").joinpath("town.html").read_text(encoding="utf-8")
    assert html.index('src="live.js"') < html.index('src="berths.js"')


def test_one_berth_per_live_project_and_none_without(tmp_path):
    r = probe(tmp_path, """
      await until(() => B() !== null);
      const before = B();
      feed([], []); await until(() => B() && B().list.length === 0);
      const empty = B();
      feed([A('a1', 'paper-kite'), A('a2', 'paper-kite'), A('b1', 'tide-api'),
            A('c1', 'maple-docs', 'working', { plot: 'library' })],
           ['paper-kite', 'tide-api', 'moss-engine', 'maple-docs']);
      await until(() => B() && B().list.length === 3);
      return { before, empty, b: B(), err: SEEN.berthsError || null };""")
    assert r["err"] is None
    assert r["before"]["list"] == [] and r["empty"]["list"] == [] and r["empty"]["over"] == []
    b = {x["id"]: x for x in r["b"]["list"]}
    # moss-engine: a project with no agent still has its berth; maple-docs works at the library
    assert list(b) == ["paper-kite", "tide-api", "moss-engine"]
    assert [x["slot"] for x in r["b"]["list"]] == [0, 1, 2]
    assert b["paper-kite"]["agents"] == ["a1", "a2"] and b["moss-engine"]["agents"] == []
    assert b["paper-kite"]["name"] == "PAPER KITE"
    assert b["paper-kite"]["tag"] == "PAPER"  # the whole name doesn't fit between boats
    assert b["moss-engine"]["tag"] == "MOSS"  # the lighthouse rock bounds the outermost one
    xs = [x["x"] for x in r["b"]["list"]]
    assert len(set(xs)) == 3 and all(w == 30 for w in (x["w"] for x in r["b"]["list"]))
    assert r["b"]["over"] == [] and r["b"]["launches"] == 0 and r["b"]["launch"] is None


def test_berths_stay_put_across_data_swaps(tmp_path):
    r = probe(tmp_path, """
      const out = {};
      feed([], ['paper-kite', 'tide-api', 'moss-engine']);
      await until(() => ids() && ids().length === 3); out.a = slots();
      // the payload's own berth order shuffles and paper-kite leaves: nobody else moves
      feed([], [PR('moss-engine', 0), PR('tide-api', 1)]);
      await until(() => ids().length === 2); out.b = slots();
      // a newcomer within the grace does not take paper-kite's berth
      feed([], [PR('moss-engine', 0), PR('tide-api', 1), PR('heron', 2)]);
      await until(() => ids().length === 3); out.c = slots();
      // paper-kite comes back within the grace: its old berth
      feed([], [PR('moss-engine', 0), PR('tide-api', 1), PR('heron', 2), PR('paper-kite', 3)]);
      await until(() => ids().length === 4); out.d = slots();
      // paper-kite leaves for longer than the grace: its berth is given to the next newcomer
      feed([], [PR('moss-engine', 0), PR('tide-api', 1), PR('heron', 2)]);
      await until(() => ids().length === 3);
      BERTHS.S.seen.set('paper-kite', performance.now() - BERTHS.GRACE_MS - 1);
      feed([], [PR('moss-engine', 0), PR('tide-api', 1), PR('heron', 2), PR('salt-book', 3)]);
      await until(() => ids().length === 4); out.e = slots();
      return out;""")
    assert r["a"] == {"paper-kite": 0, "tide-api": 1, "moss-engine": 2}
    assert r["b"] == {"tide-api": 1, "moss-engine": 2}
    assert r["c"] == {"tide-api": 1, "moss-engine": 2, "heron": 3}
    assert r["d"] == {"paper-kite": 0, "tide-api": 1, "moss-engine": 2, "heron": 3}
    assert r["e"] == {"salt-book": 0, "tide-api": 1, "moss-engine": 2, "heron": 3}


def test_a_new_project_is_launched_once(tmp_path):
    r = probe(tmp_path, """
      Object.assign(BERTHS.TUNE, { slide: 300, splash: 150, glide: 150 });   // a quick launch
      // the first payload a page sees: its boats are already moored, even a new one
      feed([A('a1', 'paper-kite')], [PR('paper-kite', 0, true)]);
      await until(() => ids() && ids().length === 1); await wait(200);
      const first = { launches: B().launches, launch: B().launch };
      const two = [PR('paper-kite', 0, true), PR('glass-kite', 1, true)];
      feed([A('a1', 'paper-kite'), A('n1', 'glass-kite')], two);
      const phases = [], xs = [];
      await until(() => B().launch !== null, 2000);
      await until(() => { const L = B().launch; if (!L) return true;
        if (phases[phases.length - 1] !== L.phase) phases.push(L.phase);
        xs.push(L.x); return false; }, 4000);
      const moored = B().list.find(b => b.id === 'glass-kite');
      // the same project, still marked new, in later payloads: calm
      feed([A('a1', 'paper-kite'), A('n1', 'glass-kite')], two);
      await wait(400);
      feed([A('a1', 'paper-kite'), A('n1', 'glass-kite', 'waiting')], two);
      await wait(400);
      return { first, phases, x0: xs[0], x1: xs[xs.length - 1], moored, after: B().launches,
        launch: B().launch };""",
              query=MOTION)
    assert r["first"] == {"launches": 0, "launch": None}
    assert r["phases"] == ["slide", "splash", "glide"], r["phases"]
    assert r["moored"]["slot"] == 1 and not r["moored"]["launching"] and r["moored"]["tag"]
    assert r["x1"] > r["x0"]  # glided out along the water to its berth, right of the slipway
    assert r["after"] == 1 and r["launch"] is None


def test_a_still_scene_launches_nothing(tmp_path):
    r = probe(tmp_path, """
      feed([], ['paper-kite']); await until(() => ids() && ids().length === 1);
      feed([], [PR('paper-kite', 0), PR('glass-kite', 1, true)]);
      await until(() => ids().length === 2); await wait(300);
      return { launch: B().launch, list: B().list };""")
    assert r["launch"] is None and not any(b["launching"] for b in r["list"])
    assert all(b["tag"] for b in r["list"])


def test_overflow_shares_one_mooring_and_moves_up_when_a_berth_frees(tmp_path):
    # review L10 #1: a newcomer in another project's grace went to the overflow and stayed there
    # for good, next to empty berths
    names = ", ".join(f"'{n}'" for n in NAMES)
    r = probe(tmp_path, f"""
      const names = [{names}];
      feed([], names); await until(() => B() && B().over.length === 2);
      const a = B(), tips = TIPS.map(t => t.tip);
      // a berth frees up: the oldest project on the overflow mooring takes it at once (the
      // grace holds a berth only while nobody needs it)
      feed([], names.slice(1)); await until(() => B().over.length === 1);
      const b = B();
      // two newcomers while tide-api is away in its grace: one takes its berth, one waits
      feed([], names.slice(2).concat(['fog-horn', 'map-room']));
      await until(() => B().over.length === 2);
      const c = B();
      // everyone but two leaves: both move up, nobody is left on the mooring
      feed([], ['fog-horn', 'map-room']); await until(() => B().over.length === 0);
      const d = B();
      // 15 projects, then 200 new ones: the berths are used, the rest share the mooring
      const many = Array.from({{ length: 15 }}, (_, i) => 'old-' + i);
      feed([], many); await until(() => B().list.length === 6);
      const fresh = Array.from({{ length: 200 }}, (_, i) => 'new-' + i);
      feed([], fresh); await until(() => B().over.length === 194);
      return {{ a, tips, b, c, d, e: {{ list: B().list.length, over: B().over.length }} }};""")
    a, b, c, d = r["a"], r["b"], r["c"], r["d"]
    assert [x["id"] for x in a["list"]] == NAMES[:6]
    assert a["over"] == NAMES[6:] and a["overAt"] is not None
    more = [t for t in r["tips"] if t[0].startswith("2 MORE PROJECTS")]
    assert more and more[0][-2:] == ["HERON", "SALT BOOK"]
    assert {x["id"]: x["slot"] for x in b["list"]}["heron"] == 0  # paper-kite's berth
    assert b["over"] == ["salt-book"]
    slots_c = {x["id"]: x["slot"] for x in c["list"]}
    assert slots_c["salt-book"] == 1 and c["over"] == ["fog-horn", "map-room"]
    assert {k: v for k, v in slots_c.items() if k not in ("salt-book",)} == {
        k: v for k, v in {x["id"]: x["slot"] for x in b["list"]}.items()
        if k not in ("tide-api", "salt-book")}  # nobody else moved
    assert sorted(x["id"] for x in d["list"]) == ["fog-horn", "map-room"] and d["over"] == []
    assert r["e"] == {"list": 6, "over": 194}


def test_the_overflow_is_never_moored_beside_an_empty_berth(tmp_path):
    # review L10 #1: churn with the grace running; berths stay stable for projects still live
    r = probe(tmp_path, """
      let seed = 7;
      const rnd = n => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
      const pool = Array.from({ length: 12 }, (_, i) => 'proj-' + i), bad = [], moved = [];
      let live = pool.slice(0, 8), prev = {};
      for (let step = 0; step < 60; step++) {
        if (rnd(3) === 0 && live.length) live.splice(rnd(live.length), 1);
        else {
          const out = pool.filter(p => !live.includes(p));
          if (out.length) live.push(out[rnd(out.length)]);
        }
        if (rnd(4) === 0) for (const [id] of BERTHS.S.seen)   // now and then the grace runs out
          if (!live.includes(id)) BERTHS.S.seen.set(id, performance.now() - BERTHS.GRACE_MS - 1);
        feed([], live.slice());
        await until(() => B() && B().list.length + B().over.length === live.length);
        const b = B(), now = Object.fromEntries(b.list.map(x => [x.id, x.slot]));
        if (b.over.length && b.list.length < 6) bad.push([step, b.list.length, b.over.length]);
        for (const [id, s] of Object.entries(prev))
          if (id in now && now[id] !== s) moved.push([step, id]);
        prev = now;
      }
      return { bad, moved };""")
    assert r["bad"] == [], r["bad"]
    assert r["moved"] == [], r["moved"]  # a project on a berth never changes berth


def test_a_project_moving_up_sails_and_its_agents_walk(tmp_path):
    # review L10 #1: moving up is a calm sail and a walk along the shore, never a jump
    names = ", ".join(f"'{n}'" for n in NAMES)
    r = probe(tmp_path, f"""
      BERTHS.TUNE.glide = 120; LAMPLIGHTERS.speed = 90;
      const names = [{names}];
      const ag = names.map((p, i) => A('w' + i, p));
      feed(ag, names); await until(() => B() && B().over.length === 2
        && (SEEN.agents || []).length === 8);
      const fig = id => (SEEN.agents || []).find(a => a.id === id);
      const start = fig('w6');
      // moss-engine's agent is done and the project leaves: heron moves up to its berth
      feed(ag.filter(a => a.project !== 'moss-engine'), names.filter(n => n !== 'moss-engine'));
      const track = [], sails = [];
      await until(() => {{ const f = fig('w6'), s = B().sail.find(q => q.id === 'heron');
        if (f) track.push([f.x, f.y, f.mode]); if (s) sails.push(s.x);
        return track.some(p => p[2] === 'walk') && f && f.mode === 'stand' && !s; }}, 5000);
      const berth = B().list.find(b => b.id === 'heron');
      return {{ start, track, sails, berth, end: fig('w6'), moves: B().moves,
        spot: BERTHS.spot(ag[6]), over: B().over }};""", query=MOTION)
    t, start, end, berth = r["track"], r["start"], r["end"], r["berth"]
    assert berth["slot"] == 2 and r["over"] == ["salt-book"] and r["moves"] == 1
    assert berth["x"] <= end["x"] < berth["x"] + 30 and [end["x"], end["y"]] == r["spot"]
    assert start["x"] < 102  # it stood at the overflow mooring
    walked = [p for p in t if p[2] == "walk"]
    assert len({p[0] for p in walked}) > 10  # it walked there, step by step
    jumps = [max(abs(t[i][0] - t[i - 1][0]), abs(t[i][1] - t[i - 1][1])) for i in range(1, len(t))]
    assert max(jumps) <= 12, max(jumps)
    assert min(p[1] for p in walked) >= 194  # along the shore, not over the boathouse
    s = r["sails"]
    assert len(set(s)) > 5 and s == sorted(s) and s[0] < 110  # her boat sailed along to it


def test_agents_stand_at_their_own_projects_berth(tmp_path):
    r = probe(tmp_path, """
      const ag = [];
      ['paper-kite', 'tide-api', 'moss-engine', 'kite-game', 'orchard'].forEach((p, i) => {
        ag.push(A('w' + i, p));
        ag.push(A('v' + i, p, i === 2 ? 'quiet' : 'working', { tool: 'claude-code' })); });
      ag.push(A('q1', 'paper-kite', 'waiting'));
      feed(ag, ['paper-kite', 'tide-api', 'moss-engine', 'kite-game', 'orchard']);
      await until(() => B() && B().list.length === 5 && (SEEN.agents || []).length === 11);
      await wait(200);
      return { b: B(), agents: SEEN.agents, chair: LAMPLIGHTERS.PLACE.boathouse.seats[0],
        feet: BERTHS.FEET };""")
    at = {}
    for b in r["b"]["list"]:
        for aid in b["agents"]:
            at[aid] = b
    figs = {f["id"]: f for f in r["agents"]}
    for aid, f in figs.items():
        if aid == "q1":
            assert [f["x"], f["y"]] == r["chair"]  # waiting: the Watcher's chair, as before
            continue
        b = at[aid]
        assert b["x"] <= f["x"] < b["x"] + b["w"], (aid, f["x"], b)
        assert f["y"] in (r["feet"], r["feet"] - 6), (aid, f["y"])
        assert f["mode"] == "stand"
    # nobody stands on top of another
    assert len({(f["x"], f["y"]) for f in r["agents"]}) == len(r["agents"])


APART = """
  // side by side: 6 px; one behind another: 12 px, or 7 px and a step to the side
  // (as live.js's spots)
  const apart = (p, q) => { const dx = Math.abs(p[0] - q[0]), dy = Math.abs(p[1] - q[1]);
    return dx >= 6 || dy >= 12 || (dy >= 7 && dx >= 3); };
  const clashes = pts => { const bad = [];
    for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++)
      if (!apart(pts[i][1], pts[j][1])) bad.push([pts[i], pts[j]]);
    return bad; };
"""


def test_overflow_and_berthless_agents_each_have_their_own_spot(tmp_path):
    # review L10 #2: every overflow agent stood on one pixel; #3: private agents (no project) and
    # unlisted projects' agents stood on the berths' back rank
    names = ", ".join(f"'{n}'" for n in NAMES + ["fog-horn", "map-room"])
    r = probe(tmp_path, APART + f"""
      const names = [{names}];
      const ag = names.map((p, i) => A('w' + i, p));
      // (older ones first: these get figures under the cap of 12)
      const pv = o => Object.assign({{ private: true, title: 'Private agent', since: ago(5) }}, o);
      ag.push(A('o2', 'heron', 'working', {{ since: ago(5) }}),
              A('o3', 'salt-book', 'working', {{ since: ago(5) }}),
              A('o4', 'fog-horn', 'working', {{ since: ago(5) }}));
      ag.push(A('pv1', '', 'working', pv()), A('pv2', '', 'quiet', pv()),
              A('pv3', '', 'working', pv({{ plot: 'nowhere' }})),
              A('un1', 'not-listed', 'working', {{ since: ago(5) }}));
      ag.push(A('q1', 'orchard', 'waiting'), A('q2', 'kite-game', 'waiting'),
              A('q3', 'brass-bot', 'waiting'), A('q4', 'tide-api', 'waiting'));
      feed(ag, names);
      await until(() => B() && B().over.length === 4 && (SEEN.agents || []).length === 12);
      await wait(200);
      const figs = SEEN.agents.map(a => [a.id, [a.x, a.y], a.mode]);
      return {{ figs, bad: clashes(figs), b: B(), visit: BERTHS.VISIT_SPOTS,
        over: BERTHS.OVER_SPOTS }};""")
    figs = {f[0]: f for f in r["figs"]}
    assert r["bad"] == [], r["bad"]
    assert len({tuple(f[1]) for f in r["figs"]}) == len(r["figs"])
    shown = [i for i in figs if i in ("w6", "w7", "w8", "w9", "o2", "o3", "o4")]
    assert all(figs[i][1] in r["over"] for i in shown) and len(shown) >= 2
    berthless = [i for i in figs if i in ("pv1", "pv2", "pv3", "un1")]
    assert berthless and all(figs[i][1] in r["visit"] for i in berthless)


def test_a_berthless_agent_steps_off_the_waterfront_when_the_wharf_is_built(tmp_path):
    # review L10 #3: with no berth in use it keeps live.js's own row by the door (no wharf to
    # clash with); the first berth taken, it walks to the visitors' yard
    r = probe(tmp_path, """
      LAMPLIGHTERS.speed = 90;
      const pv = A('pv1', '', 'working', { private: true, title: 'Private agent' });
      feed([pv], []); await until(() => (SEEN.agents || []).length === 1); await wait(100);
      const fig = () => SEEN.agents.find(a => a.id === 'pv1'), before = fig();
      feed([pv, A('a1', 'paper-kite')], ['paper-kite']);
      const modes = new Set();
      await until(() => { const f = fig(); if (f) modes.add(f.mode);
        return modes.has('walk') && f && f.mode === 'stand'; }, 4000);
      return { before, after: fig(), modes: [...modes], row: LAMPLIGHTERS.PLACE.boathouse.stand[0],
        yard: BERTHS.VISIT_SPOTS[0] };""", query=MOTION)
    assert [r["before"]["x"], r["before"]["y"]] == r["row"]
    assert [r["after"]["x"], r["after"]["y"]] == r["yard"] and "walk" in r["modes"]


def test_every_harbour_spot_keeps_clear_of_the_others(tmp_path):
    # the berths (all six), the overflow mooring, the visitors' yard, the waiting seats and the
    # failed spots of the Harbour: no spot hides another (as live.js's own spots)
    r = probe(tmp_path, APART + """
      const P = LAMPLIGHTERS.PLACE.boathouse, pts = [];
      BERTHS.BERTH_SPOTS.forEach((l, s) =>
        l.forEach((p, i) => pts.push(['berth' + s + ':' + i, p])));
      BERTHS.OVER_SPOTS.forEach((p, i) => pts.push(['over:' + i, p]));
      BERTHS.VISIT_SPOTS.forEach((p, i) => pts.push(['visit:' + i, p]));
      P.seats.forEach((p, i) => pts.push(['seat:' + i, p]));
      P.out.forEach((p, i) => pts.push(['out:' + i, p]));
      return { bad: clashes(pts), n: pts.length, over: BERTHS.OVER_SPOTS.length,
        visit: BERTHS.VISIT_SPOTS.length, cap: LAMPLIGHTERS.CAP };""")
    assert r["bad"] == [], r["bad"]
    assert r["over"] >= r["cap"] and r["visit"] >= r["cap"]


def test_the_room_shows_the_same_berths_as_outside(tmp_path):
    names = ", ".join(f"'{n}'" for n in NAMES[:7])
    r = probe(tmp_path, f"""
      const names = [{names}];
      const tool = i => ({{ tool: i % 2 ? 'codex' : 'wren-cli' }});
      feed(names.map((p, i) => A('w' + i, p, 'working', tool(i))), names);
      await until(() => B() && B().over.length === 1);
      const town = B(), coats = (SEEN.agents || []).map(a => [a.tool, a.coat]);
      document.querySelector('.hit[data-id="{ROOM}"]').click();
      await until(() => RS.id === '{ROOM}' && RS.room.seen().quay, 4000);
      const q1 = RS.room.seen().quay, t1 = RS.room.targets().filter(t => t.id.startsWith('berth:'));
      const view = BERTHS.view().berths.map(b => [b.id, b.tip]);
      // a swap while the room is open: the quay follows it
      feed(names.map((p, i) => A('w' + i, p, i === 0 ? 'ok' : 'working')), names);
      await until(() => RS.room.seen().quay && RS.room.seen().quay[0].done === 1);
      return {{ town, q1, q2: RS.room.seen().quay, tips: t1.map(t => [t.id, t.tip]),
        view, coats,
        mine: coats.map(c => BERTHS.coatOf(c[0])), enters: RS.room.seen().enters }};""")
    town, q1, q2 = r["town"], r["q1"], r["q2"]
    cells = [c for c in q1 if "over" not in c]
    assert [c["id"] for c in cells] == [b["id"] for b in town["list"]]  # the same order
    assert [c["name"] for c in cells] == [b["name"] for b in town["list"]]
    assert [c["paint"] for c in cells] == [b["paint"] for b in town["list"]]
    assert [c["agents"] for c in cells] == [b["agents"] for b in town["list"]]
    assert q1[-1]["over"] == town["over"]
    tips = dict(r["tips"])
    for bid, tip in r["view"]:
        assert tips["berth:" + bid] == tip
    assert "berth:more" in tips and tips["berth:more"][0].startswith("1 MORE PROJECT")
    assert q2[0]["agents"] == [] and q2[0]["done"] == 1 and r["enters"] == 1
    assert [c[1] for c in r["coats"]] == r["mine"]  # the room's Lamplighters wear the town's coats


def test_names_only_never_paths_or_messages(tmp_path):
    r = probe(tmp_path, """
      const secret = { message: 'MSG-PLEASE-HIDE the build broke',
        cwd: 'C:\\\\Users\\\\you\\\\work\\\\kite',
        path: '/work/kite/secret.txt', text: 'MSG-PLEASE-HIDE' };
      feed([A('a1', 'paper-kite', 'error', Object.assign({ title: 'Fix the kite' }, secret)),
            A('a2', 'paper-kite', 'working', secret)],
           [Object.assign(PR('paper-kite', 0), secret)]);
      await until(() => B() && B().list.length === 1);
      const town = JSON.stringify({ berths: B(), tips: TIPS,
        view: BERTHS.view().berths.map(b => b.tip),
        hits: document.getElementById('hits').innerHTML });
      document.querySelector('.hit[data-id="agents"]').click();
      await until(() => RS.id === 'agents' && RS.room.seen().quay, 4000);
      const room = JSON.stringify({ quay: RS.room.seen().quay,
        targets: RS.room.targets().map(t => [t.tip, t.label]),
        hits: document.getElementById('hits').innerHTML });
      return { town, room, tip: BERTHS.view().berths[0].tip };""")
    for s in (r["town"], r["room"]):
        assert "MSG-PLEASE-HIDE" not in s and "you\\\\work" not in s and "/work/" not in s
        assert "secret.txt" not in s
    assert r["tip"][0] == "PAPER KITE: ITS BERTH AT THE HARBOUR"
    assert "Fix the kite: FAILED" in r["tip"]


def test_name_tags_are_distinct_short_forms(tmp_path):
    # review L10 #4: "Paper Kite" and "Paper Boat" both read PAPER, "Paper Kite" beside "Kite"
    # read KITE, and the two Tidebooks had no tag at all
    r = probe(tmp_path, """
      const P = [['kite', 'Kite'], ['paper-kite', 'Paper Kite'], ['paper-boat', 'Paper Boat'],
        ['tidebook-indexer', 'Tidebook Indexer'], ['tidebook-web', 'Tidebook Web'],
        ['kite-game', 'Kite Game']];
      feed([], P.map(([id, name], i) => ({ id, name, berth: i, new: false })));
      await until(() => B() && B().list.length === 6);
      const one = Object.fromEntries(B().list.map(b => [b.id, b.tag]));
      // Paper Kite alone among other names: its first word is its own
      feed([], [PR('paper-kite', 0), PR('tide-api', 1)]);
      await until(() => B().list.length === 2);
      const alone = Object.fromEntries(B().list.map(b => [b.id, b.tag]));
      return { one, alone, names: P.map(p => p[1].toUpperCase()) };""")
    tags, names = r["one"], set(r["names"])
    assert all(tags.values()), tags  # a short form fits each one
    assert len(set(tags.values())) == len(tags), tags  # and no two read the same
    full = dict(zip(["kite", "paper-kite", "paper-boat", "tidebook-indexer", "tidebook-web",
                     "kite-game"], r["names"], strict=True))
    for pid, t in tags.items():
        assert t not in names - {full[pid]}, (pid, t)  # never another project's name
    assert tags["kite"] == "KITE"
    assert tags["paper-kite"] not in ("PAPER", "KITE")
    assert tags["paper-boat"] != "PAPER" and tags["tidebook-web"] != "TIDEBOOK"
    assert r["alone"]["paper-kite"] == "PAPER"


def test_berths_in_use_never_share_a_paint(tmp_path):
    # review L10 #5: a bare hash into 8 paints: two of six berths almost always shared one
    names = ", ".join(f"'{n}'" for n in NAMES[:6])
    r = probe(tmp_path, f"""
      const names = [{names}];
      feed([], names); await until(() => B() && B().list.length === 6);
      const a = Object.fromEntries(B().list.map(b => [b.id, b.paint]));
      // one leaves (past its grace), a newcomer takes its berth: the others keep their paint
      feed([], names.slice(1)); await until(() => B().list.length === 5);
      BERTHS.S.seen.set(names[0], performance.now() - BERTHS.GRACE_MS - 1);
      feed([], names.slice(1).concat(['fog-horn', 'reed-boat']));
      await until(() => B().list.length === 6);
      const b = Object.fromEntries(B().list.map(x => [x.id, x.paint]));
      const view = Object.fromEntries(BERTHS.view().berths.map(x => [x.id, x.paint]));
      return {{ a, b, view, hash: names.map(n => BERTHS.paintOf(n)) }};""")
    a, b = r["a"], r["b"]
    assert len(set(r["hash"])) < 6  # the plain hash would repeat a paint here
    assert len(set(a.values())) == 6 and len(set(b.values())) == 6
    assert all(b[k] == v for k, v in a.items() if k in b)  # stable while moored
    assert r["view"] == b  # the room's quay cells wear the same paint


def test_a_boat_shown_moored_never_launches_later(tmp_path):
    # review L10 #6: a project moored at page load, away past the grace, came back still `new`
    r = probe(tmp_path, """
      Object.assign(BERTHS.TUNE, { slide: 300, splash: 150, glide: 150 });
      const pk = [PR('paper-kite', 0, true)];
      feed([A('a1', 'paper-kite')], pk); await until(() => ids() && ids().length === 1);
      // its agent works at the library for longer than the grace: no berth, then forgotten
      feed([A('a1', 'paper-kite', 'working', { plot: 'library' })], pk);
      await until(() => ids().length === 0);
      BERTHS.S.seen.set('paper-kite', performance.now() - BERTHS.GRACE_MS - 1);
      feed([A('a1', 'paper-kite', 'working', { plot: 'library' })], pk.slice());
      await wait(100);
      // back at the Harbour, the payload still says new
      feed([A('a1', 'paper-kite')], pk.slice()); await until(() => ids().length === 1);
      let seen = null;
      await until(() => { seen = seen || B().launch; return false; }, 800);
      return { launches: B().launches, seen };""", query=MOTION)
    assert r["launches"] == 0 and r["seen"] is None


def test_a_launch_that_waits_too_long_is_dropped_and_its_boat_waits_unseen(tmp_path):
    # review L10 #7: queued launches played minutes late, their boats drawn moored meanwhile
    r = probe(tmp_path, """
      Object.assign(BERTHS.TUNE, { slide: 500, splash: 200, glide: 60, wait: 400 });
      feed([A('a1', 'paper-kite')], ['paper-kite']); await until(() => ids() && ids().length === 1);
      feed([A('a1', 'paper-kite')], [PR('paper-kite', 0), PR('glass-kite', 1, true),
        PR('reed-boat', 2, true)]);
      await until(() => B().launch !== null);
      const during = B().list.find(b => b.id === 'reed-boat'), played = new Set();
      await until(() => { if (B().launch) played.add(B().launch.id); return false; }, 2600);
      // a launch queued while the Harbour room is open: by the time the town shows, too late
      document.querySelector('.hit[data-id="agents"]').click();
      await until(() => RS.id === 'agents' && RS.mode === 'room', 3000);
      feed([A('a1', 'paper-kite')], [PR('paper-kite', 0), PR('glass-kite', 1),
        PR('reed-boat', 2), PR('tern-sail', 3, true)]);
      BERTHS.view(); await wait(700);
      leaveRoom(); await until(() => !RS.id, 3000);
      await until(() => { if (B().launch) played.add(B().launch.id); return false; }, 600);
      return { during, played: [...played], launches: B().launches,
        after: B().list.map(b => [b.id, b.tag, b.queued]) };""", query=MOTION)
    d = r["during"]
    assert d["queued"] and d["tag"] == "" and not d["launching"]  # waiting its turn: not moored
    assert r["played"] == ["glass-kite"], r["played"]
    assert all(tag and not q for _, tag, q in r["after"]), r["after"]  # moored now, tags back


GREEN = """
[town]
title = "Green field"
latitude = 51.5
longitude = -0.1

[[building]]
id = "agents"
type = "harbour"
label = "Harbour"
  [[building.source]]
  kind = "static"
  state = "ok"
  rows = [{ id = "kite", name = "Paper Kite", state = "ok" }]
"""


def test_the_overflow_mooring_keeps_clear_of_the_sea_serpent(tmp_path):
    # review L10 #8: the tarp boat sat on the serpent's head and the "+N" showed the serpent's tip
    names = ", ".join(f"'{n}'" for n in NAMES)
    page = build_town(tmp_path, GREEN)
    r = run(page, HELP + f"""
      const names = [{names}];
      const sc = cvs.width / W;
      const grab = () => Array.from(main.getImageData(Math.round(4 * sc), Math.round(190 * sc),
        Math.round(83 * sc), Math.round(35 * sc)).data).join(',');
      feed([], []); await until(() => B() && SEEN.serpent); await wait(100);
      const bare = grab();
      feed(names.map((p, i) => A('w' + i, p)), names);
      await until(() => B().over.length === 2 && (SEEN.agents || []).length === 8);
      await wait(200);
      const o = BERTHS.OVER;
      return {{ same: grab() === bare, serpent: !!SEEN.serpent, o,
        tag: (tipAt(o + 2, 219) || {{ tip: [''] }}).tip[0],
        boat: (tipAt(o + 2, 211) || {{ tip: [''] }}).tip[0] }};""", "?live=0&t=300")
    assert r["serpent"]
    assert r["same"]  # the serpent (x 4..86) looks just as it does without the mooring
    assert r["tag"].startswith("2 MORE PROJECTS") and r["boat"].startswith("2 MORE PROJECTS")


def test_the_room_keeps_the_hulls_note_for_the_agent_with_the_quay(tmp_path):
    # review L10 #9: with the live quay the hull's pinned note went, and the caption row's hover
    # target stayed behind when the quay came in mid-visit
    r = probe(tmp_path, """
      const d = JSON.parse(JSON.stringify(D));
      Object.assign(d.rooms.agents.rows[0],
        { agent_items: [{ text: 'Tidy the docs' }], agent_n: 1 });
      lamptownData(d);
      document.querySelector('.hit[data-id="agents"]').click();
      await until(() => RS.id === 'agents' && RS.mode === 'room', 4000); await wait(200);
      const lead = () => RS.room.targets().filter(t => t.id.startsWith('lead:')).map(t => t.box);
      const sc = cvs.width / W, px = b => Array.from(main.getImageData(Math.round((b[0] + 1) * sc),
        Math.round((b[1] + 2) * sc), 1, 1).data).join(',');
      const before = { lead: lead(), seen: RS.room.seen().bays[0].leadNote,
        quay: !!RS.room.seen().quay };
      const note = before.lead[0], pxBefore = px(note);
      feed([A('a1', 'kite')], ['kite']);   // the Watcher starts while the room is open
      await until(() => RS.room.seen().quay, 3000); await wait(200);
      return { before, after: { lead: lead(), seen: RS.room.seen().bays[0].leadNote },
        note, same: px(note) === pxBefore };""")
    b, a = r["before"], r["after"]
    assert not b["quay"] and len(b["lead"]) == 2 and b["seen"]  # the note and the caption row
    assert a["seen"] and a["lead"] == [r["note"]]  # the note stays; the row (and its target) went
    assert r["same"]  # and it is still drawn


def test_a_project_listed_twice_is_drawn_once(tmp_path):
    # review L10 #10
    r = probe(tmp_path, """
      feed([A('d1', 'dup'), A('d2', 'dup')], [PR('dup', 0), PR('dup', 1), PR('other', 2)]);
      await until(() => B() && B().list.length >= 2); await wait(100);
      document.querySelector('.hit[data-id="agents"]').click();
      await until(() => RS.id === 'agents' && RS.room.seen().quay, 4000);
      return { list: B().list.map(b => [b.id, b.agents]),
        targets: RS.room.targets().filter(t => t.id === 'berth:dup').length };""")
    assert r["list"] == [["dup", ["d1", "d2"]], ["other", []]]
    assert r["targets"] == 1


def test_the_room_draws_lamplighters_with_live_js_own_painter(tmp_path):
    # review L10 #11: berths.js kept a copy of the painter that could drift
    r = probe(tmp_path, """
      let calls = 0; const own = LAMPLIGHTERS.paintFigure;
      LAMPLIGHTERS.paintFigure = (...a) => { calls++; return own(...a); };
      feed([A('a1', 'paper-kite', 'working', { tool: 'wren-cli' }), A('a2', 'paper-kite', 'error')],
        ['paper-kite']);
      await until(() => B() && B().list.length === 1);
      document.querySelector('.hit[data-id="agents"]').click();
      await until(() => RS.id === 'agents' && RS.room.seen().quay, 4000); await wait(200);
      const tools = ['codex', 'claude-code', 'wren-cli', 'Some Tool', 'constructor'];
      return { calls, same: tools.every(t => BERTHS.coatOf(t) === LAMPLIGHTERS.coatOf(t)),
        fallback: tools.every(t => BERTHS.ownCoat(t) === LAMPLIGHTERS.coatOf(t)) };""")
    assert r["calls"] > 0 and r["same"] and r["fallback"]
