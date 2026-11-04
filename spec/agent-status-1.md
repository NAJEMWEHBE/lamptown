# agent-status/1

A tiny, provider-neutral file format that says what one AI agent, task, job or project is
doing right now. Anything that can write a small JSON file can report its status, and
anything that can read a folder can show it: a dashboard, a tray icon, a status bar, a
script.

- **Version:** 1.0 (identifier `agent-status/1`)
- **Machine-readable schema:** [`agent-status-1.schema.json`](agent-status-1.schema.json)
  (JSON Schema 2020-12)
- **Reference implementation:** Lamptown (`lamptown status`, `lamptown.status`), but the
  format does not depend on it. You may implement it in any tool, free of charge.

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

## 1. Files and folders

- One status is one file named `<id>.json`, where `<id>` is the file's `id` field.
- The default folder is `$AGENT_STATUS_DIR` when that variable is set, else
  `~/.agent-status/` in the user's home folder. A project MAY keep its own statuses in a
  `.agent-status/` folder at its root; readers SHOULD let users add such folders.
- Files MUST be UTF-8 JSON objects. Readers SHOULD accept a leading byte-order mark.
- A file MUST NOT be larger than 64 KB (65,536 bytes). Readers MUST skip larger files
  (and SHOULD show them as unreadable).
- **Atomic writes.** Writers MUST write a temporary file in the same folder and then
  rename it over `<id>.json`, so readers never see half a file. The temporary name MUST
  start with `.` or not end in `.json` (for example `.<id>.json.<random>.tmp`).
  Readers MUST ignore names that start with `.` and names that don't end in `.json`.
- Two writers updating the same file: the last rename wins. Writers that merge (read,
  change, write) SHOULD keep fields they don't know about.

## 2. Example

```json
{"spec": "agent-status/1", "id": "refactor-auth", "title": "Refactor the auth module",
 "agent": "codex", "kind": "task", "state": "working", "message": "Running tests",
 "needs_you": [{"text": "Approve the plan", "href": "https://example.com/pr/12"}],
 "progress": {"done": 3, "total": 8}, "updated": "2026-11-04T10:12:00Z",
 "stale_after": 1800, "links": [{"label": "PR", "href": "https://example.com/pr/12"}]}
```

The smallest valid file:

```json
{"spec": "agent-status/1", "id": "nightly-backup", "title": "Nightly backup",
 "state": "ok", "updated": "2026-11-04T02:00:41+01:00"}
```

## 3. Fields

| Field | Required | Type | Meaning |
|---|---|---|---|
| `spec` | yes | string | `agent-status/1`. Readers accept `agent-status/1` and any `agent-status/1.x`. |
| `id` | yes | string | Letters, digits, `.`, `-`, `_`; starts with a letter or digit; at most 128 characters. Also the file name. |
| `title` | yes | string | Short human name, 1 to 200 characters. |
| `state` | yes | string | `ok`, `waiting`, `working` or `error` (section 4). |
| `updated` | yes | string | When the file was last written: RFC 3339 date-time **with an offset** (`Z` or `+02:00`). |
| `kind` | no | string | `task` (default), `job` (scheduled or repeating) or `project`. |
| `agent` | no | string | Who writes the file: `claude-code`, `codex`, `gemini-cli`, `cursor`, `aider`, `cron`... At most 64 characters. |
| `message` | no | string | One line about what is happening now. At most 500 characters. |
| `needs_you` | no | array | Up to 20 `{"text", "href"?}` objects: things a person must do. Empty or absent means nothing. |
| `progress` | no | object | `{"done", "total"}`, whole numbers, `0 <= done <= total`. |
| `stale_after` | no | integer or null | Seconds; see the stale rule. `0` or `null` turns it off. |
| `started` | no | string | When the current run or session started (date-time with an offset). |
| `next_run` | no | string | For `kind: job`: when the next run is due (date-time with an offset). |
| `links` | no | array | Up to 20 `{"label", "href"}` objects. |
| `x-...` | no | any | Extensions. Readers ignore the ones they don't know. |

Readers MUST ignore fields they don't know. Writers SHOULD put their own extras under
`x-` keys so a later version of this standard never clashes with them.

## 4. States

| State | Meaning | Typical lamp |
|---|---|---|
| `ok` | Fine, idle or finished | green |
| `waiting` | Blocked on a person (see `needs_you`) | amber |
| `working` | Running right now | cyan |
| `error` | Failed or stopped responding | red |

Readers that merge many files SHOULD rank them `error > waiting > working > ok`.

**Stale rule.** A file whose `state` is `working` and whose `updated` is older than
`stale_after` seconds MUST be shown as `error` ("stopped responding"), because the
writer probably died. When `stale_after` is absent, readers use 1800 seconds (30
minutes). Long-running writers SHOULD rewrite the file (a heartbeat) more often than
`stale_after`. Other states never go stale; readers MAY hide old `ok` files.

## 5. Links

`href` values in `links` and `needs_you` MUST use the `https:`, `http:` or `file:` scheme,
written in lower case. Readers MUST NOT turn any other scheme (such as `javascript:` or
`data:`) into a clickable link, and SHOULD treat a file that contains one as invalid.
Readers MUST treat every text field as plain text, never as HTML.

## 6. Invalid files

A file that is not JSON, not an object, too big, or breaks a rule in section 3 is
invalid. Readers SHOULD show it as a single unreadable entry (named after the file)
rather than hide it, so the problem gets fixed, and MUST NOT fail as a whole.

When two folders hold files with the same `id`, readers SHOULD show the one with the
newest `updated`.

## 7. Privacy

Status files are local and meant to be glanced at. Writers SHOULD NOT copy prompts, tool
input, file contents, account names, e-mail addresses, tokens or other secrets into them.
A one-line summary is enough. Readers MUST NOT send status files anywhere.

## 8. Versioning

- `agent-status/1.x` versions only add optional fields or relax limits. A reader for
  `agent-status/1` reads every `1.x` file.
- A change that breaks readers gets a new major identifier (`agent-status/2`). Readers
  MUST skip major versions they don't know (and MAY show them as unreadable).

## 9. Writing a file without any library

Any language works. A POSIX shell example:

```sh
dir="${AGENT_STATUS_DIR:-$HOME/.agent-status}"; mkdir -p "$dir"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
tmp="$dir/.nightly-backup.json.$$.tmp"
printf '{"spec":"agent-status/1","id":"nightly-backup","title":"Nightly backup","state":"working","updated":"%s"}\n' "$now" > "$tmp"
mv -f "$tmp" "$dir/nightly-backup.json"
```

A reader only needs to list `*.json` in the folder, parse each file, check `spec`,
`state` and `updated`, and apply the stale rule.
