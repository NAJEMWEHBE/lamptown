"""The live town's moving parts outside the page: in-process builds, one Watcher, and the loop
that ``lamptown watch`` runs. The desktop app (:mod:`lamptown.app.live`) uses the same pieces.

* :class:`Builder` builds the town in-process and reads the config again on every call, so edits
  show up live; a broken config is logged once and keeps the last good page.
* :class:`LiveFeed` owns **one** :class:`~lamptown.status.watch.Watcher` for the config's status
  folder. A live tick runs the Watcher (when it is due), then writes ``town-live.js`` (and
  ``town-history.js`` when due) next to ``town.html`` through ``build.write_live_files``:
  atomic, and always, so the file's ``at`` is the page's heartbeat. The Watcher runs every
  ``[live] poll`` seconds, but a tick comes at least every :data:`HEARTBEAT_MAX` seconds (the
  page shows BUILT 30 s after the last write), and every :data:`BEHIND_EVERY` second while a
  reader is catching up. ``town-data.js`` is written only by a build, before its
  ``town-live.js``; a payload's ``rev`` is always the one in the ``town-data.js`` on disk, so
  two programs writing the same town folder never disagree about it. When another Watcher
  holds the folder (``TickResult.busy``) this one stands by quietly and takes over once it is
  free; the live file is still written (it only reads the status folder).
* :func:`watch_loop` is ``lamptown watch``: a live tick when the feed says one is due (every
  3 s by default) and a full rebuild every ``rebuild_every`` seconds (15), whichever comes
  first.
* :class:`FailLog`: a step that keeps failing logs its traceback once, then a counted line now
  and then.

Nothing here imports the builder at module level: the CLI registers its commands without
loading it.
"""

from __future__ import annotations

import dataclasses
import math
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LIVE_EVERY = 3.0  # seconds between Watcher runs (the config's [live] poll overrides it)
REBUILD_EVERY = 15.0  # seconds between full rebuilds
HEARTBEAT_MAX = 10.0  # town-live.js is written at least this often, whatever the poll
BEHIND_EVERY = 1.0  # seconds between ticks while a reader is catching up (L4-fix's `behind`)
SLACK = 0.05  # a Watcher run due within this many seconds runs now (clocks and timers jitter)
_sleep = time.sleep  # watch_loop's default (tests replace it)


# ----------------------------------------------------------------------------- logging
class FailLog:
    """One step that may fail over and over (a tick every 3 s): its traceback is logged the
    first time, then one counted line on the 2nd, 4th, 8th... failure in a row (and every
    1000th), and one line when it works again. A different failure (another place in the code,
    or another exception type) logs its traceback again."""

    def __init__(self, log: Callable[[str], None], what: str) -> None:
        self.log = log
        self.what = what
        self.key: str | None = None
        self.count = 0

    def failed(self, exc: BaseException | None = None) -> None:
        exc = exc if exc is not None else _current()
        if exc is None:
            return
        tb = "".join(traceback.format_tb(exc.__traceback__))
        key = f"{type(exc).__qualname__}\n{tb}"
        last = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        if key != self.key:
            self.key, self.count = key, 1
            self.log(f"{self.what} failed:\n" + "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)))
            return
        self.count += 1
        n = self.count
        if n & (n - 1) == 0 or n % 1000 == 0:
            self.log(f"{self.what} still failing ({n} times in a row): {last[:300]}")

    def ok(self) -> None:
        if self.key is not None:
            if self.count > 1:
                self.log(f"{self.what} works again (after {self.count} failures)")
            else:
                self.log(f"{self.what} works again")
        self.key, self.count = None, 0


def _current() -> BaseException | None:
    return sys.exc_info()[1]


# ----------------------------------------------------------------------------- building
class Builder:
    """Builds the town in-process. Reads the config on every call, so edits show up live; a broken
    config is logged (once per distinct message) and keeps the last good page.

    ``on_config(cfg, out_dir)`` runs with every config that loaded, before the build (the
    :class:`LiveFeed` follows the config through it)."""

    def __init__(self, config: str | None, out: str | None, log: Callable[[str], None],
                 on_config: Callable[[Any, Path], None] | None = None) -> None:
        self.config = config
        self.out = Path(out).expanduser() if out else None
        self.log = log
        self.on_config = on_config
        self.town: Path | None = None
        self.error: str | None = None
        self.cfg: Any = None  # the last config that loaded
        self.data: dict[str, Any] | None = None  # the last good build's data
        self._said: str | None = None

    def out_dir(self, cfg) -> Path:
        if self.out is None:
            from lamptown.build import default_out_dir

            self.out = default_out_dir(cfg)
        return self.out

    def fallback_town(self) -> Path:
        """Where the town page will be once a build works (for a window that opens on an error)."""
        if self.out is None:
            from lamptown.config import user_data_dir

            self.out = user_data_dir() / "town"
        from lamptown.build import TOWN_NAME

        return self.out / TOWN_NAME

    def __call__(self) -> dict[str, Any] | None:
        from lamptown.build import build
        from lamptown.config import ConfigError, load_config

        try:
            cfg = load_config(self.config)
            out = self.out_dir(cfg)
            self.cfg = cfg
            if self.on_config is not None:
                self.on_config(cfg, out)
            res = build(cfg, out_dir=out)
        except (ConfigError, OSError) as e:
            self.error = str(e)
            if self.error != self._said:
                self.log(f"can't build the town: {self.error}")
                self._said = self.error
            return None
        except Exception:
            self.error = "the build crashed (see the log)"
            self.log("build crashed:\n" + traceback.format_exc())
            return None
        self.error = self._said = None
        self.town = res.town
        self.data = res.data
        return res.data

    @property
    def rev(self) -> str | None:
        """The ``rev`` of the last good build (the one in ``town-data.js``)."""
        rev = (self.data or {}).get("rev")
        return rev if isinstance(rev, str) and rev else None


# ----------------------------------------------------------------------------- the Watcher
@dataclass
class LiveTick:
    payload: dict[str, Any]  # what town-live.js now holds
    busy: bool = False  # another Watcher holds the status folder: this one stood by
    changed: bool = False  # the Watcher wrote status files or events
    watched: bool = False  # the Watcher ran this tick (False: a heartbeat-only tick)


class LiveFeed:
    """One Watcher plus the live files. Thread-safe: the app ticks it on its worker thread and
    closes it from another. Use :meth:`close` (or ``with``) when done: it saves the Watcher's
    state and releases the status folder's lock.

    ``poll``: seconds between Watcher runs (``None``: the config's ``[live] poll``; ``lamptown
    watch --every`` sets it). ``mono``: the monotonic clock the Watcher's cadence is kept on."""

    def __init__(self, log: Callable[[str], None] | None = None,
                 make_watcher: Callable[..., Any] | None = None, *,
                 poll: float | None = None,
                 mono: Callable[[], float] = time.monotonic) -> None:
        self.log = log or (lambda _m: None)
        self._make = make_watcher
        self._mono = mono
        self._lock = threading.RLock()
        self.cfg: Any = None
        self.out: Path | None = None
        self.watcher: Any = None
        self.poll_override = poll
        self._key: Any = None
        self._ctx: Any = None
        self._cache: dict = {}
        self._said: set[str] = set()
        self._fail = FailLog(self.log, "watcher tick")
        self._watched: float | None = None  # when the Watcher last ran (self._mono)
        self._rev_sig: Any = None  # town-data.js's (mtime, size) when its rev was read
        self._rev_disk = ""
        self.standby = False
        self.behind = False  # the last Watcher run left something unread (L4-fix)
        self.closed = False
        self.ticks = 0
        self.watches = 0  # Watcher runs

    # ------------------------------------------------------------ config
    @property
    def enabled(self) -> bool:
        return bool(self.cfg is not None and self.cfg.live.enabled)

    @property
    def poll(self) -> float:
        """Seconds between Watcher runs: ``poll`` given, else the config's ``[live] poll``
        (default 3)."""
        try:
            v = self.poll_override if self.poll_override is not None else (
                self.cfg.live.poll if self.cfg is not None else LIVE_EVERY)
            v = float(v)
        except (AttributeError, TypeError, ValueError):
            return LIVE_EVERY
        return v if math.isfinite(v) and v > 0 else LIVE_EVERY

    @property
    def every(self) -> float:
        """Seconds from now until the next tick is due: the Watcher's next run, but never more
        than :data:`HEARTBEAT_MAX` (the heartbeat), and :data:`BEHIND_EVERY` while a reader is
        catching up."""
        poll = self.poll
        if self.behind:
            return min(poll, BEHIND_EVERY)
        if poll <= HEARTBEAT_MAX or self._watched is None:
            return min(poll, HEARTBEAT_MAX)
        left = self._watched + poll - self._mono()
        return max(SLACK, min(HEARTBEAT_MAX, left))

    def configure(self, cfg, out_dir: str | Path) -> None:
        """Follow a (re)loaded config: a new Watcher only when what it watches changed (its
        status folder, readers, private folders, place rules, ageing; not ``poll`` and not a
        building's label or text), so a rebuild never lets the status folder go."""
        with self._lock:
            if self.closed:
                return
            self.cfg, self.out = cfg, Path(out_dir)
            key = self._watch_key(cfg)
            if key == self._key:
                return
            self._close_watcher()
            self._key, self._ctx, self._cache = key, None, {}
            if key is None:
                return
            from lamptown import livetown

            self._ctx = livetown.context(cfg)
            self.watcher = self._new_watcher(cfg)

    @staticmethod
    def _watch_key(cfg) -> Any:
        """What the Watcher and the live payload's context are made from."""
        if not cfg.live.enabled:
            return None
        from lamptown import livetown

        return (dataclasses.replace(cfg.live, poll=0.0), livetown.watcher_options(cfg))

    def _new_watcher(self, cfg):
        from lamptown import livetown
        from lamptown.status.readers import make_reader

        readers = []
        for entry in cfg.live.readers:
            try:
                readers.append(make_reader(entry))
            except Exception as e:  # one broken reader must not stop the others
                name = entry.get("kind") or Path(str(entry.get("reader", "?"))).name
                self._say(f"live reader {name!r} can't start: {type(e).__name__}: {e}")
        opts = livetown.watcher_options(cfg)
        self._watched = None
        if self._make is not None:
            return self._make(readers=readers, **opts)
        from lamptown.status.watch import Watcher

        return Watcher(readers=readers, **opts)

    def _close_watcher(self) -> None:
        w, self.watcher = self.watcher, None
        self.standby = self.behind = False
        if w is not None:
            try:
                w.close()
            except Exception as e:
                self._say(f"couldn't close the watcher cleanly: {type(e).__name__}: {e}")

    def _say(self, msg: str) -> None:
        if msg not in self._said:  # each problem once, not every 3 s
            if len(self._said) >= 200:
                self._said.clear()
            self._said.add(msg)
            self.log(msg)

    def _disk_rev(self) -> str:
        """The ``rev`` in the ``town-data.js`` on disk (read again only when the file changed)."""
        from lamptown.build import DATA_JS, read_rev

        try:
            st = (self.out / DATA_JS).stat()
        except OSError:
            return ""
        sig = (st.st_mtime_ns, st.st_size)
        if sig != self._rev_sig:
            self._rev_sig, self._rev_disk = sig, read_rev(self.out)
        return self._rev_disk

    # ------------------------------------------------------------ ticks
    def _watch_due(self) -> bool:
        if self._watched is None or self.behind:
            return True
        poll = self.poll
        return poll <= HEARTBEAT_MAX or self._mono() - self._watched >= poll - SLACK

    def tick(self, rev: str | None = None, *, now=None) -> LiveTick | None:
        """Run the Watcher once when it is due and write ``town-live.js`` (always: the
        heartbeat). ``None`` when live is off, the feed is closed or the file couldn't be
        written. The payload's ``rev`` is the one in ``town-data.js`` on disk; ``rev`` (the
        caller's last build) is used only when that file has none."""
        with self._lock:
            if self.closed or not self.enabled or self.out is None:
                return None
            self.ticks += 1
            busy, changed = self.standby, False
            watched = self.watcher is not None and self._watch_due()
            if watched:
                self._watched = self._mono()
                self.watches += 1
                try:
                    res = self.watcher.tick(now)
                except Exception as e:
                    self._fail.failed(e)
                else:
                    self._fail.ok()
                    busy, changed = bool(res.busy), bool(res.changed)
                    if busy and not self.standby:
                        self.log("another Lamptown watcher is running on this status folder: "
                                 "this one stands by and takes over when it stops")
                    elif self.standby and not busy:
                        self.log("the other watcher stopped: this one is watching now")
                    self.standby = busy
                    # --- L6-fix: catch-up (L4-fix's TickResult.behind) --- a reader that left
                    # something unread makes the feed tick every BEHIND_EVERY s until it has
                    # caught up. getattr: a Watcher without the field never speeds up.
                    self.behind = bool(getattr(res, "behind", False)) and not busy
                    for err in res.errors if not busy else ():
                        self._say(f"watcher: {err}")
            from lamptown.build import write_live_files

            try:
                files = write_live_files(self.cfg, self.out, rev=self._disk_rev() or rev,
                                         now=now, ctx=self._ctx, cache=self._cache)
            except OSError as e:  # the town folder is gone or read-only: say so once, go on
                self._say(f"can't write the live files: {e}")
                return None
            for w in getattr(files, "warnings", None) or ():  # a held file, a broken log
                self._say(f"live files: {w}")
            return LiveTick(payload=files.payload, busy=busy, changed=changed, watched=watched)

    def close(self) -> None:
        """Save the Watcher's state and let the status folder go. Safe to call twice."""
        with self._lock:
            self.closed = True
            self._close_watcher()

    def __enter__(self) -> LiveFeed:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ----------------------------------------------------------------------------- lamptown watch
def watch_loop(
    feed: LiveFeed,
    rebuild: Callable[[], dict[str, Any] | None],
    *,
    rev: Callable[[], str | None] = lambda: None,
    every: Callable[[], float] | None = None,
    rebuild_every: float = REBUILD_EVERY,
    ticks: int | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] | None = None,
) -> int:
    """``lamptown watch``'s loop: a live tick every ``every()`` seconds (default: the feed's,
    which says when its next tick is due; never more than :data:`HEARTBEAT_MAX`), and a rebuild
    every ``rebuild_every`` seconds (the town was just built when it starts). It sleeps until
    whichever is due first. Stops after ``ticks`` live ticks (tests), when live goes off in the
    config, or on ``KeyboardInterrupt`` (raised on to the caller). Returns the live ticks run."""
    every = every or (lambda: feed.every)
    sleep = sleep or _sleep
    fails = FailLog(log or (lambda _m: None), "live tick")
    n = 0
    next_live = clock()
    next_build = next_live + rebuild_every
    while True:
        start = clock()
        if start >= next_live - 1e-9:
            try:
                out = feed.tick(rev())
            except Exception as e:
                fails.failed(e)
                out = None
            else:
                fails.ok()
            n += 1
            if ticks is not None and n >= ticks:
                return n
            if out is None and not feed.enabled:
                return n
            next_live = start + _cap(every())
        if clock() >= next_build - 1e-9:
            rebuild()
            next_build = clock() + rebuild_every
            if not feed.enabled:
                return n
        sleep(max(0.0, min(next_live, next_build) - clock()))


def _cap(v: Any) -> float:
    """A live cadence: a positive number of seconds, at most the heartbeat's."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return LIVE_EVERY
    if not (math.isfinite(v) and v > 0):
        return LIVE_EVERY
    return min(v, HEARTBEAT_MAX)
