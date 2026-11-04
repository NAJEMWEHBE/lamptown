"""Real-window smoke test of the desktop app (needs pywebview and a display).

Runs the app in a child process on a fictional config with a 1 s poll and the live town on,
then from inside it: enters a room and opens its card, changes the data and checks it was pushed
in place (no reload; the room updated, not entered again), changes the town's shape and checks
the reload kept the room and the card, takes a screenshot (Windows), writes an agent that waits
on you and checks the window title counts it, minimises the window and checks nothing is rebuilt
and the page stops drawing, restores it and checks rebuilds resume. Skipped where there is no
display (headless Linux CI without xvfb) and, for now, on macOS unless LAMPTOWN_APP_SMOKE=1.

Screenshots go to $LAMPTOWN_SHOT_DIR when set, else the test's temp folder.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOM = "docks"
CONFIG = """version = 1
[town]
title = "Harbour Watch"

[live]
status_dir = "status"
poll = 1

[[building]]
id = "docks"
type = "harbour"
label = "Docks"
  [[building.source]]
  kind = "static"
  state = "working"
  [[building.source.rows]]
  id = "paper-kite"
  name = "Paper Kite"
  status = "active"
  state = "working"
  progress = {{ done = {done}, total = 8 }}
  [[building.source.rows]]
  id = "moss-engine"
  name = "Moss Engine"
  status = "active"
  state = "ok"
  progress = {{ done = 5, total = 5 }}
  [[building.source.rows]]
  id = "tidebook"
  name = "Tidebook"
  status = "paused"
  state = "waiting"
  progress = {{ done = 1, total = 4 }}

[[building]]
id = "bell"
type = "jobs"
label = "Bell tower"
  [[building.source]]
  kind = "static"
  state = "ok"
{extra}"""

# a new building: the town changes shape, so the window reloads
EXTRA = """
[[building]]
id = "kiosk"
type = "kiosk"
plot = "lab"
label = "Tea kiosk"
  [[building.source]]
  kind = "static"
  state = "ok"
"""


def has_pywebview() -> bool:
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return True


def has_display() -> bool:
    if sys.platform == "win32":
        return True
    if sys.platform == "darwin":
        return os.environ.get("LAMPTOWN_APP_SMOKE") == "1"
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


pytestmark = [
    pytest.mark.skipif(not has_pywebview(), reason='needs the [app] extra (pywebview)'),
    pytest.mark.skipif(not has_display(), reason="needs a display (use xvfb-run on Linux)"),
]


def test_real_window_keeps_the_room_across_a_reload_and_pauses_when_minimised(tmp_path):
    shots = Path(os.environ.get("LAMPTOWN_SHOT_DIR") or tmp_path)
    (tmp_path / "lamptown.toml").write_text(CONFIG.format(done=3, extra=""),
                                             encoding="utf-8")
    r = subprocess.run([sys.executable, __file__, "run", str(tmp_path), str(shots)],
                       capture_output=True, text=True, timeout=180, check=False)
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
    results = [json.loads(ln) for ln in lines]
    detail = "\n".join(f"{'PASS' if x['ok'] else 'FAIL'} {x['name']} {x.get('detail', '')}"
                       for x in results)
    log = tmp_path / "state" / "app.log"
    tail = log.read_text(encoding="utf-8")[-2000:] if log.is_file() else "(no log)"
    assert results, f"no results\nstdout: {r.stdout[-2000:]}\nstderr: {r.stderr[-2000:]}\n{tail}"
    assert all(x["ok"] for x in results), f"{detail}\nlog:\n{tail}"
    assert r.returncode == 0, f"{detail}\nstderr: {r.stderr[-2000:]}"
    names = {x["name"] for x in results}
    assert "room kept after the reload" in names and "rebuilds resume after restore" in names
    assert "data change pushed in place" in names and "title counts who needs you" in names
    assert "a .bat link only showed its folder" in names
    assert not (tmp_path / "marker.txt").exists(), "a link ran a program"


# ----------------------------------------------------------------------------- the child process
def _scenario(folder: Path, shots: Path) -> int:
    from lamptown.app import links
    from lamptown.app.window import run_app

    results: list[dict] = []
    spawned: list[list[str]] = []
    browsed: list[str] = []
    links._spawn = lambda args: spawned.append(list(args))  # record; never open a real window
    links._browse = browsed.append
    probe = folder / ("probe.bat" if sys.platform == "win32" else "probe.sh")
    probe.write_text("echo ran > marker.txt\n", encoding="utf-8")

    def check(name: str, ok, detail="") -> None:
        results.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
        print(json.dumps(results[-1]), flush=True)

    def wait_js(win, expr: str, want, timeout: float = 20.0):
        end = time.monotonic() + timeout
        val = None
        while time.monotonic() < end:
            try:
                val = win.evaluate_js(expr)
                if val == want:
                    return val
            except Exception as e:
                val = f"error: {e}"
            time.sleep(0.25)
        return val

    def url_name(win) -> str:
        from lamptown.app.live import url_file_name

        return url_file_name(win.get_current_url())

    def wait(cond, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.1)
        return cond()

    def scenario(live, win) -> None:
        try:
            check("town loaded", wait_js(win, "typeof viewHash", "function") == "function")
            check("page knows it is in the app", win.evaluate_js("!!window.pywebview") is True)
            check("link guard installed", wait_js(win, "window.__lamptownLinks", 1) == 1)
            href = json.dumps(probe.as_uri())
            # a target=_blank link, a plain link and window.open, all to a .bat; then a web link
            win.evaluate_js(
                "(function(){for (const t of ['_blank', '']) {"
                f"const a = document.createElement('a'); a.href = {href}; if (t) a.target = t;"
                "document.body.appendChild(a); a.click();}"
                f"window.open({href}, '_blank');"
                "const w = document.createElement('a'); w.href = 'https://example.com/kite';"
                "w.target = '_blank'; document.body.appendChild(w); w.click();})()")
            shown = links.reveal_command(probe, False)  # explorer /select, | open -R | xdg-open
            check("a .bat link only showed its folder", wait(lambda: len(spawned) >= 3, 10)
                  and len(spawned) == 3 and all(a == shown for a in spawned), spawned)
            check("a web link went to the browser", wait(lambda: browsed, 5)
                  and browsed == ["https://example.com/kite"], browsed)
            check("the window stayed on the town", url_name(win) == "town.html",
                  win.get_current_url())
            check("nothing ran", not (folder / "marker.txt").exists())
            win.evaluate_js(f"enterRoom('{ROOM}')")
            check("walked into the room", wait_js(win, "RS.id", ROOM) == ROOM)
            win.evaluate_js(f"openPanel(plotOf('{ROOM}'), stage)")
            hash0 = wait_js(win, "viewHash()", f"#room={ROOM}&open={ROOM}")
            check("view hash names the room and the card", hash0 == f"#room={ROOM}&open={ROOM}",
                  hash0)
            t0 = win.evaluate_js("performance.timeOrigin")
            reloads0, pushes0 = live.reloads, live.pushes
            enters0 = win.evaluate_js("RS.room.seen().enters")
            planks0 = win.evaluate_js("JSON.stringify(RS.room.seen().bays.map(b => b.planks))")
            (folder / "lamptown.toml").write_text(CONFIG.format(done=6, extra=""),
                                                  encoding="utf-8")
            check("data change pushed in place", wait(lambda: live.pushes > pushes0, 15),
                  f"ticks {live.ticks}")
            check("harbour updated, not entered again", wait_js(
                win, "JSON.stringify(RS.room.seen().bays.map(b => b.planks)) !== "
                f"{json.dumps(planks0)} && RS.room.seen().updates > 0 && "
                f"RS.room.seen().enters === {json.dumps(enters0)}", True, 10) is True, planks0)
            check("no reload for a data change", live.reloads == reloads0 and
                  win.evaluate_js("performance.timeOrigin") == t0)
            check("room and card still open", wait_js(win, "RS.id === 'docks' && openPlot !== null",
                                                      True, 5) is True)
            (folder / "lamptown.toml").write_text(CONFIG.format(done=6, extra=EXTRA),
                                                  encoding="utf-8")
            check("a new building reloads the page", wait(lambda: live.reloads > reloads0, 15),
                  f"ticks {live.ticks}")
            ok = wait(lambda: wait_js(win, "performance.timeOrigin", None, 0.5) not in (t0, None),
                      15)
            check("page really reloaded", ok)
            check("room kept after the reload", wait_js(win, "RS.id", ROOM) == ROOM)
            url = win.evaluate_js("location.href")
            check("reload url carries ?r= and the view", "?r=" in url
                  and url.endswith(f"#room={ROOM}&open={ROOM}"), url)
            check("card open again", wait_js(win, "openPlot !== null", True) is True)
            time.sleep(1.5)  # let the walk-in settle before the picture
            if sys.platform == "win32":
                shot = shots / "app-room-after-reload.png"
                check("screenshot", _screenshot(win, shot), shot)
            ticks, lives = live.ticks, live.live_ticks
            check("rebuilds while showing", wait(lambda: live.ticks >= ticks + 2, 6),
                  f"{live.ticks - ticks}")
            check("live ticks while showing", live.live_ticks >= lives + 2,
                  f"{live.live_ticks - lives}")
            from lamptown.status.store import update_status

            plain = "Harbour Watch - Lamptown"
            update_status("codex-trim", folder / "status", title="Trim the sails",
                          state="waiting", agent="codex")
            check("title counts who needs you",
                  wait(lambda: _title(win) == "1 needs you - " + plain, 10), _title(win))
            check("the page got the live payload",
                  wait_js(win, "LIVE.payload && LIVE.payload.need", 1, 5) == 1)
            check("its corner says live", wait_js(win, "LIVE.on", True, 5) is True)
            if sys.platform == "win32":
                shot = shots / "app-title-badge.png"
                check("screenshot of the badge", _screenshot(win, shot), shot)
            update_status("codex-trim", folder / "status", state="working")
            check("title plain again", wait(lambda: _title(win) == plain, 10), _title(win))
            win.minimize()
            check("minimise noticed", wait(lambda: live.vis.hidden, 5))
            time.sleep(1.5)  # a rebuild already running may finish
            n = live.ticks
            time.sleep(3.5)
            check("no rebuilds while minimised", live.ticks == n, f"{live.ticks - n} extra")
            hidden = win.evaluate_js("document.hidden")
            check("page reports hidden (it stops drawing)", hidden is True, hidden)
            win.restore()
            check("restore noticed", wait(lambda: not live.vis.hidden, 5))
            check("rebuilds resume after restore", wait(lambda: live.ticks > n, 5),
                  f"{live.ticks - n}")
            # on the details page a data change must not yank the user back to the town
            win.evaluate_js("location.href = 'town-details.html#b-docks'")
            check("details page open", wait_js(win, "document.title.endsWith('details')", True)
                  is True)
            last, reloads = live.last, live.reloads
            (folder / "lamptown.toml").write_text(CONFIG.format(done=7, extra=EXTRA),
                                                  encoding="utf-8")
            check("change seen on the details page", wait(lambda: live.last != last, 10))
            time.sleep(1.5)
            check("details page left alone", live.reloads == reloads and
                  "town-details.html" in str(win.evaluate_js("location.href")),
                  win.get_current_url())
        except Exception as e:
            check("scenario ran", False, f"{type(e).__name__}: {e}")
        finally:
            win.destroy()

    rc = run_app(str(folder / "lamptown.toml"), str(folder / "out"), poll=1.0,
                 state_dir=folder / "state", on_ready=scenario, echo=False)
    check("app exited cleanly", rc == 0, rc)
    return 0 if all(r["ok"] for r in results) else 1


_HWND: dict[int, int] = {}


def _hwnd(win) -> int:
    """The window's handle, read once (a WinForms property; the window exists by then)."""
    key = id(win)
    if key not in _HWND:
        _HWND[key] = int(win.native.Handle.ToInt64())
    return _HWND[key]


def _title(win) -> str:
    """The window's title as the OS shows it, else pywebview's.

    Windows: user32's InternalGetWindowText, through ctypes. Not ``win.native.Text``: that
    pythonnet call keeps the GIL while it waits for the GUI thread, which may itself be waiting
    for the GIL to run the app's set_title, and the two deadlock (the window then shows "Not
    Responding" and the test times out). InternalGetWindowText sends no message at all."""
    if sys.platform == "win32":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.InternalGetWindowText(_hwnd(win), buf, len(buf))
            return buf.value
        except Exception:
            pass
    return str(win.title)


PS_SHOT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class LtShot {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
"@
[void][LtShot]::SetProcessDPIAware()
$h = [IntPtr][long]$env:LT_HWND
$r = New-Object LtShot+RECT; [void][LtShot]::GetWindowRect($h, [ref]$r)
$bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
[void][LtShot]::PrintWindow($h, $dc, 2); $g.ReleaseHdc($dc); $g.Dispose()
$bmp.Save($env:LT_OUT, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
"""


def _screenshot(win, out: Path) -> bool:
    """Capture the window (PrintWindow renders it even when covered). Windows only."""
    from lamptown.app.shortcut import run_powershell

    out.parent.mkdir(parents=True, exist_ok=True)
    hwnd = _hwnd(win)
    run_powershell(PS_SHOT, {"LT_HWND": str(hwnd), "LT_OUT": str(out)})
    return out.is_file() and out.stat().st_size > 1000


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "run":
        sys.exit(_scenario(Path(sys.argv[2]), Path(sys.argv[3])))
