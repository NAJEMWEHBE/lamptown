"""The shelf room (the flag locker): the ``skill_dirs`` / ``staging_dir`` collectors on fixtures,
and the room itself in headless Chrome (what each state draws, tooltips, scale limits)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from lamptown import build as B
from lamptown import collectors as C
from lamptown.collectors import shelf as S
from lamptown.collectors.schema import room_schema, validate
from lamptown.config import parse_config

FIX = Path(__file__).parent / "fixtures" / "shelf"
NOW = dt.datetime(2026, 3, 14, 22, 0, tzinfo=dt.UTC)


def fixture_paths(**more) -> dict:
    p = {
        "dirs": [str(FIX / "skills")],
        "staging_dir": str(FIX / "staging"),
        "shortnames_file": str(FIX / "plates.json"),
    }
    p.update(more)
    return p


def schema_ok(data: dict) -> None:
    errs = validate(data, room_schema("shelf"))
    assert errs == [], errs


# ----------------------------------------------------------------------------- the collector
def test_fixture_folders_become_rows_in_stage_order():
    r = S.collect(fixture_paths(), NOW)
    schema_ok(r)
    assert r["state"] == "error" and r["label"] == "2 can't be read"
    assert r["counts"] == {"installed": 5, "broken": 2, "staged": 1, "building": 1, "synced": 3}
    stages = [x["stage"] for x in r["rows"]]
    assert stages == sorted(stages, key=["installed", "broken", "staged", "building"].index)
    by = {x["id"]: x for x in r["rows"]}
    assert by["kite-knots"]["short"] == "KITE"
    assert by["kite-knots"]["line"] == "Tie the six knots a paper kite needs"
    assert by["lantern-check"]["short"] == "LAMPS"  # plate override, plain string
    assert by["paper-fold"]["short"] == "BOAT"  # override with its own line
    assert by["paper-fold"]["line"] == "A paper boat from one sheet"
    assert by["old-scraps"]["stage"] == "broken" and by["half-note"]["stage"] == "broken"
    assert by["wren-sketch"]["stage"] == "staged" and by["wren-sketch"]["state"] == "waiting"
    assert by["otter-notes"]["stage"] == "building" and by["otter-notes"]["state"] == "working"
    # installed wins: the copy in staging is not listed again, nor hidden/underscore folders
    assert [x["id"] for x in r["rows"]].count("tide-reader") == 1
    assert not any(x["id"].startswith((".", "_")) for x in r["rows"])
    assert "synced" not in by  # a bundle is a chest, never an item to fix
    assert r["bundles"][0]["count"] == 3 and len(r["bundles"][0]["names"]) == 3
    flags = {(x["flag"]["pat"], x["flag"]["a"], x["flag"]["b"]) for x in r["rows"]}
    assert len(flags) == len(r["rows"])  # no two flags alike
    assert all(x["href"].startswith("file:") for x in r["rows"])


def test_folded_description_block_is_read():
    r = S.collect(fixture_paths(), NOW)
    moss = next(x for x in r["rows"] if x["id"] == "moss-indexer")
    assert moss["stage"] == "installed" and moss["line"] and "\n" not in moss["line"]


def test_flags_and_plates_do_not_follow_the_clock():
    a = S.collect(fixture_paths(), NOW)
    b = S.collect(fixture_paths(), NOW + dt.timedelta(days=40))
    assert a == b


def test_no_folder_is_missing_and_no_dirs_says_how_to_set_up(tmp_path):
    r = S.collect({"dirs": [str(tmp_path / "nope")]}, NOW)
    schema_ok(r)
    assert r["state"] == "missing" and r["found"] is False and r["rows"] == []
    assert r["label"] == "Can't see the skills folder"
    r = S.collect({}, NOW)
    assert r["state"] == "missing" and "dirs = [...]" in r["label"]


def test_empty_folder_is_ok_with_nothing_installed(tmp_path):
    r = S.collect({"dir": str(tmp_path), "noun": "plugin"}, NOW)
    assert r["state"] == "ok" and r["label"] == "No plugins installed yet" and r["noun"] == "plugin"


def test_all_installed_is_ok(tmp_path):
    for n in ("alpha-tool", "beta-tool"):
        (tmp_path / n).mkdir()
        (tmp_path / n / "SKILL.md").write_text(
            f"---\nname: {n}\ndescription: Use when you need to test {n}.\n---\n", encoding="utf-8"
        )
    r = S.collect({"dirs": [str(tmp_path)]}, NOW)
    schema_ok(r)
    assert r["state"] == "ok" and r["label"] == "All 2 installed"
    assert r["rows"][0]["line"] == "Test alpha-tool"  # the 'Use when you need to' lead-in is gone


def test_json_manifest_and_bad_noun(tmp_path):
    d = tmp_path / "harbour-lights"
    d.mkdir()
    (d / "plugin.json").write_text(
        json.dumps({"name": "harbour-lights", "description": "Dim the harbour lights at dawn."}),
        encoding="utf-8",
    )
    (tmp_path / "torn").mkdir()
    (tmp_path / "torn" / "plugin.json").write_text("{not json", encoding="utf-8")
    r = S.collect({"dir": str(tmp_path), "manifest": "plugin.json", "noun": "<b>x"}, NOW)
    assert r["noun"] == "skill"  # an odd noun falls back
    by = {x["id"]: x for x in r["rows"]}
    assert by["harbour-lights"]["stage"] == "installed"
    assert by["harbour-lights"]["line"] == "Dim the harbour lights at dawn"
    assert by["torn"]["stage"] == "broken"


def test_manifest_with_a_path_is_ignored(tmp_path):
    assert S.collect({"dir": str(tmp_path), "manifest": "../x.md"}, NOW)["manifest"] == "SKILL.md"


def test_bundle_counted_from_sub_folders_when_manifest_has_no_list(tmp_path):
    b = tmp_path / "mirror"
    (b / "one").mkdir(parents=True)
    (b / "two").mkdir()
    (b / "manifest.json").write_text("{}", encoding="utf-8")
    for n in ("one", "two"):
        (b / n / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\n", encoding="utf-8")
    r = S.collect({"dir": str(tmp_path)}, NOW)
    assert r["rows"] == [] and r["bundles"][0]["count"] == 2 and r["counts"]["synced"] == 2


def test_overrides_file_odd_inputs(tmp_path):
    f = tmp_path / "plates.json"
    f.write_text('{"_note": "x", "a": "AA", "b": {"short": 5}, "c": 3}', encoding="utf-8")
    assert S.load_overrides(f) == {"a": {"short": "AA"}, "b": {}}
    f.write_text("[1, 2]", encoding="utf-8")
    assert S.load_overrides(f) == {}
    f.write_text("{bad", encoding="utf-8")
    assert S.load_overrides(f) == {}
    assert S.load_overrides(tmp_path / "none.json") == {}


@pytest.mark.parametrize(
    ("folder", "plate"),
    [
        ("tide-reader", "TIDE"),
        ("the-big-kettle", "KETTLE"),  # 'the' is filler; BIG + KETTLE would not fit a plate
        ("ox-cart", "OXCART"),  # a short word glued to the next
        ("supercalifragilistic", "SUPERCA"),
        ("my-skill", "MYSKILL"),  # only filler words: they are used after all
    ],
)
def test_short_names(folder, plate):
    assert S.short_name(folder) == plate


def test_plain_line_cuts_at_a_word():
    s = S.plain_line("Use this skill when you want to " + "count the gulls on the long pier " * 4)
    assert len(s) <= S.LINE_MAX and s.endswith("...") and s.startswith("Count the gulls")


def test_staging_only_kind(tmp_path):
    r = S.collect_staging({"dir": str(FIX / "staging")}, NOW)
    schema_ok(r)
    assert r["state"] == "waiting"  # staged (waiting) beats building (working)
    assert {x["id"] for x in r["rows"]} == {"wren-sketch", "tide-reader", "otter-notes"}
    r = S.collect_staging({"staging_dir": str(tmp_path / "gone")}, NOW)
    assert r["state"] == "missing"
    r = S.collect_staging({"dir": str(tmp_path)}, NOW)
    assert r["state"] == "ok" and r["label"] == "Nothing in staging"


def test_many_folders_stay_within_the_row_cap(tmp_path):
    for i in range(30):
        (tmp_path / f"item-{i:02d}").mkdir()
    r = S.collect({"dir": str(tmp_path)}, NOW)
    assert len(r["rows"]) == 30 and r["counts"]["broken"] == 30 and r["state"] == "error"


# ----------------------------------------------------------------------------- through the build
def shelf_toml(dirs: Path, staging: Path | None = None, extra: str = "") -> str:
    st = f'\n  staging_dir = "{staging.as_posix()}"' if staging else ""
    return f"""
[[building]]
id = "skills"
type = "shelf"
label = "Flag locker"
  [[building.source]]
  kind = "skill_dirs"
  dirs = ["{dirs.as_posix()}"]{st}
  shortnames_file = "{(FIX / "plates.json").as_posix()}"
{extra}
"""


@pytest.fixture
def kinds(monkeypatch):
    """Registers the shelf kinds for the build (until they are in the built-in table)."""
    monkeypatch.setitem(C.BUILTIN, "skill_dirs", "lamptown.collectors.shelf:collect")
    monkeypatch.setitem(C.BUILTIN, "staging_dir", "lamptown.collectors.shelf:collect_staging")


def build_shelf(tmp_path: Path, toml: str) -> Path:
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    return B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW).town


def town_data(page: Path) -> dict:
    t = page.read_text(encoding="utf-8")
    i = t.index('<script id="town-data" type="application/json">')
    j = t.index("</script>", i)
    return json.loads(t[t.index(">", i) + 1 : j])


def test_build_inlines_the_room_and_its_data(tmp_path, kinds):
    page = build_shelf(tmp_path, shelf_toml(FIX / "skills", FIX / "staging"))
    text = page.read_text(encoding="utf-8")
    assert "registerRoom('shelf'" in text
    d = town_data(page)
    room = d["rooms"]["skills"]
    assert room["state"] == "error" and room["counts"]["installed"] == 5
    assert "skills" in d["rooms"]


# ----------------------------------------------------------------------------- in the browser
from _browser import browser  # noqa: E402  (shared headless browser)
from test_town import run  # noqa: E402  (shared headless-Chrome probe)

SEEN = "return Object.assign({ id: RS.id, mode: RS.mode, plate: RS.room.plate() }, RS.room.seen());"


@browser
def test_room_on_fixtures_draws_each_stage(tmp_path, kinds):
    page = build_shelf(tmp_path, shelf_toml(FIX / "skills", FIX / "staging"))
    r = run(
        page,
        """const s = RS.room.seen();
      const tips = allTargets().filter(t => t.id.startsWith('item-')).map(t => [t.id, t.tip]);
      return Object.assign({ id: RS.id, plate: RS.room.plate(), tips }, s);""",
        frag="#room=skills",
    )
    assert (r["id"], r["state"], r["sample"]) == ("skills", "error", False)
    assert r["plate"] == {"num": "5", "state": "error"}
    assert r["cubbies"]["installed"] == 5 and r["cubbies"]["broken"] == 2
    assert r["cubbies"]["staged"] == 1 and r["baskets"] == 1  # an empty cubby + its basket
    assert r["bench"] == 1 and r["workLamp"] is True  # the half-sewn flag under the cyan lamp
    assert r["chest"] is True and r["chestCount"] == 3
    assert r["padlocked"] is False and r["cols"] == 4
    tips = dict(r["tips"])
    assert tips["item-installed-kite-knots"][0] == "kite-knots: installed"
    assert tips["item-installed-kite-knots"][1] == "Tie the six knots a paper kite needs"
    assert tips["item-broken-old-scraps"][0] == "old-scraps: can't read"
    assert tips["item-staged-wren-sketch"][0].endswith("waiting for you to vet")
    assert tips["item-building-otter-notes"][0] == "otter-notes: being built"


@browser
def test_hovering_a_cubby_runs_its_flag_up_with_its_tag(tmp_path, kinds):
    page = build_shelf(tmp_path, shelf_toml(FIX / "skills"))
    r = run(
        page,
        """const i = allTargets().findIndex(t => t.id.startsWith('item-'));
      const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true });
      for (let k = 0; k <= i; k++) document.dispatchEvent(tab);
      const focused = allTargets()[RS.focus].id;
      await wait(2600);
      return Object.assign({ focused }, RS.room.seen());""",
        query="?motion=1&t=300",
        frag="#room=skills",
    )
    assert r["focused"].startswith("item-installed-")
    name = r["focused"].removeprefix("item-installed-")
    assert r["hoisting"] == name and r["hoist"] == "flying" and r["tag"] == name


@browser
def test_missing_folder_padlocks_the_locker(tmp_path, kinds):
    page = build_shelf(tmp_path, shelf_toml(tmp_path / "gone"))
    r = run(page, SEEN, frag="#room=skills")
    assert r["state"] == "missing" and r["padlocked"] is True and r["found"] is False
    assert r["plate"]["num"] == "-" and r["chest"] is False and r["bench"] == 0


@browser
def test_scale_more_cubby_and_extra_baskets(tmp_path, kinds):
    inst, stg = tmp_path / "inst", tmp_path / "stg"
    for i in range(60):
        d = inst / f"tool-{i:02d}"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: tool-{i:02d}\ndescription: Sample tool {i}.\n---\n", encoding="utf-8"
        )
    for i in range(11):
        d = stg / f"new-{i:02d}"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: n\ndescription: d\n---\n", encoding="utf-8")
    for i in range(7):
        (stg / f"wip-{i}").mkdir()
    page = build_shelf(tmp_path, shelf_toml(inst, stg))
    r = run(page, SEEN, frag="#room=skills")
    assert r["cols"] == 8 and r["rows"] == 6  # 48 cubbies at most
    assert r["cubbies"]["more"] == 1 and r["baskets"] == 8 and r["extraStaged"] == 3
    shown = r["cubbies"]["installed"]
    assert shown + r["more"] == 60 and shown + 1 + 8 <= 48
    assert r["bench"] == 5 and r["benchMore"] == 2
    assert r["plate"]["num"] == "60" and r["state"] == "waiting"


@browser
@pytest.mark.parametrize(
    ("state", "check"),
    [
        ("ok", lambda r: r["cubbies"]["installed"] >= 8 and r["baskets"] == 0 and r["bench"] == 0),
        ("waiting", lambda r: r["baskets"] == 2 and r["cubbies"]["staged"] == 2),
        ("working", lambda r: r["bench"] == 1 and r["workLamp"]),
        ("error", lambda r: r["cubbies"]["broken"] == 1),
        ("missing", lambda r: r["padlocked"] and r["plate"]["num"] == "-"),
    ],
)
def test_demo_shows_each_state_with_sample_flags(tmp_path, kinds, state, check):
    page = build_shelf(tmp_path, shelf_toml(tmp_path / "gone"))
    r = run(page, SEEN, query=f"?t=300&demo=1&state={state}", frag="#room=skills")
    assert r["sample"] is True and r["state"] == state
    assert check(r), r


@browser
def test_demo_item_count_and_keys(tmp_path, kinds):
    page = build_shelf(tmp_path, shelf_toml(tmp_path / "gone"))
    r = run(
        page,
        """const a = RS.room.seen();
      document.dispatchEvent(new KeyboardEvent('keydown', { key: '4', bubbles: true }));
      await wait(100);
      return { a, b: RS.room.seen() };""",
        query="?t=300&demo=1&items=80",
        frag="#room=skills",
    )
    assert r["a"]["cols"] == 8 and r["a"]["cubbies"]["more"] == 1 and r["a"]["state"] == "ok"
    assert r["b"]["state"] == "error" and r["b"]["cubbies"]["broken"] == 1
