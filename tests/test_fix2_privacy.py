"""Fix round 2, privacy (fix2-PRIV): the book shows "link" for a web address, private
projects leave no name in the readers' saved state, and a worktree never leads into a
private repository. (Its title cases, addresses and ordinary dotted words, are in
tests/data/title_privacy.txt.)

Every name, path and address here is invented.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from datetime import UTC, timedelta
from pathlib import Path

import pytest

from _reader_guard import guard
from lamptown import livetown
from lamptown.config import parse_config
from lamptown.status import events as ev
from lamptown.status.readers import claude_code as cc
from lamptown.status.readers import codex as codex_mod
from lamptown.status.readers import make_reader
from lamptown.status.readers.ci_gh import Answer, CiGhReader
from lamptown.status.readers.codex import CodexReader
from lamptown.status.readers.git import GitReader, find_repo, private_roots
from lamptown.status.store import update_status
from lamptown.status.watch import Watcher
from test_live_privacy import NOW, _deny_reads_under, _Repo


@pytest.fixture(autouse=True)
def _no_real_tools(monkeypatch, tmp_path):
    guard(monkeypatch, tmp_path)


# ---------------------------------------------------------------- PRIV 1 and 3: addresses
# The title cases moved to tests/data/title_privacy.txt (tests/test_title_privacy.py).


def test_the_book_shows_link_not_a_handle(tmp_path):
    ev.append(tmp_path, [{"t": NOW, "id": "codex-aaaabbbbcccc", "k": "start",
                          "title": "Review github.com/you/private-repo"}], tz=UTC)
    [(_day, [event])] = ev.read_days(tmp_path, 1, now=NOW, tz=UTC)
    assert event["title"] == "Review link"


# ---------------------------------------------------------------- PRIV 4: reader state
def _codex_session(tmp_path: Path, folder: Path, title: str) -> Path:
    from test_status_reader_codex import Session, make_db, sid

    root = tmp_path / "codex"
    root.mkdir(exist_ok=True)
    s = Session(root, 1, cwd=str(folder))
    s.started()
    s.item()
    make_db(root, rows=[{"id": sid(1), "title": title, "first": "some other words"}])
    return root


@pytest.mark.parametrize("how", ["watcher", "reader"])
def test_codex_saves_no_private_name_or_title(tmp_path, how):
    from test_status_reader_codex import NOW as CNOW

    clients = tmp_path / "clients"
    secret = clients / "harbour-merger"
    secret.mkdir(parents=True)
    root = _codex_session(tmp_path, secret, "Harbour merger term sheet")
    status = tmp_path / "status"
    if how == "watcher":  # private folders handed on by the Watcher
        w = Watcher(status_dir=status, readers=[CodexReader(root=root)],
                    private_dirs=[str(clients)])
    else:  # ... or given to the reader itself, as the live town does
        w = Watcher(status_dir=status, private_dirs=[str(clients)],
                    readers=[CodexReader(root=root, private_dirs=[str(clients)])])
    for i in range(3):
        w.tick(CNOW + timedelta(seconds=i))
    w.close()
    saved = (status / ".lamptown" / "cursors.json").read_text(encoding="utf-8")
    assert "harbour" not in saved.lower() and "Harbour" not in saved
    logged = "".join(p.read_text(encoding="utf-8") for p in (status / "events").glob("*"))
    assert "harbour" not in logged.lower()


def test_codex_drops_what_an_older_run_saved_for_a_private_session(tmp_path):
    from test_status_reader_codex import NOW as CNOW

    clients = tmp_path / "clients"
    secret = clients / "harbour-merger"
    secret.mkdir(parents=True)
    root = _codex_session(tmp_path, secret, "Harbour merger term sheet")
    state: dict = {}
    CodexReader(root=root).poll(state, CNOW)  # an older run: no private folders known
    CodexReader(root=root).poll(state, CNOW)
    [s] = state["sess"].values()
    assert s["p"] == "harbour-merger" and s["ti"] == "Harbour merger term sheet"
    reader = CodexReader(root=root, private_dirs=[str(clients)])
    batch = reader.poll(state, CNOW + timedelta(seconds=1))  # nothing new in the file
    assert "harbour" not in json.dumps(state).lower()
    assert s.get("pv") == 1
    assert all("harbour" not in json.dumps(a).lower().replace(
        json.dumps(str(secret).lower())[1:-1], "") for a in batch.agents)
    # no longer private (the folder left private_dirs): the title comes back from the database
    open_again = CodexReader(root=root)
    open_again.poll(state, CNOW + timedelta(seconds=2))
    open_again.poll(state, CNOW + timedelta(seconds=3))
    assert s.get("ti") == "Harbour merger term sheet" and "pv" not in s


def test_codex_cuts_saved_titles_once_not_every_tick(tmp_path, monkeypatch):
    from test_status_reader_codex import NOW as CNOW

    root = _codex_session(tmp_path, tmp_path / "work" / "kite", "Fix the kite")
    state: dict = {}
    reader = CodexReader(root=root)
    reader.poll(state, CNOW)
    reader.poll(state, CNOW)  # the title comes from the database
    state.pop("tc")  # as an older version saved it
    calls: list[str] = []
    real = codex_mod.name_only
    monkeypatch.setattr(codex_mod, "name_only", lambda t, n: calls.append(t) or real(t, n))
    for i in range(3):  # the Watcher hands every poll a copy of the state
        state = copy.deepcopy(state)
        reader.poll(state, CNOW + timedelta(seconds=i))
    assert calls == ["Fix the kite"]


def test_claude_saves_no_private_title(tmp_path, monkeypatch):
    from test_status_reader_claude import CWD, SID, T0, Tree, alive, assistant, prompt, title

    procs: dict = {}
    monkeypatch.setattr(cc, "process_info", lambda pid: procs.get(pid, (False, None)))
    tree = Tree(tmp_path / "claude")
    alive(procs)
    tree.session("busy", name="Harbour night shift", source="user")
    tree.write(title("ai-title", "Harbour merger memo"), title("custom-title", "Harbour deal"),
               prompt(-10), assistant(-5, stop="end_turn"))
    tree.sub("a1", prompt(-8), finished=False, description="Read the Harbour contract")
    private = [str(Path(CWD).parent)]
    state: dict = {}
    reader = cc.ClaudeCodeReader(tree.root, private_dirs=private)
    reader.rescan = 0
    batch = reader.poll(state, T0)
    assert "harbour" not in json.dumps(state).lower()
    main = [a for a in batch.agents if a["id"] == cc.make_id("claude-code", SID)]
    assert main and main[0]["title"] == "Claude Code"
    # the same through make_reader (the live town's options)
    r2 = make_reader({"kind": "claude_code", "root": str(tree.root), "private_dirs": private})
    assert r2.private == private_roots(private)


def test_the_live_town_gives_codex_and_claude_its_private_folders(tmp_path):
    cfg = parse_config("""
[live]
private_dirs = ["clients"]

[[live.reader]]
kind = "codex"

[[live.reader]]
kind = "claude_code"
""", tmp_path / "lamptown.toml")
    for entry in cfg.live.readers:
        out = livetown.reader_entry(cfg, entry)
        assert out["private_dirs"] == [str(tmp_path / "clients")]
        reader = make_reader(out)
        assert reader.private == private_roots([str(tmp_path / "clients")])


# ---------------------------------------------------------------- PRIV 6: worktrees
def _worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A private repository in clients/, and a worktree of it outside (in work/)."""
    clients = tmp_path / "clients"
    secret = _Repo(clients, "harbour-merger")
    gitdir = secret.root / ".git" / "worktrees" / "wt"
    (gitdir / "logs").mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n")
    (gitdir / "HEAD").write_text("ref: refs/heads/side\n")
    wt = tmp_path / "work" / "harbour-wt"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {gitdir}\n")
    return clients, wt


def test_find_repo_never_follows_a_worktree_into_a_private_repo(tmp_path):
    clients, wt = _worktree(tmp_path)
    assert find_repo(str(wt)) is not None  # without private folders it is a repository
    roots = private_roots([str(clients)])
    touched: list[str] = []
    mp = pytest.MonkeyPatch()
    try:
        _deny_reads_under(mp, clients, touched)
        assert find_repo(str(wt), private=roots) is None
    finally:
        mp.undo()
    assert touched == []
    # a commondir that points into a private folder counts the same
    other = tmp_path / "work" / "plain"
    (other / ".git").mkdir(parents=True)
    (other / ".git" / "commondir").write_text(f"{clients / 'harbour-merger' / '.git'}\n")
    assert find_repo(str(other), private=roots) is None


def test_git_and_ci_readers_never_open_a_private_repo_through_a_worktree(tmp_path, monkeypatch):
    clients, wt = _worktree(tmp_path)
    status = tmp_path / "status"
    update_status("claude-code-wt1", status, now=NOW - timedelta(minutes=5), title="Work",
                  state="working", agent="claude-code", **{"x-folder": str(wt)})
    calls: list[list[str]] = []

    def runner(argv, timeout):
        calls.append(list(argv))
        return Answer(code=0, out=b"[]")

    git_r = GitReader(status_dir=str(status), private_dirs=[str(clients)])
    ci_r = CiGhReader(status_dir=str(status), private_dirs=[str(clients)], runner=runner,
                      background=False)
    ci_r._gh = lambda now: sys.executable
    touched: list[str] = []
    _deny_reads_under(monkeypatch, clients, touched)
    w = Watcher(readers=[git_r, ci_r], tz=UTC, status_dir=status, private_dirs=[str(clients)])
    for i in range(3):
        w.tick(NOW + timedelta(seconds=i))
    w.close()
    assert touched == [] and calls == []
    saved = (status / ".lamptown" / "cursors.json").read_text(encoding="utf-8")
    # the worktree's own folder name may be kept; nothing of the private repository is
    assert "harbour-merger" not in saved and "clients" not in saved
    assert os.path.isdir(clients)  # (the repository is there; it was just never opened)
