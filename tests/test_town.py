"""The town page in a real browser (headless Chrome, `--dump-dom`; no extra dependency).

Each case builds a town, appends a probe script that reads the page's own globals (what the last
frame drew: SEEN; the data model: TRUTH; the room state: RS) and writes JSON into <pre id="T">.
The browser comes from ``_browser.py`` ($CHROME to choose one); without one the tests skip.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from pathlib import Path

import pytest

from _browser import browser, dump_dom, headless
from lamptown import build as B
from lamptown.config import parse_config

NOW = dt.datetime(2026, 3, 14, 22, 0, tzinfo=dt.UTC)


pytestmark = browser

PROBE = """
<script>
(() => {
  const write = r => { const pre = document.createElement('pre'); pre.id = 'T';
    pre.textContent = JSON.stringify(r); document.body.appendChild(pre); };
  const errs = () => [...document.querySelectorAll('pre')]
    .filter(p => p.id !== 'T' && / line \\d+/.test(p.textContent)).map(p => p.textContent);
  const wait = ms => new Promise(res => setTimeout(res, ms));
  setTimeout(async () => {
    let r;
    try { r = await (async () => { %BODY% })(); }
    catch (e) { r = { probeError: String(e && e.stack || e) }; }
    r = Object.assign({ pageErrors: errs() }, r);
    write(r);
  }, 300);
})();
</script>
"""

ROOM_JS = """
registerRoom('testroom', {
  enter(data, ctx) {
    window.__room = { entered: 1, data, id: ctx.id, left: 0, drawn: 0, clicked: 0 };
  },
  leave() { window.__room.left++; },
  draw(g, t, data) { window.__room.drawn++; K.R(10, 20, 30, 40, '#ff00ff'); },
  targets() { return [{ id: 'bell', box: [10, 20, 30, 40], tip: ['A BELL'], label: 'Ring the bell',
                        onClick() { window.__room.clicked++; K.toast('DING'); } }]; },
  plate() { return { num: '7' }; },
});
"""

FICTIONAL = """
[town]
title = "Kite field"
latitude = 51.5
longitude = -0.1

[[building]]
id = "agents"
type = "harbour"
label = "Wren and Otter"
  [[building.source]]
  kind = "static"
  state = "waiting"
  needs = [{ text = "Approve the plan" }]
  rows = [
    { id = "kite", name = "Paper Kite", state = "waiting", progress = { done = 3, total = 8 } },
  ]

[[building]]
id = "nightly"
type = "jobs"
label = "Night jobs"
  [[building.source]]
  kind = "static"
  state = "working"
  rows = [{ name = "Tidebook index", state = "working", next = "2026-03-15T02:00:00+00:00" }]

[[building]]
id = "kiosk"
type = "kiosk"
plot = "lab"
label = "Tea kiosk"
  [[building.source]]
  kind = "static"
  state = "error"
  error = "the kettle is unplugged"

[[building]]
id = "tr"
type = "testroom"
plot = "gate"
label = "Test room"
room_js = "room.js"
  [[building.source]]
  kind = "static"
  state = "ok"
  marker = "fictional-data"
"""


def build_town(tmp_path: Path, toml: str = "") -> Path:
    (tmp_path / "room.js").write_text(ROOM_JS, encoding="utf-8")
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    return B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW).town


def run(page: Path, body: str, query: str = "?t=300", frag: str = "", *, flags: tuple = (),
        replace_data: str | None = None) -> dict:
    text = page.read_text(encoding="utf-8")
    if replace_data is not None:
        text = re.sub(r'(<script id="town-data" type="application/json">).*?(</script>)',
                      lambda m: m.group(1) + replace_data + m.group(2), text, count=1, flags=re.S)
    i = text.rindex("</body>")
    probe = PROBE.replace("%BODY%", body)
    case = page.with_name(f"case-{abs(hash((body, query, frag, flags))) % 10**8}.html")
    case.write_text(text[:i] + probe + text[i:], encoding="utf-8")
    prof = page.parent / "chrome-profile"
    cmd = headless(prof, "--window-size=1600,900", "--virtual-time-budget=6000",
                   "--allow-file-access-from-files", *flags, "--dump-dom",
                   case.as_uri() + query + frag)
    out = dump_dom(cmd)
    m = re.search(r'<pre id="T">(.*?)</pre>', out, re.S)
    assert m, f"the probe wrote nothing; Chrome said: {out[-2000:]}"
    res = json.loads(html.unescape(m.group(1)))
    assert "probeError" not in res, res["probeError"]
    assert res["pageErrors"] == [], res["pageErrors"]
    return res


# ----------------------------------------------------------------------------- the empty town
def test_empty_config_is_a_town_of_dark_lots(tmp_path):
    page = build_town(tmp_path, "")
    r = run(page, """return { plates: SEEN.plates, lamps: SEEN.lamps, lots: SEEN.lots,
      banner: SEEN.banner, needs: SEEN.needs || 0,
      hits: [...document.querySelectorAll('.hit[data-plot]')]
        .map(b => [b.dataset.plot, b.dataset.id || null]),
      sr: document.getElementById('town-sr').textContent,
      moving: !!(SEEN.belts && Object.values(SEEN.belts.moving).some(Boolean)),
      bell: SEEN.bell || null, walkers: SEEN.walkers };""")
    assert len(r["lots"]) == 6
    assert set(r["plates"]) == {"tower", "library", "gate", "lab", "workshop", "boathouse"}
    assert all(p["name"] == "EMPTY LOT" and p["num"] == "" for p in r["plates"].values())
    assert r["lamps"] == {}
    assert r["needs"] == 0 and r["bell"] is None and r["walkers"] == 0 and not r["moving"]
    assert r["banner"] == "SIX EMPTY LOTS"
    assert all(bid is None for _, bid in r["hits"]) and len(r["hits"]) == 6
    assert "Every plot is an empty lot." in r["sr"]


def test_empty_lot_opens_a_card_that_says_how_to_build(tmp_path):
    page = build_town(tmp_path, "")
    r = run(page, """document.querySelector('.hit[data-plot="library"]').click();
      return { hidden: document.getElementById('panel').hidden,
               body: document.getElementById('p-body').textContent };""")
    assert r["hidden"] is False and 'plot = "library"' in r["body"]


# ----------------------------------------------------------------------------- a fictional town
def test_fictional_town_lamps_plates_and_sign(tmp_path):
    page = build_town(tmp_path, FICTIONAL)
    r = run(page, """return { plates: SEEN.plates, lamps: SEEN.lamps, lots: SEEN.lots,
      needs: SEEN.needs,
      bell: SEEN.bell || null, title: document.title, place: SKY_PLACE,
      tip: (tipAt(42, 2) || { tip: [] }).tip, kioskTip: (tipAt(34, 93) || { tip: [] }).tip };""")
    assert r["lamps"] == {"tower": "green", "lab": "red", "gate": "green", "boathouse": "amber"}
    assert r["plates"]["boathouse"] == {"name": "WREN AND OTTER", "num": "1"}
    assert r["plates"]["lab"]["name"] == "TEA KIOSK"
    assert r["plates"]["library"]["name"] == "EMPTY LOT"
    assert sorted(r["lots"]) == ["library", "workshop"]
    assert r["needs"] == 1
    assert r["bell"] == "running"  # a job running now rings the bell
    assert r["title"] == "Kite field"
    assert r["place"].startswith("51.5N")
    assert r["tip"][0] == "NIGHT JOBS LAMP"
    assert r["kioskTip"][0] == "TEA KIOSK LAMP" and "SOMETHING IS WRONG" in r["kioskTip"][1]


def test_quiet_jobs_do_not_ring(tmp_path):
    toml = FICTIONAL.replace(
        'state = "working"\n  rows = [{ name = "Tidebook index", state = "working"',
        'state = "ok"\n  rows = [{ name = "Tidebook index", state = "ok"')
    r = run(build_town(tmp_path, toml), "return { bell: SEEN.bell || null };")
    assert r["bell"] is None


@pytest.mark.parametrize(("at", "phase"), [("2026-03-14T12:00:00Z", "day"),
                                           ("2026-03-14T02:00:00Z", "night")])
def test_sky_follows_the_clock_and_the_configured_place(tmp_path, at, phase):
    r = run(build_town(tmp_path, FICTIONAL), "return { phase: SEEN.phase };",
            query=f"?t=300&at={at}")
    assert r["phase"] == phase


def test_a_title_that_spells_the_data_slot_still_loads(tmp_path):
    toml = FICTIONAL.replace('title = "Kite field"', 'title = "Kite /*%%TOWN_DATA%%*/ field"')
    assert toml != FICTIONAL
    r = run(build_town(tmp_path, toml), "return { err: DATA_ERR, title: D.title,"
            " sample: !!D.sample, parts: Object.keys(D.parts).sort() };", query="?t=300&preview=1")
    assert r["err"] == "" and r["title"] == "Kite /*%%TOWN_DATA%%*/ field"
    assert r["sample"] is False and r["parts"] == ["agents", "kiosk", "nightly", "tr"]


def test_unreadable_data_goes_dark_with_a_sign(tmp_path):
    r = run(build_town(tmp_path, FICTIONAL),
            "return { err: DATA_ERR, lots: SEEN.lots, lamps: SEEN.lamps };",
            replace_data="{not json")
    assert r["err"] and len(r["lots"]) == 6 and r["lamps"] == {}


# ----------------------------------------------------------------------------- motion
def test_reduced_motion_gives_a_still_town_and_motion_1_overrides(tmp_path):
    page = build_town(tmp_path, FICTIONAL)
    body = "return { still: stillMode(), running, frameMs: FRAME_MS };"
    still = run(page, body, query="", flags=("--force-prefers-reduced-motion",))
    assert still == {"pageErrors": [], "still": True, "running": False, "frameMs": 50}
    moving = run(page, body, query="?motion=1", flags=("--force-prefers-reduced-motion",))
    assert moving["still"] is False and moving["running"] is True


# ----------------------------------------------------------------------------- rooms
def test_kit_k_is_frozen_and_complete(tmp_path):
    r = run(build_town(tmp_path, FICTIONAL), """return { frozen: Object.isFrozen(K),
      keys: Object.keys(K).sort(), part: K.part('tr') && K.part('tr').name, W: K.W, H: K.H };""")
    assert r["frozen"] is True and (r["W"], r["H"]) == (400, 225)
    for k in ("R", "P", "text", "textW", "wrapText", "sprite", "glow", "toast", "leave", "card",
              "open", "part", "still", "demo", "nowMs"):
        assert k in r["keys"], k
    assert r["part"] == "Test room"


def test_room_hash_walks_in_and_escape_walks_out(tmp_path):
    page = build_town(tmp_path, FICTIONAL)
    r = run(page, """
      const inside = { id: RS.id, mode: RS.mode, room: Object.assign({}, window.__room),
        px: Array.from(main.getImageData(15, 25, 1, 1).data), hitsHidden: hits.hidden };
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await wait(1500);
      return { inside, after: { id: RS.id, mode: RS.mode, left: window.__room.left,
        hash: location.hash, hitsHidden: hits.hidden } };""", frag="#room=tr")
    i = r["inside"]
    assert (i["id"], i["mode"]) == ("tr", "room")
    assert i["room"]["entered"] == 1 and i["room"]["id"] == "tr" and i["room"]["drawn"] > 0
    assert i["room"]["data"]["marker"] == "fictional-data"
    assert i["px"][:3] == [255, 0, 255]  # the room drew through K into the screen
    assert i["hitsHidden"] is True
    a = r["after"]
    assert a["id"] is None and a["mode"] == "town" and a["left"] == 1
    assert a["hash"] == "" and a["hitsHidden"] is False


def test_zoom_freezes_the_walk_in(tmp_path):
    r = run(build_town(tmp_path, FICTIONAL),
            "return { id: RS.id, mode: RS.mode, frozen: RS.frozen };",
            query="?t=300&zoom=0.4", frag="#room=tr")
    assert r == {"pageErrors": [], "id": "tr", "mode": "in", "frozen": 0.4}


def test_clicking_a_building_with_a_room_walks_in_and_targets_work(tmp_path):
    r = run(build_town(tmp_path, FICTIONAL), """
      const b = document.querySelector('.hit[data-id="tr"]');
      const label = b.getAttribute('aria-label');
      b.click();
      await wait(1500);
      const s1 = { id: RS.id, mode: RS.mode, hash: location.hash };
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
      const focused = allTargets()[RS.focus] && allTargets()[RS.focus].id;
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      return { label, s1, focused, clicked: window.__room.clicked, toast: RS.toast && RS.toast.msg,
               plate: RS.room.plate().num };""", query="?motion=1&t=300")
    assert "Press Enter to walk inside" in r["label"]
    assert r["s1"] == {"id": "tr", "mode": "room", "hash": "#room=tr"}
    assert r["focused"] == "bell"  # three HUD buttons first, then the room's own target
    assert r["clicked"] == 1 and r["toast"] == "DING" and r["plate"] == "7"


def test_a_building_without_a_room_opens_its_card(tmp_path):
    r = run(build_town(tmp_path, FICTIONAL), """
      const b = document.querySelector('.hit[data-id="kiosk"]');
      b.click();
      return { room: RS.id, hidden: document.getElementById('panel').hidden,
               popup: b.getAttribute('aria-haspopup'),
               text: document.getElementById('panel').textContent };""")
    assert r["room"] is None and r["hidden"] is False and r["popup"] == "dialog"
    assert "kettle is unplugged" in r["text"]


def test_unknown_room_hash_stays_in_town(tmp_path):
    r = run(build_town(tmp_path, FICTIONAL), "return { id: RS.id };", frag="#room=kiosk")
    assert r["id"] is None
