"""The Codex reader: rollout logs and state_5.sqlite to status changes and events.

Every tree here is invented under ``tmp_path``; the autouse guard fails a test that touches
the real ``~/.codex``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from _reader_guard import guard
from lamptown.status import events as ev
from lamptown.status.adapters import make_id
from lamptown.status.readers import BUILTIN, make_reader
from lamptown.status.readers import codex as codex_mod
from lamptown.status.readers.codex import NEED, CodexReader
from lamptown.status.readers.tail import Budget
from lamptown.status.store import read_status, status_path
from lamptown.status.watch import Watcher

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=1)
CWD = "/work/paper-kite"
SECRET = "please fix the save slots in the kite game"  # a prompt: must never show


@pytest.fixture(autouse=True)
def _no_real_codex(monkeypatch, tmp_path):
    guard(monkeypatch, tmp_path)


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "codex"
    folder.mkdir()
    return folder


def sid(n: int, ms: int | None = None) -> str:
    """A UUIDv7-shaped thread id, as Codex makes them: a millisecond clock first (sessions
    ``n`` ms apart, so their first 12 characters are the same), random bits last."""
    clock = f"{ms if ms is not None else int(T0.timestamp() * 1000) + n:012x}"
    rand = hashlib.sha256(f"session {n}".encode()).hexdigest()
    return f"{clock[:8]}-{clock[8:]}-7{rand[:3]}-8{rand[3:6]}-{rand[6:18]}"


def aid(n: int) -> str:
    """The record id hooks and reader share: ``codex-`` + the id's random last 12 digits."""
    return "codex-" + sid(n).replace("-", "")[-12:]


def ts(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Session:
    """One invented rollout file."""

    def __init__(self, root, n, *, cwd=CWD, start=T0, source="exec", thread_source=None,
                 day=None):
        self.sid = sid(n) if isinstance(n, int) else n
        local = (day or start).astimezone().date()
        folder = root / "sessions" / f"{local:%Y}" / f"{local:%m}" / f"{local:%d}"
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"rollout-{start:%Y-%m-%dT%H-%M-%S}-{self.sid}.jsonl"
        meta = {"id": self.sid, "session_id": self.sid, "cwd": cwd, "timestamp": ts(start),
                "source": source, "originator": "codex_exec", "cli_version": "0.0.0",
                "base_instructions": {"text": "Be helpful."}}
        if thread_source:
            meta["thread_source"] = thread_source
        self.t = start
        self.write({"timestamp": ts(start), "type": "session_meta", "payload": meta})

    def write(self, *docs):
        with open(self.path, "ab") as fh:
            for doc in docs:
                fh.write((json.dumps(doc) + "\n").encode())

    def tick(self, seconds=1):
        self.t += timedelta(seconds=seconds)
        return self.t

    def msg(self, ptype, **payload):
        self.write({"timestamp": ts(self.tick()), "type": "event_msg",
                    "payload": {"type": ptype, **payload}})
        return self.t

    def item(self, ptype="function_call_output", **payload):
        self.write({"timestamp": ts(self.tick()), "type": "response_item",
                    "payload": {"type": ptype, **payload}})
        return self.t

    def started(self):
        return self.msg("task_started", turn_id="t", started_at=0)

    def complete(self):
        return self.msg("task_complete", turn_id="t", last_agent_message=SECRET)


def poll(reader, state, now=NOW, budget=None):
    return reader.poll(state, now, budget=budget or Budget())


def kinds(batch):
    return [e["k"] for e in batch.events]


def one(batch):
    assert len(batch.agents) == 1, batch.agents
    return batch.agents[0]


def make_db(root, rows=(), edges=()):
    con = sqlite3.connect(root / "state_5.sqlite")
    con.executescript(
        "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL DEFAULT '',"
        " title TEXT NOT NULL, first_user_message TEXT NOT NULL DEFAULT '',"
        " preview TEXT NOT NULL DEFAULT '', agent_nickname TEXT, source TEXT NOT NULL,"
        " updated_at INTEGER NOT NULL, updated_at_ms INTEGER, thread_source TEXT,"
        " archived INTEGER NOT NULL DEFAULT 0);"
        "CREATE INDEX idx_threads_updated_at_ms ON threads(updated_at_ms DESC, id DESC);"
        "CREATE TABLE thread_spawn_edges (parent_thread_id TEXT NOT NULL,"
        " child_thread_id TEXT NOT NULL PRIMARY KEY, status TEXT NOT NULL);")
    for row in rows:
        add_thread(con, **row)
    for parent, child in edges:
        con.execute("INSERT INTO thread_spawn_edges VALUES (?, ?, 'open')", (parent, child))
    con.commit()
    return con


def add_thread(con, *, id, title="", first="", nick=None, source="exec", at=T0):
    ms = int(at.timestamp() * 1000)
    con.execute("INSERT OR REPLACE INTO threads (id, title, first_user_message, preview,"
                " agent_nickname, source, updated_at, updated_at_ms) VALUES (?,?,?,?,?,?,?,?)",
                (id, title, first, first[:40], nick, source, ms // 1000, ms))


# ---------------------------------------------------------------- states
def test_every_state_from_the_rollout_signals(root):
    s = Session(root, 1)
    reader, state = CodexReader(root=root), {}

    a = one(poll(reader, state))  # only the session_meta line: started, idle
    assert a["id"] == aid(1) and a["agent"] == "codex" and a["kind"] == "task"
    assert (a["state"], a["message"]) == ("ok", "Session started")
    assert a["title"] == "paper-kite" and a["x-project"] == "paper-kite"
    assert a["x-folder"] == CWD and a["started"] == codex_mod.iso(T0) and a["needs_you"] == []

    s.started()
    t = s.item()
    b = poll(reader, state)
    a = one(b)
    assert (a["state"], a["message"], a["at"]) == ("working", "Working", codex_mod.iso(t))
    assert kinds(b) == ["start"]
    assert ev.clean_event(b.events[0])["project"] == "paper-kite"
    assert b.events[0]["folder"] == CWD and b.events[0]["tool"] == "codex"

    s.msg("exec_approval_request", call_id="c1", command=["rm", "-rf", "x"])
    b = poll(reader, state)
    a = one(b)
    assert a["state"] == "waiting" and a["needs_you"] == NEED and kinds(b) == ["wait"]
    assert a["message"] == "Waiting for approval"

    s.item()  # the approval was answered: work goes on
    b = poll(reader, state)
    assert one(b)["state"] == "working" and kinds(b) == ["answer"]
    assert one(b)["needs_you"] == []

    done = s.complete()
    s.msg("token_count", info=None)  # bookkeeping after the turn: no state change
    b = poll(reader, state)
    a = one(b)
    assert (a["state"], a["message"]) == ("ok", "Turn finished") and kinds(b) == ["done"]
    assert a["at"] == state["sess"][next(iter(state["sess"]))]["at"] == codex_mod.iso(done)

    s.started()
    s.msg("error", message="stream disconnected")
    b = poll(reader, state)
    assert (one(b)["state"], one(b)["message"]) == ("error", "Turn failed")
    assert kinds(b) == ["start", "fail"]

    s.started()
    s.msg("turn_aborted", reason="interrupted")
    b = poll(reader, state)
    assert (one(b)["state"], one(b)["message"]) == ("ok", "Interrupted")
    assert kinds(b) == ["start", "done"]

    s.started()
    s.started()  # a new turn without a task_complete in between is still a new turn
    assert kinds(poll(reader, state)) == ["start", "start"]

    assert poll(reader, state).agents == []  # nothing new: no change at all


def test_other_wait_signals_and_a_mid_turn_first_read(root):
    s = Session(root, 2)
    s.started()
    s.msg("request_user_input", call_id="c")
    reader, state = CodexReader(root=root), {}
    b = poll(reader, state)
    assert one(b)["state"] == "waiting" and kinds(b) == ["start", "wait"]
    for name in ("apply_patch_approval_request", "elicitation_request", "request_permissions"):
        s.item()
        s.msg(name)
        assert one(poll(reader, state))["state"] == "waiting"


def test_a_failed_command_is_work_not_an_error(root):
    s = Session(root, 3)
    s.started()
    s.msg("item_completed", item={"type": "CommandExecution", "status": "failed",
                                  "exit_code": 1})
    assert one(poll(CodexReader(root=root), {}))["state"] == "working"


def test_approval_reviewer_sessions_are_skipped(root):
    Session(root, 4, thread_source="guardian_review").started()
    Session(root, 5, source={"subagent": {"other": "guardian"}}).started()
    reader, state = CodexReader(root=root), {}
    assert poll(reader, state).agents == []
    assert poll(reader, state).agents == []


# ---------------------------------------------------------------- the Watcher, restarts
def test_through_the_watcher_and_a_restart_without_duplicates(root, tmp_path):
    status = tmp_path / "status"
    s = Session(root, 6, start=NOW - timedelta(seconds=60))  # recent: not stale at NOW
    s.started()
    s.complete()
    s.started()
    with Watcher(status, [CodexReader(root=root)], tz=UTC) as w:
        res = w.tick(NOW)
        assert not res.errors
    doc = read_status(status_path(aid(6), status))
    assert doc["state"] == "working" and doc["agent"] == "codex"
    assert doc["x-folder"] == CWD and doc["title"] == "paper-kite"

    s.complete()  # while no Watcher runs
    with Watcher(status, [CodexReader(root=root)], tz=UTC) as w:  # a new reader: no memory
        res = w.tick(NOW + timedelta(seconds=5))
        assert not res.errors
        w.tick(NOW + timedelta(seconds=10))
    doc = read_status(status_path(aid(6), status))
    assert doc["state"] == "ok" and doc["x-folder"] == CWD
    logged = [e for _, es in ev.read_days(status, 2, now=NOW, tz=UTC) for e in es
              if e["id"] == aid(6) and e["k"] != "project"]
    keys = [ev.event_key(e) for e in logged]
    assert len(keys) == len(set(keys))
    assert [e["k"] for e in logged] == ["start", "done", "start", "done"]


def test_state_round_trips_and_a_repeated_poll_gives_the_same_answer(root):
    s = Session(root, 7)
    s.started()
    reader, state = CodexReader(root=root), {}
    poll(reader, state)
    s.item()
    s.complete()
    saved = json.loads(json.dumps(state))
    first = poll(CodexReader(root=root), copy.deepcopy(saved))
    again = poll(CodexReader(root=root), copy.deepcopy(saved))  # the Watcher rolled back
    assert kinds(first) == kinds(again) == ["done"]
    assert first.agents == again.agents
    # and the saved state never holds a path
    text = json.dumps(saved)
    assert "/work" not in text and str(root) not in text and "rollout-" not in text


def test_a_lost_cursor_or_rewritten_file_does_not_replay_events(root):
    s = Session(root, 8)
    s.started()
    s.complete()
    reader, state = CodexReader(root=root), {}
    assert kinds(poll(reader, state)) == ["start", "done"]
    state["files"].clear()  # the cursor is gone, the session is known
    assert kinds(poll(reader, state)) == []
    s.started()
    assert kinds(poll(reader, state)) == ["start"]
    data = s.path.read_bytes()  # the file is rewritten in place (same lines, new file)
    s.path.unlink()
    s.path.write_bytes(data)
    assert kinds(poll(reader, state)) == []


# ---------------------------------------------------------------- files
def test_changed_files_count_once_as_salted_hashes(root):
    s = Session(root, 9)
    s.started()
    s.msg("item_completed", item={"type": "FileChange", "status": "completed",
                                  "changes": {f"{CWD}/src/save.py": {"type": "update",
                                                                     "content": "x"},
                                              f"{CWD}/src/kite.py": {"type": "add"}}})
    reader, state = CodexReader(root=root), {}
    b = poll(reader, state)
    assert one(b)["x-files"] == 2
    assert [(e["k"], e.get("n")) for e in b.events] == [("start", None), ("files", 2)]
    patch = "*** Begin Patch\n*** Update File: src/save.py\n@@\n-a\n+b\n*** Add File: notes.md\n"
    s.item("custom_tool_call", name="apply_patch", input=patch, call_id="p")
    s.item("function_call", name="apply_patch", call_id="q",
           arguments=json.dumps({"input": "*** Delete File: old/kite.py\n"}))
    s.msg("patch_apply_end", changes={f"{CWD}/notes.md": {"add": {}}})
    b = poll(reader, state)
    assert one(b)["x-files"] == 4  # save.py again (relative this time) and notes.md twice
    assert sum(e.get("n", 0) for e in b.events if e["k"] == "files") == 2
    text = json.dumps(state)
    for name in ("save.py", "kite.py", "notes.md", "src/"):
        assert name not in text


# ---------------------------------------------------------------- budget, big files and lines
def _filler(s, n, size=1000):
    pad = "x" * size
    s.write(*({"timestamp": ts(s.tick()), "type": "response_item",
               "payload": {"type": "function_call_output", "output": pad}} for _ in range(n)))


def test_a_big_file_starts_near_its_end_and_is_never_read_again(root):
    s = Session(root, 10)
    s.started()
    s.complete()  # an old turn, far before the end
    _filler(s, 1500)  # ~1.5 MB
    s.started()
    s.msg("exec_approval_request", call_id="c")
    reader, state = CodexReader(root=root), {}
    budget = Budget(total=8 * 1024 * 1024, per_file=64 * 1024)
    b = reader.poll(state, NOW, budget=budget)
    spent = budget.total - budget.left
    assert spent <= 64 * 1024 + codex_mod.META_HEAD
    assert one(b)["state"] == "waiting"
    assert "done" not in kinds(b)  # the old turn was skipped, not replayed
    s.item()
    size = s.path.stat().st_size
    budget = Budget(total=8 * 1024 * 1024, per_file=64 * 1024)
    b = reader.poll(state, NOW, budget=budget)
    line = len(s.path.read_bytes().splitlines(keepends=True)[-1])
    assert budget.total - budget.left == line  # only the new line
    assert one(b)["state"] == "working" and kinds(b) == ["answer"]
    assert s.path.stat().st_size == size
    budget = Budget()
    assert reader.poll(state, NOW, budget=budget).agents == []
    assert budget.left == budget.total  # unchanged: stat only


def test_a_small_file_is_read_whole_and_a_shared_budget_defers_the_rest(root):
    a, b_ = Session(root, 11), Session(root, 12, start=T0 + timedelta(seconds=30))
    for s in (a, b_):
        s.started()
        _filler(s, 60)  # ~60 KB each
        s.complete()
    reader, state = CodexReader(root=root), {}
    budget = Budget(total=100 * 1024, per_file=512 * 1024)
    first = reader.poll(state, NOW, budget=budget)
    assert budget.left <= 0 or len(first.agents) == 2
    done = {x["id"] for x in first.agents}
    second = reader.poll(state, NOW, budget=Budget())
    done |= {x["id"] for x in second.agents}
    assert done == {aid(11), aid(12)}
    all_kinds = kinds(first) + kinds(second)
    assert sorted(all_kinds) == ["done", "done", "start", "start"]  # whole files, no repeats
    assert reader.poll(state, NOW, budget=Budget()).agents == []


def test_a_budget_spent_on_the_first_line_defers_the_whole_file(root):
    s = Session(root, 15)
    s.started()
    _filler(s, 30)
    s.complete()
    reader, state = CodexReader(root=root), {}
    first = reader.poll(state, NOW, budget=Budget(total=codex_mod.META_STEP, per_file=1 << 20))
    assert first.agents == [] and first.events == []  # known, not read, not shown as idle
    second = poll(reader, state)
    assert kinds(second) == ["start", "done"]  # from its start, nothing lost
    assert one(second)["state"] == "ok"
    assert poll(reader, state).agents == []


def test_big_lines_are_never_parsed_as_json(root, monkeypatch):
    s = Session(root, 13)
    s.started()
    s.item(output="y" * (300 * 1024))
    s.msg("item_completed", item={"type": "FileChange", "status": "completed",
                                  "changes": {f"{CWD}/big.bin": {"type": "add",
                                                                 "content": "z" * 300_000}}})
    s.msg("task_complete", last_agent_message="w" * (300 * 1024))
    real = json.loads

    def careful(text, *args, **kwargs):
        assert len(text) <= 256 * 1024, "a big line was parsed"
        return real(text, *args, **kwargs)

    monkeypatch.setattr(codex_mod.json, "loads", careful)
    monkeypatch.setattr("lamptown.status.readers.tail.json.loads", careful)
    b = poll(CodexReader(root=root), {}, budget=Budget(total=4 << 20, per_file=2 << 20))
    a = one(b)
    assert a["state"] == "ok" and a["x-files"] == 1
    assert kinds(b) == ["start", "files", "done"]


def test_a_partial_last_line_waits_for_its_newline(root):
    s = Session(root, 14)
    s.started()
    reader, state = CodexReader(root=root), {}
    poll(reader, state)
    line = json.dumps({"timestamp": ts(s.tick()), "type": "event_msg",
                       "payload": {"type": "task_complete"}}).encode()
    with open(s.path, "ab") as fh:
        fh.write(line[:20])
    assert poll(reader, state).agents == []
    with open(s.path, "ab") as fh:
        fh.write(line[20:] + b"\n")
    assert kinds(poll(reader, state)) == ["done"]


# ---------------------------------------------------------------- the thread database
def test_titles_and_spawn_edges_from_the_database(root):
    for n in (20, 21, 22, 24, 25):
        Session(root, n)
    Session(root, 23, source=json.dumps(  # a spawned subagent says so itself
        {"subagent": {"thread_spawn": {"parent_thread_id": sid(20), "depth": 1}}}))
    make_db(root, rows=[
        {"id": sid(20), "title": "Save slot rework", "first": SECRET},
        {"id": sid(21), "title": "", "nick": "Ohm", "source": "subagent"},
        {"id": sid(22), "title": "", "nick": "Volta"},
        {"id": sid(24), "title": SECRET, "first": SECRET},  # the title is the prompt
        {"id": sid(25), "title": SECRET[:20] + "…", "first": SECRET},  # its start
    ], edges=[(sid(20), sid(21))]).close()
    reader, state = CodexReader(root=root), {}
    agents = {a["id"]: a for a in poll(reader, state).agents}
    assert agents[aid(20)]["title"] == "Save slot rework"
    assert agents[aid(21)]["title"] == "Ohm" and agents[aid(21)]["x-parent"] == aid(20)
    assert agents[aid(22)]["title"] == "Volta" and "x-parent" not in agents[aid(22)]
    assert agents[aid(23)]["x-parent"] == aid(20)
    assert agents[aid(24)]["title"] == agents[aid(25)]["title"] == "paper-kite"
    blob = json.dumps(agents) + json.dumps(state)
    assert SECRET[:20] not in blob

    con = sqlite3.connect(root / "state_5.sqlite")  # renamed later: the time cursor sees it
    add_thread(con, id=sid(20), title="Save slots v2", first=SECRET, at=NOW)
    con.commit()
    con.close()
    b = poll(reader, state)
    assert [(a["id"], a["title"]) for a in b.agents] == [(aid(20), "Save slots v2")]
    assert poll(reader, state).agents == []  # caught up: nothing again


def test_a_locked_missing_or_broken_database_is_skipped(root):
    Session(root, 30).started()
    reader, state = CodexReader(root=root), {}
    assert one(poll(reader, state))["title"] == "paper-kite"  # no database at all

    con = make_db(root, rows=[{"id": sid(30), "title": "Kite physics"}])
    con.execute("BEGIN EXCLUSIVE")  # another process holds the database
    try:
        assert poll(reader, state).agents == []  # locked: no error, tried again later
    finally:
        con.rollback()
        con.close()
    assert one(poll(reader, state))["title"] == "Kite physics"

    (root / "state_5.sqlite").write_bytes(b"not a database at all" * 100)
    Session(root, 31).started()
    a = one(poll(reader, state))
    assert a["id"] == aid(31) and a["title"] == "paper-kite"


def test_an_older_schema_without_prompt_columns_gives_no_title(root):
    Session(root, 32).started()
    con = sqlite3.connect(root / "state_5.sqlite")
    con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, source TEXT,"
                " updated_at INTEGER)")
    con.execute("INSERT INTO threads VALUES (?, ?, 'exec', 0)", (sid(32), SECRET))
    con.commit()
    con.close()
    a = one(poll(CodexReader(root=root), {}))
    assert a["title"] == "paper-kite"


# ---------------------------------------------------------------- ids, window, registry
def test_two_sessions_with_the_same_id_start_get_two_records(root):
    """M8: this used to pass whatever the ids were. Now: the exact ids, either poll order."""
    first = "019e7777-aaaa-7000-8000-000000000001"
    second = "019e7777-aaaa-7000-8000-000000000002"
    Session(root, first).started()
    Session(root, second, start=T0 + timedelta(seconds=5)).started()
    ids = {a["id"] for a in poll(CodexReader(root=root), {}).agents}
    assert ids == {"codex-000000000001", "codex-000000000002"}


def test_only_the_last_days_are_watched(root):
    Session(root, 40, start=T0 - timedelta(days=10)).started()
    Session(root, 41).started()
    reader, state = CodexReader(root=root), {}
    assert {a["id"] for a in poll(reader, state).agents} == {aid(41)}
    later = NOW + timedelta(days=9)
    assert poll(reader, state, now=later).agents == []
    assert state["sess"] == {} and state["files"] == {}


def test_the_guard_stops_a_read_of_the_real_codex_folder():
    import os

    from _reader_guard import REAL_HOME

    with pytest.raises(pytest.fail.Exception):
        os.stat(REAL_HOME / ".codex" / "state_5.sqlite")
    with pytest.raises(pytest.fail.Exception):
        sqlite3.connect((REAL_HOME / ".codex" / "state_5.sqlite").as_uri() + "?mode=ro",
                        uri=True)


def test_root_from_options_and_the_built_in_table(root, monkeypatch):
    assert BUILTIN["codex"] == "lamptown.status.readers.codex:make_reader"
    reader = make_reader({"kind": "codex", "root": str(root)})
    assert reader.name == "codex" and reader.root == root
    monkeypatch.setenv("CODEX_HOME", str(root))
    assert CodexReader().root == root
    monkeypatch.delenv("CODEX_HOME")
    assert CodexReader().root.name == ".codex"  # the (test) home's, never the real one
    assert poll(CodexReader(), {}).agents == []  # no such folder: nothing, no error


# ---------------------------------------------------------------- L5-fix (review-L5)
def test_parallel_sessions_ms_apart_get_their_own_ids_shared_with_the_hooks(root, tmp_path):
    """B3: UUIDv7 ids start with a clock; ids from their first 12 characters collided."""
    from lamptown.status.adapters import apply
    from lamptown.status.adapters import codex as hook

    ids = [sid(1), sid(2), sid(3)]  # 1 ms apart: the same first 12 characters
    assert len({make_id("codex", i) for i in ids}) == 1
    sessions = [Session(root, n, start=T0 + timedelta(seconds=n)) for n in (1, 2, 3)]
    for s in sessions:
        s.started()
    reader_ids = {a["id"] for a in poll(CodexReader(root=root), {}).agents}
    status = tmp_path / "status"
    for i in ids:
        apply(hook.translate({"hook_event_name": "UserPromptSubmit", "session_id": i,
                              "cwd": CWD}), status)
        apply(hook.translate({"type": "agent-turn-complete", "thread-id": i, "cwd": CWD}),
              status)
    hook_ids = {f.stem for f in status.glob("*.json")}
    assert reader_ids == hook_ids == {aid(1), aid(2), aid(3)}


def test_a_saved_state_from_before_the_id_change_still_loads(root):
    s = Session(root, 4)
    s.started()
    reader, state = CodexReader(root=root), {}
    poll(reader, state)
    for x in state["sess"].values():
        x["id"] = make_id("codex", x["sid"])  # the old id (its clock)
    saved = json.loads(json.dumps(state))
    s.complete()
    a = one(poll(CodexReader(root=root), saved))
    assert a["id"] == aid(4) and a["state"] == "ok"


@pytest.mark.parametrize("title", [
    "  " + SECRET,  # leading whitespace
    SECRET.replace(" the kite", "\n the kite"),  # a newline folded
    SECRET.replace("save slots in", "save  slots\tin"),  # whitespace runs
    SECRET.capitalize(),  # case
    SECRET.upper(),
    "Fix the save slots in the kite game",  # the prompt's middle
    SECRET[7:30] + "…",  # a cut middle
    SECRET + " now",  # the prompt and more
    "'" + SECRET + ".'",
])
def test_a_title_that_is_the_prompt_in_another_form_is_never_shown(root, title):
    """B2: only an exact prefix was caught; whitespace, case or a middle got through."""
    Session(root, 50).started()
    make_db(root, rows=[{"id": sid(50), "title": title, "first": SECRET}]).close()
    reader, state = CodexReader(root=root), {}
    b = poll(reader, state)
    assert one(b)["title"] == "paper-kite"
    blob = json.dumps(b.agents) + json.dumps(state) + json.dumps(b.events, default=str)
    assert "save slots" not in blob.lower()


def test_a_real_title_and_a_null_prompt_column(root):
    Session(root, 51).started()
    Session(root, 52).started()
    con = sqlite3.connect(root / "state_5.sqlite")
    con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, first_user_message TEXT,"
                " preview TEXT, agent_nickname TEXT, source TEXT, updated_at INTEGER,"
                " updated_at_ms INTEGER)")
    con.execute("INSERT INTO threads VALUES (?, 'Kite physics', ?, ?, NULL, 'exec', 0, 0)",
                (sid(51), SECRET, SECRET[:40]))
    con.execute("INSERT INTO threads VALUES (?, ?, NULL, '', NULL, 'exec', 0, 0)",
                (sid(52), SECRET))  # no prompt to compare with: no title, as before
    con.commit()
    con.close()
    agents = {a["id"]: a["title"] for a in poll(CodexReader(root=root), {}).agents}
    assert agents == {aid(51): "Kite physics", aid(52): "paper-kite"}


@pytest.mark.parametrize("bad", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00",
                                 "0000-00-00T00:00:00Z"])
def test_one_out_of_range_time_does_not_wedge_the_reader(root, bad):
    """B4: such a line raised on every poll, for good."""
    s = Session(root, 53)
    s.started()
    s.write({"timestamp": bad, "type": "event_msg", "payload": {"type": "task_complete"}})
    s.write({"timestamp": bad[:10] + "XX", "type": "event_msg", "payload": {"type": "error"}})
    reader, state = CodexReader(root=root), {}
    assert kinds(poll(reader, state)) == ["start"]
    s.complete()
    assert kinds(poll(reader, state)) == ["done"]
    other = Session(root, 54, start=T0 + timedelta(seconds=9))
    with open(other.path, "wb") as fh:  # its session_meta has the bad time
        fh.write((json.dumps({"timestamp": bad, "type": "session_meta",
                              "payload": {"id": other.sid, "cwd": CWD, "timestamp": bad}})
                  + "\n").encode())
    other.started()
    b = poll(reader, state)
    assert kinds(b) == ["start"] and one(b)["id"] == aid(54) and "started" not in one(b)


def test_a_session_in_the_home_folder_never_shows_the_login_name(root):
    """B5: the title fell back to the folder's name: in ~, the login name."""
    home = os.path.expanduser("~")  # the test's own home (the guard moved it)
    name = os.path.basename(home)
    s = Session(root, 55, cwd=home)
    s.started()
    s.msg("item_completed", item={"type": "FileChange", "changes": {home + "/a.txt": {}}})
    b = poll(CodexReader(root=root), {})
    a = one(b)
    assert a["title"] == "Codex" and "x-project" not in a and a["x-folder"] == home
    assert {e["title"] for e in b.events} == {"Codex"}
    assert all(e["project"] == "" for e in b.events)
    shown = json.dumps([a["title"], [(e["title"], e["project"]) for e in b.events]])
    assert name and name not in shown
    Session(root, 56, cwd="C:\\" if os.name == "nt" else "/").started()  # a drive root
    agents = {x["id"]: x for x in poll(CodexReader(root=root), {}).agents}
    assert agents[aid(56)]["title"] == "Codex" and "x-project" not in agents[aid(56)]


def test_the_hooks_never_take_a_title_from_the_home_folder(tmp_path):
    from lamptown.status.adapters import apply, claude_code, cursor, gemini_cli
    from lamptown.status.adapters import codex as hook

    home = os.path.expanduser("~")
    status = tmp_path / "hooked"
    for mod, payload in (
            (hook, {"hook_event_name": "SessionStart", "session_id": sid(57), "cwd": home}),
            (hook, {"type": "agent-turn-complete", "cwd": home}),  # no thread id at all
            (claude_code, {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": home}),
            (gemini_cli, {"hook_event_name": "SessionStart", "session_id": "s2", "cwd": home}),
            (cursor, {"hook_event_name": "sessionStart", "conversation_id": "s3",
                      "workspace_roots": [home]})):
        update = mod.translate(payload)
        assert update.title is None, mod.AGENT
        assert os.path.basename(home).casefold() not in update.id.removeprefix(mod.AGENT)
        doc = apply(update, status)
        assert doc["title"] == mod.DISPLAY and doc["x-folder"] == home


def test_one_poll_never_returns_more_than_the_event_cap(root, monkeypatch):
    """M1: one file could give thousands of events; the ones past the cap were lost."""
    monkeypatch.setattr(codex_mod, "MAX_EVENTS", 510)  # 500 kept free: 10 per poll
    s = Session(root, 58)
    for _ in range(12):
        s.started()
        s.complete()
    reader, state = CodexReader(root=root), {}
    got = []
    for _ in range(5):
        state = json.loads(json.dumps(state))  # saved and loaded, as the Watcher does
        b = poll(reader, state)
        assert len(b.events) <= 10
        got += [(e["k"], e["t"]) for e in b.events]
    assert [k for k, _ in got] == ["start", "done"] * 12
    assert len(set(got)) == 24


def test_work_after_an_error_is_working_again(root):
    """M2: activity after an error used to leave the session red until the next turn."""
    s = Session(root, 59)
    s.started()
    s.msg("error", message="stream disconnected; retrying")
    s.item()
    s.item()
    reader, state = CodexReader(root=root), {}
    b = poll(reader, state)
    assert one(b)["state"] == "working" and kinds(b) == ["start", "fail", "start"]
    s.complete()
    assert kinds(poll(reader, state)) == ["done"]


def test_changed_file_hashes_stay_bounded_and_counts_never_drop(root, monkeypatch):
    """M5: hashes grew without a bound against the Watcher's 4 MB state file."""
    monkeypatch.setattr(codex_mod, "MAX_HASHES", 5)
    monkeypatch.setattr(codex_mod, "MAX_ALL_HASHES", 12, raising=False)
    sessions = [Session(root, n, start=T0 + timedelta(seconds=n)) for n in range(60, 64)]
    for s in sessions:
        s.started()
        s.msg("item_completed", item={"type": "FileChange",
                                      "changes": {f"{CWD}/f{i}.py": {} for i in range(4)}})
    reader, state = CodexReader(root=root), {}
    first = {a["id"]: a["x-files"] for a in poll(reader, state).agents}
    assert first == {aid(n): 4 for n in range(60, 64)}

    def hashes():
        return sum(len(x.get("fh") or []) for x in state["sess"].values())

    assert hashes() <= 12
    for s in sessions:
        s.msg("item_completed", item={"type": "FileChange",
                                      "changes": {f"{CWD}/g{i}.py": {} for i in range(4)}})
        s.complete()
    later = {a["id"]: a["x-files"] for a in poll(reader, state).agents}
    assert later == {aid(n): 8 for n in range(60, 64)} and hashes() <= 12
    assert all(len(x.get("fh") or []) <= 5 for x in state["sess"].values())
    sessions[0].item()  # a finished session, quiet for two days: its hashes go, its count stays
    b = poll(reader, state, now=NOW + timedelta(days=2))
    assert {a["id"]: a["x-files"] for a in b.agents} == {aid(60): 8}
    assert state["sess"][next(k for k, x in state["sess"].items() if x["sid"] == sid(61))
                         ].get("fh") == []


def test_title_lookups_reach_every_session_while_the_database_keeps_changing(root, monkeypatch):
    """M7: the same first lookups were retried on every change; later sessions starved."""
    monkeypatch.setattr(codex_mod, "DB_LOOKUPS", 2)
    for n in range(70, 76):
        Session(root, n, start=T0 + timedelta(seconds=n)).started()
    con = make_db(root)
    reader, state = CodexReader(root=root), {}
    poll(reader, state)  # two lookups, both miss: no rows yet
    tried = {x["sid"] for x in state["sess"].values() if x.get("dbt")}
    assert len(tried) == 2
    old = T0 - timedelta(days=20)  # too old for the time cursor: point lookups only
    want = {}
    for n in range(70, 76):
        if sid(n) not in tried:  # the two tried first never get a row
            add_thread(con, id=sid(n), title=f"Kite job {n}", at=old)
            want[aid(n)] = f"Kite job {n}"
    con.commit()
    titles = {}
    for i in range(4):
        add_thread(con, id=f"other-{i}", title="x", at=NOW)  # the database keeps changing
        con.commit()
        titles.update({a["id"]: a["title"] for a in poll(reader, state).agents})
    con.close()
    assert {k: titles.get(k) for k in want} == want
