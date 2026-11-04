"""The ``lamptown app`` and ``lamptown shortcut`` commands.

The main CLI registers them with :func:`add_parser`; ``python -m lamptown.app`` runs the app on its
own (that is what the shortcuts start).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from lamptown.app.live import POLL


def _seconds(text: str) -> float:
    try:
        v = float(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from e
    if not 0.2 <= v <= 86400:
        raise argparse.ArgumentTypeError("use between 0.2 and 86400 seconds")
    return v


def add_app_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", metavar="FILE",
                   help="the lamptown.toml to use (default: $LAMPTOWN_CONFIG, then the per-user "
                        "one)")
    p.add_argument("--out", metavar="DIR",
                   help="where to write town.html (default: [town] out, then the per-user data "
                        "folder)")
    p.add_argument("--poll", type=_seconds, default=POLL, metavar="SECONDS",
                   help=f"seconds between rebuilds while the window shows (default {POLL:g})")
    p.add_argument("--state-dir", metavar="DIR",
                   help="keep the app's log, lock and web view profile in this folder instead of "
                        "the per-user one ($LAMPTOWN_APP_DIR does the same)")


def cmd_app(args: argparse.Namespace) -> int:
    from lamptown.app.window import run_app

    return run_app(args.config, args.out, poll=args.poll, state_dir=args.state_dir)


def cmd_shortcut(args: argparse.Namespace) -> int:
    from lamptown.app.shortcut import ShortcutError, make_shortcut

    if args.config and not Path(args.config).expanduser().is_file():
        print(f"lamptown: no config file at {args.config}", file=sys.stderr)
        return 2
    try:
        path = make_shortcut(args.dir, config=args.config, name=args.name)
    except (ShortcutError, OSError) as e:
        print(f"lamptown: can't make the shortcut: {e}", file=sys.stderr)
        return 3
    print(f"Shortcut: {path}")
    if sys.platform == "darwin":
        print("Open it from Applications (the first start may ask you to confirm).")
    elif sys.platform != "win32":
        print("Lamptown is now in your desktop's app menu.")
    return 0


def add_parser(subparsers) -> None:
    """Register ``app`` and ``shortcut`` on the main CLI's subparsers."""
    a = subparsers.add_parser(
        "app",
        help="open the town in its own window, kept live",
        description="Open the town in a desktop window that keeps itself live: it rebuilds the "
                    "town every --poll seconds and swaps the new data in place, and with [live] "
                    "on it runs the Watcher every few seconds and counts what needs you in the "
                    "window title. Needs the [app] extra (pywebview); without it the town opens "
                    "in your browser. A second launch brings the open window to the front.",
    )
    add_app_args(a)
    a.set_defaults(func=cmd_app)

    s = subparsers.add_parser(
        "shortcut",
        help="make a desktop shortcut / app-menu entry for the app",
        description="Windows: a .lnk on the desktop. macOS: Lamptown.app in ~/Applications. "
                    "Linux: a .desktop entry in your app menu (with a PNG icon).",
    )
    s.add_argument("--dir", metavar="DIR", help="put the shortcut in this folder instead")
    s.add_argument("--config", metavar="FILE",
                   help="make the shortcut open the app with this lamptown.toml")
    s.add_argument("--name", default="Lamptown", help="the shortcut's name (default Lamptown)")
    s.set_defaults(func=cmd_shortcut)


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m lamptown.app``: the app alone."""
    p = argparse.ArgumentParser(prog="python -m lamptown.app",
                                description="The Lamptown desktop app.")
    add_app_args(p)
    return cmd_app(p.parse_args(argv))
