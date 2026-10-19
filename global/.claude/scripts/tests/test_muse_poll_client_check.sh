#!/usr/bin/env bash
# The client-mail checker keeps only real Gmail message IDs (15-16 hex), logs what it drops, and
# muse_poll_record.sh client-seen refuses anything else. A stub claude stands in for the Haiku pass;
# a fake HOME holds the state. Nothing live is touched.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d); trap 'rm -rf -- "$TMP"' EXIT
H=$TMP/home; ST=$H/.claude/state
mkdir -p "$H/.claude/scripts" "$H/.config/afa" "$ST"
echo NX-01 >"$H/.config/afa/machine"
cp "$HERE/muse_poll.client-check.prompt" "$H/.claude/scripts/"
printf 'client\trepo_dir\tkind\tgmail_from\nExample Brewing\t/x\tmail\tbrewer@example.test\n' >"$TMP/senders.tsv"
: >"$TMP/seen.txt"; : >"$TMP/inbox.md"
cat >"$TMP/claude" <<'MOCK'
#!/usr/bin/env bash
cat <<'OUT'
MSG 1792119952000 | Pat <brewer@example.test> | Re: Tap list | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | 1a14c8be4df08299
MSG 1a14c8be4df08299 | Pat <brewer@example.test> | Re: Tap list | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | 1a14c8be4df08299
OUT
MOCK
chmod +x "$TMP/claude"

check() { HOME=$H MUSE_CLIENT_TSV=$TMP/senders.tsv MUSE_CLIENT_SEEN=$TMP/seen.txt MUSE_CLIENT_INBOX=$TMP/inbox.md \
  MUSE_CLIENT_CLAUDE=$TMP/claude bash "$HERE/muse_poll_client_check.sh"; }
ok() { [[ "$2" == "$3" ]] && printf 'PASS: %s\n' "$1" || { printf 'FAIL: %s (expected %s, got %s)\n' "$1" "$2" "$3" >&2; exit 1; }; }

out=$(check)
ok 'only the real ID comes out NEW' 1 "$(printf '%s\n' "$out" | grep -c '^NEW ')"
ok 'the NEW line carries the 16-hex ID' 1 "$(printf '%s\n' "$out" | grep -c '^NEW 1a14c8be4df08299 |')"
ok 'the timestamp-as-ID line is not NEW' 0 "$(printf '%s\n' "$out" | grep -c '1792119952000')"
ok 'the drop is logged' 1 "$(grep -c 'client check: dropped malformed line.*1792119952000' "$ST/muse-poll.log")"

rec() { MUSE_STATE=$ST bash "$HERE/muse_poll_record.sh" "$@"; }
: >"$ST/muse-inbox.md"
if rec client-seen "1792119952000" 2>/dev/null; then ok 'client-seen refuses a 13-digit timestamp' refused accepted; fi
ok 'client-seen refuses a 13-digit timestamp' 0 "$(grep -c 1792119952000 "$ST/client-mail-seen.txt" 2>/dev/null || true)"
rec client-seen "1a14c8be4df08299" && ok 'client-seen takes a 16-hex ID' 1 "$(grep -c 1a14c8be4df08299 "$ST/client-mail-seen.txt")"

# A kind=payment sender (Gusto) mails from a no-reply address and must still come out NEW, because its handler
# marks the invoice paid; a no-reply sender of any other kind is still skipped.
printf 'client\trepo_dir\tkind\tgmail_from\nPayroll\t/x\tpayment\tno-reply@pay.example.test\nNews\t/x\tmail\tnoreply@news.example.test\n' >"$TMP/senders.tsv"
cat >"$TMP/claude" <<'MOCK'
#!/usr/bin/env bash
cat <<'OUT'
MSG 2a14c8be4df08299 | Pay <no-reply@pay.example.test> | Payment sent | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | 2a14c8be4df08299
MSG 3a14c8be4df08299 | News <noreply@news.example.test> | Digest | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | 3a14c8be4df08299
OUT
MOCK
out=$(check)
ok 'a no-reply payment sender comes out NEW' 1 "$(printf '%s\n' "$out" | grep -c '^NEW 2a14c8be4df08299 |')"
ok 'a no-reply sender of another kind is skipped' 0 "$(printf '%s\n' "$out" | grep -c 3a14c8be4df08299)"
