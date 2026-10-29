#!/usr/bin/env bash
# Registers the Windows task \AFA\TowerKeeper from windows/AFA-TowerKeeper.xml (run from WSL as the signed-in Windows user).
#
# Why a script and not the bare schtasks line: a LogonTrigger with no UserId fires for ANY user's sign-in, and Windows refuses to register that
# without administrator rights ("Access is denied", 28 Oct 2026). So this fills the signed-in user (from whoami.exe) into the LogonTrigger,
# which a normal user may register. No password is read, written or stored: the task is Interactive only and runs while that user is signed in.
#
#   install-tower-keeper.sh           register (or replace) the task
#   install-tower-keeper.sh --print   print the filled-in XML and register nothing (the word print works too; the XML comment cannot hold "--")
# Test hooks: WHOAMI, SCHTASKS, WSLPATH (commands), XML (the task file).
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
xml=${XML:-$here/AFA-TowerKeeper.xml}
who=$(${WHOAMI:-whoami.exe} | tr -d '\r\n')
case $who in
  *\\*) ;;
  *) echo "install-tower-keeper: whoami.exe gave '$who', expected DOMAIN\\user" >&2; exit 1 ;;
esac
if ! printf '%s' "$who" | grep -Eq '^[A-Za-z0-9._ $-]+\\[A-Za-z0-9._ $-]+$'; then
  echo "install-tower-keeper: unexpected characters in the user name '$who'" >&2; exit 1
fi
filled=$(WHO="$who" python3 - "$xml" <<'PY'
import os, re, sys
s = open(sys.argv[1], encoding="utf-8").read()
s = re.sub(r"(<LogonTrigger>\s*)<UserId>[^<]*</UserId>\s*", r"\1", s)  # idempotent: drop an old user first
s, n = re.subn(r"(<LogonTrigger>)(\s*)", lambda m: "%s%s<UserId>%s</UserId>%s" % (m.group(1), m.group(2) or "\n      ", os.environ["WHO"], m.group(2)), s, count=1)
if n != 1:
    sys.exit("install-tower-keeper: no <LogonTrigger> in the task file")
if re.search(r"<Password>|<LogonType>Password</LogonType>", s):
    sys.exit("install-tower-keeper: the task file stores a password; refusing")
sys.stdout.write(s)
PY
)
case "${1:-}" in --print|print) printf '%s\n' "$filled"; exit 0 ;; esac
tmp=$(mktemp "${TMPDIR:-/tmp}/AFA-TowerKeeper.XXXXXX.xml")
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "$filled" | iconv -f UTF-8 -t UTF-16 >"$tmp"
${SCHTASKS:-schtasks.exe} /create /tn '\AFA\TowerKeeper' /xml "$(${WSLPATH:-wslpath} -w "$tmp")" /f
echo "registered \\AFA\\TowerKeeper for $who (interactive only, no password)"
