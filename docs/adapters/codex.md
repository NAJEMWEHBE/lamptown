# Codex CLI

Codex has two ways to call Lamptown. Use `notify` for the simplest setup, and add hooks
if you also want the `working` and "permission needed" states.

## `notify` (simplest)

In `~/.codex/config.toml` (Codex reads `notify` only from the user-level file):

```toml
notify = ["lamptown", "status", "hook", "codex"]
```

After every finished turn Codex runs the program with one JSON argument:

```json
{"type": "agent-turn-complete", "thread-id": "...", "turn-id": "...", "cwd": "...",
 "input-messages": ["..."], "last-assistant-message": "..."}
```

| `type` | Lamp | Message |
|---|---|---|
| `agent-turn-complete` | ok (or waiting + "Your turn" with `--on-stop waiting`) | Turn finished |
| anything else | unchanged | ignored until mapped |

`input-messages` (your prompts) and `last-assistant-message` (the reply) are never written;
`cwd` is kept as `x-folder` for `private_dirs`. Reply text is not written unless you opt in: add `--excerpts` after the provider (or set
`LAMPTOWN_STATUS_EXCERPTS=1`) and the message also carries the first line of the reply, cut
to 80 characters, with anything that looks like a key, token or password masked. See
[../status.md](../status.md#what-a-hook-writes). Add options after
`codex`, for example `notify = ["lamptown", "status", "hook", "codex", "--on-stop", "waiting"]`.

## Hooks (richer, optional)

Codex hooks live in `~/.codex/hooks.json` or a `[hooks]` table in `config.toml` (user
level or `<repo>/.codex/`). See [`examples/codex/config.toml`](../../examples/codex/config.toml).
Codex asks you to review and trust new hooks before they run.

| Event | Lamp | Message |
|---|---|---|
| `SessionStart` | ok | Session started / resumed |
| `UserPromptSubmit` | working | Working |
| `PreToolUse`, `PostToolUse` | working | Using / Ran `<tool>` |
| `PermissionRequest` | waiting + need | Allow `<tool>`? |
| `Stop` | ok (or waiting) | Turn finished |
| `Interrupt` | ok | Interrupted |
| `PostModelRequest` with `status: failed` | error | Model request failed (the error text only with `--excerpts`) |
| `SessionEnd` | ok | Session ended |

If you use hooks, `notify` is optional (`Stop` covers a finished turn). The file id is
`codex-<last 12 hex digits of the session or thread id>` (Codex thread ids are UUIDv7: their
first digits are a clock, so parallel sessions share them; the last ones are random), so
`notify` and hooks update the same file only when Codex gives them the same id. The live town's
`codex` reader uses the same id, so it and the hooks share one record
([live.md](../live.md)). Lamptown 0.1 used the first 12 characters: a file a 0.1 hook wrote
goes stale once after the upgrade, see [Upgrading from 0.1](../live.md#upgrading-from-01).

## Checked

On 2026-11-04 against https://learn.chatgpt.com/docs/config-file/config-advanced
(`notify`; the older developers.openai.com Codex config page redirects there) and
https://learn.chatgpt.com/docs/hooks (hooks): the `notify` field names with hyphens,
`agent-turn-complete` as the only type, the hook events above and the stdin fields
`session_id`, `cwd`, `hook_event_name`, `turn_id`, `last_assistant_message`.
Not confirmed: that the JSON is always the last argument (the docs say "one JSON
argument"; Lamptown reads the first argument that is not an option, else stdin), the exact `status`
and `error` fields of `PostModelRequest`, and whether `thread-id` in `notify` equals
`session_id` in hooks.
