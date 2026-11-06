#!/usr/bin/env bash
# Tests for opencode_lane.sh's retry and stamp logic (issue 267): a per-minute refusal waits and retries the same provider; a per-day refusal
# writes a stamp, moves on, and later runs skip that provider. A stub opencode (OPENCODE_BIN) plays the providers.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/opencode_lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/opencode-lane.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
HOME_T="$TMP/home"; REPO="$TMP/repo"; HAND="$TMP/hand"; PROMPT="$TMP/prompt.md"
mkdir -p "$HOME_T" "$REPO" "$HAND"
echo 'do the thing' >"$PROMPT"

GEMINI=google/gemini-3-flash-preview
ROUTER=openrouter/nvidia/nemotron-3-ultra-550b-a55b:free

# Stub: calls are logged as "<model>". STUB_PLAN is a file with one line per call to a model, "<model>|<action>"; each call to a model uses the
# next unused line for it (a model with no lines left succeeds). Actions: ok, minute (Gemini's per-minute message, retry in 2s), minute-forever,
# day (OpenRouter's per-day message), nodelay (a refusal with no delay and no daily wording).
cat >"$TMP/opencode" <<'STUB'
#!/usr/bin/env bash
model=""; while [ $# -gt 0 ]; do [ "$1" = --model ] && { model="$2"; shift; }; shift; done
echo "$model" >>"$STUB_CALLS"
n=$(grep -cFx "$model" "$STUB_CALLS")
action=$(grep -F "$model|" "$STUB_PLAN" 2>/dev/null | sed -n "${n}p" | cut -d'|' -f2 || true)
case "${action:-ok}" in
  minute) printf '\033[91m\033[1mError: \033[0mRate limit exceeded: quota exceeded for metric generate_content_free_tier_requests, PerMinute. Please retry in 2.01s\n'; exit 1 ;;
  minute-forever) printf 'Error: 429 rate limit. Please retry in 3s\n'; exit 1 ;;
  day) printf 'Error: Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day\n'; exit 1 ;;
  nodelay) printf 'Error: 429 RESOURCE_EXHAUSTED\n'; exit 1 ;;
  *) printf 'tests ran: this text says rate limit and quota but is not an Error line\n'; exit 0 ;;
esac
STUB
chmod +x "$TMP/opencode"

fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" <<<"$1" || fail "$3: expected '$2' in: $1"; }
lacks() { grep -qF -- "$2" <<<"$1" && fail "$3: did not expect '$2' in: $1"; return 0; }
count() { grep -cFx -- "$2" "$1" || true; }

export HOME="$HOME_T" OPENCODE_BIN="$TMP/opencode" OPENCODE_QUOTA_DIR="$TMP/quota" OPENCODE_RETRY_SCALE=0.01 OPENCODE_LANE_POLL=0.05
export STUB_CALLS="$TMP/calls" STUB_PLAN="$TMP/plan"
run() {  # <plan lines...>: runs the wrapper with a fresh call log; sets OUT and STATUS
  : >"$STUB_CALLS"; printf '%s\n' "$@" >"$STUB_PLAN"
  set +e; OUT="$(bash "$SCRIPT" "$REPO" "$HAND" "$GEMINI" "$PROMPT" 2>&1)"; STATUS=$?; set -e
}
CASES=0

# 1. per-minute refusal once, then success: the same provider is retried, exit 0, OpenRouter never called, no stamp.
run "$GEMINI|minute" "$GEMINI|ok"
CASES=$((CASES + 1))
[ "$STATUS" = 0 ] || fail "retry then success should exit 0, got $STATUS: $OUT"
[ "$(count "$STUB_CALLS" "$GEMINI")" = 2 ] || fail "Gemini should be called twice: $(cat "$STUB_CALLS")"
[ "$(count "$STUB_CALLS" "$ROUTER")" = 0 ] || fail "OpenRouter should not be called"
contains "$OUT" "opencode: google per-minute limit; retrying in" "retry line"
contains "$OUT" "(1/5)" "retry counter"
lacks "$OUT" "trying the next provider" "no fallback on a per-minute limit"
[ ! -e "$TMP/quota" ] || [ -z "$(ls -A "$TMP/quota")" ] || fail "a per-minute limit must not write a stamp"

# 2. per-minute refusal on every try: five retries, then the next provider takes over and succeeds.
run "$GEMINI|minute-forever" "$GEMINI|minute-forever" "$GEMINI|minute-forever" "$GEMINI|minute-forever" "$GEMINI|minute-forever" "$GEMINI|minute-forever"
CASES=$((CASES + 1))
[ "$STATUS" = 0 ] || fail "fallback after retries should exit 0, got $STATUS: $OUT"
[ "$(count "$STUB_CALLS" "$GEMINI")" = 6 ] || fail "Gemini: one try plus five retries: $(cat "$STUB_CALLS")"
[ "$(count "$STUB_CALLS" "$ROUTER")" = 1 ] || fail "OpenRouter takes over after the retries"
contains "$OUT" "(5/5)" "fifth retry"
contains "$OUT" "still rate limited after 5 retries" "retries used up"

# 3. a refusal with no delay and no daily wording falls through at once and writes no stamp.
rm -rf "$TMP/quota"
run "$GEMINI|nodelay"
CASES=$((CASES + 1))
[ "$(count "$STUB_CALLS" "$GEMINI")" = 1 ] || fail "no retry without a delay"
[ "$(count "$STUB_CALLS" "$ROUTER")" = 1 ] || fail "falls through to OpenRouter"
[ ! -e "$TMP/quota" ] || [ -z "$(ls -A "$TMP/quota")" ] || fail "a delay-less refusal is not stamped"

# 4. OpenRouter's per-day refusal: stamp written, exit 75; the next run skips OpenRouter without calling it.
rm -rf "$TMP/quota"
run "$GEMINI|nodelay" "$ROUTER|day"
CASES=$((CASES + 1))
[ "$STATUS" = 75 ] || fail "every provider out should exit 75, got $STATUS: $OUT"
stamp="$TMP/quota/openrouter-$(date +%F)"
[ -e "$stamp" ] || fail "per-day refusal should write $stamp; have: $(ls "$TMP/quota" 2>/dev/null)"
contains "$OUT" "free quota exhausted on every provider" "line the dispatcher reads"
run "$GEMINI|nodelay" "$ROUTER|ok"
CASES=$((CASES + 1))
[ "$(count "$STUB_CALLS" "$ROUTER")" = 0 ] || fail "a stamped provider must not be called"
contains "$OUT" "skipping $ROUTER" "skip line"
[ "$STATUS" = 75 ] || fail "Gemini out and OpenRouter stamped should exit 75, got $STATUS"

# 5. a stamp from another day does not count, and Gemini working still exits 0 with OpenRouter stamped.
rm -rf "$TMP/quota"; mkdir -p "$TMP/quota"; : >"$TMP/quota/openrouter-2000-01-01"
run "$GEMINI|nodelay" "$ROUTER|ok"
CASES=$((CASES + 1))
[ "$(count "$STUB_CALLS" "$ROUTER")" = 1 ] || fail "an old stamp must not block OpenRouter"
[ "$STATUS" = 0 ] || fail "OpenRouter succeeding should exit 0, got $STATUS"

# 6. keys never reach the log: the wrapper's own output holds no key text.
mkdir -p "$HOME_T/.config/gemini"; echo 'SECRETKEY123' >"$HOME_T/.config/gemini/api-key"
run "$GEMINI|minute" "$GEMINI|ok"
CASES=$((CASES + 1))
lacks "$OUT" "SECRETKEY123" "key stays out of the log"

echo "ok: $CASES cases"
