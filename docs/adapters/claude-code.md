# Claude Code

Claude Code runs shell commands on hook events and sends each one a JSON object on stdin.
Lamptown's hook turns those events into one status file per session.

## Set up

Merge [`examples/claude-code/settings.json`](../../examples/claude-code/settings.json)
into `~/.claude/settings.json` (every project) or `.claude/settings.json` (one project).
Each entry is:

```json
{"hooks": [{"type": "command", "command": "lamptown status hook claude-code", "timeout": 10}]}
```

`timeout` is in seconds. If `lamptown` is not on the `PATH` Claude Code uses, give the
full path to it (`pipx` puts it in `~/.local/bin`).

## What lights up

| Event | Lamp | Message |
|---|---|---|
| `SessionStart` | ok | Session started / resumed (`source` `resume` or `compact`) |
| `UserPromptSubmit` | working | Working |
| `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch` | working | Using / Ran `<tool>` |
| `PermissionRequest` | waiting + need | Allow `<tool>`? |
| `Notification` | waiting + need | Needs your permission (`permission_prompt`), Has a question for you (`elicitation_dialog`), else Needs you; not for `idle_prompt`, `auth_success` or `agent_completed` |
| `Stop` | ok (or waiting with `--on-stop waiting`) | Turn finished; stays working while `background_tasks` run |
| `StopFailure` | error | Stopped by an API error: `<error>` (only a short error code such as `rate_limit`, else "unknown") |
| `SessionEnd` | ok | Session ended |

Other events (`SubagentStop`, `PreCompact`...) don't change the lamp. The example config
registers only the events that matter, to keep each turn fast; add `PreToolUse` or
`PermissionRequest` if you want a finer lamp.

The file id is `claude-code-<first 12 characters of session_id>`; the title is the
name of `cwd`'s folder, and `cwd` itself is kept as `x-folder` (only so `private_dirs` can
hide the session; the town never shows it). Use `--id` to share one lamp between sessions.
Prompts, replies, notification text, tool input and file contents are never written.
Reply text is not written unless you opt in: add `--excerpts` after the provider (or set
`LAMPTOWN_STATUS_EXCERPTS=1`) and the message also carries the first line of the reply, cut
to 80 characters, with anything that looks like a key, token or password masked. See
[../status.md](../status.md#what-a-hook-writes). The hook prints nothing and exits 0, which Claude Code
treats as "no effect", so it never allows, denies or blocks anything.

## Checked

Event names, the common stdin fields (`session_id`, `transcript_path`, `cwd`,
`hook_event_name`), `SessionStart.source`, `Notification.message` / `notification_type`,
`Stop.last_assistant_message` / `background_tasks`, `StopFailure.error` and the
exit-0 rule were checked on 2026-11-04 against https://code.claude.com/docs/en/hooks.
Not confirmed: whether `PostToolBatch` and `UserPromptExpansion` keep those names in
later releases; the adapter handles them but the example doesn't depend on them.
