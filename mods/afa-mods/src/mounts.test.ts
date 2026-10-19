import { describe, expect, test } from 'claude-code/testing'

import { classify, hostRmSegment } from './lib'
import type { Hold } from './lib'
import { touches } from './touches'
import type { Probe } from './touches'

const HOME = '/home/t'
const held = (command: string, home = HOME): Hold[] => classify(command, 0, { home })
const WT = `${HOME}/repos/.worktrees/ScriptHammer--skip-conformance`

// 19 Oct: both of these froze a lane on "can't tell what this touches" and the hub dug through transcripts to clear them.
const SCRIPTHAMMER = `cd ~/repos/.worktrees && docker run --rm -v "$PWD/ScriptHammer--skip-conformance:/w" alpine sh -c 'rm -rf /w/* /w/.[!.]* /w/..?*'`
const CHATTANOOGA = `docker run --rm -v cd-repro-core:/work cd-repro sh -c 'rm -rf /work/.good'`

const probe = (files: Record<string, [string, number, number]> = {}): Probe => ({
  fs: {
    stat: async path => {
      if (!(path in files)) throw new Error('not found')
      return { kind: files[path][0], size: files[path][1] }
    },
    list: async path => Array.from({ length: files[path]?.[2] ?? 0 }, (_, i) => ({ name: `e${i}` })),
    read: async () => '',
    exists: async () => false,
  },
  process: { run: async () => ({ exitCode: 1, stdout: '' }) },
})

describe('rm -rf inside docker run: the mounts say what it touches', () => {
  test('ScriptHammer: a bind mount through $PWD after cd resolves to the worktree folder, and still holds like a host rm', () => {
    const holds = held(SCRIPTHAMMER)
    expect(holds.map(h => h.kind)).toEqual(['rm -rf'])
    const mapped = holds[0].container ?? []
    expect(mapped.map(m => m.kind)).toEqual(['host', 'host', 'host'])
    expect(mapped.map(m => (m.kind === 'host' ? m.host : ''))).toEqual([`${WT}/*`, `${WT}/.[!.]*`, `${WT}/..?*`])
    expect(mapped.every(m => m.kind === 'host' && m.folder === WT && m.mountAt === '/w')).toBe(true)
  })
  test('Chattanooga: a named Docker volume is not a host deletion, so nothing is held', () => {
    expect(held(CHATTANOOGA)).toEqual([])
    expect(held(`docker run --rm -v cd-repro-core:/work cd-repro bash -c 'rm -rf /work/.good'`)).toEqual([])
    expect(held(`docker run --rm -v cd-repro-core:/work:ro cd-repro sh -c 'rm -rf /work/.good'`)).toEqual([])
    expect(held(`docker compose run --rm -v cd-repro-core:/work app sh -c 'rm -rf /work/.good'`)).toEqual([])
    expect(held(`docker-compose run -v cd-repro-core:/work app sh -c 'rm -rf /work/.good'`)).toEqual([])
    expect(held(`docker run --rm --volume cd-repro-core:/work cd-repro sh -c 'rm -rf /work/.good'`)).toEqual([])
    expect(held(`docker run --rm --volume=cd-repro-core:/work cd-repro sh -c 'rm -rf /work/.good'`)).toEqual([])
    expect(held(`docker run --rm --mount type=volume,source=cd-repro-core,target=/work cd-repro sh -c 'rm -rf /work/.good'`)).toEqual([])
    expect(held(`docker run --rm --mount source=cd-repro-core,target=/work cd-repro sh -c 'rm -rf /work/.good'`)).toEqual([])
    expect(held(`docker run --rm -v /scratchvol cd-repro sh -c 'rm -rf /scratchvol/x'`)).toEqual([])
    expect(held(`docker run --rm --tmpfs /w --mount type=tmpfs,target=/t cd-repro sh -c 'rm -rf /t/x'`)).toEqual([])
  })
  test('a dot source (compose reads it as a path) and a volume made over a host folder are host folders, not volumes', () => {
    expect(held(`cd ${HOME}/p && docker compose run -v .:/w app sh -c 'rm -rf /w/x'`)[0].container?.[0]).toMatchObject({ kind: 'host', host: `${HOME}/p/x` })
    expect(held(`docker compose run -v ./sub:/w app sh -c 'rm -rf /w/x'`)[0].container?.[0].kind).toBe('unresolved')
    expect(
      held(`docker run --rm --mount type=volume,target=/w,volume-opt=type=none,volume-opt=o=bind,volume-opt=device=${HOME}/p alpine sh -c 'rm -rf /w/x'`)[0].container?.[0],
    ).toMatchObject({ kind: 'host', host: `${HOME}/p/x` })
  })
  test('a mount of $HOME or ~/repos still holds, however it is written', () => {
    for (const mount of [
      '-v $HOME:/h',
      '-v "$HOME":/h',
      '-v ${HOME}:/h',
      '-v ~:/h',
      `-v ${HOME}:/h`,
      '-v ~/repos:/h',
      `-v ${HOME}/repos:/h:rw`,
      '--volume=$HOME:/h',
      '--mount type=bind,source=$HOME,target=/h',
      '--mount type=bind,src=/home/t/repos,dst=/h',
      '--mount source=/home/t,target=/h',
    ]) {
      const holds = held(`docker run --rm ${mount} alpine sh -c 'rm -rf /h/x'`)
      expect(holds.map(h => h.kind), mount).toEqual(['rm -rf'])
      const m = holds[0].container?.[0]
      expect(m?.kind, mount).toBe('host')
      expect(m && m.kind === 'host' ? m.host : '', mount).toMatch(/^\/home\/t(\/repos)?\/x$/)
    }
  })
  test('docker run through the whole $HOME, with its own scratch name inside the container, does not slip through as /tmp', () => {
    const holds = held(`docker run --rm -v $HOME:/tmp alpine sh -c 'rm -rf /tmp/x'`)
    expect(holds.map(h => h.kind)).toEqual(['rm -rf'])
    expect(holds[0].container?.[0]).toMatchObject({ kind: 'host', host: `${HOME}/x` })
  })
  test('a source that cannot be resolved keeps holding and names the mount', () => {
    for (const source of ['$X', '${X}/w', '$PWD/w']) {
      const holds = held(`docker run --rm -v ${source}:/w alpine sh -c 'rm -rf /w/x'`)
      expect(holds.map(h => h.kind), source).toEqual(['rm -rf'])
      expect(holds[0].container?.[0], source).toMatchObject({ kind: 'unresolved', source, mountAt: '/w' })
    }
  })
  test('a source built by $( ) or backticks holds and names the mount; the splitter used to lose the whole command', () => {
    for (const mount of ['"$(pwd)/x:/w"', '$(pwd)/x:/w', '`pwd`:/w', '--volume="$(git rev-parse --show-toplevel):/w"', '--mount type=bind,source="$(pwd)",target=/w']) {
      const flag = mount.startsWith('--') ? mount : `-v ${mount}`
      const holds = held(`docker run --rm ${flag} alpine sh -c 'rm -rf /w/x'`)
      expect(holds.map(h => h.kind), mount).toEqual(['rm -rf'])
      expect(holds[0].container?.[0], mount).toMatchObject({ kind: 'unresolved', mountAt: '/w' })
      expect(holds[0].container?.[0].kind === 'unresolved' && holds[0].container[0].source, mount).toContain('$(...)')
    }
    // a substitution elsewhere on the line changes nothing for a volume, and the substitution's own commands are still read
    expect(held(`docker run --rm -v data:/w alpine sh -c 'rm -rf /w/x' "$(date)"`)).toEqual([])
    expect(held(`docker run --rm -v "$(rm -rf /home/t/y):/w" alpine true`).map(h => h.kind)).toEqual(['rm -rf'])
    // a host rm on the same line is reported once
    expect(held(`docker run --rm -v "$(pwd):/w" alpine true; rm -rf /home/t/y`).map(h => h.segment)).toEqual(['rm -rf /home/t/y'])
  })
  test('$PWD is trusted only after a plain cd to a full path, not after a subshell, pushd or an unknown path', () => {
    const run = (before: string) => `${before} docker run --rm -v $PWD:/w alpine sh -c 'rm -rf /w/x'`
    expect(held(run('cd /home/t/a &&'))[0].container?.[0]).toMatchObject({ kind: 'host', host: '/home/t/a/x' })
    expect(held(run('cd /tmp/a &&'))).toEqual([]) // scratch, as a host rm of /tmp/a/x would be
    expect(held(run('cd ~/repos/App &&'))[0].container?.[0]).toMatchObject({ kind: 'host', host: `${HOME}/repos/App/x` })
    for (const before of ['(cd /tmp/a &&', 'pushd /tmp/a &&', 'cd $SOMEWHERE &&', 'cd rel &&', 'cd - &&', '']) {
      const holds = held(before.startsWith('(') ? `${before} docker run --rm -v $PWD:/w alpine sh -c 'rm -rf /w/x')` : run(before))
      expect(holds[0].container?.[0].kind, before).toBe('unresolved')
    }
  })
  test('a variable the command sets to a plain path resolves; one made by mktemp does not', () => {
    const lit = held(`D=${HOME}/repos/.worktrees/App--x; docker run --rm -v $D:/w alpine sh -c 'rm -rf /w/old'`)
    expect(lit[0].container?.[0]).toMatchObject({ kind: 'host', host: `${HOME}/repos/.worktrees/App--x/old` })
    const tmp = held(`D=$(mktemp -d); docker run --rm -v $D:/w alpine sh -c 'rm -rf /w/old'`)
    expect(tmp[0].container?.[0].kind).toBe('unresolved')
  })
  test('a host folder is judged as a host rm would be: scratch folders pass, patterns and everything else hold', () => {
    expect(held(`docker run --rm -v /tmp/x:/w alpine sh -c 'rm -rf /w/old'`)).toEqual([])
    expect(held(`docker run --rm -v /tmp/x:/w alpine sh -c 'rm -rf /w/*'`).map(h => h.kind)).toEqual(['rm -rf'])
    expect(held(`docker run --rm -v /tmp:/w alpine sh -c 'rm -rf /w'`).map(h => h.kind)).toEqual(['rm -rf'])
    expect(held(`docker run --rm -v ${HOME}/repos/App:/w alpine sh -c 'rm -rf /w/node_modules/.cache'`)).toEqual([])
    expect(held(`docker run --rm -v ${HOME}/repos/App:/w alpine sh -c 'rm -rf /w/src'`).map(h => h.kind)).toEqual(['rm -rf'])
  })
  test('the longest mount holds the path; a mount below the path is reached too', () => {
    const under = held(`docker run --rm -v ${HOME}:/w -v data:/w/data alpine sh -c 'rm -rf /w/data/x'`)
    expect(under).toEqual([])
    const above = held(`docker run --rm -v ${HOME}/p:/w/sub alpine sh -c 'rm -rf /w'`)
    expect(above.map(h => h.kind)).toEqual(['rm -rf'])
    expect(above[0].container?.[0]).toMatchObject({ kind: 'host', folder: `${HOME}/p`, host: `${HOME}/p` })
    const pattern = held(`docker run --rm -v data:/w -v ${HOME}/p:/w/sub alpine sh -c 'rm -rf /w/*'`)
    expect(pattern[0].container?.map(m => m.kind)).toEqual(['volume', 'host'])
  })
  test('a path no mount covers, a climb out of the mount and a relative path still hold', () => {
    const rest = held(`docker run --rm -v data:/w alpine sh -c 'rm -rf /etc/x'`)
    expect(rest.map(h => h.kind)).toEqual(['rm -rf'])
    expect(rest[0].container?.[0].kind).toBe('uncovered')
    expect(held(`docker run --rm -v data:/w alpine sh -c 'rm -rf /w/../etc'`)[0].container?.[0].kind).toBe('uncovered')
    expect(held(`docker run --rm -v data:/w alpine sh -c 'rm -rf build'`)[0].container?.[0].kind).toBe('uncovered')
    expect(held(`docker run --rm -v data:/w alpine sh -c 'rm -rf $OUT'`)[0].container?.[0].kind).toBe('uncovered')
    // the mount does not hide a second, host path in the same rm
    expect(held(`docker run --rm -v data:/w -v ${HOME}:/h alpine sh -c 'rm -rf /w/a /h/b'`)[0].container?.map(m => m.kind)).toEqual(['volume', 'host'])
  })
  test('docker exec and a docker run with no mounts are unchanged: held, with no mount to read', () => {
    for (const command of [
      `docker exec app sh -c 'rm -rf /w/x'`,
      `docker compose exec app sh -c 'rm -rf /w/x'`,
      `docker run --rm alpine sh -c 'rm -rf /w/x'`,
    ]) {
      const holds = held(command)
      expect(holds.map(h => h.kind), command).toEqual(['rm -rf'])
      expect(holds[0].container, command).toBeUndefined()
    }
    // a mount on a docker run does not leak into a later docker exec
    const later = held(`docker run --rm -v data:/w alpine true; docker exec app sh -c 'rm -rf /w/x'`)
    expect(later.map(h => h.kind)).toEqual(['rm -rf'])
    expect(later[0].container).toBeUndefined()
  })
  test('hostRmSegment turns the mapped paths into one host rm, or says why it cannot', () => {
    expect(hostRmSegment(held(`docker run --rm -v ${HOME}/p:/w alpine sh -c 'rm -rf /w/a /w/b'`)[0])).toEqual({ segment: `rm -rf ${HOME}/p/a ${HOME}/p/b` })
    expect(hostRmSegment(held(`docker run --rm -v data:/w alpine sh -c 'rm -rf /w/a /etc/b'`)[0])).toMatchObject({ reason: expect.stringContaining('/etc/b') })
    expect(hostRmSegment(held(`docker run --rm -v $X:/w alpine sh -c 'rm -rf /w/a'`)[0])).toMatchObject({ reason: expect.stringContaining('"$X:/w"') })
    expect(hostRmSegment(held('rm -rf /home/t/x')[0])).toEqual({ segment: 'rm -rf /home/t/x' })
  })
})

describe('what the dialog says about an rm inside docker run', () => {
  test('ScriptHammer: shows the resolved host folder for /w, and what is in it', async () => {
    const text = await touches(probe({ [WT]: ['directory', 4096, 3] }), held(SCRIPTHAMMER)[0], HOME)
    expect(text).toContain('/w is ~/repos/.worktrees/ScriptHammer--skip-conformance on this machine')
    expect(text).toContain('a host folder mounted into the container')
    expect(text).toContain('3 entries at the top level')
    expect(text).not.toContain("can't tell")
  })
  test('a mount of $HOME shows the home folder', async () => {
    const text = await touches(probe({ [HOME]: ['directory', 4096, 40] }), held(`docker run --rm -v $HOME:/h alpine sh -c 'rm -rf /h'`)[0], HOME)
    expect(text).toContain('/h is ~ on this machine')
    expect(text).toContain('40 entries at the top level, all removed')
  })
  test('a $(...) mount source reads as $(...) in the dialog, never the internal placeholder', async () => {
    const text = await touches(probe(), held(`docker run --rm -v "$(pwd)/x:/w" alpine sh -c 'rm -rf /w/x'`)[0], HOME)
    expect(text).toContain('/w is mounted from "$(...)/x", a host folder that is built by another command')
    expect(text).not.toContain('CMDSUBST')
  })
  test('an unresolved mount names the mount it could not read', async () => {
    const text = await touches(probe(), held(`docker run --rm -v $X:/w alpine sh -c 'rm -rf /w/x'`)[0], HOME)
    expect(text).toContain("can't tell what this touches: /w is mounted from \"$X\"")
    expect(text).toContain('goes through $X')
  })
  test('a volume mixed with a host path says which is which', async () => {
    const text = await touches(probe({ [`${HOME}/b`]: ['directory', 4096, 1] }), held(`docker run --rm -v data:/w -v ${HOME}:/h alpine sh -c 'rm -rf /w/a /h/b'`)[0], HOME)
    expect(text).toContain('/w/a: /w is the Docker volume "data", not a folder on this machine')
    expect(text).toContain('/h/b: /h is ~ on this machine')
  })
  test('docker exec still gets the plain host reading it had', async () => {
    const text = await touches(probe(), held(`docker exec app sh -c 'rm -rf /w/x'`)[0], HOME)
    expect(text).toContain('/w/x (not found)')
  })
})
