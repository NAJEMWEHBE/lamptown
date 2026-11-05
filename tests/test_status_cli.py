"""``lamptown status set | run | list`` through ``main(argv)`` and as a real process."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lamptown.status import validate
from lamptown.status.cli import add_parser, main


def _doc(folder: Path, status_id: str) -> dict:
    return json.loads((folder / f"{status_id}.json").read_text(encoding="utf-8"))


def test_set_then_list(tmp_path, capsys):
    assert main(["set", "kite", "--dir", str(tmp_path), "--title", "Paper Kite",
                 "--state", "working", "-m", "Running tests", "--progress", "3/8",
                 "--link", "PR=https://example.com/pr/12", "--agent", "aider"]) == 0
    doc = _doc(tmp_path, "kite")
    assert validate(doc) == []
    assert doc["progress"] == {"done": 3, "total": 8}
    assert doc["links"] == [{"label": "PR", "href": "https://example.com/pr/12"}]
    capsys.readouterr()
    assert main(["list", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "working" in out
    assert "Paper Kite: Running tests [3/8]" in out


def test_need_implies_waiting_and_pairs_hrefs(tmp_path):
    main(["set", "kite", "--dir", str(tmp_path), "-q",
          "--need", "Approve the plan", "--need-href", "https://example.com/pr/12",
          "--need", "Pick a colour"])
    doc = _doc(tmp_path, "kite")
    assert doc["state"] == "waiting"
    assert doc["needs_you"] == [
        {"text": "Approve the plan", "href": "https://example.com/pr/12"},
        {"text": "Pick a colour"},
    ]
    main(["set", "kite", "--dir", str(tmp_path), "-q", "--clear-needs", "--state", "ok"])
    doc = _doc(tmp_path, "kite")
    assert (doc["state"], doc["needs_you"]) == ("ok", [])


def test_set_keeps_fields_not_given(tmp_path):
    main(["set", "kite", "--dir", str(tmp_path), "-q", "--title", "Paper Kite", "--kind", "job",
          "--next-run", "2026-11-05T02:00:00Z"])
    main(["set", "kite", "--dir", str(tmp_path), "-q", "--state", "ok"])
    doc = _doc(tmp_path, "kite")
    assert (doc["title"], doc["kind"], doc["next_run"]) == ("Paper Kite", "job",
                                                            "2026-11-05T02:00:00Z")


@pytest.mark.parametrize("bad", [
    ["--progress", "9/8"], ["--progress", "three"], ["--link", "nolabel"],
    ["--state", "building"], ["--next-run", "tomorrow"],
])
def test_set_rejects_bad_options(tmp_path, bad):
    with pytest.raises(SystemExit) as exc:
        main(["set", "kite", "--dir", str(tmp_path), *bad])
    assert exc.value.code == 2


def test_set_rejects_bad_values_without_traceback(tmp_path, capsys):
    assert main(["set", "../escape", "--dir", str(tmp_path)]) == 2
    assert main(["set", "kite", "--dir", str(tmp_path), "--link", "x=javascript:alert(1)"]) == 2
    assert "bad id" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_list_json_and_empty(tmp_path, capsys):
    assert main(["list", "--dir", str(tmp_path)]) == 0
    assert "No status files" in capsys.readouterr().out
    assert main(["list", "--dir", str(tmp_path / "nope")]) == 0
    assert "no folder" in capsys.readouterr().out
    main(["set", "kite", "--dir", str(tmp_path), "-q", "--state", "ok"])
    capsys.readouterr()
    main(["list", "--json", "--dir", str(tmp_path)])
    rows = json.loads(capsys.readouterr().out)
    assert [(r["id"], r["state"], r["valid"]) for r in rows] == [("kite", "ok", True)]


def test_run_success_and_failure(tmp_path, capfd):
    ok = main(["run", "--id", "build", "--dir", str(tmp_path), "--kind", "job", "--",
               sys.executable, "-c", "print('hello from the kite')"])
    assert ok == 0
    doc = _doc(tmp_path, "build")
    assert (doc["state"], doc["kind"]) == ("ok", "job")
    assert doc["message"].startswith("Finished in")
    assert "hello from the kite" in capfd.readouterr().out  # output passes through

    script = "import sys; sys.stderr.write('line one\\nthe string snapped\\n'); sys.exit(3)"
    code = main(["run", "--id", "build", "--dir", str(tmp_path), "--",
                 sys.executable, "-c", script])
    assert code == 3
    doc = _doc(tmp_path, "build")
    assert doc["state"] == "error"
    assert doc["message"].startswith("Exit 3")
    # review-fix B1: only a count of stderr lines, never their text (the town shows the message)
    assert doc["message"].endswith(", 2 lines on stderr")
    assert "the string snapped" not in doc["message"]
    assert "the string snapped" in capfd.readouterr().err  # stderr passes through too


def test_run_missing_command(tmp_path, capsys):
    code = main(["run", "--id", "ghost", "--dir", str(tmp_path), "--",
                 "lamptown-no-such-program-xyz"])
    assert code == 127
    assert _doc(tmp_path, "ghost")["state"] == "error"
    assert main(["run", "--id", "ghost", "--dir", str(tmp_path)]) == 2


def test_run_heartbeat_keeps_the_file_fresh(tmp_path):
    from lamptown.status.adapters.wrapper import run_command

    script = (
        "import json, sys, time, pathlib\n"
        "status, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])\n"
        "first = json.loads(status.read_text(encoding='utf-8'))['updated']\n"
        "time.sleep(2.6)\n"
        "second = json.loads(status.read_text(encoding='utf-8'))['updated']\n"
        "out.write_text(first + ' ' + second, encoding='utf-8')\n"
    )
    seen = tmp_path / "seen.txt"
    code = run_command([sys.executable, "-c", script, str(tmp_path / "beat.json"), str(seen)],
                       "beat", directory=tmp_path, heartbeat=1.0, stale_after=600)
    assert code == 0
    first, second = seen.read_text(encoding="utf-8").split()
    assert second > first, "the heartbeat should refresh 'updated' while the command runs"
    assert _doc(tmp_path, "beat")["state"] == "ok"


def test_add_parser_wires_into_a_main_parser(tmp_path):
    parser = argparse.ArgumentParser(prog="lamptown")
    sub = parser.add_subparsers(dest="command")
    add_parser(sub)
    args = parser.parse_args(["status", "set", "kite", "--dir", str(tmp_path), "-q"])
    assert args.func(args) == 0
    assert _doc(tmp_path, "kite")["state"] == "working"
    args = parser.parse_args(["status"])
    assert args.func(args) == 0  # prints the status help


def _module_cli(*argv: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "lamptown.status.cli", *argv], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", timeout=60, check=False)


def test_module_entry_point_set_and_list(tmp_path):
    env = {**os.environ, "AGENT_STATUS_DIR": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
    done = _module_cli("set", "tidebook", "--title", "Tidebook", "--state", "waiting",
                       "--need", "Review the tide table", env=env)
    assert done.returncode == 0, done.stderr
    assert validate(_doc(tmp_path, "tidebook")) == []
    shown = _module_cli("list", env=env)
    assert shown.returncode == 0, shown.stderr
    assert "tidebook" in shown.stdout
    assert "needs you: Review the tide table" in shown.stdout


def test_codex_notify_as_a_real_process(tmp_path):
    env = {**os.environ, "AGENT_STATUS_DIR": str(tmp_path)}
    payload = json.dumps({"type": "agent-turn-complete", "thread-id": "t-42",
                          "cwd": "/home/you/code/tidebook", "input-messages": ["hi"],
                          "last-assistant-message": "Done."})
    done = _module_cli("hook", "codex", payload, env=env)
    assert (done.returncode, done.stdout) == (0, "")
    assert _doc(tmp_path, "codex-t-42")["state"] == "ok"


def test_claude_hook_as_a_real_process(tmp_path):
    env = {**os.environ, "AGENT_STATUS_DIR": str(tmp_path)}
    payload = json.dumps({"session_id": "s-77", "cwd": "/home/you/code/paper-kite",
                          "hook_event_name": "Notification", "message": "Needs approval",
                          "notification_type": "permission_prompt"})
    done = subprocess.run([sys.executable, "-m", "lamptown.status", "hook", "claude-code"],
                          input=payload, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=60, check=False)
    assert (done.returncode, done.stdout) == (0, "")
    assert _doc(tmp_path, "claude-code-s-77")["state"] == "waiting"


# ---------------------------------------------------------------- run never blocked by the lamp
def test_run_still_runs_the_command_when_the_status_folder_is_unwritable(tmp_path, capfd):
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    marker = tmp_path / "ran.txt"
    script = f"import pathlib, sys; pathlib.Path({str(marker)!r}).write_text('ran'); sys.exit(5)"
    code = main(["run", "--id", "job", "--dir", str(blocker / "sub"), "--",
                 sys.executable, "-c", script])
    assert code == 5
    assert marker.read_text() == "ran"
    err = capfd.readouterr().err
    assert err.count("can't update the status file") == 1  # warned once, not per write


def test_a_failing_final_write_keeps_the_exit_code(tmp_path, monkeypatch, capfd):
    from lamptown.status.adapters import wrapper

    real = wrapper.update_status

    def flaky(status_id, directory=None, **changes):
        if changes.get("state") in ("ok", "error"):
            raise PermissionError("disk went read-only")
        return real(status_id, directory, **changes)

    monkeypatch.setattr(wrapper, "update_status", flaky)
    for want in (0, 4):
        code = main(["run", "--id", "job", "--dir", str(tmp_path), "--",
                     sys.executable, "-c", f"raise SystemExit({want})"])
        assert code == want
    assert "disk went read-only" in capfd.readouterr().err


def test_a_bad_id_is_a_usage_error_before_anything_runs(tmp_path, capsys):
    marker = tmp_path / "ran.txt"
    script = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran')"
    assert main(["run", "--id", "../x", "--dir", str(tmp_path), "--",
                 sys.executable, "-c", script]) == 2
    assert not marker.exists()
    assert "bad id" in capsys.readouterr().err


def test_exit_codes_of_signal_killed_children():
    from lamptown.status.adapters.wrapper import exit_code

    assert exit_code(0) == 0 and exit_code(3) == 3
    assert exit_code(-15) == 143 and exit_code(-9) == 137 and exit_code(-2) == 130


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_run_maps_a_signal_kill_to_128_plus_n(tmp_path):
    script = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
    code = main(["run", "--id", "sig", "--dir", str(tmp_path), "--", sys.executable, "-c", script])
    assert code == 143
    assert _doc(tmp_path, "sig")["message"].startswith("Exit 143")


class _FakeProc:
    def __init__(self, plan):
        self.plan = list(plan)  # what each wait() does: an exception to raise, or a return code
        self.terminated = self.killed = False

    def wait(self, timeout=None):
        step = self.plan.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_ctrl_c_lets_the_command_stop_by_itself():
    from lamptown.status.adapters.wrapper import _wait

    proc = _FakeProc([KeyboardInterrupt(), 0])
    assert _wait(proc) == (0, True)
    assert not proc.terminated
    proc = _FakeProc([KeyboardInterrupt(), 3221225786 if sys.platform == "win32" else -2])
    assert _wait(proc)[1] is True


def test_a_second_ctrl_c_or_a_stuck_command_is_ended_with_130():
    from lamptown.status.adapters.wrapper import EXIT_INTERRUPTED, _wait

    proc = _FakeProc([KeyboardInterrupt(), KeyboardInterrupt(), 1])
    assert _wait(proc) == (EXIT_INTERRUPTED, True) and proc.terminated
    stuck = _FakeProc([KeyboardInterrupt(), subprocess.TimeoutExpired("x", 10),
                       subprocess.TimeoutExpired("x", 5), 1])
    assert _wait(stuck) == (EXIT_INTERRUPTED, True) and stuck.terminated and stuck.killed


# ---------------------------------------------------------------- clear
def _aged(folder: Path, status_id: str, state: str, days: float, **extra) -> Path:
    from datetime import timedelta

    from lamptown.status.store import iso, utcnow

    path = folder / f"{status_id}.json"
    folder.mkdir(parents=True, exist_ok=True)
    doc = {"spec": "agent-status/1", "id": status_id, "title": status_id, "state": state,
           "updated": iso(utcnow() - timedelta(days=days)), **extra}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _left(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir()}


def test_clear_by_default_removes_what_the_town_already_hides(tmp_path, capsys):
    _aged(tmp_path, "ok-new", "ok", 0.5)
    _aged(tmp_path, "ok-old", "ok", 2)
    _aged(tmp_path, "wait-new", "waiting", 6)
    _aged(tmp_path, "wait-old", "waiting", 8)
    _aged(tmp_path, "work-old", "working", 30)
    _aged(tmp_path, "daemon", "working", 30, stale_after=0)
    assert main(["clear", "--dir", str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would remove ok-old.json" in out and "would remove 3 status file(s)" in out
    assert len(_left(tmp_path)) == 6  # a dry run touches nothing
    assert main(["clear", "--dir", str(tmp_path)]) == 0
    assert _left(tmp_path) == {"ok-new.json", "wait-new.json", "daemon.json"}
    capsys.readouterr()
    assert main(["list", "--dir", str(tmp_path)]) == 0
    assert "working  daemon" in capsys.readouterr().out


def test_list_shows_quiet_files_as_quiet(tmp_path, capsys):
    _aged(tmp_path, "gone", "working", 1)
    assert main(["list", "--dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.startswith("quiet    gone")


def test_clear_older_than_and_all(tmp_path):
    _aged(tmp_path, "a", "working", 0.01)
    _aged(tmp_path, "b", "error", 0.5)
    _aged(tmp_path, "c", "ok", 3)
    assert main(["clear", "--dir", str(tmp_path), "--older-than", "6h"]) == 0
    assert _left(tmp_path) == {"a.json"}
    assert main(["clear", "--dir", str(tmp_path), "--all"]) == 0
    assert _left(tmp_path) == set()


def test_clear_only_touches_its_own_files(tmp_path, capsys):
    _aged(tmp_path, "mine", "ok", 30)
    (tmp_path / "notes.json").write_text('{"hello": 1}', encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    _aged(tmp_path, "renamed", "ok", 30).rename(tmp_path / "other-name.json")
    (tmp_path / "readme.txt").write_text("keep", encoding="utf-8")
    (tmp_path / ".hidden.json").write_text("{}", encoding="utf-8")
    _aged(tmp_path / "sub", "below", "ok", 30)
    assert main(["clear", "--dir", str(tmp_path), "--all"]) == 0
    assert _left(tmp_path) == {"notes.json", "broken.json", "other-name.json", "readme.txt",
                               ".hidden.json", "sub"}
    assert _left(tmp_path / "sub") == {"below.json"}
    assert "left 3 file(s) that aren't status files alone" in capsys.readouterr().out


def test_clear_leaves_a_file_rewritten_while_it_looked(tmp_path, monkeypatch):
    from lamptown.status import store

    path = _aged(tmp_path, "busy", "ok", 30)
    real = store.owned_status_file

    def and_rewrite(p):
        doc = real(p)
        p.write_text(json.dumps({**doc, "state": "working", "title": "busy again!"}),
                     encoding="utf-8")
        return doc

    monkeypatch.setattr(store, "owned_status_file", and_rewrite)
    removed, _foreign, _failed = store.clear_files(tmp_path, lambda _doc: True)
    assert removed == [] and path.exists()


@pytest.mark.parametrize("bad", ["soon", "-1d", "7y", ""])
def test_clear_rejects_bad_durations(tmp_path, bad):
    with pytest.raises(SystemExit) as exc:
        main(["clear", "--dir", str(tmp_path), "--older-than", bad])
    assert exc.value.code == 2


def test_clear_a_missing_folder_is_an_error(tmp_path, capsys):
    assert main(["clear", "--dir", str(tmp_path / "nope")]) == 1
    assert "can't read" in capsys.readouterr().err
