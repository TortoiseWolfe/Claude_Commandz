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

H=$(date +%-H)
exec 9>/tmp/muse-poll.lock
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

# No Write or Edit in general: print mode denies any path not allowed below, and these keep the repos and this
# config unwritable even if a global allow rule appears. The one writable file is muse_jev.py's request
# (~/.local/state/muse-jev/request.json), because a bare `Write` deny would cover it too (tested 2026-10-10).
JEV_REQ=$HOME/.local/state/muse-jev/request.json
mkdir -p -m 700 "${JEV_REQ%/*}"
DENY="Bash(git push:*),Bash(git commit:*),Bash(rm:*),Bash(docker:*),Bash(curl:*),Bash(gh:*),\
Edit(/$HOME/repos/**),Edit(/$HOME/.claude/**),\
mcp__claude_ai_Gmail__delete_draft,mcp__claude_ai_Gmail__trash_message,mcp__claude_ai_Gmail__trash_thread,\
mcp__claude_ai_Gmail__update_draft,mcp__claude_ai_Gmail__mark_message_spam,mcp__claude_ai_Gmail__mark_thread_spam,\
NotebookEdit,WebFetch,WebSearch,Agent,Workflow"

check=$(timeout 300 "$CLAUDE" -p "$(cat "$S/muse_poll.check.prompt")" --model haiku --max-turns 12 \
  --permission-mode default --disallowedTools "$DENY" \
  --allowedTools "mcp__claude_ai_Gmail__list_drafts,mcp__claude_ai_Gmail__search_threads,Read" 2>>"$LOG" | tail -1)
echo "$(ts) check ($mode): ${check:-<no output>}" >>"$LOG"

# Client mail: its own check and queue step, so it runs whether or not a Hatch note is waiting.
cnew=""
if (($(age "$CLIENT_LAST") >= CLIENT_EVERY - 15)); then
  touch "$CLIENT_LAST"
  cnew=$("$S/muse_poll_client_check.sh" 2>>"$LOG")
  echo "$(ts) client check: $([[ -n "$cnew" ]] && grep -c '^NEW ' <<<"$cnew" || echo 0) new" >>"$LOG"
fi
if [[ -n "$cnew" ]]; then
  CALLOW="Read,Bash($S/muse_poll_record.sh:*)"
  hprompt=$(cat "$S/muse_poll.client-handle.prompt"); hprompt=${hprompt//\{\{HOME\}\}/$HOME}
  timeout 300 "$CLAUDE" -p "$hprompt"$'\n\n'"$cnew" --model sonnet --max-turns 12 \
    --permission-mode default --disallowedTools "$DENY" --allowedTools "$CALLOW" >>"$LOG" 2>&1
  echo "$(ts) client handler finished (exit $?)" >>"$LOG"
fi
[[ "$check" == NEW* ]] || exit 0
touch "$ACTIVE"                                # a Hatch note arrived: the exchange is live

ALLOW="Read,Grep,Glob,\
mcp__claude_ai_Gmail__list_drafts,mcp__claude_ai_Gmail__get_draft,mcp__claude_ai_Gmail__search_threads,\
mcp__claude_ai_Gmail__get_message,mcp__claude_ai_Gmail__get_thread,mcp__claude_ai_Gmail__create_draft,\
mcp__claude_ai_Gmail__label_message,\
Bash($S/muse_poll_record.sh:*),Bash($S/muse_poll_roadmap.sh),Bash(python3 $S/openclaw_tray.py notify:*),Bash(python3 $S/muse_jev.py request),Edit(/$JEV_REQ)"
timeout 1200 "$CLAUDE" -p "$(cat "$S/muse_poll.handle.prompt")" --model sonnet --max-turns 40 \
  --permission-mode default --disallowedTools "$DENY" --allowedTools "$ALLOW" >>"$LOG" 2>&1
echo "$(ts) handler finished (exit $?)" >>"$LOG"
