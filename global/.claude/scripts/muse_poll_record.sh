#!/usr/bin/env bash
# The only writes the unattended notes poller may make:
#   muse_poll_record.sh processed <note-id>   -> appends the ID to the handled-notes state file
#   muse_poll_record.sh inbox "<one line>"    -> queues a line for Jonathan's next Claude session
#   muse_poll_record.sh active                -> a note just went to Hatch: the poller checks every minute
#                                                again, backing off as the exchange goes quiet
#   muse_poll_record.sh done <our-draft-id>   -> Hatch's done-note arrived: ticks the open inbox lines
#                                                ("Waiting on Hatch: ...") that end with that draft ID
set -euo pipefail
STATE=/home/TurtleWolfe/.claude/state
case "${1:-}" in
  processed)
    id="${2:?note id}"
    [[ "$id" =~ ^[A-Za-z0-9_-]{6,40}$ ]] || { echo "bad id" >&2; exit 1; }
    grep -qxF "$id" "$STATE/agent-notes-processed.txt" 2>/dev/null || echo "$id" >>"$STATE/agent-notes-processed.txt"
    ;;
  inbox)
    line="${2:?text}"
    line="${line//$'\n'/ }"
    printf -- '- [ ] %s | %s\n' "$(date '+%F %H:%M')" "${line:0:400}" >>"$STATE/muse-inbox.md"
    ;;
  active)
    touch "$STATE/muse-active.stamp"
    ;;
  done)
    id="${2:?our draft id}"
    [[ "$id" =~ ^[A-Za-z0-9_-]{6,40}$ ]] || { echo "bad id" >&2; exit 1; }
    n=$(grep -c -- "^- \[ \] .*| $id\$" "$STATE/muse-inbox.md" || true)
    sed -i -- "s/^- \[ \] \(.*| $id\)\$/- [x] \1/" "$STATE/muse-inbox.md"
    echo "ticked $n"
    ;;
  *) echo "usage: $0 processed <id> | inbox \"<line>\" | active | done <our-draft-id>" >&2; exit 2 ;;
esac
