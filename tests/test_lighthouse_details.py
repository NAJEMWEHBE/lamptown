"""L9-fix: the details page reads the handover book's past days in words (the book's text view):
newest first, one line per agent with its tool and state at close. Names only. Made-up names."""

from __future__ import annotations

from datetime import UTC, timedelta

from lamptown import build as B
from lamptown.status.store import update_status
from test_livetown import NOW, config


def _day(date: str, agents: list, events: list, today: bool = False) -> dict:
    return {"date": date, "today": today, "minutes": 1440, "agents": agents, "events": events}


def _agent(name: str, tool: str = "codex") -> dict:
    return {"id": name.lower().replace(" ", "-"), "name": name, "tool": tool, "plot": "workshop"}


HISTORY = {"spec": "lamptown-history/1", "at": "2026-11-04T17:48:00Z", "days": [
    _day("2026-11-02", [_agent("KITE"), _agent("MOSS ENGINE", "claude-code"), _agent("<B>")],
         [[540, 0, "start"], [600, 0, "push"], [610, 0, "ci", 0], [620, 0, "done"],
          [550, 1, "start"], [700, 1, "wait"], [560, 2, "start"], [800, 2, "fail"]]),
    _day("2026-11-03", [], []),
    _day("2026-11-04", [_agent("WREN")], [[60, 0, "start"]], today=True),
]}


def test_past_days_are_listed_newest_first_with_the_state_at_close():
    html = B._lighthouse_days_section(HISTORY)
    assert 'id="lighthouse-days"' in html and "Lighthouse, the last days" in html
    tue, mon = html.index("<h3>Tuesday 3 November</h3>"), html.index("<h3>Monday 2 November</h3>")
    assert tue < mon                                     # newest first
    assert "No agents." in html[tue:mon]
    assert "Kite" in html and "done, CI red" in html     # the CI word stays with a done line
    assert "Moss Engine" in html and "(claude-code)" in html and "waiting for you at close" in html
    assert "failed" in html
    assert "&lt;B&gt;" in html and "<B>" not in html     # names are escaped
    assert "Wren" not in html                            # today has its own section


def test_no_past_days_no_section():
    assert B._lighthouse_days_section(None) == ""
    assert B._lighthouse_days_section({"days": [HISTORY["days"][2]]}) == ""


def test_render_details_places_the_past_days_after_today():
    data = B.town_data(config_only(), {}, NOW)
    page = B.render_details(data, [], HISTORY)
    assert "Lighthouse, the last days" in page
    assert "Lighthouse, the last days" not in B.render_details(data, [], None)


def config_only():
    from lamptown.config import parse_config
    return parse_config("")


def test_a_live_build_writes_the_past_days_and_never_message_text(tmp_path):
    cfg, work = config(tmp_path)
    update_status("codex-3f9a", work / "status", now=NOW - timedelta(days=1, hours=2),
                  title="Fix save slots", state="waiting", agent="codex",
                  summary="a private note that must not show",
                  **{"x-folder": str(work / "skills" / "k")})
    res = B.build(cfg, out_dir=tmp_path / "out", now=NOW, tz=UTC)
    details = res.details.read_text(encoding="utf-8")
    assert 'id="lighthouse-days"' in details
    days = details[details.index('id="lighthouse-days"'):]
    # the build's own book (history_days = 7): six past days, newest first, empty here
    heads = [days.index(f"<h3>{d}</h3>") for d in ("Tuesday 3 November", "Thursday 29 October")]
    assert heads == sorted(heads) and days.count("<h3>") == 6
    assert "private note" not in details and str(work) not in days


def test_without_live_no_past_days(tmp_path):
    res = B.build(config_only(), out_dir=tmp_path / "out", now=NOW)
    assert "lighthouse-days" not in res.details.read_text(encoding="utf-8")
