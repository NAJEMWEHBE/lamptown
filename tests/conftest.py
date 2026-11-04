"""Shared test setup: the ``browser`` and ``app`` markers.

* ``browser``: needs a headless Chrome/Chromium/Edge (see ``_browser.py``). Skipped with the
  reason when none is found, or failed when ``LAMPTOWN_REQUIRE_BROWSER=1``.
* ``app``: the desktop app's tests (every ``test_app_*.py``), marked here by file name.

CI splits on them: ``-m "not browser and not app"`` on every OS and Python, ``-m browser`` and
``-m app`` on Python 3.13 (see ``.github/workflows/ci.yml``).
"""

from __future__ import annotations

import pytest

from _browser import CHROME, REQUIRED, WHY


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "browser: needs a headless Chrome, Chromium or Edge")
    config.addinivalue_line("markers", "app: a desktop app test (tests/test_app_*.py)")


@pytest.hookimpl(tryfirst=True)  # before -m deselects
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.name.startswith("test_app_"):
            item.add_marker(pytest.mark.app)


def pytest_runtest_setup(item: pytest.Item) -> None:
    if CHROME is None and item.get_closest_marker("browser") is not None:
        if REQUIRED:
            pytest.fail(f"LAMPTOWN_REQUIRE_BROWSER=1 but {WHY}", pytrace=False)
        pytest.skip(WHY)
