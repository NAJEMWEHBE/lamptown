"""`lamptown shortcut`: the launcher files for each OS. macOS and Linux files are written and
checked on every OS; the Windows .lnk is made (in a temp folder, never on the real desktop), read
back and launched on Windows only."""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lamptown.app import shortcut as sc
from lamptown.app.cli import cmd_shortcut

WIN = sys.platform == "win32"


def write_config(folder: Path) -> Path:
    p = folder / "lamptown.toml"
    p.write_text(
        'version = 1\n[town]\ntitle = "Kite Field"\n\n[[building]]\nid = "docks"\n'
        'type = "harbour"\nlabel = "Docks"\n  [[building.source]]\n  kind = "static"\n'
        '  state = "ok"\n  rows = [{ id = "tidebook", name = "Tidebook", status = "active", '
        'state = "ok", progress = { done = 2, total = 6 } }]\n',
        encoding="utf-8",
    )
    return p


# ----------------------------------------------------------------------------- what it runs
def test_launch_runs_the_app_module_with_an_absolute_config(tmp_path):
    cfg = write_config(tmp_path)
    launch = sc.launch_for(str(cfg), extra=["--poll", "5"])
    assert launch.args[:3] == ("-P", "-m", "lamptown.app")
    assert launch.args[3:] == ("--config", str(cfg.resolve()), "--poll", "5")
    assert launch.argv[0] == str(launch.python)
    assert sc.launch_for().args == ("-P", "-m", "lamptown.app")


def test_gui_python_prefers_pythonw_on_windows(tmp_path):
    exe = tmp_path / "python.exe"
    exe.write_bytes(b"")
    assert sc.gui_python(exe, "win32") == exe  # no pythonw next to it
    (tmp_path / "pythonw.exe").write_bytes(b"")
    assert sc.gui_python(exe, "win32") == tmp_path / "pythonw.exe"
    assert sc.gui_python(exe, "linux") == exe


def test_default_destinations(tmp_path):
    home = tmp_path / "home"
    assert sc.default_dest("win32", {}, home) is None  # PowerShell asks for the real Desktop
    assert sc.default_dest("darwin", {}, home) == home / "Applications"
    assert sc.default_dest("linux", {}, home) == home / ".local" / "share" / "applications"
    xdg = tmp_path / "xdg"
    assert sc.default_dest("linux", {"XDG_DATA_HOME": str(xdg)}, home) == xdg / "applications"


# ----------------------------------------------------------------------------- Linux
def unquote_exec(value: str) -> list[str]:
    """Read an Exec= value back the way the Desktop Entry spec says a desktop does: string-value
    escapes first, then %% and the quoting rules (inside quotes a backslash escapes one of
    " ` $ and itself)."""
    text, i = [], 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            text.append(value[i + 1])
            i += 2
        else:
            text.append(value[i])
            i += 1
    line = "".join(text).replace("%%", "%")
    args: list[str] = []
    cur: list[str] | None = None
    quoted = False
    i = 0
    while i < len(line):
        ch = line[i]
        if quoted:
            if ch == "\\" and i + 1 < len(line) and line[i + 1] in '"`$\\':
                cur.append(line[i + 1])
                i += 1
            elif ch == '"':
                quoted = False
            else:
                cur.append(ch)
        elif ch == " ":
            if cur is not None:
                args.append("".join(cur))
                cur = None
        elif ch == '"':
            cur = cur if cur is not None else []
            quoted = True
        else:
            cur = (cur if cur is not None else []) + [ch]
        i += 1
    if cur is not None:
        args.append("".join(cur))
    return args


@pytest.mark.parametrize(
    "arg",
    ["plain", "/opt/py 3/bin/python", r"C:\Program Files\Py\pythonw.exe", 'say "hi"', "50%",
     "$HOME", "back`tick", "a\\b c", ""],
)
def test_desktop_quote_round_trips(arg):
    assert unquote_exec(sc.desktop_quote(arg)) == [arg]


def test_linux_desktop_entry(tmp_path):
    cfg = write_config(tmp_path)
    launch = sc.Launch(Path("/opt/py env/bin/python3"), ("-P", "-m", "lamptown.app",
                                                        "--config", str(cfg)))
    path = sc.linux_desktop(launch, tmp_path / "apps", tmp_path / "icons")
    assert path == tmp_path / "apps" / "lamptown.desktop"
    text = path.read_text(encoding="utf-8")
    fields = dict(line.split("=", 1) for line in text.splitlines()[1:])
    assert text.splitlines()[0] == "[Desktop Entry]"
    assert fields["Type"] == "Application" and fields["Name"] == "Lamptown"
    assert fields["Terminal"] == "false"
    assert unquote_exec(fields["Exec"]) == launch.argv
    icon = Path(fields["Icon"])
    assert icon == tmp_path / "icons" / "lamptown.png"
    assert icon.read_bytes().startswith(b"\x89PNG")
    if not WIN:
        assert os.access(path, os.X_OK)


# ----------------------------------------------------------------------------- macOS
def test_mac_app_bundle(tmp_path):
    launch = sc.Launch(Path("/Users/you/.venv/bin/python"), ("-P", "-m", "lamptown.app"))
    app = sc.mac_app(launch, tmp_path / "Applications")
    assert app == tmp_path / "Applications" / "Lamptown.app"
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "Lamptown" and info["CFBundlePackageType"] == "APPL"
    assert info["CFBundleIconFile"] == "lamptown.icns"
    assert info["CFBundleShortVersionString"].count(".") == 2
    script = app / "Contents" / "MacOS" / "Lamptown"
    lines = script.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#!/bin/sh"
    assert shlex.split(lines[-1]) == ["exec", *launch.argv, "$@"]
    icns = (app / "Contents" / "Resources" / "lamptown.icns").read_bytes()
    assert icns[:4] == b"icns" and int.from_bytes(icns[4:8], "big") == len(icns)
    if not WIN:
        assert os.access(script, os.X_OK)


@pytest.mark.parametrize(("platform", "name"), [("darwin", "Lamptown.app"),
                                                ("linux", "lamptown.desktop")])
def test_make_shortcut_dispatches_per_platform(tmp_path, platform, name):
    path = sc.make_shortcut(tmp_path / "dest", icon_dir=tmp_path / "icons", platform=platform)
    assert path == tmp_path / "dest" / name and path.exists()


# ----------------------------------------------------------------------------- Windows
needs_windows = pytest.mark.skipif(not WIN, reason="Windows .lnk")


def has_pywebview() -> bool:
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return True


@needs_windows
def test_windows_lnk_is_written_and_reads_back(tmp_path):
    cfg = write_config(tmp_path)
    lnk = sc.make_shortcut(tmp_path / "desk", config=cfg, icon_dir=tmp_path / "icons",
                           name="Lamptown test")
    assert lnk == tmp_path / "desk" / "Lamptown test.lnk" and lnk.is_file()
    got = sc.read_windows_shortcut(lnk)
    assert Path(got["target"]) == sc.gui_python()
    assert Path(got["target"]).name.lower() == "pythonw.exe" or not (
        Path(sys.executable).with_name("pythonw.exe").exists())
    assert got["arguments"] == subprocess.list2cmdline(
        ["-P", "-m", "lamptown.app", "--config", str(cfg.resolve())])
    assert got["icon"].lower() == (str(tmp_path / "icons" / "lamptown.ico") + ",0").lower()
    assert (tmp_path / "icons" / "lamptown.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"


@needs_windows
def test_windows_shortcut_cli(tmp_path, capsys):
    import argparse

    rc = cmd_shortcut(argparse.Namespace(dir=str(tmp_path), config=None, name="Lamptown"))
    assert rc == 0
    assert (tmp_path / "Lamptown.lnk").is_file()
    assert "Lamptown.lnk" in capsys.readouterr().out


@needs_windows
@pytest.mark.skipif(not has_pywebview(), reason="needs the [app] extra (pywebview)")
def test_windows_shortcut_launches_the_app(tmp_path):
    """Double-click equivalent: the .lnk starts pythonw, the window opens on the town, a second
    launch is answered by it, and it quits when asked."""
    from lamptown.app.instance import INFO_NAME, SingleInstance

    cfg = write_config(tmp_path)
    state, out = tmp_path / "state", tmp_path / "out"
    lnk = sc.make_shortcut(tmp_path / "desk", config=cfg, icon_dir=tmp_path / "icons",
                           extra=["--state-dir", str(state), "--out", str(out), "--poll", "1"])
    os.startfile(str(lnk))  # what Explorer does on a double-click
    log = state / "app.log"

    def logged(text: str) -> bool:
        return log.is_file() and text in log.read_text(encoding="utf-8", errors="replace")

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not logged("page loaded: town.html"):
        time.sleep(0.25)
    try:
        said = log.read_text(encoding="utf-8") if log.is_file() else "no log"
        assert logged("window shown"), said
        assert logged("page loaded: town.html")
        assert (out / "town.html").is_file()
        info = json.loads((state / INFO_NAME).read_text(encoding="utf-8"))
        assert info["pid"] != os.getpid()
        other = SingleInstance(state)
        assert not other.acquire(), "the launched app must hold the lock"
        assert other.send("focus")
    finally:
        SingleInstance(state).send("quit", timeout=3)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not logged("app closed"):
        time.sleep(0.25)
    assert logged("app closed")


def test_shortcut_cli_refuses_a_missing_config(tmp_path, capsys):
    import argparse

    rc = cmd_shortcut(argparse.Namespace(dir=str(tmp_path), config=str(tmp_path / "nope.toml"),
                                         name="Lamptown"))
    assert rc == 2
    assert not list(tmp_path.iterdir())  # no shortcut made
    assert "no config file" in capsys.readouterr().err
