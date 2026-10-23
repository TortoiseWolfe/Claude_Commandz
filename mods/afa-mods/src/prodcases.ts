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
export const DEPLOY = '#!/usr/bin/env bash\n# stand-in deploy script for the hook tests\ncontainer_exec() { :; }\n'
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
