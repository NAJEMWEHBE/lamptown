"""A release says one version everywhere: ``lamptown.__version__``, the top ``## X.Y.Z`` heading
of CHANGELOG.md and, on a CI tag build, the tag ``vX.Y.Z`` (with no "Unreleased" left in the
heading). CI runs on tags ``v*`` too (``.github/workflows/ci.yml``), so a tag pushed on the wrong
commit goes red before anyone publishes from it.

A core test (no browser, no app): it runs in every core CI job.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

import lamptown

ROOT = Path(__file__).resolve().parents[1]
HEADING = re.compile(r"^## +(?P<text>.+?)\s*$", re.MULTILINE)
VERSION = re.compile(r"(?P<version>\d+\.\d+\.\d+)(?![\w.])")


def release_problems(version: str, changelog: str, env: Mapping[str, str]) -> list[str]:
    """What is wrong with this release, in words (an empty list: nothing)."""
    top = HEADING.search(changelog)
    if not top:
        return ["CHANGELOG.md has no '## X.Y.Z' heading"]
    text = top.group("text")
    found = VERSION.match(text)
    problems = []
    if not found:
        problems.append(f"CHANGELOG.md's top heading {text!r} does not start with a version")
    elif found.group("version") != version:
        problems.append(f"lamptown.__version__ is {version} but CHANGELOG.md's top heading is "
                        f"{found.group('version')}")
    if env.get("GITHUB_REF_TYPE") == "tag":
        tag = env.get("GITHUB_REF_NAME", "")
        if tag != f"v{version}":
            problems.append(f"the tag {tag!r} is not v{version} (lamptown.__version__)")
        if "unreleased" in text.lower():
            problems.append(f"the tag build's CHANGELOG.md heading still says {text!r}")
    return problems


def test_the_version_the_changelog_and_the_tag_agree():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release_problems(lamptown.__version__, changelog, os.environ) == []


LOG = "# Changelog\n\nWords.\n\n## {head}\n\n### Added\n\n- a thing\n\n## 0.1.0\n\n- first\n"
BRANCH = {"GITHUB_REF_TYPE": "branch", "GITHUB_REF_NAME": "main"}


def tag(name: str) -> dict[str, str]:
    return {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": name}


@pytest.mark.parametrize(("head", "env", "wrong"), [
    ("0.2.0", {}, []),
    ("0.2.0", BRANCH, []),
    ("0.2.0", tag("v0.2.0"), []),
    ("0.2.0 - 2026-11-05", tag("v0.2.0"), []),
    ("0.2.0 (Unreleased)", BRANCH, []),  # work in progress on a branch is fine
    ("0.2.0 (Unreleased)", tag("v0.2.0"), ["still says"]),
    ("0.2.0", tag("v0.2.1"), ["is not v0.2.0"]),
    ("0.2.0", tag("0.2.0"), ["is not v0.2.0"]),
    ("0.1.9", BRANCH, ["top heading is 0.1.9"]),
    ("0.2.0.1", BRANCH, ["does not start with a version"]),
    ("0.2.0rc1", BRANCH, ["does not start with a version"]),
    ("0.2.1 (Unreleased)", tag("v0.2.0"), ["top heading is 0.2.1", "still says"]),
    ("Unreleased", BRANCH, ["does not start with a version"]),
])
def test_the_release_rules(head, env, wrong):
    problems = release_problems("0.2.0", LOG.format(head=head), env)
    assert len(problems) == len(wrong), problems
    for problem, words in zip(problems, wrong, strict=True):
        assert words in problem


def test_a_changelog_with_no_heading_is_a_problem():
    assert release_problems("0.2.0", "# Changelog\n\nNothing yet.\n", {}) == [
        "CHANGELOG.md has no '## X.Y.Z' heading"]
