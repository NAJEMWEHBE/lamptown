"""Ledger room collector: the two-signature register (kind ``scan_glob``).

One row per tool. Two signatures close a row:

1. the scanner's: the newest scan file of that tool (its verdict, a risk from 0 to 100 and how
   many flags it raised), found by the ``glob`` patterns (third-party tools) and ``own_glob``
   patterns (tools built here);
2. yours: the call you recorded in the ``decisions_file`` (install, skip or park).

Rows are every tool with a scan, every item installed in ``installed_dir`` (a sub-folder with a
``manifest``, default ``SKILL.md``; folders another app syncs in, with a ``bundle_manifest``, are
only counted) and every tool in the decisions file.

```toml
[[building]]
id = "gate"
type = "ledger"
  [[building.source]]
  kind = "scan_glob"
  glob = ["~/vetting/*/scans/*.json"]       # scans of third-party tools
  own_glob = "~/skills-build/scans/*.json"  # optional: scans of tools built here
  installed_dir = "~/.agents/skills"        # optional: what is installed (str or list)
  own_dir = "~/skills-build"                # optional: its sub-folder names are home-made
  decisions_file = "./vet-decisions.json"   # optional: your calls
  scanning_minutes = 10                     # optional: an unreadable scan younger than this
                                            #   is still being written
  format = "auto"                           # optional: auto | generic | skillspector
  noun = "skill"                            # optional: what the room calls an installed item
```

Scan files (``format = "auto"`` reads both):

* **generic**: ``{"tool": "kite-knots", "verdict": "safe", "risk": 12, "flags": 2,
  "scanned_at": "2026-03-01T10:00:00+00:00", "scanner": "any name"}``. ``verdict`` is ``safe``,
  ``caution`` or ``block`` (``pass``/``ok``, ``warn``/``review`` and ``fail``/``unsafe``/
  ``do_not_install`` are read too; other words are shown as written). ``risk`` (or ``score``) is
  0-100. ``flags`` is a count or a list of findings. ``scanned_at`` must carry an offset; without
  one the file's own time is used. ``tool`` (or ``name``) defaults to the file name, without a
  ``-r2`` style round suffix.
* **skillspector**: SkillSpector's JSON report: ``risk_assessment.recommendation`` / ``score`` /
  ``severity``, ``skill.name`` / ``scanned_at``, and ``issues`` (or ``issue_count``).

Decisions file: ``{"tools": {"kite-knots": {"call": "install", "date": "2026-03-01",
"origin": "third-party", "note": "read every flag", "source": "https://..."}}}``. ``origin`` is
``own`` or ``third-party``. A missing file means no calls are recorded; one that can't be read
turns the room red.

Row kinds (state): ``signed`` scan + call (ok); ``waiting`` scanned, no call, not installed
(waiting); ``scanning`` its newest scan file is still being written (working); ``noscan``
installed with no scan, third-party or unknown origin (error); ``unreadable`` its newest scan
can't be read (error); ``skipped-in`` you skipped it, yet it is installed (error); and three grey
ones that never raise the room's state: ``homemade`` installed, built here, no scan; ``norecord``
scanned and installed, no call recorded; ``unscanned`` a call with no scan, not installed. The
room's state is the worst of the non-grey rows; only grey rows, or none, is ``missing``.
Read-only; stdlib only; times are absolute (ISO 8601, UTC), newest row first.
"""

from __future__ import annotations

import datetime as dt
import glob as globmod
import json
import math
import re
from pathlib import Path
from typing import Any

from lamptown.collectors.shelf import (
    _bundle_manifest,
    _is_dir,
    _is_file,
    _paths,
    _subdirs,
    _uri,
    bundle,
    font_safe,
)

SCANNING_MIN = 10
MAX_SCANS = 5000  # scan files read per source
MAX_BYTES = 4_000_000  # a bigger scan file is not read
CALLS = ("install", "skip", "park")
ORIGINS = ("own", "third-party")
KIND_STATE = {
    "signed": "ok",
    "waiting": "waiting",
    "scanning": "working",
    "noscan": "error",
    "unreadable": "error",
    "skipped-in": "error",
    "homemade": "missing",
    "norecord": "missing",
    "unscanned": "missing",
}
ORDER = ("error", "waiting", "working", "ok")
VERDICTS = {
    "SAFE": "SAFE",
    "PASS": "SAFE",
    "OK": "SAFE",
    "CAUTION": "CAUTION",
    "WARN": "CAUTION",
    "WARNING": "CAUTION",
    "REVIEW": "CAUTION",
    "BLOCK": "DO_NOT_INSTALL",
    "FAIL": "DO_NOT_INSTALL",
    "UNSAFE": "DO_NOT_INSTALL",
    "DANGER": "DO_NOT_INSTALL",
    "DO_NOT_INSTALL": "DO_NOT_INSTALL",
}
WORD = {"SAFE": "SAFE", "CAUTION": "CAUTION", "DO_NOT_INSTALL": "DO NOT INSTALL"}
NOUN_RE = re.compile(r"^[a-z][a-z -]{0,19}$")


# ----------------------------------------------------------------------------- small helpers
def _iso(d: dt.datetime | None) -> str | None:
    if d is None:
        return None
    try:
        return d.astimezone(dt.UTC).isoformat(timespec="minutes")
    except (ValueError, OverflowError, OSError):
        return None


def _mtime(p: Path) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(p.stat().st_mtime, tz=dt.UTC)
    except (OSError, ValueError, OverflowError):
        return None


def aware(s: Any) -> dt.datetime | None:
    """An ISO time with an offset, else None (a time without one says nothing absolute)."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        d = dt.datetime.fromisoformat(s.strip().replace("z", "Z"))
    except ValueError:
        return None
    return d if d.tzinfo is not None else None


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if math.isfinite(f) and abs(f) < 1e9 else None


def tool_name(stem: str) -> str:
    """kite-knots.json, kite-knots-r2.json, kite-knots-scan.json -> kite-knots."""
    s = stem.strip().lower()
    s = re.sub(r"[-_.]scan$", "", s)
    return re.sub(r"[-_]r\d+$", "", s) or stem


def verdict_of(v: Any) -> str | None:
    s = re.sub(r"[^A-Z_]", "", str(v or "").strip().upper().replace(" ", "_").replace("-", "_"))
    return (VERDICTS.get(s) or s[:40]) if s else None


# ----------------------------------------------------------------------------- scan files
def _skillspector(j: dict) -> dict | None:
    ra = j.get("risk_assessment")
    if not isinstance(ra, dict) or not verdict_of(ra.get("recommendation")):
        return None
    sk = j.get("skill") if isinstance(j.get("skill"), dict) else {}
    issues = j.get("issues")
    nm = sk.get("name")
    return {
        "tool": nm.strip()
        if isinstance(nm, str) and nm.strip().lower() not in ("", "unknown")
        else None,
        "verdict": verdict_of(ra.get("recommendation")),
        "severity": str(ra.get("severity") or "").upper()[:20] or None,
        "risk": _int(ra.get("score")),
        "flags": len(issues) if isinstance(issues, list) else _int(j.get("issue_count")),
        "when": aware(sk.get("scanned_at")),
        "scanner": "SkillSpector",
    }


def _generic(j: dict) -> dict | None:
    v = verdict_of(j.get("verdict"))
    if not v:
        return None
    nm = j.get("tool") or j.get("name")
    fl = j.get("flags")
    risk = j.get("risk", j.get("score"))
    sev = j.get("severity")
    sc = j.get("scanner")
    return {
        "tool": nm.strip() if isinstance(nm, str) and nm.strip() else None,
        "verdict": v,
        "severity": sev.strip().upper()[:20] if isinstance(sev, str) and sev.strip() else None,
        "risk": _int(risk),
        "flags": len(fl) if isinstance(fl, list) else _int(fl),
        "when": aware(j.get("scanned_at")),
        "scanner": font_safe(sc)[:40] if isinstance(sc, str) and sc.strip() else None,
    }


def read_scan(p: Path, now: dt.datetime, fmt: str = "auto", minutes: int = SCANNING_MIN) -> dict:
    """One scan file -> ``{tool, file, href, ok, status, ...}``. Never raises."""
    rec: dict[str, Any] = {"file": p.name, "href": _uri(p), "tool": tool_name(p.stem), "ok": False}
    mt = _mtime(p)
    rec["when"] = _iso(mt)
    raw: bytes | None
    try:
        raw = p.read_bytes() if p.stat().st_size <= MAX_BYTES else b"\0too big"
    except OSError:
        raw = None
    j = None
    if raw is not None and raw.strip() and raw != b"\0too big":
        try:
            j = json.loads(raw.decode("utf-8-sig", errors="replace"))
        except ValueError:
            j = None
    got = None
    if isinstance(j, dict):
        if fmt in ("auto", "skillspector"):
            got = _skillspector(j)
        if got is None and fmt in ("auto", "generic"):
            got = _generic(j)
    if got is None:
        young = mt is not None and dt.timedelta(0) <= now - mt <= dt.timedelta(minutes=minutes)
        rec["status"] = "scanning" if young and raw is not None else "unreadable"
        rec["why"] = (
            "can't open the file"
            if raw is None
            else "file too big"
            if raw == b"\0too big"
            else "empty file"
            if not raw.strip()
            else "not a scan result this room can read"
        )
        return rec
    risk = got["risk"]
    rec.update(
        ok=True,
        status="ok",
        tool=got["tool"] or rec["tool"],
        verdict=got["verdict"],
        word=WORD.get(got["verdict"], got["verdict"].replace("_", " ")),
        severity=got["severity"],
        risk=None if risk is None else max(0, min(100, risk)),
        flags=None if got["flags"] is None else max(0, got["flags"]),
        when=_iso(got["when"]) or rec["when"],
        scanner=got["scanner"],
    )
    return rec


def scan_files(pattern_list: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for pat in pattern_list:
        try:
            hits = sorted(globmod.glob(str(pat), recursive=True))
        except (OSError, ValueError):
            hits = []
        for h in hits:
            p = Path(h)
            k = str(p).lower()
            if k in seen or not _is_file(p) or p.suffix.lower() != ".json":
                continue
            seen.add(k)
            out.append(p)
            if len(out) >= MAX_SCANS:
                return out
    return out


# ----------------------------------------------------------------------------- the other inputs
def installed_items(
    dirs: list[Path], manifest: str, bundle_name: str
) -> tuple[dict[str, Path], int]:
    """``{name: folder}`` for installed items, and how many another app synced in (counted only)."""
    inst: dict[str, Path] = {}
    synced = 0
    for root in dirs:
        for d in _subdirs(root):
            if d.name.startswith((".", "_")) or d.name.lower() in {k.lower() for k in inst}:
                continue
            if _is_file(d / manifest):
                inst[d.name] = d
                continue
            man = _bundle_manifest(d, bundle_name) if bundle_name else None
            if man:
                synced += bundle(d, man, manifest)["count"] or 0
    return inst, synced


def read_decisions(p: Path | None) -> tuple[dict[str, dict], dict[str, Any]]:
    """(calls by lower-case tool name, file info). No file = no calls; a bad one = an error."""
    info: dict[str, Any] = {
        "file": p.name if p else "",
        "href": _uri(p) if p and _is_file(p) else "",
        "present": bool(p) and _is_file(p),
        "error": None,
        "bad": [],
    }
    if not info["present"] or p is None:
        return {}, info
    try:
        j = json.loads(p.read_text(encoding="utf-8-sig"))
        tools = j.get("tools") if isinstance(j, dict) else None
        if not isinstance(tools, dict):
            raise ValueError('no "tools" object')
    except (OSError, ValueError) as e:
        info["error"] = f"{type(e).__name__}: {e}"[:200]
        return {}, info
    out: dict[str, dict] = {}
    for name, e in tools.items():
        if not isinstance(e, dict) or not isinstance(name, str) or not name.strip():
            info["bad"].append(str(name)[:60])
            continue
        call = str(e.get("call") or "").strip().lower()
        origin = str(e.get("origin") or "").strip().lower()
        date = str(e.get("date") or "").strip()[:10]
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            date = ""
        out[name.strip().lower()] = {
            "call": call if call in CALLS else None,
            "date": date or None,
            "origin": origin if origin in ORIGINS else None,
            "note": font_safe(str(e.get("note") or "").replace(";", ","))[:120],
            "source": str(e.get("source") or "")[:300],
        }
        if call and call not in CALLS:
            info["bad"].append(name[:60])
    return out, info


# ----------------------------------------------------------------------------- rows
def _kind(newest: dict | None, good: list, call: str | None, installed: bool, origin: str) -> str:
    if newest and newest["status"] == "scanning":
        return "scanning"
    if newest and newest["status"] == "unreadable":
        return "unreadable"
    if call == "skip" and installed:
        return "skipped-in"
    if good and call:
        return "signed"
    if good and not installed:
        return "waiting"
    if good:
        return "norecord"
    if installed:
        return "homemade" if origin == "own" else "noscan"
    return "unscanned"


def result_line(kind: str, scan: dict | None, call: str | None) -> str:
    """One plain sentence for the row (the town's crate tooltip, the details page)."""
    s = scan or {}
    v = s.get("word") or "-"
    risk = "-" if s.get("risk") is None else s["risk"]
    return {
        "signed": f"Scanner: {v}, risk {risk}. You: {call}",
        "waiting": f"Scanner: {v}, risk {risk}. Waits for your call",
        "scanning": "Being scanned now",
        "noscan": "Installed with no scan on file",
        "unreadable": f"The newest scan can't be read ({s.get('why') or 'unreadable'})",
        "skipped-in": "You skipped it, yet it is installed",
        "homemade": "Home-made here, no scan",
        "norecord": f"Scanner: {v}, risk {risk}. No call recorded",
        "unscanned": f"You: {call or 'no call'}. No scan on file",
    }[kind]


def _label(state: str, rows: list[dict], derr: str | None) -> str:
    n = {k: sum(1 for r in rows if r["kind"] == k) for k in KIND_STATE}
    if derr:
        return "Can't read your decisions file"
    if state == "error":
        bad = n["noscan"] + n["unreadable"] + n["skipped-in"]
        return f"{bad} stopped at the gate"
    if state == "waiting":
        return f"{n['waiting']} wait for your call"
    if state == "working":
        return f"{n['scanning']} being scanned"
    if state == "ok":
        return f"All {n['signed']} signed twice" if n["signed"] else "Nothing waits for you"
    return "No scans found" if not rows else "No scans signed yet"


def collect(paths: dict, now: dt.datetime) -> dict[str, Any]:
    """``scan_glob``: the two-signature register."""
    fmt = paths.get("format", "auto")
    fmt = fmt if fmt in ("auto", "generic", "skillspector") else "auto"
    minutes = _int(paths.get("scanning_minutes"))
    minutes = SCANNING_MIN if minutes is None or minutes < 0 else minutes
    noun = paths.get("noun", "skill")
    noun = (
        noun.strip().lower()
        if isinstance(noun, str) and NOUN_RE.match(noun.strip().lower())
        else "skill"
    )
    manifest = paths.get("manifest", "SKILL.md")
    manifest = (
        manifest
        if isinstance(manifest, str) and manifest and not re.search(r"[\\/]", manifest)
        else "SKILL.md"
    )
    bundle_name = paths.get("bundle_manifest", "manifest.json")
    bundle_name = bundle_name if isinstance(bundle_name, str) else ""

    dec_file = next(iter(_paths(paths.get("decisions_file"))), None)
    decisions, dinfo = read_decisions(dec_file)
    inst_dirs = [d for d in _paths(paths.get("installed_dir")) if _is_dir(d)]
    inst, synced = installed_items(inst_dirs, manifest, bundle_name)
    own_names = {
        d.name.lower()
        for root in _paths(paths.get("own_dir"))
        for d in _subdirs(root)
        if not d.name.startswith((".", "_"))
    }

    by_tool: dict[str, list[dict]] = {}
    for src, key in (("third-party", "glob"), ("own", "own_glob")):
        for p in scan_files(_paths(paths.get(key))):
            s = read_scan(p, now, fmt, minutes)
            s["src"] = src
            by_tool.setdefault(s["tool"].lower(), []).append(s)
    for lst in by_tool.values():
        lst.sort(key=lambda s: (s.get("when") or "", s["file"]))

    inst_l = {k.lower(): (k, v) for k, v in inst.items()}
    rows: list[dict[str, Any]] = []
    for key in sorted(set(by_tool) | set(inst_l) | set(decisions)):
        scans = by_tool.get(key, [])
        newest = scans[-1] if scans else None
        good = [s for s in scans if s["ok"]]
        dec = decisions.get(key)
        call = dec["call"] if dec else None
        installed = key in inst_l
        disp = (
            inst_l[key][0]
            if installed
            else good[-1]["tool"]
            if good
            else newest["tool"]
            if newest
            else key
        )
        origin = (dec or {}).get("origin") or (
            "own" if key in own_names or any(s["src"] == "own" for s in scans) else "unknown"
        )
        kind = _kind(newest, good, call, installed, origin)
        scan = None
        if good:
            s = good[-1]
            scan = {
                k: s.get(k)
                for k in ("verdict", "word", "severity", "risk", "flags", "when", "file", "href")
            }
            scan["scanner"] = s.get("scanner")
            scan["src"] = s["src"]
        elif newest:
            scan = {
                "verdict": None,
                "file": newest["file"],
                "href": newest["href"],
                "when": newest.get("when"),
                "src": newest["src"],
                "why": newest.get("why"),
            }
        when = (scan or {}).get("when")
        if not when and installed:
            when = _iso(_mtime(inst_l[key][1]))
        href = (scan or {}).get("href") or (_uri(inst_l[key][1]) if installed else "")
        rows.append(
            {
                "tool": font_safe(disp)[:120] or key[:120],
                "kind": kind,
                "state": KIND_STATE[kind],
                "origin": origin,
                "installed": installed,
                "scan": scan,
                "scans": len(scans),
                "history": [
                    {"verdict": s["verdict"], "risk": s["risk"], "when": s["when"]} for s in good
                ][-4:],
                "decision": (
                    {k: dec[k] for k in ("call", "date", "note", "source")}
                    if dec and call
                    else None
                ),
                "result": result_line(kind, scan, call),
                "when": when,
                "href": href,
                "_sort": when or (dec or {}).get("date") or "",
            }
        )
    rows.sort(key=lambda r: (r["_sort"], r["tool"].lower()), reverse=True)
    for r in rows:
        del r["_sort"]

    live = [r["state"] for r in rows if r["state"] != "missing"]
    state = "error" if dinfo["error"] else next((s for s in ORDER if s in live), "missing")
    needs = [
        {
            "text": f"Read the scan of {r['tool']} and record your call",
            **({"href": r["href"]} if r["href"] else {}),
        }
        for r in rows
        if r["kind"] == "waiting"
    ][:20]
    if dinfo["error"]:
        needs.insert(0, {"text": "Fix the decisions file: it can't be read"})
    first_glob = next(iter(_paths(paths.get("glob")) + _paths(paths.get("own_glob"))), None)
    out: dict[str, Any] = {
        "state": state,
        "label": _label(state, rows, dinfo["error"]),
        "noun": noun,
        "rows": rows[:2000],
        "installed": len(inst),
        "synced": synced,
        "counts": {k: sum(1 for r in rows if r["kind"] == k) for k in KIND_STATE},
        "scanned": sum(1 for r in rows if r["scan"] and r["scan"].get("verdict")),
        "decisions": {**dinfo, "recorded": len(decisions)},
        "sources": {
            "installed": _uri(inst_dirs[0]) if inst_dirs else "",
            "scans": _uri(first_glob.parent) if first_glob and _is_dir(first_glob.parent) else "",
        },
    }
    if needs:
        out["needs"] = needs
    return out
