"""``lamptown init``: the starter config, folder-only detection, writing only the target."""

from __future__ import annotations

import builtins
import io
import json
import os
import tomllib
from pathlib import Path

import pytest

from lamptown import build as B
from lamptown.cli import main
from lamptown.config import CONFIG_NAME, load_config, parse_config, user_config_dir
from lamptown.onboarding import agents as A
from lamptown.onboarding import init as I

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = {
    "claude-code": "examples/claude-code/settings.json",
    "codex": "examples/codex/config.toml",
    "gemini-cli": "examples/gemini-cli/settings.json",
    "cursor": "examples/cursor/hooks.json",
}
PLANTED = "kestrel-private-note-91"  # a fictional secret inside the fake agent folders


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home folder with every known agent folder; HOME, USERPROFILE and the per-user
    config/data folders all point into tmp_path."""
    h = tmp_path / "home"
    for a in A.AGENTS:
        (h / a.folder).mkdir(parents=True)
        (h / a.folder / "settings.json").write_text(PLANTED, encoding="utf-8")
    (h / ".claude" / "skills" / "kite-knots").mkdir(parents=True)
    (h / ".claude" / "skills" / "kite-knots" / "SKILL.md").write_text(PLANTED, encoding="utf-8")
    for k, v in {"HOME": h, "USERPROFILE": h, "APPDATA": tmp_path / "appdata",
                 "LOCALAPPDATA": tmp_path / "localappdata",
                 "XDG_CONFIG_HOME": tmp_path / "xdg-config",
                 "XDG_DATA_HOME": tmp_path / "xdg-data"}.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.delenv("AGENT_STATUS_DIR", raising=False)
    monkeypatch.delenv("LAMPTOWN_CONFIG", raising=False)
    return h


def tree(root: Path) -> dict[str, tuple[int, int]]:
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


@pytest.fixture
def no_reads_in(monkeypatch):
    """Fail if anything opens or lists a path inside the given folder."""
    guarded: list[Path] = []

    def inside(p) -> bool:
        try:
            q = Path(os.fsdecode(p)).resolve()
        except (TypeError, ValueError, OSError):
            return False
        return any(q == g or g in q.parents for g in guarded)

    real_open, real_scandir, real_listdir = builtins.open, os.scandir, os.listdir

    def guard_open(file, *a, **k):
        if not isinstance(file, int) and inside(file):
            raise AssertionError(f"opened a file inside a private folder: {file}")
        return real_open(file, *a, **k)

    def guard_scandir(path="."):
        if inside(path):
            raise AssertionError(f"listed a private folder: {path}")
        return real_scandir(path)

    def guard_listdir(path="."):
        if inside(path):
            raise AssertionError(f"listed a private folder: {path}")
        return real_listdir(path)

    monkeypatch.setattr(builtins, "open", guard_open)
    monkeypatch.setattr(io, "open", guard_open)
    monkeypatch.setattr(os, "scandir", guard_scandir)
    monkeypatch.setattr(os, "listdir", guard_listdir)
    return guarded


# ----------------------------------------------------------------------------- render
@pytest.mark.parametrize("skills", [(), ("~/.claude/skills",), ("~/.claude/skills",
                                                                "~/.codex/skills")])
def test_every_starter_is_a_valid_config(tmp_path, skills):
    found = A.Found((A.BY_ID["claude-code"],), skills, False)
    plan = I.render(found=found)
    cfg = parse_config(plan.text, tmp_path / CONFIG_NAME)
    ids = [b.id for b in cfg.buildings]
    assert ids == ["agents", "jobs"] + (["skills"] if skills else [])
    assert [b.label for b in cfg.buildings] == list(plan.buildings)
    if skills:
        dirs = cfg.buildings[2].sources[0].table["dirs"]
        assert len(dirs) == len(skills) and all(Path(d).is_absolute() for d in dirs)


def test_the_starter_names_what_was_found():
    plan = I.render(found=A.Found((A.BY_ID["codex"], A.BY_ID["cursor"]), (), False))
    assert "# (found: Codex CLI, Cursor)" in plan.text


def test_title_and_paths_are_escaped_for_toml(tmp_path):
    title = 'Kite "field" \\ north é'
    status = "kites\\status\\dir"  # Windows-style separators must survive as written
    plan = I.render(title=title, status_dir=status)
    data = tomllib.loads(plan.text)
    assert data["town"]["title"] == title
    assert data["building"][0]["source"][0]["dir"] == status


def test_commented_examples_become_valid_when_switched_on(tmp_path):
    text = I.render().text
    on = "\n".join(line[2:] if line.startswith("# [[") or line.startswith("# id")
                   or line.startswith("# type") or line.startswith("# plot")
                   or line.startswith("# label") or line.startswith("#   ") else line
                   for line in text.splitlines())
    cfg = parse_config(on, tmp_path / CONFIG_NAME)
    assert {b.type for b in cfg.buildings} == {"harbour", "jobs", "vault"}
    assert len(cfg.buildings) == 4


# ----------------------------------------------------------------------------- detection
def test_detect_checks_folder_existence_only(home, no_reads_in):
    no_reads_in.append(home)
    found = A.detect(home)
    assert [a.id for a in found.agents] == ["claude-code", "codex", "gemini-cli", "cursor"]
    assert found.skill_dirs == ("~/.claude/skills",)
    assert found.status_folder is False


def test_detect_on_an_empty_home(tmp_path):
    found = A.detect(tmp_path)
    assert found == A.Found((), (), False)


def test_a_file_with_an_agent_folder_name_is_not_an_agent(tmp_path):
    (tmp_path / ".codex").write_text("not a folder", encoding="utf-8")
    assert A.detect(tmp_path).agents == ()


# ----------------------------------------------------------------------------- the command
def test_init_writes_only_the_target_and_never_reads_agent_folders(home, tmp_path, no_reads_in,
                                                                   capsys):
    target = tmp_path / "town"
    before = tree(home)
    no_reads_in.append(home)
    assert main(["init", "--dir", str(target)]) == 0
    no_reads_in.clear()
    assert tree(home) == before
    assert sorted(p.name for p in target.iterdir()) == [CONFIG_NAME]
    out = capsys.readouterr().out
    assert PLANTED not in out and PLANTED not in (target / CONFIG_NAME).read_text("utf-8")
    assert "Found Claude Code (~/.claude), Codex CLI (~/.codex), Gemini CLI (~/.gemini), " \
           "Cursor (~/.cursor)" in out
    for agent in A.AGENTS:
        assert f"lamptown init --hooks {agent.id}" in out
    assert "lamptown doctor --config" in out


def test_init_defaults_to_the_current_folder(home, tmp_path, monkeypatch, capsys):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert main(["init", "--no-detect"]) == 0
    cfg = load_config(str(work / CONFIG_NAME))
    assert [b.id for b in cfg.buildings] == ["agents", "jobs"]
    out = capsys.readouterr().out
    assert f"--config {CONFIG_NAME}" in out and "lamptown init --user" in out
    assert "Agent detection was off" in out


def test_init_user_writes_where_lamptown_looks(home, capsys):
    assert main(["init", "--user", "--title", "Harbour Watch"]) == 0
    target = user_config_dir() / CONFIG_NAME
    cfg = load_config()  # the default lookup finds it with no flags
    assert cfg.path == target.resolve() and cfg.town.title == "Harbour Watch"
    out = capsys.readouterr().out
    assert "--config" not in out.split("Next steps:")[1]


def test_an_existing_config_is_kept_unless_forced(tmp_path, home, capsys):
    target = tmp_path / CONFIG_NAME
    target.write_text("# mine\n", encoding="utf-8")
    assert main(["init", "--dir", str(tmp_path)]) == I.EXIT_EXISTS
    assert target.read_text(encoding="utf-8") == "# mine\n"
    assert "already exists" in capsys.readouterr().err
    assert main(["init", "--dir", str(tmp_path), "--force"]) == 0
    assert (tmp_path / (CONFIG_NAME + ".bak")).read_text(encoding="utf-8") == "# mine\n"
    load_config(str(target))


def test_print_writes_nothing(tmp_path, home, capsys):
    assert main(["init", "--dir", str(tmp_path / "t"), "--print"]) == 0
    assert not (tmp_path / "t").exists()
    parse_config(capsys.readouterr().out)


def test_a_folder_in_the_way_is_an_error(tmp_path, home, capsys):
    (tmp_path / CONFIG_NAME).mkdir()
    assert main(["init", "--dir", str(tmp_path)]) == I.EXIT_WRITE
    assert "is a folder" in capsys.readouterr().err


@pytest.mark.parametrize("title", ["", "   ", "x" * 61])
def test_bad_titles_are_refused(tmp_path, home, title, capsys):
    assert main(["init", "--dir", str(tmp_path), "--title", title]) == I.EXIT_USAGE
    assert not (tmp_path / CONFIG_NAME).exists()


def test_status_dir_choice(tmp_path, home, monkeypatch, capsys):
    work, town = tmp_path / "work", tmp_path / "town"
    work.mkdir()
    monkeypatch.chdir(work)

    def written(*flags) -> tuple[str, Path]:
        """(what the config says, where Lamptown will look) for `init --dir town ...`."""
        assert main(["init", "--dir", str(town), "--force", "--no-detect", *flags]) == 0
        capsys.readouterr()
        cfg = load_config(str(town / CONFIG_NAME))
        raw = tomllib.loads((town / CONFIG_NAME).read_text("utf-8"))
        return (raw["building"][0]["source"][0]["dir"],
                Path(cfg.buildings[0].sources[0].table["dir"]))

    assert written() == ("~/.agent-status", home / ".agent-status")
    monkeypatch.setenv("AGENT_STATUS_DIR", str(home / "lamps"))
    assert written() == ("~/lamps", home / "lamps")
    monkeypatch.delenv("AGENT_STATUS_DIR")
    elsewhere = tmp_path / "shared-lamps"
    assert written("--status-dir", str(elsewhere)) == (str(elsewhere), elsewhere)
    # relative = from the current folder, as on any command line; never town/town/...
    said, looks = written("--status-dir", "lamps")
    assert looks.resolve() == (work / "lamps").resolve() and Path(said).is_absolute()
    assert written("--status-dir", "../town/lamps")[0] == "lamps"  # inside: stays relative
    assert written("--status-dir", str(town / "lamps"))[0] == "lamps"
    assert written("--status-dir", str(town)) == (".", town)
    monkeypatch.setenv("AGENT_STATUS_DIR", "lamps")  # a relative env value: same rule
    assert written()[1].resolve() == (work / "lamps").resolve()


def test_the_starter_builds_a_town(tmp_path, home, monkeypatch):
    status = home / ".agent-status"
    status.mkdir()
    (status / "wren.json").write_text(json.dumps({
        "spec": "agent-status/1", "id": "wren", "title": "Wren: tidy the kite shed",
        "state": "waiting", "updated": "2026-03-14T21:50:00Z"}), encoding="utf-8")
    assert main(["init", "--dir", str(tmp_path), "--no-detect"]) == 0
    import datetime as dt  # a fixed clock: waiting files expire after 7 days

    res = B.build(load_config(str(tmp_path / CONFIG_NAME)), out_dir=tmp_path / "out",
                  now=dt.datetime(2026, 3, 14, 22, 0, tzinfo=dt.UTC))
    assert res.town.is_file()
    assert res.data["rooms"]["agents"]["state"] == "waiting"


# ----------------------------------------------------------------------------- hooks
@pytest.mark.parametrize("agent", sorted(EXAMPLES))
def test_hook_blocks_match_the_examples(agent, capsys):
    assert main(["init", "--hooks", agent]) == 0
    got = capsys.readouterr()
    example = (ROOT / EXAMPLES[agent]).read_text(encoding="utf-8")
    if EXAMPLES[agent].endswith(".json"):
        assert json.loads(got.out) == json.loads(example)
    else:
        assert tomllib.loads(got.out) == tomllib.loads(example)
    assert A.BY_ID[agent].hook_file in got.err  # where to paste it goes to stderr


def test_unknown_hook_agent_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as e:
        main(["init", "--hooks", "nobody"])
    assert e.value.code == 2


def test_init_and_doctor_are_registered():
    from lamptown.cli import build_parser

    sub = next(a for a in build_parser()._actions if a.dest == "command")
    assert {"init", "doctor"} <= set(sub.choices)


def test_registering_init_and_doctor_stays_light():
    """Every `lamptown status hook` call builds the parser: init/doctor must not load the
    builder, the collectors or a GUI module just to register."""
    import subprocess
    import sys

    code = ("import sys, lamptown.cli as c; c.build_parser(); "
            "print(sorted(m for m in ('lamptown.build', 'lamptown.collectors', 'webview') "
            "if m in sys.modules))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                       check=False)
    assert r.stdout.strip() == "[]", r.stderr
