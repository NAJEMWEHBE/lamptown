"""``lamptown doctor``: check the setup and say how to fix each problem.

It checks: the Python version, the config (found and valid), every source's paths and kind, each
custom room script, the agent-status folder (writable, every status file readable), the output
folder, the desktop window (pywebview and a GUI backend) and a headless browser for the tests.

Nothing is changed. Paths are only checked for existence and glob matches; no source file is opened
and no collector runs (``lamptown build`` does that). The one exception is the status folder: its
``*.json`` files are agent-status/1 files written for Lamptown, and each is parsed to find broken
ones. Writability is checked with a temporary file that is removed straight away.

Exit code 0 when nothing is wrong (warnings are fine; ``--strict`` makes them count), 1 otherwise.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import os
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lamptown import __version__
from lamptown.config import (
    BUILTIN_TYPES,
    CONFIG_NAME,
    ENV_CONFIG,
    Config,
    ConfigError,
    find_config,
    is_path_key,
    load_config,
    resolve_path,
    user_config_dir,
)
from lamptown.onboarding.browser import find_browser
from lamptown.onboarding.init import tilde, tilde_text

OK, WARN, FAIL = "ok", "warn", "FAIL"
EXIT_OK, EXIT_PROBLEMS = 0, 1
MIN_PY = (3, 11)
GLOB_CHARS = frozenset("*?[")
# Keys a source needs to work at all; a missing folder there is a problem. Other path keys
# (``installed_dir``, ``decisions_file``...) are optional extras: missing ones are warnings.
PRIMARY_KEYS = frozenset({"path", "dir", "dirs", "glob", "globs", "file", "files"})
# Sources whose folder is created on first use: missing is expected on a fresh machine.
CREATED_ON_USE = frozenset({"status_dir"})


@dataclass(frozen=True)
class Check:
    level: str  # ok | warn | FAIL
    what: str
    detail: str = ""
    fix: str = ""


# ----------------------------------------------------------------------------- the checks
def check_python(version: tuple[int, ...] | None = None) -> Check:
    v = tuple(sys.version_info[:3]) if version is None else tuple(version)
    shown = ".".join(str(x) for x in v)
    if v[:2] >= MIN_PY:
        return Check(OK, f"Python {shown}")
    return Check(FAIL, f"Python {shown}", "Lamptown needs Python 3.11 or newer",
                 "install Python 3.11+ and reinstall: pipx install --python python3.13 lamptown")


def check_config(explicit: str | None) -> tuple[Check, Config | None]:
    try:
        path = find_config(explicit)
    except ConfigError as e:
        return (Check(FAIL, "Config", tilde_text(str(e)), "fix the path, or unset $" + ENV_CONFIG),
                None)
    if path is None:
        where = tilde(user_config_dir() / CONFIG_NAME)
        return (Check(WARN, "Config", f"none yet (looked for {where}); the town is six empty "
                                      "lots", "lamptown init --user"), Config())
    try:
        cfg = load_config(str(path))
    except ConfigError as e:
        return (Check(FAIL, "Config", tilde_text(str(e)),
                      "edit the file; docs/config.md lists every key"), None)
    n = len(cfg.buildings)
    return Check(OK, "Config", f"{tilde(path)} ({n} building{'s' if n != 1 else ''})"), cfg


def _values(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def _glob_base(pattern: str) -> Path:
    parts = Path(pattern).parts
    keep: list[str] = []
    for part in parts:
        if GLOB_CHARS & set(part):
            break
        keep.append(part)
    return Path(*keep) if keep else Path(".")


def _known_kinds() -> set[str]:
    from lamptown.collectors import BUILTIN, entry_point_collectors

    try:
        return set(BUILTIN) | set(entry_point_collectors())
    except Exception:
        return set(BUILTIN)


def check_path(label: str, key: str, value: str, *, missing_level: str) -> Check:
    if GLOB_CHARS & set(value):
        base = _glob_base(value)
        if not base.is_dir():
            return Check(missing_level, label, f"{key}: no folder {tilde(base)}",
                         f"create it, or change {key} in the config")
        n = len(glob.glob(value, recursive=True))
        if n == 0:
            return Check(WARN, label, f"{key}: nothing matches {tilde(Path(value))} yet",
                         "fine if the files come later; else check the pattern")
        return Check(OK, label, f"{key}: {n} match{'es' if n != 1 else ''} for "
                                f"{tilde(Path(value))}")
    p = Path(value)
    want_file = key in {"file", "files", "collector"} or key.endswith("_file")
    if want_file and not p.is_file():
        return Check(missing_level, label, f"{key}: no file {tilde(p)}",
                     f"create it, or change {key} in the config")
    if not want_file and not p.exists():
        return Check(missing_level, label, f"{key}: {tilde(p)} does not exist",
                     f"create it, or change {key} in the config")
    return Check(OK, label, f"{key}: {tilde(p)}")


def _same(a: str | Path, b: Path | None) -> bool:
    if b is None:
        return False
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(b.resolve()))
    except OSError:
        return False


def check_sources(cfg: Config, status_folder: Path | None = None) -> Iterator[Check]:
    """Each building's room script and each source's kind and paths. ``status_folder`` is the
    folder the status check covers; sources that read it point there instead of repeating it."""
    kinds = _known_kinds()
    base = cfg.base_dir
    for b in cfg.buildings:
        if b.room_js:
            if Path(b.room_js).is_file():
                yield Check(OK, f"{b.id}: room script", tilde(Path(b.room_js)))
            else:
                yield Check(FAIL, f"{b.id}: room script", f"no file {tilde(Path(b.room_js))}",
                            "fix room_js in the config")
        elif b.type not in BUILTIN_TYPES and not _room_entry_point(b.type):
            yield Check(WARN, f"{b.id}: room", f"no room script for type {b.type!r}; the "
                                                "plot shows a plain building",
                        "add room_js = \"./my-room.js\", or install a package that has it")
        if not b.sources:
            yield Check(WARN, f"{b.id}", "no sources: its lamp stays dark",
                        "add a [[building.source]] table")
        for i, s in enumerate(b.sources, 1):
            label = f"{b.id} / source #{i} ({s.kind})"
            if s.collector:
                yield check_path(label, "collector", s.collector, missing_level=FAIL)
            elif s.kind not in kinds:
                yield Check(FAIL, label, f"unknown kind {s.kind!r}",
                            "use a built-in kind (docs/collectors.md) or install the package "
                            "that provides it")
                continue
            checked = False
            for key, raw in s.table.items():
                if key == "collector" or not is_path_key(key):
                    continue
                for value in _values(raw):
                    checked = True
                    if s.kind == "status_dir" and _same(value, status_folder):
                        yield Check(OK, label, f"{key}: the status folder (checked below)")
                        continue
                    level = (WARN if s.kind in CREATED_ON_USE or key not in PRIMARY_KEYS
                             else FAIL)
                    yield check_path(label, key, value, missing_level=level)
            for j, proj in enumerate(_list_of_tables(s.table.get("projects")), 1):
                raw = proj.get("path")
                if isinstance(raw, str) and raw.strip():
                    checked = True
                    name = proj.get("name") or f"project #{j}"
                    yield check_path(label, f"project {name!r}",
                                     str(resolve_path(raw.strip(), base)), missing_level=WARN)
            if not checked and s.kind == "status_dir":
                yield Check(OK, label, "no dir: reads $AGENT_STATUS_DIR, else ~/.agent-status")
            elif not checked and s.kind != "static" and not s.collector:
                yield Check(OK, label, "no paths to check")


def _list_of_tables(v: Any) -> list[dict]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _room_entry_point(rtype: str) -> bool:
    try:
        from importlib import metadata

        return any(e.name == rtype for e in metadata.entry_points(group="lamptown.rooms"))
    except Exception:
        return False


def _writable(folder: Path) -> str | None:
    """None when a file can be made in ``folder``; else the reason."""
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".lamptown-doctor-", delete=True):
            pass
    except OSError as e:
        return e.strerror or tilde_text(str(e))
    return None


def _nearest_existing(p: Path) -> Path | None:
    for q in (p, *p.parents):
        if q.exists():
            return q
    return None


def check_status_dir(override: str | None, now: datetime | None = None) -> Iterator[Check]:
    from lamptown.status.collector import scan
    from lamptown.status.store import default_dir

    folder = default_dir(override)
    shown = tilde(folder)
    if not folder.exists():
        parent = _nearest_existing(folder.parent)
        if parent is not None and parent.is_dir() and os.access(parent, os.W_OK):
            yield Check(WARN, "Status folder", f"{shown} does not exist yet (the first status "
                                               "write makes it)",
                        "run a hook once, or: lamptown status set hello --state ok")
        else:
            yield Check(FAIL, "Status folder", f"{shown} does not exist and can't be made",
                        "set $AGENT_STATUS_DIR to a folder you can write")
        return
    if not folder.is_dir():
        yield Check(FAIL, "Status folder", f"{shown} is a file, not a folder",
                    "move it away, or set $AGENT_STATUS_DIR")
        return
    why = _writable(folder)
    if why:
        yield Check(FAIL, "Status folder", f"{shown} is not writable ({why})",
                    "fix its permissions, or set $AGENT_STATUS_DIR")
        return
    records, _ = scan([folder], now or datetime.now(UTC))
    bad = [r for r in records if r.invalid]
    good = len(records) - len(bad)
    yield Check(OK, "Status folder", f"{shown}: writable, {good} status "
                                     f"file{'s' if good != 1 else ''}")
    for r in bad:
        yield Check(FAIL, "Status file", f"{tilde(r.path)}: {r.message}",
                    "fix or remove it; it shows as a red lamp")


def check_out_dir(cfg: Config | None) -> Check:
    from lamptown.build import default_out_dir

    out = default_out_dir(cfg or Config())
    if out.is_dir():
        why = _writable(out)
    else:
        parent = _nearest_existing(out)
        if parent is None or not parent.is_dir():
            return Check(FAIL, "Output folder", f"{tilde(out)}: no usable folder",
                         "set [town] out in the config")
        why = None if os.access(parent, os.W_OK) else "permission denied"
    if why:
        return Check(FAIL, "Output folder", f"{tilde(out)} is not writable ({why})",
                     "set [town] out, or lamptown build --out DIR")
    return Check(OK, "Output folder", tilde(out))


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def gui_backend(platform: str | None = None, has: Callable[[str], bool] = _has) -> str | None:
    """Name of the GUI backend pywebview can use here, or None. Nothing is imported."""
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return "WebView2 (via pythonnet)" if has("clr") or has("clr_loader") else None
    if platform == "darwin":
        return "WebKit (via PyObjC)" if has("objc") and has("WebKit") else None
    if has("gi"):
        return "GTK (via PyGObject; WebKitGTK must be installed too)"
    for qt in ("PyQt6", "PyQt5", "PySide6", "PySide2"):
        if has(qt):
            return f"Qt ({qt})"
    return None


def check_window(platform: str | None = None, has: Callable[[str], bool] = _has) -> Check:
    if not has("webview"):
        return Check(WARN, "Desktop window", "pywebview is not installed: `lamptown app` "
                                             "opens the browser instead",
                     'pipx install "lamptown[app]" (or pip install "lamptown[app]")')
    try:
        from importlib import metadata

        version = metadata.version("pywebview")
    except Exception:  # not installed as a distribution (or metadata unreadable)
        version = "?"
    backend = gui_backend(platform, has)
    if backend is None:
        hint = {"win32": "pip install pythonnet",
                "darwin": "pip install pyobjc-framework-WebKit"}.get(
            platform or sys.platform,
            'pip install "pywebview[qt]", or pywebview[gtk] plus your system\'s WebKitGTK')
        return Check(WARN, "Desktop window", f"pywebview {version}, but no GUI backend found",
                     hint)
    return Check(OK, "Desktop window", f"pywebview {version} with {backend}")


def check_browser() -> Check:
    found = find_browser()
    if found.path:
        return Check(OK, "Headless browser", f"{tilde(Path(found.path))} ({found.why}); for "
                                             "the browser tests and tools/make_media.py")
    return Check(WARN, "Headless browser", found.why + "; only the browser tests and "
                                                       "tools/make_media.py need it",
                 "install Chrome or Chromium, or set $CHROME to its path")


def run_checks(config: str | None, status_dir: str | None) -> list[Check]:
    checks = [check_python()]
    cfg_check, cfg = check_config(config)
    checks.append(cfg_check)
    if cfg is not None:
        from lamptown.status.store import default_dir

        checks += list(check_sources(cfg, default_dir(status_dir)))
    checks += list(check_status_dir(status_dir))
    checks.append(check_out_dir(cfg))
    checks.append(check_window())
    checks.append(check_browser())
    return checks


# ----------------------------------------------------------------------------- report
def report(checks: list[Check], *, quiet: bool = False) -> str:
    lines = [f"Lamptown doctor {__version__}", ""]
    for c in checks:
        if quiet and c.level == OK:
            continue
        text = f"{c.what}: {c.detail}" if c.detail else c.what
        lines.append(f"  {c.level:<4}  {text}")
        if c.fix and c.level != OK:
            lines.append(f"        fix: {c.fix}")
    fails = sum(c.level == FAIL for c in checks)
    warns = sum(c.level == WARN for c in checks)
    lines.append("")
    if not fails and not warns:
        lines.append("All good.")
    else:
        lines.append(f"{fails} problem{'s' if fails != 1 else ''}, "
                     f"{warns} warning{'s' if warns != 1 else ''}.")
    return "\n".join(lines)


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = run_checks(args.config, args.status_dir)
    print(report(checks, quiet=args.quiet))
    bad = {FAIL, WARN} if args.strict else {FAIL}
    return EXIT_PROBLEMS if any(c.level in bad for c in checks) else EXIT_OK


def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "doctor",
        help="check the setup and say how to fix problems",
        description="Check Python, the config and every source path, the status folder and its "
                    "files, the output folder, the desktop window and a headless browser. "
                    "Changes nothing. Exit code 1 when something is wrong.",
    )
    p.add_argument("--config", metavar="FILE",
                   help="the lamptown.toml to check (default: $LAMPTOWN_CONFIG, then the "
                        "per-user one)")
    p.add_argument("--status-dir", metavar="DIR",
                   help="the agent-status folder to check (default $AGENT_STATUS_DIR, then "
                        "~/.agent-status)")
    p.add_argument("--strict", action="store_true", help="warnings count as problems too")
    p.add_argument("-q", "--quiet", action="store_true", help="show only warnings and problems")
    p.set_defaults(func=cmd_doctor)
    return p
