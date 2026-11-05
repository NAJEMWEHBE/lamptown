"""The README pictures in docs/media are what tools/make_media.py makes from today's code.

``town.png`` and ``live-town.png`` are rendered again into a temp folder (``--only``, so the rest
of the set and the GIF are skipped) and compared with docs/media pixel for pixel. A change to the
town that moves a pixel then fails here until the media are made again.

Chrome's canvas may draw a glow or a blend a shade apart on another OS, so the comparison runs
only on the OS that made the set: docs/media/made-on.txt, written by a full make_media run. The
media in the repo were made on Windows (made-on.txt says which); the "Regenerate media" workflow
makes them on Linux and writes "linux". CI's browser job runs on all three, so the one that
matches checks them. Needs Pillow (make_media's own dependency; CI's browser job installs it).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

import _browser
from test_media import rgb

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "docs" / "media"
STILLS = ("town", "live-town")


def this_os() -> str:
    """The OS family, as tools/make_media.py writes it to made-on.txt."""
    if sys.platform == "win32":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def test_made_on_names_an_os():
    assert (MEDIA / "made-on.txt").read_text(encoding="utf-8").strip() in (
        "windows", "macos", "linux")


@_browser.browser
def test_the_readme_pictures_match_a_fresh_render(tmp_path):
    made_on = (MEDIA / "made-on.txt").read_text(encoding="utf-8").strip()
    if made_on != this_os():
        pytest.skip(f"docs/media were made on {made_on}; their pixels are checked only there")
    if importlib.util.find_spec("PIL") is None:
        if _browser.REQUIRED:
            pytest.fail("LAMPTOWN_REQUIRE_BROWSER=1 but Pillow (tools/make_media.py) is missing",
                        pytrace=False)
        pytest.skip("tools/make_media.py needs Pillow: pip install pillow")
    env = {**os.environ, "CHROME": str(_browser.CHROME)}
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "make_media.py"), "--out",
                        str(tmp_path), "--only", *STILLS], env=env, capture_output=True,
                       text=True, timeout=900, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    stale = []
    for name in STILLS:
        fresh, kept = rgb(tmp_path / f"{name}.png"), rgb(MEDIA / f"{name}.png")
        if fresh[:2] != kept[:2]:
            stale.append(f"{name}.png is {kept[0]} x {kept[1]}, a fresh render {fresh[0]} x "
                         f"{fresh[1]}")
            continue
        off = sum(a[i:i + 3] != b[i:i + 3] for a, b in zip(fresh[2], kept[2], strict=True)
                  for i in range(0, len(a), 3))
        if off:
            stale.append(f"{name}.png: {off} pixels differ from a fresh render")
    assert not stale, ("docs/media is out of date (" + "; ".join(stale)
                       + "): run tools/make_media.py")
