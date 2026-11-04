"""The town truth test: everything that moves in the town follows the data, and nothing else.

The demo world is built at a fixed moment, then each case injects odd data into the page's data
slot and reads what the frame really drew (``SEEN``), the truth model (``TRUTH``), the tooltips
and the sky functions in headless Chrome (the harness in ``test_town.py``).

What is checked: walkers = unsorted vault inbox notes; belts carry only real waiting items; the
bell rings only while a job runs or in the minute one is due; the scanner sweeps only while a scan
runs; the guard's light is the ledger state's colour; the shipwright works only while the project
on the slip is busy; the serpent surfaces only when every building is green; unreadable data never
shows made-up numbers; the moon's phase is right for known dates; still mode is still.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import shutil
from importlib import resources

import pytest

from _browser import browser
from lamptown.demo import build_demo
from test_town import run

pytestmark = browser

NOW = dt.datetime(2026, 11, 24, 21, 30)  # local time: a full-moon night
AT = "2026-11-24T21:30:00"
DAY = "?motion=1&t=300&at=2026-11-24T12:00:00"  # long after the ride-in, at noon
NIGHT = f"?motion=1&t=300&at={AT}"
_Z = NOW.astimezone().strftime("%z")
NOW_OFFSET = _Z[:3] + ":" + _Z[3:]  # the local offset on that day, as ISO writes it
STC = {"ok": "#3ddc7a", "waiting": "#ffb020", "working": "#36d3e6", "error": "#ff4a4a",
       "missing": "#4a4e62"}
TIPS = "const tips = () => TIPS.concat(TIPS_BACK).map(t => t.tip);"


# ----------------------------------------------------------------------------- pages + data
@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    """The demo world built at NOW: the lively default and the quiet `ok` state."""
    base = tmp_path_factory.mktemp("truth")
    now = NOW.astimezone()
    lively = build_demo(base / "lively", now=now)
    calm = build_demo(base / "calm", now=now, state="ok")
    return {"lively": lively, "calm": calm}


def slot(data) -> str:
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def quiet(pages) -> dict:
    """Nothing happening: the demo's `ok` world, with the gate amber so the town is not all
    green (no serpent)."""
    D = copy.deepcopy(pages["calm"].data)
    D["parts"]["gate"]["lamp"] = "amber"
    D["parts"]["gate"]["state"] = "waiting"
    return D


def check(pages, mutate, body: str, query: str = DAY, **kw) -> dict:
    D = quiet(pages)
    if mutate:
        mutate(D)
    return run(pages["calm"].town, TIPS + body, query=query, replace_data=slot(D), **kw)


def ideas(n: int, kept: int = 1, froms=("Paper Kite", "Skill shelf", "")):
    def f(D):
        items = [{"n": i + 1, "title": f"Idea number {i + 1}", "triaged": False,
                  **({"from": froms[i % len(froms)]} if froms[i % len(froms)] else {})}
                 for i in range(n)]
        items += [{"n": n + k + 1, "title": "Kept one, not walking", "mark": "keep",
                   "triaged": True} for k in range(kept)]
        D["rooms"]["notes"]["inbox"] = {"date": "2026-11-23", "items": items, "untriaged": n}
        D["rooms"]["notes"]["state"] = "waiting"
        D["parts"]["notes"]["lamp"] = "amber"
    return f


def ledger(rows, state, lamp):
    def f(D):
        D["rooms"]["gate"] = {"state": state, "rows": rows}
        D["parts"]["gate"]["lamp"] = lamp
    return f


GATE_RED = ledger([{"tool": "wait-one", "state": "waiting"},
                   {"tool": "noscan-tool", "state": "error", "result": "No scan on record"},
                   {"tool": "signed-tool", "state": "ok"},
                   {"tool": "wait-two", "state": "waiting"}], "error", "red")
GATE_SCAN = ledger([{"tool": "being-scanned", "state": "working"},
                    {"tool": "signed-tool", "state": "ok"}], "working", "green")


def job_running(feeds: str | None = None):
    def f(D):
        row = D["rooms"]["jobs"]["rows"][1]
        row["state"] = "working"
        if feeds is None:
            row.pop("feeds", None)
        else:
            row["feeds"] = feeds
    return f


def busy(index: int, done_total=(3, 9)):
    def f(D):
        rows = D["rooms"]["harbour"]["rows"]
        for r in rows:
            r["busy"] = False
        rows[0]["progress"] = {"done": done_total[0], "total": done_total[1]}
        rows[index]["busy"] = True
    return f


def all_green(D):
    for p in D["parts"].values():
        p["lamp"] = "green"
    D["needs"] = 0


# ----------------------------------------------------------------------------- walkers
def test_no_unsorted_notes_no_walkers(pages):
    r = check(pages, None,
              "return { walkers: SEEN.walkers, more: SEEN.more || 0, n: TRUTH.notes.length };")
    assert (r["walkers"], r["more"], r["n"]) == (0, 0, 0)


def test_three_unsorted_notes_three_walkers_from_what_they_are_about(pages):
    r = check(pages, ideas(3), """
      const titles = TRUTH.notes.map(n => n.title);
      return { walkers: SEEN.walkers, from: SEEN.walkerFrom, titles,
        tipped: titles.every(tl => tips().some(t => t[0] === tl)),
        front: (tipAt(199, 74) || { tip: [''] }).tip[0] };""")
    assert r["walkers"] == 3
    # a project name walks from the boathouse, a building's label from its plot, else the tower
    assert r["from"] == ["boathouse", "workshop", "tower"]
    assert r["titles"] == ["Idea number 1", "Idea number 2", "Idea number 3"]
    assert "Kept one, not walking" not in r["titles"]
    assert r["tipped"] is True and r["front"] == "Idea number 1"


def test_many_unsorted_notes_cap_the_walkers_and_show_a_sign(pages):
    r = check(pages, ideas(9), """return { walkers: SEEN.walkers, more: SEEN.more, cap: WALK_CAP,
      tip: tips().some(t => t[0] === (9 - WALK_CAP) + ' MORE NOTES WAITING') };""")
    assert r["walkers"] == r["cap"] and r["more"] == 9 - r["cap"] and r["tip"] is True


def test_a_count_alone_still_walks(pages):
    def f(D):
        D["rooms"]["notes"]["inbox"] = {"items": [], "untriaged": 2}
    r = check(pages, f, "return { walkers: SEEN.walkers, from: SEEN.walkerFrom };")
    assert r["walkers"] == 2 and r["from"] == ["tower", "tower"]


def test_a_dark_vault_sends_no_walkers(pages):
    def f(D):
        ideas(3)(D)
        D["parts"]["notes"]["lamp"] = "grey"
    r = check(pages, f, "return { walkers: SEEN.walkers };")
    assert r["walkers"] == 0


def test_a_custom_room_on_a_plot_keeps_its_lamp_but_not_the_plots_animations(pages):
    def f(D):
        GATE_SCAN(D)
        ideas(3)(D)
        D["parts"]["gate"]["type"] = "kiosk"  # not the gate's own room type
        D["parts"]["notes"]["type"] = "kiosk"
    r = check(pages, f, """return { A: SEEN.belts.A, scanner: !!SEEN.scanner, guard: SEEN.guard,
      walkers: SEEN.walkers, lamp: SEEN.lamps.gate };""")
    assert r["A"] == [] and r["scanner"] is False and r["walkers"] == 0
    assert r["lamp"] == "green" and r["guard"] == STC["ok"]  # the lamp's colour, nothing more


# ----------------------------------------------------------------------------- belts + gate
def test_quiet_town_belts_are_empty_and_stopped(pages):
    r = check(pages, None, """return { belts: SEEN.belts, scanner: !!SEEN.scanner,
      stoppedTip: tips().some(t => /STOPPED/.test(t[1] || '')) };""")
    b = r["belts"]
    assert (b["A"], b["B"], b["C"]) == ([], [], [])
    assert not any(b["moving"].values()) and all(b["shown"].values())
    assert r["scanner"] is False and r["stoppedTip"] is True


def test_belt_a_carries_the_real_tools_at_the_gate_and_the_guard_light_is_red(pages):
    r = check(pages, GATE_RED, """const gt = tipAt(336, 64);
      return { A: SEEN.belts.A, moving: SEEN.belts.moving.A, scanner: !!SEEN.scanner,
        guard: SEEN.guard, guardTip: gt && gt.tip,
        named: ['noscan-tool', 'wait-one', 'wait-two']
          .every(n => tips().some(t => t[0] === n)) };""")
    assert r["A"] == ["red", "wait", "wait"]  # stopped one in front, signed one not on the belt
    assert r["moving"] is False  # they have arrived: the belt stands still
    assert r["scanner"] is False
    assert r["guard"] == STC["error"]
    assert r["guardTip"][0] == "THE GUARD'S LIGHT: SOMETHING IS WRONG"
    assert r["named"] is True


def test_a_running_scan_sweeps_the_scanner(pages):
    r = check(pages, GATE_SCAN, """return { A: SEEN.belts.A, scanner: !!SEEN.scanner,
      guard: SEEN.guard, tip: tips().some(t => t[0] === 'SCANNER RUNNING') };""")
    assert r == {"pageErrors": [], "A": ["scan"], "scanner": True, "guard": STC["working"],
                 "tip": True}


@pytest.mark.parametrize("state", ["ok", "waiting", "working", "error", "missing"])
def test_the_guard_light_is_the_ledger_state_colour(pages, state):
    lamp = {"ok": "green", "waiting": "amber", "working": "green", "error": "red",
            "missing": "grey"}[state]
    r = check(pages, ledger([], state, lamp), "return { guard: SEEN.guard };")
    assert r["guard"] == STC[state]


def test_more_tools_than_fit_show_a_sign(pages):
    rows = [{"tool": f"tool-{i}", "state": "waiting"} for i in range(7)]
    r = check(pages, ledger(rows, "waiting", "amber"), """return { A: SEEN.belts.A,
      cap: BELTS.A.cap, tip: tips().some(t => t[0] === 'MORE TOOLS AT THE GATE') };""")
    assert len(r["A"]) == r["cap"] and r["tip"] is True


def test_belt_b_and_c_carry_staged_items_and_results_waiting_for_a_pick(pages):
    def f(D):
        D["rooms"]["skills"]["rows"] += [
            {"name": "new-skill", "stage": "building", "state": "working"},
            {"name": "sold-skill", "stage": "staged", "state": "waiting"}]
        c = D["rooms"]["contest"]
        c.update(state="waiting", pick=None,
                 entries=[{"code": "A1", "name": "First", "sealed": False},
                          {"code": "A2", "name": "Second", "sealed": False},
                          {"code": "A3", "sealed": True}],
                 judges={"expected": 2, "received": 2})
    r = check(pages, f, """return { B: SEEN.belts.B, C: SEEN.belts.C, bubbles: !!SEEN.bubbles,
      trophy: !!SEEN.trophy,
      tipped: ['new-skill', 'sold-skill', 'First', 'Second']
        .every(n => tips().some(t => t[0] === n)) };""")
    assert r["B"] == ["building", "staged"]
    assert r["C"] == ["orb", "orb"]  # the sealed entry is not a result you can pick yet
    assert r["bubbles"] is False and r["trophy"] is False and r["tipped"] is True


def test_judges_still_out_bubble_and_send_no_results(pages):
    def f(D):
        D["rooms"]["contest"].update(state="working", pick=None,
                                     judges={"expected": 3, "received": 1})
    r = check(pages, f, """return { C: SEEN.belts.C, bubbles: !!SEEN.bubbles,
      tip: tips().some(t => t[0] === 'JUDGING IN PROGRESS' && /1 OF 3 JUDGES IN/.test(t[1])) };""")
    assert r == {"pageErrors": [], "C": [], "bubbles": True, "tip": True}


def test_a_recorded_pick_shows_the_trophy(pages):
    r = check(pages, None, "return { trophy: !!SEEN.trophy, picked: TRUTH.picked };")
    assert r["trophy"] is True and r["picked"]


def test_items_ride_in_at_the_start(pages):
    r = check(pages, GATE_RED, "return { moving: SEEN.belts.moving.A, A: SEEN.belts.A };",
              query="?motion=1&t=5.7&at=2026-11-24T12:00:00")
    assert r["moving"] is True


# ----------------------------------------------------------------------------- bell, spark, dome
def test_no_job_running_the_bell_hangs_still(pages):
    r = check(pages, None,
              "return { bell: SEEN.bell || null, spark: !!SEEN.spark, dome: !!SEEN.dome };")
    assert r == {"pageErrors": [], "bell": None, "spark": False, "dome": False}


def test_a_running_job_rings_the_bell_without_a_spark_unless_it_feeds_the_vault(pages):
    body = """return { bell: SEEN.bell || null, job: SEEN.bellJob, spark: !!SEEN.spark,
      tip: tips().some(t => t[0] === 'THE BELL IS RINGING'
                            && /INBOX SORTER IS RUNNING NOW/.test(t[1])) };"""
    r = check(pages, job_running(), body)
    assert (r["bell"], r["job"], r["spark"], r["tip"]) == ("running", "Inbox sorter", False, True)
    r = check(pages, job_running(feeds="notes"), body)
    assert (r["bell"], r["spark"]) == ("running", True)


def test_a_running_review_lifts_notes_in_the_dome(pages):
    def f(D):
        D["rooms"]["notes"]["state"] = "working"
    r = check(pages, f, """return { dome: !!SEEN.dome,
      tip: tips().some(t => t[0] === 'A REVIEW IS FILING NOTES NOW') };""")
    assert r["dome"] is True and r["tip"] is True


def test_the_bell_rings_once_in_the_minute_a_job_is_due(pages):
    def f(D):
        D["rooms"]["jobs"]["rows"][2]["next"] = "2026-11-24T13:00:00" + NOW_OFFSET
    r = check(pages, f, """return { bell: SEEN.bell || null,
      later: bellNow(nowMs() + 90e3), before: bellNow(nowMs() - 120e3) };""",
              query="?motion=1&t=300&at=2026-11-24T13:00:20")
    assert r["bell"] == "due" and r["later"] is None and r["before"] is None


def test_a_dark_tower_never_rings(pages):
    def f(D):
        job_running()(D)
        D["parts"]["jobs"]["lamp"] = "grey"
    r = check(pages, f, "return { bell: SEEN.bell || null };")
    assert r["bell"] is None


# ----------------------------------------------------------------------------- shipwright
def test_the_shipwright_works_only_while_the_project_on_the_slip_is_busy(pages):
    body = "return { ship: !!SEEN.shipwright, hull: SEEN.hull };"
    r = check(pages, busy(0), body)
    assert r["ship"] is True and r["hull"]["name"] == "Paper Kite"
    r = check(pages, busy(1), body)  # another project is busy, not the one on the slip
    assert r["ship"] is False
    r = check(pages, busy(0, (9, 9)), body)  # busy, but its hull is finished
    assert r["ship"] is False
    r = check(pages, None, body)
    assert r["ship"] is False


# ----------------------------------------------------------------------------- serpent
def test_the_serpent_surfaces_only_when_every_building_is_green(pages):
    body = "return { serpent: !!SEEN.serpent };"
    assert check(pages, all_green, body)["serpent"] is True
    assert check(pages, None, body)["serpent"] is False  # the gate is amber

    def one_grey(D):
        all_green(D)
        D["parts"]["contest"]["lamp"] = "grey"
    assert check(pages, one_grey, body)["serpent"] is False
    r = check(pages, all_green, "const tp = tipAt(40, 212); return { tip: tp && tp.tip };")
    assert r["tip"][0] == "THE SEA SERPENT" and "EVERY BUILDING IS GREEN" in r["tip"][1]


# ----------------------------------------------------------------------------- unreadable data
UNREADABLE_BODY = """return { err: DATA_ERR, sample: !!D.sample, lamps: SEEN.lamps, lots: SEEN.lots,
  plates: SEEN.plates, banner: SEEN.banner, corner: cornerBuilt(), walkers: SEEN.walkers,
  belts: [SEEN.belts.A, SEEN.belts.B, SEEN.belts.C], serpent: !!SEEN.serpent,
  bell: SEEN.bell || null };"""


@pytest.mark.parametrize("raw", ['{"parts": {"jobs": {"lamp": "green", "num": "61"',
                                 "not json at all", '{"parts": []}'])
@pytest.mark.parametrize("query", [DAY, DAY + "&preview=1"])
def test_unreadable_data_goes_dark_and_never_shows_made_up_numbers(pages, raw, query):
    r = run(pages["lively"].town, UNREADABLE_BODY, query=query, replace_data=raw)
    assert r["err"] and r["sample"] is False  # ?preview=1 never brings the sample to a real page
    assert r["lamps"] == {} and len(r["lots"]) == 6
    assert all(p["num"] == "" for p in r["plates"].values())
    assert "61" not in json.dumps(r["plates"])
    assert r["banner"] == "CAN'T READ THE TOWN'S DATA" and r["corner"] == "NO DATA"
    assert r["walkers"] == 0 and r["belts"] == [[], [], []]
    assert r["serpent"] is False and r["bell"] is None


def test_a_building_it_cannot_see_shows_a_dash_not_its_number(pages):
    def f(D):
        D["parts"]["jobs"].update(lamp="grey", num="61")
    r = check(pages, f, "return { plate: SEEN.plates.tower };")
    assert r["plate"]["num"] == "-"


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """The unfilled template next to its scripts, as a user might open it."""
    d = tmp_path_factory.mktemp("template")
    web = resources.files("lamptown").joinpath("web")
    for name in ("town.html", "kit.js", "town.js"):
        with resources.as_file(web.joinpath(name)) as src:
            shutil.copyfile(src, d / name)
    return d / "town.html"


def test_the_raw_template_is_an_error_view(template):
    r = run(template, UNREADABLE_BODY, query=DAY)
    assert "EMPTY TEMPLATE" in r["err"] and r["sample"] is False
    assert r["lamps"] == {} and r["corner"] == "NO DATA"


def test_the_template_preview_is_a_labelled_sample(template):
    r = run(template, UNREADABLE_BODY, query=DAY + "&preview=1")
    assert r["sample"] is True and r["err"] == ""
    assert r["banner"] == "SAMPLE: TEMPLATE PREVIEW" and r["corner"] == "SAMPLE"
    assert r["serpent"] is False


# ----------------------------------------------------------------------------- sky
def test_the_moon_phase_is_right_for_known_dates(pages):
    r = check(pages, None, """const P = s => moonPhase(Date.parse(s));
      const k = s => skyAt(new Date(s).getTime()).phase;
      return {
        full: P('2026-11-24T15:00:00Z').name, new_: P('2026-11-09T12:00:00Z').name,
        eclipse24: P('2024-04-08T18:21:00Z').cycle, lunar25: P('2025-03-14T06:55:00Z').cycle,
        annular24: P('2024-10-02T18:49:00Z').cycle,
        quarter: [P('2026-11-17T12:00:00Z').illum, P('2026-11-17T12:00:00Z').waxing],
        noon: k('2026-11-04T12:00:00'), late: k('2026-11-04T23:00:00') };""")
    near = lambda c, want: min(abs(c - want), 1 - abs(c - want)) <= 0.03  # noqa: E731
    assert r["full"] == "FULL MOON" and r["new_"] == "NEW MOON"
    assert near(r["eclipse24"], 0) and near(r["annular24"], 0)  # published new moons
    assert near(r["lunar25"], 0.5)  # a published full moon (a lunar eclipse)
    assert abs(r["quarter"][0] - 0.5) < 0.12 and r["quarter"][1] is True
    assert (r["noon"], r["late"]) == ("day", "night")


def test_a_full_moon_night_draws_the_moon_and_fireflies(pages):
    r = check(pages, None, """return { phase: SEEN.phase, moon: SEEN.moon, flies: !!SEEN.fireflies,
      lit: LIT, tip: tips().some(t => t[0] === 'THE MOON: FULL MOON') };""", query=NIGHT)
    assert r["phase"] == "night" and r["moon"]["up"] and r["moon"]["x"] > 0
    assert r["moon"]["name"] == "FULL MOON" and r["flies"] and r["lit"] and r["tip"]


def test_a_new_moon_night_has_no_moon_and_says_why(pages):
    r = check(pages, None, """return { phase: SEEN.phase, moon: SEEN.moon || null,
      sea: (tips().find(t => t[0] === 'THE SEA (SCENERY)') || []).join(' ') };""",
              query="?motion=1&t=300&at=2026-11-09T22:00:00")
    assert r["phase"] == "night" and not (r["moon"] or {}).get("x")
    assert "NO MOON NOW: NEW MOON" in r["sea"]


def test_by_day_no_moon_no_fireflies_glass_windows(pages):
    r = check(pages, None, """return { phase: SEEN.phase, moon: SEEN.moon || null,
      flies: !!SEEN.fireflies, sun: SEEN.sun || null, lit: LIT };""")
    assert r["phase"] == "day" and r["moon"] is None and r["flies"] is False
    assert r["sun"]["x"] > 0 and r["lit"] is False


# ----------------------------------------------------------------------------- still mode
HASH = """const hash = () => { const d = main.getImageData(0, 0, 400, 225).data; let h = 0;
  for (let i = 0; i < d.length; i += 4) h = (h * 31 + d[i] + d[i + 1] * 7 + d[i + 2] * 13) | 0;
  return h; };"""


def test_still_mode_is_still_and_still_tells_the_truth(pages):
    def f(D):
        ideas(3)(D)
        GATE_RED(D)
        job_running()(D)
    r = check(pages, f, HASH + """const h0 = hash(); await wait(1500);
      return { still: stillMode(), running, h0, h1: hash(), walkers: SEEN.walkers,
               A: SEEN.belts.A, bell: SEEN.bell || null };""",
              query=f"?at={AT}", flags=("--force-prefers-reduced-motion",))
    assert r["still"] is True and r["running"] is False
    assert r["h0"] == r["h1"]  # no frame loop: nothing changes on screen
    # ...yet it shows the real state: the walkers already at the door, the crates, a tilted bell
    assert r["walkers"] == 3 and r["A"] == ["red", "wait", "wait"] and r["bell"] == "running"


def test_motion_mode_does_move(pages):
    """The control for the still test: the same hash changes when the town animates."""
    r = check(pages, None, HASH + "const h0 = hash(); await wait(1500); return { h0, h1: hash() };",
              query=f"?motion=1&at={AT}")
    assert r["h0"] != r["h1"]


# ----------------------------------------------------------------------------- everything at once
def test_everything_that_moves_has_a_tooltip(pages):
    def f(D):
        for g in (ideas(3), GATE_SCAN, job_running(feeds="notes"), busy(0)):
            g(D)
        D["rooms"]["notes"]["state"] = "working"
        D["rooms"]["contest"].update(state="working", pick=None,
                                     judges={"expected": 3, "received": 1})
        all_green(D)
    r = check(pages, f, """const want = { walkers: 'NOT TRIAGED YET', bell: 'THE BELL IS RINGING',
        spark: 'THE SPARK CARRIES', scanner: 'SCANNER RUNNING', bubbles: 'JUDGING IN PROGRESS',
        shipwright: 'SHIPWRIGHT AT WORK', dome: 'A REVIEW IS FILING NOTES NOW',
        serpent: 'THE SEA SERPENT', fireflies: 'FIREFLIES', moon: 'THE MOON:' };
      const out = {};
      const has = s => tips().some(t => t.join(' ').includes(s));
      for (const [k, s] of Object.entries(want)) out[k] = !!SEEN[k] && has(s);
      out.beltItems = ['A', 'B', 'C']
        .every(b => BELTS[b].items.every(it => tips().some(t => t === it.tip)));
      out.lamps = ORDER.every(p => tips().some(t => t[0] === nameOn(p) + ' LAMP'));
      out.scenery = tips().some(t => t[0] === 'FOUNTAIN')
        && tips().some(t => t[0] === 'THE SEA (SCENERY)');
      return out;""", query=NIGHT)
    r.pop("pageErrors")
    assert all(r.values()), {k: v for k, v in r.items() if not v}


# ---------------------------------------------------------------------------- the demo world itself
STATE_BODY = """return { lamps: SEEN.lamps, walkers: SEEN.walkers, from: SEEN.walkerFrom,
  belts: SEEN.belts, bell: SEEN.bell || null, job: SEEN.bellJob || null, spark: !!SEEN.spark,
  scanner: !!SEEN.scanner, guard: SEEN.guard, ship: !!SEEN.shipwright, serpent: !!SEEN.serpent,
  bubbles: !!SEEN.bubbles, dome: !!SEEN.dome, needs: SEEN.needs || 0, plates: SEEN.plates,
  clock: SEEN.clock || null, tools: SEEN.tools };"""


def test_the_lively_demo_world_moves_for_real_reasons(pages):
    r = run(pages["lively"].town, STATE_BODY, query=NIGHT)
    assert r["lamps"] == {"tower": "green", "library": "amber", "gate": "green", "lab": "amber",
                          "workshop": "red", "boathouse": "amber"}
    assert r["walkers"] == 3 and r["from"] == ["boathouse", "workshop", "tower"]
    assert r["belts"]["A"] == ["scan"] and r["scanner"] is True and r["guard"] == STC["working"]
    assert r["belts"]["B"] == ["staged", "building"]
    assert r["belts"]["C"] == ["orb", "orb", "orb"]  # the three finalists wait for your pick
    assert (r["bell"], r["job"], r["spark"]) == ("running", "Inbox sorter", True)
    assert r["ship"] is True and r["serpent"] is False and r["bubbles"] is False
    assert r["needs"] == 5 and r["tools"] == 6
    assert r["clock"] and r["clock"]["job"] == "Inbox sorter"  # its next run is the soonest


@pytest.mark.parametrize("state", ["ok", "waiting", "working", "error", "missing"])
def test_each_demo_state_lights_every_lamp_and_moves_only_for_it(tmp_path, state):
    res = build_demo(tmp_path / "w", now=NOW.astimezone(), state=state)
    r = run(res.town, STATE_BODY, query=NIGHT)
    lamp = {"ok": "green", "waiting": "amber", "working": "green", "error": "red",
            "missing": "grey"}[state]
    assert set(r["lamps"].values()) == {lamp} and len(r["lamps"]) == 6
    assert r["serpent"] is (lamp == "green")
    if state in ("ok", "missing"):
        assert r["walkers"] == 0 and r["bell"] is None and not r["scanner"] and not r["ship"]
        assert all(not v for v in (r["belts"]["A"], r["belts"]["B"], r["belts"]["C"]))
        assert r["needs"] == 0
    if state == "missing":
        assert all(p["num"] == "-" for p in r["plates"].values())
        assert r["guard"] == STC["missing"] and r["clock"] is None
    if state == "working":
        assert r["bell"] == "running" and r["scanner"] and r["bubbles"] and r["dome"]
        assert r["walkers"] == 0 and r["needs"] == 0
    if state == "waiting":
        assert r["bell"] is None and r["walkers"] == 3 and r["belts"]["A"] == ["wait"]

