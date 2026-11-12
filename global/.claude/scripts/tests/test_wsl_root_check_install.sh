#!/usr/bin/env bash
# Tests for windows/install-wsl-root-check.sh: the signed-in user is filled into the LogonTrigger (a trigger without one needs administrator
# rights to register), no password is ever written, the task runs wsl.exe as root, and a second run does not stack users.
# whoami.exe and schtasks.exe are fakes.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
SRC="$(cd "$(dirname "$0")/.." && pwd -P)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/rootcheck-install.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }

printf '#!/usr/bin/env bash\nprintf "ncc-test\\\\someone\\r\\n"\n' >"$TMP/whoami"
printf '#!/usr/bin/env bash\necho "$@" >"%s/schtasks.args"\nwhile [ $# -gt 0 ]; do [ "$1" = /xml ] && iconv -f UTF-16 -t UTF-8 "$2" >"%s/registered.xml"; shift; done\n' "$TMP" "$TMP" >"$TMP/schtasks"
printf '#!/usr/bin/env bash\n[ "$1" = -w ] && echo "$2"\n' >"$TMP/wslpath"
printf '#!/usr/bin/env bash\necho "$@" >>"%s/wsl.args"\n' "$TMP" >"$TMP/wslexe"
chmod +x "$TMP"/whoami "$TMP"/schtasks "$TMP"/wslpath "$TMP"/wslexe
run() { WHOAMI="$TMP/whoami" SCHTASKS="$TMP/schtasks" WSLPATH="$TMP/wslpath" WSLEXE="$TMP/wslexe" bash "$SRC/windows/install-wsl-root-check.sh" "$@"; }

# 1. --print shows the user inside the LogonTrigger and registers nothing.
out="$(run --print)"
printf '%s' "$out" | python3 -c '
import re, sys
s = sys.stdin.read()
m = re.search(r"<LogonTrigger>(.*?)</LogonTrigger>", s, re.S)
assert m and "<UserId>ncc-test\\someone</UserId>" in m.group(1), "UserId not inside the LogonTrigger"
assert s.count("<UserId>") == 1, "exactly one UserId"
' || fail "--print puts the signed-in user inside the LogonTrigger"; ok
printf '%s' "$out" | sed 's/UTF-16/UTF-8/' | python3 -c 'import sys, xml.dom.minidom as m; m.parseString(sys.stdin.read().encode())' || fail "the filled-in task file is well-formed XML"; ok
[ ! -e "$TMP/schtasks.args" ] || fail "--print registers nothing"; ok
[ ! -e "$TMP/wsl.args" ] || fail "--print copies nothing into the root-owned folder"; ok

# 2. The real run hands schtasks a UTF-16 copy, replaces an existing task (/f), and the file has no password.
run >/dev/null
grep -q '/create /tn \\AFA\\WslRootCheck /xml' "$TMP/schtasks.args" && grep -q ' /f$' "$TMP/schtasks.args" || fail "schtasks gets /create, the task name, /xml and /f ($(cat "$TMP/schtasks.args"))"; ok
grep -q '<UserId>ncc-test\\someone</UserId>' "$TMP/registered.xml" || fail "the registered file has the user"; ok
! grep -Eq '<Password>|<LogonType>Password' "$TMP/registered.xml" || fail "the registered file stores no password"; ok
grep -q '<LogonType>InteractiveToken</LogonType>' "$TMP/registered.xml" || fail "the task stays interactive-only"; ok
grep -q 'wsl.exe -d Debian -u root' "$TMP/registered.xml" || fail "the task runs wsl.exe as root in Debian"; ok
grep -q 'python3 -I /usr/local/lib/afa-root-check/wsl_root_check.py' "$TMP/registered.xml" || fail "the task runs the root-owned copy, isolated"; ok
! grep -q '/home/' "$TMP/registered.xml" || fail "the task never runs code from a home folder"; ok
# The copy into the root-owned folder goes through wsl.exe as root, with both files, owner root, mode 0755.
grep -q -- '-d Debian -u root -e sh -c' "$TMP/wsl.args" || fail "the copy runs as root in Debian through -e, so no shell expands the $ text ($(cat "$TMP/wsl.args"))"; ok
grep -q 'install -m 0755 -o root -g root' "$TMP/wsl.args" && grep -q 'install -d -m 0755 -o root -g root' "$TMP/wsl.args" || fail "files and folder are installed root-owned, mode 0755"; ok
grep -q "$SRC/wsl_root_check.py $SRC/wsl_reserve.py $SRC/docker_volume_backup.py" "$TMP/wsl.args" && grep -q ' /usr/local/lib/afa-root-check ' "$TMP/wsl.args" || fail "all three scripts go to /usr/local/lib/afa-root-check"; ok

# 3. A task file that already has a user (a second run on a filled copy) ends with one, not two.
run --print >"$TMP/filled.xml"
[ "$(XML="$TMP/filled.xml" run --print | grep -c '<UserId>')" = 1 ] || fail "a second pass keeps a single UserId"; ok

# 4. Refusals: a name without DOMAIN\user, a task file that stores a password, a file with no LogonTrigger.
printf '#!/usr/bin/env bash\necho nobody\n' >"$TMP/whoami"
! run --print >/dev/null 2>&1 || fail "a user name without a domain is refused"; ok
printf '#!/usr/bin/env bash\nprintf "ncc-test\\\\someone\\r\\n"\n' >"$TMP/whoami"
sed 's|<LogonType>InteractiveToken</LogonType>|<LogonType>Password</LogonType>|' "$SRC/windows/AFA-WslRootCheck.xml" >"$TMP/pw.xml"
! XML="$TMP/pw.xml" run --print >/dev/null 2>&1 || fail "a password task file is refused"; ok
sed '/LogonTrigger/d' "$SRC/windows/AFA-WslRootCheck.xml" >"$TMP/nolog.xml"
! XML="$TMP/nolog.xml" run --print >/dev/null 2>&1 || fail "a file with no LogonTrigger is refused"; ok

printf 'ok: %s checks\n' "$checks"
