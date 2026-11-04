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
   are merged in. A reader that raises is reported in :attr:`TickResult.errors` and its state
   is rolled back; the others still run.
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
``expire_after`` keep only a small entry.

Plots: :func:`default_plot` is a stub (``x-plot``, else ``tower`` for jobs, else
``boathouse``); pass ``plot_for`` to map a record's folder to a building (L3 does).
Private: with ``private_dirs``, events of records whose ``x-folder`` (or the status folder
itself) is in or under one of them are written as "Private agent", with no project and the
collector's masked id (``private-`` + a hash of the status folder and the id), so private names
never reach the log and L3 can link a masked live record to its history. A reader event with
no status record and no ``folder`` can't be checked, so with ``private_dirs`` set it is masked.
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
    StatusError,
    atomic_write_json,
    default_dir,
    iso,
    read_status,
    status_path,
    update_status,
    utcnow,
)

STATE_DIR = ".lamptown"
STATE_FILE = "cursors.json"
LOCK_FILE = "watch.lock"
STATE_VERSION = 1
STATE_MAX_BYTES = 4 * 1024 * 1024
HEARTBEAT = 60  # seconds: an unchanged record is rewritten at most this often
SAVE_EVERY = 20  # seconds: the saved state is written at most this often while things change
MAX_PROJECTS = 2000
RECENT_WINDOW = 120  # seconds: a push or CI result from a reader and a record counts once
MAX_RECENT = 1000  # pushes and CI results remembered for that, at most
STATE_EVENT = {"waiting": "wait", "ok": "done", "error": "fail", "quiet": "quiet"}
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

    @property
    def changed(self) -> bool:
        return bool(self.written or self.events)


def _fresh_state() -> dict:
    return {"v": STATE_VERSION, "readers": {}, "folder": None, "pending": {}, "flushed": {},
            "projects": [], "pruned": "", "wrote": {}, "recent": {}}


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


def _takes_budget(poll: Callable) -> bool:
    try:
        params = inspect.signature(poll).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "budget" or p.kind is p.VAR_KEYWORD for p in params)


def state_kind(old: str | None, new: str) -> str | None:
    """The event for a change of (effective) state, or ``None``."""
    if new == "working":
        return "answer" if old == "waiting" else "start"
    return STATE_EVENT.get(new)


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
    ) -> None:
        self.dir = default_dir(status_dir)
        self.readers = list(readers)
        self.plot_for = plot_for
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
        """Save the state (if this Watcher holds the folder) and let the folder go."""
        if self._fd is None:
            return
        try:
            if self._state is not None:
                self.save()
        finally:
            self._unlock()

    def __enter__(self) -> Watcher:
        return self

    def __exit__(self, *exc: object) -> None:
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
                          ("wrote", dict), ("recent", dict)):
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
            result.errors.append(f"watcher state: {exc}"[:200])
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
        state = self.state()
        if not self._good:
            self._good = json.dumps(state, sort_keys=True)
        if self._since is None:
            self._since = iso(now)  # a run before this one may have logged events up to now
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
        reader_events: list[Mapping[str, Any]] = []
        for reader in readers:
            batch = self._poll(reader, state, now, result, budget)
            if batch is None:
                continue
            agents, found = list(batch.agents), list(batch.events)
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
        result.behind = self._behind = any(getattr(r, "behind", False) is True
                                           for r in self.readers)
        # --- end L4-fix ---

        folder_events = self._diff(state, now, result)
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
                result.errors.append(f"event log: {getattr(exc, 'strerror', None) or exc}"[:200])
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
        return result

    def _poll(self, reader: Reader, state: dict, now: datetime, result: TickResult,
              budget: Budget) -> Batch | None:
        name = reader.name
        saved = state["readers"].get(name)
        own = copy.deepcopy(saved) if isinstance(saved, dict) else {}
        state["readers"][name] = own
        try:
            takes = self._budget_kw.get(id(reader))
            if takes is None:
                takes = self._budget_kw[id(reader)] = _takes_budget(reader.poll)
            batch = reader.poll(own, now, budget=budget) if takes else reader.poll(own, now)
            if not isinstance(batch, Batch):
                raise TypeError("poll() must return a Batch")
            json.dumps(own)  # the state must stay JSON
        except Exception as exc:  # one broken reader must not stop the town
            state["readers"][name] = saved if isinstance(saved, dict) else {}
            result.errors.append(f"reader {name}: {type(exc).__name__}: {exc}"[:200])
            return None
        return batch

    # ------------------------------------------------------------ status changes
    def _apply(self, change: dict, now: datetime, result: TickResult) -> None:
        sid = change.pop("id")
        at = min(change.pop("at") or now, now)
        old = None
        path = status_path(sid, self.dir)
        if path.exists():
            try:
                doc = read_status(path)
                old = doc if not validate(doc) else None
            except StatusError:
                old = None
        if old is None:
            self._write(sid, {k: v for k, v in change.items() if v is not None}, at, result,
                        ours=True)
            return
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
        if held:
            result.kept.append(sid)  # the record was written after the reader's source
        fresh = updated is not None and at - updated < timedelta(seconds=self.heartbeat)
        if merged == base and (not newer or fresh):
            return  # nothing to merge, and no heartbeat due
        diff = {k: merged.get(k) for k in set(raw) | set(merged) if raw.get(k) != merged.get(k)}
        if newer:
            self._write(sid, diff, at, result, ours=True)
        else:  # counters and x- fields only: the record keeps its writer's time
            self._write(sid, diff, updated or at, result, ours=False)

    def _write(self, sid: str, changes: dict, when: datetime, result: TickResult, *,
               ours: bool) -> None:
        try:
            doc = update_status(sid, self.dir, now=when, **changes)
        except (OSError, ValueError) as exc:  # StatusError, UnicodeError
            result.errors.append(f"status {sid}: {exc}"[:200])
            return
        result.written.append(sid)
        if ours:
            self._wrote[sid] = _digest(doc)
        else:
            self._wrote.pop(sid, None)  # its state is still the hook's

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

    def _entry(self, doc: dict, sig: list[int], result: TickResult) -> dict:
        # --- L3-fix: home folder --- (and an x-project path counts like a folder for privacy)
        x_project = doc.get("x-project")
        private = self._private(doc.get("x-folder")) or (
            event_log.path_like(x_project) and self._private(x_project))
        project = event_log.project_of(x_project, doc.get("x-folder"))
        # --- end L3-fix ---
        title = name_only(doc["title"], event_log.LIMITS["title"])
        return {  # a private record keeps no name here either: this snapshot is saved to disk
            "m": sig, "raw": doc["state"], "u": doc["updated"],
            "sa": doc.get("stale_after", self.stale_after) or 0,
            "kd": doc.get("kind", "task"),
            "ti": "" if private else title, "p": "" if private else project,
            "to": name_only(doc.get("agent"), event_log.LIMITS["tool"]),
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
            if prev is not None and prev.get("m") == sig:
                if prev.get("x"):
                    new[path.stem] = prev  # asleep and unchanged
                    continue
                entry = dict(prev)
            else:
                entry = self._read_entry(path, sig, result) if sig else None
                if entry is None:
                    # unreadable for now (a writer mid-replace on Windows) or broken: keep
                    # what we knew, unchanged, so it isn't "new" again once it reads
                    if prev is not None:
                        new[path.stem] = prev
                    continue
            state_now, when = self._effective(entry, now)
            entry["s"] = state_now
            entry["at"] = iso(when) if when else entry["u"]
            if not first:
                out.extend(self._changes(path.stem, prev, entry, now))
            new[path.stem] = self._asleep(entry) if self._expired(entry, now) else entry
        state["folder"] = new
        out.extend(self._new_projects(state, new, now, announce=not first))
        return out

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
                "project": ""}

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
            doc["project"] = event_log.project_of(doc.get("project"), folder)
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
