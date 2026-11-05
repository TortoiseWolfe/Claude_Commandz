#!/usr/bin/env bash
# The no-AI wall at the mail poller: mail from a listed sender never reaches the model pass (the check script's output).
# It raises one toast and queues one "No-AI mail: <client> | <address> | <time>" inbox line with no subject and no body, once per message.
# A stub stands in for the Gmail API and for the toast; a fake HOME holds the lists and the state. Nothing live is touched.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d); trap 'rm -rf -- "$TMP"' EXIT
H=$TMP/home; ST=$H/.claude/state
mkdir -p "$H/.claude/scripts" "$H/.config/afa" "$ST"
echo NX-01 >"$H/.config/afa/machine"
printf 'client\trepo_dir\tkind\tgmail_from\nExample Brewing\t/x\tmail\tbrewer@example.test\nSecret Co\t/y\tmail\tboss@secret.test\n' >"$TMP/senders.tsv"
printf 'client\trepo_dir\tkind\tgmail_from\nSecret Co\t/y\tmail\tboss@secret.test\nHidden Ltd\t/z\tmail\thidden.test\n' >"$TMP/noai.tsv"
: >"$TMP/seen.txt"; : >"$ST/hub-inbox.md"
now_ms=$(( $(date +%s) * 1000 ))
id_ok=$(printf '%016x' $(( (now_ms << 20) | 11111 )))
id_s1=$(printf '%016x' $(( (now_ms << 20) | 22222 )))
id_s2=$(printf '%016x' $(( (now_ms << 20) | 33333 )))
id_s3=$(printf '%016x' $(( (now_ms << 20) | 44444 )))
cat >"$TMP/gmail" <<MOCK
#!/usr/bin/env bash
echo "\$*" >>"$TMP/gmail-queries"
cat <<OUT
MSG $id_ok | brewer@example.test | Pat <brewer@example.test> | Re: Tap list | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_ok
MSG $id_s1 | boss@secret.test | Boss <boss@secret.test> | Merger plans SUBJECTMARK | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_s1
MSG $id_s2 | accounts@hidden.test | Acct <accounts@hidden.test> | Invoice SUBJECTMARK | Sat, 17 Oct 2026 21:00:00 | INBOX,UNREAD | $id_s2
MSG $id_s3 | nobody@hidden.test | Me <nobody@hidden.test> | Our reply SUBJECTMARK | Sat, 17 Oct 2026 21:00:00 | SENT | $id_s3
OUT
MOCK
cat >"$TMP/toast" <<MOCK
#!/usr/bin/env bash
echo "\$*" >>"$TMP/toasts"
MOCK
chmod +x "$TMP/gmail" "$TMP/toast"

check() { HOME=$H MUSE_STATE=$ST NO_AI_SENDERS_FILE=$TMP/noai.tsv NO_AI_PATHS_FILE=$TMP/nopaths.txt NO_AI_TOAST=$TMP/toast \
  MUSE_CLIENT_TSV=$TMP/senders.tsv MUSE_CLIENT_SEEN=$TMP/seen.txt MUSE_CLIENT_INBOX=$ST/hub-inbox.md MUSE_CLIENT_GMAIL=$TMP/gmail \
  bash "$HERE/muse_poll_client_check.sh" "$@"; }
ok() { [[ "$2" == "$3" ]] && printf 'PASS: %s\n' "$1" || { printf 'FAIL: %s (expected %s, got %s)\n' "$1" "$2" "$3" >&2; exit 1; }; }

out=$(check)
ok 'the ordinary client message still comes out NEW' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_ok |")"
ok 'no listed sender is printed to the model pass' 0 "$(printf '%s\n' "$out" | grep -c -e boss@secret.test -e hidden.test -e SUBJECTMARK -e "$id_s1" -e "$id_s2" || true)"
ok 'the Gmail query includes the listed addresses so their mail is seen and held' 1 "$(grep -c 'hidden.test' "$TMP/gmail-queries")"
ok 'one inbox line for the exact-address sender' 1 "$(grep -c '| No-AI mail: Secret Co | boss@secret.test | [0-9]\{1,2\}:[0-9][0-9] [AP]M$' "$ST/hub-inbox.md")"
ok 'one inbox line for the domain sender' 1 "$(grep -c '| No-AI mail: Hidden Ltd | accounts@hidden.test | ' "$ST/hub-inbox.md")"
ok 'the inbox lines carry no subject' 0 "$(grep -c SUBJECTMARK "$ST/hub-inbox.md" || true)"
ok 'our own SENT message from a listed address raises nothing' 2 "$(grep -c 'No-AI mail:' "$ST/hub-inbox.md")"
ok 'two toasts' 2 "$(wc -l <"$TMP/toasts")"
ok 'a toast names no subject' 0 "$(grep -c SUBJECTMARK "$TMP/toasts" || true)"
ok 'listed mail is not marked absorbed by the client path' 0 "$(grep -c -e "$id_s1" -e "$id_s2" "$TMP/seen.txt" || true)"

out=$(check)
ok 'a second run adds no duplicate line' 2 "$(grep -c 'No-AI mail:' "$ST/hub-inbox.md")"
ok 'a second run adds no duplicate toast' 2 "$(wc -l <"$TMP/toasts")"

# --dry-run: counts only, nothing written, nothing toasted.
: >"$ST/hub-inbox.md"; rm -f "$ST/no-ai-mail-seen.txt" "$TMP/toasts"
out=$(check --dry-run)
ok 'dry run prints counts only' "dry run (counts only): 2 message(s) from no-AI senders held and not shown, 1 other new" "$out"
ok 'dry run queues nothing' 0 "$(grep -c . "$ST/hub-inbox.md" || true)"
ok 'dry run raises no toast' 0 "$([[ -e $TMP/toasts ]] && wc -l <"$TMP/toasts" || echo 0)"

# A listed sender who is not in client-senders.tsv at all is still held.
printf 'client\trepo_dir\tkind\tgmail_from\nExample Brewing\t/x\tmail\tbrewer@example.test\n' >"$TMP/senders.tsv"
out=$(check)
ok 'a listed sender outside client-senders.tsv is held' 2 "$(grep -c 'No-AI mail:' "$ST/hub-inbox.md")"

# A list that exists but cannot be read holds all client mail (fail closed) and says why on stderr.
printf 'client\trepo_dir\tkind\tgmail_from\nBroken row with no sender column\n' >"$TMP/noai.tsv"
set +e; out=$(check 2>"$TMP/err"); rc=$?; set -e
ok 'an unreadable list exits 1' 1 "$rc"
ok 'an unreadable list prints nothing to the model pass' 0 "${#out}"
ok 'an unreadable list says so' 1 "$(grep -c 'sender list is unreadable' "$TMP/err")"

# No list at all: the wall is off and the poller behaves as before.
rm -f "$TMP/noai.tsv"
out=$(check)
ok 'with no list the ordinary message prints NEW' 1 "$(printf '%s\n' "$out" | grep -c "^NEW $id_ok |")"
echo "all passed"
