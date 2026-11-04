"""OpenAI Codex CLI -> agent-status/1, through ``notify`` or through Codex hooks.

* ``notify = ["lamptown", "status", "hook", "codex"]`` in ``~/.codex/config.toml``: Codex
  runs the program with one JSON argument (``type``, ``thread-id``, ``turn-id``, ``cwd``,
  ``input-messages``, ``last-assistant-message``). Only ``agent-turn-complete`` exists today.
* Codex hooks (``~/.codex/hooks.json`` or ``[hooks]`` in ``config.toml``): one JSON object on
  stdin with ``session_id``, ``cwd`` and ``hook_event_name``. Print nothing, exit 0.

Checked on 2026-11-04 against https://learn.chatgpt.com/docs/config-file/config-advanced
(notify) and https://learn.chatgpt.com/docs/hooks (hooks).
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

AGENT = "codex"
DISPLAY = "Codex"


def _base(payload: dict[str, Any]) -> Update:
    folder = folder_name(payload.get("cwd"))
    session = payload.get("session_id") or payload.get("thread-id") or payload.get("thread_id")
    return Update(id=make_id(AGENT, session, folder), agent=AGENT, title=folder or None,
                  display=DISPLAY, folder_path=folder_path(payload.get("cwd")))


def translate(payload: dict[str, Any], on_stop: str = "ok", *,
              excerpts: bool = False) -> Update | None:
    base = _base(payload)
    kind = payload.get("type")
    if kind is not None and "hook_event_name" not in payload:
        if kind == "agent-turn-complete":
            return stop_update(base, on_stop, payload.get("last-assistant-message"), excerpts)
        return None  # future notify types: ignore until mapped

    event = payload.get("hook_event_name")
    tool = one_line(payload.get("tool_name"), 60)
    if event == "SessionStart":
        resumed = payload.get("source") == "resume"
        base.state, base.needs_you = "ok", []
        base.message = "Session resumed" if resumed else "Session started"
        base.session_start = not resumed
        return base
    if event == "UserPromptSubmit":
        base.state, base.message, base.needs_you = "working", "Working", []
        return base
    if event in ("PreToolUse", "PostToolUse"):
        base.state, base.needs_you = "working", []
        verb = "Using" if event == "PreToolUse" else "Ran"
        base.message = f"{verb} {tool}" if tool else "Working"
        return base
    if event == "PermissionRequest":
        text = f"Allow {tool}?" if tool else "Allow a tool?"
        base.state, base.message, base.needs_you = "waiting", text, [{"text": text}]
        return base
    if event == "Stop":
        return stop_update(base, on_stop, payload.get("last_assistant_message"), excerpts)
    if event == "Interrupt":
        base.state, base.message, base.needs_you = "ok", "Interrupted", []
        return base
    if event == "PostModelRequest" and payload.get("status") == "failed":
        reason = excerpt(payload.get("error")) if excerpts else ""
        base.state = "error"
        base.message = f"Model request failed: {reason}" if reason else "Model request failed"
        return base
    if event == "SessionEnd":
        base.state, base.message, base.needs_you = "ok", "Session ended", []
        return base
    return None


def respond(payload: dict[str, Any]) -> str:
    return ""
