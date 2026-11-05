"""The shared title privacy list: every case in ``tests/data/title_privacy.txt``.

A title the town shows (an agent's or a thread's) goes through ``name_only`` first. The data file
holds what it must lose (``[cut]``: the title and exactly what is shown) and what it must keep
(``[keep]``: titles that come out unchanged). Every privacy review adds its cases there; the
file's header says how. A failure names the data file's line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lamptown.status.events import name_only

DATA = Path(__file__).resolve().parent / "data" / "title_privacy.txt"
ARROW = " -> "
PLACEHOLDERS = {"{AT}": "@", "{BS}": "\\", "{SL}": "/"}  # see the data file's header
MAX = 200


def _fill(text: str) -> str:
    for key, value in PLACEHOLDERS.items():
        text = text.replace(key, value)
    return text


def load(path: Path = DATA) -> dict[str, list]:
    """``{"cut": [param(title, shown)], "keep": [param(title)]}``, each with its line as id."""
    cases: dict[str, list] = {"cut": [], "keep": []}
    seen: dict[str, int] = {}
    section = None
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        if line in ("[cut]", "[keep]"):
            section = line[1:-1]
            continue
        assert section, f"{path.name}:{n}: a case before the first [cut] or [keep]"
        assert line == line.strip(), f"{path.name}:{n}: a space at the start or end of the line"
        if section == "cut":
            assert line.count(ARROW) == 1, f'{path.name}:{n}: a cut case needs one " -> "'
            title, shown = (_fill(part) for part in line.split(ARROW))
            case = pytest.param(title, shown, id=f"line{n}")
        else:
            assert ARROW not in line, f"{path.name}:{n}: a keep case has no \" -> \""
            title = _fill(line)
            case = pytest.param(title, id=f"line{n}")
        assert title not in seen, f"{path.name}:{n}: the same title as line {seen[title]}"
        seen[title] = n
        cases[section].append(case)
    return cases


CASES = load()


def test_the_list_has_both_sections():
    assert len(CASES["cut"]) >= 150 and len(CASES["keep"]) >= 50


@pytest.mark.parametrize(("title", "shown"), CASES["cut"])
def test_a_title_loses_what_it_must(title, shown):
    assert len(title) <= MAX  # so the cut is name_only's own, not the length cap
    assert name_only(title, MAX) == shown


@pytest.mark.parametrize("title", CASES["keep"])
def test_a_title_keeps_what_it_may(title):
    assert name_only(title, MAX) == title


def test_the_data_file_keeps_the_privacy_gate_quiet():
    """Written with the placeholders, the planted paths and e-mails never trip the gate, even as
    a new blob (no baseline): every edit of the file makes one."""
    import importlib.util
    import sys

    tool = Path(__file__).resolve().parents[1] / "tools" / "privacy_gate.py"
    spec = importlib.util.spec_from_file_location("lamptown_privacy_gate_t", tool)
    assert spec and spec.loader
    pg = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = pg
    try:
        spec.loader.exec_module(pg)
        scanner = pg.Scanner(None)
        scanner.scan_text(DATA.read_text(encoding="utf-8"), DATA.name)
    finally:
        sys.modules.pop(spec.name, None)
    assert [(h.line, h.check) for h in scanner.hits] == []
