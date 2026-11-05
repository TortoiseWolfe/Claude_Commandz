#!/usr/bin/env bash
# Bash side of the no-AI wall: source this file; it reads the same two private lists as no_ai_wall.py (the single reader).
#   ~/.config/afa/no-ai-paths.txt     paths and globs no model session may touch
#   ~/.config/afa/no-ai-senders.tsv   client, repo_dir, kind, gmail_from
# A missing list means the wall is off for it (one warning line per run); a list that exists but cannot be read fails closed.
#
#   no_ai_wall_paths_hit <path>...        returns 0 when any path is listed (or the list is unreadable), 1 when none is
#   no_ai_wall_text_hit <file>            returns 0 when the text file names a listed path
#   no_ai_wall_sender_client <address>    prints the client and returns 0 when the sender is listed
#   no_ai_wall_notify <client> <address> <message-id>
#                                         the no-model notifier for mail from a listed sender: one desktop toast and one
#                                         hub-inbox line "No-AI mail: <client> | <address> | <time>", never a subject or a body.
#                                         A message id is notified once (no-ai-mail-seen.txt).
# A refusal prints one line on stderr and names the list entry by number, never by its text. If the Python reader itself fails
# (anything but a clean "no" or "listed"), the answer is "listed": the wall fails closed.
_no_ai_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
_no_ai_py="$_no_ai_dir/no_ai_wall.py"

_no_ai_run() {  # a clean 0 = none listed; 3 = listed; anything else = the reader broke, so treat it as listed
  local rc=0
  python3 -I "$_no_ai_py" "$@" || rc=$?
  export NO_AI_QUIET=1  # the first call said "list missing" if it was; one warning per run
  case "$rc" in
    0) return 1 ;;
    3) return 0 ;;
    *) echo "no-ai wall: the list reader failed (exit $rc), so this is refused" >&2; return 0 ;;
  esac
}

no_ai_wall_paths_hit() { _no_ai_run check-path "$@"; }
no_ai_wall_text_hit() { _no_ai_run check-text "$1"; }

no_ai_wall_sender_client() {
  local out rc=0
  out=$(python3 -I "$_no_ai_py" check-sender "$1") || rc=$?
  export NO_AI_QUIET=1
  case "$rc" in
    0) return 1 ;;
    3) printf '%s\n' "$out"; return 0 ;;
    *) echo "no-ai wall: the list reader failed (exit $rc), so this sender is held" >&2; printf 'unknown client\n'; return 0 ;;
  esac
}

no_ai_wall_notify() {
  local client=${1:?client} addr=${2:?address} id=${3:?message id}
  local state=${MUSE_STATE:-$HOME/.claude/state} when
  when=$(date '+%-I:%M %p')
  mkdir -p "$state"
  # The record script keeps the one-per-message rule and the board lock; it prints "queued 1" or "queued 0".
  if [[ "$("$_no_ai_dir/muse_poll_record.sh" no-ai-mail "$client" "$addr" "$id" "$when")" == "queued 1" ]]; then
    if [[ -n "${NO_AI_TOAST:-}" ]]; then
      "$NO_AI_TOAST" "No-AI mail" "$client: new mail, left unread" >/dev/null 2>&1 || true
    else
      timeout 20 python3 "$_no_ai_dir/openclaw_tray.py" notify "No-AI mail" "$client: new mail, left unread" >/dev/null 2>&1 || true
    fi
  fi
}
