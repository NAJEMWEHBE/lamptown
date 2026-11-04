"""The Harbour room under live data (headless Chrome, as in ``test_town.py``): a data swap while
the room is open refreshes it in place through ``harbour.update(data)``, never by walking in
again, and the room's view (the hovered boat, the open card) stays. Every name is made up."""

from __future__ import annotations

import copy

from _browser import browser
from test_town import run
from test_town_live import js, town

pytestmark = browser

ROOM = "agents"  # the fictional town's harbour


def test_the_open_harbour_survives_a_data_swap_without_walking_in_again(tmp_path):
    page, _, d2 = town(tmp_path)
    r = run(page, f"""
      const seen = () => RS.room.seen();
      const t0 = performance.timeOrigin, href = location.href, room = RS.room;
      RS.room.hover({{ id: 'boat:kite' }});
      RS.hover = RS.room.targets().find(t => t.id === 'boat:kite');   // the town's own hover
      openPanel(plotOf('{ROOM}'), stage);
      const before = seen(), tipBefore = RS.hover.tip.join(' / ');
      lamptownData({js(d2)});
      const after = seen(), tipAfter = RS.hover && RS.hover.tip.join(' / ');
      lamptownData({js(d2)});   // the same data again: nothing to update
      return {{ before, after, tipBefore, tipAfter, again: seen().updates,
        same: RS.room === room, id: RS.id, mode: RS.mode, card: openPlot === plotOf('{ROOM}'),
        nav: performance.timeOrigin === t0 && location.href === href,
        plate: RS.data.rows[0].state }};""", frag=f"#room={ROOM}")
    b, a = r["before"], r["after"]
    assert b["enters"] == 1 and b["updates"] == 0
    assert a["enters"] == 1, "the swap walked into the room again"
    assert a["updates"] == 1 and r["again"] == 1
    assert b["bays"][0]["state"] == "waiting" and a["bays"][0]["state"] == "ok"
    assert a["bays"][0]["planks"][0] > b["bays"][0]["planks"][0]  # 3 of 8 done, now 5 of 8
    assert a["hov"] == "boat:kite"  # the boat under the pointer is still hovered
    # ... and its tooltip is the new data's, not the old one's
    assert "3 of 8" in r["tipBefore"] and "5 of 8" in r["tipAfter"], (r["tipBefore"],
                                                                    r["tipAfter"])
    assert r["same"] and (r["id"], r["mode"]) == (ROOM, "room") and r["card"] and r["nav"]
    assert r["plate"] == "ok"


def test_a_hovered_boat_that_leaves_is_no_longer_hovered(tmp_path):
    page, d1, _ = town(tmp_path)
    d3 = copy.deepcopy(d1)
    room = d3["rooms"][ROOM]
    room["rows"] = [dict(room["rows"][0], id="tern", name="Tern")]
    r = run(page, f"""
      RS.room.hover({{ id: 'boat:kite' }});
      const before = RS.room.seen().hov;
      lamptownData({js(d3)});
      const s = RS.room.seen();
      return {{ before, after: s.hov, bays: s.bays.map(b => b.id), enters: s.enters }};""",
            frag=f"#room={ROOM}")
    assert r["before"] == "boat:kite" and r["after"] is None
    assert r["bays"] == ["tern"] and r["enters"] == 1


def test_the_harbour_demo_keeps_its_sample_projects_on_a_swap(tmp_path):
    page, _, d2 = town(tmp_path)
    r = run(page, f"""
      const before = RS.room.seen();
      lamptownData({js(d2)});
      const after = RS.room.seen();
      return {{ before: before.bays.map(b => b.id), after: after.bays.map(b => b.id),
        sample: after.sample, enters: after.enters }};""",
            query="?t=300&demo=1", frag=f"#room={ROOM}")
    assert r["sample"] is True and r["after"] == r["before"] and r["enters"] == 1
