#!/usr/bin/env bash
set -euo pipefail

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
    TMUX_HAS_SESSION="${TMUX_HAS_SESSION:-0}" TMUX_WINDOWS="${TMUX_WINDOWS:-}" TMUX_PANE_FILE="${TMUX_PANE_FILE:-}" \
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

# Lean Claude lanes: effort high on every model, connectors/plugins/Artifact off, workspace CLAUDE.md swapped for the slim rules.
PROMPT_MAIL="$TMP/prompt-mail.md"
printf 'Check the Gmail drafts for replies.\n' >"$PROMPT_MAIL"
run_lane lean-opus "$REPO" opus "$PROMPT"
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
assert_contains "$LAST_OUTPUT" 'connectors off' 'start line reports connectors off'
run_lane lean-haiku "$REPO" haiku "$PROMPT"
assert_not_contains "$LAST_COMMAND" '--effort' 'haiku takes no effort'
run_lane --effort max lean-max "$REPO" opus "$PROMPT"
assert_contains "$LAST_COMMAND" '--effort max' '--effort still overrides'

printf '# Lane rules\n' >"$HOME_TEST/repos/hub/lane-rules.md"
run_lane lean-rules "$REPO" sonnet "$PROMPT"
assert_contains "$LAST_COMMAND" 'claudeMdExcludes' 'workspace CLAUDE.md is excluded once the rule file exists'
assert_contains "$LAST_COMMAND" "$HOME_TEST/repos/CLAUDE.md" 'the exclude names the workspace CLAUDE.md'
assert_contains "$LAST_COMMAND" "--append-system-prompt-file $HOME_TEST/repos/hub/lane-rules.md" 'the slim rules are appended'

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

# --status shows the connectors value, and still reads ten-column rows.
STATUS_CONN="$(TMUX_HAS_SESSION=1 TMUX_WINDOWS='lean-opus lean-mail old-row' HOME="$HOME_TEST" PATH="$BIN:$PATH" \
  TMUX_CALLS="$TMP/status-calls" TMUX_SENT="$TMP/status-sent" bash "$SCRIPT" --status 2>&1)"
assert_contains "$STATUS_CONN" 'connectors:off' 'status shows an off lane'
assert_contains "$STATUS_CONN" 'connectors:on' 'status shows an on lane and a ten-column row'
assert_contains "$STATUS_CONN" 'old-row' 'status lists the ten-column row'

# A dispatcher lane (name oc-*, or AFA_LANE_UNATTENDED=1) tells its Claude process nobody is at the window; a hub lane does not.
run_lane oc-own-1016-0000 "$REPO" sonnet "$PROMPT"
assert_exit 0 "$LAST_STATUS" 'dispatcher-named lane starts'
assert_contains "$LAST_COMMAND" 'AFA_LANE_UNATTENDED=1 AFA_LANE_HAND=' 'oc- lane gets the unattended flag'
assert_contains "$LAST_COMMAND" "$(date +%F)-oc-own-1016-0000" 'oc- lane is told its own folder'
assert_contains "$LAST_COMMAND" 'claude --model sonnet' 'oc- lane still runs Claude'
AFA_LANE_UNATTENDED=1 run_lane env-flagged-lane "$REPO" sonnet "$PROMPT"
assert_contains "$LAST_COMMAND" 'AFA_LANE_UNATTENDED=1 AFA_LANE_HAND=' 'the env flag alone also marks a lane unattended'
run_lane hub-lane "$REPO" sonnet "$PROMPT"
assert_not_contains "$LAST_COMMAND" 'AFA_LANE_UNATTENDED' 'a hub lane is not marked unattended'
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

TMUX_PANE_FILE="$TMP/pane-dim" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane 'continue this work'
assert_contains "$LAST_OUTPUT" 'input line is empty but no spinner seen' 'dim "push it" in the input line counts as empty for --send'
assert_not_contains "$LAST_OUTPUT" 'DID NOT TAKE' 'a dim suggestion is not reported as text that did not take'
TMUX_PANE_FILE="$TMP/pane-typed" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane 'continue this work'
assert_contains "$LAST_OUTPUT" 'DID NOT TAKE' 'the same text in normal colour still counts as typed'
TMUX_PANE_FILE="$TMP/pane-colour2" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane 'continue this work'
assert_contains "$LAST_OUTPUT" 'DID NOT TAKE' 'a colour with a 2 in its parameters is not read as dim'
TMUX_PANE_FILE="$TMP/pane-carry" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane run_lane --send default-lane 'continue this work'
assert_contains "$LAST_OUTPUT" 'input line is empty' 'dim carried over from an earlier line still applies'

status_with_pane() {
  TMUX_PANE_FILE="$1" TMUX_HAS_SESSION=1 TMUX_WINDOWS=default-lane HOME="$HOME_TEST" PATH="$BIN:$PATH" \
    TMUX_CALLS="$TMP/status-calls" TMUX_SENT="$TMP/status-sent" bash "$SCRIPT" --status 2>&1
}
STATUS_DIM="$(status_with_pane "$TMP/pane-dim")"
assert_contains "$STATUS_DIM" 'suggestion: "push it"' '--status shows the dim suggestion on the idle lane row'
assert_contains "$STATUS_DIM" 'something the lane said' '--status still shows the lane last line'
assert_not_contains "$(status_with_pane "$TMP/pane-typed")" 'suggestion:' '--status does not call normal-colour text a suggestion'
assert_not_contains "$(status_with_pane "$TMP/pane-bare")" 'suggestion:' '--status shows no suggestion for an empty prompt'

printf 'ok - %s checks passed\n' "$checks"
