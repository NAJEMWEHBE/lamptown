#!/usr/bin/env python3
"""Lamptown privacy gate: keep personal data out of the public repo.

Standard library only. Run from the repo root:

    python tools/privacy_gate.py                    # every file in the working tree
    python tools/privacy_gate.py FILE...            # only these files (pre-commit: staged)
    python tools/privacy_gate.py --history          # tree + every commit, ref and blob
    python tools/privacy_gate.py --commit-msg FILE  # a commit message + author/committer
    python tools/privacy_gate.py --pre-push [REMOTE]  # everything a push is about to send

Checks (exit 1 on any hit, 2 on a usage or setup error):

1. user-path: absolute personal paths on any OS, such as X:\\Users\\<name>, /Users/<name>,
   /home/<name>, plus drive-letter paths outside the Windows system folders. The only
   allowed user name is the placeholder "you" (template tokens such as <name>, $USER or
   %USERNAME% are fine too).
2. email: e-mail addresses, except example/test domains (RFC 2606) and noreply addresses.
3. deny-list: terms from the owner's private deny-list (--denylist or $LAMPTOWN_DENYLIST),
   whole-word and case-insensitive; spaces, "-" and "_" match each other and "/" matches
   "\\". Checked in file contents, file names, commit messages, commit authors and
   committers, ref names, and the text inside PNG/GIF metadata. The list must live
   outside the repo. Without a list this check is skipped with a notice and every
   other check still runs (fork pull requests in CI have no access to the secret).
4. image: images are only allowed under docs/media/, and only as PNG or GIF so their
   metadata can be checked. One narrow exception: the desktop app's icons, directly in
   src/lamptown/app/assets/, as .png, .ico or .icns, where every .ico/.icns frame must be a
   PNG (its metadata is checked like any PNG's).

The gate never prints a deny-list term. A hit names the deny-list line number instead, and
any term inside a file name or revealed snippet is masked the same way, so CI logs stay clean.

Deny-list format: one term per line; blank lines and lines starting with "#" are ignored.
A line "!author: <text>" allows that exact author/committer name or e-mail (case-insensitive)
in commit identities only, for example the owner's public noreply identity.

Commit, tag and push identities: the e-mail must be a noreply address (noreply@..., no-reply@...,
or a forge's per-user noreply host such as <id>+<name>@users.noreply.github.com) or an
example/reserved one; anything else fails, also a bare login, user@PCNAME and user@host.(none).

Baseline (tools/privacy_gate_baseline.txt, in the repo): reviewed, fictional user-path and
e-mail hits inside blobs that are already public and can't be rewritten (test fixtures written
before the gate learned a path form). One entry per line: "<blob sha> <line or -> <check>",
then an optional "# comment". An entry only covers that exact file content (the blob sha), that
line and that check, so an edited file or a new leak is never covered. Deny-list, image and
identity hits are never baselined. --suggest-baseline prints entries for the current hits;
review every one before adding it.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import os
import re
import subprocess
import sys
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PurePosixPath

ENV_DENYLIST = "LAMPTOWN_DENYLIST"
BASELINE_FILE = "tools/privacy_gate_baseline.txt"
BASELINE_CHECKS = ("user-path", "email")
MEDIA_DIR = "docs/media/"
APP_ICON_DIR = "src/lamptown/app/assets/"   # the app icons (tools/make_icon.py), nothing else
APP_ICON_EXTS = {".png", ".ico", ".icns"}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ICO_MAGIC = b"\x00\x00\x01\x00"
INSPECTABLE_IMAGE_EXTS = {".png", ".gif"}
IMAGE_EXTS = INSPECTABLE_IMAGE_EXTS | {
    ".jpg", ".jpeg", ".webp", ".bmp", ".ico", ".icns", ".tif", ".tiff", ".svg", ".avif",
    ".heic", ".heif", ".psd", ".xcf", ".jxl",
}
SKIP_DIRS = {".git", "_scratch", "_progress", "__pycache__", ".venv", "venv", "build", "dist",
             ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules"}
MAX_METADATA_BYTES = 8 * 1024 * 1024
ZERO_SHA = "0" * 40

# ----------------------------------------------------------------------------- patterns

_SEP = r"(?:[\\/]|%5[Cc]|%2[Ff])"      # a path separator, also URL-encoded
_NAME = r"[^\\/\s\"'`<>|:*?,;()\[\]{}%]+"
_USER_PATH_PREFIXES = "|".join([
    r"[A-Za-z](?::|%3A)",                                      # C:  C%3A
    _SEP + r"+(?:mnt|cygdrive)" + _SEP + r"+[A-Za-z](?=" + _SEP + ")",   # /mnt/c (WSL, Cygwin)
    _SEP + r"+[A-Za-z](?=" + _SEP + ")",                       # /c (Git Bash, MSYS)
    _SEP + r"+var(?=" + _SEP + ")",                            # /var/home (Fedora Atomic)
    _SEP + r"{2,}wsl(?:\$|\.localhost)" + _SEP + "+" + _NAME,  # \\wsl$\<distro>
])
# The lookbehind applies to the start of the prefix, so ".../c/Users/x" inside a URL stays clean.
USER_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9.~_-])(?:" + _USER_PATH_PREFIXES + ")?" + _SEP
    + r"+(?:Users|Documents and Settings|home)" + _SEP + r"+(?P<name>" + _NAME + ")",
    re.IGNORECASE,
)
DRIVE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z](?::|%3[Aa])" + _SEP + r"+"
    r"|[a-z](?::\\+|%3[Aa](?:%5[Cc]|%2[Ff])+))(?P<seg>" + _NAME + ")",
)
# A lower-case drive + "/" reads like minified JS ("x?a:/re/.test(s)", "{k:/re/gi.test(s)}"), so
# it only counts after a space, quote, "=", "(", "[", "<", ">" or "/" (never "?", ":", "," or "{",
# where JS puts a regex literal) and only with a further path segment that doesn't start with ".".
DRIVE_SLASH_RE = re.compile(
    r"(?<![^\s\"'`=(\[<>/])[a-z]:/+(?P<seg>" + _NAME + r")(?=/+[^\s/\\\"'`<>|*?,;()\[\]{}.:])",
)
SYSTEM_DRIVE_DIRS = ("windows", "program", "users", "documents and settings")
EMAIL_RE = re.compile(
    r"(?<![\w.+%-])(?P<local>[A-Za-z0-9._%+-]+)@"
    r"(?P<domain>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,})(?![\w-])"
)
# A whole identity e-mail: the local part may hold more than EMAIL_RE finds in prose, such as
# GitHub's bot noreply "<id>+github-actions[bot]@users.noreply.github.com"; the domain needs a
# dotted, letters-only top-level label (so "user@PCNAME" and "user@host.(none)" don't parse).
IDENTITY_EMAIL_RE = re.compile(
    r"(?P<local>[^\s@<>]+)@(?P<domain>(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,})"
)
EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_TLDS = ("example", "test", "invalid", "localhost")
NOREPLY_LABELS = ("noreply", "no-reply")
# Hosts whose every address is a per-user noreply alias (any local part is fine there).
NOREPLY_HOSTS = ("users.noreply.github.com", "users.noreply.gitlab.com", "noreply.codeberg.org")


def _placeholder_name(name: str) -> bool:
    return name.lower() == "you" or name[0] in "$%" or set(name) == {"."}


def _noreply_email(local: str, domain: str) -> bool:
    """A plain noreply@ / no-reply@ address, or any address on a forge's noreply host."""
    return local.lower() in NOREPLY_LABELS or domain.lower() in NOREPLY_HOSTS


def _reserved_domain(domain: str, *, localhost: bool = True) -> bool:
    d = domain.lower()
    if any(d == e or d.endswith("." + e) for e in EXAMPLE_DOMAINS):
        return True
    tld = d.rsplit(".", 1)[-1]
    return tld in RESERVED_TLDS and (localhost or tld != "localhost")


def _allowed_email(local: str, domain: str) -> bool:
    return _reserved_domain(domain) or _noreply_email(local, domain)


def allowed_identity_email(value: str) -> bool:
    """The e-mail of a commit, tag or push identity is an allow-list: a noreply address or an
    example/reserved one. Anything else fails, also what EMAIL_RE would not see as an address
    (a bare login, user@PCNAME, git's user@host.(none)): those leak a login and a machine name.
    An empty e-mail holds nothing and passes."""
    if not value:
        return True
    m = IDENTITY_EMAIL_RE.fullmatch(value.strip())
    return bool(m) and (_noreply_email(m.group("local"), m.group("domain"))
                        or _reserved_domain(m.group("domain"), localhost=False))


def generic_matches(text: str) -> Iterable[tuple[int, int, str, str]]:
    """Yield (start, end, check, detail) for every personal path or e-mail address in text."""
    for m in USER_PATH_RE.finditer(text):
        if not _placeholder_name(m.group("name")):
            yield (m.start(), m.end(), "user-path",
                   "absolute personal path (use ~ or the placeholder 'you')")
    for rx in (DRIVE_PATH_RE, DRIVE_SLASH_RE):
        for m in rx.finditer(text):
            if not m.group("seg").lower().startswith(SYSTEM_DRIVE_DIRS):
                yield (m.start(), m.end(), "user-path",
                       "absolute drive-letter path (use ~ or a config value)")
    for m in EMAIL_RE.finditer(text):
        if not _allowed_email(m.group("local"), m.group("domain")):
            yield (m.start(), m.end(), "email",
                   "e-mail address (use @example.com or a noreply address)")


def mask_generic(text: str) -> str:
    """Hide every personal path and e-mail in a display label (a file or ref name)."""
    spans = sorted((s, e, check) for s, e, check, _ in generic_matches(text))
    out, pos = [], 0
    for start, end, check in spans:
        if start < pos:
            continue
        out.append(text[pos:start])
        out.append("[e-mail]" if check == "email" else "[path]")
        pos = end
    out.append(text[pos:])
    return "".join(out)


# ----------------------------------------------------------------------------- deny-list


class SetupError(Exception):
    """A usage or setup problem (exit code 2). Messages never contain deny-list terms."""


@dataclass
class DenyList:
    terms: list[tuple[int, re.Pattern[str]]] = field(default_factory=list)
    author_allow: set[str] = field(default_factory=set)

    def find(self, text: str) -> Iterable[tuple[int, int, int]]:
        """Yield (deny-list line, start, end) for every term hit in text."""
        for line_no, pat in self.terms:
            for m in pat.finditer(text):
                yield line_no, m.start(), m.end()

    def redact(self, text: str) -> str:
        """Mask every deny-list term in text, for safe display."""
        spans = sorted(self.find(text), key=lambda h: (h[1], -h[2]))
        out, pos = [], 0
        for line_no, start, end in spans:
            if start < pos:
                continue
            out.append(text[pos:start])
            out.append(f"[deny-list line {line_no}]")
            pos = end
        out.append(text[pos:])
        return "".join(out)


def term_pattern(term: str) -> re.Pattern[str]:
    parts = re.split(r"([\\/]+|[\s_-]+)", term)
    body = []
    for part in parts:
        if not part:
            continue
        if re.fullmatch(r"[\\/]+", part):
            body.append(r"[\\/]+")
        elif re.fullmatch(r"[\s_-]+", part):
            body.append(r"[\s_-]+")
        else:
            body.append(re.escape(part))
    left = r"(?<![^\W_])" if term[0].isalnum() else ""
    right = r"(?![^\W_])" if term[-1].isalnum() else ""
    return re.compile(left + "".join(body) + right, re.IGNORECASE)


def load_denylist(path: Path, repo_root: Path) -> DenyList:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise SetupError("deny-list file not found or unreadable") from None
    if resolved.is_relative_to(repo_root.resolve()):
        raise SetupError("the deny-list is stored inside the repo; keep it outside and pass "
                         f"its path with --denylist or ${ENV_DENYLIST}")
    try:
        raw = resolved.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise SetupError("deny-list file is unreadable (expected UTF-8 text)") from None
    deny = DenyList()
    for line_no, line in enumerate(raw.splitlines(), 1):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("!"):
            key, _, value = entry[1:].partition(":")
            if key.strip().lower() != "author" or not value.strip():
                raise SetupError(f"deny-list line {line_no}: unknown directive "
                                 "(only '!author: <name or e-mail>' is supported)")
            deny.author_allow.add(value.strip().lower())
            continue
        deny.terms.append((line_no, term_pattern(entry)))
    return deny


# ----------------------------------------------------------------------------- hits


@dataclass(frozen=True)
class Hit:
    where: str          # already redacted display location
    line: int | None
    check: str          # user-path | email | deny-list | image
    detail: str         # never contains matched deny-list text
    snippet: str = ""   # redacted matched text, shown only with --reveal
    blob: str = ""      # git blob sha of the content the hit is in (baseline key), if any

    def format(self, reveal: bool) -> str:
        loc = self.where if self.line is None else f"{self.where}:{self.line}"
        text = f"{loc}: [{self.check}] {self.detail}"
        if reveal and self.snippet:
            text += f"  -> {self.snippet}"
        return text

    def baseline_entry(self) -> str:
        return f"{self.blob} {'-' if self.line is None else self.line} {self.check}  # {self.where}"


def blob_ids(data: bytes) -> tuple[str, ...]:
    """The git blob sha of data, plus that of its LF form when it has CRLF line ends (a
    Windows checkout of an LF blob), so a baseline entry matches either way."""
    def sha(b: bytes) -> str:
        return hashlib.sha1(b"blob %d\0" % len(b) + b, usedforsecurity=False).hexdigest()
    ids = (sha(data),)
    return ids + (sha(data.replace(b"\r\n", b"\n")),) if b"\r\n" in data else ids


def load_baseline(root: Path) -> frozenset[tuple[str, str, str]]:
    path = root / BASELINE_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return frozenset()
    except (OSError, UnicodeDecodeError):
        raise SetupError(f"{BASELINE_FILE} is unreadable (expected UTF-8 text)") from None
    out = set()
    for line_no, line in enumerate(raw.splitlines(), 1):
        entry = line.split("#", 1)[0].split()
        if not entry:
            continue
        if (len(entry) != 3 or not re.fullmatch(r"[0-9a-f]{40}", entry[0])
                or not (entry[1] == "-" or entry[1].isdigit())
                or entry[2] not in BASELINE_CHECKS):
            raise SetupError(f"{BASELINE_FILE} line {line_no}: expected "
                             "'<blob sha> <line or -> <user-path|email>'")
        out.add((entry[0], entry[1], entry[2]))
    return frozenset(out)


class Scanner:
    def __init__(self, deny: DenyList | None,
                 baseline: frozenset[tuple[str, str, str]] = frozenset()):
        self.deny = deny
        self.baseline = baseline
        self.baselined = 0               # hits skipped because the baseline lists them
        self.hits: list[Hit] = []
        self._blobs: tuple[str, ...] = ()  # blob ids of the content being scanned

    # -- helpers
    def display(self, name: str) -> str:
        return self.deny.redact(name) if self.deny else name

    def _snippet(self, text: str) -> str:
        return self.display(text)

    def add(self, where: str, line: int | None, check: str, detail: str, snippet: str = ""):
        if self._blobs and check in BASELINE_CHECKS and self.baseline:
            key = "-" if line is None else str(line)
            if any((b, key, check) in self.baseline for b in self._blobs):
                self.baselined += 1
                return
        # The location may itself be a file or ref name holding the match: mask it there too.
        self.hits.append(Hit(self.display(mask_generic(where)), line, check, detail,
                             self._snippet(snippet), self._blobs[0] if self._blobs else ""))

    # -- text
    def scan_text(self, text: str, where: str, *, lines: bool = True, label: str = "") -> None:
        """Run the path, e-mail and deny-list checks over text."""
        starts = [0] + [m.end() for m in re.finditer("\n", text)] if lines else []

        def line_of(pos: int) -> int | None:
            return bisect.bisect_right(starts, pos) if lines else None

        prefix = f"{label}: " if label else ""
        for start, end, check, detail in generic_matches(text):
            self.add(where, line_of(start), check, prefix + detail, text[start:end])
        if self.deny:
            for line_no, start, _end in self.deny.find(text):
                self.add(where, line_of(start), "deny-list",
                         prefix + f"deny-list term (deny-list line {line_no})")

    def scan_identity(self, value: str, where: str, field_name: str, *,
                      email: bool | None = None) -> None:
        """Check a commit/tag author, committer or tagger name or e-mail.

        An e-mail field (email=True, or a field_name with "e-mail" in it) must be on the
        allow-list (allowed_identity_email); a name field gets the deny-list check only.
        """
        if not value:
            return
        if email is None:
            email = "e-mail" in field_name
        if email and not allowed_identity_email(value):
            self.add(where, None, "email",
                     f"{field_name} is not a noreply or example address", value)
        if self.deny and value.strip().lower() not in self.deny.author_allow:
            for line_no, _s, _e in self.deny.find(value):
                self.add(where, None, "deny-list",
                         f"{field_name}: deny-list term (deny-list line {line_no})")

    # -- names
    def scan_name(self, rel: str) -> None:
        """Check a repo-relative path (posix form): the text checks and the image location."""
        # A file passed from outside the repo: only its own name is the repo's business.
        shown = PurePath(rel).name if Path(rel).is_absolute() else rel
        self.scan_text(shown, rel, lines=False, label="file name")
        ext = PurePosixPath(rel).suffix.lower()
        if ext in IMAGE_EXTS:
            if is_app_icon(rel):
                pass
            elif not rel.startswith(MEDIA_DIR):
                self.add(rel, None, "image", f"images are only allowed under {MEDIA_DIR}")
            elif ext not in INSPECTABLE_IMAGE_EXTS:
                self.add(rel, None, "image", "use PNG or GIF so the image metadata can be "
                         "checked")

    # -- file content
    def scan_bytes(self, data: bytes, rel: str, where: str | None = None) -> None:
        """Check file content. rel is the repo path (image rules), where the display label."""
        outer, self._blobs = self._blobs, blob_ids(data)
        try:
            self._scan_bytes(data, rel, where or rel)
        finally:
            self._blobs = outer

    def _scan_bytes(self, data: bytes, rel: str, where: str) -> None:
        kind = sniff_image(data)
        ext = PurePosixPath(rel).suffix.lower()
        if is_app_icon(rel) and kind != ext[1:]:
            # the allowance holds only for files that really are what their name says
            self.add(where, None, "image", f"not a valid {ext[1:].upper()} file")
            return
        if kind:
            # Image extensions are handled by scan_name; this catches disguised images.
            if PurePosixPath(rel).suffix.lower() not in IMAGE_EXTS:
                self.add(where, None, "image", f"{kind.upper()} image data in a file without "
                         "an image extension")
            if kind == "png":
                self._scan_chunks(png_text_chunks(data), where, "PNG metadata")
            elif kind == "gif":
                self._scan_chunks(gif_text_blocks(data), where, "GIF metadata")
            elif kind in ("ico", "icns"):
                self._scan_icon(data, kind, where)
            return
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            self.scan_text(data.decode("utf-16", errors="replace"), where)
        elif b"\0" in data[:8192]:
            self.scan_text(data.decode("latin-1"), where, lines=False, label="binary file")
        else:
            self.scan_text(data.decode("utf-8", errors="replace"), where)

    def _scan_icon(self, data: bytes, kind: str, where: str) -> None:
        label = kind.upper()
        try:
            frames = icon_png_frames(data, kind)
        except ImageParseError as exc:
            self.add(where, None, "image", f"{label} could not be read ({exc})")
            return
        for i, frame in enumerate(frames, 1):
            self._scan_chunks(png_text_chunks(frame), where, f"{label} frame {i} PNG metadata")

    def _scan_chunks(self, chunks: Iterable[tuple[str, str]], where: str, label: str) -> None:
        try:
            for name, text in chunks:
                self.scan_text(text, where, lines=False, label=f"{label} ({name})")
        except ImageParseError as exc:
            self.add(where, None, "image", f"{label} could not be read ({exc}); re-export it")


# ----------------------------------------------------------------------------- images


class ImageParseError(Exception):
    pass


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == ICO_MAGIC and len(data) >= 22 and data[4:6] != b"\0\0":
        return "ico"
    if data[:4] == b"icns" and len(data) >= 8 and int.from_bytes(data[4:8], "big") == len(data):
        return "icns"
    return None


def is_app_icon(rel: str) -> bool:
    """The one allowance outside docs/media/: an app icon directly in APP_ICON_DIR."""
    rest = rel[len(APP_ICON_DIR):] if rel.startswith(APP_ICON_DIR) else ""
    return bool(rest) and "/" not in rest and PurePosixPath(rest).suffix.lower() in APP_ICON_EXTS


def icon_png_frames(data: bytes, kind: str) -> list[bytes]:
    """Every image inside an .ico or .icns. Only PNG frames are accepted: the metadata of BMP
    or raw-bitmap frames can't be checked."""
    frames: list[bytes] = []
    if kind == "ico":
        count = int.from_bytes(data[4:6], "little")
        if len(data) < 6 + 16 * count:
            raise ImageParseError("truncated directory")
        for i in range(count):
            entry = data[6 + 16 * i:22 + 16 * i]
            size = int.from_bytes(entry[8:12], "little")
            off = int.from_bytes(entry[12:16], "little")
            if off + size > len(data) or size < 8:
                raise ImageParseError(f"frame {i + 1} runs past the end")
            frames.append(data[off:off + size])
    else:
        pos = 8
        while pos < len(data):
            if pos + 8 > len(data):
                raise ImageParseError("truncated entry")
            size = int.from_bytes(data[pos + 4:pos + 8], "big")
            if size < 8 or pos + size > len(data):
                raise ImageParseError("entry runs past the end")
            frames.append(data[pos + 8:pos + size])
            pos += size
    if not frames:
        raise ImageParseError("no frames")
    for i, frame in enumerate(frames, 1):
        if not frame.startswith(PNG_MAGIC):
            raise ImageParseError(f"frame {i} is not a PNG; only PNG frames can be checked")
    return frames


def _inflate(blob: bytes) -> bytes:
    d = zlib.decompressobj()
    try:
        out = d.decompress(blob, MAX_METADATA_BYTES)
    except zlib.error:
        raise ImageParseError("bad compressed text chunk") from None
    if d.unconsumed_tail:
        raise ImageParseError("compressed text chunk too large")
    return out


def png_text_chunks(data: bytes) -> Iterable[tuple[str, str]]:
    """Yield (chunk name, text) for every text-bearing PNG chunk and trailing bytes."""
    pos, ended = 8, False
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if pos + 12 + length > len(data):
            raise ImageParseError("truncated chunk")
        pos += 12 + length
        if ctype == b"tEXt":
            key, _, text = body.partition(b"\0")
            yield "tEXt", key.decode("latin-1") + "\n" + text.decode("latin-1")
        elif ctype == b"zTXt":
            key, _, rest = body.partition(b"\0")
            yield "zTXt", key.decode("latin-1") + "\n" + _inflate(rest[1:]).decode("latin-1")
        elif ctype == b"iTXt":
            key, _, rest = body.partition(b"\0")
            if len(rest) < 2:
                raise ImageParseError("truncated iTXt chunk")
            compressed = rest[0]
            lang, _, rest = rest[2:].partition(b"\0")
            tkey, _, text = rest.partition(b"\0")
            if compressed:
                text = _inflate(text)
            yield "iTXt", "\n".join(
                b.decode("utf-8", errors="replace") for b in (key, lang, tkey, text))
        elif ctype == b"eXIf":
            yield "eXIf", body.decode("latin-1")
        elif ctype == b"IEND":
            ended = True
            break
    if not ended:
        raise ImageParseError("missing IEND chunk")
    if pos < len(data):
        yield "trailing data", data[pos:].decode("latin-1")


def _gif_subblocks(data: bytes, pos: int) -> tuple[bytes, int]:
    out = bytearray()
    while True:
        if pos >= len(data):
            raise ImageParseError("truncated block")
        size = data[pos]
        pos += 1
        if size == 0:
            return bytes(out), pos
        if pos + size > len(data):
            raise ImageParseError("truncated block")
        out += data[pos:pos + size]
        pos += size


def gif_text_blocks(data: bytes) -> Iterable[tuple[str, str]]:
    """Yield (block name, text) for GIF comment, plain-text and application blocks."""
    if len(data) < 13:
        raise ImageParseError("truncated header")
    flags = data[10]
    pos = 13 + (3 * (2 << (flags & 7)) if flags & 0x80 else 0)
    while pos < len(data):
        intro = data[pos]
        pos += 1
        if intro == 0x3B:  # trailer
            break
        if intro == 0x21:  # extension
            if pos >= len(data):
                raise ImageParseError("truncated extension")
            label = data[pos]
            payload, pos = _gif_subblocks(data, pos + 1)
            if label == 0xFE:
                yield "comment", payload.decode("latin-1")
            elif label == 0xFF:
                yield "application", payload.decode("latin-1")
            elif label == 0x01:
                yield "plain text", payload[12:].decode("latin-1")
        elif intro == 0x2C:  # image descriptor
            if pos + 9 > len(data):
                raise ImageParseError("truncated image descriptor")
            iflags = data[pos + 8]
            pos += 9 + (3 * (2 << (iflags & 7)) if iflags & 0x80 else 0)
            _, pos = _gif_subblocks(data, pos + 1)  # skip LZW code size + image data
        else:
            raise ImageParseError("unknown block")
    if pos < len(data):
        yield "trailing data", data[pos:].decode("latin-1")


# ----------------------------------------------------------------------------- git


def git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(["git", "-C", str(root), *args], input=stdin,
                              capture_output=True, check=False)
    except FileNotFoundError:
        raise SetupError("git is not installed or not on PATH") from None
    if proc.returncode != 0:
        raise SetupError(f"git {args[0]} failed (exit {proc.returncode})")
    return proc.stdout


def is_git_repo(root: Path) -> bool:
    try:
        return git(root, "rev-parse", "--is-inside-work-tree").strip() == b"true"
    except SetupError:
        return False


def has_commits(root: Path) -> bool:
    try:
        git(root, "rev-parse", "--verify", "-q", "HEAD")
    except SetupError:
        return False
    return True


def find_root(start: Path) -> Path:
    try:
        top = git(start, "rev-parse", "--show-toplevel").decode().strip()
    except SetupError:
        return start.resolve()
    return Path(top).resolve() if top else start.resolve()


def tree_files(root: Path) -> list[str]:
    if is_git_repo(root):
        raw = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        names = [n for n in raw.decode("utf-8", errors="replace").split("\0") if n]
        return sorted({n for n in names if (root / n).is_file()})
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for fn in filenames:
            found.append((Path(dirpath) / fn).relative_to(root).as_posix())
    return sorted(found)


def scan_history(scanner: Scanner, root: Path, revs: Sequence[str], refs: bool) -> None:
    """Commit messages and identities, ref names, every path and blob reachable from revs."""
    if not has_commits(root):
        return
    log = git(root, "log", "-z", "--format=%H%x01%an%x01%ae%x01%cn%x01%ce%x01%B", *revs, "--")
    for rec in log.decode("utf-8", errors="replace").split("\0"):
        if not rec.strip():
            continue
        sha, an, ae, cn, ce, body = (rec.split("\x01", 5) + [""] * 6)[:6]
        where = f"commit {sha.strip()[:10]}"
        scanner.scan_text(body, f"{where} message")
        scanner.scan_identity(an, where, "author name")
        scanner.scan_identity(ae, where, "author e-mail")
        scanner.scan_identity(cn, where, "committer name")
        scanner.scan_identity(ce, where, "committer e-mail")

    if refs:
        raw = git(root, "for-each-ref", "--format=%(refname)%01%(objectname)%01%(objecttype)%00")
        for raw_rec in raw.decode("utf-8", errors="replace").split("\0"):
            rec = raw_rec.strip("\n")
            if not rec:
                continue
            name, sha, otype = (rec.split("\x01", 2) + [""] * 3)[:3]
            scanner.scan_text(name, "ref name", lines=False)
            if otype == "tag":
                scan_tags(scanner, root, sha)

    names = git(root, "log", "-z", "-m", "--format=", "--name-only", *revs, "--")
    paths = {n.strip("\n") for n in names.decode("utf-8", errors="replace").split("\0")}
    first_path: dict[str, str] = {}
    for line in git(root, "rev-list", "--objects", *revs, "--").decode(
            "utf-8", errors="replace").splitlines():
        sha, _, path = line.partition(" ")
        if path:
            first_path.setdefault(sha, path)
    blobs: list[str] = []
    if first_path:
        checks = git(root, "cat-file", "--batch-check=%(objectname) %(objecttype)",
                     stdin="\n".join(first_path).encode() + b"\n")
        blobs = [ln.split()[0] for ln in checks.decode().splitlines() if ln.endswith(" blob")]
    paths.update(first_path[b] for b in blobs)
    for sha, data in read_blobs(root, blobs):
        scanner.scan_bytes(data, first_path[sha], f"{first_path[sha]} (blob {sha[:10]})")
    for p in sorted(p for p in paths if p):
        scanner.scan_name(p)


def read_blobs(root: Path, shas: Sequence[str]) -> Iterable[tuple[str, bytes]]:
    """(sha, content) for each blob, read in one `git cat-file --batch`."""
    if not shas:
        return
    out = git(root, "cat-file", "--batch", stdin="\n".join(shas).encode() + b"\n")
    pos = 0
    for sha in shas:
        nl = out.index(b"\n", pos)
        size = int(out[pos:nl].split()[2])
        yield sha, out[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1


def split_ident(ident: str) -> tuple[str, str]:
    """'Name <e-mail> [time zone]' -> (name, e-mail)."""
    m = re.match(r"(.*?)\s*<([^>]*)>", ident)
    return (m.group(1), m.group(2)) if m else (ident.strip(), "")


def scan_tags(scanner: Scanner, root: Path, sha: str) -> None:
    """If sha is an annotated tag: its tagger, tag name and message, then the same for every
    tag it points at (a tag of a tag), until the chain reaches a commit, tree or blob."""
    seen: set[str] = set()
    while sha and sha not in seen:
        seen.add(sha)
        if git(root, "cat-file", "-t", sha).strip() != b"tag":
            return
        raw = git(root, "cat-file", "tag", sha).decode("utf-8", errors="replace")
        head, _, message = raw.partition("\n\n")
        where = "annotated tag"
        sha = ""
        for line in head.splitlines():
            key, _, value = line.partition(" ")
            if key == "object":
                sha = value.strip()
            elif key == "tag":
                scanner.scan_text(value, f"{where} name", lines=False)
            elif key == "tagger":
                name, mail = split_ident(value)
                scanner.scan_identity(name, where, "tagger name", email=False)
                scanner.scan_identity(mail, where, "tagger e-mail", email=True)
        scanner.scan_text(message, f"{where} message")


def git_identity(root: Path, var: str) -> tuple[str, str]:
    try:
        ident = git(root, "var", var).decode("utf-8", errors="replace").strip()
    except SetupError:
        return "", ""
    return split_ident(ident)


def staged_files(root: Path) -> list[tuple[str, str]]:
    """(path, blob sha) for every file added, copied, modified, renamed or retyped in the index
    (the one git uses for this commit: GIT_INDEX_FILE is honoured). Gitlinks are skipped."""
    raw = git(root, "diff", "--cached", "--name-only", "-z", "--no-renames",
              "--diff-filter=ACMRT")
    wanted = {n for n in raw.decode("utf-8", errors="replace").split("\0") if n}
    if not wanted:
        return []
    out = []
    listing = git(root, "ls-files", "-s", "-z").decode("utf-8", errors="replace")
    for entry in listing.split("\0"):
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if path in wanted and len(parts) == 3 and parts[0] != "160000":
            out.append((path, parts[1]))
    return sorted(out)


def scan_staged(scanner: Scanner, root: Path, only: Sequence[str] = ()) -> None:
    files = [(p, s) for p, s in staged_files(root) if not only or p in only]
    path_of = {sha: p for p, sha in files}
    for path, _sha in files:
        scanner.scan_name(path)
    for sha, data in read_blobs(root, list(path_of)):
        scanner.scan_bytes(data, path_of[sha], f"{path_of[sha]} (staged)")


SCISSORS_RE = re.compile(r"^# -+ >8 -+$")


def commit_message_body(text: str) -> str:
    """What git will store: drop '#' comment lines and the `commit -v` diff below the scissors.

    Line numbers are kept, so a hit points at the right line of the editor buffer.
    """
    kept = []
    for ln in text.splitlines():
        if SCISSORS_RE.match(ln):
            break
        kept.append("" if ln.startswith("#") else ln)
    return "\n".join(kept)


def push_refs(stdin_text: str, env: dict[str, str]) -> list[tuple[str, str, str]]:
    """(local ref, local sha, remote ref) per pushed ref (git pre-push stdin or pre-commit env).
    Deletes (an all-zero local sha) are left out: they send nothing, and deleting a badly
    named branch from the remote must stay possible."""
    if env.get("PRE_COMMIT_TO_REF"):
        refs = [(env.get("PRE_COMMIT_LOCAL_BRANCH", ""), env["PRE_COMMIT_TO_REF"],
                 env.get("PRE_COMMIT_REMOTE_BRANCH", ""))]
    else:
        refs = [(p[0], p[1], p[2]) for p in (ln.split() for ln in stdin_text.splitlines())
                if len(p) == 4]
    return [r for r in refs if r[1] and r[1] != ZERO_SHA]


def push_revs(remote: str | None, stdin_text: str, env: dict[str, str]) -> list[str]:
    """Revision arguments for what a push sends (git pre-push stdin or pre-commit env)."""
    shas = [sha for _local, sha, _remote in push_refs(stdin_text, env)]
    if env.get("PRE_COMMIT_TO_REF"):
        remote = remote or env.get("PRE_COMMIT_REMOTE_NAME")
    if not shas:
        return []
    return [*shas, "--not", f"--remotes={remote}"] if remote else shas


def scan_push(scanner: Scanner, root: Path, remote: str | None, stdin_text: str,
              env: dict[str, str]) -> None:
    """Everything a push sends: new commits and blobs, both ref names, annotated tags."""
    revs = push_revs(remote, stdin_text, env)
    if revs:
        scan_history(scanner, root, revs, refs=False)
    for local_ref, sha, remote_ref in push_refs(stdin_text, env):
        for name in dict.fromkeys((local_ref, remote_ref)):
            if name:
                scanner.scan_text(name, "ref name", lines=False)
        scan_tags(scanner, root, sha)


# ----------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="privacy_gate.py",
        description="Fail when personal data would enter the public Lamptown repo.",
    )
    p.add_argument("paths", nargs="*", help="files or folders to scan (default: the whole tree)")
    p.add_argument("--root", type=Path, help="repo root (default: git top level or cwd)")
    p.add_argument("--denylist", type=Path,
                   help=f"private deny-list file outside the repo (default: ${ENV_DENYLIST})")
    p.add_argument("--require-denylist", action="store_true",
                   help="fail instead of skipping when no deny-list is given (CI with the secret)")
    p.add_argument("--history", action="store_true",
                   help="also scan every commit, ref and blob (use --rev to limit)")
    p.add_argument("--rev", action="append", default=[], metavar="REV",
                   help="revision argument for --history (repeatable, default --all)")
    p.add_argument("--no-tree", action="store_true", help="skip the working-tree scan")
    p.add_argument("--commit-msg", type=Path, metavar="FILE",
                   help="scan a commit message file and the author/committer identity")
    p.add_argument("--pre-push", nargs="?", const="", metavar="REMOTE",
                   help="scan what is being pushed (git pre-push hook or pre-commit)")
    p.add_argument("--staged", action="store_true",
                   help="scan the staged (index) version of every added or changed file, or of "
                        "the named ones (git pre-commit hook)")
    p.add_argument("--reveal", action="store_true",
                   help="show the matched path or e-mail (deny-list terms are never shown)")
    p.add_argument("--suggest-baseline", action="store_true",
                   help=f"also print {BASELINE_FILE} entries for the path and e-mail hits in "
                        "file contents (review each one: only fictional, already-public text)")
    return p


def run(argv: Sequence[str] | None = None, *, stdin_text: str | None = None,
        env: dict[str, str] | None = None, cwd: Path | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = dict(os.environ) if env is None else env
    cwd = cwd or Path.cwd()
    root = (args.root or find_root(cwd)).resolve()

    try:
        deny_path = args.denylist or (Path(env[ENV_DENYLIST]) if env.get(ENV_DENYLIST) else None)
        deny = load_denylist(deny_path, root) if deny_path else None
        if args.require_denylist and (deny is None or not deny.terms):
            raise SetupError("--require-denylist: no deny-list terms loaded")
        if deny is None:
            print(f"privacy gate: notice: no deny-list (--denylist or ${ENV_DENYLIST}); "
                  "the deny-list check is skipped, all other checks run", file=sys.stderr)
        elif not deny.terms:
            print("privacy gate: notice: the deny-list has no terms, so the deny-list check "
                  "finds nothing; all other checks run", file=sys.stderr)

        scanner = Scanner(deny, load_baseline(root))
        mode_set = args.commit_msg is not None or args.pre_push is not None or args.staged
        if args.commit_msg is not None:
            try:
                text = args.commit_msg.read_text(encoding="utf-8", errors="replace")
            except OSError:
                raise SetupError("commit message file could not be read") from None
            scanner.scan_text(commit_message_body(text), "commit message")
            for var, label in (("GIT_AUTHOR_IDENT", "author"),
                               ("GIT_COMMITTER_IDENT", "committer")):
                name, email = git_identity(root, var)
                scanner.scan_identity(name, "commit identity", f"{label} name")
                scanner.scan_identity(email, "commit identity", f"{label} e-mail")
        if args.pre_push is not None:
            if stdin_text is None:
                stdin_text = "" if env.get("PRE_COMMIT_TO_REF") else sys.stdin.read()
            scan_push(scanner, root, args.pre_push or None, stdin_text or "", env)
        if args.staged:
            scan_staged(scanner, root, expand_paths(args.paths, cwd, root) if args.paths else ())
        if not mode_set:
            if args.paths:
                for rel in expand_paths(args.paths, cwd, root):
                    scan_file(scanner, root, rel)
            elif not args.no_tree:
                for rel in tree_files(root):
                    scan_file(scanner, root, rel)
            if args.history:
                scan_history(scanner, root, args.rev or ["--all"], refs=not args.rev)
    except SetupError as exc:
        print(f"privacy gate: error: {exc}", file=sys.stderr)
        return 2

    if scanner.baselined:
        print(f"privacy gate: notice: {scanner.baselined} reviewed fixture hit"
              f"{'s' if scanner.baselined != 1 else ''} skipped ({BASELINE_FILE})",
              file=sys.stderr)
    if scanner.hits:
        hits = sorted(set(scanner.hits), key=lambda h: (h.where, h.line or 0, h.check, h.detail))
        print(f"privacy gate: FAIL ({len(hits)} hit{'s' if len(hits) != 1 else ''})")
        for hit in hits:
            print("  " + hit.format(args.reveal))
        if args.suggest_baseline:
            entries: dict[str, str] = {}
            for h in hits:
                if h.blob and h.check in BASELINE_CHECKS:
                    entry = h.baseline_entry()
                    entries.setdefault(entry.split("#", 1)[0].strip(), entry)
            print(f"privacy gate: {BASELINE_FILE} entries for these hits (add only reviewed, "
                  "fictional, already-public ones):")
            for entry in entries.values():
                print(entry)
        return 1
    print("privacy gate: pass")
    return 0


def expand_paths(paths: Sequence[str], cwd: Path, root: Path) -> list[str]:
    out: list[str] = []
    for raw in paths:
        p = Path(raw)
        p = p if p.is_absolute() else cwd / p
        items = [p] if p.is_file() else [q for q in p.rglob("*") if q.is_file()]
        for item in items:
            try:
                out.append(item.resolve().relative_to(root).as_posix())
            except ValueError:
                out.append(str(item))
    return out


def scan_file(scanner: Scanner, root: Path, rel: str) -> None:
    scanner.scan_name(rel)
    path = Path(rel) if Path(rel).is_absolute() else root / rel
    try:
        data = path.read_bytes()
    except OSError:
        scanner.add(rel, None, "image" if Path(rel).suffix.lower() in IMAGE_EXTS else "read",
                    "file could not be read")
        return
    scanner.scan_bytes(data, rel)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    return run()


if __name__ == "__main__":
    sys.exit(main())
