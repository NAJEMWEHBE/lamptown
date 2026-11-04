"""Real-window smoke test of the desktop app (needs pywebview and a display).

Runs the app in a child process on a fictional config with a 1 s poll, then from inside it:
enters a room and opens its card, changes the data and checks the reload kept both, takes a
screenshot (Windows), minimises the window and checks nothing is rebuilt and the page stops
drawing, restores it and checks rebuilds resume. Skipped where there is no display (headless
Linux CI without xvfb) and, for now, on macOS unless LAMPTOWN_APP_SMOKE=1.

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
    (tmp_path / "lamptown.toml").write_text(CONFIG.format(done=3), encoding="utf-8")
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
            reloads0 = live.reloads
            (folder / "lamptown.toml").write_text(CONFIG.format(done=6), encoding="utf-8")
            check("data change noticed", wait(lambda: live.reloads > reloads0, 15),
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
            ticks = live.ticks
            check("rebuilds while showing", wait(lambda: live.ticks >= ticks + 2, 6),
                  f"{live.ticks - ticks}")
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
            (folder / "lamptown.toml").write_text(CONFIG.format(done=7), encoding="utf-8")
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
    hwnd = int(win.native.Handle.ToInt64())
    run_powershell(PS_SHOT, {"LT_HWND": str(hwnd), "LT_OUT": str(out)})
    return out.is_file() and out.stat().st_size > 1000


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "run":
        sys.exit(_scenario(Path(sys.argv[2]), Path(sys.argv[3])))
