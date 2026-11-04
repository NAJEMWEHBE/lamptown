"""Load and check the ``agent-status/1`` JSON Schema with the standard library only.

The schema file (``agent-status-1.schema.json``, shipped as package data) is the single
source of truth. :func:`validate` walks it with a small interpreter for the JSON Schema
keywords that file uses, plus the two rules a schema can't express (an offset on every
date-time that also parses, and ``progress.done <= progress.total``).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import cache
from importlib import resources
from typing import Any

SCHEMA_FILE = "agent-status-1.schema.json"


@cache
def load_schema() -> dict:
    """Return the parsed schema (cached; treat it as read-only)."""
    text = resources.files("lamptown.status").joinpath(SCHEMA_FILE).read_text(encoding="utf-8")
    return json.loads(text)


def schema_text() -> str:
    """Return the schema file's exact text."""
    return resources.files("lamptown.status").joinpath(SCHEMA_FILE).read_text(encoding="utf-8")


def parse_time(value: Any) -> datetime | None:
    """Parse an RFC 3339 date-time that carries an offset; ``None`` if it isn't one."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().upper().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _resolve(node: dict, root: dict) -> dict:
    ref = node.get("$ref")
    if not ref:
        return node
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref {ref!r}")
    target: Any = root
    for part in ref[2:].split("/"):
        target = target[part]
    return target


def _check(value: Any, node: dict, root: dict, where: str, out: list[str]) -> None:
    node = _resolve(node, root)
    kinds = node.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not any(_TYPES[k](value) for k in kinds):
            out.append(f"{where}: must be {' or '.join(kinds)}")
            return
    if "enum" in node and value not in node["enum"]:
        out.append(f"{where}: must be one of {', '.join(map(str, node['enum']))}")
        return
    if isinstance(value, str):
        if len(value) < node.get("minLength", 0):
            out.append(f"{where}: must not be empty")
        if "maxLength" in node and len(value) > node["maxLength"]:
            out.append(f"{where}: longer than {node['maxLength']} characters")
        if "pattern" in node and not re.search(node["pattern"], value):
            out.append(f"{where}: does not match {node['pattern']}")
        elif node.get("format") == "date-time" and parse_time(value) is None:
            out.append(f"{where}: not a date-time with an offset")
    if (isinstance(value, int | float) and not isinstance(value, bool)
            and "minimum" in node and value < node["minimum"]):
        out.append(f"{where}: must be at least {node['minimum']}")
    if isinstance(value, list):
        if "maxItems" in node and len(value) > node["maxItems"]:
            out.append(f"{where}: more than {node['maxItems']} items")
        if "items" in node:
            for i, item in enumerate(value):
                _check(item, node["items"], root, f"{where}[{i}]", out)
    if isinstance(value, dict):
        for key in node.get("required", []):
            if key not in value:
                out.append(f"{where}: missing required field {key!r}")
        for key, sub in node.get("properties", {}).items():
            if key in value:
                _check(value[key], sub, root, f"{where}.{key}" if where != "$" else key, out)


def validate(doc: Any) -> list[str]:
    """Return a list of problems; an empty list means ``doc`` is a valid status file."""
    root = load_schema()
    out: list[str] = []
    _check(doc, root, root, "$", out)
    progress = doc.get("progress") if isinstance(doc, dict) else None
    if isinstance(progress, dict):
        done, total = progress.get("done"), progress.get("total")
        if isinstance(done, int) and isinstance(total, int) and done > total:
            out.append("progress: done is larger than total")
    return out
