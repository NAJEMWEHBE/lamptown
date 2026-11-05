"""Release-review privacy fixes (fix-PRIV): private projects stay unread, titles carry no
addresses, and the Watcher's saved state (``cursors.json``) keeps no path and no raw title.

Every name, path and address here is invented. Addresses are built with ``AT`` so this file
holds none.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from _reader_guard import guard
from lamptown import livetown
from lamptown.config import parse_config
from lamptown.status import events as ev
from lamptown.status.events import name_only, project_of
from lamptown.status.readers import ci_gh, make_reader
from lamptown.status.readers import git as git_mod
from lamptown.status.readers.ci_gh import Answer, CiGhReader
from lamptown.status.readers.git import GitReader
from lamptown.status.store import update_status
from lamptown.status.watch import Watcher

AT = "@"
NOW = datetime(2026, 11, 4, 10, 0, 0, tzinfo=UTC)
SHA = "a1" * 20


@pytest.fixture(autouse=True)
def _no_real_tools(monkeypatch, tmp_path):
    guard(monkeypatch, tmp_path)


# ---------------------------------------------------------------- titles (privacy 2 and 4)
@pytest.mark.parametrize(("text", "shown"), [
    # e-mail addresses become a neutral word
    (f"ping you{AT}corp-internal.example.com about it", "ping someone about it"),
    (f"mail first.last{AT}corp-internal.biz", "mail someone"),
    (f"ssh you{AT}host.internal", "ssh someone"),
    (f"Email me at <you{AT}corp.dev>.", "Email me at <someone>."),
    # fix2-PRIV: a web address without a scheme, and an scp remote, become "link", whole
    ("fix github.com/you/private-repo", "fix link"),
    ("fix www.example.com:8080/you/private-repo now", "fix link now"),
    (f"git{AT}github.com:you/private-repo.git", "link"),
    (f"clone git{AT}gitlab.example.org:team/kite", "clone link"),
    # a rooted Windows path with no drive
    ("Edit \\Users\\you now", "Edit ~"),
    ("Edit \\home\\you\\x.py", "Edit x.py"),
    # a hard character inside the user name: the rest of the name goes too
    ('Edit /home/you"r name/x.py', "Edit ~"),
    ("Edit /home/you`r/x.py", "Edit ~"),
    ("Edit /home/you<r/x.py", "Edit ~"),
    ("Edit C:/Users/you|r/x", "Edit ~"),
    ('Edit C:/Users/you"x', "Edit ~"),
    # ... but a hard character that closes the path stays
    ('Edit "C:\\Users\\you" and more', 'Edit "~" and more'),
    ("Edit <C:\\Users\\you>", "Edit <~>"),
    # a date-named chat folder never shows its words
    ("cd C:\\Users\\you\\chats\\2026-03-14-plan-the-launch", "cd chats"),
    ("cd C:\\Users\\you\\chats\\2026-03-14-plan-the-launch now", "cd chats now"),
    ("Edit ~/chats/2026-03-14-plan-the-launch/", "Edit chats"),
    ("cd C:\\Users\\you\\2026-03-14-plan-the-launch", "cd 2026-03-14"),
    ("Edit /work/chats/2026-03-14-plan-the-launch/notes.md", "Edit notes.md"),
])
def test_titles_keep_no_address_user_name_or_chat_words(text, shown):
    assert name_only(text, 120) == shown


@pytest.mark.parametrize("text", [
    "Speed in km/h", "I/O bound", "save/load test", "and/or", "TCP/IP", "Fix CI/CD",
    "npm i lodash@4.17.21", "bump @types/node", "ping @you", "Fix Node.js/Deno build",
    "v1.2/v1.3 diff", "use 3.5/4 rule", "visit example.com", "docs/live.md",
    "time 10:30", "Update README.md",
])
def test_ordinary_words_stay(text):
    assert name_only(text, 120) == text


@pytest.mark.parametrize(("folder", "project"), [
    ("/Volumes/Your Name HD", ""),
    ("/media/you/USB", ""),
    ("/run/media/you/Your Stick", ""),
    ("/media/you", ""),
    ("/Volumes/Data/paper-kite", "paper-kite"),
    ("/media/you/USB/paper-kite", "paper-kite"),
])
def test_a_drive_label_is_never_a_project_name(folder, project):
    assert project_of(None, folder) == project
    assert project_of("Your Name HD" if "Name" in folder else None, folder) == project


def test_the_book_gets_no_address(tmp_path):
    ev.append(tmp_path, [{"t": NOW, "id": "codex-aaaabbbbcccc", "k": "start",
                          "title": f"Fix login for first.last{AT}corp-internal.biz"}], tz=UTC)
    [(_day, [event])] = ev.read_days(tmp_path, 1, now=NOW, tz=UTC)
    assert event["title"] == "Fix login for someone"


# ---------------------------------------------------------------- private folders (privacy 1)
class _Repo:
    """An invented repository with a GitHub remote, a recent push and a recent commit."""

    def __init__(self, base: Path, name: str) -> None:
        self.root = base / name
        git = self.root / ".git"
        (git / "logs" / "refs" / "remotes" / "origin").mkdir(parents=True)
        (git / "HEAD").write_text("ref: refs/heads/main\n")
        (git / "config").write_text(f'[remote "origin"]\n\turl = git{AT}github.com:'
                                    f"team/{name}.git\n")
        stamp = int((NOW - timedelta(minutes=4)).timestamp())
        who = f"Lamp Lighter <lamp{AT}example.com>"
        (git / "logs" / "refs" / "remotes" / "origin" / "main").write_text(
            f"{'0' * 40} {SHA} {who} {stamp} +0000\tupdate by push\n")
        (git / "logs" / "HEAD").write_text(
            f"{'0' * 40} {SHA} {who} {stamp - 60} +0000\tcommit: hi\n")


def _deny_reads_under(monkeypatch, root: Path, touched: list[str]) -> None:
    """Record any open, stat or listing of a path in or under ``root``."""
    mark = os.path.normcase(str(root))

    def check(path):
        try:
            text = os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(path))))
        except TypeError:
            return
        if text == mark or text.startswith(mark + os.sep):
            touched.append(text)

    def wrap(fn):
        def watched(path=".", *args, **kwargs):
            if not isinstance(path, int):
                check(path)
            return fn(path, *args, **kwargs)
        return watched

    for mod, name in ((builtins, "open"), (io, "open"), (os, "open"), (os, "stat"),
                      (os, "lstat"), (os, "scandir"), (os, "listdir")):
        monkeypatch.setattr(mod, name, wrap(getattr(mod, name)))


def test_private_projects_are_never_read_asked_about_or_named(tmp_path, monkeypatch):
    status, clients, work = tmp_path / "status", tmp_path / "clients", tmp_path / "work"
    secret, kite = _Repo(clients, "acme-secret-merger"), _Repo(work, "paper-kite")
    for rid, repo in (("claude-code-secret1", secret), ("claude-code-kite1", kite)):
        update_status(rid, status, now=NOW - timedelta(minutes=5), title="Work",
                      state="working", agent="claude-code", **{"x-folder": str(repo.root)})
    cfg = parse_config(f"""
[live]
status_dir = "{status.as_posix()}"
private_dirs = ["clients"]

[[live.reader]]
kind = "git"

[[live.reader]]
kind = "ci_gh"
repos = ["{(clients / 'acme-secret-merger').as_posix()}"]
""", tmp_path / "lamptown.toml")
    entries = [livetown.reader_entry(cfg, e) for e in cfg.live.readers]
    assert all(e["private_dirs"] == [str(clients)] for e in entries)
    git_r, ci_r = (make_reader(e) for e in entries)
    calls: list[list[str]] = []

    def runner(argv, timeout):
        calls.append(list(argv))
        return Answer(code=0, out=b"[]")

    ci_r.runner, ci_r.background = runner, False
    ci_r._gh = lambda now: sys.executable  # any absolute program: the runner is fake
    touched: list[str] = []
    _deny_reads_under(monkeypatch, clients, touched)
    w = Watcher(readers=[git_r, ci_r], tz=UTC, **livetown.watcher_options(cfg))
    notes = []
    for i in range(3):
        notes += w.tick(NOW + timedelta(seconds=i)).errors
    w.close()
    assert touched == []  # nothing of the private project was opened, listed or read
    assert [c[4] for c in calls] == ["team/paper-kite"]  # gh asked about the open one only
    assert not any("acme" in n for n in notes) and not any("acme" in n for n in ci_r.notes)
    saved = (status / ".lamptown" / "cursors.json").read_text(encoding="utf-8")
    assert "acme" not in saved and "clients" not in saved


def test_a_private_status_folder_names_no_live_folder(tmp_path):
    status = tmp_path / "hush" / "status"
    repo = _Repo(tmp_path / "work", "paper-kite")
    update_status("claude-code-kite1", status, now=NOW, state="working",
                  **{"x-folder": str(repo.root)})
    for cls in (GitReader, CiGhReader):
        r = cls(status_dir=str(status), private_dirs=[str(tmp_path / "hush")])
        assert r.status_dir is None
    git = GitReader(status_dir=str(status), private_dirs=["hush"], config_dir=str(tmp_path))
    assert git.status_dir is None  # relative entries count from the config's folder


def test_a_record_whose_project_path_is_private_is_skipped(tmp_path):
    status = tmp_path / "status"
    repo = _Repo(tmp_path / "work", "paper-kite")
    update_status("claude-code-kite1", status, now=NOW, state="working",
                  **{"x-folder": str(repo.root), "x-project": str(tmp_path / "clients" / "x")})
    priv = [str(tmp_path / "clients")]
    assert GitReader(status_dir=str(status), private_dirs=priv)._live(NOW) == []
    assert CiGhReader(status_dir=str(status), private_dirs=priv)._live_folders(NOW) == []
    assert len(GitReader(status_dir=str(status))._live(NOW)) == 1


# ---------------------------------------------------------------- newest first (engine 2)
def test_the_newest_records_are_seen_past_the_cap(tmp_path):
    status, work = tmp_path / "status", tmp_path / "work" / "kite"
    work.mkdir(parents=True)
    old = NOW - timedelta(days=60)
    for i in range(git_mod.MAX_STATUS_FILES):  # two months of finished sessions
        update_status(f"claude-code-{i:06d}", status, now=old, state="ok",
                      **{"x-folder": str(tmp_path / "work" / f"p{i % 40}")})
        os.utime(status / f"claude-code-{i:06d}.json", (old.timestamp(), old.timestamp()))
    update_status("codex-aaaabbbbcccc", status, now=NOW, state="working",
                  **{"x-folder": str(work)})
    git_live = [a["id"] for a in GitReader(status_dir=str(status))._live(NOW)]
    assert git_live == ["codex-aaaabbbbcccc"]
    assert CiGhReader(status_dir=str(status))._live_folders(NOW) == [str(work)]


# ---------------------------------------------------------------- saved state (privacy 3)
def _ci_runner(state: str):
    def runner(argv, timeout):
        runs = [{"databaseId": 1, "headSha": SHA, "status": state,
                 "conclusion": "" if state != "completed" else "failure",
                 "workflowName": "tests", "createdAt": "2026-11-04T09:57:00Z",
                 "updatedAt": "2026-11-04T09:58:00Z"}]
        return Answer(code=0, out=json.dumps(runs).encode())
    return runner


def test_ci_state_holds_no_path_and_a_restart_still_follows_the_run(tmp_path):
    status = tmp_path / "status"
    repo = _Repo(tmp_path / "work", "secret-client-repo")
    update_status("claude-code-kite1", status, now=NOW - timedelta(minutes=5),
                  state="working", **{"x-folder": str(repo.root)})

    def reader(state: str) -> CiGhReader:
        r = CiGhReader(status_dir=str(status), runner=_ci_runner(state), background=False)
        r._gh = lambda now: sys.executable
        return r

    with Watcher(status, [reader("in_progress")], tz=UTC) as w:
        w.tick(NOW)
    saved = json.loads((status / ".lamptown" / "cursors.json").read_text(encoding="utf-8"))
    text = json.dumps(saved)
    for path in (repo.root, repo.root.parent, tmp_path):  # the project's *name* may be kept
        assert json.dumps(str(path))[1:-1] not in text and str(path) not in text
    [open_runs] = [s["open"] for s in saved["readers"].values() if "open" in s]
    assert open_runs and all(set(v) == {"at"} for v in open_runs.values())  # record ids only
    # two hours later the agent is long gone (no live folder), the push is past the hour:
    # the run is found again from its own record and followed to its result
    update_status("claude-code-kite1", status, now=NOW - timedelta(minutes=5), state="ok")
    later = NOW + timedelta(hours=2)
    os.utime(status / "claude-code-kite1.json", (NOW.timestamp(), NOW.timestamp()))
    r2 = reader("completed")
    with Watcher(status, [r2], tz=UTC) as w:
        w.tick(later)
    assert r2.calls == 1
    [rid] = [p.stem for p in status.glob("ci-gh-*.json")]
    doc = json.loads((status / f"{rid}.json").read_text(encoding="utf-8"))
    assert doc["x-ci"] == "red"
    old = {"open": {rid: {"at": "2026-11-04T10:00:00Z", "root": str(repo.root)}}}
    assert ci_gh._open_runs(old["open"], NOW) == {rid: {"at": "2026-11-04T10:00:00Z"}}


def test_codex_titles_are_saved_cut(tmp_path):
    from lamptown.status.readers.codex import CodexReader
    from test_status_reader_codex import NOW as CNOW
    from test_status_reader_codex import Session, make_db, sid

    root = tmp_path / "codex"
    root.mkdir()
    s = Session(root, 1)
    s.started()
    s.item()
    make_db(root, rows=[{"id": sid(1), "first": "something else",
                         "title": f"Fix C:\\Users\\you\\secret-client\\x.py for "
                                  f"first.last{AT}corp-internal.biz"}])
    status = tmp_path / "status"
    w = Watcher(status_dir=status, readers=[CodexReader(root=root)])
    for _ in range(3):
        w.tick(CNOW)
    w.close()
    for path in status.rglob("*.json*"):
        text = path.read_text(encoding="utf-8")
        for word in ("secret-client", "corp-internal", "first.last", "Users"):
            assert word not in text, (path.name, word)
    state: dict = {}
    CodexReader(root=root).poll(state, CNOW)
    [key] = state["sess"]
    state["sess"][key]["ti"] = "Fix C:\\Users\\you\\secret-client\\y.py"  # an older version's
    state.pop("tc")  # fix2-PRIV: ... which wrote no title-rule mark
    CodexReader(root=root).poll(state, CNOW)
    assert "secret-client" not in state["sess"][key]["ti"]


def test_a_status_line_never_saves_the_reader_path_text(tmp_path):
    # the git reader's saved state is keyed by hashes, as before (a guard for the fix above)
    repo = _Repo(tmp_path / "work", "secret-client-repo")
    state: dict = {}
    GitReader(repos=[str(repo.root)]).poll(state, NOW)
    assert "secret-client-repo" not in json.dumps(state)
