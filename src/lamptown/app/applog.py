"""A small capped log for the desktop app (it often runs with no console to print to).

Past ``max_bytes`` the file moves to ``<name>.old<suffix>`` and a fresh one starts, so the two
together never pass about twice the cap. Logging never raises.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import threading
from pathlib import Path

LOG_MAX = 256 * 1024


class AppLog:
    def __init__(self, path: Path | None, max_bytes: int = LOG_MAX, echo=None) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.echo = echo  # optional callable(str): also show the line (a console run)
        self._lock = threading.Lock()

    @property
    def old(self) -> Path | None:
        if not self.path:
            return None
        return self.path.with_name(self.path.stem + ".old" + self.path.suffix)

    def __call__(self, msg: str) -> None:
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
        if self.echo:
            with contextlib.suppress(Exception):
                self.echo(line)
        if not self.path:
            return
        with self._lock:
            try:
                if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                    os.replace(self.path, self.old)
            except OSError:
                pass
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
