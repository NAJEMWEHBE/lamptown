"""Command-line entry point for ``lamptown``.

Subcommands so far: ``build`` (collect every source and write the town page) and ``open`` (build,
then show the page in the default browser). ``status`` is registered by :mod:`lamptown.status.cli`
when that module is present, and ``demo`` (the fictional demo world) by :mod:`lamptown.demo.cli`.
``app`` and ``shortcut`` come from :mod:`lamptown.app.cli`, and ``init`` and ``doctor`` from
:mod:`lamptown.onboarding`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import webbrowser
from collections.abc import Sequence

from lamptown import __version__

EXIT_OK, EXIT_CONFIG, EXIT_WRITE = 0, 2, 3


def _now(text: str) -> dt.datetime:
    """``--now``: an ISO time; without a zone it is read as local time."""
    try:
        t = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not an ISO time like 2026-01-02T03:04"
        ) from e
    return t if t.tzinfo else t.astimezone()


def _add_build_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--config",
        metavar="FILE",
        help="the lamptown.toml to use (default: $LAMPTOWN_CONFIG, then the per-user one)",
    )
    p.add_argument(
        "--out",
        metavar="DIR",
        help="where to write town.html (default: [town] out, then the per-user data folder)",
    )
    p.add_argument(
        "--now",
        type=_now,
        metavar="ISO",
        help="pretend it is this time (screenshots and tests); the page clock counts from it",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="print nothing but errors")


def _run_build(args: argparse.Namespace):
    from lamptown.build import build
    from lamptown.config import ConfigError, load_config

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"lamptown: config problem: {e}", file=sys.stderr)
        return None, EXIT_CONFIG
    try:
        res = build(
            config,
            out_dir=args.out,
            now=args.now,
            log=None if args.quiet else (lambda w: print(f"warning: {w}", file=sys.stderr)),
        )
    except OSError as e:
        print(f"lamptown: can't write the town: {e}", file=sys.stderr)
        return None, EXIT_WRITE
    if not args.quiet:
        n = len(config.buildings)
        where = (
            f"from {config.path}"
            if config.path
            else "with no config file (every plot an empty lot)"
        )
        print(f"Built {res.town} ({n} building{'s' if n != 1 else ''}, {where}).")
    return res, EXIT_OK


def cmd_build(args: argparse.Namespace) -> int:
    return _run_build(args)[1]


def cmd_open(args: argparse.Namespace) -> int:
    res, code = _run_build(args)
    if res is None:
        return code
    url = res.town.resolve().as_uri()
    if not webbrowser.open(url):
        print(f"Open this in a browser: {url}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lamptown",
        description="A pixel-art town whose lamps show the live state of your AI agents.",
    )
    parser.add_argument("--version", action="version", version=f"lamptown {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    b = sub.add_parser(
        "build",
        help="collect every source and write town.html",
        description="Run every source in lamptown.toml and write town.html and town-details.html.",
    )
    _add_build_args(b)
    b.set_defaults(func=cmd_build)

    o = sub.add_parser(
        "open",
        help="build, then open the town in your browser",
        description="Build the town, then open town.html in the default browser.",
    )
    _add_build_args(o)
    o.set_defaults(func=cmd_open)

    # ---- HOOK: the `status` subcommand group (agent-status/1) registers itself here through
    # lamptown.status.cli:add_parser(subparsers) when that module imports; otherwise the CLI
    # simply has no `status` command.
    try:
        from lamptown.status.cli import add_parser as add_status_parser
    except Exception:  # not installed, or broken: the other commands must still work
        add_status_parser = None
    if add_status_parser is not None:
        add_status_parser(sub)

    # ---- HOOK: `demo` (the fictional demo world) registers itself the same way.
    try:
        from lamptown.demo.cli import add_parser as add_demo_parser
    except Exception:  # a broken demo must not take the other commands down with it
        add_demo_parser = None
    if add_demo_parser is not None:
        add_demo_parser(sub)

    # ---- HOOK: `app` and `shortcut` (the desktop window) register themselves the same way.
    try:
        from lamptown.app.cli import add_parser as add_app_parser
    except Exception:  # the window is optional: without it the browser commands still work
        add_app_parser = None
    if add_app_parser is not None:
        add_app_parser(sub)

    # ---- HOOK: `init` and `doctor` (getting started) register themselves the same way.
    try:
        from lamptown.onboarding import add_parser as add_onboarding_parser
    except Exception:  # a broken helper must not take the other commands down with it
        add_onboarding_parser = None
    if add_onboarding_parser is not None:
        add_onboarding_parser(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_OK
    return int(func(args) or 0)
