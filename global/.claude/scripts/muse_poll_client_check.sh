#!/usr/bin/env bash
# Client-mail check, shared by the notes poller (muse_poll.sh) and /prep.
#   muse_poll_client_check.sh
# Reads the client senders from the private hub file (hub/client-senders.tsv: client, repo_dir, kind,
# gmail_from), has a metadata-only Haiku pass list every message from those senders in the last two days
# (search_threads, never a body), then drops the IDs already absorbed. Prints one line per new message:
#   NEW <message id> | <From> | <Subject> | <Date>
# Only mail a client actually sent counts: the check prints each message's labels, and anything labelled
# DRAFT or SENT (our own drafts and replies, which ride along inside a matched thread) is dropped, as is any
# line with no labels field (fail closed).
# A message also stops being new once an inbox line (open or ticked) carries its ID: it is queued, so the
# handler must not run again for it. client-mail-seen.txt keeps its meaning (absorbed by the hub).
# and nothing when there is nothing new. Senders of no-reply@ or noreply@ are skipped UNLESS they match a
# client pattern: matching no-reply senders are marked "Client alert" so the handler queues them, except
# kind "payment" (Gusto), which come out unmarked because their handler step marks the invoice paid. Names live only in the TSV and Gmail; this script and its prompt
# hold none. Overrides for tests and seeding: MUSE_CLIENT_TSV, MUSE_CLIENT_SEEN, MUSE_CLIENT_INBOX (point at copies; the board is hub-inbox.md now), MUSE_CLIENT_CLAUDE (a stub for the Haiku pass),
# MUSE_CLIENT_WINDOW (Gmail newer_than value, default 2d).
set -u
S=$HOME/.claude/scripts
TSV=${MUSE_CLIENT_TSV:-$HOME/repos/hub/client-senders.tsv}
SEEN=${MUSE_CLIENT_SEEN:-$HOME/.claude/state/client-mail-seen.txt}
INBOX=${MUSE_CLIENT_INBOX:-${HUB_INBOX:-${MUSE_INBOX:-$HOME/.claude/state/hub-inbox.md}}}
LOG=$HOME/.claude/state/muse-poll.log
CLAUDE=${MUSE_CLIENT_CLAUDE:-$HOME/.local/bin/claude}
WINDOW=${MUSE_CLIENT_WINDOW:-2d}
# Client mail is NX-01's job only; the second tower exits quietly so the same mail is not absorbed twice.
[[ "$(cat "$HOME/.config/afa/machine" 2>/dev/null)" == NX-01 ]] || exit 0
[[ -r "$TSV" ]] || exit 0
froms=$(awk -F'\t' 'NR>1 && $4!="" {print $4}' "$TSV" | sort -u | paste -sd' ' | sed 's/ / OR /g')
[[ -n "$froms" ]] || exit 0
query="from:($froms) newer_than:$WINDOW -from:me"
prompt=$(cat "$S/muse_poll.client-check.prompt")
prompt=${prompt//\{\{QUERY\}\}/$query}
out=$(env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN timeout 300 "$CLAUDE" -p "$prompt" --model haiku --max-turns 12 \
  --permission-mode default \
  --disallowedTools "Bash,Edit,Write,Agent,WebFetch,WebSearch,Read" \
  --allowedTools "mcp__claude_ai_Gmail__search_threads" 2>>"$LOG")
touch "$SEEN"
# Keep only messages from a TSV sender (a thread that matched also holds Jonathan's own replies), and skip
# no-reply bots; then drop what has been absorbed already.
pats=$(awk -F'\t' 'NR>1 && $4!="" {print tolower($4)}' "$TSV")
# kind "payment" senders (Gusto) mail from no-reply addresses but have their own handler step (mark the invoice
# paid), so they are never tagged "Client alert"
paypats=$(awk -F'\t' 'NR>1 && $3=="payment" && $4!="" {print tolower($4)}' "$TSV")
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
  IFS='|' read -r _ _ _ _ labels _ <<<"${line#MSG }"
  labels=$(printf '%s' "$labels" | tr 'a-z' 'A-Z')
  [[ -n "${labels//[[:space:]]/}" ]] || continue
  [[ "$labels" == *DRAFT* || "$labels" == *SENT* ]] && continue
  from=$(printf '%s' "${line#MSG * | }" | cut -d'|' -f1 | tr 'A-Z' 'a-z')
  ok=""; while IFS= read -r p; do [[ -n "$p" && "$from" == *"$p"* ]] && { ok=1; break; }; done <<<"$pats"
  [[ -n "$ok" ]] || continue
  alert=""
  if [[ "$from" == *no-reply@* || "$from" == *noreply@* ]]; then
    pay=""; while IFS= read -r p; do [[ -n "$p" && "$from" == *"$p"* ]] && { pay=1; break; }; done <<<"$paypats"
    [[ -n "$pay" ]] || alert=" | Client alert"
  fi
  grep -qxF "$id" "$SEEN" && continue
  grep -qF -- "$id" "$INBOX" 2>/dev/null && continue
  printf 'NEW %s%s\n' "$(printf '%s' "${line#MSG }" | cut -d'|' -f1-4)" "$alert"
done
