"""Per-user folders for the desktop app's state: the log, the web view profile and the lock.

Nothing is ever written next to the package or inside a project folder.

* Windows: everything in ``%LOCALAPPDATA%\\lamptown\\app`` (the profile in its ``webview``).
* macOS: the log in ``~/Library/Logs/lamptown``; the lock and the profile in
  ``~/Library/Application Support/lamptown/app``.
* Linux and other XDG systems: the log and the lock in ``$XDG_STATE_HOME/lamptown`` (default
  ``~/.local/state/lamptown``); the profile in ``$XDG_DATA_HOME/lamptown/webview`` (default
  ``~/.local/share/lamptown/webview``).

``$LAMPTOWN_APP_DIR`` (or ``lamptown app --state-dir``) puts all three in one folder instead: handy
for tests and portable installs.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_APP_DIR = "LAMPTOWN_APP_DIR"
APP = "lamptown"


@dataclass(frozen=True)
class AppPaths:
    log: Path  # the capped log file (one ".old" copy is kept next to it)
    webview: Path  # the web view profile (keeps the rooms' localStorage, e.g. the mute switch)
    lock_dir: Path  # the single-instance lock file and the running copy's address
    icons: Path  # where `lamptown shortcut` copies the icon a shortcut points at

    def ensure(self) -> AppPaths:
        for d in (self.log.parent, self.webview, self.lock_dir, self.icons):
            d.mkdir(parents=True, exist_ok=True)
        return self


def _env_path(env: Mapping[str, str], key: str) -> Path | None:
    v = env.get(key, "").strip()
    # XDG says relative values are invalid and must be ignored
    return Path(v) if v and Path(v).is_absolute() else None


def app_paths(
    state_dir: str | os.PathLike[str] | None = None,
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> AppPaths:
    """The app's folders for this user. ``platform``, ``env`` and ``home`` exist for tests."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path(os.path.expanduser("~")) if home is None else home
    if state_dir is None and env.get(ENV_APP_DIR, "").strip():
        state_dir = env[ENV_APP_DIR].strip()
    if state_dir is not None:
        base = Path(state_dir).expanduser().resolve()  # WebView2 wants an absolute profile path
        return AppPaths(base / "app.log", base / "webview", base, base / "icons")
    if platform == "win32":
        local = _env_path(env, "LOCALAPPDATA") or home / "AppData" / "Local"
        base = local / APP / "app"
        return AppPaths(base / "app.log", base / "webview", base, base / "icons")
    if platform == "darwin":
        support = home / "Library" / "Application Support" / APP / "app"
        return AppPaths(home / "Library" / "Logs" / APP / "app.log", support / "webview", support,
                        support / "icons")
    state = (_env_path(env, "XDG_STATE_HOME") or home / ".local" / "state") / APP
    data = (_env_path(env, "XDG_DATA_HOME") or home / ".local" / "share") / APP
    return AppPaths(state / "app.log", data / "webview", state, data / "icons")
