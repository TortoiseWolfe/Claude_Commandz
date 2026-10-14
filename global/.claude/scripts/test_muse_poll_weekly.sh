#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
SCRIPT=$SCRIPT_DIR/muse_poll.sh
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
CONFIG=$TMP/weekly.tsv
INBOX=$TMP/inbox.md
HOME_TEST=$TMP/home
mkdir -p "$HOME_TEST/.claude/scripts" "$HOME_TEST/.local/bin" "$HOME_TEST/repos" "$HOME_TEST/.claude/state"
printf '2\t17:00\tWeekly Notes {date}\tnotes\n' >"$CONFIG"
printf 'keep this line\n' >"$INBOX"

expect_eq() {
  local name=$1 expected=$2 actual=$3
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL: %s\nExpected: <%s>\nActual:   <%s>\n' "$name" "$expected" "$actual" >&2
    exit 1
  fi
  printf 'PASS: %s\n' "$name"
}

expected='- [ ] 2026-10-13 17:20 | Weekly transcript: fetch "Weekly Notes Oct 13" from YouTube | for the ~/repos director: run lane notes-transcript-2026-10-13 | weekly-notes-2026-10-13'
actual=$(HOME="$HOME_TEST" WEEKLY_TRANSCRIPTS_TSV="$CONFIG" MUSE_INBOX="$INBOX" \
  "$SCRIPT" --weekly-dry-run '2026-10-13' '17:20')
expect_eq 'due Tuesday after the configured time' "$expected" "$actual"
expect_eq 'dry run leaves the inbox untouched' 'keep this line' "$(cat "$INBOX")"

actual=$(HOME="$HOME_TEST" WEEKLY_TRANSCRIPTS_TSV="$CONFIG" MUSE_INBOX="$INBOX" \
  "$SCRIPT" --weekly-dry-run '2026-10-13' '16:59')
expect_eq 'nothing before the configured time' '' "$actual"

actual=$(HOME="$HOME_TEST" WEEKLY_TRANSCRIPTS_TSV="$CONFIG" MUSE_INBOX="$INBOX" \
  "$SCRIPT" --weekly-dry-run '2026-10-14' '17:20')
expect_eq 'nothing on another weekday' '' "$actual"

actual=$(HOME="$HOME_TEST" WEEKLY_TRANSCRIPTS_TSV="$TMP/missing.tsv" MUSE_INBOX="$INBOX" \
  "$SCRIPT" --weekly-dry-run '2026-10-13' '17:20')
expect_eq 'missing config is silent' '' "$actual"

cat >"$HOME_TEST/.local/bin/claude" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
cat >"$HOME_TEST/.claude/scripts/muse_poll.check.prompt" <<'MOCK'
mock
MOCK
cat >"$HOME_TEST/.claude/scripts/muse_poll_client_check.sh" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
chmod +x "$HOME_TEST/.local/bin/claude" "$HOME_TEST/.claude/scripts/muse_poll_client_check.sh"
today_weekday=$(date +%u)
printf '%s\t00:00\tWeekly Notes {date}\tnotes\n' "$today_weekday" >"$CONFIG"
: >"$INBOX"
touch "$HOME_TEST/.claude/state/user-active.stamp"
HOME="$HOME_TEST" WEEKLY_TRANSCRIPTS_TSV="$CONFIG" MUSE_INBOX="$INBOX" "$SCRIPT"
touch -d '2 minutes ago' "$HOME_TEST/.claude/state/muse-poll-last-check.stamp"
HOME="$HOME_TEST" WEEKLY_TRANSCRIPTS_TSV="$CONFIG" MUSE_INBOX="$INBOX" "$SCRIPT"
queued=$(grep -Fc "weekly-notes-$(date +%F)" "$INBOX" || true)
expect_eq 'second poll does not duplicate the queued slug/day' '1' "$queued"
logged=$(grep -Fc 'queued weekly transcript' "$HOME_TEST/.claude/state/muse-poll.log" || true)
expect_eq 'queue event is logged once' '1' "$logged"
