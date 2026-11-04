"""The contest room (the blinded trial): the ``contest_json`` collector on fixtures, and the room
itself in headless Chrome (each state's object, the two-step wait, the
pick kept apart from the judges' winner, tooltips and scale limits)."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

from lamptown import build as B
from lamptown import collectors as C
from lamptown.collectors import contest as CT
from lamptown.collectors.schema import room_schema, validate
from lamptown.config import parse_config

FIX = Path(__file__).parent / "fixtures" / "contest"
DOC = Path(__file__).resolve().parents[1] / "docs" / "contest-format.md"
NOW = dt.datetime(2026, 3, 14, 22, 0, tzinfo=dt.UTC)


def schema_ok(data: dict) -> dict:
    errs = validate(data, room_schema("contest"))
    assert errs == [], errs
    return data


def from_json(*files: Path | str, glob: str | None = None) -> dict:
    p: dict = {"files": [str(f) for f in files]}
    if glob:
        p["glob"] = glob
    return schema_ok(CT.collect(p, NOW))


def write(tmp_path: Path, j: object, name: str = "c.json") -> Path:
    p = tmp_path / name
    p.write_text(j if isinstance(j, str) else json.dumps(j), encoding="utf-8")
    return p


def by_name(r: dict) -> dict:
    return {c["name"]: c for c in r["contests"]}


def cards(c: dict) -> dict:
    return {x["code"]: x for x in c["cards"]}


BASIC = {
    "title": "Soup names",
    "date": "2026-03-10",
    "entries": [{"code": "S1", "name": "Leek Lantern"}, {"code": "S2", "name": "Barley Boat"}],
    "judges": {"expected": 1},
    "rankings": {"A": ["S2", "S1"]},
}


# ----------------------------------------------------------------------------- contest.json
def test_json_fixtures_newest_first_and_states():
    r = from_json(glob=str(FIX / "json" / "*.json"))
    assert [c["name"] for c in r["contests"]] == ["cafe-names", "lantern-route", "kite-colours"]
    assert [(c["state"], c["step"]) for c in r["contests"]] == [
        ("waiting", "unblind"),
        ("waiting", "pick"),
        ("ok", None),
    ]
    assert r["state"] == "waiting" and r["count"] == 3 and r["num"] == "8"
    assert r["label"] == "Cafe names: results are in, unblind?"
    assert r["judges"] == {"expected": 3, "received": 3}
    assert [n["text"] for n in r["needs"]] == [
        "Cafe names: results are in, unblind them",
        "Lantern route: pick one of the finalists",
    ]
    assert r["updated"] == "2026-03-12T18:30+00:00"


def test_sealed_names_never_reach_the_top_level():
    r = from_json(FIX / "json" / "cafe-names.json")
    assert all(e["sealed"] is True and "name" not in e for e in r["entries"])
    assert r["winner"] == "C03" and r["pick"] is None  # a code while sealed
    dump = json.dumps({k: v for k, v in r.items() if k != "contests"})
    assert "Rain Table" not in dump and "The Usual" not in dump
    # the room's own list carries the names, for the in-room unblind
    assert cards(r["contests"][0])["C03"]["name"] == "Rain Table"


def test_round_one_fill_is_the_mean_rank_turned_round():
    c = from_json(FIX / "json" / "cafe-names.json")["contests"][0]
    k = cards(c)
    assert c["measure"] == "rank" and c["rounds"] == 1 and c["full"] is None
    assert k["C03"]["fill"] == 1.0 and k["C03"]["first"] and k["C03"]["place"] == 1
    assert k["C03"]["mean"] == 1.33 and k["C03"]["ranks"] == [1, 2, 1]
    assert k["C07"]["fill"] == 0.05 and k["C07"]["place"] == 8
    assert k["C06"]["control"] is True
    assert [x["code"] for x in c["cards"] if x["finalist"]] == ["C03", "C05"]  # the top two


def test_pick_step_unseals_only_the_finalists():
    r = from_json(FIX / "json" / "lantern-route.json")
    assert r["state"] == "waiting" and r["label"] == "Lantern route: pick one"
    unsealed = [e for e in r["entries"] if not e["sealed"]]
    assert [(e["code"], e["name"]) for e in unsealed] == [
        ("R1", "Harbour Loop"),
        ("R2", "Hill Steps"),
    ]
    assert all("name" not in e for e in r["entries"] if e["sealed"])
    assert r["winner"] == "Harbour Loop"
    c = r["contests"][0]
    assert c["first"] == ["R1", "R2"]  # a tie at the top: both wear the rosette


def test_the_pick_is_not_the_judges_winner():
    r = from_json(FIX / "json" / "kite-colours.json")
    c = r["contests"][0]
    k = cards(c)
    assert r["state"] == "ok" and r["pick"] == "Dusk Violet" and r["winner"] == "Sea Glass"
    assert c["pick"]["code"] == "K03" and c["pick"]["note"] == "Reads best against a grey sky."
    assert k["K03"]["pick"] and not k["K03"]["first"] and k["K02"]["first"] and not k["K02"]["pick"]
    assert c["say"] == "PICKED: DUSK VIOLET" and c["pickWords"] == "Dusk Violet"
    assert all(not e["sealed"] and e["name"] for e in r["entries"])  # picked: all unsealed
    assert r["label"] == "Kite colours: picked Dusk Violet"


def test_scores_fill_the_bottles_and_empty_ones_went_out():
    c = from_json(FIX / "json" / "kite-colours.json")["contests"][0]
    k = cards(c)
    assert (c["measure"], c["rounds"], c["full"], c["of"]) == ("score", 2, 9, 12)
    assert k["K02"]["fill"] == 1.0 and k["K02"]["score"] == 9 and k["K02"]["inR2"]
    assert k["K03"]["fill"] == 0.7778
    assert k["K04"]["fill"] is None and k["K04"]["score"] is None and not k["K04"]["inR2"]
    assert k["K06"]["wild"] and k["K06"]["fill"] is None
    assert c["finalistsBuilt"] is True
    assert k["K02"]["built"]["href"].startswith("file:") and k["K02"]["built"]["href"].endswith(
        "/K02"
    )
    assert k["K03"]["built"]["href"] == "https://example.invalid/dusk"


def test_an_older_contest_waiting_raises_the_room(tmp_path):
    kite = json.loads((FIX / "json" / "kite-colours.json").read_text(encoding="utf-8"))
    kite["date"] = "2026-04-01"
    r = from_json(write(tmp_path, kite, "kite.json"), FIX / "json" / "lantern-route.json")
    assert r["contests"][0]["state"] == "ok" and r["state"] == "waiting"
    assert r["pick"] == "Dusk Violet" and len(r["needs"]) == 1


def test_pick_none_is_a_decision(tmp_path):
    j = dict(BASIC, pick="none", pick_note="Keep the old name.")
    r = from_json(write(tmp_path, j))
    c = r["contests"][0]
    assert r["state"] == "ok" and r["pick"] == "None picked"
    assert c["pick"]["code"] is None and c["pick"]["text"] == "Keep the old name."
    assert c["say"] == "DECIDED: NONE PICKED" and r["label"] == "Soup names: decided, none picked"


def test_pick_by_name_and_case_free_codes(tmp_path):
    j = dict(BASIC, pick="barley boat", rankings={"A": ["s2", "s1"]})
    c = from_json(write(tmp_path, j))["contests"][0]
    assert c["pick"]["code"] == "S2" and cards(c)["S2"]["place"] == 1


def test_a_one_word_name_is_not_taken_for_a_code(tmp_path):
    j = dict(BASIC, entries=[*BASIC["entries"], {"code": "S3", "name": "Mustard"}])
    j.update(pick="Mustard", finalists=["mustard", "S1"], rankings={"A": ["S2", "S1", "S3"]})
    c = from_json(write(tmp_path, j))["contests"][0]
    assert c["state"] == "ok" and c["pick"]["code"] == "S3"
    assert [x["code"] for x in c["cards"] if x["finalist"]] == ["S1", "S3"]


def test_judges_still_ranking_is_working(tmp_path):
    j = dict(BASIC, judges={"expected": 3})
    r = from_json(write(tmp_path, j))
    c = r["contests"][0]
    assert r["state"] == "working" and c["why"] == "r1" and c["say"] == "JUDGES ARE STILL RANKING"
    assert r["judges"] == {"expected": 3, "received": 1}
    assert all(x["fill"] is None for x in c["cards"]) and r["winner"] is None
    r = from_json(write(tmp_path, dict(BASIC, rankings={}, judges={}), "none.json"))
    assert r["contests"][0]["say"] == "WAITING FOR THE JUDGES"


def test_scores_without_rankings_are_enough(tmp_path):
    j = dict(BASIC, rankings={}, judges={}, scores={"S1": 4, "S2": 1.5})
    c = from_json(write(tmp_path, j))["contests"][0]
    assert c["state"] == "waiting" and c["measure"] == "score" and c["rounds"] == 1
    assert cards(c)["S2"]["fill"] == 0.375 and cards(c)["S2"]["score"] == 1.5


def test_many_contests_in_one_file(tmp_path):
    j = {"contests": [BASIC, dict(BASIC, title="Pie names", date="2026-03-11", pick="S1")]}
    r = from_json(write(tmp_path, j, "all.json"))
    assert [c["name"] for c in r["contests"]] == ["all-2", "all-1"]
    assert r["state"] == "waiting" and r["pick"] == "Leek Lantern"


@pytest.mark.parametrize(
    ("j", "say", "why"),
    [
        ("{oops", "CAN'T READ THE FILE", "c.json is not valid JSON."),
        ([1, 2], "CAN'T READ THE CONTEST", "A contest must be a JSON object."),
        ({"entries": "S1"}, "CAN'T READ THE ENTRIES", "entries must be a list."),
        ({"entries": [{"name": "x"}]}, "CAN'T READ AN ENTRY", "Entry 1 has no usable code."),
        (
            dict(BASIC, entries=BASIC["entries"] * 2),
            "CAN'T READ AN ENTRY",
            "The code S1 is used twice.",
        ),
        (
            dict(BASIC, rankings={"A": ["S2", "S9"]}),
            "RANKING DOESN'T MATCH",
            "A ranks S9, which is not an entry.",
        ),
        (dict(BASIC, rankings={"A": []}), "CAN'T READ A RANKING", "No ranking found for A."),
        (
            dict(BASIC, rankings={"A": [None]}),
            "RANKING DOESN'T MATCH",
            "A ranks ?, which is not an entry.",
        ),
        (
            dict(BASIC, rankings=["S1"]),
            "CAN'T READ THE RANKINGS",
            "rankings must map each judge to a list.",
        ),
        (
            dict(BASIC, scores={"S7": 2}),
            "SCORES DON'T MATCH",
            "The scores name S7, which is not an entry.",
        ),
        (
            dict(BASIC, scores={"S1": "lots"}),
            "CAN'T READ THE SCORES",
            "The score of S1 is not a number.",
        ),
        (dict(BASIC, pick="S5"), "PICK DOESN'T MATCH", "The pick names S5, which is not an entry."),
        (dict(BASIC, pick=3), "CAN'T READ THE PICK", 'pick must be a code, "none" or null.'),
        (
            dict(BASIC, finalists=["S4"]),
            "FINALISTS DON'T MATCH",
            "The finalist S4 is not an entry.",
        ),
        (dict(BASIC, wild="S4"), "WILD CARD DOESN'T MATCH", "The wild card is not an entry."),
    ],
)
def test_unreadable_contests_are_errors_and_nothing_is_guessed(tmp_path, j, say, why):
    r = from_json(write(tmp_path, j))
    c = r["contests"][0]
    assert r["state"] == "error" and c["state"] == "error"
    assert (c["say"], c["error"]) == (say, why)
    assert r["needs"][0]["text"].endswith(why) and r["winner"] is None


def test_nothing_found_or_no_entries_is_missing(tmp_path):
    r = from_json(glob=str(tmp_path / "*.json"))
    assert r["state"] == "missing" and r["count"] == 0 and r["contests"] == []
    assert r["label"] == "No contest yet" and "num" not in r
    r = from_json(write(tmp_path, {"title": "Empty", "entries": []}))
    assert r["state"] == "missing" and r["contests"][0]["say"] == "NO ENTRIES YET"
    r = from_json(write(tmp_path, {"entries": [], "judges": {"expected": 2}}, "j.json"))
    assert r["contests"][0]["state"] == "error"


def test_odd_values_never_crash(tmp_path):
    j = {
        "title": 7,
        "date": "2026-02-30",
        "updated": "yesterday",
        "entries": [{"code": "A1", "name": None, "control": "yes", "built": 5}, {"code": "A2"}],
        "judges": {"expected": "x", "received": -2},
        "rankings": {"": ["A1", "A1"], "B": ["A2"]},
        "scores_of": "many",
        "revealed": "yes",
    }
    r = from_json(write(tmp_path, j, "odd.json"))
    c = r["contests"][0]
    assert c["title"] == "odd" and c["date"] != "2026-02-30"
    assert cards(c)["A1"]["name"] == "A1" and cards(c)["A1"]["control"] is False
    assert (c["state"], c["step"], c["judges"]["r1of"]) == ("waiting", "unblind", 2)
    assert cards(c)["A1"]["ranks"] == [1] and not c["revealed"]


def test_the_documented_example_reads():
    m = re.search(r"## A whole file\s+```json\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    j = json.loads(m.group(1))
    spec = CT.read_json_contest(j, "doc", DOC.parent, NOW, "")
    c = CT.model(spec)
    assert c["state"] == "waiting" and c["step"] == "unblind"
    assert c["measure"] == "score" and c["rounds"] == 2 and c["of"] == 6
    assert [x["code"] for x in c["cards"] if x["finalist"]] == ["C02", "C01"]


# ----------------------------------------------------------------------------- the room
from _browser import browser  # noqa: E402  (shared headless browser)
from test_town import run  # noqa: E402

SEEN = "return Object.assign({ id: RS.id, plate: RS.room.plate() }, RS.room.seen());"
KEY = "document.dispatchEvent(new KeyboardEvent('keydown', { key: %r, bubbles: true }));"


@pytest.fixture
def kinds(monkeypatch):
    """Registers the contest kinds for the build (until they are in the built-in table)."""
    monkeypatch.setitem(C.BUILTIN, "contest_json", "lamptown.collectors.contest:collect")


def build_lab(tmp_path: Path, kind: str = "contest_json", **paths) -> Path:
    if not paths:
        paths = {"glob": str(FIX / "json" / "*.json")}
    lines = []
    for k, v in paths.items():
        if isinstance(v, list):
            lines.append(f"  {k} = [{', '.join(json.dumps(Path(x).as_posix()) for x in v)}]")
        else:
            lines.append(f"  {k} = {json.dumps(Path(v).as_posix())}")
    toml = (
        '[[building]]\nid = "lab"\ntype = "contest"\nlabel = "Idea lab"\n'
        f'  [[building.source]]\n  kind = "{kind}"\n' + "\n".join(lines) + "\n"
    )
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    return B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW).town


def tips_js(ids: str) -> str:
    return (
        "Object.fromEntries(RS.room.targets().filter(t => /^(" + ids + ")/.test(t.id || ''))"
        ".map(t => [t.id, t.tip]))"
    )


@browser
def test_room_unblinds_in_two_steps(tmp_path, kinds):
    r = run(
        build_lab(tmp_path),
        "const a = Object.assign({ plate: RS.room.plate(), tips: "
        + tips_js("final|envelope|lamp")
        + " }, RS.room.seen());"
        + KEY % "u"
        + " await wait(2600); const b = Object.assign({ tips: "
        + tips_js("final|mid")
        + " }, RS.room.seen());"
        " return { a, b, stored: localStorage.getItem('lamptown-contest-unblinded') };",
        frag="#room=lab",
    )
    a, b = r["a"], r["b"]
    assert a["plate"] == {"num": "8", "state": "waiting"} and a["title"] == "Cafe names"
    assert a["step"] == "unblind" and a["envelope"] == "sealed" and a["lamp"] == 0
    assert a["finals"] == ["C03", "C05"] and a["rosettes"] == ["C03"] and a["placebo"] == []
    assert a["mid"] == 6 and a["tops"] == 0 and a["legend"] == ["= RANK", "ROUND 1", "FULL=1ST"]
    assert a["crates"] == 2 and a["word"] == "NEEDS YOU"
    assert a["tips"]["final-0"][0] == "C03 (NAME SEALED)"
    assert a["tips"]["envelope"][0] == "UNBLIND?"
    assert "NOT YOUR PICK" in a["tips"]["lamp"][1]
    # step two: names on hover, the placebo shows, PICK ONE tags; still no pick, the lamp stays off
    assert (
        b["step"] == "pick" and b["envelope"] == "open:?" and b["pickOne"] == 2 and b["lamp"] == 0
    )
    assert b["placebo"] == ["C06"] and b["state"] == "waiting"
    assert b["tips"]["final-0"][0] == "C03: Rain Table"
    assert "PICK ONE: RECORD IT IN THE CONTEST FILE" in b["tips"]["final-0"]
    placebo = next(t for t in b["tips"].values() if t[0].startswith("C06"))
    assert any(x.startswith("PLACEBO: THE CONTROL") for x in placebo)
    assert json.loads(r["stored"]) == ["lab|cafe-names|2026-03-12"]


@browser
def test_older_contest_from_a_crate_is_picked_under_the_lamp(tmp_path, kinds):
    r = run(
        build_lab(tmp_path),
        "const t = RS.room.targets().find(x => x.id === 'crate-1'); const tip = t.tip; t.onClick();"
        " await wait(300); return Object.assign({ tip, plate: RS.room.plate() }, RS.room.seen());",
        frag="#room=lab",
    )
    assert r["tip"][0] == "Kite colours" and r["tip"][2] == "PICK: Dusk Violet"
    assert r["title"] == "Kite colours" and r["onBench"] == 2 and r["state"] == "waiting"
    assert r["plate"]["state"] == "waiting"  # the room stays waiting: the newest still needs you
    assert r["lamp"] == 1 and r["pickTag"] == ["DUSK VIOLET"] and r["envelope"] == "open:PICK"
    assert r["finals"] == ["K03", "K02"] and r["rosettes"] == ["K02"] and r["stars"] == ["K06"]
    assert r["legend"] == ["= SCORE", "FINAL", "FULL=9"] and r["word"] == "PICKED"
    assert r["placebo"] == ["K05"] and r["tops"] == 2


@browser
def test_judges_still_out(tmp_path, kinds):
    tea = {
        "title": "Tea flavours",
        "date": "2026-03-08",
        "entries": [{"code": f"T{i}", "name": f"Tea {i}"} for i in range(1, 5)],
        "judges": {"expected": 3},
        "rankings": {"A": ["T2", "T1", "T4", "T3"]},
    }
    write(tmp_path, tea, "tea.json")
    (tmp_path / "kite.json").write_bytes((FIX / "json" / "kite-colours.json").read_bytes())
    r = run(build_lab(tmp_path, glob=str(tmp_path / "*.json")), SEEN, frag="#room=lab")
    assert r["state"] == "working" and r["judges"] == [1, 3] and r["word"] == "JUDGING"
    assert r["say"] == "JUDGES ARE STILL RANKING: 1 OF 3 DONE"
    assert r["finals"] == [] and r["tops"] == 4 and r["envelope"] is None and r["crates"] == 1


@browser
def test_no_contest_is_missing(tmp_path, kinds):
    r = run(build_lab(tmp_path, glob=str(tmp_path / "*.json")), SEEN, frag="#room=lab")
    assert r["state"] == "missing" and r["plate"] == {"num": "0", "state": "missing"}
    assert r["say"] == "NO CONTEST FILE FOUND YET" and r["crates"] == 0


@browser
def test_unreadable_contest_is_a_red_notice(tmp_path, kinds):
    write(tmp_path, "{oops", "bad.json")
    r = run(build_lab(tmp_path, glob=str(tmp_path / "*.json")), SEEN, frag="#room=lab")
    assert r["state"] == "error" and r["say"] == "CAN'T READ THE FILE" and r["lamp"] == 0


@browser
@pytest.mark.parametrize(
    ("state", "check"),
    [
        (
            "ok",
            lambda r: (
                r["lamp"] == 1 and r["pickTag"] == ["SALT HALO"] and r["envelope"] == "open:PICK"
            ),
        ),
        (
            "waiting",
            lambda r: r["step"] == "unblind" and r["envelope"] == "sealed" and r["placebo"] == [],
        ),
        ("pick", lambda r: r["step"] == "pick" and r["pickOne"] == 3 and r["placebo"] == ["C08"]),
        ("working", lambda r: r["judges"] == [1, 3] and r["finals"] == [] and r["tops"] == 19),
        ("error", lambda r: r["say"] == "CAN'T READ THE SCORES" and r["finals"] == []),
        ("missing", lambda r: r["say"] == "NO CONTEST FILE FOUND YET"),
    ],
)
def test_demo_states(tmp_path, kinds, state, check):
    # the pick's label turns on entry: read the room once it has settled
    r = run(
        build_lab(tmp_path),
        "await wait(1800); " + SEEN,
        query=f"?t=300&demo=1&state={state}",
        frag="#room=lab",
    )
    assert r["sample"] is True and r["caption"]
    want = "waiting" if state == "pick" else state
    assert r["state"] == want and r["plate"]["state"] == want
    assert check(r), r


@browser
def test_demo_unblinds_then_a_click_picks(tmp_path, kinds):
    r = run(
        build_lab(tmp_path),
        # wait on what the room shows, not on the clock: the demo's own step unblinds; the click
        # is the viewer's, so the script stops and the lamp is read once it has turned on
        "const until = async (f, ms) => { const t0 = performance.now();"
        " while (!f()) { if (performance.now() - t0 > ms) return false; await wait(50); }"
        " return true; };"
        " const a = RS.room.seen();"
        " const unblinded = await until(() => RS.room.seen().pickOne > 0, 4000);"
        " const b = RS.room.seen();"
        " RS.room.targets().find(t => t.id === 'final-1').onClick();"
        " const lit = await until(() => RS.room.seen().lamp === 1, 3000);"
        " await wait(1000); return { a, b, unblinded, lit, c: RS.room.seen() };",
        query="?demo=1",
        frag="#room=lab",
    )
    assert r["a"]["title"] == "SAMPLE: LIGHTHOUSE NAMES" and r["a"]["step"] == "unblind"
    assert r["b"]["step"] == "pick" and r["b"]["placebo"] == ["C08"]
    assert r["unblinded"] and r["lit"]
    assert r["c"]["state"] == "ok" and r["c"]["lamp"] == 1 and r["c"]["pickTag"] == ["SALT HALO"]
    assert r["c"]["caption"] == ""  # the viewer took over: the script no longer runs


@browser
def test_demo_scale_limits(tmp_path, kinds):
    r = run(
        build_lab(tmp_path), SEEN, query="?t=300&demo=1&entries=80&state=pick", frag="#room=lab"
    )
    assert r["tops"] == 23 and r["topMore"] > 0  # 24 slots, the last says +N
    assert r["mid"] == 14 and r["midMore"] > 0  # 15 small bottles, the last says +N
    assert len(r["finals"]) == 3 and r["crates"] == 2
