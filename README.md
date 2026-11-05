# Claude_Commandz

Disaster-recovery backup of Claude Code slash commands and dotfiles for this machine. If your system dies, clone this repo on the replacement and use the restore procedure below to get every global command, dotfile, and per-project command back into place.

## Layout

Files live at their **original relative path** so restore is always a direct `cp -a` with no path rewriting.

```
Claude_Commandz/
├── README.md                     # this file
├── CLAUDE.md                     # instructions for Claude Code working in this repo
├── global/
│   ├── .claude/commands/         # 73 files — mirror of ~/.claude/commands/, minus email-voice-check.md and weekly-review.md (they name clients: private half)
│   ├── .claude/workflows/        # saved Workflow scripts: director.js (tiered Opus/Haiku/Sonnet); client-specific workflows are kept out of this public repo
│   ├── .claude/agents/           # tiered subagents: shell-proxy, worker-mechanical, worker-builder, reviewer-senior; Explore.md overrides the built-in Explore to run on Sonnet
│   ├── .claude/skills/agent-notes/  # Muse <-> Claude Code note protocol over Gmail
│   ├── .claude/skills/council/      # independent advisors stress-test a decision or diff
│   ├── .claude/skills/game-demo/    # scaffold a playable 3D demo inside a ScriptHammer app
│   ├── .claude/skills/scroll-world/ # scroll-driven 3D world pipeline (see #9)
│   │   # not here: skills/site-model (private half); skills/synced (Anthropic re-syncs it); skills/graphify (reinstall, see Restore)
│   ├── .claude/scripts/          # jev_precheck.py (Jev shadow pre-screen for director.js); muse_jev.py (Jev second opinion on the Muse poller's iffy answers); openclaw_tray.py (4-tool client for the OpenClaw tray MCP); session_to_md.py (scrubbed Markdown export of Claude Code sessions); api-quiet.sh; gated_post.sh; portainer_status.py; email_voice_check.py is in the private half
│   ├── .claude/hooks/            # roadmap-drift.sh; muse-drafts.sh (picks up Muse's notes in the ~/repos session only; state file of handled draft IDs); secret-guard.py; explore-override-drift.sh (+ .ref) warns when built-in Explore changes under agents/Explore.md; context-warn.py warns before hub auto-compaction
│   └── dotfiles/                 # 3 files — .bashrc, settings.json, statusline-command.sh
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

`context-warn.py` checks the hub transcript on `PostToolUse` and `UserPromptSubmit`, warning once per session at 50%, 60%, and 70% of the auto-compaction window so the session can be handed off before compaction. Register it in `settings.json` with `"PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/context-warn.py"}]}]` and `"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/context-warn.py"}]}]`.

`coop-scrubber.py` (a `PostToolUse` hook) rewrites a tool's result in a co-op folder so every person's name, email address and phone number is a placeholder before a vendor model reads it; elsewhere it does nothing. The work is in `scripts/coop_scrubber.py`; `scripts/coop_roster_build.py` builds the private roster at run time and prints counts only; `scripts/coop_scrubber_install_presidio.sh` sets up the Presidio venv. The folder patterns, the roster and the venv are private and never come into this repo. Register it with `"PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/coop-scrubber.py"}]}]`. Tests: `scripts/tests/test_coop_scrubber.py`, `test_coop_roster_build.py`, `test_coop_scrubber_hook.py`.

**Totals:** 73 global commands + 3 dotfiles + 90 project commands across 8 repos = **166 backed-up files**.

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

### Updating a machine that already runs (install after a merge)

Scenarios A and B are for an empty machine: their `cp -a` lines overwrite every file, so on a machine with live files they
would also overwrite a file edited after its last commit, or one that was left older than the repo on purpose. After a merge
use the install script instead. It copies only the files that changed since the last install, and refuses a file whose live
copy matches neither the old nor the new repo version (a live edit), printing its name.

```bash
cd ~/repos/Claude_Commandz && git pull
python3 global/.claude/scripts/install_global.py --dry-run   # what would be copied, what would be refused
python3 global/.claude/scripts/install_global.py             # copy it; the commit is recorded in ~/.claude/state/install-global-commit
python3 global/.claude/scripts/install_global.py --status    # every live file that differs from the repo: "stale" or "live edit"
```

First run on a machine with no record: `--status`, deal with each "live edit" (commit it into the repo, or discard it), then
`--record` (or pass `--from <commit>`). `--include-stale` also reinstalls the stale files `--status` lists. It covers scripts,
hooks, workflows, agents and skills. Tests: `python3 -m pytest global/.claude/scripts/tests/test_install_global.py`.

### Installing the no-AI wall (a person runs this, never a session)

The wall's three files live in `~/.claude/no-ai-wall/`, a folder sessions cannot change. One command installs them from a named
commit (it reads git, never the working tree or the network), backs up every live file it replaces, moves the hook entry in
`settings.json`, also runs the usual install step for the other global files, and proves the result:

```bash
bash <(git -C ~/repos/Claude_Commandz show <commit>:global/.claude/no-ai-wall/install.sh) <commit>
```

The last line it prints is `WALL OK <commit> refused <n>/<n> ordinary <n>/<n>`, or `WALL FAILED <reason> after undoing itself`.
The backup folder, `~/.claude/state/no-ai-wall-backups/<date>-<commit>/`, holds `undo.sh`; `bash <that folder>/undo.sh` puts everything back.
Tests: `python3 -m pytest global/.claude/scripts/tests/test_no_ai_wall_install.py`.

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
lane.sh [--dry-run] [--engine claude|codex|copilot|agy|opencode|local] [--effort <level>] [--connectors|--no-connectors] [--allow-client] [--allow-low-tier] [--console [--headless]] <name> <repo-dir> <model> <prompt-file>
lane.sh --status | --send <name> <text> | [--dry-run] [--console] [--connectors] [--allow-low-tier] --resume <name>
lane.sh --help
```

Claude is the default, so existing calls continue to use Claude Code. `--console` keeps Claude on Console credits, and `--headless` runs Claude without an interactive session. Codex, Copilot, agy, OpenCode and local run headless and save their output in `out.log`; `--status` reports whether each log is still running or has an exit code. `--send` types a message into a live window, and `--resume` resumes a Claude session. Claude lanes keep the existing `console` or `subscription` value in the index. Codex, Copilot, agy and OpenCode use that same index column for `codex`, `copilot`, `agy` and `opencode`. `--engine opencode` runs `opencode_lane.sh`: OpenCode on the free Gemini API, then the free OpenRouter model if Gemini is out of quota (Groq's free tier is too small for OpenCode's prompt). Both free APIs train on input, so it refuses client repos and `~/repos` itself, like Codex and Copilot. Own-repo code lanes try agy, Codex, Copilot and OpenCode in that order, then Claude Sonnet. `--engine local` runs `local_lane.sh`: the same OpenCode, pointed at an Ollama server on this machine (`LOCAL_LANE_URL`, default `http://127.0.0.1:11434`; start it with a context length of 32768 or more). Model `ollama/<tag>`, default `ollama/qwen3-coder:30b`. Nothing leaves the machine and there is no quota, and it never falls back to a hosted model: with no server or the model not pulled it stops with exit 69. It keeps the client guard for now, and the dispatcher does not pick it.

Claude lanes start lean, because a lane re-reads everything it starts with on every turn. Effort defaults to `high` on every model (haiku takes none; `--effort` overrides it), and a lane compacts in place at 200K. One `--settings` JSON switches off the claude.ai connectors, the plugins listed in `LANE_PLUGINS_OFF` at the top of the script, and the workspace `~/repos/CLAUDE.md`, which `--append-system-prompt-file` replaces with `$LANE_RULES` (default `~/repos/hub/lane-rules.md`; with no such file the workspace CLAUDE.md loads as before). The Artifact tools are withheld with `--disallowedTools`. Connectors stay on when the lane name ends in `-mail`, when the prompt file mentions Gmail, Calendar or Drive, or with `--connectors`; the choice is column 11 of the index, shown by `--status` and reused by `--resume` (an older row without it counts as on). Measured with a one-line "Reply READY" prompt on sonnet, a lane's first-turn context went from 58.2K to 34.0K tokens.

`--no-connectors` explicitly disables connectors even when the lane name or prompt would otherwise enable them.

3D fitting, photo matching and site-model work runs only on `fable` or `opus` (each at effort high). When a Claude lane's prompt file contains `data/sites/` or `site-model`, lane.sh refuses `sonnet`, `haiku` or any other model on start, `--console`, `--headless` and `--resume`, and names the rule in `~/repos/hub/lane-rules.md`. `--allow-low-tier` overrides it. Jonathan's rule of 19 Oct 2026: a cheap model on work it can't do is no saving.

### Where is this lane?

`global/.claude/scripts/lane_brief.py` answers "where is this lane and does it need me?" in one call, in place of `lane.sh --status`, a `tmux capture-pane`, a read of `report.md` and `lane.sh --recycle-point`. It is read-only: it sends no keys and writes no file.

```bash
python3 ~/.claude/scripts/lane_brief.py <lane> [--lines N] [--json]   # exit 2 for a lane that is not in index.tsv
```

It prints line 1 of `report.md` with the file's time and the same report verdict `lane.sh --status` shows (written, stale, or not yet), the engine and model, the context in K against the lane's recycle line, whether the pane is still running claude (`DEAD (pane shows bash)` when not), whether a dialog is waiting (the lane_watch.sh rule) with the dialog's first lines, and the last N (default 12) screen lines joined with `-J`, so a long line is not cut at the screen width. A grey suggestion in an idle input box is marked `(suggestion, not typed)`. A headless lane (agy, codex, copilot, opencode) has no pane: it prints the tail of `out.log` and the time the engine is blocked until, in Eastern time, from `openclaw-dispatch.json`. `--json` prints the same facts as one object with nothing cut.

### The hub starts lean too

The hub (`claude` started in `~/repos`) used to carry about 80K tokens before its first reply, against about 47K for a lane. `global/.claude/scripts/hub_lean.py` closes most of that gap with no new flags: it writes a project settings file in `~/repos/.claude/` (untracked, the workspace `.gitignore` is an allowlist) that Claude Code reads whenever a session starts there. The file switches off the plugins in lane.sh's `LANE_PLUGINS_OFF`, withholds the Artifact tools (`LANE_TOOLS_OFF`, via `permissions.deny`) and disconnects every claude.ai connector except Gmail, Google Calendar and Google Drive, the only three the hub called in its 16-19 Oct transcripts. Plugins and tools are read out of lane.sh, so there is one list. The hub keeps its own `CLAUDE.md`, user skills, slash commands and agents.

```bash
python3 ~/.claude/scripts/hub_lean.py --install   # write the file (silent when current); lane.sh runs this on every Claude lane start
python3 ~/.claude/scripts/hub_lean.py --check     # exit 1 when missing or stale
python3 ~/.claude/scripts/hub_lean.py --print     # show what it manages
```

On a new or second machine, run `--install` once after syncing the scripts (any lane start does it too). A claude.ai connector added to the account later loads in the hub until its name is added to `HUB_CONNECTORS_OFF` (`allowedMcpServers` ignores claude.ai connectors, so an allow-list is not possible). Measured on NX-01 with a one-line prompt in an interactive opus session in `~/repos`: first reply 80.4K tokens before, 53.5K after.

### Three ideas borrowed from OpenDev (a terminal coding agent): smaller rules, big output in files, reminders

A comparison with OpenDev found three habits worth borrowing. All three are on for new lanes and a restarted hub; a lane already running keeps what it started with.

- **Modular lane rules.** The rules live in the workspace repo as parts: `hub/lane-rules/core.md` (every lane gets it) plus `mail.md`, `browser.md`, `streams.md`, `coop.md` and `fleet.md`. `global/.claude/scripts/lane_rules_compose.py` picks the parts a lane needs from its name, folder, connectors and prompt text (when unsure, a part is included) and `lane.sh` writes the result to `~/.claude/state/lanes/.rules/<lane folder name>.md` (hub-owned, outside the folder the lane can write, like the `.deny` files), which it passes with `--append-system-prompt-file`. `hub/lane-rules.md` is now a short pointer, so an old prompt that says "Read ~/repos/hub/lane-rules.md first" costs a few lines. A test in the workspace repo (`hub/scripts/test_lane_rules_parts.py`) proves every rule line is in exactly one part and none was reworded.
- **Big output goes to a file.** `global/.claude/scripts/big_output.py`: when a session has `AFA_BIG_OUT=1` (lane.sh sets it for every Claude lane, `hub_lean.py` for the hub) a long answer from `hub_digest.py`, `lane.sh --status`, `gmail_api.py` (`drafts-list`, `messages-list`, `message-text`) or `muse_poll_record.sh open` that is over 8,000 characters is written whole to `<lane folder>/out-<name>-<time>.txt` (or `~/.claude/state/out/`), and the terminal shows at most 500 characters plus the path. `--full` prints everything. Scripts that read another script's output pass `--full` or set `AFA_BIG_OUT=0`, so their input does not change.
- **Rules reminder hook.** `global/.claude/hooks/rules_reminder.py` runs after each tool call (`PostToolUse` and `PostToolUseFailure`). Every 30 calls, or when the same command fails twice, it puts three key rules back in view, at most twice per kind per session. Lanes get it through the `--settings` that `lane.sh` passes; the hub gets it from `hub_lean.py`. Check it with `python3 ~/.claude/hooks/rules_reminder.py --self-test`.

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
cp ~/.claude/scripts/jev_precheck.py ~/.claude/scripts/muse_jev.py ~/.claude/scripts/openclaw_tray.py ~/.claude/scripts/session_to_md.py ~/.claude/scripts/api-quiet.sh ~/.claude/scripts/gated_post.sh ~/.claude/scripts/portainer_status.py global/.claude/scripts/
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
