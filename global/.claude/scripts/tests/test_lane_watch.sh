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

[ "$failures" = 0 ] || { echo "$failures failed"; exit 1; }
echo "all lane_watch tests passed"
