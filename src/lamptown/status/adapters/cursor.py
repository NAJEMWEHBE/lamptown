"""Cursor agent hooks -> agent-status/1.

Register ``lamptown status hook cursor`` in ``~/.cursor/hooks.json`` (see
``examples/cursor/hooks.json``) for the non-gating events only: ``sessionStart``,
``beforeSubmitPrompt``, ``postToolUse``, ``afterAgentResponse``, ``stop`` and
``sessionEnd``. Cursor sends one JSON object on stdin with ``conversation_id``,
``hook_event_name`` and ``workspace_roots``. ``user_email`` is never read.

Do NOT register this hook for permission events (``preToolUse``, ``beforeShellExecution``,
``beforeMCPExecution``, ``beforeReadFile``...): Lamptown never answers a permission
question for you, and Cursor may treat an empty answer there as a block.

Checked against https://cursor.com/docs/agent/hooks on 2026-11-04.
"""

from __future__ import annotations

import json
from typing import Any

from lamptown.status.adapters import (
    Update,
    excerpt,
    folder_name,
    folder_path,
    make_id,
    one_line,
    stop_update,
)

AGENT = "cursor"
DISPLAY = "Cursor"


def translate(payload: dict[str, Any], on_stop: str = "ok", *,
              excerpts: bool = False) -> Update | None:
    event = payload.get("hook_event_name")
    roots = payload.get("workspace_roots")
    root = roots[0] if isinstance(roots, list) and roots else None
    folder = folder_name(root)
    session = payload.get("conversation_id") or payload.get("session_id")
    base = Update(id=make_id(AGENT, session, folder), agent=AGENT, title=folder or None,
                  display=DISPLAY, folder_path=folder_path(root))

    if event == "sessionStart":
        base.state, base.message, base.needs_you = "ok", "Session started", []
        base.session_start = True
        return base
    if event == "beforeSubmitPrompt":
        base.state, base.message, base.needs_you = "working", "Working", []
        return base
    if event in ("postToolUse", "afterShellExecution", "afterMCPExecution", "afterFileEdit"):
        tool = one_line(payload.get("tool_name"), 60)
        base.state, base.needs_you = "working", []
        base.message = f"Ran {tool}" if tool else (
            "Edited a file" if event == "afterFileEdit" else "Working")
        return base
    if event == "afterAgentResponse":
        line = excerpt(payload.get("text")) if excerpts else ""
        if not line:
            return None  # the reply itself is only written with excerpts on
        base.message = f"Said: {line}"
        return base
    if event == "stop":
        status = payload.get("status")
        if status == "error":
            base.state, base.message = "error", "The agent stopped with an error"
            return base
        if status == "aborted":
            base.state, base.message, base.needs_you = "ok", "Stopped by you", []
            return base
        return stop_update(base, on_stop, None)
    if event == "sessionEnd":
        if payload.get("reason") == "error":
            detail = excerpt(payload.get("error_message")) if excerpts else ""
            base.state = "error"
            base.message = f"Session ended with an error: {detail}" if detail else (
                "Session ended with an error")
            return base
        base.state, base.message, base.needs_you = "ok", "Session ended", []
        return base
    return None


def respond(payload: dict[str, Any]) -> str:
    """``beforeSubmitPrompt`` must let the prompt through; other events need no answer."""
    if payload.get("hook_event_name") == "beforeSubmitPrompt":
        return json.dumps({"continue": True})
    return ""
