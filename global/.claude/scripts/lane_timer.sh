#!/usr/bin/env bash
# lane_timer.sh <name> '<YYYY-MM-DD HH:MM[:SS]>' [--log <path>] -- <command...>
# Loops sleep 30 until target epoch, runs detached with setsid, logs to timer.log next to --log path.
# Refuses a second timer with the same name.

set -euo pipefail

usage() {
    echo "usage: lane_timer.sh <name> '<YYYY-MM-DD HH:MM[:SS]>' [--log <path>] -- <command...>" >&2
    echo "       lane_timer.sh --list" >&2
    echo "       lane_timer.sh --cancel <name>" >&2
    exit 1
}

TIMER_DIR="${LANE_TIMER_DIR:-$HOME/.claude/state/lane-timers}"
mkdir -p "$TIMER_DIR"

list_timers() {
    for f in "$TIMER_DIR"/*.pid; do
        [ -e "$f" ] || continue
        name=$(basename "$f" .pid)
        pid=$(cat "$f" 2>/dev/null || echo "?")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$name: running (pid $pid)"
        else
            echo "$name: done (pid $pid)"
        fi
    done
}

cancel_timer() {
    local name="$1"
    local pidfile="$TIMER_DIR/$name.pid"
    [ -f "$pidfile" ] || { echo "no timer named $name" >&2; exit 1; }
    local pid; pid=$(cat "$pidfile")
    kill "$pid" 2>/dev/null && echo "killed $name (pid $pid)" || echo "$name (pid $pid) already dead"
    rm -f "$pidfile" "$TIMER_DIR/$name.log"
}

if [ $# -eq 0 ]; then usage; fi

case "$1" in
    --list) list_timers; exit 0 ;;
    --cancel)
        [ $# -eq 2 ] || usage
        cancel_timer "$2"
        exit 0
        ;;
esac

# Parse: name, target_str, [--log path], --, command...
[ $# -ge 4 ] || usage
name="$1"; shift
target_str="$1"; shift

# Parse optional --log
log_path="$TIMER_DIR/$name.log"
if [ "${1:-}" = "--log" ]; then
    shift
    log_path="$1"
    shift
    # If log_path is a directory, use timer.log inside it
    if [ -d "$log_path" ]; then
        log_path="$log_path/timer.log"
    fi
fi

[ "${1:-}" = "--" ] || usage; shift
cmd=("$@")
[ ${#cmd[@]} -gt 0 ] || usage

# Parse target time (accepts HH:MM or HH:MM:SS)
target_epoch=$(date -d "$target_str" +%s 2>/dev/null) || { echo "invalid time format: $target_str (use 'YYYY-MM-DD HH:MM[:SS]')" >&2; exit 1; }
now_epoch=$(date +%s)
[ "$target_epoch" -gt "$now_epoch" ] || { echo "target time is in the past" >&2; exit 1; }

# Check for existing timer (by pid file existence, not just live pid)
pidfile="$TIMER_DIR/$name.pid"
if [ -f "$pidfile" ]; then
    existing_pid=$(cat "$pidfile" 2>/dev/null || echo "")
    if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
        echo "timer '$name' already running (pid $existing_pid)" >&2
    else
        echo "timer '$name' already exists (pid $existing_pid), use --cancel to reuse" >&2
    fi
    exit 1
fi

# Run timer in background with setsid
(
    # Re-parse target in case of DST changes (not critical for short timers)
    target=$(date -d "$target_str" +%s)
    while true; do
        now=$(date +%s)
        remaining=$((target - now))
        if [ "$remaining" -le 0 ]; then
            break
        fi
        # Sleep in 30s chunks
        if [ "$remaining" -gt 30 ]; then
            sleep 30
        else
            sleep "$remaining"
        fi
    done
    # Time reached, run command
    exec setsid "${cmd[@]}" >>"$log_path" 2>&1
) &
timer_pid=$!

# Save pid
echo "$timer_pid" >"$pidfile"
echo "timer '$name' started (pid $timer_pid), target: $target_str, log: $log_path"
