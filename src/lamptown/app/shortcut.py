"""``lamptown shortcut``: a launcher for the desktop app, the native way on each OS.

* Windows: a ``.lnk`` on the desktop, written through the built-in WScript.Shell COM object driven
  by PowerShell (no pywin32). It runs ``pythonw`` so no console window shows.
* macOS: ``~/Applications/Lamptown.app``, a minimal bundle (Info.plist, a launcher script and the
  ``.icns`` icon).
* Linux and other XDG desktops: ``~/.local/share/applications/lamptown.desktop`` plus a PNG icon,
  so the app shows in the desktop's app menu.

Every launcher runs ``<this python> -P -m lamptown.app [--config FILE]``: ``-P`` keeps the working
folder off ``sys.path``, and the user's site-packages stay available.
"""

from __future__ import annotations

import base64
import os
import plistlib
import shlex
import shutil
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lamptown import __version__
from lamptown.app.window import asset

NAME = "Lamptown"
DESCRIPTION = "Lamptown: the pixel town of your agents, live"
BUNDLE_ID = "local.lamptown.app"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: PowerShell never flashes a console


class ShortcutError(RuntimeError):
    pass


@dataclass(frozen=True)
class Launch:
    """What a shortcut runs."""

    python: Path
    args: tuple[str, ...]

    @property
    def argv(self) -> list[str]:
        return [str(self.python), *self.args]


def gui_python(executable: str | os.PathLike[str] | None = None, platform: str | None = None
               ) -> Path:
    """The interpreter a shortcut should start: ``pythonw`` next to this one on Windows."""
    exe = Path(executable or sys.executable)
    platform = sys.platform if platform is None else platform
    if platform == "win32" and exe.name.lower() == "python.exe":
        w = exe.with_name("pythonw.exe")
        if w.is_file():
            return w
    return exe


def launch_for(config: str | os.PathLike[str] | None = None, extra: Sequence[str] = (),
               executable: str | os.PathLike[str] | None = None, platform: str | None = None
               ) -> Launch:
    args = ["-P", "-m", "lamptown.app"]
    if config:
        args += ["--config", str(Path(config).expanduser().resolve())]
    args += list(extra)
    return Launch(gui_python(executable, platform), tuple(args))


# ----------------------------------------------------------------------------- Windows
PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
trap { Write-Output ('ERROR: ' + $_.Exception.Message); exit 1 }
$dir = if ($env:LT_DIR) { $env:LT_DIR } else { [Environment]::GetFolderPath('Desktop') }
$path = Join-Path $dir ($env:LT_NAME + '.lnk')
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($path)
$lnk.TargetPath = $env:LT_TARGET
$lnk.Arguments = $env:LT_ARGS
$lnk.WorkingDirectory = $env:LT_CWD
$lnk.WindowStyle = 1
if ($env:LT_ICON) { $lnk.IconLocation = $env:LT_ICON + ',0' }
$lnk.Description = $env:LT_DESC
$lnk.Save()
Write-Output $path
"""

PS_READ = r"""
$ErrorActionPreference = 'Stop'
trap { Write-Output ('ERROR: ' + $_.Exception.Message); exit 1 }
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LT_LNK)
Write-Output $lnk.TargetPath
Write-Output $lnk.Arguments
Write-Output $lnk.WorkingDirectory
Write-Output $lnk.IconLocation
"""


def powershell() -> str:
    for name in ("powershell.exe", "pwsh.exe", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    raise ShortcutError("PowerShell isn't on PATH, so the .lnk can't be written")


def run_powershell(script: str, env: Mapping[str, str]) -> str:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    kwargs = {"creationflags": NO_WINDOW} if sys.platform == "win32" else {}
    r = subprocess.run(
        [powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", encoded],
        env={**os.environ, **env}, capture_output=True, text=True, timeout=60, check=False,
        **kwargs,
    )
    if r.returncode:
        said = [ln[7:] for ln in r.stdout.splitlines() if ln.startswith("ERROR: ")]
        raise ShortcutError("PowerShell failed: " + (said[-1] if said else
                                                     f"exit {r.returncode}"))
    return r.stdout


def windows_shortcut(launch: Launch, dest: Path | None, icon_dir: Path, name: str = NAME,
                     work_dir: Path | None = None) -> Path:
    icon = _copy_icon("lamptown.ico", icon_dir)
    if dest:
        dest.mkdir(parents=True, exist_ok=True)
    out = run_powershell(PS_SCRIPT, {
        "LT_DIR": str(dest) if dest else "", "LT_NAME": name,
        "LT_TARGET": str(launch.python), "LT_ARGS": subprocess.list2cmdline(launch.args),
        "LT_CWD": str(work_dir or icon_dir), "LT_ICON": str(icon) if icon else "",
        "LT_DESC": DESCRIPTION,
    })
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not lines:
        raise ShortcutError("PowerShell didn't say where it wrote the shortcut")
    return Path(lines[-1])


def read_windows_shortcut(lnk: Path) -> dict[str, str]:
    """Target, arguments, working folder and icon of a .lnk (for tests and `doctor`)."""
    lines = run_powershell(PS_READ, {"LT_LNK": str(lnk)}).splitlines()
    lines += [""] * (4 - len(lines))
    return dict(zip(("target", "arguments", "work_dir", "icon"), lines[:4], strict=True))


# ----------------------------------------------------------------------------- macOS
def mac_app(launch: Launch, dest: Path, name: str = NAME) -> Path:
    app = dest / f"{name}.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    (contents / "Resources").mkdir(parents=True, exist_ok=True)
    plist = {
        "CFBundleName": name, "CFBundleDisplayName": name, "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": name, "CFBundleIconFile": "lamptown.icns",
        "CFBundlePackageType": "APPL", "CFBundleShortVersionString": _short_version(),
        "CFBundleVersion": _short_version(), "CFBundleInfoDictionaryVersion": "6.0",
        "LSMinimumSystemVersion": "10.13", "NSHighResolutionCapable": True,
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(plist))
    script = contents / "MacOS" / name
    script.write_text(
        "#!/bin/sh\n# Starts the Lamptown desktop app (written by `lamptown shortcut`).\n"
        f"exec {shlex.join(launch.argv)} \"$@\"\n",
        encoding="utf-8", newline="\n",
    )
    _make_executable(script)
    _copy_icon("lamptown.icns", contents / "Resources")
    return app


def _short_version() -> str:
    parts = []
    for p in __version__.split(".")[:3]:
        digits = "".join(ch for ch in p if ch.isdigit())
        if not digits or digits != p:
            parts.append(digits or "0")
            break
        parts.append(digits)
    while len(parts) < 3:
        parts.append("0")
    return ".".join(parts[:3])


# ----------------------------------------------------------------------------- Linux / XDG
def desktop_quote(arg: str) -> str:
    """One Exec argument, quoted per the Desktop Entry spec (then escaped as a string value)."""
    if arg and not any(c in arg for c in " \t\n\"'\\><~|&;$*?#()`%"):
        return arg
    inner = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    quoted = f'"{inner}"'
    return quoted.replace("\\", "\\\\").replace("%", "%%")


def desktop_entry(launch: Launch, icon: Path | None, name: str = NAME) -> str:
    exec_line = " ".join(desktop_quote(a) for a in launch.argv)
    lines = [
        "[Desktop Entry]", "Type=Application", "Version=1.5", f"Name={name}",
        f"Comment={DESCRIPTION}", f"Exec={exec_line}", "Terminal=false",
        "Categories=Utility;Development;", "StartupNotify=true",
    ]
    if icon:
        lines.append(f"Icon={icon}")
    return "\n".join(lines) + "\n"


def linux_desktop(launch: Launch, dest: Path, icon_dir: Path, name: str = NAME) -> Path:
    icon = _copy_icon("lamptown.png", icon_dir)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{name.lower().replace(' ', '-')}.desktop"
    path.write_text(desktop_entry(launch, icon, name), encoding="utf-8", newline="\n")
    _make_executable(path)  # GNOME only launches desktop-folder entries that are executable
    return path


# ----------------------------------------------------------------------------- shared
def _copy_icon(name: str, folder: Path) -> Path | None:
    src = asset(name)
    if src is None:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / name
    shutil.copyfile(src, dst)
    return dst


def _make_executable(p: Path) -> None:
    if sys.platform != "win32":
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def default_dest(platform: str | None = None, env: Mapping[str, str] | None = None,
                 home: Path | None = None) -> Path | None:
    """Where a shortcut goes by default (None on Windows: PowerShell asks for the Desktop folder,
    which may be redirected, e.g. into a synced folder)."""
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    home = Path(os.path.expanduser("~")) if home is None else home
    if platform == "win32":
        return None
    if platform == "darwin":
        return home / "Applications"
    data = env.get("XDG_DATA_HOME", "").strip()
    base = Path(data) if data and Path(data).is_absolute() else home / ".local" / "share"
    return base / "applications"


def make_shortcut(
    dest: str | os.PathLike[str] | None = None,
    *,
    config: str | os.PathLike[str] | None = None,
    icon_dir: str | os.PathLike[str] | None = None,
    name: str = NAME,
    extra: Sequence[str] = (),
    platform: str | None = None,
) -> Path:
    """Write the launcher for this OS and return its path."""
    from lamptown.app.paths import app_paths

    platform = sys.platform if platform is None else platform
    launch = launch_for(config, extra, platform=platform)
    where = Path(dest).expanduser() if dest else default_dest(platform)
    icons = Path(icon_dir) if icon_dir else app_paths(platform=platform).icons
    if platform == "win32":
        return windows_shortcut(launch, where, icons, name)
    if platform == "darwin":
        return mac_app(launch, where, name)
    return linux_desktop(launch, where, icons, name)
