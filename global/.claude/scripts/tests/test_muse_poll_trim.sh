#!/usr/bin/env bash
# Card 4 poll trim: the hot-phase floor is 5 minutes, client mail looks at most every 10, and every
# "nothing new" tick costs exactly one Haiku look. Uses a fake claude and a fake HOME; nothing live is touched.
set -euo pipefail
SCRIPT=$(cd -- "$(dirname -- "$0")/.." && pwd)/muse_poll.sh
TMP=$(mktemp -d); trap 'rm -rf -- "$TMP"' EXIT
H=$TMP/home; ST=$H/.claude/state; CALLS=$TMP/calls
mkdir -p "$H/.claude/scripts" "$H/.local/bin" "$H/repos" "$ST"
cat >"$H/.local/bin/claude" <<MOCK
#!/usr/bin/env bash
echo "claude \$*" | cut -c1-60 >>"$CALLS"
echo "\${FAKE_OUT:-NONE}"
MOCK
cat >"$H/.claude/scripts/muse_poll_client_check.sh" <<MOCK
#!/usr/bin/env bash
echo client >>"$CALLS"
MOCK
mkdir -p "$H/repos/hub/scripts"   # the mail index: a fake that records each call
cat >"$H/repos/hub/scripts/mail_index.py" <<MOCK
import sys
open("$CALLS", "a").write("mailidx " + sys.argv[1] + "\\n")
MOCK
echo mock >"$H/.claude/scripts/muse_poll.check.prompt"; echo mock >"$H/.claude/scripts/muse_poll.handle.prompt"
chmod +x "$H/.local/bin/claude" "$H/.claude/scripts/muse_poll_client_check.sh"

run() { : >"$CALLS"; HOME=$H "$@" "$SCRIPT" >/dev/null 2>&1 || true; }
count() { grep -c "^$1" "$CALLS" || true; }
ok() { [[ "$2" == "$3" ]] && printf 'PASS: %s\n' "$1" || { printf 'FAIL: %s (expected %s, got %s)\n' "$1" "$2" "$3" >&2; exit 1; }; }
fresh() {   # ACTIVE a minute old (hot phase); LAST $1 old; CLIENT_LAST $2 old (or absent)
  rm -f "$ST"/muse-poll-*.stamp; touch "$ST/user-active.stamp"; touch -d '1 minute ago' "$ST/muse-active.stamp"
  [[ -n "$1" ]] && touch -d "$1 ago" "$ST/muse-poll-last-check.stamp"
  [[ -n "${2:-}" ]] && touch -d "$2 ago" "$ST/muse-poll-client-last.stamp"
  return 0
}

fresh '2 minutes'; run env
ok 'hot phase, last look 2 min ago: not due, no model' 0 "$(count claude)"
fresh '2 minutes'; run env MUSE_POLL_FLOOR=60
ok 'mutation: the old 60 s floor would have run' 1 "$(count claude)"

fresh '6 minutes'; run env
ok 'hot phase, last look 6 min ago: one Haiku look' 1 "$(count claude)"
ok 'first client look runs' 1 "$(count client)"

fresh '6 minutes' '2 minutes'; run env
ok 'client look 2 min ago: skipped' 0 "$(count client)"
ok 'the Hatch look still ran once' 1 "$(count claude)"
fresh '6 minutes' '2 minutes'; run env MUSE_CLIENT_EVERY=60
ok 'mutation: a 60 s client spacing would have run it' 1 "$(count client)"

fresh '6 minutes' '11 minutes'; run env
ok 'client look 11 min ago: runs' 1 "$(count client)"

fresh '6 minutes' '11 minutes'; run env FAKE_OUT='NEW abc123'
ok 'a note found: look plus handler' 2 "$(count claude)"
[[ $(( $(date +%s) - $(stat -c %Y "$ST/muse-active.stamp") )) -lt 30 ]] && echo 'PASS: a found note restarts the hot phase' || { echo 'FAIL: active stamp not touched' >&2; exit 1; }

fresh '6 minutes' '11 minutes'; run env FAKE_OUT='NEW *** a|b'
ok 'a NEW answer with no usable id: look only, no handler' 1 "$(count claude)"
[[ $(( $(date +%s) - $(stat -c %Y "$ST/muse-active.stamp") )) -ge 30 ]] && echo 'PASS: no usable id leaves the hot phase alone' || { echo 'FAIL: no usable id touched the active stamp' >&2; exit 1; }

fresh '6 minutes'; run env
ok 'mail index: update and jobs-queue run on a due poll' 1 "$(count 'mailidx update')"
ok 'mail index: jobs-queue ran too' 1 "$(count 'mailidx jobs-queue')"
ok 'mail index: replies sweep ran too' 1 "$(count 'mailidx replies')"
queue_line=$(grep -n '^mailidx jobs-queue$' "$CALLS" | cut -d: -f1)
replies_line=$(grep -n '^mailidx replies$' "$CALLS" | cut -d: -f1)
[[ "$replies_line" -gt "$queue_line" ]] && echo 'PASS: replies sweep follows jobs-queue' || { echo 'FAIL: replies sweep order' >&2; exit 1; }
ok 'mail index: replies sweep has a 120-second timeout' 1 "$(grep -Fxc '  timeout 120 python3 -I "$MAILIDX" replies >>"$LOG" 2>&1' "$SCRIPT")"
fresh '6 minutes'; touch -d '2 minutes ago' "$ST/muse-poll-mail-index-last.stamp"; run env
ok 'mail index looked 2 min ago: skipped' 0 "$(count mailidx)"
fresh '6 minutes'; touch -d '2 minutes ago' "$ST/muse-poll-mail-index-last.stamp"; run env MUSE_MAIL_INDEX_EVERY=60
ok 'mutation: a 60 s spacing would have run it' 1 "$(count 'mailidx update')"
rm -f "$H/repos/hub/scripts/mail_index.py"; fresh '6 minutes'; run env
ok 'script missing: poll goes on, Hatch look still ran' 1 "$(count claude)"
