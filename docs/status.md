# Agent status files

Anything that can write a small JSON file can light a lamp in the town. Lamptown reads
**`agent-status/1`** files: one `<id>.json` per agent, task or job, in a status folder.
The format is provider-neutral and has its own spec, so other tools can read and write it
too: [`spec/agent-status-1.md`](../spec/agent-status-1.md) and the JSON Schema
[`spec/agent-status-1.schema.json`](../spec/agent-status-1.schema.json).

```json
{"spec": "agent-status/1", "id": "refactor-auth", "title": "Refactor the auth module",
 "agent": "codex", "kind": "task", "state": "working", "message": "Running tests",
 "needs_you": [{"text": "Approve the plan", "href": "https://example.com/pr/12"}],
 "progress": {"done": 3, "total": 8}, "updated": "2026-11-04T10:12:00Z"}
```

| State | Lamp | Meaning |
|---|---|---|
| `ok` | green | running fine: idle or finished |
| `waiting` | amber | needs you: a person must do something (`needs_you`) |
| `working` | green in town (the building moves), cyan in rooms | running now |
| `error` | red | something is wrong: failed, or `working` for longer than `stale_after` ("stopped responding") |
| (quiet) | grey | `working` but silent for longer than `quiet_after` (default 6 h): "gone quiet", no longer an alert |

Lamps age out, so old sessions don't glow forever: see [how lamps age](#how-lamps-age).

## Where the files live

`$AGENT_STATUS_DIR` when it is set, else `~/.agent-status/`. Every `lamptown status`
command takes `--dir` to use another folder (for example a project's `.agent-status/`).

## Connect your agent

| Tool | How | Guide |
|---|---|---|
| Claude Code | hooks in `settings.json` | [adapters/claude-code.md](adapters/claude-code.md) |
| Codex CLI | `notify` (and optionally hooks) in `~/.codex/config.toml` | [adapters/codex.md](adapters/codex.md) |
| Gemini CLI | hooks in `settings.json` | [adapters/gemini-cli.md](adapters/gemini-cli.md) |
| Cursor | `hooks.json` | [adapters/cursor.md](adapters/cursor.md) |
| Aider, cron, scripts, anything | `lamptown status run --id X -- <command>` | [adapters/wrapper.md](adapters/wrapper.md) |
| Your own code | `lamptown status set` or `agent_status()` | below |

Ready-to-paste configs are in [`examples/`](../examples/).

## `lamptown status set`

Creates or changes one file. Fields you don't give stay as they are.

```sh
lamptown status set paper-kite --title "Paper Kite" --state working -m "Running tests" --progress 3/8
lamptown status set paper-kite --need "Approve the plan" --need-href https://example.com/pr/12
lamptown status set paper-kite --state ok --clear-needs --no-progress -m "Merged"
```

| Option | Meaning |
|---|---|
| `ID` | the file name and `id` (letters, digits, `.`, `-`, `_`) |
| `--state ok\|waiting\|working\|error` | the lamp; a new file starts as `working` |
| `--title`, `-m/--message` | name, and one line about now |
| `--kind task\|job\|project` | `job` goes to the jobs room, the others to the harbour |
| `--agent NAME` | who writes the file |
| `--need TEXT` (repeat) + `--need-href URL` | replaces the needs list and sets `waiting` |
| `--clear-needs` | empties the needs list |
| `--progress DONE/TOTAL`, `--no-progress` | progress bar |
| `--link LABEL=URL` (repeat) | replaces the links list (`https`, `http` or `file` only) |
| `--stale-after SECONDS` | when a silent `working` file turns red (default 1800; 0 = never) |
| `--next-run ISO` | for jobs: when the next run is due |
| `--dir DIR`, `-q` | another status folder; no output |

Exit codes: 0 written, 2 bad value (one line on stderr, nothing written), 1 disk error.

## `lamptown status run`

```sh
lamptown status run --id nightly-backup --title "Nightly backup" --kind job -- ./backup.sh
```

`working` while the command runs (rewritten as a heartbeat), `ok` on exit 0, `error` with
the exit code and the last stderr lines otherwise. Output and exit code pass through (a child
killed by signal N gives `128 + N`). A status folder that can't be written never stops the
command: one warning on stderr, same exit code. See [adapters/wrapper.md](adapters/wrapper.md).

## `lamptown status hook PROVIDER`

`PROVIDER` is `claude-code`, `codex`, `gemini-cli` or `cursor`. Reads one hook payload
(stdin, or a JSON argument for Codex `notify`) and updates that session's file. It
**always exits 0** and prints only what the provider expects, so it never blocks or
changes your agent. Problems go to stderr as one line.

Options after the provider: `--dir DIR`, `--id ID` (fixed id instead of one per
session), `--title TITLE`, `--on-stop ok|waiting` (what a finished turn means; `waiting`
adds a "Your turn" need), `--stale-after SECONDS`, `--excerpts` (see below).

### What a hook writes

Each session gets the id `<provider>-<first 12 characters of the session id>` (Codex:
`codex-<last 12 hex digits of the thread id>`, as Codex thread ids start with a clock) and the
project folder's name as its title (none for the home folder or a drive root: the title is then
the provider's name, never your login name). Exactly these fields are written:

| Field | Value |
|---|---|
| `id`, `agent`, `kind` | `<provider>-<session>`, the provider, `task` |
| `title` | the session folder's name (or `--title`) |
| `state`, `needs_you` | the lamp; a need reads "Your turn", "Allow `<tool>`?", "Needs your permission", "Has a question for you" or "Needs you" |
| `message` | fixed words: "Session started", "Working", "Using `<tool>`" / "Ran `<tool>`" (the tool's name only), "Turn finished", "Session ended", "Stopped by an API error: `<code>`" |
| `x-folder` | the session folder's full path, only so a `status_dir` source's `private_dirs` can hide it; the town never shows it |
| `started`, `updated`, `stale_after` | times, and `--stale-after` if given |

Never written: your prompts, the agent's replies, notification and error text, tool input,
file contents, and account fields (such as e-mail).

**Reply excerpts (opt-in).** Add `--excerpts` after the provider, or set
`LAMPTOWN_STATUS_EXCERPTS=1` in the agent's environment, and a finished turn reads
"Turn finished: `<first line of the reply>`" (Cursor: "Said: ..."), and notifications and
errors carry their own first line. Only the first non-empty line is kept, cut to 80
characters, and anything that looks like a secret (`NAME=value` where the name says key,
token, secret, password or auth; bearer tokens; runs of 24+ key-like characters) is replaced
by `…`. The masking is a safety net, not a promise: leave excerpts off for work you would not
put on a screen.

## `lamptown status list`

```text
waiting  moss-engine  task      1s  Moss Engine
                      needs you: Approve the plan (https://example.com/pr/7)
working  paper-kite   task      1s  Paper Kite: Running tests [3/8]
ok       tidebook     task      1s  Tidebook: Finished in 0 s
```

`--json` for scripts; `--dir` (repeat) for other folders. The stale and quiet rules are
applied (a quiet file lists as `quiet`); files the town has aged out are still listed, until
`lamptown status clear` removes them.

## `lamptown status clear`

```sh
lamptown status clear                  # what the town already leaves out
lamptown status clear --older-than 3d  # anything not updated for 3 days
lamptown status clear --all --dry-run  # say what --all would remove
```

With no option it removes `ok` files older than 1 day and every other file older than 7
days (the default `hide_after` and `expire_after`; a `working` file with `stale_after: 0`
never ages and stays). `--older-than DURATION` (`90s`, `45m`,
`6h`, `7d`, `2w`) removes every file not updated for that long; `--all` removes every status
file. `--dry-run` only lists. `--dir` (repeat) for other folders.

It only removes files it would write itself: valid `agent-status/1` files named `<id>.json`,
directly in the folder (never below it, never a link), and not a file that changed while it
looked. Anything else is left alone and counted. Exit 0, or 1 when a folder can't be read or
a file can't be removed.

## Python

```python
from lamptown.status import agent_status

with agent_status("nightly-report", "Nightly report", kind="job") as s:
    s.message("Drawing charts")
    s.progress(2, 3)
    # s.need("Check the numbers", "https://example.com/report")  -> waiting
```

`working` on entry, `ok` on a clean exit (a `waiting` or `error` you set inside is kept),
`error` with the exception's text if one escapes; the exception is re-raised. Disk errors
only warn (pass `strict=True` to raise), so a lamp never breaks your program.

Lower level: `update_status(id, directory, **fields)` merges and writes atomically,
`write_status(doc)` writes a whole document, `read_status(path)` reads one, and
`validate(doc)` returns a list of problems (empty = valid). All are standard library only.

## The `status_dir` collector

In `lamptown.toml`, any `jobs` or `harbour` building can read a status folder:

```toml
[[building.source]]
kind = "status_dir"
dir = "~/.agent-status"     # or dirs = ["~/.agent-status", "~/code/paper-kite/.agent-status"]
kinds = ["task", "project"] # optional; default jobs -> job, harbour -> task + project
stale_after = 1800          # optional; for files that don't set their own
quiet_after = 21600         # optional; silent working files turn grey "gone quiet"
hide_after = 86400          # optional; ok files older than this are left out (0 = never)
expire_after = 604800       # optional; waiting, error and quiet files older than this too
private = true              # optional; every row shows as "Private agent"
private_dirs = ["~/private"] # optional; sessions in or under these show as "Private agent"
```

- Read-only; no network.
- `working` older than `stale_after` reads as `error` "Stopped responding".
- Links and need links keep only `https:`, `http:` and `file:`; a file with any other
  scheme is invalid.
- A file that isn't valid (bad JSON, over 64 KB, a broken rule) shows as one red row
  named after the file, in the building that reads tasks.
- Same `id` in two folders: the newest `updated` wins.
- Each file's needs feed the town's "needs you" sign.
- No folder at all gives the grey `missing` state.
- At most 500 rows and 200 needs per source, the newest first; the rest become one "and N
  more" row (and need), so a crowded folder never breaks the building.

### How lamps age

`updated` is the file's last write. All four limits are seconds, and `0` switches a rule off.

| Rule | Default | What happens |
|---|---|---|
| `stale_after` | 1800 (30 min) | a silent `working` file reads as red `error` "Stopped responding (last: ...)": something probably crashed. A file may set its own; `0` there means this file never ages. |
| `quiet_after` | 21600 (6 h) | a `working` file silent for longer (and longer than its `stale_after`) turns grey "Gone quiet". It no longer sets the building's lamp; a building with only quiet files is grey. |
| `hide_after` | 86400 (1 day) | `ok` files older than this are left out. |
| `expire_after` | 604800 (7 days) | `waiting`, `error` and quiet files older than this are left out, and so is an unreadable file whose own modification time is that old. |

Nothing is deleted by the town; `lamptown status clear` tidies the folder.

### Private agents

A session in a private project must not show its name in the town. Two keys:

- `private = true`: every file this source reads shows as "Private agent".
- `private_dirs = ["~/private", "~/clients"]`: a file shows as "Private agent" when the
  session's folder (`x-folder`, written by the hooks and `lamptown status run`) or the status
  folder itself is one of these folders or under one. `~` and `$VARS` are expanded; relative
  paths are read against the config's folder. The paths are compared as text: nothing in
  them is opened.

A private row keeps its lamp state, kind, agent and progress. Its title becomes "Private
agent", its id a hash, its message empty (or "Needs you" / "Error"), its needs one "Needs you"
and its links none, so the town, the details page and the town's "needs you" sign carry no
name or text from it. Files written by `lamptown status set` have no `x-folder`: keep
them in a status folder under a private one, or use `private = true` on that source. Your own
code can write the field (`update_status(id, d, **{"x-folder": path})`).

Every key of this source is also in [config.md](config.md#status_dir); the collector contract is in
[collectors.md](collectors.md).

## Reading Claude Code's own files (no hooks)

The live town's Watcher can also follow Claude Code sessions without any hook, through the
built-in reader `kind = "claude_code"` (options: `root`, default `~/.claude`; `on_stop`, `"ok"`
or `"waiting"` as for the hook's `--on-stop`; `days`, default 7; `titles`, `"session"` or
`"folder"`, see below). It reads, read-only:

- `sessions/<pid>.json`: one per running session, `busy` or `idle`. The reader checks that the
  process is still alive (and that a reused process id is not taken for it).
- the session's log under `projects/`: titles, the session folder, the files its `Write` /
  `Edit` / `MultiEdit` / `NotebookEdit` calls changed, questions and tool calls still waiting
  for an answer, API errors.
- its subagents' `.meta.json` and logs: each subagent is a record of its own, with `x-parent`.

It writes the same records as the hook (same ids), so with both you get one record per
session; a hook's newer state wins, and the reader adds `x-files`. What it maps to what:

| Claude Code | Lamp |
|---|---|
| `busy` | `working` |
| `busy`, a question (`AskUserQuestion`, `ExitPlanMode`) not yet answered | `waiting` "Has a question for you" |
| `busy`, a quick tool (Read, Edit, Write...) with no result after 30 s, permissions not bypassed | `waiting` "May need your permission" (a guess: permission prompts leave no line in the log; the hook knows for sure) |
| `busy`, a Bash or MCP tool waiting for permission | `working`, without hooks: a long Bash looks the same as one waiting for you (install the hook to see these prompts) |
| `idle` | `ok` "Turn finished" (`waiting` "Your turn" with `on_stop = "waiting"`) |
| the last reply was an API error | `error` |
| the session exited | `ok` "Session ended" |
| its process died mid-turn | "Stopped responding" a minute after the reader sees it die, then grey "Gone quiet" |
| a subagent not finished, its log quiet for 2 min and the call that launched it answered (or gone) | `ok` "Ended" |

With hooks installed too, the hook's sure states (a permission prompt, say) are not overwritten
by the reader's guesses: the reader's records are timed by the last line that can change the
state (a reply, a prompt, a tool result), never by other lines or by subagents' lines.

Only names leave the reader: the title, the folder's name (plus the folder itself as
`x-folder`, for `private_dirs`) and file *counts*. The title is Claude Code's session title,
which Claude Code writes from your first message (and a subagent's is the task description
Claude wrote for it), so it can carry words from your prompts. For names only, set
`titles = "folder"`: sessions are then named by their project folder and subagents by their
type; or list the folders in `private_dirs` to hide them altogether. A session started in your
home folder or a drive root shows no folder name. The paths of changed files are kept only as
salted hashes in the Watcher's saved state. Logs not changed in `days` are never opened; a big
log is read a little each tick (a live one starts near its end, its history fills in behind).

The first run catches up on up to `days` of history. While any reader is behind, the Watcher
says so (`TickResult.behind`): the app and `lamptown watch` then tick every second instead of
every 3 s, and each such tick may read 32 MB (4 MB a file) within a 200 ms reading deadline;
live sessions are read first. History that no one is writing any more is skimmed: only lines
that can change a count, a title, an error or a turn's end are parsed. A finished subagent log
over 64 MB is read by its head and its last 1 MB only (its file count is then a floor).

## The live town

With a `[live]` table, the status folder is also what the live town watches: every agent's file
here becomes a Lamplighter walking the town, and every change a line in the lighthouse's book. Besides
the hooks and `lamptown status`, built-in readers can write these files from a tool's own logs:
`claude_code` (above), `codex` (Codex's rollout logs) and `git` (commits and pushes, counted on
the agent working in that repository). The live town, its readers and its event log are in
[live.md](live.md).
