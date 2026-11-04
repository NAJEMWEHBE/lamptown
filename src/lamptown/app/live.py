"""The desktop app's live loop, kept free of any GUI import so it runs with a fake window in tests.

Every ``poll`` seconds the town is rebuilt in-process (on this worker thread). The window reloads
only when the town's *data* changed: the build time alone doesn't count, and the data holds only
absolute times (the page writes "in 3 h" itself), so a quiet setup never reloads. A reload keeps
the view: the page's ``viewHash()`` says which room is open and which card (``#room=<id>&open=<id>``
or ``#open=<id>``).

While the window is hidden (minimised, or the page reports ``document.hidden``) nothing is rebuilt;
the loop only checks the page every ``hidden_check`` seconds and catches up the moment the window
shows again. The page itself stops drawing while it is hidden. Hidden or not comes from two
portable sources, see :class:`Visibility`.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import traceback
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlsplit

from lamptown.app.links import LINKS_JS

DETAILS_NAME = "town-details.html"  # = lamptown.build.DETAILS_NAME; not imported, so the CLI
# can register `lamptown app` without loading the builder

POLL = 15.0  # seconds between rebuilds while the window shows
HIDDEN_CHECK = 5.0  # seconds between "still hidden?" checks of the page while hidden
CLOCK_KEYS = ("built", "builtIso")  # what changes on every build without the data changing

# Installed into every loaded page: report the Page Visibility state to the app (js_api).
VISIBILITY_JS = """(function () {
  if (window.__lamptownVis) return; window.__lamptownVis = 1;
  var send = function () {
    try { var a = window.pywebview && window.pywebview.api;
          if (a && a.visibility) a.visibility(!!document.hidden); } catch (e) {}
  };
  document.addEventListener('visibilitychange', send);
  if (window.pywebview && window.pywebview.api && window.pywebview.api.visibility) send();
  else window.addEventListener('pywebviewready', send);
})();"""


def fingerprint(data: dict[str, Any] | None) -> str | None:
    """The town's data without its build time: equal fingerprints mean nothing worth a reload."""
    if not isinstance(data, dict):
        return None
    rest = {k: v for k, v in data.items() if k not in CLOCK_KEYS}
    return json.dumps(rest, sort_keys=True, ensure_ascii=False, default=str)


def url_file_name(url: str | None) -> str:
    """The file name a page URL points at (no query, no hash): 'town.html', 'town-details.html'."""
    if not url:
        return ""
    # Some web views report a file URL with its query encoded ("town.html%3Fr=1"): cut it too.
    name = unquote(urlsplit(url).path).rstrip("/").rsplit("/", 1)[-1]
    return name.split("?", 1)[0].split("#", 1)[0]


class Visibility:
    """Is the window hidden? Two signals, either can say so:

    * window events from pywebview: ``minimized`` sets it; ``restored``, ``maximized`` and ``shown``
      clear it (on Windows a minimised window that comes back maximised fires only ``maximized``);
    * the page's ``visibilitychange`` (pushed through the js_api) and a poll of ``document.hidden``.

    A page that turns visible clears both (it is on screen). A poll only updates the page's own
    flag, so a backend whose page never reports hidden can't wake the loop while minimised.
    """

    WINDOW_SHOWN = frozenset({"restored", "maximized", "shown"})

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.minimised = False
        self.page_hidden = False

    @property
    def hidden(self) -> bool:
        return self.minimised or self.page_hidden

    def _set(self, fn: Callable[[], None]) -> bool:
        """Apply a change; True when it turned the window from hidden to showing."""
        with self._lock:
            was = self.hidden
            fn()
            return was and not self.hidden

    def window_event(self, name: str) -> bool:
        def fn():
            if name == "minimized":
                self.minimised = True
            elif name in self.WINDOW_SHOWN:
                self.minimised = False
        return self._set(fn)

    def page_event(self, hidden: bool) -> bool:
        def fn():
            self.page_hidden = bool(hidden)
            if not hidden:
                self.minimised = False
        return self._set(fn)

    def page_poll(self, hidden: bool) -> bool:
        def fn():
            self.page_hidden = bool(hidden)
        return self._set(fn)


class Live:
    """Keeps one window's town fresh.

    ``window`` needs ``events.<name> += fn`` for minimized/restored/maximized/shown/loaded/closed,
    ``get_current_url()``, ``evaluate_js(js)``, and for :meth:`focus` ``restore()``, ``show()``
    and an ``on_top`` attribute (a pywebview Window has all of them).
    ``rebuild()`` builds the town and returns its data dict, or None when the build failed.
    """

    def __init__(
        self,
        window: Any,
        rebuild: Callable[[], dict[str, Any] | None],
        town_url: str,
        *,
        last: str | None = None,
        poll: float = POLL,
        hidden_check: float = HIDDEN_CHECK,
        log: Callable[[str], None] | None = None,
        clock: Callable[[], dt.datetime] = dt.datetime.now,
    ) -> None:
        self.w = window
        self.rebuild = rebuild
        self.town_url = town_url
        self.last = last  # fingerprint of what the window shows
        self.poll = poll
        self.hidden_check = hidden_check
        self.log = log or (lambda _m: None)
        self.clock = clock
        self.vis = Visibility()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.ticks = 0  # rebuilds run (tests and the smoke check read it)
        self.reloads = 0
        ev = window.events
        for name in ("minimized", "restored", "maximized", "shown"):
            getattr(ev, name).__iadd__(lambda *_a, n=name: self.on_window_event(n))
        ev.loaded += self.on_loaded
        ev.closed += self.on_closed

    # ---------------------------------------------------------------- signals
    def on_window_event(self, name: str) -> None:
        if self.vis.window_event(name):
            self.wake.set()  # catch up right away

    def on_page_visibility(self, hidden: bool) -> None:
        if self.vis.page_event(bool(hidden)):
            self.wake.set()

    def on_loaded(self, *_a) -> None:
        try:
            self.w.evaluate_js(LINKS_JS)  # first: link clicks go through the app's safe opener
        except Exception as e:
            self.log(f"couldn't guard the page's links: {e}")
        try:
            self.w.evaluate_js(VISIBILITY_JS)
        except Exception as e:
            self.log(f"couldn't watch the page's visibility: {e}")

    def on_closed(self, *_a) -> None:
        self.stop.set()
        self.wake.set()

    def focus(self) -> None:
        """Bring the window back and to the front (a second launch asks for this)."""
        try:
            if self.vis.minimised:
                self.w.restore()
            self.w.show()
            self.w.on_top = True  # the portable way to raise a window above the others
            self.w.on_top = False
        except Exception as e:
            self.log(f"couldn't bring the window forward: {e}")

    def probe_page(self) -> None:
        """While hidden: ask the page itself (the fallback when no visibility event came)."""
        try:
            hidden = self.w.evaluate_js("document.hidden")
        except Exception:
            return
        if isinstance(hidden, bool) and self.vis.page_poll(hidden):
            self.wake.set()

    # ---------------------------------------------------------------- the loop
    def run(self) -> None:
        while not self.stop.is_set():
            self.wake.wait(self.poll)  # the town was built just before the window opened
            self.wake.clear()
            while not self.stop.is_set() and self.vis.hidden:  # no rebuilds while hidden
                self.wake.wait(self.hidden_check)
                self.wake.clear()
                if self.vis.hidden:
                    self.probe_page()
            if self.stop.is_set():
                break
            try:
                self.tick()  # also the catch-up right after the window shows again
            except Exception:
                self.log("tick failed:\n" + traceback.format_exc())

    def tick(self) -> bool:
        """Rebuild once; reload the window if the data changed. True when it reloaded."""
        self.ticks += 1
        data = self.rebuild()
        if data is None:
            return False
        fp = fingerprint(data)
        if fp is None or fp == self.last:
            return False
        if url_file_name(self.w.get_current_url()) == DETAILS_NAME:
            self.last = fp  # on the details page: it reads fresh data when it is opened
            return False
        # ?r= forces a real reload (a URL that differs only after '#' just scrolls the old page);
        # the hash reopens what was open.
        base = json.dumps(f"{self.town_url}?r={self.clock():%H%M%S%f}")
        self.log("data changed, reloading the town")
        self.w.evaluate_js(
            f"location.replace({base} + (typeof viewHash === 'function' ? viewHash() : ''))"
        )
        self.last = fp  # only now: if the reload threw, the next tick tries again
        self.reloads += 1
        return True
