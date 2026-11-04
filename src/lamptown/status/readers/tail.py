"""Incremental reading of growing log files (JSON Lines and the like), within a byte budget.

    def poll(self, state, now, budget=None):
        cursors = state.setdefault("files", {})   # the reader's saved state
        budget = budget or Budget()                # the Watcher's, shared by every reader
        for path in paths:
            for line in tail(path, cursors, budget).lines:
                doc = line.json()                  # None for a big or broken line

How it keeps its place: ``cursors[cursor_key(path)]`` (a hash: the saved state holds no path)
holds the file's ``size`` and ``mtime_ns`` when it was last read, the ``offset`` of the first
byte not yet handed out, how far it has ``seen``, and a hash of the first 256 bytes (``head``,
over ``hlen`` bytes) plus the file id (``ino``).

* **Nothing changed**: size and mtime are the same and everything was seen, so the file is only
  ``stat``-ed, never opened.
* **Partial lines** (a writer is half-way through a line) are not handed out: ``offset`` stays at
  the start of that line and the line is read again, whole, once its newline arrives.
* **Replaced or truncated files** (a different first 256 bytes or file id, or a size smaller
  than ``offset``) are read again from the start; :attr:`TailResult.reset` says which.
* **Budget**: at most ``per_file`` bytes per file and ``total`` bytes per tick. A file that had
  more to read says so in :attr:`TailResult.more` and goes on next tick.
* **Big lines**: a line longer than ``max_line`` (256 KB, or less than ``per_file`` if that is
  smaller) is handed out as a :class:`Line` with ``big=True`` and only its first ``max_line``
  bytes, for a regex; its remainder is skipped.
* **Gone or busy**: a file that is gone loses its cursor (:attr:`TailResult.missing`); one that
  can't be opened for now (Windows, mid-replace) keeps it (:attr:`TailResult.busy`).

Readers get one :class:`Budget` per tick from the Watcher (``poll(state, now, budget)``), shared
by every reader, so the 2 MB total holds for the whole tick.

Files are opened read-only and closed straight away (on Windows ``open(..., "rb")`` shares read
and write with the program writing the log). Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TOTAL = 2 * 1024 * 1024
PER_FILE = 512 * 1024
MAX_LINE = 256 * 1024
HEAD = 256
# --- L4-fix: catch-up --- a tick while a reader is behind (first run, a long pause):
CATCHUP_TOTAL = 32 * 1024 * 1024
CATCHUP_PER_FILE = 4 * 1024 * 1024
CATCHUP_SECONDS = 0.2  # reading deadline of such a tick
# --- end L4-fix ---


class Budget:
    """Bytes one tick may read: ``total`` in all, ``per_file`` from any one file.

    ``deadline`` (a ``clock()`` reading, default ``time.monotonic``): after it, :meth:`allow`
    gives 0, so a catch-up tick with a big byte budget still ends on time; it is checked
    before each read, so a tick runs over by at most one ``per_file`` read."""

    # --- L4-fix: catch-up --- (deadline, clock)
    def __init__(self, total: int = TOTAL, per_file: int = PER_FILE, *,
                 deadline: float | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.total, self.per_file, self.left = total, per_file, total
        self.deadline, self.clock = deadline, clock

    def allow(self) -> int:
        if self.deadline is not None and self.clock() >= self.deadline:
            return 0
        return max(0, min(self.per_file, self.left))
    # --- end L4-fix ---

    def spend(self, n: int) -> None:
        self.left -= n

    # --- L4-fix: catch-up ---
    @classmethod
    def catch_up(cls, clock: Callable[[], float] = time.monotonic) -> Budget:
        """The budget of a tick while a reader is behind: 32 MB, 4 MB a file, 200 ms."""
        return cls(CATCHUP_TOTAL, CATCHUP_PER_FILE, deadline=clock() + CATCHUP_SECONDS,
                   clock=clock)
    # --- end L4-fix ---


@dataclass
class Line:
    """One complete line, without its newline. A ``big`` line holds only its first bytes."""

    data: bytes
    offset: int
    big: bool = False

    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """The parsed JSON value, or ``None`` for a big, broken or non-UTF-8 line."""
        if self.big:
            return None
        try:
            return json.loads(self.data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None


@dataclass
class TailResult:
    lines: list[Line] = field(default_factory=list)
    more: bool = False  # the budget stopped before the end of the file
    reset: str | None = None  # "replaced" or "truncated": read again from the start
    missing: bool = False  # the file is gone; its cursor was dropped
    busy: bool = False  # the file could not be read this time (locked); its cursor is kept


def cursor_key(path: str | os.PathLike) -> str:
    """The key of ``path`` in a cursors dict: a hash, so the saved state holds no path (log
    folders such as ``projects/C--Users-<name>-<project>`` carry private names)."""
    return "f" + hashlib.sha256(os.fsencode(os.fspath(path))).hexdigest()[:24]


def _digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:16]


def _same_file(cur: dict, st: os.stat_result) -> bool:
    old = cur.get("ino") or 0
    return not (old and st.st_ino and old != st.st_ino)


def tail(path: str | os.PathLike, cursors: dict, budget: Budget, *, max_line: int = MAX_LINE,
         from_end: bool = False) -> TailResult:
    """The complete new lines of ``path`` since ``cursors`` last saw it (see the module doc).

    ``from_end``: a file seen for the first time starts at its end (only new lines) instead of
    its start. ``cursors`` is changed in place (keyed by :func:`cursor_key`); a file that is
    gone loses its entry, one that can't be read for now (``busy``) keeps it."""
    key = cursor_key(path)
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        cursors.pop(key, None)
        return TailResult(missing=True)
    except OSError:  # Windows: access denied while a writer replaces the file
        return TailResult(busy=True)
    cur = cursors.get(key)
    if not isinstance(cur, dict):
        cur = None
    if (cur and cur.get("size") == st.st_size and cur.get("mtime_ns") == st.st_mtime_ns
            and cur.get("seen", 0) >= st.st_size and _same_file(cur, st)):
        return TailResult()  # nothing changed: stat only
    result = TailResult()
    try:
        with open(path, "rb") as fh:
            new = _read(fh, st, cur, budget, max_line=max_line, from_end=from_end,
                        result=result)
    except (FileNotFoundError, NotADirectoryError):
        cursors.pop(key, None)
        return TailResult(missing=True)
    except OSError:
        return TailResult(busy=True)
    cursors[key] = new
    return result


def _read(fh, st: os.stat_result, cur: dict | None, budget: Budget, *, max_line: int,
          from_end: bool, result: TailResult) -> dict:
    size = st.st_size
    # A line that can't fit in one tick's per-file budget would never complete: it is "big".
    max_line = max(1, min(max_line, budget.per_file - 1))
    head = fh.read(min(HEAD, size))
    offset, skip = 0, False
    if cur is not None:
        offset, skip = int(cur.get("offset", 0)), bool(cur.get("skip"))
        hlen = int(cur.get("hlen", 0))
        if size < offset or size < cur.get("seen", 0):
            result.reset = "truncated"
        elif not _same_file(cur, st) or (hlen and _digest(head[:hlen]) != cur.get("head")):
            result.reset = "replaced"
        if result.reset:
            offset, skip = 0, False
    elif from_end and size:
        offset = size
        fh.seek(size - 1)
        skip = fh.read(1) != b"\n"  # the file ends mid-line: skip the rest of that line

    data = b""
    allow = budget.allow()
    if allow and offset < size:
        fh.seek(offset)
        data = fh.read(min(allow, size - offset))
        budget.spend(len(data))
    seen = offset + len(data)
    result.more = seen < size

    pos = 0
    if skip and data:
        nl = data.find(b"\n")
        if nl < 0:
            pos = len(data)
        else:
            pos, skip = nl + 1, False
    while True:
        nl = data.find(b"\n", pos)
        if nl < 0:
            break
        line = data[pos:nl]
        if line.endswith(b"\r"):
            line = line[:-1]
        big = len(line) > max_line
        result.lines.append(Line(line[:max_line] if big else line, offset + pos, big))
        pos = nl + 1
    rest = len(data) - pos
    if rest > max_line:  # a big line still being read: hand out its head, skip the rest
        result.lines.append(Line(data[pos:pos + max_line], offset + pos, True))
        pos, skip = len(data), True
    # else: a partial line stays unread (offset at its start) until its newline arrives

    return {"size": size, "mtime_ns": st.st_mtime_ns, "offset": offset + pos, "seen": seen,
            "head": _digest(head), "hlen": len(head), "ino": st.st_ino or 0,
            **({"skip": True} if skip else {})}


def start_at(path: str | os.PathLike, cursors: dict, offset: int) -> None:
    """Make the next :func:`tail` of ``path`` start at the first line that begins at or after
    ``offset`` (a big log seen for the first time can start near its end, say). The cursor
    has no head hash yet, so it can't tell a replaced file until that first read; a file
    shorter than ``offset`` by then reads as truncated, from the start."""
    offset = max(0, int(offset))
    skip = False
    if offset:
        try:
            with open(path, "rb") as fh:
                fh.seek(offset - 1)
                skip = fh.read(1) != b"\n"  # mid-line: skip to the next line start
        except OSError:
            skip = True
    cursors[cursor_key(path)] = {"size": -1, "mtime_ns": 0, "offset": offset, "seen": offset,
                                 "head": "", "hlen": 0, "ino": 0,
                                 **({"skip": True} if skip else {})}


def forget(cursors: dict, keep: Iterable[str | os.PathLike]) -> list[str]:
    """Drop the cursors of files not in ``keep`` (paths: gone, or too old to watch). Returns
    the dropped keys."""
    wanted = {cursor_key(p) for p in keep}
    dropped = [k for k in cursors if k not in wanted]
    for k in dropped:
        del cursors[k]
    return dropped


def tail_json(path: str | Path, cursors: dict, budget: Budget, **kw: Any
              ) -> tuple[list[tuple[Any, Line]], TailResult]:
    """:func:`tail`, with each line paired with its parsed JSON (``None`` when big or broken)."""
    result = tail(path, cursors, budget, **kw)
    return [(line.json(), line) for line in result.lines], result
