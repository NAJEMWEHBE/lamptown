"""Release review fixes for the town page and the release itself (Lamptown 0.2, agent TOWN).

#1 the package says the version the CHANGELOG ships; #4 the town page's screen-reader text says
the live agents; #5 the lighthouse skips payload plots that are Object.prototype keys. (#6, the
app's stale corner, is in test_docs_live.py.) Every name here is made up.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _browser import browser
from lamptown import __version__, cli
from lamptown import build as B
from lamptown.config import parse_config
from test_town import FICTIONAL, build_town, run

ROOT = Path(__file__).resolve().parents[1]
LIVE_TOML = FICTIONAL + '\n[live]\nstatus_dir = "status"\n'

# feed(agents, at, more): a payload (fresh unless `at` says otherwise); ag(id, state, plot, extra):
# one agent; errs: what console.error got; sr(): the town page's screen-reader text
HELP = """
const errs = [], was = console.error;
console.error = (...a) => { errs.push(String(a[0] && a[0].stack || a[0])); was(...a); };
const feed = (agents, at, more) => lamptownLive({ spec: 'lamptown-live/1',
  at: at || new Date().toISOString(), rev: '', more: more || 0, agents,
  projects: [{ id: 'paper-kite', name: 'PAPER KITE', berth: 0, new: false }],
  need: agents.filter(a => a.state === 'waiting').length });
const ag = (id, state, plot, extra) => Object.assign({ id, tool: 'codex', title: 'Agent ' + id,
  project: 'paper-kite', plot, state, since: new Date(Date.now() - 60000).toISOString(),
  parent: null, subagents: 0, files: 0, commits: 0, pushed: false, ci: null, private: false },
  extra || {});
const frames = async n => { for (let i = 0; i < n; i++) { kick(); await wait(60); } };
const sr = () => document.getElementById('town-sr').textContent;
"""


def live_town(tmp_path: Path) -> Path:
    return build_town(tmp_path, LIVE_TOML)


# ----------------------------------------------------------------------------- #1 the version
def test_the_version_is_the_one_the_changelog_ships(tmp_path, capsys):
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    top = re.search(r"^## (\d+\.\d+\.\d+)", log, re.M)
    assert top and __version__ == top.group(1) == "0.2.0", (__version__, top and top.group(1))
    # pyproject reads it from the package (the wheel's name), and every place that shows it says it
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = { attr = "lamptown.__version__" }' in toml
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == "lamptown 0.2.0"
    p = tmp_path / "lamptown.toml"
    p.write_text("", encoding="utf-8")
    town = B.build(parse_config("", p), out_dir=tmp_path / "out").town
    details = (town.parent / "town-details.html").read_text(encoding="utf-8")
    assert "<footer>Lamptown 0.2.0." in details


# ----------------------------------------------------------------------------- #4 screen readers
BUSY = """[ag('a', 'waiting', 'gate'), ag('b', 'working', 'boathouse'), ag('c', 'error', 'lab'),
  ag('d', 'waiting', 'boathouse', { title: 'Ferry timetable', project: '' }),
  ag('e', 'ok', 'tower'), ag('ci1', 'working', 'boathouse', { tool: 'ci' })]"""


@browser
def test_the_screen_reader_text_says_the_live_agents(tmp_path):
    r = run(live_town(tmp_path), HELP + f"""
      const before = sr();
      feed({BUSY}, null, 3);
      const busy = sr();
      // the same news again (a heartbeat): the text is left alone
      let changed = 0;
      new MutationObserver(() => changed++).observe(document.getElementById('town-sr'),
        {{ childList: true, characterData: true, subtree: true }});
      feed({BUSY}, null, 3);
      await wait(50);
      const beat = changed;
      feed([ag('e', 'ok', 'tower'), ag('f', 'quiet', 'lab')]);
      const idle = sr();
      feed([ag('a', 'waiting', 'gate')], new Date(Date.now() - 600000).toISOString());   // stale
      const stale = sr();
      feed([ag('a', 'waiting', 'gate')]);
      const again = sr();
      return {{ before, busy, beat, idle, stale, again,
               gate: plotLabel('gate'), harbour: plotLabel('boathouse') }};""",
        query="?live=0")
    assert "Live agents" not in r["before"]
    busy = r["busy"]
    head = "Live agents: 1 working, 2 waiting for you, 1 failed, 1 done, and 3 more."
    assert busy.startswith(head), busy
    assert ("Waiting for you: Agent a (Paper Kite) at " + r["gate"]
            + "; Ferry timetable at " + r["harbour"] + ".") in busy, busy
    assert "ci1" not in busy and "Agent b" not in busy   # a CI run is no agent; waits are named
    assert r["beat"] == 0
    assert "Live agents" not in r["idle"]                # done and quiet only: nothing to say
    assert "Live agents" not in r["stale"]               # a stale payload is no news
    assert r["again"].startswith("Live agents: 1 waiting for you."), r["again"]


@browser
def test_the_screen_reader_names_at_most_five_waits(tmp_path):
    r = run(live_town(tmp_path), HELP + """
      feed(Array.from({ length: 8 }, (_, i) => ag('w' + i, 'waiting', 'gate')));
      return { text: sr() };""", query="?live=0")
    assert "8 waiting for you" in r["text"]
    assert "Agent w5" not in r["text"]
    assert "Agent w4 (Paper Kite) at Test room; and 3 more. " in r["text"], r["text"]


# ----------------------------------------------------------------------------- #5 the lighthouse
@browser
def test_the_lighthouse_skips_plots_named_like_object_keys(tmp_path):
    r = run(live_town(tmp_path), HELP + """
      const odd = ['constructor', 'toString', 'hasOwnProperty', '__proto__', 'valueOf'];
      feed(odd.map((p, i) => ag('o' + i, i % 2 ? 'error' : 'waiting', p)));
      await frames(6);
      const L = SEEN.lighthouse || {};
      const oddSeen = { errs: errs.slice(), targets: L.targets, mode: L.mode,
                        err: SEEN.lighthouseError || null };
      feed([ag('ok1', 'waiting', 'gate'), ag('o0', 'waiting', 'constructor')]);
      await frames(6);
      const M = SEEN.lighthouse || {};
      return { oddSeen, errs, targets: M.targets, count: M.count };""",
        query="?live=0&motion=1")
    assert r["oddSeen"]["errs"] == [] and r["oddSeen"]["err"] is None, r["oddSeen"]
    assert r["oddSeen"]["targets"] == [] and r["oddSeen"]["mode"] == "idle"
    assert r["errs"] == []
    assert r["targets"] == ["gate"] and r["count"] == 1
