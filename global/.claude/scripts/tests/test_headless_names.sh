#!/usr/bin/env bash
# Fleet review C16: 2,110 sessions in a week belonged to no lane, because the unattended `claude -p` calls (the Muse poller, the tower
# poller, the dispatcher's pass) passed no name. Claude Code writes a --name into the session file as its custom title, so the ledger can
# tie a session to the job that started it. This test holds every headless call in the shipped scripts to a --name, so a new call that
# forgets one fails here.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
D=$(cd -- "$(dirname -- "$0")/.." && pwd)
fails=0
check() {  # <file> <the text that starts the call's line> <expected name>
  local line
  line=$(grep -F -- "$2" "$D/$1" | head -1)
  if [ -z "$line" ]; then printf 'FAIL: %s: no call starting with: %s\n' "$1" "$2" >&2; fails=$((fails+1)); return; fi
  if printf '%s' "$line" | grep -qF -- "--name $3"; then printf 'PASS: %s names its session %s\n' "$1" "$3"
  else printf 'FAIL: %s: call lacks --name %s\n' "$1" "$3" >&2; fails=$((fails+1)); fi
}
check muse_poll.sh '"$CLAUDE" -p "$(cat "$S/muse_poll.check.prompt")"' muse-poll-check
check muse_poll.sh '"$CLAUDE" -p "$hprompt"' muse-poll-client-handle
check muse_poll.sh '"$CLAUDE" -p "$HANDLE_PROMPT"' muse-poll-handle
check peer_poll.sh '"$CLAUDE" -p "$(cat "$S/peer_poll.check.prompt")"' peer-poll-check
check peer_poll.sh '"$CLAUDE" -p "$(cat "$S/peer_poll.handle.prompt")"' peer-poll-handle
if grep -qF -- '"-p", prompt, "--name", "openclaw-dispatch-pass"' "$D/openclaw_dispatch.py"; then echo 'PASS: openclaw_dispatch.py names its pass'
else echo 'FAIL: openclaw_dispatch.py: the claude -p pass has no --name' >&2; fails=$((fails+1)); fi
# A new unnamed call: every shell line that runs claude -p must carry --name.
unnamed=$(grep -n -E '"\$CLAUDE" +-p ' "$D"/*.sh | grep -v -F -- '--name' || true)
if [ -z "$unnamed" ]; then echo 'PASS: no unnamed "$CLAUDE" -p call in the shell scripts'
else printf 'FAIL: unnamed headless call:\n%s\n' "$unnamed" >&2; fails=$((fails+1)); fi
[ "$fails" = 0 ] && echo "ok - every headless claude call names its session" || { echo "$fails failed" >&2; exit 1; }
