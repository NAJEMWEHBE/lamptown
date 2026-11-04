"""The ledger room (the two-signature register): the ``scan_glob`` collector on fixtures, and the
room itself in headless Chrome (each state's object, tooltips, pages and scale limits)."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from pathlib import Path

import pytest

from lamptown import build as B
from lamptown import collectors as C
from lamptown.collectors import ledger as L
from lamptown.collectors.schema import room_schema, validate
from lamptown.config import parse_config

FIX = Path(__file__).parent / "fixtures" / "ledger"
NOW = dt.datetime(2026, 3, 14, 22, 0, tzinfo=dt.UTC)


def fixture_paths(**more) -> dict:
    p = {
        "glob": [str(FIX / "scans" / "*.json")],
        "own_glob": str(FIX / "own-scans" / "*.json"),
        "installed_dir": str(FIX / "installed"),
        "own_dir": str(FIX / "staging"),
        "decisions_file": str(FIX / "decisions.json"),
    }
    p.update(more)
    return p


def schema_ok(data: dict) -> None:
    errs = validate(data, room_schema("ledger"))
    assert errs == [], errs


def by_tool(r: dict) -> dict:
    return {x["tool"]: x for x in r["rows"]}


# ----------------------------------------------------------------------------- the collector
def test_fixture_rows_get_their_kinds():
    r = L.collect(fixture_paths(), NOW)
    schema_ok(r)
    kinds = {t: x["kind"] for t, x in by_tool(r).items()}
    assert kinds == {
        "kite-knots": "signed",
        "harbour-lights": "signed",
        "gull-counter": "waiting",
        "paper-fold": "waiting",
        "sail-mender": "norecord",
        "tide-reader": "skipped-in",
        "rope-ladder": "unreadable",
        "lantern-check": "noscan",
        "moss-indexer": "homemade",
        "otter-notes": "unscanned",
        "odd-call": "unscanned",
    }
    assert r["state"] == "error" and r["label"] == "3 stopped at the gate"
    assert r["installed"] == 5 and r["synced"] == 2
    assert r["decisions"]["recorded"] == 6 and sorted(r["decisions"]["bad"]) == [
        "bad-entry",
        "odd-call",
    ]
    assert [n["text"] for n in r["needs"]] == [
        "Read the scan of paper-fold and record your call",
        "Read the scan of gull-counter and record your call",
    ]


def test_signatures_scans_and_history():
    rows = by_tool(L.collect(fixture_paths(), NOW))
    kite = rows["kite-knots"]
    assert (
        kite["scan"]["verdict"] == "SAFE"
        and kite["scan"]["risk"] == 8
        and kite["scan"]["flags"] == 0
    )
    assert kite["scan"]["scanner"] == "SkillSpector" and kite["scans"] == 2
    assert [h["verdict"] for h in kite["history"]] == [
        "CAUTION",
        "SAFE",
    ]  # oldest first; newest wins
    assert kite["decision"] == {
        "call": "install",
        "date": "2026-03-01",
        "note": "Read every flag, all fine",  # the pixel font has no ';'
        "source": "https://example.invalid/kite",
    }
    assert kite["origin"] == "third-party" and kite["installed"] is True
    gull = rows["gull-counter"]  # the generic format: list of flags, a +01:00 time made UTC
    assert gull["scan"]["word"] == "CAUTION" and gull["scan"]["flags"] == 2
    assert (
        gull["scan"]["when"] == "2026-03-10T11:15+00:00"
        and gull["scan"]["scanner"] == "Sample Scanner"
    )
    sail = rows["sail-mender"]  # 'pass' + 'score', and the tool name from the file
    assert sail["scan"]["verdict"] == "SAFE" and sail["scan"]["risk"] == 5
    assert rows["harbour-lights"]["scan"]["word"] == "DO NOT INSTALL"
    assert rows["paper-fold"]["origin"] == "own" and rows["paper-fold"]["scan"]["src"] == "own"
    assert (
        rows["rope-ladder"]["scan"]["verdict"] is None and rows["rope-ladder"]["state"] == "error"
    )
    assert rows["moss-indexer"]["state"] == "missing"  # home-made, no scan: grey, never red
    assert (
        rows["lantern-check"]["origin"] == "unknown" and rows["lantern-check"]["state"] == "error"
    )


def test_rows_newest_first_and_times_absolute():
    r = L.collect(fixture_paths(), NOW)
    whens = [x["when"] for x in r["rows"] if x["scan"] and x["scan"].get("verdict")]
    assert whens == sorted(whens, reverse=True)
    for x in r["rows"]:
        assert x["when"] is None or x["when"].endswith("+00:00")


def test_a_scan_still_being_written_is_working_then_unreadable(tmp_path):
    (tmp_path / "fog-horn.json").write_text("", encoding="utf-8")
    mt = dt.datetime.fromtimestamp((tmp_path / "fog-horn.json").stat().st_mtime, tz=dt.UTC)
    r = L.collect({"glob": str(tmp_path / "*.json")}, mt + dt.timedelta(minutes=2))
    assert r["rows"][0]["kind"] == "scanning" and r["state"] == "working"
    assert r["label"] == "1 being scanned"
    r = L.collect({"glob": str(tmp_path / "*.json")}, mt + dt.timedelta(minutes=30))
    assert r["rows"][0]["kind"] == "unreadable" and r["rows"][0]["scan"]["why"] == "empty file"
    r = L.collect(
        {"glob": str(tmp_path / "*.json"), "scanning_minutes": 60}, mt + dt.timedelta(minutes=30)
    )
    assert r["rows"][0]["kind"] == "scanning"


def test_bad_decisions_file_turns_the_room_red(tmp_path):
    f = tmp_path / "calls.json"
    f.write_text('{"tools": [1, 2]}', encoding="utf-8")
    r = L.collect(fixture_paths(decisions_file=str(f)), NOW)
    schema_ok(r)
    assert r["state"] == "error" and r["label"] == "Can't read your decisions file"
    assert r["decisions"]["error"] and r["needs"][0]["text"].startswith("Fix the decisions file")
    assert by_tool(r)["kite-knots"]["kind"] == "norecord"  # no calls could be read


def test_nothing_set_up_is_missing(tmp_path):
    r = L.collect({}, NOW)
    schema_ok(r)
    assert r["state"] == "missing" and r["rows"] == [] and r["label"] == "No scans found"
    assert "needs" not in r
    r = L.collect(
        {"glob": str(tmp_path / "*.json"), "decisions_file": str(tmp_path / "x.json")}, NOW
    )
    assert r["state"] == "missing" and r["decisions"]["present"] is False


def test_only_grey_rows_stay_missing(tmp_path):
    d = tmp_path / "inst" / "home-brew"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: home-brew\ndescription: x\n---\n", encoding="utf-8")
    (tmp_path / "staging" / "home-brew").mkdir(parents=True)
    r = L.collect(
        {"installed_dir": str(tmp_path / "inst"), "own_dir": str(tmp_path / "staging")}, NOW
    )
    assert r["rows"][0]["kind"] == "homemade" and r["state"] == "missing"


def test_all_signed_is_ok(tmp_path):
    (tmp_path / "scans").mkdir()
    (tmp_path / "scans" / "a.json").write_text(
        json.dumps({"tool": "anchor", "verdict": "SAFE", "risk": 2, "flags": 0}), encoding="utf-8"
    )
    (tmp_path / "calls.json").write_text(
        json.dumps({"tools": {"anchor": {"call": "skip", "date": "2026-03-01"}}}), encoding="utf-8"
    )
    r = L.collect(
        {
            "glob": str(tmp_path / "scans" / "*.json"),
            "decisions_file": str(tmp_path / "calls.json"),
        },
        NOW,
    )
    rows = [x for x in r["rows"] if x["tool"] == "anchor"]
    assert rows[0]["kind"] == "signed" and r["label"] == "All 1 signed twice"


def test_format_generic_does_not_read_skillspector():
    r = L.collect({"glob": str(FIX / "scans" / "kite-knots.json"), "format": "generic"}, NOW)
    assert r["rows"][0]["kind"] == "unreadable"
    r = L.collect({"glob": str(FIX / "scans" / "gull-counter.json"), "format": "skillspector"}, NOW)
    assert r["rows"][0]["kind"] == "unreadable"


def test_naive_scan_time_falls_back_to_the_file_time(tmp_path):
    p = tmp_path / "pier-clock.json"
    p.write_text(
        json.dumps({"verdict": "warn", "risk": 250, "flags": -3, "scanned_at": "2026-03-01T10:00"}),
        encoding="utf-8",
    )
    os.utime(p, (1_700_000_000, 1_700_000_000))
    s = L.read_scan(p, NOW)
    assert s["verdict"] == "CAUTION" and s["risk"] == 100 and s["flags"] == 0
    assert s["when"] == "2023-11-14T22:13+00:00"


@pytest.mark.parametrize(
    ("word", "verdict"),
    [
        ("safe", "SAFE"),
        ("Do not install", "DO_NOT_INSTALL"),
        ("block", "DO_NOT_INSTALL"),
        ("needs-human", "NEEDS_HUMAN"),
        ("", None),
        (None, None),
    ],
)
def test_verdict_words(word, verdict):
    assert L.verdict_of(word) == verdict


@pytest.mark.parametrize(
    ("stem", "tool"),
    [
        ("kite-knots", "kite-knots"),
        ("Kite-Knots-r2", "kite-knots"),
        ("kite-knots.scan", "kite-knots"),
    ],
)
def test_tool_name_from_file(stem, tool):
    assert L.tool_name(stem) == tool


def test_odd_files_never_crash(tmp_path):
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "bin.json").write_bytes(b"\xff\xfe\x00garbage")
    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "x.json").write_text("{}", encoding="utf-8")
    r = L.collect({"glob": str(tmp_path / "**" / "*.json")}, NOW)
    assert {x["kind"] for x in r["rows"]} == {"unreadable"} and len(r["rows"]) == 3


# ----------------------------------------------------------------------------- in the browser
from _browser import browser  # noqa: E402  (shared headless browser)
from test_town import run  # noqa: E402  (shared headless-Chrome probe)

SEEN = "return Object.assign({ id: RS.id, plate: RS.room.plate() }, RS.room.seen());"


@pytest.fixture
def kinds(monkeypatch):
    """Registers the ledger kind for the build (until it is in the built-in table)."""
    monkeypatch.setitem(C.BUILTIN, "scan_glob", "lamptown.collectors.ledger:collect")


def fixed_copy(tmp_path: Path) -> dict:
    """The fixtures copied with one fixed file time, so rows without a scan time sort the same
    on every machine."""
    root = tmp_path / "fx"
    shutil.copytree(FIX, root)
    t = dt.datetime(2026, 3, 13, tzinfo=dt.UTC).timestamp()
    for p in [root, *root.rglob("*")]:
        os.utime(p, (t, t))
    return {
        "glob": [str(root / "scans" / "*.json")],
        "own_glob": str(root / "own-scans" / "*.json"),
        "installed_dir": str(root / "installed"),
        "own_dir": str(root / "staging"),
        "decisions_file": str(root / "decisions.json"),
    }


def build_ledger(tmp_path: Path, paths: dict | None = None) -> Path:
    paths = fixed_copy(tmp_path) if paths is None else paths
    lines = []
    for k, v in paths.items():
        if isinstance(v, list):
            lines.append(f"  {k} = [{', '.join(json.dumps(Path(x).as_posix()) for x in v)}]")
        else:
            lines.append(f"  {k} = {json.dumps(Path(v).as_posix() if isinstance(v, str) else v)}")
    toml = (
        '[[building]]\nid = "gate"\ntype = "ledger"\nlabel = "Vetting gate"\n'
        '  [[building.source]]\n  kind = "scan_glob"\n' + "\n".join(lines) + "\n"
    )
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    return B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW).town


@browser
def test_room_on_fixtures(tmp_path, kinds):
    page = build_ledger(tmp_path)
    r = run(
        page,
        """const s = RS.room.seen();
      const tips = Object.fromEntries(allTargets().filter(t => /^(scan|you)-/.test(t.id || ''))
        .map(t => [t.id, t.tip]));
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      await wait(800);
      return Object.assign({ id: RS.id, plate: RS.room.plate(), tips, p2: RS.room.seen() }, s);""",
        frag="#room=gate",
    )
    assert r["id"] == "gate" and r["plate"] == {"num": "11", "state": "error"}
    assert r["notice"] == ["error", "1 SCAN FILE CAN'T BE READ", "ROPE-LADDER"]
    assert r["rows"][0] == ["rope-ladder", "unreadable"] and len(r["rows"]) == 5
    assert (r["page"], r["pages"]) == (1, 3)
    assert r["locks"] == {"scan": "forced", "you": "shut"}  # something got past; two wait for you
    assert r["ribbons"] == [[1, "error"], [2, "error"]]
    assert r["bottles"] == 5 and r["bottlesMore"] == 0 and r["crate"] is True
    assert r["deskBottles"] == 2 and r["deskMore"] == 0 and r["lamp"] is True
    first_waiting = next(i for i, row in enumerate(r["rows"]) if row[1] == "waiting")
    tip = r["tips"][f"scan-{first_waiting}"]
    assert tip[0].startswith("paper-fold (home-made")
    assert tip[1].startswith("Scanner: SAFE, risk 3 of 100")
    assert "record your call" in tip[2]
    assert r["p2"]["page"] == 2 and r["p2"]["rows"][0][0] == "otter-notes"


@browser
def test_bad_decisions_file_forces_your_lock(tmp_path, kinds):
    f = tmp_path / "calls.json"
    f.write_text("{oops", encoding="utf-8")
    r = run(build_ledger(tmp_path, fixture_paths(decisions_file=str(f))), SEEN, frag="#room=gate")
    assert r["notice"][1] == "CAN'T READ YOUR DECISIONS" and r["notice"][2] == "FIX CALLS.JSON"
    assert r["locks"]["you"] == "forced" and r["state"] == "error"


@browser
def test_no_scans_is_a_dark_blank_ledger(tmp_path, kinds):
    r = run(build_ledger(tmp_path, {"glob": str(tmp_path / "*.json")}), SEEN, frag="#room=gate")
    assert r["state"] == "missing" and r["lamp"] is False and r["rows"] == []
    assert r["locks"] == {"scan": "no key", "you": "no key"}
    assert r["notice"][1] == "NO SCANS FOUND -" and r["plate"]["num"] == "0"


@browser
@pytest.mark.parametrize(
    ("state", "check"),
    [
        (
            "ok",
            lambda r: r["locks"] == {"scan": "turned", "you": "turned"} and r["deskBottles"] == 0,
        ),
        ("waiting", lambda r: r["locks"]["you"] == "shut" and r["rows"][0][1] == "waiting"),
        ("working", lambda r: r["locks"]["scan"] == "turning" and r["rows"][0][1] == "scanning"),
        ("error", lambda r: r["locks"]["scan"] == "forced" and r["rows"][0][1] == "noscan"),
        ("missing", lambda r: r["lamp"] is False and r["rows"] == []),
        ("norecord", lambda r: r["state"] == "missing" and r["locks"]["you"] == "no key"),
    ],
)
def test_demo_states(tmp_path, kinds, state, check):
    r = run(build_ledger(tmp_path), SEEN, query=f"?t=300&demo=1&state={state}", frag="#room=gate")
    assert r["sample"] is True and r["caption"]
    want = "missing" if state == "norecord" else state
    assert r["state"] == want and r["plate"]["state"] == want
    assert check(r), r


@browser
def test_demo_signs_the_waiting_row(tmp_path, kinds):
    r = run(
        build_ledger(tmp_path),
        """const a = RS.room.seen(); await wait(3000); const b = RS.room.seen();
      return { a, b };""",
        query="?demo=1",
        frag="#room=gate",
    )
    assert r["a"]["signing"] == "waiting" and r["a"]["locks"]["you"] == "shut"
    assert r["b"]["signing"] == "signing" and r["b"]["caption"] == "YOU READ THE FLAGS AND SIGN"


@browser
def test_demo_scale_pages_ribbons_and_bottles(tmp_path, kinds):
    r = run(
        build_ledger(tmp_path),
        SEEN,
        query="?t=300&demo=1&state=ok&rows=60&page=4",
        frag="#room=gate",
    )
    assert r["pages"] == 13 and r["page"] == 4 and len(r["rows"]) == 5
    assert (
        r["bottles"] == 21 and r["bottlesMore"] > 0
    )  # 22 slots beside the crate, the last says +N
    assert r["ribbons"] == []
