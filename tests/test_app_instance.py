"""One app per user: the lock file and the loopback 'wake up' socket (no GUI, every OS)."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lamptown.app.instance import INFO_NAME, SingleInstance


def wait_for(cond, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def test_second_copy_cannot_take_the_lock(tmp_path):
    first, second = SingleInstance(tmp_path), SingleInstance(tmp_path)
    try:
        assert first.acquire()
        assert first.acquire()  # idempotent for the holder
        assert not second.acquire()
    finally:
        first.release()
    assert second.acquire(), "the lock must be free once the first copy is gone"
    second.release()


def test_focus_reaches_the_running_copy(tmp_path):
    got: list[str] = []
    first = SingleInstance(tmp_path)
    assert first.acquire()
    first.serve(got.append)
    try:
        second = SingleInstance(tmp_path)
        assert not second.acquire()
        assert second.send("focus")
        assert second.send("ping")  # ping is answered but doesn't reach the handler
        assert wait_for(lambda: got == ["focus"], 3), got
    finally:
        first.release()
    assert not (tmp_path / INFO_NAME).exists(), "the address file is removed on exit"


def test_wrong_token_is_ignored(tmp_path):
    got: list[str] = []
    first = SingleInstance(tmp_path)
    first.acquire()
    port = first.serve(got.append)
    try:
        with socket.create_connection(("127.0.0.1", port), 2) as c:
            c.settimeout(2)
            c.sendall(b"not-the-token focus\n")
            assert c.recv(64) == b""  # closed without an answer
        time.sleep(0.2)
        assert got == []
    finally:
        first.release()


def test_unknown_commands_are_refused(tmp_path):
    first = SingleInstance(tmp_path)
    first.acquire()
    first.serve(lambda cmd: None)
    try:
        with pytest.raises(ValueError):
            SingleInstance(tmp_path).send("rm")
        info = json.loads((tmp_path / INFO_NAME).read_text(encoding="utf-8"))
        with socket.create_connection(("127.0.0.1", info["port"]), 2) as c:
            c.settimeout(2)
            c.sendall(f"{info['token']} explode\n".encode())
            assert c.recv(64) == b"unknown\n"
    finally:
        first.release()


def test_listens_on_loopback_only(tmp_path):
    first = SingleInstance(tmp_path)
    first.acquire()
    first.serve(lambda cmd: None)
    try:
        assert first._sock.getsockname()[0] == "127.0.0.1"
        if sys.platform != "win32":
            assert ((tmp_path / INFO_NAME).stat().st_mode & 0o077) == 0, "address file is private"
    finally:
        first.release()


def test_send_gives_up_when_nobody_answers(tmp_path):
    (tmp_path / INFO_NAME).write_text(json.dumps({"pid": 1, "port": 9, "token": "x"}),
                                      encoding="utf-8")
    t = time.monotonic()
    assert SingleInstance(tmp_path).send("focus", timeout=0.6) is False
    assert time.monotonic() - t < 5


def test_serve_needs_the_lock(tmp_path):
    with pytest.raises(RuntimeError):
        SingleInstance(tmp_path).serve(lambda cmd: None)


def test_across_processes_and_after_a_crash(tmp_path):
    """A real second process: it holds the lock, gets 'focus', quits on 'quit'; then a hard kill
    leaves no stale lock behind."""
    marker = tmp_path / "got.txt"
    proc = subprocess.Popen([sys.executable, __file__, "serve", str(tmp_path), str(marker)])
    try:
        assert wait_for((tmp_path / INFO_NAME).exists), "the other process didn't start"
        me = SingleInstance(tmp_path)
        assert not me.acquire()
        assert me.send("focus")
        assert wait_for(lambda: marker.exists() and "focus" in marker.read_text()), "no focus"
        assert me.send("quit")
        assert proc.wait(10) == 0
        assert me.acquire()
        me.release()
        # a copy killed outright (no clean-up at all) must not block the next start
        proc = subprocess.Popen([sys.executable, __file__, "serve", str(tmp_path), str(marker)])
        assert wait_for(lambda: SingleInstance(tmp_path).send("ping", timeout=0.3), 10)
        proc.kill()
        proc.wait(10)
        again = SingleInstance(tmp_path)
        # Windows frees a dead process's file locks shortly after it ends, not at once
        assert wait_for(again.acquire, 10)
        again.release()
    finally:
        if proc.poll() is None:
            proc.kill()


def _serve(folder: str, marker: str) -> int:
    """Helper process for the cross-process test."""
    import threading

    done = threading.Event()
    inst = SingleInstance(Path(folder))
    if not inst.acquire():
        return 3

    def handle(cmd: str) -> None:
        with open(marker, "a", encoding="utf-8") as f:
            f.write(cmd + "\n")
        if cmd == "quit":
            done.set()

    inst.serve(handle)
    done.wait(60)
    inst.release()
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "serve":
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        sys.exit(_serve(sys.argv[2], sys.argv[3]))
