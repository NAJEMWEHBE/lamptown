"""``lamptown app``: the town in its own window (pywebview), kept live.

pywebview is the optional ``[app]`` extra and the only GUI dependency; it uses each OS's own web
view (WebView2 on Windows, WKWebView on macOS, WebKitGTK or Qt on Linux). The page notices it is
inside the app through ``window.pywebview``: it then always animates and labels itself live.

If pywebview or its GUI backend is missing, the app says what to install and opens the town in the
default browser instead.
"""

from __future__ import annotations

import contextlib
import html
import os
import sys
import threading
import traceback
import webbrowser
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

from lamptown.app.applog import AppLog
from lamptown.app.instance import SingleInstance
from lamptown.app.links import open_link
from lamptown.app.live import POLL, Live, fingerprint
from lamptown.app.paths import AppPaths, app_paths

APP_ID = "Lamptown.Desktop"  # Windows taskbar grouping: the window gets its own button and icon
BG = "#0b0e1c"
EXIT_OK, EXIT_FALLBACK, EXIT_BUSY = 0, 1, 4

INSTALL_HINT = 'The desktop window needs pywebview: pip install "lamptown[app]"'
LINUX_HINT = (
    "pywebview found no GUI backend. Install one of them:\n"
    '  GTK: pip install "pywebview[gtk]" (it also needs your system\'s WebKitGTK and PyGObject,\n'
    "       for example the packages gir1.2-webkit2-4.1 and python3-gi)\n"
    '  Qt:  pip install "pywebview[qt]"'
)


def say(msg: str) -> None:
    """Print for a console run; a windowed run (pythonw, a shortcut) has no stream to print to."""
    stream = sys.stderr
    if stream is None:
        return
    with contextlib.suppress(OSError, ValueError):
        print(msg, file=stream)


def asset(name: str) -> Path | None:
    try:
        p = Path(str(resources.files("lamptown.app").joinpath("assets", name)))
    except (ModuleNotFoundError, TypeError):
        return None
    return p if p.is_file() else None


def icon_for_platform(platform: str | None = None) -> Path | None:
    platform = sys.platform if platform is None else platform
    return asset("lamptown.ico" if platform == "win32" else "lamptown.png")


# ----------------------------------------------------------------------------- building
class Builder:
    """Builds the town in-process. Reads the config on every call, so edits show up live; a broken
    config is logged (once per distinct message) and keeps the last good page."""

    def __init__(self, config: str | None, out: str | None, log: Callable[[str], None]) -> None:
        self.config = config
        self.out = Path(out).expanduser() if out else None
        self.log = log
        self.town: Path | None = None
        self.error: str | None = None
        self._said: str | None = None

    def out_dir(self, cfg) -> Path:
        if self.out is None:
            from lamptown.build import default_out_dir

            self.out = default_out_dir(cfg)
        return self.out

    def fallback_town(self) -> Path:
        """Where the town page will be once a build works (for a window that opens on an error)."""
        if self.out is None:
            from lamptown.config import user_data_dir

            self.out = user_data_dir() / "town"
        from lamptown.build import TOWN_NAME

        return self.out / TOWN_NAME

    def __call__(self) -> dict[str, Any] | None:
        from lamptown.build import build
        from lamptown.config import ConfigError, load_config

        try:
            cfg = load_config(self.config)
            res = build(cfg, out_dir=self.out_dir(cfg))
        except (ConfigError, OSError) as e:
            self.error = str(e)
            if self.error != self._said:
                self.log(f"can't build the town: {self.error}")
                self._said = self.error
            return None
        except Exception:
            self.error = "the build crashed (see the log)"
            self.log("build crashed:\n" + traceback.format_exc())
            return None
        self.error = self._said = None
        self.town = res.town
        return res.data


def error_page(msg: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Lamptown</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:{BG};color:#eef1ff;
font:17px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}main{{max-width:640px;
padding:24px 28px;background:#161b38;border:3px solid #59629a;border-left:8px solid #ff5a5a}}
h1{{margin:0 0 8px;font-size:24px}}p{{margin:6px 0;color:#aab2e0}}code{{color:#ffd36e}}</style>
</head><body><main><h1>The town can't be built yet</h1><p>{html.escape(msg)}</p>
<p>Fix it and this window picks the town up by itself.</p></main></body></html>"""


def window_size(webview) -> tuple[int, int]:
    try:
        s = webview.screens[0]
        sw, sh = int(s.width), int(s.height)
    except Exception:
        sw, sh = 1920, 1080
    w = min(1640, int(sw * 0.92))
    return w, min(int(w * 9 / 16) + 60, int(sh * 0.9))


class _Api:
    """What the page may call (window.pywebview.api.*). Kept tiny on purpose: pywebview exposes
    every public attribute of this object to the page."""

    def __init__(self, log: Callable[[str], None] | None = None) -> None:
        self._live: Live | None = None
        self._log = log

    def visibility(self, hidden: bool) -> None:
        if self._live is not None:
            self._live.on_page_visibility(bool(hidden))

    def open_link(self, href: str) -> str:
        """Every link the page opens comes here (see lamptown.app.links for the rules)."""
        return open_link(href, log=self._log)


# ----------------------------------------------------------------------------- the app
def run_app(
    config: str | None = None,
    out: str | None = None,
    *,
    poll: float = POLL,
    state_dir: str | os.PathLike[str] | None = None,
    on_ready: Callable[[Live, Any], None] | None = None,
    echo: bool = True,
) -> int:
    """Open the window (or wake the open one). Returns an exit code.

    ``on_ready(live, window)`` runs on its own thread once the loop starts (the smoke test drives
    the window from there)."""
    paths = app_paths(state_dir).ensure()
    log = AppLog(paths.log, echo=say if echo and sys.stderr is not None else None)
    inst = SingleInstance(paths.lock_dir)
    if not inst.acquire():
        if inst.send("focus"):
            say("Lamptown is already open: brought its window to the front.")
            return EXIT_OK
        # No answer: the holder may have just died (Windows frees its lock a moment later).
        if not inst.acquire():
            say("Another Lamptown app holds the lock but doesn't answer; close it and try again.")
            log("second launch: the running copy didn't answer")
            return EXIT_BUSY
        log("took over the lock from a copy that didn't answer")
    try:
        return _run(paths, inst, log, config, out, poll, on_ready)
    finally:
        inst.release()


def _run(paths: AppPaths, inst: SingleInstance, log: AppLog, config, out, poll,  # noqa: PLR0917
         on_ready) -> int:
    builder = Builder(config, out, log)
    data = builder()  # open on fresh data instead of reloading right after the window shows
    town = builder.town or builder.fallback_town()
    try:
        import webview
    except Exception as e:  # not installed, or its own imports failed
        log(f"pywebview unavailable ({type(e).__name__}: {e})")
        return _browser_fallback(builder, log, INSTALL_HINT)

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    shown = threading.Event()
    try:
        # Off: pywebview would hand new-window links to webbrowser.open, which on Windows runs a
        # file: link through os.startfile. The page sends every link to api.open_link instead.
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        webview.settings["ALLOW_DOWNLOADS"] = False
        api = _Api(log)
        w, h = window_size(webview)
        title = _title(data)
        if data is not None:
            win = webview.create_window(title, town.resolve().as_uri(), js_api=api, width=w,
                                        height=h, min_size=(800, 500), background_color=BG)
        else:
            win = webview.create_window(title, html=error_page(builder.error or "unknown error"),
                                        js_api=api, width=w, height=h, min_size=(800, 500),
                                        background_color=BG)
        live = Live(win, builder, town.resolve().as_uri(), last=fingerprint(data), poll=poll,
                    log=log)
        api._live = live
        win.events.shown += lambda *_a: (shown.set(), log("window shown"))
        win.events.loaded += lambda *_a: log(f"page loaded: {_page(win)}")

        def on_command(cmd: str) -> None:
            if cmd == "focus":
                log("second launch: bringing the window forward")
                live.focus()
            elif cmd == "quit":
                log("asked to quit")
                win.destroy()

        inst.serve(on_command)

        def worker() -> None:
            if on_ready is not None:
                threading.Thread(target=on_ready, args=(live, win), daemon=True,
                                 name="lamptown-on-ready").start()
            live.run()

        icon = icon_for_platform()
        log(f"app starting (pywebview {_version('pywebview')}, poll {poll:g} s)")
        webview.start(worker, private_mode=False, storage_path=str(paths.webview),
                      icon=str(icon) if icon else None)
    except Exception as e:
        log("app failed:\n" + traceback.format_exc())
        if shown.is_set():  # it ran and then broke: don't pop a browser on top
            return EXIT_FALLBACK
        hint = LINUX_HINT if sys.platform.startswith(("linux", "freebsd")) else (
            f"The app window couldn't open ({type(e).__name__}: {e}).")
        return _browser_fallback(builder, log, hint)
    log("app closed")
    return EXIT_OK


def _version(dist: str) -> str:
    try:
        from importlib.metadata import version

        return version(dist)
    except Exception:
        return "?"


def _title(data: dict[str, Any] | None) -> str:
    t = str((data or {}).get("title") or "Lamptown").strip() or "Lamptown"
    return t if "lamptown" in t.lower() else f"{t} - Lamptown"


def _page(win) -> str:
    try:
        from lamptown.app.live import url_file_name

        return url_file_name(win.get_current_url()) or "(inline page)"
    except Exception:
        return "?"


def _browser_fallback(builder: Builder, log: Callable[[str], None], hint: str) -> int:
    say(hint)
    if builder.town is None:
        say(f"The town can't be built either: {builder.error}")
        return EXIT_FALLBACK
    url = builder.town.resolve().as_uri()
    say("Opening the town in your browser instead (it won't refresh itself there).")
    log(f"browser fallback: {url}")
    if not webbrowser.open(url):
        say(f"Open this in a browser: {url}")
    return EXIT_FALLBACK
