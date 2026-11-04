"""The live town's contract: ``[live]`` config, the two payloads, the folder -> plot rules, the
build's live files and a page that loads them. Every name and folder here is made up."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

import pytest

from _browser import browser
from lamptown import build as B
from lamptown import livetown as L
from lamptown.config import ConfigError, parse_config
from lamptown.status import events as ev
from lamptown.status.readers import BUILTIN
from lamptown.status.store import update_status
from lamptown.status.watch import Watcher
from test_town import run
from test_town_live import UNTIL

NOW = datetime(2026, 11, 4, 17, 48, tzinfo=UTC)


def at(hh: int, mm: int, day: int = 4) -> str:
    return datetime(2026, 11, day, hh, mm, tzinfo=UTC).isoformat().replace("+00:00", "Z")


def town_toml(work: Path, live: str = "", private: bool = True) -> str:
    w = work.as_posix()
    return f"""
[[building]]
id = "notes"
type = "vault"
  [[building.source]]
  kind = "markdown_vault"
  path = "{w}/vault"

[[building]]
id = "skills"
type = "shelf"
  [[building.source]]
  kind = "skill_dirs"
  dir = "{w}/skills"

[[building]]
id = "gate"
type = "ledger"
  [[building.source]]
  kind = "scan_glob"
  glob = "{w}/scans/*/report.json"

[[building]]
id = "contest"
type = "contest"
  [[building.source]]
  kind = "contest_json"
  glob = "{w}/ideas/**/contest.json"

[[building]]
id = "agents"
type = "harbour"
  [[building.source]]
  kind = "status_dir"
  dir = "{w}/status"

[live]
{f'private_dirs = ["{w}/secret"]' if private else ""}
{live}
"""


def config(tmp_path: Path, live: str = "", private: bool = True):
    work = tmp_path / "work"
    p = tmp_path / "lamptown.toml"
    text = town_toml(work, live, private)
    p.write_text(text, encoding="utf-8")
    return parse_config(text, p), work


def rec(sid: str, title: str, state: str, updated: str, **more) -> dict:
    return {"spec": "agent-status/1", "id": sid, "title": title, "state": state,
            "updated": updated, **more}


# ----------------------------------------------------------------------------- [live] config
def test_no_live_table_means_live_is_off():
    cfg = parse_config("")
    assert cfg.live.enabled is False and cfg.live.history_days == 7 and cfg.live.poll == 3.0


def test_live_table_switches_it_on_and_resolves_paths(tmp_path):
    p = tmp_path / "lamptown.toml"
    text = """
[live]
poll = 2
history_days = 7
private_dirs = "private"
status_dir = "status"

[[live.reader]]
kind = "claude-code"
root = "logs"
root_dir = "logs"

[[live.reader]]
reader = "./mine.py"
limit = 3

[[live.place]]
dir = "work/ideas"
plot = "lab"
"""
    cfg = parse_config(text, p)
    live = cfg.live
    assert live.enabled is True and live.poll == 2.0
    assert live.private_dirs == (str(tmp_path / "private"),)
    assert live.status_dir == str(tmp_path / "status")
    assert live.readers[0]["kind"] == "claude-code"
    assert live.readers[0]["root"] == "logs"  # not a path key: left as written
    assert live.readers[0]["root_dir"] == str(tmp_path / "logs")
    assert live.readers[1] == {"reader": str(tmp_path / "mine.py"), "limit": 3}
    assert live.places[0].dir == str(tmp_path / "work" / "ideas")
    assert live.places[0].plot == "lab"
    assert parse_config("[live]\nenabled = false").live.enabled is False


@pytest.mark.parametrize("toml, words", [
    ("live = 3", "live: must be a table"),
    ("[live]\nenabled = \"yes\"", "live.enabled: must be true or false"),
    ("[live]\npoll = 0", "live.poll: must be a number of seconds from 1 to 60"),
    ("[live]\npoll = true", "live.poll"),
    ("[live]\nhistory_days = 0", "live.history_days: must be a whole number"),
    ("[live]\nhistory_days = 2.5", "live.history_days"),
    ("[live]\nfoo = 1", "live: unknown key 'foo'"),
    ("[live]\nprivate_dirs = 5", "live.private_dirs: must be a path or a list"),
    ("[live]\nreader = 1", "live.reader: must be an array of tables"),
    ("[[live.reader]]\nname = \"x\"", "live.reader #1: needs kind"),
    ("[[live.reader]]\nkind = \"Bad Kind\"", "live.reader #1.kind: 'Bad Kind' must be"),
    ("[[live.reader]]\nkind = \"x\"\n_secret = 1", "keys starting with '_' are reserved"),
    ("[[live.place]]\ndir = \"x\"", "live.place #1: needs dir"),
    ("[[live.place]]\ndir = \"x\"\nplot = \"lighthouse\"", "'lighthouse' is not a plot"),
    ("[[live.place]]\ndir = \"x\"\nplot = \"tower\"\nwhy = 1", "unknown key 'why'"),
    ("[[building]]\nid = \"lighthouse\"\ntype = \"jobs\"", "reserved for the live town"),
    ("[[building]]\nid = \"beacon\"\ntype = \"jobs\"\nplot = \"lighthouse\"",
     "the live town's own place, not a plot"),
    ("[[building]]\nid = \"beacon\"\ntype = \"lighthouse\"\nplot = \"lab\"",
     "the room type 'lighthouse' is reserved"),
])
def test_live_config_errors_read_clearly(toml, words):
    with pytest.raises(ConfigError) as e:
        parse_config(toml)
    assert words in str(e.value)


def test_the_missing_kind_hint_names_only_real_readers():
    # review L12 m3: the hint said kind = "claude-code", a reader that doesn't exist
    with pytest.raises(ConfigError) as e:
        parse_config("[[live.reader]]\nname = \"x\"")
    hint = str(e.value).split("needs kind = ", 1)[1].split("(", 1)[0]
    named = set(re.findall(r'"([^"]+)"', hint))
    assert named == set(BUILTIN), (hint, sorted(BUILTIN))


# ----------------------------------------------------------------------------- folder -> plot
def test_plots_come_from_each_buildings_source_paths(tmp_path):
    cfg, work = config(tmp_path)
    # a glob with a wildcard folder counts the folders of the files it matches (L3-fix 6)
    for folder, name in (("scans/kite-knots", "report.json"), ("ideas/sprint-3", "contest.json")):
        (work / folder).mkdir(parents=True)
        (work / folder / name).write_text("{}", encoding="utf-8")
    plot = L.plot_map(cfg)
    f = lambda *parts: {"x-folder": str(work.joinpath(*parts))}  # noqa: E731
    assert plot(f("vault")) == "library" and plot(f("vault", "inbox")) == "library"
    assert plot(f("skills", "tide-reader")) == "workshop"
    assert plot(f("scans", "kite-knots")) == "gate"
    assert plot(f("scans", "other")) == "boathouse"  # no report there: not the gate's
    assert plot(f("ideas", "sprint-3")) == "lab"
    assert plot(f("vaulted")) == "boathouse"  # a name that starts the same is not inside
    assert plot(f("paper-kite")) == "boathouse"  # any other working folder: the harbour
    assert plot({}) == "boathouse" and plot({"x-folder": 7}) == "boathouse"
    # the status folder is not a place work happens in
    assert plot(f("status", "x")) == "boathouse"


def test_places_override_and_the_most_specific_rule_wins(tmp_path):
    w = (tmp_path / "work").as_posix()
    cfg, work = config(tmp_path, f"""
[[live.place]]
dir = "{w}/skills"
plot = "lab"

[[live.place]]
dir = "{w}/vault/drafts/*"
plot = "gate"

[[live.place]]
dir = "{w}"
plot = "tower"
""")
    plot = L.plot_map(cfg)
    f = lambda *parts: {"x-folder": str(work.joinpath(*parts))}  # noqa: E731
    assert plot(f("skills", "kite")) == "lab"  # a tie: the override wins
    assert plot(f("vault", "drafts", "a")) == "gate"  # more specific than the vault
    assert plot(f("vault", "inbox")) == "library"  # the vault is more specific than work/
    assert plot(f("paper-kite")) == "tower"  # the broad override beats the default
    # a reader's x-plot wins, then jobs go to the tower
    assert plot({**f("vault"), "x-plot": "lab"}) == "lab"
    assert plot({**f("vault"), "x-plot": "nowhere"}) == "library"
    assert plot({**f("vault"), "kind": "job"}) == "tower"


def test_rules_that_would_swallow_every_folder_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home" / "you"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home" / "you"))
    home = (tmp_path / "home" / "you").as_posix()
    text = f"""
[[building]]
id = "skills"
type = "shelf"
  [[building.source]]
  kind = "skill_dirs"
  dirs = ["{home}", "{home}/skills"]
  shortnames_file = "{home}/names.json"

[[building]]
id = "contest"
type = "contest"
  [[building.source]]
  kind = "contest_json"
  file = "{home}/contest.json"
"""
    cfg = parse_config(text, tmp_path / "x.toml")
    roots = {(r.root.rsplit("/", 1)[-1], r.plot) for r in L.plot_map(cfg).rules}
    assert roots == {("skills", "workshop")}  # home itself, and a file's folder at home: gone
    assert L.static_prefix("/work/a/*/b.json") == "/work/a"
    assert L.static_prefix("work\\ideas\\**\\c.json") == "work\\ideas"  # Windows separators
    assert L.static_prefix("/work/plain") == "/work/plain"


def test_custom_sources_and_local_collectors_count_their_dir_and_glob_keys(tmp_path):
    w = (tmp_path / "work").as_posix()
    text = f"""
[[building]]
id = "kiosk"
type = "kiosk"
plot = "lab"
  [[building.source]]
  kind = "my-kind"
  notes_dir = "{w}/kiosk"
  data_glob = "{w}/kiosk-data/*.json"
  menu_file = "{w}/menu.json"
"""
    cfg = parse_config(text, tmp_path / "x.toml")
    names = sorted(r.root.rsplit("/", 1)[-1] for r in L.plot_map(cfg).rules)
    assert names == ["kiosk", "kiosk-data"]


def test_the_watcher_gets_the_same_folder_private_dirs_and_rules(tmp_path):
    cfg, work = config(tmp_path)
    opts = L.watcher_options(cfg)
    assert opts["status_dir"] == str(L.live_status_dir(cfg))
    assert Path(opts["status_dir"]) == work / "status"
    assert opts["private_dirs"] == [str(work / "secret")]
    with Watcher(readers=(), **opts) as w:
        assert w.plot_for({"x-folder": str(work / "vault" / "a")}) == "library"
        assert w.private_id("claude-code-h1") == L.private_id(L.live_status_dir(cfg),
                                                              "claude-code-h1")


# ----------------------------------------------------------------------------- live payload
def test_live_payload_golden(tmp_path):
    cfg, work = config(tmp_path)
    fold = lambda name: str(work / name)  # noqa: E731
    records = [
        rec("codex-3f9a", "Fix save slots", "working", at(17, 47), agent="codex",
            started=at(17, 40), **{"x-folder": fold("paper-kite"), "x-files": 14,
                                   "x-commits": 2, "x-pushed": True, "x-ci": "running"}),
        rec("claude-code-77aa", f"Tidy {work / 'vault' / 'inbox' / 'note.md'}", "waiting",
            at(17, 30), agent="claude-code",
            **{"x-folder": str(work / "vault" / "inbox"), "x-parent": "codex-3f9a"}),
        rec("codex-old", "Long done", "ok", at(17, 0), agent="codex"),
        rec("codex-done", "Polish the kite", "ok", at(17, 45), agent="codex",
            **{"x-folder": str(work / "skills" / "kite")}),
        rec("claude-code-stale", "Stuck", "working", at(17, 0), agent="claude-code",
            **{"x-folder": fold("otter")}),
        rec("codex-quiet", "Sleepy", "working", at(8, 0), agent="codex",
            **{"x-folder": fold("lab-notes")}),
        rec("proj-heron", "heron", "ok", at(17, 41), kind="project"),
        rec("job-nightly", "Nightly backup", "working", at(17, 47), kind="job",
            agent="cron", **{"x-parent": "codex-gone"}),
        rec("claude-code-h1", "Secret plan", "working", at(17, 46), agent="claude-code",
            **{"x-folder": str(work / "secret" / "hidden-kite"), "x-project": "hidden-kite"}),
    ]
    p = L.live_payload(records, cfg, NOW, rev="abc123")
    masked = L.private_id(L.live_status_dir(cfg), "claude-code-h1")

    def agent(sid, tool, title, project, plot, state, since, **more):  # noqa: PLR0917
        return {"id": sid, "tool": tool, "title": title, "project": project, "plot": plot,
                "state": state, "since": since, "parent": None, "subagents": 0, "files": 0,
                "commits": 0, "pushed": False, "ci": None, "private": False, **more}

    assert p == {
        "spec": "lamptown-live/1", "at": "2026-11-04T17:48:00.000Z", "rev": "abc123",
        "need": 1, "more": 0,
        "agents": [
            agent("claude-code-77aa", "claude-code", "Tidy note.md", "inbox", "library",
                  "waiting", at(17, 30), parent="codex-3f9a"),
            agent("claude-code-stale", "claude-code", "Stuck", "otter", "boathouse", "error",
                  at(17, 30)),
            agent("codex-3f9a", "codex", "Fix save slots", "paper-kite", "boathouse",
                  "working", at(17, 40), subagents=1, files=14, commits=2, pushed=True,
                  ci="running"),
            agent("job-nightly", "cron", "Nightly backup", "", "tower", "working", at(17, 47)),
            agent(masked, "claude-code", "Private agent", "", "boathouse", "working",
                  at(17, 46), private=True),
            agent("codex-quiet", "codex", "Sleepy", "lab-notes", "boathouse", "quiet",
                  at(14, 0)),
            agent("codex-done", "codex", "Polish the kite", "kite", "workshop", "ok",
                  at(17, 45)),
        ],
        "projects": [
            {"id": "lab-notes", "name": "LAB NOTES", "berth": 0, "new": False},
            {"id": "inbox", "name": "INBOX", "berth": 1, "new": False},
            {"id": "otter", "name": "OTTER", "berth": 2, "new": False},
            {"id": "paper-kite", "name": "PAPER KITE", "berth": 3, "new": True},
            {"id": "heron", "name": "HERON", "berth": 4, "new": True},
            {"id": "kite", "name": "KITE", "berth": 5, "new": True},
        ],
    }
    text = json.dumps(p)
    assert "Secret plan" not in text and "hidden-kite" not in text and "secret" not in text
    assert tmp_path.name not in text and work.as_posix() not in text  # names only, no paths


def test_the_payload_is_capped_and_at_is_the_write_time(tmp_path):
    cfg, _ = config(tmp_path, private=False)
    many = [rec(f"codex-{i:04d}", "x" * 500, "working", at(17, 47), agent="codex")
            for i in range(L.MAX_AGENTS + 5)]
    p = L.live_payload(many, cfg, NOW, at=NOW + timedelta(seconds=1, milliseconds=5))
    assert len(p["agents"]) == L.MAX_AGENTS and p["more"] == 5
    assert all(len(a["title"]) <= 120 for a in p["agents"])
    assert p["at"] == "2026-11-04T17:48:01.005Z" and p["rev"] == ""


# ----------------------------------------------------------------------------- history payload
def _ev(t, kind, sid="codex-3f9a", **more):
    return {"t": t, "id": sid, "k": kind, "title": "Fix save slots", "project": "paper-kite",
            "tool": "codex", "plot": "boathouse", **more}


def test_history_payload_golden(tmp_path):
    cfg, _ = config(tmp_path, "history_days = 3")
    events = [
        _ev(at(9, 7), "start"), _ev(at(9, 20), "files", n=12), _ev(at(9, 21), "commit", n=1),
        _ev(at(9, 50), "push"), _ev(at(9, 56), "ci", ok=True), _ev(at(10, 0), "done"),
        {"t": at(10, 5), "id": "proj-heron", "k": "project", "title": "heron",
         "project": "heron", "tool": "", "plot": "boathouse"},
        {"t": at(10, 10), "id": "private-0123456789", "k": "start", "title": "Private agent",
         "project": "", "tool": "claude-code", "plot": "library"},
        _ev(at(23, 59, day=2), "fail", sid="codex-b"),
        _ev(at(12, 0, day=1), "start", sid="codex-too-old"),  # outside the 3 days
    ]
    h = L.history_payload(events, cfg, NOW, UTC)
    assert h == {"spec": "lamptown-history/1", "at": "2026-11-04T17:48:00.000Z", "days": [
        {"date": "2026-11-02", "today": False, "minutes": 1440,
         "agents": [{"id": "codex-b", "name": "PAPER KITE / FIX SAVE SLOTS",
                     "plot": "boathouse", "tool": "codex"}],
         "events": [[1439, 0, "fail"]]},
        {"date": "2026-11-03", "today": False, "minutes": 1440, "agents": [], "events": []},
        {"date": "2026-11-04", "today": True, "minutes": 1440,
         "agents": [
             {"id": "codex-3f9a", "name": "PAPER KITE / FIX SAVE SLOTS", "plot": "boathouse",
              "tool": "codex"},
             {"id": "proj-heron", "name": "HERON", "plot": "boathouse", "tool": ""},
             {"id": "private-0123456789", "name": "PRIVATE AGENT", "plot": "library",
              "tool": "claude-code"}],
         "events": [[547, 0, "start"], [560, 0, "files", 12], [561, 0, "commit", 1],
                    [590, 0, "push"], [596, 0, "ci", 1], [600, 0, "done"],
                    [605, 1, "project"], [610, 2, "start"]]},
    ]}


def test_history_reads_the_event_log_seven_days(tmp_path):
    cfg, work = config(tmp_path)
    status = work / "status"
    ev.append(status, [_ev(at(9, 7), "start"), _ev(at(9, 9, day=3), "done")], tz=UTC,
              now=NOW)
    h = L.history_payload(L.read_history(cfg, NOW, UTC), cfg, NOW, UTC)
    assert [d["date"] for d in h["days"]] == [f"2026-10-{d}" for d in (29, 30, 31)] + [
        f"2026-11-0{d}" for d in (1, 2, 3, 4)]
    assert h["days"][-1]["today"] and h["days"][-1]["events"] == [[547, 0, "start"]]
    assert h["days"][-2]["events"] == [[549, 0, "done"]]


class TwoSwitches(tzinfo):
    """A made-up zone: UTC+1, and UTC+2 from 2026-03-29 01:00 UTC to 2026-10-25 01:00 UTC
    (local 02:00 -> 03:00 in spring, 03:00 -> 02:00 in autumn)."""

    SPRING = datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
    AUTUMN = datetime(2026, 10, 25, 1, 0, tzinfo=UTC)

    def utcoffset(self, dt):
        naive = dt.replace(tzinfo=None)
        end = datetime(2026, 10, 25, 3 if dt.fold == 0 else 2)
        return timedelta(hours=2 if datetime(2026, 3, 29, 3) <= naive < end else 1)

    def dst(self, dt):
        return self.utcoffset(dt) - timedelta(hours=1)

    def tzname(self, dt):
        return "XST"

    def fromutc(self, dt):
        utc = dt.replace(tzinfo=UTC)
        summer = self.SPRING <= utc < self.AUTUMN
        local = (utc + timedelta(hours=2 if summer else 1)).replace(tzinfo=self)
        if self.AUTUMN <= utc < self.AUTUMN + timedelta(hours=1):
            local = local.replace(fold=1)  # the second 02:xx
        return local


@pytest.mark.parametrize("day, now, minutes, late_utc, late_minute", [
    # spring: a 23-hour day; 23:30 local (+2) is 21:30 UTC, 22.5 h after midnight (+1)
    ("2026-03-29", datetime(2026, 3, 30, 12, 0, tzinfo=UTC), 1380,
     datetime(2026, 3, 29, 21, 30, tzinfo=UTC), 1350),
    # autumn: a 25-hour day; 23:30 local (+1) is 22:30 UTC, 24.5 h after midnight (+2)
    ("2026-10-25", datetime(2026, 10, 26, 12, 0, tzinfo=UTC), 1500,
     datetime(2026, 10, 25, 22, 30, tzinfo=UTC), 1470),
])
def test_dst_days_are_23_or_25_hours_long(  # noqa: PLR0917
        tmp_path, day, now, minutes, late_utc, late_minute):
    cfg, _ = config(tmp_path, "history_days = 2")
    zone = TwoSwitches()
    t = lambda m: m.isoformat().replace("+00:00", "Z")  # noqa: E731
    start = L.midnight(datetime.fromisoformat(day).date(), zone)
    events = [_ev(t(start), "start"), _ev(t(start + timedelta(hours=2, minutes=30)), "files",
                                          n=3),
              _ev(t(late_utc), "done")]
    h = L.history_payload(events, cfg, now, zone)
    first = h["days"][0]
    assert first["date"] == day and first["minutes"] == minutes
    assert h["days"][1]["minutes"] == 1440
    assert first["events"] == [[0, 0, "start"], [150, 0, "files", 3], [late_minute, 0, "done"]]
    assert all(0 <= e[0] < first["minutes"] for e in first["events"])


def test_a_real_zone_too():
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        zone = zoneinfo.ZoneInfo("Europe/London")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no time zone database here")
    d = datetime(2026, 3, 29).date()
    assert L.day_minutes(d, zone) == 1380
    assert L.day_minutes(datetime(2026, 10, 25).date(), zone) == 1500
    assert L.day_minutes(datetime(2026, 11, 4).date(), zone) == 1440


# ----------------------------------------------------------------------------- the build
def _all_text(out: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(out.iterdir())
                     if p.is_file() and p.suffix in (".html", ".js"))


def test_build_writes_the_live_files_with_one_rev(tmp_path):
    cfg, work = config(tmp_path)
    status = work / "status"
    update_status("codex-3f9a", status, now=NOW - timedelta(minutes=1), title="Fix save slots",
                  state="waiting", agent="codex", **{"x-folder": str(work / "skills" / "k")})
    out = tmp_path / "out"
    res = B.build(cfg, out_dir=out, now=NOW, tz=UTC)
    names = sorted(p.name for p in out.iterdir())
    assert names == ["town-data.js", "town-details.html", "town-history.js", "town-live.js",
                     "town.html"]
    rev = res.data["rev"]
    assert isinstance(rev, str) and len(rev) == 16
    data_js = (out / "town-data.js").read_text(encoding="utf-8")
    assert data_js.startswith("lamptownData({") and data_js.endswith("});\n")
    assert B.read_rev(out) == rev
    live = _payload(out / "town-live.js", "lamptownLive")
    assert live["rev"] == rev and live["spec"] == "lamptown-live/1" and live["need"] == 1
    assert live["agents"][0]["plot"] == "workshop"
    age = datetime.now(UTC) - datetime.fromisoformat(live["at"].replace("Z", "+00:00"))
    assert timedelta(0) <= age < timedelta(seconds=60)  # the write time, not the build's now
    hist = _payload(out / "town-history.js", "lamptownHistory")
    assert hist["spec"] == "lamptown-history/1" and len(hist["days"]) == 7
    page = res.town.read_text(encoding="utf-8")
    assert f'"rev": "{rev}"' in page
    # the lighthouse: a place of its own, never one of the six plots
    part = res.data["parts"]["lighthouse"]
    assert part["plot"] == "lighthouse" and part["lamp"] == "amber" and part["num"] == "1"
    assert "lighthouse" not in res.data["plots"] and len(res.data["plots"]) == 6
    details = res.details.read_text(encoding="utf-8")
    assert "Lighthouse today" in details and "Fix save slots" in details
    assert 'id="lighthouse"' in details


def _payload(path: Path, fn: str) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    assert text.startswith(fn + "(") and text.endswith(");")
    return json.loads(text[len(fn) + 1:-2])


def test_rev_changes_with_the_data_not_the_build_time(tmp_path):
    cfg, _ = config(tmp_path)
    a = B.build(cfg, out_dir=tmp_path / "a", now=NOW, tz=UTC).data["rev"]
    b = B.build(cfg, out_dir=tmp_path / "b", now=NOW + timedelta(minutes=5), tz=UTC).data["rev"]
    assert a == b
    other = parse_config(town_toml(tmp_path / "work").replace('id = "gate"', 'id = "gates"'),
                         tmp_path / "lamptown.toml")
    assert B.build(other, out_dir=tmp_path / "c", now=NOW, tz=UTC).data["rev"] != a


def test_without_live_only_town_data_js_and_no_lighthouse(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "town-live.js").write_text("lamptownLive({});\n", encoding="utf-8")  # an old run's
    res = B.build(parse_config(""), out_dir=out, now=NOW)
    assert sorted(p.name for p in out.iterdir()) == ["town-data.js", "town-details.html",
                                                     "town.html"]
    assert "lighthouse" not in res.data["parts"] and res.live is None
    assert "Lighthouse today" not in res.details.read_text(encoding="utf-8")
    assert B.read_rev(out) == res.data["rev"]


def test_private_agents_leave_no_name_or_folder_in_any_file(tmp_path):
    cfg, work = config(tmp_path)
    status = work / "status"
    opts = L.watcher_options(cfg)
    with Watcher(readers=(), tz=UTC, **opts) as w:
        w.tick(NOW - timedelta(minutes=3))
        update_status("claude-code-h1", status, now=NOW - timedelta(minutes=2),
                      title="Secret plan", state="waiting", agent="claude-code",
                      needs_you=[{"text": "Approve the secret plan"}],
                      **{"x-folder": str(work / "secret" / "hidden-kite"),
                         "x-project": "hidden-kite"})
        w.tick(NOW - timedelta(minutes=2))
        masked = w.private_id("claude-code-h1")
    out = tmp_path / "out"
    res = B.build(cfg, out_dir=out, now=NOW, tz=UTC)
    text = _all_text(out)
    for word in ("Secret plan", "secret plan", "hidden-kite", "HIDDEN", "claude-code-h1",
                 str(work / "secret"), (work / "secret").as_posix()):
        assert word not in text, word
    live = _payload(out / "town-live.js", "lamptownLive")
    assert live["agents"][0]["id"] == masked and live["agents"][0]["private"] is True
    assert live["agents"][0]["title"] == "Private agent" and live["agents"][0]["project"] == ""
    hist = _payload(out / "town-history.js", "lamptownHistory")
    today = hist["days"][-1]
    assert today["agents"][0] == {"id": masked, "name": "PRIVATE AGENT", "plot": "boathouse",
                                  "tool": "claude-code"}
    # the harbour room masks the same record with the same id (one status folder text)
    rows = res.data["rooms"]["agents"]["rows"]
    assert [r["id"] for r in rows] == [masked] and rows[0]["name"] == "Private agent"


def test_private_dirs_of_a_status_source_count_for_the_live_town(tmp_path):
    w = (tmp_path / "work").as_posix()
    text = f"""
[[building]]
id = "agents"
type = "harbour"
  [[building.source]]
  kind = "status_dir"
  dir = "{w}/status"
  private_dirs = ["{w}/hush"]

[live]
"""
    cfg = parse_config(text, tmp_path / "x.toml")
    ctx = L.context(cfg)
    assert ctx.private(f"{w}/hush/a") and not ctx.private(f"{w}/open")
    every = parse_config(text.replace('private_dirs = ["' + w + '/hush"]', "private = true"),
                         tmp_path / "x.toml")
    assert L.context(every).private(f"{w}/open")  # the whole status folder is private


# ----------------------------------------------------------------------------- the tick writer
def test_write_live_files_refreshes_at_and_history_once_a_minute(tmp_path):
    cfg, _ = config(tmp_path)
    out = tmp_path / "out"
    res = B.build(cfg, out_dir=out, now=NOW, tz=UTC)
    first = _payload(out / "town-live.js", "lamptownLive")["at"]
    os.utime(out / "town-history.js", (time.time() - 5, time.time() - 5))
    time.sleep(0.01)
    cache: dict = {}
    tick = B.write_live_files(cfg, out, tz=UTC, cache=cache)
    assert tick.history is None  # written 5 s ago: not due
    assert tick.payload["rev"] == res.data["rev"]  # read back from town-data.js
    assert tick.payload["at"] > first  # a heartbeat even with nothing new
    old = time.time() - B.HISTORY_EVERY - 1
    os.utime(out / "town-history.js", (old, old))
    tick = B.write_live_files(cfg, out, rev="r9", tz=UTC, cache=cache)
    assert tick.history == out / "town-history.js"
    assert _payload(out / "town-live.js", "lamptownLive")["rev"] == "r9"
    assert sorted(p.name for p in out.iterdir() if p.suffix == ".tmp") == []


# ----------------------------------------------------------------------------- the page
@browser
def test_a_built_live_town_loads_town_live_js_and_says_live(tmp_path):
    cfg, work = config(tmp_path)
    update_status("codex-3f9a", work / "status", title="Fix save slots", state="working",
                  agent="codex", **{"x-folder": str(work / "paper-kite")})
    out = tmp_path / "out"
    res = B.build(cfg, out_dir=out, tz=UTC)
    B.write_live_files(cfg, out, rev=res.data["rev"], tz=UTC)  # a fresh heartbeat
    r = run(res.town, UNTIL + """
      const on = await until(() => LIVE.on);
      const p = LIVE.payload || {};
      const r = { on, corner: SEEN.corner, rev: LIVE.rev, loads: LIVE.loads, swaps: LIVE.swaps,
        payloadRev: p.rev, agents: (p.agents || []).map(a => [a.title, a.plot, a.state]),
        lighthouse: PT.lighthouse && PT.lighthouse.plot, lots: SEEN.lots,
        plates: Object.keys(SEEN.plates).length };
      quiet();
      return r;""")
    assert r["on"] is True and r["corner"] == "LIVE"
    assert r["rev"] == res.data["rev"] == r["payloadRev"]
    assert r["loads"] == 0 and r["swaps"] == 0  # the same rev: nothing to swap in
    assert r["agents"] == [["Fix save slots", "boathouse", "working"]]
    assert r["lighthouse"] == "lighthouse" and r["plates"] == 6
    assert "lighthouse" not in r["lots"]


# ----------------------------------------------------------------------------- L3-fix regressions
# (review-L3 findings 1-7; each fails before its fix)
def _write_events(status: Path, lines: list[str]) -> None:
    path = ev.day_path(status, NOW.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(x + "\n" for x in lines), encoding="utf-8")


def test_vars_in_live_private_dirs_status_dir_and_places_are_expanded(tmp_path, monkeypatch):
    work = tmp_path / "work"
    monkeypatch.setenv("LT_WORK", str(work))
    text = """
[live]
private_dirs = ["$LT_WORK/secret"]
status_dir = "$LT_WORK/status"

[[live.place]]
dir = "$LT_WORK/ideas"
plot = "lab"
"""
    cfg = parse_config(text, tmp_path / "lamptown.toml")
    assert cfg.live.private_dirs == (str(work / "secret"),)
    assert cfg.live.status_dir == str(work / "status")
    assert cfg.live.places[0].dir == str(work / "ideas")
    assert L.plot_map(cfg)({"x-folder": str(work / "ideas" / "s1")}) == "lab"
    update_status("claude-code-h1", work / "status", now=NOW - timedelta(minutes=2),
                  title="Secret plan", state="waiting", agent="claude-code",
                  **{"x-folder": str(work / "secret" / "hidden-kite")})
    out = tmp_path / "out"
    B.build(cfg, out_dir=out, now=NOW, tz=UTC)
    text = _all_text(out)
    for word in ("Secret plan", "hidden-kite", "HIDDEN KITE"):
        assert word not in text, word
    live = _payload(out / "town-live.js", "lamptownLive")
    assert live["agents"][0]["private"] is True


def _fake_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path.joinpath("Users", "rowan-login")
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def test_an_agent_in_the_home_folder_gets_no_project_name(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    work = tmp_path / "work"
    cfg = parse_config(f'[live]\nstatus_dir = "{(work / "status").as_posix()}"\n',
                       tmp_path / "lamptown.toml")

    def project(**more) -> tuple[str, list]:
        doc = rec("claude-code-1", "Tidy up", "working", at(17, 47), **more)
        p = L.live_payload([doc], cfg, NOW)
        return p["agents"][0]["project"], [x["id"] for x in p["projects"]]

    login = "rowan-login"
    for folder, named in [(str(home), login), (str(home.parent), "Users"),
                          (str(Path(str(home)).anchor or "/"), ""),
                          ("C:\\Users\\you", "you"), ("/Users/you", "you"), ("/home/you", "you"),
                          ("~", login)]:
        assert project(**{"x-folder": folder}) == ("", []), folder
        assert project(**{"x-folder": folder, "x-project": named}) == ("", []), folder
    assert project(**{"x-project": login}) == ("", [])  # no folder: the login name still never
    assert project(**{"x-folder": str(home / "paper-kite")}) == ("paper-kite", ["paper-kite"])
    # a real project folder that happens to share the name is still a project
    assert project(**{"x-folder": str(work / login), "x-project": login}) == (login, [login])
    assert project(**{"x-folder": str(home), "x-project": "paper-kite"})[0] == "paper-kite"
    # end to end: the Watcher's event log and every built file
    with Watcher(readers=(), tz=UTC, **L.watcher_options(cfg)) as w:
        w.tick(NOW - timedelta(minutes=3))
        update_status("claude-code-2", work / "status", now=NOW - timedelta(minutes=2),
                      title="Tidy up", state="working", agent="claude-code",
                      **{"x-folder": str(home), "x-project": login})
        w.tick(NOW - timedelta(minutes=2))
    logged = ev.read_day(ev.day_path(work / "status", NOW.date()))
    assert logged and all(e["project"] == "" for e in logged)
    out = tmp_path / "out"
    B.build(cfg, out_dir=out, now=NOW, tz=UTC)
    text = _all_text(out).lower()
    assert "rowan" not in text
    hist = _payload(out / "town-history.js", "lamptownHistory")
    assert hist["days"][-1]["agents"][0]["name"] == "TIDY UP"
    # an old log line that still carries the login name is cleaned on the way out too
    _write_events(work / "status", ['{"t":"2026-11-04T17:00:00Z","id":"codex-a","k":"start",'
                                    '"title":"Old","project":"rowan-login"}'])
    hist = L.history_payload(L.read_history(cfg, NOW, UTC), cfg, NOW, UTC)
    assert hist["days"][-1]["agents"][0]["name"] == "OLD"


BAD_TIMES = ['{"t":"0001-01-01T00:30:00+01:00","id":"codex-a","k":"start"}',
             '{"t":"9999-12-31T23:30:00-01:00","id":"codex-b","k":"start"}',
             '{"t":"0001-01-01T00:00:00Z","id":"codex-c","k":"start"}']


def test_a_bad_time_in_the_event_log_is_skipped_and_never_stops_a_build(tmp_path):
    cfg, work = config(tmp_path)
    status = work / "status"
    good = '{"t":"2026-11-04T17:00:00Z","id":"codex-d","k":"start","title":"Fix save slots"}'
    _write_events(status, [*BAD_TIMES, good])
    assert [e["id"] for e in ev.read_day(ev.day_path(status, NOW.date()))] == ["codex-d"]
    for line in BAD_TIMES:
        assert ev.clean_event(json.loads(line)) is None, line
    flat = [json.loads(x) for x in BAD_TIMES]
    hist = L.history_payload(flat, cfg, NOW, UTC)  # a flat list: grouped by day here
    assert all(not d["events"] for d in hist["days"])
    out = tmp_path / "out"
    res = B.build(cfg, out_dir=out, now=NOW, tz=UTC)
    names = [a["name"] for a in _payload(out / "town-history.js", "lamptownHistory")["days"][-1]
             ["agents"]]
    assert names == ["FIX SAVE SLOTS"] and res.warnings == []
    assert B.write_live_files(cfg, out, tz=UTC, history=True).history == out / "town-history.js"


def test_a_history_that_cant_be_read_is_empty_with_a_warning(tmp_path, monkeypatch):
    cfg, _ = config(tmp_path)

    def broken(*a, **k):
        raise OverflowError("date value out of range")

    monkeypatch.setattr(L, "read_history", broken)
    out = tmp_path / "out"
    res = B.build(cfg, out_dir=out, now=NOW, tz=UTC)
    assert any("history" in w for w in res.warnings), res.warnings
    hist = _payload(out / "town-history.js", "lamptownHistory")
    assert len(hist["days"]) == 7 and all(not d["events"] for d in hist["days"])
    tick = B.write_live_files(cfg, out, tz=UTC, history=True)
    assert tick.history == out / "town-history.js" and tick.warnings


def test_an_x_project_path_under_a_private_dir_is_private(tmp_path):
    cfg, work = config(tmp_path)
    hidden = str(work / "secret" / "hidden-kite")
    doc = rec("claude-code-h1", "Secret plan", "waiting", at(17, 47), **{"x-project": hidden})
    a = L.live_payload([doc], cfg, NOW)["agents"][0]
    assert a["private"] is True and a["project"] == "" and a["title"] == "Private agent"
    # a bare name is no path: not private
    plain = rec("codex-1", "Fix", "working", at(17, 47), **{"x-project": "hidden-kite"})
    assert L.live_payload([plain], cfg, NOW)["agents"][0]["private"] is False
    with Watcher(readers=(), tz=UTC, **L.watcher_options(cfg)) as w:
        w.tick(NOW - timedelta(minutes=3))
        update_status("claude-code-h2", work / "status", now=NOW - timedelta(minutes=2),
                      title="Secret plan", state="waiting", agent="claude-code",
                      **{"x-project": hidden})
        w.tick(NOW - timedelta(minutes=2))
    out = tmp_path / "out"
    B.build(cfg, out_dir=out, now=NOW, tz=UTC)
    text = _all_text(out)
    for word in ("Secret plan", "hidden-kite", "HIDDEN KITE"):
        assert word not in text, word


def test_a_status_sources_ageing_options_count_for_the_live_town(tmp_path):
    w = (tmp_path / "work").as_posix()
    text = f"""
[[building]]
id = "agents"
type = "harbour"
  [[building.source]]
  kind = "status_dir"
  dir = "{w}/status"
  stale_after = 600
  quiet_after = 1200
  expire_after = 3600

[live]
"""
    cfg = parse_config(text, tmp_path / "lamptown.toml")
    status = tmp_path / "work" / "status"
    for sid, mins, state in (("codex-stale", 15, "working"), ("codex-quiet", 25, "working"),
                             ("codex-gone", 90, "waiting"), ("codex-fresh", 5, "working")):
        update_status(sid, status, now=NOW - timedelta(minutes=mins), title=sid, state=state)
    res = B.build(cfg, out_dir=tmp_path / "out", now=NOW, tz=UTC)
    room = {r["id"]: ("quiet" if r.get("why") == "quiet" else r["state"])
            for r in res.data["rooms"]["agents"]["rows"]}
    live = {a["id"]: a["state"] for a in
            L.live_payload(L.read_records(status), cfg, NOW)["agents"]}
    assert room == live == {"codex-stale": "error", "codex-quiet": "quiet",
                            "codex-fresh": "working"}
    opts = L.watcher_options(cfg)
    assert (opts["stale_after"], opts["quiet_after"], opts["expire_after"]) == (600, 1200, 3600)
    # without the options: the defaults, as before
    (tmp_path / "b").mkdir()
    plain = L.watcher_options(config(tmp_path / "b")[0])
    assert plain["stale_after"] == 1800 and plain["quiet_after"] == 21600


def test_a_glob_with_a_wildcard_folder_claims_only_the_folders_it_matches(tmp_path):
    w = (tmp_path / "work").as_posix()
    text = f"""
[[building]]
id = "contest"
type = "contest"
  [[building.source]]
  kind = "contest_json"
  glob = "{w}/*/contest.json"

[[building]]
id = "jobs"
type = "jobs"
  [[building.source]]
  kind = "log_glob"
  glob = "{w}/logs/backup-*.log"

[[building]]
id = "kiosk"
type = "kiosk"
plot = "gate"
  [[building.source]]
  kind = "my-kind"
  glob = "{w}/kiosk/*.json"
"""
    work = tmp_path / "work"
    (work / "sprint-a").mkdir(parents=True)
    (work / "sprint-a" / "contest.json").write_text("{}", encoding="utf-8")
    (work / "paper-kite").mkdir()
    cfg = parse_config(text, tmp_path / "lamptown.toml")
    pm = L.plot_map(cfg)
    f = lambda *parts: {"x-folder": str(work.joinpath(*parts))}  # noqa: E731
    assert pm(f("paper-kite")) == "boathouse"  # a sibling project stays in the harbour
    assert pm(f("sprint-a")) == "lab" and pm(f("sprint-a", "notes")) == "lab"
    assert pm(f("sprint-b")) == "boathouse"
    # a wildcard in the file name only: its fixed folder counts, files or not
    assert pm(f("kiosk", "x")) == "gate"
    assert pm(f("logs")) == "boathouse"  # a log_glob names no work place (as before)
    (work / "sprint-b").mkdir()
    (work / "sprint-b" / "contest.json").write_text("{}", encoding="utf-8")
    pm.refresh = 0  # the glob is looked at again (at most once a minute by default)
    assert pm(f("sprint-b")) == "lab"


def test_a_held_live_file_warns_and_never_fails_the_build(tmp_path, monkeypatch):
    cfg, _ = config(tmp_path)
    real = B.write_atomic

    def held(out: Path, text: str) -> None:
        if out.name in (B.LIVE_JS, B.HISTORY_JS):  # a browser has it open for over a second
            raise PermissionError(13, "The process cannot access the file", str(out))
        real(out, text)

    monkeypatch.setattr(B, "write_atomic", held)
    out = tmp_path / "out"
    res = B.build(cfg, out_dir=out, now=NOW, tz=UTC)
    assert res.town.is_file() and (out / "town-data.js").is_file()
    assert res.live is None and res.history is None
    assert any(B.LIVE_JS in w for w in res.warnings), res.warnings
    tick = B.write_live_files(cfg, out, tz=UTC, history=True)
    assert tick.history is None and any(B.LIVE_JS in w for w in tick.warnings)
    assert tick.payload["spec"] == "lamptown-live/1"
