"""Load and check ``lamptown.toml``.

Lookup order: ``--config`` (an explicit path), then ``$LAMPTOWN_CONFIG``, then ``lamptown.toml`` in
the per-user config dir (:func:`user_config_dir`). No file at all is fine: the town is then six
empty lots.

Every problem is a :class:`ConfigError` whose message names the file, the key and what is wrong, so
the user can fix it without reading code. Paths in sources are resolved here (``~`` expanded,
relative paths made absolute against the config file's folder) so collectors only ever see absolute
paths.
"""

from __future__ import annotations

import math
import os
import re
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_NAME = "lamptown.toml"
ENV_CONFIG = "LAMPTOWN_CONFIG"
CONFIG_VERSION = 1

# The fixed v1 map: six hand-drawn plots, named after what stands on them. Each built-in room type
# has a
# home plot; any building may name another plot, and a custom room type must name one.
PLOTS = ("tower", "library", "gate", "lab", "workshop", "boathouse")
HOME_PLOT = {
    "jobs": "tower",
    "vault": "library",
    "ledger": "gate",
    "contest": "lab",
    "shelf": "workshop",
    "harbour": "boathouse",
}
BUILTIN_TYPES = tuple(HOME_PLOT)
DEFAULT_LABEL = {
    "jobs": "Scheduled jobs",
    "vault": "Notes vault",
    "ledger": "Vetting gate",
    "contest": "Idea contest",
    "shelf": "Skill shelf",
    "harbour": "Projects",
}

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
LABEL_MAX = 32
TITLE_MAX = 60

TOP_KEYS = {"version", "town", "building"}
TOWN_KEYS = {"title", "latitude", "longitude", "out"}
BUILDING_KEYS = {"id", "type", "label", "plot", "enabled", "room_js", "source"}

# Keys whose string values are paths (rule written in docs/collectors.md).
PATH_KEYS = {"path", "dir", "file", "glob", "paths", "dirs", "files", "globs", "collector"}
PATH_SUFFIXES = ("_path", "_dir", "_file", "_glob")


class ConfigError(ValueError):
    """The config can't be used; the message says where and why."""


@dataclass(frozen=True)
class Source:
    """One ``[[building.source]]`` table, already resolved (see :func:`resolve_source`)."""

    kind: str
    table: dict[str, Any]
    collector: str | None = None  # absolute path of a local collector file, when given

    @property
    def name(self) -> str:
        return str(self.table.get("name") or self.kind)


@dataclass(frozen=True)
class Building:
    id: str
    type: str
    label: str
    plot: str
    sources: tuple[Source, ...] = ()
    room_js: str | None = None  # absolute path of a custom room script, when given


@dataclass(frozen=True)
class Town:
    title: str = "Lamptown"
    latitude: float | None = None
    longitude: float | None = None
    out: str | None = None


@dataclass(frozen=True)
class Config:
    town: Town = field(default_factory=Town)
    buildings: tuple[Building, ...] = ()
    path: Path | None = None  # the file it came from (None: no file, built-in defaults)

    @property
    def base_dir(self) -> Path:
        """The folder relative paths are resolved against."""
        return self.path.parent if self.path else user_config_dir()

    def by_plot(self) -> dict[str, Building]:
        return {b.plot: b for b in self.buildings}


# ----------------------------------------------------------------------------- per-user folders
def _home() -> Path:
    return Path(os.path.expanduser("~"))


def user_config_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where ``lamptown.toml`` lives when no path is given: per OS, never inside a project."""
    env = os.environ if env is None else env
    if sys.platform == "win32":
        base = Path(env["APPDATA"]) if env.get("APPDATA") else _home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = _home() / "Library" / "Application Support"
    else:
        base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else _home() / ".config"
    return base / "lamptown"


def user_data_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where generated pages (and later app state) go by default."""
    env = os.environ if env is None else env
    if sys.platform == "win32":
        local = env.get("LOCALAPPDATA")
        base = Path(local) if local else _home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = _home() / "Library" / "Application Support"
    else:
        base = (
            Path(env["XDG_DATA_HOME"]) if env.get("XDG_DATA_HOME") else _home() / ".local" / "share"
        )
    return base / "lamptown"


# ----------------------------------------------------------------------------- lookup + load
def find_config(
    explicit: str | os.PathLike[str] | None = None, env: Mapping[str, str] | None = None
) -> Path | None:
    """The config file to use, or None when there is none (an empty town).

    An explicit path (flag or ``$LAMPTOWN_CONFIG``) must exist; the per-user default may be absent.
    """
    env = os.environ if env is None else env
    for given, where in ((explicit, "--config"), (env.get(ENV_CONFIG) or None, f"${ENV_CONFIG}")):
        if given:
            p = Path(os.path.expanduser(str(given)))
            if not p.is_file():
                raise ConfigError(f"{where} points at {p}, which is not a file")
            return p.resolve()
    p = user_config_dir(env) / CONFIG_NAME
    return p.resolve() if p.is_file() else None


def load_config(
    explicit: str | os.PathLike[str] | None = None, env: Mapping[str, str] | None = None
) -> Config:
    """Find, read and check the config (see the module docstring for the lookup order)."""
    path = find_config(explicit, env)
    if path is None:
        return Config()
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ConfigError(f"{path}: can't read it ({e.strerror or e})") from e
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path}: not UTF-8 text") from e
    return parse_config(text, path)


def parse_config(text: str, path: Path | None = None) -> Config:
    """Check a config's text. ``path`` is used for messages and for resolving relative paths."""
    where = str(path) if path else CONFIG_NAME
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{where}: not valid TOML: {e}") from e
    return _check(data, path, where)


# ----------------------------------------------------------------------------- checks
def _fail(where: str, key: str, msg: str) -> ConfigError:
    return ConfigError(f"{where}: {key}: {msg}")


def _unknown(where: str, key: str, table: Mapping[str, Any], allowed: set[str]) -> None:
    extra = sorted(set(table) - allowed)
    if extra:
        raise _fail(
            where,
            key or "(top level)",
            f"unknown key {extra[0]!r} (allowed: {', '.join(sorted(allowed))})",
        )


def _str(where: str, key: str, v: Any, *, max_len: int | None = None, empty: bool = False) -> str:
    if not isinstance(v, str):
        raise _fail(where, key, f"must be a string, not {type(v).__name__}")
    if not empty and not v.strip():
        raise _fail(where, key, "must not be empty")
    if max_len is not None and len(v) > max_len:
        raise _fail(where, key, f"is {len(v)} characters long; at most {max_len}")
    return v


def _coord(where: str, key: str, v: Any, limit: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise _fail(where, key, "must be a number")
    if not -limit <= v <= limit:
        raise _fail(where, key, f"must be between -{limit:g} and {limit:g}")
    return float(v)


def _check(data: dict[str, Any], path: Path | None, where: str) -> Config:
    _unknown(where, "", data, TOP_KEYS)
    version = data.get("version", CONFIG_VERSION)
    if isinstance(version, bool) or version != CONFIG_VERSION:
        raise _fail(
            where,
            "version",
            f"must be {CONFIG_VERSION} (this Lamptown reads config version "
            f"{CONFIG_VERSION}), not {version!r}",
        )
    base = path.parent if path else user_config_dir()

    town_raw = data.get("town", {})
    if not isinstance(town_raw, dict):
        raise _fail(where, "town", "must be a table: [town]")
    _unknown(where, "town", town_raw, TOWN_KEYS)
    town = Town(
        title=_str(where, "town.title", town_raw["title"], max_len=TITLE_MAX)
        if "title" in town_raw
        else Town.title,
        latitude=_coord(where, "town.latitude", town_raw["latitude"], 90)
        if "latitude" in town_raw
        else None,
        longitude=_coord(where, "town.longitude", town_raw["longitude"], 180)
        if "longitude" in town_raw
        else None,
        out=str(resolve_path(_str(where, "town.out", town_raw["out"]), base))
        if "out" in town_raw
        else None,
    )

    blist = data.get("building", [])
    if not isinstance(blist, list) or not all(isinstance(b, dict) for b in blist):
        raise _fail(where, "building", "must be an array of tables: [[building]]")
    buildings: list[Building] = []
    ids: dict[str, int] = {}
    plots: dict[str, str] = {}
    for i, b in enumerate(blist, 1):
        key = f"building #{i}"
        _unknown(where, key, b, BUILDING_KEYS)
        if "id" not in b:
            raise _fail(where, key, 'needs an id, for example id = "jobs"')
        bid = _str(where, f"{key}.id", b["id"])
        if not ID_RE.match(bid):
            raise _fail(
                where, f"{key}.id", f"{bid!r} must be 1-40 lower-case letters, digits, '-' or '_'"
            )
        key = f"building {bid!r}"
        if bid in ids:
            raise _fail(where, key, f"the id is used twice (building #{ids[bid]} and #{i})")
        ids[bid] = i
        enabled = b.get("enabled", True)
        if not isinstance(enabled, bool):
            raise _fail(where, f"{key}.enabled", "must be true or false")
        rtype = _str(
            where, f"{key}.type", b.get("type", bid if bid in HOME_PLOT else ""), empty=True
        )
        if not rtype:
            raise _fail(
                where,
                f"{key}.type",
                "is missing (a room type: " + ", ".join(BUILTIN_TYPES) + ", or your own)",
            )
        if not TYPE_RE.match(rtype):
            raise _fail(
                where, f"{key}.type", f"{rtype!r} must be lower-case letters, digits or '_'"
            )
        if "plot" in b:
            plot = _str(where, f"{key}.plot", b["plot"])
            if plot not in PLOTS:
                raise _fail(
                    where, f"{key}.plot", f"{plot!r} is not a plot (plots: {', '.join(PLOTS)})"
                )
        elif rtype in HOME_PLOT:
            plot = HOME_PLOT[rtype]
        else:
            raise _fail(
                where,
                f"{key}.plot",
                f"room type {rtype!r} is not built in, so the building "
                f"needs plot = one of {', '.join(PLOTS)}",
            )
        label = (
            _str(where, f"{key}.label", b["label"], max_len=LABEL_MAX)
            if "label" in b
            else DEFAULT_LABEL.get(rtype, bid.replace("-", " ").replace("_", " ").capitalize())
        )
        room_js = None
        if "room_js" in b:
            room_js = str(resolve_path(_str(where, f"{key}.room_js", b["room_js"]), base))
        srcs_raw = b.get("source", [])
        if not isinstance(srcs_raw, list) or not all(isinstance(s, dict) for s in srcs_raw):
            raise _fail(where, f"{key}.source", "must be an array of tables: [[building.source]]")
        sources = tuple(
            _source(where, f"{key} source #{j}", s, base) for j, s in enumerate(srcs_raw, 1)
        )
        if not enabled:
            continue  # a disabled building leaves its plot as an empty lot
        if plot in plots:
            raise _fail(
                where,
                f"{key}.plot",
                f"plot {plot!r} already holds building {plots[plot]!r}; "
                "give one of them another plot",
            )
        plots[plot] = bid
        buildings.append(
            Building(id=bid, type=rtype, label=label, plot=plot, sources=sources, room_js=room_js)
        )
    return Config(town=town, buildings=tuple(buildings), path=path)


def _source(where: str, key: str, s: dict[str, Any], base: Path) -> Source:
    bad = sorted(k for k in s if k.startswith("_"))
    if bad:
        raise _fail(where, key, f"key {bad[0]!r}: keys starting with '_' are reserved for Lamptown")
    if "kind" not in s and "collector" not in s:
        raise _fail(where, key, 'needs kind = "..." (or collector = "./mine.py")')
    kind = _str(where, f"{key}.kind", s["kind"]) if "kind" in s else "local"
    table = resolve_source(s, base)
    collector = table.get("collector")
    if collector is not None and not isinstance(collector, str):
        raise _fail(where, f"{key}.collector", 'must be a path string, for example "./mine.py"')
    return Source(kind=kind, table=table, collector=collector)


# ----------------------------------------------------------------------------- paths
def is_path_key(key: str) -> bool:
    return key in PATH_KEYS or key.endswith(PATH_SUFFIXES)


def resolve_path(value: str, base: Path) -> Path:
    """``~`` expanded; a relative path made absolute against ``base``. Globs stay globs."""
    p = Path(os.path.expanduser(value))
    if not p.is_absolute():
        p = base / p
    return Path(os.path.normpath(p))


def resolve_source(table: Mapping[str, Any], base: Path) -> dict[str, Any]:
    """A copy of a source table with every path-like value resolved (docs/collectors.md)."""
    out: dict[str, Any] = {}
    for k, v in table.items():
        if is_path_key(k) and isinstance(v, str):
            out[k] = str(resolve_path(v, base))
        elif is_path_key(k) and isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[k] = [str(resolve_path(x, base)) for x in v]
        else:
            out[k] = v
    return out
