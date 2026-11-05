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
import posixpath  # L3-fix: home folder
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from lamptown.status.adapters import DATED, folder_name, one_line
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
# --- W2-left-fix2: paths in a text, one structural rule ---
# A path START is a drive ("x:/", "x:\"), a home ("~/", "~name/"), a UNC root ("\\", "//host/"),
# any URL ("file://", "vscode-remote://", "https://"), or a root folder that only a path has:
# /Users/, /home/, /root/, /mnt/, /cygdrive/, a Git Bash drive (/d/), /srv/, /opt/, /tmp/, /var/,
# /private/, /Volumes/, /media/, /run/, /export/, /System/, /usr/, /etc/, /Library/,
# /Applications/, /workspaces/ (in any case). A drive, "~" or URL starts after anything but a
# letter, digit, "_", ".", "~" or "-" (so in brackets, quotes, backticks or after "=" too, but
# "save/load" is no path); a root folder after anything but a letter or digit ("x~/home/...").
# --- fix2-PRIV: web addresses --- A web address shows none of itself (its last part is often a
# person's handle or a private repository's name): the whole address becomes the word "link".
# That is any URL but a file or editor one ("https://...", "ssh://..."; "file://" and
# "vscode://file/..." are paths), and an address with no scheme: a host, an optional port, then
# "/" and a name. A host is "localhost", an IPv4 address, "www." and more labels, or labels whose
# last one is a known top-level domain (_TLD_ANY in any case, _TLD_LOWER in lower case only, so
# the tech names ".NET", "Socket.IO" and "jQuery.ui", or file names such as "main.go/x",
# "data.csv/x", "St.Louis/x", stay words). Labels may be any letters (an IDN host).
_LABEL = r"[^\W_](?:[\w-]*[^\W_])?"
_IPV4 = r"\d{1,3}(?:\.\d{1,3}){3}"
_TLD_ANY = r"com|org|edu|gov|mil|int|info|biz"
_TLD_LOWER = (r"net|io|ai|app|dev|co|me|tv|gg|fm|ly|xyz|site|online|tech|cloud|blog|wiki|email"
              r"|page|local|lan|internal|corp|example|invalid|localhost|eu|uk|de|fr|nl|es|it|se"
              r"|dk|fi|ch|at|be|cz|ru|ua|cn|jp|kr|au|nz|ca|us|br|mx|ar|cl|za|ie|pt|gr|tr|il|ae"
              r"|sa|kw|qa|eg|sg|hk|tw|vn|th")
_PORT = r"(?::\d{1,5})?"
_WEB_HOST = (rf"(?:(?:{_LABEL}\.)+(?:{_TLD_ANY}|(?-i:{_TLD_LOWER})){_PORT}"
             rf"|www\.(?:{_LABEL}\.)*{_LABEL}{_PORT}|localhost{_PORT}|{_IPV4}{_PORT})")
_PATH_SCHEME_RE = re.compile(r"[a-z]|file|vscode(?:-[a-z]+)?|cursor|windsurf",  # "d:\\" a drive
                             re.IGNORECASE)
LINK_WORD = "link"
# --- end fix2-PRIV ---
_START_RE = re.compile(
    r"(?<![\w.+~-])(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*):[\\/]{2}"
    r"|(?<![\w.~-])(?:[A-Za-z]:[\\/]|~(?:[A-Za-z][\w.-]*)?[\\/])"
    r"|(?<![^\W_])(?:\\\\|//(?=[^\s\\/]+[\\/])"
    # fix-PRIV: a named root may lead with "\" too ("\Users\you" has no drive)
    r"|[\\/](?:users|home|root|mnt|cygdrive|srv|opt|tmp|var|private|volumes|media|run|export"
    r"|system|usr|etc|library|applications|workspaces?)[\\/]|/[a-z][\\/])"
    # fix-PRIV, fix2-PRIV: a web address with no scheme ("github.com/you/kite",
    # "www.example.org:8080/x", "localhost:3000/x", "10.0.0.5/x")
    rf"|(?<![\w.@~%-])(?P<web>{_WEB_HOST})/(?=[^\s/])",
    re.IGNORECASE)
# --- fix-PRIV, fix2-PRIV: addresses --- an e-mail address is never kept: it becomes "someone".
# Its domain is dotted or bare ("you@buildbox"), in any letters, or an IPv4 address; its last
# label starts with a letter, so "pkg@1.2.3" and "time@12:30" are none, and neither is a version
# or a git name after "@" ("actions/checkout@v4", "pkg@latest", "x@main", "x@1a2b3c4d").
# "@" may be written "%40". A remote in scp form ("git@example.com:you/kite.git") becomes "link".
# "@types/node" and "@mention" (nothing before the "@") are no address.
_DOMAIN = rf"(?:(?:{_LABEL}\.)*[^\W\d_](?:[\w-]*[^\W_])?|{_IPV4})"
_NOT_HOST = (r"(?!(?:v\d[\w.-]*|[0-9a-f]{7,40}|latest|next|beta|alpha|canary|rc|nightly|lts"
             r"|stable|edge|experimental|insiders|preview|main|master|trunk|develop|head)"
             r"(?![\w.-]))")
_MAIL_RE = re.compile(rf"(?<![\w.+%-])[\w.+%-]+(?:@|%40){_NOT_HOST}{_DOMAIN}(?![\w-])",
                      re.IGNORECASE)
_SCP_RE = re.compile(rf"(?<![\w.+%-])[\w.+%-]+@{_DOMAIN}:(?=[^\s\\:\"<>|`])[^\s\"<>|`]*")
_TRAIL_RE = re.compile(r"[.,;:!?)\]}'*]+$")  # punctuation after an address is the title's
MAIL_WORD = "someone"
NAME_RULE = 2  # fix2-PRIV: raise when name_only cuts more: titles a reader saved are cut again


def _link(match: re.Match) -> str:
    trail = _TRAIL_RE.search(match.group(0))
    return LINK_WORD + (trail.group(0) if trail else "")
# --- end fix-PRIV ---
# From its start, a path RUNS to the first character no name holds in a title (" < > | or a
# backtick), or to ", " or "; " right after a file name with an extension ("...x.py, then
# save/load"), else to the end of the text. Its names may hold spaces, so where it ends inside
# the run is unclear: everything from the start through the run's LAST separator goes (doubled
# ones too), and only the final name stays ("Edit d:/My Project//src/x.py now" gives "Edit x.py
# now"). Cutting too much is the safe failure; too little leaks: a title that goes on past a
# path loses its words up to the last "/" ("Fix c:/temp and the save/load test" gives "Fix load
# test"). A run that ends in a separator keeps its last folder ("cd d:/kite/ now": "cd kite now").
_HARD_RE = re.compile(r'["<>|`]')
_SEPS_RE = re.compile(r"[\\/]+")
_CLAUSE_RE = re.compile(r"[^\s\\/.]\.(?=[A-Za-z0-9]{0,7}[A-Za-z])[A-Za-z0-9]{1,8}[,;](?=\s|$)")
# A home folder's user name, and the user and label of a removable drive (/media/<user>/<label>),
# are never shown: when one would be the final name, the whole rest of the run becomes "~" (it
# may hold spaces: "see /home/you there" gives "see ~").
_HOMES = frozenset({"users", "home", "volumes", "media", "documents and settings"})
# A root path with no start above ("/work/kite/x.py", "/api/users endpoint") is cut to its last
# part too, but only over names without spaces: its words may be a title's. Two parts or more
# ("/code-review" and "\n" are a slash command and an escape). It starts at the first separator
# of a run, so a long run is scanned once.
_LEAD = r"(?<![\w.~-])"
_TAIL = r"(?:[^\s\\/]+[\\/]+)*[^\s\\/]*"  # name/name/.../last (no spaces)
_PATH_RE = re.compile(_LEAD + r"(?<![\\/])[\\/]+[^\s\\/]+[\\/]" + _TAIL)


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


def _is_home_name(folders: list[str]) -> bool:
    """The name after ``folders`` is a user name or a drive label (``/home/<name>``,
    ``C:\\Users\\<name>``, ``/media/<user>/<label>``)."""
    return bool(folders) and (folders[-1].casefold() in _HOMES
                              or (len(folders) > 1 and folders[-2].casefold() == "media"))


def _cut_run(text: str, start: re.Match, last: int, end: int) -> tuple[str, int]:
    """What stands for ``text[start.start():last + 1]`` (the run through its last separator),
    and where the text goes on."""
    head = text[start.start():last + 1]
    folders = [p for p in _SEPS_RE.split(head[:-1]) if p]  # the last one is cut off
    after = text[last + 1:end]
    if after and not after[0].isspace():  # a final name follows the separator
        if _is_home_name(folders):
            return "~", end
        # fix-PRIV: a date-named folder (a chat's) shows as folder_name shows it, never its words
        name = after.split(None, 1)[0]
        if DATED.match(name):
            return folder_name(head + name), last + 1 + len(name)
        return "", last + 1
    # the run ends in a separator: its last folder is the final name (but not the root itself)
    if len(head.rstrip("\\/")) <= start.end() - start.start():
        return ("~" if head.startswith("~") else ""), last + 1
    if _is_home_name(folders[:-1]):
        return "~", last + 1
    return (folder_name(head) if DATED.match(folders[-1]) else folders[-1]), last + 1  # fix-PRIV


def _is_web(start: re.Match) -> bool:
    """The path start is a web address (fix2-PRIV): a host, or a URL that is no file one."""
    scheme = start.group("scheme")
    return start.group("web") is not None or (
        scheme is not None and not _PATH_SCHEME_RE.fullmatch(scheme))


def _cut_paths(text: str) -> str:
    out, pos = [], 0
    while (start := _START_RE.search(text, pos)) is not None:
        hard = _HARD_RE.search(text, start.end())
        end = hard.start() if hard else len(text)
        clause = _CLAUSE_RE.search(text, start.end(), end)
        if clause:
            end = clause.end() - 1
        last = max(text.rfind("/", start.start(), end), text.rfind("\\", start.start(), end))
        if _is_web(start):  # fix2-PRIV: a web address, its final name too, becomes "link"
            rest = text[last + 1:end]
            name = rest.split(None, 1)[0] if rest[:1] and not rest[0].isspace() else ""
            trail = _TRAIL_RE.search(name)
            out += [text[pos:start.start()], LINK_WORD]
            pos = last + 1 + len(name) - (len(trail.group(0)) if trail else 0)
            continue
        kept, pos_next = _cut_run(text, start, last, end)
        # fix-PRIV: a hard character inside a user name ('/home/you"r/x'): the rest of the name
        # goes too, up to a hard character that ends it (one followed by a space, or the end)
        if kept == "~" and hard is not None and pos_next == end == hard.start():
            while end + 1 < len(text) and not text[end + 1].isspace():
                more = _HARD_RE.search(text, end + 1)
                end = more.start() if more else len(text)
            pos_next = end
        out += [text[pos:start.start()], kept]
        pos = pos_next
    out.append(text[pos:])
    return "".join(out)


def name_only(text: Any, limit: int) -> str:
    """One line of ``text`` with every path cut to its final name, at most ``limit`` chars.

    ``"Edit /work/paper-kite/src/main.py"`` becomes ``"Edit main.py"``; ``"C:\\Users\\you\\kite"``
    becomes ``"kite"``; so do paths in brackets or quotes, after ``=``, UNC paths and URLs.
    A path with a start (a drive, ``~``, a home or other system root, UNC, a URL) is cut
    through the last separator of its run, so a folder name with spaces never stays; a title
    that goes on past it may lose words. A home folder's user name is never kept: ``"see
    /home/you"`` becomes ``"see ~"``. Relative fragments such as ``"save/load"`` stay. Lone
    surrogates become ``?``. A web address (``"https://example.com/you"``,
    ``"github.com/you/kite"``, ``"localhost:3000/x"``) and a remote in scp form
    (``"git@example.com:you/kite.git"``) become ``"link"``, whole; an e-mail address becomes
    ``"someone"``. A date-named (chat) folder never shows its words."""
    flat = scrub(one_line(text, 4096))
    if not flat:
        return ""
    flat = _MAIL_RE.sub(MAIL_WORD, _SCP_RE.sub(_link, flat))  # fix-PRIV: addresses
    flat = _PATH_RE.sub(_last_part, _cut_paths(flat))
    return one_line(flat, limit)
# --- end W2-left-fix2 ---


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
    # --- L3-fix: out-of-range time --- (a bad line is skipped like any other malformed one)
    try:
        if isinstance(value, datetime):
            moment = value if value.tzinfo is not None else value.astimezone()
        else:
            moment = parse_time(value)
        moment = moment.astimezone(UTC).replace(microsecond=0) if moment else None
    except (OverflowError, ValueError):
        return None
    # years 1 and 9999 are refused too: their local day may not exist in a datetime
    return moment if moment is not None and 1 < moment.year < 9999 else None
    # --- end L3-fix ---


# --- L3-fix: home folder ---
# A bare home folder (or the folder of all of them), on any OS, also behind a mount.
# fix-PRIV: and a removable drive's own folder (/Volumes/<label>, /media/<user>/<label>,
# /run/media/<user>/<label>): its label is often a person's name
_HOMES_RE = re.compile(r"^(?:[a-z]:|/mnt/[a-z]|/cygdrive/[a-z]|/[a-z])?/"
                       r"(?:(?:users|home)(?:/[^/]+)?|volumes(?:/[^/]+)?"
                       r"|(?:run/)?media(?:/[^/]+){0,2})$")


def _flat(path: str) -> str:
    text = path.strip()
    text = os.path.expanduser(text) if text.startswith("~") else text
    return posixpath.normpath(text.replace("\\", "/")).rstrip("/").casefold()


def path_like(value: Any) -> bool:
    """``value`` is a path, not a bare name (it has a separator or starts with ``~``)."""
    return isinstance(value, str) and ("/" in value or "\\" in value
                                       or value.strip().startswith("~"))


def home_or_root(path: Any) -> bool:
    """A folder that names no project: a drive or file-system root, the home folder or one
    above it, or any bare home folder (``/home/<name>``, ``/Users/<name>``,
    ``C:\\Users\\<name>``). Text only: nothing is opened."""
    if not isinstance(path, str) or not path.strip():
        return False
    flat = _flat(path)
    parts = [p for p in flat.split("/") if p]
    if not parts or (len(parts) == 1 and parts[0].endswith(":")) or parts == ["."]:
        return True
    home = _flat(os.path.expanduser("~"))
    if home and (flat == home or home.startswith(flat + "/")):
        return True
    return bool(_HOMES_RE.match(flat))


def project_of(project: Any, folder: Any) -> str:
    """A record's project name: ``project`` (``x-project``: a name, or a path cut to its last
    part), else the last part of ``folder`` (``x-folder``). Never a login name: a folder that
    :func:`home_or_root` holds names no project, and neither does a ``project`` that only
    repeats its name, nor the home folder's own name when no other folder gives one."""
    login = folder_name(os.path.expanduser("~")).casefold()
    broad = home_or_root(folder)
    from_folder = "" if broad else project_name(folder)
    name = project_name(project)
    if name:
        key = name.casefold()
        if ((path_like(project) and home_or_root(project))
                or (broad and key == project_name(folder).casefold())
                or (key == login and not from_folder)):
            name = ""
    return name or from_folder
# --- end L3-fix: home folder ---


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
           seen: dict[date, set] | None = None) -> list[dict]:
    """Add ``events`` to their day files (by the local date of each ``t``). Events that
    :func:`clean_event` rejects are skipped. Returns the events written, in their on-disk
    form (day by day, each day in the order given): not the ones skipped as too old or as
    already logged.

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
    written: list[dict] = []
    if not by_day:
        return written
    folder = events_dir(status_dir)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
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
        written.extend(todo)
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
