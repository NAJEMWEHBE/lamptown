"""The lighthouse room (the keeper's handover book) in headless Chrome, on fixture history payloads.

Each case builds a live town (``[live]`` on, so the room script is inlined and
``parts.lighthouse`` exists), adds the lighthouse place if the exterior (L8) hasn't yet, hands
the page a ``lamptown-history/1`` payload (and sometimes a ``lamptown-live/1`` one) and walks
in. ``?live=0`` keeps the page from loading the build's own ``town-history.js`` over the
fixture. ``?at=`` pins the page's clock to midday UTC on 2026-11-04, so "today" is that date in
every usual time zone. A live payload counts only while it is fresh (its ``at`` under 30 s old by
the real clock), so the probes stamp it with ``fresh()`` as they hand it over. Every name here is
made up.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
from pathlib import Path

import pytest

from _browser import CHROME, browser, headless, run_browser
from lamptown import build as B
from lamptown.config import parse_config
from test_town import NOW, PROBE, run

pytestmark = browser

AT = "2026-11-04T12:00:00Z"
QUERY = f"?live=0&at={AT}"
DATES = [f"2026-10-{d}" for d in (29, 30, 31)] + [f"2026-11-0{d}" for d in (1, 2, 3, 4)]
KITE, MOSS = "PAPER KITE / FIX SAVE SLOTS", "MOSS ENGINE / TILE RENAME"
ROOM_JS = (Path(__file__).resolve().parents[1] / "src" / "lamptown" / "web" / "rooms"
           / "lighthouse.js")


def shots_dir() -> Path | None:
    """Where the review screenshots go: only when ``LAMPTOWN_SHOTS`` names a folder (never the
    repo by default: a test run writes nothing outside its tmp_path)."""
    want = os.environ.get("LAMPTOWN_SHOTS", "").strip()
    return Path(want) if want else None


def build_live(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    toml = f'[live]\nstatus_dir = "{(tmp_path / "status").as_posix()}"\n'
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    return B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW).town


def agent(name: str, tool: str = "codex", plot: str = "boathouse", aid: str = "") -> dict:
    return {"id": aid or "codex-" + name.lower().replace(" ", "-").replace("/", ""),
            "name": name, "plot": plot, "tool": tool}


def day(date: str, agents: list | None = None, events: list | None = None,
        minutes: int = 1440) -> dict:
    return {"date": date, "today": date == DATES[-1], "minutes": minutes,
            "agents": agents or [], "events": events or []}


def history(days: dict[str, dict] | None = None) -> dict:
    days = days or {}
    return {"spec": "lamptown-history/1", "at": AT,
            "days": [days.get(d) or day(d) for d in DATES]}


# a full working day: one agent of each fate, and a new project
MIXED_AGENTS = [agent(KITE), agent(MOSS, "claude-code"),
                agent("TIDEBOOK / CI CACHE"), agent("OTTER / MAP IMPORT", "claude-code"),
                agent("PUFFIN / SHOP PAGE"), agent("HERON", "", aid="proj-heron")]
MIXED_EVENTS = [
    [547, 0, "start"], [550, 1, "start"], [552, 2, "start"], [555, 3, "start"],
    [557, 4, "start"], [560, 0, "files", 12], [561, 0, "commit", 1], [565, 2, "files", 3],
    [566, 1, "wait"], [570, 5, "project"], [575, 2, "fail"], [580, 3, "quiet"],
    [590, 0, "push"], [596, 0, "ci", 1], [600, 0, "done"], [605, 4, "files", 2],
]


def mixed(date: str) -> dict:
    return day(date, MIXED_AGENTS, MIXED_EVENTS)


PRE = """
const until = async (f, cap) => {
  for (let w = 0; w < (cap || 4000) && !f(); w += 50) await wait(50);
  return !!f();
};
if (!isPlace('lighthouse')) addPlace('lighthouse', { hit: [330, 190, 30, 30] });
lamptownHistory(%HIST%);
enterRoom('lighthouse', { instant: true, history: false });
await wait(120);
const S = () => RS.room.seen();
const tab = i => { RS.room.targets().find(t => t.id === 'tab-' + i).onClick(); kick(); };
const key = (k, mods) => {
  RS.room.key(Object.assign({ key: k, shiftKey: false, preventDefault() {} }, mods)); kick();
};
// a heartbeat written just now; a wall-clock time here
const fresh = p => Object.assign({}, p, { at: new Date().toISOString() });
const local = (y, mo, d, h, mi) => new Date(y, mo - 1, d, h, mi).toISOString();
const byName = s => Object.fromEntries(s.lines.map(l => [l.name, l]));
const allWritten = () => S().lines.every(l => l.written === l.words);
"""


def book(tmp_path: Path, hist: dict, body: str, query: str = QUERY, **kw) -> dict:
    return run(build_live(tmp_path), PRE.replace("%HIST%", json.dumps(hist)) + body,
               query=query, **kw)


def by_name(seen: dict) -> dict:
    return {ln["name"]: ln for ln in seen["lines"]}


# ----------------------------------------------------------------------------- the lines
def test_lines_and_status_words_of_a_past_day(tmp_path):
    r = book(tmp_path, history({DATES[3]: mixed(DATES[3])}),
             "tab(3); await wait(200); return { s: S(), id: RS.id };")
    s = r["s"]
    assert r["id"] == "lighthouse" and s["tab"] == 3 and not s["day"]["today"]
    assert s["day"] == {"date": DATES[3], "today": False, "name": "SUNDAY", "minutes": 1440,
                        "end": 1440, "agents": 5, "projects": 1, "events": 16, "logged": True}
    assert s["tabs"] == ["THU", "FRI", "SAT", "SUN", "MON", "TUE", "WED*"]
    L = by_name(s)
    assert [ln["name"] for ln in s["lines"]][:3] == [KITE, MOSS, "TIDEBOOK / CI CACHE"]
    # PUSHED gives way to the CI word when the page is tight
    assert L[KITE]["words"] == "IN 09:07, CI GREEN, DONE 10:00"
    assert L[KITE]["tool"] == "CODEX"
    assert L[MOSS]["words"] == "IN 09:10, WAITING SINCE 09:26"
    assert L[MOSS]["tool"] == "CLAUDE"
    assert L["TIDEBOOK / CI CACHE"]["words"] == "IN 09:12, FAILED 09:35"
    assert L["OTTER / MAP IMPORT"]["words"] == "IN 09:15, QUIET SINCE 09:40"
    puffin = L["PUFFIN / SHOP PAGE"]
    assert puffin["words"] == "IN 09:17" and puffin["state"] == "work"
    assert L["HERON"]["words"] == "NEW PROJECT 09:30"
    # idle on a finished day: everything is written, no pen still moving
    assert all(ln["written"] == ln["words"] for ln in s["lines"])
    assert s["mode"] == "idle" and s["replay"] is None and s["slip"] is None


def test_counts_are_written_only_when_the_agent_finishes(tmp_path):
    r = book(tmp_path, history({DATES[3]: mixed(DATES[3])}), """
      tab(3); await wait(100);
      RS.room.seek(592); const mid = byName(S()); RS.room.seek(601); const end = byName(S());
      return { mid, end, failed: end['TIDEBOOK / CI CACHE'], working: end['PUFFIN / SHOP PAGE'] };
    """)
    assert r["mid"][KITE]["words"] == "IN 09:07, PUSHED" and r["mid"][KITE]["counts"] is None
    assert r["end"][KITE]["words"].endswith("DONE 10:00")
    assert r["end"][KITE]["counts"] == {"files": 12, "commits": 1}
    assert r["failed"]["counts"] == {"files": 3, "commits": 0}   # failing finishes it too
    assert r["working"]["counts"] is None                         # still working: no count yet


# ----------------------------------------------------------------------------- the replay
def busy_day(date: str, n: int = 40) -> dict:
    agents, events = [], []
    for i in range(n):
        agents.append(agent(f"PROJECT {i % 9} / TASK NUMBER {i}",
                            "codex" if i % 2 else "claude-code", aid=f"codex-busy-{i}"))
    for i in range(n):
        t = 300 + i * 17
        events += [[t, i, "start"]] + [[t + 2 + k * 3, i, "files", 4] for k in range(12)]
        events += [[t + 20, i, "commit", 2], [t + 40, i, "push"],
                   [t + 44, i, "ci", 0 if i % 7 == 0 else 1]]
        events += [[t + 45, i, "fail"] if i % 11 == 3 else [t + 46, i, "done"]]
    events.sort(key=lambda e: e[0])
    return day(date, agents, events)


def quiet_day(date: str) -> dict:
    return day(date, [agent("KELP / NIGHT BACKUP", "codex")],
               [[181, 0, "start"], [186, 0, "files", 2], [192, 0, "done"]])


def test_replay_lasts_about_a_minute_and_quiet_days_play_faster(tmp_path):
    hist = history({DATES[1]: busy_day(DATES[1]), DATES[2]: quiet_day(DATES[2])})
    r = book(tmp_path, hist, """
      const plans = S().plans;
      tab(2); await wait(50); key('p'); const t0 = performance.now();
      const started = S().mode;
      const inMid = () => { const x = S(); return x.replay && x.replay.minute > 150
                                                 && x.replay.minute < 200; };
      const mid = await until(inMid, 20000) && S();
      await until(() => S().slip, 30000);
      const took = (performance.now() - t0) / 1000, after = S();
      return { plans, started, took, after,
               midMinute: mid && mid.replay.minute, midClock: mid && mid.replay.clock };
    """, flags=("--virtual-time-budget=120000",))
    busy, quiet, empty = r["plans"][1], r["plans"][2], r["plans"][0]
    assert 45 <= busy <= 70, r["plans"]          # a busy day reads in about a minute
    assert quiet < busy / 4 and quiet < 12        # a quiet day plays much faster
    assert empty < 3                               # an empty day is a quick sweep
    assert r["started"] == "play"
    assert abs(r["took"] - quiet) < 1.5, (r["took"], quiet)   # it really took that long
    assert r["midClock"] and r["midClock"].startswith("0")
    a = r["after"]
    assert a["mode"] == "idle" and a["slip"]["past"] is True and a["slip"]["items"] == []
    assert a["lines"][0]["words"] == "IN 03:01, DONE 03:12"
    assert a["lines"][0]["counts"]["files"] == 2


def test_play_pause_scrub_and_keys(tmp_path):
    r = book(tmp_path, history({DATES[3]: mixed(DATES[3])}), """
      tab(3); key('p'); await wait(400); key('p');
      const p1 = S(); await wait(600); const p2 = S();
      RS.room.seek(580); const s1 = S();
      key('ArrowRight'); const s2 = S(); key('ArrowLeft'); key('ArrowLeft'); const s3 = S();
      key('Home'); const s4 = S(); key('p'); await wait(300); const s5 = S();
      key('End'); await wait(100); const s6 = S();
      key('ArrowUp'); const up = S();
      return { p1, p2, s1, s2, s3, s4, s5, s6, up };
    """, flags=("--virtual-time-budget=30000",))
    assert r["p1"]["mode"] == "pause"
    assert r["p1"]["replay"]["rt"] == r["p2"]["replay"]["rt"]   # paused: the clock stops
    assert round(r["s1"]["replay"]["minute"]) == 580 and r["s1"]["replay"]["clock"] == "09:40"
    assert round(r["s2"]["replay"]["minute"]) == 640
    assert round(r["s3"]["replay"]["minute"]) == 520
    assert r["s4"]["replay"]["minute"] == 0 and r["s4"]["mode"] == "pause"
    assert r["s5"]["mode"] == "play" and r["s5"]["replay"]["rt"] > 0
    assert r["s6"]["mode"] == "idle" and r["s6"]["slip"]["past"] is True
    assert r["up"]["tab"] == 2


def test_dst_days_keep_their_length(tmp_path):
    long_day = day(DATES[3], [agent("WREN / LATE PASS")],
                   [[30, 0, "start"], [1490, 0, "done"]], minutes=1500)
    short_day = day(DATES[1], [agent("GULL POST / EARLY")],
                    [[10, 0, "start"], [1370, 0, "done"]], minutes=1380)
    r = book(tmp_path, history({DATES[3]: long_day, DATES[1]: short_day}), """
      tab(3); const a = S(); RS.room.seek(1495); const a2 = S();
      tab(1); const b = S(); RS.room.seek(5000); const b2 = S();
      return { a, a2, b, b2 };
    """)
    assert r["a"]["day"]["minutes"] == 1500 and r["a"]["day"]["end"] == 1500
    assert r["a"]["lines"][0]["state"] == "done"   # the event in the 25th hour is in the book
    assert round(r["a2"]["replay"]["minute"]) == 1495
    assert r["b"]["day"]["minutes"] == 1380 and r["b"]["day"]["end"] == 1380
    assert r["b2"]["replay"]["minute"] <= 1380
    assert r["a"]["plans"][3] > 0 and r["a"]["plans"][1] > 0


# ----------------------------------------------------------------------------- the slip
def test_slip_is_sorted_by_urgency_and_past_days_say_open_at_close(tmp_path):
    hist = history({DATES[-1]: mixed(DATES[-1]), DATES[4]: mixed(DATES[4])})
    r = book(tmp_path, hist, """
      const today = S(); tab(4); const past = S();
      key('End'); await wait(200); const pastEnd = S();
      return { today, past, pastEnd, plate: RS.room.plate(), drawn: today.drawn };
    """)
    t = r["today"]["slip"]
    assert t["title"].startswith("STILL OPEN, TODAY") and t["past"] is False
    assert t["groups"] == ["NEEDS YOU", "FAILED", "GONE QUIET", "STILL WORKING"]
    assert [i["k"] for i in t["items"]] == ["W", "F", "Q", "R"]
    assert [i["name"] for i in t["items"]] == [MOSS, "TIDEBOOK / CI CACHE",
                                              "OTTER / MAP IMPORT", "PUFFIN / SHOP PAGE"]
    assert t["items"][0]["what"].startswith("WAITING ")
    assert r["plate"] == {"num": "1", "state": "waiting"}
    assert r["past"]["slip"] is None                 # a past day hands nothing until its end
    p = r["pastEnd"]["slip"]
    assert p["past"] is True and p["title"] == "OPEN AT CLOSE OF MONDAY 2 NOV"
    assert p["groups"] == ["WAITING AT CLOSE", "FAILED", "GONE QUIET", "WORKING AT CLOSE"]
    assert "NEEDS YOU" not in json.dumps(r["pastEnd"]["drawn"])


def test_ci_red_counts_as_failed_and_waits_sort_longest_first(tmp_path):
    agents = [agent("A / ONE"), agent("B / TWO"), agent("C / THREE")]
    events = [[100, 0, "start"], [101, 1, "start"], [102, 2, "start"], [110, 1, "wait"],
              [200, 2, "wait"], [300, 0, "push"], [305, 0, "ci", 0], [306, 0, "done"]]
    r = book(tmp_path, history({DATES[5]: day(DATES[5], agents, events)}),
             "tab(5); key('End'); return S().slip;")
    assert [(i["k"], i["name"]) for i in r["items"]] == [
        ("W", "B / TWO"), ("W", "C / THREE"), ("F", "A / ONE")]
    assert r["items"][2]["what"] == "CI RED 05:05"


# ----------------------------------------------------------------------------- empty, live, names
def test_empty_days_and_no_log(tmp_path):
    r = book(tmp_path, history(), "tab(2); await wait(200); return S();")
    assert r["lines"] == [] and r["day"]["agents"] == 0 and r["day"]["logged"] is True
    assert "A QUIET DAY." in r["drawn"] and "NO AGENTS THIS DAY" in r["drawn"]
    page = build_live(tmp_path / "nolog")
    r2 = run(page, PRE.replace("lamptownHistory(%HIST%);", "") + "return S();", query=QUERY)
    assert r2["lines"] == [] and "NO LOG YET." in r2["drawn"] and len(r2["tabs"]) == 7
    assert r2["slip"]["items"] == [] and r2["day"]["today"] is True


def live(agents: list, projects: list | None = None) -> dict:
    return {"spec": "lamptown-live/1", "at": AT, "rev": "",
            "need": sum(a["state"] == "waiting" for a in agents),
            "more": 0, "agents": agents, "projects": projects or []}


def live_agent(aid: str, title: str, state: str, project: str = "paper-kite", **more) -> dict:
    return {"id": aid, "tool": "codex", "title": title, "project": project,
            "plot": "boathouse", "state": state, "since": "2026-11-04T11:59:00Z",
            "parent": None, "subagents": 0, "files": 0, "commits": 0,
            "pushed": False, "ci": None, "private": False, **more}


def test_live_payload_writes_today_in_place(tmp_path):
    today = day(DATES[-1], [agent(KITE, aid="codex-1")], [[300, 0, "start"]])
    live1 = live([live_agent("codex-1", "Fix save slots", "waiting"),
                  live_agent("codex-2", "Tide tables", "working", project="moss-engine")],
                 [{"id": "paper-kite", "name": "PAPER KITE"},
                  {"id": "moss-engine", "name": "MOSS ENGINE"}])
    live2 = live([live_agent("codex-1", "Fix save slots", "ok", files=14, commits=2,
                             pushed=True, ci="green")],
                 [{"id": "paper-kite", "name": "PAPER KITE"}])
    r = book(tmp_path, history({DATES[-1]: today}), """
      const before = S();
      lamptownLive(fresh(%LIVE1%));
      await until(() => S().lines.length === 2 && allWritten(), 4000);
      const after = S();
      lamptownLive(fresh(%LIVE2%));
      await until(() => allWritten() && S().lines[0].state === 'done', 4000);
      const done = S();
      tab(2);
      const d = JSON.parse(JSON.stringify(D)); d.rooms.lighthouse.label = 'changed';
      lamptownData(d); await wait(100);
      return { before, after, done, swapped: S(), id: RS.id, plate: RS.room.plate() };
    """.replace("%LIVE1%", json.dumps(live1)).replace("%LIVE2%", json.dumps(live2)),
        flags=("--virtual-time-budget=20000",))
    assert [ln["state"] for ln in r["before"]["lines"]] == ["work"]
    a = by_name(r["after"])
    assert a[KITE]["state"] == "wait"
    assert a[KITE]["words"].startswith("IN 05:00, WAITING")
    assert a["MOSS ENGINE / TIDE TABLES"]["state"] == "work"
    assert r["after"]["slip"]["items"][0]["k"] == "W"
    k = r["done"]["lines"][0]
    assert k["state"] == "done" and "CI GREEN, DONE" in k["words"]
    assert k["counts"] == {"files": 14, "commits": 2}       # the live totals, at the finish
    assert r["id"] == "lighthouse"
    assert r["swapped"]["tab"] == 2                          # a data swap keeps your page


def test_names_only_never_a_path(tmp_path):
    agents = [agent("C:\\Users\\you\\work\\paper-kite / FIX C:\\Users\\you\\work\\notes.txt",
                    aid="codex-a"),
              agent("/work/moss-engine/src / READ /work/moss-engine/README.md", aid="codex-b")]
    events = [[100, 0, "start"], [101, 1, "start"], [130, 1, "done"]]
    now = live([live_agent("codex-9", "Edit /work/kelp/app.py", "working", project="kelp")])
    r = book(tmp_path, history({DATES[-1]: day(DATES[-1], agents, events)}), """
      lamptownLive(fresh(%LIVE%));
      await until(() => S().lines.length === 3 && allWritten(), 4000);
      const s = S(); RS.room.seek(120); kick(); await wait(200); const r2 = S();
      return { s, tips: RS.room.targets().map(t => t.tip), r2 };
    """.replace("%LIVE%", json.dumps(now)))
    s = r["s"]
    text = json.dumps([s["drawn"], r["r2"]["drawn"], r["tips"], s["lines"], s["slip"]])
    assert "\\\\" not in text and "/WORK" not in text.upper() and "USERS" not in text.upper()
    names = [ln["name"] for ln in s["lines"]]
    assert names[0] == "PAPER-KITE / FIX NOTES.TXT" and names[1] == "SRC / READ README.MD"
    assert "KELP / EDIT APP.PY" in names


# ----------------------------------------------------------------------------- busy day, shots
def shot(page: Path, body: str, png: Path, query: str, budget: int) -> Path:
    text = page.read_text(encoding="utf-8")
    i = text.rindex("</body>")
    case = page.with_name(f"shot-{png.stem}.html")
    probe = PROBE.replace("%BODY%", body + "; return {};")
    case.write_text(text[:i] + probe + text[i:], encoding="utf-8")
    png.parent.mkdir(parents=True, exist_ok=True)
    cmd = headless(page.parent / "chrome-profile", "--window-size=1600,900",
                   f"--virtual-time-budget={budget}", "--allow-file-access-from-files",
                   "--hide-scrollbars", f"--screenshot={png}", case.as_uri() + query)
    run_browser(cmd, timeout=180)
    return png


def test_a_busy_day_of_40_agents_stays_readable(tmp_path):
    hist = history({DATES[4]: busy_day(DATES[4])})
    r = book(tmp_path, hist, """
      tab(4); const a = S(); key('PageDown'); const b = S();
      key('PageDown'); key('PageDown'); const c = S();
      RS.room.seek(720); const mid = S();
      return { a, b, c, mid };
    """)
    a = r["a"]
    assert a["day"]["agents"] == 40 and a["leaves"] == 3 and a["leaf"] == 0
    assert r["b"]["leaf"] == 1 and r["c"]["leaf"] == 2
    assert len({ln["name"] for ln in a["lines"]}) == 40
    assert all(ln["words"] for ln in a["lines"])
    # a fail and a red CI on the same line still fit one line of the page
    assert any("CI RED" in ln["words"] for ln in a["lines"])
    # PUSHED gives way to a CI word on every line alike, red or green (not only on tight lines)
    assert any("CI GREEN" in ln["words"] for ln in a["lines"])
    assert not [w for w in (ln["words"] for ln in a["lines"]) if "PUSHED" in w and "CI " in w]
    assert r["mid"]["leaf"] >= 1                 # the replay turned the leaf to where the pen is
    png = shot(build_live(tmp_path / "s"), PRE.replace("%HIST%", json.dumps(hist))
               + "tab(4); RS.room.seek(760); key('p')", tmp_path / "busy.png", QUERY, 5000)
    assert png.is_file() and png.stat().st_size > 5000


# ------------------------------------------------------------------------- L9-fix: carried, stale
TUE = DATES[5]   # 2026-11-03, the day before "today"


def dur(minutes: float) -> str:
    """The book's duration words: 1D 2H, 13H 30M, 45M."""
    m = round(minutes)
    d, h, mm = m // 1440, m % 1440 // 60, m % 60
    if d:
        return f"{d}D" + (f" {h}H" if h else "")
    return f"{h}H" + (f" {mm}M" if mm else "") if h else f"{mm}M"


def carried_history() -> dict:
    """KELP waited for you from Tuesday 22:30 and no line of today's log has it yet; OTTER worked on
    from Tuesday 21:00 and got an answer today at 08:00; GULL POST finished on Tuesday."""
    tue = day(TUE, [agent("KELP / NIGHT BACKUP", aid="codex-kelp"),
                    agent("OTTER / MAP IMPORT", aid="codex-otter"),
                    agent("GULL POST / EARLY", aid="codex-gull")],
              [[1260, 1, "start"], [1270, 0, "start"], [1280, 2, "start"],
               [1300, 2, "done"], [1320, 1, "wait"], [1350, 0, "wait"]])
    today = day(DATES[-1], [agent("OTTER / MAP IMPORT", aid="codex-otter"),
                            agent("GULL POST / EARLY", aid="codex-gull")],
                [[480, 0, "answer"], [500, 1, "start"]])
    return history({TUE: tue, DATES[-1]: today})


def test_a_wait_from_yesterday_keeps_its_real_start(tmp_path):
    """Finding 1a: a line open since before midnight says when it really began, and its wait
    counts from then (not "IN 00:00, WAITING SINCE 00:00" and the hours since midnight)."""
    now = live([live_agent("codex-kelp", "Night backup", "waiting", project="kelp"),
                live_agent("codex-wren", "Late pass", "waiting", project="wren")],
               [{"id": "kelp", "name": "KELP"}, {"id": "wren", "name": "WREN"}])
    r = book(tmp_path, carried_history(), """
      const p = %LIVE%;
      p.agents[0].since = local(2026, 11, 3, 22, 30);   // the history's own Tuesday 22:30
      p.agents[1].since = '2026-11-03T10:00:00Z';        // only the live payload knows this one
      lamptownLive(fresh(p));
      await until(() => S().lines.length === 4 && allWritten(), 4000);
      const s = S(), w = new Date('2026-11-03T10:00:00Z');
      return { s, plate: RS.room.plate(), wren: String(w.getHours()).padStart(2, '0') + ':'
               + String(w.getMinutes()).padStart(2, '0'), wrenMin: (K.nowMs() - w) / 6e4,
               kelpMin: (K.nowMs() - new Date(2026, 10, 3, 22, 30)) / 6e4 };
    """.replace("%LIVE%", json.dumps(now)), flags=("--virtual-time-budget=20000",))
    s, L = r["s"], by_name(r["s"])
    kelp, otter, wren = L["KELP / NIGHT BACKUP"], L["OTTER / MAP IMPORT"], L["WREN / LATE PASS"]
    assert kelp["state"] == "wait" and otter["state"] == "work" and wren["state"] == "wait"
    # from Tuesday (first seen 21:10 in the log), waiting since Tuesday 22:30 (shortened to fit)
    assert re.fullmatch(r"FROM TUE( 21:10)?, WAITING( SINCE)? TUE 22:30", kelp["words"]), kelp
    # only the live payload knows this one: from its state's day, with the real time
    assert re.fullmatch(rf"FROM TUE, WAITING( SINCE)? TUE {r['wren']}", wren["words"]), wren
    assert otter["words"] == "FROM TUE 21:00", otter
    assert L["GULL POST / EARLY"]["words"] == "IN 08:20"   # done on Tuesday: a fresh start today
    drawn = json.dumps(s["drawn"])
    assert "00:00" not in json.dumps([ln["words"] for ln in s["lines"]]) and "IN 00:00" not in drawn
    # the slip counts each wait from its real start, the longest first
    items = s["slip"]["items"]
    assert [(i["k"], i["name"]) for i in items[:2]] == [("W", "WREN / LATE PASS"),
                                                         ("W", "KELP / NIGHT BACKUP")]
    assert items[0]["what"] == "WAITING " + dur(r["wrenMin"]), items[0]   # 1D 2H, not 12H
    assert items[1]["what"] == "WAITING " + dur(r["kelpMin"]), items[1]
    otter_item = next(i for i in items if i["name"] == "OTTER / MAP IMPORT")
    assert otter_item["what"] == "WORKING SINCE TUE 21:00"
    assert r["plate"] == {"num": "2", "state": "waiting"}
    # the carried lines are no events of today: today's header counts the two real ones
    assert s["day"]["events"] == 2 and s["day"]["agents"] == 4


def test_a_stale_live_payload_is_not_merged(tmp_path):
    """Finding 1b: a stopped Watcher's payload (the corner says BUILT) adds nothing to the book;
    a fresh one does, until it too goes stale."""
    now = live([live_agent("codex-kelp", "Night backup", "waiting", project="kelp",
                           since="2026-11-04T11:00:00Z")], [{"id": "kelp", "name": "KELP"}])
    r = book(tmp_path, history(), """
      const p = %LIVE%;
      lamptownLive(Object.assign({}, p, { at: new Date(Date.now() - 3600e3).toISOString() }));
      await wait(300);
      const stale = { s: S(), on: SEEN.liveOn, live: K.live(), plate: RS.room.plate() };
      lamptownLive(fresh(p));
      await until(() => S().lines.length === 1 && allWritten(), 4000);
      const fed = { s: S(), plate: RS.room.plate() };
      await until(() => S().lines.length === 0, 40000);
      const gone = { s: S(), live: K.live(), plate: RS.room.plate() };
      return { stale, fed, gone, load: K.liveScript('town-history.js') };
    """.replace("%LIVE%", json.dumps(now)), flags=("--virtual-time-budget=60000",))
    st = r["stale"]
    assert st["live"] is None and st["s"]["lines"] == [] and st["plate"] == {"num": ""}
    assert "KELP / NIGHT BACKUP" not in json.dumps(st["s"]["drawn"])
    assert r["fed"]["s"]["lines"][0]["state"] == "wait"
    assert r["fed"]["plate"] == {"num": "1", "state": "waiting"}
    assert r["gone"]["live"] is None and r["gone"]["s"]["lines"] == []   # 30 s on: old news
    assert r["gone"]["plate"] == {"num": ""}
    assert r["load"] == 0                                # ?live=0: the kit loads no live file


# ----------------------------------------------------------------------------- L9-fix: keys, clicks
def test_keys_with_modifiers_are_the_browsers(tmp_path):
    """Finding 2: Alt+Left (Back), Ctrl+P (print), Meta+... reach the browser untouched."""
    r = book(tmp_path, history({DATES[3]: mixed(DATES[3])}), """
      tab(3);
      const press = (k, mods) => { const e = new KeyboardEvent('keydown', Object.assign(
        { key: k, bubbles: true, cancelable: true }, mods)); document.dispatchEvent(e); kick();
        return e.defaultPrevented; };
      const altLeft = press('ArrowLeft', { altKey: true }), m1 = S().mode;
      const ctrlP = press('p', { ctrlKey: true }), m2 = S().mode;
      const metaP = press('P', { metaKey: true }), m3 = S().mode;
      const altHome = press('Home', { altKey: true }), m4 = S().mode;
      press('Home');
      const shiftRight = press('ArrowRight', { shiftKey: true }), s5 = S();
      const plainP = press('p'), m6 = S().mode;
      return { altLeft, ctrlP, metaP, altHome, modes: [m1, m2, m3, m4], shiftRight, s5, m6 };
    """)
    assert not r["altLeft"] and not r["altHome"]
    assert r["modes"] == ["idle"] * 4
    assert r["shiftRight"] is True and r["s5"]["mode"] == "pause"   # Shift still scrubs 3 hours
    assert round(r["s5"]["replay"]["minute"]) == 180
    assert r["m6"] == "play"


def test_clicking_a_line_mid_replay_works_after_a_live_payload(tmp_path):
    """Finding 3: a live payload rebuilds today mid-replay; a click on a line still jumps."""
    now = live([live_agent("codex-new", "Tide tables", "working", project="kite")],
               [{"id": "kite", "name": "PAPER KITE"}])
    r = book(tmp_path, history({DATES[-1]: mixed(DATES[-1])}), """
      key('s'); RS.room.seek(600); key('p'); await wait(250);
      const before = S().replay;
      lamptownLive(fresh(%LIVE%));
      const mid = S();   // the room sees the payload: today is rebuilt, the replay keeps its copy
      const line = RS.room.targets().find(t => t.id.startsWith('line-')
                                              && t.label.startsWith('MOSS ENGINE'));
      line.onClick(); kick();
      const after = S();
      return { before, mid, after };
    """.replace("%LIVE%", json.dumps(now)), flags=("--virtual-time-budget=20000",))
    assert r["before"]["minute"] >= 600 and r["before"]["playing"] is True
    assert not any(ln["id"] == "codex-new" for ln in r["mid"]["lines"])   # still the old copy
    a = r["after"]["replay"]
    assert 540 <= a["minute"] <= 566, a       # just before MOSS ENGINE starts waiting (09:26)
    assert a["playing"] is True
    assert any(ln["id"] == "codex-new" for ln in r["after"]["lines"])   # the newest day replays


# ----------------------------------------------------------------------------- L9-fix: names
def test_titles_with_a_slash_are_kept_and_paths_still_cut(tmp_path):
    """Finding 4: "I/O", "CI/CD", "TCP/IP" are words of a title, not paths."""
    agents = [agent("TIDEBOOK / FIX I/O BUG", aid="a1"), agent("OTTER / CI/CD CACHE", aid="a2"),
              agent("KELP / TCP/IP RETRY", aid="a3"),
              agent("WREN / READ ~/notes/today.md AND ./src/app.py", aid="a4")]
    events = [[100, 0, "start"], [101, 1, "start"], [102, 2, "start"], [103, 3, "start"]]
    r = book(tmp_path, history({DATES[-1]: day(DATES[-1], agents, events)}), "return S();")
    names = [ln["name"] for ln in r["lines"]]
    assert names == ["TIDEBOOK / FIX I/O BUG", "OTTER / CI/CD CACHE", "KELP / TCP/IP RETRY",
                     "WREN / READ TODAY.MD AND APP.PY"]


ARABIC = "تقرير الليل"   # "the night report" (a made-up title)


def test_names_in_any_script_are_drawn(tmp_path):
    """Finding 5: the kit's glyph fallback: accented letters are their base letters in the pixel
    font, other scripts a crisp system-font run (Arabic right to left); plain ASCII is unchanged."""
    agents = [agent(f"KELP / {ARABIC}", aid="a1"), agent("CAFÉ / ÜBER STRASSE", aid="a2"),
              agent("PUFFIN / ЗАПУСК 🚀", aid="a3")]
    events = [[100, 0, "start"], [101, 1, "start"], [102, 2, "start"]]
    r = book(tmp_path, history({DATES[-1]: day(DATES[-1], agents, events)}), """
      await until(allWritten, 4000);
      const ink = c => { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; };
      const px = c => c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      const partial = c => { const d = px(c);
        let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] && d[i] < 255) n++; return n; };
      const same = (a, b) => { const x = px(a),
        y = b.getContext('2d').getImageData(0, 0, b.width, b.height).data;
        return a.width === b.width && a.height === b.height && x.every((v, i) => v === y[i]); };
      // right to left: alef (a tall stroke, the first letter) must stand at the run's right end
      const rtl = K.label('ا ببب', '#ffffff'), rc = rtl.getContext('2d')
        .getImageData(0, 0, rtl.width, rtl.height).data;
      let tallX = -1, tallTop = 99;
      for (let x = 0; x < rtl.width; x++) for (let y = 0; y < rtl.height; y++)
        if (rc[(y * rtl.width + x) * 4 + 3] && y < tallTop) { tallTop = y; tallX = x; }
      const ar = K.label('KELP / ' + '%AR%', '#ffffff'), ascii = K.label('KELP', '#ffffff');
      return { s: S(), arW: K.textW('%AR%'), arInk: ink(K.label('%AR%', '#ffffff')),
        arPartial: partial(ar), arH: ar.height, arOy: ar.oy,
        asciiH: ascii.height, asciiOy: ascii.oy,
        accent: same(K.label('CAFÉ ÜBER', '#ffffff'), K.label('CAFE UBER', '#ffffff')),
        accentW: K.textW('ÉÜÇÑ') === K.textW('EUCN'),
        cyr: ink(K.label('ЗАПУСК', '#ffffff')), emoji: ink(K.label('🚀', '#ffffff')),
        override: K.textW('\u202eKELP') === K.textW('KELP'),
        cut: K.fitText('PUFFIN 🚀🚀🚀🚀🚀🚀', 40), tallX, rtlW: rtl.width };
    """.replace("%AR%", ARABIC))
    names = [ln["name"] for ln in r["s"]["lines"]]
    assert names == [f"KELP / {ARABIC}", "CAFÉ / ÜBER STRASSE", "PUFFIN / ЗАПУСК 🚀"]
    assert r["arW"] > 15 and r["arInk"] > 30           # drawn, not blank
    assert r["arPartial"] == 0                         # whole pixels only: no smoothing halo
    assert (r["arH"], r["arOy"]) == (9, 2) and (r["asciiH"], r["asciiOy"]) == (5, 0)
    assert r["accent"] and r["accentW"]                # CAFÉ reads CAFE in the pixel font
    assert r["cyr"] > 20 and r["emoji"] > 5
    assert r["override"]                               # a direction override is dropped
    assert "�" not in r["cut"] and not re.search(r"[\ud800-\udfff]", r["cut"])
    assert r["tallX"] > r["rtlW"] / 2, r               # the first Arabic letter is on the right


# ----------------------------------------------------------------------------- L9-fix: hidden page
def test_history_reload_pauses_while_the_page_is_hidden(tmp_path):
    """Finding 6: no town-history.js loads while the page is hidden (a minimised app); one load
    as it shows again. Live is on here (no ?live=0): the build wrote town-history.js."""
    r = book(tmp_path, history(), """
      await until(() => S().histLoads >= 2, 8000);   // the fixture, then the file itself
      const shown = S().histLoads;
      let hidden = true;
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
      document.dispatchEvent(new Event('visibilitychange'));
      await wait(130000);
      const whileHidden = S().histLoads;
      hidden = false; document.dispatchEvent(new Event('visibilitychange'));
      await until(() => S().histLoads > whileHidden, 8000);
      return { shown, whileHidden, back: S().histLoads, bad: K.liveScript('../x.js'),
               scripts: document.querySelectorAll('script[data-live]').length };
    """, query=f"?at={AT}", flags=("--virtual-time-budget=200000",))
    assert r["whileHidden"] == r["shown"], r
    assert r["back"] == r["shown"] + 1, r
    assert r["bad"] == 0                    # only town-<name>.js next to the page
    assert r["scripts"] <= 2                # no script element left behind


def test_the_room_uses_only_the_kit():
    """Finding 7: the room reaches the town only through the documented kit (docs/kit-k.md)."""
    src = ROOM_JS.read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", src, flags=re.S)
    for bad in (r"\bLIVE\.", r"(?<!K\.)\bliveScript\(", r"\btoLogical\b", r"(?<!\.)\bkick\(",
                r"\bRS\.", r"\bSEEN\b", r"\bPT\[", r"\bD\.rooms"):
        assert not re.search(bad, code), bad
    docs = (ROOM_JS.parents[4] / "docs" / "kit-k.md").read_text(encoding="utf-8")
    for name in ("live()", "liveScript(name, done)", "pointer(event)", "redraw()"):
        assert f"`{name}`" in docs, name


# ----------------------------------------------------------------------------- L9-fix: day count
def days_back(n: int) -> list[str]:
    end = dt.date(2026, 11, 4)
    return [(end - dt.timedelta(days=k)).isoformat() for k in range(n - 1, -1, -1)]


def test_the_tabs_follow_history_days(tmp_path):
    """Finding 8: as many tabs as the log keeps days (history_days 1..31), 10 on show at once."""
    def hist(n: int) -> dict:
        return {"spec": "lamptown-history/1", "at": AT,
                "days": [day(d) | {"today": d == "2026-11-04"} for d in days_back(n)]}
    body = """
      const a = S(), ids = () => RS.room.targets().map(t => t.id).filter(i => i.startsWith('tab'));
      const firstIds = ids();
      for (let k = 0; k < 40; k++) key('ArrowUp');
      await until(() => S().drawn.includes('THU') && S().drawn.includes('22'));
      const b = S(), oldIds = ids();
      RS.room.targets().find(t => t.id === 'tabs-newer').onClick();
      return { a, firstIds, b, oldIds, c: S() };
    """
    three = book(tmp_path / "3", hist(3), "return S();")
    assert three["tabs"] == ["MON", "TUE", "WED*"] and three["tab"] == 2
    r = book(tmp_path / "14", hist(14), body)
    a, b = r["a"], r["b"]
    assert len(a["tabs"]) == 14 and a["tab"] == 13 and a["tabWin"] == [4, 10]
    assert r["firstIds"] == ["tabs-older"] + [f"tab-{i}" for i in range(4, 14)]
    assert b["tab"] == 0 and b["tabWin"] == [0, 10] and b["day"]["date"] == "2026-10-22"
    assert r["oldIds"] == [f"tab-{i}" for i in range(10)] + ["tabs-newer"]
    assert r["c"]["tab"] == 10 and r["c"]["tabWin"] == [1, 10]
    assert "22" in b["drawn"]                     # more than a week: each tab says its date too
    month = book(tmp_path / "31", hist(31), "return S();")
    assert len(month["tabs"]) == 31 and month["tabWin"] == [21, 10]


def test_the_demo_keeps_its_sample_week(tmp_path):
    """The day count comes from the log: the demo (no log) still shows its seven sample days."""
    body = ("if (!isPlace('lighthouse')) addPlace('lighthouse', { hit: [330, 190, 30, 30] });"
            "enterRoom('lighthouse', { instant: true, history: false });"
            "for (let w = 0; w < 4000 && !RS.room.seen().tabs; w += 50) await wait(50);"
            "return RS.room.seen();")
    s = run(build_live(tmp_path), body, query="?demo=1&live=0")
    assert s["sample"] is True and len(s["tabs"]) == 7 and s["tabs"][-1].endswith("*")
    assert s["lines"]                                   # the sample day has its agents


# ----------------------------------------------------------------------------- L9-fix: DST
def dst_case(tmp_path: Path, hist: dict, body: str, at: str, zone: str) -> dict:
    from _cdp import probe
    page = build_live(tmp_path)
    text = page.read_text(encoding="utf-8")
    i = text.rindex("</body>")
    case = page.with_name("case-dst.html")
    probe_js = PROBE.replace("%BODY%", PRE.replace("%HIST%", json.dumps(hist)) + body)
    case.write_text(text[:i] + probe_js + text[i:], encoding="utf-8")
    res = json.loads(probe(case.as_uri() + f"?live=0&at={at}", zone, tmp_path))
    assert "probeError" not in res, res["probeError"]
    assert res["pageErrors"] == [], res["pageErrors"]
    return res


DST_BODY = """
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  tab(%TAB%); const s = S(), clocks = {};
  for (const m of %MINUTES%) { RS.room.seek(m); clocks[m] = S().replay.clock; }
  return { tz, s, clocks };
"""


def test_dst_days_read_the_wall_clock_in_a_zone_that_has_them(tmp_path):
    """Finding 10: New York's fall-back day (1 Nov 2026, 25 hours: 01:00-01:59 twice) and its
    spring-forward day (8 Mar 2026, 23 hours: 02:00 is skipped), in a real browser in that zone."""
    if CHROME is None:
        pytest.skip("no headless browser")
    fall = day(DATES[3], [agent("WREN / LATE PASS")],
               [[30, 0, "start"], [90, 0, "files", 2], [150, 0, "files", 3], [1490, 0, "done"]],
               minutes=1500)
    r = dst_case(tmp_path / "fall", history({DATES[3]: fall}),
                 DST_BODY.replace("%TAB%", "3")
                 .replace("%MINUTES%", "[60, 90, 120, 150, 180, 1495]"),
                 AT, "America/New_York")
    assert r["tz"] == "America/New_York"
    assert r["s"]["day"]["minutes"] == 1500
    assert r["s"]["lines"][0]["words"] == "IN 00:30, DONE 23:50"
    assert r["clocks"] == {"60": "01:00", "90": "01:30", "120": "01:00", "150": "01:30",
                           "180": "02:00", "1495": "23:55"}
    spring_dates = [(dt.date(2026, 3, 10) - dt.timedelta(days=k)).isoformat()
                    for k in range(6, -1, -1)]
    spring = day("2026-03-08", [agent("GULL POST / EARLY")],
                 [[100, 0, "start"], [130, 0, "files", 1], [1370, 0, "done"]], minutes=1380)
    hist = {"spec": "lamptown-history/1", "at": "2026-03-10T15:00:00Z",
            "days": [spring if d == "2026-03-08" else day(d) | {"today": d == "2026-03-10"}
                     for d in spring_dates]}
    r2 = dst_case(tmp_path / "spring", hist,
                  DST_BODY.replace("%TAB%", "4").replace("%MINUTES%", "[100, 119, 120, 130, 1370]"),
                  "2026-03-10T15:00:00Z", "America/New_York")
    assert r2["s"]["day"]["date"] == "2026-03-08" and r2["s"]["day"]["minutes"] == 1380
    assert r2["s"]["lines"][0]["words"] == "IN 01:40, DONE 23:50"
    assert r2["clocks"] == {"100": "01:40", "119": "01:59", "120": "03:00", "130": "03:10",
                            "1370": "23:50"}


def test_a_dst_day_in_another_zone_never_reads_past_midnight(tmp_path):
    """Finding 10, the fallback: the browser's zone has no DST change that day (here: Tokyo), so
    the change is taken at 02:00. No "24:50": the last hour of a 25-hour day reads 23:xx."""
    if CHROME is None:
        pytest.skip("no headless browser")
    long_day = day(DATES[3], [agent("WREN / LATE PASS")],
                   [[30, 0, "start"], [1490, 0, "done"]], minutes=1500)
    short_day = day(DATES[1], [agent("GULL POST / EARLY")],
                    [[10, 0, "start"], [1370, 0, "done"]], minutes=1380)
    body = """
      tab(3); RS.room.seek(1495); const a = S();
      tab(1); RS.room.seek(1379); const b = S(); RS.room.seek(130); const b2 = S();
      return { a, b, b2, tz: Intl.DateTimeFormat().resolvedOptions().timeZone };
    """
    r = dst_case(tmp_path, history({DATES[3]: long_day, DATES[1]: short_day}), body, AT,
                 "Asia/Tokyo")
    assert r["tz"] == "Asia/Tokyo"
    assert r["a"]["lines"][0]["words"] == "IN 00:30, DONE 23:50"
    assert r["a"]["replay"]["clock"] == "23:55"
    assert r["b"]["lines"][0]["words"] == "IN 00:10, DONE 23:50"
    assert r["b2"]["replay"]["clock"] == "03:10"
    texts = json.dumps([r["a"]["drawn"], r["b"]["drawn"]])
    assert not re.search(r"\b2[4-9]:\d\d\b", texts)


# ------------------------------------------------------------------------- L9-fix: slip, tab dot
def fails_day(date: str, n: int) -> dict:
    agents = [agent(f"PROJECT {i} / JOB {i}", aid=f"f{i}") for i in range(n)]
    events = [[100 + i, i, "start"] for i in range(n)] + [[200 + i, i, "fail"] for i in range(n)]
    return day(date, agents, sorted(events, key=lambda e: e[0]))


def test_a_slip_with_one_group_uses_its_rows(tmp_path):
    """Finding 11: one group gets the slip's rows (no "+6 MORE" over an empty slip)."""
    nine = book(tmp_path / "9", history({DATES[-1]: fails_day(DATES[-1], 9)}), "return S().slip;")
    assert nine["groups"] == ["FAILED"] and nine["rows"] == [[9, 0]]
    many = book(tmp_path / "30", history({DATES[-1]: fails_day(DATES[-1], 30)}), "return S().slip;")
    assert many["rows"] == [[14, 16]]
    mix = book(tmp_path / "mix", history({DATES[-1]: mixed(DATES[-1])}), "return S().slip;")
    assert mix["rows"] == [[1, 0], [1, 0], [1, 0], [1, 0]]


def test_todays_tab_dot_is_not_the_waits_amber(tmp_path):
    """The today mark on the tabs is a plain light dot (amber means "waits" in the legend)."""
    r = book(tmp_path, history({DATES[-1]: mixed(DATES[-1])}), """
      tab(3); kick(); await wait(300);
      const px = (x, y) => [...document.getElementById('c').getContext('2d')
                              .getImageData(x, y, 1, 1).data].slice(0, 3);
      return { dot: px(381 + 13, 18 + 6 * 16 + 1), amber: K.ST.waiting.lamp };
    """)
    amber = [int(r["amber"][i:i + 2], 16) for i in (1, 3, 5)]
    assert r["dot"] != amber and r["dot"] == [0xE6, 0xED, 0xF7], r


# ----------------------------------------------------------------------------- review screenshots
DEMO = ("if (!isPlace('lighthouse')) addPlace('lighthouse', { hit: [330, 190, 30, 30] }); "
        "enterRoom('lighthouse', { instant: true, history: false }); await wait(9000)")


def test_review_screenshots_are_written_only_on_request(monkeypatch, tmp_path):
    """Finding 9: a plain test run writes no screenshots into the repo."""
    monkeypatch.delenv("LAMPTOWN_SHOTS", raising=False)
    assert shots_dir() is None
    monkeypatch.setenv("LAMPTOWN_SHOTS", str(tmp_path))
    assert shots_dir() == tmp_path


@pytest.mark.skipif(shots_dir() is None, reason="set LAMPTOWN_SHOTS=<folder> to write them")
def test_screenshots_for_review(tmp_path):
    """Writes the review screenshots into $LAMPTOWN_SHOTS (a person looks at them)."""
    out_dir = shots_dir()
    assert out_dir is not None
    page = build_live(tmp_path)
    full = history({DATES[-1]: mixed(DATES[-1]), DATES[4]: busy_day(DATES[4]),
                    DATES[3]: mixed(DATES[3]), DATES[2]: quiet_day(DATES[2])})
    pre = PRE.replace("%HIST%", json.dumps(full))
    cases = [
        ("key('s'); key('Home'); RS.room.seek(560); key('p')", "today-mid-replay", 2500),
        ("", "today-slip", 2500),
        ("tab(3); key('End')", "past-slip", 2500),
        ("tab(4); RS.room.seek(760); key('p')", "busy-day", 3000),
        ("tab(4)", "busy-day-whole", 2500),
        ("tab(0)", "empty-day", 2500),
    ]
    out = [shot(page, pre + body, out_dir / f"{name}.png", QUERY, budget)
           for body, name, budget in cases]
    out.append(shot(page, DEMO, out_dir / "demo-today.png", "?demo=1&live=0", 12000))
    # L9-fix: a carried-over wait, names in other scripts, a past-day text view, a DST day
    kelp = live([live_agent("codex-kelp", "Night backup", "waiting", project="kelp"),
                 live_agent("codex-wren", "Late pass", "waiting", project="wren",
                            since="2026-11-03T10:00:00Z")],
                [{"id": "kelp", "name": "KELP"}, {"id": "wren", "name": "WREN"}])
    carried = (PRE.replace("%HIST%", json.dumps(carried_history()))
               + "const p = " + json.dumps(kelp)
               + "; p.agents[0].since = local(2026, 11, 3, 22, 30);"
               + " lamptownLive(fresh(p)); await wait(2500)")
    out.append(shot(page, carried, out_dir / "today-carried-wait.png", QUERY, 6000))
    out.append(shot(page, carried + "; key('s')", out_dir / "today-carried-book.png", QUERY, 6000))
    names = day(DATES[-1], [agent(f"KELP / {ARABIC}", aid="a1"),
                            agent("CAFÉ / ÜBER STRASSE", "claude-code", aid="a2"),
                            agent("PUFFIN / ЗАПУСК 🚀", aid="a3"), agent(KITE, aid="a4")],
                [[100, 0, "start"], [101, 1, "start"], [102, 2, "start"], [103, 3, "start"],
                 [130, 0, "wait"], [140, 1, "done"]])
    out.append(shot(page, PRE.replace("%HIST%", json.dumps(history({DATES[-1]: names})))
                    + "key('s')", out_dir / "names-any-script.png", QUERY, 4000))
    fall = day(DATES[3], [agent("WREN / LATE PASS")],
               [[30, 0, "start"], [90, 0, "files", 2], [150, 0, "files", 3], [1490, 0, "done"]],
               minutes=1500)
    out.append(shot(page, PRE.replace("%HIST%", json.dumps(history({DATES[3]: fall})))
                    + "tab(3); RS.room.seek(150)", out_dir / "dst-fallback-zone.png", QUERY, 4000))
    details = tmp_path / "details-past.html"
    details.write_text(B.render_details(B.town_data(parse_config("", tmp_path / "x.toml"), {},
                                                    NOW), [], full), encoding="utf-8")
    png = out_dir / "details-past-days.png"
    run_browser(headless(tmp_path / "chrome-details", "--window-size=1000,1800",
                         "--hide-scrollbars", f"--screenshot={png}",
                         details.as_uri() + "#lighthouse-days"), timeout=180)
    out.append(png)
    assert all(p.is_file() for p in out)
    assert html.escape("Lighthouse, the last days") in details.read_text(encoding="utf-8")
