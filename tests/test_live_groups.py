"""Folders named by date (``2026-03-14-plan-the-launch``) and ``[[live.group]]``.

Tools make one such folder per chat and put the chat's first words after the date, so those
words never show: the folder is named by its parent folder (or the date alone). ``[[live.group]]``
names every folder in or under ``dir`` with one fixed project name. Every name here is made up.
"""

from __future__ import annotations

import json
from datetime import UTC, timedelta
from pathlib import Path

import pytest

from lamptown import build as B
from lamptown import livetown as L
from lamptown.config import ConfigError, parse_config
from lamptown.status import adapters
from lamptown.status import events as ev
from lamptown.status.adapters import claude_code, folder_name, project_folder
from lamptown.status.store import update_status
from lamptown.status.watch import Watcher
from test_livetown import NOW, _all_text, _fake_home, _payload, at, config, rec

SLUG = "plan-the-moon-heist"  # stands in for a chat's first words: never shown
DRIVE = "C" + ":"  # a Windows drive, joined at run time (no drive path in the source)


# ----------------------------------------------------------------------------- the default
@pytest.mark.parametrize(("path", "name"), [
    (f"/work/chats/2026-11-03-{SLUG}", "chats"),
    (f"C:\\Users\\you\\.chats\\2026-11-03-{SLUG}", ".chats"),
    (f"/work/chats/2026-11-03_{SLUG}/", "chats"),
    (f"/work/chats/2026-11-03 {SLUG}", "chats"),
    ("/work/chats/2026-11-03T10-22-05", "chats"),
    (f"/work/notes/2026-01-01-old-words/2026-01-02-{SLUG}", "notes"),   # dated parents too
    (f"2026-11-03-{SLUG}", "2026-11-03"),                               # a bare name: the date
    (f"/2026-11-03-{SLUG}", "2026-11-03"),                              # under a root
    (DRIVE + f"\\2026-11-03-{SLUG}", "2026-11-03"),                     # a drive root
    (f"/home/you/2026-11-03-{SLUG}", "2026-11-03"),                     # a home: never its name
    (f"C:\\Users\\you\\2026-11-03-{SLUG}", "2026-11-03"),
    ("/work/2026-11-03", "2026-11-03"),                                 # just a date: kept
    ("/work/v2026-11-03-launch", "v2026-11-03-launch"),                 # not a leading date
    ("/work/paper-kite", "paper-kite"),
])
def test_a_folder_named_by_date_never_shows_its_words(path, name):
    assert folder_name(path) == name


def test_the_home_folder_never_names_a_dated_folder(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    assert folder_name(str(home / f"2026-11-03-{SLUG}")) == "2026-11-03"
    assert folder_name(f"~/2026-11-03-{SLUG}") == "2026-11-03"
    assert folder_name(str(home / "chats" / f"2026-11-03-{SLUG}")) == "chats"


def test_hooks_and_names_use_the_same_rule(tmp_path):
    cwd = f"/work/chats/2026-11-03-{SLUG}"
    assert project_folder(cwd) == "chats"
    update = claude_code.translate({"session_id": "s9", "cwd": cwd,
                                    "hook_event_name": "UserPromptSubmit"})
    doc = adapters.apply(update, tmp_path)
    assert doc["title"] == "chats" and doc["x-folder"] == cwd   # the path stays for private_dirs
    assert ev.project_of(None, cwd) == "chats"
    assert ev.project_of(f"2026-11-03-{SLUG}", None) == "2026-11-03"   # an old record's name
    line = ev.clean_event({"t": "2026-11-03T10:00:00Z", "id": "codex-a", "k": "start",
                           "title": f"Working in {cwd} now", "project": cwd})
    assert SLUG not in json.dumps(line) and line["project"] == "chats"


# ----------------------------------------------------------------------------- config
def _live(tmp_path: Path, body: str):
    return parse_config("[live]\n" + body, tmp_path / "lamptown.toml").live


def test_a_group_is_read(tmp_path):
    live = _live(tmp_path, '[[live.group]]\ndir = "work/chats"\nname = "  my   chats "\n'
                           '[[live.group]]\ndir = "/work/notes/*/daily"\nname = "daily notes"\n')
    assert [g.name for g in live.groups] == ["my chats", "daily notes"]
    assert Path(live.groups[0].dir) == tmp_path / "work" / "chats"
    assert parse_config("[live]\n").live.groups == ()


@pytest.mark.parametrize(("body", "words"), [
    ('group = "chats"', "array of tables"),
    ('[[live.group]]\nname = "chats"', "needs dir"),
    ('[[live.group]]\ndir = "/work/chats"', "needs dir"),
    ('[[live.group]]\ndir = "/work/chats"\nname = "chats"\nplot = "lab"', "plot"),
    ('[[live.group]]\ndir = "/work/chats"\nname = ""', "must not be empty"),
    ('[[live.group]]\ndir = "/work/chats"\nname = "   "', "must not be empty"),
    ('[[live.group]]\ndir = "/work/chats"\nname = "a/b"', "not a path"),
    ('[[live.group]]\ndir = "/work/chats"\nname = "~chats"', "not a path"),
    ('[[live.group]]\ndir = "/work/chats"\nname = "' + "x" * 41 + '"', "1 to 40"),
    ('[[live.group]]\ndir = "/work/chats"\nname = 3', "must be a string"),
    ('[[live.group]]\ndir = "~"\nname = "chats"', "home folder"),
    ('[[live.group]]\ndir = "/"\nname = "chats"', "home folder"),
    ('[[live.group]]\ndir = "/*/chats"\nname = "chats"', "home folder"),
])
def test_a_bad_group_is_a_config_error(tmp_path, body, words):
    with pytest.raises(ConfigError, match=words):
        _live(tmp_path, body)


def test_a_group_above_home_is_a_config_error(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    with pytest.raises(ConfigError, match="home folder"):
        _live(tmp_path, f'[[live.group]]\ndir = "{home.parent.as_posix()}"\nname = "x"\n')


# ----------------------------------------------------------------------------- grouping
def _groups(work: Path) -> str:
    w = work.as_posix()
    return (f'[[live.group]]\ndir = "{w}/chats"\nname = "chats"\n'
            f'[[live.group]]\ndir = "{w}/chats/pinned"\nname = "pinned-chats"\n'
            f'[[live.group]]\ndir = "{w}/secret/talks"\nname = "talks"\n')


def test_folders_in_a_group_share_its_name(tmp_path):
    cfg, work = config(tmp_path, _groups(work=tmp_path / "work"))
    docs = [rec("claude-code-1", "Chat one", "working", at(17, 40),
                **{"x-folder": str(work / "chats" / f"2026-11-03-{SLUG}"),
                   "x-project": "2026-11-03"}),
            rec("claude-code-2", "Chat two", "waiting", at(17, 41),
                **{"x-folder": str(work / "chats" / "deep" / "inside")}),
            rec("claude-code-3", "Pinned", "working", at(17, 42),
                **{"x-folder": str(work / "chats" / "pinned" / "a")}),   # the closer group wins
            rec("codex-4", "Elsewhere", "working", at(17, 43),
                **{"x-folder": str(work / "paper-kite")}),
            rec("codex-5", "By path", "working", at(17, 44),
                **{"x-project": str(work / "chats" / "x")}),             # an x-project path
            rec("codex-6", "Hidden", "working", at(17, 45),
                **{"x-folder": str(work / "secret" / "talks" / "a")})]   # private beats a group
    p = L.live_payload(docs, cfg, NOW)
    got = {a["id"]: a["project"] for a in p["agents"]}
    assert got["claude-code-1"] == got["claude-code-2"] == got["codex-5"] == "chats"
    assert got["claude-code-3"] == "pinned-chats" and got["codex-4"] == "paper-kite"
    assert [a["project"] for a in p["agents"] if a["private"]] == [""]
    assert sorted(x["name"] for x in p["projects"]) == ["CHATS", "PAPER KITE", "PINNED CHATS"]
    assert L.group_map(cfg)(str(work / "chatsx" / "a")) == ""   # a sibling, not inside


def test_without_a_group_a_dated_folder_shows_its_parent(tmp_path):
    cfg, work = config(tmp_path)
    doc = rec("claude-code-1", "Chat", "working", at(17, 40),
              **{"x-folder": str(work / "scratch" / f"2026-11-03-{SLUG}")})
    p = L.live_payload([doc], cfg, NOW)
    assert p["agents"][0]["project"] == "scratch"
    assert SLUG not in json.dumps(p)


def test_the_watcher_logs_the_group_name_and_the_build_never_shows_the_words(tmp_path):
    cfg, work = config(tmp_path, _groups(work=tmp_path / "work"))
    status = work / "status"
    chat = str(work / "chats" / f"2026-11-03-{SLUG}")
    with Watcher(readers=(), tz=UTC, **L.watcher_options(cfg)) as w:
        w.tick(NOW - timedelta(minutes=3))
        update_status("claude-code-7", status, now=NOW - timedelta(minutes=2), title="Chat",
                      state="working", agent="claude-code",
                      **{"x-folder": chat, "x-project": f"2026-11-03-{SLUG}"})
        w.tick(NOW - timedelta(minutes=2))
    logged = ev.read_day(ev.day_path(status, NOW.date()))
    assert logged and {e.get("project") for e in logged} == {"chats"}
    out = tmp_path / "out"
    B.build(cfg, out_dir=out, now=NOW, tz=UTC)
    assert SLUG not in _all_text(out)
    live = _payload(out / "town-live.js", "lamptownLive")
    assert [a["project"] for a in live["agents"]] == ["chats"]


def test_the_watcher_options_compare_equal_when_the_config_does(tmp_path):
    # the live feed rebuilds its Watcher only when these change (livefeed._watch_key)
    a, _ = config(tmp_path, _groups(work=tmp_path / "work"))
    b, _ = config(tmp_path, _groups(work=tmp_path / "work"))
    assert L.watcher_options(a) == L.watcher_options(b)
    plain = L.watcher_options(config(tmp_path)[0])
    assert L.watcher_options(a)["project_for"] != plain["project_for"]
