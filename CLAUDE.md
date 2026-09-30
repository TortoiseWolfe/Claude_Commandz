# CLAUDE.md

Instructions for Claude Code when working inside the `Claude_Commandz` repository.

## Purpose

This repo is a **disaster-recovery backup**, not a live source of truth. It mirrors four things:

1. `~/.claude/commands/` — user-level global slash commands
2. `~/.claude/{workflows,agents,skills,scripts,hooks}/` — saved Workflow scripts, tiered subagents, user skills, helper scripts and hook scripts (selected files; see "Public repo" below)
3. `~/.claude/` dotfiles — `settings.json`, `statusline-command.sh`, etc.
4. Each `~/repos/<project>/.claude/commands/` — per-project slash commands

If the machine dies, this repo is how everything above gets restored.

## Structure invariant (do not violate)

Every backed-up file lives at its **original relative path** under one of three roots:

```
global/.claude/<dir>/<file>           mirrors  ~/.claude/<dir>/<file>
                                      <dir> = commands | workflows | agents | skills | scripts | hooks
global/dotfiles/<file>                mirrors  ~/.claude/<file>    (selected files)
repos/<name>/.claude/commands/<file>  mirrors  ~/repos/<name>/.claude/commands/<file>
```

A new `~/.claude/<dir>/` gets backed up as `global/.claude/<dir>/`, never under `dotfiles/` or a
renamed folder. Skills keep their whole folder (`skills/<name>/SKILL.md` plus anything beside it).

Restore is always a direct `cp -a` with no path rewriting. Because of this:

- **Do not flatten.** Don't move files up a level "for convenience."
- **Do not rename.** The filename is the slash-command name — renaming breaks restore.
- **Do not reorganize by category** (e.g., `speckit/`, `wireframe/`). Category grouping destroys the mirror and makes restore bespoke per file.
- **Do not dedupe across repos.** Two files with the same name (e.g., `prep-operator.md` in `ScriptHammer` vs `TurtleWolfe`) usually have different contents — they are per-project customizations.

## Authoring rule

**Do not write new slash commands in this repo.** Commands are authored in their source location:

- Global commands → edit `~/.claude/commands/<name>.md`, then sync into `global/.claude/commands/`
- Project commands → edit `~/repos/<project>/.claude/commands/<name>.md`, then sync into `repos/<project>/.claude/commands/`

If the user asks for a new command, create it in the source location first, then refresh this backup.

## Refresh workflow

When the user says "back up commands", "refresh backups", or similar, re-sync from live locations:

```bash
# Global commands
cp -a ~/.claude/commands/. global/.claude/commands/

# Global dotfiles (only the tracked ones — don't sweep all of ~/.claude/)
cp ~/.claude/settings.json global/dotfiles/settings.json
cp ~/.claude/statusline-command.sh global/dotfiles/statusline-command.sh

# Workflows, agents, skills, scripts, hooks — only the files already tracked here
cp ~/.claude/workflows/director.js global/.claude/workflows/
cp -a ~/.claude/agents/. global/.claude/agents/
cp -a ~/.claude/skills/agent-notes/. global/.claude/skills/agent-notes/
cp ~/.claude/scripts/jev_precheck.py ~/.claude/scripts/openclaw_tray.py global/.claude/scripts/
cp ~/.claude/hooks/roadmap-drift.sh ~/.claude/hooks/explore-override-drift.sh ~/.claude/hooks/explore-override-drift.ref global/.claude/hooks/

# Per-repo commands — loop over existing repos/<name>/ dirs
for d in repos/*/; do
  name=$(basename "$d")
  src=~/repos/"$name"/.claude/commands
  [ -d "$src" ] && cp -a "$src/." "$d/.claude/commands/"
done
```

Then review `git status`, drop intentionally-excluded files (see below), and commit.

## Public repo

`TortoiseWolfe/Claude_Commandz` is **public**. Anything that names a client, a client's people or
a private mailbox stays out, even when it lives in a backed-up folder:

- `~/.claude/workflows/rescuedogs-weekly-review.js`, `~/.claude/skills/greg/`, `~/.claude/hooks/greg-inbox.sh*`
- Keys and tokens never come here (`~/.config/typesafe/api-key`, OpenClaw tokens). `jev_precheck.py` reads its key at run time.

Before adding a new file from those folders, read it for names, addresses and client details first.

Those private items ARE backed up, in the private hub repo `TortoiseWolfe/workspace` (`~/repos`),
under `hub/claude-private/` at the same mirror paths, along with `~/.claude/plans/` and every
project's `memory/`. Refresh there with `hub/scripts/sync-private.sh`.

## Exclusions (intentional)

- **`mercor-*` commands in `good_prompt_bad_prompt`** — ephemeral evaluation tooling, not worth preserving. If they reappear after a refresh, delete them before committing.
- **Repos without `.claude/commands/`** — not represented. Currently: `ada-stair-generator`, `ScanDo`, `Template-Library`, `GrimGlow_planning`, `HR24`, `Trinam_23`, `RedWood-Blog`, `nextjs-tutorial`, `sandbox*`, `SpokeToWork` (app, as opposed to `-Business-Development`).

## Unknown files

If you see a file in `repos/<name>/` that you don't recognize, **do not delete it**. It is almost certainly a command from a project you haven't explored this session. Ask before removing anything.

## Related docs

- `README.md` (this repo) — human-facing restore procedure
- `/home/TurtleWolfe/repos/CLAUDE.md` — workspace overview of all projects
