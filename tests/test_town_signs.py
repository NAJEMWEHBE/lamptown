"""The signs by the map's right edge fit inside the map at every count (headless Chrome, as in
``test_town.py``): the lighthouse's count sign (1..999+ agents waiting or failed, both at once)
and the town's "N NEEDS YOU" badge. A real run once showed "2 AGENTS FAILE." cut at the edge.
Payloads are fed with ``lamptownLive()``; every name is made up.
"""

from __future__ import annotations

import json

import pytest

from _browser import browser
from test_town_lighthouse import STILL, probe, town

pytestmark = browser

# a few counts per browser run (each case feeds a payload and grabs the canvas twice: the page's
# virtual-time budget is a few seconds)
CASES = [("wait", [1, 2, 9]), ("wait", [99, 999, 1000]), ("fail", [1, 2, 9]),
         ("fail", [99, 999, 1000]), ("both", [1, 999])]

SIGN_FITS = """
  const PLOTS = ['tower', 'library', 'gate', 'lab', 'workshop', 'boathouse'];
  const many = (n, state, from) => Array.from({ length: n },
    (_, i) => ag(state + (from + i), state, PLOTS[i % PLOTS.length]));
  const KIND = @KIND@, COUNTS = @COUNTS@, out = [];
  for (const n of COUNTS) {
    const waits = many(n, 'waiting', 0), fails = many(n, 'error', 5000);
    const agents = KIND === 'fail' ? fails : KIND === 'wait' ? waits : waits.concat(fails);
    const sign = KIND === 'fail' ? (n === 1 ? '1 AGENT FAILED' : n + ' AGENTS FAILED')
      : (n === 1 ? '1 AGENT WAITS' : n + ' AGENTS WAIT');
    feed(agents); kick();
    const ok = await until(() => L().sign === sign && L().signBox, 3000);
    // the pixels the lighthouse's lights draw, to check the box SEEN reports is the one drawn
    kick(); await wait(100);
    const s = L(), a = grab(), b = await without('lights', 'lights');
    const [x, y, w, h] = s.signBox || [0, 0, 0, 0];
    let right = -1;
    for (let yy = y; yy < y + h; yy++) for (let xx = 0; xx < W; xx++)
      if (dist(at(a, xx, yy), at(b, xx, yy)) > 0) right = Math.max(right, xx);
    out.push({ n, ok: !!ok, box: s.signBox, lines: s.signLines, right,
               widths: (s.signLines || []).map(textW) });
  }
  return { out, W, H };"""


@pytest.mark.parametrize(("kind", "counts"), CASES, ids=[f"{k}-{c[-1]}" for k, c in CASES])
def test_the_lighthouse_sign_fits_inside_the_map(tmp_path, kind, counts):
    body = SIGN_FITS.replace("@KIND@", json.dumps(kind)).replace("@COUNTS@", json.dumps(counts))
    r = probe(town(tmp_path), body, query=STILL)
    W, H = r["W"], r["H"]
    assert [c["n"] for c in r["out"]] == counts
    for c in r["out"]:
        assert c["ok"], c
        x, y, w, h = c["box"]
        assert x >= 0 and y >= 0 and x + w <= W and y + h <= H, c  # inside the map
        assert y + h < H - 17, c  # clear of the LIVE/BUILT corner box
        assert c["right"] == x + w - 1, c  # the sign really ends where it says
        assert all(not ln.endswith(".") for ln in c["lines"]), c  # nothing cut short
        assert all(tw + 8 <= w for tw in c["widths"]), c  # every line fits its board
        n = c["n"]
        count = ("999+" if n > 999 else str(n)) + (" AGENT" if n == 1 else " AGENTS")
        word = "FAILED" if kind == "fail" else "WAITS" if n == 1 else "WAIT"
        assert c["lines"] == [count, word], c


def test_no_sign_box_without_a_wait_or_failure(tmp_path):
    r = probe(town(tmp_path), """
      feed([ag('a', 'working', 'gate')]); await until(() => L().mode === 'sweep');
      return L();""", query=STILL)
    assert r["sign"] == "" and r["signBox"] is None and r["signLines"] == []


def test_the_needs_you_badge_fits_inside_the_map(tmp_path):
    r = probe(town(tmp_path), """
      const rows = async n => { D.needs = n; kick(); await wait(150); return grab(); };
      const none = await rows(0), out = {};
      for (const n of [1, 99, 999, 99999]) {
        const d = await rows(n);
        let left = W, right = -1;
        for (let y = 0; y < 20; y++) for (let x = 0; x < W; x++)
          if (dist(at(d, x, y), at(none, x, y)) > 0) {
            left = Math.min(left, x); right = Math.max(right, x); }
        out[n] = { left, right, seen: SEEN.needs };
      }
      return { out, W };""", query=STILL)
    for n, c in r["out"].items():
        assert c["seen"] == int(n)
        assert c["left"] > 0 and c["right"] < r["W"] - 1, (n, c)  # a margin on both sides
