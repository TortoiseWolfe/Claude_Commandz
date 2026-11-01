#!/usr/bin/env bash
# The only writes the unattended notes poller may make:
#   muse_poll_record.sh processed <note-id>   -> appends the ID to the handled-notes state file; a draft ID (r123) is also
#                                                labelled Label_31 and Label_32 through gmail_api.py (failures go to agent-notes-label-fail.log)
#   muse_poll_record.sh inbox "<one line>"    -> queues a line for Jonathan's next Claude session
#   muse_poll_record.sh active                -> a note just went to Hatch: the poller checks every minute
#                                                again, backing off as the exchange goes quiet
#   muse_poll_record.sh done <our-draft-id>   -> Hatch's done-note arrived: ticks the open inbox lines
#                                                ("Waiting on Hatch: ...") that end with that draft ID or name it as a whole word
#   muse_poll_record.sh client-mail <Client mail|Client alert> "<client>" "<id> [<id>...]"
#                                             -> queues "<label>: <client> | absorb lane | <ids>", leaving out
#                                                every ID already in client-mail-seen.txt or already on an
#                                                inbox line (open or ticked), so no ID is queued twice
#   muse_poll_record.sh client-talk "<client>" "<detail>" "<talk|deck ids>"
#                                             -> queues one client-talk line for co-op chat, never a model pass
#   muse_poll_record.sh client-seen "<id> [<id>...]"
#                                             -> marks absorbed Gmail or Talk IDs (client-mail-seen.txt) and
#                                                ticks open client lines whose IDs are all absorbed. Also used for routine alerts.
#   muse_poll_record.sh open [<prefix>] [-v]  -> prints the open lines only (never the done history), optionally one
#                                                kind ("Waiting on Hatch"); -v adds each open line's indented notes.
#                                                This is how a model reads the board: never read the whole file.
#   muse_poll_record.sh archive [<days>]      -> moves done lines (with their indented notes) not touched for more
#                                                than <days> (default 7) to hub-inbox-archive/YYYY-MM.md
#
# The board is ~/.claude/state/hub-inbox.md (it was muse-inbox.md until 21 Oct 2026; that name is now a symlink).
# HUB_INBOX overrides the path, and the older MUSE_INBOX still works. Every line begins with the owner of the next
# move: Waiting on Hatch, Waiting on tower, Job, Client mail, Client alert, Client talk, Question or Hub. Every writer takes
# hub-inbox.lock (flock) beside the board, so the archive run and the appends never interleave.
set -euo pipefail
STATE=${MUSE_STATE:-$HOME/.claude/state}
INBOX=${HUB_INBOX:-${MUSE_INBOX:-$STATE/hub-inbox.md}}
[[ -e "$INBOX" ]] && INBOX=$(readlink -f "$INBOX")   # sed -i on the old symlink name would replace the link with a file
LOCK=$(dirname "$INBOX")/hub-inbox.lock
CANON_RE='^(Waiting on Hatch|Waiting on tower|Job|Client mail|Client alert|Client talk|Question|Hub):'
lock() { mkdir -p "$(dirname "$LOCK")"; exec 9>>"$LOCK"; flock -w 60 9 || { echo "hub-inbox is locked by another writer" >&2; exit 1; }; }
case "${1:-}" in
  processed)
    id="${2:?note id}"
    [[ "$id" =~ ^[A-Za-z0-9_-]{6,40}$ ]] || { echo "bad id" >&2; exit 1; }
    grep -qxF "$id" "$STATE/agent-notes-processed.txt" 2>/dev/null || echo "$id" >>"$STATE/agent-notes-processed.txt"
    # Hatch's sweep deletes a note draft only once it carries Label_32, so label a draft id (r123, r-123) here too.
    # The connector's label tools are refused on drafts; our own Gmail API tool is not. Sent-message ids (16 hex) get none.
    # A label that fails never fails the record: it is logged and the note stays recorded.
    if [[ "$id" =~ ^r-?[0-9]+$ ]]; then
      api=${MUSE_GMAIL_API:-$HOME/.claude/scripts/gmail_api.py}
      for label in Label_31 Label_32; do
        timeout 60 python3 "$api" draft-label "$id" "$label" </dev/null >/dev/null 2>&1 \
          || printf '%s %s %s label failed\n' "$(date -Iseconds)" "$id" "$label" >>"$STATE/agent-notes-label-fail.log"
      done
    fi
    ;;
  inbox)
    line="${2:?text}"
    line="${line//$'\n'/ }"
    if ! [[ "$line" =~ $CANON_RE ]]; then
      echo "unknown prefix: start the line with Waiting on Hatch:, Waiting on tower:, Job:, Client mail:, Client alert:, Client talk:, Question: or Hub: (the owner of the next move)" >&2
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
    # An open line ticks when it ends "| <id>" (any owner), or when it is a "Waiting on ..." line that names the id as a whole
    # word anywhere ("... note r-123 (details)") and names no other draft id: the id carries "r-" and sits after prose as often
    # as after a bar, but a line waiting on several notes ticks only through the trailing form, never on the first done-note.
    tmp=$(mktemp)
    awk -v id="$id" '
      function ids(l,  c) { c = l; return gsub(/(^|[^A-Za-z0-9_-])r-?[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]+/, "", c) }
      BEGIN { tok = "(^|[^A-Za-z0-9_-])" id "([^A-Za-z0-9_-]|$)"; tail = "[|] " id "$" }
      /^- \[ \] / && ($0 ~ tail || ($0 ~ /^- \[ \] [^|]*[|] Waiting on / && $0 ~ tok && ids($0) == 1)) { sub(/^- \[ \]/, "- [x]"); n++ }
      { print }
      END { print n + 0 > "/dev/stderr" }' "$INBOX" >"$tmp" 2>"$tmp.n"
    cat "$tmp" >"$INBOX"
    echo "ticked $(cat "$tmp.n")"
    rm -f "$tmp" "$tmp.n"
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
  client-talk)
    client="${2:?client}"; detail="${3:?detail}"; ids="${4:?ids}"
    client="${client//$'\n'/ }"; client="${client//$'\r'/ }"; client="${client//|/\/}"
    detail="${detail//$'\n'/ }"; detail="${detail//$'\r'/ }"; detail="${detail//|/\/}"
    lock
    keep=()
    for id in $ids; do
      [[ "$id" =~ ^(talk|deck):[A-Za-z0-9_-]+:[0-9]+$ ]] || { echo "bad id $id" >&2; exit 1; }
      grep -qxF "$id" "$STATE/client-mail-seen.txt" 2>/dev/null && continue
      grep -qF -- "$id" "$INBOX" 2>/dev/null && continue
      keep+=("$id")
    done
    if ((${#keep[@]})); then
      printf -- '- [ ] %s | Client talk: %s | %s | %s\n' "$(date '+%F %H:%M')" "${client:0:80}" "${detail:0:300}" "${keep[*]}" >>"$INBOX"
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
      [[ "$id" =~ ^([0-9a-f]{15,16}|(talk|deck):[A-Za-z0-9_-]+:[0-9]+)$ ]] || { echo "bad id $id" >&2; exit 1; }
      grep -qxF "$id" "$STATE/client-mail-seen.txt" || echo "$id" >>"$STATE/client-mail-seen.txt"
    done
    # tick open client lines whose IDs are all absorbed now
    tmp=$(mktemp)
    while IFS= read -r line; do
      if [[ "$line" == "- [ ] "*"| Client mail: "* || "$line" == "- [ ] "*"| Client alert: "* || "$line" == "- [ ] "*"| Client talk: "* ]]; then
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
  *) echo "usage: $0 processed <id> | inbox \"<Owner: line>\" | active | done <our-draft-id> | client-mail <label> <client> <ids> | client-talk <client> <detail> <ids> | client-seen <ids> | open [<prefix>] [-v] | archive [<days>]" >&2; exit 2 ;;
esac
