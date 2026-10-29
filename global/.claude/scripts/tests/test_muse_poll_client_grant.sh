#!/usr/bin/env bash
# 29 Oct 2026 hub security call: the unattended client-mail pass reads email, so what it may do is the least it needs. A fake claude
# records the exact flags the poller gives each call (NUL-separated, so the tool list survives intact) and the Gusto variables it was
# started with; a stub client check queues one mail, payment-kind or not. A fake HOME holds the state. Nothing live is touched.
set -euo pipefail
SCRIPT=$(cd -- "$(dirname -- "$0")/.." && pwd)/muse_poll.sh
TMP=$(mktemp -d); trap 'rm -rf -- "$TMP"' EXIT
H=$TMP/home; ST=$H/.claude/state; CALLS=$TMP/calls
mkdir -p "$H/.claude/scripts" "$H/.local/bin" "$H/repos" "$ST" "$CALLS"
cat >"$H/.local/bin/claude" <<MOCK
#!/usr/bin/env bash
n=\$(ls "$CALLS" | wc -l)
printf '%s\0' "\$@" >"$CALLS/\$n.args"
printf 'GUSTO_UNATTENDED=%s\nGUSTO_QUEUED_IDS_FILE=%s\n' "\${GUSTO_UNATTENDED:-}" "\${GUSTO_QUEUED_IDS_FILE:-}" >"$CALLS/\$n.vars"
echo NONE
MOCK
cat >"$H/.claude/scripts/muse_poll_client_check.sh" <<'MOCK'
#!/usr/bin/env bash
echo "NEW 0123456789abcdef | a@x.test | A <a@x.test> | Subject | Date"
[[ -n "${STUB_PAY:-}" && -n "${MUSE_CLIENT_PAYIDS:-}" ]] && echo 0123456789abcdef >>"$MUSE_CLIENT_PAYIDS"
exit 0
MOCK
echo mock >"$H/.claude/scripts/muse_poll.check.prompt"; echo mock >"$H/.claude/scripts/muse_poll.handle.prompt"; echo mock >"$H/.claude/scripts/muse_poll.client-handle.prompt"
chmod +x "$H/.local/bin/claude" "$H/.claude/scripts/muse_poll_client_check.sh"

look() {  # look [VAR=value ...]: a fresh, due look; the calls it made land in $CALLS
  rm -f "$CALLS"/*; rm -f "$ST"/muse-poll-*.stamp; touch "$ST/user-active.stamp"; touch -d '1 minute ago' "$ST/muse-active.stamp"
  touch -d '6 minutes ago' "$ST/muse-poll-last-check.stamp"
  HOME=$H MUSE_POLL_LOCK=$TMP/poll.lock env "$@" bash "$SCRIPT" >/dev/null 2>&1 || true
}
expect() {  # $1 = label; stdin = python over calls_by_model(model) -> list of (argv, vars)
  local code; code=$(cat)
  python3 -I - "$CALLS" "$H" "$1" "$code" <<'PY' || exit 1
import os, sys
calls_dir, home, label, code = sys.argv[1:5]
def calls():
    out = []
    for f in sorted(os.listdir(calls_dir)):
        if f.endswith('.args'):
            argv = open(os.path.join(calls_dir, f), 'rb').read().decode().split('\0')[:-1]
            vars_ = dict(l.split('=', 1) for l in open(os.path.join(calls_dir, f[:-5] + '.vars')).read().splitlines())
            out.append((argv, vars_))
    return out
def flag(argv, name): return argv[argv.index(name) + 1]
def by_model(model): return [c for c in calls() if flag(c[0], '--model') == model]
exec(code)
print('PASS: ' + label)
PY
}

# 1. A mail from a client that is not a payment sender.
look env
expect 'client handler, no payment mail: only client-mail and client-seen, one Read path, no Gusto, no mail reader, no Gusto variables' <<'PY'
(argv, env), = by_model('sonnet')
allow = flag(argv, '--allowedTools').split(',')
rec = home + '/.claude/scripts/muse_poll_record.sh'
assert allow == ['Read(/%s/repos/hub/client-senders.tsv)' % home, 'Bash(%s client-mail:*)' % rec, 'Bash(%s client-seen:*)' % rec], allow
assert 'Read' not in allow and not any(a == 'Bash(%s:*)' % rec for a in allow), 'a bare Read or a bare record.sh:* is back'
assert not any('gusto' in a or 'get_message' in a or '--apply' in a for a in allow), allow
assert env['GUSTO_UNATTENDED'] == '' and env['GUSTO_QUEUED_IDS_FILE'] == '', env
deny = flag(argv, '--disallowedTools')
assert 'Bash(*--apply*)' in deny and 'Bash(*--test-mode*)' in deny, deny
PY

# 2. A payment-kind mail: the mail reader and the dry-run Gusto form are granted, still never --apply, and the script is told its limits.
look env STUB_PAY=1
expect 'client handler, payment mail: get_message and the dry-run Gusto form only, never --apply, limits passed to the script' <<'PY'
(argv, env), = by_model('sonnet')
allow = flag(argv, '--allowedTools').split(',')
gusto = 'Bash(python3 %s/.claude/scripts/gusto_mark_paid.py match --amount:*)' % home
assert gusto in allow and 'mcp__claude_ai_Gmail__get_message' in allow, allow
assert not any('--apply' in a or '--test-mode' in a for a in allow), allow
assert not any(a == 'Bash(%s/.claude/scripts/muse_poll_record.sh:*)' % home for a in allow), allow
assert env['GUSTO_UNATTENDED'] == '1', env
assert env['GUSTO_QUEUED_IDS_FILE'] == home + '/.claude/state/client-payment-ids.txt', env
assert open(env['GUSTO_QUEUED_IDS_FILE']).read().split() == ['0123456789abcdef']
PY

# 3. The next look without a payment empties the list, so an old payment ID cannot be reused.
look env
expect 'the payment ID list is emptied at each look' <<'PY'
assert open(home + '/.claude/state/client-payment-ids.txt').read() == ''
(argv, env), = by_model('sonnet')
assert 'gusto' not in flag(argv, '--allowedTools') and env['GUSTO_UNATTENDED'] == ''
PY

# 4. The Hatch look reads one file, not the disk.
expect 'the Hatch check reads only agent-notes-processed.txt' <<'PY'
(argv, env), = by_model('haiku')
allow = flag(argv, '--allowedTools').split(',')
assert 'Read(/%s/.claude/state/agent-notes-processed.txt)' % home in allow and 'Read' not in allow, allow
PY
