"""Third-party readers found through the ``lamptown.readers`` entry-point group.

An installed package registers ``kind = "module:factory"`` under the group; ``make_reader`` builds
it like a built-in. ``importlib.metadata.entry_points`` is patched so no package is installed.
"""

from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

import pytest

from lamptown.status import readers as R

PLUGIN = '''
class KiteReader:
    name = "kite-reader"

    def __init__(self, options):
        self.options = options

    def poll(self, state, now):
        return None


def make_reader(options):
    return KiteReader(options)


def make_shadow(options):
    reader = KiteReader(options)
    reader.name = "shadow"
    return reader


NOT_A_FACTORY = 42
'''


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    """A module ``kite_plugin_pkg`` on sys.path and a way to register entry points for it."""
    (tmp_path / "kite_plugin_pkg.py").write_text(PLUGIN, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "kite_plugin_pkg", raising=False)
    seen: list[dict] = []

    def register(**points: str) -> None:
        eps = [metadata.EntryPoint(name, value, R.ENTRY_GROUP) for name, value in points.items()]

        def entry_points(**kw):
            seen.append(kw)
            return [ep for ep in eps if kw.get("group") == ep.group]

        monkeypatch.setattr(R.metadata, "entry_points", entry_points)

    register.seen = seen  # type: ignore[attr-defined]
    return register


def test_an_entry_point_reader_is_built_with_its_options(plugin):
    plugin(kite="kite_plugin_pkg:make_reader")
    reader = R.make_reader({"kind": "kite", "depth": 3, "label": "paper"})
    assert reader.name == "kite-reader"
    assert reader.options == {"depth": 3, "label": "paper"}
    assert plugin.seen == [{"group": "lamptown.readers"}]
    assert "kite" in R.entry_point_readers()


def test_a_non_callable_entry_point_is_a_reader_error(plugin):
    plugin(kite="kite_plugin_pkg:NOT_A_FACTORY")
    with pytest.raises(R.ReaderError, match="entry point 'kite' is not callable"):
        R.make_reader({"kind": "kite"})


def test_a_raising_entry_points_call_is_an_unknown_kind(monkeypatch):
    def broken(**kw):
        raise RuntimeError("a broken installed distribution")

    monkeypatch.setattr(R.metadata, "entry_points", broken)
    assert R.entry_point_readers() == {}
    with pytest.raises(R.ReaderError, match="unknown reader kind 'kite'"):
        R.make_reader({"kind": "kite"})


def test_an_unregistered_kind_is_unknown(plugin):
    plugin(other="kite_plugin_pkg:make_reader")
    with pytest.raises(R.ReaderError, match=r"unknown reader kind 'kite'.*lamptown\.readers"):
        R.find_reader("kite")


def test_a_built_in_kind_wins_over_a_same_name_entry_point(plugin):
    plugin(git="kite_plugin_pkg:make_shadow")
    factory = R.find_reader("git")
    assert factory.__module__ == "lamptown.status.readers.git"
    reader = R.make_reader({"kind": "git"})
    assert reader.name != "shadow"
    assert plugin.seen == []  # the entry points are not even looked at for a built-in kind


def test_a_local_reader_file_wins_over_an_entry_point(plugin, tmp_path):
    plugin(kite="kite_plugin_pkg:make_shadow")
    local = tmp_path / "local_kite.py"
    local.write_text(PLUGIN, encoding="utf-8")
    reader = R.make_reader({"kind": "kite", "reader": str(Path(local))})
    assert reader.name == "kite-reader"
