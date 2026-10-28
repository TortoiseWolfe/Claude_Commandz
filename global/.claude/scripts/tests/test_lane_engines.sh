#!/usr/bin/env bash
set -euo pipefail
# The CI machine has no claude, agy or codex program; the engine check (test_lane_engine_check.sh) is tested on its own.
export AFA_ENGINES_AVAILABLE="*"

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-engines.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

BIN="$TMP/bin"
HOME_TEST="$TMP/home"
REPO="$TMP/project repo"
PROMPT="$TMP/prompt.md"
PROTECTED_REPO="$HOME_TEST/repos/protected-worktree-repo"
PROTECTED_WORKTREE="$TMP/worktrees/protected-topic"
UNPROTECTED_REPO="$TMP/unprotected-worktree-repo"
UNPROTECTED_WORKTREE="$TMP/worktrees/unprotected-topic"
mkdir -p "$BIN" "$HOME_TEST/repos/hub" "$HOME_TEST/repos/sample-client-repo" \
  "$HOME_TEST/repos/listed-only-repo/nested" "$HOME_TEST/repos/commented-client-repo" \
  "$HOME_TEST/repos/CD/nested" "$REPO" "$TMP/repo_dir" "$PROTECTED_REPO" \
  "$UNPROTECTED_REPO" "$TMP/worktrees"
printf 'id\trepo_dir\n# ignored\t~/repos/commented-client-repo\nblank\t\nexample\t~/repos/sample-client-repo\n' >"$HOME_TEST/repos/hub/client-senders.tsv"
printf '# Client repositories\n~/repos/listed-only-repo\n\n# ~/repos/commented-client-repo\n' >"$HOME_TEST/repos/hub/client-repos.txt"

init_test_repo() {
  local repo="$1"
  git -C "$repo" init -q
  git -C "$repo" -c user.name=lane-test -c user.email=lane-test commit --allow-empty -qm initial
}
init_test_repo "$PROTECTED_REPO"
git -C "$PROTECTED_REPO" worktree add -q -b protected-topic "$PROTECTED_WORKTREE"
init_test_repo "$UNPROTECTED_REPO"
git -C "$UNPROTECTED_REPO" worktree add -q -b unprotected-topic "$UNPROTECTED_WORKTREE"
printf '%s\n' "$PROTECTED_REPO" >>"$HOME_TEST/repos/hub/client-repos.txt"

printf '%s\n' 'Review this safely.' '$(touch injected); "quoted"; `backticks`' >"$PROMPT"

cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
printf '%s\0' "$@" >>"$TMUX_CALLS"
case "$1" in
  has-session) [ "${TMUX_HAS_SESSION:-0}" = 1 ] ;;
  list-windows) for window in ${TMUX_WINDOWS:-}; do printf '%s\n' "$window"; done ;;
  capture-pane)
    if [ -n "${TMUX_PANE_FILE:-}" ]; then
      # Like real tmux: colour escapes only come out when -e is passed.
      case " $* " in
        *' -e '*) cat "$TMUX_PANE_FILE" ;;
        *) sed -E 's/\x1b\[[0-9;:]*m//g' "$TMUX_PANE_FILE" ;;
      esac
    else printf 'ready\n'; fi ;;
  send-keys)
    if [ "${2:-}" = -t ] && [ -n "${4:-}" ]; then
      printf '%s\n' "$4" >"$TMUX_SENT"
      if [ "${5:-}" = Enter ] && [ "${RUN_LANE_COMMANDS:-0}" = 1 ]; then
        case "$4" in
          'codex exec '*|'GIT_SSH_COMMAND='*'codex exec '*|'cd '* ) (cd "$TEST_CWD" && bash -c "$4") ;;
        esac
      fi
    fi
    ;;
esac
STUB
cat >"$BIN/sleep" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
cat >"$BIN/codex" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CODEX_CALLS"
STUB
cat >"$BIN/copilot" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$COPILOT_CALLS"
STUB
cat >"$BIN/agy" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$AGY_CALLS"
STUB
# opencode stub: logs "<model> <config>" per call; OPENCODE_QUOTA_MODELS (space separated) makes those models print a free-tier limit error and exit 1,
# OPENCODE_LOOP_MODELS makes them print the limit error and then hang, as the real thing did on Groq's 8000 tokens/minute.
cat >"$BIN/opencode" <<'STUB'
#!/usr/bin/env bash
model=""; while [ $# -gt 0 ]; do [ "$1" = --model ] && { model="$2"; shift; }; shift; done
printf '%s %s\n' "$model" "${OPENCODE_CONFIG:-}" >>"$OPENCODE_CALLS"
for m in ${OPENCODE_QUOTA_MODELS:-}; do
  [ "$m" = "$model" ] && { printf '\033[91m\033[1mError: \033[0mRequest too large for model `x` on tokens per minute (TPM): Limit 8000\n'; exit 1; }
done
for m in ${OPENCODE_LOOP_MODELS:-}; do
  [ "$m" = "$model" ] && { printf 'Error: 429 RESOURCE_EXHAUSTED\n'; exec /bin/sleep 30; }
done
printf 'tests ran: this text says rate limit and quota but is not an Error line\n'
STUB
chmod +x "$BIN/tmux" "$BIN/sleep" "$BIN/codex" "$BIN/copilot" "$BIN/agy" "$BIN/opencode"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
assert_contains() {
  local value="$1" part="$2" label="$3"
  case "$value" in *"$part"*) checks=$((checks + 1)) ;; *) fail "$label (missing: $part)" ;; esac
}
assert_not_contains() {
  local value="$1" part="$2" label="$3"
  case "$value" in *"$part"*) fail "$label (unexpected: $part)" ;; *) checks=$((checks + 1)) ;; esac
}
assert_exit() {
  local expected="$1" actual="$2" label="$3"
  [ "$actual" = "$expected" ] || fail "$label (expected exit $expected, got $actual)"
  checks=$((checks + 1))
}

# These tests are about the command each engine builds, not about the agy default (tests/test_lane_agy_default.sh covers that), so every
# run gives --claude-because: $REPO is an own repo, and a Claude start there would otherwise be refused.
run_lane() {
  local sent="$TMP/tmux-sent" output="$TMP/run.out" status
  rm -f "$sent"
  set +e
  (cd "$TMP" && HOME="$HOME_TEST" PATH="$BIN:$PATH" TMUX_CALLS="$TMP/tmux-calls" TMUX_SENT="$sent" \
    TMUX_HAS_SESSION="${TMUX_HAS_SESSION:-0}" TMUX_WINDOWS="${TMUX_WINDOWS:-}" TMUX_PANE_FILE="${TMUX_PANE_FILE:-}" \
    CODEX_CALLS="$TMP/codex-calls" COPILOT_CALLS="$TMP/copilot-calls" AGY_CALLS="$TMP/agy-calls" \
    OPENCODE_CALLS="$TMP/opencode-calls" OPENCODE_QUOTA_MODELS="${OPENCODE_QUOTA_MODELS:-}" OPENCODE_LOOP_MODELS="${OPENCODE_LOOP_MODELS:-}" OPENCODE_LANE_POLL=1 \
    TEST_CWD="$REPO" RUN_LANE_COMMANDS=1 \
    bash "$SCRIPT" --claude-because "engines test fixture" "$@") >"$output" 2>&1
  status=$?
  set -e
  LAST_STATUS="$status"
  LAST_OUTPUT="$(cat "$output")"
  LAST_COMMAND="$(cat "$sent" 2>/dev/null || true)"
}

# Default engine remains Claude and prompt contents stay in the prompt file.
HELP_OUTPUT="$(HOME="$HOME_TEST" bash "$SCRIPT" --help)"
assert_contains "$HELP_OUTPUT" '--engine claude|codex|copilot|agy' 'help documents available engines'
assert_contains "$HELP_OUTPUT" '--allow-client' 'help documents the client override'

run_lane default-lane "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'default Claude lane starts'
assert_contains "$LAST_COMMAND" 'claude --model sonnet' 'default command uses Claude'
assert_contains "$LAST_COMMAND" '$(cat ' 'default command reads the prompt file'
assert_contains "$LAST_COMMAND" '--add-dir' 'Claude lane start gets its hand-off folder as a working folder'
assert_contains "$LAST_COMMAND" 'default-lane' 'Claude lane --add-dir names the hand-off folder'
assert_not_contains "$LAST_COMMAND" 'touch injected' 'default prompt is not interpolated into the shell command'
# A Claude lane start also refreshes the hub's lean project settings (hub_lean.py), so the tower gets them too.
HUB_LEAN_FILE="$HOME_TEST/repos/.claude/settings.json"
[ -f "$HUB_LEAN_FILE" ] || fail 'a Claude lane start installs the hub lean settings'
assert_contains "$(cat "$HUB_LEAN_FILE")" '"Artifact"' 'hub lean settings withhold the Artifact tools'

# Codex gets the required workspace flags and omits Claude model names.
run_lane --engine codex codex-lane "$REPO" opus "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'Codex lane starts'
assert_contains "$LAST_COMMAND" 'codex exec -C ' 'Codex uses exec and the working directory'
assert_contains "$LAST_COMMAND" '-s workspace-write --add-dir ' 'Codex gets workspace-write and lane access'
assert_not_contains "$LAST_COMMAND" '--add-dir /var/run' 'Codex does not mount /var/run (bwrap cannot mkdir /run/.git)'
assert_contains "$LAST_COMMAND" 'GIT_SSH_COMMAND="ssh -F $HOME/.ssh/config"' 'Codex git ssh skips the system ssh config'
assert_contains "$LAST_COMMAND" '-c sandbox_workspace_write.network_access=true' 'Codex gets network access'
assert_not_contains "$LAST_COMMAND" ' -m ' 'Codex omits Claude model names'
assert_contains "$LAST_COMMAND" '$(cat -- ' 'Codex reads the prompt file'
assert_not_contains "$LAST_COMMAND" 'touch injected' 'Codex prompt is not interpolated into the shell command'
assert_contains "$LAST_COMMAND" 'out.log' 'Codex output goes to out.log'
assert_contains "$(cat "$HOME_TEST/.claude/state/lanes/$(date +%F)-codex-lane/out.log")" 'exit 0' 'Codex exit code is recorded in out.log'
[ ! -e "$REPO/injected" ] || fail 'Codex prompt contents are not executed by the shell'
checks=$((checks + 1))

run_lane --engine codex codex-model-lane "$REPO" gpt-test "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'Codex accepts a non-Claude model'
assert_contains "$LAST_COMMAND" '-m gpt-test' 'Codex passes non-Claude models'

run_lane --engine codex codex-prefixed-claude-model "$REPO" claude-sonnet-5-5 "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'Codex accepts a versioned Claude model name'
assert_not_contains "$LAST_COMMAND" ' -m ' 'Codex omits the flag for any claude-prefixed model'

# Copilot runs from the repo, permits tools, and omits Claude model names.
run_lane --engine copilot copilot-lane "$REPO" fable "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'Copilot lane starts'
assert_contains "$LAST_COMMAND" 'cd ' 'Copilot changes to the repository'
assert_contains "$LAST_COMMAND" 'copilot -p ' 'Copilot uses prompt mode'
assert_contains "$LAST_COMMAND" '--allow-all-tools --log-dir ' 'Copilot gets tool access and its log directory'
COPILOT_HAND="$HOME_TEST/.claude/state/lanes/$(date +%F)-copilot-lane"
assert_contains "$LAST_COMMAND" "--add-dir $(printf %q "$COPILOT_HAND")" 'Copilot gets write access to the lane folder'
assert_not_contains "$LAST_COMMAND" '--model ' 'Copilot omits Claude model names'
assert_contains "$LAST_COMMAND" 'out.log' 'Copilot output goes to out.log'
assert_contains "$(cat "$HOME_TEST/.claude/state/lanes/$(date +%F)-copilot-lane/out.log")" 'exit 0' 'Copilot exit code is recorded in out.log'
[ ! -e "$REPO/injected" ] || fail 'Copilot prompt contents are not executed by the shell'
checks=$((checks + 1))

run_lane --engine copilot copilot-model-lane "$REPO" model-x "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'Copilot accepts a non-Claude model'
assert_contains "$LAST_COMMAND" '--model model-x' 'Copilot passes non-Claude models'

run_lane --engine copilot copilot-prefixed-claude-model "$REPO" claude-3-5-sonnet-20241022 "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'Copilot accepts a dated Claude model name'
assert_not_contains "$LAST_COMMAND" '--model ' 'Copilot omits the flag for any claude-prefixed model'

# agy is edit-only: accept-edits and the sandbox, never a permission bypass, the hand-off folder added, and its own model names only.
run_lane --engine agy agy-lane "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'agy lane starts'
AGY_HAND="$HOME_TEST/.claude/state/lanes/$(date +%F)-agy-lane"
assert_contains "$LAST_COMMAND" 'agy -p ' 'agy uses prompt mode'
assert_contains "$LAST_COMMAND" '--mode accept-edits --sandbox --print-timeout 0 ' 'agy gets accept-edits, the sandbox and no print timeout'
assert_contains "$LAST_COMMAND" "--add-dir $(printf %q "$AGY_HAND")" 'agy gets write access to the lane folder'
assert_contains "$LAST_COMMAND" '--model gemini-3.8-flash-high' 'a Claude alias becomes the default agy model'
assert_contains "$LAST_COMMAND" '$(cat -- ' 'agy reads the prompt file'
assert_contains "$LAST_COMMAND" 'out.log' 'agy output goes to out.log'
assert_contains "$(cat "$AGY_HAND/out.log")" 'exit 0' 'agy exit code is recorded in out.log'
assert_not_contains "$LAST_COMMAND" 'dangerously-skip-permissions' 'agy never skips permissions'
assert_not_contains "$LAST_COMMAND" '--mode auto' 'agy never runs in auto mode'
assert_not_contains "$(cat "$TMP/agy-calls")" 'dangerously-skip-permissions' 'the agy binary is never called with a permission bypass'
[ ! -e "$REPO/injected" ] || fail 'agy prompt contents are not executed by the shell'
checks=$((checks + 1))
# The header is on the lane's copy of the prompt, ahead of the caller's words; the caller's file is untouched.
AGY_PROMPT_COPY="$(cat "$AGY_HAND/prompt.md")"
assert_contains "$AGY_PROMPT_COPY" "You can edit files but cannot run commands. Make the change, then write $AGY_HAND/report.md: files changed, what each change does, and the exact commands the hub must run to test and commit." 'agy prompt copy carries the edit-only header'
assert_contains "$AGY_PROMPT_COPY" "Open only files in this checkout and in $AGY_HAND: any other read is refused and ends the run" 'agy header keeps reads inside the checkout and the lane folder'
assert_contains "$AGY_PROMPT_COPY" "$(cat "$PROMPT")" 'agy prompt copy keeps the caller prompt'
case "$AGY_PROMPT_COPY" in "You can edit files"*) checks=$((checks + 1)) ;; *) fail 'the agy header comes first' ;; esac
assert_not_contains "$(cat "$PROMPT")" 'You can edit files but cannot run commands' 'the caller prompt file is unchanged'
# A restart from a prompt that already sits in the hand-off folder, header and all, does not stack a second header.
AGY_AGAIN_HAND="$HOME_TEST/.claude/state/lanes/$(date +%F)-agy-again"
mkdir -p "$AGY_AGAIN_HAND"
printf '%s\n' "$AGY_PROMPT_COPY" >"$AGY_AGAIN_HAND/prompt.md"
run_lane --engine agy agy-again "$REPO" sonnet "$AGY_AGAIN_HAND/prompt.md"
assert_exit 0 "$LAST_STATUS" 'agy restart from the hand-off prompt starts'
[ "$(grep -c '^You can edit files but cannot run commands' "$AGY_AGAIN_HAND/prompt.md")" = 1 ] || fail 'a restarted agy prompt keeps exactly one header'
checks=$((checks + 1))

# Model mapping: agy's own names pass through, Claude Code aliases and unknown names become the default.
for pair in gemini-3.8-flash-low:gemini-3.8-flash-low gemini-3.8-flash-medium:gemini-3.8-flash-medium gpt-oss-120b-medium:gpt-oss-120b-medium \
            claude-sonnet-4-6:claude-sonnet-4-6 claude-opus-4-6-thinking:claude-opus-4-6-thinking \
            opus:gemini-3.8-flash-high sonnet:gemini-3.8-flash-high haiku:gemini-3.8-flash-high fable:gemini-3.8-flash-high \
            claude-sonnet-5-5:gemini-3.8-flash-high claude-3-5-sonnet-20241022:gemini-3.8-flash-high model-x:gemini-3.8-flash-high; do
  run_lane --engine agy "agy-map-${pair%%:*}" "$REPO" "${pair%%:*}" "$PROMPT"
  assert_exit 0 "$LAST_STATUS" "agy accepts model ${pair%%:*}"
  case "$LAST_COMMAND" in *"--model ${pair#*:} >"*|*"--model ${pair#*:}"[[:space:]]">"*) checks=$((checks + 1)) ;; *) fail "agy model ${pair%%:*} should map to ${pair#*:}: $LAST_COMMAND" ;; esac
done

run_lane --dry-run --engine agy agy-dry "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'agy dry run works'
assert_contains "$LAST_OUTPUT" 'agy\ -p\ ' 'agy dry run prints the command'
assert_not_contains "$LAST_OUTPUT" 'dangerously-skip-permissions' 'agy dry run never skips permissions'

# Sending follow-up text to one-shot lanes must stop before touching their tmux window.
run_lane --send codex-lane 'echo should-not-be-sent'
assert_exit 1 "$LAST_STATUS" 'Codex lane refuses --send'
assert_contains "$LAST_OUTPUT" 'start a new lane with a follow-up prompt' 'Codex --send refusal gives the next step'
[ ! -e "$TMP/tmux-sent" ] || fail 'Codex --send does not type into tmux'
checks=$((checks + 1))
run_lane --send copilot-lane 'echo should-not-be-sent'
assert_exit 1 "$LAST_STATUS" 'Copilot lane refuses --send'
assert_contains "$LAST_OUTPUT" 'start a new lane with a follow-up prompt' 'Copilot --send refusal gives the next step'
[ ! -e "$TMP/tmux-sent" ] || fail 'Copilot --send does not type into tmux'
checks=$((checks + 1))

run_lane --send agy-lane 'echo should-not-be-sent'
assert_exit 1 "$LAST_STATUS" 'agy lane refuses --send'
assert_contains "$LAST_OUTPUT" 'start a new lane with a follow-up prompt' 'agy --send refusal gives the next step'
[ ! -e "$TMP/tmux-sent" ] || fail 'agy --send does not type into tmux'
checks=$((checks + 1))

# Claude lanes retain the interactive --send, and an emptied input line is not delivery: the words must be in the lane's transcript.
LANE_SEND_CONFIRM_SECS=1 TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane 'continue this work'
assert_exit 1 "$LAST_STATUS" 'Claude send with no transcript entry is not counted as sent'
assert_contains "$LAST_OUTPUT" 'NOT IN ITS TRANSCRIPT' 'Claude --send says the words are not in the transcript'

# opencode: headless, runs opencode_lane.sh with the checkout, the lane folder, a free-API model and the prompt file; no key or prompt in the command.
: >"$TMP/opencode-calls"
run_lane --engine opencode oc-lane "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'opencode lane starts'
OC_HAND="$HOME_TEST/.claude/state/lanes/$(date +%F)-oc-lane"
assert_contains "$LAST_COMMAND" 'opencode_lane.sh' 'opencode runs through the wrapper'
assert_contains "$LAST_COMMAND" "$(printf %q "$OC_HAND")" 'opencode gets the lane folder'
assert_contains "$LAST_COMMAND" 'google/gemini-3-flash-preview' 'a Claude alias becomes the free Gemini model'
assert_contains "$LAST_COMMAND" "$(printf %q "$OC_HAND/prompt.md")" 'the opencode prompt is a file the wrapper reads at run time'
assert_not_contains "$LAST_COMMAND" 'touch injected' 'the opencode prompt is not interpolated into the shell command'
assert_not_contains "$LAST_COMMAND" 'api-key' 'no key file is named in the opencode command'
assert_not_contains "$LAST_COMMAND" 'API_KEY' 'no key variable is named in the opencode command'
assert_contains "$LAST_COMMAND" 'out.log' 'opencode output goes to out.log'
assert_contains "$(cat "$OC_HAND/out.log")" 'exit 0' 'opencode exit code is recorded in out.log'
assert_contains "$(cat "$OC_HAND/out.log")" 'opencode: model google/gemini-3-flash-preview' 'the wrapper says which model ran'
assert_contains "$(cat "$TMP/opencode-calls")" "google/gemini-3-flash-preview $OC_HAND/opencode.json" 'opencode is called with the free model and the lane-folder config'
assert_contains "$(cat "$OC_HAND/opencode.json")" '"external_directory":{"*":"deny"' 'opencode may not leave the checkout except for two named folders'
assert_contains "$(cat "$OC_HAND/opencode.json")" "$OC_HAND/**" 'opencode may write the lane folder'
assert_contains "$(cat "$OC_HAND/opencode.json")" "$HOME_TEST/repos/.worktrees/$(basename "$REPO")--*/**" 'opencode may use its own repo worktrees'
assert_contains "$(cat "$OC_HAND/opencode.json")" "$HOME_TEST/repos/.worktrees/$(basename "$REPO")-wf-*/**" 'opencode may use its own director worktrees'
assert_not_contains "$(cat "$OC_HAND/opencode.json")" "$HOME_TEST/repos/.worktrees/**" 'opencode may not use every repo worktree'
[ ! -e "$REPO/opencode.json" ] || fail 'opencode writes its config to the lane folder, never into the repo'
[ ! -e "$REPO/injected" ] || fail 'opencode prompt contents are not executed by the shell'
assert_contains "$(cat "$OC_HAND/prompt.md")" "$(cat "$PROMPT")" 'opencode prompt copy keeps the caller prompt'
case "$(head -n1 "$OC_HAND/prompt.md")" in 'You can edit files'*) fail 'opencode prompt copy has no edit-only header' ;; *) checks=$((checks + 1)) ;; esac
assert_contains "$(cat "$OC_HAND/out.log")" 'tests ran' 'the lane output is kept even when it talks about rate limits'
assert_not_contains "$(cat "$OC_HAND/out.log")" 'free quota exhausted' 'a lane whose output only mentions quota is not out of quota'
case "$(grep -P '\toc-lane\t' "$HOME_TEST/.claude/state/lanes/index.tsv")" in *$'\topencode\t'*) checks=$((checks + 1)) ;; *) fail 'the index row says billing opencode' ;; esac

# Model mapping: provider/model names on a provider whose key the wrapper loads pass through, anything else is the free default.
for pair in google/gemini-3.8-flash=google/gemini-3.8-flash openrouter/nvidia/nemotron-3-ultra-550b-a55b:free=openrouter/nvidia/nemotron-3-ultra-550b-a55b:free groq/openai/gpt-oss-120b=google/gemini-3-flash-preview opus=google/gemini-3-flash-preview; do
  run_lane --dry-run --engine opencode "oc-map" "$REPO" "${pair%%=*}" "$PROMPT"
  assert_exit 0 "$LAST_STATUS" "opencode accepts model ${pair%%=*}"
  case "$LAST_OUTPUT" in *"$(printf %q "${pair#*=}")"*) checks=$((checks + 1)) ;; *) fail "opencode model ${pair%%=*} should map to ${pair#*=}: $LAST_OUTPUT" ;; esac
done
# Groq's free tier (8000 tokens/minute) is smaller than OpenCode's own prompt, so it is never passed through.
run_lane --dry-run --engine opencode oc-free "$REPO" groq/openai/gpt-oss-120b "$PROMPT"
assert_not_contains "$(grep 'send-keys' <<<"$LAST_OUTPUT")" 'groq' 'opencode never runs on Groq'

# Fallback: the first provider reports its free limit, the second takes the same prompt; both out leaves the line the dispatcher reads.
: >"$TMP/opencode-calls"
OPENCODE_QUOTA_MODELS="google/gemini-3-flash-preview" run_lane --engine opencode oc-fallback "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'opencode fallback lane starts'
OC_FB="$(cat "$HOME_TEST/.claude/state/lanes/$(date +%F)-oc-fallback/out.log")"
assert_contains "$OC_FB" 'google/gemini-3-flash-preview is out of free quota or rate limit; trying the next provider' 'the first provider out of quota is named'
assert_contains "$OC_FB" 'opencode: model openrouter/nvidia/nemotron-3-ultra-550b-a55b:free' 'the second provider takes over'
assert_contains "$OC_FB" 'tests ran' 'the second provider ran the prompt'
assert_not_contains "$OC_FB" 'free quota exhausted' 'one provider left means not exhausted'
assert_contains "$OC_FB" 'exit 0' 'the fallback run ends with exit 0'
[ "$(grep -c '' "$TMP/opencode-calls")" = 2 ] || fail 'the fallback calls opencode twice'
: >"$TMP/opencode-calls"
OPENCODE_QUOTA_MODELS="google/gemini-3-flash-preview openrouter/nvidia/nemotron-3-ultra-550b-a55b:free" run_lane --engine opencode oc-exhausted "$REPO" sonnet "$PROMPT"
OC_EX="$(cat "$HOME_TEST/.claude/state/lanes/$(date +%F)-oc-exhausted/out.log")"
assert_contains "$OC_EX" 'opencode: free quota exhausted on every provider' 'both providers out ends with the line the dispatcher reads'
assert_not_contains "$OC_EX" 'exit 0' 'an exhausted opencode lane does not exit 0'
assert_contains "$OC_EX" 'exit 75' 'an exhausted opencode lane exits 75'
# A provider that loops on its limit error (OpenCode retries forever) is stopped while it runs, not left to the time limit.
: >"$TMP/opencode-calls"
OPENCODE_LOOP_MODELS="google/gemini-3-flash-preview" run_lane --engine opencode oc-loop "$REPO" sonnet "$PROMPT"
OC_LP="$(cat "$HOME_TEST/.claude/state/lanes/$(date +%F)-oc-loop/out.log")"
assert_contains "$OC_LP" 'google/gemini-3-flash-preview is out of free quota or rate limit' 'a looping provider is caught while it runs'
assert_contains "$OC_LP" 'opencode: model openrouter/' 'a looping provider falls through to the next'

# The client guard covers opencode like the other engines, and ~/repos itself is refused too (the free APIs train on input).
run_lane --engine opencode blocked-client-oc "$HOME_TEST/repos/sample-client-repo" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'opencode: protected repository is refused'
assert_contains "$LAST_OUTPUT" 'refusing engine opencode' 'opencode refusal names the engine'
assert_contains "$LAST_OUTPUT" 'protected client repository' 'opencode refusal explains the client-repository guard'
run_lane --engine opencode blocked-listed-oc "$HOME_TEST/repos/listed-only-repo" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'opencode: client-repos-only repository is refused'
run_lane --engine opencode blocked-cd-oc "$HOME_TEST/repos/CD/nested" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'opencode: the protected CD tree is refused'
run_lane --engine opencode blocked-home-repos "$HOME_TEST/repos" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'opencode: ~/repos itself is refused'
assert_contains "$LAST_OUTPUT" 'free APIs train on their input' 'the ~/repos refusal says why'
run_lane --engine opencode blocked-home-oc "$HOME_TEST" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'opencode: the folder above ~/repos is refused'
run_lane --engine opencode blocked-root-oc / sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'opencode: / is refused'
run_lane --engine opencode --allow-client allowed-client-oc "$HOME_TEST/repos/sample-client-repo" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'opencode: --allow-client bypasses the repository guard'
mv "$HOME_TEST/repos/hub/client-repos.txt" "$TMP/client-repos.txt"
run_lane --engine opencode missing-repos-list-oc "$REPO" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'opencode: a missing client list fails closed'
mv "$TMP/client-repos.txt" "$HOME_TEST/repos/hub/client-repos.txt"
run_lane --dry-run --engine bogus oc-bad "$REPO" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'an unknown engine is refused'
assert_contains "$LAST_OUTPUT" 'agy, or opencode' 'the unknown-engine message lists opencode'
run_lane --engine opencode --console oc-console "$REPO" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'opencode: --console is refused'
run_lane --send oc-lane 'echo should-not-be-sent'
assert_exit 1 "$LAST_STATUS" 'opencode lane refuses --send'

# The second TSV field expands ~/ and protects client repositories; comments, blanks,
# and the header row are not repository paths.
run_lane --engine codex blocked-client "$HOME_TEST/repos/sample-client-repo" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'protected repository is refused'
assert_contains "$LAST_OUTPUT" 'protected client repository' 'refusal explains the client-repository guard'

# The client guard covers agy exactly like Codex and Copilot.
run_lane --engine agy blocked-client-agy "$HOME_TEST/repos/sample-client-repo" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'agy: protected repository is refused'
assert_contains "$LAST_OUTPUT" 'refusing engine agy' 'agy refusal names the engine'
assert_contains "$LAST_OUTPUT" 'protected client repository' 'agy refusal explains the client-repository guard'
run_lane --engine agy blocked-listed-agy "$HOME_TEST/repos/listed-only-repo" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'agy: client-repos-only repository is refused'
run_lane --engine agy --allow-client allowed-client-agy "$HOME_TEST/repos/sample-client-repo" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'agy: --allow-client bypasses the repository guard'

# client-repos.txt protects repositories absent from the sender list, including children.
run_lane --engine codex blocked-listed-only "$HOME_TEST/repos/listed-only-repo" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'client-repos-only repository is refused'
run_lane --engine codex blocked-listed-child "$HOME_TEST/repos/listed-only-repo/nested" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'subfolder of a client-repos entry is refused'

# Commented paths do not protect repositories.
run_lane --engine codex allowed-commented "$HOME_TEST/repos/commented-client-repo" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'commented-out path is not protected'
run_lane --engine codex allowed-header-path "$TMP/repo_dir" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'client-senders.tsv header is not treated as a repository path'

run_lane --engine copilot --allow-client allowed-client "$HOME_TEST/repos/sample-client-repo" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" '--allow-client bypasses the repository guard'
assert_contains "$LAST_COMMAND" 'copilot -p ' '--allow-client starts the selected engine'

# A protected main checkout also protects linked worktrees outside its path.
run_lane --engine codex blocked-protected-worktree "$PROTECTED_WORKTREE" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'worktree of a protected repository is refused'
assert_contains "$LAST_OUTPUT" 'protected client repository' 'worktree refusal explains the repository guard'
mkdir "$PROTECTED_WORKTREE/nested"
run_lane --engine codex blocked-protected-worktree-child "$PROTECTED_WORKTREE/nested" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'subfolder of a protected worktree is refused'
run_lane --engine codex allowed-unprotected-worktree "$UNPROTECTED_WORKTREE" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'worktree of an unprotected repository is allowed'
run_lane --engine codex --allow-client allowed-protected-worktree "$PROTECTED_WORKTREE" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" '--allow-client overrides the protected worktree check'

run_lane --engine codex blocked-cd "$HOME_TEST/repos/CD/nested" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'the protected CD tree is refused'
assert_contains "$LAST_OUTPUT" 'under the protected ~/repos/CD tree' 'CD refusal explains the guard'

# A directory at either list path is invalid, and --allow-client still overrides it.
mv "$HOME_TEST/repos/hub/client-senders.tsv" "$TMP/client-senders.tsv"
mkdir "$HOME_TEST/repos/hub/client-senders.tsv"
run_lane --engine codex directory-senders-list "$REPO" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'directory at client-senders.tsv is refused'
assert_contains "$LAST_OUTPUT" 'client-senders.tsv is missing or unreadable' 'directory sender-list refusal names the file'
run_lane --engine codex --allow-client directory-senders-list-allowed "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" '--allow-client overrides a directory at client-senders.tsv'
rmdir "$HOME_TEST/repos/hub/client-senders.tsv"
mv "$TMP/client-senders.tsv" "$HOME_TEST/repos/hub/client-senders.tsv"

mv "$HOME_TEST/repos/hub/client-repos.txt" "$TMP/client-repos.txt"
mkdir "$HOME_TEST/repos/hub/client-repos.txt"
run_lane --engine codex directory-repos-list "$REPO" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'directory at client-repos.txt is refused'
assert_contains "$LAST_OUTPUT" 'client-repos.txt is missing or unreadable' 'directory repo-list refusal names the file'
run_lane --engine codex --allow-client directory-repos-list-allowed "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" '--allow-client overrides a directory at client-repos.txt'
rmdir "$HOME_TEST/repos/hub/client-repos.txt"
mv "$TMP/client-repos.txt" "$HOME_TEST/repos/hub/client-repos.txt"

# A missing client-repos.txt also fails closed, unless explicitly overridden.
mv "$HOME_TEST/repos/hub/client-repos.txt" "$TMP/client-repos.txt"
run_lane --engine codex missing-repos-list "$REPO" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'missing client-repos.txt is refused'
assert_contains "$LAST_OUTPUT" 'client-repos.txt is missing or unreadable' 'missing client-repos refusal names the file'
run_lane --engine codex --allow-client missing-repos-list-allowed "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" '--allow-client bypasses a missing client-repos.txt'
mv "$TMP/client-repos.txt" "$HOME_TEST/repos/hub/client-repos.txt"

# A missing TSV fails closed, including for a non-client repository.
mv "$HOME_TEST/repos/hub/client-senders.tsv" "$TMP/client-senders.tsv"
run_lane --engine codex missing-list "$REPO" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'missing repository list is refused'
assert_contains "$LAST_OUTPUT" 'client-senders.tsv is missing or unreadable' 'missing-list refusal explains why'
run_lane --engine codex --allow-client missing-list-allowed "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" '--allow-client bypasses a missing list'

INDEX="$HOME_TEST/.claude/state/lanes/index.tsv"
# Resume checks use the latest index billing value before checking for a session id.
for lane_engine in codex copilot agy; do
  resume_name="resume-$lane_engine-check"
  printf '%s\t%s\t%s\tsonnet\t%s\t%s\t%s\t-\t-\n' "$(date +%F)" "$resume_name" "$REPO" "$lane_engine" "$PROMPT" "$TMP/no-report" >>"$INDEX"
  run_lane --resume "$resume_name"
  assert_exit 1 "$LAST_STATUS" "$lane_engine lane refuses --resume without an interactive session"
  assert_contains "$LAST_OUTPUT" 'headless lane' "$lane_engine --resume refusal identifies a headless lane"
  assert_contains "$LAST_OUTPUT" 'start a new lane with a follow-up prompt' "$lane_engine --resume refusal gives the next step"
done

# Claude lanes retain the existing resume command construction.
printf '%s\tclaude-resume-check\t%s\tsonnet\tsubscription\t%s\t%s\tsession-123\t-\n' \
  "$(date +%F)" "$REPO" "$PROMPT" "$TMP/claude-resume-report.md" >>"$INDEX"
run_lane --dry-run --resume claude-resume-check
assert_exit 0 "$LAST_STATUS" 'Claude lane still supports --resume'
assert_contains "$LAST_OUTPUT" 'claude\ --resume\ session-123\ --model\ sonnet' 'Claude resume command remains unchanged'
assert_contains "$LAST_OUTPUT" "--add-dir\\ $TMP" 'Claude resume gets the report folder as a working folder'

# Existing subscription rows and new engine values remain readable in --status.
assert_contains "$(cut -f5 "$INDEX")" 'subscription' 'default lane keeps the old billing value'
assert_contains "$(cut -f5 "$INDEX")" 'codex' 'Codex is recorded in the existing column'
assert_contains "$(cut -f5 "$INDEX")" 'copilot' 'Copilot is recorded in the existing column'
if TMUX_HAS_SESSION=1 TMUX_WINDOWS='default-lane codex-lane copilot-lane' \
  HOME="$HOME_TEST" PATH="$BIN:$PATH" TMUX_CALLS="$TMP/status-calls" TMUX_SENT="$TMP/status-sent" \
  bash "$SCRIPT" --status >"$TMP/status.out" 2>&1; then
  checks=$((checks + 1))
else
  cat "$TMP/status.out" >&2
  fail '--status exits successfully for old and new rows'
fi
STATUS_OUTPUT="$(cat "$TMP/status.out")"
assert_contains "$STATUS_OUTPUT" 'engine/billing:subscription' 'status still reads legacy billing rows'
assert_contains "$STATUS_OUTPUT" 'engine/billing:codex' 'status displays the Codex engine'
assert_contains "$STATUS_OUTPUT" 'engine/billing:copilot' 'status displays the Copilot engine'

# Lean Claude lanes: effort high on every model, connectors/plugins/Artifact off, workspace CLAUDE.md swapped for the slim rules.
PROMPT_MAIL="$TMP/prompt-mail.md"
printf 'Check the Gmail drafts for replies.\n' >"$PROMPT_MAIL"
run_lane --allow-high-tier "engines test fixture" lean-opus "$REPO" opus "$PROMPT"
assert_contains "$LAST_COMMAND" '--effort high' 'opus defaults to effort high'
assert_not_contains "$LAST_COMMAND" 'xhigh' 'no lane defaults to xhigh'
assert_contains "$LAST_COMMAND" 'disableClaudeAiConnectors' 'connectors are off by default'
assert_contains "$LAST_COMMAND" 'superpowers@claude-plugins-official' 'listed plugins are switched off'
assert_contains "$LAST_COMMAND" 'creative-writing@grey-haven-plugins' 'creative-writing is switched off'
assert_not_contains "$LAST_COMMAND" 'afa-mods' 'afa-mods stays on'
assert_not_contains "$LAST_COMMAND" 'security-guidance' 'security-guidance stays on'
assert_contains "$LAST_COMMAND" '--disallowedTools Artifact' 'the Artifact tools are withheld'
assert_not_contains "$LAST_COMMAND" 'claudeMdExcludes' 'workspace CLAUDE.md is kept while no rule file exists'
assert_not_contains "$LAST_COMMAND" 'append-system-prompt-file' 'no rule file, no appended rules'
assert_not_contains "$LAST_COMMAND" 'autoMemoryEnabled' 'the memory index is kept while no rule file exists'
assert_contains "$LAST_OUTPUT" 'connectors off' 'start line reports connectors off'
run_lane lean-haiku "$REPO" haiku "$PROMPT"
assert_not_contains "$LAST_COMMAND" '--effort' 'haiku takes no effort'
run_lane --allow-high-tier "engines test fixture" --effort max lean-max "$REPO" opus "$PROMPT"
assert_contains "$LAST_COMMAND" '--effort max' '--effort still overrides'

printf '# Lane rules\n' >"$HOME_TEST/repos/hub/lane-rules.md"
mkdir -p "$HOME_TEST/.claude/commands" "$HOME_TEST/.claude/skills/zz-off-skill" "$HOME_TEST/.claude/skills/graphify" "$HOME_TEST/.claude/skills/no-skill-file"
: >"$HOME_TEST/.claude/commands/zz-off-cmd.md"; : >"$HOME_TEST/.claude/commands/prep.md"
: >"$HOME_TEST/.claude/skills/zz-off-skill/SKILL.md"; : >"$HOME_TEST/.claude/skills/graphify/SKILL.md"
run_lane lean-rules "$REPO" sonnet "$PROMPT"
assert_contains "$LAST_COMMAND" 'claudeMdExcludes' 'workspace CLAUDE.md is excluded once the rule file exists'
assert_contains "$LAST_COMMAND" "$HOME_TEST/repos/CLAUDE.md" 'the exclude names the workspace CLAUDE.md'
assert_contains "$LAST_COMMAND" "--append-system-prompt-file $HOME_TEST/repos/hub/lane-rules.md" 'the slim rules are appended'
assert_contains "$LAST_COMMAND" 'autoMemoryEnabled' 'the memory index is off once the rule file exists'
assert_contains "$LAST_COMMAND" 'syncClaudeAiSkills' 'claude.ai synced skills are off'
assert_contains "$LAST_COMMAND" 'skillOverrides' 'skills not on the keep list are switched off'
assert_contains "$LAST_COMMAND" 'zz-off-cmd\":\"off' 'a command not on the keep list is off'
assert_contains "$LAST_COMMAND" 'zz-off-skill\":\"off' 'a skill not on the keep list is off'
assert_not_contains "$LAST_COMMAND" 'prep\":\"off' 'a kept command stays on'
assert_not_contains "$LAST_COMMAND" 'graphify\":\"off' 'a kept skill stays on'
assert_not_contains "$LAST_COMMAND" 'no-skill-file' 'a folder without SKILL.md is not a skill'

run_lane lean-mail "$REPO" sonnet "$PROMPT_MAIL"
assert_not_contains "$LAST_COMMAND" 'disableClaudeAiConnectors' 'a prompt that mentions Gmail keeps the connectors'
assert_contains "$LAST_OUTPUT" 'connectors on' 'start line reports connectors on'
run_lane triage-mail "$REPO" sonnet "$PROMPT"
assert_not_contains "$LAST_COMMAND" 'disableClaudeAiConnectors' 'a -mail name keeps the connectors'
run_lane --connectors lean-flag "$REPO" sonnet "$PROMPT"
assert_not_contains "$LAST_COMMAND" 'disableClaudeAiConnectors' '--connectors keeps the connectors'
run_lane --no-connectors lean-no-connectors-mail "$REPO" sonnet "$PROMPT_MAIL"
assert_contains "$LAST_COMMAND" 'disableClaudeAiConnectors' '--no-connectors overrides the lane name and prompt'
assert_contains "$LAST_OUTPUT" 'connectors off' '--no-connectors reports connectors off'
conn_of() { awk -F'\t' -v n="$1" '$2==n{c=$11} END{print c}' "$INDEX"; }
[ "$(conn_of lean-opus)" = off ] || fail 'index records connectors off'
[ "$(conn_of lean-mail)" = on ] || fail 'index records connectors on'
checks=$((checks + 2))
ent_of() { awk -F'\t' -v n="$1" '$2==n{c=$13} END{print c}' "$INDEX"; }
[ "$(ent_of lean-opus)" = 'engines test fixture' ] || fail "an opus start writes its --allow-high-tier reason to column 13, got: $(ent_of lean-opus)"
[ "$(ent_of lean-mail)" = - ] || fail "a sonnet start writes - to column 13, got: $(ent_of lean-mail)"
[ "$(awk -F'\t' '$2=="lean-opus"{c=$12} END{print c}' "$INDEX")" = 100 ] || fail 'column 12 (recycle line) stays a number before the reason'
checks=$((checks + 3))

# --resume reuses the recorded choice; a row from before the column counts as on.
for pair in res-off:off res-on:on; do
  printf '%s\t%s\t%s\tsonnet\tsubscription\t%s\t%s\tsession-x\t-\thigh\t%s\n' \
    "$(date +%F)" "${pair%%:*}" "$REPO" "$PROMPT" "$TMP/r.md" "${pair#*:}" >>"$INDEX"
done
run_lane --dry-run --resume res-off
assert_contains "$LAST_OUTPUT" 'disableClaudeAiConnectors' 'resume keeps connectors off for an off lane'
assert_contains "$LAST_OUTPUT" 'effort\ high' 'resume reuses the recorded effort'
run_lane --dry-run --resume res-on
assert_not_contains "$LAST_OUTPUT" 'disableClaudeAiConnectors' 'resume keeps connectors on for an on lane'
printf '%s\told-row\t%s\tsonnet\tsubscription\t%s\t%s\tsession-old\t-\thigh\n' "$(date +%F)" "$REPO" "$PROMPT" "$TMP/old-report.md" >>"$INDEX"
run_lane --dry-run --resume old-row
assert_not_contains "$LAST_OUTPUT" 'disableClaudeAiConnectors' 'a ten-column row resumes with connectors on'
run_lane --dry-run --connectors --resume res-off
assert_not_contains "$LAST_OUTPUT" 'disableClaudeAiConnectors' '--connectors turns them on at resume'

# Each lane has its own recycle line (--recycle-at, else the default for its kind of work) and its backstop follows it. Claude Code compacts
# at about 0.84 x autoCompactWindow, so the window is (line + 60K) / 0.84 rounded up to 10K and capped at 200000.
plain() { printf '%s' "$1" | tr -d '\\'; }
rk_of() { awk -F'\t' -v n="$1" '$2==n{c=$12} END{print c}' "$INDEX"; }
win_of() { printf '%s' "$LAST_COMMAND" | python3 -c '
import json, shlex, sys
w = shlex.split(sys.stdin.read())
print(json.loads(w[w.index("--settings") + 1])["autoCompactWindow"])'; }
run_lane rk-default "$REPO" sonnet "$PROMPT"
[ "$(rk_of rk-default)" = 100 ] || fail "a plain code lane records the 100K default, got: $(rk_of rk-default)"; checks=$((checks + 1))
[ "$(win_of)" = 200000 ] || fail 'a 100K line gets the 200000 window (the cap)'; checks=$((checks + 1))
run_lane --recycle-at 60 rk-sixty "$REPO" sonnet "$PROMPT"
[ "$(rk_of rk-sixty)" = 60 ] || fail "--recycle-at 60 is stored in the index row, got: $(rk_of rk-sixty)"; checks=$((checks + 1))
[ "$(win_of)" = 150000 ] || fail "a 60K line gets a 150000 backstop (60+60=120K / 0.84 = 143K, rounded up), got $(win_of)"; checks=$((checks + 1))
run_lane --recycle-at=80 rk-eighty "$REPO" sonnet "$PROMPT"
[ "$(rk_of rk-eighty)" = 80 ] && [ "$(win_of)" = 170000 ] || fail "--recycle-at=80: row 80, window 170000, got $(rk_of rk-eighty) / $(win_of)"; checks=$((checks + 1))
LANE_WINDOW_CAP=160000 run_lane rk-capped "$REPO" sonnet "$PROMPT"
[ "$(win_of)" = 160000 ] || fail 'LANE_WINDOW_CAP caps the backstop below the line plus room'; checks=$((checks + 1))
OPS_PROMPT="$TMP/ops-prompt.md"; echo 'Check the Traefik routes and the Portainer stack for the deploy' >"$OPS_PROMPT"
run_lane rk-ops "$REPO" sonnet "$OPS_PROMPT"
[ "$(rk_of rk-ops)" = 110 ] || fail "an ops lane (primes at 57K) defaults to 110K, got: $(rk_of rk-ops)"; checks=$((checks + 1))
SITE_PROMPT="$TMP/site-prompt.md"; echo 'Fit the photos for data/sites/carbarns and write the SketchUp geometry' >"$SITE_PROMPT"
run_lane --allow-low-tier rk-site "$REPO" opus "$SITE_PROMPT"
[ "$(rk_of rk-site)" = 100 ] || fail "a site-model lane defaults to 100K, got: $(rk_of rk-site)"; checks=$((checks + 1))
[ "$(awk -F'\t' '$2=="rk-site"{c=$13} END{print c}' "$INDEX")" = 'site-model prompt (fable or opus only)' ] || fail 'a site-model start records why it may be on opus or fable'; checks=$((checks + 1))
for bad in 30 151 abc ''; do
  run_lane --recycle-at "$bad" rk-bad "$REPO" sonnet "$PROMPT"
  assert_exit 1 "$LAST_STATUS" "--recycle-at '$bad' is refused"
done
assert_contains "$LAST_OUTPUT" '40 to 150' 'the refusal names the range'
run_lane --recycle-at 90 --status
assert_exit 1 "$LAST_STATUS" '--recycle-at only applies to a start or a resume'
# --recycle-point and --resume: the stored value wins, a row without one falls back to the kind's default, --recycle-at overrides on resume.
printf '%s\t%s\t%s\tsonnet\tsubscription\t%s\t%s\tsession-rk\t-\thigh\toff\t70\n' "$(date +%F)" rk-stored "$REPO" "$PROMPT" "$TMP/r.md" >>"$INDEX"
printf '%s\t%s\t%s\tsonnet\tsubscription\t%s\t%s\tsession-rk2\t-\thigh\toff\n' "$(date +%F)" rk-old "$REPO" "$OPS_PROMPT" "$TMP/r.md" >>"$INDEX"
run_lane --recycle-point rk-stored
[ "$LAST_OUTPUT" = 70 ] || fail "--recycle-point reads the stored 70, got: $LAST_OUTPUT"; checks=$((checks + 1))
run_lane --recycle-point rk-old
[ "$LAST_OUTPUT" = 110 ] || fail "a row without the column falls back to its kind (ops: 110), got: $LAST_OUTPUT"; checks=$((checks + 1))
run_lane --recycle-point no-such-lane
[ "$LAST_OUTPUT" = 100 ] || fail "an unknown lane answers the plain default, got: $LAST_OUTPUT"; checks=$((checks + 1))
run_lane --dry-run --resume rk-stored
assert_contains "$(plain "$LAST_OUTPUT")" '"autoCompactWindow":160000' 'resume reuses the stored 70K line: backstop 160000'
run_lane --dry-run --recycle-at 100 --resume rk-stored
assert_contains "$(plain "$LAST_OUTPUT")" '"autoCompactWindow":200000' '--recycle-at overrides the stored line at resume'
# fill_ids rewrites the index; it must keep the twelfth column.
run_lane --status
[ "$(rk_of rk-stored)" = 70 ] || fail "--status (fill_ids) dropped the recycle line, got: $(rk_of rk-stored)"; checks=$((checks + 1))

# --status shows the connectors value, and still reads ten-column rows.
STATUS_CONN="$(TMUX_HAS_SESSION=1 TMUX_WINDOWS='lean-opus lean-mail old-row' HOME="$HOME_TEST" PATH="$BIN:$PATH" \
  TMUX_CALLS="$TMP/status-calls" TMUX_SENT="$TMP/status-sent" bash "$SCRIPT" --status 2>&1)"
assert_contains "$STATUS_CONN" 'connectors:off' 'status shows an off lane'
assert_contains "$STATUS_CONN" 'connectors:on' 'status shows an on lane and a ten-column row'
assert_contains "$STATUS_CONN" 'old-row' 'status lists the ten-column row'

# A dispatcher lane (name oc-*, or AFA_LANE_UNATTENDED=1) tells its Claude process nobody is at the window; a hub lane does not.
run_lane oc-own-1016-0000 "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'dispatcher-named lane starts'
assert_contains "$LAST_COMMAND" 'AFA_LANE=1 AFA_LANE_UNATTENDED=1 AFA_LANE_HAND=' 'oc- lane gets the lane mark and the unattended flag'
assert_contains "$LAST_COMMAND" "$(date +%F)-oc-own-1016-0000" 'oc- lane is told its own folder'
assert_contains "$LAST_COMMAND" 'claude --model sonnet' 'oc- lane still runs Claude'
AFA_LANE_UNATTENDED=1 run_lane env-flagged-lane "$REPO" sonnet "$PROMPT"
assert_contains "$LAST_COMMAND" 'AFA_LANE_UNATTENDED=1 AFA_LANE_HAND=' 'the env flag alone also marks a lane unattended'
run_lane hub-lane "$REPO" sonnet "$PROMPT"
assert_not_contains "$LAST_COMMAND" 'AFA_LANE_UNATTENDED' 'a hub lane is not marked unattended'
assert_contains "$LAST_COMMAND" 'AFA_LANE=1 ' 'every Claude lane is marked a lane, so the guard denies fixable holds instead of asking'
# An unattended lane may make one roadmap post, and its settings carry a narrow rule for exactly that; a hub lane's do not.
# bash printf %q escapes the characters of the JSON, so the settings are compared after the escapes are dropped.
plain() { printf '%s' "$1" | tr -d '\\'; }
run_lane oc-rules-1016-0001 "$REPO" sonnet "$PROMPT"
RULES="$(plain "$LAST_COMMAND")"
assert_contains "$RULES" '"permissions":{"allow":[' 'an oc- lane carries an allow list'
assert_contains "$RULES" 'Bash(python3 ~/.claude/scripts/session_prime_facts.py --post:*)' 'the tilde form of the roadmap post is allowed'
assert_contains "$RULES" "Bash(python3 $HOME_TEST/.claude/scripts/session_prime_facts.py --post:*)" 'the absolute form of the roadmap post is allowed'
assert_contains "$RULES" 'Bash(python3 $HOME/.claude/scripts/session_prime_facts.py --post:*)' 'the $HOME form of the roadmap post is allowed'
assert_not_contains "$RULES" 'Bash(python3:*)' 'no broad python3 rule is added'
printf '%s' "$LAST_COMMAND" | python3 -c '
import json, re, shlex, sys
words = shlex.split(sys.stdin.read())
s = json.loads(words[words.index("--settings") + 1])
allow = s["permissions"]["allow"]
assert len(allow) == 3 and all(a.startswith("Bash(python3 ") and a.endswith("session_prime_facts.py --post:*)") for a in allow), allow
assert s["autoCompactWindow"] == 200000
' || fail 'the unattended settings are valid JSON with exactly the three roadmap rules'
checks=$((checks + 1))
AFA_LANE_UNATTENDED=1 run_lane env-rules "$REPO" sonnet "$PROMPT"
assert_contains "$(plain "$LAST_COMMAND")" 'session_prime_facts.py --post:*' 'the env flag alone also adds the rule'
run_lane hub-rules "$REPO" sonnet "$PROMPT"
assert_not_contains "$(plain "$LAST_COMMAND")" 'session_prime_facts' 'a hub lane gets no roadmap rule'
assert_not_contains "$(plain "$LAST_COMMAND")" '"permissions"' 'a hub lane gets no permissions block'
printf '%s' "$LAST_COMMAND" | python3 -c '
import json, shlex, sys
words = shlex.split(sys.stdin.read())
json.loads(words[words.index("--settings") + 1])
' || fail 'the hub lane settings are still valid JSON'
checks=$((checks + 1))
printf '%s\toc-resume-check\t%s\tsonnet\tsubscription\t%s\t%s\tsession-9\t-\n' "$(date +%F)" "$REPO" "$PROMPT" "$TMP/oc-resume-report.md" >>"$INDEX"
run_lane --dry-run --resume oc-resume-check
assert_contains "$LAST_OUTPUT" 'AFA_LANE_UNATTENDED=1' 'a resumed dispatcher lane stays unattended'

# --ask <id>: after the window starts, the lane is claimed under its OpenClaw ask. A failed claim warns and keeps the lane.
cat >"$TMP/fake_dispatch.py" <<'STUB'
import os, sys
open(os.path.join(os.path.dirname(sys.argv[0]), "claim-log"), "a").write(" ".join(sys.argv[1:]) + "\n")
sys.exit(int(os.environ.get("FAKE_CLAIM_EXIT", "0")))
STUB
export AFA_OPENCLAW_DISPATCH="$TMP/fake_dispatch.py"
run_lane hub-lane-2 "$REPO" sonnet "$PROMPT"
[ ! -e "$TMP/claim-log" ] || fail 'a lane without --ask must not claim anything'
checks=$((checks + 1))
run_lane --ask oc-1a2b3c4d ask-lane "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'a lane with --ask starts'
assert_contains "$(cat "$TMP/claim-log")" 'claim oc-1a2b3c4d ask-lane' '--ask claims the lane under its ask'
assert_contains "$LAST_COMMAND" 'claude --model sonnet' '--ask does not change the lane command'
run_lane --dry-run --ask oc-1a2b3c4d ask-dry "$REPO" sonnet "$PROMPT"
assert_contains "$LAST_OUTPUT" 'would run: openclaw_dispatch.py claim oc-1a2b3c4d ask-dry' 'a dry run says it would claim'
assert_not_contains "$(cat "$TMP/claim-log")" 'ask-dry' 'a dry run claims nothing'
FAKE_CLAIM_EXIT=1 run_lane --ask oc-1a2b3c4d ask-failed-claim "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'a failed claim does not undo the lane'
assert_contains "$LAST_OUTPUT" 'WARNING: the lane is running but claiming ask oc-1a2b3c4d failed' 'a failed claim is loud'
run_lane --ask not-an-id ask-bad "$REPO" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'a malformed ask id is refused'
run_lane --ask oc-1a2b3c4d --status
assert_exit 1 "$LAST_STATUS" '--ask is refused outside a start'
unset AFA_OPENCLAW_DISPATCH

# Claude Code's grey prompt suggestion (SGR 2) is not typed text. Real panes look like the 19 Oct
# capture-pane -e of a lane's empty prompt: the arrow in grey, then a no-break space, then the text.
ESC=$'\033'; NBSP=$'\302\240'
printf '%s\n' 'something the lane said' "${ESC}[38;5;246m❯${NBSP}${ESC}[39m${ESC}[2mpush it${ESC}[22m" '  ⏵⏵ auto mode on' >"$TMP/pane-dim"
printf '%s\n' 'something the lane said' "${ESC}[38;5;246m❯${NBSP}${ESC}[39mpush it" '  ⏵⏵ auto mode on' >"$TMP/pane-typed"
printf '%s\n' 'something the lane said' "${ESC}[38;5;246m❯${NBSP}${ESC}[39m" '  ⏵⏵ auto mode on' >"$TMP/pane-bare"
# A colour whose parameters contain a 2 (38;5;2 and 38;2;...) is not dim.
printf '%s\n' "${ESC}[38;5;246m❯${NBSP}${ESC}[39m${ESC}[38;5;2mtyped${ESC}[39m ${ESC}[38;2;2;2;2mtoo${ESC}[39m" >"$TMP/pane-colour2"
# tmux carries attributes across lines, so a dim set earlier holds until a reset.
printf '%s\n' "${ESC}[2mfooter" "❯${NBSP}push it" >"$TMP/pane-carry"

LANE_SEND_CONFIRM_SECS=1 TMUX_PANE_FILE="$TMP/pane-dim" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane 'continue this work'
assert_contains "$LAST_OUTPUT" 'the input line emptied' 'dim "push it" in the input line counts as empty for --send'
assert_not_contains "$LAST_OUTPUT" 'DID NOT TAKE' 'a dim suggestion is not reported as text that did not take'
LANE_SEND_CONFIRM_SECS=1 TMUX_PANE_FILE="$TMP/pane-typed" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane 'continue this work'
assert_contains "$LAST_OUTPUT" 'DID NOT TAKE' 'the same text in normal colour still counts as typed'
LANE_SEND_CONFIRM_SECS=1 TMUX_PANE_FILE="$TMP/pane-colour2" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane 'continue this work'
assert_contains "$LAST_OUTPUT" 'DID NOT TAKE' 'a colour with a 2 in its parameters is not read as dim'
LANE_SEND_CONFIRM_SECS=1 TMUX_PANE_FILE="$TMP/pane-carry" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane 'continue this work'
assert_contains "$LAST_OUTPUT" 'the input line emptied' 'dim carried over from an earlier line still applies'

# Delivered is the words in the lane's transcript: a user turn there holding the text sent makes --send exit 0.
SLUG="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$REPO")"; mkdir -p "$HOME_TEST/.claude/projects/$SLUG"
printf '%s\n' '{"type":"user","message":{"role":"user","content":"Jonathan says (from his OpenClaw chat): it does show the  porch columns"}}' >"$HOME_TEST/.claude/projects/$SLUG/live.jsonl"
LANE_SEND_CONFIRM_SECS=1 TMUX_PANE_FILE="$TMP/pane-dim" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane 'Jonathan says (from his OpenClaw chat): it does show the porch columns'
assert_exit 0 "$LAST_STATUS" '--send exits 0 once the words are in the transcript'
assert_contains "$LAST_OUTPUT" 'confirmed in its transcript' '--send says it found the words in the transcript'
rm -f "$HOME_TEST/.claude/projects/$SLUG/live.jsonl"

# The transcript decides: a screen the checks cannot read (text still looks typed, no spinner) does not turn a delivered message into a
# failure (28 Oct 2026: two recycles reported FAILED on the tower with the re-prime message already in the lane's transcript).
printf '%s\n' '{"type":"user","message":{"role":"user","content":"Hub: the screen is unreadable but this landed"}}' >"$HOME_TEST/.claude/projects/$SLUG/live.jsonl"
LANE_SEND_CONFIRM_SECS=1 TMUX_PANE_FILE="$TMP/pane-typed" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane 'Hub: the screen is unreadable but this landed'
assert_exit 0 "$LAST_STATUS" '--send exits 0 when the transcript holds the words even though the screen looks unchanged'
assert_contains "$LAST_OUTPUT" 'confirmed in its transcript (the screen check could not tell)' 'the wording says the screen could not tell'
rm -f "$HOME_TEST/.claude/projects/$SLUG/live.jsonl"

# A message typed while the lane is mid-turn is not a "user" turn in its transcript until the turn ends: Claude Code logs it at once as a
# queue-operation "enqueue", then an attachment "queued_command" (seen in real lane transcripts, 21 Oct). --send used to print
# NOT IN ITS TRANSCRIPT for every message that landed on a working lane.
MID='Hub: one correction to your prompt, write the report to the lane folder'
printf '%s\n' '{"type":"queue-operation","operation":"enqueue","content":"Hub:  one correction to your prompt, write the report to the lane folder"}' >"$HOME_TEST/.claude/projects/$SLUG/live.jsonl"
LANE_SEND_CONFIRM_SECS=1 TMUX_PANE_FILE="$TMP/pane-dim" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane "$MID"
assert_exit 0 "$LAST_STATUS" '--send exits 0 for a message queued on a working lane (enqueue record)'
assert_contains "$LAST_OUTPUT" 'confirmed in its transcript' 'a queued message counts as delivered'
printf '%s\n' '{"type":"attachment","attachment":{"type":"queued_command","prompt":"'"$MID"'"}}' >"$HOME_TEST/.claude/projects/$SLUG/live.jsonl"
LANE_SEND_CONFIRM_SECS=1 TMUX_PANE_FILE="$TMP/pane-dim" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane "$MID"
assert_exit 0 "$LAST_STATUS" '--send exits 0 once the queued message reaches the lane (queued_command record)'
# Only an enqueue holds the words as input: a dequeue record with the same content is not delivery.
printf '%s\n' '{"type":"queue-operation","operation":"dequeue","content":"'"$MID"'"}' >"$HOME_TEST/.claude/projects/$SLUG/live.jsonl"
LANE_SEND_CONFIRM_SECS=1 TMUX_PANE_FILE="$TMP/pane-dim" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane "$MID"
assert_exit 1 "$LAST_STATUS" '--send still fails when no record holds the words as input'
assert_contains "$LAST_OUTPUT" 'NOT IN ITS TRANSCRIPT' 'the real failure is still reported'
rm -f "$HOME_TEST/.claude/projects/$SLUG/live.jsonl"

# After /clear or a resume the lane writes a new transcript while the index row still names the old id: --send checks the recent ones too.
printf '%s\tstale-id-lane\t%s\tsonnet\tsubscription\t%s\t%s\told-sess\t-\thigh\toff\n' "$(date +%F)" "$REPO" "$PROMPT" "$TMP/stale-id.md" >>"$HOME_TEST/.claude/state/lanes/index.tsv"
printf '%s\n' '{"type":"user","message":{"role":"user","content":"You are a lane"}}' >"$HOME_TEST/.claude/projects/$SLUG/old-sess.jsonl"
printf '%s\n' '{"type":"user","message":{"role":"user","content":"Hub: after the clear, carry on"}}' >"$HOME_TEST/.claude/projects/$SLUG/new-sess.jsonl"
LANE_SEND_CONFIRM_SECS=1 TMUX_PANE_FILE="$TMP/pane-dim" TMUX_HAS_SESSION=1 TMUX_WINDOWS=stale-id-lane run_lane --send stale-id-lane 'Hub: after the clear, carry on'
assert_exit 0 "$LAST_STATUS" '--send finds the words in the lane new transcript when the index id is the old one'
rm -f "$HOME_TEST/.claude/projects/$SLUG/old-sess.jsonl" "$HOME_TEST/.claude/projects/$SLUG/new-sess.jsonl"

status_with_pane() {
  TMUX_PANE_FILE="$1" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane HOME="$HOME_TEST" PATH="$BIN:$PATH" \
    TMUX_CALLS="$TMP/status-calls" TMUX_SENT="$TMP/status-sent" bash "$SCRIPT" --status 2>&1
}
STATUS_DIM="$(status_with_pane "$TMP/pane-dim")"
assert_contains "$STATUS_DIM" 'suggestion: "push it"' '--status shows the dim suggestion on the idle lane row'
assert_contains "$STATUS_DIM" 'something the lane said' '--status still shows the lane last line'
assert_not_contains "$(status_with_pane "$TMP/pane-typed")" 'suggestion:' '--status does not call normal-colour text a suggestion'
assert_not_contains "$(status_with_pane "$TMP/pane-bare")" 'suggestion:' '--status shows no suggestion for an empty prompt'

# A hand-off newer than the report (or with no report) shows its Done and Next first lines under the lane's row; an older one does not.
HD="$TMP/hand-lane-dir"; mkdir -p "$HD"
printf '%s\thand-lane\t%s\tsonnet\tsubscription\t%s\t%s\t-\t-\thigh\toff\n' "$(date +%F)" "$REPO" "$PROMPT" "$HD/report.md" >>"$HOME_TEST/.claude/state/lanes/index.tsv"
hand_status() { TMUX_PANE_FILE="$TMP/pane-bare" TMUX_HAS_SESSION=1 TMUX_WINDOWS=hand-lane HOME="$HOME_TEST" PATH="$BIN:$PATH" TMUX_CALLS="$TMP/status-calls" TMUX_SENT="$TMP/status-sent" bash "$SCRIPT" --status 2>&1; }
printf '# handoff\n## Done\nmerged the widget fix\n## Next\nrun the suite then open the PR\n' >"$HD/handoff.md"
assert_contains "$(hand_status)" 'hand-off (newer than report): Done: merged the widget fix | Next: run the suite then open the PR' '--status shows Done and Next when the lane has a hand-off and no report'
echo "report text" >"$HD/report.md"; touch -d '-1 hour' "$HD/handoff.md"
assert_not_contains "$(hand_status)" 'hand-off (newer' '--status hides an older hand-off behind the newer report'
touch "$HD/handoff.md"
assert_contains "$(hand_status)" 'hand-off (newer than report): Done: merged the widget fix' '--status shows a hand-off written after the report'

# 19 Oct: "report:yes idle" got tigard-trace closed while it was mid-work. A report is the last word only when it is newer than
# every input the lane got and no background job is running; otherwise --status says report:stale.
# Times relative to now: lane_report_state.py calls a job lost after LOST_JOB_HOURS (12), so fixed 19 Oct stamps went red on 20 Oct.
T_REP="$(date -u -d '-2 hours' '+%F %T UTC')"
T_OLD="$(date -u -d '-4 hours' +%FT%T.000Z)"
T_NEW="$(date -u -d '-100 minutes' +%FT%T.000Z)"
T_BG="$(date -u -d '-75 minutes' +%FT%T.000Z)"
stale_row() {  # <name> <transcript-json-lines>...: a lane whose report.md is two hours old, with this transcript
  local n="$1"; shift
  local dir="$TMP/stale-$n" slug rep
  mkdir -p "$dir"; slug="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$dir")"
  mkdir -p "$HOME_TEST/.claude/projects/$slug"; printf '%s\n' "$@" >"$HOME_TEST/.claude/projects/$slug/sess-$n.jsonl"
  rep="$TMP/stale-$n.md"; echo done >"$rep"; touch -d "$T_REP" "$rep"
  printf '%s\t%s\t%s\tsonnet\tsubscription\t%s\t%s\tsess-%s\t-\thigh\toff\n' "$(date +%F)" "$n" "$dir" "$PROMPT" "$rep" "$n" >>"$INDEX"
}
U_OLD='{"type":"user","timestamp":"'"$T_OLD"'","message":{"role":"user","content":"You are a lane"}}'
U_NEW='{"type":"user","timestamp":"'"$T_NEW"'","message":{"role":"user","content":"Jonathan says the blue lines do not match"}}'
BG='{"type":"assistant","timestamp":"'"$T_BG"'","message":{"role":"assistant","content":[{"type":"tool_use","id":"tb1","name":"Bash","input":{"command":"run tests","run_in_background":true}}]}}'
stale_row calm "$U_OLD"
stale_row told "$U_OLD" "$U_NEW"
stale_row busy "$U_OLD" "$BG"
STATUS_STALE="$(TMUX_PANE_FILE="$TMP/pane-bare" TMUX_HAS_SESSION=1 TMUX_WINDOWS='calm told busy' HOME="$HOME_TEST" PATH="$BIN:$PATH" \
  TMUX_CALLS="$TMP/status-calls" TMUX_SENT="$TMP/status-sent" bash "$SCRIPT" --status 2>&1)"
assert_contains "$STATUS_STALE" 'calm' 'status lists the calm lane'
case "$(grep '^calm ' <<<"$STATUS_STALE")" in *report:yes*) checks=$((checks + 1)) ;; *) fail 'a report newer than all input, with no job, is report:yes' ;; esac
case "$(grep '^told ' <<<"$STATUS_STALE")" in *report:stale*) checks=$((checks + 1)) ;; *) fail 'input after the report makes it report:stale' ;; esac
case "$(grep '^busy ' <<<"$STATUS_STALE")" in *report:stale*) checks=$((checks + 1)) ;; *) fail 'a background job still running makes it report:stale' ;; esac
assert_contains "$STATUS_STALE" 'report stale: the lane got input after report.md was written' 'status says why: input'
assert_contains "$STATUS_STALE" 'report stale: a background job it started has not finished' 'status says why: job'

# 3D work tier: a prompt naming data/sites/ or site-model runs on fable or opus only (his rule, 19 Oct).
SITE_PROMPT="$TMP/site-prompt.md"
printf '%s\n' 'Fit the walls in data/sites/tigard-01 against the overlay gate.' >"$SITE_PROMPT"
SKILL_PROMPT="$TMP/skill-prompt.md"
printf '%s\n' 'Read the site-model skill, then rebuild the porch.' >"$SKILL_PROMPT"
for low in sonnet haiku claude-sonnet-5-5 default none; do
  run_lane --dry-run "tier-$low" "$REPO" "$low" "$SITE_PROMPT"
  assert_exit 1 "$LAST_STATUS" "$low is refused on a data/sites/ prompt"
  assert_contains "$LAST_OUTPUT" "refusing model $low" "the refusal names the model ($low)"
  assert_contains "$LAST_OUTPUT" 'fable or opus' "the refusal names the two allowed models ($low)"
  assert_contains "$LAST_OUTPUT" 'lane-rules.md' "the refusal names the rule ($low)"
  assert_not_contains "$LAST_OUTPUT" 'mkdir -p' "a refused lane makes no hand-off folder ($low)"
done
run_lane --dry-run tier-skill "$REPO" sonnet "$SKILL_PROMPT"
assert_exit 1 "$LAST_STATUS" 'sonnet is refused on a prompt naming the site-model skill'
run_lane --dry-run tier-empty "$REPO" '' "$SITE_PROMPT"
assert_exit 1 "$LAST_STATUS" 'an empty model is refused on a site prompt'
assert_contains "$LAST_OUTPUT" 'refusing model (none)' 'the refusal says no model was named'
for ok in opus fable claude-opus-5-5 claude-fable-5-1 Opus; do
  run_lane --dry-run "tier-$ok" "$REPO" "$ok" "$SITE_PROMPT"
  assert_exit 0 "$LAST_STATUS" "$ok is allowed on a site prompt"
  assert_contains "$LAST_OUTPUT" "claude\\ --model\\ $ok" "$ok lane gets its model"
  assert_contains "$LAST_OUTPUT" 'effort\ high' "$ok lane runs at effort high"
done
run_lane --dry-run --allow-low-tier tier-override "$REPO" sonnet "$SITE_PROMPT"
assert_exit 0 "$LAST_STATUS" '--allow-low-tier lets sonnet start on a site prompt'
assert_contains "$LAST_OUTPUT" 'claude\ --model\ sonnet' '--allow-low-tier keeps the model asked for'
run_lane --dry-run tier-plain "$REPO" haiku "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'a prompt without data/sites/ or site-model is unaffected (haiku)'
run_lane --dry-run --console tier-console "$REPO" sonnet "$SITE_PROMPT"
assert_exit 1 "$LAST_STATUS" '--console is held to the tier too'
run_lane --dry-run --console --headless tier-headless "$REPO" haiku "$SITE_PROMPT"
assert_exit 1 "$LAST_STATUS" '--headless is held to the tier too'
run_lane --dry-run --engine codex tier-codex "$REPO" sonnet "$SITE_PROMPT"
assert_not_contains "$LAST_OUTPUT" 'refusing model' 'the tier applies to Claude lanes only'
assert_contains "$HELP_OUTPUT" '--allow-low-tier' 'help documents the tier override'
# --resume checks the recorded model against the recorded prompt.
printf '%s\ttier-res-low\t%s\tsonnet\tsubscription\t%s\t%s\tsess-tier-1\t-\thigh\toff\n' "$(date +%F)" "$REPO" "$SITE_PROMPT" "$TMP/tier-r1.md" >>"$INDEX"
printf '%s\ttier-res-fable\t%s\tfable\tsubscription\t%s\t%s\tsess-tier-2\t-\thigh\toff\n' "$(date +%F)" "$REPO" "$SITE_PROMPT" "$TMP/tier-r2.md" >>"$INDEX"
printf '%s\ttier-res-plain\t%s\tsonnet\tsubscription\t%s\t%s\tsess-tier-3\t-\thigh\toff\n' "$(date +%F)" "$REPO" "$PROMPT" "$TMP/tier-r3.md" >>"$INDEX"
run_lane --dry-run --resume tier-res-low
assert_exit 1 "$LAST_STATUS" 'resuming a sonnet site lane is refused'
assert_contains "$LAST_OUTPUT" 'refusing model sonnet' 'the resume refusal names the model'
run_lane --dry-run --allow-low-tier --resume tier-res-low
assert_exit 0 "$LAST_STATUS" '--allow-low-tier lets a sonnet site lane resume'
run_lane --dry-run --resume tier-res-fable
assert_exit 0 "$LAST_STATUS" 'a fable site lane resumes'
run_lane --dry-run --resume tier-res-plain
assert_exit 0 "$LAST_STATUS" 'a sonnet lane without site words resumes'

printf 'ok - %s checks passed\n' "$checks"
