#!/usr/bin/env bash
# Call an HTTP API without its response body ever reaching stdout.
#
# WHY THIS EXISTS. On 2026-09-18 four separate secret leaks happened in one
# session, and every one had the same shape: a response body I had not thought
# of as secret-bearing, echoed to the terminal.
#
#   1. a Portainer stack GET written to /tmp with the umask default (0644),
#      carrying 41 variables including DB, S3, SMTP and JWT secrets
#   2. `occ user:auth-tokens:add` output, where the parse failed and the error
#      path printed the raw text containing the token
#   3. a Portainer stack-create response, which echoes the whole env back
#   4. the same again on the retry
#
# The rule "never print .value" kept being written down and kept being too
# narrow, because each new response shape was a new special case. So the rule is
# inverted here: NOTHING is printed unless a field is named explicitly.
#
# Usage:
#   api-quiet.sh <jq-ish field> [field...] -- curl-args...
#
#   api-quiet.sh Id Name -- -X POST "$URL/api/stacks/..." -H "..." --data-binary @payload
#
# Fields are top-level JSON keys, or dotted paths (GitConfig.ReferenceName).
# Anything not named never leaves the temp file, which is 0600 and shredded.
set -uo pipefail

FIELDS=()
while [ $# -gt 0 ]; do
  [ "$1" = "--" ] && { shift; break; }
  FIELDS+=("$1"); shift
done

if [ ${#FIELDS[@]} -eq 0 ]; then
  echo "api-quiet.sh: refusing to run with no fields named." >&2
  echo "              Name what you want printed; everything else stays hidden." >&2
  exit 2
fi

TMP=$(mktemp); chmod 600 "$TMP"
trap 'shred -u "$TMP" 2>/dev/null || rm -f "$TMP"' EXIT

CODE=$(curl -sS -o "$TMP" -w '%{http_code}' "$@")
echo "HTTP $CODE"

# A denylist is not enough on its own, but naming these is never legitimate and
# on 2026-09-18 naming `Env` dumped a live stack's entire credential set.
for f in "${FIELDS[@]}"; do
  case "$(printf '%s' "$f" | tr 'A-Z' 'a-z')" in
    env|environment|*secret*|*password*|*passwd*|*token*|*key*|*credential*|*.value|value)
      echo "api-quiet.sh: refusing to print field '$f'." >&2
      echo "              Fields like this carry credentials. If you need the" >&2
      echo "              value, use it from a variable; do not display it." >&2
      exit 3;;
  esac
done

FIELDS_JOINED=$(printf '%s\n' "${FIELDS[@]}")
FIELDS="$FIELDS_JOINED" python3 - "$TMP" <<'PY'
import json, os, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    # NEVER echo the raw body on a parse failure - that is exactly how leak #2
    # happened. Report shape only.
    n = os.path.getsize(sys.argv[1])
    print("  (response is not JSON; %d bytes, withheld)" % n)
    sys.exit(0)
for path in os.environ["FIELDS"].splitlines():
    if not path.strip():
        continue
    cur = d
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            break
    if isinstance(cur, (dict, list)):
        print("  %-28s (structure withheld: %d entries)" % (path, len(cur)))
        continue
    t = str(cur)
    # a long opaque token-looking string is withheld even if the key looked safe
    if len(t) >= 24 and sum(c.isalnum() for c in t) / max(len(t), 1) > 0.9 and any(c.isdigit() for c in t):
        print("  %-28s (withheld: %d chars, looks like a credential)" % (path, len(t)))
        continue
    print("  %-28s %s" % (path, cur if cur is not None else "(absent)"))
PY
