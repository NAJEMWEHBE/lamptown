"""0.2.1 review fixes, batch B2: the Watcher (privacy rules changed, the hook race, close)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lamptown.status import events as ev
from lamptown.status import watch as watch_mod
from lamptown.status.collector import PRIVATE_NAME
from lamptown.status.readers import Batch
from lamptown.status.store import read_status, update_status
from lamptown.status.watch import Watcher

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC)
SID = "codex-3f9a2b"
SECRET = "Harbour deal"
PROJECT = "tide-ledger"


class FakeReader:
    name = "fake"

    def __init__(self):
        self.agents: list[dict] = []

    def poll(self, state, now):
        batch = Batch(agents=self.agents)
        self.agents = []
        return batch


def _logged(folder, now):
    return [e for _, es in ev.read_days(folder, 2, now=now, tz=UTC) for e in es]


def _secret_record(status, private, **extra):
    update_status(SID, status, now=T0, title=SECRET, state="working",
                  **{"x-folder": str(private / PROJECT), "x-project": PROJECT, **extra})


def _no_secret(status):
    for path in status.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".jsonl") and path.stem != SID:
            text = path.read_text(encoding="utf-8")
            assert SECRET not in text, f"private title in {path.name}"
            assert PROJECT not in text, f"private project in {path.name}"


# ---------------------------------------------------------------- 1: privacy rules changed
def test_a_folder_made_private_after_a_restart_is_masked_in_time_based_events(tmp_path):
    status, private = tmp_path / "status", tmp_path / "hidden-work"
    _secret_record(status, private)
    with Watcher(status, [], stale_after=60, tz=UTC) as w:  # not private yet
        w.tick(T0)
        w.tick(T0 + timedelta(seconds=3))
    later = T0 + timedelta(minutes=5)
    with Watcher(status, [], stale_after=60, private_dirs=[private], tz=UTC) as w:
        result = w.tick(later)  # the record went silent: a "fail" event, from the snapshot
    fails = [e for e in result.events if e["k"] == "fail"]
    assert fails, result.events
    for event in result.events:
        assert event["title"] == PRIVATE_NAME and event["id"].startswith("private-")
        assert event.get("project", "") == ""
    _no_secret(status)
    assert all(e["title"] == PRIVATE_NAME for e in _logged(status, later) if e["k"] == "fail")


def test_an_open_files_group_is_masked_when_its_folder_turns_private(tmp_path):
    status, private = tmp_path / "status", tmp_path / "hidden-work"
    _secret_record(status, private)
    w = Watcher(status, [], tz=UTC)
    w.tick(T0)
    update_status(SID, status, now=T0 + timedelta(seconds=10), **{"x-files": 3})
    assert w.tick(T0 + timedelta(seconds=20)).events == []  # the minute isn't over: pending
    w.close()
    w = Watcher(status, [], private_dirs=[private], tz=UTC)
    out = w.tick(T0 + timedelta(seconds=90))
    w.close()
    files = [e for e in out.events if e["k"] == "files"]
    assert len(files) == 1 and files[0]["n"] == 3
    assert files[0]["title"] == PRIVATE_NAME and files[0]["id"].startswith("private-")
    _no_secret(status)


def test_a_folder_made_public_again_gets_its_names_back(tmp_path):
    status, private = tmp_path / "status", tmp_path / "hidden-work"
    _secret_record(status, private)
    with Watcher(status, [], private_dirs=[private], tz=UTC) as w:
        w.tick(T0)
    with Watcher(status, [], tz=UTC) as w:
        w.tick(T0 + timedelta(seconds=3))
        update_status(SID, status, now=T0 + timedelta(seconds=4), state="waiting")
        out = w.tick(T0 + timedelta(seconds=6)).events
    (event,) = [e for e in out if e["k"] == "wait"]
    assert event["title"] == SECRET and event["id"] == SID and event["project"] == PROJECT


def test_unchanged_privacy_rules_still_only_stat_unchanged_files(tmp_path, monkeypatch):
    status, private = tmp_path / "status", tmp_path / "hidden-work"
    _secret_record(status, private)
    with Watcher(status, [], private_dirs=[private], tz=UTC) as w:
        w.tick(T0)
    reads = []
    real = watch_mod.read_status
    monkeypatch.setattr(watch_mod, "read_status", lambda p: reads.append(p) or real(p))
    with Watcher(status, [], private_dirs=[private], tz=UTC) as w:
        w.tick(T0 + timedelta(seconds=3))
        w.tick(T0 + timedelta(seconds=6))
    assert reads == []


# ---------------------------------------------------------------- 3: the hook race
def test_a_hook_write_between_the_watchers_read_and_write_is_kept(tmp_path, monkeypatch):
    update_status(SID, tmp_path, now=T0, title="Fix save slots", state="working")
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    real = watch_mod.read_existing
    fired = []

    def then_hook(path, **kw):
        doc = real(path, **kw)
        if not fired:  # a hook writes "waiting" right after the Watcher read the record
            fired.append(1)
            update_status(SID, tmp_path, now=T0 + timedelta(seconds=10), state="waiting")
        return doc

    monkeypatch.setattr(watch_mod, "read_existing", then_hook)
    reader.agents = [{"id": SID, "at": T0 + timedelta(seconds=5), "state": "working",
                      "x-files": 2}]
    result = w.tick(T0 + timedelta(seconds=12))
    w.close()
    doc = read_status(tmp_path / f"{SID}.json")
    assert fired
    assert doc["state"] == "waiting", "the older reader change overwrote the hook's state"
    assert doc["updated"] == "2026-11-04T09:00:10Z"
    assert doc["x-files"] == 2  # counters still merge in
    assert SID in result.kept


def test_a_record_that_keeps_changing_is_tried_again_next_tick(tmp_path, monkeypatch):
    update_status(SID, tmp_path, now=T0, title="Fix save slots", state="working")
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    w.tick(T0)
    real = watch_mod.read_existing
    n = {"i": 0}

    def churn(path, **kw):
        doc = real(path, **kw)
        n["i"] += 1  # someone rewrites it after every read (a counter only: no state change)
        update_status(SID, tmp_path, now=T0, **{"x-commits": n["i"]})
        return doc

    monkeypatch.setattr(watch_mod, "read_existing", churn)
    reader.agents = [{"id": SID, "at": T0 + timedelta(seconds=5), "state": "waiting"}]
    first = w.tick(T0 + timedelta(seconds=6))
    assert first.retry == [SID] and SID not in first.written
    monkeypatch.setattr(watch_mod, "read_existing", real)
    second = w.tick(T0 + timedelta(seconds=9))
    w.close()
    assert SID in second.written
    assert read_status(tmp_path / f"{SID}.json")["state"] == "waiting"


def test_a_reader_title_with_a_lone_surrogate_is_still_known_as_the_watchers_own(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], tz=UTC)
    reader.agents = [{"id": SID, "at": T0, "title": "Kite \ud83d", "state": "working",
                      "message": "Half \udc00", "x-folder": "/work/kite-\ud83d",
                      "x-project": "kite-\ud83d", "x-branch": "main-\ud83d"}]
    assert w.tick(T0).written == [SID]
    text = (tmp_path / f"{SID}.json").read_text(encoding="utf-8")
    assert "\\ud83d" not in text and "\\udc00" not in text
    doc = read_status(tmp_path / f"{SID}.json")
    assert w._wrote[SID] == watch_mod._digest(doc)  # a later same-second change is its own
    w.close()


# ---------------------------------------------------------------- 6: close() in a with block
def test_a_failed_save_on_exit_never_hides_the_with_bodys_error(tmp_path, monkeypatch):
    def denied(*a, **k):
        raise PermissionError(13, "Permission denied")

    with pytest.raises(RuntimeError, match="the body broke"), \
            Watcher(tmp_path, [], tz=UTC) as w:
        w.tick(T0)
        monkeypatch.setattr(watch_mod, "atomic_write_json", denied)
        update_status(SID, tmp_path, now=T0 + timedelta(seconds=1), title="Fix")
        tick = w.tick(T0 + timedelta(seconds=3))  # logs a start; its save fails: close() retries
        assert tick.events and any(e.startswith("watcher state") for e in tick.errors)
        raise RuntimeError("the body broke")
    assert w._fd is None  # the folder was still let go


def test_a_failed_save_on_a_clean_exit_still_raises(tmp_path, monkeypatch):
    def denied(*a, **k):
        raise PermissionError(13, "Permission denied")

    with pytest.raises(PermissionError), Watcher(tmp_path, [], tz=UTC) as w:
        w.tick(T0)
        monkeypatch.setattr(watch_mod, "atomic_write_json", denied)
        update_status(SID, tmp_path, now=T0 + timedelta(seconds=1), title="Fix")
        w.tick(T0 + timedelta(seconds=3))
    assert w._fd is None
