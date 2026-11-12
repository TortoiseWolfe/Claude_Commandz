#!/usr/bin/env bash
# CLAUDE_CODE_SHELL_PREFIX entry point: runs a Bash tool call through the output filter, and every other shell command untouched.
# Use when: CLAUDE_CODE_SHELL_PREFIX points here (Claude Code then calls this as: prefix '<whole command line>', one argument).
#
# Order: 1. the off switch file exists            -> exec bash -c "$1" (anyone can create the file with the Write tool)
#        2. $1 holds "pwd -P >| " (a Bash tool call) -> exec python3 shell_scrub_wrapper.py "$1" (secret masking)
#        3. wrapper file missing                  -> one line on stderr saying output is NOT masked, then run unfiltered
#        4. anything else (hooks, status line, MCP start) -> exec bash -c "$1"; no python start on this path.
# Environment (tests): SHELL_SCRUB_OFF_FILE (off switch path, default ~/.claude/state/shell-scrub-off),
#                      SHELL_SCRUB_WRAPPER (wrapper path, default shell_scrub_wrapper.py beside this script).
off="${SHELL_SCRUB_OFF_FILE:-$HOME/.claude/state/shell-scrub-off}"
if [ -e "$off" ]; then
    exec bash -c "$1"
fi
case "$1" in
*'pwd -P >| '*)
    here="${0%/*}"
    [ "$here" = "$0" ] && here="."
    wrapper="${SHELL_SCRUB_WRAPPER:-$here/shell_scrub_wrapper.py}"
    if [ ! -f "$wrapper" ]; then
        echo "shell-scrub: $wrapper is missing, so this command's output is NOT being masked" >&2
        exec bash -c "$1"
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "shell-scrub: python3 not found, so this command's output is NOT being masked" >&2
        exec bash -c "$1"
    fi
    exec python3 -S "$wrapper" "$1"
    ;;
esac
exec bash -c "$1"
