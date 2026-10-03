#!/usr/bin/env bash
# The always-on half of the Claude <-> Hatch (Muse) notes channel.
#
# Windows Task Scheduler runs this every 10 minutes ("\AFA\MuseNotesPoll"). It acts only between
# 08:00 and 22:00 local, the same window Hatch's own watcher uses. Before this existed, Claude
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
((H >= 8 && H < 22)) || exit 0
exec 9>/tmp/muse-poll.lock
flock -n 9 || exit 0

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
echo "$(ts) check: ${check:-<no output>}" >>"$LOG"
[[ "$check" == NEW* ]] || exit 0

ALLOW="Read,Grep,Glob,\
mcp__claude_ai_Gmail__list_drafts,mcp__claude_ai_Gmail__get_draft,mcp__claude_ai_Gmail__search_threads,\
mcp__claude_ai_Gmail__get_message,mcp__claude_ai_Gmail__get_thread,mcp__claude_ai_Gmail__create_draft,\
mcp__claude_ai_Gmail__label_message,\
Bash($S/muse_poll_record.sh:*),Bash(python3 $S/openclaw_tray.py notify:*)"
timeout 1200 "$CLAUDE" -p "$(cat "$S/muse_poll.handle.prompt")" --model sonnet --max-turns 40 \
  --permission-mode default --disallowedTools "$DENY" --allowedTools "$ALLOW" >>"$LOG" 2>&1
echo "$(ts) handler finished (exit $?)" >>"$LOG"
