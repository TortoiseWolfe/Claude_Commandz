#!/usr/bin/env bash
# Issue 332: no engine's lane may read a settings file, and no real secret value may reach an outside provider.
#
# For each engine (codex, copilot, agy, opencode, local) this starts a lane with lane.sh and a stand-in engine program. The stand-in does what a
# misbehaving model would: it tries to read every fixture settings file, tries to unmount what hides them, then prints a secret value it "learned".
# The test fails if the stand-in ever sees a fixture value. It has no skip path: if this machine cannot make the user namespace the guard needs, the test fails.
# Every secret here is a made-up fixture; nothing on this machine is read.
#
# To watch it go red, run it against a lane.sh from before issue 332 (the red proof for the pull request):
#   git archive origin/main global/.claude/scripts | tar -x -C "$dir"; cp this_file "$dir"/global/.claude/scripts/tests/; bash that copy
set -euo pipefail
export AFA_ENGINES_AVAILABLE="*"
export AFA_BIG_OUT=0
unset AFA_LANE AFA_LANE_NAME LANE_STATUS_INNER LANE_SECRET_ENV LANE_HIDE_EXTRA_ROOTS LANE_HIDE_REPOS LANE_SCAN_REPOS

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-secret-guard.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
assert_contains() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2)" ;; esac; }
assert_not_contains() { case "$1" in *"$2"*) fail "$3 (unexpected: $2)" ;; *) ok ;; esac; }

# The guard needs unprivileged user namespaces. A machine without them must fail here, never skip: a skipped guard test is no proof.
unshare -Urm true 2>/dev/null || fail 'this machine cannot make a user and mount namespace (unshare -Urm); on Ubuntu 24.04 run: sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0'
command -v setpriv >/dev/null || fail 'setpriv is not installed'

BIN="$TMP/bin"
HOME_TEST="$TMP/home"
REPO="$TMP/project repo"
PROBE="$TMP/probe"
PROMPT="$TMP/prompt.md"
mkdir -p "$BIN" "$PROBE" "$REPO/nested" "$HOME_TEST/repos/hub" "$HOME_TEST/repos/other-project" "$HOME_TEST/repos/hub/sealed" \
  "$HOME_TEST/.config/afa/secrets" "$HOME_TEST/.config/afa/rotated-test-users" "$HOME_TEST/.config/gemini" "$HOME_TEST/.config/openrouter" "$HOME_TEST/.ssh"
printf 'placeholder\n' >"$HOME_TEST/.config/gemini/api-key"; printf 'placeholder\n' >"$HOME_TEST/.config/openrouter/api-key"
printf '# Client repositories\n' >"$HOME_TEST/repos/hub/client-repos.txt"
printf 'id\trepo_dir\n' >"$HOME_TEST/repos/hub/client-senders.tsv"
printf 'Review this safely.\n' >"$PROMPT"

# Fixture secrets: made up, long and mixed enough that known_secrets_scan.py treats them as secret values.
# Built from a hash at run time so no secret-shaped literal sits in this public file (gitleaks flags one); each is 32 hex characters.
fixture_value() { printf 'lane-guard-fixture-%s' "$1" | sha256sum | cut -c1-32; }
V_ENV="$(fixture_value env)"
V_ENV2="$(fixture_value env2)"
V_SEALED="$(fixture_value sealed)"
V_SECRETS="$(fixture_value secrets)"
V_ROTATED="$(fixture_value rotated)"
V_PEM="$(fixture_value pem)"
V_GRANT="$(fixture_value grant)"
printf 'API_TOKEN=%s\n' "$V_ENV" >"$REPO/.env"
printf 'API_TOKEN=%s\n' "$V_ENV2" >"$REPO/.env.local"
printf 'API_TOKEN=%s\n' "$V_ENV2" >"$REPO/nested/.env.production"
printf 'API_TOKEN=%s\n' "$V_ENV" >"$HOME_TEST/repos/other-project/.env"
printf 'DEPLOY_TOKEN=%s\n' "$V_SEALED" >"$HOME_TEST/repos/hub/sealed/deploy.key"
printf '%s\n' "$V_SECRETS" >"$HOME_TEST/.config/afa/secrets/service_token"
printf '%s\n' "$V_GRANT" >"$HOME_TEST/.config/afa/secrets/FAKE_TEST_TOKEN"
printf '{"password":"%s"}\n' "$V_ROTATED" >"$HOME_TEST/.config/afa/rotated-test-users/user1.json"
printf '%s\n' "$V_PEM" >"$REPO/deploy.pem"
# A placeholder env file is not a settings file and must stay readable (the guard is not a blanket).
printf 'API_TOKEN=change-me\n' >"$REPO/.env.example"

HIDDEN=("$REPO/.env" "$REPO/.env.local" "$REPO/nested/.env.production" "$REPO/deploy.pem" "$HOME_TEST/repos/other-project/.env"
  "$HOME_TEST/repos/hub/sealed/deploy.key" "$HOME_TEST/.config/afa/secrets/service_token" "$HOME_TEST/.config/afa/secrets/FAKE_TEST_TOKEN"
  "$HOME_TEST/.config/afa/rotated-test-users/user1.json")
printf '%s\n' "${HIDDEN[@]}" >"$TMP/hidden.list"

# The stand-in engine: a model that goes looking. It writes what it saw to $PROBE/<tag> and never prints a fixture to its own output except the one
# value it "learned" (LEARNED_VALUE), which the log scrub must remove.
cat >"$BIN/probe.sh" <<'STUB'
#!/usr/bin/env bash
tag="${PROBE_TAG:?}"
out="$PROBE/$tag"
: >"$out"
while IFS= read -r p; do
  if [ -e "$p" ]; then printf 'FILE %s <%s>\n' "$p" "$(cat -- "$p" 2>/dev/null | tr '\n' ' ')" >>"$out"; else printf 'MISSING %s\n' "$p" >>"$out"; fi
done <"$HIDDEN_LIST"
# Unmounting what hides a file must fail, and the file must still read empty afterwards.
for p in "$FIRST_HIDDEN" "$SECOND_HIDDEN"; do
  umount "$p" >/dev/null 2>&1 && echo "UMOUNT-WORKED $p" >>"$out"
  umount -l "$p" >/dev/null 2>&1 && echo "UMOUNT-LAZY-WORKED $p" >>"$out"
  unshare -m umount "$p" >/dev/null 2>&1 && echo "UNSHARE-UMOUNT-WORKED $p" >>"$out"
  printf 'AFTER %s <%s>\n' "$p" "$(cat -- "$p" 2>/dev/null | tr '\n' ' ')" >>"$out"
done
# The placeholder file must stay readable.
printf 'EXAMPLE <%s>\n' "$(cat -- "$EXAMPLE_FILE" 2>/dev/null | tr '\n' ' ')" >>"$out"
# A secret granted by name arrives as an environment variable.
printf 'ENVVAR <%s>\n' "${FAKE_TEST_TOKEN:-}" >>"$out"
printf 'ENV-FILE-LISTING <%s>\n' "$(ls -A "$HOME/.config/afa/secrets" 2>/dev/null | tr '\n' ' ')" >>"$out"
printf 'learned: %s\n' "$LEARNED_VALUE"
STUB
chmod +x "$BIN/probe.sh"
for engine_program in codex copilot agy opencode; do
  printf '#!/usr/bin/%s\nexec "%s/probe.sh" "$@"\n' 'env bash' "$BIN" >"$BIN/$engine_program"
  chmod +x "$BIN/$engine_program"
done
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  has-session) exit 1 ;;
  list-windows) exit 0 ;;
  display) [ "${5:-}" = '#{pane_current_command}' ] && echo claude; exit 0 ;;
  capture-pane) echo ready; exit 0 ;;
  send-keys)
    if [ "${2:-}" = -t ] && [ -n "${4:-}" ] && [ "${5:-}" = Enter ]; then
      set +e
      (cd "$TEST_CWD" && bash -c "$4") >>"$TMUX_PANE_LOG" 2>&1
      echo $? >"$TMUX_RC"
    fi ;;
esac
exit 0
STUB
cat >"$BIN/sleep" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
cat >"$BIN/curl" <<'STUB'
#!/usr/bin/env bash
printf '{"models":[{"name":"qwen3-coder:30b"}]}'
STUB
chmod +x "$BIN/tmux" "$BIN/sleep" "$BIN/curl"

# Control: with no guard, the stand-in does see the fixtures. Without this the test could pass because the stand-in is broken.
run_probe_direct() {
  HOME="$HOME_TEST" PATH="$BIN:$PATH" PROBE="$PROBE" PROBE_TAG=control HIDDEN_LIST="$TMP/hidden.list" FIRST_HIDDEN="${HIDDEN[0]}" SECOND_HIDDEN="${HIDDEN[5]}" \
    EXAMPLE_FILE="$REPO/.env.example" LEARNED_VALUE=nothing "$BIN/probe.sh" >/dev/null
}
run_probe_direct
control="$(cat "$PROBE/control")"
for v in "$V_ENV" "$V_ENV2" "$V_SEALED" "$V_SECRETS" "$V_ROTATED" "$V_PEM"; do assert_contains "$control" "$v" 'control: the stand-in sees every fixture when nothing hides them'; done

run_lane() {  # run_lane <tag> <prompt file> [lane.sh args...]; sets LAST_RC (the command's exit) and LAST_PANE
  local tag="$1" prompt="$2"; shift 2
  rm -f "$TMP/rc" "$TMP/pane.log" "$PROBE/$tag"
  local rc
  (cd "$TMP" && HOME="$HOME_TEST" PATH="$BIN:$PATH" TEST_CWD="$REPO" TMUX_RC="$TMP/rc" TMUX_PANE_LOG="$TMP/pane.log" OPENCODE_LANE_POLL=1 \
    PROBE="$PROBE" PROBE_TAG="$tag" HIDDEN_LIST="$TMP/hidden.list" FIRST_HIDDEN="${HIDDEN[0]}" SECOND_HIDDEN="${HIDDEN[5]}" \
    EXAMPLE_FILE="$REPO/.env.example" LEARNED_VALUE="$V_SECRETS" \
    bash "$SCRIPT" --claude-because "secret guard test fixture" "$@" "$prompt") >"$TMP/lane.out" 2>&1 || fail "lane.sh itself failed for $tag: $(cat "$TMP/lane.out")"
  LAST_RC="$(cat "$TMP/rc" 2>/dev/null || echo none)"
  LAST_PANE="$(cat "$TMP/pane.log" 2>/dev/null || true)"
}

check_engine() {  # check_engine <engine> [extra lane.sh flags]
  local engine="$1" tag="guard-$1" seen log
  shift
  run_lane "$tag" "$PROMPT" --engine "$engine" "$@" "$tag" "$REPO" sonnet
  [ -f "$PROBE/$tag" ] || fail "$engine: the stand-in engine never ran (exit $LAST_RC): $LAST_PANE"
  seen="$(cat "$PROBE/$tag")"
  for v in "$V_ENV" "$V_ENV2" "$V_SEALED" "$V_SECRETS" "$V_ROTATED" "$V_PEM" "$V_GRANT"; do
    case "$seen" in
      *"ENVVAR <$v>"*) ;;  # the one granted value is meant to arrive in the environment
      *"$v"*) fail "$engine: a lane could read a settings file; it saw a fixture value" ;;
    esac
  done
  ok
  assert_not_contains "$seen" 'UMOUNT-WORKED' "$engine: the lane cannot unmount what hides a settings file"
  assert_not_contains "$seen" 'UMOUNT-LAZY-WORKED' "$engine: the lane cannot lazily unmount what hides a settings file"
  assert_not_contains "$seen" 'UNSHARE-UMOUNT-WORKED' "$engine: the lane cannot unmount from a new namespace"
  assert_contains "$seen" 'EXAMPLE <API_TOKEN=change-me >' "$engine: a placeholder env file stays readable"
  assert_contains "$seen" "FILE $REPO/.env <>" "$engine: the checkout's .env reads empty"
  assert_contains "$seen" "FILE $REPO/nested/.env.production <>" "$engine: a nested .env.production reads empty"
  assert_contains "$seen" "FILE $HOME_TEST/repos/other-project/.env <>" "$engine: another project's .env reads empty"
  assert_contains "$seen" "MISSING $HOME_TEST/repos/hub/sealed/deploy.key" "$engine: the sealed-key folder shows no key"
  assert_contains "$seen" "MISSING $HOME_TEST/.config/afa/secrets/service_token" "$engine: ~/.config/afa/secrets reads empty"
  assert_contains "$seen" "MISSING $HOME_TEST/.config/afa/rotated-test-users/user1.json" "$engine: rotated test users read empty"
  assert_contains "$seen" "ENV-FILE-LISTING <>" "$engine: the secrets folder lists nothing"
  # The scrub: the stand-in printed a real fixture value; the lane's log must not keep it.
  log="$(cat "$HOME_TEST/.claude/state/lanes/$(date +%F)-$tag/out.log" 2>/dev/null || true)"
  assert_contains "$log" 'learned:' "$engine: the stand-in's output reached out.log"
  assert_not_contains "$log" "$V_SECRETS" "$engine: a secret value the engine printed is scrubbed from out.log"
}

for engine in codex copilot agy opencode local; do check_engine "$engine"; done

# A secret a test needs: granted by name, it reaches the lane as an environment variable and not as a file.
LANE_SECRET_ENV=FAKE_TEST_TOKEN run_lane grant "$PROMPT" --engine codex grant "$REPO" sonnet
seen="$(cat "$PROBE/grant" 2>/dev/null || fail 'granted-secret lane never ran')"
assert_contains "$seen" "ENVVAR <$V_GRANT>" 'a granted secret arrives as an environment variable'
assert_contains "$seen" "MISSING $HOME_TEST/.config/afa/secrets/FAKE_TEST_TOKEN" 'the granted secret has no readable file'

# A prompt that holds a real secret value is refused before any engine starts.
printf 'Use this password: %s\n' "$V_ENV" >"$TMP/leaky-prompt.md"
run_lane refused "$TMP/leaky-prompt.md" --engine codex refused "$REPO" sonnet
[ ! -e "$PROBE/refused" ] || fail 'a prompt holding a secret value still started the engine'
ok
assert_contains "$LAST_RC" '98' 'a prompt holding a secret value exits 98'
assert_not_contains "$LAST_PANE" "$V_ENV" 'the refusal never prints the value'

# The hand-off folder is scanned too.
printf 'Review this safely.\n' >"$TMP/clean-prompt-leaky-handoff.md"
HAND="$HOME_TEST/.claude/state/lanes/$(date +%F)-handoff-scan"
mkdir -p "$HAND"; printf 'note: %s\n' "$V_ENV" >"$HAND/handoff.md"
run_lane handoff-scan "$TMP/clean-prompt-leaky-handoff.md" --engine copilot handoff-scan "$REPO" sonnet
[ ! -e "$PROBE/handoff-scan" ] || fail "a hand-off folder holding a secret value still started the engine (exit $LAST_RC): $LAST_PANE $(cat "$TMP/lane.out") $(ls "$HOME_TEST/.claude/state/lanes")"
ok
assert_contains "$LAST_RC" '98' 'a hand-off folder holding a secret value exits 98'

# The Claude engine is outside this guard (its own secret-guard hook covers it): lane_hide_secrets.sh is not in its command.
HOME="$HOME_TEST" PATH="$BIN:$PATH" bash "$SCRIPT" --dry-run --claude-because "secret guard test fixture" claude-lane "$REPO" sonnet "$PROMPT" >"$TMP/claude-dry.out" 2>&1 || true
assert_not_contains "$(cat "$TMP/claude-dry.out")" 'lane_hide_secrets.sh' 'a Claude lane is not wrapped'

echo "All $checks checks passed"
