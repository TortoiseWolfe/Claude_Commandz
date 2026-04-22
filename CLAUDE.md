# CLAUDE.md

Instructions for Claude Code when working inside the `Claude_Commandz` repository.

## Purpose

This repo is a **disaster-recovery backup**, not a live source of truth. It mirrors three things:

1. `~/.claude/commands/` — user-level global slash commands
2. `~/.claude/` dotfiles — `settings.json`, `statusline-command.sh`, etc.
3. Each `~/repos/<project>/.claude/commands/` — per-project slash commands

If the machine dies, this repo is how everything above gets restored.

## Structure invariant (do not violate)

Every backed-up file lives at its **original relative path** under one of two roots:

```
global/.claude/commands/<file>        mirrors  ~/.claude/commands/<file>
global/dotfiles/<file>                mirrors  ~/.claude/<file>    (selected files)
repos/<name>/.claude/commands/<file>  mirrors  ~/repos/<name>/.claude/commands/<file>
```

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

# Per-repo commands — loop over existing repos/<name>/ dirs
for d in repos/*/; do
  name=$(basename "$d")
  src=~/repos/"$name"/.claude/commands
  [ -d "$src" ] && cp -a "$src/." "$d/.claude/commands/"
done
```

Then review `git status`, drop intentionally-excluded files (see below), and commit.

## Exclusions (intentional)

- **`mercor-*` commands in `good_prompt_bad_prompt`** — ephemeral evaluation tooling, not worth preserving. If they reappear after a refresh, delete them before committing.
- **Repos without `.claude/commands/`** — not represented. Currently: `ada-stair-generator`, `ScanDo`, `Template-Library`, `GrimGlow_planning`, `HR24`, `Trinam_23`, `RedWood-Blog`, `nextjs-tutorial`, `sandbox*`, `SpokeToWork` (app, as opposed to `-Business-Development`).

## Unknown files

If you see a file in `repos/<name>/` that you don't recognize, **do not delete it**. It is almost certainly a command from a project you haven't explored this session. Ask before removing anything.

## Related docs

- `README.md` (this repo) — human-facing restore procedure
- `/home/TurtleWolfe/repos/CLAUDE.md` — workspace overview of all projects
