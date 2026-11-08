#!/usr/bin/env bash
# OpenCode keeps every message and tool result of every session in one SQLite database (~/.local/share/opencode/opencode.db). A password a command
# printed stays in it, readable by the next lane, until something scrubs it (7 Nov 2026: a scan found 5 real values in that database). lane_hide_secrets.sh
# now runs scrub_sqlite_secrets.py on it after each run.
#
# This runs lane_hide_secrets.sh with a stand-in command that does what an engine does: it writes a row holding a secret value into the database.
# The test fails if that value is still in the database file (or its log) afterwards, if the database is damaged, or if a row with no secret changed.
# It also checks that a second process holding the database open stops the scrub (the database is left alone and the lane's exit code still passes
# through). It has no skip path: a machine without user namespaces fails here.
# Every secret is a made-up fixture built at run time; nothing on this machine is read.
#
# To watch it go red, run it beside a lane_hide_secrets.sh from before this change (it fails with "a secret value is still in the database"):
#   d=$(mktemp -d); mkdir $d/tests; cp this_file $d/tests/
#   for f in lane_hide_secrets.sh lane_secret_paths.py known_secrets_scan.py scrub_known_secrets.py scrub_sqlite_secrets.py; do git show <commit before this change>:global/.claude/scripts/$f >$d/$f; done
#   chmod +x $d/lane_hide_secrets.sh; bash $d/tests/test_lane_hide_opencode_db.sh
set -euo pipefail
unset LANE_SECRET_ENV LANE_HIDE_EXTRA_ROOTS LANE_HIDE_REPOS LANE_SCAN_REPOS LANE_TOOL_OUTPUT_DIR LANE_OPENCODE_DB

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane_hide_secrets.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-hide-opencode-db.XXXXXX")"
HOLDER_PID=""
trap '[ -n "$HOLDER_PID" ] && kill "$HOLDER_PID" 2>/dev/null; rm -rf "$TMP"' EXIT

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }

unshare -Urm true 2>/dev/null || fail 'this machine cannot make a user and mount namespace (unshare -Urm); on Ubuntu 24.04 run: sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0'
command -v setpriv >/dev/null || fail 'setpriv is not installed'

HOME_TEST="$TMP/home"
REPO="$TMP/project"
DB="$TMP/opencode.db"
HAND="$TMP/hand"
PROMPT="$TMP/prompt.md"
mkdir -p "$HOME_TEST/repos/app" "$HOME_TEST/.config/afa/secrets" "$REPO" "$HAND"
printf 'Review this safely.\n' >"$PROMPT"

fixture_value() { printf 'opencode-db-fixture-%s' "$1" | sha256sum | cut -c1-32; }
V_KNOWN="$(fixture_value known)"
printf 'API_TOKEN=%s\n' "$V_KNOWN" >"$HOME_TEST/repos/app/.env"

# The database starts with one clean row, the way a real one holds earlier sessions.
python3 -I - "$DB" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("PRAGMA journal_mode=WAL")
con.execute("CREATE TABLE part (id TEXT PRIMARY KEY, data TEXT)")
con.execute("INSERT INTO part VALUES ('p0', 'build finished, 12 tests passed')")
con.commit()
con.close()
PY

# The stand-in engine: writes a tool result that holds the value, and one that does not.
cat >"$TMP/engine.py" <<'STUB'
import os, sqlite3
con = sqlite3.connect(os.environ["DB"])
con.execute("INSERT INTO part VALUES ('p1', ?)", ("gh run log: TEST_USER_PASSWORD value is %s done" % os.environ["LEARNED"],))
con.execute("INSERT INTO part VALUES ('p2', 'second clean row')")
con.commit()
con.close()
STUB

run() {  # run <exit code the command should return>
  HOME="$HOME_TEST" LANE_SCAN_REPOS="$HOME_TEST/repos" LANE_HIDE_REPOS="$HOME_TEST/repos" KNOWN_SECRETS_DIR="$HOME_TEST/.config/afa/secrets" \
    LANE_OPENCODE_DB="$DB" LANE_TOOL_OUTPUT_DIR="$TMP/none" DB="$DB" LEARNED="$V_KNOWN" \
    bash "$SCRIPT" "$REPO" "$HAND" "$PROMPT" -- bash -c "python3 -I '$TMP/engine.py'; exit $1" >"$TMP/run.out" 2>&1
}
files_have_value() {  # a missing -wal file must not hide a match (a pipe with cat would fail under pipefail and read as "no value")
  local f
  for f in "$DB" "$DB-wal"; do
    if [ -f "$f" ] && grep -aqF -- "$V_KNOWN" "$f"; then return 0; fi
  done
  return 1
}
rows() { python3 -I - "$DB" "$1" <<'PY'
import sqlite3, sys
con = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
print(con.execute(sys.argv[2]).fetchall())
PY
}

# 1. A normal run: the value is gone, the rest is intact, and the command's own exit code (7) passes through.
rc=0; run 7 || rc=$?
[ "$rc" = 7 ] || fail "the lane's exit code did not pass through (got $rc): $(cat "$TMP/run.out")"
ok
if files_have_value; then fail 'a secret value is still in the database or its log'; fi
ok
[ "$(rows 'PRAGMA integrity_check')" = "[('ok',)]" ] || fail 'the database is damaged after the scrub'
ok
[ "$(rows "SELECT count(*) FROM part")" = "[(3,)]" ] || fail 'a row was lost'
ok
rows "SELECT data FROM part WHERE id = 'p1'" | grep -qF 'TEST_USER_PASSWORD value is <redacted:' || fail 'the scrubbed row does not keep its surrounding text'
ok
[ "$(rows "SELECT data FROM part WHERE id = 'p0'")" = "[('build finished, 12 tests passed',)]" ] || fail 'a row with no secret was changed'
ok
if grep -qF -- "$V_KNOWN" "$TMP/run.out"; then fail 'the run printed a secret value'; fi
ok
if ls "$TMP" | grep -q 'pre-scrub'; then fail 'the backup (it holds the old values) was left behind after a clean rescan'; fi
ok

# 2. Another process holds the database open: the scrub must wait (exit 3 inside), leave the file alone and end no process.
python3 -I - "$DB" "$V_KNOWN" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("INSERT INTO part VALUES ('p3', ?)", ("again %s" % sys.argv[2],))
con.commit()
con.close()
PY
python3 -I -c "
import sqlite3, sys, time
c = sqlite3.connect(sys.argv[1]); c.execute('select count(*) from part').fetchall()
open(sys.argv[2], 'w').write('ready')
time.sleep(120)" "$DB" "$TMP/holder.ready" &
HOLDER_PID=$!
for _ in $(seq 1 100); do [ -f "$TMP/holder.ready" ] && break; sleep 0.1; done
[ -f "$TMP/holder.ready" ] || fail 'the stand-in holder did not start'
before="$({ cat "$DB" "$DB-wal" 2>/dev/null || true; } | sha256sum)"
HOME="$HOME_TEST" LANE_SCAN_REPOS="$HOME_TEST/repos" LANE_HIDE_REPOS="$HOME_TEST/repos" KNOWN_SECRETS_DIR="$HOME_TEST/.config/afa/secrets" \
  LANE_OPENCODE_DB="$DB" LANE_TOOL_OUTPUT_DIR="$TMP/none" \
  bash "$SCRIPT" "$REPO" "$HAND" "$PROMPT" -- bash -c 'exit 0' >"$TMP/run2.out" 2>&1 || fail "a held database stopped the lane: $(cat "$TMP/run2.out")"
ok
grep -qF 'another OpenCode process holds' "$TMP/run2.out" || fail "a held database was not reported: $(cat "$TMP/run2.out")"
ok
[ "$({ cat "$DB" "$DB-wal" 2>/dev/null || true; } | sha256sum)" = "$before" ] || fail 'the database changed while another process held it'
ok
kill -0 "$HOLDER_PID" 2>/dev/null || fail 'the scrub ended the process that held the database'
ok
if grep -qF -- "$V_KNOWN" "$TMP/run2.out"; then fail 'the held-database run printed a secret value'; fi
ok

echo "All $checks checks passed"
