#!/usr/bin/env bash
# Start a lane in its own tmux window. Claude Code is the default engine; Codex and
# GitHub Copilot can be selected with --engine.
#
#   lane.sh [--dry-run] [--engine claude|codex|copilot] [--allow-client] [--console [--headless]] <name> <dir> <model> <prompt-file>
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
# Hand-off across hub sessions (lanes outlive the hub terminal; only WSL shutting down stops them):
#   lane.sh --status                    one line per window in "lanes" (report yes/no, working/idle/error,
#                                       last pane line, dir, model, engine/billing, session id) plus lanes with no window
#                                       and no report from the last 3 days
#   lane.sh --send <name> <text>        type <text> into the window, Enter, confirm it was submitted
#   lane.sh [--dry-run] [--console] --resume <name>
#                                       fresh window running claude --resume <session-id> --model <m> in <dir>
# Every start appends one row to ~/.claude/state/lanes/index.tsv (tab-separated, no header):
#   date  name  dir  model  billing(console|subscription; codex|copilot)  prompt  report  session-id  start-epoch
# The session id is the earliest transcript under ~/.claude/projects/<dir-slug>/ created after the start
# and not claimed by another row; if it is not there yet at start, the next --status fills it in.
#
# Folder trust: a new folder makes Claude Code ask "Yes, I trust this folder". For a folder
# under ~/repos this script answers it. Anywhere else the window waits for the user.
# The same poll also answers, by dialog text: with --console, the "use this API key?"
# first-run dialog (Yes); with any lane, the "Teach auto mode about your environment?"
# opt-in (Esc).
set -euo pipefail
SESSION=lanes

INDEX="$HOME/.claude/state/lanes/index.tsv"
PROJ="$HOME/.claude/projects"
TAB=$'\t'

usage() {
  cat <<'USAGE'
Usage:
  lane.sh [--dry-run] [--engine claude|codex|copilot] [--allow-client] [--console [--headless]] <name> <dir> <model> <prompt-file>
  lane.sh --status
  lane.sh --send <name> <text>
  lane.sh [--dry-run] [--console] --resume <name>

Claude is the default engine. Codex and Copilot run headless and write to out.log.
Their model flag is used only when <model> is not a Claude name.
Codex and Copilot refuse repositories listed in client-senders.tsv or client-repos.txt,
and folders under ~/repos/CD. client-repos.txt has one path per line. Blank lines
and lines starting with # are ignored. Both list files must be regular readable files; pass --allow-client to continue.
--headless is only for Claude with --console.
USAGE
}

dry=0; console=0; headless=0; allow_client=0; engine=claude; mode=start
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --help|-h) usage; exit 0 ;;
    --engine)
      [ $# -ge 2 ] || { echo "--engine requires claude, codex, or copilot" >&2; exit 1; }
      engine="$2"; shift 2 ;;
    --engine=*) engine="${1#*=}"; shift ;;
    --allow-client) allow_client=1; shift ;;
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
  local tmp claimed d n dir m b p r id st
  tmp="$(mktemp)"
  claimed="$(cut -f8 "$INDEX" | tr '\n' ' ')"
  while IFS="$TAB" read -r d n dir m b p r id st; do
    if { [ -z "$id" ] || [ "$id" = - ]; } && [ "${st:--}" != - ]; then
      id="$(find_session "$dir" "$st" "$claimed")"
      [ -n "$id" ] && claimed="$claimed $id"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$d" "$n" "$dir" "$m" "$b" "$p" "$r" "${id:--}" "${st:--}" >>"$tmp"
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
      IFS="$TAB" read -r d n dir m b p rep id st <<<"${row:-}"
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
      printf '    dir:%s model:%s engine/billing:%s session:%s %s\n' "${dir:-?}" "${m:-?}" "${b:-subscription}" "$([ "${id:--}" = - ] && echo unknown || echo "$id")" "$extra"
    done < <(tmux list-windows -t "$SESSION" -F '#{window_name}')
  else
    echo "(no tmux session \"$SESSION\")"
  fi
  # Index lanes from the last 3 days with no window and no report.
  if [ -s "$INDEX" ]; then
    cutoff="$(date -d '3 days ago' +%F)"
    while IFS="$TAB" read -r d n dir m b p rep id st; do
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
    IFS="$TAB" read -r d n dir m b p rep id st <<<"$row"
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
  IFS="$TAB" read -r d n dir m b p rep id st <<<"$row"
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
  if [ "$console" = 1 ]; then
    cmd="$HOME/.claude/scripts/console_worker.sh --inner $(printf %q "$dir") --resume $(printf %q "$id") --model $(printf %q "$m")"
  else
    cmd="claude --resume $(printf %q "$id") --model $(printf %q "$m")"
  fi
  if [ "$dry" = 0 ] && ! tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux new-session -d -s "$SESSION" -n "$name" -c "$dir"
  else
    t new-window -t "$SESSION:" -n "$name" -c "$dir"
  fi
  t send-keys -t "$SESSION:$name" "$cmd" Enter
  echo "resumed: $name  ($m, $dir, session $id, billing $([ "$console" = 1 ] && echo console || echo subscription))"
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
if [ "$engine" != claude ]; then
  out="$hand/out.log"
  [ "$dry" = 1 ] || : >"$out"
  prompt_cmd="\$(cat -- $(printf %q "$hprompt"))"
  out_cmd="$(printf %q "$out")"
  if [ "$engine" = codex ]; then
    # GIT_SSH_COMMAND: inside the sandbox's user namespace root-owned system files look
    # wrongly owned, so ssh rejects /etc/ssh/ssh_config.d/*; -F points it at the user config only.
    cmd="GIT_SSH_COMMAND=\"ssh -F \$HOME/.ssh/config\" codex exec -C $(printf %q "$dir") -s workspace-write --add-dir $(printf %q "$hand")"
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
  cmd="{ $HOME/.claude/scripts/console_worker.sh --inner $(printf %q "$dir") -p --model $(printf %q "$model") \"\$(cat $(printf %q "$hprompt"))\" >$(printf %q "$out") 2>&1; echo \"exit \$?\" >>$(printf %q "$out"); }"
elif [ "$console" = 1 ]; then
  cmd="$HOME/.claude/scripts/console_worker.sh --inner $(printf %q "$dir") --model $(printf %q "$model") \"\$(cat $(printf %q "$hprompt"))\""
else
  cmd="claude --model $(printf %q "$model") \"\$(cat $(printf %q "$hprompt"))\""
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
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date +%F)" "$name" "$dir" "$model" \
    "$lane_billing" "$hprompt" "$report" "${sid:--}" "$start_epoch" >>"$INDEX"
else
  echo "# would append a row to $INDEX"
fi

echo "lane:   $name  ($model, $dir)"
echo "prompt: $hprompt"
echo "report: $report"
if [ "$headless" = 1 ] || [ "$engine" != claude ]; then echo "log:    $hand/out.log"; fi
echo "watch:  tmux attach -t $SESSION   (Ctrl-b n / p to switch windows, Ctrl-b d to detach)"
