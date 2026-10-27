#!/usr/bin/env bash
# Tests for project MCP servers in lane.sh (Job 23 Oct): a lane started in a repo with a .mcp.json stalled on Claude Code's "New MCP server
# found" dialog and its prompt never arrived. lane.sh now lists every project server (the folder's .mcp.json and any above it) as
# disabledMcpjsonServers in the lane's own settings, which answers the dialog in advance and loads none of them. Everything runs with
# --dry-run in a fake HOME; nothing touches the live lanes. The last block also checks the recycle line a connector lane starts with.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-mcp.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; LANES="$HOME_T/.claude/state/lanes"
WITH="$HOME_T/repos/with-mcp"; PLAIN="$HOME_T/repos/plain"; NESTED="$HOME_T/repos/with-mcp/packages/app"; BAD="$HOME_T/repos/bad-json"
mkdir -p "$BIN" "$HOME_T/repos/hub" "$WITH" "$PLAIN" "$NESTED" "$BAD" "$LANES"
printf '{"mcpServers":{"playwright":{"command":"docker"},"files":{"command":"x"}}}\n' >"$WITH/.mcp.json"
printf '{"mcpServers":{"extra":{"command":"y"},"playwright":{"command":"z"}}}\n' >"$WITH/packages/.mcp.json"
printf '{ not json\n' >"$BAD/.mcp.json"
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
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }

P="$TMP/p.md"; printf 'Write report.md.\n' >"$P"

# The lane's --settings JSON, decoded from the tmux send-keys line the dry run prints (empty when there is none).
settings_of() {
  python3 -I -c '
import shlex, sys
for line in sys.stdin.read().splitlines():
    if line.startswith("tmux send-keys") and "--settings" in line:
        words = shlex.split(shlex.split(line)[4])
        print(words[words.index("--settings") + 1])
        break
' <<<"$LAST_OUT"
}
servers_of() {  # -> the disabledMcpjsonServers list as JSON, or "none"
  python3 -I -c 'import json,sys; d=json.loads(sys.argv[1]); print(json.dumps(d.get("disabledMcpjsonServers","none"), separators=(",", ":")))' "$(settings_of)"
}

lane --dry-run --claude-because "needs Docker" probe-a "$WITH" sonnet "$P"
[ "$LAST_RC" = 0 ] || fail "dry run in a repo with .mcp.json failed: $LAST_OUT"; ok
[ "$(servers_of)" = '["playwright","files"]' ] || fail "a repo with a .mcp.json gets its servers (once each) disabled in the lane settings; got $(servers_of)"; ok

lane --dry-run --claude-because "needs Docker" probe-b "$NESTED" sonnet "$P"
[ "$(servers_of)" = '["extra","playwright","files"]' ] || fail "every .mcp.json from the lane dir up counts; got $(servers_of)"; ok

lane --dry-run --claude-because "needs Docker" probe-c "$PLAIN" sonnet "$P"
[ "$LAST_RC" = 0 ] || fail "dry run in a plain repo failed: $LAST_OUT"; ok
[ "$(servers_of)" = '"none"' ] || fail "a repo with no .mcp.json adds nothing to the settings; got $(servers_of)"; ok

lane --dry-run --claude-because "needs Docker" probe-d "$BAD" sonnet "$P"
[ "$LAST_RC" = 0 ] || fail "a malformed .mcp.json stopped the lane from starting: $LAST_OUT"; ok
[ "$(servers_of)" = '"none"' ] || fail "a malformed .mcp.json is ignored, not fatal; got $(servers_of)"; ok

# The rest of the settings are untouched by the new key.
lane --dry-run --claude-because "needs Docker" probe-e "$WITH" sonnet "$P"
python3 -I -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["disableClaudeAiConnectors"] is True and "autoCompactWindow" in d and "hooks" in d, d' "$(settings_of)" || fail "the other lane settings are still there"; ok

# A real start (fake tmux, fake HOME) records the recycle line in the index row: connectors on -> 127K, off -> 100K, --recycle-at wins.
lane --claude-because "needs Docker" --connectors row-on "$PLAIN" sonnet "$P"
[ "$LAST_RC" = 0 ] || fail "real start with connectors failed: $LAST_OUT"; ok
[ "$(awk -F'\t' '$2=="row-on"{print $11" "$12}' "$LANES/index.tsv")" = "on 127" ] || fail "a connector lane starts with line 127K; row: $(grep row-on "$LANES/index.tsv")"; ok
lane --claude-because "needs Docker" row-off "$PLAIN" sonnet "$P"
[ "$(awk -F'\t' '$2=="row-off"{print $11" "$12}' "$LANES/index.tsv")" = "off 100" ] || fail "a lane without connectors starts with line 100K"; ok
lane --claude-because "needs Docker" --connectors --recycle-at 90 row-set "$PLAIN" sonnet "$P"
[ "$(awk -F'\t' '$2=="row-set"{print $12}' "$LANES/index.tsv")" = 90 ] || fail "--recycle-at wins over the connector default"; ok

# 27 Oct: a connector lane with large tool lists primed near 130K and hit the 150K ceiling six minutes after a re-prime. With the
# connectors on, --recycle-at goes up to 165K (just under where Claude Code compacts) and the backstop window follows the line up to the cap.
lane --claude-because "needs Docker" --connectors --recycle-at 160 row-high "$PLAIN" sonnet "$P"
[ "$LAST_RC" = 0 ] || fail "--recycle-at 160 with the connectors on was refused: $LAST_OUT"; ok
[ "$(awk -F'\t' '$2=="row-high"{print $11" "$12}' "$LANES/index.tsv")" = "on 160" ] || fail "a connector lane may start with line 160K; row: $(grep row-high "$LANES/index.tsv")"; ok
lane --claude-because "needs Docker" --connectors --recycle-at 166 row-over "$PLAIN" sonnet "$P"
[ "$LAST_RC" = 1 ] || fail "--recycle-at 166 is past the ceiling even with the connectors on"; ok
lane --claude-because "needs Docker" --recycle-at 160 row-noconn "$PLAIN" sonnet "$P"
[ "$LAST_RC" = 1 ] || fail "--recycle-at 160 without the connectors keeps the 150K ceiling"; ok
win_of() { python3 -I -c 'import json,sys; print(json.loads(sys.argv[1])["autoCompactWindow"])' "$(settings_of)"; }
lane --dry-run --claude-because "needs Docker" --connectors --recycle-at 160 win-a "$PLAIN" sonnet "$P"
[ "$(win_of)" = 200000 ] || fail "the backstop for a 160K line is capped at the 200000 window; got $(win_of)"; ok
LANE_WINDOW_CAP=300000 lane --dry-run --claude-because "needs Docker" --connectors --recycle-at 160 win-b "$PLAIN" sonnet "$P"
[ "$(win_of)" = 270000 ] || fail "the backstop follows the line (160K + 60K room, over 0.84) when the cap allows; got $(win_of)"; ok

printf 'ok: %d checks\n' "$checks"
