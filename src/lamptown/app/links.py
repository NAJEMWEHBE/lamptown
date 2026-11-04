"""Opening links from the town safely: the one door from the page to the operating system.

Links come from data (status files, contest files, job logs), so a link is never trusted:

* ``http:`` and ``https:`` open in the default browser.
* ``file:`` is never run or opened as a file. Lamptown shows where it is instead: the file's
  folder opens with the file selected (Windows ``explorer /select,``, macOS ``open -R``, Linux
  the parent folder through ``xdg-open``). A folder link opens that folder (macOS reveals it,
  because an ``.app`` is a folder too). Programs are started with an argument list, never a shell.
* ``file:`` links that name a host (``file://server/share/...``) or a UNC path are refused: opening
  one would make the system log in to that server.
* Every other scheme is refused.

The page reaches this through the app's JS API (``window.pywebview.api.open_link``): the script
in :data:`LINKS_JS` routes every link click and ``window.open`` there, and pywebview's own
"open new windows in the browser" path (which on Windows hands the URL to ``os.startfile``) is
switched off.
"""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit

TOWN_PAGES = ("town.html", "town-details.html")  # the app's own pages: these navigate in place
MAX_LEN = 2048

# Installed into every page the window loads (with the visibility script, see live.py).
LINKS_JS = """(function () {
  if (window.__lamptownLinks) return; window.__lamptownLinks = 1;
  var PAGES = %s;
  var send = function (href) {
    try { var a = window.pywebview && window.pywebview.api;
          if (a && a.open_link) a.open_link(String(href)); } catch (e) {}
  };
  var abs = function (href) {
    try { return new URL(href, location.href); } catch (e) { return null; }
  };
  var own = function (u) {  /* one of the town's own pages, next to this one */
    if (!u || u.protocol !== location.protocol || u.host !== location.host) return false;
    var here = location.pathname.replace(/[^/]*$/, ''), dir = u.pathname.replace(/[^/]*$/, '');
    var name = decodeURIComponent(u.pathname.slice(dir.length));
    return dir === here && PAGES.indexOf(name) >= 0;
  };
  var onClick = function (e) {
    var t = e.target, a = t && t.closest ? t.closest('a[href]') : null;
    if (!a) return;
    var raw = a.getAttribute('href') || '';
    if (raw.charAt(0) === '#') return;  /* in-page jump */
    var u = abs(a.href);
    e.preventDefault(); e.stopImmediatePropagation();
    if (own(u)) { if (e.type === 'click') location.href = u.href; return; }
    if (u) send(u.href);
  };
  document.addEventListener('click', onClick, true);
  document.addEventListener('auxclick', onClick, true);
  window.open = function (href) {
    var u = abs(href);
    if (u && own(u)) location.href = u.href; else if (u) send(u.href);
    return null;
  };
})();""" % ('["' + '", "'.join(TOWN_PAGES) + '"]')


Spawn = Callable[[Sequence[str]], object]
Browse = Callable[[str], object]


def _spawn(args: Sequence[str]) -> object:
    """Start a helper program from an argument list: no shell, no inherited handles."""
    return subprocess.Popen(list(args), close_fds=True, stdin=subprocess.DEVNULL,  # noqa: S603
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _browse(url: str) -> object:
    return webbrowser.open(url, new=2)


class Refused(ValueError):
    """A link Lamptown will not open (the message says why, for the log)."""


def _clean(href: object) -> str:
    if not isinstance(href, str):
        raise Refused("not text")
    text = href.strip()
    if not text or len(text) > MAX_LEN:
        raise Refused("empty or too long")
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise Refused("control characters")
    return text


def web_url(href: str) -> str | None:
    """``href`` when it is an ``http(s)`` URL with a host, else ``None``."""
    parts = urlsplit(href)
    if parts.scheme.lower() in ("http", "https") and parts.netloc and " " not in href:
        return href
    return None


def local_path(href: str, platform: str | None = None) -> PurePosixPath | PureWindowsPath:
    """The local path a ``file:`` URL names. Raises :class:`Refused` for a host or a UNC path."""
    platform = sys.platform if platform is None else platform
    parts = urlsplit(href)
    if parts.scheme.lower() != "file":
        raise Refused("not a file: link")
    host = parts.netloc
    if host and host.lower() != "localhost":
        raise Refused("file: link with a host (a network share)")
    raw = unquote(parts.path)
    if "\x00" in raw:
        raise Refused("NUL in the path")
    if platform == "win32":
        text = raw.replace("/", "\\")
        if text.startswith("\\") and len(text) > 2 and text[2] == ":":
            text = text[1:]  # drop the slash before a drive letter
        win = PureWindowsPath(text)
        if text.startswith("\\\\") or win.drive.startswith("\\\\") or not win.drive:
            raise Refused("a UNC or relative path")
        if not win.is_absolute():
            raise Refused("not an absolute path")
        return win
    # "/\\host\share" is a UNC path written for Windows: refuse it here too, never guess
    if raw.startswith("//") or not raw.startswith("/") or raw.lstrip("/").startswith("\\"):
        raise Refused("a network or relative path")
    return PurePosixPath(raw)


def reveal_command(path: Path, is_dir: bool, platform: str | None = None) -> list[str]:
    """The argument list that shows ``path`` in the file manager without opening it."""
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        windir = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
        explorer = str(Path(windir) / "explorer.exe")
        return [explorer, str(path)] if is_dir else [explorer, f"/select,{path}"]
    if platform == "darwin":
        return ["/usr/bin/open", "-R", str(path)]  # reveal: never launches, even an .app
    return ["xdg-open", str(path if is_dir else path.parent)]


def open_link(
    href: object,
    *,
    spawn: Spawn | None = None,
    browse: Browse | None = None,
    platform: str | None = None,
    log: Callable[[str], None] | None = None,
) -> str:
    """Open one link from the page by the rules above. Returns what happened, for the page:
    ``"browser"``, ``"folder"`` or ``"refused"``. Never raises."""
    say = log or (lambda _m: None)
    spawn = spawn or _spawn  # looked up per call, so a test can swap the module's openers
    browse = browse or _browse
    try:
        text = _clean(href)
        scheme = urlsplit(text).scheme.lower()
        if scheme in ("http", "https"):
            url = web_url(text)
            if url is None:
                raise Refused("a web link without a host")
            browse(url)
            return "browser"
        if scheme != "file":
            raise Refused(f"the {scheme or 'missing'} scheme is not allowed")
        pure = local_path(text, platform)
        path = Path(os.path.normpath(str(pure)))
        try:
            is_dir = path.is_dir()
            exists = is_dir or path.exists()
        except OSError:
            is_dir = exists = False
        if not exists:
            parent = path.parent
            if parent == path or not parent.is_dir():
                raise Refused("nothing is there")
            path, is_dir = parent, True  # gone: show the folder it was in
        spawn(reveal_command(path, is_dir, platform))
        return "folder"
    except Refused as why:
        say(f"link refused ({why})")
        return "refused"
    except Exception as e:  # a missing file manager, say: log it, never crash the page's call
        say(f"couldn't open a link ({type(e).__name__}: {e})")
        return "refused"
