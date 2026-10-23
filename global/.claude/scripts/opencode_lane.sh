#!/usr/bin/env bash
# The body of an OpenCode lane (lane.sh --engine opencode): OpenCode (opencode.ai) on a free model API, headless, able to edit files AND run commands.
#
#   opencode_lane.sh <dir> <hand-off-folder> <model> <prompt-file>
#
# <model> is OpenCode's provider/model form (google/gemini-3-flash-preview, openrouter/nvidia/nemotron-3-ultra-550b-a55b:free); lane.sh passes
# the default when the lane was given a Claude alias. Providers run in a fixed order, the requested model first: when one reports its free
# quota or rate limit (an "Error:" line about quota, 429 or tokens per minute) the run is stopped and the next provider takes the same prompt
# against whatever the first one already changed. When none is left the last line says "opencode: free quota exhausted", which is what the
# dispatcher reads (AGY_QUOTA_RE) to stop routing to this engine for a while.
#
# Keys are read from their files here, at run time, into this process's environment only: they never appear in a command line, a tmux window
# or a log. Both free APIs train on free-tier input (see ~/.config/panel/free-limits.json), so lane.sh refuses client repos and ~/repos.
# Groq's free tier is not used: its 8000 tokens/minute is smaller than OpenCode's own system prompt (proved 22 Oct: it loops on "Request too large").
#
# Permissions: everything allowed except paths outside the checkout; the lane folder and this repo's own worktrees (~/repos/.worktrees/<repo>--*,
# <repo>-wf-*) are the outside paths it may use, never another repo's worktree. Shell commands are not sandboxed beyond that: the client guard in
# lane.sh on the checkout is what keeps client code away from the free APIs.
# The config is written to the lane folder (OPENCODE_CONFIG), never into the repo. A wall-clock limit (OPENCODE_LANE_TIMEOUT seconds, default 7200)
# stops a run that loops.
set -u

[ $# -eq 4 ] || { echo "usage: opencode_lane.sh <dir> <hand-off-folder> <model> <prompt-file>" >&2; exit 2; }
dir="$1"; hand="$2"; want="$3"; prompt_file="$4"

DEFAULT_MODELS=(google/gemini-3-flash-preview openrouter/nvidia/nemotron-3-ultra-550b-a55b:free)
LIMIT="${OPENCODE_LANE_TIMEOUT:-7200}"

# nvm's node is not always on PATH in a bare tmux window.
command -v opencode >/dev/null 2>&1 || for d in "$HOME"/.nvm/versions/node/*/bin; do [ -x "$d/opencode" ] && PATH="$d:$PATH"; done
command -v opencode >/dev/null 2>&1 || { echo "opencode is not installed (npm install -g opencode-ai)" >&2; exit 127; }

load_key() {  # <env var> <file>...
  local var="$1" f; shift
  for f in "$@"; do
    if [ -r "$f" ] && [ -s "$f" ]; then export "$var=$(cat -- "$f")"; return 0; fi
  done
  return 1
}
load_key GOOGLE_GENERATIVE_AI_API_KEY "$HOME/.config/gemini/api-key" || true
load_key OPENROUTER_API_KEY "$HOME/.config/openrouter/api-key" || true

# One config for the run: allow everything, deny paths outside the checkout except the ones this lane is told to use.
repo_name="$(basename -- "$dir")"
mkdir -p -- "$hand"
conf="$hand/opencode.json"
cat >"$conf" <<JSON
{"\$schema":"https://opencode.ai/config.json","autoupdate":false,"share":"disabled",
 "permission":{"*":"allow","external_directory":{"*":"deny","$hand/**":"allow","$HOME/repos/.worktrees/$repo_name--*/**":"allow","$HOME/repos/.worktrees/$repo_name-wf-*/**":"allow"}}}
JSON
export OPENCODE_CONFIG="$conf"

# Order: the requested model, then the defaults not already tried.
models=()
case "$want" in google/*|openrouter/*) models+=("$want") ;; esac  # only providers whose key is loaded above
for m in "${DEFAULT_MODELS[@]}"; do
  seen=0; for x in "${models[@]:-}"; do [ "$x" = "$m" ] && seen=1; done
  [ "$seen" = 1 ] || models+=("$m")
done

# A quota or rate limit shows up as an "Error:" line; the lane's own output (test logs that say "rate limit") is not scanned.
quota_hit() {  # <log file>
  sed -E 's/\x1b\[[0-9;]*m//g' -- "$1" | grep -E '^Error: ' | grep -qiE 'quota|rate[ _-]?limit|RESOURCE_EXHAUSTED|\b429\b|tokens per (minute|day)|Request too large|free-models-per-day|insufficient credits'
}

prompt="$(cat -- "$prompt_file")" || exit 2
cd -- "$dir" || exit 2
log="$hand/.opencode-run.log"
last=0
for m in "${models[@]}"; do
  echo "opencode: model $m"
  : >"$log"
  # In the background so a loop on a rate-limit error (OpenCode retries and compacts forever) is caught while it runs, not after the time limit.
  timeout "$LIMIT" opencode run --model "$m" --auto --dir "$dir" "$prompt" >"$log" 2>&1 &
  pid=$!
  hit=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep "${OPENCODE_LANE_POLL:-10}"
    if quota_hit "$log"; then hit=1; kill "$pid" 2>/dev/null; break; fi
  done
  wait "$pid" 2>/dev/null; last=$?
  cat -- "$log"
  if [ "$hit" = 0 ] && quota_hit "$log"; then hit=1; fi
  if [ "$hit" = 1 ]; then echo "opencode: $m is out of free quota or rate limit; trying the next provider"; last=75; continue; fi
  exit "$last"
done
echo "opencode: free quota exhausted on every provider (${models[*]})"
exit "$last"
