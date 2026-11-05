"""Read and write ``agent-status/1`` files: one ``<id>.json`` per agent, task or job.

Writes are atomic (temp file in the same folder, then rename), so a reader never sees a
half-written file. Standard library only, no network.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections.abc import Generator
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


# --- release-fix: ENG (engine 4, privacy 5) ---
class StatusBusy(StatusError):
    """An existing status file can't be read right now (held open while a writer replaces it,
    or half written by a writer that doesn't write atomically). Nothing was written."""


READ_TRIES = 3  # an existing file that can't be read is read again this often ...
READ_WAIT = 0.02  # ... this many seconds apart, before it counts as unreadable


def read_existing(path: str | os.PathLike, *, tries: int = READ_TRIES,
                  wait: float = READ_WAIT) -> dict | None:
    """The document in ``path``, or ``None`` when there is no such file. A file that is there
    but can't be read is tried ``tries`` times, then raises :class:`StatusBusy`."""
    for attempt in range(max(1, tries)):
        try:
            return read_status(path)
        except StatusError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                return None  # gone (or never there): a new file
            if attempt >= tries - 1:
                raise StatusBusy(str(exc)) from exc
        time.sleep(wait)
    return None  # not reached


_PATHISH = re.compile(r"[\\/~]")


def safe_reason(exc: BaseException, *, typed: bool = False) -> str:
    """A short reason for an error line that is safe to log or paste into an issue: an
    ``OSError``'s ``strerror`` (never its file name), else the message's first line when it
    holds nothing path-like, else nothing. ``typed`` puts the exception's type in front."""
    name = type(exc).__name__
    if isinstance(exc, OSError) and exc.strerror:
        text = str(exc.strerror)
    else:
        lines = str(exc).strip().splitlines()
        text = lines[0].strip() if lines else ""
        if _PATHISH.search(text):
            text = ""
    text = text[:160]
    if typed:
        return f"{name}: {text}" if text else name
    return text or name
# --- end release-fix: ENG ---


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


# --- review-fix B1: lone surrogates ---
_PAIR_RE = re.compile("[\ud800-\udbff][\udc00-\udfff]")
_LONE_RE = re.compile("[\ud800-\udfff]")


def scrub_text(text: str) -> str:
    """``text`` that can always be written as UTF-8: a surrogate pair held as two code points
    becomes its character, and a lone surrogate (an undecodable byte from POSIX argv, half an
    emoji from a JavaScript slice) becomes U+FFFD. Valid text is returned unchanged."""
    if not _LONE_RE.search(text):
        return text
    text = _PAIR_RE.sub(lambda m: m.group(0).encode("utf-16", "surrogatepass").decode("utf-16"),
                        text)
    return _LONE_RE.sub("\ufffd", text)


def scrub_value(value: Any) -> Any:
    """:func:`scrub_text` over every string (keys too) in a JSON value."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {scrub_value(k): scrub_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub_value(v) for v in value]
    return value
# --- end review-fix B1 ---


def atomic_write_json(path: Path, data: Any, *, max_bytes: int = MAX_BYTES) -> None:
    """Write ``data`` as UTF-8 JSON to ``path`` through a temp file and an atomic rename.

    ``max_bytes`` caps the file (status files: :data:`MAX_BYTES`; the Watcher's own state files
    pass a bigger cap). Lone surrogates are written as U+FFFD (:func:`scrub_text`)."""
    text = scrub_text(json.dumps(data, ensure_ascii=False, indent=1))  # review-fix B1
    payload = (text + "\n").encode("utf-8")
    if len(payload) > max_bytes:
        raise StatusError(f"status file would be {len(payload)} bytes; the limit is {max_bytes}")
    atomic_write_bytes(path, payload)


def atomic_write_bytes(path: str | os.PathLike, payload: bytes) -> None:
    """Write ``payload`` to ``path`` through a temp file in the same folder and an atomic
    rename (R2b: the Watcher rewrites event day files with it too)."""
    path = Path(path)
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


# --- review-fix R2b 2: the per-record write lock ---
LOCK_SUFFIX = ".lock"
LOCK_WAIT = 0.5  # seconds a writer waits for a record's lock at most, then writes without it
LOCK_STALE = 2.0  # seconds: a lock file older than this was left by a writer that died
LOCK_POLL = 0.005  # seconds between tries while the lock is taken
_log = logging.getLogger("lamptown.status")


def lock_path(path: str | os.PathLike) -> Path:
    """The lock file beside a record: ``.<id>.json.lock`` (hidden, so no scan reads it)."""
    path = Path(path)
    return path.with_name(f".{path.name}{LOCK_SUFFIX}")


def _drop_stale(lock: Path, stale: float) -> None:
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return  # gone already (or can't be looked at): just try again
    if age > stale:
        with contextlib.suppress(OSError):
            lock.unlink()


def drop_stale_lock(path: str | os.PathLike) -> None:
    """A record was removed (tidied, cleared): its lock file goes too when a killed writer left
    it (older than :data:`LOCK_STALE`). A fresh one is a writer's at work: it removes its own.
    (review-fix R3b 4)"""
    _drop_stale(lock_path(path), LOCK_STALE)


@contextlib.contextmanager
def record_lock(path: str | os.PathLike) -> Generator[bool]:
    """Hold a record's short write lock while reading, deciding and writing it, so two writers
    (a hook and the Watcher) never lose each other's change. Yields whether the lock is held.

    The lock is a file made with ``O_CREAT | O_EXCL`` beside the record (:func:`lock_path`),
    holding a token of this writer's own. One older than :data:`LOCK_STALE` is broken (its
    writer died). A writer waits :data:`LOCK_WAIT` at most; then it writes without the lock, as
    before there was one, and says so in the ``lamptown.status`` log (no path). Leaving, it
    removes the lock only while it still holds its own token."""
    lock = lock_path(path)
    token = f"{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}\n".encode()
    deadline = time.monotonic() + max(0.0, LOCK_WAIT)
    held = made = False
    while True:
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                         0o600)
        except FileNotFoundError:  # no folder yet: make it, then try once more
            if made:
                break
            made = True
            try:
                lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            except OSError:
                break
            continue
        except (FileExistsError, PermissionError):  # taken (Windows: or being removed)
            _drop_stale(lock, LOCK_STALE)
            if time.monotonic() >= deadline:
                break
            time.sleep(LOCK_POLL)
            continue
        except OSError:
            break  # a folder that can't hold the lock: write as before
        try:
            held = os.write(fd, token) == len(token)
        except OSError:
            held = False
        finally:
            os.close(fd)
        if not held:  # a full disk: no lock, and no lock file without our token left behind
            with contextlib.suppress(OSError):
                lock.unlink()
        break
    if not held:
        _log.warning("lamptown: a status record's write lock stayed busy for %.1f s; "
                     "it is written without the lock", LOCK_WAIT)
    try:
        yield held
    finally:
        if held:
            with contextlib.suppress(OSError):
                if lock.read_bytes() == token:  # never someone else's (ours broken as stale)
                    lock.unlink()
# --- end review-fix R2b 2 ---


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
    doc = scrub_value(dict(doc))  # review-fix B1: lone surrogates
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
    replace_unreadable: bool = True,
    defaults: dict[str, Any] | None = None,
    **changes: Any,
) -> dict:
    """Merge ``changes`` into the existing file (if any) and write it back atomically.

    A change whose value is ``None`` removes that field. ``updated`` is set to ``now``.
    ``defaults`` are set only where the file (after ``changes``) has no such field: a hook
    names a record after its folder when it creates it, and never renames it later.
    A new file gets ``title = id`` and ``state = "working"`` unless given. Returns the
    document that was written. Concurrent writers: last one wins, never a torn file.
    Lone surrogates in any text become U+FFFD (:func:`scrub_value`).

    An existing file that can't be read is read again a few times (:func:`read_existing`).
    If it still can't be read, it is replaced (``replace_unreadable``, the default: a broken
    file is fixed), or :class:`StatusBusy` is raised and nothing is written (the Watcher: a
    record held open for a moment must not be rewritten with only the change).

    The read, merge and write hold the record's :func:`record_lock` (R2b), so a Watcher
    deciding the same record meanwhile never writes over this change.
    """
    path = status_path(status_id, directory)
    with record_lock(path):  # review-fix R2b 2
        return _update_locked(path, status_id, directory, now=now,
                              replace_unreadable=replace_unreadable, defaults=defaults,
                              changes=changes)


def _update_locked(path: Path, status_id: str, directory: str | os.PathLike | None, *,
                   now: datetime | None, replace_unreadable: bool,
                   defaults: dict[str, Any] | None, changes: dict[str, Any]) -> dict:
    doc: dict = {}
    # --- release-fix: ENG (engine 4) ---
    try:
        old = read_existing(path)
    except StatusBusy:
        if not replace_unreadable:
            raise
        old = None
    if old is not None and not validate(old):
        doc = old
    # --- end release-fix: ENG ---
    for key, value in changes.items():
        if value is None:
            doc.pop(key, None)
        else:
            doc[key] = value
    for key, value in (defaults or {}).items():  # review-fix B1: title flapping
        if value is not None and key not in doc:
            doc[key] = value
    doc = scrub_value(doc)  # review-fix B1: the returned doc is the written one
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
            drop_stale_lock(path)  # review-fix R3b 4
        except OSError as exc:
            failed.append((path, exc.strerror or type(exc).__name__))
    return removed, foreign, failed
