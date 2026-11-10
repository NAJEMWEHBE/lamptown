"""Contest room collector: the blinded trial (kind ``contest_json``).

One contest is a trial: identical coded bottles, one per sealed entry. The liquid in a bottle is
its score; the judges' winner is the fullest bottle; your pick is a separate thing (the warm lamp
in the room, the trophy in the town). The newest contest is on the bench; older ones are crates.

```toml
[[building]]
id = "lab"
type = "contest"
  [[building.source]]
  kind = "contest_json"            # the documented format: docs/contest-format.md
  glob = "~/contests/*.json"       # or file = "./contest.json" (str or list for both)
```

States: ``missing`` no entries yet; ``working`` judges still ranking; ``error`` a file that can't
be read, or a ranking, score or pick naming a code that is not an entry; ``waiting`` scored and no
pick yet (``step`` ``unblind`` while the key is sealed, ``pick`` once it is revealed); ``ok`` your
pick is recorded (an entry, or a decision that picks none of them). The room's state is the newest
contest's, raised to ``waiting`` when an older one still waits for its pick.

The liquid (one rule, here): the ``scores`` scaled to the top score, entries
without a score are empty; with no scores, the round-1 mean rank turned round (first everywhere
= full) and scaled so the best entry is full.

Top-level ``entries`` (the town and the details page) carry a name only once it is unsealed: while
the key is sealed none is; at the pick step only the finalists (the ones you pick from) are.
The room's own ``contests`` list carries the names: the room shows them only after you unblind.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from pathlib import Path
from typing import Any

from .ledger import _int, _iso, _mtime, aware, scan_files
from .shelf import _is_dir, _is_file, _paths, _read, _uri, font_safe

FORMAT = "lamptown-contest/1"
MAX_CONTESTS = 40  # contests sent (the count is always the true one)
MAX_ENTRIES = 120  # entries sent per contest
MAX_TOP = 200  # top-level entries
MAX_BYTES = 2_000_000  # a bigger contest file is not read
MAX_FILES = 500  # contest files read
CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,7}$")
DAY_RE = re.compile(r"^\d{4}-\d\d-\d\d$")
ORDER = ("error", "waiting", "working", "ok")


class Unreadable(Exception):
    """A contest file that can't be read: the contest shows as an error, nothing is guessed."""

    def __init__(self, say: str, why: str) -> None:
        super().__init__(why)
        self.say = say
        self.why = why


# ----------------------------------------------------------------------------- small helpers
def _txt(v: Any, n: int = 120) -> str:
    return font_safe(v)[:n] if isinstance(v, str) else ""


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _day(v: Any) -> str | None:
    """A real calendar day 'YYYY-MM-DD', else None."""
    if not isinstance(v, str) or not DAY_RE.match(v.strip()):
        return None
    try:
        return dt.date.fromisoformat(v.strip()).isoformat()
    except ValueError:
        return None


# ----------------------------------------------------------------------------- the model
def _base(spec: dict) -> dict[str, Any]:
    return {
        "name": spec["name"],
        "title": spec["title"],
        "date": spec["date"],
        "state": "ok",
        "say": "",
        "count": 0,
        "step": None,
        "error": "",
        "why": "",
        "rounds": 0,
        "measure": "",
        "full": None,
        "of": None,
        "more": 0,
        "judges": dict(spec["judges"]),
        "revealed": False,
        "pick": None,
        "pickWords": "",
        "finalistsBuilt": False,
        "stamp": _iso(spec["stamp"]),
        "href": spec["href"],
        "hrefs": dict(spec["hrefs"]),
        "cards": [],
    }


def _card(e: dict, mean: float | None, fill: float | None) -> dict[str, Any]:
    return {
        "code": e["code"],
        "name": e["name"],
        "control": bool(e.get("control")),
        "mean": round(mean, 2) if mean is not None else None,
        "fill": round(max(0.0, min(1.0, fill)), 4) if fill is not None else None,
        "place": None,
        "ranks": [],
        "score": None,
        "inR2": False,
        "finalist": False,
        "wild": False,
        "built": None,
        "pick": False,
        "first": False,
    }


def model(spec: dict) -> dict[str, Any]:
    """One contest from what a reader found: the bench logic, one place for every rule."""
    c = _base(spec)
    if spec.get("error"):
        return {**c, "state": "error", "say": spec["say"], "error": spec["error"]}
    entries: list[dict] = spec["entries"]
    j = c["judges"]
    rankings: list[tuple[str, list[str]]] = spec["rankings"]
    if not entries:
        if rankings or j["r1of"]:
            return {
                **c,
                "state": "error",
                "say": "NO ENTRIES",
                "error": "Judges are set up but there are no entries.",
            }
        return {**c, "state": "missing", "say": "NO ENTRIES YET"}
    c["count"] = len(entries)
    by = {e["code"]: e for e in entries}
    n = len(entries)

    ranks: dict[str, list[int]] = {k: [] for k in by}
    for judge, order in rankings:
        if not order:
            return {
                **c,
                "state": "error",
                "say": "CAN'T READ A RANKING",
                "error": f"No ranking found for {judge}.",
            }
        for i, code in enumerate(order):
            if code in ranks:
                ranks[code].append(i + 1)
            else:
                return {
                    **c,
                    "state": "error",
                    "say": "RANKING DOESN'T MATCH",
                    "error": f"{judge} ranks {code}, which is not an entry.",
                }
    mean = {k: (sum(r) / len(r) if r else float(n)) for k, r in ranks.items()}
    scores: dict[str, float] | None = spec.get("scores")
    sealed_cards = [_card(e, None, None) for e in entries[:MAX_ENTRIES]]
    more = max(0, n - MAX_ENTRIES)
    if j["r1"] < j["r1of"] or (not rankings and scores is None):
        return {
            **c,
            "state": "working",
            "why": "r1",
            "say": "JUDGES ARE STILL RANKING" if j["r1of"] else "WAITING FOR THE JUDGES",
            "cards": sealed_cards,
            "more": more,
        }
    if scores is not None:
        stray = next((k for k in scores if k not in by), None)
        if stray:
            return {
                **c,
                "state": "error",
                "say": "SCORES DON'T MATCH",
                "error": f"The scores name {stray}, which is not an entry.",
            }

    # the liquid
    if scores:
        top = max(scores.values()) or 1.0
        fill = {k: (scores[k] / top if k in scores else None) for k in by}
        c.update(
            rounds=2 if rankings else 1,
            measure=spec.get("measure") or "score",
            full=_tidy(top),
            of=spec.get("of"),
        )
    else:
        sc = {k: ((n - m) / (n - 1) if n > 1 else 1.0) for k, m in mean.items()}
        top = max(sc.values()) or 1.0
        fill = {k: max(0.0, s / top) for k, s in sc.items()}
        c.update(rounds=1, measure="rank", full=None)
    sv = scores or {}
    order = sorted(by, key=lambda k: (-sv.get(k, -1e18), mean[k], k))
    place = {k: i + 1 for i, k in enumerate(order)}
    key = {k: (sv.get(k, -1e18), -mean[k]) for k in by}
    firsts = [k for k in order if key[k] == key[order[0]]]

    wild = spec.get("wild") if spec.get("wild") in by else None
    builds = {e["code"]: e["built"] for e in entries if e.get("built")}
    finals = [f for f in spec.get("finalists") or [] if f in by]
    if not finals and builds:
        finals = [k for k in order if k in builds]
    if not finals:
        finals = order[:2] + ([wild] if wild and wild not in order[:2] and scores else [])
    c["finalistsBuilt"] = bool(finals) and all(f in builds for f in finals)

    revealed = bool(spec.get("revealed"))
    pick = spec.get("pick")
    if pick:
        code = pick.get("code")
        if code and code not in by:
            return {
                **c,
                "state": "error",
                "say": "PICK DOESN'T MATCH",
                "error": f"The pick names {code}, which is not an entry.",
            }
        pick = {
            "code": code,
            "name": by[code]["name"] if code else "",
            "text": _txt(pick.get("text"), 200) if not code else "",
            "note": _txt(pick.get("note"), 200),
            "href": spec.get("pick_href") or c["hrefs"].get("result", ""),
        }
        revealed = True
        if code and code not in finals:
            finals.append(code)
    c["revealed"], c["pick"] = revealed, pick

    cards = []
    for e in sorted(entries, key=lambda e: place[e["code"]])[:MAX_ENTRIES]:
        k = e["code"]
        card = _card(e, mean[k], fill[k])
        card.update(
            place=place[k],
            ranks=ranks[k],
            score=_tidy(sv[k]) if k in sv else None,
            inR2=bool(scores) and k in sv,
            finalist=k in finals,
            wild=k == wild,
            built=builds.get(k),
            pick=bool(pick and pick["code"] == k),
            first=k in firsts,
        )
        cards.append(card)
    c["cards"], c["more"] = cards, more
    c["first"] = firsts

    if pick:
        c.update(
            state="ok",
            say=("PICKED: " + pick["name"].upper()) if pick["code"] else "DECIDED: NONE PICKED",
            pickWords=pick["name"] or "none picked",
        )
    elif not revealed:
        c.update(state="waiting", step="unblind", say="RESULTS ARE IN: UNBLIND?")
    else:
        c.update(state="waiting", step="pick", say="PICK ONE")
    return c


def _tidy(v: float | None) -> int | float | None:
    if v is None:
        return None
    return int(v) if float(v).is_integer() else round(v, 2)


# ----------------------------------------------------------------------------- contest.json
def _spec_base(name: str, title: str, stamp: dt.datetime, href: str) -> dict[str, Any]:
    return {
        "name": name,
        "title": (title or name)[:120],  # the file stem has no length limit of its own
        "date": stamp.date().isoformat(),
        "stamp": stamp,
        "href": href,
        "hrefs": {"key": href, "cards": href, "result": href, "tally": href},
        "judges": {"r1": 0, "r1of": 0, "r2": 0, "r2of": 0},
        "entries": [],
        "rankings": [],
    }


def _code(v: Any) -> str | None:
    return v.strip().upper() if isinstance(v, str) and CODE_RE.match(v.strip()) else None


def _built(v: Any, base: Path) -> dict[str, str] | None:
    if not isinstance(v, str) or not v.strip():
        return None
    v = v.strip()
    if re.match(r"^https?://", v, re.I):
        return {"href": v[:2048], "shot": ""}
    p = Path(v).expanduser()
    p = p if p.is_absolute() else base / p
    if not (_is_file(p) or _is_dir(p)):
        return None
    shot = p if _is_file(p) and p.suffix.lower() == ".png" else None
    return {"href": _uri(p), "shot": _uri(shot) if shot else ""}


def read_json_contest(j: Any, name: str, base: Path, stamp: dt.datetime, href: str) -> dict:
    """One contest object from a contest.json file -> a spec for ``model``."""
    if not isinstance(j, dict):
        raise Unreadable("CAN'T READ THE CONTEST", "A contest must be a JSON object.")
    upd = aware(j.get("updated"))
    stamp = upd.astimezone(dt.UTC) if upd else stamp
    spec = _spec_base(name, _txt(j.get("title")), stamp, href)
    spec["date"] = _day(j.get("date")) or spec["date"]
    raw = j.get("entries", [])
    if not isinstance(raw, list):
        raise Unreadable("CAN'T READ THE ENTRIES", "entries must be a list.")
    entries, seen = [], set()
    for i, e in enumerate(raw):
        code = _code(e.get("code")) if isinstance(e, dict) else None
        if not code:
            raise Unreadable("CAN'T READ AN ENTRY", f"Entry {i + 1} has no usable code.")
        if code in seen:
            raise Unreadable("CAN'T READ AN ENTRY", f"The code {code} is used twice.")
        seen.add(code)
        entries.append(
            {
                "code": code,
                "name": _txt(e.get("name")) or code,
                "control": e.get("control") is True,
                "built": _built(e.get("built"), base),
            }
        )
    spec["entries"] = entries
    by_name = {e["name"].lower(): e["code"] for e in entries}

    def code_of(v: Any) -> str | None:
        """An entry's code, else the code of the entry with that exact name ("Mustard" is a
        name, not a code, when no entry has the code MUSTARD)."""
        c = _code(v)
        if c in seen:
            return c
        named = by_name.get(v.strip().lower()) if isinstance(v, str) else None
        return named or c

    rk = j.get("rankings") or {}
    if not isinstance(rk, dict):
        raise Unreadable("CAN'T READ THE RANKINGS", "rankings must map each judge to a list.")
    rankings = []
    for judge, order in list(rk.items())[:200]:
        codes: list[str] = []
        for x in order if isinstance(order, list) else []:
            c = _code(x) or (str(x)[:12] if x is not None else "?")
            if c not in codes:
                codes.append(c)
        rankings.append((_txt(judge, 40) or "a judge", codes))
    spec["rankings"] = rankings
    js = j.get("judges") if isinstance(j.get("judges"), dict) else {}
    exp, rec = _int(js.get("expected")), _int(js.get("received"))
    received = rec if rec is not None and rec >= 0 else len(rankings)
    spec["judges"]["r1"] = received
    spec["judges"]["r1of"] = max(received, exp if exp is not None and exp >= 0 else 0)

    sc = j.get("scores")
    if sc is not None:
        if not isinstance(sc, dict):
            raise Unreadable("CAN'T READ THE SCORES", "scores must map codes to numbers.")
        scores = {}
        for k, v in sc.items():
            num = _num(v)
            code = _code(k) or str(k)[:12]
            if num is None:
                raise Unreadable("CAN'T READ THE SCORES", f"The score of {code} is not a number.")
            scores[code] = num
        spec["scores"] = scores
        of = _int(j.get("scores_of"))
        spec["of"] = of if of is not None and of > 0 else None
    spec["finalists"] = [code_of(x) or str(x)[:12] for x in (j.get("finalists") or [])][:12]
    bad = next((f for f in spec["finalists"] if f not in seen), None)
    if bad:
        raise Unreadable("FINALISTS DON'T MATCH", f"The finalist {bad} is not an entry.")
    if j.get("wild") is not None:
        spec["wild"] = code_of(j.get("wild"))
        if spec["wild"] not in seen:
            raise Unreadable("WILD CARD DOESN'T MATCH", "The wild card is not an entry.")
    spec["revealed"] = j.get("revealed") is True
    p = j.get("pick")
    if isinstance(p, str) and p.strip():
        if p.strip().lower() == "none":
            spec["pick"] = {"code": None, "text": j.get("pick_note") or "None of these."}
        else:
            spec["pick"] = {"code": code_of(p) or p.strip()[:12], "note": j.get("pick_note")}
    elif p is not None:
        raise Unreadable("CAN'T READ THE PICK", 'pick must be a code, "none" or null.')
    return spec


def _json_file(p: Path, now: dt.datetime) -> list[dict]:
    stamp = _mtime(p) or now
    href = _uri(p)
    try:
        if p.stat().st_size > MAX_BYTES:
            raise Unreadable("FILE TOO BIG", f"{p.name} is bigger than 2 MB.")
        text = _read(p)
        if text is None:
            raise Unreadable("CAN'T READ THE FILE", f"{p.name} can't be opened.")
        try:
            j = json.loads(text)
        except ValueError:
            raise Unreadable("CAN'T READ THE FILE", f"{p.name} is not valid JSON.") from None
        many = j.get("contests") if isinstance(j, dict) and "entries" not in j else None
        if isinstance(many, list):
            return [
                _one(x, f"{p.stem}-{i + 1}", p, stamp, href)
                for i, x in enumerate(many[:MAX_FILES])
            ]
        return [_one(j, p.stem, p, stamp, href)]
    except Unreadable as e:
        return [model({**_spec_base(p.stem, p.stem, stamp, href), "error": e.why, "say": e.say})]
    except OSError as e:
        return [
            model(
                {
                    **_spec_base(p.stem, p.stem, stamp, href),
                    "error": str(e)[:200],
                    "say": "CAN'T READ THE FILE",
                }
            )
        ]


def _one(j: Any, name: str, p: Path, stamp: dt.datetime, href: str) -> dict:
    try:
        return model(read_json_contest(j, name, p.parent, stamp, href))
    except Unreadable as e:
        title = _txt(j.get("title")) if isinstance(j, dict) else ""
        return model({**_spec_base(name, title, stamp, href), "error": e.why, "say": e.say})


# ----------------------------------------------------------------------------- the room data
def _fit(base: str, tail: str, n: int = 120) -> str:
    """``base + tail`` in at most ``n`` characters (the schema's label length): the base is cut
    so the tail, the part that says what to do, stays whole. A tail too long for that (a long
    pick name) keeps the base's first 24 characters and is cut at the end."""
    if len(base) + len(tail) <= n:
        return base + tail
    keep = max(n - 3 - len(tail), 24)
    out = (base[:keep].rstrip() + "..." if keep < len(base) else base) + tail
    return out if len(out) <= n else out[: n - 3].rstrip() + "..."


def _label(c: dict | None) -> str:
    if c is None:
        return "No contest yet"
    st = c["state"]
    if st == "ok":
        tail = f": picked {c['pick']['name']}" if c["pick"]["code"] else ": decided, none picked"
    elif st == "waiting":
        tail = ": results are in, unblind?" if c["step"] == "unblind" else ": pick one"
    elif st == "working":
        tail = f": {c['say'].lower()}"
    elif st == "error":
        tail = ": can't read it"
    else:
        tail = ": no entries yet"
    return _fit(c["title"], tail)


def finish(contests: list[dict], now: dt.datetime) -> dict[str, Any]:
    """The room data: the newest contest on the bench, the rest as crates."""
    contests.sort(key=lambda c: (c["date"], c["stamp"] or "", c["name"]), reverse=True)
    if not contests:
        return {
            "state": "missing",
            "label": _label(None),
            "count": 0,
            "entries": [],
            "judges": {"expected": 0, "received": 0},
            "winner": None,
            "pick": None,
            "contests": [],
        }
    top = contests[0]
    state = top["state"]
    if state == "ok" and any(c["state"] == "waiting" for c in contests[1:]):
        state = "waiting"  # an older contest still waits for your pick
    unsealed_all = top["state"] == "ok" and top["revealed"]
    pick_step = top["state"] == "waiting" and top["step"] == "pick"
    entries = []
    for card in top["cards"][:MAX_TOP]:
        sealed = not (unsealed_all or (pick_step and card["finalist"]))
        e: dict[str, Any] = {"code": card["code"], "sealed": sealed}
        if not sealed:
            e["name"] = card["name"]
        if card["fill"] is not None:
            e["fill"] = card["fill"]
        entries.append(e)
    j = top["judges"]
    first = next((x for x in top["cards"] if x["first"]), None)
    winner = None
    if first and top["state"] in ("waiting", "ok"):
        winner = first["name"] if top["revealed"] else first["code"]
    pick = top["pick"]
    needs = []
    for c in contests:
        href = c["hrefs"].get("result") or c["href"]
        if c["state"] == "waiting":
            text = (
                f"{c['title']}: results are in, unblind them"
                if c["step"] == "unblind"
                else f"{c['title']}: pick one of the finalists"
            )
        elif c["state"] == "error":
            text = f"Fix {c['title']}: {c['error'] or 'a file cannot be read'}"
        else:
            continue
        needs.append({"text": text[:300], **({"href": href} if href else {})})
    out: dict[str, Any] = {
        "state": state,
        "label": _label(top),
        "title": top["title"],
        "num": str(top["count"]),
        "updated": top["stamp"],
        "entries": entries,
        "judges": {"expected": j["r1of"], "received": j["r1"]},
        "winner": winner,
        "pick": (pick["name"] if pick["code"] else "None picked") if pick else None,
        "count": len(contests),
        "contests": contests[:MAX_CONTESTS],
    }
    if needs:
        out["needs"] = needs[:20]
    return out


def collect(paths: dict, now: dt.datetime) -> dict[str, Any]:
    """``contest_json``: contest files in the documented format (docs/contest-format.md)."""
    files: list[Path] = []
    taken: set[str] = set()
    named = [p for p in _paths(paths.get("file")) + _paths(paths.get("files")) if _is_file(p)]
    for p in named + scan_files(_paths(paths.get("glob")) + _paths(paths.get("globs"))):
        if str(p).lower() not in taken:
            taken.add(str(p).lower())
            files.append(p)
    contests = [c for p in files[:MAX_FILES] for c in _json_file(p, now)]
    return finish(contests, now)

