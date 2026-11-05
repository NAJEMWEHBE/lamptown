"""The app's link opener: web links go to the browser, file links only ever show a folder.

A link in a status file or a contest file must never run a program, whatever it points at. Every
opener the OS offers (``os.startfile``, ``webbrowser.open``, ``subprocess``) is mocked to fail the
test if it is used other than as the rules say.
"""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from lamptown.app import links
from lamptown.app.links import LINKS_JS, local_path, open_link, reveal_command

PROGRAMS = ("probe.bat", "probe.exe", "probe.sh", "probe.lnk", "probe.cmd", "probe.ps1")


@pytest.fixture
def guard(monkeypatch):
    """Record every spawn/browse; any other way of opening something fails the test."""
    calls: dict[str, list] = {"spawn": [], "browse": []}

    def boom(*_a, **_k):
        raise AssertionError("a link reached an opener that can run programs")

    if hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", boom)
    monkeypatch.setattr(webbrowser, "open", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(links, "_spawn", lambda args: calls["spawn"].append(list(args)))
    monkeypatch.setattr(links, "_browse", calls["browse"].append)
    return calls


@pytest.mark.parametrize("name", PROGRAMS)
def test_a_program_link_is_never_run_only_shown_in_its_folder(tmp_path, guard, name):
    target = tmp_path / name
    target.write_text("echo ran > marker.txt\n", encoding="utf-8")
    assert open_link(target.as_uri()) == "folder"
    assert guard["browse"] == []
    (args,) = guard["spawn"]
    if sys.platform == "win32":
        assert Path(args[0]).name.lower() == "explorer.exe"
        assert args[1:] == [f"/select,{target}"]
    elif sys.platform == "darwin":
        assert args == ["/usr/bin/open", "-R", str(target)]
    else:
        assert args == ["xdg-open", str(tmp_path)]
    assert not (tmp_path / "marker.txt").exists()


def test_reveal_commands_never_hand_the_file_itself_to_an_opener():
    exe = PureWindowsPath(r"C:\Users\you\Tools\probe.bat")
    win = reveal_command(exe, False, "win32")
    assert win[0].lower().endswith("explorer.exe")
    assert win[1:] == [r"/select,C:\Users\you\Tools\probe.bat"]
    assert reveal_command(PurePosixPath("/tmp/x.sh"), False, "darwin") == [
        "/usr/bin/open", "-R", "/tmp/x.sh"]
    assert reveal_command(PurePosixPath("/tmp/Paper.app"), True, "darwin") == [
        "/usr/bin/open", "-R", "/tmp/Paper.app"]  # an .app is a folder: reveal, never launch
    assert reveal_command(PurePosixPath("/tmp/x.sh"), False, "linux") == ["xdg-open", "/tmp"]
    assert reveal_command(PurePosixPath("/tmp/kite"), True, "linux") == ["xdg-open", "/tmp/kite"]


def test_a_folder_link_opens_the_folder(tmp_path, guard):
    assert open_link(tmp_path.as_uri()) == "folder"
    (args,) = guard["spawn"]
    if sys.platform == "win32":
        assert args[1:] == [str(tmp_path)]
    elif sys.platform == "darwin":
        assert args[1:] == ["-R", str(tmp_path)]
    else:
        assert args == ["xdg-open", str(tmp_path)]


def test_a_missing_file_shows_the_folder_it_was_in(tmp_path, guard):
    assert open_link((tmp_path / "gone.exe").as_uri()) == "folder"
    (args,) = guard["spawn"]
    assert str(tmp_path) in args[-1] and "gone.exe" not in args[-1]
    assert open_link((tmp_path / "no" / "such" / "x.bat").as_uri()) == "refused"


@pytest.mark.parametrize("href", [
    "file://evil.example/share/probe.bat",
    "file://192.0.2.7/c$/x.exe",
    "file:////evil.example/share/x.lnk",
    "file:///%5C%5Cevil.example%5Cshare%5Cx.bat",
    "file://///evil.example/share/x",
])
def test_network_file_links_are_refused(guard, href):
    """Opening one would make the system log in to that host (and leak a password hash)."""
    assert open_link(href) == "refused"
    assert guard == {"spawn": [], "browse": []}


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_local_path_rules(platform):
    if platform == "win32":
        assert local_path("file:///C:/Users/you/a%20b.txt", "win32") == PureWindowsPath(
            r"C:\Users\you\a b.txt")
        assert local_path("file://localhost/C:/Users/you/x", "win32") == PureWindowsPath(
            r"C:\Users\you\x")
        for bad in ("file:///%5C%5Chost%5Cshare%5Cx", "file:////host/share/x", "file:relative.bat",
                    "file:///Kite/x"):
            with pytest.raises(links.Refused):
                local_path(bad, "win32")
    else:
        assert local_path("file:///home/you/x.sh", platform) == PurePosixPath("/home/you/x.sh")
        for bad in ("file:////host/share/x", "file://host/x", "file:relative.sh"):
            with pytest.raises(links.Refused):
                local_path(bad, platform)


def test_web_links_go_to_the_default_browser(guard):
    assert open_link("https://example.com/pr/12") == "browser"
    assert open_link(" http://example.com/a?b=c ") == "browser"
    assert guard["browse"] == ["https://example.com/pr/12", "http://example.com/a?b=c"]
    assert guard["spawn"] == []


@pytest.mark.parametrize("href", [
    "javascript:alert(1)", "data:text/html,<b>x</b>", "ms-settings:", "search-ms:query=x",
    "vbscript:x", "smb://host/share", "https://", "http:///nohost", "https://ex ample.com",
    "https://example.com/\nx", "", "   ", None, 42, "x" * 5000, "C:\\Users\\you\\probe.bat",
])
def test_everything_else_is_refused(guard, href):
    seen: list[str] = []
    assert open_link(href, log=seen.append) == "refused"
    assert guard == {"spawn": [], "browse": []}
    assert seen and seen[0].startswith("link refused")


def test_a_broken_file_manager_is_logged_not_raised(tmp_path, monkeypatch):
    def fail(_args):
        raise FileNotFoundError("xdg-open")

    seen: list[str] = []
    assert open_link(tmp_path.as_uri(), spawn=fail, log=seen.append) == "refused"
    assert "FileNotFoundError" in seen[0]


def test_the_page_script_routes_links_and_window_open_to_the_api():
    js = LINKS_JS
    assert "open_link" in js and "window.open = function" in js
    assert "addEventListener('click'" in js and "addEventListener('auxclick'" in js
    assert '["town.html", "town-details.html"]' in js and "%s" not in js
    assert "preventDefault" in js and "stopImmediatePropagation" in js


def test_the_window_api_exposes_only_visibility_and_open_link():
    from lamptown.app.window import _Api

    public = sorted(n for n in dir(_Api) if not n.startswith("_"))
    assert public == ["open_link", "visibility"]


def test_the_app_switches_off_pywebviews_own_link_opening():
    src = Path(links.__file__).with_name("window.py").read_text(encoding="utf-8")
    assert 'settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False' in src
    assert 'settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True' not in src


LINK_PAGE = """<!doctype html><html><body>
<a id="web" href="https://example.com/kite">kite</a><pre id="T"></pre>
<script>var sent = [];
window.pywebview = {api: {open_link: function (h) { sent.push(h); }}};</script>
<script>%s</script>
<script>
function fire(type, button) {
  var e = new MouseEvent(type, {bubbles: true, cancelable: true, button: button});
  return {prevented: !document.getElementById('web').dispatchEvent(e)};
}
var r = {right: fire('auxclick', 2), back: fire('auxclick', 3), middle: fire('auxclick', 1),
         click: fire('click', 0)};
r.sent = sent.slice();
document.getElementById('T').textContent = JSON.stringify(r);
</script></body></html>"""


def test_only_a_left_or_middle_click_opens_a_link(tmp_path):
    """In a real headless browser (app job: the runner's Chrome; skipped without one): a right
    click (the context menu) or a back/forward button never opens the link."""
    import json
    import re

    from _browser import dump_dom, headless

    page = tmp_path / "links.html"
    page.write_text(LINK_PAGE % LINKS_JS, encoding="utf-8")
    out = dump_dom(headless(tmp_path / "chrome", "--virtual-time-budget=2000", "--dump-dom",
                            page.as_uri()))
    m = re.search(r'<pre id="T">(.*?)</pre>', out, re.S)
    assert m, out[-1500:]
    r = json.loads(m.group(1))
    assert r["right"] == {"prevented": False} and r["back"] == {"prevented": False}
    assert r["middle"] == {"prevented": True} and r["click"] == {"prevented": True}
    assert r["sent"] == ["https://example.com/kite"] * 2  # the middle click and the click
