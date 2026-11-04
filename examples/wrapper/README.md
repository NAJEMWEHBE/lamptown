# Wrap any command

No hooks needed: put `lamptown status run --id <id> --` in front of the command.

```sh
# an interactive agent without hooks (Aider, a local model runner, a bot)
lamptown status run --id aider-paper-kite --title "Aider: paper-kite" --agent aider -- aider

# a scheduled job (crontab line): the jobs room shows it, next to its log
0 2 * * * lamptown status run --id nightly-backup --title "Nightly backup" --kind job -- /usr/local/bin/backup.sh

# a long build that may run for hours: refresh well inside the stale window
lamptown status run --id render-trailer --stale-after 7200 -- make render
```

Windows Task Scheduler: program `lamptown`, arguments
`status run --id nightly-backup --kind job -- powershell -File backup.ps1`.

While the command runs the file says `working` (rewritten every minute, so a killed wrapper
turns red after `--stale-after`). Exit code 0 gives `ok`; anything else gives `error` with
the exit code and the last lines of stderr. Output and the exit code pass through unchanged.

From your own scripts, set fields directly:

```sh
lamptown status set paper-kite --title "Paper Kite" --state working -m "Running tests" --progress 3/8
lamptown status set paper-kite --need "Approve the plan" --need-href https://example.com/pr/12
lamptown status set paper-kite --state ok --clear-needs -m "Merged"
```
