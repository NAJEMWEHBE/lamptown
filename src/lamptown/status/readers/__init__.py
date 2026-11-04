"""Readers: turn local logs and state files into ``agent-status/1`` changes and events.

A reader is the polling cousin of a hook adapter. It never writes anything itself: the Watcher
(``lamptown.status.watch``) calls it every few seconds and writes what it returns.

The contract::

    class MyReader:
        name = "my-tool"                       # stable: it keys the reader's saved state

        def poll(self, state: dict, now: datetime, budget: Budget | None = None) -> Batch:
            ...                                # read what changed since ``state`` says
            return Batch(agents=[{...}], events=[{...}])

* ``state`` is the reader's own JSON-safe dict (cursors, offsets, seen ids). The Watcher saves it
  in ``<status_dir>/.lamptown/cursors.json`` (at most every few seconds, and when it closes)
  and hands the same dict back next time. Change it in place. When ``poll`` raises, or the
  event log can't be written, the Watcher throws away the changes and the next poll sees the
  old state again; after a crash a poll may also see state a few seconds old. So a poll must be
  repeatable from any saved state: the Watcher skips events already in the log. Never put a
  path in it: key files by ``tail.cursor_key(path)`` (``tail.tail`` does).
* ``budget`` is the tick's :class:`~lamptown.status.readers.tail.Budget` (2 MB in all, 512 KB
  per file), shared by every reader: pass it to ``tail.tail``. A reader whose ``poll`` has no
  ``budget`` parameter is called as ``poll(state, now)`` (it should then use a small budget of
  its own).
* At most :data:`MAX_AGENTS` status changes and :data:`MAX_EVENTS` events per poll are taken;
  more is reported in ``TickResult.errors`` and dropped. A reader catching up stops early and
  goes on next poll (``tail`` does this through the budget).
* ``agents`` are status changes, one dict per record: ``id`` plus any of :data:`AGENT_KEYS`.
  ``at`` is when the source showed this state (default: ``now``); it becomes the record's
  ``updated``. Use the same ids as the hooks (``adapters.make_id``), so one session is one
  record. When the record on disk was written by someone else (a hook) at or after ``at``
  (the same second counts: a hook fires just after the log line), the change is merged
  *partly*: its :data:`GUARDED` fields (state, names, needs) are held back and the record keeps
  its ``updated``; :data:`COUNTERS` still merge (the larger count wins, always: counts never go
  down), :data:`FILL_ONLY` fields fill in only when missing, and the other ``x-`` fields
  (which hooks never write) merge as sent. A record the Watcher wrote itself in the same
  second is simply updated.
* ``events`` are things a status file can't show by itself: history found while catching up
  on a log, commits, pushes. Each is an event dict (see ``lamptown.status.events``) with its
  real time in ``t``. State changes (start, wait, done, ...) need no event: the Watcher sees
  them in the status folder and writes them itself. An event may carry ``folder``: the full
  path of the working folder it belongs to. It is never written; the Watcher uses it for
  ``private_dirs`` and, when ``project`` is empty, for the project name. **Set ``folder`` on
  every event whose ``id`` has no status record** (a git reader's commits, say): with
  ``private_dirs`` configured, such an event without ``folder`` can't be checked and is
  written masked ("Private agent", no project, a hashed id). Masked ids are the
  ``status_dir`` collector's (``private-`` + a hash of the status folder and the id).

Everything a reader returns goes through :func:`clean_agent` / :func:`clean_event`: unknown keys
are dropped, text is cut to length, paths are cut to their last part (``x-folder`` alone keeps
the full path, for ``private_dirs``) and the project name always comes from
``adapters.folder_name``.

Where a reader comes from, mirroring ``collectors.find_collector``: the built-in table
:data:`BUILTIN`, then the entry-point group ``lamptown.readers``; ``reader = "./mine.py"``
loads a local file instead. Each target is a factory ``make_reader(options) -> reader``.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from lamptown.status.adapters import folder_path
from lamptown.status.collector import safe_href
from lamptown.status.events import clean_event, name_only, project_name, scrub
from lamptown.status.schema import parse_time
from lamptown.status.store import KINDS, STATES, StatusError, check_id, iso

ENTRY_GROUP = "lamptown.readers"
FACTORY = "make_reader"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
CI_STATES = ("running", "green", "red")
MAX_AGENTS = 500  # per reader and tick
MAX_EVENTS = 5000

# Built-in kinds: "module:factory", imported only when used. The public readers (claude-code,
# codex, git, ci_gh) arrive in later tickets.
BUILTIN: dict[str, str] = {}

# What a reader may set on a record. ``None`` as a value removes an optional field.
AGENT_KEYS = ("at", "title", "state", "kind", "agent", "message", "needs_you", "started",
              "progress", "stale_after", "x-folder", "x-project", "x-plot", "x-parent",
              "x-files", "x-commits", "x-pushed", "x-ci")
REMOVABLE = ("message", "needs_you", "progress", "stale_after", "x-plot", "x-parent", "x-ci",
             "x-pushed", "x-files", "x-commits")
# How a change merges into a record someone else wrote at or after its ``at`` (see above).
GUARDED = ("title", "state", "kind", "agent", "message", "needs_you", "started", "progress",
           "stale_after")  # held back
COUNTERS = ("x-files", "x-commits")  # the larger count wins, always
FILL_ONLY = ("x-folder",)  # hooks write it too: a reader only fills it in


class ReaderError(Exception):
    """A reader could not be found or loaded; the message is short and safe to show."""


@dataclass
class Batch:
    """What one ``poll`` found: status changes and extra events."""

    agents: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)


@runtime_checkable
class Reader(Protocol):
    name: str

    def poll(self, state: dict, now: datetime) -> Batch: ...


Factory = Callable[[dict], Any]  # returns a Reader; checked by make_reader


# ---------------------------------------------------------------- sanitiser
def _at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.astimezone()
    else:
        moment = parse_time(value)
    return moment.astimezone(UTC).replace(microsecond=0) if moment else None


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return min(value, 10**9)


def _needs(items: Any) -> list[dict] | None:
    if not isinstance(items, list):
        return None
    out = []
    for item in items[:20]:
        text = name_only(item.get("text"), 300) if isinstance(item, Mapping) else ""
        if not text:
            continue
        need: dict[str, Any] = {"text": text}
        href = safe_href(item.get("href"))
        if href:
            need["href"] = scrub(href)
        out.append(need)
    return out


def _started(value: Any) -> str | None:
    moment = _at(value)
    return iso(moment) if moment else None


def _id(value: Any) -> str | None:
    try:
        return check_id(value)
    except StatusError:
        return None


def clean_agent(raw: Mapping[str, Any]) -> dict | None:
    """The safe form of one status change from a reader, or ``None`` without a usable ``id``.

    Returns ``{"id": ..., "at": datetime | None, <changes>}``. Unknown keys and bad values are
    dropped; a ``None`` value for one of :data:`REMOVABLE` stays ``None`` (the store removes
    that field)."""
    if not isinstance(raw, Mapping):
        return None
    sid = _id(raw.get("id"))
    if sid is None:
        return None
    out: dict[str, Any] = {"id": sid, "at": _at(raw.get("at"))}
    for key in AGENT_KEYS[1:]:
        if key not in raw:
            continue
        value = raw[key]
        if value is None:
            if key in REMOVABLE:
                out[key] = None
            continue
        clean = _clean_value(key, value)
        if clean is not None and clean != "":
            out[key] = clean
    if "x-project" not in out and out.get("x-folder"):
        name = project_name(out["x-folder"])
        if name:
            out["x-project"] = name
    return out


def _progress(value: Any) -> dict | None:
    if not isinstance(value, Mapping):
        return None
    done, total = _count(value.get("done")), _count(value.get("total"))
    if done is None or total is None or done > total:
        return None
    return {"done": done, "total": total}


def _plot(value: Any) -> str | None:
    return _PLOT_RE.sub("", value.lower())[:32] if isinstance(value, str) else None


_PLOT_RE = re.compile(r"[^a-z0-9_-]+")
_CLEANERS: dict[str, Callable[[Any], Any]] = {
    "title": lambda v: name_only(v, 200),
    "agent": lambda v: name_only(v, 64),
    "message": lambda v: name_only(v, 200),
    "state": lambda v: v if isinstance(v, str) and v in STATES else None,
    "kind": lambda v: v if isinstance(v, str) and v in KINDS else None,
    "needs_you": _needs,
    "started": _started,
    "progress": _progress,
    "stale_after": _count,
    "x-folder": lambda v: scrub(folder_path(v)),
    "x-project": project_name,
    "x-plot": _plot,
    "x-parent": _id,
    "x-files": _count,
    "x-commits": _count,
    "x-pushed": lambda v: v if isinstance(v, bool) else None,
    "x-ci": lambda v: v if isinstance(v, str) and v in CI_STATES else None,
}


def _clean_value(key: str, value: Any) -> Any:
    return _CLEANERS[key](value)


__all__ = [
    "AGENT_KEYS", "BUILTIN", "COUNTERS", "ENTRY_GROUP", "FILL_ONLY", "GUARDED", "MAX_AGENTS",
    "MAX_EVENTS", "Batch", "Reader", "ReaderError", "clean_agent", "clean_event", "find_reader",
    "make_reader",
]  # clean_event is re-exported from lamptown.status.events


# ---------------------------------------------------------------- registry
def _load_target(target: str) -> Factory:
    mod_name, _, attr = target.partition(":")
    fn = getattr(importlib.import_module(mod_name), attr or FACTORY)
    if not callable(fn):
        raise ReaderError(f"{target} is not callable")
    return fn


def entry_point_readers() -> dict[str, metadata.EntryPoint]:
    """Installed third-party readers by kind (the entry-point group ``lamptown.readers``)."""
    try:
        eps = metadata.entry_points(group=ENTRY_GROUP)
    except Exception:  # a broken installed distribution must not stop the town
        return {}
    return {ep.name: ep for ep in eps}


def load_local(path: str | Path) -> Factory:
    """Import ``make_reader`` from a local ``.py`` file."""
    p = Path(path)
    if not p.is_file():
        raise ReaderError(f"reader file not found: {p.name}")
    mod_name = "lamptown_reader_" + hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(mod_name, p)
    if spec is None or spec.loader is None:
        raise ReaderError(f"can't load {p.name} as Python")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(mod_name, None)
        raise
    fn = getattr(mod, FACTORY, None)
    if not callable(fn):
        raise ReaderError(f"{p.name} has no {FACTORY}(options) function")
    return fn


def find_reader(kind: str, local: str | Path | None = None) -> Factory:
    """The factory for a reader kind (or a local file). Raises :class:`ReaderError`."""
    if local:
        return load_local(local)
    if kind in BUILTIN:
        try:
            return _load_target(BUILTIN[kind])
        except ImportError as e:
            raise ReaderError(f"reader kind {kind!r} is not available in this install ({e})") from e
    ep = entry_point_readers().get(kind)
    if ep is None:
        raise ReaderError(
            f"unknown reader kind {kind!r} (not built in, and no installed package registers it "
            f"under {ENTRY_GROUP!r})"
        )
    fn = ep.load()
    if not callable(fn):
        raise ReaderError(f"entry point {kind!r} is not callable")
    return fn


def make_reader(entry: Mapping[str, Any]) -> Reader:
    """Build a reader from one config entry: ``{"kind": ..., "reader": path?, ...options}``.

    The options (every key but ``kind`` and ``reader``) go to the factory as a dict."""
    kind = entry.get("kind")
    local = entry.get("reader")
    if not local and not isinstance(kind, str):
        raise ReaderError("a reader needs kind = \"<name>\" or reader = \"./file.py\"")
    options = {k: v for k, v in entry.items() if k not in ("kind", "reader")}
    reader = find_reader(kind if isinstance(kind, str) else "", local)(options)
    name = getattr(reader, "name", None)
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ReaderError(f"reader {kind or local!s} has no valid name (lower case, digits, -_.)")
    if not callable(getattr(reader, "poll", None)):
        raise ReaderError(f"reader {name!r} has no poll(state, now) method")
    return reader

