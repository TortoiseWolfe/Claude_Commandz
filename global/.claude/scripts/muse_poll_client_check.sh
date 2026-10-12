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
# and nothing when there is nothing new. Names live only in the TSV and Gmail; this script and its prompt
# hold none. Overrides for tests and seeding: MUSE_CLIENT_TSV, MUSE_CLIENT_SEEN (point at copies),
# MUSE_CLIENT_WINDOW (Gmail newer_than value, default 2d).
set -u
S=$HOME/.claude/scripts
TSV=${MUSE_CLIENT_TSV:-$HOME/repos/hub/client-senders.tsv}
SEEN=${MUSE_CLIENT_SEEN:-$HOME/.claude/state/client-mail-seen.txt}
LOG=$HOME/.claude/state/muse-poll.log
CLAUDE=$HOME/.local/bin/claude
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
printf '%s\n' "$out" | grep -E '^MSG [0-9a-f]{8,20} \|' | while IFS= read -r line; do
  id=${line#MSG }; id=${id%% *}
  IFS='|' read -r _ _ _ _ labels _ <<<"${line#MSG }"
  labels=$(printf '%s' "$labels" | tr 'a-z' 'A-Z')
  [[ -n "${labels//[[:space:]]/}" ]] || continue
  [[ "$labels" == *DRAFT* || "$labels" == *SENT* ]] && continue
  from=$(printf '%s' "${line#MSG * | }" | cut -d'|' -f1 | tr 'A-Z' 'a-z')
  [[ "$from" == *no-reply@* || "$from" == *noreply@* ]] && continue
  ok=""; while IFS= read -r p; do [[ -n "$p" && "$from" == *"$p"* ]] && { ok=1; break; }; done <<<"$pats"
  [[ -n "$ok" ]] || continue
  grep -qxF "$id" "$SEEN" || printf 'NEW %s\n' "$(printf '%s' "${line#MSG }" | cut -d'|' -f1-4)"
done
