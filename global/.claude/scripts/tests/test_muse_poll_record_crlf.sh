#!/usr/bin/env bash
# Claude_Commandz issue 30: a line that ends in a carriage return (CR, the invisible Windows line-end byte) must not
# hide a note id from muse_poll_record.sh. Three entry points: inbox (stored CR), done (awk id at the very end of the
# line) and client-seen (the seen file or the board line has CR). Scratch folder only; nothing live is touched.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
REC=$(cd -- "$(dirname -- "$0")/.." && pwd)/muse_poll_record.sh
TMP=$(mktemp -d); trap 'rm -rf -- "$TMP"' EXIT
export MUSE_STATE=$TMP HUB_INBOX=$TMP/hub-inbox.md
ok() { [[ "$2" == "$3" ]] && printf 'PASS: %s\n' "$1" || { printf 'FAIL: %s (expected %s, got %s)\n' "$1" "$2" "$3" >&2; exit 1; }; }

# 1. inbox: a CR at the end of the line is not stored, and the same note queued again is seen as a duplicate.
: >"$HUB_INBOX"
bash "$REC" inbox $'Waiting on Hatch: look it up | detail | r-1111111111\r' >/dev/null
ok 'inbox: no CR stored' 0 "$(grep -c $'\r' "$HUB_INBOX" || true)"
out=$(bash "$REC" inbox $'Waiting on Hatch: look it up | detail | r-1111111111\r')
ok 'inbox: the second copy is a duplicate' 'already queued: r-1111111111' "$out"
ok 'inbox: one line on the board' 1 "$(grep -c 'r-1111111111' "$HUB_INBOX")"

# 2. done: a board line saved with Windows line ends still ticks.
printf -- '- [ ] 2026-11-25 10:00 | Waiting on Hatch: windows line | detail | r-2222222222\r\n' >"$HUB_INBOX"
bash "$REC" done r-2222222222 >/dev/null
ok 'done: ticked despite CR' 1 "$(grep -c '^- \[x\] ' "$HUB_INBOX")"

# 3. client-seen: a seen file with Windows line ends, and a board line with CR, still tick the line.
printf -- '- [ ] 2026-11-25 10:00 | Client mail: a client | absorb lane | abcdef0123456789\r\n' >"$HUB_INBOX"
printf 'abcdef0123456789\r\n' >"$TMP/client-mail-seen.txt"
bash "$REC" client-seen abcdef0123456789 >/dev/null
ok 'client-seen: ticked despite CR in both files' 1 "$(grep -c '^- \[x\] ' "$HUB_INBOX")"

# Mutation guard: an id that is not in the seen file leaves the line open.
printf -- '- [ ] 2026-11-25 10:00 | Client mail: a client | absorb lane | 0123456789abcdef\r\n' >"$HUB_INBOX"
printf 'abcdef0123456789\r\n' >"$TMP/client-mail-seen.txt"
bash "$REC" client-seen abcdef0123456789 >/dev/null
ok 'client-seen: an unseen id stays open' 1 "$(grep -c '^- \[ \] ' "$HUB_INBOX")"
