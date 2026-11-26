#!/usr/bin/env bash
# The only writes the unattended notes poller may make:
#   muse_poll_record.sh processed <note-id>   -> appends the ID to the handled-notes state file; a draft ID (r123) is also
#                                                labelled Label_31 and Label_32 through gmail_api.py (failures go to agent-notes-label-fail.log)
#   muse_poll_record.sh inbox "<one line>"    -> queues a line for Jonathan's next Claude session
#   muse_poll_record.sh active                -> a note just went to Hatch: the poller checks every minute
#                                                again, backing off as the exchange goes quiet
#   muse_poll_record.sh done <our-draft-id>   -> Hatch's done-note arrived: ticks the open inbox lines
#                                                ("Waiting on Hatch: ...") that end with that draft ID or name it as a whole word
#   muse_poll_record.sh tick "<unique substring>" "<done note>" -> ticks exactly one matching open item, with a dated backup
#   muse_poll_record.sh client-mail <Client mail|Client alert> "<client>" "<id> [<id>...]"
#                                             -> queues "<label>: <client> | absorb lane | <ids>", leaving out
#                                                every ID already in client-mail-seen.txt or already on an
#                                                inbox line (open or ticked), so no ID is queued twice
#   muse_poll_record.sh client-talk "<client>" "<detail>" "<talk|deck ids>"
#                                             -> queues one client-talk line for co-op chat, never a model pass
#   muse_poll_record.sh client-seen "<id> [<id>...]" --reply <draft or message id> | --card <Deck number or issue URL> | --fyi
#                                             -> marks absorbed Gmail or Talk IDs (client-mail-seen.txt) and
#                                                ticks open client lines whose IDs are all absorbed. Seen is not handled (23 Nov 2026: five
#                                                of Greg's asks were ticked seen and never answered), so a tick with none of the three is
#                                                refused: a reply or draft, a card that carries it, or --fyi (it asked for nothing; routine
#                                                alerts). Gmail ids are stamped into the mail index (handled and reply columns).
#   muse_poll_record.sh open [<prefix>] [-v] [--full]  -> prints the open lines only (never the done history), optionally one
#                                                kind ("Waiting on Hatch"); -v adds each open line's indented notes.
#                                                This is how a model reads the board: never read the whole file. Items older than 10 days with no
#                                                change since (HUB_STALE_DAYS) are left out and counted in a last line; --stale lists only
#                                                them, --all lists both.
#   muse_poll_record.sh ledger|order|risk|counts|stale  -> notes_ledger.py (state word per open item from checked facts, one work-order
#                                                block per item, the at-risk escalation line, counts only, the stale list). No model.
#   muse_poll_record.sh expire [--days 7] [--close] | --close-id ID --reason "why"
#                                             -> routine machine lines (ScoutJob check-ins, new-job-mail lines, recovery and wake notes with no
#                                                person's words, client, money or dated event in them) older than N days and not marked [keep]:
#                                                a dry-run list; --close ticks them and appends " (expired YYYY-MM-DD)". Every other open line
#                                                stays; the dry run lists those older than 30 days, and --close-id closes one with a reason.
#                                                Never deletes; writes under hub-inbox.lock.
#   muse_poll_record.sh archive [<days>]      -> moves done lines (with their indented notes) not touched for more
#                                                than <days> (default 7) to hub-inbox-archive/YYYY-MM.md
#
# The board is ~/.claude/state/hub-inbox.md on NX-01 (it was muse-inbox.md until 21 Oct 2026; that name is now a symlink) and
# peer-inbox.md on any other machine. HUB_INBOX overrides the path, and the older MUSE_INBOX still works. Every line begins with the owner
# of the next move: Waiting on Hatch, Waiting on tower, Waiting on NX-01, Waiting on NCC-74656, Job, Client mail, Client alert, Client talk, Question or Hub. Every writer takes
# hub-inbox.lock (flock) beside the board, so the archive run and the appends never interleave.
set -euo pipefail
STATE=${MUSE_STATE:-$HOME/.claude/state}
# Which board: HUB_INBOX or MUSE_INBOX when set. Otherwise NX-01 (and a machine with no machine file) keeps hub-inbox.md, and every other
# machine writes peer-inbox.md, the file its own hub_digest.py and lane_watch.sh read (1 Nov: the tower's "inbox" lines went to hub-inbox.md
# while its digest read peer-inbox.md). MUSE_STATE, the tests' hook for a scratch folder, keeps hub-inbox.md so a test never depends on the
# machine it runs on.
BOARD=hub-inbox.md
if [[ -z "${MUSE_STATE:-}" ]]; then
  machine=$(cat "$HOME/.config/afa/machine" 2>/dev/null || true)
  [[ -z "$machine" || "$machine" == "NX-01" ]] || BOARD=peer-inbox.md
fi
INBOX=${HUB_INBOX:-${MUSE_INBOX:-$STATE/$BOARD}}
[[ -e "$INBOX" ]] && INBOX=$(readlink -f "$INBOX")   # sed -i on the old symlink name would replace the link with a file
LOCK=$(dirname "$INBOX")/hub-inbox.lock
CANON_RE='^(Waiting on Hatch|Waiting on tower|Waiting on NX-01|Waiting on NCC-74656|Job|Client mail|Client alert|Client talk|Question|Hub):'
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
    line="${line//$'\r'/}"   # a line from a Windows tool ends in a carriage return; stored, it would hide the note id from the duplicate and done checks
    if ! [[ "$line" =~ $CANON_RE ]]; then
      echo "unknown prefix: start the line with Waiting on Hatch:, Waiting on tower:, Waiting on NX-01:, Waiting on NCC-74656:, Job:, Client mail:, Client alert:, Client talk:, Question: or Hub: (the owner of the next move)" >&2
      exit 1
    fi
    lock
    # The same note queued twice (a handler that retried) writes one line: a line whose last "|" field is a note id already on the
    # board, ticked or open, is not added again. Lines with no id-shaped last field are always added.
    qid=${line:0:400}; qid=${qid##* | }
    if [[ "${line:0:400}" == *" | "*" | "* && "$qid" =~ ^[A-Za-z0-9_-]{6,40}$ ]] && [[ -s "$INBOX" ]] \
       && awk -v id="$qid" '/^- \[[ x]\] /{n=split($0,a," [|] "); sub(/[ \t]+$/,"",a[n]); if(a[n]==id){f=1; exit}} END{exit !f}' "$INBOX"; then
      echo "already queued: $qid"
      exit 0
    fi
    # The whole line is stored. Only a line past 4000 characters is cut, and the cut is marked so nobody reads a half line as whole.
    stored=$line
    (( ${#stored} > 4000 )) && stored="${stored:0:4000} ... [cut]"
    printf -- '- [ ] %s | %s\n' "$(date '+%F %H:%M')" "$stored" >>"$INBOX"
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
      BEGIN { tok = "(^|[^A-Za-z0-9_-])" id "([^A-Za-z0-9_-]|$)"; tail = "[|] " id "\r?$" }   # \r?: a board line saved with Windows line ends still ticks
      /^- \[ \] / && ($0 ~ tail || ($0 ~ /^- \[ \] [^|]*[|] Waiting on / && $0 ~ tok && ids($0) == 1)) { sub(/^- \[ \]/, "- [x]"); n++ }
      { print }
      END { print n + 0 > "/dev/stderr" }' "$INBOX" >"$tmp" 2>"$tmp.n"
    cat "$tmp" >"$INBOX"
    echo "ticked $(cat "$tmp.n")"
    rm -f "$tmp" "$tmp.n"
    ;;
  tick)
    needle="${2:?unique substring}"
    note="${3:?done note}"
    note="${note//$'\r'/ }"; note="${note//$'\n'/ }"
    lock
    python3 -I "$(dirname "$(readlink -f "$0")")/muse_poll_tick.py" "$INBOX" "$needle" "$note"
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
      [[ "$id" =~ ^((talk|deck):[A-Za-z0-9_-]+:[0-9]+|deck-notif:[A-Za-z0-9_-]+)$ ]] || { echo "bad id $id" >&2; exit 1; }
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
    # Seen is not handled: say what covers the message, or nothing is ticked (the check runs before any write).
    proof=()
    case "${3:-}" in
      --reply) [[ "${4:-}" =~ ^[A-Za-z0-9_-]{6,40}$ ]] || { echo "client-seen: --reply wants a draft or message id" >&2; exit 1; }; proof=(--reply "$4") ;;
      --card)  [[ "${4:-}" =~ ^([0-9]{1,6}|deck:[A-Za-z0-9_-]+:[0-9]{1,6}|https://[A-Za-z0-9./_#?=\&%~-]+)$ ]] || { echo "client-seen: --card wants a Deck card number or an https issue address" >&2; exit 1; }; proof=(--card "$4") ;;
      --fyi)   proof=(--fyi) ;;
      *) echo "client-seen: seen is not handled. Say what covers it: --reply <draft or message id>, --card <Deck number or issue URL>, or --fyi (it asked for nothing)" >&2; exit 1 ;;
    esac
    gids=()
    for id in $ids; do
      [[ "$id" =~ ^([0-9a-f]{15,16}|(talk|deck):[A-Za-z0-9_-]+:[0-9]+|deck-notif:[A-Za-z0-9_-]+)$ ]] || { echo "bad id $id" >&2; exit 1; }
      [[ "$id" =~ ^[0-9a-f]{15,16}$ ]] && gids+=("$id")
    done
    # Gmail ids are stamped into the mail index first; a refusal there ticks nothing. An id with no row yet (the index updates every
    # ten minutes) gets one update, then one more try; one still missing is a warning, never a reason to leave the tick undone.
    if ((${#gids[@]})); then
      MAILIDX=${MUSE_MAIL_INDEX_PY:-$HOME/repos/hub/scripts/mail_index.py}
      [[ -f "$MAILIDX" ]] || { echo "client-seen: $MAILIDX is missing, so the mail index cannot be stamped" >&2; exit 1; }
      rc=0; python3 -I "$MAILIDX" handled "${gids[@]}" "${proof[@]}" >/dev/null || rc=$?
      if ((rc == 1)); then
        timeout 200 python3 -I "$MAILIDX" update >/dev/null 2>&1 || true
        rc=0; python3 -I "$MAILIDX" handled "${gids[@]}" "${proof[@]}" >/dev/null || rc=$?
      fi
      ((rc == 0)) || { ((rc == 1)) && echo "client-seen: some ids have no mail-index row yet (ticked anyway)" >&2 || exit 1; }
    fi
    lock
    touch "$STATE/client-mail-seen.txt"
    for id in $ids; do
      grep -qxF "$id" "$STATE/client-mail-seen.txt" || echo "$id" >>"$STATE/client-mail-seen.txt"
    done
    # tick open client lines whose IDs are all absorbed now
    tmp=$(mktemp)
    # Both files are read with any carriage return removed, so a Windows line end never makes an id miss (the board line keeps its own bytes).
    seen=$(tr -d '\r' <"$STATE/client-mail-seen.txt")
    while IFS= read -r line; do
      if [[ "$line" == "- [ ] "*"| Client mail: "* || "$line" == "- [ ] "*"| Client alert: "* || "$line" == "- [ ] "*"| Client talk: "* ]]; then
        all=1; for id in ${line##*| }; do id=${id%$'\r'}; grep -qxF -- "$id" <<<"$seen" || all=0; done
        [[ $all == 1 ]] && line="- [x] ${line#- \[ \] }"
      fi
      printf '%s\n' "$line"
    done <"$INBOX" >"$tmp"
    cat "$tmp" >"$INBOX"; rm -f "$tmp"
    ;;
  open)
    shift; prefix=""; notes=0; full=0; mode=fresh
    for a in "$@"; do
      if [[ "$a" == "-v" ]]; then notes=1
      elif [[ "$a" == "--full" ]]; then full=1
      elif [[ "$a" == "--stale" ]]; then mode=stale
      elif [[ "$a" == "--all" ]]; then mode=all
      else prefix="$a"; fi
    done
    [[ -f "$INBOX" ]] || exit 0
    # An open item is stale when its last change (the date on its line, or on the head of any note under it) is more than
    # HUB_STALE_DAYS (default 10) days before today (HUB_TODAY overrides today, for tests). The default read leaves stale items out and says
    # how many; --stale lists only them; --all lists both. A line with no readable date is never stale.
    today=${HUB_TODAY:-$(date +%F)}
    cutoff=$(date -d "$today -${HUB_STALE_DAYS:-10} days" +%F)
    # One awk program, run twice: once for what is shown, once (mode "count") to count the stale items the default read leaves out.
    # shellcheck disable=SC2016
    OPEN_AWK='
      function flush(   stale, ok) {
        if (!have) return
        stale = (last != "" && last < cutoff)
        ok = match_p && (mode == "all" || (mode == "stale" && stale) || (mode == "fresh" && !stale) || (mode == "count" && stale))
        if (ok) { n++; if (mode != "count") { print head; if (notes) printf "%s", buf } }
        have = 0
      }
      /^- \[[ x]\] / { flush() }
      /^- \[ \] / {
        t = $0; sub(/^- \[ \] [^|]*\| /, "", t)
        match_p = (p == "" || tolower(substr(t, 1, length(p))) == tolower(p))
        head = $0; buf = ""; have = 1
        last = (substr($0, 7, 10) ~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$/) ? substr($0, 7, 10) : ""
        next }
      /^- \[x\] / { next }
      /^[ \t]/ { if (have) { buf = buf $0 "\n"
          if (match(substr($0, 1, 30), /[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/)) { d = substr($0, RSTART, 10); if (last != "" && d > last) last = d } } }
      END { flush(); if (mode == "count") print n + 0 }'
    # A lane or hub session (AFA_BIG_OUT=1) gets a count by kind and the path when the board is over 8,000 characters; --full prints it all.
    out=$(awk -v p="$prefix" -v notes="$notes" -v mode="$mode" -v cutoff="$cutoff" "$OPEN_AWK" "$INBOX")
    hidden=0
    if [[ $mode == fresh ]]; then hidden=$(awk -v p="$prefix" -v notes=0 -v mode=count -v cutoff="$cutoff" "$OPEN_AWK" "$INBOX"); fi
    if [[ -n "$out" ]]; then
      if [[ $full == 1 ]]; then printf '%s\n' "$out"
      else printf '%s\n' "$out" | python3 -I "$(dirname "$(readlink -f "$0")")/big_output.py" --name inbox-open --group-by '^- \[ \] [^|]*\| ([A-Za-z][A-Za-z ]*[A-Za-z])[:|]'; fi
    fi
    if [[ $hidden -gt 0 ]]; then
      echo "($hidden stale open item(s) not shown: older than ${HUB_STALE_DAYS:-10} days with no change since. List them with: muse_poll_record.sh open --stale)"
    fi
    ;;
  expire)
    # Expiry (notes_ledger.py expire): a dry run by default; --close ticks routine machine lines older than 7 days and --close-id ID
    # --reason "why" closes one line. The python takes hub-inbox.lock itself (as the archive does); taking it here as well would be a
    # second open of the same lock file in the same process tree, and the two would wait on each other.
    exec python3 -I "$(dirname "$(readlink -f "$0")")/notes_ledger.py" expire --inbox "$INBOX" "${@:2}"
    ;;
  stale|counts|ledger|order|risk)
    # The ledger (notes_ledger.py): the stale view, a state word per open item from facts a script can check, one work-order block per
    # item, the at-risk escalation line. No model anywhere in this path.
    exec python3 -I "$(dirname "$(readlink -f "$0")")/notes_ledger.py" "$1" --inbox "$INBOX" "${@:2}"
    ;;
  archive)
    exec python3 "$(dirname "$(readlink -f "$0")")/hub_inbox_archive.py" --inbox "$INBOX" --days "${2:-7}"
    ;;
  *) echo "usage: $0 processed <id> | inbox \"<Owner: line>\" | active | done <our-draft-id> | tick \"<unique substring>\" \"<done note>\" | client-mail <label> <client> <ids> | client-talk <client> <detail> <ids> | client-seen <ids> | open [<prefix>] [-v] [--stale|--all] | archive [<days>] | stale | counts | ledger | order | risk | expire [--days N] [--close]" >&2; exit 2 ;;
esac
