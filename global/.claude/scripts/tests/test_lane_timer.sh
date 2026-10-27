#!/usr/bin/env bash
# test_lane_timer.sh: target 70 seconds ahead, the command must fire within 30 seconds after it.

set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane_timer.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-timer-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

export LANE_TIMER_DIR="$TMP/timers"
mkdir -p "$LANE_TIMER_DIR"

fail() { echo "FAIL: $1" >&2; exit 1; }
ok() { echo "ok: $1"; }

# Test 1: Basic timer fires after target time (70s target, must fire within 30s after = 70-100s)
target=$(date -d "+70 seconds" "+%Y-%m-%d %H:%M:%S")
start_epoch=$(date +%s)
outfile="$TMP/fired.txt"

"$SCRIPT" test-timer "$target" -- touch "$outfile"

# Test 3: --list shows running timer WHILE it's waiting (check at ~30s)
sleep 30
list_out=$("$SCRIPT" --list)
echo "$list_out" | grep -q "test-timer: running" || fail "--list doesn't show running timer: $list_out"
ok "--list shows running timer"

# Wait for timer to fire (target + 30 seconds max)
max_wait=110
for i in $(seq 31 $max_wait); do
    if [ -f "$outfile" ]; then
        fire_epoch=$(date +%s)
        elapsed=$((fire_epoch - start_epoch))
        if [ "$elapsed" -ge 70 ] && [ "$elapsed" -le 100 ]; then
            ok "timer fired at ${elapsed}s (target 70s, tolerance 30s)"
        else
            fail "timer fired at ${elapsed}s, expected 70-100s"
        fi
        break
    fi
    sleep 1
done

[ -f "$outfile" ] || fail "timer did not fire within ${max_wait}s"

# Test 2: Refuses second timer with same name (even after first completes)
target2=$(date -d "+80 seconds" "+%Y-%m-%d %H:%M:%S")
if "$SCRIPT" test-timer "$target2" -- touch "$TMP/should_not_fire.txt" 2>/dev/null; then
    fail "second timer with same name should be refused"
else
    ok "second timer with same name refused"
fi

# Test 4: --cancel stops timer and removes it from --list
target3=$(date -d "+120 seconds" "+%Y-%m-%d %H:%M:%S")
"$SCRIPT" test-timer2 "$target3" -- touch "$TMP/timer2.txt"

"$SCRIPT" --cancel test-timer2
list_out=$("$SCRIPT" --list)
# Canceled timer should not appear in --list (pid file removed)
echo "$list_out" | grep -q "test-timer2" && fail "--cancel should remove timer from list: $list_out" || ok "--cancel removes timer from --list"

# Test 5: Custom log path
target4=$(date -d "+5 seconds" "+%Y-%m-%d %H:%M:%S")
custom_log="$TMP/custom.log"
"$SCRIPT" test-timer3 "$target4" --log "$custom_log" -- echo "custom log test"
sleep 10
[ -f "$custom_log" ] || fail "custom log path not used"
grep -q "custom log test" "$custom_log" || fail "custom log doesn't contain output"
ok "custom log path works"

echo "All tests passed"
