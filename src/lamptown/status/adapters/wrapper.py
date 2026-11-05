"""The generic wrapper: ``lamptown status run --id X -- <command>``.

Works for any agent or script without hooks (Aider, cron jobs, build scripts...): the file
reads ``working`` while the command runs (refreshed by a heartbeat, so a killed wrapper goes
stale instead of glowing forever), ``ok`` on exit code 0, and ``error`` with the exit code
and how many lines it wrote on stderr otherwise. The file names only the program (its last
path part): never its arguments, its folder or anything it printed, since the town shows the
message. Output passes through unchanged; the exit code is returned unchanged (a child killed
by signal N gives ``128 + N``, as a shell would).

The lamp never gets in the command's way: if the status file can't be written (a full disk, a
bad folder, any other error), the wrapper says so once on stderr and runs the command anyway,
with the same exit code.

Ctrl+C reaches the command itself (it shares the console), and the wrapper waits for it to
finish. A second Ctrl+C, or a command that hasn't stopped 10 s after the first, is ended by
the wrapper (exit 130).
"""

from __future__ import annotations

import contextlib
import os
import re
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
    safe_reason,
    update_status,
    utcnow,
)

INTERRUPT_GRACE = 10.0  # seconds the command gets to stop by itself after a Ctrl+C
EXIT_INTERRUPTED = 130


def program_name(program: object) -> str:
    """The last part of the program's path (``~/bin/deploy.sh`` -> ``deploy.sh``, either kind of
    slash): what the status file says it runs. Never its folder or arguments (review-fix B1)."""
    leaf = re.split(r"[\\/]", str(program).strip().rstrip("\\/"))[-1]
    return one_line(leaf, 60) or "the command"


def _pump(stream, lines: list[int]) -> None:
    """Copy the child's stderr through to ours while counting its non-blank lines (only the
    count reaches the status file: stderr text can hold paths and secrets)."""
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
            lines[0] += 1
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
        except Exception as exc:  # review-fix B1: any lamp error, never the command's way
            if not self.warned:
                self.warned = True
                with contextlib.suppress(Exception):
                    print("lamptown status run: can't update the status file "
                          f"({safe_reason(exc, typed=True)}); the command runs anyway",
                          file=sys.stderr, flush=True)
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
    label = program_name(command[0])  # review-fix B1: the program's name only, no arguments
    lamp.write(state="working", message=f"Running {label}", started=iso(utcnow()),
               needs_you=[], progress=None, **base)

    started = time.monotonic()
    lines = [0]  # non-blank stderr lines
    try:
        # shutil.which finds npm.cmd-style launchers on Windows without a shell
        exe = shutil.which(command[0]) or command[0]
        proc = subprocess.Popen([exe, *command[1:]], stderr=subprocess.PIPE)
    except OSError as exc:
        lamp.write(state="error",
                   message=one_line(f"Could not start {label}: {safe_reason(exc)}", 500))
        print(f"lamptown status run: could not start {command[0]}: {exc}", file=sys.stderr)
        return 127

    pump = threading.Thread(target=_pump, args=(proc.stderr, lines), daemon=True)
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
            # review-fix B1: a fixed description, the exit code and a count, never stderr text
            what = "Interrupted" if interrupted else f"Exit {code}"
            count = lines[0]
            said = f", {count} line{'s' if count != 1 else ''} on stderr" if count else ""
            lamp.write(state="error", message=f"{what} after {took}{said}")
    return code
