"""Self-tests for tools/privacy_gate.py with planted, fictional strings.

Planted personal paths and e-mail addresses are assembled at run time, so this file itself
stays clean when the gate scans the repo. The deny-list terms below are invented words.
"""

from __future__ import annotations

import importlib.util
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
TERMS = (TERM_WORD, TERM_PHRASE, TERM_PATH)
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
