#!/usr/bin/env bash
# Start a lane: a Claude Code session in its own tmux window, billed to the subscription
# (or, with --console, to Console credits).
#
#   lane.sh [--dry-run] [--console] <name> <dir> <model> <prompt-file>
#   e.g. lane.sh prod-logs ~/repos/SpokeToWork sonnet ~/.claude/state/lanes/2026-10-11-prod-logs/prompt.md
#
# The hub (~/repos on NX-01) is the director of directors: it dispatches lanes and reads
# their reports, it does not do lane work itself. Each lane is a window in the detached
# tmux session "lanes", started in <dir>, running: claude --model <model> "<prompt>".
# Hand-off folder: ~/.claude/state/lanes/<YYYY-MM-DD>-<name>/ holds prompt.md and report.md
# (the lane writes report.md; the hub reads it).
#
# --console: the window runs console_worker.sh --inner <dir> --model <model> "<prompt>"
# instead of plain claude, so the capped Console worker key reaches only that process's
# environment (CONSOLE_WORKER=1 is set there too). Without --console nothing changes.
# claude.ai connectors (Gmail, Calendar) may not load under API-key auth, so email lanes
# stay on the subscription.
#
# Hand-off across hub sessions (lanes outlive the hub terminal; only WSL shutting down stops them):
#   lane.sh --status                    one line per window in "lanes" (report yes/no, working/idle/error,
#                                       last pane line, dir, model, session id) plus lanes with no window
#                                       and no report from the last 3 days
#   lane.sh --send <name> <text>        type <text> into the window, Enter, confirm it was submitted
#   lane.sh [--dry-run] [--console] --resume <name>
#                                       fresh window running claude --resume <session-id> --model <m> in <dir>
# Every start appends one row to ~/.claude/state/lanes/index.tsv (tab-separated, no header):
#   date  name  dir  model  billing(console|subscription)  prompt  report  session-id  start-epoch
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

dry=0; console=0; mode=start
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --console) console=1; shift ;;
    --status) mode=status; shift ;;
    --send) mode=send; shift ;;
    --resume) mode=resume; shift ;;
    *) break ;;
  esac
done

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
      if command grep -aqE 'Credit balance too low|API Error' <<<"$pane"; then state=error
      elif pane_spinner "$pane"; then state=working
      else state=idle; fi
      last="$(command grep -av '^[[:space:]]*$' <<<"$pane" | command grep -avE '^[[:space:]]*[─━═]+[[:space:]]*$|⏵|🐢|^[[:space:]]*❯|esc to|Enter to confirm|Not now|Don.t show again|Teach auto mode|Auto mode works' | tail -1 | sed 's/^[[:space:]]*//' | cut -c1-80)"
      printf '%-18s report:%-3s %-7s %s\n' "$w" "$wrote" "$state" "$last"
      printf '    dir:%s model:%s session:%s %s\n' "${dir:-?}" "${m:-?}" "$([ "${id:--}" = - ] && echo unknown || echo "$id")" "$extra"
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
      printf '%-18s dead, resume with: lane.sh --resume %s   (%s, %s, session %s)\n' "$n" "$n" "$dir" "$m" "$([ "${id:--}" = - ] && echo unknown || echo "$id")"
    done < <(awk -F'\t' '{r[$2]=$0; if(!($2 in o)){o[$2]=++c; k[c]=$2}} END{for(i=1;i<=c;i++)print r[k[i]]}' "$INDEX")
  fi
  exit 0
fi

if [ "$mode" = send ]; then
  [ $# -ge 2 ] || { echo "usage: lane.sh --send <name> <text>" >&2; exit 1; }
  name="$1"; shift; text="$*"
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
  { [ -n "${id:-}" ] && [ "$id" != - ]; } || { echo "no session id recorded for $name; find it under $PROJ and add it to $INDEX" >&2; exit 1; }
  [ -d "$dir" ] || { echo "no such folder: $dir" >&2; exit 1; }
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

[ $# -eq 4 ] || { echo "usage: lane.sh [--dry-run] [--console] <name> <dir> <model> <prompt-file>
       lane.sh --status | --send <name> <text> | [--dry-run] [--console] --resume <name>" >&2; exit 1; }
name="$1"; dir="$2"; model="$3"; prompt="$4"

[[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "name must be letters, digits, . _ - only: $name" >&2; exit 1; }
[ -d "$dir" ] || { echo "no such folder: $dir" >&2; exit 1; }
[ -r "$prompt" ] || { echo "no such prompt file: $prompt" >&2; exit 1; }
dir="$(cd "$dir" && pwd -P)"
prompt="$(cd "$(dirname "$prompt")" && pwd -P)/$(basename "$prompt")"

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
if [ "$console" = 1 ]; then
  cmd="$HOME/.claude/scripts/console_worker.sh --inner $(printf %q "$dir") --model $(printf %q "$model") \"\$(cat $(printf %q "$hprompt"))\""
else
  cmd="claude --model $(printf %q "$model") \"\$(cat $(printf %q "$hprompt"))\""
fi
t send-keys -t "$target" "$cmd" Enter

if [ "$dry" = 1 ]; then
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
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date +%F)" "$name" "$dir" "$model" \
    "$([ "$console" = 1 ] && echo console || echo subscription)" "$hprompt" "$report" "${sid:--}" "$start_epoch" >>"$INDEX"
else
  echo "# would append a row to $INDEX"
fi

echo "lane:   $name  ($model, $dir)"
echo "prompt: $hprompt"
echo "report: $report"
echo "watch:  tmux attach -t $SESSION   (Ctrl-b n / p to switch windows, Ctrl-b d to detach)"
