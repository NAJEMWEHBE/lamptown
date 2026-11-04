# Cursor

Cursor's agent runs hook commands on agent events and sends each one a JSON object on
stdin.

## Set up

Copy [`examples/cursor/hooks.json`](../../examples/cursor/hooks.json) to
`~/.cursor/hooks.json` (every project) or `.cursor/hooks.json` in a project, or merge its
`hooks` into the file you have:

```json
{"version": 1, "hooks": {"stop": [{"command": "lamptown status hook cursor", "timeout": 10}]}}
```

**Register it only for the events in the example.** Do not add it to permission events
(`preToolUse`, `beforeShellExecution`, `beforeMCPExecution`, `beforeReadFile`...):
Lamptown never answers a permission question for you, and Cursor treats an answer that
isn't valid JSON as a block. For `beforeSubmitPrompt` the hook prints
`{"continue": true}`, so your prompt always goes through.

## What lights up

| Event | Lamp | Message |
|---|---|---|
| `sessionStart` | ok | Session started |
| `beforeSubmitPrompt` | working | Working |
| `postToolUse`, `afterShellExecution`, `afterMCPExecution`, `afterFileEdit` | working | Ran `<tool>` / Edited a file |
| `afterAgentResponse` | unchanged | ignored, unless `--excerpts`: Said: first line of `text` |
| `stop` | ok (or waiting with `--on-stop waiting`); error for `status: error`; ok "Stopped by you" for `aborted` | Turn finished |
| `sessionEnd` | ok; error for `reason: error` | Session ended (with an error) |

Cursor has no "needs you" event outside the permission hooks, so a Cursor lamp turns
amber only with `--on-stop waiting`. The file id is `cursor-<first 12 characters of
conversation_id>`; the title is the first workspace root's folder name, and that root is
kept as `x-folder` for `private_dirs`. The prompt, the agent's replies, attachments, error
text and `user_email` are never written. Reply text is not written unless you opt in: add `--excerpts` after the provider (or set
`LAMPTOWN_STATUS_EXCERPTS=1`) and the message also carries the first line of the reply, cut
to 80 characters, with anything that looks like a key, token or password masked. See
[../status.md](../status.md#what-a-hook-writes).

## Checked

On 2026-11-04 against https://cursor.com/docs/agent/hooks: the `hooks.json` shape with
`version: 1` and `timeout` in seconds, the event names above, the common fields
(`conversation_id`, `hook_event_name`, `workspace_roots`, `user_email`),
`stop.status`, `sessionEnd.reason` / `error_message`, `afterAgentResponse.text` and the
`continue` answer of `beforeSubmitPrompt`. Per that page, invalid JSON or exit code 2
blocks, and crashes, timeouts and other exits fail open unless `failClosed` is set.
Not confirmed: the exact location of the user-level `hooks.json` on every OS (the
example uses `~/.cursor/hooks.json`), and what Cursor does with an empty stdout on a
permission event (another reason not to register it there).
