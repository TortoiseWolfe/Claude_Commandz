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

// ---- the whole command line around the call ----
// The hook reads the forwarder before the command runs, so nothing else in the command may touch that file or run beside it:
// only plain assignments or `cd /abs/path` before the call (joined by ; newline or &&), then the call, then optionally 2>&1,
// then optionally ONE pipe into tail/head with a number. Anything else is held.

type Piece = { text: string; sep: string }

const ASSIGN = /^([A-Za-z_]\w*)=([\w./:,@%+=~-]*|'[\w ./:,@%+=~-]*'|"[\w ./:,@%+=~-]*")$/
const ASSIGN_BAD = /^(?:PATH|HOME|SHELL|BASH_ENV|ENV|IFS|CDPATH|SHELLOPTS|BASHOPTS|PS4|PROMPT_COMMAND|GLOBIGNORE|BASH_XTRACEFD|FPATH|LD_\w*|BASH_FUNC_\w*)$/
const CD_ABS = /^cd\s+\/[\w./-]*$/
const TAIL_HEAD = /^(?:tail|head)(?:\s+-n\s*\d+|\s+-\d+)?$/

const blank = (p: Piece | undefined) => p !== undefined && p.text.trim() === ''
const lineEnd = (sep: string) => sep === '' || sep === '\n' || sep === ';'

// Returns the plain assignments before the call (the only variables the script path may use), or undefined when the line is not the plain shape.
// A `<<` anywhere refuses it: the splitter drops heredoc bodies it takes for text, and the shell would not see the line the same way.
export function prodLineShape(command: string, parts: Piece[], index: number, fusedErr: boolean): Map<string, string> | undefined {
  if (command.includes('<<')) return undefined
  const vars = new Map<string, string>()
  const ok = lineShape(parts, index, fusedErr, vars)
  return ok ? vars : undefined
}

function lineShape(parts: Piece[], index: number, fusedErr: boolean, vars: Map<string, string>): boolean {
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
    if (!(a ? !ASSIGN_BAD.test(a[1]) : CD_ABS.test(t))) return false
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
  if (!lineEnd(sep)) return false
  return parts.slice(k).every(p => blank(p) && (p.sep === '' || p.sep === '\n'))
}

// ---- the forwarder script: every line must be one of these, so it only passes its second argument on ----

const FORWARDER_LINES: RegExp[] = [
  /^#.*$/,
  /^set -[euo]+(?: pipefail)?$/,
  /^T="\$1"; CMD="\$2"; U="\$\{3-82\}"$/,
  /^T="\$1"; CMD="\$2"; U="\$\{3:-82\}"$/,
  /^cd (?:~|\$HOME)\/repos\/[\w-]+(?: \|\| exit 1)?$/,
  /^source scripts\/portainer-deploy\.sh "\$T"$/,
  /^auth >\/dev\/null 2>&1 \|\| \{ echo "auth failed"; exit 1; \}$/,
  /^cid="\$\(find_service_container "\$\{STACK_NAME\}_php"\)"$/,
  /^\[ -n "\$cid" \] \|\| \{ echo "no php container for \$STACK_NAME"; exit 1; \}$/,
  /^echo "\[rexec\] stack=\$STACK_NAME portainer=\$PORTAINER_URL" >&2$/,
  /^container_exec "\$cid" "\$CMD" "\$U"$/,
]

export function forwarderIsPlain(text: string): boolean {
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean)
  if (lines.length === 0 || lines.length > 40) return false
  // the repo's own portainer-deploy.sh line is the only file it may source; any other `source` or `.` runs code we did not read
  if (lines.some(l => /^(?:source|\.)\s/.test(l) && l !== 'source scripts/portainer-deploy.sh "$T"')) return false
  if (lines.filter(l => l.startsWith('source ')).length > 1) return false
  if (!lines.every(l => FORWARDER_LINES.some(r => r.test(l)))) return false
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

const PLAIN_READERS = new Set(['echo', 'printf', 'true', 'pwd', 'ls', 'cat', 'head', 'tail', 'grep', 'egrep', 'fgrep', 'cut', 'wc', 'tr', 'stat', 'du', 'df', 'test', '[', 'uname', 'whoami', 'id', 'hostname', 'basename', 'dirname', 'readlink', 'realpath'])

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
