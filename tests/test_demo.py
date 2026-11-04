"""`lamptown demo`: the fictional world, its time tokens, its states and the command."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import tomllib
from pathlib import Path

import pytest

from lamptown import cli
from lamptown.config import BUILTIN_TYPES, load_config
from lamptown.demo import (
    STATES,
    DemoError,
    build_demo,
    fill,
    fill_name,
    make_world,
    offset,
    package_dir,
    plan,
)

NOW = dt.datetime(2026, 3, 14, 21, 30, tzinfo=dt.timezone(dt.timedelta(hours=1)))
LAMP = {"ok": "green", "working": "green", "waiting": "amber", "error": "red", "missing": "grey"}


# ----------------------------------------------------------------------------- time tokens
@pytest.mark.parametrize(("text", "secs"), [("0", 0), ("-2h", -7200), ("+1d", 86400),
                                            ("-1d30m", -88200), ("-45s", -45)])
def test_offsets(text, secs):
    assert offset(text) == dt.timedelta(seconds=secs)


@pytest.mark.parametrize("bad", ["", "2", "-h", "1y", "-1d-2h", "now"])
def test_bad_offsets_are_refused(bad):
    with pytest.raises(ValueError):
        offset(bad)


def test_tokens_in_text():
    t = fill("a {{-2h}} b {{-1d:date}} c {{-1h:hm}} d {{-2d:dow}} e {{0:epoch}} f {{0:tz}} "
             "g {{0:utc}} h {{date}} i {{-1x}}", NOW)
    assert t == ("a 2026-03-14T19:30:00+01:00 b 2026-03-13 c 20:30 d thu e 1773520200 f +0100 "
                 "g 2026-03-14T20:30:00Z h {{date}} i {{-1x}}")


def test_tokens_in_names():
    assert fill_name("jobs/backup/backup-{{-6h}}.log", NOW) == "jobs/backup/backup-2026-03-14.log"
    assert fill_name("vault/inbox/{{-1d}}.md", NOW) == "vault/inbox/2026-03-13.md"
    assert fill_name("p/dot-git/logs/HEAD", NOW) == "p/.git/logs/HEAD"
    assert fill_name("p/dot-/x", NOW) == "p/dot-/x"


# ----------------------------------------------------------------------------- the world
def _rows(data, bid):
    return data["rooms"][bid].get("rows") or []


@pytest.fixture(scope="module")
def lively(tmp_path_factory):
    return build_demo(tmp_path_factory.mktemp("demo") / "w", now=NOW)


def test_the_world_uses_every_room_type_and_the_promised_cast(lively):
    config = load_config(lively.world / "lamptown.toml")
    assert sorted(b.type for b in config.buildings) == sorted(BUILTIN_TYPES)
    d = lively.data
    jobs = _rows(d, "jobs")
    assert len(jobs) == 4 and all(j.get("schedule") for j in jobs)
    harbour = _rows(d, "harbour")
    projects = [r for r in harbour if r.get("status")]
    agents = [r for r in harbour if r.get("agent")]
    assert [p["name"] for p in projects] == ["Paper Kite", "Moss Engine", "Tidebook"]
    assert sorted(a["agent"] for a in agents) == ["heron", "marten", "otter", "puffin", "wren"]
    assert d["rooms"]["notes"]["notes"] == 40 and d["parts"]["notes"]["num"] == "40"
    assert len(_rows(d, "skills")) == 9
    assert len(_rows(d, "gate")) == 6
    assert len(d["rooms"]["contest"]["entries"]) == 8


def test_the_default_world_is_alive(lively):
    """Some lamps wait for you, one is red, and things run right now."""
    st = {bid: room["state"] for bid, room in lively.data["rooms"].items()}
    assert list(st.values()).count("error") == 1
    assert list(st.values()).count("waiting") >= 2
    assert "working" in st.values()
    assert lively.data["needs"] >= 3
    jobs = {j["name"]: j for j in _rows(lively.data, "jobs")}
    assert jobs["Inbox sorter"]["state"] == "working" and jobs["Inbox sorter"]["feeds"] == "notes"
    kite = next(r for r in _rows(lively.data, "harbour") if r["name"] == "Paper Kite")
    assert kite["busy"] is True and kite["needs_n"] == 2
    assert kite["last_commit"]["msg"] == "Glue the bridle"


def test_world_times_are_relative_to_now(lively):
    """Every absolute time in the data sits within a month of the build moment."""
    seen = []

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and len(v) >= 19 and v[4] == "-" and v[10] == "T":
            seen.append(dt.datetime.fromisoformat(v.replace("Z", "+00:00")))

    walk(lively.data["rooms"])
    assert len(seen) > 20
    assert all(abs(t - NOW) < dt.timedelta(days=31) for t in seen)


@pytest.mark.parametrize("state", STATES)
def test_each_state_lights_every_building_that_way(tmp_path, state):
    res = build_demo(tmp_path / "w", now=NOW, state=state)
    rooms = res.data["rooms"]
    assert {bid: r["state"] for bid, r in rooms.items()} == dict.fromkeys(rooms, state)
    assert {p["lamp"] for p in res.data["parts"].values()} == {LAMP[state]}
    if state in ("ok", "working", "missing"):
        assert res.data["needs"] == 0


def test_the_missing_state_copies_only_the_config(tmp_path):
    cfg = make_world(tmp_path / "w", NOW, "missing")
    names = sorted(p.name for p in cfg.parent.iterdir())
    assert names == [".lamptown-demo", "README.txt", "lamptown.toml"]


def test_file_times_follow_the_rules(tmp_path):
    cfg = make_world(tmp_path / "w", NOW)
    w = cfg.parent
    age = lambda p: NOW.timestamp() - os.stat(w / p).st_mtime  # noqa: E731
    assert age("jobs/sorter/sorter-2026-03-14.log") == pytest.approx(180, abs=2)
    assert age("gate/scans/paper-fold.json") == pytest.approx(120, abs=2)
    assert age("vault/kites/tail-length.md") == pytest.approx(2 * 86400, abs=2)
    assert (w / "projects/paper-kite/.git/logs/HEAD").is_file()


def test_overlays_only_name_world_files_or_add_new_ones():
    """An overlay's .skip must name something in the world, or it silently does nothing."""
    base, _, _ = plan(None)
    for state in STATES:
        over = package_dir("states").joinpath(state)
        if not over.is_dir():
            continue
        stack = [(over, "")]
        while stack:
            node, pre = stack.pop()
            for c in node.iterdir():
                rel = pre + c.name
                if c.is_dir():
                    stack.append((c, rel + "/"))
                elif rel.endswith(".skip"):
                    gone = rel[: -len(".skip")]
                    assert any(k == gone or k.startswith(gone + "/") for k in base), (state, rel)


def test_mtime_rules_parse():
    for state in (None, *STATES):
        _, default, rules = plan(state)
        offset(default)
        for v in rules.values():
            offset(v)


def test_world_json_files_are_valid_after_filling(tmp_path):
    cfg = make_world(tmp_path / "w", NOW)
    bad = []
    for p in cfg.parent.rglob("*.json"):
        if p.name == "paper-fold.json":  # half-written on purpose: a scan in progress
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            bad.append(p.name)
    assert bad == []
    tomllib.loads(cfg.read_text(encoding="utf-8"))


def test_a_fixed_now_gives_the_same_town_twice(tmp_path):
    a = build_demo(tmp_path / "a", now=NOW)
    b = build_demo(tmp_path / "b", now=NOW)

    def norm(res):
        # rev hashes the data as built, folder paths included, so two folders differ there
        data = {k: v for k, v in res.data.items() if k != "rev"}
        return json.dumps(data, sort_keys=True).replace(res.root.as_uri(), "ROOT") \
            .replace(json.dumps(str(res.root))[1:-1], "ROOT")
    assert norm(a) == norm(b)


def test_a_folder_that_is_not_empty_is_refused(tmp_path):
    (tmp_path / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(DemoError, match="not empty"):
        build_demo(tmp_path, now=NOW)
    assert [p.name for p in tmp_path.iterdir()] == ["keep.txt"]


def test_an_unknown_state_is_refused(tmp_path):
    with pytest.raises(DemoError, match="unknown state"):
        build_demo(tmp_path / "w", now=NOW, state="purple")
    assert not (tmp_path / "w").exists()


# ----------------------------------------------------------------------------- the command
def test_demo_no_open_builds_in_under_ten_seconds(tmp_path, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url) or True)
    t0 = time.perf_counter()
    code = cli.main(["demo", "--no-open", "--out", str(tmp_path / "d")])
    took = time.perf_counter() - t0
    assert code == 0 and took < 10, took
    assert opened == []
    out = capsys.readouterr().out
    assert "made up" in out and (tmp_path / "d" / "town" / "town.html").is_file()
    page = (tmp_path / "d" / "town" / "town.html").read_text(encoding="utf-8")
    assert "Lamptown demo" in page and "Paper Kite" in page


def test_demo_opens_the_town(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url) or True)
    assert cli.main(["demo", "-q", "--state", "waiting", "--out", str(tmp_path / "d")]) == 0
    assert opened == [(tmp_path / "d" / "town" / "town.html").resolve().as_uri()]


def test_demo_default_folder_is_a_new_temp_folder(monkeypatch, tmp_path):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    first = build_demo(now=NOW)
    second = build_demo(now=NOW)
    assert first.root != second.root
    assert first.root.parent == second.root.parent == tmp_path


def test_demo_command_errors(tmp_path, capsys):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "f").write_text("", encoding="utf-8")
    assert cli.main(["demo", "--no-open", "--out", str(tmp_path / "x")]) == 2
    assert "not empty" in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:
        cli.main(["demo", "--state", "purple"])
    assert e.value.code == 2


def test_demo_now_pins_the_page_clock(tmp_path):
    assert cli.main(["demo", "-q", "--no-open", "--now", "2026-03-14T21:30:00+01:00",
                     "--out", str(tmp_path / "d")]) == 0
    page = Path(tmp_path / "d" / "town" / "town.html").read_text(encoding="utf-8")
    assert '"fixedClock": true' in page and "2026-03-14T21:30:00+01:00" in page
