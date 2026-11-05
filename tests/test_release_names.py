"""The details page's "Lighthouse today" spells live and finished agents the same way."""

from lamptown.livetown import lighthouse_today


def test_live_and_finished_agents_share_one_spelling():
    live = {"agents": [{"id": "a1", "title": "Otter", "project": "moss-engine",
                        "tool": "claude", "plot": "workshop", "state": "working"}]}
    history = {"days": [{"today": True,
                         "agents": [{"id": "b2", "name": "TIDEBOOK / MARTEN",
                                     "tool": "codex", "plot": "library"}],
                         "events": [[0, 0, "done"]]}]}
    names = [r["name"] for r in lighthouse_today(live, history)]
    assert names == ["Moss Engine / Otter", "Tidebook / Marten"]
