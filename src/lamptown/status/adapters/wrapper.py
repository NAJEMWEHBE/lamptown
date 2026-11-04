"""The generic wrapper: ``lamptown status run --id X -- <command>``.

Works for any agent or script without hooks (Aider, cron jobs, build scripts...): the file
reads ``working`` while the command runs (refreshed by a heartbeat, so a killed wrapper goes
stale instead of glowing forever), ``ok`` on exit code 0, and ``error`` with the exit code
and the last lines of stderr otherwise. Output passes through unchanged; the exit code is
returned unchanged (a child killed by signal N gives ``128 + N``, as a shell would).

The lamp never gets in the command's way: if the status folder can't be written, the wrapper
says so once on stderr and runs the command anyway, with the same exit code.

Ctrl+C reaches the command itself (it shares the console), and the wrapper waits for it to
finish. A second Ctrl+C, or a command that hasn't stopped 10 s after the first, is ended by
the wrapper (exit 130).
"""

from __future__ import annotations

import collections
import contextlib
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from typing import Any

from lamptown.status.adapters import folder_path, one_line
from lamptown.status.store import (
    DEFAULT_STALE_AFTER,
    StatusError,
    check_id,
    iso,
    update_status,
    utcnow,
)

TAIL_LINES = 5
INTERRUPT_GRACE = 10.0  # seconds the command gets to stop by itself after a Ctrl+C
EXIT_INTERRUPTED = 130


def _pump(stream, tail: collections.deque) -> None:
    """Copy the child's stderr through to ours while keeping its last lines."""
    raw_out = getattr(sys.stderr, "buffer", None)
    for raw in iter(stream.readline, b""):
        text = raw.decode("utf-8", errors="replace")
        try:
            if raw_out is not None:
                raw_out.write(raw)
                raw_out.flush()
            else:
                sys.stderr.write(text)
        except (OSError, ValueError, TypeError):
            pass
        if text.strip():
            tail.append(text.strip())
    stream.close()


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def exit_code(returncode: int) -> int:
    """A shell's view of a child's exit: killed by signal N (``-N`` from Popen) -> ``128 + N``."""
    return 128 - returncode if returncode < 0 else returncode


class _Lamp:
    """Status writes that can fail without consequence: one warning on stderr, then quiet."""

    def __init__(self, status_id: str, directory: str | os.PathLike | None) -> None:
        self.status_id = status_id
        self.directory = directory
        self.warned = False
        self.lock = threading.Lock()

    def write(self, **changes: Any) -> bool:
        try:
            with self.lock:
                update_status(self.status_id, self.directory, **changes)
            return True
        except (OSError, StatusError) as exc:
            if not self.warned:
                self.warned = True
                with contextlib.suppress(Exception):
                    print(f"lamptown status run: can't update the status file ({exc}); "
                          "the command runs anyway", file=sys.stderr, flush=True)
            return False


def _wait(proc: subprocess.Popen) -> tuple[int, bool]:
    """Wait for the child. Returns (exit code, interrupted)."""
    try:
        return exit_code(proc.wait()), False
    except KeyboardInterrupt:
        pass
    # The child got the same Ctrl+C (same console / process group): let it stop by itself.
    try:
        return exit_code(proc.wait(timeout=INTERRUPT_GRACE)), True
    except (KeyboardInterrupt, subprocess.TimeoutExpired):
        pass
    with contextlib.suppress(OSError):
        proc.terminate()
    while True:
        try:
            proc.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                proc.kill()
        except KeyboardInterrupt:
            with contextlib.suppress(OSError):
                proc.kill()
    return EXIT_INTERRUPTED, True


def run_command(
    command: Sequence[str],
    status_id: str,
    *,
    title: str | None = None,
    directory: str | os.PathLike | None = None,
    kind: str = "task",
    agent: str | None = None,
    stale_after: int | None = None,
    heartbeat: float | None = None,
) -> int:
    """Run ``command`` and keep ``<dir>/<status_id>.json`` in step with it.

    Raises :class:`StatusError` only for a usage mistake found before anything runs (no
    command, a bad id). Everything after that returns the command's own exit code."""
    if not command:
        raise StatusError("no command given (put it after --)")
    check_id(status_id)
    stale = DEFAULT_STALE_AFTER if stale_after is None else stale_after
    beat = heartbeat if heartbeat is not None else (max(1.0, min(60.0, stale / 3)) if stale else 0)
    base = {"kind": kind, "title": title or None, "agent": agent or None}
    base = {k: v for k, v in base.items() if v is not None}
    if stale_after is not None:
        base["stale_after"] = stale_after
    with contextlib.suppress(OSError):
        here = folder_path(os.getcwd())
        if here:
            base["x-folder"] = here  # only for a status_dir source's private_dirs
    lamp = _Lamp(status_id, directory)
    label = one_line(" ".join(command), 120)
    lamp.write(state="working", message=f"Running {label}", started=iso(utcnow()),
               needs_you=[], progress=None, **base)

    started = time.monotonic()
    tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
    try:
        # shutil.which finds npm.cmd-style launchers on Windows without a shell
        exe = shutil.which(command[0]) or command[0]
        proc = subprocess.Popen([exe, *command[1:]], stderr=subprocess.PIPE)
    except OSError as exc:
        lamp.write(state="error",
                   message=one_line(f"Could not start {command[0]}: {exc.strerror or exc}", 500))
        print(f"lamptown status run: could not start {command[0]}: {exc}", file=sys.stderr)
        return 127

    pump = threading.Thread(target=_pump, args=(proc.stderr, tail), daemon=True)
    pump.start()
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(beat):
            lamp.write()

    beater = threading.Thread(target=_beat, daemon=True) if beat else None
    if beater:
        beater.start()
    try:
        code, interrupted = _wait(proc)
    finally:
        stop.set()
        pump.join(timeout=5)
        if beater:
            beater.join(timeout=5)

    took = _duration(time.monotonic() - started)
    with contextlib.suppress(KeyboardInterrupt):  # a late Ctrl+C must not lose the exit code
        if code == 0:
            lamp.write(state="ok", message=f"Finished in {took}")
        else:
            detail = " | ".join(tail)
            what = "Interrupted" if interrupted else f"Exit {code}"
            text = f"{what} after {took}" + (f": {detail}" if detail else "")
            lamp.write(state="error", message=one_line(text, 500))
    return code
