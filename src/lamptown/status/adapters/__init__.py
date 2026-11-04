"""Provider adapters: turn one hook payload into one status change.

Each adapter module has ``AGENT`` (the ``agent`` field it writes), ``translate(payload,
on_stop)`` returning an :class:`Update` or ``None`` (ignore this event), and
``respond(payload)`` returning the text a provider expects on stdout (``""`` for none).
Adapters are pure functions of the payload, so they are tested against recorded payloads.

What a hook writes: the state, a fixed message ("Working", "Turn finished", "Using Bash": the
tool's name only), the name of the session's folder as the title, and that folder's path as
``x-folder`` (only so a ``status_dir`` source's ``private_dirs`` can hide it; the town never
shows it). Never prompts, replies, tool input, file contents or account fields (such as e-mail).
Reply excerpts are opt-in (``--excerpts`` or ``LAMPTOWN_STATUS_EXCERPTS=1``): then the first
line of the agent's reply, cut to 80 characters, with anything that looks like a secret masked.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from lamptown.status.store import iso, slug, status_path, update_status, utcnow

ON_STOP = ("ok", "waiting")
ENV_EXCERPTS = "LAMPTOWN_STATUS_EXCERPTS"
EXCERPT_LIMIT = 80
FOLDER_LIMIT = 1024
# Masked even in an opted-in excerpt: NAME=value pairs whose name says key/token/secret/password,
# bearer tokens, and long runs of key-like characters.
_SECRET_PAIR = re.compile(
    r"(?i)\b([\w.-]*(?:key|token|secret|passw(?:or)?d|pwd|credential|auth)[\w.-]*)(\s*[:=]\s*)"
    r"((?:bearer|basic)\s+\S+|\"[^\"]*\"|'[^']*'|\S+)")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_+/=.-]{24,}")


@dataclass
class Update:
    """One change to one status file. ``None`` fields stay as they are."""

    id: str
    agent: str
    title: str | None = None  # None keeps the title already in the file
    display: str = ""  # title for a brand-new file when the payload names no folder
    state: str | None = None
    message: str | None = None
    needs_you: list[dict] | None = None  # [] clears, None keeps
    session_start: bool = False
    folder_path: str = ""  # the session's folder, kept as x-folder for private_dirs
    extra: dict[str, Any] = field(default_factory=dict)


def one_line(text: Any, limit: int = 200) -> str:
    """Squash whitespace and cut to ``limit`` characters (with an ellipsis)."""
    if not isinstance(text, str):
        return ""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def excerpts_wanted(flag: bool = False) -> bool:
    """Reply excerpts are off unless the hook says ``--excerpts`` or the env var is on."""
    if flag:
        return True
    return os.environ.get(ENV_EXCERPTS, "").strip().lower() in ("1", "true", "yes", "on")


def mask_secrets(text: str) -> str:
    text = _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}…", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} …", text)
    return _LONG_TOKEN.sub("…", text)


def excerpt(text: Any, limit: int = EXCERPT_LIMIT) -> str:
    """The first non-empty line of ``text``, secrets masked, cut to ``limit`` characters."""
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        flat = mask_secrets(" ".join(line.split()))
        if flat:
            return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"
    return ""


def folder_path(path: Any) -> str:
    """The session folder as given (for ``x-folder``), or ``""``."""
    if not isinstance(path, str) or not path.strip() or len(path) > FOLDER_LIMIT:
        return ""
    return path.strip()


def folder_name(path: Any) -> str:
    """Last part of a folder path from any OS (``""`` if none)."""
    if not isinstance(path, str) or not path.strip():
        return ""
    text = path.strip().rstrip("/\\")
    pure = PureWindowsPath(text) if ("\\" in text or text[1:2] == ":") else PurePosixPath(text)
    return pure.name


def make_id(agent: str, session: Any, folder: str = "") -> str:
    """``<agent>-<session>`` (first 12 id-safe characters), else ``<agent>-<folder>``."""
    for part in (session, folder):
        frag = slug(part, 12) if isinstance(part, str) else ""
        if frag:
            return f"{agent}-{frag}"
    return agent


def stop_update(base: Update, on_stop: str, last_text: Any, excerpts: bool = False) -> Update:
    """The end of a turn: ``ok`` by default, ``waiting`` + a need with ``--on-stop waiting``.
    The reply's first line is added only when excerpts are on."""
    line = excerpt(last_text) if excerpts else ""
    base.state = on_stop
    base.message = f"Turn finished: {line}" if line else "Turn finished"
    base.needs_you = [{"text": "Your turn"}] if on_stop == "waiting" else []
    return base


def apply(
    update: Update,
    directory: str | os.PathLike | None = None,
    *,
    id_override: str | None = None,
    title_override: str | None = None,
    stale_after: int | None = None,
) -> dict:
    """Write ``update`` into its status file (merging with what is there)."""
    changes: dict[str, Any] = {"agent": update.agent, "kind": "task"}
    status_id = id_override or update.id
    title = title_override or update.title
    if not title and not status_path(status_id, directory).exists():
        title = update.display
    if title:
        changes["title"] = title[:200]
    if update.state:
        changes["state"] = update.state
    if update.message is not None:
        changes["message"] = update.message[:500]
    if update.needs_you is not None:
        changes["needs_you"] = update.needs_you[:20]
    if update.session_start:
        changes["started"] = iso(utcnow())
    if update.folder_path:
        changes["x-folder"] = update.folder_path
    if stale_after is not None:
        changes["stale_after"] = stale_after
    changes.update(update.extra)
    return update_status(status_id, directory, **changes)


def get(provider: str):
    """Return the adapter module for ``provider`` (raises ``KeyError``)."""
    from lamptown.status.adapters import claude_code, codex, cursor, gemini_cli

    table = {
        "claude-code": claude_code, "claude": claude_code,
        "codex": codex,
        "gemini-cli": gemini_cli, "gemini": gemini_cli,
        "cursor": cursor,
    }
    return table[provider]


PROVIDERS = ("claude-code", "codex", "gemini-cli", "cursor")
