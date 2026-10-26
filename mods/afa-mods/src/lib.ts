// Pure helpers for the three mods: text in, text out, no engine calls.
import { type DeployPins, afterSourcedIsPlain, definedFunctions, deployEnvIsPlain, deployIsPinned, deployScript, forwarderIsPlain, hasDotPart, parseDeployCall, parseProdCall, prodLineShape, readOnlyActions, remoteIsRead } from './prodread'

export type Band = { lanes: number; inbox: number; client: number }

// Open lines (- [ ]) in hub-inbox.md. Client mail and client alerts are
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
  'docker run without limits',
  'git push --force',
  'production deploy',
  'tmux shared lanes',
] as const
export type HeldKind = (typeof HELD_KINDS)[number]

export type Hold = { kind: string; hard: boolean; segment: string; container?: Mapped[] }
// volumeDevices: named Docker volumes that are really a host folder (local driver, device=/path), read by the caller with docker volume inspect
// uninspectedVolumes: named volumes docker could not be asked about; they are held as unresolved, never resolved as text
// deployEnvKeys: names the repo's dotenv file may hold (default DEPLOY_ENV_KEYS; a test injects its own)
// deployPins: SHA-256 list, by repo folder, of the deploy scripts a production proof may source (default DEPLOY_PINS; a test injects its own)
// composeLimited: keys (composeKey) of the compose services whose file gives them a cpus limit, read by the caller
export type ScratchEnv = { home?: string; cwd?: string; tmpdir?: string; volumeDevices?: Record<string, string>; uninspectedVolumes?: string[]; composeLimited?: string[]; prodScripts?: Record<string, string>; prodWanted?: Set<string>; prodWantedEnv?: Set<string>; deployPins?: DeployPins; deployEnvKeys?: ReadonlySet<string> }

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
    const shellIn = (s: string) => s.split(/\s+/).some(runs) || /^(?:docker(?:\s+compose)?|docker-compose|podman|kubectl)\s+(?:exec|run)\b/.test(s.trim())
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
export function segmentsWithSeps(command: string): Array<{ text: string; sep: string }> {
  const text = withoutTextHeredocs(command)
  const out: Array<{ text: string; sep: string }> = []
  let cur = ''
  let q: '' | "'" | '"' = ''
  const resume: { close: string; depth: number }[] = [] // a double quote waiting for its $( ) or ` ` to close
  const flush = (sep: string) => {
    out.push({ text: cur, sep })
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
        flush(c === '`' ? '`' : '$(')
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
        flush(')')
        continue
      }
      if (c === top.close) {
        flush(c)
        resume.pop()
        q = '"'
        continue
      }
    }
    if (c === "'" || c === '"') {
      q = c
      cur += c
    } else if (c === '$' && text[i + 1] === '(') {
      flush('$(')
      i += 1
    } else if ('&|;\n()`'.includes(c)) flush(c)
    else cur += c
  }
  flush('')
  return out
}

export const segmentsOf = (command: string): string[] => segmentsWithSeps(command).map(x => x.text)

// A quoted piece that is one word (`"--prod"`) is an argument the shell would unquote, so it stays; a quoted message with spaces is text and goes.
const QUOTED = /'([^']*)'|"((?:\\.|[^"\\])*)"/g
const withoutQuoted = (seg: string) => seg.replace(QUOTED, (_m, a?: string, b?: string) => ((a ?? b ?? '') !== '' && /^\S+$/.test(a ?? b ?? '') ? (a ?? b ?? '') : ''))

// Commands that run the quoted text they are given (`ssh host "a; b"`, `docker exec c "..."`): each quoted piece is a command line to judge too.
const WRAPPERS = new Set(['env', 'nohup', 'exec', 'nice', 'ionice', 'stdbuf', 'doas', 'timeout', 'setsid', 'chrt', 'command', 'time', 'xargs'])
// Fail closed: behind a wrapper (`env -i A=b ssh ...`, `timeout 5 bash -c ...`) we do not try to work out which word runs, so its quoted text is read as commands.
const runsQuoted = (seg: string) =>
  RUNS_ITS_ARGS.has(firstWord(seg)) || WRAPPERS.has(firstWord(seg)) || /^(?:docker(?:\s+compose)?|docker-compose|podman|kubectl)\s+(?:exec|run)\b/.test(strip(seg))
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
// incomplete: the mount list may miss mounts (a compose file, --volumes-from, a flag not read), so an unlisted path is not known to be the container's own
type Scope = { home: string; cwd?: string; vars: Map<string, string | null>; mounts?: Mount[]; incomplete?: boolean; devices: Record<string, string>; uninspected: string[]; timeout?: boolean }

// One path an rm names inside a container, seen through the mounts: where it really is.
export type Mapped =
  | { path: string; kind: 'host'; mountAt: string; folder: string; host: string } // folder: the mount's source here; host: the path here (may hold a pattern)
  | { path: string; kind: 'volume'; mountAt: string; volume: string } // a Docker volume, not a folder on this machine
  | { path: string; kind: 'unresolved'; mountAt: string; source: string; why: string } // a host source that cannot be worked out
  | { path: string; kind: 'uncovered'; why: string } // no mount covers it

const newScope = (command: string, env: ScratchEnv): Scope => ({ home: env.home ?? '', vars: assignmentsOf(command, env), devices: env.volumeDevices ?? {}, uninspected: env.uninspectedVolumes ?? [] })

const SUBSTITUTION = /\$\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)|`[^`]*`/g
const SUBST_WORD = '$CMDSUBST'

// The rest of each docker run line with every $( ) and backtick substitution replaced by one unresolvable word
function foldSubstitutionsInDockerRuns(command: string): string {
  return command.replace(/((?:^|[\s;&|(])(?:(?:docker|podman)\s+(?:(?:compose|container)\s+)?run|docker-compose\s+run)\b)([^\n]*)/g, (_m, head: string, tail: string) =>
    head + tail.replace(SUBSTITUTION, SUBST_WORD),
  )
}

const DOCKER_RUN = /^(?:docker|podman)\s+(?:compose\s+|container\s+)?run\b|^docker-compose\s+run\b/

// ---- a container started with no CPU cap and no timeout in front ----
// 19 Oct: one `docker run` with neither took 14.6 of 16 cores and the machine thrashed. Held with the one-line fix.
const DOCKER_WORDS = new Set(['docker', 'podman', 'docker-compose'])
const baseName = (w: string) => w.split('/').pop() ?? w
const COMPOSE_GLOBAL_VALUE = new Set(['-f', '--file', '-p', '--project-name', '--project-directory', '--profile', '--env-file', '--ansi', '--progress', '-H', '--host', '--context'])
const DOCKER_GLOBAL_VALUE = new Set(['-H', '--host', '--context', '-c', '--config', '-l', '--log-level'])
// a flag only caps the container when its value does: --cpus 0 and --cpu-quota -1 mean no limit, and $X is not known to be a number
const CPU_FLAG = /^--(cpus|cpu-quota|cpuset-cpus)(?:=(.*))?$/
const capValue = (flag: string, value: string | undefined): boolean => {
  const v = unquote(value ?? '')
  if (flag === 'cpus') return /^\d+(?:\.\d+)?$/.test(v) && Number(v) > 0
  if (flag === 'cpu-quota') return /^\d+$/.test(v) && Number(v) > 0
  return /^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$/.test(v)
}
const isTimeout = (w: string) => /^g?timeout$/.test(baseName(w))
// `timeout [options] DURATION` among the words in front of what runs: the duration must be a positive number of seconds, minutes, hours
// or days, at most a day (`timeout 0` means no limit). Stops at the first shell or docker word.
function timeoutIn(words: string[]): boolean {
  for (let k = 0; k < words.length; k += 1) {
    if (isTimeout(words[k])) {
      let j = k + 1
      while (j < words.length && words[j].startsWith('-')) j += ['-s', '-k', '--signal', '--kill-after'].includes(words[j]) ? 2 : 1
      const m = /^(\d+(?:\.\d+)?)([smhd]?)$/.exec(unquote(words[j] ?? ''))
      if (!m) return false
      const seconds = Number(m[1]) * ({ s: 1, '': 1, m: 60, h: 3600, d: 86400 } as Record<string, number>)[m[2]]
      return seconds > 0 && seconds <= 86400
    }
    if (SHELL_WORDS.has(baseName(words[k])) || DOCKER_WORDS.has(baseName(words[k]))) return false
  }
  return false
}

export const UNLIMITED_FIX =
  'add --cpus N (and a timeout in front), as "timeout <secs> docker run --cpus <n> --memory <m> ...", or run it through "~/.claude/scripts/docker-heavy.sh -- docker ..."'

// detached: -d / --detach, so the container outlives a timeout in front of the docker command and only a cap limits it
export type RunCall = { mode: 'run' | 'compose'; timeout: boolean; detached: boolean; capped: boolean; service?: string; image?: string; files: string[]; projectDir?: string }

// the files, project directory, service and the folder the command is in: the same service in another folder is another key
export const composeKey = (c: Pick<RunCall, 'files' | 'projectDir' | 'service'>, cwd?: string) => `${c.files.join('|')}::${c.projectDir ?? ''}::${c.service ?? ''}::${cwd ?? ''}`

// A segment that starts a container with `docker run` / `docker compose run` (also podman, docker-compose), looking only at the
// options before the image. undefined: not one, or one run through docker-heavy.sh (it adds the limits, the lock and a timeout).
function parseRunCall(seg: string): RunCall | undefined {
  const t = seg.split(/\s+/).filter(Boolean).map(unquote)
  if (t.length === 0) return undefined
  let at = 0
  if (!DOCKER_WORDS.has(baseName(t[0]))) {
    if (!WRAPPERS.has(baseName(t[0]))) return undefined
    at = t.findIndex(w => DOCKER_WORDS.has(baseName(w)))
    if (at < 0) return undefined
  }
  const prefix = t.slice(0, at)
  // docker-heavy.sh adds the limits, the lock and a timeout; it is called as `docker-heavy.sh [options] -- docker ...`
  if (prefix.some(w => baseName(w) === 'docker-heavy.sh') && t[at - 1] === '--') return undefined
  const timeout = timeoutIn(prefix)
  let i = at + 1
  let mode: 'run' | 'compose'
  const files: string[] = []
  let projectDir: string | undefined
  if (baseName(t[at]) === 'docker-compose') mode = 'compose'
  else {
    while (i < t.length && t[i].startsWith('-')) i += DOCKER_GLOBAL_VALUE.has(t[i]) ? 2 : 1
    if (t[i] === 'compose') {
      mode = 'compose'
      i += 1
    } else {
      if (t[i] === 'container') i += 1
      if (t[i] !== 'run') return undefined
      mode = 'run'
      i += 1
    }
  }
  if (mode === 'compose') {
    while (i < t.length && t[i].startsWith('-')) {
      const w = t[i]
      if (w === '-f' || w === '--file') {
        files.push(t[i + 1] ?? '')
        i += 2
      } else if (w.startsWith('--file=')) {
        files.push(w.slice(7))
        i += 1
      } else if (/^-f./.test(w)) {
        files.push(w.slice(2))
        i += 1
      } else if (w === '--project-directory') {
        projectDir = t[i + 1]
        i += 2
      } else if (w.startsWith('--project-directory=')) {
        projectDir = w.slice(20)
        i += 1
      } else i += COMPOSE_GLOBAL_VALUE.has(w) ? 2 : 1
    }
    if (t[i] !== 'run') return undefined
    i += 1
  }
  let capped = false
  let detached = false
  for (; i < t.length; i += 1) {
    const w = t[i]
    if (w === '--') {
      i += 1
      break
    }
    if (!w.startsWith('-')) break
    const flag = CPU_FLAG.exec(w)
    if (flag && capValue(flag[1], flag[2] ?? t[i + 1])) capped = true
    if (w === '--detach' || w.startsWith('--detach=')) detached = true
    else if (!w.startsWith('--') && w.length > 1) {
      // grouped short flags (-itd): a d before the first flag that takes a value
      for (const c of w.slice(1)) {
        if (c === 'd') detached = true
        if (SHORT_VALUE.has(c) || c === 'v') break
      }
    }
    if (w.startsWith('--')) {
      if (!w.includes('=') && RUN_VALUE_ALL.has(w)) i += 1
    } else if (w.length === 2) {
      if (SHORT_VALUE.has(w[1]) || w[1] === 'v') i += 1
    } else {
      const takes = (c: string) => SHORT_VALUE.has(c) || c === 'v'
      if (takes(w[w.length - 1]) && !takes(w[1])) i += 1
    }
  }
  return { mode, timeout, detached, capped, service: mode === 'compose' ? t[i] : undefined, image: mode === 'run' ? t[i] : undefined, files, projectDir }
}

// What an uncapped run starts: its image, or its compose service
export const runTargetOf = (seg: string): string | undefined => {
  const c = parseRunCall(strip(seg))
  return c?.image ?? c?.service
}

// What a compose file says about a service's CPU: 'capped' (cpus, cpu_quota or cpuset as a direct key, or deploy.resources.limits.cpus,
// with a positive value: a `${X}` value never counts), 'none' (a CPU key whose value is no cap, such as 0), 'absent'. Read as text:
// anchors, `extends` and includes are not followed, so a limit set that way is 'absent' and the run stays held.
export function composeServiceLimit(yaml: string, service: string): 'capped' | 'none' | 'absent' {
  // text the reader cannot follow: an include, or a service that merges, extends or resets keys. Such a file is 'none', so the run is held.
  if (/^include\s*:/m.test(yaml)) return 'none'
  const lines = yaml.split('\n')
  const live = (l: string) => l.trim() !== '' && !l.trim().startsWith('#')
  const indentOf = (l: string) => l.length - l.trimStart().length
  const top = lines.findIndex(l => live(l) && /^services\s*:\s*(#.*)?$/.test(l))
  if (top < 0) return 'absent'
  let svcIndent = -1
  let start = -1
  for (let k = top + 1; k < lines.length && start < 0; k += 1) {
    const l = lines[k]
    if (!live(l)) continue
    if (indentOf(l) === 0) break
    if (svcIndent < 0) svcIndent = indentOf(l)
    if (indentOf(l) === svcIndent && /^["']?([^"':\s]+)["']?\s*:/.exec(l.trim())?.[1] === service) {
      // the service must be a plain block: `web: {...}`, `web: *base` or `web: &a` hides its keys from this reader
      if (!/^["']?[^"':\s]+["']?\s*:\s*(#.*)?$/.test(l.trim())) return 'none'
      start = k
    }
  }
  if (start < 0) return 'absent'
  const stack: { indent: number; key: string }[] = []
  let found = false
  for (let k = start + 1; k < lines.length; k += 1) {
    const l = lines[k]
    if (!live(l)) continue
    const indent = indentOf(l)
    if (indent <= svcIndent) break
    if (/<<\s*:|\bextends\s*:|!reset|!override/.test(l)) return 'none'
    while (stack.length > 0 && stack[stack.length - 1].indent >= indent) stack.pop()
    const m = /^\s*["']?([\w.-]+)["']?\s*:\s*(.*?)\s*(?:#.*)?$/.exec(l)
    if (!m) continue
    const path = [...stack.map(x => x.key), m[1]].join('.')
    stack.push({ indent, key: m[1] })
    if (!['cpus', 'cpu_quota', 'cpuset', 'deploy.resources.limits.cpus'].includes(path)) continue
    // a ${VAR} value is unknown (the variable may be set elsewhere), so it never counts as a cap
    const v = unquote(m[2])
    found = true
    if (path === 'cpuset' ? /^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$/.test(v) : /^\d+(?:\.\d+)?$/.test(v) && Number(v) > 0) return 'capped'
  }
  return found ? 'none' : 'absent'
}

export const composeServiceCapped = (yaml: string, service: string): boolean => composeServiceLimit(yaml, service) === 'capped'

export type ComposeRun = { key: string; service: string; files: string[]; projectDir?: string; cwd?: string; needsCwd: boolean }

// The `docker compose run` calls in a command that have no timeout in front, so the caller can read each one's compose file for a cpus limit.
export function composeRunsOf(command: string, depth = 0, timeoutFront = false, scope: Scope = newScope(command, {})): ComposeRun[] {
  const out: ComposeRun[] = []
  for (const raw of segmentsOf(command)) {
    const seg = strip(raw).replace(/^\\/, '')
    if (seg === '') continue
    trackCd(seg, command, scope)
    const call = parseRunCall(seg)
    if (call?.mode === 'compose' && call.service && ((!call.timeout && !timeoutFront) || call.detached)) {
      const absolute = (p: string) => p.startsWith('/') || p.startsWith('~/')
      out.push({
        key: composeKey(call, scope.cwd),
        service: call.service,
        files: call.files,
        projectDir: call.projectDir,
        cwd: scope.cwd,
        // a relative -f or project directory, or the default files of the folder, depend on where the shell is
        needsCwd: call.files.length === 0 || call.files.some(f => !absolute(f)) || (call.projectDir !== undefined && !absolute(call.projectDir)),
      })
    }
    const wrapped = /^(?:ba|z)?sh\s+-c\s+(.*)$/.exec(seg)
    const front = timeoutFront || segmentTimeout(seg)
    if (depth < 3 && wrapped) out.push(...composeRunsOf(wrapped[1].replace(/^["']|["']$/g, ''), depth + 1, front, scope))
    else if (depth < 3 && runsQuoted(seg)) for (const piece of quotedPieces(seg)) out.push(...composeRunsOf(piece, depth + 1, front, scope))
  }
  return out
}

// a timeout among the words in front of what the segment runs (`timeout 600 bash -c '...'`), which then covers the quoted text it runs
function segmentTimeout(seg: string): boolean {
  return timeoutIn(seg.split(/\s+/).filter(Boolean))
}

// `cd /full/path` before the command gives it a known $PWD. Anything that may have run in another shell (a "(" in the
// command, pushd, popd, a path that is not plain) leaves $PWD unknown, so a source that uses it keeps holding.
function trackCd(seg: string, command: string, scope: Scope): void {
  if (scope.mounts) return // inside a container its own cd says nothing about this machine
  const m = /^(?:(cd)|pushd|popd)\b\s*(.*)$/.exec(seg)
  if (!m) return
  const arg = unquote(m[2].trim())
  const dir = m[1] && arg !== '' && !/\s/.test(arg) && !withoutTextHeredocs(command).includes('(') ? resolveHostPath(arg, scope) : undefined
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

// what docker accepts as a volume name; any other non-path source is not known to be a volume, so it is judged as a folder
export const VOLUME_NAME = /^[A-Za-z0-9][\w.-]*$/
const kindOf = (source: string): 'host' | 'volume' => (HOSTY.test(source) || !VOLUME_NAME.test(source) ? 'host' : 'volume')

// docker run / compose run flags, so the scan stops at the image and never reads the container's own arguments as mounts
const RUN_BOOL = new Set(['-d', '-i', '-t', '-q', '-P', '-T', '--detach', '--interactive', '--tty', '--rm', '--privileged', '--init', '--read-only', '--no-healthcheck', '--oom-kill-disable', '--publish-all', '--sig-proxy', '--disable-content-trust', '--quiet', '--no-deps', '--service-ports', '--no-TTY', '--build', '--use-aliases', '--remove-orphans', '--quiet-pull', '--help'])
const RUN_VALUE = new Set(['-a', '-c', '-e', '-h', '-l', '-m', '-p', '-u', '-w', '--add-host', '--annotation', '--attach', '--blkio-weight', '--blkio-weight-device', '--cap-add', '--cap-drop', '--cgroup-parent', '--cgroupns', '--cidfile', '--cpu-period', '--cpu-quota', '--cpu-rt-period', '--cpu-rt-runtime', '--cpu-shares', '--cpus', '--cpuset-cpus', '--cpuset-mems', '--detach-keys', '--device', '--device-cgroup-rule', '--device-read-bps', '--device-read-iops', '--device-write-bps', '--device-write-iops', '--dns', '--dns-option', '--dns-search', '--domainname', '--entrypoint', '--env', '--env-file', '--expose', '--gpus', '--group-add', '--health-cmd', '--health-interval', '--health-retries', '--health-start-period', '--health-timeout', '--hostname', '--ip', '--ip6', '--ipc', '--isolation', '--label', '--label-file', '--link', '--link-local-ip', '--log-driver', '--log-opt', '--mac-address', '--memory', '--memory-reservation', '--memory-swap', '--memory-swappiness', '--name', '--network', '--net', '--network-alias', '--oom-score-adj', '--pid', '--pids-limit', '--platform', '--publish', '--pull', '--restart', '--runtime', '--security-opt', '--shm-size', '--stop-signal', '--stop-timeout', '--storage-opt', '--sysctl', '--tmpfs', '--ulimit', '--user', '--userns', '--uts', '--volume-driver', '--workdir', '--cap', '--entrypoint'])
const SHORT_BOOL = new Set(['d', 'i', 't', 'q', 'P', 'T'])
const SHORT_VALUE = new Set(['a', 'c', 'e', 'h', 'l', 'm', 'p', 'u', 'w'])
const RUN_VALUE_ALL = new Set([...RUN_VALUE, '-v', '--volume', '--mount', '--volumes-from'])

// The mounts a `docker run` / `docker compose run` gives its container: -v, --volume and --mount, in the forms docker accepts,
// read from the options before the image only. `incomplete` says the list may miss mounts: a compose service brings its own,
// --volumes-from and --mount forms or flags not understood are not listed.
function parseMounts(seg: string): { mounts: Mount[]; incomplete: boolean } {
  const t = seg.split(/\s+/).map(unquote)
  const out: Mount[] = []
  let incomplete = /\bcompose\b|^docker-compose\b/.test(seg.split(/\s+run\b/)[0] ?? '')
  const volume = (spec: string) => {
    const [first, second] = splitColons(spec)
    if (second === undefined) {
      if (first.startsWith('/')) out.push({ source: '', target: first, kind: 'volume' }) // anonymous volume
      else incomplete = true
    } else if (second.startsWith('/')) out.push({ source: first, target: second, kind: kindOf(first) })
    else incomplete = true
  }
  const mount = (spec: string) => {
    const kv = new Map(spec.split(',').map(p => [p.slice(0, p.indexOf('=')), p.slice(p.indexOf('=') + 1)] as const))
    const type = kv.get('type') ?? kv.get('Type')
    const source = kv.get('source') ?? kv.get('src') ?? ''
    const target = kv.get('target') ?? kv.get('destination') ?? kv.get('dst')
    if (!target?.startsWith('/') || (type === 'bind' && !source)) {
      incomplete = true
      return
    }
    // a volume made over a host folder with the local driver (volume-opt=device=/path) is that folder
    const device = /device=([^,]+)/.exec(spec)?.[1]
    if (device) out.push({ source: device, target, kind: 'host' })
    else if (type === 'bind') out.push({ source, target, kind: 'host' })
    else if (type === 'tmpfs') out.push({ source, target, kind: 'volume' })
    else if (type === 'volume') out.push({ source, target, kind: source === '' ? 'volume' : kindOf(source) })
    else out.push({ source, target, kind: kindOf(source) })
  }
  // the next word is this flag's value
  const takes = (i: number, use: (v: string) => void): number => {
    if (t[i + 1] === undefined) {
      incomplete = true
      return i
    }
    use(t[i + 1])
    return i + 1
  }
  let i = t.findIndex(w => w === 'run') + 1
  let imageSeen = false
  for (; i < t.length; i += 1) {
    const w = t[i]
    if (/["']/.test(w)) incomplete = true // a quote left in a word: a value with spaces split the words, so what follows may be misread
    if (w === '--' || !w.startsWith('-')) {
      // the image (or the compose service) starts what the container runs; a word that is not a plausible one means the scan lost its place
      const image = w === '--' ? t[i + 1] : w
      if (image === undefined || !/^[\w][\w./:@-]*$/.test(image)) incomplete = true
      imageSeen = true
      break
    }
    if (w === '-v' || w === '--volume') i = takes(i, volume)
    else if (w === '--mount') i = takes(i, mount)
    else if (w.startsWith('--volume=')) volume(unquote(w.slice(9)))
    else if (w.startsWith('--mount=')) mount(unquote(w.slice(8)))
    else if (w.startsWith('--volumes-from')) {
      incomplete = true
      if (w === '--volumes-from') i += 1
    } else if (w.startsWith('--')) {
      if (RUN_BOOL.has(w) || w.includes('=')) continue
      if (RUN_VALUE.has(w)) i += 1
      else incomplete = true // a flag not known: whether it takes a value is a guess
    } else if (w.length === 2) {
      if (SHORT_VALUE.has(w[1])) i += 1
      else if (!SHORT_BOOL.has(w[1])) incomplete = true
    } else {
      // grouped short flags (-it, -dv, -itv src:dst) or one with its value attached (-vsrc:dst, -eNAME=x)
      const first = w[1]
      if (first === 'v') volume(unquote(w.slice(2).replace(/^=/, '')))
      else if (SHORT_VALUE.has(first)) continue
      else {
        const letters = [...w.slice(1)]
        const last = letters[letters.length - 1]
        if (!letters.slice(0, -1).every(c => SHORT_BOOL.has(c))) incomplete = true
        else if (last === 'v') i = takes(i, volume)
        else if (SHORT_VALUE.has(last)) i += 1
        else if (!SHORT_BOOL.has(last)) incomplete = true
      }
    }
  }
  return { mounts: out, incomplete: incomplete || !imageSeen }
}

// The names of the Docker volumes a command mounts into a docker run, so the caller can ask docker whether one is really a host folder
export function namedVolumesOf(command: string, depth = 0): string[] {
  const names = new Set<string>()
  for (const text of depth === 0 ? [command, foldSubstitutionsInDockerRuns(command)] : [command]) {
    for (const raw of segmentsOf(text)) {
      const seg = strip(raw).replace(/^\\/, '')
      if (DOCKER_RUN.test(seg)) {
        for (const m of parseMounts(seg).mounts) if (m.kind === 'volume' && m.source !== '') names.add(m.source)
      }
      const wrapped = /^(?:ba|z)?sh\s+-c\s+(.*)$/.exec(seg)
      if (depth < 3 && wrapped) for (const n of namedVolumesOf(wrapped[1].replace(/^["']|["']$/g, ''), depth + 1)) names.add(n)
      else if (depth < 3 && runsQuoted(seg)) for (const piece of quotedPieces(seg)) for (const n of namedVolumesOf(piece, depth + 1)) names.add(n)
    }
  }
  return [...names]
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
      const device = m.kind === 'volume' ? scope.devices[m.source] : undefined
      if (m.kind === 'volume' && scope.uninspected.includes(m.source)) {
        entries.push({ path, kind: 'unresolved', mountAt, source: m.source, why: 'docker could not be asked what this volume is (it may be a host folder)' })
        continue
      }
      if (m.kind === 'volume' && !device) {
        entries.push({ path, kind: 'volume', mountAt, volume: m.source })
        continue
      }
      const r = resolveHostPath(device ?? m.source, scope)
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
function containerPathIsHarmless(m: Mapped, env: ScratchEnv, incomplete = false): boolean {
  if (m.kind === 'volume') return true
  if (m.kind === 'host') return isOwnScratch(m.host, new Map(), env)
  if (m.kind === 'uncovered') return !incomplete && isOwnScratch(m.path, new Map(), env)
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

// A production call counts as a read only when its forwarder script is plain and every remote command is a read.
// The script text comes from env.prodScripts; a path not read yet is asked for through env.prodWanted and held this time. The repo's dotenv file is
// asked for through env.prodWantedEnv instead, so the caller reduces exactly that path, and no other, to its key names.
function prodReadOnly(raw: string, fusedErr: boolean, shape: Map<string, string> | undefined, depth: number, scope: Scope, env: ScratchEnv): boolean {
  // depth 0 only: inside `bash -c` the commands before it are out of sight, and one of them could rewrite the script the hook just read
  if (depth > 0 || !shape || scope.mounts) return false
  const call = parseProdCall(raw.trim(), fusedErr)
  if (!call) return false
  // the script path uses only the assignments the shape check accepted, never the regex over the raw command or the inherited environment
  const script = resolveHostPath(call.script, { ...scope, vars: shape })
  // backstop: the path bash will open is one plain absolute word
  if (!('path' in script) || !/^\/[\w./-]+$/.test(script.path)) return false
  // no hidden last part either (a hidden folder such as .worktrees is fine): the forwarder is a script, never a dotfile such as the dotenv file the caller reduces to key names
  if ((script.path.split('/').pop() ?? '').startsWith('.')) return false
  // no `.` or `..` part, in the word or in any value it expands: the kernel follows a symlink before it folds `..`
  const expanded = call.script.replace(/\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))/g, (_m, a?: string, b?: string) => shape.get((a ?? b) as string) ?? '')
  if (hasDotPart(expanded)) return false
  const text = env.prodScripts?.[script.path]
  if (text === undefined) {
    env.prodWanted?.add(script.path)
    return false
  }
  if (!forwarderIsPlain(text) || !remoteIsRead(call.remote)) return false
  // the one file the forwarder sources must be a version we have reviewed
  const deploy = deployScript(text, scope.home)
  if (deploy === undefined) return false
  return deployCopyIsReviewed(deploy, scope, env) !== undefined
}

// The deploy script at this path must be a reviewed copy (SHA-256 pin) and the dotenv file it loads must hold only allowed key names. Returns the
// pinned text, or undefined; a file not read yet is asked for through env and the answer is "not yet" (held this time).
function deployCopyIsReviewed(deploy: { repo: string; path: string }, scope: Scope, env: ScratchEnv): string | undefined {
  const deployText = env.prodScripts?.[deploy.path]
  if (deployText === undefined) {
    env.prodWanted?.add(deploy.path)
    return undefined
  }
  if (!deployIsPinned(deploy.repo, deployText, env.deployPins)) return undefined
  // the dotenv file it loads: key names only (index.tsx keeps nothing else of the file), every one on the allowlist
  const envPath = `${scope.home}/repos/${deploy.repo}/.env`
  const keys = env.prodScripts?.[envPath]
  if (keys === undefined) {
    env.prodWantedEnv?.add(envPath)
    return undefined
  }
  return deployEnvIsPlain(keys, env.deployEnvKeys) ? deployText : undefined
}

// 26 Oct: the deploy script run straight (`timeout 120 scripts/portainer-deploy.sh deployed-commit --prod`, `source scripts/portainer-deploy.sh --prod
// status`) is a read when the script is the reviewed copy and the action is one its own dispatcher marks read-only. `cwd` is where a relative script
// word starts: the session's folder, or the one `cd /abs` the line shape allowed.
function deployReadOnly(raw: string, fusedErr: boolean, shape: Map<string, string> | undefined, depth: number, scope: Scope, env: ScratchEnv, cwd: string | undefined): { fns: Set<string> | undefined } | undefined {
  if (depth > 0 || !shape || scope.mounts || !scope.home.startsWith('/') || hasDotPart(scope.home)) return undefined
  const call = parseDeployCall(raw.trim(), fusedErr)
  if (!call) return undefined
  const script = resolveHostPath(call.script, { ...scope, vars: shape, cwd })
  if (!('path' in script)) return undefined
  const expanded = call.script.replace(/\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))/g, (_m, a?: string, b?: string) => shape.get((a ?? b) as string) ?? '')
  if (hasDotPart(expanded)) return undefined
  // exactly HOME/repos/NAME/scripts/portainer-deploy.sh, the one place a pin is kept for
  const prefix = `${scope.home}/repos/`
  const suffix = '/scripts/portainer-deploy.sh'
  const repo = script.path.startsWith(prefix) && script.path.endsWith(suffix) ? script.path.slice(prefix.length, script.path.length - suffix.length) : ''
  if (!/^[\w-]+$/.test(repo)) return undefined
  const text = deployCopyIsReviewed({ repo, path: script.path }, scope, env)
  if (text === undefined) return undefined
  if (!readOnlyActions(text).has(call.action)) return undefined
  if (call.operand !== undefined && !['config-get', 'state-get', 'logs'].includes(call.action)) return undefined
  // a sourced script leaves its functions callable for the rest of the command: the caller judges later segments by them
  return { fns: call.sourced ? definedFunctions(text) : undefined }
}

function classifyWithEnv(command: string, depth: number, env: ScratchEnv, scope: Scope): Hold[] {
  const holds: Hold[] = []
  const parts = segmentsWithSeps(command)
  let sourcedFns: Set<string> | undefined
  for (const [index, part] of parts.entries()) {
    const raw = part.text
    // `2>&1` is cut at its "&": the piece ends in `2>` and the next one starts with `1`
    const fusedErr = part.sep === '&' && /(?:^|\s)2>\s*$/.test(raw) && /^1\s*$/.test(parts[index + 1]?.text ?? '')
    const seg = strip(raw).replace(/^\\/, '')
    if (seg === '') continue
    trackCd(seg, command, scope)
    // `docker run -v SRC:DST ...`: the text it runs sees its paths through these mounts
    const parsed = DOCKER_RUN.test(seg) ? parseMounts(seg) : undefined
    const inner: Scope = { ...scope, mounts: parsed ? parsed.mounts : scope.mounts, incomplete: parsed ? parsed.incomplete : scope.incomplete, timeout: scope.timeout || segmentTimeout(seg) }
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
      if (r && f && scope.mounts && (scope.mounts.length > 0 || scope.incomplete)) {
        // inside a container that has mounts: judge each path by what it really is on this machine
        const mapped = mapRmTargets(targetsOf(seg).map(unquote), scope.mounts, scope)
        if (mapped.some(m => !containerPathIsHarmless(m, env, scope.incomplete))) holds.push({ kind: 'rm -rf', hard: false, segment: seg, container: mapped })
      } else if (r && f && !ownScratchTargets(command, env, depth).some(group => group.segment === seg)) {
        add('rm -rf', false)
      }
    }
    const call = parseRunCall(seg)
    if (call && !((call.timeout || scope.timeout) && !call.detached) && !(call.mode === 'run' ? call.capped : (env.composeLimited ?? []).includes(composeKey(call, scope.cwd)))) add('docker run without limits', false)
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
    // after a sourced deploy script, the functions it defined are callable: only its read helpers pass (afterSourcedIsPlain)
    if (sourcedFns && !afterSourcedIsPlain(seg, sourcedFns)) add('production deploy', true)
    else if (isProdDeploy(seg) && !prodReadOnly(raw, fusedErr, prodLineShape(command, parts, index, fusedErr), depth, scope, env)) {
      const read = deployReadOnly(raw, fusedErr, prodLineShape(command, parts, index, fusedErr, 'more'), depth, scope, env, parts.slice(0, index).some(p => /^\s*cd\b/.test(p.text)) ? scope.cwd : env.cwd)
      if (!read) add('production deploy', true)
      else if (read.fns) sourcedFns = read.fns
    }
  }
  return holds
}

type OwnScratchGroup = { segment: string; targets: string[] }

// Returns targets from rm -rf segments that are safe scratch locations. This
// uses the same best-effort segmentation as classify, not a full shell parser.
// A relative target counts only when a literal `cd <full path> &&` leads to it (see cwdTracker); the targets returned are the full paths.
export function ownScratchTargets(command: string, env: ScratchEnv = {}, depth = 0): OwnScratchGroup[] {
  const groups: OwnScratchGroup[] = []
  const failed = new Set<string>() // the same words that failed once (say, with no cd before them) never pass on another line
  const parts = segmentsWithSeps(command)
  const cwdAt = cwdTracker(command, env)
  let before = '' // the command up to this segment: a variable counts only if it was set before the rm that uses it
  for (const [index, { text }] of parts.entries()) {
    const assignments = assignmentsOf(before, env)
    before += text + parts[index].sep
    const cwd = cwdAt(index)
    const seg = strip(text).replace(/^\\/, '')
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
    // a relative word is joined to the folder a guarded cd led to; with none, it stays relative and is held
    const full = targets.map(x => (cwd && x !== '' && !/^[/~$]/.test(x) ? `${cwd}/${x}` : x))
    if (full.length > 0 && full.every(target => isOwnScratch(target, assignments, env))) {
      groups.push({ segment: seg, targets: full })
    } else failed.add(seg)
  }
  return groups.filter(g => !failed.has(g.segment))
}

// The folder each segment runs in, when a literal `cd <full path>` sets it. A cd counts only on a chain of && that starts a list
// (the start, or after ; or a newline), so the later words run only if it worked: after `cd X; rm -rf a`, `cd X || ...`, a pipe,
// a background `&`, a subshell or a substitution, the folder is unknown and a relative target keeps holding.
function cwdTracker(command: string, env: ScratchEnv): (index: number) => string | undefined {
  const segs = segmentsWithSeps(command)
  let before = ''
  // text a heredoc only writes (a python script's parens) is not shell, so it blinds nothing
  const blind = /(?:^|[^$])\(|`|\$\(\s*(?:cd|pushd|popd)\b/.test(withoutTextHeredocs(command)) // a subshell or substitution may hold its own cd
  const folders: Array<string | undefined> = []
  let cwd: string | undefined
  for (let i = 0; i < segs.length; i += 1) {
    const prev = segs[i - 1]
    // `a && b` splits into `a`, an empty piece between the two &, and `b`: the empty piece changes nothing
    if (i >= 1 && prev.sep === '&' && segs[i].sep === '&' && strip(segs[i].text) === '') {
      folders.push(cwd)
      before += segs[i].text + segs[i].sep
      continue
    }
    const andBefore = i >= 2 && prev.sep === '&' && strip(prev.text) === '' && segs[i - 2].sep === '&'
    const startsList = i === 0 || prev.sep === ';' || prev.sep === '\n'
    if (!andBefore) cwd = undefined // anything but && ends what the last cd promised
    const seg = strip(segs[i].text).replace(/^\\/, '')
    const assignments = assignmentsOf(before, env)
    before += segs[i].text + segs[i].sep
    folders.push(cwd)
    const m = /^(cd|pushd|popd)\b\s*(.*)$/.exec(seg)
    if (m) {
      const word = unquote(m[2].trim())
      const guarded = m[1] === 'cd' && !blind && (startsList || andBefore)
      cwd = guarded && word !== '' && !/\s/.test(word) ? literalFolder(word, cwd, assignments, env) : undefined
    }
  }
  return index => folders[index]
}

// A folder word written out in full, once ~ and the command's own literal variables are filled in; undefined for anything else.
function literalFolder(word: string, cwd: string | undefined, assignments: Map<string, string | null>, env: ScratchEnv): string | undefined {
  let w = word
  if (w === '~' || w.startsWith('~/')) {
    if (!env.home) return undefined
    w = env.home + w.slice(1)
  }
  w = w.replace(/\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))/g, (m, a?: string, b?: string) => {
    const value = assignments.get((a ?? b) as string)
    return typeof value === 'string' ? value : m
  })
  if (/[$*?[\]{}`~]/.test(w)) return undefined
  if (!w.startsWith('/')) {
    if (!cwd) return undefined
    w = `${cwd}/${w}`
  }
  return normalizePath(w)
}

// Variables the command sets itself: a literal value, or null for one set from `mktemp` (a fresh temp folder, path unknown).
// A value that names another variable set earlier to a literal is filled in (S=/tmp/x; P=$S/prof gives P=/tmp/x/prof); one that
// names a variable nothing set keeps its `$`, so a target built from it stays held.
function assignmentsOf(command: string, env: ScratchEnv): Map<string, string | null> {
  const assignments = new Map<string, string | null>()
  if (env.tmpdir) assignments.set('TMPDIR', env.tmpdir)
  const mktemp = /(?:^|[;&|\n]\s*)([A-Za-z_]\w*)=\$\(\s*mktemp(?:\s+[^)]*)?\)/g
  for (const match of command.matchAll(mktemp)) assignments.set(match[1], null)
  const literals = /(?:^|[;&|\n]\s*)(?:export\s+)?([A-Za-z_]\w*)=("[^"]*"|'[^']*'|[^\s;&|]+)/g
  const unsure = new Set<string>() // set twice to different things, or by mktemp and by hand: which one the rm sees depends on how the shell ran
  for (const match of command.matchAll(literals)) {
    if (match[2].startsWith('$(')) continue
    const value = unquote(match[2])
    if (assignments.has(match[1]) && assignments.get(match[1]) !== value) unsure.add(match[1])
    assignments.set(match[1], value)
  }
  for (const name of unsure) assignments.delete(name)
  for (let pass = 0; pass < 5; pass += 1) {
    let changed = false
    for (const [name, value] of assignments) {
      if (typeof value !== 'string' || !value.includes('$')) continue
      const next = value.replace(/\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))/g, (m, a?: string, b?: string) => {
        const ref = (a ?? b) as string
        const other = ref === name ? undefined : assignments.get(ref)
        return typeof other === 'string' && !other.includes('$') ? other : m
      })
      if (next !== value) {
        assignments.set(name, next)
        changed = true
      }
    }
    if (!changed) break
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
    case 'docker run without limits':
      return {
        what: 'starts a container with no CPU cap and no timeout in front, so it can take every core and keep them',
        why: 'on 19 Oct one such container took 14.6 of 16 cores and the machine stopped answering',
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

// A lane (AFA_LANE=1) is not asked "Run it?" about a hold whose fix can be named: the refusal says the fix and the lane runs the
// corrected command. A hold with no fix here keeps the dialog. Only a relative path in an rm and a docker run without limits have one.
export function laneFix(hold: Hold): string | undefined {
  if (hold.kind === 'docker run without limits') return `Fix: ${UNLIMITED_FIX}. Then run it again.`
  if (hold.kind === 'rm -rf' && !hold.container && targetsOf(hold.segment).map(unquote).some(x => x !== '' && !/^[/~$]/.test(x))) {
    return 'Fix: use the full absolute path, written out in full (for "cd /a/b && rm -rf c" write "rm -rf /a/b/c"): a relative name depends on the folder the shell is in, which the guard cannot confirm. Then run it again.'
  }
  return undefined
}

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
