"""The CI workflows and the shared browser helper.

The workflows can only really run on GitHub; these tests check what can be checked here: the
matrix, pinned actions, permissions, the deny-list handling, the test split by marker, and (when
the ``actionlint`` binary is around: on PATH or in $ACTIONLINT) the full workflow lint.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import _browser

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
OSES = {"ubuntu-latest", "windows-latest", "macos-latest"}


def load(name: str) -> dict:
    data = yaml.safe_load((WF / name).read_text(encoding="utf-8"))
    if True in data:  # YAML 1.1 reads the key `on` as True
        data["on"] = data.pop(True)
    return data


@pytest.fixture(scope="module")
def ci() -> dict:
    return load("ci.yml")


@pytest.fixture(scope="module")
def media() -> dict:
    return load("media.yml")


def steps(job: dict) -> list[dict]:
    return job.get("steps", [])


def runs(job: dict) -> str:
    return "\n".join(s.get("run", "") for s in steps(job))


def pythons(job: dict) -> set[str]:
    out = set()
    for s in steps(job):
        if str(s.get("uses", "")).startswith("actions/setup-python@"):
            out.add(str(s["with"]["python-version"]))
    return out


# ----------------------------------------------------------------------------- shape
def test_every_action_is_pinned_by_sha_with_its_version():
    for f in sorted(WF.glob("*.yml")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"uses:\s*(\S+)", line)
            if not m or m.group(1).startswith("./"):
                continue
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", m.group(1)), (f.name, n, line)
            assert re.search(r"#\s*v\d", line), f"{f.name}:{n}: say which version the SHA is"


def test_minimal_permissions(ci, media):
    assert ci["permissions"] == {"contents": "read"}
    for name, job in ci["jobs"].items():
        assert "write" not in str(job.get("permissions", "")), name
    assert media["permissions"] == {}
    assert media["jobs"]["media"]["permissions"] == {"contents": "write",
                                                     "pull-requests": "write"}


def test_every_job_has_a_timeout_and_no_persisted_token(ci, media):
    for wf in (ci, media):
        for name, job in wf["jobs"].items():
            assert job.get("timeout-minutes"), name
            for s in steps(job):
                if str(s.get("uses", "")).startswith("actions/checkout@"):
                    assert s["with"]["persist-credentials"] is False, name


def test_core_matrix_is_three_oses_by_two_pythons(ci):
    test = ci["jobs"]["test"]
    assert set(test["strategy"]["matrix"]["os"]) == OSES
    assert set(test["strategy"]["matrix"]["python"]) == {"3.11", "3.13"}
    assert test["strategy"]["fail-fast"] is False
    assert 'pytest -m "not browser and not app"' in runs(test)
    assert "pip install . " in runs(test)  # the built package, not an editable install


def test_browser_and_app_jobs_run_on_3_13_on_every_os(ci):
    for name, marker in (("browser", "-m browser"), ("app", "-m app")):
        job = ci["jobs"][name]
        oses = {o.replace("ubuntu-24.04", "ubuntu-latest") for o in job["strategy"]["matrix"]["os"]}
        assert oses == OSES, name
        assert pythons(job) == {"3.13"}, name
        assert marker in runs(job), name


def test_the_three_marker_runs_cover_every_test(ci):
    """not(browser or app) + browser + app: every test lands in exactly one job per OS."""
    test, browser, app = (runs(ci["jobs"][j]) for j in ("test", "browser", "app"))
    assert '-m "not browser and not app"' in test
    assert "-m browser" in browser and "-m app" in app
    assert "tests/test_app_window.py" in app  # the smoke test runs on its own step


def test_browser_job_installs_chrome_and_requires_it(ci):
    job = ci["jobs"]["browser"]
    assert job["env"]["LAMPTOWN_REQUIRE_BROWSER"] == "1"
    chrome = [s for s in steps(job) if "setup-chrome" in str(s.get("uses", ""))]
    assert len(chrome) == 1 and chrome[0]["id"] == "chrome"
    # macOS uses the runner's own Chrome (Chrome for Testing hangs there); an empty
    # $CHROME makes find_browser look in /Applications
    assert chrome[0]["if"] == "runner.os != 'macOS'"
    (run,) = [s for s in steps(job) if "-m browser" in s.get("run", "")]
    assert run["env"]["CHROME"] == "${{ steps.chrome.outputs.chrome-path }}"


def test_app_job_uses_xvfb_on_linux_and_only_macos_may_fail(ci):
    job = ci["jobs"]["app"]
    text = runs(job)
    assert "xvfb-run" in text and "openbox" in text
    soft = [s for s in steps(job) if s.get("continue-on-error")]
    assert [s["if"] for s in soft] == ["runner.os == 'macOS'"]
    assert soft[0]["env"]["LAMPTOWN_APP_SMOKE"] == "1"
    assert 'grep -q "SKIPPED"' in text  # a skipped smoke test must not pass as green


def test_ci_ok_waits_for_every_job(ci):
    ok = ci["jobs"]["ci-ok"]
    assert ok["if"] == "always()"
    assert set(ok["needs"]) == set(ci["jobs"]) - {"ci-ok"}


def test_a_release_tag_runs_ci_so_the_release_test_sees_it(ci):
    """tests/test_release_consistency.py checks the tag against __version__ on a tag build."""
    assert ci["on"]["push"] == {"branches": ["main"], "tags": ["v*"]}


def test_the_browser_job_can_render_the_media(ci, media):
    """tests/test_media_fresh.py runs tools/make_media.py, which needs Pillow: the same one the
    "Regenerate media" workflow makes the media with."""
    version = ci["env"]["PILLOW_VERSION"]
    assert 'pillow==${PILLOW_VERSION}' in runs(ci["jobs"]["browser"])
    assert f'"pillow=={version}"' in runs(media["jobs"]["media"])


# ----------------------------------------------------------------------------- the deny-list
def deny_steps(wf: dict) -> list[dict]:
    return [s for job in wf["jobs"].values() for s in steps(job)
            if "secrets.LAMPTOWN_DENYLIST" in str(s)]


def test_the_secret_only_reaches_one_env_var_and_is_never_printed(ci, media):
    raw = (WF / "ci.yml").read_text(encoding="utf-8") + (WF / "media.yml").read_text("utf-8")
    assert raw.count("secrets.LAMPTOWN_DENYLIST") == 2
    for wf in (ci, media):
        (step,) = deny_steps(wf)
        assert step["env"]["DENYLIST"] == "${{ secrets.LAMPTOWN_DENYLIST }}"
        assert step["shell"] == "python"
        code = step["run"]
        assert "secrets." not in code  # never pasted into the script itself
        for line in code.splitlines():
            if "print(" in line or "echo" in line:
                assert "text" not in line and "DENYLIST\"" not in line, line
        assert 'RUNNER_TEMP' in code and "0o600" in code
        assert "LAMPTOWN_DENYLIST={path}" in code


def test_forks_skip_only_the_deny_list_and_others_fail_without_it(ci):
    (step,) = deny_steps(ci)
    cond = " ".join(step["env"]["NO_SECRETS_EXPECTED"].split())
    assert "github.event.pull_request.head.repo.full_name != github.repository" in cond
    assert "sys.exit(1)" in step["run"]  # anywhere else a missing secret is an error
    gate = runs(ci["jobs"]["privacy"])
    assert "privacy_gate.py --history" in gate
    assert "--require-denylist" in step["run"]  # set only once the list is loaded


def test_media_workflow_is_manual_and_gated_before_it_pushes(media):
    assert set(media["on"]) == {"workflow_dispatch"}
    job = media["jobs"]["media"]
    names = [s.get("name", s.get("uses", "")) for s in steps(job)]
    order = {k: next(i for i, n in enumerate(names) if k in n)
             for k in ("Make the media", "deny-list", "Privacy gate", "pull request")}
    assert order["Make the media"] < order["deny-list"] < order["Privacy gate"] \
        < order["pull request"]
    text = runs(job)
    assert "make_media.py" in text and "gh pr create" in text
    assert "--commit-msg" in text and text.index("--commit-msg") < text.index("git push")
    assert "noreply.github.com" in text


@pytest.mark.skipif(not (os.environ.get("ACTIONLINT") or shutil.which("actionlint")),
                    reason="actionlint not found (set $ACTIONLINT or put it on PATH)")
def test_actionlint_is_clean():
    exe = os.environ.get("ACTIONLINT") or shutil.which("actionlint")
    r = subprocess.run([exe, *sorted(str(p) for p in WF.glob("*.yml"))], cwd=ROOT,
                       capture_output=True, text=True, timeout=120, check=False)
    assert r.returncode == 0, r.stdout + r.stderr


# ----------------------------------------------------------------------------- browser helper
def test_dump_dom_stops_a_hung_browser():
    hang = [sys.executable, "-c", "import time; print('<html>', flush=True); time.sleep(120)"]
    t = time.monotonic()
    with pytest.raises(pytest.fail.Exception, match="did not finish"):
        _browser.dump_dom(hang, timeout=1.5)
    assert time.monotonic() - t < 40


def test_dump_dom_returns_stdout():
    out = _browser.dump_dom([sys.executable, "-c", "print('<pre id=\"T\">{}</pre>')"])
    assert '<pre id="T">{}</pre>' in out


def test_headless_flags(monkeypatch, tmp_path):
    monkeypatch.setattr(_browser, "CHROME", "/opt/kite/chrome")
    monkeypatch.setattr(_browser, "_no_sandbox", lambda: False)
    cmd = _browser.headless(tmp_path / "p", "--dump-dom")
    assert cmd[0] == "/opt/kite/chrome" and "--headless=new" in cmd and cmd[-1] == "--dump-dom"
    assert f"--user-data-dir={tmp_path / 'p'}" in cmd and "--no-sandbox" not in cmd
    monkeypatch.setattr(_browser, "_no_sandbox", lambda: True)
    assert "--no-sandbox" in _browser.headless(tmp_path / "p")


def _one_browser_test(env: dict) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items()
            if k not in {"CHROME", "LAMPTOWN_REQUIRE_BROWSER", "PYTEST_ADDOPTS"}}
    full.update(env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs",
         "tests/test_town.py::test_empty_config_is_a_town_of_dark_lots"],
        cwd=ROOT, env=full, capture_output=True, text=True, timeout=120, check=False)


def test_without_a_browser_the_tests_skip_with_the_reason(tmp_path):
    r = _one_browser_test({"CHROME": str(tmp_path / "no-such-browser")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 skipped" in r.stdout and "no-such-browser" in r.stdout


def test_ci_turns_a_missing_browser_into_a_failure(tmp_path):
    r = _one_browser_test({"CHROME": str(tmp_path / "no-such-browser"),
                           "LAMPTOWN_REQUIRE_BROWSER": "1"})
    assert r.returncode == 1, r.stdout + r.stderr
    assert "LAMPTOWN_REQUIRE_BROWSER=1" in r.stdout
