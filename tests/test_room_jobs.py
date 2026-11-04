"""The ``jobs`` room's data: the ``log_glob`` collector and the schedule parser (fictional logs)."""

from __future__ import annotations

import datetime as dt
import os
import shutil
from pathlib import Path

import pytest

from lamptown import build as B
from lamptown import collectors as reg
from lamptown.collectors import jobs as J
from lamptown.collectors import jobs_schedule as S
from lamptown.collectors.schema import room_schema, validate
from lamptown.config import parse_config

FIX = Path(__file__).parent / "fixtures" / "jobs"
NOW = dt.datetime(2026, 3, 14, 22, 0, tzinfo=dt.UTC)  # a Saturday
BUILTIN_KIND = ("log_glob", "lamptown.collectors.jobs:collect")


@pytest.fixture
def logs(tmp_path):
    """A copy of the fixture logs with their times set relative to NOW."""
    root = tmp_path / "logs"
    shutil.copytree(FIX, root)
    ages = {  # file -> hours before NOW
        "backup/kite-backup-2026-03-07.log": 7 * 24 + 20, "backup/kite-backup-2026-03-14.log": 20,
        "index/tidebook-index-2026-03-14.log": 0.1, "digest/moss-digest-2026-02-21.log": 21 * 24,
        "sort/kite-ideas-2026-03-14.log": 3, "build/moss-build-2026-03-14.log": 4,
    }
    for rel, h in ages.items():
        t = (NOW - dt.timedelta(hours=h)).timestamp()
        os.utime(root / rel, (t, t))
    return root


def run(paths: dict) -> dict:
    out = J.collect(dict(paths, _room="jobs", _building="jobs", _config_dir="."), NOW)
    assert validate(out, room_schema("jobs")) == [], out
    return out


def row(out: dict) -> dict:
    assert len(out["rows"]) == 1
    return out["rows"][0]


# ----------------------------------------------------------------------------- schedules
@pytest.mark.parametrize(("text", "nxt", "period_h", "words"), [
    ("daily 02:00", "2026-03-15T02:00:00+00:00", 24, "Every day 02:00"),
    ("weekly tue 07:15", "2026-03-17T07:15:00+00:00", 168, "Tuesdays 07:15"),
    ("weekly sun 09:30", "2026-03-15T09:30:00+00:00", 168, "Sundays 09:30"),
    ("weekly mon,thu 07:15", "2026-03-16T07:15:00+00:00", 96, "Mondays, Thursdays 07:15"),
    ("Weekdays 08:30", "2026-03-16T08:30:00+00:00", 72, "Weekdays 08:30"),
    ("monthly 1 06:00", "2026-04-01T06:00:00+00:00", 744, "Monthly on day 1 06:00"),
    ("monthly 31 06:00", "2026-03-31T06:00:00+00:00", 744, "Monthly on day 31 06:00"),
    ("hourly", "2026-03-14T23:00:00+00:00", 1, "Every hour"),
    ("hourly :20", "2026-03-14T22:20:00+00:00", 1, "Every hour at :20"),
])
def test_schedule_forms(text, nxt, period_h, words):
    s = S.parse(text)
    assert s.next_after(NOW).isoformat() == nxt
    assert s.period() == dt.timedelta(hours=period_h)
    assert s.words() == words


def test_monthly_past_the_months_end_is_its_last_day():
    feb = dt.datetime(2026, 2, 10, tzinfo=dt.UTC)
    assert S.parse("monthly 31 06:00").next_after(feb).isoformat() == "2026-02-28T06:00:00+00:00"


def test_every_needs_a_last_run_for_its_next():
    s = S.parse("every 6h")
    assert s.next_after(NOW) is None and s.last_slot(NOW) is None
    last = NOW - dt.timedelta(hours=13)
    assert s.next_after(NOW, last) == last + dt.timedelta(hours=18)
    assert s.words() == "Every 6 h" and s.period() == dt.timedelta(hours=6)


def test_last_slot_and_the_missed_rule():
    s = S.parse("weekly tue 07:15")
    assert s.last_slot(NOW).isoformat() == "2026-03-10T07:15:00+00:00"
    tue = dt.datetime(2026, 3, 17, 8, 0, tzinfo=dt.UTC)  # 45 min after the slot: today's counts
    assert s.last_slot(tue).isoformat() == "2026-03-17T07:15:00+00:00"
    assert s.last_slot(tue - dt.timedelta(hours=1)).isoformat() == "2026-03-10T07:15:00+00:00"
    assert S.parse("weekly mon 18:30").last_slot(NOW).isoformat() == "2026-03-09T18:30:00+00:00"
    assert S.parse("monthly 1 06:00").last_slot(NOW).isoformat() == "2026-03-01T06:00:00+00:00"
    week = dt.timedelta(days=7)
    assert not s.missed(NOW - week * 1.14, NOW)
    assert s.missed(NOW - week * 1.16, NOW)
    assert not s.missed(None, NOW)
    assert S.parse("weekly tue 07:15").short() == "EVERY TUE 07:15"


@pytest.mark.parametrize("bad", ["", "  ", "sometimes", "daily 25:00", "weekly funday 10:00",
                                 "monthly 40 06:00", "every 0h", "weekly 10:00"])
def test_bad_schedules_say_what_is_wrong(bad):
    with pytest.raises(ValueError, match="schedule"):
        S.parse(bad)


# ----------------------------------------------------------------------------- log_glob
def test_a_finished_run_reads_ok_with_its_result_and_next_run(logs):
    out = run({"name": "Kite backup", "glob": str(logs / "backup" / "kite-backup-*.log"),
               "done": r"Done:\s*(.+)$", "schedule": "daily 02:00", "colour": "#7FD8FF"})
    r = row(out)
    assert out["state"] == "ok" and r["state"] == "ok" and r["why"] == "ok"
    assert r["id"] == "kite-backup" and r["name"] == "Kite backup"
    assert r["result"] == "62 files copied, 0 failed"
    assert r["last"] == "2026-03-14T02:00:00Z"
    assert r["next"] == "2026-03-15T02:00:00Z"
    assert r["schedule"] == "daily 02:00" and r["when"] == "Every day 02:00"
    assert r["href"].startswith("file:") and r["href"].endswith("kite-backup-2026-03-14.log")
    assert r["colour"] == "#7fd8ff"
    assert "needs" not in out and "error" not in out


def test_newest_log_wins_by_time_not_by_name(logs):
    older_name = logs / "backup" / "kite-backup-2026-03-07.log"
    t = (NOW - dt.timedelta(minutes=5)).timestamp()
    os.utime(older_name, (t, t))
    r = row(run({"name": "Kite backup", "glob": str(logs / "backup" / "*.log"),
                 "done": "Done: (.+)"}))
    assert r["result"] == "58 files copied, 0 failed"


def test_no_done_line_is_running_while_fresh_then_stopped(logs):
    paths = {"name": "Tidebook index", "glob": str(logs / "index" / "*.log"), "done": "Done: (.+)"}
    r = row(run(paths))
    assert (r["state"], r["why"]) == ("working", "running")
    log = logs / "index" / "tidebook-index-2026-03-14.log"
    t = (NOW - dt.timedelta(hours=2)).timestamp()
    os.utime(log, (t, t))
    out = run(paths)
    assert (row(out)["state"], row(out)["why"]) == ("error", "stopped")
    assert out["label"] == "Tidebook index: stopped half-way"
    assert "error" not in out  # a failed run is the job's state, not a room that can't read
    assert (row(run(dict(paths, running_minutes=180)))["state"]) == "working"


def test_without_a_done_pattern_the_last_line_is_the_result(logs):
    r = row(run({"name": "Tidebook index", "glob": str(logs / "index" / "*.log")}))
    assert r["state"] == "ok" and r["result"] == "chapter 2 of 9"


def test_an_old_last_run_is_missed(logs):
    out = run({"name": "Moss digest", "glob": str(logs / "digest" / "*.log"), "done": "Done: (.+)",
               "schedule": "weekly sat 09:00"})
    r = row(out)
    assert (r["state"], r["why"]) == ("error", "missed")
    assert r["result"].startswith("Missed its run: the newest log is from 2026-02-21.")
    assert out["label"] == "Moss digest: missed its run"


def test_fail_pattern_and_needs_pattern(logs):
    r = row(run({"name": "Moss build", "glob": str(logs / "build" / "*.log"), "done": "Done: (.+)",
                 "fail": r"\b[1-9][0-9]* failed\b"}))
    assert (r["state"], r["why"], r["result"]) == ("error", "failed", "17 checked, 1 failed")
    out = run({"name": "Idea box", "glob": str(logs / "sort" / "*.log"), "done": "Done: (.+)",
               "needs": r"(\d+ ideas? to sort)"})
    assert (row(out)["state"], out["state"]) == ("waiting", "waiting")
    assert out["needs"][0]["text"] == "Idea box: 3 ideas to sort"
    assert out["needs"][0]["href"].startswith("file:")


def test_no_logs_and_no_folder_are_missing(logs, tmp_path):
    r = row(run({"name": "Empty", "glob": str(logs / "empty" / "*.log")}))
    assert (r["state"], r["why"]) == ("missing", "never")
    assert r["last"] is None
    r = row(run({"name": "Gone", "glob": str(tmp_path / "nowhere" / "*.log"),
                 "schedule": "daily 01:00"}))
    assert (r["state"], r["why"]) == ("missing", "nosource")
    assert r["next"] == "2026-03-15T01:00:00Z"


def test_name_and_id_fall_back_to_the_glob(logs):
    r = row(run({"glob": str(logs / "backup" / "kite-backup-*.log")}))
    assert r["name"] == "kite-backup" and r["id"] == "kite-backup"


@pytest.mark.parametrize(("extra", "msg"), [
    ({"done": "Done: ("}, "done is not a valid regular expression"),
    ({"running_minutes": -1}, "running_minutes"),
    ({"schedule": "now and then"}, "schedule"),
])
def test_bad_settings_raise_clear_errors(logs, extra, msg):
    with pytest.raises(ValueError, match=msg):
        J.collect({"name": "x", "glob": str(logs / "backup" / "*.log"), **extra}, NOW)


def test_a_glob_is_required():
    with pytest.raises(ValueError, match="glob"):
        J.collect({"name": "x"}, NOW)


def test_huge_log_reads_only_its_tail(tmp_path):
    p = tmp_path / "big.log"
    p.write_text("Done: early\n" + "x" * (J.TAIL_BYTES + 10) + "\nDone: late\n", encoding="utf-8")
    assert row(run({"glob": str(p), "done": "Done: (.+)"}))["result"] == "late"


# ----------------------------------------------------------------------------- through a build
def test_several_jobs_in_one_building_build_a_checked_room(logs, tmp_path, monkeypatch):
    monkeypatch.setitem(reg.BUILTIN, *BUILTIN_KIND)  # until the registry lists log_glob itself
    g = logs.as_posix()
    toml = f"""
[[building]]
id = "jobs"
type = "jobs"
label = "Night jobs"
  [[building.source]]
  kind = "log_glob"
  name = "Kite backup"
  glob = "{g}/backup/*.log"
  done = 'Done:\\s*(.+)$'
  schedule = "daily 02:00"
  [[building.source]]
  kind = "log_glob"
  name = "Tidebook index"
  glob = "{g}/index/*.log"
  done = 'Done:\\s*(.+)$'
  feeds = "notes"
  [[building.source]]
  kind = "log_glob"
  name = "Idea box"
  glob = "{g}/sort/*.log"
  done = 'Done:\\s*(.+)$'
  needs = '(\\d+ ideas? to sort)'
"""
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    res = B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW)
    room = res.data["rooms"]["jobs"]
    assert [r["name"] for r in room["rows"]] == ["Kite backup", "Tidebook index", "Idea box"]
    assert room["state"] == "waiting"  # worst of ok, working, waiting
    assert room["rows"][1]["feeds"] == "notes"
    assert len(room["needs"]) == 1 and res.data["needs"] == 1
    assert res.data["parts"]["jobs"]["num"] == "3"
    assert validate(room, room_schema("jobs")) == []
    html = res.town.read_text(encoding="utf-8")
    assert 'data-room="jobs"' in html  # the built-in room script is inlined


# ----------------------------------------------------------------------------- in the browser
from _browser import browser  # noqa: E402  (shared headless browser)
from test_town import run as probe  # noqa: E402

SEEN = "return Object.assign({ id: RS.id, mode: RS.mode, plate: RS.room.plate() }, RS.room.seen());"
SIX = [("Kite backup", "backup", 'schedule = "daily 02:00"'),
       ("Tidebook index", "index", 'schedule = "weekly sun 09:30"'),
       ("Moss digest", "digest", 'schedule = "weekly sat 09:00"'),
       ("Idea box", "sort", r"needs = '(\d+ ideas? to sort)'"),
       ("Moss build", "build", r"fail = '\b[1-9][0-9]* failed\b'"),
       ("Empty", "empty", 'schedule = "monthly 1 06:00"')]


def build_jobs(tmp_path, monkeypatch, logs, jobs) -> Path:
    monkeypatch.setitem(reg.BUILTIN, *BUILTIN_KIND)
    g = logs.as_posix()
    src = "".join(f'  [[building.source]]\n  kind = "log_glob"\n  name = "{n}"\n'
                  f'  glob = "{g}/{d}/*.log"\n  done = \'Done:\\s*(.+)$\'\n  {x}\n'
                  for n, d, x in jobs)
    toml = f'[[building]]\nid = "nightly"\ntype = "jobs"\nlabel = "Night jobs"\n{src}'
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    return B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW).town


@browser
def test_room_on_fixtures_one_window_per_job(logs, tmp_path, monkeypatch):
    page = build_jobs(tmp_path, monkeypatch, logs, SIX)
    r = probe(page, """const s = RS.room.seen();
      const tips = allTargets().filter(t => t.id.startsWith('win-')).map(t => [t.id, t.tip]);
      return Object.assign({ id: RS.id, plate: RS.room.plate(), tips }, s);""",
              frag="#room=nightly")
    assert (r["id"], r["sample"], r["total"], r["shown"], r["more"]) == ("nightly", False, 6, 6, 0)
    assert r["plate"] == {"num": "6", "state": "error"}
    w = {x["id"]: x for x in r["windows"]}
    assert [x["state"] for x in r["windows"]] == [
        "ok", "working", "error", "waiting", "error", "missing"]
    assert w["kite-backup"]["trumpet"] == "hook" and w["tidebook-index"]["trumpet"] == "played"
    assert w["moss-digest"]["why"] == "missed" and w["moss-digest"]["trumpet"] == "hook"
    assert w["moss-build"]["trumpet"] == "floor"  # a failed run drops the trumpet
    assert w["idea-box"]["bubble"] is True and w["empty"]["shut"] is True
    assert w["empty"]["trumpet"] == "none"
    tips = dict(r["tips"])
    assert tips["win-kite-backup"][0].startswith("Kite backup")
    assert any("Every day 02:00" in t for t in tips["win-kite-backup"])
    assert tips["win-idea-box"][-1] == "Click: hear its call."


@browser
def test_more_than_eight_jobs_share_a_more_board(logs, tmp_path, monkeypatch):
    many = [(f"Job {i}", "backup", 'schedule = "daily 02:00"') for i in range(9)] + SIX[3:5]
    page = build_jobs(tmp_path, monkeypatch, logs, many)
    r = probe(page, """const m = allTargets().find(t => t.id === 'more');
      return Object.assign({ plate: RS.room.plate(), moreTip: m && m.tip }, RS.room.seen());""",
            frag="#room=nightly")
    assert (r["total"], r["shown"], r["more"]) == (11, 8, 3)
    assert r["plate"] == {"num": "11", "state": "error"}  # the worst of all, shown or not
    assert r["moreTip"][0].startswith("+3")
    # the worst jobs are listed first on the board
    assert "Moss build" in r["moreTip"][1]


@browser
@pytest.mark.parametrize(("state", "check"), [
    ("ok", lambda w: all(x["state"] == "ok" for x in w)),
    ("waiting", lambda w: w[0]["state"] == "waiting" and w[0]["bubble"]),
    ("working", lambda w: w[0]["state"] == "working" and w[0]["trumpet"] == "played"),
    ("error", lambda w: w[-1]["state"] == "error" and w[-1]["trumpet"] == "floor"),
    ("missing", lambda w: w[-1]["shut"] and w[-1]["trumpet"] == "none"),
    ("missed", lambda w: w[-1]["why"] == "missed" and w[-1]["trumpet"] == "hook"),
])
def test_demo_shows_each_state_on_sample_jobs(logs, tmp_path, monkeypatch, state, check):
    page = build_jobs(tmp_path, monkeypatch, logs, SIX[:1])
    r = probe(page, SEEN, query=f"?t=300&demo=1&state={state}&jobs=4", frag="#room=nightly")
    assert r["sample"] is True and r["total"] == 4
    assert check(r["windows"]), r["windows"]


@browser
def test_demo_scale_and_keys(logs, tmp_path, monkeypatch):
    page = build_jobs(tmp_path, monkeypatch, logs, SIX[:1])
    r = probe(page, """const a = RS.room.seen();
      document.dispatchEvent(new KeyboardEvent('keydown', { key: '4', bubbles: true }));
      await wait(100);
      return { a, b: RS.room.seen(), plate: RS.room.plate() };""",
            query="?t=300&demo=1&jobs=12", frag="#room=nightly")
    assert (r["a"]["total"], r["a"]["shown"], r["a"]["more"]) == (12, 8, 4)
    assert r["plate"] == {"num": "12", "state": "error"}
    assert r["a"]["windows"][-1]["state"] == "ok"
    assert r["b"]["windows"][-1]["state"] == "error"  # key 4: a failed run on the last window
    assert r["b"]["windows"][-1]["trumpet"] == "floor"
