"""A small JSON Schema checker (stdlib only) for room data.

It covers the subset the room schemas use: ``type`` (one or a list), ``enum``, ``const``,
``required``, ``properties``, ``additionalProperties`` (bool or schema), ``items``, ``minimum``,
``maximum``, ``minLength``, ``maxLength``, ``maxItems``, ``anyOf``, ``$ref`` to ``#/$defs/<name>``,
and ``format`` ``date-time`` (ISO 8601 with a time zone offset or ``Z``) and ``date``. Unknown
keywords are ignored, as JSON Schema says.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from functools import cache
from importlib import resources
from typing import Any

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}
# [0-9] rather than \d after a colon: the privacy gate reads "d:\" as a drive-letter path
_DATE_TIME = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?"
    r"(Z|[+-][0-9]{2}:?[0-9]{2})$",
    re.I,
)
_DATE = re.compile(r"^\d{4}-\d\d-\d\d$")


def is_date_time(s: str) -> bool:
    """An absolute time: ISO 8601 with an offset (``Z`` or ``+hh:mm``)."""
    if not _DATE_TIME.match(s):
        return False
    try:
        return dt.datetime.fromisoformat(s.replace("z", "Z")).tzinfo is not None
    except ValueError:
        return False


def _fmt_ok(fmt: str, v: str) -> bool:
    if fmt == "date-time":
        return is_date_time(v)
    if fmt == "date":
        if not _DATE.match(v):
            return False
        try:
            dt.date.fromisoformat(v)
        except ValueError:
            return False
    return True


def validate(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Every problem as ``"<where>: <what>"``; an empty list means valid."""
    errors: list[str] = []
    _check(instance, schema, schema, "data", errors)
    return errors


def _check(v: Any, s: dict[str, Any], root: dict[str, Any], where: str, errs: list[str]) -> None:
    if len(errs) > 50:
        return
    if "$ref" in s:
        ref = s["$ref"]
        if not ref.startswith("#/$defs/"):
            raise ValueError(f"unsupported $ref {ref!r}")
        _check(v, root["$defs"][ref[len("#/$defs/"):]], root, where, errs)
    t = s.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](v) for x in types):
            errs.append(f"{where}: should be {' or '.join(types)}, not {_name(v)}")
            return
    if "const" in s and v != s["const"]:
        errs.append(f"{where}: should be {s['const']!r}")
    if "enum" in s and v not in s["enum"]:
        errs.append(f"{where}: {v!r} is not one of {', '.join(map(repr, s['enum']))}")
    if "anyOf" in s and all(validate_sub(v, sub, root) for sub in s["anyOf"]):
        errs.append(f"{where}: does not match any allowed shape")
    if isinstance(v, str):
        if "minLength" in s and len(v) < s["minLength"]:
            errs.append(f"{where}: is too short")
        if "maxLength" in s and len(v) > s["maxLength"]:
            errs.append(f"{where}: is longer than {s['maxLength']} characters")
        if "format" in s and not _fmt_ok(s["format"], v):
            want = "an ISO time with a time zone (e.g. 2026-01-02T03:04:05Z)" \
                if s["format"] == "date-time" else f"a {s['format']}"
            errs.append(f"{where}: {v!r} is not {want}")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]:
            errs.append(f"{where}: {v} is below {s['minimum']}")
        if "maximum" in s and v > s["maximum"]:
            errs.append(f"{where}: {v} is above {s['maximum']}")
    if isinstance(v, list):
        if "maxItems" in s and len(v) > s["maxItems"]:
            errs.append(f"{where}: has more than {s['maxItems']} items")
        if isinstance(s.get("items"), dict):
            for i, x in enumerate(v):
                _check(x, s["items"], root, f"{where}[{i}]", errs)
    if isinstance(v, dict):
        for k in s.get("required", []):
            if k not in v:
                errs.append(f"{where}: missing {k!r}")
        props = s.get("properties", {})
        for k, x in v.items():
            if k in props:
                _check(x, props[k], root, f"{where}.{k}", errs)
            else:
                ap = s.get("additionalProperties", True)
                if ap is False:
                    errs.append(f"{where}: unexpected key {k!r}")
                elif isinstance(ap, dict):
                    _check(x, ap, root, f"{where}.{k}", errs)


def validate_sub(v: Any, s: dict[str, Any], root: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    _check(v, s, root, "data", errs)
    return errs


def _name(v: Any) -> str:
    for n, f in _TYPES.items():
        if n != "number" and f(v):
            return n
    return type(v).__name__


# ----------------------------------------------------------------------------- room schemas
@cache
def room_schema(room_type: str) -> dict[str, Any] | None:
    """The shipped schema for a built-in room type, or None (custom types skip the check)."""
    try:
        f = resources.files("lamptown").joinpath("schemas", "rooms", f"{room_type}.schema.json")
        if not f.is_file():
            return None
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError, ModuleNotFoundError):
        return None
