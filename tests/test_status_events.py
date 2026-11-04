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
    assert [e["k"] for e in n] == ["start", "files"]  # what was written, as written
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
    assert len(ev.append(tmp_path, [good, *bad], tz=UTC)) == 1
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
    assert len(ev.append(tmp_path, [_event(T0, title=title), _event(T0, "done")],
                         tz=UTC)) == 2
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
    assert [e["t"][:10] for e in n] == ["2026-10-29", "2026-11-04"]  # not the dropped one
    assert sorted(p.name for p in (tmp_path / "events").iterdir()) == [
        "2026-10-29.jsonl", "2026-11-04.jsonl"]


def test_append_can_skip_events_already_in_the_log(tmp_path):
    first = [_event(T0, "start"), _event(T0 + timedelta(seconds=5), "files", n=3)]
    ev.append(tmp_path, first, tz=UTC)
    again = [*first, _event(T0 + timedelta(seconds=5), "files", n=4), _event(T0, "done")]
    seen: dict = {}
    wrote = ev.append(tmp_path, again, tz=UTC, seen=seen)
    assert [(e["k"], e.get("n")) for e in wrote] == [("files", 4), ("done", None)]  # only new
    assert ev.append(tmp_path, again, tz=UTC, seen=seen) == []  # the cache knows what it wrote
    got = [(e["k"], e.get("n")) for e in ev.read_day(tmp_path / "events" / "2026-11-04.jsonl")]
    assert got == [("start", None), ("done", None), ("files", 3), ("files", 4)]


# ---------------------------------------------------------------- re-check fixes (W1)
@pytest.mark.parametrize(("text", "expected"), [
    # W2: home folders behind a mount prefix (WSL, Git Bash, Cygwin)
    ("/mnt/c/Users/Sam Doe/secret-app/x.py", "x.py"),
    ("Edit /c/Users/Sam Doe/secret-app", "Edit secret-app"),
    ("/cygdrive/d/Users/Sam Doe/secret-app/x.py", "x.py"),
    ("see /mnt/d/home/sam/secret-app/x.py", "see x.py"),
    # W8: forward-slash drive paths with spaces, macOS volumes, odd leads, ~user
    ("d:/My Projects/secret app/x.py", "x.py"),
    ('open "d:/My Projects/secret app/x.py" now', 'open "x.py" now'),
    ("/Volumes/Work Drive/secret-client/x.md", "x.md"),
    ("-/home/sam/secret-app", "-secret-app"),
    (".../home/sam/secret-app/x.py", "...x.py"),
    ("x~/home/sam/secret-app", "x~secret-app"),
    ("~sam/secret-app/x.py", "x.py"),
    ("~2/3 of tests pass", "~2/3 of tests pass"),  # "about", not a home folder
    # slash commands and escapes are no paths; a real path next to one still goes
    ("Run /code-review on L1", "Run /code-review on L1"),
    ("/story-done L2", "/story-done L2"),
    ("Add /help command", "Add /help command"),
    (r"split on \n and \t", r"split on \n and \t"),
    ("Run /code-review, then edit /work/paper-kite/a.py", "Run /code-review, then edit a.py"),
    # relative paths that hold a "home" or "users" part stay
    ("Edit app/home/page.tsx, src/users/a.ts", "Edit app/home/page.tsx, src/users/a.ts"),
    ("see /home/you", "see ~"),  # W1-fix2: a home folder's name is never shown, even last
])
def test_w2_w8_name_only_cuts_mounted_homes_and_keeps_commands(text, expected):
    assert ev.name_only(text, 200) == expected


@pytest.mark.parametrize("text", [
    "/mnt/c" * 700, "/c/users/" + "a " * 2000, "D:/" + "a " * 2000, "a:/ " * 1024,
    "-/home/ " * 500, "/volumes/x" * 400, "~a/" * 1300, "/a " * 1300, "x/home/ a\\b c:/d " * 240,
])
def test_w2_w8_name_only_stays_fast_on_new_hostile_titles(text):
    import time

    start = time.perf_counter()
    ev.name_only(text, 200)
    assert time.perf_counter() - start < 0.05


# ---------------------------------------------------------------- review fixes (W1-fix2)
@pytest.mark.parametrize(("text", "expected"), [
    # 1. a user name with a space, then forward slashes (Python's f"{Path.home()}/x")
    (r"C:\Users\you there/secret-app/x.py", "x.py"),
    (r"Edit C:\Users\you there/secret-app", "Edit secret-app"),
    (r"C:/Users\you there\secret-app\x.py", "x.py"),
    (r"\\fileserver\home\you there/secret-app/x.py", "x.py"),
    ("//wsl.localhost/Ubuntu/home/you there/secret-app/x.py", "x.py"),
    # 1. ... in file:// and editor URLs
    ("file:///C:/Users/you there/secret-app/x.py", "x.py"),
    ("vscode://file/C:/Users/you there/secret-app/x.py", "x.py"),
    ("Open vscode://file/c:/Users/you there/secret-app now", "Open secret-app now"),
    # 1. ... and as the last part of the text
    (r"see C:\Users\you there", "see ~"),
    ("see /mnt/c/Users/Sam Doe", "see ~"),
    ('open "/Users/you there" now', 'open "~" now'),
    # 5. other home roots, and volume labels with spaces
    ("/var/home/Sam Doe/secret-app/x.py", "x.py"),
    ("/export/home/Sam Doe/secret-app/x.py", "x.py"),
    ("/System/Volumes/Data/Users/Sam Doe/secret-app/x.py", "x.py"),
    ("/Volumes/Data/Users/Sam Doe/secret-app/x.py", "x.py"),
    ("/run/media/sam/Work Drive/secret-client/x.md", "x.md"),
    ("/media/sam/Work Drive/secret-client/x.md", "x.md"),
    ("see /media/sam/Work Drive", "see ~"),
    # 2. (W2-left-fix2: a title after a drive path keeps no words before a later "/") ...
    ("Fix c:/temp handling and the save/load test", "Fix load test"),  # W2-left-fix2: over-cut
    ("Use D:/ for builds, then tidy src/a.ts and lib/b.ts", "Use b.ts"),  # W2-left-fix2: over-cut
    # 2. ... but still cuts real folder names with spaces, sync folders too
    ("d:/Program Files (x86)/Secret Tool/x.exe", "x.exe"),
    ("d:/Microsoft Visual Studio 2022/secret app/x.sln", "x.sln"),
    ("c:/Users/you/OneDrive - Acme Holdings Group Inc/secret plan/x.docx", "x.docx"),
    ("c:/Work/Acme Corp - Shared Documents/secret/x.md, then load", "x.md, then load"),
    # slash commands and ordinary titles stay
    ("Route /home page and /users list", "Route /home page and /users list"),
    ("Wire /gsd:plan and /code-review", "Wire /gsd:plan and /code-review"),
    ("Edit app/home/page.tsx and src/media/a.ts", "Edit app/home/page.tsx and src/media/a.ts"),
    ("Fix km/h, I/O and TCP/IP", "Fix km/h, I/O and TCP/IP"),
])
def test_w1fix2_home_names_never_leak_and_titles_keep_their_words(text, expected):
    assert ev.name_only(text, 200) == expected


@pytest.mark.parametrize("text", [
    "C:\\Users\\" + "a b/" * 1000, "\\\\" + "a " * 2000, "\\\\a\\" * 1000, "//a/" * 1000,
    "/var/home/ " * 400, "/run/media/" + "a b/" * 1000, "/media/a/" * 500,
    "D:/" + "a - " * 1000, "D:/" + "a b c d/" * 500, "d:/a - b c d e - " * 250,
    "file:///" + "users/" * 600, "/system/volumes/data" * 200, "x/users/ a\\b c:/d - e " * 180,
    "/users/" + "a" * 4000, "\\\\a\\users\\" + "b - " * 1000,
])
def test_w1fix2_name_only_stays_fast_on_hostile_titles(text):
    import time

    best = 1.0
    for _ in range(3):  # the best of three: a busy CI runner may pause any one run
        start = time.perf_counter()
        ev.name_only(text, 200)
        best = min(best, time.perf_counter() - start)
    # about 2 ms here; a pattern that backtracks badly takes 50 ms or more on 4 KB, so a slow
    # CI runner gets room without hiding one
    assert best < 0.02


# --- W2-left: folder names of five words or more ---
@pytest.mark.parametrize(("text", "expected"), [
    # a drive path whose folder has 5+ words is cut whole when a path goes on after it ...
    ("Edit d:/one two three four five/kite/main.py", "Edit main.py"),
    ("Edit d:/Client Acme Secret Project Alpha/main.py", "Edit main.py"),
    ("Edit d:/a b c d e f g h/x.py now", "Edit x.py now"),
    ("cd d:/Client Acme Secret Project Alpha/kite", "cd kite"),
    ("Open C:/Users/you/My Five Word Folder Name/x.py", "Open x.py"),
    ("d:/Work/Acme - Client Big Secret Plan Two/x.md, then load", "x.md, then load"),
    # ... so is a home or ~ path whose folders hold spaces
    ("Edit /Users/you/My Project/x.py", "Edit x.py"),
    ("Edit /home/you/one two three four five/kite/x.py", "Edit x.py"),
    ("Edit ~/My Project/x.py", "Edit x.py"),
    ("Edit ~/Client Acme Secret Project Alpha/x.py", "Edit x.py"),
    # ... while an ordinary title after a drive or root path keeps its words
    ("Fix c:/temp handling and the save/load test", "Fix load test"),  # W2-left-fix2: over-cut
    ("Add /api/users endpoint and save/load tests", "Add users endpoint and save/load tests"),
    ("Route /home page and /users list", "Route /home page and /users list"),
    ("Fix km/h, I/O and TCP/IP", "Fix km/h, I/O and TCP/IP"),
])
def test_w2left_long_folder_names_never_stay_in_titles(text, expected):
    assert ev.name_only(text, 200) == expected


@pytest.mark.parametrize("text", [
    "D:/" + "a b c d e " * 800, "D:/" + "a b c d e/" * 400, "d:/" + "a - b c d e f " * 400,
    "~/" + "a b c d e f/" * 300, "~/" + "a " * 2000, "/Users/you/" + "a b c d e " * 700,
    "/home/you/~/" + "a b/" * 1000,
])
def test_w2left_long_folder_names_stay_fast_on_hostile_titles(text):
    import time

    best = 1.0
    for _ in range(3):
        start = time.perf_counter()
        ev.name_only(text, 200)
        best = min(best, time.perf_counter() - start)
    assert best < 0.02
# --- end W2-left ---


# --- W2-left-fix: review findings 1, 2 and 6 ---
BS = "\\"  # a backslash (spelled out, so no test line reads as a real drive path)


@pytest.mark.parametrize(("text", "expected"), [
    # 1. a 5+ word folder after a drive, a home or ~ is cut whenever a separator follows it
    ("Edit d:/Client Acme Secret Project Alpha/notes on it.txt", "Edit notes on it.txt"),
    ("edit d:/Client Acme Secret Project Alpha/Makefile then test", "edit Makefile then test"),
    ("Edit d:/Client Acme Secret Project Alpha/README and more", "Edit README and more"),
    # (a path that ends in a separator shows its last part, as "cd d:/kite/ now" does)
    ("Open d:/Client Acme Secret Project Alpha/ now", "Open Client Acme Secret Project Alpha now"),
    ("home /Users/you/Client Acme Secret Project Alpha/notes on it.txt", "home notes on it.txt"),
    ("tilde ~/Client Acme Secret Project Alpha/notes on it.txt", "tilde notes on it.txt"),
    ("Edit d:/Big Client - Acme Secret Project Alpha Two/notes on it.txt", "Edit notes on it.txt"),
    # 2. the other spellings of a drive root keep no folder name with spaces either
    ("file:///d:/My Project/src/x.py", "x.py"),
    ("url file:///d:/Client Acme Secret Project Alpha/src/x.py", "url x.py"),
    ("Open vscode://file/d:/My Project/src/x.py now", "Open x.py now"),
    ("//server/share/My Project/src/x.py", "x.py"),
    ("unc //server/share/Client Acme Secret Project Alpha/src/x.py", "unc x.py"),
    ("/mnt/d/My Project/src/x.py", "x.py"),
    ("wsl /mnt/d/Client Acme Secret Project Alpha/src/x.py", "wsl x.py"),
    ("/cygdrive/d/My Project/src/x.py", "x.py"),
    ("/d/My Project/src/x.py", "x.py"),
    ("gitbash /d/Client Acme Secret Project Alpha/src/x.py", "gitbash x.py"),
    (f"d:{BS}My Project/src/x.py", "x.py"),
    (f"mixed d:{BS}Client Acme Secret Project Alpha/src/x.py", "mixed x.py"),
    (f"Edit d:{BS}Work{BS}My Project/src/x.py now", "Edit x.py now"),
    # ... without cutting ordinary titles
    ("Add /api/users endpoint and save/load tests", "Add users endpoint and save/load tests"),
    ("Fix /home page layout for the mobile and desktop/tablet views",
     "Fix /home page layout for the mobile and desktop/tablet views"),
    (r"C:\Users\you there\secret app\x.py, then save/load", "x.py, then save/load"),
    # 6. (W2-left-fix2: words after a drive or a home stay only up to no later "/")
    ("Fix c:/temp handling and the save/load", "Fix load"),  # W2-left-fix2: over-cut
    ("Fix c:/temp handling and the save/load.", "Fix load."),  # W2-left-fix2: over-cut
    ("Fix c:/temp handling and the save/load test", "Fix load test"),  # W2-left-fix2: over-cut
    ("Fix c:/temp handling and the I/O code", "Fix O code"),  # W2-left-fix2: over-cut
    ("Read /Users/you then fix km/h and I/O", "Read O"),  # W2-left-fix2: over-cut
    ("Fix ~/ path handling in the save/load code", "Fix load code"),  # W2-left-fix2: over-cut
    # ... but a home name is never kept, and words that look like a folder go
    ("Read /Users/You There then fix km/h and I/O", "Read O"),
    ("Edit d:/Client Acme Secret Project Alpha/x.py", "Edit x.py"),
    ("Edit d:/client acme secret project alpha/notes on it.txt", "Edit notes on it.txt"),
])
def test_w2leftfix_drive_folders_go_and_ordinary_words_stay(text, expected):
    assert ev.name_only(text, 200) == expected


@pytest.mark.parametrize("text", [
    "c:/" * 1400, "c:/a " * 800, "c:/a b c d e " * 300, "c:/a b c d e/x " * 250,
    "d:/" + "a b c d e/" * 400 + " x", "d:/" + "a - " * 1300, "d:/" + "a b c d - " * 400,
    "d:/" + "a - b - c - d - e/ " * 200, "~/" * 2000, "~/" + "a b c d e/x " * 300,
    "/home/you/" + "a b c d e/ " * 360, "/Users/you/" * 400, "d:/" + "a." * 2000,
    "d:/" + "aaaaa " * 600 + "/", "d:/" + "a b c d e" + "/" * 4000,
    # the title guard's own worst cases: many small words, function words, pairs
    "d:/" + "a " * 2000, "d:/" + "and the " * 500 + "x/", "d:/x " + "and " * 1000 + "km/h.",
    "d:/" + "the a/b " * 500, "/Users/you " + "then " * 800 + "km/h", "/Users/" + "a " * 2000,
    "file:///" + "d:/" * 1300, "file:///d:/" + "a b c d e/" * 400, "//a/b/" * 600,
    "//s/" + "a b c d e/" * 400, "/mnt/d/" * 600, "/mnt/d/" + "a b c d e/" * 400, "/d/" * 1300,
    "/d/" + "a b c d e/" * 400, "d:\\" + "a b c d e/" * 400, "d:\\" + "a " * 2000 + "/",
    f"d:{BS}x{BS}" + "a, " * 1300 + "/", "\\\\" + "a\\" * 1000 + "b c/" * 500,
])
def test_w2leftfix_name_only_stays_fast_on_hostile_titles(text):
    import time

    best = 1.0
    for _ in range(3):
        start = time.perf_counter()
        ev.name_only(text, 200)
        best = min(best, time.perf_counter() - start)
    assert best < 0.02
# --- end W2-left-fix ---


# --- W2-left-fix2: one structural rule (a path start, then its run's last separator) ---
@pytest.mark.parametrize(("text", "expected"), [
    # finding 1: a doubled separator ends no folder name
    ("Edit d:/Client Secret//x.py", "Edit x.py"),
    ("Edit d:/a//Client Secret/x.py", "Edit x.py"),
    ("Edit ~/Client Secret//x.py", "Edit x.py"),
    ("Edit /home/you/Client Secret//x.py", "Edit x.py"),
    ("Edit /mnt/d//Client Secret/x.py", "Edit x.py"),
    ("Edit file:///d:/Client Secret//x.py", "Edit x.py"),
    ("Edit d:/Client Acme Secret Project Alpha//x.py", "Edit x.py"),
    (f"Edit d:{BS}{BS}Client Secret{BS}{BS}x.py", "Edit x.py"),
    # finding 2: file and remote URLs without a drive
    ("Open file:///home/you/Client Secret/x.py", "Open x.py"),
    ("Open file:///Users/you/Client Secret/x.py", "Open x.py"),
    ("Open file:///mnt/d/Client Secret/x.py", "Open x.py"),
    ("Open file://server/share/Client Secret/x.py", "Open x.py"),
    ("Open vscode-remote://wsl+Ubuntu/home/you/Client Secret/x.py", "Open x.py"),
    ("Open vscode-remote://ssh-remote+box/home/you/Client Secret/x.py", "Open x.py"),
    ("Open https://intranet/Client Secret/plan", "Open plan"),
    # finding 3: a 5+ word lowercase folder with a function word, before a short last part
    ("Open d:/notes for the acme merger deal/todo", "Open todo"),
    ("Open d:/acme merger plans of the client/src", "Open src"),
    ("Open d:/old/notes for the acme merger deal/todo", "Open todo"),
    ("Open ~/notes for the acme merger deal/todo", "Open todo"),
    ("Open /home/you/notes for the acme merger deal/todo", "Open todo"),
    ("Open /mnt/d/notes for the acme merger deal/todo", "Open todo"),
    (f"Open d:{BS}x{BS}notes for the acme merger deal/todo", "Open todo"),
    ("Open d:/work for the acme bank ACME/HR", "Open HR"),
    # finding 4: no part of a home folder's user name, whatever follows it
    ("Read /Users/you there then fix km/h", "Read h"),
    ("Read /Users/you there then fix it", "Read ~"),
    (f"Read C:{BS}Users{BS}you there then fix it", "Read ~"),
    ("Read /home/you there/ then fix it", "Read ~ then fix it"),
    ("see /media/sam/Work Drive then go", "see ~"),
    # finding 5: other root folders, and a share name with a space
    ("Edit /srv/Client Secret/x.py", "Edit x.py"),
    ("Edit /opt/Client Secret/x.py", "Edit x.py"),
    ("Edit /tmp/Client Secret/x.py", "Edit x.py"),
    ("Edit /private/var/folders/Client Secret/x.py", "Edit x.py"),
    ("Edit /mnt/wsl/Client Secret/x.py", "Edit x.py"),
    ("Edit /root/Client Secret/x.py", "Edit x.py"),
    ("Edit /var/Client Secret/x.py", "Edit x.py"),
    ("Edit //server/Client Secret/x.py", "Edit x.py"),
    ("Edit /Volumes/Client Secret/x.py", "Edit x.py"),
    # the run stops at a quote or after a file name and ", "; a run ending in "/" keeps its folder
    ('open "/srv/Client Secret/x.py" and km/h', 'open "x.py" and km/h'),
    ("Edit `~/Client Secret/x.py` then I/O", "Edit `x.py` then I/O"),
    ("Edit /srv/Client Secret/x.py, then save/load", "Edit x.py, then save/load"),
    ("cd d:/kite/ now", "cd kite now"),
    ("cd /home/you/ now", "cd ~ now"),
    # titles with no path start stay as they are
    ("Add /api/users endpoint", "Add users endpoint"),
    ("Fix km/h, I/O and TCP/IP in save/load", "Fix km/h, I/O and TCP/IP in save/load"),
    ("Route /home page and /users list", "Route /home page and /users list"),
    ("Remove // TODO comments and a / b", "Remove // TODO comments and a / b"),
    ("Edit app/home/page.tsx and src/media/a.ts", "Edit app/home/page.tsx and src/media/a.ts"),
    ("~2/3 of tests pass", "~2/3 of tests pass"),
    # the limit, documented: a root path with no start keeps a folder name with spaces
    ("Edit /work/Client Secret/x.py", "Edit Client Secret/x.py"),
])
def test_w2leftfix2_a_path_start_cuts_its_whole_run(text, expected):
    out = ev.name_only(text, 200)
    assert out == expected
    assert "Secret" not in out or "/work/" in text
    assert "there" not in out and "merger" not in out


@pytest.mark.parametrize("text", [
    "c:/a " * 4000, "a+" * 20000 + "://", "//a" * 20000, "/home/" * 10000, "x.py, " * 10000,
    "file:///" + "a b//" * 10000, "/srv/" + "a b c " * 10000, "~/" * 30000, "\\\\" * 30000,
    "d:/" + "a" * 60000, "d:/a" + "/" * 60000 + "b", "/a/b " * 12000, 'c:/a"' * 12000,
    "/media/x/" * 7000, "a.bcdefghij, " * 5000, "vscode-remote://" * 4000,
], ids=lambda text: f"{len(text)}:{text[:12]!r}")  # a 64 KB id breaks PYTEST_CURRENT_TEST
def test_w2leftfix2_name_only_stays_fast_on_hostile_titles(text):
    import time

    assert 4096 <= len(text) <= 65536
    best = 1.0
    for _ in range(3):
        start = time.perf_counter()
        ev.name_only(text, 200)
        best = min(best, time.perf_counter() - start)
    assert best < 0.02
# --- end W2-left-fix2 ---
