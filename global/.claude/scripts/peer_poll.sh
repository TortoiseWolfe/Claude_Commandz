#!/usr/bin/env bash
# The always-on half of the notes channel on the second tower (NCC-74656), adapted from muse_poll.sh
# (Claude_Commandz, NX-01) on 2026-10-10.
#
# Windows Task Scheduler runs this every minute ("\AFA\PeerNotesPoll"). Between 08:00 and 22:00 local
# it always runs; after hours only within two hours of a note moving either way. (NX-01 also wakes
# after hours when Jonathan typed within the hour, via hooks/muse-drafts.sh; that hook is off here
# because [MUSE>CC] belongs to NX-01, so this tower has no typing stamp.) How often it actually
# checks scales with use: right after a note either way it checks every minute; as the exchange
# goes quiet it waits a quarter of the quiet time between checks, up to every 30 minutes, but never
# more than every 5 minutes inside a show window (see below). The clock
# is peer-active.stamp, touched by `peer_poll_record.sh active` whenever a session writes a note,
# and here whenever a note arrives. A tick that isn't due exits before any model runs.
#
# Show windows (29 Oct 2026, triage item 99: the tower missed two URGENT notes at 7:50 PM because its poll had backed off to every 30
# minutes): ~/.config/afa/show-windows.tsv, one window per line, "<days> <HH:MM> <HH:MM>" with days "*" or a comma list (Mon,Tue,...) in
# local time; "#" lines and blanks are ignored. The file is private and may be missing (then there are no windows). Inside a window the
# wait between checks is capped at PEER_POLL_SHOW_CAP seconds (default 300). The hub writes the file; this script only reads it.
#
#   1. A cheap Haiku pass LOOKS for unhandled [NX-01>NCC-74656] or [MUSE>NCC-74656] notes. It also looks for a [MUSE>CC] note that has
#      sat unhandled for 30 minutes or more (NX-01 handles those and normally answers within one of its own polls, so an old one means
#      NX-01 may be down). It lists such a note as cc:<id>. Two machines answering one note has caused trouble before, so this tower
#      NEVER answers a [MUSE>CC] note: the handler only reads it and queues one line in peer-inbox.md for the hub, which is what was
#      missing on 29 Oct (triage item 99). peer-muse-cc-seen.txt keeps it from queueing the same note twice.
#   2. Only if there is one, a Sonnet pass HANDLES it: answers read-only with a
#      [NCC-74656>NX-01] or "[CC>MUSE] NCC-74656: <topic>" (Hatch) draft, records the note (no labels: the hosted connector refuses them on drafts), and queues
#      anything needing work or approval in ~/.claude/state/peer-inbox.md.
#
# It cannot send mail, push, deploy, delete, or change settings: the allow and deny lists below
# say so, and print mode denies anything else.
set -u
# Paths come from $HOME (the tower's home is /home/turtle_wolfe); PEER_* variables are for the tests, which never touch the real poller.
S=${PEER_SCRIPTS:-$HOME/.claude/scripts}
[ -x "$S/fleet-up.sh" ] && timeout 100 "$S/fleet-up.sh" --heartbeat >/dev/null 2>&1
H=${PEER_POLL_HOUR:-$(date +%-H)}
exec 9>"${PEER_POLL_LOCK:-/tmp/peer-poll.lock}"
flock -n 9 || exit 0

STATE=${PEER_STATE:-$HOME/.claude/state}
ACTIVE=$STATE/peer-active.stamp
LAST=$STATE/peer-poll-last-check.stamp
now=$(date +%s)
age() { if [[ -e "$1" ]]; then echo $((now - $(stat -c %Y "$1"))); else echo 999999; fi; }
night=""
if ! ((H >= 8 && H < 22)); then
  (($(age "$ACTIVE") <= 7200)) || exit 0
  night="after hours, "
fi
quiet=$(age "$ACTIVE")
want=$((quiet / 4)); ((want < 60)) && want=60; ((want > 1800)) && want=1800
# Inside a show window never wait longer than PEER_POLL_SHOW_CAP (default 300 s).
in_show_window() {
  local f=${PEER_SHOW_WINDOWS:-$HOME/.config/afa/show-windows.tsv} dow hm days from to
  [[ -r "$f" ]] || return 1
  dow=${PEER_POLL_DOW:-$(date +%a)}; hm=${PEER_POLL_HM:-$(date +%H:%M)}
  while read -r days from to _; do
    [[ -n "$days" && "$days" != \#* && "$from" =~ ^[0-9]{1,2}:[0-9]{2}$ && "$to" =~ ^[0-9]{1,2}:[0-9]{2}$ ]] || continue
    [[ "$days" == "*" || ",$days," == *",$dow,"* ]] || continue
    # zero-padded HH:MM compares as text; a window that ends before it starts runs past midnight
    from=$(printf '%02d:%s' "${from%%:*}" "${from##*:}"); to=$(printf '%02d:%s' "${to%%:*}" "${to##*:}")
    if [[ "$from" < "$to" ]]; then [[ ! "$hm" < "$from" && "$hm" < "$to" ]] && return 0
    else [[ ! "$hm" < "$from" || "$hm" < "$to" ]] && return 0; fi
  done <"$f"
  return 1
}
show=""
if in_show_window; then
  cap=${PEER_POLL_SHOW_CAP:-300}
  ((want > cap)) && want=$cap
  show="show window, "
fi
(($(age "$LAST") < want - 15)) && exit 0      # not due yet (15 s allows for scheduler jitter)
mode="${night}${show}quiet $((quiet / 60))m, every $((want / 60))m"
export MUSE_POLL=1                             # our own claude runs don't count as Jonathan typing
touch "$LAST"

CLAUDE=${PEER_CLAUDE:-$HOME/.local/bin/claude}
LOG=$STATE/peer-poll.log
ts() { date '+%F %T'; }

# The claude calls below run in a lean folder, not in ~/repos (same block as muse_poll.sh, 29 Oct 2026 token study; copied here for triage
# item 129): ~/repos/CLAUDE.md, the memory index and the skill list loaded before the first word of every headless call, 45-61K tokens each.
# ~/.claude/state/lean sits outside ~/repos, so none of that loads; hub_lean.py --lean-dir makes it with the hub's lean project settings (Gmail
# stays on) and prints it. --disable-slash-commands drops the skill listing. The prompts name every path absolutely. If hub_lean.py is
# missing or fails, the plain folder still works.
LEAN=$(timeout 30 python3 -I "$S/hub_lean.py" --lean-dir 2>>"$LOG") && [[ -d "$LEAN" ]] || LEAN=${AFA_LEAN_DIR:-$HOME/.claude/state/lean}
mkdir -p "$LEAN" && cd "$LEAN" || exit 1
LEANFLAGS=(--disable-slash-commands)

DENY="Bash(git push:*),Bash(git commit:*),Bash(rm:*),Bash(docker:*),Bash(curl:*),Bash(gh:*),\
Edit(/$HOME/repos/**),Edit(/$HOME/.claude/**),\
Write,Edit,\
mcp__claude_ai_Gmail__delete_draft,mcp__claude_ai_Gmail__trash_message,mcp__claude_ai_Gmail__trash_thread,\
mcp__claude_ai_Gmail__update_draft,mcp__claude_ai_Gmail__mark_message_spam,mcp__claude_ai_Gmail__mark_thread_spam,\
mcp__claude_ai_Gmail__send_message,mcp__claude_ai_Gmail__reply,mcp__claude_ai_Gmail__forward,\
NotebookEdit,WebFetch,WebSearch,Agent,Workflow"

check=$(timeout 300 "$CLAUDE" -p "$(cat "$S/peer_poll.check.prompt")" --model haiku --max-turns 12 "${LEANFLAGS[@]}" \
  --permission-mode default --disallowedTools "$DENY" \
  --allowedTools "mcp__claude_ai_Gmail__list_drafts,mcp__claude_ai_Gmail__search_threads,Read" 2>>"$LOG" | tail -1)
echo "$(ts) check ($mode): ${check:-<no output>}" >>"$LOG"
[[ "$check" == NEW* ]] || exit 0
# a note for this machine arrived: the exchange is live. A cc:<id> item alone is only a line queued for the hub, so it does not speed the poll up.
for item in ${check#NEW}; do [[ "$item" == cc:* ]] || { touch "$ACTIVE"; break; }; done

ALLOW="Read,Grep,Glob,\
mcp__claude_ai_Gmail__list_drafts,mcp__claude_ai_Gmail__get_draft,mcp__claude_ai_Gmail__search_threads,\
mcp__claude_ai_Gmail__get_message,mcp__claude_ai_Gmail__get_thread,mcp__claude_ai_Gmail__create_draft,\
Bash($S/peer_poll_record.sh:*)"
timeout 1200 "$CLAUDE" -p "$(cat "$S/peer_poll.handle.prompt")" --model sonnet --max-turns 40 "${LEANFLAGS[@]}" \
  --permission-mode default --disallowedTools "$DENY" --allowedTools "$ALLOW" >>"$LOG" 2>&1
echo "$(ts) handler finished (exit $?)" >>"$LOG"
# Publish the read record (draft IDs only) so NX-01's sweep can see what this tower has read; this
# replaces the Gmail label. Quiet when nothing changed. A failure is logged and never stops the poller.
PATH=$HOME/.local/bin:$PATH timeout 60 python3 $S/notes_check.py \
  --publish-read "$STATE/peer-notes-processed.txt" hub/tower/peer-notes-processed.txt >>"$LOG" 2>&1
echo "$(ts) read record publish (exit $?)" >>"$LOG"
