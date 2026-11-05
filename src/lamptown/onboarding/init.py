"""``lamptown init``: write a starter ``lamptown.toml``.

No questions are asked: flags choose everything, and the defaults give a working town. The only
file written is the target ``lamptown.toml`` (plus a ``.bak`` of an old one next to it with
``--force``; ``.bak.1``, ``.bak.2`` ... when older backups are there, which are never
replaced). Agent setups are found by folder existence alone
(:mod:`lamptown.onboarding.agents`); no file outside the target is opened or changed. Hooks
are printed for you to paste, never installed.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from lamptown.config import CONFIG_NAME, TITLE_MAX, ConfigError, parse_config, user_config_dir
from lamptown.onboarding.agents import AGENTS, BY_ID, STATUS_FOLDER, Found, detect

EXIT_OK, EXIT_USAGE, EXIT_EXISTS, EXIT_WRITE = 0, 2, 3, 4
DEFAULT_TITLE = "Lamptown"


def _q(text: str) -> str:
    """A TOML basic string. JSON's escapes are a subset of TOML's, but not its ``\\ud83e``
    surrogate escapes (TOML wants the character itself), so non-ASCII text is written as is;
    DEL, the one control character JSON leaves bare, is escaped; a lone surrogate half (no
    character at all) becomes U+FFFD."""
    from lamptown.collectors import clean_text  # here: registering `init` stays light

    return json.dumps(clean_text(text), ensure_ascii=False).replace("\x7f", "\\u007f")


def tilde(path: Path, home: Path | None = None) -> str:
    """``~/...`` for a path inside the home folder (as configs and messages should show it)."""
    home = Path(os.path.expanduser("~")) if home is None else home
    try:
        rel = path.resolve().relative_to(home.resolve())
    except (ValueError, OSError):
        return str(path)
    return "~" if not rel.parts else "~/" + rel.as_posix()


def tilde_text(text: str, home: Path | None = None) -> str:
    """``text`` (an error message) with every path inside the home folder written as
    :func:`tilde` writes it: ``C:\\Users\\you\\town\\lamptown.toml: ...`` -> ``~/town/...``."""
    home = Path(os.path.expanduser("~")) if home is None else home
    forms = {str(home)}
    with contextlib.suppress(OSError):
        forms.add(str(home.resolve()))
    flags = re.IGNORECASE if os.name == "nt" else 0
    for h in sorted((f.rstrip("/\\") for f in forms if len(f.rstrip("/\\")) > 3), key=len,
                    reverse=True):
        text = re.sub(re.escape(h) + r"(?![^\\/\s,:;'\"])([^\s,:;'\"]*)",
                      lambda m: "~" + m.group(1).replace("\\", "/"), text, flags=flags)
    return text


@dataclass(frozen=True)
class Plan:
    text: str  # the lamptown.toml to write
    found: Found
    buildings: tuple[str, ...]  # labels, for the summary


def render(title: str = DEFAULT_TITLE, status_dir: str = "~/" + STATUS_FOLDER,
           found: Found | None = None) -> Plan:
    """The starter config for what was found. Always valid (checked by the caller)."""
    found = found or Found((), (), False)
    names = ", ".join(a.name for a in found.agents)
    lines = [
        "# Lamptown starter config, written by `lamptown init`.",
        "# Reference: docs/config.md in the Lamptown repository. Check it with `lamptown doctor`.",
        "version = 1",
        "",
        "[town]",
        f"title = {_q(title)}",
        "# latitude = 48.1     # only for the sky; without it the sky follows your UTC offset",
        "# longitude = 11.6",
        "",
        "# ---- the boathouse: one berth per agent session or task that writes a status file",
        f"# ({'found: ' + names if names else 'any agent can: see `lamptown status --help`'})",
        "[[building]]",
        'id = "agents"',
        'type = "harbour"',
        'label = "Agents"',
        "  [[building.source]]",
        '  kind = "status_dir"',
        f"  dir = {_q(status_dir)}",
        "",
        "# ---- the clock tower: jobs you wrap in `lamptown status run --kind job --id ID -- CMD`",
        "[[building]]",
        'id = "jobs"',
        'type = "jobs"',
        'label = "Scheduled jobs"',
        "  [[building.source]]",
        '  kind = "status_dir"',
        f"  dir = {_q(status_dir)}",
    ]
    labels = ["Agents", "Scheduled jobs"]
    if found.skill_dirs:
        lines += [
            "",
            "# ---- the workshop: your skills (folders that hold a SKILL.md)",
            "[[building]]",
            'id = "skills"',
            'type = "shelf"',
            'label = "Skills"',
            "  [[building.source]]",
            '  kind = "skill_dirs"',
            "  dirs = [" + ", ".join(_q(d) for d in found.skill_dirs) + "]",
        ]
        labels.append("Skills")
    lines += [
        "",
        "# ---- more plots to fill: remove the '# ' in front of a block and point it at your files",
        "",
        "# [[building]]",
        '# id = "notes"',
        '# type = "vault"',
        "#   [[building.source]]",
        '#   kind = "markdown_vault"',
        '#   path = "~/notes"',
        '#   inbox = "inbox/*.md"',
        "",
        "# [[building]]",
        '# id = "projects"',
        '# type = "harbour"',
        '# plot = "lab"',
        '# label = "Projects"',
        "#   [[building.source]]",
        '#   kind = "projects"',
        '#   projects = [{ name = "My project", path = "~/code/my-project", status = "active" }]',
        "",
    ]
    return Plan("\n".join(lines), found, tuple(labels))


def target_file(args: argparse.Namespace) -> Path:
    if args.user:
        return user_config_dir() / CONFIG_NAME
    folder = Path(os.path.expanduser(args.dir)) if args.dir else Path.cwd()
    return folder / CONFIG_NAME


def _status_dir(args: argparse.Namespace, home: Path, config_dir: Path) -> str:
    """The status folder as the config should say it. A relative --status-dir (or
    $AGENT_STATUS_DIR) means relative to the current folder, like any path on a command line; the
    config resolves paths against its own folder, so it is rewritten: relative to the config
    folder when inside it (the pair can move together), else ``~/...``, else absolute."""
    raw = args.status_dir or os.environ.get("AGENT_STATUS_DIR", "").strip()
    if not raw:
        return "~/" + STATUS_FOLDER
    p = Path(os.path.abspath(os.path.expanduser(raw)))
    try:
        rel = p.resolve().relative_to(config_dir.resolve())
    except (ValueError, OSError):
        return tilde(p, home)
    return rel.as_posix() if rel.parts else "."


BACKUPS_MAX = 1000  # lamptown.toml.bak, then .bak.1 ... .bak.999


def backup_name(path: Path) -> Path:
    """Where the next backup of ``path`` goes: ``.bak``, else the first free ``.bak.N``. An old
    backup is never replaced (the first one holds the config the user wrote)."""
    for n in range(BACKUPS_MAX):
        p = path.with_name(path.name + ".bak" + (f".{n}" if n else ""))
        if not p.exists() and not p.is_symlink():
            return p
    raise FileExistsError(f"{path.name}.bak.1 to .bak.{BACKUPS_MAX - 1} all exist; "
                          "delete some old backups")


def _backup(path: Path) -> Path:
    """Copy ``path`` to :func:`backup_name`, created exclusively (two runs at once never write
    the same backup)."""
    for _ in range(BACKUPS_MAX):
        dest = backup_name(path)
        try:
            with open(path, "rb") as src, open(dest, "xb") as out:
                shutil.copyfileobj(src, out)
        except FileExistsError:
            continue  # taken between the look and the open: take the next one
        shutil.copystat(path, dest)
        return dest
    raise FileExistsError(f"can't find a free backup name next to {path.name}")


def _write(path: Path, text: str, force: bool) -> Path | None:
    """Write atomically; with ``force`` keep the old file as a backup (:func:`backup_name`).
    Returns that backup.

    The old file is copied (not moved) before the new one replaces it in one step, so a failed
    write leaves the old config where it was."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    backup = None
    try:
        with open(tmp, "x", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if path.exists() and force:
            backup = _backup(path)  # review-fix C4: never over an older backup
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return backup


def print_hooks(provider: str) -> int:
    agent = BY_ID.get(provider)
    if agent is None:
        print(f"lamptown init: unknown agent {provider!r} (known: {', '.join(BY_ID)})",
              file=sys.stderr)
        return EXIT_USAGE
    # the how-to goes to stderr, so `lamptown init --hooks cursor > hooks.json` stays clean
    print(f"{agent.name}: {agent.how}: {agent.hook_file}", file=sys.stderr)
    sys.stdout.write(agent.snippet)
    return EXIT_OK


def next_steps(target: Path, plan: Plan, default_lookup: bool) -> list[str]:
    cfg = "" if default_lookup else f" --config {_shell(_short(target))}"
    out = ["", "Next steps:"]
    n = 1
    if plan.found.agents:
        out.append(f"  {n}. Light a lamp for each agent (Lamptown never edits these files):")
        for a in plan.found.agents:
            out.append(f"       {a.name}: run `lamptown init --hooks {a.id}` and paste what it "
                       f"prints into {a.hook_file}")
        n += 1
    out.append(f"  {n}. Any other tool or job: lamptown status run --id my-job -- <command>")
    others = [a.id for a in AGENTS if a not in plan.found.agents]
    if others:
        out.append(f"     Hooks for the others: lamptown init --hooks NAME "
                   f"(NAME: {', '.join(others)})")
    out.append(f"  {n + 1}. Check everything: lamptown doctor{cfg}")
    out.append(f"  {n + 2}. See the town: lamptown open{cfg}   (or `lamptown app` for a window)")
    if not default_lookup:
        out.append(f"     Tip: `lamptown init --user` puts it where Lamptown looks without "
                   f"--config ({tilde(user_config_dir() / CONFIG_NAME)}).")
    return out


def _short(p: Path) -> Path:
    """``p`` relative to the current folder when it is inside it (shorter commands to copy)."""
    try:
        return p.resolve().relative_to(Path.cwd().resolve())
    except (ValueError, OSError):
        return p


def _shell(p: Path) -> str:
    s = str(p)
    return f'"{s}"' if " " in s else s


def cmd_init(args: argparse.Namespace) -> int:
    if args.hooks:
        return print_hooks(args.hooks)
    if args.title is not None and not (0 < len(args.title.strip()) <= TITLE_MAX):
        print(f"lamptown init: --title must be 1-{TITLE_MAX} characters", file=sys.stderr)
        return EXIT_USAGE
    home = Path(os.path.expanduser("~"))
    found = Found((), (), False) if args.no_detect else detect(home)
    target = target_file(args)
    plan = render(args.title.strip() if args.title else DEFAULT_TITLE,
                  _status_dir(args, home, target.parent), found)
    try:
        parse_config(plan.text, target)  # never write a config Lamptown would refuse
    except ConfigError as e:  # pragma: no cover - a bug in render(), not a user error
        print(f"lamptown init: the starter config is broken ({e}); please report this",
              file=sys.stderr)
        return EXIT_WRITE
    if args.print:
        sys.stdout.write(plan.text)
        return EXIT_OK
    if target.is_dir():
        print(f"lamptown init: {target} is a folder", file=sys.stderr)
        return EXIT_WRITE
    if target.exists() and not args.force:
        try:
            kept = f"the old file is kept as {backup_name(target).name}"
        except OSError:
            kept = "first delete some old backups next to it"
        print(f"lamptown init: {target} already exists. Use --force to replace it ({kept}), "
              "or --print to see the new one.", file=sys.stderr)
        return EXIT_EXISTS
    try:
        backup = _write(target, plan.text, args.force)
    except OSError as e:
        print(f"lamptown init: can't write {target}: {e.strerror or e}", file=sys.stderr)
        return EXIT_WRITE
    n = len(plan.buildings)
    print(f"Wrote {target} ({n} buildings: {', '.join(plan.buildings)}).")
    if backup:
        print(f"The old file is now {backup}.")
    if args.no_detect:
        print("Agent detection was off (--no-detect).")
    elif found.agents:
        print("Found " + ", ".join(f"{a.name} (~/{a.folder})" for a in found.agents)
              + ". Only folder names were checked; nothing inside them was read.")
    else:
        print("No known agent folders found (~/.claude, ~/.codex, ~/.gemini, ~/.cursor).")
    try:
        default_lookup = target.resolve() == (user_config_dir() / CONFIG_NAME).resolve()
    except OSError:
        default_lookup = False
    print("\n".join(next_steps(target, plan, default_lookup)))
    return EXIT_OK


def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "init",
        help="write a starter lamptown.toml",
        description="Write a starter lamptown.toml in the current folder (or --dir, or --user "
                    "for the per-user config folder). Agent setups are found only by checking "
                    "whether ~/.claude, ~/.codex, ~/.gemini and ~/.cursor exist; nothing inside "
                    "them is read or changed. No questions are asked.",
    )
    where = p.add_mutually_exclusive_group()
    where.add_argument("--dir", metavar="DIR",
                       help="write DIR/lamptown.toml (default: the current folder)")
    where.add_argument("--user", action="store_true",
                       help="write it to the per-user config folder, where Lamptown looks "
                            "without --config")
    p.add_argument("--title", metavar="TEXT", help=f"the town's name (default {DEFAULT_TITLE})")
    p.add_argument("--status-dir", metavar="DIR",
                   help="the agent-status folder to show (default $AGENT_STATUS_DIR, then "
                        "~/.agent-status). A relative DIR is taken from the current folder and "
                        "written relative to the config's folder (or as ~/... or an absolute "
                        "path when outside it), so it still points at the same place")
    p.add_argument("--no-detect", action="store_true",
                   help="don't look for agent folders; write the plain starter")
    p.add_argument("--force", action="store_true",
                   help="replace an existing lamptown.toml (the old one is kept as .bak, or "
                        ".bak.1, .bak.2 ... when older backups are there)")
    p.add_argument("--print", action="store_true",
                   help="print the config instead of writing it")
    p.add_argument("--hooks", metavar="AGENT", choices=tuple(BY_ID),
                   help="print the hook block for one agent and stop: " + ", ".join(BY_ID))
    p.set_defaults(func=cmd_init)
    return p
