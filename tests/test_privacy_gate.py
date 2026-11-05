"""Self-tests for tools/privacy_gate.py with planted, fictional strings.

Planted personal paths and e-mail addresses are assembled at run time, so this file itself
stays clean when the gate scans the repo. The deny-list terms below are invented words.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

GATE_PATH = Path(__file__).resolve().parents[1] / "tools" / "privacy_gate.py"
_spec = importlib.util.spec_from_file_location("privacy_gate", GATE_PATH)
pg = importlib.util.module_from_spec(_spec)
sys.modules["privacy_gate"] = pg
_spec.loader.exec_module(pg)

BS, FS = "\\", "/"
TERM_WORD = "glimmerfen"            # deny-list line 3
TERM_PHRASE = "Zorblax Quentaro"    # deny-list line 4
TERM_PATH = "fakeorg/vault-nine"    # deny-list line 5
TERM_DRIVE = "Q:" + "\\fakedrive\\cellar"   # deny-list line 9: a folder on a drive
TERMS = (TERM_WORD, TERM_PHRASE, TERM_PATH, TERM_DRIVE)
FAKE_HANDLE = "wrenquill77"         # deny-list line 6
FAKE_NOREPLY = "1234+" + FAKE_HANDLE + "@users.noreply.github.com"
DENYLIST_TEXT = "\n".join([
    "# fictional deny-list for the gate's self-tests",
    "",
    TERM_WORD,
    TERM_PHRASE,
    TERM_PATH,
    FAKE_HANDLE,
    "!author: " + FAKE_NOREPLY,
    "!author: " + FAKE_HANDLE,
    TERM_DRIVE,
]) + "\n"


def email(local: str, domain: str) -> str:
    return local + "@" + domain


# ----------------------------------------------------------------------------- fixtures


@pytest.fixture
def root(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "ok.py").write_text('print("hello from a clean file")\n', encoding="utf-8")
    return repo


@pytest.fixture
def deny(tmp_path: Path) -> Path:
    path = tmp_path / "private" / "deny.txt"
    path.parent.mkdir()
    path.write_text(DENYLIST_TEXT, encoding="utf-8")
    return path


@pytest.fixture
def gate(capsys):
    def _run(root: Path, *args: str, env: dict | None = None, stdin_text: str | None = None):
        code = pg.run([*args, "--root", str(root)], env=env or {}, cwd=root,
                      stdin_text=stdin_text)
        cap = capsys.readouterr()
        return code, cap.out + cap.err
    return _run


@pytest.fixture
def git_env(tmp_path: Path, monkeypatch):
    """Isolate git from the developer's own config, hooks and identity."""
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL", "GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)


def git(root: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                         text=True)
    return out.stdout.strip()


def init_repo(root: Path, name: str = "Test Writer",
              mail: str = email("test", "example.com")) -> None:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", name)
    git(root, "config", "user.email", mail)
    git(root, "config", "commit.gpgsign", "false")


def commit_all(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def assert_no_term_leak(output: str) -> None:
    low = output.lower()
    for term in TERMS:
        assert term.lower() not in low
        assert term.lower().replace(" ", "-") not in low
    assert "quentaro" not in low and "vault-nine" not in low
    assert "fakedrive" not in low and "cellar" not in low


# ----------------------------------------------------------------------------- images


def png(*chunks: tuple[bytes, bytes], trailing: bytes = b"") -> bytes:
    def chunk(ctype: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    body = b"".join(chunk(t, d) for t, d in chunks)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + body
            + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b"") + trailing)


def sub_blocks(data: bytes) -> bytes:
    out = b"".join(bytes([len(data[i:i + 255])]) + data[i:i + 255]
                   for i in range(0, len(data), 255))
    return out + b"\x00"


def gif(*extensions: bytes) -> bytes:
    header = b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0x80, 0, 0) + b"\x00" * 6
    image = (b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02"
             + sub_blocks(b"\x4c\x01"))
    return header + b"".join(extensions) + image + b"\x3b"


def gif_comment(text: str) -> bytes:
    return b"\x21\xfe" + sub_blocks(text.encode("latin-1"))


def gif_app(text: str) -> bytes:
    return b"\x21\xff\x0b" + b"XMP DataXMP" + sub_blocks(text.encode("latin-1"))


# ----------------------------------------------------------------------------- content


def test_clean_tree_passes(root, deny, gate):
    code, out = gate(root, "--denylist", str(deny))
    assert code == 0, out
    assert "pass" in out


PLANTED = {
    "windows user folder": "C:" + BS + "Users" + BS + "dana" + BS + "notes.txt",
    "windows user folder, forward slashes": "D:" + FS + "Users" + FS + "dana" + FS + "x",
    "windows user folder, json-escaped": "C:" + BS * 2 + "Users" + BS * 2 + "dana",
    "macOS user folder": FS + "Users" + FS + "dana" + FS + "Library",
    "linux home folder": "cd " + FS + "home" + FS + "dana" + FS + "src",
    "file url to a home folder": "file:" + FS * 3 + "home" + FS + "dana" + FS + "a.txt",
    "drive-letter project path": "E:" + BS + "work" + BS + "secretproject",
    "lower-case drive-letter path": "e:" + BS + "work" + BS + "secretproject",
    "drive-letter path, forward slashes": "E:" + FS + "work" + FS + "secretproject",
    "personal e-mail": "mail " + email("dana.fake", "fakemail.io") + " today",
    "e-mail on a company noreply host": email("dana.fake", "noreply.bigcorp-fake.com"),
    "git bash user folder": FS + "c" + FS + "Users" + FS + "dana" + FS + "notes",
    "wsl user folder": "cd " + FS + "mnt" + FS + "c" + FS + "Users" + FS + "dana",
    "cygwin user folder": FS + "cygdrive" + FS + "c" + FS + "Users" + FS + "dana",
    "fedora atomic home folder": FS + "var" + FS + "home" + FS + "dana" + FS + "x",
    "wsl share home folder": BS * 2 + "wsl$" + BS + "Ubuntu" + BS + "home" + BS + "dana",
    "wsl.localhost home folder": BS * 2 + "wsl.localhost" + BS + "Debian" + BS + "home" + BS
    + "dana",
    "wsl share, json-escaped": BS * 4 + "wsl$" + BS * 2 + "Ubuntu" + BS * 2 + "home" + BS * 2
    + "dana",
    "url-encoded user folder": "?p=C" + "%3A%5CUsers%5Cdana%5Cnotes",
    "url-encoded user folder, slashes": "?p=C" + "%3a%2fUsers%2fdana",
    "url-encoded drive-letter path": "?p=E" + "%3A%5Cwork%5Csecretproject",
    "lower-case drive, forward slashes": "open d:" + FS + "work" + FS + "client",
    "lower-case drive in a file url": "file:" + FS * 3 + "d:" + FS + "work" + FS + "client",
    "lower-case drive in a quoted value": 'dir = "e:' + FS + "clients" + FS + 'acme"',
}


@pytest.mark.parametrize("label", sorted(PLANTED))
def test_planted_path_or_email_fails(label, root, deny, gate):
    (root / "src" / "planted.md").write_text("intro\n\n" + PLANTED[label] + "\n",
                                             encoding="utf-8")
    code, out = gate(root, "--denylist", str(deny))
    assert code == 1, out
    assert "src/planted.md:3:" in out
    assert ("[email]" if "e-mail" in label else "[user-path]") in out
    assert PLANTED[label] not in out  # matched text is only shown with --reveal


def test_reveal_shows_path_snippet(root, gate):
    (root / "a.txt").write_text(PLANTED["macOS user folder"], encoding="utf-8")
    code, out = gate(root, "--reveal")
    assert code == 1
    assert "dana" in out


ALLOWED = [
    "C:" + BS + "Users" + BS + "you" + BS + "lamptown.toml",
    FS + "Users" + FS + "you" + FS + "Library",
    FS + "home" + FS + "you" + FS + ".config",
    FS + "home" + FS + "<name>" + FS + ".config",
    FS + "home" + FS + "$USER" + FS + ".config",
    "C:" + BS + "Users" + BS + "%USERNAME%" + BS + "AppData",
    "C:" + BS + "Program Files" + BS + "Google" + BS + "Chrome",
    "C:" + BS + "Windows" + BS + "System32",
    "~/logs/backup-*.log",
    "https://example.org/home/page and https://example.org/Users/list",
    email("someone", "example.com") + " " + email("a", "mail.example.org"),
    email("x", "host.test") + " " + email("noreply", "vendor.io"),
    "Co-Authored-By: Bot <" + email("noreply", "bots.dev") + ">",
    "x" + TERM_WORD + " " + TERM_WORD + "s " + TERM_WORD.replace("e", "3"),
    "Zorblax only, Quentaro only",
    "re.compile(r'[A-Za-z]:[\\\\/]+') and 'key: value'",
    "const ok = s ? a:/^lot-[0-9]+$/.test(id) : b;",  # minified JS, not a drive path
    "x?a:/re/.test(s)", "{k:/re/gi.test(s)}", "x?a:/re/g.test(s)", "b?a:/x/y.test(a)",
    "f(a,b){return b?a:/x/u/.exec(a)}",
    "open d:" + FS + "work and e:" + FS + "x",   # one segment only: not enough to be a path
    FS + "c" + FS + "Users" + FS + "you" + FS + "notes",
    FS + "mnt" + FS + "c" + FS + "Users" + FS + "you",
    FS + "var" + FS + "home" + FS + "you",
    BS * 2 + "wsl$" + BS + "Ubuntu" + BS + "home" + BS + "you",
    "C%3A%5CUsers%5C%25USERNAME%25",
    FS + "work" + FS + "client" + FS + "notes and " + FS + "work" + FS + "paper-kite",
    "https://example.org/c/Users/list and https://example.org/var/home/x",
    "a" + FS + "c" + FS + "Users" + FS + "list",
    email("kitequill", "users.noreply.github.com") + " " + email("no-reply", "bots.dev"),
]


@pytest.mark.parametrize("text", ALLOWED)
def test_allowed_content_passes(text, root, deny, gate):
    (root / "doc.md").write_text(text + "\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(deny))
    assert code == 0, out


DENY_VARIANTS = {
    "exact word": ("see " + TERM_WORD + " here", 3),
    "upper case": ("SEE " + TERM_WORD.upper(), 3),
    "inside punctuation": ("(" + TERM_WORD + ").", 3),
    "next to underscore": ("load_" + TERM_WORD + "_data()", 3),
    "phrase with hyphen": ("zorblax-quentaro", 4),
    "phrase with underscore": ("ZORBLAX_QUENTARO", 4),
    "phrase across a line break": ("Zorblax\nQuentaro", 4),
    "path term with backslashes": ("fakeorg" + BS + "vault-nine" + BS + "x", 5),
    "path term with doubled backslashes": ("fakeorg" + BS * 2 + "vault_nine", 5),
}


@pytest.mark.parametrize("label", sorted(DENY_VARIANTS))
def test_deny_term_in_content_fails_without_leaking(label, root, deny, gate):
    text, deny_line = DENY_VARIANTS[label]
    (root / "src" / "notes.txt").write_text("line one\n" + text + "\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(deny), "--reveal")
    assert code == 1, out
    assert "src/notes.txt:2: [deny-list]" in out
    assert f"deny-list line {deny_line}" in out
    assert_no_term_leak(out)


def test_deny_term_in_file_name_is_masked(root, deny, gate):
    (root / "docs").mkdir()
    (root / "docs" / f"{TERM_WORD}-plan.md").write_text("clean\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(deny), "--reveal")
    assert code == 1
    assert "docs/[deny-list line 3]-plan.md" in out
    assert_no_term_leak(out)


def test_deny_term_in_folder_name_is_masked(root, deny, gate):
    (root / "Zorblax_Quentaro").mkdir()
    (root / "Zorblax_Quentaro" / "a.txt").write_text("clean\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(deny))
    assert code == 1
    assert_no_term_leak(out)


def test_utf16_and_binary_files_are_scanned(root, deny, gate):
    (root / "a.txt").write_bytes(("x " + TERM_WORD).encode("utf-16"))
    (root / "b.bin").write_bytes(b"\x00\x01" + TERM_PHRASE.encode() + b"\x00")
    code, out = gate(root, "--denylist", str(deny))
    assert code == 1
    assert "a.txt:1: [deny-list]" in out
    assert "b.bin: [deny-list] binary file" in out
    assert_no_term_leak(out)


# ----------------------------------------------------------------------------- images


def add_media(root: Path, name: str, data: bytes) -> str:
    path = root / "docs" / "media" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"docs/media/{name}"


def test_clean_images_in_docs_media_pass(root, deny, gate):
    add_media(root, "hero.gif", gif(gif_comment("made with lamptown")))
    add_media(root, "room.png", png((b"tEXt", b"Software\x00lamptown make_media")))
    code, out = gate(root, "--denylist", str(deny))
    assert code == 0, out


@pytest.mark.parametrize("where", ["src/icon.png", "docs/shot.gif", "tests/data/x.png"])
def test_image_outside_docs_media_fails(where, root, gate):
    path = root / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png())
    code, out = gate(root)
    assert code == 1
    assert f"{where}: [image]" in out


@pytest.mark.parametrize("name", ["photo.jpg", "logo.svg", "pic.webp"])
def test_uninspectable_image_formats_fail(name, root, gate):
    add_media(root, name, b"\xff\xd8\xff\xe0 fake jpeg bytes")
    code, out = gate(root)
    assert code == 1
    assert "use PNG or GIF" in out


def test_image_disguised_as_text_fails(root, gate):
    (root / "notes.txt").write_bytes(png())
    code, out = gate(root)
    assert code == 1
    assert "notes.txt: [image] PNG image data" in out


def itxt(text: str, compressed: bool) -> bytes:
    payload = zlib.compress(text.encode()) if compressed else text.encode()
    return b"Description\x00" + bytes([int(compressed), 0]) + b"en\x00\x00" + payload


PNG_PLANTS = {
    "tEXt": lambda t: png((b"tEXt", b"Author\x00" + t.encode("latin-1"))),
    "zTXt": lambda t: png((b"zTXt", b"Comment\x00\x00" + zlib.compress(t.encode("latin-1")))),
    "iTXt": lambda t: png((b"iTXt", itxt(t, compressed=False))),
    "iTXt compressed": lambda t: png((b"iTXt", itxt(t, compressed=True))),
    "eXIf": lambda t: png((b"eXIf", b"MM\x00\x2a" + t.encode("latin-1"))),
    "trailing data": lambda t: png(trailing=t.encode("latin-1")),
}


@pytest.mark.parametrize("chunk", sorted(PNG_PLANTS))
def test_png_metadata_deny_term_fails(chunk, root, deny, gate):
    rel = add_media(root, "room.png", PNG_PLANTS[chunk]("by " + TERM_PHRASE))
    code, out = gate(root, "--denylist", str(deny))
    assert code == 1, out
    assert f"{rel}: [deny-list] PNG metadata" in out
    assert_no_term_leak(out)


def test_png_metadata_path_fails(root, gate):
    add_media(root, "room.png", PNG_PLANTS["tEXt"](PLANTED["windows user folder"]))
    code, out = gate(root)
    assert code == 1
    assert "[user-path] PNG metadata (tEXt)" in out


@pytest.mark.parametrize("block", ["comment", "application"])
def test_gif_metadata_deny_term_fails(block, root, deny, gate):
    ext = gif_comment if block == "comment" else gif_app
    rel = add_media(root, "hero.gif", gif(ext("<x:xmpmeta>" + TERM_WORD + "</x:xmpmeta>")))
    code, out = gate(root, "--denylist", str(deny))
    assert code == 1, out
    assert f"{rel}: [deny-list] GIF metadata ({block})" in out
    assert_no_term_leak(out)


def test_gif_metadata_email_fails(root, gate):
    add_media(root, "hero.gif", gif(gif_comment(PLANTED["personal e-mail"])))
    code, out = gate(root)
    assert code == 1
    assert "[email] GIF metadata (comment)" in out


@pytest.mark.parametrize(
    "data",
    [png()[:30], png()[:-12], b"GIF89a\x01\x00", gif()[:-6] + b"\x21\xfe\x09ab"],
    ids=["png-truncated-chunk", "png-no-iend", "gif-short-header", "gif-truncated-block"],
)
def test_malformed_images_fail(data, root, gate):
    name = "bad.png" if data.startswith(b"\x89PNG") else "bad.gif"
    add_media(root, name, data)
    code, out = gate(root)
    assert code == 1
    assert "could not be read" in out


# ----------------------------------------------------------------------------- app icons
APP_ASSETS = "src/lamptown/app/assets/"


def ico(*frames: bytes) -> bytes:
    off = 6 + 16 * len(frames)
    head = struct.pack("<HHH", 0, 1, len(frames))
    entries = b""
    for f in frames:
        entries += struct.pack("<BBBBHHII", 1, 1, 0, 0, 1, 32, len(f), off)
        off += len(f)
    return head + entries + b"".join(frames)


def icns(*frames: bytes) -> bytes:
    body = b"".join(b"ic07" + struct.pack(">I", 8 + len(f)) + f for f in frames)
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def add_app_icon(root: Path, name: str, data: bytes) -> str:
    path = root / APP_ASSETS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return APP_ASSETS + name


def test_app_icons_are_allowed(root, deny, gate):
    add_app_icon(root, "lamptown.png", png())
    add_app_icon(root, "lamptown.ico", ico(png(), png()))
    add_app_icon(root, "lamptown.icns", icns(png(), png()))
    code, out = gate(root, "--denylist", str(deny))
    assert code == 0, out


def test_the_real_app_icons_pass(root, deny, gate):
    real = Path(__file__).resolve().parents[1] / APP_ASSETS
    for name in ("lamptown.png", "lamptown.ico", "lamptown.icns"):
        add_app_icon(root, name, (real / name).read_bytes())
    code, out = gate(root, "--denylist", str(deny))
    assert code == 0, out


@pytest.mark.parametrize("rel", [APP_ASSETS + "sub/icon.png", APP_ASSETS + "photo.jpg",
                                 "src/lamptown/app/icon.ico", "src/lamptown/assets/icon.png",
                                 "docs/media/icon.ico"])
def test_app_icon_allowance_is_narrow(rel, root, gate):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ico(png()) if rel.endswith(".ico") else png())
    code, out = gate(root)
    assert code == 1
    assert f"{rel}: [image]" in out


def test_app_icon_metadata_is_checked(root, deny, gate):
    rel = add_app_icon(root, "lamptown.ico",
                       ico(png(), PNG_PLANTS["tEXt"]("by " + TERM_PHRASE)))
    code, out = gate(root, "--denylist", str(deny))
    assert code == 1
    assert f"{rel}: [deny-list] ICO frame 2 PNG metadata (tEXt)" in out
    assert_no_term_leak(out)
    rel = add_app_icon(root, "lamptown.icns", icns(PNG_PLANTS["iTXt"](PLANTED["personal e-mail"])))
    code, out = gate(root, "--denylist", str(deny))
    assert f"{rel}: [email] ICNS frame 1 PNG metadata" in out


@pytest.mark.parametrize(
    ("name", "data"),
    [("lamptown.ico", ico(b"BM" + b"\0" * 40)), ("lamptown.icns", icns(b"\0" * 64)),
     ("lamptown.ico", ico(png())[:-10]), ("lamptown.icns", icns(png()) + b"tail"),
     ("lamptown.png", b"not a png")],
    ids=["ico-bmp-frame", "icns-raw-frame", "ico-truncated", "icns-bad-length", "png-not-png"],
)
def test_app_icons_with_uncheckable_frames_fail(name, data, root, gate):
    add_app_icon(root, name, data)
    code, out = gate(root)
    assert code == 1
    assert "[image]" in out


def test_icon_disguised_as_text_fails(root, gate):
    (root / "notes.txt").write_bytes(ico(png()))
    code, out = gate(root)
    assert code == 1
    assert "notes.txt: [image] ICO image data" in out


# ----------------------------------------------------------------------------- deny-list setup


def test_denylist_inside_repo_is_refused(root, gate):
    inside = root / "deny.txt"
    inside.write_text(TERM_WORD + "\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(inside))
    assert code == 2
    assert "inside the repo" in out
    assert_no_term_leak(out)


def test_denylist_from_environment(root, deny, gate):
    (root / "a.txt").write_text(TERM_WORD, encoding="utf-8")
    code, out = gate(root, env={pg.ENV_DENYLIST: str(deny)})
    assert code == 1
    assert "[deny-list]" in out


def test_missing_denylist_skips_only_that_check(root, gate):
    (root / "a.txt").write_text(TERM_WORD, encoding="utf-8")
    code, out = gate(root)
    assert code == 0
    assert "deny-list check is skipped" in out
    (root / "b.txt").write_text(PLANTED["linux home folder"], encoding="utf-8")
    code, out = gate(root)
    assert code == 1
    assert "[user-path]" in out


def test_require_denylist(root, gate, tmp_path):
    assert gate(root, "--require-denylist")[0] == 2
    empty = tmp_path / "empty-list.txt"
    empty.write_text("# nothing\n", encoding="utf-8")
    assert gate(root, "--require-denylist", "--denylist", str(empty))[0] == 2


def test_missing_denylist_file_is_an_error(root, gate, tmp_path):
    assert gate(root, "--denylist", str(tmp_path / "nope.txt"))[0] == 2


def test_unknown_directive_is_an_error(root, gate, tmp_path):
    bad = tmp_path / "bad-list.txt"
    bad.write_text("!allow: " + TERM_WORD + "\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(bad))
    assert code == 2
    assert_no_term_leak(out)


def test_only_named_files_are_scanned(root, deny, gate):
    (root / "dirty.txt").write_text(TERM_WORD, encoding="utf-8")
    assert gate(root, "--denylist", str(deny), "src/ok.py")[0] == 0
    assert gate(root, "--denylist", str(deny), "dirty.txt")[0] == 1


# ----------------------------------------------------------------------------- git


def test_git_tree_uses_gitignore(root, deny, gate, git_env):
    init_repo(root)
    (root / ".gitignore").write_text("_scratch/\n", encoding="utf-8")
    (root / "_scratch").mkdir()
    (root / "_scratch" / "local.txt").write_text(TERM_WORD, encoding="utf-8")
    assert gate(root, "--denylist", str(deny))[0] == 0
    (root / "untracked.txt").write_text(TERM_WORD, encoding="utf-8")
    assert gate(root, "--denylist", str(deny))[0] == 1


def test_commit_message_in_history_fails(root, deny, gate, git_env):
    init_repo(root)
    sha = commit_all(root, "Add notes for the " + TERM_PHRASE + " launch")
    code, out = gate(root, "--denylist", str(deny), "--history")
    assert code == 1
    assert f"commit {sha[:10]} message:1: [deny-list]" in out
    assert_no_term_leak(out)


def test_commit_message_email_and_path_fail(root, gate, git_env):
    init_repo(root)
    commit_all(root, "Fix\n\nReported-by: " + email("dana.fake", "fakemail.io"))
    code, out = gate(root, "--history")
    assert code == 1
    assert "message:3: [email]" in out


@pytest.mark.parametrize("who", ["name", "email"])
def test_commit_author_fails(who, root, deny, gate, git_env):
    if who == "name":
        init_repo(root, name="Glimmerfen Dev")
        expect = "[deny-list] author name"
    else:
        init_repo(root, mail=email("dana.fake", "fakemail.io"))
        expect = "[email] author e-mail is not a noreply"
    commit_all(root, "Initial skeleton")
    code, out = gate(root, "--denylist", str(deny), "--history")
    assert code == 1, out
    assert expect in out
    assert_no_term_leak(out)


def test_allowed_author_identity_passes(root, deny, gate, git_env):
    init_repo(root, name=FAKE_HANDLE, mail=FAKE_NOREPLY)
    commit_all(root, "Initial skeleton")
    code, out = gate(root, "--denylist", str(deny), "--history")
    assert code == 0, out
    (root / "a.txt").write_text("thanks " + FAKE_HANDLE, encoding="utf-8")
    code, out = gate(root, "--denylist", str(deny))
    assert code == 1  # the identity allowance never covers file contents


def test_branch_name_fails(root, deny, gate, git_env):
    init_repo(root)
    commit_all(root, "Initial skeleton")
    git(root, "branch", "client-" + TERM_WORD)
    code, out = gate(root, "--denylist", str(deny), "--history")
    assert code == 1
    assert "ref name: [deny-list]" in out
    assert_no_term_leak(out)


def test_deleted_file_still_caught_in_history(root, deny, gate, git_env):
    init_repo(root)
    (root / "leak.txt").write_text("old note: " + TERM_WORD, encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "shot.png").write_bytes(png())
    commit_all(root, "Add files")
    (root / "leak.txt").unlink()
    (root / "docs" / "shot.png").unlink()
    commit_all(root, "Remove files")
    assert gate(root, "--denylist", str(deny))[0] == 0
    code, out = gate(root, "--denylist", str(deny), "--history")
    assert code == 1
    assert "leak.txt (blob " in out
    assert "docs/shot.png: [image]" in out
    assert_no_term_leak(out)


def test_commit_msg_mode(root, deny, gate, git_env, tmp_path):
    init_repo(root)
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("Add the room\n# " + TERM_WORD + " in a git comment is ignored\n",
                   encoding="utf-8")
    assert gate(root, "--denylist", str(deny), "--commit-msg", str(msg))[0] == 0
    msg.write_text("Add the " + TERM_WORD + " room\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(deny), "--commit-msg", str(msg))
    assert code == 1
    assert "commit message:1: [deny-list]" in out
    msg.write_text("Add the room\n# Please enter the commit message\n"
                   "# ------------------------ >8 ------------------------\n"
                   "-removed line about " + TERM_WORD + "\n", encoding="utf-8")
    assert gate(root, "--denylist", str(deny), "--commit-msg", str(msg))[0] == 0
    msg.write_text("Title\n\n# a comment\nBody about " + TERM_WORD + "\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(deny), "--commit-msg", str(msg))
    assert "commit message:4: [deny-list]" in out
    assert gate(root, "--commit-msg", str(tmp_path / "missing-msg"))[0] == 2
    git(root, "config", "user.email", email("dana.fake", "fakemail.io"))
    msg.write_text("Add the room\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(deny), "--commit-msg", str(msg))
    assert code == 1
    assert "author e-mail" in out
    assert_no_term_leak(out)


def test_pre_push_from_git_stdin(root, deny, gate, git_env):
    init_repo(root)
    clean = commit_all(root, "Initial skeleton")
    stdin = f"refs/heads/main {clean} refs/heads/main {pg.ZERO_SHA}\n"
    assert gate(root, "--denylist", str(deny), "--pre-push", "origin", stdin_text=stdin)[0] == 0
    (root / "a.txt").write_text(TERM_WORD, encoding="utf-8")
    dirty = commit_all(root, "Add a note")
    stdin = f"refs/heads/main {dirty} refs/heads/main {clean}\n"
    code, out = gate(root, "--denylist", str(deny), "--pre-push", "origin", stdin_text=stdin)
    assert code == 1
    assert "a.txt (blob " in out
    deleting = f"(delete) {pg.ZERO_SHA} refs/heads/old {clean}\n"
    assert gate(root, "--denylist", str(deny), "--pre-push", "origin",
                stdin_text=deleting)[0] == 0


def test_pre_push_skips_commits_the_remote_has(root, deny, gate, git_env, tmp_path):
    init_repo(root)
    (root / "a.txt").write_text(TERM_WORD, encoding="utf-8")
    old = commit_all(root, "Old commit")
    git(root, "update-ref", "refs/remotes/origin/main", old)
    (root / "a.txt").write_text("cleaned\n", encoding="utf-8")
    new = commit_all(root, "Clean up")
    stdin = f"refs/heads/main {new} refs/heads/main {old}\n"
    assert gate(root, "--denylist", str(deny), "--pre-push", "origin", stdin_text=stdin)[0] == 0


def test_pre_push_from_pre_commit_env(root, deny, gate, git_env):
    init_repo(root)
    sha = commit_all(root, "Mention " + TERM_WORD)
    env = {"PRE_COMMIT_TO_REF": sha, "PRE_COMMIT_REMOTE_NAME": "origin"}
    code, out = gate(root, "--denylist", str(deny), "--pre-push", env=env)
    assert code == 1
    assert "[deny-list]" in out
    assert_no_term_leak(out)


def test_history_on_empty_repo_passes(root, deny, gate, git_env):
    init_repo(root)
    assert gate(root, "--denylist", str(deny), "--history")[0] == 0


# ----------------------------------------------------------------------------- 0.2.1 review fixes
# Commit identities: the e-mail field is an allow-list (noreply, example and reserved addresses).

BAD_IDENTITY_EMAILS = {
    "login at a PC name": "dana@" + "DANAPC",
    "git's unknown-host form": "dana@" + "danapc.(none)",
    "bare login": "dana",
    "localhost": "dana@" + "localhost",
    "localhost subdomain": "dana@" + "danapc.localhost",
    "company noreply host": email("dana.fake", "noreply.bigcorp-fake.com"),
    "personal mailbox": email("dana.fake", "fakemail.io"),
    "address inside angle brackets": "<" + email("dana.fake", "fakemail.io") + ">",
}
GOOD_IDENTITY_EMAILS = [
    FAKE_NOREPLY,
    "41898341+github-actions[bot]@users.noreply.github.com",  # .github/workflows/media.yml
    "49699333+dependabot[bot]@users.noreply.github.com",
    email(FAKE_HANDLE, "users.noreply.github.com"),
    email("noreply", "github.com"),
    email("no-reply", "vendor.io"),
    email("dana", "example.com"),
    email("dana", "dev.example.org"),
    email("dana", "host.invalid"),
    email("dana", "pc.test"),
    email("dana", "box.example"),
    "",  # no e-mail at all: nothing to leak (lightweight or old tags have no tagger)
]


@pytest.mark.parametrize("label", sorted(BAD_IDENTITY_EMAILS))
def test_identity_email_outside_the_allow_list_fails(label):
    scanner = pg.Scanner(None)
    scanner.scan_identity(BAD_IDENTITY_EMAILS[label], "commit identity", "author e-mail")
    assert [h.check for h in scanner.hits] == ["email"], label


@pytest.mark.parametrize("value", GOOD_IDENTITY_EMAILS)
def test_identity_email_on_the_allow_list_passes(value):
    scanner = pg.Scanner(None)
    scanner.scan_identity(value, "commit identity", "author e-mail")
    assert scanner.hits == []


def test_identity_names_are_not_e_mail_checked():
    scanner = pg.Scanner(None)
    scanner.scan_identity("Dana", "commit identity", "author name")
    assert scanner.hits == []


def test_dotless_author_e_mail_in_history_fails(root, gate, git_env):
    init_repo(root, mail="dana@" + "DANAPC")
    sha = commit_all(root, "Initial skeleton")
    code, out = gate(root, "--history", "--no-tree")
    assert code == 1, out
    assert f"commit {sha[:10]}: [email] author e-mail" in out
    assert "DANAPC" not in out


def test_dotless_identity_in_commit_msg_mode_fails(root, gate, git_env, tmp_path):
    init_repo(root, mail="dana@" + "danapc.(none)")
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("Add the room\n", encoding="utf-8")
    code, out = gate(root, "--commit-msg", str(msg))
    assert code == 1, out
    assert "author e-mail" in out and "committer e-mail" in out


# File names get the same path and e-mail checks as file contents, without showing the match.

def test_e_mail_in_a_file_name_fails_and_is_masked(root, gate):
    name = email("wren", "fictional-mail.org") + ".md"
    (root / "docs").mkdir()
    (root / "docs" / name).write_text("clean\n", encoding="utf-8")
    code, out = gate(root)
    assert code == 1, out
    assert "[email] file name" in out
    assert "fictional-mail" not in out


def test_e_mail_in_a_deleted_file_name_fails_in_history(root, gate, git_env):
    init_repo(root)
    name = email("wren", "fictional-mail.org") + ".txt"
    (root / name).write_text("clean\n", encoding="utf-8")
    commit_all(root, "Add a note")
    (root / name).unlink()
    commit_all(root, "Remove the note")
    assert gate(root)[0] == 0
    code, out = gate(root, "--history", "--no-tree")
    assert code == 1, out
    assert "[email] file name" in out
    assert "fictional-mail" not in out


def test_deny_term_in_file_name_still_reports_its_line(root, deny, gate):
    (root / (TERM_WORD + ".txt")).write_text("clean\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(deny))
    assert code == 1
    assert "[deny-list] file name: deny-list term (deny-list line 3)" in out
    assert_no_term_leak(out)


def test_the_repo_tree_still_passes(gate):
    code, out = gate(GATE_PATH.parents[1])
    assert code == 0, out


# Pre-push: annotated tags (tagger, message, nested tags) and the pushed ref names.

def tag(root: Path, name: str, target: str, message: str, tagger_mail: str | None = None) -> str:
    pre = ["-c", "user.email=" + tagger_mail] if tagger_mail else []
    git(root, *pre, "tag", "-a", name, target, "-m", message)
    return git(root, "rev-parse", name)


def test_pre_push_checks_the_annotated_tag_tagger_and_message(root, gate, git_env):
    init_repo(root)
    commit_all(root, "Initial skeleton")
    sha = tag(root, "v1", "HEAD", "release by " + email("dana.fake", "fakemail.io"),
              tagger_mail=email("dana.fake", "fakemail.io"))
    stdin = f"refs/tags/v1 {sha} refs/tags/v1 {pg.ZERO_SHA}\n"
    code, out = gate(root, "--pre-push", "origin", stdin_text=stdin)
    assert code == 1, out
    assert "annotated tag: [email] tagger e-mail" in out
    assert "annotated tag message:1: [email]" in out


def test_pre_push_checks_a_tag_of_a_tag(root, gate, git_env):
    init_repo(root)
    commit_all(root, "Initial skeleton")
    tag(root, "inner", "HEAD", "inner", tagger_mail="dana@" + "DANAPC")
    outer = tag(root, "outer", "inner", "outer")
    stdin = f"refs/tags/outer {outer} refs/tags/outer {pg.ZERO_SHA}\n"
    code, out = gate(root, "--pre-push", "origin", stdin_text=stdin)
    assert code == 1, out
    assert "tagger e-mail" in out


def test_pre_push_clean_annotated_tag_passes(root, gate, git_env):
    init_repo(root)
    commit_all(root, "Initial skeleton")
    sha = tag(root, "v1", "HEAD", "Lamptown 1.0")
    stdin = f"refs/tags/v1 {sha} refs/tags/v1 {pg.ZERO_SHA}\n"
    code, out = gate(root, "--pre-push", "origin", stdin_text=stdin)
    assert code == 0, out


@pytest.mark.parametrize("side", ["local", "remote"])
def test_pre_push_checks_ref_names(side, root, deny, gate, git_env):
    init_repo(root)
    sha = commit_all(root, "Initial skeleton")
    bad = "refs/heads/client-" + TERM_WORD
    local, remote = (bad, "refs/heads/main") if side == "local" else ("refs/heads/main", bad)
    stdin = f"{local} {sha} {remote} {pg.ZERO_SHA}\n"
    code, out = gate(root, "--denylist", str(deny), "--pre-push", "origin", stdin_text=stdin)
    assert code == 1, out
    assert "ref name: [deny-list]" in out
    assert_no_term_leak(out)


def test_pre_push_can_delete_a_badly_named_branch(root, deny, gate, git_env):
    init_repo(root)
    sha = commit_all(root, "Initial skeleton")
    stdin = f"(delete) {pg.ZERO_SHA} refs/heads/client-{TERM_WORD} {sha}\n"
    code, out = gate(root, "--denylist", str(deny), "--pre-push", "origin", stdin_text=stdin)
    assert code == 0, out


def test_pre_push_from_pre_commit_env_checks_tag_and_branch_names(root, deny, gate, git_env):
    init_repo(root)
    commit_all(root, "Initial skeleton")
    sha = tag(root, "v1", "HEAD", "Lamptown 1.0", tagger_mail="dana@" + "DANAPC")
    env = {"PRE_COMMIT_TO_REF": sha, "PRE_COMMIT_REMOTE_NAME": "origin",
           "PRE_COMMIT_LOCAL_BRANCH": "refs/tags/" + TERM_WORD,
           "PRE_COMMIT_REMOTE_BRANCH": "refs/tags/v1"}
    code, out = gate(root, "--denylist", str(deny), "--pre-push", env=env)
    assert code == 1, out
    assert "tagger e-mail" in out
    assert "ref name: [deny-list]" in out
    assert_no_term_leak(out)


def test_history_checks_nested_tags(root, gate, git_env):
    init_repo(root)
    commit_all(root, "Initial skeleton")
    tag(root, "inner", "HEAD", "inner", tagger_mail="dana@" + "DANAPC")
    tag(root, "outer", "inner", "outer")
    git(root, "tag", "-d", "inner")
    code, out = gate(root, "--history", "--no-tree")
    assert code == 1, out
    assert "tagger e-mail" in out


# Deny-list setup: a list with no terms says so.

def test_empty_denylist_prints_a_notice(root, gate, tmp_path):
    empty = tmp_path / "empty-list.txt"
    empty.write_text("# nothing yet\n!author: " + FAKE_HANDLE + "\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(empty))
    assert code == 0
    assert "deny-list has no terms" in out


# --staged: the index version of each added or changed file (what the commit will hold).

def test_staged_scans_the_index_not_the_working_tree(root, gate, git_env):
    init_repo(root)
    commit_all(root, "Initial skeleton")
    (root / "a.txt").write_text(PLANTED["personal e-mail"] + "\n", encoding="utf-8")
    git(root, "add", "a.txt")
    (root / "a.txt").write_text("cleaned in the working tree only\n", encoding="utf-8")
    code, out = gate(root, "--staged")
    assert code == 1, out
    assert "a.txt (staged):1: [email]" in out
    git(root, "add", "a.txt")
    (root / "a.txt").write_text(PLANTED["personal e-mail"] + "\n", encoding="utf-8")
    code, out = gate(root, "--staged")
    assert code == 0, out


def test_staged_checks_names_images_and_the_first_commit(root, gate, git_env):
    init_repo(root)
    (root / "docs").mkdir()
    (root / "docs" / "shot.png").write_bytes(png())
    (root / (email("wren", "fictional-mail.org") + ".txt")).write_text("x\n", encoding="utf-8")
    git(root, "add", "-A")
    code, out = gate(root, "--staged")
    assert code == 1, out
    assert "docs/shot.png: [image]" in out
    assert "[email] file name" in out


def test_staged_ignores_deleted_files(root, gate, git_env):
    init_repo(root)
    (root / "old.txt").write_text("clean\n", encoding="utf-8")
    commit_all(root, "Add")
    git(root, "rm", "-q", "old.txt")
    assert gate(root, "--staged")[0] == 0


# Baseline: reviewed fictional hits in blobs that are already public (history can't change).

FIXTURE = "cases\n" + PLANTED["git bash user folder"] + "\n" + PLANTED["wsl user folder"] + "\n"


def write_baseline(root: Path, *entries: str) -> None:
    path = root / pg.BASELINE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# reviewed fixtures\n" + "".join(e + "\n" for e in entries),
                    encoding="utf-8")


def blob_of(root: Path, rel: str) -> str:
    return git(root, "hash-object", rel)


def publish(root: Path, rev: str = "HEAD") -> None:
    """Make rev look already public: the remote-tracking origin/main the baseline trusts."""
    git(root, "update-ref", "refs/remotes/origin/main", rev)


def test_baseline_covers_only_the_listed_blob_line_and_check(root, gate, git_env):
    init_repo(root)
    (root / "fixture.txt").write_text(FIXTURE, encoding="utf-8")
    commit_all(root, "Add fixture")
    publish(root)
    blob = blob_of(root, "fixture.txt")
    code, out = gate(root, "--history", "--suggest-baseline")
    assert code == 1, out
    assert f"{blob} 2 user-path" in out and f"{blob} 3 user-path" in out
    write_baseline(root, f"{blob} 2 user-path  # fixture.txt", f"{blob} 3 email")
    code, out = gate(root, "--history")
    assert code == 1, out          # line 3 is listed for the wrong check
    assert "fixture.txt:3: [user-path]" in out and "fixture.txt:2:" not in out
    write_baseline(root, f"{blob} 2 user-path", f"{blob} 3 user-path")
    code, out = gate(root, "--history")
    assert code == 0, out
    assert "reviewed fixture hits skipped" in out


def test_baseline_never_covers_changed_content(root, gate, git_env):
    init_repo(root)
    (root / "fixture.txt").write_text(FIXTURE, encoding="utf-8")
    commit_all(root, "Add fixture")
    publish(root)
    blob = blob_of(root, "fixture.txt")
    write_baseline(root, f"{blob} 2 user-path", f"{blob} 3 user-path")
    assert gate(root)[0] == 0
    (root / "fixture.txt").write_text(FIXTURE + "\n", encoding="utf-8")  # one byte more
    code, out = gate(root)
    assert code == 1, out
    (root / "fixture.txt").write_bytes(FIXTURE.replace("\n", "\r\n").encode())  # CRLF checkout
    assert gate(root)[0] == 0


def test_baseline_never_covers_deny_list_hits_or_file_names(root, deny, gate, git_env):
    (root / "fixture.txt").write_text("cases\n" + TERM_WORD + "\n", encoding="utf-8")
    blob = blob_of(root, "fixture.txt")
    write_baseline(root, f"{blob} 2 deny-list")
    code, out = gate(root, "--denylist", str(deny))
    assert code == 2, out              # deny-list entries are refused outright
    (root / "fixture.txt").unlink()
    name = email("wren", "fictional-mail.org") + ".txt"
    (root / name).write_text("", encoding="utf-8")
    write_baseline(root, f"{blob_of(root, name)} - email")
    code, out = gate(root)
    assert code == 1, out              # a file name is not file content
    assert "[email] file name" in out


def test_malformed_baseline_is_a_setup_error(root, gate):
    write_baseline(root, "not-a-sha 2 user-path")
    code, out = gate(root)
    assert code == 2
    assert "privacy_gate_baseline.txt line 2" in out


def test_the_real_baseline_lists_only_public_blobs():
    """Every entry must name a blob that exists in this repo's history (no stale entries)."""
    repo = GATE_PATH.parents[1]
    entries = pg.load_baseline(repo)
    if not entries or not (repo / ".git").exists():
        pytest.skip("no baseline or not a git checkout")
    shas = sorted({e[0] for e in entries})
    out = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch-check"], check=True,
                         capture_output=True, text=True, input="\n".join(shas) + "\n").stdout
    assert "missing" not in out, out
    # ... and, where origin/main is fetched, one already public there (else it is ignored).
    if pg.resolve_commit(repo, pg.DEFAULT_BASELINE_BASE):
        assert pg.public_baseline(repo, entries, None) == entries


# ----------------------------------------------------------------------------- tools/hooks
# The maintainer's clone runs `git config core.hooksPath tools/hooks`, which makes git skip the
# hooks `pre-commit install` would set up. These tests drive real git commands through the hooks.

HOOKS_DIR = GATE_PATH.parent / "hooks"


@pytest.fixture
def hooked(tmp_path: Path, git_env, monkeypatch) -> Path:
    repo = tmp_path / "hooked"
    (repo / "tools" / "hooks").mkdir(parents=True)
    shutil.copy2(GATE_PATH, repo / "tools" / "privacy_gate.py")
    for name in ("pre-commit", "commit-msg", "pre-push"):
        shutil.copy2(HOOKS_DIR / name, repo / "tools" / "hooks" / name)
        (repo / "tools" / "hooks" / name).chmod(0o755)
    init_repo(repo)
    (repo / ".git" / "info" / "exclude").write_text("tools/\n.pre-commit-config.yaml\n",
                                                    encoding="utf-8")
    git(repo, "config", "core.hooksPath", "tools/hooks")
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep
                       + os.environ.get("PATH", ""))
    for var in ("LAMPTOWN_DENYLIST", "LAMPTOWN_NO_DENYLIST"):
        monkeypatch.delenv(var, raising=False)
    return repo


def run_git(repo: Path, *args: str, env: dict | None = None) -> tuple[int, str]:
    proc = subprocess.run(["git", "-C", str(repo), *args], check=False, capture_output=True,
                          text=True, env={**os.environ, **(env or {})}, timeout=120)
    return proc.returncode, proc.stdout + proc.stderr


def test_hook_pre_commit_blocks_a_staged_leak(hooked):
    (hooked / "a.txt").write_text(PLANTED["personal e-mail"] + "\n", encoding="utf-8")
    git(hooked, "add", "a.txt")
    (hooked / "a.txt").write_text("cleaned, but not staged\n", encoding="utf-8")
    code, out = run_git(hooked, "commit", "-q", "-m", "Add a note")
    assert code != 0, out
    assert "privacy gate: FAIL" in out
    git(hooked, "add", "a.txt")
    code, out = run_git(hooked, "commit", "-q", "-m", "Add a note")
    assert code == 0, out


def test_hook_commit_msg_blocks_message_and_identity(hooked):
    (hooked / "a.txt").write_text("clean\n", encoding="utf-8")
    git(hooked, "add", "a.txt")
    code, out = run_git(hooked, "commit", "-q", "-m",
                        "Thanks " + email("dana.fake", "fakemail.io"))
    assert code != 0, out
    assert "commit message:1: [email]" in out
    code, out = run_git(hooked, "-c", "user.email=dana@" + "DANAPC", "commit", "-q", "-m", "Add")
    assert code != 0, out
    assert "author e-mail" in out


def test_hook_pre_push_requires_the_denylist(hooked, deny, tmp_path):
    (hooked / "a.txt").write_text("clean\n", encoding="utf-8")
    commit_all(hooked, "Add a note")
    code, out = run_git(hooked, "push", "-q", "origin", "main")
    assert code != 0, out
    assert "LAMPTOWN_NO_DENYLIST" in out
    empty = tmp_path / "empty-list.txt"
    empty.write_text("# nothing\n", encoding="utf-8")
    code, out = run_git(hooked, "push", "-q", "origin", "main",
                        env={"LAMPTOWN_DENYLIST": str(empty)})
    assert code != 0, out
    code, out = run_git(hooked, "push", "-q", "origin", "main",
                        env={"LAMPTOWN_DENYLIST": str(deny)})
    assert code == 0, out
    (hooked / "b.txt").write_text("clean\n", encoding="utf-8")
    commit_all(hooked, "Add another note")
    code, out = run_git(hooked, "push", "-q", "origin", "main", env={"LAMPTOWN_NO_DENYLIST": "1"})
    assert code == 0, out
    assert "deny-list check is skipped" in out


def test_hook_pre_push_blocks_a_leaky_tag(hooked):
    (hooked / "a.txt").write_text("clean\n", encoding="utf-8")
    commit_all(hooked, "Add a note")
    tag(hooked, "v1", "HEAD", "v1", tagger_mail="dana@" + "DANAPC")
    code, out = run_git(hooked, "push", "-q", "origin", "v1", env={"LAMPTOWN_NO_DENYLIST": "1"})
    assert code != 0, out
    assert "tagger e-mail" in out


def test_hooks_hand_over_to_pre_commit_when_it_is_set_up(hooked, tmp_path):
    """With pre-commit installed and configured, the hooks run its stages (gitleaks included)."""
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    log = tmp_path / "pre-commit-calls.txt"
    fake = fake_bin / "pre-commit"
    fake.write_text('#!/bin/sh\necho "$*" >> "$FAKE_PRE_COMMIT_LOG"\nexit 0\n',
                    encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    (hooked / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    env = {"PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
           "FAKE_PRE_COMMIT_LOG": str(log)}
    (hooked / "a.txt").write_text(PLANTED["personal e-mail"] + "\n", encoding="utf-8")
    git(hooked, "add", "a.txt")
    code, out = run_git(hooked, "commit", "-q", "-m", "Add", env=env)
    assert code == 0, out  # the fake pre-commit passes everything
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls[0] == "run --hook-stage pre-commit"
    assert calls[1].startswith("run --hook-stage commit-msg --commit-msg-filename ")


# ----------------------------------------------------------------------------- 0.2.1 round 2 (R2c)
# More spellings of the same personal path: a Git Bash or WSL drive folder, a network share and a
# macOS volume. Deny-list folder entries match every spelling of their folder.

PLANTED_R2 = {
    "wsl drive folder": "cd " + FS + "mnt" + FS + "f" + FS + "work" + FS + "clientco",
    "wsl drive folder, one segment": FS + "mnt" + FS + "e" + FS + "clientco",
    "cygwin drive folder": FS + "cygdrive" + FS + "d" + FS + "clientco",
    "network share user folder": BS * 2 + "KITEPC" + BS + "Users" + BS + "dana" + BS + "x",
    "network share drive share": BS * 2 + "KITEPC" + BS + "C$" + BS + "Users" + BS + "dana",
    "network share by address": BS * 2 + "10.0.0.7" + BS + "home" + BS + "dana",
    "network share, json-escaped": BS * 4 + "KITEPC" + BS * 2 + "Users" + BS * 2 + "dana",
    "network share, url-encoded": "%5C" + "%5CKITEPC%5CUsers%5C" + "dana",
    "macOS volume user folder": FS + "Volumes" + FS + "Backup" + FS + "Users" + FS + "dana",
    "macOS system volume": FS + "System" + FS + "Volumes" + FS + "Data" + FS + "Users" + FS
    + "dana",
    "macOS volume with a space": FS + "Volumes" + FS + "Backup Disk" + FS + "Users" + FS + "dana",
}


@pytest.mark.parametrize("label", sorted(PLANTED_R2))
def test_more_path_spellings_fail(label, root, gate):
    (root / "src" / "planted.md").write_text("intro\n\n" + PLANTED_R2[label] + "\n",
                                             encoding="utf-8")
    code, out = gate(root)
    assert code == 1, out
    assert "src/planted.md:3: [user-path]" in out
    assert PLANTED_R2[label] not in out


ALLOWED_R2 = [
    "#!" + FS + "usr" + FS + "bin" + FS + "env python3",
    "see " + FS + "etc" + FS + "x and " + FS + "tmp" + FS + "x and " + FS + "usr" + FS + "bin",
    'route = "' + FS + "a" + FS + 'b"',
    "if p == " + FS + "a" + FS + "b:",
    FS + "c" + FS + "Windows" + FS + "System32" + FS + "drivers",
    FS + "mnt" + FS + "c" + FS + "Program Files" + FS + "Git",
    FS + "mnt" + FS + "data" + FS + "backups",
    "https://example.org/f/work/clientco and https://example.org/mnt/f/work",
    "sed -e s" + FS + "a" + FS + "b" + FS + "g",
    "x?a:/b/c/.test(s)",
    "." + FS + "f" + FS + "work" + FS + "notes and ~" + FS + "f" + FS + "work" + FS + "notes",
    BS * 2 + "KITEPC" + BS + "Users" + BS + "you" + BS + "x",
    BS * 2 + "KITEPC" + BS + "shared" + BS + "music",
    FS + "Volumes" + FS + "Backup" + FS + "Users" + FS + "you",
    FS + "Volumes" + FS + "Backup" + FS + "music",
    "//cdn.example.org/Users/list",
    # A bare Git Bash drive folder reads like a short Unix path in code; the deny-list catches
    # the owner's own folders in that spelling (test_deny_listed_folder_matches_every_spelling).
    'home=Path("' + FS + 'h"); ' + FS + "h" + FS + "Library" + FS + "Logs" + FS + "app.log",
    FS + "f" + FS + "work" + FS + "notes",
]


@pytest.mark.parametrize("text", ALLOWED_R2)
def test_ordinary_unix_paths_stay_clean(text, root, deny, gate):
    (root / "doc.md").write_text(text + "\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(deny))
    assert code == 0, out


DRIVE_SPELLINGS = {
    "windows": "Q:" + BS + "fakedrive" + BS + "cellar" + BS + "x",
    "forward slashes": "Q:" + FS + "fakedrive" + FS + "cellar",
    "lower-case drive": "q:" + FS + "fakedrive" + FS + "cellar",
    "json-escaped": "Q:" + BS * 2 + "fakedrive" + BS * 2 + "cellar",
    "git bash": FS + "q" + FS + "fakedrive" + FS + "cellar" + FS + "x",
    "wsl": FS + "mnt" + FS + "q" + FS + "fakedrive" + FS + "cellar",
    "cygwin": FS + "cygdrive" + FS + "q" + FS + "fakedrive" + FS + "cellar",
    "url-encoded": "?p=Q" + "%3A%5Cfakedrive%5Ccellar",
    "url-encoded, slashes": "?p=q" + "%3a%2ffakedrive%2fcellar",
}


@pytest.mark.parametrize("label", sorted(DRIVE_SPELLINGS))
def test_deny_listed_folder_matches_every_spelling(label, root, deny, gate):
    (root / "notes.txt").write_text("one\n" + DRIVE_SPELLINGS[label] + "\n", encoding="utf-8")
    code, out = gate(root, "--denylist", str(deny), "--reveal")
    assert code == 1, out
    assert "notes.txt:2: [deny-list] deny-list term (deny-list line 9)" in out
    assert_no_term_leak(out)


@pytest.mark.parametrize("entry", ["/q/fakedrive/cellar", "/mnt/q" + "/fakedrive/cellar",
                                   "q:" + "/fakedrive/cellar", "Q%3A" + "%5Cfakedrive%5Ccellar"])
def test_deny_list_folder_entry_in_any_spelling_matches_the_others(entry, root, gate, tmp_path):
    listed = tmp_path / "private" / "other-spelling.txt"
    listed.parent.mkdir(exist_ok=True)
    listed.write_text(entry + "\n", encoding="utf-8")
    for text in DRIVE_SPELLINGS.values():
        (root / "notes.txt").write_text(text + "\n", encoding="utf-8")
        code, out = gate(root, "--denylist", str(listed))
        assert code == 1 and "deny-list line 1" in out, (text, out)
        assert_no_term_leak(out)


def test_deny_listed_drive_folder_needs_the_drive(root, deny, gate):
    """The folder elsewhere (another drive, a sub-folder, a relative path) is another folder."""
    for text in ("R:" + BS + "fakedrive" + BS + "cellar", "fakedrive" + FS + "cellar",
                 FS + "home" + FS + "you" + FS + "q" + FS + "fakedrive" + FS + "cellar"):
        (root / "notes.txt").write_text(text + "\n", encoding="utf-8")
        out = gate(root, "--denylist", str(deny))[1]
        assert "deny-list line 9" not in out, (text, out)


# Baseline entries cover only blobs that are already public: in the history of the base branch
# (origin/main, or --baseline-base <rev>), never a brand-new file.


def test_baseline_never_covers_a_new_blob(root, gate, git_env):
    init_repo(root)
    commit_all(root, "Initial skeleton")
    publish(root)
    (root / "fixture.txt").write_text(FIXTURE, encoding="utf-8")
    blob = blob_of(root, "fixture.txt")
    write_baseline(root, f"{blob} 2 user-path", f"{blob} 3 user-path")
    code, out = gate(root)
    assert code == 1, out                   # a new file in the tree
    assert "fixture.txt:2: [user-path]" in out and "fixture.txt:3: [user-path]" in out
    commit_all(root, "Add fixture and its baseline entries")
    code, out = gate(root, "--history")
    assert code == 1, out                   # committed, but not on origin/main yet
    assert "not on origin/main" in out
    publish(root)
    code, out = gate(root, "--history")
    assert code == 0, out                   # once public, the entries count


def test_baseline_base_flag(root, gate, git_env):
    init_repo(root)
    first = commit_all(root, "Initial skeleton")
    (root / "fixture.txt").write_text(FIXTURE, encoding="utf-8")
    blob = blob_of(root, "fixture.txt")
    write_baseline(root, f"{blob} 2 user-path", f"{blob} 3 user-path")
    commit_all(root, "Add fixture")
    git(root, "branch", "public")
    assert gate(root, "--history")[0] == 1                       # no origin/main at all
    assert gate(root, "--history", "--baseline-base", first)[0] == 1
    code, out = gate(root, "--history", "--baseline-base", "public")
    assert code == 0, out
    code, out = gate(root, "--baseline-base", "no-such-branch")
    assert code == 2, out
    assert "--baseline-base" in out


def test_pre_push_baseline_needs_the_blob_on_origin_main(root, gate, git_env):
    init_repo(root)
    commit_all(root, "Initial skeleton")
    (root / "fixture.txt").write_text(FIXTURE, encoding="utf-8")
    blob = blob_of(root, "fixture.txt")
    write_baseline(root, f"{blob} 2 user-path", f"{blob} 3 user-path")
    sha = commit_all(root, "Add fixture")
    stdin = f"refs/heads/main {sha} refs/heads/main {pg.ZERO_SHA}\n"
    code, out = gate(root, "--pre-push", "origin", stdin_text=stdin)
    assert code == 1, out                    # the push itself would make the blob public
    env = {"PRE_COMMIT_TO_REF": sha, "PRE_COMMIT_REMOTE_NAME": "origin"}
    assert gate(root, "--pre-push", env=env)[0] == 1     # pre-commit's pre-push stage too
    publish(root)                            # public on origin; now pushed to a second remote
    code, out = gate(root, "--pre-push", "mirror", stdin_text=stdin)
    assert code == 0, out
    assert "reviewed fixture hits skipped" in out


def test_hook_pre_push_ignores_a_baseline_entry_for_a_new_file(hooked):
    (hooked / "fixture.txt").write_text(FIXTURE, encoding="utf-8")
    blob = blob_of(hooked, "fixture.txt")
    write_baseline(hooked, f"{blob} 2 user-path", f"{blob} 3 user-path")
    git(hooked, "add", "-A")
    git(hooked, "commit", "-q", "--no-verify", "-m", "Add fixture")  # past the pre-commit hook
    code, out = run_git(hooked, "push", "-q", "origin", "main", env={"LAMPTOWN_NO_DENYLIST": "1"})
    assert code != 0, out
    assert "fixture.txt" in out and "[user-path]" in out
