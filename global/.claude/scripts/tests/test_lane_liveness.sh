#!/usr/bin/env bash
# Tests for the lane liveness gate (28 Oct 2026: a lane ran pgrep -f on words from its own prompt and killed its claude process; the pane sat at a
# bare bash prompt for 3 hours while lane.sh --send and --recycle typed two messages into bash). --send and --recycle now type nothing into a pane
# that is not running claude, and lane.sh --pane says what a pane holds (lane_watch.sh uses it). A fake tmux plays the lane; nothing here is live.
set -euo pipefail
export AFA_ENGINES_AVAILABLE="*"

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-liveness.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; REPO="$TMP/repo"; ST="$TMP/fake"
HAND="$HOME_T/.claude/state/lanes/2026-10-29-lv"; INDEX="$HOME_T/.claude/state/lanes/index.tsv"
SLUG="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$REPO")"; PROJ="$HOME_T/.claude/projects/$SLUG"
mkdir -p "$BIN" "$REPO" "$ST" "$HAND" "$PROJ"
echo 'do the thing' >"$HAND/prompt.md"
printf '%s\n' '⏺ Merged.' '✻ Worked for 4m 2s · done 1:05 PM' '❯ ' >"$ST/pane"
echo live-sess >"$ST/cur"

printf '#!/usr/bin/env bash\nexec /bin/sleep 0.02\n' >"$BIN/sleep"
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  has-session) exit 0 ;;
  list-windows) printf "%s\n" lv-lane ;;
  capture-pane) cat "$FAKE_ST/pane" ;;
  display) [ "${5:-}" = '#{pane_current_command}' ] && printf '%s\n' "${FAKE_PANE_CMD-claude}" ;;
  send-keys)
    printf '%s\n' "${4:-}" >>"$FAKE_ST/keys.log"
    if [ "${4:-}" = -l ]; then printf '%s' "${6:-}" >"$FAKE_ST/pending"
    elif [ "${4:-}" = C-m ] && [ -s "$FAKE_ST/pending" ]; then
      python3 -I - "$FAKE_ST" "$FAKE_PROJ" "$(cat "$FAKE_ST/pending")" <<'PY'
import json, os, sys
st, proj, text = sys.argv[1:4]
open(os.path.join(st, "pending"), "w").close()
cur = open(os.path.join(st, "cur")).read().strip()
with open(os.path.join(proj, cur + ".jsonl"), "a") as f:
    f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
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
assert_eq() { [ "$1" = "$2" ] || fail "$3 (expected $2, got $1)"; ok; }
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" FAKE_ST="$ST" FAKE_PROJ="$PROJ" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }
row() {  # row <name> <billing>
  printf '%s\t%s\t%s\tsonnet\t%s\t%s\t%s\tlive-sess\t-\thigh\toff\n' "$(date +%F)" "$1" "$REPO" "$2" "$HAND/prompt.md" "$HAND/report.md" >>"$INDEX"
}
row lv-lane subscription
: >"$PROJ/live-sess.jsonl"
RLOG="$HOME_T/.claude/state/lanes/recycle.log"

# --send to a pane at a bare shell: nothing typed, exit 1, "not sent:" on stderr.
for sh in bash zsh sh ''; do
  rm -f "$ST/keys.log"
  FAKE_PANE_CMD="$sh" lane --send lv-lane 'hello there'
  assert_eq "$LAST_RC" 1 "--send to a pane showing '${sh:-nothing}' exits 1"
  assert_contains "$LAST_OUT" "not sent: lv-lane is not running claude (pane shows ${sh:-nothing})" "the refusal says what the pane shows ('${sh:-nothing}')"
  [ ! -e "$ST/keys.log" ] || fail "keys were typed into a '${sh:-nothing}' pane"; ok
done

# --send to a pane running claude (as claude, node, or a version string) goes through and lands in the lane's transcript.
for c in claude node 2.1.40; do
  rm -f "$ST/keys.log"
  FAKE_PANE_CMD="$c" lane --send lv-lane "hello there from $c"
  assert_eq "$LAST_RC" 0 "--send to a pane showing $c proceeds"
  assert_contains "$(cat "$PROJ/live-sess.jsonl")" "hello there from $c" "the text reached the lane's transcript ($c)"
done

# --recycle on a bash pane: no keys, exit 1, and a FAILED row in recycle.log (tab format: date, name, FAILED, reason).
rm -f "$ST/keys.log" "$RLOG"
FAKE_PANE_CMD=bash lane --recycle lv-lane
assert_eq "$LAST_RC" 1 '--recycle on a bash pane exits 1'
assert_contains "$LAST_OUT" 'not sent: lv-lane is not running claude (pane shows bash)' '--recycle says why on stderr'
[ ! -e "$ST/keys.log" ] || fail 'a recycle typed into a bash pane'; ok
[ "$(awk -F'\t' '{print $2 "|" $3 "|" $4}' "$RLOG")" = 'lv-lane|FAILED|claude not running (pane shows bash)' ] || fail "recycle.log holds the FAILED row, got: $(cat "$RLOG")"; ok
[ ! -d "$HOME_T/.claude/state/lanes/.recycle-lv-lane.lock" ] || fail 'no recycle lock left behind'; ok

# --pane: claude, dead <cmd>, headless.
FAKE_PANE_CMD=claude lane --pane lv-lane; assert_eq "$LAST_OUT" claude '--pane on a claude pane prints claude'
FAKE_PANE_CMD=bash lane --pane lv-lane; assert_eq "$LAST_OUT" 'dead bash' '--pane on a bash pane prints dead bash'
FAKE_PANE_CMD='' lane --pane lv-lane; assert_eq "$LAST_OUT" 'dead nothing' '--pane on an empty pane prints dead nothing'
lane --pane no-such-lane; assert_eq "$LAST_OUT" '?' '--pane on an unknown lane prints ?'
printf '%s\t%s\t%s\tsonnet\tcodex\t%s\t%s\tx\t-\thigh\toff\n' "$(date +%F)" hl-lane "$REPO" "$HAND/prompt.md" "$HAND/report.md" >>"$INDEX"
FAKE_PANE_CMD=bash lane --pane hl-lane; assert_eq "$LAST_OUT" headless '--pane on a headless (codex) lane prints headless'

echo "ok - $checks checks passed"
