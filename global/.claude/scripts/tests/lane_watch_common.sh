# Shared setup for the three lane_watch tests (test_lane_watch.sh, test_lane_watch_reminders.sh, test_lane_watch_wakes.sh).
# Loaded with "." from a test file, so $0 is still that test file. It builds fixtures and defines helper functions; it runs no watcher and sleeps nowhere.
# The CI machine has no claude, agy or codex program; the engine check (test_lane_engine_check.sh) is tested on its own.
export AFA_ENGINES_AVAILABLE="*"
# The idle heartbeat stretch (tests/test_lane_watch_quiet.sh covers it) would turn every "--max-min 0" run here into a two-hour wait.
export LANE_WATCH_IDLE_MAX_MIN=0
export LANE_WATCH_IDLE_REMIND_MIN=0   # off for the older cases; the idle-reminder cases in test_lane_watch_reminders.sh turn it on
# hub_transcript reads these two: left set, the hub-transcript checks would read the real session's transcript, not the test's.
unset CLAUDE_CODE_SESSION_ID AFA_HUB_SESSION_ID

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane_watch.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-watch.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
# The watcher runs wsl_bridge_watchdog.py from every poll: tests get a fake one and their own state folder, never the live VM or state.
export WSL_BRIDGE_WATCHDOG="$TMP/fake-watchdog.py" WSL_BRIDGE_STATE="$TMP/bridge-state"
export WATCHDOG_RAN="$TMP/watchdog-ran"
printf 'import os\nopen(os.environ["WATCHDOG_RAN"], "a").write("ran\\n")\n' > "$WSL_BRIDGE_WATCHDOG"
source "$SCRIPT"

failures=0
fake_dispatch="$TMP/auto-dispatch.py"
cat > "$fake_dispatch" <<'PY'
import os, sys
if sys.argv[1:2] == ["post-reports"]:
    open(os.environ["POSTED_MARK"], "a").write("x")
else:
    print("none")
PY
rc_home="$TMP/rchome"; mkdir -p "$rc_home/.claude/scripts" "$rc_home/.claude/state/lanes"
export POINT_FILE="$TMP/rc-point" TURN_FILE="$TMP/rc-turn" CTX_FILE="$TMP/rc-ctx" RC_CALLS="$TMP/rc-calls" RC_FAIL="$TMP/rc-fail" RC_PANE="$TMP/rc-pane" JOB_FILE="$TMP/rc-job"
cat > "$rc_home/.claude/scripts/lane.sh" <<'SH'
#!/usr/bin/env bash
lanes="$HOME/.claude/state/lanes"
case "$1" in
  --status)
    case "$(cat "${JOB_FILE:-/dev/null}" 2>/dev/null)" in
      job)   echo "rl  report:stale  idle"; echo "    report stale: a background job it started has not finished"; echo "    dir:/x model:sonnet" ;;
      input) echo "rl  report:stale  idle"; echo "    report stale: the lane got input after report.md was written; the report is not its last word"; echo "    dir:/x model:sonnet" ;;
      *)     echo "rl  report:no  idle"; echo "    dir:/x model:sonnet" ;;
    esac ;;
  --ctx) cat "$CTX_FILE" ;;
  --turn) cat "${TURN_FILE:-/dev/null}" ;;
  --recycle-point) cat "${POINT_FILE:-/dev/null}" ;;
  --send) printf '%s\t%s\n' "$2" "$3" >> "${SEND_LOG:-/dev/null}"; exit "${FAKE_SEND_EXIT:-0}" ;;
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
echo 120 > "$POINT_FILE"   # the end state the reminder and wake pieces rely on (test_lane_watch.sh leaves it at 120 too)
rl_dir="$rc_home/.claude/state/lanes"; rl_hand="$rl_dir/2026-10-26-rl"; rl_log="$rl_dir/recycle.log"
ready_pane=$(printf '%s\n' '⏺ handoff written. Ready to recycle.' '✻ Worked for 1m 9s · done 1:09 PM' '❯ ')
log_row() { # log_row <lane> <minutes ago> [verb]
  printf '%s\t%s\t%s\tfrom 120K (session a) to session b, line 128K\n' "$(date -Is -d "-$2 min")" "$1" "${3:-recycled}" >> "$rl_log"
}
rl_reset() { rm -f "$rl_log" "$rl_dir"/.thrash-* "$rl_dir"/.orphan-* "$rl_dir"/.recycle-rl.tried "$rl_hand/handoff.md"; : > "$RC_CALLS"; printf '%s\n' "$idle_pane" > "$RC_PANE"; : > "$TURN_FILE"; }
rl_watch() { # rl_watch <ctx> <seconds> <out>
  echo "$1" > "$CTX_FILE"
  ( HOME="$rc_home" PATH="$TMP/fakebin-rc:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 LANE_RECYCLE_RETRY_SECS=600 \
      timeout "$2" bash "$SCRIPT" --max-min 5 rl > "$3" 2>&1 ) || true
}
job_pane() { printf '%s\n' '⏺ Waiting on CI.' "✻ Worked for 1m 9s · done $(date -d "-$1 min" '+%-I:%M %p')" '❯ '; }
ro_run() { # ro_run <ctx> <pane> ; leaves the output in $ro_out and the exit code in $ro_rc, then waits out the fake's background recycle
  echo "$1" > "$CTX_FILE"; printf '%s\n' "$2" > "$RC_PANE"; : > "$RC_CALLS"
  ro_rc=0; ro_out=$(HOME="$rc_home" PATH="$TMP/fakebin-rc:$PATH" LANE_RECYCLE_RETRY_SECS=600 timeout 30 bash "$SCRIPT" --recycle-once 2>&1) || ro_rc=$?
  sleep 1
}
ro_case() { # ro_case <label> <want calls> ; checks exit 0, no output, calls
  if [ "$ro_rc" = 0 ] && [ -z "$ro_out" ] && [ "$(tr '\n' ' ' < "$RC_CALLS" | sed 's/ $//')" = "$2" ]; then echo "ok   $1"
  else echo "FAIL $1: rc=$ro_rc calls='$(tr '\n' ' ' < "$RC_CALLS")' out=$ro_out"; failures=$((failures + 1)); fi
}
export SEND_LOG="$TMP/send-log"
su_dir="$rl_dir/2026-10-26-rl"; su_rep="$su_dir/report.md"; mkdir -p "$su_dir"
su_watch() { # su_watch <report line 1> <out>: the report appears 1.5 s after the watcher starts
  rl_reset; rm -f "$rl_dir"/.stepurl-*; : > "$SEND_LOG"; echo 50 > "$CTX_FILE"
  ( HOME="$rc_home" PATH="$TMP/fakebin-rc:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 LANE_RECYCLE_RETRY_SECS=600 \
      timeout 8 bash "$SCRIPT" --max-min 5 rl > "$2" 2>&1 ) &
  suw=$!
  sleep 1.5; printf '%s\nbody line two\n' "$1" > "$su_rep"
  wait "$suw" || true
}
su_again() { # su_again <out>: another watcher, the report touched after it starts
  : > "$SEND_LOG"
  ( HOME="$rc_home" PATH="$TMP/fakebin-rc:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 timeout 6 bash "$SCRIPT" --max-min 5 rl > "$1" 2>&1 ) &
  suw=$!; sleep 1.5; touch "$su_rep"; wait "$suw" || true
}
