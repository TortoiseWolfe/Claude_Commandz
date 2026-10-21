#!/usr/bin/env bash
# Tests for lane_watch.sh: the stuck-dialog match, and running with no lane names.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane_watch.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-watch.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
# The watcher runs wsl_bridge_watchdog.py from every poll: tests get a fake one and their own state folder, never the live VM or state.
export WSL_BRIDGE_WATCHDOG="$TMP/fake-watchdog.py" WSL_BRIDGE_STATE="$TMP/bridge-state"
export WATCHDOG_RAN="$TMP/watchdog-ran"
printf 'import os\nopen(os.environ["WATCHDOG_RAN"], "a").write("ran\\n")\n' > "$WSL_BRIDGE_WATCHDOG"
source "$SCRIPT"

failures=0
expect_dialog() { # expect_dialog <match|none> <label> <pane text>
  local want=$1 label=$2 got=none
  if printf '%s\n' "$3" | dialog_waiting; then got=match; fi
  if [ "$got" = "$want" ]; then echo "ok   $label"; else echo "FAIL $label: want $want, got $got"; failures=$((failures + 1)); fi
}

expect_done_time() { # expect_done_time <expected time> <label> <pane text>
  local want=$1 label=$2 got
  got=$(printf '%s\n' "$3" | turn_done_time)
  if [ "$got" = "$want" ]; then echo "ok   $label"; else echo "FAIL $label: want '$want', got '$got'"; failures=$((failures + 1)); fi
}

done_pane=$(printf '%s\n' '⏺ Answered the questions' '✻ Worked for 6m 9s · done 1:05 PM')
expect_done_time "1:05" "completed turn returns its done time" "$done_pane"

no_done_pane=$(printf '%s\n' '⏺ Reading files' 'plain text')
expect_done_time "" "pane with no done line returns empty" "$no_done_pane"

working=$(printf '%s\n' '⏺ Reading lane_watch.sh' '✻ Thinking… (12s · esc to interrupt)' '')
expect_done_time "" "working pane without a done line returns empty" "$working"

permission_prompt=$(printf '%s\n' \
  'Bash command' '  git push origin main' '' \
  'Do you want to proceed?' \
  '❯ 1. Yes' \
  '  2. Yes, and allow access to ~/repos/' \
  '  3. No' \
  'Esc to cancel · Tab to amend')
expect_dialog match "permission prompt matches" "$permission_prompt"

menu=$(printf '%s\n' 'Select a model' '❯ 1. Sonnet' '  2. Opus' 'Enter to select · ↑/↓ to navigate')
expect_dialog match "Enter to select menu matches" "$menu"

expect_dialog none "working screen does not match" "$working"

# A prompt above more than 4 lines of output is still a dialog; the old tail -4 check missed it.
buried=$(printf '%s\n' 'Do you want to proceed?' '❯ 1. Yes' '  3. No' 'Esc to cancel' \
  'line a' 'line b' 'line c' 'line d' 'line e' 'line f' 'line g' 'line h')
expect_dialog match "prompt with 8 lines below it matches" "$buried"

# A dialog scrolled far above the bottom of the pane no longer counts.
scrolled=$(printf '%s\n' 'Esc to cancel'; for i in $(seq 1 20); do echo "output $i"; done)
expect_dialog none "dialog scrolled out of the last 15 lines does not match" "$scrolled"

# No lane names: the heartbeat still runs and exits 0.
home="$TMP/home"; mkdir -p "$home"
rc=0; out=$(HOME="$home" bash "$SCRIPT" --max-min 0) || rc=$?
if [ "$rc" = 0 ] && [ "$out" = "heartbeat: nothing new in 0 min" ]; then
  echo "ok   no lane names runs and exits 0 with heartbeat"
else
  echo "FAIL no lane names: rc=$rc out=$out"; failures=$((failures + 1))
fi

# A post for his chat that has failed to land for 15 minutes wakes the hub; a quiet outbox does not.
fake_box="$TMP/outbox.py"
cat > "$fake_box" <<'PY'
import sys
if sys.argv[1:] == ["queue", "check"]:
    print("outbox: 4 post(s) for Jonathan have not reached his chat since 5:21 PM (6 tries; the next at 5:52 PM).")
PY
rc=0; out=$(HOME="$home" OPENCLAW_OUTBOX="$fake_box" OPENCLAW_RECONCILE=/nonexistent bash "$SCRIPT" --max-min 0) || rc=$?
if [ "$rc" = 0 ] && [ "$(printf '%s\n' "$out" | head -1)" = "outbox: 4 post(s) for Jonathan have not reached his chat since 5:21 PM (6 tries; the next at 5:52 PM)." ]; then
  echo "ok   a post that keeps failing to land wakes the hub"
else
  echo "FAIL outbox wake: rc=$rc out=$out"; failures=$((failures + 1))
fi
printf 'import sys\n' > "$fake_box"
rc=0; out=$(HOME="$home" OPENCLAW_OUTBOX="$fake_box" OPENCLAW_RECONCILE=/nonexistent bash "$SCRIPT" --max-min 0) || rc=$?
if [ "$rc" = 0 ] && ! printf '%s\n' "$out" | grep -q '^outbox'; then
  echo "ok   a quiet outbox does not wake the hub"
else
  echo "FAIL quiet outbox: rc=$rc out=$out"; failures=$((failures + 1))
fi

# A message of his that reached nobody wakes the hub with the reconciler's lines.
fake_rec="$TMP/reconcile.py"
cat > "$fake_rec" <<'PY'
print("undelivered | 3:35 PM | oc-d5c135d5 | his reply was meant for a lane | \"it does show the porch\"")
print("filed with the hub: oc-d5c135d5")
PY
rc=0; out=$(HOME="$home" OPENCLAW_RECONCILE="$fake_rec" bash "$SCRIPT" --max-min 0) || rc=$?
if [ "$rc" = 0 ] && printf '%s\n' "$out" | head -1 | grep -q "^undelivered: messages of Jonathan's" \
   && printf '%s\n' "$out" | grep -q "^undelivered | 3:35 PM | oc-d5c135d5" && printf '%s\n' "$out" | grep -q "^filed with the hub: oc-d5c135d5"; then
  echo "ok   an undelivered message wakes the hub with the reconciler's lines"
else
  echo "FAIL undelivered wake: rc=$rc out=$out"; failures=$((failures + 1))
fi
printf 'print("")\n' > "$fake_rec"
rc=0; out=$(HOME="$home" OPENCLAW_RECONCILE="$fake_rec" bash "$SCRIPT" --max-min 0) || rc=$?
if [ "$rc" = 0 ] && ! printf '%s\n' "$out" | grep -q undelivered; then
  echo "ok   no undelivered message, no undelivered wake"
else
  echo "FAIL quiet reconcile: rc=$rc out=$out"; failures=$((failures + 1))
fi

fake_open="$TMP/open.py"
cat > "$fake_open" <<'PY'
import sys
print("oc-aaaaaaaa | 18 min | deferred | Which sample status?")
print("oc-bbbbbbbb | 12 min | fallback-lane | Where is the sample change?")
PY
rc=0; out=$(HOME="$home" OPENCLAW_DISPATCH="$fake_open" bash "$SCRIPT" --max-min 0) || rc=$?
if [ "$rc" = 0 ] && [ "$out" = "ask: open receptionist questions:
oc-aaaaaaaa | 18 min | deferred | Which sample status?
oc-bbbbbbbb | 12 min | fallback-lane | Where is the sample change?" ]; then
  echo "ok   open receptionist questions print every ask and exit"
else
  echo "FAIL open receptionist questions: rc=$rc out=$out"; failures=$((failures + 1))
fi

call_count="$TMP/open-count"
cat > "$fake_open" <<'PY'
import os
from pathlib import Path
p = Path(os.environ["OPEN_COUNT"])
p.write_text(p.read_text() + "x" if p.exists() else "x")
print("none")
PY
rc=0; out=$(HOME="$home" OPENCLAW_DISPATCH="$fake_open" OPEN_COUNT="$call_count" bash "$SCRIPT" --max-min 0) || rc=$?
if [ "$rc" = 0 ] && [ "$out" = "heartbeat: nothing new in 0 min" ] && [ "$(cat "$call_count")" = "xx" ]; then
  echo "ok   empty question queue is checked at arm and on heartbeat"
else
  echo "FAIL empty question queue: rc=$rc out=$out calls=$(cat "$call_count" 2>/dev/null || echo missing)"; failures=$((failures + 1))
fi

# Change 4: a burst of questions wakes the hub once. The wake file is touched by each question; lane_watch holds until it has been
# quiet, then prints the whole list in one wake.
burst_home="$TMP/burst"; mkdir -p "$burst_home/.claude/state"
wake="$burst_home/.claude/state/openclaw-ask.wake"
burst_open="$TMP/burst-open.py"
cat > "$burst_open" <<'PY'
import os
from pathlib import Path
p = Path(os.environ["OPEN_COUNT"])
n = len(p.read_text()) + 1 if p.exists() else 1
p.write_text("x" * n)
print("oc-aaaaaaaa | 1 min | deferred | first question")
if n >= 2:
    print("oc-bbbbbbbb | 0 min | deferred | second question, filed during the hold")
PY
rm -f "$TMP/burst-count"; echo oc-aaaaaaaa > "$wake"
t0=$(date +%s); rc=0
out=$(HOME="$burst_home" OPENCLAW_DISPATCH="$burst_open" OPEN_COUNT="$TMP/burst-count" LANE_WATCH_BATCH_QUIET=3 LANE_WATCH_BATCH_POLL=1 \
      bash "$SCRIPT" --max-min 0) || rc=$?
took=$(( $(date +%s) - t0 ))
if [ "$rc" = 0 ] && [ "$took" -ge 2 ] && [ "$(printf '%s\n' "$out" | grep -c '^ask: open receptionist questions:')" = 1 ] \
   && printf '%s\n' "$out" | grep -q 'second question, filed during the hold'; then
  echo "ok   a fresh question is held until the burst is quiet, then one wake lists every question"
else
  echo "FAIL burst hold: rc=$rc took=${took}s out=$out"; failures=$((failures + 1))
fi

# A wake file that has been quiet for a while does not delay the wake at all.
rm -f "$TMP/burst-count"; touch -d '10 minutes ago' "$wake"
t0=$(date +%s); rc=0
out=$(HOME="$burst_home" OPENCLAW_DISPATCH="$burst_open" OPEN_COUNT="$TMP/burst-count" LANE_WATCH_BATCH_QUIET=30 LANE_WATCH_BATCH_POLL=1 \
      bash "$SCRIPT" --max-min 0) || rc=$?
took=$(( $(date +%s) - t0 ))
if [ "$rc" = 0 ] && [ "$took" -le 5 ] && printf '%s\n' "$out" | grep -q 'first question'; then
  echo "ok   a quiet wake file wakes at once"
else
  echo "FAIL quiet wake: rc=$rc took=${took}s out=$out"; failures=$((failures + 1))
fi

# A burst that never goes quiet is cut off at LANE_WATCH_BATCH_MAX.
rm -f "$TMP/burst-count"; touch "$wake"
( for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do sleep 1; touch "$wake"; done ) &
toucher=$!
t0=$(date +%s); rc=0
out=$(HOME="$burst_home" OPENCLAW_DISPATCH="$burst_open" OPEN_COUNT="$TMP/burst-count" LANE_WATCH_BATCH_QUIET=5 LANE_WATCH_BATCH_MAX=3 \
      LANE_WATCH_BATCH_POLL=1 bash "$SCRIPT" --max-min 0) || rc=$?
took=$(( $(date +%s) - t0 ))
kill "$toucher" 2>/dev/null || true; wait "$toucher" 2>/dev/null || true
if [ "$rc" = 0 ] && [ "$took" -ge 2 ] && [ "$took" -le 6 ] && printf '%s\n' "$out" | grep -q 'first question'; then
  echo "ok   a burst that never goes quiet is cut off at the cap"
else
  echo "FAIL burst cap: rc=$rc took=${took}s out=$out"; failures=$((failures + 1))
fi

# A question filed while the hub is already waiting (the loop path) is held for its burst too, before the hub is woken.
loop_open="$TMP/loop-open.py"
printf 'print("none")\n' > "$loop_open"  # the question is not listed yet (its pass is still running), only the wake file changes
rm -f "$wake"
( sleep 1; echo oc-aaaaaaaa > "$wake" ) &
filer=$!
t0=$(date +%s); rc=0
out=$(HOME="$burst_home" OPENCLAW_DISPATCH="$loop_open" LANE_WATCH_POLL=1 LANE_WATCH_BATCH_QUIET=3 LANE_WATCH_BATCH_POLL=1 \
      bash "$SCRIPT" --max-min 1) || rc=$?
took=$(( $(date +%s) - t0 ))
wait "$filer" 2>/dev/null || true
if [ "$rc" = 0 ] && [ "$took" -ge 3 ] && printf '%s\n' "$out" | grep -q "^ask: a new 'Jonathan via OpenClaw:' line"; then
  echo "ok   a question filed while the hub waits is held for its burst before the wake"
else
  echo "FAIL loop-path burst: rc=$rc took=${took}s out=$out"; failures=$((failures + 1))
fi

# 18 Oct: the hub armed the watcher with no lane names and four reports and a stuck dialog sat unseen. With no names it now
# watches every live lane (from lane.sh --status, not the dead rows), re-reading the list each poll.
auto="$TMP/auto"; mkdir -p "$auto/.claude/scripts" "$auto/.claude/state/lanes" "$TMP/fakebin"
export LIVE_FILE="$TMP/live-lanes" PANE_DIR="$TMP/panes"; mkdir -p "$PANE_DIR"
cat > "$auto/.claude/scripts/lane.sh" <<'SH'
#!/usr/bin/env bash
# fake lane.sh --status: two rows per live lane, then a dead one
while read -r n; do [ -n "$n" ] && { echo "$n  report:no  working"; echo "    dir:/x model:sonnet"; }; done < "$LIVE_FILE"
echo "oldlane  dead, resume with: lane.sh --resume oldlane   (/x)"
SH
chmod +x "$auto/.claude/scripts/lane.sh"
cat > "$TMP/fakebin/tmux" <<'SH'
#!/usr/bin/env bash
case "$1" in
  list-windows) cat "$LIVE_FILE" ;;
  capture-pane) for last; do :; done; cat "$PANE_DIR/${last#lanes:}" 2>/dev/null || true ;;
esac
SH
chmod +x "$TMP/fakebin/tmux"
fake_dispatch="$TMP/auto-dispatch.py"
cat > "$fake_dispatch" <<'PY'
import os, sys
if sys.argv[1:2] == ["post-reports"]:
    open(os.environ["POSTED_MARK"], "a").write("x")
else:
    print("none")
PY
echo early > "$LIVE_FILE"
mkdir -p "$auto/.claude/state/lanes/2026-10-18-late" "$auto/.claude/state/lanes/2026-10-18-oldlane"

# A lane that starts after the watcher writes its report: the watcher wakes with "report: late".
out="$TMP/auto-out"; : > "$out"
( HOME="$auto" PATH="$TMP/fakebin:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" POSTED_MARK="$TMP/posted-mark" LANE_WATCH_SETTLE=0 LANE_WATCH_POLL=1 \
    timeout 25 bash "$SCRIPT" --max-min 5 > "$out" 2>&1 ) &
watcher=$!
sleep 2.5; echo late >> "$LIVE_FILE"
sleep 2.5; echo "Done." > "$auto/.claude/state/lanes/2026-10-18-late/report.md"
wait "$watcher" || true
if grep -q '^report: late ' "$out"; then
  echo "ok   no lane names: a lane started after the watcher is watched, and its report wakes it"
else
  echo "FAIL late lane report: out=$(cat "$out")"; failures=$((failures + 1))
fi
sleep 1
if [ -s "$TMP/posted-mark" ]; then
  echo "ok   a report asks the dispatcher to record new reports (it posts nothing to the chat)"
else
  echo "FAIL report did not call post-reports"; failures=$((failures + 1))
fi

# A lane started after the watcher that stops on a dialog wakes it too.
: > "$out"; echo early > "$LIVE_FILE"; rm -f "$auto/.claude/state/lanes/2026-10-18-late/report.md"
( HOME="$auto" PATH="$TMP/fakebin:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 timeout 25 bash "$SCRIPT" --max-min 5 > "$out" 2>&1 ) &
watcher=$!
sleep 2.5; echo late >> "$LIVE_FILE"
printf '%s\n' 'Run it?' 'Do you want to proceed?' '❯ 1. Yes' 'Esc to cancel' > "$PANE_DIR/late"
wait "$watcher" || true
if grep -q '^waiting on a dialog: late' "$out"; then
  echo "ok   no lane names: a late lane stuck on a dialog wakes the watcher"
else
  echo "FAIL late lane dialog: out=$(cat "$out")"; failures=$((failures + 1))
fi
rm -f "$PANE_DIR/late"

# Dead lanes are not watched: a report from the dead row's lane wakes nothing.
: > "$out"; echo early > "$LIVE_FILE"
( HOME="$auto" PATH="$TMP/fakebin:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 timeout 6 bash "$SCRIPT" --max-min 5 > "$out" 2>&1 ) &
watcher=$!
sleep 2; echo "Done." > "$auto/.claude/state/lanes/2026-10-18-oldlane/report.md"
wait "$watcher" || true
if [ ! -s "$out" ]; then
  echo "ok   no lane names: a dead lane's report does not wake the watcher"
else
  echo "FAIL dead lane woke the watcher: out=$(cat "$out")"; failures=$((failures + 1))
fi

# Named lanes work as before: only the named lane is watched.
: > "$out"; echo "early" > "$LIVE_FILE"; echo late >> "$LIVE_FILE"; rm -f "$auto/.claude/state/lanes/2026-10-18-late/report.md"
( HOME="$auto" PATH="$TMP/fakebin:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 timeout 25 bash "$SCRIPT" --max-min 5 early > "$out" 2>&1 ) &
watcher=$!
sleep 2; echo "Done." > "$auto/.claude/state/lanes/2026-10-18-late/report.md"   # not named: ignored
sleep 2; mkdir -p "$auto/.claude/state/lanes/2026-10-18-early"; echo "Done." > "$auto/.claude/state/lanes/2026-10-18-early/report.md"
wait "$watcher" || true
if grep -q '^report: early ' "$out" && ! grep -q 'late' "$out"; then
  echo "ok   named lanes: only the named lane is watched"
else
  echo "FAIL named lanes: out=$(cat "$out")"; failures=$((failures + 1))
fi

# 19 Oct: a report from a lane that was told something after it (or has a job running) says "report stale", so the hub does not close it.
stale_home="$TMP/stale"; mkdir -p "$stale_home/.claude/scripts" "$stale_home/.claude/state/lanes/2026-10-19-told"
cat > "$stale_home/.claude/scripts/lane.sh" <<'SH'
#!/usr/bin/env bash
echo "told  report:stale idle    ✻ Worked for 3m"
echo "    report stale: the lane got input after report.md was written; the report is not its last word"
echo "    dir:/x model:sonnet"
echo "calm  report:yes idle    done"
SH
chmod +x "$stale_home/.claude/scripts/lane.sh"
echo "Done." > "$stale_home/.claude/state/lanes/2026-10-19-told/report.md"
note=$(HOME="$stale_home" stale_note told); calm=$(HOME="$stale_home" stale_note calm)
if [ "${note#report stale: the lane got input}" != "$note" ] && [ -z "$calm" ]; then
  echo "ok   a stale report gets a note saying so; a settled one gets none"
else
  echo "FAIL stale_note: told='$note' calm='$calm'"; failures=$((failures + 1))
fi
rm -f "$LIVE_FILE"; echo told > "$LIVE_FILE"; rm -rf "$stale_home/.claude/state/lanes/2026-10-19-told"; : > "$out"
( HOME="$stale_home" PATH="$TMP/fakebin:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" POSTED_MARK="$TMP/posted-mark2" LANE_WATCH_SETTLE=0 LANE_WATCH_POLL=1 \
    timeout 20 bash "$SCRIPT" --max-min 5 told > "$out" 2>&1 ) &
watcher=$!
sleep 2; mkdir -p "$stale_home/.claude/state/lanes/2026-10-19-told"; echo "Done." > "$stale_home/.claude/state/lanes/2026-10-19-told/report.md"
wait "$watcher" || true
if grep -q '^report: told .*report stale: the lane got input.*do not close it' "$out"; then
  echo "ok   the report wake says report stale and not to close the lane"
else
  echo "FAIL stale wake: out=$(cat "$out")"; failures=$((failures + 1))
fi

# The bridge alert: printed once per change of the alert file, then silent.
mkdir -p "$WSL_BRIDGE_STATE"
first=$(bridge_alert)
if [ -z "$first" ]; then echo "ok   no alert file: nothing to print"; else echo "FAIL bridge_alert printed '$first' with no alert"; failures=$((failures + 1)); fi
echo "9:05 PM wsl bridge: free order-7+ blocks 0 (min 200) after healing, probe FAILED" > "$WSL_BRIDGE_STATE/alert"
first=$(bridge_alert); second=$(bridge_alert)
if [ "${first#9:05 PM wsl bridge}" != "$first" ] && [ -z "$second" ]; then echo "ok   a new alert prints once, then is silent"; else echo "FAIL bridge_alert: first='$first' second='$second'"; failures=$((failures + 1)); fi
sleep 1; echo "9:40 PM wsl bridge: again" > "$WSL_BRIDGE_STATE/alert"
third=$(bridge_alert)
if [ "${third#9:40 PM}" != "$third" ]; then echo "ok   a rewritten alert prints again"; else echo "FAIL bridge_alert after rewrite: '$third'"; failures=$((failures + 1)); fi

# The watcher runs the watchdog on each poll and wakes on its alert.
rm -f "$TMP/watchdog-ran" "$WSL_BRIDGE_STATE/alert" "$WSL_BRIDGE_STATE/alert.seen"
bridge_home="$TMP/bridgehome"; mkdir -p "$bridge_home/.claude/scripts" "$bridge_home/.claude/state"
printf '#!/usr/bin/env bash\necho "nobody  dead"\n' > "$bridge_home/.claude/scripts/lane.sh"; chmod +x "$bridge_home/.claude/scripts/lane.sh"
out="$TMP/bridge-out"
( HOME="$bridge_home" PATH="$TMP/fakebin:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 timeout 20 bash "$SCRIPT" --max-min 5 > "$out" 2>&1 ) &
watcher=$!
sleep 3; echo "9:05 PM wsl bridge: probe FAILED" > "$WSL_BRIDGE_STATE/alert"
wait "$watcher" || true
if [ -s "$TMP/watchdog-ran" ] && grep -q '^bridge: 9:05 PM wsl bridge: probe FAILED' "$out"; then
  echo "ok   the watcher runs the watchdog and wakes the hub on its alert"
else
  echo "FAIL bridge wake: ran=$(cat "$TMP/watchdog-ran" 2>/dev/null | wc -l) out=$(cat "$out")"; failures=$((failures + 1))
fi

# Lanes never auto-compact (his rule, 20 Oct): past LANE_RECYCLE_K thousand tokens, with the turn ended, the watcher starts
# `lane.sh --recycle` in the background and wakes nobody. A fake lane.sh stands in for the real one (tests/test_lane_recycle.sh has the real).
rc_home="$TMP/rchome"; mkdir -p "$rc_home/.claude/scripts" "$rc_home/.claude/state/lanes"
export POINT_FILE="$TMP/rc-point" TURN_FILE="$TMP/rc-turn" CTX_FILE="$TMP/rc-ctx" RC_CALLS="$TMP/rc-calls" RC_FAIL="$TMP/rc-fail" RC_PANE="$TMP/rc-pane"
cat > "$rc_home/.claude/scripts/lane.sh" <<'SH'
#!/usr/bin/env bash
lanes="$HOME/.claude/state/lanes"
case "$1" in
  --status) echo "rl  report:no  idle"; echo "    dir:/x model:sonnet" ;;
  --ctx) cat "$CTX_FILE" ;;
  --turn) cat "${TURN_FILE:-/dev/null}" ;;
  --recycle-point) cat "${POINT_FILE:-/dev/null}" ;;
  --recycle)
    echo "$2" >> "$RC_CALLS"; mkdir "$lanes/.recycle-$2.lock"; sleep 3
    [ -e "$RC_FAIL" ] && printf '2026-10-21T10:00:00-04:00\t%s\tFAILED\tno new handoff.md in 600s; the lane was NOT cleared\n' "$2" >> "$lanes/recycle.log"
    rmdir "$lanes/.recycle-$2.lock" ;;
esac
SH
chmod +x "$rc_home/.claude/scripts/lane.sh"
cat > "$TMP/fakebin-rc.tmux" <<'SH'
#!/usr/bin/env bash
case "$1" in
  list-windows) echo rl ;;
  capture-pane)
    if [ -n "${RC_TICK:-}" ]; then   # a lane that ends a turn between every two looks (it answers Monitor events)
      n=$(( $(cat "$RC_TICK" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$RC_TICK"
      printf '%s\n' '⏺ tick' "✻ Worked for 2s · done 3:$(printf %02d $((n % 60))) PM" '❯ '
    else cat "$RC_PANE"; fi ;;
esac
SH
mkdir -p "$TMP/fakebin-rc"; cp "$TMP/fakebin-rc.tmux" "$TMP/fakebin-rc/tmux"; chmod +x "$TMP/fakebin-rc/tmux"
idle_pane=$(printf '%s\n' '⏺ Merged.' '✻ Worked for 4m 2s · done 1:05 PM' '❯ ')
busy_pane=$(printf '%s\n' '⏺ Reading' '✻ Thinking… (12s · esc to interrupt)' '❯ ')

# recycle_due: over the lane's own line, no dialog, no recycle under way; a working lane counts (a long turn outruns the backstop).
lane_sh="$rc_home/.claude/scripts/lane.sh"; recycle_dir="$rc_home/.claude/state/lanes"; echo 120 > "$POINT_FILE"
expect_due() { # expect_due <yes|no> <label> <ctx> <pane>
  local want=$1 label=$2 got=no
  echo "$3" > "$CTX_FILE"
  if printf '%s\n' "$4" | HOME="$rc_home" recycle_due rl; then got=yes; fi
  if [ "$got" = "$want" ]; then echo "ok   $label"; else echo "FAIL $label: want $want, got $got"; failures=$((failures + 1)); fi
}
expect_due yes "recycle_due: 130K and idle fires" 130 "$idle_pane"
expect_due yes "recycle_due: exactly the line (120K) fires" 120 "$idle_pane"
expect_due no  "recycle_due: 119K does not" 119 "$idle_pane"
expect_due no  "recycle_due: an unreadable context (?) does not" '?' "$idle_pane"
expect_due yes "recycle_due: a lane mid-turn (spinner) is recycled too, its turn may not end before the backstop" 130 "$busy_pane"
expect_due no  "recycle_due: a lane mid-turn under its line is left alone" 119 "$busy_pane"
expect_due no  "recycle_due: a lane stuck on a dialog is not recycled" 200 "$(printf '%s\n' 'Do you want to proceed?' '❯ 1. Yes' 'Esc to cancel')"
mkdir "$recycle_dir/.recycle-rl.lock"
expect_due no  "recycle_due: a lane already being recycled is left alone" 200 "$idle_pane"
rmdir "$recycle_dir/.recycle-rl.lock"

# Each lane has its own line (lane.sh --recycle-point): 90K fires at 95K, 150K does not at 140K, no answer falls back to 100K, and
# LANE_RECYCLE_K, when set, overrides all of them.
echo 90 > "$POINT_FILE";  expect_due yes "recycle_due: a lane with its own line of 90K fires at 95K" 95 "$idle_pane"
echo 90 > "$POINT_FILE";  expect_due no  "recycle_due: ... and not at 89K" 89 "$idle_pane"
echo 150 > "$POINT_FILE"; expect_due no  "recycle_due: a lane with its own line of 150K is left alone at 140K" 140 "$idle_pane"
: > "$POINT_FILE";        expect_due yes "recycle_due: no answer from lane.sh falls back to 100K (fires at 100K)" 100 "$idle_pane"
: > "$POINT_FILE";        expect_due no  "recycle_due: ... and not at 99K" 99 "$idle_pane"
echo 90 > "$POINT_FILE"
got=no; echo 95 > "$CTX_FILE"; if printf '%s\n' "$idle_pane" | LANE_RECYCLE_K=120 HOME="$rc_home" recycle_due rl; then got=yes; fi
if [ "$got" = no ]; then echo "ok   LANE_RECYCLE_K overrides the lane's own line"; else echo "FAIL LANE_RECYCLE_K did not override"; failures=$((failures + 1)); fi
echo 120 > "$POINT_FILE"

# Whole watcher: starts one recycle (not one per poll), and neither the recycle nor the hand-off turn wakes the hub.
run_rc_watcher() { # run_rc_watcher <ctx> <seconds> <out>; the pane's done time changes while the recycle holds its lock
  echo "$1" > "$CTX_FILE"; : > "$RC_CALLS"; printf '%s\n' "$idle_pane" > "$RC_PANE"; rm -f "$rc_home/.claude/state/lanes/.recycle-rl.tried"
  ( HOME="$rc_home" PATH="$TMP/fakebin-rc:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 LANE_RECYCLE_RETRY_SECS=600 \
      timeout "$2" bash "$SCRIPT" --max-min 5 rl > "$3" 2>&1 ) &
  rcw=$!
  sleep 1.5; printf '%s\n' '⏺ handoff written' '✻ Worked for 1m 9s · done 1:09 PM' '❯ ' > "$RC_PANE"
  wait "$rcw" || true
}
run_rc_watcher 130 9 "$TMP/rc-out1"
if [ "$(wc -l < "$RC_CALLS")" = 1 ] && [ "$(cat "$RC_CALLS")" = rl ] && ! grep -q 'idle:\|recycle failed' "$TMP/rc-out1"; then
  echo "ok   the watcher recycles a lane past the line once, and its hand-off turn wakes nobody"
else
  echo "FAIL recycle trigger: calls=$(cat "$RC_CALLS" | tr '\n' ' ') out=$(cat "$TMP/rc-out1")"; failures=$((failures + 1))
fi
# A lane in the middle of a long turn (spinner on screen the whole time) is recycled all the same: 21 Oct, a 15-minute turn took a lane from
# 120K to the 168K backstop and it compacted, because the old trigger waited for the turn to end.
echo 130 > "$CTX_FILE"; : > "$RC_CALLS"; printf '%s\n' "$busy_pane" > "$RC_PANE"; rm -f "$rc_home/.claude/state/lanes/.recycle-rl.tried"
( HOME="$rc_home" PATH="$TMP/fakebin-rc:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 LANE_RECYCLE_RETRY_SECS=600 \
    timeout 6 bash "$SCRIPT" --max-min 5 rl > "$TMP/rc-outbusy" 2>&1 ) || true
if [ "$(cat "$RC_CALLS")" = rl ]; then echo "ok   the watcher recycles a lane past its line while its turn is still running"; else echo "FAIL no mid-turn recycle: calls=$(cat "$RC_CALLS") out=$(cat "$TMP/rc-outbusy")"; failures=$((failures + 1)); fi
run_rc_watcher 100 6 "$TMP/rc-out2"
if [ ! -s "$RC_CALLS" ]; then echo "ok   a lane under the line is not recycled"; else echo "FAIL recycled at 100K: $(cat "$RC_CALLS")"; failures=$((failures + 1)); fi
: > "$RC_FAIL"
run_rc_watcher 130 20 "$TMP/rc-out3"
if grep -q '^recycle failed: rl: no new handoff.md' "$TMP/rc-out3"; then echo "ok   a failed recycle wakes the hub, naming the lane and why"; else echo "FAIL recycle failure not reported: $(cat "$TMP/rc-out3")"; failures=$((failures + 1)); fi
rm -f "$RC_FAIL"

# 19 Oct 11:59 PM: a lane watching a download with Monitor ended a 2-second turn on every progress line, and each one woke the hub. A turn that
# only answered a Monitor event and asked nothing (lane.sh --turn says monitor) wakes nobody; any other end of turn still does.
echo monitor > "$TURN_FILE"
run_rc_watcher 50 6 "$TMP/mon-out1"
if ! grep -q 'idle:' "$TMP/mon-out1"; then echo "ok   a turn that only answered a Monitor event wakes nobody"; else echo "FAIL monitor tick woke the hub: $(cat "$TMP/mon-out1")"; failures=$((failures + 1)); fi
echo real > "$TURN_FILE"
run_rc_watcher 50 8 "$TMP/mon-out2"
if grep -q '^idle: rl ended a turn (1:09)' "$TMP/mon-out2"; then echo "ok   any other end of turn still wakes the hub"; else echo "FAIL real end of turn did not wake: $(cat "$TMP/mon-out2")"; failures=$((failures + 1)); fi
: > "$TURN_FILE"
run_rc_watcher 50 8 "$TMP/mon-out3"
if grep -q '^idle: rl ended a turn' "$TMP/mon-out3"; then echo "ok   a turn the transcript cannot classify wakes the hub"; else echo "FAIL unknown turn did not wake: $(cat "$TMP/mon-out3")"; failures=$((failures + 1)); fi

# 21 Oct 2:16-2:25 PM: coop-site went 100K to 153K with no recycle although the watcher was re-armed 15 times. The recycle check sat in the
# else-branch of the end-of-turn checks, and a lane answering Monitor events ends a turn between nearly every two polls, so it was skipped.
# The fake tmux below ends a turn between every two looks.
tick_watcher() { # tick_watcher <turn-kind> <seconds> <out>
  echo 130 > "$CTX_FILE"; : > "$RC_CALLS"; echo "$1" > "$TURN_FILE"; : > "$TMP/rc-tick"; rm -f "$rc_home/.claude/state/lanes/.recycle-rl.tried"
  ( HOME="$rc_home" PATH="$TMP/fakebin-rc:$PATH" RC_TICK="$TMP/rc-tick" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 LANE_RECYCLE_RETRY_SECS=600 \
      timeout "$2" bash "$SCRIPT" --max-min 5 rl > "$3" 2>&1 ) || true
}
tick_watcher monitor 6 "$TMP/tick-out1"
if [ "$(cat "$RC_CALLS")" = rl ]; then echo "ok   a lane that ends a Monitor turn on every poll is still recycled past its line"; else echo "FAIL tick lane (monitor) not recycled: calls=$(cat "$RC_CALLS") out=$(cat "$TMP/tick-out1")"; failures=$((failures + 1)); fi
tick_watcher real 6 "$TMP/tick-out2"
if [ "$(cat "$RC_CALLS")" = rl ] && grep -q '^idle: rl' "$TMP/tick-out2"; then echo "ok   a pass that reports an end of turn also starts the recycle (it does not wait for the next pass)"; else echo "FAIL tick lane (real): calls=$(cat "$RC_CALLS") out=$(cat "$TMP/tick-out2")"; failures=$((failures + 1)); fi
# The retry spacing outlives the watcher: the hub re-arms it every few minutes, and a second watcher must not start a second recycle at once.
echo 130 > "$CTX_FILE"; : > "$RC_CALLS"; date +%s > "$rc_home/.claude/state/lanes/.recycle-rl.tried"; printf '%s\n' "$busy_pane" > "$RC_PANE"
( HOME="$rc_home" PATH="$TMP/fakebin-rc:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 LANE_RECYCLE_RETRY_SECS=600 \
    timeout 4 bash "$SCRIPT" --max-min 5 rl > "$TMP/tick-out3" 2>&1 ) || true
if [ ! -s "$RC_CALLS" ]; then echo "ok   a re-armed watcher honours the retry time of the one before it"; else echo "FAIL second watcher recycled again at once: $(cat "$RC_CALLS")"; failures=$((failures + 1)); fi
: > "$TURN_FILE"

[ "$failures" = 0 ] || { echo "$failures failed"; exit 1; }
echo "all lane_watch tests passed"
