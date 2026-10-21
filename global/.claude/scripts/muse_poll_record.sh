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
#   muse_poll_record.sh open [<prefix>] [-v]  -> prints the open lines only (never the done history), optionally one
#                                                kind ("Waiting on Hatch"); -v adds each open line's indented notes.
#                                                This is how a model reads the board: never read the whole file.
#   muse_poll_record.sh archive [<days>]      -> moves done lines (with their indented notes) not touched for more
#                                                than <days> (default 7) to hub-inbox-archive/YYYY-MM.md
#
# The board is ~/.claude/state/hub-inbox.md (it was muse-inbox.md until 21 Oct 2026; that name is now a symlink).
# HUB_INBOX overrides the path, and the older MUSE_INBOX still works. Every line begins with the owner of the next
# move: Waiting on Hatch, Waiting on tower, Job, Client mail, Client alert, Question or Hub. Every writer takes
# hub-inbox.lock (flock) beside the board, so the archive run and the appends never interleave.
set -euo pipefail
STATE=${MUSE_STATE:-$HOME/.claude/state}
INBOX=${HUB_INBOX:-${MUSE_INBOX:-$STATE/hub-inbox.md}}
[[ -e "$INBOX" ]] && INBOX=$(readlink -f "$INBOX")   # sed -i on the old symlink name would replace the link with a file
LOCK=$(dirname "$INBOX")/hub-inbox.lock
CANON_RE='^(Waiting on Hatch|Waiting on tower|Job|Client mail|Client alert|Question|Hub):'
lock() { mkdir -p "$(dirname "$LOCK")"; exec 9>>"$LOCK"; flock -w 60 9 || { echo "hub-inbox is locked by another writer" >&2; exit 1; }; }
case "${1:-}" in
  processed)
    id="${2:?note id}"
    [[ "$id" =~ ^[A-Za-z0-9_-]{6,40}$ ]] || { echo "bad id" >&2; exit 1; }
    grep -qxF "$id" "$STATE/agent-notes-processed.txt" 2>/dev/null || echo "$id" >>"$STATE/agent-notes-processed.txt"
    ;;
  inbox)
    line="${2:?text}"
    line="${line//$'\n'/ }"
    if ! [[ "$line" =~ $CANON_RE ]]; then
      echo "unknown prefix: start the line with Waiting on Hatch:, Waiting on tower:, Job:, Client mail:, Client alert:, Question: or Hub: (the owner of the next move)" >&2
      exit 1
    fi
    lock
    printf -- '- [ ] %s | %s\n' "$(date '+%F %H:%M')" "${line:0:400}" >>"$INBOX"
    ;;
  active)
    touch "$STATE/muse-active.stamp"
    ;;
  done)
    id="${2:?our draft id}"
    [[ "$id" =~ ^[A-Za-z0-9_-]{6,40}$ ]] || { echo "bad id" >&2; exit 1; }
    lock
    n=$(grep -c -- "^- \[ \] .*| $id\$" "$INBOX" || true)
    sed -i -- "s/^- \[ \] \(.*| $id\)\$/- [x] \1/" "$INBOX"
    echo "ticked $n"
    ;;
  client-mail)
    label="${2:?label}"; client="${3:?client}"; ids="${4:?ids}"
    [[ "$label" == "Client mail" || "$label" == "Client alert" ]] || { echo "bad label" >&2; exit 1; }
    client="${client//$'\n'/ }"; client="${client//|/ }"
    lock
    keep=()
    for id in $ids; do
      [[ "$id" =~ ^[0-9a-f]{15,16}$ ]] || { echo "bad id $id" >&2; exit 1; }
      grep -qxF "$id" "$STATE/client-mail-seen.txt" 2>/dev/null && continue
      grep -q -- "$id" "$INBOX" 2>/dev/null && continue
      keep+=("$id")
    done
    if ((${#keep[@]})); then
      printf -- '- [ ] %s | %s: %s | absorb lane | %s\n' "$(date '+%F %H:%M')" "$label" "${client:0:80}" "${keep[*]}" >>"$INBOX"
      echo "queued ${#keep[@]}"
    else
      echo "queued 0"
    fi
    ;;
  client-seen)
    ids="${2:?ids}"
    lock
    touch "$STATE/client-mail-seen.txt"
    for id in $ids; do
      [[ "$id" =~ ^[0-9a-f]{15,16}$ ]] || { echo "bad id $id" >&2; exit 1; }
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
    done <"$INBOX" >"$tmp"
    cat "$tmp" >"$INBOX"; rm -f "$tmp"
    ;;
  open)
    shift; prefix=""; notes=0
    for a in "$@"; do if [[ "$a" == "-v" ]]; then notes=1; else prefix="$a"; fi; done
    [[ -f "$INBOX" ]] || exit 0
    awk -v p="$prefix" -v notes="$notes" '
      /^- \[[ x]\] / { show = 0 }
      /^- \[ \] / {
        t = $0; sub(/^- \[ \] [^|]*\| /, "", t)
        if (p == "" || tolower(substr(t, 1, length(p))) == tolower(p)) { print; show = 1 }
        next }
      /^[ \t]/ { if (show && notes) print }' "$INBOX"
    ;;
  archive)
    exec python3 "$(dirname "$(readlink -f "$0")")/hub_inbox_archive.py" --inbox "$INBOX" --days "${2:-7}"
    ;;
  *) echo "usage: $0 processed <id> | inbox \"<Owner: line>\" | active | done <our-draft-id> | client-mail <label> <client> <ids> | client-seen <ids> | open [<prefix>] [-v] | archive [<days>]" >&2; exit 2 ;;
esac
