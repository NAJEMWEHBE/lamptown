"""The ``markdown_vault`` collector: a folder of Markdown notes, its inbox and its reviews.

```toml
[[building.source]]
kind = "markdown_vault"
path = "~/notes"                     # the vault folder
inbox = "inbox/*.md"                 # optional; inside the vault; the newest file is the inbox
item = '^##\\s+'                      # optional: where each inbox note starts
triage = 'triage:\\s*(keep|drop)'     # optional: a match sorts a note; group 1 "drop" drops it
dropped = "inbox/dropped"            # optional: a folder of dropped notes
kept = "inbox/kept"                  # optional: a folder of kept notes (its own drying line)
reviews = "reviews/*.md"             # optional: review notes with a YYYY-MM-DD date in the name
log = "log.md"                       # optional: '## [YYYY-MM-DD] review' lines count as reviews
schedule = "weekly tue 07:15"        # optional: when the review runs (next review, missed rule)
run_hours = 3                        # optional: how long after its slot a review may still run
exclude = ["templates"]              # optional: top-level folders that are not note shelves
labels = { field-notes = "FIELD" }   # optional: board names (and their order)
```

**Folders are found automatically**: every top-level folder with ``.md`` files in it (counted
through its subfolders) is one shelf, except hidden ones (``.`` or ``_``), the ``exclude`` list
and the folders that hold the inbox, the reviews and the dropped and kept notes. Notes right in
the vault folder are one shelf too ("LOOSE NOTES").

State (``why`` names the case): ``missing``/``nosource`` (no vault folder), ``working``/``running``
(inside the review's slot, no review logged for it yet, and notes changed since it began),
``error``/``missed`` (the last review is older than the schedule's period + 15 %),
``waiting``/``inbox`` (inbox notes with no triage mark), ``missing``/``never`` (reviews are set up
but none is on record yet), else ``ok``.

Read-only and stdlib only. It lists files and reads the inbox note and the log file.
"""

from __future__ import annotations

import datetime as dt
import glob as globmod
import os
import re
from pathlib import Path
from typing import Any

from lamptown.collectors.jobs import clip, file_uri, iso, mtime, regex
from lamptown.collectors.jobs_schedule import LOCAL
from lamptown.collectors.jobs_schedule import parse as parse_schedule

MAX_FILES = 20000  # notes counted at most (the walk stops there)
MAX_NAMES = 400  # note names sent per shelf (the count is always the true one)
MAX_ITEMS = 500  # inbox notes sent
RUN_HOURS = 3
ITEM = r"^##\s+"
TRIAGE = r"triage:\s*(keep|drop)\b"
FROM = r"^\s*[*_]*(?:from|target)[*_]*\s*:?[*_]*\s*:?\s*\[*([^\]|\n]+?)\]*\s*$"
LOG_LINE = r"^##\s+\[(\d{4}-\d\d-\d\d)\]\s*review\b"
DROP_WORDS = {"drop", "dropped", "no", "skip", "reject", "rejected"}
_DAY = re.compile(r"(\d{4}-\d\d-\d\d)")


def _day(s: str | None) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def _inside(root: Path, rel: str | None) -> Path | None:
    """A vault-relative path (or an absolute one, kept as written)."""
    if not rel or not isinstance(rel, str):
        return None
    p = Path(os.path.expanduser(rel))
    return p if p.is_absolute() else root / p


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _top(root: Path, p: Path | None) -> str | None:
    """The top-level vault folder ``p`` sits in (None when outside the vault or at its root)."""
    if p is None:
        return None
    try:
        parts = p.relative_to(root).parts
    except ValueError:
        return None
    first = next((x for x in parts if x not in (".", "")), None)
    return first if first and len(parts) > 1 and not re.search(r"[*?[]", first) else None


def _md_files(folder: Path, budget: list[int]) -> list[Path]:
    """Every .md file under ``folder`` (hidden folders skipped), within the shared budget."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith((".", "_")))
        for f in sorted(filenames):
            if f.lower().endswith(".md") and not f.startswith("."):
                if budget[0] <= 0:
                    return out
                budget[0] -= 1
                out.append(Path(dirpath) / f)
    return out


def _label(name: str, labels: dict) -> str:
    if isinstance(labels.get(name), str) and labels[name].strip():
        return labels[name].strip()[:40]
    words = name.replace("-", " ").replace("_", " ")
    return re.sub(r"[^A-Za-z0-9 ]", " ", words).upper().strip()[:24]


def shelves(root: Path, skip: set[str], labels: dict, budget: list[int],
            ignore: set[str] = frozenset()) -> list[dict]:
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    out = []
    loose = [p for p in entries if p.is_file() and p.suffix.lower() == ".md"
             and not p.name.startswith(".") and _norm(p) not in ignore]
    for p in entries:
        if not p.is_dir() or p.name.startswith((".", "_")) or p.name in skip:
            continue
        files = _md_files(p, budget)
        if files:
            out.append({"key": p.name, "label": _label(p.name, labels), "n": len(files),
                        "notes": [f.stem[:80] for f in files[:MAX_NAMES]], "href": file_uri(p)})
    order = [k for k in labels if isinstance(k, str)]
    out.sort(key=lambda s: (order.index(s["key"]) if s["key"] in order else len(order),
                            s["key"].lower()))
    if loose:
        budget[0] -= len(loose)
        out.append({"key": ".", "label": "LOOSE NOTES", "n": len(loose),
                    "notes": [f.stem[:80] for f in loose[:MAX_NAMES]], "href": file_uri(root)})
    return out


def _items(item: re.Pattern[str], text: str) -> list[str]:
    """The text after each ``item`` match, up to the next one. Not ``item.split()``: a pattern
    with a group (``^(##|###)\\s+``) would add each captured heading mark as an item."""
    starts = list(item.finditer(text))
    return [text[m.end():(starts[i + 1].start() if i + 1 < len(starts) else len(text))]
            for i, m in enumerate(starts)]


def read_inbox(path: Path, paths: dict, tz: dt.tzinfo | None) -> dict[str, Any]:
    item = regex(paths, "item") or re.compile(ITEM, re.M)
    triage = regex(paths, "triage") or re.compile(TRIAGE, re.M | re.I)
    from_re = regex(paths, "from") or re.compile(FROM, re.M | re.I)
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        text = None
    items = []
    for part in _items(item, text or ""):
        chunk = re.split(r"^---\s*$", part, maxsplit=1, flags=re.M)[0]
        lines = chunk.splitlines()
        title = re.sub(r"^\d+[.)]?\s*", "", lines[0] if lines else "").strip().strip("*_ ")
        m = triage.search(chunk)
        word = (m.group(1) if m and m.re.groups else (m.group(0) if m else "")).strip().lower()
        mark = None if not m else ("drop" if word in DROP_WORDS else "keep")
        reason = ""
        if m and mark == "drop":
            rest = chunk[m.end():].split("\n", 1)[0]
            reason = clip(re.sub(r"[`*_]", "", rest).strip(" :-"), 120)
        f = from_re.search(chunk)
        n = len(items) + 1
        it = {"n": n, "id": f"{path.stem}#{n}", "title": clip(title or "Untitled note", 120),
              "mark": mark, "triaged": mark is not None}
        if reason:
            it["reason"] = reason
        if f and f.group(1).strip():
            it["from"] = clip(f.group(1).strip().split("/")[-1], 120)
        items.append(it)
    day = _DAY.search(path.stem)
    date = day.group(1) if day and _day(day.group(1)) else None
    if not date and text:
        fm = re.search(r"^created:\s*(\d{4}-\d\d-\d\d)", text, re.M)
        date = fm.group(1) if fm and _day(fm.group(1)) else None
    if not date:
        t = mtime(path, tz)
        date = t.date().isoformat() if t else None
    out = {"date": date, "file": path.name[:120], "href": file_uri(path),
           "items": items[:MAX_ITEMS],
           "total": len(items), "untriaged": sum(1 for i in items if not i["mark"]),
           "kept": sum(1 for i in items if i["mark"] == "keep"),
           "drops": sum(1 for i in items if i["mark"] == "drop")}
    if text is None:
        out["unreadable"] = True
    return out


def _glob(pattern: Path, root: Path | None = None) -> list[str]:
    """``glob`` on a pattern; inside the vault, the vault's own path is escaped first (a vault
    folder named ``kites [north]`` is a name, not a pattern). Review-fix C6."""
    text = str(pattern)
    if root is not None:
        try:
            rel = pattern.relative_to(root)
        except ValueError:
            pass
        else:
            text = os.path.join(globmod.escape(str(root)), str(rel))
    return globmod.glob(text, recursive=True)


def newest(pattern: Path | None, root: Path | None = None) -> Path | None:
    if pattern is None:
        return None
    files = [Path(f) for f in _glob(pattern, root) if Path(f).is_file()]
    if not files:
        return None

    def key(p: Path) -> tuple[str, str]:
        d = _DAY.search(p.stem)
        return (d.group(1) if d and _day(d.group(1)) else "", p.name.lower())

    return max(files, key=key)


def last_review(root: Path, paths: dict) -> tuple[dt.date | None, Path | None, bool]:
    """(newest review day, its note, reviews are set up at all)."""
    pat, log = _inside(root, paths.get("reviews")), _inside(root, paths.get("log"))
    days: list[tuple[dt.date, Path | None]] = []
    if pat is not None:
        for f in _glob(pat, root):
            d = _DAY.search(Path(f).stem)
            if d and _day(d.group(1)):
                days.append((_day(d.group(1)), Path(f)))
    if log is not None:
        line = regex(paths, "review_line") or re.compile(LOG_LINE, re.M | re.I)
        try:
            text = log.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            text = ""
        for m in line.finditer(text):
            d = _day(m.group(1) if m.re.groups else None)
            if d:
                days.append((d, None))
    if not days:
        return None, None, pat is not None or log is not None
    best = max(d for d, _ in days)
    note = next((p for d, p in sorted(days, key=lambda x: x[0], reverse=True)
                 if d == best and p is not None), None)
    return best, note, True


def changed_since(root: Path, since: dt.datetime, skip: Path | None, tz) -> bool:
    budget = [MAX_FILES]
    for f in _md_files(root, budget):
        if skip is not None and skip in f.parents:
            continue
        t = mtime(f, tz)
        if t and t >= since:
            return True
    return False


def collect(paths: dict, now: dt.datetime) -> dict[str, Any]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=LOCAL)  # review-fix C6: slots follow the local clock
    if not paths.get("path"):
        raise ValueError('needs path = "..." (the vault folder)')
    root = Path(str(paths["path"]))
    name = root.name[:120]
    sched = parse_schedule(str(paths["schedule"])) if paths.get("schedule") else None
    base: dict[str, Any] = {"vault_name": name, "folders": [], "notes": 0}
    if sched:
        nxt = sched.next_after(now)
        base.update(next_review=iso(nxt), review_when=sched.words())
    if not root.is_dir():
        return base | {"state": "missing", "why": "nosource", "label": "can't see the vault folder"}
    labels = paths.get("labels") if isinstance(paths.get("labels"), dict) else {}
    exclude = paths.get("exclude") or []
    if isinstance(exclude, str):
        exclude = [exclude]
    inbox_pat = _inside(root, paths.get("inbox"))
    dropped_dir, kept_dir = _inside(root, paths.get("dropped")), _inside(root, paths.get("kept"))
    reviews_pat = _inside(root, paths.get("reviews"))
    skip = {str(x) for x in exclude} | {
        t for t in (_top(root, inbox_pat), _top(root, reviews_pat), _top(root, dropped_dir),
                    _top(root, kept_dir)) if t}
    budget = [MAX_FILES]
    log = _inside(root, paths.get("log"))
    folders = shelves(root, skip, labels, budget, {_norm(log)} if log is not None else set())
    notes = sum(f["n"] for f in folders)
    out = base | {"folders": folders, "notes": notes}
    if kept_dir is not None and kept_dir.is_dir():
        kept = _md_files(kept_dir, budget)
        out["kept"] = len(kept)
        folders.append({"key": "kept", "label": _label("kept", labels) if "kept" in labels
                        else "KEPT", "n": len(kept), "kept": True,
                        "notes": [f.stem[:80] for f in kept[:MAX_NAMES]],
                        "href": file_uri(kept_dir)})
    if dropped_dir is not None:
        gone = _md_files(dropped_dir, budget) if dropped_dir.is_dir() else []
        out["dropped"] = {"n": len(gone), "names": [f.stem[:80] for f in gone[:MAX_NAMES]],
                          "href": file_uri(dropped_dir) if dropped_dir.is_dir() else ""}
    ib = newest(inbox_pat, root)
    inbox = read_inbox(ib, paths, now.tzinfo) if ib else None
    if inbox:
        out["inbox"] = inbox
    day, note, reviewed = last_review(root, paths)
    hour, minute = (sched.hour, sched.minute) if sched else (12, 0)
    last = (dt.datetime.combine(day, dt.time(hour, minute), tzinfo=now.tzinfo) if day else None)
    if last is not None and last > now:
        last = now.replace(microsecond=0)
    out["last_review"] = iso(last)
    if note is not None:
        out["last_review_href"] = file_uri(note)
    if log is not None and log.is_file():
        out["log_href"] = file_uri(log)
    run_hours = paths.get("run_hours", RUN_HOURS)
    if isinstance(run_hours, bool) or not isinstance(run_hours, int | float) or run_hours < 0:
        raise ValueError("run_hours must be a number of hours, 0 or more")
    untriaged = inbox["untriaged"] if inbox else 0
    slot = sched.last_slot(now) if sched else None
    inbox_dir = ib.parent if ib else None
    running = bool(
        slot is not None and dt.timedelta(0) <= now - slot <= dt.timedelta(hours=run_hours)
        and not (day and day >= slot.date()) and changed_since(root, slot, inbox_dir, now.tzinfo))
    if running:
        state, why, label = "working", "running", "the review is running now"
    elif sched and last and sched.missed(last, now):
        state, why, label = "error", "missed", f"the review missed its run (last {day})"
    elif untriaged:
        state, why = "waiting", "inbox"
        label = f"{untriaged} inbox note{'' if untriaged == 1 else 's'} to sort"
    elif reviewed and not day:
        state, why, label = "missing", "never", "no review on record yet"
    else:
        state, why, label = "ok", "ok", f"{notes} note{'' if notes == 1 else 's'}, all sorted"
    out.update(state=state, why=why, label=clip(label, 120))
    if untriaged:
        need = {"text": clip(f"{untriaged} inbox note{'' if untriaged == 1 else 's'} to sort", 300)}
        if inbox and inbox["href"]:
            need["href"] = inbox["href"]
        out["needs"] = [need]
    return out
