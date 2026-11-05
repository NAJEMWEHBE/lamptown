"""The whole live town, end to end: invented Claude Code, Codex, git and status folders under
``tmp_path`` -> ``lamptown watch`` (the real readers and Watcher, a few ticks) -> ``town-live.js``
and ``town-history.js`` -> the page in headless Chrome (as in ``test_town.py``).

Nothing here is faked between the folders and the page: the readers read the made-up logs, the
Watcher writes the status files and the event log, the builder writes the town, and the page reads
the files the Watcher wrote. Only process liveness (Claude Code's session pid) is stubbed, and the
page stamps each file's ``at`` with its own moment as it arrives (see ``PAGE``). The reader guard
fails the test on any touch of the real ``~/.claude``, ``~/.codex`` or a real git repository.
Every name, path and message is made up; waits in the page are condition-with-cap.

The folders and the Watcher's runs are made once (the ``town`` fixture); the files are checked
without a browser, again on a clock that reads just after local midnight, and then in the page.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import lamptown
from _browser import browser
from _reader_guard import guard
from lamptown import cli, livefeed
from lamptown.status.adapters import make_id
from lamptown.status.readers import claude_code as cc
from lamptown.status.store import slug, update_status
from test_town import run

ROOT = Path(__file__).resolve().parents[1]

SECRET = "please fix the save slots in the kite game"  # a prompt and a reply: must never show
WHO = "Lamp Lighter <lamp@example.com>"                # a commit author: must never show
SUBJECT = "Fix the save slots for real"                # a commit message: must never show
CLAUDE_SID = "3f9a2b1c-1d2e-4f5a-8b9c-0d1e2f3a4b5c"
HELPER = "a1b2c3d4e5f6a7b8"
PID = 4242
WREN = make_id("claude-code", CLAUDE_SID)


def z(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def jsonl(path: Path, *docs: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.writelines(json.dumps(d, separators=(",", ":")) + "\n" for d in docs)


# ----------------------------------------------------------------------------- the folders
class Claude:
    """A made-up ``~/.claude`` with one live session (and one helper) working in ``cwd``."""

    def __init__(self, root: Path, cwd: Path, now: dt.datetime) -> None:
        self.root, self.cwd, self.now = root, str(cwd), now
        start = now - dt.timedelta(minutes=10)
        session = {"pid": PID, "sessionId": CLAUDE_SID, "cwd": self.cwd,
                   "startedAt": int(start.timestamp() * 1000), "procStart": "1",
                   "version": "9.9.9", "kind": "interactive", "entrypoint": "cli", "name": "",
                   "nameSource": "auto", "status": "busy",
                   "updatedAt": int(now.timestamp() * 1000),
                   "statusUpdatedAt": int(now.timestamp() * 1000)}
        (root / "sessions").mkdir(parents=True)
        (root / "sessions" / f"{PID}.json").write_text(json.dumps(session), encoding="utf-8")
        self.project = root / "projects" / "".join(c if c.isalnum() else "-" for c in self.cwd)
        self.started = start

    def line(self, kind: str, seconds_ago: float, **more) -> dict:
        doc = {"parentUuid": None, "isSidechain": False, "type": kind, "cwd": self.cwd,
               "sessionId": CLAUDE_SID,
               "timestamp": z(self.now - dt.timedelta(seconds=seconds_ago)), **more}
        return doc

    def work(self) -> None:
        bash = {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "make"},
                "caller": {}}
        jsonl(self.project / f"{CLAUDE_SID}.jsonl",
              {"type": "custom-title", "customTitle": "Wren: save slots", "sessionId": CLAUDE_SID},
              self.line("user", 600, promptId="p", permissionMode="bypassPermissions",
                        message={"role": "user", "content": SECRET}),
              self.line("assistant", 5, message={
                  "model": "m", "id": "msg", "type": "message", "role": "assistant",
                  "content": [bash], "stop_reason": "tool_use"}))
        sub = self.project / CLAUDE_SID / "subagents"
        grep = {"type": "tool_use", "id": "g1", "name": "Grep", "input": {"pattern": "slot"},
                "caller": {}}
        jsonl(sub / f"agent-{HELPER}.jsonl",
              {**self.line("assistant", 3, message={
                  "model": "m", "id": "msg2", "type": "message", "role": "assistant",
                  "content": [grep], "stop_reason": "tool_use"}),
               "agentId": HELPER, "isSidechain": True})
        meta = {"agentType": "general-purpose", "isBuiltIn": True, "description": "Knot check",
                "toolUseId": "t", "spawnDepth": 1, "finished": False}
        (sub / f"agent-{HELPER}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


def codex_sid(n: int, now: dt.datetime) -> str:
    """A UUIDv7-shaped thread id (a millisecond clock first, random-looking bits last)."""
    clock = f"{int(now.timestamp() * 1000) + n:012x}"
    rand = hashlib.sha256(f"session {n}".encode()).hexdigest()
    return f"{clock[:8]}-{clock[8:]}-7{rand[:3]}-8{rand[3:6]}-{rand[6:18]}"


class Codex:
    """One made-up Codex rollout file under ``<root>/sessions/YYYY/MM/DD``."""

    def __init__(self, root: Path, n: int, cwd: Path, start: dt.datetime) -> None:
        self.sid = codex_sid(n, start)
        day = start.astimezone().date()
        self.path = (root / "sessions" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
                     / f"rollout-{start:%Y-%m-%dT%H-%M-%S}-{self.sid}.jsonl")
        self.t = start
        jsonl(self.path, {"timestamp": z(start), "type": "session_meta", "payload": {
            "id": self.sid, "session_id": self.sid, "cwd": str(cwd), "timestamp": z(start),
            "source": "vscode", "originator": "codex_vscode", "cli_version": "0.0.0",
            "base_instructions": {"text": "Be helpful."}}})

    @property
    def id(self) -> str:  # the id hooks and reader share
        return "codex-" + self.sid.replace("-", "")[-12:]

    def add(self, kind: str, ptype: str, **payload) -> None:
        self.t += dt.timedelta(seconds=1)
        jsonl(self.path, {"timestamp": z(self.t), "type": kind,
                          "payload": {"type": ptype, **payload}})

    def work(self) -> None:
        self.add("event_msg", "task_started", turn_id="t", started_at=0)
        self.add("response_item", "function_call_output", output=SECRET)

    def ask(self) -> None:
        self.add("event_msg", "exec_approval_request", call_id="c1", command=["rm", "x"])


class Repo:
    """A made-up git repository: just the reflogs the git reader looks at."""

    def __init__(self, root: Path) -> None:
        self.git = root / ".git"
        (self.git / "logs").mkdir(parents=True)
        (self.git / "HEAD").write_text("ref: refs/heads/main\n")
        self.n = 0

    def _add(self, path: Path, when: dt.datetime, msg: str) -> None:
        self.n += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as fh:
            fh.write(f"{self.n - 1:040x} {self.n:040x} {WHO} {int(when.timestamp())} +0300\t"
                     f"{msg}\n".encode())

    def commit(self, when: dt.datetime) -> None:
        self._add(self.git / "logs" / "HEAD", when, f"commit: {SUBJECT}")

    def push(self, when: dt.datetime) -> None:
        self._add(self.git / "logs" / "refs" / "remotes" / "origin" / "main", when,
                  "update by push")


CONFIG = """version = 1
[town]
title = "Kite field"

[[building]]
id = "docks"
type = "harbour"
label = "Docks"
  [[building.source]]
  kind = "static"
  state = "ok"

[live]
status_dir = "{base}/status"

[[live.reader]]
kind = "claude_code"
root = "{base}/claude"

[[live.reader]]
kind = "codex"
root = "{base}/codex"

[[live.reader]]
kind = "git"
status_dir = "{base}/status"

[[live.place]]
dir = "{base}/work/tidebook"
plot = "library"
"""


def watch(cfg: Path, out: Path, ticks: int) -> None:
    rc = cli.main(["watch", "--config", str(cfg), "--out", str(out), "-q", "--every", "0.2",
                   "--ticks", str(ticks)])
    assert rc == 0


def payload(path: Path, fn: str) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    assert text.startswith(fn + "(") and text.endswith(");")
    return json.loads(text[len(fn) + 1:-2])


def aged(src: Path, dst: Path, by: dt.timedelta) -> None:
    """``src``'s live payload as a slow runner would hand it to the page: ``by`` older."""
    p = payload(src, "lamptownLive")
    p["at"] = z(dt.datetime.fromisoformat(p["at"].replace("Z", "+00:00")) - by)
    dst.write_text(f"lamptownLive({json.dumps(p)});\n", encoding="utf-8")


PAGE = """
const until = async (f, cap) => {
  for (let w = 0; w < (cap || 4000) && !f(); w += 50) await wait(50);
  return !!f(); };
// The files are the Watcher's, but their `at` ages while the later runs and Chrome's cold start go
// by (the test hands them over five minutes old, as the slowest runner might), and the page calls
// a payload LIVE only while it is under 30 s old by the real clock. So each payload is stamped with
// the page's own moment as it arrives (as the app's push is). Nothing else in them is touched.
const real = lamptownLive;
lamptownLive = p => {
  if (p && typeof p === 'object') p.at = new Date().toISOString();
  return real(p); };
const load = name => new Promise(res => liveScript(name, ok => res(ok)));
const L = () => Object.assign({}, SEEN.lighthouse || {});
const figs = () => (SEEN.agents || []).map(e => ({ id: e.id, state: e.state, plot: e.plot,
  parent: e.parent, helpers: e.helpers || [], tool: e.tool || null }));
const before = await load('live-first.js');
await until(() => (SEEN.agents || []).filter(e => !e.small).length >= 4);
const first = { figs: figs(), light: L(), on: LIVE.on };
const after = await load('live-last.js');
const flashed = await until(() => L().flashing);
const flash = L();
await until(() => !L().flashing && L().phase === 'hold', 6000);
return { before, after, first, flashed, flash, held: L(), figs: figs(), on: LIVE.on,
  corner: SEEN.corner,
  berths: ((SEEN.berths || {}).list || []).map(b => ({ id: b.id, name: b.name, agents: b.agents,
    commits: b.sail })) };
"""


# ----------------------------------------------------------------------------- the town
@pytest.fixture(scope="module")
def town(tmp_path_factory) -> SimpleNamespace:
    """The folders, three runs of the Watcher and the files they wrote; made once a module."""
    tmp_path = tmp_path_factory.mktemp("e2e")
    with pytest.MonkeyPatch.context() as monkeypatch:
        guard(monkeypatch, tmp_path)  # the readers may touch nothing real
        monkeypatch.setattr(livefeed, "_sleep", lambda s: None)  # ticks back to back
        return make_town(tmp_path, monkeypatch)


def make_town(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    base = tmp_path.as_posix()
    work = tmp_path / "work"
    kite, moss, tide = work / "paper-kite", work / "moss-engine", work / "tidebook"
    for folder in (kite, moss, tide):
        folder.mkdir(parents=True)
    monkeypatch.setattr(cc, "process_info",  # Claude Code's pid is alive, started with it
                        lambda pid: (True, (now - dt.timedelta(minutes=10, seconds=1))
                                     .timestamp()) if pid == PID else (False, None))

    claude = Claude(tmp_path / "claude", kite, now)
    claude.work()
    otter = Codex(tmp_path / "codex", 1, moss, now - dt.timedelta(minutes=4))
    otter.work()
    puffin = Codex(tmp_path / "codex", 2, tide, now - dt.timedelta(minutes=3))
    puffin.work()
    repo = Repo(kite)
    heron = {"title": "Heron: gull counter", "agent": "kitebot", "kind": "task",
             "started": z(now - dt.timedelta(minutes=8)),
             "x-folder": str(moss), "x-project": "moss-engine"}  # a hook's status file
    update_status("kitebot-heron", tmp_path / "status", state="working", **heron)
    cfg = tmp_path / "lamptown.toml"
    cfg.write_text(CONFIG.format(base=base), encoding="utf-8")
    out = tmp_path / "out"

    # 1. everyone at work: the Watcher's first file is kept for the page's first look
    watch(cfg, out, 2)
    shutil.copyfile(out / "town-live.js", out / "live-before.js")
    first = payload(out / "town-live.js", "lamptownLive")

    # 2. Otter asks for approval, Wren commits twice and pushes; a fresh run of the Watcher
    otter.ask()
    repo.commit(now - dt.timedelta(minutes=2))
    repo.commit(now - dt.timedelta(minutes=1))
    repo.push(now - dt.timedelta(seconds=50))
    update_status("kitebot-heron", tmp_path / "status", state="error", message="Stopped",
                  **heron)
    watch(cfg, out, 3)
    watch(cfg, out, 1)  # a run starts with a build: the book (town-history.js) catches up
    return SimpleNamespace(tmp=tmp_path, base=base, out=out, first=first,
                           knots=f"{WREN}-{slug(HELPER, 12)}", otter=otter.id, puffin=puffin.id)


# ----------------------------------------------------------------------------- the files
def test_folders_to_watcher_to_files(town):
    want = os.environ.get("LAMPTOWN_E2E_PACKAGE")  # set by the midnight run for its child
    if want:
        got = Path(str(lamptown.__file__)).resolve()
        assert got == Path(want), "the child runs another lamptown"
    out, wren, knots, otter, puffin = town.out, WREN, town.knots, town.otter, town.puffin
    assert town.first["need"] == 0
    p = payload(out / "town-live.js", "lamptownLive")
    by = {a["id"]: a for a in p["agents"]}
    assert set(by) == {wren, knots, otter, puffin, "kitebot-heron"}, sorted(by)
    assert by[wren]["state"] == "working" and by[wren]["title"] == "Wren: save slots"
    assert by[wren]["tool"] == "claude-code" and by[wren]["subagents"] == 1
    assert by[wren]["commits"] == 2 and by[wren]["pushed"] is True
    assert by[knots]["parent"] == wren and by[knots]["state"] == "working"
    assert by[otter]["state"] == "waiting" and p["need"] == 1
    assert by["kitebot-heron"]["state"] == "error"
    plots = {i: a["plot"] for i, a in by.items()}
    assert plots[puffin] == "library"  # [[live.place]]
    assert {plots[wren], plots[otter], plots["kitebot-heron"]} == {"boathouse"}
    names = {a["project"] for a in p["agents"]}
    assert names == {"paper-kite", "moss-engine", "tidebook"}
    # every project is in the payload; the page moors only those with an agent at the harbour
    assert sorted(x["id"] for x in p["projects"]) == ["moss-engine", "paper-kite", "tidebook"]
    assert p["rev"] and f'"rev": "{p["rev"]}"' in (out / "town-data.js").read_text("utf-8")

    # the book. Every fixture moment is minutes before now, so a run just after local midnight
    # keeps some (or all) of an agent's events in yesterday: read the last two days, never
    # only today (test_the_files_hold_up_just_after_local_midnight runs this on such a clock)
    h = payload(out / "town-history.js", "lamptownHistory")
    assert len(h["days"]) == 7 and h["days"][-1]["today"]
    seen, titles = {}, {}
    for day in h["days"][-2:]:
        for _minute, i, kind, *_ in day["events"]:
            seen.setdefault(day["agents"][i]["id"], set()).add(kind)
        titles.update({a["id"]: a["name"] for a in day["agents"]})
    assert {"start", "commit", "push"} <= seen[wren], seen
    assert {"start", "wait"} <= seen[otter]
    assert "start" in seen[puffin] and "fail" in seen["kitebot-heron"]
    assert titles[wren] == "PAPER KITE / WREN: SAVE SLOTS"  # the book's project / title

    # names only: no folder, prompt, reply, author or commit message left the folders
    for name in ("town-live.js", "town-history.js", "town-data.js"):
        text = (out / name).read_text(encoding="utf-8")
        for bit in (SECRET, SUBJECT, "Lamp Lighter", "lamp@example.com", town.base,
                    str(town.tmp), json.dumps(str(town.tmp))[1:-1]):
            assert bit not in text, (name, bit)


def midnight_zone(utc: dt.datetime) -> str:
    """A ``TZ`` value whose wall clock reads 00:00 (and the seconds of ``utc``) at ``utc``.
    POSIX form (hours *west* of UTC; Windows' C runtime reads it too), whole minutes only, as real
    zones are, and the nearer way round (-12 h to +12 h)."""
    east = -(utc.hour * 60 + utc.minute) % 1440  # minutes to add to UTC to land on 00:00
    east = east - 1440 if east > 720 else east
    west = -east
    return f"LMT{'-' if west < 0 else '+'}{abs(west) // 60:02d}:{abs(west) % 60:02d}"


def test_midnight_zone():
    for utc, local in ((dt.datetime(2026, 11, 4, 21, 0, 7, tzinfo=dt.UTC), "LMT-03:00"),
                       (dt.datetime(2026, 11, 4, 0, 0, 7, tzinfo=dt.UTC), "LMT+00:00"),
                       (dt.datetime(2026, 11, 4, 6, 30, 7, tzinfo=dt.UTC), "LMT+06:30"),
                       (dt.datetime(2026, 11, 4, 12, 0, 7, tzinfo=dt.UTC), "LMT-12:00"),
                       (dt.datetime(2026, 11, 4, 12, 1, 7, tzinfo=dt.UTC), "LMT-11:59")):
        assert midnight_zone(utc) == local, utc


def child_env(zone: str, package: str | None = None) -> dict[str, str]:
    """The child Python's environment: ``TZ`` set, and the same lamptown the tests import.
    From a checkout (``src/lamptown``) that is ``src`` on PYTHONPATH; from an installed package
    (CI's ``pip install .``) nothing is added, so the child tests the installed one too. The child
    checks it got the same package (``LAMPTOWN_E2E_PACKAGE``)."""
    pkg = Path(package or str(lamptown.__file__)).resolve()
    env = {**os.environ, "TZ": zone, "LAMPTOWN_E2E_PACKAGE": str(pkg)}
    src = ROOT / "src"
    if pkg.parent.parent == src.resolve():
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(src), os.environ.get("PYTHONPATH")]))
    return env


def test_the_child_runs_the_package_the_tests_import(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "kite-path")
    here = child_env("UTC", str(ROOT / "src" / "lamptown" / "__init__.py"))
    assert here["PYTHONPATH"].split(os.pathsep) == [str(ROOT / "src"), "kite-path"]
    site = tmp_path / "site-packages" / "lamptown" / "__init__.py"
    installed = child_env("UTC", str(site))
    assert installed["PYTHONPATH"] == "kite-path"  # untouched: no src in front
    assert installed["LAMPTOWN_E2E_PACKAGE"] == str(site.resolve()) and installed["TZ"] == "UTC"


def test_the_files_hold_up_just_after_local_midnight(tmp_path):
    """The test above on a computer whose clock reads just after midnight, so every agent's
    first minutes (Wren starts ten minutes before) fall in yesterday's day. A zone is read once,
    as Python starts, so that test runs again in a fresh Python with ``TZ`` set to such a zone."""
    zone = midnight_zone(dt.datetime.now(dt.UTC))
    env = child_env(zone)
    child = tmp_path / "child"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp",
         str(child), f"{Path(__file__).resolve()}::test_folders_to_watcher_to_files"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=900, check=False)
    assert done.returncode == 0, (zone, done.stdout[-6000:], done.stderr[-2000:])
    # it really ran across midnight: the Watcher's log has yesterday (Wren's start) and today
    logs = sorted(child.glob("e2e*/status/events/*.jsonl"))
    days = [dt.date.fromisoformat(f.stem) for f in logs]
    assert len(days) >= 2 and days[-1] - days[-2] == dt.timedelta(days=1), (zone, days)
    early = [json.loads(line) for line in logs[-2].read_text("utf-8").splitlines() if line]
    assert any(e["id"] == WREN and e["k"] == "start" for e in early), (zone, early)


# ----------------------------------------------------------------------------- the page
@browser
def test_folders_to_watcher_to_page(town):
    """The first file, then the Watcher's newest one (a NEW wait)."""
    out, wren, knots, otter, puffin = town.out, WREN, town.knots, town.otter, town.puffin
    late = dt.timedelta(minutes=5)  # far past the page's 30 s: only the page's own stamp helps
    aged(out / "live-before.js", out / "live-first.js", late)
    aged(out / "town-live.js", out / "live-last.js", late)
    r = run(out / "town.html", PAGE, query="?live=0&motion=1")
    assert r["before"] is True and r["after"] is True
    first = {f["id"]: f for f in r["first"]["figs"]}
    assert first[wren]["state"] == "working" and first[otter]["state"] == "working"
    assert r["first"]["light"]["mode"] == "sweep" and r["first"]["on"] is True
    figs = {f["id"]: f for f in r["figs"]}
    assert {wren, otter, puffin, "kitebot-heron"} <= set(figs)
    assert figs[otter]["state"] == "waiting" and figs[puffin]["plot"] == "library"
    assert knots in figs[wren]["helpers"] or knots in figs  # the helper walks with Wren
    assert r["flashed"] is True and r["flash"]["color"] == "wait" and r["flash"]["count"] == 1
    assert r["held"]["mode"] == "wait" and r["held"]["target"] == "boathouse"
    assert r["on"] is True and r["corner"] == "LIVE"
    berths = {b["id"]: b for b in r["berths"]}
    assert set(berths) == {"paper-kite", "moss-engine"}
    assert wren in berths["paper-kite"]["agents"]
    assert otter in berths["moss-engine"]["agents"]
    assert berths["paper-kite"]["commits"] is True  # pushed: the sail is up
