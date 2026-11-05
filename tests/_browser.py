"""The headless browser every browser test shares (Chrome, Chromium or Edge; no extra dependency).

* ``CHROME``: the browser found by :func:`lamptown.onboarding.browser.find_browser` ($CHROME, the
  usual install places on each OS, then a browser name on PATH), or None.
* ``browser``: the pytest marker for tests that need it. ``conftest.py`` skips them with the reason
  in ``WHY`` when there is no browser, or fails them when ``LAMPTOWN_REQUIRE_BROWSER=1`` (CI sets
  that, so a missing browser can't pass as a green run).
* :func:`headless` builds the command line; :func:`run_browser` runs it and never hangs: the
  output goes to a file (not a pipe a stray child process could hold open), on a timeout the whole
  process tree is killed, and a timeout or a non-zero exit fails the test with the browser's
  stderr. :func:`dump_dom` is ``run_browser(...).stdout``. Every test that starts a browser
  uses one of them (``test_ci.py`` checks).
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lamptown.onboarding.browser import find_browser

_FOUND = find_browser()
CHROME: str | None = _FOUND.path
WHY = f"no headless browser: {_FOUND.why}"
REQUIRED = os.environ.get("LAMPTOWN_REQUIRE_BROWSER", "").strip() == "1"
TIMEOUT = 120.0

browser = pytest.mark.browser


def _no_sandbox() -> bool:
    """Linux CI runners (and root) can't start Chrome's sandbox; tests only load local files."""
    if not sys.platform.startswith("linux"):
        return False
    root = hasattr(os, "geteuid") and os.geteuid() == 0
    return root or bool(os.environ.get("CI"))


def headless(profile: Path, *flags: str) -> list[str]:
    """The start of a headless command line; add the page URL (and e.g. ``--dump-dom``)."""
    if CHROME is None:
        pytest.skip(WHY)
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check", "--disable-extensions", "--mute-audio",
           f"--user-data-dir={profile}"]
    if _no_sandbox():
        cmd.append("--no-sandbox")
    if sys.platform == "darwin":
        cmd.append("--use-mock-keychain")  # a real keychain can block a headless run on a prompt
    return cmd + list(flags)


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, check=False, timeout=30)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=30)


def _read(f) -> str:
    f.seek(0)
    return f.read().decode("utf-8", errors="replace")


def run_browser(cmd: list[str], timeout: float = TIMEOUT) -> subprocess.CompletedProcess:
    """Run a headless browser command and return its result (text stdout and stderr). Every test
    that starts a browser goes through here: a browser that doesn't finish in ``timeout`` seconds
    is killed with its child processes, and a timeout or a non-zero exit fails the test with the
    tail of the browser's stderr."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        kwargs: dict = {"stdout": out, "stderr": err, "stdin": subprocess.DEVNULL}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True  # its own process group, so it can be killed whole
        proc = subprocess.Popen(cmd, **kwargs)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            pytest.fail(f"the browser did not finish in {timeout:g} s and was stopped: "
                        f"{Path(cmd[0]).name}\nits last words:\n{_read(err)[-1500:]}")
        done = subprocess.CompletedProcess(cmd, proc.returncode, _read(out), _read(err))
    if done.returncode != 0:
        pytest.fail(f"the browser stopped with exit {done.returncode}: {Path(cmd[0]).name}\n"
                    f"its last words:\n{done.stderr[-1500:]}")
    return done


def dump_dom(cmd: list[str], timeout: float = TIMEOUT) -> str:
    """Run a headless ``--dump-dom`` command (through :func:`run_browser`) and return its stdout."""
    return run_browser(cmd, timeout).stdout
