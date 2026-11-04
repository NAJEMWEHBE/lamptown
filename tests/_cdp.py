"""A tiny DevTools client (stdlib only) for the browser tests that need what a command line can't
set: the page's time zone (``Emulation.setTimezoneOverride``). Chrome on Windows ignores ``TZ``,
so a DST day can only be tested in a zone that has one this way, on every OS alike.

:func:`probe` starts the shared headless browser (``_browser.headless``) with a DevTools port,
sets the zone, opens the page and waits (condition with a cap) for the probe's ``<pre id="T">``.
The browser is always stopped, its whole process tree with it.
"""

from __future__ import annotations

import base64
import contextlib
import http.client
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _browser import _kill_tree, headless


class _WebSocket:
    """Just enough of RFC 6455 for DevTools: text frames out (masked), frames in (unmasked)."""

    def __init__(self, url: str, timeout: float) -> None:
        assert url.startswith("ws://"), url
        host, path = url[5:].split("/", 1)
        name, port = host.rsplit(":", 1)
        self.sock = socket.create_connection((name, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            self.buf += self._recv()
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0]
        assert b" 101 " in status, f"DevTools refused the socket: {status!r}"

    def _recv(self) -> bytes:
        chunk = self.sock.recv(1 << 16)
        if not chunk:
            raise ConnectionError("DevTools closed the socket")
        return chunk

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            self.buf += self._recv()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text: str, opcode: int = 0x1) -> None:
        data = text.encode()
        head = bytearray([0x80 | opcode])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 1 << 16:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        msg = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else b""
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            op = b0 & 0x0F
            if op == 0x8:
                raise ConnectionError("DevTools closed the socket")
            if op == 0x9:  # ping: pong, then keep reading
                self.send(data.decode("latin-1"), 0xA)
                continue
            if op == 0xA:
                continue
            msg += data
            if b0 & 0x80:
                return msg.decode("utf-8", errors="replace")

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()


class _Session:
    def __init__(self, ws: _WebSocket) -> None:
        self.ws, self.n = ws, 0

    def call(self, method: str, **params) -> dict:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                assert "error" not in msg, f"{method}: {msg['error']}"
                return msg.get("result", {})


def _port(profile: Path, proc: subprocess.Popen, cap: float) -> int:
    f = profile / "DevToolsActivePort"
    end = time.monotonic() + cap
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"the browser quit at start (exit {proc.returncode})")
        with contextlib.suppress(OSError, ValueError, IndexError):
            return int(f.read_text(encoding="utf-8").split()[0])
        time.sleep(0.1)
    raise TimeoutError("the browser never opened its DevTools port")


def _page_socket(port: int, cap: float) -> str:
    end = time.monotonic() + cap
    while time.monotonic() < end:
        with contextlib.suppress(OSError, ValueError, http.client.HTTPException):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("GET", "/json/list")
            targets = json.loads(c.getresponse().read())
            c.close()
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        time.sleep(0.1)
    raise TimeoutError("the browser shows no page to drive")


def probe(url: str, zone: str, work: Path, cap: float = 90.0) -> str:
    """Open ``url`` in headless Chrome with its clock in ``zone`` (an IANA name) and return the
    text of the probe's ``<pre id="T">`` once it is written (at most ``cap`` seconds)."""
    profile = Path(tempfile.mkdtemp(prefix="cdp-", dir=work))
    cmd = headless(profile, "--window-size=1600,900", "--remote-debugging-port=0",
                   "--allow-file-access-from-files", "about:blank")
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "stdin": subprocess.DEVNULL}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    ws = None
    try:
        ws = _WebSocket(_page_socket(_port(profile, proc, 30), 30), timeout=30)
        s = _Session(ws)
        s.call("Emulation.setTimezoneOverride", timezoneId=zone)
        s.call("Page.navigate", url=url)
        end = time.monotonic() + cap
        while time.monotonic() < end:
            r = s.call("Runtime.evaluate", returnByValue=True,
                       expression="(document.getElementById('T') || {}).textContent || ''")
            text = (r.get("result") or {}).get("value") or ""
            if text:
                return text
            time.sleep(0.2)
        raise TimeoutError(f"the probe wrote nothing in {cap:g} s")
    finally:
        if ws is not None:
            with contextlib.suppress(Exception):
                ws.send(json.dumps({"id": 10**6, "method": "Browser.close"}))
            ws.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
        _kill_tree(proc)
