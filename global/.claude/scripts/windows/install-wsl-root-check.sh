#!/usr/bin/env bash
# Registers the Windows task \AFA\WslRootCheck from windows/AFA-WslRootCheck.xml (run from WSL as the signed-in Windows user).
#
# Why a script and not the bare schtasks line: a LogonTrigger with no UserId fires for ANY user's sign-in, and Windows refuses to register that
# without administrator rights ("Access is denied", 28 Oct 2026). So this fills the signed-in user (from whoami.exe) into the LogonTrigger,
# which a normal user may register. No password is read, written or stored: the task is Interactive only and runs while that user is signed in.
#
#   install-wsl-root-check.sh           register (or replace) the task
#   install-wsl-root-check.sh --print   print the filled-in XML and register nothing (the word print works too; the XML comment cannot hold "--")
# Before it registers, it copies wsl_root_check.py and wsl_reserve.py into /usr/local/lib/afa-root-check (owned by root, mode 755) through
# wsl.exe -u root, because the task runs as root and root must not run code from a folder a person's own processes can write.
# A changed script takes effect only when this is run again. The copy is a person-run step: read the two files first.
# Test hooks: WHOAMI, SCHTASKS, WSLPATH, WSLEXE (commands), XML (the task file), ROOT_LIB (target folder).
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
xml=${XML:-$here/AFA-WslRootCheck.xml}
who=$(${WHOAMI:-whoami.exe} | tr -d '\r\n')
case $who in
  *\\*) ;;
  *) echo "install-wsl-root-check: whoami.exe gave '$who', expected DOMAIN\\user" >&2; exit 1 ;;
esac
if ! printf '%s' "$who" | grep -Eq '^[A-Za-z0-9._ $-]+\\[A-Za-z0-9._ $-]+$'; then
  echo "install-wsl-root-check: unexpected characters in the user name '$who'" >&2; exit 1
fi
filled=$(WHO="$who" python3 - "$xml" <<'PY'
import os, re, sys
s = open(sys.argv[1], encoding="utf-8").read()
s = re.sub(r"(<LogonTrigger>\s*)<UserId>[^<]*</UserId>\s*", r"\1", s)  # idempotent: drop an old user first
s, n = re.subn(r"(<LogonTrigger>)(\s*)", lambda m: "%s%s<UserId>%s</UserId>%s" % (m.group(1), m.group(2) or "\n      ", os.environ["WHO"], m.group(2)), s, count=1)
if n != 1:
    sys.exit("install-wsl-root-check: no <LogonTrigger> in the task file")
if re.search(r"<Password>|<LogonType>Password</LogonType>", s):
    sys.exit("install-wsl-root-check: the task file stores a password; refusing")
sys.stdout.write(s)
PY
)
case "${1:-}" in --print|print) printf '%s\n' "$filled"; exit 0 ;; esac
lib=${ROOT_LIB:-/usr/local/lib/afa-root-check}
src=$(cd "$here/.." && pwd)
for f in wsl_root_check.py wsl_reserve.py; do
  [ -f "$src/$f" ] || { echo "install-wsl-root-check: $src/$f is missing; install the scripts first" >&2; exit 1; }
done
${WSLEXE:-wsl.exe} -d Debian -u root -e sh -c 'set -e; lib=$1; shift; install -d -m 0755 -o root -g root "$lib"; for f in "$@"; do install -m 0755 -o root -g root "$f" "$lib/$(basename "$f")"; done' sh "$lib" "$src/wsl_root_check.py" "$src/wsl_reserve.py"
echo "copied wsl_root_check.py and wsl_reserve.py into $lib (root-owned)"
tmp=$(mktemp "${TMPDIR:-/tmp}/AFA-WslRootCheck.XXXXXX.xml")
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "$filled" | iconv -f UTF-8 -t UTF-16 >"$tmp"
${SCHTASKS:-schtasks.exe} /create /tn '\AFA\WslRootCheck' /xml "$(${WSLPATH:-wslpath} -w "$tmp")" /f
echo "registered \\AFA\\WslRootCheck for $who (interactive only, no password)"
