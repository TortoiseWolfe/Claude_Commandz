#!/usr/bin/env bash
# SessionStart hook for lane windows (lane.sh adds it to every Claude lane's --settings and sets AFA_LANE_NAME in the lane's environment).
# Claude Code runs it inside the lane's own process, on startup, resume, /clear and compact, with the session id on stdin. It records
# "<session id> TAB <epoch> TAB <tmux pane>" in ~/.claude/state/lanes/sessions/<lane name>, so lane.sh can PROVE which transcript is the
# lane's live one. 23 Oct: a lane started in ~/repos was handed the hub's own session as its successor, because lane.sh guessed from file
# age and ~/repos holds the hub's and a person's sessions too. Only a process that carries AFA_LANE_NAME (a lane started by lane.sh) writes.
name="${AFA_LANE_NAME:-}"
[[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || exit 0
id="$(python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)"
[[ "$id" =~ ^[0-9a-f-]{8,}$ ]] || exit 0
dir="${LANE_SESSIONS_DIR:-$HOME/.claude/state/lanes/sessions}"
mkdir -p "$dir" || exit 0
[ "$(tail -n 1 "$dir/$name" 2>/dev/null | cut -f1)" = "$id" ] || printf '%s\t%s\t%s\n' "$id" "$(date +%s)" "${TMUX_PANE:--}" >>"$dir/$name"
exit 0
