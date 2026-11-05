"""0.2.1 review fixes, batch C: the builder, the collectors and onboarding.

Each test was red before its fix. Every name, path and address here is made up."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

import lamptown
from lamptown import build as B
from lamptown.cli import main
from lamptown.collectors import run_sources
from lamptown.collectors import shelf as S
from lamptown.collectors import vault as V
from lamptown.config import CONFIG_NAME, parse_config
from lamptown.onboarding import doctor as D
from lamptown.onboarding import init as I

NOW = dt.datetime(2026, 11, 5, 12, tzinfo=dt.UTC)
FAR = ("0001-01-01T00:00:00+05:00", "9999-12-31T23:59:00-05:00")


def cfg(tmp_path: Path, text: str):
    p = tmp_path / CONFIG_NAME
    p.write_text(text, encoding="utf-8")
    return parse_config(text, p)


# ----------------------------------------------------------------------------- C1 far-off times
@pytest.mark.parametrize("value", FAR)
def test_a_time_past_the_calendar_shows_as_text(value):
    assert B._cell(value) == value  # shown as written, never a crash


@pytest.mark.parametrize("value", FAR)
def test_a_time_past_the_calendar_never_stops_the_build(tmp_path, value):
    c = cfg(tmp_path, f'version = 1\n[[building]]\nid = "sign"\ntype = "sign"\nplot = "lab"\n'
                      f'  [[building.source]]\n  kind = "static"\n  when = "{value}"\n')
    res = B.build(c, out_dir=tmp_path / "out", now=NOW)
    assert value in res.details.read_text(encoding="utf-8")


def test_a_contest_with_a_far_off_updated_time_still_reads(tmp_path):
    good = {"title": "Kite race", "entries": [{"code": "A", "name": "Alpha"}]}
    for i, v in enumerate(FAR):
        (tmp_path / f"c{i}.json").write_text(json.dumps(dict(good, updated=v)), encoding="utf-8")
    [res] = run_sources([("contest_json", None, {"glob": str(tmp_path / "*.json")})], NOW)
    assert res.get("state") != "error", res.get("error")


# ----------------------------------------------------------------------------- C2 lone surrogates
LONE = "Kite \\ud800 run \\udfff end \\ud83e\\ude81"  # JSON text: two lone halves, one real pair


def test_a_lone_surrogate_in_a_status_title_never_stops_the_build(tmp_path):
    status = tmp_path / "status"
    status.mkdir()
    rec = ('{"spec": "agent-status/1", "id": "wren", "title": "' + LONE + '", '
           '"state": "working", "updated": "2026-11-05T11:59:00Z"}')
    (status / "wren.json").write_text(rec, encoding="utf-8")
    c = cfg(tmp_path, 'version = 1\n[[building]]\nid = "agents"\ntype = "harbour"\n'
                      '  [[building.source]]\n  kind = "status_dir"\n  dir = "status"\n'
                      '[live]\nstatus_dir = "status"\n')
    res = B.build(c, out_dir=tmp_path / "out", now=NOW)
    for p in (res.town, res.details, res.data_js):  # the harbour room shows the title
        text = p.read_text(encoding="utf-8")
        assert "Kite \ufffd run \ufffd end \U0001fa81" in text  # the real pair kept, as one
    res.live.read_text(encoding="utf-8")  # valid UTF-8 (names there are name_only()'s already)
    blob = json.dumps(res.data, ensure_ascii=False)
    blob.encode("utf-8")  # no lone surrogate left in the data either
    again = B.write_live_files(c, tmp_path / "out", history=True)
    assert again.history and again.history.read_text(encoding="utf-8")
    json.dumps(again.payload, ensure_ascii=False).encode("utf-8")


def test_a_collector_returning_a_lone_surrogate_is_cleaned(tmp_path):
    mine = tmp_path / "mine.py"
    mine.write_text('def collect(paths, now):\n'
                    '    return {"state": "ok", "label": "half \\udc80 here",\n'
                    '            "rows": [{"name": "x\\ud800", "\\udc81key": 1}]}\n',
                    encoding="utf-8")
    [res] = run_sources([("mine", str(mine), {})], NOW)
    assert res["label"] == "half \ufffd here"
    assert res["rows"] == [{"name": "x\ufffd", "\ufffdkey": 1}]


def test_write_atomic_never_writes_a_lone_surrogate(tmp_path):
    out = tmp_path / "x.txt"
    B.write_atomic(out, "a\ud800b\U0001fa81")
    assert out.read_text(encoding="utf-8") == "a\ufffdb\U0001fa81"


# ----------------------------------------------------------------------------- C3 init + emoji
@pytest.mark.parametrize("title", ["Kite town \U0001fa81", "Tab\tand DEL\x7f", "Caf\u00e9 \u2028"])
def test_init_writes_any_title_as_valid_toml(title):
    plan = I.render(title)
    assert tomllib.loads(plan.text)["town"]["title"] == title
    assert parse_config(plan.text).town.title == title
    assert "\\ud83e" not in plan.text and "\x7f" not in plan.text


def test_init_writes_a_non_bmp_status_path(tmp_path):
    status = "~/kites \U0001fa81/status"
    plan = I.render("Kites", status)
    assert tomllib.loads(plan.text)["building"][0]["source"][0]["dir"] == status
    parse_config(plan.text, tmp_path / CONFIG_NAME)


def test_cli_init_with_an_emoji_title(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert main(["init", "--dir", str(tmp_path), "--no-detect",
                 "--title", "Kite town \U0001fa81"]) == I.EXIT_OK
    text = (tmp_path / CONFIG_NAME).read_text(encoding="utf-8")
    assert parse_config(text).town.title == "Kite town \U0001fa81"


# ----------------------------------------------------------------------------- C4 backups
def test_init_force_never_overwrites_an_old_backup(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    target = tmp_path / CONFIG_NAME
    target.write_text("# mine, the original\n", encoding="utf-8")
    args = ["init", "--dir", str(tmp_path), "--no-detect", "--force"]
    for n, title in enumerate(("Second", "Third", "Fourth")):
        assert main([*args, "--title", title]) == I.EXIT_OK
        out = capsys.readouterr().out
        want = CONFIG_NAME + ".bak" + (f".{n}" if n else "")
        assert (tmp_path / want).is_file() and want in out
    assert (tmp_path / (CONFIG_NAME + ".bak")).read_text(encoding="utf-8") \
        == "# mine, the original\n"
    assert 'title = "Second"' in (tmp_path / (CONFIG_NAME + ".bak.1")).read_text(
        encoding="utf-8")
    assert 'title = "Third"' in (tmp_path / (CONFIG_NAME + ".bak.2")).read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- C5 shelf folder
def test_shelf_sends_only_the_folder_name(tmp_path):
    gone = tmp_path / "wren" / "skills"
    r = S.collect({"dirs": [str(gone)]}, NOW)
    assert r["folder"] == "skills" and str(tmp_path) not in json.dumps(r)
    (tmp_path / "otter" / "skills" / "kite").mkdir(parents=True)
    r = S.collect({"dirs": [str(tmp_path / "otter" / "skills")]}, NOW)
    assert r["folder"] == "skills" and r["folderHref"].startswith("file:")
    assert S.collect({"dirs": ["C:\\Users\\you\\.claude\\skills\\"]}, NOW)["folder"] == "skills"


# ----------------------------------------------------------------------------- C6 doctor, vault
def test_doctor_shows_a_broken_config_from_home_as_tilde(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "town").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    bad = home / "town" / CONFIG_NAME
    bad.write_text('version = 1\n[[building]]\nid = "Bad Id"\n', encoding="utf-8")
    c, _ = D.check_config(str(bad))
    assert c.level == D.FAIL and "lower-case" in c.detail
    assert str(home) not in c.detail and "~/town/" + CONFIG_NAME in c.detail
    c, _ = D.check_config(str(home / "town" / "nope.toml"))
    assert c.level == D.FAIL and str(home) not in c.detail and "~/town/nope.toml" in c.detail


def test_vault_item_regex_with_a_group_counts_items_not_groups(tmp_path):
    p = tmp_path / "2026-11-05.md"
    p.write_text("## One\ntext\n### Two\nmore\n", encoding="utf-8")
    got = V.read_inbox(p, {"item": r"^(##|###)\s+"}, None)
    assert [i["title"] for i in got["items"]] == ["One", "Two"] and got["total"] == 2


def test_vault_root_with_glob_characters(tmp_path):
    root = tmp_path / "kites [north]"
    (root / "reviews").mkdir(parents=True)
    (root / "reviews" / "2026-11-01-review.md").write_text("done\n", encoding="utf-8")
    day, note, setup = V.last_review(root, {"reviews": "reviews/*.md"})
    assert setup and day == dt.date(2026, 11, 1) and note is not None
    (root / "inbox").mkdir()
    (root / "inbox" / "2026-11-04.md").write_text("## One\n", encoding="utf-8")
    assert V.newest(V._inside(root, "inbox/*.md"), root) == root / "inbox" / "2026-11-04.md"


# ----------------------------------------------------------------------------- C6 clock changes
CHILD = r'''
import datetime as dt, json, sys
from pathlib import Path
from lamptown import build as B
from lamptown.collectors.jobs_schedule import LOCAL, parse
from lamptown.config import parse_config

d = Path(sys.argv[1])
jan, jul = (dt.datetime(2026, m, 15, 12).astimezone().utcoffset() for m in (1, 7))
out = {"dst": jan != jul}
(d / "logs").mkdir()
(d / "logs" / "run.log").write_text("Done: ok\n", encoding="utf-8")
text = ('version = 1\n[[building]]\nid = "jobs"\ntype = "jobs"\n  [[building.source]]\n'
        '  kind = "log_glob"\n  glob = "logs/*.log"\n  schedule = "daily 03:00"\n')
cfg = parse_config(text, d / "lamptown.toml")
# the day before the US clocks go forward (8 March 2026), at noon local time
res = B.build(cfg, out_dir=d / "out", now=dt.datetime(2026, 3, 7, 12))
out["next"] = res.data["rooms"]["jobs"]["rows"][0]["next"]
now = dt.datetime(2026, 3, 7, 12, tzinfo=LOCAL)
out["weekly"] = parse("weekly mon 03:00").next_after(now).isoformat()
u = dt.datetime(2026, 11, 1, 9, 30, tzinfo=dt.UTC)  # 01:30 the second time (PST)
back = u.astimezone(LOCAL)
out["fold"] = [back.isoformat(), back.fold, back.astimezone(dt.UTC) == u]
# does this OS show 01:30 twice that night? (some Linux zone data ends summer time earlier)
import time
walls = [time.localtime(x.timestamp())[3:5] for x in (u - dt.timedelta(hours=1), u)]
out["twice"] = walls == [(1, 30)] * 2
out["stamp"] = dt.datetime.fromtimestamp(u.timestamp(), tz=LOCAL).isoformat()
print(json.dumps(out))
'''


def test_schedules_follow_the_local_clock_across_a_clock_change(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    env = dict(os.environ, TZ="PST8PDT",
               PYTHONPATH=str(Path(lamptown.__file__).resolve().parent.parent))
    run = subprocess.run([sys.executable, str(script), str(work)], env=env, capture_output=True,
                         text=True, timeout=120, check=False)
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout.strip().splitlines()[-1])
    if not got["dst"]:
        pytest.skip("this OS ignores TZ=PST8PDT, so there is no clock change to test")
    assert got["next"] == "2026-03-08T03:00:00-07:00"  # not -08:00 (the build day's offset)
    assert got["weekly"] == "2026-03-09T03:00:00-07:00"
    # the second 01:30 has fold=1 where the OS repeats that hour; the instant always round-trips
    assert got["fold"] == ["2026-11-01T01:30:00-08:00", int(got["twice"]), True]
    assert got["stamp"] == "2026-11-01T01:30:00-08:00"


def _pacific_2026(u: float):
    """A fake US Pacific clock for 2026 (summer time 8 March 10:00 UTC to 1 November 09:00 UTC)."""
    start = dt.datetime(2026, 3, 8, 10, tzinfo=dt.UTC).timestamp()
    end = dt.datetime(2026, 11, 1, 9, tzinfo=dt.UTC).timestamp()
    return time.gmtime(u + (-7 if start <= u < end else -8) * 3600)


def test_the_local_zone_follows_the_clock_change_on_every_os(monkeypatch):
    from lamptown.collectors import jobs_schedule as J

    monkeypatch.setattr(J, "_localtime", _pacific_2026)
    hours = [dt.timedelta(hours=-7), dt.timedelta(hours=-8)]
    first = dt.datetime(2026, 11, 1, 8, 30, tzinfo=dt.UTC).astimezone(J.LOCAL)
    second = dt.datetime(2026, 11, 1, 9, 30, tzinfo=dt.UTC).astimezone(J.LOCAL)
    assert [first.isoformat(), first.fold] == ["2026-11-01T01:30:00-07:00", 0]
    assert [second.isoformat(), second.fold] == ["2026-11-01T01:30:00-08:00", 1]
    wall = dt.datetime(2026, 11, 1, 1, 30, tzinfo=J.LOCAL)
    assert [wall.utcoffset(), wall.replace(fold=1).utcoffset()] == hours
    after = dt.datetime(2026, 3, 7, 3, tzinfo=J.LOCAL) + dt.timedelta(days=1)
    assert after.utcoffset() == hours[0]  # the day after the clocks go forward
    gap = dt.datetime(2026, 3, 8, 2, 30, tzinfo=J.LOCAL)  # an hour the clock skips
    assert gap.utcoffset() in hours
    days = (dt.datetime(2026, 3, 8, 6, tzinfo=dt.UTC), dt.datetime(2026, 11, 1, 5, tzinfo=dt.UTC))
    for day in days:
        for minutes in range(0, 8 * 60, 15):
            u = day + dt.timedelta(minutes=minutes, microseconds=250)
            here = u.astimezone(J.LOCAL)
            assert here.astimezone(dt.UTC) == u
            assert here.replace(tzinfo=None, microsecond=0).timetuple()[:6] == _pacific_2026(
                u.timestamp())[:6]
