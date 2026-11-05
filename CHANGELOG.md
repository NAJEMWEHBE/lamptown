# Changelog

All notable changes to Lamptown are written down here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Lamptown uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.2.1 (Unreleased)

Fixes from a whole-project review: privacy gaps closed, crashes fixed, idle rooms made still.

### Privacy

- `lamptown status run` names only the program. A failure shows its exit code, time and how many
  stderr lines it wrote, never the command or its output. `agent_status` records the exception type only.
- The town cuts paths, replaces e-mail addresses and masks secrets in every status title, message
  and need, even in a status file written by hand.
- When a folder becomes private, the Watcher reads every record again. Saved projects, the event
  log, `town-history.js` and open-file counts drop its names, and private sessions keep no titles
  in saved state. A build run before the Watcher's next pass masks that history too.
- A private agent shows only a known tool word (claude, codex, ...), never its free-text `agent`
  field. Making a folder private keeps the history of public git repos the config can place.
- A home folder in `/var/home`, `/export/home`, `Documents and Settings` or network-share form
  never names a project, and project names lose e-mail addresses and secrets.
- The skill shelf's padlock tip names the folder, never its path.
- Secrets are masked in every title, message and room text: `token=...`, `Bearer ...`, JSON
  (`"token": "..."`), secret flags (`--password ...`, glued `-pSECRET`), `PASS=` values,
  well-known token shapes (`ghp_`, `sk-`, `xoxb-`, `AKIA`, `glpat-`, `AIza`, `hf_`, `npm_`), JWTs
  and PEM private keys, URL passwords, webhook paths and signed-link signatures. URL-encoded
  paths are cut like plain ones. The status `agent` field,
  link labels, vault note names, project names and the harbour's newest commit subject get the
  same path, e-mail and secret cuts as titles.
- A leak-hunter test plants made-up paths, e-mails, tokens and private titles in every input the
  town reads. It checks that none of them reaches anything the town writes, and that the privacy
  gate catches every form.
- The privacy gate checks more:
  - tag names, annotated tags and commit e-mails (noreply or example addresses only);
  - file names;
  - WSL, Cygwin, network-share, macOS volume, URL-encoded and lower-case drive paths;
  - deny-listed folders in their common spellings: Git Bash `/f/...`, WSL drive mounts, URL-encoded,
    either slash.
- A privacy-gate baseline entry counts only for a file already on `origin/main` (or
  `--baseline-base <rev>`), so a change can't approve its own new leak.
- `tools/hooks` adds pre-commit and commit-msg hooks. pre-push needs the deny-list unless
  `LAMPTOWN_NO_DENYLIST=1`.

### Changed

- Headless runs are jobs and walk to the clock tower: `claude -p` and Agent SDK sessions, and `codex exec`.
- Status-file jobs read as missed once they are 15 % of their period overdue, and finished job
  rows stay listed in the jobs room.
- Rooms move only while something is running or waiting, and the green lamp no longer pulses.
- Hooks set a session's title only when they create its record, so titles stop flapping.

### Fixed

- A year-1 or year-9999 date shows as text instead of stopping the build or the jobs room.
- Broken characters (lone surrogates) in status, collector, live or history data become `�`
  instead of crashing.
- Schedules keep wall-clock time across daylight-saving changes.
- `lamptown init` writes emoji titles as valid TOML, and `--force` never overwrites an older
  backup (`.bak`, `.bak.1`, ...).
- `doctor` shows config paths as `~/...`.
- Vault patterns with groups, and folder names with glob characters, work.
- A hook writing while the Watcher saves the same record keeps its state: both take a short
  per-record lock (`.<id>.json.lock`).
- A failed save no longer hides the error that caused it.
- Lantern click boxes are fixed, and the bell no longer rings on load.
- The app answers a second launch while its first build is still running.
- A right-click no longer opens a link.
- The app goes back to the town if a page left it before the link guard was in.
- The app checks every 10 s while hidden.
- Browser tests fail on a timeout or a crash instead of hanging.
- CI installs jsonschema and actionlint so their tests run instead of skipping.
- Shared room chrome lives once in the kit: `K.doorSign`, `K.cursor`, `K.sampleBadge`, `K.guard`.

## 0.2.0

The live town: your agents walk the town as they work. See [docs/live.md](docs/live.md).

### Breaking

- **Codex record ids changed.** A Codex session's id is now `codex-` and the last 12 hex digits of
  its thread id (Codex thread ids are UUIDv7: the first digits are a clock, so parallel sessions
  shared the old `codex-<first 12 characters>` id). The hooks and the new Codex reader agree on
  it, and the reader's saved state moves to the new ids by itself. A status file a 0.1 Codex hook
  wrote keeps its old id, so it goes stale once after the upgrade: it ages out as any silent file
  does, or `lamptown status clear` removes it.
- **`lighthouse` is reserved.** A building with `id = "lighthouse"` or `type = "lighthouse"` is now
  a config error, even with `[live]` off: the name belongs to the live town's lighthouse and its
  room. A 0.1 config with such a building (a room of your own called that, from `room_js` or the
  `lamptown.rooms` entry point) stops loading until you rename it; the error names the building.
  (`plot = "lighthouse"` was never a plot; its error now says why.)

### Added

- **`[live]`** in `lamptown.toml` switches the live town on: `enabled`, `poll`, `history_days`,
  `private_dirs`, `status_dir`, `[[live.reader]]`, `[[live.place]]` and `[[live.group]]`
  ([docs/config.md](docs/config.md#live)).
- **`lamptown watch`** and **`lamptown open --watch`**: run the Watcher every few seconds and
  rebuild every 15 s, writing `town-live.js`, `town-data.js` and `town-history.js` next to
  `town.html`; an open page picks them up by itself, with no reload. `--every`, `--rebuild`,
  `--quiet`.
- **The Watcher**: runs the readers, writes the status files and a 7-day event log
  (`<status folder>/events/YYYY-MM-DD.jsonl`, names only), one Watcher per status folder (a
  second one stands by), crash-safe saved state, a catch-up mode for history. It removes the
  status files it wrote itself once they are older than `expire_after` plus `[live] history_days`
  (14 days by default), so the status folder stays small; a file a hook or `status run` wrote
  last is never removed. No config switch: `Watcher(remove_own=False)` in Python turns it off.
- **Readers** that follow a tool's own files, no hooks needed: `claude_code` (sessions,
  subagents, questions and likely permission prompts, API errors, changed-file counts), `codex`
  (rollout logs and thread titles) and `git` (commits and pushes from reflogs, counted on the
  agent working there). Your own: `reader = "./mine.py"` or the `lamptown.readers` entry point.
- **`ci_gh` reader** (optional, off by default): a pushed project's GitHub CI runs through
  `gh run list` (read-only, only for repositories pushed in the last hour, at most every 120 s
  each, on a background thread). A run flies as the pennant on its project's boat and is its own
  line in the keeper's book; no figure walks for it. Skips cleanly, with one note, when `gh` is
  missing, not logged in or offline. Readers may now say such things through a `notes` list.
- **Lamplighters**: one figure with a lantern per live agent, at the building it works in.
  Waiting agents sit by the door, failed ones step aside with a red lantern, quiet ones stand
  dark with a "?", done ones hang their lantern on the hook for 15 minutes. Subagents follow as
  helpers; changed files ride a cart. Hover for a card, click to pin it.
- **The lighthouse** in the bay: its beam sweeps while agents work; a new wait flashes it and
  rings its bell once, then its light runs up the footpaths to the door that needs you (red for
  a failure).
- **The lighthouse room**: the keeper's handover book of the last 7 days, a replay clock, and
  a closing slip of what is still open.
- **Berths**: one boat per live project at the harbour's wharf, with its sail up once pushed and
  a CI pennant; new projects are launched down the slipway.
- **The desktop app** runs the Watcher itself, pushes each payload into the page, and its window
  title counts what needs you ("2 need you - Lamptown").
- `lamptown demo` is live: nine made-up agents in every state, a helper, berths and a week of
  history (its book follows each `--state`, and today's page is filled at any hour).
  `--no-live` builds it without watching.
- **For room authors**: a room may have `update(data)`, called with its new data while you're
  inside, so it keeps its view instead of walking in again; and the kit has `K.live()` (the fresh
  live payload, else `null`), `K.liveScript()` (load a `town-<name>.js` the way the town polls),
  `K.pointer()` (a mouse event in the room's pixels) and `K.redraw()` (draw a still scene again)
  ([docs/rooms.md](docs/rooms.md#bring-your-own-room), [docs/kit-k.md](docs/kit-k.md)).
- The town page's screen-reader text says the live agents too, while the corner says LIVE and one
  works, waits or failed: how many in each state, and who waits for you, by name and building.
- Docs: [docs/live.md](docs/live.md); README pictures of the live town and the lighthouse room;
  [SECURITY.md](SECURITY.md) lists what the live town reads and writes.

### Changed

- The page swaps new town data in place (`town-data.js`, by `rev`) instead of reloading, and says
  LIVE only while the heartbeat is under 30 s old (else BUILT and the build time), in the desktop
  app too.
- The harbour room shows a failed agent's bay as FAILED (CAN'T READ is for a project whose
  `TASKS.md` can't be read), and the live berths along its floor.
- A session started in your home folder or at a drive root never shows the folder's name (your
  login name) as its title or project.
- A folder named by date and words (`2026-03-14-plan-the-launch`, as tools make per chat) never
  shows the words: it is named by its parent folder, or the date alone; `[[live.group]]` gives
  every folder under one `dir` a project name of your own ([docs/config.md](docs/config.md#live)).
- The lighthouse's sign hangs on two lines ("2 AGENTS" / "FAILED") so it never runs off the map's
  edge, up to "999+ AGENTS".

## 0.1.0

The first version: the town, six rooms, collectors, `agent-status/1` and its hooks, the desktop
app.
