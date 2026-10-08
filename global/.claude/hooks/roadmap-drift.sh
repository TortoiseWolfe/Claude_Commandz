#!/usr/bin/env bash
#
# roadmap-drift.sh — report when a rolling "session-prime" roadmap issue has
# fallen behind the repo it describes.
#
# WHY THIS EXISTS
#
# TortoiseWolfe/ScriptHammer#115's body claims to be "always-current". It
# drifted 68 commits without anyone noticing, because the claim was never
# checked. This measures the claim.
#
# THE ONE RULE THIS SCRIPT MUST NOT BREAK
#
# "No drift" and "the check could not run" must never look the same. The naive
# version of this reports a comforting zero whenever the network is down:
#
#     git log --since='' | wc -l   ->  0, rc=0
#
# A staleness detector that fails silently is the exact defect it exists to
# catch. Every failure path here reports UNKNOWN, never 0.
#
# Modes:  session-start | stop | check | seed-cache
# Design: ~/.claude/plans/jiggly-sleeping-crab.md
#
# NOT `set -e`: a hook that dies mid-way emits nothing, which reads as "all
# clear". Failures are handled explicitly and reported.
set -uo pipefail

MODE="${1:-check}"

RD_GH_TIMEOUT="${RD_GH_TIMEOUT:-6}"
RD_MIN_DAYS="${RD_MIN_DAYS:-10}"
RD_LOUD_AT="${RD_LOUD_AT:-60}"
ISSUE_LABEL="next-session"
CACHE_DIR="$HOME/.claude/roadmap-drift/cache"
STATE_DIR="${XDG_RUNTIME_DIR:-/tmp}/claude-roadmap-drift"

TTL_FOUND=$((6 * 3600))
TTL_NONE=$((72 * 3600))
TTL_ERROR=$((15 * 60))
FETCH_STALE_AFTER=$((12 * 3600))

is_check() { [ "$MODE" = "check" ]; }
say() { is_check && printf '%s\n' "$*"; }

# ---------------------------------------------------------------- json emit --
# additionalContext MUST nest inside hookSpecificOutput. At the top level
# Claude Code silently ignores it — which would make this whole script a no-op
# that looks like it works.
emit_context() {
  local event="$1" text="$2"
  if is_check; then printf '%s\n' "$text"; return; fi
  EVENT="$event" TEXT="$text" python3 -c '
import json, os
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": os.environ["EVENT"],
    "additionalContext": os.environ["TEXT"],
}}))'
}

# Failures go to the USER, not into the model'\''s context — a broken check is
# not something the model should try to act on mid-task.
emit_failure() {
  local text="roadmap-drift: $1"
  if is_check; then printf '%s\n' "$text"; return; fi
  TEXT="$text" python3 -c '
import json, os
print(json.dumps({"systemMessage": os.environ["TEXT"]}))'
}

quiet_exit() { exit 0; }

# ------------------------------------------------------------ repo identity --
# Offline. `gh repo view` would be a network call on every session start in
# every repo; the remote URL is already on disk.
repo_slug() {
  local url
  url="$(git remote get-url origin 2>/dev/null)" || return 1
  [ -n "$url" ] || return 1
  url="${url%.git}"
  case "$url" in
    git@*:*)        printf '%s\n' "${url#*:}" ;;
    ssh://git@*/*)  printf '%s\n' "${url#ssh://git@*/}" ;;
    https://*/*/*)  printf '%s\n' "$(printf '%s' "$url" | sed -E 's#https://[^/]+/##')" ;;
    *) return 1 ;;
  esac
}

now() { date +%s; }

cache_file() { printf '%s/%s__%s.json\n' "$CACHE_DIR" "$1" "$2"; }

cache_read() {  # $1=file $2=key
  [ -f "$1" ] || return 1
  KEY="$2" python3 -c '
import json, os, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
v = d.get(os.environ["KEY"])
if v is None: sys.exit(1)
print(v)' "$1" 2>/dev/null
}

cache_write() {  # $1=file $2=status $3=issue $4=stamp_sha $5=branch $6=generated
  mkdir -p "$(dirname "$1")" 2>/dev/null || return 1
  F="$1" ST="$2" ISS="${3:-}" SHA="${4:-}" BR="${5:-}" GEN="${6:-}" TS="$(now)" python3 -c '
import json, os
json.dump({
  "status": os.environ["ST"], "issue": os.environ["ISS"],
  "stamp_sha": os.environ["SHA"], "branch": os.environ["BR"],
  "generated": os.environ["GEN"], "checked_at": int(os.environ["TS"]),
}, open(os.environ["F"], "w"))'
}

# ------------------------------------------------------------ the gh lookup --
# Every gh call is timeout-wrapped. Unwrapped, a TCP blackhole (captive portal,
# half-up VPN) hangs 30s on gh's own dial timeout — measured.
gh_find_issue() {  # $1=owner/repo ; echoes "number<TAB>body" ; rc!=0 on failure
  local out
  out="$(timeout "$RD_GH_TIMEOUT" gh issue list --repo "$1" --state open \
          --label "$ISSUE_LABEL" --limit 1 --json number,body 2>/dev/null)"
  local rc=$?
  [ $rc -ne 0 ] && return $rc
  [ -z "$out" ] && return 1
  printf '%s' "$out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if not d: print("NONE"); sys.exit(0)
print("%s\t%s" % (d[0]["number"], d[0].get("body", "").replace("\n", "\\n")))'
}

gh_last_edited() {  # $1=owner/repo $2=number — the no-stamp fallback
  local owner="${1%%/*}" name="${1##*/}"
  timeout "$RD_GH_TIMEOUT" gh api graphql -f query="
    {repository(owner:\"$owner\",name:\"$name\"){issue(number:$2){lastEditedAt createdAt}}}" \
    --jq '.data.repository.issue | (.lastEditedAt // .createdAt)' 2>/dev/null
}

parse_stamp() {  # stdin: body ; echoes "sha<TAB>branch<TAB>generated"
  python3 -c '
import re, sys
m = None
# lines split on the encoded "\\n" from gh_find_issue, or on a real newline: the stamp is its own line
for line in re.split(r"\\n|\n", sys.stdin.read()):
    if "roadmap-stamp" in line:
        m = line
if not m: sys.exit(1)
g = dict(re.findall(r"(\w+)=([^\s>]+)", m))
if "sha" not in g: sys.exit(1)
print("%s\t%s\t%s" % (g.get("sha",""), g.get("branch","main"), g.get("generated","")))'
}

# ------------------------------------------------------------------- drift ---
# --first-parent is not cosmetic. Over the same real 68-commit range:
#   plain rev-list -> 109 ; --no-merges -> 89 ; --first-parent -> 68
# Only the last matches what a human counts when PRs are squash-merged. A hook
# that cries "109 behind" when the answer is 68 gets ignored within a week.
count_drift() {  # $1=base_sha $2=branch ; echoes count ; rc=2 if sha unknown
  git cat-file -e "${1}^{commit}" 2>/dev/null || return 2
  local tip="refs/remotes/origin/$2"
  git show-ref --verify --quiet "$tip" || tip="HEAD"
  # Prefer the remote ref, but if local HEAD is ahead of it, measure to HEAD —
  # otherwise a /session-prime run with unpushed work reports drift against
  # itself the moment it finishes.
  if [ "$tip" != "HEAD" ] && git merge-base --is-ancestor "$tip" HEAD 2>/dev/null; then
    tip="HEAD"
  fi
  git rev-list --count --first-parent "${1}..${tip}" 2>/dev/null || return 2
}

fetch_age_note() {
  local f="$(git rev-parse --git-dir 2>/dev/null)/FETCH_HEAD"
  [ -f "$f" ] || return 0
  local age=$(( $(now) - $(stat -c %Y "$f" 2>/dev/null || echo 0) ))
  [ "$age" -lt "$FETCH_STALE_AFTER" ] && return 0
  printf ' (origin was last fetched %dh ago, so this is a floor)' $((age / 3600))
}

min_commits() {
  local v
  v="$(git config --get roadmap.drift.minCommits 2>/dev/null)"
  printf '%s\n' "${RD_MIN_COMMITS:-${v:-25}}"
}

# =============================================================== seed-cache ==
if [ "$MODE" = "seed-cache" ]; then
  # Called by /session-prime after it writes the stamp, so the next session
  # start is exact and needs no network — and so a freshly-seeded roadmap is
  # never hidden behind the 72h negative cache.
  slug="${2:-}"; issue="${3:-}"; sha="${4:-}"; branch="${5:-main}"; gen="${6:-}"
  [ -z "$slug" ] && { echo "usage: $0 seed-cache <owner/repo> <issue> <sha> <branch> <generated>" >&2; exit 64; }
  f="$(cache_file "${slug%%/*}" "${slug##*/}")"
  cache_write "$f" found "$issue" "$sha" "$branch" "$gen" && touch "${f}.everfound"
  echo "roadmap-drift: cached ${slug}#${issue} at ${sha:0:8}"
  exit 0
fi

# ===================================================== hook / check entrypt ==
if [ "${ROADMAP_DRIFT:-}" = "off" ] && ! is_check; then quiet_exit; fi
is_check && [ "${ROADMAP_DRIFT:-}" = "off" ] && say "(note: ROADMAP_DRIFT=off — hooks are silenced; check still runs)"

# Only the hook modes are given JSON on stdin. A bare `cat` here hangs forever
# when stdin is an inherited open pipe (measured: it blocked a `check` run for
# 3 minutes) — and in a real hook that would stall session start. Bounded, and
# only where it is actually needed.
STDIN_JSON=""
case "$MODE" in
  session-start|stop)
    if [ ! -t 0 ]; then STDIN_JSON="$(timeout 2 cat 2>/dev/null || true)"; fi
    ;;
esac

json_field() {
  [ -z "$STDIN_JSON" ] && return 1
  K="$1" python3 -c '
import json, os, sys
try: d = json.load(sys.stdin)
except Exception: sys.exit(1)
v = d.get(os.environ["K"])
if v is None: sys.exit(1)
# json.dumps for scalars so a bool prints "true"/"false" rather than Python
# "True"/"False". The shell compares against lowercase, so without this the
# stop_hook_active guard silently never matched — measured.
print(json.dumps(v) if isinstance(v, bool) else v)' <<<"$STDIN_JSON" 2>/dev/null
}

git rev-parse --show-toplevel >/dev/null 2>&1 || { say "not a git repo"; quiet_exit; }

# Record the session's starting HEAD *now*, before any threshold gate can exit.
# Seeding this lazily from the Stop hook was wrong: if drift starts below
# threshold and commits push it over mid-session, the first Stop to get through
# would record HEAD as it is *then* — measuring zero session commits and
# nudging a turn late against the wrong baseline.
if [ "$MODE" = "session-start" ]; then
  _sid="$(json_field session_id || echo '')"
  if [ -n "$_sid" ]; then
    _sid="$(printf '%s' "$_sid" | tr -c 'A-Za-z0-9._-' '_')"
    mkdir -p "$STATE_DIR" 2>/dev/null && chmod 700 "$STATE_DIR" 2>/dev/null
    _sf="$STATE_DIR/${_sid}.state"
    [ -f "$_sf" ] || printf 'head_at_start=%s\nnudged=0\n' "$(git rev-parse HEAD 2>/dev/null)" > "$_sf"
  fi
fi

SLUG="$(repo_slug)" || { say "no GitHub origin remote"; quiet_exit; }
OWNER="${SLUG%%/*}"; NAME="${SLUG##*/}"
CF="$(cache_file "$OWNER" "$NAME")"
EVERFOUND=0; [ -f "${CF}.everfound" ] && EVERFOUND=1

STATUS="$(cache_read "$CF" status || echo '')"
CHECKED="$(cache_read "$CF" checked_at || echo 0)"
AGE=$(( $(now) - ${CHECKED:-0} ))

need_refresh=1
case "$STATUS" in
  found) [ "$AGE" -lt "$TTL_FOUND" ] && need_refresh=0 ;;
  none)  [ "$AGE" -lt "$TTL_NONE"  ] && need_refresh=0 ;;
  error) [ "$AGE" -lt "$TTL_ERROR" ] && need_refresh=0 ;;
esac
[ -n "${RD_FORCE_REFRESH:-}" ] && need_refresh=1
is_check && need_refresh=1

# A cached 'none' inside its window: this repo has no roadmap. Say nothing.
if [ "$STATUS" = "none" ] && [ "$need_refresh" -eq 0 ]; then
  say "$SLUG: no $ISSUE_LABEL issue (cached)"; quiet_exit
fi

ISSUE="$(cache_read "$CF" issue || echo '')"
STAMP_SHA="$(cache_read "$CF" stamp_sha || echo '')"
BRANCH="$(cache_read "$CF" branch || echo 'main')"
GENERATED="$(cache_read "$CF" generated || echo '')"
LOOKUP_ERR=""

if [ "$need_refresh" -eq 1 ]; then
  if RES="$(gh_find_issue "$SLUG")"; then
    if [ "$RES" = "NONE" ]; then
      cache_write "$CF" none
      say "$SLUG: no $ISSUE_LABEL issue"
      quiet_exit
    fi
    ISSUE="${RES%%$'\t'*}"; BODY="${RES#*$'\t'}"
    # The body as gh_find_issue encoded it, never through printf '%b': that decodes every backslash in
    # it, so a Windows path (C:\Users\...) printed "missing unicode digit for \U", and the decoded
    # newlines left parse_stamp one line, where a "sha=" after the stamp won (2026-10-08, measured).
    if STAMP="$(printf '%s' "$BODY" | parse_stamp)"; then
      STAMP_SHA="${STAMP%%$'\t'*}"; rest="${STAMP#*$'\t'}"
      BRANCH="${rest%%$'\t'*}"; GENERATED="${rest#*$'\t'}"
    else
      STAMP_SHA=""; GENERATED=""
    fi
    cache_write "$CF" found "$ISSUE" "$STAMP_SHA" "$BRANCH" "$GENERATED"
    touch "${CF}.everfound"; EVERFOUND=1
  else
    rc=$?
    LOOKUP_ERR="gh failed (rc=$rc)"
    [ "$rc" -eq 124 ] && LOOKUP_ERR="gh timed out after ${RD_GH_TIMEOUT}s (network blackhole?)"
    [ "$rc" -eq 4 ] && LOOKUP_ERR="gh is not authenticated (run: gh auth login)"
    cache_write "$CF" error "$ISSUE" "$STAMP_SHA" "$BRANCH" "$GENERATED"
  fi
fi

# A lookup failure in a repo never known to have a roadmap is indistinguishable
# from "this repo has none" — and the global contract says be quiet there.
if [ -n "$LOOKUP_ERR" ] && [ -z "$STAMP_SHA" ]; then
  if [ "$EVERFOUND" -eq 1 ] || is_check; then
    emit_failure "could not check $SLUG — $LOOKUP_ERR. Drift is UNKNOWN, not zero. Debug: bash ~/.claude/hooks/roadmap-drift.sh check"
    exit 0
  fi
  quiet_exit
fi

[ -z "$ISSUE" ] && { say "$SLUG: no roadmap issue resolved"; quiet_exit; }

# ------------------------------------------------------------ measure drift --
[ -n "${RD_FAKE_STAMP_SHA:-}" ] && { STAMP_SHA="$RD_FAKE_STAMP_SHA"; GENERATED="${GENERATED:-unknown}"; }
EXACT=1
if [ -z "$STAMP_SHA" ]; then
  EXACT=0
  LAST_EDITED="${RD_FAKE_LAST_EDITED:-$(gh_last_edited "$SLUG" "$ISSUE")}"
  if [ -z "$LAST_EDITED" ]; then
    emit_failure "could not check ${SLUG}#${ISSUE} — no stamp in the body and the last-edit lookup failed. Drift is UNKNOWN, not zero. Debug: bash ~/.claude/hooks/roadmap-drift.sh check"
    exit 0
  fi
  DRIFT="$(git rev-list --count --first-parent "origin/${BRANCH}" --since="$LAST_EDITED" 2>/dev/null)"
  if [ -z "$DRIFT" ]; then
    emit_failure "could not count commits since $LAST_EDITED in $SLUG. Drift is UNKNOWN, not zero."
    exit 0
  fi
else
  DRIFT="$(count_drift "$STAMP_SHA" "$BRANCH")"
  rc=$?
  if [ "$rc" -eq 2 ] || [ -z "$DRIFT" ]; then
    emit_failure "could not check ${SLUG}#${ISSUE} — stamped commit ${STAMP_SHA:0:8} is not in this clone (shallow clone, or a branch never fetched) — try: git fetch origin. Drift is UNKNOWN, not zero."
    exit 0
  fi
fi

NOTE="$(fetch_age_note)"
MINC="$(min_commits)"
REF="origin/${BRANCH}"
# " on <date>" only when we actually have one — never " on unknown".
WHEN=""
case "${GENERATED:-}" in "" |unknown) ;; *) WHEN=" on ${GENERATED%%T*}" ;; esac

if is_check; then
  if [ "$EXACT" -eq 1 ]; then
    echo "${SLUG}#${ISSUE}: drift ${DRIFT} commits (exact, from ${STAMP_SHA:0:8}${WHEN:+ generated${WHEN# on}}) vs ${REF}${NOTE}"
  else
    echo "${SLUG}#${ISSUE}: drift ${DRIFT} commits (approximate — no stamp; measured from last body edit ${LAST_EDITED}) vs ${REF}${NOTE}"
  fi
  echo "threshold: ${MINC} commits or ${RD_MIN_DAYS} days; escalate at ${RD_LOUD_AT}"
  exit 0
fi

# --------------------------------------------------------------- threshold --
OVER=0
[ "$DRIFT" -ge "$MINC" ] && OVER=1
if [ "$OVER" -eq 0 ] && [ "$DRIFT" -gt 0 ] && [ -n "$GENERATED" ] && [ "$GENERATED" != "unknown" ]; then
  GEN_EPOCH="$(date -d "$GENERATED" +%s 2>/dev/null || echo 0)"
  if [ "$GEN_EPOCH" -gt 0 ]; then
    DAYS=$(( ( $(now) - GEN_EPOCH ) / 86400 ))
    [ "$DAYS" -ge "$RD_MIN_DAYS" ] && OVER=1
  fi
fi
[ "$OVER" -eq 0 ] && quiet_exit

# ============================================================ stop-hook mode ==
if [ "$MODE" = "stop" ]; then
  [ "$(json_field stop_hook_active || echo false)" = "true" ] && quiet_exit
  SID="$(json_field session_id || echo '')"
  [ -z "$SID" ] && quiet_exit
  SID="$(printf '%s' "$SID" | tr -c 'A-Za-z0-9._-' '_')"
  mkdir -p "$STATE_DIR" 2>/dev/null && chmod 700 "$STATE_DIR" 2>/dev/null
  SF="$STATE_DIR/${SID}.state"
  if [ ! -f "$SF" ]; then
    printf 'head_at_start=%s\nnudged=0\n' "$(git rev-parse HEAD 2>/dev/null)" > "$SF"
    quiet_exit   # nothing has landed yet this session
  fi
  # shellcheck disable=SC1090
  . "$SF" 2>/dev/null || quiet_exit
  [ "${nudged:-0}" = "1" ] && quiet_exit
  SESSION_COMMITS="$(git rev-list --count --first-parent "${head_at_start}..HEAD" 2>/dev/null || echo 0)"
  [ "${SESSION_COMMITS:-0}" -lt 1 ] && quiet_exit
  # Written BEFORE the emit so a crash cannot repeat the nudge.
  printf 'head_at_start=%s\nnudged=1\n' "$head_at_start" > "$SF"
  emit_context Stop "This session added ${SESSION_COMMITS} commit(s). The rolling roadmap (${SLUG}#${ISSUE}) was generated ${DRIFT} commits ago and does not reflect them. If you are wrapping up, offer to run /session-prime — it rewrites the issue body and appends the audit comment. If work is continuing, ignore this."
  exit 0
fi

# ======================================================== session-start mode ==
if [ "$EXACT" -eq 0 ]; then
  emit_context SessionStart "Roadmap drift (approximate): ${SLUG}#${ISSUE} has no generation stamp in its body, so this is measured from the issue's last body edit (${LAST_EDITED}): ${DRIFT} commits have landed on ${REF} since${NOTE}. Approximate because any edit to the body resets the baseline. The next /session-prime run adds a stamp and makes this exact. Not urgent — do not stop to fix this now."
elif [ "$DRIFT" -ge "$RD_LOUD_AT" ]; then
  emit_context SessionStart "Roadmap drift: ${SLUG}#${ISSUE} (the rolling session-prime roadmap) was generated from commit ${STAMP_SHA:0:8}${WHEN}; ${DRIFT} commits have landed on ${REF} since${NOTE}. The body is materially out of date — its \"active arc\" and \"next 3 sessions\" predate that work. Read it for intent, not for current state, and confirm the plan with the user before following it. /session-prime rewrites the body and appends the audit comment."
else
  emit_context SessionStart "Roadmap drift: ${SLUG}#${ISSUE} (the rolling session-prime roadmap) was generated from commit ${STAMP_SHA:0:8}${WHEN}; ${DRIFT} commits have landed on ${REF} since${NOTE}. Its body describes an older state of the repo — use it for intent, and check anything you rely on against git. Running /session-prime at the end of this session resets it. Not urgent — do not stop to fix this now."
fi
exit 0
