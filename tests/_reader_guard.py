"""A test guard for the log readers: no test may read the real ``~/.codex`` (or ``~/.claude``)
or a real git repository outside the test's ``tmp_path``.

``guard(monkeypatch, tmp_path)`` points ``HOME``, ``USERPROFILE``, ``APPDATA``,
``AGENT_STATUS_DIR`` and ``CODEX_HOME`` away from the real ones, and wraps the file calls the
readers use (``open``, ``os.open``, ``os.stat``, ``os.scandir``, ``sqlite3.connect``) so that
touching a real one fails the test at once.
"""

from __future__ import annotations

import builtins
import io
import os
import sqlite3
from pathlib import Path
from urllib.parse import unquote

import pytest

REAL_HOME = Path.home()  # read once, before any test moves HOME
REAL_TOOL_DIRS = [REAL_HOME / ".codex", REAL_HOME / ".claude"]


def _flat(path: object, lower: bool = True) -> str:
    try:
        text = os.fspath(path)  # type: ignore[arg-type]
    except TypeError:
        return ""
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    text = unquote(text).replace("\\", "/")
    return text.lower() if lower else text


def guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("AGENT_STATUS_DIR", str(tmp_path / "status-default"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    banned = [_flat(p) for p in REAL_TOOL_DIRS]
    inside = _flat(tmp_path)
    lexists = os.path.lexists

    def check(path: object) -> None:
        flat = _flat(path)
        if not flat:
            return
        for real in banned:
            if real in flat:
                pytest.fail("a reader test touched the real tool folder", pytrace=False)
        if "/.git" in flat and inside not in flat:
            # ci-pr1: test the path as given too: ``flat`` is lower case, which names no file
            # on a case-sensitive file system (a real repository in a folder named with capitals)
            for text in (flat, _flat(path, lower=False)):
                plain = text.split("?", 1)[0].removeprefix("file:///").removeprefix("file:")
                if lexists(plain):
                    pytest.fail("a reader test touched a real git repository", pytrace=False)

    def wrap(fn):
        def guarded(*args, **kwargs):
            if args:
                check(args[0])
            return fn(*args, **kwargs)
        return guarded

    monkeypatch.setattr(builtins, "open", wrap(builtins.open))
    monkeypatch.setattr(io, "open", wrap(io.open))
    monkeypatch.setattr(os, "open", wrap(os.open))
    monkeypatch.setattr(os, "stat", wrap(os.stat))
    monkeypatch.setattr(os, "scandir", wrap(os.scandir))
    monkeypatch.setattr(sqlite3, "connect", wrap(sqlite3.connect))
