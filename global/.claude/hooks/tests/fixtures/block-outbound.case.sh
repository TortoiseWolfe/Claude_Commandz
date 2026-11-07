#!/usr/bin/env bash
# CI stand-in for the tower's private ~/.claude/hooks/block-outbound.sh. Only the part that hands Gmail draft tools to
# draft-recipient-guard.py and refuses the send tools is here, copied from the live hook; the live hook's other rules (mail transfer agents and so on) are not.
# install_scripts.py compares the case block below with the live hook's and refuses to install if they differ (tested in test_install_scripts.py).
set -uo pipefail

payload="$(timeout 2 cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0        # cannot parse -> defer to the deny rules

tool="$(printf '%s' "$payload" | timeout 2 python3 -c '
import json,sys
try: print(json.load(sys.stdin).get("tool_name",""))
except Exception: print("")
' 2>/dev/null || true)"

block() {
  echo "BLOCKED: $1" >&2
  exit 2
}

case "$tool" in
  mcp__claude_ai_Gmail__create_draft|mcp__claude_ai_Gmail__update_draft)
    # Fail closed: a missing or crashing guard denies, unlike the send block above.
    guard="$HOME/.claude/hooks/draft-recipient-guard.py"
    if [ ! -f "$guard" ]; then
      echo "BLOCKED: $tool — $guard is missing; drafts to anyone but Jonathan go through gmail_api.py draft-create" >&2
      exit 2
    fi
    printf '%s' "$payload" | timeout 4 python3 "$guard"
    rc=$?
    [ "$rc" -eq 0 ] || exit 2
    ;;
  mcp__claude_ai_Gmail__send_message|mcp__claude_ai_Gmail__reply|mcp__claude_ai_Gmail__forward)
    block "$tool — outbound email" ;;
esac
exit 0
