# afa-mods

Three Claude Code mods in one plugin (Claude Code 2.1.296, early-access function hooks).
No network calls. The only writes go to `~/.claude/state/mods/`.

## What they do

**Hub band.** In a session started in `~/repos`, one line above the prompt:
`/prep not run` until `/prep` has run in the session, then open lanes (rows of
`~/.claude/state/lanes/index.tsv` from the last 3 days whose report is missing or whose
`out.log` has no `exit` line), unticked items in `~/.claude/state/muse-inbox.md`, and
`Client mail: N new` (unticked lines containing `Client mail:` or `Client alert:`).
Counts are read from those private files at run time and refreshed every 5 seconds.

**Token Weather.** From the context percent: clear under 40%, showers 40-60%, storm past 60%.
It adds "hand off and end" past 60% in `~/repos` (the hub rule) and past 80% everywhere.
Shown on the same line above the prompt.

**Blast Radius.** A safety net on Bash. It holds `rm -rf`, `docker system|volume|image prune`,
`docker compose down -v`, `docker volume rm`, `git push --force` / `-f`, and production deploys
(`--prod`, or `redeploy` aimed at production). Interactively it lists what the command would
touch and asks `1 - Run it` / `2 - Refuse`. In a non-interactive session (`claude -p`, the Console
lanes) it never waits: force pushes and production deploys are denied with a reason, the rest are
allowed, and each verdict is appended to `~/.claude/state/mods/blast-radius.log`.
Regenerable caches and paths strictly inside `/tmp`, `$TMPDIR`, or an individual lane's scratch
area are allowed without a prompt and logged as `allowed (own scratch)`.
If the guard itself throws, the command is denied. The match is best effort (a net, not a shell parser). Existing hooks and permission rules still apply.

## Install

```
claude plugin marketplace add TortoiseWolfe/Claude_Commandz
claude plugin install afa-mods@afa-mods --scope user
```

On this machine the marketplace is the local clone (`claude plugin marketplace add ~/repos/Claude_Commandz`),
so a `git pull` plus `/reload-plugins` picks up edits.

## Turn it off

```
claude plugin disable afa-mods     # keep it installed, stop it loading
claude plugin uninstall afa-mods   # remove it
```

## Develop

```
claude plugin validate mods/afa-mods
claude plugin test mods/afa-mods
```
