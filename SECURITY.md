# Security

Lamptown sits on top of your private work: your agents' sessions, your projects, your notes. We
take that seriously, and we are grateful for every report.

## Reporting a problem

**Please don't open a public issue for a security problem.** Use GitHub's private vulnerability
reporting instead: open the repository's **Security** tab and choose **Report a vulnerability**.
Only the maintainers can see the report.

Please include what you found, how to reproduce it (a small config or file is ideal, with made-up
data), and what an attacker could do with it.

What to expect: we confirm we have your report, keep you posted while we fix it, and credit you in
the release notes if you'd like. Please give us a reasonable time to ship a fix before you talk
about it in public.

Security fixes go into the newest release. Lamptown is young, so please update before reporting.

## What Lamptown reads

- **Your config**, `lamptown.toml` (see [docs/config.md](docs/config.md) for where it is looked up).
- **The sources you list in it**, read-only: log files, a notes folder, status files, skill
  folders, scan reports, contest files, and for active projects their `TASKS.md` and the newest
  line of `.git/logs/HEAD`. Collectors never write, move or delete anything in a source.
- **Never inside private projects.** A project marked `private = true`, or in or under a folder in
  `private_dirs`, is never opened, listed or read; only whether its folder exists is checked.
- **Agent hook payloads**, on stdin, when an agent runs `lamptown status hook`. The hook keeps the
  start of the session id, the project folder's name and path (`x-folder`, so `private_dirs` can
  hide it; the town never shows the path), the lamp state and one fixed line ("Working", "Turn
  finished", "Needs your permission", a tool's name). It never writes your prompts, the agent's
  replies, notification or error text, tool input, file contents or account details (such as an
  e-mail address). With `--excerpts` (or `LAMPTOWN_STATUS_EXCERPTS=1`) it also keeps the first
  line of the reply, cut to 80 characters, with anything that looks like a key, token or
  password masked. `lamptown status run` keeps the command line and the last stderr lines.

## What Lamptown writes

| Command | Writes |
|---|---|
| `lamptown build`, `open`, `app` | `town.html` and `town-details.html` in the output folder (per-user data folder by default, or `[town] out`, or `--out`), atomically. |
| `lamptown demo` | A copy of the fictional demo world and its town, in a new temp folder (or `--out`). |
| `lamptown status set`, `run`, `hook` | One `<id>.json` status file in `~/.agent-status/`, `$AGENT_STATUS_DIR` or `--dir`, atomically, at most 64 KB. |
| `lamptown status clear` | Removes old status files from that folder (`--older-than`, `--all`, `--dry-run`). Only valid `agent-status/1` files named `<id>.json`, directly in the folder, and not one that changed while it looked. |
| `lamptown app` | Its log, a lock file, `instance.json` (a port and a random token; owner-only on macOS and Linux) and the web view's profile, in a per-user state folder (`$LAMPTOWN_APP_DIR` or `--state-dir` to move them). |
| `lamptown shortcut` | One launcher: a `.lnk` on the Windows desktop, `~/Applications/Lamptown.app` on macOS, or `~/.local/share/applications/lamptown.desktop` on Linux, plus a copy of the icon. |
| `lamptown init` | A starter `lamptown.toml` in the current folder, `--dir` or (with `--user`) your config folder. It never replaces one without `--force`, and then keeps the old one as `.bak`. To find your agents it only checks whether `~/.claude`, `~/.codex`, `~/.gemini` and `~/.cursor` exist. |
| `lamptown doctor` | Nothing. To test that a folder is writable it makes one empty temp file there and removes it at once. |

The town page itself remembers three small things in the browser's `localStorage`, on that screen
only: whether sound is muted, which dropped notes the vault's cat has already eaten, and which
contests you have unblinded.

## Network

Lamptown makes **no network connections** and sends **no telemetry**. The town page is one
self-contained file: it loads no outside scripts, fonts or images.

The one socket is local: the desktop app listens on `127.0.0.1` (never a public interface) on a
random port, so a second launch can ask the first window to come to the front. A request without
the random token from `instance.json` is ignored.

Links you put in data (needs, status files, job logs) open only when you click them, and only
`https:`, `http:` and `file:` links are kept. Anything else is dropped before it reaches the page.

In the desktop app every click goes through one opener. `http:` and `https:` open in your default
browser. A `file:` link is never run or opened as a file, whatever it points at (a `.bat`, `.exe`,
`.sh` or `.lnk`): its folder opens with the file selected (Windows `explorer /select,`, macOS
`open -R`, Linux the parent folder through `xdg-open`), started with an argument list, never a
shell. `file:` links that name a host or a UNC path (`file://server/share/...`) are refused, so a
link can't make your computer log in to another machine. Any other scheme is refused. In a
browser, the browser's own rules apply.

## How data reaches the page

- Room data is checked against its room type's JSON Schema before it is used.
- Data is inlined as JSON with every `<` escaped, so no value can end the script it lives in. The
  details page escapes every value as HTML.
- A collector that crashes, hangs (10 seconds) or returns something that isn't JSON turns only its
  own building red.
- Agent hooks always exit 0 and print only what their agent expects, so they never allow, deny or
  block anything in your agent.

## Treat a config like a script

A config can run code: `collector = "./mine.py"` runs that Python file at every build, and
`room_js = "./mine.js"` puts that script into your town page, where it runs with all of the
town's data. Installed packages can add collectors and rooms through entry points. So:

- only use a `lamptown.toml`, collector or room script from someone you trust, and read it first;
- only install Lamptown plugins you would install as any other Python package.

## Keeping secrets out of this repository

Every commit, push and pull request runs the privacy gate (`tools/privacy_gate.py`: personal paths,
e-mail addresses, a private deny-list, image metadata) and gitleaks (secrets). See
[CONTRIBUTING.md](CONTRIBUTING.md#the-privacy-gate).
