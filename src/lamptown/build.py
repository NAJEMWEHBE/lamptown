"""Build the town: config -> collectors -> checked room data -> one self-contained ``town.html``.

Also writes ``town-details.html``, a plain accessible text view of the same data, and
``town-data.js`` (``lamptownData(<the same data>);``: a live page swaps it in when the data's
``rev`` changes). With ``[live]`` on, ``town-history.js`` and ``town-live.js`` too (see
``lamptown.livetown``), in that order: ``town-data.js`` first, ``town-live.js`` last. Every file
is written atomically (temp file + rename), so a reader (a browser, the desktop app) never sees
half a page.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import html
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata, resources
from pathlib import Path
from typing import Any

from lamptown import __version__, livetown
from lamptown.collectors import (
    TIMEOUT,
    clean,
    clean_text,
    error_result,
    merge,
    run_sources,
    safe_href,
    source_paths,
)
from lamptown.collectors.jobs_schedule import LOCAL
from lamptown.collectors.schema import room_schema, validate
from lamptown.config import LIGHTHOUSE, PLOTS, Building, Config, user_data_dir

TOWN_NAME = "town.html"
DETAILS_NAME = "town-details.html"
DATA_JS = "town-data.js"
LIVE_JS = "town-live.js"
HISTORY_JS = "town-history.js"
HISTORY_EVERY = 60  # seconds: town-history.js is rewritten at most this often between builds
DATA_SPEC = "lamptown-town/1"
ROOM_ENTRY_GROUP = "lamptown.rooms"

DATA_SLOT = "/*%%TOWN_DATA%%*/"
ROOMS_SLOT = "<!--%%ROOMS%%-->"
SCRIPT_TAG = '<script src="{name}"></script>'
WEB_SCRIPTS = ("kit.js", "town.js",
               "lighthouse.js")  # live: L8 lighthouse (draws only with parts.lighthouse)
WEB_SCRIPTS += ("live.js",)  # live: L7 lamplighters (one figure per live agent)
WEB_SCRIPTS += ("berths.js",)  # live: L10 harbour berths (one per live project)

LAMP_OF_STATE = {"ok": "green", "working": "green", "waiting": "amber", "error": "red",
                 "missing": "grey"}
LAMP_WORDS = {"green": "Running fine", "amber": "Needs you", "red": "Something is wrong",
              "grey": "Can't see it"}
# harbour rows whose ``status`` is one of these are projects with a life-cycle; any other text is a
# message
PROJECT_STATUSES = frozenset({"active", "paused", "parked", "done", "closed", "archived"})
STATE_WORDS = {"ok": "running fine", "working": "running now", "waiting": "needs you",
               "error": "something is wrong", "missing": "can't see it"}


@dataclass
class BuildResult:
    town: Path
    details: Path
    data: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    data_js: Path | None = None
    live: Path | None = None  # town-live.js, with [live] on
    history: Path | None = None  # town-history.js, with [live] on


# ----------------------------------------------------------------------------- rooms
def _status_paths(config: Config, paths: dict) -> dict:
    """A ``status_dir`` source reads the live town's status folder when it names none, and
    every ``[live] private_dirs`` entry is private in it too: the Watcher and the room then
    mask the same records with the same ids."""
    if "dir" not in paths and "dirs" not in paths:
        paths["dir"] = str(livetown.live_status_dir(config))
    if config.live.private_dirs:
        own = paths.get("private_dirs") or []
        own = [own] if isinstance(own, str) else list(own)
        paths["private_dirs"] = own + [d for d in config.live.private_dirs if d not in own]
    return paths


def collect_rooms(config: Config, now: dt.datetime, timeout: float = TIMEOUT) -> dict[str, dict]:
    """Room data per building id: every source run at once, merged, then checked against its schema.
    """
    jobs: list[tuple[str, str | None, dict]] = []
    owner: list[str] = []
    for b in config.buildings:
        for s in b.sources:
            paths = source_paths(s.table, b.id, b.type, config.base_dir)
            if s.kind == "status_dir" and not s.collector:
                paths = _status_paths(config, paths)
            jobs.append((s.kind, s.collector, paths))
            owner.append(b.id)
    results = run_sources(jobs, now, timeout) if jobs else []
    per: dict[str, list[dict]] = {b.id: [] for b in config.buildings}
    for bid, r in zip(owner, results, strict=True):
        per[bid].append(r)
    return {b.id: check_room(b, merge(per[b.id])) for b in config.buildings}


def check_room(b: Building, data: dict[str, Any]) -> dict[str, Any]:
    """Validate merged room data against the room type's schema (custom types skip this)."""
    schema = room_schema(b.type)
    if schema is None:
        return data
    errs = validate(data, schema)
    if not errs:
        return data
    more = f" (and {len(errs) - 1} more)" if len(errs) > 1 else ""
    return error_result(f"its data doesn't fit the {b.type} room: {errs[0]}{more}")


def plate_num(rtype: str, room: dict[str, Any]) -> str:
    """The number on a building's name plate."""
    if "num" in room and isinstance(room["num"], (str, int)) and not isinstance(room["num"], bool):
        return str(room["num"])[:6]
    rows = room.get("rows") if isinstance(room.get("rows"), list) else None
    if rtype == "vault":
        if isinstance(room.get("notes"), int):
            return str(room["notes"])
        folders = room.get("folders") or []
        return str(sum(f.get("n", 0) for f in folders if isinstance(f, dict))) if folders else ""
    if rtype == "contest":
        return str(len(room.get("entries") or [])) if room.get("entries") else ""
    if rows is None:
        return ""
    if rtype == "shelf" and any("stage" in r for r in rows):
        return str(sum(1 for r in rows if r.get("stage") == "installed"))
    if rtype == "harbour" and any(r.get("status") in PROJECT_STATUSES for r in rows):
        # project rows: the plate counts the active ones (agent berths carry no project
        # status, their text is in `message`, so they count as active)
        return str(sum(1 for r in rows if r.get("status") not in PROJECT_STATUSES - {"active"}))
    return str(len(rows))


def needs_of(room: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for n in room.get("needs") or []:
        item = {"text": str(n.get("text", ""))[:300]}
        h = safe_href(n.get("href"))
        if h:
            item["href"] = h
        out.append(item)
    return out


def part_for(b: Building, room: dict[str, Any]) -> dict[str, Any]:
    st = room.get("state", "missing")
    lamp = LAMP_OF_STATE.get(st, "grey")
    words = LAMP_WORDS[lamp]
    said = str(room.get("error") or room.get("label") or "")
    needs = needs_of(room)
    links = []
    first = next((n["href"] for n in needs if n.get("href")), None)
    if first:
        links.append({"href": first, "label": "Open what's waiting"})
    links.append({"href": f"{DETAILS_NAME}#b-{b.id}", "label": "Details"})
    return {
        "id": b.id, "plot": b.plot, "type": b.type, "name": b.label, "label": b.label,
        "lamp": lamp, "state": st, "num": "" if lamp == "grey" else plate_num(b.type, room),
        "status": words + (f" ({STATE_WORDS[st]})." if st == "working" else "."),
        "result": said, "waiting": [n["text"] for n in needs], "links": links,
        "configured": True,
    }


def town_data(config: Config, rooms: dict[str, dict], now: dt.datetime,
              fixed_clock: bool = False, live: dict[str, Any] | None = None, *,
              room_js: list[str] | None = None) -> dict[str, Any]:
    """The data the town page reads (``D`` in the page), with its ``rev``.

    ``live`` (the live payload, when ``[live]`` is on) adds ``parts.lighthouse``: the live
    town's own place, not one of the six plots (``plots`` keeps only those). ``room_js``: the
    room types whose script the page carries (``roomJs``): a live swap that brings one the
    loaded page lacks reloads the page once, since only a reload brings its script."""
    data = _town_data(config, rooms, now, fixed_clock)
    if live is not None:
        part = livetown.lighthouse_part(live, DETAILS_NAME)
        data["parts"][LIGHTHOUSE] = part
        data["rooms"] = {**data["rooms"],
                         LIGHTHOUSE: {"state": part["state"], "label": part["result"]}}
    if room_js is not None:  # pr1-review
        data["roomJs"] = list(room_js)
    data["rev"] = livetown.rev_of(data)
    return data


def _town_data(config: Config, rooms: dict[str, dict], now: dt.datetime,
               fixed_clock: bool) -> dict[str, Any]:
    parts = {b.id: part_for(b, rooms.get(b.id, {"state": "missing"})) for b in config.buildings}
    plots = {p: None for p in PLOTS}
    for b in config.buildings:
        plots[b.plot] = b.id
    needs = 0
    for b in config.buildings:
        room = rooms.get(b.id, {})
        n = len(room.get("needs") or [])
        needs += n if n else (1 if room.get("state") == "waiting" else 0)
    return {
        "spec": DATA_SPEC, "version": __version__,
        "title": config.town.title,
        "built": f"{now:%a} {now.day} {now:%b}, {now:%H:%M}", "builtIso": now.isoformat(),
        "fixedClock": bool(fixed_clock),
        "sky": {"lat": config.town.latitude, "lon": config.town.longitude},
        "needs": needs, "details": DETAILS_NAME,
        "plots": plots, "parts": parts, "rooms": rooms,
    }


# ----------------------------------------------------------------------------- page
def web_text(name: str) -> str:
    return resources.files("lamptown").joinpath("web", name).read_text(encoding="utf-8")


def script_json(obj: Any) -> str:
    """JSON that is safe inside a <script> element: every '<' becomes \\u003c (still valid JSON and
    JS), and every '%' \\u0025, so no value can spell the empty template's data slot."""
    return json.dumps(obj, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c") \
        .replace("%", "\\u0025").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def script_block(src: str, attrs: str = "") -> str:
    """Code inside its own <script>: no '</script' or '<!--' can end or derail the block."""
    src = re.sub(r"</(script)", r"<\\/\1", src, flags=re.I).replace("<!--", "<\\!--")
    return f"<script{attrs}>\n{src}\n</script>"


def room_scripts(config: Config, warnings: list[str]) -> str:
    """One <script data-room="<type>"> per room type in use: the building's ``room_js`` file, else
    an
    installed ``lamptown.rooms`` entry point, else a built-in ``web/rooms/<type>.js``."""
    eps = None
    blocks, done = [], set()
    for b in config.buildings:
        if b.type in done:
            continue
        src: str | None = None
        try:
            if b.room_js:
                src = Path(b.room_js).read_text(encoding="utf-8-sig")
            else:
                if eps is None:
                    try:
                        eps = {e.name: e for e in metadata.entry_points(group=ROOM_ENTRY_GROUP)}
                    except Exception:
                        eps = {}
                if b.type in eps:
                    src = _room_from_entry_point(eps[b.type].load())
                else:
                    f = resources.files("lamptown").joinpath("web", "rooms", f"{b.type}.js")
                    src = f.read_text(encoding="utf-8") if f.is_file() else None
        except Exception as e:  # a broken room never stops the town
            warnings.append(
                f"building {b.id!r}: can't load its room script ({type(e).__name__}: {e})"
            )
            src = None
        if src:
            done.add(b.type)
            blocks.append(script_block(src, f' data-room="{html.escape(b.type)}"'))
    # --- live: L9 lighthouse room (the keeper's handover book; the type is reserved) ---
    if config.live.enabled and LIGHTHOUSE not in done:
        f = resources.files("lamptown").joinpath("web", "rooms", f"{LIGHTHOUSE}.js")
        blocks.append(script_block(f.read_text(encoding="utf-8"), f' data-room="{LIGHTHOUSE}"'))
    # --- end live: L9 ---
    return "\n".join(blocks)


def _room_from_entry_point(obj: Any) -> str:
    """A ``lamptown.rooms`` entry point gives the room's JS: a string of code, a path to a .js file,
    or
    a callable returning either."""
    if callable(obj):
        obj = obj()
    if isinstance(obj, os.PathLike):
        return Path(obj).read_text(encoding="utf-8-sig")
    if isinstance(obj, str):
        if obj.endswith(".js") and "\n" not in obj and Path(obj).is_file():
            return Path(obj).read_text(encoding="utf-8-sig")
        return obj
    raise TypeError(f"expected JS text or a path, got {type(obj).__name__}")


def render_town(data: dict[str, Any], rooms_html: str = "") -> str:
    tpl = web_text("town.html")
    if DATA_SLOT not in tpl or ROOMS_SLOT not in tpl:
        raise RuntimeError("the town template has lost its data or rooms slot")
    fills: dict[str, str] = {}
    for name in WEB_SCRIPTS:
        tag = SCRIPT_TAG.format(name=name)
        if tag not in tpl:
            raise RuntimeError(f"the town template has no {tag}")
        fills[tag] = script_block(web_text(name), f' data-src="{name}"')
    title = html.escape(str(data.get("title") or "Lamptown"))
    fills["<title>Lamptown</title>"] = f"<title>{title}</title>"
    fills[DATA_SLOT] = script_json(data)
    fills[ROOMS_SLOT] = rooms_html
    return fill_slots(tpl, fills)


def fill_slots(tpl: str, fills: dict[str, str]) -> str:
    """Each slot's first place in the template gets its text, in one pass over the template only:
    text that goes in is never searched again, so a title, a value or a room script that spells
    a slot stays plain text."""
    done: set[str] = set()

    def put(m: re.Match[str]) -> str:
        slot = m.group(0)
        if slot in done:
            return slot
        done.add(slot)
        return fills[slot]

    return re.sub("|".join(re.escape(s) for s in fills), put, tpl)


# ----------------------------------------------------------------------------- details page
def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


_ISO = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}")


def _cell(v: Any) -> str:
    if v is None:
        return '<span class="dim">-</span>'
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, str) and _ISO.match(v):
        try:
            t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
            local = t.astimezone() if t.tzinfo else t
            shown = f"{local:%a} {local.day} {local:%b} {local:%H:%M}"
            return f'<time datetime="{_e(v)}">{_e(shown)}</time>'
        except (ValueError, OverflowError, OSError):  # 0001-01-01T00:00+05:00 has no local time
            return _e(v)
    if isinstance(v, dict):
        if set(v) >= {"done", "total"}:
            return f"{_e(v.get('done'))} of {_e(v.get('total'))}"
        if "href" in v and safe_href(v.get("href")):
            return f'<a href="{_e(v["href"])}">{_e(v.get("label") or v.get("text") or "link")}</a>'
        return _e(", ".join(f"{k}: {x}" for k, x in v.items() if not isinstance(x, (dict, list))))
    if isinstance(v, list):
        if all(isinstance(x, dict) and safe_href(x.get("href")) for x in v) and v:
            return ", ".join(_cell(x) for x in v)
        if all(isinstance(x, dict) and "text" in x for x in v):
            return _e("; ".join(str(x["text"]) for x in v)) if v else '<span class="dim">-</span>'
        return _e(str(len(v)))
    if isinstance(v, str) and safe_href(v) and v == safe_href(v):
        return f'<a href="{_e(v)}">{_e(v)}</a>'
    return _e(v)


# agent-status rows say why a lamp is grey or red ("why"); these words beat the plain state word
WHY_WORDS = {"quiet": "gone quiet", "stopped": "stopped responding"}


def _state_badge(st: str, why: Any = None) -> str:
    st = st if st in STATE_WORDS else "missing"
    shape = '<span class="shape" aria-hidden="true"></span>'
    word = WHY_WORDS.get(why, STATE_WORDS[st]) if isinstance(why, str) else STATE_WORDS[st]
    return f'<span class="st st-{st}">{shape}{_e(word)}</span>'


ROW_FIRST = ("name", "tool", "title", "id", "code")


def _rows_table(rows: list[Any], caption: str) -> str:
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return ""
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    first = [k for k in ROW_FIRST if k in keys][:1]
    rest = [k for k in keys if k not in first and k not in {"state", "href"}]
    cols = first + (["state"] if "state" in keys else []) + rest[:6]
    head = "".join(f'<th scope="col">{_e(k.replace("_", " ").capitalize())}</th>' for k in cols)
    body = []
    for r in rows[:500]:
        cells = []
        for i, k in enumerate(cols):
            v = r.get(k)
            if k == "state" and isinstance(v, str):
                c = _state_badge(v, r.get("why"))
            elif i == 0 and safe_href(r.get("href")):
                c = f'<a href="{_e(safe_href(r.get("href")))}">{_cell(v)}</a>'
            else:
                c = _cell(v)
            cells.append(f'<th scope="row">{c}</th>' if i == 0 else f"<td>{c}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    more = f'<p class="dim">And {len(rows) - 500} more.</p>' if len(rows) > 500 else ""
    return (
        f'<div class="tw"><table><caption>{_e(caption)}</caption><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>{more}"
    )


def _facts(room: dict[str, Any]) -> str:
    skip = {"state", "label", "error", "rows", "needs", "num"}
    items = []
    for k, v in room.items():
        if k in skip:
            continue
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            items.append(_rows_table(v, k.replace("_", " ").capitalize()))
            continue
        if isinstance(v, dict) and any(isinstance(x, list) for x in v.values()):
            sub = "".join(
                _rows_table(x, f"{k} {sk}".replace("_", " ").capitalize())
                for sk, x in v.items()
                if isinstance(x, list) and x
            )
            scal = {sk: x for sk, x in v.items() if not isinstance(x, list)}
            if scal:
                items.append(
                    f'<dl class="facts"><dt>{_e(k.replace("_", " ").capitalize())}</dt>'
                    f"<dd>{_cell(scal)}</dd></dl>"
                )
            items.append(sub)
            continue
        items.append(
            f'<dl class="facts"><dt>{_e(k.replace("_", " ").capitalize())}</dt>'
            f"<dd>{_cell(v)}</dd></dl>"
        )
    return "".join(items)


DETAILS_CSS = """
:root{color-scheme:dark;--bg:#0b0e1c;--panel:#141933;--line:#2b336a;--ink:#eef1ff;--mut:#aab2e0;
  --ok:#3ddc7a;--wait:#ffb020;--work:#36d3e6;--err:#ff5a5a;--miss:#8a8fa8;--link:#ffd36e}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--link)}a:focus-visible{outline:2px solid var(--link);outline-offset:2px}
.skip{position:absolute;left:-999px}.skip:focus{left:12px;top:12px;background:var(--panel);padding:8px}
header,main,footer{max-width:1100px;margin:0 auto;padding:0 20px}
header{padding-top:28px;padding-bottom:8px;display:flex;flex-wrap:wrap;align-items:baseline;
  gap:8px 24px}
h1{margin:0;font-size:30px;letter-spacing:.01em}
h2{margin:0;font-size:21px}
.meta{color:var(--mut)}
.needs{margin:18px 0 26px;padding:16px 18px;background:var(--panel);border:2px solid var(--line);
  border-left:6px solid var(--wait)}
.needs.none{border-left-color:var(--ok)}
.needs h2{font-size:18px;margin-bottom:6px}
.needs ul{margin:0;padding-left:20px}
.bld{margin:0 0 22px;padding:16px 18px 18px;background:var(--panel);border:2px solid var(--line);
  border-left:6px solid var(--miss)}
.bld.bs-ok,.bld.bs-working{border-left-color:var(--ok)}.bld.bs-waiting{border-left-color:var(--wait)}
.bld.bs-error{border-left-color:var(--err)}
.bh{display:flex;flex-wrap:wrap;align-items:center;gap:6px 16px;margin-bottom:8px}
.bh .kind{color:var(--mut)}
.st{display:inline-flex;align-items:center;gap:7px;font-weight:700}
.st .shape{display:inline-block;width:12px;height:12px}
.st-ok .shape{background:var(--ok)}.st-ok{color:var(--ok)}
.st-working .shape{background:var(--work);transform:rotate(45deg)}.st-working{color:var(--work)}
.st-waiting .shape{width:0;height:0;border-left:7px solid transparent;
  border-right:7px solid transparent;border-bottom:12px solid var(--wait)}
.st-waiting{color:var(--wait)}
.st-error .shape{background:var(--err);clip-path:polygon(20% 0,50% 30%,80% 0,100% 20%,70% 50%,
  100% 80%,80% 100%,50% 70%,20% 100%,0 80%,30% 50%,0 20%)}
.st-error{color:var(--err)}
.st-missing .shape{border:2px solid var(--miss)}.st-missing{color:var(--miss)}
.said{margin:0 0 10px;color:var(--mut)}
.bld ul.wait{margin:6px 0 10px;padding-left:20px}
.tw{overflow-x:auto;margin:10px 0}
table{border-collapse:collapse;min-width:60%}
caption{text-align:left;font-weight:700;padding:4px 0}
th,td{padding:6px 12px 6px 0;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
thead th{color:var(--mut);font-weight:600}
tbody th{font-weight:600}
.facts{display:grid;grid-template-columns:minmax(120px,auto) 1fr;gap:4px 14px;margin:8px 0}
.facts dt{color:var(--mut)}.facts dd{margin:0}
.dim{color:var(--mut)}
.lots{color:var(--mut);margin:0 0 30px}
footer{padding-bottom:30px;color:var(--mut)}
@media (prefers-reduced-motion:no-preference){a{transition:color .15s}}
"""


def _lighthouse_section(rows: list[dict] | None) -> str:
    """The details page's "Lighthouse today": names only (a title, a project name, a tool)."""
    if rows is None:
        return ""
    if rows:
        items = "".join(
            f"<li>{_e(r['name'])}"
            + (f' <span class="dim">({_e(r["tool"])})</span>' if r.get("tool") else "")
            + (f": {_e(r['state'])}" if r.get("state") else "") + "</li>"
            for r in rows[:200])
        more = f'<p class="dim">And {len(rows) - 200} more.</p>' if len(rows) > 200 else ""
        body = f'<ul class="wait">{items}</ul>{more}'
    else:
        body = '<p class="said">No agents today.</p>'
    return ('<section class="bld" id="lighthouse" aria-labelledby="h-lighthouse">'
            '<div class="bh"><h2 id="h-lighthouse">Lighthouse today</h2>'
            '<span class="kind">the live town: every agent seen today</span></div>'
            f"{body}</section>")


# --- L9-fix: the lighthouse's past days in words (the book's text view) ---
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")
_CLOSE_WORDS = {"work": "working at close", "wait": "waiting for you at close", "fail": "failed",
                "quiet": "gone quiet", "done": "done", "project": "new project"}


def _state_at_close(events: list) -> str:
    """One agent's state when its day closed, as the book's slip reads it (names only)."""
    st, ci = None, None
    for e in events:
        k = e[2]
        if k == "project":
            st = st or "project"
            continue
        if st in (None, "project"):
            st = "work"
        if k in ("start", "answer") or (k in ("files", "commit") and st == "quiet"):
            st = "work"
        elif k in ("wait", "fail", "quiet", "done"):
            st = k
        elif k == "ci" and len(e) > 3:
            ci = e[3]
    words = _CLOSE_WORDS.get(st or "", "")
    return words + (", CI red" if ci == 0 and st not in ("fail", "project") else "")


def _lighthouse_days_section(history: dict[str, Any] | None) -> str:
    """The details page's "Lighthouse, the last days": every past day of the handover book, newest
    first, one line per agent: its name, tool and state at close. Names only, as the book."""
    days = [d for d in (history or {}).get("days") or [] if not d.get("today")]
    if not days:
        return ""
    out = []
    for d in reversed(days):
        try:
            when = dt.date.fromisoformat(d["date"])
        except (KeyError, TypeError, ValueError):
            continue
        evs: dict[int, list] = {}
        for e in d.get("events") or []:
            evs.setdefault(e[1], []).append(e)
        rows = [(a, evs[i]) for i, a in enumerate(d.get("agents") or []) if i in evs]
        items = "".join(
            f"<li>{_e(a['name'].title() if a['name'] != 'AGENT' else 'Agent')}"
            + (f' <span class="dim">({_e(a["tool"])})</span>' if a.get("tool") else "")
            + (f": {_e(_state_at_close(ev))}" if _state_at_close(ev) else "") + "</li>"
            for a, ev in rows[:200])
        more = f'<p class="dim">And {len(rows) - 200} more.</p>' if len(rows) > 200 else ""
        body = f'<ul class="wait">{items}</ul>{more}' if rows else '<p class="said">No agents.</p>'
        head = f"{_WEEKDAYS[when.weekday()]} {when.day} {_MONTHS[when.month - 1]}"
        out.append(f"<h3>{head}</h3>{body}")
    return ('<section class="bld" id="lighthouse-days" aria-labelledby="h-lighthouse-days">'
            '<div class="bh"><h2 id="h-lighthouse-days">Lighthouse, the last days</h2>'
            f'<span class="kind">the handover book: each agent as its day closed</span></div>'
            f'{"".join(out)}</section>')
# --- end L9-fix ---


def render_details(data: dict[str, Any], lighthouse: list[dict] | None = None,
                   history: dict[str, Any] | None = None) -> str:
    """The details page. ``lighthouse``: the "Lighthouse today" lines (``[live]`` on);
    ``history``: the book's payload, for its past days in words (L9-fix)."""
    parts, rooms, plots = data["parts"], data["rooms"], data["plots"]
    title = _e(data.get("title") or "Lamptown")
    all_needs = [(p, t) for p in parts.values() for t in needs_of(rooms.get(p["id"], {}))]
    if all_needs:
        needs = (
            '<section class="needs" aria-labelledby="needs-h"><h2 id="needs-h">'
            f"{len(all_needs)} waiting for you</h2><ul>"
            + "".join(
                f'<li><a href="#b-{_e(p["id"])}">{_e(p["label"])}</a>: {_e(n["text"])}'
                + (f' (<a href="{_e(n["href"])}">open</a>)' if n.get("href") else "")
                + "</li>"
                for p, n in all_needs
            )
            + "</ul></section>"
        )
    else:
        needs = (
            '<section class="needs none" aria-labelledby="needs-h"><h2 id="needs-h">'
            "Nothing is waiting for you</h2></section>"
        )
    secs = []
    for plot in PLOTS:
        bid = plots.get(plot)
        if not bid:
            continue
        p, room = parts[bid], rooms.get(bid, {})
        st = room.get("state", "missing")
        said = p.get("result") or ""
        wait = "".join(
            f"<li>{_e(n['text'])}"
            + (f' (<a href="{_e(n["href"])}">open</a>)' if n.get("href") else "")
            + "</li>"
            for n in needs_of(room)
        )
        secs.append(
            f'<section class="bld bs-{_e(st)}" id="b-{_e(bid)}" aria-labelledby="h-{_e(bid)}">'
            f'<div class="bh"><h2 id="h-{_e(bid)}">{_e(p["label"])}</h2>{_state_badge(st)}'
            f'<span class="kind">{_e(p["type"])} room, {_e(plot)} plot'
            + (f", {_e(p['num'])} on the plate" if p.get("num") else "")
            + "</span></div>"
            + (f'<p class="said">{_e(said)}</p>' if said else "")
            + (f'<ul class="wait" aria-label="Waiting for you">{wait}</ul>' if wait else "")
            + _rows_table(room.get("rows") or [], "Rows")
            + _facts(room)
            + "</section>"
        )
    empty = [p for p in PLOTS if not plots.get(p)]
    lots = (
        (
            f'<p class="lots">Empty lots: {_e(", ".join(empty))}. '
            'Add a [[building]] with plot = "..." '
            "to your lamptown.toml to build on one.</p>"
        )
        if empty
        else ""
    )
    if not secs:
        secs.append('<p class="lots">No buildings yet: every plot is an empty lot.</p>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}: details</title>
<style>{DETAILS_CSS}</style>
</head>
<body>
<a class="skip" href="#main">Skip to the buildings</a>
<header><h1>{title}</h1><span class="meta">Built
<time datetime="{_e(data["builtIso"])}">{_e(data["built"])}</time>.
<a href="{TOWN_NAME}">Back to the town</a></span></header>
<main id="main">
{needs}
{"".join(secs)}
{_lighthouse_section(lighthouse)}
{_lighthouse_days_section(history)}
{lots}
</main>
<footer>Lamptown {_e(data.get("version", ""))}.
Everything here is read-only; times are your local time.</footer>
</body>
</html>
"""


# ----------------------------------------------------------------------------- writing
REPLACE_TRIES = 40  # x 25 ms: Windows refuses a rename while a browser has the file open


def write_atomic(out: Path, text: str) -> None:
    """Write to a unique temp file in the same folder, then swap it in (on Windows, trying
    again for up to a second while another program has the file open)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=out.name + ".", suffix=".tmp", dir=out.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(clean_text(text))  # review-fix C2: the last wall (the data is clean already)
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(tmp, out)
                break
            except PermissionError:
                if os.name != "nt" or attempt == REPLACE_TRIES - 1:
                    raise
                time.sleep(0.025)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def js_call(fn: str, obj: Any) -> str:
    """One classic-script line, ``fn(<obj>);``, safe for any value (see :func:`script_json`)."""
    return f"{fn}({script_json(obj)});\n"


def default_out_dir(config: Config) -> Path:
    return Path(config.town.out) if config.town.out else user_data_dir() / "town"


def read_rev(out_dir: str | os.PathLike[str]) -> str:
    """The ``rev`` in ``town-data.js`` (``""`` when there is none)."""
    try:
        text = (Path(out_dir) / DATA_JS).read_text(encoding="utf-8")
        head, tail = "lamptownData(", ");"
        body = text.strip()
        if body.startswith(head) and body.endswith(tail):
            rev = json.loads(body[len(head):-len(tail)]).get("rev")
            return rev if isinstance(rev, str) else ""
    except (OSError, ValueError, AttributeError):
        pass
    return ""


@dataclass
class LiveFiles:
    live: Path
    payload: dict[str, Any]
    history: Path | None = None  # None: not due this time (or not written: see warnings)
    warnings: list[str] = field(default_factory=list)  # L3-fix: a file skipped this tick


# --- L3-fix: a broken history or a held live file never fails a build ---
def _history(config: Config, now: dt.datetime, tz: dt.tzinfo | None,
             ctx: livetown.LiveContext, warnings: list[str]) -> dict[str, Any]:
    """The history payload; an event log that can't be read gives empty days and a warning."""
    try:  # review-fix C2: clean(): a status title may hold a lone surrogate
        return clean(livetown.history_payload(livetown.read_history(config, now, tz, ctx),
                                              config, now, tz))
    except Exception as e:  # the book is a nicety: the town is built without it
        warnings.append(f"live history: the event log can't be read ({type(e).__name__}: "
                        f"{e}); the lighthouse's book is empty this time"[:300])
        return livetown.history_payload([], config, now, tz)


def _write_live(path: Path, text: str, warnings: list[str]) -> bool:
    """Write a live file; one another program holds (a browser, past the retries) is skipped
    with a warning: the next tick writes it again."""
    try:
        write_atomic(path, text)
    except PermissionError as e:
        warnings.append(f"{path.name}: in use by another program, not written this time "
                        f"({e.strerror or type(e).__name__}); the next tick writes it again")
        return False
    return True
# --- end L3-fix ---


def write_live_files(
    config: Config,
    out_dir: str | os.PathLike[str] | None = None,
    *,
    rev: str | None = None,
    now: dt.datetime | None = None,
    tz: dt.tzinfo | None = None,
    history: bool | None = None,
    ctx: livetown.LiveContext | None = None,
    cache: dict | None = None,
) -> LiveFiles:
    """One Watcher tick's files, without a rebuild: ``town-history.js`` when due, then
    ``town-live.js`` (always: its ``at`` is the page's heartbeat, so call this at least every
    ~10 s even when nothing changed).

    ``rev``: the town data's (default: read from ``town-data.js``). ``history``: ``True``
    writes it now, ``False`` never, ``None`` when the file is missing or over
    :data:`HISTORY_EVERY` seconds old. ``ctx`` and ``cache`` (a dict kept between calls) save
    work on every tick: ``livetown.context(config)`` once, and records re-read only when their
    file changed."""
    out = Path(out_dir) if out_dir else default_out_dir(config)
    ctx = ctx or livetown.context(config)
    clock = now or dt.datetime.now(dt.UTC)
    hist_path = out / HISTORY_JS
    if history is None:
        try:
            history = time.time() - hist_path.stat().st_mtime >= HISTORY_EVERY
        except OSError:
            history = True
    wrote_history = None
    warnings: list[str] = []
    if history:
        hist = _history(config, clock, tz, ctx, warnings)
        if _write_live(hist_path, js_call("lamptownHistory", hist), warnings):
            wrote_history = hist_path
    if rev is None:
        rev = read_rev(out)
    records = livetown.read_records(ctx.status_dir, cache)
    payload = clean(livetown.live_payload(records, config, clock, rev=rev, ctx=ctx,
                                          at=dt.datetime.now(dt.UTC)))  # review-fix C2
    _write_live(out / LIVE_JS, js_call("lamptownLive", payload), warnings)
    return LiveFiles(live=out / LIVE_JS, payload=payload, history=wrote_history,
                     warnings=warnings)


def build(
    config: Config,
    out_dir: str | os.PathLike[str] | None = None,
    now: dt.datetime | None = None,
    timeout: float = TIMEOUT,
    log: Callable[[str], None] | None = None,
    *,
    tz: dt.tzinfo | None = None,
) -> BuildResult:
    """Collect, check and write the town page, the details page and ``town-data.js`` (plus,
    with ``[live]`` on, ``town-history.js`` and ``town-live.js``). Returns where they went.
    ``tz``: the zone the handover book's days are in (default: this computer's)."""
    fixed = now is not None
    if now is None:  # review-fix C6: the local zone, so schedules follow clock changes
        now = dt.datetime.now(LOCAL).replace(microsecond=0)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=LOCAL)
    out = Path(out_dir) if out_dir else default_out_dir(config)
    warnings: list[str] = []
    rooms = collect_rooms(config, now, timeout)
    for b in config.buildings:
        r = rooms[b.id]
        if r.get("state") == "error" and r.get("error"):
            warnings.append(f"building {b.id!r}: {r['error']}")
    live = hist = None
    ctx = None
    if config.live.enabled:
        ctx = livetown.context(config)
        live = clean(livetown.live_payload(livetown.read_records(ctx.status_dir), config, now,
                                           ctx=ctx))  # review-fix C2
        hist = _history(config, now, tz, ctx, warnings)  # L3-fix: never a failed build
    scripts = room_scripts(config, warnings)  # pr1-review: first, so the data says which
    data = town_data(config, rooms, now, fixed_clock=fixed, live=live,
                     room_js=sorted(set(re.findall(r'<script data-room="([^"]+)"', scripts))))
    town_html = render_town(data, scripts)
    today = livetown.lighthouse_today(live, hist) if live is not None and hist else None
    # L9-fix: past days
    details_html = render_details(data, today, hist if live is not None else None)
    details = out / DETAILS_NAME
    town = out / TOWN_NAME
    data_js = out / DATA_JS
    write_atomic(details, details_html)
    write_atomic(data_js, js_call("lamptownData", data))  # before town-live.js (its rev)
    write_atomic(town, town_html)
    result = BuildResult(town=town, details=details, data=data, warnings=warnings,
                         data_js=data_js)
    if live is not None and hist is not None:
        # L3-fix: the town is written already; a held live file only loses one heartbeat
        if _write_live(out / HISTORY_JS, js_call("lamptownHistory", hist), warnings):
            result.history = out / HISTORY_JS
        live = dict(live, rev=data["rev"], at=livetown.stamp(dt.datetime.now(dt.UTC)))
        if _write_live(out / LIVE_JS, js_call("lamptownLive", live), warnings):
            result.live = out / LIVE_JS
    else:
        for name in (LIVE_JS, HISTORY_JS):  # live switched off: no old heartbeat to load
            with contextlib.suppress(OSError):
                (out / name).unlink()
    if log:
        for w in warnings:
            log(w)
    return result
