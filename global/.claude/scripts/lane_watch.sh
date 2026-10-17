#!/usr/bin/env bash
# Wake the hub when a lane has something to report, so Jonathan never has to ask for progress.
#
#   lane_watch.sh [--max-min N] [--check 'cmd'] [<lane-name>...]
#
# Exits (printing one line per event) as soon as any of these happens:
#   - a named lane's report.md (newest ~/.claude/state/lanes/*-<name>/report.md) is new or changes
#   - a named lane ends a turn without a new report (see turn_done_time), i.e. it may be asking the hub
#   - a named lane's tmux window is stuck on a dialog (see dialog_waiting), i.e. it is waiting on the hub
#   - a named lane's tmux window disappears
#   - Jonathan asks from OpenClaw (a new "Jonathan via OpenClaw:" line in muse-inbox.md)
#   - --check 'cmd' exits 0 (an outside condition, e.g. a VOD now returns 404)
#   - --max-min minutes pass (default 45): exits with "heartbeat", so the hub reports even when nothing moved
# With no lane names it still wakes on the OpenClaw ask, --check and the heartbeat.
# Run it with the Bash tool's run_in_background; its exit re-invokes the hub, which reports and re-arms it.
set -u

# Does this pane text show a dialog that waits on the hub? Reads the pane on stdin.
# Claude Code menus say "Enter to select"; its permission prompts say "Do you want to proceed"
# and end with "Esc to cancel". The last 15 lines are checked, so a prompt with options below it still matches.
dialog_waiting() {
  tail -15 | grep -qE 'Enter to select|Do you want to proceed|Esc to cancel'
}

# Return the last completed-turn line visible in this pane.
turn_done_line() {
  tail -15 | sed -nE '/· done [0-9]{1,2}:[0-9]{2}/p' | tail -1
}

turn_done_time() {
  turn_done_line | sed -nE 's/.*· done ([0-9]{1,2}:[0-9]{2}).*/\1/p'
}

# When sourced (by the test script), stop here with the functions defined.
[[ "${BASH_SOURCE[0]}" != "$0" ]] && return 0

max=45; check=''
while [ $# -gt 0 ]; do
  case "$1" in
    --max-min) max=$2; shift 2 ;;
    --check) check=$2; shift 2 ;;
    *) break ;;
  esac
done
lanes=("$@")
declare -A seen seen_turn
report_of() { ls -t "$HOME"/.claude/state/lanes/*-"$1"/report.md 2>/dev/null | head -1; }
stamp() { local f; f=$(report_of "$1"); [ -n "$f" ] && stat -c %Y "$f" || echo none; }
for l in "${lanes[@]}"; do
  seen[$l]=$(stamp "$l")
  seen_turn[$l]=$(tmux capture-pane -p -t "lanes:$l" 2>/dev/null | turn_done_line)
done
# Jonathan asking the hub from OpenClaw: openclaw_pickup.py --act touches this file when it files a "Jonathan via OpenClaw:" line
# in muse-inbox.md. It holds only a time and an id, never the message.
ask_wake="$HOME/.claude/state/openclaw-ask.wake"
ask_stamp() { stat -c %Y "$ask_wake" 2>/dev/null || echo none; }
ask_seen=$(ask_stamp)
end=$(( $(date +%s) + max * 60 ))
while :; do
  hit=0
  for l in "${lanes[@]}"; do
    s=$(stamp "$l")
    if [ "$s" != "${seen[$l]}" ]; then
      echo "report: $l ($(report_of "$l"))"; hit=1
      # a report changes what is in flight: refresh OpenClaw's status digest, detached so the exit is not held up
      setsid nohup timeout 90 "$HOME/.claude/scripts/openclaw_status_refresh.sh" >/dev/null 2>&1 &
    fi
    if ! tmux list-windows -t lanes -F '#{window_name}' 2>/dev/null | grep -qx "$l"; then
      echo "gone: $l"; hit=1
    else
      pane=$(tmux capture-pane -p -t "lanes:$l" 2>/dev/null)
      if printf '%s\n' "$pane" | dialog_waiting; then
        echo "waiting on a dialog: $l"; hit=1
      fi
      done_line=$(printf '%s\n' "$pane" | turn_done_line)
      if [ -n "$done_line" ] && [ "$done_line" != "${seen_turn[$l]}" ]; then
        done_time=$(printf '%s\n' "$done_line" | sed -nE 's/.*· done ([0-9]{1,2}:[0-9]{2}).*/\1/p')
        echo "idle: $l ended a turn ($done_time) without a new report; it may be asking the hub"; hit=1
      fi
    fi
  done
  a=$(ask_stamp)
  if [ "$a" != "$ask_seen" ]; then
    echo "ask: a new 'Jonathan via OpenClaw:' line is in muse-inbox.md ($(head -c 60 "$ask_wake" 2>/dev/null))"; ask_seen=$a; hit=1
  fi
  if [ -n "$check" ] && bash -c "$check" >/dev/null 2>&1; then echo "check passed: $check"; hit=1; fi
  [ $hit = 1 ] && exit 0
  [ "$(date +%s)" -ge "$end" ] && { echo "heartbeat: nothing new in ${max} min"; exit 0; }
  sleep 30
done
