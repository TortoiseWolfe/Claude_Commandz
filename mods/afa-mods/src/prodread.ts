// A command that ships to production is held. One exception is checked here, by reading what will run: a --prod call of a forwarder
// script (rexec.sh) whose script does nothing but pass the command on, and whose every remote command is a read (drush status, a
// SELECT, composer audit, a curl GET ...). Nothing here trusts a label: a command the reader does not recognise word for word is
// not a read, and the caller keeps holding it.

export type Word = { v: string; quote: 'none' | 'single' | 'double' | 'mixed' }

type Redirects = { errToOut?: boolean }

// The words of a command line, with quotes taken off. Returns undefined at anything it will not judge: a backslash outside quotes,
// a substitution, a subshell, a redirect other than the three that only discard or merge output, a "#" comment. `remote` is the
// text a remote shell will run (no unquoted "$" at all); the local line may use $VAR in a word, which the caller resolves.
// With `fusedErr`, a last word `2>` stands for `2>&1` (the splitter cut the line at the "&"; the caller checked what followed).
function tokenize(text: string, remote: boolean, fusedErr = false): { commands: Word[][]; ops: string[] } | undefined {
  const commands: Word[][] = [[]]
  const ops: string[] = []
  let word: Word | undefined
  let i = 0
  const endWord = () => {
    if (word) commands[commands.length - 1].push(word)
    word = undefined
  }
  const endCommand = (op: string) => {
    endWord()
    ops.push(op)
    commands.push([])
  }
  const REDIRECTS = ['2>&1', '2>/dev/null', '>/dev/null', '1>/dev/null']
  while (i < text.length) {
    const c = text[i]
    if (!word && /[ \t]/.test(c)) {
      i += 1
      continue
    }
    if (!word) {
      const rest = text.slice(i)
      const redirect = REDIRECTS.find(r => rest.startsWith(r) && (rest.length === r.length || /[\s;&|]/.test(rest[r.length])))
      if (redirect) {
        i += redirect.length
        continue
      }
      if (fusedErr && /^2>\s*$/.test(rest)) {
        i = text.length
        continue
      }
    }
    if (c === "'" || c === '"') {
      const piece = c
      let j = i + 1
      let value = ''
      for (; j < text.length && text[j] !== piece; j += 1) {
        if (piece === "'") value += text[j]
        else if (text[j] === '\\') {
          if (text[j + 1] !== '"' && text[j + 1] !== '\\') return undefined
          value += text[j + 1]
          j += 1
        } else if (text[j] === '$' || text[j] === '`') return undefined
        else value += text[j]
      }
      if (j >= text.length) return undefined
      const kind = piece === "'" ? 'single' : 'double'
      word = word ? { v: word.v + value, quote: word.quote === kind ? kind : 'mixed' } : { v: value, quote: kind }
      i = j + 1
      continue
    }
    if (c === '\\' || c === '`' || c === '(' || c === ')' || c === '<' || c === '>') return undefined
    if (c === '$' && (remote || !/[A-Za-z_{]/.test(text[i + 1] ?? ''))) return undefined
    if (c === '#' && !word) return undefined
    if (c === ';' || c === '\n') {
      endCommand(';')
      i += 1
      continue
    }
    if (c === '&') {
      if (text[i + 1] !== '&') return undefined
      endCommand('&&')
      i += 2
      continue
    }
    if (c === '|') {
      const op = text[i + 1] === '|' ? '||' : '|'
      endCommand(op)
      i += op.length
      continue
    }
    if (/[ \t]/.test(c)) {
      endWord()
      i += 1
      continue
    }
    word = word ? { v: word.v + c, quote: word.quote === 'none' ? 'none' : 'mixed' } : { v: c, quote: 'none' }
    i += 1
  }
  endWord()
  return { commands: commands.filter(c => c.length > 0), ops }
}

// ---- the local line: [STACK_NAME=x] [timeout N] [bash|sh] SCRIPT --prod 'REMOTE' [USER] ----

export type ProdCall = { script: string; remote: string; user?: string }

export function parseProdCall(raw: string, fusedErr: boolean): ProdCall | undefined {
  const t = tokenize(raw, false, fusedErr)
  if (!t || t.commands.length !== 1) return undefined
  let w = t.commands[0]
  while (w.length > 0 && /^[A-Za-z_]\w*=/.test(w[0].v)) {
    if (!/^STACK_NAME=[\w-]+$/.test(w[0].v)) return undefined
    w = w.slice(1)
  }
  if (w[0]?.v === 'timeout' && w[0].quote === 'none') {
    let j = 1
    while (w[j]?.v.startsWith('-')) j += ['-s', '-k', '--signal', '--kill-after'].includes(w[j].v) ? 2 : 1
    if (!/^\d+[smh]?$/.test(w[j]?.v ?? '')) return undefined
    w = w.slice(j + 1)
  }
  if (w[0] && (w[0].v === 'bash' || w[0].v === 'sh') && w[0].quote === 'none') w = w.slice(1)
  const [script, flag, remote, user, ...extra] = w
  if (!script || !flag || !remote || extra.length > 0) return undefined
  if (!/^--prod(?:uction)?$/.test(flag.v) || flag.quote !== 'none') return undefined
  if (remote.quote !== 'single' && remote.quote !== 'double') return undefined
  if (user && !/^(?:\d+|www-data|root)$/.test(user.v)) return undefined
  return { script: script.v, remote: remote.v, user: user?.v }
}

// ---- the deploy script run directly: [STACK_NAME=x] [env] [timeout N] [nice -n N] [bash|sh|source|.] SCRIPT [--prod] ACTION [OPERAND] ----
// 26 Oct: `timeout 120 scripts/portainer-deploy.sh status --prod` and `source scripts/portainer-deploy.sh --prod status` were held as a production
// deploy. The wrappers are looked through here to the script and its action; the caller decides whether the action is a read.

export type DeployCall = { script: string; action: string; operand?: string }

const DEPLOY_FLAG = /^--(?:prod|production|staging)$/
const OPERAND = /^[A-Za-z0-9_][\w.:-]*$/

export function parseDeployCall(raw: string, fusedErr: boolean): DeployCall | undefined {
  const t = tokenize(raw, false, fusedErr)
  if (!t || t.commands.length !== 1) return undefined
  let w = t.commands[0]
  for (let n = 0; n < 6 && w[0]?.quote === 'none'; n += 1) {
    const head = w[0].v
    if (/^[A-Za-z_]\w*=/.test(head)) {
      if (!/^STACK_NAME=[\w-]+$/.test(head)) return undefined
      w = w.slice(1)
    } else if (head === 'env') {
      w = w.slice(1)
    } else if (head === 'timeout') {
      let j = 1
      while (w[j]?.v.startsWith('-')) j += ['-s', '-k', '--signal', '--kill-after'].includes(w[j].v) ? 2 : 1
      if (!/^\d+[smh]?$/.test(w[j]?.v ?? '')) return undefined
      w = w.slice(j + 1)
    } else if (head === 'nice') {
      const nv = w[1]?.v === '-n' ? 2 : /^-\d+$/.test(w[1]?.v ?? '') ? 1 : 0
      if (nv === 2 && !/^-?\d+$/.test(w[2]?.v ?? '')) return undefined
      w = w.slice(nv + 1)
    } else if (['bash', 'sh', 'source', '.'].includes(head)) {
      w = w.slice(1)
      break
    } else break
  }
  const [script, ...args] = w
  if (!script || script.v.split('/').pop() !== 'portainer-deploy.sh') return undefined
  const words: Word[] = []
  for (const a of args) {
    if (a.quote === 'none' && DEPLOY_FLAG.test(a.v)) continue
    words.push(a)
  }
  if (words.length > 2) return undefined
  const [action, operand] = words
  if (action && !/^[a-z][a-z-]*$/.test(action.v)) return undefined
  if (operand && !OPERAND.test(operand.v)) return undefined
  // the script's own default action is status
  return { script: script.v, action: action?.v ?? 'status', operand: operand?.v }
}

// The actions of the pinned deploy script's dispatcher that only read: a case line whose comment says `read-only`, or a usage line that says READ-ONLY.
// `status` has neither in the real script, so it counts when the dispatcher lists it. Anything not named here (redeploy, force-update, create, delete,
// backup, live-changes, redeploy-destructive, dns-check, an action the dispatcher does not know) stays held. The text is the pinned copy, never a lane's.
export function readOnlyActions(text: string): Set<string> {
  const lines = text.split('\n')
  const start = lines.findIndex(l => /^dispatch_action\(\)\s*\{\s*$/.test(l))
  const end = start < 0 ? -1 : lines.findIndex((l, i) => i > start && l === '}')
  if (start < 0 || end < 0) return new Set()
  const labels = new Set<string>()
  const reads = new Set<string>()
  for (const line of lines.slice(start + 1, end)) {
    const c = /^\s*([a-z][a-z0-9-]*)\)\s+\S/.exec(line)
    if (c) {
      labels.add(c[1])
      if (/#.*read-only/i.test(line)) reads.add(c[1])
    }
    const u = /^\s*echo\s+"\s+([a-z][a-z0-9-]*)\b[^"]*\bREAD-ONLY\b/.exec(line)
    if (u) reads.add(u[1])
  }
  if (labels.has('status')) reads.add('status')
  return new Set([...reads].filter(a => labels.has(a)))
}

// ---- the whole command line around the call ----
// The hook reads the forwarder before the command runs, so nothing else in the command may touch that file or run beside it:
// only plain assignments or `cd /abs/path` before the call (joined by ; newline or &&), then the call, then optionally 2>&1,
// then optionally ONE pipe into tail/head with a number. Anything else is held.

type Piece = { text: string; sep: string }

// A value is one plain word: no space (the unquoted `$SP` in the call would split on it), no `~` (bash expands it in an assignment, the map would not), no `$`, glob or redirect characters.
const ASSIGN = /^([A-Za-z_]\w*)=([\w./:,@%+=-]*|'[\w./:,@%+=-]*'|"[\w./:,@%+=-]*")$/
// Only these names may be set before the call: bash gives many other names a meaning of their own (readonly, recomputed, or `$_`), and a value stored here would then differ from what bash holds.
const ASSIGN_OK = /^(?:SP|L|DIR|SCRIPTS|STACK_NAME)$/
const CD_ABS = /^cd\s+\/[\w./-]*$/
// the deploy script's own read-only actions also accept `cd ~/repos/NAME`: bash expands the ~ the same way the hook does
const CD_HOME = /^cd\s+~\/[\w./-]*$/

// bash keeps CR, NBSP and other odd characters inside a word where JS .trim() drops them, so the hook and the shell would read different lines.
// Anything but printable ASCII, tab and newline refuses the exception.
export const PLAIN_ASCII = /^[\x20-\x7e\t\n]*$/
// a `.` or `..` part is followed by the kernel through a symlink before it is folded, which a text tidy-up cannot know
export const hasDotPart = (path: string) => path.split('/').some(part => part === '.' || part === '..')
const TAIL_HEAD = /^(?:tail|head)(?:\s+-n\s*\d+|\s+-\d+)?$/

const blank = (p: Piece | undefined) => p !== undefined && p.text.trim() === ''
const lineEnd = (sep: string) => sep === '' || sep === '\n' || sep === ';'

// Returns the plain assignments before the call (the only variables the script path may use), or undefined when the line is not the plain shape.
// A `<<` anywhere refuses it: the splitter drops heredoc bodies it takes for text, and the shell would not see the line the same way.
// `rest` says what may follow the call: 'none' (the forwarder proof: nothing but an optional tail/head pipe) or 'more' (the deploy script's own
// read-only actions: later commands may follow once the call has finished, joined by ; newline or &&; they are judged on their own).
export function prodLineShape(command: string, parts: Piece[], index: number, fusedErr: boolean, rest: 'none' | 'more' = 'none'): Map<string, string> | undefined {
  if (command.includes('<<') || !PLAIN_ASCII.test(command)) return undefined
  const vars = new Map<string, string>()
  const ok = lineShape(parts, index, fusedErr, vars, rest)
  return ok ? vars : undefined
}

function lineShape(parts: Piece[], index: number, fusedErr: boolean, vars: Map<string, string>, rest: 'none' | 'more'): boolean {
  for (let i = 0; i < index; i += 1) {
    const { text, sep } = parts[i]
    const t = text.trim()
    if (t === '') {
      // a blank line, or the second "&" of "&&" (checked where the first one is)
      if (sep === ';' || sep === '\n') continue
      if (sep === '&' && i > 0 && parts[i - 1].sep === '&' && !blank(parts[i - 1])) continue
      return false
    }
    const a = ASSIGN.exec(t)
    if (!(a ? ASSIGN_OK.test(a[1]) : (CD_ABS.test(t) || (rest === 'more' && CD_HOME.test(t))) && !hasDotPart(t.slice(2).trim()))) return false
    if (a) vars.set(a[1], a[2].replace(/^(['"])(.*)\1$/, '$2'))
    if (sep === '&') {
      if (!(blank(parts[i + 1]) && parts[i + 1].sep === '&')) return false // a lone & puts it in the background
    } else if (sep !== ';' && sep !== '\n') return false
  }
  let last = index
  if (fusedErr) {
    if (parts[index + 1]?.text.trim() !== '1') return false // `2>&1` and nothing else: other words would reach the script
    last = index + 1
  }
  let sep = parts[last].sep
  let k = last + 1
  if (sep === '|') {
    const next = parts[k]
    if (!next || !TAIL_HEAD.test(next.text.trim())) return false
    sep = next.sep
    k += 1
  }
  if (rest === 'more') return lineEnd(sep) || (sep === '&' && blank(parts[k]) && parts[k].sep === '&')
  if (!lineEnd(sep)) return false
  return parts.slice(k).every(p => blank(p) && (p.sep === '' || p.sep === '\n'))
}

// ---- the forwarder script: every line must be one of these, so it only passes its second argument on ----

const FORWARDER_LINES: RegExp[] = [
  /^#(?!!).*$/,
  /^set -[euo]+(?: pipefail)?$/,
  /^T="\$1"; CMD="\$2"; U="\$\{3-82\}"$/,
  /^T="\$1"; CMD="\$2"; U="\$\{3:-82\}"$/,
  /^cd (?:~|\$HOME)\/repos\/[\w-]+ \|\| exit 1$/,
  /^source scripts\/portainer-deploy\.sh "\$T"$/,
  /^auth >\/dev\/null 2>&1 \|\| \{ echo "auth failed"; exit 1; \}$/,
  /^cid="\$\(find_service_container "\$\{STACK_NAME\}_php"\)"$/,
  /^\[ -n "\$cid" \] \|\| \{ echo "no php container for \$STACK_NAME"; exit 1; \}$/,
  /^echo "\[rexec\] stack=\$STACK_NAME portainer=\$PORTAINER_URL" >&2$/,
  /^container_exec "\$cid" "\$CMD" "\$U"$/,
]

const SHEBANGS = new Set(['#!/usr/bin/env bash', '#!/bin/bash'])

// the lines both the plain check and the deploy-script lookup read, so they cannot disagree about which line is the cd
const forwarderLines = (text: string): string[] => text.split('\n').map(l => l.trim()).filter(Boolean)

export function forwarderIsPlain(text: string): boolean {
  if (!PLAIN_ASCII.test(text)) return false
  // a `#!` line is the interpreter when the script is run without `bash` in front, so line 1 may name only bash; later `#!` lines are not comments we trust
  const first = text.split('\n')[0]
  if (first.startsWith('#!') && !SHEBANGS.has(first)) return false
  // no indented line: the real forwarders have none, and one would be read differently by a line match and by a person
  if (text.split('\n').some(l => /^\s/.test(l))) return false
  const lines = forwarderLines(text)
  if (lines.length === 0 || lines.length > 40) return false
  // the repo's own portainer-deploy.sh line is the only file it may source; any other `source` or `.` runs code we did not read
  if (lines.some(l => /^(?:source|\.)\s/.test(l) && l !== 'source scripts/portainer-deploy.sh "$T"')) return false
  if (lines.filter(l => l.startsWith('source ')).length > 1) return false
  if (!lines.every(l => (l.startsWith('#!') ? l === first : FORWARDER_LINES.some(r => r.test(l))))) return false
  // the cd that picks the repo must come before the source line, and stop the script when it fails
  if (lines.filter(l => l.startsWith('cd ')).length !== 1) return false
  const cdAt = lines.findIndex(l => l.startsWith('cd '))
  const srcAt = lines.findIndex(l => l.startsWith('source '))
  if (srcAt >= 0 && !(cdAt >= 0 && cdAt < srcAt)) return false
  // exactly one thing runs in the container, and it is the second argument
  return lines.filter(l => l.startsWith('container_exec ')).length === 1
}

// ---- the remote commands: each must be a read ----

// The remote environment words a read may set, each with the values it may take: HOME and COMPOSER_HOME only to the two scratch folders
// (they decide where a tool keeps its cache and which config it loads), the rest to a plain name.
const ENV_OK: Record<string, RegExp> = {
  HOME: /^\/tmp$/,
  COMPOSER_HOME: /^\/tmp\/composer$/,
  TZ: /^[A-Za-z_/+-]{1,40}$/,
  LANG: /^(?:C|C\.UTF-8|en_US\.UTF-8)$/,
  LC_ALL: /^(?:C|C\.UTF-8|en_US\.UTF-8)$/,
}
const PLAIN = /^[\w./:,@%+=-]*$/

const PLAIN_READERS = new Set(['echo', 'printf', 'true', 'pwd', 'ls', 'cat', 'head', 'tail', 'grep', 'egrep', 'fgrep', 'cut', 'wc', 'tr', 'stat', 'du', 'df', 'test', '[', 'uname', 'whoami', 'id', 'basename', 'dirname', 'readlink', 'realpath'])

const DRUSH_SUBCOMMANDS = new Set(['status', 'st', 'core:status', 'core-status', 'state:get', 'sget', 'watchdog:show', 'ws', 'wd-show', 'pm:list', 'pml', 'pm-list', 'pm:security', 'config:get', 'cget', 'config:status', 'cst', 'core:requirements', 'requirements', 'sqlq', 'sql:query', 'sql-query', 'version'])
const DRUSH_GLOBAL = /^(?:--no-ansi|--ansi|-v|-vv|-vvv|--verbose|--quiet|-q|--uri=[\w.:/-]+)$/
const DRUSH_OPTION = /^--(?:format|fields|field|filter|severity|count|type|status|uri|package|extra-sort|limit|offset)=[\w .,:*@/-]*$|^--(?:pipe|full|no-ansi|ansi|extended|detail)$/
const SQL_READ = /^\s*(?:select|show|describe|desc|explain\s+select)\b/i
const SQL_BAD = /\b(?:into|outfile|dumpfile|for\s+update|lock\s+in\s+share|sleep|benchmark|get_lock|release_lock|load_file|call|set|handler|delete|update|insert|replace|merge|truncate|alter|drop|create|grant|revoke|analyze|optimize|repair|lock|unlock|nextval|setval|lo_export|lo_import|master_pos_wait|sys_exec|sys_eval|pg_\w+)\b|;|--|\/\*|#/i

const COMPOSER_SUBCOMMANDS = new Set(['outdated', 'audit', 'show', 'diff', 'licenses', 'why', 'why-not', 'depends', 'prohibits', 'validate', 'status', '--version', 'about'])
const COMPOSER_OPTION = /^(?:--(?:direct|no-ansi|ansi|locked|no-dev|minor-only|patch-only|major-only|strict|no-interaction|quiet|installed|outdated|self|tree|latest|name-only|path|platform|available|all|abandoned=(?:report|ignore|fail)|format=(?:json|text|summary|plain)|no-check-publish|no-check-all|no-plugins|no-scripts)|-(?:o|D|m|a|i|t|l|N|s|v|vv|vvv))$/

const CURL_FLAG = /^(?:-[sSfLkvigI]+|--silent|--show-error|--fail|--location|--insecure|--head|--include|--compressed|--globoff)$/
// each option that takes a value, and what the value may be: a write-out format is %{name} pieces and plain text only (no %output{file}, no @file),
// a header or agent has no @ (curl reads a file for @), a time or count is a number
const CURL_VALUE: Record<string, RegExp> = {
  '-w': /^(?:%\{[a-z_]+\}|[\w .:/\\,-])*$/,
  '--write-out': /^(?:%\{[a-z_]+\}|[\w .:/\\,-])*$/,
  '-H': /^[^@\n\r]*$/,
  '--header': /^[^@\n\r]*$/,
  '-A': /^[^@\n\r]*$/,
  '--user-agent': /^[^@\n\r]*$/,
  '-m': /^\d+(?:\.\d+)?$/,
  '--max-time': /^\d+(?:\.\d+)?$/,
  '--connect-timeout': /^\d+(?:\.\d+)?$/,
  '--retry': /^\d+$/,
}

const findIsRead = (args: string[]) => !args.some(a => /^-(?:exec|execdir|ok|okdir|delete|fprint|fprint0|fprintf|fls)$/.test(a))

function drushIsRead(args: Word[]): boolean {
  let k = 0
  while (k < args.length && DRUSH_GLOBAL.test(args[k].v)) k += 1
  const sub = args[k]?.v
  if (!sub || !DRUSH_SUBCOMMANDS.has(sub)) return false
  const rest = args.slice(k + 1)
  const isSql = sub === 'sqlq' || sub === 'sql:query' || sub === 'sql-query'
  let sql = 0
  for (const a of rest) {
    if (a.v.startsWith('-')) {
      if (!DRUSH_OPTION.test(a.v)) return false
    } else if (isSql) {
      sql += 1
      if (!SQL_READ.test(a.v) || SQL_BAD.test(a.v)) return false
    } else if (!PLAIN.test(a.v)) return false
  }
  return !isSql || sql === 1
}

function composerIsRead(args: Word[]): boolean {
  let k = 0
  while (k < args.length && /^(?:--no-ansi|--ansi|-n|--no-interaction|-q|--quiet)$/.test(args[k].v)) k += 1
  if (args[k]?.v === '-d' || args[k]?.v === '--working-dir') {
    if (args[k + 1]?.v !== '/var/www/html') return false
    k += 2
  }
  const sub = args[k]?.v
  if (!sub || !COMPOSER_SUBCOMMANDS.has(sub)) return false
  return args.slice(k + 1).every(a => (a.v.startsWith('-') ? COMPOSER_OPTION.test(a.v) : PLAIN.test(a.v)))
}

function curlIsRead(args: Word[]): boolean {
  let urls = 0
  for (let k = 0; k < args.length; k += 1) {
    const a = args[k].v
    if (CURL_FLAG.test(a)) continue
    if (Object.hasOwn(CURL_VALUE, a)) {
      const val = args[k + 1]
      if (!val || !CURL_VALUE[a].test(val.v)) return false
      k += 1
      continue
    }
    if (a === '-o' || a === '--output') {
      if (args[k + 1]?.v !== '/dev/null') return false
      k += 1
      continue
    }
    if (a.startsWith('-')) return false
    if (!/^https?:\/\/[\w.:/?&=%+,@~-]+$/.test(a)) return false
    urls += 1
  }
  return urls >= 1
}

function phpIsRead(args: Word[]): boolean {
  if (args.length === 1) return ['-v', '-m', '-i', '--version', '--ini'].includes(args[0].v)
  if (args.length === 2 && args[0].v === '-r') {
    return /^echo\s+(?:PHP_[A-Z_]+|'[\w .:-]*'|"[\w .:-]*")(?:\s*,\s*(?:PHP_[A-Z_]+|'[\w .:-]*'|"[\w .:-]*"))*\s*;?$/.test(args[1].v)
  }
  return false
}

// date shows the time; -s, --set and a bare number (which sets it) are not allowed. -d takes a plain value, +FORMAT is the output shape.
function dateIsRead(args: Word[]): boolean {
  for (let k = 0; k < args.length; k += 1) {
    const a = args[k].v
    if (/^(?:-u|-I|-R|--utc|--universal|--rfc-email|--iso-8601(?:=[a-z]+)?)$/.test(a)) continue
    if (a === '-d' || a === '--date') {
      if (!/^[\w :+.-]+$/.test(args[k + 1]?.v ?? '')) return false
      k += 1
      continue
    }
    if (/^--date=[\w :+.-]+$/.test(a)) continue
    if (a.startsWith('+')) continue
    return false
  }
  return true
}

function commandIsRead(words: Word[]): boolean {
  let w = words
  while (w.length > 0 && /^[A-Za-z_]\w*=/.test(w[0].v) && w[0].quote === 'none') {
    const m = /^([A-Za-z_]\w*)=(.*)$/.exec(w[0].v) as RegExpExecArray
    if (!Object.hasOwn(ENV_OK, m[1]) || !ENV_OK[m[1]].test(m[2])) return false
    w = w.slice(1)
  }
  if (w.length === 0) return true
  const [head, ...args] = w
  if (head.quote !== 'none') return false
  const name = head.v.split('/').pop() as string
  if (head.v.includes('/') && !/^(?:\.\/)?(?:[\w.-]+\/)*(?:drush|composer)$/.test(head.v)) return false
  if (!/^[A-Za-z][\w.:[-]*$|^\[$/.test(name)) return false
  switch (name) {
    case 'cd':
      return args.length === 1 && PLAIN.test(args[0].v)
    case 'drush':
      return drushIsRead(args)
    case 'composer':
      return composerIsRead(args)
    case 'curl':
      return curlIsRead(args)
    case 'php':
      return phpIsRead(args)
    case 'find':
      return findIsRead(args.map(a => a.v))
    case 'date':
      return dateIsRead(args)
    case 'sort':
      return args.every(a => (a.v.startsWith('-') ? /^-[nrukfdhbsgiMV]+$/.test(a.v) : PLAIN.test(a.v) || a.quote !== 'none') && !/^-o|--output/.test(a.v))
    case 'uniq':
      return args.every(a => /^-[cdiuf]+$/.test(a.v))
    default:
      return PLAIN_READERS.has(name)
  }
}

// True only when every command in the remote line is a recognised read.
export function remoteIsRead(remote: string): boolean {
  const t = tokenize(remote, true)
  if (!t || t.commands.length === 0) return false
  return t.commands.every(commandIsRead)
}


// ---- the one file the forwarder sources: pinned by SHA-256 ----

// The repo folder the forwarder cd's into (`cd ~/repos/NAME || exit 1`, the only cd line) and the deploy script it then sources.
export function deployScript(forwarder: string, home: string): { repo: string; path: string } | undefined {
  if (!home.startsWith('/') || hasDotPart(home)) return undefined
  const cds = forwarderLines(forwarder).filter(l => l.startsWith('cd '))
  const m = cds.length === 1 ? /^cd (?:~|\$HOME)\/repos\/([\w-]+) \|\| exit 1$/.exec(cds[0]) : null
  return m ? { repo: m[1], path: `${home}/repos/${m[1]}/scripts/portainer-deploy.sh` } : undefined
}

// SHA-256 (hex) of the UTF-8 bytes of a text; undefined for text that is not valid Unicode.
export function sha256Hex(text: string): string | undefined {
  let bytes: number[]
  try {
    bytes = Array.from(unescape(encodeURIComponent(text)), c => c.charCodeAt(0))
  } catch {
    return undefined
  }
  const K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]
  const h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
  const bitLen = bytes.length * 8
  bytes.push(0x80)
  while (bytes.length % 64 !== 56) bytes.push(0)
  for (let i = 7; i >= 0; i -= 1) bytes.push(i >= 4 ? 0 : (bitLen >>> (i * 8)) & 0xff)
  const rotr = (x: number, n: number) => (x >>> n) | (x << (32 - n))
  const w = new Array<number>(64)
  for (let off = 0; off < bytes.length; off += 64) {
    for (let i = 0; i < 16; i += 1) w[i] = ((bytes[off + 4 * i] << 24) | (bytes[off + 4 * i + 1] << 16) | (bytes[off + 4 * i + 2] << 8) | bytes[off + 4 * i + 3]) | 0
    for (let i = 16; i < 64; i += 1) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3)
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10)
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0
    }
    let [a, b, c, d, e, f, g, hh] = h
    for (let i = 0; i < 64; i += 1) {
      const t1 = (hh + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0
      hh = g
      g = f
      f = e
      e = (d + t1) | 0
      d = c
      c = b
      b = a
      a = (t1 + t2) | 0
    }
    h[0] = (h[0] + a) | 0
    h[1] = (h[1] + b) | 0
    h[2] = (h[2] + c) | 0
    h[3] = (h[3] + d) | 0
    h[4] = (h[4] + e) | 0
    h[5] = (h[5] + f) | 0
    h[6] = (h[6] + g) | 0
    h[7] = (h[7] + hh) | 0
  }
  return h.map(x => (x >>> 0).toString(16).padStart(8, '0')).join('')
}

// SHA-256 of the scripts/portainer-deploy.sh versions a production proof may source, by the repo folder they live in. Only a folder listed here counts:
// a lane can make a folder of its own under ~/repos with a copy of a pinned script beside files we have not read. A different copy, or a pinned copy in
// another folder, keeps the command held. The list changes only by a reviewed PR; when a repo's script changes, so does this.
// Eduity is not listed: its script has no BASH_SOURCE guard (sourcing it runs its action dispatch), takes REPO_ROOT from $0 (the forwarder's scratch
// folder) and defines neither find_service_container nor container_exec. It gets a pin after it has a source guard and the helpers.
export type DeployPins = Record<string, readonly string[]>
export const DEPLOY_PINS: DeployPins = {
  'Chattanooga-Digital': ['3224c8b19ee7e7f9e0edf30b16c789a7a45eaf71422ef20843127f5077e8f527'], // main (aed358a)
}

export const deployIsPinned = (repo: string, text: string, pins: DeployPins = DEPLOY_PINS): boolean => {
  const h = sha256Hex(text)
  return h !== undefined && Object.prototype.hasOwnProperty.call(pins, repo) && pins[repo].includes(h)
}

// ---- the dotenv file the pinned script loads ----
// portainer-deploy.sh's load_env exports every KEY=value line of the repo's dotenv file that the caller has not set, so a key such as PYTHONPATH,
// LD_PRELOAD, BASH_ENV or PORTAINER_URL_OVERRIDE would reach the python3 and curl it runs while it holds the production login. The hook reads the key
// NAMES only (envKeyLines keeps what is before the first `=` and drops the values at once; nothing else of the file is held or shown) and allows a read
// only when every key is one of these. An allowlist: a new key keeps proofs held until a reviewed PR adds it. Values are not checked, so a changed
// value of an allowed key (a Portainer address) is outside this check.
export const DEPLOY_ENV_KEYS: ReadonlySet<string> = new Set([
  'PROJECT_NAME', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_ROOT_PASSWORD', 'DB_HOST', 'DB_PORT', 'DB_DRIVER',
  'DRUPAL_ADMIN_USER', 'DRUPAL_ADMIN_PASS', 'DRUPAL_CMS_TAG', 'MARIADB_TAG', 'NGINX_TAG', 'NGINX_VHOST_PRESET', 'DRUPAL_PORT',
  'GITHUB_BACKUP_TOKEN', 'GITHUB_REPO', 'GREG_PASSWORD', 'SMTP_PASSWORD',
  'PORTAINER_URL', 'PORTAINER_USER', 'PORTAINER_PASSWORD', 'PORTAINER_ENDPOINT_ID',
  'PORTAINER_PROD_URL', 'PORTAINER_PROD_USER', 'PORTAINER_PROD_PASSWORD', 'PORTAINER_PROD_ENDPOINT_ID', 'PORTAINER_GIT_TOKEN_FILE',
  'UPTIMEROBOT_API_KEY', 'DRUPAL_GITLAB_TOKEN', 'DRUPAL_ORG_USER', 'DRUPAL_ORG_PASS',
  'GREG_NEXTCLOUD_STAGING_URL', 'GREG_NEXTCLOUD_STAGING_USER', 'GREG_NEXTCLOUD_STAGING_PASS',
  'NEXTCLOUD_PROD_URL', 'NEXTCLOUD_PROD_USER', 'NEXTCLOUD_PROD_PASS', 'NEXTCLOUD_PROD_APP_PASSWORD',
])

// The key part of each line load_env would look at (blank lines and lines starting with `#` are skipped as it skips them), values dropped.
export const envKeyLines = (text: string): string =>
  text
    .split('\n')
    .filter(l => l !== '' && !l.startsWith('#'))
    .map(l => {
      const at = l.indexOf('=')
      return at < 0 ? l : l.slice(0, at)
    })
    .join('\n')

// Strict on purpose: a key part that is not exactly an allowed name (spaces, a CR, a non-ASCII character, nothing before the `=`) is held, though
// load_env would skip it, so the hook never has to agree with bash about what counts as a name.
export const deployEnvIsPlain = (keyLines: string, allowed: ReadonlySet<string> = DEPLOY_ENV_KEYS): boolean =>
  keyLines.split('\n').every(k => k === '' || allowed.has(k))
