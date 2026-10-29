#!/usr/bin/env bash
# The client-mail checker keeps only real Gmail message IDs (15-16 hex), logs what it drops, and
# muse_poll_record.sh client-seen refuses anything else. A stub gmail_api.py stands in for the Gmail API;
# a fake HOME holds the state. Nothing live is touched.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d); trap 'rm -rf -- "$TMP"' EXIT
H=$TMP/home; ST=$H/.claude/state
mkdir -p "$H/.claude/scripts" "$H/.config/afa" "$ST"
echo NX-01 >"$H/.config/afa/machine"
printf 'client\trepo_dir\tkind\tgmail_from\nExample Brewing\t/x\tmail\tbrewer@example.test\n' >"$TMP/senders.tsv"
: >"$TMP/seen.txt"; : >"$TMP/inbox.md"
# Compute test IDs in the test script for assertions
now_ms=$(( $(date +%s) * 1000 ))
id_now=$(printf '%016x' $(( (now_ms << 20) | 12345 )))
id_future=$(printf '%016x' $(( ((now_ms + 172800000) << 20) | 12345 )))
id_past=$(printf '%016x' $(( ((now_ms - 5184000000) << 20) | 12345 )))
cat >"$TMP/claude" <<MOCK
#!/usr/bin/env bash
echo "\$*" >"$TMP/stub-args"
cat <<OUT
MSG 1792119952000 | brewer@example.test | Pat <brewer@example.test> | Re: Tap list | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_now
MSG $id_now | brewer@example.test | Pat <brewer@example.test> | Re: Tap list | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_now
MSG $id_future | brewer@example.test | Pat <brewer@example.test> | Future mail | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_future
MSG $id_past | brewer@example.test | Pat <brewer@example.test> | Old mail | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_past
OUT
MOCK
chmod +x "$TMP/claude"

check() { HOME=$H MUSE_CLIENT_TSV=$TMP/senders.tsv MUSE_CLIENT_SEEN=$TMP/seen.txt MUSE_CLIENT_INBOX=$TMP/inbox.md \
  MUSE_CLIENT_GMAIL=$TMP/claude bash "$HERE/muse_poll_client_check.sh"; }
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
MSG $id_pay | no-reply@pay.example.test | Pay <no-reply@pay.example.test> | Payment sent | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_pay
MSG $id_news | noreply@news.example.test | News <noreply@news.example.test> | Digest | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_news
MSG $id_random_noreply | no-reply@random.test | Bot <no-reply@random.test> | Spam | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_random_noreply
MSG $id_spoof | noreply@evil.test | noreply@news.example.test shop.example.test <noreply@evil.test> | Urgent | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_spoof
MSG $id_subj | orders@shop.example.test | Pat <orders@shop.example.test> | Order ALERT | Client alert | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_subj
MSG $id_sub | no-reply@mail.shop.example.test | Store <no-reply@mail.shop.example.test> | Site notice | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_sub
MSG $id_lookalike | no-reply@notshop.example.test | Shop <no-reply@notshop.example.test> | Hi | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_lookalike
OUT
MOCK
out=$(check)
ok 'a no-reply payment sender comes out NEW, untagged' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_pay |")"
ok 'a no-reply client sender comes out NEW with ALERT after its ID' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_news ALERT |")"
ok 'unrelated no-reply sender is still skipped' 0 "$(printf '%s\n' "$out" | grep -c "$id_random_noreply")"
ok 'a client address in the display name only is skipped' 0 "$(printf '%s\n' "$out" | grep -c "$id_spoof")"
ok 'ALERT in a Subject does not tag the line' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_subj |")"
# 29 Oct 2026: with MUSE_CLIENT_PAYIDS set, the ID of every payment-kind line printed goes to that file, and only those.
# The poller grants the mail reader and the Gusto script to the client handler only when it is not empty.
: >"$TMP/pay.ids"; out=$(MUSE_CLIENT_PAYIDS=$TMP/pay.ids check)
ok 'only the payment-kind ID reaches MUSE_CLIENT_PAYIDS' "$id_pay" "$(cat "$TMP/pay.ids")"
ok 'the other NEW lines still print' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_news ALERT |")"
ok 'a no-reply sender on a client subdomain is tagged' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_sub ALERT |")"
ok 'a lookalike domain ending in the client name is skipped' 0 "$(printf '%s\n' "$out" | grep -c "$id_lookalike")"
ok 'exactly two lines carry the tag' 2 "$(printf '%s\n' "$out" | grep -cE '^NEW [0-9a-f]{15,16} ALERT \|')"

# Field two is the only thing matched. A display name carrying "|" and a client address can't shift it; a sender
# field that isn't one plain address is logged and dropped; a "|" in a Subject leaves the labels readable.
id_pipe=$(printf '%016x' $(( (now_ms2 << 20) | 65555 )))
id_badaddr=$(printf '%016x' $(( (now_ms2 << 20) | 66666 )))
id_subjpipe=$(printf '%016x' $(( (now_ms2 << 20) | 67777 )))
id_draftpipe=$(printf '%016x' $(( (now_ms2 << 20) | 68888 )))
cat >"$TMP/claude" <<MOCK
#!/usr/bin/env bash
cat <<OUT
MSG $id_pipe | noreply@evil.test | "x <noreply@news.example.test> | y" <noreply@evil.test> | Hi | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_pipe
MSG $id_badaddr | Pat <orders@shop.example.test | Pat <orders@shop.example.test> | Hi | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_badaddr
MSG $id_subjpipe | orders@shop.example.test | Pat <orders@shop.example.test> | Order 12 | shipped | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_subjpipe
MSG $id_draftpipe | orders@shop.example.test | Pat <orders@shop.example.test> | Re: a | b | Sat, 17 Oct 2026 21:00:00 | DRAFT | $id_draftpipe
OUT
MOCK
out=$(check)
ok 'a "|" and client address in a display name do not pass' 0 "$(printf '%s\n' "$out" | grep -c "$id_pipe")"
ok 'a sender field that is not one plain address is dropped' 0 "$(printf '%s\n' "$out" | grep -c "$id_badaddr")"
ok 'that drop is logged' 1 "$(grep -c "dropped $id_badaddr: sender field" "$ST/muse-poll.log")"
ok 'a "|" in a client Subject still comes out NEW, untagged' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_subjpipe | orders@shop.example.test |")"
ok 'a "|" in a Subject does not hide the DRAFT label' 0 "$(printf '%s\n' "$out" | grep -c "$id_draftpipe")"

# The trailer is checked before the labels are trusted: a line with no thread ID at the end, or a labels field
# that isn't label IDs, is logged and dropped, never read as inbox mail.
id_notid=$(printf '%016x' $(( (now_ms2 << 20) | 69999 )))
id_badlabels=$(printf '%016x' $(( (now_ms2 << 20) | 71111 )))
id_goodlabels=$(printf '%016x' $(( (now_ms2 << 20) | 72222 )))
cat >"$TMP/claude" <<MOCK
#!/usr/bin/env bash
cat <<OUT
MSG $id_notid | orders@shop.example.test | Pat <orders@shop.example.test> | Re: order | Sat, 17 Oct 2026 21:00:00 | DRAFT
MSG $id_badlabels | orders@shop.example.test | Pat <orders@shop.example.test> | Re: order | Sat, 17 Oct 2026 21:00:00 | see you soon! | $id_badlabels
MSG $id_goodlabels | orders@shop.example.test | Pat <orders@shop.example.test> | Re: order | Sat, 17 Oct 2026 21:00:00 | INBOX, UNREAD, Label_32, CATEGORY_UPDATES | $id_goodlabels
OUT
MOCK
out=$(check)
ok 'a line with no thread ID at the end is dropped' 0 "$(printf '%s\n' "$out" | grep -c "$id_notid")"
ok 'that drop is logged' 1 "$(grep -c "dropped $id_notid: no thread ID" "$ST/muse-poll.log")"
ok 'a labels field that is not label IDs is dropped' 0 "$(printf '%s\n' "$out" | grep -c "$id_badlabels")"
ok 'real label IDs with spaces and a user label pass' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_goodlabels |")"

# A payment row that names a whole domain also catches that domain's marketing. Gmail files marketing under
# CATEGORY_PROMOTIONS; for kind=payment senders that message is dropped, the real notice still comes out, and a
# Promotions message from a non-payment client is left alone (a client's mail can be mis-filed).
id_promo=$(printf '%016x' $(( (now_ms2 << 20) | 71111 )))
id_notice=$(printf '%016x' $(( (now_ms2 << 20) | 72222 )))
id_promo_mail=$(printf '%016x' $(( (now_ms2 << 20) | 73333 )))
id_promo_other=$(printf '%016x' $(( (now_ms2 << 20) | 74444 )))
printf 'client\trepo_dir\tkind\tgmail_from\nPayroll\t/x\tpayment\tpayco.example.test\nShop\t/x\tmail\tshop.example.test\n' >"$TMP/senders.tsv"
cat >"$TMP/claude" <<MOCK
#!/usr/bin/env bash
cat <<OUT
MSG $id_promo | news@payco.example.test | PayCo <news@payco.example.test> | Save big this week | Sat, 17 Oct 2026 21:00:00 | CATEGORY_PROMOTIONS,UNREAD,INBOX | $id_promo
MSG $id_notice | no-reply@payco.example.test | PayCo <no-reply@payco.example.test> | Payroll processed | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_notice
MSG $id_promo_mail | no-reply@payco.example.test | PayCo <no-reply@payco.example.test> | Another offer | Sat, 17 Oct 2026 21:00:00 | CATEGORY_PROMOTIONS,UNREAD | $id_promo_mail
MSG $id_promo_other | orders@shop.example.test | Pat <orders@shop.example.test> | Re: order | Sat, 17 Oct 2026 21:00:00 | CATEGORY_PROMOTIONS,UNREAD,INBOX | $id_promo_other
OUT
MOCK
out=$(check)
ok 'a Promotions message from a payment sender is dropped' 0 "$(printf '%s\n' "$out" | grep -c "$id_promo" || true)"
ok 'a Promotions no-reply message from a payment sender is dropped' 0 "$(printf '%s\n' "$out" | grep -c "$id_promo_mail" || true)"
ok 'the payment notice from the same domain still comes out' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_notice |")"
ok 'a Promotions message from a non-payment client still comes out' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_promo_other |")"

# No model pass: the stub is called as `messages-list <query>` and nothing else, and its IDs come out byte for byte.
cat >"$TMP/claude" <<MOCK
#!/usr/bin/env bash
echo "\$*" >"$TMP/stub-args"
echo "MSG 1a15559aee5b0b47 | orders@shop.example.test | Pat <orders@shop.example.test> | Re: order | Mon, 19 Oct 2026 21:00:00 | INBOX,UNREAD | 1a15559aee5b0b47"
MOCK
rm -f "$TMP/stub-args"
out=$(check)
ok 'the ID prints exactly as listed' 1 "$(printf '%s\n' "$out" | grep -c '^NEW 1a15559aee5b0b47 | orders@shop.example.test | Pat <orders@shop.example.test> | Re: order | Mon, 19 Oct 2026 21:00:00 $' || true)"
ok 'the lister gets the subcommand and the sender query' 1 "$(grep -c '^messages-list from:(.*shop.example.test.*) newer_than:2d -from:me$' "$TMP/stub-args" || true)"

# A failing lister (dead token, no network) is loud: nothing NEW, exit 1, the reason on stderr, never a quiet "nothing new".
cat >"$TMP/claude" <<'MOCK'
#!/usr/bin/env bash
echo "gmail_api: Gmail API 401 on GET /messages: invalid credentials" >&2
exit 1
MOCK
rc=0; out=$(check 2>"$TMP/err") || rc=$?
ok 'a failing lister exits 1' 1 "$rc"
ok 'a failing lister prints no NEW line' 0 "$(printf '%s\n' "$out" | grep -c '^NEW ' || true)"
ok 'the failure reason reaches stderr' 1 "$(grep -c 'messages-list failed: gmail_api: Gmail API 401' "$TMP/err" || true)"

# An empty list is quiet and fine.
printf '#!/usr/bin/env bash\nexit 0\n' >"$TMP/claude"
rc=0; out=$(check) || rc=$?
ok 'an empty list exits 0 with no output' "0:" "$rc:$out"
