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
A lane the dispatcher started (`AFA_LANE_UNATTENDED=1`, set by `lane.sh`) never waits either: a delete
inside its own folder, a worktree or its scratchpad, by a full path, runs and is logged as
`allowed (lane folder)`; anything else held is refused with a plain reason the lane can act on.
The question and every refusal (the lane refusals, the non-interactive denial, and the guard-failure
denial too) say in words what the command does, why it was held, and **what it would touch**: each
path with `(dir, N bytes, ...)`, `(file, N bytes)` or `(not found)`; the branch and remote host of a
force push (never a token); the stopped containers, unused volumes, images and disk use of a prune;
the volumes of a compose stack; the service of a deploy. When the words cannot say, the line reads
`can't tell what this touches: <why>`; it is never empty and never a code fragment. The lines are built in
`src/touches.ts`; `HELD_KINDS` in `src/lib.ts` lists every rule, and `src/touches.test.ts` fails if a rule has no
description or a sample command gives no targets.
The classifier judges what the command itself would run: it does not match text inside quoted
message text (`echo`, `tmux send-keys`, `git commit -m`), heredoc bodies that only write a file, or
files a test runner loads (`claude plugin test` is not a deploy). `sh -c`, `ssh host "..."` and a
heredoc fed to `bash` or piped into a shell are still read. Known tradeoff, asked for by the hub: `tmux send-keys` text is treated as
a message, so a deploy command typed into another window that way is not held here; that window's own guard judges it.
If the guard itself throws, the command is denied. The match is best effort (a net, not a shell parser). Existing hooks and permission rules still apply.
Changes load only in new `claude` processes, not in sessions already running.

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
