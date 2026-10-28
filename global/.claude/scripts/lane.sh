#!/usr/bin/env bash
# Start a lane in its own tmux window. Claude Code is the default engine; Codex,
# GitHub Copilot, Google Antigravity (agy) and OpenCode (opencode, on free Gemini / OpenRouter keys) can be selected with --engine.
#
#   lane.sh [--dry-run] [--engine claude|codex|copilot|agy|opencode] [--effort <level>] [--connectors] [--allow-client] [--allow-low-tier] [--allow-high-tier "<reason>"] [--claude-because "<reason>"] [--console [--headless]] [--ask <id>] <name> <dir> <model> <prompt-file>
#   e.g. lane.sh prod-logs ~/repos/SpokeToWork sonnet ~/.claude/state/lanes/2026-10-11-prod-logs/prompt.md
#
# The hub (~/repos on NX-01) is the director of directors: it dispatches lanes and reads
# their reports, it does not do lane work itself. Each lane is a window in the detached
# tmux session "lanes", started in <dir>. Claude runs interactively; Codex and Copilot
# run headless and write output to out.log in the hand-off folder.
# Hand-off folder: ~/.claude/state/lanes/<YYYY-MM-DD>-<name>/ holds prompt.md and report.md
# (the lane writes report.md; the hub reads it).
#
# --console: the window runs console_worker.sh --inner <dir> --model <model> "<prompt>"
# instead of plain claude, so the capped Console worker key reaches only that process's
# environment (CONSOLE_WORKER=1 is set there too). Without --console nothing changes.
# claude.ai connectors (Gmail, Calendar) may not load under API-key auth, so email lanes
# stay on the subscription.
#
# --headless (only with --console): no interactive session; interactive Claude Code on Console
# credits gets a 400, but claude -p works. The window runs
#   console_worker.sh --inner <dir> -p --model <model> "<prompt>"
# with stdout and stderr going to out.log in the hand-off folder (beside report.md), and
# "exit <code>" appended there when the process ends. The index row says billing "console".
# --status shows such a lane as working while out.log has no exit line, then done / exit <code>.
# --resume refuses headless lanes (there is no interactive session to resume).
# --engine codex runs codex exec with workspace-write access to <dir> and the lane folder,
# plus network access (curl, docker ps and git over ssh all proven in the sandbox). Codex lanes are refused for client repositories unless --allow-client
# is passed; the guard prevents the broader sandbox access from being used there.
# --engine copilot runs copilot -p with all tools allowed. Both engines are client-guarded:
# lane.sh checks repo roots in ~/repos/hub/client-senders.tsv and paths in
# ~/repos/hub/client-repos.txt, as well as ~/repos/CD. If either list is missing,
# unreadable, or not a regular file, either external engine is refused. --allow-client bypasses the guard.
# The model argument is passed through to Codex/Copilot only when it is not a Claude model.
# --engine agy runs agy -p --mode accept-edits --sandbox (Google Antigravity, free quota). It is EDIT-ONLY:
# headless agy cannot run commands and --dangerously-skip-permissions is never used. The prompt copy gets a
# header telling the lane to write report.md with the commands the hub must run. Same client guard as above.
# The model is passed through when it is one of agy's own (gemini-*, gpt-oss-*, claude-*-4-6*); any other
# name (a Claude Code alias) becomes gemini-3.8-flash-high (see agy_model).
# --engine opencode runs opencode_lane.sh (beside this script): OpenCode, headless, edit files AND run commands, on a free API key read from
# ~/.config/gemini and ~/.config/openrouter at run time (never in the command line). Model: google/gemini-3-flash-preview, with
# openrouter/nvidia/nemotron-3-ultra-550b-a55b:free as the fallback when the first is out of quota; any other model name (a Claude alias) becomes the
# first. Both free tiers train on the input, so besides the client guard above it refuses ~/repos itself (which holds client repos). It works
# like Codex/Copilot: git, tests and the report are the lane's own. Order for his own-repo code lanes: agy, codex, copilot, opencode, then Claude Sonnet.
#
# 3D work tier (his rule, 19 Oct): a Claude lane whose prompt file names data/sites/ or site-model (3D fitting, photo
# matching, site models) starts or resumes only on fable or opus, each at effort high.
# sonnet, haiku or any other model name is refused, on start, --console, --headless and --resume alike. --allow-low-tier bypasses it.
#
# Mail tier (his rule, 21 Oct 7:15 PM: "why are you burning Fable on emails!"; one day of mail lanes on fable and opus was $85 of $303):
# client email is execution, so a Claude lane doing mail starts, resumes and switches on sonnet only. A mail lane is one whose name ends
# in -mail, whose prompt opens with the absorb-template header ("# Absorb lane:"), or whose prompt is about drafting or answering email.
# fable and opus are refused unless --allow-high-tier "<reason>" is given; the override is logged to
# ~/.claude/state/lanes/tier-overrides.log. Site-model prompts keep their own rule above.
#
# --ask <oc-id>: this lane answers an OpenClaw ask ("Jonathan via OpenClaw:", ask oc-xxxxxxxx). Once the window is up and the lane is in
# index.tsv, lane.sh runs `openclaw_dispatch.py claim <id> <name>`, which registers the lane so its finish, failure or stall is
# posted to Jonathan's OpenClaw chat, and cancels the five-minute fallback that would otherwise start the job a second time.
# A claim that fails prints a warning and does not undo the lane.
#
# Hand-off across hub sessions (lanes outlive the hub terminal; only WSL shutting down stops them):
#   lane.sh --status                    one line per window in "lanes" (report yes/no, working/idle/error,
#                                       last pane line, dir, model, engine/billing, session id) plus lanes with no window
#                                       and no report from the last 3 days. "report:yes" is not "done": a lane stays open
#                                       until its context is full (his rule, 19 Oct), so a report never means close the window.
#   lane.sh --send <name> <text>        type <text> into the window, Enter, confirm it was submitted
#   lane.sh --model <name> <model>      switch a running lane's model (types /model into it), then put back the USER default model that
#                                       /model saves (it moved the hub's own default to sonnet on 21 Oct); the index row follows. The
#                                       settings file is touched only through user_model.py, which prints just the model key.
#   lane.sh [--dry-run] [--console] --resume <name>
#                                       fresh window running claude --resume <session-id> --model <m> in <dir>
#   lane.sh [--dry-run] --recycle <name>
#                                       give a running lane a fresh context in its own window (his rule, 20 Oct: lanes never auto-compact):
#                                       the lane writes handoff.md in its hand-off folder, then gets /clear, then a message to read
#                                       prompt.md, then handoff.md, then carry on; a new index row follows its new session. A lane that
#                                       never writes handoff.md is not cleared. The request goes to the lane at once, even mid-turn (a long turn would never end first): it
#                                       finishes the step in hand and writes handoff.md as its next action. lane_watch.sh runs this when a
#                                       lane passes its own recycle line.
#   lane.sh --ctx <name>                the lane's context in K tokens (a bare number, ? if unknown)
#   lane.sh --recycle-point <name>      the lane's own recycle line in K tokens (a bare number): the value stored in its index row, else the
#                                       default for its kind of work. lane_watch.sh recycles each lane at its own line.
#   lane.sh --recycle-at <K> ...        at start or --resume: this lane's recycle line (40 to 150; up to 165 with connectors; default by kind and connectors: see default_recycle_k)
#   lane.sh --tier <name>               "<model> TAB <reason>": the model in the lane's row and the --allow-high-tier reason written there ("-" = none;
#                                       "site-model" for a site-model prompt). lane_watch.sh names a live lane on opus or fable with no reason.
#   lane.sh --turn <name>               what its last finished turn was: monitor (a Monitor progress event that asked nothing; lane_watch.sh does
#                                       not wake the hub for it), real, or ? (see lane_turn_kind.py)
# Every start appends one row to ~/.claude/state/lanes/index.tsv (tab-separated, no header):
#   date  name  dir  model  billing(console|subscription; codex|copilot|agy|opencode)  prompt  report  session-id  start-epoch  effort(- if none)  connectors(on|off|- )  recycle-at(K tokens; "-" = default for its kind)  high-tier reason("-" = none)  project-mcp(comma list of .mcp.json servers kept on; "-" = none)
# Rows from before the connectors column have nothing in it; they were started with connectors loaded and count as on.
# The session id is the one the lane's own SessionStart hook recorded (lane_session_hook.sh, ~/.claude/state/lanes/sessions/<name>), never
# a guess from file age; if it is not there yet at start, the next --status fills it in.
#
# High tier (his rule, 22 Oct 8 PM, after ten lanes ran on Opus all day and only one was entitled): opus and fable need a stated reason.
# --allow-high-tier "<reason>" at start, --resume or --model is written to column 13 of the index row and logged to tier-overrides.log; without it
# a plain opus or fable start is refused (site-model prompts and mail lanes have their own rules above). --recycle puts the lane back on sonnet
# right after the /clear unless it is a site-model lane or holds a reason; a mail lane is always sonnet.
#
# Effort and compaction (Claude lanes only, including --console, --headless and --resume):
# every Claude lane gets --effort <level> and a --settings JSON with an autoCompactWindow as a backstop (a lane is recycled with
# --recycle at its own line, so it should never reach it). The backstop follows the line: window_for gives min(200000, (line + 60K) / 0.84),
# because Claude Code compacts at about 0.84 x the window, not at the window (measured on 265 real auto compactions in lanes started
# with 200000: median 168K, lowest 165.5K). Default effort by model: sonnet, opus and
# fable high, haiku no flag (it takes no effort setting). --effort <level> overrides
# it (low, medium, high, xhigh, max); on --resume the level the lane started with is reused.
# The level is recorded in column 10 of the index; --status shows it with the lane's context size.
#
# A lane starts lean (Claude lanes only, every start and resume). Each lane re-reads everything it
# starts with on every turn, so the same --settings JSON also carries:
#   - the plugins in LANE_PLUGINS_OFF set to false (list below, near the top of the code);
#   - disableClaudeAiConnectors, so the claude.ai connectors (Calendly, Clay, Resend, PayPal ...)
#     are not loaded. They stay on when the lane name ends in -mail, when the prompt file mentions
#     Gmail, Calendar or Drive (or get_thread, create_draft, list_drafts), or with --connectors.
#     The choice is recorded in column 11 of the index, shown by --status and reused on --resume;
#   - --disallowedTools LANE_TOOLS_OFF (the Artifact tools: their schema is about a quarter of a lane's start);
#   - claudeMdExcludes for the workspace CLAUDE.md, replaced through --append-system-prompt-file by
#     the slim rule file LANE_RULES (default ~/repos/hub/lane-rules.md). Both happen only when that
#     file exists; a repo's own CLAUDE.md files load as usual. In the same case autoMemoryEnabled is false, so the
#     memory index (MEMORY.md, about 4K tokens) is not loaded: the rule file carries the rules;
#   - skillOverrides "off" for every skill and command not in LANE_SKILLS_KEEP, and syncClaudeAiSkills false so the
#     claude.ai-synced skills (anthropic-skills:*) are not loaded (the skill listing is about 14K tokens).
#
# The hub (claude started in ~/repos) gets the same lean launch from hub_lean.py through a project settings file, so it
# needs no flags. Every Claude lane start refreshes that file, so a machine that mostly runs lanes keeps it current.
#
# Folder trust: a new folder makes Claude Code ask "Yes, I trust this folder". For a folder
# under ~/repos this script answers it. Anywhere else the window waits for the user.
# The same poll also answers, by dialog text: with --console, the "use this API key?"
# first-run dialog (Yes); with any lane, the "Teach auto mode about your environment?"
# opt-in (Esc).
set -euo pipefail
SESSION=lanes

# Plugins switched off in every Claude lane (set to false through --settings enabledPlugins).
# Kept on: afa-mods (--status reads its band), security-guidance, code-review.
LANE_PLUGINS_OFF="
  creative-writing@grey-haven-plugins creative-writing-skills@creative-writing-skills
  deep-plan@piercelamb-plugins deep-project@piercelamb-plugins deep-implement@piercelamb-plugins
  superpowers@claude-plugins-official hookify@claude-plugins-official feature-dev@claude-plugins-official
  coderabbit@claude-plugins-official frontend-design@claude-plugins-official
  claude-code-setup@claude-plugins-official claude-md-management@claude-plugins-official
  supabase@claude-plugins-official
"
# Tools withheld from every Claude lane (--disallowedTools). The Artifact tool's schema alone is about a
# quarter of a lane's starting context, and the standing rule is that nothing is published to Artifacts.
LANE_TOOLS_OFF="Artifact ArtifactComments ArtifactData"

# Skills and slash commands a lane keeps in its start-up listing (~14K tokens for all of them). Every other skill under
# ~/.claude/skills and every command under ~/.claude/commands is set to "off" through --settings skillOverrides, worked out at
# each start so a newly added one is off by default. Picked from the skills named in lane prompts (prep, session-prime,
# code-review, goal, run, graphify, email-voice-check, demo-video ...) plus the spec-kit set the app repos' lanes run.
LANE_SKILLS_KEEP="
  prep session-prime goal code-review commit ship log graphify graphify-init graph-refresh email-voice-check demo-video
  site-model agent-notes greg council director weekly-review rescuedogs-weekly-review security-review
  specify clarify plan tasks implement analyze constitution
  speckit.analyze speckit.checklist speckit.clarify speckit.constitution speckit.implement speckit.plan speckit.specify
  speckit.tasks speckit.taskstoissues speckit.workflow
"
# The workspace CLAUDE.md lanes skip, and the slim rule file that stands in for it.
LANE_WORKSPACE_MD="${LANE_WORKSPACE_MD:-$HOME/repos/CLAUDE.md}"
LANE_RULES="${LANE_RULES:-$HOME/repos/hub/lane-rules.md}"

INDEX="$HOME/.claude/state/lanes/index.tsv"

# index.tsv is written by many lane.sh runs at once (lane_watch's --ctx/--status/--recycle polls, a lane start, --model). 26 Oct: the
# in-place `cat "$tmp" >"$INDEX"` truncated the file before writing it, a second run read the half-written prefix (364 of 982 rows) and
# wrote that back, and every lane after row 364 lost its record. Now every write takes one lock, and a rewrite goes to a temp file in the
# same folder and replaces the index with an atomic mv, so a reader sees the old file or the new one, never a part. A rewrite with fewer
# rows than the file it replaces is refused and logged (index-guard.log): no lane.sh edit ever removes a row.
INDEX_LOCK="${INDEX}.lock"
INDEX_GUARD_LOG="$(dirname "$INDEX")/index-guard.log"

# Run a command (a function in this script) holding the index lock; the lock is released when it returns.
index_locked() {  # <command> [args...]
  local fd rc
  mkdir -p "$(dirname "$INDEX")"
  exec {fd}>>"$INDEX_LOCK" || return 1
  if ! flock -w 60 "$fd"; then exec {fd}>&-; echo "lane.sh: could not lock $INDEX within 60 s" >&2; return 1; fi
  rc=0; "$@" || rc=$?
  exec {fd}>&-
  return "$rc"
}

# With the lock held: replace the index with <tmp-file> (made by index_tmp, so it sits beside the index), unless it has fewer rows.
index_commit() {  # <tmp-file>
  local new="$1" old_n new_n
  old_n="$(wc -l <"$INDEX" 2>/dev/null || echo 0)"; new_n="$(wc -l <"$new")"
  if [ "$new_n" -lt "$old_n" ]; then
    printf '%s\trefused a rewrite of index.tsv: %s rows would replace %s (caller %s)\n' "$(date -Is)" "$new_n" "$old_n" "${FUNCNAME[1]:-?}" >>"$INDEX_GUARD_LOG"
    echo "lane.sh: refused to shrink $INDEX from $old_n rows to $new_n; see $INDEX_GUARD_LOG" >&2
    rm -f "$new"; return 1
  fi
  chmod --reference="$INDEX" "$new" 2>/dev/null || chmod 644 "$new"
  mv -f "$new" "$INDEX"
}
index_tmp() { mkdir -p "$(dirname "$INDEX")"; mktemp "$INDEX.tmp.XXXXXX"; }

# Append one row (a tab-separated line) under the lock, so a rewrite in progress cannot drop it.
index_append_locked() { printf '%s\n' "$1" >>"$INDEX"; }
index_append() { index_locked index_append_locked "$1"; }
PROJ="$HOME/.claude/projects"
SESS_DIR="$HOME/.claude/state/lanes/sessions"   # lane_session_hook.sh writes one file per lane here: the proof of a lane's live session
TAB=$'\t'

# Every Claude lane gets AFA_LANE=1, which tells the afa-mods Blast Radius guard this is a lane and not the hub: a hold it can
# name the fix for (a relative rm after a cd, a docker run without limits) comes back to the lane as a refusal saying how to fix
# the command, instead of a "Run it?" dialog that idles the lane until the hub answers. A lane the OpenClaw dispatcher started
# (AFA_LANE_UNATTENDED=1, or a name starting oc-) has nobody at its window at all. It also gets AFA_LANE_UNATTENDED=1 and
# AFA_LANE_HAND=<its hand-off folder>: such a lane is never asked "Run it?", it is allowed a delete inside its own folder, a
# worktree or the scratchpad and refused the rest.
lane_env_prefix() {
  printf 'AFA_LANE=1 '
  if [ "${AFA_LANE_UNATTENDED:-}" = 1 ] || [[ "$1" == oc-* ]]; then
    printf 'AFA_LANE_UNATTENDED=1 AFA_LANE_HAND=%q ' "$2"
  fi
  printf 'AFA_LANE_NAME=%q ' "$1"   # lane_session_hook.sh records the lane's session against this name
}

usage() {
  cat <<'USAGE'
Usage:
  lane.sh [--dry-run] [--restart] [--engine claude|codex|copilot|agy|opencode] [--effort <level>] [--connectors|--no-connectors] [--recycle-at <K>] [--project-mcp <name[,name]>] [--allow-client] [--allow-low-tier] [--allow-high-tier "<reason>"] [--claude-because "<reason>"] [--console [--headless]] [--ask <oc-id>] <name> <dir> <model> <prompt-file>
  lane.sh --status
  lane.sh --send <name> <text>
  lane.sh [--dry-run] --model <name> <model>
  lane.sh [--dry-run] [--console] [--effort <level>] [--connectors] [--allow-low-tier] [--allow-high-tier "<reason>"] --resume <name>
  lane.sh [--dry-run] --recycle <name>
  lane.sh --ctx <name>
  lane.sh --recycle-point <name>
  (start and --resume also take --recycle-at <K> and --project-mcp <name[,name]>: project .mcp.json servers a lane keeps on; the rest stay off)

Claude is the default engine. Codex, Copilot, agy and opencode run headless and write to out.log.
Their model flag is used only when <model> is not a Claude name.
Codex, Copilot, agy and opencode refuse repositories listed in client-senders.tsv or client-repos.txt,
and folders under ~/repos/CD (opencode also refuses ~/repos itself: its free APIs train on input). client-repos.txt has one path per line. Blank lines
and lines starting with # are ignored. Both list files must be regular readable files; pass --allow-client to continue.
--headless is only for Claude with --console.
--ask <oc-id> claims an OpenClaw ask for this lane (its result then reaches Jonathan's OpenClaw chat).
Claude lanes default to --effort high on sonnet, opus and fable, none on haiku;
they are recycled at their own line (--recycle-at <K>, default by kind of work) and compact in place only as a backstop above it.
Claude lanes start without the claude.ai connectors, the workspace CLAUDE.md and a set of plugins.
--connectors keeps connectors; --no-connectors disables them even when the name or prompt would enable them.
A Claude lane whose prompt names data/sites/ or site-model (3D and photo-match work) runs only on fable or opus;
sonnet and haiku are refused there. --allow-low-tier overrides that.
A Claude lane doing mail (name ending -mail, an absorb-template prompt, or a prompt about drafting or answering email) runs on sonnet;
fable and opus are refused unless --allow-high-tier "<reason>" is given (logged to ~/.claude/state/lanes/tier-overrides.log).
A Claude lane in one of his own repos (not a client repo, not ~/repos itself) is refused unless --claude-because "<reason>" is given (logged to the same file):
his own-repo code runs on agy (--engine agy <name> <dir> gemini-3.8-flash-high <prompt-file>) until Codex returns on 31 Oct 2026; with agy out of usage, --engine opencode <name> <dir> sonnet <prompt-file>. Accepted reasons: needs Docker, needs wsl.exe,
needs a live service, agy out of usage, review or planning (strongest tier), not code. Mail lanes, site-model prompts and client repos never need one.
opus and fable need --allow-high-tier "<reason>" (written to the lane's index row, shown by --tier); --recycle returns a lane without a reason to sonnet.
--tier <name> prints the lane's model and that reason.
--model <name> <model> switches a running lane and puts back the user default model that /model would otherwise change.
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
dry=0; console=0; headless=0; allow_client=0; engine=claude; mode=start; effort_opt=; connectors_opt=0; no_connectors=0; ask_id=; allow_low_tier=0; allow_high_reason=; claude_because=; recycle_at_opt=; recycle_at_set=0; project_mcp_opt=; restart_opt=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --restart) restart_opt=1; shift ;;
    --help|-h) usage; exit 0 ;;
    --engine)
      [ $# -ge 2 ] || { echo "--engine requires claude, codex, copilot, agy, or opencode" >&2; exit 1; }
      engine="$2"; shift 2 ;;
    --engine=*) engine="${1#*=}"; shift ;;
    --effort)
      [ $# -ge 2 ] || { echo "--effort requires low, medium, high, xhigh, or max" >&2; exit 1; }
      effort_opt="$2"; shift 2 ;;
    --effort=*) effort_opt="${1#*=}"; shift ;;
    --recycle-at)
      [ $# -ge 2 ] || { echo "--recycle-at requires a number of K tokens (40 to 150)" >&2; exit 1; }
      recycle_at_opt="$2"; recycle_at_set=1; shift 2 ;;
    --recycle-at=*) recycle_at_opt="${1#*=}"; recycle_at_set=1; shift ;;
    --project-mcp)
      [ $# -ge 2 ] || { echo "--project-mcp requires a server name, or a comma list of them" >&2; exit 1; }
      project_mcp_opt="$2"; shift 2 ;;
    --project-mcp=*) project_mcp_opt="${1#*=}"; shift ;;
    --ask)
      [ $# -ge 2 ] || { echo "--ask requires an ask id (oc-xxxxxxxx)" >&2; exit 1; }
      ask_id="$2"; shift 2 ;;
    --ask=*) ask_id="${1#*=}"; shift ;;
    --allow-client) allow_client=1; shift ;;
    --allow-low-tier) allow_low_tier=1; shift ;;
    --allow-high-tier)
      [ $# -ge 2 ] && [ -n "${2// /}" ] || { echo "--allow-high-tier needs a reason in words, e.g. --allow-high-tier \"a claim-check pass\"" >&2; exit 1; }
      allow_high_reason="$2"; shift 2 ;;
    --allow-high-tier=*)
      [ -n "${1#*=}" ] || { echo "--allow-high-tier needs a reason in words" >&2; exit 1; }
      allow_high_reason="${1#*=}"; shift ;;
    --claude-because)
      [ $# -ge 2 ] && [ -n "${2// /}" ] || { echo "--claude-because needs a reason in words, e.g. --claude-because \"needs Docker\"" >&2; exit 1; }
      claude_because="$2"; shift 2 ;;
    --claude-because=*)
      [ -n "${1#*=}" ] || { echo "--claude-because needs a reason in words" >&2; exit 1; }
      claude_because="${1#*=}"; shift ;;
    --connectors) connectors_opt=1; no_connectors=0; shift ;;
    --no-connectors) no_connectors=1; connectors_opt=0; shift ;;
    --console) console=1; shift ;;
    --headless) headless=1; shift ;;
    --status) mode=status; shift ;;
    --send) mode=send; shift ;;
    --model) mode=model; shift ;;
    --recycle) mode=recycle; shift ;;
    --ctx) mode=ctx; shift ;;
    --recycle-point) mode=point; shift ;;
    --turn) mode=turn; shift ;;
    --tier) mode=tier; shift ;;
    --resume) mode=resume; shift ;;
    *) break ;;
  esac
done

case "$engine" in
  claude|codex|copilot|agy|opencode) ;;
  *) echo "unknown engine: $engine (choose claude, codex, copilot, agy, or opencode)" >&2; exit 1 ;;
esac

# An engine whose program this machine does not have is refused up front, with the machine named, instead of a window that opens and dies
# on "command not found" (28 Oct 2026: the tower has no agy or OpenCode, so the dispatcher's agy-first order started lanes that died at once).
# AFA_ENGINES_AVAILABLE (a comma list of engine names, or *) says an engine is present when this shell's PATH cannot show it.
engine_present() {  # <engine>
  case ",${AFA_ENGINES_AVAILABLE:-}," in *,"*",*|*,"$1",*) return 0 ;; esac
  command -v "$1" >/dev/null 2>&1
}
if [ "$mode" = start ] && ! engine_present "$engine"; then
  echo "refusing engine $engine: the program '$engine' is not installed on this machine ($(cat "$HOME/.config/afa/machine" 2>/dev/null || hostname)); pick another engine, or install it" >&2
  exit 1
fi

case "$effort_opt" in
  ''|low|medium|high|xhigh|max) ;;
  *) echo "unknown effort: $effort_opt (choose low, medium, high, xhigh, or max)" >&2; exit 1 ;;
esac

if [ "$recycle_at_set" = 1 ]; then
  # A connector lane starts near 77K, and with large tool lists far higher (27 Oct: 152K six minutes after a re-prime), so its line may
  # go to LANE_RECYCLE_MAX_CONN_K (165, just under where Claude Code compacts, about 168K); the 150 ceiling holds for the rest.
  # A resume's connectors come from its index row, so it is checked again once they are known.
  rk_max=150; { [ "$connectors_opt" = 1 ] || [ "$mode" = resume ]; } && rk_max=${LANE_RECYCLE_MAX_CONN_K:-165}
  [[ "$recycle_at_opt" =~ ^[0-9]+$ ]] && [ "$recycle_at_opt" -ge 40 ] && [ "$recycle_at_opt" -le "$rk_max" ] \
    || { echo "--recycle-at needs a whole number of K tokens from 40 to 150 (up to ${LANE_RECYCLE_MAX_CONN_K:-165} with --connectors; Claude Code compacts at about 168K), got: $recycle_at_opt" >&2; exit 1; }
  case "$mode" in start|resume) ;; *) echo "--recycle-at only applies when starting or resuming a lane" >&2; exit 1 ;; esac
fi
if [ -n "$project_mcp_opt" ]; then
  [[ "$project_mcp_opt" =~ ^[A-Za-z0-9_.-]+(,[A-Za-z0-9_.-]+)*$ ]] \
    || { echo "--project-mcp needs server names of letters, digits, _ . - separated by commas, got: $project_mcp_opt" >&2; exit 1; }
  case "$mode" in start|resume) ;; *) echo "--project-mcp only applies when starting or resuming a lane" >&2; exit 1 ;; esac
fi
[ -z "$ask_id" ] || [[ "$ask_id" =~ ^oc-[0-9a-f]{8}$ ]] || { echo "--ask needs an id like oc-1a2b3c4d, got: $ask_id" >&2; exit 1; }
[ -z "$ask_id" ] || [ "$mode" = start ] || { echo "--ask only applies when starting a lane" >&2; exit 1; }
[ "$headless" = 0 ] || [ "$console" = 1 ] || { echo "--headless is only valid with --console" >&2; exit 1; }
[ "$headless" = 0 ] || [ "$mode" = start ] || { echo "--headless only applies when starting a lane" >&2; exit 1; }

# Run a tmux command, or just print it under --dry-run.
t() { if [ "$dry" = 1 ]; then printf 'tmux'; printf ' %q' "$@"; echo; else tmux "$@"; fi; }

# A lane's session is never guessed from file age: a folder like ~/repos also holds the hub's sessions and a person's own, and the newest
# unclaimed transcript there is not the lane's (23 Oct: mac-mini-prep was handed the hub director's session). The proof is the lane's own
# process: lane_session_hook.sh (a SessionStart hook in every lane's settings) records the session id against AFA_LANE_NAME, and the pane it
# ran in. proven_session prints that id when it was recorded at/after <start-epoch>, its pane (when tmux can say) is a window of that
# name, and no OTHER lane's row claims it.
proven_session() {  # <name> <start-epoch>
  local name="$1" start="${2:-0}" f id ts pane win
  [[ "$start" =~ ^[0-9]+$ ]] || start=0
  f="$SESS_DIR/$name"; [ -s "$f" ] || return 0
  IFS="$TAB" read -r id ts pane < <(tail -n 1 "$f")
  [[ "$ts" =~ ^[0-9]+$ ]] && [ "$ts" -ge $((start - 2)) ] || return 0
  if [[ "${pane:-}" == %* ]]; then
    win="$(tmux display -p -t "$pane" '#{window_name}' 2>/dev/null || true)"
    [ -z "$win" ] || [ "$win" = "$name" ] || return 0
  fi
  if [ -s "$INDEX" ] && awk -F'\t' -v n="$name" -v i="$id" '$2!=n && $8==i{f=1} END{exit !f}' "$INDEX"; then return 0; fi
  echo "$id"
}

# For a lane whose process predates the hook (it has no record): the transcript, born at/after <start-epoch>, that holds the message
# lane.sh itself typed into that window. The re-prime names the lane's own hand-off folder, so no other session contains it.
session_with_text() {  # <dir> <start-epoch> <text>
  local slug f b
  slug="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$1")"
  [ -d "$PROJ/$slug" ] || return 0
  while IFS= read -r f; do
    b="$(stat -c %W "$f")"; [ "$b" -gt 0 ] || b="$(stat -c %Y "$f")"
    [ "$b" -ge $(( $2 - 2 )) ] || continue
    if grep -qF -- "$3" "$f" 2>/dev/null; then basename "$f" .jsonl; return 0; fi
  done < <(ls -t "$PROJ/$slug"/*.jsonl 2>/dev/null)
}

# Fill blank session ids in the index (column 8) where a transcript can be found.
fill_ids_locked() {
  local tmp d n dir m b p r id st eff conn rk ent pm
  tmp="$(index_tmp)"
  while IFS="$TAB" read -r d n dir m b p r id st eff conn rk ent pm; do
    if { [ -z "$id" ] || [ "$id" = - ]; } && [ "${st:--}" != - ]; then
      id="$(proven_session "$n" "$st")"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$d" "$n" "$dir" "$m" "$b" "$p" "$r" "${id:--}" "${st:--}" "${eff:-?}" "${conn:--}" "${rk:--}" "${ent:--}" "${pm:--}" >>"$tmp"
  done <"$INDEX"
  index_commit "$tmp"
}
fill_ids() {
  [ -s "$INDEX" ] || return 0
  index_locked fill_ids_locked || true
}

# Latest index row for a lane name (tab-separated line), or nothing.
index_row() { [ -s "$INDEX" ] && awk -F'\t' -v n="$1" '$2==n{r=$0} END{if(r)print r}' "$INDEX" || true; }

# Where a lane's index row can go stale: a /clear (typed by the hub or the lane itself) or a --resume starts a new transcript and nothing
# rewrites column 8 (22 Oct: harmonicegg-mail's row said da6e22ef while the live session was 00e024a9, so --ctx read 22K at 157K and
# lane_watch never recycled it). follow_session prints the lane's live session id: the one proven_session reports from the lane's own
# SessionStart hook, if its transcript exists; otherwise the row's id. A change is written back to the lane's last row. Never inferred
# from file age (23 Oct: that handed a lane in ~/repos the hub's own session). A lane started before the hook existed has no record and
# keeps its row's id until it is next recycled (lane.sh --recycle finds the new session by the message it typed).
follow_session_locked() {  # <name> <session-id>: point the lane's last row at the session
  local tmp; tmp="$(index_tmp)"
  awk -F'\t' -v OFS='\t' -v n="$1" -v id="$2" '{rows[NR]=$0; if ($2==n) last=NR} END{for (i=1;i<=NR;i++) { if (i==last) { k=split(rows[i], f, "\t"); f[8]=id; line=f[1]; for (j=2;j<=k;j++) line=line OFS f[j]; print line } else print rows[i] }}' "$INDEX" >"$tmp" \
    && index_commit "$tmp" || rm -f "$tmp"
}
follow_session() {  # <name> <dir> <session-id> <start-epoch>
  local name="$1" dir="$2" id="$3" start="${4:-0}" slug best
  { [ -n "$id" ] && [ "$id" != - ] && [ "$id" != "?" ] && [ -s "$INDEX" ]; } || { echo "$id"; return 0; }
  slug="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$dir")"
  best="$(proven_session "$name" "$start")"
  if [ -z "$best" ] || [ "$best" = "$id" ] || [ ! -e "$PROJ/$slug/$best.jsonl" ]; then echo "$id"; return 0; fi
  index_locked follow_session_locked "$name" "$best" || true
  echo "$best"
}

# Set column <col> of the lane's last index row, padding a short row out to 12 fields with "-" (an empty field would collapse in `read`).
index_set_locked() {
  local tmp; tmp="$(index_tmp)"
  awk -F'\t' -v OFS='\t' -v n="$1" -v c="$2" -v v="$3" '{rows[NR]=$0; if ($2==n) last=NR} END{for (i=1;i<=NR;i++) { if (i==last) { k=split(rows[i], f, "\t"); for (j=k+1;j<c;j++) f[j]="-"; f[c]=v; if (k<c) k=c; line=f[1]; for (j=2;j<=k;j++) line=line OFS f[j]; print line } else print rows[i] }}' "$INDEX" >"$tmp" \
    && index_commit "$tmp" || rm -f "$tmp"
}
index_set() { index_locked index_set_locked "$@"; }  # <name> <col> <value>

# Expand only the current user's ~ forms; never eval data from the repository lists.
# Match the literal ~/ prefix from list data.
# shellcheck disable=SC2088
expand_home_path() {
  case "$1" in
    '~') printf '%s\n' "$HOME" ;;
    '~/'*) printf '%s/%s\n' "$HOME" "${1#\~/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

canonical_dir() { [ -d "$1" ] && (cd -- "$1" && pwd -P); }

# Match a path equal to, or nested under, a protected repository root.
path_within() {
  if [ "$2" = / ]; then
    case "$1" in /*) return 0 ;; *) return 1 ;; esac
  fi
  case "$1" in "$2"|"$2/"*) return 0 ;; *) return 1 ;; esac
}

check_external_target_path() {
  local candidate="$1" sender_paths="$2" repo_paths="$3" protected root

  root="$(canonical_dir "$HOME/repos/CD" 2>/dev/null || true)"
  if [ -n "$root" ] && path_within "$candidate" "$root"; then
    echo "refusing engine $engine: target is under the protected ~/repos/CD tree; pass --allow-client to continue" >&2
    return 1
  fi

  while IFS= read -r protected || [ -n "$protected" ]; do
    [ -n "$protected" ] || continue
    protected="$(expand_home_path "$protected")"
    root="$(canonical_dir "$protected" 2>/dev/null || true)"
    if [ -n "$root" ] && path_within "$candidate" "$root"; then
      echo "refusing engine $engine: target is a protected client repository; pass --allow-client to continue" >&2
      return 1
    fi
  done <<<"$sender_paths"

  while IFS= read -r protected || [ -n "$protected" ]; do
    case "$protected" in
      ''|'#'*) continue ;;
    esac
    protected="$(expand_home_path "$protected")"
    root="$(canonical_dir "$protected" 2>/dev/null || true)"
    if [ -n "$root" ] && path_within "$candidate" "$root"; then
      echo "refusing engine $engine: target is a protected client repository; pass --allow-client to continue" >&2
      return 1
    fi
  done <<<"$repo_paths"
}

guard_external_engine() {
  local client_file="$HOME/repos/hub/client-senders.tsv"
  local repos_file="$HOME/repos/hub/client-repos.txt"
  local sender_rows sender_paths repo_paths in_worktree common_dir checkout_path checkout_root
  [ "$engine" = claude ] && return 0
  [ "$allow_client" = 1 ] && return 0
  if [ "$engine" = opencode ]; then  # ~/repos itself, or any folder above it ($HOME, /), would put the client repos inside the lane's checkout
    local oc_dir oc_repos
    oc_dir="$(canonical_dir "$dir" 2>/dev/null || true)"
    oc_repos="$(canonical_dir "$HOME/repos" 2>/dev/null || echo /nonexistent)"
    if [ -z "$oc_dir" ] || path_within "$oc_repos" "$oc_dir"; then
      echo "refusing engine $engine: $dir is ~/repos or a folder above it, which holds client repositories, and opencode's free APIs train on their input; work in one sub-repo, or pass --allow-client to continue" >&2
      return 1
    fi
  fi
  if [ ! -f "$client_file" ] || [ ! -r "$client_file" ]; then
    echo "refusing engine $engine: cannot check client repositories because client-senders.tsv is missing or unreadable, or is not a regular file; pass --allow-client to continue" >&2
    return 1
  fi
  if [ ! -f "$repos_file" ] || [ ! -r "$repos_file" ]; then
    echo "refusing engine $engine: cannot check client repositories because client-repos.txt is missing or unreadable, or is not a regular file; pass --allow-client to continue" >&2
    return 1
  fi

  if ! sender_rows="$(cat -- "$client_file" 2>/dev/null)"; then
    echo "refusing engine $engine: cannot read client-senders.tsv; pass --allow-client to continue" >&2
    return 1
  fi
  if ! repo_paths="$(cat -- "$repos_file" 2>/dev/null)"; then
    echo "refusing engine $engine: cannot read client-repos.txt; pass --allow-client to continue" >&2
    return 1
  fi
  if ! sender_paths="$(awk -F '\t' '$1 !~ /^#/ && $2 != "" && !($1 == "id" && $2 == "repo_dir") {print $2}' <<<"$sender_rows")"; then
    echo "refusing engine $engine: cannot read client-senders.tsv; pass --allow-client to continue" >&2
    return 1
  fi

  check_external_target_path "$dir" "$sender_paths" "$repo_paths" || return 1

  # A worktree may live outside the protected checkout, so also check its common
  # Git directory's parent when Git identifies the usual <checkout>/.git layout.
  command -v git >/dev/null 2>&1 || return 0
  if ! in_worktree="$(git -C "$dir" rev-parse --is-inside-work-tree 2>/dev/null)" \
     || [ "$in_worktree" != true ]; then
    return 0
  fi
  if ! common_dir="$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" \
     || [ -z "$common_dir" ]; then
    echo "refusing engine $engine: cannot resolve the repository checkout for target; pass --allow-client to continue" >&2
    return 1
  fi

  while [ "$common_dir" != / ] && [ "${common_dir%/}" != "$common_dir" ]; do
    common_dir="${common_dir%/}"
  done
  case "$common_dir" in
    */.git)
      checkout_path="${common_dir%/.git}"
      [ -n "$checkout_path" ] || checkout_path=/
      checkout_root="$(canonical_dir "$checkout_path" 2>/dev/null || true)"
      if [ -z "$checkout_root" ]; then
        echo "refusing engine $engine: cannot resolve the repository checkout for target; pass --allow-client to continue" >&2
        return 1
      fi
      check_external_target_path "$checkout_root" "$sender_paths" "$repo_paths" || return 1
      ;;
  esac
  return 0
}

# agy's own model names pass through; a Claude Code alias or anything unknown becomes the free default.
agy_model() {
  case "$1" in
    gemini-*|gpt-oss-*|claude-*-4-6*) printf '%s' "$1" ;;
    *) printf 'gemini-3.8-flash-high' ;;
  esac
}

# OpenCode's own provider/model names pass through when they are on a provider whose free key opencode_lane.sh loads; anything else (a Claude alias) is the free default.
opencode_model() {
  case "$1" in
    google/*|openrouter/*) printf '%s' "$1" ;;
    *) printf 'google/gemini-3-flash-preview' ;;
  esac
}

is_claude_model() {
  case "$1" in
    opus|sonnet|haiku|fable|claude-*) return 0 ;;
    *) return 1 ;;
  esac
}

# 3D fitting, photo matching and site-model work is judgment work, never execution (his rule, 19 Oct: "using the lower
# models isn't a savings if it's not capable of doing the job"). A prompt that names data/sites/ or site-model runs on
# fable or opus only; anything else (sonnet, haiku, an unnamed default) is refused unless --allow-low-tier is passed.
site_tier_ok() {  # <model> <prompt-file>
  [ "$allow_low_tier" = 1 ] && return 0
  grep -qE 'data/sites/|site-model' -- "$2" 2>/dev/null || return 0
  case "${1,,}" in *opus*|*fable*) return 0 ;; esac
  echo "refusing model ${1:-(none)}: the prompt names data/sites/ or site-model, and 3D fitting, photo matching and site-model work runs only on fable or opus, each at effort high; never Sonnet or Haiku (rule: ~/repos/hub/lane-rules.md, 19 Oct). Pass --allow-low-tier to override." >&2
  return 1
}

# Mail is execution, never judgment (his rule, 21 Oct 7:15 PM: "why are you burning Fable on emails!"; "over 300 dollars on one fucking
# email"): the co-op mail lane redrafted one reply six times on fable and opus for $85 of a $303 day. A lane whose name ends in -mail,
# whose prompt opens with the absorb-template header, or whose prompt is about drafting or answering email runs on sonnet; fable and opus
# are refused unless --allow-high-tier "<reason>" is given, and the override is logged. A site-model prompt keeps its own rule (fable or opus).
LANE_TIER_LOG="${LANE_TIER_LOG:-$HOME/.claude/state/lanes/tier-overrides.log}"
mail_lane() {  # <name> <prompt-file>
  [[ "$1" == *-mail ]] && return 0
  [ -n "${2:-}" ] && [ -r "$2" ] || return 1
  grep -qiE '^# *absorb lane:' -- "$2" 2>/dev/null && return 0
  grep -qiE '\b(draft|redraft|answer|reply|respond|proof-?[[:space:]]?read|polish)[a-z]*\b[^.?!]{0,60}\be-?mails?\b|\be-?mails?\b[^.?!]{0,30}\b(draft|reply|replies|thread)\b' -- "$2" 2>/dev/null
}
mail_tier_ok() {  # <model> <name> <prompt-file> [verb]
  case "${1,,}" in *opus*|*fable*) ;; *) return 0 ;; esac
  mail_lane "$2" "${3:-}" || return 0
  grep -qE 'data/sites/|site-model' -- "${3:-/nonexistent}" 2>/dev/null && return 0
  if [ -n "$allow_high_reason" ]; then
    if [ "$dry" = 1 ]; then echo "# would log the mail-tier override for $2 ($1) to $LANE_TIER_LOG"
    else
      mkdir -p "$(dirname "$LANE_TIER_LOG")"
      printf '%s\t%s\t%s\tmail\t%s\n' "$(date -Is)" "$2" "$1" "$(one_line "$allow_high_reason")" >>"$LANE_TIER_LOG"
    fi
    return 0
  fi
  echo "refusing mail model $1 for $2: email work (drafting, answering, absorbing client mail) is execution and runs on sonnet, never fable or opus (his rule, 21 Oct 7:15 PM). Use sonnet, or pass --allow-high-tier \"<reason>\" (logged to $LANE_TIER_LOG)." >&2
  return 1
}

# Opus and fable cost several times sonnet, and a lane that stays on one all day is what ran up the bill (his rule, 22 Oct: ten lanes on Opus,
# only tigard-fable entitled; coop-mail on Fable). So a Claude lane may run on opus or fable only with a reason WRITTEN DOWN: the
# --allow-high-tier "<reason>" given at start, resume or --model goes into index.tsv column 13 (ent, "-" = none). Site-model prompts need none
# (their own rule above) and mail lanes are settled by mail_tier_ok. A recycle puts a lane without a reason back on sonnet (policy_model),
# and lane_watch.sh names any live lane on opus or fable with no reason (lane.sh --tier prints "<model> TAB <reason>").
one_line() { printf '%s' "$1" | tr '\t\n' '  '; }
is_high_model() { case "${1,,}" in *opus*|*fable*) return 0 ;; *) return 1 ;; esac; }
site_prompt() { grep -qE 'data/sites/|site-model' -- "${1:-/nonexistent}" 2>/dev/null; }
high_tier_ok() {  # <model> <name> <prompt-file> [recorded reason]
  is_high_model "$1" || return 0
  [ -z "${4:-}" ] || [ "${4:-}" = - ] || return 0
  site_prompt "${3:-}" && return 0
  mail_lane "$2" "${3:-}" && return 0
  if [ -n "$allow_high_reason" ]; then
    if [ "$dry" = 1 ]; then echo "# would log the high-tier reason for $2 ($1) to $LANE_TIER_LOG"
    else
      mkdir -p "$(dirname "$LANE_TIER_LOG")"
      printf '%s\t%s\t%s\thigh\t%s\n' "$(date -Is)" "$2" "$1" "$(one_line "$allow_high_reason")" >>"$LANE_TIER_LOG"
    fi
    return 0
  fi
  echo "refusing model $1 for $2: opus and fable need a stated reason (his rule, 22 Oct: ten lanes ran on Opus all day with no entitlement). Use sonnet, or pass --allow-high-tier \"<reason>\"; the reason is written to the lane's index row and logged to $LANE_TIER_LOG." >&2
  return 1
}
# The reason an index row carries for a start: the stated one, or the site-model rule, else "-".
high_ent() {  # <model> <prompt-file>
  is_high_model "$1" || { echo -; return 0; }
  if [ -n "$allow_high_reason" ]; then one_line "$allow_high_reason"
  elif site_prompt "${2:-}"; then echo "site-model prompt (fable or opus only)"
  else echo -; fi
}
# The model a lane goes back to after a recycle: its own when it is not high, when its prompt is site-model work, or when it holds a reason;
# sonnet otherwise (a mail lane is always sonnet).
policy_model() {  # <model> <name> <prompt-file> <recorded reason>
  is_high_model "$1" || { echo "$1"; return 0; }
  site_prompt "${3:-}" && { echo "$1"; return 0; }
  mail_lane "$2" "${3:-}" && { echo sonnet; return 0; }
  [ -z "${4:-}" ] || [ "${4:-}" = - ] || { echo "$1"; return 0; }
  echo sonnet
}

# Default effort for a Claude model: high (sonnet, opus, fable); haiku none (prints nothing).
default_effort() {
  case "$1" in
    *haiku*) ;;
    *) echo high ;;
  esac
}

# Effort for a lane: haiku never takes one; else the override, else the recorded level, else the model default.
lane_effort() {  # <model> [recorded]
  case "$1" in *haiku*) return 0 ;; esac
  if [ -n "$effort_opt" ]; then echo "$effort_opt"
  elif case "${2:-}" in low|medium|high|xhigh|max) true ;; *) false ;; esac; then echo "$2"
  else default_effort "$1"; fi
}

# Whether a new lane keeps the claude.ai connectors: --connectors, a -mail name, or a prompt that mentions
# Gmail, Calendar or Drive (or their tool names), unless --no-connectors overrides them. Prints on or off.
lane_connectors() {  # <name> <prompt-file>
  if [ "$no_connectors" = 1 ]; then
    echo off
  elif [ "$connectors_opt" = 1 ] || [[ "$1" == *-mail ]] \
     || grep -qiE '\b(gmail|calendar|drive)\b|get_thread|create_draft|list_drafts' -- "$2" 2>/dev/null; then
    echo on
  else echo off; fi
}

# The recycle line and the backstop, per lane (Jonathan, 21 Oct: "this assumes all lanes should be hard coded to the same context?").
# Measured on 427 lane transcripts (lane-recycle report, 21 Oct): a median lane primes at 42-57K; one turn of a busy lane adds a median
# 18-75K and a p90 of 100-130K tokens, so a line that waits for the turn to end is overrun; and Claude Code's autoCompactWindow W fires at
# about 0.84 x W (265 real auto compactions, median 168K, lowest 165.5K), not at W. Hence: the line fires mid-turn, and the backstop sits
# LANE_BACKSTOP_ROOM_K (60) above the line where the model's window allows. Kinds differ little in cost (flat 80-100K, plateau to 160K);
# a cycle must give at least 50K of working room above its priming, which puts the ops default (primes at 57K) 10K higher.
lane_kind() {  # <prompt-file> <dir> <connectors on|off|-> -> 3d | mail | ops | code
  local t; t="$(head -c 6000 -- "$1" 2>/dev/null | tr 'A-Z' 'a-z')$(tr 'A-Z' 'a-z' <<<"$2")"
  if command grep -qE 'data/sites|site-model|sketchup|photogrammetry|blender' <<<"$t"; then echo 3d
  elif [ "${3:--}" = on ] || command grep -qE 'gmail|inbox|email|absorb|\bmail\b' <<<"$t"; then echo mail
  elif command grep -qE 'portainer|traefik|nextcloud|docker compose|deploy|watchdog|cron|systemd|\bops\b' <<<"$t"; then echo ops
  else echo code; fi
}
# A lane with the claude.ai connectors loaded starts near 77K tokens (their tool list alone; Job 26 Oct), so a
# 100K line left it 23K of room and it recycled every few turns. Its line is its start load plus the same 50K of working room as above.
LANE_CONNECTOR_START_K=${LANE_CONNECTOR_START_K:-77}
LANE_WORKING_ROOM_K=${LANE_WORKING_ROOM_K:-50}
default_recycle_k() {  # <kind> [connectors on|off|-] -> K tokens
  local k floor=$(( LANE_CONNECTOR_START_K + LANE_WORKING_ROOM_K ))
  case "$1" in
    ops) k=110 ;;
    *) k=100 ;;
  esac
  if [ "${2:--}" = on ] && [ "$k" -lt "$floor" ]; then k=$floor; fi
  echo "$k"
}
lane_recycle_k() {  # <stored value or empty> <prompt-file> <dir> <connectors> -> K tokens
  if [[ "${1:-}" =~ ^[0-9]+$ ]]; then echo "$1"; else default_recycle_k "$(lane_kind "${2:-}" "${3:-}" "${4:--}")" "${4:--}"; fi
}
window_for() {  # <recycle K> -> autoCompactWindow, capped at LANE_WINDOW_CAP (200000: the window of every tier a lane runs on)
  local cap=${LANE_WINDOW_CAP:-200000} w
  w=$(( ((${1:-100} + ${LANE_BACKSTOP_ROOM_K:-60}) * 100000 / 84 + 9999) / 10000 * 10000 ))
  [ "$w" -le "$cap" ] || w=$cap
  echo "$w"
}

# Flags every Claude lane gets: effort (when the model takes one), one --settings JSON (compaction backstop from window_for,
# plugins off, connectors off unless <connectors> is on, the workspace CLAUDE.md excluded) and the slim rules.
# The workspace CLAUDE.md is excluded only when the rule file exists to replace it.
# Project MCP servers (a .mcp.json in the lane's folder or above it) are off for lanes. Claude Code asks about each new one with a
# "New MCP server found" dialog that nobody is there to answer, so the lane stalls before its prompt arrives (23 Oct, a repo's
# playwright). Listing the servers as disabled in the lane's own settings answers the dialog in advance and loads none of them.
# Prints a JSON array of server names ([] when there is none); servers the user added to Claude Code itself are not affected.
project_mcp_names() {  # <dir>
  python3 -I -c '
import json, os, sys
names, d = [], os.path.abspath(sys.argv[1])
while True:
    try:
        with open(os.path.join(d, ".mcp.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        servers = cfg.get("mcpServers") if isinstance(cfg, dict) else None
        for n in (servers if isinstance(servers, dict) else {}):
            if n not in names:
                names.append(n)
    except (OSError, ValueError):
        pass
    parent = os.path.dirname(d)
    if parent == d:
        break
    d = parent
print(json.dumps(names, separators=(",", ":")))
' "${1:-.}" 2>/dev/null || echo '[]'
}

# JSON object {"skill":"off",...} for every skill dir and command file in the claude home that is not in LANE_SKILLS_KEEP.
lane_skill_overrides() {
  python3 -I -c '
import json, os, sys
home, keep = sys.argv[1], set(sys.argv[2].split())
names = set()
for sub, is_skill in (("commands", False), ("skills", True)):
    d = os.path.join(home, sub)
    try:
        entries = sorted(os.listdir(d))
    except OSError:
        continue
    for e in entries:
        if is_skill and os.path.isfile(os.path.join(d, e, "SKILL.md")):
            names.add(e)
        elif not is_skill and e.endswith(".md"):
            names.add(e[:-3])
print(json.dumps({n: "off" for n in sorted(names - keep)}, separators=(",", ":")))
' "${LANE_CLAUDE_HOME:-$HOME/.claude}" "$LANE_SKILLS_KEEP" 2>/dev/null || echo '{}'
}

claude_lane_flags() {  # <effort or empty> <connectors on|off> [lane name] [recycle K] [dir] [project servers to keep on: a,b]
  local json p s r mcp keep
  # Keep the hub's own lean project settings (hub_lean.py) current: any machine that starts a lane gets them.
  [ "$dry" = 1 ] || python3 -I "$(dirname "$0")/hub_lean.py" --install >/dev/null 2>&1 || true
  [ -z "$1" ] || printf -- '--effort %q ' "$1"
  json='{"autoCompactWindow":'"$(window_for "${4:-100}")"
  [ "$2" = on ] || json+=',"disableClaudeAiConnectors":true'
  mcp="$(project_mcp_names "${5:-.}")"
  # --project-mcp <name[,name]>: those servers stay out of the disabled list and are pre-approved, so no dialog and the lane has their tools.
  keep="${6:--}"
  if [ "$keep" != - ] && [ "$mcp" != '[]' ]; then
    mcp="$(python3 -I -c '
import json, sys
names, keep = json.loads(sys.argv[1]), sys.argv[2].split(",")
off = [n for n in names if n not in keep]
on = [n for n in names if n in keep]
out = []
if off: out.append("\"disabledMcpjsonServers\":" + json.dumps(off, separators=(",", ":")))
if on: out.append("\"enabledMcpjsonServers\":" + json.dumps(on, separators=(",", ":")))
print(",".join(out))
' "$mcp" "$keep" 2>/dev/null)"
    [ -z "$mcp" ] || json+=",$mcp"
  elif [ "$mcp" != '[]' ]; then
    json+=',"disabledMcpjsonServers":'"$mcp"
  fi
  [ -f "$LANE_RULES" ] && json+=",\"claudeMdExcludes\":[\"$LANE_WORKSPACE_MD\"],\"autoMemoryEnabled\":false"
  json+=',"skillOverrides":'"$(lane_skill_overrides)"',"syncClaudeAiSkills":false'
  json+=',"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"'"$HOME"'/.claude/scripts/lane_session_hook.sh"}]}]}'
  json+=',"enabledPlugins":{'
  for p in $LANE_PLUGINS_OFF; do json+="\"$p\":false,"; done
  json="${json%,}}"
  # An unattended lane has nobody to approve a prompt and auto mode drops the broad python3 rule, so the one roadmap post it may
  # make gets its own narrow rule, in each way the session-prime skill writes the path. Nothing else is added.
  if [ "${AFA_LANE_UNATTENDED:-}" = 1 ] || [[ "${3:-}" == oc-* ]]; then
    json+=',"permissions":{"allow":['
    for s in '~' "$HOME" '$HOME'; do
      r="Bash(python3 $s/.claude/scripts/session_prime_facts.py --post:*)"
      json+="\"$r\","
    done
    json="${json%,}]}"
  fi
  json+='}'
  printf -- '--settings %q' "$json"
  [ -z "$LANE_TOOLS_OFF" ] || printf -- ' --disallowedTools %q' "$LANE_TOOLS_OFF"
  [ -f "$LANE_RULES" ] && printf -- ' --append-system-prompt-file %q' "$LANE_RULES"
  return 0
}

# Context size in K tokens from the last assistant usage in a lane's transcript.
# yes / input / job / no for a lane's report: "yes" only when report.md is newer than everything the lane was told and no background job
# is running (lane_report_state.py reads the transcript). 19 Oct: "report:yes idle" got a lane closed mid-work.
report_word() {  # <report.md> <dir> <session-id>
  local rep="$1" dir="$2" id="$3" slug st
  [ -n "$rep" ] && [ -s "$rep" ] || { echo no; return 0; }
  slug="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$dir")"
  if [ -n "$id" ] && [ "$id" != - ] && [ "$id" != "?" ] && [ -r "$PROJ/$slug/$id.jsonl" ]; then
    st="$(python3 -I "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lane_report_state.py" "$rep" "$PROJ/$slug/$id.jsonl" 2>/dev/null || echo '?')"
    case "$st" in input|job) echo "stale:$st"; return 0 ;; esac
  fi
  echo yes
}
ctx_k() {  # <dir> <session-id>
  local f slug
  slug="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$1")"
  f="$PROJ/$slug/$2.jsonl"
  [ -r "$f" ] || { echo "?"; return 0; }
  tail -n 400 "$f" | python3 -I -c '
import sys, json
last = None
for line in sys.stdin:
    try:
        o = json.loads(line)
    except ValueError:
        continue
    m = o.get("message")
    u = m.get("usage") if isinstance(m, dict) and o.get("type") == "assistant" else None
    if u:
        last = u
if last is None:
    print("?")
else:
    n = sum(last.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    print("%dK" % round(n / 1000))
' 2>/dev/null || echo "?"
}

is_headless_billing() {
  case "$1" in codex|copilot|agy|opencode) return 0 ;; *) return 1 ;; esac
}

# Colour-coded pane text (tmux capture-pane -e) in, plain text out.
strip_ansi() { sed -E 's/\x1b\[[0-9;:?]*[A-Za-z]//g'; }
# Text on the lane's input line (last line starting with the prompt arrow), from a capture-pane -e
# pane. "typed" is the normal-colour text; "dim" is Claude Code's grey suggestion (SGR 2), which
# Enter does not submit and which is not Jonathan's words. Attributes carry across lines, as in tmux.
pane_input_text() {
  python3 -I -c '
import re, sys
want, pane = sys.argv[1], sys.stdin.read()
sgr = re.compile(r"\x1b\[([0-9;:]*)m")
csi = re.compile(r"\x1b\[[0-9;:?]*[A-Za-z]")
dim, found = False, None
for line in pane.split("\n"):
    out = {"typed": "", "dim": ""}
    arrow, i = None, 0
    while i < len(line):
        m = sgr.match(line, i)
        if m:
            ps = m.group(1).replace(":", ";").split(";")
            j = 0
            while j < len(ps):
                p = ps[j] or "0"
                if p in ("38", "48", "58"):
                    j += 3 if j + 1 < len(ps) and ps[j + 1] == "5" else 5
                    continue
                if p == "0":
                    dim = False
                elif p == "2":
                    dim = True
                elif p == "22":
                    dim = False
                j += 1
            i = m.end()
            continue
        m = csi.match(line, i)
        if m:
            i = m.end()
            continue
        c = line[i]
        i += 1
        if arrow is None:
            if c == "\u276f":
                arrow = True
            elif not c.isspace():
                arrow = False
            continue
        if arrow is False:
            continue
        out["dim" if dim else "typed"] += c
    if arrow:
        found = out
print(found[want].strip() if found else "")
' "$1" <<<"$2"
}
# True when the lane's input line holds no typed text; a dim suggestion counts as empty.
pane_input_empty() {
  local line
  line="$(pane_input_text typed "$1" | tr -d '[:space:]\302\240')"
  [ -z "$line" ]
}
# True when a spinner line like "✻ Thinking… (12s · ..." is on screen.
pane_spinner() { command grep -aqP '…\s*\(\d+(m \d+)?s' <<<"$1"; }

if [ "$mode" = status ]; then
  command -v tmux >/dev/null || { echo "tmux not installed" >&2; exit 1; }
  fill_ids
  now=$(date +%s); live=" "
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    while IFS= read -r w; do
      live="$live$w "
      pane_e="$(tmux capture-pane -e -p -t "$SESSION:$w" 2>/dev/null || true)"
      pane="$(strip_ansi <<<"$pane_e")"
      row="$(index_row "$w")"
      IFS="$TAB" read -r d n dir m b p rep id st eff conn rk ent pm <<<"${row:-}"
      if [ -z "$row" ]; then
        rep="$(ls -t "$HOME"/.claude/state/lanes/*-"$w"/report.md 2>/dev/null | head -1 || true)"
        dir="$(tmux display -p -t "$SESSION:$w" '#{pane_current_path}')"; m="?"; id="?"
        extra="(not in index)"
      else extra=""; fi
      [ -z "$row" ] || id="$(follow_session "$w" "$dir" "${id:-}" "${st:-0}")"
      wrote="$(report_word "${rep:-}" "${dir:-}" "${id:-}")"; why=""
      case "$wrote" in stale:*) why="${wrote#stale:}"; wrote=stale ;; esac
      outlog=""
      [ -n "${rep:-}" ] && [ -e "$(dirname "$rep")/out.log" ] && outlog="$(dirname "$rep")/out.log"
      if [ -n "$outlog" ]; then
        # Headless lane: the process writes "exit <code>" to out.log when it ends.
        code="$(tail -n 1 "$outlog" | sed -n 's/^exit \([0-9][0-9]*\)$/\1/p')"
        if [ -z "$code" ]; then state=working
        elif [ "$code" = 0 ]; then state=done
        else state="exit$code"; fi
        pane="$(tail -n 5 "$outlog")"; pane_e=""
      elif command grep -aqE 'Credit balance too low|API Error' <<<"$pane"; then state=error
      elif pane_spinner "$pane"; then state=working
      else state=idle; fi
      last="$(command grep -av '^[[:space:]]*$' <<<"$pane" | command grep -avE '^[[:space:]]*[─━═]+[[:space:]]*$|⏵|🐢|^[[:space:]]*❯|esc to|Enter to confirm|Not now|Don.t show again|Teach auto mode|Auto mode works' | tail -1 | sed 's/^[[:space:]]*//' | cut -c1-80 || true)"
      sug=""
      if [ "$state" = idle ]; then
        sug="$(pane_input_text dim "$pane_e" | cut -c1-120)"
        [ -z "$sug" ] || sug="  suggestion: \"$sug\""
      fi
      printf '%-18s report:%-3s %-7s %s%s\n' "$w" "$wrote" "$state" "$last" "$sug"
      ctx="?"
      case "${b:-subscription}" in codex|copilot|agy|opencode) ctx="n/a" ;; *) { [ -n "${id:-}" ] && [ "$id" != - ] && [ "$id" != "?" ]; } && ctx="$(ctx_k "$dir" "$id")" ;; esac
      case "${b:-subscription}" in codex|copilot|agy|opencode) cn="n/a" ;; *) if [ -z "$row" ]; then cn="?"; else cn="${conn:--}"; [ "$cn" = - ] && cn=on; fi ;; esac
      [ -z "$why" ] || printf '    report stale: %s\n' "$([ "$why" = input ] && echo 'the lane got input after report.md was written; the report is not its last word' || echo 'a background job it started has not finished')"
      printf '    dir:%s model:%s effort:%s connectors:%s%s context:%s engine/billing:%s session:%s %s\n' "${dir:-?}" "${m:-?}" "${eff:-?}" "$cn" "$([ "${pm:--}" = - ] || printf ' project-mcp:%s' "$pm")" "$ctx" "${b:-subscription}" "$([ "${id:--}" = - ] && echo unknown || echo "$id")" "$extra"
      # A hand-off newer than the report (or with no report) is the lane's latest word: show its Done and Next first lines, read by the
      # same rules as hub_digest.py, so a recycled lane's state is visible here without opening its folder.
      if [ -n "${rep:-}" ] && [ -s "$(dirname "$rep")/handoff.md" ] && { [ ! -e "$rep" ] || [ "$(dirname "$rep")/handoff.md" -nt "$rep" ]; }; then
        hand="$(python3 -I "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lane_handoff_summary.py" "$(dirname "$rep")/handoff.md" 2>/dev/null | cut -c1-300 || true)"
        [ -z "$hand" ] || printf '    hand-off (newer than report): %s\n' "$hand"
      fi
    done < <(tmux list-windows -t "$SESSION" -F '#{window_name}')
  else
    echo "(no tmux session \"$SESSION\")"
  fi
  # Index lanes from the last 3 days with no window and no report.
  if [ -s "$INDEX" ]; then
    cutoff="$(date -d '3 days ago' +%F)"
    while IFS="$TAB" read -r d n dir m b p rep id st eff conn rk ent pm; do
      [[ "$d" < "$cutoff" ]] && continue
      case "$live" in *" $n "*) continue ;; esac
      [ -n "${rep:-}" ] && [ -s "$rep" ] && continue
      printf '%-18s dead, resume with: lane.sh --resume %s   (%s, %s, engine/billing %s, session %s%s)\n' "$n" "$n" "$dir" "$m" "${b:-subscription}" "$([ "${id:--}" = - ] && echo unknown || echo "$id")" "$([ "${pm:--}" = - ] || printf ', project-mcp %s' "$pm")"
    done < <(awk -F'\t' '{r[$2]=$0; if(!($2 in o)){o[$2]=++c; k[c]=$2}} END{for(i=1;i<=c;i++)print r[k[i]]}' "$INDEX")
  fi
  exit 0
fi

# Type <text> into a lane window and confirm it landed (used by --send and --recycle). Returns 0 only when the words are in the lane's transcript.
send_to() {  # <name> <text...>
  [ $# -ge 2 ] || { echo "usage: lane.sh --send <name> <text>" >&2; return 1; }
  local name="$1"; shift
  text="$*"; dir=""; id=""
  row="$(index_row "$name")"
  if [ -n "$row" ]; then
    IFS="$TAB" read -r d n dir m b p rep id st eff conn rk ent pm <<<"$row"
    if is_headless_billing "${b:-}"; then
      echo "$name is a $b headless lane; start a new lane with a follow-up prompt instead of sending text to its window" >&2
      return 1
    fi
  fi
  tmux has-session -t "$SESSION" 2>/dev/null && tmux list-windows -t "$SESSION" -F '#{window_name}' | command grep -qx -- "$name" \
    || { echo "no window named $name in $SESSION" >&2; return 1; }
  target="$SESSION:$name"
  took() {  # poll up to ~$1 seconds for: input line empty and a spinner on screen
    local i pane
    for i in $(seq 1 $(($1 * 2))); do
      sleep 0.5; pane="$(tmux capture-pane -e -p -t "$target")"
      if pane_input_empty "$pane" && pane_spinner "$(strip_ansi <<<"$pane")"; then return 0; fi
    done
    return 1
  }
  # Delivered means the words are in the lane's own transcript, not only that the input line emptied (19 Oct: "I can't have you
  # dropping messages"). The transcript is the session's jsonl under ~/.claude/projects/<dir slug>, plus any written in the last 10 minutes.
  confirmed() {  # poll up to ~$1 seconds for a user turn or queued message in the lane's transcript that holds the text sent
    local i
    [ -n "${dir:-}" ] || return 2
    for i in $(seq 1 $(($1 * 2))); do
      python3 -I - "$PROJ/$(sed 's/[^A-Za-z0-9]/-/g' <<<"$dir")" "${id:--}" "$text" <<'PY' && return 0
import json, os, sys, time
proj, sid, want = sys.argv[1], sys.argv[2], " ".join(sys.argv[3].split())[:200]
try:
    files = [os.path.join(proj, f) for f in os.listdir(proj) if f.endswith(".jsonl")]
except OSError:
    sys.exit(1)
# The lane's own transcript plus any written in the last 10 minutes: a /clear or a resume starts a new transcript, and the index
# row keeps the id the lane started with.
own = os.path.join(proj, sid + ".jsonl")
files = [f for f in files if f == own or time.time() - os.path.getmtime(f) < 600]

def text_of(c):
    if isinstance(c, list):
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and isinstance(x.get("text"), str))
    return " ".join(c.split()) if isinstance(c, str) else ""

for f in files:
    try:
        with open(f, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 4_000_000))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        continue
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        # A message typed while the lane is mid-turn is not a "user" turn until the turn ends. Claude Code logs it at once as
        # a queue-operation "enqueue" (content) and then an attachment "queued_command" (prompt).
        t = o.get("type")
        if t == "user":
            m = o.get("message")
            got = text_of(m.get("content")) if isinstance(m, dict) else ""
        elif t == "queue-operation" and o.get("operation") == "enqueue":
            got = text_of(o.get("content"))
        elif t == "attachment" and isinstance(o.get("attachment"), dict) and o["attachment"].get("type") == "queued_command":
            got = text_of(o["attachment"].get("prompt"))
        else:
            continue
        if want in got:
            sys.exit(0)
sys.exit(1)
PY
      sleep 0.5
    done
    return 1
  }
  # A permission dialog on the lane's screen would take these keystrokes as its answer (a digit picks an option, Enter accepts the default),
  # and a lane that is mid-turn can raise one at any moment. So look before typing and again before every Enter; tmux has no atomic
  # "type if no dialog", so this narrows the race to the gap between one look and the keys that follow it (a security review of c0baa36).
  dialog_up() { tmux capture-pane -p -t "$target" 2>/dev/null | tail -15 | command grep -qE 'Enter to select|Do you want to proceed|Esc to cancel'; }
  if dialog_up; then echo "sent to $name: NOT SENT (a dialog is on its screen; typing would answer it)"; return 1; fi
  # LANE_SEND_PANE_RE: for a local command (/model) that never reaches the transcript. Delivered means a line matching the regex is on the
  # pane that was not there before the Enter (22 Oct: "Set model to Sonnet 5.5 and saved as your default for new sessions").
  pane_hits() { tmux capture-pane -p -S -300 -t "$target" 2>/dev/null | command grep -ciE -- "$LANE_SEND_PANE_RE" || true; }
  hits_before=0; [ -z "${LANE_SEND_PANE_RE:-}" ] || hits_before="$(pane_hits)"
  tmux send-keys -t "$target" -l -- "$text"
  sleep 0.3
  if dialog_up; then
    tmux send-keys -t "$target" C-u
    echo "sent to $name: NOT SUBMITTED (a dialog came up while the text was typed; no Enter was sent)"; return 1
  fi
  tmux send-keys -t "$target" C-m
  if [ -n "${LANE_SEND_PANE_RE:-}" ]; then
    local i
    for i in $(seq 1 $(( ${LANE_SEND_CONFIRM_SECS:-15} * 2 ))); do
      [ "$(pane_hits)" -gt "$hits_before" ] && { echo "sent to $name: confirmed on its screen"; return 0; }
      sleep 0.5
    done
    echo "sent to $name: NOT CONFIRMED (no new line matching \"$LANE_SEND_PANE_RE\" on its screen)"
    return 1
  fi
  how=""
  if took 6; then how="submitted"
  elif dialog_up; then echo "sent to $name: NOT SUBMITTED (a dialog came up after the Enter; no second Enter was sent)"; return 1
  else tmux send-keys -t "$target" C-m; if took 6; then how="submitted after a second Enter"; fi; fi
  pane="$(tmux capture-pane -e -p -t "$target")"
  if [ -z "$how" ] && ! pane_input_empty "$pane"; then
    echo "sent to $name: DID NOT TAKE (text still in the input line after two Enters)"; return 1
  fi
  rc=0; confirmed "${LANE_SEND_CONFIRM_SECS:-15}" || rc=$?
  if [ "$rc" -eq 0 ]; then echo "sent to $name: confirmed in its transcript${how:+ ($how)}"; return 0; fi
  if [ "$rc" -eq 2 ] && [ -n "$how" ]; then echo "sent to $name: $how (input empty, spinner showing; no index row, so no transcript to check)"; return 0; fi
  echo "sent to $name: NOT IN ITS TRANSCRIPT (the input line emptied${how:+ and it was $how}, but the lane's transcript does not hold the words)"
  return 1
}
# send_held <name> <text...>: a lane past its recycle line, or with a recycle already under way, is not typed into. Text typed mid-turn queues
# in Claude Code, and queued input is what --recycle must not carry into the fresh session (26 Oct: a hub message typed into scout-run-2 at
# 163K, past its 128K line, queued and blocked the watcher's recycle, which failed safe). So the words go to hub-pending.md in the lane's
# hand-off folder, the recycle starts (unless one is under way or the lane is recycling too often), and the re-prime message sends the fresh
# session to read the file. Returns 0 when it held the text (and says what it did), 1 when the lane should be typed into as usual.
send_held() {
  local name="$1"; shift
  local text="$*" row d n dir m b p rep id st eff conn rk ent pm hand pend lockd ctxk line locked=0 past=0 rlog cnt
  row="$(index_row "$name")"; [ -n "$row" ] || return 1
  IFS="$TAB" read -r d n dir m b p rep id st eff conn rk ent pm <<<"$row"
  is_headless_billing "${b:-}" && return 1
  case "${b:-subscription}" in codex|copilot|agy|opencode) return 1 ;; esac
  [ -n "${rep:-}" ] && [ -z "$(find "$(dirname "$rep")/out.log" -maxdepth 0 2>/dev/null)" ] || return 1
  tmux has-session -t "$SESSION" 2>/dev/null && tmux list-windows -t "$SESSION" -F '#{window_name}' | command grep -qx -- "$name" || return 1
  lockd="$HOME/.claude/state/lanes/.recycle-$name.lock"
  if [ -d "$lockd" ] && [ -z "$(find "$lockd" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then locked=1; fi
  ctxk="?"
  if [ -n "${id:-}" ] && [ "$id" != - ]; then
    id="$(follow_session "$name" "$dir" "$id" "${st:-0}")"
    ctxk="$(ctx_k "$dir" "$id")"; ctxk="${ctxk%K}"
  fi
  line="$(lane_recycle_k "${rk:-}" "${p:-}" "$dir" "${conn:--}")"
  [[ "$ctxk" =~ ^[0-9]+$ ]] && [ "$ctxk" -ge "$line" ] && past=1
  [ "$past" = 1 ] || [ "$locked" = 1 ] || return 1
  hand="$(dirname "$rep")"; pend="$hand/hub-pending.md"
  mkdir -p "$hand"
  printf '## %s (typed with lane.sh --send while you were past your recycle line)\n%s\n\n' "$(date '+%-I:%M %p')" "$text" >>"$pend"
  rlog="$HOME/.claude/state/lanes/recycle.log"
  cnt=0
  if [ -f "$rlog" ]; then
    cnt="$(command grep -a $'\trecycled\t' "$rlog" | awk -F'\t' -v n="$name" -v now="$(date +%s)" -v w="${LANE_THRASH_SECS:-1800}" '$2 == n { cmd = "date -d \"" $1 "\" +%s"; cmd | getline t; close(cmd); if (now - t <= w) c++ } END { print c + 0 }')"
  fi
  if [ "$locked" = 1 ]; then
    echo "held for $name: a recycle is already under way, so I did not type into its box. Your text is in $pend and the re-prime message sends it there to read it."
  elif [ "$cnt" -ge "${LANE_THRASH_COUNT:-3}" ]; then
    echo "held for $name: it is at ${ctxk}K, past its ${line}K line, but it was recycled $cnt times in the last $(( ${LANE_THRASH_SECS:-1800} / 60 )) minutes so I did not start another. Your text is in $pend; it is read at the next recycle (lane.sh --recycle $name), or tell the lane to read it."
  else
    date +%s >"$HOME/.claude/state/lanes/.recycle-$name.tried"
    setsid nohup timeout 1500 "$(readlink -f "${BASH_SOURCE[0]}")" --recycle "$name" >>"$HOME/.claude/state/lanes/recycle.out" 2>&1 </dev/null &
    echo "held for $name: it is at ${ctxk}K, past its ${line}K line, so I did not type into its box (queued text would block its recycle). Your text is in $pend; I started its recycle, and its re-prime message tells it to read that file."
  fi
  return 0
}
if [ "$mode" = send ]; then
  [ $# -ge 2 ] || { echo "usage: lane.sh --send <name> <text>" >&2; exit 1; }
  send_held "$@" && exit 0
  send_to "$@"; exit $?
fi

# switch_model <name> <model>: type /model into a running lane, put back the user default, and move the lane's index row to the new model.
# Claude Code's /model also saves the choice as the USER default model, which moved the hub's own default to sonnet on 21 Oct, so read the
# default first and put it back after (user_model.py prints only the model key). Returns 0 only when the pane showed the switch. Used by
# --model and by --recycle (which puts a lane back on its policy model).
switch_model() {  # <name> <model>
  local name="$1" newm="$2" um before now changed=0 calm=0 fam rc=0 _
  um="$(cd "$(dirname "$0")" && pwd -P)/user_model.py"
  python3 -I "$um" check >/dev/null 2>&1 \
    || { echo "cannot switch $name: the user default model cannot be read back and restored safely, and /model would change it" >&2; return 1; }
  before="$(python3 -I "$um" get)"
  # /model is a local command: it never lands in the transcript, so the switch is confirmed by the pane's "Set model to" line.
  fam="$(command grep -oiE 'opus|sonnet|haiku|fable' <<<"$newm" | head -1 || true)"  # the pane names the family ("Sonnet 5.5"), not the id
  LANE_SEND_PANE_RE="Set model to .*${fam}" send_to "$name" "/model $newm" || rc=$?
  for _ in $(seq 1 "${LANE_MODEL_POLLS:-12}"); do  # /model saves the default a moment after it runs: restore until it has settled
    now="$(python3 -I "$um" get)"
    if [ "$now" != "$before" ]; then
      changed=1; calm=0
      if [ "$before" = - ]; then python3 -I "$um" unset; else python3 -I "$um" set "$before"; fi
    else
      calm=$((calm + 1))
      [ "$changed" = 1 ] && [ "$calm" -ge 2 ] && break
    fi
    sleep 0.5
  done
  [ "$rc" -eq 0 ] || return "$rc"
  index_set "$name" 4 "$newm"
  echo "model: $name now on $newm; the user default model is still ${before}"
  return 0
}

# --model <name> <model>: switch a running lane's model (see switch_model).
if [ "$mode" = model ]; then
  [ $# -eq 2 ] || { echo "usage: lane.sh [--dry-run] --model <name> <model>" >&2; exit 1; }
  name="$1"; newm="$2"
  is_claude_model "$newm" || { echo "--model needs a Claude model name (opus, sonnet, haiku, fable or claude-...), got: $newm" >&2; exit 1; }
  row="$(index_row "$name")"
  [ -n "$row" ] || { echo "no lane named $name in $INDEX" >&2; exit 1; }
  IFS="$TAB" read -r d n dir m b p rep id st eff conn rk ent pm <<<"$row"
  site_tier_ok "$newm" "${p:-}" || exit 1
  mail_tier_ok "$newm" "$name" "${p:-}" || exit 1
  high_tier_ok "$newm" "$name" "${p:-}" "${ent:-}" || exit 1
  if [ "$dry" = 1 ]; then
    um="$(cd "$(dirname "$0")" && pwd -P)/user_model.py"
    python3 -I "$um" check >/dev/null 2>&1 \
      || { echo "cannot switch $name: the user default model cannot be read back and restored safely, and /model would change it" >&2; exit 1; }
    echo "would type '/model $newm' into $name, then restore the user default model ($(python3 -I "$um" get))"
    exit 0
  fi
  rc=0; switch_model "$name" "$newm" || rc=$?
  [ "$rc" -eq 0 ] || exit "$rc"
  if is_high_model "$newm" && [ -n "$allow_high_reason" ] && { [ -z "${ent:-}" ] || [ "${ent:-}" = - ]; }; then
    index_set "$name" 13 "$(one_line "$allow_high_reason")"
  fi
  exit 0
fi

# --tier <name>: "<model> TAB <reason>" for the lane's last index row; the reason is the one written to the row (column 13), "site-model" for a
# site-model prompt on opus or fable, else "-". lane_watch.sh names any live lane on opus or fable whose reason is "-".
if [ "$mode" = tier ]; then
  [ $# -eq 1 ] || { echo "usage: lane.sh --tier <name>" >&2; exit 1; }
  row="$(index_row "$1")"
  [ -n "$row" ] || { printf '?\t-\n'; exit 0; }
  IFS="$TAB" read -r d n dir m b p rep id st eff conn rk ent pm <<<"$row"
  reason="${ent:--}"
  if [ "$reason" = - ] && is_high_model "${m:-}" && site_prompt "${p:-}"; then reason=site-model; fi
  printf '%s\t%s\n' "${m:-?}" "$reason"
  exit 0
fi

# --recycle-point <name>: the lane's own recycle line in K tokens, as a bare number (lane_watch.sh reads it for each lane it watches).
if [ "$mode" = point ]; then
  [ $# -eq 1 ] || { echo "usage: lane.sh --recycle-point <name>" >&2; exit 1; }
  row="$(index_row "$1")"
  if [ -z "$row" ]; then default_recycle_k code; exit 0; fi
  IFS="$TAB" read -r d n dir m b p rep id st eff conn rk ent pm <<<"$row"
  lane_recycle_k "${rk:-}" "${p:-}" "${dir:-}" "${conn:--}"
  exit 0
fi

# --ctx <name>: the lane's context in K tokens as a bare number, or ? when it cannot be read (lane_watch.sh uses it against the recycle line).
# --turn <name>: monitor, real or ? for the lane's last finished turn (lane_watch.sh skips waking the hub for a Monitor tick that asked nothing).
if [ "$mode" = ctx ] || [ "$mode" = turn ]; then
  [ $# -eq 1 ] || { echo "usage: lane.sh --$mode <name>" >&2; exit 1; }
  row="$(index_row "$1")"
  [ -n "$row" ] || { echo "?"; exit 0; }
  IFS="$TAB" read -r d n dir m b p rep id st eff conn rk ent pm <<<"$row"
  case "${b:-subscription}" in codex|copilot|agy|opencode) echo "?"; exit 0 ;; esac
  # A lane just started has no session id in its row yet (--status fills it in): look for its transcript without rewriting the index.
  if { [ -z "${id:-}" ] || [ "$id" = - ]; } && [ "${st:--}" != - ]; then
    id="$(proven_session "$1" "$st")"
  fi
  { [ -n "${id:-}" ] && [ "$id" != - ]; } || { echo "?"; exit 0; }
  id="$(follow_session "$1" "$dir" "$id" "${st:-0}")"
  if [ "$mode" = turn ]; then
    f="$PROJ/$(sed 's/[^A-Za-z0-9]/-/g' <<<"$dir")/$id.jsonl"
    python3 -I "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lane_turn_kind.py" "$f" 2>/dev/null || echo "?"
    exit 0
  fi
  v="$(ctx_k "$dir" "$id")"; echo "${v%K}"
  exit 0
fi

# --recycle <name>: hand a lane a fresh context in its own window instead of letting it auto-compact (his rule, 20 Oct: "they should never
# have to compact on their own, that's token waste"). The lane writes handoff.md in its hand-off folder; only once that file has changed
# and the lane is idle does it get /clear, then a message to read prompt.md, then handoff.md, then carry on. The new session id goes
# into index.tsv as a new row, so --status, --ctx and --resume follow the new transcript. A lane that never writes handoff.md is NOT cleared.
# The request is typed at once, mid-turn if need be: Claude Code queues it and the lane sees it after the tool call in flight. Waiting for
# the turn to end first failed (21 Oct, this lane: one 15-minute turn from 120K to the 168K backstop, "did not finish its turn in 300s",
# then an auto compaction). Only a dialog on screen holds the request back, since typing would answer the dialog.
# A recycle within LANE_RECYCLE_THRASH_SECS (1200) of the lane's previous start or recycle means its working set is bigger than its line:
# the new row's line goes up 20K, to at most 0.84 x the window cap - 40K.
#   LANE_RECYCLE_IDLE_SECS (300)  how long a dialog may hold the request back
#   LANE_RECYCLE_WAIT_SECS (600)  how long to wait for handoff.md after asking
if [ "$mode" = recycle ]; then
  [ $# -eq 1 ] || { echo "usage: lane.sh [--dry-run] --recycle <name>" >&2; exit 1; }
  name="$1"
  row="$(index_row "$name")"
  [ -n "$row" ] || { echo "no lane named $name in $INDEX" >&2; exit 1; }
  IFS="$TAB" read -r d n dir m b p rep id st eff conn rk ent pm <<<"$row"
  # Judged by the newest index row's engine only: a folder can hold an out.log from an earlier headless run (26 Oct) while the lane now
  # in it, the one on this row, is an interactive window.
  if is_headless_billing "${b:-}"; then
    echo "$name is a headless lane; there is no window to recycle" >&2; exit 1
  fi
  [ -n "${rep:-}" ] || { echo "$name has no hand-off folder recorded in $INDEX" >&2; exit 1; }
  id="$(follow_session "$name" "$dir" "${id:-}" "${st:-0}")"
  hand="$(dirname "$rep")"; handoff="$hand/handoff.md"
  ctxk="$([ -n "${id:-}" ] && [ "$id" != - ] && ctx_k "$dir" "$id" || echo '?')"
  rk_now="$(lane_recycle_k "${rk:-}" "${p:-}" "$dir" "${conn:--}")"
  rk_next="$rk_now"
  if [[ "${st:-}" =~ ^[0-9]+$ ]] && [ $(( $(date +%s) - st )) -lt "${LANE_RECYCLE_THRASH_SECS:-1200}" ]; then
    rk_next=$(( rk_now + 20 )); rk_ceiling=$(( ${LANE_WINDOW_CAP:-200000} * 84 / 100000 - 40 ))
    [ "$rk_next" -le "$rk_ceiling" ] || rk_next=$(( rk_ceiling > rk_now ? rk_ceiling : rk_now ))
  fi
  target_model="$(policy_model "${m:-}" "$name" "${p:-}" "${ent:--}")"
  rid="$(date +%s)"   # marks this recycle's request and re-prime, so a leftover copy of the request in a fresh session is recognisable
  msg_handoff="Hub: your context is at $ctxk, past your recycle line (${rk_now}K), and Claude Code compacts on its own not far above it. Finish only the step in hand (the tool call you are in), start no new step, and make your very next action writing $handoff, so that a fresh session can carry on from $p and that file alone, never from this conversation, an older hand-off or a log: (1) what is done; (2) what is in flight: each file, branch, worktree, pull request and background job with its path and state; (3) the next concrete step: the exact command, or the file and line to start at; (4) what to re-arm (timers, background jobs, watchers); (5) every fact the next step needs, copied in rather than pointed at. Keep it under 120 lines: it is read in full and costs the new session context. I keep your previous handoff.md as handoff-prevN.md, so do not copy it yourself and do not send the next session to read it. Then stop, even mid-task, and say \"ready to recycle\". Once it is written I clear your context and send you back to prompt.md and handoff.md. (recycle $rid)"
  build_reprime() {  # the re-prime message; words the hub held back for a lane past its line (hub-pending.md) are named in it
    local pend="$hand/hub-pending.md" reads="Read $p, then $handoff, and nothing else"
    [ -s "$pend" ] && reads="Read $p, then $handoff, then $pend (words the hub typed for you while you were past your line, held back so they could not queue into the recycle: act on them, then delete that file), and nothing else"
    printf '%s' "Hub: your context was cleared (recycle $rid, not a restart). $reads before carrying on from the next step it names and re-arming what it lists; it is meant to be self-contained, so read a handoff-prev file or an old log only when handoff.md names the exact thing you need. Your hand-off folder is unchanged: $hand. A hub request to write handoff.md marked (recycle $rid) that reaches you now is the one you already answered before the clear: ignore it. A request with a different number is a new one."
  }
  msg_reprime="$(build_reprime)"
  if [ "$dry" = 1 ]; then
    echo "recycle $name ($dir, context $ctxk):"
    echo "  1. wait only for any dialog on its screen to clear (up to ${LANE_RECYCLE_IDLE_SECS:-300}s); a lane mid-turn is not waited for"
    echo "  1b. keep the current handoff.md as handoff-prev<N>.md and delete all but the newest ${LANE_HANDOFF_PREV_KEEP:-5} handoff-prev files"
    echo "  2. send: $msg_handoff"
    echo "  3. wait for $handoff to change and the lane to go idle (up to ${LANE_RECYCLE_WAIT_SECS:-600}s); without it the lane is not cleared"
    echo "  3b. make sure no input is queued in the lane (a queued message would reach the fresh session): wait up to ${LANE_RECYCLE_QUEUE_SECS:-120}s for it to run, then pop it with Escape; without that the lane is not cleared"
    echo "  4. send: /clear"
    echo "  4b. set the model to the policy model ($target_model) if it is not already on it (${m:-?} now; opus and fable stay only for a site-model prompt or a logged --allow-high-tier reason, and a mail lane is always sonnet)"
    echo "  5. send: $msg_reprime (plus, when $hand/hub-pending.md exists by then, the sentence that sends the lane to read it: words lane.sh --send held back while the lane was past its line)"
    echo "  6. append an index row to $INDEX with the lane's new session id, model ${target_model} and recycle line ${rk_next}K"
    exit 0
  fi
  command -v tmux >/dev/null && tmux has-session -t "$SESSION" 2>/dev/null && tmux list-windows -t "$SESSION" -F '#{window_name}' | command grep -qx -- "$name" \
    || { echo "no window named $name in $SESSION" >&2; exit 1; }
  lockd="$HOME/.claude/state/lanes/.recycle-$name.lock"
  mkdir -p "$(dirname "$lockd")"
  if [ -d "$lockd" ] && [ -n "$(find "$lockd" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then rmdir "$lockd" 2>/dev/null || true; fi
  mkdir "$lockd" 2>/dev/null || { echo "$name is already being recycled ($lockd)" >&2; exit 1; }
  rlog="$HOME/.claude/state/lanes/recycle.log"
  trap 'rmdir "$lockd" 2>/dev/null || true' EXIT
  rfail() { echo "recycle $name FAILED: $*" >&2; printf '%s\t%s\tFAILED\t%s\n' "$(date -Is)" "$name" "$*" >>"$rlog"; exit 1; }
  lane_idle() {  # no spinner and no dialog in the lane's pane
    local pane; pane="$(tmux capture-pane -p -t "$SESSION:$name" 2>/dev/null)" || return 1
    pane_spinner "$pane" && return 1
    printf '%s\n' "$pane" | tail -15 | command grep -qE 'Enter to select|Do you want to proceed|Esc to cancel' && return 1
    return 0
  }
  wait_no_dialog() {  # <secs>: no dialog on screen (looked at least once); a working lane is fine, the request queues into its turn
    local end=$(( $(date +%s) + $1 )) pane
    while :; do
      pane="$(tmux capture-pane -p -t "$SESSION:$name" 2>/dev/null)" || return 1
      printf '%s\n' "$pane" | tail -15 | command grep -qE 'Enter to select|Do you want to proceed|Esc to cancel' || return 0
      [ "$(date +%s)" -lt "$end" ] || return 1
      sleep 2
    done
  }
  wait_idle() {  # <secs>: idle on two looks a moment apart
    local end=$(( $(date +%s) + $1 ))
    while [ "$(date +%s)" -lt "$end" ]; do
      if lane_idle; then sleep 2; lane_idle && return 0; fi
      sleep 2
    done
    return 1
  }
  stamp() { stat -c '%Y.%s' "$handoff" 2>/dev/null || echo none; }
  # Keep the hand-off the lane is about to overwrite as handoff-prev<N>.md (handoff-prev.md counts as 1) and keep only the newest few: the
  # pokeys-site lane left 21 of them (25 Oct) and its fresh sessions read them, which is what filled a 128K line again within minutes.
  rotate_handoff_prev() {
    local f n max=1 keep="${LANE_HANDOFF_PREV_KEEP:-5}"
    if [ -s "$handoff" ]; then
      for f in "$hand"/handoff-prev*.md; do
        [ -e "$f" ] || continue
        n="${f##*/handoff-prev}"; n="${n%.md}"; [ -n "$n" ] || n=1
        [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -gt "$max" ] && max="$n"
      done
      [ -e "$hand/handoff-prev.md" ] || [ "$max" -gt 1 ] || max=0
      cp -p "$handoff" "$hand/handoff-prev$((max + 1)).md" || return 1
    fi
    ls -t "$hand"/handoff-prev*.md 2>/dev/null | tail -n +"$((keep + 1))" | while IFS= read -r f; do rm -f -- "$f"; done
    return 0
  }
  # Input queued in the lane (typed while it was mid-turn: our own request, a task notification, anything) would run, or sit in the input
  # line, after /clear and reach the FRESH session (25 Oct, pokeys-site: a re-primed lane at 95K, under its 128K line, wrote handoff.md and
  # stopped; nothing recycled it and it sat idle seven hours). Claude Code logs each queued input in the transcript: enqueue, then remove or
  # dequeue when it runs and popAll when Escape puts it back in the input line. queue_pending counts what is still queued.
  queue_pending() {
    [ -n "${id:-}" ] && [ "$id" != - ] || { echo 0; return; }
    python3 -I - "$PROJ/$(sed 's/[^A-Za-z0-9]/-/g' <<<"$dir")/$id.jsonl" <<'PY'
import json, sys
q = []
try:
    with open(sys.argv[1], "rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 4_000_000))
        lines = fh.read().decode("utf-8", "replace").splitlines()
except OSError:
    print(0); sys.exit(0)
for line in lines:
    if '"queue-operation"' not in line[:80]:
        continue
    try:
        o = json.loads(line)
    except ValueError:
        continue
    op, c = o.get("operation"), o.get("content")
    if op == "enqueue":
        q.append(c)
    elif op == "remove":
        # A subagent hand-back is enqueued with content None (its text is in a later queued_command attachment) but removed with its text
        # (26 Oct, scout-run-2: the remove never matched, one item counted forever, every recycle failed). No match: drop a None entry.
        if c in q: q.remove(c)
        elif None in q: q.remove(None)
    elif op == "dequeue":
        if q: q.pop(0)
    elif op == "popAll":
        q = []
print(len(q))
PY
  }
  # Wait for queued input to run (a lane that is idle runs it at once), and if it is still there after LANE_RECYCLE_QUEUE_SECS press Escape
  # once, which puts it back in the input line (logged as popAll), then clear the line. Returns 1 when something is still queued.
  drain_queue() {
    local end=$(( $(date +%s) + ${LANE_RECYCLE_QUEUE_SECS:-120} )) n
    while :; do
      n="$(queue_pending)"
      if [ "$n" = 0 ] && lane_idle; then return 0; fi
      [ "$(date +%s)" -lt "$end" ] || break
      sleep 2
    done
    [ "$n" != 0 ] || return 1   # only a busy lane: the caller waits for idle itself
    lane_idle || return 1
    tmux send-keys -t "$SESSION:$name" Escape; sleep 1
    tmux send-keys -t "$SESSION:$name" C-u; sleep 1
    [ "$(queue_pending)" = 0 ]
  }

  wait_no_dialog "${LANE_RECYCLE_IDLE_SECS:-300}" || rfail "a dialog stayed on the lane's screen for ${LANE_RECYCLE_IDLE_SECS:-300}s, so the hand-off request was not typed"
  rotate_handoff_prev || rfail "could not keep the old handoff.md as a handoff-prev file in $hand"
  before="$(stamp)"; t0="$(date +%s)"
  send_to "$name" "$msg_handoff" >/dev/null || rfail "the hand-off request did not reach the lane"
  end=$(( t0 + ${LANE_RECYCLE_WAIT_SECS:-600} )); written=0
  while [ "$(date +%s)" -lt "$end" ]; do
    now_stamp="$(stamp)"
    if [ "$now_stamp" != none ] && [ "$now_stamp" != "$before" ] && [ -s "$handoff" ] && [ "$(stat -c %Y "$handoff")" -ge "$t0" ] && lane_idle; then
      sleep 2; lane_idle && { written=1; break; }
    fi
    sleep 2
  done
  [ "$written" = 1 ] || rfail "no new $handoff in ${LANE_RECYCLE_WAIT_SECS:-600}s; the lane was NOT cleared"
  drain_queue || rfail "input was still queued in the lane after ${LANE_RECYCLE_QUEUE_SECS:-120}s and Escape did not clear it, so /clear would have carried it into the fresh session; the lane was NOT cleared"

  # /clear starts no turn, so there is no spinner to wait for: type it, press Enter, and wait for the input line to empty.
  cpane=""
  wait_no_dialog 30 || rfail "a dialog came up before /clear was typed; the lane was NOT cleared"
  tmux send-keys -t "$SESSION:$name" -l -- "/clear"; sleep 0.5
  wait_no_dialog 0 || { tmux send-keys -t "$SESSION:$name" C-u; rfail "a dialog came up while /clear was typed; no Enter was sent"; }
  tmux send-keys -t "$SESSION:$name" C-m
  for _ in $(seq 1 20); do
    sleep 0.5; cpane="$(tmux capture-pane -e -p -t "$SESSION:$name")"
    pane_input_empty "$cpane" && break
  done
  pane_input_empty "$cpane" || rfail "/clear stayed in the input line"
  sleep 2
  # The lane's model goes back to its policy model before it reads anything (his rule, 22 Oct: lanes sat on opus and fable all day because the
  # tier was only checked at start). A failed switch is logged and the recycle goes on; lane_watch names the lane for the model instead.
  if [ "$target_model" != "$m" ]; then
    if switch_model "$name" "$target_model" >/dev/null 2>&1; then
      printf '%s\t%s\tmodel-switched\tfrom %s to %s\n' "$(date -Is)" "$name" "$m" "$target_model" >>"$rlog"
      m="$target_model"
    else
      printf '%s\t%s\tmodel-switch-failed\twanted %s, still %s\n' "$(date -Is)" "$name" "$target_model" "$m" >>"$rlog"
      echo "warning: could not set $name to $target_model after the recycle; it is still on $m" >&2
    fi
  fi
  msg_reprime="$(build_reprime)"   # a --send that arrived while the recycle ran has left hub-pending.md by now
  send_to "$name" "$msg_reprime" >/dev/null || rfail "the lane was cleared but the re-prime message did not reach it; send it by hand: $msg_reprime"

  newid=""
  for _ in $(seq 1 20); do
    newid="$(proven_session "$name" "$t0")"
    [ -n "$newid" ] || newid="$(session_with_text "$dir" "$t0" "$msg_reprime")"   # a lane whose process predates the hook
    [ -n "$newid" ] && break; sleep 1
  done
  [ -n "$newid" ] || rfail "the lane was re-primed but no new transcript was found under $PROJ to record"
  index_append "$(printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' "$(date +%F)" "$name" "$dir" "$m" "${b:-subscription}" "$p" "$rep" "$newid" "$t0" "${eff:--}" "${conn:--}" "$rk_next" "${ent:--}" "${pm:--}")"
  printf '%s\t%s\trecycled\tfrom %s (session %s) to session %s, line %sK\n' "$(date -Is)" "$name" "$ctxk" "${id:--}" "$newid" "$rk_next" >>"$rlog"
  echo "recycled $name: handoff.md written, context cleared at $ctxk, re-primed; new session $newid, line ${rk_next}K"
  exit 0
fi

if [ "$mode" = resume ]; then
  [ $# -eq 1 ] || { echo "usage: lane.sh [--dry-run] [--console] --resume <name>" >&2; exit 1; }
  name="$1"
  fill_ids
  row="$(index_row "$name")"
  [ -n "$row" ] || { echo "no lane named $name in $INDEX" >&2; exit 1; }
  IFS="$TAB" read -r d n dir m b p rep id st eff conn rk ent pm <<<"$row"
  if is_headless_billing "${b:-}"; then
    echo "$name is a $b headless lane; start a new lane with a follow-up prompt instead of resuming it" >&2
    exit 1
  fi
  [ "$engine" = claude ] || { echo "--engine only applies when starting a lane; lane resume uses Claude Code" >&2; exit 1; }
  { [ -n "${id:-}" ] && [ "$id" != - ]; } || { echo "no session id recorded for $name; find it under $PROJ and add it to $INDEX" >&2; exit 1; }
  id="$(follow_session "$name" "$dir" "$id" "${st:-0}")"  # a /clear since the row was written moved the lane to a newer transcript
  [ -d "$dir" ] || { echo "no such folder: $dir" >&2; exit 1; }
  if [ -e "$(dirname "${rep:-/nonexistent}")/out.log" ]; then
    echo "$name is a headless lane (out.log beside its report); there is no interactive session to resume. Read $(dirname "$rep")/out.log" >&2; exit 1
  fi
  [ "$b" = console ] && console=1
  site_tier_ok "$m" "${p:-}" || exit 1
  mail_tier_ok "$m" "$name" "${p:-}" || exit 1
  high_tier_ok "$m" "$name" "${p:-}" "${ent:-}" || exit 1
  if [ "$dry" = 0 ]; then
    command -v tmux >/dev/null || { echo "tmux not installed" >&2; exit 1; }
    if tmux has-session -t "$SESSION" 2>/dev/null \
       && tmux list-windows -t "$SESSION" -F '#{window_name}' | command grep -qx -- "$name"; then
      echo "a window named $name already exists in $SESSION" >&2; exit 1
    fi
  fi
  if [ "$dry" = 0 ] && is_high_model "$m" && [ -n "$allow_high_reason" ] && { [ -z "${ent:-}" ] || [ "${ent:-}" = - ]; }; then
    index_set "$name" 13 "$(one_line "$allow_high_reason")"
  fi
  eff="$(lane_effort "$m" "${eff:-}")"
  # Reuse the recorded connectors choice; a row from before the column was started with them on.
  if [ "$connectors_opt" = 1 ] || [ "${conn:--}" != off ]; then conn=on; else conn=off; fi
  rk="${recycle_at_opt:-$(lane_recycle_k "${rk:-}" "${p:-}" "$dir" "${conn:--}")}"
  if [ "$conn" != on ] && [ "$rk" -gt 150 ]; then echo "--recycle-at above 150K is only for lanes with the connectors on ($name has them off), got: $rk" >&2; exit 1; fi
  pm="${project_mcp_opt:-${pm:--}}"
  [ -z "$project_mcp_opt" ] || index_set "$name" 14 "$pm"
  flags="$(claude_lane_flags "$eff" "$conn" "$name" "$rk" "$dir" "$pm")"
  uenv="$(lane_env_prefix "$name" "$(dirname "${rep:-$HOME/.claude/state/lanes/$d-$name/report.md}")")"
  if [ "$console" = 1 ]; then
    cmd="${uenv}$HOME/.claude/scripts/console_worker.sh --inner $(printf %q "$dir") --resume $(printf %q "$id") --model $(printf %q "$m") $flags"
  else
    # --add-dir: the hand-off folder is outside the lane's cwd; without it a recycle re-prime stalls on a "Read outside the working directories" dialog (28 Oct 2026).
    cmd="${uenv}claude --resume $(printf %q "$id") --model $(printf %q "$m") --add-dir $(printf %q "$(dirname "${rep:-$HOME/.claude/state/lanes/$d-$name/report.md}")") $flags"
  fi
  if [ "$dry" = 0 ] && ! tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux new-session -d -s "$SESSION" -n "$name" -c "$dir"
  else
    t new-window -t "$SESSION:" -n "$name" -c "$dir"
  fi
  t send-keys -t "$SESSION:$name" "$cmd" Enter
  echo "resumed: $name  ($m, effort ${eff:--}, connectors $conn, $dir, session $id, billing $([ "$console" = 1 ] && echo console || echo subscription))"
  exit 0
fi

# His own-repo code lanes start on agy until Codex returns (his rule, 22 Oct 3:30 PM: "route my own-repo code to agy now"); written down
# only, 67 own-repo lanes since 18 Oct still ran on Claude. A Claude start in one of his own repos is refused unless --claude-because
# "<reason>" says why (logged beside the tier overrides). Not his own repo, so never refused: a client repo (the test the codex and copilot
# guard uses), ~/repos itself, a mail lane, a site-model prompt. When the client lists cannot be read the repo cannot be told apart, so the
# guard stands aside (the external-engine guard is the one that fails closed). Only a start is checked; resume, recycle, send and the rest are other modes.
agy_default_ok() {  # <name> <dir> <model> <prompt-file>
  local name="$1" d="$2" model="$3" pf="${4:-}" home_repos
  [ "$engine" = claude ] || return 0
  [ -r "$HOME/repos/hub/client-senders.tsv" ] && [ -f "$HOME/repos/hub/client-senders.tsv" ] \
    && [ -r "$HOME/repos/hub/client-repos.txt" ] && [ -f "$HOME/repos/hub/client-repos.txt" ] || return 0
  home_repos="$(canonical_dir "$HOME/repos" 2>/dev/null || true)"
  [ "$d" != "$home_repos" ] || return 0
  mail_lane "$name" "$pf" && return 0
  grep -qE 'data/sites/|site-model' -- "$pf" 2>/dev/null && return 0
  ( engine=codex; allow_client=0; dir="$d"; guard_external_engine ) >/dev/null 2>&1 || return 0  # a client repo, or one that cannot be checked
  if [ -n "$claude_because" ]; then
    if [ "$dry" = 1 ]; then echo "# would log the agy-default override for $name ($model) to $LANE_TIER_LOG"
    else
      mkdir -p "$(dirname "$LANE_TIER_LOG")"
      printf '%s\t%s\t%s\tagy-default\t%s\t%s\n' "$(date -Is)" "$name" "$model" "$(tr '\t\n' '  ' <<<"$claude_because")" "$d" >>"$LANE_TIER_LOG"
    fi
    return 0
  fi
  {
    echo "refusing a Claude lane for $name in $d: his own-repo code lanes start on agy until Codex returns on 31 Oct 2026 (his rule, 22 Oct 3:30 PM). Use:"
    echo "  lane.sh --engine agy $name $d gemini-3.8-flash-high $pf"
    echo "(agy out of usage: --engine codex, then --engine copilot, then --engine opencode $name $d sonnet $pf, each when it has quota; Claude Sonnet is the last resort.)"
    echo "or keep Claude and pass --claude-because \"<reason>\" (logged to $LANE_TIER_LOG). Accepted reasons: needs Docker, needs wsl.exe, needs a live service, agy out of usage, review or planning (strongest tier), not code."
  } >&2
  return 1
}

[ $# -eq 4 ] || { usage >&2; exit 1; }
name="$1"; dir="$2"; model="$3"; prompt="$4"

[[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "name must be letters, digits, . _ - only: $name" >&2; exit 1; }
[ -d "$dir" ] || { echo "no such folder: $dir" >&2; exit 1; }
[ -r "$prompt" ] || { echo "no such prompt file: $prompt" >&2; exit 1; }
dir="$(cd "$dir" && pwd -P)"
prompt="$(cd "$(dirname "$prompt")" && pwd -P)/$(basename "$prompt")"

if [ "$engine" != claude ] && [ "$console" = 1 ]; then
  echo "--console applies only to the Claude engine" >&2
  exit 1
fi
if [ "$engine" != claude ] && [ "$headless" = 1 ]; then
  echo "--headless applies only to Claude with --console; Codex, Copilot, agy and opencode are already headless" >&2
  exit 1
fi
[ "$engine" != claude ] || site_tier_ok "$model" "$prompt" || exit 1
[ "$engine" != claude ] || mail_tier_ok "$model" "$name" "$prompt" || exit 1
[ "$engine" != claude ] || high_tier_ok "$model" "$name" "$prompt" || exit 1
guard_external_engine || exit 1
agy_default_ok "$name" "$dir" "$model" "$prompt" || exit 1

# A start gets today's dated folder, and its prompt.md is write-once (see lprompt below). A lane name is reused for new work (a client's mail
# batch starts "<client>-mail" again each time), so a later day without --restart is a new lane: a fresh folder with its own prompt.md and no
# restart header. --restart is the explicit restart of the same lane: it reuses the previous row's folder (when that folder still exists), so
# prompt.md, handoff.md and report.md stay together, and it keeps the lane's last recycle line (28 Oct 2026: a next-day restart split
# bandwagon-live across two folders, and a restart reset 120K to 100K so the lane recycled two minutes later).
prev_row="$(index_row "$name")"; prev_rk=
hand="$HOME/.claude/state/lanes/$(date +%F)-$name"
if [ -n "$prev_row" ]; then
  prev_rep="$(cut -f7 <<<"$prev_row")"; prev_dir=
  [ -n "$prev_rep" ] && prev_dir="$(dirname "$prev_rep")"
  if [ "$restart_opt" = 1 ] && [ -n "$prev_dir" ] && [ -d "$prev_dir" ]; then hand="$prev_dir"; fi
  # The recycle line carries over only when this start lands in the previous row's folder (--restart, or a same-day start).
  [ "$hand" = "$prev_dir" ] && prev_rk="$(cut -f12 <<<"$prev_row")"
fi
hprompt="$hand/prompt.md"
report="$hand/report.md"
# lprompt is the file the lane reads as its first message. prompt.md is write-once: a different prompt for a folder that already has one is
# a restart note, stored as prompt-<HHMM>.md; the index keeps pointing at prompt.md, so every recycle re-primes from the original mission.
lprompt="$hprompt"; restart_note=0
if [ -e "$hprompt" ] && [ "$prompt" != "$hprompt" ] && ! cmp -s -- "$prompt" "$hprompt"; then
  restart_note=1
  lprompt="$hand/prompt-$(date +%H%M).md"
  [ ! -e "$lprompt" ] || lprompt="$hand/prompt-$(date +%H%M%S).md"
fi

under_repos=0
case "$dir/" in "$(cd "$HOME/repos" && pwd -P)/"*) under_repos=1 ;; esac

if [ "$dry" = 0 ]; then
  command -v tmux >/dev/null || { echo "tmux not installed" >&2; exit 1; }
  if tmux has-session -t "$SESSION" 2>/dev/null \
     && tmux list-windows -t "$SESSION" -F '#{window_name}' | grep -qx -- "$name"; then
    echo "a window named $name already exists in $SESSION; pick another name or kill it first" >&2
    exit 1
  fi
fi

restart_header() {  # the one line a restart note starts with, so the lane knows where its mission and last hand-off are
  printf 'This lane was restarted. Your original mission is %s and your last hand-off is %s/handoff.md (when it exists): read both first, then continue with the note below.\n\n' "$hprompt" "$hand"
}
if [ "$dry" = 1 ]; then
  echo "mkdir -p $hand"
  if [ "$restart_note" = 1 ]; then echo "# restart: $hprompt stays as it is; the new prompt is stored as $lprompt"; echo "cp $prompt $lprompt"
  elif [ "$prompt" != "$hprompt" ]; then echo "cp $prompt $hprompt"; fi
  [ "$engine" != agy ] || echo "# prepend the agy edit-only header to $lprompt"
else
  mkdir -p "$hand"
  if [ "$restart_note" = 1 ]; then { restart_header; cat -- "$prompt"; } >"$lprompt"
  elif [ "$prompt" != "$hprompt" ]; then cp "$prompt" "$hprompt"; fi
  # Once only: a restarted lane's prompt already in the hand-off folder keeps its one header.
  if [ "$engine" = agy ] && ! head -n1 -- "$lprompt" | grep -q '^You can edit files but cannot run commands'; then
    { printf 'You can edit files but cannot run commands. Make the change, then write %s/report.md: files changed, what each change does, and the exact commands the hub must run to test and commit. Open only files in this checkout and in %s: any other read is refused and ends the run, so leave the live copies a task names under ~/.claude alone.\n\n' "$hand" "$hand"
      cat -- "$lprompt"; } >"$lprompt.agy" && mv "$lprompt.agy" "$lprompt"
  fi
fi

# A repo gets its graphify graph when sustained work starts there; code-only extraction is free.
# Repos skipped on purpose are listed, one top-level path per line, in ~/.config/afa/graphify-skip.
if top="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)" && [ ! -d "$top/graphify-out" ] \
   && ! grep -qxF -- "$top" "$HOME/.config/afa/graphify-skip" 2>/dev/null; then
  echo "note: no graphify graph in this repo; for sprint work run /graphify-init (code-only, free) first" >&2
fi

if [ "$dry" = 0 ] && ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" -n "$name" -c "$dir"
elif [ "$dry" = 1 ]; then
  echo "# if session $SESSION is missing:"
  t new-session -d -s "$SESSION" -n "$name" -c "$dir"
  echo "# else:"
  t new-window -t "$SESSION:" -n "$name" -c "$dir"
else
  tmux new-window -t "$SESSION:" -n "$name" -c "$dir"
fi

lane_t0=$(date +%s)
target="$SESSION:$name"
eff="-"; flags=""; conn="-"; rk=""
if [ "$engine" = claude ]; then
  conn="$(lane_connectors "$name" "$prompt")"
  # A restart keeps the lane's last recycle line unless --recycle-at is given (28 Oct 2026: a restart reset 120K to 100K and the lane recycled two minutes later).
  rk="${recycle_at_opt:-$(lane_recycle_k "$prev_rk" "$prompt" "$dir" "$conn")}"
  eff="$(lane_effort "$model")"; flags="$(claude_lane_flags "$eff" "$conn" "$name" "$rk" "$dir" "${project_mcp_opt:--}")"; eff="${eff:--}"
fi
if [ "$engine" != claude ]; then
  out="$hand/out.log"
  [ "$dry" = 1 ] || : >"$out"
  prompt_cmd="\$(cat -- $(printf %q "$lprompt"))"
  out_cmd="$(printf %q "$out")"
  if [ "$engine" = codex ]; then
    # GIT_SSH_COMMAND: inside the sandbox's user namespace root-owned system files look
    # wrongly owned, so ssh rejects /etc/ssh/ssh_config.d/*; -F points it at the user config only.
    # --add-dir ~/.cache/docker-heavy: the shared docker-heavy.sh lock lives there, so Codex lanes
    # take the same lock as Claude/Copilot lanes. User-owned and not a git repo, so bwrap's .git guard is harmless.
    mkdir -p "$HOME/.cache/docker-heavy"
    cmd="GIT_SSH_COMMAND=\"ssh -F \$HOME/.ssh/config\" codex exec -C $(printf %q "$dir") -s workspace-write --add-dir $(printf %q "$hand") --add-dir $(printf %q "$HOME/.cache/docker-heavy")"
    cmd+=" -c sandbox_workspace_write.network_access=true"
    if ! is_claude_model "$model"; then cmd+=" -m $(printf %q "$model")"; fi
    cmd+=" \"$prompt_cmd\" >$out_cmd 2>&1"
  elif [ "$engine" = opencode ]; then
    cmd="cd $(printf %q "$dir") && $(printf %q "$SCRIPT_DIR/opencode_lane.sh") $(printf %q "$dir") $(printf %q "$hand") $(printf %q "$(opencode_model "$model")") $(printf %q "$lprompt")"
    cmd+=" >$out_cmd 2>&1"
  elif [ "$engine" = agy ]; then
    cmd="cd $(printf %q "$dir") && agy -p \"$prompt_cmd\" --mode accept-edits --sandbox --print-timeout 0 --add-dir $(printf %q "$hand") --model $(printf %q "$(agy_model "$model")")"
    cmd+=" >$out_cmd 2>&1"
  else
    cmd="cd $(printf %q "$dir") && copilot -p \"$prompt_cmd\" --allow-all-tools --log-dir $(printf %q "$hand") --add-dir $(printf %q "$hand")"
    if ! is_claude_model "$model"; then cmd+=" --model $(printf %q "$model")"; fi
    cmd+=" >$out_cmd 2>&1"
  fi
  cmd+="; lane_rc=\$?; printf 'exit %s\\n' \"\$lane_rc\" >>$out_cmd; exit \"\$lane_rc\""
elif [ "$headless" = 1 ]; then
  out="$hand/out.log"
  [ "$dry" = 1 ] || : >"$out"  # exists from the start, so --status knows the lane is headless
  cmd="{ $HOME/.claude/scripts/console_worker.sh --inner $(printf %q "$dir") -p --model $(printf %q "$model") $flags \"\$(cat $(printf %q "$lprompt"))\" >$(printf %q "$out") 2>&1; echo \"exit \$?\" >>$(printf %q "$out"); }"
elif [ "$console" = 1 ]; then
  cmd="$(lane_env_prefix "$name" "$hand")$HOME/.claude/scripts/console_worker.sh --inner $(printf %q "$dir") --model $(printf %q "$model") $flags \"\$(cat $(printf %q "$lprompt"))\""
else
  # --add-dir: same 28 Oct 2026 reason as on --resume (hand-off folder is outside the cwd).
  cmd="$(lane_env_prefix "$name" "$hand")claude --model $(printf %q "$model") --add-dir $(printf %q "$hand") $flags \"\$(cat $(printf %q "$lprompt"))\""
fi
t send-keys -t "$target" "$cmd" Enter

if [ "$engine" != claude ] || [ "$headless" = 1 ]; then
  echo "# headless: no dialogs to answer; progress in $hand/out.log"
elif [ "$dry" = 1 ]; then
  if [ "$under_repos" = 1 ]; then
    echo "# then poll capture-pane up to ~10 s; on \"Yes, I trust this folder\":"
    t send-keys -t "$target" Down Enter
  else
    echo "# $dir is outside ~/repos: a trust prompt would be left for the user"
  fi
  [ "$console" = 1 ] && { echo "# with --console, on \"Do you want to use this API key\": pick Yes:"; t send-keys -t "$target" Enter; }
  echo "# on \"Teach auto mode about your environment?\":"
  t send-keys -t "$target" Escape
else
  # Dialogs can arrive in sequence, so keep polling; stop after 8 quiet polls once one was answered.
  answered=0; quiet=0
  for _ in $(seq 1 40); do
    sleep 0.5
    pane="$(tmux capture-pane -p -t "$target" 2>/dev/null || true)"
    hit=0
    if grep -q "Yes, I trust this folder" <<<"$pane"; then
      if [ "$under_repos" = 1 ]; then
        # Enter takes the highlighted choice; only press Down if the cursor is not on "Yes".
        if grep -E "Yes, I trust this folder" <<<"$pane" | grep -q '^[[:space:]]*[❯>]'; then
          tmux send-keys -t "$target" Enter
        else
          tmux send-keys -t "$target" Down Enter
        fi
        echo "answered the folder-trust prompt (folder is under ~/repos)"
        hit=1
      else
        echo "folder-trust prompt is waiting in $target: $dir is outside ~/repos, so the user must answer it"
        break
      fi
    elif [ "$console" = 1 ] && grep -q "Do you want to use this API key" <<<"$pane"; then
      # The default cursor is on "No"; choose "Yes".
      if grep -E "Yes" <<<"$pane" | grep -q '^[[:space:]]*[❯>]'; then
        tmux send-keys -t "$target" Enter
      else
        tmux send-keys -t "$target" Up Enter
      fi
      echo "answered the API-key dialog with Yes (--console)"
      hit=1
    elif grep -q "Teach auto mode about your environment" <<<"$pane"; then
      tmux send-keys -t "$target" Escape
      echo "dismissed the auto-mode opt-in dialog"
      hit=1
    fi
    if [ "$hit" = 1 ]; then answered=1; quiet=0; sleep 1
    elif [ "$answered" = 1 ]; then quiet=$((quiet+1)); [ "$quiet" -ge 8 ] && break
    fi
  done
fi

# Record the lane so a later hub session can find, check and resume it.
if [ "$dry" = 0 ]; then
  start_epoch=$lane_t0  # transcripts created from here on belong to this lane
  mkdir -p "$(dirname "$INDEX")"
  sid=""
  for _ in $(seq 1 10); do
    sid="$(proven_session "$name" "$start_epoch")"
    [ -n "$sid" ] && break; sleep 1
  done
  if [ "$engine" != claude ]; then lane_billing="$engine"
  elif [ "$console" = 1 ]; then lane_billing=console
  else lane_billing=subscription; fi
  lane_ent=-; [ "$engine" != claude ] || lane_ent="$(high_ent "$model" "$prompt")"
  index_append "$(printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' "$(date +%F)" "$name" "$dir" "$model" \
    "$lane_billing" "$hprompt" "$report" "${sid:--}" "$start_epoch" "$eff" "$conn" "${rk:--}" "$lane_ent" "${project_mcp_opt:--}")"
else
  echo "# would append a row to $INDEX"
fi

# An OpenClaw ask: register this lane under it so the result reaches the chat (and the fallback does not start the job again).
if [ -n "$ask_id" ]; then
  if [ "$dry" = 1 ]; then
    echo "# would run: openclaw_dispatch.py claim $ask_id $name"
  else
    claim_py="${AFA_OPENCLAW_DISPATCH:-$HOME/repos/Claude_Commandz/global/.claude/scripts/openclaw_dispatch.py}"
    if claim_out="$(python3 "$claim_py" claim "$ask_id" "$name" 2>&1)"; then echo "claim:  $claim_out"
    else echo "WARNING: the lane is running but claiming ask $ask_id failed: $claim_out" >&2; fi
  fi
fi

echo "lane:   $name  ($model, effort $eff, connectors $conn, $dir)"
echo "prompt: $hprompt"
echo "report: $report"
if [ "$headless" = 1 ] || [ "$engine" != claude ]; then echo "log:    $hand/out.log"; fi
echo "watch:  tmux attach -t $SESSION   (Ctrl-b n / p to switch windows, Ctrl-b d to detach)"
