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
(`--prod`, or `redeploy` aimed at production), plus `tmux kill-server` on the default server and
`tmux kill-session` targeting the shared `lanes` session. These tmux holds explain that the command
closes every lane window (the tmux session `lanes`), with no undo; a default-server `kill-server`
also closes every other session and window there. Private tmux sockets and individual `kill-window`
commands are not held. Interactively it lists what the command would
touch and asks `1 - Run it` / `2 - Refuse`. In a non-interactive session (`claude -p`, the Console
lanes) it never waits: force pushes and production deploys are denied with a reason, the rest are
allowed, and each verdict is appended to `~/.claude/state/mods/blast-radius.log`.
Regenerable caches and paths strictly inside `/tmp`, `$TMPDIR`, or an individual lane's scratch
area are allowed without a prompt and logged as `allowed (own scratch)`.
That includes a path built from variables the command sets itself (`S=/tmp/x; P=$S/prof; rm -rf $P`) and a relative one after a literal
`cd /full/path &&` that starts a list (`cd /tmp/x && rm -rf b481`); the log shows the full path. A `cd` followed by `;`, `||`, a pipe, `&` or a subshell
proves nothing (if it fails the rm runs elsewhere), a variable nothing set or set empty still collapses to nothing, and a path that climbs out with `..` is judged where it lands.
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
An `rm -rf` inside `docker run` or `docker compose run` is read through the container's `-v`, `--volume` and `--mount`
flags (`src/lib.ts`, `mapRmTargets`). A path under a Docker volume (a named one, an anonymous one, tmpfs) is not a
host deletion and is not held; the volume's own data is outside this check, so an `rm` of a volume that holds a database
is not held either. A path under a host folder is shown as that folder ("/w is ~/repos/.worktrees/App--x on this machine") and
judged like a host `rm` of it: held, unless it is own scratch, and a lane nobody watches may do it only inside its own
folder or a worktree. A mount of `~`, `$HOME` or `~/repos` therefore still holds. The source resolves through `~`, `$HOME`,
variables the command sets to a plain path, and `$PWD` after a plain `cd /full/path` (not after a subshell or `pushd`). A source
that does not resolve (`$X`, `$(pwd)`, backticks) still holds and the question names the mount. A path no mount covers, a
climb out of a mount with `..`, and a relative path still hold. Only the options before the image are read, so a `-v` in the
container's own arguments is not a mount. When the list may be incomplete (compose run, `--volumes-from`, a flag not read) a
path no mount covers is held, not assumed to be the container's own scratch. A named volume that `docker volume inspect` reports
as bound to a host folder is judged as that folder; one docker cannot answer for (daemon down, timeout, or a volume it does not know yet, which the same command may make) is held, and every
named volume is asked about, with no cap. A quote left in an option word, or an image word that is not plausible, also marks the
list incomplete, and a source that is not a legal volume name is judged as a folder. `docker exec` has no mounts to read and is unchanged.
A `docker run` or `docker compose run` (also podman and docker-compose) with no CPU cap and no `timeout` in front is held with the
one-line fix: add `--cpus N` (and a timeout), or run it through `~/.claude/scripts/docker-heavy.sh -- docker run ...`
(19 Oct: one uncapped run took 14.6 of 16 cores and the machine stopped answering). A cap is `--cpus`, `--cpu-quota` or
`--cpuset-cpus` among the options before the image, so a `--cpus` the container's own program is given does not count, and its value
must be a real limit (`--cpus 0`, `--cpu-quota -1` and `$X` are not). A timeout is `timeout [options] DURATION` with a positive
duration of at most a day (`timeout 0` is not one). Either a cap or a timeout in front clears it, except a detached run (`-d`, `--detach`), which outlives a timeout and needs a cap. A compose run has no `--cpus` flag, so
its service is read in the compose file the way compose picks it: the `-f` files in order, else the first default file in the folder
(`compose.yaml`, `compose.yml`, `docker-compose.yaml`, `docker-compose.yml`) plus its override file; the last file that says anything
about the service's CPU wins, so `cpus: 0` in an override uncaps it. Only a direct `cpus`, `cpu_quota` or `cpuset` key, or
`deploy.resources.limits.cpus`, with a positive number counts; a `${X}` value, an `include`, `<<:`, `extends` or `!reset` does not. The run stays held when it is unclear: a
relative path or the default files with no plain `cd` before them, `COMPOSE_FILE` (command, environment or the project env file),
`--env-file`, `--profile`, `COMPOSE_PROJECT_DIR`, a `source`, `eval` or `export` before the call, a file or service that cannot be read, or a limit set through an anchor or
`extends`. `docker-heavy.sh` itself, and `docker ps`, `logs`, `inspect`, `stats`, `update` and `exec`, are never held.
A lane nobody watches is refused with the fix and runs it again.
The classifier judges what the command itself would run: it does not match text inside quoted
message text (`echo`, `tmux send-keys`, `git commit -m`), heredoc bodies that only write a file (including
`git commit -m "$(cat <<'EOF' ...)"`), or
files a test runner loads (`claude plugin test` is not a deploy). A word containing `/` is a path, not a flag:
`~/repos/.worktrees/App--prod-lock` is not `--prod`, and `--prod` must be a whole word. When Jonathan refuses and types a
reason under "Type something.", the reason is in the refusal the session reads (only the exact `1 - Run it` label runs). `sh -c`, `ssh host "..."` and a
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
