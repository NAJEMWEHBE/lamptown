"""Shelf room collectors: the skills, plugins or tools an agent has installed (the flag locker).

Two kinds:

``skill_dirs`` (:func:`collect`)
    Every sub-folder of the folders in ``dirs`` is one item. An item is *installed* when its
    manifest (``manifest``, default ``SKILL.md``) gives a name and a description, and *broken* when
    it can't be read. A folder with no manifest of its own but a bundle manifest
    (``bundle_manifest``, default ``manifest.json``) in it or one level down is a *bundle* synced
    in by another app: the room shows it as one sea chest with its count, never as an item to fix.
    With ``staging_dir`` set, its sub-folders are items on the way in: *staged* (a readable
    manifest, waiting for you to check and install it) or *building* (no manifest yet, or one that
    can't be read yet). An item already installed is not listed again from staging; folders
    starting with ``.`` or ``_`` are skipped.

``staging_dir`` (:func:`collect_staging`)
    Only the staging part, for a building that has its installed items from another source.

```toml
[[building]]
id = "skills"
type = "shelf"
  [[building.source]]
  kind = "skill_dirs"
  dirs = ["~/.agents/skills"]        # or dir = "..."
  staging_dir = "~/skills-build"     # optional
  manifest = "SKILL.md"              # optional; a .json manifest is read for "name"/"description"
  noun = "skill"                     # optional: what the room calls an item (skill, plugin, tool)
  shortnames_file = "./plates.json"  # optional: {"folder": "PLATE"} or {"folder": {"short": ..}}
```

Rows: ``{id, name, short, line, state, stage, flag, href}``. ``stage`` is installed / staged /
building / broken and maps to the state ok / waiting / working / error. ``short`` is the brass
plate (7 letters fit), ``line`` one plain sentence from the description. ``flag`` (``{pat, a, b}``)
is the flag's look, from a hash of the folder name in real signal-flag colours: identity only,
never a status. Nothing here depends on the clock. Read-only; stdlib only.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from lamptown.collectors import worst

PLATE_LETTERS = 7  # what a brass plate holds at 4 columns
LINE_MAX = 60  # the tooltip / tag line
NAME_MAX = 120
MAX_ROWS = 2000
FILLER = {
    "a",
    "an",
    "the",
    "to",
    "of",
    "and",
    "or",
    "for",
    "my",
    "in",
    "on",
    "with",
    "skill",
    "skills",
    "plugin",
    "plugins",
    "tool",
    "tools",
}
# signal-flag colour pairs (field, charge) as on real code flags: red, white, blue, yellow, black
_PAIRS = [
    ("r", "w"),
    ("b", "w"),
    ("y", "b"),
    ("y", "r"),
    ("w", "k"),
    ("y", "k"),
    ("w", "r"),
    ("b", "y"),
    ("w", "b"),
]
PAIRS = _PAIRS + [(b, a) for a, b in _PAIRS if (b, a) not in _PAIRS]
PATTERNS = 8  # vertical, horizontal, cross, saltire, border, quarters, three stripes, diagonal
FONT_OK = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!/-:><.,'?+()%=_&\"[]# ")
STAGE_STATE = {"installed": "ok", "broken": "error", "staged": "waiting", "building": "working"}
NOUN_RE = re.compile(r"^[a-z][a-z -]{0,19}$")


# ----------------------------------------------------------------------------- small readers
def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def _is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


def _is_file(p: Path) -> bool:
    try:
        return p.is_file()
    except OSError:
        return False


def _subdirs(p: Path) -> list[Path]:
    try:
        kids = [x for x in p.iterdir() if _is_dir(x)]
    except OSError:
        return []
    return sorted(kids, key=lambda x: x.name.lower())


def _uri(p: Path) -> str:
    try:
        return p.resolve().as_uri()
    except (OSError, ValueError):
        return ""


def _leaf(p: Path) -> str:
    """A folder's own name, from a path written for any OS (the town shows names, not paths)."""
    parts = [x for x in re.split(r"[\\/]+", str(p)) if x]
    return parts[-1][:120] if parts else ""


def _paths(v: Any) -> list[Path]:
    if isinstance(v, str) and v.strip():
        return [Path(v)]
    if isinstance(v, list):
        return [Path(x) for x in v if isinstance(x, str) and x.strip()]
    return []


def front_matter(text: str | None) -> dict[str, str]:
    """The YAML front matter's top-level ``key: value`` pairs (plain, quoted, or a ``>`` / ``|``
    block). Enough for a manifest's name and description; nested values are ignored."""
    out: dict[str, str] = {}
    text = (text or "").lstrip("﻿")
    if not text.startswith("---"):
        return out
    end = text.find("\n---", 3)
    if end < 0:
        return out
    lines = text[3:end].splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", lines[i])
        i += 1
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val in (">", "|", ">-", "|-", ">+", "|+"):
            block = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            val = (
                " ".join(b for b in block if b) if val.startswith(">") else "\n".join(block).strip()
            )
        elif len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        out[key] = val
    return out


def read_manifest(p: Path) -> tuple[str | None, dict[str, str]]:
    """(text, fields) of an item's manifest: front matter for Markdown, top-level keys for JSON."""
    text = _read(p)
    if text is None:
        return None, {}
    if p.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except ValueError:
            return text, {}
        if not isinstance(data, dict):
            return text, {}
        return text, {k: v.strip() for k, v in data.items() if isinstance(v, str) and v.strip()}
    return text, front_matter(text)


def load_overrides(p: Path | None) -> dict[str, dict[str, str]]:
    """``{folder: {"short", "line"}}``; a plain string value is a short name. A bad file -> {}."""
    if p is None:
        return {}
    raw = _read(p)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for k, v in data.items() if isinstance(data, dict) else []:
        if not isinstance(k, str) or k.startswith("_"):
            continue
        e = {"short": v} if isinstance(v, str) else v
        if isinstance(e, dict):
            out[k] = {
                f: str(e[f])
                for f in ("short", "line")
                if isinstance(e.get(f), str) and e[f].strip()
            }
    return out


# ----------------------------------------------------------------------------- names
def font_safe(s: str) -> str:
    """Only characters the pixel font has (it prints anything else as a gap)."""
    s = (
        s.replace("—", "-")
        .replace("–", "-")
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    return re.sub(r"\s+", " ", "".join(ch for ch in s if ch.upper() in FONT_OK)).strip()


def short_name(folder: str) -> str:
    """The brass plate: the first word that fits a plate (4-7 letters), skipping filler words. A 1-3
    letter word is glued to the next one when both fit, and stands alone as the last word. No fit
    at all: the first word cut to 7."""
    parts = [w for w in re.split(r"[-_\s.]+", folder) if w]
    words = [w for w in parts if w.lower() not in FILLER] or parts or [folder or "?"]
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        if len(w) <= 3 and nxt and len(w) + len(nxt) <= PLATE_LETTERS:
            return font_safe((w + nxt).upper())
        if 4 <= len(w) <= PLATE_LETTERS or (len(w) <= 3 and nxt is None):
            return font_safe(w.upper())
    return font_safe(words[0][:PLATE_LETTERS].upper()) or "?"


def plain_line(desc: str, limit: int = LINE_MAX) -> str:
    """One plain line from a description: its first sentence without a 'Use when' lead-in, cut at a
    word to ``limit`` characters ('...' when cut)."""
    s = re.sub(r"\s+", " ", desc or "").strip()
    s = re.split(r"(?<=[.!?])\s|\s[-–—]\s|;\s", s, maxsplit=1)[0].rstrip(".!? ")
    s = re.sub(
        r"^(use (this (skill|plugin|tool) )?(when(ever)?|for|to)|when)\s+(you |the user )?"
        r"(need to |needs to |want to |wants to )?",
        "",
        s,
        flags=re.I,
    )
    s = font_safe(s)
    if len(s) > limit:
        cut = s[: limit - 3].rsplit(" ", 1)[0].rstrip(",:;-( ")
        cut = re.sub(r"\s+(a|an|the|or|and|of|to|for|from|with|in|on|by)$", "", cut, flags=re.I)
        s = cut + "..."
    return s[:1].upper() + s[1:]


# ----------------------------------------------------------------------------- flags
def fnv1a(s: str) -> int:
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def assign_flags(items: list[dict[str, Any]]) -> None:
    """A pattern + colour pair per item from a hash of its id. No two flags in one room look alike:
    a clash steps to the next free colour pair (while pairs last), then to the next free pattern."""
    used_pairs: set[int] = set()
    used: set[tuple[int, int]] = set()
    for it in items:
        h = fnv1a(it["id"])
        pi, pat = h % len(PAIRS), (h >> 8) % PATTERNS
        if len(used_pairs) < len(PAIRS):
            while pi in used_pairs:
                pi = (pi + 1) % len(PAIRS)
        k = 0
        while (pi, pat) in used and k < len(PAIRS) * PATTERNS:
            pat = (pat + 1) % PATTERNS
            if pat == (h >> 8) % PATTERNS:
                pi = (pi + 1) % len(PAIRS)
            k += 1
        used_pairs.add(pi)
        used.add((pi, pat))
        a, b = PAIRS[pi]
        it["flag"] = {"pat": pat, "a": a, "b": b}


# ----------------------------------------------------------------------------- items
def _bundle_manifest(d: Path, name: str) -> Path | None:
    for c in [d, *[x for x in _subdirs(d) if not x.name.startswith(".")]]:
        m = c / name
        if _is_file(m):
            return m
    return None


def bundle(d: Path, man: Path, item_manifest: str) -> dict[str, Any]:
    """A folder another app syncs items into: how many and a few names. The count comes from the
    manifest's ``items``/``skills``/``plugins`` list (or a top-level list), else from the
    sub-folders (one or two levels down) holding an item manifest; neither -> count None (the
    chest shows '-')."""
    names: list[str] = []
    count: int | None = None
    try:
        data = json.loads(_read(man) or "")
    except ValueError:
        data = None
    lst = None
    if isinstance(data, dict):
        lst = next(
            (
                data[k]
                for k in ("items", "skills", "plugins", "tools")
                if isinstance(data.get(k), list)
            ),
            None,
        )
    elif isinstance(data, list):
        lst = data
    if lst is not None:
        count = len(lst)
        for e in lst:
            n = (e.get("name") or e.get("id")) if isinstance(e, dict) else e
            if isinstance(n, str) and n.strip():
                names.append(font_safe(n.strip()))
    else:
        subs = [x for x in _subdirs(d) if not x.name.startswith(".")]
        found = sorted(
            {x.name for c in [d, *subs] for x in _subdirs(c) if _is_file(x / item_manifest)},
            key=str.lower,
        )
        count, names = (len(found) or None), [font_safe(n) for n in found]
    return {
        "name": d.name[:NAME_MAX],
        "count": count,
        "names": [n for n in names if n][:40],
        "href": _uri(d),
    }


def item(d: Path, stage_if_ok: str, ov: dict[str, dict[str, str]], manifest: str) -> dict[str, Any]:
    """One folder -> one row (``stage_if_ok`` is installed or staged)."""
    o = ov.get(d.name, {})
    row: dict[str, Any] = {
        "id": d.name[:NAME_MAX],
        "name": d.name[:NAME_MAX],
        "short": font_safe(o.get("short", "").upper())[:12] or short_name(d.name),
    }
    mf = d / manifest
    staging = stage_if_ok == "staged"
    if not _is_file(mf):
        if staging:
            row.update(
                stage="building",
                href=_uri(d),
                line=o.get("line") or f"Being built: no {manifest} yet",
            )
        else:
            row.update(
                stage="broken", href=_uri(d), line=f"Can't read: no {manifest} in this folder"
            )
        return row
    text, fm = read_manifest(mf)
    if text is None or not fm.get("name") or not fm.get("description"):
        why = "can't be opened" if text is None else "has no name or description"
        row.update(
            stage="building" if staging else "broken",
            href=_uri(mf),
            line=("Being built: its " if staging else "Can't read: its ") + f"{manifest} {why}",
        )
        return row
    row.update(
        stage=stage_if_ok,
        name=font_safe(fm["name"])[:NAME_MAX] or row["name"],
        href=_uri(mf),
        line=font_safe(o["line"])[:LINE_MAX] if o.get("line") else plain_line(fm["description"]),
    )
    return row


def _staging_rows(staging: Path, taken: set[str], ov: dict, manifest: str) -> list[dict[str, Any]]:
    rows = []
    for d in _subdirs(staging):
        if d.name.startswith(("_", ".")) or d.name.lower() in taken:
            continue
        rows.append(item(d, "staged", ov, manifest))
    return rows


def _say(state: str, counts: dict[str, int], found: bool, any_rows: bool, noun: str) -> str:
    n = counts
    if not found:
        return f"Can't see the {noun}s folder"
    if not any_rows and not n["synced"]:
        return f"No {noun}s installed yet"
    return {
        "ok": f"All {n['installed']} installed",
        "error": f"{n['broken']} can't be read",
        "waiting": f"{n['staged']} waiting for you to check",
        "working": f"{n['building']} being built",
        "missing": f"Can't see the {noun}s folder",
    }[state]


def _noun(paths: dict) -> str:
    n = paths.get("noun", "skill")
    return n.strip().lower() if isinstance(n, str) and NOUN_RE.match(n.strip().lower()) else "skill"


def _manifest(paths: dict) -> str:
    m = paths.get("manifest", "SKILL.md")
    return m if isinstance(m, str) and m and "/" not in m and "\\" not in m else "SKILL.md"


def _finish(rows: list[dict[str, Any]], states: Iterable[str]) -> str:
    for r in rows:
        r["state"] = STAGE_STATE[r["stage"]]
    assign_flags(rows)
    st = list(states)
    return worst(st) if st else "ok"


def collect(paths: dict, now: dt.datetime) -> dict[str, Any]:
    """``skill_dirs``: installed items (+ staging when ``staging_dir`` is set)."""
    del now  # nothing here follows the clock
    noun, manifest = _noun(paths), _manifest(paths)
    bundle_name = paths.get("bundle_manifest", "manifest.json")
    dirs = _paths(paths.get("dirs")) + _paths(paths.get("dir"))
    staging = _paths(paths.get("staging_dir"))
    ov = load_overrides(next(iter(_paths(paths.get("shortnames_file"))), None))
    found_dirs = [d for d in dirs if _is_dir(d)]
    found = bool(found_dirs)
    rows: list[dict[str, Any]] = []
    bundles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in found_dirs:
        for d in _subdirs(root):
            if d.name.startswith((".", "_")) or d.name.lower() in seen:
                continue
            seen.add(d.name.lower())
            man = None
            if not _is_file(d / manifest) and isinstance(bundle_name, str) and bundle_name:
                man = _bundle_manifest(d, bundle_name)
            if man:
                bundles.append(bundle(d, man, manifest))
            else:
                rows.append(item(d, "installed", ov, manifest))
    staging_found = bool(staging) and _is_dir(staging[0])
    if staging_found:
        rows += _staging_rows(staging[0], seen, ov, manifest)
    order = {"installed": 0, "broken": 1, "staged": 2, "building": 3}
    rows.sort(key=lambda r: order[r["stage"]])
    rows = rows[:MAX_ROWS]
    state = _finish(rows, [STAGE_STATE[r["stage"]] for r in rows] + ([] if found else ["missing"]))
    counts = {k: sum(1 for r in rows if r["stage"] == k) for k in order}
    counts["synced"] = sum(b["count"] or 0 for b in bundles)
    out: dict[str, Any] = {
        "state": state,
        "label": _say(state, counts, found, bool(rows), noun),
        "noun": noun,
        "manifest": manifest,
        "found": found,
        "counts": counts,
        "rows": rows,
        "bundles": bundles,
        "folder": _leaf(dirs[0]) if dirs else "",  # review-fix C5: its name, never its path
        "folderHref": _uri(found_dirs[0]) if found else "",
        "stagingFound": staging_found,
        "stagingHref": _uri(staging[0]) if staging_found else "",
    }
    if not dirs:
        out["label"] = f"No {noun}s folder set up (dirs = [...])"
    return out


def collect_staging(paths: dict, now: dt.datetime) -> dict[str, Any]:
    """``staging_dir``: only the items on their way in (``dir`` or ``staging_dir``)."""
    del now
    noun, manifest = _noun(paths), _manifest(paths)
    where = (_paths(paths.get("dir")) or _paths(paths.get("staging_dir")))[:1]
    ov = load_overrides(next(iter(_paths(paths.get("shortnames_file"))), None))
    if not where or not _is_dir(where[0]):
        return {
            "state": "missing",
            "label": "Can't see the staging folder",
            "noun": noun,
            "rows": [],
            "stagingFound": False,
        }
    rows = _staging_rows(where[0], set(), ov, manifest)[:MAX_ROWS]
    state = _finish(rows, [STAGE_STATE[r["stage"]] for r in rows])
    counts = {k: sum(1 for r in rows if r["stage"] == k) for k in ("staged", "building")}
    label = (
        f"{counts['staged']} waiting for you to check"
        if counts["staged"]
        else f"{counts['building']} being built"
        if counts["building"]
        else "Nothing in staging"
    )
    return {
        "state": state,
        "label": label,
        "noun": noun,
        "rows": rows,
        "stagingFound": True,
        "stagingHref": _uri(where[0]),
    }
