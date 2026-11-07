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
    # Same rule as muse_poll_record.sh: a line whose last "|" field is a note id already on the board is not added twice.
    qid=${line:0:400}; qid=${qid##* | }
    if [[ "${line:0:400}" == *" | "*" | "* && "$qid" =~ ^[A-Za-z0-9_-]{6,40}$ ]] && [[ -s "$STATE/peer-inbox.md" ]] \
       && awk -v id="$qid" '/^- \[[ x]\] /{n=split($0,a," [|] "); sub(/[ \t]+$/,"",a[n]); if(a[n]==id){f=1; exit}} END{exit !f}' "$STATE/peer-inbox.md"; then
      echo "already queued: $qid"
      exit 0
    fi
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
