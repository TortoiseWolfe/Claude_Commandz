#!/usr/bin/env bash
# Tests for lane.sh restarting a lane under a name it already has (28 Oct 2026: a same-day restart overwrote bandwagon-comic's
# prompt.md and reset its recycle line from 120K to 100K; a next-day restart of bandwagon-live made a second folder).
# Now prompt.md is write-once, a different prompt for a folder that has one is stored as prompt-<HHMM>.md, --restart reuses the previous
# row's folder and recycle line (a reused name WITHOUT it gets a fresh dated folder), and --recycle-at always wins. A fake HOME and a fake tmux hold everything: nothing here touches a live lane.
set -euo pipefail
# The CI machine has no claude, agy or codex program; the engine check (test_lane_engine_check.sh) is tested on its own.
export AFA_ENGINES_AVAILABLE="*"

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-restart.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; LANES="$HOME_T/.claude/state/lanes"; WORK="$HOME_T/repos/proj"
mkdir -p "$BIN" "$WORK" "$LANES"
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in has-session) exit 0 ;; list-windows) exit 0 ;; capture-pane) printf 'ready\n' ;; esac
STUB
printf '#!/usr/bin/env bash\nexit 0\n' >"$BIN/sleep"
chmod +x "$BIN/tmux" "$BIN/sleep"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }
rk_of() { awk -F'\t' -v n="$1" '$2==n{r=$12} END{print r}' "$LANES/index.tsv"; }

printf 'Original mission: build the thing.\n' >"$TMP/orig.md"
printf 'Restart: the process died, carry on.\n' >"$TMP/restart.md"

# 1. First start: folder is date-name, prompt.md is a copy, the index row names it.
lane --recycle-at 120 comic "$WORK" sonnet "$TMP/orig.md"
[ "$LAST_RC" = 0 ] || fail "first start works ($LAST_OUT)"; ok
DIR1="$LANES/$(date +%F)-comic"
cmp -s "$DIR1/prompt.md" "$TMP/orig.md" || fail "prompt.md is the original prompt"; ok
[ "$(rk_of comic)" = 120 ] || fail "the first row keeps --recycle-at 120, got $(rk_of comic)"; ok

# 2. Restart on the same day with a different prompt: prompt.md untouched, note stored, line carried over.
echo 'handoff text' >"$DIR1/handoff.md"
lane comic "$WORK" sonnet "$TMP/restart.md"
[ "$LAST_RC" = 0 ] || fail "restart works ($LAST_OUT)"; ok
cmp -s "$DIR1/prompt.md" "$TMP/orig.md" || fail "a restart does not overwrite prompt.md"; ok
note="$(ls "$DIR1"/prompt-[0-9]*.md 2>/dev/null | head -1)"
[ -n "$note" ] || fail "the restart prompt is stored as prompt-<HHMM>.md"; ok
grep -q 'Restart: the process died' "$note" || fail "the stored note holds the new prompt"; ok
head -n1 "$note" | grep -q 'was restarted' || fail "the note starts with the restart line"; ok
grep -q "$DIR1/prompt.md" "$note" || fail "the restart line names the original mission"; ok
[ "$(rk_of comic)" = 120 ] || fail "a restart keeps the previous recycle line (120), got $(rk_of comic)"; ok
[ "$(awk -F'\t' '$2=="comic"{print $6}' "$LANES/index.tsv" | tail -1)" = "$DIR1/prompt.md" ] || fail "the index row still names prompt.md"; ok
[ "$(awk -F'\t' '$2=="comic"{print $7}' "$LANES/index.tsv" | tail -1)" = "$DIR1/report.md" ] || fail "the index row names the same report"; ok

# 3. --recycle-at on a restart wins.
lane --recycle-at 90 comic "$WORK" sonnet "$TMP/restart.md"
[ "$(rk_of comic)" = 90 ] || fail "--recycle-at on a restart overrides the old line, got $(rk_of comic)"; ok

# 4. A name reused on a later day WITHOUT --restart is new work: a fresh dated folder, its own prompt.md, no restart header, no inherited line.
OLD="$LANES/2026-01-02-live"; mkdir -p "$OLD"; cp "$TMP/orig.md" "$OLD/prompt.md"; echo 'old report' >"$OLD/report.md"
printf '2026-01-02\tlive\t%s\tsonnet\tsubscription\t%s\t%s\tid\t1700000000\thigh\toff\t125\t-\t-\n' "$WORK" "$OLD/prompt.md" "$OLD/report.md" >>"$LANES/index.tsv"
NEW="$LANES/$(date +%F)-live"
lane live "$WORK" sonnet "$TMP/restart.md"
[ "$LAST_RC" = 0 ] || fail "a reused name on a later day starts ($LAST_OUT)"; ok
cmp -s "$NEW/prompt.md" "$TMP/restart.md" || fail "the new day's folder has its own prompt.md, the new prompt as it is"; ok
! ls "$OLD"/prompt-[0-9]*.md >/dev/null 2>&1 || fail "the old folder gets no restart note"; ok
! grep -q 'was restarted' "$NEW/prompt.md" || fail "no restart header on a fresh folder's prompt"; ok
cmp -s "$OLD/prompt.md" "$TMP/orig.md" || fail "the old prompt.md is intact"; ok
[ "$(rk_of live)" != 125 ] || fail "a fresh folder does not inherit the old recycle line"; ok
[ "$(awk -F'\t' '$2=="live"{print $7}' "$LANES/index.tsv" | tail -1)" = "$NEW/report.md" ] || fail "the index row names the new folder's report"; ok

# 4b. The same, with --restart: the previous folder is reused, a note is stored there, the old recycle line carries over.
rm -rf "$NEW"
sed -i '$d' "$LANES/index.tsv"
lane --restart live "$WORK" sonnet "$TMP/restart.md"
[ "$LAST_RC" = 0 ] || fail "a --restart on a later day works ($LAST_OUT)"; ok
[ ! -e "$NEW" ] || fail "--restart makes no second folder"; ok
ls "$OLD"/prompt-[0-9]*.md >/dev/null 2>&1 || fail "the restart note lands in the old folder"; ok
[ "$(rk_of live)" = 125 ] || fail "--restart keeps the old line (125), got $(rk_of live)"; ok
cmp -s "$OLD/prompt.md" "$TMP/orig.md" || fail "--restart keeps the old prompt.md"; ok
[ "$(ls -t "$LANES"/*-live/report.md | head -1)" = "$OLD/report.md" ] || fail "lane_watch's report lookup finds the one report"; ok

# 5. Passing the folder's own prompt.md, or the same text again, makes no note.
lane comic "$WORK" sonnet "$DIR1/prompt.md"
n="$(ls "$DIR1"/prompt-*.md | wc -l)"
lane comic "$WORK" sonnet "$TMP/orig.md"
[ "$(ls "$DIR1"/prompt-*.md | wc -l)" = "$n" ] || fail "an identical prompt stores no note"; ok

# 6. A dry run shows the plan and writes nothing.
cp "$LANES/index.tsv" "$TMP/index.before"
lane --dry-run comic "$WORK" sonnet "$TMP/restart.md"
case "$LAST_OUT" in *"prompt.md stays as it is"*) ok ;; *) fail "the dry run explains the restart: $LAST_OUT" ;; esac
cmp -s "$LANES/index.tsv" "$TMP/index.before" || fail "a dry run adds no index row"; ok

printf 'ok: %s checks\n' "$checks"
