"""CI runs in town (the ci_gh reader's records, tool ``ci``) in a real browser (headless Chrome).

A CI run is no agent: no Lamplighter walks for it and the lighthouse doesn't count it. It flies as
its project's pennant on the berth's boat (amber running, green, red), its berth's hover names it
by its workflow, and the keeper's book writes it as its own line (CI GREEN / CI RED). The probes
feed payloads with ``lamptownLive`` (``?live=0``: no polling). Every name here is made up.
"""

from __future__ import annotations

from importlib import resources

from _browser import browser
from test_town import FICTIONAL, build_town, run

pytestmark = browser

STILL = "?live=0&t=300"

HELP = """
const until = async (f, cap) => {
  for (let w = 0; w < (cap || 4000) && !f(); w += 30) await wait(30);
  return !!f();
};
const ago = m => new Date(Date.now() - m * 6e4).toISOString();
const A = (id, project, state, o) => Object.assign({
  id, tool: 'codex', title: 'Agent ' + id, project, plot: 'boathouse', state: state || 'working',
  since: ago(1), parent: null, subagents: 0, files: 0, commits: 0, pushed: false, ci: null,
  private: false,
}, o || {});
const CI_STATE = { running: 'working', green: 'ok', red: 'error' };
const CI = (id, project, ci, title) => A(id, project, CI_STATE[ci],
  { tool: 'ci', title: title || 'CI', ci });
let N = 0;
const feed = (agents, projects) => { N++; lamptownLive({
  spec: 'lamptown-live/1', at: new Date().toISOString(), rev: String(N),
  need: agents.filter(a => a.state === 'waiting').length, more: 0, agents,
  projects: projects.map((p, i) => ({
    id: p, name: p.replace(/-/g, ' ').toUpperCase(), berth: i, new: false })) }); };
const B = () => SEEN.berths || null;
const berth = id => (B() ? B().list.find(b => b.id === id) || null : null);
const figs = () => (SEEN.agents || []).map(f => f.id);
"""


def probe(tmp_path, body: str, query: str = STILL, frag: str = "", *, lighthouse=False) -> dict:
    if lighthouse:  # a live town ([live] on): it has the lighthouse
        from test_room_lighthouse import build_live

        return run(build_live(tmp_path), HELP + body, query, frag)
    return run(build_town(tmp_path, FICTIONAL), HELP + body, query, frag)


def test_a_ci_run_is_its_berths_pennant_not_a_figure(tmp_path):
    r = probe(tmp_path, """
      await until(() => B() !== null);
      feed([A('a1', 'paper-kite'), CI('ci-gh-1', 'paper-kite', 'running')], ['paper-kite']);
      await until(() => berth('paper-kite') && figs().includes('a1'));
      const running = { b: berth('paper-kite'), figs: figs(), lh: SEEN.lighthouse };
      feed([A('a1', 'paper-kite'), CI('ci-gh-1', 'paper-kite', 'red', 'Tests')], ['paper-kite']);
      await until(() => berth('paper-kite') && berth('paper-kite').ci === 'red');
      const red = { b: berth('paper-kite'), figs: figs(), lh: SEEN.lighthouse,
        tip: BERTHS.view().berths[0].tip, hooks: SEEN.agentHooks, more: SEEN.agentMore };
      return { running, red, err: SEEN.berthsError || SEEN.agentsError || null };""",
              lighthouse=True)
    assert r["err"] is None
    assert r["running"]["lh"]["shown"] is True
    run_, red = r["running"], r["red"]
    assert run_["b"]["ci"] == "running" and run_["b"]["agents"] == ["a1"]
    assert run_["figs"] == ["a1"]  # the CI run walks nowhere
    assert run_["lh"]["working"] == 1  # the lighthouse counts the agent only
    assert red["b"]["ci"] == "red" and red["figs"] == ["a1"]
    assert red["lh"]["mode"] != "fail" and red["lh"]["failed"] == 0  # no red alarm for CI
    assert "CI TESTS: RED" in red["tip"] and "1 AGENT ON THIS PROJECT" in red["tip"]
    assert red["more"] == 0 and not any("ci-gh-1" in v for v in red["hooks"].values())


def test_a_project_with_only_its_ci_run_still_gets_a_berth(tmp_path):
    r = probe(tmp_path, """
      await until(() => B() !== null);
      feed([CI('ci-gh-2', 'tide-api', 'green')], ['tide-api']);
      await until(() => berth('tide-api') !== null);
      return { b: berth('tide-api'), figs: figs(), tip: BERTHS.view().berths[0].tip,
        err: SEEN.berthsError || SEEN.agentsError || null };""")
    assert r["err"] is None
    assert r["b"]["ci"] == "green" and r["b"]["agents"] == [] and r["figs"] == []
    assert r["tip"][1] == "NO AGENT ON IT NOW" and "CI: GREEN" in r["tip"]


def test_the_book_writes_a_ci_run_as_its_own_line(tmp_path):
    # the keeper's book: its own fixtures (test_room_lighthouse.py); a CI record is one more line
    import json

    from test_room_lighthouse import DATES, book, day, history, live, live_agent

    ci = live_agent("ci-gh-1", "Tests", "working", tool="ci", ci="running")
    red = live_agent("ci-gh-1", "Tests", "error", tool="ci", ci="red")
    who = live_agent("codex-1", "Fix save slots", "ok", pushed=True)
    r = book(tmp_path, history({DATES[-1]: day(DATES[-1])}), """
      lamptownLive(fresh(%L1%));
      await until(() => S().lines.length === 2 && allWritten(), 4000);
      const running = S();
      lamptownLive(fresh(%L2%));
      await until(() => allWritten() && S().lines.some(l => /CI RED/.test(l.words)), 4000);
      return { running, red: S() };
    """.replace("%L1%", json.dumps(live([who, ci]))).replace("%L2%", json.dumps(live([who, red]))),
        flags=("--virtual-time-budget=20000",))
    lines = {ln["name"]: ln for ln in r["red"]["lines"]}
    assert "PAPER KITE / TESTS" in lines, list(lines)
    assert "CI RED" in lines["PAPER KITE / TESTS"]["words"]
    assert any(it["k"] == "F" for it in r["red"]["slip"]["items"])  # on the closing slip


def test_live_js_and_lighthouse_js_skip_ci_records_by_tool():
    for name in ("live.js", "berths.js", "lighthouse.js"):
        src = resources.files("lamptown.web").joinpath(name).read_text(encoding="utf-8")
        assert "'ci'" in src, name  # the one marker: tool "ci" (status/readers/ci_gh.py TOOL)
        assert "toast(" not in src
