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

[ "$failures" = 0 ] || { echo "$failures failed"; exit 1; }
echo "all lane_watch tests passed"
