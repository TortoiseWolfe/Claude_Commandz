#!/usr/bin/env bash
# Tests for lane.sh --add-dir (28 Oct 2026: three Band Wagon lanes froze on "Read outside the working directories" because the stream folder
# was not one of their working folders and lane.sh had no way to add one). The option repeats, goes to claude (and to codex, agy and copilot)
# as --add-dir, is kept in index column 15 so --resume, --restart and the recycle keep it, and --status shows it.
# A fake HOME and a fake tmux that logs what is typed hold everything: nothing here touches a live lane.
set -euo pipefail
# The CI machine has no claude, agy or codex program; the engine check (test_lane_engine_check.sh) is tested on its own.
export AFA_ENGINES_AVAILABLE="*"

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-adddir.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; LANES="$HOME_T/.claude/state/lanes"; WORK="$HOME_T/repos/proj"
STREAM="$TMP/stream folder"; OTHER="$TMP/other"
mkdir -p "$BIN" "$WORK" "$LANES" "$STREAM" "$OTHER"
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  has-session) exit 0 ;;
  list-windows) [ -n "${FAKE_WINDOWS:-}" ] && printf '%s\n' $FAKE_WINDOWS; exit 0 ;;
  capture-pane) printf 'ready\n' ;;
  send-keys) [ "${5:-}" = Enter ] && printf "%s\n" "${4:-}" >>"$FAKE_KEYS" || true ;;
esac
exit 0
STUB
printf '#!/usr/bin/env bash\nexit 0\n' >"$BIN/sleep"
chmod +x "$BIN/tmux" "$BIN/sleep"
KEYS="$TMP/keys.log"; : >"$KEYS"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" FAKE_KEYS="$KEYS" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }
ad_of() { awk -F'\t' -v n="$1" '$2==n{r=$15} END{print r}' "$LANES/index.tsv"; }
last_cmd() { tail -n 1 "$KEYS"; }
has() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
hasnt() { case "$1" in *"$2"*) fail "$3 (unexpected: $2)" ;; *) ok ;; esac; }

printf 'Mission one.\n' >"$TMP/p1.md"
printf 'Restart note.\n' >"$TMP/p2.md"

# 1. Two --add-dir options: both reach claude, before the other flags, the prompt stays last; the row holds them as a colon list.
lane --add-dir "$STREAM" --add-dir "$OTHER" bw "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] || fail "a start with two --add-dir works ($LAST_OUT)"; ok
c="$(last_cmd)"
has "$c" "--add-dir $(printf %q "$STREAM") --add-dir $OTHER " "both folders go to claude, each before a flag"
has "$c" "$LANES/$(date +%F)-bw" "the hand-off folder is still added"
[ "$(ad_of bw)" = "$STREAM:$OTHER" ] || fail "column 15 holds both folders, got $(ad_of bw)"; ok
case "$c" in *'follow'*'it.'*) ok ;; *) fail "the fixed message naming the prompt file is still the last argument: $c" ;; esac

# 2. No option: nothing extra, column 15 is "-".
lane plain "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] || fail "a plain start works ($LAST_OUT)"; ok
hasnt "$(last_cmd)" "$STREAM" "a plain start adds no folder"
[ "$(ad_of plain)" = - ] || fail "column 15 is - without the option, got $(ad_of plain)"; ok

# 3. Bad paths are refused, and --equals form works.
lane --add-dir "$TMP/nope" bad "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 1 ] && has "$LAST_OUT" "no such folder" "a missing folder is refused"
mkdir -p "$TMP/a:b"
lane --add-dir "$TMP/a:b" bad "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 1 ] && has "$LAST_OUT" "may not hold a colon" "a path with a colon is refused"
lane --add-dir="$OTHER" eq "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] && [ "$(ad_of eq)" = "$OTHER" ] || fail "--add-dir=<path> works ($LAST_OUT)"; ok

# 4. A dry run shows the folders and writes no row.
cp "$LANES/index.tsv" "$TMP/index.before"
lane --dry-run --add-dir "$OTHER" dry "$WORK" sonnet "$TMP/p1.md"
has "$LAST_OUT" "--add-dir\\ $OTHER" "the dry run shows the extra folder (the dry run prints the command quoted)"
cmp -s "$LANES/index.tsv" "$TMP/index.before" || fail "a dry run adds no index row"; ok

# 5. Other engines take it on their command lines (the folder is also what a restart passes on).
for eng in codex copilot agy; do
  lane --dry-run --allow-client --engine "$eng" --add-dir "$OTHER" "e-$eng" "$WORK" sonnet "$TMP/p1.md"
  case "$LAST_OUT" in *"--add-dir $OTHER"*|*"--add-dir\\ $OTHER"*) ok ;; *) fail "$eng gets --add-dir: $LAST_OUT" ;; esac
done

# 6. --resume keeps the folders; a new --add-dir on the resume is added and recorded.
printf '2026-10-28\tres\t%s\tsonnet\tsubscription\t%s\t%s\tsess-1\t1700000000\thigh\toff\t100\t-\t-\t%s\n' "$WORK" "$LANES/res/prompt.md" "$LANES/res/report.md" "$STREAM" >>"$LANES/index.tsv"
lane --resume res
[ "$LAST_RC" = 0 ] || fail "--resume works ($LAST_OUT)"; ok
has "$(last_cmd)" "--add-dir $(printf %q "$STREAM") " "--resume passes the recorded folder"
lane --resume --add-dir "$OTHER" res
has "$(last_cmd)" "--add-dir $OTHER " "--resume passes a folder given now"
[ "$(ad_of res)" = "$STREAM:$OTHER" ] || fail "--resume records the union, got $(ad_of res)"; ok
lane --resume res
has "$(last_cmd)" "--add-dir $OTHER " "the next --resume still has the added folder"

# 7. --restart keeps them (same day, same folder), and adds a new one.
lane --restart bw "$WORK" sonnet "$TMP/p2.md"
[ "$LAST_RC" = 0 ] || fail "--restart works ($LAST_OUT)"; ok
[ "$(ad_of bw)" = "$STREAM:$OTHER" ] || fail "--restart keeps the folders, got $(ad_of bw)"; ok
has "$(last_cmd)" "--add-dir $(printf %q "$STREAM") " "a restart passes the kept folder"
mkdir -p "$TMP/third"
lane --restart --add-dir "$TMP/third" --add-dir "$STREAM" bw "$WORK" sonnet "$TMP/p2.md"
[ "$(ad_of bw)" = "$STREAM:$OTHER:$TMP/third" ] || fail "--restart adds a new folder once, got $(ad_of bw)"; ok

# 8. --add-dir with a mode that starts nothing is refused.
lane --status --add-dir "$STREAM"
[ "$LAST_RC" = 1 ] && has "$LAST_OUT" "only applies when starting or resuming" "--add-dir with --status is refused"

# 9. --status shows the folders of a live lane (and shows nothing for a lane without any).
FAKE_WINDOWS="bw plain" lane --status
has "$LAST_OUT" "add-dirs:$STREAM:$OTHER:$TMP/third" "--status shows the extra folders"
[ "$(grep -c 'add-dirs:' <<<"$LAST_OUT")" = 1 ] || fail "only the lane with folders shows add-dirs"; ok

printf 'ok: %s checks\n' "$checks"
