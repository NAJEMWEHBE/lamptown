# Changelog

All notable changes to Lamptown are written down here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Lamptown uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.2.0 - Unreleased

The live town: your agents walk the town as they work. See [docs/live.md](docs/live.md).

### Breaking

- **Codex record ids changed.** A Codex session's id is now `codex-` and the last 12 hex digits of
  its thread id (Codex thread ids are UUIDv7: the first digits are a clock, so parallel sessions
  shared the old `codex-<first 12 characters>` id). The hooks and the new Codex reader agree on
  it, and the reader's saved state moves to the new ids by itself. A status file a 0.1 Codex hook
  wrote keeps its old id, so it goes stale once after the upgrade: it ages out as any silent file
  does, or `lamptown status clear` removes it.

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
  second one stands by), crash-safe saved state, a catch-up mode for history.
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
- Docs: [docs/live.md](docs/live.md); README pictures of the live town and the lighthouse room;
  [SECURITY.md](SECURITY.md) lists what the live town reads and writes.

### Changed

- The page swaps new town data in place (`town-data.js`, by `rev`) instead of reloading, and says
  LIVE only while the heartbeat is under 30 s old (else BUILT and the build time).
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
