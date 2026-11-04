"""``lamptown doctor``: each check, the report, exit codes; it changes and opens nothing."""

from __future__ import annotations

import builtins
import io
import json
import os
from pathlib import Path

import pytest

from lamptown.cli import main
from lamptown.config import CONFIG_NAME, parse_config
from lamptown.onboarding import browser as BR
from lamptown.onboarding import doctor as D

PLANTED = "kestrel-private-note-91"
STATUS = {"spec": "agent-status/1", "id": "wren", "title": "Wren: tidy the kite shed",
          "state": "ok", "updated": "2026-03-14T21:50:00Z"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    for k, v in {"HOME": h, "USERPROFILE": h, "APPDATA": tmp_path / "appdata",
                 "LOCALAPPDATA": tmp_path / "localappdata",
                 "XDG_CONFIG_HOME": tmp_path / "xdg-config",
                 "XDG_DATA_HOME": tmp_path / "xdg-data"}.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.delenv("AGENT_STATUS_DIR", raising=False)
    monkeypatch.delenv("LAMPTOWN_CONFIG", raising=False)
    # the machine-dependent checks are tested on their own below
    monkeypatch.setattr(D, "check_window", lambda: D.Check(D.OK, "Desktop window", "fake"))
    monkeypatch.setattr(D, "check_browser", lambda: D.Check(D.OK, "Headless browser", "fake"))
    return h


def by_what(checks, what):
    return [c for c in checks if c.what.startswith(what)]


def write_cfg(folder: Path, body: str) -> Path:
    p = folder / CONFIG_NAME
    p.write_text(f'version = 1\n[town]\nout = "out"\n{body}', encoding="utf-8")
    return p


# ----------------------------------------------------------------------------- python + config
def test_python_version():
    assert D.check_python((3, 11, 0)).level == D.OK
    assert D.check_python((3, 13, 2)).level == D.OK
    bad = D.check_python((3, 10, 9))
    assert bad.level == D.FAIL and "3.11" in bad.detail and bad.fix


def test_no_config_is_a_warning_with_the_fix(env):
    c, cfg = D.check_config(None)
    assert c.level == D.WARN and "lamptown init --user" in c.fix
    assert cfg is not None and cfg.buildings == ()


def test_missing_explicit_config_fails(env, tmp_path):
    c, cfg = D.check_config(str(tmp_path / "nope.toml"))
    assert c.level == D.FAIL and cfg is None and "not a file" in c.detail


def test_invalid_config_fails_with_its_message(env, tmp_path):
    p = tmp_path / CONFIG_NAME
    p.write_text('version = 1\n[[building]]\nid = "Bad Id"\n', encoding="utf-8")
    c, cfg = D.check_config(str(p))
    assert c.level == D.FAIL and cfg is None and "lower-case" in c.detail


def test_valid_config_counts_buildings(env, tmp_path):
    p = write_cfg(tmp_path, '[[building]]\nid = "jobs"\n')
    c, cfg = D.check_config(str(p))
    assert c.level == D.OK and "(1 building)" in c.detail and len(cfg.buildings) == 1


# ----------------------------------------------------------------------------- sources
SOURCES = """
[[building]]
id = "jobs"
  [[building.source]]
  kind = "log_glob"
  glob = "logs/*.log"
  [[building.source]]
  kind = "log_glob"
  glob = "nowhere/*.log"
  [[building.source]]
  kind = "log_glob"
  glob = "quiet/*.log"
[[building]]
id = "vault"
  [[building.source]]
  kind = "markdown_vault"
  path = "missing-notes"
[[building]]
id = "ledger"
  [[building.source]]
  kind = "scan_glob"
  glob = "scans/*.json"
  decisions_file = "decisions.json"
[[building]]
id = "contest"
  [[building.source]]
  kind = "kite_counter"
  [[building.source]]
  collector = "./gone.py"
[[building]]
id = "kiosk"
type = "kiosk"
plot = "boathouse"
room_js = "./kiosk.js"
  [[building.source]]
  kind = "static"
  state = "ok"
[[building]]
id = "tea"
type = "tea_room"
plot = "workshop"
"""


@pytest.fixture
def sources(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "a.log").write_text("Done: ok\n", encoding="utf-8")
    (tmp_path / "logs" / "b.log").write_text("Done: ok\n", encoding="utf-8")
    (tmp_path / "quiet").mkdir()
    (tmp_path / "scans").mkdir()
    (tmp_path / "scans" / "x.json").write_text("{}", encoding="utf-8")
    p = write_cfg(tmp_path, SOURCES)
    return parse_config(p.read_text(encoding="utf-8"), p)


def test_each_source_path_is_checked(sources):
    checks = list(D.check_sources(sources))
    jobs = by_what(checks, "jobs /")
    assert [c.level for c in jobs] == [D.OK, D.FAIL, D.WARN]
    assert "2 matches" in jobs[0].detail
    assert "no folder" in jobs[1].detail and "nowhere" in jobs[1].detail
    assert "nothing matches" in jobs[2].detail
    (vault,) = by_what(checks, "vault /")
    assert vault.level == D.FAIL and "missing-notes" in vault.detail and vault.fix
    ledger = by_what(checks, "ledger /")
    assert [c.level for c in ledger] == [D.OK, D.WARN]  # decisions_file is optional
    contest, local = by_what(checks, "contest /")
    assert contest.level == D.FAIL and "unknown kind 'kite_counter'" in contest.detail
    assert local.level == D.FAIL and "gone.py" in local.detail
    (kiosk,) = by_what(checks, "kiosk: room script")
    assert kiosk.level == D.FAIL and "kiosk.js" in kiosk.detail
    tea = by_what(checks, "tea")
    assert {c.level for c in tea} == {D.WARN}
    assert any("no room script" in c.detail for c in tea)
    assert any("no sources" in c.detail for c in tea)


def test_projects_paths_and_the_status_folder(tmp_path):
    (tmp_path / "paper-kite").mkdir()
    status = tmp_path / "lamps"
    p = write_cfg(tmp_path, f"""
[[building]]
id = "harbour"
  [[building.source]]
  kind = "projects"
  projects = [{{ name = "Paper Kite", path = "paper-kite", status = "active" }},
              {{ name = "Moss Engine", path = "moss-engine", status = "paused" }}]
  [[building.source]]
  kind = "status_dir"
  dir = {json.dumps(str(status))}
  [[building.source]]
  kind = "status_dir"
  dir = "other-lamps"
  [[building.source]]
  kind = "status_dir"
""")
    cfg = parse_config(p.read_text(encoding="utf-8"), p)
    checks = list(D.check_sources(cfg, status))
    assert [(c.level, c.detail.split(":")[0]) for c in checks] == [
        (D.OK, "project 'Paper Kite'"),
        (D.WARN, "project 'Moss Engine'"),
        (D.OK, "dir"),  # the status folder: checked once, by the status check
        (D.WARN, "dir"),  # another status folder: made on first write, so only a warning
        (D.OK, "no dir"),
    ]
    assert "checked below" in checks[2].detail


# ----------------------------------------------------------------------------- status folder
def test_status_folder_missing_is_a_warning(tmp_path):
    (c,) = D.check_status_dir(str(tmp_path / "lamps"))
    assert c.level == D.WARN and "does not exist yet" in c.detail


def test_status_folder_that_is_a_file_fails(tmp_path):
    f = tmp_path / "lamps"
    f.write_text("x", encoding="utf-8")
    (c,) = D.check_status_dir(str(f))
    assert c.level == D.FAIL and "is a file" in c.detail


def test_status_folder_counts_files_and_names_broken_ones(tmp_path):
    d = tmp_path / "lamps"
    d.mkdir()
    (d / "wren.json").write_text(json.dumps(STATUS), encoding="utf-8")
    (d / "otter.json").write_text(json.dumps(dict(STATUS, id="otter", state="sleepy")),
                                  encoding="utf-8")
    (d / "notes.txt").write_text("not a status file", encoding="utf-8")
    checks = list(D.check_status_dir(str(d)))
    assert checks[0].level == D.OK and "writable, 1 status file" in checks[0].detail
    assert [c.level for c in checks[1:]] == [D.FAIL]
    assert "otter.json" in checks[1].detail
    assert sorted(p.name for p in d.iterdir()) == ["notes.txt", "otter.json", "wren.json"]


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="POSIX permissions, not as root")
def test_status_folder_not_writable_fails(tmp_path):
    d = tmp_path / "lamps"
    d.mkdir()
    d.chmod(0o500)
    try:
        (c,) = D.check_status_dir(str(d))
    finally:
        d.chmod(0o700)
    assert c.level == D.FAIL and "not writable" in c.detail


def test_output_folder(tmp_path, env):
    p = write_cfg(tmp_path, "")
    cfg = parse_config(p.read_text(encoding="utf-8"), p)
    c = D.check_out_dir(cfg)
    assert c.level == D.OK and not (tmp_path / "out").exists()  # checked, not made
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    p.write_text('version = 1\n[town]\nout = "blocker/out"\n', encoding="utf-8")
    c = D.check_out_dir(parse_config(p.read_text(encoding="utf-8"), p))
    assert c.level == D.FAIL


# ----------------------------------------------------------------------------- window + browser
@pytest.mark.parametrize(("platform", "mods", "want"), [
    ("win32", {"webview", "clr"}, "WebView2"),
    ("win32", {"webview"}, None),
    ("darwin", {"webview", "objc", "WebKit"}, "WebKit"),
    ("darwin", {"webview", "objc"}, None),
    ("linux", {"webview", "gi"}, "GTK"),
    ("linux", {"webview", "PyQt6"}, "Qt (PyQt6)"),
    ("linux", {"webview"}, None),
])
def test_gui_backend(platform, mods, want):
    got = D.gui_backend(platform, mods.__contains__)
    assert (got is None) if want is None else want in got
    c = D.check_window(platform, mods.__contains__)
    assert c.level == (D.OK if want else D.WARN)
    if not want:
        assert "no GUI backend" in c.detail and c.fix


def test_without_pywebview_the_window_check_warns():
    c = D.check_window("linux", set().__contains__)
    assert c.level == D.WARN and "lamptown[app]" in c.fix


def fake_fs(files):
    files = {str(Path(f)) for f in files}
    return (lambda p: str(p) in files)


def test_browser_env_override_path_or_name():
    got = BR.find_browser({"CHROME": "/opt/kite/chrome"}, "linux", lambda n: None,
                          fake_fs(["/opt/kite/chrome"]))
    assert got.path == "/opt/kite/chrome" and "$CHROME" in got.why
    got = BR.find_browser({"CHROME": "kite-browser"}, "linux",
                          {"kite-browser": "/usr/local/bin/kite-browser"}.get, fake_fs([]))
    assert got.path == "/usr/local/bin/kite-browser"


def test_a_bad_browser_override_is_not_silently_replaced():
    got = BR.find_browser({"CHROME": "/opt/none"}, "linux", {"chromium": "/usr/bin/x"}.get,
                          fake_fs(["/usr/bin/google-chrome"]))
    assert got.path is None and "/opt/none" in got.why


def test_browser_usual_places_per_os():
    win = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    got = BR.find_browser({"PROGRAMFILES": "C:/Program Files"}, "win32", lambda n: None,
                          fake_fs([win]))
    assert got.path == str(win)
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    got = BR.find_browser({"PROGRAMFILES(X86)": "C:/Program Files (x86)"}, "win32",
                          lambda n: None, fake_fs([edge]))
    assert got.path == str(edge)
    mac = Path("/Applications/Chromium.app/Contents/MacOS/Chromium")
    got = BR.find_browser({}, "darwin", lambda n: None, fake_fs([mac]), home=Path("/Users/you"))
    assert got.path == str(mac)
    got = BR.find_browser({}, "linux", {"chromium-browser": "/usr/bin/chromium-browser"}.get,
                          fake_fs([]))
    assert got.path == "/usr/bin/chromium-browser"


def test_no_browser_says_how_to_get_one():
    got = BR.find_browser({}, "linux", lambda n: None, fake_fs([]))
    assert got.path is None and "$CHROME" in got.why


# ----------------------------------------------------------------------------- the command
def test_report_shape():
    text = D.report([D.Check(D.OK, "Python 3.13.0"),
                     D.Check(D.WARN, "Config", "none yet", "lamptown init --user"),
                     D.Check(D.FAIL, "jobs / source #1", "no folder ~/logs", "create it")])
    lines = text.splitlines()
    assert "  ok    Python 3.13.0" in lines
    assert "  warn  Config: none yet" in lines and "        fix: lamptown init --user" in lines
    assert "  FAIL  jobs / source #1: no folder ~/logs" in lines
    assert lines[-1] == "1 problem, 1 warning."
    quiet = D.report([D.Check(D.OK, "Python 3.13.0")], quiet=True)
    assert "Python" not in quiet and quiet.splitlines()[-1] == "All good."


def test_exit_codes(env, tmp_path, capsys):
    status = tmp_path / "lamps"
    status.mkdir()
    good = write_cfg(tmp_path, '[[building]]\nid = "agents"\ntype = "harbour"\n'
                               '  [[building.source]]\n  kind = "status_dir"\n'
                               f"  dir = {json.dumps(str(status))}\n")
    assert main(["doctor", "--config", str(good), "--status-dir", str(status)]) == 0
    assert capsys.readouterr().out.rstrip().endswith("All good.")
    assert main(["doctor", "--config", str(good), "--status-dir", str(tmp_path / "x")]) == 0
    assert main(["doctor", "--config", str(good), "--status-dir", str(tmp_path / "x"),
                 "--strict"]) == 1
    bad = write_cfg(tmp_path, '[[building]]\nid = "notes"\ntype = "vault"\n'
                              '  [[building.source]]\n  kind = "markdown_vault"\n'
                              '  path = "no-such-vault"\n')
    assert main(["doctor", "--config", str(bad), "--status-dir", str(status)]) == 1
    out = capsys.readouterr().out
    assert "FAIL  notes / source #1 (markdown_vault)" in out and "fix:" in out


def test_doctor_after_init_has_no_problems(env, tmp_path, capsys):
    assert main(["init", "--dir", str(tmp_path), "--no-detect"]) == 0
    assert main(["doctor", "--config", str(tmp_path / CONFIG_NAME)]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out and "0 problems" in out


def test_doctor_changes_nothing_and_opens_no_source_file(env, tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / "kites").mkdir(parents=True)
    (vault / "kites" / "secret.md").write_text(PLANTED, encoding="utf-8")
    status = tmp_path / "lamps"
    status.mkdir()
    (status / "wren.json").write_text(json.dumps(STATUS), encoding="utf-8")
    cfg = write_cfg(tmp_path, '[[building]]\nid = "notes"\ntype = "vault"\n'
                              '  [[building.source]]\n  kind = "markdown_vault"\n'
                              '  path = "vault"\n')

    def snapshot():
        return {p.relative_to(tmp_path).as_posix(): p.read_bytes() if p.is_file() else None
                for p in sorted(tmp_path.rglob("*"))}

    before = snapshot()
    real_open = builtins.open

    def guard(file, *a, **k):
        if not isinstance(file, int) and vault in Path(os.fsdecode(file)).resolve().parents:
            raise AssertionError(f"doctor opened a source file: {file}")
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", guard)
    monkeypatch.setattr(io, "open", guard)
    code = main(["doctor", "--config", str(cfg), "--status-dir", str(status)])
    monkeypatch.undo()
    assert code == 0, capsys.readouterr().out
    assert snapshot() == before
    assert PLANTED not in capsys.readouterr().out
