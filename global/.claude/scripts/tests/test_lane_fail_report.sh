#!/usr/bin/env bash
# Tests for #234 (5 Nov 2026: lane.sh sent a lane to agy while agy was out of quota; the lane died in its first minute with no report.md and nobody
# was told for 70 minutes). A headless lane that exits non-zero with no report.md now gets "FAILED exit <code> | <last error line>" as report line 1,
# and an engine that says when its quota resets is refused at the next start until then. A fake HOME and a fake tmux; nothing here is live.
set -euo pipefail

SCRIPTS="$(cd "$(dirname "$0")/.." && pwd -P)"
SCRIPT="$SCRIPTS/lane.sh"; HELPER="$SCRIPTS/lane_fail_report.py"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-failreport.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; STATE="$HOME_T/.claude/state"; OWN="$HOME_T/repos/own-app"; LANES="$STATE/lanes"
mkdir -p "$BIN" "$OWN" "$LANES" "$TMP/h" "$HOME_T/repos/hub"
printf "id\trepo_dir\nacme\t~/repos/client-app\n" >"$HOME_T/repos/hub/client-senders.tsv"
printf "# Client repositories\n" >"$HOME_T/repos/hub/client-repos.txt"
printf '#!/usr/bin/env bash\ncase "$1" in has-session) exit 0 ;; list-windows) exit 0 ;; capture-pane) printf "ready\\n" ;; esac\n' >"$BIN/tmux"
printf '#!/usr/bin/env bash\nexit 0\n' >"$BIN/sleep"
chmod +x "$BIN/tmux" "$BIN/sleep"
export AFA_ENGINES_AVAILABLE="*"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
assert_eq() { [ "$1" = "$2" ] || fail "$3 (expected: $2; got: $1)"; ok; }
assert_contains() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }

# ---- the helper ------------------------------------------------------------------------------------------------------------------------------
H="$TMP/h"
printf 'starting\nError: 429 RESOURCE_EXHAUSTED: quota exceeded. Resets in 91h56m\nexit 3\n' >"$H/out.log"
python3 -I "$HELPER" "$H/report.md" "$H/out.log" 3 agy "$STATE"
assert_eq "$(cat "$H/report.md")" 'FAILED exit 3 | Error: 429 RESOURCE_EXHAUSTED: quota exceeded. Resets in 91h56m' 'a failed lane with no report gets a FAILED line 1'
want=$(( $(date +%s) + 91 * 3600 + 56 * 60 ))
got="$(cat "$STATE/engine-out/agy.until")"
[ "$got" -ge $((want - 5)) ] && [ "$got" -le $((want + 5)) ] || fail "the reset time is about 91h56m from now (got $got, want about $want)"; ok

# A report the lane wrote itself is never touched.
printf 'DONE it worked\n' >"$H/report.md"
python3 -I "$HELPER" "$H/report.md" "$H/out.log" 3 agy "$STATE"
assert_eq "$(cat "$H/report.md")" 'DONE it worked' "the lane's own report is left alone"

# An empty report counts as missing; an error with no reset time writes the report and no marker.
: >"$H/report.md"; rm -rf "$STATE/engine-out"
printf 'opencode: boom\nTraceback\nValueError: bad key\nexit 1\n' >"$H/out.log"
python3 -I "$HELPER" "$H/report.md" "$H/out.log" 1 opencode "$STATE"
assert_eq "$(cat "$H/report.md")" 'FAILED exit 1 | ValueError: bad key' 'an empty report is replaced, and the last error line is the one shown'
[ ! -e "$STATE/engine-out/opencode.until" ] || fail 'no reset time in the log means no marker'; ok

# No out.log at all still writes a report.
rm -f "$H/report.md"
python3 -I "$HELPER" "$H/report.md" "$H/missing.log" 2 codex "$STATE"
assert_eq "$(cat "$H/report.md")" 'FAILED exit 2 | no output' 'no output at all still gives the watcher something'

# A report.md that is a symlink is never written through.
rm -f "$H/report.md"; echo keep >"$TMP/target"; ln -s "$TMP/target" "$H/report.md"
python3 -I "$HELPER" "$H/report.md" "$H/out.log" 1 codex "$STATE"
assert_eq "$(cat "$TMP/target")" keep 'a symlinked report.md is not written through'
rm -f "$H/report.md"

# #260 (5 Nov 2026): a lane that ends while line 1 of its report still says WORKING left the hub with no news (two lanes ended on Claude Code's
# 10-minute background wait with exit 0). The helper now rewrites that line 1, whatever the exit code, and keeps the rest of the report.
printf 'WORKING 9:27 PM | PR 144 | checks pending\nnotes line\n' >"$H/report.md"
printf 'Background tasks still running 10m after the last turn; stopping them.\nexit 0\n' >"$H/out.log"
python3 -I "$HELPER" "$H/report.md" "$H/out.log" 0 claude "$STATE"
assert_eq "$(head -1 "$H/report.md")" 'ENDED WHILE WORKING exit 0 | was: WORKING 9:27 PM | PR 144 | checks pending' 'a lane that ends on WORKING gets an ENDED line 1, exit 0 included'
assert_eq "$(sed -n 2p "$H/report.md")" 'notes line' 'the rest of its report is kept'
printf 'WORKING 9:27 PM | x\n' >"$H/report.md"
python3 -I "$HELPER" "$H/report.md" "$H/out.log" 1 claude "$STATE"
assert_eq "$(cat "$H/report.md")" 'ENDED WHILE WORKING exit 1 | was: WORKING 9:27 PM | x' 'a non-zero exit on WORKING gets the same line with its code'
printf 'DONE shipped\n' >"$H/report.md"
python3 -I "$HELPER" "$H/report.md" "$H/out.log" 0 claude "$STATE"
assert_eq "$(cat "$H/report.md")" 'DONE shipped' 'a clean exit with a DONE report is left alone'
rm -f "$H/report.md"; python3 -I "$HELPER" "$H/report.md" "$H/out.log" 0 claude "$STATE"
[ ! -e "$H/report.md" ] || fail 'a clean exit with no report writes nothing'; ok
rm -rf "$STATE/engine-out"; printf 'quota note\nResets in 5h\nexit 0\n' >"$H/out.log"
python3 -I "$HELPER" "$H/report.md" "$H/out.log" 0 claude "$STATE"
[ ! -e "$STATE/engine-out/claude.until" ] || fail 'a clean exit never sets a quota marker'; ok

# ---- lane.sh --------------------------------------------------------------------------------------------------------------------------------
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }
P="$TMP/p.md"; echo 'Fix the lint errors.' >"$P"

# The command a headless lane runs calls the helper after a failed run, and only then.
lane --dry-run --engine agy fr-ok "$OWN" sonnet "$P"
assert_eq "$LAST_RC" 0 'an agy lane starts when no quota marker exists'
assert_contains "$LAST_OUT" 'lane_fail_report.py' 'the lane command calls the failure helper'
assert_contains "$LAST_OUT" '\"\$lane_rc\"\ -eq\ 0\ \]\ \|\|\ python3' 'the helper runs only when the engine exited non-zero (tmux send-keys shows it shell-escaped)'

# A live quota marker refuses that engine, names the reset time, and leaves other engines alone.
mkdir -p "$STATE/engine-out"; echo $(( $(date +%s) + 3600 )) >"$STATE/engine-out/agy.until"
lane --dry-run --engine agy fr-out "$OWN" sonnet "$P"
assert_eq "$LAST_RC" 1 'an engine out of quota is refused'
assert_contains "$LAST_OUT" 'refusing engine agy: it said it is out of quota and resets at' 'the refusal says why and when'
lane --dry-run --engine opencode fr-oc "$OWN" sonnet "$P"
assert_eq "$LAST_RC" 0 'another engine still starts'

# An expired marker no longer refuses.
echo $(( $(date +%s) - 60 )) >"$STATE/engine-out/agy.until"
lane --dry-run --engine agy fr-back "$OWN" sonnet "$P"
assert_eq "$LAST_RC" 0 'an expired marker is ignored'

# #260: a headless Claude lane waits up to an hour for a background job, not Claude Code's 10 minutes, and the helper runs on every exit.
lane --claude-because "fail report test" --dry-run --console --headless fr-hl "$OWN" sonnet "$P"
assert_eq "$LAST_RC" 0 'a headless Claude lane starts'
assert_contains "$LAST_OUT" 'CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=3600000' 'a headless lane waits an hour for a background job'
assert_contains "$LAST_OUT" 'lane_fail_report.py' 'a headless Claude lane calls the helper when it exits'
CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=1800000 lane --claude-because "fail report test" --dry-run --console --headless fr-hl2 "$OWN" sonnet "$P"
assert_contains "$LAST_OUT" 'CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=1800000' 'a value already set is kept'

echo "ok - $checks checks passed"
