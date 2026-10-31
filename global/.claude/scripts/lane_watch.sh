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
#   - a watched lane's tmux window is stuck on a dialog (see dialog_waiting), i.e. it is waiting on the hub
#   - a watched lane's tmux window disappears
#   - a watched lane's window is still there but its pane is a bare shell (lane.sh --pane says "dead <cmd>"): the claude process is gone.
#     Named once per death ("dead: <lane> (pane shows <cmd>); restart it fresh from its handoff.md"); the mark is cleared when the pane runs
#     claude again. 28 Oct 2026: a lane killed its own claude with pgrep -f, sat at a bash prompt for 3 hours and nothing woke the hub.
#   - a recycle (see below) failed
#   - Jonathan asks from OpenClaw (a new "Jonathan via OpenClaw:" line in hub-inbox.md)
#   - a message of Jonathan's reached no lane and not the hub's list (openclaw_reconcile.py; it files each one with the hub as it wakes)
#   - a post for Jonathan's chat has failed to land for 15 minutes (openclaw_dispatch.py queue check). Every poll also tries the
#     outbox again in the background (queue pump), so a post that failed while Windows could not reach WSL lands once it can.
#   - the WSL-to-Windows bridge cannot be healed or still fails its 10 s probe (wsl_bridge_watchdog.py runs from every poll and raises the alert)
#   - a lane has cost over $LANE_SPEND_LIMIT (default 25) dollars of API-equivalent spend today (lane_spend.py, from the usage ledger),
#     once per lane per day. 21 Oct: $303 went in one day, $85 on one mail lane redrafting a reply, and nothing said so until night.
#   - a live lane is on opus or fable and its index row holds no --allow-high-tier reason (lane.sh --tier says "<model> TAB -"; a site-model
#     prompt counts as a reason). Named once per lane per day with its model and today's spend. 22 Oct: ten lanes ran on Opus all day, only
#     one was entitled, and the tier was only checked at start. Checked on every poll; a recycle also puts such a lane back on sonnet.
#   - --check 'cmd' exits 0 (an outside condition, e.g. a VOD now returns 404)
#   - --max-min minutes pass (default 45): exits with "heartbeat", so the hub reports even when nothing moved
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
# Every new or changed report is also recorded by the dispatcher's post-reports (nothing goes to his chat; the hub reads the report when this wakes it).
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
# of the episode it named, so the same episode is not named twice but a later one is.
thrash_count=${LANE_THRASH_COUNT:-3}
thrash_secs=${LANE_THRASH_SECS:-1800}
recycle_thrash() {
  local l=$1 quiet=${2:-} now ts e newest='' times='' n=0 mark
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
spend_cmd=${LANE_SPEND_CMD:-"python3 $(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lane_spend.py"}
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
# using a stamp file $HOME/.claude/state/hub-handoff-woke.<session-id>. Reads AFA_HANDOFF_K (default 100).
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
  local handoff_k=${AFA_HANDOFF_K:-100}
  [[ "$handoff_k" =~ ^[0-9]+$ ]] && (( handoff_k > 0 )) || handoff_k=100
  if (( size >= handoff_k * 1000 )); then
    local tokens_k=$(( (size + 500) / 1000 ))
    mkdir -p "$(dirname "$stamp_file")" 2>/dev/null
    printf '%s\n' "$(date +%s)" > "$stamp_file"
    echo "hub context: ${tokens_k}K, past the ${handoff_k}K hand-off line. Write the hand-off, run /session-prime, ask for a fresh terminal."
    exit 0
  fi
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

max=45; check=''; recycle_once=0
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
# --recycle-once: the scheduled pass (openclaw_status_refresh.sh runs it from the 15-minute Windows task). One look at every live lane, the same
# recycle decision as the poll loop (its own line, no dialog, no recycle under way, the retry spacing, the thrash guard), then exit. It wakes
# nobody and prints nothing: a recycle that fails is a FAILED row in recycle.log for whichever watcher is armed. It exists because the hub
# arms a watcher only while a session is open: 26 Oct, between two hub sessions nothing was armed and scout-run-2 went 128K to 163K and
# compacted on its own.
if [ "$recycle_once" = 1 ]; then
  while read -r l; do
    [ -n "$l" ] || continue
    recycle_pass "$l" "$(tmux capture-pane -p -t "lanes:$l" 2>/dev/null)" quiet || true
  done < <(live_lanes)
  exit 0
fi
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
hub_handoff_check
printf '%s' "$recycle_log_seen" > "$recycle_log_seen_file" 2>/dev/null || true   # first run: the offset starts here
end=$(( $(date +%s) + max * 60 ))
while :; do
  check_bridge
  check_outbox
  check_undelivered
  check_open_questions
  hub_handoff_check
  refresh_watched
  hit=0; other=0
  for l in "${lanes[@]}"; do
    s=$(stamp "$l")
    if [ "$s" != "${seen[$l]}" ]; then
      note=$(stale_note "$l")
      echo "report: $l ($(report_of "$l"))${note:+ -- $note; do not close it} -- a report does not end a lane; keep it open (it recycles itself at its own line, $(recycle_line "$l")K)"; hit=1; other=1
      step_url_check "$l"
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
        echo "waiting on a dialog: $l"; hit=1; other=1
      fi
      dead_check "$l"
      high_check "$l"
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
        fi
        if [ -n "$done_line" ] && [ "$done_line" != "${seen_turn[$l]}" ] && [ "$("$lane_sh" --turn "$l" 2>/dev/null)" = monitor ]; then
          seen_turn[$l]=$done_line   # a Monitor progress event answered with no question: news to nobody (19 Oct 11:59 PM)
        elif [ -n "$done_line" ] && [ "$done_line" != "${seen_turn[$l]}" ]; then
          done_time=$(printf '%s\n' "$done_line" | sed -nE 's/.*· done ([0-9]{1,2}:[0-9]{2}).*/\1/p')
          echo "idle: $l ended a turn ($done_time) without a new report; it may be asking the hub"; hit=1; other=1
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
