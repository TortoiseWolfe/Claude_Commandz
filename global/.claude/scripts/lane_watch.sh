#!/usr/bin/env bash
# Wake the hub when a lane has something to report, so Jonathan never has to ask for progress.
#
#   lane_watch.sh [--max-min N] [--check 'cmd'] [<lane-name>...]
#   lane_watch.sh --recycle-once        one silent recycle pass over every live lane, then exit (the 15-minute scheduled task)
#
# Exits (printing one line per event) as soon as any of these happens:
#   - openclaw_dispatch.py open lists an unanswered receptionist question, or a new one is filed. A burst of questions wakes the
#     hub ONCE: it waits until no new question has come for LANE_WATCH_BATCH_QUIET seconds (default 60), at most LANE_WATCH_BATCH_MAX
#     (default 180), then prints the whole list. Each wake after the hub's cache has gone cold costs $1.2 to $1.6 against $0.05 warm.
#   - a watched lane's report.md (newest ~/.claude/state/lanes/*-<name>/report.md) is new or changes
#   - a watched lane ends a turn without a new report (see turn_done_time), i.e. it may be asking the hub. Not a turn that only answered a
#     Monitor progress event and asked nothing (lane.sh --turn says monitor): a lane watching a long download would wake the hub every tick.
#   - a watched lane's report.md line 1 starts STEP-FOR-JONATHAN and has no https:// URL (step_url_check; the lane is asked to rewrite it)
#   - a watched lane's report.md line 1 starts with a status word and a time (WORKING 3:05 PM ...) that is more than 2 minutes ahead of the
#     file's modification time (stamp_ahead_check; the lane is sent the correction). Only that fixed place counts; times deeper in the line do not.
#   - a watched lane's tmux window is stuck on a dialog (see dialog_waiting), i.e. it is waiting on the hub. Named ONCE per dialog: a dialog
#     the hub was already told about is skipped by every later poll and re-armed watcher until the pane shows no dialog (then the next is named).
#   - a watched lane's tmux window disappears
#   - a watched lane's window is still there but its pane is a bare shell (lane.sh --pane says "dead <cmd>"): the claude process is gone.
#     Named once per death ("dead: <lane> (pane shows <cmd>); restart it fresh from its handoff.md"); the mark is cleared when the pane runs
#     claude again. 28 Oct 2026: a lane killed its own claude with pgrep -f, sat at a bash prompt for 3 hours and nothing woke the hub.
#   - a recycle (see below) failed
#   - the hub asked the owner a question in the terminal and he has not answered in LANE_WATCH_UNANSWERED_MIN minutes (default 15; 0 or off
#     disables): unanswered_check names it once, so the question can go to Hatch as one [CC>MUSE] note draft
#   - Jonathan asks from OpenClaw (a new "Jonathan via OpenClaw:" line in hub-inbox.md)
#   - a message of Jonathan's reached no lane and not the hub's list (openclaw_reconcile.py; it files each one with the hub as it wakes)
#   - a post for Jonathan's chat has failed to land for 15 minutes (openclaw_dispatch.py queue check). Every poll also tries the
#     outbox again in the background (queue pump), so a post that failed while Windows could not reach WSL lands once it can.
#   - the WSL-to-Windows bridge cannot be healed or still fails its 10 s probe (wsl_bridge_watchdog.py runs from every poll and raises the alert)
#   - the weekly budget reading failed (check_calibrate: usage_calibrate.js wrote ok:false to usage-calibrate-status.json; named once per distinct failure)
#   - a lane has cost over $LANE_SPEND_LIMIT (default 25) dollars of API-equivalent spend today (lane_spend.py, from the usage ledger),
#     once per lane per day. 21 Oct: $303 went in one day, $85 on one mail lane redrafting a reply, and nothing said so until night.
#   - a live lane is on opus or fable and its index row holds no --allow-high-tier reason (lane.sh --tier says "<model> TAB -"; a site-model
#     prompt counts as a reason). Named once per lane per day with its model and today's spend. 22 Oct: ten lanes ran on Opus all day, only
#     one was entitled, and the tier was only checked at start. Checked on every poll; a recycle also puts such a lane back on sonnet.
#   - --check 'cmd' exits 0 (an outside condition, e.g. a VOD now returns 404)
#   - --max-min minutes pass (default 45): exits with "heartbeat", so the hub reports even when nothing moved. While every watched lane is
#     idle (no spinner, no dialog, no recycle under way) and no client line is open in hub-inbox.md, the heartbeat
#     is stretched to LANE_WATCH_IDLE_MAX_MIN (default 120) minutes; a lane that starts working puts it back to --max-min. 31 Oct: the
#     tower's 45-minute heartbeat woke the hub at 9:15 AM with nothing new while every lane was idle.
# What does NOT wake the hub (31 Oct 2026, the tower's note "Hub wakes that do nothing"; a wake after the cache has gone cold costs $1.2 to $1.6):
#   - a turn that ends within LANE_WATCH_TURN_GRACE (default 300) seconds of a report.md change: the report is the news, the turn end is the
#     same turn. (PR 175 fixed the pane-redraw repeat; this is the report-then-turn-end double wake.)
#   - a report whose line 1 starts HOURLY or STATUS: routine status. Lanes are told to keep it in status.md, so line 1 changes only when the hub must act.
# A report that lands between a wake and the hub's re-arm is still news: each wake that names a report writes .reportseen-<lane> (the stamp the
# hub was shown) in the lane state folder, and a new watcher starts from it (when it is under LANE_WATCH_SEEN_MAX_SECS, default 1800, old) instead
# of from the moment it starts.
# Which lanes are watched: the names given, or, with none, EVERY live lane (the rows of `lane.sh --status` that are not dead and
# have a tmux window), read again on every poll so a lane started after the watcher began is covered for reports, end of turn and
# stuck dialogs. (18 Oct: armed with no names it watched nothing, and four reports and a stuck dialog sat unseen for hours.)
# Lanes never auto-compact (his rule, 20 Oct): when a watched lane's context passes its OWN recycle line (`lane.sh --recycle-point <lane>`,
# in thousand tokens: set at start with --recycle-at, else the default for its kind of work), the poll starts `lane.sh --recycle <lane>` in
# the background, mid-turn if need be (the lane finishes the step in hand, writes handoff.md, gets /clear and a re-prime message) and logs it
# to ~/.claude/state/lanes/recycle.log. It does not wait for the turn to end: a long turn outruns Claude Code's backstop (21 Oct: one
# 15-minute turn went 120K to 168K and compacted). That wakes nobody; only a FAILED recycle does. The check runs on every poll, whatever the lane's last turn did. A lane is tried again after
# LANE_RECYCLE_RETRY_SECS (default 300), counted across re-armed watchers (.recycle-<lane>.tried). LANE_RECYCLE_K, when set, overrides every lane's line (tests, emergencies).
# A lane that keeps refilling its line is not recycled forever: when it has been recycled LANE_THRASH_COUNT (default 3) times inside LANE_THRASH_SECS
# (default 1800), the watcher stops recycling it and wakes the hub ONCE per episode with one line (lane, count, times). 25-26 Oct: pokeys-site
# was recycled every 5 to 7 minutes for hours because each fresh session loaded about 90K of context before it did anything.
# A lane that wrote handoff.md, said "ready to recycle" and was not recycled (its context is under its line, so nothing else will) wakes the hub
# once per handoff.md after LANE_ORPHAN_SECS (default 600) of sitting idle. 26 Oct 1:30 AM: a queued copy of the request reached a freshly
# primed lane at 95K; it wrote handoff.md, stopped, and sat idle until 8:28 AM.
# A lane that has sat idle LANE_WATCH_JOB_IDLE_MIN (default 30) minutes with a background job still running wakes the hub ONCE per idle stretch
# (job_idle_check): "idle: <lane> has been idle N min with a background job still running". A job counts when lane.sh --status --full says so
# ("report stale: a background job it started has not finished", from lane_report_state.py; a job with no word back after 12 hours is
# treated as lost and does not count). Before 5 Nov 2026 (inbox triage item 39) a running job made a lane "fine" indefinitely, so a download or
# CI wait that hung, or a job whose finish notice never arrived, kept the lane idle with nobody asked. The idle time is read from the pane's
# "· done H:MM" line; the mark .jobidle-<lane> in the lane state folder keeps a re-armed watcher from naming the same stretch twice, and a lane
# with no job is not looked up more than once every 5 minutes.
# A lane idle LANE_WATCH_IDLE_REMIND_MIN (default 20) minutes whose report.md line 1 is not QUEUE EMPTY or BLOCKED is named AGAIN every that many
# minutes while it stays idle (idle_remind_check; 6 Nov 2026, Jonathan: "we have to big of a backlog to idle on smoke breaks"). Before, the
# "ended a turn without a new report" line came once per ended turn and a lane with a backlog could then sit idle for hours. The standing rule
# every lane is given (lane.sh): finish a step, start the next item; end a turn only with QUEUE EMPTY or BLOCKED (who/what) on line 1.
# Also left alone (line1_rests): a first word that names who the lane waits on (HUB-ACT, NEED-HUB, STEP-FOR-JONATHAN) and DONE with no counted ask.
# Every new or changed report is also recorded by the dispatcher's post-reports (nothing goes to his chat; the hub reads the report when this wakes it).
# Run it with the Bash tool's run_in_background; its exit re-invokes the hub, which reports and re-arms it.
# A watcher parses what it reads: no child of it may send long output to a file (big_output.py reads this).
export AFA_BIG_OUT=0
set -u
# Where this script lives, found before any cd (a relative $0 stops resolving once the folder changes).
_lw_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# Leave the folder the watcher was started from. 6 Nov 2026: the hub started it inside a lane worktree, the lane removed the worktree after
# its merge, and the watcher died with "getcwd: cannot access parent directories". Nothing here needs the starting folder.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then cd "${HOME:-/}" 2>/dev/null || cd / || true; fi

# Does this pane text show a dialog that waits on the hub? Reads the pane on stdin.
# Claude Code menus say "Enter to select"; its permission prompts say "Do you want to proceed"
# and end with "Esc to cancel". The last 15 lines are checked, so a prompt with options below it still matches.
dialog_waiting() {
  tail -15 | grep -qE 'Enter to select|Do you want to proceed|Esc to cancel'
}

# Claude Code's "Read outside the working directories" dialog: prints the path it asks about (or "a file outside its working directories" when
# none is visible) and returns 0; returns 1 for any other pane. Reads the pane on stdin. lane.sh gives each lane its own hand-off folder and its
# repo's worktree checkouts as --add-dir, so this dialog means something else is being read; the hub answers it, and sees the path here.
dialog_read_path() {
  local text path
  text=$(tail -30)
  grep -qiE 'outside the working director' <<<"$text" || return 1
  path=$(sed -nE 's/.*Read\(([^)]+)\).*/\1/p' <<<"$text" | tail -1)
  [ -n "$path" ] || path=$(grep -viE 'outside the working director|Esc to cancel|Enter to select|Do you want' <<<"$text" | grep -oE '(~|/)[A-Za-z0-9._~+@/-]*[A-Za-z0-9._~+@-]' | head -1)
  printf '%s\n' "${path:-a file outside its working directories}"
}

# Return the last completed-turn line visible in this pane.
turn_done_line() {
  # Keep only up to the done time: the tail ("· 5 monitors still running", spaces, a box edge) is redrawn as jobs end.
  tail -15 | sed -nE 's/^(.*· done [0-9]{1,2}:[0-9]{2}( [AP]M)?).*$/\1/p' | tail -1
}

turn_done_time() {
  turn_done_line | sed -nE 's/.*· done ([0-9]{1,2}:[0-9]{2}).*/\1/p'
}

# Recycling: a lane past its recycle line, with no dialog waiting and not already being recycled, is handed to
# `lane.sh --recycle` in the background, whether or not its turn is running. lane.sh holds .recycle-<lane>.lock while it works; the watcher reads that lock and the log.
lane_sh="${LANE_SH:-$HOME/.claude/scripts/lane.sh}"
recycle_dir="${LANE_RECYCLE_DIR:-$HOME/.claude/state/lanes}"
recycle_log="$recycle_dir/recycle.log"
recycle_retry=${LANE_RECYCLE_RETRY_SECS:-300}
recycle_locked() { [ -d "$recycle_dir/.recycle-$1.lock" ]; }
# Seconds since the watcher (this one or an earlier one the hub re-armed) last started a recycle for the lane; big if never. Kept in a
# file, not in memory, because the hub re-arms the watcher every few minutes and a retry timer that dies with it never spaces anything.
recycle_tried_age() {
  local m; m=$(cat "$recycle_dir/.recycle-$1.tried" 2>/dev/null)
  [[ "$m" =~ ^[0-9]+$ ]] || { echo 999999; return; }
  echo $(( $(date +%s) - m ))
}
# recycle_line <lane>: the lane's own line in K tokens (LANE_RECYCLE_K overrides it for every lane; 100 if lane.sh cannot say).
recycle_line() {
  local k=${LANE_RECYCLE_K:-}
  [[ "$k" =~ ^[0-9]+$ ]] || k=$("$lane_sh" --recycle-point "$1" 2>/dev/null)
  [[ "$k" =~ ^[0-9]+$ ]] || k=100
  echo "$k"
}
# recycle_due <lane>, the lane's pane on stdin: 0 when its context is at or over its line, a working lane included.
recycle_due() {
  local l=$1 pane c; pane=$(cat)
  recycle_locked "$l" && return 1
  printf '%s\n' "$pane" | dialog_waiting && return 1
  c=$("$lane_sh" --ctx "$l" 2>/dev/null)
  [[ "$c" =~ ^[0-9]+$ ]] && [ "$c" -ge "$(recycle_line "$l")" ]
}
# Thrash guard. recycle_thrash <lane> (no subshell: it sets hit and other) is true, and says so once per episode, when the lane has been
# recycled thrash_count times within thrash_secs by the rows of the recycle log. The marker (.thrash-<lane>) holds the newest recycled stamp
# of the episode it named, so the same episode is not named twice but a later one is. A lane whose stop marker (.recycle-stop-<lane>, written by
# lane.sh when the third recycle in the window succeeded, or a recycle was held three times by running subagents) stands is never recycled by
# the watcher, however old its rows are: it returns 0 at once and says nothing, because the FAILED row lane.sh wrote has told the hub already.
thrash_count=${LANE_THRASH_COUNT:-3}
thrash_secs=${LANE_THRASH_SECS:-1800}
recycle_thrash() {
  local l=$1 quiet=${2:-} now ts e newest='' times='' n=0 mark
  [ ! -e "$recycle_dir/.recycle-stop-$l" ] || return 0
  [ -f "$recycle_log" ] || return 1
  now=$(date +%s)
  while IFS= read -r ts; do
    e=$(date -d "$ts" +%s 2>/dev/null) || continue
    [ $(( now - e )) -le "$thrash_secs" ] || continue
    n=$(( n + 1 )); newest=$ts
    times+="${times:+, }$(date -d "@$e" '+%-I:%M %p')"
  done < <(command grep -a $'\trecycled\t' "$recycle_log" | awk -F'\t' -v n="$l" '$2 == n {print $1}')
  [ "$n" -ge "$thrash_count" ] || return 1
  [ "$quiet" != quiet ] || return 0   # the scheduled pass only holds back: naming it is left to a watcher the hub has armed
  mark="$recycle_dir/.thrash-$l"
  if [ "$(cat "$mark" 2>/dev/null)" != "$newest" ]; then
    printf '%s' "$newest" > "$mark"
    echo "thrash: lane $l was recycled $n times in $(( thrash_secs / 60 )) min ($times); not recycling it again until that window passes. Each fresh session fills its line again, so what it loads at the start (prompt.md, handoff.md, files it re-reads) is too big for the line: read them and shrink them, or recycle it by hand with lane.sh --recycle $l."
    hit=1; other=1
  fi
  return 0
}

# Unseen handoff. orphan_check <lane> <pane> (no subshell: it sets hit and other) names a lane that wrote handoff.md after its last recycle,
# said "ready to recycle", and has sat idle since, with its context under its line so recycle_due will never fire. Once per handoff.md.
orphan_secs=${LANE_ORPHAN_SECS:-600}
orphan_check() {
  local l=$1 pane=$2 rep dir h hm now last ts e c k mark
  recycle_locked "$l" && return 0
  printf '%s\n' "$pane" | dialog_waiting && return 0
  command grep -aqP '…\s*\(\d+(m \d+)?s' <<<"$pane" && return 0   # still working
  rep=$(report_of "$l"); [ -n "$rep" ] || return 0
  dir=$(dirname "$rep"); h="$dir/handoff.md"
  hm=$(stat -c %Y "$h" 2>/dev/null) || return 0
  now=$(date +%s)
  [ $(( now - hm )) -ge "$orphan_secs" ] || return 0
  last=0
  if [ -f "$recycle_log" ]; then
    ts=$(command grep -a $'\trecycled\t' "$recycle_log" | awk -F'\t' -v n="$l" '$2 == n {t = $1} END {print t}')
    [ -z "$ts" ] || { e=$(date -d "$ts" +%s 2>/dev/null) && last=$e; }
  fi
  [ "$hm" -gt "$last" ] || return 0
  tmux capture-pane -p -S -120 -t "lanes:$l" 2>/dev/null | command grep -aqi 'ready to recycle' || return 0
  c=$("$lane_sh" --ctx "$l" 2>/dev/null); k=$(recycle_line "$l")
  [[ "$c" =~ ^[0-9]+$ ]] || return 0
  [ "$c" -lt "$k" ] || return 0   # over its line the recycle path takes it
  mark="$recycle_dir/.orphan-$l"
  [ "$(cat "$mark" 2>/dev/null)" != "$hm" ] || return 0
  printf '%s' "$hm" > "$mark"
  echo "handoff: lane $l wrote handoff.md at $(date -d "@$hm" '+%-I:%M %p') but was never recycled (context ${c}K, under its ${k}K line) and sits idle; recycle it with lane.sh --recycle $l or tell it to carry on. Named once for this handoff."
  hit=1; other=1
}

# A step for Jonathan with no link. step_url_check <lane> (no subshell: it sets hit and other) looks at the lane's report.md: when line 1 starts
# with STEP-FOR-JONATHAN and holds no https:// URL, it names the lane to the hub ("step without URL: <lane>") and types a note into the lane
# through lane.sh --send asking it to rewrite line 1 with the URL. Once per distinct line 1, so a lane that rewrites it is checked again.
# 26 Oct 11:52 AM, Jonathan, after a relayed lane step reached him with no link: "so you are asking me to do something with out givng me the
# url to do it, again".
step_url_check() {
  local l=$1 rep first sum mark
  rep=$(report_of "$l"); [ -n "$rep" ] || return 0
  first=$(head -n 1 "$rep" 2>/dev/null)
  case "$first" in STEP-FOR-JONATHAN*) ;; *) return 0 ;; esac
  case "$first" in *https://*) return 0 ;; esac
  sum=$(printf '%s' "$first" | cksum | cut -d' ' -f1)
  mark="$recycle_dir/.stepurl-$l"
  [ "$(cat "$mark" 2>/dev/null)" != "$sum" ] || return 0
  printf '%s' "$sum" > "$mark"
  echo "step without URL: $l (line 1 of $rep starts STEP-FOR-JONATHAN and has no https:// link); asked the lane to rewrite it"
  "$lane_sh" --send "$l" "Hub: line 1 of your report.md is a STEP-FOR-JONATHAN with no link. Rewrite line 1 so it carries the full https:// URL of the exact page, says which browser (the fleet Chromium and its tab title, or his own) and how he will know it is done; bring the fleet tab to the front first. He cannot act on a step without the URL." >/dev/null 2>&1 || true
  hit=1; other=1
}

# A report time that is ahead of the clock. stamp_ahead_check <lane> (no subshell: it sets hit and other) reads the report's own time from ONE fixed
# place, the head of line 1 of the lane's report.md: 1 to 4 status words in capitals (WORKING, QUEUE EMPTY, STEP-FOR-JONATHAN), then separators
# (space , : ; | - and the long dashes), at most one "( ... )" group and "at" or "as of", then the H:MM AM|PM. Anything else before the first time
# (lower-case words, "until", "by", a second group, a time inside the group) means the line has no stamp and is left alone, and so does a target
# word in capitals among the status words (BY, UNTIL, TILL, DEADLINE, DUE, BEFORE, ETA). The stamp is compared with the file's own modification time
# (the clock when the lane wrote it). More than LANE_WATCH_STAMP_SLACK minutes (default 2) ahead, within the next 12 hours: it names the lane to the
# hub ("stamp ahead: <lane> wrote <stamp> at <mtime>") and types the correction into the lane through lane.sh --send, then says whether that send
# worked. A stamp that is behind the clock is left alone (a lane may report work that finished earlier). Once per distinct line 1.
# 6 Nov 2026: a lane wrote "WORKING 8:30 AM" at 8:13 AM; the rule (every time comes from date at the moment of writing) was already in every lane's rules.
# 6 and 11 Nov 2026: false alarms from the first time found anywhere on the line, a schedule time ("nightly 3:17 AM Eastern") and the time of a
# quoted mail deep in the line; each woke the hub and told a correct report to change. Hence the fixed place.
stamp_ahead_check() {
  local l=$1 rep first tok pre h m ap sm mt now_h now_m now_min diff sum mark slack=${LANE_WATCH_STAMP_SLACK:-2} wrote rc msg
  local w='[A-Z][A-Z-]*' sep='( |,|:|;|\||—|–|-)*' grp='\([^()]*\)'
  local re="^ *$w( $w){0,3}$sep($grp$sep)?(at |as of )?((1[0-2]|0?[1-9]):[0-5][0-9] ?[AaPp][Mm])"
  local LC_ALL=C   # [A-Z] means the 26 plain capitals on every machine (under en_US.UTF-8 it also took accented capitals)
  rep=$(report_of "$l"); [ -n "$rep" ] || return 0
  first=$(head -n 1 "$rep" 2>/dev/null)
  [[ $first =~ $re ]] || return 0
  tok=${BASH_REMATCH[6]}   # group 6: the time (1 more words, 2 and 4 separators, 3 the group, 5 at/as of)
  [ -n "$tok" ] || return 0
  # A target word in capitals among the status words ("WAITING UNTIL 7:00 PM", "DONE BY 5:00 PM", "WORKING ETA 7:00 PM") makes the time a
  # target, not the report's own time. Only the words before any "( ... )" group are looked at.
  pre=" ${first%%"$tok"*}"; pre=${pre%%\(*}   # cut at the first "(" so the bracket itself is never the character before a target word
  [[ ! $pre =~ [^A-Z-](BY|UNTIL|TILL|DEADLINE|DUE|BEFORE|ETA)([^A-Z-]|$) ]] || return 0
  h=${tok%%:*}; h=$((10#$h)); m=${tok#*:}; m=$((10#${m:0:2})); ap=${tok: -2}
  case "$ap" in [Pp][Mm]) [ "$h" -eq 12 ] || h=$((h + 12)) ;; *) [ "$h" -ne 12 ] || h=0 ;; esac
  sm=$((h * 60 + m))
  mt=$(stat -c %Y "$rep" 2>/dev/null) || return 0
  read -r now_h now_m < <(date -d "@$mt" '+%-H %-M')
  now_min=$((now_h * 60 + now_m))
  diff=$(( (sm - now_min + 1440) % 1440 ))
  [ "$diff" -gt "$slack" ] && [ "$diff" -lt 720 ] || return 0
  sum=$(printf '%s' "$first" | cksum | cut -d' ' -f1)
  mark="$recycle_dir/.stampahead-$l"
  [ "$(cat "$mark" 2>/dev/null)" != "$sum" ] || return 0
  printf '%s' "$sum" > "$mark"
  wrote=$(date -d "@$mt" '+%-I:%M %p')
  msg="Hub: line 1 of your report.md says $tok, but the clock read $wrote when you wrote it. Run date '+%-I:%M %p' at the moment you write a time and rewrite line 1 with that. Every time in a report, hand-off or note comes from date, never from memory or an estimate. Put the report's own time right after the status word (WORKING 3:05 PM ...); the watcher reads only that place."
  rc=0; "$lane_sh" --send "$l" "$msg" >/dev/null 2>&1 || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "stamp ahead: $l wrote $tok at $wrote (line 1 of $rep is $diff minutes ahead of the clock); sent the lane its correction"
  else
    echo "stamp ahead: $l wrote $tok at $wrote (line 1 of $rep is $diff minutes ahead of the clock); could NOT send the lane its correction (lane.sh exit $rc)"
  fi
  hit=1; other=1
}

# job_idle_check <lane> <pane>: names a lane idle for LANE_WATCH_JOB_IDLE_MIN minutes or more whose background job is still running. Idle
# means no dialog, no spinner, no recycle under way, and a "· done H:MM" line that old (a time of day later than now is yesterday's).
job_idle_check() {
  local l=$1 pane=$2 dl t de now mark prev note mins limit=${LANE_WATCH_JOB_IDLE_MIN:-30}
  [[ "$limit" =~ ^[0-9]+$ ]] || limit=30
  recycle_locked "$l" && return 0
  printf '%s\n' "$pane" | dialog_waiting && return 0
  command grep -aqP '…\s*\(\d+(m \d+)?s' <<<"$pane" && return 0   # still working
  dl=$(printf '%s\n' "$pane" | turn_done_line)
  t=$(printf '%s\n' "$dl" | sed -nE 's/.*· done ([0-9]{1,2}:[0-9]{2}( [AP]M)?).*/\1/p')
  [ -n "$t" ] || return 0
  now=$(date +%s)
  de=$(date -d "$t" +%s 2>/dev/null) || return 0
  [ "$de" -le "$now" ] || de=$(( de - 86400 ))
  mins=$(( (now - de) / 60 ))
  [ "$mins" -ge "$limit" ] || return 0
  mark="$recycle_dir/.jobidle-$l"
  prev=$(cat "$mark" 2>/dev/null)   # "<done epoch> named" or "<done epoch> nojob <checked epoch>"
  case "$prev" in
    "$de named") return 0 ;;
    "$de nojob "*) [ $(( now - ${prev##* } )) -ge 300 ] || return 0 ;;
  esac
  note=$(stale_note "$l")
  case "$note" in
    *"background job"*)
      printf '%s named' "$de" > "$mark"
      echo "idle: $l has been idle ${mins} min with a background job still running; check the job (read the lane's last screen) and ask it or stop it. Named once for this idle stretch."
      hit=1; other=1 ;;
    *) printf '%s nojob %s' "$de" "$now" > "$mark" ;;
  esac
}

# idle_remind_check <lane> <pane>: a lane idle LANE_WATCH_IDLE_REMIND_MIN (default 20) minutes whose report.md line 1 is not QUEUE EMPTY or
# BLOCKED is named again, and again every LANE_WATCH_IDLE_REMIND_MIN minutes while it stays idle. 6 Nov 2026 (Jonathan: "we have to big of a
# backlog to idle on smoke breaks"): the "ended a turn without a new report" line above comes once per ended turn, so a lane that stopped with
# a backlog still queued sat idle for hours after that one line. A lane may end a turn only with QUEUE EMPTY or BLOCKED (who/what) on line 1
# (the standing rule lane.sh puts in the start and re-prime text); those two are left alone, and so is a line line1_rests accepts. Idle means what job_idle_check means: no dialog,
# no spinner, no recycle under way, and a "· done H:MM" line that old. The mark .idleremind-<lane> holds "<done epoch> <last named epoch>",
# so a re-armed watcher keeps the spacing and a new turn (new done time) starts a new stretch. LANE_WATCH_IDLE_REMIND_MIN=0 turns it off.
#
# line1_rests <report line 1>: true when the line says the lane has nothing to start, so the idle reminder leaves it alone. Four kinds:
#   QUEUE EMPTY or BLOCKED at the start (the standing rule's two endings);
#   EMPTY <time> | ..., what the lane prompts have always asked for as the end-of-queue line (6 Nov: six lanes wrote it and were named every 20 min);
#   a first word that names who the lane waits on: HUB or JONATHAN as one hyphen-joined part of it (HUB-ACT, NEED-HUB, READY-FOR-HUB,
#     STEP-FOR-JONATHAN). The hub is woken once by the report itself; naming the lane again every 20 minutes woke it for nothing (8 Nov,
#     a lane waiting on "HUB-ACT 3:23 AM | ..." was named at 24 minutes and after);
#   DONE as the whole first word, unless the line still counts an ask ("needs hub 2", "needs Jonathan 1"; a count of 0 is no ask). 6 Nov: two
#     finished lanes that wrote DONE were named, one after 197 minutes, and the hub had to ask each to rewrite the line as QUEUE EMPTY.
# The first word is capitals joined by hyphens and must end at a character that is not a letter, digit, underscore or hyphen, so HUBRIS,
# DONE-ISH and "Hub: ..." are not taken. LC_ALL=C is set for this function only: [A-Z] means the 26 plain capitals on every machine.
line1_rests() {
  local first=$1 w low LC_ALL=C
  case "$first" in "QUEUE EMPTY"*|"BLOCKED"*|"EMPTY"|"EMPTY "*|"EMPTY:"*|"EMPTY|"*) return 0 ;; esac
  [[ $first =~ ^([A-Z]+(-[A-Z]+)*)([^A-Za-z0-9_-]|$) ]] || return 1
  w=${BASH_REMATCH[1]}
  case "-$w-" in *-HUB-*|*-JONATHAN-*) return 0 ;; esac
  [ "$w" = DONE ] || return 1
  low=${first,,}
  [[ ! $low =~ needs?\ (the\ )?(hub|jonathan):?\ *[1-9] ]]
}

idle_remind_check() {
  local l=$1 pane=$2 dl t de now mins limit=${LANE_WATCH_IDLE_REMIND_MIN:-20} mark prev rep first
  [[ "$limit" =~ ^[0-9]+$ ]] || limit=20
  [ "$limit" -gt 0 ] || return 0
  recycle_locked "$l" && return 0
  printf '%s\n' "$pane" | dialog_waiting && return 0
  command grep -aqP '…\s*\(\d+(m \d+)?s' <<<"$pane" && return 0   # still working
  dl=$(printf '%s\n' "$pane" | turn_done_line)
  t=$(printf '%s\n' "$dl" | sed -nE 's/.*· done ([0-9]{1,2}:[0-9]{2}( [AP]M)?).*/\1/p')
  [ -n "$t" ] || return 0
  now=$(date +%s)
  de=$(date -d "$t" +%s 2>/dev/null) || return 0
  [ "$de" -le "$now" ] || de=$(( de - 86400 ))
  mins=$(( (now - de) / 60 ))
  [ "$mins" -ge "$limit" ] || return 0
  rep=$(report_of "$l")
  first=$(head -n 1 "$rep" 2>/dev/null)
  line1_rests "$first" && return 0
  mark="$recycle_dir/.idleremind-$l"
  prev=$(cat "$mark" 2>/dev/null)   # "<done epoch> <named epoch>"
  if [ "${prev%% *}" = "$de" ] && [ -n "${prev##* }" ] && [ $(( now - ${prev##* } )) -lt $(( limit * 60 )) ]; then return 0; fi
  mkdir -p "$recycle_dir" 2>/dev/null
  printf '%s %s' "$de" "$now" > "$mark" 2>/dev/null || true
  echo "idle: $l has been idle ${mins} min and its report line 1 is not QUEUE EMPTY or BLOCKED, so it should have a next item; tell it to start the next item in its queue (or to write QUEUE EMPTY or BLOCKED (who/what) on line 1). Named again every ${limit} min while it stays idle."
  hit=1; other=1
}

# green_pr_check <lane>: a pull request named under "## Hub merges" in the lane's report.md (a line holding `gh pr merge <n> ... -R <owner>/<repo>`)
# whose checks have all passed wakes the hub once with "green: ...", so it merges without waiting to be asked (issue 278). The watcher never merges
# itself: the hub keeps the one place where the client-repo rule is applied. Only Jonathan's own repos get the line: the owner must be
# TortoiseWolfe or TurtleWolfe, and the repo's name must not appear in ~/repos/hub/client-repos.txt (the last folder of each path) or in
# column 2 of client-senders.tsv; if either list cannot be read the check says nothing (a client PR must never be called ready by mistake).
# "Green" means `gh pr checks` lists at least one check, none pending, failing or cancelled, and the PR is still open. The mark
# .green-<lane>-<owner>-<repo>-<n> in $recycle_dir keeps it to one wake per PR, even across re-armed watchers. gh is asked at most once per
# LANE_WATCH_GREEN_EVERY seconds (default 120) per watcher, and LANE_WATCH_GREEN_EVERY=off turns the check off.
green_last=0
green_own_repo() { # green_own_repo <owner/repo>: 0 only for one of his own repos
  local owner=${1%%/*} name=${1##*/} hub="${HOME}/repos/hub" p x
  case "$owner" in TortoiseWolfe|TurtleWolfe) ;; *) return 1 ;; esac
  [ -f "$hub/client-repos.txt" ] && [ -r "$hub/client-repos.txt" ] && [ -f "$hub/client-senders.tsv" ] && [ -r "$hub/client-senders.tsv" ] || return 1
  while IFS= read -r p; do
    p=${p%%#*}; p=${p//[[:space:]]/}; [ -n "$p" ] || continue
    [ "${p##*/}" = "$name" ] && return 1
  done < "$hub/client-repos.txt"
  while IFS=$'\t' read -r _ x _; do
    x=${x##*/}; [ -n "$x" ] && [ "${x,,}" = "${name,,}" ] && return 1
  done < "$hub/client-senders.tsv"
  return 0
}
green_pr_check() {
  local l=$1 every=${LANE_WATCH_GREEN_EVERY:-120} rep line rest tok pr repo mark out total skipped now
  [ "$every" = off ] && return 0
  [[ "$every" =~ ^[0-9]+$ ]] || every=120
  now=$(date +%s)
  [ $(( now - green_last )) -ge "$every" ] || return 0
  rep=$(report_of "$l"); [ -n "$rep" ] && [ -f "$rep" ] || return 0
  while IFS= read -r line; do
    [[ "$line" == *"gh pr merge"* ]] || continue
    pr=""; rest=${line#*gh pr merge}
    for tok in $rest; do tok=${tok//[\`\'\".,;:)(]/}; tok=${tok#\#}; if [[ "$tok" =~ ^[0-9]+$ ]]; then pr=$tok; break; fi; done
    repo=""; [[ "$line" =~ (-R|--repo)[\ =]+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+) ]] && repo=${BASH_REMATCH[2]}
    [ -n "$pr" ] && [ -n "$repo" ] || continue
    green_own_repo "$repo" || continue
    mark="$recycle_dir/.green-$l-${repo//\//-}-$pr"
    [ -e "$mark" ] && continue
    green_last=$now
    [ "$(timeout 30 gh pr view "$pr" -R "$repo" --json state -q .state 2>/dev/null)" = OPEN ] || continue
    out=$(timeout 30 gh pr checks "$pr" -R "$repo" 2>/dev/null) || true
    [ -n "$out" ] || continue
    printf '%s\n' "$out" | command grep -qiE '(^|[[:space:]])(pending|fail|cancel)|no checks' && continue
    printf '%s\n' "$out" | command grep -qiE '(^|[[:space:]])pass' || continue
    total=$(printf '%s\n' "$out" | command grep -c .)
    skipped=$(printf '%s\n' "$out" | command grep -ciE '(^|[[:space:]])skipp') || skipped=0
    mkdir -p "$recycle_dir" 2>/dev/null
    : > "$mark" 2>/dev/null || true
    echo "green: $l's pull request $pr in $repo has every check passed ($total checks, $skipped skipped) and is listed under Hub merges in its report; it is one of his own repos, so the standing rule is: gh pr merge $pr -R $repo --squash --delete-branch (the watcher does not merge)."
    hit=1; other=1
  done < <(awk '/^## Hub merges/{f=1; next} /^## /{f=0} f' "$rep")
}

# The recycle decision for one lane, shared by the poll loop and the scheduled pass (--recycle-once). recycle_pass <lane> <pane> [quiet]
# returns 0 when the lane is due: it started `lane.sh --recycle` in the background, or it is recycling too often and the thrash guard held
# it back (named to the hub unless quiet). It returns 1 when the lane is not due, was tried a moment ago, or has a recycle under way.
recycle_pass() {
  local l=$1 pane=$2 quiet=${3:-}
  [ "$(recycle_tried_age "$l")" -ge "$recycle_retry" ] || return 1
  printf '%s\n' "$pane" | recycle_due "$l" || return 1
  recycle_thrash "$l" "$quiet" && return 0   # a lane recycled again and again is named to the hub instead
  date +%s > "$recycle_dir/.recycle-$l.tried"
  setsid nohup timeout 1500 "$lane_sh" --recycle "$l" >>"$recycle_dir/recycle.out" 2>&1 </dev/null &
  return 0
}

# New "FAILED" rows in the recycle log since the last look (byte offset in $recycle_log_seen).
# The offset lives in a file ($recycle_dir/.recycle-log-seen), so a FAILED row written while no watcher was armed is still reported by the next
# one. 28 Oct 2026: a watcher started after the row and began at the log's end, and the failure was never named. No file (first run): start at the end.
recycle_log_seen_file="$recycle_dir/.recycle-log-seen"
recycle_log_seen=$(cat "$recycle_log_seen_file" 2>/dev/null || true)
[[ "$recycle_log_seen" =~ ^[0-9]+$ ]] || recycle_log_seen=$(stat -c %s "$recycle_log" 2>/dev/null || echo 0)
# Sets recycle_fail_out (one "lane: reason" per line); called without a subshell so the offset sticks.
recycle_failures() {
  local size; recycle_fail_out=''
  size=$(stat -c %s "$recycle_log" 2>/dev/null) || return 0
  [ "$size" -gt "$recycle_log_seen" ] || { recycle_log_seen=$size; printf '%s' "$size" > "$recycle_log_seen_file" 2>/dev/null; return 0; }
  recycle_fail_out=$(tail -c +"$((recycle_log_seen + 1))" "$recycle_log" | command grep -a $'\tFAILED\t' | awk -F'\t' '{print $2 ": " $4}')
  recycle_log_seen=$size
  printf '%s' "$size" > "$recycle_log_seen_file" 2>/dev/null
}

# Dead-pane alarm. dead_check <lane> (no subshell: it sets hit and other) names a lane whose window is open but whose pane is not running claude
# (lane.sh --pane says "dead <cmd>"), once per death: the mark file .dead-<lane> holds the command shown and is cleared when the pane runs claude
# again. A headless lane, or a lane.sh that cannot say, is never named.
dead_check() {
  local l=$1 st cmd mark="$recycle_dir/.dead-$1"
  st=$("$lane_sh" --pane "$l" 2>/dev/null)
  case "$st" in
    "dead "*)
      [ -e "$mark" ] && return 0
      cmd=${st#dead }
      printf '%s' "$cmd" > "$mark"
      echo "dead: $l (pane shows ${cmd:-nothing}); restart it fresh from its handoff.md"
      hit=1; other=1 ;;
    claude) rm -f "$mark" ;;
  esac
}

# Spend alarm. spend_check sets spend_out (one "<lane>\t<usd>\t<models>" per lane that has newly passed the limit today). It asks the ledger at
# most every LANE_WATCH_SPEND_SECS (default 300; the stamp is a file, so a re-armed watcher keeps the spacing) and names a lane once a day
# (.spend-alarm-<date> in $recycle_dir lists the lanes already named), so a lane that stays over the limit does not wake the hub again.
# Separately, the hub itself has a limit (HUB_SPEND_LIMIT, default 75).
spend_limit=${LANE_SPEND_LIMIT:-25}
hub_limit=${HUB_SPEND_LIMIT:-75}
spend_every=${LANE_WATCH_SPEND_SECS:-300}
spend_cmd=${LANE_SPEND_CMD:-"python3 $_lw_dir/lane_spend.py"}
spend_check() {
  local now last f out hub_out usd n fam col1 col2 col3
  spend_out=''
  now=$(date +%s); last=$(cat "$recycle_dir/.spend-checked" 2>/dev/null)
  [[ "$last" =~ ^[0-9]+$ ]] && [ $(( now - last )) -lt "$spend_every" ] && return 0
  printf '%s' "$now" > "$recycle_dir/.spend-checked"
  f="$recycle_dir/.spend-alarm-$(date +%F)"
  hub_out=$(timeout 90 $spend_cmd 2>/dev/null) || hub_out=''
  if [ -n "$hub_out" ]; then
    while IFS=$'\t' read -r col1 col2 col3; do
      [ "$col1" = hub ] || continue
      usd=$col2; fam=$col3
      [[ "$usd" =~ ^[0-9]+(\.[0-9]+)?$ ]] || continue
      [ "$(awk -v u="$usd" -v l="$hub_limit" 'BEGIN{print (u >= l)}')" = 1 ] || continue
      grep -qxF -- "hub" "$f" 2>/dev/null && continue
      printf 'hub\n' >> "$f"
      spend_out+="hub"$'\t'"$usd"$'\t'"$fam"$'\n'
    done <<<"$hub_out"
  fi
  out=$(timeout 90 $spend_cmd --over "$spend_limit" 2>/dev/null) || out=''
  if [ -n "$out" ]; then
    while IFS=$'\t' read -r col1 col2 col3; do
      usd=$col1; n=$col2; fam=$col3
      [[ "$usd" =~ ^[0-9]+(\.[0-9]+)?$ ]] || continue
      [ -n "$n" ] || continue
      [ "$(awk -v u="$usd" -v l="$spend_limit" 'BEGIN{print (u >= l)}')" = 1 ] || continue
      grep -qxF -- "$n" "$f" 2>/dev/null && continue
      printf '%s\n' "$n" >> "$f"
      spend_out+="$n"$'\t'"$usd"$'\t'"$fam"$'\n'
    done <<<"$out"
  fi
  find "$recycle_dir" -maxdepth 1 -name '.spend-alarm-*' -mtime +3 -delete 2>/dev/null
  return 0
}

# High-tier alarm. high_check <lane> (no subshell: it sets hit and other) names a live lane that is on opus or fable with no logged reason,
# once per lane per day (.high-alarm-<date> in $recycle_dir lists the lanes already named), with today's spend from the ledger.
high_check() {
  local l=$1 model reason f usd
  IFS=$'\t' read -r model reason <<<"$("$lane_sh" --tier "$l" 2>/dev/null)"
  case "${model,,}" in *opus*|*fable*) ;; *) return 0 ;; esac
  [ "${reason:--}" = - ] || return 0
  f="$recycle_dir/.high-alarm-$(date +%F)"
  grep -qxF -- "$l" "$f" 2>/dev/null && return 0
  printf '%s\n' "$l" >> "$f"
  usd=$(timeout 90 $spend_cmd 2>/dev/null | awk -F'\t' -v n="$l" '$2==n{print $1; exit}')
  [[ "$usd" =~ ^[0-9]+(\.[0-9]+)?$ ]] || usd=0
  echo "tier: lane $l is on $model with no logged --allow-high-tier reason, \$$usd API-equivalent today. Set it to sonnet: lane.sh --model $l sonnet (a recycle does this too), or give the reason: lane.sh --allow-high-tier \"<reason>\" --model $l $model. Named once today."
  find "$recycle_dir" -maxdepth 1 -name '.high-alarm-*' -mtime +3 -delete 2>/dev/null
  hit=1; other=1
}

# Hub context hand-off wake. The hub's transcripts are in ~/.claude/projects/<repo-with-dashes>/*.jsonl.
# Derive the folder from $HOME/repos the way Claude Code does (slashes to dashes). Wake once per session id
# using a stamp file $HOME/.claude/state/hub-handoff-woke.<session-id>. Reads AFA_HANDOFF_K (default 150, at the first natural break and never mid-task; Jonathan raised it from 330K on 11 Nov; auto-compact is at 367K).
# Calls context-warn.py --size <transcript> to get the token count.
hub_handoff_check() {
  local repos_path="${HOME}/repos"
  local project_folder="${repos_path//\//-}"
  # The leading dash stays: /home/x/repos becomes -home-x-repos. AFA_HUB_PROJECTS_DIR overrides (tests).
  local projects_dir="${AFA_HUB_PROJECTS_DIR:-${HOME}/.claude/projects/${project_folder}}"
  [ -d "$projects_dir" ] || return 0
  local newest=""
  local newest_mtime=0
  local f
  for f in "$projects_dir"/*.jsonl; do
    [ -f "$f" ] || continue
    local m; m=$(stat -c %Y "$f" 2>/dev/null) || continue
    if (( m > newest_mtime )); then
      newest_mtime=$m
      newest=$f
    fi
  done
  [ -n "$newest" ] || return 0
  local session_id="${newest##*/}"
  session_id="${session_id%.jsonl}"
  local stamp_file="${HOME}/.claude/state/hub-handoff-woke.${session_id}"
  [ -f "$stamp_file" ] && return 0
  local size
  size=$(python3 -I "${HOME}/.claude/hooks/context-warn.py" --size "$newest" 2>/dev/null)
  [[ "$size" =~ ^[0-9]+$ ]] || return 0
  local handoff_k=${AFA_HANDOFF_K:-150}
  [[ "$handoff_k" =~ ^[0-9]+$ ]] && (( handoff_k > 0 )) || handoff_k=150
  if (( size >= handoff_k * 1000 )); then
    local tokens_k=$(( (size + 500) / 1000 ))
    mkdir -p "$(dirname "$stamp_file")" 2>/dev/null
    printf '%s\n' "$(date +%s)" > "$stamp_file"
    echo "hub context: ${tokens_k}K, past the ${handoff_k}K hand-off line. Write the hand-off, run /session-prime, ask for a fresh terminal."
    exit 0
  fi
}

# Unanswered-question wake. The hub asked the owner something in the terminal and 15 minutes (LANE_WATCH_UNANSWERED_MIN; 0 or "off" turns
# it off) later he has not answered: the question goes to his phone assistant as one [CC>MUSE] note draft. Reads only the last 200 lines of
# the hub's newest transcript (same folder as hub_handoff_check). Fires when the last assistant text comes after the last typed user
# message (not a tool_result, not isMeta, not a "<" wrapper), its final paragraph outside fenced code holds a "?" or "your call" /
# "which do you want" / "do you want" / "should I", and it is old enough. A question card (AskUserQuestion) with no tool_result yet
# counts too, timed from the card. Named once per assistant uuid (.unanswered-<uuid> in $recycle_dir).
# Sets hit and other (call it inside the poll loop, after they are reset).
unanswered_check() {
  local min=${LANE_WATCH_UNANSWERED_MIN:-15}
  [ "$min" = off ] && return 0
  [[ "$min" =~ ^[0-9]+$ ]] && (( min > 0 )) || return 0
  local projects_dir="${AFA_HUB_PROJECTS_DIR:-${HOME}/.claude/projects/$(printf '%s' "${HOME}/repos" | tr / -)}"
  [ -d "$projects_dir" ] || return 0
  local newest="" newest_mtime=0 f m
  for f in "$projects_dir"/*.jsonl; do
    [ -f "$f" ] || continue
    m=$(stat -c %Y "$f" 2>/dev/null) || continue
    if (( m > newest_mtime )); then newest_mtime=$m; newest=$f; fi
  done
  [ -n "$newest" ] || return 0
  local res uuid clock age
  res=$(tail -n 200 "$newest" 2>/dev/null | python3 -I -c '
import sys, json, re, datetime
user_i = asst_i = -1
asst = None
card = None  # (entry, tool_use id) of a question card still waiting for its answer
for i, line in enumerate(sys.stdin):
    try:
        e = json.loads(line)
    except Exception:
        continue
    if not isinstance(e, dict) or e.get("isSidechain"):
        continue
    msg = e.get("message")
    c = msg.get("content") if isinstance(msg, dict) else None
    if e.get("type") == "user" and card and isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == card[1] for b in c):
            card = None
    if e.get("type") == "assistant" and isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                card = (e, b.get("id"))
    if e.get("type") == "user" and not e.get("isMeta"):
        texts = []
        if isinstance(c, str):
            texts = [c]
        elif isinstance(c, list):
            texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
        texts = [t for t in texts if isinstance(t, str) and t.strip() and not t.lstrip().startswith("<")]
        if texts:
            user_i = i
    elif e.get("type") == "assistant" and isinstance(c, list):
        texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str) and b.get("text").strip()]
        if texts:
            asst_i, asst = i, (e, "\n".join(texts))
def stamp(e):
    try:
        t = datetime.datetime.fromisoformat(str(e.get("timestamp")).replace("Z", "+00:00"))
    except Exception:
        sys.exit(0)
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    age = int((datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() // 60)
    uuid = re.sub(r"[^A-Za-z0-9_-]", "", str(e.get("uuid", "")))
    if not uuid:
        sys.exit(0)
    lt = t.astimezone()
    clock = "%d:%02d %s" % (lt.hour % 12 or 12, lt.minute, "AM" if lt.hour < 12 else "PM")
    print("%s\t%d\t%s" % (uuid, age, clock))
    sys.exit(0)
if card is not None:
    stamp(card[0])  # an open question card counts whatever text came before it
if asst is None or asst_i < user_i:
    sys.exit(0)
e, text = asst
blocks, cur, fenced = [], [], False
for ln in text.splitlines():
    if ln.lstrip().startswith("```"):
        fenced = not fenced
        continue
    if fenced:
        continue
    if ln.strip():
        cur.append(ln)
    elif cur:
        blocks.append(cur); cur = []
if cur:
    blocks.append(cur)
if not blocks:
    sys.exit(0)
last = "\n".join(blocks[-1]).lower()
if not ("?" in last or any(p in last for p in ("your call", "which do you want", "do you want", "should i"))):
    sys.exit(0)
stamp(e)
' 2>/dev/null) || return 0
  [ -n "$res" ] || return 0
  IFS=$'\t' read -r uuid age clock <<<"$res"
  [[ "$age" =~ ^[0-9]+$ ]] && [ -n "$uuid" ] || return 0
  (( age >= min )) || return 0
  [ -e "$recycle_dir/.unanswered-$uuid" ] && return 0
  mkdir -p "$recycle_dir" 2>/dev/null
  : > "$recycle_dir/.unanswered-$uuid"
  find "$recycle_dir" -maxdepth 1 -name '.unanswered-*' -mtime +3 -delete 2>/dev/null
  echo "unanswered: your question to the owner at $clock has had no reply for $age min; send it to Hatch as one [CC>MUSE] note draft (one question, its link, what yes and no do), then carry on with work that does not need the answer."
  hit=1; other=1
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

# The stamp the hub was last shown for a lane's report (see the header). seen_stamp_read <lane> prints it when the file is fresh, else nothing.
seen_max_secs=${LANE_WATCH_SEEN_MAX_SECS:-1800}
seen_stamp_read() {
  local f="$recycle_dir/.reportseen-$1" m
  m=$(stat -c %Y "$f" 2>/dev/null) || return 0
  [ $(( $(date +%s) - m )) -le "$seen_max_secs" ] || return 0
  cat "$f" 2>/dev/null
}
seen_stamp_write() { mkdir -p "$recycle_dir" 2>/dev/null; printf '%s' "$2" > "$recycle_dir/.reportseen-$1" 2>/dev/null || true; }
# routine_report <report.md>: 0 when line 1 starts HOURLY or STATUS (status for the record, not a request for the hub).
routine_report() {
  case "$(head -n 1 "$1" 2>/dev/null)" in HOURLY*|STATUS*) return 0 ;; esac
  return 1
}

# Dead-watcher alarm. A hub watcher is a bash running lane_watch.sh that is not --recycle-once (the same match as hooks/lane-watch-guard.py).
# A watcher writes its pid and process start time to $recycle_dir/.lane_watch.pid when it starts, before it watches anything. That file lives
# in the machine's state folder, not a Claude Code session folder, so it survives /clear. It is found with kill -0 and a start-time check,
# never by reading a process command line (a lane's command line can carry a prompt).
# LANE_WATCH_PROCLIST, when set, stands in for a `pgrep -af lane_watch.sh` listing (tests). LANE_WATCH_LISTING_FAIL=1 stands in for a pgrep
# that could not list processes (tests). watcher_running returns 0 when a watcher runs, 1 when none does, and 2 when the answer is unknown.
process_start_time() { # process_start_time <pid>: field 22 of /proc/<pid>/stat, the clock ticks at which the process started
  local rest
  [ -r "/proc/$1/stat" ] || return 1
  rest=$(<"/proc/$1/stat") || return 1
  # comm (field 2) is in parentheses and may contain spaces, so read the fields after the last ") ".
  rest=${rest##*') '}
  set -- $rest
  [ "$#" -ge 20 ] && printf '%s' "${20}"
}
watcher_running() {
  local pid_file="$recycle_dir/.lane_watch.pid" pid start now out rc recycled
  if [ -f "$pid_file" ]; then
    read -r pid start < "$pid_file" 2>/dev/null || { pid=; start=; }
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      now=$(process_start_time "$pid") || now=
      # An older pid file has no start time; accept the live pid. A recycled pid does not match the stored start time.
      if [ -z "$start" ] || [ -z "$now" ] || [ "$start" = "$now" ]; then return 0; fi
    fi
  fi
  [ "${LANE_WATCH_LISTING_FAIL:-0}" = 1 ] && return 2
  if [ -n "${LANE_WATCH_PROCLIST+x}" ]; then
    out=$LANE_WATCH_PROCLIST
    printf '%s\n' "$out" | sed -E 's/^[0-9]+ //' | command grep -E '^(/usr/bin/|/bin/)?bash [^ ]*/\.claude/scripts/lane_watch\.sh( |$)' | command grep -v -- '--recycle-once' | command grep -q .
    return
  fi
  # Plain pgrep prints pids only, so no command line is read or printed. A watcher and the 15-minute --recycle-once pass share the
  # first pattern; the second call names the pass, so it is never mistaken for a watcher.
  out=$(pgrep -f '^(/usr/bin/|/bin/)?bash [^ ]*/\.claude/scripts/lane_watch\.sh( |$)' 2>/dev/null)
  rc=$?
  [ "$rc" -le 1 ] || return 2
  [ "$rc" -eq 1 ] && return 1
  recycled=$(pgrep -f '^(/usr/bin/|/bin/)?bash [^ ]*/\.claude/scripts/lane_watch\.sh --recycle-once( |$)' 2>/dev/null) || recycled=
  for pid in $out; do
    case " $recycled " in *" $pid "*) continue ;; esac
    return 0
  done
  return 1
}
# hub_watcher_check <open lane count>: called by the scheduled pass. With no lane open, or a watcher running, it clears its marks. With lanes open
# and no watcher for LANE_WATCH_DEAD_SECS (default 600, so two passes 15 minutes apart, not the gap while the hub re-arms), it writes ONE "Hub:" line
# in the hub inbox and sends ONE desktop ping per gap. 30 Oct 7:30 PM: Claude Code's memory reaper killed the tower's watcher and nothing woke the
# hub for 12 hours while a pull request sat ready to merge.
hub_watcher_check() {
  local n=$1 first mark="$recycle_dir/.no-watcher" named="$recycle_dir/.no-watcher-named" now age mins rc
  if [ "${n:-0}" -le 0 ]; then rm -f "$mark" "$named"; return 0; fi
  watcher_running; rc=$?
  if [ "$rc" -eq 0 ]; then rm -f "$mark" "$named"; return 0; fi
  # A listing that failed is not a sighting of "no watcher": leave the marks alone so a normal re-arm gap is never named.
  [ "$rc" -eq 2 ] && return 0
  now=$(date +%s)
  if [ ! -e "$mark" ]; then printf '%s' "$now" > "$mark"; return 0; fi
  first=$(cat "$mark" 2>/dev/null); [[ "$first" =~ ^[0-9]+$ ]] || { printf '%s' "$now" > "$mark"; return 0; }
  age=$(( now - first ))
  [ "$age" -ge "${LANE_WATCH_DEAD_SECS:-600}" ] || return 0
  [ ! -e "$named" ] || return 0
  : > "$named"
  mins=$(( age / 60 ))
  "${LANE_WATCH_RECORD:-$_lw_dir/muse_poll_record.sh}" inbox "Hub: no lane_watch.sh watcher has been running for ${mins} min while ${n} lane(s) are open, so a report, a stuck dialog or a dead lane will not wake the hub. Arm one (lane_watch.sh, in the background). Named once per gap." >/dev/null 2>&1 || true
  timeout 30 python3 "${LANE_WATCH_TRAY:-$_lw_dir/openclaw_tray.py}" notify "Hub watcher is not running" "${n} lane(s) open, no watcher for ${mins} min" >/dev/null 2>&1 || true
}
# A client line open in the hub's inbox: the heartbeat is not stretched while one is.
client_line_open() {
  command grep -qE '^- \[ \] [^|]*\| Client (mail|alert|talk):' "${HUB_INBOX:-$HOME/.claude/state/hub-inbox.md}" 2>/dev/null
}

# When sourced (by the test script), stop here with the functions defined.
# "report stale: ..." when lane.sh --status says the lane's report.md is not its last word (input arrived after it, or a background job is
# still running), else nothing. 19 Oct: a lane was closed on "report:yes idle" while it was mid-work.
stale_note() {
  "$HOME/.claude/scripts/lane.sh" --status --full 2>/dev/null | awk -v n="$1" '$1 == n && $2 == "report:stale" {f = 1; next} f && /report stale:/ {sub(/^[[:space:]]+/, ""); print; exit} f && !/^[[:space:]]/ {exit}'
}
[[ "${BASH_SOURCE[0]}" != "$0" ]] && return 0

max=45; check=''; recycle_once=0
turn_grace=${LANE_WATCH_TURN_GRACE:-300}
idle_max=${LANE_WATCH_IDLE_MAX_MIN:-120}
while [ $# -gt 0 ]; do
  case "$1" in
    --recycle-once) recycle_once=1; shift ;;
    --max-min) max=$2; shift 2 ;;
    --check) check=$2; shift 2 ;;
    *) break ;;
  esac
done
lanes=("$@")
auto=0; [ ${#lanes[@]} -eq 0 ] && auto=1
start_ts=$(date +%s)
declare -A seen seen_turn recycling
report_of() { ls -t "$HOME"/.claude/state/lanes/*-"$1"/report.md 2>/dev/null | head -1; }
stamp() { local f; f=$(report_of "$1"); [ -n "$f" ] && stat -c %Y "$f" || echo none; }
watch_lane() { # watch_lane <name> [fresh]: remember its report stamp and last finished turn; fresh = a lane that began after the watcher
  local l=$1 f shown
  seen[$l]=$(stamp "$l")
  shown=$(seen_stamp_read "$l")   # what the hub was last shown, when that was minutes ago: a report since then is still news
  [ -z "$shown" ] || seen[$l]=$shown
  if [ "${2:-}" = fresh ] && f=$(report_of "$l") && [ -n "$f" ] && [ "$(stat -c %Y "$f")" -ge "$start_ts" ]; then
    seen[$l]=none   # its report was written before the first poll saw the lane: still news
  fi
  seen_turn[$l]=$(tmux capture-pane -p -t "lanes:$l" 2>/dev/null | turn_done_line)
}
# The live lanes now: rows of lane.sh --status that start in column one and are not "dead", and that have a tmux window.
live_lanes() {
  local wins; wins=$(tmux list-windows -t lanes -F '#{window_name}' 2>/dev/null) || return 0
  "$HOME/.claude/scripts/lane.sh" --status --full 2>/dev/null | awk '!/^[[:space:]]/ && NF >= 2 && $2 !~ /^dead/ {print $1}' |
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
# --recycle-once: the scheduled pass (openclaw_status_refresh.sh runs it from the 15-minute Windows task). One look at every live lane, the same
# recycle decision as the poll loop (its own line, no dialog, no recycle under way, the retry spacing, the thrash guard), then exit. It wakes
# nobody and prints nothing: a recycle that fails is a FAILED row in recycle.log for whichever watcher is armed. It exists because the hub
# arms a watcher only while a session is open: 26 Oct, between two hub sessions nothing was armed and scout-run-2 went 128K to 163K and
# compacted on its own.
if [ "$recycle_once" = 1 ]; then
  open_n=0
  while read -r l; do
    [ -n "$l" ] || continue
    open_n=$(( open_n + 1 ))
    recycle_pass "$l" "$(tmux capture-pane -p -t "lanes:$l" 2>/dev/null)" quiet || true
  done < <(live_lanes)
  hub_watcher_check "$open_n"
  exit 0
fi

# A real watcher announces itself before it watches: the pid file is how a later pass (or the Stop hook) finds it without reading a
# command line and without knowing which Claude Code session started it. Starting also ends any gap a previous pass marked, so a watcher
# that ran between two 15-minute passes is never named as missing. A stale file is harmless because watcher_running checks the start time.
mkdir -p "$recycle_dir" 2>/dev/null || true
printf '%s %s\n' "$$" "$(process_start_time $$)" > "$recycle_dir/.lane_watch.pid" 2>/dev/null || true
rm -f "$recycle_dir/.no-watcher" "$recycle_dir/.no-watcher-named" 2>/dev/null || true
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
talk_wake="$HOME/.claude/state/client-tag.wake"
talk_stamp() { stat -c %Y "$talk_wake" 2>/dev/null || echo none; }
talk_seen=$(talk_stamp)
# A note landing in this machine's own inbox file: peer-inbox.md on any machine but NX-01 (the tower's poller queues the other machine's and
# Hatch's notes there), hub-inbox.md on NX-01. 1 Nov: twelve tower notes sat unseen from 7:45 AM to 3 PM because nothing woke the hub.
# The lines already reported live in a state file, so one line wakes the hub once, even across watcher restarts; the file is rewritten to
# the lines still open, so a ticked line is forgotten. The first run (no state file yet) records what is open without waking.
notes_file() {
  if [ -n "${LANE_WATCH_NOTES_FILE:-}" ]; then printf '%s' "$LANE_WATCH_NOTES_FILE"; return; fi
  local m; m=$(cat "$HOME/.config/afa/machine" 2>/dev/null)
  case "$m" in ""|NX-01) printf '%s' "$HOME/.claude/state/hub-inbox.md" ;; *) printf '%s' "$HOME/.claude/state/peer-inbox.md" ;; esac
}
notes_seen_file="${LANE_WATCH_NOTES_SEEN:-$HOME/.claude/state/lane-watch-notes-seen.txt}"
notes_out=""
notes_check() {
  notes_out=""
  local f; f=$(notes_file)
  [ -r "$f" ] || return 0
  local cur; cur=$(command grep -E '^- \[ \] ' "$f" 2>/dev/null) || cur=""
  mkdir -p "$(dirname "$notes_seen_file")" 2>/dev/null || return 0
  if [ ! -e "$notes_seen_file" ]; then printf '%s\n' "$cur" >"$notes_seen_file"; return 0; fi
  notes_out=$(printf '%s\n' "$cur" | awk 'NR==FNR { seen[$0]; next } NF && !($0 in seen)' "$notes_seen_file" -)
  printf '%s\n' "$cur" >"$notes_seen_file.new" && mv "$notes_seen_file.new" "$notes_seen_file"
}
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
# The weekly budget reading failed. usage_calibrate.js writes its outcome to usage-calibrate-status.json; the Windows task that runs it hides the
# script's exit code (conhost --headless), so a Cloudflare check or a signed-out page used to leave the budget gauge stale with nothing to say so.
# A failed outcome (ok:false) is named once per distinct failure, kept as a checksum mark in $recycle_dir, and the hub wakes. A later success
# clears the mark so the next failure is named again. 6 Nov 2026: the gauge sat at the 30 Oct reading; Task Scheduler said Last Result 0.
calibrate_status="${USAGE_CALIBRATE_STATUS:-$HOME/.config/ledger/usage-calibrate-status.json}"
check_calibrate() {
  local line sum mark="$recycle_dir/.calibrate-failed"
  [ -f "$calibrate_status" ] || return 0
  line=$(python3 -I - "$calibrate_status" <<'PY' 2>/dev/null
import json, sys
d = json.load(open(sys.argv[1]))
if d.get("ok") is False:
    print("budget reading failed: exit %s (%s) at %s" % (d.get("code"), str(d.get("reason", ""))[:200], d.get("at")))
PY
  )
  if [ -z "$line" ]; then rm -f "$mark"; return 0; fi
  sum=$(printf '%s' "$line" | cksum | cut -d' ' -f1)
  [ "$(cat "$mark" 2>/dev/null)" != "$sum" ] || return 0
  printf '%s' "$sum" > "$mark"
  echo "$line"
  exit 0
}
check_bridge
check_calibrate
check_outbox
check_undelivered
check_open_questions
hub_handoff_check
printf '%s' "$recycle_log_seen" > "$recycle_log_seen_file" 2>/dev/null || true   # first run: the offset starts here
end=$(( $(date +%s) + max * 60 ))
stretched=0
# all_idle: 0 when no watched lane is working, waiting on a dialog or being recycled (the same signs orphan_check reads).
all_idle() {
  local l pane
  for l in "${lanes[@]}"; do
    recycle_locked "$l" && return 1
    pane=$(tmux capture-pane -p -t "lanes:$l" 2>/dev/null) || return 1
    printf '%s\n' "$pane" | dialog_waiting && return 1
    command grep -aqP '…\s*\(\d+(m \d+)?s' <<<"$pane" && return 1
  done
  return 0
}
while :; do
  check_bridge
  check_calibrate
  check_outbox
  check_undelivered
  check_open_questions
  hub_handoff_check
  refresh_watched
  hit=0; other=0
  unanswered_check
  for l in "${lanes[@]}"; do
    s=$(stamp "$l")
    if [ "$s" != "${seen[$l]}" ] && [ "$s" != none ] && routine_report "$(report_of "$l")"; then
      seen[$l]=$s; seen_stamp_write "$l" "$s"   # HOURLY / STATUS on line 1: routine, wakes nobody
    elif [ "$s" != "${seen[$l]}" ]; then
      seen_stamp_write "$l" "$s"
      note=$(stale_note "$l")
      echo "report: $l ($(report_of "$l"))${note:+ -- $note; do not close it} -- a report does not end a lane; keep it open (it recycles itself at its own line, $(recycle_line "$l")K)"; hit=1; other=1
      step_url_check "$l"
      stamp_ahead_check "$l"
      # a report changes what is in flight: refresh OpenClaw's status digest, detached so the exit is not held up
      setsid nohup timeout 90 "$HOME/.claude/scripts/openclaw_status_refresh.sh" >>"$HOME/.claude/state/status-refresh.log" 2>&1 &
      # and put the report itself into Jonathan's chat, after a short settle so a report still being written is posted whole
      setsid nohup bash -c 'sleep "$1"; timeout 120 python3 "$2" post-reports' _ "${LANE_WATCH_SETTLE:-12}" "$dispatch" >/dev/null 2>&1 &
    fi
    if ! tmux list-windows -t lanes -F '#{window_name}' 2>/dev/null | grep -qx "$l"; then
      echo "gone: $l"; hit=1; other=1
    else
      pane=$(tmux capture-pane -p -t "lanes:$l" 2>/dev/null)
      if printf '%s\n' "$pane" | dialog_waiting; then
        # Once per dialog (5 Nov 2026: a stale "Teach auto mode" nag sat in night-watch all night and every re-armed watcher exited on it at once).
        # The mark .dialog-<lane> lives in $recycle_dir, so a re-armed watcher skips a dialog the hub has already been told about and keeps
        # watching; it is cleared when the pane shows no dialog, so the next dialog is named again.
        if [ ! -e "$recycle_dir/.dialog-$l" ]; then
          : > "$recycle_dir/.dialog-$l"
          if rp=$(printf '%s\n' "$pane" | dialog_read_path); then echo "waiting on a dialog: $l (waiting on a read of $rp)"
          else echo "waiting on a dialog: $l"; fi
          hit=1; other=1
        fi
      else
        rm -f "$recycle_dir/.dialog-$l"
      fi
      dead_check "$l"
      high_check "$l"
      green_pr_check "$l"
      done_line=$(printf '%s\n' "$pane" | turn_done_line)
      if recycle_locked "$l"; then
        recycling[$l]=1; seen_turn[$l]=$done_line   # the hand-off turn is ours, not news
      else
        if [ "${recycling[$l]:-}" = 1 ]; then recycling[$l]=0; seen_turn[$l]=$done_line; fi
        # The recycle check comes BEFORE the end-of-turn checks and does not depend on them. (21 Oct 2:16-2:25 PM: coop-site went 100K to
        # 153K with no recycle because the check sat in the else-branch of the turn checks, and a lane answering Monitor events ends a
        # turn nearly every poll, so almost every pass took a turn branch and skipped it.)
        if ! recycle_pass "$l" "$pane"; then
          orphan_check "$l" "$pane"
          job_idle_check "$l" "$pane"
          idle_remind_check "$l" "$pane"
        fi
        if [ -n "$done_line" ] && [ "$done_line" != "${seen_turn[$l]}" ] && [ "$("$lane_sh" --turn "$l" 2>/dev/null)" = monitor ]; then
          seen_turn[$l]=$done_line   # a Monitor progress event answered with no question: news to nobody (19 Oct 11:59 PM)
        elif [ -n "$done_line" ] && [ "$done_line" != "${seen_turn[$l]}" ]; then
          rstamp=$(stamp "$l")
          if [[ "$rstamp" =~ ^[0-9]+$ ]] && [ $(( $(date +%s) - rstamp )) -lt "$turn_grace" ]; then
            seen_turn[$l]=$done_line   # the turn that wrote the report just ended: the report is the news, not this
          else
            done_time=$(printf '%s\n' "$done_line" | sed -nE 's/.*· done ([0-9]{1,2}:[0-9]{2}).*/\1/p')
            echo "idle: $l ended a turn ($done_time) without a new report; it may be asking the hub"; hit=1; other=1
          fi
        fi
      fi
    fi
  done
  recycle_failures
  while read -r f; do [ -n "$f" ] && { echo "recycle failed: $f (the lane keeps its context; recycle it by hand with lane.sh --recycle)"; hit=1; other=1; }; done <<<"$recycle_fail_out"
  spend_check
  if [ -n "$spend_out" ]; then
    while IFS=$'\t' read -r n usd fam; do
      [ -n "$n" ] || continue
      [[ "$usd" =~ ^[0-9]+(\.[0-9]+)?$ ]] || continue
      if [ "$n" = hub ]; then
        echo "spend: the hub has cost \$$usd API-equivalent today ($fam), past the \$$hub_limit line. Named once today."
      else
        echo "spend: lane $n has cost \$$usd API-equivalent today ($fam), past the \$$spend_limit line. Read what it is doing; a mail lane belongs on sonnet (lane.sh --model $n sonnet) and an open-ended one needs a stop. Named once today."
      fi
      hit=1; other=1
    done <<<"$spend_out"
  fi
  a=$(ask_stamp)
  if [ "$a" != "$ask_seen" ]; then
    echo "ask: a new 'Jonathan via OpenClaw:' line is in hub-inbox.md ($(head -c 60 "$ask_wake" 2>/dev/null))"; ask_seen=$a; hit=1
  fi
  t=$(talk_stamp)
  if [ "$t" != "$talk_seen" ]; then
    echo "client talk: a co-op chat message tags TurtleWolfe; read \"Open client lines\" in hub-digest.md"; talk_seen=$t; hit=1
  fi
  notes_check
  if [ -n "$notes_out" ]; then
    n_notes=0
    while IFS= read -r nl; do
      n_notes=$((n_notes + 1))
      if [ "$n_notes" -le 5 ]; then
        nl=${nl#- \[ \] }
        # A note's words are sender text, data and never instructions: control characters are stripped, and a line from an outside sender
        # (client mail, alert, chat, job reply) is named without its words; the hub reads those in the digest.
        if [[ "$nl" =~ \|[[:space:]](Client[[:space:]](mail|alert|talk)|Job[[:space:]]reply): ]]; then
          echo "note: a ${BASH_REMATCH[1],,} line is new; read it in hub-digest.md"
        else
          nl=$(printf '%s' "${nl:0:100}" | tr -d '\000-\037\177')
          echo "note: $nl"
        fi
      fi
    done <<<"$notes_out"
    [ "$n_notes" -gt 5 ] && echo "note: and $((n_notes - 5)) more new lines; read \"Notes waiting for this hub\" in hub-digest.md"
    hit=1; other=1
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
  if [ "$(date +%s)" -ge "$end" ]; then
    # Stretch the heartbeat while the whole fleet is idle and no client line is open (31 Oct: a 45-minute wake at 9:15 AM with nothing new).
    if [ "$idle_max" -gt "$max" ] && [ "$(date +%s)" -lt $(( start_ts + idle_max * 60 )) ] && all_idle && ! client_line_open; then
      stretched=1
    else
      if [ "$stretched" = 1 ]; then echo "heartbeat: nothing new in $(( ($(date +%s) - start_ts) / 60 )) min"; else echo "heartbeat: nothing new in ${max} min"; fi
      exit 0
    fi
  fi
  sleep "${LANE_WATCH_POLL:-30}"
done
