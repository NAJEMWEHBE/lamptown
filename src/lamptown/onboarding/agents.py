"""The agent setups ``lamptown init`` knows about, and the hook blocks that light their lamps.

Detection only asks whether a well-known folder exists in the home folder (``~/.claude``,
``~/.codex``...). It never lists those folders or opens a file in them: what is inside is private.

The hook blocks are the same as the files in ``examples/`` (a test keeps them in step), so a
``pipx`` install can print them without the repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Agent:
    id: str  # the provider name `lamptown status hook` takes
    name: str
    folder: str  # relative to the home folder; its existence is all that is checked
    hook_file: str  # where the hook block goes (shown to the user, never opened)
    how: str  # one line: what to do with the block
    snippet: str
    skills: str | None = None  # a skills folder (folders of SKILL.md) this agent uses, if any


CLAUDE_CODE = """{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "lamptown status hook claude-code", "timeout": 10}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "lamptown status hook claude-code", "timeout": 10}]}
    ],
    "PostToolUse": [
      {"matcher": "*", "hooks": [{"type": "command", "command": "lamptown status hook claude-code", "timeout": 10}]}
    ],
    "Notification": [
      {"hooks": [{"type": "command", "command": "lamptown status hook claude-code", "timeout": 10}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "lamptown status hook claude-code", "timeout": 10}]}
    ],
    "StopFailure": [
      {"hooks": [{"type": "command", "command": "lamptown status hook claude-code", "timeout": 10}]}
    ],
    "SessionEnd": [
      {"hooks": [{"type": "command", "command": "lamptown status hook claude-code", "timeout": 10}]}
    ]
  }
}
"""  # noqa: E501  (kept byte-for-byte like examples/claude-code/settings.json)

CODEX = """# Paste into ~/.codex/config.toml (notify only works in the user-level file).

# Simplest: Codex runs this after every finished turn, with one JSON argument.
notify = ["lamptown", "status", "hook", "codex"]

# Richer, optional: Codex hooks also show "working" and permission requests.
# Codex asks you to review and trust new hooks with /hooks before they run.
[[hooks.SessionStart]]
[[hooks.SessionStart.hooks]]
type = "command"
command = "lamptown status hook codex"

[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "lamptown status hook codex"

[[hooks.PermissionRequest]]
[[hooks.PermissionRequest.hooks]]
type = "command"
command = "lamptown status hook codex"

[[hooks.PostToolUse]]
[[hooks.PostToolUse.hooks]]
type = "command"
command = "lamptown status hook codex"

[[hooks.SessionEnd]]
[[hooks.SessionEnd.hooks]]
type = "command"
command = "lamptown status hook codex"
"""

GEMINI_CLI = """{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "name": "lamptown-status", "command": "lamptown status hook gemini-cli", "timeout": 10000}]}
    ],
    "BeforeAgent": [
      {"hooks": [{"type": "command", "name": "lamptown-status", "command": "lamptown status hook gemini-cli", "timeout": 10000}]}
    ],
    "AfterTool": [
      {"matcher": ".*", "hooks": [{"type": "command", "name": "lamptown-status", "command": "lamptown status hook gemini-cli", "timeout": 10000}]}
    ],
    "Notification": [
      {"hooks": [{"type": "command", "name": "lamptown-status", "command": "lamptown status hook gemini-cli", "timeout": 10000}]}
    ],
    "AfterAgent": [
      {"hooks": [{"type": "command", "name": "lamptown-status", "command": "lamptown status hook gemini-cli", "timeout": 10000}]}
    ],
    "SessionEnd": [
      {"hooks": [{"type": "command", "name": "lamptown-status", "command": "lamptown status hook gemini-cli", "timeout": 10000}]}
    ]
  }
}
"""  # noqa: E501  (kept byte-for-byte like examples/gemini-cli/settings.json)

CURSOR = """{
  "version": 1,
  "hooks": {
    "sessionStart": [{"command": "lamptown status hook cursor", "timeout": 10}],
    "beforeSubmitPrompt": [{"command": "lamptown status hook cursor", "timeout": 10}],
    "postToolUse": [{"command": "lamptown status hook cursor", "timeout": 10}],
    "stop": [{"command": "lamptown status hook cursor", "timeout": 10}],
    "sessionEnd": [{"command": "lamptown status hook cursor", "timeout": 10}]
  }
}
"""

AGENTS: tuple[Agent, ...] = (
    Agent("claude-code", "Claude Code", ".claude", "~/.claude/settings.json",
          "merge its \"hooks\" into the file (keep the hooks you already have)",
          CLAUDE_CODE, skills=".claude/skills"),
    Agent("codex", "Codex CLI", ".codex", "~/.codex/config.toml",
          "add these lines to the file (notify alone is enough to start)",
          CODEX, skills=".codex/skills"),
    Agent("gemini-cli", "Gemini CLI", ".gemini", "~/.gemini/settings.json",
          "merge its \"hooks\" into the file (keep the hooks you already have)",
          GEMINI_CLI),
    Agent("cursor", "Cursor", ".cursor", "~/.cursor/hooks.json",
          "save it as the file, or merge its \"hooks\" into the one you have",
          CURSOR),
)
BY_ID = {a.id: a for a in AGENTS}

# The agent-status/1 folder every hook and `lamptown status` write to by default.
STATUS_FOLDER = ".agent-status"


@dataclass(frozen=True)
class Found:
    agents: tuple[Agent, ...]
    skill_dirs: tuple[str, ...]  # "~/..." forms of the skills folders that exist
    status_folder: bool  # ~/.agent-status already exists


def detect(home: Path) -> Found:
    """Which known setups exist under ``home``. Only folder existence is checked."""
    agents = tuple(a for a in AGENTS if (home / a.folder).is_dir())
    skills = tuple(
        "~/" + a.skills for a in agents if a.skills and (home / a.skills).is_dir()
    )
    return Found(agents, skills, (home / STATUS_FOLDER).is_dir())
