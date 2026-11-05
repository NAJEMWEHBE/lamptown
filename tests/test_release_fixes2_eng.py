"""Fix round 2 (engine side): a change put off is never lost on close, a clock jump removes
nothing, a reader whose data raises fails alone, a Watcher's private folders reach its readers,
and the CLI's error lines name no folder. Every name and path is made up (``tmp_path``)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lamptown import cli
from lamptown.status import events as ev
from lamptown.status import store
from lamptown.status import watch as watch_mod
from lamptown.status.readers import Batch
from lamptown.status.readers.ci_gh import CiGhReader
from lamptown.status.readers.git import GitReader
from lamptown.status.store import StatusError, read_status, update_status
from lamptown.status.watch import Watcher
from test_status_reader_ci_gh import NOW, FakeRepo, Runner, ok, run

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC)


class OnceReader:
    """Hands out each change once (its saved state is how far it got, like a log cursor)."""

    name = "once"

    def __init__(self) -> None:
        self.log: list[dict] = []

    def poll(self, state, now):
        pos = state.get("pos", 0)
        state["pos"] = len(self.log)
        return Batch(agents=self.log[pos:])


def _kinds(folder: Path, now: datetime) -> list[str]:
    return [e["k"] for _, es in ev.read_days(folder, 2, now=now, tz=UTC) for e in es]


def _busy(monkeypatch, stem: str) -> dict:
    """``read_status`` fails (held open) for ``stem`` while ``on`` is set."""
    real = store.read_status
    flag = {"on": True}

    def fake(path):
        if flag["on"] and Path(path).stem == stem:
            raise StatusError("held open") from PermissionError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(store, "read_status", fake)
    monkeypatch.setattr(watch_mod, "read_status", fake)
    monkeypatch.setattr(store, "READ_WAIT", 0.0, raising=False)
    return flag


def _working_then_held(tmp_path, monkeypatch) -> tuple[Watcher, OnceReader, dict]:
    r = OnceReader()
    w = Watcher(tmp_path, [r], tz=UTC)
    w.tick(T0)
    r.log.append({"id": "agent-1", "at": T0, "title": "One", "state": "working"})
    w.tick(T0 + timedelta(seconds=3))
    flag = _busy(monkeypatch, "agent-1")
    r.log.append({"id": "agent-1", "at": T0 + timedelta(seconds=5), "state": "waiting",
                  "message": "Pick one"})
    res = w.tick(T0 + timedelta(seconds=6))
    assert res.retry == ["agent-1"]
    return w, r, flag


def _restart(folder: Path, r: OnceReader) -> None:
    with Watcher(folder, [r], tz=UTC) as w2:
        for i in range(3):
            w2.tick(T0 + timedelta(seconds=9 + 3 * i))


# ---------------------------------------------------------------- ENG 2: held changes on close
def test_close_before_the_retry_keeps_the_waiting_state_and_its_event(tmp_path, monkeypatch):
    """The reviewer's probe: the record is busy, the change is put off, then the Watcher closes
    (Ctrl+C, app quit) once the file reads again. The change and its `wait` event survive."""
    w, r, flag = _working_then_held(tmp_path, monkeypatch)
    flag["on"] = False
    w.close()
    doc = read_status(tmp_path / "agent-1.json")
    assert doc["state"] == "waiting" and doc["title"] == "One"
    _restart(tmp_path, r)
    assert _kinds(tmp_path, T0 + timedelta(minutes=1)).count("wait") == 1


def test_close_while_the_record_is_still_busy_still_writes_the_change(tmp_path, monkeypatch):
    w, r, flag = _working_then_held(tmp_path, monkeypatch)
    w.close()  # forced, as after HOLD_TICKS: the change is never dropped
    flag["on"] = False
    assert read_status(tmp_path / "agent-1.json")["state"] == "waiting"
    _restart(tmp_path, r)
    assert "wait" in _kinds(tmp_path, T0 + timedelta(minutes=1))


def test_close_applies_a_held_change_at_the_run_s_time_not_an_older_clock(tmp_path,
                                                                           monkeypatch):
    """Ticks given a time ahead of the PC's clock (``--now``, tests): the change put off still
    wins over its own record on close."""
    ahead = datetime(2099, 1, 1, tzinfo=UTC)
    r = OnceReader()
    w = Watcher(tmp_path, [r], tz=UTC)
    w.tick(ahead)
    r.log.append({"id": "agent-5", "at": ahead, "title": "Five", "state": "working"})
    w.tick(ahead + timedelta(seconds=3))
    flag = _busy(monkeypatch, "agent-5")
    r.log.append({"id": "agent-5", "at": ahead + timedelta(seconds=5), "state": "waiting"})
    assert w.tick(ahead + timedelta(seconds=6)).retry == ["agent-5"]
    flag["on"] = False
    w.close()
    assert read_status(tmp_path / "agent-5.json")["state"] == "waiting"


def test_ctrl_c_during_a_held_retry_keeps_the_change_for_close(tmp_path, monkeypatch):
    w, r, flag = _working_then_held(tmp_path, monkeypatch)
    flag["on"] = False
    real_apply = w._apply

    def interrupted(change, now, result, tries=0):
        if tries:
            raise KeyboardInterrupt
        return real_apply(change, now, result, tries=tries)

    w._apply = interrupted
    with pytest.raises(KeyboardInterrupt):
        w.tick(T0 + timedelta(seconds=9))
    w._apply = real_apply
    assert "agent-1" in w._held, "a held change left the queue before it was applied"
    w.close()
    assert read_status(tmp_path / "agent-1.json")["state"] == "waiting"


# ---------------------------------------------------------------- ENG 4: clock jumps
def test_a_clock_jump_removes_no_fresh_record(tmp_path):
    """The reviewer's probe: one tick 20 days ahead, then back: the waiting record stays."""
    r = OnceReader()
    w = Watcher(tmp_path, [r], tz=UTC)
    w.tick(T0)
    r.log.append({"id": "agent-2", "at": T0, "title": "Two", "state": "waiting",
                  "message": "Pick one"})
    w.tick(T0 + timedelta(seconds=3))
    w.tick(T0 + timedelta(days=20))
    w.tick(T0 + timedelta(minutes=1))
    w.close()
    assert (tmp_path / "agent-2.json").exists()


def test_an_own_old_record_goes_on_the_tick_after_a_long_gap(tmp_path):
    r = OnceReader()
    with Watcher(tmp_path, [r], tz=UTC) as w:
        w.tick(T0)
        r.log.append({"id": "agent-3", "at": T0, "title": "Three", "state": "ok"})
        w.tick(T0 + timedelta(seconds=3))
        later = T0 + timedelta(days=15)
        w.tick(later)  # the gap: not yet
        assert (tmp_path / "agent-3.json").exists()
        w.tick(later + timedelta(seconds=3))  # the time holds: now
    assert not (tmp_path / "agent-3.json").exists()


# ---------------------------------------------------------------- ENG 5: reader data that raises
class BadBatch:
    name = "bad-batch"

    def poll(self, state, now):
        def agents():
            raise RuntimeError("broken batch")
            yield {}

        return Batch(agents=agents())


class BadBehind:
    name = "bad-behind"

    @property
    def behind(self):
        raise RuntimeError("broken behind")

    def poll(self, state, now):
        return Batch()


class Steady:
    name = "steady"

    def __init__(self) -> None:
        self.n = 0

    def poll(self, state, now):
        self.n += 1
        return Batch(agents=[{"id": "agent-4", "at": now, "title": "Four", "state": "working",
                              "x-files": self.n}])


def test_a_reader_whose_data_raises_fails_alone(tmp_path):
    steady = Steady()
    with Watcher(tmp_path, [BadBatch(), BadBehind(), steady], tz=UTC) as w:
        w.tick(T0)
        for i in (1, 2):
            res = w.tick(T0 + timedelta(seconds=3 * i))
            assert "agent-4" in res.written
            assert any(e.startswith("reader bad-batch: RuntimeError") for e in res.errors)
            assert any(e.startswith("reader bad-behind: RuntimeError") for e in res.errors)
    assert read_status(tmp_path / "agent-4.json")["x-files"] >= 2


# ---------------------------------------------------------------- PRIV 5: readers get private_dirs
def test_a_watcher_built_in_python_hands_its_private_folders_to_its_readers(tmp_path):
    private = tmp_path / "clients"
    repo = FakeRepo(private, name="hidden-kite")
    repo.push(NOW - timedelta(minutes=4))
    status = tmp_path / "status"
    update_status("claude-code-you1", status, now=NOW - timedelta(minutes=5), title="Private",
                  state="working", agent="claude-code", **{"x-folder": str(repo.root)})
    gh = Runner(ok(run()))
    ci = CiGhReader(repos=[str(repo.root)], status_dir=str(status), runner=gh, background=False)
    git = GitReader(repos=[str(repo.root)], status_dir=str(status))
    with Watcher(status, [ci, git], private_dirs=[str(private)]) as w:
        for i in range(3):
            w.tick(NOW + timedelta(seconds=i))
    assert gh.calls == [] and ci.calls == 0, "a private repository was asked about"
    assert ci.repos == [] and git.repos == []
    assert git.status_dir is not None  # the status folder itself is not private


def test_a_private_status_folder_is_dropped_from_the_readers(tmp_path):
    status = tmp_path / "clients" / "status"
    git = GitReader(status_dir=str(status))
    Watcher(status, [git], private_dirs=[str(tmp_path / "clients")]).close()
    assert git.status_dir is None


# ---------------------------------------------------------------- cli error lines
def test_cli_config_error_names_the_file_not_its_folder(tmp_path, capsys):
    cfg = tmp_path / "you-secret" / "lamptown.toml"
    cfg.parent.mkdir()
    cfg.write_text("version = 1\n[[building]]\nid = 'Bad Id!'\n", encoding="utf-8")
    assert cli.main(["build", "--config", str(cfg), "--quiet"]) == cli.EXIT_CONFIG
    err = capsys.readouterr().err
    assert "config problem: lamptown.toml: " in err and "building" in err
    assert "you-secret" not in err and str(tmp_path) not in err
    assert cli.main(["watch", "--config", str(cfg)]) == cli.EXIT_CONFIG
    err = capsys.readouterr().err
    assert "lamptown.toml: " in err and "you-secret" not in err


def test_cli_missing_config_and_write_errors_name_no_folder(tmp_path, capsys):
    missing = tmp_path / "you-secret" / "nope.toml"
    assert cli.main(["build", "--config", str(missing)]) == cli.EXIT_CONFIG
    err = capsys.readouterr().err
    assert "config problem: " in err and "you-secret" not in err
    blocker = tmp_path / "you-secret-out"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    assert cli.main(["build", "--out", str(blocker / "town"), "--quiet"]) == cli.EXIT_WRITE
    err = capsys.readouterr().err
    assert "can't write the town: " in err and "you-secret" not in err


def test_reason_keeps_short_messages():
    assert cli._reason(PermissionError(13, "Permission denied", "C:\\Users\\you\\x")) == \
        "Permission denied"
    assert cli._reason(ValueError("lamptown.toml: town: unknown key")) == \
        "lamptown.toml: town: unknown key"
    assert cli._reason(ValueError("/work/you/lamptown.toml: town: unknown key")) == \
        "lamptown.toml: town: unknown key"
