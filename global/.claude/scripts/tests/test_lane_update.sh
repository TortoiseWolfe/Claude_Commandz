#!/usr/bin/env bash
# Tests for lane.sh --update and --behind (6 Nov 2026: Claude Code 2.1.319 landed while 10 of 14 running sessions were on 2.1.318).
# Fake lanes are real processes: a copy of bash named by version (versions/2.1.318), started through a symlink called "claude" under a pane
# shell, so /proc/<pid>/exe and comm look like Claude Code's. A fake tmux keeps the window list and plays the lane (it writes handoff.md when
# it is asked). Nothing here touches a real lane; only the fake processes this script started are ended, by exact PID.
set -euo pipefail
export AFA_ENGINES_AVAILABLE="*"
SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-update.XXXXXX")"
BIN="$TMP/bin"; HOME_T="$TMP/home"; REPO="$TMP/repo"; ST="$TMP/fake"; ADD="$TMP/extra"; VER="$TMP/versions"
LANES="$HOME_T/.claude/state/lanes"; INDEX="$LANES/index.tsv"; HAND="$LANES/2026-11-05-up-lane"
SLUG="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$REPO")"; PROJ="$HOME_T/.claude/projects/$SLUG"
mkdir -p "$BIN" "$REPO" "$ST" "$ADD" "$VER" "$HAND" "$PROJ" "$LANES"
cleanup() {  # only processes this test started, by exact PID
  if [ -s "$ST/pids" ]; then while read -r p; do for c in $(pgrep -P "$p" 2>/dev/null); do kill "$c" 2>/dev/null || true; done; kill "$p" 2>/dev/null || true; done <"$ST/pids"; fi
  rm -rf "$TMP"
}
trap cleanup EXIT
trap 'echo "test_lane_update.sh: died at line $LINENO: $BASH_COMMAND" >&2' ERR   # a set -e exit must say where
# Fakes are copies of bash, not sleep: the CI runner's sleep is a multi-call binary that refuses to run under another name (6 Nov 2026).
cp /bin/bash "$VER/2.1.318"; cp /bin/bash "$VER/2.1.319"; cp /bin/bash "$VER/2.1.320"   # 2.1.320 ignores SIGTERM
mkdir -p "$TMP/p318" "$TMP/p319" "$TMP/p320"
ln -s "$VER/2.1.318" "$TMP/p318/claude"; ln -s "$VER/2.1.319" "$TMP/p319/claude"; ln -s "$VER/2.1.320" "$TMP/p320/claude"
printf '#!/usr/bin/env bash\necho "2.1.319 (Claude Code)"\n' >"$BIN/claude"; chmod +x "$BIN/claude"

# spawn.sh <name> <version dir>: a pane shell with a claude child; the shell's pid is recorded for the fake tmux
cat >"$TMP/spawn.sh" <<'SH'
#!/usr/bin/env bash
name="$1"; vdir="$2"; ST="$3"
if [ "$vdir" = p320 ]; then
  bash -c "\"$4/$vdir/claude\" -c 'trap \"\" TERM; while :; do /bin/sleep 1; done' & wait" >/dev/null 2>&1 &
else
  bash -c "\"$4/$vdir/claude\" -c 'while :; do /bin/sleep 1; done' & wait" >/dev/null 2>&1 &
fi
echo $! >"$ST/pid-$name"; echo $! >>"$ST/pids"
for _ in $(seq 1 50); do [ -n "$(pgrep -P "$(cat "$ST/pid-$name")" 2>/dev/null)" ] && break; /bin/sleep 0.1; done
SH
chmod +x "$TMP/spawn.sh"

cat >"$BIN/sleep" <<'STUB'
#!/usr/bin/env bash
exec /bin/sleep 0.05
STUB
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  has-session) exit 0 ;;
  list-windows) cat "$FAKE_ST/windows" ;;
  capture-pane) cat "$FAKE_ST/pane" ;;
  display)
    fmt="${@: -1}"; name="${4#lanes:}"
    case "$fmt" in
      '#{pane_current_command}') printf 'claude\n' ;;
      '#{pane_pid}') cat "$FAKE_ST/pid-$name" 2>/dev/null ;;
      '#{window_id}') printf '@7\n' ;;
    esac ;;
  kill-window) printf '%s\n' "$3" >>"$FAKE_ST/kills.log"; : >"$FAKE_ST/windows" ;;
  new-window)  # new-window -t lanes: -n NAME -c DIR: the restarted lane comes up on the installed version
    printf '%s\n' "$5" >>"$FAKE_ST/windows"; bash "$FAKE_TMP/spawn.sh" "$5" p319 "$FAKE_ST" "$FAKE_TMP" ;;
  send-keys)
    if [ "${4:-}" = -l ]; then printf '%s' "${6:-}" >"$FAKE_ST/pending"
    elif [ "${4:-}" = C-m ] && [ -s "$FAKE_ST/pending" ]; then
      python3 -I - "$FAKE_ST" "$FAKE_PROJ" "$(cat "$FAKE_ST/pending")" <<'PY'
import json, os, sys
st, proj, text = sys.argv[1:4]
open(os.path.join(st, "pending"), "w").close()
open(os.path.join(st, "sent.log"), "a").write(text + "\n")
with open(os.path.join(proj, "old-sess.jsonl"), "a") as f:
    f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
if "restarting you onto it" in text and os.environ.get("FAKE_WRITE_HANDOFF") == "1":
    open(os.environ["FAKE_HANDOFF"], "w").write("# handoff\nnext step: carry on\n")
    if os.environ.get("FAKE_REPORT_AFTER") == "1":
        import time; time.sleep(1.1); os.utime(os.environ["FAKE_REPORT"], None)
PY
    fi ;;
esac
STUB
chmod +x "$BIN/sleep" "$BIN/tmux"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
assert_contains() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
assert_not_contains() { case "$1" in *"$2"*) fail "$3 (unexpected: $2)" ;; *) ok ;; esac; }
lane() {
  set +e
  LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" FAKE_ST="$ST" FAKE_TMP="$TMP" FAKE_PROJ="$PROJ" FAKE_HANDOFF="$HAND/handoff.md" FAKE_REPORT="$HAND/report.md" bash "$SCRIPT" "$@" 2>&1)"
  LAST_RC=$?
  set -e
}
alive() { kill -0 "$1" 2>/dev/null; }
claude_of() { pgrep -P "$(cat "$ST/pid-$1")" | head -1 || true; }
idle_pane() { printf '%s\n' '⏺ Merged.' '✻ Worked for 4m 2s · done 1:05 PM' '❯ ' >"$ST/pane"; }
row() {  # row <name> <billing>
  printf '2026-11-05\t%s\t%s\tsonnet\t%s\t%s\t%s\told-sess\t1700000000\thigh\toff\t120\t-\t-\t%s\n' "$1" "$REPO" "$2" "$HAND/prompt.md" "$HAND/report.md" "$ADD" >>"$INDEX"
}
echo 'do the thing' >"$HAND/prompt.md"; echo 'status' >"$HAND/report.md"; echo '# old handoff' >"$HAND/handoff.md"
touch -d '10 minutes ago' "$HAND/report.md" "$HAND/handoff.md"
: >"$PROJ/old-sess.jsonl"; idle_pane
row up-lane subscription; row cur-lane subscription; row cx-lane codex
printf 'up-lane\ncur-lane\n' >"$ST/windows"
bash "$TMP/spawn.sh" up-lane p318 "$ST" "$TMP"; bash "$TMP/spawn.sh" cur-lane p319 "$ST" "$TMP"
OLDPID="$(claude_of up-lane || true)"; CURPID="$(claude_of cur-lane || true)"
if [ -z "$OLDPID" ] || [ -z "$CURPID" ]; then  # say why a fake lane has no claude child (seen in CI on 6 Nov)
  { echo "diag: TMP=$TMP shell pids: $(cat "$ST"/pid-* 2>/dev/null | tr '\n' ' ')"; ls -l "$TMP/p318/" "$VER" 2>&1; mount | grep -E ' (/tmp|/home)[ /]' || true
    "$TMP/p318/claude" 0.1; echo "direct run rc=$?"; ps -eo pid,ppid,stat,comm,args | tail -25; } >&2 || true
fi
[ -n "$OLDPID" ] && [ -n "$CURPID" ] || fail "the fake lanes did not start"; ok

# --behind names the lane on the older version, with its screen state, and not the current one.
lane --behind
[ "$LAST_RC" = 0 ] || fail "--behind exits 0 ($LAST_OUT)"; ok
[ "$LAST_OUT" = "up-lane 2.1.318 2.1.319 idle" ] || fail "--behind lists exactly the lane behind: $LAST_OUT"; ok

# --update on a lane already current does nothing.
lane --update cur-lane
assert_contains "$LAST_OUT" "already runs 2.1.319" "a current lane is skipped"; [ "$LAST_RC" = 0 ] || fail "skip exits 0"; ok
alive "$CURPID" || fail "the current lane's process was touched"; ok

# Refusals: working, dialog, other engine, no window, no such lane. Nothing is typed and the process lives.
printf '%s\n' '✻ Thinking… (12s · esc to interrupt)' '❯ ' >"$ST/pane"
lane --update up-lane; [ "$LAST_RC" = 1 ] || fail "a working lane is refused"; assert_contains "$LAST_OUT" "is working" "says why"
printf '%s\n' 'Do you want to proceed?' '❯ 1. Yes' '  Esc to cancel' >"$ST/pane"
lane --update up-lane; [ "$LAST_RC" = 1 ] || fail "a lane with a dialog is refused"; assert_contains "$LAST_OUT" "dialog" "says why"
lane --behind; assert_contains "$LAST_OUT" "up-lane 2.1.318 2.1.319 dialog" "--behind shows the dialog"
idle_pane
lane --update cx-lane; [ "$LAST_RC" = 1 ] || fail "a codex lane is refused"; assert_contains "$LAST_OUT" "engine codex" "says why"
lane --update nobody; [ "$LAST_RC" = 1 ] || fail "an unknown lane is refused"
printf 'cur-lane\n' >"$ST/windows"; lane --update up-lane; [ "$LAST_RC" = 1 ] || fail "no window is refused"; assert_contains "$LAST_OUT" "no window named up-lane" "says why"
printf 'up-lane\ncur-lane\n' >"$ST/windows"
[ ! -e "$ST/sent.log" ] || fail "a refusal typed into a lane"; ok
alive "$OLDPID" || fail "a refusal ended the process"; ok

# --dry-run: the plan, the restart built from the row, nothing typed or ended.
lane --dry-run --update up-lane
[ "$LAST_RC" = 0 ] || fail "--dry-run exits 0 ($LAST_OUT)"; ok
assert_contains "$LAST_OUT" "pid $OLDPID runs 2.1.318, installed is 2.1.319" "dry run names the versions"
assert_contains "$LAST_OUT" "run: lane.sh --restart --effort high --no-connectors --add-dir $ADD --recycle-at 120 up-lane $REPO sonnet" "dry run carries the row's settings"
assert_contains "$LAST_OUT" "by exact PID" "dry run says the stop is by PID"
assert_contains "$LAST_OUT" "the restart passes its own --dry-run" "the restart is pre-checked"
[ ! -e "$ST/sent.log" ] && alive "$OLDPID" || fail "--dry-run changed something"; ok

# The own-repo tier rule (agy default) must not stop an update: an update continues the same lane, so the restart reuses the reason last
# logged for it, or the one given before --update. A lane with no logged reason and none given is still refused while it is alive.
mkdir -p "$HOME_T/repos/hub"; printf 'id\trepo_dir\nacme\t~/repos/client-app\n' >"$HOME_T/repos/hub/client-senders.tsv"; printf '# clients\n' >"$HOME_T/repos/hub/client-repos.txt"
TLOG="$LANES/tier-overrides.log"; rm -f "$TLOG"
lane --dry-run --update up-lane
[ "$LAST_RC" = 1 ] || fail "an own-repo lane with no logged reason is refused by the tier rule ($LAST_OUT)"; assert_contains "$LAST_OUT" "REFUSED" "says the restart would be refused"
printf '2026-11-04T11:49:01-05:00\tup-lane\tsonnet\tagy-default\told reason\t%s\n2026-11-05T15:59:13-05:00\tother-lane\tsonnet\tagy-default\tsomeone else\t%s\n2026-11-05T16:00:00-05:00\tup-lane\tsonnet\tagy-default\tneeds a live service \t%s\n' "$REPO" "$REPO" "$REPO" >"$TLOG"
lane --dry-run --update up-lane
[ "$LAST_RC" = 0 ] || fail "an update reuses the lane's last logged reason ($LAST_OUT)"; ok
assert_contains "$LAST_OUT" '--claude-because needs a live service --add-dir' "the newest reason for this lane is passed to the restart, trailing space trimmed"
assert_not_contains "$LAST_OUT" 'someone else' "another lane's reason is not used"
lane --dry-run --claude-because 'agy out until Sun' --update up-lane
assert_contains "$LAST_OUT" '--claude-because agy out until Sun --add-dir' "a reason given on the command line wins over the logged one"
# A new lane (a start, not an update) is still refused without its own reason, even though another lane has a logged one.
lane --dry-run newbie "$REPO" sonnet "$HAND/prompt.md"
[ "$LAST_RC" = 1 ] || fail "a brand-new own-repo lane still needs its own reason ($LAST_OUT)"; assert_contains "$LAST_OUT" '--claude-because' "the refusal names the override"
rm -f "$TLOG" "$HOME_T/repos/hub/client-senders.tsv" "$HOME_T/repos/hub/client-repos.txt"

# A lane that never writes handoff.md is left running.
export LANE_UPDATE_WAIT_SECS=3
lane --update up-lane
[ "$LAST_RC" = 1 ] || fail "no hand-off: refused"; assert_contains "$LAST_OUT" "left running" "says the lane was left running"
alive "$OLDPID" || fail "the process was ended without a hand-off"; ok
[ ! -e "$ST/kills.log" ] || fail "the window was closed without a hand-off"; ok
# The old handoff.md is kept as handoff-prev1.md already by that attempt.
[ -s "$HAND/handoff-prev1.md" ] || fail "the old hand-off was not kept"; ok

# A hand-off older than the report (the lane touched report.md after writing it) does not count.
FAKE_WRITE_HANDOFF=1 FAKE_REPORT_AFTER=1 lane --update up-lane
[ "$LAST_RC" = 1 ] || fail "a hand-off older than report.md is not accepted ($LAST_OUT)"; alive "$OLDPID" || fail "ended on a stale hand-off"; ok
rm -f "$ST/sent.log"; touch -d '10 minutes ago' "$HAND/report.md" "$HAND/handoff.md"

# The full roll: hand-off, exact-PID stop, window closed, restart with the row's settings, new process on the installed version.
BEFORE_ROWS="$(wc -l <"$INDEX")"
T0=$(date +%s)
LANE_UPDATE_WAIT_SECS=20 LANE_UPDATE_TERM_SECS=60 FAKE_WRITE_HANDOFF=1 lane --update up-lane
T1=$(date +%s)
[ "$LAST_RC" = 0 ] || fail "the roll succeeds ($LAST_OUT)"; ok
# SIGTERM alone ends a normal claude: the roll must not sit out the SIGKILL wait.
[ $((T1 - T0)) -lt 30 ] || fail "the roll waited for SIGKILL ($((T1 - T0))s): SIGTERM was not sent"; ok
assert_contains "$LAST_OUT" "updated up-lane from 2.1.318 to 2.1.319" "the roll says what it did"
alive "$OLDPID" && fail "the old claude process is still alive"; ok
alive "$CURPID" || fail "another lane's process was ended"; ok
[ "$(cat "$ST/kills.log")" = "@7" ] || fail "the window was closed by id"; ok
NEWPID="$(claude_of up-lane)"; [ -n "$NEWPID" ] && [ "$NEWPID" != "$OLDPID" ] || fail "no new process"; ok
[ "$(readlink "/proc/$NEWPID/exe" | sed 's|.*/||')" = 2.1.319 ] || fail "the new process is not on 2.1.319"; ok
grep -q 'restarting you onto it' "$ST/sent.log" || fail "the lane was asked for its hand-off"; ok
[ "$(wc -l <"$INDEX")" -gt "$BEFORE_ROWS" ] || fail "no new index row"; ok
last="$(awk -F'\t' '$2=="up-lane"' "$INDEX" | tail -1)"
[ "$(cut -f4 <<<"$last")" = sonnet ] && [ "$(cut -f10 <<<"$last")" = high ] && [ "$(cut -f11 <<<"$last")" = off ] && [ "$(cut -f12 <<<"$last")" = 120 ] && [ "$(cut -f15 <<<"$last")" = "$ADD" ] \
  || fail "the new row keeps model, effort, connectors, recycle line and add-dirs: $last"; ok
[ "$(cut -f7 <<<"$last")" = "$HAND/report.md" ] || fail "the restart reuses the lane's hand-off folder"; ok
note="$(ls "$HAND"/prompt-[0-9]*.md 2>/dev/null | head -1)"; [ -n "$note" ] && grep -q 'updated to 2.1.319' "$note" || fail "the restart note names the update"; ok
grep -q "$HAND/handoff.md" "$note" || fail "the note points at the hand-off"; ok
grep -q "updated" "$LANES/recycle.log" || fail "recycle.log records the update"; ok
[ ! -d "$LANES/.recycle-up-lane.lock" ] || fail "the lock was left behind"; ok

# A claude that ignores SIGTERM is ended by SIGKILL after LANE_UPDATE_TERM_SECS, by exact PID, and only that one.
row stub-lane subscription; printf 'stub-lane\ncur-lane\n' >"$ST/windows"
bash "$TMP/spawn.sh" stub-lane p320 "$ST" "$TMP"; STUBPID="$(claude_of stub-lane)"
# 2.1.320 is "newer" than the installed 2.1.319, which is still a mismatch worth rolling
rm -f "$ST/kills.log"; touch -d '10 minutes ago' "$HAND/report.md"
LANE_UPDATE_WAIT_SECS=20 LANE_UPDATE_TERM_SECS=2 FAKE_WRITE_HANDOFF=1 lane --update stub-lane
[ "$LAST_RC" = 0 ] || fail "a TERM-proof process is still rolled ($LAST_OUT)"; ok
alive "$STUBPID" && fail "the TERM-proof process was not ended"; ok
alive "$CURPID" || fail "SIGKILL reached another lane"; ok

printf 'ok: %s checks\n' "$checks"
