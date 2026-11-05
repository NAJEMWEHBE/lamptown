# Contributing to Lamptown

Thank you for helping. Bug reports, new collectors, new rooms, adapters for more agents, and fixes
to these docs are all welcome. This page covers the setup, the checks every change must pass, and
the house rules that keep the town fast, private and consistent.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- **Small fix?** Open a pull request directly.
- **New room, new built-in collector, or a change to `agent-status/1`?** Open an issue first, so
  we can agree on the shape before you draw a single pixel. Most new rooms and collectors are best
  shipped as their own package through the `lamptown.rooms` and `lamptown.collectors` entry points,
  and need no change here at all.
- **Security problem?** Don't open a public issue. See [SECURITY.md](SECURITY.md).

## Dev setup

You need Python 3.11 or newer and git. Chrome, Chromium or Edge is optional (for the browser
tests).

```sh
git clone <your fork's URL> lamptown
cd lamptown
python -m venv .venv
. .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -e ".[dev,app]"
pre-commit install
```

`pre-commit install` sets up three git hooks: the privacy gate and gitleaks on every commit, the
gate on every commit message and identity, and the gate on everything a push would send (see
[the privacy gate](#the-privacy-gate)).

**Or use the repo's own hooks** (maintainers do): `git config core.hooksPath tools/hooks`. git
then skips the hooks `pre-commit install` sets up, and pre-commit refuses to install while that
setting is on, so `tools/hooks/` holds all three: `pre-commit` and `commit-msg` run pre-commit's
stages when pre-commit is installed, otherwise the gate directly (over the staged version of each
file) and gitleaks if it is installed (with a notice if not). `pre-push` runs the gate over
everything the push sends and **requires the private deny-list**: point `LAMPTOWN_DENYLIST` at it,
or, if you have none, push with `LAMPTOWN_NO_DENYLIST=1` (every other check still runs).

The `[app]` extra (pywebview) is only needed for the desktop window and its tests. The core uses
the standard library only: please keep `dependencies` in `pyproject.toml` empty.

## Run the checks

```sh
python -m pytest                     # every test
ruff check .                         # lint
python tools/privacy_gate.py         # the privacy gate over the working tree
lamptown demo --no-live              # opens the demo town and returns: look at it
```

CI runs the same on Windows, macOS and Linux. A pull request needs all of them green.

### The tests

| Kind | Files | Needs |
|---|---|---|
| data | `test_config.py`, `test_build.py`, `test_collectors.py`, `test_status_*.py`, the data half of `test_room_*.py` | nothing |
| browser | `test_town.py`, `test_truth.py`, the browser half of `test_room_*.py` | Chrome, Chromium or Edge |
| app | `test_app_*.py` | a fake window (always runs); the real-window smoke test needs pywebview and a display |
| privacy | `test_privacy_gate.py` | nothing (it plants its own fake terms) |

**How the browser tests find a browser.** They use `$CHROME` when it is set (a path or a command
name). Otherwise they look in the usual install places for Chrome, Chromium and Edge on each OS,
then for `google-chrome`, `chromium`, `chromium-browser`, `chrome`, `microsoft-edge` or `msedge` on
your `PATH`. With no browser found they are skipped, with the reason in the test summary; set
`LAMPTOWN_REQUIRE_BROWSER=1` to make them fail instead (CI does). They drive the browser headless
with `--dump-dom`, so no extra Python package is needed. `lamptown doctor` tells you which browser
they will use.

```sh
CHROME=/usr/bin/chromium python -m pytest tests/test_town.py
python -m pytest -m "not browser and not app"     # the core only: no browser, no window
```

**The real-window smoke test** (`test_app_window.py`) runs only when pywebview and a display are
there. On Linux without a desktop, run it under `xvfb-run -a python -m pytest tests/test_app_window.py`.
On macOS it is opt-in for now: set `LAMPTOWN_APP_SMOKE=1`. Set `LAMPTOWN_SHOT_DIR` to keep its
screenshots.

**The town truth test** (`test_truth.py`) checks the town's promise: every moving thing follows
the data. If you add something that moves, add it to that test.

## The privacy gate

Lamptown is built to sit on top of private work, so the repository itself must never hold any.
`tools/privacy_gate.py` blocks:

1. absolute personal paths on any OS (a home folder with a real user name in it, in Windows,
   macOS, Linux, Git Bash, WSL, network-share `\\PC\Users\...`, macOS volume
   `/Volumes/<disk>/Users/...` or URL-encoded form), and drive-letter paths (also in WSL or
   Cygwin form, `/mnt/<drive>/...`). Write `~/...`, or the placeholder user `you`;
2. e-mail addresses, except `example.com`-style test domains and noreply addresses. Commit, tag
   and push identities are stricter: the e-mail must be a noreply address (your GitHub one, see
   below) or an `example.com`/`.invalid`/`.test` one; a bare login, `you@YOUR-PC` or git's
   `you@host.(none)` fail;
3. terms from the maintainers' private deny-list (in file contents, file names, commit messages and
   identities, ref names, annotated tags, and image metadata). A folder on a drive matches in every
   spelling: `<drive>:\...`, `<drive>:/...` in either case, URL-encoded, Git Bash `/<drive>/...`,
   WSL `/mnt/<drive>/...` and Cygwin;
4. images outside `docs/media/`, and images that are not PNG or GIF. The one exception is the
   desktop app's icons, directly in `src/lamptown/app/assets/` as `.png`, `.ico` or `.icns`
   (`tools/make_icon.py` makes them; every frame must be a PNG so its metadata is checked too);
5. secrets (gitleaks).

Checks 1 to 3 cover file names as well as file contents, and a push is checked down to its ref
names and annotated tags (tagger and message).

**You don't need the deny-list.** Without it the gate skips check 3 and runs every other check
(with `tools/hooks/pre-push`, push with `LAMPTOWN_NO_DENYLIST=1`). The list is kept outside the
repository and is never shown: a hit names a line number, never the term.

**Old fixtures.** A few test fixtures already in the public history plant invented paths in forms
the gate learned later. `tools/privacy_gate_baseline.txt` lists those exact hits (file content,
line and check), so `--history` passes without rewriting history. It never covers a changed file,
a file name, an identity or a deny-list term, and an entry only counts once its file content is
already on `origin/main` (CI checks against the pull request's target branch with
`--baseline-base`). So an entry can't silence a hit in a file your change adds: write new fixtures
with the placeholder user `you`.

**Your pull request is checked too.** CI runs the gate over the tree and the full history on every
pull request. Pull requests from forks get no repository secrets, so CI runs every check except the
deny-list there; a maintainer runs the full gate, with the deny-list, before merging. If it finds
something, we'll tell you which file and line to change.

**Fixtures are fictional.** Every sample, test fixture and screenshot uses made-up names, places
and data (the demo world's agents are birds and animals; its projects are "Paper Kite", "Moss
Engine" and "Tidebook"). Never paste real logs, real paths, real project names or real
screenshots, even your own. Use `tmp_path` in tests and `~` in docs.

Use your GitHub noreply address for commits (GitHub, Settings, Emails) so your own e-mail stays
out of the history.

## House rules

### Collectors

- Read-only, always. A collector never writes, moves or deletes anything.
- Standard library only for built-ins.
- Return absolute ISO times with an offset; never "3 h ago".
- Take `now` from the argument, never from the clock.
- Fail small: a source that can't be read returns `state: "error"` with a short reason.
- Respect privacy flags: anything marked private is never opened.

The full contract: [docs/collectors.md](docs/collectors.md).

### Rooms

- **Everything that moves stands for data.** No idle animation that could be mistaken for work.
- **State is an object, not only a colour**, so a room reads without colour too.
- **Use the kit** `K`: one pixel font, the five state colours, whole pixels, the shared outline.
  No images, no outside fonts or scripts: all art is drawn in code.
- **CPU budget.** The town caps itself at 20 fps and stops while hidden. A room should draw a frame
  in a few milliseconds on an ordinary laptop (aim for under 5 ms). Build static parts once with
  `K.layer`, cache text with `K.textL`, and don't allocate in `draw`. In still mode, return
  `busy()` true only while something must move.
- **Every state, every scale.** Give the room a `?demo=1` mode with fictional sample data and
  `&state=` for each state, and decide what happens at 1, 8 and 100 rows.
- **Accessible.** Everything you can click is a `targets()` entry with a `label`.

The interface: [docs/rooms.md](docs/rooms.md#bring-your-own-room) and [docs/kit-k.md](docs/kit-k.md).

### Adding a built-in collector

1. Write `src/lamptown/collectors/<name>.py` with `collect(paths, now)` and a docstring that shows
   its TOML.
2. Register its kind in `BUILTIN` in `src/lamptown/collectors/__init__.py`.
3. Add fictional fixtures and tests (odd inputs, a missing source, a crash, absolute times).
4. Document its keys in [docs/config.md](docs/config.md#built-in-source-kinds).

### Adding a built-in room

The v1 town has six fixed plots, one per built-in room type, so a new built-in room type needs a
plot, which comes with the generated map planned for v2. Until then, ship new rooms as packages
(`lamptown.rooms`), or improve the six. A room change touches:

- `src/lamptown/web/rooms/<type>.js`, the room itself, with its header comment kept up to date
  (data, states, scale, demo);
- `src/lamptown/schemas/rooms/<type>.schema.json`, when the data changes;
- `src/lamptown/demo/world/`, so the demo shows it;
- `tests/test_room_<type>.py`, data and browser tests for every state;
- [docs/rooms.md](docs/rooms.md);
- the README media, through `python tools/make_media.py` (needs `pip install -e ".[media]"` and a
  browser). Maintainers can also run the "Regenerate media" workflow from the Actions tab, which
  opens a pull request with the new images. `tests/test_media_fresh.py` (a browser test) fails
  when `town.png` or `live-town.png` no longer match a fresh render; it compares only on the OS
  named in `docs/media/made-on.txt`, the one that made them.

A release bumps `__version__` in `src/lamptown/__init__.py` and the top `## X.Y.Z` heading of
CHANGELOG.md together, then tags that commit `vX.Y.Z`: `tests/test_release_consistency.py` checks
all three (the tag on CI's tag build) and fails on a heading that still says "Unreleased".
Between releases, the top heading names the next version, `## X.Y.Z (Unreleased)`, and
`__version__` is already bumped to it: a bare `## Unreleased` fails the same test on every push.

### Style

- Python: `ruff check .` clean, line length 100, type hints on public functions, `pathlib` for
  paths, no OS-only calls without a portable fallback.
- JavaScript: plain browser JS, `'use strict'`, no build step, no dependencies.
- Docs and messages: short sentences, plain words, concrete examples. The UI's words are upper case
  in the pixel font; the docs use the same state words everywhere: running fine, running now, needs
  you, something is wrong, can't see it.

## Commits and pull requests

- One topic per commit. Subject line: what changes, in plain words, under about 70 characters
  (`Harbour: show a paused project's note on hover`). Prefix with the area when it helps (`Shelf:`,
  `Status:`, `Docs:`).
- The body says why, and anything a reviewer should look at first.
- A pull request says what changed, how you checked it (tests, a screenshot of the room in each
  state you touched), and links its issue.
- Visible changes need screenshots or a short GIF, made from the demo world.

Thank you for making the town better.
