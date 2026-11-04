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
    assert sorted(a["name"].split(":")[0] for a in agents) == [
        "Gannet", "Heron", "Kestrel", "Knot check", "Lark", "Marten", "Otter", "Puffin", "Wren"]
    # an agent's `agent` is the tool it runs on (the live town's coat colour)
    assert {a["agent"] for a in agents} == {"claude-code", "codex", "kitebot"}
    assert d["rooms"]["notes"]["notes"] == 40 and d["parts"]["notes"]["num"] == "40"
    assert len(_rows(d, "skills")) == 9
    assert len(_rows(d, "gate")) == 6
    assert len(d["rooms"]["contest"]["entries"]) == 8


def test_the_default_world_is_alive(lively):
    """Some lamps wait for you, two are red (a broken skill, a failed agent at the harbour),
    and things run right now."""
    st = {bid: room["state"] for bid, room in lively.data["rooms"].items()}
    assert sorted(b for b, s in st.items() if s == "error") == ["harbour", "skills"]
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
    # the lighthouse is the live town's own place, not a building: it follows the live agents
    rooms = {bid: r for bid, r in res.data["rooms"].items() if bid != "lighthouse"}
    assert {bid: r["state"] for bid, r in rooms.items()} == dict.fromkeys(rooms, state)
    parts = [p for bid, p in res.data["parts"].items() if bid != "lighthouse"]
    assert {p["lamp"] for p in parts} == {LAMP[state]}
    if state in ("ok", "working", "missing"):
        assert res.data["needs"] == 0


def _js(path: Path, fn: str) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    assert text.startswith(fn + "(") and text.endswith(");")
    return json.loads(text[len(fn) + 1:-2])


def test_the_live_town_has_every_state_a_subagent_and_berths(lively):
    """`lamptown demo` shows the live town: agents in every state, a helper, three berths."""
    p = _js(lively.town.with_name("town-live.js"), "lamptownLive")
    by_id = {a["id"]: a for a in p["agents"]}
    assert {a["state"] for a in p["agents"]} == {"working", "waiting", "ok", "error", "quiet"}
    assert by_id["otter"]["state"] == "waiting" and p["need"] == 1
    assert by_id["kestrel"]["state"] == "error"
    assert by_id["lark"]["state"] == "quiet"
    assert by_id["heron"]["state"] == "ok"
    assert by_id["wren-knots"]["parent"] == "wren" and by_id["wren"]["subagents"] == 1
    assert by_id["wren"]["pushed"] and by_id["wren"]["ci"] == "running"
    assert {a["plot"] for a in p["agents"]} == {"boathouse", "library", "lab", "tower", "workshop"}
    assert sorted(x["id"] for x in p["projects"]) == ["moss-engine", "paper-kite", "tidebook"]
    assert "marten" not in by_id  # done three hours ago: its lantern is off the hook
    assert lively.data["parts"]["lighthouse"]["lamp"] == "amber"
    assert p["rev"] == lively.data["rev"]


def test_the_lighthouse_book_has_a_week_dated_from_now(lively):
    h = _js(lively.town.with_name("town-history.js"), "lamptownHistory")
    days = h["days"]
    assert [d["date"] for d in days][-1] == NOW.date().isoformat() and days[-1]["today"]
    assert len(days) == 7
    counts = [len(d["agents"]) for d in days]
    assert counts[-1] == 9 and 0 in counts  # today's nine, and one empty day
    kinds = {e[2] for d in days for e in d["events"]}
    assert {"start", "files", "commit", "push", "ci", "wait", "answer", "fail", "quiet",
            "done", "project"} <= kinds
    yesterday = days[-2]
    last = {}
    for minute, i, kind, *_ in yesterday["events"]:
        last[i] = (minute, kind)
    marten = next(i for i, a in enumerate(yesterday["agents"]) if "MARTEN" in a["name"])
    assert last[marten] == (23 * 60 + 10, "fail")  # open at the close: 23:10 in NOW's zone


def test_history_times_and_mistakes():
    from lamptown.demo import history_events

    status, evs = history_events(
        'status_dir = "agents"\n[[agent]]\nid = "a"\nevents = ["-2d@09:12 start", '
        '"-30m files 3", "-10m ci red", "+1h done"]\n', NOW, NOW.tzinfo)
    assert status == "agents"
    assert [e["t"] for e in evs] == [
        dt.datetime(2026, 3, 12, 9, 12, tzinfo=NOW.tzinfo), NOW - dt.timedelta(minutes=30),
        NOW - dt.timedelta(minutes=10)]  # the future one is left out
    assert evs[1]["n"] == 3 and evs[2]["ok"] is False
    for bad in ('["-1h jump"]', '["soon start"]', '["-1h files lots"]'):
        with pytest.raises(DemoError):
            history_events(f'status_dir = "a"\n[[agent]]\nid = "a"\nevents = {bad}\n', NOW)


SMALL_HOURS = NOW.replace(day=15, hour=0, minute=5)  # 00:05 the next night, NOW's zone


def _book_today(res) -> tuple[dict, list]:
    """(each agent of today's page in the book -> its last event's kind, today's events)."""
    day = _js(res.town.with_name("town-history.js"), "lamptownHistory")["days"][-1]
    last = {}
    for _minute, i, kind, *_ in day["events"]:
        last[day["agents"][i]["id"]] = kind
    return last, day["events"]


def test_the_book_has_today_in_the_small_hours(tmp_path):
    # review L12 m4: at 00:05 every "-40m" event fell in yesterday and today's page was empty
    res = build_demo(tmp_path / "w", now=SMALL_HOURS)
    live = _js(res.town.with_name("town-live.js"), "lamptownLive")
    last, events = _book_today(res)
    assert len(last) == 9 and {a["id"] for a in live["agents"]} <= set(last)
    minutes = [e[0] for e in events]
    assert minutes == sorted(minutes) and minutes[0] >= 1 and minutes[-1] <= 5
    # yesterday's own day is untouched: Marten's proofs still fail at 23:10
    h = _js(res.town.with_name("town-history.js"), "lamptownHistory")
    names = [a["name"] for a in h["days"][-2]["agents"]]
    assert any("MARTEN" in n for n in names) and h["days"][-2]["date"] == "2026-03-14"


def test_todays_events_are_drawn_after_midnight_in_order():
    from lamptown.demo import history_events

    text = ('status_dir = "a"\n[[agent]]\nid = "a"\nevents = ["-8h start", "-7h files 2", '
            '"-3m fail", "-2d@09:00 start"]\n[[agent]]\nid = "b"\nevents = ["-30m start"]\n')
    _, evs = history_events(text, SMALL_HOURS, SMALL_HOURS.tzinfo)
    midnight = SMALL_HOURS.replace(hour=0, minute=0)
    assert evs[0]["t"] == midnight - dt.timedelta(days=2) + dt.timedelta(hours=9)  # as written
    today = [(e["id"], e["k"]) for e in evs[1:]]
    assert today == [("a", "start"), ("a", "files"), ("b", "start"), ("a", "fail")]
    assert evs[1]["t"] == midnight + dt.timedelta(minutes=1)
    assert evs[-1]["t"] == SMALL_HOURS - dt.timedelta(minutes=3)  # already today: as written
    assert all(midnight < e["t"] <= evs[-1]["t"] for e in evs[1:])
    # at 21:30 nothing moves
    _, evs = history_events(text, NOW, NOW.tzinfo)
    assert [e["t"] for e in evs][1:3] == [NOW - dt.timedelta(hours=8), NOW - dt.timedelta(hours=7)]


# what a live state reads as in the book: the kind of the agent's last event today
LAST = {"working": {"start", "files", "commit", "push", "ci", "answer", "project"},
        "waiting": {"wait"}, "error": {"fail"}, "quiet": {"quiet"}, "ok": {"done"}}


@pytest.mark.parametrize("now", [NOW, SMALL_HOURS], ids=["evening", "small-hours"])
@pytest.mark.parametrize("state", [None, "ok", "working", "waiting", "error"])
def test_each_states_book_agrees_with_its_town(tmp_path, state, now):
    # review L12 m5: `--state ok` lit every lamp green but its book ended with a failure, a
    # wait and a quiet agent. Now an agent the town shows ends its day in the book as the town
    # shows it, and one it doesn't show is done (or not in the book at all)
    res = build_demo(tmp_path / "w", now=now, state=state)
    live = {a["id"]: a["state"] for a in
            _js(res.town.with_name("town-live.js"), "lamptownLive")["agents"]}
    last, _ = _book_today(res)
    for aid, st in live.items():
        assert last.get(aid) in LAST[st], (state, aid, st, last.get(aid))
    for aid in set(last) - set(live):
        assert last[aid] == "done", (state, aid, last[aid])


def test_a_states_own_book_names_only_world_agents():
    from lamptown.demo import history_events

    world = 'status_dir = "a"\n[[agent]]\nid = "a"\nevents = ["-8h start"]\n'
    _, evs = history_events(world, NOW, state_text='[[agent]]\nid = "a"\nevents = ["-1h done"]')
    assert [(e["k"], e["t"]) for e in evs] == [("done", NOW - dt.timedelta(hours=1))]
    _, evs = history_events(world, NOW, gone={"a/a.json"})
    assert evs == []
    with pytest.raises(DemoError):
        history_events(world, NOW, state_text='[[agent]]\nid = "zz"\nevents = []')


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
    assert cli.main(["demo", "-q", "--no-live", "--state", "waiting",
                     "--out", str(tmp_path / "d")]) == 0
    assert opened == [(tmp_path / "d" / "town" / "town.html").resolve().as_uri()]


@pytest.mark.parametrize("args, hint", [
    (["--no-open"], True), (["--no-live", "--state", "error"], True),
    (["--no-open", "--now", "2026-03-14T21:30:00+01:00"], False),
    (["--no-open", "--state", "missing"], False)])
def test_demo_suggests_a_watcher_only_where_one_fits(tmp_path, monkeypatch, capsys, args, hint):
    # review L12 m6: a pinned --now world under a Watcher shows every agent stopped or gone, and
    # a Watcher would give the missing state a status folder
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: True)
    assert cli.main(["demo", *args, "--out", str(tmp_path / "d")]) == 0
    assert ("lamptown watch" in capsys.readouterr().out) is hint


def test_demo_keeps_its_live_town_live(tmp_path, monkeypatch, capsys):
    """Without --no-live the demo opens the town and runs the Watcher loop on its world (no
    readers), so town-live.js keeps a fresh heartbeat; it lets the status folder go after."""
    from lamptown import livefeed
    from lamptown.status.watch import Watcher

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url) or True)
    monkeypatch.setattr(livefeed, "_sleep", lambda _s: None)
    out = tmp_path / "d"
    assert cli.main(["demo", "--out", str(out), "--ticks", "2"]) == 0
    assert opened == [(out / "town" / "town.html").resolve().as_uri()]
    assert "Watching:" in capsys.readouterr().out
    text = (out / "town" / "town-live.js").read_text(encoding="utf-8")
    p = json.loads(text[len("lamptownLive("):text.rindex(")")])
    at = dt.datetime.fromisoformat(p["at"].replace("Z", "+00:00"))
    assert abs(dt.datetime.now(dt.UTC) - at) < dt.timedelta(minutes=2)  # a real heartbeat
    assert p["need"] == 1 and len(p["agents"]) == 8
    with Watcher(out / "world" / "agents") as w:
        assert not w.tick().busy  # the demo's Watcher let the folder go


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
