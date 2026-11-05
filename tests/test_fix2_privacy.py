"""Fix round 2, privacy (fix2-PRIV): web addresses show none of themselves, ordinary dotted
words stay, more e-mail shapes are cut, private projects leave no name in the readers' saved
state, and a worktree never leads into a private repository.

Every name, path and address here is invented. Addresses are built with ``AT`` so this file
holds none.
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
from lamptown.status.events import name_only
from lamptown.status.readers import claude_code as cc
from lamptown.status.readers import codex as codex_mod
from lamptown.status.readers import make_reader
from lamptown.status.readers.ci_gh import Answer, CiGhReader
from lamptown.status.readers.codex import CodexReader
from lamptown.status.readers.git import GitReader, find_repo, private_roots
from lamptown.status.store import update_status
from lamptown.status.watch import Watcher
from test_live_privacy import NOW, _deny_reads_under, _Repo

AT = "@"


@pytest.fixture(autouse=True)
def _no_real_tools(monkeypatch, tmp_path):
    guard(monkeypatch, tmp_path)


# ---------------------------------------------------------------- PRIV 1 and 3: addresses
@pytest.mark.parametrize(("text", "shown"), [
    # a web address shows none of itself, with a scheme or without
    ("see github.com/you", "see link"),
    ("fix github.com/you/private-repo now", "fix link now"),
    ("see linkedin.com/in/you-there", "see link"),
    ("see x.com/you_there today", "see link today"),
    ("fix www.example.com/you/private-repo", "fix link"),
    ("corp-internal.example.com:8443/team/secret-thing", "link"),
    ("see https://example.com/you/secret-app.", "see link."),
    ("(see https://example.com/you)", "(see link)"),
    ("clone ssh://git" + AT + "github.com/you/r.git", "clone link"),
    ("open GitHub.COM/you", "open link"),
    ("open bücher.de/you", "open link"),  # an IDN host
    # an scp remote too
    (f"fix git{AT}github.com:you/private-repo.git", "fix link"),
    (f"fix git{AT}github.com:private-repo.git, then push", "fix link, then push"),
    (f"pull you{AT}buildbox:~/kite now", "pull link now"),
    # PRIV 3: localhost and IP hosts with a path
    ("open localhost:3000/you", "open link"),
    ("open localhost/you/x", "open link"),
    ("open 10.0.0.5/you/x", "open link"),
    ("open 10.0.0.5:8080/you", "open link"),
    # PRIV 3: e-mail shapes: a bare host, any letters, "%40", an IP
    (f"ssh you{AT}buildbox", "ssh someone"),
    (f"ssh you{AT}localhost now", "ssh someone now"),
    (f"write jörg{AT}exämple.de", "write someone"),
    ("mail you%40example.org", "mail someone"),
    (f"ssh you{AT}10.0.0.5", "ssh someone"),
    (f"ssh you{AT}dev", "ssh someone"),  # a dev box, not a version tag
    # round 1 shapes stay cut
    (f"ping you{AT}corp-internal.example.com", "ping someone"),
    (f"mailto:you{AT}example.org", "mailto:someone"),
    (f"<you{AT}example.org>.", "<someone>."),
    # a file or editor URL is still a path: its last part stays
    ("Open file:///home/you/kite/x.py", "Open x.py"),
    ("Open vscode://file/d:/kite/x.py now", "Open x.py now"),
])
def test_addresses_show_none_of_themselves(text, shown):
    out = name_only(text, 120)
    assert out == shown
    for bit in ("you", "private-repo", "secret", "github", "example", "buildbox", "jörg"):
        assert bit not in out.replace("Open x.py", "")


# the reviewer's list of ordinary words: none is an address, all stay whole
WORDS = [
    "km/h", "I/O", "TCP/IP", "and/or", "save/load", "e.g./i.e.", "v0.2/v0.3",
    "Node.js/Deno", "Vue.js/React", "ASP.NET/Core migration", "Socket.IO/WebRTC bridge",
    "main.go/main_test.go", "app.vue/app.svelte", "data.csv/xlsx export", "St.Louis/Chicago trip",
    "fix README.md/docs", "U.S./EU rules", "x.py/y.py", "Mr.Smith/Jones", "config.yaml/toml",
    "Rust/Go", "client/server", "Q3/Q4 plan", "50/50 split", "24/7 support", "A/B test",
    "npm i @types/node", "pkg@1.2.3", "ping @you", "meet at 10:30", "time@12:30 ok",
    "C++/C#", "1.5/2.0", "ratio 3.2/kg", "lamptown.toml/[live]",
    "build.gradle/kts", "index.php/route", "style.scss/less", "jQuery.ui/widgets",
    "Mon/Tue", "front-end/back-end", "his/her", "w/o tests", "w/ tests", "n/a", "he/him",
    "docs/live.md", "src/x", "a.b.c/d", "Chapter 1.2/3", "files/dirs", "Python3.12/3.13",
    "/help command", "use /compact", "type /exit", "visit example.com", "Update README.md",
    # versions and git names after "@" are no address
    "bump actions/checkout@v4", "npm i lodash@4.17.21", "npm i react@latest", "pin x@main",
    "pin actions/cache@1a2b3c4d5e", "git show HEAD@{1}", "bump node@lts",
    "app.swift/app.dart", "lib.rs/main.rs", "main.cc/main.h", "Settings.app",
]


@pytest.mark.parametrize("text", WORDS)
def test_ordinary_dotted_words_stay(text):
    assert name_only(text, 120) == text


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
