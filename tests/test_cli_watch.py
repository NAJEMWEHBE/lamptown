"""``lamptown watch`` and ``lamptown open --watch``: the Watcher every few seconds and a rebuild
every 15 s, the files next to town.html, a clean stop on Ctrl+C (the Watcher's lock released),
and a clear message when live is off. Every name and path is made up (``tmp_path``)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lamptown import cli, livefeed
from lamptown.livefeed import LiveFeed, watch_loop
from lamptown.status.store import update_status
from lamptown.status.watch import Watcher

CONFIG = """version = 1
[town]
title = "Kite field"

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
def town(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("AGENT_STATUS_DIR", str(tmp_path / "elsewhere"))
    cfg = tmp_path / "lamptown.toml"
    cfg.write_text(CONFIG, encoding="utf-8")
    return cfg


def live_file(out: Path) -> dict:
    text = (out / "town-live.js").read_text(encoding="utf-8").strip()
    assert text.startswith("lamptownLive(") and text.endswith(");")
    return json.loads(text[len("lamptownLive("):-2])


def folder_is_free(status: Path) -> bool:
    with Watcher(status) as w:
        return not w.tick().busy


# ----------------------------------------------------------------------------- the command
def test_watch_runs_writes_the_files_and_lets_the_folder_go(town, tmp_path, capsys):
    out = tmp_path / "out"
    update_status("codex-trim", tmp_path / "status", title="Trim the sails", state="waiting",
                  agent="codex")
    rc = cli.main(["watch", "--config", str(town), "--out", str(out), "--every", "0.2",
                   "--ticks", "3"])
    assert rc == 0, capsys.readouterr().err
    for name in ("town.html", "town-data.js", "town-live.js", "town-history.js"):
        assert (out / name).is_file(), name
    p = live_file(out)
    data = (out / "town-data.js").read_text(encoding="utf-8")
    assert p["rev"] and f'"rev": "{p["rev"]}"' in data  # the payload asks for the data on disk
    assert p["need"] == 1
    said = capsys.readouterr().out
    assert "Watching:" in said and "Ctrl+C" in said
    assert folder_is_free(tmp_path / "status")


def test_ctrl_c_stops_cleanly_and_releases_the_lock(town, tmp_path, monkeypatch, capsys):
    calls = []

    def interrupt(seconds):
        calls.append(seconds)
        if len(calls) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(livefeed, "_sleep", interrupt)
    rc = cli.main(["watch", "--config", str(town), "--out", str(tmp_path / "out")])
    assert rc == 0
    assert "Stopped watching." in capsys.readouterr().out
    assert 0 < calls[0] <= 3.0  # the [live] poll (3 s by default), less the tick's own time
    assert folder_is_free(tmp_path / "status")
    # the Watcher saved its state on the way out
    assert (tmp_path / "status" / ".lamptown" / "cursors.json").is_file()


def test_watch_with_live_off_says_so(tmp_path, capsys):
    cfg = tmp_path / "lamptown.toml"
    cfg.write_text(CONFIG.replace('[live]\nstatus_dir = "status"\n', ""), encoding="utf-8")
    rc = cli.main(["watch", "--config", str(cfg), "--out", str(tmp_path / "out")])
    assert rc == cli.EXIT_CONFIG
    err = capsys.readouterr().err
    assert "live is off" in err and "[live]" in err
    assert not (tmp_path / "out").exists(), "nothing is built when there is nothing to watch"


def test_watch_with_a_broken_config_says_so(tmp_path, capsys):
    cfg = tmp_path / "lamptown.toml"
    cfg.write_text("version = 1\n[[building]]\nid = 'Bad Id!'\n", encoding="utf-8")
    assert cli.main(["watch", "--config", str(cfg)]) == cli.EXIT_CONFIG
    assert "config problem" in capsys.readouterr().err


def test_open_watch_opens_the_page_then_watches(town, tmp_path, monkeypatch, capsys):
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url) or True)
    out = tmp_path / "out"
    rc = cli.main(["open", "--watch", "--config", str(town), "--out", str(out), "--every", "0.2",
                   "--ticks", "2", "-q"])
    assert rc == 0
    assert opened == [(out / "town.html").resolve().as_uri()]
    assert live_file(out)["spec"] == "lamptown-live/1"
    assert capsys.readouterr().out == ""  # -q
    assert folder_is_free(tmp_path / "status")


def test_open_without_watch_still_just_opens(town, tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url) or True)
    assert cli.main(["open", "--config", str(town), "--out", str(tmp_path / "out"), "-q"]) == 0
    assert len(opened) == 1


def test_watch_args():
    p = cli.build_parser()
    a = p.parse_args(["watch", "--every", "2", "--rebuild", "30"])
    assert a.func is cli.cmd_watch and a.every == 2.0 and a.rebuild == 30.0 and a.ticks is None
    assert p.parse_args(["watch"]).rebuild == 15.0
    for bad in (["watch", "--every", "0"], ["watch", "--rebuild", "nope"]):
        with pytest.raises(SystemExit):
            p.parse_args(bad)


# ----------------------------------------------------------------------------- the loop
class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class CountingFeed:
    def __init__(self, every: float = 3.0) -> None:
        self.every = every
        self.enabled = True
        self.revs: list = []

    def tick(self, rev=None):
        self.revs.append(rev)
        return object()


def test_the_loop_runs_two_cadences():
    clock, feed, builds = Clock(), CountingFeed(), []

    def rebuild():
        builds.append(clock.t)
        return {}

    n = watch_loop(feed, rebuild, rev=lambda: "r1", rebuild_every=15.0, ticks=11,
                   sleep=clock.sleep, clock=clock)
    assert n == 11 and feed.revs == ["r1"] * 11  # live ticks at 0, 3, ..., 30
    assert builds == [15.0]  # 30 is the 11th tick: the loop stops before its rebuild


def test_the_loop_stops_when_live_goes_off():
    clock, feed = Clock(), CountingFeed()

    def rebuild():
        feed.enabled = False  # the config now says enabled = false
        return {}

    n = watch_loop(feed, rebuild, rebuild_every=6.0, sleep=clock.sleep, clock=clock)
    assert n == 3  # ticks at 0, 3, 6; the rebuild at 6 sees live off


def test_the_loop_survives_a_failing_tick():
    clock, logs = Clock(), []

    class Broken(CountingFeed):
        def tick(self, rev=None):
            raise OSError("the town folder is read-only")

    n = watch_loop(Broken(), dict, ticks=3, sleep=clock.sleep, clock=clock, log=logs.append)
    assert n == 3 and "read-only" in logs[0] and "Traceback" in logs[0]
    assert len(logs) == 2 and "still failing (2 times" in logs[1]  # not a traceback per tick


def test_a_real_feed_stands_by_while_another_watcher_holds_the_folder(town, tmp_path):
    from lamptown.livefeed import Builder

    with Watcher(tmp_path / "status") as other:
        assert not other.tick().busy
        logs: list[str] = []
        feed = LiveFeed(log=logs.append)
        b = Builder(str(town), str(tmp_path / "out"), logs.append, on_config=feed.configure)
        with feed:
            assert b() is not None
            t = feed.tick(b.rev)
            assert t.busy and (tmp_path / "out" / "town-live.js").is_file()
        assert any("stands by" in m for m in logs)


# ----------------------------------------------------------------------------- L6-fix
class FakeWatcher:
    """Counts its runs; ``behind`` says how many runs in a row report a reader catching up."""

    def __init__(self, clock: Clock, behind: int = 0) -> None:
        self.clock, self.behind, self.runs = clock, behind, []
        self.closed = False

    def tick(self, now=None):
        self.runs.append(self.clock.t)
        behind = self.behind > 0
        self.behind -= 1
        return SimpleNamespace(busy=False, changed=False, errors=[], behind=behind)

    def close(self) -> None:
        self.closed = True


class Enough(Exception):
    """Ends a watch_loop driven on a fake clock."""


def drive(feed, rebuild, clock: Clock, until: float, **kw) -> None:
    def sleep(s: float) -> None:
        clock.sleep(s)
        if clock.t > until:
            raise Enough

    with pytest.raises(Enough):
        watch_loop(feed, rebuild, sleep=sleep, clock=clock, **kw)


def gaps(times: list[float]) -> list[float]:
    return [b - a for a, b in zip(times, times[1:], strict=False)]


@pytest.mark.parametrize("poll", [25, 60])
def test_the_heartbeat_and_the_rebuild_never_wait_for_a_slow_poll(town, tmp_path, monkeypatch,
                                                                   poll):
    """[live] poll up to 60 s: town-live.js is still written at least every 10 s (the page goes
    BUILT 30 s after the last write), the Watcher runs every ``poll`` seconds, and the rebuild
    stays every 15 s."""
    from lamptown import build as B
    from lamptown.livefeed import HEARTBEAT_MAX, Builder

    town.write_text(CONFIG.replace('[live]\n', f'[live]\npoll = {poll}\n'), encoding="utf-8")
    clock, writes, builds = Clock(), [], []
    real = B.write_atomic
    monkeypatch.setattr(B, "write_atomic", lambda out, text: (
        writes.append(clock.t) if out.name == "town-live.js" else None, real(out, text))[1])
    watchers: list[FakeWatcher] = []
    feed = LiveFeed(make_watcher=lambda **_k: watchers.append(FakeWatcher(clock)) or watchers[-1],
                    mono=clock)
    b = Builder(str(town), str(tmp_path / "out"), print, on_config=feed.configure)
    with feed:
        assert b() is not None
        writes.clear()

        def rebuild():
            builds.append(clock.t)
            return b()

        drive(feed, rebuild, clock, 180.0, rev=lambda: b.rev)
    assert len(watchers) == 1, "a rebuild with the same config keeps the Watcher"
    assert max(gaps([0.0, *writes])) <= HEARTBEAT_MAX + 1e-6, gaps(writes)
    assert writes[-1] >= 170.0
    runs = watchers[0].runs
    assert runs[:3] == [0.0, float(poll), 2.0 * poll]  # the Watcher keeps the config's pace
    assert builds[:4] == [15.0, 30.0, 45.0, 60.0] and len(builds) == 12  # 15, 30, ..., 180


def test_the_loop_rebuilds_on_time_whatever_the_live_cadence():
    clock, feed, builds, lives = Clock(), CountingFeed(every=60.0), [], []

    def tick(rev=None):
        lives.append(clock.t)
        return object()

    feed.tick = tick
    drive(feed, lambda: builds.append(clock.t) or {}, clock, 61.0, rebuild_every=15.0)
    assert builds == [15.0, 30.0, 45.0, 60.0]
    assert lives == [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0]  # every() is capped at 10 s


def test_a_reader_catching_up_ticks_every_second_then_back_to_the_poll(town, tmp_path):
    """L4-fix's TickResult.behind: one tick a second while a reader is behind."""
    from lamptown.livefeed import Builder

    clock, ws = Clock(), []
    feed = LiveFeed(make_watcher=lambda **_k: ws.append(FakeWatcher(clock, behind=3)) or ws[-1],
                    mono=clock)
    b = Builder(str(town), str(tmp_path / "out"), print, on_config=feed.configure)
    with feed:
        assert b() is not None
        drive(feed, b, clock, 12.0, rev=lambda: b.rev)
    # behind after the runs at 0, 1 and 2; caught up at 3; then the poll (3 s) again
    assert ws[0].runs == [0.0, 1.0, 2.0, 3.0, 6.0, 9.0, 12.0]
    assert not feed.behind


def test_two_programs_on_one_town_folder_agree_on_the_rev(town, tmp_path):
    """The app and `lamptown watch` on the same config: whichever rebuilt last, every live file
    either of them writes carries the rev of the town-data.js on disk (no flip-flopping, so no
    extra page loads)."""
    from lamptown.build import read_rev
    from lamptown.livefeed import Builder

    out = tmp_path / "out"
    a, b = LiveFeed(), LiveFeed()
    ba = Builder(str(town), str(out), print, on_config=a.configure)
    bb = Builder(str(town), str(out), print, on_config=b.configure)
    try:
        assert ba() is not None and bb() is not None
        assert not a.tick(ba.rev).busy
        assert b.tick(bb.rev).busy and b.standby
        seen = []
        for label in ("Dry docks", "Wet docks"):
            town.write_text(CONFIG.replace('label = "Docks"', f'label = "{label}"'),
                            encoding="utf-8")
            assert bb() is not None  # the stand-by one rebuilt first: A's rev is now old
            assert ba.rev != bb.rev == read_rev(out)
            for feed, builder in ((a, ba), (b, bb), (a, ba), (b, bb)):
                seen.append(feed.tick(builder.rev).payload["rev"] == read_rev(out))
                assert live_file(out)["rev"] == read_rev(out)
            assert ba() is not None  # and now A catches up
        assert all(seen), seen
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("argv, word", [
    (["open", "--watch", "--now", "2020-01-02", "--ticks", "1"], "--now"),  # ticks: bounded
    (["open", "--every", "1"], "--every"),
    (["open", "--rebuild", "2"], "--rebuild"),
    (["open", "--ticks", "2"], "--ticks"),
])
def test_open_rejects_flags_that_would_be_ignored(argv, word, town, monkeypatch, capsys):
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url) or True)
    with pytest.raises(SystemExit) as e:
        cli.main([*argv, "--config", str(town), "--out", str(town.parent / "out"), "-q"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert word in err and "--watch" in err
    assert opened == []


def test_ctrl_c_during_the_first_build_stops_cleanly(town, tmp_path, monkeypatch, capsys):
    from lamptown import build as B

    def slow_build(*_a, **_k):
        raise KeyboardInterrupt  # the user gave up while the sources were still being read

    monkeypatch.setattr(B, "build", slow_build)
    try:
        rc = cli.main(["watch", "--config", str(town), "--out", str(tmp_path / "out")])
    except KeyboardInterrupt:
        pytest.fail("Ctrl+C escaped `lamptown watch` as a traceback")
    assert rc == 0
    captured = capsys.readouterr()
    assert "Stopped watching." in captured.out and "Traceback" not in captured.err
    assert folder_is_free(tmp_path / "status")


def test_editing_a_building_keeps_the_watcher_and_its_lock(town, tmp_path):
    """Only the live and status settings restart the Watcher: a label, a building's text or the
    poll never let the folder go (a parallel `lamptown watch` could take it in that gap)."""
    from lamptown.livefeed import Builder

    feed = LiveFeed()
    b = Builder(str(town), str(tmp_path / "out"), print, on_config=feed.configure)
    with feed:
        assert b() is not None
        first = feed.watcher
        assert not feed.tick(b.rev).busy
        for text in (CONFIG.replace('label = "Docks"', 'label = "Dry docks"'),
                     CONFIG.replace('state = "ok"', 'state = "waiting"'),
                     CONFIG.replace('[live]\n', '[live]\npoll = 5\n')):
            town.write_text(text, encoding="utf-8")
            assert b() is not None
            assert feed.watcher is first
            with Watcher(tmp_path / "status") as other:
                assert other.tick().busy, "the folder was let go"
        town.write_text(CONFIG.replace('status_dir = "status"', 'status_dir = "status2"'),
                        encoding="utf-8")
        assert b() is not None
        assert feed.watcher is not first  # a new status folder: a new Watcher


def test_live_file_warnings_are_said_once(town, tmp_path, monkeypatch):
    """L3-fix: a held town-live.js is a warning on the tick, not an error; say it once."""
    from lamptown import build as B
    from lamptown.livefeed import Builder

    logs: list[str] = []
    feed = LiveFeed(log=logs.append)
    b = Builder(str(town), str(tmp_path / "out"), print, on_config=feed.configure)
    with feed:
        assert b() is not None
        real = B.write_atomic

        def held(out, text):
            if out.name == "town-live.js":
                raise PermissionError(13, "Access is denied")
            return real(out, text)

        monkeypatch.setattr(B, "write_atomic", held)
        for _ in range(4):
            t = feed.tick(b.rev)
            assert t is not None and t.payload["spec"] == "lamptown-live/1"
    said = [m for m in logs if "town-live.js" in m]
    assert len(said) == 1 and "in use by another program" in said[0], logs


def test_a_failure_logs_its_traceback_once_then_counts():
    from lamptown.livefeed import FailLog

    logs: list[str] = []
    f = FailLog(logs.append, "live tick")

    def fail(msg: str) -> None:
        try:
            raise OSError(msg)
        except OSError as e:
            f.failed(e)

    for i in range(10):
        fail(f"the town folder is read-only ({i})")
    assert "Traceback" in logs[0] and "(0)" in logs[0]
    assert logs[1:] == [f"live tick still failing ({n} times in a row): OSError: the town "
                        f"folder is read-only ({n - 1})" for n in (2, 4, 8)]
    assert sum(len(m) for m in logs) < 1500
    f.ok()
    assert logs[-1] == "live tick works again (after 10 failures)"
    fail("again")
    assert "Traceback" in logs[-1]


def test_a_real_watcher_with_a_reader_behind_speeds_the_feed_up(town, tmp_path):
    """The real Watcher's TickResult.behind (L4-fix) reaches the feed's cadence."""
    from lamptown.livefeed import BEHIND_EVERY, Builder
    from lamptown.status.readers import Batch

    class Behind:
        name = "catching-up"
        behind = True

        def poll(self, state, now):
            return Batch()

    feed = LiveFeed(make_watcher=lambda readers, **opts: Watcher(readers=[Behind()], **opts))
    b = Builder(str(town), str(tmp_path / "out"), print, on_config=feed.configure)
    with feed:
        assert b() is not None
        assert feed.every == 3.0
        feed.tick(b.rev)
        assert feed.behind and feed.every == BEHIND_EVERY
        feed.watcher.readers[0].behind = False
        feed.tick(b.rev)
        assert not feed.behind and feed.every == 3.0
