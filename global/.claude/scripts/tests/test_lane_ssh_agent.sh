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
[ -z "${SSH_GOOD:-}" ] || [ "${SSH_AUTH_SOCK:-}" = "$SSH_GOOD" ] || exit 1   # SSH_GOOD names the one socket that answers
exit "${SSH_ADD_RC:-0}"
STUB
printf '#!/usr/bin/env bash\nexit 0\n' >"$BIN/sleep"
chmod +x "$BIN/tmux" "$BIN/ssh-add" "$BIN/sleep"
export AFA_ENGINES_AVAILABLE="*"
export SSH_AGENT_SCAN_DIR="$TMP/no-agents"   # an empty folder: the tests never look at the real /tmp
mkdir -p "$SSH_AGENT_SCAN_DIR"

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

# 2b. Claude_Commandz issue 184: a caller with no SSH_AUTH_SOCK (the recovery task) finds an unlocked agent in ssh-*/agent.* itself, skips a locked one,
# and passes it to the new session; a window opened from it runs ssh-add -l with exit 0 and no prompt.
SCAN="$TMP/scan"; mkdir -p "$SCAN/ssh-aaa" "$SCAN/ssh-bbb" "$SCAN/ssh-ccc" "$SCAN/ssh-ddd"; chmod 700 "$SCAN/ssh-aaa" "$SCAN/ssh-bbb" "$SCAN/ssh-ccc"; chmod 777 "$SCAN/ssh-ddd"
python3 -c 'import socket,sys; [socket.socket(socket.AF_UNIX).bind(p) for p in sys.argv[1:]]' "$SCAN/ssh-aaa/agent.1" "$SCAN/ssh-bbb/agent.2" "$SCAN/ssh-ddd/agent.4"
ln -s "$SCAN/ssh-bbb/agent.2" "$SCAN/ssh-ccc/agent.3"   # a symlink to a good socket, and a socket in a world-writable folder: neither is used
: >"$CALLS"
env -u SSH_AUTH_SOCK SSH_AGENT_SCAN_DIR="$SCAN" SSH_GOOD="$SCAN/ssh-bbb/agent.2" TMUX_HAS_SESSION=0 HOME="$HOME_T" TMUX_CALLS="$CALLS" PATH="$BIN:$PATH" bash "$SCRIPT" --claude-because "test" ssh-found "$REPO" haiku "$TMP/prompt.md" >/dev/null 2>&1 || true
assert_contains "$(cat "$CALLS")" "-e SSH_AUTH_SOCK=$SCAN/ssh-bbb/agent.2" 'no SSH_AUTH_SOCK: the unlocked agent in the scan folder goes to the new session'
assert_not_contains "$(cat "$CALLS")" "ssh-aaa" 'the locked agent is not used'
for bad in ssh-ccc/agent.3 ssh-ddd/agent.4; do
  : >"$CALLS"
  env -u SSH_AUTH_SOCK SSH_AGENT_SCAN_DIR="$SCAN" SSH_GOOD="$SCAN/$bad" TMUX_HAS_SESSION=0 HOME="$HOME_T" TMUX_CALLS="$CALLS" PATH="$BIN:$PATH" bash "$SCRIPT" --claude-because "test" ssh-bad "$REPO" haiku "$TMP/prompt.md" >/dev/null 2>&1 || true
  assert_not_contains "$(cat "$CALLS")" 'SSH_AUTH_SOCK' "an agent at $bad (a symlink, or in a folder others can write) is not used"
done
: >"$CALLS"
env -u SSH_AUTH_SOCK SSH_AGENT_SCAN_DIR="$SCAN" SSH_GOOD="$SCAN/nowhere" TMUX_HAS_SESSION=0 HOME="$HOME_T" TMUX_CALLS="$CALLS" PATH="$BIN:$PATH" bash "$SCRIPT" --claude-because "test" ssh-locked "$REPO" haiku "$TMP/prompt.md" >/dev/null 2>&1 || true
assert_not_contains "$(cat "$CALLS")" 'SSH_AUTH_SOCK' 'every agent locked: nothing is passed'
# the window's own ssh-add -l: the new window's shell gets the socket through tmux, so run the stub the way that shell would
( export SSH_AUTH_SOCK="$SCAN/ssh-bbb/agent.2" SSH_GOOD="$SCAN/ssh-bbb/agent.2"; PATH="$BIN:$PATH" ssh-add -l >/dev/null 2>&1 ) && ok || fail 'a window with the found socket gets ssh-add -l exit 0'

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
