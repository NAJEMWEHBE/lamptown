"""``lamptown demo``: build the fictional demo world and open it.

Registered by :mod:`lamptown.cli` through :func:`add_parser`.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from lamptown.demo import STATES, DemoError, build_demo

EXIT_OK, EXIT_USAGE, EXIT_WRITE = 0, 2, 3


def cmd_demo(args: argparse.Namespace) -> int:
    try:
        res = build_demo(args.out, now=args.now, state=args.state)
    except DemoError as e:
        print(f"lamptown: demo: {e}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as e:
        print(f"lamptown: demo: can't write the demo world: {e}", file=sys.stderr)
        return EXIT_WRITE
    url = res.town.resolve().as_uri()
    if not args.quiet:
        how = f" (every lamp: {args.state})" if args.state else ""
        print(f"Demo world{how} in {res.root}")
        print(f"Built {res.town} in {res.seconds:.1f} s. Every name and number in it is made up.")
    if args.no_open:
        if not args.quiet:
            print(f"Open it with: {url}")
        return EXIT_OK
    if not webbrowser.open(url):
        print(f"Open this in a browser: {url}")
    return EXIT_OK


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    from lamptown.cli import _now  # the shared --now parser (lamptown.cli is loaded by now)

    p = sub.add_parser(
        "demo",
        help="build a made-up town and open it (no setup needed)",
        description=(
            "Copy Lamptown's fictional demo world (agents, projects, jobs, notes, tools and a "
            "contest) into a new folder, date it from now, build its town and open it."
        ),
    )
    p.add_argument(
        "--state",
        choices=STATES,
        help="make every building show this state (its files are changed to match)",
    )
    p.add_argument("--no-open", action="store_true", help="build only; print where the town is")
    p.add_argument(
        "--out",
        metavar="DIR",
        help="an empty or new folder for the world and the town (default: a new temp folder)",
    )
    p.add_argument(
        "--now",
        type=_now,
        metavar="ISO",
        help="pretend it is this time: the world is dated from it and the page clock stays on it",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="print nothing but errors")
    p.set_defaults(func=cmd_demo)
    return p
