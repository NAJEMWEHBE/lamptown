"""The agent-status/1 schema, its spec copy, and the standard-library validator."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from lamptown.status import SPEC, load_schema, parse_time, validate
from lamptown.status.schema import schema_text

ROOT = Path(__file__).resolve().parents[1]

GOOD = {
    "spec": "agent-status/1", "id": "refactor-auth", "title": "Refactor the auth module",
    "agent": "codex", "kind": "task", "state": "working", "message": "Running tests",
    "needs_you": [{"text": "Approve the plan", "href": "https://example.com/pr/12"}],
    "progress": {"done": 3, "total": 8}, "updated": "2026-11-04T10:12:00Z",
    "stale_after": 1800, "links": [{"label": "PR", "href": "https://example.com/pr/12"}],
}


def test_schema_is_valid_json_with_the_spec_name():
    schema = load_schema()
    assert schema["title"] == SPEC == "agent-status/1"
    assert schema["$schema"].startswith("https://json-schema.org/draft/2020-12")
    assert set(schema["required"]) == {"spec", "id", "title", "state", "updated"}


def test_spec_folder_copy_is_identical():
    published = (ROOT / "spec" / "agent-status-1.schema.json").read_text(encoding="utf-8")
    assert published == schema_text()


def test_spec_document_example_validates():
    text = (ROOT / "spec" / "agent-status-1.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, flags=re.S)
    assert blocks, "the spec should show at least one JSON example"
    for block in blocks:
        doc = json.loads(block)
        if isinstance(doc, dict) and "spec" in doc:
            assert validate(doc) == [], block


def test_good_document():
    assert validate(GOOD) == []


def test_minimal_document():
    assert validate({k: GOOD[k] for k in ("spec", "id", "title", "state", "updated")}) == []


@pytest.mark.parametrize("version", ["agent-status/1", "agent-status/1.0", "agent-status/1.7.2"])
def test_readers_accept_any_1x(version):
    assert validate({**GOOD, "spec": version}) == []


def test_unknown_and_extension_fields_are_ignored():
    assert validate({**GOOD, "x-team": {"any": ["thing"]}, "future_field": 1}) == []


def _broken(**changes):
    doc = copy.deepcopy(GOOD)
    for key, value in changes.items():
        if value is ...:
            doc.pop(key)
        else:
            doc[key] = value
    return doc


@pytest.mark.parametrize(("doc", "fragment"), [
    (_broken(spec=...), "spec"),
    (_broken(id=...), "id"),
    (_broken(title=...), "title"),
    (_broken(state=...), "state"),
    (_broken(updated=...), "updated"),
    (_broken(spec="agent-status/2"), "spec"),
    (_broken(spec="lamp/1"), "spec"),
    (_broken(state="building"), "state"),
    (_broken(state="missing"), "state"),
    (_broken(kind="epic"), "kind"),
    (_broken(id="../escape"), "id"),
    (_broken(id="a/b"), "id"),
    (_broken(id="-dash-first"), "id"),
    (_broken(id="x" * 129), "id"),
    (_broken(title=""), "title"),
    (_broken(title="t" * 201), "title"),
    (_broken(updated="2026-11-04T10:12:00"), "updated"),
    (_broken(updated="yesterday"), "updated"),
    (_broken(updated="2026-13-45T10:12:00Z"), "updated"),
    (_broken(updated=1730714000), "updated"),
    (_broken(message="m" * 501), "message"),
    (_broken(progress={"done": 9, "total": 8}), "progress"),
    (_broken(progress={"done": -1, "total": 8}), "progress"),
    (_broken(progress={"done": 1}), "progress"),
    (_broken(progress={"done": True, "total": 8}), "progress"),
    (_broken(stale_after=-5), "stale_after"),
    (_broken(stale_after="30m"), "stale_after"),
    (_broken(links=[{"label": "x", "href": "javascript:alert(1)"}]), "links"),
    (_broken(links=[{"label": "x", "href": "data:text/html,hi"}]), "links"),
    (_broken(links=[{"href": "https://example.com"}]), "links"),
    (_broken(needs_you=[{"href": "https://example.com"}]), "needs_you"),
    (_broken(needs_you=[{"text": "x", "href": "ftp://example.com"}]), "needs_you"),
    (_broken(needs_you=[{"text": "x"}] * 21), "needs_you"),
    (_broken(needs_you="Approve it"), "needs_you"),
])
def test_bad_documents(doc, fragment):
    problems = validate(doc)
    assert problems, doc
    assert any(fragment in p for p in problems), problems


def test_not_an_object():
    assert validate([GOOD]) == ["$: must be object"]


def test_stale_after_null_and_zero_are_allowed():
    assert validate({**GOOD, "stale_after": None}) == []
    assert validate({**GOOD, "stale_after": 0}) == []


@pytest.mark.parametrize(("text", "ok"), [
    ("2026-11-04T10:12:00Z", True),
    ("2026-11-04T10:12:00.123+05:30", True),
    ("2026-11-04T10:12:00-07:00", True),
    ("2026-11-04T10:12:00", False),
    ("2026-11-04", False),
    ("", False),
    (None, False),
])
def test_parse_time(text, ok):
    assert (parse_time(text) is not None) is ok


def test_matches_a_full_json_schema_validator_when_installed():
    jsonschema = pytest.importorskip("jsonschema")
    checker = jsonschema.Draft202012Validator(load_schema())
    assert not list(checker.iter_errors(GOOD))
    assert list(checker.iter_errors(_broken(state="building")))
    assert list(checker.iter_errors(_broken(links=[{"label": "x", "href": "javascript:x"}])))
