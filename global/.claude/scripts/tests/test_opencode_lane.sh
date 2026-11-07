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
ZEN=opencode/big-pickle

# Stub: calls are logged as "<model>". STUB_PLAN is a file with one line per call to a model, "<model>|<action>"; each call to a model uses the
# next unused line for it (a model with no lines left succeeds). Actions: ok, minute (Gemini's per-minute message, retry in 2s), minute-forever,
# day (OpenRouter's per-day message), nodelay (a refusal with no delay and no daily wording).
cat >"$TMP/opencode" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_ARGS"
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

# The existing cases run with both provider keys present; the keyless cases at the end remove them. (Key files hold a placeholder.)
mkdir -p "$HOME_T/.config/gemini" "$HOME_T/.config/openrouter"; printf 'placeholder\n' >"$HOME_T/.config/gemini/api-key"; printf 'placeholder\n' >"$HOME_T/.config/openrouter/api-key"
export HOME="$HOME_T" OPENCODE_BIN="$TMP/opencode" OPENCODE_QUOTA_DIR="$TMP/quota" OPENCODE_RETRY_SCALE=0.01 OPENCODE_LANE_POLL=0.05
export STUB_CALLS="$TMP/calls" STUB_PLAN="$TMP/plan" STUB_ARGS="$TMP/args"
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
run "$GEMINI|nodelay" "$ROUTER|day" "$ZEN|nodelay"
CASES=$((CASES + 1))
[ "$STATUS" = 75 ] || fail "every provider out should exit 75, got $STATUS: $OUT"
stamp="$TMP/quota/openrouter-$(date +%F)"
[ -e "$stamp" ] || fail "per-day refusal should write $stamp; have: $(ls "$TMP/quota" 2>/dev/null)"
contains "$OUT" "free quota exhausted on every provider" "line the dispatcher reads"
run "$GEMINI|nodelay" "$ROUTER|ok" "$ZEN|nodelay"
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

# 7. Gemini and OpenRouter both out for the day: OpenCode's keyless free tier takes the prompt and the lane exits 0.
rm -rf "$TMP/quota"
run "$GEMINI|nodelay" "$ROUTER|day"
CASES=$((CASES + 1))
[ "$STATUS" = 0 ] || fail "the keyless Zen model should rescue the run, got $STATUS: $OUT"
[ "$(count "$STUB_CALLS" "$ZEN")" = 1 ] || fail "Zen is called once: $(cat "$STUB_CALLS")"
lacks "$OUT" "free quota exhausted" "not exhausted while Zen answers"

# 8. No Gemini key on this machine (stw-117-ci, 6 Nov 2026: opencode on google/* with no key fails at once with "Unexpected server error", which is
# not a quota line, so the lane died instead of trying the next provider): the unkeyed providers are left out and the keyless Zen model runs.
unset GOOGLE_GENERATIVE_AI_API_KEY OPENROUTER_API_KEY
rm -f "$HOME_T/.config/gemini/api-key" "$HOME_T/.config/openrouter/api-key"; rm -rf "$TMP/quota"
run
CASES=$((CASES + 1))
[ "$STATUS" = 0 ] || fail "with no keys the Zen model should run, got $STATUS: $OUT"
[ "$(count "$STUB_CALLS" "$GEMINI")" = 0 ] || fail "Gemini must not be called without its key: $(cat "$STUB_CALLS")"
[ "$(count "$STUB_CALLS" "$ROUTER")" = 0 ] || fail "OpenRouter must not be called without its key"
[ "$(count "$STUB_CALLS" "$ZEN")" = 1 ] || fail "Zen is called once: $(cat "$STUB_CALLS")"
contains "$OUT" "no key for google on this machine" "says why Gemini was left out"
contains "$OUT" "leaving out $ROUTER" "says why OpenRouter was left out"
# 9. Only OpenRouter has a key: Gemini is left out, OpenRouter runs first.
printf 'placeholder\n' >"$HOME_T/.config/openrouter/api-key"
run
CASES=$((CASES + 1))
[ "$(count "$STUB_CALLS" "$GEMINI")" = 0 ] || fail "Gemini must not be called without its key"
[ "$(count "$STUB_CALLS" "$ROUTER")" = 1 ] && [ "$(count "$STUB_CALLS" "$ZEN")" = 0 ] || fail "OpenRouter answers: $(cat "$STUB_CALLS")"
contains "$OUT" "leaving out $GEMINI" "Gemini is the one left out"
lacks "$OUT" "leaving out $ROUTER" "OpenRouter has its key"
# 10. A model asked for by name on an unkeyed provider is left out too, and the run still works.
rm -f "$HOME_T/.config/openrouter/api-key"; : >"$STUB_CALLS"; : >"$STUB_PLAN"
set +e; OUT="$(bash "$SCRIPT" "$REPO" "$HAND" "$ROUTER" "$PROMPT" 2>&1)"; STATUS=$?; set -e
CASES=$((CASES + 1))
[ "$STATUS" = 0 ] && [ "$(count "$STUB_CALLS" "$ZEN")" = 1 ] || fail "a named unkeyed model falls to Zen ($STATUS): $OUT"

# 11. Secrets (6 Nov 2026: a password from an env file reached the shared OpenCode log): the lane config is valid JSON that denies the read and edit
# tools on env and key files, denies shell commands naming an env file, still allows ordinary files and keeps the old folder rules.
: >"$STUB_ARGS"; run
CASES=$((CASES + 1))
python3 -I - "$HAND/opencode.json" <<'PYEOF' || fail "lane config does not deny env files"
import fnmatch, json, sys
cfg = json.load(open(sys.argv[1]))
perm = cfg["permission"]
def last_match(rules, text):  # OpenCode: the last matching rule wins
    out = None
    for pat, act in rules.items():
        if fnmatch.fnmatchcase(text, pat): out = act
    return out
for tool in ("read", "edit"):
    for denied in (".env", "sub/dir/.env", "/x/y/.env.local", ".env.supabase.local", "ci/.tmp-seed.env", "/h/.config/gemini/api-key", "k.pem"):
        assert last_match(perm[tool], denied) == "deny", (tool, denied)
    for ok in ("README.md", "src/environment.ts", "docs/env-notes.md", "src/index.js"):
        assert last_match(perm[tool], ok) == "allow", (tool, ok)
for denied in ("cat .env", "cat ./.env.local", "echo hi && cat sub/.env", ".env", "source .env.test", "cat /home/u/repos/sandbox/model_a/.env",
               "cd model_a && cat .env", "grep PASS ci/.tmp-seed.env", "printenv", "env", "env | sort", "cat /proc/self/environ", "cat /h/.config/gemini/api-key"):
    assert last_match(perm["bash"], denied) == "deny", denied
for denied in ("git push origin main", "git push -u origin master", "git push origin HEAD:main", "git -C /x/y push origin main", "git push origin HEAD:refs/heads/main",
               "git push --force origin mybranch", "git push origin +mybranch", "git push --mirror", "git push --all origin", "echo $OPENROUTER_API_KEY", "git push -f origin mybranch", "git push origin main --no-verify", "python3 ~/.claude/scripts/install_scripts.py",
               "python3 -I <(git show origin/main:global/.claude/scripts/install_scripts.py)"):
    assert last_match(perm["bash"], denied) == "deny", denied
for denied in ("git config --global user.email x", "git config user.name y", "git -C /x config --global user.email z", "sed -i s/a/b/ ~/.gitconfig",
               "git -c user.email=a commit -m x", "echo x >> .git/config", "cp h .git/hooks/pre-commit", "GIT_CONFIG_GLOBAL=/x git commit -m x"):
    assert last_match(perm["bash"], denied) == "deny", denied
for ok in ("git config --get user.email", "git config --list", "git commit -m x", "git log", "git diff --stat"):
    assert last_match(perm["bash"], ok) == "allow", ok
for denied in ("/home/u/.gitconfig", "/x/repo/.git/config", "/x/repo/.git/hooks/pre-commit"):
    assert last_match(perm["edit"], denied) == "deny", ("edit", denied)
for ok in ("git push -u origin skip-census", "git push origin fix-main-menu", "git push origin maintenance", "git push", "git pull origin main", "git fetch origin main", "gh pr create --fill",
           "npm test", "git status", "ls docs", "grep -rn environment src", "cat docs/environment.md"):
    assert last_match(perm["bash"], ok) == "allow", ok
assert perm["external_directory"]["*"] == "deny" and perm["*"] == "allow"
PYEOF

# 12. The run asks OpenCode for its quietest log: at INFO it writes every permission check with the whole command text the model typed.
grep -qF -- '--log-level ERROR' "$STUB_ARGS" || fail "opencode must run with --log-level ERROR: $(cat "$STUB_ARGS")"

# 13. The prompt the model gets starts with the rule never to open env files or key files, then the lane's own prompt.
grep -qF 'never open, print, copy or type into a command the contents of an environment file' "$STUB_ARGS" || fail "the no-env-file rule is missing from the prompt"
grep -qF 'Never push to main or master, never force-push, and never run install_scripts.py' "$STUB_ARGS" || fail "the no-push-to-main rule is missing from the prompt"
grep -qF 'do the thing' "$STUB_ARGS" || fail "the lane's own prompt must still follow the rule"
CASES=$((CASES + 1))

# 14. After the run the shared OpenCode log is scrubbed (best effort): a fake scrubber is called on that folder; with no scrubber the run is unharmed.
mkdir -p "$HOME_T/.claude/scripts" "$HOME_T/.local/share/opencode/log"
cat >"$HOME_T/.claude/scripts/scrub_known_secrets.py" <<'PYEOF'
import sys
open(sys.argv[0] + ".called", "w").write(" ".join(sys.argv[1:]))
PYEOF
run
CASES=$((CASES + 1))
[ "$STATUS" = 0 ] || fail "run with a scrubber should exit 0, got $STATUS: $OUT"
[ "$(cat "$HOME_T/.claude/scripts/scrub_known_secrets.py.called" 2>/dev/null)" = "$HOME_T/.local/share/opencode/log" ] || fail "the shared log folder should be scrubbed after the run"
rm -f "$HOME_T/.claude/scripts/scrub_known_secrets.py"
run
[ "$STATUS" = 0 ] || fail "a missing scrubber must not fail the run, got $STATUS: $OUT"
CASES=$((CASES + 1))

echo "ok: $CASES cases"
