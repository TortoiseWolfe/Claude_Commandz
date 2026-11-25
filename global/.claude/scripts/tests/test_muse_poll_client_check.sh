#!/usr/bin/env bash
# The client-mail checker keeps only real Gmail message IDs (15-16 hex), logs what it drops, and
# muse_poll_record.sh client-seen refuses anything else. A stub gmail_api.py stands in for the Gmail API;
# a fake HOME holds the state. Nothing live is touched.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
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

FAKE_LOG=$TMP/mail-index-calls.log; : >"$FAKE_LOG"
rec() { MUSE_STATE=$ST MUSE_MAIL_INDEX_PY="$HERE/tests/fake_mail_index.py" FAKE_MAIL_INDEX_LOG=$FAKE_LOG bash "$HERE/muse_poll_record.sh" "$@"; }
: >"$ST/hub-inbox.md"
if rec client-seen "1792119952000" --fyi 2>/dev/null; then ok 'client-seen refuses a 13-digit timestamp' refused accepted; fi
ok 'client-seen refuses a 13-digit timestamp' 0 "$(cat "$ST/client-mail-seen.txt" 2>/dev/null | grep -c 1792119952000 || true)"
rec client-seen "1a14c8be4df08299" --fyi && ok 'client-seen takes a 16-hex ID' 1 "$(grep -c 1a14c8be4df08299 "$ST/client-mail-seen.txt")"

# Seen is not handled: a bare tick is refused and writes nothing; a reply, a card or an FYI is stamped into the mail index.
if rec client-seen "1a14c8be4df08298" 2>"$TMP/bare.err"; then ok 'a bare client-seen is refused' refused accepted; fi
ok 'a bare client-seen says what to give' 1 "$(grep -c 'seen is not handled' "$TMP/bare.err")"
ok 'a bare client-seen writes nothing' 0 "$(grep -c 1a14c8be4df08298 "$ST/client-mail-seen.txt")"
ok 'a bare client-seen does not reach the mail index' 0 "$(grep -c 1a14c8be4df08298 "$FAKE_LOG")"
if rec client-seen "1a14c8be4df08298" --reply "no good" 2>/dev/null; then ok 'a malformed --reply is refused' refused accepted; fi
if rec client-seen "1a14c8be4df08298" --card "javascript:alert(1)" 2>/dev/null; then ok 'a malformed --card is refused' refused accepted; fi
ok 'refused ticks write nothing' 0 "$(grep -c 1a14c8be4df08298 "$ST/client-mail-seen.txt")"
rec client-seen "1a14c8be4df08298" --reply "r1234567890abcdef"
ok '--reply stamps the mail index with the draft id' 1 "$(grep -c '^handled 1a14c8be4df08298 --reply r1234567890abcdef$' "$FAKE_LOG")"
ok '--reply ticks the id as seen' 1 "$(grep -c 1a14c8be4df08298 "$ST/client-mail-seen.txt")"
rec client-seen "1a14c8be4df08297" --card 23
ok '--card stamps the mail index with the card' 1 "$(grep -c '^handled 1a14c8be4df08297 --card 23$' "$FAKE_LOG")"
rec client-seen "talk:room_a:55" --fyi
ok 'a Talk id never reaches the mail index' 0 "$(grep -c 'talk:room_a' "$FAKE_LOG")"
ok 'a Talk id is ticked seen' 1 "$(grep -c 'talk:room_a:55' "$ST/client-mail-seen.txt")"
# An id with no index row yet (exit 1): one update, one more try, then ticked anyway; a refusal (exit 2) ticks nothing.
: >"$FAKE_LOG"
FAKE_MAIL_INDEX_RC_FIRST=1 rec client-seen "1a14c8be4df08296" --fyi
ok 'no row yet: handled, then update, then handled again' "handled|update|handled" "$(cut -d' ' -f1 "$FAKE_LOG" | paste -sd'|')"
ok 'no row yet: the id is still ticked' 1 "$(grep -c 1a14c8be4df08296 "$ST/client-mail-seen.txt")"
if FAKE_MAIL_INDEX_RC=2 rec client-seen "1a14c8be4df08295" --fyi 2>/dev/null; then ok 'an index refusal stops the tick' refused accepted; fi
ok 'an index refusal ticks nothing' 0 "$(grep -c 1a14c8be4df08295 "$ST/client-mail-seen.txt")"

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
# The ID is built from the clock: a fixed one aged past the script's 30-day window on 18 Nov 2026 and failed every CI run.
id_exact=$(printf '%016x' $(( (now_ms2 << 20) | 75555 )))
cat >"$TMP/claude" <<MOCK
#!/usr/bin/env bash
echo "\$*" >>"$TMP/stub-args"
echo "MSG $id_exact | orders@shop.example.test | Pat <orders@shop.example.test> | Re: order | Mon, 19 Oct 2026 21:00:00 | INBOX,UNREAD | $id_exact"
MOCK
rm -f "$TMP/stub-args"
out=$(check)
ok 'the ID prints exactly as listed' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_exact | orders@shop.example.test | Pat <orders@shop.example.test> | Re: order | Mon, 19 Oct 2026 21:00:00 \$" || true)"
ok 'the lister gets the subcommand and the sender query (senders, list subject tags)' 1 "$(grep -c '^messages-list (from:(.*shop.example.test.*) OR subject:"\[Advisors\]" OR subject:"\[Devops\]") newer_than:2d -from:me$' "$TMP/stub-args" || true)"
ok 'the lister also gets the co-op list query' 1 "$(grep -c '^messages-list list:list.chattanooga.digital newer_than:2d -from:me$' "$TMP/stub-args" || true)"

# 21 Nov 2026: co-op members who write from a personal address never matched a sender row, so their mail got no line.
# A row naming the personal address matches it exactly; an unlisted address is taken only for a [Advisors] / [Devops]
# subject or when the list query (list:list.chattanooga.digital) returns it; no-reply, DRAFT, SENT, seen and no-label
# lines are still dropped (fail closed).
now_ms3=$(( $(date +%s) * 1000 ))
mk() { printf '%016x' $(( (now_ms3 << 20) | $1 )); }
id_member=$(mk 81111); id_adv=$(mk 82222); id_plain=$(mk 83333); id_dev=$(mk 84444); id_listonly=$(mk 85555)
id_listdraft=$(mk 86666); id_advnoreply=$(mk 87777); id_advnolabels=$(mk 88888); id_advseen=$(mk 89999); id_advsent=$(mk 91111)
printf 'client\trepo_dir\tkind\tgmail_from\nCoop\t/x\tmail\tlist.example.test\nCoop\t/x\tmail\tmember@personal.example.test\n' >"$TMP/senders.tsv"
echo "$id_advseen" >"$TMP/seen.txt"
cat >"$TMP/claude" <<MOCK
#!/usr/bin/env bash
echo "\$*" >>"$TMP/stub-args2"
case "\$2" in
list:*) cat <<OUT
MSG $id_listonly | someone@elsewhere.example.test | Rob <someone@elsewhere.example.test> | Re: Meeting recap | Sat, 21 Nov 2026 09:00:00 | INBOX,UNREAD | $id_listonly
MSG $id_listdraft | someone@elsewhere.example.test | Rob <someone@elsewhere.example.test> | Re: Meeting recap | Sat, 21 Nov 2026 09:00:00 | DRAFT | $id_listdraft
OUT
;;
"(to:"*) : ;;
*) cat <<OUT
MSG $id_member | member@personal.example.test | Sofia <member@personal.example.test> | Plain subject | Sat, 21 Nov 2026 09:00:00 | INBOX,UNREAD | $id_member
MSG $id_adv | sofia@other.example.test | Sofia <sofia@other.example.test> | [Advisors] Re: Input on slogans | Sat, 21 Nov 2026 09:00:00 | INBOX,UNREAD | $id_adv
MSG $id_plain | stranger@other.example.test | Pat <stranger@other.example.test> | Plain subject, unlisted | Sat, 21 Nov 2026 09:00:00 | INBOX,UNREAD | $id_plain
MSG $id_dev | rob@other.example.test | Rob <rob@other.example.test> | Re: [Devops] Backup window | Sat, 21 Nov 2026 09:00:00 | INBOX,UNREAD | $id_dev
MSG $id_advnoreply | no-reply@other.example.test | Bot <no-reply@other.example.test> | [Advisors] bot | Sat, 21 Nov 2026 09:00:00 | INBOX,UNREAD | $id_advnoreply
MSG $id_advnolabels | sofia@other.example.test | Sofia <sofia@other.example.test> | [Advisors] no trailer | Sat, 21 Nov 2026 09:00:00
MSG $id_advseen | sofia@other.example.test | Sofia <sofia@other.example.test> | [Advisors] already absorbed | Sat, 21 Nov 2026 09:00:00 | INBOX,UNREAD | $id_advseen
MSG $id_advsent | sofia@other.example.test | Sofia <sofia@other.example.test> | [Advisors] our reply | Sat, 21 Nov 2026 09:00:00 | SENT | $id_advsent
OUT
;;
esac
MOCK
out=$(check)
ok 'a member on a personal-address row comes out NEW' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_member | member@personal.example.test |")"
ok 'an unlisted sender with an [Advisors] subject comes out NEW' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_adv | sofia@other.example.test |")"
ok 'an unlisted sender with "Re: [Devops]" comes out NEW' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_dev |")"
ok 'a message only the list query returned comes out NEW' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_listonly |")"
ok 'an unlisted sender with a plain subject is still skipped' 0 "$(printf '%s\n' "$out" | grep -c "$id_plain" || true)"
ok 'a list-query DRAFT is dropped' 0 "$(printf '%s\n' "$out" | grep -c "$id_listdraft" || true)"
ok 'a no-reply sender with an [Advisors] subject is skipped' 0 "$(printf '%s\n' "$out" | grep -c "$id_advnoreply" || true)"
ok 'a tagged line with no labels or thread ID is dropped (fail closed)' 0 "$(printf '%s\n' "$out" | grep -c "$id_advnolabels" || true)"
ok 'that drop is logged' 1 "$(grep -c "dropped $id_advnolabels: no thread ID" "$ST/muse-poll.log")"
ok 'an already-absorbed tagged message is not NEW' 0 "$(printf '%s\n' "$out" | grep -c "$id_advseen" || true)"
ok 'our own SENT reply with the tag is not NEW' 0 "$(printf '%s\n' "$out" | grep -c "$id_advsent" || true)"
ok 'the list query went out with the List-Id operator' 1 "$(grep -c '^messages-list list:list.chattanooga.digital newer_than:2d -from:me$' "$TMP/stub-args2" || true)"
: >"$TMP/seen.txt"

# 23 Nov 2026: a member writing from a personal address and copying the co-op slipped past the From-only match. The third query asks
# for mail whose To or Cc holds an address at a co-op domain (domains read from the sender file's bare-domain rows of a co-op client);
# what it returns comes out NEW like any other line, with the same DRAFT / SENT / label / seen drops. Made-up names only.
now_ms4=$(( $(date +%s) * 1000 ))
mk4() { printf '%016x' $(( (now_ms4 << 20) | $1 )); }
id_cc=$(mk4 71111); id_ccdraft=$(mk4 72222); id_ccsent=$(mk4 73333); id_ccseen=$(mk4 74444)
printf 'client\trepo_dir\tkind\tgmail_from\nCoop\t/x\tmail\tlist.example.test\nCoop\t/x\tmail\tmember@personal.example.test\nShop\t/y\tmail\tshop.example.test\n' >"$TMP/senders.tsv"
echo "$id_ccseen" >"$TMP/seen.txt"
rm -f "$TMP/stub-args3"
cat >"$TMP/claude" <<MOCK
#!/usr/bin/env bash
echo "\$*" >>"$TMP/stub-args3"
case "\$2" in
"(to:"*) cat <<OUT
MSG $id_cc | friend@elsewhere.example.test | Alex <friend@elsewhere.example.test> | Quick question for the group | Mon, 23 Nov 2026 09:00:00 | INBOX,UNREAD | $id_cc
MSG $id_ccdraft | friend@elsewhere.example.test | Alex <friend@elsewhere.example.test> | Quick question for the group | Mon, 23 Nov 2026 09:00:00 | DRAFT | $id_ccdraft
MSG $id_ccsent | friend@elsewhere.example.test | Alex <friend@elsewhere.example.test> | Our own reply | Mon, 23 Nov 2026 09:00:00 | SENT | $id_ccsent
MSG $id_ccseen | friend@elsewhere.example.test | Alex <friend@elsewhere.example.test> | Already absorbed | Mon, 23 Nov 2026 09:00:00 | INBOX,UNREAD | $id_ccseen
OUT
;;
*) : ;;
esac
MOCK
out=$(check)
ok 'a Cc-only co-op message comes out NEW' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_cc | friend@elsewhere.example.test |")"
ok 'a to/cc-query DRAFT is dropped' 0 "$(printf '%s\n' "$out" | grep -c "$id_ccdraft" || true)"
ok 'a to/cc-query SENT line is dropped' 0 "$(printf '%s\n' "$out" | grep -c "$id_ccsent" || true)"
ok 'an already-absorbed to/cc message is not NEW' 0 "$(printf '%s\n' "$out" | grep -c "$id_ccseen" || true)"
ok 'the to/cc query names the co-op domain and not the shop' 1 "$(grep -c '^messages-list (to:(list.example.test) OR cc:(list.example.test)) newer_than:2d -from:me$' "$TMP/stub-args3" || true)"
# With no co-op row in the sender file there is no to/cc query at all (nothing to ask for).
printf 'client\trepo_dir\tkind\tgmail_from\nShop\t/y\tmail\tshop.example.test\n' >"$TMP/senders.tsv"
rm -f "$TMP/stub-args3"
out=$(check)
ok 'no co-op row, no to/cc query' 0 "$(grep -c '(to:(' "$TMP/stub-args3" || true)"
: >"$TMP/seen.txt"

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
