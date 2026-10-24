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
# Compute test IDs in the test script for assertions
now_ms=$(( $(date +%s) * 1000 ))
id_now=$(printf '%016x' $(( (now_ms << 20) | 12345 )))
id_future=$(printf '%016x' $(( ((now_ms + 172800000) << 20) | 12345 )))
id_past=$(printf '%016x' $(( ((now_ms - 5184000000) << 20) | 12345 )))
cat >"$TMP/claude" <<MOCK
#!/usr/bin/env bash
cat <<OUT
MSG 1792119952000 | Pat <brewer@example.test> | Re: Tap list | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_now
MSG $id_now | Pat <brewer@example.test> | Re: Tap list | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_now
MSG $id_future | Pat <brewer@example.test> | Future mail | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_future
MSG $id_past | Pat <brewer@example.test> | Old mail | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_past
OUT
MOCK
chmod +x "$TMP/claude"

check() { HOME=$H MUSE_CLIENT_TSV=$TMP/senders.tsv MUSE_CLIENT_SEEN=$TMP/seen.txt MUSE_CLIENT_INBOX=$TMP/inbox.md \
  MUSE_CLIENT_CLAUDE=$TMP/claude bash "$HERE/muse_poll_client_check.sh"; }
ok() { [[ "$2" == "$3" ]] && printf 'PASS: %s\n' "$1" || { printf 'FAIL: %s (expected %s, got %s)\n' "$1" "$2" "$3" >&2; exit 1; }; }

out=$(check)
ok 'only the current-time ID comes out NEW' 1 "$(printf '%s\n' "$out" | grep -c '^NEW ')"
ok 'the NEW line carries the 16-hex current ID' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_now |")"
ok 'the timestamp-as-ID line is not NEW' 0 "$(printf '%s\n' "$out" | grep -c '1792119952000')"
ok 'the future ID is not NEW' 0 "$(printf '%s\n' "$out" | grep -c "$id_future")"
ok 'the past ID is not NEW' 0 "$(printf '%s\n' "$out" | grep -c "$id_past")"
ok 'the malformed drop is logged' 1 "$(grep -c 'client check: dropped malformed line.*1792119952000' "$ST/muse-poll.log")"
ok 'the future ID drop is logged' 1 "$(grep -c "client check: dropped implausible id.*$id_future" "$ST/muse-poll.log")"
ok 'the past ID drop is logged' 1 "$(grep -c "client check: dropped implausible id.*$id_past" "$ST/muse-poll.log")"

rec() { MUSE_STATE=$ST bash "$HERE/muse_poll_record.sh" "$@"; }
: >"$ST/hub-inbox.md"
if rec client-seen "1792119952000" 2>/dev/null; then ok 'client-seen refuses a 13-digit timestamp' refused accepted; fi
ok 'client-seen refuses a 13-digit timestamp' 0 "$(grep -c 1792119952000 "$ST/client-mail-seen.txt" 2>/dev/null || true)"
rec client-seen "1a14c8be4df08299" && ok 'client-seen takes a 16-hex ID' 1 "$(grep -c 1a14c8be4df08299 "$ST/client-mail-seen.txt")"

# A no-reply sender at a client domain must come out "NEW <id> ALERT |", except a kind=payment sender (Gusto),
# which comes out NEW untagged because its own handler step marks the invoice paid; a no-reply sender at an
# unrelated domain is still skipped. Matching reads the address only, never the display name, and the tag sits
# right after the hex ID, so neither a display name nor a Subject can fake a client or an alert.
now_ms2=$(( $(date +%s) * 1000 ))
id_pay=$(printf '%016x' $(( (now_ms2 << 20) | 23456 )))
id_news=$(printf '%016x' $(( (now_ms2 << 20) | 34567 )))
id_random_noreply=$(printf '%016x' $(( (now_ms2 << 20) | 56789 )))
id_spoof=$(printf '%016x' $(( (now_ms2 << 20) | 61111 )))
id_subj=$(printf '%016x' $(( (now_ms2 << 20) | 62222 )))
id_sub=$(printf '%016x' $(( (now_ms2 << 20) | 63333 )))
id_lookalike=$(printf '%016x' $(( (now_ms2 << 20) | 64444 )))
printf 'client\trepo_dir\tkind\tgmail_from\nPayroll\t/x\tpayment\tno-reply@pay.example.test\nNews\t/x\tmail\tnoreply@news.example.test\nShop\t/x\tmail\tshop.example.test\n' >"$TMP/senders.tsv"
cat >"$TMP/claude" <<MOCK
#!/usr/bin/env bash
cat <<OUT
MSG $id_pay | Pay <no-reply@pay.example.test> | Payment sent | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_pay
MSG $id_news | News <noreply@news.example.test> | Digest | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_news
MSG $id_random_noreply | Bot <no-reply@random.test> | Spam | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_random_noreply
MSG $id_spoof | noreply@news.example.test shop.example.test <noreply@evil.test> | Urgent | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_spoof
MSG $id_subj | Pat <orders@shop.example.test> | Order ALERT | Client alert | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_subj
MSG $id_sub | Store <no-reply@mail.shop.example.test> | Site notice | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_sub
MSG $id_lookalike | Shop <no-reply@notshop.example.test> | Hi | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_lookalike
OUT
MOCK
out=$(check)
ok 'a no-reply payment sender comes out NEW, untagged' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_pay |")"
ok 'a no-reply client sender comes out NEW with ALERT after its ID' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_news ALERT |")"
ok 'unrelated no-reply sender is still skipped' 0 "$(printf '%s\n' "$out" | grep -c "$id_random_noreply")"
ok 'a client domain in the display name only is skipped' 0 "$(printf '%s\n' "$out" | grep -c "$id_spoof")"
ok 'ALERT in a Subject does not tag the line' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_subj |")"
ok 'a no-reply sender on a client subdomain is tagged' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_sub ALERT |")"
ok 'a lookalike domain ending in the client name is skipped' 0 "$(printf '%s\n' "$out" | grep -c "$id_lookalike")"
ok 'exactly two lines carry the tag' 2 "$(printf '%s\n' "$out" | grep -cE '^NEW [0-9a-f]{15,16} ALERT \|')"
