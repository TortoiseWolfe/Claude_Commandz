// What a held command would touch. Every prompt and every refusal carries these lines, for every rule:
// real things (a path with its size, a branch and remote, containers, volumes, the service) or, when the
// words cannot say, "can't tell what this touches: <why>". Never nothing, never a fragment of code.
//
// Read-only probes only. `Probe` is the part of the engine this needs (the same $.fs / $.process calls),
// so a test hands in a fake with the same shape. Every async function here takes it first, spelled `$`.

import { targetsOf, unquote } from './lib'
import type { Hold } from './lib'

export type Probe = {
  fs: {
    stat(path: string): Promise<{ kind: string; size: number }> // throws when the path is not there
    list(path: string): Promise<unknown[]>
    read(path: string): Promise<unknown>
    exists(path: string): Promise<boolean>
  }
  process: {
    run(argv: string[], opts: { timeoutMs: number }): Promise<{ exitCode: number; stdout: string }>
  }
}

export const cantTell = (why: string) => `can't tell what this touches: ${why}`

const MAX_LISTED = 8
const DOCKER_DOWN = 'docker did not answer (is the Docker engine running?)'
const SAFE_WORD = /^[A-Za-z0-9][\w.\/~:@+=-]*$/

const clip = (lines: string[], n: number) =>
  lines.length > n ? [...lines.slice(0, n), `... ${lines.length - n} more not listed`] : lines

const outLines = (out: string) =>
  out
    .split('\n')
    .map(l => l.trim())
    .filter(Boolean)

// stdout of a read-only program, or null when it failed or is not there
async function ran($: Probe, argv: string[]): Promise<string | null> {
  try {
    const r = await $.process.run(argv, { timeoutMs: 10_000 })
    return r.exitCode === 0 ? r.stdout : null
  } catch {
    return null
  }
}

async function rmLines($: Probe, segment: string, home: string): Promise<string[]> {
  const all = targetsOf(segment).map(unquote)
  if (all.length === 0) {
    return [cantTell('rm names no path here (it may be given its paths by another command)')]
  }
  const out: string[] = []
  for (const raw of all.slice(0, MAX_LISTED)) {
    const path = raw.replace(/^~(?=\/|$)/, home)
    const variable = /\$\{?([A-Za-z_]\w*)/.exec(path)
    if (variable || /[`$]/.test(path)) {
      const why = variable
        ? `the path goes through $${variable[1]}, which is only read when the command runs`
        : 'the path is built by another command when it runs'
      out.push(`${path}: ${cantTell(why)}`)
    } else if (/[*?[\]{}]/.test(path)) {
      out.push(`${path}: ${cantTell('the pattern only expands when the command runs')}`)
    } else {
      try {
        const st = await $.fs.stat(path)
        if (st.kind === 'directory') {
          let inside = ', everything inside removed'
          try {
            inside = `, ${(await $.fs.list(path)).length} entries at the top level, all removed`
          } catch {
            // the size and the words are still true
          }
          out.push(`${path} (dir, ${st.size} bytes${inside})`)
        } else out.push(`${path} (${st.kind}, ${st.size} bytes)`)
      } catch {
        out.push(`${path} (not found)`)
      }
    }
  }
  if (all.length > MAX_LISTED) out.push(`... ${all.length - MAX_LISTED} more targets not listed`)
  return out
}

async function dockerPruneLines($: Probe, hold: Hold): Promise<string[]> {
  const df = await ran($, ['docker', 'system', 'df'])
  if (df === null) return [cantTell(DOCKER_DOWN)]
  const out: string[] = []
  if (hold.kind === 'docker volume prune') {
    const vols = await ran($, ['docker', 'volume', 'ls', '--filter', 'dangling=true', '--format', '{{.Name}}'])
    if (vols === null) return [cantTell(DOCKER_DOWN)]
    const names = outLines(vols)
    out.push(names.length > 0 ? 'unused volumes that would go:' : 'unused volumes that would go: none found right now')
    out.push(...clip(names, 10).map(n => `  ${n}`))
    return out
  }
  if (hold.kind === 'docker system prune') {
    const stopped = await ran($, ['docker', 'ps', '-a', '--filter', 'status=exited', '--format', '{{.Names}} ({{.Image}})'])
    const names = outLines(stopped ?? '')
    out.push(names.length > 0 ? 'stopped containers that would go:' : 'stopped containers that would go: none found right now')
    out.push(...clip(names, 10).map(n => `  ${n}`))
  } else {
    const every = /\s(-a|--all)\b/.test(hold.segment)
    const filter = every ? [] : ['--filter', 'dangling=true']
    const imgs = await ran($, ['docker', 'image', 'ls', ...filter, '--format', '{{.Repository}}:{{.Tag}} ({{.Size}})'])
    const names = outLines(imgs ?? '')
    out.push(every ? 'images on this machine (every unused one goes):' : 'untagged images that would go:')
    out.push(...(names.length > 0 ? clip(names, 10).map(n => `  ${n}`) : ['  none found right now']))
  }
  out.push('disk use now:', ...clip(outLines(df), 6).map(l => `  ${l}`))
  return out
}

async function volumeRmLines($: Probe, segment: string): Promise<string[]> {
  const t = segment.split(/\s+/)
  const at = t.findIndex(x => x === 'rm' || x === 'remove')
  const names = t.slice(at + 1).filter(x => !x.startsWith('-')).map(unquote)
  if (names.length === 0) return [cantTell('no volume name is given here (another command may supply it)')]
  const have = await ran($, ['docker', 'volume', 'ls', '--format', '{{.Name}}'])
  if (have === null) return [cantTell(DOCKER_DOWN)]
  const exists = new Set(outLines(have))
  return clip(
    names.map(n =>
      /[$`*?]/.test(n)
        ? `${n}: ${cantTell('the name is built when the command runs')}`
        : `${n} (${exists.has(n) ? 'volume exists, its data goes' : 'volume not found'})`,
    ),
    MAX_LISTED,
  )
}

async function composeDownLines($: Probe, segment: string): Promise<string[]> {
  const t = segment.split(/\s+/)
  const flags: string[] = []
  for (let i = 0; i < t.length; i += 1) {
    const next = t[i + 1] ? unquote(t[i + 1]) : ''
    if ((t[i] === '-f' || t[i] === '--file' || t[i] === '-p' || t[i] === '--project-name') && SAFE_WORD.test(next)) {
      flags.push(t[i], next)
    }
  }
  const named = await ran($, ['docker', 'compose', ...flags, 'config', '--volumes'])
  const names = outLines(named ?? '')
  if (names.length === 0) {
    return [cantTell("the compose file here could not be read (docker compose config --volumes gave nothing), so the stack's volumes are unknown")]
  }
  return ['volumes this stack declares (deleted with it, with their data):', ...clip(names, 10).map(n => `  ${n}`)]
}

// host and path only: a remote address can carry a token in its user part, and that part is cut
const hostOf = (url: string): string => {
  const web = /^[a-z+]+:\/\/(?:[^@/]*@)?([^/?#]+)([^?#]*)/i.exec(url)
  if (web) return `${web[1]}${web[2]}`
  const scp = /^(?:[^@/\s]+@)?([^:/\s]+):(.+)$/.exec(url)
  return scp ? `${scp[1]}/${scp[2]}` : ''
}

async function forcePushLines($: Probe, segment: string): Promise<string[]> {
  const t = segment.split(/\s+/)
  const after = t.slice(t.indexOf('push') + 1)
  const words = after.filter(x => !x.startsWith('-')).map(unquote)
  const current = ((await ran($, ['git', 'branch', '--show-current'])) ?? '').trim()
  let remote = words[0]
  if (!remote && SAFE_WORD.test(current)) {
    remote = ((await ran($, ['git', 'config', '--get', `branch.${current}.remote`])) ?? '').trim() || undefined
  }
  const refs = words.slice(1).map(w => w.replace(/^\+/, '').split(':').pop() || w)
  let branch = refs.length > 0 ? refs.join(', ') : current
  if (after.includes('--all')) branch = 'every local branch (--all)'
  else if (after.includes('--mirror')) branch = 'every ref on the remote (--mirror)'
  if (!branch) return [cantTell('no branch is named and this folder is on a detached HEAD')]
  if (!remote) return [`branch ${branch}; ${cantTell('no remote is named and none is set for this branch')}`]
  const isAddress = remote.includes(':') || remote.includes('/')
  const url = isAddress ? remote : SAFE_WORD.test(remote) ? ((await ran($, ['git', 'remote', 'get-url', remote])) ?? '').trim() : ''
  const where = hostOf(url)
  return [
    `branch ${branch} on remote ${isAddress ? '(given as an address)' : remote} (${where || 'its address could not be read here'}); commits there that this machine lacks are lost`,
  ]
}

const DEPLOY_TOOLS: Record<string, string> = {
  vercel: 'Vercel (the project linked to the current folder)',
  netlify: 'Netlify (the site linked to the current folder)',
  wrangler: 'Cloudflare Workers/Pages (the project in the wrangler config)',
  firebase: 'Firebase Hosting (the project in .firebaserc)',
  fly: 'Fly.io (the app named in fly.toml)',
  flyctl: 'Fly.io (the app named in fly.toml)',
  heroku: 'Heroku (the app set for this folder)',
  railway: 'Railway (the service linked to this folder)',
  supabase: 'Supabase (the project linked to this folder)',
  eas: 'Expo EAS (store builds and updates)',
  kubectl: 'a Kubernetes cluster (the current context)',
  terraform: 'infrastructure managed by Terraform',
  aws: 'AWS (the active profile)',
  gcloud: 'Google Cloud (the active project)',
  az: 'Azure (the active subscription)',
  serverless: 'AWS through Serverless',
  sls: 'AWS through Serverless',
}

async function deployLines($: Probe, segment: string): Promise<string[]> {
  const t = segment.split(/\s+/)
  const tool = (t[0] ?? '').split('/').pop() ?? ''
  const hosts = [...segment.matchAll(/https?:\/\/([^\s/"']+)/g)].map(m => m[1])
  const flag = /--prod(?:uction)?\b/.exec(segment)?.[0] ?? 'redeploy aimed at prod'
  const named = hosts.length > 0 ? `; host named: ${hosts.join(', ')}` : ''
  if (DEPLOY_TOOLS[tool]) return [`service: ${DEPLOY_TOOLS[tool]}; target: production (${flag})${named}`]
  if (['npm', 'pnpm', 'yarn', 'npx', 'bun'].includes(tool)) {
    const script = t.slice(1).find(x => !x.startsWith('-') && x !== 'run')
    let pkg = ''
    try {
      pkg = (await $.fs.exists('package.json')) ? String(await $.fs.read('package.json')) : ''
    } catch {
      pkg = ''
    }
    const body = /"scripts"\s*:\s*\{([^}]*)\}/s.exec(pkg)?.[1] ?? ''
    const line = script ? new RegExp(`"${script.replace(/[^\w-]/g, '')}"\\s*:\\s*"([^"]*)"`).exec(body)?.[1] : undefined
    if (line) {
      const inner = DEPLOY_TOOLS[(line.trim().split(/\s+/)[0] ?? '').split('/').pop() ?? '']
      const service = inner ? `service: ${inner}` : cantTell(`the script "${script}" runs "${line.slice(0, 80)}", which is not a deploy tool I know`)
      return [`script "${script}" in this folder's package.json runs: ${line.slice(0, 200)}; ${service}; target: production (${flag})`]
    }
    return [cantTell(`${tool} runs a project script${script ? ` ("${script}")` : ''} and package.json here does not show what it ships to`)]
  }
  if (hosts.length > 0) return [`host named in the command: ${hosts.join(', ')}; target: production (${flag})`]
  return [cantTell(`"${tool.slice(0, 40)}" is not a deploy tool I know and the command names no host, so the live site it ships to is unknown`)]
}

// One entry per rule `classify` can raise, with commands that raise it. A test walks this table, and a second
// test fails when `classify` or `explain` names a kind that is missing here: a new rule cannot ship undescribed.
export const RULE_SAMPLES: Record<string, string[]> = {
  'rm -rf': ['rm -rf /home/t/build /home/t/missing', 'rm -rf "$BUILD_DIR"/old', 'rm -rf dist/*', 'rm -rf'],
  'docker system prune': ['docker system prune -af'],
  'docker image prune': ['docker image prune -a', 'docker image prune'],
  'docker volume prune': ['docker volume prune -f'],
  'docker volume rm': ['docker volume rm pgdata cache', 'docker volume rm'],
  'docker compose down -v': ['docker compose down -v', 'docker compose -f ci.yml -p demo down --volumes'],
  'git push --force': ['git push --force origin main', 'git push -f', 'git push origin +feat/x'],
  'production deploy': ['vercel --prod', 'npm run deploy --production', 'redeploy prod now', 'mystery-tool --prod'],
  'tmux shared lanes': [
    'tmux kill-server',
    'tmux kill-session -t lanes',
    'tmux kill-session -tlanes',
    'tmux kill-session -t=lanes',
  ],
}

// The lines for one hold: the command, then what it would touch. Never just the command.
export async function touches($: Probe, hold: Hold, home: string): Promise<string> {
  const cut = hold.segment.length > 300 ? ' ... (cut, full command is longer)' : ''
  const head = hold.kind + ': ' + hold.segment.slice(0, 300) + cut
  let lines: string[]
  try {
    switch (hold.kind) {
      case 'rm -rf':
        lines = await rmLines($, hold.segment, home)
        break
      case 'docker system prune':
      case 'docker image prune':
      case 'docker volume prune':
        lines = await dockerPruneLines($, hold)
        break
      case 'docker volume rm':
        lines = await volumeRmLines($, hold.segment)
        break
      case 'docker compose down -v':
        lines = await composeDownLines($, hold.segment)
        break
      case 'git push --force':
        lines = await forcePushLines($, hold.segment)
        break
      case 'production deploy':
        lines = await deployLines($, hold.segment)
        break
      case 'tmux shared lanes':
        lines = [
          hold.segment.includes('kill-server')
            ? "closes every lane window (the tmux session 'lanes'), with no undo; also closes every other session and window on the default tmux server"
            : "closes every lane window (the tmux session 'lanes'), with no undo",
        ]
        break
      default:
        lines = [cantTell('"' + hold.kind + '" is a held rule that has no description of its targets yet')]
    }
  } catch {
    lines = [cantTell('looking it up failed')]
  }
  if (lines.length === 0) lines = [cantTell('nothing could be read about it')]
  return [head, ...lines.map(l => (l.startsWith(' ') ? l : '  ' + l))].join('\n')
}
