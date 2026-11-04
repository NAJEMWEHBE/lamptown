# Lamptown docs

Lamptown turns local files into a pixel-art town: config, then collectors, then room data, then one
`town.html`. These pages follow the same path. New here? Start with the
[README](../README.md) and `lamptown demo`.

## Set up your town

- [Configuration](config.md): `lamptown.toml`, every key, where the file lives, paths, schedules,
  and every built-in source kind.
- [Rooms](rooms.md): the six built-in rooms, what data each reads, what each state looks like, and
  how far each one scales; plus the lighthouse's handover book.
- [The live town](live.md): your agents walking the town as they work, the lighthouse, the
  berths and the 7-day book; `lamptown watch`, what is read and kept, and troubleshooting.

## Connect your agents

- [Agent status files](status.md): the `agent-status/1` files that light the lamps, and the
  `lamptown status set | run | hook | list | clear` commands.
- [The live town](live.md#what-it-reads-and-what-it-keeps): readers that follow Claude Code,
  Codex and git without hooks.
- The format itself: [spec/agent-status-1.md](../spec/agent-status-1.md) and its
  [JSON Schema](../spec/agent-status-1.schema.json). Any tool may read or write it.
- Adapters, one per agent:
  [Claude Code](adapters/claude-code.md), [Codex CLI](adapters/codex.md),
  [Gemini CLI](adapters/gemini-cli.md), [Cursor](adapters/cursor.md), and the
  [wrapper](adapters/wrapper.md) for anything else.
- Ready-to-paste configs: [examples/](../examples/).

## Extend it

- [Collectors](collectors.md): write a collector for any data source. The contract, the rules,
  and how to ship one as a package.
- [Rooms: bring your own room](rooms.md#bring-your-own-room): the `registerRoom` interface and a
  whole example room.
- [The room kit `K`](kit-k.md): the shared pixel toolkit every room draws with.
- [The contest file format](contest-format.md): `contest.json`, for the idea contest room.

## Words used everywhere

| Word | Means |
|---|---|
| **building** | One `[[building]]` in the config, standing on one of six plots. |
| **plot** | A fixed spot on the v1 map: `tower`, `library`, `gate`, `lab`, `workshop`, `boathouse`. |
| **room type** | What a building opens into: `jobs`, `vault`, `ledger`, `contest`, `shelf`, `harbour`, or your own. |
| **source** | One `[[building.source]]`: one collector run against one place. |
| **collector** | A read-only function that turns files into room data. |
| **room data** | The JSON a building's sources produce, checked against its room type's schema. |
| **state** | `ok`, `waiting`, `working`, `error` or `missing`. Worst first: `error > waiting > working > missing > ok`. |
| **lamp** | A building's state in the town: green (`ok`, and `working` with the building moving), amber, red, grey. Inside rooms, `working` is cyan. |
| **need** | Something a person must do: `{text, href}`. Needs feed the "N NEEDS YOU" sign. |
| **Lamplighter** | One live agent in the live town: a figure with a lantern at the building it works in. |
| **Watcher** | What keeps the live town live: it runs the readers, writes the status files and the event log, and writes `town-live.js` (`lamptown watch` or the app). |
| **berth** | A project's boat at the harbour's wharf in the live town. |

## Project

- [Changelog](../CHANGELOG.md): what changed in each version (0.2 changed Codex ids).
- [Contributing](../CONTRIBUTING.md), [Security](../SECURITY.md),
  [Code of Conduct](../CODE_OF_CONDUCT.md), [Licence](../LICENSE) (MIT).
