#!/usr/bin/env bash
# Tests for the "hub wakes that do nothing" fixes in lane_watch.sh (31 Oct 2026, the second PC's note): a turn end right after a report,
# a routine HOURLY/STATUS report, a report that lands between a wake and the re-arm, a dead watcher, and the longer idle heartbeat.
set -euo pipefail
export AFA_ENGINES_AVAILABLE="*"

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane_watch.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-watch-quiet.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
export WSL_BRIDGE_WATCHDOG="$TMP/fake-watchdog.py" WSL_BRIDGE_STATE="$TMP/bridge-state"
printf 'pass\n' > "$WSL_BRIDGE_WATCHDOG"
failures=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; failures=$((failures + 1)); }

# A fake home with one live lane, q, whose pane we control.
H="$TMP/home"; LANES="$H/.claude/state/lanes"; RDIR="$LANES/2026-10-31-q"
mkdir -p "$H/.claude/scripts" "$RDIR" "$TMP/bin"
cat > "$H/.claude/scripts/lane.sh" <<'SH'
#!/usr/bin/env bash
case "$1" in
  --status) echo "q  report:no  idle"; echo "    dir:/x model:sonnet" ;;
  --ctx) echo 10 ;;
  --turn) : ;;
  --recycle-point) echo 120 ;;
esac
SH
chmod +x "$H/.claude/scripts/lane.sh"
cat > "$TMP/bin/tmux" <<'SH'
#!/usr/bin/env bash
case "$1" in
  list-windows) echo q ;;
  capture-pane) cat "$PANE" ;;
esac
SH
chmod +x "$TMP/bin/tmux"
printf 'import sys\nprint("none")\n' > "$TMP/dispatch.py"
export PANE="$TMP/pane" HUB_INBOX="$TMP/hub-inbox.md"
: > "$HUB_INBOX"
pane_done() { printf '%s\n' '⏺ ok' "✻ Worked for 1m 0s · done $1" '❯ ' > "$PANE"; }

# watch <seconds> <max-min> [env...]: run the watcher on lane q; prints its output.
watch() {
  local secs=$1 mx=$2; shift 2
  env HOME="$H" PATH="$TMP/bin:$PATH" OPENCLAW_DISPATCH="$TMP/dispatch.py" OPENCLAW_RECONCILE=/nonexistent LANE_WATCH_POLL=1 LANE_WATCH_SETTLE=0 \
    LANE_WATCH_IDLE_MAX_MIN=0 "$@" timeout "$secs" bash "$SCRIPT" --max-min "$mx" q 2>&1 || true
}
reset() { rm -f "$RDIR/report.md" "$LANES"/.reportseen-q "$LANES"/.recycle-q.* ; : > "$HUB_INBOX"; }

# 1. The turn that wrote the report ends after the hub was shown the report: silent. Past the grace, it wakes.
reset; pane_done "1:05 PM"; echo "DONE all fixed" > "$RDIR/report.md"; touch -d '10 seconds ago' "$RDIR/report.md"
( sleep 2; pane_done "1:09 PM" ) &
out=$(watch 7 5); wait
if ! printf '%s\n' "$out" | grep -q '^idle:'; then ok "a turn ending right after a report wakes nobody"; else bad "turn end after report woke the hub: $out"; fi
reset; pane_done "1:05 PM"; echo "DONE all fixed" > "$RDIR/report.md"; touch -d '20 minutes ago' "$RDIR/report.md"
( sleep 2; pane_done "1:09 PM" ) &
out=$(watch 7 5); wait
if printf '%s\n' "$out" | grep -q '^idle: q ended a turn (1:09)'; then ok "a turn ending long after the report still wakes the hub"; else bad "late turn end stayed silent: $out"; fi
reset; pane_done "1:05 PM"
( sleep 2; pane_done "1:09 PM" ) &
out=$(watch 7 5); wait
if printf '%s\n' "$out" | grep -q '^idle: q ended a turn'; then ok "a turn end with no report at all still wakes the hub"; else bad "turn end with no report stayed silent: $out"; fi

# 2. A report whose line 1 starts HOURLY or STATUS is routine and wakes nobody; anything else wakes.
for first in "HOURLY 3:00 PM all well" "STATUS scanning 40 of 90"; do
  reset; pane_done "1:05 PM"
  ( sleep 2; printf '%s\nmore\n' "$first" > "$RDIR/report.md" ) &
  out=$(watch 6 5); wait
  if ! printf '%s\n' "$out" | grep -q '^report:'; then ok "report line 1 '${first%% *}' is routine and wakes nobody"; else bad "routine report woke the hub: $out"; fi
done
reset; pane_done "1:05 PM"
( sleep 2; printf 'DONE shipped\n' > "$RDIR/report.md" ) &
out=$(watch 6 5); wait
if printf '%s\n' "$out" | grep -q '^report: q '; then ok "a DONE report wakes the hub"; else bad "DONE report did not wake: $out"; fi
if [ -f "$LANES/.reportseen-q" ]; then ok "a shown report writes the seen stamp"; else bad "no seen stamp written"; fi
reset; pane_done "1:05 PM"
( sleep 2; printf 'HOURLY ok\n' > "$RDIR/report.md" ) &
out=$(watch 6 5); wait
( sleep 2; printf 'BLOCKED need a decision\n' > "$RDIR/report.md" ) &
out=$(watch 6 5); wait
if printf '%s\n' "$out" | grep -q '^report: q '; then ok "a routine report followed by a BLOCKED one still wakes"; else bad "BLOCKED after HOURLY missed: $out"; fi

# 3. A report that landed between the last wake and the next watcher's start is still news, through the seen stamp.
reset; pane_done "1:05 PM"; echo "DONE a" > "$RDIR/report.md"
old=$(( $(stat -c %Y "$RDIR/report.md") - 100 )); printf '%s' "$old" > "$LANES/.reportseen-q"
out=$(watch 4 5)
if printf '%s\n' "$out" | grep -q '^report: q '; then ok "a report newer than the hub's last-seen stamp wakes the new watcher"; else bad "gap report missed: $out"; fi
reset; pane_done "1:05 PM"; echo "DONE a" > "$RDIR/report.md"
printf '%s' "$old" > "$LANES/.reportseen-q"; touch -d '2 hours ago' "$LANES/.reportseen-q"
out=$(watch 4 5)
if ! printf '%s\n' "$out" | grep -q '^report:'; then ok "a stale seen stamp (2 hours) is ignored"; else bad "stale stamp used: $out"; fi
reset; pane_done "1:05 PM"; echo "DONE a" > "$RDIR/report.md"
printf '%s' "$(stat -c %Y "$RDIR/report.md")" > "$LANES/.reportseen-q"
out=$(watch 4 5)
if ! printf '%s\n' "$out" | grep -q '^report:'; then ok "a report the hub was already shown does not wake the next watcher"; else bad "shown report woke again: $out"; fi

# 4. Dead-watcher check (hub_watcher_check), called by the 15-minute pass.
( source "$SCRIPT"
  recycle_dir="$TMP/wd"; mkdir -p "$recycle_dir"
  export LANE_WATCH_RECORD="$TMP/rec.sh" LANE_WATCH_TRAY="$TMP/tray.py" LANE_WATCH_DEAD_SECS=600
  printf '#!/usr/bin/env bash\necho "$*" >> "%s/rec.log"\n' "$TMP" > "$TMP/rec.sh"; chmod +x "$TMP/rec.sh"
  cat > "$TMP/tray.py" <<PY
import sys
open("$TMP/tray.log", "a").write(" ".join(sys.argv[1:]) + "\\n")
PY
  : > "$TMP/rec.log"; : > "$TMP/tray.log"
  chk() { # chk <label> <want inbox lines> <want pings>
    local a b; a=$(wc -l < "$TMP/rec.log"); b=$(wc -l < "$TMP/tray.log")
    if [ "$a" = "$2" ] && [ "$b" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: inbox lines $a (want $2), pings $b (want $3)"; exit 1; fi
  }
  export LANE_WATCH_PROCLIST=""
  hub_watcher_check 0; chk "no lane open: nothing said" 0 0
  hub_watcher_check 3; chk "lanes open, no watcher, first pass: only marked" 0 0
  [ -e "$recycle_dir/.no-watcher" ] || { echo "FAIL no mark written"; exit 1; }
  printf '%s' "$(( $(date +%s) - 700 ))" > "$recycle_dir/.no-watcher"
  hub_watcher_check 3; chk "no watcher for over 10 minutes: one inbox line and one ping" 1 1
  grep -q '^inbox Hub: no lane_watch.sh watcher' "$TMP/rec.log" || { echo "FAIL inbox line text: $(cat "$TMP/rec.log")"; exit 1; }
  hub_watcher_check 3; chk "... and the same gap is named only once" 1 1
  export LANE_WATCH_PROCLIST="1234 /usr/bin/bash /home/x/.claude/scripts/lane_watch.sh --max-min 45"
  hub_watcher_check 3; chk "a running watcher clears the alarm" 1 1
  [ ! -e "$recycle_dir/.no-watcher" ] && [ ! -e "$recycle_dir/.no-watcher-named" ] || { echo "FAIL marks not cleared"; exit 1; }
  export LANE_WATCH_PROCLIST="1235 bash /home/x/.claude/scripts/lane_watch.sh --recycle-once"
  hub_watcher_check 3; printf '%s' "$(( $(date +%s) - 700 ))" > "$recycle_dir/.no-watcher"; hub_watcher_check 3
  chk "the 15-minute --recycle-once pass does not count as a watcher; a second gap is named again" 2 2
) > "$TMP/wd.out" 2>&1 || true
cat "$TMP/wd.out"; if grep -q '^FAIL' "$TMP/wd.out" || ! grep -q 'second gap is named again' "$TMP/wd.out"; then failures=$((failures + 1)); fi

# 5. Longer heartbeat while every lane is idle and no client line is open.
pane_done "1:05 PM"; reset
out=$(watch 5 0 LANE_WATCH_IDLE_MAX_MIN=1)
if [ -z "$out" ]; then ok "idle fleet: the heartbeat is stretched past --max-min"; else bad "idle heartbeat not stretched: $out"; fi
printf '%s\n' '⏺ Reading' '✻ Thinking… (12s · esc to interrupt)' '❯ ' > "$PANE"
out=$(watch 5 0 LANE_WATCH_IDLE_MAX_MIN=1)
if printf '%s\n' "$out" | grep -q '^heartbeat: nothing new in 0 min'; then ok "a working lane keeps the plain heartbeat"; else bad "busy lane stretched the heartbeat: $out"; fi
pane_done "1:05 PM"
echo '- [ ] 2026-10-31 | Client mail: Gail wrote' > "$HUB_INBOX"
out=$(watch 5 0 LANE_WATCH_IDLE_MAX_MIN=1)
if printf '%s\n' "$out" | grep -q '^heartbeat: nothing new in 0 min'; then ok "an open client line keeps the plain heartbeat"; else bad "client line ignored: $out"; fi
: > "$HUB_INBOX"
out=$(watch 5 0 LANE_WATCH_IDLE_MAX_MIN=0)
if printf '%s\n' "$out" | grep -q '^heartbeat: nothing new in 0 min'; then ok "LANE_WATCH_IDLE_MAX_MIN=0 turns the stretch off"; else bad "stretch not switchable: $out"; fi

[ "$failures" = 0 ] || { echo "$failures failed"; exit 1; }
echo "all lane_watch quiet tests passed"
