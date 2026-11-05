"""The Claude Code reader (``readers/claude_code.py``) on a made-up ``~/.claude`` tree.

Every log line here is invented. An autouse guard fails any test that touches the real
``~/.claude``.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lamptown.status import events as ev
from lamptown.status.adapters import claude_code as hook
from lamptown.status.adapters import make_id
from lamptown.status.readers import claude_code as cc
from lamptown.status.readers import make_reader
from lamptown.status.readers.tail import MAX_LINE, PER_FILE, Budget, cursor_key, start_at, tail
from lamptown.status.store import read_status, status_path, update_status
from lamptown.status.watch import Watcher

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC)
CWD = "/work/paper-kite"
SID = "3f9a2b1c-1d2e-4f5a-8b9c-0d1e2f3a4b5c"
SID2 = "7c6b5a49-3827-4165-9f8e-7d6c5b4a3928"
PID = 4242
REAL_CLAUDE = os.path.normcase(os.path.abspath(os.path.expanduser("~/.claude")))


# ---------------------------------------------------------------- the guard
@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch, tmp_path):
    """Point every home variable at a temp folder and fail on any touch of the real
    ``~/.claude`` (open, stat, listing)."""
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("AGENT_STATUS_DIR", str(tmp_path / "agent-status"))

    def check(path):
        if isinstance(path, int) or path is None:
            return
        try:
            text = os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(path))))
        except TypeError:
            return
        if text == REAL_CLAUDE or text.startswith(REAL_CLAUDE + os.sep):
            raise AssertionError("a test touched the real ~/.claude")

    def wrap(fn):
        def guarded(path=".", *args, **kwargs):
            check(path)
            return fn(path, *args, **kwargs)
        return guarded

    for mod, name in ((builtins, "open"), (io, "open"), (os, "open"), (os, "stat"),
                      (os, "lstat"), (os, "scandir"), (os, "listdir")):
        monkeypatch.setattr(mod, name, wrap(getattr(mod, name)))


def test_the_guard_trips_on_the_real_folder():
    with pytest.raises(AssertionError, match="real"):
        os.stat(os.path.join(REAL_CLAUDE, "sessions"))


def test_default_root_is_under_the_test_home(tmp_path):
    reader = cc.ClaudeCodeReader()
    assert reader.root == tmp_path / "home" / ".claude"
    assert reader.poll({}, T0).agents == []  # nothing there: nothing found, no error


# ---------------------------------------------------------------- the fixture tree
def ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def ms(seconds: float) -> int:
    return int((T0 + timedelta(seconds=seconds)).timestamp() * 1000)


def line(doc: dict) -> str:
    return json.dumps(doc, separators=(",", ":")) + "\n"


def use(tid: str, name: str, **inp) -> dict:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp, "caller": {}}


def assistant(t: float, *blocks: dict, stop: str = "tool_use", error: bool = False,
              sid: str = SID) -> dict:
    doc = {"parentUuid": None, "isSidechain": False,
           "message": {"model": "m", "id": "msg", "type": "message", "role": "assistant",
                       "content": list(blocks) or [{"type": "text", "text": "Made-up reply."}],
                       "stop_reason": stop},
           "type": "assistant", "cwd": CWD, "sessionId": sid, "timestamp": ts(t)}
    if error:
        doc["isApiErrorMessage"] = True
    return doc


def results(t: float, *ids: str, mode: str = "default", sid: str = SID) -> dict:
    return {"parentUuid": None, "isSidechain": False, "promptId": "p", "type": "user",
            "message": {"role": "user", "content": [
                {"tool_use_id": i, "type": "tool_result", "content": "ok"} for i in ids]},
            "permissionMode": mode, "cwd": CWD, "sessionId": sid, "timestamp": ts(t)}


def prompt(t: float, sid: str = SID) -> dict:
    return {"parentUuid": None, "isSidechain": False, "promptId": "p", "type": "user",
            "message": {"role": "user", "content": "An invented request."},
            "cwd": CWD, "sessionId": sid, "timestamp": ts(t)}


def title(kind: str, text: str, sid: str = SID) -> dict:
    key = {"custom-title": "customTitle", "ai-title": "aiTitle"}[kind]
    return {"type": kind, key: text, "sessionId": sid}


class Tree:
    """A made-up ``~/.claude``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "sessions").mkdir(parents=True)
        self.project = root / "projects" / "-work-paper-kite"
        self.project.mkdir(parents=True)

    def log(self, sid: str = SID) -> Path:
        return self.project / f"{sid}.jsonl"

    def write(self, *docs: dict, sid: str = SID) -> Path:
        path = self.log(sid)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.writelines(line(d) for d in docs)
        return path

    def session(self, status: str = "busy", *, pid: int = PID, sid: str = SID,
                since: float = 0, started: float = -600, name: str = "",
                source: str = "auto", proc_start: str | None = None) -> Path:
        doc = {"pid": pid, "sessionId": sid, "cwd": CWD, "startedAt": ms(started),
               "procStart": proc_start or "1", "version": "9.9.9", "kind": "interactive",
               "entrypoint": "cli", "name": name, "nameSource": source, "status": status,
               "updatedAt": ms(since), "statusUpdatedAt": ms(since)}
        path = self.root / "sessions" / f"{pid}.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def sub(self, agent: str, *docs: dict, sid: str = SID, finished: bool | None = None,
            description: str = "Map the save code", parent: str | None = None,
            tool_use: str = "t") -> None:
        folder = self.project / sid / "subagents"
        folder.mkdir(parents=True, exist_ok=True)
        if docs:
            with open(folder / f"agent-{agent}.jsonl", "a", encoding="utf-8",
                      newline="\n") as fh:
                fh.writelines(line({**d, "agentId": agent, "isSidechain": True}) for d in docs)
        if finished is not None:
            meta = {"agentType": "general-purpose", "isBuiltIn": True,
                    "description": description, "toolUseId": tool_use, "spawnDepth": 1,
                    "finished": finished}
            if parent:
                meta["parentAgentId"] = parent
            (folder / f"agent-{agent}.meta.json").write_text(json.dumps(meta), "utf-8")


@pytest.fixture
def tree(tmp_path) -> Tree:
    return Tree(tmp_path / "claude")


@pytest.fixture
def procs(monkeypatch):
    """Fake processes: pid -> (alive, start time). Unknown pids are dead."""
    table: dict[int, tuple[bool, float | None]] = {}
    monkeypatch.setattr(cc, "process_info", lambda pid: table.get(pid, (False, None)))
    return table


def alive(procs, pid: int = PID, started: float = -601) -> None:
    procs[pid] = (True, (T0 + timedelta(seconds=started)).timestamp())


def reader_for(tree: Tree, **kw) -> cc.ClaudeCodeReader:
    reader = cc.ClaudeCodeReader(tree.root, **kw)
    reader.rescan = 0  # list the tree on every poll (tests add files between polls)
    return reader


def poll(reader, state, now=T0, budget=None):
    batch = reader.poll(state, now, budget)
    return {a["id"]: a for a in batch.agents}, batch.events


MAIN = make_id("claude-code", SID)


# ---------------------------------------------------------------- registry and ids
def test_registered_as_a_builtin_kind(tree):
    reader = make_reader({"kind": "claude_code", "root": str(tree.root), "on_stop": "waiting"})
    assert reader.name == "claude-code"
    assert reader.root == tree.root and reader.on_stop == "waiting"


def test_same_id_as_the_hook():
    update = hook.translate({"hook_event_name": "UserPromptSubmit", "session_id": SID,
                             "cwd": CWD})
    assert update is not None and update.id == MAIN


# ---------------------------------------------------------------- live states
def test_busy_live_session_is_working(tree, procs):
    alive(procs)
    tree.session("busy", since=-5)
    tree.write(title("ai-title", "Fix save slots"), prompt(-10),
               assistant(-5, use("t1", "Bash", command="x")))
    state: dict = {}
    agents, events = poll(reader_for(tree), state)
    rec = agents[MAIN]
    assert rec["state"] == "working" and rec["message"] == "Working"
    assert rec["title"] == "Fix save slots"
    assert rec["x-folder"] == CWD and rec["x-project"] == "paper-kite"
    assert rec["stale_after"] == cc.LIVE_STALE and rec["agent"] == "claude-code"
    assert rec["at"] == ts(-5) and rec["started"] == ts(-600)
    assert [(e["k"], e["t"]) for e in events] == [("start", ts(-600))]
    assert events[0]["folder"] == CWD


def test_custom_title_beats_ai_title_and_titles_change(tree, procs):
    alive(procs)
    tree.session("busy")
    tree.write(title("ai-title", "Auto name"), title("custom-title", "Save slots"),
               title("ai-title", "Later auto name"))
    reader, state = reader_for(tree), {}
    assert poll(reader, state)[0][MAIN]["title"] == "Save slots"
    tree.write(title("custom-title", "Save slots, part two"))
    assert poll(reader, state)[0][MAIN]["title"] == "Save slots, part two"
    assert poll(reader, state)[0] == {}  # nothing new: nothing sent


def test_session_name_set_by_the_user_beats_ai_title(tree, procs):
    alive(procs)
    tree.session("busy", name="Kite night shift", source="user")
    tree.write(title("ai-title", "Auto name"), prompt(-1))
    assert poll(reader_for(tree), {})[0][MAIN]["title"] == "Kite night shift"


def test_no_title_falls_back_to_the_folder_name(tree, procs):
    alive(procs)
    tree.session("idle")
    tree.write(prompt(-1))
    assert poll(reader_for(tree), {})[0][MAIN]["title"] == "paper-kite"


def test_idle_is_ok_or_waiting_with_on_stop(tree, procs):
    alive(procs)
    tree.session("idle", since=-2)
    tree.write(prompt(-10), assistant(-2, stop="end_turn"))
    rec = poll(reader_for(tree), {})[0][MAIN]
    assert (rec["state"], rec["message"], rec["needs_you"]) == ("ok", "Turn finished", None)
    rec = poll(reader_for(tree, on_stop="waiting"), {})[0][MAIN]
    assert rec["state"] == "waiting" and rec["needs_you"] == [{"text": "Your turn"}]


def test_a_question_waits_until_answered(tree, procs):
    alive(procs)
    tree.session("busy")
    tree.write(prompt(-10), assistant(-5, use("q1", "AskUserQuestion", questions=[])))
    reader, state = reader_for(tree), {}
    rec = poll(reader, state)[0][MAIN]
    assert rec["state"] == "waiting" and rec["message"] == "Has a question for you"
    assert rec["needs_you"] == [{"text": "Has a question for you"}]
    tree.write(results(5, "q1"))
    rec = poll(reader, state, T0 + timedelta(seconds=6))[0][MAIN]
    assert rec["state"] == "working" and rec["needs_you"] is None


def test_a_quick_tool_without_result_reads_as_a_permission_prompt(tree, procs):
    alive(procs)
    tree.session("busy")
    tree.write(prompt(-10), results(-9, mode="default"),
               assistant(0, use("e1", "Edit", file_path=f"{CWD}/save.py", old_string="a",
                                new_string="b")))
    reader, state = reader_for(tree), {}
    assert poll(reader, state, T0 + timedelta(seconds=10))[0][MAIN]["state"] == "working"
    rec = poll(reader, state, T0 + timedelta(seconds=31))[0][MAIN]
    assert rec["state"] == "waiting" and rec["message"] == "May need your permission"
    tree.write(results(32, "e1"))
    assert poll(reader, state, T0 + timedelta(seconds=33))[0][MAIN]["state"] == "working"


def test_no_permission_guess_when_permissions_are_bypassed(tree, procs):
    alive(procs)
    tree.session("busy")
    tree.write(prompt(-10), results(-9, mode="bypassPermissions"),
               assistant(0, use("e1", "Edit", file_path=f"{CWD}/save.py")))
    rec = poll(reader_for(tree), {}, T0 + timedelta(seconds=300))[0][MAIN]
    assert rec["state"] == "working"


def test_a_long_bash_is_still_working(tree, procs):
    alive(procs)
    tree.session("busy")
    tree.write(prompt(-10), assistant(0, use("b1", "Bash", command="make")))
    rec = poll(reader_for(tree), {}, T0 + timedelta(seconds=900))[0][MAIN]
    assert rec["state"] == "working"


def test_a_waiting_status_in_the_session_file(tree, procs):
    alive(procs)
    tree.session("waiting_for_input")
    tree.write(prompt(-1))
    assert poll(reader_for(tree), {})[0][MAIN]["state"] == "waiting"


def test_api_error_fails_until_the_next_prompt(tree, procs):
    alive(procs)
    tree.session("idle", since=-1)
    tree.write(prompt(-10), assistant(-1, error=True, stop="stop_sequence"))
    reader, state = reader_for(tree), {}
    rec = poll(reader, state)[0][MAIN]
    assert rec["state"] == "error" and rec["message"] == "Stopped by an API error"
    tree.session("busy", since=5)
    tree.write(prompt(5))
    assert poll(reader, state, T0 + timedelta(seconds=6))[0][MAIN]["state"] == "working"


def test_a_same_size_session_rewrite_in_one_time_tick_is_still_seen(tree, procs):
    """ci-pr1: "idle" -> "busy" keeps the session file's size; a rewrite within the file
    system's time tick (coarse on Windows) keeps its mtime too. It must still be read."""
    alive(procs)
    path = tree.session("idle", since=-1)
    tree.write(prompt(-10), assistant(-1, error=True, stop="stop_sequence"))
    reader, state = reader_for(tree), {}
    assert poll(reader, state)[0][MAIN]["state"] == "error"
    before = os.stat(path)
    tree.session("busy", since=-1)  # same size
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))  # same tick: same mtime
    after = os.stat(path)
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)
    tree.write(prompt(5))
    assert poll(reader, state, T0 + timedelta(seconds=6))[0][MAIN]["state"] == "working"


def test_a_settled_session_file_is_not_read_again(tree, procs, monkeypatch):
    """The (mtime, size) cache still holds for a session file not changed just now."""
    alive(procs)
    path = tree.session("busy")
    old = os.stat(path).st_mtime_ns - 60 * 10**9
    os.utime(path, ns=(old, old))
    tree.write(prompt(-10))
    reader, state = reader_for(tree), {}
    poll(reader, state)
    reads: list[str] = []
    real = cc._read_small_json
    monkeypatch.setattr(cc, "_read_small_json", lambda p: reads.append(str(p)) or real(p))
    poll(reader, state, T0 + timedelta(seconds=1))
    assert reads == []


# ---------------------------------------------------------------- the process
def test_dead_process_idle_is_done(tree, procs):
    tree.session("idle", since=-30)  # no live process in the table
    tree.write(prompt(-60), assistant(-30, stop="end_turn"))
    rec = poll(reader_for(tree), {})[0][MAIN]
    assert (rec["state"], rec["message"], rec["stale_after"]) == ("ok", "Session ended", None)


def test_dead_process_busy_stops_responding_then_goes_quiet(tree, procs, tmp_path):
    tree.session("busy", since=-30)
    tree.write(prompt(-60), assistant(-30, use("b1", "Bash", command="x")))
    rec = poll(reader_for(tree), {})[0][MAIN]
    assert (rec["state"], rec["message"]) == ("working", "Ended mid-turn")
    assert rec["stale_after"] == cc.DEAD_STALE
    # what the town makes of it: red "stopped responding" now, grey "gone quiet" later
    status = tmp_path / "status"
    with Watcher(status, [reader_for(tree)], tz=UTC) as w:
        w.tick(T0)  # its log moved 30 s ago: it died just now (L4-fix M3: "at" is now)
        w.tick(T0 + timedelta(seconds=61))
        later = w.tick(T0 + timedelta(hours=7))
    logged = [e["k"] for _, es in ev.read_days(status, 1, now=T0, tz=UTC) for e in es
              if e["k"] != "project"]
    assert logged == ["start", "fail", "quiet"]
    assert later.errors == []


def test_a_reused_pid_is_not_the_session(tree, procs):
    procs[PID] = (True, (T0 + timedelta(hours=1)).timestamp())  # a newer process, same pid
    tree.session("busy", since=-30)
    tree.write(prompt(-60), assistant(-30, use("b1", "Bash", command="x")))
    rec = poll(reader_for(tree), {}, T0 + timedelta(hours=2))[0][MAIN]
    assert rec["message"] == "Ended mid-turn"


def test_session_file_removed_means_ended(tree, procs):
    alive(procs)
    path = tree.session("busy")
    tree.write(prompt(-10), assistant(-5, use("b1", "Bash", command="x")))
    reader, state = reader_for(tree), {}
    assert poll(reader, state)[0][MAIN]["state"] == "working"
    os.replace(path, tree.root / "gone.json")  # Claude Code removes it on exit
    rec = poll(reader, state, T0 + timedelta(seconds=3))[0][MAIN]
    assert (rec["state"], rec["message"]) == ("ok", "Session ended")


def test_a_new_session_without_a_log_yet(tree, procs):
    alive(procs)
    tree.session("idle", started=-1)
    rec = poll(reader_for(tree), {})[0][MAIN]
    assert rec["state"] == "ok" and rec["title"] == "paper-kite"


def test_same_process_rules():
    started = ms(0)
    start = (T0 - timedelta(seconds=1)).timestamp()
    assert cc.same_process(start, started, None, nt=False)
    assert cc.same_process(None, started, None, nt=False)  # unknown start: trust alive
    assert not cc.same_process(start + 3600, started, None, nt=False)
    filetime = str(int(start * 1e7) + cc._EPOCH_FILETIME)
    assert cc.same_process(start, started, filetime, nt=True)
    assert not cc.same_process(start - 30, started, filetime, nt=True)  # another process
    assert cc.same_process(start, started, "123", nt=True)  # not a plausible FILETIME


def test_process_info_on_this_machine(monkeypatch):
    if os.name == "nt":  # on Windows os.kill(pid, 0) would end the process: never call it
        def boom(*a):
            raise AssertionError("os.kill on Windows")
        monkeypatch.setattr(os, "kill", boom)
    ok, start = cc.process_info(os.getpid())
    assert ok
    if start is not None:
        assert start <= datetime.now(UTC).timestamp()
    assert cc.process_info(0) == (False, None) and cc.process_info(-1) == (False, None)
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert cc.process_info(child.pid)[0] is False


# ---------------------------------------------------------------- files
def test_changed_files_count_once_and_only_as_hashes(tree, procs):
    alive(procs)
    tree.session("busy")
    tree.write(
        prompt(-100),
        assistant(-90, use("a", "Write", file_path=f"{CWD}/save.py", content="x")),
        assistant(-80, use("b", "Edit", file_path=f"{CWD}/save.py", old_string="x",
                           new_string="y")),
        assistant(-70, use("c", "MultiEdit", file_path=f"{CWD}/load.py", edits=[])),
        assistant(-10, use("d", "NotebookEdit", notebook_path=f"{CWD}/kite.ipynb")),
        assistant(-9, use("e", "Read", file_path=f"{CWD}/other.py")),  # reading isn't a change
        assistant(-8, use("f", "Edit", file_path="\\\\KITE-SHARE\\Work\\A.py")),
        assistant(-7, use("g", "Edit", file_path="//kite-share/work/a.py")),  # Windows: same
        {"type": "api-request-blob", "sessionId": SID, "hash": "h",  # a copy: not counted
         "message": {"role": "assistant", "content": [use("z", "Write", file_path="/b.py")]}},
    )
    reader, state = reader_for(tree), {}
    agents, events = poll(reader, state)
    assert agents[MAIN]["x-files"] == 4
    files = [(e["t"], e["n"]) for e in events if e["k"] == "files"]
    assert files == [(ts(-90), 2), (ts(-10), 2)]  # one event per minute: 08:58 and 08:59
    text = json.dumps(state)
    assert "paper-kite" not in text and "save.py" not in text
    assert "kite-share" not in text.lower() and "work" not in text.lower()
    tree.write(assistant(1, use("h", "Write", file_path=f"{CWD}/save.py")),
               assistant(2, use("i", "Write", file_path=f"{CWD}/new.py")))
    agents, events = poll(reader, state, T0 + timedelta(seconds=3))
    assert agents[MAIN]["x-files"] == 5
    assert [(e["k"], e["n"]) for e in events] == [("files", 1)]


def test_salt_differs_per_install(tree, procs):
    alive(procs)
    tree.session("busy")
    tree.write(assistant(-1, use("a", "Write", file_path=f"{CWD}/save.py")))
    one, two = {}, {}
    poll(reader_for(tree), one)
    poll(reader_for(tree), two)
    assert one["s"][SID]["fh"] != two["s"][SID]["fh"]


# ---------------------------------------------------------------- big lines
def test_a_big_line_is_never_parsed(tree, procs, monkeypatch):
    alive(procs)
    tree.session("busy")
    big = assistant(-5, use("w1", "Write", file_path=f"{CWD}/huge.txt",
                            content="x" * (300 * 1024)))
    blob = {"type": "api-request-blob", "sessionId": SID, "hash": "h",
            "message": {"role": "assistant", "content": [
                use("w9", "Write", file_path=f"{CWD}/copy.txt", content="y" * (300 * 1024))]}}
    tree.write(prompt(-10), big, blob)
    parsed = []
    real = json.loads
    monkeypatch.setattr(cc.json, "loads", lambda s, *a, **k: parsed.append(len(s)) or real(
        s, *a, **k))
    reader, state = reader_for(tree), {}
    poll(reader, state, T0 + timedelta(seconds=40))  # 512 KB a poll: still catching up
    rec = poll(reader, state, T0 + timedelta(seconds=40))[0][MAIN]
    assert rec["x-files"] == 1  # the regex found the Write; the blob copy was skipped
    assert rec["state"] == "waiting"  # its Write is still waiting for a result after 40 s
    assert max(parsed) < 256 * 1024
    huge_result = {**results(41, "w1"), "toolUseResult": "z" * (300 * 1024)}
    tree.write(huge_result)
    assert poll(reader, state, T0 + timedelta(seconds=42))[0][MAIN]["state"] == "working"


# ---------------------------------------------------------------- subagents
def test_subagents_are_child_records(tree, procs):
    alive(procs)
    tree.session("busy")
    tree.write(prompt(-100), assistant(-90, use("ag", "Agent", prompt="x")))
    tree.sub("a1", prompt(-80), assistant(-70, use("w", "Write", file_path=f"{CWD}/x.py")),
             assistant(-60, stop="end_turn"), finished=True, description="Map the save code")
    tree.sub("a2", prompt(-50), assistant(-40, use("b", "Bash", command="x")),
             finished=False, description="Run the kite tests")
    tree.sub("a3", prompt(-30), finished=False, description="Nested helper", parent="a2")
    agents, events = poll(reader_for(tree), {})
    one, two, three = (agents[cc.sub_id(SID, a)] for a in ("a1", "a2", "a3"))
    assert (one["state"], one["title"], one["x-parent"]) == ("ok", "Map the save code", MAIN)
    assert one["x-files"] == 1 and one["at"] == ts(-60) and one["x-project"] == "paper-kite"
    assert (two["state"], two["x-parent"]) == ("working", MAIN)
    assert three["x-parent"] == cc.sub_id(SID, "a2")
    assert MAIN in agents and "x-files" not in agents[MAIN]  # a subagent's files are its own
    starts = sorted(e["id"] for e in events if e["k"] == "start")
    assert starts == sorted([MAIN, *(cc.sub_id(SID, a) for a in ("a1", "a2", "a3"))])


def test_finished_subagent_is_skipped_until_it_changes(tree, procs):
    alive(procs)
    tree.session("busy")
    tree.write(prompt(-100), assistant(-90, use("ag", "Agent", prompt="x")))
    tree.sub("a1", prompt(-80), assistant(-70, use("w", "Write", file_path=f"{CWD}/x.py")),
             assistant(-60, stop="end_turn"), finished=True)
    reader, state = reader_for(tree), {}
    one = cc.sub_id(SID, "a1")
    assert poll(reader, state)[0][one]["x-files"] == 1
    assert one not in poll(reader, state)[0]  # nothing new: no change sent
    tree.sub("a1", assistant(-20, use("w2", "Edit", file_path=f"{CWD}/y.py")),
             assistant(-10, stop="end_turn"))
    assert poll(reader, state)[0][one]["x-files"] == 2  # written again: read and sent


def test_subagent_of_an_ended_session_ends(tree, procs):
    tree.write(prompt(-4000), assistant(-3990, stop="end_turn"))
    tree.sub("a1", prompt(-3995), finished=False)
    agents, _ = poll(reader_for(tree), {})
    assert agents[MAIN]["state"] == "ok"
    assert agents[cc.sub_id(SID, "a1")]["state"] == "ok"
    assert agents[cc.sub_id(SID, "a1")]["message"] == "Ended"


# ---------------------------------------------------------------- history and catching up
def test_old_logs_are_never_opened(tree, procs):
    path = tree.write(prompt(-100), assistant(-90, stop="end_turn"))
    old = (T0 - timedelta(days=8)).timestamp()
    os.utime(path, (old, old))
    state: dict = {}
    reader = reader_for(tree)
    assert poll(reader, state) == ({}, [])
    assert cursor_key(path) not in state["files"] and reader.read_bytes == 0


def test_a_log_without_a_session_file_recent_and_not(tree, procs):
    tree.write(prompt(-60), assistant(-30, use("b", "Bash", command="x")))
    reader, state = reader_for(tree), {}
    assert poll(reader, state)[0][MAIN]["state"] == "working"  # moved 30 s ago, mid-turn
    rec = poll(reader, state, T0 + timedelta(seconds=600))[0][MAIN]
    assert (rec["state"], rec["message"]) == ("ok", "Session ended")


def _fifty_mb(tree: Tree, files: int = 120) -> tuple[int, int]:
    """A made-up 50 MB log shaped like a real one: mostly lines of a few KB (request copies),
    one line over 256 KB in every 400, and a few file writes early on. Returns (size, number
    of lines over 256 KB)."""
    filler = line({"type": "api-request-blob", "sessionId": SID, "hash": "h",
                   "message": {"role": "user", "content": "q" * (6 * 1024)}})
    huge = line({**results(-6000, "none"), "toolUseResult": "r" * (300 * 1024)})
    path, n_huge = tree.log(), 0
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(line(title("custom-title", "Long night")))
        fh.write(line(prompt(-7200)))
        i = 0
        while fh.tell() < 50 * 1024 * 1024:
            if i < files:
                fh.write(line(assistant(-7000 + i * 10,
                                        use(f"w{i}", "Write", file_path=f"{CWD}/f{i}.py"))))
                fh.write(line(results(-6995 + i * 10, f"w{i}")))
            if i % 400 == 399:
                fh.write(huge)
                n_huge += 1
            else:
                fh.write(filler)
            i += 1
        fh.write(line(assistant(-3700, stop="end_turn")))
    return path.stat().st_size, n_huge


def _reread_bound(size: int, polls: int, n_huge: int) -> int:
    """What a read-once may cost: ``tail`` hands out whole lines only, so a line the budget
    cuts is read again, whole, next poll: at most one ordinary line (about 6 KB here) a poll,
    and each line over 256 KB at most once (up to 256 KB of it)."""
    return size + polls * 8 * 1024 + n_huge * MAX_LINE + cc.HEAD_SCAN


def test_a_50mb_history_log_is_read_within_the_budget_once(tree, procs):
    size, n_huge = _fifty_mb(tree)
    reader, state = reader_for(tree), {}
    spent, polls, rec, events = 0, 0, None, []
    while rec is None and polls < 400:
        budget = Budget()
        agents, found = poll(reader, state, T0, budget)
        polls += 1
        assert reader.read_bytes <= budget.per_file  # one file: at most 512 KB a poll
        spent += reader.read_bytes
        events += found
        rec = agents.get(MAIN)
    assert rec is not None and size / PER_FILE <= polls <= size / PER_FILE * 1.2
    assert spent <= _reread_bound(size, polls, n_huge)  # never from the start again
    assert rec["x-files"] == 120 and rec["title"] == "Long night" and rec["state"] == "ok"
    assert rec["at"] == ts(-3700) and rec["started"] == ts(-7200)
    assert sum(e["n"] for e in events if e["k"] == "files") == 120
    assert [e["t"] for e in events if e["k"] == "start"] == [ts(-7200)]
    for _ in range(3):  # caught up: nothing more to read or send
        assert poll(reader, state) == ({}, [])
        assert reader.read_bytes == 0


def test_a_big_live_log_starts_near_its_end_and_backfills(tree, procs):
    size, n_huge = _fifty_mb(tree)
    alive(procs)
    tree.session("idle", since=-3700)
    reader, state = reader_for(tree), {}
    budget = Budget()
    agents, _ = poll(reader, state, T0, budget)
    # live state at once, reading only the live tail, one backfill chunk and the log's head
    assert reader.read_bytes <= cc.BACK + budget.per_file + cc.HEAD_SCAN and MAIN in agents
    rec = agents[MAIN]
    assert (rec["state"], rec["message"], rec["title"]) == ("ok", "Turn finished", "Long night")
    assert state["s"][SID]["c"] == 0  # the live part (the last BACK bytes) read in one poll
    spent, polls, files = reader.read_bytes, 1, rec.get("x-files", 0)
    while state["s"][SID].get("bf") is not None and polls < 400:
        agents, _ = poll(reader, state, T0, Budget())
        spent += reader.read_bytes
        polls += 1
        if MAIN in agents:
            files = agents[MAIN].get("x-files", files)
    assert files == 120  # the backfill found every earlier file
    # read once: the backfill's last chunk may run up to one per-file budget past its stop
    assert spent <= _reread_bound(size, polls, n_huge) + budget.per_file
    assert state["back"] == {}
    assert poll(reader, state)[0] == {} and reader.read_bytes == 0


# ---------------------------------------------------------------- the Watcher, and restarts
def _logged(status: Path) -> list[dict]:
    return [e for _, es in ev.read_days(status, 1, now=T0, tz=UTC) for e in es]


def test_through_the_watcher_history_has_real_times(tree, procs, tmp_path):
    tree.write(title("custom-title", "Fix save slots"), prompt(-3600),
               assistant(-3500, use("a", "Write", file_path=f"{CWD}/a.py")),
               assistant(-3400, use("b", "Write", file_path=f"{CWD}/b.py")),
               assistant(-3000, stop="end_turn"))
    status = tmp_path / "status"
    with Watcher(status, [reader_for(tree)], tz=UTC) as w:
        result = w.tick(T0)
        w.tick(T0 + timedelta(minutes=2))
    assert result.errors == []
    got = [(e["k"], e["t"], e.get("n")) for e in _logged(status) if e["k"] != "project"]
    assert got == [("start", ts(-3600), None), ("files", ts(-3500), 1),
                   ("files", ts(-3400), 1), ("done", ts(-3000), None)]
    assert {e["title"] for e in _logged(status)} == {"Fix save slots"}
    assert {e["project"] for e in _logged(status)} == {"paper-kite"}
    doc = read_status(status_path(MAIN, status))
    assert doc["x-files"] == 2 and doc["x-folder"] == CWD


def test_restart_from_saved_state_sends_nothing_twice(tree, procs, tmp_path):
    alive(procs)
    tree.session("busy")
    tree.write(prompt(-100), assistant(-90, use("a", "Write", file_path=f"{CWD}/a.py")))
    status = tmp_path / "status"
    with Watcher(status, [reader_for(tree)], tz=UTC) as w:
        w.tick(T0)
        w.tick(T0 + timedelta(minutes=2))
    before = _logged(status)
    tree.write(assistant(200, use("b", "Write", file_path=f"{CWD}/b.py")))
    with Watcher(status, [reader_for(tree)], tz=UTC) as w:  # a new process, saved state
        w.tick(T0 + timedelta(minutes=5))
        w.tick(T0 + timedelta(minutes=7))
    after = _logged(status)
    keys = [ev.event_key(e) for e in after]
    assert len(keys) == len(set(keys))
    assert after[: len(before)] == before
    assert [(e["k"], e.get("n")) for e in after[len(before):]] == [("files", 1)]


def test_restart_in_the_middle_of_catching_up(tree, procs, tmp_path):
    _fifty_mb(tree, files=30)
    status = tmp_path / "status"
    for _ in range(3):  # three short runs, each a few ticks, then a whole one
        with Watcher(status, [reader_for(tree)], tz=UTC) as w:
            for i in range(20):
                w.tick(T0 + timedelta(seconds=i))
    with Watcher(status, [reader_for(tree)], tz=UTC) as w:
        for i in range(120):
            w.tick(T0 + timedelta(seconds=i))
    logged = _logged(status)
    keys = [ev.event_key(e) for e in logged]
    assert len(keys) == len(set(keys))
    assert sum(e["n"] for e in logged if e["k"] == "files") == 30
    assert [e["k"] for e in logged].count("start") == 1


# ---------------------------------------------------------------- tail.start_at
def test_start_at_skips_to_the_next_whole_line(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"a":1}\n{"b":2}\n{"c":3}\n')
    cursors: dict = {}
    start_at(path, cursors, 3)  # mid-line
    assert [ln.json() for ln in tail(path, cursors, Budget()).lines] == [{"b": 2}, {"c": 3}]
    cursors = {}
    start_at(path, cursors, 8)  # a line start
    assert [ln.json() for ln in tail(path, cursors, Budget()).lines] == [{"b": 2}, {"c": 3}]
    cursors = {}
    start_at(path, cursors, 1000)  # past the end: read as truncated, from the start
    result = tail(path, cursors, Budget())
    assert result.reset == "truncated" and len(result.lines) == 3


# ---------------------------------------------------------------- L4-fix: review findings
SECRET = "/work/secret-kite"


def _in(doc: dict, cwd: str) -> dict:
    return {**doc, "cwd": cwd}


def _hook_wrote(status: Path, at: datetime, **fields) -> None:
    """A hook's record (written by someone other than the Watcher) at ``at``."""
    update_status(MAIN, status, now=at, agent="claude-code", kind="task", title="paper-kite",
                  **fields)


def test_b1_private_subagent_after_a_restart_is_never_unmasked(tree, procs, tmp_path):
    # an ended session in a private folder; its finished subagent's log needs several polls
    tree.write(_in(prompt(-4000), SECRET), _in(assistant(-3990, stop="end_turn"), SECRET))
    filler = {"type": "user", "message": {"role": "user", "content": "q" * 4000}}
    tree.sub("a1", _in(prompt(-3980), SECRET), *[filler] * (PER_FILE // 4000 + 40),
             _in(assistant(-3900, stop="end_turn"), SECRET), finished=True,
             description="Audit the Blue Heron contract")
    status = tmp_path / "status"
    with Watcher(status, [reader_for(tree)], tz=UTC, private_dirs=[SECRET]) as w:
        w.tick(T0)
    with Watcher(status, [reader_for(tree)], tz=UTC, private_dirs=[SECRET]) as w:  # restart
        for i in range(1, 4):
            w.tick(T0 + timedelta(seconds=i))
    sub = read_status(status_path(cc.sub_id(SID, "a1"), status))
    assert sub["x-folder"] == SECRET  # so the Watcher can tell it is private
    logged = _logged(status)
    assert logged and "Blue Heron" not in json.dumps(logged)
    assert not any(e["id"] == cc.sub_id(SID, "a1") for e in logged)  # masked ids only


def test_b2_hook_waiting_survives_state_free_and_subagent_lines(tree, procs, tmp_path):
    alive(procs)
    tree.session("busy", since=-60)
    tree.write(prompt(-60), assistant(-3, use("b1", "Bash", command="make deploy")))
    tree.sub("bg1", prompt(-30), finished=False)
    status = tmp_path / "status"
    status.mkdir()
    _hook_wrote(status, T0, state="waiting", message="Needs your permission",
                needs_you=[{"text": "Allow Bash?"}])
    with Watcher(status, [reader_for(tree)], tz=UTC) as w:
        w.tick(T0 + timedelta(seconds=1))
        assert read_status(status_path(MAIN, status))["state"] == "waiting"
        # Claude Code adds a state-free line, and a parallel subagent keeps working
        tree.write({"type": "attachment", "sessionId": SID, "timestamp": ts(2)})
        tree.sub("bg1", assistant(2, use("g1", "Grep", pattern="x")))
        w.tick(T0 + timedelta(seconds=3))
    doc = read_status(status_path(MAIN, status))
    assert doc["state"] == "waiting" and doc["needs_you"] == [{"text": "Allow Bash?"}]


def test_b3_stale_unfinished_subagents_are_ended_not_working(tree, procs, tmp_path):
    alive(procs)
    tree.session("busy", since=-5)
    tree.write(prompt(-11000), assistant(-10900, use("ag1", "Agent", prompt="x")),
               results(-10000, "ag1"), prompt(-700),
               assistant(-600, use("ag2", "Agent", prompt="y")),
               assistant(-5, use("b1", "Bash", command="make")))
    tree.sub("old1", prompt(-10850), finished=False, description="Old helper", tool_use="ag1")
    tree.sub("acompact-1234", prompt(-10800))  # a log without a meta
    tree.sub("wait1", prompt(-590), finished=False, description="Long helper", tool_use="ag2")
    agents, _ = poll(reader_for(tree), {})
    for agent in ("old1", "acompact-1234"):
        rec = agents[cc.sub_id(SID, agent)]
        assert (rec["state"], rec["message"]) == ("ok", "Ended")
    rec = agents[cc.sub_id(SID, "wait1")]  # its launching call still awaits its result
    assert (rec["state"], rec["stale_after"]) == ("working", cc.LIVE_STALE)
    status = tmp_path / "status"
    with Watcher(status, [reader_for(tree)], tz=UTC) as w:
        w.tick(T0)
        w.tick(T0 + timedelta(seconds=30))
    fails = [e for e in _logged(status) if e["k"] == "fail"]
    assert fails == []


def test_m1_an_infinite_or_huge_pid_never_stops_the_poll(tree, procs):
    alive(procs)
    tree.session("busy")
    tree.write(prompt(-1))
    (tree.root / "sessions" / "odd.json").write_text(
        '{"pid": Infinity, "sessionId": "' + SID2 + '", "startedAt": Infinity}', "utf-8")
    (tree.root / "sessions" / "huge.json").write_text(json.dumps(
        {"pid": 2**32 + 4, "sessionId": SID2}), "utf-8")
    agents, _ = poll(reader_for(tree), {})
    assert agents[MAIN]["state"] == "working"
    assert cc.process_info(2**32 + 4) == (False, None)  # never cut to 32 bits (pid 4)
    assert cc._pid(float("inf")) is None and cc._pid(4.0) == 4 and cc._pid(True) is None


@pytest.mark.parametrize("big", [
    {"sessionId": SID, "type": "api-request-blob", "message": {"role": "assistant", "content": [
        use("w1", "Write", file_path=f"{CWD}/a.py", content="y" * (300 * 1024))]}},
    {"parentUuid": None, "type": "progress", "timestamp": ts(-20), "data": {
        "type": "agent_progress", "message": {"type": "assistant", "message": {
            "role": "assistant", "content": [
                use("w1", "Write", file_path=f"{CWD}/a.py", content="y" * (300 * 1024))]}}}},
], ids=["copy-type-not-first", "progress-nesting-a-reply"])
def test_m2_big_copies_leave_no_phantom_pending_tool(tree, procs, big):
    alive(procs)
    tree.session("busy")
    tree.write(prompt(-60), assistant(-50, use("w1", "Write", file_path=f"{CWD}/a.py")),
               results(-49, "w1"), big, assistant(-10, use("b1", "Bash", command="make")))
    reader, state = reader_for(tree), {}
    for k in range(3):
        poll(reader, state, T0 + timedelta(seconds=k))
    assert sorted(state["s"][SID]["pend"]) == ["b1"]
    rec = poll(reader, state, T0 + timedelta(seconds=60))[0].get(MAIN)
    assert rec is None or rec["state"] == "working"  # never "May need your permission"
    assert len(state["s"][SID]["fh"]) == 1


def test_m2_big_role_rules():
    pad = b"x" * 300
    assert cc._big_role(b'{"parentUuid":null,"message":{"model":"m","role":"assistant"'
                        + pad) == b"assistant"
    assert cc._big_role(b'{"type":"user","message":{"role":"user","content":"' + pad) == b"user"
    assert cc._big_role(b'{"sessionId":"s","type":"api-request-blob","message":{"role":'
                        b'"assistant"' + pad) is None
    assert cc._big_role(b'{"type":"progress","data":{"message":{"role":"assistant"'
                        + pad) is None
    assert cc._big_role(b'{"note":"a \\"message\\":{ inside","message":{"role":"user"'
                        + pad) == b"user"


def test_m3_a_process_that_dies_after_a_hook_write_still_ends(tree, procs, tmp_path):
    alive(procs)
    tree.session("busy", since=-30)
    tree.write(prompt(-60), assistant(-30, use("b1", "Bash", command="x")))
    status = tmp_path / "status"
    status.mkdir()
    with Watcher(status, [reader_for(tree)], tz=UTC) as w:
        w.tick(T0)
        # the hook's PreToolUse lands after the log line; then the process dies
        _hook_wrote(status, T0 + timedelta(seconds=1), state="working", message="Using Bash")
        procs.clear()
        result = w.tick(T0 + timedelta(seconds=5))
    assert MAIN not in result.kept
    doc = read_status(status_path(MAIN, status))
    assert (doc["message"], doc["stale_after"]) == ("Ended mid-turn", cc.DEAD_STALE)


def test_m3_a_log_without_session_file_ends_after_a_hook_write(tree, procs, tmp_path):
    tree.write(prompt(-60), assistant(-30, use("b", "Bash", command="x")))
    status = tmp_path / "status"
    status.mkdir()
    with Watcher(status, [reader_for(tree)], tz=UTC) as w:
        w.tick(T0)
        _hook_wrote(status, T0 - timedelta(seconds=29), state="working", message="Using Bash")
        w.tick(T0 + timedelta(seconds=600))
    doc = read_status(status_path(MAIN, status))
    assert (doc["state"], doc["message"]) == ("ok", "Session ended")


def test_m7_prune_forgets_the_memory_caches(tree, procs):
    path = tree.write(prompt(-100), assistant(-90, stop="end_turn"))
    tree.sub("a1", prompt(-80), assistant(-70, stop="end_turn"), finished=True)
    reader, state = reader_for(tree), {}
    poll(reader, state)
    assert reader._metas and reader._sig and reader._done and reader._cwd
    old = (T0 - timedelta(days=8)).timestamp()
    os.utime(path, (old, old))  # too old to watch now
    poll(reader, state)
    assert not (reader._metas or reader._sig or reader._done or reader._cwd or reader._subs)
    assert state["s"] == {} and state["a"] == {}


def test_m8_a_resumed_big_live_log_starts_before_its_history(tree, procs):
    _fifty_mb(tree)
    alive(procs)
    tree.session("idle", since=-3700)
    reader, state = reader_for(tree), {}
    # other live sessions took the rest of the budget: no backfill in this first poll
    _, events = poll(reader, state, T0, Budget(cc.BACK))
    starts = [e["t"] for e in events if e["k"] == "start"]
    assert starts == [ts(-7200)]  # the log's first line, not the last 384 KB
    while state["s"][SID].get("bf") is not None:
        events += poll(reader, state, T0, Budget())[1]
    assert all(e["t"] >= ts(-7200) for e in events if e["k"] == "files")


def test_m9_unknown_start_time_is_alive_only_while_it_moves(tree, procs):
    procs[PID] = (True, None)  # alive, start time unknown (access denied: a protected process)
    tree.session("busy", since=-3 * 3600)
    path = tree.write(prompt(-3 * 3600 - 60),
                      assistant(-3 * 3600, use("b1", "Bash", command="x")))
    old = (T0 - timedelta(hours=3)).timestamp()
    os.utime(path, (old, old))
    rec = poll(reader_for(tree), {})[0][MAIN]
    assert rec["message"] == "Ended mid-turn"
    tree.session("busy", since=-10)  # it moved: trust the alive check again
    assert poll(reader_for(tree), {})[0][MAIN]["message"] == "Working"


def test_home_folder_never_names_the_session(tree, procs, tmp_path):
    home = "C:\\Users\\you"
    tree.write(_in(prompt(-100), home), _in(assistant(-90, stop="end_turn"), home))
    tree.sub("a1", _in(prompt(-95), home), finished=True)
    rec = poll(reader_for(tree), {})[0][MAIN]
    assert rec["title"] == "Claude Code" and rec["x-project"] is None
    assert rec["x-folder"] == home  # kept whole for private_dirs
    status = tmp_path / "status"
    with Watcher(status, [reader_for(tree)], tz=UTC) as w:
        w.tick(T0)
    for sid in (MAIN, cc.sub_id(SID, "a1")):
        doc = read_status(status_path(sid, status))
        assert "x-project" not in doc and doc["x-folder"] == home
    assert "you" not in json.dumps(_logged(status))


def test_m5_titles_folder_option_shows_names_only(tree, procs):
    alive(procs)
    tree.session("busy")
    tree.write(title("ai-title", "Fix the Blue Heron invoice"), prompt(-1))
    tree.sub("a1", prompt(-1), finished=False, description="Audit the Blue Heron contract")
    reader = make_reader({"kind": "claude_code", "root": str(tree.root), "titles": "folder"})
    reader.rescan = 0
    agents, _ = poll(reader, {})
    assert agents[MAIN]["title"] == "paper-kite"
    assert agents[cc.sub_id(SID, "a1")]["title"] == "general-purpose"


# ---------------------------------------------------------------- L4-fix: fast catch-up
def test_budget_deadline_stops_reading(tmp_path):
    clock = [0.0]
    budget = Budget(10_000, 5_000, deadline=1.0, clock=lambda: clock[0])
    assert budget.allow() == 5_000
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"a":1}\n' * 2000)
    cursors: dict = {}
    assert tail(path, cursors, budget).more  # 5000 of 16000 bytes
    clock[0] = 1.0
    assert budget.allow() == 0
    result = tail(path, cursors, budget)
    assert result.lines == [] and result.more  # past the deadline: nothing more read
    big = Budget.catch_up(clock=lambda: 5.0)
    assert (big.total, big.per_file, big.deadline) == (32 << 20, 4 << 20, 5.0 + 0.2)


def test_the_reader_says_when_it_is_behind(tree, procs):
    _fifty_mb(tree, files=10)
    reader, state = reader_for(tree), {}
    agents, _ = poll(reader, state)
    assert reader.behind and MAIN not in agents  # held back until read to its end
    for _ in range(200):
        if not reader.behind:
            break
        agents, _ = poll(reader, state, T0, Budget(8 << 20, 4 << 20))
    assert not reader.behind and agents[MAIN]["x-files"] == 10
    assert poll(reader, state) == ({}, []) and not reader.behind


def _old(*paths: Path, seconds: float = 3600) -> None:
    when = (T0 - timedelta(seconds=seconds)).timestamp()
    for path in paths:
        os.utime(path, (when, when))


def test_the_watcher_ticks_with_a_big_budget_while_behind(tree, procs, tmp_path, monkeypatch):
    from lamptown.status.readers import tail as tail_mod

    monkeypatch.setattr(tail_mod, "CATCHUP_SECONDS", 60.0)  # a slow CI runner: no deadline
    size, _ = _fifty_mb(tree, files=10)
    _old(tree.log())
    reader = reader_for(tree)
    status = tmp_path / "status"
    with Watcher(status, [reader], tz=UTC) as w:
        first = w.tick(T0)
        assert first.behind and reader.read_bytes <= PER_FILE
        ticks = 1
        while w.tick(T0 + timedelta(seconds=ticks)).behind and ticks < 100:
            assert reader.read_bytes > PER_FILE  # 4 MB a file on a catch-up tick
            ticks += 1
        last = w.tick(T0 + timedelta(seconds=ticks + 1))
    assert not last.behind and ticks <= size / (4 << 20) + 3
    assert read_status(status_path(MAIN, status))["x-files"] == 10


def test_catch_up_ticks_take_turns_between_readers(tmp_path):
    calls: list[str] = []

    class Slow:
        def __init__(self, name: str) -> None:
            self.name, self.behind = name, True

        def poll(self, state, now, budget=None):
            calls.append(self.name)
            return cc.Batch()

    readers = [Slow("one"), Slow("two")]
    with Watcher(tmp_path / "status", readers, tz=UTC) as w:
        for i in range(3):
            w.tick(T0 + timedelta(seconds=i))
    assert calls == ["one", "two", "two", "one", "one", "two"]


def _skim_tree(tree: Tree) -> None:
    """An ended session and finished subagents, with every kind of line a real log has."""
    def noise(t: float) -> list[dict]:
        return [{"type": "attachment", "sessionId": SID, "timestamp": ts(t)},
                {"type": "system", "subtype": "api_error", "timestamp": ts(t + 1)},
                {"type": "queue-operation", "timestamp": ts(t + 2)}]

    tree.write(
        title("ai-title", "Kite history"), prompt(-3600),
        assistant(-3590, {"type": "text", "text": "Thinking aloud."}, stop=None),
        assistant(-3580, use("w1", "Write", file_path=f"{CWD}/a.py")), results(-3570, "w1"),
        *noise(-3560), assistant(-3500, error=True, stop="stop_sequence"),
        assistant(-3490, {"type": "text", "text": "Back again."}, stop=None),
        assistant(-3480, use("e1", "Edit", file_path=f"{CWD}/b.py")), results(-3470, "e1"),
        {**prompt(-3460), "isMeta": True}, assistant(-3400, stop="end_turn"),
        title("custom-title", "Kite history, renamed"),
        assistant(-3300, use("e2", "Edit", file_path=f"{CWD}/c.py")))
    tree.sub("s1", prompt(-3590), *noise(-3580),
             assistant(-3550, use("w2", "Write", file_path=f"{CWD}/d.py")),
             results(-3540, "w2"), assistant(-3530, stop="end_turn"), finished=True)
    tree.sub("s2", prompt(-3520), assistant(-3510, error=True, stop="stop_sequence"),
             finished=True)
    folder = tree.project / SID / "subagents"
    _old(tree.log(), *folder.glob("*.jsonl"), seconds=3000)


def test_skimming_history_gives_what_a_full_parse_gives(tree, procs, monkeypatch):
    _skim_tree(tree)
    outs = []
    real = json.loads
    parsed: list[int] = []
    monkeypatch.setattr(cc.json, "loads", lambda s, *a, **k: parsed.append(1) or real(
        s, *a, **k))
    for skim in (False, True):
        parsed.clear()
        reader, state = reader_for(tree), {"v": cc.STATE_VERSION, "salt": "5a1t"}
        reader.skim = skim
        agents, events = poll(reader, state)
        for rec in [*state["a"].values(), state["s"][SID]]:
            # what only a live session uses: its pending calls and permission mode (a
            # tool result's), and the state time (a hook race needs a log being written)
            for key in ("pend", "pm", "st"):
                rec.pop(key, None)
        outs.append((agents, sorted(events, key=json.dumps), state["s"], state["a"],
                     len(parsed)))
    (full_a, full_e, full_s, full_sub, n_full), (a, e, s, sub, n_skim) = outs
    assert a == full_a and e == full_e and sub == full_sub
    assert s == full_s
    assert full_a[MAIN]["title"] == "Kite history, renamed"
    assert full_a[cc.sub_id(SID, "s2")]["state"] == "error"
    assert n_skim < n_full


def test_a_huge_finished_subagent_is_read_by_head_and_tail(tree, procs, monkeypatch):
    monkeypatch.setattr(cc, "HUGE_SUB", 1 << 20)
    monkeypatch.setattr(cc, "HUGE_TAIL", 64 << 10)
    tree.write(prompt(-4000), assistant(-3990, stop="end_turn"))
    filler = {"type": "attachment", "sessionId": SID, "pad": "p" * 4000}
    tree.sub("big", prompt(-3980), assistant(-3970, use("w1", "Write", file_path="/w/a.py")),
             *[filler] * 300, assistant(-3900, use("w2", "Write", file_path="/w/b.py")),
             *[filler] * 300, assistant(-3800, use("w3", "Write", file_path="/w/c.py")),
             assistant(-3790, stop="end_turn"), finished=True)
    log = tree.project / SID / "subagents" / "agent-big.jsonl"
    _old(tree.log(), log, seconds=3000)
    reader, state = reader_for(tree), {}
    spent, rec = 0, None
    for _ in range(20):
        agents, _ = poll(reader, state, T0, Budget(2 << 20, 256 << 10))
        spent += reader.read_bytes
        rec = agents.get(cc.sub_id(SID, "big")) or rec
        if rec and not reader.behind:
            break
    assert rec is not None and rec["message"] == "Finished" and rec["at"] == ts(-3790)
    assert rec["x-files"] == 2  # the head's and the tail's; the middle one is skipped
    assert spent < log.stat().st_size


# --- fix-PRIV: titles = "folder" keeps no session title, not even in the saved state ---
def test_folder_titles_keep_no_session_title_in_the_saved_state(tree, procs):
    alive(procs)
    tree.session("busy", name="Kite night shift", source="user")
    tree.write(title("ai-title", "Plan the secret launch"),
               title("custom-title", "Secret client work"), prompt(-100),
               assistant(-90, use("ag", "Agent", prompt="x")))
    tree.sub("a1", prompt(-80), finished=False, description="Read the secret contract")
    state: dict = {}
    agents, _ = poll(reader_for(tree, titles="folder"), state)
    assert agents[MAIN]["title"] == "paper-kite"
    assert agents[cc.sub_id(SID, "a1")]["title"] == "general-purpose"
    saved = json.dumps(state)
    for words in ("secret", "Secret", "Kite night shift"):
        assert words not in saved
    # a state an older run saved in "session" mode loses its titles at the next poll
    state2: dict = {}
    poll(reader_for(tree), state2)
    assert "Secret client work" in json.dumps(state2)
    tree.write(prompt(-50))
    poll(reader_for(tree, titles="folder"), state2)
    assert "Secret" not in json.dumps(state2) and "secret" not in json.dumps(state2)
# --- end fix-PRIV ---
