#!/usr/bin/env bash
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-engines.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

BIN="$TMP/bin"
HOME_TEST="$TMP/home"
REPO="$TMP/project repo"
PROMPT="$TMP/prompt.md"
mkdir -p "$BIN" "$HOME_TEST/repos/hub" "$HOME_TEST/repos/sample-client-repo" \
  "$HOME_TEST/repos/listed-only-repo/nested" "$HOME_TEST/repos/commented-client-repo" \
  "$HOME_TEST/repos/CD/nested" "$REPO" "$TMP/repo_dir"
printf 'id\trepo_dir\n# ignored\t~/repos/commented-client-repo\nblank\t\nexample\t~/repos/sample-client-repo\n' >"$HOME_TEST/repos/hub/client-senders.tsv"
printf '# Client repositories\n~/repos/listed-only-repo\n\n# ~/repos/commented-client-repo\n' >"$HOME_TEST/repos/hub/client-repos.txt"
printf '%s\n' 'Review this safely.' '$(touch injected); "quoted"; `backticks`' >"$PROMPT"

cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
printf '%s\0' "$@" >>"$TMUX_CALLS"
case "$1" in
  has-session) [ "${TMUX_HAS_SESSION:-0}" = 1 ] ;;
  list-windows) for window in ${TMUX_WINDOWS:-}; do printf '%s\n' "$window"; done ;;
  capture-pane) printf 'ready\n' ;;
  send-keys)
    if [ "${2:-}" = -t ] && [ -n "${4:-}" ]; then
      printf '%s\n' "$4" >"$TMUX_SENT"
      if [ "${5:-}" = Enter ] && [ "${RUN_LANE_COMMANDS:-0}" = 1 ]; then
        case "$4" in
          'codex exec '*|'cd '* ) (cd "$TEST_CWD" && bash -c "$4") ;;
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
chmod +x "$BIN/tmux" "$BIN/sleep" "$BIN/codex" "$BIN/copilot"

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

run_lane() {
  local sent="$TMP/tmux-sent" output="$TMP/run.out" status
  rm -f "$sent"
  set +e
  (cd "$TMP" && HOME="$HOME_TEST" PATH="$BIN:$PATH" TMUX_CALLS="$TMP/tmux-calls" TMUX_SENT="$sent" \
    TMUX_HAS_SESSION="${TMUX_HAS_SESSION:-0}" TMUX_WINDOWS="${TMUX_WINDOWS:-}" \
    CODEX_CALLS="$TMP/codex-calls" COPILOT_CALLS="$TMP/copilot-calls" \
    TEST_CWD="$REPO" RUN_LANE_COMMANDS=1 \
    bash "$SCRIPT" "$@") >"$output" 2>&1
  status=$?
  set -e
  LAST_STATUS="$status"
  LAST_OUTPUT="$(cat "$output")"
  LAST_COMMAND="$(cat "$sent" 2>/dev/null || true)"
}

# Default engine remains Claude and prompt contents stay in the prompt file.
HELP_OUTPUT="$(HOME="$HOME_TEST" bash "$SCRIPT" --help)"
assert_contains "$HELP_OUTPUT" '--engine claude|codex|copilot' 'help documents available engines'
assert_contains "$HELP_OUTPUT" '--allow-client' 'help documents the client override'

run_lane default-lane "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'default Claude lane starts'
assert_contains "$LAST_COMMAND" 'claude --model sonnet' 'default command uses Claude'
assert_contains "$LAST_COMMAND" '$(cat ' 'default command reads the prompt file'
assert_not_contains "$LAST_COMMAND" 'touch injected' 'default prompt is not interpolated into the shell command'

# Codex gets the required workspace flags and omits Claude model names.
run_lane --engine codex codex-lane "$REPO" opus "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'Codex lane starts'
assert_contains "$LAST_COMMAND" 'codex exec -C ' 'Codex uses exec and the working directory'
assert_contains "$LAST_COMMAND" '-s workspace-write --add-dir ' 'Codex gets workspace-write and lane access'
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

# Claude lanes retain the existing interactive --send behavior.
TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane 'continue this work'
assert_exit 1 "$LAST_STATUS" 'Claude send remains on the existing submission check'
assert_contains "$LAST_OUTPUT" 'input line is empty but no spinner seen' 'Claude --send still reaches the pane submission check'

# The second TSV field expands ~/ and protects client repositories; comments, blanks,
# and the header row are not repository paths.
run_lane --engine codex blocked-client "$HOME_TEST/repos/sample-client-repo" sonnet "$PROMPT"
assert_exit 1 "$LAST_STATUS" 'protected repository is refused'
assert_contains "$LAST_OUTPUT" 'protected client repository' 'refusal explains the client-repository guard'

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
for lane_engine in codex copilot; do
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

printf 'ok - %s checks passed\n' "$checks"
