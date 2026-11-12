#!/usr/bin/env bash
# Tests for lane.sh --add-dir (28 Oct 2026: three Band Wagon lanes froze on "Read outside the working directories" because the stream folder
# was not one of their working folders and lane.sh had no way to add one). The option repeats, goes to claude (and to codex, agy and copilot)
# as --add-dir, is kept in index column 15 so --resume, --restart and the recycle keep it, and --status shows it.
# A fake HOME and a fake tmux that logs what is typed hold everything: nothing here touches a live lane.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
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

# 8. A recorded folder that is gone by the next start is dropped, not passed on: the engines refuse a missing --add-dir and the lane dies
#    at start (1 Nov 2026, job-replies round 2 on round 1's removed worktree).
rmdir "$TMP/third"
lane --restart bw "$WORK" sonnet "$TMP/p2.md"
[ "$LAST_RC" = 0 ] || fail "--restart with a removed folder still works ($LAST_OUT)"; ok
hasnt "$(last_cmd)" "$TMP/third" "a removed folder is not passed on a restart"
has "$(last_cmd)" "--add-dir $(printf %q "$STREAM") " "the folders that still exist are passed"
[ "$(ad_of bw)" = "$STREAM:$OTHER" ] || fail "the index drops the removed folder, got $(ad_of bw)"; ok
has "$LAST_OUT" "no longer exists: $TMP/third" "the restart says which folder it dropped"
rmdir "$OTHER"
lane --resume res
[ "$LAST_RC" = 0 ] || fail "--resume with a removed folder still works ($LAST_OUT)"; ok
hasnt "$(last_cmd)" "--add-dir $OTHER " "a removed folder is not passed on a resume"

# 10. A lane started in a repo gets that repo's own worktree checkouts (~/repos/.worktrees/<repo>--<topic>) as --add-dir, and nothing else
#     (29 Oct 2026: sh-1414 froze three times on "Read outside the working directories" for files in its own checkout). Another repo's
#     checkouts, a repo whose name merely starts the same, and a folder outside ~/repos get none.
WT="$HOME_T/repos/.worktrees"
mkdir -p "$WT/proj--topic-a" "$WT/proj--topic-b" "$WT/projx--other" "$WT/elsewhere--c" "$TMP/outside"
lane wt "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] || fail "a start in a repo with checkouts works ($LAST_OUT)"; ok
c="$(last_cmd)"
has "$c" "--add-dir $WT/proj--topic-a --add-dir $WT/proj--topic-b " "both checkouts of the lane's repo are added, each before a flag"
hasnt "$c" "projx--other" "a repo whose name starts the same is not matched"
hasnt "$c" "elsewhere--c" "another repo's checkout is not added"
# 7 Nov 2026: the hub reversed the old rule "the whole .worktrees folder is never added". A path given to --add-dir must exist, so a worktree the lane makes after
# launch can only be covered by its parent folder; before this every read in one stopped the lane on the "outside the working directories" dialog.
has "$c" "--add-dir $WT " "the .worktrees folder is added, so worktrees made after launch read without the dialog"
[ "$(printf '%s' "$c" | grep -o -- "--add-dir $WT " | wc -l)" = 1 ] || fail "the .worktrees folder is added once ($c)"; ok
has "$c" "$LANES/$(date +%F)-wt" "the hand-off folder is still added"
[ "$(ad_of wt)" = "$WT/proj--topic-a:$WT/proj--topic-b" ] || fail "column 15 holds the checkouts, got $(ad_of wt)"; ok
lane --add-dir "$STREAM" wt2 "$WORK/" sonnet "$TMP/p1.md"
[ "$(ad_of wt2)" = "$STREAM:$WT/proj--topic-a:$WT/proj--topic-b" ] || fail "an explicit --add-dir stays, the checkouts follow it, got $(ad_of wt2)"; ok
mkdir -p "$WORK/sub"
lane wt3 "$WORK/sub" sonnet "$TMP/p1.md"
has "$(last_cmd)" "--add-dir $WT/proj--topic-a " "a lane started in a folder inside the repo gets the repo's checkouts"
lane wt4 "$WT/proj--topic-a" sonnet "$TMP/p1.md"
has "$(last_cmd)" "--add-dir $WT/proj--topic-b " "a lane started in a checkout gets its repo's other checkouts"
hasnt "$(last_cmd)" "elsewhere--c" "...and not another repo's"
lane out "$TMP/outside" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] && hasnt "$(last_cmd)" "$WT" "a lane outside ~/repos gets no checkout"
[ "$(ad_of out)" = - ] || fail "column 15 is - outside ~/repos, got $(ad_of out)"; ok
lane --dry-run dry "$WORK" sonnet "$TMP/p1.md"
has "$LAST_OUT" "proj--topic-a" "a dry run shows the checkouts"
# a checkout made after the lane started is picked up by --resume, and a removed one is dropped
mkdir -p "$WT/proj--late"; rmdir "$WT/proj--topic-b"
printf '2026-11-04\twtr\t%s\tsonnet\tsubscription\t%s\t%s\tsess-2\t1700000000\thigh\toff\t100\t-\t-\t%s\n' "$WORK" "$LANES/wtr/prompt.md" "$LANES/wtr/report.md" "$WT/proj--topic-a:$WT/proj--topic-b" >>"$LANES/index.tsv"
lane --resume wtr
[ "$LAST_RC" = 0 ] || fail "--resume with a new checkout works ($LAST_OUT)"; ok
has "$(last_cmd)" "--add-dir $WT/proj--late " "--resume adds a checkout made since the start"
hasnt "$(last_cmd)" "topic-b" "--resume drops a removed checkout"
[ "$(ad_of wtr)" = "$WT/proj--topic-a:$WT/proj--late" ] || fail "the index follows, got $(ad_of wtr)"; ok
# a symlink and a name with a colon are never added (the colon would split the list in column 15)
ln -s "$TMP/outside" "$WT/proj--link"; mkdir -p "$WT/proj--co:lon"
lane wtq "$WORK" sonnet "$TMP/p1.md"
hasnt "$(last_cmd)" "proj--link" "a symlinked checkout is not added"
hasnt "$(last_cmd)" "co:lon" "a checkout with a colon in its name is not added"
rm -f "$WT/proj--link"; rmdir "$WT/proj--co:lon"
# the HOME of a lane with no ~/repos/.worktrees at all is untouched
rm -rf "$WT"
lane nowt "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] && [ "$(ad_of nowt)" = - ] || fail "no .worktrees folder: nothing added"; ok

# 11. A claude lane also gets the shared read folders (Claude Code's read block is lifted only by a working directory): ~/.claude/state, the Claude temp
# folder, and the main checkout of the repo when the lane runs in a worktree. Computed at each start/resume, never written to column 15; other engines get none.
TMPC="$TMP/tmpdir"; mkdir -p "$TMPC/claude-$(id -u)" "$WT/proj--topic-a" "$WORK"
shared() { set +e; LAST_OUT="$(HOME="$HOME_T" TMPDIR="$TMPC" PATH="$BIN:$PATH" FAKE_KEYS="$KEYS" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }
shared sh1 "$WT/proj--topic-a" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] || fail "start in a worktree works ($LAST_OUT)"; ok
c="$(last_cmd)"
has "$c" "--add-dir $HOME_T/.claude/state " "the state folder (hub and peer inboxes) is added"
has "$c" "--add-dir $TMPC/claude-$(id -u) " "Claude's temp folder is added"
has "$c" "--add-dir $WORK " "a lane in a worktree gets the repo's main checkout"
has "$c" "--add-dir $WT " "a lane gets ~/repos/.worktrees, so worktrees it makes later read without the dialog (7 Nov 2026)"
[ "$(ad_of sh1)" = "$WT/proj--topic-a" ] || fail "column 15 holds only the checkouts, got $(ad_of sh1)"; ok
shared sh2 "$WORK" sonnet "$TMP/p1.md"
c="$(last_cmd)"
has "$c" "--add-dir $HOME_T/.claude/state " "a lane in the main checkout gets the state folder"
hasnt "$c" "--add-dir $WORK " "...but not its own folder again"
has "$c" "--add-dir $WT " "a lane in the main checkout gets ~/repos/.worktrees too"
shared --add-dir "$HOME_T/.claude/state" sh3 "$WORK" sonnet "$TMP/p1.md"
c="$(last_cmd)"
[ "$(printf '%s' "$c" | grep -o -- "--add-dir $HOME_T/.claude/state " | wc -l)" = 1 ] || fail "a folder already given is not added twice ($c)"; ok
# --resume adds them too, and still records nothing
printf '2026-11-04\tsh4\t%s\tsonnet\tsubscription\t%s\t%s\tsess-4\t1700000000\thigh\toff\t100\t-\t-\t-\n' "$WT/proj--topic-a" "$LANES/sh4/prompt.md" "$LANES/sh4/report.md" >>"$LANES/index.tsv"
shared --resume sh4
[ "$LAST_RC" = 0 ] || fail "--resume works ($LAST_OUT)"; ok
has "$(last_cmd)" "--add-dir $HOME_T/.claude/state " "--resume adds the state folder"
has "$(last_cmd)" "--add-dir $WORK " "--resume adds the main checkout"
has "$(last_cmd)" "--add-dir $WT " "--resume adds ~/repos/.worktrees"
[ "$(ad_of sh4)" = "-" ] || [ "$(ad_of sh4)" = "$WT/proj--topic-a" ] || fail "--resume leaves column 15 to the checkouts, got $(ad_of sh4)"; ok
# the other engines get none of them
for eng in codex agy copilot; do
  shared --dry-run --allow-client --engine "$eng" "sh5-$eng" "$WT/proj--topic-a" sonnet "$TMP/p1.md"
  [ "$LAST_RC" = 0 ] || fail "$eng dry run works ($LAST_OUT)"; ok
  hasnt "$LAST_OUT" "--add-dir $WT " "$eng gets no worktrees folder"; hasnt "$LAST_OUT" "--add-dir $HOME_T/.claude/state " "$eng gets no state folder"; hasnt "$LAST_OUT" "claude-$(id -u)" "$eng gets no temp folder"
done
# a symlink, a missing folder and a folder on the no-AI list are left out
mkdir -p "$HOME_T/.claude/no-ai-wall"
printf 'no_ai_wall_paths_hit() { case "$1" in *claude-*) return 0 ;; *) return 1 ;; esac; }\nno_ai_wall_text_hit() { return 1; }\n' >"$HOME_T/.claude/no-ai-wall/no_ai_wall.sh"
shared sh6 "$WT/proj--topic-a" sonnet "$TMP/p1.md"
c="$(last_cmd)"; hasnt "$c" "claude-$(id -u)" "a folder on the no-AI list is left out"; has "$c" "--add-dir $HOME_T/.claude/state " "...the others stay"
rm -rf "$HOME_T/.claude/no-ai-wall"
rm -rf "$TMPC/claude-$(id -u)"; ln -s "$TMP/outside" "$TMPC/claude-$(id -u)"
shared sh7 "$WT/proj--topic-a" sonnet "$TMP/p1.md"
hasnt "$(last_cmd)" "claude-$(id -u)" "a symlinked temp folder is left out"
rm -f "$TMPC/claude-$(id -u)"
shared sh8 "$WT/proj--topic-a" sonnet "$TMP/p1.md"
hasnt "$(last_cmd)" "claude-$(id -u)" "a missing temp folder is left out"

# the shared folders are not a way around the hub's control files: Edit/Write are denied on them (Bash is unchanged), and the main checkout's .git too
shared sh9 "$WT/proj--topic-a" sonnet "$TMP/p1.md"
c="$(last_cmd)"
has "$c" "--disallowedTools" "a claude lane carries deny rules"
has "$c" "Edit\\(/$HOME_T/.claude/state/lanes/.deny/\\*\\*\\)" "the .deny files cannot be edited"
has "$c" "Edit\\(/$HOME_T/.claude/state/lanes/.rules/\\*\\*\\)" "the .rules files cannot be edited"
has "$c" "Edit\\(/$HOME_T/.claude/state/lanes/index.tsv\\)" "the lane index cannot be edited"
has "$c" "Edit\\(/$WORK/.git/\\*\\*\\)" "the main checkout's .git (hooks) cannot be edited from a worktree lane"
has "$c" "Edit\\(/$WT/\\*/.git\\)" "with the worktrees folder granted, no worktree's .git pointer file can be edited"
# a temp folder others can write to, or one owned by someone else's mode, is left out
mkdir -p "$TMPC/claude-$(id -u)"; chmod 777 "$TMPC/claude-$(id -u)"
shared sh10 "$WT/proj--topic-a" sonnet "$TMP/p1.md"
hasnt "$(last_cmd)" "--add-dir $TMPC/claude-$(id -u) " "a world-writable temp folder is left out"
chmod 700 "$TMPC/claude-$(id -u)"
shared sh11 "$WT/proj--topic-a" sonnet "$TMP/p1.md"
has "$(last_cmd)" "--add-dir $TMPC/claude-$(id -u) " "a private temp folder is added"

printf 'ok: %s checks\n' "$checks"
