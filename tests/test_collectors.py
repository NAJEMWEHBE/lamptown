"""The collector registry: kinds, local files, entry points, crash isolation, merging, schemas."""

from __future__ import annotations

import datetime as dt
import json
import math
from importlib import metadata, resources
from pathlib import Path

import pytest

from lamptown import collectors as col
from lamptown.collectors import schema as sch

NOW = dt.datetime(2026, 3, 14, 9, 30, tzinfo=dt.UTC)


def job(kind: str, local: str | None = None, **table) -> tuple[str, str | None, dict]:
    paths = {"kind": kind, **table, "_building": "b", "_room": "harbour", "_config_dir": "."}
    return (kind, local, paths)


def local_file(tmp_path: Path, body: str, name: str = "mine.py") -> str:
    f = tmp_path / name
    f.write_text(body, encoding="utf-8")
    return str(f)


# ----------------------------------------------------------------------------- states
def test_worst_order():
    assert col.worst(["ok", "missing"]) == "missing"
    assert col.worst(["ok", "working", "missing"]) == "working"
    assert col.worst(["working", "waiting"]) == "waiting"
    assert col.worst(["waiting", "error", "ok"]) == "error"
    assert col.worst([]) == "ok"


# ----------------------------------------------------------------------------- finding kinds
def test_builtin_static_passes_its_table_through():
    (out,) = col.run_sources([job("static", state="waiting", label="Sign it",
                                  needs=[{"text": "Sign the form"}])], NOW)
    assert out == {"state": "waiting", "label": "Sign it", "needs": [{"text": "Sign the form"}]}


def test_static_defaults_to_ok_and_hides_reserved_keys():
    (out,) = col.run_sources([job("static")], NOW)
    assert out == {"state": "ok"}


def test_status_dir_is_a_builtin_kind():
    assert col.BUILTIN["status_dir"] == "lamptown.status.collector:collect"


def test_unknown_kind_is_an_error_for_that_source_only():
    a, b = col.run_sources([job("no_such_kind"), job("static")], NOW)
    assert a["state"] == "error" and "no_such_kind" in a["error"]
    assert b["state"] == "ok"


def test_local_file_collector(tmp_path):
    f = local_file(tmp_path, "def collect(paths, now):\n    return {'state': 'ok',"
                             " 'label': paths['_building'] + now.isoformat()}\n")
    (out,) = col.run_sources([job("anything", f)], NOW)
    assert out == {"state": "ok", "label": "b" + NOW.isoformat()}


def test_local_file_edits_show_on_the_next_run(tmp_path):
    f = local_file(tmp_path, "def collect(paths, now):\n    return {'state': 'ok'}\n")
    assert col.run_sources([job("x", f)], NOW)[0]["state"] == "ok"
    Path(f).write_text("def collect(paths, now):\n    return {'state': 'waiting'}\n",
                       encoding="utf-8")
    assert col.run_sources([job("x", f)], NOW)[0]["state"] == "waiting"


@pytest.mark.parametrize(("body", "words"), [
    ("x = 1\n", "no collect"),
    ("def collect(paths, now):\n    raise RuntimeError('kettle unplugged')\n", "kettle unplugged"),
    ("import nothing_here_at_all\n", "ModuleNotFoundError"),
    ("def collect(paths, now):\n    return [1, 2]\n", "not a dict"),
    ("def collect(paths, now):\n    return {'state': 'fine'}\n", "state is 'fine'"),
    ("def collect(paths, now):\n    return {'state': 'ok', 'x': float('nan')}\n", "JSON"),
    ("def collect(paths, now):\n    return {'state': 'ok', 'x': {1, 2}}\n", "set"),
    ("def collect(paths, now):\n    return {'state': 'ok', 1: 'a'}\n", "not a string"),
    ("def collect(paths, now):\n    return {'state': 'ok', 'needs': 'help'}\n", "needs"),
    ("def collect(paths, now):\n    return {'state': 'ok', 'needs': [{'text': ''}]}\n", "needs[0]"),
    ("def collect(paths, now):\n    raise SystemExit(3)\n", "SystemExit"),
    ("syntax error here (\n", "SyntaxError"),
])
def test_crash_isolation(tmp_path, body, words):
    bad = local_file(tmp_path, body, "bad.py")
    good = local_file(tmp_path, "def collect(paths, now):\n    return {'state': 'ok'}\n", "good.py")
    out_bad, out_good = col.run_sources([job("x", bad), job("y", good)], NOW)
    assert out_bad["state"] == "error"
    assert words in out_bad["error"]
    assert len(out_bad["error"]) <= 300
    assert out_good == {"state": "ok"}


def test_missing_local_file(tmp_path):
    (out,) = col.run_sources([job("x", str(tmp_path / "gone.py"))], NOW)
    assert out["state"] == "error" and "gone.py" in out["error"]


def test_timeout_isolates_a_slow_source(tmp_path):
    slow = local_file(tmp_path, "import time\ndef collect(paths, now):\n    time.sleep(5)\n"
                                "    return {'state': 'ok'}\n", "slow.py")
    fast = local_file(tmp_path, "def collect(paths, now):\n    return {'state': 'ok'}\n", "fast.py")
    t0 = dt.datetime.now()
    a, b = col.run_sources([job("x", slow), job("y", fast)], NOW, timeout=0.5)
    assert (dt.datetime.now() - t0).total_seconds() < 4
    assert a["state"] == "error" and "longer than 0.5 s" in a["error"]
    assert b == {"state": "ok"}


def test_default_timeout_is_ten_seconds():
    assert col.TIMEOUT == 10.0


def test_collector_gets_its_own_copy_of_paths(tmp_path):
    f = local_file(tmp_path, "def collect(paths, now):\n    paths['kind'] = 'changed'\n"
                             "    return {'state': 'ok'}\n")
    j = job("x", f)
    col.run_sources([j], NOW)
    assert j[2]["kind"] == "x"


# ----------------------------------------------------------------------------- entry points
class _FakeEP:
    def __init__(self, name, fn):
        self.name, self._fn = name, fn

    def load(self):
        return self._fn


def test_entry_point_kind(monkeypatch):
    def tracker(paths, now):
        return {"state": "working", "label": "third party"}

    eps = {"tracker": _FakeEP("tracker", tracker)}
    monkeypatch.setattr(col, "entry_point_collectors", lambda: eps)
    (out,) = col.run_sources([job("tracker")], NOW)
    assert out == {"state": "working", "label": "third party"}


def test_builtin_wins_over_an_entry_point_of_the_same_name(monkeypatch):
    def impostor(paths, now):
        return {"state": "error", "error": "impostor"}

    eps = {"static": _FakeEP("static", impostor)}
    monkeypatch.setattr(col, "entry_point_collectors", lambda: eps)
    assert col.run_sources([job("static")], NOW)[0] == {"state": "ok"}


def test_entry_point_lookup_uses_the_documented_group(monkeypatch):
    seen = {}

    def fake(group=None):
        seen["group"] = group
        return []

    monkeypatch.setattr(metadata, "entry_points", fake)
    assert col.entry_point_collectors() == {}
    assert seen["group"] == "lamptown.collectors"


def test_broken_entry_point_metadata_does_not_stop_the_build(monkeypatch):
    def boom(group=None):
        raise RuntimeError("bad dist")

    monkeypatch.setattr(metadata, "entry_points", boom)
    assert col.entry_point_collectors() == {}


# ------------------------------------------------------------------------ JSON safety and links
def test_json_safe_converts_times_and_paths():
    naive = dt.datetime(2026, 1, 2, 3, 4, 5)
    out = col.json_safe({"a": NOW, "b": dt.date(2026, 1, 2), "c": Path("x") / "y", "d": (1, 2.5),
                         "e": naive})
    assert out["a"] == "2026-03-14T09:30:00+00:00"
    assert out["b"] == "2026-01-02"
    assert out["c"] == str(Path("x") / "y")
    assert out["d"] == [1, 2.5]
    assert dt.datetime.fromisoformat(out["e"]).tzinfo is not None  # naive -> local, with an offset
    json.dumps(out, allow_nan=False)


@pytest.mark.parametrize("bad", [math.inf, b"bytes", object(), {1: "x"}])
def test_json_safe_refuses(bad):
    with pytest.raises(TypeError):
        col.json_safe({"x": bad})


def test_links_are_scrubbed_everywhere():
    out = col.normalise({"state": "ok", "needs": [
        {"text": "a", "href": "https://example.com/a"},
        {"text": "b", "href": "javascript:alert(1)"},
        "plain words",
    ], "rows": [{"name": "r", "state": "ok", "href": "data:text/html,x",
                 "links": [{"href": "file:///tmp/x", "label": "f"}, {"href": "vbscript:x"}]}]})
    assert out["needs"] == [{"text": "a", "href": "https://example.com/a"}, {"text": "b"},
                            {"text": "plain words"}]
    assert "href" not in out["rows"][0]
    assert out["rows"][0]["links"] == [{"href": "file:///tmp/x", "label": "f"}, {}]


# ----------------------------------------------------------------------------- merging
def test_merge_rules():
    a = {"state": "ok", "rows": [{"n": 1}], "label": "fine", "folders": ["first"]}
    b = {"state": "waiting", "rows": [{"n": 2}], "needs": [{"text": "x"}], "label": "late",
         "folders": ["second"], "extra": 1}
    c = {"state": "working", "rows": [{"n": 3}], "needs": [{"text": "y"}]}
    out = col.merge([a, b, c])
    assert out["state"] == "waiting"
    assert out["label"] == "late"  # from the worst source
    assert [r["n"] for r in out["rows"]] == [1, 2, 3]
    assert out["needs"] == [{"text": "x"}, {"text": "y"}]
    assert out["folders"] == ["first"]  # other keys: first value wins
    assert out["extra"] == 1


def test_merge_of_nothing_and_of_one():
    assert col.merge([])["state"] == "missing"
    one = {"state": "error", "error": "e"}
    assert col.merge([one]) == one


def test_source_paths_adds_reserved_keys_and_drops_user_ones(tmp_path):
    p = col.source_paths({"kind": "k", "dir": "/x", "_sneaky": 1}, "jobs", "jobs", tmp_path)
    assert p == {"kind": "k", "dir": "/x", "_building": "jobs", "_room": "jobs",
                 "_config_dir": str(tmp_path)}


# ----------------------------------------------------------------------------- schemas
ROOM_TYPES = ("jobs", "vault", "shelf", "ledger", "contest", "harbour")

GOOD = {
    "jobs": {"state": "working", "rows": [
        {"id": "kite-backup", "name": "Kite backup", "schedule": "daily 02:00",
         "next": "2026-03-15T02:00:00+00:00", "last": "2026-03-14T02:00:00Z", "state": "working",
         "result": "copying", "href": "https://example.com/run/4"}]},
    "vault": {"state": "waiting", "folders": [{"label": "Ideas", "n": 12}],
              "inbox": {"items": [{"title": "Moss notes"}], "untriaged": 1},
              "last_review": "2026-03-10T07:15:00+00:00",
              "needs": [{"text": "Sort one note"}]},
    "shelf": {"state": "ok", "rows": [{"name": "tide-reader", "short": "Reads tide tables",
                                       "state": "ok", "stage": "installed"}]},
    "ledger": {"state": "waiting", "rows": [{"tool": "paper-crane", "state": "waiting",
                                             "decision": None, "installed": False}]},
    "contest": {"state": "waiting",
                "entries": [{"code": "A", "name": "Glass kite", "sealed": True}],
                "judges": {"expected": 3, "received": 1}},
    "harbour": {"state": "waiting", "rows": [
        {"id": "paper-kite", "name": "Paper Kite", "status": "active", "state": "waiting",
         "progress": {"done": 3, "total": 8}, "busy": False, "updated": "2026-03-14T09:00:00Z",
         "links": [{"label": "PR", "href": "https://example.com/pr/1"}]}]},
}


@pytest.mark.parametrize("room", ROOM_TYPES)
def test_every_room_schema_ships_and_parses(room):
    f = resources.files("lamptown").joinpath("schemas", "rooms", f"{room}.schema.json")
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["type"] == "object" and "state" in data["required"]
    assert sch.room_schema(room) == data


@pytest.mark.parametrize("room", ROOM_TYPES)
def test_good_samples_pass(room):
    assert sch.validate(GOOD[room], sch.room_schema(room)) == []


@pytest.mark.parametrize("room", ROOM_TYPES)
def test_bad_state_and_relative_times_fail(room):
    s = sch.room_schema(room)
    assert sch.validate({"state": "sleeping"}, s)
    assert sch.validate({}, s)
    assert sch.validate({"state": "ok", "updated": "in 3 h"}, s)
    assert sch.validate({"state": "ok", "updated": "2026-03-14T09:00:00"}, s)  # no offset
    assert sch.validate({"state": "ok", "updated": "2026-03-14T09:00:00+01:00"}, s) == []


def test_row_problems_are_named():
    errs = sch.validate({"state": "ok", "rows": [{"state": "ok"}]}, sch.room_schema("jobs"))
    assert errs and errs[0].startswith("data.rows[0]") and "'name'" in errs[0]


def test_custom_types_have_no_schema():
    assert sch.room_schema("kiosk") is None


def test_validator_subset():
    s = {"type": "object", "required": ["a"], "additionalProperties": False,
         "properties": {"a": {"type": "integer", "minimum": 1, "maximum": 3},
                        "b": {"type": "array", "maxItems": 1, "items": {"type": "string"}},
                        "c": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "d": {"const": 4}, "e": {"type": "string", "format": "date"}}}
    assert sch.validate({"a": 2, "b": ["x"], "c": None, "d": 4, "e": "2026-02-28"}, s) == []
    assert sch.validate({"a": True}, s)  # a bool is not an integer
    assert sch.validate({"a": 0}, s)
    assert sch.validate({"a": 1, "b": ["x", "y"]}, s)
    assert sch.validate({"a": 1, "c": 3}, s)
    assert sch.validate({"a": 1, "d": 5}, s)
    assert sch.validate({"a": 1, "e": "2026-02-30"}, s)
    assert sch.validate({"a": 1, "zzz": 1}, s)
    assert sch.is_date_time("2026-03-14T09:30:00Z")
    assert not sch.is_date_time("yesterday")
