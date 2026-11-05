"""Review fixes 0.2.1, round 2, batch R2a: what the town shows.

1. secrets in JSON, flag and bare well-known token shapes are masked;
2. URL-encoded paths are cut like plain ones;
3. what a room collector returns has its secrets masked;
4. a hook never turns a reader's job back into a task;
5. a year-1 job with a ``+`` offset does not stop the jobs room;
6. the vault's dropped note names are names only.

Every marker here is invented; paths and tokens are put together at run time.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from lamptown.build import build
from lamptown.collectors import normalise
from lamptown.collectors import vault as vault_collector
from lamptown.config import parse_config
from lamptown.status import adapters
from lamptown.status import collector as status_collector
from lamptown.status.adapters import mask_secrets
from lamptown.status.adapters.claude_code import translate
from lamptown.status.events import name_only, project_of
from lamptown.status.store import read_status, update_status

BS, SL, AT = "\\", "/", "@"
NOW = dt.datetime(2026, 11, 5, 12, tzinfo=dt.UTC)
CANARY = "QZCANARYQZ"  # ten letters, upper case: also a valid AWS key piece


# ----------------------------------------------------------------------------- 1. secrets
def _tokens(m: str) -> dict[str, str]:
    """Bare well-known token shapes, each holding the marker ``m``."""
    pad = "0123456789abcdef0123456789"
    return {
        "ghp": "ghp_" + m + pad,
        "gho": "gho_" + m + pad,
        "ghs": "ghs_" + m + pad,
        "ghu": "ghu_" + m + pad,
        "github-pat": "github_pat_11" + m + "_" + pad,
        "sk": "sk-" + m + pad,
        "sk-proj": "sk-proj-" + m + pad,
        "xoxb": "xoxb-1234567890-" + m,
        "xoxp": "xoxp-1234567890-" + m,
        "akia": "AKIA" + m.upper()[:10] + "ABCDEF",
        "glpat": "glpat-" + m + pad[:10],
    }


SECRET_FORMS = {
    "json": lambda m: '{"token":"' + m + '"}',
    "json-space": lambda m: '{"api_key": "' + m + '"}',
    "json-single": lambda m: "{'token': '" + m + "'}",
    "flag-token": lambda m: "deploy --token " + m + " now",
    "flag-password": lambda m: "login --password " + m,
    "flag-api-key": lambda m: "run --api-key " + m,
    "long-name": lambda m: "A" * 100 + "_TOKEN=" + m,  # a NAME longer than any bound
    "dotted-name": lambda m: "cfg.db.password: " + m,
    **{"raw-" + k: (lambda m, k=k: "pushed with " + _tokens(m)[k] + " today")
       for k in _tokens("x")},
}


@pytest.mark.parametrize("form", SECRET_FORMS)
@pytest.mark.parametrize("long_tokens", [True, False])
def test_mask_secrets_masks_json_flag_and_raw_tokens(form, long_tokens):
    text = SECRET_FORMS[form](CANARY)
    masked = mask_secrets(text, long_tokens=long_tokens)
    assert CANARY not in masked and CANARY.lower() not in masked.lower(), (form, masked)
    assert "…" in masked


@pytest.mark.parametrize("form", SECRET_FORMS)
def test_name_only_masks_json_flag_and_raw_tokens(form):
    out = name_only("Kite: " + SECRET_FORMS[form](CANARY), 200)
    assert CANARY.lower() not in out.lower(), (form, out)
    assert out.startswith("Kite:")


def test_a_token_cut_at_the_limit_leaves_no_part_of_it():
    """A token past the cut: masked first, so no head of it is left at the end."""
    text = "x" * 150 + " ghp_" + CANARY + "0123456789abcdef0123456789"
    out = name_only(text, 160)
    assert "ghp_" not in out and CANARY[:4] not in out, out


@pytest.mark.parametrize("title", [
    "CI/CD pipeline",
    "nightly-backup-of-the-photo-archive",
    "sk-learn notes for the kite model",
    "sk-learn-tutorial-for-beginners",
    "Fix the skipped-tests job",
    "Read the task-runner docs",
    "--verbose build of the kite",
    "git log --author Wren",
    "set --keyboard us",
    "make --pass 3 of the build",
    "ghp notes",
    "Docs: the AKIA prefix",
    "Plan the token budget",
])
def test_ordinary_titles_stay_whole(title):
    assert mask_secrets(title, long_tokens=False) == title
    assert name_only(title, 200) == title


def test_mask_secrets_is_fast_on_a_hostile_text():
    """The NAME part of a NAME=value pair is bounded: no quadratic scan of a long word run."""
    import time

    start = time.perf_counter()
    for text in ("a-" * 20000 + "b", "token" * 8000 + "x", "sk-" + "a-" * 20000,
                 "--a-" * 10000 + "token x"):
        mask_secrets(text, long_tokens=False)
        name_only(text, 200)
    assert time.perf_counter() - start < 2.0


# ----------------------------------------------------------------------------- 2. encoded paths
ENCODED = {
    "win": "C%3A%5CUsers%5C" + CANARY + "%5Ckite%5Cnotes.txt",
    "win-lower": "c%3a%5cusers%5c" + CANARY + "%5ckite%5cnotes.txt",
    "win-mixed": "C:%5CUsers%5C" + CANARY + "%5Cnotes.txt",
    "win-slash": "C%3A%2FUsers%2F" + CANARY + "%2Fnotes.txt",
    "posix": "%2Fhome%2F" + CANARY + "%2Fkite%2Fnotes.txt",
    "mac": "%2FUsers%2F" + CANARY + "%2Fnotes.txt",
    "double": "C%253A%255CUsers%255C" + CANARY + "%255Cnotes.txt",
    "file-url": "file:%2F%2F%2FC%3A%2FUsers%2F" + CANARY + "%2Fnotes.txt",
    "home-only": "%2Fhome%2F" + CANARY,
}


@pytest.mark.parametrize("form", ENCODED)
def test_name_only_cuts_url_encoded_paths(form):
    out = name_only("Edit " + ENCODED[form] + " now", 200)
    assert CANARY.lower() not in out.lower(), (form, out)
    assert out.startswith("Edit ")


def test_an_encoded_path_keeps_its_final_name():
    assert name_only("Edit " + ENCODED["win"], 200) == "Edit notes.txt"
    assert name_only("Edit " + ENCODED["posix"], 200) == "Edit notes.txt"
    assert name_only("see " + ENCODED["home-only"], 200) == "see ~"


@pytest.mark.parametrize("form", ["win", "posix", "home-only"])
def test_an_encoded_project_names_no_login(form):
    home = ENCODED[form].rsplit("%", 1)[0] if form != "home-only" else ENCODED[form]
    for value in (ENCODED[form], home):
        assert CANARY.lower() not in project_of(value, "").lower(), value
        assert CANARY.lower() not in project_of("", value).lower(), value


def test_plain_percent_text_stays():
    assert name_only("Coverage up 5% and 20%", 200) == "Coverage up 5% and 20%"


# ----------------------------------------------------------------------------- 3. collectors
COLLECTORS = r'''[[building]]
id = "jobs"
type = "jobs"
[[building.source]]
kind = "log_glob"
name = "Kites"
glob = "logs/*.log"
done = 'Done:\s*(.+)$'
[[building]]
id = "notes"
type = "vault"
[[building.source]]
kind = "markdown_vault"
path = "vault"
inbox = "inbox/*.md"
[[building]]
id = "gate"
type = "ledger"
[[building.source]]
kind = "scan_glob"
glob = "scans/*.json"
[[building]]
id = "skills"
type = "shelf"
[[building.source]]
kind = "skill_dirs"
dirs = ["skills"]
[[building]]
id = "lab"
type = "contest"
[[building.source]]
kind = "contest_json"
glob = "contests/*.json"
'''


def _put(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_collector_text_has_its_secrets_masked(tmp_path):
    marks = {k: "QZ" + k.upper() + "SECRET" for k in ("jobs", "vault", "ledger", "shelf",
                                                       "contest", "flag", "raw")}
    raw = "ghp_" + marks["raw"] + "0123456789abcdef0123"
    _put(tmp_path, "logs/run.log", "Done: token=" + marks["jobs"] + " and " + raw + "\n")
    _put(tmp_path, "vault/inbox/ideas.md", "## 1. token=" + marks["vault"] + "\ntriage: keep\n")
    _put(tmp_path, "scans/scan.json", json.dumps({"tool": "token=" + marks["ledger"],
                                                  "verdict": "safe", "risk": 1}))
    _put(tmp_path, "skills/kite/SKILL.md",
         "---\nname: kite\ndescription: run --password " + marks["flag"] + " then token="
         + marks["shelf"] + "\n---\n")
    _put(tmp_path, "contests/one.json", json.dumps({
        "title": '{"token":"' + marks["contest"] + '"}',
        "entries": [{"code": "A", "name": "Kite"}]}))
    result = build(parse_config(COLLECTORS, tmp_path / "lamptown.toml"),
                   out_dir=tmp_path / "out", now=NOW)
    pages = {p.name: p.read_text(encoding="utf-8") for p in (tmp_path / "out").iterdir()
             if p.is_file()}
    for kind, mark in marks.items():
        reached = sorted(n for n, text in pages.items() if mark in text)
        assert not reached, f"{kind} reaches {reached}"
    rooms = result.data["rooms"]
    assert {k: v["state"] for k, v in rooms.items()} == {
        "jobs": "ok", "notes": "ok", "gate": "waiting", "skills": "ok", "lab": "working"}
    data = (tmp_path / "out" / "town-data.js").read_text(encoding="utf-8")
    assert "token=…" in data  # the field is still shown, its secret masked


@pytest.mark.parametrize("form", ["raw-ghp", "raw-sk", "raw-xoxb", "raw-glpat", "flag-token"])
def test_a_room_that_capitalises_or_cuts_text_masks_it_first(form):
    """The shelf capitalises a skill's line and drops what its font lacks; contest and ledger
    cut theirs. The mask runs before (font_safe), so the shape is still seen."""
    from lamptown.collectors.shelf import font_safe, plain_line

    text = SECRET_FORMS[form](CANARY).replace("pushed with ", "")
    for shown in (plain_line(text), font_safe(text)[:20], font_safe(text.upper())):
        assert CANARY.lower() not in shown.lower(), (form, shown)
    assert "..." in font_safe(text)


def test_collector_hrefs_are_left_for_their_own_fix():
    """A link is not text: masking it would break it (hrefs are 0.3)."""
    href = "file:///work/kite/token=abc123/notes.md"
    out = normalise({"state": "ok", "rows": [{"name": "token=abc123", "href": href,
                                              "folderHref": href}]})
    row = out["rows"][0]
    assert row["href"] == href and row["folderHref"] == href
    assert row["name"] == "token=…"


def test_collector_errors_have_their_secrets_masked(monkeypatch):
    import lamptown.collectors as col

    def boom(paths, now):
        raise ValueError("bad login --password " + CANARY)

    monkeypatch.setattr(col, "find_collector", lambda kind, local=None: boom)
    (out,) = col.run_sources([("static", None, {})], NOW, timeout=5)
    assert out["state"] == "error" and CANARY not in out["error"], out


# ----------------------------------------------------------------------------- 4. hook kind
def _hook(name: str = "PreToolUse"):
    return translate({"hook_event_name": name,
                      "session_id": "11111111-1111-4111-8111-111111111111",
                      "cwd": "/work/kite", "tool_name": "Bash"})


def test_a_hook_never_turns_a_job_back_into_a_task(tmp_path):
    sid = "claude-code-11111111-111"
    update_status(sid, tmp_path, now=NOW, kind="job", title="Kite job", state="working")
    adapters.apply(_hook(), tmp_path, id_override=sid)
    doc = read_status(tmp_path / (sid + ".json"))
    assert doc["kind"] == "job"
    assert doc["message"] == "Using Bash"  # the hook still wrote its change


def test_a_hook_makes_a_new_record_a_task(tmp_path):
    adapters.apply(_hook(), tmp_path, id_override="claude-code-fresh")
    assert read_status(tmp_path / "claude-code-fresh.json")["kind"] == "task"


# ----------------------------------------------------------------------------- 5. year-1 job
def test_a_year_one_job_does_not_stop_the_jobs_room(tmp_path):
    doc = {"spec": "agent-status/1", "id": "far-job", "title": "Far job", "state": "ok",
           "kind": "job", "updated": "0001-01-01T00:00:00+05:00",
           "next_run": "0001-01-02T00:00:00+05:00"}
    (tmp_path / "far-job.json").write_text(json.dumps(doc), encoding="utf-8")
    now = dt.datetime.now(dt.UTC)
    for room in ("harbour", "jobs"):
        out = status_collector.collect({"dir": str(tmp_path), "_room": room,
                                        "kinds": ["job"]}, now)
        assert out["state"] != "error", (room, out)
        assert all(r.get("why") != "missed" for r in out.get("rows", [])), out


# ----------------------------------------------------------------------------- 6. vault dropped
def test_vault_dropped_names_are_names_only(tmp_path):
    name = "wren" + AT + "qzmail.net token=" + CANARY
    for sub in ("kites", "inbox/dropped", "kept"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
        (tmp_path / sub / f"{name}.md").write_text("# a note\n", encoding="utf-8")
    out = vault_collector.collect({"path": str(tmp_path), "dropped": "inbox/dropped",
                                   "kept": "kept"}, NOW)
    kept = next(f for f in out["folders"] if f.get("kept"))
    assert out["dropped"]["names"] == kept["notes"]
    for shown in out["dropped"]["names"]:
        assert CANARY not in shown and AT not in shown and "qzmail" not in shown, shown
