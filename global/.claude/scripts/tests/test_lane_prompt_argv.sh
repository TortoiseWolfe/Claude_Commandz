#!/usr/bin/env bash
# Tests that the lane's prompt text stays off claude's command line (28 Oct 2026: lane.sh passed the whole prompt as claude's first argument, the
# prompt held "pgrep -f docker-heavy.sh...", and the lane's own pgrep -f matched and killed its own claude). The first message is now a short fixed
# line naming the prompt file. A dry run prints the command; nothing here touches a live lane.
set -euo pipefail
export AFA_ENGINES_AVAILABLE="*"

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-argv.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; WORK="$HOME_T/repos/proj"; LANES="$HOME_T/.claude/state/lanes"
mkdir -p "$BIN" "$WORK" "$LANES"
printf '#!/usr/bin/env bash\ncase "$1" in has-session) exit 0 ;; esac\nexit 0\n' >"$BIN/tmux"; chmod +x "$BIN/tmux"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
has() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
hasnt() { case "$1" in *"$2"*) fail "$3 (unexpected: $2)" ;; *) ok ;; esac; }
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }

P="$TMP/prompt folder/task.md"; mkdir -p "$(dirname "$P")"
printf 'Run pgrep-trap-canary-word over docker-heavy.sh.*form-stage and report.\nSecond line zebra-quartz-marker.\n' >"$P"

lane --dry-run argv "$WORK" sonnet "$P"
[ "$LAST_RC" = 0 ] || fail "a dry run starts ($LAST_OUT)"; ok
hasnt "$LAST_OUT" "pgrep-trap-canary-word" "the prompt's words are not on the command line"
hasnt "$LAST_OUT" "zebra-quartz-marker" "no later line of the prompt is on the command line either"
hasnt "$LAST_OUT" "form-stage" "nor the docker-heavy pattern from the prompt"
# lane.sh copies the prompt into the lane's hand-off folder and names that copy; the message is %q-quoted twice by the dry run (spaces show as \\\ ).
has "$LAST_OUT" "$LANES/$(date +%F)-argv/prompt.md" "the command names the prompt file's path"
has "$LAST_OUT" 'Read\\\ that\\\ whole\\\ file\\\ now\\\ and\\\ follow\\\ it.' "the fixed message tells the lane to read the file"

# Other Claude start forms (console, headless) hold the same line.
lane --dry-run --console argv-c "$WORK" sonnet "$P"
hasnt "$LAST_OUT" "pgrep-trap-canary-word" "--console keeps the prompt text off the command line"

echo "ok - $checks checks passed"
