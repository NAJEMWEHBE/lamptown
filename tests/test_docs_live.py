"""What the docs say about the live town (0.2) matches the code (review L12: M3, m1, m2)."""

from __future__ import annotations

import re
from pathlib import Path

from _browser import browser
from lamptown.build import DATA_JS, HISTORY_JS, LIVE_JS
from lamptown.status.events import EVENTS_DIR
from lamptown.status.watch import LOCK_FILE, STATE_DIR, STATE_FILE
from test_town import build_town, run

ROOT = Path(__file__).resolve().parents[1]


def text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_security_names_every_file_the_live_town_writes():
    # M3: `lamptown watch` and the app write into the output folder and the status folder
    table = text("SECURITY.md").split("## What Lamptown writes", 1)[1].split("\n## ", 1)[0]
    for bit in (DATA_JS, HISTORY_JS, LIVE_JS, f"{EVENTS_DIR}/YYYY-MM-DD.jsonl",
                f"{STATE_DIR}/{STATE_FILE}", f"{STATE_DIR}/{LOCK_FILE}", "`lamptown watch`",
                "`open --watch`", "history_days"):
        assert bit in table, bit
    demo = next(row for row in table.splitlines() if row.startswith("| `lamptown demo`"))
    assert "world/agents/" in demo and "Ctrl+C" in demo


def test_contributing_checks_run_to_the_end():
    # m2: `lamptown demo` stays live until Ctrl+C, so a check list must not run it bare
    block = text("CONTRIBUTING.md").split("## Run the checks", 1)[1].split("```", 2)[1]
    demos = [line for line in block.splitlines() if line.startswith("lamptown demo")]
    assert demos and all(re.search(r"--no-live|--no-open", line) for line in demos), demos


@browser
def test_the_apps_corner_says_what_live_md_says(tmp_path):
    # m1: in the app the corner says LIVE while a fresh payload feeds it, "LIVE - UPDATED <time>"
    # only without one
    r = run(build_town(tmp_path), """
      window.pywebview = {};   // the desktop app's page
      const off = cornerBuilt();
      lamptownLive({ spec: 'lamptown-live/1', at: new Date().toISOString(), agents: [] });
      return { off, on: cornerBuilt() };""", query="?live=0")
    assert r["on"] == "LIVE"
    assert r["off"].startswith("LIVE - UPDATED ") and r["off"] != "LIVE - UPDATED "
    app = text("docs/live.md").split("## The desktop app", 1)[1].split("\n## ", 1)[0]
    flat = " ".join(app.split())
    assert "corner says **LIVE** while its Watcher feeds the page" in flat
    assert 'without `[live]` (or when the Watcher has stalled) it says "LIVE - UPDATED"' in flat


# --- W2-left-fix: the git reader's status_dir default (review W2-left #4) ---
def test_the_git_readers_status_dir_default_is_written_down():
    # the live town now hands a git reader its own status folder unless the entry says otherwise
    row = next(line for line in text("docs/config.md").splitlines() if line.startswith("| `git` |"))
    assert "default: the live town's own `status_dir`" in row and "`false`" in row, row
    head = " ".join(text("src/lamptown/status/readers/git.py").split('"""', 2)[1].split())
    example = head.split("status_dir =", 1)[1].split("days =", 1)[0]
    assert "default in the live town" in example and "false" in example, example
# --- end W2-left-fix ---
