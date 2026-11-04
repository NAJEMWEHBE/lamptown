"""``lamptown demo``: build the fictional demo world, open it and keep its live town live.

Registered by :mod:`lamptown.cli` through :func:`add_parser`. After opening the town it runs
the same loop as ``lamptown open --watch`` on the demo world (a Watcher with no readers: it
keeps ``town-live.js`` fresh, so the page shows the agents), until Ctrl+C. ``--no-open``,
``--no-live``, ``--now`` and ``--state missing`` build (and open) only.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from lamptown.demo import CONFIG_NAME, STATES, DemoError, build_demo

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
    config = res.world / CONFIG_NAME
    if not args.quiet:
        how = f" (every lamp: {args.state})" if args.state else ""
        print(f"Demo world{how} in {res.root}")
        print(f"Built {res.town} in {res.seconds:.1f} s. Every name and number in it is made up.")
    # the live town shows its agents only while a Watcher keeps town-live.js fresh (the page's
    # 30 s rule): so the demo keeps watching, as `lamptown open --watch` does, until Ctrl+C
    # (the missing state has no status folder: a Watcher would make one, and the next rebuild
    # would light the harbour)
    live = not (args.no_open or args.no_live or args.now is not None or args.state == "missing")
    if not live:
        if not args.quiet:
            if args.no_open:
                print(f"Open it with: {url}")
            # only where a Watcher fits: a pinned --now world would show every agent stopped
            # responding or gone, and the missing state would get a status folder
            if args.now is None and args.state != "missing":
                print("Its live town (the Lamplighters and the lighthouse) shows while a Watcher "
                      f'runs: lamptown watch --config "{config}" --out "{res.town.parent}"')
        if args.no_open:
            return EXIT_OK
        if not webbrowser.open(url):
            print(f"Open this in a browser: {url}")
        return EXIT_OK
    from lamptown.cli import _watch

    watch = argparse.Namespace(config=str(config), out=str(res.town.parent), quiet=args.quiet,
                               every=None, rebuild=None, ticks=args.ticks)
    return _watch(watch, open_page=True)


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    from lamptown.cli import _now  # the shared --now parser (lamptown.cli is loaded by now)

    p = sub.add_parser(
        "demo",
        help="build a made-up town and open it (no setup needed)",
        description=(
            "Copy Lamptown's fictional demo world (agents, projects, jobs, notes, tools, a "
            "contest and a week of live-town history) into a new folder, date it from now, "
            "build its town and open it. Then it keeps the town live, as `lamptown open "
            "--watch` does, until Ctrl+C."
        ),
    )
    p.add_argument(
        "--state",
        choices=STATES,
        help="make every building show this state (its files are changed to match)",
    )
    p.add_argument("--no-open", action="store_true",
                   help="build only; print where the town is (and how to keep it live)")
    p.add_argument("--no-live", action="store_true",
                   help="open the town and stop: no Watcher, so its agents show for 30 s only")
    p.add_argument(
        "--out",
        metavar="DIR",
        help="an empty or new folder for the world and the town (default: a new temp folder)",
    )
    p.add_argument(
        "--now",
        type=_now,
        metavar="ISO",
        help="pretend it is this time: the world is dated from it and the page clock stays on "
             "it (no Watcher: a live town keeps the real time)",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="print nothing but errors")
    p.add_argument("--ticks", type=int, help=argparse.SUPPRESS)  # stop after N live ticks (tests)
    p.set_defaults(func=cmd_demo)
    return p
