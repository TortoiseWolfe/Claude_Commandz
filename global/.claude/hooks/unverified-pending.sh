#!/usr/bin/env bash
# unverified-pending — catch "send this draft" / "merge this PR" handed to Jonathan without a
# live check in the same turn.
#
# WHY: a memory rule (feedback_verify_loose_ends_live.md, 2026-10-09) already says never to
# list a draft as "for you to send" from memory. On 2026-10-11 it was broken again: Gail's
# email had gone out at 4:53 PM, and at 5:05 PM a roadmap comment and a reply still told him to
# send it. His words: "gail's is sent, you should know to check that before telling me to do it
# again". A rule that keeps getting broken needs a structural check (see naked-instruction.sh).
#
# At Stop it reads the transcript: the last assistant reply, and the tool calls made since his
# last message. If the reply asks him to send a draft (an r-<digits> id near "send") and no Gmail
# lookup ran this turn, or to merge a PR and no `gh pr` call ran this turn, it adds context so the
# model checks before he has to ask. Context only, never a block.
# Same conventions as naked-instruction.sh: additionalContext nested in hookSpecificOutput,
# no `set -e`, stop_hook_active guard, stdin read with a timeout.
#
# Kill switch: UNVERIFIED_PENDING=off
# Self-test:   python3 ~/.claude/hooks/unverified-pending.py --self-test
[ "${UNVERIFIED_PENDING:-on}" = "off" ] && exit 0
INPUT=$(timeout 2 cat 2>/dev/null)
ACTIVE=$(printf '%s' "$INPUT" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("stop_hook_active") is True)
except Exception: print(False)' 2>/dev/null)
[ "$ACTIVE" = "True" ] && exit 0
TRANSCRIPT=$(printf '%s' "$INPUT" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("transcript_path",""))
except Exception: print("")' 2>/dev/null)
[ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ] && exit 0
HITS=$(python3 "$HOME/.claude/hooks/unverified-pending.py" "$TRANSCRIPT" 2>/dev/null)
[ -z "$HITS" ] && exit 0
HITS="$HITS" python3 -c '
import json, os
print(json.dumps({"hookSpecificOutput": {
  "hookEventName": "Stop",
  "additionalContext":
    "unverified-pending: your reply hands Jonathan a draft to send or a PR to merge, and you did not "
    "check its live state this turn. He has corrected this twice (2026-10-09, 2026-10-11). Check now "
    "(Gmail in:sent to that recipient plus list_drafts; gh pr view), then correct the reply if it is "
    "already done:\n  " + os.environ["HITS"].replace("\n", "\n  "),
}}))'
