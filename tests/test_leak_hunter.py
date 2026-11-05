"""The leak hunter: canaries in every input the town reads, never in anything it writes.

Every case plants one unique, made-up marker (``qz`` and eight hex characters) through one input
(a status file field, a ``status run`` argument or its stderr, a Claude Code or Codex log, a room's
files, a config value), written in one form: a user path in each OS form (Windows with either
slash, POSIX, macOS, Git Bash, WSL, a lower-case drive, a file URL), an e-mail address, a token, a
bearer header, plain words (for what must never show at all), or a file name with an e-mail.
Then the real ``lamptown watch`` builds the town and runs the Watcher over it all, and no marker
may appear in any file it writes: the pages, the data and live files, the book, the event log,
the Watcher's state and the status files it writes.

The second half feeds every path and e-mail form to the privacy gate (``tools/privacy_gate.py``) as
text, as a file name, as a commit identity and in an annotated tag, and each must be caught.

To add an input: write a planter (``plant(world, text, marker)``) and add one line to ``INPUTS``
with the forms it must lose. A failure names the case and the output file, never the text. Every
marker, path and address here is invented; the text is put together at run time so this file
itself never trips the gate.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from _reader_guard import guard
from lamptown import cli, livefeed
from lamptown.livetown import MAX_AGENTS
from lamptown.status.adapters.wrapper import run_command
from lamptown.status.readers import claude_code as cc
from lamptown.status.store import update_status
from test_live_town_e2e import Codex, jsonl, z
from test_status_reader_codex import make_db

ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = ROOT / "tools" / "privacy_gate.py"

BS, AT, SL = "\\", "@", "/"  # pieces, so no planted path or address is written here whole
MAIL = "qzmail.net"          # an invented domain: not example.com, so the gate must flag it
BASE = "qzbase"              # the world's own folder: a full path in a page shows it


def marker(case: str) -> str:
    return "qz" + hashlib.sha256(case.encode()).hexdigest()[:8]


# ----------------------------------------------------------------------------- the forms
def _tail(m: str, sep: str = SL) -> str:
    return sep.join((m, "kite", "notes.txt"))


FORMS: dict[str, Callable[[str], str]] = {
    "win": lambda m: "C:" + BS + BS.join(("Users", _tail(m, BS))),
    "win-slash": lambda m: "C:" + SL + SL.join(("Users", _tail(m))),
    "posix": lambda m: SL + SL.join(("home", _tail(m))),
    "mac": lambda m: SL + SL.join(("Users", _tail(m))),
    "gitbash": lambda m: SL + SL.join(("c", "Users", _tail(m))),
    "wsl": lambda m: SL + SL.join(("mnt", "c", "Users", _tail(m))),
    "drive": lambda m: "d:" + SL + _tail(m),
    "file-url": lambda m: "file:" + SL * 3 + "C:" + SL + SL.join(("Users", _tail(m))),
    "email": lambda m: m + AT + MAIL,
    "token": lambda m: "--token=" + m,
    "bearer": lambda m: "Authorization: Bearer " + m,
    "words": lambda m: m + " plain words",
    "email-file": lambda m: "notes-" + m + AT + MAIL + ".md",
    "segment": lambda m: m,
    # --- review-fix R2a: more forms --- a path written with percent-escapes, a secret in JSON,
    # a secret flag and its next word, and bare well-known token shapes (put together here, so
    # no scanner sees a token in this file)
    "win-encoded": lambda m: "C%3A%5C" + "%5C".join(("Users", m, "kite", "notes.txt")),
    "posix-encoded": lambda m: "%2F" + "%2F".join(("home", m, "kite", "notes.txt")),
    "json-secret": lambda m: '{"token":"' + m + "\", 'api_key': '" + m + "'}",
    "flag-secret": lambda m: "--token " + m + " --password " + m,
    "raw-github": lambda m: "gh" + "p_" + m + PAD + " github" + "_pat_" + m + "_" + PAD + PAD,
    "raw-ai": lambda m: "s" + "k-proj-" + m + PAD + " gl" + "pat-" + m + PAD,
    "raw-chat": lambda m: "xo" + "xb-1234-" + m + " AK" + "IA" + m.upper() + "QZ0123",
    # --- end review-fix R2a ---
    # --- review-fix R3a: more forms --- Google, Hugging Face and npm tokens and a JWT; a PEM
    # private key (it masks the rest of a text, so it has a form of its own); a password glued
    # to "-p" and PASS= names; a URL's password; webhook and signed URLs; and home folders in
    # the forms home_or_root learned (bare, so the user name is the last part)
    # (each at most 64 characters: a status file's agent field holds no more)
    "raw-cloud": lambda m: "AI" + "za" + m + PAD * 2 + " h" + "f_" + m + PAD,
    "raw-jwt": lambda m: "np" + "m_" + m + PAD + " e" + "yJhbGci.eyJ" + m + ".c2ln",
    "pem": lambda m: "-----BEGIN " + "RSA PRIV" + "ATE KEY----- MIIE" + m + "AAAA",
    "glued-pass": lambda m: "mysql -p" + m + " PASS=" + m,
    "url-password": lambda m: "https://bob:" + m + AT + "example.com/kite.git",
    "url-hook": lambda m: "https://hooks." + "slack.com/services/T0/B0/" + m,
    "url-sig": lambda m: "https://h.io/c?sig=" + m + "&X-Amz-Signature=" + m,
    "var-home": lambda m: SL + SL.join(("var", "home", m)),
    "export-home": lambda m: SL + SL.join(("export", "home", m)),
    "unc-home": lambda m: BS * 2 + BS.join(("PC", "Users", m)),
    "docs-settings": lambda m: "C:" + BS + BS.join(("Documents and Settings", m)),
    # --- end review-fix R3a ---
}
PAD = "01234a"  # with a marker, the shortest run each token shape needs
FILE_LINK = "file:" + SL * 3 + "C:" + SL + SL.join(("Users", "{}", "notes.txt"))
DIRS = ("win", "win-slash", "posix", "mac", "gitbash", "wsl", "drive")  # a config path value
PATHS = (*DIRS, "file-url")
ENCODED = ("win-encoded", "posix-encoded")  # review-fix R2a
SECRETS = ("token", "bearer", "json-secret", "flag-secret", "raw-github", "raw-ai",
           "raw-chat")  # review-fix R2a
MORE_SECRETS = ("raw-cloud", "raw-jwt", "pem", "glued-pass")  # review-fix R3a
URL_SECRETS = ("url-password", "url-hook", "url-sig")         # review-fix R3a
HOMES = ("var-home", "export-home", "unc-home", "docs-settings")  # review-fix R3a
OLD_SAYS = (*PATHS, *ENCODED, "email", *SECRETS)
SAYS = (*OLD_SAYS, *MORE_SECRETS, *URL_SECRETS, *HOMES)  # what a text the town shows must lose
# what a text the town never shows must lose: "words" proves none of it shows, so the R3a
# forms add nothing there (and the live town shows at most MAX_AGENTS agents)
ALL = (*OLD_SAYS, "words")
# review-fix R2a: a room's own text (a job's result, an inbox title, a tool's name...) keeps its
# paths (the owner's config choice) but loses its secrets; a file name loses what a name may hold
ROOM_SAYS = (*SECRETS, *MORE_SECRETS, *URL_SECRETS)  # review-fix R3a: and the new ones
FILE_SAYS = ("email-file", "token", "flag-secret", "raw-github", *ENCODED,
             "raw-cloud", "raw-jwt", "glued-pass")  # review-fix R3a


# ----------------------------------------------------------------------------- the world
class World:
    """The made-up folders. Planters add to them; :meth:`finish` writes what is collected."""

    def __init__(self, base: Path, now: dt.datetime) -> None:
        self.base, self.now = base, now
        self.status, self.private, self.work = base / "status", base / "private", base / "work"
        self.claude_root, self.codex_root = base / "claude", base / "codex"
        for folder in (self.status, self.private, self.work, self.claude_root / "sessions",
                       self.codex_root):
            folder.mkdir(parents=True, exist_ok=True)
        self.sessions: dict[tuple[str, str], ClaudeSession] = {}
        self.threads: dict[tuple[str, str], dict] = {}
        self.pids: set[int] = set()
        self.reflogs: dict[Path, list[tuple[str, str]]] = {}
        self.lines: dict[Path, list[str]] = {}       # text files built line by line
        self.sources: dict[str, list[str]] = {}      # more sources, by building id
        self.live_private: list[str] = []            # more [live] private_dirs
        self.places: list[str] = []                  # [[live.place]] and [[live.group]] tables
        self.runs: list[tuple[list[str], str]] = []  # status run: (command, id)
        self.private_runs: list[tuple[list[str], str, str]] = []  # R3a: (command, id, agent)
        self.records: dict[tuple[bool, str], dict] = {}  # status files: (private, form)
        self.shown: set[str] = set()                 # ids of the public status files
        self.form = ""                               # the form of the case being planted
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n

    def add_line(self, path: Path, line: str) -> None:
        self.lines.setdefault(path, []).append(line)

    # The live town shows at most MAX_AGENTS agents, so the cases share them: one status file,
    # Claude Code session and Codex thread for each form, each case in its own field of it.
    def record(self, private: bool, own: str = "") -> dict:
        key = (private, own + self.form)  # review-fix R3a: ``own``: a record of its own
        if key not in self.records:
            n = self.next()
            folder = self.private / "plain" if private else self.work / "plain"
            self.records[key] = {"title": f"Heron {n}", "agent": "kitebot", "kind": "task",
                                 "state": "waiting", "message": "Waiting for you",
                                 "x-folder": str(folder)}
        return self.records[key]

    def claude(self, group: str, cwd: Path | None = None) -> ClaudeSession:
        key = (group, self.form if cwd is None else str(cwd))
        if key not in self.sessions:
            where = self.private if group == "private" else self.work
            self.sessions[key] = ClaudeSession(self, cwd or where / f"cc-{len(self.sessions)}")
        return self.sessions[key]

    def codex(self, group: str, cwd: Path | None = None) -> dict:
        key = (group, self.form if cwd is None else str(cwd))
        if key not in self.threads:
            where = self.private if group == "private" else self.work
            folder = cwd or where / f"cx-{len(self.threads)}"
            folder.mkdir(parents=True, exist_ok=True)
            start = self.now - dt.timedelta(minutes=4)
            log = Codex(self.codex_root, self.next(), folder, start)
            log.add("event_msg", "task_started", turn_id="t", started_at=0)
            self.threads[key] = {"log": log, "row": {"id": log.sid, "at": start,
                                                     "title": f"Otter {len(self.threads)}"}}
        return self.threads[key]

    def source(self, building: str, toml: str) -> None:
        self.sources.setdefault(building, []).append(f"  [[building.source]]\n  {toml}\n")

    def reflog(self, repo: Path, who: str, subject: str) -> None:
        self.reflogs.setdefault(repo, []).append((who, subject))

    def finish(self) -> None:
        for (private, _), doc in self.records.items():
            status_id = f"plant-{self.next()}"
            update_status(status_id, self.status, **doc)
            if not private:
                self.shown.add(status_id)
        for s in self.sessions.values():
            s.write()
        for t in self.threads.values():
            t["log"].add("response_item", "function_call_output", output="done")
        make_db(self.codex_root, [t["row"] for t in self.threads.values()]).close()
        for repo, entries in self.reflogs.items():
            logs = repo / ".git" / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            when = int((self.now - dt.timedelta(minutes=2)).timestamp())
            with open(logs / "HEAD", "ab") as fh:
                for i, (who, subject) in enumerate(entries):
                    fh.write(f"{i:040x} {i + 1:040x} {who} {when + i} +0300\t"
                             f"commit: {subject}\n".encode())
        for path, lines in self.lines.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class ClaudeSession:
    """One live Claude Code session (its own pid) working in ``cwd``."""

    def __init__(self, world: World, cwd: Path) -> None:
        n = world.next()
        self.world, self.cwd, self.pid = world, cwd, 6000 + n
        world.pids.add(self.pid)
        h = hashlib.sha256(f"claude {n}".encode()).hexdigest()
        self.sid = f"{h[:8]}-{h[8:12]}-4{h[13:16]}-8{h[17:20]}-{h[20:32]}"
        cwd.mkdir(parents=True, exist_ok=True)
        self.start = world.now - dt.timedelta(minutes=10)
        self.docs: list[dict] = []

    def line(self, kind: str, seconds_ago: float, **more) -> dict:
        return {"parentUuid": None, "isSidechain": False, "type": kind, "cwd": str(self.cwd),
                "sessionId": self.sid,
                "timestamp": z(self.world.now - dt.timedelta(seconds=seconds_ago)), **more}

    def meta(self, kind: str, field: str, text: str) -> None:
        self.docs.append({"type": kind, field: text, "sessionId": self.sid})

    def prompt(self, text: str) -> None:
        self.docs.append(self.line("user", 500, promptId="p", permissionMode="default",
                                   message={"role": "user", "content": text}))

    def said(self, content: list[dict], seconds_ago: float = 60) -> None:
        self.docs.append(self.line("assistant", seconds_ago, message={
            "model": "m", "id": f"msg{len(self.docs)}", "type": "message", "role": "assistant",
            "content": content, "stop_reason": "tool_use"}))

    def write(self) -> None:
        now = self.world.now
        session = {"pid": self.pid, "sessionId": self.sid, "cwd": str(self.cwd),
                   "startedAt": int(self.start.timestamp() * 1000), "procStart": "1",
                   "version": "9.9.9", "kind": "interactive", "entrypoint": "cli", "name": "",
                   "nameSource": "auto", "status": "busy",
                   "updatedAt": int(now.timestamp() * 1000),
                   "statusUpdatedAt": int(now.timestamp() * 1000)}
        root = self.world.claude_root
        (root / "sessions" / f"{self.pid}.json").write_text(json.dumps(session), "utf-8")
        project = root / "projects" / "".join(c if c.isalnum() else "-" for c in str(self.cwd))
        bash = {"type": "tool_use", "id": "b9", "name": "Bash", "input": {"command": "make"},
                "caller": {}}
        self.said([bash], seconds_ago=5)  # the newest line: the session is at work
        jsonl(project / f"{self.sid}.jsonl", *self.docs)


# ----------------------------------------------------------------------------- the planters
# Each planter is ``plant(world, text, marker)``; ``text`` is the marker in its case's form.
def _status(private: bool, **fields) -> Callable[[World, str, str], None]:
    """A status file written as a hook or a script would. ``fields`` name where the text goes:
    ``{"title": "{}"}`` puts it in the title."""

    def plant(w: World, text: str, m: str) -> None:
        doc = w.record(private)
        for key, value in fields.items():
            if isinstance(value, list):
                doc.setdefault(key, []).extend(_fill(value, text))
            else:
                doc[key] = _fill(value, text)

    return plant


def _fill(value, text: str):
    """``value`` with every ``{}`` in its strings (also in lists and tables) made ``text``."""
    if isinstance(value, str):
        return value.replace("{}", text)
    if isinstance(value, list):
        return [_fill(v, text) for v in value]
    return {k: _fill(v, text) for k, v in value.items()}


def _status_folder(w: World, text: str, m: str) -> None:
    n = w.next()
    update_status(f"plant-{n}", w.status, title=f"Heron {n}", agent="kitebot", kind="task",
                  state="working", **{"x-folder": str(w.private / f"{text}-kite")})


def _public_folder(w: World, text: str, m: str) -> None:
    """review-fix R3a: a public record's folder, in a record of its own (in the form's shared
    record, its x-project would name the project and hide what the folder gives)."""
    w.record(False, own="folder/")["x-folder"] = text


def _run_argv(w: World, text: str, m: str) -> None:
    w.runs.append(([sys.executable, str(w.base / "say.py"), "-", text], f"run-{w.next()}"))


def _run_stderr(w: World, text: str, m: str) -> None:
    said = w.base / "said" / f"{w.next()}.txt"
    said.parent.mkdir(exist_ok=True)
    said.write_text(text + "\nand a second line\n", encoding="utf-8")
    w.runs.append(([sys.executable, str(w.base / "say.py"), str(said)], f"run-{w.n}"))


def _run_private_agent(w: World, text: str, m: str) -> None:
    """review-fix R3a: ``status run --agent TEXT`` started in a private folder. Its id starts
    with ``plant-``: the status file the wrapper writes holds the agent as given (an input)."""
    w.private_runs.append(([sys.executable, str(w.base / "say.py"), "-"],
                           f"plant-run-{w.next()}", text))


def _run_program(w: World, text: str, m: str) -> None:
    """The program itself lives under a folder with the marker in its name."""
    folder = w.base / "bin" / text
    folder.mkdir(parents=True)
    if sys.platform == "win32":
        prog = folder / "deploy.bat"
        prog.write_text("@exit /b 3\r\n", encoding="utf-8")
    else:
        prog = folder / "deploy.sh"
        prog.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
        prog.chmod(0o755)
    w.runs.append(([str(prog), "--dry-run"], f"run-{w.next()}"))


def _claude(group: str, how: str) -> Callable[[World, str, str], None]:
    def plant(w: World, text: str, m: str) -> None:
        s = w.claude(group)
        if how == "custom-title":
            s.meta("custom-title", "customTitle", f"Wren: {text}")
        elif how == "ai-title":
            s.meta("ai-title", "aiTitle", f"Wren: {text}")
        elif how == "agent-name":
            s.meta("agent-name", "agentName", text)
        elif how == "prompt":
            s.prompt(f"please look at {text}")
        elif how == "tool":
            s.said([{"type": "tool_use", "id": "t1", "name": "Bash",
                     "input": {"command": f"cat {text}"}, "caller": {}}])
        elif how == "reply":
            s.said([{"type": "text", "text": f"I read {text}"}])
        else:
            raise AssertionError(how)

    return plant


def _claude_folder(w: World, text: str, m: str) -> None:
    w.claude("private", cwd=w.private / f"{text}-kite").meta(
        "custom-title", "customTitle", "Wren: kites")


def _codex(group: str, how: str) -> Callable[[World, str, str], None]:
    def plant(w: World, text: str, m: str) -> None:
        t = w.codex(group)
        if how == "title":
            t["row"]["title"] = f"Otter: {text}"
        elif how == "first":
            t["row"]["first"] = f"please look at {text}"
        elif how == "output":
            t["log"].add("response_item", "function_call_output", output=f"read {text}")
        elif how == "command":
            t["log"].add("event_msg", "exec_approval_request", call_id="c1",
                         command=["cat", text])
        else:
            raise AssertionError(how)

    return plant


def _codex_folder(w: World, text: str, m: str) -> None:
    w.codex("private", cwd=w.private / f"{text}-moss")


def git_folder(w: World, group: str) -> Path:
    """The folder of the live Claude Code session the git cases commit in."""
    where = w.private if group == "private" else w.work
    return w.claude(group, cwd=where / "kite-repo").cwd


def _git(group: str, how: str) -> Callable[[World, str, str], None]:
    """A commit in the reflog of a live Claude Code session's folder."""

    def plant(w: World, text: str, m: str) -> None:
        repo = git_folder(w, group)
        if how == "author":
            w.reflog(repo, f"{text} <lamp{AT}example.com>", "Fix the kite")
        elif how == "email":
            w.reflog(repo, f"Lamp Lighter <{text}>", "Fix the kite")
        else:
            w.reflog(repo, f"Lamp Lighter <lamp{AT}example.com>", text)

    return plant


def _job_line(w: World, text: str, m: str) -> None:
    w.add_line(w.base / "jobs" / "kite" / "run-1.log", f"step: {text}")


def _job_name(w: World, text: str, m: str) -> None:
    log = w.base / "jobs" / "mail" / f"run-{text}"
    w.add_line(log, "Done: 3 kites")


def _vault(where: str) -> Callable[[World, str, str], None]:
    def plant(w: World, text: str, m: str) -> None:
        vault = w.base / "vault"
        if where == "note":
            w.add_line(vault / "kites" / "kite-notes.md", f"A line about {text}")
        elif where == "inbox":
            w.add_line(vault / "inbox" / "ideas.md", f"## {w.next()}. A kite idea\n"
                                                      f"From: Paper Kite\nSee {text}\n")
        elif where == "name":
            w.add_line(vault / "kites" / text, "# A note")
        elif where == "dropped":  # review-fix R2a: the cat's tooltip names them
            name = text if text.endswith(".md") else text + ".md"
            w.add_line(vault / "inbox" / "dropped" / name, "# A note")
        elif where == "inbox-title":  # review-fix R2a: an inbox note's title shows
            w.add_line(vault / "inbox" / "ideas.md", f"## {w.next()}. {text}\nFrom: Paper Kite\n")
        else:
            raise AssertionError(where)

    return plant


# --- review-fix R2a: the rooms' shown text --- each planter puts the text in a field the room
# shows; test_room_text_lands_where_it_shows plants plain words the same way and finds them
def _job_result(w: World, text: str, m: str) -> None:
    n = w.next()
    folder = w.base / "jobs" / f"result-{n}"
    w.add_line(folder / "run-1.log", f"Done: {text}")
    w.source("jobs", f"kind = \"log_glob\"\n  name = \"Result {n}\"\n"
                     f"  glob = '{folder.as_posix()}/*.log'\n  done = 'Done:\\s*(.+)$'")


def _ledger_tool(w: World, text: str, m: str) -> None:
    doc = {"tool": text, "verdict": "safe", "risk": 8, "flags": 0,
           "scanned_at": z(w.now - dt.timedelta(days=2)), "scanner": "tidewatch-scan"}
    path = w.base / "gate" / "scans" / f"named-{w.next()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _skill_description(w: World, text: str, m: str) -> None:
    n = w.next()
    w.add_line(w.base / "shelf" / "skills" / f"lark-{n}" / "SKILL.md",
               f"---\nname: lark-{n}\ndescription: {text}\n---")


def _contest_title(w: World, text: str, m: str) -> None:
    doc = {"contest": "lamptown-contest/1", "title": text, "date": f"{w.now:%Y-%m-%d}",
           "updated": z(w.now - dt.timedelta(minutes=50)),
           "entries": [{"code": "F01", "name": "Gull Runner"}]}
    path = w.base / "contests" / f"titled-{w.next()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
# --- end review-fix R2a ---


def _project(where: str) -> Callable[[World, str, str], None]:
    def plant(w: World, text: str, m: str) -> None:
        if where.startswith("private"):
            kite = w.base / "projects" / ("secret-kite" if where == "private" else "hidden")
        elif where == "tasks":
            kite = w.base / "projects" / "paper-kite"
        else:  # the room shows only the newest commit: a project of its own for each case
            n = w.next()
            kite = w.base / "projects" / f"kite-{n}"
            w.source("docks", f"kind = \"projects\"\n  projects = [{{ name = \"Kite {n}\", "
                              f"path = '{kite.as_posix()}' }}]")
        if where in ("tasks", "private", "private-dir"):
            w.add_line(kite / "TASKS.md", f"- [ ] look at {text}")
        elif where == "author":
            w.reflog(kite, f"Lamp Lighter <{text}>", "Fix the kite")
        elif where == "subject":
            w.reflog(kite, f"Lamp Lighter <lamp{AT}example.com>", text)
        else:
            raise AssertionError(where)
        if where.startswith("private"):
            w.reflog(kite, f"Lamp Lighter <lamp{AT}example.com>", text)

    return plant


def _skill_body(w: World, text: str, m: str) -> None:
    w.add_line(w.base / "shelf" / "skills" / "kite-knots" / "SKILL.md",
               f"Read {text} first.")


def _shelf_parent(w: World, text: str, m: str) -> None:
    skills = w.base / f"shelf-{text}" / "skills"
    w.add_line(skills / "rope-ladder" / "SKILL.md",
               "---\nname: rope-ladder\ndescription: Climb the rope ladder.\n---")
    w.source("skills", f"kind = \"skill_dirs\"\n  dirs = ['{skills.as_posix()}']")


def _scan_extra(w: World, text: str, m: str) -> None:
    n = w.next()
    doc = {"tool": f"tool-{n}", "verdict": "safe", "risk": 8, "flags": 0,
           "scanned_at": z(w.now - dt.timedelta(days=2)), "scanner": "tidewatch-scan",
           "x-where": text, "path": text}
    path = w.base / "gate" / "scans" / f"tool-{n}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _contest_extra(w: World, text: str, m: str) -> None:
    doc = {"contest": "lamptown-contest/1", "title": "Names for the ferry",
           "date": f"{w.now:%Y-%m-%d}", "updated": z(w.now - dt.timedelta(minutes=50)),
           "_note": text,
           "entries": [{"code": "F01", "name": "Gull Runner", "where": text},
                       {"code": "F02", "name": "Slow Tide"}]}
    path = w.base / "contests" / f"contest-{w.next()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _config(kind: str) -> Callable[[World, str, str], None]:
    """A config value naming a folder that isn't there."""

    def plant(w: World, text: str, m: str) -> None:
        if kind == "private_dirs":
            w.live_private.append(text)
            return
        if kind == "place":
            w.places.append(f"[[live.place]]\ndir = '{text}'\nplot = \"library\"\n")
            return
        if kind == "group":
            w.places.append(f"[[live.group]]\ndir = '{text}'\nname = \"Kites {w.next()}\"\n")
            return
        source = {"log_glob": f"kind = \"log_glob\"\n  name = \"Ghost {w.next()}\"\n"
                              f"  glob = '{text}/*.log'",
                  "vault": f"kind = \"markdown_vault\"\n  path = '{text}'",
                  "skills": f"kind = \"skill_dirs\"\n  dirs = ['{text}']",
                  "project": f"kind = \"projects\"\n  projects = [{{ name = \"Ghost {w.next()}\","
                             f" path = '{text}' }}]",
                  "status_dir": f"kind = \"status_dir\"\n  dir = '{text}'",
                  "scans": f"kind = \"scan_glob\"\n  glob = '{text}/*.json'",
                  "contest": f"kind = \"contest_json\"\n  glob = '{text}/*.json'"}[kind]
        building = {"log_glob": "jobs", "vault": "notes", "skills": "skills",
                    "project": "docks", "status_dir": "docks", "scans": "gate",
                    "contest": "contest"}[kind]
        w.source(building, source)

    return plant


# input -> (the forms it must lose, its planter). One line per input.
INPUTS: dict[str, tuple[tuple[str, ...], Callable[[World, str, str], None]]] = {
    "status.title": (SAYS, _status(False, title="Heron: {}")),
    "status.message": (SAYS, _status(False, message="Stuck on {}")),
    "status.need": (SAYS, _status(False, needs_you=[{"text": "Check {}"}])),
    "status.link-label": (SAYS, _status(False, links=[{"label": "See {}",
                                                       "href": "https://example.com/kite"}])),
    "status.agent": (SAYS, _status(False, agent="{}")),
    "status.private-title": (ALL, _status(True, title="Heron: {}")),
    "status.private-message": (ALL, _status(True, message="Stuck on {}")),
    "status.private-need": (ALL, _status(True, needs_you=[{"text": "Check {}"}])),
    "status.private-link-label": (ALL, _status(True, links=[
        {"label": "See {}", "href": "https://example.com/kite"}])),
    "status.private-link-href": (("segment",), _status(True, links=[
        {"label": "Notes", "href": "https://example.com/{}/notes"}])),
    "status.private-file-link": (("segment",), _status(True, links=[
        {"label": "Notes", "href": FILE_LINK}], needs_you=[{"text": "Look", "href": FILE_LINK}])),
    "status.private-project": (("words", "segment"), _status(True, **{"x-project": "{}"})),
    "status.private-folder": (("segment",), _status_folder),
    "run.argv": (ALL, _run_argv),
    "run.stderr": (ALL, _run_stderr),
    "run.program": (("segment",), _run_program),
    "claude.prompt": (ALL, _claude("public", "prompt")),
    "claude.tool-input": (ALL, _claude("public", "tool")),
    "claude.reply": (ALL, _claude("public", "reply")),
    "claude.title": (SAYS, _claude("public", "custom-title")),
    "claude.private-title": (ALL, _claude("private", "custom-title")),
    "claude.private-ai-title": (ALL, _claude("private", "ai-title")),
    "claude.private-agent-name": (ALL, _claude("private", "agent-name")),
    "claude.private-prompt": (ALL, _claude("private", "prompt")),
    "claude.private-folder": (("segment",), _claude_folder),
    "codex.title": (SAYS, _codex("public", "title")),
    "codex.first-message": (ALL, _codex("public", "first")),
    "codex.output": (ALL, _codex("public", "output")),
    "codex.approval-command": (ALL, _codex("public", "command")),
    "codex.private-title": (ALL, _codex("private", "title")),
    "codex.private-first-message": (ALL, _codex("private", "first")),
    "codex.private-folder": (("segment",), _codex_folder),
    "git.author-name": (("words",), _git("public", "author")),
    "git.author-email": (("email",), _git("public", "email")),
    "git.subject": (ALL, _git("public", "subject")),
    "git.private-subject": (ALL, _git("private", "subject")),
    "jobs.log-line": (ALL, _job_line),
    "jobs.log-file-name": (("email-file",), _job_name),
    "vault.note-body": (ALL, _vault("note")),
    "vault.inbox-item-body": (ALL, _vault("inbox")),
    "vault.note-file-name": (("email-file",), _vault("name")),
    "harbour.tasks": (ALL, _project("tasks")),
    "harbour.head-author": (("email",), _project("author")),
    "harbour.head-subject": (SAYS, _project("subject")),  # the newest commit's words show
    "harbour.private-project": (ALL, _project("private")),
    "harbour.private-dirs-project": (ALL, _project("private-dir")),
    "shelf.skill-body": (ALL, _skill_body),
    "shelf.parent-folder": (("segment",), _shelf_parent),
    "ledger.scan-extra": (ALL, _scan_extra),
    "contest.extra": (ALL, _contest_extra),
    "config.private-dirs": (DIRS, _config("private_dirs")),
    "config.live-place": (DIRS, _config("place")),
    "config.live-group": (DIRS, _config("group")),
    "config.log-glob": (DIRS, _config("log_glob")),
    "config.vault-path": (DIRS, _config("vault")),
    "config.skill-dirs": (DIRS, _config("skills")),
    "config.project-path": (DIRS, _config("project")),
    "config.status-dir": (DIRS, _config("status_dir")),
    "config.scan-glob": (DIRS, _config("scans")),
    "config.contest-glob": (DIRS, _config("contest")),
    # review-fix R2a
    "vault.dropped-file-name": (FILE_SAYS, _vault("dropped")),
    "vault.inbox-title": (ROOM_SAYS, _vault("inbox-title")),
    "jobs.result": (ROOM_SAYS, _job_result),
    "ledger.tool-name": (ROOM_SAYS, _ledger_tool),
    "shelf.description": (ROOM_SAYS, _skill_description),
    "contest.title": (ROOM_SAYS, _contest_title),
    "status.project": ((*PATHS, *ENCODED, *HOMES), _status(False, **{"x-project": "{}"})),
    # review-fix R3a: a public record whose folder is a home in a form home_or_root learned,
    # and the free-text agent of a private record (a status file, and status run --agent)
    "status.folder": (HOMES, _public_folder),
    "status.private-agent": (("words", "segment"), _status(True, agent="{}")),
    "status.private-agent-run": (("words", "segment"), _run_private_agent),
}
# review-fix R2a: inputs whose text a room shows as it is: plain words planted there must show
SHOWN = ("vault.dropped-file-name", "vault.inbox-title", "jobs.result", "ledger.tool-name",
         "shelf.description", "contest.title")
CASES = [f"{name}/{form}" for name, (forms, _) in INPUTS.items() for form in forms]

# Leaks this hunter found that are not fixed yet: case prefix -> where it shows. Each is a strict
# xfail, so the fix turns it red (XPASS) until its line here goes. Never add a line to make a
# new leak pass: fix it.
OPEN_LEAKS: dict[str, str] = {}


def _open_leak(case: str) -> str | None:
    return next((why for prefix, why in OPEN_LEAKS.items() if case.startswith(prefix)), None)


PARAMS = [pytest.param(c, marks=pytest.mark.xfail(strict=True, reason=_open_leak(c) or ""))
          if _open_leak(c) else c for c in CASES]


SAY_PY = """import sys
if sys.argv[1] != "-":
    sys.stderr.write(open(sys.argv[1], encoding="utf-8").read())
sys.exit(3)
"""

CONFIG = """version = 1
[town]
title = "Kite field"

[[building]]
id = "docks"
type = "harbour"
label = "Docks"
  [[building.source]]
  kind = "status_dir"
  dir = '{status}'
  [[building.source]]
  kind = "projects"
  private_dirs = ['{base}/projects/hidden']
  projects = [
    {{ name = "Paper Kite", path = '{base}/projects/paper-kite' }},
    {{ name = "Secret Kite", path = '{base}/projects/secret-kite', private = true }},
    {{ name = "Hidden Kite", path = '{base}/projects/hidden' }},
  ]
{docks}
[[building]]
id = "jobs"
type = "jobs"
label = "Night jobs"
  [[building.source]]
  kind = "status_dir"
  dir = '{status}'
  kinds = ["job", "task"]
  [[building.source]]
  kind = "log_glob"
  name = "Kite backup"
  glob = '{base}/jobs/kite/*.log'
  done = 'Done:\\s*(.+)$'
  [[building.source]]
  kind = "log_glob"
  name = "Mail backup"
  glob = '{base}/jobs/mail/*'
  done = 'Done:\\s*(.+)$'
{jobs}
[[building]]
id = "notes"
type = "vault"
label = "Notes"
  [[building.source]]
  kind = "markdown_vault"
  path = '{base}/vault'
  inbox = "inbox/*.md"
  dropped = "inbox/dropped"
{notes}
[[building]]
id = "gate"
type = "ledger"
label = "Tool gate"
  [[building.source]]
  kind = "scan_glob"
  glob = '{base}/gate/scans/*.json'
{gate}
[[building]]
id = "contest"
type = "contest"
label = "Idea contest"
  [[building.source]]
  kind = "contest_json"
  glob = '{base}/contests/*.json'
{contest}
[[building]]
id = "skills"
type = "shelf"
label = "Skill shelf"
  [[building.source]]
  kind = "skill_dirs"
  dirs = ['{base}/shelf/skills']
{skills}
[live]
status_dir = '{status}'
private_dirs = [{private}]

[[live.reader]]
kind = "claude_code"
root = '{base}/claude'

[[live.reader]]
kind = "codex"
root = '{base}/codex'

[[live.reader]]
kind = "git"
status_dir = '{status}'

{places}"""


def watch(cfg: Path, out: Path, ticks: int) -> None:
    rc = cli.main(["watch", "--config", str(cfg), "--out", str(out), "-q", "--every", "0.2",
                   "--ticks", str(ticks)])
    assert rc == 0


def build_world(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    base = tmp / BASE
    w = World(base, now)
    (base / "say.py").write_text(SAY_PY, encoding="utf-8")
    w.add_line(base / "jobs" / "kite" / "run-1.log", "start")
    w.add_line(base / "shelf" / "skills" / "kite-knots" / "SKILL.md",
               "---\nname: kite-knots\ndescription: Tie the six knots a paper kite needs.\n---")
    w.add_line(base / "vault" / "inbox" / "ideas.md", "# Inbox\n")
    git_folder(w, "public")  # the folders the git cases commit in
    git_folder(w, "private")
    marks: dict[str, str] = {}
    for case in CASES:
        name, form = case.split("/")
        m = marks[case] = marker(case)
        w.form = form
        INPUTS[name][1](w, FORMS[form](m), m)
    shown: dict[str, str] = {}  # review-fix R2a: plain words where a room shows its text
    for name in SHOWN:
        m = shown[name] = marker("shown/" + name)
        INPUTS[name][1](w, "Lark " + m, m)
    w.add_line(base / "jobs" / "kite" / "run-1.log", "Done: 3 kites")
    w.finish()
    (w.work / "runs").mkdir()
    monkeypatch.chdir(w.work / "runs")  # a run's folder is where it starts: not this repo
    for command, status_id in w.runs:
        run_command(command, status_id, directory=w.status, kind="job", heartbeat=0)
    (w.private / "runs").mkdir()
    monkeypatch.chdir(w.private / "runs")  # review-fix R3a: these runs start in a private folder
    for command, status_id, agent in w.private_runs:
        run_command(command, status_id, directory=w.status, kind="job", heartbeat=0, agent=agent)
    start = (now - dt.timedelta(minutes=10, seconds=1)).timestamp()
    monkeypatch.setattr(cc, "process_info",
                        lambda pid: (True, start) if pid in w.pids else (False, None))
    cfg = base / "lamptown.toml"
    private = ", ".join(f"'{p}'" for p in [w.private.as_posix(), *w.live_private])
    more = {b: "".join(w.sources.get(b, ()))
            for b in ("docks", "jobs", "notes", "gate", "contest", "skills")}
    cfg.write_text(CONFIG.format(base=base.as_posix(), status=w.status.as_posix(),
                                 private=private, places="\n".join(w.places), **more),
                   encoding="utf-8")
    out = base / "out"
    watch(cfg, out, 2)
    watch(cfg, out, 1)  # a run starts with a build: the book catches up with the first run
    return SimpleNamespace(base=base, out=out, status=w.status, marks=marks, world=w,
                           shown=shown)


@pytest.fixture(scope="module")
def town(tmp_path_factory) -> SimpleNamespace:
    tmp = tmp_path_factory.mktemp("hunt")
    with pytest.MonkeyPatch.context() as monkeypatch:
        guard(monkeypatch, tmp)  # the readers may touch nothing real
        monkeypatch.setattr(livefeed, "_sleep", lambda s: None)
        return build_world(tmp, monkeypatch)


def written(town: SimpleNamespace) -> dict[str, str]:
    """Every file the town and the Watcher wrote, as lower-case text, by relative name. The
    status files written by hand (``plant-*``) are inputs; the others are the Watcher's and
    the wrapper's, without ``x-folder`` and ``x-project`` (the session folder and its name, kept
    there by design and never shown for a private one).

    In the pages, a ``file:`` link to a file of the world is taken out first: a room links to
    its own files by design (a job's log, a note folder, a skill's manifest), with their full
    path; :func:`test_full_paths_show_only_as_file_links` keeps every full path there. A link
    to anywhere else stays in, so a private ``file:`` link that slips through is still found."""
    links = re.compile(re.escape(town.base.as_uri().lower()) + r"[^\"'<>\s]*")
    files: dict[str, str] = {}
    for path in sorted(town.out.rglob("*")):
        if path.is_file():
            files[path.relative_to(town.base).as_posix()] = links.sub("file:link", path.read_text(
                "utf-8", errors="replace").lower())
    for path in sorted(town.status.rglob("*")):
        rel = path.relative_to(town.base).as_posix()
        if not path.is_file() or path.name.startswith("plant-"):
            continue
        text = path.read_text("utf-8", errors="replace")
        if path.parent == town.status and path.suffix == ".json":
            try:
                doc = json.loads(text)
            except ValueError:
                pass
            else:
                doc.pop("x-folder", None)
                doc.pop("x-project", None)
                text = json.dumps(doc, ensure_ascii=False)
        files[rel] = text.lower()
    return files


@pytest.fixture(scope="module")
def outputs(town) -> dict[str, str]:
    files = written(town)
    for name in ("out/town.html", "out/town-details.html", "out/town-data.js",
                 "out/town-live.js", "out/town-history.js", "status/.lamptown/cursors.json"):
        assert name in files, name
    assert any(n.startswith("status/events/") for n in files)
    return files


@pytest.mark.parametrize("case", PARAMS)
def test_no_canary_reaches_what_the_town_writes(town, outputs, case):
    m = town.marks[case]
    reached = [name for name, text in outputs.items() if m in text]
    assert not reached, f"{case} reaches {reached}"


def test_every_case_has_its_own_marker(town):
    assert len(set(town.marks.values())) == len(CASES)
    assert all(_open_leak(c) for c in OPEN_LEAKS)  # no stale line: each still names a case
    assert all(any(c.startswith(p) for c in CASES) for p in OPEN_LEAKS)


def test_the_town_read_every_input(town):
    """The planted inputs were read, so a case that passes is a wall, not a file never opened:
    every status file, run, session and thread is an agent or a row, and every room is there."""
    w = town.world
    live = json.loads(re.sub(r"^lamptownLive\(|\);\s*$", "", (town.out / "town-live.js")
                             .read_text("utf-8")))
    assert len(live["agents"]) < MAX_AGENTS and not live.get("more")  # none cut off unseen
    ids = {a["id"] for a in live["agents"]}
    planted = w.shown | {i for _, i in w.runs}
    assert len(planted) == (len(SAYS) + len(INPUTS["status.folder"][0])  # R3a: its own
                            + len(INPUTS["run.argv"][0]) * 2 + 1)
    assert planted <= ids, sorted(planted - ids)[:5]
    tools = [a["tool"] for a in live["agents"]]
    assert tools.count("claude-code") == len(w.sessions)
    assert tools.count("codex") == len(w.threads)
    assert sum(a["title"] == "Private agent" for a in live["agents"]) >= 2 * len(ALL)
    data = (town.out / "town-data.js").read_text("utf-8")
    for seen in ("3 kites", "kite-knots", "rope-ladder", "kite-notes", "Gull Runner",
                 "Paper Kite", "Kite field", '"tool": "tool-'):
        assert seen in data, seen


@pytest.mark.parametrize("name", SHOWN)
def test_room_text_lands_where_it_shows(town, name):
    """review-fix R2a: the room inputs are fields the town really shows: plain words planted
    the same way reach the data, so their secret cases pass because of the mask."""
    assert town.shown[name] in (town.out / "town-data.js").read_text("utf-8").lower(), name


def test_full_paths_show_only_as_file_links(town, outputs):
    """The world's folder (its name is ``BASE``) is in no page except inside a ``file:`` link
    to one of its files: no room shows a full path as text."""
    shown = [n for n, text in outputs.items() if n.startswith("out/") and BASE in text]
    assert not shown, shown


LATER = """version = 1
[town]
title = "Kite field"

[[building]]
id = "docks"
type = "harbour"
label = "Docks"
  [[building.source]]
  kind = "status_dir"
  dir = '{status}'

[live]
status_dir = '{status}'
private_dirs = [{private}]
"""


def test_a_folder_made_private_later_shows_nothing_from_then(tmp_path, monkeypatch):
    """A folder added to ``private_dirs`` between two runs, its status file unchanged: from the
    next run on, its title and project are in no page, no saved state and no new event (here a
    ``fail``, the lamp going stale while the town was closed)."""
    guard(monkeypatch, tmp_path)
    monkeypatch.setattr(livefeed, "_sleep", lambda s: None)
    m = marker("rules-change/words")
    status, out, cfg = tmp_path / "status", tmp_path / "out", tmp_path / "lamptown.toml"
    folder = tmp_path / "work" / f"{m}-kite"
    folder.mkdir(parents=True)
    update_status("plant-later", status, title=f"Heron: {m} plain words", agent="kitebot",
                  kind="task", state="working", stale_after=1,
                  **{"x-folder": str(folder), "x-project": f"{m}-kite"})
    cfg.write_text(LATER.format(status=status.as_posix(), private=""), encoding="utf-8")
    watch(cfg, out, 1)
    assert m in (out / "town-live.js").read_text("utf-8").lower()  # public at first: shown
    before = {p: len(p.read_text("utf-8").splitlines()) for p in status.glob("events/*.jsonl")}
    end = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=10)
    while dt.datetime.now(dt.UTC) < end and not _stale(status / "plant-later.json"):
        time.sleep(0.05)
    cfg.write_text(LATER.format(status=status.as_posix(), private=f"'{folder.as_posix()}'"),
                   encoding="utf-8")
    watch(cfg, out, 1)
    new = [line for p in status.glob("events/*.jsonl")
           for line in p.read_text("utf-8").splitlines()[before.get(p, 0):]]
    assert any('"fail"' in line for line in new), f"no fail event in {len(new)} new lines"
    reached = [name for name, text in [
        ("town-live.js", (out / "town-live.js").read_text("utf-8")),
        ("town-details.html", (out / "town-details.html").read_text("utf-8")),
        ("cursors.json", (status / ".lamptown" / "cursors.json").read_text("utf-8")),
        ("the new events", "\n".join(new))] if m in text.lower()]
    assert not reached, f"a folder made private later reaches {reached}"


def _stale(path: Path) -> bool:
    doc = json.loads(path.read_text("utf-8"))
    updated = dt.datetime.fromisoformat(doc["updated"].replace("Z", "+00:00"))
    return dt.datetime.now(dt.UTC) > updated + dt.timedelta(seconds=doc["stale_after"] + 1.2)


# ----------------------------------------------------------------------------- the gate
# The privacy gate stops what must never reach the repository. It checks paths and e-mail
# addresses (tokens are gitleaks' job, names and words the private deny-list's).
GATE_TEXT = (*PATHS, "email")
GATE_NAMES = ("email-file",)
IDENTITY: dict[str, Callable[[str], str]] = {  # an e-mail that leaks a login or a PC name
    "address": FORMS["email"],
    "user-at-host": lambda m: m + AT + "QZPC",
    "git-none": lambda m: m + AT + "qzpc.(none)",
    "bare-login": lambda m: m,
}


def _load_gate():
    if "privacy_gate" in sys.modules:  # test_privacy_gate.py loads it under this name too
        return sys.modules["privacy_gate"]
    spec = importlib.util.spec_from_file_location("privacy_gate", GATE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pg():
    return _load_gate()


def _caught(scanner) -> list[str]:
    return [h.check for h in scanner.hits]


@pytest.mark.parametrize("form", GATE_TEXT)
def test_the_gate_catches_every_form_in_text(pg, form):
    s = pg.Scanner(None)
    s.scan_text(f"notes: see {FORMS[form](marker('gate/' + form))} today", "a test file")
    assert _caught(s), form


@pytest.mark.parametrize("form", GATE_NAMES)
def test_the_gate_catches_every_form_in_a_file_name(pg, form):
    s = pg.Scanner(None)
    s.scan_name(f"docs/{FORMS[form](marker('gate-name/' + form))}")
    assert _caught(s), form


@pytest.mark.parametrize("form", IDENTITY)
def test_the_gate_catches_every_identity_email(pg, form):
    value = IDENTITY[form](marker("gate-identity/" + form))
    assert not pg.allowed_identity_email(value)
    s = pg.Scanner(None)
    s.scan_identity(value, "commit 0123456789", "author e-mail", email=True)
    assert _caught(s), form


def test_the_gate_lets_the_placeholder_identity_pass(pg):
    """The other side: the allowed addresses pass, so the cases above are the gate's own call."""
    assert pg.allowed_identity_email("you" + AT + "example.com")
    assert pg.allowed_identity_email("12345+you" + AT + "users.noreply.github.com")


@pytest.fixture(scope="module")
def tag_repo(tmp_path_factory):
    """A one-commit repository with a clean identity, away from the developer's git config."""
    root = tmp_path_factory.mktemp("tags")
    with pytest.MonkeyPatch.context() as mp:
        empty = root / "empty.gitconfig"
        empty.write_text("", encoding="utf-8")
        mp.setenv("GIT_CONFIG_GLOBAL", str(empty))
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                    "GIT_COMMITTER_EMAIL", "GIT_DIR", "GIT_WORK_TREE"):
            mp.delenv(var, raising=False)
        repo = root / "repo"
        repo.mkdir()
        _run_git(repo, "init", "-q", "-b", "main")
        _run_git(repo, "config", "user.name", "Test Writer")
        _run_git(repo, "config", "user.email", "you" + AT + "example.com")
        _run_git(repo, "config", "commit.gpgsign", "false")
        _run_git(repo, "config", "tag.gpgsign", "false")
        (repo / "kite.txt").write_text("a kite\n", encoding="utf-8")
        _run_git(repo, "add", "-A")
        _run_git(repo, "commit", "-q", "-m", "A kite")
        yield SimpleNamespace(root=repo, head=_run_git(repo, "rev-parse", "HEAD"), mp=mp)


def _run_git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True)
    return done.stdout.strip()


def _tag(repo, name: str, message: str, tagger: str | None = None) -> str:
    pre = ["-c", "user.email=" + tagger] if tagger else []
    _run_git(repo.root, *pre, "tag", "-a", name, repo.head, "-m", message)
    return _run_git(repo.root, "rev-parse", name)


@pytest.mark.parametrize("form", GATE_TEXT)
def test_the_gate_catches_every_form_in_a_tag_message(pg, tag_repo, form):
    text = FORMS[form](marker("gate-tag/" + form))
    sha = _tag(tag_repo, f"v0.0.{GATE_TEXT.index(form)}", f"Release notes\n\nsee {text}\n")
    s = pg.Scanner(None)
    pg.scan_tags(s, tag_repo.root, sha)
    assert _caught(s), form


@pytest.mark.parametrize("form", IDENTITY)
def test_the_gate_catches_every_tagger_email(pg, tag_repo, form):
    value = IDENTITY[form](marker("gate-tagger/" + form))
    sha = _tag(tag_repo, f"v1.0.{list(IDENTITY).index(form)}", "A clean message", tagger=value)
    s = pg.Scanner(None)
    pg.scan_tags(s, tag_repo.root, sha)
    assert _caught(s), form
