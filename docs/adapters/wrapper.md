# Any command: the wrapper

For tools without hooks (Aider, a local model runner, a bot) and for scheduled jobs, put
`lamptown status run --id <id> --` in front of the command:

```sh
lamptown status run --id aider-paper-kite --title "Aider: paper-kite" --agent aider -- aider
lamptown status run --id nightly-backup --title "Nightly backup" --kind job -- ./backup.sh
```

| Moment | Lamp | Message |
|---|---|---|
| start | working | Running `<command>` |
| while it runs | working | the file is rewritten every `stale_after / 3` seconds, and at least once a minute |
| exit 0 | ok | Finished in `<time>` |
| any other exit | error | Exit `<code>` after `<time>`: last 5 stderr lines |
| Ctrl+C | error (or ok on exit 0) | Interrupted after `<time>` |
| can't start | error | Could not start `<command>` (exit code 127) |

Output passes through unchanged and the wrapper exits with the command's exit code, so it
is safe in scripts and cron. A command killed by signal N exits `128 + N`, as in a shell.
The lamp never gets in the way: if the status folder can't be written (full disk, no
permission, a file where the folder should be), the wrapper says so once on stderr and runs
the command anyway, with the same exit code. Only a usage mistake (no command, a bad `--id`)
stops it before it starts (exit 2).

Ctrl+C reaches the command itself, which shares the console; the wrapper waits for it to
finish and records how it ended. A second Ctrl+C, or a command still running 10 seconds after
the first, is ended by the wrapper (exit 130).

The file also keeps the folder the command ran in as `x-folder`, so a `status_dir` source's
`private_dirs` can hide it; the command line itself is in the message ("Running ..."), so
don't put secrets in its arguments. If the wrapper itself is killed, the heartbeat stops and the
lamp turns red after `stale_after` seconds (default 1800; set `--stale-after` for jobs
that print nothing for longer, `0` to turn the rule off).

Options: `--id` (required), `--title`, `--kind task|job|project`, `--agent`,
`--stale-after SECONDS`, `--dir DIR`.

Scheduling examples are in [`examples/wrapper/README.md`](../../examples/wrapper/README.md).
To report progress or needs from inside a script, call `lamptown status set` (see
[../status.md](../status.md)) or use `agent_status()` in Python.
