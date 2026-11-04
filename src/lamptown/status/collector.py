"""The ``status_dir`` collector: every ``agent-status/1`` file in one or more folders.

```toml
[[building.source]]
kind = "status_dir"
dir = "~/.agent-status"          # or dirs = [...]; default $AGENT_STATUS_DIR, then ~/.agent-status
kinds = ["task", "project"]      # optional; default by room: jobs -> job, harbour -> task + project
stale_after = 1800               # optional; for files that don't set their own
quiet_after = 21600              # optional; a silent `working` file turns grey "gone quiet"
hide_after = 86400               # optional; ok files older than this are left out (0 = never)
expire_after = 604800            # optional; waiting, error and quiet files older than this too
private = true                   # optional; every row shows as "Private agent", no text
private_dirs = ["~/private"]     # optional; sessions in or under these show as private
```

Read-only. How a lamp ages (``updated`` = the file's last write):

* ``working`` silent for longer than ``stale_after`` (default 30 min; a file may set its own)
  reads as ``error`` "Stopped responding": something probably crashed.
* ``working`` silent for longer than ``quiet_after`` (default 6 h) reads as grey ``missing``
  "Gone quiet": no longer a red alert, and it no longer sets the building's lamp.
* ``ok`` older than ``hide_after`` (default 1 day) is left out.
* ``waiting``, ``error`` and quiet files older than ``expire_after`` (default 7 days) are left
  out; ``lamptown status clear`` tidies the folder itself.

``0`` switches a rule off. Links and need links keep only ``https``, ``http`` and ``file`` schemes.
A file that can't be read or isn't a valid status file shows as one error row (left out, too,
once the file is older than ``expire_after``). At most :data:`MAX_ROWS` rows and
:data:`MAX_NEEDS` needs come out, newest first, then one "and N more" entry.

Private: with ``private = true`` every file of this source, and with ``private_dirs`` every file
whose session folder (``x-folder``, written by the hooks and ``status run``) or whose status
folder is in or under one of them, shows as "Private agent" with no message, needs text or links.
Paths are compared as text; nothing in them is opened.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lamptown.status.schema import parse_time, validate
from lamptown.status.store import (
    DEFAULT_STALE_AFTER,
    KINDS,
    StatusError,
    default_dir,
    iso,
    read_status,
)

ALLOWED_SCHEMES = ("https:", "http:", "file:")
WORST_FIRST = ("error", "waiting", "working", "missing", "ok")
DEFAULT_HIDE_AFTER = 86400
DEFAULT_QUIET_AFTER = 6 * 3600
DEFAULT_EXPIRE_AFTER = 7 * 86400
ROOM_KINDS = {"jobs": ("job",), "harbour": ("task", "project")}
MAX_ROWS = 500  # room schemas allow 2000 per building; leave room for other sources
MAX_NEEDS = 200  # room schemas allow 500
PRIVATE_NAME = "Private agent"


@dataclass
class Record:
    """One status file as a reader sees it, after the stale rule."""

    id: str
    path: Path
    title: str
    state: str
    kind: str = "task"
    agent: str | None = None
    message: str = ""
    updated: datetime | None = None
    needs: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    progress: dict | None = None
    started: datetime | None = None
    next_run: datetime | None = None
    stale: bool = False
    quiet: bool = False
    invalid: bool = False
    folder: str = ""  # x-folder: the session's folder, only for private_dirs
    project_path: str = ""  # L3-fix: x-project when it is a path; only for private_dirs too
    written: datetime | None = None  # the file's own time (for invalid files)
    private: bool = False


def safe_href(href: Any) -> str | None:
    """Return ``href`` when its scheme is allowed, else ``None``."""
    if not isinstance(href, str):
        return None
    text = href.strip()
    return text if text.lower().startswith(ALLOWED_SCHEMES) and len(text) <= 2048 else None


def _clean_links(items: Any, text_key: str) -> list[dict]:
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get(text_key), str):
            continue
        entry = {text_key: item[text_key][:300]}
        href = safe_href(item.get("href"))
        if href:
            entry["href"] = href
        elif text_key == "label":
            continue  # a link without a usable href is no link
        out.append(entry)
    return out


def _as_aware(now: datetime) -> datetime:
    return now if now.tzinfo is not None else now.astimezone()


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except (OSError, OverflowError, ValueError):
        return None


def read_record(path: Path, now: datetime, stale_default: int | None,
                quiet_after: int | None = DEFAULT_QUIET_AFTER) -> Record:
    """Read one file into a :class:`Record`; problems become an invalid error record."""
    now = _as_aware(now)
    try:
        doc = read_status(path)
        problems = validate(doc)
    except StatusError as exc:
        problems, doc = [str(exc)], {}
    if problems:
        return Record(id=path.stem, path=path, title=path.name, state="error",
                      message=f"Not a valid status file: {problems[0]}"[:500], invalid=True,
                      written=_mtime(path))
    updated = parse_time(doc["updated"])
    folder = doc.get("x-folder")
    rec = Record(
        id=doc["id"], path=path, title=doc["title"], state=doc["state"],
        kind=doc.get("kind", "task"), agent=doc.get("agent"), message=doc.get("message", ""),
        updated=updated, needs=_clean_links(doc.get("needs_you"), "text"),
        links=_clean_links(doc.get("links"), "label"), progress=doc.get("progress"),
        started=parse_time(doc.get("started")), next_run=parse_time(doc.get("next_run")),
        folder=folder if isinstance(folder, str) and len(folder) <= 1024 else "",
    )
    project = doc.get("x-project")  # --- L3-fix: an x-project path counts like a folder ---
    if isinstance(project, str) and len(project) <= 1024 and ("/" in project or "\\" in project
                                                              or project.startswith("~")):
        rec.project_path = project
    stale_after = doc.get("stale_after", stale_default)
    if rec.state != "working" or updated is None or not stale_after:
        return rec  # a file (or source) with stale_after = 0 never ages
    silent = now - updated
    if quiet_after and silent > timedelta(seconds=max(quiet_after, stale_after)):
        rec.state, rec.stale, rec.quiet = "missing", True, True
        rec.message = ("Gone quiet" + (f" (last: {rec.message})" if rec.message else ""))[:500]
        return rec
    if silent > timedelta(seconds=stale_after):
        rec.state, rec.stale = "error", True
        rec.message = ("Stopped responding" + (f" (last: {rec.message})" if rec.message else ""))
        rec.message = rec.message[:500]
    return rec


def scan(
    dirs: list[Path], now: datetime, *, stale_after: int | None = DEFAULT_STALE_AFTER,
    quiet_after: int | None = DEFAULT_QUIET_AFTER,
) -> tuple[list[Record], list[Path]]:
    """Read every ``*.json`` status file in ``dirs``. Returns (records, missing folders).

    Hidden files and temp files are skipped. When two files share an id, the newest wins.
    """
    found: dict[str, Record] = {}
    missing: list[Path] = []
    for folder in dirs:
        try:
            names = sorted(p for p in folder.iterdir() if p.suffix == ".json")
        except OSError:
            missing.append(folder)
            continue
        for path in names:
            if path.name.startswith(".") or not path.is_file():
                continue
            rec = read_record(path, now, stale_after, quiet_after)
            key = rec.id if not rec.invalid else f"\0{path}"
            old = found.get(key)
            if old is None or (rec.updated and (old.updated is None or rec.updated > old.updated)):
                found[key] = rec
    return list(found.values()), missing


def worst(states) -> str:
    seen = set(states)
    return next((s for s in WORST_FIRST if s in seen), "ok")


def _expand(raw: Any, base: Path | None = None) -> Path:
    p = Path(os.path.expandvars(str(raw))).expanduser()
    if base is not None and not p.is_absolute():
        p = base / p
    return p


def _dirs(paths: dict) -> list[Path]:
    raw = paths.get("dirs", paths.get("dir"))
    if raw is None:
        return [default_dir()]
    items = [raw] if isinstance(raw, str | os.PathLike) else list(raw)
    return [_expand(item) for item in items]


def _seconds(paths: dict, key: str, default: int | None) -> int | None:
    value = paths.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ValueError(f"{key} must be a number of seconds, 0 or more")
    return int(value)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------- private
def _norm(text: str) -> str:
    """Compare paths as text: no file system access (a path in a status file is not trusted)."""
    flat = text.replace("\\", "/").rstrip("/")
    return os.path.normcase(os.path.normpath(flat)).replace("\\", "/") if flat else ""


def _under(path: str, roots: list[str]) -> bool:
    p = _norm(path)
    return bool(p) and any(p == r or p.startswith(r.rstrip("/") + "/") for r in roots)


def _private_roots(paths: dict) -> list[str]:
    raw = paths.get("private_dirs") or []
    if isinstance(raw, str | os.PathLike):
        raw = [raw]
    base = Path(str(paths.get("_config_dir") or "."))
    roots: list[str] = []
    for item in raw:
        p = _expand(item, base)
        roots.append(_norm(str(p)))
        try:  # the config's own folders are trusted: their real path counts too
            real = _norm(str(p.resolve()))
        except (OSError, RuntimeError):
            continue
        if real not in roots:
            roots.append(real)
    return [r for r in roots if r]


def _mask(rec: Record) -> Record:
    """A private record: nothing of its name, folder, message, needs or links is kept."""
    digest = hashlib.sha256(f"{rec.path.parent}\0{rec.id}".encode()).hexdigest()[:10]
    needs = [{"text": "Needs you"}] if rec.needs else []  # town sign: "Private agent: ..."
    message = ("Not a valid status file" if rec.invalid
               else {"error": "Error", "waiting": "Needs you"}.get(rec.state, ""))
    return replace(rec, id=f"private-{digest}", title=PRIVATE_NAME, message=message,
                   needs=needs, links=[], folder="", private=True)


# ---------------------------------------------------------------- rows
# Row limits follow the room schemas (src/lamptown/schemas/rooms/{harbour,jobs}.schema.json).
def _why(rec: Record) -> str | None:
    if rec.quiet:
        return "quiet"
    if rec.stale:
        return "stopped"
    return "invalid" if rec.invalid else None


def _harbour_row(rec: Record) -> dict:
    row = {
        # the agent's own words go in "message"; "status" is a project's stage (active, paused,
        # closed), which a status file does not have, so agent berths leave it out (= active)
        "id": rec.id[:120], "name": _cut(rec.title, 120), "message": _cut(rec.message, 300),
        "state": rec.state, "kind": rec.kind,
        "progress": rec.progress, "needs": rec.needs, "busy": rec.state == "working",
        "updated": iso(rec.updated) if rec.updated else None,
        "links": [{**link, "label": _cut(link["label"], 80)} for link in rec.links],
    }
    if rec.agent:
        row["agent"] = rec.agent[:80]
    if _why(rec):
        row["why"] = _why(rec)
    if rec.private:
        row["private"] = True
    return row


def _jobs_row(rec: Record) -> dict:
    row = {
        "id": rec.id[:120], "name": _cut(rec.title, 120),
        "next": iso(rec.next_run) if rec.next_run else None,
        "last": iso(rec.updated) if rec.updated else None,
        "state": rec.state, "result": _cut(rec.message, 300),
    }
    if rec.links:
        row["href"] = rec.links[0]["href"]
    if _why(rec):
        row["why"] = _why(rec)
    return row


def _more_row(room: str, n: int) -> dict:
    text = f"and {n} more"
    note = f"{n} older status files are not shown (lamptown status clear tidies the folder)"
    if room == "jobs":
        return {"id": "more", "name": text, "state": "ok", "result": note, "why": "more"}
    return {"id": "more", "name": text, "state": "ok", "message": note, "kind": "task",
            "busy": False, "needs": [], "links": [], "why": "more"}


def _expired(rec: Record, now: datetime, hide_after: int | None,
             expire_after: int | None) -> bool:
    when = rec.updated or rec.written
    if when is None or rec.state == "working":
        return False  # still "working" here means fresh, or a file that opted out of ageing
    limit = hide_after if rec.state == "ok" and not rec.invalid else expire_after
    return bool(limit) and now - when > timedelta(seconds=limit)


def _newest_first(rec: Record) -> float:
    when = rec.updated or rec.written
    return -(when.timestamp() if when else 0.0)


def collect(paths: dict, now: datetime) -> dict:
    """Collector entry point (see ``docs/collectors.md``)."""
    now = _as_aware(now)
    room = paths.get("_room", "harbour")
    kinds = paths.get("kinds")
    if isinstance(kinds, str):
        kinds = [kinds]
    kinds = tuple(kinds) if kinds else ROOM_KINDS.get(room, KINDS)
    stale_after = _seconds(paths, "stale_after", DEFAULT_STALE_AFTER)
    quiet_after = _seconds(paths, "quiet_after", DEFAULT_QUIET_AFTER)
    hide_after = _seconds(paths, "hide_after", DEFAULT_HIDE_AFTER)
    expire_after = _seconds(paths, "expire_after", DEFAULT_EXPIRE_AFTER)
    all_private = bool(paths.get("private"))
    private_roots = _private_roots(paths)
    dirs = _dirs(paths)

    records, missing = scan(dirs, now, stale_after=stale_after, quiet_after=quiet_after)
    if len(missing) == len(dirs):
        return {"state": "missing", "rows": [], "label": "no status folder yet"}

    shown = []
    for rec in records:
        if rec.kind not in kinds and not (rec.invalid and "task" in kinds):
            continue  # unreadable files show once, in the building that reads tasks
        if _expired(rec, now, hide_after, expire_after):
            continue
        private = all_private or (private_roots and (
            _under(rec.folder, private_roots) or _under(str(rec.path.parent), private_roots)
            or _under(rec.project_path, private_roots)))  # L3-fix
        shown.append(_mask(rec) if private else rec)

    # cap before anything is shaped, so a crowded folder never breaks the room's schema
    shown.sort(key=_newest_first)
    more = max(0, len(shown) - MAX_ROWS)
    if more:
        more += 1  # the "and N more" row takes one place
        shown = shown[: MAX_ROWS - 1]
    shown.sort(key=lambda r: (WORST_FIRST.index(r.state), r.title.lower(), r.id))

    shape = _jobs_row if room == "jobs" else _harbour_row
    rows = [shape(r) for r in shown]
    if more:
        rows.append(_more_row(room, more))
    needs = [
        {"text": f"{rec.title}: {need['text']}"[:300],
         **({"href": need["href"]} if "href" in need else {})}
        for rec in sorted(shown, key=_newest_first) for need in rec.needs
    ]
    if len(needs) > MAX_NEEDS:
        extra = len(needs) - (MAX_NEEDS - 1)
        needs = needs[: MAX_NEEDS - 1] + [{"text": f"and {extra} more"}]

    lit = [r for r in shown if not r.quiet]  # quiet files are grey and don't set the lamp
    state = worst(r.state for r in lit) if lit else ("missing" if shown else "ok")
    counts = {s: sum(1 for r in lit if r.state == s) for s in WORST_FIRST}
    parts = [f"{n} {s}" for s, n in counts.items() if n]
    quiet = len(shown) - len(lit)
    if quiet:
        parts.append(f"{quiet} quiet")
    if more:
        parts.append(f"{more} more")
    out: dict[str, Any] = {"state": state, "rows": rows,
                           "label": _cut(", ".join(parts) or "no agents", 120)}
    if needs:
        out["needs"] = needs
    stamps = [r.updated for r in shown if r.updated]
    if stamps:
        out["updated"] = iso(max(stamps))
    # "error" on a room means "couldn't read its files" (the room says so on a banner): only
    # unreadable files set it. An agent that failed or stopped responding is its own red row.
    unreadable = [r for r in shown if r.invalid]
    if unreadable:
        out["error"] = f"{unreadable[0].title}: {unreadable[0].message}"[:200]
    return out
