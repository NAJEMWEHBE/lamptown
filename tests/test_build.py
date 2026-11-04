"""`lamptown build`: config -> collectors -> checked data -> town.html + town-details.html."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

from lamptown import build as B
from lamptown import cli
from lamptown.config import PLOTS, parse_config

NOW = dt.datetime(2026, 3, 14, 22, 0, tzinfo=dt.UTC)
SLOT_RE = re.compile(r'<script id="town-data" type="application/json">(.*?)</script>', re.S)


def page_data(html: str) -> dict:
    return json.loads(SLOT_RE.search(html).group(1))


def cfg(tmp_path: Path, text: str):
    p = tmp_path / "lamptown.toml"
    p.write_text(text, encoding="utf-8")
    return parse_config(text, p)


FICTIONAL = """
[town]
title = "Kite field"
latitude = 51.5
longitude = -0.1

[[building]]
id = "agents"
type = "harbour"
label = "Wren and Otter"
  [[building.source]]
  kind = "static"
  state = "waiting"
  needs = [{ text = "Approve the plan", href = "https://example.com/pr/12" }]
  rows = [
    { id = "paper-kite", name = "Paper Kite", status = "active", state = "waiting", busy = true },
    { id = "moss-engine", name = "Moss Engine", status = "paused", state = "ok" },
  ]

[[building]]
id = "nightly"
type = "jobs"
label = "Night jobs"
  [[building.source]]
  kind = "static"
  state = "working"
  rows = [{ name = "Tidebook index", state = "working", next = "2026-03-15T02:00:00+00:00" }]

[[building]]
id = "kiosk"
type = "kiosk"
plot = "lab"
label = "Tea kiosk"
  [[building.source]]
  kind = "flaky"
  collector = "./flaky.py"
"""


@pytest.fixture
def fictional(tmp_path):
    (tmp_path / "flaky.py").write_text(
        "def collect(paths, now):\n    raise RuntimeError('the kettle is unplugged')\n",
        encoding="utf-8")
    return cfg(tmp_path, FICTIONAL)


# ----------------------------------------------------------------------------- the empty town
def test_empty_config_builds_six_dark_lots(tmp_path):
    res = B.build(parse_config(""), out_dir=tmp_path / "out", now=NOW)
    assert res.town == tmp_path / "out" / "town.html" and res.town.is_file()
    assert res.details.is_file()
    d = page_data(res.town.read_text(encoding="utf-8"))
    assert d["plots"] == {p: None for p in PLOTS}
    assert d["parts"] == {} and d["rooms"] == {} and d["needs"] == 0
    assert d["spec"] == "lamptown-town/1"
    # neutral default: the page guesses the place from the UTC offset
    assert d["sky"] == {"lat": None, "lon": None}
    assert res.warnings == []
    details = res.details.read_text(encoding="utf-8")
    assert "every plot is an empty lot" in details
    assert "Nothing is waiting for you" in details


def test_page_is_self_contained(tmp_path):
    res = B.build(parse_config(""), out_dir=tmp_path, now=NOW)
    html = res.town.read_text(encoding="utf-8")
    assert '<script src=' not in html
    assert "/*%%TOWN_DATA%%*/" not in html and "<!--%%ROOMS%%-->" not in html
    assert page_data(html)["spec"] == "lamptown-town/1"
    assert "registerRoom" in html and "const KIT" in html
    assert "bootRooms();" in html


def test_no_temp_files_left_behind(tmp_path):
    B.build(parse_config(""), out_dir=tmp_path, now=NOW)
    B.build(parse_config(""), out_dir=tmp_path, now=NOW)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["town-details.html", "town.html"]


# ----------------------------------------------------------------------------- a fictional town
def test_fictional_town_data(tmp_path, fictional):
    res = B.build(fictional, out_dir=tmp_path / "out", now=NOW)
    d = res.data
    assert d["title"] == "Kite field"
    assert d["sky"] == {"lat": 51.5, "lon": -0.1}
    assert d["plots"] == {"tower": "nightly", "library": None, "gate": None, "lab": "kiosk",
                          "workshop": None, "boathouse": "agents"}
    agents, nightly, kiosk = d["parts"]["agents"], d["parts"]["nightly"], d["parts"]["kiosk"]
    assert (agents["lamp"], agents["num"], agents["name"]) == ("amber", "1", "Wren and Otter")
    assert agents["waiting"] == ["Approve the plan"]
    assert agents["links"][0] == {"href": "https://example.com/pr/12",
                                  "label": "Open what's waiting"}
    assert (nightly["lamp"], nightly["state"], nightly["num"]) == ("green", "working", "1")
    assert kiosk["lamp"] == "red" and "kettle is unplugged" in kiosk["result"]
    assert d["needs"] == 1
    assert d["fixedClock"] is True and d["builtIso"] == NOW.isoformat()
    assert any("kettle" in w for w in res.warnings)
    assert page_data(res.town.read_text(encoding="utf-8")) == d


def test_details_page_lists_every_building(tmp_path, fictional):
    res = B.build(fictional, out_dir=tmp_path, now=NOW)
    h = res.details.read_text(encoding="utf-8")
    for bid in ("agents", "nightly", "kiosk"):
        assert f'id="b-{bid}"' in h
    assert "1 waiting for you" in h
    assert "Empty lots: library, gate, workshop" in h
    assert 'href="https://example.com/pr/12"' in h
    assert "<table>" in h and "Paper Kite" in h
    # the section's state class must not restyle the state badges inside it
    assert 'class="bld bs-waiting"' in h and 'class="bld st-' not in h


def test_schema_failure_turns_the_room_red(tmp_path):
    c = cfg(tmp_path, '[[building]]\nid = "jobs"\n[[building.source]]\nkind = "static"\n'
                      'rows = [{ name = "x", state = "ok", next = "in 3 h" }]\n')
    res = B.build(c, out_dir=tmp_path / "o", now=NOW)
    room = res.data["rooms"]["jobs"]
    assert room["state"] == "error" and "jobs room" in room["error"] and "next" in room["error"]
    assert res.data["parts"]["jobs"]["lamp"] == "red"


def test_custom_type_skips_the_schema(tmp_path):
    c = cfg(tmp_path, '[[building]]\nid = "k"\ntype = "kiosk"\nplot = "gate"\n'
                      '[[building.source]]\nkind = "static"\nanything = "goes"\n')
    assert B.build(c, out_dir=tmp_path / "o", now=NOW).data["rooms"]["k"] == {
        "state": "ok", "anything": "goes"}


def test_building_without_sources_is_grey(tmp_path):
    c = cfg(tmp_path, '[[building]]\nid = "jobs"\n')
    d = B.build(c, out_dir=tmp_path / "o", now=NOW).data
    assert d["parts"]["jobs"]["lamp"] == "grey" and d["parts"]["jobs"]["num"] == ""


def test_waiting_without_needs_counts_once(tmp_path):
    c = cfg(tmp_path, '[[building]]\nid = "jobs"\n[[building.source]]\nkind = "static"\n'
                      'state = "waiting"\n')
    assert B.build(c, out_dir=tmp_path / "o", now=NOW).data["needs"] == 1


def test_harbour_plate_counts_active_projects_or_every_agent_row():
    rows = [{"name": "a", "state": "ok", "status": "active"},
            {"name": "b", "state": "ok", "status": "paused"},
            {"name": "c", "state": "ok", "status": "done"}]
    assert B.plate_num("harbour", {"rows": rows}) == "1"
    agents = [{"name": "a", "state": "working", "status": "Running tests"},
              {"name": "b", "state": "waiting", "status": "Plan ready"}]
    assert B.plate_num("harbour", {"rows": agents}) == "2"


# ----------------------------------------------------------------------------- escaping
def test_hostile_text_cannot_break_out_of_the_page(tmp_path):
    evil = "</script><img src=x onerror=alert(1)>"
    c = cfg(tmp_path, '[town]\ntitle = "</title><b>t</b>"\n[[building]]\nid = "jobs"\n'
                      'label = "</script><i id=pwn>"\n[[building.source]]\nkind = "static"\n'
                      f'state = "waiting"\nneeds = [{{ text = "{evil}" }}]\n'
                      f'rows = [{{ name = "{evil}", state = "ok" }}]\n')
    res = B.build(c, out_dir=tmp_path / "o", now=NOW)
    town = res.town.read_text(encoding="utf-8")
    assert "<img" not in town and "<i id=pwn>" not in town
    assert town.count("</script>") == town.count("<script")
    assert "<title>&lt;/title&gt;&lt;b&gt;t&lt;/b&gt;</title>" in town
    assert page_data(town)["parts"]["jobs"]["label"] == "</script><i id=pwn>"
    details = res.details.read_text(encoding="utf-8")
    assert "<img" not in details and "<i id=pwn>" not in details and "<b>t</b>" not in details


SHORT = f"{B.DATA_SLOT} {B.ROOMS_SLOT}"  # titles and labels have a length cap
SLOTS = f"{SHORT} <title>Lamptown</title> {B.SCRIPT_TAG.format(name='kit.js')}"


def test_values_that_spell_a_slot_stay_text(tmp_path):
    # the first match used to be replaced: a title holding the data slot took the data and left
    # the real slot empty, so the page could not read its buildings
    c = cfg(tmp_path, f"[town]\ntitle = {json.dumps(SHORT)}\n[[building]]\nid = \"jobs\"\n"
                      f"label = {json.dumps(B.DATA_SLOT)}\n[[building.source]]\nkind = \"static\"\n"
                      f"state = \"waiting\"\nneeds = [{{ text = {json.dumps(SLOTS)} }}]\n")
    town = B.build(c, out_dir=tmp_path / "o", now=NOW).town.read_text(encoding="utf-8")
    data = page_data(town)
    assert data["title"] == SHORT and data["parts"]["jobs"]["label"] == B.DATA_SLOT
    assert data["rooms"]["jobs"]["needs"][0]["text"] == SLOTS
    assert "%" not in SLOT_RE.search(town).group(1)  # no value can spell a slot in the data
    assert f"<title>{SHORT.replace('<', '&lt;').replace('>', '&gt;')}</title>" in town
    assert town.count('<script id="town-data"') == 1 and town.count("<title>") == 1
    assert town.count('<script data-src="kit.js">') == 1
    assert town.count("</script>") == town.count("<script")


def test_fill_slots_puts_each_slot_once_and_never_rescans():
    tpl = "A[x]B[y]C[x]"
    assert B.fill_slots(tpl, {"[x]": "[y]", "[y]": "[x]"}) == "A[y]B[x]C[x]"


def test_script_json_and_block_escaping():
    s = B.script_json({"a": "</script><!--  "})
    assert "<" not in s and " " not in s
    assert json.loads(s) == {"a": "</script><!--  "}
    blk = B.script_block("let a = '</script>'; // <!--")
    assert blk.count("</script>") == 1


# ----------------------------------------------------------------------------- room scripts
def test_room_js_is_inlined_once_per_type(tmp_path):
    (tmp_path / "kiosk.js").write_text("registerRoom('kiosk', { draw() {} }); // KIOSK-ROOM",
                                       encoding="utf-8")
    one = '[[building]]\nid = "{}"\ntype = "kiosk"\nplot = "{}"\nroom_js = "kiosk.js"\n'
    c = cfg(tmp_path, one.format("a", "lab") + one.format("b", "gate"))
    town = B.build(c, out_dir=tmp_path / "o", now=NOW).town.read_text(encoding="utf-8")
    assert town.count("KIOSK-ROOM") == 1 and 'data-room="kiosk"' in town


def test_missing_room_js_warns_but_builds(tmp_path):
    c = cfg(tmp_path, '[[building]]\nid = "a"\ntype = "kiosk"\nplot = "lab"\nroom_js = "nope.js"\n')
    res = B.build(c, out_dir=tmp_path / "o", now=NOW)
    assert res.town.is_file() and any("room script" in w for w in res.warnings)


def test_room_from_entry_point_forms(tmp_path):
    f = tmp_path / "r.js"
    f.write_text("registerRoom('x', {draw(){}})", encoding="utf-8")
    assert "registerRoom" in B._room_from_entry_point(lambda: "registerRoom('x', {draw(){}})")
    assert "registerRoom" in B._room_from_entry_point(str(f))
    assert "registerRoom" in B._room_from_entry_point(f)
    with pytest.raises(TypeError):
        B._room_from_entry_point(3)


# ----------------------------------------------------------------------------- output folder
def test_out_dir_order(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "user_data_dir", lambda: tmp_path / "userdata")
    assert B.default_out_dir(parse_config("")) == tmp_path / "userdata" / "town"
    c = cfg(tmp_path, '[town]\nout = "site"\n')
    assert B.default_out_dir(c) == tmp_path / "site"
    assert B.build(c, now=NOW).town == tmp_path / "site" / "town.html"


def test_naive_now_is_local_time(tmp_path):
    res = B.build(parse_config(""), out_dir=tmp_path, now=dt.datetime(2026, 3, 14, 12, 0))
    assert dt.datetime.fromisoformat(res.data["builtIso"]).tzinfo is not None


# ----------------------------------------------------------------------------- CLI
def test_cli_build(tmp_path, fictional, capsys, monkeypatch):
    monkeypatch.delenv("LAMPTOWN_CONFIG", raising=False)
    out = tmp_path / "cli-out"
    code = cli.main(["build", "--config", str(tmp_path / "lamptown.toml"), "--out", str(out),
                     "--now", "2026-03-14T22:00:00Z"])
    assert code == 0
    o = capsys.readouterr()
    assert "3 buildings" in o.out and "kettle" in o.err
    assert page_data((out / "town.html").read_text(encoding="utf-8"))["builtIso"] == NOW.isoformat()


def test_cli_build_quiet_and_bad_config(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("version = 9\n", encoding="utf-8")
    assert cli.main(["build", "--config", str(bad), "--out", str(tmp_path)]) == cli.EXIT_CONFIG
    assert "version" in capsys.readouterr().err
    assert cli.main(["build", "--config", str(tmp_path / "missing.toml")]) == cli.EXIT_CONFIG


def test_cli_bad_now_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["build", "--now", "tomorrow"])
    assert e.value.code == 2


def test_cli_open_builds_then_opens(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    c = tmp_path / "lamptown.toml"
    c.write_text("", encoding="utf-8")
    assert cli.main(["open", "-q", "--config", str(c), "--out", str(tmp_path / "o")]) == 0
    assert opened == [(tmp_path / "o" / "town.html").resolve().as_uri()]


def test_cli_without_a_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "build" in capsys.readouterr().out


def test_cli_status_group_registers_when_present():
    sub = next(a for a in cli.build_parser()._actions if a.dest == "command")
    try:
        import lamptown.status.cli  # noqa: F401
    except ImportError:
        assert "status" not in sub.choices
    else:
        assert "status" in sub.choices
