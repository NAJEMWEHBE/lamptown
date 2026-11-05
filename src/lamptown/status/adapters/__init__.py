"""Provider adapters: turn one hook payload into one status change.

Each adapter module has ``AGENT`` (the ``agent`` field it writes), ``translate(payload,
on_stop)`` returning an :class:`Update` or ``None`` (ignore this event), and
``respond(payload)`` returning the text a provider expects on stdout (``""`` for none).
Adapters are pure functions of the payload, so they are tested against recorded payloads.

What a hook writes: the state, a fixed message ("Working", "Turn finished", "Using Bash": the
tool's name only), the name of the session's folder as the title of a new file (a title
already there is kept), and that folder's path as ``x-folder`` (only so a ``status_dir``
source's ``private_dirs`` can hide it; the town never shows it). Never prompts, replies, tool
input, file contents or account fields (such as e-mail).
Reply excerpts are opt-in (``--excerpts`` or ``LAMPTOWN_STATUS_EXCERPTS=1``): then the first
line of the agent's reply, cut to 80 characters, with anything that looks like a secret masked.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from lamptown.status.store import iso, slug, update_status, utcnow

ON_STOP = ("ok", "waiting")
ENV_EXCERPTS = "LAMPTOWN_STATUS_EXCERPTS"
EXCERPT_LIMIT = 80
FOLDER_LIMIT = 1024
# Masked even in an opted-in excerpt: NAME=value pairs whose name says key/token/secret/password,
# bearer tokens, and long runs of key-like characters.
# --- review-fix R2a: secret shapes --- the NAME may sit in quotes (JSON '"token": "x"', a
# Python dict "'token': 'x'"). A match starts at the NAME's word (key, token...), so a name of
# any length is seen ("MY_..._TOKEN=x"); the rest of the NAME is bounded, so a long word run
# is scanned in linear time.
_SECRET_PAIR = re.compile(
    r"(?i)((?:key|token|secret|passw(?:or)?d|pwd|credential|auth)[\w.-]{0,128}[\"']?)"
    r"(\s*[:=]\s*)"
    r"((?:bearer|basic)\s+\S+|\"[^\"]*\"|'[^']*'|\S+)")
# A command-line flag that takes a secret as its next word: "--token X", "--db-password X",
# "--api-key X", "-auth X". The flag's name ends in the word ("--author", "--keyboard" do not).
_SECRET_FLAG = re.compile(
    r"(?i)(?<![\w-])(--?(?:[a-z0-9]{1,32}[-_]){0,4}"
    r"(?:token|secret|passw(?:or)?d|pwd|passphrase|api[-_]?key|key|auth|credentials?))"
    r"(?![\w-])(\s+)(?!-)(\"[^\"]*\"|'[^']*'|\S+)")
# Well-known token shapes: never a title, so masked even where long runs are kept.
_KNOWN_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:(?i:gh[pousr]_)[A-Za-z0-9]{16,}|(?i:github_pat_)[A-Za-z0-9_]{20,}"
    r"|(?i:sk-)(?:[A-Za-z0-9]+[-_]){0,3}[A-Za-z0-9]{16,}[A-Za-z0-9_-]*"
    r"|(?i:[sr]k_(?:live|test)_)[A-Za-z0-9]{16,}"
    r"|(?i:xox[abpr]-)[A-Za-z0-9-]{10,}|(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Za-z])"
    r"|(?i:glpat-)[A-Za-z0-9_-]{16,}"  # the prefix in any case: a room may capitalise it
    # --- review-fix R3a: more shapes --- Google (AIza...), Hugging Face (hf_...), npm
    # (npm_...), and a JWT (three base64url parts, the first a JSON header: "eyJ")
    # (the header part is bounded: each "eyJ" starts a match, so an unbounded one scans to
    # the end of the text each time; a Google key's prefix is case-sensitive, as real keys are,
    # so a word such as "aizawa-..." stays: rooms mask before they capitalise)
    r"|AIza[A-Za-z0-9_-]{20,}|(?i:hf_)[A-Za-z0-9]{16,}|(?i:npm_)[A-Za-z0-9]{16,}"
    r"|eyJ[A-Za-z0-9_-]{4,512}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*)")
# --- end review-fix R2a ---
# --- review-fix R3a: more secret shapes ---
# A PEM private key: its header, its body and its END line go; with no END line, the rest of
# the text (a title cut short is all key from the header on).
_PEM_HEAD = re.compile(r"(?i)-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_PEM_END = re.compile(r"(?i)-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
# A password glued to "-p" ("mysql -pSECRET", "7z x -pSECRET"): only after a program whose
# "-p" takes a password, so "mkdir -p" and "docker run -p8080:80" stay. "-P" is a port.
_PW_PROGRAM = re.compile(r"(?i)(?<![\w.-])(?:mysql[\w-]*|mariadb[\w-]*|sshpass|7z[ar]?|unrar"
                         r"|rar)(?:\.exe)?(?![\w.-])")
_GLUED_P = re.compile(r"(?<![\w-])-p(?=[^\s-])(?:\"[^\"]*\"|'[^']*'|\S+)")
# A NAME that ends in "pass" ("PASS=x", "DB_PASS=x", '"pass": "x"'), but not a word that
# holds it ("bypass=1", "compass=north", "PASSES=3", "tests pass: 12").
_PASS_PAIR = re.compile(r"(?i)((?<![a-z])pass(?:\s*=\s*|[\"']\s*:\s*))(\"[^\"]*\"|'[^']*'|\S+)")
# In a URL: the password of its userinfo ("https://bob:PW@host"), a webhook's secret path
# (Slack, Discord) and a signature query value ("sig=", "X-Amz-Signature=", "Signature=").
# "token=" and "key=" values are NAME=value pairs already.
_URL_PASSWORD = re.compile(r"(?i)(?<![\w+.-])([a-z][a-z0-9+.-]*://[^\s/?#@:]*:)[^\s/?#@]+(?=@)")
_WEBHOOK = re.compile(r"(?i)((?:hooks\.slack\.com/(?:services|workflows|triggers)"
                      r"|discord(?:app)?\.com/api/webhooks)/)[^\s?#\"'<>]+")
_SIGNED_QUERY = re.compile(r"(?i)([?&;](?:[\w-]*[-_.])?(?:sig|signature)=)[^&#\s\"'<>]+")


def _mask_pem(text: str) -> str:
    out, pos = [], 0
    while (head := _PEM_HEAD.search(text, pos)) is not None:
        out += [text[pos:head.start()], "…"]
        end = _PEM_END.search(text, head.end())
        pos = end.end() if end else len(text)
    out.append(text[pos:])
    return "".join(out)


def _mask_glued_password(text: str) -> str:
    program = _PW_PROGRAM.search(text)
    if program is None:
        return text
    return text[:program.end()] + _GLUED_P.sub("-p…", text[program.end():])
# --- end review-fix R3a ---
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_+/=.-]{24,}")


@dataclass
class Update:
    """One change to one status file. ``None`` fields stay as they are."""

    id: str
    agent: str
    title: str | None = None  # the folder's name: the title of a brand-new file only
    display: str = ""  # title for a brand-new file when the payload names no folder
    state: str | None = None
    message: str | None = None
    needs_you: list[dict] | None = None  # [] clears, None keeps
    session_start: bool = False
    folder_path: str = ""  # the session's folder, kept as x-folder for private_dirs
    extra: dict[str, Any] = field(default_factory=dict)


def one_line(text: Any, limit: int = 200) -> str:
    """Squash whitespace and cut to ``limit`` characters (with an ellipsis)."""
    if not isinstance(text, str):
        return ""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def excerpts_wanted(flag: bool = False) -> bool:
    """Reply excerpts are off unless the hook says ``--excerpts`` or the env var is on."""
    if flag:
        return True
    return os.environ.get(ENV_EXCERPTS, "").strip().lower() in ("1", "true", "yes", "on")


def mask_secrets(text: str, *, long_tokens: bool = True) -> str:
    """``NAME=value`` secrets (also ``"token": "x"`` in JSON), secret flags (``--token X``),
    bearer tokens and well-known token shapes (``ghp_...``, ``sk-...``, ``xoxb-...``,
    ``AKIA...``, ``glpat-...``, ``AIza...``, ``hf_...``, ``npm_...``, a JWT, a PEM private
    key, ``mysql -pSECRET``, ``PASS=x``, a URL's password, webhook path and signature) masked;
    long key-like runs too, unless ``long_tokens`` is off (a title such as
    ``nightly-backup-of-the-photo-archive``)."""
    text = _mask_pem(text)  # review-fix R3a
    text = _URL_PASSWORD.sub(lambda m: f"{m.group(1)}…", text)  # review-fix R3a
    text = _WEBHOOK.sub(lambda m: f"{m.group(1)}…", text)  # review-fix R3a
    text = _SIGNED_QUERY.sub(lambda m: f"{m.group(1)}…", text)  # review-fix R3a
    text = _KNOWN_TOKEN.sub("…", text)  # review-fix R2a
    text = _mask_glued_password(text)  # review-fix R3a
    text = _PASS_PAIR.sub(lambda m: f"{m.group(1)}…", text)  # review-fix R3a
    text = _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}…", text)
    text = _SECRET_FLAG.sub(lambda m: f"{m.group(1)}{m.group(2)}…", text)  # review-fix R2a
    text = _BEARER.sub(lambda m: f"{m.group(1)} …", text)
    return _LONG_TOKEN.sub("…", text) if long_tokens else text


def excerpt(text: Any, limit: int = EXCERPT_LIMIT) -> str:
    """The first non-empty line of ``text``, secrets masked, cut to ``limit`` characters."""
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        flat = mask_secrets(" ".join(line.split()))
        if flat:
            return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"
    return ""


def folder_path(path: Any) -> str:
    """The session folder as given (for ``x-folder``), or ``""``."""
    if not isinstance(path, str) or not path.strip() or len(path) > FOLDER_LIMIT:
        return ""
    return path.strip()


def folder_name(path: Any) -> str:
    """Last part of a folder path from any OS (``""`` if none). A folder whose name starts with
    a date (``2026-03-14-plan-the-launch``, :data:`DATED`) never shows the words after it: tools
    make such folders per chat and write the chat's first words into them. It shows as its
    parent folder's name instead (one name for all of them), or the date alone when there is no
    parent to name (a bare name, or a parent that is the home folder or a root)."""
    if not isinstance(path, str) or not path.strip():
        return ""
    text = path.strip().rstrip("/\\")
    pure = PureWindowsPath(text) if ("\\" in text or text[1:2] == ":") else PurePosixPath(text)
    # --- ab: date-named folders ---
    dated = DATED.match(pure.name)
    if not dated:
        return pure.name
    from lamptown.status.events import home_or_root  # events imports this module

    parent = str(pure.parent)
    if pure.parent.name and parent != text and not home_or_root(parent):
        return folder_name(parent)  # a dated parent is named the same way
    return dated.group(1)
    # --- end ab ---


# --- ab: date-named folders --- a date, then a separator and more: the words are never shown
DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})[-_ .T].")
# --- end ab ---


# --- L5-fix: home folder ---
def project_folder(path: Any) -> str:
    """:func:`folder_name`, but ``""`` for a folder that names no project: the home folder, a
    drive or file-system root, or one above home (:func:`lamptown.status.events.home_or_root`),
    so a session started in ``~`` never shows the login name as its title or id."""
    from lamptown.status.events import home_or_root  # events imports this module

    return "" if home_or_root(path) else folder_name(path)
# --- end L5-fix ---


def make_id(agent: str, session: Any, folder: str = "") -> str:
    """``<agent>-<session>`` (first 12 id-safe characters), else ``<agent>-<folder>``."""
    for part in (session, folder):
        frag = slug(part, 12) if isinstance(part, str) else ""
        if frag:
            return f"{agent}-{frag}"
    return agent


def stop_update(base: Update, on_stop: str, last_text: Any, excerpts: bool = False) -> Update:
    """The end of a turn: ``ok`` by default, ``waiting`` + a need with ``--on-stop waiting``.
    The reply's first line is added only when excerpts are on."""
    line = excerpt(last_text) if excerpts else ""
    base.state = on_stop
    base.message = f"Turn finished: {line}" if line else "Turn finished"
    base.needs_you = [{"text": "Your turn"}] if on_stop == "waiting" else []
    return base


def apply(
    update: Update,
    directory: str | os.PathLike | None = None,
    *,
    id_override: str | None = None,
    title_override: str | None = None,
    stale_after: int | None = None,
) -> dict:
    """Write ``update`` into its status file (merging with what is there)."""
    changes: dict[str, Any] = {"agent": update.agent}
    status_id = id_override or update.id
    # review-fix B1: the folder's name (or the provider's) names a record only when the hook
    # creates it; a title already there (a reader's session title) is never overwritten, so
    # the two don't take turns. Only ``--title`` sets it every time.
    # review-fix R2a: the kind too: a hook makes a new record a task, but never turns a
    # session a reader called a job (a headless run) back into one
    defaults: dict[str, Any] = {"kind": "task"}
    if title_override:
        changes["title"] = title_override[:200]
    elif update.title or update.display:
        defaults["title"] = (update.title or update.display)[:200]
    if update.state:
        changes["state"] = update.state
    if update.message is not None:
        changes["message"] = update.message[:500]
    if update.needs_you is not None:
        changes["needs_you"] = update.needs_you[:20]
    if update.session_start:
        changes["started"] = iso(utcnow())
    if update.folder_path:
        changes["x-folder"] = update.folder_path
    if stale_after is not None:
        changes["stale_after"] = stale_after
    changes.update(update.extra)
    return update_status(status_id, directory, defaults=defaults, **changes)


def get(provider: str):
    """Return the adapter module for ``provider`` (raises ``KeyError``)."""
    from lamptown.status.adapters import claude_code, codex, cursor, gemini_cli

    table = {
        "claude-code": claude_code, "claude": claude_code,
        "codex": codex,
        "gemini-cli": gemini_cli, "gemini": gemini_cli,
        "cursor": cursor,
    }
    return table[provider]


PROVIDERS = ("claude-code", "codex", "gemini-cli", "cursor")
