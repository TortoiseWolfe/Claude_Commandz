import { describe, expect, mock, test } from 'claude-code/testing'

const HOME = '/home/t'
const STATE = `${HOME}/.claude/state`
const FILES: Record<string, string> = {
  [`${STATE}/muse-inbox.md`]: '- [ ] open\n- [ ] Client mail: reply waiting\n- [x] done\n',
  [`${STATE}/lanes/index.tsv`]:
    '2026-10-12\tdone\t/x\tm\tb\t-\t/r/done.md\ts\t-\n2026-10-12\tbusy\t/x\tm\tb\t-\t-\ts\t-\n',
  '/r/done.md': 'report',
}
const PROPS = {
  hasSurvey: false,
  isWorking: false,
  maxRows: 6,
  bodyColumns: 100,
  scroll: { offset: 0, bodyRows: 5, rows: 1 },
} as never

const NOW = Date.parse('2026-10-12T12:00:00Z')

const setup = (on: any, vars: Record<string, string> = {}) => {
  mock.env(on, { HOME, ...vars })
  mock.clock(on, { now: NOW })
  on('session.start', ($: any, e: any) => ({ cwd: e.cwd }))
  on('session.measure', ($: any, e: any) => ({ changed: e.changed }))
  on('ui.render', ($: any, e: any) => h($.ui.resolve(e).Text, {}, 'ENGINE'))
  on('fs.exists', ($: any, e: any) => ({ value: e.path in FILES }))
  on('fs.read', ($: any, e: any) => ({ value: FILES[e.path] ?? '' }))
}

const measure = ($: any, percent: number) =>
  $.session.measure({ context: { window: 200000, percent }, rateLimits: [], changed: ['context'] })

const draw = async ($: any) => {
  const ui = await $.ui.mount({ plugin: 'afa-mods', surface: 'terminal', component: 'AbovePrompt', props: PROPS })
  const text = (await ui.findAll({ type: 'Text' })).map((t: any) => t.text).join('')
  await ui.unmount()
  return text
}

describe('hub band', () => {
  test('shows /prep not run, open lanes, inbox and client mail in ~/repos', async ($, on) => {
    setup(on)
    await $.session.start({ cwd: `${HOME}/repos`, surface: 'terminal', isInteractive: true })
    expect(await draw($)).toBe('/prep not run | 1 lane open | Muse inbox: 1 open | Client mail: 1 new')
  })
  test('/prep clears the first item', async ($, on) => {
    setup(on)
    await $.session.start({ cwd: `${HOME}/repos`, surface: 'terminal', isInteractive: true })
    await $.command.run({ command: 'prep', args: '' }).catch(() => undefined)
    expect(await draw($)).not.toContain('/prep not run')
  })
  test('draws nothing outside the hub before the first measure', async ($, on) => {
    setup(on)
    await $.session.start({ cwd: `${HOME}/repos/Other`, surface: 'terminal', isInteractive: true })
    expect(await draw($)).toBe('ENGINE')
  })
})

describe('token weather', () => {
  for (const [pct, want] of [
    [30, 'Weather: clear 30%'],
    [50, 'Weather: showers 50%'],
    [70, 'Weather: storm 70%'],
    [85, 'Weather: storm 85% - hand off and end'],
  ] as const) {
    test(`${pct}% outside the hub`, async ($, on) => {
      setup(on)
      await $.session.start({ cwd: `${HOME}/repos/Other`, surface: 'terminal', isInteractive: true })
      await measure($, pct)
      expect(await draw($)).toBe(want)
    })
  }
  test('70% in the hub says hand off and end', async ($, on) => {
    setup(on)
    await $.session.start({ cwd: `${HOME}/repos`, surface: 'terminal', isInteractive: true })
    await measure($, 70)
    expect(await draw($)).toContain('Weather: storm 70% - hand off and end')
  })
})

describe('blast radius', () => {
  test('interactive: holds rm -rf and asks; refusing denies', async ($, on) => {
    setup(on)
    let asked = ''
    on('session.surfaces', () => ({ value: ['terminal'] }))
    on('fs.stat', () => ({ value: { kind: 'directory', size: 0, mtimeMs: 0, isLink: false } }))
    on('fs.list', () => ({ value: [{ name: 'a' }, { name: 'b' }] }))
    on('tool.call', { tool: 'AskUserQuestion' }, ($: any, e: any) => {
      asked = JSON.stringify(e)
      return { result: { answers: { q: '2 - Refuse' } }, text: '2 - Refuse' }
    })
    const r: any = await $.tool.call({ tool: 'Bash', command: 'rm -rf /home/t/afa-mods-test' })
    expect(asked).toContain('/home/t/afa-mods-test')
    expect(asked).toContain('2 entries')
    expect(r.deny).toContain('Blast Radius')
  })
  test('non-interactive: denies git push --force, logs it', async ($, on) => {
    setup(on)
    let log = ''
    on('session.surfaces', () => ({ value: [] }))
    on('fs.write', ($: any, e: any) => {
      log += e.text
      return { value: undefined }
    })
    const r: any = await $.tool.call({ tool: 'Bash', command: 'git push --force origin main' })
    expect(r.deny).toContain('non-interactive')
    expect(log).toContain('denied | git push --force')
  })
  test('non-interactive: denies tmux kill-server and names the shared lane windows', async ($, on) => {
    setup(on)
    let log = ''
    on('session.surfaces', () => ({ value: [] }))
    on('fs.write', ($: any, e: any) => {
      log += e.text
      return { value: undefined }
    })
    const r: any = await $.tool.call({ tool: 'Bash', command: 'tmux kill-server' })
    expect(r.deny).toContain("closes every lane window (the tmux session 'lanes'), with no undo")
    expect(r.deny).toContain('every other session and window on the default tmux server')
    expect(log).toContain('denied | tmux shared lanes')
  })
  test('non-interactive: allows rm -rf and logs it', async ($, on) => {
    setup(on)
    let log = ''
    on('session.surfaces', () => ({ value: [] }))
    on('fs.write', ($: any, e: any) => {
      log += e.text
      return { value: undefined }
    })
    on('tool.call', { tool: 'Bash' }, () => ({ result: 'ok', text: 'ok' }))
    const r: any = await $.tool.call({ tool: 'Bash', command: 'rm -rf /home/t/afa-mods-test' })
    expect(r.deny).toBeUndefined()
    expect(log).toContain('allowed | rm -rf')
  })
  test('allows own scratch without prompting and logs its targets', async ($, on) => {
    setup(on)
    let log = ''
    on('fs.write', ($: any, e: any) => {
      log += e.text
      return { value: undefined }
    })
    on('tool.call', { tool: 'Bash' }, () => ({ result: 'ok', text: 'ok' }))
    const r: any = await $.tool.call({ tool: 'Bash', command: 'rm -rf /tmp/afa-mods-test' })
    expect(r.deny).toBeUndefined()
    expect(log).toContain('allowed (own scratch) | rm -rf | /tmp/afa-mods-test')
  })
  test('interactive: the question says in plain words what it stopped and why', async ($, on) => {
    setup(on)
    let asked = ''
    on('session.surfaces', () => ({ value: ['terminal'] }))
    on('fs.stat', () => ({ value: { kind: 'directory', size: 0, mtimeMs: 0, isLink: false } }))
    on('fs.list', () => ({ value: [{ name: 'a' }] }))
    on('tool.call', { tool: 'AskUserQuestion' }, ($: any, e: any) => {
      asked = JSON.stringify(e)
      return { result: { answers: { q: '2 - Refuse' } }, text: '2 - Refuse' }
    })
    const r: any = await $.tool.call({ tool: 'Bash', command: 'rm -rf /home/t/afa-mods-test' })
    expect(asked).toContain('Blast Radius stopped a command before it ran')
    expect(asked).toContain('deletes the named files and folders')
    expect(asked).toContain('a wrong or empty path deletes far more than meant')
    expect(r.deny).toContain('Blast Radius')
  })
})

// A lane the dispatcher started: lane.sh sets AFA_LANE_UNATTENDED=1 and AFA_LANE_HAND. It has a window (so the
// session counts as interactive) but nobody at it, so the guard must never ask.
describe('blast radius, unattended lane', () => {
  const HAND = `${HOME}/handoff/2026-10-16-oc-x`
  // A stand-in for realpath: `links` maps a path that is really a symlink to where it points. Run with `broken`,
  // realpath fails the way it would on a sandbox without the program, and the guard must refuse.
  const lane = async (on: any, command: string, links: Record<string, string> = {}, broken = false) => {
    setup(on, { AFA_LANE_UNATTENDED: '1', AFA_LANE_HAND: HAND })
    on('process.run', (_$: any, e: any) => {
      let path: string = e.argv.at(-1)
      for (const [from, to] of Object.entries(links)) {
        if (path === from || path.startsWith(`${from}/`)) path = to + path.slice(from.length)
      }
      return { value: { exitCode: broken ? 1 : 0, stdout: broken ? '' : `${path}\n`, stderr: '' } }
    })
    const seen = { asked: false, log: '' }
    on('session.surfaces', () => ({ value: ['terminal'] }))
    on('tool.call', { tool: 'AskUserQuestion' }, () => {
      seen.asked = true
      return { result: { answers: { q: '1 - Run it' } }, text: '1 - Run it' }
    })
    on('fs.write', ($: any, e: any) => {
      seen.log += e.text
      return { value: undefined }
    })
    on('tool.call', { tool: 'Bash' }, () => ({ result: 'ok', text: 'ok' }))
    return { seen, run: async ($: any) => ($.tool.call({ tool: 'Bash', command }) as Promise<any>) }
  }
  for (const [what, command] of [
    ['its own folder', `rm -rf ${HAND}/repo`],
    ['a worktree', `rm -rf ${HOME}/repos/.worktrees/Claude_Commandz--topic`],
    ['a worktree by ~', 'rm -rf ~/repos/.worktrees/Claude_Commandz--topic/build'],
  ] as const) {
    test(`allows a delete of ${what} by a full path, without asking`, async ($, on) => {
      const { seen, run } = await lane(on, command)
      const r = await run($)
      expect(r.deny).toBeUndefined()
      expect(seen.asked).toBe(false)
      expect(seen.log).toContain('allowed (lane folder) | rm -rf')
    })
  }
  test('refuses a delete inside its folder when a link on the way leads out of it', async ($, on) => {
    const { seen, run } = await lane(on, `rm -rf ${HAND}/repo/build`, { [`${HAND}/repo`]: `${HOME}/repos/ScriptHammer` })
    const r = await run($)
    expect(seen.asked).toBe(false)
    expect(r.deny).toContain('did not wait')
    expect(r.deny).toContain(`really points to ${HOME}/repos/ScriptHammer/build (through a link)`)
  })
  test('refuses a delete in a worktree whose folder is a link to somewhere else', async ($, on) => {
    const { run } = await lane(on, `rm -rf ${HOME}/repos/.worktrees/App--x/old`, { [`${HOME}/repos/.worktrees/App--x`]: '/etc' })
    expect((await run($)).deny).toContain('really points to /etc/old')
  })
  test('a lane folder that is itself a link is judged by where it really is, so deletes inside it still work', async ($, on) => {
    const { run } = await lane(on, `rm -rf ${HAND}/x`, { [HAND]: '/mnt/data/lane' })
    expect((await run($)).deny).toBeUndefined()
  })
  test('a link that stays inside the folder is fine', async ($, on) => {
    const { seen, run } = await lane(on, `rm -rf ${HAND}/current`, { [`${HAND}/current`]: `${HAND}/repo-2` })
    expect((await run($)).deny).toBeUndefined()
    expect(seen.asked).toBe(false)
  })
  test('refuses when realpath cannot be run: it fails closed', async ($, on) => {
    const { run } = await lane(on, `rm -rf ${HAND}/repo`, {}, true)
    expect((await run($)).deny).toContain('could not be resolved')
  })
  test('refuses a delete through an unset variable, says why, and never asks', async ($, on) => {
    const { seen, run } = await lane(on, 'rm -rf "$BUILD_DIR"/old')
    const r = await run($)
    expect(seen.asked).toBe(false)
    expect(r.deny).toContain('did not wait')
    expect(r.deny).toContain('$BUILD_DIR, which may be empty or unset')
    expect(r.deny).toContain(HAND)
    expect(seen.log).toContain('denied | rm -rf')
  })
  for (const command of [
    `rm -rf ${HOME}/repos/ScriptHammer`,
    `rm -rf ${HOME}/repos/.worktrees`,
    `rm -rf ${HAND}`,
    `rm -rf ${HAND}/../other/x`,
    `rm -rf ${HOME}/repos/.worktrees/a/../../ScriptHammer`,
    'rm -rf build',
    'rm -rf /tmp',
    `rm -rf ${HAND}/*`,
    `rm -rf ${HAND}/repo ${HOME}/other`,
  ]) {
    test(`refuses ${command}`, async ($, on) => {
      const { seen, run } = await lane(on, command)
      const r = await run($)
      expect(seen.asked).toBe(false)
      expect(r.deny).toContain('did not wait')
      expect(r.deny).toContain('Delete only by a full path inside')
    })
  }
  for (const [command, word] of [
    ['docker volume prune -f', 'Docker volume'],
    ['docker compose down -v', 'deletes its volumes'],
    ['git push --force origin main', 'overwrites the branch'],
  ] as const) {
    test(`refuses ${command} with a plain reason`, async ($, on) => {
      const { seen, run } = await lane(on, command)
      const r = await run($)
      expect(seen.asked).toBe(false)
      expect(r.deny).toContain(word)
      expect(r.deny).toContain('needs Jonathan to approve it')
    })
  }
  test('a lane that is not flagged is still asked', async ($, on) => {
    setup(on)
    let asked = false
    on('session.surfaces', () => ({ value: ['terminal'] }))
    on('fs.stat', () => ({ value: { kind: 'directory', size: 0, mtimeMs: 0, isLink: false } }))
    on('fs.list', () => ({ value: [] }))
    on('tool.call', { tool: 'AskUserQuestion' }, () => {
      asked = true
      return { result: { answers: { q: '2 - Refuse' } }, text: '2 - Refuse' }
    })
    await $.tool.call({ tool: 'Bash', command: `rm -rf ${HOME}/repos/ScriptHammer` })
    expect(asked).toBe(true)
  })
})

// Jonathan, 9:10 PM: "I need to understand what it's effecting any time you call it". Every prompt and every
// refusal says what the command does and what it would touch (or that it cannot tell, and why).
describe('blast radius always says what it would touch', () => {
  const RULES: [string, string][] = [
    ['rm -rf', 'rm -rf /home/t/afa-mods-test'],
    ['docker system prune', 'docker system prune -af'],
    ['docker image prune', 'docker image prune -a'],
    ['docker volume prune', 'docker volume prune -f'],
    ['docker volume rm', 'docker volume rm pgdata'],
    ['docker compose down -v', 'docker compose down -v'],
    ['git push --force', 'git push --force origin main'],
    ['production deploy', 'vercel --prod'],
  ]
  const TOUCH = /\(dir,|\(not found\)|\(volume|branch \S+ on remote|service: |host named|disk use|containers that would|images|unused volumes|volumes this stack/
  const CANT = "can't tell what this touches: "
  const says = (text: string) => {
    expect(text).toContain('It would touch: ')
    const part = text.slice(text.indexOf('It would touch: '))
    expect(TOUCH.test(part) || part.includes(CANT)).toBe(true)
    expect(/=>|\/\^|\\s\+|\bexpect\(/.test(part)).toBe(false)
  }
  for (const [kind, command] of RULES) {
    test(`${kind}, interactive: the question says what it does and what it would touch`, async ($, on) => {
      setup(on)
      let asked = ''
      on('session.surfaces', () => ({ value: ['terminal'] }))
      on('fs.stat', () => ({ value: { kind: 'directory', size: 4096, mtimeMs: 0, isLink: false } }))
      on('fs.list', () => ({ value: [{ name: 'a' }] }))
      on('tool.call', { tool: 'AskUserQuestion' }, ($: any, e: any) => {
        asked = JSON.stringify(e)
        return { result: { answers: { q: '2 - Refuse' } }, text: '2 - Refuse' }
      })
      await $.tool.call({ tool: 'Bash', command })
      expect(asked).toContain('Blast Radius stopped a command before it ran')
      expect(asked).toContain(`${kind}`)
      expect(asked).toContain('Held because')
      expect(asked).toContain('It would touch:')
      expect(asked.length).toBeGreaterThan(200)
      expect(TOUCH.test(asked) || asked.includes(CANT)).toBe(true)
    })
    test(`${kind}, unattended lane: the refusal says what it would touch`, async ($, on) => {
      setup(on, { AFA_LANE_UNATTENDED: '1', AFA_LANE_HAND: `${HOME}/handoff/x` })
      on('session.surfaces', () => ({ value: ['terminal'] }))
      on('fs.stat', () => ({ value: { kind: 'directory', size: 4096, mtimeMs: 0, isLink: false } }))
      on('fs.list', () => ({ value: [] }))
      on('process.run', (_$: any, e: any) => ({
        value: { exitCode: 0, stdout: e.argv[0] === 'realpath' ? `${e.argv.at(-1)}\n` : '', stderr: '' },
      }))
      on('fs.write', () => ({ value: undefined }))
      const r: any = await $.tool.call({ tool: 'Bash', command })
      expect(r.deny).toContain('did not wait')
      expect(r.deny).toContain('Held because')
      says(r.deny)
    })
  }
  for (const [kind, command] of RULES.filter(([k]) => k === 'git push --force' || k === 'production deploy')) {
    test(`${kind}, non-interactive: the hard refusal says what it would touch`, async ($, on) => {
      setup(on)
      on('session.surfaces', () => ({ value: [] }))
      on('fs.write', () => ({ value: undefined }))
      const r: any = await $.tool.call({ tool: 'Bash', command })
      expect(r.deny).toContain('non-interactive')
      says(r.deny)
    })
  }
  test('a rule whose lookup fails still says it cannot tell and why, never nothing', async ($, on) => {
    setup(on, { AFA_LANE_UNATTENDED: '1', AFA_LANE_HAND: `${HOME}/handoff/x` })
    on('session.surfaces', () => ({ value: ['terminal'] }))
    on('process.run', () => {
      throw new Error('no docker here')
    })
    on('fs.write', () => ({ value: undefined }))
    const r: any = await $.tool.call({ tool: 'Bash', command: 'docker volume prune -f' })
    expect(r.deny).toContain(CANT)
  })
})
