"""The desktop app's live town with a fake window (no GUI): pushes instead of reloads, a reload
only when the town changes shape, the title badge, the two cadences, what runs while the window
is hidden, and the app's one Watcher (a real one, on a status folder in ``tmp_path``)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lamptown.app.live import (
    HIDDEN_CHECK,
    HIDDEN_WATCH,
    Live,
    badge_title,
    structure,
    window_title,
)
from lamptown.livefeed import Builder, LiveFeed
from lamptown.status.store import update_status
from lamptown.status.watch import Watcher
from test_app_live import Counter, FakeWindow

TOWN = "file:///town/town.html"
DATA = {
    "spec": "lamptown-town/1", "version": "0.2.0", "title": "Harbour Watch",
    "built": "Mon 1 Jan, 10:00", "rev": "r1",
    "plots": {"boathouse": "docks", "tower": "bell"},
    "parts": {"docks": {"id": "docks", "type": "harbour", "plot": "boathouse"},
              "bell": {"id": "bell", "type": "jobs", "plot": "tower"}},
    "rooms": {"docks": {"state": "working"}, "bell": {"state": "ok"}},
}


def changed(data: dict, **kw) -> dict:
    return json.loads(json.dumps({**data, **kw}))


class TitledWindow(FakeWindow):
    """A FakeWindow with pywebview's ``set_title``."""

    def __init__(self, url: str = TOWN) -> None:
        super().__init__(url)
        self.titles: list[str] = []

    def set_title(self, title: str) -> None:
        self.titles.append(title)


class FakeFeed:
    """Stands in for LiveFeed: hands back a payload with ``need`` and records the rev asked."""

    def __init__(self, need: int = 0, every: float = 3.0) -> None:
        self.need = need
        self.every = every
        self.revs: list[str | None] = []

    def tick(self, rev=None):
        self.revs.append(rev)
        return SimpleNamespace(payload={"spec": "lamptown-live/1", "at": "2026-11-04T10:00:00Z",
                                        "rev": rev, "need": self.need, "agents": []})


def pushed(w: FakeWindow, fn: str) -> list[dict]:
    """The payloads pushed into the page through ``fn(...)``."""
    head = f"typeof {fn} === 'function' && {fn}("
    return [json.loads(j[len(head):-1]) for j in w.js if j.startswith(head)]


def reloads(w: FakeWindow) -> list[str]:
    return [j for j in w.js if j.startswith("location.replace(")]


# ----------------------------------------------------------------------------- push, not reload
def test_a_data_change_is_pushed_and_never_reloads():
    w, b = TitledWindow(), Counter(DATA)
    live = Live(w, b, TOWN, data=DATA)
    b.data = changed(DATA, rooms={"docks": {"state": "waiting"}, "bell": {"state": "ok"}},
                     rev="r2")
    assert live.tick() is False
    assert reloads(w) == []
    (d,) = pushed(w, "lamptownData")
    assert d["rooms"]["docks"]["state"] == "waiting" and d["rev"] == "r2"
    assert live.rev == "r2"  # the next live payload asks for this rev
    assert (live.pushes, live.reloads) == (1, 0)


def test_a_new_lighthouse_or_room_text_is_still_a_push():
    """``rooms`` and a building's words change all the time while agents work: no reload."""
    w, b = TitledWindow(), Counter(DATA)
    live = Live(w, b, TOWN, data=DATA)
    parts = json.loads(json.dumps(DATA["parts"]))
    parts["docks"]["status"] = "Two boats being built."
    b.data = changed(DATA, parts=parts)
    live.tick()
    assert reloads(w) == [] and live.pushes == 1


@pytest.mark.parametrize("change", [
    {"title": "Kite Field"},
    {"version": "0.3.0"},
    {"spec": "lamptown-town/2"},
    {"plots": {"boathouse": "docks", "tower": None, "lab": "bell"}},
    {"parts": {"docks": {"id": "docks", "type": "kiosk", "plot": "boathouse"},
               "bell": {"id": "bell", "type": "jobs", "plot": "tower"}}},
    {"parts": {"docks": {"id": "docks", "type": "harbour", "plot": "boathouse"},
               "bell": {"id": "bell", "type": "jobs", "plot": "tower"},
               "lighthouse": {"id": "lighthouse", "type": "lighthouse", "plot": "lighthouse"}}},
])
def test_a_change_of_shape_reloads(change):
    w, b = TitledWindow(), Counter(DATA)
    live = Live(w, b, TOWN, data=DATA)
    b.data = changed(DATA, **change)
    assert structure(b.data) != structure(DATA)
    assert live.tick() is True
    assert len(reloads(w)) == 1 and pushed(w, "lamptownData") == []
    assert "viewHash()" in reloads(w)[0]
    assert live.tick() is False  # the new shape is stored
    assert live.reloads == 1


def test_a_page_that_refuses_the_data_is_reloaded():
    class Refusing(TitledWindow):
        def evaluate_js(self, js):
            super().evaluate_js(js)
            return False if "lamptownData(" in js else None

    w, b = Refusing(), Counter(DATA)
    live = Live(w, b, TOWN, data=DATA)
    b.data = changed(DATA, rooms={})
    assert live.tick() is True
    assert live.pushes == 0 and len(reloads(w)) == 1


def test_a_failed_push_is_tried_again():
    w, b = TitledWindow(), Counter(DATA)
    live = Live(w, b, TOWN, data=DATA)
    b.data = changed(DATA, rooms={})
    w.fail = True
    with pytest.raises(RuntimeError):
        live.tick()
    w.fail = False
    assert live.tick() is False and live.pushes == 1


def test_the_error_page_still_loads_the_town():
    w, b = TitledWindow(url="about:blank"), Counter(DATA)
    live = Live(w, b, TOWN, data=None)  # the window opened on "can't build yet"
    assert live.tick() is True and reloads(w)


def test_on_the_details_page_nothing_is_pushed_or_reloaded():
    w, b = TitledWindow(url="file:///town/town-details.html#b-docks"), Counter(DATA)
    feed = FakeFeed(need=1)
    live = Live(w, b, TOWN, data=DATA, feed=feed)
    b.data = changed(DATA, title="Kite Field")
    live.tick()
    live.live_tick()
    assert w.js == [] and live.reloads == 0
    # the window title follows the town's new name and still counts
    assert w.titles == ["Kite Field - Lamptown", "1 needs you - Kite Field - Lamptown"]


# ----------------------------------------------------------------------------- live ticks
def test_a_live_tick_pushes_the_payload_with_the_data_rev():
    w, feed = TitledWindow(), FakeFeed(need=0)
    live = Live(w, Counter(DATA), TOWN, data=DATA, feed=feed)
    assert live.live_tick() is True
    assert feed.revs == ["r1"]
    (p,) = pushed(w, "lamptownLive")
    assert p["spec"] == "lamptown-live/1" and p["rev"] == "r1" and p["at"]
    assert live.live_pushes == 1 and live.reloads == 0


def test_a_live_tick_without_a_feed_or_payload_does_nothing():
    w = TitledWindow()
    assert Live(w, Counter(DATA), TOWN, data=DATA).live_tick() is False
    off = SimpleNamespace(every=3.0, tick=lambda rev=None: None)  # live is off in the config
    assert Live(w, Counter(DATA), TOWN, data=DATA, feed=off).live_tick() is False
    assert w.js == [] and w.titles == []


# ----------------------------------------------------------------------------- the title badge
def test_badge_title():
    plain = window_title({"title": "Harbour Watch"})
    assert plain == "Harbour Watch - Lamptown"
    assert badge_title(plain, 2) == "2 need you - Harbour Watch - Lamptown"
    assert badge_title(plain, 1) == "1 needs you - Harbour Watch - Lamptown"
    for nothing in (0, -1, None, True, "2", 1.5):
        assert badge_title(plain, nothing) == plain


def test_the_title_counts_who_needs_you_and_goes_back_to_plain():
    w, feed = TitledWindow(), FakeFeed(need=2)
    live = Live(w, Counter(DATA), TOWN, data=DATA, feed=feed)
    live.live_tick()
    live.live_tick()  # the same count: not set again
    assert w.titles == ["2 need you - Harbour Watch - Lamptown"]
    feed.need = 1
    live.live_tick()
    feed.need = 0
    live.live_tick()
    assert w.titles[1:] == ["1 needs you - Harbour Watch - Lamptown", "Harbour Watch - Lamptown"]


def test_the_title_falls_back_to_the_title_attribute():
    w = FakeWindow()  # no set_title
    w.title = "Harbour Watch - Lamptown"
    feed = FakeFeed(need=3)
    Live(w, Counter(DATA), TOWN, data=DATA, feed=feed).live_tick()
    assert w.title == "3 need you - Harbour Watch - Lamptown"


def test_a_window_that_cannot_take_a_title_is_logged_once():
    class NoTitle(FakeWindow):
        def set_title(self, title):
            raise RuntimeError("the window is closing")

    logs: list[str] = []
    feed = FakeFeed(need=1)
    live = Live(NoTitle(), Counter(DATA), TOWN, data=DATA, feed=feed, log=logs.append)
    live.live_tick()
    live.live_tick()
    assert len([m for m in logs if "title" in m]) == 1


# ----------------------------------------------------------------------------- the cadences
def run_for(live: Live, seconds: float, start: float = 0.0) -> float:
    """Drive ``live.step`` on a fake monotonic clock, waking exactly when it asks."""
    now = start
    while now <= start + seconds:
        now = max(live.step(now), now + 1e-6)
    return now


def test_two_cadences_while_showing():
    w, b, feed = TitledWindow(), Counter(DATA), FakeFeed(every=3.0)
    live = Live(w, b, TOWN, data=DATA, feed=feed, poll=15.0)
    run_for(live, 30.0)
    # live ticks at 0, 3, ..., 30; rebuilds at 15 and 30 (the town was built when it opened)
    assert len(feed.revs) == 11
    assert b.n == 2 and live.ticks == 2


def test_the_live_cadence_follows_the_feed():
    w, b, feed = TitledWindow(), Counter(DATA), FakeFeed(every=5.0)
    live = Live(w, b, TOWN, data=DATA, feed=feed, poll=15.0)
    run_for(live, 30.0)
    assert len(feed.revs) == 7
    live2 = Live(TitledWindow(), Counter(DATA), TOWN, data=DATA, feed=FakeFeed(every=5.0),
                 live_every=10.0)
    run_for(live2, 30.0)
    assert len(live2.feed.revs) == 4


def test_without_a_feed_only_rebuilds_run():
    b = Counter(DATA)
    live = Live(TitledWindow(), b, TOWN, data=DATA, poll=15.0)
    assert live.step(0.0) == 15.0
    run_for(live, 45.0)
    assert b.n == 3


def test_hidden_window_rebuilds_nothing_but_the_watcher_still_ticks():
    w, b, feed = TitledWindow(), Counter(DATA), FakeFeed(need=2, every=3.0)
    live = Live(w, b, TOWN, data=DATA, feed=feed, poll=15.0)
    live.step(0.0)  # the first live tick
    assert len(feed.revs) == 1
    w.doc_hidden = None  # the page answers nothing while minimised
    w.events.minimized.fire()
    w.js.clear()
    run_for(live, 60.0, start=0.5)
    assert b.n == 0, "rebuilt while hidden"
    # the Watcher every 15 s while hidden: at 15.5, 30.5, 45.5 and 60.5
    assert len(feed.revs) == 1 + 4
    assert pushed(w, "lamptownLive") == [] and pushed(w, "lamptownData") == []
    assert w.calls.count("probe") == 60 // HIDDEN_CHECK  # every 5 s: "still hidden?"
    assert w.titles == ["2 need you - Harbour Watch - Lamptown"]  # the taskbar still counts
    # back: both catch up at once
    n = len(feed.revs)
    w.events.restored.fire()
    live.step(61.0)
    assert b.n == 1 and len(feed.revs) == n + 1
    assert len(pushed(w, "lamptownLive")) == 1


def test_a_page_that_turns_visible_through_the_probe_catches_up():
    w, b, feed = TitledWindow(), Counter(DATA), FakeFeed()
    live = Live(w, b, TOWN, data=DATA, feed=feed)
    live.step(0.0)
    w.doc_hidden = True
    live.on_page_visibility(True)
    live.step(1.0)  # just hid: no probe yet
    w.doc_hidden = False
    live.step(1.0 + HIDDEN_CHECK)  # the probe sees the page again: catch up now
    assert b.n == 1 and len(feed.revs) == 2


def test_hidden_watch_default_is_fifteen_seconds():
    assert HIDDEN_WATCH == 15.0


def test_the_loop_survives_a_crashing_feed():
    def boom(rev=None):
        raise OSError("the town folder is gone")

    logs: list[str] = []
    feed = SimpleNamespace(every=3.0, tick=boom)
    b = Counter(DATA)
    live = Live(TitledWindow(), b, TOWN, data=DATA, feed=feed, log=logs.append)
    run_for(live, 15.0)
    assert b.n == 1  # the rebuilds still run
    assert any("tick failed" in m and "town folder is gone" in m for m in logs)


# ----------------------------------------------------------------------------- the one Watcher
LIVE_CONFIG = """version = 1
[town]
title = "Harbour Watch"

[live]
status_dir = "status"

[[building]]
id = "docks"
type = "harbour"
label = "Docks"
  [[building.source]]
  kind = "static"
  state = "ok"
"""


@pytest.fixture
def live_town(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("AGENT_STATUS_DIR", str(tmp_path / "elsewhere"))
    cfg = tmp_path / "lamptown.toml"
    cfg.write_text(LIVE_CONFIG, encoding="utf-8")
    return cfg


def test_the_feed_writes_town_live_js_every_tick_with_a_fresh_at(live_town, tmp_path):
    feed = LiveFeed()
    b = Builder(str(live_town), str(tmp_path / "out"), print, on_config=feed.configure)
    with feed:
        assert b() is not None
        live_js = tmp_path / "out" / "town-live.js"
        data_js = tmp_path / "out" / "town-data.js"
        data_before = data_js.read_bytes()
        first = feed.tick(b.rev)
        assert first.payload["rev"] == b.rev and first.payload["need"] == 0
        text = live_js.read_text(encoding="utf-8")
        assert text.startswith("lamptownLive(") and b.rev in text
        live_js.write_text("old", encoding="utf-8")
        second = feed.tick(b.rev)  # nothing changed: still written (the heartbeat)
        assert live_js.read_text(encoding="utf-8").startswith("lamptownLive(")
        assert second.payload["at"] >= first.payload["at"]
        assert data_js.read_bytes() == data_before, "a live tick must not touch town-data.js"
        update_status("codex-trim", tmp_path / "status", title="Trim the sails",
                      state="waiting", agent="codex")
        assert feed.tick(b.rev).payload["need"] == 1


def test_a_build_writes_town_data_before_town_live(live_town, tmp_path, monkeypatch):
    from lamptown import build as B

    order: list[str] = []
    real = B.write_atomic
    monkeypatch.setattr(B, "write_atomic", lambda out, text: (order.append(out.name),
                                                              real(out, text))[1])
    feed = LiveFeed()
    with feed:
        assert Builder(str(live_town), str(tmp_path / "out"), print,
                       on_config=feed.configure)() is not None
    assert order.index("town-data.js") < order.index("town-live.js")


def test_one_watcher_per_status_folder(live_town, tmp_path):
    """A second feed on the same folder stands by quietly (still writing its live file) and
    takes over once the first closes."""
    logs: list[str] = []
    a, b = LiveFeed(), LiveFeed(log=logs.append)
    ba = Builder(str(live_town), str(tmp_path / "a"), print, on_config=a.configure)
    bb = Builder(str(live_town), str(tmp_path / "b"), print, on_config=bb_conf(b))
    assert ba() is not None and bb() is not None
    try:
        assert not a.tick(ba.rev).busy
        t = b.tick(bb.rev)
        assert t.busy and b.standby
        assert (tmp_path / "b" / "town-live.js").is_file()
        assert b.tick(bb.rev).busy
        assert len([m for m in logs if "stands by" in m]) == 1, logs
        assert not any("watcher:" in m for m in logs), "standing by is no error"
    finally:
        a.close()
    try:
        assert not b.tick(bb.rev).busy and not b.standby
        assert any("watching now" in m for m in logs)
    finally:
        b.close()
    # both closed: the folder is free for anyone
    with Watcher(tmp_path / "status") as w:
        assert not w.tick().busy


def bb_conf(feed: LiveFeed):
    return feed.configure


def test_the_feed_keeps_its_watcher_until_the_config_changes(live_town, tmp_path):
    feed = LiveFeed()
    b = Builder(str(live_town), str(tmp_path / "out"), print, on_config=feed.configure)
    with feed:
        b()
        first = feed.watcher
        assert first is not None
        feed.tick(b.rev)
        b()
        assert feed.watcher is first  # the same config: the same Watcher
        live_town.write_text(LIVE_CONFIG.replace('status_dir = "status"',
                                                 'status_dir = "status2"'), encoding="utf-8")
        b()
        assert feed.watcher is not first and first._fd is None  # the old one let go
        live_town.write_text(LIVE_CONFIG.replace("[live]\n", "[live]\nenabled = false\n"),
                             encoding="utf-8")
        b()
        assert feed.watcher is None and not feed.enabled
        assert feed.tick(b.rev) is None
    assert feed.closed and feed.tick() is None


def test_a_broken_reader_is_reported_once_and_the_rest_runs(live_town, tmp_path):
    live_town.write_text(LIVE_CONFIG.replace(
        'status_dir = "status"', 'status_dir = "status"\n[[live.reader]]\nkind = "no-such-tool"'),
        encoding="utf-8")
    logs: list[str] = []
    feed = LiveFeed(log=logs.append)
    b = Builder(str(live_town), str(tmp_path / "out"), logs.append, on_config=feed.configure)
    with feed:
        assert b() is not None
        assert feed.tick(b.rev) is not None
        b()  # the same config again: no second message
    assert len([m for m in logs if "no-such-tool" in m]) == 1, logs


def test_live_with_a_real_feed_end_to_end(live_town, tmp_path):
    """The app's pieces together: a real build, a real Watcher, a fake window."""
    feed = LiveFeed()
    b = Builder(str(live_town), str(tmp_path / "out"), print, on_config=feed.configure)
    data = b()
    w = TitledWindow(url=b.town.as_uri())
    live = Live(w, b, b.town.as_uri(), data=data, feed=feed, poll=15.0)
    with feed:
        live.step(0.0)
        (p,) = pushed(w, "lamptownLive")
        assert p["rev"] == data["rev"] and p["need"] == 0
        update_status("codex-trim", tmp_path / "status", title="Trim the sails",
                      state="waiting", agent="codex")
        live.step(3.0)
        assert w.titles[-1] == "1 needs you - Harbour Watch - Lamptown"
        live.step(15.0)  # the rebuild sees the waiting agent: pushed, not reloaded
        assert live.reloads == 0 and reloads(w) == []
        assert live.pushes == 1
        (d,) = pushed(w, "lamptownData")
        assert d["parts"]["lighthouse"]["state"] == "waiting" and d["rev"] == b.rev


def test_a_town_folder_that_cannot_be_written_is_said_once(live_town, tmp_path):
    logs: list[str] = []
    feed = LiveFeed(log=logs.append)
    b = Builder(str(live_town), str(tmp_path / "out"), print, on_config=feed.configure)
    with feed:
        assert b() is not None
        feed.out = tmp_path / "lamptown.toml" / "not-a-folder"  # a file where the folder was
        assert feed.tick(b.rev) is None
        assert feed.tick(b.rev) is None
    assert len([m for m in logs if "live files" in m]) == 1, logs


# ----------------------------------------------------------------------------- L6-fix
@pytest.mark.parametrize("every", [25.0, 60.0])
def test_the_live_tick_is_at_least_every_ten_seconds(every):
    """A slow [live] poll must not stretch the heartbeat: the page goes BUILT 30 s after the
    last town-live.js write."""
    w, b, feed = TitledWindow(), Counter(DATA), FakeFeed(every=every)
    times: list[float] = []
    clock = [0.0]
    real = feed.tick
    feed.tick = lambda rev=None: (times.append(clock[0]), real(rev))[1]
    live = Live(w, b, TOWN, data=DATA, feed=feed, poll=15.0)
    while clock[0] <= 120.0:
        clock[0] = max(live.step(clock[0]), clock[0] + 1e-6)
    assert max(t2 - t1 for t1, t2 in zip(times, times[1:], strict=False)) <= 10.0 + 1e-6
    assert b.n == 8  # rebuilds at 15, 30, ..., 120


def test_a_real_feed_keeps_the_watcher_at_the_poll_and_the_heartbeat_at_ten(live_town,
                                                                             tmp_path):
    live_town.write_text(LIVE_CONFIG.replace("[live]\n", "[live]\npoll = 60\n"),
                         encoding="utf-8")
    clock = [0.0]
    runs: list[float] = []

    class Counting(Watcher):
        def tick(self, now=None):
            runs.append(clock[0])
            return super().tick(now)

    feed = LiveFeed(make_watcher=Counting, mono=lambda: clock[0])
    b = Builder(str(live_town), str(tmp_path / "out"), print, on_config=feed.configure)
    with feed:
        data = b()
        w = TitledWindow(url=b.town.as_uri())
        live = Live(w, b, b.town.as_uri(), data=data, feed=feed, poll=15.0)
        while clock[0] <= 130.0:
            clock[0] = max(live.step(clock[0]), clock[0] + 1e-6)
    assert runs == [0.0, 60.0, 120.0]
    beats = [0.0] + [i * 10.0 for i in range(1, 14)]
    assert live.live_ticks == len(beats)  # a heartbeat every 10 s, the Watcher every 60 s
    assert len(pushed(w, "lamptownLive")) == len(beats)


class ClosingWindow(TitledWindow):
    """A TitledWindow with pywebview's ``closing`` event."""

    def __init__(self, url: str = TOWN) -> None:
        super().__init__(url)
        from test_app_live import FakeEvent

        self.events.closing = FakeEvent()
        self.urls = 0

    def get_current_url(self) -> str:
        self.urls += 1
        return super().get_current_url()


def test_nothing_is_sent_to_a_closing_window():
    """Once pywebview says ``closing``, the loop makes no more calls into the window: a web view
    being torn down may never answer a script (WebView2 waits with no timeout)."""
    w, b, feed = ClosingWindow(), Counter(DATA), FakeFeed(need=2)
    live = Live(w, b, TOWN, data=DATA, feed=feed)
    w.events.closing.fire()
    assert live.stop.is_set()
    b.data = changed(DATA, rooms={}, title="Kite Field")
    live.live_tick()
    live.tick()
    live.probe_page()
    live.on_loaded()
    live.reload("fp", "shape")
    assert w.js == [] and w.titles == [] and w.calls == [] and w.urls == 0
    assert feed.revs == ["r1"]  # the live files are still written (the heartbeat)


class _StuckWindow(ClosingWindow):
    """A web view torn down mid-script: evaluate_js of a push never returns."""

    def __init__(self, url: str) -> None:
        super().__init__(url)
        import threading

        self.stuck, self.release = threading.Event(), threading.Event()

    def evaluate_js(self, js: str):
        if "lamptownLive(" in js:
            self.stuck.set()
            self.release.wait()
        return super().evaluate_js(js)


class _StuckWebview:
    """Just enough of pywebview for run_app: start() runs the worker on a non-daemon thread (as
    pywebview does), waits until the loop is stuck in evaluate_js, then closes the window."""

    def __init__(self) -> None:
        self.settings: dict = {}
        self.screens: list = []
        self.win: _StuckWindow | None = None
        self.worker = None

    def create_window(self, title, url=None, **_k):
        self.win = _StuckWindow(url or "about:blank")
        return self.win

    def start(self, func, **_k):
        import threading

        self.worker = threading.Thread(target=func, name="fake-pywebview-worker")
        self.worker.start()
        assert self.win.stuck.wait(30), "the loop never pushed"
        self.win.events.closing.fire()
        self.win.events.closed.fire()


def test_a_web_view_that_never_answers_does_not_keep_the_app_alive(live_town, tmp_path,
                                                                   monkeypatch):
    import sys
    import threading

    from lamptown.app import window
    from lamptown.app.instance import SingleInstance

    fake = _StuckWebview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(window, "STOP_WAIT", 0.5, raising=False)
    try:
        rc = window.run_app(str(live_town), str(tmp_path / "out"),
                            state_dir=tmp_path / "state", echo=False)
        assert rc == window.EXIT_OK
        fake.worker.join(5)
        assert not fake.worker.is_alive(), "pywebview's (non-daemon) worker is stuck"
        stuck = [t for t in threading.enumerate() if t.name.startswith("lamptown")
                 and t.is_alive() and not t.daemon]
        assert stuck == [], "a non-daemon thread would keep the process alive"
        log = (tmp_path / "state" / "app.log").read_text(encoding="utf-8")
        assert "didn't stop" in log and "app closed" in log
        with Watcher(tmp_path / "status") as w:  # the app's Watcher let the folder go
            assert not w.tick().busy
        s = SingleInstance(tmp_path / "state")
        assert s.acquire()
        s.release()
    finally:
        if fake.win is not None:
            fake.win.release.set()  # let the stuck thread end


def test_a_tick_that_keeps_failing_logs_one_traceback():
    def boom(rev=None):
        raise OSError("the town folder is gone")

    logs: list[str] = []
    feed = SimpleNamespace(every=3.0, tick=boom)
    live = Live(TitledWindow(), Counter(DATA), TOWN, data=DATA, feed=feed, log=logs.append)
    run_for(live, 60.0)  # 21 failing ticks
    assert len([m for m in logs if "Traceback" in m]) == 1
    assert len(logs) <= 6 and sum(len(m) for m in logs) < 3000, logs
    feed.tick = FakeFeed().tick
    run_for(live, 3.0, start=61.0)
    assert "works again" in logs[-1]
