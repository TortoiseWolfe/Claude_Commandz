#!/usr/bin/env bash
# Claude_Commandz issue 29: muse_poll_record.sh inbox stores the whole line (no silent cut at 400). Past 4000 characters
# the cut is marked " ... [cut]". The duplicate check still reads the first 400 characters exactly as before. Scratch folder only.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
REC=$(cd -- "$(dirname -- "$0")/.." && pwd)/muse_poll_record.sh
TMP=$(mktemp -d); trap 'rm -rf -- "$TMP"' EXIT
export MUSE_STATE=$TMP HUB_INBOX=$TMP/hub-inbox.md
ok() { [[ "$2" == "$3" ]] && printf 'PASS: %s\n' "$1" || { printf 'FAIL: %s (expected %s, got %s)\n' "$1" "$2" "$3" >&2; exit 1; }; }
pad() { head -c "$1" /dev/zero | tr '\0' 'x'; }

# 1. a 900-character line keeps its tail.
: >"$HUB_INBOX"
bash "$REC" inbox "Job: long one $(pad 880) END-OF-LINE" >/dev/null
ok 'long line keeps its tail' 1 "$(grep -c 'END-OF-LINE$' "$HUB_INBOX")"

# 2. a line over 4000 is cut and marked.
: >"$HUB_INBOX"
bash "$REC" inbox "Job: huge $(pad 5000) TAIL" >/dev/null
ok 'huge line is marked' 1 "$(grep -c '\[cut\]$' "$HUB_INBOX")"
ok 'huge line has no tail' 0 "$(grep -c 'TAIL' "$HUB_INBOX" || true)"
len=$(awk '{print length($0)}' "$HUB_INBOX")
[[ $len -lt 4100 ]] && printf 'PASS: huge line stays under the cap\n' || { echo "FAIL: length $len" >&2; exit 1; }

# 3. the duplicate check still catches a repeated note id (read from the first 400 characters, as before).
: >"$HUB_INBOX"
bash "$REC" inbox "Hub: short | detail | r-5555555555" >/dev/null
out=$(bash "$REC" inbox "Hub: short | detail | r-5555555555")
ok 'duplicate id still caught' 'already queued: r-5555555555' "$out"
ok 'one line' 1 "$(grep -c 'r-5555555555' "$HUB_INBOX")"
