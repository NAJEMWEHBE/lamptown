"""The ci_gh reader: CI runs of pushed repositories, from a fake ``gh`` (never the real one).

Every repository is an invented folder under ``tmp_path`` (the autouse guard fails a test that
touches a real one). Most tests inject a runner (no process at all); a few run a fake ``gh``
script put first on a temp ``PATH``. Nothing here calls GitHub or the network.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from _reader_guard import guard
from lamptown import livetown
from lamptown.config import parse_config
from lamptown.status import events as ev
from lamptown.status.readers import BUILTIN, ci_gh, make_reader
from lamptown.status.readers.ci_gh import (
    FIELDS,
    Answer,
    CiGhReader,
    classify,
    github_slug,
    run_gh,
    verdict,
)
from lamptown.status.store import read_status, status_path, update_status
from lamptown.status.watch import Watcher

NOW = datetime(2026, 11, 4, 10, 0, 0, tzinfo=UTC)
OWNER = "lamp-owner"  # a GitHub user name: must never reach a file or the page
SHA = "a1" * 20
OLD_SHA = "b2" * 20
AT = "@"  # scp-style remotes ("git" AT "host:o/r") are not e-mail addresses


@pytest.fixture(autouse=True)
def _no_real_repo(monkeypatch, tmp_path):
    guard(monkeypatch, tmp_path)


# ---------------------------------------------------------------- a fake world
class FakeRepo:
    """An invented repository: its config (remotes) and push reflogs, nothing else."""

    def __init__(self, base: Path, name: str = "paper-kite",
                 url: str = f"git{AT}github.com:{OWNER}/paper-kite.git"):
        self.root = base / name
        self.git = self.root / ".git"
        (self.git / "logs").mkdir(parents=True, exist_ok=True)
        (self.git / "HEAD").write_text("ref: refs/heads/main\n")
        cfg = "[core]\n\tbare = false\n"
        if url:
            cfg += (f'[remote "origin"]\n\turl = {url}\n'
                    '\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
        (self.git / "config").write_text(cfg)

    def push(self, when: datetime, new: str = SHA, branch: str = "main",
             msg: str = "update by push") -> None:
        path = self.git / "logs" / "refs" / "remotes" / "origin" / branch
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = int(when.timestamp())
        line = f"{'0' * 40} {new} Lamp Lighter <lamp@example.com> {stamp} +0000\t{msg}\n"
        with open(path, "ab") as fh:
            fh.write(line.encode())


def run(status="completed", conclusion="success", *, sha=SHA, wf="CI",
        created="2026-11-04T09:52:00Z", updated="2026-11-04T09:55:00Z", rid=1):
    return {"databaseId": rid, "headSha": sha, "status": status, "conclusion": conclusion,
            "workflowName": wf, "createdAt": created, "updatedAt": updated}


class Runner:
    """An injected ``gh``: records each argument list, answers from a script."""

    def __init__(self, *answers: Answer):
        self.answers = list(answers)
        self.calls: list[list[str]] = []

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def ok(*runs) -> Answer:
    return Answer(code=0, out=json.dumps(list(runs)).encode())


@pytest.fixture
def gh_on_path(tmp_path, monkeypatch):
    """A fake ``gh`` first on PATH (alone on it): answers from ``scenario.json`` next to it and
    logs each argument list to ``calls.jsonl``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "fake_gh.py"
    script.write_text(
        "import json, os, sys, time\n"
        "here = os.path.dirname(os.path.abspath(__file__))\n"
        "with open(os.path.join(here, 'calls.jsonl'), 'a') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "with open(os.path.join(here, 'scenario.json')) as fh:\n"
        "    s = json.load(fh)\n"
        "time.sleep(s.get('sleep', 0))\n"
        "sys.stdout.write(s.get('out', ''))\n"
        "sys.stderr.write(s.get('err', ''))\n"
        "sys.exit(s.get('code', 0))\n")
    if os.name == "nt":
        (bin_dir / "gh.cmd").write_text(f'@"{sys.executable}" -I "{script}" %*\r\n')
    else:
        gh = bin_dir / "gh"
        gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{script}" "$@"\n')
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bin_dir))
    if os.name == "nt":
        monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")

    class Fake:
        dir = bin_dir

        @staticmethod
        def scenario(**s):
            (bin_dir / "scenario.json").write_text(json.dumps(s))

        @staticmethod
        def calls():
            path = bin_dir / "calls.jsonl"
            return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    Fake.scenario(out="[]")
    return Fake


@pytest.fixture
def no_gh(tmp_path, monkeypatch):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def reader(repo: FakeRepo | None = None, runner=None, **kw) -> CiGhReader:
    return CiGhReader(repos=[str(repo.root)] if repo else [], runner=runner, background=False, **kw)


def poll(r: CiGhReader, now=NOW):
    return r.poll({}, now)


# ---------------------------------------------------------------- results
def test_a_green_run_is_a_done_ci_record_at_the_projects_berth(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=10))
    gh = Runner(ok(run(), run(sha=OLD_SHA, conclusion="failure", rid=0)))
    r = reader(repo, gh)
    batch = poll(r)
    assert len(gh.calls) == 1
    argv = gh.calls[0]
    assert Path(argv[0]).parent == gh_on_path.dir  # the gh found on PATH
    assert argv[1:] == ["run", "list", "-R", f"{OWNER}/paper-kite", "--limit", "20",
                        "--json", FIELDS]
    [change] = batch.agents
    assert change["id"].startswith("ci-gh-") and OWNER not in change["id"]
    assert change["state"] == "ok" and change["x-ci"] == "green"
    assert change["agent"] == "ci" and change["kind"] == "job" and change["x-plot"] == "boathouse"
    assert change["title"] == "CI" and change["x-folder"] == str(repo.root)
    assert change["at"] == datetime(2026, 11, 4, 9, 55, tzinfo=UTC)  # when the run finished
    assert change["started"] == "2026-11-04T09:52:00Z"
    assert batch.events == [] and r.notes == []


def test_a_failed_run_is_red_and_failed(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=10))
    gh = Runner(ok(run(conclusion="failure", wf="Tests"), run(wf="Lint", rid=2)))
    [change] = poll(reader(repo, gh)).agents
    assert (change["state"], change["x-ci"], change["title"]) == ("error", "red", "Tests")


def test_a_running_run_is_working_and_checked_at_most_every_120_s(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=2))
    gh = Runner(ok(run(status="in_progress", conclusion="", updated="2026-11-04T09:59:00Z")))
    r = reader(repo, gh)
    [change] = poll(r).agents
    assert (change["state"], change["x-ci"], change["at"]) == ("working", "running", NOW)
    for later in (3, 60, 119):  # every tick polls; gh is not called again before 120 s
        assert poll(r, NOW + timedelta(seconds=later)).agents == []
    assert len(gh.calls) == 1
    again = poll(r, NOW + timedelta(seconds=121)).agents
    assert len(gh.calls) == 2 and again[0]["at"] == NOW + timedelta(seconds=121)  # confirmed


def test_queued_then_green_then_one_more_check_then_quiet(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=1))
    gh = Runner(ok(run(status="queued", conclusion="")), ok(run()), ok(run()))
    r = reader(repo, gh)
    states = []
    for minute in range(0, 20, 2):
        states += [c["x-ci"] for c in poll(r, NOW + timedelta(minutes=minute)).agents]
    assert states == ["running", "green"]  # the same green twice is sent once
    assert len(gh.calls) == 3  # running, green, green again: then no more calls for this push
    repo.push(NOW + timedelta(minutes=21), new=OLD_SHA)  # a new push: checked again
    gh.answers = [ok(run(sha=OLD_SHA, status="in_progress", conclusion=""))]
    assert poll(r, NOW + timedelta(minutes=22)).agents[0]["x-ci"] == "running"
    assert len(gh.calls) == 4


def test_cancelled_and_skipped_runs_dont_count_and_the_newest_run_per_workflow_wins():
    assert verdict([run(conclusion="cancelled"), run(conclusion="skipped", wf="Lint")]) is None
    # a re-run that passed after a failure: green
    ci, pick = verdict([run(conclusion="failure", created="2026-11-04T09:00:00Z", rid=1),
                        run(conclusion="success", created="2026-11-04T09:30:00Z", rid=2)])
    assert ci == "green" and pick["databaseId"] == 2
    assert verdict([run(), run(status="in_progress", conclusion="", wf="Docs")])[0] == "running"
    busy, broke = run(status="in_progress", conclusion=""), run(conclusion="timed_out", wf="E2E")
    assert verdict([busy, broke])[0] == "red"


def test_runs_for_other_commits_are_not_this_pushs_ci(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=1))
    gh = Runner(ok(run(sha=OLD_SHA)))  # CI hasn't started on the pushed commit yet
    r = reader(repo, gh)
    assert poll(r).agents == []
    gh.answers = [ok(run(status="queued", conclusion=""), run(sha=OLD_SHA))]
    assert poll(r, NOW + timedelta(seconds=125)).agents[0]["x-ci"] == "running"


# ---------------------------------------------------------------- when it calls, and when not
def test_no_push_in_the_last_hour_means_no_call(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    gh = Runner(ok(run()))
    r = reader(repo, gh)
    step = timedelta(seconds=ci_gh.RESCAN)  # the reflogs are looked at again after this

    def later(n):
        return poll(r, NOW + n * step)

    assert later(0).agents == [] and gh.calls == []  # never pushed
    repo.push(NOW - timedelta(minutes=61))
    assert later(1).agents == [] and gh.calls == []  # pushed, but over an hour ago
    repo.push(NOW - timedelta(minutes=5), msg="fetch: fast-forward")  # not a push
    assert later(2).agents == [] and gh.calls == []
    repo.push(NOW - timedelta(minutes=5), new="0" * 40)  # a deleted branch
    assert later(3).agents == [] and gh.calls == []
    repo.push(NOW - timedelta(minutes=5))
    assert later(4).agents and len(gh.calls) == 1


def test_a_run_still_going_is_followed_past_the_hour(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=58))
    gh = Runner(ok(run(status="in_progress", conclusion="")))
    r = reader(repo, gh)
    assert poll(r).agents[0]["x-ci"] == "running"
    gh.answers = [ok(run(conclusion="failure"))]
    assert poll(r, NOW + timedelta(minutes=10)).agents[0]["x-ci"] == "red"  # 68 min after the push
    assert len(gh.calls) == 2


def test_a_folder_with_no_github_remote_is_skipped_with_one_note(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path, url="https://git.example.org/kites/paper-kite.git")
    repo.push(NOW - timedelta(minutes=3))
    gh = Runner(ok(run()))
    r = reader(repo, gh)
    assert poll(r).agents == [] and gh.calls == []
    assert len(r.notes) == 1 and "paper-kite" in r.notes[0] and "not on GitHub" in r.notes[0]
    assert "example.org" not in r.notes[0] and "kites" not in r.notes[0]
    poll(r, NOW + timedelta(minutes=5))
    assert r.notes == [] and gh.calls == []  # said once
    bare = FakeRepo(tmp_path, name="no-remote", url="")
    bare.push(NOW - timedelta(minutes=3))
    r2 = reader(bare, gh)
    assert poll(r2).agents == [] and gh.calls == []


def test_the_live_agents_folders_are_watched_through_the_status_folder(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=4))
    status = tmp_path / "status"
    update_status("claude-code-kite1", status, now=NOW - timedelta(minutes=5),
                  title="Fix save slots", state="ok", agent="claude-code",
                  **{"x-folder": str(repo.root / "src")})
    gh = Runner(ok(run()))
    r = CiGhReader(status_dir=str(status), runner=gh, background=False)
    assert poll(r).agents[0]["x-ci"] == "green"
    # an agent record untouched for over an hour names no folder to watch
    later = FakeRepo(tmp_path, name="harbour-map")
    later.push(NOW + timedelta(hours=2))
    update_status("codex-map1", status, now=NOW, title="Map", state="ok", agent="codex",
                  **{"x-folder": str(later.root)})
    r2 = CiGhReader(status_dir=str(status), runner=Runner(ok(run())), background=False)
    assert poll(r2, NOW + timedelta(hours=2, minutes=1)).agents == []


# ---------------------------------------------------------------- problems: skip, say it once
def test_gh_missing_is_one_note_and_no_crash(tmp_path, no_gh):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=3))
    r = CiGhReader(repos=[str(repo.root)], background=False)  # the real runner: gh isn't on PATH
    assert poll(r).agents == []
    assert len(r.notes) == 1 and "gh" in r.notes[0] and "not found" in r.notes[0]
    for later in (3, 200, 400):
        assert poll(r, NOW + timedelta(seconds=later)).agents == [] and r.notes == []
    assert r.calls == 0


@pytest.mark.parametrize("answer, word, hold", [
    (Answer(code=4, err=b"To get started with GitHub CLI, please run:  gh auth login\n"),
     "not logged in", 600),
    (Answer(code=1, err=b"error connecting to api.github.com\ncheck your internet connection\n"),
     "offline", 300),
    (Answer(code=1, err=b"HTTP 403: API rate limit exceeded for user ID 1234.\n"),
     "rate limit", 900),
    (Answer(error="timeout"), "longer than", 300),
])
def test_a_gh_problem_is_one_note_and_a_pause(tmp_path, gh_on_path, answer, word, hold):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=3))
    gh = Runner(answer)
    r = reader(repo, gh)
    assert poll(r).agents == []
    assert len(r.notes) == 1 and word in r.notes[0]
    assert "1234" not in r.notes[0] and "api.github.com" not in r.notes[0]  # never gh's own text
    assert poll(r, NOW + timedelta(seconds=hold - 5)).agents == [] and len(gh.calls) == 1
    assert r.notes == []
    gh.answers = [ok(run(status="queued", conclusion=""))]
    assert poll(r, NOW + timedelta(seconds=hold + 5)).agents[0]["x-ci"] == "running"
    assert len(gh.calls) == 2


def test_a_repo_gh_cant_see_is_skipped_for_an_hour(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=3))
    gone = b"GraphQL: Could not resolve to a Repository with the name 'x'.\n"
    gh = Runner(Answer(code=1, err=gone))
    r = reader(repo, gh)
    assert poll(r).agents == [] and len(r.notes) == 1 and "x'" not in r.notes[0]
    assert poll(r, NOW + timedelta(minutes=30)).agents == [] and len(gh.calls) == 1


def test_a_broken_runner_or_answer_never_crashes(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=3))

    def boom(argv, timeout):
        raise RuntimeError("nope")

    r = reader(repo, boom)
    assert poll(r).agents == []
    r2 = reader(repo, Runner(Answer(code=0, out=b"<html>not json</html>")))
    assert poll(r2).agents == [] and r2.notes
    r3 = reader(repo, Runner(Answer(code=0, out=b'{"a": 1}')))
    assert poll(r3).agents == []


def test_classify():
    assert classify(Answer(code=0)) == ""
    assert classify(Answer(error="missing")) == "missing"
    assert classify(Answer(code=1, err=b"failed to get runs: HTTP 404: Not Found")) == "repo"
    assert classify(Answer(code=1, err=b"something odd")) == "failed"


# ---------------------------------------------------------------- remotes: only real GitHub names
@pytest.mark.parametrize("url, slug", [
    (f"git{AT}github.com:you/paper-kite.git", "you/paper-kite"),
    ("https://github.com/you/paper-kite", "you/paper-kite"),
    ("https://github.com/you/paper-kite.git/", "you/paper-kite"),
    (f"ssh://git{AT}github.com:22/you/paper-kite.git", "you/paper-kite"),
    (f"https://x-token{AT}github.com/you/paper-kite.git", "you/paper-kite"),
    ("https://gitlab.com/you/paper-kite.git", ""),
    ("https://github.com/you", ""),
    ("https://github.com/you/kite/extra", ""),
    ("https://github.com/-R/--web", ""),
    ("https://github.com/you/kite;rm", ""),
    ("C:" + "/work/paper-kite", ""),  # a local path (split: the privacy gate reads drives)
    ("/work/paper-kite", ""),
])
def test_github_slug(url, slug):
    assert github_slug(url, ("github.com",)) == slug


def test_an_enterprise_host_counts_only_when_named():
    url = "git@ghe.example.com:team/kite.git"
    assert github_slug(url, ("github.com",)) == ""
    assert github_slug(url, ("github.com", "ghe.example.com")) == "ghe.example.com/team/kite"


# ---------------------------------------------------------------- the real process path (fake gh)
def test_a_fake_gh_on_path_end_to_end(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=3))
    gh_on_path.scenario(out=json.dumps([run(conclusion="failure", wf="Build")]))
    r = CiGhReader(repos=[str(repo.root)], background=False)
    [change] = poll(r).agents
    assert (change["x-ci"], change["title"]) == ("red", "Build")
    assert gh_on_path.calls() == [["run", "list", "-R", f"{OWNER}/paper-kite", "--limit", "20",
                                   "--json", FIELDS]]


def test_a_fake_gh_that_is_not_logged_in(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=3))
    gh_on_path.scenario(code=4, err="To get started with GitHub CLI, please run:  gh auth login\n")
    r = CiGhReader(repos=[str(repo.root)], background=False)
    assert poll(r).agents == [] and "not logged in" in r.notes[0]


def test_run_gh_times_out(gh_on_path):
    gh_on_path.scenario(sleep=3, out="[]")
    exe = str(next(p for p in gh_on_path.dir.iterdir() if p.name in ("gh", "gh.cmd")))
    t0 = time.monotonic()
    answer = run_gh([exe, "run", "list"], 0.5)
    assert answer.error == "timeout" and time.monotonic() - t0 < 30


def test_the_call_runs_in_the_background_and_lands_on_a_later_poll(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=3))
    gh_on_path.scenario(out=json.dumps([run()]))
    r = CiGhReader(repos=[str(repo.root)])  # background: the tick never waits on gh
    assert poll(r).agents == []
    found, deadline = [], time.monotonic() + 30
    while not found and time.monotonic() < deadline:
        time.sleep(0.05)
        found = poll(r, NOW + timedelta(seconds=3)).agents
    assert found and found[0]["x-ci"] == "green"
    assert r.calls == 1


# ---------------------------------------------------------------- through the Watcher
def test_through_the_watcher_names_only_and_the_books_events(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=3))
    status = tmp_path / "status"
    status.mkdir()
    gh = Runner(ok(run(status="in_progress", conclusion="", created="2026-11-04T09:58:00Z")))
    r = CiGhReader(repos=[str(repo.root)], runner=gh, background=False)
    with Watcher(status, [r], tz=UTC) as w:
        w.tick(NOW - timedelta(seconds=10))  # the first tick: a snapshot
        w.tick(NOW)
        gh.answers = [ok(run(conclusion="failure", updated="2026-11-04T10:03:00Z"))]
        res = w.tick(NOW + timedelta(minutes=4))
    [rid] = [p.stem for p in status.glob("ci-gh-*.json")]
    doc = read_status(status_path(rid, status))
    assert (doc["state"], doc["x-ci"], doc["agent"], doc["kind"], doc["x-plot"]) == \
        ("error", "red", "ci", "job", "boathouse")
    assert doc["x-project"] == "paper-kite" and doc["title"] == "CI"
    assert not res.errors
    days = ev.read_days(status, 2, now=NOW + timedelta(minutes=5), tz=UTC)
    kinds = [(e["k"], e.get("ok")) for _d, es in days for e in es if e["id"] == rid]
    # the project had no berth yet: it gets one (a real event); then the book's CI line
    assert kinds == [("start", None), ("project", None), ("ci", False), ("fail", None)]
    every = "".join(p.read_text(encoding="utf-8", errors="replace")
                    for p in status.rglob("*") if p.is_file())
    assert OWNER not in every and "lamp@example.com" not in every and "Lamp Lighter" not in every
    assert SHA not in every


def test_notes_reach_the_watchers_errors(tmp_path, no_gh):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=3))
    status = tmp_path / "status"
    status.mkdir()
    r = CiGhReader(repos=[str(repo.root)], background=False)
    with Watcher(status, [r], tz=UTC) as w:
        first = w.tick(NOW)
        second = w.tick(NOW + timedelta(seconds=3))
    assert [e for e in first.errors if "ci_gh" in e and "not found" in e]
    assert not [e for e in second.errors if "ci_gh" in e]


# ---------------------------------------------------------------- config and registry
def test_off_by_default_and_built_in_when_asked(tmp_path):
    assert "ci_gh" in BUILTIN
    cfg = parse_config("[live]\n")
    assert all(e.get("kind") != "ci_gh" for e in cfg.live.readers)
    cfg = parse_config('[live]\nstatus_dir = "/work/status"\n'
                       '[[live.reader]]\nkind = "ci_gh"\nevery = 30\n')
    entry = livetown.reader_entry(cfg, cfg.live.readers[0])
    assert entry["status_dir"] == str(livetown.live_status_dir(cfg))  # the live agents' folders
    r = make_reader(entry)
    assert r.name == "ci_gh" and r.every == 120  # never more often than every 120 s
    off = livetown.reader_entry(cfg, {"kind": "ci_gh", "status_dir": False})
    assert make_reader(off).status_dir is None


def test_the_live_payload_carries_it_and_the_lighthouse_ignores_it(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=3))
    status = tmp_path / "status"
    status.mkdir()
    gh = Runner(ok(run(conclusion="failure", wf="Tests", updated="2026-11-04T09:59:00Z")))
    with Watcher(status, [reader(repo, gh)], tz=UTC) as w:
        w.tick(NOW)
    cfg = parse_config(f'[live]\nstatus_dir = "{status.as_posix()}"\n')
    records = livetown.read_records(status)
    payload = livetown.live_payload(records, cfg, NOW + timedelta(minutes=1))
    [a] = payload["agents"]
    assert (a["tool"], a["title"], a["project"], a["plot"], a["state"], a["ci"]) == \
        ("ci", "Tests", "paper-kite", "boathouse", "error", "red")
    assert [p["id"] for p in payload["projects"]] == ["paper-kite"]  # its berth
    part = livetown.lighthouse_part(payload, "details.html")
    assert part["lamp"] == "green" and part["state"] == "ok"  # CI red is the pennant, not an alarm
    assert OWNER not in json.dumps(payload)


def test_the_reader_saves_only_the_runs_it_saw_going(tmp_path, gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=3))
    state: dict = {}
    reader(repo, Runner(ok(run()))).poll(state, NOW)
    assert state == {}  # a finished run: nothing
    gh = Runner(ok(run(status="in_progress", conclusion="")))
    r = reader(repo, gh)
    [change] = r.poll(state, NOW).agents
    # fix-PRIV: by record id only, never the repository's path
    assert state == {"open": {change["id"]: {"at": "2026-11-04T10:00:00Z"}}}
    gh.answers = [ok(run())]
    r.poll(state, NOW + timedelta(minutes=3))
    assert state == {}  # its result is in
    junk = {"open": {"ci-gh-x": {"at": "soon", "root": "rel"}, "other": 1}}
    reader(repo, Runner(ok(run()))).poll(junk, NOW)  # a broken saved state: dropped
    assert junk == {}
    assert ci_gh.CiGhReader.name == "ci_gh"


# ---------------------------------------------------------------- review fixes (L11-fix)
def _book(status: Path, rid: str, until: datetime) -> list[tuple]:
    days = ev.read_days(status, 2, now=until, tz=UTC)
    return [(e["k"], e.get("ok")) for _d, es in days for e in es if e["id"] == rid]


def _ci_doc(status: Path) -> dict:
    [rid] = [p.stem for p in status.glob("ci-gh-*.json")]
    return read_status(status_path(rid, status))


def test_a_result_that_ended_before_the_last_running_stamp_still_lands(tmp_path, gh_on_path):
    # gh saw the run going at 10:00:00; it had in fact ended at 09:59:58 (GitHub's clock, or
    # an answer collected a moment after it ended): the green must still reach the record
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=2))
    status = tmp_path / "status"
    status.mkdir()
    gh = Runner(ok(run(status="in_progress", conclusion="", updated="2026-11-04T09:59:50Z")))
    with Watcher(status, [reader(repo, gh)], tz=UTC) as w:
        w.tick(NOW)
        gh.answers = [ok(run(updated="2026-11-04T09:59:58Z"))]
        for later in (timedelta(seconds=125), timedelta(seconds=250), timedelta(minutes=40),
                      timedelta(hours=7)):
            w.tick(NOW + later)
    doc = _ci_doc(status)
    assert (doc["state"], doc["x-ci"]) == ("ok", "green")
    kinds = _book(status, doc["id"], NOW + timedelta(hours=8))
    assert ("ci", True) in kinds and ("done", None) in kinds
    assert ("fail", None) not in kinds and ("ci", False) not in kinds


def test_a_running_answer_is_stamped_when_gh_looked_not_when_it_was_collected(tmp_path,
                                                                                 gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=2))
    gh = Runner(ok(run(status="in_progress", conclusion="")))
    r = CiGhReader(repos=[str(repo.root)], runner=gh)  # background: collected on a later poll
    assert poll(r).agents == []
    found, deadline = [], time.monotonic() + 30
    while not found and time.monotonic() < deadline:
        time.sleep(0.02)
        found = poll(r, NOW + timedelta(seconds=3)).agents
    assert found and found[0]["at"] == NOW  # when gh looked, not the later poll


def test_gh_is_never_run_from_the_current_folder(tmp_path, monkeypatch):
    planted = tmp_path / "cloned"  # an untrusted clone the town was started from
    planted.mkdir()
    for name in ("gh", "gh.exe", "gh.cmd", "gh.bat", "gh.com"):
        p = planted / name
        p.write_text("planted\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.chdir(planted)
    # Windows looks in the current folder first unless this is set: never rely on it
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([str(empty), ".", "", "cloned"]))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=3))
    gh = Runner(ok(run()))
    for name in ("gh", os.path.join(".", "gh"), "gh.cmd"):  # by name, or a relative path
        r = reader(repo, gh, gh=name)
        assert poll(r).agents == [] and gh.calls == [], name
        assert len(r.notes) == 1 and "not found" in r.notes[0]
    # an absolute path (named, or a PATH entry) is fine
    exe = planted / ("gh.cmd" if os.name == "nt" else "gh")
    assert poll(reader(repo, gh, gh=str(exe))).agents
    assert gh.calls[-1][0] == str(exe)
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(planted)]))
    assert poll(reader(repo, gh)).agents
    assert os.path.isabs(gh.calls[-1][0]) and Path(gh.calls[-1][0]).parent == planted


def test_losing_gh_mid_run_ends_quiet_never_failed(tmp_path, gh_on_path):
    # a laptop asleep, or offline, through the rest of the run: gh never answers again
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=2))
    status = tmp_path / "status"
    status.mkdir()
    gh = Runner(ok(run(status="in_progress", conclusion="")))
    with Watcher(status, [reader(repo, gh)], tz=UTC) as w:
        w.tick(NOW)
        gh.answers = [Answer(error="timeout")]
        for minutes in (31, 45, 90, 200, 300, 361, 400, 500):
            w.tick(NOW + timedelta(minutes=minutes))
    doc = _ci_doc(status)
    kinds = _book(status, doc["id"], NOW + timedelta(hours=9))
    assert ("fail", None) not in kinds and ("ci", False) not in kinds
    assert kinds[-1] == ("quiet", None)  # unknown: it went quiet
    cfg = parse_config(f'[live]\nstatus_dir = "{status.as_posix()}"\n')
    [a] = livetown.live_payload(livetown.read_records(status), cfg,
                                NOW + timedelta(minutes=400))["agents"]
    assert a["state"] == "quiet"
    # a sleep shorter than that is no news at all: the run is followed to its real result
    later = NOW + timedelta(hours=9)
    repo.push(later, new=OLD_SHA)
    gh2 = Runner(ok(run(sha=OLD_SHA, status="in_progress", conclusion="")))
    with Watcher(status, [reader(repo, gh2)], tz=UTC) as w:
        w.tick(later)
        gh2.answers = [ok(run(sha=OLD_SHA, updated="2026-11-04T19:45:00Z"))]
        w.tick(later + timedelta(minutes=50))  # woke up 50 min later
    kinds = _book(status, doc["id"], later + timedelta(hours=1))
    assert ("fail", None) not in kinds and kinds[-2:] == [("ci", True), ("done", None)]


def test_a_restart_long_after_the_push_follows_the_run_to_its_real_result(tmp_path,
                                                                           gh_on_path):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(minutes=50))
    status = tmp_path / "status"
    status.mkdir()
    gh = Runner(ok(run(status="in_progress", conclusion="")))
    with Watcher(status, [reader(repo, gh)], tz=UTC) as w:
        w.tick(NOW)
    # the town restarts 40 min later: 90 min after the push, past the hour; memory is empty
    gh2 = Runner(ok(run(conclusion="failure", updated="2026-11-04T10:30:00Z")))
    with Watcher(status, [reader(repo, gh2)], tz=UTC) as w:
        w.tick(NOW + timedelta(minutes=40))
        w.tick(NOW + timedelta(hours=8))
    assert len(gh2.calls) == 1  # it looked again, once
    doc = _ci_doc(status)
    assert (doc["state"], doc["x-ci"]) == ("error", "red")  # the real result, from gh
    kinds = _book(status, doc["id"], NOW + timedelta(hours=9))
    assert kinds[-2:] == [("ci", False), ("fail", None)]
    saved = (status / ".lamptown" / "cursors.json").read_text(encoding="utf-8")
    assert OWNER not in saved and SHA not in saved


def test_no_recent_push_means_no_reflog_rescan_every_tick(tmp_path, gh_on_path, monkeypatch):
    repo = FakeRepo(tmp_path)
    repo.push(NOW - timedelta(hours=3))  # long ago
    scans = []
    real = ci_gh.last_push
    monkeypatch.setattr(ci_gh, "last_push", lambda *a: scans.append(1) or real(*a))
    gh = Runner(ok(run()))
    r = reader(repo, gh)
    for second in range(120):  # a tick a second for two minutes
        assert poll(r, NOW + timedelta(seconds=second)).agents == []
    assert 1 <= len(scans) <= 120 // ci_gh.RESCAN + 1
    repo.push(NOW + timedelta(seconds=120))  # a new push is still seen within the pause
    seen = [s for s in range(ci_gh.RESCAN + 1)
            if poll(r, NOW + timedelta(seconds=120 + s)).agents]
    assert seen and len(gh.calls) == 1
