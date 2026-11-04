"""The Watcher: readers to status files and events, the folder diff, the reader contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from lamptown.status import events as ev
from lamptown.status.adapters import Update, apply
from lamptown.status.collector import scan
from lamptown.status.readers import (
    Batch,
    ReaderError,
    clean_agent,
    find_reader,
    make_reader,
)
from lamptown.status.store import read_status, update_status
from lamptown.status.watch import Watcher, default_plot

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC)
SID = "codex-3f9a2b"


class FakeReader:
    """Hands out whatever the test queued, and counts its polls in its saved state."""

    name = "fake"

    def __init__(self):
        self.agents: list[dict] = []
        self.events: list[dict] = []
        self.states: list[dict] = []

    def poll(self, state, now):
        state["polls"] = state.get("polls", 0) + 1
        self.states.append(dict(state))
        batch = Batch(agents=self.agents, events=self.events)
        self.agents, self.events = [], []
        return batch


class BrokenReader:
    name = "broken"

    def poll(self, state, now):
        state["half-done"] = True
        raise RuntimeError("log moved")


def _agent(**changes):
    return {"id": SID, "title": "Fix save slots", "state": "working", "agent": "codex",
            "kind": "task", "x-folder": "/work/paper-kite", **changes}


def _logged(folder, days=1, now=T0):
    return [e for _, es in ev.read_days(folder, days, now=now, tz=UTC) for e in es]


def _kinds(result):
    return [e["k"] for e in result.events]


# ---------------------------------------------------------------- readers -> files + events
def test_fake_reader_becomes_status_files_and_events(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    reader.agents = [_agent(at=T0)]
    first = w.tick(T0)
    assert first.written == [SID]
    doc = read_status(tmp_path / f"{SID}.json")
    assert doc["state"] == "working" and doc["updated"] == "2026-11-04T09:00:00Z"
    assert doc["x-project"] == "paper-kite" and doc["x-folder"] == "/work/paper-kite"
    assert _kinds(first) == ["start", "project"]
    start = first.events[0]
    assert start == {"t": "2026-11-04T09:00:00Z", "id": SID, "k": "start",
                     "title": "Fix save slots", "project": "paper-kite", "tool": "codex",
                     "plot": "boathouse"}

    reader.agents = [_agent(state="waiting", at=T0 + timedelta(minutes=1),
                            needs_you=[{"text": "Your turn"}])]
    second = w.tick(T0 + timedelta(minutes=1, seconds=3))
    assert _kinds(second) == ["wait"]
    assert second.events[0]["t"] == "2026-11-04T09:01:00Z"

    reader.agents = [_agent(state="working", at=T0 + timedelta(minutes=2))]
    reader.agents[0]["needs_you"] = []
    assert _kinds(w.tick(T0 + timedelta(minutes=2))) == ["answer"]
    reader.agents = [_agent(state="ok", at=T0 + timedelta(minutes=3), needs_you=[])]
    assert _kinds(w.tick(T0 + timedelta(minutes=3))) == ["done"]
    assert [e["k"] for e in _logged(tmp_path)] == ["start", "project", "wait", "answer", "done"]
    assert reader.states[-1]["polls"] == 4


def test_unchanged_records_are_not_rewritten_until_the_heartbeat(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC, heartbeat=60)
    reader.agents = [_agent(at=T0)]
    w.tick(T0)
    reader.agents = [_agent(at=T0 + timedelta(seconds=20))]
    quiet = w.tick(T0 + timedelta(seconds=20))
    assert quiet.written == [] and quiet.events == [] and not quiet.changed
    reader.agents = [_agent(at=T0 + timedelta(seconds=90))]
    beat = w.tick(T0 + timedelta(seconds=90))
    assert beat.written == [SID] and beat.events == []
    assert read_status(tmp_path / f"{SID}.json")["updated"] == "2026-11-04T09:01:30Z"


def test_a_newer_hook_write_is_never_overwritten(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    update_status(SID, tmp_path, now=T0 + timedelta(minutes=10), title="Fix save slots",
                  state="ok", agent="codex")
    reader.agents = [_agent(state="working", at=T0 + timedelta(minutes=5))]
    result = w.tick(T0 + timedelta(minutes=11))
    assert result.kept == [SID]
    doc = read_status(tmp_path / f"{SID}.json")
    assert doc["state"] == "ok" and doc["updated"] == "2026-11-04T09:10:00Z"
    # only what hooks never write merges in (x-folder fills in: the hook had none)
    assert doc["x-project"] == "paper-kite" and doc["x-folder"] == "/work/paper-kite"
    reader.agents = [_agent(state="working", at=T0 + timedelta(minutes=6))]
    again = w.tick(T0 + timedelta(minutes=12))
    assert again.kept == [SID] and again.written == []  # nothing left to merge


def test_reader_times_in_the_future_are_clamped_to_now(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    reader.agents = [_agent(at=T0 + timedelta(hours=3))]
    reader.events = [{"t": T0 + timedelta(hours=3), "id": SID, "k": "commit"}]
    result = w.tick(T0)
    assert read_status(tmp_path / f"{SID}.json")["updated"] == "2026-11-04T09:00:00Z"
    assert all(e["t"] <= "2026-11-04T09:00:00Z" for e in result.events)


def test_reader_events_fill_names_and_win_over_the_folder_diff(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    reader.agents = [_agent(at=T0)]
    w.tick(T0)
    reader.agents = [_agent(at=T0 + timedelta(minutes=5), **{"x-commits": 1})]
    reader.events = [{"t": T0 + timedelta(minutes=4, seconds=12), "id": SID, "k": "commit",
                      "n": 1}]
    result = w.tick(T0 + timedelta(minutes=5))
    (commit,) = result.events
    assert commit["t"] == "2026-11-04T09:04:12Z"  # the reader's own time, counted once
    assert (commit["title"], commit["project"], commit["tool"]) == (
        "Fix save slots", "paper-kite", "codex")


def test_history_from_a_reader_keeps_its_times(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    yesterday = T0 - timedelta(days=1)
    reader.events = [{"t": yesterday, "id": SID, "k": "start", "title": "Old run",
                      "project": "/work/tidebook", "tool": "codex"},
                     {"t": yesterday + timedelta(minutes=9), "id": SID, "k": "done"}]
    w.tick(T0)
    (old_day, old), (_, today) = ev.read_days(tmp_path, 2, now=T0, tz=UTC)
    assert [e["k"] for e in old] == ["start", "done"] and today == []
    assert old[0]["project"] == "tidebook"


def test_files_are_grouped_to_one_event_per_agent_per_minute(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    reader.agents = [_agent(at=T0)]
    w.tick(T0)
    reader.agents = [_agent(at=T0 + timedelta(seconds=10), **{"x-files": 3})]
    assert w.tick(T0 + timedelta(seconds=10)).events == []  # the minute isn't over yet
    reader.agents = [_agent(at=T0 + timedelta(seconds=40), **{"x-files": 7})]
    reader.events = [{"t": T0 + timedelta(seconds=45), "id": SID, "k": "files", "n": 2,
                      "id2": "ignored"}]
    assert w.tick(T0 + timedelta(seconds=45)).events == []
    w.close()
    w2 = Watcher(tmp_path, [reader], tz=UTC)  # a restart keeps the open group
    flushed = w2.tick(T0 + timedelta(minutes=1, seconds=2))
    (files,) = flushed.events
    assert files["k"] == "files" and files["t"] == "2026-11-04T09:00:10Z"
    assert files["n"] == 3 + 2  # tick 2: +3 from the folder; tick 3: the reader's 2 (wins)


def test_a_broken_reader_is_reported_and_rolled_back(tmp_path):
    good = FakeReader()
    w = Watcher(tmp_path, [BrokenReader(), good], tz=UTC)
    good.agents = [_agent(at=T0)]
    result = w.tick(T0)
    assert result.errors == ["reader broken: RuntimeError: log moved"]
    assert result.written == [SID]
    assert w.state()["readers"]["broken"] == {}


def test_a_reader_returning_the_wrong_thing_is_an_error(tmp_path):
    class Wrong:
        name = "wrong"

        def poll(self, state, now):
            state["cursor"] = object()  # not JSON
            return Batch()

    result = Watcher(tmp_path, [Wrong()], tz=UTC).tick(T0)
    assert result.errors and result.errors[0].startswith("reader wrong: TypeError")


# ---------------------------------------------------------------- folder diff
def test_folder_diff_catches_hook_written_changes(tmp_path):
    w = Watcher(tmp_path, [], tz=UTC)
    assert w.tick(T0).events == []  # empty folder: baseline
    hook = Update(id="claude-code-77aa", agent="claude-code", display="paper-kite",
                  state="working", message="Working", session_start=True,
                  folder_path="/work/paper-kite")
    apply(hook, tmp_path)
    found = w.tick(datetime.now(UTC))
    assert _kinds(found) == ["start", "project"]
    assert found.events[0]["title"] == "paper-kite" and found.events[0]["tool"] == "claude-code"
    apply(Update(id="claude-code-77aa", agent="claude-code", state="waiting",
                 needs_you=[{"text": "Your turn"}]), tmp_path)
    assert _kinds(w.tick(datetime.now(UTC))) == ["wait"]
    apply(Update(id="claude-code-77aa", agent="claude-code", state="ok", needs_you=[]),
          tmp_path)
    assert _kinds(w.tick(datetime.now(UTC))) == ["done"]


def test_first_run_does_not_replay_what_is_already_there(tmp_path):
    update_status("tidebook-sync", tmp_path, now=T0, title="Tidebook sync", state="error")
    reader = FakeReader()
    reader.agents = [_agent(at=T0)]
    result = Watcher(tmp_path, [reader], tz=UTC).tick(T0 + timedelta(seconds=5))
    assert {e["id"] for e in result.events} == {SID}  # only the reader's new agent


def test_silence_turns_into_fail_then_quiet(tmp_path):
    w = Watcher(tmp_path, [], tz=UTC, stale_after=1800, quiet_after=6 * 3600)
    w.tick(T0 - timedelta(seconds=1))
    update_status("moss-build", tmp_path, now=T0, title="Moss build", state="working")
    assert _kinds(w.tick(T0)) == ["start"]
    assert w.tick(T0 + timedelta(minutes=20)).events == []
    stopped = w.tick(T0 + timedelta(minutes=31))
    assert _kinds(stopped) == ["fail"] and stopped.events[0]["t"] == "2026-11-04T09:30:00Z"
    quiet = w.tick(T0 + timedelta(hours=7))
    assert _kinds(quiet) == ["quiet"] and quiet.events[0]["t"] == "2026-11-04T15:00:00Z"
    update_status("moss-build", tmp_path, now=T0 + timedelta(hours=7, seconds=5),
                  state="working")
    assert _kinds(w.tick(T0 + timedelta(hours=7, seconds=6))) == ["start"]


def test_counters_commits_pushes_and_ci(tmp_path):
    w = Watcher(tmp_path, [], tz=UTC)
    update_status(SID, tmp_path, now=T0, title="Fix save slots", agent="codex")
    w.tick(T0)
    update_status(SID, tmp_path, now=T0 + timedelta(minutes=1),
                  **{"x-commits": 2, "x-pushed": True, "x-ci": "running"})
    assert _kinds(w.tick(T0 + timedelta(minutes=1))) == ["commit", "push"]
    update_status(SID, tmp_path, now=T0 + timedelta(minutes=2), **{"x-ci": "red"})
    (ci,) = w.tick(T0 + timedelta(minutes=2)).events
    assert ci["k"] == "ci" and ci["ok"] is False
    assert _logged(tmp_path)[0]["n"] == 2


def test_new_project_event_once(tmp_path):
    w = Watcher(tmp_path, [], tz=UTC)
    w.tick(T0)
    update_status("kite-a", tmp_path, now=T0, title="A", **{"x-project": "paper-kite"})
    update_status("kite-b", tmp_path, now=T0, title="B", **{"x-project": "paper-kite"})
    result = w.tick(T0 + timedelta(seconds=3))
    assert sorted(_kinds(result)) == ["project", "start", "start"]
    update_status("kite-c", tmp_path, now=T0, title="C", **{"x-project": "paper-kite"})
    assert _kinds(w.tick(T0 + timedelta(seconds=6))) == ["start"]


def test_broken_and_foreign_files_make_no_events(tmp_path):
    w = Watcher(tmp_path, [], tz=UTC)
    w.tick(T0)
    (tmp_path / "broken.json").write_text("{nope", encoding="utf-8")
    (tmp_path / "other.json").write_text(json.dumps({"hello": 1}), encoding="utf-8")
    (tmp_path / ".hidden.json").write_text("{}", encoding="utf-8")
    result = w.tick(T0 + timedelta(seconds=3))
    assert result.events == [] and result.errors == []


def test_a_file_unreadable_for_a_moment_is_no_new_agent(tmp_path, monkeypatch):
    from lamptown.status import watch as watch_mod
    from lamptown.status.store import StatusError

    w = Watcher(tmp_path, [], tz=UTC)
    w.tick(T0)
    update_status(SID, tmp_path, now=T0, title="Fix save slots")
    assert _kinds(w.tick(T0 + timedelta(seconds=3))) == ["start"]
    update_status(SID, tmp_path, now=T0 + timedelta(seconds=4), state="waiting")
    real = watch_mod.read_status

    def busy(path):  # Windows: a writer is replacing the file right now
        raise StatusError("unreadable: PermissionError")

    monkeypatch.setattr(watch_mod, "read_status", busy)
    assert w.tick(T0 + timedelta(seconds=6)).events == []
    monkeypatch.setattr(watch_mod, "read_status", real)
    assert _kinds(w.tick(T0 + timedelta(seconds=9))) == ["wait"]


def test_state_survives_a_restart_without_duplicate_events(tmp_path):
    reader = FakeReader()
    reader.agents = [_agent(at=T0)]
    with Watcher(tmp_path, [reader], tz=UTC) as w:
        w.tick(T0)
    saved = tmp_path / ".lamptown" / "cursors.json"
    state = json.loads(saved.read_text(encoding="utf-8"))
    assert state["readers"]["fake"] == {"polls": 1} and SID in state["folder"]
    with Watcher(tmp_path, [reader], tz=UTC) as w:
        again = w.tick(T0 + timedelta(seconds=3))
    assert again.events == [] and again.errors == []
    assert reader.states[-1]["polls"] == 2
    records, _ = scan([tmp_path], T0)  # the state and the log are no status files
    assert [r.id for r in records] == [SID]


def test_a_damaged_state_file_starts_fresh(tmp_path):
    (tmp_path / ".lamptown").mkdir()
    (tmp_path / ".lamptown" / "cursors.json").write_text("{half", encoding="utf-8")
    w = Watcher(tmp_path, [FakeReader()], tz=UTC)
    assert w.tick(T0).errors == []
    assert json.loads((tmp_path / ".lamptown" / "cursors.json").read_text())["v"] == 1


def test_plot_seam(tmp_path):
    def plot_for(doc):
        return "workshop" if str(doc.get("x-folder", "")).startswith("/work/skills") else "lab"

    w = Watcher(tmp_path, [], plot_for=plot_for, tz=UTC)
    w.tick(T0)
    update_status("skill-a", tmp_path, now=T0, title="A", **{"x-folder": "/work/skills/kite"})
    update_status("idea-b", tmp_path, now=T0, title="B")
    plots = {e["id"]: e["plot"] for e in w.tick(T0).events if e["k"] == "start"}
    assert plots == {"skill-a": "workshop", "idea-b": "lab"}
    assert default_plot({"kind": "job"}) == "tower"
    assert default_plot({"x-plot": "library"}) == "library"
    assert default_plot({}) == "boathouse"


def test_private_folders_never_reach_disk(tmp_path):
    private = tmp_path / "hidden-work"
    reader = FakeReader()
    w = Watcher(tmp_path / "status", [reader], private_dirs=[private], tz=UTC)
    w.tick(T0)
    folder = str(private / "secret-plan")
    reader.agents = [{"id": "codex-99ff", "title": "Secret plan title", "state": "working",
                      "agent": "codex", "x-folder": folder, "at": T0}]
    reader.events = [{"t": T0, "id": "codex-99ff", "k": "commit", "title": "Secret plan title"}]
    result = w.tick(T0 + timedelta(seconds=3))
    assert {e["title"] for e in result.events} == {"Private agent"}
    assert {e["project"] for e in result.events} == {""}
    assert all(e["id"].startswith("private-") for e in result.events)
    log = "".join(p.read_text(encoding="utf-8")
                  for p in (tmp_path / "status" / "events").iterdir())
    state = (tmp_path / "status" / ".lamptown" / "cursors.json").read_text(encoding="utf-8")
    for text in (log, state):
        assert "secret" not in text.lower() and "hidden-work" not in text


def test_events_go_to_the_local_day(tmp_path):
    from datetime import timezone

    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=timezone(timedelta(hours=-8)))
    reader.agents = [_agent(at=T0)]  # 01:00 local on 4 Nov
    w.tick(T0)
    reader.agents = [_agent(state="ok", at=T0 - timedelta(hours=2))]  # older: kept
    w.tick(T0)
    assert sorted(p.name for p in (tmp_path / "events").iterdir()) == ["2026-11-04.jsonl"]


def test_prune_runs_once_a_day(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ev, "prune", lambda *a, **k: calls.append(k["now"]) or [])
    w = Watcher(tmp_path, [], tz=UTC)
    w.tick(T0)
    w.tick(T0 + timedelta(hours=1))
    w.tick(T0 + timedelta(days=1))
    assert len(calls) == 2


# ---------------------------------------------------------------- sanitiser
def test_sanitiser_drops_unknown_keys_and_full_paths():
    raw = {"id": SID, "title": "Edit /work/paper-kite/src/save.py", "state": "working",
           "prompt": "the user's words", "message": "Reading C:\\Users\\you\\notes.md",
           "x-folder": "/work/paper-kite", "x-secret": 1, "x-files": 4, "x-pushed": "yes",
           "x-ci": "purple", "needs_you": [{"text": "Answer /work/paper-kite/q.md",
                                            "href": "javascript:alert(1)"}],
           "progress": {"done": 5, "total": 2}, "at": "2026-11-04T09:00:00+01:00",
           "agent": "codex", "kind": "task", "started": "nonsense"}
    out = clean_agent(raw)
    assert out["title"] == "Edit save.py"
    assert out["message"] == "Reading notes.md"
    assert out["needs_you"] == [{"text": "Answer q.md"}]
    assert out["x-project"] == "paper-kite" and out["x-folder"] == "/work/paper-kite"
    assert out["at"] == datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
    for gone in ("prompt", "x-secret", "x-pushed", "x-ci", "progress", "started"):
        assert gone not in out
    assert out["x-files"] == 4


def test_sanitiser_rejects_bad_ids_and_keeps_removals():
    assert clean_agent({"id": "../../etc"}) is None
    assert clean_agent({"id": "con"}) is None  # a reserved Windows file name
    assert clean_agent("nope") is None
    out = clean_agent({"id": SID, "x-ci": None, "title": None, "x-project": "C:\\Users\\you\\moss"})
    assert out == {"id": SID, "at": None, "x-ci": None, "x-project": "moss"}


# ---------------------------------------------------------------- registry
READER_FILE = '''
from lamptown.status.readers import Batch

class Mine:
    def __init__(self, options):
        self.name = "mine"
        self.options = options

    def poll(self, state, now):
        return Batch(agents=[{"id": "mine-1", "title": self.options["title"]}])

def make_reader(options):
    return Mine(options)
'''


def test_local_reader_file_is_loaded(tmp_path):
    path = tmp_path / "mine_reader.py"
    path.write_text(READER_FILE, encoding="utf-8")
    reader = make_reader({"reader": str(path), "title": "Tidebook"})
    assert reader.name == "mine" and reader.options == {"title": "Tidebook"}
    status = tmp_path / "status"
    Watcher(status, [reader], tz=UTC).tick(T0)
    assert read_status(status / "mine-1.json")["title"] == "Tidebook"


def test_unknown_reader_kinds_say_so(tmp_path):
    with pytest.raises(ReaderError, match="unknown reader kind 'nope'"):
        find_reader("nope")
    with pytest.raises(ReaderError, match="not found"):
        find_reader("x", tmp_path / "missing.py")
    with pytest.raises(ReaderError, match="needs kind"):
        make_reader({})
    bad = tmp_path / "bad.py"
    bad.write_text("def make_reader(options):\n    return object()\n", encoding="utf-8")
    with pytest.raises(ReaderError, match="no valid name"):
        make_reader({"reader": str(bad)})


# ---------------------------------------------------------------- review fixes (L1)
class CursorReader:
    """A log reader: hands out the lines of ``log`` it hasn't handed out yet (its cursor is in
    its saved state, so a restart from older saved state replays)."""

    name = "cursor"

    def __init__(self):
        self.log: list[dict] = []

    def poll(self, state, now):
        pos = state.get("pos", 0)
        state["pos"] = len(self.log)
        return Batch(events=self.log[pos:])


def test_b2_first_catch_up_into_a_missing_status_folder_logs_events(tmp_path):
    folder = tmp_path / "status-not-yet"
    reader = FakeReader()
    w = Watcher(folder, [reader], tz=UTC)
    reader.agents = [_agent(state="ok", at=T0 - timedelta(hours=2))]
    result = w.tick(T0)
    assert result.written == [SID]
    assert sorted(_kinds(result)) == ["done", "project", "start"]
    assert sorted(e["k"] for e in _logged(folder)) == ["done", "project", "start"]


def test_b3_a_hook_write_in_the_same_second_beats_the_reader(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    # the Stop hook at 09:00:05.7 (stored as :05); the reader's last log line at 09:00:05.2
    update_status(SID, tmp_path, now=T0 + timedelta(seconds=5, microseconds=700000),
                  title="Fix save slots", state="waiting", agent="codex",
                  needs_you=[{"text": "Your turn"}])
    reader.agents = [{"id": SID, "state": "working",
                      "at": T0 + timedelta(seconds=5, microseconds=200000)}]
    result = w.tick(T0 + timedelta(seconds=6))
    doc = read_status(tmp_path / f"{SID}.json")
    assert result.kept == [SID] and result.written == []
    assert doc["state"] == "waiting" and doc["needs_you"] == [{"text": "Your turn"}]


def test_b3_the_reader_may_follow_its_own_write_in_the_same_second(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    reader.agents = [_agent(at=T0 + timedelta(seconds=5))]
    w.tick(T0 + timedelta(seconds=5))
    reader.agents = [_agent(state="waiting", at=T0 + timedelta(seconds=5, microseconds=900000))]
    result = w.tick(T0 + timedelta(seconds=6))
    assert result.written == [SID] and result.kept == []
    assert read_status(tmp_path / f"{SID}.json")["state"] == "waiting"


def test_b4_counters_merge_into_a_record_a_hook_wrote_later(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    update_status(SID, tmp_path, now=T0 + timedelta(seconds=10), title="Fix save slots",
                  state="working", agent="codex")  # a PostToolUse hook
    reader.agents = [{"id": SID, "state": "ok", "title": "Old title", "x-files": 3,
                      "x-commits": 1, "x-pushed": True, "x-ci": "running",
                      "x-folder": "/work/paper-kite", "at": T0 + timedelta(seconds=8)}]
    result = w.tick(T0 + timedelta(seconds=12))
    doc = read_status(tmp_path / f"{SID}.json")
    assert result.kept == [SID] and result.written == [SID]
    assert (doc["state"], doc["title"]) == ("working", "Fix save slots")  # the hook's
    assert (doc["x-files"], doc["x-commits"], doc["x-pushed"], doc["x-ci"]) == (3, 1, True,
                                                                                 "running")
    assert doc["x-folder"] == "/work/paper-kite" and doc["updated"] == "2026-11-04T09:00:10Z"
    assert sorted(_kinds(result)) == ["commit", "project", "push", "start"]
    # counters never go down, and a later tie with the hook's record still goes to the hook
    reader.agents = [{"id": SID, "state": "ok", "x-files": 2, "at": T0 + timedelta(seconds=10)}]
    again = w.tick(T0 + timedelta(seconds=15))
    doc = read_status(tmp_path / f"{SID}.json")
    assert again.kept == [SID] and doc["x-files"] == 3 and doc["state"] == "working"


def test_b5_a_lone_surrogate_never_stops_the_tick(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    half = json.loads(r'"Fix kite \ud83d"')
    reader.agents = [_agent(title=half, at=T0, **{"x-folder": "/work/kite" + half[-1]})]
    reader.events = [{"t": T0, "id": SID, "k": "commit", "title": half},
                     {"t": T0, "id": "other-1", "k": "commit", "title": "Fine"}]
    result = w.tick(T0 + timedelta(seconds=3))
    assert result.errors == [] and result.written == [SID]
    assert read_status(tmp_path / f"{SID}.json")["title"] == "Fix kite ?"
    assert sorted(e["title"] for e in _logged(tmp_path) if e["k"] == "commit") == [
        "Fine", "Fix kite ?"]
    # a hook that wrote a surrogate into a field the Watcher keeps in its state
    (tmp_path / "hooked.json").write_text(json.dumps({
        "spec": "agent-status/1", "id": "hooked", "title": half, "state": "working",
        "updated": "2026-11-04T09:00:05Z", "x-pushed": half}), encoding="utf-8")
    later = w.tick(T0 + timedelta(seconds=6))
    assert later.errors == [] and "push" in _kinds(later)
    w.close()


def test_b6_a_second_watcher_on_the_same_folder_stands_by(tmp_path):
    first, second = FakeReader(), FakeReader()
    wa, wb = Watcher(tmp_path, [first], tz=UTC), Watcher(tmp_path, [second], tz=UTC)
    wa.tick(T0)
    standby = wb.tick(T0)
    assert standby.busy and standby.errors == [
        "another Lamptown watcher is running on this status folder"]
    update_status(SID, tmp_path, now=T0 + timedelta(seconds=1), title="Fix", state="waiting")
    wa.tick(T0 + timedelta(seconds=3))
    assert wb.tick(T0 + timedelta(seconds=4)).busy
    assert second.states == []  # the stand-by Watcher never polled its readers
    assert [e["k"] for e in _logged(tmp_path)] == ["start", "wait"]  # once, not twice
    wa.close()  # the app quits: the other one takes over, from the saved state
    update_status(SID, tmp_path, now=T0 + timedelta(seconds=7), state="ok")
    took = wb.tick(T0 + timedelta(seconds=8))
    assert not took.busy and _kinds(took) == ["done"]
    wb.close()


def test_m1_events_are_kept_when_the_log_cannot_be_written(tmp_path, monkeypatch):
    reader = CursorReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    reader.log.append({"t": T0, "id": SID, "k": "commit", "title": "Fix"})
    real = ev.append

    def full_disk(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ev, "append", full_disk)
    failed = w.tick(T0 + timedelta(seconds=3))
    assert failed.errors == ["event log: No space left on device"] and failed.events == []
    assert w.state()["readers"]["cursor"] == {"pos": 0}  # the cursor did not move
    monkeypatch.setattr(ev, "append", real)
    assert _kinds(w.tick(T0 + timedelta(seconds=6))) == ["commit"]
    assert [e["k"] for e in _logged(tmp_path)] == ["commit"]
    w.close()


def test_m1_a_restart_from_older_saved_state_does_not_double_events(tmp_path, monkeypatch):
    from lamptown.status import watch as watch_mod

    reader = CursorReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)  # saved
    reader.log.append({"t": T0, "id": SID, "k": "commit", "title": "Fix"})
    update_status("moss-build", tmp_path, now=T0, title="Moss build")

    def crash(*a, **k):  # the process dies after the log write, before the save
        raise OSError(5, "I/O error")

    monkeypatch.setattr(watch_mod, "atomic_write_json", crash)
    assert sorted(_kinds(w.tick(T0 + timedelta(seconds=3)))) == ["commit", "start"]
    monkeypatch.undo()
    w._unlock()  # a crash: the state of that tick was never saved
    again = Watcher(tmp_path, [reader], tz=UTC)
    assert again.state()["readers"]["cursor"] == {"pos": 0}  # older state: it replays...
    assert again.tick(T0 + timedelta(seconds=6)).errors == []
    assert sorted(e["k"] for e in _logged(tmp_path)) == ["commit", "start"]  # ...once
    again.close()


def test_m2_too_much_from_one_reader_is_reported(tmp_path, monkeypatch):
    from lamptown.status import watch as watch_mod

    monkeypatch.setattr(watch_mod, "MAX_AGENTS", 2)
    monkeypatch.setattr(watch_mod, "MAX_EVENTS", 1)
    reader = FakeReader()
    reader.agents = [_agent(id=f"kite-{i}", at=T0) for i in range(3)]
    reader.events = [{"t": T0, "id": SID, "k": "commit"}] * 2
    result = Watcher(tmp_path, [reader], tz=UTC).tick(T0)
    assert result.errors == [
        "reader fake: 3 status changes in one poll, only 2 taken",
        "reader fake: 2 events in one poll, only 1 taken"]


def test_m3_reader_events_without_record_or_folder_are_masked_with_private_dirs(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path / "status", [reader], private_dirs=[tmp_path / "hidden-work"],
                tz=UTC)
    w.tick(T0)
    reader.events = [
        {"t": T0, "id": "git-secret", "k": "commit", "title": "Secret plan", "project": "plan"},
        {"t": T0, "id": "git-open", "k": "push", "title": "Open work", "project": "kite",
         "folder": "/work/kite"}]
    result = w.tick(T0 + timedelta(seconds=3))
    by_kind = {e["k"]: e for e in result.events}
    assert by_kind["commit"]["title"] == "Private agent" and by_kind["commit"]["project"] == ""
    assert by_kind["push"]["title"] == "Open work" and by_kind["push"]["project"] == "kite"


def test_m4_a_rewrite_with_the_same_size_and_time_is_still_seen(tmp_path):
    import os

    w = Watcher(tmp_path, [], tz=UTC)
    w.tick(T0)
    update_status(SID, tmp_path, now=T0, title="Fix", state="working")
    path = tmp_path / f"{SID}.json"
    w.tick(T0 + timedelta(seconds=3))
    before = path.stat()
    update_status(SID, tmp_path, now=T0, state="waiting")  # same size ("working" is 7 too)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))  # a coarse clock
    assert path.stat().st_size == before.st_size
    if path.stat().st_ino in (0, before.st_ino):
        pytest.skip("this file system has no file ids")
    assert _kinds(w.tick(T0 + timedelta(seconds=6))) == ["wait"]


def test_m8_catch_up_older_than_the_kept_days_makes_no_day_file(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    reader.events = [{"t": T0 - timedelta(days=20), "id": SID, "k": "commit", "title": "x"},
                     {"t": T0 - timedelta(days=1), "id": SID, "k": "commit", "title": "y"}]
    w.tick(T0 + timedelta(seconds=3))
    assert sorted(p.name for p in (tmp_path / "events").iterdir()) == ["2026-11-03.jsonl"]


def test_m9_a_late_files_event_joins_the_next_group(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    reader.events = [{"t": T0 + timedelta(seconds=10), "id": SID, "k": "files", "n": 2}]
    w.tick(T0 + timedelta(seconds=20))
    w.tick(T0 + timedelta(seconds=70))  # the 09:00 group is written
    reader.events = [{"t": T0 + timedelta(seconds=50), "id": SID, "k": "files", "n": 1}]
    w.tick(T0 + timedelta(seconds=73))
    w.tick(T0 + timedelta(seconds=130))
    files = [(e["t"], e["n"]) for e in _logged(tmp_path) if e["k"] == "files"]
    assert files == [("2026-11-04T09:00:10Z", 2), ("2026-11-04T09:01:00Z", 1)]


def test_m10_saved_state_holds_no_log_paths(tmp_path):
    from lamptown.status.readers.tail import Budget, tail

    log = tmp_path / "C--Users-you-secret-app" / "abc.jsonl"
    log.parent.mkdir()
    log.write_bytes(b'{"x":1}\n')

    class Tailer:
        name = "tailer"

        def poll(self, state, now, budget=None):
            tail(log, state.setdefault("files", {}), budget or Budget())
            return Batch()

    with Watcher(tmp_path / "status", [Tailer()], tz=UTC) as w:
        w.tick(T0)
    text = (tmp_path / "status" / ".lamptown" / "cursors.json").read_text(encoding="utf-8")
    assert "secret-app" not in text and '"files"' in text


def test_m11_state_is_saved_at_most_every_few_seconds(tmp_path, monkeypatch):
    from lamptown.status import watch as watch_mod

    writes = []
    real = watch_mod.atomic_write_json
    monkeypatch.setattr(watch_mod, "atomic_write_json",
                        lambda *a, **k: writes.append(1) or real(*a, **k))
    reader = FakeReader()  # its saved state changes every tick
    w = Watcher(tmp_path, [reader], tz=UTC)
    for second in range(0, 60, 3):
        w.tick(T0 + timedelta(seconds=second))
    assert 2 <= len(writes) <= 4  # not 20
    w.close()
    saved = json.loads((tmp_path / ".lamptown" / "cursors.json").read_text(encoding="utf-8"))
    assert saved["readers"]["fake"]["polls"] == 20  # close() saves the last tick


def test_m11_records_past_their_expiry_shrink_in_the_snapshot(tmp_path):
    update_status("old-run", tmp_path, now=T0 - timedelta(days=9), title="Old run", state="ok",
                  **{"x-files": 4})
    update_status("new-run", tmp_path, now=T0, title="New run", state="working")
    w = Watcher(tmp_path, [], tz=UTC)
    w.tick(T0)
    snap = w.state()["folder"]
    assert "ti" not in snap["old-run"] and "ti" in snap["new-run"]
    assert len(json.dumps(snap["old-run"])) < len(json.dumps(snap["new-run"])) / 2
    assert w.tick(T0 + timedelta(seconds=3)).events == []  # no news while it sleeps
    update_status("old-run", tmp_path, now=T0 + timedelta(seconds=4), state="working",
                  **{"x-files": 6})
    woke = w.tick(T0 + timedelta(seconds=5))
    assert _kinds(woke) == ["start"]  # back to work: one start, files +2 still open
    assert w.state()["pending"] and next(iter(w.state()["pending"].values()))["n"] == 2
    w.close()


def test_m12_private_ids_match_the_collector(tmp_path):
    from lamptown.status.collector import collect

    private = tmp_path / "hidden-work"
    status = tmp_path / "status"
    w = Watcher(status, [], private_dirs=[private], tz=UTC)
    w.tick(T0)
    update_status("codex-99ff", status, now=T0, title="Secret plan", state="working",
                  **{"x-folder": str(private / "plan")})
    (event,) = w.tick(T0 + timedelta(seconds=3)).events
    room = collect({"dir": str(status), "private_dirs": [str(private)]}, T0)
    assert [row["id"] for row in room["rows"]] == [event["id"]]


def test_m12_readers_share_one_budget_per_tick(tmp_path):
    seen = []

    class Hungry:
        def __init__(self, name):
            self.name = name

        def poll(self, state, now, budget=None):
            seen.append(budget)
            budget.spend(budget.allow())
            return Batch()

    old_style = FakeReader()  # poll(state, now): still called
    w = Watcher(tmp_path, [Hungry("a"), old_style, Hungry("b")], tz=UTC)
    assert w.tick(T0).errors == []
    assert seen[0] is seen[1] and seen[0].left == seen[0].total - 2 * seen[0].per_file
    assert old_style.states == [{"polls": 1}]


# ---------------------------------------------------------------- re-check fixes (W1)
def test_w1_a_crash_after_a_logged_event_never_logs_it_again(tmp_path):
    update_status("hook-1", tmp_path, now=T0 - timedelta(seconds=5), title="Fix kite",
                  state="working", agent="codex")
    w = Watcher(tmp_path, [], tz=UTC)
    w.tick(T0)  # the snapshot, saved
    update_status("other-1", tmp_path, now=T0 + timedelta(seconds=24), title="Other")
    w.tick(T0 + timedelta(seconds=25))  # saved again (25 s after the last save)
    update_status("hook-1", tmp_path, now=T0 + timedelta(seconds=26), state="waiting")
    assert _kinds(w.tick(T0 + timedelta(seconds=27))) == ["wait"]  # 2 s after the last save
    update_status("hook-1", tmp_path, now=T0 + timedelta(seconds=28), **{"x-files": 3})
    w.tick(T0 + timedelta(seconds=29))  # the record is touched again: it gets a new time
    w._unlock()  # the process dies: no close()
    again = Watcher(tmp_path, [], tz=UTC)
    again.tick(T0 + timedelta(seconds=31))
    again.tick(T0 + timedelta(seconds=65))  # the files group of minute 09:00 is written
    again.close()
    assert [e["k"] for e in _logged(tmp_path) if e["id"] == "hook-1"] == ["wait", "files"]


def test_w1_ticks_without_events_still_save_at_most_every_few_seconds(tmp_path, monkeypatch):
    from lamptown.status import watch as watch_mod

    writes = []
    real = watch_mod.atomic_write_json
    monkeypatch.setattr(watch_mod, "atomic_write_json",
                        lambda *a, **k: writes.append(1) or real(*a, **k))
    reader = CursorReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    before = len(writes)
    reader.log.append({"t": T0, "id": SID, "k": "commit", "title": "Fix"})
    w.tick(T0 + timedelta(seconds=3))  # logged a line: saved at once
    assert len(writes) == before + 1
    w.tick(T0 + timedelta(seconds=6))  # nothing new: no write
    assert len(writes) == before + 1
    w.close()


def test_w5_tick_events_are_only_the_lines_the_log_took(tmp_path):
    reader = CursorReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    reader.log.append({"t": T0 - timedelta(days=20), "id": SID, "k": "commit", "title": "Old"})
    old = w.tick(T0 + timedelta(seconds=3))
    assert old.events == [] and not (tmp_path / "events").exists()  # too old for keep_days
    reader.log.append({"t": T0 + timedelta(seconds=4), "id": SID, "k": "commit", "title": "Fix"})
    assert _kinds(w.tick(T0 + timedelta(seconds=6))) == ["commit"]
    w._unlock()  # a crash; the next run's reader sends that commit again
    replay = FakeReader()
    replay.events = [dict(reader.log[-1])]
    again = Watcher(tmp_path, [replay], tz=UTC)
    assert again.tick(T0 + timedelta(seconds=9)).events == []  # already in the log
    again.close()
    assert [e["title"] for e in _logged(tmp_path)] == ["Fix"]


def test_w6_a_hook_record_with_a_lone_surrogate_still_takes_merges(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    half = json.loads(r'"Fix kite \ud83d"')
    (tmp_path / f"{SID}.json").write_text(json.dumps({
        "spec": "agent-status/1", "id": SID, "title": half, "state": "working",
        "updated": "2026-11-04T09:00:05Z", "message": half,
        "needs_you": [{"text": half}]}), encoding="utf-8")  # a Node hook writes "\ud83d"
    w.tick(T0 + timedelta(seconds=6))
    reader.agents = [{"id": SID, "x-files": 2, "at": T0 + timedelta(seconds=9)}]
    result = w.tick(T0 + timedelta(seconds=10))
    assert result.errors == [] and result.written == [SID]
    doc = read_status(tmp_path / f"{SID}.json")
    assert doc["x-files"] == 2 and doc["title"] == doc["message"] == "Fix kite ?"
    assert doc["needs_you"] == [{"text": "Fix kite ?"}]
    w.close()


def test_w7_after_a_restart_the_reader_may_follow_its_own_write_in_the_same_second(tmp_path):
    reader = FakeReader()
    with Watcher(tmp_path, [reader], tz=UTC) as w:
        w.tick(T0)
        reader.agents = [_agent(at=T0 + timedelta(seconds=5))]
        w.tick(T0 + timedelta(seconds=5))
    with Watcher(tmp_path, [reader], tz=UTC) as w:  # the app restarted
        reader.agents = [_agent(state="waiting", needs_you=[{"text": "Your turn"}],
                                at=T0 + timedelta(seconds=5, microseconds=900000))]
        result = w.tick(T0 + timedelta(seconds=6))
    assert result.kept == [] and result.written == [SID]
    assert read_status(tmp_path / f"{SID}.json")["state"] == "waiting"
    saved = (tmp_path / ".lamptown" / "cursors.json").read_text(encoding="utf-8")
    assert "Fix save slots" not in saved.split('"wrote"')[-1]  # digests only, no names


def test_w9_only_a_taken_lock_means_another_watcher(tmp_path, monkeypatch):
    import errno

    from lamptown.status import watch as watch_mod

    def unsupported(fd):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(watch_mod, "_lock_fd", unsupported)
    result = Watcher(tmp_path, [], tz=UTC).tick(T0)
    assert not result.busy and result.errors == ["watcher lock: No locks available"]

    def taken(fd):
        raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")

    monkeypatch.setattr(watch_mod, "_lock_fd", taken)
    result = Watcher(tmp_path, [], tz=UTC).tick(T0)
    assert result.busy and result.errors == [watch_mod.BUSY]


# ---------------------------------------------------------------- review fixes (W1-fix2)
def _at(seconds):
    return T0 + timedelta(seconds=seconds)


class AgentScript:
    """A log reader of status changes: ``(seconds, agent)`` pairs, handed out by a cursor
    kept in its saved state (so a restart from older saved state replays them)."""

    name = "script"

    def __init__(self, items):
        self.items = items

    def poll(self, state, now):
        i = state.get("i", 0)
        agents = []
        while i < len(self.items) and _at(self.items[i][0]) <= now:
            seconds, doc = self.items[i]
            agents.append({**doc, "at": _at(seconds)})
            i += 1
        state["i"] = i
        return Batch(agents=agents)


def test_w1fix2_a_failed_save_after_a_logged_tick_is_retried_next_tick(tmp_path, monkeypatch):
    from lamptown.status import watch as watch_mod

    update_status("hook-1", tmp_path, now=_at(-5), title="Fix kite", state="working",
                  agent="codex")
    w = Watcher(tmp_path, [], tz=UTC)
    w.tick(_at(0))
    update_status("other-1", tmp_path, now=_at(24), title="Other")
    w.tick(_at(25))  # saved
    update_status("hook-1", tmp_path, now=_at(26), state="waiting")
    real = watch_mod.atomic_write_json

    def held(*args, **kwargs):  # an antivirus scanner holds cursors.json past the retries
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(watch_mod, "atomic_write_json", held)
    failed = w.tick(_at(27))
    assert _kinds(failed) == ["wait"] and failed.errors[0].startswith("watcher state:")
    monkeypatch.setattr(watch_mod, "atomic_write_json", real)
    update_status("hook-1", tmp_path, now=_at(28), title="Fix kite tail")  # no event
    w.tick(_at(29))  # only 4 s after the last good save: still saved, to retry the failed one
    w._unlock()  # the process dies: no close()
    again = Watcher(tmp_path, [], tz=UTC)
    again.tick(_at(31))
    again.close()
    assert [e["k"] for e in _logged(tmp_path) if e["id"] == "hook-1"] == ["wait"]


def test_w1fix2_a_record_write_without_events_is_saved_at_once(tmp_path):
    items = [
        (5, {"id": SID, "title": "Fix save slots", "state": "working", "agent": "codex"}),
        (10, {"id": SID, "title": "Fix save slots now"}),  # a record write, no event
        (10.9, {"id": SID, "state": "waiting", "needs_you": [{"text": "Your turn"}]}),
    ]
    w = Watcher(tmp_path, [AgentScript(items[:1])], tz=UTC)
    w.tick(_at(0))
    w.tick(_at(6))  # the record is made and "start" logged: saved
    w.readers = [AgentScript(items[:2])]
    assert w.tick(_at(10)).written == [SID]
    w._unlock()  # the process dies 0 s after its own write
    with Watcher(tmp_path, [AgentScript(items)], tz=UTC) as again:
        result = again.tick(_at(11))  # it still knows the record is its own
    assert result.kept == [] and result.written == [SID]
    assert read_status(tmp_path / f"{SID}.json")["state"] == "waiting"


def test_w1fix2_a_crash_mid_minute_keeps_the_files_count(tmp_path):
    def run(folder, crash):
        update_status(SID, folder, now=_at(-5), title="Fix kite", state="working",
                      agent="codex")
        reader = CursorReader()
        w = Watcher(folder, [reader], tz=UTC)
        w.tick(_at(0))
        reader.log.append({"t": _at(2), "id": SID, "k": "files", "n": 2})
        w.tick(_at(3))  # the reader's 2 files: the minute's group is still open
        update_status(SID, folder, now=_at(5), **{"x-files": 3})
        w.tick(_at(6))  # the hook's 3 files join the open group
        if crash:
            w._unlock()  # the process dies: no close()
            w = Watcher(folder, [reader], tz=UTC)
        w.tick(_at(9))
        w.tick(_at(65))  # minute 09:00 is over: its group is written
        w.close()
        return [e.get("n") for e in _logged(folder) if e["k"] == "files"]

    clean = run(tmp_path / "clean", crash=False)
    assert clean == [5]
    assert run(tmp_path / "crash", crash=True) == clean


def test_w1fix2_a_push_or_ci_from_both_sources_is_logged_once(tmp_path):
    update_status(SID, tmp_path, now=_at(-5), title="Fix kite", state="working", agent="codex")
    reader = CursorReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(_at(0))
    reader.log.append({"t": _at(63), "id": SID, "k": "push"})
    assert _kinds(w.tick(_at(63))) == ["push"]  # the reader saw the push in the session log
    w._unlock()  # the process dies: the memory of it is in the saved state
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(_at(64))
    update_status(SID, tmp_path, now=_at(66), **{"x-pushed": True})
    assert _kinds(w.tick(_at(66))) == []  # the record's copy of the same push, 3 s later
    reader.log.append({"t": _at(70), "id": SID, "k": "ci", "ok": True})
    assert _kinds(w.tick(_at(70))) == ["ci"]
    update_status(SID, tmp_path, now=_at(72), **{"x-ci": "green"})
    assert _kinds(w.tick(_at(72))) == []  # the same green run
    update_status(SID, tmp_path, now=_at(80), **{"x-ci": "red"})
    assert _kinds(w.tick(_at(80))) == ["ci"]  # a new result is news
    reader.log.append({"t": _at(100), "id": SID, "k": "push"})
    assert _kinds(w.tick(_at(100))) == ["push"]  # a second push from the same source
    saved = (tmp_path / ".lamptown" / "cursors.json").read_text(encoding="utf-8")
    recent = json.loads(saved)["recent"]
    assert recent and SID not in json.dumps(recent)  # hashed: no ids in it
    w.tick(_at(400))
    assert w.state()["recent"] == {}  # forgotten after two minutes
    w.close()
    pushes = [(e["k"], e["t"][14:19]) for e in _logged(tmp_path) if e["k"] in ("push", "ci")]
    assert pushes == [("push", "01:03"), ("ci", "01:10"), ("ci", "01:20"), ("push", "01:40")]


def test_w1fix2_the_push_memory_stays_bounded(tmp_path):
    from lamptown.status import watch as watch_mod

    reader = CursorReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(_at(0))
    for i in range(watch_mod.MAX_RECENT + 50):
        reader.log.append({"t": _at(1), "id": f"codex-{i:05d}", "k": "push"})
    w.tick(_at(3))
    assert len(w.state()["recent"]) == watch_mod.MAX_RECENT
    w.close()
