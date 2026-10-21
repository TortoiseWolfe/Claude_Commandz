#!/usr/bin/env bash
# Start a lane in its own tmux window. Claude Code is the default engine; Codex and
# GitHub Copilot can be selected with --engine.
#
#   lane.sh [--dry-run] [--engine claude|codex|copilot] [--effort <level>] [--connectors] [--allow-client] [--allow-low-tier] [--console [--headless]] [--ask <id>] <name> <dir> <model> <prompt-file>
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
#
# 3D work tier (his rule, 19 Oct): a Claude lane whose prompt file names data/sites/ or site-model (3D fitting, photo
# matching, site models) starts or resumes only on fable or opus, each at effort high.
# sonnet, haiku or any other model name is refused, on start, --console, --headless and --resume alike. --allow-low-tier bypasses it.
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
#   lane.sh --recycle-at <K> ...        at start or --resume: this lane's recycle line (40 to 150, default by kind: see default_recycle_k)
#   lane.sh --turn <name>               what its last finished turn was: monitor (a Monitor progress event that asked nothing; lane_watch.sh does
#                                       not wake the hub for it), real, or ? (see lane_turn_kind.py)
# Every start appends one row to ~/.claude/state/lanes/index.tsv (tab-separated, no header):
#   date  name  dir  model  billing(console|subscription; codex|copilot)  prompt  report  session-id  start-epoch  effort(- if none)  connectors(on|off|- )  recycle-at(K tokens; empty = default for its kind)
# Rows from before the connectors column have nothing in it; they were started with connectors loaded and count as on.
# The session id is the earliest transcript under ~/.claude/projects/<dir-slug>/ created after the start
# and not claimed by another row; if it is not there yet at start, the next --status fills it in.
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
#     file exists; a repo's own CLAUDE.md files load as usual.
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
# The workspace CLAUDE.md lanes skip, and the slim rule file that stands in for it.
LANE_WORKSPACE_MD="${LANE_WORKSPACE_MD:-$HOME/repos/CLAUDE.md}"
LANE_RULES="${LANE_RULES:-$HOME/repos/hub/lane-rules.md}"

INDEX="$HOME/.claude/state/lanes/index.tsv"
PROJ="$HOME/.claude/projects"
TAB=$'\t'

# A lane the OpenClaw dispatcher started (AFA_LANE_UNATTENDED=1, or a name starting oc-) has nobody at its window. Its Claude
# process gets AFA_LANE_UNATTENDED=1 and AFA_LANE_HAND=<its hand-off folder>, which the afa-mods Blast Radius guard reads: such a
# lane is never asked "Run it?", it is allowed a delete inside its own folder, a worktree or the scratchpad and refused the rest.
lane_env_prefix() {
  if [ "${AFA_LANE_UNATTENDED:-}" = 1 ] || [[ "$1" == oc-* ]]; then
    printf 'AFA_LANE_UNATTENDED=1 AFA_LANE_HAND=%q ' "$2"
  fi
}

usage() {
  cat <<'USAGE'
Usage:
  lane.sh [--dry-run] [--engine claude|codex|copilot] [--effort <level>] [--connectors|--no-connectors] [--recycle-at <K>] [--allow-client] [--allow-low-tier] [--console [--headless]] [--ask <oc-id>] <name> <dir> <model> <prompt-file>
  lane.sh --status
  lane.sh --send <name> <text>
  lane.sh [--dry-run] [--console] [--effort <level>] [--connectors] [--allow-low-tier] --resume <name>
  lane.sh [--dry-run] --recycle <name>
  lane.sh --ctx <name>
  lane.sh --recycle-point <name>
  (start and --resume also take --recycle-at <K>)

Claude is the default engine. Codex and Copilot run headless and write to out.log.
Their model flag is used only when <model> is not a Claude name.
Codex and Copilot refuse repositories listed in client-senders.tsv or client-repos.txt,
and folders under ~/repos/CD. client-repos.txt has one path per line. Blank lines
and lines starting with # are ignored. Both list files must be regular readable files; pass --allow-client to continue.
--headless is only for Claude with --console.
--ask <oc-id> claims an OpenClaw ask for this lane (its result then reaches Jonathan's OpenClaw chat).
Claude lanes default to --effort high on sonnet, opus and fable, none on haiku;
they are recycled at their own line (--recycle-at <K>, default by kind of work) and compact in place only as a backstop above it.
Claude lanes start without the claude.ai connectors, the workspace CLAUDE.md and a set of plugins.
--connectors keeps connectors; --no-connectors disables them even when the name or prompt would enable them.
A Claude lane whose prompt names data/sites/ or site-model (3D and photo-match work) runs only on fable or opus;
sonnet and haiku are refused there. --allow-low-tier overrides that.
USAGE
}

dry=0; console=0; headless=0; allow_client=0; engine=claude; mode=start; effort_opt=; connectors_opt=0; no_connectors=0; ask_id=; allow_low_tier=0; recycle_at_opt=; recycle_at_set=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --help|-h) usage; exit 0 ;;
    --engine)
      [ $# -ge 2 ] || { echo "--engine requires claude, codex, or copilot" >&2; exit 1; }
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
    --ask)
      [ $# -ge 2 ] || { echo "--ask requires an ask id (oc-xxxxxxxx)" >&2; exit 1; }
      ask_id="$2"; shift 2 ;;
    --ask=*) ask_id="${1#*=}"; shift ;;
    --allow-client) allow_client=1; shift ;;
    --allow-low-tier) allow_low_tier=1; shift ;;
    --connectors) connectors_opt=1; no_connectors=0; shift ;;
    --no-connectors) no_connectors=1; connectors_opt=0; shift ;;
    --console) console=1; shift ;;
    --headless) headless=1; shift ;;
    --status) mode=status; shift ;;
    --send) mode=send; shift ;;
    --recycle) mode=recycle; shift ;;
    --ctx) mode=ctx; shift ;;
    --recycle-point) mode=point; shift ;;
    --turn) mode=turn; shift ;;
    --resume) mode=resume; shift ;;
    *) break ;;
  esac
done

case "$engine" in
  claude|codex|copilot) ;;
  *) echo "unknown engine: $engine (choose claude, codex, or copilot)" >&2; exit 1 ;;
esac

case "$effort_opt" in
  ''|low|medium|high|xhigh|max) ;;
  *) echo "unknown effort: $effort_opt (choose low, medium, high, xhigh, or max)" >&2; exit 1 ;;
esac

if [ "$recycle_at_set" = 1 ]; then
  [[ "$recycle_at_opt" =~ ^[0-9]+$ ]] && [ "$recycle_at_opt" -ge 40 ] && [ "$recycle_at_opt" -le 150 ] \
    || { echo "--recycle-at needs a whole number of K tokens from 40 to 150 (Claude Code compacts at about 168K), got: $recycle_at_opt" >&2; exit 1; }
  case "$mode" in start|resume) ;; *) echo "--recycle-at only applies when starting or resuming a lane" >&2; exit 1 ;; esac
fi
[ -z "$ask_id" ] || [[ "$ask_id" =~ ^oc-[0-9a-f]{8}$ ]] || { echo "--ask needs an id like oc-1a2b3c4d, got: $ask_id" >&2; exit 1; }
[ -z "$ask_id" ] || [ "$mode" = start ] || { echo "--ask only applies when starting a lane" >&2; exit 1; }
[ "$headless" = 0 ] || [ "$console" = 1 ] || { echo "--headless is only valid with --console" >&2; exit 1; }
[ "$headless" = 0 ] || [ "$mode" = start ] || { echo "--headless only applies when starting a lane" >&2; exit 1; }

# Run a tmux command, or just print it under --dry-run.
t() { if [ "$dry" = 1 ]; then printf 'tmux'; printf ' %q' "$@"; echo; else tmux "$@"; fi; }

# Earliest transcript for <dir> created at/after <start-epoch> whose id is not in <claimed> (space-separated).
find_session() {
  local dir="$1" start="$2" claimed="$3" slug f b id
  slug="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$dir")"
  [ -d "$PROJ/$slug" ] || return 0
  while IFS= read -r f; do
    b="$(stat -c %W "$f")"; [ "$b" -gt 0 ] || b="$(stat -c %Y "$f")"
    [ "$b" -ge $((start - 2)) ] || continue
    id="$(basename "$f" .jsonl)"
    case " $claimed " in *" $id "*) continue ;; esac
    echo "$id"; return 0
  done < <(ls -tr "$PROJ/$slug"/*.jsonl 2>/dev/null)
}

# Fill blank session ids in the index (column 8) where a transcript can be found.
fill_ids() {
  [ -s "$INDEX" ] || return 0
  local tmp claimed d n dir m b p r id st eff conn rk
  tmp="$(mktemp)"
  claimed="$(cut -f8 "$INDEX" | tr '\n' ' ')"
  while IFS="$TAB" read -r d n dir m b p r id st eff conn rk; do
    if { [ -z "$id" ] || [ "$id" = - ]; } && [ "${st:--}" != - ]; then
      id="$(find_session "$dir" "$st" "$claimed")"
      [ -n "$id" ] && claimed="$claimed $id"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$d" "$n" "$dir" "$m" "$b" "$p" "$r" "${id:--}" "${st:--}" "${eff:-?}" "${conn:--}" "${rk:-}" >>"$tmp"
  done <"$INDEX"
  cat "$tmp" >"$INDEX"; rm -f "$tmp"
}

# Latest index row for a lane name (tab-separated line), or nothing.
index_row() { [ -s "$INDEX" ] && awk -F'\t' -v n="$1" '$2==n{r=$0} END{if(r)print r}' "$INDEX" || true; }

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
default_recycle_k() {  # <kind> -> K tokens
  case "$1" in
    ops) echo 110 ;;
    mail|code|3d) echo 100 ;;
    *) echo 100 ;;
  esac
}
lane_recycle_k() {  # <stored value or empty> <prompt-file> <dir> <connectors> -> K tokens
  if [[ "${1:-}" =~ ^[0-9]+$ ]]; then echo "$1"; else default_recycle_k "$(lane_kind "${2:-}" "${3:-}" "${4:--}")"; fi
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
claude_lane_flags() {  # <effort or empty> <connectors on|off> [lane name] [recycle K]
  local json p s r
  # Keep the hub's own lean project settings (hub_lean.py) current: any machine that starts a lane gets them.
  [ "$dry" = 1 ] || python3 -I "$(dirname "$0")/hub_lean.py" --install >/dev/null 2>&1 || true
  [ -z "$1" ] || printf -- '--effort %q ' "$1"
  json='{"autoCompactWindow":'"$(window_for "${4:-100}")"
  [ "$2" = on ] || json+=',"disableClaudeAiConnectors":true'
  [ -f "$LANE_RULES" ] && json+=",\"claudeMdExcludes\":[\"$LANE_WORKSPACE_MD\"]"
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
  case "$1" in codex|copilot) return 0 ;; *) return 1 ;; esac
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
      IFS="$TAB" read -r d n dir m b p rep id st eff conn rk <<<"${row:-}"
      if [ -z "$row" ]; then
        rep="$(ls -t "$HOME"/.claude/state/lanes/*-"$w"/report.md 2>/dev/null | head -1 || true)"
        dir="$(tmux display -p -t "$SESSION:$w" '#{pane_current_path}')"; m="?"; id="?"
        extra="(not in index)"
      else extra=""; fi
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
      case "${b:-subscription}" in codex|copilot) ctx="n/a" ;; *) { [ -n "${id:-}" ] && [ "$id" != - ] && [ "$id" != "?" ]; } && ctx="$(ctx_k "$dir" "$id")" ;; esac
      case "${b:-subscription}" in codex|copilot) cn="n/a" ;; *) if [ -z "$row" ]; then cn="?"; else cn="${conn:--}"; [ "$cn" = - ] && cn=on; fi ;; esac
      [ -z "$why" ] || printf '    report stale: %s\n' "$([ "$why" = input ] && echo 'the lane got input after report.md was written; the report is not its last word' || echo 'a background job it started has not finished')"
      printf '    dir:%s model:%s effort:%s connectors:%s context:%s engine/billing:%s session:%s %s\n' "${dir:-?}" "${m:-?}" "${eff:-?}" "$cn" "$ctx" "${b:-subscription}" "$([ "${id:--}" = - ] && echo unknown || echo "$id")" "$extra"
    done < <(tmux list-windows -t "$SESSION" -F '#{window_name}')
  else
    echo "(no tmux session \"$SESSION\")"
  fi
  # Index lanes from the last 3 days with no window and no report.
  if [ -s "$INDEX" ]; then
    cutoff="$(date -d '3 days ago' +%F)"
    while IFS="$TAB" read -r d n dir m b p rep id st eff conn rk; do
      [[ "$d" < "$cutoff" ]] && continue
      case "$live" in *" $n "*) continue ;; esac
      [ -n "${rep:-}" ] && [ -s "$rep" ] && continue
      printf '%-18s dead, resume with: lane.sh --resume %s   (%s, %s, engine/billing %s, session %s)\n' "$n" "$n" "$dir" "$m" "${b:-subscription}" "$([ "${id:--}" = - ] && echo unknown || echo "$id")"
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
    IFS="$TAB" read -r d n dir m b p rep id st eff conn rk <<<"$row"
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
  tmux send-keys -t "$target" -l -- "$text"
  sleep 0.3
  tmux send-keys -t "$target" C-m
  how=""
  if took 6; then how="submitted"; else tmux send-keys -t "$target" C-m; if took 6; then how="submitted after a second Enter"; fi; fi
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
if [ "$mode" = send ]; then send_to "$@"; exit $?; fi

# --recycle-point <name>: the lane's own recycle line in K tokens, as a bare number (lane_watch.sh reads it for each lane it watches).
if [ "$mode" = point ]; then
  [ $# -eq 1 ] || { echo "usage: lane.sh --recycle-point <name>" >&2; exit 1; }
  row="$(index_row "$1")"
  if [ -z "$row" ]; then default_recycle_k code; exit 0; fi
  IFS="$TAB" read -r d n dir m b p rep id st eff conn rk <<<"$row"
  lane_recycle_k "${rk:-}" "${p:-}" "${dir:-}" "${conn:--}"
  exit 0
fi

# --ctx <name>: the lane's context in K tokens as a bare number, or ? when it cannot be read (lane_watch.sh uses it against the recycle line).
# --turn <name>: monitor, real or ? for the lane's last finished turn (lane_watch.sh skips waking the hub for a Monitor tick that asked nothing).
if [ "$mode" = ctx ] || [ "$mode" = turn ]; then
  [ $# -eq 1 ] || { echo "usage: lane.sh --$mode <name>" >&2; exit 1; }
  row="$(index_row "$1")"
  [ -n "$row" ] || { echo "?"; exit 0; }
  IFS="$TAB" read -r d n dir m b p rep id st eff conn rk <<<"$row"
  case "${b:-subscription}" in codex|copilot) echo "?"; exit 0 ;; esac
  # A lane just started has no session id in its row yet (--status fills it in): look for its transcript without rewriting the index.
  if { [ -z "${id:-}" ] || [ "$id" = - ]; } && [ "${st:--}" != - ]; then
    id="$(find_session "$dir" "$st" "$(cut -f8 "$INDEX" | tr '\n' ' ')")"
  fi
  { [ -n "${id:-}" ] && [ "$id" != - ]; } || { echo "?"; exit 0; }
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
  IFS="$TAB" read -r d n dir m b p rep id st eff conn rk <<<"$row"
  if is_headless_billing "${b:-}" || [ -e "$(dirname "${rep:-/nonexistent}")/out.log" ]; then
    echo "$name is a headless lane; there is no window to recycle" >&2; exit 1
  fi
  [ -n "${rep:-}" ] || { echo "$name has no hand-off folder recorded in $INDEX" >&2; exit 1; }
  hand="$(dirname "$rep")"; handoff="$hand/handoff.md"
  ctxk="$([ -n "${id:-}" ] && [ "$id" != - ] && ctx_k "$dir" "$id" || echo '?')"
  rk_now="$(lane_recycle_k "${rk:-}" "${p:-}" "$dir" "${conn:--}")"
  rk_next="$rk_now"
  if [[ "${st:-}" =~ ^[0-9]+$ ]] && [ $(( $(date +%s) - st )) -lt "${LANE_RECYCLE_THRASH_SECS:-1200}" ]; then
    rk_next=$(( rk_now + 20 )); rk_ceiling=$(( ${LANE_WINDOW_CAP:-200000} * 84 / 100000 - 40 ))
    [ "$rk_next" -le "$rk_ceiling" ] || rk_next=$(( rk_ceiling > rk_now ? rk_ceiling : rk_now ))
  fi
  msg_handoff="Hub: your context is at $ctxk, past your recycle line (${rk_now}K), and Claude Code compacts on its own not far above it. Finish only the step in hand (the tool call you are in), start no new step, and make your very next action writing $handoff: what is done, what is in flight, the next step, and what to re-arm (timers, background jobs, watchers). Then stop, even mid-task, and say \"ready to recycle\". Once it is written I clear your context and send you back to prompt.md and handoff.md."
  msg_reprime="Hub: your context was cleared (recycle, not a restart). Read $p, then $handoff, then carry on from the next step it names and re-arm what it lists. Your hand-off folder is unchanged: $hand."
  if [ "$dry" = 1 ]; then
    echo "recycle $name ($dir, context $ctxk):"
    echo "  1. wait only for any dialog on its screen to clear (up to ${LANE_RECYCLE_IDLE_SECS:-300}s); a lane mid-turn is not waited for"
    echo "  2. send: $msg_handoff"
    echo "  3. wait for $handoff to change and the lane to go idle (up to ${LANE_RECYCLE_WAIT_SECS:-600}s); without it the lane is not cleared"
    echo "  4. send: /clear"
    echo "  5. send: $msg_reprime"
    echo "  6. append an index row to $INDEX with the lane's new session id and recycle line ${rk_next}K"
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
  wait_no_dialog() {  # <secs>: no dialog on screen; a working lane is fine, the request queues into its turn
    local end=$(( $(date +%s) + $1 )) pane
    while [ "$(date +%s)" -lt "$end" ]; do
      pane="$(tmux capture-pane -p -t "$SESSION:$name" 2>/dev/null)" || return 1
      printf '%s\n' "$pane" | tail -15 | command grep -qE 'Enter to select|Do you want to proceed|Esc to cancel' || return 0
      sleep 2
    done
    return 1
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

  wait_no_dialog "${LANE_RECYCLE_IDLE_SECS:-300}" || rfail "a dialog stayed on the lane's screen for ${LANE_RECYCLE_IDLE_SECS:-300}s, so the hand-off request was not typed"
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

  # /clear starts no turn, so there is no spinner to wait for: type it, press Enter, and wait for the input line to empty.
  cpane=""
  tmux send-keys -t "$SESSION:$name" -l -- "/clear"; sleep 0.5; tmux send-keys -t "$SESSION:$name" C-m
  for _ in $(seq 1 20); do
    sleep 0.5; cpane="$(tmux capture-pane -e -p -t "$SESSION:$name")"
    pane_input_empty "$cpane" && break
  done
  pane_input_empty "$cpane" || rfail "/clear stayed in the input line"
  sleep 2
  send_to "$name" "$msg_reprime" >/dev/null || rfail "the lane was cleared but the re-prime message did not reach it; send it by hand: $msg_reprime"

  claimed="$(cut -f8 "$INDEX" | tr '\n' ' ')"; newid=""
  for _ in $(seq 1 20); do
    newid="$(find_session "$dir" "$t0" "$claimed")"; [ -n "$newid" ] && break; sleep 1
  done
  [ -n "$newid" ] || rfail "the lane was re-primed but no new transcript was found under $PROJ to record"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date +%F)" "$name" "$dir" "$m" "${b:-subscription}" "$p" "$rep" "$newid" "$t0" "${eff:--}" "${conn:--}" "$rk_next" >>"$INDEX"
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
  IFS="$TAB" read -r d n dir m b p rep id st eff conn rk <<<"$row"
  if is_headless_billing "${b:-}"; then
    echo "$name is a $b headless lane; start a new lane with a follow-up prompt instead of resuming it" >&2
    exit 1
  fi
  [ "$engine" = claude ] || { echo "--engine only applies when starting a lane; lane resume uses Claude Code" >&2; exit 1; }
  { [ -n "${id:-}" ] && [ "$id" != - ]; } || { echo "no session id recorded for $name; find it under $PROJ and add it to $INDEX" >&2; exit 1; }
  [ -d "$dir" ] || { echo "no such folder: $dir" >&2; exit 1; }
  if [ -e "$(dirname "${rep:-/nonexistent}")/out.log" ]; then
    echo "$name is a headless lane (out.log beside its report); there is no interactive session to resume. Read $(dirname "$rep")/out.log" >&2; exit 1
  fi
  [ "$b" = console ] && console=1
  site_tier_ok "$m" "${p:-}" || exit 1
  if [ "$dry" = 0 ]; then
    command -v tmux >/dev/null || { echo "tmux not installed" >&2; exit 1; }
    if tmux has-session -t "$SESSION" 2>/dev/null \
       && tmux list-windows -t "$SESSION" -F '#{window_name}' | command grep -qx -- "$name"; then
      echo "a window named $name already exists in $SESSION" >&2; exit 1
    fi
  fi
  eff="$(lane_effort "$m" "${eff:-}")"
  # Reuse the recorded connectors choice; a row from before the column was started with them on.
  if [ "$connectors_opt" = 1 ] || [ "${conn:--}" != off ]; then conn=on; else conn=off; fi
  rk="${recycle_at_opt:-$(lane_recycle_k "${rk:-}" "${p:-}" "$dir" "${conn:--}")}"
  flags="$(claude_lane_flags "$eff" "$conn" "$name" "$rk")"
  uenv="$(lane_env_prefix "$name" "$(dirname "${rep:-$HOME/.claude/state/lanes/$d-$name/report.md}")")"
  if [ "$console" = 1 ]; then
    cmd="${uenv}$HOME/.claude/scripts/console_worker.sh --inner $(printf %q "$dir") --resume $(printf %q "$id") --model $(printf %q "$m") $flags"
  else
    cmd="${uenv}claude --resume $(printf %q "$id") --model $(printf %q "$m") $flags"
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
  echo "--headless applies only to Claude with --console; Codex and Copilot are already headless" >&2
  exit 1
fi
[ "$engine" != claude ] || site_tier_ok "$model" "$prompt" || exit 1
guard_external_engine || exit 1

hand="$HOME/.claude/state/lanes/$(date +%F)-$name"
hprompt="$hand/prompt.md"
report="$hand/report.md"

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

if [ "$dry" = 1 ]; then
  echo "mkdir -p $hand"
  [ "$prompt" = "$hprompt" ] || echo "cp $prompt $hprompt"
else
  mkdir -p "$hand"
  [ "$prompt" = "$hprompt" ] || cp "$prompt" "$hprompt"
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
  rk="${recycle_at_opt:-$(default_recycle_k "$(lane_kind "$prompt" "$dir" "$conn")")}"
  eff="$(lane_effort "$model")"; flags="$(claude_lane_flags "$eff" "$conn" "$name" "$rk")"; eff="${eff:--}"
fi
if [ "$engine" != claude ]; then
  out="$hand/out.log"
  [ "$dry" = 1 ] || : >"$out"
  prompt_cmd="\$(cat -- $(printf %q "$hprompt"))"
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
  else
    cmd="cd $(printf %q "$dir") && copilot -p \"$prompt_cmd\" --allow-all-tools --log-dir $(printf %q "$hand") --add-dir $(printf %q "$hand")"
    if ! is_claude_model "$model"; then cmd+=" --model $(printf %q "$model")"; fi
    cmd+=" >$out_cmd 2>&1"
  fi
  cmd+="; lane_rc=\$?; printf 'exit %s\\n' \"\$lane_rc\" >>$out_cmd; exit \"\$lane_rc\""
elif [ "$headless" = 1 ]; then
  out="$hand/out.log"
  [ "$dry" = 1 ] || : >"$out"  # exists from the start, so --status knows the lane is headless
  cmd="{ $HOME/.claude/scripts/console_worker.sh --inner $(printf %q "$dir") -p --model $(printf %q "$model") $flags \"\$(cat $(printf %q "$hprompt"))\" >$(printf %q "$out") 2>&1; echo \"exit \$?\" >>$(printf %q "$out"); }"
elif [ "$console" = 1 ]; then
  cmd="$(lane_env_prefix "$name" "$hand")$HOME/.claude/scripts/console_worker.sh --inner $(printf %q "$dir") --model $(printf %q "$model") $flags \"\$(cat $(printf %q "$hprompt"))\""
else
  cmd="$(lane_env_prefix "$name" "$hand")claude --model $(printf %q "$model") $flags \"\$(cat $(printf %q "$hprompt"))\""
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
    sid="$(find_session "$dir" "$start_epoch" "$( [ -s "$INDEX" ] && cut -f8 "$INDEX" | tr '\n' ' ' || true)")"
    [ -n "$sid" ] && break; sleep 1
  done
  if [ "$engine" != claude ]; then lane_billing="$engine"
  elif [ "$console" = 1 ]; then lane_billing=console
  else lane_billing=subscription; fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date +%F)" "$name" "$dir" "$model" \
    "$lane_billing" "$hprompt" "$report" "${sid:--}" "$start_epoch" "$eff" "$conn" "${rk:-}" >>"$INDEX"
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
