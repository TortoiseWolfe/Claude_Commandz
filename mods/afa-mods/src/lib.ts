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

export type Hold = { kind: string; hard: boolean; segment: string }
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

// Which dangerous patterns a Bash command holds. Best effort, a net and not a
// parser: it splits on shell separators and looks inside sh -c. `hard` ones (force push,
// production deploy) are refused outright where nobody can be asked.
export function classify(command: string, depth = 0, env: ScratchEnv = {}): Hold[] {
  return classifyWithEnv(command, depth, env)
}

function classifyWithEnv(command: string, depth: number, env: ScratchEnv): Hold[] {
  const holds: Hold[] = []
  for (const raw of segmentsOf(command)) {
    const seg = strip(raw).replace(/^\\/, '')
    if (seg === '') continue
    const wrapped = /^(?:ba|z)?sh\s+-c\s+(.*)$/.exec(seg)
    if (wrapped && depth < 3) {
      holds.push(...classifyWithEnv(wrapped[1].replace(/^["']|["']$/g, ''), depth + 1, env))
    } else if (depth < 3 && runsQuoted(seg)) {
      for (const piece of quotedPieces(seg)) holds.push(...classifyWithEnv(piece, depth + 1, env))
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
      if (r && f && !ownScratchTargets(command, env, depth).some(group => group.segment === seg)) {
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
  const assignments = new Map<string, string | null>()
  if (env.tmpdir) assignments.set('TMPDIR', env.tmpdir)
  const mktemp = /(?:^|[;&|]\s*)([A-Za-z_]\w*)=\$\(\s*mktemp(?:\s+[^)]*)?\)/g
  for (const match of command.matchAll(mktemp)) assignments.set(match[1], null)
  const literals = /(?:^|[;&|]\s*)(?:export\s+)?([A-Za-z_]\w*)=("[^"]*"|'[^']*'|[^\s;&|]+)/g
  for (const match of command.matchAll(literals)) {
    if (!match[2].startsWith('$(')) assignments.set(match[1], unquote(match[2]))
  }
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
