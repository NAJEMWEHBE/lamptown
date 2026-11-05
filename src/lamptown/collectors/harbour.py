"""The ``projects`` collector: one berth per project, listed in the config.

```toml
[[building.source]]
kind = "projects"
private_dirs = ["~/private"]       # optional: never read inside these (or anything under them)
busy_minutes = 30                  # optional: a change this recent means someone is at work
projects = [
  { name = "Paper Kite", path = "~/code/paper-kite", status = "active" },
  { name = "Moss Engine", path = "~/code/moss-engine", status = "paused", since = "2026-10-01" },
  { name = "Tidebook", path = "~/code/tidebook", status = "closed", note = "Shipped." },
  { name = "Lantern", path = "~/code/lantern", status = "active", private = true },
]
```

Each project may also set ``id``, ``short`` (berth board, 14 characters), ``board`` (wall board,
9), ``note`` and ``since`` (a date). ``status`` is ``active``, ``paused`` (also ``parked``) or
``closed`` (also ``done``, ``archived``). Relative paths are read against the config's folder.

What is read, read-only:

- **active** projects: ``TASKS.md`` (checkbox counts for the hull's planks; open items under a
  heading that starts "Needs you" or "Waiting on you/the user" wait on you, and items there that
  start "(agent" or "(lead" are the agent's own), the tail of ``.git/logs/HEAD`` (time and
  message of the newest commit) and three modification times (``TASKS.md``, the git log, the git
  index) for "busy";
- **paused** and **closed** projects: nothing inside, only whether the folder is there;
- **private** projects (``private = true``, or a path in or under ``private_dirs``): shown paused
  whatever their status says, and only the folder's existence is checked. Their path is never
  opened, listed or read.

Project state: ``missing`` (folder gone), ``error`` (TASKS.md there but unreadable), ``waiting``
(items wait on you), ``working`` (nothing waits, but something changed in the last
``busy_minutes``), else ``ok``. Room state: the worst active project; no active ones: ``ok``; no
projects at all: ``missing``.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path
from typing import Any

from lamptown.collectors.jobs import clip, file_uri, iso, mtime
from lamptown.status.events import name_only

STATUSES = {"active": "active", "paused": "paused", "parked": "paused", "closed": "closed",
            "done": "closed", "archived": "closed"}
WORST_FIRST = ("error", "waiting", "working", "missing", "ok")
BUSY_MINUTES = 30
MAX_ITEMS = 40
NEEDS_HEAD = re.compile(r"^(?:needs you|waiting on (?:you|the user))\b", re.I)
AGENT_ITEM = re.compile(r"^\(\s*(?:agent|lead)\b[^)]*\)\s*", re.I)
COMMIT = re.compile(r"^[0-9a-f]{7,64} [0-9a-f]{7,64} .*?> (\d{9,11}) [+-]\d{4}\t(.*)$")


def worst(states) -> str:
    s = set(states)
    return next((w for w in WORST_FIRST if w in s), "ok")


def _abs(raw: str, base: Path) -> Path:
    p = Path(os.path.expanduser(os.path.expandvars(raw)))
    return Path(os.path.normpath(p if p.is_absolute() else base / p))


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def is_private(root: Path, private_dirs: list[Path]) -> bool:
    """``root`` is one of ``private_dirs`` or under one. Compared as written first (nothing is
    opened); only when that says no is the path resolved, so a link into a private folder counts
    too. Resolving reads link targets on the way, never anything inside the folder."""

    def under(a: str, b: str) -> bool:
        return a == b or a.startswith(b.rstrip(os.sep) + os.sep)

    if any(under(_norm(root), _norm(d)) for d in private_dirs):
        return True
    try:
        real = os.path.normcase(str(root.resolve()))
        return any(under(real, _norm(d)) or under(real, os.path.normcase(str(d.resolve())))
                   for d in private_dirs)
    except (OSError, RuntimeError):
        return False


def _isdir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


def _date(v: Any) -> str | None:
    try:
        return dt.date.fromisoformat(str(v).strip()).isoformat() if v else None
    except ValueError:
        return None


def short_item(s: str) -> str:
    """A waiting item as one short plain line (no markdown, no asides in brackets)."""
    s = AGENT_ITEM.sub("", s)
    s = re.sub(r"\s*\([^()]*\)", "", s)
    s = re.sub(r"[`*_]", "", s).replace(";", ",").replace("—", "-").replace("–", "-")
    return clip(s.strip(" .,:"), 90)


def read_tasks(text: str) -> dict[str, Any]:
    """Checkbox counts and the open items under the "Needs you" heading."""
    done = len(re.findall(r"^\s*[-*]\s+\[[xX]\]", text, re.M))
    open_ = len(re.findall(r"^\s*[-*]\s+\[ \]", text, re.M))
    yours, agent = [], []
    for sec in re.split(r"^#{2,3}\s+", text, flags=re.M)[1:]:
        if not NEEDS_HEAD.match(sec.split("\n", 1)[0].strip()):
            continue
        for m in re.finditer(r"^\s*[-*]\s+\[ \]\s+(.+)$", sec, re.M):
            raw = m.group(1).strip()
            item = {"text": clip(re.sub(r"[`*]", "", raw), 300), "short": short_item(raw)}
            (agent if AGENT_ITEM.match(raw) else yours).append(item)
        break
    return {"done": done, "total": done + open_, "yours": yours, "agent": agent}


def last_commit(root: Path, tz) -> dict[str, Any] | None:
    """The newest line of .git/logs/HEAD (its tail only): when and what. No author details."""
    p = root / ".git" / "logs" / "HEAD"
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.strip().splitlines()):
        m = COMMIT.match(line)
        if m:
            try:
                when = iso(dt.datetime.fromtimestamp(int(m.group(1)), tz=tz or dt.UTC))
            except (OverflowError, OSError, ValueError):
                when = None
            msg = re.sub(r"^(commit(?: \(\w+\))?|merge [^:]*|checkout|reset|rebase[^:]*|pull[^:]*)"
                         r": ", "", m.group(2))
            return {"when": when, "msg": clip(name_only(msg, 120), 120)}
    return None


def entries(paths: dict) -> list[dict[str, Any]]:
    raw = paths.get("projects", paths.get("project"))
    if raw is None:
        raise ValueError("needs projects = [{ name, path, status }, ...]")
    if not isinstance(raw, list):
        raise ValueError("projects must be a list of { name, path, status } tables")
    return raw


def project(e: Any, i: int, *, base: Path, private_dirs: list[Path], busy_min: float,
            now: dt.datetime, seen: set[str]) -> dict[str, Any]:
    if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not e["name"].strip():
        return {"id": f"entry-{i + 1}", "name": f"Entry {i + 1}", "status": "active",
                "state": "error", "why": "badentry", "exists": False,
                "message": "This entry needs a name, a path and a status."}
    name = clip(e["name"].strip(), 80)
    status = STATUSES.get(str(e.get("status") or "active").strip().lower())
    pid = re.sub(r"[^a-z0-9-]+", "-", str(e.get("id") or name).lower()).strip("-") or f"p{i}"
    while pid in seen:
        pid += "-2"
    seen.add(pid)
    row: dict[str, Any] = {"id": pid[:120], "name": name}
    for key, n in (("short", 14), ("board", 9)):
        if isinstance(e.get(key), str) and e[key].strip():
            row[key] = clip(e[key].strip().upper(), n)
    if isinstance(e.get("note"), str) and e["note"].strip():
        row["note"] = clip(e["note"], 160)
    if _date(e.get("since")):
        row["since"] = _date(e.get("since"))
    if status is None:
        return row | {"status": "active", "state": "error", "why": "badstatus", "exists": False,
                      "message": "status must be active, paused or closed."}
    row["status"] = status
    raw_path = e.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return row | {"state": "missing", "why": "nopath", "exists": False}
    root = _abs(raw_path.strip(), base)
    private = bool(e.get("private")) or is_private(root, private_dirs)
    if private:  # the deny-list wins: shown paused, and only the folder itself is looked at
        there = _isdir(root)
        return row | {"status": "paused", "private": True, "exists": there,
                      "state": "ok" if there else "missing", "why": "private"}
    there = _isdir(root)
    row["exists"] = there
    if not there:
        return row | {"state": "missing", "why": "nofolder"}
    row |= {"state": "ok", "why": status}
    if status != "active":
        return row
    row["href"] = file_uri(root)
    tz = now.tzinfo
    tp = root / "TASKS.md"
    stamps = [mtime(tp, tz), mtime(root / ".git" / "logs" / "HEAD", tz),
              mtime(root / ".git" / "index", tz)]
    if tp.is_file():
        try:
            text = tp.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return row | {"state": "error", "why": "unreadable", "tasks_href": file_uri(tp)}
        t = read_tasks(text)
        row["tasks_href"] = file_uri(tp)
        if t["total"]:
            row["progress"] = {"done": t["done"], "total": t["total"]}
        row["needs"] = [dict(x, href=row["tasks_href"]) for x in t["yours"][:MAX_ITEMS]]
        row["needs_n"] = len(t["yours"])
        row["agent_items"] = t["agent"][:MAX_ITEMS]
        row["agent_n"] = len(t["agent"])
        row["tasks_changed"] = iso(stamps[0])
    else:
        row["why"] = "notasks"
    commit = last_commit(root, tz)
    if commit:
        row["last_commit"] = commit
    recent = [s for s in stamps if s]
    if recent:
        row["updated"] = iso(max(recent))
    row["busy"] = bool(recent) and dt.timedelta(0) <= now - max(recent) <= dt.timedelta(
        minutes=busy_min)
    if row.get("needs_n"):
        row.update(state="waiting", why="yours")
    elif row["busy"]:
        row.update(state="working", why="busy")
    return row


def collect(paths: dict, now: dt.datetime) -> dict[str, Any]:
    if now.tzinfo is None:
        now = now.astimezone()
    base = Path(str(paths.get("_config_dir") or "."))
    raw_private = paths.get("private_dirs") or []
    if isinstance(raw_private, str):
        raw_private = [raw_private]
    private_dirs = [_abs(str(p), base) for p in raw_private]
    busy_min = paths.get("busy_minutes", BUSY_MINUTES)
    if isinstance(busy_min, bool) or not isinstance(busy_min, int | float) or busy_min < 0:
        raise ValueError("busy_minutes must be a number of minutes, 0 or more")
    seen: set[str] = set()
    rows = []
    for i, e in enumerate(entries(paths)):
        try:
            rows.append(project(e, i, base=base, private_dirs=private_dirs,
                                busy_min=busy_min, now=now, seen=seen))
        except Exception as ex:  # one project never takes the room down
            name = clip(str(e.get("name") if isinstance(e, dict) else "") or f"Entry {i + 1}", 80)
            rows.append({"id": f"entry-{i + 1}", "name": name, "status": "active",
                         "state": "error", "why": "crash", "exists": True,
                         "message": clip(f"{type(ex).__name__}: {ex}", 300)})
    active = [r for r in rows if r["status"] == "active"]
    state = worst(r["state"] for r in active) if active else ("ok" if rows else "missing")
    needs = [{"text": clip(f"{r['name']}: {n['text']}", 300), **({"href": n["href"]}
                                                                   if n.get("href") else {})}
             for r in active for n in r.get("needs", [])]
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("active", "paused", "closed")}
    out: dict[str, Any] = {
        "state": state, "rows": rows,
        "label": clip(", ".join(f"{n} {s}" for s, n in counts.items() if n) or "no projects", 120),
    }
    if needs:
        out["needs"] = needs[:500]
    return out
