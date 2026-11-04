"""Command-line entry point for ``lamptown``.

Subcommands so far: ``build`` (collect every source and write the town page), ``open`` (build,
then show the page in the default browser) and ``watch`` (keep the town live in a browser: the
Watcher every few seconds, a rebuild every 15 s; ``open --watch`` does both). ``status`` is
registered by :mod:`lamptown.status.cli` when that module is present, and ``demo`` (the
fictional demo world) by :mod:`lamptown.demo.cli`.
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
    if getattr(args, "watch", False):
        return _watch(args, open_page=True)
    res, code = _run_build(args)
    if res is None:
        return code
    _open_page(res.town)
    return EXIT_OK


def _open_page(town) -> None:
    url = town.resolve().as_uri()
    if not webbrowser.open(url):
        print(f"Open this in a browser: {url}")


# ----------------------------------------------------------------------------- watch
LIVE_OFF = (
    "lamptown: live is off in the config, so there is nothing to watch.\n"
    "Add a [live] table to lamptown.toml (it needs no keys; see docs/config.md), then run "
    "`lamptown watch` again."
)


def _seconds(text: str) -> float:
    try:
        v = float(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from e
    if not 0.2 <= v <= 3600:
        raise argparse.ArgumentTypeError("use between 0.2 and 3600 seconds")
    return v


def cmd_watch(args: argparse.Namespace) -> int:
    return _watch(args, open_page=False)


def _watch(args: argparse.Namespace, *, open_page: bool) -> int:
    """``lamptown watch``: one Watcher every few seconds (``[live] poll``, or ``--every``) and a
    rebuild every ``--rebuild`` seconds, writing the files next to town.html; Ctrl+C stops it
    cleanly (the Watcher saves its state and lets the status folder go)."""
    from lamptown.config import ConfigError, load_config
    from lamptown.livefeed import Builder, LiveFeed, watch_loop

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"lamptown: config problem: {e}", file=sys.stderr)
        return EXIT_CONFIG
    if not config.live.enabled:
        print(LIVE_OFF, file=sys.stderr)
        return EXIT_CONFIG

    def say(msg: str) -> None:
        if not args.quiet:
            print(msg, file=sys.stderr, flush=True)

    rebuild_every = args.rebuild or REBUILD
    with LiveFeed(log=say, poll=args.every) as feed:
        # Ctrl+C anywhere in here (the first build can take a while: its sources have up to
        # 10 s) stops cleanly; leaving the `with` saves the Watcher and lets the folder go
        try:
            builder = Builder(args.config, args.out, lambda _m: None, on_config=feed.configure)
            if builder() is None:  # the first build: said here, once, even with --quiet
                print(f"lamptown: can't build the town: {builder.error}", file=sys.stderr)
                return EXIT_WRITE
            builder.log = say  # later builds keep the last good page and say why, once
            town = builder.town
            if not args.quiet:
                print(f"Watching: {town} stays live (the Watcher every {feed.poll:g} s, a "
                      f"rebuild every {rebuild_every:g} s). Ctrl+C stops.", flush=True)
            if open_page:
                _open_page(town)
            watch_loop(feed, builder, rev=lambda: builder.rev, rebuild_every=rebuild_every,
                       ticks=args.ticks, log=say)
        except KeyboardInterrupt:
            if not args.quiet:
                print("\nStopped watching.", flush=True)
            return EXIT_OK
    if not feed.enabled:
        print("lamptown: live was switched off in the config: stopped watching.",
              file=sys.stderr)
        return EXIT_CONFIG
    return EXIT_OK


REBUILD = 15.0  # seconds between rebuilds (= livefeed.REBUILD_EVERY; not imported, stays light)
WATCH_ONLY = ("every", "rebuild", "ticks")  # `open` takes these only with --watch


def _add_watch_args(p: argparse.ArgumentParser, *, rebuild: float | None = REBUILD) -> None:
    p.add_argument("--every", type=_seconds, metavar="SECONDS",
                   help="seconds between Watcher runs (default: [live] poll, 3; the page's "
                        "heartbeat is written at least every 10 s either way)")
    p.add_argument("--rebuild", type=_seconds, default=rebuild, metavar="SECONDS",
                   help=f"seconds between full rebuilds (default {REBUILD:g})")
    p.add_argument("--ticks", type=int, help=argparse.SUPPRESS)  # stop after N runs (tests)


def _check_open(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """``open``'s flags that only mean something together (argparse can't say it)."""
    if args.watch and args.now is not None:
        parser.error("--now can't be used with --watch (a live town keeps the real time)")
    if not args.watch:
        given = [f"--{k}" for k in WATCH_ONLY if getattr(args, k, None) is not None]
        if given:
            verb = "works" if len(given) == 1 else "work"
            parser.error(f"{' and '.join(given)} only {verb} with --watch")


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
    o.add_argument("--watch", action="store_true",
                   help="then keep it live, as `lamptown watch` does (Ctrl+C stops)")
    _add_watch_args(o, rebuild=None)  # None: given or not (see _check_open)
    o.set_defaults(func=cmd_open, check_args=lambda a, p=o: _check_open(a, p))

    w = sub.add_parser(
        "watch",
        help="keep the town live in your browser (the Watcher, plus a rebuild every 15 s)",
        description="Run the live town's Watcher every few seconds and rebuild the town every "
                    "15 s, writing town-live.js and town-data.js next to town.html: an open "
                    "town page then updates itself. Needs a [live] table in lamptown.toml. "
                    "Ctrl+C stops it.",
    )
    w.add_argument("--config", metavar="FILE",
                   help="the lamptown.toml to use (default: $LAMPTOWN_CONFIG, then the per-user "
                        "one)")
    w.add_argument("--out", metavar="DIR",
                   help="where town.html is (default: [town] out, then the per-user data folder)")
    w.add_argument("-q", "--quiet", action="store_true", help="print nothing but errors")
    _add_watch_args(w)
    w.set_defaults(func=cmd_watch)

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
    check = getattr(args, "check_args", None)
    if check is not None:  # a subcommand's own rules on its flags (exits 2 with a message)
        check(args)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_OK
    return int(func(args) or 0)
