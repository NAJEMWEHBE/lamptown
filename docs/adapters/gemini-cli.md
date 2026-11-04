# Gemini CLI

Gemini CLI runs `command` hooks on agent events and sends each one a JSON object on stdin.

## Set up

Merge [`examples/gemini-cli/settings.json`](../../examples/gemini-cli/settings.json) into
`~/.gemini/settings.json` (every project) or `.gemini/settings.json` (one project). Each
entry is:

```json
{"hooks": [{"type": "command", "name": "lamptown-status",
            "command": "lamptown status hook gemini-cli", "timeout": 10000}]}
```

`timeout` is in milliseconds here. Gemini CLI parses stdout as JSON, so the hook prints
nothing and exits 0; it never blocks a tool or a turn.

## What lights up

| Event | Lamp | Message |
|---|---|---|
| `SessionStart` | ok | Session started / resumed |
| `BeforeAgent` | working | Working |
| `BeforeTool`, `AfterTool` | working | Using / Ran `<tool>` |
| `Notification` | waiting + need | Needs your permission (`ToolPermission`), else Needs you |
| `AfterAgent` | ok (or waiting with `--on-stop waiting`) | Turn finished |
| `SessionEnd` | ok | Session ended |

`BeforeModel`, `AfterModel`, `BeforeToolSelection` and `PreCompress` don't change the
lamp. The file id is `gemini-cli-<first 12 characters of session_id>`; the title is the
name of `cwd`'s folder, and `cwd` is kept as `x-folder` for `private_dirs`. The prompt, the
reply (`prompt_response`) and the notification's text are never written. Reply text is not written unless you opt in: add `--excerpts` after the provider (or set
`LAMPTOWN_STATUS_EXCERPTS=1`) and the message also carries the first line of the reply, cut
to 80 characters, with anything that looks like a key, token or password masked. See
[../status.md](../status.md#what-a-hook-writes).

## Checked

On 2026-11-04 against https://geminicli.com/docs/hooks/reference/ (mirrors
https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md): the
settings shape, the millisecond timeout, all event names above, the stdin fields
`session_id`, `cwd`, `hook_event_name`, `timestamp`, `SessionStart.source`,
`AfterAgent.prompt_response` / `stop_hook_active` and `Notification.message` /
`notification_type`.
Not confirmed: whether your Gemini CLI version needs hooks switched on first (older
releases had a setting for that; the reference page names none), and that an empty stdout
is accepted on every event (the page says stdout must hold only the JSON answer).
If your Gemini CLI has no hooks, use the [wrapper](wrapper.md):
`lamptown status run --id gemini-paper-kite -- gemini`.
