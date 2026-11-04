"""The demo: a fictional world (package data) copied out, brought up to date and built.

``world/`` is a complete little setup: a ``lamptown.toml`` that uses all six room types and the
files its sources read (job logs, a notes vault, scans and calls, a contest, a skill shelf, three
projects and nine agents writing ``agent-status/1`` files), plus the live town. Every name in it
is made up.

Times in the world are written as tokens and filled in when it is copied, so the town always
looks alive:

* in file contents, ``{{-2h}}`` is an ISO time two hours ago (with the local offset);
  ``{{-1d:date}}`` a date, ``{{-1h:hm}}`` a clock time, ``{{-2d:dow}}`` a weekday (``mon``),
  ``{{-6m:epoch}}`` Unix seconds, ``{{0:tz}}`` the offset (``+0100``) and ``{{0:utc}}`` an ISO
  time in UTC. Offsets are one or more ``<n>d``, ``h``, ``m`` or ``s`` parts after a sign.
* in file names a token carries no format and is the date: ``backup-{{-6h}}.log``.
* a path part starting with ``dot-`` is copied as ``.`` (``dot-git`` -> ``.git``): a real
  ``.git`` folder can't live in the package.
* ``_mtimes.toml`` gives the copied files their modification times (offsets from now), which
  the collectors read to tell a running job, a busy project or a scan being written.

``states/<state>/`` are overlays for ``lamptown demo --state``: their files replace or add to the
world's, a ``<name>.skip`` file leaves that file or folder out, and their ``_mtimes.toml`` adds
rules. The ``missing`` state copies only the config, so every source finds nothing. Each overlay
changes the files, never the town: every lamp then shows that state because the data says so.

The live town: the world's ``[live]`` table switches it on, its agents' status files carry the
live fields (``x-project``, ``x-plot``, ``x-files``, ...), and ``_history.toml`` (not copied)
becomes the event log a Watcher would have written over the last week
(``<status_dir>/events/YYYY-MM-DD.jsonl``), so the lighthouse's book has a week in it. Its
events are dated from now too: ``-40m`` (that long ago) or ``-3d@09:12`` (three local days ago
at 09:12). The ``-40m`` kind are today's: those that would fall before local midnight (a demo
started in the small hours) are drawn together, in order, between 00:01 and the first one that
is already today, so today's page of the book is never empty. A state overlay keeps the book
in step with its files: an agent whose status file it skips leaves the book, and its own
``_history.toml`` gives the agents it changes their own events (``[[agent]]`` with ``id`` and
``events``, replacing the world's).
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import os
import re
import tempfile
import time
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

STATES = ("ok", "waiting", "working", "error", "missing")
CONFIG_NAME = "lamptown.toml"
MTIMES_NAME = "_mtimes.toml"
HISTORY_NAME = "_history.toml"  # the live town's last week, written as an event log
STATE_HISTORY = "states/_history.toml"  # plan()'s key for an overlay's own _history.toml
SKIP_SUFFIX = ".skip"
MISSING_KEEP = (CONFIG_NAME, "README.txt")  # all the `missing` state copies
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_OFFSET = r"[+-]?(?:\d+[dhms])+|[+-]?0"
TOKEN = re.compile(r"\{\{(" + _OFFSET + r")(?::(iso|utc|date|hm|dow|epoch|tz))?\}\}")
NAME_TOKEN = re.compile(r"\{\{(" + _OFFSET + r")\}\}")
_PART = re.compile(r"(\d+)([dhms])")
_UNIT = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_DAY_AT = re.compile(r"^-(\d+)d@(\d{1,2}):(\d{2})$")  # -3d@09:12: three local days ago at 09:12


class DemoError(Exception):
    """The demo can't be set up; the message says why."""


@dataclass
class DemoResult:
    root: Path  # the folder holding world/ and town/
    world: Path  # the copied world (its lamptown.toml is the config)
    town: Path  # town.html
    details: Path  # town-details.html
    state: str | None
    seconds: float  # copy + build time
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- time tokens
def offset(text: str) -> dt.timedelta:
    """``"-1d3h"`` -> minus 27 hours. Raises ValueError for anything else."""
    t = text.strip()
    if not re.fullmatch(_OFFSET, t):
        raise ValueError(f"{text!r} is not an offset like -2h, +1d or -1d30m")
    sign = -1 if t.startswith("-") else 1
    secs = sum(int(n) * _UNIT[u] for n, u in _PART.findall(t))
    return dt.timedelta(seconds=sign * secs)


def stamp(now: dt.datetime, off: str, fmt: str | None) -> str:
    t = now + offset(off)
    if fmt in (None, "iso"):
        return t.isoformat(timespec="seconds")
    if fmt == "utc":
        return t.astimezone(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    if fmt == "date":
        return t.date().isoformat()
    if fmt == "hm":
        return f"{t:%H:%M}"
    if fmt == "dow":
        return DAYS[t.weekday()]
    if fmt == "epoch":
        return str(int(t.timestamp()))
    if fmt == "tz":
        return f"{t:%z}" or "+0000"
    raise ValueError(f"unknown time format {fmt!r}")


def fill(text: str, now: dt.datetime) -> str:
    """Every ``{{offset[:format]}}`` token in a file's text, filled in."""
    return TOKEN.sub(lambda m: stamp(now, m.group(1), m.group(2)), text)


def fill_name(rel: str, now: dt.datetime) -> str:
    """A template path -> the copied path: date tokens filled in, ``dot-`` parts made hidden."""
    parts = []
    for raw in rel.split("/"):
        part = NAME_TOKEN.sub(lambda m: stamp(now, m.group(1), "date"), raw)
        if part.startswith("dot-") and len(part) > 4:
            part = "." + part[4:]
        parts.append(part)
    return "/".join(parts)


# ----------------------------------------------------------------------------- the templates
def _walk(node: Traversable, prefix: str = "") -> Iterator[tuple[str, Traversable]]:
    for child in sorted(node.iterdir(), key=lambda c: c.name):
        rel = f"{prefix}{child.name}"
        if child.is_dir():
            if child.name != "__pycache__":
                yield from _walk(child, rel + "/")
        elif not child.name.endswith((".pyc", ".pyo")):
            yield rel, child


def package_dir(name: str) -> Traversable:
    return resources.files(__name__).joinpath(name)


def _rules(src: Traversable) -> tuple[str | None, dict[str, str]]:
    try:
        data = tomllib.loads(src.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:  # a packaging mistake, not a user error
        raise DemoError(f"the demo's {MTIMES_NAME} can't be read: {e}") from e
    files = data.get("files") or {}
    return data.get("default"), {str(k): str(v) for k, v in files.items()}


def plan(state: str | None = None) -> tuple[dict[str, Traversable], str, dict[str, str]]:
    """The template files of a world (template path -> file), the default file age, and the
    file-age rules, with the state's overlay applied."""
    if state is not None and state not in STATES:
        raise DemoError(f"unknown state {state!r} (states: {', '.join(STATES)})")
    world = package_dir("world")
    if not world.is_dir():
        raise DemoError("the demo world is not installed with this copy of Lamptown")
    files: dict[str, Traversable] = {}
    default, rules = "-2d", {}
    for rel, src in _walk(world):
        if rel == MTIMES_NAME:
            d, r = _rules(src)
            default, rules = d or default, {**rules, **r}
        else:
            files[rel] = src
    if state == "missing":
        files = {k: v for k, v in files.items() if k in MISSING_KEEP}
    elif state is not None:
        over = package_dir("states").joinpath(state)
        if over.is_dir():
            for rel, src in _walk(over):
                if rel == MTIMES_NAME:
                    d, r = _rules(src)
                    default, rules = d or default, {**rules, **r}
                elif rel == HISTORY_NAME:  # amends the world's book (not copied either)
                    files[STATE_HISTORY] = src
                elif rel.endswith(SKIP_SUFFIX):
                    gone = rel[: -len(SKIP_SUFFIX)]
                    files = {k: v for k, v in files.items()
                             if k != gone and not k.startswith(gone + "/")}
                else:
                    files[rel] = src
    return files, default, rules


def file_age(rel: str, default: str, rules: dict[str, str]) -> dt.timedelta:
    """The offset from now a copied file's modification time gets (the last matching rule)."""
    age = default
    for pat, off in rules.items():
        if fnmatch.fnmatchcase(rel, pat):
            age = off
    return offset(age)


# ----------------------------------------------------------------------------- the live history
def _when(text: str, now: dt.datetime, tz: dt.tzinfo | None) -> dt.datetime:
    """``-40m`` -> now minus 40 minutes; ``-3d@09:12`` -> 09:12 local time three days ago
    (local: ``tz``, else this computer's zone, DST included)."""
    m = _DAY_AT.match(text)
    if not m:
        return now + offset(text)
    days, hh, mm = (int(g) for g in m.groups())
    day = (now.astimezone(tz) - dt.timedelta(days=days)).date()
    wall = dt.datetime(day.year, day.month, day.day, hh, mm)
    return wall.replace(tzinfo=tz) if tz is not None else wall.astimezone()


def _load_history(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise DemoError(f"the demo's {HISTORY_NAME} can't be read: {e}") from e


def _into_today(events: list[dict[str, Any]], now: dt.datetime,
                tz: dt.tzinfo | None) -> None:
    """Today's events (``-40m``) that would fall before local midnight are drawn together, in
    their order, between 00:01 and the first one that is already today (else ``now``): a demo
    started at 00:05 has today's whole story in the book, not an empty page. Changes ``t``."""
    floor = min(_when("-0d@00:01", now, tz), now)
    early = [e for e in events if e["t"] < floor]
    if not early:
        return
    top = min((e["t"] for e in events if e["t"] >= floor), default=now)
    first = min(e["t"] for e in early)
    scale = (top - floor) / (floor - first)
    for e in early:
        e["t"] = floor + (e["t"] - first) * scale


def history_events(text: str, now: dt.datetime, tz: dt.tzinfo | None = None, *,
                   state_text: str | None = None,
                   gone: frozenset[str] | set[str] = frozenset(),
                   ) -> tuple[str, list[dict[str, Any]]]:
    """``_history.toml``'s text -> (its status folder, relative to the world; the events, oldest
    first), each event in the form ``lamptown.status.events.append`` takes. Events after ``now``
    are left out. ``state_text``: a state overlay's ``_history.toml``, whose agents' events
    replace the world's; ``gone``: the world files the overlay skips (an agent whose status file
    is skipped leaves the book). A mistake in a file is a packaging bug: :class:`DemoError`."""
    from lamptown.status.events import KINDS

    data = _load_history(text)
    status = data.get("status_dir")
    if not isinstance(status, str) or not status:
        raise DemoError(f"the demo's {HISTORY_NAME} names no status_dir")
    agents = data.get("agent") or []
    ids = {a.get("id") for a in agents}
    own: dict[str, list[str]] = {}
    for agent in (_load_history(state_text).get("agent") or []) if state_text else []:
        if agent.get("id") not in ids or not isinstance(agent.get("events"), list):
            raise DemoError(f"a state's {HISTORY_NAME}: {agent.get('id')!r} needs an id from "
                            "the world's and a list of events")
        own[agent["id"]] = agent["events"]
    out: list[dict[str, Any]] = []
    today: list[dict[str, Any]] = []  # the "-40m" kind
    for agent in agents:
        if f"{status}/{agent.get('id')}.json" in gone:
            continue
        base = {k: agent[k] for k in ("id", "title", "project", "tool", "plot") if k in agent}
        for line in own.get(agent.get("id"), agent.get("events") or []):
            parts = str(line).split()
            if len(parts) not in (2, 3) or parts[1] not in KINDS:
                raise DemoError(f"the demo's {HISTORY_NAME}: {line!r} is not '<when> <kind> [n]'")
            try:
                t = _when(parts[0], now, tz)
            except ValueError as e:
                raise DemoError(f"the demo's {HISTORY_NAME}: {line!r}: {e}") from e
            event: dict[str, Any] = {"t": t, "k": parts[1], **base}
            if len(parts) == 3:
                if parts[1] == "ci":
                    event["ok"] = parts[2] == "ok"
                elif parts[2].isdigit():
                    event["n"] = int(parts[2])
                else:
                    raise DemoError(f"the demo's {HISTORY_NAME}: {line!r}: a count is a number")
            if t <= now:
                out.append(event)
                if not _DAY_AT.match(parts[0]):
                    today.append(event)
    order = {id(e): (e["t"], i) for i, e in enumerate(out)}  # ties keep the written order
    _into_today(today, now, tz)
    out.sort(key=lambda e: (e["t"], order[id(e)]))
    return status, out


def write_history(root: Path, src: Traversable, now: dt.datetime,
                  tz: dt.tzinfo | None = None, *, state_src: Traversable | None = None,
                  gone: frozenset[str] | set[str] = frozenset()) -> int:
    """Write ``_history.toml`` (with a state's own, see :func:`history_events`) as the world's
    event log; returns the number of events."""
    from lamptown.status.events import append

    status, evs = history_events(
        src.read_text(encoding="utf-8"), now, tz, gone=gone,
        state_text=state_src.read_text(encoding="utf-8") if state_src is not None else None)
    return len(append(root.joinpath(*status.split("/")), evs, tz=tz))


# ----------------------------------------------------------------------------- making a world
def _ready(dest: Path) -> None:
    if dest.exists():
        if not dest.is_dir():
            raise DemoError(f"{dest} is a file, not a folder")
        if any(dest.iterdir()):
            raise DemoError(f"{dest} is not empty: give the demo an empty or new folder")
    dest.mkdir(parents=True, exist_ok=True)


def make_world(dest: str | os.PathLike[str], now: dt.datetime | None = None,
               state: str | None = None, *, tz: dt.tzinfo | None = None) -> Path:
    """Copy the demo world into ``dest`` (new or empty), times relative to ``now``.

    Returns the copied ``lamptown.toml``. ``state`` (one of :data:`STATES`) applies that overlay.
    ``tz``: the zone the event log's days are in (default: this computer's; build the town with
    the same one).
    """
    if now is None:
        now = dt.datetime.now().astimezone().replace(microsecond=0)
    elif now.tzinfo is None:
        now = now.astimezone()
    files, default, rules = plan(state)
    history = files.pop(HISTORY_NAME, None)
    own_history = files.pop(STATE_HISTORY, None)
    gone = set(plan(None)[0]) - set(files) if state else set()  # what the overlay skipped
    root = Path(dest)
    _ready(root)
    stamps: list[tuple[Path, float]] = []
    for rel, src in files.items():
        out = root.joinpath(*fill_name(rel, now).split("/"))
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = src.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # not text: copied as it is
            out.write_bytes(src.read_bytes())
        else:
            out.write_bytes(fill(text, now).encode("utf-8"))
        stamps.append((out, (now + file_age(rel, default, rules)).timestamp()))
    if history is not None:  # before the times: the log's folder is inside the status folder
        write_history(root, history, now, tz, state_src=own_history, gone=gone)
    for out, ts in stamps:  # after every write, so a folder's later files can't touch them
        os.utime(out, (ts, ts))
    (root / ".lamptown-demo").write_text(
        f"A Lamptown demo world, made {now.isoformat(timespec='seconds')}"
        + (f" in the {state} state" if state else "") + ". Safe to delete.\n",
        encoding="utf-8")
    return root / CONFIG_NAME


def build_demo(out: str | os.PathLike[str] | None = None, *, now: dt.datetime | None = None,
               state: str | None = None,
               log: Callable[[str], None] | None = None) -> DemoResult:
    """Make the world in ``out`` (default: a new temp folder) and build its town.

    ``now`` pins the clock (screenshots, tests): the world's times, the build and the page all
    use it, and the lighthouse's days are in its zone. Without it the world is dated from this
    moment, the page follows the real clock and the days are this computer's.
    """
    from lamptown.build import build
    from lamptown.config import ConfigError, load_config

    t0 = time.perf_counter()
    if state is not None and state not in STATES:  # before any folder is made
        raise DemoError(f"unknown state {state!r} (states: {', '.join(STATES)})")
    fixed = now is not None
    if now is None:
        now = dt.datetime.now().astimezone().replace(microsecond=0)
    elif now.tzinfo is None:
        now = now.astimezone()
    root = Path(out) if out else Path(tempfile.mkdtemp(prefix="lamptown-demo-"))
    _ready(root)
    tz = now.tzinfo if fixed else None  # the lighthouse's days: the pinned moment's zone
    cfg = make_world(root / "world", now, state, tz=tz)
    try:
        config = load_config(cfg)
    except ConfigError as e:  # the shipped world is broken: a bug, say so plainly
        raise DemoError(f"the demo world's config is broken: {e}") from e
    res = build(config, out_dir=root / "town", now=now if fixed else None, log=log, tz=tz)
    return DemoResult(root=root, world=cfg.parent, town=res.town, details=res.details, state=state,
                      seconds=time.perf_counter() - t0, data=res.data, warnings=res.warnings)
