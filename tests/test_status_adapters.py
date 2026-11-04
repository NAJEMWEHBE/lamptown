"""Adapters against recorded payload shapes (tests/fixtures/status/*.json, fictional content).

Each fixture file lists one provider's hook payloads in the order a real session sends
them. Every case runs through ``lamptown status hook <provider>`` exactly as the provider
would call it (stdin, or a JSON argument for Codex ``notify``), then the written file is
checked against the case's ``expect`` block.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from lamptown.status import adapters, validate
from lamptown.status.adapters import claude_code, codex, cursor, folder_name, gemini_cli, make_id
from lamptown.status.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "status"
PROVIDERS = ["claude-code", "codex", "gemini-cli", "cursor"]


def _snapshot(folder: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.json"))}


def _feed(monkeypatch, text: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(text.encode("utf-8")),
                                                       encoding="utf-8"))


def _run_hook(monkeypatch, capsys, provider, case, folder) -> tuple[int, str]:
    payload = json.dumps(case["payload"])
    argv = ["hook", provider, "--dir", str(folder), *case.get("args", [])]
    if case.get("argv"):
        _feed(monkeypatch, "")
        argv.append(payload)
    else:
        _feed(monkeypatch, payload)
    capsys.readouterr()
    code = main(argv)
    return code, capsys.readouterr().out


@pytest.mark.parametrize("provider", PROVIDERS)
def test_recorded_session(provider, tmp_path, monkeypatch, capsys):
    data = json.loads((FIXTURES / f"{provider}.json").read_text(encoding="utf-8"))
    assert data["provider"] == provider
    assert data["cases"], "fixture has no cases"
    last_id = None
    for case in data["cases"]:
        name = f"{provider}: {case['name']}"
        expect = case["expect"]
        before = _snapshot(tmp_path)
        code, out = _run_hook(monkeypatch, capsys, provider, case, tmp_path)
        assert code == 0, name

        want_out = expect.get("stdout", "")
        if isinstance(want_out, dict):
            assert json.loads(out) == want_out, name
        else:
            assert out.strip() == want_out, name

        if expect.get("ignored"):
            assert _snapshot(tmp_path) == before, f"{name}: an ignored event changed a file"
            continue

        status_id = expect.get("id") or last_id
        assert status_id, f"{name}: fixture must name the id on its first case"
        last_id = status_id
        path = tmp_path / f"{status_id}.json"
        assert path.exists(), f"{name}: {path.name} not written (have {sorted(before)})"
        text = path.read_text(encoding="utf-8")
        doc = json.loads(text)
        assert validate(doc) == [], name
        assert doc["agent"] == provider, name
        assert doc["state"] == expect["state"], name
        if "message" in expect:
            assert doc.get("message") == expect["message"], name
        if "title" in expect:
            assert doc["title"] == expect["title"], name
        if "needs" in expect:
            assert len(doc.get("needs_you", [])) == expect["needs"], name
        if expect.get("started"):
            assert "started" in doc, name
        for secret in expect.get("absent", []):
            assert secret not in text, f"{name}: {secret!r} leaked into the status file"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_hook_survives_garbage(provider, tmp_path, monkeypatch, capsys):
    for junk in ("", "not json", "[1, 2]", '{"hook_event_name": 7}', "\ufeff{}"):
        _feed(monkeypatch, junk)
        assert main(["hook", provider, "--dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""
    assert list(tmp_path.iterdir()) == []


def test_hook_unknown_provider_and_bad_options_exit_zero(tmp_path, monkeypatch, capsys):
    _feed(monkeypatch, "{}")
    assert main(["hook", "nobody"]) == 0
    _feed(monkeypatch, "{}")
    assert main(["hook"]) == 0
    _feed(monkeypatch, '{"hook_event_name": "Stop", "session_id": "s1"}')
    assert main(["hook", "claude-code", "--dir", str(tmp_path), "--stale-after", "soon"]) == 0
    _feed(monkeypatch, '{"hook_event_name": "Stop", "session_id": "s1"}')
    assert main(["hook", "claude-code", "--dir", str(tmp_path), "--wat", "--on-stop", "x"]) == 0
    assert capsys.readouterr().out == ""
    doc = json.loads((tmp_path / "claude-code-s1.json").read_text(encoding="utf-8"))
    assert doc["state"] == "ok"  # an unknown --on-stop value falls back to ok


def test_hook_overrides(tmp_path, monkeypatch):
    _feed(monkeypatch, '{"hook_event_name": "UserPromptSubmit", "session_id": "s2", "cwd": "/x/y"}')
    main(["hook", "claude-code", "--dir", str(tmp_path), "--id", "kite-bot", "--title", "Kite bot",
          "--stale-after", "60"])
    doc = json.loads((tmp_path / "kite-bot.json").read_text(encoding="utf-8"))
    assert (doc["title"], doc["state"], doc["stale_after"]) == ("Kite bot", "working", 60)


def test_hook_is_unwritable_dir_safe(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x", encoding="utf-8")
    _feed(monkeypatch, '{"hook_event_name": "Stop", "session_id": "s3"}')
    assert main(["hook", "claude-code", "--dir", str(blocker / "sub")]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "skipped" in captured.err


def test_waiting_cleared_by_next_activity(tmp_path):
    base = {"session_id": "s4", "cwd": "/home/you/code/tidebook"}
    for payload in (
        {**base, "hook_event_name": "Notification", "message": "Approve?",
         "notification_type": "permission_prompt"},
        {**base, "hook_event_name": "PostToolUse", "tool_name": "Bash"},
    ):
        adapters.apply(claude_code.translate(payload), tmp_path)
    doc = json.loads((tmp_path / "claude-code-s4.json").read_text(encoding="utf-8"))
    assert doc["state"] == "working"
    assert doc["needs_you"] == []


def test_title_kept_when_later_event_has_no_folder(tmp_path):
    adapters.apply(gemini_cli.translate({"hook_event_name": "BeforeAgent", "session_id": "g",
                                         "cwd": "/home/you/code/moss-engine"}), tmp_path)
    adapters.apply(gemini_cli.translate({"hook_event_name": "SessionEnd", "session_id": "g"}),
                   tmp_path)
    doc = json.loads((tmp_path / "gemini-cli-g.json").read_text(encoding="utf-8"))
    assert doc["title"] == "moss-engine"
    adapters.apply(gemini_cli.translate({"hook_event_name": "SessionEnd", "session_id": "h"}),
                   tmp_path)
    assert json.loads((tmp_path / "gemini-cli-h.json").read_text(encoding="utf-8"))["title"] == (
        "Gemini CLI")


@pytest.mark.parametrize(("path", "name"), [
    ("/home/you/code/paper-kite", "paper-kite"),
    ("/home/you/code/paper-kite/", "paper-kite"),
    ("C:\\Users\\you\\code\\moss-engine", "moss-engine"),
    ("C:/Users/you/work/tidebook", "tidebook"),
    ("", ""),
    (None, ""),
])
def test_folder_name(path, name):
    assert folder_name(path) == name


def test_make_id_falls_back():
    assert make_id("codex", "ABC/../def") == "codex-abc-def"
    assert make_id("codex", None, "Paper Kite") == "codex-paper-kite"
    assert make_id("codex", "///", "") == "codex"


def test_long_text_is_cut_to_one_line():
    update = codex.translate({"type": "agent-turn-complete", "thread-id": "t",
                              "last-assistant-message": "line one " + "word " * 100 + "\nline two"},
                             excerpts=True)
    assert "\n" not in update.message and "line two" not in update.message
    assert len(update.message) <= len("Turn finished: ") + 80
    assert update.message.endswith("\u2026")


PLANTED = "Done. Here is your config: API_KEY=kite-fake-1234 DB_PASSWORD=moss-fake-pw"


@pytest.mark.parametrize(("provider", "payload"), [
    ("claude-code", {"hook_event_name": "Stop", "session_id": "s", "cwd": "/w/kite",
                     "last_assistant_message": PLANTED}),
    ("codex", {"type": "agent-turn-complete", "thread-id": "s", "cwd": "/w/kite",
               "last-assistant-message": PLANTED}),
    ("codex", {"hook_event_name": "Stop", "session_id": "s", "cwd": "/w/kite",
               "last_assistant_message": PLANTED}),
    ("codex", {"hook_event_name": "PostModelRequest", "session_id": "s", "status": "failed",
               "error": PLANTED}),
    ("gemini-cli", {"hook_event_name": "AfterAgent", "session_id": "s", "cwd": "/w/kite",
                    "prompt_response": PLANTED}),
    ("gemini-cli", {"hook_event_name": "Notification", "session_id": "s", "message": PLANTED}),
    ("claude-code", {"hook_event_name": "Notification", "session_id": "s", "message": PLANTED,
                     "notification_type": "elicitation_dialog"}),
    ("claude-code", {"hook_event_name": "StopFailure", "session_id": "s", "error": PLANTED}),
    ("cursor", {"hook_event_name": "afterAgentResponse", "conversation_id": "s", "text": PLANTED}),
    ("cursor", {"hook_event_name": "sessionEnd", "conversation_id": "s", "reason": "error",
                "error_message": PLANTED}),
])
def test_no_reply_text_is_written_by_default(provider, payload, tmp_path, monkeypatch, capsys):
    """The default hook writes fixed words only: no reply, notification or error text."""
    monkeypatch.delenv(adapters.ENV_EXCERPTS, raising=False)
    _feed(monkeypatch, json.dumps(payload))
    assert main(["hook", provider, "--dir", str(tmp_path)]) == 0
    for path in tmp_path.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        for planted in ("kite-fake-1234", "moss-fake-pw", "Here is your", "API_KEY"):
            assert planted not in text, f"{provider}: {planted!r} written to {path.name}"


def test_excerpts_by_env_var_are_one_short_masked_line(tmp_path, monkeypatch):
    monkeypatch.setenv(adapters.ENV_EXCERPTS, "1")
    _feed(monkeypatch, json.dumps({"hook_event_name": "Stop", "session_id": "s5", "cwd": "/w/k",
                                   "last_assistant_message": PLANTED + "\nsecond line"}))
    main(["hook", "claude-code", "--dir", str(tmp_path)])
    doc = json.loads((tmp_path / "claude-code-s5.json").read_text(encoding="utf-8"))
    assert doc["message"] == ("Turn finished: Done. Here is your config: API_KEY=\u2026 "
                              "DB_PASSWORD=\u2026")
    monkeypatch.setenv(adapters.ENV_EXCERPTS, "0")
    _feed(monkeypatch, json.dumps({"hook_event_name": "Stop", "session_id": "s5",
                                   "last_assistant_message": PLANTED}))
    main(["hook", "claude-code", "--dir", str(tmp_path)])
    doc = json.loads((tmp_path / "claude-code-s5.json").read_text(encoding="utf-8"))
    assert doc["message"] == "Turn finished"


@pytest.mark.parametrize(("text", "want"), [
    ("token: abc123", "token: \u2026"),
    ("Authorization: Bearer abcdefghijklmnop", "Authorization: \u2026"),
    ("use bearer abcdefghijklmnop now", "use bearer \u2026 now"),
    ("key is " + "Q" * 30, "key is \u2026"),
    ("all 14 tests pass", "all 14 tests pass"),
])
def test_mask_secrets(text, want):
    assert adapters.mask_secrets(text) == want


def test_hooks_keep_the_session_folder_for_private_dirs(tmp_path):
    adapters.apply(claude_code.translate({"hook_event_name": "UserPromptSubmit",
                                          "session_id": "s6", "cwd": "/w/lantern"}), tmp_path)
    doc = json.loads((tmp_path / "claude-code-s6.json").read_text(encoding="utf-8"))
    assert doc["x-folder"] == "/w/lantern" and doc["title"] == "lantern"
    assert validate(doc) == []


def test_cursor_never_answers_permission_events():
    for event in ("preToolUse", "beforeShellExecution", "beforeMCPExecution", "beforeReadFile"):
        assert cursor.respond({"hook_event_name": event}) == ""
        assert cursor.translate({"hook_event_name": event}) is None


def test_every_provider_has_an_adapter():
    for name in PROVIDERS:
        module = adapters.get(name)
        assert name == module.AGENT
        assert module.translate({}) is None
        assert module.respond({}) == ""


# --- L5-fix (review-L5 B3): Codex ids from the random end of the thread id ---
def test_codex_ids_come_from_the_random_end_of_the_thread_id():
    # UUIDv7 thread ids 1 ms apart: the same first 12 characters (a clock), other ends
    a = "019e4b1c-2d00-7a1b-8c2d-3e4f5a6b7c8d"
    b = "019e4b1c-2d01-7f0e-9d1c-0b2a39485766"
    ids = {codex.translate({"hook_event_name": "Stop", "session_id": a}).id,
           codex.translate({"type": "agent-turn-complete", "thread-id": b}).id}
    assert ids == {"codex-3e4f5a6b7c8d", "codex-0b2a39485766"}
    assert codex.thread_id(a.upper()) == "codex-3e4f5a6b7c8d"
    assert codex.thread_id(a.replace("-", "")) == "codex-3e4f5a6b7c8d"
    assert codex.thread_id("not-a-uuid") == make_id("codex", "not-a-uuid")
    assert codex.thread_id(None, "kite") == "codex-kite"


# --- W2-left: parallel Codex subagents never share an id ---
def test_codex_threads_started_in_the_same_millisecond_get_their_own_ids():
    # 10,000 UUIDv7 thread ids with one clock (as many subagents spawned at once): the id keeps
    # 48 random bits, so a clash is about a 1 in 10 million chance here
    import secrets

    clock = "019e4b1c2d00"
    ids = set()
    for _ in range(10_000):
        rest = f"{0x7000 | secrets.randbits(12):04x}{0x8000 | secrets.randbits(14):04x}"
        h = clock + rest + f"{secrets.randbits(48):012x}"
        ids.add(codex.thread_id(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"))
    assert len(ids) == 10_000
# --- end W2-left ---
