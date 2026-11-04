"""Claude Code hooks -> agent-status/1.

Register ``lamptown status hook claude-code`` as a ``"type": "command"`` hook in
``settings.json`` (see ``examples/claude-code/settings.json``). Claude Code sends one JSON
object on stdin with ``session_id``, ``cwd`` and ``hook_event_name``; the hook prints
nothing and always exits 0, so it never blocks or changes Claude's behaviour.

Checked against https://code.claude.com/docs/en/hooks on 2026-11-04.
"""

from __future__ import annotations

import re
from typing import Any

from lamptown.status.adapters import (
    Update,
    excerpt,
    folder_path,
    make_id,
    one_line,
    project_folder,
    stop_update,
)

AGENT = "claude-code"
DISPLAY = "Claude Code"

# Notification types that are not a request for the user.
_QUIET_NOTIFICATIONS = {"idle_prompt", "auth_success", "agent_completed"}
# Fixed words per notification type: the notification's own text can quote an MCP server.
_NOTIFICATION_TEXT = {"permission_prompt": "Needs your permission",
                      "elicitation_dialog": "Has a question for you"}
_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def translate(payload: dict[str, Any], on_stop: str = "ok", *,
              excerpts: bool = False) -> Update | None:
    event = payload.get("hook_event_name")
    folder = project_folder(payload.get("cwd"))  # L5-fix: never the login name
    base = Update(id=make_id(AGENT, payload.get("session_id"), folder), agent=AGENT,
                  title=folder or None,
                  display=DISPLAY, folder_path=folder_path(payload.get("cwd")))
    tool = one_line(payload.get("tool_name"), 60)

    if event == "SessionStart":
        resumed = payload.get("source") in ("resume", "compact")
        base.state, base.needs_you = "ok", []
        base.message = "Session resumed" if resumed else "Session started"
        base.session_start = not resumed
        return base
    if event in ("UserPromptSubmit", "UserPromptExpansion"):
        base.state, base.message, base.needs_you = "working", "Working", []
        return base
    if event == "PreToolUse":
        base.state, base.needs_you = "working", []
        base.message = f"Using {tool}" if tool else "Using a tool"
        return base
    if event in ("PostToolUse", "PostToolUseFailure", "PostToolBatch"):
        base.state, base.needs_you = "working", []
        base.message = f"Ran {tool}" if tool else "Working"
        return base
    if event == "PermissionRequest":
        text = f"Allow {tool}?" if tool else "Allow a tool?"
        base.state, base.message, base.needs_you = "waiting", text, [{"text": text}]
        return base
    if event == "Notification":
        if payload.get("notification_type") in _QUIET_NOTIFICATIONS:
            return None
        text = (excerpt(payload.get("message")) if excerpts else "") or _NOTIFICATION_TEXT.get(
            payload.get("notification_type"), "Needs you")
        base.state, base.message, base.needs_you = "waiting", text, [{"text": text}]
        return base
    if event == "Stop":
        if payload.get("background_tasks"):
            base.state, base.message = "working", "Waiting on background tasks"
            return base
        return stop_update(base, on_stop, payload.get("last_assistant_message"), excerpts)
    if event == "StopFailure":
        code = payload.get("error")
        reason = code if isinstance(code, str) and _ERROR_CODE.match(code) else "unknown"
        base.state, base.message = "error", f"Stopped by an API error: {reason}"
        return base
    if event == "SessionEnd":
        base.state, base.message, base.needs_you = "ok", "Session ended", []
        return base
    return None  # SubagentStop, PreCompact and the rest don't change the lamp


def respond(payload: dict[str, Any]) -> str:
    return ""
