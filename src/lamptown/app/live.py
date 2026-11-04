"""The desktop app's live loop, kept free of any GUI import so it runs with a fake window in tests.

Two cadences while the window shows:

* a **live tick** every few seconds (the config's ``[live] poll``, 3 s; at least every
  ``HEARTBEAT_MAX`` 10 s, and every second while a reader catches up): the app's one Watcher
  runs when due and ``town-live.js`` is written (the page's heartbeat), then the payload is
  pushed into the page (``lamptownLive(p)``) and the window title counts what needs you
  ("2 need you - Lamptown");
* a **rebuild** every ``poll`` seconds (15 s): the town is built in-process. When its data
  changed (the build time alone doesn't count) the new data is pushed into the page
  (``lamptownData(D)``): it swaps in place, so a walk in progress finishes. The page reloads
  only when the town's *structure* changed (plots, building ids and types, title, version) or
  the window isn't showing the town (it opened on the error page). A reload keeps the view: the
  page's ``viewHash()`` says which room is open and which card (``#room=<id>&open=<id>`` or
  ``#open=<id>``).

While the window is hidden (minimised, or the page reports ``document.hidden``) nothing is rebuilt
or pushed; the loop checks the page every ``hidden_check`` seconds, still runs the Watcher every
``hidden_watch`` seconds (so the history keeps filling and the title still counts), and catches
up the moment the window shows again. Hidden or not comes from two portable sources, see
:class:`Visibility`.

Once the window starts closing (``closing``, then ``closed``) the loop makes no more calls into
the window: a web view that is being torn down may never answer a script.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlsplit

from lamptown.app.links import LINKS_JS
from lamptown.livefeed import HEARTBEAT_MAX, FailLog

DETAILS_NAME = "town-details.html"  # = lamptown.build.DETAILS_NAME; not imported, so the CLI
# can register `lamptown app` without loading the builder

POLL = 15.0  # seconds between rebuilds while the window shows
LIVE_POLL = 3.0  # seconds between live ticks (Watcher + town-live.js) without a feed's own
HIDDEN_CHECK = 5.0  # seconds between "still hidden?" checks of the page while hidden
HIDDEN_WATCH = 15.0  # seconds between Watcher ticks while hidden
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
    """The town's data without its build time: equal fingerprints mean nothing to push."""
    if not isinstance(data, dict):
        return None
    rest = {k: v for k, v in data.items() if k not in CLOCK_KEYS}
    return json.dumps(rest, sort_keys=True, ensure_ascii=False, default=str)


def structure(data: dict[str, Any] | None) -> str | None:
    """What a data swap can't change in a loaded page: the plots, each building's id, type and
    plot (a new room type needs its script, which only a reload brings), the title, the data
    spec and the version. Equal structures: push the data; different: reload the page."""
    if not isinstance(data, dict):
        return None
    parts = data.get("parts") if isinstance(data.get("parts"), dict) else {}
    shape = {
        "spec": data.get("spec"), "version": data.get("version"), "title": data.get("title"),
        "plots": data.get("plots"),
        "parts": sorted([str(k), str(p.get("type")), str(p.get("plot"))]
                        for k, p in parts.items() if isinstance(p, dict)),
    }
    return json.dumps(shape, sort_keys=True, ensure_ascii=False, default=str)


def window_title(data: dict[str, Any] | None) -> str:
    """The window's plain title: the town's own, always saying Lamptown."""
    t = str((data or {}).get("title") or "Lamptown").strip() or "Lamptown"
    return t if "lamptown" in t.lower() else f"{t} - Lamptown"


def badge_title(plain: str, need: Any) -> str:
    """``"2 need you - <plain>"`` while something needs you, else the plain title."""
    n = need if isinstance(need, int) and not isinstance(need, bool) else 0
    if n <= 0:
        return plain
    return f"{n} need{'s' if n == 1 else ''} you - {plain}"


def js_value(obj: Any) -> str:
    """A JSON value as JS source for ``evaluate_js`` (ASCII only, so U+2028 can't break it)."""
    return json.dumps(obj, ensure_ascii=True, allow_nan=False, default=str)


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
    ``get_current_url()``, ``evaluate_js(js)``, ``set_title(t)`` (or a ``title`` attribute), and
    for :meth:`focus` ``restore()``, ``show()`` and an ``on_top`` attribute (a pywebview Window
    has all of them).
    ``rebuild()`` builds the town and returns its data dict, or None when the build failed.
    ``feed`` (a :class:`lamptown.livefeed.LiveFeed`, or anything with ``tick(rev)`` returning an
    object with a ``payload``, or None when live is off) runs the Watcher; ``feed.every`` is the
    live cadence. ``data``: what the window opened on.
    """

    def __init__(
        self,
        window: Any,
        rebuild: Callable[[], dict[str, Any] | None],
        town_url: str,
        *,
        last: str | None = None,
        data: dict[str, Any] | None = None,
        feed: Any = None,
        poll: float = POLL,
        live_every: float | None = None,
        hidden_check: float = HIDDEN_CHECK,
        hidden_watch: float = HIDDEN_WATCH,
        log: Callable[[str], None] | None = None,
        clock: Callable[[], dt.datetime] = dt.datetime.now,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self.w = window
        self.rebuild = rebuild
        self.town_url = town_url
        self.town_name = url_file_name(town_url)
        self.last = fingerprint(data) if data is not None else last  # what the window shows
        self.shape = structure(data)  # its structure (None: unknown, so a change reloads)
        self.rev = _rev(data)
        self.plain = window_title(data)
        self.need: Any = 0
        self.shown_title: str | None = window_title(data) if data is not None else None
        self.feed = feed
        self.poll = poll
        self.live_every = live_every
        self.hidden_check = hidden_check
        self.hidden_watch = hidden_watch
        self.log = log or (lambda _m: None)
        self.clock = clock
        self.mono = mono
        self.vis = Visibility()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.ticks = 0  # rebuilds run (tests and the smoke check read it)
        self.reloads = 0
        self.pushes = 0  # town data pushed into the page
        self.live_ticks = 0
        self.live_pushes = 0  # live payloads pushed into the page
        self._due: dict[str, float] = {}
        self._hidden = False
        self._said: set[str] = set()
        self._fails: dict[str, FailLog] = {}
        ev = window.events
        for name in ("minimized", "restored", "maximized", "shown"):
            getattr(ev, name).__iadd__(lambda *_a, n=name: self.on_window_event(n))
        ev.loaded += self.on_loaded
        if getattr(ev, "closing", None) is not None:  # pywebview: before the window goes
            ev.closing += self.on_closed
        ev.closed += self.on_closed

    # ---------------------------------------------------------------- signals
    def on_window_event(self, name: str) -> None:
        if self.vis.window_event(name):
            self.wake.set()  # catch up right away

    def on_page_visibility(self, hidden: bool) -> None:
        if self.vis.page_event(bool(hidden)):
            self.wake.set()

    @property
    def closing(self) -> bool:
        """The window is closing or closed: no more calls into it."""
        return self.stop.is_set()

    def on_loaded(self, *_a) -> None:
        if self.closing:
            return
        try:
            self.w.evaluate_js(LINKS_JS)  # first: link clicks go through the app's safe opener
        except Exception as e:
            self.log(f"couldn't guard the page's links: {e}")
        try:
            self.w.evaluate_js(VISIBILITY_JS)
        except Exception as e:
            self.log(f"couldn't watch the page's visibility: {e}")

    def on_closed(self, *_a) -> None:
        """The window is closing (or closed): stop the loop; returns None, so a ``closing``
        handler never cancels the close."""
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
        if self.closing:
            return
        try:
            hidden = self.w.evaluate_js("document.hidden")
        except Exception:
            return
        if isinstance(hidden, bool) and self.vis.page_poll(hidden):
            self.wake.set()

    # ---------------------------------------------------------------- the loop
    def run(self) -> None:
        while not self.stop.is_set():
            due = self.step(self.mono())
            if self.stop.is_set():
                break
            self.wake.wait(max(0.0, min(due - self.mono(), 3600.0)))
            self.wake.clear()

    def every(self) -> float:
        """Seconds until the next live tick: ``live_every``, else the feed's own (it says when
        its next tick is due); never more than ``HEARTBEAT_MAX`` (the page's heartbeat)."""
        v = self.live_every if self.live_every is not None else getattr(self.feed, "every",
                                                                         LIVE_POLL)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return LIVE_POLL
        return min(v, HEARTBEAT_MAX) if math.isfinite(v) and v > 0 else LIVE_POLL

    def step(self, now: float) -> float:
        """Do what is due at ``now`` (a monotonic time); returns when something is due next.
        The town was built just before the window opened, so the first rebuild waits a ``poll``;
        the first live tick comes at once (the Watcher starts reading)."""
        due = self._due
        if not due:
            due.update(live=now, build=now + self.poll)
        if self.vis.hidden:
            if not self._hidden:  # just hid
                self._hidden = True
                due.update(probe=now + self.hidden_check, watch=now + self.hidden_watch)
            if now >= due["probe"]:
                self.probe_page()
                due["probe"] = now + self.hidden_check
            if self.vis.hidden:  # still hidden: only the Watcher, and only now and then
                if self.feed is not None and now >= due["watch"]:
                    self._guard(self.live_tick, push=False)
                    due["watch"] = now + self.hidden_watch
                return min(due["probe"], due["watch"]) if self.feed is not None else due["probe"]
        if self._hidden:  # showing again: catch up at once
            self._hidden = False
            due.update(live=now, build=now)
        if self.feed is not None and now >= due["live"]:
            self._guard(self.live_tick)
            due["live"] = now + self.every()
        if now >= due["build"]:
            self._guard(self.tick)  # also the catch-up right after the window shows again
            due["build"] = now + self.poll
        return min(due["live"], due["build"]) if self.feed is not None else due["build"]

    def _guard(self, fn: Callable[..., Any], **kw: Any) -> None:
        """Run a tick; one that keeps failing logs its traceback once, then a counted line
        now and then (:class:`~lamptown.livefeed.FailLog`)."""
        name = getattr(fn, "__name__", "tick")
        fails = self._fails.get(name)
        if fails is None:
            fails = self._fails[name] = FailLog(self.log, "live tick" if name == "live_tick"
                                                else "tick")
        try:
            fn(**kw)
        except Exception as e:
            fails.failed(e)
        else:
            fails.ok()

    # ---------------------------------------------------------------- live ticks
    def on_town(self) -> bool:
        """Is the window showing the town page (not the details page or the error page)?"""
        if self.closing:
            return False
        try:
            return url_file_name(self.w.get_current_url()) == self.town_name
        except Exception:
            return False

    def live_tick(self, push: bool = True) -> bool:
        """Run the Watcher once (it writes town-live.js), update the title and, when ``push``,
        hand the payload to the page. True when it pushed."""
        self.live_ticks += 1
        if self.feed is None:
            return False
        out = self.feed.tick(self.rev)
        payload = getattr(out, "payload", None)
        if not isinstance(payload, dict):
            return False
        self.need = payload.get("need", 0)
        self.update_title()
        if not push or not self.on_town() or self.closing:
            return False
        self.w.evaluate_js(
            f"typeof lamptownLive === 'function' && lamptownLive({js_value(payload)})")
        self.live_pushes += 1
        return True

    def update_title(self) -> None:
        title = badge_title(self.plain, self.need)
        if title == self.shown_title or self.closing:
            return
        try:
            fn = getattr(self.w, "set_title", None)
            if callable(fn):
                fn(title)
            else:
                self.w.title = title
        except Exception as e:
            msg = f"couldn't set the window title: {type(e).__name__}: {e}"
            if msg not in self._said:
                self._said.add(msg)
                self.log(msg)
            return
        self.shown_title = title

    # ---------------------------------------------------------------- rebuilds
    def tick(self) -> bool:
        """Rebuild once; push the new data into the page, or reload it when its structure
        changed. True when it reloaded."""
        self.ticks += 1
        data = self.rebuild()
        if data is None:
            return False
        self.rev = _rev(data) or self.rev
        if window_title(data) != self.plain:
            self.plain = window_title(data)
            self.update_title()
        fp, shape = fingerprint(data), structure(data)
        if fp is None or fp == self.last or self.closing:
            return False
        page = url_file_name(self.w.get_current_url())
        if page == DETAILS_NAME:
            # on the details page: it reads fresh data when it is opened, and the town page
            # loads fresh when the user goes back to it
            self.last, self.shape = fp, shape
            return False
        if page == self.town_name and shape is not None and shape == self.shape:
            ok = self.w.evaluate_js(
                f"typeof lamptownData === 'function' && lamptownData({js_value(data)})")
            if ok is not False:  # None: a backend that doesn't hand results back
                self.last = fp  # only now: if the push threw, the next tick tries again
                self.pushes += 1
                return False
            self.log("the page refused the new data, reloading it")
        return self.reload(fp, shape)

    def reload(self, fp: str | None, shape: str | None) -> bool:
        # ?r= forces a real reload (a URL that differs only after '#' just scrolls the old page);
        # the hash reopens what was open.
        if self.closing:
            return False
        base = json.dumps(f"{self.town_url}?r={self.clock():%H%M%S%f}")
        self.log("the town changed shape, reloading it")
        self.w.evaluate_js(
            f"location.replace({base} + (typeof viewHash === 'function' ? viewHash() : ''))"
        )
        self.last, self.shape = fp, shape  # only now: if the reload threw, the next tick retries
        self.reloads += 1
        return True


def _rev(data: dict[str, Any] | None) -> str | None:
    rev = (data or {}).get("rev") if isinstance(data, dict) else None
    return rev if isinstance(rev, str) and rev else None
