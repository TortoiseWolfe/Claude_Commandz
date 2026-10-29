#!/usr/bin/env bash
# Tests for lane.sh --deny (29 Oct 2026: the weekly co-op Deck lane reads cards that members and anyone with board access wrote, so it starts
# without Deck writes, ssh and docker). The option repeats, adds its rules to LANE_TOOLS_OFF in --disallowedTools, and is kept one rule per line in
# $LANES/.deny/<lane folder>.txt (outside the lane's own writable folder) so --resume and --restart reuse it; a bad line there fails closed. A fake HOME and a fake tmux that logs what is typed hold everything.
set -euo pipefail
export AFA_ENGINES_AVAILABLE="*"

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-deny.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; LANES="$HOME_T/.claude/state/lanes"; WORK="$HOME_T/repos/proj"
mkdir -p "$BIN" "$WORK" "$LANES"
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  has-session) exit 0 ;;
  list-windows) [ -n "${FAKE_WINDOWS:-}" ] && printf '%s\n' $FAKE_WINDOWS; exit 0 ;;
  capture-pane) printf 'ready\n' ;;
  send-keys) [ "${5:-}" = Enter ] && printf "%s\n" "${4:-}" >>"$FAKE_KEYS" || true ;;
esac
exit 0
STUB
printf '#!/usr/bin/env bash\nexit 0\n' >"$BIN/sleep"
chmod +x "$BIN/tmux" "$BIN/sleep"
KEYS="$TMP/keys.log"; : >"$KEYS"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" FAKE_KEYS="$KEYS" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }
last_cmd() { tail -n 1 "$KEYS"; }
has() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
hasnt() { case "$1" in *"$2"*) fail "$3 (unexpected: $2)" ;; *) ok ;; esac; }
DAY="$(date +%F)"
printf 'Mission one.\n' >"$TMP/p1.md"

# 1. Two --deny rules: both ride in --disallowedTools after the Artifact tools; the file holds one per line.
lane --deny 'Bash(*deck_write*)' --deny 'Bash(ssh:*)' dk "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] || fail "a start with --deny works ($LAST_OUT)"; ok
c="$(last_cmd)"
has "$c" "--disallowedTools Artifact\\ ArtifactComments\\ ArtifactData\\ Bash\\(\\*deck_write\\*\\)\\ Bash\\(ssh:\\*\\)" "both rules are added to the withheld tools"
[ "$(cat "$LANES/.deny/$DAY-dk.txt")" = "$(printf 'Bash(*deck_write*)\nBash(ssh:*)')" ] || fail "the deny file holds one rule per line"; ok

# 2. No option: only the Artifact tools, and no file.
lane plain "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] || fail "a plain start works ($LAST_OUT)"; ok
has "$(last_cmd)" "--disallowedTools Artifact\\ ArtifactComments\\ ArtifactData " "a plain start withholds only the Artifact tools"
hasnt "$(last_cmd)" "deck_write" "a plain start denies nothing else"
[ ! -e "$LANES/.deny/$DAY-plain.txt" ] || fail "a plain start writes no deny file"; ok

# 3. --resume (no option given) and --restart reuse the file.
# a started lane has no session id until Claude records one, so give dk one in index column 8
awk -F'\t' -v OFS='\t' '$2 == "dk" { $8 = "sess-dk" } { print }' "$LANES/index.tsv" >"$TMP/index.new" && mv "$TMP/index.new" "$LANES/index.tsv"
lane --resume dk
[ "$LAST_RC" = 0 ] || fail "resume works ($LAST_OUT)"; ok
has "$(last_cmd)" 'Bash\(\*deck_write\*\)' "a resume keeps the denied rules"
has "$(last_cmd)" 'Bash\(ssh:\*\)' "a resume keeps every denied rule"
lane --restart dk "$WORK" sonnet "$TMP/p1.md"
has "$(last_cmd)" 'Bash\(\*deck_write\*\)' "a restart keeps the denied rules"
[ ! -e "$LANES/$DAY-dk/deny-tools.txt" ] || fail "no rule file sits in the lane's own folder"; ok
# a tampered rule file fails closed: a line that is not a rule withholds Bash, Write and Edit
awk -F'\t' -v OFS='\t' '$2 == "dk" { $8 = "sess-dk" } { print }' "$LANES/index.tsv" >"$TMP/index.new" && mv "$TMP/index.new" "$LANES/index.tsv"
printf 'Bash(*deck_write*)\nBash(ssh:* ; rm -rf)\n' >"$LANES/.deny/$DAY-dk.txt"
lane --resume dk
has "$LAST_OUT" "not a permission rule" "a bad line in the rule file is reported"
has "$(last_cmd)" 'Bash\ Write\ Edit' "a bad line withholds Bash, Write and Edit"
printf 'Bash(*deck_write*)\nBash(ssh:*)\n' >"$LANES/.deny/$DAY-dk.txt"

# 4. Bad rules and wrong modes are refused.
lane --deny 'Bash(git log *)' bad "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 1 ] && has "$LAST_OUT" "no spaces or commas" "a rule with a space is refused"
lane --deny 'rm -rf' bad "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 1 ] || fail "a bare command is not a rule"; ok
lane --engine codex --deny 'Bash(ssh:*)' bad "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 1 ] && has "$LAST_OUT" "only applies to Claude lanes" "other engines refuse --deny"
lane --deny 'Bash(ssh:*)' --status
[ "$LAST_RC" = 1 ] && has "$LAST_OUT" "only applies when starting or resuming" "--deny with --status is refused"

# 5. A dry run shows the rules and writes no file.
lane --dry-run --deny 'Bash(docker:*)' dry "$WORK" sonnet "$TMP/p1.md"
has "$LAST_OUT" 'Bash\\\(docker:' "the dry run shows the rule"
[ ! -e "$LANES/.deny/$DAY-dry.txt" ] || fail "a dry run writes no deny file"; ok

echo "ok: $checks checks"
