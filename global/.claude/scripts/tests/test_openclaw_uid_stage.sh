#!/usr/bin/env bash
# Staged proof for openclaw_uid.sh. Runs from the main distro; does its work inside OpenClawGateway as root
# on a THROWAWAY user (octest, uid 51900), a stub gateway (a python http.server on 127.0.0.1:18999 in a user
# unit) and a throwaway drop folder. It never touches the real openclaw user, unit, port or folder.
#
#   tests/test_openclaw_uid_stage.sh            setup, apply, verify, rollback, check, teardown
#   tests/test_openclaw_uid_stage.sh keep       the same but leave the stage in place for inspection
#
# Exit 0 only if apply verified clean AND rollback restored the old unit, uid and ownership.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../openclaw_uid.sh"
KEEP="${1:-}"
W() { # W: the snippet comes on stdin (arguments through wsl.exe mangle quotes); it runs as a function with stdin closed
  { echo 'snippet() {'; cat; echo '}'; echo 'snippet </dev/null'; } | timeout "${WSL_TIMEOUT:-300}" wsl.exe -d OpenClawGateway -u root -- bash -s 2>&1 | tr -d '\r' | grep -v 'screen size is bogus'
  return "${PIPESTATUS[1]}"
}

ENVLINES='export OC_USER=octest OC_NEW_UID=52900 OC_NEW_GID=52900 OC_DROP_GROUP=octestdrop OC_DROP_GID=52901
export OC_UNIT=octest-gateway OC_PORT=18999 OC_DROP_DIR=/mnt/wsl/octest-drop OC_BACKUP_ROOT=/var/backups/openclaw-uid-stage OC_LIVE=0
export OC_SHIM=/usr/local/bin/octest-systemctl'
run_script() { { echo "$ENVLINES"; cat "$SCRIPT"; } | timeout 900 wsl.exe -d OpenClawGateway -u root -- bash -s -- "$@" 2>&1 | tr -d '\r' | grep -v 'screen size is bogus'; return "${PIPESTATUS[1]}"; }

ok=0
expect() { # expect NAME 0|1
  if [ "$2" = 0 ]; then echo "STAGE-PASS $1"; else echo "STAGE-FAIL $1"; ok=1; fi
}

teardown() {
  W <<'SNIP' | tail -3
    systemctl disable --now octest-gateway.service 2>/dev/null; rm -f /etc/systemd/system/octest-gateway.service /etc/tmpfiles.d/octest-gateway-dropdir.conf; systemctl daemon-reload
    loginctl disable-linger octest 2>/dev/null; systemctl stop user@51900.service user@52900.service 2>/dev/null
    pkill -u 51900 2>/dev/null; pkill -u 52900 2>/dev/null; sleep 1
    userdel -r octest 2>/dev/null; groupdel octest 2>/dev/null; groupdel octestdrop 2>/dev/null
    rm -rf /mnt/wsl/octest-drop /var/backups/openclaw-uid-stage /tmp/octest-owned; rm -f /usr/local/bin/octest-systemctl
    getent passwd octest octestdrop; getent group octest octestdrop; echo teardown-done
SNIP
}

# The live probe shim (/usr/local/bin/systemctl) is what lets Companion's web chat load. The stage must never touch it:
# on 17 Oct 20:33 a run without OC_SHIM overwrote it and its rollback deleted it, and the web chat broke.
live_shim() { W <<'SNIP' | tail -1
  sha256sum /usr/local/bin/systemctl 2>/dev/null | cut -d' ' -f1 || true; [ -e /usr/local/bin/systemctl ] || echo absent
SNIP
}
LIVE_SHIM_BEFORE="$(live_shim)"

echo "== stage setup: user octest uid 51900 with a stub gateway as a user unit"
teardown >/dev/null
W <<'SNIP' | tail -6
  set -e
  groupadd -g 51900 octest; useradd -u 51900 -g 51900 -m -s /bin/bash octest
  loginctl enable-linger octest
  for i in $(seq 1 30); do [ -S /run/user/51900/bus ] && break; sleep 1; done
  H=/home/octest; mkdir -p $H/.config/systemd/user $H/.octest-state/deep/er; echo data > $H/.octest-state/deep/er/f; chmod 700 $H/.octest-state
  ln -s /etc/hostname $H/.octest-state/a-link
  cat > $H/.config/systemd/user/octest-gateway.service <<EOF
[Unit]
Description=stub gateway for the uid stage
[Service]
ExecStart=/usr/bin/python3 -m http.server 18999 --bind 127.0.0.1 --directory /home/octest
Restart=always
Environment=HOME=/home/octest
[Install]
WantedBy=default.target
EOF
  chown -R 51900:51900 $H
  mkdir -p /tmp/octest-owned; echo x > /tmp/octest-owned/f; chown -R 51900:51900 /tmp/octest-owned
  mkdir -p /mnt/wsl/octest-drop; chown 51900:51900 /mnt/wsl/octest-drop; chmod 1777 /mnt/wsl/octest-drop
  echo "stub" >/dev/null
  runuser -u octest -- env XDG_RUNTIME_DIR=/run/user/51900 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/51900/bus systemctl --user daemon-reload
  runuser -u octest -- env XDG_RUNTIME_DIR=/run/user/51900 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/51900/bus systemctl --user enable --now octest-gateway.service
  for i in $(seq 1 20); do ss -ltn "sport = :18999" | grep -q LISTEN && break; sleep 1; done
  ss -ltnp "sport = :18999" | tail -1 | sed "s/users:.*pid=\([0-9]*\).*/pid=\1/"
  echo "gateway-stub uid: $(ps -o uid= -p $(systemctl --machine=octest@.host --user show octest-gateway.service -p MainPID --value 2>/dev/null || echo 1) 2>/dev/null)"
SNIP

echo; echo "== APPLY on the stage"
run_script apply | tee /tmp/uid-stage-apply.out | sed 's/^/   /'
rc=${PIPESTATUS[0]}
expect "apply exits 0 and every check passes" "$rc"
[ "$(grep -c '^FAIL' /tmp/uid-stage-apply.out)" = 0 ]; expect "no FAIL line in apply output" $?
[ "$(grep -c '^PASS' /tmp/uid-stage-apply.out)" -ge 25 ]; expect "at least 25 PASS lines" $?

echo; echo "== independent read-back of the staged apply (not through the script's own checks)"
R="$(W <<'SNIP'
  echo "uid=$(id -u octest) gid=$(id -g octest) groups=$(id -G octest)"
  echo "unit=$(systemctl show octest-gateway.service -p FragmentPath -p User -p ActiveState --value | paste -sd" ")"
  P=$(systemctl show octest-gateway.service -p MainPID --value); echo "mainpid_uid=$(awk "/^Uid:/{print \$2}" /proc/$P/status)"
  echo "home_owner=$(stat -c %u:%g /home/octest) deep=$(stat -c %u:%g /home/octest/.octest-state/deep/er/f) link=$(stat -c %u:%g -L /home/octest/.octest-state/a-link) linkself=$(stat -c %u /home/octest/.octest-state/a-link)"
  echo "tmp_owner=$(stat -c %u:%g /tmp/octest-owned/f)"
  echo "drop=$(stat -c "%a %u %g" /mnt/wsl/octest-drop)"
  echo "http=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:18999/)"
  echo "old_uid_files=$(find / -xdev -uid 51900 2>/dev/null | wc -l)"
SNIP
)"
echo "$R" | sed 's/^/   /'
grep -q '^uid=52900 gid=52900 groups=52900 52901' <<<"$R"; expect "account renumbered to 52900/52900 with groups 52900 52901" $?
grep -q '^unit=' <<<"$R" && grep -q 'unit=.*/etc/systemd/system/octest-gateway.service' <<<"$R" && grep -q 'unit=.*octest' <<<"$R" && grep -q 'unit=.*active' <<<"$R"; expect "system unit, User=octest, active" $?
grep -q '^mainpid_uid=52900' <<<"$R"; expect "stub gateway process runs as uid 52900" $?
grep -q 'home_owner=52900:52900 deep=52900:52900 link=0:0 linkself=52900' <<<"$R"; expect "home, nested file and the symlink itself moved (target not followed)" $?
grep -q '^tmp_owner=52900:52900' <<<"$R"; expect "file outside home on / moved too" $?
grep -q '^drop=1770 1000 52901' <<<"$R"; expect "drop folder 1770 owner 1000 group 52901" $?
grep -q '^http=200' <<<"$R"; expect "stub gateway answers on its port as the new uid" $?
grep -q '^old_uid_files=0' <<<"$R"; expect "no file left at uid 51900" $?

echo; echo "== verify (standalone, changes nothing)"
run_script verify > /tmp/uid-stage-verify.out; expect "standalone verify exits 0" $?

echo; echo "== ROLLBACK"
run_script rollback | tee /tmp/uid-stage-rollback.out | sed 's/^/   /'
expect "rollback exits 0 and its four checks pass" "${PIPESTATUS[0]}"
R2="$(W <<'SNIP'
  echo "uid=$(id -u octest) gid=$(id -g octest) groups=$(id -G octest)"
  echo "system_unit=$([ -e /etc/systemd/system/octest-gateway.service ] && echo present || echo gone) tmpfiles=$([ -e /etc/tmpfiles.d/octest-gateway-dropdir.conf ] && echo present || echo gone)"
  P=$(runuser -u octest -- env XDG_RUNTIME_DIR=/run/user/51900 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/51900/bus systemctl --user show octest-gateway.service -p MainPID --value)
  echo "user_unit_pid_uid=$(awk "/^Uid:/{print \$2}" /proc/$P/status 2>/dev/null)"
  echo "http=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:18999/)"
  echo "home_owner=$(stat -c %u:%g /home/octest) deep=$(stat -c %u:%g /home/octest/.octest-state/deep/er/f) tmp=$(stat -c %u:%g /tmp/octest-owned/f)"
  echo "drop=$(stat -c "%a %u %g" /mnt/wsl/octest-drop)"
  echo "new_ids_left=$(find / -xdev \( -uid 52900 -o -gid 52900 -o -gid 52901 \) 2>/dev/null | wc -l) dropgroup=$(getent group octestdrop | wc -l)"
  echo "user_enabled=$(runuser -u octest -- env XDG_RUNTIME_DIR=/run/user/51900 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/51900/bus systemctl --user is-enabled octest-gateway.service)"
SNIP
)"
echo "$R2" | sed 's/^/   /'
grep -q '^uid=51900 gid=51900 groups=51900$' <<<"$R2"; expect "account back to 51900/51900, drop group gone from it" $?
grep -q 'system_unit=gone tmpfiles=gone' <<<"$R2"; expect "system unit and tmpfiles entry removed" $?
grep -q '^user_unit_pid_uid=51900' <<<"$R2"; expect "old user unit running again as 51900" $?
grep -q '^http=200' <<<"$R2"; expect "stub gateway answers again" $?
grep -q 'home_owner=51900:51900 deep=51900:51900 tmp=51900:51900' <<<"$R2"; expect "ownership restored" $?
grep -q '^drop=1777 51900 51900' <<<"$R2"; expect "drop folder back to 1777 51900:51900" $?
grep -q 'new_ids_left=0 dropgroup=0' <<<"$R2"; expect "nothing left at 52900/52901" $?
grep -q '^user_enabled=enabled' <<<"$R2"; expect "user unit enabled again" $?

LIVE_SHIM_AFTER="$(live_shim)"
[ -n "$LIVE_SHIM_BEFORE" ] && [ "$LIVE_SHIM_BEFORE" = "$LIVE_SHIM_AFTER" ]; expect "live probe shim /usr/local/bin/systemctl untouched by the stage" $?

echo
if [ "$KEEP" = keep ]; then echo "stage left in place (keep)"; else echo "== teardown"; teardown; fi
echo
if [ "$ok" = 0 ]; then echo "STAGE RESULT: ALL PASS"; else echo "STAGE RESULT: FAILURES"; fi
exit "$ok"
