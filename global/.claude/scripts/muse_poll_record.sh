#!/usr/bin/env bash
# The only writes the unattended notes poller may make:
#   muse_poll_record.sh processed <note-id>   -> appends the ID to the handled-notes state file
#   muse_poll_record.sh inbox "<one line>"    -> queues a line for Jonathan's next Claude session
#   muse_poll_record.sh active                -> a note just went to Hatch: the poller checks every minute
#                                                again, backing off as the exchange goes quiet
#   muse_poll_record.sh done <our-draft-id>   -> Hatch's done-note arrived: ticks the open inbox lines
#                                                ("Waiting on Hatch: ...") that end with that draft ID
#   muse_poll_record.sh client-mail <Client mail|Client alert> "<client>" "<id> [<id>...]"
#                                             -> queues "<label>: <client> | absorb lane | <ids>", leaving out
#                                                every ID already in client-mail-seen.txt or already on an
#                                                inbox line (open or ticked), so no ID is queued twice
#   muse_poll_record.sh client-seen "<id> [<id>...]"
#                                             -> marks Gmail message IDs absorbed (client-mail-seen.txt) and
#                                                ticks the open "Client mail:" / "Client alert:" lines whose
#                                                IDs are now all absorbed. Also used for routine alerts.
set -euo pipefail
STATE=${MUSE_STATE:-/home/TurtleWolfe/.claude/state}
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
  client-mail)
    label="${2:?label}"; client="${3:?client}"; ids="${4:?ids}"
    [[ "$label" == "Client mail" || "$label" == "Client alert" ]] || { echo "bad label" >&2; exit 1; }
    client="${client//$'\n'/ }"; client="${client//|/ }"
    keep=()
    for id in $ids; do
      [[ "$id" =~ ^[0-9a-f]{8,20}$ ]] || { echo "bad id $id" >&2; exit 1; }
      grep -qxF "$id" "$STATE/client-mail-seen.txt" 2>/dev/null && continue
      grep -q -- "$id" "$STATE/muse-inbox.md" 2>/dev/null && continue
      keep+=("$id")
    done
    if ((${#keep[@]})); then
      printf -- '- [ ] %s | %s: %s | absorb lane | %s\n' "$(date '+%F %H:%M')" "$label" "${client:0:80}" "${keep[*]}" >>"$STATE/muse-inbox.md"
      echo "queued ${#keep[@]}"
    else
      echo "queued 0"
    fi
    ;;
  client-seen)
    ids="${2:?ids}"
    touch "$STATE/client-mail-seen.txt"
    for id in $ids; do
      [[ "$id" =~ ^[0-9a-f]{8,20}$ ]] || { echo "bad id $id" >&2; exit 1; }
      grep -qxF "$id" "$STATE/client-mail-seen.txt" || echo "$id" >>"$STATE/client-mail-seen.txt"
    done
    # tick open client lines whose IDs are all absorbed now
    tmp=$(mktemp)
    while IFS= read -r line; do
      if [[ "$line" == "- [ ] "*"| Client mail: "* || "$line" == "- [ ] "*"| Client alert: "* ]]; then
        all=1; for id in ${line##*| }; do grep -qxF "$id" "$STATE/client-mail-seen.txt" || all=0; done
        [[ $all == 1 ]] && line="- [x] ${line#- \[ \] }"
      fi
      printf '%s\n' "$line"
    done <"$STATE/muse-inbox.md" >"$tmp"
    cat "$tmp" >"$STATE/muse-inbox.md"; rm -f "$tmp"
    ;;
  *) echo "usage: $0 processed <id> | inbox \"<line>\" | active | done <our-draft-id> | client-mail <label> <client> <ids> | client-seen <ids>" >&2; exit 2 ;;
esac
