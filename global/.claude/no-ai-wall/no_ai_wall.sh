#!/usr/bin/env bash
# Bash side of the no-AI wall: source this file; it reads the private list as no_ai_wall.py (the single reader).
#   ~/.config/afa/no-ai-paths.txt     paths and globs no model session may touch
# A missing list means the wall is off for it (one warning line per run); a list that exists but cannot be read fails closed.
#
#   no_ai_wall_paths_hit <path>...        returns 0 when any path is listed (or the list is unreadable), 1 when none is
#   no_ai_wall_text_hit <file>            returns 0 when the text file names a listed path
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
