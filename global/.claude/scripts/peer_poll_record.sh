#!/usr/bin/env bash
# The only writes the unattended peer notes poller (NCC-74656) may make:
#   peer_poll_record.sh processed <note-id>   -> appends the ID to the handled-notes state file
#   peer_poll_record.sh inbox "<one line>"    -> queues a line for Jonathan's next Claude session
#   peer_poll_record.sh ccseen <note-id>     -> an old [MUSE>CC] note was queued for the hub: never queue it twice
#   peer_poll_record.sh active                -> a note just went out: the poller checks every minute
#                                                again, backing off as the exchange goes quiet
set -euo pipefail
STATE=${PEER_STATE:-$HOME/.claude/state}
case "${1:-}" in
  processed)
    id="${2:?note id}"
    [[ "$id" =~ ^[A-Za-z0-9_-]{6,40}$ ]] || { echo "bad id" >&2; exit 1; }
    grep -qxF "$id" "$STATE/peer-notes-processed.txt" 2>/dev/null || echo "$id" >>"$STATE/peer-notes-processed.txt"
    ;;
  inbox)
    line="${2:?text}"
    line="${line//$'\n'/ }"
    printf -- '- [ ] %s | %s\n' "$(date '+%F %H:%M')" "${line:0:400}" >>"$STATE/peer-inbox.md"
    ;;
  ccseen)
    id="${2:?note id}"
    [[ "$id" =~ ^[A-Za-z0-9_-]{6,40}$ ]] || { echo "bad id" >&2; exit 1; }
    grep -qxF "$id" "$STATE/peer-muse-cc-seen.txt" 2>/dev/null || echo "$id" >>"$STATE/peer-muse-cc-seen.txt"
    ;;
  active)
    touch "$STATE/peer-active.stamp"
    ;;
  *) echo "usage: $0 processed <id> | ccseen <id> | inbox \"<line>\" | active" >&2; exit 2 ;;
esac
