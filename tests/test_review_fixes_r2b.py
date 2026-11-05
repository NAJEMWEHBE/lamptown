"""0.2.1 review fixes, round 2, batch R2b: the Watcher's privacy transition and the hook race."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta

from lamptown.build import build
from lamptown.config import parse_config
from lamptown.status import events as ev
from lamptown.status import store
from lamptown.status import watch as watch_mod
from lamptown.status.collector import PRIVATE_NAME
from lamptown.status.readers import Batch
from lamptown.status.store import read_status, update_status
from lamptown.status.watch import Watcher

T0 = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
SID = "kite"
TITLE = "QZ_PRIVATE_SESSION_CANARY"
PROJECT = "QZ_PRIVATE_PROJECT_CANARY"


class FakeReader:
    name = "r2b-fake"

    def __init__(self):
        self.agents: list[dict] = []
        self.events: list[dict] = []

    def poll(self, state, now):
        batch = Batch(agents=self.agents, events=self.events)
        self.agents, self.events = [], []
        return batch


def _log_text(status):
    folder = ev.events_dir(status)
    return "".join(p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.jsonl")))


def _logged(status, now):
    return [e for _, es in ev.read_days(status, 2, now=now, tz=UTC) for e in es]


def _public_history(status, private):
    """A record logged while public (start, then wait), then its folder made private."""
    update_status(SID, status, now=T0, title=TITLE, state="working", agent="codex",
                  **{"x-folder": str(private / "secret-project"), "x-project": PROJECT})
    with Watcher(status, [], tz=UTC) as w:
        w.tick(T0)
        update_status(SID, status, now=T0 + timedelta(seconds=1), state="waiting")
        w.tick(T0 + timedelta(seconds=2))
    text = _log_text(status)
    assert TITLE in text and PROJECT in text  # the setup: it was logged by name


def _config(root, status, private):
    text = (f"[live]\nstatus_dir = '{status.as_posix()}'\n"
            f"private_dirs = ['{private.as_posix()}']\n")
    return parse_config(text, root / "lamptown.toml")


# ---------------------------------------------------------------- 1: folder made private
def test_a_folder_made_private_rewrites_its_logged_events(tmp_path):
    status, private = tmp_path / "status", tmp_path / "hidden"
    _public_history(status, private)
    before = [e for e in _logged(status, T0) if e["id"] == SID]
    with Watcher(status, [], private_dirs=[private], tz=UTC) as w:
        result = w.tick(T0 + timedelta(seconds=3))
        masked_id = w.private_id(SID)
    assert not result.errors
    text = _log_text(status)
    assert TITLE not in text and PROJECT not in text
    after = _logged(status, T0)
    assert not [e for e in after if e["id"] == SID]
    mine = [e for e in after if e["id"] == masked_id]
    # every event of the record is still there (same kinds and times), only masked
    assert [(e["t"], e["k"]) for e in mine] == [(e["t"], e["k"]) for e in before
                                               if e["k"] != "project"]
    assert {e["title"] for e in mine} == {PRIVATE_NAME} and {e["project"] for e in mine} == {""}
    # nothing else on disk names it either (the record itself is the owner's own file)
    for path in status.rglob("*"):
        if path.is_file() and path.name != f"{SID}.json":
            body = path.read_text(encoding="utf-8")
            assert TITLE not in body and PROJECT not in body, path.name


def test_a_fresh_build_after_a_folder_made_private_has_no_private_name_in_any_page(tmp_path):
    """The Codex probe (history_transition.py), town-history.js included."""
    status, private = tmp_path / "status", tmp_path / "hidden"
    _public_history(status, private)
    with Watcher(status, [], private_dirs=[private], tz=UTC) as w:
        w.tick(T0 + timedelta(seconds=3))
    out = tmp_path / "new-output"
    build(_config(tmp_path, status, private), out_dir=out, now=T0 + timedelta(seconds=4), tz=UTC)
    pages = sorted(p.name for p in out.iterdir() if p.is_file())
    assert "town-history.js" in pages and "town-live.js" in pages
    reached = [p.name for p in out.iterdir() if p.is_file() and p.suffix in (".js", ".html")
               and (TITLE in (b := p.read_text(encoding="utf-8")) or PROJECT in b)]
    assert reached == []


def test_a_build_before_any_watcher_tick_under_the_new_rules_masks_the_history(tmp_path):
    """The folder was made private in the config and a build ran before the Watcher did."""
    status, private = tmp_path / "status", tmp_path / "hidden"
    _public_history(status, private)
    out = tmp_path / "out"
    build(_config(tmp_path, status, private), out_dir=out, now=T0 + timedelta(seconds=4), tz=UTC)
    history = (out / "town-history.js").read_text(encoding="utf-8")
    assert TITLE not in history and PROJECT not in history
    assert PRIVATE_NAME.upper() in history  # its events are still in the book, masked


def test_a_reader_event_with_no_record_is_masked_once_a_folder_is_made_private(tmp_path):
    status, private = tmp_path / "status", tmp_path / "hidden"
    status.mkdir()
    reader = FakeReader()
    with Watcher(status, [reader], tz=UTC) as w:
        w.tick(T0)
        reader.events = [{"t": T0 + timedelta(seconds=1), "id": "repo-x", "k": "commit", "n": 1,
                          "title": TITLE, "project": PROJECT,
                          "folder": str(private / "repo-x")}]
        w.tick(T0 + timedelta(seconds=2))
    assert TITLE in _log_text(status)
    with Watcher(status, [], private_dirs=[private], tz=UTC) as w:
        w.tick(T0 + timedelta(seconds=3))  # no record to check it against: hidden
    text = _log_text(status)
    assert TITLE not in text and PROJECT not in text
    assert [e["k"] for e in _logged(status, T0) if e["id"].startswith("private-")] == ["commit"]


def test_making_a_folder_public_again_keeps_public_history_by_name(tmp_path):
    """Only a rule that hides more masks what can't be checked: taking a folder out of the
    private ones never masks an unrelated public event."""
    status, private, other = tmp_path / "status", tmp_path / "hidden", tmp_path / "other"
    status.mkdir()
    reader = FakeReader()
    with Watcher(status, [reader], private_dirs=[private, other], tz=UTC) as w:
        w.tick(T0)
        reader.events = [{"t": T0 + timedelta(seconds=1), "id": "repo-y", "k": "commit", "n": 1,
                          "title": "Public work", "folder": str(tmp_path / "public" / "y")}]
        w.tick(T0 + timedelta(seconds=2))
    with Watcher(status, [], private_dirs=[private], tz=UTC) as w:  # "other" made public
        w.tick(T0 + timedelta(seconds=3))
    assert [e["title"] for e in _logged(status, T0) if e["id"] == "repo-y"] == ["Public work"]


def test_the_rewrite_is_done_once_and_a_failed_rewrite_is_tried_again(tmp_path, monkeypatch):
    status, private = tmp_path / "status", tmp_path / "hidden"
    _public_history(status, private)
    real = store.atomic_write_bytes
    calls = []

    def broken(path, payload):
        calls.append(path)
        raise PermissionError(13, "held open")

    monkeypatch.setattr(watch_mod, "atomic_write_bytes", broken)
    with Watcher(status, [], private_dirs=[private], tz=UTC) as w:
        failed = w.tick(T0 + timedelta(seconds=3))
        assert calls and any("event log" in e for e in failed.errors)
        monkeypatch.setattr(watch_mod, "atomic_write_bytes", real)
        w.tick(T0 + timedelta(seconds=4))  # rolled back: the next tick rewrites it
        assert TITLE not in _log_text(status)
        calls.clear()
        monkeypatch.setattr(watch_mod, "atomic_write_bytes", broken)
        later = w.tick(T0 + timedelta(seconds=5))  # rules unchanged now: no rewrite
    assert not calls and not later.errors


# ---------------------------------------------------------------- 2: the hook race
class _Once:
    name = "race"

    def __init__(self):
        self.agents: list[dict] = []

    def poll(self, state, now):
        out, self.agents = self.agents, []
        return Batch(agents=out)


def test_a_hook_landing_between_the_watchers_last_read_and_its_write_is_kept(tmp_path,
                                                                             monkeypatch):
    """The Codex probe (watcher_race.py), with the hook on its own thread as in real life: it
    starts right after the Watcher's final read, before its replace (the old window)."""
    monkeypatch.setattr(store, "LOCK_WAIT", 30.0)  # the race, not the timeout, is tested here
    folder = tmp_path
    update_status("kite", folder, now=T0, title="Kite", state="working")
    reader = _Once()
    hook_done = threading.Event()
    started = threading.Event()

    def hook():
        started.set()
        update_status("kite", folder, now=T0 + timedelta(seconds=10), state="waiting",
                      needs_you=[{"text": "Allow Bash?"}])
        hook_done.set()

    real = watch_mod.atomic_write_json
    threads = []

    def interleave(path, doc, **kwargs):
        if os.path.basename(path) == "kite.json" and not threads:
            t = threading.Thread(target=hook)
            threads.append(t)
            t.start()
            started.wait(5)
            hook_done.wait(1.0)  # without the lock it lands here, inside the window
        return real(path, doc, **kwargs)

    with Watcher(folder, [reader], tz=UTC) as watcher:
        watcher.tick(T0)
        monkeypatch.setattr(watch_mod, "atomic_write_json", interleave)
        reader.agents = [{"id": "kite", "at": T0 + timedelta(seconds=5), "state": "working",
                          "x-files": 2}]
        result = watcher.tick(T0 + timedelta(seconds=12))
        monkeypatch.setattr(watch_mod, "atomic_write_json", real)
        for t in threads:
            t.join(10)
    assert threads and hook_done.is_set()
    doc = read_status(folder / "kite.json")
    assert doc["state"] == "waiting"
    assert doc["needs_you"] == [{"text": "Allow Bash?"}]
    assert doc.get("x-files") == 2  # the Watcher's counter, written first, is merged in
    assert not result.errors
    assert not list(folder.glob(".*.lock"))


def test_a_held_lock_delays_a_hook_about_half_a_second_then_it_writes_anyway(tmp_path, caplog):
    assert store.LOCK_WAIT <= 0.5 and store.LOCK_STALE >= 1.5
    lock = store.lock_path(store.status_path("kite", tmp_path))
    lock.write_text("someone\n", encoding="utf-8")  # fresh: another writer holds it
    start = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="lamptown.status"):
        doc = update_status("kite", tmp_path, now=T0, title="Kite", state="waiting")
    took = time.monotonic() - start
    assert store.LOCK_WAIT * 0.8 <= took < 3.0  # waited the short while, never for long
    assert read_status(tmp_path / "kite.json") == doc
    assert any("lock" in r.getMessage() for r in caplog.records)
    assert all(str(tmp_path) not in r.getMessage() for r in caplog.records)  # no path
    assert lock.read_text(encoding="utf-8") == "someone\n"  # not ours: left alone


def test_a_stale_lock_is_broken_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "LOCK_WAIT", 30.0)  # waiting it out would take 30 s
    lock = store.lock_path(store.status_path("kite", tmp_path))
    lock.write_text("crashed\n", encoding="utf-8")
    old = time.time() - 10
    os.utime(lock, (old, old))
    start = time.monotonic()
    update_status("kite", tmp_path, now=T0, title="Kite")
    assert time.monotonic() - start < 10
    assert not lock.exists()


def test_the_lock_file_is_hidden_and_gone_after_a_write(tmp_path):
    update_status("kite", tmp_path, now=T0, title="Kite")
    lock = store.lock_path(store.status_path("kite", tmp_path))
    assert lock.name.startswith(".") and lock.parent == tmp_path and not lock.exists()
    with store.record_lock(tmp_path / "kite.json") as got:
        assert got and lock.exists()
        with Watcher(tmp_path, [], tz=UTC) as w:
            w.tick(T0)
            w.tick(T0 + timedelta(seconds=3))
            assert "kite" in (w.state()["folder"] or {})
            assert all(not k.startswith(".") for k in w.state()["folder"])
    assert not lock.exists()


def test_the_watcher_writes_anyway_when_the_lock_stays_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "LOCK_WAIT", 0.05)
    update_status("kite", tmp_path, now=T0, title="Kite", state="working")
    reader = _Once()
    with Watcher(tmp_path, [reader], tz=UTC) as w:
        w.tick(T0)
        lock = store.lock_path(tmp_path / "kite.json")
        lock.write_text("held\n", encoding="utf-8")
        reader.agents = [{"id": "kite", "at": T0 + timedelta(seconds=5), "state": "ok"}]
        result = w.tick(T0 + timedelta(seconds=6))
    assert "kite" in result.written
    assert read_status(tmp_path / "kite.json")["state"] == "ok"
    assert any("lock" in e for e in result.errors)


# ---------------------------------------------------------------- round 2 review: N1
def _pages_with(out, needle):
    return sorted(p.name for p in out.iterdir() if p.is_file() and needle in p.read_text("utf-8"))


def test_a_build_before_the_watcher_masks_an_agent_whose_record_is_gone(tmp_path):
    """A session logged while public, its record tidied away, then its folder made private: a
    build before the Watcher's next tick masks it too (nothing says where it ran)."""
    status, private = tmp_path / "status", tmp_path / "hidden"
    _public_history(status, private)
    os.remove(status / f"{SID}.json")
    out = tmp_path / "out"
    build(_config(tmp_path, status, private), out_dir=out, now=T0 + timedelta(seconds=4), tz=UTC)
    assert _pages_with(out, TITLE) == [] and _pages_with(out, PROJECT) == []


def test_once_the_watcher_has_masked_a_public_agent_with_no_record_stays_named(tmp_path):
    """After the Watcher masked the log for the private folder, an agent logged later in a
    public folder keeps its name in history even once its record is gone."""
    status, private, public = tmp_path / "status", tmp_path / "hidden", tmp_path / "open"
    name = "QZ_PUBLIC_GULL_TITLE"
    with Watcher(status, [], private_dirs=[private], tz=UTC) as w:
        w.tick(T0)
        update_status("gull", status, now=T0 + timedelta(seconds=1), title=name,
                      state="working", agent="codex", **{"x-folder": str(public / "fine")})
        w.tick(T0 + timedelta(seconds=2))
    assert not watch_mod.remask_pending(status, watch_mod.Watcher(
        status, [], private_dirs=[private], tz=UTC).private_roots)
    os.remove(status / "gull.json")
    out = tmp_path / "out"
    build(_config(tmp_path, status, private), out_dir=out, now=T0 + timedelta(seconds=4), tz=UTC)
    assert "town-history.js" in _pages_with(out, name)
