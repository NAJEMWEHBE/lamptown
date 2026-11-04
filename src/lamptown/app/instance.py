"""One app per user: a lock file plus a loopback socket that wakes the running copy.

The first copy holds an OS lock on ``app.lock`` (``msvcrt`` on Windows, ``flock`` elsewhere; the OS
drops it when the process ends, even after a crash, so a stale lock can't block a new start). It
then listens on ``127.0.0.1`` on a random port and writes ``{pid, port, token}`` to
``instance.json`` (owner-only on POSIX). A second copy that can't take the lock reads that file
and sends ``<token> <command>``; the first copy checks the token and runs the command (``focus``
brings its window to the front). Nothing listens on a public interface, and a client without the
token is ignored.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

LOCK_NAME = "app.lock"
INFO_NAME = "instance.json"
COMMANDS = frozenset({"focus", "ping", "quit"})
MAX_MSG = 512


class SingleInstance:
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.lock_path = self.folder / LOCK_NAME
        self.info_path = self.folder / INFO_NAME
        self._fh = None
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._token = ""

    # ---------------------------------------------------------------- the first copy
    def acquire(self) -> bool:
        """Take the per-user lock. False means another copy is running."""
        if self._fh is not None:
            return True
        self.folder.mkdir(parents=True, exist_ok=True)
        fh = open(self.lock_path, "a+b")  # noqa: SIM115 - held open for the life of the app
        try:
            _lock(fh)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def serve(self, handler: Callable[[str], None]) -> int:
        """Listen for other copies; ``handler(command)`` runs on a background thread."""
        if self._fh is None:
            raise RuntimeError("serve() needs the lock first")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(4)
        sock.settimeout(0.5)
        self._sock = sock
        self._token = secrets.token_hex(16)
        port = sock.getsockname()[1]
        _write_private(self.info_path, json.dumps({"pid": os.getpid(), "port": port,
                                                   "token": self._token}))
        self._thread = threading.Thread(target=self._loop, args=(handler,), daemon=True,
                                        name="lamptown-instance")
        self._thread.start()
        return port

    def _loop(self, handler: Callable[[str], None]) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(2)
                try:
                    msg = _read_line(conn)
                except OSError:
                    continue
                token, _, cmd = msg.partition(" ")
                if not hmac.compare_digest(token.encode(), self._token.encode()):
                    continue
                cmd = cmd.strip()
                if cmd not in COMMANDS:
                    with contextlib.suppress(OSError):
                        conn.sendall(b"unknown\n")
                    continue
                with contextlib.suppress(OSError):
                    conn.sendall(b"ok\n")
            if cmd != "ping":
                with contextlib.suppress(Exception):
                    handler(cmd)

    def release(self) -> None:
        self._stop.set()
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
            self._sock = None
        if self._thread is not None:
            self._thread.join(2)
            self._thread = None
        if self._fh is not None:
            with contextlib.suppress(OSError):
                if _read_info(self.info_path).get("pid") == os.getpid():
                    self.info_path.unlink()
            with contextlib.suppress(OSError):
                _unlock(self._fh)
            self._fh.close()
            self._fh = None

    def __enter__(self) -> SingleInstance:
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    # ---------------------------------------------------------------- a second copy
    def send(self, command: str = "focus", timeout: float = 5.0) -> bool:
        """Ask the running copy to do ``command``. True once it said ok."""
        if command not in COMMANDS:
            raise ValueError(f"unknown command {command!r}")
        end = time.monotonic() + timeout
        while True:
            info = _read_info(self.info_path)
            if info.get("port") and info.get("token"):
                try:
                    with socket.create_connection(("127.0.0.1", int(info["port"])), 1.0) as c:
                        c.settimeout(2)
                        c.sendall(f"{info['token']} {command}\n".encode())
                        if _read_line(c) == "ok":
                            return True
                except (OSError, ValueError):
                    pass
            if time.monotonic() >= end:  # the first copy may still be starting: retry a while
                return False
            time.sleep(0.2)


def _read_line(conn: socket.socket) -> str:
    buf = b""
    while b"\n" not in buf and len(buf) < MAX_MSG:
        chunk = conn.recv(MAX_MSG)
        if not chunk:
            break
        buf += chunk
    return buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()


def _read_info(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_private(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        if sys.platform != "win32":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


if sys.platform == "win32":
    import msvcrt

    def _lock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
