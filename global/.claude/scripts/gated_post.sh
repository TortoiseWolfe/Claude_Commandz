#!/usr/bin/env bash
# Gate client-visible text, and post ONLY on PASS -- in one process, so the gate
# is a blocking condition rather than a suggestion.
#
# Why this exists: the rule "gate before posting" has now been broken twice the
# same way. Writing the check and the post as two sequential shell lines runs the
# post regardless of the verdict, and GitHub mails the notification on the FIRST
# post, so editing afterwards does not unsend it. A rule that depends on
# remembering to chain the commands is not a gate.
#
#   gated_post.sh <recipient> <body-file> -- <command…>
#
# The command runs only if the voice check passes. Everything after `--` is the
# post command, executed unchanged.
set -euo pipefail

RECIPIENT="${1:?usage: gated_post.sh <recipient> <body-file> -- <command...>}"
BODY="${2:?body file required}"
shift 2
[ "${1:-}" = "--" ] || { echo "gated_post.sh: expected -- before the command" >&2; exit 2; }
shift

[ -r "$BODY" ] || { echo "gated_post.sh: cannot read $BODY" >&2; exit 2; }

OUT="$(python3 ~/.claude/scripts/email_voice_check.py --stdin --strict --recipient "$RECIPIENT" < "$BODY" 2>&1)" || true
printf '%s\n' "$OUT"

VERDICT="$(printf '%s\n' "$OUT" | awk -F': ' '/^Verdict:/{print $2; exit}')"
if [ "$VERDICT" != "PASS" ]; then
  echo
  echo "BLOCKED: verdict is '${VERDICT:-unknown}', nothing was posted."
  echo "Fix the body and re-run, or post deliberately by hand if the failure is"
  echo "a house rule that does not apply to this medium -- and say so out loud."
  exit 1
fi

echo
echo "PASS -- posting."
exec "$@"
