#!/usr/bin/env bash
# Wake the hub when a lane has something to report, so Jonathan never has to ask for progress.
#
#   lane_watch.sh [--max-min N] [--check 'cmd'] [<lane-name>...]
#
# Exits (printing one line per event) as soon as any of these happens:
#   - openclaw_dispatch.py open lists an unanswered receptionist question, or a new one is filed. A burst of questions wakes the
#     hub ONCE: it waits until no new question has come for LANE_WATCH_BATCH_QUIET seconds (default 60), at most LANE_WATCH_BATCH_MAX
#     (default 180), then prints the whole list. Each wake after the hub's cache has gone cold costs $1.2 to $1.6 against $0.05 warm.
#   - a watched lane's report.md (newest ~/.claude/state/lanes/*-<name>/report.md) is new or changes
#   - a watched lane ends a turn without a new report (see turn_done_time), i.e. it may be asking the hub
#   - a watched lane's tmux window is stuck on a dialog (see dialog_waiting), i.e. it is waiting on the hub
#   - a watched lane's tmux window disappears
#   - a recycle (see below) failed
#   - Jonathan asks from OpenClaw (a new "Jonathan via OpenClaw:" line in hub-inbox.md)
#   - a message of Jonathan's reached no lane and not the hub's list (openclaw_reconcile.py; it files each one with the hub as it wakes)
#   - a post for Jonathan's chat has failed to land for 15 minutes (openclaw_dispatch.py queue check). Every poll also tries the
#     outbox again in the background (queue pump), so a post that failed while Windows could not reach WSL lands once it can.
#   - the WSL-to-Windows bridge cannot be healed or still fails its 10 s probe (wsl_bridge_watchdog.py runs from every poll and raises the alert)
#   - --check 'cmd' exits 0 (an outside condition, e.g. a VOD now returns 404)
#   - --max-min minutes pass (default 45): exits with "heartbeat", so the hub reports even when nothing moved
# Which lanes are watched: the names given, or, with none, EVERY live lane (the rows of `lane.sh --status` that are not dead and
# have a tmux window), read again on every poll so a lane started after the watcher began is covered for reports, end of turn and
# stuck dialogs. (18 Oct: armed with no names it watched nothing, and four reports and a stuck dialog sat unseen for hours.)
# Lanes never auto-compact (his rule, 20 Oct): when a watched lane's context passes LANE_RECYCLE_K (default 120) thousand tokens and its turn
# has ended, the poll starts `lane.sh --recycle <lane>` in the background (the lane writes handoff.md, gets /clear and a re-prime message)
# and logs it to ~/.claude/state/lanes/recycle.log. That wakes nobody; only a FAILED recycle does. A lane is tried again after
# LANE_RECYCLE_RETRY_SECS (default 1800).
# Every new or changed report also goes to Jonathan's chat: the dispatcher's post-reports does it, so he reads it without the hub.
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

# Recycling: a lane past LANE_RECYCLE_K thousand tokens, idle (no spinner, no dialog) and not already being recycled, is handed to
# `lane.sh --recycle` in the background. lane.sh holds .recycle-<lane>.lock while it works; the watcher reads that lock and the log.
lane_sh="${LANE_SH:-$HOME/.claude/scripts/lane.sh}"
recycle_k=${LANE_RECYCLE_K:-120}
recycle_dir="${LANE_RECYCLE_DIR:-$HOME/.claude/state/lanes}"
recycle_log="$recycle_dir/recycle.log"
recycle_retry=${LANE_RECYCLE_RETRY_SECS:-1800}
recycle_locked() { [ -d "$recycle_dir/.recycle-$1.lock" ]; }
# recycle_due <lane>, the lane's pane on stdin: 0 when its context is at or over the line and its turn has ended.
recycle_due() {
  local l=$1 pane c; pane=$(cat)
  recycle_locked "$l" && return 1
  command grep -aqP '…\s*\(\d+(m \d+)?s' <<<"$pane" && return 1
  printf '%s\n' "$pane" | dialog_waiting && return 1
  c=$("$lane_sh" --ctx "$l" 2>/dev/null)
  [[ "$c" =~ ^[0-9]+$ ]] && [ "$c" -ge "$recycle_k" ]
}
# New "FAILED" rows in the recycle log since the last look (byte offset in $recycle_log_seen).
recycle_log_seen=$(stat -c %s "$recycle_log" 2>/dev/null || echo 0)
# Sets recycle_fail_out (one "lane: reason" per line); called without a subshell so the offset sticks.
recycle_failures() {
  local size; recycle_fail_out=''
  size=$(stat -c %s "$recycle_log" 2>/dev/null) || return 0
  [ "$size" -gt "$recycle_log_seen" ] || { recycle_log_seen=$size; return 0; }
  recycle_fail_out=$(tail -c +"$((recycle_log_seen + 1))" "$recycle_log" | command grep -a $'\tFAILED\t' | awk -F'\t' '{print $2 ": " $4}')
  recycle_log_seen=$size
}

# The WSL-to-Windows bridge alert (wsl_bridge_watchdog.py writes it when a heal did not bring the big free memory blocks back or the
# 10 s cmd.exe probe still fails). Prints its text once per change of the file, nothing otherwise. 19 Oct: the bridge went quiet twice
# (5:21 PM and 8:15 PM) with nothing to tell the hub.
bridge_state="${WSL_BRIDGE_STATE:-$HOME/.claude/state/wsl-bridge}"
bridge_alert() {
  local m
  m=$(stat -c %Y "$bridge_state/alert" 2>/dev/null) || return 0
  [ "$m" = "$(cat "$bridge_state/alert.seen" 2>/dev/null)" ] && return 0
  printf '%s' "$m" > "$bridge_state/alert.seen"
  cat "$bridge_state/alert"
}

# When sourced (by the test script), stop here with the functions defined.
# "report stale: ..." when lane.sh --status says the lane's report.md is not its last word (input arrived after it, or a background job is
# still running), else nothing. 19 Oct: a lane was closed on "report:yes idle" while it was mid-work.
stale_note() {
  "$HOME/.claude/scripts/lane.sh" --status 2>/dev/null | awk -v n="$1" '$1 == n && $2 == "report:stale" {f = 1; next} f && /report stale:/ {sub(/^[[:space:]]+/, ""); print; exit} f && !/^[[:space:]]/ {exit}'
}
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
auto=0; [ ${#lanes[@]} -eq 0 ] && auto=1
start_ts=$(date +%s)
declare -A seen seen_turn recycling recycle_try
report_of() { ls -t "$HOME"/.claude/state/lanes/*-"$1"/report.md 2>/dev/null | head -1; }
stamp() { local f; f=$(report_of "$1"); [ -n "$f" ] && stat -c %Y "$f" || echo none; }
watch_lane() { # watch_lane <name> [fresh]: remember its report stamp and last finished turn; fresh = a lane that began after the watcher
  local l=$1 f
  seen[$l]=$(stamp "$l")
  if [ "${2:-}" = fresh ] && f=$(report_of "$l") && [ -n "$f" ] && [ "$(stat -c %Y "$f")" -ge "$start_ts" ]; then
    seen[$l]=none   # its report was written before the first poll saw the lane: still news
  fi
  seen_turn[$l]=$(tmux capture-pane -p -t "lanes:$l" 2>/dev/null | turn_done_line)
}
# The live lanes now: rows of lane.sh --status that start in column one and are not "dead", and that have a tmux window.
live_lanes() {
  local wins; wins=$(tmux list-windows -t lanes -F '#{window_name}' 2>/dev/null) || return 0
  "$HOME/.claude/scripts/lane.sh" --status 2>/dev/null | awk '!/^[[:space:]]/ && NF >= 2 && $2 !~ /^dead/ {print $1}' |
    while read -r n; do printf '%s\n' "$wins" | grep -qx "$n" && echo "$n"; done
}
refresh_watched() { # no names given: add the lanes that started since the last poll
  [ "$auto" = 1 ] || return 0
  local n known w
  while read -r n; do
    [ -n "$n" ] || continue
    known=0; for w in "${lanes[@]}"; do [ "$w" = "$n" ] && { known=1; break; }; done
    [ "$known" = 1 ] || { lanes+=("$n"); watch_lane "$n" fresh; }
  done < <(live_lanes)
}
if [ "$auto" = 1 ]; then
  lanes=()
  while read -r n; do [ -n "$n" ] && { lanes+=("$n"); watch_lane "$n"; }; done < <(live_lanes)
else
  for l in "${lanes[@]}"; do watch_lane "$l"; done
fi
# Jonathan asking the hub from OpenClaw: openclaw_pickup.py --act touches this file when it files a "Jonathan via OpenClaw:" line
# in hub-inbox.md. It holds only a time and an id, never the message.
ask_wake="$HOME/.claude/state/openclaw-ask.wake"
ask_stamp() { stat -c %Y "$ask_wake" 2>/dev/null || echo none; }
ask_seen=$(ask_stamp)
dispatch="${OPENCLAW_DISPATCH:-$HOME/.claude/scripts/openclaw_dispatch.py}"
batch_quiet=${LANE_WATCH_BATCH_QUIET:-60}; batch_cap=${LANE_WATCH_BATCH_MAX:-180}; batch_poll=${LANE_WATCH_BATCH_POLL:-5}
wake_age() {
  local m; m=$(stat -c %Y "$ask_wake" 2>/dev/null) || { echo 999999; return; }
  echo $(( $(date +%s) - m ))
}
# Hold while questions are still arriving (the wake file is touched by each one), so a burst is one wake.
hold_burst() {
  local t0; t0=$(date +%s)
  while [ "$(wake_age)" -lt "$batch_quiet" ] && [ $(( $(date +%s) - t0 )) -lt "$batch_cap" ]; do sleep "$batch_poll"; done
}
check_open_questions() {
  local questions fresh
  if questions=$(python3 "$dispatch" open 2>/dev/null) && [ "$questions" != none ]; then
    hold_burst
    fresh=$(python3 "$dispatch" open 2>/dev/null) && [ "$fresh" != none ] && questions=$fresh  # the burst's later questions too
    echo "ask: open receptionist questions:"
    printf '%s\n' "$questions"
    exit 0
  fi
}
# A message of his that reached nobody: the reconciler puts it on the hub's list (so the next wake sees it as an open question, not again
# as a drop) and the hub wakes with the list. 19 Oct 3:34-3:57 PM: six of his messages went nowhere and nothing woke anyone.
reconcile="${OPENCLAW_RECONCILE:-$(dirname "$dispatch")/openclaw_reconcile.py}"
check_undelivered() {
  local u
  u=$(timeout 60 python3 "$reconcile" --file --quiet 2>/dev/null | grep -E '^(undelivered \||filed with the hub:)') || return 0
  echo "undelivered: messages of Jonathan's that reached no lane and not the hub (now on the hub's list):"
  printf '%s\n' "$u"
  exit 0
}
# The outbox (everything the hub sends to his chat) is retried from here as well as from the every-minute poller, because the poller is
# started by Windows and stops when Windows cannot reach WSL (19 Oct from 5:21 PM). The pump runs in the background: a post that is
# failing can take minutes, and the watch must not stop for it.
outbox="${OPENCLAW_OUTBOX:-$(dirname "$dispatch")/openclaw_dispatch.py}"
check_outbox() {
  local w
  [ -f "$outbox" ] || return 0
  setsid nohup timeout 600 python3 "$outbox" queue pump >/dev/null 2>&1 &
  w=$(timeout 60 python3 "$outbox" queue check 2>/dev/null | grep -E '^outbox: ') || return 0
  printf '%s\n' "$w"
  exit 0
}
# The bridge watchdog runs from every poll, detached (a heal can take a minute and the watch must not stop for it; it takes its own lock
# and heals at most once in five minutes). When it raises the alert the hub wakes.
watchdog="${WSL_BRIDGE_WATCHDOG:-$(dirname "$(readlink -f "$0")")/wsl_bridge_watchdog.py}"
check_bridge() {
  local w
  [ -f "$watchdog" ] && { setsid nohup timeout 300 python3 "$watchdog" >/dev/null 2>&1 & }
  w=$(bridge_alert)
  [ -n "$w" ] || return 0
  echo "bridge: $w"
  exit 0
}
check_bridge
check_outbox
check_undelivered
check_open_questions
end=$(( $(date +%s) + max * 60 ))
while :; do
  check_bridge
  check_outbox
  check_undelivered
  check_open_questions
  refresh_watched
  hit=0; other=0
  for l in "${lanes[@]}"; do
    s=$(stamp "$l")
    if [ "$s" != "${seen[$l]}" ]; then
      note=$(stale_note "$l")
      echo "report: $l ($(report_of "$l"))${note:+ -- $note; do not close it} -- a report does not end a lane; keep it open (it recycles itself at about ${recycle_k}K)"; hit=1; other=1
      # a report changes what is in flight: refresh OpenClaw's status digest, detached so the exit is not held up
      setsid nohup timeout 90 "$HOME/.claude/scripts/openclaw_status_refresh.sh" >/dev/null 2>&1 &
      # and put the report itself into Jonathan's chat, after a short settle so a report still being written is posted whole
      setsid nohup bash -c 'sleep "$1"; timeout 120 python3 "$2" post-reports' _ "${LANE_WATCH_SETTLE:-12}" "$dispatch" >/dev/null 2>&1 &
    fi
    if ! tmux list-windows -t lanes -F '#{window_name}' 2>/dev/null | grep -qx "$l"; then
      echo "gone: $l"; hit=1; other=1
    else
      pane=$(tmux capture-pane -p -t "lanes:$l" 2>/dev/null)
      if printf '%s\n' "$pane" | dialog_waiting; then
        echo "waiting on a dialog: $l"; hit=1; other=1
      fi
      done_line=$(printf '%s\n' "$pane" | turn_done_line)
      if recycle_locked "$l"; then
        recycling[$l]=1; seen_turn[$l]=$done_line   # the hand-off turn is ours, not news
      else
        if [ "${recycling[$l]:-}" = 1 ]; then recycling[$l]=0; seen_turn[$l]=$done_line; fi
        if [ -n "$done_line" ] && [ "$done_line" != "${seen_turn[$l]}" ]; then
          done_time=$(printf '%s\n' "$done_line" | sed -nE 's/.*· done ([0-9]{1,2}:[0-9]{2}).*/\1/p')
          echo "idle: $l ended a turn ($done_time) without a new report; it may be asking the hub"; hit=1; other=1
        elif [ $(( $(date +%s) - ${recycle_try[$l]:-0} )) -ge "$recycle_retry" ] && printf '%s\n' "$pane" | recycle_due "$l"; then
          recycle_try[$l]=$(date +%s)
          setsid nohup timeout 1500 "$lane_sh" --recycle "$l" >>"$recycle_dir/recycle.out" 2>&1 &
        fi
      fi
    fi
  done
  recycle_failures
  while read -r f; do [ -n "$f" ] && { echo "recycle failed: $f (the lane keeps its context; recycle it by hand with lane.sh --recycle)"; hit=1; other=1; }; done <<<"$recycle_fail_out"
  a=$(ask_stamp)
  if [ "$a" != "$ask_seen" ]; then
    echo "ask: a new 'Jonathan via OpenClaw:' line is in hub-inbox.md ($(head -c 60 "$ask_wake" 2>/dev/null))"; ask_seen=$a; hit=1
  fi
  if [ -n "$check" ] && bash -c "$check" >/dev/null 2>&1; then echo "check passed: $check"; hit=1; other=1; fi
  if [ $hit = 1 ]; then
    if [ $other = 0 ]; then  # only a new question: let the rest of its burst arrive, then print the whole list
      hold_burst
      if fresh=$(python3 "$dispatch" open 2>/dev/null) && [ "$fresh" != none ]; then
        echo "ask: open receptionist questions:"; printf '%s\n' "$fresh"
      fi
    fi
    exit 0
  fi
  [ "$(date +%s)" -ge "$end" ] && { echo "heartbeat: nothing new in ${max} min"; exit 0; }
  sleep "${LANE_WATCH_POLL:-30}"
done
