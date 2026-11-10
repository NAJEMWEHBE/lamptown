"""The ``status_dir`` collector: stale rule, link allow-list, room shapes, odd files."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from lamptown.status.collector import collect, safe_href

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)


def _iso(delta_seconds: int) -> str:
    return (NOW - timedelta(seconds=delta_seconds)).isoformat().replace("+00:00", "Z")


def _put(folder: Path, status_id: str, *, age: int = 60, **fields) -> Path:
    doc = {"spec": "agent-status/1", "id": status_id, "title": status_id.title(),
           "state": "ok", "updated": _iso(age), **fields}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{status_id}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _rows(out: dict) -> dict[str, dict]:
    return {row["id"]: row for row in out["rows"]}


def test_missing_folder(tmp_path):
    out = collect({"kind": "status_dir", "dir": str(tmp_path / "nope")}, NOW)
    assert out["state"] == "missing"
    assert out["rows"] == []


def test_empty_folder_is_quiet_ok(tmp_path):
    out = collect({"dir": str(tmp_path)}, NOW)
    assert (out["state"], out["rows"]) == ("ok", [])


def test_default_folder_comes_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_STATUS_DIR", str(tmp_path))
    _put(tmp_path, "wren", state="working", agent="codex")
    out = collect({"kind": "status_dir", "_room": "harbour"}, NOW)
    assert _rows(out)["wren"]["busy"] is True


def test_stale_working_reads_as_error(tmp_path):
    _put(tmp_path, "fresh", state="working", age=100, stale_after=600)
    _put(tmp_path, "old", state="working", age=700, stale_after=600, message="Running tests")
    _put(tmp_path, "default-old", state="working", age=1801)
    _put(tmp_path, "never", state="working", age=99999, stale_after=0)
    _put(tmp_path, "waiting-old", state="waiting", age=99999)
    rows = _rows(collect({"dir": str(tmp_path)}, NOW))
    assert rows["fresh"]["state"] == "working"
    assert rows["old"]["state"] == "error"
    assert rows["old"]["message"] == "Stopped responding (last: Running tests)"
    assert rows["old"]["busy"] is False
    assert rows["default-old"]["state"] == "error"
    assert rows["never"]["state"] == "working"
    assert rows["waiting-old"]["state"] == "waiting"


def test_source_level_stale_after(tmp_path):
    _put(tmp_path, "w", state="working", age=120)
    assert _rows(collect({"dir": str(tmp_path), "stale_after": 60}, NOW))["w"]["state"] == "error"


def test_old_ok_files_are_hidden(tmp_path):
    _put(tmp_path, "recent", age=3600)
    _put(tmp_path, "ancient", age=3 * 86400)
    _put(tmp_path, "ancient-error", state="error", age=3 * 86400)
    rows = _rows(collect({"dir": str(tmp_path)}, NOW))
    assert set(rows) == {"recent", "ancient-error"}
    rows = _rows(collect({"dir": str(tmp_path), "hide_after": 0}, NOW))
    assert "ancient" in rows


def test_link_scheme_allow_list(tmp_path):
    _put(tmp_path, "links", links=[
        {"label": "PR", "href": "https://example.com/pr/1"},
        {"label": "Folder", "href": "file:///home/you/code/paper-kite"},
    ], needs_you=[{"text": "Look", "href": "http://example.com/look"}])
    row = _rows(collect({"dir": str(tmp_path)}, NOW))["links"]
    assert [link["href"] for link in row["links"]] == [
        "https://example.com/pr/1", "file:///home/you/code/paper-kite"]


def test_bad_link_scheme_makes_the_file_invalid(tmp_path):
    _put(tmp_path, "evil", links=[{"label": "x", "href": "javascript:alert(1)"}])
    out = collect({"dir": str(tmp_path)}, NOW)
    assert out["state"] == "error"
    assert "javascript" not in json.dumps(out)


def test_safe_href():
    assert safe_href("HTTPS://example.com/Kite") == "https://example.com/Kite"  # scheme only
    assert safe_href(" File:///tmp/kite.md ") == "file:///tmp/kite.md"
    for bad in ("javascript:alert(1)", "data:text/html,x", "vbscript:x", "//example.com", 7, None):
        assert safe_href(bad) is None


def test_needs_feed_the_town_sign(tmp_path):
    _put(tmp_path, "otter", state="waiting", title="Otter",
         needs_you=[{"text": "Approve the plan", "href": "https://example.com/pr/12"},
                    {"text": "Pick a name"}])
    out = collect({"dir": str(tmp_path)}, NOW)
    assert out["state"] == "waiting"
    assert out["needs"] == [
        {"text": "Otter: Approve the plan", "href": "https://example.com/pr/12"},
        {"text": "Otter: Pick a name"},
    ]
    assert _rows(out)["otter"]["needs"][1] == {"text": "Pick a name"}


def test_invalid_files_become_one_error_row(tmp_path):
    (tmp_path / "broken.json").write_text("{nope", encoding="utf-8")
    (tmp_path / "list.json").write_text("[]", encoding="utf-8")
    (tmp_path / "other-spec.json").write_text(json.dumps({"spec": "agent-status/2"}),
                                              encoding="utf-8")
    (tmp_path / "huge.json").write_text(json.dumps({"x": "a" * 70000}), encoding="utf-8")
    _put(tmp_path, "good")
    out = collect({"dir": str(tmp_path)}, NOW)
    rows = _rows(out)
    assert out["state"] == "error"
    for stem in ("broken", "list", "other-spec", "huge"):
        assert rows[stem]["state"] == "error", stem
        assert rows[stem]["message"].startswith("Not a valid status file"), stem
    assert rows["good"]["state"] == "ok"
    assert "error" in out


def test_hidden_temp_and_non_json_files_are_skipped(tmp_path):
    _put(tmp_path, "good")
    (tmp_path / ".good.json.abc.tmp").write_text("{", encoding="utf-8")
    (tmp_path / ".hidden.json").write_text("{", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "sub.json").mkdir()
    assert set(_rows(collect({"dir": str(tmp_path)}, NOW))) == {"good"}


def test_kinds_route_to_rooms(tmp_path):
    _put(tmp_path, "task-a", kind="task")
    _put(tmp_path, "proj-b", kind="project")
    _put(tmp_path, "job-c", kind="job", message="Backed up 3 files",
         next_run="2026-11-05T02:00:00Z", links=[{"label": "Log", "href": "file:///tmp/c.log"}])
    _put(tmp_path, "untyped")
    harbour = _rows(collect({"dir": str(tmp_path), "_room": "harbour"}, NOW))
    assert set(harbour) == {"task-a", "proj-b", "untyped"}
    jobs = collect({"dir": str(tmp_path), "_room": "jobs"}, NOW)
    assert set(_rows(jobs)) == {"job-c"}
    row = _rows(jobs)["job-c"]
    assert row == {
        "id": "job-c", "name": "Job-C", "next": "2026-11-05T02:00:00Z",
        "last": _iso(60), "state": "ok", "result": "Backed up 3 files",
        "href": "file:///tmp/c.log",
    }
    picked = collect({"dir": str(tmp_path), "_room": "harbour", "kinds": ["job"]}, NOW)
    assert set(_rows(picked)) == {"job-c"}


def test_harbour_row_shape(tmp_path):
    _put(tmp_path, "wren", state="working", title="Wren", agent="claude-code",
         message="Running tests", progress={"done": 3, "total": 8})
    row = _rows(collect({"dir": str(tmp_path), "_room": "harbour"}, NOW))["wren"]
    assert row == {
        "id": "wren", "name": "Wren", "message": "Running tests", "state": "working",
        "kind": "task", "agent": "claude-code", "progress": {"done": 3, "total": 8},
        "needs": [], "busy": True, "updated": _iso(60), "links": [],
    }


def test_several_folders_and_duplicate_ids(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _put(a, "same", age=500, message="older")
    _put(b, "same", age=10, message="newer")
    _put(b, "only-b")
    out = collect({"dirs": [str(a), str(b), str(tmp_path / "missing")]}, NOW)
    rows = _rows(out)
    assert rows["same"]["message"] == "newer"
    assert set(rows) == {"same", "only-b"}


def test_worst_state_and_label(tmp_path):
    _put(tmp_path, "a", state="ok")
    _put(tmp_path, "b", state="working")
    _put(tmp_path, "c", state="waiting", needs_you=[{"text": "x"}])
    out = collect({"dir": str(tmp_path)}, NOW)
    assert out["state"] == "waiting"
    assert out["label"] == "1 waiting, 1 working, 1 ok"
    assert [r["id"] for r in out["rows"]] == ["c", "b", "a"]
    assert out["updated"] == _iso(60)


def test_naive_now_and_offsets(tmp_path):
    minus4 = timezone(timedelta(hours=-4))
    _put(tmp_path, "w", state="working", age=0,
         updated=(NOW.astimezone(minus4) - timedelta(seconds=30)).isoformat())
    rows = _rows(collect({"dir": str(tmp_path)}, NOW))
    assert rows["w"]["state"] == "working"
    out = collect({"dir": str(tmp_path)}, datetime(2026, 11, 4, 12, 0, 0))  # naive: local
    assert out["state"] in {"working", "error"}


def test_output_is_json_safe_with_absolute_times(tmp_path):
    _put(tmp_path, "a", state="working", next_run="2026-11-05T02:00:00Z", kind="job")
    _put(tmp_path, "b", state="waiting", needs_you=[{"text": "x"}])
    for room in ("harbour", "jobs"):
        out = collect({"dir": str(tmp_path), "_room": room}, NOW)
        text = json.dumps(out, allow_nan=False)
        assert " ago" not in text
        assert " in " not in text


def test_collector_is_read_only(tmp_path):
    _put(tmp_path, "a", state="working", age=9999)
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in tmp_path.iterdir()}
    collect({"dir": str(tmp_path)}, NOW)
    after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in tmp_path.iterdir()}
    assert before == after


@pytest.mark.parametrize("room", ["harbour", "jobs"])
def test_output_fits_the_room_schema(tmp_path, room):
    """Long titles, messages and labels are cut to the room schema's limits."""
    schema_mod = pytest.importorskip("lamptown.collectors.schema")
    from importlib import resources

    schema = json.loads(resources.files("lamptown.schemas.rooms")
                        .joinpath(f"{room}.schema.json").read_text(encoding="utf-8"))
    long = "x" * 200
    _put(tmp_path, "a" * 128, state="working", kind="job", title=long, message="m" * 500,
         links=[{"label": "L" * 100, "href": "https://example.com/" + "p" * 50}],
         needs_you=[{"text": "t" * 300, "href": "https://example.com/n"}],
         progress={"done": 1, "total": 2}, next_run="2026-11-05T02:00:00Z")
    _put(tmp_path, "plain", kind="task" if room == "harbour" else "job")
    (tmp_path / "broken.json").write_text("{nope", encoding="utf-8")
    out = collect({"dir": str(tmp_path), "_room": room}, NOW)
    assert len(out["rows"]) >= 2
    assert schema_mod.validate(out, schema) == []


# ---------------------------------------------------------------- lamps that age
def test_a_long_silent_working_file_goes_grey_not_red(tmp_path):
    _put(tmp_path, "crashed", state="working", age=2 * 3600, message="Running tests")
    _put(tmp_path, "forgotten", state="working", age=7 * 3600, message="Running tests")
    out = collect({"dir": str(tmp_path)}, NOW)
    rows = _rows(out)
    assert (rows["crashed"]["state"], rows["crashed"]["why"]) == ("error", "stopped")
    assert (rows["forgotten"]["state"], rows["forgotten"]["why"]) == ("missing", "quiet")
    assert rows["forgotten"]["message"] == "Gone quiet (last: Running tests)"
    assert out["state"] == "error" and out["label"] == "1 error, 1 quiet"
    only_quiet = collect({"dir": str(tmp_path), "quiet_after": 3600}, NOW)
    assert {r["state"] for r in only_quiet["rows"]} == {"missing"}
    assert only_quiet["state"] == "missing"  # nothing alive here: grey, not a red alert
    assert "error" not in only_quiet
    never = _rows(collect({"dir": str(tmp_path), "quiet_after": 0}, NOW))
    assert never["forgotten"]["state"] == "error"


def test_a_file_with_a_long_stale_after_goes_quiet_no_earlier(tmp_path):
    _put(tmp_path, "nightly", state="working", age=10 * 3600, stale_after=12 * 3600)
    assert _rows(collect({"dir": str(tmp_path)}, NOW))["nightly"]["state"] == "working"


def test_old_waiting_error_and_quiet_files_expire(tmp_path):
    week = 7 * 86400
    _put(tmp_path, "w-new", state="waiting", age=week - 60, needs_you=[{"text": "Look"}])
    _put(tmp_path, "w-old", state="waiting", age=week + 60, needs_you=[{"text": "Old ask"}])
    _put(tmp_path, "e-old", state="error", age=30 * 86400)
    _put(tmp_path, "q-old", state="working", age=week + 60)
    _put(tmp_path, "daemon", state="working", age=30 * 86400, stale_after=0)  # never ages
    bad = tmp_path / "broken.json"
    bad.write_text("{", encoding="utf-8")
    old = (NOW - timedelta(days=8)).timestamp()
    os.utime(bad, (old, old))
    out = collect({"dir": str(tmp_path)}, NOW)
    assert set(_rows(out)) == {"w-new", "daemon"}
    assert _rows(out)["daemon"]["state"] == "working"
    assert [n["text"] for n in out["needs"]] == ["W-New: Look"]
    keep = collect({"dir": str(tmp_path), "expire_after": 0}, NOW)
    assert set(_rows(keep)) == {"w-new", "w-old", "e-old", "q-old", "broken", "daemon"}
    short = collect({"dir": str(tmp_path), "expire_after": 3600}, NOW)
    assert set(_rows(short)) == {"daemon"}


@pytest.mark.parametrize("key", ["stale_after", "quiet_after", "hide_after", "expire_after"])
@pytest.mark.parametrize("bad", [-1, "soon", True])
def test_bad_ageing_values_are_a_clear_error(tmp_path, key, bad):
    with pytest.raises(ValueError, match=key):
        collect({"dir": str(tmp_path), key: bad}, NOW)


# ---------------------------------------------------------------- private
def test_private_source_shows_no_names_text_or_links(tmp_path):
    _put(tmp_path, "codex-lantern", state="waiting", title="lantern-secret-plan",
         message="Rewrote the lantern-secret-plan", needs_you=[{"text": "Check lantern-secret-plan",
                                                                "href": "file:///w/lantern"}],
         links=[{"label": "PR", "href": "https://example.com/lantern-secret-plan"}],
         progress={"done": 1, "total": 3})
    out = collect({"dir": str(tmp_path), "private": True}, NOW)
    text = json.dumps(out)
    assert "lantern" not in text
    (row,) = out["rows"]
    assert (row["name"], row["private"], row["links"], row["state"]) == (
        "Private agent", True, [], "waiting")
    assert row["progress"] == {"done": 1, "total": 3}
    assert out["needs"] == [{"text": "Private agent: Needs you"}]


def test_private_dirs_hide_sessions_in_or_under_them(tmp_path):
    secret = tmp_path / "private"
    _put(tmp_path / "s", "a", state="working", title="lantern", **{"x-folder": str(secret)})
    _put(tmp_path / "s", "b", state="working", title="ember",
         **{"x-folder": str(secret / "ember" / "src")})
    _put(tmp_path / "s", "c", state="working", title="paper-kite",
         **{"x-folder": str(tmp_path / "private-not")})
    _put(tmp_path / "s", "d", state="working", title="plain")
    _put(secret / ".agent-status", "e", state="error", title="moss-hidden", message="moss boom")
    out = collect({"dirs": [str(tmp_path / "s"), str(secret / ".agent-status")],
                   "private_dirs": [str(secret)]}, NOW)
    names = sorted(r["name"] for r in out["rows"])
    assert names == ["Private agent", "Private agent", "Private agent", "paper-kite", "plain"]
    text = json.dumps(out)
    for word in ("lantern", "ember", "moss"):
        assert word not in text
    assert out["state"] == "error" and "error" not in out  # a failed agent is not a broken room
    _put(secret / ".agent-status", "f", state="ok")
    (secret / ".agent-status" / "moss-broken.json").write_text("{", encoding="utf-8")
    out = collect({"dirs": [str(secret / ".agent-status")], "private_dirs": [str(secret)]}, NOW)
    assert "moss" not in json.dumps(out)
    assert out["error"].startswith("Private agent: Not a valid status file")


def test_private_dirs_relative_to_the_config_and_with_a_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    _put(tmp_path / "s", "a", title="lantern", **{"x-folder": str(tmp_path / "vault" / "x")})
    _put(tmp_path / "s", "b", title="ember", **{"x-folder": str(tmp_path / "code" / "ember")})
    rel = collect({"dir": str(tmp_path / "s"), "private_dirs": "vault",
                   "_config_dir": str(tmp_path)}, NOW)
    assert sorted(r["name"] for r in rel["rows"]) == ["Private agent", "ember"]
    tilde = collect({"dir": str(tmp_path / "s"), "private_dirs": ["~/code"]}, NOW)
    assert sorted(r["name"] for r in tilde["rows"]) == ["Private agent", "lantern"]


# ---------------------------------------------------------------- crowded folders
@pytest.mark.parametrize("room", ["harbour", "jobs"])
def test_a_crowded_folder_is_cut_to_the_newest_with_an_and_more_row(tmp_path, room):
    schema_mod = pytest.importorskip("lamptown.collectors.schema")
    from importlib import resources

    from lamptown.status import collector

    schema = json.loads(resources.files("lamptown.schemas.rooms")
                        .joinpath(f"{room}.schema.json").read_text(encoding="utf-8"))
    kind = "job" if room == "jobs" else "task"
    n = 2100
    for i in range(n):
        _put(tmp_path, f"a{i:04d}", state="waiting", kind=kind, age=10 + i,
             needs_you=[{"text": f"need {j}"} for j in range(3)])
    out = collect({"dir": str(tmp_path), "_room": room}, NOW)
    assert schema_mod.validate(out, schema) == []
    assert out["state"] == "waiting"
    rows = out["rows"]
    assert len(rows) == collector.MAX_ROWS
    assert rows[-1]["name"] == f"and {n - collector.MAX_ROWS + 1} more"
    kept = {r["id"] for r in rows[:-1]}
    assert "a0000" in kept and f"a{collector.MAX_ROWS - 2:04d}" in kept  # the newest
    assert f"a{collector.MAX_ROWS - 1:04d}" not in kept
    assert len(out["needs"]) == collector.MAX_NEEDS
    assert out["needs"][0]["text"] == "A0000: need 0"
    assert out["needs"][-1]["text"].startswith("and ") and out["needs"][-1]["text"].endswith(
        " more")
