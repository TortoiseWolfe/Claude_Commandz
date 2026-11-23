#!/usr/bin/env bash
# The only writes the unattended peer notes poller (NCC-74656) may make:
#   peer_poll_record.sh processed <note-id>   -> appends the ID to the handled-notes state file
#   peer_poll_record.sh files <note-id>       -> a note with attached files: skipped by the next checks, NOT recorded as processed
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
  files)
    # A note that carries files this machine cannot see (the Gmail connector here shows no attachments). Recorded here so the
    # poller does not pick it up again every minute, and NEVER in peer-notes-processed.txt: that file is the read record
    # NX-01's sweep deletes from, and the files would be lost with the note (19 and 23 Nov 2026, issue 143).
    # The name must not match peer-*processed*.txt, which notes_check.py reads as a processed-ids file.
    id="${2:?note id}"
    [[ "$id" =~ ^[A-Za-z0-9_-]{6,40}$ ]] || { echo "bad id" >&2; exit 1; }
    grep -qxF "$id" "$STATE/peer-files-waiting.txt" 2>/dev/null || echo "$id" >>"$STATE/peer-files-waiting.txt"
    ;;
  ccseen)
    id="${2:?note id}"
    [[ "$id" =~ ^[A-Za-z0-9_-]{6,40}$ ]] || { echo "bad id" >&2; exit 1; }
    grep -qxF "$id" "$STATE/peer-muse-cc-seen.txt" 2>/dev/null || echo "$id" >>"$STATE/peer-muse-cc-seen.txt"
    ;;
  active)
    touch "$STATE/peer-active.stamp"
    ;;
  *) echo "usage: $0 processed <id> | files <id> | ccseen <id> | inbox \"<line>\" | active" >&2; exit 2 ;;
esac
