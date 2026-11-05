"""GitHub CI reader: a pushed project's CI runs, from ``gh run list`` (optional, off by default).

Built-in kind ``ci_gh``. It runs only when it is configured::

    [[live.reader]]
    kind = "ci_gh"
    repos = ["~/code/paper-kite"]    # optional: folders to watch besides the live agents' ones
    status_dir = "~/.agent-status"   # optional: also watch the live agents' folders (the live
                                     # town passes its own status folder when none is named;
                                     # status_dir = false keeps it to repos)
    gh = "gh"                        # optional: the GitHub CLI to run (a name on PATH or an
                                     # absolute path)
    every = 120                      # optional: seconds between checks of one repository (120+)
    timeout = 20                     # optional: seconds one gh call may take (5 to 60)
    hosts = ["github.com"]           # optional: the GitHub hosts whose remotes count

It needs the GitHub CLI (``gh``), logged in (``gh auth login``). Without it, or offline, the
reader says so once (in the Watcher's notes) and shows nothing; the rest of the town goes on.

**When it calls ``gh``.** Only for a repository with a push in the last hour, read from the
push lines of its reflogs (``<git dir>/logs/refs/remotes/<remote>/...``: the same lines the
``git`` reader counts, with no ``git`` process), and only when the remote that push went to is
on GitHub (``[remote "<name>"] url`` in the repository's ``config``). At most once every
``every`` seconds per repository, one call at a time, never while the last one still runs. A
run of the pushed commit that is still going is followed until it ends (at most :data:`FOLLOW`
after the push), even past the hour. A finished result is checked once more, then the reader
stops calling until the next push. The call is a fixed, read-only argument list (no shell)::

    gh run list -R <owner>/<repo> --limit 20 --json databaseId,headSha,status,conclusion,
        workflowName,createdAt,updatedAt

It runs on a background thread with a ``timeout``, so a slow network never holds up the tick:
its answer is taken on the next poll. No console window opens on Windows.

**What it sends.** One status record per repository: id ``ci-gh-`` + a hash of its folder,
tool ``ci``, kind ``job``, plot ``boathouse`` (CI belongs to the project's berth), the project
from the folder's name (none for a repository in the home folder or at a root), the title the
workflow's name. Of the runs of the last pushed commit (the newest run per workflow; cancelled,
skipped and neutral runs don't count): any failed (``failure``, ``timed_out``,
``startup_failure``) is ``x-ci = "red"`` and state ``error``; else any queued or running is
``"running"`` and ``working``; else all passed is ``"green"`` and ``ok``. The Watcher turns the
record's changes into the book's events (start, CI green / CI red, done, failed). While a run
goes on the record is confirmed at every check, stamped when ``gh`` looked; a finished result is
stamped when the run ended, but never before the last confirmation (or the Watcher would hold
it back). If ``gh`` stops answering (asleep, offline) the record keeps "running" and the run is
asked about again once it answers; one never confirmed again goes quiet after :data:`STALE`
(the town's default ``quiet_after``), never ``error``: CI not seen to fail never reads as
failed.

Never kept or sent: logs, commit messages, branch names, run URLs, owner or user names, the
remote's URL, ``gh``'s own error text. The ``owner/repo`` name is used for the call only, in
memory. The only saved state is the runs last sent as going (``{"open": {record id: {"at": last
stamp}}}``: no path), so a restarted reader follows them on, even past the hour (it finds the
repository again from that record's own ``x-folder`` in the status folder, or from ``repos``);
everything else (when it last called, what it last sent) is in memory.

Private folders (``private_dirs``, passed by the live town): a repository in or under one is
never read, never asked about (no ``gh`` call) and never named in a note. The status folder is
listed newest record first, so a folder with more than :data:`MAX_STATUS_FILES` records still
finds the live agents.

``gh`` is only ever run from an absolute path (:func:`find_gh`): never from the current folder,
which ``shutil.which`` searches first on Windows.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from lamptown.status.adapters import project_folder
from lamptown.status.collector import _norm
from lamptown.status.events import path_like
from lamptown.status.readers import Batch
from lamptown.status.readers.git import (
    find_repo,
    hidden,
    newest_first,
    parse_line,
    private_roots,
)
from lamptown.status.schema import parse_time
from lamptown.status.store import StatusError, default_dir, iso, read_status

TOOL = "ci"  # the page draws no Lamplighter for this tool: CI is the berth's pennant
ID_PREFIX = "ci-gh-"
DISPLAY = "CI"  # a workflow with no name
PLOT = "boathouse"
EVERY = 120  # seconds: one repository is checked at most this often
WINDOW = timedelta(hours=1)  # a push this recent makes a repository worth a call
FOLLOW = timedelta(hours=6)  # a running run is followed this long after its push (GitHub's cap)
LIVE_FOR = timedelta(hours=1)  # an agent record changed this recently names a folder to watch
TIMEOUT = 20
# seconds a running record may go unconfirmed (gh not answering: asleep, offline, the town
# stopped) before it ages. As long as the town's ``quiet_after`` (6 h by default), so an
# unconfirmed run goes quiet ("unknown"), never error: CI that was not seen to fail never
# reads as failed.
STALE = int(FOLLOW.total_seconds())
RESCAN = 30  # seconds: a repository with nothing to check has its push reflogs looked at this often
LIMIT = 20  # runs asked for per call
FIELDS = "databaseId,headSha,status,conclusion,workflowName,createdAt,updatedAt"
MAX_REPOS = 50
MAX_REMOTE_LOGS = 200
MAX_STATUS_FILES = 2000
TAIL_BYTES = 16 * 1024  # how much of a remote reflog's end is read
MAX_OUT = 1024 * 1024  # gh's answer, at most
REFIND = timedelta(minutes=5)  # how often a folder's repository (and its remotes) is looked up
RUNNING = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})
GREEN = frozenset({"success"})
RED = frozenset({"failure", "timed_out", "startup_failure"})
# how long to wait after a failed call (seconds)
BACK_OFF = {"missing": 600, "auth": 600, "offline": 300, "rate": 900, "repo": 3600,
            "timeout": 300, "failed": EVERY, "empty": 900}
NOTES = {
    "missing": "GitHub CLI (gh) not found: CI runs are not shown "
               "(install gh and run gh auth login)",
    "auth": "gh is not logged in: CI runs are not shown (run gh auth login)",
    "offline": "gh can't reach GitHub (offline?): CI runs are not shown until it can",
    "rate": "GitHub's API rate limit was hit: CI checks pause for 15 minutes",
    "timeout": "gh took longer than {timeout} s: CI checks pause for 5 minutes",
}
GLOBAL = ("missing", "auth", "offline", "rate", "timeout")  # problems that hold every repository
_SLUG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})$")
_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})$")
_RID = re.compile(r"^ci-gh-[0-9a-f]{10}$")  # fix-PRIV: a record id, as _id_for makes it
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SECTION = re.compile(r'^\s*\[\s*remote\s+"([^"]+)"\s*\]')
_URL = re.compile(r"^\s*url\s*=\s*(.+?)\s*$", re.IGNORECASE)


@dataclass
class Answer:
    """What one ``gh`` call gave: its exit code and output (``error`` for a call that never
    ran or ran out of time: ``"missing"`` or ``"timeout"``)."""

    code: int = 0
    out: bytes = b""
    err: bytes = b""
    error: str = ""


Runner = Callable[[list[str], float], Answer]


def run_gh(argv: list[str], timeout: float) -> Answer:
    """Run ``argv`` (no shell, no input, no window) and wait at most ``timeout`` seconds."""
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1", NO_COLOR="1",
               GH_SPINNER_DISABLED="1", CLICOLOR="0")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, creationflags=flags)
    except FileNotFoundError:
        return Answer(error="missing")
    except OSError:
        return Answer(code=-1)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        with contextlib.suppress(Exception):
            proc.communicate(timeout=5)
        return Answer(error="timeout")
    return Answer(code=proc.returncode, out=out[:MAX_OUT], err=err[:8192])


def find_gh(name: str) -> str | None:
    """The absolute path of the ``gh`` to run, or ``None``.

    ``name`` is either an absolute path, or a bare name looked up in the absolute ``PATH``
    entries (with ``PATHEXT`` on Windows). Never the current folder (``shutil.which`` looks
    there first on Windows) and never a relative ``PATH`` entry or a relative path: the town
    may be started from inside an untrusted clone that carries its own ``gh.cmd``."""
    name = os.path.expanduser(name.strip())
    head, base = os.path.split(name)
    if head:
        if not os.path.isabs(head):
            return None
        folders = [head]
    else:
        folders = [d for d in (p.strip().strip('"')
                               for p in os.environ.get("PATH", "").split(os.pathsep))
                   if d and os.path.isabs(d)]
    if not base or base in (".", ".."):
        return None
    names = [base]
    if os.name == "nt":
        exts = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
        if not any(base.lower().endswith(e.lower()) for e in exts):
            names = [base + e.lower() for e in exts]
    for folder in folders:
        for candidate in names:
            path = os.path.normpath(os.path.join(folder, candidate))
            if os.path.isfile(path) and (os.name == "nt" or os.access(path, os.X_OK)):
                return path
    return None


def make_reader(options: dict) -> CiGhReader:
    repos = options.get("repos", options.get("repo", []))
    hosts = options.get("hosts", ["github.com"])
    return CiGhReader(repos=[repos] if isinstance(repos, str) else list(repos or []),
                      status_dir=options.get("status_dir"), gh=options.get("gh", "gh"),
                      every=options.get("every", EVERY), timeout=options.get("timeout", TIMEOUT),
                      hosts=[hosts] if isinstance(hosts, str) else list(hosts or []),
                      private_dirs=options.get("private_dirs"),  # fix-PRIV
                      config_dir=options.get("config_dir"))


# ---------------------------------------------------------------- the repository's side
def github_slug(url: str, hosts: tuple[str, ...]) -> str:
    """``owner/repo`` (``host/owner/repo`` for a host other than github.com) of a GitHub remote
    URL, or ``""``. ``https://github.com/o/r(.git)``, the scp form ``git@<host>:o/r`` and
    ``ssh://git@<host>/o/r`` all count; anything else, or odd characters, does not."""
    text = url.strip()
    m = re.match(r"^(?:[a-z+]+://)?(?:[^@/\s]+@)?([^/:\s]+)(?::\d+)?[:/]+(.*)$", text, re.I)
    if not m:
        return ""
    host, rest = m.group(1).lower(), m.group(2)
    if host not in hosts or not _HOST.match(host):
        return ""
    parts = [p for p in rest.split("/") if p]
    if len(parts) != 2:
        return ""
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not (_SLUG.match(owner) and _SLUG.match(repo)) or repo in (".", ".."):
        return ""
    return f"{owner}/{repo}" if host == "github.com" else f"{host}/{owner}/{repo}"


def remotes(common: str, hosts: tuple[str, ...]) -> dict[str, str]:
    """``{remote name: owner/repo}`` of the GitHub remotes in ``<common>/config``."""
    try:
        with open(os.path.join(common, "config"), "rb") as fh:
            text = fh.read(64 * 1024).decode("utf-8", "replace")
    except OSError:
        return {}
    out: dict[str, str] = {}
    name = None
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            m = _SECTION.match(line)
            name = m.group(1) if m else None
            continue
        if name is None or name in out:
            continue
        m = _URL.match(line)
        if m:
            slug = github_slug(m.group(1).strip('"'), hosts)
            if slug:
                out[name] = slug
    return out


@dataclass(frozen=True)
class Push:
    when: datetime
    sha: str
    remote: str


def last_push(common: str, oldest: datetime) -> Push | None:
    """The newest ``update by push`` line at or after ``oldest`` in the repository's remote
    reflogs, read from their ends (a log not changed since ``oldest`` is only ``stat``-ed)."""
    base = os.path.join(common, "logs", "refs", "remotes")
    try:
        names = sorted(e.name for e in os.scandir(base) if e.is_dir(follow_symlinks=False))
    except OSError:
        return None
    since = oldest.timestamp()
    best: Push | None = None
    seen = 0
    for remote in names:
        stack = [os.path.join(base, remote)]
        while stack and seen < MAX_REMOTE_LOGS:
            try:
                entries = list(os.scandir(stack.pop()))
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                    continue
                if not entry.is_file(follow_symlinks=False) or seen >= MAX_REMOTE_LOGS:
                    continue
                seen += 1
                found = _newest_push(entry.path, since)
                if found and (best is None or found[0] > best.when):
                    best = Push(when=found[0], sha=found[1], remote=remote)
    return best


def _newest_push(path: str, since: float) -> tuple[datetime, str] | None:
    try:
        st = os.stat(path)
        if st.st_mtime < since:
            return None  # not written since: no recent line in it
        with open(path, "rb") as fh:
            start = max(0, st.st_size - TAIL_BYTES)
            fh.seek(start)
            data = fh.read(TAIL_BYTES)
    except OSError:
        return None
    lines = data.split(b"\n")
    if start:
        lines = lines[1:]  # cut mid-line
    best = None
    for raw in lines:
        parsed = parse_line(raw)
        if parsed is None or raw.partition(b"\t")[2].strip() != b"update by push":
            continue
        _old, new, when, _lead = parsed
        if not new.strip("0") or when < since or not _SHA.match(new):
            continue  # a deleted branch, or too old
        moment = datetime.fromtimestamp(when, UTC)
        if best is None or moment >= best[0]:
            best = (moment, new)
    return best


# ---------------------------------------------------------------- gh's answer
def classify(answer: Answer) -> str:
    """``""`` for a good answer, else why it failed: one of :data:`BACK_OFF`'s keys."""
    if answer.error:
        return answer.error
    if answer.code == 0:
        return ""
    err = answer.err.decode("utf-8", "replace").lower()
    if answer.code == 4 or "gh auth login" in err or "not logged" in err \
            or "authentication" in err or "http 401" in err:
        return "auth"
    if "rate limit" in err:
        return "rate"
    if any(w in err for w in ("could not resolve to a repository", "http 404", "not found")):
        return "repo"
    if any(w in err for w in ("error connecting", "dial tcp", "no such host",
                              "could not resolve host", "connection", "network",
                              "timeout", "timed out", "tls")):
        return "offline"
    return "failed"


def runs_of(answer: Answer, sha: str) -> list[dict] | None:
    """The runs of commit ``sha`` in a good answer (``None``: not a run list)."""
    try:
        data = json.loads(answer.out.decode("utf-8", "replace") or "[]")
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    return [r for r in data if isinstance(r, dict) and r.get("headSha") == sha]


def verdict(runs: list[dict]) -> tuple[str, dict] | None:
    """``(ci, deciding run)`` of one commit's runs (the newest run per workflow), or ``None``
    when no run counts yet. ``ci`` is ``red``, ``running`` or ``green``."""
    def created(r: dict) -> str:
        return str(r.get("createdAt") or "")

    def updated(r: dict) -> str:
        return str(r.get("updatedAt") or r.get("createdAt") or "")

    newest: dict[str, dict] = {}
    for r in runs:
        wf = str(r.get("workflowName") or "")
        key = wf or str(r.get("databaseId"))
        if key not in newest or created(r) > created(newest[key]):
            newest[key] = r
    groups: dict[str, list[dict]] = {"red": [], "running": [], "green": []}
    for r in newest.values():
        status, conclusion = r.get("status"), r.get("conclusion")
        if status in RUNNING:
            groups["running"].append(r)
        elif status == "completed" and conclusion in RED:
            groups["red"].append(r)
        elif status == "completed" and conclusion in GREEN:
            groups["green"].append(r)
    for ci in ("red", "running", "green"):
        if groups[ci]:
            return ci, max(groups[ci], key=updated)
    return None


# ---------------------------------------------------------------- the reader
@dataclass
class _Repo:
    """What the reader knows about one repository (memory only)."""

    root: str
    common: str
    slugs: dict[str, str]
    found: datetime
    rid: str = ""
    push: Push | None = None
    next: datetime | None = None  # no call before this
    sha: str = ""  # the commit the result below is for
    ci: str = ""  # its last verdict
    finals: int = 0  # answers in a row that said it had finished
    sent: tuple = ()
    said: set[str] = field(default_factory=set)


class CiGhReader:
    """CI runs of recently pushed repositories (see the module docstring)."""

    name = "ci_gh"

    def __init__(self, *, repos: list[Any] | None = None, status_dir: Any = None, gh: Any = "gh",
                 every: Any = EVERY, timeout: Any = TIMEOUT, hosts: list[Any] | None = None,
                 runner: Runner | None = None, background: bool = True,
                 private_dirs: Any = None, config_dir: Any = None) -> None:
        # --- fix-PRIV: a private project is never read, asked about or named in a note ---
        self.private = private_roots(private_dirs, config_dir)
        self.repos = [p for p in (_expand(r) for r in repos or []) if p
                      and not hidden(self.private, p)]
        self.status_dir = default_dir(status_dir) if status_dir else None
        if self.status_dir is not None and hidden(self.private, str(self.status_dir)):
            self.status_dir = None
        # --- end fix-PRIV ---
        self.gh = gh if isinstance(gh, str) and gh.strip() else "gh"
        self.every = _number(every, EVERY, EVERY, 3600)
        self.timeout = _number(timeout, TIMEOUT, 5, 60)
        self.hosts = tuple(h.strip().lower() for h in (hosts or ["github.com"])
                           if isinstance(h, str) and _HOST.match(h.strip())) or ("github.com",)
        self.runner = runner or run_gh
        self.background = background
        self.notes: list[str] = []  # read by the Watcher after each poll (short, safe)
        self.calls = 0  # gh calls started (tests and doctor)
        self._known: dict[str, _Repo] = {}  # by the common git dir
        self._folders: dict[str, tuple[str | None, datetime]] = {}  # folder -> common dir
        self._records: dict[str, tuple[list[int], Any]] = {}  # status file -> _summary
        self._held: tuple[str, datetime] | None = None  # (problem, until): every repo waits
        self._said: set[str] = set()
        self._job: tuple[str, str, threading.Thread | None, list, datetime] | None = None
        self._exe: tuple[str | None, datetime] | None = None
        self._open: dict[str, dict] = {}  # record id -> {at, root}: runs last seen going

    # ------------------------------------------------------------ folders
    def _live_folders(self, now: datetime) -> list[str]:
        if self.status_dir is None:
            return []
        entries = newest_first(self.status_dir, MAX_STATUS_FILES)  # fix-PRIV: before the cap
        out: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            if not entry.name.endswith(".json") or entry.name.startswith((".", ID_PREFIX)):
                continue
            seen.add(entry.name)
            try:
                st = entry.stat()
            except OSError:
                continue
            sig = [st.st_mtime_ns, st.st_size]
            cached = self._records.get(entry.name)
            if cached is not None and cached[0] == sig:
                summary = cached[1]
            else:
                try:
                    summary = _summary(read_status(entry.path))
                except StatusError:
                    summary = None
                self._records[entry.name] = (sig, summary)
            folder = _live_folder(summary, now)
            if folder and not (summary and hidden(self.private, folder, summary[3])):  # fix-PRIV
                out.append(folder)
        for name in [n for n in self._records if n not in seen]:
            del self._records[name]
        return out

    def _repo_for(self, folder: str, now: datetime) -> _Repo | None:
        known = self._folders.get(folder)
        if known is not None and now - known[1] < REFIND:
            return self._known.get(known[0]) if known[0] else None
        repo = find_repo(folder, private=self.private)  # fix2-PRIV
        if repo is None:
            self._folders[folder] = (None, now)
            return None
        key = _norm(repo.common)
        root = os.path.dirname(repo.common) if os.path.basename(repo.common) == ".git" \
            else repo.root
        mine = self._known.get(key)
        if mine is None or now - mine.found >= REFIND:
            slugs = remotes(repo.common, self.hosts)
            if mine is None:
                mine = self._known[key] = _Repo(root=root, common=repo.common, slugs=slugs,
                                                found=now, rid=_id_for(root))
            else:
                mine.slugs, mine.found, mine.root = slugs, now, root
        self._folders[folder] = (key, now)
        return mine

    # --- fix-PRIV: a followed run's folder, from the record this reader sent for it ---
    def _record_folder(self, rid: str) -> str:
        """The repository folder of the CI record ``rid`` in the status folder (its
        ``x-folder``), when that folder still hashes to ``rid`` and is not private; else ``""``."""
        if self.status_dir is None:
            return ""
        try:
            doc = read_status(os.path.join(self.status_dir, rid + ".json"))
        except StatusError:
            return ""
        folder = doc.get("x-folder") if isinstance(doc, dict) else None
        if not (isinstance(folder, str) and folder and os.path.isabs(folder)):
            return ""
        folder = os.path.normpath(folder)
        if _id_for(folder) != rid or hidden(self.private, folder):
            return ""
        return folder
    # --- end fix-PRIV ---

    # ------------------------------------------------------------ poll
    def poll(self, state: dict, now: datetime, budget: Any = None) -> Batch:
        now = now.astimezone(UTC)
        self.notes = []
        batch = Batch()
        self._open = _open_runs(state.get("open"), now)
        self._collect(now, batch)
        folders = [*self.repos, *self._live_folders(now)]
        # a run last seen going (by this reader, or by one before a restart) is followed on:
        # --- fix-PRIV: its folder is found again from its own record (the saved state keeps
        # only the record id, never a path) ---
        known = {r.rid for r in self._known.values()}
        for rid in self._open:
            root = "" if rid in known else self._record_folder(rid)
            if root and root not in folders:
                folders.append(root)
        # --- end fix-PRIV ---
        used: set[str] = set()
        repos: dict[str, _Repo] = {}
        for folder in folders:
            if len(repos) >= MAX_REPOS:
                break
            used.add(folder)
            repo = self._repo_for(folder, now)
            if repo is not None:
                repos.setdefault(_norm(repo.common), repo)
        for folder in [f for f in self._folders if f not in used]:
            del self._folders[folder]
        for key, repo in self._known.items():  # a run still going is followed to its end
            if key not in repos and repo.ci == "running" and repo.push is not None \
                    and now - repo.push.when <= FOLLOW:
                repos[key] = repo
        for key in [k for k in self._known if k not in repos
                    and not (self._job and self._job[0] == k)]:
            del self._known[key]
        if self._job is None:
            due = [r for r in repos.values() if self._due(r, now)]
            if due:
                self._start(min(due, key=lambda r: (r.next or now, r.root)), now, batch)
        if self._open:  # the only saved state: the runs last seen going
            state["open"] = self._open
        else:
            state.pop("open", None)
        return batch

    def _due(self, repo: _Repo, now: datetime) -> bool:
        if repo.next is not None and now < repo.next:
            return False
        if self._held is not None:  # every repository waits: no reflog read either
            if now < self._held[1]:
                return False
            self._held = None
        push = last_push(repo.common, now - FOLLOW)
        repo.push = push
        rest = now + timedelta(seconds=RESCAN)  # nothing to check: look again in a while
        if push is None:
            repo.next = rest
            return False
        going = (push.sha == repo.sha and repo.ci == "running") or repo.rid in self._open
        if now - push.when > WINDOW and not (going and now - push.when <= FOLLOW):
            repo.next = rest
            return False
        if push.sha == repo.sha and repo.finals >= 2:
            repo.next = rest
            return False  # its result is in (and was checked once more)
        slug = repo.slugs.get(push.remote)
        if not slug:
            self._say(repo, f"remote:{push.remote}", f"{_name(repo)}: pushed to a remote "
                      "that is not on GitHub: its CI is not shown")
            repo.next = now + timedelta(seconds=self.every)
            return False
        return True

    def _start(self, repo: _Repo, now: datetime, batch: Batch) -> None:
        exe = self._gh(now)
        if exe is None:
            self._hold("missing", now)
            return
        push = repo.push
        if push is None:
            return
        slug = repo.slugs[push.remote]
        argv = [exe, "run", "list", "-R", slug, "--limit", str(LIMIT), "--json", FIELDS]
        repo.next = now + timedelta(seconds=self.every)
        self.calls += 1
        box: list = []
        key = _norm(repo.common)
        if not self.background:
            box.append(self._call(argv))
            self._job = (key, push.sha, None, box, now)
            self._collect(now, batch)
            return
        thread = threading.Thread(target=lambda: box.append(self._call(argv)),
                                  name="lamptown-ci-gh", daemon=True)
        self._job = (key, push.sha, thread, box, now)  # now: when gh looks
        thread.start()

    def _call(self, argv: list[str]) -> Answer:
        try:
            answer = self.runner(argv, self.timeout)
        except FileNotFoundError:
            return Answer(error="missing")
        except subprocess.TimeoutExpired:
            return Answer(error="timeout")
        except Exception:  # a broken runner: one failed call, never a crash
            return Answer(code=-1)
        return answer if isinstance(answer, Answer) else Answer(code=-1)

    def _collect(self, now: datetime, batch: Batch) -> None:
        if self._job is None:
            return
        key, sha, thread, box, asked = self._job
        if thread is not None and thread.is_alive():
            return
        self._job = None
        repo = self._known.get(key)
        answer = box[0] if box else Answer(code=-1)
        why = classify(answer)
        if repo is None:
            return
        if why in GLOBAL:
            self._hold(why, now)
            repo.next = max(repo.next or now, self._held[1] if self._held else now)
            return
        if why:
            self._say(repo, why, f"{_name(repo)}: gh could not list its CI runs"
                      + (" (no such repository, or no access)" if why == "repo" else ""))
            repo.next = now + timedelta(seconds=BACK_OFF[why])
            return
        for kind in GLOBAL:  # it answered: those problems are over
            self._said.discard(kind)
        repo.said.clear()
        runs = runs_of(answer, sha)
        if runs is None:
            self._say(repo, "failed", f"{_name(repo)}: gh's answer was not a run list")
            return
        found = verdict(runs)
        if found is None:
            if not answer.out.strip(b" \r\n[]"):  # no runs at all: maybe no workflows
                repo.next = now + timedelta(seconds=BACK_OFF["empty"])
            return
        ci, run = found
        if sha != repo.sha:
            repo.sha, repo.finals = sha, 0
        repo.ci = ci
        repo.finals = repo.finals + 1 if ci != "running" else 0
        change = self._change(repo, ci, run, runs, asked=asked, now=now)
        sig = tuple(sorted((k, str(v)) for k, v in change.items() if k != "at"))
        if ci == "running" or sig != repo.sent:
            repo.sent = sig
            batch.agents.append(change)
        if ci == "running":  # remembered (saved state, no path: fix-PRIV) until its result is in
            self._open[repo.rid] = {"at": iso(change["at"])}
        else:
            self._open.pop(repo.rid, None)

    def _change(self, repo: _Repo, ci: str, run: dict, runs: list[dict], *, asked: datetime,
                now: datetime) -> dict:
        starts = [t for t in (parse_time(r.get("createdAt")) for r in runs) if t is not None]
        if ci == "running":
            at = asked  # when gh looked (its answer may be collected a poll later)
        else:  # when the run ended (GitHub's clock), but never before the last "running"
            # stamp: an older change would be held back by the Watcher, leaving the record
            # "working" until it aged
            at = min(parse_time(run.get("updatedAt")) or now, now)
            last = self._open.get(repo.rid)
            last_at = parse_time(last["at"]) if last else None
            if last_at is not None and at <= last_at:
                at = min(last_at + timedelta(seconds=1), now)
        state = {"running": "working", "green": "ok", "red": "error"}[ci]
        change: dict[str, Any] = {
            "id": repo.rid, "at": at,
            "title": str(run.get("workflowName") or "") or DISPLAY, "agent": TOOL, "kind": "job",
            "state": state, "x-ci": ci, "x-folder": repo.root, "x-plot": PLOT,
            "stale_after": STALE,
        }
        if starts:
            change["started"] = iso(min(*starts, now))
        return change

    # ------------------------------------------------------------ problems
    def _gh(self, now: datetime) -> str | None:
        if self._exe is not None and (self._exe[0] or now - self._exe[1] < timedelta(
                seconds=BACK_OFF["missing"])):
            return self._exe[0]
        found = find_gh(self.gh)
        self._exe = (found, now)
        return found

    def _hold(self, why: str, now: datetime) -> None:
        self._held = (why, now + timedelta(seconds=BACK_OFF[why]))
        if why == "missing":
            self._exe = None if self._exe is None else (None, now)
        if why not in self._said:
            self._said.add(why)
            self.notes.append(NOTES[why].format(timeout=self.timeout))

    def _say(self, repo: _Repo, key: str, text: str) -> None:
        if key not in repo.said:
            repo.said.add(key)
            self.notes.append(text[:200])


# ---------------------------------------------------------------- helpers
def _expand(path: Any) -> str:
    if not isinstance(path, str | os.PathLike) or not str(path).strip():
        return ""
    return os.path.normpath(os.path.expanduser(os.path.expandvars(str(path).strip())))


def _number(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return int(min(max(value, low), high))


def _id_for(root: str) -> str:
    return ID_PREFIX + hashlib.sha256(_norm(root).encode("utf-8", "replace")).hexdigest()[:10]


def _name(repo: _Repo) -> str:
    return project_folder(repo.root) or "a repository"


def _open_runs(saved: Any, now: datetime) -> dict[str, dict]:
    """The runs last sent as going, from the saved state (``{record id: {"at": its last
    stamp}}``): well-formed ones of the last :data:`FOLLOW` only, at most :data:`MAX_REPOS`.
    A ``"root"`` an older version saved is dropped (fix-PRIV: no path is saved)."""
    out: dict[str, dict] = {}
    if not isinstance(saved, dict):
        return out
    for rid, entry in saved.items():
        if len(out) >= MAX_REPOS:
            break
        # fix-PRIV: only an id this reader makes (it names a status file to read)
        if not (isinstance(rid, str) and _RID.match(rid) and isinstance(entry, dict)):
            continue
        at = parse_time(entry.get("at"))
        if at is None or now - at > FOLLOW:
            continue
        out[rid] = {"at": iso(at)}
    return out


def _summary(doc: Any) -> tuple[str, str, datetime | None, str] | None:
    """``(folder, state, updated, x-project when it is a path)`` of an agent record with an
    absolute working folder, or ``None``. CI records and project records name none."""
    if not isinstance(doc, dict):
        return None
    folder = doc.get("x-folder")
    if not isinstance(folder, str) or not folder or not os.path.isabs(folder):
        return None  # relative: it would name the Watcher's own folder
    if doc.get("kind") == "project" or doc.get("agent") == TOOL:
        return None
    project = doc.get("x-project")
    return (os.path.normpath(folder), str(doc.get("state") or ""),
            parse_time(doc.get("updated")), str(project) if path_like(project) else "")


def _live_folder(summary: tuple[str, str, datetime | None, str] | None,
                 now: datetime) -> str | None:
    """The folder of a live agent (working, waiting, or changed within :data:`LIVE_FOR`)."""
    if summary is None:
        return None
    folder, state, updated, _project = summary
    if state not in ("working", "waiting") and (updated is None or now - updated > LIVE_FOR):
        return None
    return folder
