"""Find a Chrome-family browser for headless use (the browser tests and ``tools/make_media.py``).

Order: ``$CHROME`` (a path, or a name on ``PATH``), then the usual install places on each OS, then
a browser name on ``PATH``. Chrome, Chromium and Edge all work. Nothing is started here: finding
a browser only checks that a file exists, so this is safe to call at import time.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_BROWSER = "CHROME"

# Names a Chrome-family browser goes by on PATH (Linux distributions, Homebrew, snaps, Windows).
PATH_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
    "microsoft-edge",
    "microsoft-edge-stable",
    "msedge",
)
MAC_APPS = (
    "Google Chrome.app/Contents/MacOS/Google Chrome",
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "Chromium.app/Contents/MacOS/Chromium",
    "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
WIN_EXES = (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Chromium", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"),
)


@dataclass(frozen=True)
class BrowserFind:
    path: str | None  # the browser to run, or None
    why: str  # where it came from, or why there is none (one plain sentence)


def candidates(
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[Path]:
    """The usual install places on ``platform`` (default: this OS), most likely first."""
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    home = Path(os.path.expanduser("~")) if home is None else home
    out: list[Path] = []
    if platform == "win32":
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = env.get(key)
            if base:
                out += [Path(base, *parts) for parts in WIN_EXES]
    elif platform == "darwin":
        for apps in (Path("/Applications"), home / "Applications"):
            out += [apps / app for app in MAC_APPS]
    else:
        out += [Path("/usr/bin/google-chrome"), Path("/usr/bin/chromium"),
                Path("/usr/bin/chromium-browser"), Path("/snap/bin/chromium")]
    return out


def find_browser(
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    is_file: Callable[[Path], bool] = Path.is_file,
    home: Path | None = None,
) -> BrowserFind:
    """Look for a browser. The arguments exist for tests; the defaults look at this machine."""
    env = os.environ if env is None else env
    given = env.get(ENV_BROWSER, "").strip()
    if given:
        if is_file(Path(given)):
            return BrowserFind(given, f"${ENV_BROWSER}")
        found = which(given)
        if found:
            return BrowserFind(found, f"${ENV_BROWSER} on PATH")
        return BrowserFind(None, f"${ENV_BROWSER} is set to {given!r}, which is not a file or a "
                                 "command on PATH")
    for c in candidates(platform, env, home):
        if is_file(c):
            return BrowserFind(str(c), "the usual install place")
    for name in PATH_NAMES:
        found = which(name)
        if found:
            return BrowserFind(found, f"{name} on PATH")
    return BrowserFind(None, "no Chrome, Chromium or Edge found (install one, or set $CHROME "
                             "to its path)")
