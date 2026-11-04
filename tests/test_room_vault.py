"""The ``vault`` room's data: the ``markdown_vault`` collector (a fictional notebook)."""

from __future__ import annotations

import datetime as dt
import os
import shutil
from pathlib import Path

import pytest

from lamptown import build as B
from lamptown import collectors as reg
from lamptown.collectors import vault as V
from lamptown.collectors.schema import room_schema, validate
from lamptown.config import parse_config

FIX = Path(__file__).parent / "fixtures" / "vault" / "notes"
NOW = dt.datetime(2026, 3, 17, 8, 15, tzinfo=dt.UTC)  # a Tuesday, an hour after 07:15
BUILTIN_KIND = ("markdown_vault", "lamptown.collectors.vault:collect")
FULL = {"inbox": "inbox/*.md", "dropped": "inbox/dropped", "kept": "inbox/kept",
        "reviews": "reviews/*.md", "log": "log.md", "schedule": "weekly tue 07:15"}


def _age_all(root: Path, days: float) -> None:
    t = (NOW - dt.timedelta(days=days)).timestamp()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            os.utime(Path(dirpath) / f, (t, t))


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "notes"
    shutil.copytree(FIX, root)
    _age_all(root, 3)  # nothing changed since the review slot began
    return root


def run(root: Path, **extra) -> dict:
    out = V.collect({"path": str(root), **extra, "_room": "vault", "_building": "notes",
                     "_config_dir": "."}, NOW)
    assert validate(out, room_schema("vault")) == [], out
    return out


def test_folders_are_found_and_counted(vault):
    out = run(vault, **FULL)
    got = [(f["key"], f["label"], f["n"]) for f in out["folders"]]
    assert got == [("field-notes", "FIELD NOTES", 4), ("moss", "MOSS", 1),
                   ("recipes", "RECIPES", 2),
                   (".", "LOOSE NOTES", 1), ("kept", "KEPT", 1)]
    assert out["notes"] == 8 and out["kept"] == 1  # kept notes are not counted twice
    fn = out["folders"][0]
    assert "fog-bank" in fn["notes"] and fn["href"].startswith("file:")
    assert out["vault_name"] == "notes"
    assert all("secret" not in n for f in out["folders"] for n in f["notes"])  # hidden: skipped


def test_labels_and_exclude(vault):
    out = run(vault, labels={"recipes": "KITCHEN", "moss": "MOSS"}, exclude=["field-notes"])
    keys = [f["key"] for f in out["folders"]]
    assert keys[:2] == ["recipes", "moss"]  # the labels' order first
    assert out["folders"][0]["label"] == "KITCHEN"
    assert "field-notes" not in keys
    assert "inbox" in keys  # with no inbox setting the inbox folder is a plain shelf


def test_the_inbox_notes_marks_and_where_they_came_from(vault):
    out = run(vault, **FULL)
    ib = out["inbox"]
    assert ib["date"] == "2026-03-12" and ib["file"] == "2026-03-12.md"
    assert [i["title"] for i in ib["items"]] == [
        "Tail ribbons for the paper kite", "A moss calendar", "Tide tables as a game board",
        "Otter's lighthouse sketch"]
    assert [i["mark"] for i in ib["items"]] == ["keep", None, "drop", None]
    assert [i["triaged"] for i in ib["items"]] == [True, False, True, False]
    assert ib["items"][0]["from"] == "paper-kite" and ib["items"][1]["from"] == "Moss Engine"
    assert ib["items"][2]["reason"] == "too close to Tidebook"
    assert (ib["untriaged"], ib["kept"], ib["drops"], ib["total"]) == (2, 1, 1, 4)
    assert out["dropped"]["n"] == 2 and "glass-kite" in out["dropped"]["names"]
    assert out["state"] == "waiting" and out["why"] == "inbox"
    assert out["needs"] == [{"text": "2 inbox notes to sort", "href": ib["href"]}]


def test_reviews_last_and_next(vault):
    out = run(vault, **FULL)
    assert out["last_review"] == "2026-03-10T07:15:00Z"  # 2026-13-40 in the log is no day
    assert out["last_review_href"].endswith("2026-03-10.md")
    assert out["next_review"] == "2026-03-24T07:15:00Z"
    assert out["review_when"] == "Tuesdays 07:15"
    assert out["log_href"].endswith("log.md")


def test_a_missed_review_is_red(vault):
    no_inbox = {k: v for k, v in FULL.items() if k != "inbox"}  # an unsorted inbox would win
    out = run(vault, **dict(no_inbox, reviews="none/*.md", log="none.md"))
    assert out["state"] == "missing" and out["why"] == "never"  # reviews set up, none on record
    (vault / "reviews" / "2026-03-10.md").unlink()
    (vault / "log.md").write_text("## [2026-02-28] review\n", encoding="utf-8")
    _age_all(vault, 3)
    out = run(vault, **FULL)
    assert (out["state"], out["why"]) == ("error", "missed")
    assert out["label"] == "the review missed its run (last 2026-02-28)"


def test_a_review_running_now_is_working(vault):
    t = (NOW - dt.timedelta(minutes=30)).timestamp()
    os.utime(vault / "moss" / "types-of-moss.md", (t, t))
    out = run(vault, **FULL)
    assert (out["state"], out["why"]) == ("working", "running")
    out = run(vault, **dict(FULL, run_hours=0.5))  # 1 h after the slot: not in its window
    assert out["state"] == "waiting"


def test_inbox_edits_alone_do_not_count_as_a_running_review(vault):
    t = (NOW - dt.timedelta(minutes=30)).timestamp()
    os.utime(vault / "inbox" / "2026-03-12.md", (t, t))
    assert run(vault, **FULL)["state"] == "waiting"


def test_all_sorted_is_ok(vault):
    p = vault / "inbox" / "2026-03-12.md"
    p.write_text(p.read_text(encoding="utf-8").replace("## 2. A moss calendar",
                 "## 2. A moss calendar\ntriage: keep").replace("lighthouse sketch\n",
                 "lighthouse sketch\nTriage: drop\n"), encoding="utf-8")
    _age_all(vault, 3)
    out = run(vault, **FULL)
    assert out["state"] == "ok" and out["label"] == "8 notes, all sorted" and "needs" not in out


def test_a_missing_vault_is_grey(tmp_path):
    out = run(tmp_path / "nowhere", schedule="weekly tue 07:15")
    assert (out["state"], out["why"], out["folders"]) == ("missing", "nosource", [])
    assert out["next_review"] == "2026-03-24T07:15:00Z"


def test_custom_patterns(vault):
    p = vault / "inbox" / "2026-03-13.md"
    p.write_text("# x\n* one [yes]\n* two\n* three [no]\n", encoding="utf-8")
    _age_all(vault, 3)
    out = run(vault, inbox="inbox/*.md", item=r"^\*\s+", triage=r"\[(yes|no)\]")
    assert [i["mark"] for i in out["inbox"]["items"]] == ["keep", None, "drop"]


def test_bad_settings(vault):
    with pytest.raises(ValueError, match="path"):
        V.collect({}, NOW)
    with pytest.raises(ValueError, match="triage"):
        run(vault, inbox="inbox/*.md", triage="(")
    with pytest.raises(ValueError, match="run_hours"):
        run(vault, **dict(FULL, run_hours=-2))


def test_the_count_stops_at_the_budget(vault, monkeypatch):
    monkeypatch.setattr(V, "MAX_FILES", 3)
    out = run(vault)
    assert sum(f["n"] for f in out["folders"] if f["key"] != ".") <= 3  # loose notes: one listing


def test_through_a_build(vault, tmp_path, monkeypatch):
    monkeypatch.setitem(reg.BUILTIN, *BUILTIN_KIND)  # until the registry lists markdown_vault
    toml = f"""
[[building]]
id = "notes"
type = "vault"
label = "Notebook"
  [[building.source]]
  kind = "markdown_vault"
  path = "{vault.as_posix()}"
  inbox = "inbox/*.md"
  dropped = "inbox/dropped"
  reviews = "reviews/*.md"
  schedule = "weekly tue 07:15"
"""
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    res = B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW)
    room = res.data["rooms"]["notes"]
    assert room["state"] == "waiting" and res.data["parts"]["notes"]["num"] == "9"
    assert res.data["parts"]["notes"]["lamp"] == "amber"
    assert 'data-room="vault"' in res.town.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- in the browser
from _browser import browser  # noqa: E402  (shared headless browser)
from test_town import run as probe  # noqa: E402

SEEN = "return Object.assign({ id: RS.id, mode: RS.mode, plate: RS.room.plate() }, RS.room.seen());"


def build_vault(tmp_path, monkeypatch, root: Path) -> Path:
    monkeypatch.setitem(reg.BUILTIN, *BUILTIN_KIND)
    extra = "".join(f'  {k} = "{v}"\n' for k, v in FULL.items())
    toml = (f'[[building]]\nid = "notes"\ntype = "vault"\nlabel = "Notebook"\n'
            f'  [[building.source]]\n  kind = "markdown_vault"\n'
            f'  path = "{root.as_posix()}"\n{extra}')
    p = tmp_path / "lamptown.toml"
    p.write_text(toml, encoding="utf-8")
    return B.build(parse_config(toml, p), out_dir=tmp_path / "out", now=NOW).town


@browser
def test_room_on_fixtures_one_line_per_folder(vault, tmp_path, monkeypatch):
    page = build_vault(tmp_path, monkeypatch, vault)
    r = probe(page, """const s = RS.room.seen();
      const tips = allTargets().filter(t => /^(line|idea)-/.test(t.id)).map(t => [t.id, t.tip]);
      return Object.assign({ id: RS.id, plate: RS.room.plate(), tips }, s);""", frag="#room=notes")
    assert (r["id"], r["sample"], r["state"], r["gone"]) == ("notes", False, "waiting", False)
    assert r["plate"] == {"num": "8", "state": "waiting"}
    lines = {x["key"]: x for x in r["lines"]}
    assert list(lines) == ["field-notes", "moss", "recipes", ".", "kept"]
    assert lines["field-notes"]["n"] == 4 and lines["field-notes"]["fish"] == 4
    assert lines["kept"]["kept"] is True and lines["."]["label"] == "LOOSE NOTES"
    assert [i["mark"] for i in r["ideas"]] == ["keep", None, "drop", None] and r["untriaged"] == 2
    assert r["cat"] is True and r["barrel"] is False
    tips = dict(r["tips"])
    assert tips["line-field-notes"][0] == "field-notes/"
    assert any("4" in t for t in tips["line-field-notes"])
    assert tips["idea-1"][0] == "Idea 1: Tail ribbons for the paper kite"


@browser
def test_a_missing_vault_is_dark_with_no_cat(tmp_path, monkeypatch):
    page = build_vault(tmp_path, monkeypatch, tmp_path / "nowhere")
    r = probe(page, SEEN, frag="#room=notes")
    assert r["state"] == "missing" and r["gone"] is True and r["cat"] is False
    assert r["plate"]["num"] == "-" and all(x["fish"] == 0 for x in r["lines"])


@browser
@pytest.mark.parametrize(("state", "check"), [
    ("ok", lambda r: r["untriaged"] == 0 and not r["barrel"]),
    ("waiting", lambda r: r["untriaged"] > 0),
    ("working", lambda r: r["barrel"] is True),
    ("error", lambda r: r["why"] == "missed"),
    ("missing", lambda r: r["gone"] and not r["cat"]),
])
def test_demo_shows_each_state_on_a_sample_notebook(tmp_path, monkeypatch, state, check):
    page = build_vault(tmp_path, monkeypatch, tmp_path / "nowhere")
    wait = "await wait(3500);" if state == "ok" else ""  # ok: the replay sorts the tray first
    r = probe(page, wait + SEEN, query=f"?t=300&demo=1&state={state}", frag="#room=notes")
    assert r["sample"] is True and r["state"] == state
    assert check(r), r


@browser
def test_demo_scale_bundles_and_keys(tmp_path, monkeypatch):
    page = build_vault(tmp_path, monkeypatch, tmp_path / "nowhere")
    r = probe(page, """const ids = allTargets().map(t => t.id);
      const a = Object.assign({ ids }, RS.room.seen());
      document.dispatchEvent(new KeyboardEvent('keydown', { key: '5', bubbles: true }));
      await wait(100);
      return { a, b: RS.room.seen() };""",
              query="?t=300&demo=1&state=waiting&ideas=9&folders=10&scale=8", frag="#room=notes")
    a = r["a"]
    assert a["sample"] is True and len(a["ideas"]) == 9 and "tray-more" in a["ids"]
    assert any(x["more"] for x in a["lines"])  # a "+N MORE" line past the room's rows
    assert all(x["fish"] <= 40 for x in a["lines"])  # big folders are bundles, not a fish each
    assert r["b"]["state"] == "missing" and r["b"]["gone"] is True
