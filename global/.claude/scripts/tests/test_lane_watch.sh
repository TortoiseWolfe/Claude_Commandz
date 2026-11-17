#!/usr/bin/env bash
# Tests for lane_watch.sh, part 1 of 3: the stuck-dialog match, no lane names, the burst wake, auto-watch, recycle and thrash. Parts 2 and 3: test_lane_watch_reminders.sh, test_lane_watch_wakes.sh; shared setup: lane_watch_common.sh.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
. "$(dirname "${BASH_SOURCE[0]}")/lane_watch_common.sh"
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

expect_done_line() { # expect_done_line <expected line> <label> <pane text>
  local want=$1 label=$2 got
  got=$(printf '%s\n' "$3" | turn_done_line)
  if [ "$got" = "$want" ]; then echo "ok   $label"; else echo "FAIL $label: want '$want', got '$got'"; failures=$((failures + 1)); fi
}

baked='✻ Baked for 7s · done 4:58 PM'
expect_done_line "$baked" "done line drops the monitor count" "$(printf '%s\n' "$baked · 5 monitors still running")"
expect_done_line "$baked" "done line is the same with 4 monitors" "$(printf '%s\n' "$baked · 4 monitors still running")"
expect_done_line "$baked" "done line is the same with 3 shells" "$(printf '%s\n' "$baked · 3 shells still running")"
expect_done_line "$baked" "done line is the same with no count" "$(printf '%s\n' "$baked")"
expect_done_line "$baked" "trailing spaces and a box edge are dropped" "$(printf '%s\n' "$baked · 5 monitors still running   │")"
expect_done_line "$baked" "box edge straight after the time is dropped" "$(printf '%s\n' "$baked   │")"
crunched='✻ Crunched for 9s · done 4:58 PM'
if [ "$(printf '%s\n' "$crunched" | turn_done_line)" != "$(printf '%s\n' "$baked" | turn_done_line)" ]; then
  echo "ok   two turns ending in the same minute differ"
else echo "FAIL two turns ending in the same minute are equal"; failures=$((failures + 1)); fi
for v in "$baked · 5 monitors still running" "$baked · 3 shells still running   │" "$crunched" "$baked   │"; do
  expect_done_time "4:58" "done time is 4:58 for: $v" "$v"
done

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

# The "Read outside the working directories" dialog: the watcher names the path the lane is waiting on.
expect_read_path() { # expect_read_path <expected path, or NONE for "not this dialog"> <label> <pane text>
  local want=$1 label=$2 got rc=0
  got=$(printf '%s\n' "$3" | dialog_read_path) || rc=$?
  if [ "$want" = NONE ]; then
    if [ "$rc" = 1 ] && [ -z "$got" ]; then echo "ok   $label"; else echo "FAIL $label: want rc 1 and no output, got rc $rc '$got'"; failures=$((failures + 1)); fi
  elif [ "$rc" = 0 ] && [ "$got" = "$want" ]; then echo "ok   $label"
  else echo "FAIL $label: want '$want', got rc $rc '$got'"; failures=$((failures + 1)); fi
}
read_dialog=$(printf '%s\n' 'Read outside the working directories' '' '  Read(/home/u/x/ep7/a.txt)' '' \
  'Do you want to proceed?' '❯ 1. Yes' '  2. Yes, allow reading from ep7/ during this session' '  4. Yes, but ask again next time' '  3. No' 'Esc to cancel')
expect_read_path /home/u/x/ep7/a.txt "read dialog names the path in Read( )" "$read_dialog"
bare_dialog=$(printf '%s\n' 'Read outside the working directories' '  /home/u/x/ep7/b.txt' 'Do you want to proceed?' '❯ 1. Yes' 'Esc to cancel')
expect_read_path /home/u/x/ep7/b.txt "read dialog with a bare path line names that path" "$bare_dialog"
nopath_dialog=$(printf '%s\n' 'Read outside the working directories' 'Do you want to proceed?' '❯ 1. Yes' 'Esc to cancel')
expect_read_path "a file outside its working directories" "read dialog with no path says so" "$nopath_dialog"
expect_read_path NONE "a Bash permission prompt is not the read dialog" "$permission_prompt"
expect_read_path NONE "a working screen is not the read dialog" "$working"

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

# A fresh client-tag wake files a hub nudge; client talk also prevents stretching the heartbeat.
talk_home="$TMP/talk-home"; mkdir -p "$talk_home/.claude/state"
talk_dispatch="$TMP/talk-dispatch.py"
cat > "$talk_dispatch" <<'PY'
import os
from pathlib import Path
Path(os.environ["TALK_WAKE"]).write_text("2026-11-01T10:00:00-05:00\ntalk:room_test:1142\n")
print("none")
PY
rc=0; out=$(HOME="$talk_home" OPENCLAW_DISPATCH="$talk_dispatch" TALK_WAKE="$talk_home/.claude/state/client-tag.wake" \
      bash "$SCRIPT" --max-min 0) || rc=$?
if [ "$rc" = 0 ] && printf '%s\n' "$out" | grep -q '^client talk: a co-op chat message tags TurtleWolfe; read "Open client lines"'; then
  echo "ok   a client-tag wake tells the hub to read open client lines"
else
  echo "FAIL client-tag wake: rc=$rc out=$out"; failures=$((failures + 1))
fi
printf '%s\n' '- [ ] 2026-11-01 10:00 | Client talk: Synthetic co-op | TAG greg, Ops: "hello" | talk:room_test:1142' > "$talk_home/.claude/state/hub-inbox.md"
if HOME="$talk_home" HUB_INBOX="$talk_home/.claude/state/hub-inbox.md" client_line_open; then
  echo "ok   an open client-talk line keeps the heartbeat awake"
else
  echo "FAIL client-talk line did not keep heartbeat awake"; failures=$((failures + 1))
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
rm -f "$PANE_DIR/late" "$auto/.claude/state/lanes/.dialog-late"

# A lane stuck on the read dialog: the wake-up line names the path (the prefix is unchanged, so greps on it still match).
: > "$out"; echo early > "$LIVE_FILE"
( HOME="$auto" PATH="$TMP/fakebin:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 timeout 25 bash "$SCRIPT" --max-min 5 > "$out" 2>&1 ) &
watcher=$!
sleep 2.5; echo late >> "$LIVE_FILE"
printf '%s\n' 'Read outside the working directories' '  Read(/home/u/x/ep7/a.txt)' 'Do you want to proceed?' '❯ 1. Yes' 'Esc to cancel' > "$PANE_DIR/late"
wait "$watcher" || true
if grep -q '^waiting on a dialog: late (waiting on a read of /home/u/x/ep7/a.txt)' "$out"; then
  echo "ok   a lane stuck on the read dialog wakes the watcher with the path"
else
  echo "FAIL read dialog line: out=$(cat "$out")"; failures=$((failures + 1))
fi
rm -f "$PANE_DIR/late" "$auto/.claude/state/lanes/.dialog-late"

# A dialog already named (5 Nov: a stale nag in night-watch woke every re-armed watcher at once): the next watcher skips that lane and keeps
# watching the rest, so another lane's report still wakes it. The mark is cleared once the pane shows no dialog, and the next dialog is named.
: > "$out"; echo "early" > "$LIVE_FILE"; echo late >> "$LIVE_FILE"; rm -f "$auto/.claude/state/lanes/2026-10-18-late/report.md"
printf '%s\n' 'Teach auto mode?' '❯ 1. Yes' 'Enter to confirm · Esc to cancel' > "$PANE_DIR/late"
: > "$auto/.claude/state/lanes/.dialog-late"
( HOME="$auto" PATH="$TMP/fakebin:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" POSTED_MARK="$TMP/posted-mark" LANE_WATCH_SETTLE=0 LANE_WATCH_POLL=1 timeout 25 bash "$SCRIPT" --max-min 5 > "$out" 2>&1 ) &
watcher=$!
sleep 3; mkdir -p "$auto/.claude/state/lanes/2026-10-18-early"; echo "Done." > "$auto/.claude/state/lanes/2026-10-18-early/report.md"
wait "$watcher" || true
if grep -q '^report: early ' "$out" && ! grep -q 'waiting on a dialog' "$out"; then
  echo "ok   a dialog already named is skipped: the watcher keeps watching and wakes on another lane's report"
else
  echo "FAIL repeat dialog woke the watcher: out=$(cat "$out")"; failures=$((failures + 1))
fi
rm -f "$auto/.claude/state/lanes/2026-10-18-early/report.md"
: > "$out"; rm -f "$PANE_DIR/late"
( HOME="$auto" PATH="$TMP/fakebin:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 timeout 4 bash "$SCRIPT" --max-min 5 > "$out" 2>&1 ) &
wait $! || true
if [ ! -e "$auto/.claude/state/lanes/.dialog-late" ]; then
  echo "ok   the dialog mark is cleared when the pane shows no dialog"
else
  echo "FAIL dialog mark not cleared"; failures=$((failures + 1))
fi

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
# stale_note reads lane.sh through $lane_sh (set once when the script is sourced), so the fake is named there, not only through HOME.
note=$(lane_sh="$stale_home/.claude/scripts/lane.sh"; stale_note told); calm=$(lane_sh="$stale_home/.claude/scripts/lane.sh"; stale_note calm)
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

# 25-26 Oct, pokeys-site: recycled every 5 to 7 minutes for hours. A lane recycled 3 times inside 30 minutes is named to the hub, once per episode,
# instead of being recycled again.
mkdir -p "$rl_hand"; : > "$rl_hand/report.md"
rl_reset; log_row rl 25; log_row rl 15; log_row rl 5; log_row other 3
rl_watch 130 8 "$TMP/th-out1"
if [ ! -s "$RC_CALLS" ] && grep -q '^thrash: lane rl was recycled 3 times in 30 min (' "$TMP/th-out1" && [ "$(grep -c '^thrash:' "$TMP/th-out1")" = 1 ]; then
  echo "ok   a lane recycled 3 times in 30 minutes is not recycled again; the hub is told in one line"
else echo "FAIL thrash: calls=$(cat "$RC_CALLS") out=$(cat "$TMP/th-out1")"; failures=$((failures + 1)); fi
if grep -qE '^thrash: .*\([0-9]{1,2}:[0-9]{2} (AM|PM), [0-9]{1,2}:[0-9]{2} (AM|PM), [0-9]{1,2}:[0-9]{2} (AM|PM)\)' "$TMP/th-out1"; then echo "ok   the thrash line names the three times, 12-hour"; else echo "FAIL thrash times: $(cat "$TMP/th-out1")"; failures=$((failures + 1)); fi
rm -f "$rl_dir/.recycle-rl.tried"
rl_watch 130 6 "$TMP/th-out2"
if [ ! -s "$RC_CALLS" ] && ! grep -q '^thrash:' "$TMP/th-out2"; then echo "ok   the same thrash episode wakes the hub once, and the lane still is not recycled"; else echo "FAIL thrash repeated: calls=$(cat "$RC_CALLS") out=$(cat "$TMP/th-out2")"; failures=$((failures + 1)); fi
log_row rl 1
rl_watch 130 8 "$TMP/th-out3"
if grep -q '^thrash: lane rl was recycled 4 times' "$TMP/th-out3"; then echo "ok   a later recycle makes it a new episode that is named again"; else echo "FAIL thrash new episode: $(cat "$TMP/th-out3")"; failures=$((failures + 1)); fi
rl_reset; log_row rl 25; log_row rl 15
rl_watch 130 6 "$TMP/th-out4"
if [ "$(cat "$RC_CALLS")" = rl ] && ! grep -q '^thrash:' "$TMP/th-out4"; then echo "ok   two recycles in 30 minutes still recycle (the third is allowed)"; else echo "FAIL two recycles blocked: calls=$(cat "$RC_CALLS") out=$(cat "$TMP/th-out4")"; failures=$((failures + 1)); fi
rl_reset; log_row rl 70; log_row rl 55; log_row rl 40; log_row rl 31 FAILED
rl_watch 130 6 "$TMP/th-out5"
if [ "$(cat "$RC_CALLS")" = rl ] && ! grep -q '^thrash:' "$TMP/th-out5"; then echo "ok   recycles older than 30 minutes, and failed ones, do not count"; else echo "FAIL old recycles counted: calls=$(cat "$RC_CALLS") out=$(cat "$TMP/th-out5")"; failures=$((failures + 1)); fi

# A lane whose third recycle in the window set the stop marker is not recycled by the watcher, however old its rows are, and the hub is not
# told a second time (the FAILED row lane.sh wrote told it). Removing the marker lets the watcher recycle it again.
rl_stop="$rl_dir/.recycle-stop-rl"
rl_reset; rm -f "$rl_stop"; echo '1:09 PM stopped after 3 recycles' > "$rl_stop"
rl_watch 130 6 "$TMP/st-out1"
if [ ! -s "$RC_CALLS" ] && ! grep -q '^thrash:' "$TMP/st-out1"; then echo "ok   a stopped lane over its line is not recycled and no thrash line is printed"; else echo "FAIL stopped lane (no rows): calls=$(cat "$RC_CALLS") out=$(cat "$TMP/st-out1")"; failures=$((failures + 1)); fi
rl_reset; echo '1:09 PM stopped after 3 recycles' > "$rl_stop"; log_row rl 25; log_row rl 15; log_row rl 5
rl_watch 130 6 "$TMP/st-out2"
if [ ! -s "$RC_CALLS" ] && ! grep -q '^thrash:' "$TMP/st-out2"; then echo "ok   a stopped lane with three recent rows is not recycled and not named again"; else echo "FAIL stopped lane (rows): calls=$(cat "$RC_CALLS") out=$(cat "$TMP/st-out2")"; failures=$((failures + 1)); fi
rl_reset; rm -f "$rl_stop"
rl_watch 130 6 "$TMP/st-out3"
if [ "$(cat "$RC_CALLS")" = rl ]; then echo "ok   with the stop marker removed the lane is recycled again"; else echo "FAIL marker removed, not recycled: calls=$(cat "$RC_CALLS") out=$(cat "$TMP/st-out3")"; failures=$((failures + 1)); fi
rl_reset; rm -f "$rl_stop"

# 26 Oct 1:30-8:28 AM: a lane at 95K (under its line) wrote handoff.md, said it was ready to recycle, and sat idle seven hours. The hub is told,
# once per handoff.md, when it has been idle 10 minutes.
orphan_case() { # orphan_case <label> <want yes|no> <ctx> <handoff age min|none> <pane> [last recycle min ago]
  local label=$1 want=$2 got=no
  rl_reset; [ "$4" = none ] || { : > "$rl_hand/handoff.md"; touch -d "-$4 min" "$rl_hand/handoff.md"; }
  [ -z "${6:-}" ] || log_row rl "$6"
  printf '%s\n' "$5" > "$RC_PANE"
  rl_watch "$3" 5 "$TMP/or-out"
  grep -q '^handoff: lane rl wrote handoff.md at ' "$TMP/or-out" && got=yes
  if [ "$got" = "$want" ]; then echo "ok   $label"; else echo "FAIL $label: want $want, got $got: $(cat "$TMP/or-out")"; failures=$((failures + 1)); fi
}
orphan_case "an idle lane under its line with an unused handoff.md wakes the hub" yes 95 20 "$ready_pane"
grep -q "(context 95K, under its 120K line)" "$TMP/or-out" && grep -q 'was never recycled' "$TMP/or-out" && echo "ok   the handoff line names the context and the line" || { echo "FAIL handoff line: $(cat "$TMP/or-out")"; failures=$((failures + 1)); }
rl_watch 95 5 "$TMP/or-out2"
if ! grep -q '^handoff:' "$TMP/or-out2"; then echo "ok   the same handoff.md is named once"; else echo "FAIL handoff named twice: $(cat "$TMP/or-out2")"; failures=$((failures + 1)); fi
orphan_case "a handoff.md written a minute ago is not yet an orphan" no 95 1 "$ready_pane"
orphan_case "a lane that never said ready to recycle is left alone" no 95 20 "$idle_pane"
orphan_case "a lane still working is left alone" no 95 20 "$(printf '%s\n' '⏺ handoff written. Ready to recycle.' '✻ Thinking… (12s · esc to interrupt)' '❯ ')"
orphan_case "a handoff.md older than the lane's last recycle was used" no 95 20 "$ready_pane" 5
orphan_case "no handoff.md, nothing to name" no 95 none "$ready_pane"
orphan_case "over its line the recycle path takes it, not the orphan check" no 130 20 "$ready_pane"
echo "ok   (orphan cases done)"
rm -rf "$rl_hand"; rm -f "$rl_log" "$rl_dir"/.thrash-* "$rl_dir"/.orphan-*; : > "$TURN_FILE"

# 5 Nov 2026 (inbox triage item 39): a lane idle 30+ minutes with a background job still running wakes the hub once; before this a running job
# made a lane fine indefinitely. Idle time comes from the pane's "· done H:MM" line.
job_case() { # job_case <label> <want yes|no> <job|input|none> <idle min> [pane]
  local label=$1 want=$2 got=no
  rl_reset; rm -f "$rl_dir"/.jobidle-*; printf '%s' "$3" > "$JOB_FILE"
  if [ -n "${5:-}" ]; then printf '%s\n' "$5" > "$RC_PANE"; else job_pane "$4" > "$RC_PANE"; fi
  rl_watch 95 5 "$TMP/jb-out"
  grep -q '^idle: rl has been idle [0-9]* min with a background job still running' "$TMP/jb-out" && got=yes
  if [ "$got" = "$want" ]; then echo "ok   $label"; else echo "FAIL $label: want $want, got $got: $(cat "$TMP/jb-out")"; failures=$((failures + 1)); fi
}
job_case "idle 45 minutes with a background job running wakes the hub" yes job 45
grep -q 'idle 4[4-6] min' "$TMP/jb-out" && echo "ok   the line names the idle minutes" || { echo "FAIL idle minutes: $(cat "$TMP/jb-out")"; failures=$((failures + 1)); }
rl_watch 95 5 "$TMP/jb-out2"
if ! grep -q 'background job still running' "$TMP/jb-out2"; then echo "ok   the same idle stretch is named once, across a re-armed watcher"; else echo "FAIL job idle named twice: $(cat "$TMP/jb-out2")"; failures=$((failures + 1)); fi
job_case "idle only 10 minutes with a job running is left alone" no job 10
job_case "idle 45 minutes with no job is left alone" no none 45
job_case "idle 45 minutes because it got input (no job) is left alone" no input 45
job_case "a lane still working (spinner) is left alone" no job 0 "$(printf '%s\n' '⏺ Waiting' '✻ Thinking… (12s · esc to interrupt)' '✻ Worked for 1m 9s · done 1:09 AM' '❯ ')"
job_case "a lane stuck on a dialog is left to the dialog check" no job 0 "$(printf '%s\n' '✻ Worked for 1m 9s · done 1:09 AM' 'Do you want to proceed?' 'Esc to cancel')"
job_case "first idle stretch named" yes job 45
job_pane 40 > "$RC_PANE"
rl_watch 95 5 "$TMP/jb-out3"
if grep -q 'background job still running' "$TMP/jb-out3"; then echo "ok   a later idle stretch (new done time) is named again"; else echo "FAIL new stretch not named: $(cat "$TMP/jb-out3")"; failures=$((failures + 1)); fi
LANE_WATCH_JOB_IDLE_MIN=90 job_case "LANE_WATCH_JOB_IDLE_MIN raises the line (45 min is under 90)" no job 45
rm -f "$rl_dir"/.jobidle-*; : > "$JOB_FILE"; rl_reset
echo "ok   (job-idle cases done)"

[ "$failures" = 0 ] || { echo "$failures failed"; exit 1; }
echo "all lane_watch tests passed"
