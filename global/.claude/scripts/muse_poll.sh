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
#   1. A cheap Haiku pass only LOOKS: is there a [MUSE>CC] note nobody has handled?
#   2. Only if so, a Sonnet pass HANDLES it per the agent-notes skill:
#      - answers what it can from the repos, read-only;
#      - replies to Hatch with a [CC>MUSE] draft;
#      - labels the note and records its ID;
#      - anything that needs real work or Jonathan's approval goes to
#        ~/.claude/state/muse-inbox.md, plus a desktop ping. Every interactive session
#        surfaces that file at start.
#
# It cannot send mail, push, deploy, delete, or change settings: the allow and deny lists below
# say so, and print mode denies anything else.
set -u
H=$(date +%-H)
exec 9>/tmp/muse-poll.lock
flock -n 9 || exit 0

STATE=/home/TurtleWolfe/.claude/state
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
want=$((quiet / 4)); ((want < 60)) && want=60; ((want > 1800)) && want=1800
(($(age "$LAST") < want - 15)) && exit 0      # not due yet (15 s allows for scheduler jitter)
mode="${night}quiet $((quiet / 60))m, every $((want / 60))m"
export MUSE_POLL=1                             # our own claude runs don't count as Jonathan typing
touch "$LAST"

CLAUDE=/home/TurtleWolfe/.local/bin/claude
S=/home/TurtleWolfe/.claude/scripts
LOG=/home/TurtleWolfe/.claude/state/muse-poll.log
ts() { date '+%F %T'; }
cd /home/TurtleWolfe/repos || exit 1

DENY="Bash(git push:*),Bash(git commit:*),Bash(rm:*),Bash(docker:*),Bash(curl:*),Bash(gh:*),\
mcp__claude_ai_Gmail__delete_draft,mcp__claude_ai_Gmail__trash_message,mcp__claude_ai_Gmail__trash_thread,\
mcp__claude_ai_Gmail__update_draft,mcp__claude_ai_Gmail__mark_message_spam,mcp__claude_ai_Gmail__mark_thread_spam,\
Write,Edit,NotebookEdit,WebFetch,WebSearch,Agent,Workflow"

check=$(timeout 300 "$CLAUDE" -p "$(cat "$S/muse_poll.check.prompt")" --model haiku --max-turns 8 \
  --permission-mode default --disallowedTools "$DENY" \
  --allowedTools "mcp__claude_ai_Gmail__list_drafts,mcp__claude_ai_Gmail__search_threads,Read" 2>>"$LOG" | tail -1)
echo "$(ts) check ($mode): ${check:-<no output>}" >>"$LOG"
[[ "$check" == NEW* ]] || exit 0
touch "$ACTIVE"                                # a Hatch note arrived: the exchange is live

ALLOW="Read,Grep,Glob,\
mcp__claude_ai_Gmail__list_drafts,mcp__claude_ai_Gmail__get_draft,mcp__claude_ai_Gmail__search_threads,\
mcp__claude_ai_Gmail__get_message,mcp__claude_ai_Gmail__get_thread,mcp__claude_ai_Gmail__create_draft,\
mcp__claude_ai_Gmail__label_message,\
Bash($S/muse_poll_record.sh:*),Bash(python3 $S/openclaw_tray.py notify:*)"
timeout 1200 "$CLAUDE" -p "$(cat "$S/muse_poll.handle.prompt")" --model sonnet --max-turns 40 \
  --permission-mode default --disallowedTools "$DENY" --allowedTools "$ALLOW" >>"$LOG" 2>&1
echo "$(ts) handler finished (exit $?)" >>"$LOG"
