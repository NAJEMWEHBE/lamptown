# Collectors

A **collector** reads one data source and returns a small JSON-safe dict for one building's room.
Every `[[building.source]]` table in `lamptown.toml` runs one collector, picked by its `kind`.
The built-in kinds and their keys are in [config.md](config.md#built-in-source-kinds); this page is
for writing your own.

## The signature

```python
from datetime import datetime

def collect(paths: dict, now: datetime) -> dict:
    ...
```

- `paths` is **this source's TOML table, already resolved** by the config loader:
  - every string under a path-like key (`path`, `dir`, `file`, `glob`, `paths`, `dirs`, `files`,
    `globs`, or any key ending in `_path`, `_dir`, `_file`, `_glob`) has `~` expanded and, when
    relative, is made absolute against the folder that holds `lamptown.toml`. Lists of strings
    under those keys are resolved item by item. Values stay `str` (use `pathlib.Path(...)` yourself).
  - `paths["kind"]` and, when given, `paths["name"]` are kept as written.
  - Three reserved keys are added by Lamptown (user keys may not start with `_`):
    - `paths["_building"]`: the building id (`"jobs"`, `"projects"`, ...);
    - `paths["_room"]`: the building's room type (`"jobs"`, `"vault"`, `"shelf"`, `"ledger"`,
      `"contest"`, `"harbour"`, or a custom type). A collector that serves several rooms
      (for example `status_dir`, which feeds both `jobs` and `harbour`) uses this to choose its
      row shape;
    - `paths["_config_dir"]`: the absolute folder of the config file, as a string.
- `now` is a **timezone-aware** `datetime` (local offset) taken once per build, so every
  collector in one build sees the same instant. Use it instead of calling `datetime.now()`.
- The return value is a `dict`. Anything else counts as a crash.

## The rules

1. **Read-only.** Never write, move or delete anything in a source.
2. **Standard library only** for built-in collectors.
3. **`state`** is one of `ok | waiting | working | error | missing`:

   | state | lamp | meaning |
   |---|---|---|
   | `ok` | green | running fine |
   | `waiting` | amber | needs you: a person must do something |
   | `working` | green in town, cyan in rooms | running right now; the building moves |
   | `error` | red | something is wrong: it failed, or can't be read |
   | `missing` | grey | can't see it: the source isn't there (yet) |

   Worst first, for merging: `error > waiting > working > missing > ok`.
4. **JSON-safe values only**: `str`, `int`, `float`, `bool`, `None`, `list`, `dict` (string keys).
   As a convenience, `datetime` / `date` values are turned into ISO 8601 strings, and `Path`
   values into strings, before the check. `NaN`/`Infinity`, sets, bytes and other objects fail.
5. **Absolute times only**: ISO 8601 strings with an offset (`2026-11-04T10:12:00+00:00` or
   `...Z`). Never write "in 3 h" or "2 d ago": the page writes relative text itself, so the
   clock alone never changes the data (and never triggers a desktop-app reload).
6. **Needs**: an optional `needs: [{"text": "...", "href": "..."}]` list feeds the town's
   "N NEEDS YOU" sign and the room. `href` is optional; only `https:`, `http:` and `file:`
   links are kept, anything else is dropped by the builder.
7. **Crash isolation**: an exception, a run longer than **10 s**, a non-dict, or a value JSON
   can't hold becomes `{"state": "error", "error": "<short reason>"}` for that source only.
   The rest of the town still builds.
8. **Schema check**: the merged room data is validated against
   `lamptown/schemas/rooms/<type>.schema.json`. Data that fails becomes the room's error state
   with the first schema problem as its reason. Custom room types without a schema skip this.

## What a collector returns

Common keys (all optional except `state`):

| key | type | use |
|---|---|---|
| `state` | str | see rule 3 (required) |
| `rows` | list of dict | one row per job / project / tool / item, each with its own `state` |
| `needs` | list of `{text, href?}` | see rule 6 |
| `label` | str | a few words for the lamp tooltip ("2 jobs late") |
| `error` | str | short reason, when `state` is `error` |
| `updated` | ISO str | newest change the source knows of |

Room types add their own keys (for example `vault` has `folders`, `inbox`, `last_review`).
The row fields each room expects are listed in its schema under `src/lamptown/schemas/rooms/`.

## Several sources in one building

When a building has more than one `[[building.source]]`, their results are merged:

- `rows` and `needs` lists are concatenated in source order;
- `state` is the worst state of all sources (rule 3 order);
- `label` / `error` come from the worst source;
- every other key takes the first value any source gave.

## Kinds and where they come from

A source with `collector = "./mine.py"` always runs that file. Otherwise `kind` is looked up in
this order:

1. **Built-in kinds** shipped in Lamptown: `status_dir`, `log_glob`, `markdown_vault`, `projects`,
   `skill_dirs`, `staging_dir`, `scan_glob`, `contest_json` and `static`. Their
   keys are listed in [config.md](config.md#built-in-source-kinds).
2. **Entry points** in the group `lamptown.collectors` from any installed package:

   ```toml
   # the third-party package's pyproject.toml
   [project.entry-points."lamptown.collectors"]
   my_tracker = "my_package.lamptown_collector:collect"
   ```

   then `kind = "my_tracker"` in `lamptown.toml`.

A built-in kind wins over an entry point of the same name, so pick a name that says whose it is
(`acme_tracker`, not `tracker`).

**A local file** (`collector = "./mine.py"`, relative to `lamptown.toml`, or `~/...`) must define
`collect(paths, now)`. `kind` may then be any word you like; it is only a label. The file is loaded
fresh on every build, so an edit shows on the next one.

## A minimal collector

```python
# mine.py
from datetime import datetime
from pathlib import Path


def collect(paths: dict, now: datetime) -> dict:
    flag = Path(paths["file"])
    if not flag.exists():
        return {"state": "missing", "label": "no flag file yet"}
    text = flag.read_text(encoding="utf-8").strip()
    if text == "help":
        return {"state": "waiting", "needs": [{"text": "The robot asks for help"}]}
    return {"state": "ok", "label": text[:40]}
```

```toml
[[building]]
id = "robot"
type = "harbour"
label = "Robot"
  [[building.source]]
  kind = "flag"
  collector = "./mine.py"
  file = "~/robot/flag.txt"
```

## Testing a collector

Call it directly with a fixed `now` and a temp folder:

```python
from datetime import datetime, timezone
from mine import collect

def test_missing(tmp_path):
    out = collect({"file": str(tmp_path / "nope.txt")}, datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert out["state"] == "missing"
```
