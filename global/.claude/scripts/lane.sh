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
# Folder trust: a new folder makes Claude Code ask "Yes, I trust this folder". For a folder
# under ~/repos this script answers it. Anywhere else the window waits for the user.
# The same poll also answers, by dialog text: with --console, the "use this API key?"
# first-run dialog (Yes); with any lane, the "Teach auto mode about your environment?"
# opt-in (Esc).
set -euo pipefail
SESSION=lanes

dry=0; console=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --console) console=1; shift ;;
    *) break ;;
  esac
done
[ $# -eq 4 ] || { echo "usage: lane.sh [--dry-run] [--console] <name> <dir> <model> <prompt-file>" >&2; exit 1; }
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

# Run a tmux command, or just print it under --dry-run.
t() { if [ "$dry" = 1 ]; then printf 'tmux'; printf ' %q' "$@"; echo; else tmux "$@"; fi; }

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

echo "lane:   $name  ($model, $dir)"
echo "prompt: $hprompt"
echo "report: $report"
echo "watch:  tmux attach -t $SESSION   (Ctrl-b n / p to switch windows, Ctrl-b d to detach)"
