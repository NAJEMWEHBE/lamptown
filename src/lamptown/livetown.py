"""The live town's data: what Python hands the page while a Watcher runs (see docs/config.md).

Pure functions, no file writes (``build.write_live_files`` writes the files):

* :func:`live_payload` -> ``lamptown-live/1``, the body of ``town-live.js``::

    {"spec": "lamptown-live/1", "at": "2026-11-04T17:48:00.123Z", "rev": "<town data rev>",
     "need": 1, "more": 0,
     "agents": [{"id": "codex-3f9a", "tool": "codex", "title": "Fix save slots",
                 "project": "paper-kite", "plot": "boathouse", "state": "working",
                 "since": "2026-11-04T17:40:00Z", "parent": null, "subagents": 0, "files": 14,
                 "commits": 2, "pushed": true, "ci": "running", "private": false}],
     "projects": [{"id": "paper-kite", "name": "PAPER KITE", "berth": 0, "new": false}]}

  ``state`` is ``working``, ``waiting``, ``ok`` (done: shown for :data:`DONE_FOR` seconds),
  ``error`` (failed, or stopped responding) or ``quiet`` (gone quiet). ``need`` counts the
  agents waiting for you. ``projects[].id`` is the project name exactly as agents carry it in
  ``project``; ``berth`` orders them by when they were first seen.
* :func:`history_payload` -> ``lamptown-history/1``, the body of ``town-history.js``: the last
  ``history_days`` local days, oldest first, the last one today. Each event is
  ``[minute, agent index, kind]`` plus ``n`` (``files`` and ``commit`` counts) or ``ok`` (a CI
  result: 1 or 0). ``minute`` counts real minutes since local midnight, so a 23-hour DST day
  runs 0..1379 and a 25-hour one 0..1499; each day says how long it is in ``minutes``.

Names only: a title, a project folder's *name* and a tool. Paths in them are cut to their last
part, links are scrubbed, lengths are capped as for room data. A private agent (its folder, or
the status folder, in or under a ``private_dirs`` entry) has the title "Private agent", no
project, and the ``status_dir`` collector's masked id; its folder never leaves Python.

Where an agent walks (:class:`PlotMap`, the Watcher's ``plot_for``): a reader's ``x-plot``,
else ``tower`` for jobs, else the most specific folder rule that holds the record's
``x-folder``, else the harbour (``boathouse``). Folder rules come from each building's resolved
source paths (a glob with a wildcard in its file name only counts from its fixed folder; one with
a wildcard folder, such as ``<work>/*/contest.json``, counts only the folders of the files it
matches, so it never claims the sibling projects next to them) and from ``[[live.place]]``,
which wins a tie. Matching a record's folder is text only: nothing it names is opened.

A project name is never a login name: an agent started in the home folder, a drive root or
above home has no project (``events.project_of``).
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from lamptown.collectors import scrub_links
from lamptown.config import LIGHTHOUSE, PLOTS, Config, is_path_key, resolve_path
from lamptown.status.collector import (
    DEFAULT_EXPIRE_AFTER,
    DEFAULT_QUIET_AFTER,
    PRIVATE_NAME,
    _norm,
    _private_roots,
    _under,
)
from lamptown.status.events import (
    LIMITS,
    local_day,
    name_only,
    path_like,
    project_of,
    read_days,
)
from lamptown.status.schema import parse_time, validate
from lamptown.status.store import DEFAULT_STALE_AFTER, StatusError, default_dir, iso, read_status

LIVE_SPEC = "lamptown-live/1"
HISTORY_SPEC = "lamptown-history/1"
DEFAULT_PLOT = "boathouse"
JOB_PLOT = "tower"
DONE_FOR = 15 * 60  # seconds a finished agent stays (its lantern on the hook)
NEW_FOR = 10 * 60  # seconds a project counts as new (a boat slides in)
MAX_AGENTS = 200
MAX_PROJECTS = 60
MAX_DAY_AGENTS = 300
MAX_DAY_EVENTS = 4000
STATE_ORDER = ("waiting", "error", "working", "quiet", "ok")
CI_STATES = ("running", "green", "red")
COUNTED = ("files", "commit")  # history kinds that carry a count
GLOB_EVERY = 60.0  # seconds: a glob with a wildcard folder is looked at again at most this often
MAX_GLOB_HITS = 500  # files one such glob may turn into folder rules
_GLOB = re.compile(r"[*?\[]")

# Which source keys name a place work happens in, per built-in kind (anything else: none).
# "dir": a folder; "glob": a glob or folder (cut to its static prefix); "file": a file (its
# folder counts). Kinds not listed (custom ones) use :data:`GENERIC_KEYS`.
WORK_KEYS: dict[str, dict[str, str]] = {
    "skill_dirs": {"dir": "dir", "dirs": "dir", "staging_dir": "dir"},
    "staging_dir": {"dir": "dir", "staging_dir": "dir"},
    "markdown_vault": {"path": "dir"},
    "scan_glob": {"glob": "glob", "own_glob": "glob", "installed_dir": "dir", "own_dir": "dir"},
    "contest_json": {"glob": "glob", "globs": "glob", "file": "file", "files": "file"},
    "projects": {},  # project folders: read from the projects list (see _project_roots)
    "status_dir": {}, "log_glob": {}, "static": {},
}
GENERIC_KEYS = {"dir": "dir", "dirs": "dir", "path": "dir", "paths": "dir", "glob": "glob",
                "globs": "glob"}


# ----------------------------------------------------------------------------- folders
def static_prefix(pattern: str) -> str:
    """The part of a glob before its first wildcard part: ``/work/ideas/*/x.json`` ->
    ``/work/ideas``. A path with no wildcard is returned whole."""
    parts = re.split(r"([\\/]+)", pattern)
    out: list[str] = []
    for part in parts:
        if _GLOB.search(part):
            break
        out.append(part)
    return "".join(out).rstrip("\\/") or pattern[:0]


def _too_broad(root: str) -> bool:
    """A root that would hold every folder: a drive or file-system root, the home folder or
    above it."""
    parts = [p for p in root.split("/") if p]
    if not parts or (len(parts) == 1 and parts[0].endswith(":")):
        return True
    home = _norm(os.path.expanduser("~"))
    return bool(home) and (root == home or home.startswith(root.rstrip("/") + "/"))


@dataclass(frozen=True)
class Rule:
    root: str  # normalised (collector._norm)
    plot: str
    override: bool = False


def _glob_roots(pattern: str) -> list[str]:
    """The folders of the files (or the folders) a glob matches now, at most
    :data:`MAX_GLOB_HITS`. Only the config's own globs come here, never a record's path."""
    out: list[str] = []
    try:
        for i, hit in enumerate(glob.iglob(pattern, recursive=True)):
            if i >= MAX_GLOB_HITS:
                break
            root = _norm(hit if os.path.isdir(hit) else os.path.dirname(hit))
            if root and not _too_broad(root) and root not in out:
                out.append(root)
    except (OSError, ValueError, RecursionError):
        pass
    return out


@dataclass
class PlotMap:
    """Which plot a record's figure walks to. Call it with a status record (a dict).

    ``globs``: source globs with a wildcard in a folder part (``<work>/*/contest.json``). Their
    static prefix would hold every sibling project, so only the folders of the files they
    match count, looked at again at most every ``refresh`` seconds."""

    rules: list[Rule] = field(default_factory=list)
    globs: list[tuple[str, str]] = field(default_factory=list)  # (pattern, plot)
    refresh: float = GLOB_EVERY
    _found: list[Rule] = field(default_factory=list, init=False, repr=False, compare=False)
    _looked: float | None = field(default=None, init=False, repr=False, compare=False)

    def _glob_rules(self) -> list[Rule]:
        if not self.globs:
            return []
        clock = time.monotonic()
        if self._looked is None or clock - self._looked >= self.refresh:
            self._found = [Rule(r, plot) for pattern, plot in self.globs
                           for r in _glob_roots(pattern)]
            self._looked = clock
        return self._found

    def match(self, folder: Any) -> str | None:
        """The plot of the most specific rule holding ``folder`` (an override wins a tie)."""
        if not isinstance(folder, str) or not folder.strip():
            return None
        best: Rule | None = None
        for rule in (*self.rules, *self._glob_rules()):
            if not _under(folder, [rule.root]):
                continue
            if best is None or (len(rule.root), rule.override) > (len(best.root), best.override):
                best = rule
        return best.plot if best else None

    def __call__(self, doc: Mapping[str, Any]) -> str:
        plot = doc.get("x-plot")
        if isinstance(plot, str) and plot in PLOTS:
            return plot
        if doc.get("kind") == "job":
            return JOB_PLOT
        return self.match(doc.get("x-folder")) or DEFAULT_PLOT


def _roots_of(value: Any, how: str, base: Path, globs: list[str] | None = None) -> list[str]:
    """Folder roots for one source value. A glob with a wildcard in a folder part goes to
    ``globs`` instead (see :class:`PlotMap`); one with a wildcard in its file name only counts
    from its fixed folder."""
    items = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    out = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        path = str(resolve_path(item, base))
        if how == "glob" and _GLOB.search(os.path.dirname(path)):
            if globs is not None:
                globs.append(path)
            continue
        if how == "glob":
            path = static_prefix(path)
        elif how == "file":
            path = str(Path(path).parent)
        root = _norm(path)
        if root and not _too_broad(root):
            out.append(root)
    return out


def _project_roots(table: Mapping[str, Any], base: Path) -> list[str]:
    raw = table.get("projects", table.get("project"))
    out: list[str] = []
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, Mapping):
            out += _roots_of(entry.get("path"), "dir", base)
    return out


def plot_map(cfg: Config) -> PlotMap:
    """The folder rules for ``cfg`` (see the module docstring)."""
    rules: list[Rule] = []
    globs: list[tuple[str, str]] = []
    base = cfg.base_dir
    for b in cfg.buildings:
        for s in b.sources:
            # a built-in kind names its own keys; a custom kind or a local collector file is
            # read the generic way
            keys = GENERIC_KEYS if s.collector else WORK_KEYS.get(s.kind, GENERIC_KEYS)
            roots: list[str] = []
            wild: list[str] = []
            for key, how in keys.items():
                roots += _roots_of(s.table.get(key), how, base, wild)
            if s.kind == "projects":
                roots += _project_roots(s.table, base)
            if keys is GENERIC_KEYS:  # any other *_dir / *_glob key of a custom source
                for key, value in s.table.items():
                    if key in keys or key == "collector" or not is_path_key(key):
                        continue
                    if key.endswith("_dir"):
                        roots += _roots_of(value, "dir", base)
                    elif key.endswith("_glob"):
                        roots += _roots_of(value, "glob", base, wild)
            rules += [Rule(r, b.plot) for r in roots]
            globs += [(pattern, b.plot) for pattern in wild]
    for place in cfg.live.places:  # your own words: the fixed part counts, siblings and all
        root = _norm(static_prefix(place.dir))
        if root:
            rules.append(Rule(root, place.plot, override=True))
    return PlotMap(rules, globs)


# ----------------------------------------------------------------------------- status folder
def _status_sources(cfg: Config):
    for b in cfg.buildings:
        for s in b.sources:
            if s.kind == "status_dir" and not s.collector:
                yield s


def live_status_dir(cfg: Config) -> Path:
    """The status folder the Watcher and the ``status_dir`` collector share (the same text, so
    a private record gets the same masked id in a room and on the page): ``[live] status_dir``,
    else the first ``status_dir`` source's ``dir`` (or first of ``dirs``), else
    ``$AGENT_STATUS_DIR``, else ``~/.agent-status``."""
    if cfg.live.status_dir:
        return default_dir(cfg.live.status_dir)
    for s in _status_sources(cfg):
        raw = s.table.get("dir", s.table.get("dirs"))
        first = raw if isinstance(raw, str) else (raw[0] if isinstance(raw, list) and raw
                                                  and isinstance(raw[0], str) else None)
        if first:
            return default_dir(first)
    return default_dir(None)


def private_dirs(cfg: Config) -> list[str]:
    """Every private folder: ``[live] private_dirs`` plus each ``status_dir`` source's, plus the
    status folder itself when a source reading it says ``private = true``. Relative entries
    count from the config's folder (pass ``config_dir=cfg.base_dir`` with them)."""
    out = list(cfg.live.private_dirs)
    status = _norm(str(live_status_dir(cfg)))
    for s in _status_sources(cfg):
        raw = s.table.get("private_dirs") or []
        out += [raw] if isinstance(raw, str) else [x for x in raw if isinstance(x, str)]
        if s.table.get("private") is True:
            dirs = s.table.get("dirs", s.table.get("dir"))
            items = [dirs] if isinstance(dirs, str) else dirs if isinstance(dirs, list) else []
            mine = [_norm(str(default_dir(x))) for x in items if isinstance(x, str)]
            if not items or status in mine:
                out.append(str(live_status_dir(cfg)))
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def private_id(status_dir: str | os.PathLike, sid: str) -> str:
    """A private record's id: the same as ``Watcher.private_id`` and the collector's."""
    digest = hashlib.sha256(f"{Path(status_dir)}\0{sid}".encode()).hexdigest()[:10]
    return f"private-{digest}"


AGEING = {"stale_after": DEFAULT_STALE_AFTER, "quiet_after": DEFAULT_QUIET_AFTER,
          "expire_after": DEFAULT_EXPIRE_AFTER}


def ageing(cfg: Config) -> dict[str, int]:
    """``stale_after``, ``quiet_after`` and ``expire_after`` of the ``status_dir`` source that
    reads the live status folder (the first such source), so the harbour room and the live
    town age a silent agent alike. A missing or unusable value keeps the default."""
    status = _norm(str(live_status_dir(cfg)))
    out = dict(AGEING)
    for s in _status_sources(cfg):
        raw = s.table.get("dirs", s.table.get("dir"))
        items = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
        if items and status not in [_norm(str(default_dir(x))) for x in items
                                    if isinstance(x, str)]:
            continue
        for key in AGEING:
            value = s.table.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool) \
                    and math.isfinite(value) and value >= 0:
                out[key] = int(value)
        break
    return out


@dataclass
class LiveContext:
    """What the payloads need from the config, worked out once."""

    status_dir: Path
    roots: list[str]
    plot_for: PlotMap
    history_days: int = 7
    stale_after: int = DEFAULT_STALE_AFTER
    quiet_after: int = DEFAULT_QUIET_AFTER
    expire_after: int = DEFAULT_EXPIRE_AFTER

    def private(self, folder: Any, project: Any = None) -> bool:
        """In or under a private folder: the status folder, ``folder`` (``x-folder``), or
        ``project`` (``x-project``) when that is a path."""
        if not self.roots:
            return False
        return _under(str(self.status_dir), self.roots) or any(
            isinstance(p, str) and _under(p, self.roots)
            for p in (folder, project if path_like(project) else None))


def context(cfg: Config) -> LiveContext:
    dirs = private_dirs(cfg)
    roots = _private_roots({"private_dirs": dirs, "_config_dir": str(cfg.base_dir)})
    return LiveContext(status_dir=live_status_dir(cfg), roots=roots, plot_for=plot_map(cfg),
                       history_days=cfg.live.history_days, **ageing(cfg))


def watcher_options(cfg: Config) -> dict[str, Any]:
    """Keyword arguments for ``Watcher(readers=..., **watcher_options(cfg))``: the same status
    folder text, private folders, plot rules and ageing (:func:`ageing`) the build uses."""
    return {"status_dir": str(live_status_dir(cfg)), "private_dirs": private_dirs(cfg),
            "config_dir": str(cfg.base_dir), "plot_for": plot_map(cfg),
            "keep_days": cfg.live.history_days, **ageing(cfg)}


def read_records(status_dir: str | os.PathLike, cache: dict | None = None) -> list[dict]:
    """Every valid status record in the folder (hidden, temp and broken files skipped).
    ``cache`` (a dict kept between calls) re-reads only files whose size or time changed."""
    folder = Path(status_dir)
    try:
        paths = sorted(p for p in folder.iterdir() if p.suffix == ".json")
    except OSError:
        return []
    out = []
    keep: dict = {}
    for path in paths:
        if path.name.startswith("."):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        sig = (st.st_mtime_ns, st.st_size)
        hit = cache.get(path.name) if cache is not None else None
        if hit is not None and hit[0] == sig:
            doc = hit[1]
        else:
            try:
                doc = read_status(path)
            except StatusError:
                doc = None
            if doc is not None and (validate(doc) or doc.get("id") != path.stem):
                doc = None
        keep[path.name] = (sig, doc)
        if doc is not None:
            out.append(doc)
    if cache is not None:
        cache.clear()
        cache.update(keep)
    return out


# ----------------------------------------------------------------------------- live payload
def _count(value: Any) -> int:
    return min(value, 10**9) if isinstance(value, int) and not isinstance(value, bool) \
        and value > 0 else 0


def _aware(moment: datetime) -> datetime:
    return (moment if moment.tzinfo is not None else moment.astimezone()).astimezone(UTC)


def stamp(moment: datetime) -> str:
    """``at``: UTC with milliseconds, ``2026-11-04T17:48:00.123Z``."""
    return _aware(moment).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def effective(doc: Mapping[str, Any], now: datetime, *,
              stale_after: int | None = DEFAULT_STALE_AFTER,
              quiet_after: int | None = DEFAULT_QUIET_AFTER,
              expire_after: int | None = DEFAULT_EXPIRE_AFTER) -> tuple[str, datetime | None]:
    """The state the town shows (``quiet`` for gone quiet) and since when, or ``("", None)``
    for a record that is no longer shown (done long ago, or expired). Same rules as the
    ``status_dir`` collector and the Watcher: ``stale_after`` (a file's own wins; ``0`` never
    ages), ``quiet_after`` (``0``: never quiet), ``expire_after`` (``0``: kept) are the
    source's (:func:`ageing`)."""
    updated = parse_time(doc.get("updated"))
    if updated is None:
        return "", None
    raw = doc.get("state")
    age = now - updated
    expired = bool(expire_after) and age > timedelta(seconds=expire_after or 0)
    if raw == "working":
        stale = doc.get("stale_after", stale_after)
        stale = stale if isinstance(stale, int) and not isinstance(stale, bool) else 0
        started = parse_time(doc.get("started"))
        since = started if started is not None and started <= updated else updated
        if not stale:
            return "working", since
        quiet = max(quiet_after, stale) if quiet_after else 0
        if quiet and age > timedelta(seconds=quiet):
            return ("", None) if expired else ("quiet", updated + timedelta(seconds=quiet))
        if age > timedelta(seconds=stale):
            return ("", None) if expired else ("error", updated + timedelta(seconds=stale))
        return "working", since
    if raw == "ok":
        return ("ok", updated) if age <= timedelta(seconds=DONE_FOR) else ("", None)
    if raw in ("waiting", "error"):
        return ("", None) if expired else (raw, updated)
    return "", None


def _project_of(doc: Mapping[str, Any]) -> str:
    """Never the login name of an agent started in the home folder (events.project_of)."""
    return project_of(doc.get("x-project"), doc.get("x-folder"))


def _shown_name(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").upper()


def live_payload(records: Iterable[Mapping[str, Any]], cfg: Config, now: datetime, *,
                 rev: str = "", at: datetime | None = None,
                 ctx: LiveContext | None = None) -> dict[str, Any]:
    """The ``lamptown-live/1`` payload (module docstring). ``now`` ages the records; ``at``
    (default ``now``) is when the file is written: the page's heartbeat."""
    ctx = ctx or context(cfg)
    now = _aware(now)
    rows: list[dict] = []
    projects: dict[str, datetime] = {}
    for doc in records:
        if not isinstance(doc, Mapping) or not isinstance(doc.get("id"), str):
            continue
        private = ctx.private(doc.get("x-folder"), doc.get("x-project"))
        updated = parse_time(doc.get("updated"))
        if doc.get("kind") == "project":  # a berth, not an agent (as in the Watcher)
            name = "" if private else _project_of(doc) or project_of(doc.get("title"), None)
            fresh = not ctx.expire_after or (
                updated is not None and now - updated <= timedelta(seconds=ctx.expire_after))
            if name and updated and fresh:
                started = parse_time(doc.get("started"))
                first = min(started, updated) if started else updated
                projects[name] = min(projects.get(name, first), first)
            continue
        state, since = effective(doc, now, stale_after=ctx.stale_after,
                                 quiet_after=ctx.quiet_after, expire_after=ctx.expire_after)
        if not state or since is None or updated is None:
            continue
        sid = doc["id"]
        rows.append({"sid": sid, "doc": doc, "state": state, "since": since,
                     "updated": updated, "private": private})
    rows.sort(key=lambda r: (STATE_ORDER.index(r["state"]), -r["updated"].timestamp(),
                             r["sid"]))
    more = max(0, len(rows) - MAX_AGENTS)
    rows = rows[:MAX_AGENTS]
    out_id = {r["sid"]: private_id(ctx.status_dir, r["sid"]) if r["private"] else r["sid"]
              for r in rows}
    agents = []
    for r in rows:
        doc, private = r["doc"], r["private"]
        project = "" if private else _project_of(doc)
        if project:
            projects[project] = min(projects.get(project, r["since"]), r["since"])
        parent = doc.get("x-parent")
        plot = _plot(ctx, doc)
        ci = doc.get("x-ci")
        agents.append({
            "id": out_id[r["sid"]],
            "tool": name_only(doc.get("agent"), LIMITS["tool"]),
            "title": PRIVATE_NAME if private else name_only(doc.get("title"), LIMITS["title"]),
            "project": project, "plot": plot, "state": r["state"], "since": iso(r["since"]),
            "parent": out_id.get(parent) if isinstance(parent, str) else None,
            "subagents": 0,
            "files": _count(doc.get("x-files")), "commits": _count(doc.get("x-commits")),
            "pushed": bool(doc.get("x-pushed")),
            "ci": ci if ci in CI_STATES else None, "private": private,
        })
    by_id = {a["id"]: a for a in agents}
    for a in agents:
        if a["parent"] == a["id"]:
            a["parent"] = None
        if a["parent"] in by_id:
            by_id[a["parent"]]["subagents"] += 1
    order = sorted(projects.items(), key=lambda kv: (kv[1], kv[0]))[:MAX_PROJECTS]
    payload = {
        "spec": LIVE_SPEC, "at": stamp(at or now), "rev": str(rev or ""),
        "need": sum(1 for a in agents if a["state"] == "waiting"), "more": more,
        "agents": agents,
        "projects": [{"id": name, "name": _shown_name(name), "berth": i,
                      "new": now - first < timedelta(seconds=NEW_FOR)}
                     for i, (name, first) in enumerate(order)],
    }
    return scrub_links(payload)


def _plot(ctx: LiveContext, doc: Mapping[str, Any]) -> str:
    try:
        plot = ctx.plot_for(doc)
    except Exception:  # a broken rule never stops the page
        plot = DEFAULT_PLOT
    return plot if plot in PLOTS else DEFAULT_PLOT


# ----------------------------------------------------------------------------- history payload
def midnight(day: date, tz: tzinfo | None = None) -> datetime:
    """The UTC moment local ``day`` begins (``tz`` default: this computer's zone)."""
    wall = datetime(day.year, day.month, day.day)
    local = wall.replace(tzinfo=tz) if tz is not None else wall.astimezone()
    return local.astimezone(UTC)


def day_minutes(day: date, tz: tzinfo | None = None) -> int:
    """How long local ``day`` is in minutes: 1440, or 1380 / 1500 on a DST day."""
    span = midnight(day + timedelta(days=1), tz) - midnight(day, tz)
    return max(1, round(span.total_seconds() / 60))


def _agent_name(event: Mapping[str, Any]) -> str:
    if str(event.get("id", "")).startswith("private-"):
        return PRIVATE_NAME.upper()
    title = name_only(event.get("title"), LIMITS["title"])
    project = _shown_name(project_of(event.get("project"), None))  # an old line's login: gone
    if event.get("k") == "project" and project:
        return project
    parts = [p for p in (project, title.upper()) if p]
    return " / ".join(parts)[:LIMITS["project"] + LIMITS["title"] + 3]


def _history_day(day: date, events: Sequence[Mapping[str, Any]], today: date,
                 tz: tzinfo | None) -> dict:
    start, minutes = midnight(day, tz), day_minutes(day, tz)
    valid = []
    for e in events:
        t = parse_time(e.get("t")) if isinstance(e, Mapping) else None
        if t is not None and isinstance(e.get("id"), str) and isinstance(e.get("k"), str):
            valid.append((t, e))
    valid.sort(key=lambda te: te[0])
    if len(valid) > MAX_DAY_EVENTS:  # keep every state change first; file counts fill the rest
        keep = [te for te in valid if te[1]["k"] != "files"][-MAX_DAY_EVENTS:]
        room = MAX_DAY_EVENTS - len(keep)
        keep += [te for te in valid if te[1]["k"] == "files"][-room:] if room else []
        valid = sorted(keep, key=lambda te: te[0])
    index: dict[str, int] = {}
    agents: list[dict] = []
    out: list[list] = []
    for t, e in valid:
        sid = e["id"]
        if sid not in index:
            if len(agents) >= MAX_DAY_AGENTS:
                continue
            index[sid] = len(agents)
            agents.append({"id": sid, "name": "", "plot": DEFAULT_PLOT, "tool": ""})
        a = agents[index[sid]]
        name = _agent_name(e)
        if name and (not a["name"] or e["k"] != "project"):
            a["name"] = name
        plot = e.get("plot")
        if isinstance(plot, str) and plot in PLOTS:
            a["plot"] = plot
        tool = name_only(e.get("tool"), LIMITS["tool"])
        if tool:
            a["tool"] = tool
        minute = int((t.astimezone(UTC) - start).total_seconds() // 60)
        item: list = [min(max(minute, 0), minutes - 1), index[sid], e["k"]]
        if e["k"] in COUNTED and isinstance(e.get("n"), int) and not isinstance(e["n"], bool):
            item.append(e["n"])
        elif e["k"] == "ci" and isinstance(e.get("ok"), bool):
            item.append(1 if e["ok"] else 0)
        out.append(item)
    for a in agents:
        a["name"] = a["name"] or "AGENT"
    return {"date": day.isoformat(), "today": day == today, "minutes": minutes,
            "agents": agents, "events": out}


def history_payload(events: Iterable[Any], cfg: Config, now: datetime,
                    tz: tzinfo | None = None, *, at: datetime | None = None) -> dict[str, Any]:
    """The ``lamptown-history/1`` payload: ``cfg.live.history_days`` local days, oldest first,
    today last. ``events`` is either what ``events.read_days`` returns (``[(date, events)]``)
    or a flat list of events (grouped by their local day here)."""
    now = _aware(now)
    n = max(1, cfg.live.history_days)
    today = local_day(now, tz)
    days = [today - timedelta(days=i) for i in range(n - 1, -1, -1)]
    by_day: dict[date, list] = {d: [] for d in days}
    for item in events:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], date):
            if item[0] in by_day:
                by_day[item[0]].extend(item[1])
            continue
        if not isinstance(item, Mapping):
            continue
        try:
            day = local_day(item.get("t"), tz)
        except (ValueError, OverflowError):  # a time no local day holds
            continue
        if day in by_day:
            by_day[day].append(item)
    payload = {"spec": HISTORY_SPEC, "at": stamp(at or now),
               "days": [_history_day(d, by_day[d], today, tz) for d in days]}
    return scrub_links(payload)


def read_history(cfg: Config, now: datetime, tz: tzinfo | None = None,
                 ctx: LiveContext | None = None) -> list:
    """The event log's last ``history_days`` days, as :func:`history_payload` takes them."""
    ctx = ctx or context(cfg)
    return read_days(ctx.status_dir, cfg.live.history_days, now=now, tz=tz)


# ----------------------------------------------------------------------------- the lighthouse
WORDS = {"working": "working", "waiting": "waiting for you", "ok": "done", "error": "failed",
         "quiet": "gone quiet"}
KIND_WORDS = {"start": "working", "files": "working", "commit": "working", "push": "pushed",
              "ci": "CI", "wait": "waiting for you", "answer": "working", "fail": "failed",
              "quiet": "gone quiet", "done": "done", "project": "new project"}


def lighthouse_part(live: Mapping[str, Any], details: str) -> dict[str, Any]:
    """``parts.lighthouse``: the live town's own place (not one of the six plots)."""
    agents = live.get("agents") or []
    need = int(live.get("need") or 0)
    states = {a.get("state") for a in agents}
    if need:
        lamp, st, words = "amber", "waiting", "Needs you."
    elif "error" in states:
        lamp, st, words = "red", "error", "Something is wrong."
    elif "working" in states:
        lamp, st, words = "green", "working", "Running fine (running now)."
    else:
        lamp, st, words = "green", "ok", "Running fine."
    working = sum(1 for a in agents if a.get("state") == "working")
    said = f"{working} working, {need} waiting for you" if agents else "No agents right now"
    return {"id": LIGHTHOUSE, "plot": LIGHTHOUSE, "type": LIGHTHOUSE, "name": "Lighthouse",
            "label": "Lighthouse", "lamp": lamp, "state": st, "num": str(need) if need else "",
            "status": words, "result": said, "waiting": [], "configured": True,
            "links": [{"href": f"{details}#{LIGHTHOUSE}", "label": "Details"}]}


def lighthouse_today(live: Mapping[str, Any], history: Mapping[str, Any]) -> list[dict]:
    """The details page's "Lighthouse today" lines: names only, one per agent, the live ones
    first (by urgency), then today's finished ones."""
    rows: list[dict] = []
    seen: set[str] = set()
    for a in live.get("agents") or []:
        name = a.get("title") or "Agent"
        if a.get("project"):
            name = f"{a['project']}: {name}"
        rows.append({"name": name, "tool": a.get("tool") or "", "plot": a.get("plot") or "",
                     "state": WORDS.get(a.get("state"), "")})
        seen.add(str(a.get("id")))
    today = next((d for d in history.get("days") or [] if d.get("today")), None)
    if today:
        last: dict[int, str] = {}
        for e in today.get("events") or []:
            last[e[1]] = e[2]
        for i, a in enumerate(today.get("agents") or []):
            if a.get("id") in seen or i not in last:
                continue
            rows.append({"name": a["name"].title() if a["name"] != "AGENT" else "Agent",
                         "tool": a.get("tool") or "", "plot": a.get("plot") or "",
                         "state": KIND_WORDS.get(last[i], last[i])})
    return rows


def rev_of(data: Mapping[str, Any]) -> str:
    """The town data's ``rev``: a hash of everything but the build time, so it changes only
    when what the town shows changes."""
    body = {k: v for k, v in data.items() if k not in ("built", "builtIso", "fixedClock", "rev")}
    text = json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                      allow_nan=False)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:16]
