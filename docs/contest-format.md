# The contest file format (`contest.json`)

The `contest` room shows a blind trial. Every entry is a sealed bottle with only a code on it. The
judges rank the entries, and the liquid in each bottle shows its score. The judges' winner is the
fullest bottle, with a blue rosette. **Your pick is a separate thing**: it is the warm lamp in the
room and the trophy in the town. A placebo (the control) can be hidden among the entries, so the
contest has something real to beat.

One JSON file holds one contest. A file can also hold several contests as
`{"contests": [ {...}, {...} ]}`.

```toml
[[building]]
id = "lab"
type = "contest"
  [[building.source]]
  kind = "contest_json"
  glob = "~/contests/*.json"      # or: file = "./contest.json" (a string or a list for both)
```

## A whole file

```json
{
  "contest": "lamptown-contest/1",
  "title": "Cafe names",
  "date": "2026-03-12",
  "updated": "2026-03-12T18:30:00+00:00",
  "entries": [
    {"code": "C01", "name": "Copper Kettle"},
    {"code": "C02", "name": "Slow Spoon", "built": "builds/C02/index.html"},
    {"code": "C03", "name": "The Usual", "control": true}
  ],
  "judges": {"expected": 3},
  "rankings": {
    "A": ["C02", "C01", "C03"],
    "B": ["C01", "C02", "C03"],
    "C": ["C02", "C03", "C01"]
  },
  "scores": {"C02": 5, "C01": 3},
  "scores_of": 6,
  "finalists": ["C02", "C01"],
  "wild": null,
  "revealed": false,
  "pick": null,
  "pick_note": ""
}
```

Every field except `entries` is optional.

| Field | Meaning |
|---|---|
| `contest` | Format marker, `"lamptown-contest/1"`. It is not checked, but it helps a later reader. |
| `title` | The contest's name. The default is the file name. |
| `date` | The day it ran, `YYYY-MM-DD`. Contests are sorted newest first by this day. The default is the day of `updated`, or else of the file's own time. |
| `updated` | When the file last changed: an ISO time **with an offset**. The default is the file's own time. |
| `entries` | A list of `{code, name, control?, built?}`. `code` has 1 to 8 letters, digits, `-` or `_` (for example `C01`). It is unique, and case does not matter. `name` stays sealed until you unblind. `control: true` marks the placebo. `built` is where a finalist was built: a path relative to the file, or an `https://` link. |
| `judges.expected` | How many judges should rank the entries. While fewer rankings are in, the room is `working` and bubbles rise in the town. |
| `judges.received` | Optional. Overrides the count of `rankings`. |
| `rankings` | Round 1. Each judge's name maps to the codes, best first. A judge does not have to rank every entry; an unranked entry counts as last. |
| `scores` | Optional. A final score per code, for example the wins in a round of head-to-heads. When it is present, it sets the liquid: the top score is a full bottle, and an entry with no score is empty (it went out earlier). Without it, the liquid is the round-1 mean rank turned round, so first place everywhere is full. |
| `scores_of` | Optional. What the scores are out of (for example 6 head-to-heads). It appears in the tooltips. |
| `finalists` | Optional. The codes you choose between. The default is the entries that were `built`, else the top two plus the wild card (when there are `scores`). |
| `wild` | Optional. A wild card: an entry kept from round 1. It wears a gold star. |
| `revealed` | `true` once the key is unblinded, so names may show. A pick reveals the key too. |
| `pick` | Your pick: a code (or an exact entry name). `"none"` records a decision that picks none of them. `null` means no pick yet. |
| `pick_note` | Optional. One line about the pick, shown on hover. |

## How the room reads it

| State | When | What you see |
|---|---|---|
| `missing` | No entries yet | Empty, dusty shelves, and the lamp is off |
| `working` | Judges are still ranking (fewer rankings than `judges.expected`, or none and no `scores`) | Sealed vials, judges' lamps and drips, "1 OF 3 DONE" |
| `waiting`, step "unblind" | Scored, not revealed, no pick | An amber sealed envelope, "UNBLIND?". Opening it in the room is remembered on that screen only, and nothing is written |
| `waiting`, step "pick" | Revealed, no pick | Amber "PICK ONE" tags on the finalists |
| `ok` | `pick` is set | The pick's tag turns round to its name under the warm lamp. For `"none"`, the envelope holds a "NONE" card |
| `error` | The file can't be read, or a ranking, score, finalist, wild card or pick names a code that is not an entry | A red notice says which. Nothing is guessed |

The judges' winner (the rosette) and your pick (the lamp) are two different things. They only
match when you pick the judges' first.

The room's state is the newest contest's state. It is raised to `waiting` when an older contest
still waits for a pick. Older contests are crates by the wall; click one to put it on the bench.

## What the town and the details page see

The room data's top level keeps the shape of the room schema (`entries`, `judges`, `winner`, `pick`). Each
entry carries a `name` only once it is unsealed. While the key is sealed, no entry is unsealed.
At the pick step, only the finalists (the ones you pick from) are. Once a pick is recorded, all of
them are. `winner` is a code while sealed and a name after that. The room's own `contests` list
carries every name, but the room shows them only after you unblind.
