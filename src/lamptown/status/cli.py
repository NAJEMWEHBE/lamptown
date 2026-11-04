"""``lamptown status``: write, wrap, hook and list agent-status/1 files.

    lamptown status set ID [--state S] [--message M] [--need TEXT] [--progress 3/8] ...
    lamptown status run --id ID [--title T] -- <command> [args...]
    lamptown status hook PROVIDER            # claude-code | codex | gemini-cli | cursor
    lamptown status list [--json]
    lamptown status clear [--older-than 7d | --all] [--dry-run]

Wiring: ``add_parser(subparsers)`` adds the ``status`` command to the main ``lamptown``
parser; every parsed namespace carries ``func(args) -> int``. This module also runs on its
own: ``python -m lamptown.status.cli list`` or ``main(["list"])``.

``hook`` never fails the agent that calls it: it always exits 0 and prints only what the
provider expects (usually nothing). Problems go to stderr as one line.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from lamptown.status import adapters
from lamptown.status.collector import DEFAULT_EXPIRE_AFTER, DEFAULT_HIDE_AFTER, scan
from lamptown.status.schema import parse_time
from lamptown.status.store import (
    DEFAULT_STALE_AFTER,
    KINDS,
    STATES,
    StatusError,
    clear_files,
    default_dir,
    iso,
    parse_duration,
    status_path,
    update_status,
    utcnow,
)

PROG = "lamptown status"


# ---------------------------------------------------------------- set
def _progress(text: str) -> dict:
    try:
        done, total = (int(x) for x in text.split("/", 1))
    except ValueError:
        raise argparse.ArgumentTypeError("use DONE/TOTAL, for example 3/8") from None
    if done < 0 or total < 0 or done > total:
        raise argparse.ArgumentTypeError("need 0 <= DONE <= TOTAL")
    return {"done": done, "total": total}


def _link(text: str) -> dict:
    label, sep, href = text.partition("=")
    if not sep or not label.strip() or not href.strip():
        raise argparse.ArgumentTypeError("use LABEL=URL, for example PR=https://example.com/pr/1")
    return {"label": label.strip(), "href": href.strip()}


def _when(text: str) -> str:
    if parse_time(text) is None:
        raise argparse.ArgumentTypeError(
            "use an ISO date-time with an offset, e.g. 2026-11-04T21:00:00Z")
    return text


def cmd_set(args: argparse.Namespace) -> int:
    changes: dict[str, Any] = {}
    for key in ("title", "message", "kind", "agent", "stale_after", "next_run"):
        value = getattr(args, key)
        if value is not None:
            changes[key] = value
    if args.progress is not None:
        changes["progress"] = args.progress
    if args.no_progress:
        changes["progress"] = None
    if args.link:
        changes["links"] = args.link
    needs = None
    if args.clear_needs:
        needs = []
    if args.need:
        hrefs = list(args.need_href or [])
        needs = [
            {"text": text, **({"href": hrefs[i]} if i < len(hrefs) and hrefs[i] else {})}
            for i, text in enumerate(args.need)
        ]
    if needs is not None:
        changes["needs_you"] = needs
    state = args.state or ("waiting" if args.need else None)
    if state:
        changes["state"] = state
    try:
        doc = update_status(args.id, args.dir, **changes)
    except StatusError as exc:
        print(f"{PROG} set: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"{PROG} set: could not write: {exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"{doc['id']}: {doc['state']} -> {status_path(args.id, args.dir)}")
    return 0


# ---------------------------------------------------------------- run
def cmd_run(args: argparse.Namespace) -> int:
    from lamptown.status.adapters.wrapper import run_command

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    try:  # only usage mistakes raise; a status folder that can't be written only warns
        return run_command(command, args.id, title=args.title, directory=args.dir,
                           kind=args.kind, agent=args.agent, stale_after=args.stale_after)
    except StatusError as exc:
        print(f"{PROG} run: {exc}", file=sys.stderr)
        return 2


# ---------------------------------------------------------------- hook
def _hook_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"{PROG} hook PROVIDER", add_help=False)
    p.add_argument("--dir")
    p.add_argument("--id")
    p.add_argument("--title")
    p.add_argument("--on-stop", default="ok")
    p.add_argument("--stale-after")
    p.add_argument("--excerpts", action="store_true")
    p.add_argument("payload", nargs="?")
    return p


def _read_stdin() -> str:
    stream = sys.stdin
    if stream is None or stream.isatty():
        return ""
    raw = getattr(stream, "buffer", None)
    data = raw.read() if raw is not None else stream.read()
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    return data.lstrip("﻿")


def cmd_hook(args: argparse.Namespace) -> int:
    """Translate one provider payload. Always returns 0 so the agent never stalls."""
    out = ""
    try:
        provider = (args.provider or "").lower()
        try:
            module = adapters.get(provider)
        except KeyError:
            names = ", ".join(adapters.PROVIDERS)
            print(f"{PROG} hook: unknown provider {provider!r} (use {names})", file=sys.stderr)
            return 0
        try:
            opts, _unknown = _hook_parser().parse_known_args(args.rest)
        except SystemExit:
            print(f"{PROG} hook: bad options {args.rest!r}", file=sys.stderr)
            return 0
        text = opts.payload if opts.payload and opts.payload.lstrip().startswith("{") else None
        text = text or _read_stdin()
        payload = json.loads(text) if text.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("payload is not a JSON object")
        out = module.respond(payload)
        on_stop = opts.on_stop if opts.on_stop in adapters.ON_STOP else "ok"
        stale = int(opts.stale_after) if opts.stale_after not in (None, "") else None
        update = module.translate(payload, on_stop,
                                  excerpts=adapters.excerpts_wanted(opts.excerpts))
        if update is not None:
            adapters.apply(update, opts.dir, id_override=opts.id, title_override=opts.title,
                           stale_after=stale)
    except Exception as exc:  # never break the calling agent
        print(f"{PROG} hook: skipped ({type(exc).__name__}: {exc})"[:300], file=sys.stderr)
    if out:
        print(out)
    return 0


# ---------------------------------------------------------------- list
def _age(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return "-"
    secs = max(0, int((now - moment).total_seconds()))
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if secs >= size:
            return f"{secs // size}{unit}"
    return f"{secs}s"


def cmd_list(args: argparse.Namespace) -> int:
    dirs = [Path(d).expanduser() for d in args.dir] if args.dir else [default_dir()]
    now = utcnow()
    records, missing = scan(dirs, now, stale_after=DEFAULT_STALE_AFTER)
    records.sort(key=lambda r: (r.updated is None, -(r.updated.timestamp() if r.updated else 0)))
    if args.json:
        rows = [{
            "id": r.id, "title": r.title, "state": r.state, "kind": r.kind, "agent": r.agent,
            "message": r.message, "updated": iso(r.updated) if r.updated else None,
            "stale": r.stale, "quiet": r.quiet, "valid": not r.invalid, "needs_you": r.needs,
            "progress": r.progress, "links": r.links, "file": str(r.path),
        } for r in records]
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    for folder in missing:
        print(f"(no folder {folder})")
    if not records:
        if not missing:
            print(f"No status files in {', '.join(map(str, dirs))}")
        return 0
    width = max(len(r.id) for r in records)
    for r in records:
        progress = f" [{r.progress['done']}/{r.progress['total']}]" if r.progress else ""
        state = "quiet" if r.quiet else r.state
        line = f"{state:<8} {r.id:<{width}}  {r.kind:<7} {_age(r.updated, now):>4}  {r.title}"
        if r.message:
            line += f": {r.message}"
        print(line + progress)
        for need in r.needs:
            print(f"{'':<8} {'':<{width}}  needs you: {need['text']}"
                  + (f" ({need['href']})" if "href" in need else ""))
    return 0


# ---------------------------------------------------------------- clear
def _duration(text: str) -> int:
    try:
        return parse_duration(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _default_rule(now: datetime):
    """What the town already leaves out: ok older than a day, anything else older than 7 days."""
    def rule(doc: dict) -> bool:
        updated = parse_time(doc.get("updated"))
        if updated is None or (doc.get("state") == "working" and doc.get("stale_after") == 0):
            return False  # a working file with stale_after = 0 never ages
        limit = DEFAULT_HIDE_AFTER if doc.get("state") == "ok" else DEFAULT_EXPIRE_AFTER
        return (now - updated).total_seconds() > limit
    return rule


def cmd_clear(args: argparse.Namespace) -> int:
    dirs = [Path(d).expanduser() for d in args.dir] if args.dir else [default_dir()]
    now = utcnow()
    if args.all:
        rule = lambda _doc: True  # noqa: E731
    elif args.older_than is not None:
        def rule(doc: dict) -> bool:
            updated = parse_time(doc.get("updated"))
            return updated is not None and (now - updated).total_seconds() > args.older_than
    else:
        rule = _default_rule(now)
    verb = "would remove" if args.dry_run else "removed"
    code = 0
    for folder in dirs:
        try:
            removed, foreign, failed = clear_files(folder, rule, dry_run=args.dry_run)
        except OSError as exc:
            print(f"{PROG} clear: can't read {folder}: {exc.strerror or exc}", file=sys.stderr)
            code = 1
            continue
        for path in removed:
            print(f"{verb} {path.name}")
        for path, why in failed:
            print(f"{PROG} clear: couldn't remove {path.name}: {why}", file=sys.stderr)
            code = 1
        note = f" (left {len(foreign)} file(s) that aren't status files alone)" if foreign else ""
        print(f"{folder}: {verb} {len(removed)} status file(s){note}")
    return code


# ---------------------------------------------------------------- parser
def _fill(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(func=lambda _args: (parser.print_help(), 0)[1])
    sub = parser.add_subparsers(dest="status_command", metavar="{set,run,hook,list,clear}")
    dir_help = "status folder (default: $AGENT_STATUS_DIR, then ~/.agent-status)"

    s = sub.add_parser("set", help="create or change one status file",
                       description="Create or change <dir>/<ID>.json. Fields not given stay.")
    s.add_argument("id", metavar="ID")
    s.add_argument("--state", choices=STATES)
    s.add_argument("--title")
    s.add_argument("-m", "--message")
    s.add_argument("--kind", choices=KINDS)
    s.add_argument("--agent", help="who writes this file, e.g. aider, cron, my-bot")
    s.add_argument("--need", action="append", metavar="TEXT",
                   help="something a person must do (repeat for more; implies --state waiting)")
    s.add_argument("--need-href", action="append", metavar="URL",
                   help="link for the need in the same position")
    s.add_argument("--clear-needs", action="store_true", help="remove every need")
    s.add_argument("--progress", type=_progress, metavar="DONE/TOTAL")
    s.add_argument("--no-progress", action="store_true", help="remove the progress field")
    s.add_argument("--link", action="append", type=_link, metavar="LABEL=URL")
    s.add_argument("--stale-after", type=int, metavar="SECONDS")
    s.add_argument("--next-run", type=_when, metavar="ISO")
    s.add_argument("--dir", help=dir_help)
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(func=cmd_set)

    r = sub.add_parser("run", help="wrap a command: working while it runs, then ok or error",
                       description="Run a command and keep a status file in step with it.")
    r.add_argument("--id", required=True)
    r.add_argument("--title")
    r.add_argument("--kind", choices=KINDS, default="task")
    r.add_argument("--agent")
    r.add_argument("--stale-after", type=int, metavar="SECONDS")
    r.add_argument("--dir", help=dir_help)
    r.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command")
    r.set_defaults(func=cmd_run)

    h = sub.add_parser(
        "hook", help="translate a provider hook payload (always exits 0)",
        description="Read one hook payload (stdin, or a JSON argument for Codex notify) and "
        "update that session's status file. Options: --dir D, --id X, --title T, "
        "--on-stop ok|waiting, --stale-after SECONDS, --excerpts (also write the first line "
        "of the agent's reply; off by default).")
    h.add_argument("provider", nargs="?", help=", ".join(adapters.PROVIDERS))
    h.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    h.set_defaults(func=cmd_hook)

    ls = sub.add_parser("list", help="show every status file and its lamp state")
    ls.add_argument("--dir", action="append", help=dir_help + "; repeat for more")
    ls.add_argument("--json", action="store_true", help="machine-readable output")
    ls.set_defaults(func=cmd_list)

    c = sub.add_parser(
        "clear", help="remove old status files (only valid agent-status files)",
        description="Remove old status files from the status folder. With no option: what the "
        "town already leaves out (ok older than 1 day, anything else older than 7 days). Only "
        "valid agent-status/1 files named <id>.json are touched; nothing below the folder.")
    which = c.add_mutually_exclusive_group()
    which.add_argument("--older-than", type=_duration, metavar="DURATION",
                       help="every status file not updated for this long (90s, 45m, 6h, 7d, 2w)")
    which.add_argument("--all", action="store_true", help="every status file")
    c.add_argument("--dry-run", action="store_true", help="only say what would go")
    c.add_argument("--dir", action="append", help=dir_help + "; repeat for more")
    c.set_defaults(func=cmd_clear)


def add_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Add ``status`` to the main parser's subparsers; returns the new parser."""
    parser = subparsers.add_parser(
        "status", help="agent-status/1 files: set, run, hook, list, clear",
        description="Write and read agent-status/1 files, the lamps of the town.")
    _fill(parser)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="Write and read agent-status/1 files.")
    _fill(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
