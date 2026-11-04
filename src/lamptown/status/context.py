"""``with agent_status("nightly-report") as s:`` lights a lamp around any Python code."""

from __future__ import annotations

import os
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from lamptown.status.store import iso, update_status, utcnow


class StatusHandle:
    """Changes one status file. Disk errors only warn, so a lamp never breaks your program."""

    def __init__(self, status_id: str, directory: str | os.PathLike | None, strict: bool):
        self.id = status_id
        self.directory = directory
        self.strict = strict
        self.doc: dict = {}

    @property
    def state(self) -> str | None:
        return self.doc.get("state")

    def set(self, **changes: Any) -> dict:
        """Change any fields (``state``, ``message``, ``progress``...); ``None`` removes one."""
        try:
            self.doc = update_status(self.id, self.directory, **changes)
        except OSError as exc:  # bad values (StatusError) still raise: those are bugs
            if self.strict:
                raise
            warnings.warn(f"agent status {self.id!r} not written: {exc}", stacklevel=2)
        return self.doc

    def message(self, text: str) -> dict:
        return self.set(message=text)

    def progress(self, done: int, total: int) -> dict:
        return self.set(progress={"done": int(done), "total": int(total)})

    def need(self, text: str, href: str | None = None) -> dict:
        """Ask a person for something: adds a need and sets the state to ``waiting``."""
        need = {"text": text, **({"href": href} if href else {})}
        needs = [*self.doc.get("needs_you", []), need]
        return self.set(state="waiting", needs_you=needs)

    def clear_needs(self, state: str = "working") -> dict:
        return self.set(state=state, needs_you=[])


@contextmanager
def agent_status(
    status_id: str,
    title: str | None = None,
    *,
    directory: str | os.PathLike | None = None,
    agent: str | None = None,
    kind: str = "task",
    message: str = "Started",
    done_message: str | None = "Done",
    stale_after: int | None = None,
    strict: bool = False,
) -> Iterator[StatusHandle]:
    """Write ``working`` on entry, ``ok`` on a clean exit and ``error`` on an exception.

    A state set inside the block (``waiting``, ``error``) is kept on a clean exit. The
    exception is never swallowed. ``strict=True`` makes disk errors raise instead of warn.
    """
    handle = StatusHandle(status_id, directory, strict)
    start: dict[str, Any] = {
        "state": "working", "kind": kind, "message": message, "started": iso(utcnow()),
        "needs_you": [], "progress": None,
    }
    if title:
        start["title"] = title
    if agent:
        start["agent"] = agent
    if stale_after is not None:
        start["stale_after"] = stale_after
    handle.set(**start)
    try:
        yield handle
    except BaseException as exc:
        text = "Interrupted" if isinstance(exc, KeyboardInterrupt) else (
            f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        )
        handle.set(state="error", message=" ".join(text.split())[:500])
        raise
    else:
        if handle.state in (None, "working"):
            handle.set(state="ok", **({"message": done_message} if done_message else {}))
