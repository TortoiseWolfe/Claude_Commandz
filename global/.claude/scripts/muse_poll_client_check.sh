#!/usr/bin/env bash
# Client-mail check, shared by the notes poller (muse_poll.sh) and /prep.
#   muse_poll_client_check.sh
# Reads the client senders from the private hub file (hub/client-senders.tsv: client, repo_dir, kind,
# gmail_from), lists every message from those senders in the last two days with gmail_api.py messages-list
# (the Gmail API, metadata only, never a body; no model pass, so IDs can't be garbled), then drops the IDs already absorbed. Prints one line per new message:
#   NEW <message id> | <From> | <Subject> | <Date>
# Only mail a client actually sent counts: the check prints each message's labels, and anything labelled
# DRAFT or SENT (our own drafts and replies, which ride along inside a matched thread) is dropped, as is any
# line with no labels field (fail closed). For kind "payment" senders, mail Gmail files under Promotions
# (CATEGORY_PROMOTIONS) is dropped too: a payment row names the whole domain, which also sends marketing.
# A message also stops being new once an inbox line (open or ticked) carries its ID: it is queued, so the
# handler must not run again for it. client-mail-seen.txt keeps its meaning (absorbed by the hub).
# and nothing when there is nothing new. Senders of no-reply@ or noreply@ are skipped UNLESS they match a
# client pattern: matching no-reply senders come out as "NEW <id> ALERT | ..." so the handler queues them, except
# kind "payment" (Gusto), which come out unmarked because their handler step marks the invoice paid. Names live only in the TSV and Gmail; this script
# hold none. Overrides for tests and seeding: MUSE_CLIENT_TSV, MUSE_CLIENT_SEEN, MUSE_CLIENT_INBOX (point at copies; the board is hub-inbox.md now), MUSE_CLIENT_GMAIL (an executable that stands in for gmail_api.py: called as `<it> messages-list <query>`),
# MUSE_CLIENT_WINDOW (Gmail newer_than value, default 2d), MUSE_CLIENT_PAYIDS (a file that gets the ID of every payment-kind line printed).
set -u
S=$HOME/.claude/scripts
TSV=${MUSE_CLIENT_TSV:-$HOME/repos/hub/client-senders.tsv}
SEEN=${MUSE_CLIENT_SEEN:-$HOME/.claude/state/client-mail-seen.txt}
INBOX=${MUSE_CLIENT_INBOX:-${HUB_INBOX:-${MUSE_INBOX:-$HOME/.claude/state/hub-inbox.md}}}
LOG=$HOME/.claude/state/muse-poll.log
WINDOW=${MUSE_CLIENT_WINDOW:-2d}
# Client mail is NX-01's job only; the second tower exits quietly so the same mail is not absorbed twice.
[[ "$(cat "$HOME/.config/afa/machine" 2>/dev/null)" == NX-01 ]] || exit 0
[[ -r "$TSV" ]] || exit 0
froms=$(awk -F'\t' 'NR>1 && $4!="" {print $4}' "$TSV" | sort -u | paste -sd' ' | sed 's/ / OR /g')
[[ -n "$froms" ]] || exit 0
query="from:($froms) newer_than:$WINDOW -from:me"
# The message list comes straight from the Gmail API (gmail_api.py messages-list, metadata only, never a body):
# no model copies the IDs. If the call fails, say so on stderr (muse_poll.sh logs it) and stop, so a dead token
# can't pass for "nothing new".
if [[ -n "${MUSE_CLIENT_GMAIL:-}" ]]; then lister=("$MUSE_CLIENT_GMAIL" messages-list); else lister=(python3 "$S/gmail_api.py" messages-list); fi
errf=$(mktemp); trap 'rm -f -- "$errf"' EXIT
if ! out=$("${lister[@]}" "$query" 2>"$errf"); then
  printf '%s client check: messages-list failed: %s\n' "$(date '+%F %T')" "$(head -c 300 "$errf" | tr '\n' ' ')" >&2
  exit 1
fi
touch "$SEEN"
# Keep only messages from a TSV sender (a thread that matched also holds Jonathan's own replies), and skip
# no-reply bots; then drop what has been absorbed already.
pats=$(awk -F'\t' 'NR>1 && $4!="" {print tolower($4)}' "$TSV")
# kind "payment" senders (Gusto) mail from no-reply addresses but have their own handler step (mark the invoice
# paid), so they are never tagged "Client alert"
paypats=$(awk -F'\t' 'NR>1 && $3=="payment" && $4!="" {print tolower($4)}' "$TSV")
# A pattern with @ is one address and must match exactly; a bare domain matches that domain and its subdomains.
sender_is() { local a=$1 p=${2//[[:space:]]/}; [[ -n "$p" ]] || return 1
  if [[ "$p" == *@* ]]; then [[ "$a" == "$p" ]]; else [[ "$a" == *"@$p" || "$a" == *".$p" ]]; fi; }
# A real Gmail message ID is 16 hex characters; a 13-digit internalDate once slipped through as an ID and hid a
# client reply, so a MSG line that fails the ID check is logged, never silently ignored.
printf '%s\n' "$out" | grep '^MSG ' | grep -vE '^MSG [0-9a-f]{15,16} \|' | while IFS= read -r bad; do
  printf '%s client check: dropped malformed line: %s\n' "$(date '+%F %T')" "${bad:0:200}" >>"$LOG"
done
printf '%s\n' "$out" | grep -E '^MSG [0-9a-f]{15,16} \|' | while IFS= read -r line; do
  id=${line#MSG }; id=${id%% *}
  ms=$(( 16#$id >> 20 )); now_ms=$(( $(date +%s) * 1000 ))
  if (( ms > now_ms + 3600000 )) || (( ms + 2592000000 < now_ms )); then
    impl_time=$(date -d "@$(( ms / 1000 ))" '+%F %T' 2>/dev/null || echo "invalid")
    printf '%s client check: dropped implausible id %s (decodes to %s)\n' "$(date '+%F %T')" "$id" "$impl_time" >>"$LOG"
    continue
  fi
  # Labels are the second-to-last field, read from the right so a "|" inside a From or Subject can't move them.
  # Fail closed: the last field must be a hex thread ID and the labels must be label IDs, or a line missing its
  # trailer could hand the Date or Subject in as labels and let a DRAFT or SENT through. Such a line is logged.
  tid=${line##*|}; tid=${tid//[[:space:]]/}
  rest=${line%|*}; labels=${rest##*|}
  labels=$(printf '%s' "$labels" | tr 'a-z' 'A-Z'); labels=${labels//[[:space:]]/}
  if ! [[ "$tid" =~ ^[0-9a-f]{15,16}$ && "$labels" =~ ^[A-Z0-9_]+(,[A-Z0-9_]+)*$ ]]; then
    printf '%s client check: dropped %s: no thread ID or label list at the end of the line\n' "$(date '+%F %T')" "$id" >>"$LOG"
    continue
  fi
  [[ "$labels" == *DRAFT* || "$labels" == *SENT* ]] && continue
  # Match on the bare sender address the Gmail pass prints right after the ID, never on the From header (anyone
  # can put a client's address in a display name, with "|" or "<>" around it). A field that is not one plain
  # address is logged and dropped.
  rest=${line#MSG "$id" |}; addr=${rest%%|*}; addr=$(printf '%s' "$addr" | tr 'A-Z' 'a-z'); addr=${addr//[[:space:]]/}
  if ! [[ "$addr" =~ ^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$ ]]; then
    printf '%s client check: dropped %s: sender field is not one plain address\n' "$(date '+%F %T')" "$id" >>"$LOG"
    continue
  fi
  ok=""; while IFS= read -r p; do sender_is "$addr" "$p" && { ok=1; break; }; done <<<"$pats"
  [[ -n "$ok" ]] || continue
  # The tag sits right after the checked hex ID, before the first "|", so no From, Subject or Date text can fake it.
  tag=""
  pay=""; while IFS= read -r p; do sender_is "$addr" "$p" && { pay=1; break; }; done <<<"$paypats"
  # A payment row often names a whole domain (Gusto's), and that domain also mails marketing. Gmail files that
  # under Promotions, so for payment senders a Promotions message is not a payment notice and is dropped (28 Oct:
  # a promo was queued as client mail).
  [[ -n "$pay" && "$labels" == *CATEGORY_PROMOTIONS* ]] && continue
  if [[ "$addr" == no-reply@* || "$addr" == noreply@* ]]; then
    [[ -n "$pay" ]] || tag=" ALERT"
  fi
  grep -qxF "$id" "$SEEN" && continue
  grep -qF -- "$id" "$INBOX" 2>/dev/null && continue
  # From, Subject and Date pass through as printed (between the address and the labels), for the reader only.
  mid=${rest#*|}; mid=${mid%|*}; mid=${mid%|*}
  # MUSE_CLIENT_PAYIDS (muse_poll.sh sets it): a file that collects the IDs of the payment-kind lines printed below, one per line.
  # The poller grants the Gmail message reader and the Gusto script to the handler only when it holds an ID, and the Gusto script
  # refuses any --ref that is not in it (29 Oct hub security call: an injected mail must not steer an unattended pass).
  [[ -n "$pay" && -n "${MUSE_CLIENT_PAYIDS:-}" ]] && printf '%s\n' "$id" >>"$MUSE_CLIENT_PAYIDS"
  printf 'NEW %s%s | %s |%s\n' "$id" "$tag" "$addr" "$mid"
done
