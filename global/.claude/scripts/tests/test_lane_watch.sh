#!/usr/bin/env bash
# Tests for lane_watch.sh: the stuck-dialog match, and running with no lane names.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane_watch.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-watch.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
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
  echo "ok   a report asks the dispatcher to post new reports to the chat"
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

[ "$failures" = 0 ] || { echo "$failures failed"; exit 1; }
echo "all lane_watch tests passed"
