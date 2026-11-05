"""PR #1 review: two ``[[live.reader]]`` entries of one kind each keep their own saved state.

Every tree here is invented under ``tmp_path``; the autouse guard fails a test that touches
the real ``~/.codex``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from _reader_guard import guard
from lamptown import livetown
from lamptown.config import parse_config
from lamptown.livefeed import LiveFeed
from lamptown.status import events as ev
from lamptown.status.readers import Batch
from lamptown.status.readers.codex import CodexReader
from lamptown.status.watch import STATE_DIR, STATE_FILE, Watcher, _state_keys

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=1)


@pytest.fixture(autouse=True)
def _no_real_codex(monkeypatch, tmp_path):
    guard(monkeypatch, tmp_path)


def _sid(n: int) -> str:
    clock = f"{int(T0.timestamp() * 1000) + n:012x}"
    rand = hashlib.sha256(f"pr1 session {n}".encode()).hexdigest()
    return f"{clock[:8]}-{clock[8:]}-7{rand[:3]}-8{rand[3:6]}-{rand[6:18]}"


def _ts(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _session(root, n: int, cwd: str, start: datetime) -> None:
    """One invented rollout file: a turn started, done, and started again (so it is live)."""
    local = start.astimezone().date()
    folder = root / "sessions" / f"{local:%Y}" / f"{local:%m}" / f"{local:%d}"
    folder.mkdir(parents=True, exist_ok=True)
    sid = _sid(n)
    path = folder / f"rollout-{start:%Y-%m-%dT%H-%M-%S}-{sid}.jsonl"
    meta = {"id": sid, "session_id": sid, "cwd": cwd, "timestamp": _ts(start),
            "source": "exec", "originator": "codex_exec", "cli_version": "0.0.0"}
    lines = [{"timestamp": _ts(start), "type": "session_meta", "payload": meta}]
    for i, kind in enumerate(("task_started", "task_complete", "task_started"), 1):
        lines.append({"timestamp": _ts(start + timedelta(seconds=i)), "type": "event_msg",
                      "payload": {"type": kind, "turn_id": "t"}})
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


def _starts(status) -> list[str]:
    """Every logged start (each session has two turns: two starts), checked never repeated."""
    logged = [e for _, es in ev.read_days(status, 2, now=NOW, tz=UTC) for e in es
              if e["k"] == "start"]
    keys = [ev.event_key(e) for e in logged]
    assert len(keys) == len(set(keys)), logged
    return [e["id"] for e in logged]


def _two_roots(tmp_path):
    a, b = tmp_path / "codex-a", tmp_path / "codex-b"
    for root in (a, b):
        root.mkdir()
    _session(a, 1, "/work/paper-kite", NOW - timedelta(seconds=60))
    _session(b, 2, "/work/harbour", NOW - timedelta(seconds=60))
    return a, b


def _readers(a, b, tags=("a", "b")):
    ra, rb = CodexReader(root=a), CodexReader(root=b)
    ra.state_tag, rb.state_tag = tags
    return [ra, rb]


def test_two_codex_roots_keep_apart_states_and_log_each_start_once(tmp_path):
    a, b = _two_roots(tmp_path)
    status = tmp_path / "status"
    with Watcher(status, _readers(a, b), tz=UTC) as w:
        for i in range(6):  # no new input after the first tick
            res = w.tick(NOW + timedelta(seconds=5 * i))
            assert not res.errors
    starts = _starts(status)
    assert sorted(set(starts)) == sorted(starts[:2]) and len(starts) == 4, starts  # 2 each
    with Watcher(status, _readers(a, b), tz=UTC) as w:  # a restart: still no replay
        for i in range(3):
            assert not w.tick(NOW + timedelta(seconds=40 + 5 * i)).errors
    assert _starts(status) == starts
    text = (status / STATE_DIR / STATE_FILE).read_text(encoding="utf-8")
    saved = json.loads(text)["readers"]
    assert len(saved) == 2 and all(k.startswith("codex~") for k in saved)
    for root in (a, b):  # cursors.json still names no path
        assert str(root) not in text and root.name not in text
        assert json.dumps(str(root))[1:-1] not in text


def test_reordered_entries_keep_their_own_state(tmp_path):
    a, b = _two_roots(tmp_path)
    status = tmp_path / "status"
    with Watcher(status, _readers(a, b), tz=UTC) as w:
        w.tick(NOW)
        w.tick(NOW + timedelta(seconds=5))
    before = _starts(status)
    with Watcher(status, _readers(b, a, tags=("b", "a")), tz=UTC) as w:  # entries swapped
        for i in range(3):
            assert not w.tick(NOW + timedelta(seconds=10 + 5 * i)).errors
    assert _starts(status) == before and len(before) == 4


def test_a_single_readers_old_state_still_loads_and_a_second_entry_adopts_none_of_it(tmp_path):
    a, b = _two_roots(tmp_path)
    status = tmp_path / "status"
    with Watcher(status, [CodexReader(root=a)], tz=UTC) as w:  # one entry: the bare name
        w.tick(NOW)
        w.tick(NOW + timedelta(seconds=5))
    saved = json.loads((status / STATE_DIR / STATE_FILE).read_text(encoding="utf-8"))
    assert list(saved["readers"]) == ["codex"]
    first = _starts(status)
    assert len(first) == 2
    with Watcher(status, [CodexReader(root=a)], tz=UTC) as w:  # the upgrade, one entry
        assert not w.tick(NOW + timedelta(seconds=10)).errors
    assert _starts(status) == first  # no re-read storm
    with Watcher(status, _readers(a, b), tz=UTC) as w:  # a second root added
        for i in range(3):
            assert not w.tick(NOW + timedelta(seconds=15 + 5 * i)).errors
    starts = _starts(status)
    assert starts.count(first[0]) == 2 and len(starts) == 4, starts  # a's not logged again
    saved = json.loads((status / STATE_DIR / STATE_FILE).read_text(encoding="utf-8"))
    assert "codex" not in saved["readers"] and len(saved["readers"]) == 2


def test_a_twin_that_fails_to_start_leaves_the_others_state_alone(tmp_path):
    a, b = _two_roots(tmp_path)
    status = tmp_path / "status"
    with Watcher(status, _readers(a, b), tz=UTC) as w:
        w.tick(NOW)
        w.tick(NOW + timedelta(seconds=5))
    before = _starts(status)
    rb = CodexReader(root=b)
    rb.state_tag = "b"
    with Watcher(status, [rb], tz=UTC) as w:  # a's entry broke: b runs alone, still tagged
        for i in range(3):
            assert not w.tick(NOW + timedelta(seconds=10 + 5 * i)).errors
    assert _starts(status) == before


class _Fake:
    def __init__(self, name, tag=None):
        self.name = name
        if tag is not None:
            self.state_tag = tag

    def poll(self, state, now):
        return Batch()


def test_state_keys_unique_names_stay_and_shared_ones_never_collide():
    rs = [_Fake("git"), _Fake("codex", "x"), _Fake("codex", "x"), _Fake("codex")]
    keys, adopt = _state_keys(rs)
    assert keys[id(rs[0])] == "git" and id(rs[0]) not in adopt
    shared = [keys[id(r)] for r in rs[1:]]
    assert len(set(shared)) == 3 and all(k.startswith("codex~") for k in shared)
    assert adopt == {id(rs[1])}
    lone = _Fake("codex", "x")  # tagged though alone (its twin didn't start): still hashed
    keys, adopt = _state_keys([lone])
    assert keys[id(lone)] == shared[0] and adopt == {id(lone)}


def test_the_live_town_tags_each_entry_and_the_tag_names_no_saved_path(tmp_path):
    cfg = parse_config('[live]\nstatus_dir = "/work/status"\n'
                       '[[live.reader]]\nkind = "codex"\nroot = "/work/codex-a"\n'
                       '[[live.reader]]\nkind = "codex"\nroot = "/work/codex-b"\n')
    made: list = []
    feed = LiveFeed(make_watcher=lambda readers, **opts: made.append(readers) or None)
    feed._new_watcher(cfg)
    ra, rb = made[0]
    assert ra.state_tag != rb.state_tag
    keys, _ = _state_keys([ra, rb])
    assert keys[id(ra)] != keys[id(rb)]
    assert all("work" not in k for k in keys.values())
    one = parse_config('[live]\nstatus_dir = "/work/status"\n'
                       '[[live.reader]]\nkind = "codex"\nroot = "/work/codex-a"\n'
                       '[[live.reader]]\nkind = "git"\nrepos = ["/work/a"]\n')
    feed._new_watcher(one)
    assert not any(hasattr(r, "state_tag") for r in made[1])  # one of a kind: the bare name
    keys, _ = _state_keys(made[1])
    assert sorted(keys.values()) == ["codex", "git"]


def test_only_the_first_git_entry_watches_the_live_status_folder():
    cfg = parse_config('[live]\nstatus_dir = "/work/status"\n'
                       '[[live.reader]]\nkind = "git"\nrepos = ["/work/a"]\n'
                       '[[live.reader]]\nkind = "git"\nrepos = ["/work/b"]\n'
                       '[[live.reader]]\nkind = "ci_gh"\n'
                       '[[live.reader]]\nkind = "ci_gh"\nstatus_dir = "/work/other"\n')
    first, second, ci, ci2 = (livetown.reader_entry(cfg, e) for e in cfg.live.readers)
    assert first["status_dir"] == str(livetown.live_status_dir(cfg))
    assert "status_dir" not in second  # its own repos only: the agents' commits count once
    assert ci["status_dir"] == str(livetown.live_status_dir(cfg))
    assert ci2["status_dir"] != ci["status_dir"]  # one given: that one
    # an entry not from the config (a test, a caller) still gets the default
    assert livetown.reader_entry(cfg, {"kind": "git"})["status_dir"] == first["status_dir"]
