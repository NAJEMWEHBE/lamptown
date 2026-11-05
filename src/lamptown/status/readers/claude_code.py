"""The Claude Code reader: what Claude Code sessions are doing, from Claude Code's own files.

No hooks needed. Built in as ``kind = "claude_code"``; options: ``root`` (default
``~/.claude``), ``on_stop`` (``"ok"``, the default, or ``"waiting"``: like the hook's
``--on-stop``), ``days`` (7: logs not changed for longer are never opened) and ``titles``
(``"session"``, the default: Claude Code's titles, written from the first prompt; ``"folder"``:
the project folder's name only, and a subagent's type).

What it reads (read-only, shared; format checked against Claude Code's files on 2026-11-04):

* ``sessions/<pid>.json``, one per running session: ``sessionId``, ``cwd``, ``status``
  (``busy`` / ``idle``), ``statusUpdatedAt`` and ``startedAt`` (epoch ms), ``procStart`` (on
  Windows the process's creation time as a FILETIME number) and ``name``. The live signal.
* ``projects/<folder>/<sessionId>.jsonl``, the session log: ``custom-title`` / ``ai-title``
  records (titles), ``cwd``, ``assistant`` records (``tool_use`` blocks, ``stop_reason``,
  ``isApiErrorMessage``) and ``user`` records (``tool_result`` blocks, ``permissionMode``).
  ``api-request-*`` records repeat earlier blocks and are skipped.
* ``projects/<folder>/<sessionId>/subagents/agent-<id>.meta.json`` (``description``,
  ``agentType``, ``finished``, ``parentAgentId``) and ``agent-<id>.jsonl`` beside it.

How signals become states (the Watcher turns state changes into events):

==========================================================  ===========================
signal                                                      state (message)
==========================================================  ===========================
live process, ``busy``                                      working ("Working")
live, ``busy``, an ``AskUserQuestion`` / ``ExitPlanMode``    waiting ("Has a question for
tool call without its result                                you")
live, ``busy``, a quick tool (Read, Edit, Write, ...)       waiting ("May need your
without its result for 30 s, permission mode not bypass     permission")
live, ``status`` saying wait / input / permission           waiting ("Needs you")
live, ``idle``                                              ok ("Turn finished"), or
                                                            waiting ("Your turn") with
                                                            ``on_stop = "waiting"``
live or ended, the last reply was an API error              error ("Stopped by an API
(``isApiErrorMessage``) and no prompt came after it         error")
session file left behind, process gone, was ``idle``        ok ("Session ended")
session file left behind, process gone, was ``busy``        working with ``stale_after``
                                                            60 s ("Ended mid-turn"): it
                                                            reads as "Stopped responding"
                                                            a minute after it is seen dead
                                                            and goes grey ("gone quiet")
                                                            after ``quiet_after``
session file removed (a clean exit)                         ok ("Session ended")
no session file, log written in the last 2 min, mid-turn    working (an older Claude
                                                            Code, or ``claude -p``)
no session file otherwise                                   ok ("Turn finished" right
                                                            after an end of turn, else
                                                            "Session ended")
subagent: ``finished``                                      ok ("Finished")
subagent: not finished, its log written in the last 2 min   working
subagent: not finished, the call that launched it            working, ``stale_after`` 2 h
(meta ``toolUseId``) still awaits its result in a live,
busy session
subagent: not finished otherwise (killed, no meta, ...)     ok ("Ended")
==========================================================  ===========================

A live ``busy`` session gets ``stale_after`` = 2 h: the reader checks the process itself, so a
long tool call doesn't turn red after the usual 30 min, and a stopped Watcher still lets the
record age. Permission prompts leave no line in the log, so "May need your permission" is a
guess from a quick tool that has not answered; with hooks installed, the hook's
``PermissionRequest`` says it for sure, and wins: the reader's ``at`` is the *state time*, the
session file's ``statusUpdatedAt`` or the last line that can change the state (a reply, a
prompt, a tool result: ``st``), never a state-free line or a subagent's line. A death the reader
sees itself is a new observation: its ``at`` is when it was first seen (``dd``), and an ``at``
never goes back. A process whose start time can't be read (another user's or a protected
process now holding the pid) counts as alive only while its session file or log moved in the
last 2 h.

Is the process alive? On Windows ``OpenProcess`` + ``GetExitCodeProcess`` (never
``os.kill(pid, 0)``: on Windows that ends the process). On Linux and macOS ``os.kill(pid, 0)``.
A reused pid is caught by the start time: on Windows ``procStart`` must equal the process's
creation time; elsewhere the process must not have started after the session's
``startedAt`` (``/proc`` on Linux, ``sysctl`` on macOS).

Records: the hook adapter's ids (``make_id("claude-code", sessionId)``), so hooks and the
reader share one record; subagents are ``claude-code-<session>-<agent>`` with ``x-parent``.
Title: the log's ``custom-title``, then a session name the user set, then ``ai-title``, then the
session file's automatic name, then the folder name. ``x-folder`` = ``cwd`` (the Watcher masks
``private_dirs``), ``x-project`` = its last part. ``x-files`` = how many different files the
session's ``Write`` / ``Edit`` / ``MultiEdit`` / ``NotebookEdit`` calls changed: the paths are
kept only as salted hashes in the reader's saved state (at most 1000 per session), never
written anywhere. A subagent's files count on the subagent's own record.

Kind: a headless run is a ``job`` (the tower), anything else a ``task``. Headless: the session
file's or the log's ``entrypoint`` starts with ``sdk-`` (``sdk-cli`` is ``claude -p``, ``sdk-ts`` /
``sdk-py`` the Agent SDK); ``cli``, ``claude-desktop``, ``claude-vscode`` and apps that name
themselves (``t3-code``) are tasks. A subagent has its session's kind.

Private folders: a session in one keeps no title in the saved state, not while its log is being
caught up either. A title read before the session's folder is known waits in memory (never
saved) until it is; titles an older run saved for a now private session go at the next poll.

Events: ``start`` at the session's start and ``files`` at the minute the files changed (so the
history shows real times while catching up); everything else comes from the status folder.

Catching up: only logs changed in the last ``days``; a live session's log over 8 MB seen for the
first time starts 384 KB before its end (its state is right at once) and the part before that is
read later as history ("backfill"), with whatever budget is left. Other logs are read from the
start, within the shared budget, over as many polls as it takes; a session without a live
process is held back (no record) until its log is read to the end, so the history gets the
right end time. Lines over 256 KB are never parsed as JSON: a regex picks out ``tool_use``
names, ``file_path`` and ``tool_use_id`` from their first 256 KB, and only when their first
``"message"`` is a top-level key with its ``role`` right after it (copies and progress lines
that nest another agent's reply are skipped). History no one is writing (a log quiet for 2 min
and not live, a finished subagent, the backfill) is *skimmed*: only lines with a tool call, a
title, an API error, an end of turn or a prompt are parsed (a byte check first), the first and
last times come from a regex, and no tool call is kept as pending. A finished subagent log over
64 MB is read by its head and its last 1 MB. :attr:`ClaudeCodeReader.behind` says the last poll
left something unread; the Watcher then reads with a catch-up budget, every second.

Cost: a quick tick ``stat``-s the session files, the logs of live or recently active sessions
and their subagent folders; the whole ``projects`` folder is listed every 30 s.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import secrets
import struct
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lamptown.status.adapters import folder_path, make_id, project_folder
from lamptown.status.events import name_only
from lamptown.status.readers import Batch
from lamptown.status.readers.git import hidden, private_roots  # fix2-PRIV
from lamptown.status.readers.tail import Budget, Line, cursor_key, forget, start_at, tail
from lamptown.status.schema import parse_time
from lamptown.status.store import iso, slug

NAME = "claude-code"
AGENT = "claude-code"
DISPLAY = "Claude Code"
STATE_VERSION = 1

# fix-PRIV: the session titles not kept with titles = "folder" (custom, AI-written, agent, live)
FOLDER_DROPS = ("ct", "ai", "an", "sn", "sa")
BIG = 8 * 1024 * 1024  # a live log bigger than this starts near its end on first sight
BACK = 384 * 1024  # ... this far before its end (one poll's per-file budget reads it)
HEAD_SCAN = 64 * 1024  # bytes read from a log's start to find its cwd, when needed
RESCAN = 30.0  # seconds between full listings of the projects folder
HOT = 15 * 60  # seconds: a log written this recently is checked every tick
ACTIVE = 120  # seconds: no session file, but the log moved this recently: still working
PROMPT_AFTER = 30  # seconds a quick tool may wait for its result before it reads as a prompt
LIVE_STALE = 2 * 3600  # stale_after of a live busy session (the reader checks the process)
DEAD_STALE = 60  # stale_after of a session whose process died mid-turn
START_SLACK = 10.0  # seconds a process may start after the session's startedAt (clocks)
MAX_HASHES = 1000  # different files counted per session
MAX_PENDING = 64  # tool calls without a result kept per session
HEADLESS = "sdk-"  # review-fix: B2 4 (an entrypoint starting so is a headless run: a job)
RACY_NS = 2_000_000_000  # a session file changed this recently is not trusted by (mtime, size)
MAX_CHANGES = 450  # status changes per poll (the Watcher takes 500)
MAX_OUT_EVENTS = 3900  # events per poll (the Watcher takes 5000; one record adds <= 1001)
SESSION_MAX_BYTES = 64 * 1024
PID_MAX = 0xFFFFFFFF if os.name == "nt" else 2**31 - 1  # a DWORD / a pid_t
HUGE_SUB = 64 * 1024 * 1024  # a finished subagent log over this: its head and tail only
HUGE_TAIL = 1024 * 1024  # ... this much of its end

FILE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}
QUESTION_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
QUICK_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "NotebookRead", "Glob",
               "Grep", "LS", "TodoWrite"}
BYPASS_MODES = {"bypassPermissions"}
WAIT_STATUS = re.compile(r"wait|input|permission|approv|question|prompt", re.IGNORECASE)

# Big lines (only their head is kept): no JSON parsing, only these.
_MESSAGE = re.compile(rb'"message":\{')
_JSON_TOKEN = re.compile(rb'"(?:[^"\\]|\\.)*"|[{}\[\]]')
_ROLE = re.compile(rb'"role":"(assistant|user)"')
_TOOL_USE = re.compile(rb'"type":"tool_use","id":"([^"\\]{1,128})","name":"([^"\\]{1,128})"')
_FILE_PATH = re.compile(rb'"(?:file_path|notebook_path)":"((?:[^"\\]|\\.){1,4096})"')
_TOOL_RESULT = re.compile(rb'"tool_use_id":"([^"\\]{1,128})"')
_TIMESTAMP = re.compile(rb'"timestamp":"([0-9T:.+Z-]{10,40})"')
_CWD = re.compile(rb'"cwd":"((?:[^"\\]|\\.){1,2048})"')
_API_ERROR = re.compile(rb'"isApiErrorMessage":true')
_EPOCH_FILETIME = 116444736000000000  # 1970-01-01 as a Windows FILETIME (100 ns steps)


# ---------------------------------------------------------------- is the process alive?
def process_info(pid: int) -> tuple[bool, float | None]:
    """``(alive, start time as epoch seconds or None when unknown)`` for ``pid``."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False, None  # os.kill(0 or -n) would signal a whole process group
    if pid > PID_MAX:
        return False, None  # OpenProcess would cut it to 32 bits (another pid), os.kill raise
    if os.name == "nt":
        return _windows_process(pid)
    try:
        os.kill(pid, 0)  # POSIX only: signal 0 checks, it sends nothing
    except ProcessLookupError:
        return False, None
    except PermissionError:
        pass  # alive, someone else's
    except OSError:
        return False, None
    return True, _posix_start(pid)


def _windows_process(pid: int) -> tuple[bool, float | None]:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    query = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
    handle = k32.OpenProcess(query, False, pid)
    if not handle:
        # 5 = access denied: the process exists but belongs to someone else
        return (ctypes.get_last_error() == 5), None
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True, None
        if code.value != 259:  # STILL_ACTIVE
            return False, None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return True, None
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return True, (created - _EPOCH_FILETIME) / 1e7
    finally:
        k32.CloseHandle(handle)


def _posix_start(pid: int) -> float | None:
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
            ticks = int(stat.rsplit(")", 1)[1].split()[19])  # field 22: starttime
            for line in Path("/proc/stat").read_text(encoding="ascii").splitlines():
                if line.startswith("btime "):
                    return int(line.split()[1]) + ticks / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, IndexError):
            return None
        return None
    if sys.platform == "darwin":
        return _darwin_start(pid)
    return None


def _darwin_start(pid: int) -> float | None:
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 4)(1, 14, 1, pid)  # CTL_KERN, KERN_PROC, KERN_PROC_PID
        buf = ctypes.create_string_buffer(1024)
        size = ctypes.c_size_t(len(buf))
        if libc.sysctl(mib, 4, buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return None
        if size.value < 16:
            return None
        sec, usec = struct.unpack_from("<qi", buf.raw, 0)  # kp_proc.p_starttime (timeval)
    except (OSError, AttributeError, struct.error, ValueError):
        return None
    start = sec + usec / 1e6
    return start if 0 < start <= time.time() + 60 else None


def same_process(start: float | None, started_ms: Any, proc_start: Any, *,
                 nt: bool | None = None) -> bool:
    """Is the process that holds the pid now (started at ``start``) the session's own?
    ``nt``: read ``procStart`` as a Windows FILETIME (default: on Windows)."""
    if start is None:
        return True  # can't tell: trust the alive check
    started = started_ms / 1000 if _number(started_ms) else None
    nt = os.name == "nt" if nt is None else nt
    if isinstance(proc_start, str) and proc_start.isdigit() and nt:
        stamp = (int(proc_start) - _EPOCH_FILETIME) / 1e7
        if started is None or abs(stamp - started) < 86400:  # a plausible creation time
            return abs(stamp - start) <= 2.0
    if started is None:
        return True
    return start <= started + START_SLACK  # a reused pid belongs to a newer process


def _number(value: Any) -> bool:
    return (isinstance(value, int | float) and not isinstance(value, bool) and value > 0
            and math.isfinite(value))


def _pid(value: Any) -> int | None:
    """A session file's ``pid``: a whole number from 1 to :data:`PID_MAX`, else ``None``."""
    if isinstance(value, float) and _number(value) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= PID_MAX:
        return value
    return None


# ---------------------------------------------------------------- helpers
def _ms(value: Any) -> datetime | None:
    if not _number(value):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, UTC).replace(microsecond=0)
    except (OverflowError, OSError, ValueError):
        return None


def _ts(value: Any) -> str | None:
    moment = parse_time(value)
    return iso(moment.astimezone(UTC)) if moment else None


def _later(a: str | None, b: str | None) -> str | None:
    return b if a is None or (b is not None and b > a) else a


def _earlier(a: str | None, b: str | None) -> str | None:
    return b if a is None or (b is not None and b < a) else a


def _json_string(raw: bytes) -> str | None:
    try:
        value = json.loads(b'"' + raw + b'"')
    except ValueError:
        return None
    return value if isinstance(value, str) else None


def _text(value: Any, limit: int = 200) -> str:
    """A name for the saved state: one line, any path cut to its last part."""
    return name_only(value, limit)


def sub_id(session: str, agent: str) -> str:
    """A subagent's status id: ``claude-code-<session>-<agent>`` (12 id-safe characters each)."""
    return f"{make_id(AGENT, session)}-{slug(agent, 12) or 'agent'}"


@dataclass
class Live:
    """One session file, read."""

    sid: str
    status: str
    since: str | None  # statusUpdatedAt
    started: str | None
    cwd: str
    name: str
    auto_name: bool
    alive: bool
    sure: bool = True  # the process's start time was known (so a reused pid would show)
    entry: str = ""  # review-fix: B2 4 (the session file's "entrypoint")


# ---------------------------------------------------------------- the reader
class ClaudeCodeReader:
    name = NAME

    def __init__(self, root: str | os.PathLike | None = None, *, on_stop: str = "ok",
                 days: int = 7, titles: str = "session", private_dirs: Any = None,
                 config_dir: Any = None) -> None:
        # --- fix2-PRIV: a session in a private folder keeps no title in the saved state (the
        # Watcher adds its own private folders here too) ---
        self.private = private_roots(private_dirs, config_dir)
        # --- end fix2-PRIV ---
        self.root =Path(os.path.expanduser(os.fspath(root))) if root else Path.home() / ".claude"
        self.on_stop = on_stop if on_stop in ("ok", "waiting") else "ok"
        self.window = timedelta(days=max(1, int(days)))
        self.titles = titles if titles in ("session", "folder") else "session"
        self.rescan = RESCAN  # seconds between full listings of the projects folder
        self.skim = True  # history logs: parse only the lines that can matter (tests compare)
        self.behind = False  # the last poll left something unread (the Watcher speeds up)
        self._sub_lag: set[str] = set()  # sessions with subagent logs not yet read to the end
        # memory only (rebuilt after a restart; never saved: these hold paths)
        self._logs: dict[str, Path] = {}  # session id -> log
        self._mtime: dict[str, float] = {}  # session id -> log mtime at the last listing
        self._subs: dict[str, dict[str, dict[str, Path]]] = {}  # sid -> agent -> log/meta
        self._scanned: float | None = None
        self._cwd: dict[str, str] = {}
        self._no_cwd: set[str] = set()
        self._sessions: dict[str, tuple[tuple[int, int] | None, dict]] = {}
        self._metas: dict[str, tuple[tuple[int, int], dict]] = {}
        self._sig: dict[str, tuple[int, int]] = {}  # subagent file -> (mtime_ns, size) listed
        self._done: dict[str, tuple] = {}  # subagent log -> (mtime_ns, size) when read to end
        self._touched: set[str] = set()  # subagents whose files changed this poll
        self.read_bytes = 0  # bytes read by the last poll (for tests and measurements)
        # --- review-fix: B2 2 --- with private folders: titles read before their session's
        # folder is known wait here (memory only), record id -> (session, field -> (text, read
        # as history)); the session files' folders of this poll
        self._unsure: dict[str, tuple[str, dict[str, tuple[str, bool]]]] = {}
        self._live_cwd: dict[str, str] = {}
        # --- end review-fix: B2 2 ---

    # ------------------------------------------------------------ poll
    def poll(self, state: dict, now: datetime, budget: Budget | None = None) -> Batch:
        now = (now if now.tzinfo else now.astimezone()).astimezone(UTC).replace(microsecond=0)
        budget = budget if budget is not None else Budget()
        left = budget.left
        if state.get("v") != STATE_VERSION:
            state.clear()
            state["v"] = STATE_VERSION
        salt = state.setdefault("salt", secrets.token_hex(8))
        cursors = state.setdefault("files", {})
        back = state.setdefault("back", {})
        mains: dict = state.setdefault("s", {})
        subs: dict = state.setdefault("a", {})

        self._touched = set()
        lives = self._read_sessions()
        self._live_cwd = {sid: live.cwd for sid, live in lives.items() if live.cwd}  # B2 2
        full = self._scan_due(lives)
        if full:
            self._scan(now)
        stamp = now.timestamp()
        for sid, live in lives.items():
            if not self._find_log(sid, live) and live.alive and sid not in mains:
                mains[sid] = {"id": make_id(AGENT, sid), "c": 0}  # no log yet: a new session
            if live.alive and not live.sure:
                # the start time is unknown (another user's or a protected process holds the
                # pid now?): alive only while the session file or its log still moves
                since = parse_time(live.since) if live.since else None
                moved = max(since.timestamp() if since else 0.0, self._mtime.get(sid, 0.0))
                if stamp - moved >= LIVE_STALE:
                    live.alive = False

        # read: live sessions first, then recent or unfinished logs, newest first; then backfill
        order = sorted(self._logs, key=lambda s: (s not in lives or not lives[s].alive,
                                                  -self._mtime.get(s, 0.0)))
        cutoff = stamp - self.window.total_seconds()
        hot_after = stamp - HOT
        quiet = stamp - ACTIVE  # a log not written since then is history: skim it
        watched: list[str] = []
        sub_lag: set[str] = set()
        for sid in order:
            ses = mains.get(sid)
            mtime = self._mtime.get(sid, 0.0)
            if mtime < cutoff:
                continue  # too old to watch (a session file left behind long ago, say)
            watched.append(sid)
            if not (full or sid in lives or mtime >= hot_after or ses is None or ses.get("c")
                    or sid in self._sub_lag):
                continue
            if ses is None:
                ses = mains[sid] = {"id": make_id(AGENT, sid), "c": 1}
            live = lives.get(sid)
            alive = bool(live and live.alive)
            if budget.allow():
                self._read_main(sid, ses, live, cursors=cursors, back=back, budget=budget,
                                salt=salt, skim=self.skim and not alive and mtime < quiet)
            if self._read_subs(sid, ses, subs, cursors=cursors, budget=budget, salt=salt,
                               refresh=full or sid in lives or mtime >= hot_after,
                               busy=alive and live is not None and live.status != "idle",
                               quiet=quiet):
                sub_lag.add(sid)
        for sid in order:  # history before each live cursor, with what is left
            ses = mains.get(sid)
            if ses and ses.get("bf") is not None and budget.allow():
                self._backfill(sid, ses, back, budget, salt)
        self._settle_titles(mains, subs)  # review-fix: B2 2

        batch = Batch()
        cut = False
        for sid, ses in list(mains.items()):
            if len(batch.agents) >= MAX_CHANGES or len(batch.events) >= MAX_OUT_EVENTS:
                cut = True
                break
            self._emit_main(sid, ses, lives.get(sid), now=now, batch=batch, budget=budget)
        for key, sub in list(subs.items()):
            if len(batch.agents) >= MAX_CHANGES or len(batch.events) >= MAX_OUT_EVENTS:
                cut = True
                break
            if (sub.get("e") and sub.get("fin") and not sub.get("pf")
                    and key not in self._touched):
                continue  # finished, sent, and nothing new: nothing can have changed
            sid = key.split("/", 1)[0]
            self._emit_sub(sid, sub, mains.get(sid), lives.get(sid), subs, now=now,
                           batch=batch, budget=budget)

        if full:
            self._prune(mains, subs, cursors, back, lives)
        self._settle_titles(mains, subs)  # review-fix: B2 2 (emitting may have found folders)
        self.read_bytes = left - budget.left
        # --- L4-fix: catch-up --- behind: the Watcher ticks faster (and reads more) until not
        self._sub_lag = sub_lag
        self.behind = cut or bool(sub_lag) or any(
            mains[s].get("c") or mains[s].get("bf") is not None for s in watched if s in mains)
        return batch

    # ------------------------------------------------------------ session files
    def _read_sessions(self) -> dict[str, Live]:
        out: dict[str, Live] = {}
        folder = self.root / "sessions"
        try:
            entries = list(os.scandir(folder))
        except OSError:
            self._sessions.clear()
            return out
        seen = set()
        for entry in entries:
            if not entry.name.endswith(".json"):
                continue
            seen.add(entry.name)
            try:
                st = entry.stat()
            except OSError:
                continue
            sig = (st.st_mtime_ns, st.st_size)
            cached = self._sessions.get(entry.name)
            if cached and cached[0] == sig:
                doc = cached[1]
            else:
                doc = _read_small_json(entry.path)
                if doc is None:
                    continue  # mid-write: try again next tick
                # ci-pr1: "idle" -> "busy" rewrites the file at the same size, and a rewrite
                # within the file system's time tick keeps the same mtime: a file changed this
                # recently is read again next tick instead of trusting (mtime, size)
                settled = time.time_ns() - st.st_mtime_ns > RACY_NS
                self._sessions[entry.name] = (sig if settled else None, doc)
            try:
                live = self._live(doc)
            except (ValueError, OverflowError, TypeError):
                live = None  # one odd file must never stop every poll
            if live is None:
                continue
            old = out.get(live.sid)
            if old is None or (live.alive, live.since or "") > (old.alive, old.since or ""):
                out[live.sid] = live
        for name in [n for n in self._sessions if n not in seen]:
            del self._sessions[name]
        return out

    def _live(self, doc: Mapping[str, Any]) -> Live | None:
        sid, pid = doc.get("sessionId"), _pid(doc.get("pid"))
        if not isinstance(sid, str) or not slug(sid, 12) or pid is None:
            return None
        alive, start = process_info(pid)
        alive = alive and same_process(start, doc.get("startedAt"), doc.get("procStart"))
        since = _ms(doc.get("statusUpdatedAt")) or _ms(doc.get("updatedAt"))
        started = _ms(doc.get("startedAt"))
        status = doc.get("status") if isinstance(doc.get("status"), str) else "busy"
        return Live(sid=sid, status=status[:40], since=iso(since) if since else None,
                    started=iso(started) if started else None,
                    cwd=folder_path(doc.get("cwd")), name=_text(doc.get("name")),
                    auto_name=doc.get("nameSource") in (None, "auto"), alive=alive,
                    sure=start is not None, entry=_text(doc.get("entrypoint"), 40))

    # ------------------------------------------------------------ finding logs
    def _scan_due(self, lives: Mapping[str, Live]) -> bool:
        if self._scanned is None or time.monotonic() - self._scanned >= self.rescan:
            return True
        return time.monotonic() - self._scanned >= min(5, self.rescan) and any(
            sid not in self._logs and not self._find_log(sid, live) for sid, live in lives.items())

    def _scan(self, now: datetime) -> None:
        self._scanned = time.monotonic()
        cutoff = now.timestamp() - self.window.total_seconds()
        logs: dict[str, Path] = {}
        mtimes: dict[str, float] = {}
        try:
            folders = [e for e in os.scandir(self.root / "projects") if e.is_dir()]
        except OSError:
            return  # keep what we knew
        for folder in folders:
            try:
                entries = list(os.scandir(folder.path))
            except OSError:
                continue
            for entry in entries:
                if not entry.name.endswith(".jsonl"):
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if mtime < cutoff:
                    continue
                sid = entry.name[:-6]
                if sid in logs and mtimes[sid] >= mtime:
                    continue
                logs[sid], mtimes[sid] = Path(entry.path), mtime
        self._logs, self._mtime = logs, mtimes
        self._subs = {sid: subs for sid, subs in self._subs.items() if sid in logs}

    def _find_log(self, sid: str, live: Live) -> bool:
        """A live session's log, by its cwd's folder name (Claude Code's: every character but
        a letter or digit becomes "-"), without waiting for the next listing."""
        path = self._logs.get(sid)
        if path is None and live.cwd:
            guess = self.root / "projects" / re.sub(r"[^A-Za-z0-9]", "-", live.cwd) / (
                sid + ".jsonl")
            if guess.is_file():
                path = self._logs[sid] = guess
        if path is None:
            return False
        with contextlib.suppress(OSError):
            self._mtime[sid] = path.stat().st_mtime
        return True

    def _sub_files(self, sid: str, refresh: bool) -> tuple[dict[str, dict[str, Path]], bool]:
        """A session's subagent files (agent -> {"log", "meta"}) and whether they were listed
        just now (then :attr:`_sig` holds each file's size and mtime from the listing: free on
        Windows, so unchanged subagents cost no ``stat``)."""
        if not refresh and sid in self._subs:
            return self._subs[sid], False
        log = self._logs.get(sid)
        found: dict[str, dict[str, Path]] = {}
        if log is not None:
            try:
                entries = list(os.scandir(log.with_suffix("") / "subagents"))
            except OSError:
                entries = []
            for entry in entries:
                name = entry.name
                if not name.startswith("agent-"):
                    continue
                if name.endswith(".meta.json"):
                    agent, kind = name[6:-10], "meta"
                elif name.endswith(".jsonl"):
                    agent, kind = name[6:-6], "log"
                else:
                    continue
                path = Path(entry.path)
                found.setdefault(agent, {})[kind] = path
                try:
                    st = entry.stat()
                    self._sig[str(path)] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    self._sig.pop(str(path), None)
        self._subs[sid] = found
        return found, True

    # ------------------------------------------------------------ reading logs
    def _read_main(self, sid: str, ses: dict, live: Live | None, *, cursors: dict,
                   back: dict, budget: Budget, salt: str, skim: bool = False) -> None:
        path = self._logs.get(sid)
        if path is None:
            return
        key = cursor_key(path)
        alive = bool(live and live.alive)
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        if alive and size is not None:
            cur = cursors.get(key)
            behind = size - int(cur.get("offset", 0)) if isinstance(cur, dict) else size
            if behind > BIG and ses.get("bf") is None:
                # live, far behind: the live cursor jumps near the end; the gap is history
                stop = size - BACK
                if isinstance(cur, dict):
                    back[key] = dict(cur)
                else:
                    back.pop(key, None)
                    start_at(path, back, 0)
                    self._head(sid, ses, path, budget)  # its start time before the first send
                ses["bf"] = stop
                start_at(path, cursors, stop)
        result = tail(path, cursors, budget)
        if result.missing:
            ses.pop("bf", None)
            back.pop(key, None)
            self._logs.pop(sid, None)
            ses["c"] = 0
            return
        if result.busy:
            return
        if result.reset:
            for k in ("fh", "pend", "end", "err", "bf"):
                ses.pop(k, None)
            back.pop(key, None)
        self._lines(sid, ses, result.lines, salt, history=False, skim=skim)
        ses["c"] = 1 if result.more else 0

    def _head(self, sid: str, ses: dict, path: Path, budget: Budget) -> None:
        """A live log that starts near its end: its first line's time (the session's start, so
        the ``start`` event comes before the history found later) and its ``cwd``."""
        if budget.left < HEAD_SCAN:
            return
        try:
            with open(path, "rb") as fh:
                head = fh.read(HEAD_SCAN)
        except OSError:
            return
        budget.spend(len(head))
        m = _TIMESTAMP.search(head)
        ts = _ts(m.group(1).decode("ascii")) if m else None
        if ts:
            ses["t0"] = _earlier(ses.get("t0"), ts)
        m = _CWD.search(head)
        cwd = folder_path(_json_string(m.group(1))) if m else ""
        if cwd:
            self._cwd.setdefault(sid, cwd)

    def _lines(self, sid: str, rec: dict, lines: list[Line], salt: str, *, history: bool,
               skim: bool, cwd_into: str | None = "main") -> None:
        """Hand ``lines`` to :meth:`_line`. ``skim`` (history: a log no one is writing): only
        the lines that can change a count, a title, an error or the end of a turn are parsed
        (a cheap byte check first); the first and last times come from a regex. No tool call
        is kept as pending (their results are not parsed)."""
        if not skim:
            for line in lines:
                self._line(sid, rec, line, salt, history=history, cwd_into=cwd_into)
            return
        for line in lines[:8]:  # the first time in this chunk
            m = _TIMESTAMP.search(line.data)
            if m:
                ts = _ts(m.group(1).decode("ascii"))
                if ts:
                    rec["t0"] = _earlier(rec.get("t0"), ts)
                    rec["t"] = _later(rec.get("t"), ts)
                break
        for line in reversed(lines[-8:]):  # ... and the last
            m = _TIMESTAMP.search(line.data)
            if m:
                ts = _ts(m.group(1).decode("ascii"))
                if ts:
                    rec["t"] = _later(rec.get("t"), ts)
                    rec["t0"] = _earlier(rec.get("t0"), ts)
                break
        for line in lines:
            data = line.data
            if (line.big or rec.get("err") or rec.get("end") or b'"tool_use"' in data
                    or b'-title"' in data or b'"agent-name"' in data
                    or b'"isApiErrorMessage"' in data or b'"end_turn"' in data
                    or (b'"user"' in data and b'"tool_use_id"' not in data)):
                self._line(sid, rec, line, salt, history=history, cwd_into=cwd_into)
        rec.pop("pend", None)  # their results were skipped: nothing is known to be pending
        rec["st"] = _later(rec.get("st"), rec.get("t"))

    def _backfill(self, sid: str, ses: dict, back: dict, budget: Budget, salt: str) -> None:
        path = self._logs.get(sid)
        stop = ses.get("bf")
        if path is None or not isinstance(stop, int):
            ses.pop("bf", None)
            return
        key = cursor_key(path)
        if key not in back:
            ses.pop("bf", None)
            return
        result = tail(path, back, budget)
        if result.busy:
            return
        done = result.missing or bool(result.reset)
        lines = [line for line in result.lines if line.offset < stop]
        done = done or len(lines) < len(result.lines)
        self._lines(sid, ses, lines, salt, history=True, skim=self.skim)
        cur = back.get(key)
        if done or not result.more or not isinstance(cur, dict) or cur.get("offset", 0) >= stop:
            back.pop(key, None)
            ses.pop("bf", None)

    def _read_subs(self, sid: str, ses: dict, subs: dict, *, cursors: dict, budget: Budget,
                   salt: str, refresh: bool, busy: bool = False, quiet: float = 0.0) -> bool:
        """Read a session's subagent logs. Returns whether one is still not read to its end.
        ``busy``: the session is live and mid-turn (its subagents' pending calls matter);
        ``quiet``: a log not written since this time is history (skimmed when allowed)."""
        files_of, listed = self._sub_files(sid, refresh)
        lag = False
        for agent, files in files_of.items():
            key = f"{sid}/{agent}"
            sub = subs.get(key)
            if sub is None:
                sub = subs[key] = {"id": sub_id(sid, agent), "c": 1}
            meta = files.get("meta")
            if meta is not None and (listed or not sub.get("e")):
                sig = self._sig.get(str(meta))
                cached = self._metas.get(str(meta))
                doc = cached[1] if cached and sig and cached[0] == sig else self._meta(meta)
                if doc is not None and (cached is None or doc is not cached[1]):
                    self._touched.add(key)
                    sub["fin"] = 1 if doc.get("finished") is True else 0
                    sub["ty"] = _text(doc.get("agentType"), 64)
                    if self.titles == "session":  # fix-PRIV: else no task text is kept
                        self._keep_title(sid, sub, "ti",  # review-fix: B2 2
                                         _text(doc.get("description"), 120) or sub["ty"])
                    tool = doc.get("toolUseId")  # the parent's call that launched it
                    if isinstance(tool, str) and tool:
                        sub["tu"] = tool[:128]
                    parent = doc.get("parentAgentId")
                    if isinstance(parent, str) and slug(parent, 12):
                        sub["pa"] = sub_id(sid, parent)
                        sub["pg"] = parent[:128]
            log = files.get("log")
            if log is None:
                sub["c"] = 0
                continue
            if not sub.get("c"):
                if not listed:
                    continue  # read to the end, and not listed again: nothing to check
                sig = self._sig.get(str(log))
                if sig is not None and self._done.get(str(log)) == sig:
                    continue  # the same size and time as when it was read to the end
            if not budget.allow():
                lag = lag or bool(sub.get("c"))
                continue
            self._touched.add(key)
            sig = self._sig.get(str(log))
            old = sig is not None and sig[0] / 1e9 < quiet  # not written lately
            ckey = cursor_key(log)
            if (sub.get("fin") and old and sig is not None and sig[1] > HUGE_SUB
                    and ckey not in cursors):
                sub["jump"] = sig[1] - HUGE_TAIL  # huge and finished: its head, then its tail
            result = tail(log, cursors, budget)
            if result.busy:
                lag = lag or bool(sub.get("c"))
                continue
            if result.missing:
                sub["c"] = 0
                sub.pop("jump", None)
                continue
            if result.reset:
                for k in ("fh", "pend", "end", "err"):
                    sub.pop(k, None)
            skim = self.skim and old and (bool(sub.get("fin")) or not busy)
            self._lines(sid, sub, result.lines, salt, history=False, skim=skim, cwd_into=None)
            jump = sub.pop("jump", None)
            cur = cursors.get(ckey)
            if (isinstance(jump, int) and result.more and isinstance(cur, dict)
                    and int(cur.get("offset", 0)) < jump):
                start_at(log, cursors, jump)  # the files changed in between are not counted
            sub["c"] = 1 if result.more else 0
            lag = lag or bool(result.more)
            cur = cursors.get(ckey)
            if not result.more and isinstance(cur, dict):
                self._done[str(log)] = (cur.get("mtime_ns"), cur.get("size"))
            ses["ta"] = _later(ses.get("ta"), sub.get("t"))
        return lag

    def _meta(self, path: Path) -> dict | None:
        try:
            st = path.stat()
        except OSError:
            return None
        sig = (st.st_mtime_ns, st.st_size)
        cached = self._metas.get(str(path))
        if cached and cached[0] == sig:
            return cached[1]
        doc = _read_small_json(path)
        if doc is not None:
            self._metas[str(path)] = (sig, doc)
        return doc

    # ------------------------------------------------------------ one line
    def _line(self, sid: str, ses: dict, line: Line, salt: str, *, history: bool,
              cwd_into: str | None = "main") -> None:
        doc = line.json()
        if doc is None:
            if line.big:
                self._big_line(sid, ses, line.data, salt=salt, history=history,
                               cwd_into=cwd_into)
            return
        if not isinstance(doc, dict):
            return
        kind = doc.get("type")
        if isinstance(kind, str) and kind.startswith("api-request"):
            return  # copies of earlier blocks
        ts = _ts(doc.get("timestamp"))
        if ts:
            ses["t"] = _later(ses.get("t"), ts)
            ses["t0"] = _earlier(ses.get("t0"), ts)
        cwd = doc.get("cwd")
        if cwd_into and isinstance(cwd, str) and folder_path(cwd):
            self._cwd.setdefault(sid, folder_path(cwd))
        entry = doc.get("entrypoint")  # review-fix: B2 4 (how the session was started)
        if cwd_into and isinstance(entry, str) and entry:
            ses["ep"] = _text(entry, 40)
        if kind in ("custom-title", "ai-title", "agent-name"):
            field, value = {"custom-title": ("ct", doc.get("customTitle")),
                            "ai-title": ("ai", doc.get("aiTitle")),
                            "agent-name": ("an", doc.get("agentName"))}[kind]
            text = _text(value, 200)
            # fix-PRIV: with titles = "folder" no session title is kept, not even in the state
            if text and self.titles == "session":
                self._keep_title(sid, ses, field, text, history=history)  # review-fix: B2 2
            return
        message = doc.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if ts and not history and (kind == "assistant" or kind == "user"
                                   and not doc.get("isMeta")):
            # the state time: only a reply, a prompt or a tool result can change the state, so
            # only these move the record's "at" (a hook's sure "waiting" written after it wins)
            ses["st"] = _later(ses.get("st"), ts)
        if kind == "assistant":
            blocks = content if isinstance(content, list) else []
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name") if isinstance(block.get("name"), str) else ""
                inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                field = FILE_TOOLS.get(name)
                if field and isinstance(inp.get(field), str):
                    self._file(ses, inp[field], ts, salt)
                if not history and isinstance(block.get("id"), str):
                    self._pending(ses, block["id"], name, ts)
            if history:
                return
            if doc.get("isApiErrorMessage") is True:
                ses["err"], ses["end"] = 1, 1
                ses.pop("pend", None)
                return
            ses.pop("err", None)
            stop = message.get("stop_reason") if isinstance(message, dict) else None
            if stop == "end_turn":
                ses["end"] = 1
                ses.pop("pend", None)
            elif stop == "tool_use" or blocks:
                ses.pop("end", None)
        elif kind == "user" and not history:
            mode = doc.get("permissionMode")
            if isinstance(mode, str) and len(mode) <= 40:
                ses["pm"] = mode
            results = [b.get("tool_use_id") for b in content if isinstance(b, dict)
                       and b.get("type") == "tool_result"] if isinstance(content, list) else []
            if results:
                pend = ses.get("pend") or {}
                for tid in results:
                    if isinstance(tid, str):
                        pend.pop(tid, None)
            elif not doc.get("isMeta") and not doc.get("isCompactSummary"):
                self._prompt(ses)

    def _big_line(self, sid: str, ses: dict, head: bytes, *, salt: str, history: bool,
                  cwd_into: str | None) -> None:
        role = _big_role(head)
        if role is None:
            return  # a copy (api-request-*), a progress line nesting another agent's reply, ...
        m = _TIMESTAMP.search(head)
        ts = _ts(m.group(1).decode("ascii")) if m else None
        if ts:
            ses["t"] = _later(ses.get("t"), ts)
            ses["t0"] = _earlier(ses.get("t0"), ts)
            if not history:
                ses["st"] = _later(ses.get("st"), ts)
        if cwd_into and sid not in self._cwd:
            m = _CWD.search(head)
            cwd = _json_string(m.group(1)) if m else None
            if cwd and folder_path(cwd):
                self._cwd[sid] = folder_path(cwd)
        if role == b"assistant":
            for use in _TOOL_USE.finditer(head):
                name = use.group(2).decode("utf-8", "replace")
                if name in FILE_TOOLS:
                    found = _FILE_PATH.search(head, use.end(), use.end() + 8192)
                    path = _json_string(found.group(1)) if found else None
                    if path:
                        self._file(ses, path, ts, salt)
                if not history:
                    self._pending(ses, use.group(1).decode("utf-8", "replace"), name, ts)
            if not history:
                if _API_ERROR.search(head):
                    ses["err"], ses["end"] = 1, 1
                    ses.pop("pend", None)
                else:
                    ses.pop("end", None)
                    ses.pop("err", None)
        elif not history:
            ids = _TOOL_RESULT.findall(head)
            if ids:
                pend = ses.get("pend") or {}
                for tid in ids:
                    pend.pop(tid.decode("utf-8", "replace"), None)
            else:
                self._prompt(ses)  # a long prompt (a paste)

    @staticmethod
    def _prompt(ses: dict) -> None:
        for key in ("pend", "end", "err"):
            ses.pop(key, None)

    @staticmethod
    def _pending(ses: dict, tool_id: str, name: str, ts: str | None) -> None:
        pend = ses.setdefault("pend", {})
        pend[tool_id[:128]] = [name[:64], ts or ses.get("t") or ""]
        while len(pend) > MAX_PENDING:
            del pend[next(iter(pend))]

    @staticmethod
    def _file(ses: dict, path: str, ts: str | None, salt: str) -> None:
        norm = path.strip().replace("\\", "/")
        if not norm:
            return
        if re.match(r"^[A-Za-z]:/", norm) or norm.startswith("//"):
            norm = norm.lower()  # Windows paths: one file whatever the case
        digest = hashlib.sha256(f"{salt}\0{norm}".encode()).hexdigest()[:16]
        hashes = ses.setdefault("fh", [])
        if digest in hashes or len(hashes) >= MAX_HASHES:
            return
        hashes.append(digest)
        when = ts or ses.get("t")
        if not when:
            return  # no time known: counted, but no event
        minute = when[:16]
        pending = ses.setdefault("pf", {})
        first, n = pending.get(minute, [when, 0])
        pending[minute] = [min(first, when), n + 1]

    # ------------------------------------------------------------ status changes
    def _cwd_of(self, sid: str, live: Live | None, budget: Budget) -> str:
        if live and live.cwd:
            self._cwd.setdefault(sid, live.cwd)
            return live.cwd
        if sid in self._cwd:
            return self._cwd[sid]
        path = self._logs.get(sid)
        if path is None or sid in self._no_cwd or budget.left < HEAD_SCAN:
            return ""  # (no room left this poll: try again next poll)
        try:
            with open(path, "rb") as fh:
                head = fh.read(HEAD_SCAN)
        except OSError:
            return ""
        budget.spend(len(head))
        m = _CWD.search(head)
        cwd = folder_path(_json_string(m.group(1))) if m else ""
        if cwd:
            self._cwd[sid] = cwd
        else:
            self._no_cwd.add(sid)  # none in its first 64 KB: the lines read later may say
        return cwd

    def _main_state(self, ses: dict, live: Live | None, now: datetime
                    ) -> tuple[str, str, int | None, str | None]:
        """(state, message, stale_after, at) of a main session.

        ``at`` is the state time: the session file's ``statusUpdatedAt`` and the last line that
        can change the state (``st``), never a state-free line or a subagent's line, so a
        hook's record written after it (a sure "waiting") keeps its state. A death the reader
        sees itself is new: its ``at`` is when it was first seen (``dd``)."""
        last = _later(ses.get("t"), ses.get("ta"))  # any line: for "is it still moving?"
        state_at = ses.get("st") or ses.get("t")
        catching = bool(ses.get("c"))
        if live is not None:
            at = _later(live.since, state_at) or last or iso(now)
            if not live.alive:
                moment = parse_time(last) if last else None
                if ses.get("dd"):
                    at = _later(at, ses["dd"])
                elif live.status != "idle" and moment is not None:
                    # never seen alive: it died after its log stopped moving (after a hook's
                    # last write); a log that moved just now: it died just now
                    gone = moment + timedelta(seconds=ACTIVE)
                    at = _later(at, iso(gone) if gone <= now else ses.setdefault("dd", iso(now)))
                if live.status == "idle":
                    if ses.get("err") and not catching:
                        return "error", "Stopped by an API error", None, at
                    return "ok", "Session ended", None, at
                return "working", "Ended mid-turn", DEAD_STALE, at
            if live.status != "idle" and WAIT_STATUS.search(live.status):
                return "waiting", "Needs you", None, at
            if live.status != "idle":
                if not catching:
                    asked = self._asked(ses, now)
                    if asked:
                        return "waiting", asked, None, at
                return "working", "Working", LIVE_STALE, at
            if ses.get("err") and not catching:
                return "error", "Stopped by an API error", None, at
            if self.on_stop == "waiting":
                return "waiting", "Your turn", None, at
            return "ok", "Turn finished", None, at
        at = _later(ses.get("gone"), state_at) or last or iso(now)
        if ses.get("err"):
            return "error", "Stopped by an API error", None, at
        if ses.get("sf"):
            return "ok", "Session ended", None, at  # its session file went away: it exited
        moment = parse_time(last) if last else None
        recent = moment is not None and now - moment < timedelta(seconds=ACTIVE)
        if recent and not ses.get("end"):
            return "working", "Working", None, at
        if recent:
            return "ok", "Turn finished", None, at
        if moment is not None and not ses.get("end"):
            # ended mid-turn: seen when its log stopped moving, after a hook's last write
            at = _later(at, iso(min(moment + timedelta(seconds=ACTIVE), now)))
        return "ok", "Session ended", None, at

    @staticmethod
    def _asked(ses: dict, now: datetime) -> str:
        pend = ses.get("pend") or {}
        quick = None
        for name, ts in pend.values():
            if name in QUESTION_TOOLS:
                return "Has a question for you"
            if name in QUICK_TOOLS and ts:
                moment = parse_time(ts)
                if moment and now - moment >= timedelta(seconds=PROMPT_AFTER):
                    quick = "May need your permission"
        if quick and ses.get("pm") not in BYPASS_MODES:
            return quick
        return ""

    def _private(self, cwd: Any) -> bool:
        """fix2-PRIV: ``cwd`` is in or under a private folder."""
        return bool(self.private) and hidden(self.private, cwd)

    # --- review-fix: B2 2 ---
    def _folder_of(self, sid: str) -> str:
        """The session's folder as known now (its session file's, else its log's), or ``""``."""
        return self._live_cwd.get(sid) or self._cwd.get(sid, "")

    def _keep_title(self, sid: str, rec: dict, field: str, text: str, *,
                    history: bool = False) -> None:
        """Keep a title of session ``sid`` (or one of its subagents: ``rec``) in the saved
        state, unless the session is in a private folder. With private folders and the folder
        not known yet, it waits in memory (:meth:`_settle_titles`). History only fills a title
        that is missing; a live line replaces it."""
        if not text:
            rec[field] = text
            return
        if self.private:
            cwd = self._folder_of(sid)
            if not cwd:
                key = str(rec.get("id"))
                fields = self._unsure.setdefault(key, (sid, {}))[1]
                if not history or field not in fields:
                    fields[field] = (text, history)
                return
            if self._private(cwd):
                rec.pop(field, None)
                return
        if not history or not rec.get(field):
            rec[field] = text

    def _settle_titles(self, mains: dict, subs: dict) -> None:
        """Titles waiting for their session's folder go into the state once it is known and
        not private (else they are dropped); and no session in a private folder keeps a title
        in the state (one an older run saved, too)."""
        for key, (sid, fields) in list(self._unsure.items()):
            # found again by id: the Watcher hands each poll a copy of the saved state
            main = mains.get(sid)
            rec = main if isinstance(main, dict) and main.get("id") == key else next(
                (v for k, v in subs.items() if k.startswith(f"{sid}/") and isinstance(v, dict)
                 and v.get("id") == key), None)
            if rec is None:
                del self._unsure[key]  # forgotten (pruned)
                continue
            cwd = self._folder_of(sid)
            if not cwd:
                continue
            del self._unsure[key]
            if self._private(cwd):
                continue
            for field, (text, history) in fields.items():
                if not history or not rec.get(field):
                    rec[field] = text
        if not self.private:
            return
        for sid, ses in mains.items():
            if self._private(self._folder_of(sid)):
                for field in FOLDER_DROPS:
                    ses.pop(field, None)
        for key, sub in subs.items():
            if self._private(self._folder_of(key.split("/", 1)[0])):
                sub.pop("ti", None)

    @staticmethod
    def _kind(ses: dict | None, live: Live | None) -> str:
        """review-fix: B2 4: ``job`` for a headless run (``entrypoint`` ``sdk-*``), else
        ``task``."""
        entry = (live.entry if live is not None and live.entry else "") or (
            ses.get("ep") if ses and isinstance(ses.get("ep"), str) else "")
        return "job" if entry.startswith(HEADLESS) else "task"
    # --- end review-fix: B2 2 ---

    def _title(self, ses: dict, live: Live | None, cwd: str) -> str:
        """The session's title, or with ``titles = "folder"`` its project folder's name only
        (Claude Code writes its titles from your first message). Never a home folder's name.
        fix2-PRIV: a session in a private folder keeps no title (the Watcher hides its record
        by its ``x-folder``)."""
        private = self._private(cwd)  # fix2-PRIV
        if self.titles == "folder" or private:  # fix-PRIV: titles an older run kept go too
            for key in FOLDER_DROPS:
                ses.pop(key, None)
            return DISPLAY if private else (project_folder(cwd) or DISPLAY)
        if live and live.name:
            ses["sn"], ses["sa"] = live.name, 1 if live.auto_name else 0
        if ses.get("ct"):
            return ses["ct"]
        if ses.get("sn") and not ses.get("sa"):
            return ses["sn"]
        return (ses.get("ai") or ses.get("sn") or ses.get("an") or project_folder(cwd)
                or DISPLAY)

    def _emit_main(self, sid: str, ses: dict, live: Live | None, *, now: datetime,
                   batch: Batch, budget: Budget) -> None:
        if live is not None:
            ses["sf"] = 1
            ses.pop("gone", None)
            if not ses.get("t0") and live.started:
                ses["t0"] = live.started
            elif live.started:
                ses["t0"] = _earlier(ses["t0"], live.started)
        elif ses.get("sf") and not ses.get("gone"):
            ses["gone"] = iso(now)
        if live is not None and live.alive:
            ses["al"] = 1
            ses.pop("dd", None)
        elif live is not None and ses.get("al"):
            ses.setdefault("dd", iso(now))  # seen alive, now dead: it died just now
        if live is None and ses.get("c"):
            return  # history: wait until the whole log is read (the right end time)
        if not ses.get("t0") and live is None:
            return  # nothing read yet
        first = not ses.get("e")
        cwd = self._cwd_of(sid, live, budget)  # (after a restart too: events carry it)
        if first and not cwd:
            return  # no folder known: private_dirs couldn't be checked, so no record yet
        state, message, stale, at = self._main_state(ses, live, now)
        # never back in time: a record this reader wrote later would hold the change back
        at = ses["la"] = _later(at, ses.get("la"))
        change: dict[str, Any] = {
            "id": ses["id"], "at": at, "agent": AGENT, "kind": self._kind(ses, live),  # B2 4
            "title": self._title(ses, live, cwd), "state": state, "message": message,
            "needs_you": [{"text": message}] if state == "waiting" else None,
            "stale_after": stale,
        }
        if ses.get("t0"):
            change["started"] = ses["t0"]
        if cwd:
            change["x-folder"] = cwd
            change["x-project"] = project_folder(cwd) or None
        if ses.get("fh"):
            change["x-files"] = len(ses["fh"])
        self._send(ses, change, cwd, first, batch)

    def _emit_sub(self, sid: str, sub: dict, parent: dict | None, live: Live | None,
                  subs: Mapping[str, dict], *, now: datetime, batch: Batch,
                  budget: Budget) -> None:
        if parent is None or not parent.get("e"):
            return  # its session's record first (x-parent must point somewhere)
        alive = bool(live and live.alive)
        if sub.get("c") and not alive:
            return
        if not sub.get("t0"):
            return
        first = not sub.get("e")
        cwd = self._cwd_of(sid, live, budget)  # the session's: after a restart too
        if first and not cwd:
            return  # no folder known: private_dirs couldn't be checked, so no record yet
        last = sub.get("t")
        moment = parse_time(last) if last else None
        recent = moment is not None and now - moment < timedelta(seconds=ACTIVE)
        # still launched: the call that started it awaits its result in a live, busy session
        holder = subs.get(f"{sid}/{sub['pg']}") if sub.get("pg") else parent
        waited = bool(alive and live is not None and live.status != "idle" and sub.get("tu")
                      and holder is not None and sub["tu"] in (holder.get("pend") or {}))
        stale = None
        if sub.get("err") and not sub.get("c"):
            state, message = "error", "Stopped by an API error"
        elif sub.get("fin"):
            state, message = "ok", "Finished"
        elif recent or waited:
            state, message = "working", "Working"
            stale = LIVE_STALE if waited else None  # a long tool call of its own, say
        else:
            state, message = "ok", "Ended"  # killed, interrupted, or its session is gone
        if self.titles == "folder" or self._private(cwd):  # fix2-PRIV: nor a private one's
            sub.pop("ti", None)  # fix-PRIV: a task title an older run kept
        title = sub.get("ti") if self.titles == "session" else sub.get("ty")
        change: dict[str, Any] = {
            "id": sub["id"], "at": last or iso(now), "agent": AGENT,
            "kind": self._kind(parent, live),  # review-fix: B2 4 (its session's kind)
            "title": title or "Subagent", "state": state, "message": message,
            "started": sub["t0"], "stale_after": stale,
            "x-parent": sub.get("pa") or parent["id"],
        }
        if cwd:
            change["x-folder"] = cwd
            change["x-project"] = project_folder(cwd) or None
        if sub.get("fh"):
            change["x-files"] = len(sub["fh"])
        self._send(sub, change, cwd, not sub.get("e"), batch)

    @staticmethod
    def _send(rec: dict, change: dict, cwd: str, first: bool, batch: Batch) -> None:
        digest = hashlib.sha1(json.dumps(change, sort_keys=True).encode()).hexdigest()[:12]
        if digest == rec.get("d") and not rec.get("pf"):
            return
        extra = {"folder": cwd} if cwd else {}
        if first:
            batch.events.append({"t": rec.get("t0") or change["at"], "id": change["id"],
                                 "k": "start", **extra})
        for _minute, (when, n) in sorted((rec.pop("pf", None) or {}).items()):
            batch.events.append({"t": when, "id": change["id"], "k": "files", "n": n, **extra})
        batch.agents.append(change)
        rec["d"], rec["e"] = digest, 1

    # ------------------------------------------------------------ forgetting
    def _prune(self, mains: dict, subs: dict, cursors: dict, back: dict,
               lives: Mapping[str, Live]) -> None:
        keep_paths: list[Path] = []
        for sid in list(mains):
            if sid in self._logs or sid in lives:
                if sid in self._logs:
                    keep_paths.append(self._logs[sid])
                continue
            del mains[sid]
        keep_files: set[str] = set()
        for key in list(subs):
            sid, _, agent = key.partition("/")
            log = self._subs.get(sid, {}).get(agent, {}).get("log")
            if sid not in mains:
                del subs[key]
                continue
            if log is not None:
                keep_paths.append(log)
        for sid, agents in self._subs.items():
            if sid in mains:
                keep_files.update(str(p) for files in agents.values() for p in files.values())
        forget(cursors, keep_paths)
        forget(back, keep_paths)
        # the memory caches too (an app left running for weeks)
        for cache in (self._sig, self._done, self._metas):
            for name in [n for n in cache if n not in keep_files]:
                del cache[name]
        self._cwd = {s: c for s, c in self._cwd.items() if s in mains}
        self._no_cwd &= set(mains)
        self._subs = {s: f for s, f in self._subs.items() if s in mains}


def _big_role(head: bytes) -> bytes | None:
    """The role (``b"assistant"`` / ``b"user"``) of a big line from its head, or ``None`` when it
    is not the session's own message: the first ``"message":{`` must be a top-level key, no
    other top-level ``"type"`` may come before it, and its ``"role"`` must follow within 200
    bytes (copies and progress lines nest the same messages deeper, or say their type first).
    """
    m = _MESSAGE.search(head, 0, 16384)
    if m is None:
        return None
    depth, key_type = 0, False
    for tok in _JSON_TOKEN.finditer(head, 0, m.start()):
        text = tok.group()
        if text in (b"{", b"["):
            depth += 1
        elif text in (b"}", b"]"):
            depth -= 1
        elif depth == 1 and key_type:
            if text not in (b'"assistant"', b'"user"'):
                return None  # {"type":"api-request-blob",...,"message":{...}}
            key_type = False
        elif depth == 1:
            key_type = text == b'"type"' and head[tok.end():tok.end() + 1] == b":"
    if depth != 1:
        return None  # nested: {"type":"progress","data":{"message":{...}}}
    role = _ROLE.search(head, m.end(), m.end() + 200)
    return role.group(1) if role else None


def _read_small_json(path: str | os.PathLike) -> dict | None:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(SESSION_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > SESSION_MAX_BYTES:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def make_reader(options: Mapping[str, Any]) -> ClaudeCodeReader:
    """The factory for ``[[live.reader]] kind = "claude_code"``."""
    days = options.get("days", 7)
    return ClaudeCodeReader(options.get("root") or None,
                            on_stop=str(options.get("on_stop", "ok")),
                            days=days if isinstance(days, int) and not isinstance(days, bool)
                            else 7,
                            titles=str(options.get("titles", "session")),
                            private_dirs=options.get("private_dirs"),  # fix2-PRIV
                            config_dir=options.get("config_dir"))
