#!/usr/bin/env bash
# The client-mail checker hands a Wordfence scan-findings alert from an alerts-kind sender to alert_issues.py:
# a finished run absorbs the ID (no NEW line) and logs the script's lines; an unparsed or failed run still prints
# the NEW line so the handler sees it; other alerts (admin login) and other senders' mail are untouched.
# A stub stands in for alert_issues.py and for gmail_api.py; a fake HOME holds the state. Nothing live is touched.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d); trap 'rm -rf -- "$TMP"' EXIT
H=$TMP/home; ST=$H/.claude/state
mkdir -p "$H/.config/afa" "$ST"
echo NX-01 >"$H/.config/afa/machine"
printf 'client\trepo_dir\tkind\tgmail_from\nSite\t/x\talerts\tfulfillment@site.example.test\nSite\t/x\tmail\tsite.example.test\n' >"$TMP/senders.tsv"
: >"$TMP/seen.txt"; : >"$TMP/inbox.md"
now_ms=$(( $(date +%s) * 1000 ))
id_scan=$(printf '%016x' $(( (now_ms << 20) | 11111 )))
id_scan2=$(printf '%016x' $(( (now_ms << 20) | 22222 )))
id_login=$(printf '%016x' $(( (now_ms << 20) | 33333 )))
id_mailkind=$(printf '%016x' $(( (now_ms << 20) | 44444 )))
id_other=$(printf '%016x' $(( (now_ms << 20) | 55555 )))
cat >"$TMP/gmail" <<MOCK
#!/usr/bin/env bash
cat <<OUT
MSG $id_scan | fulfillment@site.example.test | Site <fulfillment@site.example.test> | [Wordfence Alert] Problems found on site.example.test | Wed, 04 Nov 2026 22:01:17 | INBOX,UNREAD | $id_scan
MSG $id_scan2 | fulfillment@site.example.test | Site <fulfillment@site.example.test> | [Wordfence Alert] Problems found on site.example.test | Wed, 04 Nov 2026 22:02:17 | INBOX,UNREAD | $id_scan2
MSG $id_login | fulfillment@site.example.test | Site <fulfillment@site.example.test> | [Wordfence Alert] site.example.test Admin Login | Wed, 04 Nov 2026 22:03:17 | INBOX,UNREAD | $id_login
MSG $id_mailkind | owner@site.example.test | Pat <owner@site.example.test> | Problems found on the invoice | Wed, 04 Nov 2026 22:04:17 | INBOX,UNREAD | $id_mailkind
OUT
MOCK
chmod +x "$TMP/gmail"
# The stub: $id_scan finishes, $id_scan2 is an unknown format (exit 2); every call is recorded.
cat >"$TMP/alerter" <<MOCK
#!/usr/bin/env bash
echo "\$1 tsv=\$ALERT_ISSUES_TSV" >>"$TMP/alerter.calls"
if [[ "\$1" == "$id_scan2" ]]; then echo "UNPARSED \$1"; exit 2; fi
echo "litespeed-cache@7.9.3: commented https://github.com/o/r/issues/1"
MOCK
chmod +x "$TMP/alerter"
check() { HOME=$H MUSE_CLIENT_TSV=$TMP/senders.tsv MUSE_CLIENT_SEEN=$TMP/seen.txt MUSE_CLIENT_INBOX=$TMP/inbox.md \
  MUSE_CLIENT_GMAIL=$TMP/gmail MUSE_CLIENT_ALERT_ISSUES=$TMP/alerter bash "$HERE/muse_poll_client_check.sh"; }
ok() { [[ "$2" == "$3" ]] && printf 'PASS: %s\n' "$1" || { printf 'FAIL: %s (expected %s, got %s)\n' "$1" "$2" "$3" >&2; exit 1; }; }

out=$(check)
ok 'a finished scan alert prints no NEW line' 0 "$(printf '%s\n' "$out" | grep -c "$id_scan |")"
ok 'a finished scan alert is absorbed' 1 "$(grep -cxF "$id_scan" "$TMP/seen.txt")"
ok 'the script saw the client senders file' 1 "$(grep -c "^$id_scan tsv=$TMP/senders.tsv" "$TMP/alerter.calls")"
ok 'the script lines reach the log' 1 "$(grep -c "alert_issues $id_scan: litespeed-cache@7.9.3: commented" "$ST/muse-poll.log")"
ok 'an unparsed alert still prints NEW' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_scan2 |")"
ok 'an unparsed alert is not absorbed' 0 "$(grep -cxF "$id_scan2" "$TMP/seen.txt")"
ok 'the unparsed exit is logged' 1 "$(grep -c "alert_issues $id_scan2 exited 2" "$ST/muse-poll.log")"
ok 'an admin-login alert never reaches the script' 0 "$(grep -c "$id_login" "$TMP/alerter.calls")"
ok 'an admin-login alert still prints NEW' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_login |")"
ok 'a mail-kind sender never reaches the script, whatever the subject' 0 "$(grep -c "$id_mailkind" "$TMP/alerter.calls")"
ok 'a mail-kind sender still prints NEW' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_mailkind |")"

# the second pass: the absorbed one stays quiet and is not run again
: >"$TMP/alerter.calls"
out=$(check)
ok 'a second pass does not run the absorbed alert again' 0 "$(grep -c "^$id_scan " "$TMP/alerter.calls")"
ok 'a second pass still offers the unparsed one' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_scan2 |")"

# With no alert_issues.py at all (an older install), the scan alert prints NEW as it always did.
: >"$TMP/seen.txt"
out=$(HOME=$H MUSE_CLIENT_TSV=$TMP/senders.tsv MUSE_CLIENT_SEEN=$TMP/seen.txt MUSE_CLIENT_INBOX=$TMP/inbox.md MUSE_CLIENT_GMAIL=$TMP/gmail \
  bash "$HERE/muse_poll_client_check.sh")
ok 'without the script the scan alert prints NEW' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_scan |")"
