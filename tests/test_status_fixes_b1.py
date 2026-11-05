"""Review fixes 0.2.1, batch B1: status records and adapters.

1. Command lines, stderr text and exception text never reach a status record's words (and the
   collector cuts paths and masks secrets in titles and messages as a second wall).
2. Lone surrogates are scrubbed where a status file is written; a lamp error never stops
   ``status run``.
3. A hook names a record after its folder only when it creates it (no title flapping).
4. Status-file jobs in the jobs room go "missed" (spec 0.1 section 2) and ``ok`` job rows don't
   vanish after a day.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lamptown.status import adapters
from lamptown.status.adapters import claude_code, wrapper
from lamptown.status.adapters.wrapper import run_command
from lamptown.status.cli import main
from lamptown.status.collector import collect
from lamptown.status.context import agent_status
from lamptown.status.store import atomic_write_json, read_status, status_path, update_status

FAKE_HOME = r"C:\Users\you\wrenfake"  # placeholder home; "wrenfake" is the canary folder
TOKEN = "--token=abc123zz"
MAIL = "wren@example.com"
CANARIES = ("wrenfake", "abc123zz", MAIL, "STDERR-CANARY")


def _doc(folder: Path, status_id: str) -> dict:
    return json.loads((folder / f"{status_id}.json").read_text(encoding="utf-8"))


def _clean(text: str) -> None:
    for canary in CANARIES:
        assert canary not in text, (canary, text)


# ---------------------------------------------------------------- 1. words that leak
def test_wrapper_start_message_names_only_the_program(tmp_path):
    copy = tmp_path / "seen.json"
    script = "import shutil, sys; shutil.copy(sys.argv[1], sys.argv[2])"
    code = run_command([sys.executable, "-c", script, str(tmp_path / "deploy.json"), str(copy),
                        TOKEN, FAKE_HOME + r"\bin\deploy.cmd"], "deploy", directory=tmp_path)
    assert code == 0
    seen = json.loads(copy.read_text(encoding="utf-8"))  # the file while the command ran
    assert seen["state"] == "working"
    assert seen["message"] == f"Running {Path(sys.executable).name}"
    _clean(seen["message"])
    assert str(tmp_path) not in seen["message"]


def test_wrapper_failure_message_never_holds_stderr_text(tmp_path, capfd):
    script = ("import sys; sys.stderr.write('STDERR-CANARY " + FAKE_HOME.replace("\\", "/")
              + " " + TOKEN + "\\nsecond line\\n'); sys.exit(3)")
    code = run_command([sys.executable, "-c", script, TOKEN], "build", directory=tmp_path)
    assert code == 3
    doc = _doc(tmp_path, "build")
    assert doc["state"] == "error"
    assert doc["message"].startswith("Exit 3 after ")
    assert "2 lines on stderr" in doc["message"]
    _clean(doc["message"])
    assert "STDERR-CANARY" in capfd.readouterr().err  # stderr still passes through


def test_wrapper_cannot_start_names_only_the_program(tmp_path, capsys):
    missing = str(tmp_path / "wrenfake" / "nope-deploy.cmd")
    code = run_command([missing, TOKEN], "deploy", directory=tmp_path)
    assert code == 127
    doc = _doc(tmp_path, "deploy")
    assert doc["state"] == "error"
    assert doc["message"].startswith("Could not start nope-deploy.cmd")
    _clean(doc["message"])
    assert str(tmp_path) not in doc["message"]


def test_context_error_shows_the_exception_type_only(tmp_path):
    with pytest.raises(RuntimeError), agent_status("report", directory=tmp_path):
        raise RuntimeError(f"open {FAKE_HOME}\\notes.txt failed {TOKEN} for {MAIL}")
    assert _doc(tmp_path, "report")["message"] == "RuntimeError"


def _put(folder: Path, status_id: str, **fields) -> None:
    doc = {"spec": "agent-status/1", "id": status_id, "title": status_id, "state": "working",
           "updated": "2026-11-04T11:59:00Z", **fields}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{status_id}.json").write_text(json.dumps(doc), encoding="utf-8")


NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("room,kind", [("harbour", "task"), ("jobs", "job")])
def test_collector_cuts_paths_and_masks_secrets_in_titles_and_messages(tmp_path, room, kind):
    _put(tmp_path, "deploy", kind=kind, title=f"Deploy {FAKE_HOME}\\bin\\deploy.cmd for {MAIL}",
         message=f"Running {FAKE_HOME}\\bin\\deploy.cmd {TOKEN} password=hunter2zz",
         state="waiting", needs_you=[{"text": f"Check {FAKE_HOME}\\out.log"}])
    out = collect({"dir": str(tmp_path), "_room": room}, NOW)
    text = json.dumps(out, ensure_ascii=False)
    _clean(text)
    assert "hunter2zz" not in text
    row = out["rows"][0]
    assert row["name"].startswith("Deploy deploy.cmd")
    words = row["message"] if room == "harbour" else row["result"]
    assert words.startswith("Running deploy.cmd")


def test_collector_wall_turns_lone_surrogates_into_the_replacement_character(tmp_path):
    (tmp_path / "wren.json").write_text(
        '{"spec": "agent-status/1", "id": "wren", "title": "Fix kite \\ud83d", '
        '"state": "working", "updated": "2026-11-04T11:59:00Z"}', encoding="utf-8")
    assert collect({"dir": str(tmp_path)}, NOW)["rows"][0]["name"] == "Fix kite \ufffd"


def test_collector_wall_keeps_plain_titles(tmp_path):
    _put(tmp_path, "nightly", kind="task", title="Nightly backup of the kite-photos-archive",
         message="Fix save/load slots")
    row = collect({"dir": str(tmp_path)}, NOW)["rows"][0]
    assert (row["name"], row["message"]) == ("Nightly backup of the kite-photos-archive",
                                            "Fix save/load slots")


# ---------------------------------------------------------------- 2. lone surrogates
def test_atomic_write_scrubs_lone_surrogates_and_keeps_real_text(tmp_path):
    path = tmp_path / "x.json"
    atomic_write_json(path, {"lone": "caf\udce9", "kite": "Kite town 🪁",
                             "pair": "split \ud83e\ude81", "caf\udce9": 1})
    doc = json.loads(path.read_bytes().decode("utf-8"))
    assert doc == {"lone": "caf\ufffd", "kite": "Kite town 🪁", "pair": "split 🪁", "caf\ufffd": 1}


def test_update_status_returns_what_it_wrote(tmp_path):
    doc = update_status("kite", tmp_path, title="caf\udce9", message="half \ud83e")
    assert (doc["title"], doc["message"]) == ("caf\ufffd", "half \ufffd")
    assert read_status(status_path("kite", tmp_path))["title"] == "caf\ufffd"


def test_status_run_with_surrogates_still_runs_the_command(tmp_path, capfd):
    # POSIX argv holds undecodable bytes as lone surrogates (surrogateescape)
    code = run_command([sys.executable, "-c", "print('ran')", "caf\udce9.txt"], "probe",
                       title="caf\udce9", directory=tmp_path)
    assert code == 0
    assert "ran" in capfd.readouterr().out
    doc = _doc(tmp_path, "probe")
    assert (doc["state"], doc["title"]) == ("ok", "caf\ufffd")


def test_a_lamp_error_never_stops_the_command(tmp_path, monkeypatch, capfd):
    def broken(*args, **kwargs):
        raise RuntimeError("the lamp broke")

    monkeypatch.setattr(wrapper, "update_status", broken)
    code = run_command([sys.executable, "-c", "print('ran'); raise SystemExit(4)"], "probe",
                       directory=tmp_path)
    assert code == 4
    out, err = capfd.readouterr()
    assert "ran" in out
    assert err.count("can't update the status file") == 1


def test_status_set_title_with_a_lone_surrogate(tmp_path, capsys):
    assert main(["set", "kite", "--dir", str(tmp_path), "--title", "caf\udce9"]) == 0
    assert _doc(tmp_path, "kite")["title"] == "caf\ufffd"


# ---------------------------------------------------------------- 3. title flapping
def _hook(event: str = "PreToolUse") -> adapters.Update:
    return claude_code.translate({"hook_event_name": event, "session_id": "abc123",
                                  "cwd": "/work/kite", "tool_name": "Bash"})


def test_hook_names_a_new_record_after_its_folder(tmp_path):
    adapters.apply(_hook(), tmp_path)
    assert _doc(tmp_path, "claude-code-abc123")["title"] == "kite"


def test_hook_never_overwrites_an_existing_title(tmp_path):
    adapters.apply(_hook("SessionStart"), tmp_path)
    update_status("claude-code-abc123", tmp_path, title="Fix save slots")  # the reader's title
    for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"):
        adapters.apply(_hook(event), tmp_path)
        assert _doc(tmp_path, "claude-code-abc123")["title"] == "Fix save slots", event


def test_hook_title_option_still_sets_the_title(tmp_path):
    update_status("claude-code-abc123", tmp_path, title="Fix save slots")
    adapters.apply(_hook(), tmp_path, title_override="Kite bot")
    assert _doc(tmp_path, "claude-code-abc123")["title"] == "Kite bot"


def test_update_status_defaults_fill_only_missing_fields(tmp_path):
    doc = update_status("kite", tmp_path, defaults={"title": "kite folder"}, state="working")
    assert doc["title"] == "kite folder"
    doc = update_status("kite", tmp_path, defaults={"title": "other"}, state="ok")
    assert (doc["title"], doc["state"]) == ("kite folder", "ok")


# ---------------------------------------------------------------- 4. job aging
LAST = datetime(2026, 3, 7, 21, 0, tzinfo=UTC)  # a weekly Saturday job


def _job(folder: Path, *, next_run: datetime | None, state: str = "ok", **extra) -> None:
    doc = {"spec": "agent-status/1", "id": "weekly-backup", "title": "Weekly backup",
           "kind": "job", "state": state, "updated": LAST.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "message": "Backed up 3 folders", **extra}
    if next_run is not None:
        doc["next_run"] = next_run.strftime("%Y-%m-%dT%H:%M:%SZ")
    (folder / "weekly-backup.json").write_text(json.dumps(doc), encoding="utf-8")


def _jobs(folder: Path, now: datetime, **extra) -> dict:
    return collect({"dir": str(folder), "_room": "jobs", **extra}, now)


def test_an_ok_job_row_stays_after_a_day(tmp_path):
    _job(tmp_path, next_run=LAST + timedelta(days=7))
    out = _jobs(tmp_path, LAST + timedelta(days=2))
    assert [(r["name"], r["state"]) for r in out["rows"]] == [("Weekly backup", "ok")]
    assert out["state"] == "ok"


def test_a_job_with_no_next_run_stays_ok(tmp_path):
    _job(tmp_path, next_run=None)
    out = _jobs(tmp_path, LAST + timedelta(days=60))
    assert [(r["state"], r.get("why")) for r in out["rows"]] == [("ok", None)]


@pytest.mark.parametrize("late,missed", [(timedelta(days=8), False),
                                         (timedelta(days=8, hours=2), True),
                                         (timedelta(days=20), True)])
def test_a_job_long_past_its_next_run_reads_as_missed(tmp_path, late, missed):
    # period = next_run - updated = 7 d; missed once the last run is older than 7 d + 15 %
    _job(tmp_path, next_run=LAST + timedelta(days=7))
    out = _jobs(tmp_path, LAST + late)
    row = out["rows"][0]
    if not missed:
        assert (row["state"], row.get("why")) == ("ok", None)
        return
    assert (row["state"], row["why"]) == ("error", "missed")
    assert row["result"].startswith("Missed its run: the last run is from 2026-03-07.")
    assert out["state"] == "error"
    assert "error" not in out  # a missed run is the job's own state, not an unreadable room


def test_a_failed_job_past_its_next_run_reads_as_missed(tmp_path):
    _job(tmp_path, next_run=LAST + timedelta(days=7), state="error", message="Disk full")
    row = _jobs(tmp_path, LAST + timedelta(days=30))["rows"][0]
    assert (row["state"], row["why"]) == ("error", "missed")
    assert row["result"].endswith("Disk full")


def test_a_next_run_before_the_last_write_never_reads_as_missed(tmp_path):
    _job(tmp_path, next_run=LAST - timedelta(hours=1))
    row = _jobs(tmp_path, LAST + timedelta(days=3))["rows"][0]
    assert (row["state"], row.get("why")) == ("ok", None)


def test_hide_after_set_in_the_config_still_hides_ok_jobs(tmp_path):
    _job(tmp_path, next_run=LAST + timedelta(days=7))
    assert _jobs(tmp_path, LAST + timedelta(days=2), hide_after=3600)["rows"] == []


def test_status_jobs_miss_with_the_jobs_collectors_grace():
    from lamptown.collectors.jobs_schedule import GRACE
    from lamptown.status.collector import MISSED_GRACE

    assert MISSED_GRACE == GRACE


def test_harbour_still_hides_old_ok_tasks(tmp_path):
    _put(tmp_path, "old-task", kind="task", state="ok", updated="2026-11-02T12:00:00Z")
    assert collect({"dir": str(tmp_path), "_room": "harbour"}, NOW)["rows"] == []
