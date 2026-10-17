import { atom, read, update } from 'claude-code'
import type { Engine, Register } from 'claude-code'

import {
  bandText,
  classify,
  countInbox,
  explain,
  hasExitLine,
  ownScratchTargets,
  recentLanes,
  targetsOf,
  unattendedRmVerdict,
  weather,
} from './lib'
import type { Hold } from './lib'

const isHub = atom({ plugin: 'afa-mods', key: 'isHub' } as const, false)
const prepRan = atom({ plugin: 'afa-mods', key: 'prepRan' } as const, false)
const percent = atom({ plugin: 'afa-mods', key: 'percent' } as const, null)
const band = atom({ plugin: 'afa-mods', key: 'band' } as const, { lanes: 0, inbox: 0, client: 0 })

const readText = async ($: Engine, path: string) =>
  (await $.fs.exists(path)) ? String(await $.fs.read(path)) : ''

// Open lanes, the unticked inbox lines and client lines, read from private
// files at run time. A file that fails to read counts as nothing.
async function refreshBand($: Engine) {
  const home = (await $.env.get('HOME')) ?? ''
  const state = `${home}/.claude/state`
  const lanes = recentLanes(
    await readText($, `${state}/lanes/index.tsv`).catch(() => ''),
    await $.clock.now(),
  )
  let open = 0
  for (const l of lanes) {
    const dir = `${state}/lanes/${l.date}-${l.name}`
    try {
      const report = l.report !== '-' ? l.report : `${dir}/report.md`
      if (!(await $.fs.exists(report))) open += 1
      else if (await $.fs.exists(`${dir}/out.log`)) {
        if (!hasExitLine(await readText($, `${dir}/out.log`))) open += 1
      }
    } catch {
      // unreadable lane: not counted
    }
  }
  const { inbox, client } = countInbox(await readText($, `${state}/muse-inbox.md`).catch(() => ''))
  const next = { lanes: open, inbox, client }
  const prev = await read($, band)
  if (JSON.stringify(prev) !== JSON.stringify(next)) await update($, band, () => next)
}

const LOG = '.claude/state/mods/blast-radius.log'

const firstLines = (text: string, n: number) => {
  const lines = text.split('\n').filter(l => l.trim() !== '')
  return lines.slice(0, n).join('\n') + (lines.length > n ? `\n... ${lines.length - n} more` : '')
}

const run = async ($: Engine, argv: string[]) => {
  try {
    const r = await $.process.run(argv, { timeoutMs: 10_000 })
    return r.exitCode === 0 ? r.stdout : ''
  } catch {
    return ''
  }
}

// What one hold would touch, in a few lines. Read-only probes only; a remote
// URL is never printed (it can carry a token).
async function touches($: Engine, home: string, hold: Hold): Promise<string> {
  const head = `${hold.kind}: ${hold.segment.slice(0, 300)}${hold.segment.length > 300 ? ' ... (cut, full command is longer)' : ''}`
  if (hold.kind === 'rm -rf') {
    const out: string[] = []
    const all = targetsOf(hold.segment)
    if (all.length > 8) out.push(`  ... ${all.length - 8} more targets not listed`)
    for (const raw of all.slice(0, 8)) {
      const path = raw.replace(/^~(?=\/|$)/, home).replace(/^["']|["']$/g, '')
      if (/[*?[]/.test(path)) {
        out.push(`  ${path} (pattern, expands when it runs)`)
        continue
      }
      try {
        const st = await $.fs.stat(path)
        if (st.kind === 'directory') {
          const n = (await $.fs.list(path)).length
          out.push(`  ${path} (folder, ${n} entries at the top level, all removed)`)
        } else out.push(`  ${path} (${st.kind}, ${st.size} bytes)`)
      } catch {
        out.push(`  ${path} (not found)`)
      }
    }
    return [head, ...out].join('\n')
  }
  if (/^docker (system|image) prune/.test(hold.kind)) {
    return `${head}\n${firstLines(await run($, ['docker', 'system', 'df']), 6)}`
  }
  if (hold.kind.startsWith('docker volume') || hold.kind === 'docker compose down -v') {
    const named = hold.kind === 'docker volume rm' ? targetsOf(hold.segment).slice(2) : []
    const vols =
      named.length > 0
        ? named.join('\n')
        : firstLines(await run($, ['docker', 'volume', 'ls', '--format', '{{.Name}}']), 10)
    return `${head}\nvolumes in play:\n${vols}`
  }
  if (hold.kind === 'git push --force') {
    const branch = (await run($, ['git', 'branch', '--show-current'])).trim()
    return `${head}\nrewrites remote history; current branch: ${branch || '(detached)'}`
  }
  return head
}

// Non-interactive runs have nobody to ask: the verdict is written down here.
async function note($: Engine, home: string, verdict: string, hold: Hold) {
  try {
    const path = `${home}/${LOG}`
    const old = (await $.fs.exists(path)) ? String(await $.fs.read(path)) : ''
    const line = `${new Date(await $.clock.now()).toISOString()} | ${verdict} | ${hold.kind} | ${hold.segment.slice(0, 160)}`
    const kept = old.split('\n').filter(Boolean).slice(-299)
    await $.fs.write(path, [...kept, line].join('\n') + '\n')
  } catch {
    // a failed log line must not change the verdict
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const home = (await $.env.get('HOME')) ?? ''
    const hub = e.cwd.replace(/\/$/, '') === `${home}/repos`
    await update($, isHub, () => hub)
    if (hub) {
      await refreshBand($).catch(() => undefined)
      $.clock.every(5000, () => refreshBand($).catch(() => undefined))
    }
    return next(e)
  })

  // Hub band: /prep has run in this session
  on('command.run', async ($, e, next) => {
    if (e.command === 'prep') await update($, prepRan, () => true)
    return next(e)
  })
  on('prompt.submit', async ($, e, next) => {
    if (/^\s*\/prep\b/.test(e.text)) await update($, prepRan, () => true)
    return next(e)
  })

  // Token Weather
  on('session.measure', async ($, e, next) => {
    const p = e.context.percent
    await update($, percent, () => (p === undefined ? null : Math.round(p)))
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const hub = await read($, isHub)
    const p = await read($, percent)
    const w = p === null ? null : weather(p, hub)
    if (!hub && w === null) return next(e)

    const { Box, Text } = $.ui.resolve(e)
    const color = w?.kind === 'storm' ? 'error' : w?.kind === 'showers' ? 'warning' : undefined
    return (
      <Box>
        {hub && <Text dimColor>{bandText(await read($, prepRan), await read($, band))}</Text>}
        {hub && w && <Text dimColor> | </Text>}
        {w && (
          <Text color={color} dimColor={color === undefined}>
            {w.text}
          </Text>
        )}
      </Box>
    )
  })

  // Blast Radius
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const home = (await $.env.get('HOME')) ?? ''
    const tmpdir = (await $.env.get('TMPDIR')) ?? undefined
    const scratchEnv = { home, tmpdir }
    const ownScratch = ownScratchTargets(e.command, scratchEnv)
    const holds = classify(e.command, 0, scratchEnv)
    const noteScratch = async () => {
      for (const { targets } of ownScratch) {
        await note($, home, 'allowed (own scratch)', {
          kind: 'rm -rf',
          hard: false,
          segment: targets.join(' '),
        })
      }
    }
    if (holds.length === 0) {
      await noteScratch()
      return next(e)
    }
    // A lane the dispatcher started has nobody at its window (lane.sh sets AFA_LANE_UNATTENDED=1): it never waits.
    // A delete inside its own folder, a worktree or the scratchpad, by a full path, runs; everything else is refused with a reason.
    if ((await $.env.get('AFA_LANE_UNATTENDED')) === '1') {
      const hand = (await $.env.get('AFA_LANE_HAND')) ?? undefined
      const approval = 'Leave it out of this job and say in your report that it needs Jonathan to approve it.'
      const refusals: string[] = []
      for (const h of holds) {
        const verdict =
          h.kind === 'rm -rf'
            ? unattendedRmVerdict(h.segment, { home, hand, tmpdir })
            : { ok: false as const, reason: approval }
        if (verdict.ok && !h.hard) {
          await note($, home, 'allowed (lane folder)', h)
          continue
        }
        await note($, home, 'denied', h)
        const { what, why } = explain(h)
        refusals.push(
          `${h.kind} "${h.segment.slice(0, 200)}" ${what}. Held because ${why}. ${verdict.ok ? approval : verdict.reason}`,
        )
      }
      if (refusals.length > 0) {
        return {
          deny: `Blast Radius refused this command and did not wait, because nobody is at this lane's window to approve it. ${refusals.join(' ')}`,
        }
      }
      await noteScratch()
      return next(e)
    }

    const isInteractive = (await $.session.surfaces()).length > 0

    if (!isInteractive) {
      const hard = holds.find(h => h.hard)
      if (hard) {
        await note($, home, 'denied', hard)
        const { what, why } = explain(hard)
        return {
          deny: `Blast Radius: ${hard.kind} is refused in a non-interactive session (nobody to ask). It ${what}. Held because ${why}.`,
        }
      }
      for (const h of holds) await note($, home, 'allowed', h)
      await noteScratch()
      return next(e)
    }

    const lines: string[] = []
    const plain: string[] = []
    for (const h of holds) {
      lines.push(await touches($, home, h))
      const { what, why } = explain(h)
      plain.push(`- ${h.kind} ${what}. Held because ${why}.`)
    }
    const choice = await $.ui.ask(
      `Blast Radius stopped a command before it ran:\n${plain.join('\n')}\nIt would touch:\n${lines.join('\n')}\nRun it?`,
      ['1 - Run it', '2 - Refuse'],
    )
    if (!choice.startsWith('1')) return { deny: 'Blast Radius: refused at the prompt. The command did not run.' }
    await noteScratch()
    return next(e)
  }).catch(($, e, next) =>
    next.called ? next(e) : { deny: 'Blast Radius: its guard failed, so the command is held.' },
  )
}
