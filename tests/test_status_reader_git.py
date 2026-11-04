"""The git reader: reflogs to commit and push events, with no git process.

Every repository here is an invented folder under ``tmp_path``; the autouse guard fails a test
that touches a real one.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from _reader_guard import guard
from lamptown.status import events as ev
from lamptown.status.readers import BUILTIN, make_reader
from lamptown.status.readers.git import GitReader, find_repo, parse_line
from lamptown.status.readers.tail import Budget
from lamptown.status.store import iso, read_status, status_path, update_status
from lamptown.status.watch import Watcher

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=1)
WHO = "Lamp Lighter <lamp@example.com>"  # an author: must never show
SUBJECT = "Fix the save slots for real"  # a commit message: must never show


@pytest.fixture(autouse=True)
def _no_real_repo(monkeypatch, tmp_path):
    guard(monkeypatch, tmp_path)


def sha(n: int) -> str:
    return f"{n:040x}"


class FakeRepo:
    """An invented repository: just the files the reader looks at."""

    def __init__(self, base: Path, name: str = "paper-kite", branch: str = "main",
                 gitdir: Path | None = None):
        self.root = base / name
        self.git = gitdir or self.root / ".git"
        (self.git / "logs").mkdir(parents=True, exist_ok=True)
        (self.git / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
        self.common = self.git
        self.n = 0

    @staticmethod
    def line(msg: str, when: datetime, old: str | None = None, new: str | None = None) -> bytes:
        return (f"{old or sha(0)} {new or sha(1)} {WHO} {int(when.timestamp())} +0300\t{msg}\n"
                ).encode()

    def head(self, msg: str, when: datetime) -> None:
        self.n += 1
        self._add(self.git / "logs" / "HEAD", self.line(msg, when, sha(self.n - 1), sha(self.n)))

    def push(self, branch: str, when: datetime, msg: str = "update by push",
             new: str | None = None) -> None:
        self.n += 1
        path = self.common / "logs" / "refs" / "remotes" / "origin" / Path(*branch.split("/"))
        self._add(path, self.line(msg, when, sha(self.n - 1), new or sha(self.n)))

    @staticmethod
    def _add(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as fh:
            fh.write(data)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def poll(reader, state, now=NOW, budget=None):
    return reader.poll(state, now, budget=budget or Budget())


def kinds(batch):
    return [e["k"] for e in batch.events]


def assert_private(*things):
    text = json.dumps(things, default=str)
    for bit in ("Lamp Lighter", "lamp@example.com", SUBJECT, "Merge made", "moving from"):
        assert bit not in text


# ---------------------------------------------------------------- commits and pushes
def test_commit_and_merge_lines_are_commits(tmp_path):
    repo = FakeRepo(tmp_path / "work")
    repo.head("commit (initial): Start the kite", at(1))
    repo.head(f"commit: {SUBJECT}", at(2))
    repo.head(f"commit (amend): {SUBJECT}", at(3))
    repo.head("checkout: moving from main to feature", at(4))
    repo.head("merge feature: Merge made by the 'ort' strategy.", at(5))
    repo.head("merge hotfix: Fast-forward", at(6))
    repo.head("reset: moving to HEAD~1", at(7))
    repo.head("rebase (pick): Fix", at(8))
    repo.head("cherry-pick: Fix", at(9))
    repo.head("commit (merge): Merge branch 'x'", at(10))
    repo.head("pull: Fast-forward", at(11))
    reader, state = GitReader(repos=[str(repo.root)]), {}
    b = poll(reader, state)
    assert kinds(b) == ["commit"] * 5  # an amend replaces a commit: not another one
    assert [e["t"] for e in b.events] == [at(m) for m in (1, 2, 5, 9, 10)]
    e = b.events[0]
    assert e["id"].startswith("git-") and e["folder"] == str(repo.root)
    assert (e["title"], e["project"], e["tool"], e["plot"]) == ("paper-kite", "paper-kite",
                                                                 "git", "boathouse")
    assert len({x["id"] for x in b.events}) == 1 and b.agents == []
    assert_private(b.events, state)
    assert str(repo.root) not in json.dumps(state)  # cursors are hashes


def test_pushes_from_remote_branch_reflogs(tmp_path):
    repo = FakeRepo(tmp_path / "work")
    repo.push("main", at(1))
    repo.push("main", at(2), msg="fetch: fast-forward")
    repo.push("feature/save-slots", at(3))
    repo.push("old", at(4), new="0" * 40)  # a branch deleted by push: no push to show
    repo.push("main", at(5), msg="pull: fast-forward")
    b = poll(GitReader(repos=[str(repo.root)]), {})
    assert kinds(b) == ["push", "push"]
    assert sorted(e["t"] for e in b.events) == [at(1), at(3)]
    assert all(e["folder"] == str(repo.root) for e in b.events)


def test_incremental_reads_and_a_restart_from_saved_state(tmp_path):
    repo = FakeRepo(tmp_path / "work")
    repo.head(f"commit: {SUBJECT}", at(1))
    repo.push("main", at(2))
    reader, state = GitReader(repos=[str(repo.root)]), {}
    assert kinds(poll(reader, state)) == ["commit", "push"]
    assert poll(reader, state).events == []
    budget = Budget()
    poll(reader, state, budget=budget)
    assert budget.left == budget.total  # nothing changed: stat only

    repo.head("commit: More", at(3))
    assert kinds(poll(reader, state)) == ["commit"]

    saved = json.loads(json.dumps(state))
    fresh = GitReader(repos=[str(repo.root)])
    assert poll(fresh, copy.deepcopy(saved)).events == []  # a restart replays nothing
    repo.push("main", at(4))
    first = poll(GitReader(repos=[str(repo.root)]), copy.deepcopy(saved))
    again = poll(GitReader(repos=[str(repo.root)]), copy.deepcopy(saved))  # rolled back
    assert kinds(first) == kinds(again) == ["push"]

    lost = copy.deepcopy(saved)
    lost["files"].clear()  # cursors gone, the high-water marks kept
    assert kinds(poll(GitReader(repos=[str(repo.root)]), lost)) == ["push"]


def test_old_lines_are_skipped(tmp_path):
    repo = FakeRepo(tmp_path / "work")
    repo.head("commit: Long ago", NOW - timedelta(days=10))
    repo.head("commit: Today", at(1))
    b = poll(GitReader(repos=[str(repo.root)]), {})
    assert [e["t"] for e in b.events] == [at(1)]


# ---------------------------------------------------------------- worktrees, rewrites, gaps
def test_a_worktree_reads_its_own_head_and_the_shared_remote_logs(tmp_path):
    base = tmp_path / "work"
    main = FakeRepo(base, "kite")
    wt_git = main.git / "worktrees" / "kite-save"
    wt = FakeRepo(base, "kite-save", branch="save-slots", gitdir=wt_git)
    wt.root.mkdir(parents=True)
    (wt.root / ".git").write_text("gitdir: ../kite/.git/worktrees/kite-save\n")
    (wt_git / "commondir").write_text("../..\n")
    wt.common = main.git
    found = find_repo(str(wt.root))
    assert found is not None and Path(found.common) == main.git and Path(found.gitdir) == wt_git

    main.head("commit: On main", at(1))
    wt.head("commit: In the worktree", at(2))
    main.push("main", at(3))
    main.push("save-slots", at(4))
    b = poll(GitReader(repos=[str(main.root), str(wt.root)]), {})
    got = sorted((e["k"], e["t"], Path(e["folder"]).name, e["project"]) for e in b.events)
    assert got == [("commit", at(1), "kite", "kite"), ("commit", at(2), "kite-save", "kite-save"),
                   ("push", at(3), "kite", "kite"), ("push", at(4), "kite-save", "kite-save")]

    only_wt = poll(GitReader(repos=[str(wt.root)]), {})  # the main folder isn't watched
    assert sorted((e["k"], e["t"]) for e in only_wt.events) == [
        ("commit", at(2)), ("push", at(3)), ("push", at(4))]


def test_a_rewritten_or_truncated_reflog_counts_nothing_twice(tmp_path):
    repo = FakeRepo(tmp_path / "work")
    for m in range(1, 6):
        repo.head(f"commit: Step {m}", at(m))
    reader, state = GitReader(repos=[str(repo.root)]), {}
    assert len(poll(reader, state).events) == 5

    log = repo.git / "logs" / "HEAD"
    keep = log.read_bytes().splitlines(keepends=True)[-2:]
    new = FakeRepo.line("commit: Step 6", at(6), sha(5), sha(6))
    tmp = log.with_name("HEAD.lock")
    tmp.write_bytes(b"".join(keep) + new)
    os.replace(tmp, log)  # what reflog expire does: a new, shorter file
    b = poll(reader, state)
    assert [e["t"] for e in b.events] == [at(6)]

    log.write_bytes(b"".join(keep))  # truncated in place, only old lines left
    assert poll(reader, state).events == []
    repo.head("commit: Step 7", at(7))
    assert [e["t"] for e in poll(reader, state).events] == [at(7)]


def test_missing_reflogs_and_folders_are_no_error(tmp_path):
    bare = tmp_path / "work" / "no-logs"
    (bare / ".git").mkdir(parents=True)
    plain = tmp_path / "work" / "plain"
    plain.mkdir()
    broken = tmp_path / "work" / "broken"
    broken.mkdir()
    (broken / ".git").write_text("this is not a gitdir line\n")
    gone = tmp_path / "work" / "gone"
    reader = GitReader(repos=[str(bare), str(plain), str(broken), str(gone), ""])
    state = {}
    b = poll(reader, state)
    assert b.events == [] and b.agents == []
    assert find_repo(str(broken)) is None and find_repo(str(gone)) is None
    (bare / ".git" / "logs").mkdir()
    FakeRepo._add(bare / ".git" / "logs" / "HEAD", FakeRepo.line("commit: Now", at(1)))
    assert kinds(poll(reader, state)) == ["commit"]


def test_a_big_reflog_starts_near_its_end(tmp_path):
    repo = FakeRepo(tmp_path / "work")
    lines = [FakeRepo.line(f"commit: Old {i}", NOW - timedelta(days=20), sha(i), sha(i + 1))
             for i in range(6000)]
    FakeRepo._add(repo.git / "logs" / "HEAD", b"".join(lines))
    repo.head("commit: Recent", at(1))
    budget = Budget(total=8 * 1024 * 1024, per_file=64 * 1024)
    b = GitReader(repos=[str(repo.root)]).poll({}, NOW, budget=budget)
    assert budget.total - budget.left <= 64 * 1024
    assert [e["t"] for e in b.events] == [at(1)]


def test_parse_line_keeps_ids_time_and_the_lead_only():
    raw = FakeRepo.line(f"commit: {SUBJECT}", at(1), sha(1), sha(2)).rstrip(b"\n")
    assert parse_line(raw) == (sha(1), sha(2), int(at(1).timestamp()), "commit")
    assert parse_line(b"garbage") is None
    assert parse_line(b"short ids 1 +0000\tcommit: x") is None


# ---------------------------------------------------------------- live agents
def _agent(status: Path, folder: Path, started: datetime, sid: str = "codex-019e0001-111"):
    update_status(sid, status, now=NOW - timedelta(minutes=1), title="Save slots",
                  state="working", agent="codex", kind="task", started=iso(started),
                  **{"x-folder": str(folder)})
    return sid


def test_commits_and_pushes_count_on_the_live_agent_in_that_folder(tmp_path):
    status = tmp_path / "status"
    repo = FakeRepo(tmp_path / "work")
    sid = _agent(status, repo.root / "src", started=at(10))  # works in a sub-folder
    repo.head("commit: Before the agent", at(5))
    repo.head(f"commit: {SUBJECT}", at(11))
    repo.head("commit: Another", at(12))
    repo.push("main", at(13))
    reader, state = GitReader(status_dir=str(status)), {}  # no repos: the agent's folder
    b = poll(reader, state)
    mine = [e for e in b.events if e["id"] == sid]
    assert [e["k"] for e in mine] == ["commit", "commit", "push"]
    assert all("title" not in e and e["folder"] == str(repo.root) for e in mine)
    other = [e for e in b.events if e["id"] != sid]
    assert [(e["k"], e["t"]) for e in other] == [("commit", at(5))]  # before it started
    doc = read_status(status_path(sid, status))
    assert b.agents == [{"id": sid, "at": doc["updated"], "x-commits": 2, "x-pushed": True}]
    assert_private(b.events, b.agents, state)


def test_a_ci_record_is_never_credited_with_commits_or_pushes(tmp_path):
    # --- live: L11 ci_gh --- the ci_gh reader's record names the repository's folder and is
    # updated at every check, often more recently than the agent: it is no agent
    status = tmp_path / "status"
    repo = FakeRepo(tmp_path / "work")
    sid = _agent(status, repo.root, started=at(10))  # updated a minute ago
    ci = "ci-gh-0123456789"
    update_status(ci, status, now=NOW - timedelta(seconds=10), title="Tests", state="working",
                  agent="ci", kind="job", started=iso(at(0)),
                  **{"x-folder": str(repo.root), "x-ci": "running"})
    repo.head(f"commit: {SUBJECT}", at(11))
    repo.push("main", at(13))
    b = poll(GitReader(status_dir=str(status)), {})
    assert [(e["k"], e["id"]) for e in b.events] == [("commit", sid), ("push", sid)]
    assert [c["id"] for c in b.agents] == [sid]
    # with only the CI record in the folder: it names no folder to watch, and gets nothing
    alone = tmp_path / "status-ci-only"
    update_status(ci, alone, now=NOW - timedelta(seconds=10), title="Tests", state="working",
                  agent="ci", kind="job", started=iso(at(0)), **{"x-folder": str(repo.root)})
    b = poll(GitReader(status_dir=str(alone)), {})
    assert b.events == [] and b.agents == []
    # a configured repository is still read: its events are credited to no one
    b = poll(GitReader(repos=[str(repo.root)], status_dir=str(alone)), {})
    assert [e["k"] for e in b.events] == ["commit", "push"]
    assert all(e["id"] != ci for e in b.events) and b.agents == []


def test_the_watcher_writes_the_counts_and_one_event_each(tmp_path):
    status = tmp_path / "status"
    repo = FakeRepo(tmp_path / "work")
    sid = _agent(status, repo.root, started=at(10))
    with Watcher(status, [GitReader(status_dir=str(status))], tz=UTC) as w:
        w.tick(NOW - timedelta(seconds=30))
        repo.head("commit: One", at(11))
        repo.head("commit: Two", at(12))
        repo.push("main", at(13))
        res = w.tick(NOW)
        assert not res.errors
        repo.head("commit: Three", NOW - timedelta(seconds=5))
        w.tick(NOW + timedelta(seconds=3))
    doc = read_status(status_path(sid, status))
    assert doc["x-commits"] == 3 and doc["x-pushed"] is True
    assert doc["state"] == "working" and doc["title"] == "Save slots"
    logged = [e for _, es in ev.read_days(status, 2, now=NOW, tz=UTC) for e in es
              if e["id"] == sid and e["k"] in ("commit", "push")]
    assert [e["k"] for e in logged] == ["commit", "commit", "push", "commit"]
    assert all(e["title"] == "Save slots" and e["tool"] == "codex" for e in logged)


def test_without_a_live_agent_events_only(tmp_path):
    status = tmp_path / "status"
    repo = FakeRepo(tmp_path / "work")
    update_status("codex-idle", status, now=NOW - timedelta(hours=3), title="Old",
                  state="ok", agent="codex", kind="task", **{"x-folder": str(repo.root)})
    repo.head("commit: Alone", at(30))
    b = poll(GitReader(repos=[str(repo.root)], status_dir=str(status)), {})
    assert b.agents == [] and [e["id"][:4] for e in b.events] == ["git-"]


# ---------------------------------------------------------------- registry, guard
def test_options_and_the_built_in_table(tmp_path):
    assert BUILTIN["git"] == "lamptown.status.readers.git:make_reader"
    repo = FakeRepo(tmp_path / "work")
    reader = make_reader({"kind": "git", "repo": str(repo.root)})
    assert reader.name == "git" and reader.repos == [os.path.normpath(str(repo.root))]
    reader = make_reader({"kind": "git", "repos": [str(repo.root)], "days": 3})
    assert reader.days == 3 and reader.status_dir is None


def test_the_guard_stops_a_read_of_a_real_repository():
    real = Path(__file__).resolve().parents[1] / ".git"
    if not real.exists():
        pytest.skip("not run from a git checkout")
    with pytest.raises(pytest.fail.Exception):
        os.stat(real / "logs" / "HEAD")


# ---------------------------------------------------------------- L5-fix (review-L5)
def test_an_agent_back_from_a_quiet_spell_counts_no_commit_twice(tmp_path):
    """B1: a log no longer watched keeps its high-water mark, so a comeback skips old lines."""
    status = tmp_path / "status"
    repo = FakeRepo(tmp_path / "work")
    sid = "codex-0c1d2e3f4a5b"

    def agent(state_, updated):
        update_status(sid, status, now=updated, title="Save slots", state=state_,
                      agent="codex", kind="task", started=iso(T0), **{"x-folder": str(repo.root)})

    agent("working", at(1))
    repo.head("commit: one", at(2))
    repo.head("commit: two", at(3))
    reader, state = GitReader(status_dir=str(status)), {}
    b = reader.poll(state, at(4), budget=Budget())
    assert kinds(b) == ["commit", "commit"] and b.agents[0]["x-commits"] == 2
    update_status(sid, status, now=at(4), **{"x-commits": 2})

    agent("ok", at(5))  # the turn ends; quiet for longer than LIVE_FOR: not watched
    assert reader.poll(state, at(50), budget=Budget()).events == []
    agent("working", at(60))  # back: same record, same start
    b = reader.poll(state, at(61), budget=Budget())
    assert b.events == [] and b.agents == []
    repo.head("commit: three", at(62))
    b = reader.poll(state, at(63), budget=Budget())
    assert kinds(b) == ["commit"] and b.agents[0]["x-commits"] == 3


def test_unwatched_high_water_marks_age_out_and_stay_bounded(tmp_path, monkeypatch):
    from lamptown.status.readers import git as git_mod

    monkeypatch.setattr(git_mod, "MAX_HW", 3, raising=False)
    repos = [FakeRepo(tmp_path / "work", f"kite-{i}") for i in range(5)]
    for i, repo in enumerate(repos):
        repo.head("commit: x", at(i))
    reader, state = GitReader(repos=[str(r.root) for r in repos]), {}
    poll(reader, state)
    assert len(state["hw"]) == 5  # all watched: all kept
    reader.repos = []
    poll(reader, state)
    assert len(state["hw"]) == 3  # none watched: the newest MAX_HW kept
    poll(reader, state, now=NOW + timedelta(days=8))
    assert state["hw"] == {}  # older than days: gone


def test_a_reflog_time_past_year_3000_is_skipped_not_fatal(tmp_path):
    """B4: one out-of-range time used to raise on every poll, for good."""
    repo = FakeRepo(tmp_path / "work")
    log = repo.git / "logs" / "HEAD"
    FakeRepo._add(log, f"{sha(0)} {sha(1)} {WHO} 99999999999 +0000\tcommit: far\n".encode())
    FakeRepo._add(log, f"{sha(1)} {sha(2)} {WHO} {'9' * 30} +0000\tcommit: huge\n".encode())
    repo.n = 2
    repo.head("commit: now", at(1))
    reader, state = GitReader(repos=[str(repo.root)]), {}
    assert [e["t"] for e in poll(reader, state).events] == [at(1)]
    assert poll(reader, state).events == []
    assert parse_line(f"{sha(0)} {sha(1)} {WHO} 32503680001 +0000\tcommit: x".encode()) is None


def test_one_poll_never_returns_more_than_the_event_cap(tmp_path, monkeypatch):
    """M1: the cap was checked between files only; the dropped events were lost for good."""
    from lamptown.status.readers import git as git_mod

    monkeypatch.setattr(git_mod, "MAX_EVENTS", 510)  # 500 kept free: 10 per poll
    repo = FakeRepo(tmp_path / "work")
    for m in range(25):
        repo.head(f"commit: {m}", at(m))
    for m in range(5):
        repo.push("main", at(30 + m))
    reader, state = GitReader(repos=[str(repo.root)]), {}
    seen = []
    for _ in range(6):
        state = json.loads(json.dumps(state))  # saved and loaded, as the Watcher does
        b = poll(reader, state)
        assert len(b.events) <= 10
        seen += [(e["k"], e["t"]) for e in b.events]
    assert sorted(seen) == sorted([("commit", at(m)) for m in range(25)]
                                  + [("push", at(30 + m)) for m in range(5)])


def test_an_amend_is_not_another_commit(tmp_path):
    """M3: a commit and its amend are one commit."""
    repo = FakeRepo(tmp_path / "work")
    repo.head(f"commit: {SUBJECT}", at(1))
    repo.head(f"commit (amend): {SUBJECT}", at(2))
    assert [e["t"] for e in poll(GitReader(repos=[str(repo.root)]), {}).events] == [at(1)]


def test_a_relative_agent_folder_is_never_read(tmp_path, monkeypatch):
    """M4: a relative x-folder resolved against the Watcher's own working folder."""
    status = tmp_path / "status"
    repo = FakeRepo(tmp_path / "work")
    repo.head("commit: in the Watcher's folder", at(11))
    monkeypatch.chdir(repo.root)
    _agent(status, Path("."), started=at(10))
    _agent(status, Path("src"), started=at(10), sid="codex-0000000000aa")
    b = poll(GitReader(status_dir=str(status)), {})
    assert b.events == [] and b.agents == []


def test_found_repositories_are_pruned_and_looked_up_again(tmp_path, monkeypatch):
    """M6: the folder -> repo cache only holds this poll's folders, and is checked again."""
    from lamptown.status.readers import git as git_mod

    refind = getattr(git_mod, "REFIND", timedelta(minutes=5))
    status = tmp_path / "status"
    outer = FakeRepo(tmp_path / "work")
    inner_folder = outer.root / "kite-inner"
    sid = _agent(status, inner_folder, started=at(10))
    outer.head("commit: outer", at(11))
    reader, state = GitReader(status_dir=str(status)), {}
    b = poll(reader, state)
    assert [Path(e["folder"]).name for e in b.events] == ["paper-kite"]
    inner = FakeRepo(outer.root, "kite-inner")  # `git init` in the nearer folder
    inner.head("commit: inner", at(12))
    assert poll(reader, state, now=NOW + timedelta(seconds=10)).events == []  # cached
    b = poll(reader, state, now=NOW + refind + timedelta(seconds=1))
    assert [Path(e["folder"]).name for e in b.events] == ["kite-inner"]
    update_status(sid, status, now=NOW - timedelta(hours=2), state="ok")
    poll(reader, state, now=NOW + refind * 2)
    assert reader._found == {}  # the agent's folder is no longer in use


def test_a_repository_in_the_home_folder_never_shows_the_login_name(tmp_path):
    """B5 (git side): a dotfiles repo in ~ names no project."""
    home = Path(os.path.expanduser("~"))
    (home / ".git" / "logs").mkdir(parents=True)
    (home / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    FakeRepo._add(home / ".git" / "logs" / "HEAD", FakeRepo.line("commit: x", at(1)))
    b = poll(GitReader(repos=[str(home)]), {})
    assert len(b.events) == 1
    assert home.name not in json.dumps(ev.clean_event(b.events[0]))
