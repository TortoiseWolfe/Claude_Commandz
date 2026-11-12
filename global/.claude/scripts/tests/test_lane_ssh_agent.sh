#!/usr/bin/env bash
# A lane window never types its start command into an ssh passphrase prompt (4 Nov 2026: fleet-up.sh made the session from a Windows task with no
# ssh agent, ~/.bashrc ran ssh-add in each new window, and the start command went in as passphrase guesses). lane.sh hands the caller's working agent to
# the session and the tmux server, and when a window still shows a passphrase prompt it cancels it, closes the window and exits 1. A fake tmux, ssh-add
# and sleep stand in for the real ones; nothing here starts a real tmux.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-sshagent.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; REPO="$HOME_T/repos/some-app"; CALLS="$TMP/tmux-calls"
mkdir -p "$BIN" "$HOME_T/repos/hub" "$REPO" "$HOME_T/.claude/state/lanes"
printf 'Say hello.\n' >"$TMP/prompt.md"
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$TMUX_CALLS"
case "$1" in
  has-session) [ "${TMUX_HAS_SESSION:-0}" = 1 ] ;;
  list-windows) exit 0 ;;
  capture-pane) printf '%s\n' "${PANE_TEXT:-user@tower:~/work\$ }" ;;
esac
STUB
cat >"$BIN/ssh-add" <<'STUB'
#!/usr/bin/env bash
exit "${SSH_ADD_RC:-0}"
STUB
printf '#!/usr/bin/env bash\nexit 0\n' >"$BIN/sleep"
chmod +x "$BIN/tmux" "$BIN/ssh-add" "$BIN/sleep"
export AFA_ENGINES_AVAILABLE="*"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
assert_contains() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
assert_not_contains() { case "$1" in *"$2"*) fail "$3 (unexpected: $2; got: $1)" ;; *) ok ;; esac; }
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" TMUX_CALLS="$CALLS" PATH="$BIN:$PATH" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }
line_no() { grep -n -m1 -- "$1" "$CALLS" | cut -d: -f1; }

# 1. A working agent goes to a new session (-e) and, for a new window in a running session, to the server and the session before the window opens.
: >"$CALLS"
SSH_AUTH_SOCK=/tmp/fake-agent.sock SSH_ADD_RC=0 TMUX_HAS_SESSION=0 lane --claude-because "test" ssh-new "$REPO" haiku "$TMP/prompt.md"
assert_contains "$(cat "$CALLS")" '-s lanes -n ssh-new' 'a new session is created'
assert_contains "$(cat "$CALLS")" '-e SSH_AUTH_SOCK=/tmp/fake-agent.sock' 'a new session carries the working agent'
: >"$CALLS"
SSH_AUTH_SOCK=/tmp/fake-agent.sock SSH_ADD_RC=0 TMUX_HAS_SESSION=1 lane --claude-because "test" ssh-old "$REPO" haiku "$TMP/prompt.md"
assert_contains "$(cat "$CALLS")" 'set-environment -g SSH_AUTH_SOCK /tmp/fake-agent.sock' 'the server gets the working agent'
assert_contains "$(cat "$CALLS")" 'set-environment -t lanes SSH_AUTH_SOCK /tmp/fake-agent.sock' 'an existing session gets the working agent'
[ "$(line_no 'set-environment -g')" -lt "$(line_no 'new-window')" ] && ok || fail 'set-environment comes before new-window'

# 2. A dead agent (ssh-add -l fails) or none at all is shared with nobody.
: >"$CALLS"
SSH_AUTH_SOCK=/tmp/dead.sock SSH_ADD_RC=2 TMUX_HAS_SESSION=1 lane --claude-because "test" ssh-dead "$REPO" haiku "$TMP/prompt.md"
assert_not_contains "$(cat "$CALLS")" 'set-environment' 'a failing agent is not shared'
: >"$CALLS"
env -u SSH_AUTH_SOCK SSH_ADD_RC=0 TMUX_HAS_SESSION=0 HOME="$HOME_T" TMUX_CALLS="$CALLS" PATH="$BIN:$PATH" bash "$SCRIPT" --claude-because "test" ssh-none "$REPO" haiku "$TMP/prompt.md" >/dev/null 2>&1 || true
assert_not_contains "$(cat "$CALLS")" 'SSH_AUTH_SOCK' 'no agent: nothing is passed'

# 3. A window at a passphrase prompt is cancelled and closed, and the start command is never typed.
: >"$CALLS"
PANE_TEXT='Enter passphrase for /home/u/.ssh/id_ed25519:' TMUX_HAS_SESSION=1 lane --claude-because "test" ssh-stuck "$REPO" haiku "$TMP/prompt.md"
calls="$(cat "$CALLS")"
[ "$LAST_RC" = 1 ] && ok || fail "a passphrase prompt exits 1 (got $LAST_RC)"
assert_contains "$calls" 'send-keys -t lanes:ssh-stuck C-c' 'the prompt is cancelled with C-c'
assert_contains "$calls" 'kill-window -t lanes:ssh-stuck' 'the window is closed'
assert_not_contains "$calls" 'claude --model' 'the start command is never typed into the prompt'
assert_contains "$LAST_OUT" 'passphrase prompt' 'the reason is printed'

# 4. A dry run prints comment lines only for all of this and starts nothing.
lane --dry-run --claude-because "test" ssh-dry "$REPO" haiku "$TMP/prompt.md"
assert_contains "$LAST_OUT" 'SSH_AUTH_SOCK' 'a dry run says what it would share'

printf 'ok - %d checks passed\n' "$checks"
