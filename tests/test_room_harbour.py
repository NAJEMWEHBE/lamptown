"""The ``harbour`` room's data: the ``projects`` collector (fictional projects) and agent berths."""

from __future__ import annotations

import builtins
import datetime as dt
import io
import json
import os
import shutil
from pathlib import Path

import pytest

from lamptown import build as B
from lamptown import collectors as reg
from lamptown.collectors import harbour as H
from lamptown.collectors.schema import room_schema, validate
from lamptown.config import parse_config

FIX = Path(__file__).parent / "fixtures" / "harbour"
NOW = dt.datetime(2026, 3, 14, 22, 0, tzinfo=dt.UTC)
BUILTIN_KIND = ("projects", "lamptown.collectors.harbour:collect")
COMMIT_TIME = int((NOW - dt.timedelta(days=2)).timestamp())
WHO = "Wren <wren@example.invalid>"  # a commit author: never kept


def _age_all(root: Path, days: float) -> None:
    t = (NOW - dt.timedelta(days=days)).timestamp()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            os.utime(Path(dirpath) / f, (t, t))


@pytest.fixture
def code(tmp_path):
    """A copy of the fictional projects; paper-kite gets a git log (nested .git can't be a
    fixture), everything is two days old."""
    root = tmp_path / "code"
    shutil.copytree(FIX, root)
    git = root / "paper-kite" / ".git" / "logs"
    git.mkdir(parents=True)
    z = "0" * 40
    (git / "HEAD").write_text(
        f"{z} {'a' * 40} {WHO} {COMMIT_TIME - 60} +0000\tcommit (initial): Frame\n"
        f"{'a' * 40} {'b' * 40} {WHO} {COMMIT_TIME} +0000\tcommit: Glue the sail\n",
        encoding="utf-8")
    _age_all(root, 2)
    return root


def projects(root: Path) -> list[dict]:
    return [
        {"name": "Paper Kite", "path": str(root / "paper-kite"), "status": "active",
         "short": "kite"},
        {"name": "Tidebook", "path": str(root / "tidebook"), "status": "active"},
        {"name": "Moss Engine", "path": str(root / "moss-engine"), "status": "parked",
         "since": "2026-02-01"},
        {"name": "Lantern", "path": str(root / "lantern"), "status": "active", "private": True},
        {"name": "Old Pier", "path": str(root / "old-pier"), "status": "done", "note": "Shipped."},
        {"name": "No Tasks", "path": str(root / "no-tasks"), "status": "active"},
    ]


def run(paths: dict) -> dict:
    out = H.collect(dict(paths, _room="harbour", _building="harbour", _config_dir="."), NOW)
    assert validate(out, room_schema("harbour")) == [], out
    return out


def rows(out: dict) -> dict[str, dict]:
    return {r["id"]: r for r in out["rows"]}


def test_active_projects_are_read(code):
    out = run({"projects": projects(code)})
    r = rows(out)["paper-kite"]
    assert (r["status"], r["state"], r["why"]) == ("active", "waiting", "yours")
    assert r["progress"] == {"done": 3, "total": 9}
    assert [n["short"] for n in r["needs"]] == [
        "Pick the tail colour", "Approve the test-flight day"]
    assert r["needs_n"] == 2 and r["agent_n"] == 1
    assert r["agent_items"][0]["short"] == "Re-run the wind check"
    assert r["last_commit"] == {"when": dt.datetime.fromtimestamp(COMMIT_TIME, dt.UTC)
                                .isoformat().replace("+00:00", "Z"), "msg": "Glue the sail"}
    assert "wren@" not in json.dumps(out)  # the commit's author is never kept
    assert r["short"] == "KITE" and r["busy"] is False
    assert r["href"].startswith("file:") and r["tasks_href"].endswith("TASKS.md")
    t = rows(out)["tidebook"]
    assert (t["state"], t["progress"]) == ("ok", {"done": 2, "total": 3})
    assert rows(out)["no-tasks"]["why"] == "notasks"


def test_room_state_needs_and_label(code):
    out = run({"projects": projects(code)})
    assert out["state"] == "waiting"
    assert [n["text"] for n in out["needs"]] == [
        "Paper Kite: Pick the tail colour (tail.md)", "Paper Kite: Approve the test-flight day"]
    assert out["label"] == "3 active, 2 paused, 1 closed"  # the private one shows paused
    assert "error" not in out


def test_paused_and_closed_are_never_read_inside(code):
    out = run({"projects": projects(code)})
    m, o = rows(out)["moss-engine"], rows(out)["old-pier"]
    assert (m["status"], m["state"], m["since"]) == ("paused", "ok", "2026-02-01")
    assert (o["status"], o["note"]) == ("closed", "Shipped.")
    for r in (m, o):
        assert "progress" not in r and "needs" not in r and "href" not in r
    assert "SHOULD NOT SHOW" not in json.dumps(out)


def _guard(monkeypatch, forbidden: Path) -> list[str]:
    """Fail any open, listing or stat *inside* ``forbidden`` (the folder itself may be checked)."""
    hits: list[str] = []
    bad = os.path.normcase(str(forbidden)) + os.sep

    def inside(p) -> bool:
        s = os.path.normcase(os.path.abspath(os.fspath(p)))
        if s.startswith(bad):
            hits.append(s)
            return True
        return False

    real_open = builtins.open
    real_listdir, real_scandir, real_stat = os.listdir, os.scandir, os.stat

    def g_open(file, *a, **k):
        if isinstance(file, str | bytes | os.PathLike) and inside(file):
            raise AssertionError(f"opened {file}")
        return real_open(file, *a, **k)

    def g_list(p="."):
        if inside(os.path.join(os.fspath(p), "x")):
            raise AssertionError(f"listed {p}")
        return real_listdir(p)

    def g_scan(p="."):
        if inside(os.path.join(os.fspath(p), "x")):
            raise AssertionError(f"listed {p}")
        return real_scandir(p)

    def g_stat(p, *a, **k):
        if isinstance(p, str | bytes | os.PathLike) and inside(p):
            raise AssertionError(f"looked at {p}")
        return real_stat(p, *a, **k)

    monkeypatch.setattr(builtins, "open", g_open)
    monkeypatch.setattr(io, "open", g_open)
    monkeypatch.setattr(os, "listdir", g_list)
    monkeypatch.setattr(os, "scandir", g_scan)
    monkeypatch.setattr(os, "stat", g_stat)
    return hits


def test_a_private_project_is_never_read_inside(code, monkeypatch):
    hits = _guard(monkeypatch, code / "lantern")
    out = run({"projects": projects(code)})
    monkeypatch.undo()
    assert hits == []
    r = rows(out)["lantern"]
    assert (r["status"], r["private"], r["exists"], r["why"]) == ("paused", True, True, "private")
    for key in ("progress", "needs", "href", "tasks_href", "last_commit", "updated"):
        assert key not in r
    assert "PRIVATE ITEM" not in json.dumps(out)


def test_private_dirs_cover_everything_under_them(code, monkeypatch):
    plist = [dict(p, private=False) for p in projects(code)]
    hits = _guard(monkeypatch, code / "lantern")
    out = run({"projects": plist, "private_dirs": [str(code / "lantern")]})
    monkeypatch.undo()
    assert hits == [] and rows(out)["lantern"]["private"] is True
    # a whole folder of projects: every one under it is private, whatever its status says
    out = run({"projects": plist, "private_dirs": str(code)})
    assert all(r["private"] and r["status"] == "paused" for r in out["rows"])
    assert "needs" not in out and out["state"] == "ok"


def test_private_dirs_resolve_against_the_config_folder(code):
    out = H.collect({"projects": [{"name": "Lantern", "path": "lantern", "status": "active"}],
                     "private_dirs": ["lantern"], "_config_dir": str(code)}, NOW)
    assert out["rows"][0]["private"] is True
    out = H.collect({"projects": [{"name": "Tidebook", "path": "tidebook", "status": "active"}],
                     "_config_dir": str(code)}, NOW)
    assert out["rows"][0]["progress"] == {"done": 2, "total": 3}


def test_a_missing_folder_is_grey(code):
    out = run({"projects": [{"name": "Gone", "path": str(code / "gone"), "status": "active"},
                            {"name": "Gone too", "path": str(code / "gone2"), "status": "active",
                             "private": True}]})
    assert [(r["state"], r["exists"]) for r in out["rows"]] == [("missing", False)] * 2
    assert out["state"] == "missing"


def test_a_recent_change_is_busy(code):
    t = (NOW - dt.timedelta(minutes=10)).timestamp()
    os.utime(code / "tidebook" / "TASKS.md", (t, t))
    r = rows(run({"projects": projects(code)}))["tidebook"]
    assert (r["state"], r["why"], r["busy"]) == ("working", "busy", True)
    assert r["updated"] == "2026-03-14T21:50:00Z"
    assert rows(run({"projects": projects(code), "busy_minutes": 5}))["tidebook"]["busy"] is False


def test_an_unreadable_tasks_file_is_red_for_that_project_only(code, monkeypatch):
    real = Path.read_text

    def fail(self, *a, **k):
        if self.parent.name == "tidebook":
            raise PermissionError("locked")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", fail)
    out = run({"projects": projects(code)})
    assert (rows(out)["tidebook"]["state"], rows(out)["tidebook"]["why"]) == ("error", "unreadable")
    assert out["state"] == "error" and "error" not in out  # the room itself still reads fine
    assert rows(out)["paper-kite"]["state"] == "waiting"


def test_bad_entries_become_red_rows_not_a_broken_room(code):
    out = run({"projects": [{"path": "x"}, "just text",
                            {"name": "Odd", "path": str(code / "tidebook"), "status": "someday"},
                            {"name": "No path", "status": "active"},
                            {"name": "Tidebook", "path": str(code / "tidebook")},
                            {"name": "Tidebook", "path": str(code / "tidebook"),
                             "status": "closed"}]})
    got = [(r["id"], r["state"], r["why"]) for r in out["rows"]]
    assert got == [("entry-1", "error", "badentry"), ("entry-2", "error", "badentry"),
                   ("odd", "error", "badstatus"), ("no-path", "missing", "nopath"),
                   ("tidebook", "ok", "active"), ("tidebook-2", "ok", "closed")]
    assert "status must be" in out["rows"][2]["message"]


def test_bad_settings(code):
    with pytest.raises(ValueError, match="projects"):
        H.collect({}, NOW)
    with pytest.raises(ValueError, match="projects"):
        H.collect({"projects": "Paper Kite"}, NOW)
    with pytest.raises(ValueError, match="busy_minutes"):
        H.collect({"projects": [], "busy_minutes": -1}, NOW)
    assert H.collect({"projects": []}, NOW)["state"] == "missing"


def test_long_lists_are_cut(code):
    p = code / "tidebook" / "TASKS.md"
    items = "".join(f"- [ ] Item {i}\n" for i in range(55))
    p.write_text("## Needs you\n" + items, encoding="utf-8")
    _age_all(code, 2)
    r = rows(run({"projects": projects(code)}))["tidebook"]
    assert len(r["needs"]) == H.MAX_ITEMS and r["needs_n"] == 55


def test_waiting_on_the_user_heading_and_short_items():
    t = H.read_tasks("# x\n### Waiting on the user\n- [ ] Sign **the** form (see `a.md`); soon\n"
                     "- [x] done one\n- [ ] (lead, 2 h) Tidy the notes\n## Other\n- [ ] not this\n")
    assert [i["short"] for i in t["yours"]] == ["Sign the form, soon"]
    assert [i["short"] for i in t["agent"]] == ["Tidy the notes"]
    assert (t["done"], t["total"]) == (1, 4)


def test_through_a_build_with_agent_berths(code, tmp_path, monkeypatch):
    monkeypatch.setitem(reg.BUILTIN, *BUILTIN_KIND)  # until the registry lists projects itself
    status = tmp_path / "status"
    status.mkdir()
    (status / "wren.json").write_text(json.dumps({
        "spec": "agent-status/1", "id": "wren", "title": "Wren on Tidebook", "state": "working",
        "message": "Writing the index", "updated": "2026-03-14T21:58:00Z"}), encoding="utf-8")
    c = code.as_posix()
    toml = f"""
[[building]]
id = "harbour"
type = "harbour"
label = "Harbour"
  [[building.source]]
  kind = "projects"
  private_dirs = ["{c}/lantern"]
  projects = [
    {{ name = "Paper Kite", path = "{c}/paper-kite", status = "active" }},
    {{ name = "Lantern", path = "{c}/lantern", status = "active" }},
    {{ name = "Old Pier", path = "{c}/old-pier", status = "closed" }},
  ]
  [[building.source]]
  kind = "status_dir"
  dir = "{status.as_posix()}"
"""
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    res = B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW)
    room = res.data["rooms"]["harbour"]
    assert [r["id"] for r in room["rows"]] == ["paper-kite", "lantern", "old-pier", "wren"]
    wren = room["rows"][-1]
    assert wren["message"] == "Writing the index" and "status" not in wren  # an agent berth
    assert room["state"] == "waiting" and res.data["needs"] == 2
    assert validate(room, room_schema("harbour")) == []
    assert "PRIVATE ITEM" not in res.town.read_text(encoding="utf-8")
    assert 'data-room="harbour"' in res.town.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- in the browser
from _browser import browser  # noqa: E402  (shared headless browser)
from test_town import run as probe  # noqa: E402

SEEN = "return Object.assign({ id: RS.id, mode: RS.mode, plate: RS.room.plate() }, RS.room.seen());"


def build_harbour(tmp_path, monkeypatch, plist: list[dict], status: Path | None = None) -> Path:
    monkeypatch.setitem(reg.BUILTIN, *BUILTIN_KIND)
    rows_ = "".join("    { " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in p.items()) + " },\n"
                    for p in plist)
    toml = ('[[building]]\nid = "harbour"\ntype = "harbour"\nlabel = "Harbour"\n'
            f'  [[building.source]]\n  kind = "projects"\n  projects = [\n{rows_}  ]\n')
    if status:
        toml += f'  [[building.source]]\n  kind = "status_dir"\n  dir = "{status.as_posix()}"\n'
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    return B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW).town


def _wren(tmp_path) -> Path:
    status = tmp_path / "status"
    status.mkdir()
    (status / "wren.json").write_text(json.dumps({
        "spec": "agent-status/1", "id": "wren", "title": "Wren on the index", "state": "working",
        "message": "Writing the index", "progress": {"done": 4, "total": 10},
        "updated": "2026-03-14T21:58:00Z"}), encoding="utf-8")
    return status


@browser
def test_room_on_fixtures_one_berth_per_project(code, tmp_path, monkeypatch):
    page = build_harbour(tmp_path, monkeypatch, projects(code), _wren(tmp_path))
    r = probe(page, """const s = RS.room.seen();
      const tips = allTargets().filter(t => /^(boat|closed|lead):/.test(t.id))
        .map(t => [t.id, t.tip]);
      return Object.assign({ id: RS.id, plate: RS.room.plate(), tips }, s);""",
              frag="#room=harbour")
    assert (r["id"], r["sample"], r["state"]) == ("harbour", False, "waiting")
    assert (r["active"], r["paused"], r["closed"]) == (4, 2, 1)  # + the agent berth
    assert r["plate"] == {"num": "4", "state": "waiting"}
    bays = {b["id"]: b for b in r["bays"]}
    assert list(bays) == ["paper-kite", "tidebook", "no-tasks", "wren", "moss-engine", "lantern"]
    kite = bays["paper-kite"]
    assert kite["state"] == "waiting" and kite["leadNote"] is True
    done, cells = kite["planks"]
    assert 0 < done < cells and abs(done / cells - 3 / 9) < 0.05  # planked = tasks done
    laid, cells = bays["no-tasks"]["planks"]
    assert laid == cells  # no TASKS.md: fully planked
    assert bays["wren"]["agent"] is True and bays["wren"]["shipwright"] is True
    assert bays["lantern"]["private"] is True and bays["lantern"]["tarp"] is True
    assert bays["moss-engine"]["tarp"] is True and r["boards"] == 1 and r["desk"] == 2
    tips = dict(r["tips"])
    assert tips["boat:paper-kite"][0] == "Paper Kite: needs you"
    assert "Tasks done: 3 of 9 (33%)" in tips["boat:paper-kite"]
    assert any(t.startswith("Last commit") and t.endswith("Glue the sail")
               for t in tips["boat:paper-kite"])
    assert tips["boat:lantern"][0] == "Lantern: private"
    assert tips["boat:wren"][1] == "Writing the index"
    assert tips["closed:old-pier"] == ["Old Pier", "Closed", "Shipped."]
    assert tips["lead:paper-kite"][1] == "Re-run the wind check"


@browser
def test_hovering_the_desk_lifts_the_list(code, tmp_path, monkeypatch):
    page = build_harbour(tmp_path, monkeypatch, projects(code))
    r = probe(page, """const i = allTargets().findIndex(t => t.id === 'desk');
      const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true });
      for (let k = 0; k <= i; k++) document.dispatchEvent(tab);
      await wait(300);
      return Object.assign({ focused: allTargets()[RS.focus].id }, RS.room.seen());""",
              query="?motion=1&t=300", frag="#room=harbour")
    assert r["focused"] == "desk" and r["hov"] == "desk" and r["list"] == 2


@browser
def test_scale_more_bay_and_more_boards(code, tmp_path, monkeypatch):
    plist = projects(code) + [
        {"name": f"Old {i}", "path": str(code / "old-pier"), "status": "closed"}
        for i in range(24)] + [
        {"name": f"Pause {i}", "path": str(code / "moss-engine"), "status": "paused"}
        for i in range(6)]
    page = build_harbour(tmp_path, monkeypatch, plist)
    r = probe(page, """const m = allTargets().find(t => t.id === 'boat:more');
      const c = allTargets().find(t => t.id === 'closed:more');
      return Object.assign({ moreTip: m && m.tip, boardTip: c && c.tip }, RS.room.seen());""",
              frag="#room=harbour")
    assert r["closed"] == 25 and r["boards"] == 19 and r["moreBoards"] == 6
    units = sum(2 if b["status"] == "active" else 1 for b in r["bays"]) + 1
    assert units <= 10 and r["moreBay"] == 3 + 8 - len(r["bays"]) == 5  # 3 active, 8 paused
    assert r["moreTip"][0] == f"+{r['moreBay']} more projects"
    assert r["boardTip"][0] == "+6 more closed projects"


@browser
def test_no_projects_is_an_empty_harbour_with_a_notice(tmp_path, monkeypatch):
    page = build_harbour(tmp_path, monkeypatch, [])
    r = probe(page, SEEN, frag="#room=harbour")
    assert r["notice"] is True and r["bays"] == [] and r["plate"]["num"] == "-"


@browser
@pytest.mark.parametrize(("state", "check"), [
    ("ok", lambda b: b["state"] == "ok" and not b["shipwright"]),
    ("waiting", lambda b: b["state"] == "waiting"),
    ("working", lambda b: b["state"] == "working" and b["shipwright"]),
    ("error", lambda b: b["flag"]),
    ("missing", lambda b: b["dust"]),
])
def test_demo_shows_each_state_on_sample_projects(tmp_path, monkeypatch, state, check):
    page = build_harbour(tmp_path, monkeypatch, [])
    r = probe(page, SEEN, query=f"?t=300&demo=1&state={state}", frag="#room=harbour")
    assert r["sample"] is True and r["state"] == state
    assert check(r["bays"][0]), r["bays"][0]
    assert any(b["private"] and b["tarp"] for b in r["bays"])


@browser
def test_demo_scale_and_keys(tmp_path, monkeypatch):
    page = build_harbour(tmp_path, monkeypatch, [])
    r = probe(page, """const a = RS.room.seen();
      document.dispatchEvent(new KeyboardEvent('keydown', { key: '4', bubbles: true }));
      await wait(100);
      return { a, b: RS.room.seen() };""",
              query="?t=300&demo=1&boats=8&closed=20", frag="#room=harbour")
    assert r["a"]["moreBay"] > 0 and r["a"]["moreBoards"] > 0 and r["a"]["sample"] is True
    assert r["b"]["state"] == "error" and r["b"]["bays"][0]["flag"] is True
