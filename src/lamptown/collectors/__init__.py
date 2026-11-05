"""Collectors: one data source in, one small JSON-safe dict out (the contract is
docs/collectors.md).

``run_sources`` runs sources at once (each in its own thread, with a time limit) and turns any
crash, time-out, non-dict or non-JSON value into an error result for that source only; ``merge``
joins one building's results (rows and needs concatenated, worst state wins).

Where a ``kind`` comes from, in order: the built-in table :data:`BUILTIN`, then the entry-point
group ``lamptown.collectors``, and a source with ``collector = "./mine.py"`` loads that file
instead.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import importlib.util
import json
import math
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from importlib import metadata
from pathlib import Path, PurePath
from typing import Any

from lamptown.status.store import scrub_text

STATES = ("ok", "waiting", "working", "error", "missing")
WORST_FIRST = ("error", "waiting", "working", "missing", "ok")
TIMEOUT = 10.0  # seconds a single source may take
ENTRY_GROUP = "lamptown.collectors"
LINK_SCHEMES = ("https:", "http:", "file:")

# Built-in kinds: "module:function", imported only when a source uses them. More arrive with the
# rooms.
BUILTIN: dict[str, str] = {
    "static": "lamptown.collectors.static:collect",
    "status_dir": "lamptown.status.collector:collect",
    "log_glob": "lamptown.collectors.jobs:collect",
    "markdown_vault": "lamptown.collectors.vault:collect",
    "projects": "lamptown.collectors.harbour:collect",
    "skill_dirs": "lamptown.collectors.shelf:collect",
    "staging_dir": "lamptown.collectors.shelf:collect_staging",
    "scan_glob": "lamptown.collectors.ledger:collect",
    "contest_json": "lamptown.collectors.contest:collect",
}

Collector = Callable[[dict, dt.datetime], dict]


class CollectorError(Exception):
    """A source could not run; the message is short and safe to show in the town."""


# ----------------------------------------------------------------------------- states
def worst(states: Iterable[str]) -> str:
    """The worst of some states (error > waiting > working > missing > ok); "ok" when there are
    none.
    """
    s = set(states)
    return next((w for w in WORST_FIRST if w in s), "ok")


def error_result(reason: str) -> dict[str, Any]:
    return {"state": "error", "error": clean_text(reason)[:300]}  # review-fix C2


# ----------------------------------------------------------------------------- finding a collector
def _load_target(target: str) -> Collector:
    mod_name, _, attr = target.partition(":")
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, attr or "collect")
    if not callable(fn):
        raise CollectorError(f"{target} is not callable")
    return fn


def entry_point_collectors() -> dict[str, metadata.EntryPoint]:
    """Installed third-party collectors by kind (the entry-point group ``lamptown.collectors``)."""
    try:
        eps = metadata.entry_points(group=ENTRY_GROUP)
    except Exception:  # a broken installed distribution must not stop the build
        return {}
    return {ep.name: ep for ep in eps}


def load_local(path: str | Path) -> Collector:
    """Import ``collect`` from a local ``.py`` file (fresh on every call, so edits show on the next
    build)."""
    p = Path(path)
    if not p.is_file():
        raise CollectorError(f"collector file not found: {p.name}")
    name = "lamptown_local_" + hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, p)
    if spec is None or spec.loader is None:
        raise CollectorError(f"can't load {p.name} as Python")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    fn = getattr(mod, "collect", None)
    if not callable(fn):
        raise CollectorError(f"{p.name} has no collect(paths, now) function")
    return fn


def find_collector(kind: str, local: str | None = None) -> Collector:
    """The collect function for a source. Raises CollectorError (or the import's own error)."""
    if local:
        return load_local(local)
    if kind in BUILTIN:
        try:
            return _load_target(BUILTIN[kind])
        except ImportError as e:
            raise CollectorError(
                f"collector kind {kind!r} is not available in this install ({e})"
            ) from e
    ep = entry_point_collectors().get(kind)
    if ep is None:
        raise CollectorError(
            f"unknown collector kind {kind!r} (not built in, and no installed package "
            f"registers it under {ENTRY_GROUP!r})"
        )
    fn = ep.load()
    if not callable(fn):
        raise CollectorError(f"entry point {kind!r} is not callable")
    return fn


# ----------------------------------------------------------------------------- JSON safety
# --- review-fix C2: lone surrogates ---
_SURROGATE = re.compile("[\ud800-\udfff]")


def clean_text(text: str) -> str:
    """``text`` as valid Unicode, so it can be written as UTF-8: a lone surrogate half (a file
    name from a non-UTF-8 disk, a broken ``\\ud83e`` JSON escape) becomes U+FFFD; a pair kept
    as two halves becomes its one character. Valid text comes back unchanged."""
    if not _SURROGATE.search(text):
        return text
    return scrub_text(text)  # one rule for every writer: lamptown.status.store


def clean(value: Any) -> Any:
    """:func:`clean_text` on every string (keys too) in JSON-like data; a copy when needed."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return {clean_text(k) if isinstance(k, str) else k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value
# --- end review-fix C2 ---


def json_safe(value: Any, where: str = "result") -> Any:
    """A JSON-only copy. datetimes/dates become ISO strings (naive ones are read as local time),
    paths
    become strings, tuples become lists, and text is valid Unicode (:func:`clean_text`). Anything
    else raises TypeError naming where it was."""
    if isinstance(value, str):
        return clean_text(value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"{where} is {value!r}, which JSON can't hold")
        return value
    if isinstance(value, dt.datetime):
        return (value if value.tzinfo else value.astimezone()).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, PurePath):
        return clean_text(str(value))
    if isinstance(value, Mapping):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"{where} has a key {k!r} that is not a string")
            out[clean_text(k)] = json_safe(v, f"{where}.{k}")
        return out
    if isinstance(value, (list, tuple)):
        return [json_safe(v, f"{where}[{i}]") for i, v in enumerate(value)]
    raise TypeError(f"{where} is a {type(value).__name__}, which JSON can't hold")


def safe_href(href: Any) -> str | None:
    """Only https:, http: and file: links are kept."""
    if not isinstance(href, str):
        return None
    h = href.strip()
    return h if h.lower().startswith(LINK_SCHEMES) and len(h) <= 2048 else None


def scrub_links(value: Any) -> Any:
    """Drop every ``href`` whose scheme is not allowed, anywhere in the data."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "href":
                h = safe_href(v)
                if h is not None:
                    out[k] = h
            else:
                out[k] = scrub_links(v)
        return out
    if isinstance(value, list):
        return [scrub_links(v) for v in value]
    return value


def normalise(result: Any) -> dict[str, Any]:
    """Check one collector's return value; raise TypeError/ValueError with a short reason when it is
    bad."""
    if not isinstance(result, dict):
        raise TypeError(f"collect() returned {type(result).__name__}, not a dict")
    data = json_safe(result)
    json.dumps(data, allow_nan=False)  # belt and braces
    st = data.get("state")
    if st not in STATES:
        raise ValueError(f"state is {st!r}; it must be one of {', '.join(STATES)}")
    needs = data.get("needs")
    if needs is not None:
        if not isinstance(needs, list):
            raise ValueError("needs must be a list of {text, href}")
        fixed = []
        for i, raw in enumerate(needs):
            n = {"text": raw} if isinstance(raw, str) else raw
            if (
                not isinstance(n, dict)
                or not isinstance(n.get("text"), str)
                or not n["text"].strip()
            ):
                raise ValueError(f"needs[{i}] must be {{text, href}} with a non-empty text")
            fixed.append(n)
        data["needs"] = fixed
    return scrub_links(data)


# ----------------------------------------------------------------------------- running
def source_paths(
    table: Mapping[str, Any], building_id: str, room_type: str, config_dir: Path
) -> dict:
    """The ``paths`` dict a collector receives: the resolved table plus the reserved keys."""
    paths = {k: v for k, v in table.items() if not k.startswith("_")}
    paths.update({"_building": building_id, "_room": room_type, "_config_dir": str(config_dir)})
    return paths


def _short(e: BaseException) -> str:
    msg = str(e).strip().splitlines()[0] if str(e).strip() else ""
    return (
        f"{type(e).__name__}: {msg}"
        if msg and not isinstance(e, CollectorError)
        else (msg or type(e).__name__)
    )


class _Run(threading.Thread):
    def __init__(self, fn: Callable[[], dict]):
        super().__init__(daemon=True, name="lamptown-collector")
        self.fn = fn
        self.result: dict[str, Any] | None = None

    def run(self) -> None:
        try:
            self.result = self.fn()
        except BaseException as e:  # noqa: BLE001 - a collector may raise anything; isolate it
            self.result = error_result(_short(e))


def run_sources(
    jobs: Sequence[tuple[str, str | None, dict]], now: dt.datetime, timeout: float = TIMEOUT
) -> list[dict[str, Any]]:
    """Run ``(kind, local_file, paths)`` jobs at once; one checked result per job, in order.

    A crash, a run longer than ``timeout`` seconds, a non-dict or a non-JSON value becomes
    ``{"state": "error", "error": ...}`` for that job only. A timed-out thread is left to finish on
    its own (it is a daemon thread and its result is ignored).
    """

    def one(kind: str, local: str | None, paths: dict) -> Callable[[], dict]:
        def go() -> dict:
            fn = find_collector(kind, local)
            return normalise(fn(dict(paths), now))

        return go

    runs = [_Run(one(*j)) for j in jobs]
    for r in runs:
        r.start()
    deadline = time.monotonic() + timeout
    out = []
    for r, (kind, local, _paths) in zip(runs, jobs, strict=True):
        r.join(max(0.0, deadline - time.monotonic()))
        if r.is_alive() or r.result is None:
            label = Path(local).name if local else kind
            out.append(error_result(f"{label} took longer than {timeout:g} s"))
        else:
            out.append(r.result)
    return out


def merge(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Several sources in one building -> one room dict (docs/collectors.md, "Several sources")."""
    if not results:
        return {"state": "missing", "label": "no sources set up"}
    if len(results) == 1:
        return dict(results[0])
    states = [r.get("state", "error") for r in results]
    st = worst(states)
    out: dict[str, Any] = {"state": st}
    first_worst = results[states.index(st)]
    for k in ("label", "error"):
        if k in first_worst:
            out[k] = first_worst[k]
    for k in ("rows", "needs"):
        lists = [r[k] for r in results if isinstance(r.get(k), list)]
        if lists:
            out[k] = [x for lst in lists for x in lst]
    for r in results:
        for k, v in r.items():
            if k not in out and k not in ("state", "label", "error", "rows", "needs"):
                out[k] = v
    return out
