"""Desktop app logic with a fake window (no GUI needed, runs on every OS): the live loop, the data
fingerprint, the visibility state machine, in-process builds, per-OS folders, the capped log and
the CLI wiring."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lamptown.app import cli as app_cli
from lamptown.app.applog import AppLog
from lamptown.app.links import LINKS_JS
from lamptown.app.live import (
    VISIBILITY_JS,
    Live,
    Visibility,
    fingerprint,
    url_file_name,
)
from lamptown.app.paths import app_paths
from lamptown.app.window import Builder, _title, error_page

EVENTS = ("minimized", "restored", "maximized", "shown", "loaded", "closed")

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

[[building]]
id = "bell"
type = "jobs"
label = "Bell tower"
  [[building.source]]
  kind = "static"
  state = "ok"
"""


# ----------------------------------------------------------------------------- fakes
class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self, *args) -> None:
        for h in list(self.handlers):
            h(*args)


class FakeWindow:
    """Just enough of a pywebview Window for Live."""

    def __init__(self, url: str = "file:///town/town.html") -> None:
        self.events = SimpleNamespace(**{n: FakeEvent() for n in EVENTS})
        self.url = url
        self.js: list[str] = []
        self.fail = False
        self.doc_hidden: bool | None = False
        self.calls: list[str] = []
        self._on_top = False

    def get_current_url(self) -> str:
        return self.url

    def evaluate_js(self, js: str):
        if self.fail:
            raise RuntimeError("the web view is gone")
        if js == "document.hidden":
            self.calls.append("probe")
            return self.doc_hidden
        self.js.append(js)
        return None

    def restore(self) -> None:
        self.calls.append("restore")

    def show(self) -> None:
        self.calls.append("show")

    @property
    def on_top(self) -> bool:
        return self._on_top

    @on_top.setter
    def on_top(self, v: bool) -> None:
        self.calls.append(f"on_top={v}")
        self._on_top = v


class Counter:
    """A stand-in builder: returns ``data`` (a dict) and counts calls."""

    DEFAULT = {"rooms": {"a": 1}, "built": "Mon 1 Jan, 10:00"}

    def __init__(self, data: dict | None = DEFAULT, delay: float = 0.0) -> None:
        self.data = data  # None: every build fails
        self.delay = delay  # a slow build (or a loaded runner) between the loop's check and it
        self.n = 0

    def __call__(self):
        if self.delay:
            time.sleep(self.delay)
        self.n += 1
        return None if self.data is None else dict(self.data)


def wait_for(cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def hidden_cycles(w: FakeWindow, k: int, timeout: float = 10.0) -> bool:
    """Wait until the hidden loop has asked the page ``k`` more times (``k`` loop turns)."""
    start = w.calls.count("probe")
    return wait_for(lambda: w.calls.count("probe") >= start + k, timeout)


@pytest.fixture
def town_uri(tmp_path: Path) -> str:
    return (tmp_path / "town.html").as_uri()


# ----------------------------------------------------------------------------- fingerprint
def test_fingerprint_ignores_the_build_time():
    a = {"rooms": {"x": {"state": "ok"}}, "built": "Mon 1 Jan, 10:00",
         "builtIso": "2026-01-01T10:00"}
    b = dict(a, built="Mon 1 Jan, 10:15", builtIso="2026-01-01T10:15")
    assert fingerprint(a) == fingerprint(b)
    c = dict(a, rooms={"x": {"state": "waiting"}})
    assert fingerprint(c) != fingerprint(a)


def test_fingerprint_is_order_independent_and_none_safe():
    assert fingerprint({"a": 1, "b": [1, 2]}) == fingerprint({"b": [1, 2], "a": 1})
    assert fingerprint(None) is None
    assert fingerprint([1, 2]) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("file:///srv/t/town.html?r=1#room=docks", "town.html"),
        ("file:///home/you/town/town-details.html#b-docks", "town-details.html"),
        ("file:///t/my%20town/town.html", "town.html"),
        # WebView2 through pywebview reports the query encoded
        ("file:///srv/t/town.html%3Fr=122201#room=docks", "town.html"),
        ("file:///srv/t/town-details.html%23b-docks", "town-details.html"),
        ("", ""),
        (None, ""),
        ("about:blank", "blank"),
    ],
)
def test_url_file_name(url, name):
    assert url_file_name(url) == name


# ----------------------------------------------------------------------------- visibility
def test_visibility_window_events():
    v = Visibility()
    assert not v.hidden
    assert v.window_event("minimized") is False
    assert v.hidden
    # a minimised window that comes back maximised fires only "maximized"
    assert v.window_event("maximized") is True
    assert not v.hidden
    v.window_event("minimized")
    assert v.window_event("restored") is True
    v.window_event("minimized")
    assert v.window_event("shown") is True
    assert v.window_event("resized") is False  # unknown events change nothing


def test_visibility_page_signals():
    v = Visibility()
    assert v.page_event(True) is False
    assert v.hidden
    assert v.page_event(False) is True
    # a visible page also clears a minimised flag whose restore event never came
    v.window_event("minimized")
    assert v.page_event(False) is True
    assert not v.hidden


def test_visibility_poll_cannot_wake_a_minimised_window():
    """A backend whose page never reports hidden must not restart rebuilds while minimised."""
    v = Visibility()
    v.window_event("minimized")
    assert v.page_poll(False) is False
    assert v.hidden
    v2 = Visibility()
    v2.page_event(True)
    assert v2.page_poll(False) is True  # but a poll can clear the page's own flag
    assert not v2.hidden


# ----------------------------------------------------------------------------- Live.tick
def test_tick_pushes_only_when_the_data_changes(town_uri):
    w = FakeWindow()
    b = Counter()
    live = Live(w, b, town_uri, data=b.data)
    assert live.tick() is False  # same data
    assert w.js == []
    b.data = dict(b.data, built="Mon 1 Jan, 10:15")  # only the clock moved
    assert live.tick() is False
    assert w.js == []
    b.data = dict(b.data, rooms={"a": 2})
    assert live.tick() is False  # pushed, not reloaded
    assert len(w.js) == 1
    assert w.js[0].startswith("typeof lamptownData === 'function' && lamptownData(")
    assert json.loads(w.js[0].split("lamptownData(", 1)[1][:-1])["rooms"] == {"a": 2}
    assert live.tick() is False  # stored: no second push for the same data
    assert (live.ticks, live.reloads, live.pushes) == (4, 0, 1)


def test_tick_reloads_when_the_shape_is_unknown(town_uri):
    """A Live told only the old fingerprint can't know the page's structure: a change reloads."""
    w = FakeWindow()
    b = Counter()
    live = Live(w, b, town_uri, last="old")
    assert live.tick() is True
    js = w.js[0]
    assert js.startswith("location.replace(" + json.dumps(town_uri + "?r=")[:-1])
    assert "viewHash()" in js  # reopens the room / card the user had open
    assert live.tick() is False


def test_tick_failed_build_keeps_the_page(town_uri):
    w = FakeWindow()
    live = Live(w, Counter(None), town_uri, last="old")
    assert live.tick() is False
    assert w.js == [] and live.last == "old"


def test_tick_failed_reload_retries_next_time(town_uri):
    w = FakeWindow()
    b = Counter()
    live = Live(w, b, town_uri, last="old")
    w.fail = True
    with pytest.raises(RuntimeError):
        live.tick()
    assert live.last == "old"  # not stored, so the next tick tries again
    w.fail = False
    assert live.tick() is True
    assert live.last == fingerprint(b.data)


def test_tick_on_the_details_page_does_not_reload(town_uri):
    w = FakeWindow(url="file:///t/town-details.html#b-docks")
    b = Counter()
    live = Live(w, b, town_uri, last="old")
    assert live.tick() is False
    assert w.js == []
    assert live.last == fingerprint(b.data)


def test_tick_from_the_error_page_loads_the_town(town_uri):
    w = FakeWindow(url="about:blank")  # the window opened on "can't build yet"
    live = Live(w, Counter(), town_uri, last=None)
    assert live.tick() is True
    assert town_uri in w.js[0]


def test_reload_urls_differ_each_time(town_uri):
    w = FakeWindow()
    b = Counter()
    times = iter(["10:00:00.000001", "10:00:00.000002"])
    import datetime as dt

    live = Live(w, b, town_uri, last="x",
                clock=lambda: dt.datetime.strptime(next(times), "%H:%M:%S.%f"))
    live.tick()
    b.data = dict(b.data, rooms={"a": 3})
    live.tick()
    assert w.js[0] != w.js[1]


# ----------------------------------------------------------------------------- signals
def test_loaded_installs_the_link_guard_and_the_visibility_watch(town_uri):
    w = FakeWindow(town_uri)
    Live(w, Counter(), town_uri)
    w.events.loaded.fire()
    assert w.js == [LINKS_JS, VISIBILITY_JS]
    assert "visibilitychange" in VISIBILITY_JS and "pywebviewready" in VISIBILITY_JS


def test_loaded_survives_a_dead_page(town_uri):
    w = FakeWindow(town_uri)
    logs: list[str] = []
    Live(w, Counter(), town_uri, log=logs.append)
    w.fail = True
    w.events.loaded.fire()  # must not raise
    assert len(logs) == 2 and "links" in logs[0] and "visibility" in logs[1]


def test_focus_restores_only_a_minimised_window(town_uri):
    w = FakeWindow()
    live = Live(w, Counter(), town_uri)
    live.focus()
    assert w.calls == ["show", "on_top=True", "on_top=False"]
    w.calls.clear()
    w.events.minimized.fire()
    live.focus()
    assert w.calls[0] == "restore"


def test_closed_stops_the_loop(town_uri):
    w = FakeWindow()
    live = Live(w, Counter(), town_uri, poll=30)
    t = threading.Thread(target=live.run, daemon=True)
    t.start()
    w.events.closed.fire()
    t.join(2)
    assert not t.is_alive()


# ----------------------------------------------------------------------------- the loop
SLOW = pytest.mark.parametrize("delay", [0.0, 0.3], ids=["fast-build", "slow-build"])


@SLOW
def test_loop_pauses_while_minimised_and_catches_up(town_uri, delay):
    w = FakeWindow()
    b = Counter(delay=delay)
    live = Live(w, b, town_uri, last=fingerprint(b.data), poll=0.05, hidden_check=0.05)
    t = threading.Thread(target=live.run, daemon=True)
    t.start()
    try:
        assert wait_for(lambda: b.n >= 3), "no rebuilds while showing"
        w.events.minimized.fire()
        # the loop asks the page only from its hidden branch: once it has, a rebuild already
        # running has finished (one thread), so the count is final
        assert wait_for(lambda: "probe" in w.calls), "never asked the page while hidden"
        n = b.n
        assert hidden_cycles(w, 5), "the hidden loop stopped asking the page"
        assert b.n == n, "rebuilt while minimised"
        # restore to maximised: only "maximized" fires; the catch-up comes at once
        live.poll = 30
        w.events.maximized.fire()
        assert wait_for(lambda: b.n > n, 2), "no catch-up after the window came back"
    finally:
        live.on_closed()
        t.join(2)
    assert not t.is_alive()


@SLOW
def test_loop_pauses_while_the_page_is_hidden(town_uri, delay):
    w = FakeWindow()
    b = Counter(delay=delay)
    live = Live(w, b, town_uri, last=fingerprint(b.data), poll=0.05, hidden_check=0.05)
    t = threading.Thread(target=live.run, daemon=True)
    t.start()
    try:
        assert wait_for(lambda: b.n >= 2)
        w.doc_hidden = True
        live.on_page_visibility(True)  # what the page pushes through the js_api
        assert wait_for(lambda: "probe" in w.calls), "never asked the page while hidden"
        n = b.n  # final: see test_loop_pauses_while_minimised_and_catches_up
        assert hidden_cycles(w, 5), "the hidden loop stopped asking the page"
        assert b.n == n
        # no event this time: the 'document.hidden' poll notices the page is back
        w.doc_hidden = False
        assert wait_for(lambda: b.n > n, 2)
    finally:
        live.on_closed()
        t.join(2)


def test_loop_survives_a_crashing_build(town_uri):
    w = FakeWindow()
    logs: list[str] = []
    calls = []

    def boom():
        calls.append(1)
        raise ValueError("collector exploded")

    live = Live(w, boom, town_uri, poll=0.02, log=logs.append)
    t = threading.Thread(target=live.run, daemon=True)
    t.start()
    try:
        assert wait_for(lambda: len(calls) >= 3)
    finally:
        live.on_closed()
        t.join(2)
    assert any("tick failed" in m and "collector exploded" in m for m in logs)


# ----------------------------------------------------------------------------- real builds
def write_config(folder: Path, done: int = 3) -> Path:
    p = folder / "lamptown.toml"
    p.write_text(CONFIG.format(done=done), encoding="utf-8")
    return p


def test_builder_builds_in_process_and_fingerprints_real_data(tmp_path):
    cfg = write_config(tmp_path)
    logs: list[str] = []
    b = Builder(str(cfg), str(tmp_path / "out"), logs.append)
    first = b()
    assert first is not None, logs
    assert b.town == tmp_path / "out" / "town.html" and b.town.is_file()
    assert first["rooms"]["docks"]["state"] == "working"
    time.sleep(1.1)  # a later build time ...
    second = b()
    assert second["built"] != first["built"] or second["builtIso"] != first["builtIso"]
    assert fingerprint(second) == fingerprint(first)  # ... is not a change
    write_config(tmp_path, done=4)
    third = b()
    assert fingerprint(third) != fingerprint(first)


def test_builder_keeps_the_last_page_on_a_broken_config(tmp_path):
    cfg = write_config(tmp_path)
    logs: list[str] = []
    b = Builder(str(cfg), str(tmp_path / "out"), logs.append)
    assert b() is not None
    page = b.town.read_text(encoding="utf-8")
    cfg.write_text("version = 1\n[[building]]\nid = 'Bad Id!'\n", encoding="utf-8")
    assert b() is None
    assert b() is None
    assert len(logs) == 1, "a broken config is logged once, not every poll"
    assert b.town.read_text(encoding="utf-8") == page
    write_config(tmp_path)
    assert b() is not None and b.error is None


def test_live_with_a_real_build_pushes_once_per_change(tmp_path):
    cfg = write_config(tmp_path)
    b = Builder(str(cfg), str(tmp_path / "out"), lambda m: None)
    data = b()
    w = FakeWindow(url=b.town.as_uri())
    live = Live(w, b, b.town.as_uri(), data=data)
    assert live.tick() is False
    write_config(tmp_path, done=6)
    assert live.tick() is False
    assert live.tick() is False
    assert len(w.js) == 1 and "lamptownData(" in w.js[0]
    assert (live.pushes, live.reloads) == (1, 0)
    # a new building changes the town's shape: the page reloads (its room script must load)
    cfg.write_text(CONFIG.format(done=6) + """
[[building]]
id = "kiosk"
type = "kiosk"
plot = "lab"
label = "Tea kiosk"
  [[building.source]]
  kind = "static"
  state = "ok"
""", encoding="utf-8")
    assert live.tick() is True
    assert w.js[-1].startswith("location.replace(")
    assert (live.pushes, live.reloads) == (1, 1)


def test_error_page_escapes_the_message():
    page = error_page("bad <script>alert(1)</script> & co")
    assert "<script>alert" not in page and "&lt;script&gt;" in page


def test_title():
    assert _title({"title": "Harbour Watch"}) == "Harbour Watch - Lamptown"
    assert _title({"title": "Lamptown"}) == "Lamptown"
    assert _title(None) == "Lamptown"


# ----------------------------------------------------------------------------- folders + log
def test_app_paths_windows():
    p = app_paths(platform="win32", env={"LOCALAPPDATA": str(Path("/la").resolve())},
                  home=Path("/h"))
    base = Path("/la").resolve() / "lamptown" / "app"
    assert p.log == base / "app.log" and p.webview == base / "webview" and p.lock_dir == base


def test_app_paths_windows_without_localappdata():
    p = app_paths(platform="win32", env={}, home=Path("/h"))
    assert p.lock_dir == Path("/h") / "AppData" / "Local" / "lamptown" / "app"


def test_app_paths_macos():
    p = app_paths(platform="darwin", env={}, home=Path("/h"))
    assert p.log == Path("/h/Library/Logs/lamptown/app.log")
    assert p.webview == Path("/h/Library/Application Support/lamptown/app/webview")


def test_app_paths_linux_xdg():
    p = app_paths(platform="linux", env={}, home=Path("/h"))
    assert p.log == Path("/h/.local/state/lamptown/app.log")
    assert p.webview == Path("/h/.local/share/lamptown/webview")
    xs, xd = Path("/xs").resolve(), Path("/xd").resolve()
    p = app_paths(platform="linux", env={"XDG_STATE_HOME": str(xs), "XDG_DATA_HOME": str(xd)},
                  home=Path("/h"))
    assert p.log == xs / "lamptown" / "app.log" and p.webview == xd / "lamptown" / "webview"
    # XDG: relative values are ignored
    p = app_paths(platform="linux", env={"XDG_STATE_HOME": "rel"}, home=Path("/h"))
    assert p.lock_dir == Path("/h/.local/state/lamptown")


def test_app_paths_override(tmp_path):
    p = app_paths(platform="linux", env={"LAMPTOWN_APP_DIR": str(tmp_path)}, home=Path("/h"))
    assert p.log == tmp_path / "app.log" and p.webview == tmp_path / "webview"
    q = app_paths(tmp_path / "x", platform="win32", env={}, home=Path("/h"))
    assert q.lock_dir == tmp_path / "x"
    q.ensure()
    assert q.webview.is_dir() and q.icons.is_dir()


def test_log_is_capped(tmp_path):
    log = AppLog(tmp_path / "app.log", max_bytes=4096)
    (tmp_path / "app.log").write_text("x" * 10_000, encoding="utf-8")
    for i in range(600):
        log(f"line {i} " + "y" * 40)
    files = sorted(tmp_path.glob("app*.log"))
    assert [f.name for f in files] == ["app.log", "app.old.log"]
    assert (tmp_path / "app.log").stat().st_size <= 4096 + 200
    assert sum(f.stat().st_size for f in files) <= 2 * 4096 + 400
    assert "line 599 " in (tmp_path / "app.log").read_text(encoding="utf-8")


def test_log_never_raises(tmp_path):
    (tmp_path / "blocker").write_text("a file where a folder should be", encoding="utf-8")
    log = AppLog(tmp_path / "blocker" / "app.log")
    log("hello")  # can't be written; must not raise
    echoed: list[str] = []
    AppLog(None, echo=echoed.append)("to the console only")
    assert echoed and echoed[0].endswith("to the console only")


# ----------------------------------------------------------------------------- CLI
def test_add_parser_registers_app_and_shortcut():
    p = argparse.ArgumentParser(prog="lamptown")
    sub = p.add_subparsers(dest="command")
    app_cli.add_parser(sub)
    a = p.parse_args(["app", "--config", "x.toml", "--poll", "2.5", "--state-dir", "s"])
    assert a.func is app_cli.cmd_app and a.poll == 2.5 and a.config == "x.toml"
    s = p.parse_args(["shortcut", "--dir", "d", "--name", "Town"])
    assert s.func is app_cli.cmd_shortcut and s.dir == "d" and s.name == "Town"
    with pytest.raises(SystemExit):
        p.parse_args(["app", "--poll", "0"])


def test_module_entry_point_help():
    r = subprocess.run([sys.executable, "-m", "lamptown.app", "--help"], capture_output=True,
                       text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    assert "--state-dir" in r.stdout and "--poll" in r.stdout


def test_cli_module_stays_light():
    """Registering `lamptown app` must not load the builder or any GUI module."""
    code = ("import sys, lamptown.app.cli; "
            "print(sorted(m for m in ('lamptown.build', 'webview') if m in sys.modules))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                       check=False)
    assert r.stdout.strip() == "[]", r.stderr


def test_details_name_matches_the_builder():
    from lamptown.app.live import DETAILS_NAME
    from lamptown.build import DETAILS_NAME as built

    assert built == DETAILS_NAME


# ----------------------------------------------------------------------------- fallbacks
@pytest.fixture
def opened(monkeypatch):
    urls: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: urls.append(url) or True)
    return urls


def test_without_pywebview_the_town_opens_in_the_browser(tmp_path, monkeypatch, opened, capsys):
    from lamptown.app.window import EXIT_FALLBACK, run_app

    monkeypatch.setitem(sys.modules, "webview", None)  # "import webview" now fails
    cfg = write_config(tmp_path)
    rc = run_app(str(cfg), str(tmp_path / "out"), state_dir=tmp_path / "state")
    assert rc == EXIT_FALLBACK
    assert opened == [(tmp_path / "out" / "town.html").resolve().as_uri()]
    err = capsys.readouterr().err
    assert 'pip install "lamptown[app]"' in err and "browser" in err
    assert "pywebview unavailable" in (tmp_path / "state" / "app.log").read_text(encoding="utf-8")
    # the lock was released: a next start isn't told another copy is running
    from lamptown.app.instance import SingleInstance

    s = SingleInstance(tmp_path / "state")
    assert s.acquire()
    s.release()


class _NoBackendWebview:
    """A pywebview whose GUI backend is missing: start() raises like it does on a bare Linux."""

    settings: dict = {}
    screens: list = []

    @staticmethod
    def create_window(*_a, **_k):
        return FakeWindow()

    @staticmethod
    def start(*_a, **_k):
        raise RuntimeError("You must have either QT or GTK with Python extensions installed")


def test_missing_gui_backend_on_linux_explains_the_extra(tmp_path, monkeypatch, opened, capsys):
    from lamptown.app import window

    monkeypatch.setitem(sys.modules, "webview", _NoBackendWebview)
    monkeypatch.setattr(window.sys, "platform", "linux")
    cfg = write_config(tmp_path)
    rc = window.run_app(str(cfg), str(tmp_path / "out"), state_dir=tmp_path / "state")
    assert rc == window.EXIT_FALLBACK
    err = capsys.readouterr().err
    assert 'pywebview[gtk]' in err and 'pywebview[qt]' in err
    assert opened and opened[0].endswith("town.html")


def test_a_second_launch_wakes_the_first(tmp_path, capsys):
    from lamptown.app.instance import SingleInstance
    from lamptown.app.window import EXIT_OK, run_app

    got: list[str] = []
    first = SingleInstance(tmp_path / "state")
    first.acquire()
    first.serve(got.append)
    try:
        assert run_app(state_dir=tmp_path / "state") == EXIT_OK
        assert wait_for(lambda: got == ["focus"], 3)
        assert "already open" in capsys.readouterr().err
    finally:
        first.release()


# ----------------------------------------------------------------------------- a slow first build
class _QuickWebview:
    """A pywebview whose window opens and is closed at once (start() returns)."""

    settings: dict = {}
    screens: list = []
    started: list = []

    @staticmethod
    def create_window(*_a, **_k):
        return FakeWindow()

    @classmethod
    def start(cls, *_a, **_k):
        cls.started.append(1)


@pytest.fixture
def slow_build(monkeypatch):
    """The first copy's build waits for ``go`` (a slow first build); ``building`` is set once
    it has started."""
    from lamptown.app import window

    building, go = threading.Event(), threading.Event()

    class SlowBuilder(window.Builder):
        def __call__(self):
            building.set()
            assert go.wait(30), "the test never let the build finish"
            return super().__call__()

    monkeypatch.setattr(window, "Builder", SlowBuilder)
    _QuickWebview.started = []
    monkeypatch.setitem(sys.modules, "webview", _QuickWebview)
    return SimpleNamespace(building=building, go=go)


def _first_copy(tmp_path) -> tuple[threading.Thread, list]:
    from lamptown.app.window import run_app

    rc: list = []
    cfg = write_config(tmp_path)
    t = threading.Thread(target=lambda: rc.append(run_app(
        str(cfg), str(tmp_path / "out"), state_dir=tmp_path / "state", echo=False)), daemon=True)
    t.start()
    return t, rc


def test_a_second_launch_during_a_slow_first_build_is_answered(tmp_path, slow_build, capsys):
    from lamptown.app.window import EXIT_OK, run_app

    t, rc = _first_copy(tmp_path)
    try:
        assert slow_build.building.wait(10)
        began = time.monotonic()
        assert run_app(state_dir=tmp_path / "state", echo=False) == EXIT_OK
        assert time.monotonic() - began < 4, "the second copy waited for an answer"
        assert "already open" in capsys.readouterr().err
    finally:
        slow_build.go.set()
        t.join(30)
    assert rc == [EXIT_OK] and _QuickWebview.started == [1]
    log = (tmp_path / "state" / "app.log").read_text(encoding="utf-8")
    assert "second launch while the town builds" in log


def test_a_quit_during_the_first_build_never_opens_the_window(tmp_path, slow_build):
    from lamptown.app.instance import SingleInstance
    from lamptown.app.window import EXIT_OK

    t, rc = _first_copy(tmp_path)
    try:
        assert slow_build.building.wait(10)
        assert SingleInstance(tmp_path / "state").send("quit", timeout=3)
    finally:
        slow_build.go.set()
        t.join(30)
    assert rc == [EXIT_OK] and _QuickWebview.started == []


# ----------------------------------------------------------------------------- off the town
class NavWindow(FakeWindow):
    """A FakeWindow with pywebview's ``load_url``."""

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.loaded_urls: list[str] = []

    def load_url(self, url: str) -> None:
        self.loaded_urls.append(url)


@pytest.mark.parametrize("where", [
    "https://example.com/kite",  # a web link followed before the link guard was in
    "file:///tmp/notes/probe.txt",  # a file link
    "town.html@elsewhere",  # a town page in another folder
])
def test_a_window_that_left_the_town_goes_back(town_uri, where):
    url = (town_uri.rsplit("/", 2)[0] + "/elsewhere/town.html") if "@" in where else where
    w = NavWindow(url)
    logs: list[str] = []
    Live(w, Counter(), town_uri, log=logs.append)
    w.events.loaded.fire()
    assert w.loaded_urls == [town_uri]
    assert LINKS_JS not in w.js  # nothing of ours goes into a page that isn't the town's
    assert any("left the town" in m for m in logs)
    assert not any(where.split("@")[0] in m for m in logs), logs  # never the page's address


@pytest.mark.parametrize("suffix", ["", "?r=101530#room=docks", "%3Fr=1", "#open=docks"])
def test_the_towns_own_pages_stay(town_uri, suffix):
    for page in ("town.html", "town-details.html"):
        w = NavWindow(town_uri.rsplit("/", 1)[0] + "/" + page + suffix)
        Live(w, Counter(), town_uri)
        w.events.loaded.fire()
        assert w.loaded_urls == [] and w.js == [LINKS_JS, VISIBILITY_JS], (page, suffix)


@pytest.mark.parametrize("url", [None, "", "about:blank", "data:text/html,<p>x</p>"])
def test_the_inline_error_page_stays(town_uri, url):
    w = NavWindow(url)  # type: ignore[arg-type]
    Live(w, Counter(), town_uri)
    w.events.loaded.fire()
    assert w.loaded_urls == [] and w.js == [LINKS_JS, VISIBILITY_JS]


def test_without_load_url_the_page_is_sent_back_by_script(town_uri):
    w = FakeWindow("https://example.com/kite")
    Live(w, Counter(), town_uri)
    w.events.loaded.fire()
    assert w.js == [f"location.replace({json.dumps(town_uri)})"]
