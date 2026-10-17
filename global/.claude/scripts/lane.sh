#!/usr/bin/env bash
# Start a lane in its own tmux window. Claude Code is the default engine; Codex and
# GitHub Copilot can be selected with --engine.
#
#   lane.sh [--dry-run] [--engine claude|codex|copilot] [--effort <level>] [--connectors] [--allow-client] [--console [--headless]] [--ask <id>] <name> <dir> <model> <prompt-file>
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
# --ask <oc-id>: this lane answers an OpenClaw ask ("Jonathan via OpenClaw:", ask oc-xxxxxxxx). Once the window is up and the lane is in
# index.tsv, lane.sh runs `openclaw_dispatch.py claim <id> <name>`, which registers the lane so its finish, failure or stall is
# posted to Jonathan's OpenClaw chat, and cancels the five-minute fallback that would otherwise start the job a second time.
# A claim that fails prints a warning and does not undo the lane.
#
# Hand-off across hub sessions (lanes outlive the hub terminal; only WSL shutting down stops them):
#   lane.sh --status                    one line per window in "lanes" (report yes/no, working/idle/error,
#                                       last pane line, dir, model, engine/billing, session id) plus lanes with no window
#                                       and no report from the last 3 days
#   lane.sh --send <name> <text>        type <text> into the window, Enter, confirm it was submitted
#   lane.sh [--dry-run] [--console] --resume <name>
#                                       fresh window running claude --resume <session-id> --model <m> in <dir>
# Every start appends one row to ~/.claude/state/lanes/index.tsv (tab-separated, no header):
#   date  name  dir  model  billing(console|subscription; codex|copilot)  prompt  report  session-id  start-epoch  effort(- if none)  connectors(on|off|- )
# Rows from before the connectors column have nothing in it; they were started with connectors loaded and count as on.
# The session id is the earliest transcript under ~/.claude/projects/<dir-slug>/ created after the start
# and not claimed by another row; if it is not there yet at start, the next --status fills it in.
#
# Effort and compaction (Claude lanes only, including --console, --headless and --resume):
# every Claude lane gets --effort <level> and a --settings JSON with autoCompactWindow 200000, so a
# lane compacts in place instead of growing to 300K. Default effort by model: sonnet, opus and
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
  lane.sh [--dry-run] [--engine claude|codex|copilot] [--effort <level>] [--connectors|--no-connectors] [--allow-client] [--console [--headless]] [--ask <oc-id>] <name> <dir> <model> <prompt-file>
  lane.sh --status
  lane.sh --send <name> <text>
  lane.sh [--dry-run] [--console] [--effort <level>] [--connectors] --resume <name>

Claude is the default engine. Codex and Copilot run headless and write to out.log.
Their model flag is used only when <model> is not a Claude name.
Codex and Copilot refuse repositories listed in client-senders.tsv or client-repos.txt,
and folders under ~/repos/CD. client-repos.txt has one path per line. Blank lines
and lines starting with # are ignored. Both list files must be regular readable files; pass --allow-client to continue.
--headless is only for Claude with --console.
--ask <oc-id> claims an OpenClaw ask for this lane (its result then reaches Jonathan's OpenClaw chat).
Claude lanes default to --effort high on sonnet, opus and fable, none on haiku;
they also compact in place at 200K tokens.
Claude lanes start without the claude.ai connectors, the workspace CLAUDE.md and a set of plugins.
--connectors keeps connectors; --no-connectors disables them even when the name or prompt would enable them.
USAGE
}

dry=0; console=0; headless=0; allow_client=0; engine=claude; mode=start; effort_opt=; connectors_opt=0; no_connectors=0; ask_id=
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
    --ask)
      [ $# -ge 2 ] || { echo "--ask requires an ask id (oc-xxxxxxxx)" >&2; exit 1; }
      ask_id="$2"; shift 2 ;;
    --ask=*) ask_id="${1#*=}"; shift ;;
    --allow-client) allow_client=1; shift ;;
    --connectors) connectors_opt=1; no_connectors=0; shift ;;
    --no-connectors) no_connectors=1; connectors_opt=0; shift ;;
    --console) console=1; shift ;;
    --headless) headless=1; shift ;;
    --status) mode=status; shift ;;
    --send) mode=send; shift ;;
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
  local tmp claimed d n dir m b p r id st eff conn
  tmp="$(mktemp)"
  claimed="$(cut -f8 "$INDEX" | tr '\n' ' ')"
  while IFS="$TAB" read -r d n dir m b p r id st eff conn; do
    if { [ -z "$id" ] || [ "$id" = - ]; } && [ "${st:--}" != - ]; then
      id="$(find_session "$dir" "$st" "$claimed")"
      [ -n "$id" ] && claimed="$claimed $id"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$d" "$n" "$dir" "$m" "$b" "$p" "$r" "${id:--}" "${st:--}" "${eff:-?}" "${conn:--}" >>"$tmp"
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

# Flags every Claude lane gets: effort (when the model takes one), one --settings JSON (compaction at 200K,
# plugins off, connectors off unless <connectors> is on, the workspace CLAUDE.md excluded) and the slim rules.
# The workspace CLAUDE.md is excluded only when the rule file exists to replace it.
claude_lane_flags() {  # <effort or empty> <connectors on|off> [lane name]
  local json p s r
  [ -z "$1" ] || printf -- '--effort %q ' "$1"
  json='{"autoCompactWindow":200000'
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

# True when the lane's input line (last line starting with the prompt arrow) holds no text.
pane_input_empty() {
  local line
  line="$(command grep -a '^[[:space:]]*❯' <<<"$1" | tail -1 | sed 's/^[[:space:]]*❯//' | tr -d '[:space:]\302\240')"
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
      pane="$(tmux capture-pane -p -t "$SESSION:$w" 2>/dev/null || true)"
      row="$(index_row "$w")"
      IFS="$TAB" read -r d n dir m b p rep id st eff conn <<<"${row:-}"
      if [ -z "$row" ]; then
        rep="$(ls -t "$HOME"/.claude/state/lanes/*-"$w"/report.md 2>/dev/null | head -1 || true)"
        dir="$(tmux display -p -t "$SESSION:$w" '#{pane_current_path}')"; m="?"; id="?"
        extra="(not in index)"
      else extra=""; fi
      if [ -n "${rep:-}" ] && [ -s "$rep" ]; then wrote=yes; else wrote=no; fi
      outlog=""
      [ -n "${rep:-}" ] && [ -e "$(dirname "$rep")/out.log" ] && outlog="$(dirname "$rep")/out.log"
      if [ -n "$outlog" ]; then
        # Headless lane: the process writes "exit <code>" to out.log when it ends.
        code="$(tail -n 1 "$outlog" | sed -n 's/^exit \([0-9][0-9]*\)$/\1/p')"
        if [ -z "$code" ]; then state=working
        elif [ "$code" = 0 ]; then state=done
        else state="exit$code"; fi
        pane="$(tail -n 5 "$outlog")"
      elif command grep -aqE 'Credit balance too low|API Error' <<<"$pane"; then state=error
      elif pane_spinner "$pane"; then state=working
      else state=idle; fi
      last="$(command grep -av '^[[:space:]]*$' <<<"$pane" | command grep -avE '^[[:space:]]*[─━═]+[[:space:]]*$|⏵|🐢|^[[:space:]]*❯|esc to|Enter to confirm|Not now|Don.t show again|Teach auto mode|Auto mode works' | tail -1 | sed 's/^[[:space:]]*//' | cut -c1-80 || true)"
      printf '%-18s report:%-3s %-7s %s\n' "$w" "$wrote" "$state" "$last"
      ctx="?"
      case "${b:-subscription}" in codex|copilot) ctx="n/a" ;; *) { [ -n "${id:-}" ] && [ "$id" != - ] && [ "$id" != "?" ]; } && ctx="$(ctx_k "$dir" "$id")" ;; esac
      case "${b:-subscription}" in codex|copilot) cn="n/a" ;; *) if [ -z "$row" ]; then cn="?"; else cn="${conn:--}"; [ "$cn" = - ] && cn=on; fi ;; esac
      printf '    dir:%s model:%s effort:%s connectors:%s context:%s engine/billing:%s session:%s %s\n' "${dir:-?}" "${m:-?}" "${eff:-?}" "$cn" "$ctx" "${b:-subscription}" "$([ "${id:--}" = - ] && echo unknown || echo "$id")" "$extra"
    done < <(tmux list-windows -t "$SESSION" -F '#{window_name}')
  else
    echo "(no tmux session \"$SESSION\")"
  fi
  # Index lanes from the last 3 days with no window and no report.
  if [ -s "$INDEX" ]; then
    cutoff="$(date -d '3 days ago' +%F)"
    while IFS="$TAB" read -r d n dir m b p rep id st eff conn; do
      [[ "$d" < "$cutoff" ]] && continue
      case "$live" in *" $n "*) continue ;; esac
      [ -n "${rep:-}" ] && [ -s "$rep" ] && continue
      printf '%-18s dead, resume with: lane.sh --resume %s   (%s, %s, engine/billing %s, session %s)\n' "$n" "$n" "$dir" "$m" "${b:-subscription}" "$([ "${id:--}" = - ] && echo unknown || echo "$id")"
    done < <(awk -F'\t' '{r[$2]=$0; if(!($2 in o)){o[$2]=++c; k[c]=$2}} END{for(i=1;i<=c;i++)print r[k[i]]}' "$INDEX")
  fi
  exit 0
fi

if [ "$mode" = send ]; then
  [ $# -ge 2 ] || { echo "usage: lane.sh --send <name> <text>" >&2; exit 1; }
  name="$1"; shift; text="$*"
  row="$(index_row "$name")"
  if [ -n "$row" ]; then
    IFS="$TAB" read -r d n dir m b p rep id st eff conn <<<"$row"
    if is_headless_billing "${b:-}"; then
      echo "$name is a $b headless lane; start a new lane with a follow-up prompt instead of sending text to its window" >&2
      exit 1
    fi
  fi
  tmux has-session -t "$SESSION" 2>/dev/null && tmux list-windows -t "$SESSION" -F '#{window_name}' | command grep -qx -- "$name" \
    || { echo "no window named $name in $SESSION" >&2; exit 1; }
  target="$SESSION:$name"
  took() {  # poll up to ~$1 seconds for: input line empty and a spinner on screen
    local i pane
    for i in $(seq 1 $(($1 * 2))); do
      sleep 0.5; pane="$(tmux capture-pane -p -t "$target")"
      if pane_input_empty "$pane" && pane_spinner "$pane"; then return 0; fi
    done
    return 1
  }
  tmux send-keys -t "$target" -l -- "$text"
  sleep 0.3
  tmux send-keys -t "$target" C-m
  if took 6; then echo "sent to $name: submitted (input empty, spinner showing)"; exit 0; fi
  tmux send-keys -t "$target" C-m
  if took 6; then echo "sent to $name: submitted after a second Enter"; exit 0; fi
  pane="$(tmux capture-pane -p -t "$target")"
  if pane_input_empty "$pane"; then
    echo "sent to $name: input line is empty but no spinner seen; it may have been taken and already finished. Check: tmux capture-pane -p -t $target"
  else
    echo "sent to $name: DID NOT TAKE (text still in the input line after two Enters)"
  fi
  exit 1
fi

if [ "$mode" = resume ]; then
  [ $# -eq 1 ] || { echo "usage: lane.sh [--dry-run] [--console] --resume <name>" >&2; exit 1; }
  name="$1"
  fill_ids
  row="$(index_row "$name")"
  [ -n "$row" ] || { echo "no lane named $name in $INDEX" >&2; exit 1; }
  IFS="$TAB" read -r d n dir m b p rep id st eff conn <<<"$row"
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
  flags="$(claude_lane_flags "$eff" "$conn" "$name")"
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
eff="-"; flags=""; conn="-"
if [ "$engine" = claude ]; then
  conn="$(lane_connectors "$name" "$prompt")"
  eff="$(lane_effort "$model")"; flags="$(claude_lane_flags "$eff" "$conn" "$name")"; eff="${eff:--}"
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
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date +%F)" "$name" "$dir" "$model" \
    "$lane_billing" "$hprompt" "$report" "${sid:--}" "$start_epoch" "$eff" "$conn" >>"$INDEX"
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
