"""The town's live data channel in a real browser (headless Chrome, as in ``test_town.py``).

The page swaps its data in place (``lamptownData``), polls ``town-live.js`` next to itself while it
shows (``lamptownPoll``), loads ``town-data.js`` when the live payload's ``rev`` differs from the
data shown, and keeps the snapshot inlined at build time when there is no Watcher. The fixture
``town-live.js`` / ``town-data.js`` files here stand in for the Watcher; every name is made up.
"""

from __future__ import annotations

import copy
import datetime as dt
import os
import threading
import time
from pathlib import Path

import pytest

from _browser import browser
from lamptown import build as B
from lamptown.config import parse_config
from test_town import FICTIONAL, NOW, ROOM_JS, run

pytestmark = browser

# waits on something the page does, never on a fixed sleep: `cap` ms of the page's (virtual) time.
# quiet(): stop polling before returning. Chrome dumps the page only once its whole
# --virtual-time-budget is spent; a page still polling (each load pauses virtual time) spends it
# slowly.
UNTIL = """const until = async (f, cap) => {
  for (let w = 0; w < (cap || 4000) && !f(); w += 50) await wait(50);
  return !!f(); };
const quiet = () => { LIVE.off = true; clearTimeout(LIVE.timer); LIVE.timer = 0; };
"""

# the same town after a change: every lamp green, a new name on the boathouse, a building on the
# library plot, the night job done
SWAPPED = FICTIONAL.replace('label = "Wren and Otter"', 'label = "Heron and Otter"') \
    .replace('state = "waiting"\n  needs = [{ text = "Approve the plan" }]', 'state = "ok"') \
    .replace('state = "waiting", progress = { done = 3, total = 8 }',
             'state = "ok", progress = { done = 5, total = 8 }') \
    .replace('state = "working"\n  rows = [{ name = "Tidebook index", state = "working"',
             'state = "ok"\n  rows = [{ name = "Tidebook index", state = "ok"') \
    .replace('state = "error"\n  error = "the kettle is unplugged"', 'state = "ok"') \
    .replace('marker = "fictional-data"', 'marker = "swapped-data"') + """
[[building]]
id = "board"
type = "kiosk"
plot = "library"
label = "Notice board"
  [[building.source]]
  kind = "static"
  state = "ok"
"""

UPDATE_ROOM_JS = ROOM_JS.replace(
    "  leave() { window.__room.left++; },",
    "  leave() { window.__room.left++; },\n"
    "  update(data) { window.__room.updated = (window.__room.updated || 0) + 1;"
    " window.__room.marker = data.marker; },")


def town_data(tmp_path: Path, toml: str, name: str, room_js: str = ROOM_JS) -> B.BuildResult:
    (tmp_path / "room.js").write_text(room_js, encoding="utf-8")
    p = tmp_path / f"{name}.toml"
    p.write_text(toml, encoding="utf-8")
    return B.build(parse_config(toml, p), out_dir=tmp_path / name, now=NOW)


def town(tmp_path: Path, room_js: str = ROOM_JS) -> tuple[Path, dict, dict]:
    """The fictional town's page, its data, and the data of the same town after a change."""
    first = town_data(tmp_path, FICTIONAL, "out", room_js)
    second = town_data(tmp_path, SWAPPED, "next", room_js)
    return first.town, first.data, second.data


def js(obj) -> str:
    return B.script_json(obj)


def with_rev(data: dict, rev: str) -> dict:
    return dict(data, rev=rev)


def iso_ago(seconds: float) -> str:
    """A real-clock time `seconds` ago, as a Watcher writes `at` (the page reads Date.now())."""
    t = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=seconds)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def live_payload(rev: str, at: str | None = None, **more) -> dict:
    """A Watcher's payload; `at` is now (a fresh heartbeat) unless given."""
    return {"spec": "lamptown-live/1", "at": at or iso_ago(0), "rev": rev, "need": 2, **more,
            "agents": [{"id": "codex-1", "tool": "codex", "title": "Trim the sails",
                        "project": "paper-kite", "plot": "boathouse", "state": "working"}],
            "projects": [{"id": "paper-kite", "name": "PAPER KITE", "berth": 0, "new": False}]}


def write_live(out: Path, payload: dict) -> None:
    (out / "town-live.js").write_text(f"lamptownLive({js(payload)});\n", encoding="utf-8")


def replace_live(out: Path, payload: dict) -> None:
    """Rewrite town-live.js while a page reads it: whole or not at all (a temp file, then a rename;
    Windows refuses the rename while the browser has the file open, so try again)."""
    tmp = out / "town-live.js.tmp"
    tmp.write_text(f"lamptownLive({js(payload)});\n", encoding="utf-8")
    for _ in range(200):
        try:
            os.replace(tmp, out / "town-live.js")
            return
        except PermissionError:
            time.sleep(0.01)
    raise AssertionError("could not replace town-live.js")


def write_data(out: Path, data: dict) -> None:
    (out / "town-data.js").write_text(f"lamptownData({js(data)});\n", encoding="utf-8")


# ----------------------------------------------------------------------------- the swap itself
def test_a_swap_changes_plates_and_lamps_with_no_navigation(tmp_path):
    page, _, d2 = town(tmp_path)
    r = run(page, f"""
      window.__life = Math.random(); const life = window.__life;
      const navs = performance.getEntriesByType('navigation').length, href = location.href;
      const hit = () => document.querySelector('.hit[data-plot="boathouse"]')
        .getAttribute('aria-label');
      const before = {{ plates: SEEN.plates, lamps: SEEN.lamps, lots: SEEN.lots, label: hit(),
        bell: SEEN.bell || null, serpent: !!SEEN.serpent, hull: SEEN.hull }};
      const ok = lamptownData({js(d2)});
      return {{ ok, before, after: {{ plates: SEEN.plates, lamps: SEEN.lamps, lots: SEEN.lots,
          label: hit(),
          bell: SEEN.bell || null, serpent: !!SEEN.serpent, hull: SEEN.hull,
          libraryHit: document.querySelector('.hit[data-plot="library"]').dataset.id || null,
          sr: document.getElementById('town-sr').textContent }},
        sameLife: window.__life === life, navs: performance.getEntriesByType('navigation').length,
        sameHref: location.href === href, swaps: LIVE.swaps }};""", query="?t=300&live=0")
    b, a = r["before"], r["after"]
    assert r["ok"] is True and r["swaps"] == 1
    assert r["sameLife"] is True and r["navs"] == 1 and r["sameHref"] is True  # no reload
    assert b["plates"]["boathouse"]["name"] == "WREN AND OTTER"
    assert a["plates"]["boathouse"]["name"] == "HERON AND OTTER"
    assert b["lamps"] == {"tower": "green", "lab": "red", "gate": "green", "boathouse": "amber"}
    assert a["lamps"] == {"tower": "green", "library": "green", "lab": "green", "gate": "green",
                          "boathouse": "green"}
    assert sorted(b["lots"]) == ["library", "workshop"] and a["lots"] == ["workshop"]
    assert a["plates"]["library"]["name"] == "NOTICE BOARD" and a["libraryHit"] == "board"
    assert b["label"].startswith("Wren and Otter") and a["label"].startswith("Heron and Otter")
    assert b["bell"] == "running" and a["bell"] is None  # TRUTH was read again
    assert b["serpent"] is False and a["serpent"] is True  # every building green now
    assert b["hull"]["planked"] == round(31 * 3 / 8) and a["hull"]["planked"] == round(31 * 5 / 8)
    assert "Every building is green." in a["sr"]


def test_unusable_data_or_payload_is_ignored(tmp_path):
    page, _, _ = town(tmp_path)
    r = run(page, """
      const before = JSON.stringify(SEEN.plates);
      return { data: lamptownData({ no: 'parts' }), arr: lamptownData([]),
        live: lamptownLive({ spec: 'other/1' }), same: JSON.stringify(SEEN.plates) === before,
        err: DATA_ERR, swaps: LIVE.swaps, on: LIVE.on };""",
            query="?t=300&live=0")
    assert r["data"] is False and r["arr"] is False and r["live"] is False
    assert r["same"] is True and r["err"] == "" and r["swaps"] == 0 and r["on"] is False


def test_an_open_card_follows_a_swap(tmp_path):
    page, _, d2 = town(tmp_path)
    r = run(page, f"""
      document.querySelector('.hit[data-id="kiosk"]').click();
      const before = document.getElementById('panel').textContent;
      lamptownData({js(d2)});
      return {{ before, after: document.getElementById('panel').textContent,
               hidden: document.getElementById('panel').hidden }};""", query="?t=300&live=0")
    assert "kettle is unplugged" in r["before"]
    assert r["hidden"] is False and "kettle is unplugged" not in r["after"]


# ----------------------------------------------------------------------------- the live channel
def test_a_new_rev_loads_town_data_and_says_live(tmp_path):
    page, d1, d2 = town(tmp_path)
    write_live(page.parent, live_payload("r2"))
    write_data(page.parent, with_rev(d2, "r2"))
    r = run(page, UNTIL + """
      const swapped = await until(() => LIVE.rev === 'r2');
      LIVE.every = 100; lamptownPoll();
      await until(() => LIVE.polls >= 4);
      return { swapped, rev: LIVE.rev, loads: LIVE.loads, swaps: LIVE.swaps, on: LIVE.on,
        corner: SEEN.corner, name: SEEN.plates.boathouse.name, live: SEEN.live, liveOn: SEEN.liveOn,
        polls: LIVE.polls, left: document.querySelectorAll('script[src*="town-"]').length };""",
            replace_data=js(with_rev(d1, "r1")))
    assert r["swapped"] is True and r["rev"] == "r2"
    # the page was built showing "WREN AND OTTER" (r1): one load of town-data.js swapped it in
    assert r["polls"] >= 4 and r["loads"] == 1 and r["swaps"] == 1  # same rev again: no reload
    assert r["on"] is True and r["corner"] == "LIVE" and r["liveOn"] is True
    assert r["name"] == "HERON AND OTTER"
    assert r["live"]["rev"] == "r2" and r["live"]["agents"][0]["title"] == "Trim the sails"
    assert r["left"] == 0  # the poll scripts don't pile up in the page


def test_the_same_rev_does_not_load_data(tmp_path):
    page, d1, d2 = town(tmp_path)
    write_live(page.parent, live_payload("r1"))
    write_data(page.parent, with_rev(d2, "r1"))
    r = run(page, UNTIL + """
      LIVE.every = 100; lamptownPoll();
      await until(() => LIVE.polls >= 3 && LIVE.on);
      return { polls: LIVE.polls, loads: LIVE.loads, swaps: LIVE.swaps, corner: SEEN.corner,
        name: SEEN.plates.boathouse.name };""", replace_data=js(with_rev(d1, "r1")))
    assert r["polls"] >= 3 and r["loads"] == 0 and r["swaps"] == 0
    assert r["corner"] == "LIVE" and r["name"] == "WREN AND OTTER"


def test_data_that_brings_nothing_is_not_asked_for_again(tmp_path):
    page, d1, _ = town(tmp_path)
    write_live(page.parent, live_payload("r2"))
    (page.parent / "town-data.js").write_text("lamptownData({});\n", encoding="utf-8")
    r = run(page, UNTIL + """
      LIVE.every = 100; lamptownPoll();
      await until(() => LIVE.polls >= 4 && LIVE.bad === 'r2');
      return { polls: LIVE.polls, loads: LIVE.loads, bad: LIVE.bad, rev: LIVE.rev,
        name: SEEN.plates.boathouse.name };""", replace_data=js(with_rev(d1, "r1")))
    assert r["polls"] >= 4 and r["loads"] == 1 and r["bad"] == "r2" and r["rev"] == "r1"
    assert r["name"] == "WREN AND OTTER"


def test_an_older_town_data_file_does_not_replace_newer_data(tmp_path):
    # the payload says r3; town-data.js still holds r2 (the Watcher hasn't rewritten it yet)
    page, d1, d2 = town(tmp_path)
    write_live(page.parent, live_payload("r3"))
    write_data(page.parent, with_rev(d2, "r2"))
    r = run(page, UNTIL + """
      LIVE.every = 100; lamptownPoll();
      await until(() => LIVE.loads >= 3 && !LIVE.want);
      return { loads: LIVE.loads, rev: LIVE.rev, bad: LIVE.bad, swaps: LIVE.swaps,
        name: SEEN.plates.boathouse.name };""", replace_data=js(with_rev(d1, "r1")))
    assert r["swaps"] == 0 and r["rev"] == "r1" and r["name"] == "WREN AND OTTER"
    assert r["loads"] >= 3 and r["bad"] is None  # asked again on each poll, until r3 is there


def test_the_apps_own_push_is_always_used(tmp_path):
    page, d1, d2 = town(tmp_path)
    r = run(page, f"""
      const a = lamptownData({js(with_rev(d2, "r3"))}), newer = SEEN.plates.boathouse.name;
      const b = lamptownData({js(with_rev(d1, "r2"))});   // the app sent it, so it is what to show
      const c = lamptownData({js(d2)});                    // no rev: shown, rev unknown
      return {{ a, b, c, newer, rev: LIVE.rev, name: SEEN.plates.boathouse.name }};""",
            query="?t=300&live=0")
    assert r["a"] is True and r["b"] is True and r["c"] is True
    assert r["newer"] == "HERON AND OTTER" and r["name"] == "HERON AND OTTER"
    assert r["rev"] is None


def test_town_data_without_a_rev_is_used_but_not_recorded(tmp_path):
    page, d1, d2 = town(tmp_path)
    write_live(page.parent, live_payload("r2"))
    write_data(page.parent, d2)  # a Watcher that forgot the rev
    r = run(page, UNTIL + """
      LIVE.every = 100; lamptownPoll();
      await until(() => LIVE.polls >= 4 && LIVE.bad === 'r2');
      return { polls: LIVE.polls, loads: LIVE.loads, rev: LIVE.rev, bad: LIVE.bad,
        swaps: LIVE.swaps, name: SEEN.plates.boathouse.name };""",
            replace_data=js(with_rev(d1, "r1")))
    assert r["name"] == "HERON AND OTTER" and r["swaps"] == 1
    assert r["rev"] == "r1"  # not labelled r2: nothing says it is r2
    assert r["polls"] >= 4 and r["loads"] == 1 and r["bad"] == "r2"  # and not loaded every poll


def test_no_watcher_keeps_the_snapshot_and_says_built(tmp_path):
    page, _, _ = town(tmp_path)  # no town-live.js next to the page
    r = run(page, UNTIL + """
      const failed = await until(() => LIVE.fails >= 1 && !LIVE.busy);
      return { failed, on: LIVE.on, corner: SEEN.corner, name: SEEN.plates.boathouse.name,
        lamps: SEEN.lamps, swaps: LIVE.swaps, again: LIVE.timer !== 0, live: SEEN.live };""")
    assert r["failed"] is True and r["on"] is False and r["swaps"] == 0
    assert r["corner"].startswith("BUILT ") and r["corner"] != "BUILT "
    assert r["name"] == "WREN AND OTTER" and r["lamps"]["boathouse"] == "amber"
    assert r["again"] is True  # it keeps looking, in case a Watcher starts
    assert r["live"] is None


def test_a_live_file_that_goes_away_turns_live_back_to_built(tmp_path):
    page, d1, _ = town(tmp_path)
    write_live(page.parent, live_payload("r1"))
    r = run(page, UNTIL + """
      const on = await until(() => LIVE.on);
      const first = SEEN.corner;
      LIVE.every = 100;   // the Watcher's file goes away: point the poll at a file that isn't there
      const real = liveScript;
      liveScript = (name, done) => real(name === 'town-live.js' ? 'gone-live.js' : name, done);
      lamptownPoll();
      const off = await until(() => !LIVE.on);
      liveScript = real;
      return { on, first, off, corner: SEEN.corner };""", replace_data=js(with_rev(d1, "r1")))
    assert r["on"] is True and r["first"] == "LIVE"
    assert r["off"] is True and r["corner"].startswith("BUILT ")


def test_a_stale_at_says_built_though_the_file_is_there(tmp_path):
    # a Watcher that was stopped (Ctrl+C, a crash, the app closed) leaves town-live.js behind
    page, d1, d2 = town(tmp_path)
    write_live(page.parent, live_payload("r2", at=iso_ago(600)))
    write_data(page.parent, with_rev(d2, "r2"))
    r = run(page, UNTIL + """
      LIVE.every = 100; lamptownPoll();
      await until(() => LIVE.polls >= 4 && LIVE.rev === 'r2');
      return { polls: LIVE.polls, fails: LIVE.fails, on: LIVE.on, corner: SEEN.corner,
        live: SEEN.live && SEEN.live.rev, rev: LIVE.rev, name: SEEN.plates.boathouse.name,
        next: LIVE.next };""", replace_data=js(with_rev(d1, "r1")))
    assert r["polls"] >= 4 and r["fails"] == 0  # the file is there and loads
    assert r["on"] is False and r["corner"].startswith("BUILT ") and r["corner"] != "BUILT "
    # it is still the newest on disk: the data follows it; only the LIVE word waits for a heartbeat
    assert r["live"] == "r2" and r["rev"] == "r2" and r["name"] == "HERON AND OTTER"
    assert r["next"] == 100  # a file that loads is not a miss: no back-off


def test_live_turns_built_when_the_heartbeat_stops(tmp_path):
    # the payload is fresh at first; nobody rewrites it, so it goes stale while the page shows
    page, d1, _ = town(tmp_path)
    write_live(page.parent, live_payload("r1", at=iso_ago(12)))
    r = run(page, UNTIL + """
      const on = await until(() => LIVE.on);
      const first = SEEN.corner, t0 = Date.now();
      const off = await until(() => !LIVE.on, 40000);   // real defaults: polls every 5 s
      quiet();
      return { on, first, off, corner: SEEN.corner, fails: LIVE.fails, polls: LIVE.polls,
        age: Date.now() - Date.parse(LIVE.payload.at), waited: Date.now() - t0 };""",
            replace_data=js(with_rev(d1, "r1")), flags=("--virtual-time-budget=60000",))
    assert r["on"] is True and r["first"] == "LIVE"
    assert r["off"] is True and r["corner"].startswith("BUILT ")
    assert r["fails"] == 0 and r["polls"] >= 2  # the file stayed and kept loading
    assert 30000 <= r["age"] < 36000  # BUILT once `at` was 30 s old, not much later


def test_polling_stops_while_the_page_is_hidden(tmp_path):
    page, d1, _ = town(tmp_path)
    write_live(page.parent, live_payload("r1"))
    r = run(page, UNTIL + """
      LIVE.every = 100; lamptownPoll();
      await until(() => LIVE.polls >= 3);
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      Object.defineProperty(document, 'visibilityState',
        { configurable: true, get: () => 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
      await until(() => !LIVE.busy);   // a poll already on its way may land
      const p0 = LIVE.polls;
      await wait(1500);                // 15 poll periods
      const p1 = LIVE.polls, timer = LIVE.timer;
      delete document.hidden; delete document.visibilityState;
      document.dispatchEvent(new Event('visibilitychange'));
      const back = await until(() => LIVE.polls > p1);
      return { p0, p1, timer, back };""", replace_data=js(with_rev(d1, "r1")))
    assert r["p1"] == r["p0"] and r["timer"] == 0
    assert r["back"] is True  # showing again polls at once


# the poll's own clock, in the page's virtual time (Chrome skips idle time, so 5 s cost no real 5 s)
POLL_TIMES = """
  const times = [], real = liveScript;
  const stamp = name => { if (name === 'town-live.js') times.push(Math.round(performance.now())); };
  liveScript = (name, done) => { stamp(name); return real(name, done); };
"""


def test_the_real_poll_interval_is_five_seconds(tmp_path):
    page, d1, _ = town(tmp_path)
    write_live(page.parent, live_payload("r1"))
    r = run(page, UNTIL + POLL_TIMES + """
      lamptownPoll();   // the first one now; then the page's own timer, untouched
      await until(() => times.length >= 4, 20000);
      quiet();
      return { gaps: times.slice(1).map((t, i) => t - times[i]), fails: LIVE.fails,
        every: LIVE.every,
        next: LIVE.next };""", replace_data=js(with_rev(d1, "r1")),
            flags=("--virtual-time-budget=30000",))
    assert r["every"] == 5000 and r["next"] == 5000 and r["fails"] == 0
    assert len(r["gaps"]) >= 3 and all(5000 <= g < 5600 for g in r["gaps"]), r["gaps"]


def test_no_watcher_backs_off_to_thirty_seconds_and_back(tmp_path):
    page, d1, _ = town(tmp_path)
    write_live(page.parent, live_payload("r1"))
    r = run(page, UNTIL + POLL_TIMES + """
      liveScript = (name, done) => { stamp(name);
        return real(LIVE.gone && name === 'town-live.js' ? 'gone-live.js' : name, done); };
      LIVE.gone = true; lamptownPoll();   // no Watcher: every load misses
      await until(() => times.length >= 5, 80000);
      const gaps = times.slice(1).map((t, i) => t - times[i]), slowNext = LIVE.next;
      LIVE.gone = false;                   // a Watcher starts: the next (slow) poll lands
      const on = await until(() => LIVE.miss === 0, 40000);   // (its `at` is old by now)
      const after = { next: LIVE.next, miss: LIVE.miss };
      LIVE.gone = true;                    // it stops again; then the page is shown again
      await until(() => LIVE.next === LIVE.slow && !LIVE.busy, 40000);
      document.dispatchEvent(new Event('visibilitychange'));
      await until(() => !LIVE.busy && LIVE.next, 4000);
      const shown = { next: LIVE.next, miss: LIVE.miss }; quiet();
      return { gaps, slowNext, on, after, shown, slow: LIVE.slow };""",
            replace_data=js(with_rev(d1, "r1")), flags=("--virtual-time-budget=200000",))
    g = r["gaps"]
    assert r["slow"] == 30000 and r["slowNext"] == 30000
    assert all(5000 <= x < 5600 for x in g[:2]), g  # misses 1 and 2: still every 5 s
    assert all(30000 <= x < 30600 for x in g[2:4]), g  # after 3 misses in a row: every 30 s
    assert r["on"] is True and r["after"] == {"next": 5000, "miss": 0}  # a landed poll resets it
    assert r["shown"] == {"next": 5000, "miss": 1}  # showing again: polled at once, 5 s again


# ----------------------------------------------------------------------------- memory and freshness
# every poll loads the same URL: Chrome keeps about 1 KB for each script URL a page has loaded, so a
# new URL per poll (a cache-buster) grew the page by ~18 MB a day at 5 s
WATCH_SCRIPTS = """
  const srcs = [];
  new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => {
    if (n.tagName === 'SCRIPT' && n.src) srcs.push(n.getAttribute('src')); })))
    .observe(document.head, { childList: true });
"""


def test_polls_use_the_same_two_urls_forever(tmp_path):
    page, d1, d2 = town(tmp_path)
    write_live(page.parent, live_payload("r2"))
    write_data(page.parent, with_rev(d2, "r2"))
    r = run(page, UNTIL + WATCH_SCRIPTS + """
      await until(() => LIVE.rev === 'r2' && !LIVE.want);   // the page's first poll swapped r2 in
      const p0 = LIVE.polls, l0 = LIVE.loads;
      LIVE.every = 20; lamptownPoll();
      for (let i = 0; i < 3; i++) {   // three more data loads: pretend the data shown is older
        LIVE.rev = 'r1'; await until(() => LIVE.rev === 'r2' && !LIVE.want); }
      await until(() => LIVE.polls >= p0 + 40);
      quiet(); await until(() => !LIVE.busy);
      return { urls: [...new Set(srcs)].sort(), scripts: srcs.length, polls: LIVE.polls - p0,
        loads: LIVE.loads - l0, rev: LIVE.rev };""",
            replace_data=js(with_rev(d1, "r1")))
    assert r["rev"] == "r2" and r["polls"] >= 40 and r["loads"] == 3
    assert r["scripts"] == r["polls"] + r["loads"]  # every load seen
    assert r["urls"] == ["town-data.js", "town-live.js"]


HEAP = UNTIL + """
  if (typeof gc !== 'function' || !performance.memory) return { skip: 'no gc() or heap size' };
  const tick = () => new Promise(res => setTimeout(res, 0));
  const spin = async n => { for (let i = 0; i < 200000 && LIVE.polls < n; i++) await tick(); };
  const heap = async () => { await tick(); gc(); gc(); return performance.memory.usedJSHeapSize; };
  LIVE.every = 0; lamptownPoll();
  await spin(800); const m0 = await heap(), p0 = LIVE.polls;   // after the warm-up (~200 B/poll)
  await spin(2300); const m1 = await heap(), p1 = LIVE.polls;
  quiet();
  return { perPoll: (m1 - m0) / (p1 - p0), polls: p1 - p0, on: LIVE.on, fails: LIVE.fails };"""
HEAP_FLAGS = ("--js-flags=--expose-gc", "--enable-precise-memory-info",
              "--virtual-time-budget=400000")


def test_a_poll_costs_almost_no_memory(tmp_path):
    page, d1, _ = town(tmp_path)
    write_live(page.parent, live_payload("r1"))
    r = run(page, HEAP, replace_data=js(with_rev(d1, "r1")), flags=HEAP_FLAGS)
    if "skip" in r:
        pytest.skip(r["skip"])
    assert r["polls"] >= 1500 and r["fails"] == 0 and r["on"] is True
    # measured: ~0 B/poll here; a new URL per poll cost ~1,000 B each, for good
    # (_scratch/agents/L2-fix/probe_heap.py compares the two)
    assert r["perPoll"] < 100, r


def test_a_fixed_url_still_reads_the_rewritten_file(tmp_path):
    # file:// classic scripts are read from disk on each load: no cache-buster is needed. A Python
    # writer rewrites town-live.js (atomically, as a Watcher must) while the page polls it.
    page, d1, _ = town(tmp_path)
    out = page.parent
    write_live(out, live_payload("r1", need=0))
    stop = threading.Event()

    def writer() -> None:
        n = 0
        while not stop.wait(0.15):
            n += 1
            replace_live(out, live_payload("r1", need=n))

    th = threading.Thread(target=writer, daemon=True)
    th.start()
    try:
        r = run(page, UNTIL + """
          const seen = new Set(), real = lamptownLive;
          lamptownLive = p => { seen.add(p.need); return real(p); };
          LIVE.every = 20; lamptownPoll();
          await until(() => seen.size >= 4, 120000);   // ~1 ms of real time per poll
          quiet();
          return { seen: [...seen], polls: LIVE.polls, fails: LIVE.fails };""",
                replace_data=js(with_rev(d1, "r1")), flags=("--virtual-time-budget=150000",))
    finally:
        stop.set()
        th.join(5)
    assert len(r["seen"]) >= 4, r  # three rewrites after the first, each one seen


# ----------------------------------------------------------------------------- places and rooms
def test_a_place_off_the_plots_can_be_entered(tmp_path):
    page, d1, _ = town(tmp_path)
    d3 = copy.deepcopy(d1)
    d3["parts"]["lh"] = dict(d3["parts"]["tr"], id="lh", plot="lighthouse", name="Lighthouse",
                             label="Lighthouse")
    d3["rooms"]["lh"] = {"state": "ok", "marker": "lighthouse-data"}
    r = run(page, f"""
      const plot = addPlace('tower', {{ hit: [0, 0, 5, 5] }});
      const added = addPlace('lighthouse', {{ hit: [336, 196, 20, 26], word: 'lighthouse' }});
      const btn = () => document.querySelector('.hit[data-plot="lighthouse"]');
      const empty = {{ hidden: btn().hidden, id: btn().dataset.id || null }};
      const ok = lamptownData({js(d3)});
      const built = {{ hidden: btn().hidden, id: btn().dataset.id || null,
        label: btn().getAttribute('aria-label'),
        last: document.querySelector('#hits > :last-child').className }};
      const seen = {{ lots: SEEN.lots, plates: Object.keys(SEEN.plates),
        lamps: Object.keys(SEEN.lamps),
        allGreen: TRUTH.allGreen, builtCount: TRUTH.builtCount }};
      btn().click();
      await wait(1500);
      const inside = {{ id: RS.id, plot: RS.plot, mode: RS.mode, room: window.__room.id,
        marker: window.__room.data.marker }};
      RS.mode = 'in'; RS.frozen = 0.3; render(300, true);   // the walk-in dives to the place's door
      return {{ plot, added, empty, ok, built, seen, inside, plotOf: plotOf('lh') }};""",
            query="?t=300&live=0")
    assert r["plot"] is False and r["added"] is True
    assert r["empty"] == {"hidden": True, "id": None}  # no building there: no empty lot, no button
    assert r["ok"] is True and r["plotOf"] == "lighthouse"
    assert r["built"]["hidden"] is False and r["built"]["id"] == "lh"
    assert "Press Enter to walk inside" in r["built"]["label"]
    assert r["built"]["last"] == "hit det"  # the details link stays last
    s = r["seen"]  # the six plots alone have lots, plates, lamps and the all-green count
    assert "lighthouse" not in s["lots"] + s["plates"] + s["lamps"] and len(s["plates"]) == 6
    assert s["builtCount"] == 4
    assert r["inside"] == {"id": "lh", "plot": "lighthouse", "mode": "room", "room": "lh",
                           "marker": "lighthouse-data"}


def test_an_open_room_without_update_walks_in_again_on_new_data(tmp_path):
    page, _, d2 = town(tmp_path)
    r = run(page, f"""
      const old = window.__room;
      lamptownData({js(d2)});
      const once = {{ oldLeft: old.left, fresh: window.__room !== old,
        marker: window.__room.data.marker, entered: window.__room.entered, id: RS.id, mode: RS.mode,
        hash: location.hash, data: RS.data.marker }};
      const now = window.__room;
      lamptownData({js(d2)});   // the same data again: the room is left alone
      return {{ once, again: window.__room === now && now.left === 0 }};""", frag="#room=tr")
    o = r["once"]
    assert o["oldLeft"] == 1 and o["fresh"] is True and o["entered"] == 1
    assert o["marker"] == "swapped-data" and o["data"] == "swapped-data"
    assert (o["id"], o["mode"], o["hash"]) == ("tr", "room", "#room=tr")  # quietly, no walk-out
    assert r["again"] is True


def test_an_open_room_with_update_gets_the_new_data(tmp_path):
    page, _, d2 = town(tmp_path, UPDATE_ROOM_JS)
    r = run(page, f"""
      const old = window.__room;
      lamptownData({js(d2)});
      return {{ same: window.__room === old, left: old.left, updated: old.updated,
        marker: old.marker,
        data: RS.data.marker, id: RS.id, mode: RS.mode }};""", frag="#room=tr")
    assert r["same"] is True and r["left"] == 0 and r["updated"] == 1
    assert r["marker"] == "swapped-data" and r["data"] == "swapped-data"
    assert (r["id"], r["mode"]) == ("tr", "room")


def test_a_room_whose_building_goes_away_walks_back_to_town(tmp_path):
    page, d1, _ = town(tmp_path)
    d3 = copy.deepcopy(d1)
    del d3["parts"]["tr"], d3["rooms"]["tr"]
    d3["plots"]["gate"] = None
    r = run(page, UNTIL + f"""
      lamptownData({js(d3)});
      const out = await until(() => RS.id === null);
      return {{ out, left: window.__room.left, mode: RS.mode, hits: hits.hidden,
        gate: SEEN.lots.includes('gate') }};""", frag="#room=tr")
    assert r["out"] is True and r["left"] == 1 and r["mode"] == "town"
    assert r["hits"] is False and r["gate"] is True


def test_two_swaps_while_a_room_closes_step_back_once(tmp_path):
    # the room's building goes away and two swaps come close together (the app's push, then a poll):
    # one step back in history, to the page you were on, not two (which could leave the page)
    page, d1, _ = town(tmp_path)
    d3 = copy.deepcopy(d1)
    del d3["parts"]["tr"], d3["rooms"]["tr"]
    d3["plots"]["gate"] = None
    r = run(page, UNTIL + f"""
      history.pushState({{ mark: 'before' }}, '', location.pathname + location.search + '#before');
      document.querySelector('.hit[data-id="tr"]').click();
      await until(() => RS.mode === 'room');
      const inside = location.hash, back = history.back.bind(history);
      let backs = 0; history.back = () => {{ backs++; back(); }};
      lamptownData({js(d3)}); lamptownData({js(d3)});
      const asked = backs;   // both swaps have run: any second step back was asked for by now
      const out = await until(() => RS.id === null && location.hash !== inside);
      return {{ inside, asked, out, hash: location.hash, state: history.state, id: RS.id,
        backing: RS.backing, left: window.__room.left, backs }};""", query="?t=300&live=0")
    assert r["inside"] == "#room=tr" and r["asked"] == 1 and r["backs"] == 1 and r["out"] is True
    assert r["hash"] == "#before" and r["state"] == {"mark": "before"}
    assert r["id"] is None and r["backing"] is None and r["left"] == 1


def test_the_details_link_takes_no_other_scheme(tmp_path):
    page, d1, _ = town(tmp_path)
    bad = [dict(d1, details=h) for h in
           ("javascript:alert(1)", " java\tscript:alert(1)", "data:text/html,hi", "vbscript:x")]
    good = [dict(d1, details=h) for h in
            ("town-details.html#lab", "https://example.org/d", "sub/details.html")]
    r = run(page, f"""
      const href = d => (lamptownData(d), DET.getAttribute('href'));
      return {{ bad: {js(bad)}.map(href), good: {js(good)}.map(href) }};""",
            query="?t=300&live=0")
    assert r["bad"] == ["town-details.html"] * 4
    assert r["good"] == ["town-details.html#lab", "https://example.org/d", "sub/details.html"]
