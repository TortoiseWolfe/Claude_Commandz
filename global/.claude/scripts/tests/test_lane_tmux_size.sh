#!/usr/bin/env bash
# A lane window's session is created at a fixed size and keeps it (28 Oct 2026, fleet review finding 7): tmux new-session gets -x 200 -y 50,
# and the session's window-size is manual with default-size 200x50, so a detached window never falls back to 80x24. An existing session is
# never touched: lane.sh adds a window to it and sets no option. A fake tmux logs every call; nothing here starts a real tmux.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-tmuxsize.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; REPO="$HOME_T/repos/some-app"; CALLS="$TMP/tmux-calls"
mkdir -p "$BIN" "$HOME_T/repos/hub" "$REPO" "$HOME_T/.claude/state/lanes"
printf 'Say hello.\n' >"$TMP/prompt.md"
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$TMUX_CALLS"
case "$1" in has-session) [ "${TMUX_HAS_SESSION:-0}" = 1 ] ;; list-windows) exit 0 ;; capture-pane) printf 'ready\n' ;; esac
STUB
chmod +x "$BIN/tmux"
export AFA_ENGINES_AVAILABLE="*"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
assert_contains() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
assert_not_contains() { case "$1" in *"$2"*) fail "$3 (unexpected: $2; got: $1)" ;; *) ok ;; esac; }
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" TMUX_CALLS="$CALLS" PATH="$BIN:$PATH" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }

# 1. A dry run shows the sized session and the two options, ahead of the new-window line.
lane --dry-run --claude-because "test" size-dry "$REPO" haiku "$TMP/prompt.md"
assert_contains "$LAST_OUT" 'new-session -d -x 200 -y 50 -s lanes' 'a dry run creates the session at 200x50'
assert_contains "$LAST_OUT" 'set-option -t lanes window-size manual' 'a dry run sets window-size manual'
assert_contains "$LAST_OUT" 'set-option -t lanes default-size 200x50' 'a dry run sets default-size'

# 2. A real start with no session creates it sized.
: >"$CALLS"
TMUX_HAS_SESSION=0 lane --claude-because "test" size-new "$REPO" haiku "$TMP/prompt.md"
calls="$(cat "$CALLS")"
assert_contains "$calls" 'new-session -d -x 200 -y 50 -s lanes -n size-new' 'a new session is created at 200x50'
assert_contains "$calls" 'set-option -t lanes window-size manual' 'a new session gets window-size manual'
assert_contains "$calls" 'set-option -t lanes default-size 200x50' 'a new session gets default-size'

# 3. A start into an existing session adds a window and changes no option, so live windows are never resized.
: >"$CALLS"
TMUX_HAS_SESSION=1 lane --claude-because "test" size-old "$REPO" haiku "$TMP/prompt.md"
calls="$(cat "$CALLS")"
assert_contains "$calls" 'new-window -t lanes: -n size-old' 'an existing session gets a new window'
assert_not_contains "$calls" 'new-session' 'an existing session is not recreated'
assert_not_contains "$calls" 'set-option' 'an existing session keeps its options'
assert_not_contains "$calls" 'resize' 'no window is resized'

printf 'ok - %d checks passed\n' "$checks"
