"""The live town's event log: daily JSONL files by local date, read back, pruned."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo

import pytest

from lamptown.status import events as ev

T0 = datetime(2026, 11, 4, 9, 7, 30, tzinfo=UTC)


class SpringForward(tzinfo):
    """A made-up zone: UTC+1, and UTC+2 from 2026-03-29 01:00 UTC (local 02:00 -> 03:00)."""

    SWITCH = datetime(2026, 3, 29, 1, 0, tzinfo=UTC)

    def utcoffset(self, dt):
        if dt is None:
            return timedelta(hours=1)
        naive = dt.replace(tzinfo=None)
        return timedelta(hours=2) if naive >= datetime(2026, 3, 29, 3, 0) else timedelta(hours=1)

    def dst(self, dt):
        return self.utcoffset(dt) - timedelta(hours=1)

    def tzname(self, dt):
        return "XST"

    def fromutc(self, dt):
        utc = dt.replace(tzinfo=UTC)
        offset = timedelta(hours=2) if utc >= self.SWITCH else timedelta(hours=1)
        return (utc + offset).replace(tzinfo=self)


def _event(t, kind="start", sid="codex-3f9a", **extra):
    return {"t": t, "id": sid, "k": kind, "title": "Fix save slots", "project": "paper-kite",
            "tool": "codex", "plot": "boathouse", **extra}


def test_append_writes_one_line_per_event(tmp_path):
    n = ev.append(tmp_path, [_event(T0), _event(T0 + timedelta(minutes=1), "files", n=12)],
                  tz=UTC)
    assert n == 2
    path = tmp_path / "events" / "2026-11-04.jsonl"
    lines = path.read_bytes().split(b"\n")
    assert lines[-1] == b"" and len(lines) == 3
    first = json.loads(lines[0])
    assert first == {"t": "2026-11-04T09:07:30Z", "id": "codex-3f9a", "k": "start",
                     "title": "Fix save slots", "project": "paper-kite", "tool": "codex",
                     "plot": "boathouse"}
    assert json.loads(lines[1])["n"] == 12
    ev.append(tmp_path, [_event(T0 + timedelta(minutes=2), "done")], tz=UTC)
    assert [e["k"] for e in ev.read_day(path)] == ["start", "files", "done"]


def test_bad_events_are_skipped_and_unknown_keys_dropped(tmp_path):
    good = _event(T0, "ci", ok=False, message="secret reply text", path="/work/paper-kite/a.py")
    bad = [_event(T0, "explode"), _event("yesterday"), _event(T0, sid="../escape"),
           {"k": "start"}, "not a dict"]
    assert ev.append(tmp_path, [good, *bad], tz=UTC) == 1
    (only,) = ev.read_day(tmp_path / "events" / "2026-11-04.jsonl")
    assert only["ok"] is False
    assert "message" not in only and "path" not in only
    assert set(only) == {"t", "id", "k", "ok", "title", "project", "tool", "plot"}


def test_names_only_paths_are_cut_to_their_last_part(tmp_path):
    raw = _event(T0, title="Edit /work/paper-kite/src/save.py now",
                 project="C:\\Users\\you\\paper-kite", tool="codex")
    out = ev.clean_event(raw)
    assert out["title"] == "Edit save.py now"
    assert out["project"] == "paper-kite"
    assert ev.clean_event(_event(T0, title="Fix save/load"))["title"] == "Fix save/load"
    assert ev.clean_event(_event(T0, project="/work/moss-engine/"))["project"] == "moss-engine"


def test_lines_stay_under_one_kilobyte(tmp_path):
    raw = _event(T0, title="鳥" * 500, project="森" * 500, tool="t" * 500, plot="x" * 100)
    line = ev.encode(ev.clean_event(raw))
    assert len(line) <= ev.MAX_LINE
    assert json.loads(line)["k"] == "start"


def test_local_date_picks_the_file_across_midnight(tmp_path):
    plus3 = timezone(timedelta(hours=3))
    late = datetime(2026, 11, 3, 20, 59, tzinfo=UTC)  # 23:59 local, 3 Nov
    early = datetime(2026, 11, 3, 21, 1, tzinfo=UTC)  # 00:01 local, 4 Nov
    ev.append(tmp_path, [_event(late, "start"), _event(early, "done")], tz=plus3)
    names = sorted(p.name for p in (tmp_path / "events").iterdir())
    assert names == ["2026-11-03.jsonl", "2026-11-04.jsonl"]
    days = ev.read_days(tmp_path, 2, now=early + timedelta(hours=1), tz=plus3)
    assert [d for d, _ in days] == [date(2026, 11, 3), date(2026, 11, 4)]
    assert [[e["k"] for e in es] for _, es in days] == [["start"], ["done"]]


def test_dst_change_keeps_one_local_day_together(tmp_path):
    zone = SpringForward()
    before = datetime(2026, 3, 29, 0, 30, tzinfo=UTC)  # 01:30 local (+1)
    after = datetime(2026, 3, 29, 1, 30, tzinfo=UTC)  # 03:30 local (+2)
    evening = datetime(2026, 3, 29, 21, 59, tzinfo=UTC)  # 23:59 local (+2)
    next_day = datetime(2026, 3, 29, 22, 1, tzinfo=UTC)  # 00:01 local, 30 Mar
    prev_day = datetime(2026, 3, 28, 22, 59, tzinfo=UTC)  # 23:59 local (+1), 28 Mar
    ev.append(tmp_path, [_event(x, k) for x, k in ((before, "start"), (after, "wait"),
              (evening, "done"), (next_day, "start"), (prev_day, "done"))], tz=zone)
    days = dict(ev.read_days(tmp_path, 3, now=next_day, tz=zone))
    assert [e["k"] for e in days[date(2026, 3, 29)]] == ["start", "wait", "done"]
    assert [e["k"] for e in days[date(2026, 3, 30)]] == ["start"]
    assert [e["k"] for e in days[date(2026, 3, 28)]] == ["done"]


def test_dst_with_a_real_zone_when_the_system_has_one(tmp_path):
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        zone = zoneinfo.ZoneInfo("America/New_York")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no time zone database here (tzdata not installed)")
    # 2026-11-01: clocks go back at 02:00 EDT -> 01:00 EST; 01:30 happens twice
    first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 01:30 EDT
    second = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # 01:30 EST
    ev.append(tmp_path, [_event(first, "start"), _event(second, "done")], tz=zone)
    (day, events), = ev.read_days(tmp_path, 1, now=second, tz=zone)
    assert day == date(2026, 11, 1)
    assert [e["k"] for e in events] == ["start", "done"]


def test_read_days_returns_empty_days_and_skips_torn_lines(tmp_path):
    ev.append(tmp_path, [_event(T0)], tz=UTC)
    path = tmp_path / "events" / "2026-11-04.jsonl"
    with open(path, "ab") as fh:
        fh.write(b'{"t":"2026-11-04T09:08:00Z","id":"codex-3f9a","k":"do')  # writer mid-line
    days = ev.read_days(tmp_path, 7, now=T0, tz=UTC)
    assert len(days) == 7 and days[-1][0] == date(2026, 11, 4)
    assert [len(e) for _, e in days] == [0, 0, 0, 0, 0, 0, 1]


def test_read_days_sorts_by_time(tmp_path):
    ev.append(tmp_path, [_event(T0 + timedelta(minutes=5), "done")], tz=UTC)
    ev.append(tmp_path, [_event(T0, "start")], tz=UTC)  # a reader catching up, later
    (_, events), = ev.read_days(tmp_path, 1, now=T0, tz=UTC)
    assert [e["k"] for e in events] == ["start", "done"]


def test_prune_keeps_the_last_days_only(tmp_path):
    for back in range(10):
        ev.append(tmp_path, [_event(T0 - timedelta(days=back))], tz=UTC)
    folder = tmp_path / "events"
    (folder / "notes.txt").write_text("not ours", encoding="utf-8")
    (folder / "2026-13-40.jsonl").write_text("", encoding="utf-8")  # not a real date
    removed = ev.prune(tmp_path, keep=7, now=T0, tz=UTC)
    assert sorted(p.name for p in removed) == [
        "2026-10-26.jsonl", "2026-10-27.jsonl", "2026-10-28.jsonl"]
    left = sorted(p.name for p in folder.iterdir())
    assert left[0] == "2026-10-29.jsonl" and "notes.txt" in left and "2026-13-40.jsonl" in left
    assert ev.prune(tmp_path / "nowhere") == []


def test_prune_across_a_dst_change(tmp_path):
    zone = SpringForward()
    now = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)
    for back in range(9):
        ev.append(tmp_path, [_event(now - timedelta(days=back))], tz=zone)
    ev.prune(tmp_path, keep=7, now=now, tz=zone)
    days = sorted(p.name for p in (tmp_path / "events").iterdir())
    assert days[0] == "2026-03-27.jsonl" and days[-1] == "2026-04-02.jsonl" and len(days) == 7


def test_naive_times_are_local(tmp_path):
    out = ev.clean_event(_event(datetime(2026, 11, 4, 9, 0)))
    assert out["t"].endswith("Z")


# ---------------------------------------------------------------- review fixes (L1)
@pytest.mark.parametrize(("text", "expected"), [
    ("Edit (/home/you/secret-app/main.py)", "Edit (main.py)"),  # in brackets
    ("see `/home/you/secret-app`", "see `secret-app`"),  # in backticks
    (r'open "C:\Users\you\secret-app\x.py"', 'open "x.py"'),  # in quotes
    ("path=/home/you/secret-app", "path=secret-app"),  # after "="
    ("file:///home/you/secret-app/x.py", "x.py"),  # a file URL
    ("https://example.com/you/secret-app", "secret-app"),  # any URL
    (r"\\fileserver\share\you\secret-app", "secret-app"),  # UNC
    ("//fileserver/share/you/secret-app", "secret-app"),  # UNC, forward slashes
    (r"C:\Users\you there\secret-app", "secret-app"),  # a user name with a space
    (r"C:\Users\you there\secret app\x.py, then save/load", "x.py, then save/load"),
    ("/Users/you there/secret-app/x.py", "x.py"),
    ("C:/Users/you there/secret-app", "secret-app"),
    ("Fix save/load and a / b", "Fix save/load and a / b"),  # relative and lone slashes stay
    ("cd ~/secret-app/src", "cd src"),
])
def test_name_only_cuts_paths_anywhere_in_the_text(text, expected):
    assert ev.name_only(text, 200) == expected


@pytest.mark.parametrize("text", ["/" * 4000, "\\" * 4000, " /a" * 1300, "C:\\" + "a " * 2000])
def test_name_only_stays_fast_on_hostile_titles(text):
    import time

    start = time.perf_counter()
    ev.name_only(text, 200)
    assert time.perf_counter() - start < 0.05  # was 0.1 s for 4000 slashes (quadratic)


def test_lone_surrogates_are_replaced_not_fatal(tmp_path):
    title = json.loads(r'"Fix kite \ud83d"')  # a JS slice that cut an emoji in half
    assert ev.name_only(title, 50) == "Fix kite ?"
    assert ev.project_name(json.loads(r'"/work/kite\udc00"')) == "kite?"
    assert ev.append(tmp_path, [_event(T0, title=title), _event(T0, "done")], tz=UTC) == 2
    assert [e["title"] for e in ev.read_day(tmp_path / "events" / "2026-11-04.jsonl")] == [
        "Fix kite ?", "Fix save slots"]


def test_a_torn_last_line_does_not_swallow_the_next_append(tmp_path):
    ev.append(tmp_path, [_event(T0, "start")], tz=UTC)
    path = tmp_path / "events" / "2026-11-04.jsonl"
    with open(path, "ab") as fh:
        fh.write(b'{"t":"2026-11-04T09:08:00Z","id":"codex-3f9a","k":"wa')  # crash mid-write
    ev.append(tmp_path, [_event(T0 + timedelta(minutes=2), "done")], tz=UTC)
    assert [e["k"] for e in ev.read_day(path)] == ["start", "done"]


def test_append_drops_events_older_than_the_kept_days(tmp_path):
    old = _event(T0 - timedelta(days=20), "commit")
    edge = _event(T0 - timedelta(days=6), "commit")  # the oldest day still kept
    n = ev.append(tmp_path, [old, edge, _event(T0)], tz=UTC, keep_days=7, now=T0)
    assert n == 2
    assert sorted(p.name for p in (tmp_path / "events").iterdir()) == [
        "2026-10-29.jsonl", "2026-11-04.jsonl"]


def test_append_can_skip_events_already_in_the_log(tmp_path):
    first = [_event(T0, "start"), _event(T0 + timedelta(seconds=5), "files", n=3)]
    ev.append(tmp_path, first, tz=UTC)
    again = [*first, _event(T0 + timedelta(seconds=5), "files", n=4), _event(T0, "done")]
    seen: dict = {}
    assert ev.append(tmp_path, again, tz=UTC, seen=seen) == 2
    assert ev.append(tmp_path, again, tz=UTC, seen=seen) == 0  # the cache knows what it wrote
    got = [(e["k"], e.get("n")) for e in ev.read_day(tmp_path / "events" / "2026-11-04.jsonl")]
    assert got == [("start", None), ("done", None), ("files", 3), ("files", 4)]
