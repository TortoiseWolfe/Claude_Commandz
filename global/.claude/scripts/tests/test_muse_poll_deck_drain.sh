#!/usr/bin/env bash
# The poller calls "deck_write.py drain" on every pass, before its throttle and night gates, so queued co-op Deck writes
# go out on their own schedule. Fake HOME and a fake deck_write.py that only records its arguments; nothing live is touched.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
SCRIPT=$(cd -- "$(dirname -- "$0")/.." && pwd)/muse_poll.sh
TMP=$(mktemp -d); trap 'rm -rf -- "$TMP"' EXIT
H=$TMP/home; ST=$H/.claude/state; CALLS=$TMP/calls
mkdir -p "$H/.claude/scripts" "$H/.local/bin" "$H/repos" "$ST"
cat >"$H/.claude/scripts/deck_write.py" <<MOCK
import sys
open("$CALLS", "a").write("deck " + " ".join(sys.argv[1:]) + "\\n")
MOCK
cat >"$H/.local/bin/claude" <<MOCK
#!/usr/bin/env bash
echo "NONE"
MOCK
chmod +x "$H/.local/bin/claude"
ok() { [[ "$2" == "$3" ]] && printf 'PASS: %s\n' "$1" || { printf 'FAIL: %s (expected %s, got %s)\n' "$1" "$2" "$3" >&2; exit 1; }; }
run() { : >"$CALLS"; HOME=$H MUSE_POLL_LOCK=$TMP/poll.lock "$SCRIPT" >/dev/null 2>&1 || true; }

# Throttled pass: the last look was 1 minute ago and the hot phase wants 5, so the poll itself exits early.
touch "$ST/user-active.stamp" "$ST/muse-active.stamp"; touch -d '1 minute ago' "$ST/muse-poll-last-check.stamp"
run
ok 'a throttled pass still drains once' 1 "$(grep -c '^deck drain$' "$CALLS" || true)"

# The poll lock held by another pass: the drain has its own lock and still runs.
: >"$CALLS"; exec 7>"$TMP/poll.lock"; flock -n 7
HOME=$H MUSE_POLL_LOCK=$TMP/poll.lock "$SCRIPT" >/dev/null 2>&1 || true
ok 'a busy poll lock does not hold the drain up' 1 "$(grep -c '^deck drain$' "$CALLS" || true)"
exec 7>&-

# No deck_write.py (a machine without it): the poll carries on.
rm -f "$H/.claude/scripts/deck_write.py"; run
ok 'a missing deck_write.py is skipped' 0 "$(grep -c '^deck' "$CALLS" || true)"
echo 'ALL PASS'
