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

working=$(printf '%s\n' '⏺ Reading lane_watch.sh' '✻ Thinking… (12s · esc to interrupt)' '')
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

[ "$failures" = 0 ] || { echo "$failures failed"; exit 1; }
echo "all lane_watch tests passed"
