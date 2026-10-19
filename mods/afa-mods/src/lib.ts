// Pure helpers for the three mods: text in, text out, no engine calls.

export type Band = { lanes: number; inbox: number; client: number }

// Open lines (- [ ]) in muse-inbox.md. Client mail and client alerts are
// counted apart from the rest so a line never shows twice.
export function countInbox(md: string): { inbox: number; client: number } {
  let inbox = 0
  let client = 0
  for (const line of md.split('\n')) {
    if (!/^- \[ \]/.test(line)) continue
    if (/Client mail:|Client alert:/.test(line)) client += 1
    else inbox += 1
  }
  return { inbox, client }
}

// Lane rows from the last `days` days: { name, report } from index.tsv
// (date, name, folder, model, billing, prompt, report, ...).
export function recentLanes(
  tsv: string,
  nowMs: number,
  days = 3,
): { date: string; name: string; report: string }[] {
  const cutoff = new Date(nowMs - days * 86_400_000).toISOString().slice(0, 10)
  const rows: { date: string; name: string; report: string }[] = []
  for (const line of tsv.split('\n')) {
    const c = line.split('\t')
    if (c.length < 2 || !/^\d{4}-\d{2}-\d{2}$/.test(c[0]) || c[0] < cutoff) continue
    rows.push({ date: c[0], name: c[1], report: c[6] ?? '-' })
  }
  return rows
}

// out.log of a headless lane ends with "exit <code>" when the process is done.
export function hasExitLine(log: string): boolean {
  const last = log.trimEnd().split('\n').pop() ?? ''
  return /^exit \d+$/.test(last)
}

export function bandText(prepRan: boolean, band: Band): string {
  const parts: string[] = []
  if (!prepRan) parts.push('/prep not run')
  if (band.lanes > 0) parts.push(`${band.lanes} lane${band.lanes === 1 ? '' : 's'} open`)
  if (band.inbox > 0) parts.push(`Muse inbox: ${band.inbox} open`)
  if (band.client > 0) parts.push(`Client mail: ${band.client} new`)
  return parts.length > 0 ? parts.join(' | ') : 'hub quiet'
}

export type Weather = { kind: 'clear' | 'showers' | 'storm'; text: string }

// Clear under 40%, showers 40-60%, storm past 60%. The hub hands off past 60%;
// every other folder hands off past 80%.
export function weather(percent: number, isHub: boolean): Weather {
  const kind = percent < 40 ? 'clear' : percent <= 60 ? 'showers' : 'storm'
  const handOff = (isHub && percent > 60) || percent > 80
  return {
    kind,
    text: `Weather: ${kind} ${percent}%${handOff ? ' - hand off and end' : ''}`,
  }
}

// Every rule classify can raise. `add` takes only these, and a test fails when one has no description of its
// targets (touches.ts RULE_SAMPLES) or no plain words (explain), so a new rule cannot ship undescribed.
export const HELD_KINDS = [
  'rm -rf',
  'docker system prune',
  'docker image prune',
  'docker volume prune',
  'docker volume rm',
  'docker compose down -v',
  'git push --force',
  'production deploy',
  'tmux shared lanes',
] as const
export type HeldKind = (typeof HELD_KINDS)[number]

export type Hold = { kind: string; hard: boolean; segment: string; container?: Mapped[] }
export type ScratchEnv = { home?: string; tmpdir?: string }

const strip = (s: string) =>
  s
    .trim()
    .replace(/^(?:\w+=\S*\s+)+/, '')
    .replace(/^(?:sudo|command|time)\s+/, '')

// Words a heredoc body is run as shell for: the body of `cat <<EOF` is text being written, the body of `bash <<EOF` is run.
const SHELL_WORDS = new Set(['sh', 'bash', 'zsh', 'dash', 'ksh', 'ssh', 'su', 'eval', 'source', '.'])
// Commands that only show, search or send text: a deploy command inside their arguments does nothing.
const TEXT_CARRIERS = new Set(['echo', 'printf', 'cat', 'grep', 'egrep', 'fgrep', 'rg', 'ag', 'sed', 'awk', 'head', 'tail', 'wc', 'less', 'more', 'tmux', 'screen', 'diff', 'tee'])
// Commands that run the text they are given, so a quoted deploy command inside their arguments is real.
const RUNS_ITS_ARGS = new Set(['ssh', 'eval', 'xargs', 'watch', 'su', 'sh', 'bash', 'zsh', 'dash', 'ksh'])

const firstWord = (seg: string) => (strip(seg).replace(/^\\/, '').split(/\s+/)[0] ?? '').split('/').pop() ?? ''

function tmuxInvocation(tokens: string[]): { command: string; index: number; privateSocket: boolean } {
  let index = 1
  let privateSocket = false
  while (index < tokens.length) {
    const arg = tokens[index]
    if (arg === '-L' || arg === '-S') {
      privateSocket = true
      index += 2
    } else if (/^-[LS]=?.+/.test(arg)) {
      privateSocket = true
      index += 1
    } else if (arg === '-f' || arg === '-T') {
      index += 2
    } else if (/^-[2CluvVND]+$/.test(arg)) {
      index += 1
    } else if (arg.startsWith('-')) {
      index += 1
    } else {
      return { command: arg, index, privateSocket }
    }
  }
  return { command: '', index, privateSocket }
}

// Heredoc bodies that only write a file (cat, tee, python ... <<EOF) are not commands: drop them. A body fed to a shell is kept.
function withoutTextHeredocs(command: string): string {
  const lines = command.split('\n')
  const out: string[] = []
  for (let i = 0; i < lines.length; i += 1) {
    out.push(lines[i])
    const m = /<<-?\s*(['"]?)([A-Za-z_]\w*)\1/.exec(lines[i])
    if (!m || /<<</.test(lines[i])) continue
    const end = lines.findIndex((l, j) => j > i && l.trim() === m[2])
    if (end < 0) continue
    const lead = lines[i].slice(0, m.index).split(/&&|\|\||[;&|]/).pop() ?? ''
    const after = lines[i].slice(m.index + m[0].length).replace(/\d*>&-?\d*|&>>?/g, '')
    // a shell anywhere in the lead (`sudo bash`, `env bash`), or a pipe/and/semicolon after the marker (`cat <<EOF | bash`): the body is run
    const plain = (w: string) => (w.replace(/^(?:\$\(|[<>]?\(|`|\{)+/, '').replace(/["'\\]/g, '').split('/').pop() ?? '')
    const runs = (w: string) => SHELL_WORDS.has(plain(w)) || RUNS_ITS_ARGS.has(plain(w)) || WRAPPERS.has(plain(w))
    const shellIn = (s: string) => s.split(/\s+/).some(runs) || /^(?:docker(?:\s+compose)?|podman|kubectl)\s+(?:exec|run)\b/.test(s.trim())
    // `git commit -m "$(cat <<'EOF' ... EOF )"`: only a known text sink (git commit, gh pr/issue/release) takes the body as one message word; anywhere else it may be run, so it is kept
    const textSink = /^\s*(?:\w+=\S*\s+)*(?:git(?:\s+-C\s+\S+)?\s+commit|gh\s+(?:pr|issue|release)\s+\S+)\b/.test(lead)
    const quotedMessage = textSink && /\$\(\s*(?:cat|tee)\s*$/.test(lead) && ((lead.replace(/\\./g, '').match(/"/g) ?? []).length % 2 === 1)
    const leadRest = quotedMessage ? lead.replace(/\$\(\s*(?:cat|tee)\s*$/, '') : lead
    // fail closed: the body is dropped only when nothing around the marker could run it (no shell word, pipe, substitution or backtick)
    if (shellIn(lead) || shellIn(after) || /[|;&`]|[$<>]\(/.test(after) || /[`]|[$<>]\(/.test(leadRest)) continue
    i = end // skip the body and its delimiter line
  }
  return out.join('\n')
}

// The command split at shell separators (& | ; newline ( ) ` $( ), but not inside quotes: "a; b" is one word. Inside double
// quotes, $( ) and backticks still run, so they still split. Heredoc bodies that only write a file are left out.
export function segmentsOf(command: string): string[] {
  const text = withoutTextHeredocs(command)
  const out: string[] = []
  let cur = ''
  let q: '' | "'" | '"' = ''
  const resume: { close: string; depth: number }[] = [] // a double quote waiting for its $( ) or ` ` to close
  const flush = () => {
    out.push(cur)
    cur = ''
  }
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i]
    if (q === "'") {
      cur += c
      if (c === "'") q = ''
      continue
    }
    if (c === '\\') {
      cur += c + (text[i + 1] ?? '')
      i += 1
      continue
    }
    if (q === '"') {
      if (c === '"') {
        q = ''
        cur += c
      } else if (c === '`' || (c === '$' && text[i + 1] === '(')) {
        flush()
        resume.push({ close: c === '`' ? '`' : ')', depth: 0 })
        q = ''
        if (c === '$') i += 1
      } else cur += c
      continue
    }
    const top = resume.at(-1)
    if (top) {
      if (c === '(' && top.close === ')') top.depth += 1
      if (c === top.close && top.depth > 0 && c === ')') {
        top.depth -= 1
        flush()
        continue
      }
      if (c === top.close) {
        flush()
        resume.pop()
        q = '"'
        continue
      }
    }
    if (c === "'" || c === '"') {
      q = c
      cur += c
    } else if (c === '$' && text[i + 1] === '(') {
      flush()
      i += 1
    } else if ('&|;\n()`'.includes(c)) flush()
    else cur += c
  }
  flush()
  return out
}

// A quoted piece that is one word (`"--prod"`) is an argument the shell would unquote, so it stays; a quoted message with spaces is text and goes.
const QUOTED = /'([^']*)'|"((?:\\.|[^"\\])*)"/g
const withoutQuoted = (seg: string) => seg.replace(QUOTED, (_m, a?: string, b?: string) => ((a ?? b ?? '') !== '' && /^\S+$/.test(a ?? b ?? '') ? (a ?? b ?? '') : ''))

// Commands that run the quoted text they are given (`ssh host "a; b"`, `docker exec c "..."`): each quoted piece is a command line to judge too.
const WRAPPERS = new Set(['env', 'nohup', 'exec', 'nice', 'ionice', 'stdbuf', 'doas', 'timeout', 'setsid', 'chrt'])
// Fail closed: behind a wrapper (`env -i A=b ssh ...`, `timeout 5 bash -c ...`) we do not try to work out which word runs, so its quoted text is read as commands.
const runsQuoted = (seg: string) =>
  RUNS_ITS_ARGS.has(firstWord(seg)) || WRAPPERS.has(firstWord(seg)) || /^(?:docker(?:\s+compose)?|podman|kubectl)\s+(?:exec|run)\b/.test(strip(seg))
const quotedPieces = (seg: string): string[] => Array.from(seg.matchAll(QUOTED), m => m[1] ?? m[2] ?? '').filter(s => s.trim() !== '')

// Would this segment ship to production? Judged on what the command runs: its quoted text is data unless the command runs
// its arguments, and a word with a "/" is a path (a folder called App--prod-lock or /srv/redeploy/prod is not a flag).
const PROD_FLAG = /^--prod(?:uction)?(?:=.*)?$/i
const PROD_WORD = /^(?:--[\w-]+=)?(?:--)?prod(?:uction)?$/i
function isProdDeploy(seg: string): boolean {
  const word = firstWord(seg)
  if (!/^[A-Za-z0-9_.-]+$/.test(word) || TEXT_CARRIERS.has(word)) return false
  const body = RUNS_ITS_ARGS.has(word) || /^(?:docker|kubectl)\s+(?:exec|run)\b/.test(seg) ? seg : withoutQuoted(seg)
  // the words as the shell would unquote them: no quotes, no backslashes. A "#" is not stripped as a comment: `vercel '#' --prod` runs.
  const words = body
    .split(/\s+/)
    .map(w => w.replace(/["'\\]/g, ''))
    .filter(Boolean)
  const args = words.filter(w => w.startsWith('-') || !w.includes('/'))
  if (args.some(w => PROD_FLAG.test(w))) return true
  return /redeploy/i.test(word) || args.some(w => /redeploy/i.test(w)) ? args.some(w => PROD_WORD.test(w)) : false
}

// ---- docker run -v / --mount: what a path inside the container is on this machine ----

// A mount of a `docker run`: `kind` says whether its source is a folder on this machine ('host') or a Docker
// volume ('volume': named, anonymous or tmpfs). The source is the text as written, not yet resolved.
export type Mount = { source: string; target: string; kind: 'host' | 'volume' }
type Scope = { home: string; cwd?: string; vars: Map<string, string | null>; mounts?: Mount[] }

// One path an rm names inside a container, seen through the mounts: where it really is.
export type Mapped =
  | { path: string; kind: 'host'; mountAt: string; folder: string; host: string } // folder: the mount's source here; host: the path here (may hold a pattern)
  | { path: string; kind: 'volume'; mountAt: string; volume: string } // a Docker volume, not a folder on this machine
  | { path: string; kind: 'unresolved'; mountAt: string; source: string; why: string } // a host source that cannot be worked out
  | { path: string; kind: 'uncovered'; why: string } // no mount covers it

const newScope = (command: string, env: ScratchEnv): Scope => ({ home: env.home ?? '', vars: assignmentsOf(command, env) })

const SUBSTITUTION = /\$\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)|`[^`]*`/g
const SUBST_WORD = '$CMDSUBST'

// The rest of each docker run line with every $( ) and backtick substitution replaced by one unresolvable word
function foldSubstitutionsInDockerRuns(command: string): string {
  return command.replace(/((?:^|[\s;&|(])(?:(?:docker|podman)\s+(?:(?:compose|container)\s+)?run|docker-compose\s+run)\b)([^\n]*)/g, (_m, head: string, tail: string) =>
    head + tail.replace(SUBSTITUTION, SUBST_WORD),
  )
}

const DOCKER_RUN = /^(?:docker|podman)\s+(?:compose\s+|container\s+)?run\b|^docker-compose\s+run\b/

// `cd /full/path` before the command gives it a known $PWD. Anything that may have run in another shell (a "(" in the
// command, pushd, popd, a path that is not plain) leaves $PWD unknown, so a source that uses it keeps holding.
function trackCd(seg: string, command: string, scope: Scope): void {
  if (scope.mounts) return // inside a container its own cd says nothing about this machine
  const m = /^(?:(cd)|pushd|popd)\b\s*(.*)$/.exec(seg)
  if (!m) return
  const arg = unquote(m[2].trim())
  const dir = m[1] && arg !== '' && !/\s/.test(arg) && !command.includes('(') ? resolveHostPath(arg, scope) : undefined
  scope.cwd = dir && 'path' in dir ? dir.path : undefined
}

// A path word on this machine: ~ and $HOME, $PWD after a cd, and variables the command set to a plain value. `why` says what blocked it.
function resolveHostPath(word: string, scope: Scope): { path: string } | { why: string } {
  let w = unquote(word)
  if (/`|\$\(/.test(w)) return { why: 'is built by another command when the command runs' }
  if (w === '~' || w.startsWith('~/')) {
    if (!scope.home) return { why: 'starts with ~ and this session does not say where home is' }
    w = scope.home + w.slice(1)
  } else if (w.startsWith('~')) return { why: "starts with another user's ~" }
  let missing = ''
  if (w.includes(SUBST_WORD)) return { why: 'is built by another command when the command runs' }
  w = w.replace(/\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))/g, (_m, a?: string, b?: string) => {
    const name = (a ?? b) as string
    const value = name === 'HOME' ? scope.home || undefined : name === 'PWD' ? scope.cwd : scope.vars.get(name)
    if (value == null) missing = missing || name
    return value ?? ''
  })
  if (missing) {
    return {
      why:
        missing === 'PWD'
          ? 'uses $PWD, which depends on the folder the shell is in, and no `cd` to a full path came before it'
          : `goes through $${missing}, which is not set to a plain path earlier in this command (it may be empty, set elsewhere, or made by mktemp)`,
    }
  }
  if (/[$`]/.test(w)) return { why: 'uses a shell expansion that is only worked out when the command runs' }
  if (!w.startsWith('/')) {
    if (!scope.cwd) return { why: 'is a relative path, which depends on the folder the shell is in' }
    w = `${scope.cwd}/${w}`
  }
  return { path: normalizePath(w) }
}

// Split `SRC:DST:opts` at the colons that are not inside ${...}
function splitColons(spec: string): string[] {
  const parts: string[] = []
  let cur = ''
  let depth = 0
  for (let i = 0; i < spec.length; i += 1) {
    if (spec[i] === '$' && spec[i + 1] === '{') depth += 1
    else if (spec[i] === '}' && depth > 0) depth -= 1
    if (spec[i] === ':' && depth === 0) {
      parts.push(cur)
      cur = ''
    } else cur += spec[i]
  }
  parts.push(cur)
  return parts
}

// a source with a / ~ or $ in it, or starting with a dot (compose reads `.` and `./x` as a path), is a folder here; anything else is a volume name
const HOSTY = /[/~$]|^\./

// The mounts a `docker run` / `docker compose run` gives its container: -v, --volume and --mount, in the forms docker accepts.
function parseMounts(seg: string): Mount[] {
  const t = seg.split(/\s+/).map(unquote)
  const out: Mount[] = []
  const volume = (spec: string) => {
    const [first, second] = splitColons(spec)
    if (second === undefined) {
      if (first.startsWith('/')) out.push({ source: '', target: first, kind: 'volume' }) // anonymous volume
    } else if (second.startsWith('/')) out.push({ source: first, target: second, kind: HOSTY.test(first) ? 'host' : 'volume' })
  }
  const mount = (spec: string) => {
    const kv = new Map(spec.split(',').map(p => [p.slice(0, p.indexOf('=')), p.slice(p.indexOf('=') + 1)] as const))
    const type = kv.get('type') ?? kv.get('Type')
    const source = kv.get('source') ?? kv.get('src') ?? ''
    const target = kv.get('target') ?? kv.get('destination') ?? kv.get('dst')
    if (!target?.startsWith('/')) return
    // a volume made over a host folder with the local driver (volume-opt=device=/path) is that folder
    const device = /device=([^,]+)/.exec(spec)?.[1]
    if (device) out.push({ source: device, target, kind: 'host' })
    else if (type === 'bind') {
      if (source) out.push({ source, target, kind: 'host' })
    } else if (type === 'volume' || type === 'tmpfs') out.push({ source, target, kind: 'volume' })
    else out.push({ source, target, kind: HOSTY.test(source) ? 'host' : 'volume' })
  }
  for (let i = 1; i < t.length; i += 1) {
    const w = t[i]
    if ((w === '-v' || w === '--volume') && t[i + 1] !== undefined) volume(t[(i += 1)])
    else if (w === '--mount' && t[i + 1] !== undefined) mount(t[(i += 1)])
    else if (w.startsWith('--volume=')) volume(unquote(w.slice(9)))
    else if (w.startsWith('--mount=')) mount(unquote(w.slice(8)))
    else if (/^-v[^v-]/.test(w)) volume(unquote(w.slice(2)))
  }
  return out
}

// Split an rm path at its first pattern character: `base` is the folder the pattern expands in (the whole path when it has none).
export function globBase(path: string): { base: string; glob: boolean } {
  const g = path.search(/[*?[{]/)
  if (g < 0) return { base: path, glob: false }
  return { base: path.slice(0, path.lastIndexOf('/', g)) || '/', glob: true }
}

const under = (parent: string, child: string) => (parent === '/' ? child !== '/' : child.startsWith(`${parent}/`))

// What each path an rm names is, given the mounts of the container it runs in. The mount with the longest target
// holds the path; a mount sitting below the path (or below the folder a pattern expands in) is reached too, because rm
// goes down into it.
function mapRmTargets(targets: string[], mounts: Mount[], scope: Scope): Mapped[] {
  const out: Mapped[] = []
  for (const path of targets) {
    if (/[$`]/.test(path) || !path.startsWith('/')) {
      out.push({
        path,
        kind: 'uncovered',
        why: /[$`]/.test(path)
          ? 'the path goes through a variable the container fills in when it runs'
          : "the path is relative to the container's working directory, which this command does not show",
      })
      continue
    }
    const p = normalizePath(path)
    const { base } = globBase(p)
    const at = (m: Mount) => normalizePath(m.target)
    const cover = mounts
      .filter(m => at(m) === base || under(at(m), base))
      .sort((a, b) => at(b).length - at(a).length)[0]
    const below = mounts.filter(m => under(base, at(m)))
    const entries: Mapped[] = []
    for (const m of [...(cover ? [cover] : []), ...below]) {
      const mountAt = at(m)
      if (m.kind === 'volume') {
        entries.push({ path, kind: 'volume', mountAt, volume: m.source })
        continue
      }
      const r = resolveHostPath(m.source, scope)
      if ('why' in r) entries.push({ path, kind: 'unresolved', mountAt, source: m.source.replace(SUBST_WORD, '$(...)'), why: r.why })
      else {
        const rest = m === cover ? p.slice(mountAt === '/' ? 0 : mountAt.length) : ''
        entries.push({ path, kind: 'host', mountAt, folder: r.path, host: normalizePath(`${r.path}${rest}`) })
      }
    }
    out.push(
      ...(entries.length > 0
        ? entries
        : [{ path, kind: 'uncovered' as const, why: 'no -v or --mount of this command covers it, so it is a path inside the container itself (or in a volume a compose file adds)' }]),
    )
  }
  return out
}

// Nothing to ask about: a Docker volume is not a folder here; a host folder is judged as a host rm would be (own scratch only);
// a path no mount covers is the container's own, held unless it is scratch by the same text rule a host rm gets.
function containerPathIsHarmless(m: Mapped, env: ScratchEnv): boolean {
  if (m.kind === 'volume') return true
  if (m.kind === 'host') return isOwnScratch(m.host, new Map(), env)
  if (m.kind === 'uncovered') return isOwnScratch(m.path, new Map(), env)
  return false
}

// A lane nobody watches judges the host side: the host paths of a container rm as one host rm, or the reason it cannot.
export function hostRmSegment(h: Hold): { segment: string } | { reason: string } {
  if (!h.container) return { segment: h.segment }
  const hosts: string[] = []
  for (const m of h.container) {
    if (m.kind === 'host') hosts.push(m.host)
    else if (m.kind === 'unresolved') return { reason: `the mount "${m.source}:${m.mountAt}" is a host folder that ${m.why}, so what "${m.path}" deletes on this machine is unknown` }
    else if (m.kind === 'uncovered') return { reason: `"${m.path}" inside the container: ${m.why}` }
  }
  return { segment: `rm -rf ${hosts.join(' ')}` }
}

// Which dangerous patterns a Bash command holds. Best effort, a net and not a
// parser: it splits on shell separators and looks inside sh -c. `hard` ones (force push,
// production deploy) are refused outright where nobody can be asked.
export function classify(command: string, depth = 0, env: ScratchEnv = {}): Hold[] {
  const holds = classifyWithEnv(command, depth, env, newScope(command, env))
  // `docker run -v "$(pwd):/w" img sh -c 'rm ...'`: the splitter cuts the docker run in two at the substitution, so a second
  // look at a copy with each substitution named as one unknown word finds what the cut hid. Only ever adds holds.
  const folded = depth === 0 ? foldSubstitutionsInDockerRuns(command) : command
  if (folded === command) return holds
  const seen = new Set(holds.map(h => `${h.kind}\0${h.segment}`))
  return [...holds, ...classifyWithEnv(folded, depth, env, newScope(folded, env)).filter(h => !seen.has(`${h.kind}\0${h.segment}`))]
}

function classifyWithEnv(command: string, depth: number, env: ScratchEnv, scope: Scope): Hold[] {
  const holds: Hold[] = []
  for (const raw of segmentsOf(command)) {
    const seg = strip(raw).replace(/^\\/, '')
    if (seg === '') continue
    trackCd(seg, command, scope)
    // `docker run -v SRC:DST ...`: the text it runs sees its paths through these mounts
    const mounts = DOCKER_RUN.test(seg) ? parseMounts(seg) : scope.mounts
    const inner: Scope = { ...scope, mounts }
    const wrapped = /^(?:ba|z)?sh\s+-c\s+(.*)$/.exec(seg)
    if (wrapped && depth < 3) {
      holds.push(...classifyWithEnv(wrapped[1].replace(/^["']|["']$/g, ''), depth + 1, env, inner))
    } else if (depth < 3 && runsQuoted(seg)) {
      for (const piece of quotedPieces(seg)) holds.push(...classifyWithEnv(piece, depth + 1, env, inner))
    }
    const t = seg.split(/\s+/)
    const flags = t.filter(x => x.startsWith('-'))
    // the words the command itself gets: a quoted message with spaces (`git commit -m "fix push -f"`) is text, not words
    const bare = withoutQuoted(seg).split(/\s+/).filter(Boolean)
    const add = (kind: HeldKind, hard: boolean) => holds.push({ kind, hard, segment: seg })

    if (/^(?:\S*\/)?tmux$/.test(t[0])) {
      const invocation = tmuxInvocation(t)
      if (!invocation.privateSocket && invocation.command === 'kill-server') {
        add('tmux shared lanes', true)
      } else if (!invocation.privateSocket && invocation.command === 'kill-session') {
        const args = t.slice(invocation.index + 1)
        const targetsLanes = args.some(
          (arg, i) => (arg === '-t' && args[i + 1] === 'lanes') || /^-t=?lanes$/.test(arg),
        )
        if (targetsLanes) add('tmux shared lanes', true)
      }
    }
    if (/^(?:\S*\/)?rm$/.test(t[0])) {
      const r = flags.some(f => /^-[^-]*[rR]/.test(f) || f === '--recursive')
      const f = flags.some(f => /^-[^-]*f/.test(f) || f === '--force')
      if (r && f && scope.mounts && scope.mounts.length > 0) {
        // inside a container that has mounts: judge each path by what it really is on this machine
        const mapped = mapRmTargets(targetsOf(seg).map(unquote), scope.mounts, scope)
        if (mapped.some(m => !containerPathIsHarmless(m, env))) holds.push({ kind: 'rm -rf', hard: false, segment: seg, container: mapped })
      } else if (r && f && !ownScratchTargets(command, env, depth).some(group => group.segment === seg)) {
        add('rm -rf', false)
      }
    }
    if (/^docker\s+(system|volume|image)\s+prune\b/.test(seg)) add(`docker ${t[1]} prune` as HeldKind, false)
    if (/^docker\s+volume\s+(rm|remove)\b/.test(seg)) add('docker volume rm', false)
    if (/^(docker\s+compose|docker-compose)\b/.test(seg) && bare.includes('down')) {
      const after = bare.slice(bare.indexOf('down'))
      if (after.some(x => x === '--volumes' || /^-[^-]*v/.test(x))) add('docker compose down -v', false)
    }
    if (/^git\b/.test(seg) && bare.includes('push')) {
      const after = bare.slice(bare.indexOf('push'))
      if (after.some(x => x === '-f' || x.startsWith('--force') || /^\+\S/.test(x))) add('git push --force', true)
    }
    if (isProdDeploy(seg)) add('production deploy', true)
  }
  return holds
}

type OwnScratchGroup = { segment: string; targets: string[] }

// Returns targets from rm -rf segments that are safe scratch locations. This
// uses the same best-effort segmentation as classify, not a full shell parser.
export function ownScratchTargets(command: string, env: ScratchEnv = {}, depth = 0): OwnScratchGroup[] {
  const assignments = assignmentsOf(command, env)
  const groups: OwnScratchGroup[] = []
  for (const raw of segmentsOf(command)) {
    const seg = strip(raw).replace(/^\\/, '')
    if (seg === '') continue
    const wrapped = /^(?:ba|z)?sh\s+-c\s+(.*)$/.exec(seg)
    if (wrapped && depth < 3) {
      groups.push(
        ...ownScratchTargets(wrapped[1].replace(/^["']|["']$/g, ''), env, depth + 1).map(
          targets => ({ segment: seg, targets }),
        ),
      )
    }
    const t = seg.split(/\s+/)
    if (!/^(?:\S*\/)?rm$/.test(t[0])) continue
    const flags = t.filter(x => x.startsWith('-'))
    if (
      !flags.some(f => /^-[^-]*[rR]/.test(f) || f === '--recursive') ||
      !flags.some(f => /^-[^-]*f/.test(f) || f === '--force')
    ) continue
    const targets = t.slice(1).filter(x => !x.startsWith('-')).map(unquote)
    if (targets.length > 0 && targets.every(target => isOwnScratch(target, assignments, env))) {
      groups.push({ segment: seg, targets })
    }
  }
  return groups
}

// Variables the command sets itself: a literal value, or null for one set from `mktemp` (a fresh temp folder, path unknown).
function assignmentsOf(command: string, env: ScratchEnv): Map<string, string | null> {
  const assignments = new Map<string, string | null>()
  if (env.tmpdir) assignments.set('TMPDIR', env.tmpdir)
  const mktemp = /(?:^|[;&|]\s*)([A-Za-z_]\w*)=\$\(\s*mktemp(?:\s+[^)]*)?\)/g
  for (const match of command.matchAll(mktemp)) assignments.set(match[1], null)
  const literals = /(?:^|[;&|]\s*)(?:export\s+)?([A-Za-z_]\w*)=("[^"]*"|'[^']*'|[^\s;&|]+)/g
  for (const match of command.matchAll(literals)) {
    if (!match[2].startsWith('$(')) assignments.set(match[1], unquote(match[2]))
  }
  return assignments
}

export const unquote = (value: string) => value.replace(/^(["'])(.*)\1$/, '$2')

function isOwnScratch(
  input: string,
  assignments: Map<string, string | null>,
  env: ScratchEnv,
): boolean {
  if (!input) return false
  let path = input
  const variable = /^\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))(\/.*)?$/.exec(path)
  if (variable ? /[*?[\]{}]/.test(variable[3] ?? '') : /[*?[\]{}]/.test(input)) {
    return false
  }
  if (variable) {
    const name = variable[1] ?? variable[2]
    if (!assignments.has(name)) return false
    const value = assignments.get(name)
    if (value === null) {
      const suffix = normalizePath((variable[3] ?? '').replace(/^\//, ''))
      return !suffix.startsWith('../') && suffix !== '..' && !suffix.startsWith('/')
    }
    if (value === undefined) return false
    path = `${value}${variable[3] ?? ''}`
  } else if (path.startsWith('$')) {
    return false
  }

  if (path === '~' || path.startsWith('~/')) path = `${env.home ?? '~'}${path.slice(1)}`
  else if (path.startsWith('~')) return false
  if (/[$*?[\]{}]/.test(path)) return false
  const normalized = normalizePath(path)

  const cacheNames = new Set([
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    '.expo',
    '.turbo',
  ])
  const parts = normalized.split('/').filter(Boolean)
  // A cache name directly in home is user state, not scratch (~/.expo holds the Expo login).
  const parent = `${normalized.startsWith('/') ? '/' : ''}${parts.slice(0, -1).join('/')}`
  const homeLevel = parent === '~' || (env.home !== undefined && parent === normalizePath(env.home))
  if (
    (!homeLevel && cacheNames.has(parts.at(-1) ?? '')) ||
    (parts.at(-2) === 'node_modules' && parts.at(-1) === '.cache')
  ) return true

  const tmpRoots = ['/tmp', env.tmpdir ? normalizePath(env.tmpdir) : undefined].filter(
    (root): root is string => Boolean(root),
  )
  if (normalized.startsWith('/') && tmpRoots.some(
    root => normalized !== root && normalized.startsWith(`${root}/`),
  )) return true

  const laneRoot = `${normalizePath(`${env.home ?? '~'}/.claude/state/lanes`)}/`
  return normalized.startsWith(laneRoot) && normalized.slice(laneRoot.length).split('/').filter(Boolean).length >= 2
}

function normalizePath(path: string): string {
  const absolute = path.startsWith('/')
  const parts: string[] = []
  for (const part of path.split('/')) {
    if (!part || part === '.') continue
    if (part === '..' && parts.length > 0 && parts.at(-1) !== '..') parts.pop()
    else if (part === '..' && !absolute) parts.push(part)
    else if (part !== '..') parts.push(part)
  }
  return `${absolute ? '/' : ''}${parts.join('/')}` || (absolute ? '/' : '.')
}

// Non-flag words after the command word: what an rm names.
export function targetsOf(segment: string): string[] {
  return segment.split(/\s+/).slice(1).filter(x => !x.startsWith('-'))
}

// In plain words: what a held command does, and why it is held. Shown in the "Run it?" question
// and in every refusal, so nobody has to decode `rm -rf` from the log.
export function explain(hold: Hold): { what: string; why: string } {
  const vars = [...hold.segment.matchAll(/\$\{?([A-Za-z_]\w*)/g)].map(m => m[1])
  switch (hold.kind) {
    case 'rm -rf':
      if (hold.container) {
        return {
          what: 'deletes files inside a Docker container; a path under a mounted host folder is deleted on this machine too, with no undo',
          why: 'a mount ties the container path to a real folder here, and the held command does not say which until the mount is read',
        }
      }
      return {
        what: 'deletes the named files and folders and everything inside them, without asking for each one',
        why:
          vars.length > 0
            ? `there is no undo, and the path goes through $${vars[0]}: if $${vars[0]} is empty or unset the path collapses (for example "$${vars[0]}/build" becomes "/build") and the wrong place is deleted`
            : 'there is no undo, and a wrong or empty path deletes far more than meant',
      }
    case 'docker system prune':
    case 'docker image prune':
      return {
        what: 'deletes every unused Docker container, network and image on this machine',
        why: 'one Docker engine is shared by every project here, and another project may need what it removes',
      }
    case 'docker volume prune':
      return {
        what: 'deletes every Docker volume no running container uses (volumes hold databases and uploads)',
        why: 'the data in a deleted volume cannot be brought back',
      }
    case 'docker volume rm':
      return { what: 'deletes the named Docker volumes and the data in them', why: 'the data cannot be brought back' }
    case 'docker compose down -v':
      return {
        what: 'stops the compose stack and deletes its volumes (its database and uploads)',
        why: 'the data in a deleted volume cannot be brought back',
      }
    case 'git push --force':
      return {
        what: "overwrites the branch on the remote with this machine's history",
        why: 'commits other people or machines pushed can be lost',
      }
    case 'production deploy':
      return { what: 'ships to the live production site', why: 'production changes wait for the agreed window' }
    case 'tmux shared lanes':
      return hold.segment.includes('kill-server')
        ? {
            what: "closes every lane window (the tmux session 'lanes'), with no undo; it also closes every other session and window on the default tmux server",
            why: 'all lanes share the default tmux server, so resetting one server closes every lane',
          }
        : {
            what: "closes every lane window (the tmux session 'lanes'), with no undo",
            why: 'all lanes share this tmux session, so ending it closes every lane',
          }
    default:
      return { what: hold.kind, why: 'it is on the held list' }
  }
}

export type LaneEnv = { home: string; hand?: string; tmpdir?: string }

// A lane nobody watches (AFA_LANE_UNATTENDED=1, set by lane.sh) may delete only inside: its own hand-off
// folder (AFA_LANE_HAND) or a worktree under ~/repos/.worktrees. The path must be written out in full: no
// variable, no pattern, no relative path, no `..`. This check is on the words only; a symlink inside an
// allowed folder that points elsewhere is caught by realPathVerdict, which index.tsx runs on the resolved paths.
export function unattendedRoots(env: LaneEnv): string[] {
  const hand = env.hand ? normalizePath(env.hand) : undefined
  return [hand, `${normalizePath(env.home)}/repos/.worktrees`].filter((r): r is string => Boolean(r))
}

// The full paths an rm names, once the words check passed: what index.tsx hands to realpath.
export function unattendedRmPaths(segment: string, env: LaneEnv): string[] {
  const home = normalizePath(env.home)
  return targetsOf(segment)
    .map(unquote)
    .map(t => normalizePath(t === '~' || t.startsWith('~/') ? `${home}${t.slice(1)}` : t))
}

// `real` maps each named path to what realpath says it is; `roots` are the allowed folders, themselves resolved.
// A path that cannot be resolved (empty) is refused, so a failing realpath never opens the door.
export function realPathVerdict(
  real: Array<{ path: string; real: string }>,
  roots: string[],
): { ok: true } | { ok: false; reason: string } {
  for (const r of real) {
    if (!r.real) return { ok: false, reason: `"${r.path}" could not be resolved to its real location, so the delete is refused` }
    if (!roots.some(root => r.real !== root && r.real.startsWith(`${root}/`))) {
      return {
        ok: false,
        reason: `"${r.path}" really points to ${r.real} (through a link), which is outside this lane's own folder and its worktree`,
      }
    }
  }
  return { ok: true }
}

export function unattendedRmVerdict(segment: string, env: LaneEnv): { ok: true } | { ok: false; reason: string } {
  const targets = targetsOf(segment).map(unquote)
  const hand = env.hand ? normalizePath(env.hand) : undefined
  const home = normalizePath(env.home)
  const where = [
    hand ? `this lane's own folder (${hand})` : null,
    `a worktree under ${home}/repos/.worktrees`,
  ].filter(Boolean).join(', ')
  const refuse = (target: string, why: string) => ({
    ok: false as const,
    reason: `"${target}" ${why}. Delete only by a full path inside ${where}, or leave it and say so in your report.`,
  })
  if (targets.length === 0) return refuse('(nothing named)', 'names no path')
  const roots = unattendedRoots(env)
  for (const target of targets) {
    if (/[$`*?[\]{}]/.test(target)) {
      const v = /\$\{?([A-Za-z_]\w*)/.exec(target)
      return refuse(
        target,
        v
          ? `goes through $${v[1]}, which may be empty or unset, so the delete could land on the wrong folder`
          : 'uses a pattern that expands when it runs, so it could match more than meant',
      )
    }
    const path = target === '~' || target.startsWith('~/') ? `${home}${target.slice(1)}` : target
    if (!path.startsWith('/')) return refuse(target, 'is not a full path, so it depends on where the shell happens to be')
    if (path.split('/').includes('..')) return refuse(target, 'climbs out with "..", so it can leave the folder it starts in')
    const full = normalizePath(path)
    if (!roots.some(root => full !== root && full.startsWith(`${root}/`))) {
      return refuse(target, 'is outside the places a lane may clean up (or is one of those places itself)')
    }
  }
  return { ok: true }
}
