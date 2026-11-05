"""PR #1 review: a live swap that brings a building whose room type the page has no script for
reloads the page once (only a reload brings the script), and never loops.

Headless Chrome, as in ``test_town_live.py``; the fixture ``town-live.js`` / ``town-data.js``
files stand in for the Watcher; every name is made up.
"""

from __future__ import annotations

from pathlib import Path

from _browser import browser
from lamptown import build as B
from lamptown.config import parse_config
from test_town import FICTIONAL, NOW, ROOM_JS, run
from test_town_live import SWAPPED, UNTIL, js, live_payload, with_rev, write_data, write_live

pytestmark = browser

BOARD_JS = ROOM_JS.replace("registerRoom('testroom',", "registerRoom('board',")
# the same town after a building with a room type new to the page went up on the library plot
WITH_BOARD = FICTIONAL + """
[[building]]
id = "notices"
type = "board"
plot = "library"
label = "Notice board"
room_js = "board.js"
  [[building.source]]
  kind = "static"
  state = "ok"
"""


def _build(tmp_path: Path, toml: str, name: str) -> B.BuildResult:
    (tmp_path / "room.js").write_text(ROOM_JS, encoding="utf-8")
    (tmp_path / "board.js").write_text(BOARD_JS, encoding="utf-8")
    p = tmp_path / f"{name}.toml"
    p.write_text(toml, encoding="utf-8")
    return B.build(parse_config(toml, p), out_dir=tmp_path / name, now=NOW)


def _town(tmp_path: Path, second: str = WITH_BOARD) -> tuple[Path, dict, dict]:
    first = _build(tmp_path, FICTIONAL, "out")
    nxt = _build(tmp_path, second, "next")
    write_live(first.town.parent, live_payload("r2"))
    write_data(first.town.parent, with_rev(nxt.data, "r2"))
    return first.town, first.data, nxt.data


# the page opens with ?live=0 (its first poll would reload before the probe runs), then polls
RECORD = UNTIL + """
  const urls = []; LIVE.reload = u => urls.push(u); LIVE.off = false;
"""


def test_the_data_lists_the_room_types_the_page_carries(tmp_path):
    page, d1, d2 = _town(tmp_path)
    assert d1["roomJs"] == ["harbour", "jobs", "testroom"]  # kiosk has no script anywhere
    assert d2["roomJs"] == ["board", "harbour", "jobs", "testroom"]
    assert 'data-room="board"' not in page.read_text(encoding="utf-8")


def test_a_swap_bringing_a_new_room_type_reloads_once_keeping_the_view(tmp_path):
    page, d1, _ = _town(tmp_path)
    r = run(page, RECORD + """
      LIVE.every = 100; lamptownPoll();
      const got = await until(() => urls.length > 0);
      await until(() => LIVE.polls >= 6);   // more polls: still one reload
      quiet();
      return { got, urls, swaps: LIVE.swaps, rev: LIVE.rev, reloading: LIVE.reloading,
        built: !!PT.notices && plotOf('notices') === 'library', room: !!roomOf('notices') };""",
            query="?t=300&live=0", frag="#room=tr", replace_data=js(with_rev(d1, "r1")))
    assert r["got"] is True and len(r["urls"]) == 1, r["urls"]
    url = r["urls"][0]
    assert "roomjs=board" in url and "t=300" in url and "live=0" in url
    assert url.endswith("#room=tr")  # the open room comes back after the reload
    assert url.split("?")[0].endswith(".html")
    assert r["rev"] == "r2" and r["swaps"] == 1 and r["reloading"] == "board"
    assert r["built"] is True  # the swap is shown until the page reloads
    assert r["room"] is False  # no script yet: that is why it reloads


def test_a_page_that_still_lacks_the_script_after_its_reload_never_reloads_again(tmp_path):
    page, d1, _ = _town(tmp_path)
    r = run(page, RECORD + """
      LIVE.every = 100; lamptownPoll();
      await until(() => LIVE.swaps >= 1 && LIVE.roomWarned === 'board');
      await until(() => LIVE.polls >= 5);
      quiet();
      return { urls, swaps: LIVE.swaps, warned: LIVE.roomWarned };""",
            query="?t=300&live=0&roomjs=board", replace_data=js(with_rev(d1, "r1")))
    assert r["urls"] == [] and r["swaps"] == 1 and r["warned"] == "board"


def test_the_real_reload_lands_once_and_stops(tmp_path):
    # town.html is not rebuilt here (a stale page): the real reload comes back to the same page,
    # which still has no script for the type, and stays
    page, d1, _ = _town(tmp_path)
    r = run(page, UNTIL + """
      if (!qs.get('roomjs')) { LIVE.every = 100; lamptownPoll(); await until(() => false, 5000);
        return { first: true }; }
      LIVE.every = 100; lamptownPoll();
      await until(() => LIVE.roomWarned === 'board');
      quiet();
      return { first: false, search: location.search, hash: location.hash, swaps: LIVE.swaps,
        warned: LIVE.roomWarned, reloading: LIVE.reloading || null };""",
            query="?t=300", replace_data=js(with_rev(d1, "r1")))
    assert r["first"] is False  # the page did reload
    assert "roomjs=board" in r["search"] and "t=300" in r["search"]
    assert r["swaps"] == 1 and r["warned"] == "board" and r["reloading"] is None


def test_no_reload_for_known_types_a_type_with_no_script_or_in_the_app(tmp_path):
    page, d1, _ = _town(tmp_path, SWAPPED)  # SWAPPED adds a kiosk: no room script anywhere
    r = run(page, RECORD + """
      LIVE.every = 100; lamptownPoll();
      await until(() => LIVE.swaps >= 1);
      await until(() => LIVE.polls >= 4);
      quiet();
      return { urls, swaps: LIVE.swaps };""", query="?t=300&live=0",
            replace_data=js(with_rev(d1, "r1")))
    assert r["swaps"] == 1 and r["urls"] == []
    (tmp_path / "app").mkdir()
    page, d1, d2 = _town(tmp_path / "app")
    r = run(page, RECORD + f"""
      window.pywebview = {{}};   // the desktop app: it reloads on a shape change itself
      const ok = lamptownData({js(with_rev(d2, "r9"))});
      return {{ ok, urls, swaps: LIVE.swaps }};""", query="?t=300&live=0")
    assert r["ok"] is True and r["swaps"] == 1 and r["urls"] == []
