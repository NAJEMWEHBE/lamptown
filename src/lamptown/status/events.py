"""The live town's event log: one append-only JSON Lines file per local day.

``<status_dir>/events/YYYY-MM-DD.jsonl`` (the date is the *local* date of the event's time, so
a day file is what the handover book shows as one day, across midnight and DST changes). One
event per line, each under :data:`MAX_LINE` bytes::

    {"t":"2026-11-04T09:07:00Z","id":"codex-3f9a","k":"files","n":12,
     "title":"Fix save slots","project":"paper-kite","tool":"codex","plot":"boathouse"}

``t`` is UTC. ``n`` (a count) and ``ok`` (a CI result) are present only when they mean
something. Only names are kept: a title, a project folder's *name* and a tool name. Any path
in them is cut down to its last part before it is written, and message text never is.

The Watcher is the only writer. Lines are added with ``O_APPEND`` in one ``write`` per file and
tick, so a reader sees whole lines (a torn last line, if any, is skipped). Standard library
only.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from lamptown.status.adapters import folder_name, one_line
from lamptown.status.schema import parse_time
from lamptown.status.store import ID_RE, iso, utcnow

KINDS = ("start", "files", "commit", "push", "ci", "wait", "answer", "fail", "quiet", "done",
         "project")
EVENTS_DIR = "events"
MAX_LINE = 1024  # bytes per line, newline included
MAX_DAY_BYTES = 16 * 1024 * 1024  # a day file bigger than this is read from its tail
MAX_COUNT = 10**9
LIMITS = {"title": 120, "project": 80, "tool": 64, "plot": 32}
DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.jsonl$")
_PLOT_RE = re.compile(r"[^a-z0-9_-]+")
# Paths inside a text. A path starts after anything but a letter, digit, "_", ".", "~" or "-"
# (so in brackets, quotes, backticks or after "=" too, but "save/load" is no path).
_LEAD = r"(?<![\w.~-])"
_TAIL = r"(?:[^\s\\/]+[\\/]+)*[^\s\\/]*"  # name/name/.../last (no spaces)
# 0. A URL: file:// and the rest.
_URL_RE = re.compile(_LEAD + r"[A-Za-z][A-Za-z0-9+.-]*:/{2,}" + _TAIL)
# 1. A Windows drive or UNC path written with backslashes. Its names may hold spaces, so it
#    runs to the last backslash of the text (cutting too much is safe; too little leaks).
_WIN_RE = re.compile(_LEAD + r"(?:[A-Za-z]:\\|\\\\)(?:[^\\]*\\)*" + _TAIL)
# 2. A home folder (/Users/<name>, /home/<name>, C:/Users/<name>): the user name may hold
#    spaces, so it runs to the next separator.
#    (One separator before "users": a long run of slashes must not be scanned again and again.)
_HOME_RE = re.compile(_LEAD + r"(?:[A-Za-z]:)?[\\/](?:users|home)[\\/]+[^\\/]*?[\\/]+" + _TAIL,
                      re.IGNORECASE)
# 3. Any other absolute path: X:/, ~/, /, //, \ or \\.
_PATH_RE = re.compile(_LEAD + r"(?:[A-Za-z]:[\\/]|~[\\/]|[\\/]+)" + _TAIL)


# ---------------------------------------------------------------- names only
def scrub(text: str) -> str:
    """``text`` with lone UTF-16 surrogates (half an emoji, cut by a JavaScript slice and
    accepted by ``json.loads``) replaced by ``?``, so it can always be written as UTF-8."""
    return text.encode("utf-8", "replace").decode("utf-8")


def _last_part(match: re.Match) -> str:
    found = match.group(0)
    name = folder_name(found)
    if name:
        return name
    return found if not found.strip("\\/") else ""  # a lone "/" in "a / b" stays


def name_only(text: Any, limit: int) -> str:
    """One line of ``text`` with every full path cut to its last part, at most ``limit`` chars.

    ``"Edit /work/paper-kite/src/main.py"`` becomes ``"Edit main.py"``; ``"C:\\Users\\you\\kite"``
    becomes ``"kite"``; so do paths in brackets or quotes, after ``=``, UNC paths and URLs.
    Relative fragments such as ``"save/load"`` stay. Lone surrogates become ``?``."""
    flat = scrub(one_line(text, 4096))
    if not flat:
        return ""
    for pattern in (_URL_RE, _WIN_RE, _HOME_RE, _PATH_RE):
        flat = pattern.sub(_last_part, flat)
    return one_line(flat, limit)


def project_name(value: Any) -> str:
    """A project's name: the last part of a folder path (any OS), at most 80 characters."""
    name = folder_name(value) if isinstance(value, str) else ""
    return one_line(scrub(name), LIMITS["project"])


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if math.isnan(value) or value < 0:
        return None
    return int(min(value, MAX_COUNT))


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.astimezone()
    else:
        moment = parse_time(value)
    return moment.astimezone(UTC).replace(microsecond=0) if moment else None


def clean_event(raw: Mapping[str, Any]) -> dict | None:
    """The on-disk form of one event, or ``None`` when it can't be one.

    Keeps only ``t``, ``id``, ``k``, ``n``, ``ok``, ``title``, ``project``, ``tool`` and
    ``plot``; ``t`` may be a datetime or ISO text and is written as UTC. Unknown keys are
    dropped, text is cut to :data:`LIMITS` and paths to their last part."""
    if not isinstance(raw, Mapping):
        return None
    kind, sid, moment = raw.get("k"), raw.get("id"), _when(raw.get("t"))
    if kind not in KINDS or not isinstance(sid, str) or not ID_RE.match(sid) or moment is None:
        return None
    out: dict[str, Any] = {"t": iso(moment), "id": sid, "k": kind}
    n = _count(raw.get("n"))
    if n is not None:
        out["n"] = n
    if isinstance(raw.get("ok"), bool):
        out["ok"] = raw["ok"]
    out["title"] = name_only(raw.get("title"), LIMITS["title"])
    out["project"] = project_name(raw.get("project"))
    out["tool"] = name_only(raw.get("tool"), LIMITS["tool"])
    plot = raw.get("plot")
    out["plot"] = _PLOT_RE.sub("", plot.lower())[: LIMITS["plot"]] if isinstance(plot, str) else ""
    return out


def encode(event: dict) -> bytes:
    """One line (newline included) under :data:`MAX_LINE` bytes; long names are shortened."""
    doc = dict(event)
    while True:
        line = (json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if len(line) <= MAX_LINE:
            return line
        for key in ("title", "project", "tool"):  # shorten the longest name first
            if len(doc[key]) > 8:
                doc[key] = doc[key][: len(doc[key]) // 2].rstrip() + "…"
                break
        else:  # pragma: no cover - ids are <= 128 ascii, so this can't happen
            raise ValueError("event line too long")


# ---------------------------------------------------------------- files
def events_dir(status_dir: str | os.PathLike) -> Path:
    return Path(status_dir) / EVENTS_DIR


def local_day(moment: datetime | str, tz: tzinfo | None = None) -> date:
    """The local calendar date of ``moment`` (``tz`` default: this computer's zone)."""
    when = _when(moment)
    if when is None:
        raise ValueError(f"not a date-time with an offset: {moment!r}")
    return when.astimezone(tz).date() if tz is not None else when.astimezone().date()


def day_path(status_dir: str | os.PathLike, day: date) -> Path:
    return events_dir(status_dir) / f"{day.isoformat()}.jsonl"


def event_key(event: Mapping[str, Any]) -> tuple:
    """What makes two log lines the same event: ``t``, ``id``, ``k``, ``n`` and ``ok``."""
    return (event["t"], event["id"], event["k"], event.get("n"), event.get("ok"))


def append(status_dir: str | os.PathLike, events: Iterable[Mapping[str, Any]], *,
           tz: tzinfo | None = None, keep_days: int | None = None, now: datetime | None = None,
           seen: dict[date, set] | None = None) -> int:
    """Add ``events`` to their day files (by the local date of each ``t``). Events that
    :func:`clean_event` rejects are skipped. Returns how many lines were written.

    ``keep_days``: events older than the last ``keep_days`` local days (today counts as one,
    ``now`` default: the clock) are dropped, so a reader catching up never makes a day file
    :func:`prune` would remove. ``seen``: skip events already in their day file (same
    :func:`event_key`); it caches each day's keys, read once and kept up to date (the Watcher
    uses this after a restart, which may replay a few seconds of events). A day file whose last
    line was torn by a crash gets a newline first, so the torn line can't swallow the next
    event."""
    oldest = (_today(now, tz) - timedelta(days=max(1, keep_days) - 1)
              if keep_days is not None else None)
    by_day: dict[date, list[dict]] = {}
    for raw in events:
        event = clean_event(raw)
        if event is None:
            continue
        day = local_day(event["t"], tz)
        if oldest is None or day >= oldest:
            by_day.setdefault(day, []).append(event)
    if not by_day:
        return 0
    folder = events_dir(status_dir)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    written = 0
    for day in sorted(by_day):
        path = day_path(status_dir, day)
        todo = by_day[day]
        if seen is not None:
            if day not in seen:
                seen[day] = {event_key(e) for e in read_day(path)}
            logged = seen[day]
            todo = [e for e in todo if event_key(e) not in logged]
            if not todo:
                continue
        payload = b"".join(encode(e) for e in todo)
        fd = os.open(path, flags, 0o600)
        try:
            if os.fstat(fd).st_size:
                os.lseek(fd, -1, os.SEEK_END)
                if os.read(fd, 1) != b"\n":  # a torn last line: end it first
                    payload = b"\n" + payload
            view = memoryview(payload)
            while view:  # one write in practice; loop only for a short write
                done = os.write(fd, view)
                view = view[done:]
        finally:
            os.close(fd)
        written += len(todo)
        if seen is not None:
            seen[day].update(event_key(e) for e in todo)
    return written


def read_day(path: str | os.PathLike) -> list[dict]:
    """Every valid event in one day file, sorted by time. Torn, oversized or unknown lines are
    skipped; a file over :data:`MAX_DAY_BYTES` is read from its tail."""
    path = Path(path)
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size > MAX_DAY_BYTES:
                fh.seek(size - MAX_DAY_BYTES)
                fh.readline()  # drop the partial first line
            data = fh.read(MAX_DAY_BYTES)
    except OSError:
        return []
    out = []
    for line in data.split(b"\n"):
        if not line.strip() or len(line) >= MAX_LINE:
            continue
        try:
            raw = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        event = clean_event(raw) if isinstance(raw, dict) else None
        if event is not None:
            out.append(event)
    out.sort(key=lambda e: e["t"])
    return out


def _today(now: datetime | None, tz: tzinfo | None) -> date:
    return local_day(now or utcnow(), tz)


def read_days(status_dir: str | os.PathLike, n: int = 7, *, now: datetime | None = None,
              tz: tzinfo | None = None) -> list[tuple[date, list[dict]]]:
    """The last ``n`` local days, oldest first, today last: ``[(date, events), ...]``.
    A day with no file has an empty list."""
    today = _today(now, tz)
    days = [today - timedelta(days=i) for i in range(max(1, n) - 1, -1, -1)]
    return [(day, read_day(day_path(status_dir, day))) for day in days]


def prune(status_dir: str | os.PathLike, keep: int = 7, *, now: datetime | None = None,
          tz: tzinfo | None = None) -> list[Path]:
    """Remove day files older than the last ``keep`` local days (today counts as one).
    Only files named ``YYYY-MM-DD.jsonl`` are touched. Returns the removed paths."""
    folder = events_dir(status_dir)
    oldest = _today(now, tz) - timedelta(days=max(1, keep) - 1)
    removed: list[Path] = []
    try:
        names = sorted(folder.iterdir())
    except OSError:
        return removed
    for path in names:
        match = DAY_RE.match(path.name)
        if not match or not path.is_file() or path.is_symlink():
            continue
        try:
            day = date(*(int(g) for g in match.groups()))
        except ValueError:
            continue
        if day < oldest:
            try:
                path.unlink()
                removed.append(path)
            except OSError:
                continue  # open elsewhere on Windows: try again next time
    return removed
