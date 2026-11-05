"""Git reader: commits and pushes from a repository's reflogs, with no ``git`` process.

Built-in kind ``git``::

    [[live.reader]]
    kind = "git"
    repos = ["~/code/paper-kite", "~/code/harbour"]   # the folders to watch
    status_dir = "~/.agent-status"   # optional (default in the live town: the Watcher's own
                                     # status folder; false: none): also watch the live agents'
                                     # folders, and count their commits and pushes on their records
    days = 7                         # optional: older reflog lines are skipped

Which folders: every ``repos`` entry, plus, when ``status_dir`` is given, the ``x-folder`` of
every live agent record there (``working`` or ``waiting``, or changed in the last 30 minutes).
The reader contract hands a reader no list of folders, so the Watcher's own folder has to come
in through ``status_dir``: the live town passes it when the entry names none
(``livetown.reader_entry``; ``status_dir = false`` turns that off). A folder may be a
sub-folder of its repository: its parents are searched for ``.git`` (at most :data:`UP`
levels, ``repos`` entries included). The status folder is listed newest record first, so a
folder holding more than :data:`MAX_STATUS_FILES` records still finds the live ones.

Private folders (``private_dirs``, given by the live town from its config): a ``repos`` entry
or a live agent's folder (or its ``x-project``, when that is a path) in or under one is never
opened, so nothing of a private project is read; with the status folder itself private, no
live agent's folder is.

What it reads (read-only, tailed incrementally like every reader):

* ``<git dir>/logs/HEAD``: lines whose message starts ``commit``, ``commit (initial)``,
  ``commit (merge)``, ``cherry-pick`` or ``revert``, and ``merge ...`` lines that made a merge
  commit (not a fast-forward) are commits. ``commit (amend)`` replaces a commit: not counted.
* ``<common dir>/logs/refs/remotes/<remote>/<branch>``: lines whose message is
  ``update by push`` are pushes (a deleted branch is not).

Only the two ids, the time and the message's first word are looked at; author names, e-mails
and commit messages are never kept or sent. A worktree or submodule (a ``.git`` *file* saying
``gitdir: ...``) reads its own ``HEAD`` log and the shared remote logs of its common dir
(``commondir``); a push goes to the worktree that has that branch checked out. A missing
reflog is no error. When ``git gc`` / ``reflog expire`` rewrites a reflog, or a log comes back
into view (an agent live again after a quiet spell), the lines already seen (by time and id)
are not counted again: a log's high-water mark is kept until it is older than ``days`` (at most
:data:`MAX_HW` of them). A line with a time past the year 3000 is a broken line, skipped. An
agent folder that is not an absolute path is never looked at. At most ``MAX_EVENTS - ROOM``
events per poll; the rest come next poll.

What it sends: one ``commit`` or ``push`` event per line, always with ``folder`` (the
worktree), so ``private_dirs`` can hide it. When a live agent works in that folder (and the
line is newer than the agent's start), the event carries that agent's id and the agent's
record gets ``x-commits`` (commits counted while it was live) and ``x-pushed``. Otherwise the
event's id is ``git-`` + a hash of the folder, its title the project folder's name (``Git`` for
a repository in the home folder or a root: never a login name).
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from lamptown.status.adapters import project_folder
from lamptown.status.collector import _norm, _private_roots, _under
from lamptown.status.events import path_like
from lamptown.status.readers import MAX_AGENTS, MAX_EVENTS, Batch
from lamptown.status.readers.tail import Budget, Line, cursor_key, forget, start_at, tail
from lamptown.status.schema import parse_time
from lamptown.status.store import StatusError, default_dir, read_status

TOOL = "git"
DAYS = 7
UP = 4  # parent levels searched for .git
MAX_REPOS = 100
MAX_REMOTE_LOGS = 200  # remote-branch reflogs per repository
MAX_STATUS_FILES = 2000
LIVE_FOR = timedelta(minutes=30)  # a record changed this recently counts as live
RECHECK = timedelta(seconds=60)  # how often a folder with no repository is looked at again
REFIND = timedelta(minutes=5)  # how often a found repository is looked for again
HW_KEEP = 20  # ids kept for the newest second of a reflog
MAX_HW = 4 * MAX_REPOS  # high-water marks kept for logs no longer watched
MAX_TIME = 32503680000  # 3000-01-01: a later reflog time is a broken line
ROOM = 500  # events kept free under MAX_EVENTS
COMMIT_WORDS = ("commit", "commit (initial)", "commit (merge)", "cherry-pick", "revert")
DISPLAY = "Git"
SMALL = Budget(total=256 * 1024, per_file=128 * 1024)


def make_reader(options: dict) -> GitReader:
    repos = options.get("repos", options.get("repo", []))
    return GitReader(repos=[repos] if isinstance(repos, str) else list(repos or []),
                     status_dir=options.get("status_dir"), days=options.get("days", DAYS),
                     private_dirs=options.get("private_dirs"),  # fix-PRIV
                     config_dir=options.get("config_dir"))


@dataclass(frozen=True)
class Repo:
    root: str  # the worktree folder (the event's ``folder``)
    gitdir: str  # its own git dir (HEAD, logs/HEAD)
    common: str  # the shared git dir (logs/refs/remotes)


def _expand(path: Any) -> str:
    if not isinstance(path, str | os.PathLike) or not str(path).strip():
        return ""
    return os.path.normpath(os.path.expanduser(os.path.expandvars(str(path).strip())))


# --- fix-PRIV: private folders and newest records first ---
def private_roots(private_dirs: Any, config_dir: Any = None) -> list[str]:
    """The private folders as compared text (the Watcher's rule): ``private_dirs`` (a path or a
    list), relative ones from ``config_dir``. The live town passes its own
    (``livetown.reader_entry``)."""
    raw = [private_dirs] if isinstance(private_dirs, str | os.PathLike) else private_dirs
    items = [str(p) for p in raw or [] if isinstance(p, str | os.PathLike) and str(p).strip()]
    if not items:
        return []
    base = str(config_dir) if isinstance(config_dir, str | os.PathLike) and str(config_dir) \
        else "."
    return _private_roots({"private_dirs": items, "_config_dir": base})


def hidden(roots: list[str], *paths: Any) -> bool:
    """One of ``paths`` (a folder, or ``x-project`` when it is a path) is in or under a private
    folder: such a project is never opened, read or asked about."""
    return bool(roots) and any(isinstance(p, str) and p and _under(p, roots) for p in paths)


def newest_first(folder: str | os.PathLike, limit: int) -> list[os.DirEntry]:
    """At most ``limit`` entries of ``folder``, most recently changed first (a folder with more
    records than the cap keeps the live ones). ``[]`` when it can't be listed."""
    def changed(entry: os.DirEntry) -> int:
        try:
            return entry.stat().st_mtime_ns
        except OSError:
            return 0
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    entries.sort(key=lambda e: e.name)
    entries.sort(key=changed, reverse=True)  # stable: same time keeps the name order
    return entries[:limit]
# --- end fix-PRIV ---


def _small(path: str, limit: int = 4096) -> str:
    """The first line of a small text file (``.git`` file, ``commondir``, ``HEAD``)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit)
    except OSError:
        return ""
    return data.decode("utf-8", "replace").splitlines()[0].strip() if data.strip() else ""


def find_repo(folder: str, up: int = UP, private: list[str] | None = None) -> Repo | None:
    """The repository ``folder`` is in (``folder`` itself or up to ``up`` parents).

    fix2-PRIV: ``private`` (roots as :func:`private_roots` gives them): a worktree or submodule
    whose ``.git`` file points into a private folder, or whose ``commondir`` does, is no
    repository here; nothing in the private folder is opened."""
    here = folder
    for _ in range(up + 1):
        dotgit = os.path.join(here, ".git")
        try:
            st = os.stat(dotgit)
        except OSError:
            st = None
        if st is not None:
            if stat.S_ISDIR(st.st_mode):
                gitdir = dotgit
            else:  # a worktree or submodule: "gitdir: <path>"
                text = _small(dotgit)
                if not text.lower().startswith("gitdir:"):
                    return None
                gitdir = os.path.normpath(os.path.join(here, text[7:].strip()))
                if private and hidden(private, gitdir):  # fix2-PRIV
                    return None
            rel = _small(os.path.join(gitdir, "commondir"))
            common = os.path.normpath(os.path.join(gitdir, rel)) if rel else gitdir
            if private and hidden(private, common):  # fix2-PRIV
                return None
            return Repo(root=here, gitdir=gitdir, common=common)
        parent = os.path.dirname(here)
        if not parent or parent == here:
            return None
        here = parent
    return None


def _id_for(root: str) -> str:
    return "git-" + hashlib.sha256(_norm(root).encode("utf-8", "replace")).hexdigest()[:10]


def parse_line(data: bytes) -> tuple[str, str, int, str] | None:
    """``(old id, new id, unix time, message's first words)`` of one reflog line, or ``None``.

    A line is ``<old> <new> <name> <<email>> <time> <zone>\\t<message>``; only the ids, the
    time and the message's lead (up to its ``:``) are taken."""
    left, tab, message = data.partition(b"\t")
    parts = left.split(b" ")
    if len(parts) < 4:
        return None
    old, new, when = parts[0], parts[1], parts[-2]
    if len(old) < 40 or len(new) < 40 or not when.isdigit() or int(when) > MAX_TIME:
        return None  # a time past the year 3000 is a broken line (and fails on Windows)
    try:
        lead = message.split(b":", 1)[0].decode("utf-8", "replace").strip() if tab else ""
        return old.decode("ascii"), new.decode("ascii"), int(when), lead
    except (UnicodeDecodeError, ValueError):
        return None


def _is_commit(lead: str, data: bytes) -> bool:
    if lead in COMMIT_WORDS:
        return True
    if lead.startswith("merge "):  # "merge <branch>: Merge made by ..." vs a fast-forward
        tail_part = data.partition(b"\t")[2].partition(b":")[2]
        return b"fast-forward" not in tail_part.lower()
    return False


def _is_push(data: bytes, new: str) -> bool:
    """An ``update by push`` line that did not delete the branch (new id all zeros)."""
    return data.partition(b"\t")[2].strip() == b"update by push" and bool(new.strip("0"))


class GitReader:
    """Commits and pushes from reflogs (see the module docstring)."""

    name = "git"

    def __init__(self, repos: list[Any] | None = None, status_dir: Any = None,
                 days: int = DAYS, private_dirs: Any = None, config_dir: Any = None) -> None:
        # --- fix-PRIV: a project in a private folder is never opened (nor its status folder's
        # agents, when the status folder itself is private) ---
        self.private = private_roots(private_dirs, config_dir)
        self.repos = [p for p in (_expand(r) for r in repos or []) if p
                      and not hidden(self.private, p)]
        self.status_dir = default_dir(status_dir) if status_dir else None
        if self.status_dir is not None and hidden(self.private, str(self.status_dir)):
            self.status_dir = None
        # --- end fix-PRIV ---
        self.days = days if isinstance(days, int) and 0 < days <= 31 else DAYS
        self._found: dict[str, tuple[Repo | None, datetime]] = {}  # folder -> repo (memory)
        self._records: dict[str, tuple[list[int], dict | None]] = {}  # status file cache

    # ------------------------------------------------------------ folders
    def _repo(self, folder: str, now: datetime) -> Repo | None:
        known = self._found.get(folder)
        if known is not None and now - known[1] < (RECHECK if known[0] is None else REFIND):
            return known[0]  # a found one is looked for again too: a nearer `git init`
        repo = find_repo(folder, private=self.private)  # fix2-PRIV
        self._found[folder] = (repo, now)
        return repo

    def _live(self, now: datetime) -> list[dict]:
        """Live agent records in ``status_dir``: ``{"id", "folder", "since", "updated"}``."""
        if self.status_dir is None:
            return []
        entries = newest_first(self.status_dir, MAX_STATUS_FILES)  # fix-PRIV: before the cap
        out: list[dict] = []
        seen: set[str] = set()
        for entry in entries:
            if not entry.name.endswith(".json") or entry.name.startswith("."):
                continue
            seen.add(entry.name)
            try:
                st = entry.stat()
            except OSError:
                continue
            sig = [st.st_mtime_ns, st.st_size]
            cached = self._records.get(entry.name)
            if cached is not None and cached[0] == sig:
                doc = cached[1]
            else:
                try:
                    raw = read_status(entry.path)
                except StatusError:
                    raw = None
                doc = _record(raw) if raw is not None else None
                self._records[entry.name] = (sig, doc)
            if doc is None or hidden(self.private, doc["folder"], doc["xp"]):  # fix-PRIV
                continue
            updated = parse_time(doc["updated"])
            if doc["state"] not in ("working", "waiting") and (
                    updated is None or now - updated > LIVE_FOR):
                continue
            out.append(doc)
        for name in [n for n in self._records if n not in seen]:
            del self._records[name]
        return out

    # ------------------------------------------------------------ poll
    def poll(self, state: dict, now: datetime, budget: Budget | None = None) -> Batch:
        budget = budget or Budget(SMALL.total, SMALL.per_file)
        now = now.astimezone(UTC)
        for key in ("files", "hw", "agents"):
            if not isinstance(state.get(key), dict):
                state[key] = {}
        cursors, hws, counts = state["files"], state["hw"], state["agents"]
        live = self._live(now)
        for agent in live:  # when the reader first saw it live: its start, if it has none
            mine = counts.get(agent["id"])
            if not isinstance(mine, dict):
                mine = counts[agent["id"]] = {"s": now.isoformat()}
            agent["since"] = agent["since"] or str(mine.get("s") or "")

        repos: dict[str, Repo] = {}  # by git dir
        used: set[str] = set()
        for folder in [*self.repos, *(a["folder"] for a in live)]:
            if len(repos) >= MAX_REPOS:
                break
            used.add(folder)
            repo = self._repo(folder, now)
            if repo is not None:
                repos.setdefault(_norm(repo.gitdir), repo)
        for folder in [f for f in self._found if f not in used]:
            del self._found[folder]  # only the folders in use now are remembered
        by_common: dict[str, list[Repo]] = {}
        for repo in repos.values():
            by_common.setdefault(_norm(repo.common), []).append(repo)

        batch = Batch()
        oldest = int((now - timedelta(days=self.days)).timestamp())
        watched: list[str] = []
        agent_changes: dict[str, dict] = {}

        def emit(repo: Repo, kind: str, when: int) -> None:
            moment = datetime.fromtimestamp(when, UTC)
            agent = _agent_for(repo, live, moment)
            event: dict[str, Any] = {"t": moment, "k": kind, "folder": repo.root}
            if agent is None:
                name = project_folder(repo.root)  # never the home folder's (login) name
                event.update(id=_id_for(repo.root), title=name or DISPLAY, project=name,
                             tool=TOOL, plot="boathouse")
            else:
                aid = agent["id"]
                event["id"] = aid  # names, tool and plot come from the agent's record
                mine = counts[aid]
                change = agent_changes.setdefault(aid, {"id": aid, "at": agent["updated"]})
                if kind == "commit":
                    # go on from the record's own count: counts never go down
                    mine["c"] = max(int(mine.get("c") or 0), agent["commits"]) + 1
                    change["x-commits"] = mine["c"]
                else:
                    change["x-pushed"] = True
            batch.events.append(event)

        def room() -> int:  # events this poll may still send; the rest goes on next poll
            return MAX_EVENTS - ROOM - len(batch.events)

        def is_commit(lead: str, data: bytes, _new: str) -> bool:
            return _is_commit(lead, data)

        def is_push(_lead: str, data: bytes, new: str) -> bool:
            return _is_push(data, new)

        stopped = False  # the event cap stopped this poll before every log was read
        for repo in repos.values():
            if room() <= 0:
                stopped = True
                break
            path = os.path.join(repo.gitdir, "logs", "HEAD")
            watched.append(path)
            lines, stopped = self._lines(path, cursors, hws, budget, oldest,
                                         room=room(), wanted=is_commit)
            for _lead, _data, when, _new in lines:
                emit(repo, "commit", when)
            if stopped:
                break
        for group in by_common.values():
            if stopped:
                break
            base = os.path.join(group[0].common, "logs", "refs", "remotes")
            for path, branch in _remote_logs(base):
                if room() <= 0:
                    stopped = True
                    break
                watched.append(path)
                lines, stopped = self._lines(path, cursors, hws, budget, oldest,
                                             room=room(), wanted=is_push)
                for _lead, _data, when, _new in lines:
                    emit(_pusher(group, branch), "push", when)
                if stopped:
                    break
        if not stopped:  # every log was visited: drop the cursors of logs no longer watched
            forget(cursors, watched)
            _age_out(hws, {cursor_key(p) for p in watched}, oldest)
        living = {a["id"] for a in live}
        for aid in [a for a in counts if a not in living]:
            del counts[aid]  # gone from the status folder or not live: start again next time
        batch.agents.extend(list(agent_changes.values())[:MAX_AGENTS])
        return batch

    @staticmethod
    def _lines(path: str, cursors: dict, hws: dict, budget: Budget, oldest: int, *, room: int,
               wanted: Any) -> tuple[list[tuple[str, bytes, int, str]], bool]:
        """New reflog lines of ``path`` that ``wanted(lead, raw line, new id)`` keeps, as
        ``(lead, raw line, unix time, new id)``, oldest first; lines seen before a rewrite, or
        older than ``days``, are skipped. At most ``room`` lines: then the log's cursor goes
        back to the first line not handed out, and ``stopped`` (the second value) is true."""
        key = cursor_key(path)
        lost = key not in cursors and key in hws
        if key not in cursors:
            _seed(path, cursors, budget)
        res = tail(path, cursors, budget)
        if res.busy or res.missing:
            return [], False
        top, ids = _hw(hws.get(key))
        again = bool(res.reset) or lost
        out: list[tuple[str, bytes, int, str]] = []
        stopped = False
        for line in res.lines:
            parsed = _parse(line)
            if parsed is None:
                continue
            _old, new, when, lead = parsed
            short = new[:12]
            if again and (when < top or (when == top and short in ids)):
                continue  # read again after gc / reflog expire: already counted
            keep = when >= oldest and wanted(lead, line.data, new)
            if keep and len(out) >= room:  # the event cap: this line and the rest next poll
                start_at(path, cursors, line.offset)
                stopped = True
                break
            if when > top:
                top, ids = when, [short]
            elif when == top and short not in ids:
                ids = [*ids, short][-HW_KEEP:]
            if keep:
                out.append((lead, line.data, when, new))
        if top:
            hws[key] = {"t": top, "s": ids}
        return out, stopped


# ---------------------------------------------------------------- helpers
def _parse(line: Line) -> tuple[str, str, int, str] | None:
    return None if line.big else parse_line(line.data)


def _hw(saved: Any) -> tuple[int, list[str]]:
    """``(time, ids)`` of one saved high-water mark (``(0, [])`` for none or a broken one)."""
    if not isinstance(saved, dict):
        return 0, []
    top, ids = saved.get("t"), saved.get("s")
    return (top if isinstance(top, int) and top > 0 else 0,
            [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else [])


def _age_out(hws: dict, watched: set[str], oldest: int) -> None:
    """Keep the high-water marks of logs no longer watched (a log back in view must not count
    its old lines again) until they are older than ``days``; at most :data:`MAX_HW` of them,
    the newest first."""
    for key in [k for k in hws if k not in watched and _hw(hws[k])[0] < oldest]:
        del hws[key]
    idle = sorted((k for k in hws if k not in watched), key=lambda k: _hw(hws[k])[0],
                  reverse=True)
    for key in idle[MAX_HW:]:
        del hws[key]


def _seed(path: str, cursors: dict, budget: Budget) -> None:
    """First sight of a reflog bigger than one per-file allowance: start that far before its
    end (older history is past ``days`` anyway; a line cut there is skipped). A smaller one
    is read whole."""
    try:
        st = os.stat(path)
    except OSError:
        return
    if st.st_size > budget.per_file:
        start_at(path, cursors, st.st_size - budget.per_file)


def _remote_logs(base: str) -> list[tuple[str, str]]:
    """``(path, branch)`` of every remote-branch reflog under ``logs/refs/remotes``."""
    out: list[tuple[str, str]] = []
    try:
        remotes = sorted(os.scandir(base), key=lambda e: e.name)
    except OSError:
        return out
    stack = [(r.path, "") for r in remotes if r.is_dir(follow_symlinks=False)]
    while stack and len(out) < MAX_REMOTE_LOGS:
        folder, prefix = stack.pop()
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            name = f"{prefix}/{entry.name}" if prefix else entry.name
            if entry.is_dir(follow_symlinks=False):
                stack.append((entry.path, name))
            elif entry.is_file(follow_symlinks=False) and len(out) < MAX_REMOTE_LOGS:
                out.append((entry.path, name))
    return out


def _branch(repo: Repo) -> str:
    head = _small(os.path.join(repo.gitdir, "HEAD"))
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else ""


def _pusher(group: list[Repo], branch: str) -> Repo:
    """The worktree a push of ``branch`` came from: the one with it checked out, else the
    first (the main worktree, usually)."""
    if len(group) > 1:
        for repo in group:
            if _branch(repo) == branch:
                return repo
    return group[0]


def _record(doc: dict) -> dict | None:
    folder = doc.get("x-folder")
    sid, state, updated = doc.get("id"), doc.get("state"), doc.get("updated")
    if not (isinstance(folder, str) and folder and isinstance(sid, str)
            and isinstance(state, str) and isinstance(updated, str)):
        return None
    if not os.path.isabs(folder):  # relative: it would name the Watcher's own folder
        return None
    if doc.get("kind") == "project":
        return None
    # --- live: L11 ci_gh --- a CI run's record (tool "ci") names the repository and is
    # updated at every check, but no commit or push is ever its own
    if doc.get("agent") == "ci" or sid.startswith("ci-gh-"):
        return None
    # --- end L11 ci_gh ---
    since = parse_time(doc.get("started")) if isinstance(doc.get("started"), str) else None
    commits = doc.get("x-commits")
    xp = doc.get("x-project")  # fix-PRIV: a path here is checked against private_dirs too
    return {"id": sid, "folder": os.path.normpath(folder), "state": state, "updated": updated,
            "xp": str(xp) if path_like(xp) else "",
            "since": since.isoformat() if since else "",
            "commits": commits if isinstance(commits, int) and commits > 0 else 0}


def _agent_for(repo: Repo, live: list[dict], moment: datetime) -> dict | None:
    """The live agent working in ``repo``'s worktree when ``moment`` happened: the most
    recently updated one whose folder is in it and that had started by then (its
    ``started``, else when this reader first saw it live)."""
    best = None
    root = [_norm(repo.root)]
    for agent in live:
        if not _under(agent["folder"], root):
            continue
        since = parse_time(agent["since"]) if agent["since"] else None
        if since is None or moment < since:
            continue
        if best is None or agent["updated"] > best["updated"]:
            best = agent
    return best
