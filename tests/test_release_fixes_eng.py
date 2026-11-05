"""Release-review fixes (engine side): a tick cut short never loses events, the Watcher tidies
its own old records, a record that can't be read for a moment is never rewritten with only the
change, and error lines carry no full path. Every name and path is made up (``tmp_path``,
``C:\\Users\\you``)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lamptown.status import events as ev
from lamptown.status import store
from lamptown.status import watch as watch_mod
from lamptown.status.readers import Batch
from lamptown.status.store import StatusError, read_status, update_status
from lamptown.status.watch import Watcher

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC)
SID = "codex-3f9a2b"
SECRET = "C:\\Users\\you\\clients\\kite-secret\\session.jsonl"


def _logged(folder: Path, days: int = 1, now: datetime = T0) -> list[dict]:
    return [e for _, es in ev.read_days(folder, days, now=now, tz=UTC) for e in es]


class LogReader:
    """A log of events; its saved state is how far it has handed them out (like tail)."""

    name = "log"

    def __init__(self) -> None:
        self.log: list[dict] = []

    def poll(self, state, now):
        pos = state.get("pos", 0)
        state["pos"] = len(self.log)
        return Batch(events=self.log[pos:])


class Trap:
    """Raises ``exc`` once, on the poll after it is armed."""

    name = "trap"

    def __init__(self) -> None:
        self.exc: BaseException | None = None

    def poll(self, state, now):
        state["seen"] = state.get("seen", 0) + 1
        exc, self.exc = self.exc, None
        if exc is not None:
            raise exc
        return Batch()


class FakeReader:
    name = "fake"

    def __init__(self) -> None:
        self.agents: list[dict] = []

    def poll(self, state, now):
        batch, self.agents = Batch(agents=self.agents), []
        return batch


START = {"t": T0 + timedelta(seconds=30), "id": "agent-1", "k": "start", "title": "One",
         "folder": "/work/kite"}


# ---------------------------------------------------------------- engine 1: Ctrl+C mid-tick
def test_ctrl_c_mid_tick_loses_no_event(tmp_path):
    """The reviewer's repro: Ctrl+C lands after the log reader moved its cursor, then close()
    saves. The next run must still log the start."""
    log, trap = LogReader(), Trap()
    w = Watcher(tmp_path, [log, trap], tz=UTC)
    w.tick(T0)
    log.log.append(START)
    trap.exc = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        w.tick(T0 + timedelta(minutes=1))
    w.close()
    saved = json.loads((tmp_path / ".lamptown" / "cursors.json").read_text(encoding="utf-8"))
    assert saved["readers"]["log"] == {"pos": 0}, "a half-done tick's cursor was saved"
    log2 = LogReader()
    log2.log = [START]
    with Watcher(tmp_path, [log2, Trap()], tz=UTC) as w2:
        w2.tick(T0 + timedelta(minutes=2))
        w2.tick(T0 + timedelta(minutes=3))
    assert [(e["id"], e["k"]) for e in _logged(tmp_path)] == [("agent-1", "start")]


def test_any_exception_mid_tick_is_rolled_back_in_memory(tmp_path, monkeypatch):
    """A bug half-way (here in the files grouping) is caught by LiveFeed and the next tick goes
    on: it must go on from the last finished tick, not the half-done one."""
    log = LogReader()
    w = Watcher(tmp_path, [log], tz=UTC)
    w.tick(T0)
    log.log.append(START)
    real = Watcher._group

    def boom(found, state, now):
        raise RuntimeError("bug")

    monkeypatch.setattr(Watcher, "_group", staticmethod(boom))
    with pytest.raises(RuntimeError):
        w.tick(T0 + timedelta(minutes=1))
    monkeypatch.setattr(Watcher, "_group", staticmethod(real))
    again = w.tick(T0 + timedelta(minutes=2))
    assert [(e["id"], e["k"]) for e in again.events] == [("agent-1", "start")]
    w.close()


def test_ctrl_c_after_the_log_write_logs_no_duplicate(tmp_path, monkeypatch):
    """Ctrl+C after the events reached the log (in the save): the replay is de-duplicated."""
    log = LogReader()
    w = Watcher(tmp_path, [log], tz=UTC)
    w.tick(T0)
    log.log.append(START)
    real = Watcher._save_tick

    def interrupted(self, *a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(Watcher, "_save_tick", interrupted)
    with pytest.raises(KeyboardInterrupt):
        w.tick(T0 + timedelta(minutes=1))
    monkeypatch.setattr(Watcher, "_save_tick", real)
    w.close()
    log2 = LogReader()
    log2.log = [START]
    with Watcher(tmp_path, [log2], tz=UTC) as w2:
        w2.tick(T0 + timedelta(minutes=2))
    assert [(e["id"], e["k"]) for e in _logged(tmp_path)] == [("agent-1", "start")]


# ---------------------------------------------------------------- engine 4: unreadable record
def _flaky_reads(monkeypatch, fail_from: int) -> dict:
    """Every read_status call (the Watcher's and the store's) from call ``fail_from`` on
    raises, as on Windows while a hook replaces the file."""
    real = store.read_status
    calls = {"n": 0, "on": True}

    def flaky(path):
        calls["n"] += 1
        if calls["on"] and calls["n"] >= fail_from:
            raise StatusError("unreadable: PermissionError")
        return real(path)

    monkeypatch.setattr(store, "read_status", flaky)
    monkeypatch.setattr(watch_mod, "read_status", flaky)
    monkeypatch.setattr(store, "READ_WAIT", 0.0, raising=False)
    return calls


def test_a_record_unreadable_for_a_moment_is_not_rewritten_with_only_the_change(
        tmp_path, monkeypatch):
    update_status(SID, tmp_path, now=T0, title="Fix save slots", state="working",
                  **{"x-folder": "/work/paper-kite", "x-project": "paper-kite"})
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    before = read_status(tmp_path / f"{SID}.json")
    calls = _flaky_reads(monkeypatch, fail_from=2)  # the Watcher's read works, the next fails
    reader.agents = [{"id": SID, "at": T0 + timedelta(seconds=5), "state": "waiting"}]
    put_off = w.tick(T0 + timedelta(seconds=5))
    calls["on"] = False
    doc = read_status(tmp_path / f"{SID}.json")
    assert doc == before, "the record was rewritten while it couldn't be read"
    assert put_off.retry == [SID] and put_off.errors == []
    later = w.tick(T0 + timedelta(seconds=8))
    doc = read_status(tmp_path / f"{SID}.json")
    assert later.written == [SID]
    assert doc["title"] == "Fix save slots" and doc["state"] == "waiting"
    assert doc["x-folder"] == "/work/paper-kite" and doc["x-project"] == "paper-kite"
    w.close()


def test_a_record_that_stays_broken_is_still_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "READ_WAIT", 0.0, raising=False)
    (tmp_path / f"{SID}.json").write_text("{broken", encoding="utf-8")
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    reader.agents = [{"id": SID, "at": T0, "title": "Fix save slots", "state": "working"}]
    ticks = [w.tick(T0 + timedelta(seconds=3 * i)) for i in range(1, watch_mod.HOLD_TICKS + 3)]
    assert any(SID in t.written for t in ticks)
    assert read_status(tmp_path / f"{SID}.json")["title"] == "Fix save slots"
    w.close()


def test_update_status_can_refuse_an_unreadable_file(tmp_path, monkeypatch):
    update_status(SID, tmp_path, now=T0, title="Fix save slots")
    _flaky_reads(monkeypatch, fail_from=1)
    with pytest.raises(store.StatusBusy):
        update_status(SID, tmp_path, now=T0, replace_unreadable=False, state="ok")
    monkeypatch.undo()
    assert read_status(tmp_path / f"{SID}.json")["title"] == "Fix save slots"


# ---------------------------------------------------------------- engine 2: tidy own records
N_OWN = 2001


def _fill(w: Watcher, reader: FakeReader, n: int, when: datetime) -> None:
    agents = [{"id": f"claude-code-{i:06d}", "at": when, "title": f"Session {i}", "state": "ok",
               "agent": "claude-code", "x-folder": f"/work/p{i % 40}"} for i in range(n)]
    for i in range(0, n, 500):
        reader.agents = agents[i:i + 500]
        w.tick(when + timedelta(seconds=i // 500 + 1))


def test_the_watcher_removes_its_own_old_records_and_stays_small(tmp_path, monkeypatch):
    monkeypatch.setattr(store.os, "fsync", lambda fd: None)  # 2000 writes: keep the test quick
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    _fill(w, reader, N_OWN, T0)
    # a working Codex agent, a hook's own old record, and an own record a hook wrote over later
    reader.agents = [{"id": SID, "at": T0, "title": "Trim", "state": "working"}]
    w.tick(T0 + timedelta(seconds=30))
    update_status("hook-run", tmp_path, now=T0, title="Hook run", state="ok")
    update_status("claude-code-000007", tmp_path, now=T0 + timedelta(seconds=40), state="ok",
                  message="Done")
    names = lambda: {p.stem for p in tmp_path.glob("*.json")}  # noqa: E731
    assert len(names()) == N_OWN + 2

    w.tick(T0 + timedelta(days=10))  # asleep, but inside the history window: all stay
    assert len(names()) == N_OWN + 2

    later = T0 + timedelta(days=15)
    for i in range(30):
        w.tick(later + timedelta(seconds=3 * i))
        if len(names()) <= 3:
            break
    # the Codex agent went quiet and expired too, and it was the Watcher's: it goes as well
    assert names() == {"hook-run", "claude-code-000007"}
    assert _logged(tmp_path, days=1, now=later) == [], "removing a record is no event"
    w.save()  # (a removal alone isn't urgent to save: a restart just finds the file gone)
    size = (tmp_path / ".lamptown" / "cursors.json").stat().st_size
    assert size < 20_000, size  # far under the 4 MB cap

    # a tick with nothing changed lists the folder once and reads no file
    reads, lists = {"n": 0}, {"n": 0}
    real_read, real_iterdir = watch_mod.read_status, Path.iterdir

    def counting_read(path):
        reads["n"] += 1
        return real_read(path)

    def counting_iterdir(self):
        if self == tmp_path:
            lists["n"] += 1
        return real_iterdir(self)

    monkeypatch.setattr(watch_mod, "read_status", counting_read)
    monkeypatch.setattr(Path, "iterdir", counting_iterdir)
    quiet = w.tick(later + timedelta(minutes=5))
    assert quiet.events == [] and quiet.written == []
    assert reads["n"] == 0 and lists["n"] == 1
    w.close()


def test_removal_can_be_switched_off_and_survives_a_restart(tmp_path):
    reader = FakeReader()
    with Watcher(tmp_path, [reader], tz=UTC) as w:
        w.tick(T0)
        reader.agents = [{"id": SID, "at": T0, "title": "Trim", "state": "ok"}]
        w.tick(T0 + timedelta(seconds=3))
    later = T0 + timedelta(days=15)
    with Watcher(tmp_path, [], tz=UTC, remove_own=False) as w:
        w.tick(later)
    assert (tmp_path / f"{SID}.json").exists()
    with Watcher(tmp_path, [], tz=UTC) as w:  # a restart still knows the record is its own
        w.tick(later + timedelta(seconds=3))
        w.tick(later + timedelta(seconds=6))  # fix2: ENG 4 (a run's first tick never tidies)
    assert not (tmp_path / f"{SID}.json").exists()


def test_an_entry_saved_before_the_fix_learns_its_time(tmp_path):
    reader = FakeReader()
    with Watcher(tmp_path, [reader], tz=UTC) as w:
        w.tick(T0)
        reader.agents = [{"id": SID, "at": T0, "title": "Trim", "state": "ok"}]
        w.tick(T0 + timedelta(seconds=3))
        w.tick(T0 + timedelta(days=8))  # asleep
    saved = tmp_path / ".lamptown" / "cursors.json"
    state = json.loads(saved.read_text(encoding="utf-8"))
    entry = state["folder"][SID]
    assert entry["x"] == 1 and entry.pop("u") == "2026-11-04T09:00:00Z"
    saved.write_text(json.dumps(state), encoding="utf-8")  # as 0.2 before the fix saved it
    with Watcher(tmp_path, [], tz=UTC) as w:
        assert w.tick(T0 + timedelta(days=9)).events == []
        assert w.state()["folder"][SID]["u"] == "2026-11-04T09:00:00Z"
        w.tick(T0 + timedelta(days=15))
        w.tick(T0 + timedelta(days=15, seconds=3))  # fix2: ENG 4 (not right after a gap)
    assert not (tmp_path / f"{SID}.json").exists()


# ---------------------------------------------------------------- privacy 5: short reasons
def _no_path(text: str) -> None:
    assert "Users" not in text and "kite-secret" not in text and not re.search(
        r"[A-Za-z]:[\\/]", text), text


def test_safe_reason_drops_paths():
    from lamptown.status.store import safe_reason

    assert safe_reason(PermissionError(13, "Permission denied", SECRET)) == "Permission denied"
    assert safe_reason(ValueError(f"bad line in {SECRET}"), typed=True) == "ValueError"
    assert safe_reason(RuntimeError("log moved"), typed=True) == "RuntimeError: log moved"


class OsReader:
    name = "osr"

    def poll(self, state, now):
        raise PermissionError(13, "Permission denied", SECRET)


def test_watcher_error_lines_carry_no_path(tmp_path, monkeypatch):
    reader = FakeReader()
    w = Watcher(tmp_path, [OsReader(), reader], tz=UTC)
    first = w.tick(T0)
    assert first.errors == ["reader osr: PermissionError: Permission denied"]

    def denied(*a, **k):
        raise PermissionError(13, "Permission denied", SECRET)
    monkeypatch.setattr(watch_mod, "atomic_write_json", denied)
    reader.agents = [{"id": SID, "at": T0, "title": "Trim", "state": "working"}]
    result = w.tick(T0 + timedelta(seconds=3))
    lines = [e for e in result.errors if not e.startswith("reader osr")]
    assert f"status {SID}: Permission denied" in lines
    for line in result.errors:
        _no_path(line)
    monkeypatch.undo()
    w.close()


CONFIG = """version = 1
[town]
title = "Kite field"

[live]
status_dir = "status"

[[building]]
id = "docks"
type = "harbour"
label = "Docks"
  [[building.source]]
  kind = "static"
  state = "ok"
"""


def test_livefeed_error_lines_carry_no_path(tmp_path, monkeypatch):
    from lamptown import build as B
    from lamptown.livefeed import Builder, FailLog, LiveFeed

    monkeypatch.setenv("AGENT_STATUS_DIR", str(tmp_path / "elsewhere"))
    cfg = tmp_path / "lamptown.toml"
    cfg.write_text(CONFIG, encoding="utf-8")
    logs: list[str] = []
    feed = LiveFeed(log=logs.append)
    with feed:
        b = Builder(str(cfg), str(tmp_path / "out"), logs.append, on_config=feed.configure)
        assert b() is not None

        def denied(*a, **k):
            raise PermissionError(13, "Permission denied", SECRET)

        monkeypatch.setattr(B, "write_live_files", denied)
        assert feed.tick(b.rev) is None
    assert "can't write the live files: Permission denied" in logs
    for line in logs:
        _no_path(line)

    tb_logs: list[str] = []
    fails = FailLog(tb_logs.append, "live tick")
    for _ in range(2):
        try:
            raise PermissionError(13, "Permission denied", SECRET)
        except PermissionError as e:
            fails.failed(e)
    assert "Traceback" in tb_logs[0] and 'test_release_fixes_eng.py", line' in tb_logs[0]
    assert "PermissionError: Permission denied" in tb_logs[0]
    assert tb_logs[1].endswith("PermissionError: Permission denied")
    for line in tb_logs:
        _no_path(line)
        assert str(Path(__file__).resolve().parent) not in line
