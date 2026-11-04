"""Incremental tailing: partial lines, replaced and truncated files, the byte budget."""

from __future__ import annotations

import json
import os

import pytest

from lamptown.status.readers import tail as tail_mod
from lamptown.status.readers.tail import Budget, cursor_key, forget, tail, tail_json


def _lines(n: int, width: int = 100, start: int = 0) -> bytes:
    out = []
    for i in range(start, start + n):
        doc = json.dumps({"i": i, "pad": ""})
        out.append(doc[:-2] + "x" * (width - len(doc) - 1) + '"}')
    return ("\n".join(out) + "\n").encode()


def _write(path, data: bytes, mode: str = "ab") -> None:
    with open(path, mode) as fh:
        fh.write(data)


def test_reads_new_lines_only(tmp_path):
    log = tmp_path / "session.jsonl"
    _write(log, b'{"a":1}\n{"a":2}\n')
    cursors: dict = {}
    assert [d for d, _ in tail_json(log, cursors, Budget())[0]] == [{"a": 1}, {"a": 2}]
    _write(log, b'{"a":3}\n')
    assert [d for d, _ in tail_json(log, cursors, Budget())[0]] == [{"a": 3}]
    entry = cursors[cursor_key(log)]
    assert entry["offset"] == entry["size"] == log.stat().st_size
    assert set(entry) >= {"size", "mtime_ns", "offset", "head", "hlen"}


def test_unchanged_file_is_only_stat_ed(tmp_path, monkeypatch):
    log = tmp_path / "session.jsonl"
    _write(log, b'{"a":1}\n')
    cursors: dict = {}
    tail(log, cursors, Budget())

    def no_open(*a, **k):
        raise AssertionError("opened an unchanged file")

    monkeypatch.setattr(tail_mod, "open", no_open, raising=False)
    result = tail(log, cursors, Budget())
    assert result.lines == [] and not result.more and result.reset is None


def test_partial_line_waits_for_its_newline(tmp_path):
    log = tmp_path / "session.jsonl"
    _write(log, b'{"a":1}\n{"b":')
    cursors: dict = {}
    first = tail(log, cursors, Budget())
    assert [line.json() for line in first.lines] == [{"a": 1}]
    assert cursors[cursor_key(log)]["offset"] == len(b'{"a":1}\n')
    assert tail(log, cursors, Budget()).lines == []  # still no newline
    _write(log, b'2}\n')
    (line,) = tail(log, cursors, Budget()).lines
    assert line.json() == {"b": 2} and line.offset == len(b'{"a":1}\n')


def test_crlf_and_broken_lines(tmp_path):
    log = tmp_path / "session.jsonl"
    _write(log, b'{"a":1}\r\nnot json\n\xff\xfe\n')
    pairs, _ = tail_json(log, {}, Budget())
    assert [d for d, _ in pairs] == [{"a": 1}, None, None]
    assert pairs[2][1].text() == "��"


def test_replaced_file_starts_again(tmp_path):
    log = tmp_path / "session.jsonl"
    _write(log, b'{"old":1}\n{"old":2}\n')
    cursors: dict = {}
    tail(log, cursors, Budget())
    fresh = tmp_path / "fresh.tmp"
    _write(fresh, b'{"new":1}\n{"new":2}\n{"new":3}\n')
    os.replace(fresh, log)
    result = tail(log, cursors, Budget())
    assert result.reset == "replaced"
    assert [line.json() for line in result.lines] == [{"new": 1}, {"new": 2}, {"new": 3}]


def test_replaced_file_with_the_same_head_is_caught_by_its_file_id(tmp_path):
    log = tmp_path / "session.jsonl"
    _write(log, b'{"a":1}\n')
    if not os.stat(log).st_ino:
        pytest.skip("this file system gives no file ids")
    cursors: dict = {}
    tail(log, cursors, Budget())
    fresh = tmp_path / "fresh.tmp"
    _write(fresh, b'{"a":1}\n{"a":2}\n')
    os.replace(fresh, log)
    result = tail(log, cursors, Budget())
    assert result.reset == "replaced"
    assert [line.json() for line in result.lines] == [{"a": 1}, {"a": 2}]


def test_truncated_file_starts_again(tmp_path):
    log = tmp_path / "session.jsonl"
    _write(log, _lines(5))
    cursors: dict = {}
    assert len(tail(log, cursors, Budget()).lines) == 5
    _write(log, _lines(2, start=50), mode="r+b")  # same head? no: rewritten from the start
    with open(log, "r+b") as fh:
        fh.truncate(len(_lines(2, start=50)))
    result = tail(log, cursors, Budget())
    assert result.reset in ("truncated", "replaced")
    assert [line.json()["i"] for line in result.lines] == [50, 51]


def test_shrunk_file_with_the_same_head_is_truncated(tmp_path):
    log = tmp_path / "session.jsonl"
    data = _lines(6)
    _write(log, data)
    cursors: dict = {}
    tail(log, cursors, Budget())
    with open(log, "r+b") as fh:  # same file, same first bytes, shorter
        fh.truncate(len(_lines(3)))
    result = tail(log, cursors, Budget())
    assert result.reset == "truncated"
    assert [line.json()["i"] for line in result.lines] == [0, 1, 2]


def test_per_file_budget_spreads_a_big_file_over_ticks(tmp_path):
    log = tmp_path / "big.jsonl"
    _write(log, _lines(10_000, width=100))  # about 1 MB
    cursors: dict = {}
    seen: list[int] = []
    ticks = 0
    while True:
        ticks += 1
        result = tail(log, cursors, Budget(total=2 * 1024 * 1024, per_file=512 * 1024))
        seen.extend(line.json()["i"] for line in result.lines)
        if not result.more:
            break
        assert ticks < 10
    assert ticks == 2
    assert seen == list(range(10_000))


def test_total_budget_is_shared_by_files(tmp_path):
    files = []
    for name in ("a", "b", "c"):
        path = tmp_path / f"{name}.jsonl"
        _write(path, _lines(3000, width=100))  # about 300 KB each
        files.append(path)
    cursors: dict = {}
    budget = Budget(total=400 * 1024, per_file=512 * 1024)
    results = [tail(p, cursors, budget) for p in files]
    assert not results[0].more
    assert results[1].more and results[1].lines  # got the rest of the budget
    assert results[2].more and not results[2].lines  # nothing left this tick
    assert budget.left == 0
    later = tail(files[2], cursors, Budget())
    assert [line.json()["i"] for line in later.lines] == list(range(3000))


def test_big_lines_are_not_parsed(tmp_path):
    log = tmp_path / "session.jsonl"
    huge = json.dumps({"type": "assistant", "file_path": "x", "text": "y" * 300_000}).encode()
    _write(log, huge + b'\n{"after":1}\n')
    result = tail(log, {}, Budget(per_file=1024 * 1024))
    big, after = result.lines
    assert big.big and len(big.data) == tail_mod.MAX_LINE
    assert big.json() is None and b'"type": "assistant"' in big.data  # a regex can look
    assert after.json() == {"after": 1}


def test_a_big_line_still_being_written_is_skipped_to_its_end(tmp_path):
    log = tmp_path / "session.jsonl"
    _write(log, b'{"before":1}\n' + b"z" * 300_000)
    cursors: dict = {}
    first = tail(log, cursors, Budget())
    assert [line.big for line in first.lines] == [False, True]
    assert cursors[cursor_key(log)]["skip"] is True
    _write(log, b"z" * 1000)
    assert tail(log, cursors, Budget()).lines == []
    _write(log, b'zzz\n{"after":2}\n')
    (line,) = tail(log, cursors, Budget()).lines
    assert line.json() == {"after": 2}
    assert "skip" not in cursors[cursor_key(log)]


def test_from_end_reads_only_new_lines(tmp_path):
    log = tmp_path / "session.jsonl"
    _write(log, b'{"old":1}\n{"half":')
    cursors: dict = {}
    assert tail(log, cursors, Budget(), from_end=True).lines == []
    _write(log, b'1}\n{"new":1}\n')
    assert [line.json() for line in tail(log, cursors, Budget()).lines] == [{"new": 1}]


def test_missing_file_drops_its_cursor(tmp_path):
    cursors = {cursor_key(tmp_path / "gone.jsonl"): {"offset": 4}}
    result = tail(tmp_path / "gone.jsonl", cursors, Budget())
    assert result.missing and cursors == {}


def test_forget_drops_cursors_not_kept(tmp_path):
    cursors = {cursor_key(p): {} for p in ("a", "b", "c")}
    assert forget(cursors, ["a", "c"]) == [cursor_key("b")]
    assert set(cursors) == {cursor_key("a"), cursor_key("c")}


# ---------------------------------------------------------------- review fixes (L1)
def test_cursor_keys_hold_no_path(tmp_path):
    log = tmp_path / "C--Users-you-secret-app" / "abc.jsonl"
    log.parent.mkdir()
    _write(log, b'{"a":1}\n')
    cursors: dict = {}
    tail(log, cursors, Budget())
    saved = json.dumps(cursors)
    assert "secret-app" not in saved and str(tmp_path.name) not in saved
    assert list(cursors) == [cursor_key(log)] and cursor_key(log) == cursor_key(str(log))


def test_a_file_busy_for_a_moment_keeps_its_cursor(tmp_path, monkeypatch):
    log = tmp_path / "session.jsonl"
    _write(log, b'{"a":1}\n')
    cursors: dict = {}
    tail(log, cursors, Budget())
    _write(log, b'{"a":2}\n')
    real = os.stat

    def denied(path, *a, **k):  # Windows: the file is being replaced right now
        if str(path) == str(log):
            raise PermissionError(13, "Access is denied")
        return real(path, *a, **k)

    monkeypatch.setattr(tail_mod.os, "stat", denied)
    result = tail(log, cursors, Budget())
    assert result.busy and not result.missing and cursor_key(log) in cursors
    monkeypatch.setattr(tail_mod.os, "stat", real)
    assert [line.json() for line in tail(log, cursors, Budget()).lines] == [{"a": 2}]


def test_a_line_longer_than_the_per_file_budget_still_moves_on(tmp_path):
    log = tmp_path / "session.jsonl"
    _write(log, b'{"a":"' + b"x" * 100_000 + b'"}\n{"b":1}\n')
    cursors: dict = {}
    got = []
    for _ in range(4):
        got += tail(log, cursors, Budget(per_file=64 * 1024)).lines
    assert [line.big for line in got] == [True, False]
    assert got[1].json() == {"b": 1}
    assert cursors[cursor_key(log)]["offset"] == log.stat().st_size


def test_a_writer_holding_the_file_open_is_not_blocked(tmp_path):
    log = tmp_path / "session.jsonl"
    cursors: dict = {}
    with open(log, "ab", buffering=0) as writer:
        writer.write(b'{"a":1}\n')
        assert [line.json() for line in tail(log, cursors, Budget()).lines] == [{"a": 1}]
        writer.write(b'{"a":2}\n')  # still writable while and after we read
        assert [line.json() for line in tail(log, cursors, Budget()).lines] == [{"a": 2}]
    os.replace(log, tmp_path / "moved.jsonl")  # and we hold no handle afterwards
