"""0.2.1 review fixes, batch D (the web rooms and the canvas JS), in a real browser (headless
Chrome, as in ``test_town.py``) plus a few source checks.

- D1: an idle room is still. Motion means a real event, so a room with nothing at work draws
  the same frame at two different times (and a room at work still moves).
- D2: the town's green "running fine" lamp is steady, as its words say.
- D3: the shelf's padlock tip names the folder by its leaf name, never a path.
- D4: the room chrome every room drew for itself (door sign, cursor, sample badge, error guard)
  is in the kit (``K.doorSign`` / ``K.cursor`` / ``K.sampleBadge`` / ``K.guard``) and the
  lighthouse walks live.js's footpath graph instead of building its own.
- D5: a lamplighter's click box holds its lantern whichever way it faces; an agent with no
  ``since`` is no news (no bell on load); the berth tags are not rebuilt on every frame.

Every name and path here is made up.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from importlib import resources
from pathlib import Path

import pytest

from _browser import browser
from lamptown import build as B
from lamptown import collectors as C
from lamptown.config import parse_config
from test_town import FICTIONAL, build_town, run

NOW = dt.datetime(2026, 3, 14, 22, 0, tzinfo=dt.UTC)

# one building of every room type, with made-up data (the demo replaces it with each room's sample)
ROOMS_TOML = """
[town]
title = "Kite field"

[[building]]
id = "agents"
type = "harbour"
label = "Wren and Otter"
  [[building.source]]
  kind = "static"
  state = "ok"
  rows = [{ id = "kite", name = "Paper Kite", state = "ok" }]

[[building]]
id = "nightly"
type = "jobs"
label = "Night jobs"
  [[building.source]]
  kind = "static"
  state = "ok"
  rows = [{ name = "Tidebook index", state = "ok", next = "2026-03-15T02:00:00+00:00" }]

[[building]]
id = "gate"
type = "ledger"
label = "Vetting gate"
  [[building.source]]
  kind = "static"
  state = "ok"

[[building]]
id = "skills"
type = "shelf"
label = "Skill shelf"
  [[building.source]]
  kind = "static"
  state = "ok"

[[building]]
id = "notes"
type = "vault"
label = "Notebook"
  [[building.source]]
  kind = "static"
  state = "ok"

[[building]]
id = "lab"
type = "contest"
label = "Idea lab"
  [[building.source]]
  kind = "static"
  state = "ok"
"""

# hash(): FNV-1a of the whole canvas at native pixels; frames(ts): warm up (eased things settle),
# then the hash of one frame drawn at each t
FRAMES = """
const CV = () => document.getElementById('c');
const hash = () => {
  const d = CV().getContext('2d').getImageData(0, 0, CV().width, CV().height).data;
  let h = 0x811c9dc5;
  for (let i = 0; i < d.length; i++) { h ^= d[i]; h = Math.imul(h, 16777619); }
  return (h >>> 0).toString(16); };
const frames = ts => { for (let i = 0; i < 24; i++) render(280 + i * 0.75, false);
  return ts.map(t => { render(t, false); return hash(); }); };
"""

TIMES = "[300, 301.7, 303.35, 307.9]"


def rooms_town(tmp_path: Path) -> Path:
    p = tmp_path / "lamptown.toml"
    p.write_text(ROOMS_TOML, encoding="utf-8")
    return B.build(parse_config(ROOMS_TOML, p), out_dir=tmp_path / "out", now=NOW).town


def room_frames(page: Path, room: str, query: str, settle: str = "", **kw) -> dict:
    body = FRAMES + settle + f"""
      const still = K.still, id = RS.id;
      const hs = frames({TIMES});
      return {{ id, still, hs, seen: RS.room.seen ? RS.room.seen() : null }};"""
    return run(page, body, query=query, frag=f"#room={room}", **kw)


def demo(state: str) -> str:
    return f"?t=300&live=0&demo=1&state={state}"


# ------------------------------------------------------------------ D1: an idle room is still
# the lab's ok scene turns its pick's label round once on the way in (a real event): idle after
SETTLE = "for (let w = 0; w < 4000 && RS.room.busy(); w += 50) await wait(50);"
IDLE = [("nightly", ""), ("agents", ""), ("gate", ""), ("skills", ""), ("lab", SETTLE)]


@browser
@pytest.mark.parametrize(("room", "settle"), IDLE, ids=[r for r, _ in IDLE])
def test_an_idle_room_draws_the_same_frame_at_any_time(tmp_path, room, settle):
    r = room_frames(rooms_town(tmp_path), room, demo("ok"), settle)
    # the demo animates: a frozen frame would prove nothing
    assert r["id"] == room and r["still"] is False
    assert len(set(r["hs"])) == 1, f"{room}: an idle room moved between frames: {r['hs']}"


# the vault's demo replays marks and a drop on a loop (real events), so its idle case is a real
# notebook whose inbox is all sorted and days old: the flies that came to the old fish rest
SORTED_INBOX = """# Inbox 2026-03-12

## 1. Tail ribbons for the paper kite
triage: keep

## 2. A moss calendar
triage: keep
"""


@browser
def test_an_idle_notebook_is_still_even_with_flies_on_its_old_tray(tmp_path, monkeypatch):
    monkeypatch.setitem(C.BUILTIN, "markdown_vault", "lamptown.collectors.vault:collect")
    root = tmp_path / "notes"
    shutil.copytree(Path(__file__).parent / "fixtures" / "vault" / "notes", root)
    for f in (root / "inbox").glob("*.md"):
        f.unlink()
    (root / "inbox" / "2026-03-12.md").write_text(SORTED_INBOX, encoding="utf-8")
    toml = (f'[[building]]\nid = "notes"\ntype = "vault"\nlabel = "Notebook"\n'
            f'  [[building.source]]\n  kind = "markdown_vault"\n'
            f'  path = "{root.as_posix()}"\n  inbox = "inbox/*.md"\n'
            f'  dropped = "inbox/dropped"\n  kept = "inbox/kept"\n')
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    page = B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW).town
    r = room_frames(page, "notes", "?t=300&live=0&motion=1")
    s = r["seen"]
    assert r["still"] is False and s["sample"] is False, s
    assert s["untriaged"] == 0 and s["flies"] > 0, s
    assert len(set(r["hs"])) == 1, f"an idle notebook moved between frames: {r['hs']}"


@browser
@pytest.mark.parametrize("room", ["nightly", "agents", "lab"])
def test_a_room_at_work_still_moves(tmp_path, room):
    r = room_frames(rooms_town(tmp_path), room, demo("working"))
    assert r["id"] == room and len(set(r["hs"])) > 1, f"{room}: work shows no motion: {r['hs']}"


# ------------------------------------------------------------------ D2: the green lamp is steady
@browser
def test_the_running_fine_lamp_is_steady(tmp_path):
    r = run(build_town(tmp_path, FICTIONAL), FRAMES + """
      const k = CV().width / W, x2 = CV().getContext('2d');
      const box = ([x, y]) => Array.from(x2.getImageData(Math.round((x - 5) * k),
        Math.round((y - 5) * k), Math.round(11 * k), Math.round(11 * k)).data).join(',');
      render(300, false);
      const green = Object.keys(SEEN.lamps).filter(p => SEEN.lamps[p] === 'green');
      const at = t => { render(t, false); return green.map(p => box(PLOT[p].lamp)); };
      const a = at(300), b = at(301.3), c = at(302.9);
      const same = a.map((v, i) => v === b[i] && v === c[i]);
      return { green, words: LAMPWORDS.green, same };""",
        query="?t=300&live=0&motion=1")
    assert r["green"], "the fictional town has a green lamp"
    assert r["words"].startswith("STEADY GREEN")
    assert all(r["same"]), dict(zip(r["green"], r["same"], strict=True))


# ------------------------------------------------------------------ D3: the padlock tip names it
def shelf_page(tmp_path: Path, monkeypatch, folder: str) -> tuple[Path, str]:
    monkeypatch.setitem(C.BUILTIN, "skill_dirs", "lamptown.collectors.shelf:collect")
    toml = f"""
[[building]]
id = "skills"
type = "shelf"
label = "Flag locker"
  [[building.source]]
  kind = "skill_dirs"
  dirs = ["{(tmp_path / "gone").as_posix()}"]
"""
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    page = B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW).town
    text = page.read_text(encoding="utf-8")
    m = re.search(r'<script id="town-data" type="application/json">(.*?)</script>', text, re.S)
    assert m
    data = json.loads(m.group(1))
    data["rooms"]["skills"]["folder"] = folder  # whatever the data holds, the tip shows a name
    return page, json.dumps(data).replace("</", "<\\/")


PADLOCK = "return { tips: allTargets().filter(t => t.id === 'padlock').map(t => t.tip) };"


@browser
@pytest.mark.parametrize("folder", ["C:\\Users\\you\\skills", "/work/kit/skills/", "skills"])
def test_the_padlock_tip_names_the_folder_never_its_path(tmp_path, monkeypatch, folder):
    page, data = shelf_page(tmp_path, monkeypatch, folder)
    r = run(page, PADLOCK, frag="#room=skills", replace_data=data)["tips"]
    assert r == [["The skills folder skills: can't see it", "Check its path in the settings."]], r
    assert not any("/" in s or "\\" in s for s in r[0])


@browser
def test_the_padlock_tip_with_no_folder_says_how_to_set_one_up(tmp_path, monkeypatch):
    page, data = shelf_page(tmp_path, monkeypatch, "")
    r = run(page, PADLOCK, frag="#room=skills", replace_data=data)["tips"]
    want = ["The skills folder: can't see it", "No folder set up: add its path in the settings."]
    assert r == [want], r


# ------------------------------------------------------------------ D4: one copy of the room chrome
def web(name: str) -> str:
    return resources.files("lamptown.web").joinpath(name).read_text(encoding="utf-8")


ROOM_FILES = ["rooms/jobs.js", "rooms/harbour.js", "rooms/shelf.js", "rooms/vault.js",
              "rooms/contest.js", "rooms/ledger.js"]


def test_the_rooms_draw_their_chrome_with_the_kit():
    for f in ROOM_FILES:
        src = web(f)
        assert "function drawCursor" not in src, f"{f}: the cursor is K.cursor()"
        assert "'< TOWN'" not in src, f"{f}: the door sign is K.doorSign()"
        if f != "rooms/contest.js":  # the lab's two-line SAMPLE / MADE UP wall plaque is its own
            assert "function drawSampleBadge" not in src, f"{f}: the badge is K.sampleBadge()"
    kit = web("kit.js")
    for fn in ("doorSign", "cursor", "sampleBadge", "guard"):
        assert f"function {fn}(" in kit, fn
    for f in ("live.js", "lighthouse.js", "berths.js"):
        src = web(f)
        assert "function guard" not in src and "K.guard(" in src, f"{f}: the guard is K.guard()"
    lh = web("lighthouse.js")
    assert "function graph" not in lh and "new Map([[from, 0]])" not in lh  # no second Dijkstra
    assert "LAMPLIGHTERS.shortest" in lh


@browser
def test_k_carries_the_room_chrome_and_it_draws(tmp_path):
    r = run(build_town(tmp_path, FICTIONAL), """
      const fns = ['doorSign', 'cursor', 'sampleBadge', 'guard'].map(n => [n, typeof K[n]]);
      const ink = fn => {
        const c = document.createElement('canvas'); c.width = W; c.height = H; const old = g;
        g = c.getContext('2d'); let out; try { out = fn(); } finally { g = old; }
        const d = c.getContext('2d').getImageData(0, 0, W, H).data;
        let n = 0; for (let i = 3; i < d.length; i += 4) n += d[i] > 0;
        return [n, out]; };
      const door = ink(() => K.doorSign(40, 40));
      const mid = ink(() => K.doorSign(100, 40, { centre: true }));
      const cur = ink(() => K.cursor(50, 50, true));
      const badge = ink(() => K.sampleBadge('SAMPLE: MADE-UP', 4, 200));
      let calls = 0; const seen0 = SEEN.dGuardTest;
      const g1 = K.guard('dGuardTest')(function layerX() { calls++; throw new Error('boom'); });
      g1(); g1();
      return { fns, door, mid, cur, badge, name: g1.name, calls,
        err: SEEN.dGuardTest || null, seen0: seen0 || null };""",
        query="?t=300&live=0")
    assert all(t == "function" for _, t in r["fns"]), r["fns"]
    # it draws, and returns its width whether centred or not
    assert r["door"][0] > 0 and r["door"][1] == r["mid"][1] > 0
    assert r["cur"][0] > 0 and r["badge"][0] > 0
    # the guarded layer is tried every frame, logged once, and keeps its name
    assert r["name"] == "layerX" and r["calls"] == 2
    assert r["seen0"] is None and "boom" in r["err"]


@browser
def test_the_lighthouse_walks_the_lamplighters_footpaths(tmp_path):
    r = run(build_town(tmp_path, FICTIONAL), """
      const key = p => p[0] + ',' + p[1];
      const plots = ['tower', 'library', 'gate', 'lab', 'workshop', 'boathouse'], bad = [];
      const from = key(LAMPLIGHTERS.PLACE.tower.join);
      for (const b of plots) {   // the way from the tower to each door ends at that door's join
        const to = key(LAMPLIGHTERS.PLACE[b].join), s = LAMPLIGHTERS.shortest(from, to);
        const ok = Array.isArray(s) && s.length && key(s[s.length - 1]) === to;
        if (b !== 'tower' && !ok) bad.push(b); }
      return { shared: typeof LAMPLIGHTERS.shortest, bad };""", query="?t=300&live=0")
    assert r["shared"] == "function" and r["bad"] == []


# ------------------------------------------------------------------ D5: the lows
LIVE_TOML = FICTIONAL + '\n[live]\nstatus_dir = "status"\n'

FEED = """
const ag = (id, state, plot, o) => Object.assign({ id, tool: 'codex', title: 'Agent ' + id,
  project: 'paper-kite', plot, state, since: new Date(Date.now() - 600000).toISOString(),
  parent: null, subagents: 0, files: 0, commits: 0, pushed: false, ci: null, private: false },
  o || {});
let N = 0;
const feed = (agents, projects) => { N++; lamptownLive({ spec: 'lamptown-live/1',
  at: new Date().toISOString(), rev: String(N), more: 0, agents, projects: projects || [],
  need: agents.filter(a => a.state === 'waiting').length }); };
"""


def live_town(tmp_path: Path) -> Path:
    return build_town(tmp_path, LIVE_TOML)


@browser
def test_a_lamplighters_click_box_holds_its_lantern_either_way_it_faces(tmp_path):
    r = run(live_town(tmp_path), FEED + """
      const plots = ['tower', 'library', 'gate', 'lab', 'workshop', 'boathouse'];
      const st = ['working', 'error', 'quiet'], agents = [];
      plots.forEach((p, i) => {
        agents.push(ag('s' + i, st[i % 3], p)); agents.push(ag('w' + i, 'waiting', p)); });
      feed(agents); await wait(300);
      const out = [], S = LAMPLIGHTERS.S;
      const holds = (b, x, y) => x >= b[0] && x < b[0] + b[2] && y >= b[1] && y < b[1] + b[3];
      for (const face of [1, -1]) {
        for (const f of S.figs.values()) f.face = face;
        render(300, true);
        for (const m of S.marks) {
          if (!m.id) continue;
          const b = S.hits.find(x => x.id === m.id).box, [lx, ly] = m.lamp;
          const inside = [[0, 0], [-1, 0], [1, 0], [0, 1]]
            .every(([dx, dy]) => holds(b, lx + dx, ly + dy));
          out.push({ id: m.id, face, st: m.st, sit: m.slump >= 0, inside }); } }
      return { out };""", query="?live=0&t=300")
    out = r["out"]
    assert {o["face"] for o in out} == {1, -1}
    assert any(o["sit"] for o in out) and any(not o["sit"] for o in out)
    assert all(o["inside"] for o in out), [o for o in out if not o["inside"]]


@browser
def test_an_agent_with_no_since_is_no_news_on_load(tmp_path):
    r = run(live_town(tmp_path), FEED + """
      let played = 0; SFX.bell = () => { played++; return true; };
      const a = ag('nosince', 'waiting', 'gate'); delete a.since;
      feed([a]); await wait(400);
      const L = SEEN.lighthouse || {};
      return { mode: L.mode, bells: L.bells, flashing: L.flashing, played };""",
        query="?live=0&motion=1")
    assert r["mode"] == "wait"  # it is still a wait: the light runs to its door
    assert r["bells"] == 0 and r["flashing"] is False and r["played"] == 0, r


@browser
def test_the_berth_tags_are_built_once_per_change_not_per_frame(tmp_path):
    r = run(live_town(tmp_path), FEED + """
      const pr = (id, i) => ({ id, name: id.replace(/-/g, ' ').toUpperCase(), berth: i,
        new: false });
      const at = (id, p) => ag(id, 'working', 'boathouse', { project: p });
      feed([at('a1', 'paper-kite'), at('b1', 'tide-api')],
           [pr('paper-kite', 0), pr('tide-api', 1)]);
      await wait(600);
      const a = BERTHS.S.tagsBuilt;
      for (let i = 0; i < 30; i++) render(300 + i * 0.1, false);
      await wait(600);
      const b = BERTHS.S.tagsBuilt;
      feed([at('a1', 'paper-kite')], [pr('paper-kite', 0), pr('moss-engine', 1)]);
      await wait(600);
      return { a, b, c: BERTHS.S.tagsBuilt, err: SEEN.berthsError || null };""",
        query="?live=0&motion=1")
    assert r["err"] is None
    assert isinstance(r["a"], int) and r["a"] >= 1, r
    assert r["b"] == r["a"], f"frames rebuilt the tags: {r}"
    assert r["c"] > r["b"], "a new project builds them again"
