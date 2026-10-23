#!/usr/bin/env bash
# 22 Oct: a [CC>MUSE] reply copies the original note's topic verbatim ("[CC>MUSE] Re: <exact original topic>", outcome in the body,
# "Picked up: <full original subject>" lines when one reply covers several notes). The rule lives in the unattended poller's handle
# prompt and in the agent-notes skill; this holds both to it so a prompt edit cannot quietly drop it.
set -uo pipefail
D=$(cd -- "$(dirname -- "$0")/.." && pwd)
SKILL=$D/../skills/agent-notes/SKILL.md
fails=0
need() {  # <file> <fixed text> <what>
  if grep -qF -- "$2" "$1"; then printf 'PASS: %s\n' "$3"; else printf 'FAIL: %s (%s lacks: %s)\n' "$3" "$(basename "$1")" "$2" >&2; fails=$((fails+1)); fi
}
refuse() {  # <file> <fixed text> <what>
  if grep -qF -- "$2" "$1"; then printf 'FAIL: %s (%s still has: %s)\n' "$3" "$(basename "$1")" "$2" >&2; fails=$((fails+1)); else printf 'PASS: %s\n' "$3"; fi
}
H=$D/muse_poll.handle.prompt
need "$H" '"[CC>MUSE] Re: " followed by the exact text of the original subject' 'handle prompt: reply subject copies the original verbatim'
need "$H" 'The outcome goes in the body, never in the subject' 'handle prompt: outcome stays out of the subject'
need "$H" 'Picked up: <the full original subject, tag included>' 'handle prompt: multi-note replies open with Picked up lines'
need "$H" '[NX-01>NCC-74656] Re: <exact original topic after the tag>' 'handle prompt: tower replies follow the same rule'
need "$H" 'subject "[CC>MUSE] Re: <exact original topic>"' 'handle prompt: the answer case names the Re: subject'
refuse "$H" 'subject "[CC>MUSE] <topic>", plain text' 'handle prompt: the old free-form reply subject is gone'
need "$SKILL" '`[CC>MUSE] Re: <exact original topic after the tag>`' 'skill: reply subject copies the original verbatim'
need "$SKILL" '`Picked up: <full original subject>`' 'skill: multi-note replies open with Picked up lines'
need "$SKILL" 'outcome in the body and never in the subject' 'skill: outcome stays out of the subject'
[ "$fails" = 0 ] && echo "ok - reply-subject rule held in both places" || { echo "$fails failed" >&2; exit 1; }
