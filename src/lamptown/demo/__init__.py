"""The demo: a fictional world (package data) copied out, brought up to date and built.

``world/`` is a complete little setup: a ``lamptown.toml`` that uses all six room types and the
files its sources read (job logs, a notes vault, scans and calls, a contest, a skill shelf, three
projects and five agents writing ``agent-status/1`` files). Every name in it is made up.

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
SKIP_SUFFIX = ".skip"
MISSING_KEEP = (CONFIG_NAME, "README.txt")  # all the `missing` state copies
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_OFFSET = r"[+-]?(?:\d+[dhms])+|[+-]?0"
TOKEN = re.compile(r"\{\{(" + _OFFSET + r")(?::(iso|utc|date|hm|dow|epoch|tz))?\}\}")
NAME_TOKEN = re.compile(r"\{\{(" + _OFFSET + r")\}\}")
_PART = re.compile(r"(\d+)([dhms])")
_UNIT = {"d": 86400, "h": 3600, "m": 60, "s": 1}


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


# ----------------------------------------------------------------------------- making a world
def _ready(dest: Path) -> None:
    if dest.exists():
        if not dest.is_dir():
            raise DemoError(f"{dest} is a file, not a folder")
        if any(dest.iterdir()):
            raise DemoError(f"{dest} is not empty: give the demo an empty or new folder")
    dest.mkdir(parents=True, exist_ok=True)


def make_world(dest: str | os.PathLike[str], now: dt.datetime | None = None,
               state: str | None = None) -> Path:
    """Copy the demo world into ``dest`` (new or empty), times relative to ``now``.

    Returns the copied ``lamptown.toml``. ``state`` (one of :data:`STATES`) applies that overlay.
    """
    if now is None:
        now = dt.datetime.now().astimezone().replace(microsecond=0)
    elif now.tzinfo is None:
        now = now.astimezone()
    files, default, rules = plan(state)
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
    use it. Without it the world is dated from this moment and the page follows the real clock.
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
    cfg = make_world(root / "world", now, state)
    try:
        config = load_config(cfg)
    except ConfigError as e:  # the shipped world is broken: a bug, say so plainly
        raise DemoError(f"the demo world's config is broken: {e}") from e
    res = build(config, out_dir=root / "town", now=now if fixed else None, log=log)
    return DemoResult(root=root, world=cfg.parent, town=res.town, details=res.details, state=state,
                      seconds=time.perf_counter() - t0, data=res.data, warnings=res.warnings)
