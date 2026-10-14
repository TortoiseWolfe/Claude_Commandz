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

const setup = (on: any) => {
  mock.env(on, { HOME })
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
})
