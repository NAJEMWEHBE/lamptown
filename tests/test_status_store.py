"""Atomic writer, merge rules and the ``agent_status()`` context manager."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from lamptown.status import (
    MAX_BYTES,
    StatusError,
    agent_status,
    default_dir,
    read_status,
    status_path,
    update_status,
    validate,
    write_status,
)
from lamptown.status.store import atomic_write_json, check_id, iso


def _doc(folder: Path, status_id: str) -> dict:
    return json.loads((folder / f"{status_id}.json").read_text(encoding="utf-8"))


def test_default_dir_order(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_STATUS_DIR", raising=False)
    assert default_dir() == Path("~/.agent-status").expanduser()
    monkeypatch.setenv("AGENT_STATUS_DIR", str(tmp_path / "env"))
    assert default_dir() == tmp_path / "env"
    assert default_dir(tmp_path / "given") == tmp_path / "given"


def test_update_creates_a_valid_file(tmp_path):
    doc = update_status("kite-run", tmp_path, message="Tying knots")
    on_disk = _doc(tmp_path, "kite-run")
    assert on_disk == doc
    assert validate(on_disk) == []
    assert (doc["title"], doc["state"], doc["spec"]) == ("kite-run", "working", "agent-status/1")
    assert list(doc)[:5] == ["spec", "id", "title", "state", "updated"]


def test_update_merges_and_none_removes(tmp_path):
    update_status("kite-run", tmp_path, title="Kite run", progress={"done": 1, "total": 4},
                  message="One")
    doc = update_status("kite-run", tmp_path, state="ok", progress=None)
    assert doc["title"] == "Kite run"
    assert doc["message"] == "One"
    assert "progress" not in doc
    assert doc["state"] == "ok"


def test_update_replaces_an_invalid_file(tmp_path):
    (tmp_path / "kite-run.json").write_text("{broken", encoding="utf-8")
    doc = update_status("kite-run", tmp_path, state="ok")
    assert validate(doc) == []


def test_update_rejects_bad_values(tmp_path):
    with pytest.raises(StatusError, match="state"):
        update_status("kite-run", tmp_path, state="building")
    with pytest.raises(StatusError, match="href"):
        update_status("kite-run", tmp_path, links=[{"label": "x", "href": "javascript:x"}])
    assert not (tmp_path / "kite-run.json").exists()


@pytest.mark.parametrize("bad", ["", "../up", "a/b", "a\\b", ".hidden", "CON", "nul.json",
                                 "x" * 129, "sp ace"])
def test_bad_ids(bad):
    with pytest.raises(StatusError):
        check_id(bad)


def test_status_path_never_leaves_the_folder(tmp_path):
    assert status_path("ok-id", tmp_path).parent == tmp_path
    with pytest.raises(StatusError):
        status_path("../../etc/passwd", tmp_path)


def test_atomic_write_leaves_no_temp_files(tmp_path):
    for i in range(5):
        update_status("busy", tmp_path, message=f"step {i}")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["busy.json"]


def test_size_limit_on_write_and_read(tmp_path):
    with pytest.raises(StatusError, match="limit"):
        atomic_write_json(tmp_path / "big.json", {"x-blob": "a" * MAX_BYTES})
    assert not list(tmp_path.iterdir())
    big = tmp_path / "big.json"
    big.write_text(json.dumps({"x": "a" * MAX_BYTES}), encoding="utf-8")
    with pytest.raises(StatusError, match="limit"):
        read_status(big)


def test_read_accepts_utf8_bom(tmp_path):
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"a": "café"}).encode("utf-8"))
    assert read_status(path) == {"a": "café"}


@pytest.mark.parametrize("content", [b"", b"[]", b"\xff\xfe\x00", b"{"])
def test_read_rejects_junk(tmp_path, content):
    path = tmp_path / "junk.json"
    path.write_bytes(content)
    with pytest.raises(StatusError):
        read_status(path)


def test_write_status_fills_spec_and_updated(tmp_path):
    path = write_status({"id": "w", "title": "W", "state": "ok"}, tmp_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["spec"] == "agent-status/1"
    assert validate(doc) == []


def test_iso_writes_z_and_keeps_offsets():
    from datetime import UTC, datetime, timedelta, timezone

    assert iso(datetime(2026, 11, 4, 10, 12, 0, 999, tzinfo=UTC)) == "2026-11-04T10:12:00Z"
    plus530 = timezone(timedelta(hours=5, minutes=30))
    assert iso(datetime(2026, 11, 4, 15, 42, tzinfo=plus530)) == "2026-11-04T15:42:00+05:30"


# ------------------------------------------------------------- agent_status()
def test_context_manager_ok(tmp_path):
    with agent_status("report", "Nightly report", directory=tmp_path, agent="cron") as s:
        assert _doc(tmp_path, "report")["state"] == "working"
        s.progress(2, 5)
        s.message("Halfway")
        assert _doc(tmp_path, "report")["progress"] == {"done": 2, "total": 5}
    doc = _doc(tmp_path, "report")
    assert (doc["state"], doc["message"], doc["agent"]) == ("ok", "Done", "cron")
    assert "started" in doc


def test_context_manager_error_reraises(tmp_path):
    with pytest.raises(RuntimeError), agent_status("report", directory=tmp_path):
        raise RuntimeError("tide too high")
    doc = _doc(tmp_path, "report")
    assert doc["state"] == "error"
    assert doc["message"] == "RuntimeError"  # review-fix B1: the type only, never its text


def test_context_manager_keyboard_interrupt(tmp_path):
    with pytest.raises(KeyboardInterrupt), agent_status("report", directory=tmp_path):
        raise KeyboardInterrupt
    assert _doc(tmp_path, "report")["message"] == "Interrupted"


def test_context_manager_keeps_a_waiting_state(tmp_path):
    with agent_status("report", directory=tmp_path) as s:
        s.need("Pick a colour", "https://example.com/colours")
    doc = _doc(tmp_path, "report")
    assert doc["state"] == "waiting"
    assert doc["needs_you"] == [{"text": "Pick a colour", "href": "https://example.com/colours"}]


def test_context_manager_clears_old_needs_and_progress(tmp_path):
    update_status("report", tmp_path, state="waiting", needs_you=[{"text": "old"}],
                  progress={"done": 1, "total": 2})
    with agent_status("report", directory=tmp_path):
        doc = _doc(tmp_path, "report")
        assert doc["needs_you"] == []
        assert "progress" not in doc


def test_context_manager_warns_instead_of_breaking_on_disk_errors(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with agent_status("report", directory=blocker / "sub") as s:
            s.message("still fine")
    assert caught, "a disk error should warn"


def test_context_manager_strict_raises_on_disk_errors(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(OSError), agent_status("report", directory=blocker / "sub", strict=True):
        pass
