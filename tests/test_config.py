"""lamptown.toml: lookup order, path resolution and clear errors."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from lamptown import config as C
from lamptown.config import ConfigError, find_config, load_config, parse_config


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def no_user_config(tmp_path, monkeypatch):
    """Point every per-user folder at an empty temp dir, so the real user config never leaks in."""
    home = tmp_path / "home"
    home.mkdir()
    for var in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(var, str(home / var.lower()))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv(C.ENV_CONFIG, raising=False)
    return home


# ----------------------------------------------------------------------------- lookup
def test_no_config_anywhere_is_an_empty_town(no_user_config):
    assert find_config() is None
    cfg = load_config()
    assert cfg.buildings == ()
    assert cfg.path is None
    assert cfg.town.title == "Lamptown"


def test_lookup_order_flag_then_env_then_user_dir(tmp_path, no_user_config):
    flag = write(tmp_path / "a" / "lamptown.toml", '[town]\ntitle = "flag"\n')
    envf = write(tmp_path / "b" / "lamptown.toml", '[town]\ntitle = "env"\n')
    user = C.user_config_dir() / C.CONFIG_NAME
    write(user, '[town]\ntitle = "user"\n')
    env = dict(os.environ)

    assert load_config(flag, env={**env, C.ENV_CONFIG: str(envf)}).town.title == "flag"
    assert load_config(None, env={**env, C.ENV_CONFIG: str(envf)}).town.title == "env"
    env.pop(C.ENV_CONFIG, None)
    assert load_config(None, env=env).town.title == "user"


def test_explicit_path_must_exist(tmp_path):
    with pytest.raises(ConfigError, match="--config"):
        find_config(tmp_path / "nope.toml", env={})
    with pytest.raises(ConfigError, match="LAMPTOWN_CONFIG"):
        find_config(None, env={C.ENV_CONFIG: str(tmp_path / "nope.toml")})


def test_user_dirs_follow_env(tmp_path):
    env = {"APPDATA": str(tmp_path / "ad"), "LOCALAPPDATA": str(tmp_path / "lad"),
           "XDG_CONFIG_HOME": str(tmp_path / "xc"), "XDG_DATA_HOME": str(tmp_path / "xd")}
    assert C.user_config_dir(env).name == "lamptown"
    assert C.user_data_dir(env).name == "lamptown"

    if sys.platform == "win32":
        assert C.user_config_dir(env).parent == tmp_path / "ad"
    elif sys.platform != "darwin":
        assert C.user_config_dir(env).parent == tmp_path / "xc"
        assert C.user_data_dir(env).parent == tmp_path / "xd"


def test_bom_is_fine_and_non_utf8_is_a_clear_error(tmp_path):
    p = tmp_path / "lamptown.toml"
    p.write_bytes(b'\xef\xbb\xbf[town]\ntitle = "bom"\n')
    assert load_config(p, env={}).town.title == "bom"
    p.write_bytes(b'[town]\ntitle = "\xff"\n')
    with pytest.raises(ConfigError, match="not UTF-8"):
        load_config(p, env={})


# ----------------------------------------------------------------------------- buildings and plots
def test_builtin_types_get_their_home_plot_and_default_label():
    cfg = parse_config('[[building]]\nid = "jobs"\n[[building]]\nid = "notes"\ntype = "vault"\n')
    by = cfg.by_plot()
    assert by["tower"].type == "jobs" and by["tower"].label == "Scheduled jobs"
    assert by["library"].id == "notes"
    assert set(C.PLOTS) == {"tower", "library", "gate", "lab", "workshop", "boathouse"}


def test_label_plot_and_custom_type():
    cfg = parse_config(
        '[[building]]\nid = "kiosk"\ntype = "kiosk"\nplot = "lab"\nlabel = "Tea kiosk"\n'
    )
    (b,) = cfg.buildings
    assert (b.type, b.plot, b.label) == ("kiosk", "lab", "Tea kiosk")


def test_disabled_building_leaves_an_empty_lot():
    cfg = parse_config(
        '[[building]]\nid = "jobs"\nenabled = false\n[[building]]\nid = "x"\ntype = "jobs"\n'
    )
    assert [b.id for b in cfg.buildings] == ["x"]  # the disabled one frees the tower for x


JOBS_A = '[[building]]\nid = "a"\ntype = "jobs"\n'


@pytest.mark.parametrize(("toml", "words"), [
    ('version = 2\n', "version"),
    ('nope = 1\n', "unknown key 'nope'"),
    ('[town]\nlatitude = 91\n', "town.latitude"),
    ('[town]\nlongitude = "east"\n', "must be a number"),
    ('[town]\ncolour = "red"\n', "unknown key 'colour'"),
    ('[[building]]\ntype = "jobs"\n', "needs an id"),
    ('[[building]]\nid = "Bad Id"\n', "lower-case"),
    ('[[building]]\nid = "a"\n', "type"),
    ('[[building]]\nid = "a"\ntype = "kiosk"\n', "needs plot"),
    ('[[building]]\nid = "a"\ntype = "jobs"\nplot = "moon"\n', "not a plot"),
    (JOBS_A + '[[building]]\nid = "a"\ntype = "vault"\n', "used twice"),
    (JOBS_A + '[[building]]\nid = "b"\ntype = "jobs"\n', "already holds"),
    ('[[building]]\nid = "a"\ntype = "jobs"\nlabel = "' + "x" * 40 + '"\n', "at most 32"),
    ('[[building]]\nid = "a"\ntype = "jobs"\nenabled = "yes"\n', "true or false"),
    ('[[building]]\nid = "a"\ntype = "jobs"\n[[building.source]]\nglob = "x"\n', "needs kind"),
    ('[[building]]\nid = "a"\ntype = "jobs"\n[[building.source]]\nkind = "x"\n_room = "y"\n',
     "reserved"),
    ('[[building]]\nid = "a"\ntype = "jobs"\n[[building.source]]\nkind = "x"\ncollector = 3\n',
     "path string"),
    ('this is = = not toml', "not valid TOML"),
])
def test_errors_name_the_key_and_the_problem(toml, words):
    with pytest.raises(ConfigError) as e:
        parse_config(toml, Path("/cfg/lamptown.toml"))
    assert words in str(e.value)
    assert "lamptown.toml" in str(e.value)


# ----------------------------------------------------------------------------- paths
def test_source_paths_are_resolved(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    cfg_path = write(tmp_path / "cfg" / "lamptown.toml", """
[[building]]
id = "jobs"
  [[building.source]]
  kind = "log_glob"
  glob = "~/logs/*.log"
  status_dir = "./status"
  dirs = ["a", "~/b"]
  pattern = "./not-a-path"
  collector = "./mine.py"
""")
    (src,) = load_config(cfg_path, env={}).buildings[0].sources
    t = src.table
    assert Path(t["glob"]) == home / "logs" / "*.log"
    assert Path(t["status_dir"]) == tmp_path / "cfg" / "status"
    assert [Path(x) for x in t["dirs"]] == [tmp_path / "cfg" / "a", home / "b"]
    assert t["pattern"] == "./not-a-path"  # not a path key: left as written
    assert src.collector == str(tmp_path / "cfg" / "mine.py")
    assert src.kind == "log_glob" and src.name == "log_glob"


def test_collector_only_source_gets_kind_local(tmp_path):
    cfg = parse_config('[[building]]\nid = "jobs"\n[[building.source]]\ncollector = "m.py"\n',
                       tmp_path / "lamptown.toml")
    (s,) = cfg.buildings[0].sources
    assert s.kind == "local" and s.collector == str(tmp_path / "m.py")


def test_town_settings(tmp_path):
    text = '[town]\ntitle = "Kite field"\nlatitude = -33.9\nlongitude = 18.4\nout = "site"\n'
    cfg = parse_config(text, tmp_path / "lamptown.toml")
    assert cfg.town.title == "Kite field"
    assert (cfg.town.latitude, cfg.town.longitude) == (-33.9, 18.4)
    assert Path(cfg.town.out) == tmp_path / "site"
    assert cfg.base_dir == tmp_path
