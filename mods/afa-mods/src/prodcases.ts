// Cases shared by hooks.test.ts (the dialog and deny per mode) and prodcall.test.ts (the classifier with the deploy pin injected).

export const HOME = '/home/t'
export const SP = '/tmp/claude-1000/-home-t-repos-chattanooga-digital/0a1b2c3d/scratchpad'
export const REXEC = `#!/usr/bin/env bash
# usage: rexec.sh --prod|--staging '<shell cmd>' [exec user, default 82 = www-data]
T="$1"; CMD="$2"; U="\${3-82}"
cd ~/repos/Chattanooga-Digital || exit 1
source scripts/portainer-deploy.sh "$T"
auth >/dev/null 2>&1 || { echo "auth failed"; exit 1; }
cid="$(find_service_container "\${STACK_NAME}_php")"
[ -n "$cid" ] || { echo "no php container for $STACK_NAME"; exit 1; }
echo "[rexec] stack=$STACK_NAME portainer=$PORTAINER_URL" >&2
container_exec "$cid" "$CMD" "$U"
`
// A stand-in for the repo's portainer-deploy.sh. The real pin list holds only reviewed copies of the real scripts, so the classifier tests inject this
// one's hash through ScratchEnv.deployPins; the hook tests, which cannot reach the plugin's list, see it as an unreviewed copy.
// Its dispatcher has the same shape as the real one: status and config-get carry no marker on the case line, drift has a doubled comment.
export const DEPLOY = `#!/usr/bin/env bash
# stand-in deploy script for the hook tests
container_exec() { :; }
auth() { :; }
find_service_container() { :; }
api() { :; }
cmd_delete() { :; }
set_service_replicas() { :; }
dispatch_action() {
  local action="$1"
  case "$action" in
    status)               cmd_status ;;
    backup)               cmd_backup ;;
    delete)               cmd_delete ;;
    create)               cmd_create ;;
    redeploy)             cmd_git_redeploy ;;
    live-changes)         cmd_live_changes ;;
    redeploy-destructive) cmd_redeploy_destructive ;;
    force-update)         cmd_force_update ;;
    config-get)           cmd_config_get ;;
    deployed-commit)      cmd_deployed_commit ;;  # read-only; prints the commit Portainer deployed
    logs)                 cmd_logs ;;          # read-only; tail a service container log
    state-get)            cmd_state_get ;;     # read-only; print one Drupal State value
    drift)                cmd_drift ;;         # read-only; deployed commit vs branch head (#319)   # read-only; prints one active config object
    dns-check)            dns_postcheck ;;  # standalone hostname probe (#56); no Portainer auth needed
    *)
      err "unknown action: $action"
      echo "Usage: $0 {status|backup|redeploy|config-get} [--prod|--staging] [--skip-backup]" >&2
      echo "  config-get <name>     READ-ONLY: print one active config object (runs as www-data)" >&2
      echo "  redeploy              SAFE DEFAULT: git redeploy; preserves every named volume" >&2
      return 1
      ;;
  esac
}
`
export const prod = (remote: string, q = "'") => `SP=${SP}; STACK_NAME=x timeout 120 bash $SP/rexec.sh --prod ${q}${remote}${q} 2>&1 | tail -5`
export const WRITES: [string, string, string?][] = [
  ['drush updb -y', prod('drush updb -y')],
  ['drush cim -y', prod('drush cim -y')],
  ['drush sqlq UPDATE', prod('drush sqlq "UPDATE users SET name=1"')],
  ['rm -rf', prod('rm -rf /var/www/html/sites')],
  ['docker service update', prod('docker service update --force x')],
  ['a redirect to a file', prod('drush status > /tmp/x')],
  ['a variable in a double-quoted remote', prod('drush $CMD', '"')],
]
// 22 Oct security review of 9015962: the script is read before the command runs, so nothing in the same command may touch or race it.
export const CALL = "STACK_NAME=x timeout 120 bash $SP/rexec.sh --prod 'drush status'"
export const BYPASSES: [string, string][] = [
  ['an earlier segment rewrites the script', `SP=${SP}; echo evil >> $SP/rexec.sh && ${CALL} 2>&1 | tail -5`],
  ['an earlier segment rewrites the script, joined by ;', `SP=${SP}; printf x > $SP/rexec.sh; ${CALL}`],
  ['an earlier segment is a command substitution', `SP=${SP}; X=$(echo evil >> $SP/rexec.sh); ${CALL}`],
  ['an earlier segment is a backtick', `SP=${SP}; X=\`echo evil >> $SP/rexec.sh\`; ${CALL}`],
  ['a background segment races the read', `SP=${SP}; ( sleep 1; echo evil >> $SP/rexec.sh ) & ${CALL}`],
  ['a lone & sends an earlier assignment to the background', `SP=${SP} & ${CALL}`],
  ['the call is in the background', `SP=${SP}; ${CALL} &`],
  ['a pipe into tee rewrites the script', `SP=${SP}; echo evil | tee $SP/rexec.sh | ${CALL}`],
  ['the output is piped into tee on the script', `SP=${SP}; ${CALL} 2>&1 | tee $SP/rexec.sh`],
  ['the output is piped into tail with a file flag', `SP=${SP}; ${CALL} 2>&1 | tail -f $SP/rexec.sh`],
  ['a second command follows the call', `SP=${SP}; ${CALL}; echo evil >> $SP/rexec.sh`],
  ['an || list', `SP=${SP}; true || ${CALL}`],
  ['the call is wrapped in bash -c after a rewrite', `SP=${SP}; echo evil >> $SP/rexec.sh; bash -c "${CALL}"`],
  ['an assignment that moves HOME', `SP=${SP}; HOME=/tmp/x; ${CALL}`],
  ['an assignment that sets PATH', `SP=${SP}; PATH=/tmp/x:$PATH; ${CALL}`],
  ['a relative cd', `SP=${SP}; cd ../x; ${CALL}`],
  // the 2>&1 split: the words after the "1" still reach rexec.sh as arguments
  // 22 Oct automated review: the script path and the heredoc view must be the ones bash sees
  ['a heredoc marker inside a quoted assignment', `SP=${SP}; X="a<<EOF"; ${CALL}`],
  ['a heredoc marker in single quotes', `SP=${SP}; X='<<EOF'; ${CALL}`],
  ['a second assignment hidden in a quoted value', `SP="${SP}; SP=/tmp/evil"; ${CALL}`],
  ['a space in a quoted value splits the unquoted $SP', `SP="${SP} x"; ${CALL}`],
  ['a ~ in an unquoted value', `SP=~/x; ${CALL}`],
  ['a readonly variable as the path', `UID=${SP}; ${CALL.replace('$SP', '$UID')}`],
  ['a changing variable as the path', `RANDOM=${SP}; ${CALL.replace('$SP', '$RANDOM')}`],
  ['the $_ parameter as the path', `_=${SP}; ${CALL.replace('$SP', '$_')}`],
  ['PIPESTATUS as the path', `PIPESTATUS=${SP}; ${CALL.replace('$SP', '$PIPESTATUS')}`],
  ['a BASH_ name as the path', `BASH_ARGV0=${SP}; ${CALL.replace('$SP', '$BASH_ARGV0')}`],
  ['a redirect in a quoted value', `SP=${SP}; X="a>b"; ${CALL}`],
  ['the script word uses a variable the line did not set', `STACK_NAME=x timeout 120 bash $NOPE/rexec.sh --prod 'drush status'`],
  ['the script word uses an inherited variable', `STACK_NAME=x timeout 120 bash $TMPDIR/rexec.sh --prod 'drush status'`],
  // senior review of #94 (23 Oct)
  ['a .. in the script word (a symlink before it changes where it lands)', `SP=${SP}; STACK_NAME=x bash $SP/lnk/../rexec.sh --prod 'drush status'`],
  ['a . in the script word', `SP=${SP}; STACK_NAME=x bash $SP/./rexec.sh --prod 'drush status'`],
  ['a .. inside a variable the script word uses', `SP=${SP}/lnk/..; ${CALL}`],
  ['a .. in a cd before a relative script', `cd ${SP}/lnk/..; STACK_NAME=x bash rexec.sh --prod 'drush status'`],
  ['a carriage return after an assignment', `SP=${SP}\r; ${CALL}`],
  ['a carriage return after the call', `SP=${SP}; ${CALL}\r`],
  ['a no-break space in the command', `SP=${SP}; ${CALL}\u00a0`],
  ['a no-break space inside an assignment', `SP=${SP}\u00a0; ${CALL}`],
  ['2>&1 with extra words after the 1', `SP=${SP}; STACK_NAME=x timeout 120 bash $SP/rexec.sh --prod 'drush status' 2>&1 extra args | tail -5`],
  ['2>&1 with an extra word and no pipe', `SP=${SP}; STACK_NAME=x bash $SP/rexec.sh --prod 'drush status' 2>&1 root`],
  ['2>&1 followed by a second command', `SP=${SP}; STACK_NAME=x bash $SP/rexec.sh --prod 'drush status' 2>&1; rm -rf /x`],
]
export const PLAIN = [
  `SP=${SP}; ${CALL}`,
  `cd /tmp && SP=${SP} && ${CALL} 2>&1 | head -n 20`,
  `SP=${SP}\nSTACK_NAME=y\n${CALL} 2>&1 | tail -n 5`,
]
export const FORWARDERS: [string, string, string | null][] = [
  ['a forwarder with a second container_exec', prod('drush status'), `${REXEC}container_exec "$cid" "rm -rf /x" "$U"\n`],
  ['a forwarder that sources another file', prod('drush status'), REXEC.replace('auth >', 'source /tmp/x.sh\nauth >')],
  ['a forwarder that dot-sources another file', prod('drush status'), REXEC.replace('auth >', '. /tmp/x.sh\nauth >')],
  ['a forwarder whose first line is an env -S shebang', prod('drush status'), REXEC.replace('#!/usr/bin/env bash', "#!/usr/bin/env -S sh -c 'anything'")],
  ['a forwarder with a carriage return', prod('drush status'), REXEC.replace('T="$1"', 'T="$1"\r')],
  ['a forwarder with a no-break space', prod('drush status'), REXEC.replace('# usage', '#\u00a0usage')],
  ['a forwarder whose cd can fail and go on', prod('drush status'), REXEC.replace(' || exit 1\nsource', '\nsource')],
  ['a forwarder with a second cd after the first', prod('drush status'), REXEC.replace('source scripts', 'cd ~/repos/evil || exit 1\nsource scripts')],
  ['a forwarder with an indented cd', prod('drush status'), REXEC.replace('cd ~/repos', '  cd ~/repos')],
  ['a forwarder with a tab-indented cd', prod('drush status'), REXEC.replace('cd ~/repos', '\tcd ~/repos')],
  ['a forwarder script that is not found', prod('drush status'), null],
]

// 26 Oct, coop-mail: the deploy script run directly with a read-only action. Every one of these is a read when the script is the reviewed copy.
export const DEPLOY_DIR = `${HOME}/repos/Chattanooga-Digital`
export const DEPLOY_READS: [string, string][] = [
  ['bash with a bare name reads it from the folder', 'cd ~/repos/Chattanooga-Digital/scripts && bash portainer-deploy.sh status --prod'],
  ['a source followed by a plain command', 'source scripts/portainer-deploy.sh --prod status; echo done'],
  ['a source, find_service_container, container_exec', `source scripts/portainer-deploy.sh --prod status; auth; cid="$(find_service_container "\${STACK_NAME}_php")"; container_exec "$cid" 'php -r "echo PHP_VERSION"' 82`],
  ['timeout + deployed-commit', 'timeout 120 scripts/portainer-deploy.sh deployed-commit --prod'],
  ['timeout + drift', 'timeout 120 scripts/portainer-deploy.sh drift --prod'],
  ['timeout + status', 'timeout 120 scripts/portainer-deploy.sh status --prod'],
  ['source + status, then the functions it defined', `source scripts/portainer-deploy.sh --prod status >/dev/null 2>&1; auth; container_exec "$cid" 'php -r "echo PHP_VERSION"' 82`],
  ['the flag before the action', 'scripts/portainer-deploy.sh --prod status'],
  ['no action (the script defaults to status)', 'scripts/portainer-deploy.sh --prod'],
  ['bash in front, a pipe into tail', 'bash scripts/portainer-deploy.sh status --prod 2>&1 | tail -20'],
  ['nice and env in front', 'env STACK_NAME=x nice -n 5 scripts/portainer-deploy.sh status --production'],
  ['config-get with its operand', 'timeout 60 scripts/portainer-deploy.sh config-get system.site --prod'],
  ['state-get with its operand', 'scripts/portainer-deploy.sh state-get system.cron_last --prod'],
  ['logs', 'scripts/portainer-deploy.sh logs --prod'],
  ['a full path', `${DEPLOY_DIR}/scripts/portainer-deploy.sh status --prod`],
  ['a ~ path', 'timeout 120 ~/repos/Chattanooga-Digital/scripts/portainer-deploy.sh status --prod'],
  ['a cd to the repo first', 'cd ~/repos/Chattanooga-Digital && timeout 120 scripts/portainer-deploy.sh status --prod'],
  ['a dot-source', '. scripts/portainer-deploy.sh --prod status'],
]
// A sourced script leaves its functions callable: after the read-only call, only its read helpers pass. A bare script name is a PATH lookup.
export const DEPLOY_SOURCED_HELD: [string, string][] = [
  ['a write function after a source', 'source scripts/portainer-deploy.sh --prod status; cmd_delete'],
  ['a write function after a source, && joined', 'source scripts/portainer-deploy.sh --prod status && set_service_replicas php 0'],
  ['api with a DELETE after a dot-source', '. scripts/portainer-deploy.sh --prod status; auth; api -X DELETE http://x/api/stacks/1'],
  ['a write function inside a substitution', 'source scripts/portainer-deploy.sh --prod status; x=$(cmd_delete); echo $x'],
  ['a write function named after a plain command', 'source scripts/portainer-deploy.sh --prod status; echo ok; cmd_delete'],
  ['eval after a source', 'source scripts/portainer-deploy.sh --prod status; eval "cmd_delete"'],
  ['a command word built from an expansion', 'source scripts/portainer-deploy.sh --prod status; $FN'],
  ['container_exec with a write', `source scripts/portainer-deploy.sh --prod status; auth; container_exec "$cid" 'rm -rf /var/www' 82`],
  ['container_exec with a double-quoted remote', 'source scripts/portainer-deploy.sh --prod status; container_exec "$cid" "php -r \'echo 1;\'" 82'],
  ['a bare script name with source (PATH lookup)', 'source portainer-deploy.sh --prod status'],
  ['a bare script name run directly (PATH lookup)', 'timeout 120 portainer-deploy.sh status --prod'],
  ['a bare script name with a dot-source', '. portainer-deploy.sh --prod status'],
]
// Everything that is not a read stays held, with or without a wrapper.
export const DEPLOY_HELD: [string, string][] = [
  ['redeploy', 'scripts/portainer-deploy.sh redeploy --prod'],
  ['redeploy behind timeout', 'timeout 120 scripts/portainer-deploy.sh redeploy --prod'],
  ['force-update', 'timeout 120 scripts/portainer-deploy.sh force-update --prod'],
  ['create', 'scripts/portainer-deploy.sh create --prod'],
  ['delete', 'scripts/portainer-deploy.sh delete --prod'],
  ['redeploy-destructive', 'scripts/portainer-deploy.sh redeploy-destructive --prod'],
  ['backup', 'scripts/portainer-deploy.sh backup --prod'],
  ['live-changes', 'scripts/portainer-deploy.sh live-changes --prod'],
  ['dns-check', 'scripts/portainer-deploy.sh dns-check --prod'],
  ['an action the script does not know', 'scripts/portainer-deploy.sh frobnicate --prod'],
  ['status with --skip-backup', 'scripts/portainer-deploy.sh status --prod --skip-backup'],
  ['status with an extra word', 'scripts/portainer-deploy.sh status --prod now please'],
  ['an operand on an action that takes none', 'scripts/portainer-deploy.sh status system.site --prod'],
  ['a substitution as the action', 'scripts/portainer-deploy.sh $(echo redeploy) --prod'],
  ['a variable as the action', 'scripts/portainer-deploy.sh $ACTION --prod'],
  ['bash -x in front', 'bash -x scripts/portainer-deploy.sh status --prod'],
  ['env -i in front', 'env -i scripts/portainer-deploy.sh status --prod'],
  ['an environment word that is not STACK_NAME', 'PATH=/tmp/x scripts/portainer-deploy.sh status --prod'],
  ['a script of another name', 'timeout 120 scripts/other-deploy.sh status --prod'],
  ['a .. in the script word', 'timeout 120 scripts/../scripts/portainer-deploy.sh status --prod'],
  ['a repo folder that is not on the pin list', '~/repos/evil/scripts/portainer-deploy.sh status --prod'],
  ['a worktree copy', '~/repos/.worktrees/Chattanooga-Digital--x/scripts/portainer-deploy.sh status --prod'],
  ['an earlier segment rewrites the script', 'echo x >> scripts/portainer-deploy.sh; timeout 120 scripts/portainer-deploy.sh status --prod'],
  ['a background run', 'scripts/portainer-deploy.sh status --prod &'],
  ['a pipe into tee', 'scripts/portainer-deploy.sh status --prod | tee scripts/portainer-deploy.sh'],
  ['an || list', 'true || scripts/portainer-deploy.sh status --prod'],
  ['a heredoc anywhere', 'scripts/portainer-deploy.sh status --prod; cat <<EOF\nx\nEOF'],
  ['a second deploy call that redeploys', 'scripts/portainer-deploy.sh status --prod; scripts/portainer-deploy.sh redeploy --prod'],
]
