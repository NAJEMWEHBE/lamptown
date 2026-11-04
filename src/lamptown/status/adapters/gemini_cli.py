"""Gemini CLI hooks -> agent-status/1.

Register ``lamptown status hook gemini-cli`` as a ``"type": "command"`` hook in Gemini
CLI's ``settings.json`` (see ``examples/gemini-cli/settings.json``). Gemini CLI sends one
JSON object on stdin with ``session_id``, ``cwd``, ``hook_event_name`` and ``timestamp``.
stdout must be empty or one JSON object, so the hook prints nothing and exits 0.

Checked against https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md
on 2026-11-04.
"""

from __future__ import annotations

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

AGENT = "gemini-cli"
DISPLAY = "Gemini CLI"


def translate(payload: dict[str, Any], on_stop: str = "ok", *,
              excerpts: bool = False) -> Update | None:
    event = payload.get("hook_event_name")
    folder = folder_name(payload.get("cwd"))
    base = Update(id=make_id(AGENT, payload.get("session_id"), folder), agent=AGENT,
                  title=folder or None,
                  display=DISPLAY, folder_path=folder_path(payload.get("cwd")))
    tool = one_line(payload.get("tool_name"), 60)

    if event == "SessionStart":
        resumed = payload.get("source") == "resume"
        base.state, base.needs_you = "ok", []
        base.message = "Session resumed" if resumed else "Session started"
        base.session_start = not resumed
        return base
    if event == "BeforeAgent":
        base.state, base.message, base.needs_you = "working", "Working", []
        return base
    if event in ("BeforeTool", "AfterTool"):
        base.state, base.needs_you = "working", []
        verb = "Using" if event == "BeforeTool" else "Ran"
        base.message = f"{verb} {tool}" if tool else "Working"
        return base
    if event == "Notification":
        fixed = ("Needs your permission" if payload.get("notification_type") == "ToolPermission"
                 else "Needs you")
        text = (excerpt(payload.get("message")) if excerpts else "") or fixed
        base.state, base.message, base.needs_you = "waiting", text, [{"text": text}]
        return base
    if event == "AfterAgent":
        if payload.get("stop_hook_active"):
            return None
        return stop_update(base, on_stop, payload.get("prompt_response"), excerpts)
    if event == "SessionEnd":
        base.state, base.message, base.needs_you = "ok", "Session ended", []
        return base
    return None  # BeforeModel, AfterModel, BeforeToolSelection, PreCompress


def respond(payload: dict[str, Any]) -> str:
    return ""
