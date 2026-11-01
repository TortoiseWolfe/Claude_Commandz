#!/usr/bin/env bash
# The always-on half of the Claude <-> Hatch (Muse) notes channel.
#
# Windows Task Scheduler runs this every minute ("\AFA\MuseNotesPoll"). Between 08:00 and 22:00 local
# (Hatch's own watcher window) it always runs; after hours only while Jonathan is working: he typed in
# a Claude session within the hour (user-active.stamp, from hooks/muse-drafts.sh) or a note moved
# either way within two hours. How often it actually checks scales with
# use (Jonathan, 2026-10-03: "the more we use it the tighter it gets, the less we use it the more it
# backs off"): right after a note either way it checks every minute; as the exchange goes quiet it
# waits a quarter of the quiet time between checks, up to every 30 minutes. The clock is
# muse-active.stamp, touched by `muse_poll_record.sh active` whenever a session writes Hatch a note,
# and here whenever a Hatch note arrives. A tick that isn't due exits before any model runs. Before this existed, Claude
# looked for Hatch's notes only when an interactive session started or the user typed, so
# questions sat for hours and Jonathan ended up relaying between the two agents (2026-10-03).
#
#   0. Client mail (2026-10-12): muse_poll_client_check.sh lists new messages from the senders in the
#      private hub/client-senders.tsv (metadata only; IDs in client-mail-seen.txt are dropped), and a Sonnet
#      pass queues one "Client mail:" or "Client alert:" line per client in hub-inbox.md through
#      muse_poll_record.sh (which never queues an ID twice). /prep then starts an absorb lane for each.
#   1. A cheap Haiku pass only LOOKS: is there a [MUSE>CC] note, or a [NCC-74656>NX-01] note from
#      Claude Code on the second tower (2026-10-10), that nobody has handled?
#   2. Only if so, a Sonnet pass HANDLES it per the agent-notes skill:
#      - answers what it can from the repos, read-only;
#      - an answer it isn't sure of goes to Jev first (muse_jev.py): sent only if every cited file exists
#        and Jev passes two narrow checks at 0.90+, else queued (Jonathan, 2026-10-10: "run it through JEV if iffy");
#      - replies to Hatch with a [CC>MUSE] draft;
#      - records the note's ID (the processed file is the record; the connector refuses to label drafts since 20 Oct, so a label failure is expected);
#      - before queuing it looks for an answer already on record (the tower's latest notes, and the hub
#        roadmap through muse_poll_roadmap.sh); a question only the second tower can check is forwarded to it;
#      - anything that needs real work or Jonathan's approval goes to
#        ~/.claude/state/hub-inbox.md. Only a decision or approval from Jonathan also gets a desktop ping
#        ("Needs your OK"); work a Claude session can do waits silently. The ~/repos session surfaces
#        that file (hooks/muse-drafts.sh).
#
# It cannot send mail, push, deploy, delete, or change settings: the allow and deny lists below
# say so, and print mode denies anything else.
set -u

queue_weekly_transcripts() {
  local mode=$1 moment=${2:-} config inbox log day weekday clock pretty
  config=${WEEKLY_TRANSCRIPTS_TSV:-$HOME/.config/afa/weekly-transcripts.tsv}
  inbox=${HUB_INBOX:-${MUSE_INBOX:-$HOME/.claude/state/hub-inbox.md}}
  log=$HOME/.claude/state/muse-poll.log
  [[ -f "$config" ]] || return 0

  [[ -n "$moment" ]] || moment=$(date '+%F %H:%M')
  day=${moment%% *}
  clock=${moment#* }
  if [[ ! "$moment" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}$ ]] ||
      ! date -d "$moment" '+%F %H:%M' >/dev/null 2>&1; then
    echo "Invalid weekly dry-run time: $moment" >&2
    return 2
  fi
  weekday=$(date -d "$moment" +%u) || return 1
  pretty=$(date -d "$day" '+%b %-d') || return 1

  while IFS=$'\t' read -r row_day after title slug || [[ -n "${row_day:-}${after:-}${title:-}${slug:-}" ]]; do
    [[ -n "${row_day:-}" && "$row_day" != \#* ]] || continue
    [[ "$row_day" == "$weekday" && "$after" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || continue
    [[ "$clock" > "$after" || "$clock" == "$after" ]] || continue
    [[ -n "$title" && -n "$slug" ]] || continue

    local rendered marker line
    rendered=${title//\{date\}/$pretty}
    rendered=${rendered//|//}           # "|" separates inbox fields
    marker="weekly-${slug}-${day}"
    [[ -f "$inbox" ]] && grep -Fq -- "$marker" "$inbox" && continue
    line="- [ ] $moment | Job: Weekly transcript: fetch \"$rendered\" from YouTube | for the ~/repos director: run lane ${slug}-transcript-${day} | $marker"
    if [[ "$mode" == print ]]; then
      printf '%s\n' "$line"
    else
      ( flock -w 30 8 && printf '%s\n' "$line" >>"$inbox" ) 8>>"$(dirname "$inbox")/hub-inbox.lock"
      printf '%s queued weekly transcript %s\n' "$(date '+%F %T')" "$marker" >>"$log"
    fi
  done <"$config"
}

if [[ "${1:-}" == --weekly-dry-run ]]; then
  if (( $# == 1 )); then
    queue_weekly_transcripts print
  elif (( $# == 3 )); then
    queue_weekly_transcripts print "$2 $3"
  else
    echo "Usage: $0 --weekly-dry-run [YYYY-MM-DD HH:MM]" >&2
    exit 2
  fi
  exit $?
fi

# The fleet heartbeat (fleet-up.sh): stamped every minute, before any gate below can exit. This is the one job that runs every minute here.
[ -x "$HOME/.claude/scripts/fleet-up.sh" ] && timeout 100 "$HOME/.claude/scripts/fleet-up.sh" --heartbeat >/dev/null 2>&1

H=$(date +%-H)
exec 9>"${MUSE_POLL_LOCK:-/tmp/muse-poll.lock}"   # a test passes its own, so it never waits on the live poller
flock -n 9 || exit 0

STATE=$HOME/.claude/state
ACTIVE=$STATE/muse-active.stamp
LAST=$STATE/muse-poll-last-check.stamp
now=$(date +%s)
age() { if [[ -e "$1" ]]; then echo $((now - $(stat -c %Y "$1"))); else echo 999999; fi; }
night=""
if ! ((H >= 8 && H < 22)); then
  (($(age "$STATE/user-active.stamp") <= 3600 || $(age "$ACTIVE") <= 7200)) || exit 0
  night="after hours, "
fi
quiet=$(age "$ACTIVE")
FLOOR=${MUSE_POLL_FLOOR:-300}                  # 2026-10-16 (card 4): was 60 s; Hatch looks every 20 min, so 5 min keeps notes well inside that
want=$((quiet / 4)); ((want < FLOOR)) && want=$FLOOR; ((want > 1800)) && want=1800
(($(age "$LAST") < want - 15)) && exit 0      # not due yet (15 s allows for scheduler jitter)
mode="${night}quiet $((quiet / 60))m, every $((want / 60))m"
CLIENT_LAST=$STATE/muse-poll-client-last.stamp
PAYIDS=$STATE/client-payment-ids.txt           # the IDs of this look's payment-kind lines (see the client handler below)
CLIENT_EVERY=${MUSE_CLIENT_EVERY:-600}         # client mail looks at most every 10 min (it has a 2-day window, so nothing is missed)
export MUSE_POLL=1                             # our own claude runs don't count as Jonathan typing
touch "$LAST"
queue_weekly_transcripts queue

CLAUDE=${MUSE_CLAUDE:-$HOME/.local/bin/claude}
S=$HOME/.claude/scripts
LOG=$HOME/.claude/state/muse-poll.log
ts() { date '+%F %T'; }
cd $HOME/repos || exit 1
# Learning-loop baseline (spec 002): writes last week's record once, then this is a quick no-op. No model runs.
[[ -f "$S/learning_baseline.py" ]] && timeout 150 python3 "$S/learning_baseline.py" observe >/dev/null 2>>"$LOG"

# Mail index (29 Oct 2026): sorts every incoming message into a class (hub/mail-index/README.md), beside the client check below and
# without a model. It reads metadata, then the replies sweep reads each eligible job body once and discards it after plain-rule sorting;
# no body is logged or stored. It never sends, labels or deletes. New job rows go to the ScoutJob day lane, and ACTION/REVIEW replies
# get one hub-inbox line. A failure is logged and never stops the poll. Missing script (the hub repo not pulled yet) = skipped.
MAILIDX=${MAIL_INDEX_PY:-$HOME/repos/hub/scripts/mail_index.py}
MAILIDX_LAST=$STATE/muse-poll-mail-index-last.stamp
if [[ -f "$MAILIDX" ]] && (($(age "$MAILIDX_LAST") >= ${MUSE_MAIL_INDEX_EVERY:-600} - 15)); then
  touch "$MAILIDX_LAST"
  mnew=$(timeout 200 python3 -I "$MAILIDX" update 2>>"$LOG") && [[ -n "$mnew" ]] && sed "s/^/$(ts) mail index: /" <<<"$mnew" >>"$LOG"
  timeout 60 python3 -I "$MAILIDX" jobs-queue >>"$LOG" 2>&1
  timeout 120 python3 -I "$MAILIDX" replies >>"$LOG" 2>&1
fi

# The three claude calls below run in a lean folder, not in ~/repos (Jonathan, 29 Oct 2026, token study: 229 headless calls in 20 hours
# started in ~/repos and each loaded ~/repos/CLAUDE.md, the memory index and the skill list before its first word: 45-61K tokens, 60% of
# these calls' weekly cost). ~/.claude/state/lean sits outside ~/repos, so none of that loads; hub_lean.py --lean-dir makes it with the
# same lean project settings the hub has (plugins, Artifact tools and unused connectors off; Gmail stays on) and prints it. Measured
# first-turn context: 47.8K -> 12.9K (check), 47.6K -> 13.4K (handler), 46.6K -> 12.1K (client queuer), same output each time.
# --disable-slash-commands drops the skill listing (no pass invokes a skill; the handler reads the agent-notes SKILL.md by path).
# Everything the prompts name is an absolute path; the rules the workspace CLAUDE.md gave these passes are written into the prompts.
# If hub_lean.py is missing or fails, the plain folder still works (the lean project settings are just not in it).
LEAN=$(timeout 30 python3 -I "$S/hub_lean.py" --lean-dir 2>>"$LOG") && [[ -d "$LEAN" ]] || LEAN=${AFA_LEAN_DIR:-$HOME/.claude/state/lean}
mkdir -p "$LEAN" && cd "$LEAN" || exit 1
LEANFLAGS=(--disable-slash-commands)

# No Write or Edit in general: print mode denies any path not allowed below, and these keep the repos and this
# config unwritable even if a global allow rule appears. The one writable file is muse_jev.py's request
# (~/.local/state/muse-jev/request.json), because a bare `Write` deny would cover it too (tested 2026-10-10).
JEV_REQ=$HOME/.local/state/muse-jev/request.json
mkdir -p -m 700 "${JEV_REQ%/*}"
DENY="Bash(git push:*),Bash(git commit:*),Bash(rm:*),Bash(docker:*),Bash(curl:*),Bash(gh:*),\
Edit(/$HOME/repos/**),Edit(/$HOME/.claude/**),\
mcp__claude_ai_Gmail__delete_draft,mcp__claude_ai_Gmail__trash_message,mcp__claude_ai_Gmail__trash_thread,\
mcp__claude_ai_Gmail__update_draft,mcp__claude_ai_Gmail__mark_message_spam,mcp__claude_ai_Gmail__mark_thread_spam,\
NotebookEdit,WebFetch,WebSearch,Agent,Workflow,Bash(*--apply*),Bash(*--test-mode*)"

check=$(timeout 300 "$CLAUDE" -p "$(cat "$S/muse_poll.check.prompt")" --model haiku --max-turns 12 "${LEANFLAGS[@]}" \
  --permission-mode default --disallowedTools "$DENY" \
  --allowedTools "mcp__claude_ai_Gmail__list_drafts,mcp__claude_ai_Gmail__search_threads,Read(/$STATE/agent-notes-processed.txt)" 2>>"$LOG" | tail -1)
echo "$(ts) check ($mode): ${check:-<no output>}" >>"$LOG"

# Client mail: its own check and queue step, so it runs whether or not a Hatch note is waiting.
cnew=""
if (($(age "$CLIENT_LAST") >= CLIENT_EVERY - 15)); then
  touch "$CLIENT_LAST"
  : >"$PAYIDS"
  cnew=$(MUSE_CLIENT_PAYIDS="$PAYIDS" "$S/muse_poll_client_check.sh" 2>>"$LOG")
  echo "$(ts) client check: $([[ -n "$cnew" ]] && grep -c '^NEW ' <<<"$cnew" || echo 0) new" >>"$LOG"
fi
if [[ -n "$cnew" ]]; then
  # This pass reads client mail unattended, so an injected mail could steer it (29 Oct hub security call). Its grant is the least it needs:
  #  - Read of the one sender list, not the whole disk;
  #  - muse_poll_record.sh client-mail and client-seen only (never inbox, which writes free text, nor done, processed or archive);
  #  - the Gmail message reader and the Gusto script only when this look found a payment-kind line (PAYIDS is not empty), and then
  #    only the dry-run form "gusto_mark_paid.py match --amount ..." (DENY above refuses --apply and --test-mode anywhere). The
  #    script itself, told GUSTO_UNATTENDED=1, refuses --apply, --test-mode, another customer, and any --ref that is not in PAYIDS.
  #    Marking an invoice paid is a person's step (--apply, after arming); the pass only queues the outcome (--record).
  CALLOW="Read(/$HOME/repos/hub/client-senders.tsv),Bash($S/muse_poll_record.sh client-mail:*),Bash($S/muse_poll_record.sh client-seen:*)"
  CENV=()
  if [[ -s "$PAYIDS" ]]; then
    CALLOW+=",mcp__claude_ai_Gmail__get_message,Bash(python3 $S/gusto_mark_paid.py match --amount:*)"
    CENV=(GUSTO_UNATTENDED=1 GUSTO_QUEUED_IDS_FILE="$PAYIDS")
  fi
  hprompt=$(cat "$S/muse_poll.client-handle.prompt"); hprompt=${hprompt//\{\{HOME\}\}/$HOME}
  env "${CENV[@]}" timeout 300 "$CLAUDE" -p "$hprompt"$'\n\n'"$cnew" --model sonnet --max-turns 12 "${LEANFLAGS[@]}" \
    --permission-mode default --disallowedTools "$DENY" --allowedTools "$CALLOW" >>"$LOG" 2>&1
  echo "$(ts) client handler finished (exit $?)" >>"$LOG"
fi

# 27-28 Oct on the second machine: Gmail answered "Resource has been exhausted (quota)".
# The poller had already marked the exchange live (touched $ACTIVE), so it re-checked
# every minute and found the same note NEW again, forever. Three note ids came up NEW
# 12 times each in 2.5 hours. This block stops the endless retry: the handler runs only
# if the processed file grows; after 2 failed handler runs per id we log GAVE-UP, queue
# a hub-inbox line, and skip that id on later passes.
# Error from the check pass: empty, or Gmail quota/exhausted. Do not treat as "nothing new".
# Match without regard to upper or lower case: ERROR, Error, Quota, EXHAUSTED all count.
shopt -s nocasematch
if [[ -z "$check" ]] || [[ "$check" =~ error|exhausted|quota ]]; then
  shopt -u nocasematch
  echo "$(ts) POLL-ERROR check: $check" >>"$LOG"
  exit 0
fi
shopt -u nocasematch

# A quiet check answers NONE: nothing to do, and the handler must not run.
[[ "$check" == NEW* ]] || exit 0

# The check prints "NEW <id> [<id>...]" (see muse_poll.check.prompt). Extract ids.
# The ids come from a model that reads mail, so a note could carry shell wildcards or a "|".
# read -a splits without wildcard expansion, and only the shape of a Gmail draft or message
# id (letters, digits, dash, underscore, 6 to 40 long) is kept.
ids=()
read -r -a raw_ids <<<"${check#NEW }"
for id in "${raw_ids[@]}"; do
  [[ "$id" =~ ^[A-Za-z0-9_-]{6,40}$ ]] && ids+=("$id")
done

# A "NEW" answer with no usable id gives the failure counter nothing to count, so it could keep
# the hot phase and the handler running every minute forever. Log it and stop here instead.
if ((${#ids[@]} == 0)); then
  echo "$(ts) POLL-ERROR no usable note id: ${check:0:120}" >>"$LOG"
  exit 0
fi

# If every id on this check has already failed twice, give up and skip the handler.
FAIL_FILE="$STATE/muse-poll-failures.tsv"
gave_up_all=1
for id in "${ids[@]}"; do
  count=0
  if [[ -f "$FAIL_FILE" ]]; then
    count=$(awk -F'\t' -v i="$id" '$1 == i {print $2; exit}' "$FAIL_FILE")
    [[ -z "$count" ]] && count=0
  fi
  if ((count < 2)); then
    gave_up_all=0
    break
  fi
done
if ((gave_up_all == 1 && ${#ids[@]} > 0)); then
  exit 0
fi

# A note is waiting and we haven't given up: mark the exchange live so we keep polling
# every minute while the handler works. The failure counter below will stop the hot
# phase after two failed handler runs per id.
touch "$ACTIVE"

# Record processed count before handler runs.
processed_before=0
PROCESSED_FILE="$STATE/agent-notes-processed.txt"
[[ -f "$PROCESSED_FILE" ]] && processed_before=$(wc -l < "$PROCESSED_FILE")

# Run the handler (Sonnet).
ALLOW="Read,Grep,Glob,\
mcp__claude_ai_Gmail__list_drafts,mcp__claude_ai_Gmail__get_draft,mcp__claude_ai_Gmail__search_threads,\
mcp__claude_ai_Gmail__get_message,mcp__claude_ai_Gmail__get_thread,mcp__claude_ai_Gmail__create_draft,\
mcp__claude_ai_Gmail__label_message,\
Bash($S/muse_poll_record.sh:*),Bash($S/muse_poll_roadmap.sh),Bash(python3 $S/openclaw_tray.py notify:*),Bash(python3 $S/muse_jev.py request),Edit(/$JEV_REQ)"
timeout 1200 "$CLAUDE" -p "$(cat "$S/muse_poll.handle.prompt")" --model sonnet --max-turns 40 "${LEANFLAGS[@]}" \
  --permission-mode default --disallowedTools "$DENY" --allowedTools "$ALLOW" >>"$LOG" 2>&1
handler_exit=$?
echo "$(ts) handler finished (exit $handler_exit)" >>"$LOG"

processed_after=0
[[ -f "$PROCESSED_FILE" ]] && processed_after=$(wc -l < "$PROCESSED_FILE")

if ((processed_after > processed_before)); then
  # Handler recorded at least one note: clear failures for those ids.
  # Remove any ids that were just processed from the failures file.
  if [[ -f "$FAIL_FILE" && ${#ids[@]} -gt 0 ]]; then
    awk -F'\t' -v ids="${ids[*]}" 'BEGIN{split(ids,a," "); for(i in a) skip[a[i]]=1} !($1 in skip)' "$FAIL_FILE" >"$FAIL_FILE.tmp" && mv "$FAIL_FILE.tmp" "$FAIL_FILE"
  fi
else
  # Handler did not grow the processed file: count a failure per id.
  for id in "${ids[@]}"; do
    count=0
    if [[ -f "$FAIL_FILE" ]]; then
      count=$(awk -F'\t' -v i="$id" '$1 == i {print $2; exit}' "$FAIL_FILE")
      [[ -z "$count" ]] && count=0
    fi
    count=$((count + 1))
    # Add the id with its new count, or replace its old count.
    if [[ -f "$FAIL_FILE" ]] && awk -F'\t' -v i="$id" '$1 == i {found=1} END {exit !found}' "$FAIL_FILE"; then
      awk -F'\t' -v i="$id" -v c="$count" 'BEGIN{OFS="\t"} $1 == i {$2=c} {print}' "$FAIL_FILE" >"$FAIL_FILE.tmp" && mv "$FAIL_FILE.tmp" "$FAIL_FILE"
    else
      printf '%s\t%d\n' "$id" "$count" >>"$FAIL_FILE"
    fi
    # Log and queue the hub-inbox line only on the run where the count first reaches 2.
    if ((count == 2)); then
      echo "$(ts) GAVE-UP $id after 2 failed handler runs" >>"$LOG"
      "$S/muse_poll_record.sh" inbox "Hub: notes poller gave up on $id after 2 failed handler runs; read it by hand"
    fi
  done
fi
