#!/usr/bin/env bash
# Tests for the status line's rate-limits save (#273, point 3): the 7-day and 5-hour used percentages Claude Code hands the
# status line are saved to a small file for the budget reader. Runs the repo copy with a fake HOME; touches no real file.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
SL="$(cd "$(dirname "$0")/../../../dotfiles" && pwd -P)/statusline-command.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/sl-rl.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
failures=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; failures=$((failures + 1)); }
export HOME="$TMP/home"; mkdir -p "$HOME"
F="$HOME/.config/ledger/statusline-rate-limits.json"
run() { printf '%s' "$1" | STATUSLINE_NOW="${2:-1000000}" bash "$SL"; }
base='"model":{"display_name":"M"},"cwd":"/tmp","session_id":"s1","context_window":{"context_window_size":200000}'
rl='"rate_limits":{"seven_day":{"used_percentage":41.26,"resets_at":1793000000},"five_hour":{"used_percentage":7,"resets_at":1792900000}}'

# 1. figures present: saved, and the line is still one line
out=$(run "{$base,$rl}" 1000000)
[ "$(printf '%s\n' "$out" | wc -l)" = 1 ] && ok "one line of output" || bad "output was not one line"
[ "$(jq -r '.seven_day.used_percentage' "$F")" = "41.3" ] && ok "7-day percentage saved, rounded to a tenth" || bad "7-day percentage: $(cat "$F" 2>&1)"
[ "$(jq -r '.seven_day.resets_at, .five_hour.used_percentage, .five_hour.resets_at, .saved_at' "$F" | tr '\n' ' ')" = "1793000000 7 1792900000 1000000 " ] \
    && ok "reset time, 5-hour figures and saved_at saved" || bad "fields: $(cat "$F")"

# 2. unchanged figures within 5 minutes: not rewritten; after 5 minutes: saved_at refreshed
run "{$base,$rl}" 1000100 >/dev/null
[ "$(jq -r .saved_at "$F")" = 1000000 ] && ok "unchanged figures inside 5 minutes are not rewritten" || bad "rewritten too soon"
run "{$base,$rl}" 1000300 >/dev/null
[ "$(jq -r .saved_at "$F")" = 1000300 ] && ok "saved_at refreshes after 5 minutes" || bad "saved_at not refreshed"

# 3. a changed figure is written at once
run "{$base,"'"rate_limits":{"seven_day":{"used_percentage":42,"resets_at":1793000000}}}' 1000310 >/dev/null
[ "$(jq -r '.seven_day.used_percentage, .five_hour.used_percentage' "$F" | tr '\n' ' ')" = "42 null " ] \
    && ok "a changed figure is written at once; a missing 5-hour figure is null" || bad "changed figure: $(cat "$F")"

# 4. no rate_limits (a key paid by the token) or a garbled value: file untouched, line still printed
before=$(cat "$F")
out=$(run "{$base}" 1009999); [ -n "$out" ] && [ "$(cat "$F")" = "$before" ] && ok "no rate_limits: file untouched, line printed" || bad "no rate_limits changed the file"
out=$(run "{$base,"'"rate_limits":{"seven_day":{"used_percentage":"x","resets_at":"y"}}}' 1009999); [ -n "$out" ] && [ "$(cat "$F")" = "$before" ] \
    && ok "garbled figures: file untouched" || bad "garbled figures changed the file"

# 5. a first run with no file at all and no rate_limits writes nothing
rm -rf "$HOME/.config"
run "{$base}" 1 >/dev/null; [ ! -e "$F" ] && ok "no figures, no file" || bad "file created with no figures"

# 6. the override path is honoured and no stray tmp files remain
OVR="$TMP/over/rl.json"; printf '%s' "{$base,$rl}" | STATUSLINE_RATE_LIMITS_FILE="$OVR" STATUSLINE_NOW=5 bash "$SL" >/dev/null
[ -f "$OVR" ] && ok "STATUSLINE_RATE_LIMITS_FILE is honoured" || bad "override path ignored"
[ -z "$(ls "$TMP/over" | grep -v '^rl.json$')" ] && ok "no tmp files left behind" || bad "stray files: $(ls "$TMP/over")"

[ "$failures" = 0 ] && echo "all statusline rate-limit tests passed" || { echo "$failures failed"; exit 1; }
