#!/usr/bin/env bash
# OpenCode saves the full output of every tool call under ~/.local/share/opencode/tool-output/. A password a command printed stays there, readable
# by the next lane, until something scrubs it (7 Nov 2026 queue item 2). lane_hide_secrets.sh now rewrites the files written during a run.
#
# This runs lane_hide_secrets.sh with a stand-in command that does what an engine does: it saves a tool-output file holding a real-looking secret value.
# The test fails if that value is still in the folder afterwards. It has no skip path: a machine without user namespaces fails here.
# Every secret is a made-up fixture built at run time; nothing on this machine is read.
#
# To watch it go red, run it beside a lane_hide_secrets.sh from before this change (it fails with "a secret value is still in the tool-output file"):
#   d=$(mktemp -d); mkdir $d/tests; cp this_file $d/tests/
#   for f in lane_hide_secrets.sh lane_secret_paths.py known_secrets_scan.py scrub_known_secrets.py; do git show <commit before this change>:global/.claude/scripts/$f >$d/$f; done
#   chmod +x $d/lane_hide_secrets.sh; bash $d/tests/test_lane_hide_tool_output.sh
set -euo pipefail
unset LANE_SECRET_ENV LANE_HIDE_EXTRA_ROOTS LANE_HIDE_REPOS LANE_SCAN_REPOS LANE_TOOL_OUTPUT_DIR

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane_hide_secrets.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-hide-tool-output.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }

unshare -Urm true 2>/dev/null || fail 'this machine cannot make a user and mount namespace (unshare -Urm); on Ubuntu 24.04 run: sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0'
command -v setpriv >/dev/null || fail 'setpriv is not installed'

HOME_TEST="$TMP/home"
REPO="$TMP/project"
TOOL_OUT="$TMP/tool-output"
HAND="$TMP/hand"
PROMPT="$TMP/prompt.md"
mkdir -p "$HOME_TEST/repos/app" "$HOME_TEST/.config/afa/secrets" "$REPO" "$TOOL_OUT/nested" "$HAND"
printf 'Review this safely.\n' >"$PROMPT"

fixture_value() { printf 'tool-output-fixture-%s' "$1" | sha256sum | cut -c1-32; }
V_KNOWN="$(fixture_value known)"
V_OLD="$(fixture_value old)"
printf 'API_TOKEN=%s\nPASSWORD_OLD=%s\n' "$V_KNOWN" "$V_OLD" >"$HOME_TEST/repos/app/.env"

# A file from an earlier run, written before this lane starts, is out of scope: it must be left exactly as it was.
printf 'earlier output: %s\n' "$V_OLD" >"$TOOL_OUT/tool_before_start"
touch -d '1 hour ago' "$TOOL_OUT/tool_before_start"

# The stand-in engine: saves two tool outputs that hold the real value (one in a sub-folder) and one that holds nothing secret.
cat >"$TMP/engine.sh" <<'STUB'
#!/usr/bin/env bash
printf 'gh run log: TEST_USER_PASSWORD value is %s done\n' "$LEARNED" >"$TOOL_OUT/tool_during_run"
printf 'nested: %s\n' "$LEARNED" >"$TOOL_OUT/nested/tool_nested"
printf 'build finished, 12 tests passed\n' >"$TOOL_OUT/tool_clean"
STUB
chmod +x "$TMP/engine.sh"

run() {  # run <hand folder> <prompt>
  HOME="$HOME_TEST" LANE_SCAN_REPOS="$HOME_TEST/repos" LANE_HIDE_REPOS="$HOME_TEST/repos" KNOWN_SECRETS_DIR="$HOME_TEST/.config/afa/secrets" \
    LANE_TOOL_OUTPUT_DIR="$TOOL_OUT" TOOL_OUT="$TOOL_OUT" LEARNED="$V_KNOWN" \
    bash "$SCRIPT" "$REPO" "$1" "$2" -- bash "$TMP/engine.sh" >"$TMP/run.out" 2>&1
}
run "$HAND" "$PROMPT" || fail "the lane did not run: $(cat "$TMP/run.out")"
ok

for f in tool_during_run nested/tool_nested; do
  [ -f "$TOOL_OUT/$f" ] || fail "the stand-in did not write $f"
  if grep -qF -- "$V_KNOWN" "$TOOL_OUT/$f"; then fail "a secret value is still in the tool-output file $f"; fi
  ok
done
grep -qF 'TEST_USER_PASSWORD value is <redacted:' "$TOOL_OUT/tool_during_run" || fail 'the scrubbed file does not keep its surrounding text'
ok
[ "$(cat "$TOOL_OUT/tool_clean")" = 'build finished, 12 tests passed' ] || fail 'a tool-output file with no secret was changed'
ok
grep -qF -- "$V_OLD" "$TOOL_OUT/tool_before_start" || fail 'a file written before the lane started was rewritten (only this run is in scope)'
ok
if grep -qF -- "$V_KNOWN" "$TMP/run.out"; then fail 'the run printed a secret value'; fi
ok

echo "All $checks checks passed"
