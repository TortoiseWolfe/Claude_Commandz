#!/usr/bin/env bash
# Issue 251 (6 Nov 2026): the check found a tower note NEW, but the handler searched on its own, never looked for the tower's tag, exited 0
# with "No new notes", and the poller gave up on the note after two runs. Now the handler is handed the check's exact ids, and the give-up line
# says whether the handler crashed or ran and recorded nothing. Fake claude, fake HOME; nothing live is touched.
set -euo pipefail
SCRIPT=$(cd -- "$(dirname -- "$0")/.." && pwd)/muse_poll.sh
TMP=$(mktemp -d); trap 'rm -rf -- "$TMP"' EXIT
H=$TMP/home; ST=$H/.claude/state; PROMPTS=$TMP/prompts; INBOX=$TMP/inbox
mkdir -p "$H/.claude/scripts" "$H/.local/bin" "$H/repos/hub/scripts" "$ST" "$PROMPTS"
: >"$INBOX"
# The check (haiku) answers FAKE_OUT; the handler (sonnet) saves the prompt it was given and exits HANDLER_EXIT.
cat >"$H/.local/bin/claude" <<MOCK
#!/usr/bin/env bash
if [[ "\$*" == *"--model haiku"* ]]; then echo "\${FAKE_OUT:-NONE}"; exit 0; fi
n=\$(ls "$PROMPTS" | wc -l)
printf '%s' "\$2" >"$PROMPTS/\$n"
exit "\${HANDLER_EXIT:-0}"
MOCK
cat >"$H/.claude/scripts/muse_poll_record.sh" <<MOCK
#!/usr/bin/env bash
printf '%s\n' "\$*" >>"$INBOX"
MOCK
cat >"$H/.claude/scripts/muse_poll_client_check.sh" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
echo 'import sys' >"$H/repos/hub/scripts/mail_index.py"
echo mock >"$H/.claude/scripts/muse_poll.check.prompt"; echo 'HANDLE RULES' >"$H/.claude/scripts/muse_poll.handle.prompt"
chmod +x "$H/.local/bin/claude" "$H/.claude/scripts/muse_poll_record.sh" "$H/.claude/scripts/muse_poll_client_check.sh"

run() { HOME=$H MUSE_POLL_LOCK=$TMP/poll.lock "$@" "$SCRIPT" >/dev/null 2>&1 || true; }
fresh() {
  rm -f "$ST"/muse-poll-*.stamp; touch "$ST/user-active.stamp"; touch -d '1 minute ago' "$ST/muse-active.stamp"
  touch -d '10 minutes ago' "$ST/muse-poll-last-check.stamp"
}
ok() { [[ "$2" == "$3" ]] && printf 'PASS: %s\n' "$1" || { printf 'FAIL: %s (expected %s, got %s)\n' "$1" "$2" "$3" >&2; exit 1; }; }
has() { grep -qF -- "$2" "$3" && printf 'PASS: %s\n' "$1" || { printf 'FAIL: %s (no "%s" in %s)\n' "$1" "$2" "$3" >&2; exit 1; }; }

# 1. The handler's prompt keeps the rules file and names every id the check found.
fresh; run env FAKE_OUT='NEW r111111 r-2222222'
has 'handler prompt still carries the handler rules' 'HANDLE RULES' "$PROMPTS/0"
has 'handler prompt names the first id' 'r111111' "$PROMPTS/0"
has 'handler prompt names the second id' 'r-2222222' "$PROMPTS/0"
has 'handler must record every id, even a receipt' 'recorded with muse_poll_record.sh processed' "$PROMPTS/0"

# 2. An id with shell characters never reaches the prompt (the id-shape filter still holds).
rm -f "$PROMPTS"/*; fresh; run env FAKE_OUT='NEW r333333 $(touch_x)'
has 'a good id is passed' 'r333333' "$PROMPTS/0"
if grep -qF 'touch_x' "$PROMPTS/0"; then echo 'FAIL: a bad id reached the prompt' >&2; exit 1; else echo 'PASS: a bad id stays out of the prompt'; fi

# 3. Two runs that record nothing: the give-up line says the handler ran without error (exit 0).
rm -f "$PROMPTS"/* "$ST/muse-poll-failures.tsv"; : >"$INBOX"
fresh; run env FAKE_OUT='NEW r444444'
fresh; run env FAKE_OUT='NEW r444444'
has 'give-up names an exit-0 handler' 'ran without error but recorded nothing' "$INBOX"
has 'give-up is logged with the same words' 'ran without error but recorded nothing' "$ST/muse-poll.log"

# 4. Two runs where the handler crashes: the give-up line carries the exit code instead.
rm -f "$PROMPTS"/* "$ST/muse-poll-failures.tsv"; : >"$INBOX"
fresh; run env FAKE_OUT='NEW r555555' HANDLER_EXIT=7
fresh; run env FAKE_OUT='NEW r555555' HANDLER_EXIT=7
has 'give-up names a crashed handler' '(last: exited 7)' "$INBOX"
echo 'ok - the handler gets the check ids and the give-up line says why'
