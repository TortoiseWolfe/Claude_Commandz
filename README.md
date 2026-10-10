# Claude_Commandz

Disaster-recovery backup of Claude Code slash commands and dotfiles for this machine. If your system dies, clone this repo on the replacement and use the restore procedure below to get every global command, dotfile, and per-project command back into place.

## Layout

Files live at their **original relative path** so restore is always a direct `cp -a` with no path rewriting.

```
Claude_Commandz/
├── README.md                     # this file
├── CLAUDE.md                     # instructions for Claude Code working in this repo
├── global/
│   ├── .claude/commands/         # 65 files — mirror of ~/.claude/commands/
│   ├── .claude/workflows/        # saved Workflow scripts: director.js (tiered Opus/Haiku/Sonnet); client-specific workflows are kept out of this public repo
│   ├── .claude/agents/           # tiered subagents: shell-proxy, worker-mechanical, worker-builder, reviewer-senior; Explore.md overrides the built-in Explore to run on Sonnet
│   ├── .claude/skills/agent-notes/  # Muse <-> Claude Code note protocol over Gmail
│   ├── .claude/scripts/          # jev_precheck.py (Jev shadow pre-screen for director.js); openclaw_tray.py (4-tool client for the OpenClaw tray MCP)
│   ├── .claude/hooks/            # roadmap-drift.sh; muse-drafts.sh (picks up Muse's notes in the ~/repos session only; state file of handled draft IDs); secret-guard.py; explore-override-drift.sh (+ .ref) warns when built-in Explore changes under agents/Explore.md; client-specific hooks are kept out of this public repo
│   └── dotfiles/                 # 2 files — settings.json, statusline-command.sh
└── repos/
    ├── ScriptHammer/.claude/commands/                         (23 files)
    ├── TurtleWolfe/.claude/commands/                          (23 files)
    ├── good_prompt_bad_prompt/.claude/commands/               (14 files, mercor-* excluded)
    ├── SpokeToWork---Business-Development/.claude/commands/   (13 files)
    ├── PeerMentor/.claude/commands/                            (8 files)
    ├── TranScripts/.claude/commands/                           (7 files)
    ├── KDG/.claude/commands/                                   (1 file)
    └── drupal-v2-sandbox/.claude/commands/                     (1 file)
```

**Totals:** 65 global commands + 2 dotfiles + 90 project commands across 8 repos = **157 backed-up files**.

## Restore on a new machine

Clone this repo anywhere, then pick the scenario you need.

### Scenario A — Full restore (global + all repo commands)

```bash
# Global commands and dotfiles
mkdir -p ~/.claude/commands
cp -a global/.claude/commands/. ~/.claude/commands/
cp global/dotfiles/settings.json ~/.claude/settings.json
cp global/dotfiles/statusline-command.sh ~/.claude/statusline-command.sh
chmod +x ~/.claude/statusline-command.sh
mkdir -p ~/.claude/workflows ~/.claude/agents ~/.claude/skills
cp -a global/.claude/workflows/. ~/.claude/workflows/
cp -a global/.claude/agents/. ~/.claude/agents/
cp -a global/.claude/skills/. ~/.claude/skills/
mkdir -p ~/.claude/scripts && cp -a global/.claude/scripts/. ~/.claude/scripts/   # jev_precheck.py reads its key from ~/.config/typesafe/api-key; openclaw_tray.py reads the tray token at run time (neither backed up)
mkdir -p ~/.claude/hooks && cp -a global/.claude/hooks/. ~/.claude/hooks/

# All project commands (assumes ~/repos/<name>/ already exists — clone those first)
for d in repos/*/; do
  name=$(basename "$d")
  mkdir -p ~/repos/"$name"/.claude/commands
  cp -a "$d/.claude/commands/." ~/repos/"$name"/.claude/commands/
done
```

### Scenario B — Global only

Use this if you're setting up a new shell environment but don't need the project repos yet.

```bash
mkdir -p ~/.claude/commands
cp -a global/.claude/commands/. ~/.claude/commands/
cp global/dotfiles/settings.json ~/.claude/settings.json
cp global/dotfiles/statusline-command.sh ~/.claude/statusline-command.sh
chmod +x ~/.claude/statusline-command.sh
mkdir -p ~/.claude/workflows ~/.claude/agents ~/.claude/skills
cp -a global/.claude/workflows/. ~/.claude/workflows/
cp -a global/.claude/agents/. ~/.claude/agents/
cp -a global/.claude/skills/. ~/.claude/skills/
mkdir -p ~/.claude/scripts && cp -a global/.claude/scripts/. ~/.claude/scripts/   # jev_precheck.py reads its key from ~/.config/typesafe/api-key; openclaw_tray.py reads the tray token at run time (neither backed up)
mkdir -p ~/.claude/hooks && cp -a global/.claude/hooks/. ~/.claude/hooks/
```

### Scenario C — Single repo only

```bash
# Example: restore just ScriptHammer's commands
mkdir -p ~/repos/ScriptHammer/.claude/commands
cp -a repos/ScriptHammer/.claude/commands/. ~/repos/ScriptHammer/.claude/commands/
```

## Coverage

| Repo | Commands | Notes |
|---|---|---|
| ScriptHammer | 23 | SpecKit workflow, `prep-operator`, `fetch-test-results` |
| TurtleWolfe | 23 | Parallel to ScriptHammer, diverged `prep-operator` and `dispatch` |
| good_prompt_bad_prompt | 14 | `prime-*` role variants, `debrief`; `mercor-*` excluded |
| SpokeToWork---Business-Development | 13 | SpecKit + `prep-operator`, `dispatch`, `prime` |
| PeerMentor | 8 | SpecKit only |
| TranScripts | 7 | LinkedIn/Facebook extractors, transcript cleaners |
| KDG | 1 | `prime-interview` |
| drupal-v2-sandbox | 1 | `site-audit` |

**Repos with no `.claude/commands/` directory** (not backed up): `ada-stair-generator`, `ScanDo`, `Template-Library`, `GrimGlow_planning`, `HR24`, `Trinam_23`, `RedWood-Blog`, `nextjs-tutorial`, `sandbox`, `sandbox_b`, `sandbox_oneshot`, `SpokeToWork` (the app — distinct from `SpokeToWork---Business-Development`).

## Exclusions

- **`mercor-*` commands** in `good_prompt_bad_prompt` are intentionally dropped. They are ephemeral evaluation tooling tied to a specific contractor workflow and not worth preserving across machines.

## Refresh (back up from live system)

When global or project commands evolve, re-sync into this repo and commit:

```bash
# Global
cp -a ~/.claude/commands/. global/.claude/commands/
cp ~/.claude/settings.json global/dotfiles/settings.json
cp ~/.claude/statusline-command.sh global/dotfiles/statusline-command.sh
cp ~/.claude/workflows/director.js global/.claude/workflows/   # client-specific workflows stay out (public repo)
cp -a ~/.claude/agents/. global/.claude/agents/
cp -a ~/.claude/skills/agent-notes/. global/.claude/skills/agent-notes/
cp ~/.claude/scripts/jev_precheck.py ~/.claude/scripts/openclaw_tray.py global/.claude/scripts/
cp ~/.claude/hooks/roadmap-drift.sh ~/.claude/hooks/muse-drafts.sh ~/.claude/hooks/secret-guard.py ~/.claude/hooks/explore-override-drift.sh ~/.claude/hooks/explore-override-drift.ref global/.claude/hooks/

# Per-repo — only loops over dirs already tracked here
for d in repos/*/; do
  name=$(basename "$d")
  src=~/repos/"$name"/.claude/commands
  [ -d "$src" ] && cp -a "$src/." "$d/.claude/commands/"
done

# Drop excluded files that may have reappeared
rm -f repos/good_prompt_bad_prompt/.claude/commands/mercor-*.md

git status
git add -A
git commit -m "refresh: sync command backups"
```

To **add a new repo** to the backup set, create `repos/<new-name>/.claude/commands/` and run the refresh loop.

## Design rationale

Why mirror the original paths instead of flattening or grouping by category?

- **Uniform restore.** Every scenario uses `cp -a <src>/. <dst>/` with matching path shapes. No per-file logic, no rename tables.
- **No name collisions.** `ScriptHammer/prep-operator.md` and `TurtleWolfe/prep-operator.md` have different contents; keeping them under their repo root preserves both without suffix hacks.
- **Grep-friendly.** `grep -r 'pattern' repos/ScriptHammer` maps cleanly to "search ScriptHammer's commands."
- **Self-documenting.** The path tells you exactly where the file came from and where it goes back to.
