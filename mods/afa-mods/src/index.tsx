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
  realPathVerdict,
  unattendedRmPaths,
  unattendedRmVerdict,
  unattendedRoots,
  weather,
} from './lib'
import type { Hold } from './lib'
import { touches } from './touches'
import type { Probe } from './touches'

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

const RUN_IT = '1 - Run it'
const REFUSE = '2 - Refuse'

// The dialog returns a label, or the text typed under "Other" ("Type something."). Only the exact Run it label runs the
// command; anything else is a refusal, and a typed reason goes back to the session so it can adjust instead of retrying blind.
const refusal = (choice: string) => {
  const base = 'Blast Radius: refused at the prompt. The command did not run.'
  const reason = choice.trim()
  return reason === '' || reason === REFUSE
    ? base
    : `${base} Jonathan's reason, in his words: "${reason}". Do not resend this command or a reworded copy; change what you are doing or explain it to him in chat.`
}

const run = async ($: Engine, argv: string[]) => {
  try {
    const r = await $.process.run(argv, { timeoutMs: 10_000 })
    return r.exitCode === 0 ? r.stdout : ''
  } catch {
    return ''
  }
}

// What a refusal says about one hold, beyond what it does and why: the things it would have touched
// (touches.ts, the same text the question shows). The engine is passed as the probe: it has the same fs / process calls.
const wouldTouch = async ($: Engine, home: string, hold: Hold) =>
  (await touches($ as unknown as Probe, hold, home)).split('\n').slice(1).map(l => l.trim()).join('; ')

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
        let verdict: { ok: true } | { ok: false; reason: string } =
          h.kind === 'rm -rf'
            ? unattendedRmVerdict(h.segment, { home, hand, tmpdir })
            : { ok: false as const, reason: approval }
        if (verdict.ok && h.kind === 'rm -rf') {
          // The words are inside the lane's folder; now make sure no link on the way leads out of it.
          const laneEnv = { home, hand, tmpdir }
          const resolve = async (path: string) => (await run($, ['realpath', '-m', '--', path])).trim()
          const roots = await Promise.all(unattendedRoots(laneEnv).map(resolve))
          const paths = unattendedRmPaths(h.segment, laneEnv)
          verdict = realPathVerdict(
            await Promise.all(paths.map(async path => ({ path, real: await resolve(path) }))),
            roots.filter(Boolean),
          )
        }
        if (verdict.ok && !h.hard) {
          await note($, home, 'allowed (lane folder)', h)
          continue
        }
        await note($, home, 'denied', h)
        const { what, why } = explain(h)
        refusals.push(
          `${h.kind} "${h.segment.slice(0, 200)}" ${what}. Held because ${why}. It would touch: ${await wouldTouch($, home, h)}. ${verdict.ok ? approval : verdict.reason}`,
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
          deny: `Blast Radius: ${hard.kind} is refused in a non-interactive session (nobody to ask). It ${what}. Held because ${why}. It would touch: ${await wouldTouch($, home, hard)}.`,
        }
      }
      for (const h of holds) await note($, home, 'allowed', h)
      await noteScratch()
      return next(e)
    }

    const lines: string[] = []
    const plain: string[] = []
    for (const h of holds) {
      lines.push(await touches($ as unknown as Probe, h, home))
      const { what, why } = explain(h)
      plain.push(`- ${h.kind} ${what}. Held because ${why}.`)
    }
    const choice = await $.ui.ask(
      `Blast Radius stopped a command before it ran:\n${plain.join('\n')}\nIt would touch:\n${lines.join('\n')}\nRun it?`,
      [RUN_IT, REFUSE],
    )
    if (choice !== RUN_IT) return { deny: refusal(choice) }
    await noteScratch()
    return next(e)
  }).catch(($, e, next) =>
    next.called
      ? next(e)
      : {
          deny: "Blast Radius: its guard failed, so the command is held. It would touch: can't tell what this touches: the guard failed before it could look.",
        },
  )
}
