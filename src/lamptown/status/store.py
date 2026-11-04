"""Read and write ``agent-status/1`` files: one ``<id>.json`` per agent, task or job.

Writes are atomic (temp file in the same folder, then rename), so a reader never sees a
half-written file. Standard library only, no network.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lamptown.status.schema import validate

SPEC = "agent-status/1"
ENV_DIR = "AGENT_STATUS_DIR"
DEFAULT_DIR = "~/.agent-status"
MAX_BYTES = 64 * 1024
DEFAULT_STALE_AFTER = 1800
STATES = ("ok", "waiting", "working", "error")
KINDS = ("task", "job", "project")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
                     *(f"lpt{i}" for i in range(10))}


class StatusError(ValueError):
    """A status file or a status change is not valid."""


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(moment: datetime) -> str:
    """Absolute ISO 8601 text with an offset (UTC is written as ``Z``)."""
    if moment.tzinfo is None:
        moment = moment.astimezone()
    text = moment.replace(microsecond=0).isoformat()
    return text[:-6] + "Z" if text.endswith("+00:00") else text


def default_dir(override: str | os.PathLike | None = None) -> Path:
    """The status folder: ``override``, else ``$AGENT_STATUS_DIR``, else ``~/.agent-status``."""
    chosen = override or os.environ.get(ENV_DIR) or DEFAULT_DIR
    return Path(os.path.expandvars(str(chosen))).expanduser()


def check_id(status_id: str) -> str:
    if not isinstance(status_id, str) or not ID_RE.match(status_id):
        raise StatusError(
            f"bad id {status_id!r}: use letters, digits, '.', '-' or '_' (max 128), "
            "starting with a letter or digit"
        )
    if status_id.split(".", maxsplit=1)[0].lower() in _WINDOWS_RESERVED:
        raise StatusError(f"bad id {status_id!r}: reserved file name on Windows")
    return status_id


def slug(text: str, limit: int = 48) -> str:
    """Turn any text into an id-safe fragment (may be empty)."""
    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return out[:limit].strip("-")


def status_path(status_id: str, directory: str | os.PathLike | None = None) -> Path:
    return default_dir(directory) / f"{check_id(status_id)}.json"


def atomic_write_json(path: Path, data: Any, *, max_bytes: int = MAX_BYTES) -> None:
    """Write ``data`` as UTF-8 JSON to ``path`` through a temp file and an atomic rename.

    ``max_bytes`` caps the file (status files: :data:`MAX_BYTES`; the Watcher's own state files
    pass a bigger cap)."""
    path = Path(path)
    payload = (json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    if len(payload) > max_bytes:
        raise StatusError(f"status file would be {len(payload)} bytes; the limit is {max_bytes}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        # On Windows a reader holding the target open makes replace fail for a moment.
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.025)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_status(path: str | os.PathLike) -> dict:
    """Read one status file. Raises :class:`StatusError` when it is too big or not JSON.

    This does not validate the content; call :func:`lamptown.status.validate` for that.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
        if size > MAX_BYTES:
            raise StatusError(f"file is {size} bytes; the limit is {MAX_BYTES}")
        text = path.read_bytes().decode("utf-8-sig")
        doc = json.loads(text)
    except StatusError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise StatusError(f"unreadable: {type(exc).__name__}") from exc
    if not isinstance(doc, dict):
        raise StatusError("not a JSON object")
    return doc


def write_status(doc: dict, directory: str | os.PathLike | None = None) -> Path:
    """Validate a complete status document and write it to ``<dir>/<id>.json``.

    ``spec`` and ``updated`` are filled in when missing.
    """
    doc = dict(doc)
    doc.setdefault("spec", SPEC)
    doc.setdefault("updated", iso(utcnow()))
    problems = validate(doc)
    if problems:
        raise StatusError("; ".join(problems))
    path = status_path(doc["id"], directory)
    atomic_write_json(path, doc)
    return path


def update_status(
    status_id: str,
    directory: str | os.PathLike | None = None,
    *,
    now: datetime | None = None,
    **changes: Any,
) -> dict:
    """Merge ``changes`` into the existing file (if any) and write it back atomically.

    A change whose value is ``None`` removes that field. ``updated`` is set to ``now``.
    A new file gets ``title = id`` and ``state = "working"`` unless given. Returns the
    document that was written. Concurrent writers: last one wins, never a torn file.
    """
    path = status_path(status_id, directory)
    doc: dict = {}
    if path.exists():
        try:
            old = read_status(path)
            if not validate(old):
                doc = old
        except StatusError:
            doc = {}
    for key, value in changes.items():
        if value is None:
            doc.pop(key, None)
        else:
            doc[key] = value
    head = {
        "spec": SPEC,
        "id": status_id,
        "title": doc.get("title", status_id),
        "state": doc.get("state", "working"),
        "updated": iso(now or utcnow()),
    }
    doc = {**head, **{k: v for k, v in doc.items() if k not in head}}
    write_status(doc, directory)
    return doc


# ---------------------------------------------------------------- clear
DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}


def parse_duration(text: str) -> int:
    """``90``, ``90s``, ``45m``, ``6h``, ``7d`` or ``2w`` as seconds (raises ``ValueError``)."""
    raw = str(text).strip().lower()
    unit = raw[-1:] if raw[-1:] in DURATION_UNITS else "s"
    number = raw[:-1] if raw[-1:] in DURATION_UNITS else raw
    if not number.isdigit():
        raise ValueError(f"bad duration {text!r}: use a number with s, m, h, d or w (e.g. 7d)")
    return int(number) * DURATION_UNITS[unit]


def owned_status_file(path: Path) -> dict | None:
    """The document when ``path`` is a status file Lamptown would write itself: a regular file
    (not a link) named ``<id>.json`` that holds a valid ``agent-status/1`` document with that
    id. Anything else is ``None`` and is never removed."""
    try:
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            return None
        doc = read_status(path)
    except (OSError, StatusError):
        return None
    if validate(doc) or doc.get("id") != path.stem or path.name.startswith("."):
        return None
    return doc


def clear_files(
    folder: str | os.PathLike,
    rule,
    *,
    dry_run: bool = False,
) -> tuple[list[Path], list[Path], list[tuple[Path, str]]]:
    """Remove the status files in ``folder`` (not below it) for which ``rule(doc)`` is true.

    Only :func:`owned_status_file` files are considered, and a file that changed after it was
    read is left alone (an agent just wrote it). Returns (removed, skipped as not status files,
    failures as (path, reason))."""
    removed: list[Path] = []
    foreign: list[Path] = []
    failed: list[tuple[Path, str]] = []
    base = Path(folder)
    for path in sorted(base.iterdir()):
        if path.suffix != ".json" or path.name.startswith("."):
            continue
        try:
            before = path.stat()
        except OSError:
            continue
        doc = owned_status_file(path)
        if doc is None:
            foreign.append(path)
            continue
        if not rule(doc):
            continue
        if dry_run:
            removed.append(path)
            continue
        try:
            now_stat = path.stat()
            if (now_stat.st_mtime_ns, now_stat.st_size) != (before.st_mtime_ns, before.st_size):
                continue  # rewritten while we looked: it's alive
            path.unlink()
            removed.append(path)
        except OSError as exc:
            failed.append((path, exc.strerror or type(exc).__name__))
    return removed, foreign, failed
