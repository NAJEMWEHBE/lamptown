"""The ``log_glob`` collector: one scheduled job, read from the newest log file its glob finds.

```toml
[[building.source]]
kind = "log_glob"
name = "Nightly backup"
glob = "~/logs/backup-*.log"        # or globs = [...]; the newest file is the last run
done = 'Done:\\s*(.+)$'             # optional: the line a finished run writes (group 1 = result)
fail = '\\b[1-9][0-9]* failed\\b'    # optional: a match means the run failed
needs = '(\\d+ ideas? to sort)'      # optional: a match means its result waits for you
schedule = "weekly tue 07:15"       # optional: see lamptown.collectors.jobs_schedule
running_minutes = 30                # optional: a log with no done line younger than this runs
```

Optional too: ``id``, ``short`` (the slate name), ``href`` (a link instead of the log file),
``feeds`` (a building id the job feeds) and ``colour`` (``"#rrggbb"`` for its banner).

How the run is read (``why`` names the case):

| state | why | when |
|---|---|---|
| ``missing`` | ``nosource`` | the glob's folder is not there |
| ``missing`` | ``never`` | no log yet |
| ``error`` | ``unreadable`` | the newest log can't be read |
| ``working`` | ``running`` | ``done`` is set, no done line yet, changed in ``running_minutes`` |
| ``error`` | ``stopped`` | ``done`` is set, the log has no done line, and it is older than that |
| ``error`` | ``missed`` | the last run is older than the schedule's period + 15 % |
| ``error`` | ``failed`` | ``fail`` matched |
| ``waiting`` | ``waiting`` | ``needs`` matched |
| ``ok`` | ``ok`` | everything else |

Read-only: it lists the glob and reads the tail (last 256 KB) of one file. Times are absolute.
"""

from __future__ import annotations

import datetime as dt
import glob as globmod
import os
import re
from pathlib import Path
from typing import Any

from lamptown.collectors.jobs_schedule import LOCAL
from lamptown.collectors.jobs_schedule import parse as parse_schedule

TAIL_BYTES = 256 * 1024
RUNNING_MINUTES = 30
RESULT_MAX = 300
NAME_MAX = 120
_MAGIC = re.compile(r"[*?[]")
_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


# ----------------------------------------------------------------------------- small helpers
def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:120] or "job"


def clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def iso(t: dt.datetime | None) -> str | None:
    if t is None:
        return None
    text = t.replace(microsecond=0).isoformat()
    return text[:-6] + "Z" if text.endswith("+00:00") else text


def file_uri(p: Path) -> str:
    try:
        return p.resolve().as_uri()
    except (OSError, ValueError):
        return ""


def mtime(p: Path, tz: dt.tzinfo | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(p.stat().st_mtime, tz=tz or dt.UTC)
    except (OSError, OverflowError, ValueError):
        return None


def static_root(pattern: str) -> Path:
    """The part of a glob before its first wildcard: the folder that must exist."""
    parts = Path(pattern).parts
    keep = []
    for part in parts:
        if _MAGIC.search(part):
            break
        keep.append(part)
    if len(keep) == len(parts):  # no wildcard: the file itself, so its folder
        keep = keep[:-1]
    return Path(*keep) if keep else Path(".")


def regex(paths: dict, key: str) -> re.Pattern[str] | None:
    raw = paths.get(key)
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ValueError(f"{key} must be a regular expression in a string")
    try:
        return re.compile(raw, re.MULTILINE)
    except re.error as e:
        raise ValueError(f"{key} is not a valid regular expression ({e})") from e


def read_tail(p: Path) -> str | None:
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            data = f.read()
    except OSError:
        return None
    return data.decode("utf-8", errors="replace").lstrip("﻿")


def matched_text(m: re.Match[str]) -> str:
    return (m.group(1) if m.re.groups and m.group(1) else m.group(0)).strip()


# ----------------------------------------------------------------------------- the reader
def find_logs(patterns: list[str]) -> tuple[list[Path], bool]:
    """(files, any source folder there)."""
    files: dict[str, Path] = {}
    there = False
    for pat in patterns:
        if static_root(pat).is_dir():
            there = True
        for name in globmod.glob(pat, recursive=True):
            p = Path(name)
            if p.is_file():
                files[str(p)] = p
    return list(files.values()), there


def read_run(paths: dict, now: dt.datetime) -> dict[str, Any]:
    """The newest log's facts: last, state, why, result, href."""
    raw = paths.get("globs", paths.get("glob"))
    if raw is None:
        raise ValueError('needs glob = "..." (the log files of this job)')
    patterns = [raw] if isinstance(raw, str) else [str(x) for x in raw]
    done, fail, need = regex(paths, "done"), regex(paths, "fail"), regex(paths, "needs")
    running_min = paths.get("running_minutes", RUNNING_MINUTES)
    if isinstance(running_min, bool) or not isinstance(running_min, int | float) or running_min < 0:
        raise ValueError("running_minutes must be a number of minutes, 0 or more")
    files, there = find_logs(patterns)
    if not there and not files:
        return {"state": "missing", "why": "nosource", "result": "Can't see its log folder."}
    if not files:
        return {"state": "missing", "why": "never", "result": "No run on record yet."}
    stamped = [(mtime(p, now.tzinfo), p) for p in files]
    stamped.sort(key=lambda x: (x[0] or dt.datetime.min.replace(tzinfo=dt.UTC), x[1].name))
    last, newest = stamped[-1]
    out: dict[str, Any] = {"last": iso(last), "href": file_uri(newest), "log": newest.name}
    text = read_tail(newest)
    if text is None:
        return out | {"state": "error", "why": "unreadable", "result": f"Can't read {newest.name}."}
    result = ""
    if done is not None:
        hits = list(done.finditer(text))
        if not hits:
            age = now - last if last else None
            if age is not None and dt.timedelta(0) <= age <= dt.timedelta(minutes=running_min):
                return out | {"state": "working", "why": "running",
                              "result": "Running now: its log has no done line yet."}
            return out | {"state": "error", "why": "stopped",
                          "result": "The run stopped half-way: its log has no done line."}
        result = matched_text(hits[-1])
    else:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        result = lines[-1] if lines else ""
    out["result"] = clip(result, RESULT_MAX)
    sched = paths.get("_schedule")
    if sched is not None and sched.missed(last, now):
        when = f"{last:%Y-%m-%d}" if last else "never"
        return out | {"state": "error", "why": "missed",
                      "result": clip(f"Missed its run: the newest log is from {when}. {result}",
                                     RESULT_MAX)}
    if fail is not None and (m := fail.search(text)):
        return out | {"state": "error", "why": "failed",
                      "result": clip(result or matched_text(m), RESULT_MAX)}
    if need is not None and (m := need.search(text)):
        return out | {"state": "waiting", "why": "waiting", "need": clip(matched_text(m), 200)}
    return out | {"state": "ok", "why": "ok"}


def collect(paths: dict, now: dt.datetime) -> dict[str, Any]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=LOCAL)  # review-fix C6: slots follow the local clock
    name = clip(paths.get("name") or "", NAME_MAX)
    raw = paths.get("glob") or (paths.get("globs") or [""])[0]
    if not name:
        name = clip(Path(str(raw)).stem.replace("*", "").strip("-_ .") or "Job", NAME_MAX)
    row: dict[str, Any] = {"id": slug(paths.get("id") or name), "name": name}
    sched = None
    if paths.get("schedule"):
        sched = parse_schedule(str(paths["schedule"]))
        row.update(schedule=sched.text[:120], when=sched.words(), when_short=sched.short())
    for key, limit in (("short", 16), ("feeds", 40)):
        if isinstance(paths.get(key), str) and paths[key].strip():
            row[key] = paths[key].strip()[:limit]
    if isinstance(paths.get("colour"), str) and _COLOUR.match(paths["colour"]):
        row["colour"] = paths["colour"].lower()
    run = read_run(dict(paths, _schedule=sched), now)
    need = run.pop("need", None)
    run.pop("log", None)
    row.update(run)
    row.setdefault("last", None)
    last =dt.datetime.fromisoformat(row["last"].replace("Z", "+00:00")) if row.get("last") else None
    nxt = sched.next_after(now, last) if sched else None
    row["next"] = iso(nxt)
    if isinstance(paths.get("href"), str) and paths["href"].strip():
        row["href"] = paths["href"].strip()
    if not row.get("href"):
        row.pop("href", None)
    out: dict[str, Any] = {"state": row["state"], "rows": [row]}
    if need:
        out["needs"] = [{"text": clip(f"{name}: {need}", 300), **({"href": row["href"]}
                                                                   if row.get("href") else {})}]
    word = {"ok": "ran fine", "working": "running now", "waiting": "needs you",
            "missing": "no run on record", "error": "last run failed"}[row["state"]]
    if row["why"] == "missed":
        word = "missed its run"
    elif row["why"] == "stopped":
        word = "stopped half-way"
    elif row["why"] == "nosource":
        word = "can't see its logs"
    # no top-level "error" key: that one means "the room could not read its source" and covers the
    # room; a failed run is the job's own state, shown on its window
    out["label"] = clip(f"{name}: {word}", 120)
    return out
