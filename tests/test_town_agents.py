"""The Lamplighters (web/live.js) in a real browser (headless Chrome, as in ``test_town.py``).

One figure per live agent, drawn from the live payload (``lamptown-live/1``): its lantern carries
the state, it walks the town's footpaths to the building of its plot, a state change that comes
mid-walk waits for the walk to end, subagents follow as smaller helpers, at most 12 figures plus a
"+N" tag. The probes feed payloads with ``lamptownLive`` (as the app pushes them; ``?live=0``: no
polling) and read what the last frame drew from ``SEEN.agents``. Every name here is made up.

Chrome runs each probe with a 6 s virtual-time budget (``test_town.run``): the waits in one probe
(300 ms start + every cap) stay under about 4.5 s, so a failure reads as an assertion, never as
"the probe wrote nothing".
"""

from __future__ import annotations

from importlib import resources

from _browser import browser
from test_town import FICTIONAL, build_town, run

pytestmark = browser

STILL = "?live=0&t=300"  # a still scene: figures stand where their state puts them, no walking
MOTION = "?live=0&motion=1"  # a moving scene: figures walk

# JS helpers for every probe: an agent, a fresh payload (a heartbeat now), waits with a cap
HELP = """
const until = async (f, cap) => {
  for (let w = 0; w < (cap || 1500) && !f(); w += 50) await wait(50);
  return !!f();
};
const ago = m => new Date(Date.now() - m * 6e4).toISOString();
const A = (id, state, plot, o) => Object.assign({
  id, tool: 'codex', title: 'Agent ' + id, project: 'paper-kite', plot, state, since: ago(1),
  parent: null, subagents: 0, files: 0, commits: 0, pushed: false, ci: null, private: false,
}, o || {});
const feed = (agents, more) => lamptownLive({
  spec: 'lamptown-live/1', at: new Date().toISOString(), rev: '',
  need: agents.filter(a => a.state === 'waiting').length, more: more || 0, agents, projects: [],
});
const fig = id => (SEEN.agents || []).find(a => a.id === id) || null;
const P = LAMPLIGHTERS.PLACE;
"""


def probe(tmp_path, body: str, query: str = STILL) -> dict:
    page = build_town(tmp_path, FICTIONAL)
    return run(page, HELP + body, query)


def test_the_page_inlines_live_js_once(tmp_path):
    town = build_town(tmp_path, FICTIONAL).read_text(encoding="utf-8")
    assert town.count('<script data-src="live.js">') == 1
    assert town.index('data-src="town.js"') < town.index('data-src="live.js"')


def test_each_state_reads_as_designed(tmp_path):
    r = probe(tmp_path, """
      feed([A('w1', 'working', 'boathouse', { files: 230 }), A('q1', 'waiting', 'gate'),
            A('e1', 'error', 'lab'), A('z1', 'quiet', 'library'), A('d1', 'ok', 'tower')]);
      await until(() => (SEEN.agents || []).length === 4);
      return { agents: SEEN.agents, hooks: SEEN.agentHooks, more: SEEN.agentMore, P };""")
    a = {f["id"]: f for f in r["agents"]}
    P = r["P"]
    assert set(a) == {"w1", "q1", "e1", "z1"}  # done: no figure
    w, q, e, z = a["w1"], a["q1"], a["e1"], a["z1"]
    assert (w["state"], w["mode"], w["lantern"]) == ("working", "stand", "working")
    assert [w["x"], w["y"]] == P["boathouse"]["stand"][0] and w["files"] == 230
    assert (q["state"], q["mode"], q["chair"]) == ("waiting", "sit", True)
    assert [q["x"], q["y"]] == P["gate"]["seats"][0]  # the Watcher's chair by the gate's door
    assert (e["state"], e["mode"], e["lantern"]) == ("error", "stand", "error")
    assert [e["x"], e["y"]] == P["lab"]["out"][0]  # stepped out onto the path
    assert (z["state"], z["mode"], z["lantern"], z["mark"]) == ("quiet", "stand", "dark", "?")
    assert r["hooks"] == {"tower": ["d1"]}  # the done lantern hangs by the tower door
    assert r["more"] == 0


def test_gone_quiet_never_reads_as_done(tmp_path):
    r = probe(tmp_path, """
      feed([A('z1', 'quiet', 'boathouse', { since: ago(40) }), A('d1', 'ok', 'boathouse')]);
      await until(() => (SEEN.agents || []).length === 1);
      return { agents: SEEN.agents, hooks: SEEN.agentHooks };""")
    (z,) = r["agents"]
    assert z["id"] == "z1" and z["mark"] == "?" and z["lantern"] == "dark" and z["mode"] == "stand"
    assert r["hooks"] == {"boathouse": ["d1"]}  # done: a lantern on the hook and no figure
    assert "z1" not in r["hooks"]["boathouse"]


def test_no_figures_without_agents_or_without_a_live_payload(tmp_path):
    r = probe(tmp_path, """
      await until(() => Array.isArray(SEEN.agents));
      const before = SEEN.agents.slice();
      feed([A('w0', 'working', 'gate'), A('d0', 'ok', 'lab')]);
      const first = await until(() => !!fig('w0') && !!SEEN.agentHooks.lab);
      // the agents left: a frame must really draw nobody (not just keep the start values)
      feed([]);
      const cleared = await until(() => SEEN.agents.length === 0 && !SEEN.agentHooks.lab);
      const none = { agents: SEEN.agents, more: SEEN.agentMore, hooks: SEEN.agentHooks };
      feed([A('w1', 'working', 'boathouse')]);
      const shown = await until(() => !!fig('w1'));
      // a payload from a Watcher that stopped a minute ago: no heartbeat, so nobody is shown
      lamptownLive({ spec: 'lamptown-live/1', at: ago(1), rev: '', need: 0, more: 0, projects: [],
        agents: [A('w1', 'working', 'boathouse')] });
      const gone = await until(() => SEEN.agents.length === 0);
      return { before, first, cleared, none, shown, gone, on: LIVE.on };""")
    assert r["before"] == []
    assert r["first"] is True and r["cleared"] is True
    assert r["none"] == {"agents": [], "more": 0, "hooks": {}}
    assert r["shown"] is True and r["gone"] is True and r["on"] is False


def test_at_most_twelve_figures_plus_a_count(tmp_path):
    r = probe(tmp_path, """
      const many = [], plots = ['boathouse', 'gate', 'lab', 'library', 'workshop'];
      for (let i = 0; i < 17; i++)
        many.push(A('w' + i, 'working', plots[i % 5], { since: ago(30 - i) }));
      for (let i = 0; i < 3; i++) many.push(A('q' + i, 'waiting', 'boathouse', { since: ago(2) }));
      feed(many, 5);
      await until(() => (SEEN.agents || []).length === 12);
      const hidden = LAMPLIGHTERS.S.hidden.map(a => a.plot);
      return { ids: SEEN.agents.map(a => a.id), more: SEEN.agentMore, at: SEEN.agentMoreAt, hidden,
        tags: TIPS.filter(t => /MORE LAMPLIGHTER/.test(t.tip[0])), fountain: tipAt(170, 100) };""")
    assert len(r["ids"]) == 12
    assert {"q0", "q1", "q2"} <= set(r["ids"])  # the ones waiting for you are never left out
    assert r["more"] == 20 - 12 + 5  # the payload's own `more` counts too
    # one tag per building, at the end of its own row (no lone "+N" by the fountain)
    want = {k: r["hidden"].count(k) for k in set(r["hidden"])}
    order = ["tower", "library", "gate", "lab", "workshop", "boathouse"]
    # ties: the first in the town's order
    busiest = max(want, key=lambda k: (want[k], -order.index(k)))
    want[busiest] += 5  # the payload's own `more` rides on the busiest building's tag
    assert r["at"] == want and sum(r["at"].values()) == 13
    assert len(r["tags"]) == len(want)
    assert all(" MORE LAMPLIGHTER" in t["tip"][0] and " AT THE " in t["tip"][0] for t in r["tags"])
    assert sum("THE WATCHER DID NOT LIST" in " ".join(t["tip"]) for t in r["tags"]) == 1
    assert r["fountain"] is None or "LAMPLIGHTER" not in r["fountain"]["tip"][0]


def test_subagents_follow_their_parent_as_helpers(tmp_path):
    r = probe(tmp_path, """
      feed([A('p1', 'working', 'boathouse', { title: 'Fix the sails', subagents: 3 }),
            A('s1', 'working', 'boathouse', { parent: 'p1', since: ago(3) }),
            A('s2', 'waiting', 'boathouse', { parent: 'p1', since: ago(2) }),
            A('s3', 'working', 'boathouse', { parent: 'p1', since: ago(1) })]);
      await until(() => (SEEN.agents || []).length === 3);
      const p = fig('p1'), box = LAMPLIGHTERS.S.hits.find(h => h.id === 'p1');
      return { agents: SEEN.agents, more: SEEN.agentMore, card: box && box.lines };""")
    a = {f["id"]: f for f in r["agents"]}
    p = a["p1"]
    assert p["small"] is False and p["helpers"] == ["s2", "s1"]  # waiting first, then the oldest
    for sid in ("s1", "s2"):
        h = a[sid]
        assert h["small"] is True and h["parent"] == "p1"
        assert abs(h["x"] - p["x"]) <= 20 and abs(h["y"] - p["y"]) <= 3
    assert a["s2"]["state"] == "waiting"  # a helper's lantern is its own
    assert "s3" not in a and r["more"] == 0  # the third is on the parent's card, not a "+N"
    assert r["card"][-1] == "3 HELPERS, 1 WAITING"


def test_a_state_change_mid_walk_waits_until_the_walk_ends(tmp_path):
    r = probe(tmp_path, """
      LAMPLIGHTERS.speed = 120;
      const log = [];
      TOWN_LAYERS.lights.push(() => {
        const f = fig('w1');
        if (f) log.push([f.mode, f.state, f.want, f.x, f.y]);
      });
      feed([]);   // the first payload places everyone at once; later ones walk
      await wait(100);
      feed([A('w1', 'working', 'boathouse')]);
      await until(() => fig('w1') && fig('w1').mode === 'walk', 800);
      await wait(250);
      const atChange = fig('w1');
      feed([A('w1', 'waiting', 'boathouse')]);
      // SEEN is what the last frame drew: wait for a frame that has seen the change
      await until(() => { const f = fig('w1'); return f && (f.want || f.state) === 'waiting'; },
                  800);
      const after = fig('w1');
      const sat = await until(() => fig('w1') && fig('w1').mode === 'sit', 2000);
      return { atChange, after, sat, log, end: fig('w1'), P };""", MOTION)
    stand, chair = r["P"]["boathouse"]["stand"][0], r["P"]["boathouse"]["seats"][0]
    at, after, end = r["atChange"], r["after"], r["end"]
    assert at["mode"] == "walk" and at["state"] == "working"
    # the new state is queued, the figure keeps walking (never frozen in the road)
    assert (after["mode"], after["state"], after["want"]) == ("walk", "working", "waiting")
    assert r["sat"] and end["state"] == "waiting" and [end["x"], end["y"]] == chair
    log = r["log"]
    first = next(i for i, e in enumerate(log) if e[1] == "waiting")
    assert [log[first][3], log[first][4]] == stand  # it changed only once it stood at its spot
    queued = [e for e in log[:first] if e[2] == "waiting"]
    assert len({(e[3], e[4]) for e in queued}) >= 3  # it kept moving while the change waited
    assert all(e[1] == "working" for e in log[:first])


def test_the_watcher_s_chair_slumps_the_longer_it_waits(tmp_path):
    r = probe(tmp_path, """
      const out = [];
      for (const m of [2, 15, 45]) {
        feed([A('q1', 'waiting', 'gate', { since: ago(m) })]);
        await until(() => fig('q1') && fig('q1').slump === (m < 10 ? 0 : m < 30 ? 1 : 2));
        out.push(fig('q1'));
      }
      return { out };""")
    assert [f["slump"] for f in r["out"]] == [0, 1, 2]
    assert all(f["mode"] == "sit" and f["chair"] for f in r["out"])
    assert len({(f["x"], f["y"]) for f in r["out"]}) == 1  # slumping is not walking


def test_calm_when_nothing_happens(tmp_path):
    r = probe(tmp_path, """
      const two = () => [A('w1', 'working', 'boathouse'), A('q1', 'waiting', 'lab')];
      feed(two());
      await until(() => (SEEN.agents || []).length === 2);
      const a = JSON.stringify(SEEN.agents);
      for (let i = 0; i < 4; i++) { await wait(250); feed(two()); }
      await wait(250);
      return { same: JSON.stringify(SEEN.agents) === a, modes: SEEN.agents.map(f => f.mode) };""",
              MOTION)
    assert r["same"] is True  # heartbeats with no change move nobody
    assert sorted(r["modes"]) == ["sit", "stand"]


def test_a_click_pins_a_names_only_card_and_no_toast(tmp_path):
    r = probe(tmp_path, """
      const toasts = []; toast = m => toasts.push(m);   // alert code must never toast
      const counts = { title: 'Approve the plan', files: 14, commits: 2, pushed: true,
                       ci: 'green' };
      feed([A('w1', 'waiting', 'boathouse', counts),
            A('e1', 'error', 'gate', { tool: 'wren-cli' })]);
      await until(() => (SEEN.agents || []).length === 2);
      const f = fig('w1'), c = document.getElementById('c').getBoundingClientRect();
      const cx = c.left + (f.x + 0.5) * c.width / 400;
      const cy = c.top + (f.y - 5 + 0.5) * c.height / 225;
      const click = new MouseEvent('click', { bubbles: true, clientX: cx, clientY: cy });
      document.elementFromPoint(cx, cy).dispatchEvent(click);
      await until(() => SEEN.agentCard);
      const panel = document.getElementById('panel').hidden;
      return { card: SEEN.agentCard, room: RS.id, panel, toasts,
        coats: SEEN.agents.map(a => [a.tool, a.coat]) };""")
    assert r["card"][0] == "Approve the plan"
    assert r["card"][1] == "PAPER-KITE / CODEX"
    assert r["card"][2].startswith("WAITING FOR YOU")
    assert r["card"][3] == "14 FILES, 2 COMMITS, PUSHED, CI GREEN"
    assert r["room"] is None and r["panel"] is True  # the click was the figure's, not the room's
    assert r["toasts"] == []
    coats = dict(r["coats"])
    assert coats["codex"] == "#3d7468" and coats["wren-cli"].startswith("#")


def test_live_js_never_calls_toast():
    src = resources.files("lamptown").joinpath("web", "live.js").read_text(encoding="utf-8")
    assert "toast(" not in src


# ----------------------------------------------------------------------------- review L7 fixes
def test_a_plot_or_tool_named_like_an_object_key_costs_nothing(tmp_path):
    # finding 1: "constructor" / "__proto__" passed `PLACE[plot]` and blanked every figure
    r = probe(tmp_path, """
      feed([A('ok1', 'working', 'gate'),
            A('bad', 'working', 'constructor', { tool: 'constructor' }),
            A('bad2', 'waiting', '__proto__', { tool: '__proto__' }),
            A('bad3', 'error', 'toString'),
            A('d1', 'ok', 'hasOwnProperty')]);
      await until(() => (SEEN.agents || []).length === 4);
      return { agents: SEEN.agents, hooks: SEEN.agentHooks, err: SEEN.agentsError || null };""")
    assert r["err"] is None
    a = {f["id"]: f for f in r["agents"]}
    assert set(a) == {"ok1", "bad", "bad2", "bad3"}
    assert a["ok1"]["plot"] == "gate"
    # an unknown plot: the Harbour
    assert all(a[k]["plot"] == "boathouse" for k in ("bad", "bad2", "bad3"))
    assert all(isinstance(f["coat"], str) and f["coat"].startswith("#") for f in r["agents"])
    assert r["hooks"] == {"boathouse": ["d1"]}


def test_a_waiting_or_failed_helper_always_walks_with_its_parent(tmp_path):
    # finding 2: only the two oldest helpers were drawn, so a waiting third helper was nowhere
    r = probe(tmp_path, """
      feed([A('p1', 'working', 'workshop'),
            A('s1', 'working', 'workshop', { parent: 'p1', since: ago(9) }),
            A('s2', 'working', 'workshop', { parent: 'p1', since: ago(8) }),
            A('s3', 'waiting', 'workshop', { parent: 'p1', since: ago(1) }),
            A('p2', 'working', 'lab'),
            A('t1', 'working', 'lab', { parent: 'p2', since: ago(9) }),
            A('t2', 'working', 'lab', { parent: 'p2', since: ago(8) }),
            A('t3', 'working', 'lab', { parent: 'p2', since: ago(7) }),
            A('t4', 'waiting', 'lab', { parent: 'p2', since: ago(2) }),
            A('t5', 'error', 'lab', { parent: 'p2', since: ago(1) }),
            A('t6', 'quiet', 'lab', { parent: 'p2', since: ago(1) })]);
      await until(() => fig('s3') && fig('t5'));
      const card = id => (LAMPLIGHTERS.S.hits.find(h => h.id === id) || {}).lines;
      return { agents: SEEN.agents, more: SEEN.agentMore, c1: card('p1'), c2: card('p2') };""")
    a = {f["id"]: f for f in r["agents"]}
    assert a["p1"]["helpers"] == ["s3", "s1"]  # the waiting one first, then the oldest
    assert a["s3"]["small"] and a["s3"]["state"] == "waiting" and "s2" not in a
    # beyond two, only working helpers stay on the parent's card: the waiting, failed and quiet
    # ones walk too
    assert a["p2"]["helpers"] == ["t4", "t5", "t6"]
    assert not {"t1", "t2", "t3"} & set(a)
    assert r["more"] == 0
    assert r["c1"][-1] == "3 HELPERS, 1 WAITING"
    assert r["c2"][-1] == "6 HELPERS, 1 WAITING, 1 FAILED"


def test_grandchildren_follow_the_root_and_a_parent_cycle_is_no_parent(tmp_path):
    # finding 3: grandchildren and cycles fell into the "+N"
    r = probe(tmp_path, """
      feed([A('p1', 'working', 'gate'), A('s1', 'working', 'gate', { parent: 'p1' }),
            A('g1', 'waiting', 'gate', { parent: 's1' }),
            A('c1', 'working', 'library', { parent: 'c2' }),
            A('c2', 'working', 'library', { parent: 'c1' })]);
      await until(() => fig('g1') && fig('c1') && fig('c2'));
      return { agents: SEEN.agents, more: SEEN.agentMore };""")
    a = {f["id"]: f for f in r["agents"]}
    assert set(a["p1"]["helpers"]) == {"s1", "g1"} and a["g1"]["parent"] == "p1"
    assert not a["c1"]["small"] and not a["c2"]["small"]
    assert r["more"] == 0


def test_two_figures_never_share_a_spot_after_failed_goes_quiet(tmp_path):
    # finding 4: going quiet freed the "out" spot it still stood on
    r = probe(tmp_path, """
      feed([A('e1', 'error', 'lab')]);
      await until(() => fig('e1'));
      feed([A('e1', 'quiet', 'lab')]);
      await until(() => fig('e1') && fig('e1').state === 'quiet');
      const quiet = fig('e1');
      feed([A('e1', 'quiet', 'lab'), A('e2', 'error', 'lab')]);
      await until(() => fig('e2'));
      return { quiet, e1: fig('e1'), e2: fig('e2'), P };""")
    q, e1, e2 = r["quiet"], r["e1"], r["e2"]
    assert [q["x"], q["y"]] == r["P"]["lab"]["out"][0]  # quiet stops where it is
    assert [e1["x"], e1["y"]] == [q["x"], q["y"]]
    assert [e2["x"], e2["y"]] != [e1["x"], e1["y"]]


SPOTS_APART = """
  // side by side: 6 px; one behind another: 12 px, or 7 px and a step to the side
  // (its face and lantern show)
  const apart = (p, q) => {
    const dx = Math.abs(p[0] - q[0]), dy = Math.abs(p[1] - q[1]);
    return dx >= 6 || dy >= 12 || (dy >= 7 && dx >= 3);
  };
  const bad = [];
  for (const k in P) {
    const spots = [];
    // (the Harbour's working figures stand at their project's berths, L10: its row keeps 6)
    for (const [list, n] of [['stand', k === 'boathouse' ? 6 : 8], ['seats', 6], ['out', 4]])
      for (let i = 0; i < n; i++) spots.push([k, list, i, LAMPLIGHTERS.spotIn(P[k][list], i)]);
    for (let i = 0; i < spots.length; i++) {
      const s = spots[i][3];
      if (!(s[0] >= 4 && s[0] <= 396 && s[1] >= 14 && s[1] <= 198)) bad.push(['off', spots[i]]);
      for (let j = i + 1; j < spots.length; j++)
        if (!apart(s, spots[j][3])) bad.push([spots[i], spots[j]]);
    }
  }
"""


def test_every_building_has_room_for_six_waiting_eight_working_four_failed(tmp_path):
    # finding 5: the 4th seat overlapped stool 1; the 7th working spot overlapped stand 4
    r = probe(tmp_path, SPOTS_APART + "return { bad };")
    assert r["bad"] == []


def test_four_waiting_at_one_building_sit_apart(tmp_path):
    r = probe(tmp_path, """
      const four = [40, 14, 6, 2].map((m, i) => A('q' + i, 'waiting', 'gate', { since: ago(m) }));
      feed(four.concat([A('w1', 'working', 'gate')]));
      await until(() => (SEEN.agents || []).filter(f => f.mode === 'sit').length === 4);
      return { agents: SEEN.agents };""")
    sit = [f for f in r["agents"] if f["mode"] == "sit"]
    assert len(sit) == 4 and sorted(f["seat"] for f in sit) == [0, 1, 2, 3]
    pts = [(f["x"], f["y"]) for f in r["agents"]]
    for i, p in enumerate(pts):
        for q in pts[i + 1:]:
            dx, dy = abs(p[0] - q[0]), abs(p[1] - q[1])
            assert dx >= 6 or dy >= 12 or (dy >= 7 and dx >= 3), (p, q)


def test_walkers_to_the_hook_never_keep_a_waiting_agent_off_the_map(tmp_path):
    # finding 6: twelve figures walking to their hooks took every slot from a new waiting agent
    r = probe(tmp_path, """
      LAMPLIGHTERS.speed = 2;   // the walks to the hooks last the whole probe
      const plots = ['gate', 'lab', 'library', 'workshop', 'tower', 'boathouse'];
      const w = [...Array(12).keys()].map(i => A('w' + i, 'working', plots[i % 6]));
      feed(w);
      await until(() => (SEEN.agents || []).length === 12);
      feed(w.map(a => Object.assign({}, a, { state: 'ok' })).concat([A('q1', 'waiting', 'gate'),
        A('e1', 'error', 'lab'), A('z1', 'working', 'tower')]));
      const shown = await until(() => fig('q1') && fig('e1'));
      return { shown, homing: SEEN.agents.filter(f => f.state === 'ok' && f.mode === 'walk').length,
               z1: !!fig('z1'), more: SEEN.agentMore };""", MOTION)
    assert r["shown"] is True and r["homing"] >= 10  # the walkers are still on their way
    assert r["z1"] is False and r["more"] == 1  # a working agent does give way to them


def test_a_figure_pushed_out_by_the_cap_steps_out_calmly(tmp_path):
    # finding 7: a working figure pushed out by a new waiting agent vanished on the spot
    r = probe(tmp_path, """
      LAMPLIGHTERS.speed = 120;
      const w = [...Array(12).keys()].map(i =>
        A('w' + i, 'working', i % 2 ? 'gate' : 'library', { since: ago(30 - i) }));
      feed(w);
      await until(() => (SEEN.agents || []).length === 12);
      const at = fig('w11');
      feed(w.concat([A('q9', 'waiting', 'gate')]));
      await until(() => fig('q9'));
      const leaving = fig('w11');
      const gone = await until(() => !fig('w11'), 2500);
      return { at, leaving, gone, more: SEEN.agentMore, moreAt: SEEN.agentMoreAt, P };""", MOTION)
    lv = r["leaving"]
    # still there, on its way out
    assert lv is not None and lv["leaving"] is True and lv["mode"] == "walk"
    assert r["gone"] is True and r["more"] == 1 and r["moreAt"] == {"gate": 1}


def test_a_plot_change_mid_walk_goes_straight_to_the_new_spot(tmp_path):
    # finding 8: it walked to the old plot, then to the new plot's working spot, then to the chair
    r = probe(tmp_path, """
      LAMPLIGHTERS.speed = 150;
      const log = [];
      TOWN_LAYERS.lights.push(() => {
        const f = fig('w1'); if (f) log.push([f.mode, f.state, f.plot, f.x, f.y]); });
      feed([]);
      await wait(100);
      feed([A('w1', 'working', 'boathouse')]);
      await until(() => fig('w1') && fig('w1').mode === 'walk', 800);
      const n0 = log.length;
      feed([A('w1', 'waiting', 'gate')]);
      const sat = await until(() => fig('w1') && fig('w1').mode === 'sit', 2500);
      return { sat, log: log.slice(n0), end: fig('w1'), P };""", MOTION)
    P, log = r["P"], r["log"]
    assert r["sat"] is True and [r["end"]["x"], r["end"]["y"]] == P["gate"]["seats"][0]
    assert all(e[3:] != P["boathouse"]["stand"][0] for e in log)  # never went on to the Harbour
    assert not any(e[0] == "stand" for e in log)  # no stop at a working spot on the way
    first = next(i for i, e in enumerate(log) if e[1] == "waiting")
    assert all(e[1] == "working" for e in log[:first])  # the lantern changed only once it got there


def test_an_agent_that_resumes_on_its_way_to_the_hook_turns_back(tmp_path):
    # finding 9: it hung its lantern, vanished, then a new figure set out from the fountain
    r = probe(tmp_path, """
      LAMPLIGHTERS.speed = 12;
      const log = [];
      TOWN_LAYERS.lights.push(() => {
        const f = fig('w1'); log.push(f ? [f.x, f.y, f.state] : null); });
      feed([A('w1', 'working', 'lab')]);
      await until(() => fig('w1'));
      feed([A('w1', 'ok', 'lab')]);
      await until(() => fig('w1') && fig('w1').state === 'ok' && fig('w1').mode === 'walk', 800);
      const n0 = log.length;
      feed([A('w1', 'working', 'lab')]);
      const back = await until(() => fig('w1') && fig('w1').mode === 'stand'
        && fig('w1').state === 'working', 2500);
      return { back, log: log.slice(n0), end: fig('w1'), P };""", MOTION)
    assert r["back"] is True
    assert all(e is not None for e in r["log"])  # never gone
    assert all(e[1] == 148 for e in r["log"])  # it stayed in its row: no walk from the fountain
    assert [r["end"]["x"], r["end"]["y"]] == r["P"]["lab"]["stand"][0]


def test_the_pinned_card_is_drawn_over_everything(tmp_path):
    # finding 10: the pinned card was drawn under the fireflies and the corner box
    r = probe(tmp_path, """
      feed([A('w1', 'working', 'workshop')]);
      await until(() => fig('w1'));
      LAMPLIGHTERS.S.pin = 'w1'; kick();
      await until(() => SEEN.agentCard);
      const order = [], oc = corner, od = drawTip, ob = beltTipsAll;
      corner = () => { order.push('corner'); oc(); };
      beltTipsAll = () => { order.push('belts'); ob(); };
      drawTip = (...a) => { order.push('card'); od(...a); };
      kick(); await wait(200);
      return { order, card: SEEN.agentCard };""")
    o = r["order"]
    assert r["card"] and "card" in o and "corner" in o
    assert o.index("card") > o.index("corner") and o.index("card") > o.index("belts")


def test_huge_counts_print_short_and_never_negative(tmp_path):
    # finding 11: files: 1e12 printed "-727379968 FILES" and lost its cart
    r = probe(tmp_path, """
      feed([A('a1', 'working', 'gate', { files: 1e12, commits: -4 }),
            A('a2', 'working', 'lab', { files: 12345, commits: 2 }),
            A('a3', 'working', 'library', { files: 'lots' })]);
      await until(() => (SEEN.agents || []).length === 3);
      const card = id => LAMPLIGHTERS.S.hits.find(h => h.id === id).lines;
      return { c1: card('a1'), c2: card('a2'), c3: card('a3'), a1: fig('a1'),
               marks: LAMPLIGHTERS.S.marks.map(m => [m.id, m.files, !!m.cart]) };""")
    assert r["c1"][3] == "999M FILES"
    assert r["c2"][3] == "12.3K FILES, 2 COMMITS"
    assert len(r["c3"]) == 3  # no counts line
    assert r["a1"]["files"] == 999_999_999
    assert ["a1", 999_999_999, True] in r["marks"]  # the cart is still drawn


def test_the_harbour_bubble_rests_while_live_is_on(tmp_path):
    # visual call (b): with live on, the figures carry "needs you"; the Harbour keeps its amber lamp
    r = probe(tmp_path, """
      const calls = [], ob = bubble;
      bubble = (x, y, ...rest) => { calls.push([x, y]); return ob(x, y, ...rest); };
      const lampAt = plot => calls.some(c => c[0] === PLOT[plot].lamp[0]
        && c[1] === PLOT[plot].lamp[1]);
      await wait(150);
      const off = { harbour: lampAt('boathouse'), lab: lampAt('lab') };
      feed([A('q1', 'waiting', 'boathouse')]);
      await until(() => fig('q1'));
      calls.length = 0; await wait(150);
      return { off, on: { harbour: lampAt('boathouse'), lab: lampAt('lab') }, lamps: SEEN.lamps,
               live: LIVE.on };""",
              MOTION)
    assert r["off"] == {"harbour": True, "lab": True}  # live off: the town's own bubble
    assert r["live"] is True and r["on"] == {"harbour": False, "lab": True}
    assert r["lamps"]["boathouse"] == "amber"  # the lamp stays
