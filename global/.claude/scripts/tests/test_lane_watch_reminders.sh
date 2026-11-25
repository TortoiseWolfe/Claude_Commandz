#!/usr/bin/env bash
# Tests for lane_watch.sh, part 2 of 3: idle reminder, green, spend alarms, --recycle-once, step without URL, done without proof. Parts 1 and 3: test_lane_watch.sh, test_lane_watch_wakes.sh; shared setup: lane_watch_common.sh.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
. "$(dirname "${BASH_SOURCE[0]}")/lane_watch_common.sh"

# 6 Nov 2026 (Jonathan: "we have to big of a backlog to idle on smoke breaks"): a lane idle 20+ minutes whose report.md line 1 is not
# QUEUE EMPTY or BLOCKED is named again every 20 minutes while it stays idle, across re-armed watchers.
remind_case() { # remind_case <label> <want yes|no> <idle min> <report line 1> [pane]
  local label=$1 want=$2 got=no
  mkdir -p "$rl_hand"; rl_reset; rm -f "$rl_dir"/.idleremind-*; printf '%s' "none" > "$JOB_FILE"
  printf '%s\n' "$4" > "$rl_hand/report.md"
  if [ -n "${5:-}" ]; then printf '%s\n' "$5" > "$RC_PANE"; else job_pane "$3" > "$RC_PANE"; fi
  LANE_WATCH_IDLE_REMIND_MIN="${REMIND_MIN:-20}" rl_watch 95 5 "$TMP/rm-out"
  grep -q '^idle: rl has been idle [0-9]* min and its report line 1 is not QUEUE EMPTY or BLOCKED' "$TMP/rm-out" && got=yes
  if [ "$got" = "$want" ]; then echo "ok   $label"; else echo "FAIL $label: want $want, got $got: $(cat "$TMP/rm-out")"; failures=$((failures + 1)); fi
}
remind_case "a lane idle 25 minutes with MERGE on line 1 is named" yes 25 "MERGE Claude_Commandz #1 (green)"
grep -q 'idle 2[4-6] min' "$TMP/rm-out" && echo "ok   the line names the idle minutes" || { echo "FAIL remind minutes: $(cat "$TMP/rm-out")"; failures=$((failures + 1)); }
LANE_WATCH_IDLE_REMIND_MIN=20 rl_watch 95 5 "$TMP/rm-out2"
if ! grep -q 'report line 1 is not QUEUE EMPTY' "$TMP/rm-out2"; then echo "ok   a re-armed watcher does not name it again inside the 20 minutes"; else echo "FAIL remind named twice at once: $(cat "$TMP/rm-out2")"; failures=$((failures + 1)); fi
done_epoch=$(cut -d' ' -f1 "$rl_dir/.idleremind-rl")
printf '%s %s' "$done_epoch" "$(( $(date +%s) - 21 * 60 ))" > "$rl_dir/.idleremind-rl"
LANE_WATCH_IDLE_REMIND_MIN=20 rl_watch 95 5 "$TMP/rm-out3"
if grep -q 'report line 1 is not QUEUE EMPTY' "$TMP/rm-out3"; then echo "ok   21 minutes after the last naming, the same idle stretch is named again"; else echo "FAIL remind not repeated: $(cat "$TMP/rm-out3")"; failures=$((failures + 1)); fi
printf '%s %s' "$done_epoch" "$(( $(date +%s) - 5 * 60 ))" > "$rl_dir/.idleremind-rl"
LANE_WATCH_IDLE_REMIND_MIN=20 rl_watch 95 5 "$TMP/rm-out4"
if ! grep -q 'report line 1 is not QUEUE EMPTY' "$TMP/rm-out4"; then echo "ok   5 minutes after the last naming, it is not named yet"; else echo "FAIL remind too soon: $(cat "$TMP/rm-out4")"; failures=$((failures + 1)); fi
remind_case "QUEUE EMPTY on line 1 is left alone" no 90 "QUEUE EMPTY: nothing left in the queue"
remind_case "EMPTY <time> on line 1 (the lane prompts' own wording) is left alone" no 90 "EMPTY 1:30 PM | done: 12 | nothing ready left"
remind_case "a bare EMPTY on line 1 is left alone" no 90 "EMPTY"
remind_case "EMPTY: on line 1 is left alone" no 90 "EMPTY: nothing ready"
remind_case "a word that only starts with EMPTY is still named" yes 90 "EMPTYING the cache now"
remind_case "BLOCKED on line 1 is left alone" no 90 "BLOCKED (hub: needs the Deck token)"
# Issue 19 (8 Nov 2026): a line 1 whose first word names who the lane waits on is waiting, not idle.
remind_case "HUB-ACT on line 1 (waiting on the hub) is left alone" no 90 "HUB-ACT 3:23 AM | the zip is staged, run it in the Sunday window"
remind_case "NEED-HUB on line 1 is left alone" no 90 "NEED-HUB: one merge"
remind_case "READY-FOR-HUB on line 1 is left alone" no 90 "READY-FOR-HUB 2:10 PM | two commands below"
remind_case "STEP-FOR-JONATHAN on line 1 (waiting on a person) is left alone" no 90 "STEP-FOR-JONATHAN https://example.com/page one click"
remind_case "a bare HUB: on line 1 is left alone" no 90 "HUB: merge the pull request below"
remind_case "a word that only starts with HUB (HUBRIS) is still named" yes 90 "HUBRIS is not a status"
remind_case "a word that only contains HUB (GITHUB-PUSH) is still named" yes 90 "GITHUB-PUSH went through"
remind_case "lower-case 'Hub:' is not a status word, so the lane is named" yes 90 "Hub: please merge"
remind_case "HUB only later on the line does not count (WORKING ... HUB-ACT)" yes 90 "WORKING 1:00 PM | HUB-ACT comes later"
# Issue 13 (6 Nov 2026): DONE with no counted ask is a finished lane.
remind_case "DONE <time>. on line 1 is left alone" no 90 "DONE 2:02 PM. Moved: the pull request merged"
remind_case "DONE ... needs hub 0 is left alone" no 90 "DONE 11:10 AM | checks 1, 2, 4, 5 pass | needs hub 0"
remind_case "DONE ... needs Jonathan 0 is left alone" no 90 "DONE 11:10 AM | needs hub 0 | needs Jonathan 0"
remind_case "a bare DONE on line 1 is left alone" no 90 "DONE"
remind_case "DONE: on line 1 is left alone" no 90 "DONE: all four merged"
remind_case "DONE ... needs Jonathan 2 is still named" yes 90 "DONE 11:10 AM | three merged | needs Jonathan 2"
remind_case "DONE ... needs hub 1 is still named" yes 90 "DONE 11:10 AM | needs hub 1"
remind_case "DONE ... Needs the hub: 3 (capitals, 'the', a colon) is still named" yes 90 "DONE 4:00 PM | Needs the hub: 3"
remind_case "DONE ... needs hub 0 and needs Jonathan 1 is still named" yes 90 "DONE 4:00 PM | needs hub 0 | needs Jonathan 1"
remind_case "a word that only starts with DONE (DONE-ISH) is still named" yes 90 "DONE-ISH, two left"
remind_case "DONEx is still named" yes 90 "DONEx 1:00 PM"
remind_case "lower-case 'Done' is not a status word, so the lane is named" yes 90 "Done with the first half"
LC_ALL=en_US.UTF-8 remind_case "HUB-ACT is still left alone under a UTF-8 locale" no 90 "HUB-ACT 3:23 AM | x" 2>/dev/null
LC_ALL=en_US.UTF-8 remind_case "an accented capital first word is named under a UTF-8 locale" yes 90 "ÉTAT-HUB 3:23 AM | x" 2>/dev/null
remind_case "an empty report.md counts as no QUEUE EMPTY, so the lane is named" yes 40 ""
remind_case "idle only 10 minutes is left alone" no 10 "MERGE Claude_Commandz #1"
remind_case "a lane still working (spinner) is left alone" no 0 "MERGE x" "$(printf '%s\n' '⏺ Waiting' '✻ Thinking… (12s · esc to interrupt)' '✻ Worked for 1m 9s · done 1:09 AM' '❯ ')"
remind_case "a lane stuck on a dialog is left to the dialog check" no 0 "MERGE x" "$(printf '%s\n' '✻ Worked for 1m 9s · done 1:09 AM' 'Do you want to proceed?' 'Esc to cancel')"
REMIND_MIN=0 remind_case "LANE_WATCH_IDLE_REMIND_MIN=0 turns the reminder off" no 90 "MERGE x"
REMIND_MIN=60 remind_case "LANE_WATCH_IDLE_REMIND_MIN raises the line (25 minutes is under 60)" no 25 "MERGE x"
rm -f "$rl_dir"/.idleremind-*; : > "$rl_hand/report.md"; rl_reset
echo "ok   (idle-reminder cases done)"

# Issue 278: a pull request listed under "## Hub merges" in a lane's report, in one of his own repos, whose checks all passed, wakes the hub
# once with a "green:" line. The watcher never merges, and a client repo, a foreign owner or unreadable client lists get no line at all.
# A fake gh answers from $GH_MODE (pass, pending, fail, closed) and logs every call to $GH_LOG.
export GH_MODE="$TMP/gh-mode" GH_LOG="$TMP/gh-log"
cat > "$TMP/fakebin-rc/gh" <<'SH'
#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
mode=$(cat "$GH_MODE" 2>/dev/null)
case "$1 $2" in
  "pr view") [ "$mode" = closed ] && echo MERGED || echo OPEN ;;
  "pr checks")
    case "$mode" in
      pending) printf 'pytest\tpass\t3m\thttps://x/1\nshell\tpending\t0\thttps://x/2\n' ;;
      fail)    printf 'pytest\tfail\t3m\thttps://x/1\nshell\tpass\t9m\thttps://x/2\n' ;;
      none)    echo "no checks reported on the 'x' branch" ;;
      *)       printf 'pytest\tpass\t3m\thttps://x/1\nshell\tpass\t9m\thttps://x/2\nlint\tskipping\t0\thttps://x/3\n' ;;
    esac ;;
esac
SH
chmod +x "$TMP/fakebin-rc/gh"
gr_hub="$rc_home/repos/hub"; mkdir -p "$gr_hub"
printf '# clients\n~/repos/Acme-Client\n' > "$gr_hub/client-repos.txt"
printf 'a@x.test\tBeta-Client\n' > "$gr_hub/client-senders.tsv"
green_case() { # green_case <label> <want yes|no> <mode> <report line> [skip-lists]
  local label=$1 want=$2 got=no
  mkdir -p "$rl_hand"; rl_reset; rm -f "$rl_dir"/.green-*; : > "$GH_LOG"; printf '%s' "$3" > "$GH_MODE"; printf '%s' none > "$JOB_FILE"
  printf 'WORKING 1:00 PM | done: 1\n\n## Hub merges\n%s\n\n## Notes\n- gh pr merge 99 -R TortoiseWolfe/Notes-Only --squash\n' "$4" > "$rl_hand/report.md"
  [ "${5:-}" = nolists ] && mv "$gr_hub/client-repos.txt" "$gr_hub/client-repos.off"
  LANE_WATCH_GREEN_EVERY="${GR_EVERY:-0}" rl_watch 95 5 "$TMP/gr-out"
  [ "${5:-}" = nolists ] && mv "$gr_hub/client-repos.off" "$gr_hub/client-repos.txt"
  grep -q "^green: rl's pull request ${GR_PR:-12} in ${GR_REPO:-TortoiseWolfe/Claude_Commandz} has every check passed" "$TMP/gr-out" && got=yes
  if [ "$got" = "$want" ]; then echo "ok   $label"; else echo "FAIL $label: want $want, got $got: $(cat "$TMP/gr-out")"; failures=$((failures + 1)); fi
}
green_case "all checks passed on his own repo's PR wakes the hub" yes pass '- gh pr merge 12 -R TortoiseWolfe/Claude_Commandz --squash --delete-branch'
grep -q '3 checks, 1 skipped' "$TMP/gr-out" && grep -q -- '--squash --delete-branch (the watcher does not merge)' "$TMP/gr-out" \
  && echo "ok   the line counts the checks and skips, and says the watcher does not merge" || { echo "FAIL green line text: $(cat "$TMP/gr-out")"; failures=$((failures + 1)); }
if ! grep -qE '^pr merge' "$GH_LOG"; then echo "ok   the watcher never ran gh pr merge"; else echo "FAIL watcher merged: $(cat "$GH_LOG")"; failures=$((failures + 1)); fi
LANE_WATCH_GREEN_EVERY=0 rl_watch 95 5 "$TMP/gr-out2"
if ! grep -q '^green:' "$TMP/gr-out2"; then echo "ok   the same PR is named once, across a re-armed watcher"; else echo "FAIL green named twice: $(cat "$TMP/gr-out2")"; failures=$((failures + 1)); fi
green_case "a check still pending is left alone" no pending '- gh pr merge 12 -R TortoiseWolfe/Claude_Commandz --squash --delete-branch'
green_case "a failed check is left alone" no fail '- gh pr merge 12 -R TortoiseWolfe/Claude_Commandz --squash --delete-branch'
green_case "a PR with no checks is left alone" no none '- gh pr merge 12 -R TortoiseWolfe/Claude_Commandz --squash --delete-branch'
green_case "a PR that is no longer open is left alone" no closed '- gh pr merge 12 -R TortoiseWolfe/Claude_Commandz --squash --delete-branch'
GR_REPO=TortoiseWolfe/Acme-Client GR_PR=12 green_case "a client repo gets no line, and gh is not even asked" no pass '- gh pr merge 12 -R TortoiseWolfe/Acme-Client --squash'
if [ ! -s "$GH_LOG" ]; then echo "ok   gh was not called for the client repo"; else echo "FAIL gh called for client: $(cat "$GH_LOG")"; failures=$((failures + 1)); fi
GR_REPO=TortoiseWolfe/beta-client green_case "a client named in client-senders.tsv gets no line (case ignored)" no pass '- gh pr merge 12 -R TortoiseWolfe/beta-client --squash'
GR_REPO=PM-Beer/scout-connector green_case "a repo owned by someone else gets no line" no pass '- gh pr merge 12 -R PM-Beer/scout-connector --squash'
green_case "unreadable client lists get no line (fails closed)" no pass '- gh pr merge 12 -R TortoiseWolfe/Claude_Commandz --squash' nolists
green_case "a line outside the Hub merges section is ignored" no pass '- nothing refused so far'
GR_PR=7 green_case "the PR number is found when the flags come first" yes pass '- `gh pr merge -R TortoiseWolfe/Claude_Commandz --squash 7`'
GR_PR=12 GR_REPO=TurtleWolfe/Claude_Commandz green_case "the older account's repos count as his own" yes pass '- gh pr merge #12 --repo TurtleWolfe/Claude_Commandz'
GR_EVERY=off green_case "LANE_WATCH_GREEN_EVERY=off turns it off" no pass '- gh pr merge 12 -R TortoiseWolfe/Claude_Commandz --squash'
rm -f "$rl_dir"/.green-* "$TMP/fakebin-rc/gh"; : > "$rl_hand/report.md"; rl_reset
echo "ok   (green-PR cases done)"

# Spend alarm: a lane past the limit today wakes the hub once per lane per day; the ledger is asked no more than every few minutes.
sp_home="$TMP/sp-home"; mkdir -p "$sp_home/.claude/state/lanes"
sp_cmd="$TMP/fake-spend.sh"; sp_calls="$TMP/spend-calls"; sp_args="$TMP/spend-args"; : > "$sp_calls"
cat > "$sp_cmd" <<'SH'
#!/usr/bin/env bash
echo x >> "$SPEND_CALLS"; echo "$*" > "$SPEND_ARGS"
printf '62.48\tacme-mail\tfable\n30.12\tmail-two\tfable+opus\ntotal\t318.11\t79.53\n'
SH
chmod +x "$sp_cmd"
sp_run() { HOME="$sp_home" OPENCLAW_RECONCILE=/nonexistent LANE_SPEND_CMD="$sp_cmd" SPEND_CALLS="$sp_calls" SPEND_ARGS="$sp_args" \
  LANE_WATCH_SPEND_SECS="${1:-0}" bash "$SCRIPT" --max-min 0 2>&1 || true; }
out=$(sp_run)
if printf '%s\n' "$out" | grep -q '^spend: lane acme-mail has cost \$62.48 API-equivalent today (fable), past the \$25 line' \
   && printf '%s\n' "$out" | grep -q '^spend: lane mail-two has cost \$30.12 .*(fable+opus)' && ! printf '%s\n' "$out" | grep -q 'total'; then
  echo "ok   a lane past the limit wakes the hub with its cost and models (the total line is not a lane)"
else echo "FAIL spend wake: $out"; failures=$((failures + 1)); fi
if grep -qx -- '--over 25' "$sp_args"; then echo "ok   the ledger is asked for lanes at or over 25 dollars"; else echo "FAIL spend args: $(cat "$sp_args")"; failures=$((failures + 1)); fi
out=$(sp_run)
if ! printf '%s\n' "$out" | grep -q '^spend:' && [ "$out" = "$(printf 'WAKE-REASON heartbeat\nheartbeat: nothing new in 0 min')" ]; then
  echo "ok   a lane already named today does not wake the hub again"
else echo "FAIL spend named twice: $out"; failures=$((failures + 1)); fi
before=$(wc -l < "$sp_calls")
out=$(sp_run 600)
if [ "$(wc -l < "$sp_calls")" = "$before" ]; then echo "ok   the ledger is not asked again inside LANE_WATCH_SPEND_SECS (a re-armed watcher keeps the spacing)"
else echo "FAIL spend throttle: calls $before -> $(wc -l < "$sp_calls")"; failures=$((failures + 1)); fi
printf '62.48\tacme-mail\tfable\n30.12\tmail-two\tfable+opus\n41.00\tthird\tsonnet\ntotal\t400\t10\n' > "$TMP/fake-out.txt"
printf '#!/usr/bin/env bash\ncat "%s"\n' "$TMP/fake-out.txt" > "$sp_cmd"
out=$(sp_run)
if [ "$(printf '%s\n' "$out" | grep -c '^spend:')" = 1 ] && printf '%s\n' "$out" | grep -q '^spend: lane third has cost \$41.00'; then
  echo "ok   a lane that crosses later in the day is named, the earlier ones are not repeated"
else echo "FAIL spend later crossing: $out"; failures=$((failures + 1)); fi

# Hub spend alarm: the hub past the hub limit today wakes the hub once per day.
hub_sp_home="$TMP/hub-sp-home"; mkdir -p "$hub_sp_home/.claude/state/lanes"
hub_sp_cmd="$TMP/fake-hub-spend.sh"; hub_sp_calls="$TMP/hub-spend-calls"; hub_sp_args="$TMP/hub-spend-args"; : > "$hub_sp_calls"
cat > "$hub_sp_cmd" <<'SH'
#!/usr/bin/env bash
echo x >> "$HUB_SPEND_CALLS"; echo "$*" > "$HUB_SPEND_ARGS"
printf '20.00\tlane-a\tsonnet\nhub\t80.50\topus+sonnet\ntotal\t100.50\t0.00\n'
SH
chmod +x "$hub_sp_cmd"
hub_sp_run() { HOME="$hub_sp_home" OPENCLAW_RECONCILE=/nonexistent LANE_SPEND_CMD="$hub_sp_cmd" SPEND_CALLS="$hub_sp_calls" SPEND_ARGS="$hub_sp_args" \
  HUB_SPEND_CALLS="$hub_sp_calls" HUB_SPEND_ARGS="$hub_sp_args" \
  LANE_WATCH_SPEND_SECS=0 bash "$SCRIPT" --max-min 0 2>&1 || true; }
out=$(HUB_SPEND_LIMIT=75 hub_sp_run)
if printf '%s\n' "$out" | grep -q '^spend: the hub has cost \$80.50 API-equivalent today (opus+sonnet), past the \$75 line' \
   && ! printf '%s\n' "$out" | grep -q '^spend: lane lane-a'; then
  echo "ok   the hub past the limit wakes the hub (a lane under its limit does not)"
else echo "FAIL hub spend wake: $out"; failures=$((failures + 1)); fi
out=$(HUB_SPEND_LIMIT=75 hub_sp_run)
if ! printf '%s\n' "$out" | grep -q '^spend: the hub' && [ "$out" = "$(printf 'WAKE-REASON heartbeat\nheartbeat: nothing new in 0 min')" ]; then
  echo "ok   the hub already named today does not wake the hub again"
else echo "FAIL hub spend named twice: $out"; failures=$((failures + 1)); fi

printf '#!/usr/bin/env bash\nexit 1\n' > "$sp_cmd"; rm -f "$sp_home"/.claude/state/lanes/.spend-alarm-*
out=$(sp_run)
if [ "$out" = "$(printf 'WAKE-REASON heartbeat\nheartbeat: nothing new in 0 min')" ]; then echo "ok   a failing ledger read wakes nobody and stops nothing"; else echo "FAIL spend failure: $out"; failures=$((failures + 1)); fi

# High-tier alarm (22 Oct 8 PM: ten lanes ran on Opus all day, one was entitled): a live lane on opus or fable with no logged reason is
# named once per lane per day, with its model and today's spend. An entitled lane and a sonnet lane are not.
hi_home="$TMP/hi-home"; mkdir -p "$hi_home/.claude/state/lanes" "$hi_home/bin"
hi_lane_sh="$TMP/hi-lane.sh"
cat > "$hi_lane_sh" <<'SH'
#!/usr/bin/env bash
[ "$1" = --tier ] || exit 0
case "$2" in
  hi-open) printf 'opus\t-\n' ;;
  hi-fable) printf 'fable\t-\n' ;;
  hi-ent) printf 'opus\tdesign review of the router\n' ;;
  hi-site) printf 'fable\tsite-model\n' ;;
  hi-sonnet) printf 'sonnet\t-\n' ;;
esac
SH
chmod +x "$hi_lane_sh"
printf '#!/usr/bin/env bash\nprintf "12.50\\thi-open\\topus\\n\\ntotal\\t12.5\\t0\\n"\n' > "$TMP/hi-spend.sh"; chmod +x "$TMP/hi-spend.sh"
printf '%s\n' hi-open hi-fable hi-ent hi-site hi-sonnet > "$TMP/hi-live"
cat > "$TMP/hi-bin-tmux" <<'SH'
#!/usr/bin/env bash
case "$1" in list-windows) cat "$HI_LIVE" ;; esac
SH
mkdir -p "$TMP/hi-bin"; cp "$TMP/hi-bin-tmux" "$TMP/hi-bin/tmux"; chmod +x "$TMP/hi-bin/tmux"
hi_run() { HOME="$hi_home" PATH="$TMP/hi-bin:$PATH" HI_LIVE="$TMP/hi-live" OPENCLAW_RECONCILE=/nonexistent LANE_SH="$hi_lane_sh" LANE_SPEND_CMD="$TMP/hi-spend.sh" \
  LANE_WATCH_SPEND_SECS=99999 LANE_WATCH_POLL=1 timeout 20 bash "$SCRIPT" --max-min 0 hi-open hi-fable hi-ent hi-site hi-sonnet 2>&1 || true; }
out=$(hi_run)
if printf '%s\n' "$out" | grep -q '^tier: lane hi-open is on opus with no logged --allow-high-tier reason, \$12.50 API-equivalent today' \
   && printf '%s\n' "$out" | grep -q '^tier: lane hi-fable is on fable with no logged --allow-high-tier reason, \$0 API-equivalent today'; then
  echo "ok   a live opus or fable lane with no logged reason wakes the hub with its model and today's spend"
else echo "FAIL high-tier wake: $out"; failures=$((failures + 1)); fi
if ! printf '%s\n' "$out" | grep -q 'hi-ent\|hi-site\|hi-sonnet'; then echo "ok   a lane with a reason, a site-model lane and a sonnet lane are not named"
else echo "FAIL high-tier named the wrong lane: $out"; failures=$((failures + 1)); fi
out=$(hi_run)
if ! printf '%s\n' "$out" | grep -q '^tier:'; then echo "ok   a lane already named today is not named again"
else echo "FAIL high-tier named twice: $out"; failures=$((failures + 1)); fi
echo hi-late >> "$TMP/hi-live"
cat > "$hi_lane_sh" <<'SH'
#!/usr/bin/env bash
[ "$1" = --tier ] || exit 0
case "$2" in hi-open) printf 'opus\t-\n' ;; hi-late) printf 'opus\t-\n' ;; esac
SH
out=$(HOME="$hi_home" PATH="$TMP/hi-bin:$PATH" HI_LIVE="$TMP/hi-live" OPENCLAW_RECONCILE=/nonexistent LANE_SH="$hi_lane_sh" LANE_SPEND_CMD="$TMP/hi-spend.sh" \
  LANE_WATCH_SPEND_SECS=99999 LANE_WATCH_POLL=1 timeout 20 bash "$SCRIPT" --max-min 0 hi-open hi-late 2>&1 || true)
if [ "$(printf '%s\n' "$out" | grep -c '^tier:')" = 1 ] && printf '%s\n' "$out" | grep -q '^tier: lane hi-late '; then
  echo "ok   a lane that moves to opus later in the day is named, the earlier one is not repeated"
else echo "FAIL high-tier later lane: $out"; failures=$((failures + 1)); fi

# --recycle-once: the 15-minute Windows task (openclaw_status_refresh.sh) runs one silent pass over every live lane, same line, same
# thrash and queued-input guards as the watcher, and wakes nobody. 26 Oct, scout-run-2 auto-compacted because no watcher was armed.
sleep 3   # let any earlier fake recycle finish
rl_reset; ro_run 130 "$idle_pane"; ro_case "--recycle-once recycles a lane past its line, silently, exit 0" rl
sleep 3; rl_reset; ro_run 100 "$idle_pane"; ro_case "--recycle-once leaves a lane under its line alone" ""
sleep 3; rl_reset; log_row rl 25; log_row rl 15; log_row rl 5; ro_run 130 "$idle_pane"
ro_case "--recycle-once does not recycle a thrashing lane, and says nothing" ""
if [ ! -e "$rl_dir/.thrash-rl" ] && ! ls "$rl_dir"/.thrash-* >/dev/null 2>&1; then echo "ok   ... and leaves no thrash marker, so an armed watcher can still name it"; else echo "FAIL thrash marker left by --recycle-once"; failures=$((failures + 1)); fi
sleep 3; rl_reset; mkdir "$rl_dir/.recycle-rl.lock"; ro_run 130 "$idle_pane"; rmdir "$rl_dir/.recycle-rl.lock"
ro_case "--recycle-once leaves a lane whose recycle is already under way" ""
sleep 3; rl_reset; date +%s > "$rl_dir/.recycle-rl.tried"; ro_run 130 "$idle_pane"
ro_case "--recycle-once honours the retry spacing (a fresh .tried file)" ""
sleep 3; rl_reset; ro_run 130 "$busy_pane"; ro_case "--recycle-once recycles a lane mid-turn too" rl
sleep 3; rl_reset
if grep -q -- '--recycle-once' "$(dirname "$SCRIPT")/openclaw_status_refresh.sh"; then echo "ok   openclaw_status_refresh.sh runs lane_watch.sh --recycle-once"; else echo "FAIL openclaw_status_refresh.sh does not run --recycle-once"; failures=$((failures + 1)); fi

# A STEP-FOR-JONATHAN report with no https:// link wakes the hub ("step without URL: <lane>") and the lane is asked, through lane.sh --send, to
# rewrite line 1 (26 Oct 11:52 AM: a relayed step reached him with no link). A throwaway lane folder stands in for the lane.
su_watch 'STEP-FOR-JONATHAN open the Vercel dashboard and approve the deploy' "$TMP/su-out1"
if grep -q '^step without URL: rl' "$TMP/su-out1" && [ "$(wc -l < "$SEND_LOG")" = 1 ] && grep -q "^rl	Hub: line 1 of your report.md is a STEP-FOR-JONATHAN with no link" "$SEND_LOG" && grep -q 'https://' "$SEND_LOG"; then
  echo "ok   a STEP-FOR-JONATHAN report with no URL wakes the hub and the lane is asked to rewrite line 1"
else echo "FAIL step without URL: out=$(cat "$TMP/su-out1") send=$(cat "$SEND_LOG")"; failures=$((failures + 1)); fi
su_watch 'STEP-FOR-JONATHAN open https://vercel.com/acme/site/deployments (fleet Chromium tab "Deployments"), approve; done when it says Ready' "$TMP/su-out2"
if ! grep -q 'step without URL' "$TMP/su-out2" && [ ! -s "$SEND_LOG" ]; then echo "ok   a STEP-FOR-JONATHAN report with an https:// URL is left alone"; else echo "FAIL step with URL flagged: out=$(cat "$TMP/su-out2") send=$(cat "$SEND_LOG")"; failures=$((failures + 1)); fi
su_watch "DONE $(date -d '30 min ago' '+%-I:%M %p') shipped, items 0, see the page at none" "$TMP/su-out3"   # a time behind the clock, so this case does not depend on the hour it runs
if ! grep -q 'step without URL' "$TMP/su-out3" && [ ! -s "$SEND_LOG" ]; then echo "ok   an ordinary report with no URL is left alone"; else echo "FAIL ordinary report flagged: out=$(cat "$TMP/su-out3") send=$(cat "$SEND_LOG")"; failures=$((failures + 1)); fi
# The same bad line 1 is named once, even across a re-armed watcher (the mark is a file); a rewrite that still has no URL is a new line.
su_watch 'STEP-FOR-JONATHAN click the button' "$TMP/su-out4"
su_again "$TMP/su-out5"
if grep -q '^step without URL' "$TMP/su-out4" && ! grep -q '^step without URL' "$TMP/su-out5" && [ ! -s "$SEND_LOG" ]; then echo "ok   the same bad line is named once, across a re-armed watcher"; else echo "FAIL repeated naming: first=$(cat "$TMP/su-out4") second=$(cat "$TMP/su-out5")"; failures=$((failures + 1)); fi
printf 'STEP-FOR-JONATHAN click the other button\n' > "$su_rep"
su_again "$TMP/su-out6"
# su_again touches the report after the rewrite, so the new line 1 is the one checked
if grep -q '^step without URL' "$TMP/su-out6" && [ "$(wc -l < "$SEND_LOG")" = 1 ]; then echo "ok   a rewrite that still has no URL is named again"; else echo "FAIL rewrite not re-checked: $(cat "$TMP/su-out6")"; failures=$((failures + 1)); fi
rm -rf "$su_dir"; rm -f "$rl_dir"/.stepurl-*

# 11 Nov 2026: a lane wrote that all 12 of its items were done after 12 minutes when 3 were. A line 1 that says the work is finished (DONE, QUEUE
# EMPTY, EMPTY) must count its items ("items N") and the report must hold N proof lines under "## Proof" (a "- " line with an https:// link or the
# path of a file that exists); otherwise the watcher names the lane ("done without proof: <lane>") and sends the lane a note. Reuses su_watch's setup.
mkdir -p "$su_dir"
dp_file="$TMP/dp-real-file"; : > "$dp_file"
dp_watch() { # dp_watch <whole report text> <out>: the report appears 1.5 s after the watcher starts
  rl_reset; rm -f "$rl_dir"/.doneproof-* "$rl_dir"/.reportseen-* "$su_rep"; : > "$SEND_LOG"; echo 50 > "$CTX_FILE"
  ( HOME="$rc_home" PATH="$TMP/fakebin-rc:$PATH" OPENCLAW_DISPATCH="$fake_dispatch" LANE_WATCH_POLL=1 LANE_RECYCLE_RETRY_SECS=600 \
      timeout 8 bash "$SCRIPT" --max-min 5 rl > "$2" 2>&1 ) &
  suw=$!
  sleep 1.5; printf '%s\n' "$1" > "$su_rep"
  wait "$suw" || true
}
dp_named() { grep -q '^done without proof: rl ' "$1"; }
dp_quiet() { ! grep -q '^done without proof' "$1" && [ ! -s "$SEND_LOG" ]; }
nl=$'\n'
dp_t=$(date -d '30 min ago' '+%-I:%M %p')   # a time behind the clock, so these cases do not depend on the hour they run
dp_watch "QUEUE EMPTY $dp_t | items 3 | all shipped${nl}${nl}## Proof${nl}- https://example.com/one" "$TMP/dp-out1"
if dp_named "$TMP/dp-out1" && [ "$(wc -l < "$SEND_LOG")" = 1 ] && grep -q '## Proof' "$SEND_LOG" && grep -q 'items N' "$SEND_LOG" && grep -q 'line 1 counts 3 items and the report has 1 proof lines' "$TMP/dp-out1"; then
  echo "ok   a finished line 1 counting 3 items with 1 proof line is named and the lane is sent for the proof"
else echo "FAIL done without proof (short): out=$(cat "$TMP/dp-out1") send=$(cat "$SEND_LOG")"; failures=$((failures + 1)); fi
dp_watch "QUEUE EMPTY $dp_t | items 3 | all shipped${nl}${nl}## Proof${nl}- https://example.com/one${nl}- merged https://example.com/two${nl}- file $dp_file" "$TMP/dp-out2"
if dp_quiet "$TMP/dp-out2"; then echo "ok   items 3 with three proof lines (two links, one existing file) is left alone"; else echo "FAIL full proof flagged: out=$(cat "$TMP/dp-out2") send=$(cat "$SEND_LOG")"; failures=$((failures + 1)); fi
dp_watch "DONE $dp_t | all finished${nl}${nl}## Proof${nl}- https://example.com/one${nl}- https://example.com/two${nl}- https://example.com/three${nl}- https://example.com/four" "$TMP/dp-out3"
if dp_named "$TMP/dp-out3" && grep -q 'no item count' "$TMP/dp-out3" && [ "$(wc -l < "$SEND_LOG")" = 1 ]; then echo "ok   DONE with no items word is named (no item count), and 4:30 is not read as 4 items"; else echo "FAIL no item count: out=$(cat "$TMP/dp-out3") send=$(cat "$SEND_LOG")"; failures=$((failures + 1)); fi
dp_watch "QUEUE EMPTY | items 0 | nothing to do" "$TMP/dp-out4"
if dp_quiet "$TMP/dp-out4"; then echo "ok   items 0 with no proof section is left alone"; else echo "FAIL items 0 flagged: out=$(cat "$TMP/dp-out4") send=$(cat "$SEND_LOG")"; failures=$((failures + 1)); fi
dp_ok=1
for dp_line in "BLOCKED $dp_t | needs hub | items 3" "WORKING $dp_t | items 3 | on it" 'STEP-FOR-JONATHAN open https://example.com/page and approve | items 3'; do
  dp_watch "$dp_line" "$TMP/dp-out5"
  dp_quiet "$TMP/dp-out5" || { dp_ok=0; echo "FAIL not a done claim but flagged: $dp_line out=$(cat "$TMP/dp-out5")"; }
done
if [ "$dp_ok" = 1 ]; then echo "ok   BLOCKED, WORKING and STEP-FOR-JONATHAN first lines with no proof are left alone"; else failures=$((failures + 1)); fi
dp_watch "QUEUE EMPTY | items 3${nl}## Proof${nl}- /no/such/file/anywhere${nl}- https://example.com/one${nl}- https://example.com/one${nl}- just words, no link or path" "$TMP/dp-out6"
if dp_named "$TMP/dp-out6" && grep -q 'the report has 1 proof lines' "$TMP/dp-out6"; then echo "ok   a missing path, a repeated line and a bare '- ' line do not count (1 of 3)"; else echo "FAIL proof counting: out=$(cat "$TMP/dp-out6")"; failures=$((failures + 1)); fi
dp_watch "QUEUE EMPTY | items 2${nl}- https://example.com/above${nl}## Proof${nl}- https://example.com/one${nl}## Other${nl}- https://example.com/two${nl}- https://example.com/three" "$TMP/dp-out7"
if dp_named "$TMP/dp-out7" && grep -q 'the report has 1 proof lines' "$TMP/dp-out7"; then echo "ok   lines above ## Proof and under a later ## Other heading are not counted"; else echo "FAIL section bounds: out=$(cat "$TMP/dp-out7")"; failures=$((failures + 1)); fi
dp_watch "QUEUE EMPTY | items 3${nl}## Proof${nl}- https://example.com/one" "$TMP/dp-out8a"
su_again "$TMP/dp-out8b"
printf 'QUEUE EMPTY | items 3\n## Proof\n- https://example.com/one\n- https://example.com/two\n' > "$su_rep"; : > "$SEND_LOG"
su_again "$TMP/dp-out8c"
printf 'QUEUE EMPTY | items 3\n## Proof\n- https://example.com/one\n- https://example.com/two\n- https://example.com/three\n' > "$su_rep"; : > "$SEND_LOG"
su_again "$TMP/dp-out8d"
if dp_named "$TMP/dp-out8a" && grep -q 'the report has 1 proof lines' "$TMP/dp-out8a" && ! grep -q '^done without proof' "$TMP/dp-out8b" && dp_named "$TMP/dp-out8c" && grep -q 'the report has 2 proof lines' "$TMP/dp-out8c" && dp_quiet "$TMP/dp-out8d"; then
  echo "ok   a short report is named once across a re-armed watcher, again when one proof is added and it is still short, and left alone once it reaches its count"
else echo "FAIL re-armed naming: a=$(cat "$TMP/dp-out8a") b=$(cat "$TMP/dp-out8b") c=$(cat "$TMP/dp-out8c") d=$(cat "$TMP/dp-out8d")"; failures=$((failures + 1)); fi
dp_watch "QUEUE EMPTY $dp_t | items 4:30 | done" "$TMP/dp-out9"
if dp_named "$TMP/dp-out9" && grep -q 'no item count' "$TMP/dp-out9"; then echo "ok   items 4:30 is a time, not a count"; else echo "FAIL items 4:30: out=$(cat "$TMP/dp-out9")"; failures=$((failures + 1)); fi
dp_watch "DONE BY 5:00 PM | half way" "$TMP/dp-out9b"
dp_watch2_out="$TMP/dp-out9c"; dp_quiet9b=0; dp_quiet "$TMP/dp-out9b" && dp_quiet9b=1
dp_watch "DONE $dp_t | by hand" "$dp_watch2_out"
if [ "$dp_quiet9b" = 1 ] && dp_named "$dp_watch2_out" && grep -q 'no item count' "$dp_watch2_out"; then echo "ok   DONE BY <time> is a target, not a finished line; DONE with a lower-case by later is still named"; else echo "FAIL DONE BY: quiet=$dp_quiet9b out=$(cat "$TMP/dp-out9b") later=$(cat "$dp_watch2_out")"; failures=$((failures + 1)); fi
mkdir -p "$rc_home/dp-dir"; : > "$rc_home/dp-dir/thing"
dp_watch "QUEUE EMPTY | items 2${nl}## Proof${nl}- ~/dp-dir/thing${nl}- see \`$dp_file\`." "$TMP/dp-out10"
if dp_quiet "$TMP/dp-out10"; then echo "ok   a ~/ path under HOME and a backtick-wrapped path both count"; else echo "FAIL ~/ and backtick paths: out=$(cat "$TMP/dp-out10") send=$(cat "$SEND_LOG")"; failures=$((failures + 1)); fi
rm -f "$TMP/dp-pwn" "$TMP/dp-pwn2"
dp_watch "QUEUE EMPTY | items 1${nl}## Proof${nl}"'- $(touch '"$TMP/dp-pwn"') and `touch '"$TMP/dp-pwn2"'`' "$TMP/dp-out11"
if [ ! -e "$TMP/dp-pwn" ] && [ ! -e "$TMP/dp-pwn2" ] && dp_named "$TMP/dp-out11"; then echo "ok   a proof line holding \$(...) and a backtick command runs nothing"; else echo "FAIL report text was run: out=$(cat "$TMP/dp-out11")"; failures=$((failures + 1)); fi
rm -rf "$su_dir" "$rc_home/dp-dir" "$dp_file"; rm -f "$rl_dir"/.doneproof-* "$TMP/dp-pwn" "$TMP/dp-pwn2"

[ "$failures" = 0 ] || { echo "$failures failed"; exit 1; }
echo "all lane_watch reminder tests passed"
