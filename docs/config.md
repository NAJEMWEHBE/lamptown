# Configuration: `lamptown.toml`

One TOML file says which buildings stand in your town and what each one reads. This page lists
every key. For what a collector is and how to write one, see [collectors.md](collectors.md).

No config file at all is fine: the town is then six dark, empty lots.

## Where Lamptown looks

In this order, first match wins:

1. `--config FILE` on the command line;
2. the `LAMPTOWN_CONFIG` environment variable;
3. `lamptown.toml` in your per-user config folder:

| OS | Config file | Default output folder |
|---|---|---|
| Windows | `%APPDATA%\lamptown\lamptown.toml` | `%LOCALAPPDATA%\lamptown\town\` |
| macOS | `~/Library/Application Support/lamptown/lamptown.toml` | `~/Library/Application Support/lamptown/town/` |
| Linux | `$XDG_CONFIG_HOME/lamptown/lamptown.toml` (default `~/.config/lamptown/`) | `$XDG_DATA_HOME/lamptown/town/` (default `~/.local/share/lamptown/town/`) |

A path given with `--config` or `LAMPTOWN_CONFIG` must exist. The per-user file may be absent.

`lamptown init --user` writes a starter file there (plain `lamptown init` writes it in the current
folder), and `lamptown doctor` checks the one you have: every key, every source path, and the
status folder. See the [README](../README.md#your-own-town).

## A whole example

```toml
version = 1

[town]
title = "Harbour lights"
latitude = 48.1            # the sky only: sunrise, sunset and the moon
longitude = 11.6

[[building]]
id = "jobs"                # type defaults to the id when the id is a built-in type
label = "Night jobs"
  [[building.source]]
  kind = "log_glob"
  name = "Nightly backup"
  glob = "~/logs/backup-*.log"
  done = 'Done:\s*(.+)$'
  schedule = "daily 02:30"

[[building]]
id = "harbour"
label = "Projects"
  [[building.source]]
  kind = "projects"
  projects = [
    { name = "Paper Kite", path = "~/code/paper-kite", status = "active" },
    { name = "Tidebook", path = "~/code/tidebook", status = "paused" },
  ]
  [[building.source]]
  kind = "status_dir"      # one berth per agent-status file
  dir = "~/.agent-status"

[[building]]
id = "lab"
type = "contest"
enabled = false            # the lab plot stays an empty lot
```

The demo world's config is a fuller, working example of every built-in room:
[`src/lamptown/demo/world/lamptown.toml`](../src/lamptown/demo/world/lamptown.toml).

## Top level

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | integer | `1` | The config format. This Lamptown reads version 1 only. |
| `[town]` | table | | The town itself (below). |
| `[[building]]` | array of tables | none | One table per building (below). |
| `[live]` | table | off | The live town: agents walking the town, and the lighthouse (below). |

Any other key is an error that names the key and lists the allowed ones.

## `[town]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `title` | string, up to 60 characters | `"Lamptown"` | The page title. |
| `latitude` | number, -90 to 90 | `40` | Where the sun and moon rise. Used for the sky only. |
| `longitude` | number, -180 to 180 | from your UTC offset | Same. |
| `out` | path | per-user output folder (table above) | Where `town.html` and `town-details.html` are written. `--out` on the command line wins. |

## `[[building]]`

The v1 town has six fixed plots. Each built-in room type has a home plot. A building may move to
another free plot, and a custom room type must name one.

| Plot | Home of | Default label |
|---|---|---|
| `tower` | `jobs` | Scheduled jobs |
| `library` | `vault` | Notes vault |
| `gate` | `ledger` | Vetting gate |
| `lab` | `contest` | Idea contest |
| `workshop` | `shelf` | Skill shelf |
| `boathouse` | `harbour` | Projects |

| Key | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | required | 1 to 40 lower-case letters, digits, `-` or `_`. Unique. Used in links (`#room=<id>`) and as `_building` for collectors. |
| `type` | string | the `id`, when the id is a built-in type | The room type: `jobs`, `vault`, `ledger`, `contest`, `shelf`, `harbour`, or your own (lower-case letters, digits, `_`). See [rooms.md](rooms.md). |
| `label` | string, up to 32 characters | the type's default label | The name on the building's plate. |
| `plot` | one of the six plots | the type's home plot | Required for a custom type. Two enabled buildings can't share a plot. |
| `enabled` | `true` / `false` | `true` | `false` leaves the plot as a dark, empty lot. The building is still checked. |
| `room_js` | path | none | Your own room script for this building's type. See [rooms.md](rooms.md#bring-your-own-room). |
| `[[building.source]]` | array of tables | none | Where the room's data comes from (below). A building with no source shows grey. |

## `[[building.source]]`

Every source runs one collector. Its results are merged into the building's room data: rows and
needs are joined in order, and the worst state wins (see
[collectors.md](collectors.md#several-sources-in-one-building)).

Keys every source understands:

| Key | Meaning |
|---|---|
| `kind` | Which collector runs. A built-in kind (below), or one an installed package registers. Required unless `collector` is given. |
| `name` | A label for this source (a job's name, for example). |
| `collector` | A local Python file with a `collect(paths, now)` function, for example `"./mine.py"`. `kind` may then be any word. |

Keys starting with `_` are reserved for Lamptown.

### Paths

Every string under a path-like key is resolved before the collector sees it: `~` becomes your home
folder, and a relative path is read against the folder that holds `lamptown.toml`. Path-like keys
are `path`, `dir`, `file`, `glob`, `paths`, `dirs`, `files`, `globs`, `collector`, and any key
ending in `_path`, `_dir`, `_file` or `_glob`. Lists of strings under those keys are resolved item
by item. Globs stay globs.

Use `~` and relative paths in a config you share. Lamptown never needs an absolute path.

### Regular expressions

Keys such as `done`, `fail`, `needs`, `item` and `triage` take a Python regular expression in a
string, matched line by line (multi-line mode). Use TOML's single-quoted strings so backslashes stay
as written: `done = 'Done:\s*(.+)$'`.

### Schedules

`schedule` (jobs and vault) is one short line, read in the build's local time zone:

| Text | Runs |
|---|---|
| `daily 03:00` | every day at 03:00 |
| `weekdays 08:30` | Monday to Friday at 08:30 |
| `weekly tue 07:15` | every Tuesday at 07:15 (`weekly mon,thu 07:15` for several days) |
| `monthly 1 06:00` | on the 1st of each month (a day past the month's end means its last day) |
| `hourly`, `hourly :20` | every hour, on the hour or at 20 past |
| `every 6h`, `every 30m`, `every 2d` | a fixed period; no "next run" until it has run once |

A run is **missed** when the last one is older than the schedule's period plus 15 %.

## Built-in source kinds

| Kind | Room types | Reads |
|---|---|---|
| [`status_dir`](#status_dir) | `harbour`, `jobs` | `agent-status/1` files |
| [`log_glob`](#log_glob) | `jobs` | the newest log file of one job |
| [`markdown_vault`](#markdown_vault) | `vault` | a folder of Markdown notes |
| [`projects`](#projects) | `harbour` | project folders listed in the config |
| [`skill_dirs`](#skill_dirs-and-staging_dir) | `shelf` | folders of skills, plugins or tools |
| [`staging_dir`](#skill_dirs-and-staging_dir) | `shelf` | items on their way in |
| [`scan_glob`](#scan_glob) | `ledger` | security scan reports and your decisions |
| [`contest_json`](#contest_json) | `contest` | `contest.json` files |
| [`static`](#static) | any | nothing: returns what the table says |

Every built-in collector is read-only and uses the Python standard library only.

### `status_dir`

One row per `agent-status/1` file. The format and the commands that write it are in
[status.md](status.md).

| Key | Default | Meaning |
|---|---|---|
| `dir` or `dirs` | `$AGENT_STATUS_DIR`, then `~/.agent-status` | The status folder (or a list of them). |
| `kinds` | `jobs` room: `["job"]`; `harbour` room: `["task", "project"]` | Which file `kind`s this building shows. |
| `stale_after` | `1800` | Seconds before a silent `working` file reads as error "stopped responding", for files that don't set their own. |
| `hide_after` | `86400` | Seconds after which an `ok` file is left out. `0` keeps them all. |
| `quiet_after` | `21600` | Seconds before a silent `working` file turns grey "gone quiet". Quiet files don't set the building's lamp. |
| `expire_after` | `604800` | Seconds after which `waiting`, `error` and quiet files (and unreadable files, by modification time) are left out. `0` keeps them. `lamptown status clear` tidies the folder itself. |
| `private` | `false` | Every row shows as "Private agent": no title, message, needs text or links. |
| `private_dirs` | `[]` | Files whose session folder (`x-folder`) or status folder is in or under one of these show as private. `~`, `$VARS` and config-relative paths work; compared as text, nothing is opened. |

See [status.md](status.md#how-lamps-age) for how lamps age and [private agents](status.md#private-agents).

### `log_glob`

One scheduled job, read from the newest file its glob finds. Add one source per job.

| Key | Default | Meaning |
|---|---|---|
| `glob` or `globs` | required | Where the job's logs are. The newest file is the last run. |
| `name` | from the glob's file name | The job's name. |
| `schedule` | none | When it runs ([schedules](#schedules)). Needed for "next run" and the missed-run rule. |
| `done` | none | A regex for the line a finished run writes. Group 1, if any, is the result shown. |
| `fail` | none | A regex; a match means the run failed. |
| `needs` | none | A regex; a match means the result waits for you. Group 1 (or the whole match) is the need's text. |
| `running_minutes` | `30` | With `done` set: a log with no done line, changed this recently, is still running. Older, it "stopped half-way". |
| `id` | from the name | A stable id for the row. |
| `short` | the name | The name on the window's slate (16 characters). |
| `href` | the log file | A link to open instead of the log. |
| `feeds` | none | A building id this job feeds; a spark flies there while it runs. |
| `colour` | from a palette | `"#rrggbb"` for the job's banner. |

### `markdown_vault`

A folder of Markdown notes. Every top-level folder with `.md` files in it becomes one drying line
in the room, found automatically.

| Key | Default | Meaning |
|---|---|---|
| `path` | required | The vault folder. |
| `inbox` | none | A glob inside the vault. The newest match is the inbox note. |
| `item` | `'^##\s+'` | Where each idea in the inbox note starts. |
| `triage` | `'triage:\s*(keep\|drop)\b'` | A match sorts an idea; group 1 `drop` drops it. |
| `from` | a `from:` / `target:` line | A regex for where an idea came from (group 1). |
| `dropped`, `kept` | none | Folders (inside the vault) of dropped and kept notes. |
| `reviews` | none | A glob of review notes with a `YYYY-MM-DD` date in their names. |
| `log` | none | A log file; lines like `## [2026-03-01] review` count as reviews. |
| `review_line` | as above | The regex for those log lines (group 1 = the date). |
| `schedule` | none | When the review runs ([schedules](#schedules)). |
| `run_hours` | `3` | How long after its slot a review may still be running. |
| `exclude` | `[]` | Top-level folders that are not note shelves (`templates`, say). |
| `labels` | folder names | Board names, in order: `labels = { field-notes = "FIELD" }`. |

Hidden folders (starting with `.` or `_`) and the inbox, review, dropped and kept folders are never
shelves. Notes right in the vault folder form one "loose notes" shelf.

### `projects`

One berth per project you list.

| Key | Default | Meaning |
|---|---|---|
| `projects` | required | An array of inline tables, one per project (below). |
| `private_dirs` | `[]` | Folders Lamptown must never read inside. Any project in or under one is private. |
| `busy_minutes` | `30` | A change this recent means someone is at work on it. |

Each project:

| Key | Meaning |
|---|---|
| `name` | Required. |
| `path` | The project folder (`~` and relative paths work). |
| `status` | `active` (default), `paused` (or `parked`), `closed` (or `done`, `archived`). |
| `private` | `true`: never read inside. It shows as a moored boat with a PRIVATE tag. |
| `id`, `short`, `board` | Its id, berth board name (14 characters) and wall board name (9). |
| `note`, `since` | A line of text, and a date (`YYYY-MM-DD`). |

What is read, for **active** projects only: `TASKS.md` checkboxes (the planks on the hull), open
items under a heading that starts "Needs you" or "Waiting on you" (they wait on you; items starting
"(agent" or "(lead" are the agent's own), the newest line of `.git/logs/HEAD`, and three file
times. Paused, closed and private projects: only whether the folder exists.

### `skill_dirs` and `staging_dir`

Every sub-folder of `dirs` is one item: **installed** when its manifest gives a name and a
description, **broken** when it can't be read.

| Key | Default | Meaning |
|---|---|---|
| `dir` or `dirs` | required for `skill_dirs` | Folders that hold one item per sub-folder. |
| `staging_dir` | none | Items on the way in: **staged** (a readable manifest, waiting for you) or **building** (no manifest yet). The `staging_dir` kind reads only this folder (`dir` works too). |
| `manifest` | `"SKILL.md"` | The manifest file. Front matter `name` and `description` are read; a `.json` manifest is read for `name` and `description`. |
| `bundle_manifest` | `"manifest.json"` | A folder with this (and no manifest of its own) is a bundle another app syncs in. It is counted, never listed as an item to fix. |
| `noun` | `"skill"` | What the room calls an item: `skill`, `plugin`, `tool`... |
| `shortnames_file` | none | A JSON file of plate names: `{"folder": "PLATE"}`. |

Folders starting with `.` or `_` are skipped.

### `scan_glob`

The two-signature register: a scanner's verdict and your own call, per tool.

| Key | Default | Meaning |
|---|---|---|
| `glob` | required | Scan files of third-party tools (a string or a list). |
| `own_glob` | none | Scan files of tools built here. |
| `installed_dir` | none | What is installed (a string or a list); one sub-folder per item. |
| `own_dir` | none | Its sub-folder names are home-made tools. |
| `decisions_file` | none | Your calls (format below). Missing means no calls yet; unreadable turns the room red. |
| `format` | `"auto"` | `auto`, `generic` or `skillspector`. |
| `scanning_minutes` | `10` | An unreadable scan file younger than this is still being written. |
| `manifest`, `bundle_manifest`, `noun` | as for `skill_dirs` | How installed items are recognised and named. |

A **generic** scan file:

```json
{"tool": "kite-knots", "verdict": "safe", "risk": 12, "flags": 2,
 "scanned_at": "2026-03-01T10:00:00+00:00", "scanner": "any name"}
```

`verdict` is `safe`, `caution` or `block` (`pass`/`ok`, `warn`/`review` and
`fail`/`unsafe`/`do_not_install` are understood too). `risk` (or `score`) is 0 to 100. `flags` is
a count or a list. `scanned_at` needs an offset; without one the file's own time is used.

The decisions file:

```json
{"tools": {"kite-knots": {"call": "install", "date": "2026-03-01", "origin": "third-party",
                          "note": "read every flag", "source": "https://example.com/kite-knots"}}}
```

`call` is `install`, `skip` or `park`. `origin` is `own` or `third-party`.

### `contest_json`

| Kind | Keys | Reads |
|---|---|---|
| `contest_json` | `file`, `files`, `glob` or `globs` | one or more `contest.json` files |

The format is described in [contest-format.md](contest-format.md).

### `static`

Returns the table as room data and reads nothing. Handy for a fixed sign, a test or a demo.

```toml
  [[building.source]]
  kind = "static"
  state = "waiting"
  label = "Two forms to sign"
  needs = [{ text = "Sign the form" }]
```

Every key except `kind`, `name` and the reserved `_` keys is passed through. `state` defaults to
`ok`.

## `[live]`

The live town: your agents walk the town as they work. A `[live]` table switches it on: the
build also writes `town-live.js` and `town-history.js` next to `town.html`, and the town gets a
lighthouse. `lamptown watch` (or the desktop app) keeps those files up to date. Only names leave
your computer's files: a title, a project folder's name and a tool. Never message text, file
contents or full paths. The whole picture is in [live.md](live.md).

```toml
[live]
poll = 3                          # seconds between live ticks (1 to 60)
history_days = 7                  # days the lighthouse's book keeps (1 to 31)
private_dirs = ["~/private"]      # agents working in or under these show as "Private agent"
# status_dir = "~/.agent-status"  # default: the status_dir source's dir, else $AGENT_STATUS_DIR

[[live.reader]]
kind = "claude_code"              # a built-in reader, or reader = "./my_reader.py"

[[live.place]]
dir = "~/work/ideas"              # work in or under this folder (a glob: from its fixed part)
plot = "lab"                      # walks to this plot

[[live.group]]
dir = "~/notes/chats"             # work in or under this folder (a glob: from its fixed part)
name = "chats"                    # shows under this one project name
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | true/false | `true` | `false` keeps the table but switches the live town off. |
| `poll` | number | `3` | Seconds between Watcher runs. The page's heartbeat (`town-live.js`) is still written at least every 10 s, and the 15 s rebuild keeps its pace, however slow the poll. |
| `history_days` | integer | `7` | Days of history. |
| `private_dirs` | path or list | none | Private folders. They count for `status_dir` sources too, and a `status_dir` source's own `private_dirs` count here. An agent is private when its folder (`x-folder`), its `x-project` written as a path, or the status folder is in or under one. |
| `status_dir` | path | see above | The status folder the live town watches. A `status_dir` source with no `dir` reads it too. |
| `[[live.reader]]` | array of tables | none | `kind = "<name>"` or `reader = "./file.py"`; every other key goes to the reader as an option (path keys are resolved as for sources). |
| `[[live.place]]` | array of tables | none | `dir` and `plot` (one of the six plots). |
| `[[live.group]]` | array of tables | none | `dir` and `name` (1 to 40 characters, not a path): every agent working in or under `dir` (its folder, or its `x-project` written as a path) shows under the project `name`, one berth for all of them. The most specific group wins; a private folder still wins over a group. `dir` may not be a root or your home folder (or above it). See below. |

The built-in readers (each one's options are in [live.md](live.md#what-it-reads-and-what-it-keeps)):

| `kind` | Follows | Options |
|---|---|---|
| `claude_code` | Claude Code's session files and logs, no hooks needed | `root` (`~/.claude`), `on_stop` (`"ok"` or `"waiting"`), `days` (7), `titles` (`"session"` or `"folder"`) |
| `codex` | Codex's rollout logs and thread titles, no hooks needed | `root` (`$CODEX_HOME`, else `~/.codex`), `days` (7) |
| `git` | commits and pushes from repository reflogs | `repos` (a list of folders), `status_dir` (also watch the folders of the agents in this status folder and count their commits and pushes on them; default: the live town's own `status_dir`; `false`: `repos` only, as events), `days` (7) |
| `ci_gh` | optional: a pushed repository's GitHub CI runs, through the GitHub CLI (`gh run list`; the only reader that goes online, see [live.md](live.md#github-ci-runs-ci_gh)) | `repos`, `status_dir` (as for `git`; default: the live town's own), `gh` (`"gh"`), `every` (120, at least 120 s per repository), `timeout` (20 s, 5 to 60), `hosts` (`["github.com"]`) |

`~`, `$VARS` and config-relative paths work in `private_dirs`, `status_dir`,
`[[live.place]] dir` and `[[live.group]] dir`, exactly as in a `status_dir` source's
`private_dirs`.

**Folders named by date.** Some tools make one folder per chat or task and name it by the date
followed by the chat's first words (`~/notes/chats/2026-03-14-plan-the-launch`). A folder whose
name starts with a date (`YYYY-MM-DD` and then `-`, `_`, a space, `.` or `T`) never shows the
words after it: it shows as its parent folder's name (`chats`), so all of them share one berth.
With no parent to name (a bare name, or a parent that is a root or your home folder) it shows as
the date alone (`2026-03-14`). The words never reach the page, the status files' titles or the
book. Why the parent and not "untitled": the parent is a folder you or the tool chose, it groups
the chats the way the tool already does, and it says where they live. A `[[live.group]]` for
that parent names them however you like instead.

A silent agent ages as the `status_dir` source that reads the live status folder says
(its `stale_after`, `quiet_after` and `expire_after`; else their defaults), so the harbour room
and the live town agree. A finished agent stays on the page for 15 minutes.

An agent started in your home folder, above it, or at a drive or file-system root has no
project on the page (no berth, no project in the book): the folder's name there would be your
login name.

Where an agent walks: the reader's own choice, else the tower for scheduled jobs, else the
building whose folders hold its working folder (from each building's sources: `skill_dirs` and
`staging_dir` folders, the `markdown_vault` path, `scan_glob` globs, `contest_json` files and
globs, `projects` paths, and any `dir`/`glob` key of other sources), else the harbour. The most
specific folder wins; on a tie, `[[live.place]]` wins. A source folder that is your home folder
or above it is ignored (it would hold every folder).

Globs in sources: a wildcard in the file name only (`~/work/kiosk/*.json`) counts the fixed
folder (`~/work/kiosk`). A wildcard in a folder part (`~/work/*/contest.json`) counts only the
folders that hold a matching file (`~/work/sprint-a`, looked at again at most once a minute),
never the fixed part before it: that would claim every sibling project in `~/work`. To send a
whole folder to a building, name it in `[[live.place]]` (its fixed part counts, siblings and
all).

`lighthouse` is reserved: no building may use it as its id, room type or plot.

## Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `LAMPTOWN_CONFIG` | every command | The config file, when `--config` is not given. |
| `AGENT_STATUS_DIR` | `lamptown status`, `status_dir` | The default status folder (else `~/.agent-status`). |
| `LAMPTOWN_APP_DIR` | `lamptown app` | Keep the app's log, lock and web view profile in this one folder. |
| `CHROME` | tests, `tools/make_media.py` | The Chrome, Chromium or Edge to use. On macOS, pick Google Chrome: Chrome for Testing can hang when run headless there. |

## When the config is wrong

Lamptown stops before it builds anything and prints one line that names the file, the key and what
is wrong, for example:

```text
lamptown: config problem: ~/town/lamptown.toml: building 'lab'.plot: 'attic' is not a plot (plots: tower, library, gate, lab, workshop, boathouse)
```

The exit code is 2. A source that fails at build time is different: only that building turns red,
with the reason on its card, and the rest of the town still builds.
