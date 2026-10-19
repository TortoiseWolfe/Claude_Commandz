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

// The dialog's answer is keyed by the question text; a key that is not the question reads as "no answer" and the guard fails closed.
const answer = (e: any, label: string) => ({ result: { answers: { [e.questions[0].question]: label } }, text: label })

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
      return answer(e, '2 - Refuse')
    })
    const r: any = await $.tool.call({ tool: 'Bash', command: 'rm -rf /home/t/afa-mods-test' })
    expect(asked).toContain('/home/t/afa-mods-test')
    expect(asked).toContain('2 entries')
    expect(r.deny).toContain('Blast Radius')
  })
  // 19 Oct: Jonathan refused two commands and typed why under "Type something." (the dialog's Other); the tool
  // result only said "refused at the prompt", so the session retried blind.
  const refuseWith = async ($: any, on: any, label: string) => {
    setup(on)
    on('session.surfaces', () => ({ value: ['terminal'] }))
    on('fs.stat', () => ({ value: { kind: 'directory', size: 0, mtimeMs: 0, isLink: false } }))
    on('fs.list', () => ({ value: [] }))
    on('tool.call', { tool: 'Bash' }, () => ({ result: 'ok', text: 'ok' }))
    on('tool.call', { tool: 'AskUserQuestion' }, ($: any, e: any) => answer(e, label))
    return (await $.tool.call({ tool: 'Bash', command: 'rm -rf /home/t/afa-mods-test' })) as any
  }
  test('interactive: the reason he types when refusing reaches the session that ran the command', async ($, on) => {
    const reason = 'Your description was too vague. Say which folder this reads.'
    const r = await refuseWith($, on, reason)
    expect(r.deny).toContain('Blast Radius')
    expect(r.deny).toContain('did not run')
    expect(r.deny).toContain(reason)
    expect(r.deny).toContain('Do not resend')
  })
  test('interactive: a typed reason that starts with 1 is a refusal, never a Run it', async ($, on) => {
    const reason = '1 hour later, after the window'
    const r = await refuseWith($, on, reason)
    expect(r.deny).toContain(reason)
  })
  test('interactive: plain Refuse adds no reason and Run it still runs', async ($, on) => {
    const r = await refuseWith($, on, '2 - Refuse')
    expect(r.deny).toBe('Blast Radius: refused at the prompt. The command did not run.')
    const ran = await refuseWith($, on, '1 - Run it').catch((e: unknown) => e)
    expect((ran as any).deny).toBeUndefined()
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
      return answer(e, '2 - Refuse')
    })
    const r: any = await $.tool.call({ tool: 'Bash', command: 'rm -rf /home/t/afa-mods-test' })
    expect(asked).toContain('Blast Radius stopped a command before it ran')
    expect(asked).toContain('deletes the named files and folders')
    expect(asked).toContain('a wrong or empty path deletes far more than meant')
    expect(r.deny).toContain('refused at the prompt')
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
    on('tool.call', { tool: 'AskUserQuestion' }, ($: any, e: any) => {
      seen.asked = true
      return answer(e, '1 - Run it')
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
      return answer(e, '2 - Refuse')
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
        return answer(e, '2 - Refuse')
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

// 19 Oct: an rm inside `docker run -v ...` froze lanes on "can't tell what this touches". The mounts now say what it touches.
describe('blast radius, rm inside docker run', () => {
  const WT = `${HOME}/repos/.worktrees/ScriptHammer--skip-conformance`
  const SCRIPTHAMMER = `cd ~/repos/.worktrees && docker run --rm -v "$PWD/ScriptHammer--skip-conformance:/w" alpine sh -c 'rm -rf /w/* /w/.[!.]* /w/..?*'`
  const CHATTANOOGA = `docker run --rm -v cd-repro-core:/work cd-repro sh -c 'rm -rf /work/.good'`
  // these tests are about the rm in a container: the CPU cap is added so the limits hold stays out of them (limits.test.ts covers it)
  const capped = (c: string) => c.replace(/\bdocker run\b/g, 'docker run --cpus 1')
  const go = async ($: any, on: any, rawCommand: string, vars: Record<string, string> = {}, device = '', raw = false) => {
    const command = raw ? rawCommand : capped(rawCommand)
    setup(on, vars)
    const seen = { asked: '', log: '' }
    on('session.surfaces', () => ({ value: ['terminal'] }))
    on('fs.stat', () => ({ value: { kind: 'directory', size: 0, mtimeMs: 0, isLink: false } }))
    on('fs.list', () => ({ value: [{ name: 'a' }, { name: 'b' }, { name: 'c' }] }))
    on('process.run', (_$: any, e: any) => ({
      value: e.argv.includes('inspect') && device === '!fail'
        ? { exitCode: 1, stdout: '', stderr: 'Cannot connect to the Docker daemon' }
        : e.argv.includes('inspect') && device === '!none'
          ? { exitCode: 1, stdout: '', stderr: 'Error: No such volume: data' }
          : { exitCode: 0, stdout: e.argv.includes('inspect') ? `${device}\n` : `${e.argv.at(-1)}\n`, stderr: '' },
    }))
    on('tool.call', { tool: 'AskUserQuestion' }, ($: any, e: any) => {
      seen.asked = JSON.stringify(e)
      return answer(e, '2 - Refuse')
    })
    on('fs.write', ($: any, e: any) => {
      seen.log += e.text
      return { value: undefined }
    })
    on('tool.call', { tool: 'Bash' }, () => ({ result: 'ok', text: 'ok' }))
    const r: any = await $.tool.call({ tool: 'Bash', command })
    return { seen, r }
  }
  test('interactive: the question names the host folder /w stands for', async ($, on) => {
    const { seen, r } = await go($, on, SCRIPTHAMMER)
    expect(seen.asked).toContain('/w is ~/repos/.worktrees/ScriptHammer--skip-conformance on this machine')
    expect(seen.asked).toContain('3 entries at the top level')
    expect(seen.asked).not.toContain("can't tell")
    expect(r.deny).toContain('refused at the prompt')
  })
  test('interactive: a named Docker volume runs without asking', async ($, on) => {
    const { seen, r } = await go($, on, CHATTANOOGA)
    expect(seen.asked).toBe('')
    expect(r.deny).toBeUndefined()
  })
  for (const mount of ['$HOME:/h', '~/repos:/h']) {
    test(`interactive: a mount of ${mount} still asks, and says it is that folder`, async ($, on) => {
      const { seen, r } = await go($, on, `docker run --rm -v ${mount} alpine sh -c 'rm -rf /h/x'`)
      expect(seen.asked).toContain('on this machine (a host folder mounted into the container)')
      expect(seen.asked).toContain(mount.startsWith('~') ? '/h is ~/repos on this machine' : '/h is ~ on this machine')
      expect(r.deny).toContain('refused at the prompt')
    })
  }
  test('interactive: an unresolved mount still asks and names it', async ($, on) => {
    const { seen, r } = await go($, on, `docker run --rm -v $WHERE:/w alpine sh -c 'rm -rf /w/x'`)
    expect(seen.asked).toContain('/w is mounted from \\"$WHERE\\"')
    expect(r.deny).toContain('refused at the prompt')
  })
  test('interactive: a named volume docker reports as a host folder asks, naming that folder', async ($, on) => {
    const { seen, r } = await go($, on, `docker run --rm -v data:/w alpine sh -c 'rm -rf /w/x'`, {}, `${HOME}/p`)
    expect(seen.asked).toContain('/w is ~/p on this machine')
    expect(r.deny).toContain('refused at the prompt')
  })
  test('interactive: docker exec is unchanged, held with the plain host reading', async ($, on) => {
    const { seen, r } = await go($, on, `docker exec app sh -c 'rm -rf /w/x'`)
    expect(seen.asked).toContain('/w/x')
    expect(seen.asked).not.toContain('on this machine')
    expect(r.deny).toContain('refused at the prompt')
  })
  describe('in a lane nobody watches', () => {
    const run = async ($: any, on: any, command: string, device = '', raw = false) => {
      const vars = { AFA_LANE_UNATTENDED: '1', AFA_LANE_HAND: `${HOME}/handoff/lane` }
      return go($, on, command, vars, device, raw)
    }
    test('a named volume runs and is not held at all', async ($, on) => {
      const { seen, r } = await run($, on, CHATTANOOGA)
      expect(r.deny).toBeUndefined()
      expect(seen.asked).toBe('')
      expect(seen.log).toBe('')
    })
    test('a delete inside a worktree through a mount runs, logged as the lane folder', async ($, on) => {
      const { seen, r } = await run($, on, `docker run --rm -v ${WT}:/w alpine sh -c 'rm -rf /w/build'`)
      expect(r.deny).toBeUndefined()
      expect(seen.asked).toBe('')
      expect(seen.log).toContain('allowed (lane folder) | rm -rf')
    })
    test('the ScriptHammer pattern is refused, naming the host folder and not waiting', async ($, on) => {
      const { seen, r } = await run($, on, SCRIPTHAMMER)
      expect(seen.asked).toBe('')
      expect(r.deny).toContain('did not wait')
      expect(r.deny).toContain(`"${WT}/*" uses a pattern`)
      expect(r.deny).toContain('/w is ~/repos/.worktrees/ScriptHammer--skip-conformance on this machine')
    })
    for (const mount of [`${HOME}:/h`, `${HOME}/repos:/h`, '$HOME:/h']) {
      test(`a mount of ${mount} is refused`, async ($, on) => {
        const { seen, r } = await run($, on, `docker run --rm -v ${mount} alpine sh -c 'rm -rf /h/x'`)
        expect(seen.asked).toBe('')
        expect(r.deny).toContain('did not wait')
        expect(r.deny).toContain('outside the places a lane may clean up')
      })
    }
    test('a mount whose source cannot be resolved is refused, naming the mount', async ($, on) => {
      const { r } = await run($, on, `docker run --rm -v $WHERE:/w alpine sh -c 'rm -rf /w/x'`)
      expect(r.deny).toContain('the mount "$WHERE:/w" is a host folder that goes through $WHERE')
    })
    test('a link inside the mounted worktree that leads out of it is still refused', async ($, on) => {
      setup(on, { AFA_LANE_UNATTENDED: '1' })
      on('session.surfaces', () => ({ value: ['terminal'] }))
      on('process.run', (_$: any, e: any) => {
        const path: string = e.argv.at(-1)
        return { value: { exitCode: 0, stdout: `${path.startsWith(`${WT}/build`) ? '/etc' : path}\n`, stderr: '' } }
      })
      on('tool.call', { tool: 'Bash' }, () => ({ result: 'ok', text: 'ok' }))
      const r: any = await $.tool.call({ tool: 'Bash', command: `docker run --rm -v ${WT}:/w alpine sh -c 'rm -rf /w/build'` })
      expect(r.deny).toContain('really points to /etc')
    })
    test('a named volume docker reports as a host folder is judged as that folder', async ($, on) => {
      const { seen, r } = await run($, on, `docker run --rm -v data:/w alpine sh -c 'rm -rf /w/x'`, `${HOME}/p`)
      expect(seen.asked).toBe('')
      expect(r.deny).toContain('did not wait')
      expect(r.deny).toContain('outside the places a lane may clean up')
    })
    test('a volume docker cannot be asked about is held, not assumed harmless', async ($, on) => {
      const { r } = await run($, on, `docker run --rm -v data:/w alpine sh -c 'rm -rf /w/x'`, '!fail')
      expect(r.deny).toContain('did not wait')
    })
    test('a volume docker does not know is unknown, not new: the same command may make it', async ($, on) => {
      const { r } = await run($, on, `docker run --rm -v data:/w alpine sh -c 'rm -rf /w/x'`, '!none')
      expect(r.deny).toContain('did not wait')
    })
    test('a command that sets the old marker name as a variable cannot talk its way past an unanswered volume', async ($, on) => {
      const { r } = await run($, on, `UNINSPECTED_VOLUME=/tmp/z; docker run --rm -v data:/w alpine sh -c 'rm -rf /w/x'`, '!fail')
      expect(r.deny).toContain('did not wait')
    })
    test('a volume the same command creates is not new just because docker does not know it yet', async ($, on) => {
      const { r } = await run($, on, `docker volume create --opt type=none --opt o=bind --opt device=${HOME}/p data && docker run --rm -v data:/w alpine sh -c 'rm -rf /w/x'`, '!none')
      expect(r.deny).toContain('did not wait')
    })
    test('a volume made by another docker command is not new', async ($, on) => {
      const { r } = await run($, on, `d=docker; $d volume create data; docker run --rm -v data:/w alpine sh -c 'rm -rf /w/x'`, '!none')
      expect(r.deny).toContain('did not wait')
    })
    test('every named volume is asked about, however many a command mounts', async ($, on) => {
      const mounts = Array.from({ length: 9 }, (_, i) => `-v v${i}:/m${i}`).join(' ')
      const { r } = await run($, on, `docker run --rm ${mounts} alpine sh -c 'rm -rf /m8/x'`, '!fail')
      expect(r.deny).toContain('did not wait')
    })
    test('docker exec is unchanged: refused with the plain reason', async ($, on) => {
      const { r } = await run($, on, `docker exec app sh -c 'rm -rf /w/x'`)
      expect(r.deny).toContain('did not wait')
      expect(r.deny).toContain('is outside the places a lane may clean up')
    })
  })
})

describe('blast radius, docker run with no CPU cap and no timeout', () => {
  const go = async ($: any, on: any, command: string, vars: Record<string, string> = {}, files: Record<string, string> = {}) => {
    setup(on, vars)
    const seen = { asked: '' }
    on('session.surfaces', () => ({ value: ['terminal'] }))
    Object.assign(FILES, files)
    on('fs.write', () => ({ value: undefined }))
    on('process.run', (_$: any, e: any) => ({ value: { exitCode: 0, stdout: `${e.argv.at(-1)}\n`, stderr: '' } }))
    on('tool.call', { tool: 'AskUserQuestion' }, ($: any, e: any) => {
      seen.asked = JSON.stringify(e)
      return answer(e, '2 - Refuse')
    })
    on('tool.call', { tool: 'Bash' }, () => ({ result: 'ok', text: 'ok' }))
    const r: any = await $.tool.call({ tool: 'Bash', command })
    return { seen, r }
  }
  const INCIDENT = 'docker run ada-stair-generator-site-tools'

  test('interactive: the incident command asks, naming the fix', async ($, on) => {
    const { seen, r } = await go($, on, INCIDENT)
    expect(seen.asked).toContain('docker run without limits')
    expect(seen.asked).toContain('no CPU cap and no timeout')
    expect(seen.asked).toContain('docker-heavy.sh')
    expect(r.deny).toContain('refused at the prompt')
  })
  test('interactive: a capped run, a timeout and docker-heavy.sh run without asking', async ($, on) => {
    const { seen, r } = await go($, on, 'docker run --cpus 4 img')
    expect(seen.asked).toBe('')
    expect(r.deny).toBeUndefined()
  })
  test('interactive: docker-heavy.sh runs without asking', async ($, on) => {
    const { seen, r } = await go($, on, '~/.claude/scripts/docker-heavy.sh -- docker run img')
    expect(seen.asked).toBe('')
    expect(r.deny).toBeUndefined()
  })
  test('interactive: docker ps, logs, inspect, stats and update are never held', async ($, on) => {
    const { seen, r } = await go($, on, 'docker ps && docker logs web && docker inspect web && docker stats --no-stream && docker update --cpus 4 web')
    expect(seen.asked).toBe('')
    expect(r.deny).toBeUndefined()
  })
  test('a lane nobody watches is refused with the fix and told to run it again', async ($, on) => {
    const { seen, r } = await go($, on, INCIDENT, { AFA_LANE_UNATTENDED: '1', AFA_LANE_HAND: `${HOME}/handoff/lane` })
    expect(seen.asked).toBe('')
    expect(r.deny).toContain('did not wait')
    expect(r.deny).toContain('fix: add --cpus N')
    expect(r.deny).toContain('Run it again with the fix')
    expect(r.deny).not.toContain('needs Jonathan to approve')
  })
  test('a lane with --cpus and a timeout in front runs', async ($, on) => {
    const { r } = await go($, on, 'timeout 600 docker run --cpus 4 img', { AFA_LANE_UNATTENDED: '1', AFA_LANE_HAND: `${HOME}/handoff/lane` })
    expect(r.deny).toBeUndefined()
  })
  test('a compose service whose file sets cpus is capped; the same run on a file with no limit is held', async ($, on) => {
    const capped = await go($, on, 'cd /srv/app && docker compose run --rm web npm test', {}, { '/srv/app/compose.yaml': 'services:\n  web:\n    image: n\n    cpus: 4\n' })
    expect(capped.seen.asked).toBe('')
    expect(capped.r.deny).toBeUndefined()
  })
  test('a compose service with no limit in its file is held', async ($, on) => {
    const { seen, r } = await go($, on, 'cd /srv/app && docker compose run --rm web npm test', {}, { '/srv/app/compose.yaml': 'services:\n  web:\n    image: n\n' })
    expect(seen.asked).toContain('docker run without limits')
    expect(r.deny).toContain('refused at the prompt')
  })
  test('a compose run whose file cannot be found, or a cd the guard cannot follow, is held', async ($, on) => {
    const { seen } = await go($, on, 'cd "$X" && docker compose run --rm web npm test', {}, { '/srv/app/compose.yaml': 'services:\n  web:\n    cpus: 4\n' })
    expect(seen.asked).toContain('docker run without limits')
  })
})
