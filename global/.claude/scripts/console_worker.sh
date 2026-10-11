#!/usr/bin/env bash
# Start a Claude Code session billed to Console credits instead of the Max subscription.
#
#   console_worker.sh <repo-dir> [claude args...]
#   e.g. console_worker.sh ~/repos/ScriptHammer
#        console_worker.sh ~/repos/ada-stair-generator --model haiku
#
# Subagents always bill to whatever their parent bills, so moving work to the Console
# means a separate claude process. Inside tmux this opens a new window named
# console-worker; outside tmux it runs in the current terminal. Sonnet unless --model
# is given.
#
# The key is ~/.config/anthropic/worker-key (mode 600): a key Jonathan created in one
# capped Console workspace. Never the personal org key. It goes into the environment of
# this one process only, never onto a command line where ps could show it.
# claude.ai connectors (Gmail, Calendar) may not load under API-key auth.
set -euo pipefail
KEY="$HOME/.config/anthropic/worker-key"

if [ "${1:-}" = "--inner" ]; then
  shift; dir="$1"; shift
  cd "$dir"
  ANTHROPIC_API_KEY="$(<"$KEY")"
  export ANTHROPIC_API_KEY CONSOLE_WORKER=1
  exec claude "$@"
fi

dir="${1:?usage: console_worker.sh <repo-dir> [claude args...]}"; shift
[ -d "$dir" ] || { echo "no such folder: $dir" >&2; exit 1; }
[ -r "$KEY" ] || { echo "no key at $KEY. Create one in a capped Console workspace and save it there (mode 600)." >&2; exit 1; }
[ "$(stat -c %a "$KEY")" = 600 ] || { echo "$KEY must be mode 600 (chmod 600 $KEY)" >&2; exit 1; }
dir="$(cd "$dir" && pwd)"
args=("$@")
[[ " ${args[*]:-} " == *" --model "* ]] || args=(--model sonnet "${args[@]}")

if [ -n "${TMUX:-}" ]; then
  tmux new-window -n console-worker -c "$dir" "$0" --inner "$dir" "${args[@]}"
  echo "opened tmux window console-worker in $dir"
else
  exec "$0" --inner "$dir" "${args[@]}"
fi
