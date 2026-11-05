"""The Watcher: runs the readers, keeps the status folder and the event log up to date.

One :meth:`Watcher.tick` (the app calls it every few seconds; ``lamptown watch`` too):

0. **One Watcher per folder.** The first tick takes an exclusive lock on
   ``<status_dir>/.lamptown/watch.lock`` and holds it until :meth:`Watcher.close` (or the
   process ends). Another Watcher on the same folder stands by: its ticks do nothing and say
   so (:attr:`TickResult.busy`), and it takes over, from the saved state, once the lock is free.
1. **Readers.** Each reader's ``poll(state, now, budget)`` gets its own saved state back and
   the tick's shared byte :class:`~lamptown.status.readers.tail.Budget`. Its status changes are
   written through the store (``update_status``), but only when something changed (or, for an
   unchanged record, once a ``heartbeat`` has passed, so a live agent doesn't read as stopped).
   A record written by someone else (a hook) at or after the change's ``at`` keeps its state
   and names: only counters (the larger count wins) and the ``x-`` fields hooks never write
   are merged in. That decision is checked against the record read again right before the
   write (a hook that wrote in between is decided against again), and the whole record is
   written at once. That last read and the write hold the record's short write lock
   (``store.record_lock``, which hooks take too), so no hook lands between them. A reader
   that raises is reported in :attr:`TickResult.errors` and its state is rolled back; the
   others still run.
2. **Folder diff.** The whole status folder is compared with the last tick's snapshot, so every
   state change becomes an event, whoever wrote it: a reader, a hook or a ``status run``
   wrapper. Unchanged files are only ``stat``-ed. Time-based states count as well: a
   ``working`` record that went silent turns into ``fail`` (stopped responding) or ``quiet``
   events, with the same rules and defaults as the ``status_dir`` collector.
3. **Events.** Reader events and folder events go to ``events.append`` (one log line each).
   ``files`` events are grouped to at most one per agent per minute (a late one joins the next
   minute's group). A reader event and a folder event of the same kind for the same agent in
   one tick count once (the reader's, which carries its real time). A ``push`` or ``ci``
   result one source logged is not logged again from the other source within
   :data:`RECENT_WINDOW` seconds, in a later tick too (the reader sees a push in the session
   log; the record's ``x-pushed`` shows it a few seconds later).

The very first tick (no saved snapshot) only takes the snapshot: records already in the
folder don't replay as events. Readers catch up on history by sending events with their own
times.

Saved state: ``<status_dir>/.lamptown/cursors.json`` (readers' cursors, the folder snapshot,
``files`` groups still open, the projects seen, a digest of each record this Watcher wrote and
the recent pushes and CI results under hashed keys), written atomically right after every tick
that added lines to the event log, wrote a status record or changed an open ``files`` group
(a failed save there is tried again every tick until it works), else when it changed at most
every :data:`SAVE_EVERY` seconds, and on :meth:`Watcher.close`. So a crash loses only ticks
that logged and wrote nothing (they replay and log what they would have), plus, in the
worst case, the one tick it hit between its log write and its save: a replayed event with the
same time is skipped, so only an event whose record was rewritten again in that moment could
come twice. If the event log can't be written, the tick's state is rolled back, so its events
come again next tick. It holds names, ids, counts, times and digests; never a folder path,
message text or file contents (readers key files by ``tail.cursor_key``, a hash). Records past
``expire_after`` keep only a small entry. A tick cut short (Ctrl+C, any exception) is rolled
back to the last finished tick, so :meth:`Watcher.close` never saves a half-done tick.

Tidying: a record this Watcher wrote itself (the file still holds exactly what it wrote) is
removed once its ``updated`` is older than ``expire_after`` plus ``keep_days`` (hidden that long,
and its events are out of the history), so the folder and the saved state stay bounded. Files
someone else wrote (hooks, ``status run``) or changed afterwards are never removed. Off with
``remove_own=False`` or ``expire_after`` 0/``None``. No tidying on a run's first tick, nor on
a tick more than :data:`TIDY_GAP` after the last finished one (a clock that jumped ahead must
not remove a fresh record). A record that is there but can't be read for a moment is never
rewritten with only the change: the change waits for the next tick (and :meth:`Watcher.close`
applies what still waits, so none is lost).

Plots: :func:`default_plot` is a stub (``x-plot``, else ``tower`` for jobs, else
``boathouse``); pass ``plot_for`` to map a record's folder to a building (L3 does).
Private: with ``private_dirs``, events of records whose ``x-folder`` (or the status folder
itself) is in or under one of them are written as "Private agent", with no project and the
collector's masked id (``private-`` + a hash of the status folder and the id), so private names
never reach the log and L3 can link a masked live record to its history. A reader event with
no status record and no ``folder`` can't be checked, so with ``private_dirs`` set it is masked.
Readers that keep private folders of their own (``git``, ``ci_gh``) get these too
(:func:`share_private`). The saved state keeps a hash of these rules: when they change (a folder
made private, or public again), every record is read again, and the projects seen and the open
``files`` counts of a record that is private now lose its names. The event log is rewritten
too: the lines of a record that is private now are masked in place, and when a rule hides a
folder it didn't before, so are the lines of agents with no record (:meth:`Watcher._remask_log`).
"""

from __future__ import annotations

import contextlib
import copy
import errno
import hashlib
import inspect
import json
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from lamptown.status import events as event_log
from lamptown.status.collector import (
    DEFAULT_EXPIRE_AFTER,
    DEFAULT_QUIET_AFTER,
    PRIVATE_NAME,
    _private_roots,
    _under,
)
from lamptown.status.events import clean_event, name_only, scrub  # L3-fix: project_of instead
from lamptown.status.readers import (
    COUNTERS,
    FILL_ONLY,
    GUARDED,
    MAX_AGENTS,
    MAX_EVENTS,
    Batch,
    Reader,
    clean_agent,
)
from lamptown.status.readers.tail import Budget
from lamptown.status.schema import parse_time, validate
from lamptown.status.store import (
    DEFAULT_STALE_AFTER,
    SPEC,
    StatusBusy,
    StatusError,
    atomic_write_bytes,
    atomic_write_json,
    default_dir,
    drop_stale_lock,
    iso,
    read_existing,
    read_status,
    record_lock,
    safe_reason,
    status_path,
    utcnow,
)

STATE_DIR = ".lamptown"
STATE_FILE = "cursors.json"
LOCK_FILE = "watch.lock"
STATE_VERSION = 1
STATE_MAX_BYTES = 4 * 1024 * 1024


def private_marks(roots: Any) -> list[str]:
    """A hash of each private folder (no path on disk): the Watcher saves them (``prs``) once
    its log is masked for them, so a build can tell a folder it hasn't masked for yet."""
    return sorted({hashlib.sha256(r.encode("utf-8")).hexdigest()[:16] for r in roots})


def remask_pending(status_dir: str | os.PathLike, roots: Any) -> bool:
    """Is a private folder in ``roots`` one the Watcher's saved state hasn't masked the log
    for (a folder just made private since its last tick)? No saved state: yes when the event
    log has lines (the state was deleted; the Watcher's next first tick masks them, R3b 3),
    else no (it hasn't run, so it logged nothing). A state that can't be read, or one from
    before 0.2.1 (no ``prs``): yes."""
    marks = set(private_marks(roots))
    if not marks:
        return False
    path = Path(status_dir) / STATE_DIR / STATE_FILE
    try:
        if path.stat().st_size > STATE_MAX_BYTES:
            return True
        saved = json.loads(path.read_text(encoding="utf-8")).get("prs")
    except FileNotFoundError:
        return _has_log(Path(status_dir))  # review-fix R3b 3
    except (OSError, ValueError, AttributeError):
        return True
    return not isinstance(saved, list) or not marks <= set(saved)


# --- review-fix R3b 1, 2, 3 ---
def _has_log(status_dir: Path) -> bool:
    """Does the event log hold a day file with anything in it? (Unreadable: yes.)"""
    try:
        for entry in os.scandir(event_log.events_dir(status_dir)):
            if event_log.DAY_RE.match(entry.name) and entry.stat().st_size > 0:
                return True
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return False


# The tool words a private agent may show: the ones Lamptown's own adapters and readers write
# (and a few other well-known agents). A private record's ``agent`` is free text (``status
# set --agent``, scripts), so anything else shows as no tool ("agent" on the page).
TOOL_WORDS = frozenset({"claude-code", "claude", "codex", "cursor", "gemini-cli", "gemini",
                        "copilot", "aider", "git", "ci"})


def private_tool(value: Any) -> str:
    """A private agent's tool as shown and logged: a :data:`TOOL_WORDS` word, else ``""``."""
    word = name_only(value, event_log.LIMITS["tool"]).casefold().replace(" ", "-")
    word = word.replace("_", "-")
    return word if word in TOOL_WORDS else ""


MAX_PLACES = 2000  # folders looked at by repo_places, at most


def repo_places(folders: Iterable[Any], private: list[str] | None = None) -> dict[str, str]:
    """The ids a ``git`` or ``ci_gh`` reader gives the repositories of ``folders`` (the readers'
    ``repos``, the records' ``x-folder``), each with its worktree folder: a logged line of such
    an id with no record is then placed and checked against the private folders, not masked
    blindly. In memory only (never saved: these are paths)."""
    from lamptown.status.readers import ci_gh, git  # late: the readers import this package

    out: dict[str, str] = {}
    for n, folder in enumerate(folders):
        if n >= MAX_PLACES:
            break
        path = git._expand(folder)
        if not path:
            continue
        repo = git.find_repo(path, private=private)
        for root in dict.fromkeys((path, repo.root if repo else path)):
            for make in (git._id_for, ci_gh._id_for):
                out.setdefault(make(root), root)
    return out


def reader_repos(readers: Iterable[Any]) -> list[str]:
    """The ``repos`` of the ``git`` and ``ci_gh`` readers among ``readers``."""
    out: list[str] = []
    for reader in readers:
        try:
            name, repos = getattr(reader, "name", None), getattr(reader, "repos", None)
        except Exception:
            continue
        if name in ("git", "ci_gh") and isinstance(repos, list):
            out.extend(str(r) for r in repos if isinstance(r, str | os.PathLike))
    return out


def unplaced(sid: str, known: set[str], places: Mapping[str, str],
             private: Callable[[str], bool]) -> bool:
    """With a folder newly private: may the logged agent ``sid`` (no record of it in ``known``)
    have run there? Yes unless ``places`` tells its repository and that one isn't private. The
    Watcher's log rewrite and the build's history mask both ask this, so they agree."""
    if sid in known:
        return False
    root = places.get(sid)
    return root is None or private(root)
# --- end review-fix R3b ---


HEARTBEAT = 60  # seconds: an unchanged record is rewritten at most this often
SAVE_EVERY = 20  # seconds: the saved state is written at most this often while things change
MAX_PROJECTS = 2000
RECENT_WINDOW = 120  # seconds: a push or CI result from a reader and a record counts once
MAX_RECENT = 1000  # pushes and CI results remembered for that, at most
STATE_EVENT = {"waiting": "wait", "ok": "done", "error": "fail", "quiet": "quiet"}
# --- release-fix: ENG (engine 2, engine 4) ---
HOLD_TICKS = 5  # a change for a record that can't be read is tried again this many ticks
MAX_TIDY = 200  # own expired records looked at for removal per tick, at most
# --- end release-fix: ENG ---
RACE_TRIES = 3  # review-fix: B2 3 (a record rewritten while the Watcher decides it, at most)
# --- fix2: ENG 4 --- no tidying on a tick this long after the last finished one (or on the
# first tick of a run): the clock may have jumped, and a jump must never remove a fresh record
TIDY_GAP = timedelta(hours=1)
# --- end fix2 ---
BUSY = "another Lamptown watcher is running on this status folder"
# what a lock call says when someone else holds the lock (msvcrt: EACCES or EDEADLOCK; flock:
# EWOULDBLOCK). Anything else (ENOLCK, ENOTSUP on a network folder) is a real error.
_TAKEN = frozenset(n for n in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES,
                               getattr(errno, "EDEADLK", None), getattr(errno, "EDEADLOCK", None))
                   if n is not None)

PlotFor = Callable[[Mapping[str, Any]], str]


def default_plot(doc: Mapping[str, Any]) -> str:
    """Which building a record's figure walks to. A stub until the folder mapping (L3)."""
    plot = doc.get("x-plot")
    if isinstance(plot, str) and plot:
        return plot
    return "tower" if doc.get("kind") == "job" else "boathouse"


@dataclass
class TickResult:
    written: list[str] = field(default_factory=list)  # status ids the Watcher wrote
    kept: list[str] = field(default_factory=list)  # changes held back: the file was newer
    events: list[dict] = field(default_factory=list)  # events written to the log
    errors: list[str] = field(default_factory=list)  # short, safe to show
    busy: bool = False  # another Watcher holds this folder: nothing was done
    # --- L4-fix: catch-up --- a reader is still catching up (history left to read): tick
    # again soon (1 s instead of 3 s); the next tick reads with Budget.catch_up()
    behind: bool = False
    # --- end L4-fix ---
    # --- release-fix: ENG (engine 4) --- changes put off: the record couldn't be read just
    # now, so it isn't rewritten with only the change; they are tried again next tick
    retry: list[str] = field(default_factory=list)
    # --- end release-fix: ENG ---

    @property
    def changed(self) -> bool:
        return bool(self.written or self.events)


def _fresh_state() -> dict:
    return {"v": STATE_VERSION, "readers": {}, "folder": None, "pending": {}, "flushed": {},
            "projects": [], "pruned": "", "wrote": {}, "recent": {}, "pr": "", "prs": []}


def _scrubbed(value: Any) -> Any:
    """A JSON value with every lone surrogate in its text (keys too) replaced by ``?``."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {scrub(str(k)): _scrubbed(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrubbed(v) for v in value]
    return value


def _aware(moment: datetime) -> datetime:
    moment = moment if moment.tzinfo is not None else moment.astimezone()
    return moment.astimezone(UTC).replace(microsecond=0)


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _flag(value: Any) -> Any:
    """``x-pushed``: a bool today; a short count or stamp is kept too, so any change shows."""
    if isinstance(value, bool | int):
        return value
    if isinstance(value, str) and len(value) <= 40:
        return scrub(value)
    return None


def _digest(doc: Mapping[str, Any]) -> str:
    text = json.dumps(doc, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("ascii")).hexdigest()


# --- review-fix: B2 3 ---
def _compose(sid: str, body: Mapping[str, Any], when: datetime) -> dict:
    """The whole record to write: ``body`` under the head ``update_status`` writes (``spec``,
    ``id``, ``title`` (the id when none), ``state`` (``working`` when none), ``updated``)."""
    head = {"spec": SPEC, "id": sid, "title": body.get("title", sid),
            "state": body.get("state", "working"), "updated": iso(when)}
    return {**head, **{k: v for k, v in body.items() if k not in head}}
# --- end review-fix: B2 3 ---


def _takes_budget(poll: Callable) -> bool:
    try:
        params = inspect.signature(poll).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "budget" or p.kind is p.VAR_KEYWORD for p in params)


# --- pr1-review: one saved state per configured reader ---
def _state_keys(readers: Sequence[Reader]) -> tuple[dict[int, str], set[int]]:
    """Where each reader's state is saved (``cursors.json`` ``readers``), by ``id(reader)``,
    and the readers that may take over the state saved under their bare name.

    A name only one reader has keeps its name, as before. A reader with a ``state_tag`` (a
    string the live town gives each entry of a kind listed more than once, e.g. two Codex
    roots, from its config entry) gets ``name~<hash of the tag>``, even when its twin failed to
    start; readers sharing a name with no tag get ``name~<hash of their place>``. No path is
    saved, reordering tagged entries keeps each one's state, and a ``~`` is in no reader name,
    so no key is another's name. The first of each such name adopts the bare name's state
    (saved when it was the only one): adding a second entry neither re-reads the first's logs
    nor logs its starts again."""
    count: dict[str, int] = {}
    for reader in readers:
        count[reader.name] = count.get(reader.name, 0) + 1
    keys: dict[int, str] = {}
    adopt: set[int] = set()
    seen: dict[str, int] = {}
    for reader in readers:
        name = reader.name
        tag = getattr(reader, "state_tag", None)
        tag = tag if isinstance(tag, str) and tag else None
        if tag is None and count[name] == 1:
            keys[id(reader)] = name
            continue
        place = seen[name] = seen.get(name, 0) + 1
        mark = tag or f"#{place}"
        key = f"{name}~{hashlib.sha256(mark.encode('utf-8')).hexdigest()[:12]}"
        while key in keys.values():  # two entries alike: still two states
            key += f"~{place}"
        keys[id(reader)] = key
        if place == 1:
            adopt.add(id(reader))
    return keys, adopt
# --- end pr1-review ---


def state_kind(old: str | None, new: str) -> str | None:
    """The event for a change of (effective) state, or ``None``."""
    if new == "working":
        return "answer" if old == "waiting" else "start"
    return STATE_EVENT.get(new)


# --- live: L11 ci_gh ---
def reader_notes(reader: Any) -> list[str]:
    """A reader's ``notes`` (optional attribute) from its last poll, as ``TickResult.errors``
    lines: at most 10, each cut to 200 characters."""
    try:  # fix2: ENG 5 (a broken property is that reader's problem, not the tick's)
        notes = getattr(reader, "notes", None)
    except Exception:
        return []
    if not isinstance(notes, list | tuple):
        return []
    name = getattr(reader, "name", "?")
    return [f"reader {name}: {n}"[:200] for n in notes[:10] if isinstance(n, str) and n]
# --- end L11 ---


# --- fix2: PRIV 5 ---
def share_private(reader: Any, roots: list[str]) -> None:
    """Give a reader that keeps private folders (``private``, a list: git, ci_gh) the Watcher's
    too, and drop what they hide from its ``repos`` and ``status_dir``, as its own
    ``private_dirs`` would have. So a Watcher built in Python covers its readers as well."""
    try:
        mine = getattr(reader, "private", None)
    except Exception:
        return
    if not roots or not isinstance(mine, list):
        return
    both = [*mine, *(r for r in roots if r not in mine)]
    reader.private = both

    def hid(path: Any) -> bool:
        return isinstance(path, str | os.PathLike) and bool(str(path)) and _under(str(path), both)

    repos = getattr(reader, "repos", None)
    if isinstance(repos, list):
        reader.repos = [p for p in repos if not hid(p)]
    if hid(getattr(reader, "status_dir", None)):
        reader.status_dir = None
# --- end fix2 ---


# ---------------------------------------------------------------- the folder lock
def _lock_fd(fd: int) -> None:
    """Take an exclusive, non-blocking lock on ``fd`` (raises ``OSError`` when it's taken).
    The OS drops it when the process ends, so a crash never leaves a stale lock."""
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        with contextlib.suppress(OSError):
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    os.close(fd)


class Watcher:
    """Runs readers and turns status changes into events. See the module docstring.

    Call :meth:`close` when done (or use it as a context manager): it saves the state and
    lets another Watcher take the folder over."""

    def __init__(
        self,
        status_dir: str | os.PathLike | None = None,
        readers: Sequence[Reader] = (),
        *,
        plot_for: PlotFor = default_plot,
        private_dirs: Iterable[str | os.PathLike] = (),
        config_dir: str | os.PathLike | None = None,
        stale_after: int | None = DEFAULT_STALE_AFTER,
        quiet_after: int | None = DEFAULT_QUIET_AFTER,
        expire_after: int | None = DEFAULT_EXPIRE_AFTER,
        keep_days: int = 7,
        heartbeat: int = HEARTBEAT,
        tz: tzinfo | None = None,
        project_for: Callable[[Any, Any], str] | None = None,  # ab: [[live.group]]
        remove_own: bool = True,  # release-fix: ENG (engine 2)
    ) -> None:
        self.dir = default_dir(status_dir)
        self.readers = list(readers)
        self.plot_for = plot_for
        # ab: (folder, project) -> a fixed project name ("" for none): livetown.GroupMap
        self.project_for = project_for
        self.private_roots = _private_roots({"private_dirs": [str(p) for p in private_dirs],
                                             "_config_dir": str(config_dir or ".")})
        self.stale_after = stale_after
        self.quiet_after = quiet_after
        self.expire_after = expire_after
        self.keep_days = keep_days
        self.heartbeat = heartbeat
        self.tz = tz
        self.state_path = self.dir / STATE_DIR / STATE_FILE
        self.lock_path = self.dir / STATE_DIR / LOCK_FILE
        self._fd: int | None = None
        self._state: dict | None = None
        self._saved = ""  # the state as last written to disk (JSON text)
        self._saved_at: datetime | None = None
        self._good = ""  # the state after the last good tick: what a failed tick rolls back to
        self._since: str | None = None  # events up to this time may be replays: check the log
        self._seen: dict[date, set] = {}  # event keys already in each day file (while replaying)
        self._wrote: dict[str, str] = {}  # status id -> digest of the record this Watcher wrote
        self._budget_kw: dict[int, bool] = {}
        self._unsaved = False  # a save that had to happen failed: try again every tick
        self._behind = False  # --- L4-fix: catch-up --- (a reader said so last tick)
        self._turn = 0  # --- L4-fix: catch-up --- (which reader goes first on such a tick)
        # --- release-fix: ENG (engine 2, engine 4) ---
        # a record this Watcher wrote is removed once its last update is older than its expiry
        # plus the history window (it has been hidden that long; its events are past the book)
        self.remove_after = (expire_after + keep_days * 86400
                             if remove_own and expire_after else None)
        self._held: dict[str, tuple[int, dict]] = {}  # status id -> (tries, change) put off
        self._tidied = 0  # records looked at for removal this tick
        # --- end release-fix: ENG ---
        # --- fix2: ENG 4, PRIV 5 ---
        self._last_tick: datetime | None = None  # the last finished tick's time (this run)
        self._tidy_ok = False  # this tick may remove own records (the clock looks sane)
        for reader in self.readers:
            share_private(reader, self.private_roots)
        # --- end fix2 ---
        self._keys, self._adopt = _state_keys(self.readers)  # pr1-review: one state each
        # --- review-fix: B2 1 --- a hash of the privacy rules (no path on disk): the saved
        # snapshot was made under these; other rules rebuild it from the files
        self._rules = hashlib.sha256(
            json.dumps(sorted(self.private_roots)).encode("utf-8")).hexdigest()[:16]
        # --- end review-fix: B2 1 ---
        # --- review-fix R2b 1 --- a hash of each private folder (no path on disk either): a
        # rule change that adds one masks what the log can't check (see _remask_log)
        self._marks = private_marks(self.private_roots)
        self._remask: tuple[set[str], bool, set[str]] | None = None
        # --- end review-fix R2b 1 ---

    # ------------------------------------------------------------ lock
    def _lock(self) -> str | None:
        """Hold the folder's lock. Returns ``None`` when held, else why not."""
        if self._fd is not None:
            return None
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0),
                         0o600)
        except OSError as exc:
            return f"watcher lock: {exc.strerror or type(exc).__name__}"
        try:
            _lock_fd(fd)
        except OSError as exc:
            os.close(fd)
            if exc.errno in _TAKEN:
                return BUSY
            return f"watcher lock: {exc.strerror or type(exc).__name__}"
        self._fd = fd
        # whoever held the folder before may have moved the saved state on: start from disk
        self._state, self._saved, self._saved_at, self._good = None, "", None, ""
        self._since, self._seen, self._wrote, self._unsaved = None, {}, {}, False
        return None

    def _unlock(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            _unlock_fd(fd)

    def close(self) -> None:
        """Save the state (if this Watcher holds the folder) and let the folder go. Only the
        state after the last finished tick is saved: a tick cut short (Ctrl+C) never is."""
        if self._fd is None:
            return
        try:
            # --- release-fix: ENG (engine 1) ---
            if self._good:
                self._state = json.loads(self._good)
            # --- end release-fix: ENG ---
            self._flush_held()  # fix2: ENG 2
            if self._state is not None:
                self.save()
        finally:
            self._unlock()

    # --- fix2: ENG 2 ---
    def _flush_held(self) -> None:
        """Closing with changes still put off (their record couldn't be read): apply each once
        more, forced (a still unreadable file is replaced, as after :data:`HOLD_TICKS`), so
        none is lost: the reader's cursor is already past it. Their events come from the next
        run's folder diff. A failure here is dropped quietly: closing must not fail."""
        if not self._held:
            return
        result = TickResult()
        now = max(self._last_tick or utcnow(), utcnow())  # never before this run's ticks
        for sid, (_tries, change) in list(self._held.items()):
            with contextlib.suppress(Exception):
                self._apply(dict(change), now, result, tries=HOLD_TICKS)
            self._held.pop(sid, None)
        self._held.clear()
        if self._state is not None:
            self._state["wrote"] = dict(sorted(self._wrote.items()))  # a restart knows them
    # --- end fix2 ---

    def __enter__(self) -> Watcher:
        return self

    def __exit__(self, kind: object, *exc: object) -> None:
        # review-fix: B2 6 --- the with-body's own error comes first: a failed save in close()
        # (the folder is still let go) must not replace it; on a clean exit it is raised
        if kind is None:
            self.close()
            return
        with contextlib.suppress(Exception):
            self.close()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self._unlock()

    # ------------------------------------------------------------ saved state
    def state(self) -> dict:
        if self._state is None:
            self._state = self._load()
        return self._state

    def _load(self) -> dict:
        try:
            raw = self.state_path.read_text(encoding="utf-8")
            doc = json.loads(raw)
        except (OSError, UnicodeDecodeError, ValueError):
            return _fresh_state()
        if not isinstance(doc, dict) or doc.get("v") != STATE_VERSION:
            return _fresh_state()
        state = _fresh_state()
        for key, kind in (("readers", dict), ("folder", dict), ("pending", dict),
                          ("flushed", dict), ("projects", list), ("pruned", str),
                          ("wrote", dict), ("recent", dict), ("pr", str), ("prs", list)):
            if isinstance(doc.get(key), kind):
                state[key] = doc[key]
        state["wrote"] = {k: v for k, v in state["wrote"].items()
                          if isinstance(k, str) and isinstance(v, str)}
        state["recent"] = {k: v for k, v in state["recent"].items()
                           if isinstance(v, dict) and isinstance(v.get("t"), str)
                           and v.get("s") in ("r", "f")}
        self._wrote = dict(state["wrote"])  # a restart still knows which records were its own
        self._saved = json.dumps(state, sort_keys=True)
        return state

    def save(self) -> bool:
        """Write the saved state now if it changed. Returns whether it wrote."""
        if self._fd is None:
            return False  # only the Watcher holding the folder writes its state
        text = json.dumps(self.state(), sort_keys=True)
        if text == self._saved:
            return False
        atomic_write_json(self.state_path, self.state(), max_bytes=STATE_MAX_BYTES)
        self._saved, self._unsaved = text, False
        return True

    def _save_tick(self, text: str, now: datetime, result: TickResult, *,
                   urgent: bool = False) -> None:
        """Save the state after a tick. ``urgent`` (the tick logged lines, wrote a record or
        changed an open ``files`` group, so a crash before the next save would change what is
        logged): now; else at most every :data:`SAVE_EVERY` seconds. An urgent save that fails
        is tried again every tick until one works."""
        if text == self._saved:
            self._unsaved = False
            return
        urgent = urgent or self._unsaved
        last = self._saved_at
        if not urgent and last is not None and timedelta(0) <= now - last < timedelta(
                seconds=SAVE_EVERY):
            # nothing a crash could change: the lost state replays into the same events
            return
        try:
            atomic_write_json(self.state_path, self.state(), max_bytes=STATE_MAX_BYTES)
        except (OSError, ValueError) as exc:
            result.errors.append(f"watcher state: {safe_reason(exc)}"[:200])  # release-fix: ENG
            self._unsaved = urgent
            return
        self._saved, self._saved_at, self._unsaved = text, now, False

    def _rollback(self, now: datetime) -> None:
        """A tick failed half-way: forget what it did, so it all comes again next tick."""
        self._state = json.loads(self._good) if self._good else None
        self._since = max(self._since or "", iso(now))  # part of it may be in the log
        self._seen = {}

    # ------------------------------------------------------------ tick
    def tick(self, now: datetime | None = None) -> TickResult:
        now = _aware(now or utcnow())
        result = TickResult()
        why = self._lock()
        if why is not None:
            result.busy = why == BUSY
            result.errors.append(why)
            return result
        # --- release-fix: ENG (engine 1) --- Ctrl+C (or any exception) half-way: the readers'
        # cursors and the folder snapshot have moved on, but their events aren't logged. Roll
        # back to the last finished tick, so it all comes again (and close() saves only that).
        try:
            return self._tick(now, result)
        except BaseException:
            self._rollback(now)
            raise
        # --- end release-fix: ENG ---

    def _tick(self, now: datetime, result: TickResult) -> TickResult:
        state = self.state()
        if not self._good:
            self._good = json.dumps(state, sort_keys=True)
        if self._since is None:
            self._since = iso(now)  # a run before this one may have logged events up to now
        # --- fix2: ENG 4 --- tidy only when this tick follows a finished one within the hour
        last = self._last_tick
        self._tidy_ok = last is not None and now - last <= TIDY_GAP
        # --- end fix2 ---
        if state.get("folder") is None:  # first run: what is already there is no news
            self._diff(state, now, result)
        # --- L4-fix: catch-up --- a reader behind: a big budget with a 200 ms reading
        # deadline, and the readers take turns going first (none starves the others)
        budget = Budget.catch_up() if self._behind else Budget()
        readers = list(self.readers)
        if self._behind and len(readers) > 1:
            self._turn = (self._turn + 1) % len(readers)
            readers = readers[self._turn:] + readers[:self._turn]
        # --- end L4-fix ---
        # --- release-fix: ENG (engine 4) --- changes put off last tick (record unreadable) first
        for sid, entry in list(self._held.items()):
            tries, change = entry
            self._apply(dict(change), now, result, tries=tries + 1)
            # fix2: ENG 2 --- it leaves the queue only once applied (or put off again, which
            # replaced it): a Ctrl+C inside _apply keeps it for close()
            if self._held.get(sid) is entry:
                del self._held[sid]
        # --- end release-fix: ENG ---
        reader_events: list[Mapping[str, Any]] = []
        behind = False  # --- L4-fix: catch-up ---
        for reader in readers:
            polled = self._poll(reader, state, now, result, budget)
            result.errors.extend(reader_notes(reader))  # --- live: L11 ci_gh ---
            if polled is None:
                continue
            agents, found, late = polled  # fix2: ENG 5 (read inside _poll's try)
            behind = behind or late
            if len(agents) > MAX_AGENTS:
                result.errors.append(f"reader {reader.name}: {len(agents)} status changes in "
                                     f"one poll, only {MAX_AGENTS} taken")
            if len(found) > MAX_EVENTS:
                result.errors.append(f"reader {reader.name}: {len(found)} events in one poll, "
                                     f"only {MAX_EVENTS} taken")
            for raw in agents[:MAX_AGENTS]:
                change = clean_agent(raw) if isinstance(raw, Mapping) else None
                if change is not None:
                    self._apply(change, now, result)
            reader_events.extend(e for e in found[:MAX_EVENTS] if isinstance(e, Mapping))
        # --- L4-fix: catch-up ---
        result.behind = self._behind = behind
        # --- end L4-fix ---

        folder_events = self._diff(state, now, result)
        # --- review-fix R2b 1 --- the privacy rules changed: the log's lines of a record that
        # is private now are masked on disk, before anything else is logged. A failed rewrite
        # rolls the tick back (the rule change is seen again next tick)
        remask, self._remask = self._remask, None
        if remask is not None:
            try:
                self._remask_log(*remask)
            except OSError as exc:
                result.errors.append(f"event log: {safe_reason(exc)}"[:200])
                self._rollback(now)
                return result
        # --- end review-fix R2b 1 ---
        snapshot = state["folder"] or {}
        mine = [e for e in (self._enrich(raw, snapshot, now) for raw in reader_events) if e]
        claimed = {(e["id"], e["k"]) for e in mine}
        found = [e for e in folder_events if (e["id"], e["k"]) not in claimed]
        mine, found = self._once(mine, "r", state, now), self._once(found, "f", state, now)
        open_before = json.dumps(state["pending"], sort_keys=True)
        out = self._group(mine + found, state, now)
        regrouped = json.dumps(state["pending"], sort_keys=True) != open_before
        out.sort(key=lambda e: e["t"])
        if out:
            replay = any(e["t"] <= (self._since or "") for e in out)
            try:
                result.events = event_log.append(
                    self.dir, out, tz=self.tz, keep_days=self.keep_days, now=now,
                    seen=self._seen if replay else None)
            except (OSError, ValueError) as exc:
                result.errors.append(f"event log: {safe_reason(exc)}"[:200])  # release-fix: ENG
                self._rollback(now)
                return result
        known = state["folder"] or {}
        self._wrote = {sid: d for sid, d in self._wrote.items()
                       if sid in known or sid in result.written}
        state["wrote"] = dict(sorted(self._wrote.items()))
        self._prune(state, now, result)
        self._good = json.dumps(state, sort_keys=True)
        # save now when a crash would change the log: logged lines would come again with new
        # times (W1), a record this Watcher wrote would read as someone else's (W7), and an
        # open files group would be counted again from the readers' and the folder's replays
        self._save_tick(self._good, now, result,
                        urgent=bool(result.events or result.written) or regrouped)
        self._last_tick = now  # fix2: ENG 4
        return result

    def _poll(self, reader: Reader, state: dict, now: datetime, result: TickResult,
              budget: Budget) -> tuple[list, list, bool] | None:
        """One reader's poll: its status changes, its events and whether it is still behind.
        ``None`` when it failed (reported, its state rolled back)."""
        name = reader.name
        # --- pr1-review: two entries of one kind each keep their own state (see _state_keys) ---
        key = self._keys.get(id(reader), name)
        if key not in state["readers"] and id(reader) in self._adopt:
            # the first of a name now shared takes the state saved when it was the only one
            saved = state["readers"].pop(name, None)
        else:
            saved = state["readers"].get(key)
        own = copy.deepcopy(saved) if isinstance(saved, dict) else {}
        state["readers"][key] = own
        # --- end pr1-review ---
        try:
            takes = self._budget_kw.get(id(reader))
            if takes is None:
                takes = self._budget_kw[id(reader)] = _takes_budget(reader.poll)
            batch = reader.poll(own, now, budget=budget) if takes else reader.poll(own, now)
            if not isinstance(batch, Batch):
                raise TypeError("poll() must return a Batch")
            json.dumps(own)  # the state must stay JSON
            # fix2: ENG 5 --- reader data read here, so a reader whose batch or `behind`
            # raises fails alone (before, the whole tick rolled back, every tick)
            agents, found = list(batch.agents), list(batch.events)
            behind = getattr(reader, "behind", False) is True
        except Exception as exc:  # one broken reader must not stop the town
            state["readers"][key] = saved if isinstance(saved, dict) else {}  # pr1-review
            result.errors.append(f"reader {name}: {safe_reason(exc, typed=True)}"[:200])  # ENG
            return None
        return agents, found, behind

    # ------------------------------------------------------------ status changes
    def _apply(self, change: dict, now: datetime, result: TickResult, tries: int = 0) -> None:
        # --- release-fix: ENG (engine 4) --- a record that is there but can't be read now (a
        # hook replacing it) is never rewritten with only the change: the change waits for the
        # next tick, up to HOLD_TICKS times; then a still broken file is replaced, as before
        orig = dict(change)
        sid = change.pop("id")
        at = min(change.pop("at") or now, now)
        path = status_path(sid, self.dir)
        force = tries >= HOLD_TICKS
        try:
            doc = read_existing(path)
        except StatusBusy:
            if not force:
                self._hold(orig, tries, result)
                return
            doc = None
        # --- end release-fix: ENG ---
        # --- review-fix: B2 3 (hook race) --- the decision holds only for the record it was
        # made on. It is written as one whole document (no second merge into whatever is there
        # by then), after reading the record once more right before the write: a hook that
        # wrote in between (a newer "waiting") is decided against again, so its state wins. A
        # record that keeps changing is tried again next tick.
        for _ in range(RACE_TRIES):
            out, ours, held = self._plan(sid, change, at, doc)
            if out is None:
                if held:
                    result.kept.append(sid)
                return
            # --- review-fix R2b 2 --- the last read and the write hold the record's lock, so
            # a hook can't land between them (update_status takes the same lock)
            with record_lock(path) as locked:
                try:
                    again = read_existing(path)
                except StatusBusy:  # it couldn't be read between our read and the write
                    if not force:
                        self._hold(orig, tries, result)
                        return
                    again = doc  # held long enough: a still unreadable file is replaced
                if again != doc:
                    doc = again  # someone wrote it just now: decide again against what is there
                    continue
                if not locked:
                    result.errors.append(f"status {sid}: its write lock stayed busy, "
                                         "written without it"[:200])
                if held:
                    result.kept.append(sid)  # the record was written after the reader's source
                self._write(sid, path, out, result, ours=ours)
                return
            # --- end review-fix R2b 2 ---
        self._hold(orig, tries, result)

    def _plan(self, sid: str, change: dict, at: datetime,
              doc: dict | None) -> tuple[dict | None, bool, bool]:
        """What to write for a reader's change into the record ``doc`` (as read, ``None``: no
        file): (the whole document or ``None`` for no write, whether it is this Watcher's own
        write, whether a guarded field was held back because the record is newer)."""
        old = doc if doc is not None and not validate(doc) else None
        if old is None:
            return _compose(sid, {k: v for k, v in change.items() if v is not None}, at), True, \
                False
        # --- end review-fix: B2 3 ---
        updated = parse_time(old.get("updated"))
        raw = {k: v for k, v in old.items() if k != "updated"}
        # a hook may have written a lone surrogate (JSON allows "\ud83d"; UTF-8 does not): the
        # merge rewrites those fields with "?", or no write into this record could ever succeed
        base = _scrubbed(raw)
        ours = self._wrote.get(sid) == _digest(old)
        # Newer than the record, or the same second as a record this Watcher wrote itself. A
        # record someone else wrote in the same second wins: a hook fires just after the line.
        newer = updated is None or at > updated or (at == updated and ours)
        merged = dict(base)
        held = False
        for key, sent in change.items():
            value = sent
            if key in COUNTERS and sent is not None:
                value = max(sent, _count(old.get(key)))  # counts never go down
            elif not newer:
                if key in GUARDED or key in COUNTERS:  # (a counter removal is guarded too)
                    held = held or merged.get(key) != sent
                    continue
                if key in FILL_ONLY and (sent is None or key in merged):
                    continue
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        fresh = updated is not None and at - updated < timedelta(seconds=self.heartbeat)
        if merged == base and (not newer or fresh):
            return None, ours, held  # nothing to merge, and no heartbeat due
        if newer:
            return _compose(sid, merged, at), True, held
        # counters and x- fields only: the record keeps its writer's time
        return _compose(sid, merged, updated or at), False, held

    def _write(self, sid: str, path: Path, doc: dict, result: TickResult, *,
               ours: bool) -> None:
        # --- release-fix: ENG (engine 4, privacy 5); review-fix: B2 3 (one whole document) ---
        try:
            problems = validate(doc)
            if problems:
                raise StatusError("; ".join(problems))
            atomic_write_json(path, doc)
        except (OSError, ValueError) as exc:  # StatusError, UnicodeError
            result.errors.append(f"status {sid}: {safe_reason(exc)}"[:200])
            return
        # --- end release-fix: ENG ---
        result.written.append(sid)
        if ours:
            self._wrote[sid] = _digest(doc)
        else:
            self._wrote.pop(sid, None)  # its state is still the hook's

    # --- release-fix: ENG (engine 4) ---
    def _hold(self, change: dict, tries: int, result: TickResult) -> None:
        """Put a change off to the next tick (its record can't be read just now). A newer
        change for the same record that is put off too is merged over it."""
        sid = change["id"]
        before = self._held.pop(sid, None)
        if before is not None:
            change, tries = {**before[1], **change}, max(tries, before[0])
        self._held[sid] = (tries, change)
        result.retry.append(sid)
        while len(self._held) > MAX_AGENTS:
            del self._held[next(iter(self._held))]  # the oldest goes
    # --- end release-fix: ENG ---

    # ------------------------------------------------------------ folder diff
    def _private(self, folder: Any) -> bool:
        if not self.private_roots:
            return False
        return _under(str(self.dir), self.private_roots) or (
            isinstance(folder, str) and _under(folder, self.private_roots))

    def _plot(self, doc: Mapping[str, Any], result: TickResult) -> str:
        try:
            plot = self.plot_for(doc)
        except Exception as exc:  # a broken mapping falls back to the stub
            result.errors.append(f"plot: {type(exc).__name__}"[:200])
            plot = default_plot(doc)
        return plot if isinstance(plot, str) else default_plot(doc)

    # --- ab: [[live.group]] ---
    def _project_group(self, folder: Any, project: Any) -> str:
        """The ``[[live.group]]`` name for this folder (``""``: none, or a broken mapping)."""
        if self.project_for is None:
            return ""
        try:
            name = self.project_for(folder, project)
        except Exception:
            return ""
        return name if isinstance(name, str) else ""
    # --- end ab ---

    def _entry(self, doc: dict, sig: list[int], result: TickResult) -> dict:
        # --- L3-fix: home folder --- (and an x-project path counts like a folder for privacy)
        x_project = doc.get("x-project")
        private = self._private(doc.get("x-folder")) or (
            event_log.path_like(x_project) and self._private(x_project))
        project = (self._project_group(doc.get("x-folder"), x_project)  # ab: [[live.group]]
                   or event_log.project_of(x_project, doc.get("x-folder")))
        # --- end L3-fix ---
        title = name_only(doc["title"], event_log.LIMITS["title"])
        return {  # a private record keeps no name here either: this snapshot is saved to disk
            "m": sig, "raw": doc["state"], "u": doc["updated"],
            "sa": doc.get("stale_after", self.stale_after) or 0,
            "kd": doc.get("kind", "task"),
            "ti": "" if private else title, "p": "" if private else project,
            "to": (private_tool(doc.get("agent")) if private  # review-fix R3b 1
                   else name_only(doc.get("agent"), event_log.LIMITS["tool"])),
            "pl": self._plot(doc, result), "pv": private,
            "f": _count(doc.get("x-files")), "c": _count(doc.get("x-commits")),
            "pu": _flag(doc.get("x-pushed")),
            "ci": doc.get("x-ci") if doc.get("x-ci") in ("running", "green", "red") else None,
        }

    @staticmethod
    def _asleep(entry: dict) -> dict:
        """The small snapshot entry of a record past its expiry: enough to diff, no names."""
        return {"x": 1, "m": entry["m"], "s": entry["s"], "f": entry["f"], "c": entry["c"],
                "pu": entry["pu"], "ci": entry["ci"], "pv": entry["pv"], "kd": entry["kd"]}

    def _expired(self, entry: dict, now: datetime) -> bool:
        if not self.expire_after or entry["s"] == "working":
            return False  # a "working" record never expires (as in the collector)
        updated = parse_time(entry["u"])
        return updated is not None and now - updated > timedelta(seconds=self.expire_after)

    def _effective(self, entry: dict, now: datetime) -> tuple[str, datetime | None]:
        """The state as the town shows it, and when a time-based change happened."""
        updated = parse_time(entry["u"])
        stale = entry["sa"]
        if entry["raw"] != "working" or updated is None or not stale:
            return entry["raw"], None
        silent = now - updated
        quiet = self.quiet_after
        if quiet and silent > timedelta(seconds=max(quiet, stale)):
            return "quiet", updated + timedelta(seconds=max(quiet, stale))
        if silent > timedelta(seconds=stale):
            return "error", updated + timedelta(seconds=stale)
        return "working", None

    def _diff(self, state: dict, now: datetime, result: TickResult) -> list[dict]:
        first = state.get("folder") is None
        old: dict = state.get("folder") or {}
        try:
            paths = sorted(self.dir.iterdir())
        except FileNotFoundError:
            if first:  # no folder yet: an empty snapshot, so what readers write next is news
                state["folder"] = {}
            return []
        except OSError:
            return []  # unreadable for now: keep the old snapshot
        new: dict[str, dict] = {}
        out: list[dict] = []
        self._tidied = 0  # release-fix: ENG (engine 2)
        # --- review-fix: B2 1 --- the privacy rules changed since this snapshot was taken
        # (a folder added to private_dirs, or taken out): its names and private marks are
        # out of date, so every record is read again, not only the changed ones
        rebuild = not first and state.get("pr") != self._rules
        # --- end review-fix: B2 1 ---
        for path in paths:
            if path.suffix != ".json" or path.name.startswith("."):
                continue
            prev = old.get(path.stem)
            prev = prev if isinstance(prev, dict) else None
            try:
                st = path.stat()
            except OSError:
                st = None
            # the file id too: a rewrite of the same size within a coarse mtime still shows
            sig = [st.st_mtime_ns, st.st_size, st.st_ino or 0] if st else None
            if prev is not None and sig is not None and prev.get("m") == sig and not rebuild:
                # --- release-fix: ENG (engine 2) --- asleep and unchanged: an own record past
                # its removal time goes (in this same scan). An entry saved before 0.2's fix has
                # no "u": an own one is read once more to learn its time.
                if prev.get("x") and ("u" in prev or path.stem not in self._wrote):
                    if not self._tidy(path, prev, sig, now):
                        new[path.stem] = prev
                    continue
                entry = (self._read_entry(path, sig, result) if prev.get("x")
                         else dict(prev))
                if entry is None:
                    new[path.stem] = prev
                    continue
                # --- end release-fix: ENG ---
            else:
                entry = self._read_entry(path, sig, result) if sig else None
                if entry is None:
                    # unreadable for now (a writer mid-replace on Windows) or broken: keep
                    # what we knew, unchanged, so it isn't "new" again once it reads
                    if prev is not None:
                        new[path.stem] = self._unsure(prev) if rebuild else prev
                    continue
            state_now, when = self._effective(entry, now)
            entry["s"] = state_now
            entry["at"] = iso(when) if when else entry["u"]
            if not first:
                out.extend(self._changes(path.stem, prev, entry, now))
            # --- release-fix: ENG (engine 2) --- an own record asleep keeps its time ("u"):
            # it is removed once that is old enough
            kept = entry
            if self._expired(entry, now):
                kept = self._asleep(entry)
                if path.stem in self._wrote:
                    kept["u"] = entry["u"]
                    if self._tidy(path, kept, sig, now):
                        continue
            new[path.stem] = kept
            # --- end release-fix: ENG ---
        state["folder"] = new
        # --- review-fix: B2 1 ---
        added = bool(set(self._marks) - set(state.get("prs") or []))
        if rebuild:
            self._rules_changed(state, old, new)
            # review-fix R2b 1: and the log, once this tick's diff is done (see _remask_log)
            self._remask = ({sid for sid, e in new.items() if e.get("pv")}, added, set(new))
        elif first and added and _has_log(self.dir):
            # review-fix R3b 3: no saved state (deleted) but a log: it is masked for these
            # folders now, as remask_pending expects, before the state says it is
            self._remask = ({sid for sid, e in new.items() if e.get("pv")}, True, set(new))
        state["pr"] = self._rules
        state["prs"] = list(self._marks)  # review-fix R2b 1
        # --- end review-fix: B2 1 ---
        out.extend(self._new_projects(state, new, now, announce=not first))
        return out

    # --- review-fix: B2 1 ---
    def _unsure(self, prev: dict) -> dict:
        """A snapshot entry that couldn't be read again under new privacy rules: no names, private
        while any folder is, and read again next tick (no signature)."""
        return {**prev, "m": None, "ti": "", "p": "",
                "pv": bool(self.private_roots) or bool(prev.get("pv"))}

    def _rules_changed(self, state: dict, old: dict, new: dict) -> None:
        """After a rebuild under new privacy rules: the saved state keeps no name of a record
        that is private now. Its project leaves the projects seen, and the ``files`` counts
        still open for it are masked (and keyed by the masked id), as if it had always been
        private. A count for an agent with no record is masked while any folder is private (it
        can't be checked). Names of a record that is no longer private come back from its file."""
        gone = set()
        for sid, entry in new.items():
            before = old.get(sid)
            if entry.get("pv") and isinstance(before, dict) and not before.get("pv"):
                gone.update(n for n in (before.get("p"), before.get("kd") == "project"
                                        and before.get("ti")) if isinstance(n, str) and n)
        if gone:
            state["projects"] = [n for n in state["projects"] if n not in gone]
        pending: dict = state["pending"]
        flushed: dict = state.setdefault("flushed", {})
        for key in list(pending):
            event = pending[key]
            sid = event.get("id") if isinstance(event, dict) else None
            if not isinstance(sid, str):
                continue
            entry = new.get(sid)
            if entry is None and sid.startswith("private-"):
                continue  # masked when it was counted
            if not (entry.get("pv") if entry else bool(self.private_roots)):
                continue
            masked = self._mask(event)
            minute = key.rpartition("|")[2]
            del pending[key]
            mkey = f"{masked['id']}|{minute}"
            if mkey in pending:
                pending[mkey]["n"] = pending[mkey].get("n", 0) + masked.get("n", 1)
            else:
                pending[mkey] = masked
            if sid in flushed:
                last = flushed.pop(sid)
                flushed[masked["id"]] = max(last, flushed.get(masked["id"], ""))
    # --- end review-fix: B2 1 ---

    # --- review-fix R2b 1 ---
    def _remask_log(self, private: set[str], unknown: bool, known: set[str]) -> None:
        """Mask the logged events of records that are private under the new rules, in place:
        each such line gets the masked id, "Private agent" and no project, as if the record had
        always been private (its ``project`` lines go). With ``unknown`` (a rule hides a folder
        it didn't before) a line for an agent with no record is masked too when it may have run
        there (:func:`unplaced`): a ``git`` or ``ci_gh`` id whose repository a reader's
        ``repos`` or a record's folder tells is checked instead (R3b 2). Already masked lines
        stay (a free-text tool on one is cleaned, R3b 1); torn or broken lines (never read) are
        dropped from a file that is rewritten. Only the Watcher holding the folder appends to
        the log, so nothing is lost meanwhile; each day file is replaced atomically."""
        try:
            paths = sorted(event_log.events_dir(self.dir).iterdir())
        except FileNotFoundError:
            return
        places = (repo_places([*reader_repos(self.readers), *self._record_folders()],
                              self.private_roots) if unknown else {})
        for path in paths:
            if not event_log.DAY_RE.match(path.name) or path.is_symlink() or not path.is_file():
                continue
            out: list[bytes] = []
            changed = False
            with open(path, "rb") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    event = self._log_line(line)
                    if event is None:
                        changed = True  # torn or broken: no reader ever sees it
                        continue
                    sid = event["id"]
                    if not sid.startswith("private-") and (sid in private or (
                            unknown and unplaced(sid, known, places, self._private))):
                        changed = True
                        if event["k"] == "project":
                            continue  # a private record names no project
                        event = clean_event(self._mask(event))
                        if event is None:
                            continue
                    elif sid.startswith("private-") and event.get("tool") != private_tool(
                            event.get("tool")):  # review-fix R3b 1: an older line's free text
                        changed = True
                        event = {**event, "tool": private_tool(event.get("tool"))}
                    out.append(event_log.encode(event))
            if changed:
                atomic_write_bytes(path, b"".join(out))
        self._seen = {}  # the day files' keys changed

    def _record_folders(self) -> list[str]:
        """The ``x-folder`` of each valid status record now, as ``livetown.read_records`` reads
        them for the build (for :func:`repo_places`; not kept)."""
        out: list[str] = []
        try:
            paths = sorted(p for p in self.dir.iterdir()
                           if p.suffix == ".json" and not p.name.startswith("."))
        except OSError:
            return out
        for path in paths[:MAX_PLACES]:
            try:
                doc = read_status(path)
            except StatusError:
                continue
            if validate(doc) or doc.get("id") != path.stem:
                continue
            folder = doc.get("x-folder")
            if isinstance(folder, str) and folder:
                out.append(folder)
        return out

    @staticmethod
    def _log_line(line: bytes) -> dict | None:
        if len(line.rstrip(b"\r\n")) >= event_log.MAX_LINE:
            return None
        try:
            raw = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return clean_event(raw) if isinstance(raw, dict) else None
    # --- end review-fix R2b 1 ---

    # --- release-fix: ENG (engine 2) ---
    def _tidy(self, path: Path, entry: dict, sig: list[int] | None, now: datetime) -> bool:
        """Remove an asleep record this Watcher wrote itself, once its last update is older
        than :attr:`remove_after` (expiry plus the history window). Only when the file still
        holds exactly what this Watcher wrote (a hook's later write keeps it for good) and is
        unchanged since this scan's ``stat``. At most :data:`MAX_TIDY` are looked at per tick.
        Returns whether it was removed."""
        sid = path.stem
        if not self.remove_after or sid not in self._wrote or sig is None:
            return False
        if not self._tidy_ok:
            return False  # fix2: ENG 4 (first tick of a run, or the clock jumped): next tick
        updated = parse_time(entry.get("u"))
        if updated is None or now - updated <= timedelta(seconds=self.remove_after):
            return False
        if self._tidied >= MAX_TIDY:
            return False  # the rest next tick
        self._tidied += 1
        try:
            if path.is_symlink():
                return False
            doc = read_status(path)
        except (OSError, StatusError):
            return False
        if _digest(doc) != self._wrote.get(sid):
            self._wrote.pop(sid, None)  # someone else wrote it since: never this Watcher's
            return False
        try:
            st = path.stat()
            if [st.st_mtime_ns, st.st_size, st.st_ino or 0] != sig:
                return False  # rewritten just now: it's alive
            path.unlink()
        except OSError:
            return False  # held open (Windows) or read-only: try again next tick
        self._wrote.pop(sid, None)
        drop_stale_lock(path)  # review-fix R3b 4: a killed writer's lock goes with it
        return True
    # --- end release-fix: ENG ---

    def _read_entry(self, path: Path, sig: list[int], result: TickResult) -> dict | None:
        try:
            doc = read_status(path)
        except StatusError:
            return None
        if validate(doc) or doc.get("id") != path.stem:
            return None  # the collector shows broken files; they are no event
        return self._entry(doc, sig, result)

    def _changes(self, sid: str, prev: dict | None, cur: dict, now: datetime) -> list[dict]:
        if cur["kd"] == "project":
            return []  # a project record is a berth, not an agent (see _new_projects)
        rec_t = min(parse_time(cur["u"]) or now, now)
        out: list[dict] = []

        def add(kind: str, when: datetime, **extra: Any) -> None:
            event = self._event(sid, cur, kind, min(when, now), **extra)
            if event:
                out.append(event)

        if prev is None:
            add("start", rec_t)
        for key, kind in (("f", "files"), ("c", "commit")):
            delta = cur[key] - (prev.get(key, 0) if prev else 0)
            if delta > 0:
                add(kind, rec_t, n=delta)
        if cur["pu"] and cur["pu"] != (prev or {}).get("pu"):
            add("push", rec_t)
        if cur["ci"] in ("green", "red") and cur["ci"] != (prev or {}).get("ci"):
            add("ci", rec_t, ok=cur["ci"] == "green")
        old_state = prev.get("s") if prev else "working"
        if cur["s"] != old_state:
            kind = state_kind(old_state, cur["s"])
            if kind and not (prev is None and kind == "start"):
                add(kind, parse_time(cur["at"]) or rec_t)
        return out

    def _new_projects(self, state: dict, snapshot: dict, now: datetime,
                      announce: bool) -> list[dict]:
        known = state["projects"]
        seen = set(known)
        out = []
        for sid, entry in snapshot.items():
            if entry.get("x"):
                continue  # asleep: its project, if any, was seen when it was awake
            name = entry["p"] or (entry["ti"] if entry["kd"] == "project" else "")
            if not name or entry["pv"] or name in seen:
                continue
            seen.add(name)
            known.append(name)
            if announce:
                when = min(parse_time(entry["u"]) or now, now)
                event = self._event(sid, {**entry, "p": name}, "project", when)
                if event:
                    out.append(event)
        del known[:-MAX_PROJECTS]
        return out

    def _event(self, sid: str, entry: Mapping[str, Any], kind: str, when: datetime,
               **extra: Any) -> dict | None:
        raw = {"t": when, "id": sid, "k": kind, "title": entry.get("ti", ""),
               "project": entry.get("p", ""), "tool": entry.get("to", ""),
               "plot": entry.get("pl", ""), **extra}
        if entry.get("pv"):
            raw = self._mask(raw)
        return clean_event(raw)

    def private_id(self, sid: str) -> str:
        """A private record's id in events: the same as the ``status_dir`` collector's."""
        digest = hashlib.sha256(f"{self.dir}\0{sid}".encode()).hexdigest()[:10]
        return f"private-{digest}"

    def _mask(self, raw: dict) -> dict:
        return {**raw, "id": self.private_id(str(raw["id"])), "title": PRIVATE_NAME,
                "project": "", "tool": private_tool(raw.get("tool"))}  # review-fix R3b 1

    # ------------------------------------------------------------ events
    def _enrich(self, raw: Mapping[str, Any], snapshot: dict, now: datetime) -> dict | None:
        """A reader's event, with missing names taken from the record and private ones hidden."""
        sid = raw.get("id")
        entry = snapshot.get(sid) if isinstance(sid, str) else None
        folder = raw.get("folder")
        doc = dict(raw)
        if entry:
            for key, mine in (("title", "ti"), ("project", "p"), ("tool", "to"), ("plot", "pl")):
                if not doc.get(key):
                    doc[key] = entry.get(mine, "")
        private = bool(entry and entry.get("pv")) or self._private(folder)
        if self.private_roots and entry is None and not (isinstance(folder, str) and folder):
            private = True  # no record and no folder: it can't be checked, so it is hidden
        # --- L3-fix: home folder ---
        if doc.get("project") or folder:
            doc["project"] = (self._project_group(folder, doc.get("project"))  # ab: [[live.group]]
                              or event_log.project_of(doc.get("project"), folder))
        # --- end L3-fix ---
        doc.pop("folder", None)
        if private:
            doc = self._mask(doc)
        event = clean_event(doc)
        if event is None:
            return None
        event["t"] = min(event["t"], iso(now))  # both UTC "...Z" text: compares as time
        return event

    @staticmethod
    def _once(events: list[dict], source: str, state: dict, now: datetime) -> list[dict]:
        """``events`` without the copy of a push or CI result the other source already logged.

        A reader sees a push in its log, and the record shows it (``x-pushed``, ``x-ci``) a few
        seconds later, often in another tick. Each one logged is kept in ``state["recent"]``
        for :data:`RECENT_WINDOW` seconds, under a hash of its id, kind and result (no names or
        ids on disk), with its time and its source (``source``: ``"r"`` reader, ``"f"`` folder
        diff). The other source's copy within that window is dropped; the same source twice is
        two real pushes. At most :data:`MAX_RECENT` are kept (the newest)."""
        recent: dict = state.setdefault("recent", {})
        edge = iso(now - timedelta(seconds=RECENT_WINDOW))
        for key in [k for k, v in recent.items() if v["t"] < edge]:
            del recent[key]
        window = timedelta(seconds=RECENT_WINDOW)
        out = []
        for event in events:
            if event["k"] not in ("push", "ci"):
                out.append(event)
                continue
            mark = f"{event['id']}|{event['k']}|{event.get('ok')}"
            key = hashlib.sha256(mark.encode()).hexdigest()[:16]
            seen = recent.get(key)
            when, then = parse_time(event["t"]), parse_time(seen["t"]) if seen else None
            if seen and seen["s"] != source and when and then and abs(when - then) <= window:
                continue  # the other source's copy of one already logged
            recent[key] = {"t": event["t"], "s": source}
            out.append(event)
        if len(recent) > MAX_RECENT:
            for key in sorted(recent, key=lambda k: recent[k]["t"])[: len(recent) - MAX_RECENT]:
                del recent[key]
        return out

    @staticmethod
    def _group(found: list[dict], state: dict, now: datetime) -> list[dict]:
        """At most one ``files`` event per agent per minute: counts wait in ``pending`` until
        their minute is over. One that comes late, for the minute last written, joins the next
        minute's group instead."""
        pending: dict = state["pending"]
        flushed: dict = state.setdefault("flushed", {})
        out = []
        for found_event in found:
            if not found_event:
                continue
            if found_event["k"] != "files":
                out.append(found_event)
                continue
            event, minute = found_event, found_event["t"][:16]
            if minute == flushed.get(event["id"]):
                after = datetime.fromisoformat(minute + ":00+00:00") + timedelta(minutes=1)
                event = {**event, "t": iso(after)}
                minute = event["t"][:16]
            key = f"{event['id']}|{minute}"
            if key in pending:
                pending[key]["n"] = pending[key].get("n", 0) + event.get("n", 1)
            else:
                pending[key] = {**event, "n": event.get("n", 1)}
        current = iso(now)[:16]
        for key in sorted(pending):
            sid, _, minute = key.rpartition("|")
            if minute < current:
                event = clean_event(pending.pop(key))
                if event:
                    out.append(event)
                    flushed[sid] = max(flushed.get(sid, ""), minute)
        day_ago = iso(now - timedelta(days=1))[:16]
        for sid in [s for s, m in flushed.items() if not isinstance(m, str) or m < day_ago]:
            del flushed[sid]
        return out

    def _prune(self, state: dict, now: datetime, result: TickResult) -> None:
        today = event_log.local_day(now, self.tz)
        for day in [d for d in self._seen if (today - d).days >= self.keep_days]:
            del self._seen[day]
        if state.get("pruned") == today.isoformat():
            return
        try:
            event_log.prune(self.dir, self.keep_days, now=now, tz=self.tz)
        except OSError as exc:
            result.errors.append(f"event log prune: {exc.strerror or type(exc).__name__}")
            return
        state["pruned"] = today.isoformat()
