#!/usr/bin/env bash
# SessionEnd: scrub real env-file secret values out of the session that just ended: its transcript,
# its subagent transcripts, its saved tool results and its file-history snapshots. Anything a session printed by mistake stays
# readable in ~/.claude/projects until scrubbed, and that store is not in any git repo, so no push
# check ever sees it. Prints key names and counts only (scrub_known_secrets.py never prints values).
INPUT=$(timeout 1 cat 2>/dev/null || true)
read -r TP SID < <(printf '%s' "$INPUT" | python3 -c 'import json,sys
try: d=json.load(sys.stdin); print(d.get("transcript_path","") or "-", d.get("session_id","") or "-")
except Exception: print("- -")' 2>/dev/null)
[ -n "$TP" ] && [ "$TP" != - ] && [ -f "$TP" ] || exit 0
SID=${SID//[^A-Za-z0-9-]/}
PATHS=("$TP")
[ -n "$SID" ] && [ -d "$(dirname "$TP")/$SID" ] && PATHS+=("$(dirname "$TP")/$SID")
# Claude Code keeps a full copy of every file a session edits, env files included.
[ -n "$SID" ] && [ -d "$HOME/.claude/file-history/$SID" ] && PATHS+=("$HOME/.claude/file-history/$SID")
LOG="$HOME/.claude/state/scrub-session-log.log"
{ echo "$(date '+%F %T') session ${SID:-?}"
  timeout 60 python3 -I "$HOME/.claude/scripts/scrub_known_secrets.py" --min-age 0 "${PATHS[@]}" 2>&1 | sed 's/^/  /'
} >>"$LOG"
exit 0
