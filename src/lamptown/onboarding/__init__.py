"""Getting started: ``lamptown init`` (a starter config) and ``lamptown doctor`` (setup checks).

The main CLI registers both through :func:`add_parser`. Both modules are imported here, so a
broken one fails the main CLI's guarded import instead of the whole command line.
"""

from __future__ import annotations

from lamptown.onboarding.doctor import add_parser as _add_doctor
from lamptown.onboarding.init import add_parser as _add_init


def add_parser(subparsers) -> None:
    """Register ``init`` and ``doctor`` on the main CLI's subparsers."""
    _add_init(subparsers)
    _add_doctor(subparsers)
