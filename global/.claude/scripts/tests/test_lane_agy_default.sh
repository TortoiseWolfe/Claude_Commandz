#!/usr/bin/env bash
# Tests for the agy default in lane.sh (his rule, 22 Oct 3:30 PM: "route my own-repo code to agy now"; 67 own-repo lanes since 18 Oct
# still ran on Claude because the rule was only written down). A Claude start in one of his own repos is refused unless
# --claude-because "<reason>" is given; client repos, ~/repos itself, mail lanes, site-model prompts and --engine agy are never asked.
# A fake HOME holds the client lists and a fake tmux; everything runs with --dry-run or against the fake HOME's own log.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-agydefault.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; LANES="$HOME_T/.claude/state/lanes"; TLOG="$LANES/tier-overrides.log"
OWN="$HOME_T/repos/own-app"; CLIENT="$HOME_T/repos/client-app"; LISTED="$HOME_T/repos/listed-app"; CDTREE="$HOME_T/repos/CD/thing"
mkdir -p "$BIN" "$HOME_T/repos/hub" "$OWN" "$CLIENT" "$LISTED" "$CDTREE" "$LANES"
printf 'id\trepo_dir\nacme\t~/repos/client-app\n' >"$HOME_T/repos/hub/client-senders.tsv"
printf '# Client repositories\n~/repos/listed-app\n' >"$HOME_T/repos/hub/client-repos.txt"
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in has-session) exit 0 ;; list-windows) exit 0 ;; capture-pane) printf 'ready\n' ;; esac
STUB
cat >"$BIN/sleep" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
chmod +x "$BIN/tmux" "$BIN/sleep"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
assert_contains() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
assert_not_contains() { case "$1" in *"$2"*) fail "$3 (unexpected: $2)" ;; *) ok ;; esac; }
assert_exit() { [ "$2" = "$1" ] || fail "$3 (expected exit $1, got $2; output: $LAST_OUT)"; ok; }
lane() { set +e; LAST_OUT="$(HOME="${LANE_HOME:-$HOME_T}" PATH="$BIN:$PATH" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }

P_CODE="$TMP/code.md"; printf 'Fix the lint errors in the header component.\n' >"$P_CODE"
P_SITE="$TMP/site.md"; printf 'Fit the photos in data/sites/example-01.\n' >"$P_SITE"
P_MAIL="$TMP/mail.md"; printf 'Draft a reply to the email the client sent about the invoice.\n' >"$P_MAIL"

# ---- own repo + Claude, no reason: refused with the agy command --------------------------------------------------------------------------
lane --dry-run fix-header "$OWN" sonnet "$P_CODE"
assert_exit 1 "$LAST_RC" 'an own-repo code lane on Claude with no reason is refused'
assert_contains "$LAST_OUT" 'lane.sh --engine agy fix-header' 'the refusal prints the agy command with the lane name'
assert_contains "$LAST_OUT" "$OWN gemini-3.8-flash-high $P_CODE" 'the refusal prints the caller own dir and prompt file'
assert_contains "$LAST_OUT" '--claude-because' 'the refusal names the override'
for kind in 'needs Docker' 'needs wsl.exe' 'needs a live service' 'agy out of usage' 'review or planning (strongest tier)' 'not code'; do
  assert_contains "$LAST_OUT" "$kind" "the refusal lists the accepted reason: $kind"
done
assert_not_contains "$LAST_OUT" 'mkdir -p' 'a refused lane makes no hand-off folder'
for m in opus fable haiku; do
  lane --dry-run fix-header "$OWN" "$m" "$P_CODE"
  assert_exit 1 "$LAST_RC" "the same lane on $m is refused too"
done

# ---- with a reason: allowed, logged -----------------------------------------------------------------------------------------------------
lane --dry-run --claude-because "needs Docker" fix-header "$OWN" sonnet "$P_CODE"
assert_exit 0 "$LAST_RC" '--claude-because "needs Docker" lets the lane start'
assert_contains "$LAST_OUT" 'would log the agy-default override' 'a dry run says it would log the override'
[ ! -e "$TLOG" ] || fail 'a dry run writes no log'; ok
lane --claude-because="needs wsl.exe" real-fix "$OWN" sonnet "$P_CODE"
assert_exit 0 "$LAST_RC" 'a real start with a reason goes through'
[ -s "$TLOG" ] || fail 'the override is logged'; ok
log="$(cat "$TLOG")"
assert_contains "$log" 'real-fix' 'the log names the lane'
assert_contains "$log" 'sonnet' 'the log names the model'
assert_contains "$log" 'agy-default' 'the log names the kind'
assert_contains "$log" 'needs wsl.exe' 'the log holds the reason'
assert_contains "$log" "$OWN" 'the log names the dir'
rm -f "$TLOG"

# ---- an empty reason is refused ----------------------------------------------------------------------------------------------------------
lane --dry-run --claude-because "" fix-header "$OWN" sonnet "$P_CODE"
assert_exit 1 "$LAST_RC" 'an empty reason is refused'
lane --dry-run --claude-because "   " fix-header "$OWN" sonnet "$P_CODE"
assert_exit 1 "$LAST_RC" 'a blank reason is refused'
lane --dry-run --claude-because
assert_exit 1 "$LAST_RC" '--claude-because with nothing after it is refused'

# ---- never asked ------------------------------------------------------------------------------------------------------------------------
lane --dry-run fix-header "$CLIENT" sonnet "$P_CODE"
assert_exit 0 "$LAST_RC" 'a client repo (client-senders.tsv) on Claude needs no reason'
lane --dry-run fix-header "$LISTED" sonnet "$P_CODE"
assert_exit 0 "$LAST_RC" 'a client repo (client-repos.txt) on Claude needs no reason'
lane --dry-run fix-header "$CDTREE" sonnet "$P_CODE"
assert_exit 0 "$LAST_RC" 'a folder under ~/repos/CD on Claude needs no reason'
lane --dry-run fix-header "$HOME_T/repos" sonnet "$P_CODE"
assert_exit 0 "$LAST_RC" '~/repos itself needs no reason'
lane --dry-run acme-mail "$OWN" sonnet "$P_CODE"
assert_exit 0 "$LAST_RC" 'a -mail lane on an own repo needs no reason'
lane --dry-run draft-it "$OWN" sonnet "$P_MAIL"
assert_exit 0 "$LAST_RC" 'a lane drafting an email reply on an own repo needs no reason'
lane --dry-run fit-photos "$OWN" opus "$P_SITE"
assert_exit 0 "$LAST_RC" 'a site-model prompt on an own repo needs no reason'
lane --dry-run --engine agy fix-header "$OWN" gemini-3.8-flash-high "$P_CODE"
assert_exit 0 "$LAST_RC" '--engine agy needs no reason'
lane --dry-run --engine codex fix-header "$OWN" gpt-5 "$P_CODE"
assert_exit 0 "$LAST_RC" '--engine codex needs no reason'
lane --dry-run --claude-because "needs Docker" fix-header "$CLIENT" sonnet "$P_CODE"
assert_exit 0 "$LAST_RC" 'a reason on a client repo is harmless'
assert_not_contains "$LAST_OUT" 'agy-default' 'and is not logged there'

# ---- the guard stands aside when the client lists cannot be read (cannot tell client from own) ---------------------------------
BARE="$TMP/bare"; mkdir -p "$BARE/repos/own-app" "$BARE/.claude/state/lanes"
LANE_HOME="$BARE" lane --dry-run fix-header "$BARE/repos/own-app" sonnet "$P_CODE"
assert_exit 0 "$LAST_RC" 'with no client lists the repo cannot be told apart, so nothing is refused'
unset LANE_HOME

# ---- other modes are untouched -----------------------------------------------------------------------------------------------------------
lane --help
assert_contains "$LAST_OUT" '--claude-because' 'help documents the option'
assert_contains "$LAST_OUT" 'gemini-3.8-flash-high' 'help names the agy command'
head -6 "$SCRIPT" | grep -q -- '--claude-because' || fail 'the header usage names the option'; ok

printf 'ok - %s checks passed\n' "$checks"
