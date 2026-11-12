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
                                      <dir> = commands | workflows | agents | skills | scripts | hooks | no-ai-wall
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

## The no-AI wall has its own folder, and a person installs it

`global/.claude/no-ai-wall/` mirrors `~/.claude/no-ai-wall/` for three files only: `no_ai_wall.py`, `no_ai_wall.sh` and `no-ai-wall-guard.py`.
Sessions are refused any change there, to the no-AI list and to settings files, and to nothing else in scripts or hooks.
`install.sh` and `smoke.py` sit beside them in the repo but are never copied live, and `install_global.py` does not touch this folder.
The install is a person-only step, run in a terminal and never by a session (README.md, "Installing the no-AI wall"). Do not add a `cp` line for it to the refresh list.

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
cp ~/.claude/scripts/class_resolver.py ~/.claude/scripts/route_log.py ~/.claude/scripts/learning_baseline.py global/.claude/scripts/
cp ~/.claude/scripts/panel_review.py ~/.claude/scripts/panel_gate.py ~/.claude/scripts/panel_scoreboard.py global/.claude/scripts/  # tests in tests/test_panel_review.py, tests/test_panel_gate.py and tests/test_panel_scoreboard.py
cp ~/.claude/scripts/muse_poll.sh global/.claude/scripts/
cp ~/.claude/scripts/peer_poll.sh ~/.claude/scripts/peer_poll.check.prompt ~/.claude/scripts/peer_poll.handle.prompt ~/.claude/scripts/peer_poll_record.sh global/.claude/scripts/   # the second tower's notes poller; authored here, tests in tests/test_peer_poll.sh. Edit the repo copy, then install: cp global/.claude/scripts/peer_poll.sh global/.claude/scripts/peer_poll.check.prompt global/.claude/scripts/peer_poll.handle.prompt global/.claude/scripts/peer_poll_record.sh ~/.claude/scripts/. ~/.config/afa/show-windows.tsv (private) holds the show windows
cp ~/.claude/scripts/openclaw_status_digest.py ~/.claude/scripts/openclaw_status_refresh.sh ~/.claude/scripts/he_runner_watch.sh global/.claude/scripts/
cp ~/.claude/scripts/hub_digest.py ~/.claude/scripts/session_prime_facts.py global/.claude/scripts/
cp ~/.claude/scripts/session_to_md.py global/.claude/scripts/   # authored here; tests in tests/test_session_to_md.py. Edit the repo copy, then install: cp global/.claude/scripts/session_to_md.py ~/.claude/scripts/
cp ~/.claude/scripts/hub_inbox_archive.py global/.claude/scripts/   # authored here; tests in tests/test_hub_inbox.py. The board is ~/.claude/state/hub-inbox.md (muse-inbox.md is a symlink until ~28 Oct 2026). Each archive move is also logged (id, month file, date, no text) in hub-inbox-archive/archive.log
# notes_ledger.py (the board's ledger: a state word per open item from checked facts only, never a model; the stale view; one work-order block per item; the at-risk line; counts that never print line text) is authored here, reached through muse_poll_record.sh stale|counts|ledger|order|risk, tests in tests/test_notes_ledger.py (invented boards only). Edit the repo copy, then install with install_scripts.py (the one command in README.md), not by cp. It reads the board and never writes it
# docker_volume_backup.py (copies Docker's named volumes into ~/.cache/docker-volume-backups before any Docker Desktop restart our scripts make; wsl_root_check.py runs it, and install-wsl-root-check.sh copies it beside the root script) is authored here, tests in tests/test_docker_volume_backup.py. Edit the repo copy, then install with install_scripts.py, not by cp
cp ~/.claude/scripts/hub_lean.py global/.claude/scripts/
cp ~/.claude/scripts/graphify_hook_portable.py global/.claude/scripts/   # authored here (makes a repo's graphify hooks portable across machines); tests in tests/test_graphify_hook_portable.py
# alert_issues.py (Wordfence scan-findings email -> one GitHub issue per finding in the client's repo; reads mail with gmail_api.py, never prints mail text) is authored here, tests in tests/test_alert_issues.py and tests/test_muse_poll_alert_issues.sh. Edit the repo copy, then install: cp global/.claude/scripts/alert_issues.py global/.claude/scripts/muse_poll_client_check.sh ~/.claude/scripts/ and global/.claude/commands/prep.md ~/.claude/commands/
# coop_scrubber.py, coop_roster_build.py, coop_scrubber_presidio.py, coop_scrubber_install_presidio.sh and ~/.claude/hooks/coop-scrubber.py (co-op operations text gets people's names, emails and phone numbers swapped for placeholders; the roster, the folder patterns in ~/.config/afa/coop-scrubber.json and the Presidio venv are private and never come here) are authored here, tests in tests/test_coop_*.py. Edit the repo copy, then install with install_global.py
# secret_scrubber.py, shell_scrub_prefix.sh, shell_scrub_wrapper.py and ~/.claude/hooks/output-scrubber.py (secret values in what a tool printed are masked before a model reads them: the wrapper filters every Bash call at any exit code through CLAUDE_CODE_SHELL_PREFIX, and the one PostToolUse hook masks every other tool and then runs the co-op scrub; real values are read at run time from the machine's own dotenv files and never come here) are authored here, tests in tests/test_secret_scrubber.py, tests/test_shell_scrub_wrapper.py and tests/test_output_scrubber_hook.py (made-up values only). Edit the repo copy, then install the three scripts with install_scripts.py and the hook by hand: cp global/.claude/hooks/output-scrubber.py ~/.claude/hooks/. A person adds the env line and the two hook entries from global/dotfiles/settings.json (the env value is one bare absolute path with the home folder written out in place of __HOME__: no spaces, no $HOME, no ~, or no connector starts); output-scrubber.py takes coop-scrubber.py's place there, never beside it. Off switch: create ~/.claude/state/shell-scrub-off
cp ~/.claude/scripts/hours.py global/.claude/scripts/   # authored here (minute-level time log for the co-op and the internship; reads the private "hours" block of ~/.config/ledger/clients.json, never committed); tests in tests/test_hours.py. Edit the repo copy, then install: cp global/.claude/scripts/hours.py ~/.claude/scripts/
# install_scripts.py (installs global/.claude/scripts/ into ~/.claude/scripts/ from origin/main with a per-file summary, a confirmation, a backup and an undo.sh; skips no-AI-wall and settings files) is authored here, tests in tests/test_install_scripts.py. A person installs it with the one command in README.md ("fetch -q origin main && python3 -I <(git show origin/main:...install_scripts.py)"), not by cp, and a session never runs it against the live ~/.claude
cp ~/.claude/scripts/gmail_api.py global/.claude/scripts/   # authored here (Gmail drafts CLI, never sends); tests in tests/test_gmail_api.py. Edit the repo copy, then install: cp global/.claude/scripts/gmail_api.py ~/.claude/scripts/. Its client.json and token.json live in ~/.config/afa/gmail/ and never come here
cp ~/.claude/scripts/calendar_api.py global/.claude/scripts/   # authored here (read-only Google Calendar for the hours timer, GET only); tests in tests/test_calendar_api.py. Edit the repo copy, then install: cp global/.claude/scripts/calendar_api.py global/.claude/scripts/hours.py ~/.claude/scripts/. Its calendar-token.json lives in ~/.config/afa/gmail/ and never comes here
# extension_status_snapshot.py (the /extension-status snapshot and "what changed since last run" helper; saves to ~/.claude/state/extension-status/, never committed) is authored here, tests in tests/test_extension_status_snapshot.py. Edit the repo copy, then install: cp global/.claude/scripts/extension_status_snapshot.py ~/.claude/scripts/ and global/.claude/commands/extension-status.md ~/.claude/commands/
# extension_status_replies.py (the /extension-status --draft-replies helper: classifies outside issues and PRs and prints a suggested reply for each; prints only, never posts) is authored here, tests in tests/test_extension_status_replies.py. Edit the repo copy, then install: cp global/.claude/scripts/extension_status_replies.py ~/.claude/scripts/ and global/.claude/commands/extension-status.md ~/.claude/commands/
cp ~/.claude/scripts/demo_video.py ~/.claude/scripts/demo_video_record.cjs global/.claude/scripts/   # authored here (PR demo videos); tests in tests/test_demo_video.py, smoke tests/run_demo_video_smoke.sh. Edit the repo copy, then install: cp global/.claude/scripts/demo_video.py global/.claude/scripts/demo_video_record.cjs ~/.claude/scripts/
cp -a ~/.claude/skills/demo-video/. global/.claude/skills/demo-video/   # SKILL.md, spec.template.json, piper/Dockerfile; real specs carry client details and stay in the lane folder
# repo_rebuild.py (rebuild a GitHub repo under the same name with every issue and pull-request number kept, to drop objects GitHub still serves; seven phases, each with --dry-run; delete-old needs --yes-delete) is authored here, tests in tests/test_repo_rebuild.py. Edit the repo copy, then install: cp global/.claude/scripts/repo_rebuild.py ~/.claude/scripts/
cp ~/.claude/scripts/tab_record.py ~/.claude/scripts/tab_record_capture.cjs ~/.claude/scripts/gh_attach_clip.js global/.claude/scripts/   # authored here (read-only film of one browser tab, with a blurred copy; started by scout-run's rec-hook.js; gh_attach_clip.js is the step the hub runs to attach an approved clip through GitHub's own control: cut, check, approve, gh_attach_clip.js, paste the link); tests in tests/test_tab_record.py, smoke tests/run_tab_record_smoke.sh. Edit the repo copy, then install: cp global/.claude/scripts/tab_record.py global/.claude/scripts/tab_record_capture.cjs global/.claude/scripts/gh_attach_clip.js ~/.claude/scripts/
# internship_status.py (Jr Dev tracker; the ledger is private and never comes here) is authored here, tests in tests/test_internship_status.py. Edit the repo copy, then install: cp global/.claude/scripts/internship_status.py global/.claude/scripts/hub_digest.py ~/.claude/scripts/
# usage_calibrate.js and usage_calibrate_task.py (the weekly budget reader and its Windows task; the task runs a wscript launcher so the exit code survives, and the script writes ~/.config/ledger/usage-calibrate-status.json, which lane_watch.sh's check_calibrate reads) are authored here from now, tests in tests/test_usage_calibrate.js (node --test) and tests/test_usage_calibrate_task.py. Edit the repo copy, then install: cp global/.claude/scripts/usage_calibrate.js global/.claude/scripts/usage_calibrate_task.py global/.claude/scripts/lane_watch.sh global/.claude/scripts/hub_digest.py ~/.claude/scripts/ and re-register the task with python3 ~/.claude/scripts/usage_calibrate_task.py install
# lane_spend.py (what each lane and the hub itself cost, from the usage ledger; hub_digest.py's spend section reads its --json) is authored here, tests in tests/test_lane_spend.py and tests/test_hub_digest.py. Edit the repo copy, then install: cp global/.claude/scripts/lane_spend.py global/.claude/scripts/hub_digest.py ~/.claude/scripts/
# identity-leak-guard.py (PreToolUse hook: refuses Bash, WebFetch and WebSearch calls that would send the account owner's email address to a website; it reads the address at run time from a private file under ~/.config/afa and from Claude Code's own account record, and the address is never written here) is authored here, tests in global/.claude/hooks/tests/test_identity_leak_guard.py. Edit the repo copy, then install by hand: cp global/.claude/hooks/identity-leak-guard.py ~/.claude/hooks/ and add its PreToolUse entry from global/dotfiles/settings.json. The scripts installer does not read the hooks folder
# own-review-merge-guard.py (PreToolUse hook on Bash: no session merges, and none pushes to main, in a repo on the private own-review list ~/.config/afa/own-review-repos.txt, the repos where we are a guest on someone else's team; it refuses gh pr merge with any flag, a gh api or curl write to a merge address, a GraphQL merge in any repo, and a merge whose repo it cannot read; lane_merge.py refuses the same repos) is authored here, tests in global/.claude/hooks/tests/test_own_review_merge_guard.py (made-up names only). Edit the repo copy, then a person installs by hand, file first: cp global/.claude/hooks/own-review-merge-guard.py ~/.claude/hooks/ and only then add its PreToolUse entry from global/dotfiles/settings.json (with the entry in place and the file missing, every Bash call is refused). Each machine needs its own copy of the list, or every merge there is refused
# list-file-guard.py (PreToolUse hook on Bash and the edit tools: no session changes the two private merge lists, ~/.config/afa/merge-repos.txt and ~/.config/afa/own-review-repos.txt, or its own installed copy; it refuses an edit of those files however the path is spelled, a Bash command that names a list or reaches one through a folder change, wildcard, brace word or symlink, and a move, removal, link or permission change of the folders that hold them; a Bash check that runs past 3 seconds is refused, and the settings timeout must stay above that) is authored here, tests in global/.claude/hooks/tests/test_list_file_guard.py (temporary HOME and made-up names only). Edit the repo copy, then a person installs by hand, file first: cp global/.claude/hooks/list-file-guard.py ~/.claude/hooks/ and only then add its PreToolUse entry from global/dotfiles/settings.json. Once it is live a session can no longer copy over the installed file, so every later update is a person's step too
# ask-card-guard.py (PreToolUse hook on AskUserQuestion, the question card: one decision per card, "Hold, ask me again" as option 1, a pull request, issue or card named by number must be said in plain words with its full https address, and a card about a draft, sent mail or a merge is refused until a live lookup ran since the owner's last message; it reads the transcript through unverified-pending.py, so install both) and bare-reference.py (Stop hook: a reply that names a pull request, issue or card by number alone, or puts its address in a code block where the terminal will not make it clickable, is sent back once to be rewritten) are authored here, tests in global/.claude/hooks/tests/test_ask_card_guard.py and test_bare_reference.py (made-up text only). unverified-pending.py (Stop, context only) also flags a draft or pull request listed as waiting on the owner with no live lookup that turn; tests in test_unverified_pending.py. Edit the repo copy, then install by hand, files first: cp global/.claude/hooks/ask-card-guard.py global/.claude/hooks/bare-reference.py global/.claude/hooks/unverified-pending.py ~/.claude/hooks/ and only then a person adds the PreToolUse entry (matcher AskUserQuestion) and the Stop entry from global/dotfiles/settings.json. Off switches: ASK_CARD_GUARD=off, BARE_REFERENCE=off, UNVERIFIED_PENDING=off
# lane_secret_paths.py and lane_hide_secrets.sh (issue 332: every non-Claude lane starts behind lane_hide_secrets.sh, which scans the prompt and hand-off folder for real secret values and hides every settings file with a mount namespace; lane_secret_paths.py is the one shared list that agy_deny.py and opencode_lane.sh follow) are authored here, tests in tests/test_lane_secret_paths.py and tests/test_lane_secret_guard.sh. Edit the repo copy, then install: cp global/.claude/scripts/lane_secret_paths.py global/.claude/scripts/lane_hide_secrets.sh global/.claude/scripts/lane.sh global/.claude/scripts/agy_deny.py ~/.claude/scripts/
# tools_catalog.py (reads the header of every script and hook and builds ~/.claude/scripts/TOOLS.md, a generated catalog that never comes here; "match <prompt file>" picks the tools that fit one lane's prompt, and lane.sh appends them to a new lane's prompt.md; openclaw_status_refresh.sh rebuilds the catalog every 15 minutes) is authored here, tests in tests/test_tools_catalog.py and tests/test_lane_tools_section.sh. Edit the repo copy, then install: cp global/.claude/scripts/tools_catalog.py global/.claude/scripts/lane.sh global/.claude/scripts/openclaw_status_refresh.sh ~/.claude/scripts/
# lane_brief.py (one-screen status of a lane for the hub: report line 1 and verdict, context against the recycle line, pane alive, held dialog, last screen lines; read-only) is authored here, tests in tests/test_lane_brief.py. Edit the repo copy, then install: cp global/.claude/scripts/lane_brief.py ~/.claude/scripts/
cp ~/.claude/scripts/wsl_bridge_watchdog.py ~/.claude/scripts/lane_watch.sh ~/.claude/scripts/lane_turn_kind.py global/.claude/scripts/  # authored here, tests in tests/test_wsl_bridge_watchdog.py, test_lane_watch.sh and test_lane_turn_kind.py (lane.sh and opencode_lane.sh: test_lane_engines.sh and test_lane_recycle.sh; opencode_lane.sh and local_lane.sh are authored here, edit the repo copy then install them with lane.sh)
# openclaw_gateway.py and openclaw_gateway.sh (the one Docker-first way every OpenClaw script reaches the gateway; tests in tests/test_openclaw_gateway.py; install both with the scripts that use them), openclaw_harden.sh, openclaw_uid.sh, openclaw_pickup.py, openclaw_dispatch.py, openclaw_local.py, openclaw_frontdesk_cost.py, openclaw_handoff_install.sh, openclaw_handoff_hook/ and tests/{test_openclaw_harden.py,test_openclaw_handoff.py,test_openclaw_handoff_install.py,openclaw_fake_gemini.py,test_openclaw_uid_stage.sh,
# test_openclaw_pickup.py,test_openclaw_dispatch.py,test_openclaw_pass.py,test_openclaw_local.py,test_openclaw_frontdesk_cost.py} are authored here and run inside the OpenClaw gateway (Docker, or the OpenClawGateway distro where it still exists) or against its drop folder
# (not from ~/.claude): do not cp over them, and run them only from this repo.
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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
