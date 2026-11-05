"""Codex reader: Codex sessions from their rollout logs, without hooks.

Built-in kind ``codex``::

    [[live.reader]]
    kind = "codex"
    root = "~/.codex"      # optional: else $CODEX_HOME, else ~/.codex
    days = 7               # optional: how many day folders to watch

What it reads (all read-only):

* ``<root>/sessions/YYYY/MM/DD/rollout-*.jsonl``, the day folders of the last ``days`` local
  days, tailed incrementally (:mod:`lamptown.status.readers.tail`), the most recently changed
  file first; and a session started before that and still written to (a long run, or one
  resumed), whose file stays in its first day's folder: found every :data:`RESCAN` and when
  the window moves, from the database's ``rollout_path`` of threads changed within ``days``
  (only files in this root's day folders count), or, with no usable database, by walking the
  older day folders for files changed within ``days``, :data:`OLD_STEP` folders a poll at
  most (and never past the tick's deadline). A file
  seen for the first time is read from its start when it fits this tick's per-file
  allowance; a bigger one starts that many bytes before its end (its current state counts,
  old turns are skipped). Lines over 256 KB are never parsed as JSON: a regex reads
  their time and record type from the first bytes.
* ``<root>/state_5.sqlite``, opened with a ``mode=ro`` URI, only when it (or its ``-wal``)
  changed or a new session needs its title: point lookups by thread id (primary keys) and one
  ``updated_at_ms`` time-cursor query (indexed, ``LIMIT``), never ``count(*)``. A locked, busy,
  missing or unknown database is skipped and tried again next poll. Sessions with no row yet
  are looked up again, the longest-waiting first, each time the database changes. (W2-left-fix:
  also, every :data:`RESCAN`, one ``updated_at_ms`` query, indexed, ``LIMIT``, for the
  ``rollout_path`` of sessions started before the window, as above.)

One rollout file is one session; its first line is ``session_meta`` (the thread ``id``,
``cwd``, ``timestamp``, ``source``). The record id is the hook adapter's,
:func:`lamptown.status.adapters.codex.thread_id` (``codex-`` + the last 12 hex digits of the
UUID: its random end, as UUIDv7 ids start with a millisecond clock), so hooks and this reader
share one record (two sessions with the same id end get the full id instead). A line whose time
can't be read, or is out of range, is skipped.

Signals (``event_msg`` payload types) to states, from the real format plus Codex's protocol:

==============================================  ==========  ================================
``task_started``                                 working     a turn began ("start")
``task_complete``                                ok          turn finished ("done")
``turn_aborted``                                 ok          interrupted ("done")
``error``                                        error       the turn failed ("fail")
``exec_approval_request``,                       waiting     needs you ("wait"); the next
``apply_patch_approval_request``,                            activity line is the answer
``request_user_input``, ``elicitation_request``,
``request_permissions``
any ``response_item`` or other ``event_msg``     working     only after a wait or an error
(not ``token_count`` / settings)                             (or when the first lines read
                                                             are mid-turn)
==============================================  ==========  ================================

The rollouts on the PC this was built on held only ``task_started`` / ``task_complete``; the
wait and error names are Codex protocol events that were not seen there. A failed command
(``CommandExecution`` with ``status: failed``) is normal work, not an error. Approval-reviewer
sessions (``thread_source`` ``guardian_review``, or a ``guardian`` subagent source) are not
agents and are skipped.

Changed files: ``FileChange`` items (their ``changes`` keys), ``patch_apply_*`` events and
``apply_patch`` tool calls (``*** Update File:`` headers) count each path once per session, as
a salted hash in the reader state (never the path): at most :data:`MAX_HASHES` per session and
:data:`MAX_ALL_HASHES` in all; past that (and for a session finished a day ago) the count is
kept and the hashes go. Titles come from ``threads.title`` (else the subagent's nickname, else
the project folder name, else ``Codex``: a session in the home folder or a root never shows the
login name); ``x-parent`` from the session's ``thread_spawn`` source or ``thread_spawn_edges``.
Message text, prompts, tool input and file contents are never kept or sent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lamptown.status.adapters import folder_path, project_folder
from lamptown.status.adapters.codex import thread_id
from lamptown.status.events import LIMITS, NAME_RULE, name_only
from lamptown.status.readers import MAX_AGENTS, MAX_EVENTS, Batch
from lamptown.status.readers.git import hidden, private_roots  # fix2-PRIV
from lamptown.status.readers.tail import Budget, Line, cursor_key, forget, start_at, tail
from lamptown.status.schema import parse_time
from lamptown.status.store import iso, slug

AGENT = "codex"
DISPLAY = "Codex"
TITLE_LIMIT = LIMITS["title"]  # fix-PRIV: a thread title is kept as the town shows it
TITLE_RULE = NAME_RULE  # fix2-PRIV: saved titles cut by an older rule are cut again, once
DB_NAME = "state_5.sqlite"
DAYS = 7
MAX_FILES = 500  # rollout files watched (most recently changed first)
# --- W2-left: sessions started before the window ---
RESCAN = timedelta(minutes=10)  # how often older day folders are searched for live sessions
MAX_OLD_DAYS = 3660  # older day folders searched, at most (newest first)
# --- end W2-left ---
# --- W2-left-fix: a bounded older-day search ---
OLD_ROWS = 2000  # rollout paths asked of the thread database per search
OLD_STEP = 32  # without one: folders (day folders, or month folders to list) walked per poll
OLD_SECONDS = 0.02  # and at most this long per poll (or until the tick's own deadline)
# --- end W2-left-fix ---
META_STEP = 8 * 1024  # the session_meta line is read in steps: this first, then
META_HEAD = 64 * 1024  # these, up to
META_MAX = 256 * 1024
MAX_HASHES = 500  # changed-file hashes kept per session
MAX_ALL_HASHES = 20000  # and in all (the Watcher's state file is 4 MB, shared)
FOLD_AFTER = timedelta(days=1)  # a session finished this long ago keeps its count only
ROOM = 500  # events kept free under MAX_EVENTS
DB_ROWS = 200  # rows per time-cursor query
DB_LOOKUPS = 100  # point lookups per poll
DB_TIMEOUT = 0.1  # seconds to wait on a busy database
SMALL = Budget(total=256 * 1024, per_file=128 * 1024)

START = ("task_started",)
WAIT = ("exec_approval_request", "apply_patch_approval_request", "request_user_input",
        "elicitation_request", "request_permissions")
# event_msg payload type -> (state, message key); see the module docstring
SIGNALS: dict[str, tuple[str, str]] = {
    "task_started": ("working", "working"),
    "task_complete": ("ok", "ok"),
    "turn_aborted": ("ok", "aborted"),
    "error": ("error", "error"),
    **{name: ("waiting", "waiting") for name in WAIT},
}
NOT_ACTIVITY = ("token_count", "thread_settings_applied")
MESSAGES = {"working": "Working", "ok": "Turn finished", "waiting": "Waiting for approval",
            "error": "Turn failed", "aborted": "Interrupted", "new": "Session started"}
NEED = [{"text": "Approve or answer in Codex"}]
EVENT_OF = {"waiting": "wait", "ok": "done", "error": "fail"}

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_NAME_RE = re.compile(r"^rollout-.*?(" + _UUID + r")\.jsonl$")
_STR = r'"((?:[^"\\]|\\.)*)"'
_TS_RE = re.compile(rb'"timestamp"\s*:\s*"([^"]{1,40})"')
_TYPE_RE = re.compile(rb'"type"\s*:\s*"([A-Za-z_]{1,64})"')
_ID_RE = re.compile(rb'"id"\s*:\s*"(' + _UUID.encode() + rb')"')
_CWD_RE = re.compile(rb'"cwd"\s*:\s*' + _STR.encode())
_CHANGES_RE = re.compile(rb'"changes"\s*:\s*\{\s*' + _STR.encode() + rb"\s*:")
_PATCH_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$|^\*\*\* Move to: (.+?)\s*$",
                       re.MULTILINE)
_PATCH_BIG_RE = re.compile(rb"\*\*\* (?:Add|Update|Delete) File: (.+?)(?:\\n|\\r|\")")


def make_reader(options: dict) -> CodexReader:
    return CodexReader(**{k: v for k, v in options.items()
                          if k in ("root", "days", "private_dirs", "config_dir")})  # fix2-PRIV


def default_root() -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env).expanduser() if env else Path.home() / ".codex"


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _when(text: Any) -> datetime | None:
    """A line's time in UTC, or ``None`` (none, unreadable or out of range: the line is
    skipped, never fatal)."""
    try:
        moment = parse_time(text) if isinstance(text, str) else None
        moment = moment.astimezone(UTC) if moment else None
    except (OverflowError, ValueError, OSError):
        return None
    return moment if moment is not None and 1 < moment.year < 9999 else None


def _unjson(raw: bytes) -> str:
    try:
        value = json.loads(b'"' + raw + b'"')
    except ValueError:
        return ""
    return value if isinstance(value, str) else ""


# --- W2-left: sessions started before the window ---
def _numbered(folder: Path | str, width: int) -> list[tuple[int, str]]:
    """The sub-folders of ``folder`` named with ``width`` digits (a year, month or day)."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    out = []
    for entry in entries:
        if len(entry.name) == width and entry.name.isdigit() and entry.is_dir():
            out.append((int(entry.name), entry.path))
    return out
# --- end W2-left ---


class CodexReader:
    """Codex sessions from rollout logs (see the module docstring)."""

    name = "codex"

    def __init__(self, root: str | os.PathLike | None = None, days: int = DAYS,
                 private_dirs: Any = None, config_dir: Any = None) -> None:
        # --- fix2-PRIV: a session in a private folder keeps no project name or title in the
        # saved state (the Watcher adds its own private folders here too) ---
        self.private = private_roots(private_dirs, config_dir)
        # --- end fix2-PRIV ---
        self.root = Path(os.path.expandvars(str(root))).expanduser() if root else default_root()
        self.days = max(1, min(int(days), 31)) if isinstance(days, int) else DAYS
        self._cwd: dict[str, str] = {}  # file key -> the session's folder (memory only)
        self._older: list[Path] = []  # W2-left: live files in older day folders (memory only)
        self._older_at: datetime | None = None  # when older day folders were last searched
        self._older_days: set[tuple[int, int, int]] = set()  # the window's days then
        self._walk: Any = None  # W2-left-fix: a folder walk in progress (a generator)

    # ------------------------------------------------------------ files
    def _files(self, now: datetime, budget: Budget | None = None
               ) -> list[tuple[Path, os.stat_result]]:
        today = now.astimezone().date()
        out: list[tuple[Path, os.stat_result]] = []
        recent: set[tuple[int, int, int]] = set()
        for back in range(-1, self.days):  # -1: tomorrow, for a clock or zone at odds
            day = today - timedelta(days=back)
            recent.add((day.year, day.month, day.day))
            folder = self.root / "sessions" / f"{day.year:04d}" / f"{day.month:02d}" / \
                f"{day.day:02d}"
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                if not _NAME_RE.match(entry.name):
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                out.append((Path(entry.path), st))
        # --- W2-left: sessions started before the window ---
        # A rollout file stays in the day folder its session started in, so one still written
        # (a long run, or a session resumed) is searched for outside the window: those changed
        # within it, at most every RESCAN. (W2-left-fix: the thread database's rollout paths
        # find them at once; with no usable database the older day folders are walked a few a
        # poll, within the tick's deadline.)
        cutoff = (now - timedelta(days=self.days)).timestamp()
        if recent != self._older_days or (self._walk is None and (
                self._older_at is None or not timedelta(0) <= now - self._older_at < RESCAN)):
            self._older_at, self._older_days = now, recent  # (or the window moved: a day left)
            self._older_search(recent, cutoff)
        self._walk_on(budget)
        keep, have = [], {path for path, _ in out}
        for path in self._older:
            if path in have:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime >= cutoff:
                keep.append(path)
                have.add(path)
                out.append((path, st))
        self._older = keep
        # --- end W2-left ---
        out.sort(key=lambda item: item[1].st_mtime_ns, reverse=True)
        return out[:MAX_FILES]

    # --- W2-left: sessions started before the window --- (W2-left-fix: bounded)
    def _older_search(self, recent: set[tuple[int, int, int]], cutoff: float) -> None:
        """Start a search for live rollout files outside the window: the thread database's
        rollout paths when it has them, else a walk of the older day folders (:meth:`_walk_on`
        takes a few steps a poll; what it finds joins ``_older`` as it goes)."""
        found = self._older_from_db(recent, cutoff)
        if found is not None:
            self._older, self._walk = found, None
        else:
            self._walk = self._older_walk(recent, cutoff)

    def _older_from_db(self, recent: set[tuple[int, int, int]], cutoff: float
                       ) -> list[Path] | None:
        """Rollout files outside the window changed since ``cutoff``, from ``state_5.sqlite``
        (one indexed query, read-only). Only files in this root's day folders count. ``None``:
        no usable database (missing, locked, or a schema without these columns)."""
        path = self.root / DB_NAME
        if not path.is_file():
            return None
        y, m, d = min(recent)
        first_ms = _ms(datetime(y, m, d).astimezone())  # the window's first local day
        try:
            con = _connect(path.absolute().as_uri() + "?mode=ro")
        except (sqlite3.Error, ValueError, OSError):
            return None
        try:
            rows = _old_rows(con, int(cutoff * 1000), first_ms)
        except sqlite3.Error:
            return None
        finally:
            con.close()
        base = os.path.normcase(os.path.abspath(self.root / "sessions")) + os.sep
        out: list[Path] = []
        for (raw,) in rows:
            if not isinstance(raw, str) or not raw or "\0" in raw:
                continue
            file = Path(raw)
            if not file.is_absolute() or not _NAME_RE.match(file.name) \
                    or not os.path.normcase(os.path.abspath(file)).startswith(base):
                continue
            day = file.parts[-4:-1]
            if len(day) != 3 or not all(x.isdigit() for x in day) \
                    or tuple(int(x) for x in day) in recent:
                continue  # not a day folder, or one the window lists anyway
            try:
                if file.stat().st_mtime >= cutoff:
                    out.append(file)
            except OSError:
                continue
            if len(out) >= MAX_FILES:
                break
        return out

    def _older_walk(self, recent: set[tuple[int, int, int]], cutoff: float) -> Any:
        """The walk without a database: a generator taking one folder a step (the year list,
        a year's months, a month's days, or a day's rollout files), newest first; its return
        value is everything it found (also put in ``_older`` as it goes)."""
        found: list[Path] = []
        days = 0
        for year, ypath in sorted(_numbered(self.root / "sessions", 4), reverse=True):
            yield
            for month, mpath in sorted(_numbered(ypath, 2), reverse=True):
                yield
                for day, dpath in sorted(_numbered(mpath, 2), reverse=True):
                    if (year, month, day) in recent:
                        continue
                    if days >= MAX_OLD_DAYS:
                        return found
                    days += 1
                    yield
                    try:
                        entries = list(os.scandir(dpath))
                    except OSError:
                        continue
                    for entry in entries:
                        try:
                            if _NAME_RE.match(entry.name) and entry.stat().st_mtime >= cutoff:
                                found.append(Path(entry.path))
                                self._older.append(Path(entry.path))
                        except OSError:
                            continue
                        if len(found) >= MAX_FILES:
                            return found
        return found

    def _walk_on(self, budget: Budget | None) -> None:
        """A few steps of the walk in progress: at most OLD_STEP, OLD_SECONDS, and never past
        the tick's own deadline."""
        if self._walk is None:
            return
        began = time.monotonic()
        for _ in range(OLD_STEP):
            if budget is not None and budget.deadline is not None \
                    and budget.clock() >= budget.deadline:
                return
            if time.monotonic() - began >= OLD_SECONDS:
                return
            try:
                next(self._walk)
            except StopIteration as done:
                self._older, self._walk = list(done.value or []), None
                return
    # --- end W2-left ---

    def _meta(self, path: Path, budget: Budget) -> dict | None:
        """The session_meta facts: ``sid``, ``cwd``, ``start``, ``par`` (parent thread id),
        ``ig`` (skip) and ``end`` (where the first line ends). ``None``: not there yet."""
        try:
            with open(path, "rb") as fh:
                head = fh.read(META_STEP)
                while b"\n" not in head and len(head) < META_MAX:
                    more = fh.read(min(META_HEAD, META_MAX - len(head)))
                    if not more:
                        break
                    head += more
        except OSError:
            return None
        budget.spend(len(head))
        nl = head.find(b"\n")
        line = head[:nl] if nl >= 0 else head
        doc = None
        if nl >= 0:
            try:
                doc = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                doc = None
        elif len(head) < META_MAX:
            return None  # the first line is still being written
        meta: dict[str, Any] = {"end": nl + 1 if nl >= 0 else len(head)}
        name_id = _NAME_RE.match(path.name)
        if isinstance(doc, dict):
            if doc.get("type") != "session_meta" or not isinstance(doc.get("payload"), dict):
                return None
            pl = doc["payload"]
            meta["sid"] = pl.get("id") if isinstance(pl.get("id"), str) else ""
            meta["cwd"] = folder_path(pl.get("cwd"))
            start = _when(pl.get("timestamp")) or _when(doc.get("timestamp"))
            source, thread_source = pl.get("source"), pl.get("thread_source")
            meta["par"] = _spawn_parent(source)
            meta["ig"] = thread_source == "guardian_review" or _guardian(source)
        else:  # a huge first line: what a regex finds in its head
            if not _TYPE_RE.search(line[:4096]) or b"session_meta" not in line[:4096]:
                return None
            found = _ID_RE.search(line)
            meta["sid"] = found.group(1).decode() if found else ""
            cwd = _CWD_RE.search(line)
            meta["cwd"] = folder_path(_unjson(cwd.group(1))) if cwd else ""
            ts = _TS_RE.search(line)
            start = _when(ts.group(1).decode("ascii", "replace")) if ts else None
            meta["par"], meta["ig"] = "", b"guardian" in line[:META_HEAD]
        if not meta["sid"] and name_id:
            meta["sid"] = name_id.group(1)
        if not meta["sid"]:
            return None
        meta["start"] = iso(start) if start else ""
        return meta

    # ------------------------------------------------------------ poll
    def poll(self, state: dict, now: datetime, budget: Budget | None = None) -> Batch:
        spend: Budget = budget if budget is not None else Budget(SMALL.total, SMALL.per_file)
        window = spend.per_file  # how far before its end a big file is first read
        now = now.astimezone(UTC)
        for key, kind in (("files", dict), ("sess", dict), ("db", dict), ("salt", str)):
            if not isinstance(state.get(key), kind):
                state[key] = secrets.token_hex(8) if kind is str else {}
        cursors, sess, salt = state["files"], state["sess"], state["salt"]
        # fix-PRIV, fix2-PRIV: a title an older version saved (raw, or cut by an older rule) is
        # cut once; the state remembers the rule it was cut by (the Watcher hands each poll a
        # copy, so an object's identity can't tell)
        if state.get("tc") != TITLE_RULE:
            for s in sess.values():
                if isinstance(s, dict) and isinstance(s.get("ti"), str):
                    s["ti"] = name_only(s["ti"], TITLE_LIMIT)
            state["tc"] = TITLE_RULE
        if not self.private:  # fix2-PRIV: no private folders (any more): no session is private
            for s in sess.values():
                if isinstance(s, dict) and s.pop("pv", None):
                    s.pop("db", None)  # its title is looked up again
        for s in sess.values():  # a state saved before ids came from the id's random end
            if isinstance(s, dict) and isinstance(s.get("sid"), str) and s.get("id") == \
                    _old_id(s["sid"]):
                s["id"] = thread_id(s["sid"])
        files = self._files(now, spend)
        live = {cursor_key(p) for p, _ in files}
        forget(cursors, [p for p, _ in files])
        for key in [k for k in sess if k not in live]:
            del sess[key]
        for key in [k for k in self._cwd if k not in live]:
            del self._cwd[key]

        batch = Batch()
        touched: set[str] = set()
        for path, st in files:
            if len(batch.agents) >= MAX_AGENTS or len(batch.events) >= MAX_EVENTS - ROOM - 1:
                break  # the rest goes on next poll
            key = cursor_key(path)
            s = sess.get(key)
            if isinstance(s, dict) and s.get("ig"):
                continue
            # fix2-PRIV: with private folders, a saved session's folder is looked up once a run
            # (even with nothing new), so one in a private folder drops what an older run kept
            if self.private and isinstance(s, dict) and key not in self._cwd \
                    and spend.allow() > 0:
                meta = self._meta(path, spend)
                if meta is not None:
                    self._cwd[key] = meta["cwd"]
                    self._hide(key, s)
            cur = cursors.get(key)
            if (isinstance(s, dict) and isinstance(cur, dict) and cur.get("size") == st.st_size
                    and cur.get("mtime_ns") == st.st_mtime_ns
                    and cur.get("seen", 0) >= st.st_size):
                continue  # nothing new: stat only
            if spend.allow() <= 0:
                break  # out of bytes for this tick: catch up next poll
            if not isinstance(s, dict) or key not in self._cwd:
                meta = self._meta(path, spend)
                if meta is None:
                    continue
                self._cwd[key] = meta["cwd"]
                if not isinstance(s, dict):
                    s = self._new_session(meta, sess)
                    sess[key] = s
                    if s.get("ig"):
                        continue
                    cursors.pop(key, None)
                    s["seed"] = meta["end"]  # where its log lines start
                self._hide(key, s)  # fix2-PRIV
            if key not in cursors and isinstance(s.get("seed"), int):
                if spend.allow() <= 0:
                    continue  # first read (and first change) next poll, with a new allowance
                # first sight: from its first log line when the rest fits one per-file
                # allowance (read over a tick or two), else that allowance before its end
                start_at(path, cursors, max(s.pop("seed"), st.st_size - window))
                touched.add(key)  # a new session shows even before its first turn
            if self._read(path, key, s, cursors=cursors, budget=spend, salt=salt, batch=batch):
                touched.add(key)
        _fold(sess, now)
        if self._db(state["db"], sess, now):
            touched.update(k for k, s in sess.items() if isinstance(s, dict) and s.get("dbc"))
        for key in sorted(touched):
            s = sess[key]
            s.pop("dbc", None)
            batch.agents.append(self._change(key, s, sess))
        return batch

    def _hide(self, key: str, s: dict) -> None:
        """fix2-PRIV: a session whose folder is in a private folder keeps no project name or
        title in the state (saved in ``cursors.json``); ``pv`` marks it, so a title the thread
        database gives later isn't kept either. Its record says "Codex"; the Watcher hides it
        by its ``x-folder``."""
        cwd = self._cwd.get(key)
        if not cwd:
            return
        if self.private and hidden(self.private, cwd):
            s.pop("p", None)
            s.pop("ti", None)
            s["pv"] = 1
        else:
            if s.pop("pv", None):
                s.pop("db", None)  # no longer private: its title is looked up again
            if not s.get("p"):
                s["p"] = project_folder(cwd)[:80]

    def _new_session(self, meta: dict, sess: dict) -> dict:
        sid = meta["sid"]
        aid = thread_id(sid)
        if any(o.get("id") == aid and o.get("sid") != sid for o in sess.values()
               if isinstance(o, dict)):  # the same random end: only a real duplicate
            aid = f"{AGENT}-{slug(sid, 40) or 'session'}"
        s: dict[str, Any] = {"sid": sid, "id": aid, "p": project_folder(meta["cwd"])[:80],
                             "st": None, "at": meta["start"], "start": meta["start"],
                             "par": meta["par"] or "", "fh": [], "hw": 0}
        if meta["ig"]:
            s["ig"] = 1
        return s

    def _read(self, path: Path, key: str, s: dict, *, cursors: dict, budget: Budget, salt: str,
              batch: Batch) -> bool:
        """Tail one rollout file into its session ``s``. Returns whether any line counted."""
        lost = key not in cursors  # a known session whose cursor is gone reads from the start
        res = tail(path, cursors, budget)
        if res.busy or res.missing:
            return False
        hw = int(s["hw"]) if isinstance(s.get("hw"), int) else 0
        again = bool(res.reset) or lost
        any_line = False
        for line in res.lines:
            if len(batch.events) > MAX_EVENTS - ROOM - 2:  # a line sends at most two events
                start_at(path, cursors, line.offset)  # the cap: this line and the rest next poll
                break
            facts = _facts(line)
            if facts is None:
                continue
            t, top, ptype, doc = facts
            if t is None:
                continue
            t_ms = _ms(t)
            if again and t_ms <= hw:
                continue  # read again after a rewrite: already counted
            any_line = True
            hw = max(hw, t_ms)
            if top == "turn_context" and isinstance(doc, dict):
                cwd = folder_path((doc.get("payload") or {}).get("cwd"))
                if cwd:
                    self._cwd[key] = cwd
                    s["p"] = project_folder(cwd)[:80]
                    self._hide(key, s)  # fix2-PRIV
                continue
            self._signal(s, key, top=top, ptype=ptype, t=t, batch=batch)
            new = self._files_in(line, doc, top=top, ptype=ptype, s=s, salt=salt, key=key)
            if new:
                batch.events.append(self._event(s, key, "files", t, n=new))
        s["hw"] = hw
        return any_line

    def _signal(self, s: dict, key: str, *, top: str, ptype: str, t: datetime, batch: Batch
                ) -> None:
        """One line's effect on the session's state (see the module's signal table)."""
        old = s.get("st")
        signal = SIGNALS.get(ptype) if top == "event_msg" else None
        if signal is None:
            activity = top == "response_item" or (top == "event_msg" and ptype not in NOT_ACTIVITY)
            if not activity:
                return
            if old not in (None, "waiting", "error"):  # work after an error: going again
                if old == "working":
                    s["at"] = iso(t)  # still busy: a fresh heartbeat
                return
            signal = ("working", "working")
        new, msg = signal
        if new != old or ptype in START:  # a task_started while working is a new turn too
            if new == "working":
                kind = "answer" if old == "waiting" else "start"
            else:
                kind = EVENT_OF[new]
            batch.events.append(self._event(s, key, kind, t))
        s["st"], s["msg"], s["at"] = new, msg, iso(t)

    def _files_in(self, line: Line, doc: Any, *, top: str, ptype: str, s: dict, salt: str,
                  key: str) -> int:
        """How many paths this line changes that the session hasn't changed before."""
        paths: list[str] = []
        if doc is None:  # a big line: its first changed path, by regex
            head = line.data
            if b'"FileChange"' in head or ptype in ("patch_apply_begin", "patch_apply_end"):
                found = _CHANGES_RE.search(head)
                if found:
                    paths.append(_unjson(found.group(1)))
            elif ptype in ("custom_tool_call", "function_call") and b"apply_patch" in head[:4096]:
                paths.extend(_unjson(m.group(1)) for m in _PATCH_BIG_RE.finditer(head))
        else:
            pl = doc.get("payload") if isinstance(doc.get("payload"), dict) else {}
            if top == "event_msg" and ptype == "item_completed":
                item = pl.get("item")
                if isinstance(item, dict) and item.get("type") == "FileChange":
                    paths.extend(_change_paths(item.get("changes")))
            elif top == "event_msg" and ptype in ("patch_apply_begin", "patch_apply_end"):
                paths.extend(_change_paths(pl.get("changes")))
            elif top == "response_item" and pl.get("name") == "apply_patch":
                text = pl.get("input")
                if not isinstance(text, str):
                    args = pl.get("arguments")
                    try:
                        parsed = json.loads(args) if isinstance(args, str) else args
                    except ValueError:
                        parsed = None
                    text = parsed.get("input") if isinstance(parsed, dict) else None
                if isinstance(text, str):
                    paths.extend(a or b for a, b in _PATCH_RE.findall(text))
        if not paths:
            return 0
        known = s.setdefault("fh", [])
        seen = set(known)
        new = 0
        for p in paths:
            h = _hash(salt, p, self._cwd.get(key, ""))
            if h is None or h in seen:
                continue
            seen.add(h)
            new += 1
            if len(known) < MAX_HASHES:
                known.append(h)
            else:
                s["fx"] = int(s.get("fx") or 0) + 1  # past the cap: counted, not remembered
        return new

    def _event(self, s: dict, key: str, kind: str, t: datetime, **extra: Any) -> dict:
        event = {"t": t, "id": s["id"], "k": kind, "title": s.get("ti") or s.get("p") or DISPLAY,
                 "project": s.get("p", ""), "tool": AGENT, **extra}
        cwd = self._cwd.get(key)
        if cwd:
            event["folder"] = cwd
        return event

    def _change(self, key: str, s: dict, sess: dict) -> dict:
        st = s.get("st") or "ok"  # a session with no turn yet: started, idle
        msg = s.get("msg") if s.get("st") else "new"
        change: dict[str, Any] = {
            "id": s["id"], "at": s.get("at") or None, "agent": AGENT, "kind": "task",
            "title": s.get("ti") or s.get("p") or DISPLAY, "state": st,
            "message": MESSAGES.get(str(msg), MESSAGES["working"]),
            "needs_you": list(NEED) if st == "waiting" else [],
        }
        if s.get("start"):
            change["started"] = s["start"]
        if s.get("p"):
            change["x-project"] = s["p"]
        cwd = self._cwd.get(key)
        if cwd:
            change["x-folder"] = cwd
        files = len(s.get("fh") or []) + int(s.get("fx") or 0)
        if files:
            change["x-files"] = files
        par = s.get("par")
        if par:
            parent = next((o["id"] for o in sess.values()
                           if isinstance(o, dict) and o.get("sid") == par), None)
            change["x-parent"] = parent or thread_id(par)
        return change

    # ------------------------------------------------------------ the thread database
    def _db(self, db: dict, sess: dict, now: datetime) -> bool:
        """Titles and spawn edges from ``state_5.sqlite``. ``db`` is the reader state's
        ``{"sig": [...], "at": [ms, id]}``. Returns whether any session changed (those carry
        ``dbc``)."""
        path = self.root / DB_NAME
        try:
            st = os.stat(path)
        except OSError:
            return False
        try:
            wal = os.stat(f"{path}-wal")
            wal_sig = [wal.st_mtime_ns, wal.st_size]
        except OSError:
            wal_sig = [0, 0]
        sig = [st.st_mtime_ns, st.st_size, *wal_sig]
        moved = sig != db.get("sig")
        mine = [s for s in sess.values() if isinstance(s, dict) and not s.get("ig")]
        # not found last time ("dbt": when it was tried): again once the database changed, the
        # longest-waiting first, so sessions with no row never starve the others
        todo = sorted((s for s in mine if not s.get("db") and (moved or not s.get("dbt"))),
                      key=lambda s: int(s["dbt"]) if isinstance(s.get("dbt"), int) else 0)
        if not moved and not todo:
            return False
        changed = False
        try:
            uri = path.absolute().as_uri() + "?mode=ro"
            con = _connect(uri)
        except (sqlite3.Error, ValueError, OSError):
            return False
        try:
            for s in todo[:DB_LOOKUPS]:
                found = _one(con, s["sid"])
                if found is None:
                    s["dbt"] = _ms(now)  # no row yet: again when the database changes
                    continue
                changed |= _take(s, found, con)
                s["db"] = 1
            if moved:
                by_sid = {s["sid"]: s for s in mine if s.get("sid")}
                cursor = db.get("at")
                if not (isinstance(cursor, list) and len(cursor) == 2):
                    cursor = [_ms(now - timedelta(days=self.days)), ""]
                rows = _since(con, cursor)
                for tid, title, nick, at_ms, src in rows:
                    cursor = [at_ms, tid]
                    s = by_sid.get(tid)
                    if s is not None:
                        changed |= _take(s, (title, nick, src), con)
                db["at"] = cursor
                if len(rows) < DB_ROWS:
                    db["sig"] = sig  # caught up: wait for the next change
        except sqlite3.Error:
            return changed  # locked, busy, or a schema we don't know: next poll
        finally:
            con.close()
        return changed


# ---------------------------------------------------------------- helpers
def _old_id(sid: str) -> str:
    """The id 0.2 builds before the review used (the first 12 characters: the clock)."""
    return f"{AGENT}-{slug(sid, 12)}" if slug(sid, 12) else ""


def _fold(sess: dict, now: datetime) -> None:
    """Keep the changed-file hashes bounded: a session finished :data:`FOLD_AFTER` ago, and
    past :data:`MAX_ALL_HASHES` the least recently active ones, keep their count (``fx``) and
    lose their hashes (a file changed again would count again)."""
    old = iso(now - FOLD_AFTER)
    mine = [s for s in sess.values() if isinstance(s, dict) and s.get("fh")]
    for s in mine:
        if s.get("st") not in ("working", "waiting") and str(s.get("at") or "") < old:
            _fold_one(s)
    total = sum(len(s["fh"]) for s in mine if isinstance(s.get("fh"), list))
    for s in sorted(mine, key=lambda s: str(s.get("at") or "")):
        if total <= MAX_ALL_HASHES:
            break
        total -= len(s.get("fh") or [])
        _fold_one(s)


def _fold_one(s: dict) -> None:
    fh = s.get("fh")
    s["fx"] = int(s.get("fx") or 0) + (len(fh) if isinstance(fh, list) else 0)
    s["fh"] = []


def _facts(line: Line) -> tuple[datetime | None, str, str, Any] | None:
    """``(time, record type, payload type, parsed doc or None)`` of one rollout line."""
    if line.big:
        head = line.data[:8192]
        ts = _TS_RE.search(head)
        types = [m.group(1).decode() for m in _TYPE_RE.finditer(head[:4096])][:2]
        if not types:
            return None
        top = types[0]
        ptype = types[1] if len(types) > 1 else ""
        return (_when(ts.group(1).decode("ascii", "replace")) if ts else None, top, ptype, None)
    doc = line.json()
    if not isinstance(doc, dict):
        return None
    pl = doc.get("payload")
    ptype = pl.get("type") if isinstance(pl, dict) else None
    top = doc.get("type")
    return (_when(doc.get("timestamp")), top if isinstance(top, str) else "",
            ptype if isinstance(ptype, str) else "", doc)


def _change_paths(changes: Any) -> list[str]:
    if isinstance(changes, dict):
        return [k for k in changes if isinstance(k, str)]
    if isinstance(changes, list):
        return [c["path"] for c in changes
                if isinstance(c, dict) and isinstance(c.get("path"), str)]
    return []


def _hash(salt: str, path: str, cwd: str) -> str | None:
    if not isinstance(path, str) or not path.strip():
        return None
    text = path.strip().replace("\\", "/")
    absolute = text.startswith("/") or (len(text) > 1 and text[1] == ":")
    if not absolute and cwd:
        text = cwd.replace("\\", "/").rstrip("/") + "/" + text
    text = os.path.normpath(text).replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        text = text.casefold()  # Windows: one file, any case
    return hashlib.sha256(f"{salt}\0{text}".encode("utf-8", "replace")).hexdigest()[:12]


def _spawn_parent(source: Any) -> str:
    """The parent thread id of a spawned subagent's ``source``, else ``""``."""
    if isinstance(source, str) and source.startswith("{"):
        try:
            source = json.loads(source)
        except ValueError:
            return ""
    if not isinstance(source, dict):
        return ""
    sub = source.get("subagent")
    spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
    parent = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
    return parent if isinstance(parent, str) else ""


def _guardian(source: Any) -> bool:
    if isinstance(source, str) and source.startswith("{"):
        try:
            source = json.loads(source)
        except ValueError:
            return False
    sub = source.get("subagent") if isinstance(source, dict) else None
    return isinstance(sub, dict) and sub.get("other") == "guardian"


# A thread's ``title`` is often its first prompt (or a piece of it, in another case or with its
# whitespace folded): message text, never shown. The prompt columns are compared inside the
# query by :func:`_title_or_none` (registered on the read-only connection, over their first
# 64 KB) and are never returned as rows or kept. A missing prompt column (an older schema) or
# a NULL one gives no title at all.
TITLE_FN = "lamptown_title"
TITLE_PIECE = 24  # a title whose first characters (normalised) are in the prompt is the prompt
_TITLE = (f"{TITLE_FN}(substr(title, 1, 1024), substr(first_user_message, 1, 65536),"
          " substr(preview, 1, 4096))")
_ONE = (f"SELECT {_TITLE}, agent_nickname, source FROM threads WHERE id = ?",
        "SELECT NULL, NULL, source FROM threads WHERE id = ?")
_SINCE = (f"SELECT id, {_TITLE}, agent_nickname, updated_at_ms, source FROM threads "
          "WHERE (updated_at_ms, id) > (?, ?) ORDER BY updated_at_ms, id LIMIT ?",
          "SELECT id, NULL, NULL, updated_at * 1000, source FROM threads "
          "WHERE (updated_at * 1000, id) > (?, ?) ORDER BY updated_at, id LIMIT ?")


# --- W2-left-fix: a bounded older-day search --- (sessions started before the window)
_OLD = ("SELECT rollout_path FROM threads WHERE updated_at_ms >= ? AND created_at_ms < ? "
        "ORDER BY updated_at_ms DESC LIMIT ?",
        "SELECT rollout_path FROM threads WHERE updated_at_ms >= ? "
        "ORDER BY updated_at_ms DESC LIMIT ?")


def _old_rows(con: sqlite3.Connection, since_ms: int, first_ms: int) -> list:
    """Rollout paths of threads changed since ``since_ms`` and made before ``first_ms`` (an
    older schema without ``created_at_ms``: changed since, all of them)."""
    try:
        return con.execute(_OLD[0], (since_ms, first_ms, OLD_ROWS)).fetchall()
    except sqlite3.OperationalError as exc:
        if "created_at_ms" not in str(exc):
            raise
    return con.execute(_OLD[1], (since_ms, OLD_ROWS)).fetchall()
# --- end W2-left-fix ---


def _norm_text(text: str) -> str:
    """Whitespace folded, case folded, end punctuation and quotes trimmed."""
    return " ".join(text.split()).casefold().strip(" .…'\"`")


def _title_or_none(title: Any, first: Any, preview: Any) -> str | None:
    """``title``, unless it is (a piece of) the prompt, the prompt is in it, or there is no
    prompt to compare it with (a NULL column)."""
    if not isinstance(title, str) or not isinstance(first, str) or not isinstance(preview, str):
        return None
    norm = _norm_text(title)
    if not norm:
        return None
    for prompt in (_norm_text(first), _norm_text(preview)):
        if prompt and (norm[:TITLE_PIECE] in prompt or prompt in norm):
            return None
    return title


def _connect(uri: str) -> sqlite3.Connection:
    con = sqlite3.connect(uri, uri=True, timeout=DB_TIMEOUT)
    try:
        con.create_function(TITLE_FN, 3, _title_or_none, deterministic=True)
    except sqlite3.Error:
        con.close()
        raise
    return con


def _first(con: sqlite3.Connection, queries: tuple[str, ...], args: tuple) -> list:
    """The first of ``queries`` this database's schema knows (older Codex versions lack some
    columns). A locked or busy database raises :class:`sqlite3.OperationalError`."""
    for i, sql in enumerate(queries):
        try:
            return con.execute(sql, args).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such column" not in str(exc) or i == len(queries) - 1:
                raise
    return []


def _one(con: sqlite3.Connection, sid: str) -> tuple | None:
    rows = _first(con, _ONE, (sid,))
    return tuple(rows[0]) if rows else None


def _since(con: sqlite3.Connection, cursor: list) -> list[tuple]:
    at, tid = cursor
    rows = _first(con, _SINCE, (int(at), str(tid), DB_ROWS))
    return [(r[0], r[1], r[2], int(r[3] or 0), r[4]) for r in rows if isinstance(r[0], str)]


def _take(s: dict, row: tuple, con: sqlite3.Connection) -> bool:
    """Merge one ``threads`` row (title, nickname, source) into a session; look up its spawn
    edge when it has no parent yet. Returns whether the session changed (marks ``dbc``)."""
    title, nick, source = row
    before = (s.get("ti"), s.get("par"))
    name = title if isinstance(title, str) and title.strip() else nick
    if isinstance(name, str) and name.strip() and not s.get("pv"):  # fix2-PRIV: not private
        s["ti"] = name_only(name, TITLE_LIMIT)  # fix-PRIV: saved, so never a path or address
    if not s.get("par"):
        s["par"] = _spawn_parent(source) or _edge(con, s["sid"])
    if (s.get("ti"), s.get("par")) != before:
        s["dbc"] = 1
        return True
    return False


def _edge(con: sqlite3.Connection, sid: str) -> str:
    try:
        row = con.execute("SELECT parent_thread_id FROM thread_spawn_edges "
                          "WHERE child_thread_id = ?", (sid,)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return ""
        raise
    return row[0] if row and isinstance(row[0], str) else ""
