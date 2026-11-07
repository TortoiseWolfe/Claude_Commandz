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
# A per-minute refusal is not an exhausted quota (issue 267, 6 Nov 2026: five lanes sharing one Gemini key each fell through to OpenRouter's
# daily cap within 20 minutes, exit 75, when Gemini had only asked for six seconds): when the refusal carries a retry delay ("retry in 6.01s",
# Retry-After) the wrapper waits that long plus 1 to 3 seconds of jitter (one wait is capped at 90 s) and retries the same provider, up to 5 times
# in a row. Only a per-day refusal ("per-day", "PerDay", "free-models-per-day"), a refusal with no retry delay, or five retries used up moves on to
# the next provider. A per-day refusal also writes a stamp file, <provider>-<date>, in OPENCODE_QUOTA_DIR (default ~/.claude/state/opencode-quota),
# and a stamped provider is skipped for the rest of that day, so later lanes do not spend a start on it. OPENCODE_RETRY_SCALE multiplies every
# wait (tests use 0.01); OPENCODE_BIN names the opencode program (default: opencode on PATH).
#
# Keys are read from their files here, at run time, into this process's environment only: they never appear in a command line, a tmux window
# or a log. Both free APIs train on free-tier input (see ~/.config/panel/free-limits.json), so lane.sh refuses client repos and ~/repos.
# Groq's free tier is not used: its 8000 tokens/minute is smaller than OpenCode's own system prompt (proved 22 Oct: it loops on "Request too large").
#
# Permissions: everything allowed except paths outside the checkout; the lane folder and this repo's own worktrees (~/repos/.worktrees/<repo>--*,
# <repo>-wf-*) are the outside paths it may use, never another repo's worktree. Shell commands are not sandboxed beyond that: the client guard in
# lane.sh on the checkout is what keeps client code away from the free APIs.
# Secrets (6 Nov 2026: a lane read an env file and a password reached the shared OpenCode log and the free model's provider): the config denies the
# read and edit tools on env files (*.env, *.env.*), key files (*api-key*, *.pem, *.netrc, ssh keys) and denies shell commands whose text names an env
# file or key file or dumps the environment (advisory only: matching command text cannot stop every way a shell reaches a file, and it also blocks a
# grep for process.env, which the grep tool does without a shell; the sh-skips lane, 6 Nov 8 PM, got a value out of a sandbox env file outside its
# checkout with a shell command, and the value went into the lane's own out.log as well), and the prompt tells the model never to open them. OpenCode logs every permission check with the whole command text the model typed, at INFO level into the shared ~/.local/share/opencode/log,
# so the run uses --log-level ERROR (no such lines) and, after the run, scrub_known_secrets.py rewrites that log (best effort: it skips a log
# written in the last two minutes; values are never printed).
# Pushes (6 Nov 2026, 8:13 PM: the skip-census lane pushed straight to Claude_Commandz main with no PR, then ran install_scripts.py before CI had
# run, though its prompt said one PR, merge on green): the config denies shell commands that push to main or master (git push origin main,
# HEAD:main, refs/heads/main), force pushes, and install_scripts.py; the prompt says a lane pushes a branch and opens a PR and never installs.
# The provider keys are in the environment the model's shells inherit; "*API_KEY*" and printenv are denied, which is advisory too (the firm fixes are
# GitHub branch protection on main and passing keys to OpenCode by file reference, neither of which this script can set).
# Git settings (6 Nov 2026, 8:58 PM: a lane had left a placeholder author email in ~/.gitconfig): shell commands that change git configuration are denied
# (git config, git -c, ~/.gitconfig, .git/config, .git/hooks, core.hooksPath, GIT_CONFIG*; reads with --get and --list stay allowed), and the edit tool
# cannot write those files. Advisory like the rest: a script can still reach the file.
# The deny is on command text, so a bare "git push" from a checkout that is on main is not caught: lane.sh starts opencode lanes on a branch.
# The config is written to the lane folder (OPENCODE_CONFIG), never into the repo. A wall-clock limit (OPENCODE_LANE_TIMEOUT seconds, default 7200)
# stops a run that loops.
set -u

[ $# -eq 4 ] || { echo "usage: opencode_lane.sh <dir> <hand-off-folder> <model> <prompt-file>" >&2; exit 2; }
dir="$1"; hand="$2"; want="$3"; prompt_file="$4"

# The third is OpenCode's own free tier (Zen): it needs no key, so a day on which the Gemini key (20 requests a day) and OpenRouter's free models
# (a daily cap) are both spent still has a provider left (proved 6 Nov 2026: with both out, opencode/big-pickle answered and edited a file).
DEFAULT_MODELS=(google/gemini-3-flash-preview openrouter/nvidia/nemotron-3-ultra-550b-a55b:free opencode/big-pickle)
LIMIT="${OPENCODE_LANE_TIMEOUT:-7200}"

# nvm's node is not always on PATH in a bare tmux window.
OPENCODE_BIN="${OPENCODE_BIN:-opencode}"
MAX_RETRIES=5
MAX_WAIT=90
QUOTA_DIR="${OPENCODE_QUOTA_DIR:-$HOME/.claude/state/opencode-quota}"
command -v "$OPENCODE_BIN" >/dev/null 2>&1 || for d in "$HOME"/.nvm/versions/node/*/bin; do [ -x "$d/opencode" ] && PATH="$d:$PATH"; done
command -v "$OPENCODE_BIN" >/dev/null 2>&1 || { echo "opencode is not installed (npm install -g opencode-ai)" >&2; exit 127; }

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
# A lane started inside a linked worktree (<repo>--<topic>) names the repo, not the worktree: the rule below lets the lane use that repo's
# OTHER worktrees, and a rule built from the worktree's own name (<repo>--<topic>--*) matches none of them (issue 285).
_common="$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || _common=""
[ "$(basename -- "$_common")" = .git ] && repo_name="$(basename -- "$(dirname -- "$_common")")"
mkdir -p -- "$hand"
conf="$hand/opencode.json"
cat >"$conf" <<JSON
{"\$schema":"https://opencode.ai/config.json","autoupdate":false,"share":"disabled",
 "permission":{"*":"allow",
  "read":{"*":"allow","*.env":"deny","*.env.*":"deny","*api-key*":"deny","*.pem":"deny","*.netrc":"deny","*id_rsa*":"deny","*id_ed25519*":"deny"},
  "edit":{"*":"allow","*.gitconfig":"deny","*/.git/config":"deny","*/.git/hooks/*":"deny","*.env":"deny","*.env.*":"deny","*api-key*":"deny","*.pem":"deny","*.netrc":"deny","*id_rsa*":"deny","*id_ed25519*":"deny"},
  "bash":{"*":"allow","*.env*":"deny","*api-key*":"deny","*.pem*":"deny","*id_rsa*":"deny","*id_ed25519*":"deny","*git*push*--force*":"deny","*git*push*--mirror*":"deny","*git*push*--all*":"deny","*git*push*+*":"deny","*API_KEY*":"deny","*git*push* -f*":"deny","*git*push* main":"deny","*git*push* main *":"deny","*git*push*:main":"deny","*git*push*:main *":"deny","*git*push* master":"deny","*git*push* master *":"deny","*git*push*:master":"deny","*git*push*:master *":"deny","*git*push*refs/heads/main*":"deny","*git*push*refs/heads/master*":"deny","*install_scripts.py*":"deny","printenv*":"deny","env":"deny","env *":"deny","*/proc/*/environ*":"deny","export -p*":"deny","*git*config*":"deny","*git -c *":"deny","*.gitconfig*":"deny","*.git/config*":"deny","*.git/hooks*":"deny","*core.hooksPath*":"deny","*GIT_CONFIG*":"deny","*git*config*--get*":"allow","*git*config*--list*":"allow"},
  "external_directory":{"*":"deny","$hand/**":"allow","$dir/**":"allow","$HOME/repos/.worktrees/$repo_name--*/**":"allow","$HOME/repos/.worktrees/$repo_name-wf-*/**":"allow"}}}
JSON
export OPENCODE_CONFIG="$conf"

# A provider whose key is not on this machine is left out: opencode started on google/* with no key does not say so, it fails at once with "Unexpected
# server error" (ref err_...), which is not a quota line, so the wrapper exited instead of trying the next provider (stw-117-ci, 6 Nov 2026: failed twice
# on a tower with no ~/.config/gemini). opencode/* (its own free tier) needs no key and always stays, so the list is never empty.
provider_has_key() {  # <provider>
  case "$1" in
    google) [ -n "${GOOGLE_GENERATIVE_AI_API_KEY:-}" ] ;;
    openrouter) [ -n "${OPENROUTER_API_KEY:-}" ] ;;
    *) return 0 ;;
  esac
}
# Order: the requested model, then the defaults not already tried.
models=()
case "$want" in google/*|openrouter/*|opencode/*) models+=("$want") ;; esac  # the keyed providers loaded above, and OpenCode's keyless free tier
for m in "${DEFAULT_MODELS[@]}"; do
  seen=0; for x in "${models[@]:-}"; do [ "$x" = "$m" ] && seen=1; done
  [ "$seen" = 1 ] || models+=("$m")
done
keyed=()
for m in "${models[@]}"; do
  if provider_has_key "${m%%/*}"; then keyed+=("$m")
  else echo "opencode: no key for ${m%%/*} on this machine (looked for the key files under ~/.config/gemini and ~/.config/openrouter); leaving out $m"; fi
done
models=("${keyed[@]}")

# A quota or rate limit shows up as an "Error:" line; the lane's own output (test logs that say "rate limit") is not scanned.
quota_lines() {  # <log file>: the matching Error lines, colour codes removed
  sed -E 's/\x1b\[[0-9;]*m//g' -- "$1" | grep -E '^Error: ' | grep -iE 'quota|rate[ _-]?limit|RESOURCE_EXHAUSTED|\b429\b|tokens per (minute|day)|Request too large|free-models-per-day|insufficient credits'
}
quota_hit() {  # <log file>
  quota_lines "$1" | grep -q .
}

# What kind of refusal is it? Prints "day" (give up on this provider for today), or "wait <seconds>" (a per-minute limit that said when to retry),
# or "none" (a refusal that names neither: treated like a daily one for this run, but not stamped).
classify() {  # <log file>
  local text n
  text="$(quota_lines "$1")"
  if grep -qiE 'per[ _-]?day|tokens per day|daily' <<<"$text"; then echo day; return; fi
  n="$(grep -oiE 'retry in [0-9]+(\.[0-9]+)?s?|retry[ _-]?after[^0-9]{0,6}[0-9]+(\.[0-9]+)?|retryDelay[^0-9]{0,6}[0-9]+(\.[0-9]+)?' <<<"$text" | head -n 1 | grep -oE '[0-9]+(\.[0-9]+)?' | head -n 1)"
  if [ -n "$n" ]; then echo "wait $n"; else echo none; fi
}

stamp_file() { echo "$QUOTA_DIR/$1-$(date +%F)"; }  # <provider>
is_stamped() { [ -e "$(stamp_file "$1")" ]; }
write_stamp() {  # <provider>: a per-day refusal; yesterday's stamps for it are removed
  mkdir -p -- "$QUOTA_DIR" 2>/dev/null || return 0
  local f; for f in "$QUOTA_DIR/$1"-*; do [ -e "$f" ] && [ "$f" != "$(stamp_file "$1")" ] && rm -f -- "$f"; done
  date '+%F %T' >"$(stamp_file "$1")" 2>/dev/null || true
}

prompt="$(cat -- "$prompt_file")" || exit 2
scrub_known() {  # <file or folder>: best effort, never fails the run; a file written in the last two minutes is skipped unless the run is over
  local s="$HOME/.claude/scripts/scrub_known_secrets.py"
  [ -r "$s" ] && [ -e "$1" ] || return 0
  if [ -f "$1" ]; then python3 -I "$s" --min-age 0 "$1" >/dev/null 2>&1 || true; else python3 -I "$s" "$1" >/dev/null 2>&1 || true; fi
}
scrub_shared_log() { scrub_known "$HOME/.local/share/opencode/log"; }
trap scrub_shared_log EXIT
prompt="Rule for this run: never open, print, copy or type into a command the contents of an environment file (.env, .env.*, *.env), a key file or a password, even if the task seems to need it; if it does, say so in your report and stop. Never push to main or master, never force-push, and never run install_scripts.py or install anything on the machine: push your own branch and open a pull request, and the person who owns the repo merges and installs it.

$prompt"
cd -- "$dir" || exit 2
log="$hand/.opencode-run.log"
last=0
for m in "${models[@]}"; do
  provider="${m%%/*}"
  if is_stamped "$provider"; then
    echo "opencode: $provider hit its daily limit earlier today (stamp $(stamp_file "$provider")); skipping $m"
    last=75; continue
  fi
  retries=0
  while :; do
    echo "opencode: model $m"
    : >"$log"
    # In the background so a loop on a rate-limit error (OpenCode retries and compacts forever) is caught while it runs, not after the time limit.
    timeout "$LIMIT" "$OPENCODE_BIN" run --model "$m" --auto --log-level ERROR --dir "$dir" "$prompt" >"$log" 2>&1 &
    pid=$!
    hit=0
    while kill -0 "$pid" 2>/dev/null; do
      sleep "${OPENCODE_LANE_POLL:-10}"
      if quota_hit "$log"; then hit=1; kill "$pid" 2>/dev/null; break; fi
    done
    wait "$pid" 2>/dev/null; last=$?
    scrub_known "$log"  # the run is over: what it printed is cleaned before it is copied into the lane's out.log
    cat -- "$log"
    if [ "$hit" = 0 ] && quota_hit "$log"; then hit=1; fi
    [ "$hit" = 1 ] || exit "$last"
    kind="$(classify "$log")"
    case "$kind" in
      wait\ *)
        if [ "$retries" -lt "$MAX_RETRIES" ]; then
          retries=$((retries + 1))
          wait_s="$(awk -v d="${kind#wait }" -v r="$RANDOM" -v cap="$MAX_WAIT" 'BEGIN{w=d+1+(r%2001)/1000; if(w>cap)w=cap; printf "%.2f", w}')"
          echo "opencode: $provider per-minute limit; retrying in $(awk -v w="$wait_s" 'BEGIN{printf "%.0f", w}')s ($retries/$MAX_RETRIES)"
          sleep "$(awk -v w="$wait_s" -v k="${OPENCODE_RETRY_SCALE:-1}" 'BEGIN{printf "%.3f", w*k}')"
          continue
        fi
        echo "opencode: $provider still rate limited after $MAX_RETRIES retries; trying the next provider"
        ;;
      day)
        write_stamp "$provider"
        echo "opencode: $provider daily limit; stamped $(stamp_file "$provider"), skipping it for the rest of today"
        ;;
      *) echo "opencode: $provider refused with no retry delay; trying the next provider" ;;
    esac
    echo "opencode: $m is out of free quota or rate limit; trying the next provider"
    last=75
    break
  done
done
echo "opencode: free quota exhausted on every provider (${models[*]})"
exit "$last"
