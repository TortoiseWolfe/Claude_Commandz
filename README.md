# Claude_Commandz

Disaster-recovery backup of Claude Code slash commands and dotfiles for this machine. If your system dies, clone this repo on the replacement and use the restore procedure below to get every global command, dotfile, and per-project command back into place.

## Layout

Files live at their **original relative path** so restore is always a direct `cp -a` with no path rewriting.

```
Claude_Commandz/
├── README.md                     # this file
├── CLAUDE.md                     # instructions for Claude Code working in this repo
├── global/
│   ├── .claude/commands/         # 71 files — mirror of ~/.claude/commands/, minus email-voice-check.md and weekly-review.md (they name clients: private half)
│   ├── .claude/workflows/        # saved Workflow scripts: director.js (tiered Opus/Haiku/Sonnet); client-specific workflows are kept out of this public repo
│   ├── .claude/agents/           # tiered subagents: shell-proxy, worker-mechanical, worker-builder, reviewer-senior; Explore.md overrides the built-in Explore to run on Sonnet
│   ├── .claude/skills/agent-notes/  # Muse <-> Claude Code note protocol over Gmail
│   ├── .claude/skills/council/      # independent advisors stress-test a decision or diff
│   ├── .claude/skills/game-demo/    # scaffold a playable 3D demo inside a ScriptHammer app
│   ├── .claude/skills/scroll-world/ # scroll-driven 3D world pipeline (see #9)
│   │   # not here: skills/site-model (private half); skills/synced (Anthropic re-syncs it); skills/graphify (reinstall, see Restore)
│   ├── .claude/scripts/          # jev_precheck.py (Jev shadow pre-screen for director.js); muse_jev.py (Jev second opinion on the Muse poller's iffy answers); openclaw_tray.py (4-tool client for the OpenClaw tray MCP); api-quiet.sh; gated_post.sh; portainer_status.py; email_voice_check.py is in the private half
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
uv tool install graphifyy==0.9.23 && graphify install --platform claude   # graphify is third-party: reinstall it, don't back it up

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
uv tool install graphifyy==0.9.23 && graphify install --platform claude   # graphify is third-party: reinstall it, don't back it up
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

## Lane dispatcher

`global/.claude/scripts/lane.sh` starts work in a named tmux window and keeps a hand-off folder under `~/.claude/state/lanes/` for the prompt, report, and logs.

```bash
lane.sh [--dry-run] [--engine claude|codex|copilot] [--effort <level>] [--connectors|--no-connectors] [--allow-client] [--console [--headless]] <name> <repo-dir> <model> <prompt-file>
lane.sh --status | --send <name> <text> | [--dry-run] [--console] [--connectors] --resume <name>
lane.sh --help
```

Claude is the default, so existing calls continue to use Claude Code. `--console` keeps Claude on Console credits, and `--headless` runs Claude without an interactive session. Codex and Copilot run headless and save their output in `out.log`; `--status` reports whether each log is still running or has an exit code. `--send` types a message into a live window, and `--resume` resumes a Claude session. Claude lanes keep the existing `console` or `subscription` value in the index. Codex and Copilot use that same index column for `codex` and `copilot`.

Claude lanes start lean, because a lane re-reads everything it starts with on every turn. Effort defaults to `high` on every model (haiku takes none; `--effort` overrides it), and a lane compacts in place at 200K. One `--settings` JSON switches off the claude.ai connectors, the plugins listed in `LANE_PLUGINS_OFF` at the top of the script, and the workspace `~/repos/CLAUDE.md`, which `--append-system-prompt-file` replaces with `$LANE_RULES` (default `~/repos/hub/lane-rules.md`; with no such file the workspace CLAUDE.md loads as before). The Artifact tools are withheld with `--disallowedTools`. Connectors stay on when the lane name ends in `-mail`, when the prompt file mentions Gmail, Calendar or Drive, or with `--connectors`; the choice is column 11 of the index, shown by `--status` and reused by `--resume` (an older row without it counts as on). Measured with a one-line "Reply READY" prompt on sonnet, a lane's first-turn context went from 58.2K to 34.0K tokens.

`--no-connectors` explicitly disables connectors even when the lane name or prompt would otherwise enable them.

For Codex and Copilot, the dispatcher checks repository roots in column 2 of `~/repos/hub/client-senders.tsv`, paths listed in `~/repos/hub/client-repos.txt`, and everything under `~/repos/CD/`. The text file has one path per line; blank lines and lines starting with `#` are ignored. Both list files must be readable or those engines are refused. `--allow-client` bypasses this check. The model argument is forwarded to Codex or Copilot only when it is not a Claude model name (`opus`, `sonnet`, `haiku`, or `fable`).

## Refresh (back up from live system)

When global or project commands evolve, re-sync into this repo and commit:

```bash
# Global
for f in ~/.claude/commands/*.md; do case "$(basename "$f")" in email-voice-check.md|weekly-review.md) ;; *) cp -p "$f" global/.claude/commands/ ;; esac; done   # those two name clients: hub/claude-private
cp ~/.claude/settings.json global/dotfiles/settings.json
cp ~/.claude/statusline-command.sh global/dotfiles/statusline-command.sh
cp ~/.claude/workflows/director.js global/.claude/workflows/   # client-specific workflows stay out (public repo)
cp -a ~/.claude/agents/. global/.claude/agents/
for s in agent-notes council game-demo scroll-world; do cp -a ~/.claude/skills/$s/. global/.claude/skills/$s/; done   # site-model is private
cp ~/.claude/scripts/jev_precheck.py ~/.claude/scripts/muse_jev.py ~/.claude/scripts/openclaw_tray.py ~/.claude/scripts/api-quiet.sh ~/.claude/scripts/gated_post.sh ~/.claude/scripts/portainer_status.py global/.claude/scripts/
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

## OpenClaw local model (last fallback)

OpenClaw's agents run `google/gemini-3.5-flash > 3.5-flash-lite > 3.6-flash`. Gemini's free tier is rate limited,
so a busy day can run all three dry. `openclaw-local/docker-compose.yml` adds an Ollama container
(`gpus: all` for the NVIDIA GPU, `mem_limit` 6g, 8 cpus, loopback port 11434) and `global/.claude/scripts/openclaw_local.py` appends
`ollama/qwen2.5:1.5b-instruct` as the LAST fallback for `agents.defaults` and the receptionist. No tools are
added, no paid provider, no secrets (the `ollama-local` apiKey is Ollama's documented marker for a loopback host).

```bash
docker compose -p openclaw-local -f openclaw-local/docker-compose.yml up -d
docker exec openclaw-local-ollama ollama pull qwen2.5:1.5b-instruct
wsl.exe -d OpenClawGateway -u openclaw -e python3 - apply < global/.claude/scripts/openclaw_local.py   # one config write, hot-applies
wsl.exe -d OpenClawGateway -u openclaw -e python3 - verify < global/.claude/scripts/openclaw_local.py
```

A config write can make the gateway reload the google plugin and close the OpenClaw app: after any apply, reopen
it from its Start-menu shortcut and check `OpenClaw.Tray.WinUI.exe` is running. `openclaw_harden.sh` carries an
existing `ollama/` tail through, so re-running it does not drop the local model.

Rules for anything that tests OpenClaw (enforced in `openclaw_local.py`, tested in `tests/test_openclaw_local.py`):

1. Jonathan's one chat is "Main session", `agent:main:main`; replies go there and nowhere else.
2. Never create or write any other session under agent `main` (no `--agent main`, no `--session-key agent:main:...`,
   no injected notes). Test on the tool-less agent `hubtest` with `openclaw_local.py probe`, which uses a fresh
   `agent:hubtest:probe-<time>` session each run.

Measured on CPU (before the container got the GPU in PR #15) while another 8-core job ran (cold = first call after the model loads, 8.3K-token real bootstrap):
qwen2.5:1.5b 42 s cold prefill, 6 to 13 s warm, 7 to 17 tok/s; 3b 80 s cold, 10 tok/s; llama3.2:3b 78 s cold, 6 tok/s;
7b 141 s cold, 5 tok/s. Status questions are answered correctly by all of them. None reliably refuses to claim an action
it cannot take, so treat the local tier as a way to keep answering, not as a router.

## Design rationale

Why mirror the original paths instead of flattening or grouping by category?

- **Uniform restore.** Every scenario uses `cp -a <src>/. <dst>/` with matching path shapes. No per-file logic, no rename tables.
- **No name collisions.** `ScriptHammer/prep-operator.md` and `TurtleWolfe/prep-operator.md` have different contents; keeping them under their repo root preserves both without suffix hacks.
- **Grep-friendly.** `grep -r 'pattern' repos/ScriptHammer` maps cleanly to "search ScriptHammer's commands."
- **Self-documenting.** The path tells you exactly where the file came from and where it goes back to.
