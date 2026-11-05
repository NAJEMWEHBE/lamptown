"""0.2.1 review fixes, round 3, batch R3b: private agents' tool, the narrowed history mask,
the Watcher state gone, and stale record locks."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta

from lamptown.build import build
from lamptown.config import parse_config
from lamptown.livetown import context, live_payload, read_records
from lamptown.status import events as ev
from lamptown.status import store
from lamptown.status import watch as watch_mod
from lamptown.status.readers import Batch
from lamptown.status.readers import git as git_mod
from lamptown.status.readers.git import GitReader
from lamptown.status.store import update_status
from lamptown.status.watch import Watcher

T0 = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
AGENT_TEXT = "qzprivagent acme merger bot"  # a private record's free-text agent field
AGENT_WORD = "qzprivagent"


class FakeReader:
    name = "r3b-fake"

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


def _logged(status, now=T0):
    return [e for _, es in ev.read_days(status, 3, now=now, tz=UTC) for e in es]


def _pages_with(out, needle):
    needle = needle.lower()
    return sorted(p.name for p in out.iterdir()
                  if p.is_file() and needle in p.read_text("utf-8").lower())


def _config(root, status, private, repos=()):
    lines = [f"[live]\nstatus_dir = '{status.as_posix()}'\n",
             "private_dirs = [" + ", ".join(f"'{p.as_posix()}'" for p in private) + "]\n"]
    if repos:
        lines.append("[[live.reader]]\nkind = 'git'\nrepos = ["
                     + ", ".join(f"'{r.as_posix()}'" for r in repos) + "]\n")
    return parse_config("".join(lines), root / "lamptown.toml")


# ---------------------------------------------------------------- 1: a private agent's tool
def test_a_private_records_free_text_agent_reaches_no_page_and_no_log_line(tmp_path):
    """p7: the record's title and id are masked; its ``agent`` text must be too."""
    status, hidden = tmp_path / "status", tmp_path / "hidden"
    now = datetime.now(UTC).replace(microsecond=0)
    update_status("qzprivid-acme", status, now=now - timedelta(minutes=3), title="Deal",
                  state="working", agent=AGENT_TEXT, **{"x-folder": str(hidden / "deal")})
    with Watcher(status, [], private_dirs=[hidden], tz=UTC) as w:
        w.tick(now - timedelta(minutes=2))
        update_status("qzprivid-acme", status, now=now - timedelta(minutes=1), state="waiting")
        w.tick(now)
    assert AGENT_WORD not in _log_text(status).lower()
    saved = (status / ".lamptown" / "cursors.json").read_text("utf-8").lower()
    assert AGENT_WORD not in saved  # the Watcher's snapshot keeps no private name either
    out = tmp_path / "out"
    build(_config(tmp_path, status, [hidden]), out_dir=out, now=now, tz=UTC)
    assert "town-live.js" in os.listdir(out) and "town-history.js" in os.listdir(out)
    assert _pages_with(out, AGENT_WORD) == []


def test_a_private_record_keeps_a_known_tool_word(tmp_path):
    """Known tool words stay (the page colours and spells them; ``ci`` is no figure)."""
    status, hidden = tmp_path / "status", tmp_path / "hidden"
    now = datetime.now(UTC).replace(microsecond=0)
    for sid, agent in (("a1", "codex"), ("a2", "Claude-Code"), ("a3", "ci"), ("a4", AGENT_TEXT),
                       ("a5", None)):
        extra = {"agent": agent} if agent else {}
        update_status(sid, status, now=now, title=f"T {sid}", state="working",
                      **{"x-folder": str(hidden / sid)}, **extra)
    cfg = _config(tmp_path, status, [hidden])
    payload = live_payload(read_records(status), cfg, now, ctx=context(cfg))
    tools = sorted(a["tool"] for a in payload["agents"])
    assert tools == sorted(["codex", "claude-code", "ci", "", ""])
    assert all(a["private"] for a in payload["agents"])


def test_a_build_before_the_watcher_masks_a_newly_private_agents_tool(tmp_path):
    """Logged while public under its free-text agent, then its folder made private: the
    build's history mask (before any Watcher tick) drops the text as well."""
    status, hidden = tmp_path / "status", tmp_path / "hidden"
    update_status("kite", status, now=T0, title="Kite", state="working", agent=AGENT_TEXT,
                  **{"x-folder": str(hidden / "kite")})
    with Watcher(status, [], tz=UTC) as w:
        w.tick(T0)
        update_status("kite", status, now=T0 + timedelta(seconds=1), state="waiting")
        w.tick(T0 + timedelta(seconds=2))
    assert AGENT_WORD in _log_text(status).lower()  # the setup: logged while public
    out = tmp_path / "out"
    build(_config(tmp_path, status, [hidden]), out_dir=out, now=T0 + timedelta(seconds=4),
          tz=UTC)
    assert "town-history.js" in _pages_with(out, "PRIVATE AGENT")
    assert _pages_with(out, AGENT_WORD) == []
    with Watcher(status, [], private_dirs=[hidden], tz=UTC) as w:  # and the Watcher's rewrite
        w.tick(T0 + timedelta(seconds=5))
    assert AGENT_WORD not in _log_text(status).lower()


def test_already_masked_old_log_lines_lose_a_free_text_tool(tmp_path):
    """A log from before this fix holds masked lines with the free text as their tool: the
    history never shows it, and the Watcher's rewrite cleans it."""
    status, hidden = tmp_path / "status", tmp_path / "hidden"
    status.mkdir()
    with Watcher(status, [], tz=UTC) as w:
        w.tick(T0)
        pid = w.private_id("kite")
    ev.append(status, [{"t": T0, "id": pid, "k": "start", "title": "Private agent",
                        "tool": AGENT_TEXT, "plot": "workshop"}], tz=UTC)
    out = tmp_path / "out"
    build(_config(tmp_path, status, [hidden]), out_dir=out, now=T0 + timedelta(seconds=4),
          tz=UTC)
    assert _pages_with(out, AGENT_WORD) == []
    with Watcher(status, [], private_dirs=[hidden], tz=UTC) as w:
        w.tick(T0 + timedelta(seconds=5))
    assert AGENT_WORD not in _log_text(status).lower()


# ---------------------------------------------------------------- 2: the narrowed mask
def _repo(path):
    (path / ".git").mkdir(parents=True)
    return path


def _git_line(repo, title, when):
    return {"t": when, "id": git_mod._id_for(os.path.normpath(str(repo))), "k": "commit",
            "n": 1, "title": title, "project": title, "tool": "git", "plot": "boathouse"}


def _seed_public_git(tmp_path):
    """A status folder whose Watcher ran with no private folder, then a log with: commits of a
    configured public repo, commits of a live agent's public repo (git-only lines: no agent
    that moment) and a reader event of an agent with no record and no place."""
    status = tmp_path / "status"
    configured = _repo(tmp_path / "open" / "qzconfrepo")
    agent_repo = _repo(tmp_path / "open" / "qzagentrepo")
    update_status("gull", status, now=T0, title="Gull", state="working", agent="codex",
                  **{"x-folder": str(agent_repo)})
    with Watcher(status, [], tz=UTC) as w:
        w.tick(T0)
    ev.append(status, [_git_line(configured, "qzconfrepo", T0 + timedelta(seconds=1)),
                       _git_line(agent_repo, "qzagentrepo", T0 + timedelta(seconds=2)),
                       {"t": T0 + timedelta(seconds=3), "id": "repo-z", "k": "commit",
                        "n": 1, "title": "QZNOPLACE", "tool": "codex"}], tz=UTC)
    return status, configured


def test_an_unrelated_private_folder_keeps_public_git_history(tmp_path):
    """p4 C: git-only lines whose repo the config or a record tells stay named; only the agent
    whose place can't be told is masked."""
    status, configured = _seed_public_git(tmp_path)
    other = tmp_path / "unrelated-private"
    reader = GitReader(repos=[str(configured)], status_dir=str(status))
    with Watcher(status, [reader], private_dirs=[other], tz=UTC) as w:
        result = w.tick(T0 + timedelta(minutes=1))
    assert not result.errors
    text = _log_text(status)
    assert "qzconfrepo" in text and "qzagentrepo" in text
    assert "QZNOPLACE" not in text
    assert [e["k"] for e in _logged(status) if e["id"].startswith("private-")] == ["commit"]


def test_a_build_before_the_watcher_agrees_on_public_git_history(tmp_path):
    status, configured = _seed_public_git(tmp_path)
    other = tmp_path / "unrelated-private"
    out = tmp_path / "out"
    build(_config(tmp_path, status, [other], repos=[configured]), out_dir=out,
          now=T0 + timedelta(minutes=1), tz=UTC)
    assert "town-history.js" in _pages_with(out, "qzconfrepo")
    assert "town-history.js" in _pages_with(out, "qzagentrepo")
    assert _pages_with(out, "QZNOPLACE") == []


def test_a_configured_repo_in_the_newly_private_folder_is_masked(tmp_path):
    status, hidden = tmp_path / "status", tmp_path / "hidden"
    secret = _repo(hidden / "qzsecretrepo")
    status.mkdir()
    with Watcher(status, [], tz=UTC) as w:
        w.tick(T0)
    ev.append(status, [_git_line(secret, "qzsecretrepo", T0 + timedelta(seconds=1))], tz=UTC)
    out = tmp_path / "out"
    build(_config(tmp_path, status, [hidden], repos=[secret]), out_dir=out,
          now=T0 + timedelta(minutes=1), tz=UTC)
    assert _pages_with(out, "qzsecretrepo") == []
    reader = GitReader(repos=[str(secret)], status_dir=str(status))
    with Watcher(status, [reader], private_dirs=[hidden], tz=UTC) as w:
        w.tick(T0 + timedelta(minutes=2))
    assert "qzsecretrepo" not in _log_text(status)
    assert [e["k"] for e in _logged(status) if e["id"].startswith("private-")] == ["commit"]


# ---------------------------------------------------------------- 3: no Watcher state
def _cleared_record_log(tmp_path):
    status, hidden = tmp_path / "status", tmp_path / "hidden"
    update_status("kite", status, now=T0, title="QZBUILDUNKNOWN", state="ok",
                  **{"x-folder": str(hidden / "secret")})
    with Watcher(status, [], tz=UTC) as w:
        w.tick(T0)
        update_status("kite", status, now=T0 + timedelta(seconds=1), state="waiting")
        w.tick(T0 + timedelta(seconds=2))
    assert "QZBUILDUNKNOWN" in _log_text(status)
    (status / "kite.json").unlink()  # `status clear`
    (status / ".lamptown" / "cursors.json").unlink()  # the Watcher's state is gone too
    return status, hidden


def test_remask_pending_with_no_state_file_but_a_log(tmp_path):
    status, hidden = _cleared_record_log(tmp_path)
    roots = Watcher(status, [], private_dirs=[hidden], tz=UTC).private_roots
    assert watch_mod.remask_pending(status, roots)
    empty = tmp_path / "empty-status"
    empty.mkdir()
    assert not watch_mod.remask_pending(empty, roots)  # no log either: nothing to mask
    out = tmp_path / "out"
    build(_config(tmp_path, status, [hidden]), out_dir=out, now=T0 + timedelta(seconds=4),
          tz=UTC)
    assert _pages_with(out, "QZBUILDUNKNOWN") == []


def test_a_watcher_starting_with_no_state_masks_the_log_it_finds(tmp_path):
    """The Watcher's side of the same edge: its first save says the log is masked for these
    folders, so its first tick must have masked it."""
    status, hidden = _cleared_record_log(tmp_path)
    with Watcher(status, [], private_dirs=[hidden], tz=UTC) as w:
        w.tick(T0 + timedelta(seconds=5))
    assert "QZBUILDUNKNOWN" not in _log_text(status)
    out = tmp_path / "out"
    build(_config(tmp_path, status, [hidden]), out_dir=out, now=T0 + timedelta(seconds=6),
          tz=UTC)
    assert _pages_with(out, "QZBUILDUNKNOWN") == []


# ---------------------------------------------------------------- 4: stale record locks
def _stale_lock(record):
    lock = store.lock_path(record)
    lock.write_bytes(b"dead-writer\n")
    old = time.time() - 60
    os.utime(lock, (old, old))
    return lock


def test_clearing_a_record_removes_its_stale_lock_but_not_a_fresh_one(tmp_path):
    update_status("gull", tmp_path, now=T0, title="Gull", state="ok")
    update_status("tern", tmp_path, now=T0, title="Tern", state="ok")
    stale = _stale_lock(tmp_path / "gull.json")
    fresh = store.lock_path(tmp_path / "tern.json")
    fresh.write_bytes(b"live-writer\n")
    removed, _foreign, failed = store.clear_files(tmp_path, lambda doc: True)
    assert sorted(p.name for p in removed) == ["gull.json", "tern.json"] and not failed
    assert not stale.exists()
    assert fresh.exists()  # a writer holding it now removes its own


def test_tidying_an_own_record_removes_its_stale_lock(tmp_path):
    reader = FakeReader()
    w = Watcher(tmp_path, [reader], expire_after=60, keep_days=1, tz=UTC)
    w.tick(T0)
    reader.agents = [{"id": "claude-code-000001", "at": T0, "title": "Session", "state": "ok",
                      "agent": "claude-code"}]
    w.tick(T0 + timedelta(seconds=1))
    record = tmp_path / "claude-code-000001.json"
    assert record.exists()
    lock = _stale_lock(record)
    later = T0 + timedelta(days=3)
    for i in range(3):
        w.tick(later + timedelta(seconds=3 * i))
    w.close()
    assert not record.exists()
    assert not lock.exists()
    assert json.loads((tmp_path / ".lamptown" / "cursors.json").read_text("utf-8"))
