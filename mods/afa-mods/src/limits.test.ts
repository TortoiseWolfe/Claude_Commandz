import { describe, expect, test } from 'claude-code/testing'

import { classify, composeKey, composeRunsOf, composeServiceCapped, composeServiceLimit } from './lib'
import type { ScratchEnv } from './lib'
import { RULE_SAMPLES, touches } from './touches'
import type { Probe } from './touches'

const KIND = 'docker run without limits'
const held = (command: string, env: ScratchEnv = { home: '/home/t' }) => classify(command, 0, env).filter(h => h.kind === KIND)
const probe: Probe = {
  fs: { stat: async () => ({ kind: 'file', size: 0 }), list: async () => [], read: async () => '', exists: async () => false },
  process: { run: async () => ({ exitCode: 1, stdout: '' }) },
}

// 19 Oct, 4:53 PM: a lane ran this with no --cpus and no timeout; it took 14.6 of 16 cores and the machine thrashed.
const INCIDENT = 'docker run ada-stair-generator-site-tools'

describe('a docker run or compose run with no CPU cap and no timeout in front is held', () => {
  test('the incident command is held', () => {
    expect(held(INCIDENT).map(h => h.kind)).toEqual([KIND])
  })
  const HELD = [
    'docker run --rm -it img make test',
    'docker run -e A=b -v /x:/y --name n img',
    'docker container run img',
    'docker -H tcp://h run img',
    'podman run --rm img',
    'sudo docker run img',
    'cd /srv/app && docker run img',
    'nice -n 10 docker run img',
    'env A=b docker run img',
    'docker compose run --rm app make test',
    'docker compose -f ci.yml -p demo run --rm web npm test',
    'docker-compose run web npm test',
    "bash -c 'docker run img'",
    "ssh host 'docker run img'",
    // a --cpus the container's own program is given is not a cap on the container
    'docker run img stress --cpus 4',
    'docker run img -- --cpus=4',
    // a cap named after the image is not one either
    'docker run -e cpus=2 img',
  ]
  for (const command of HELD) {
    test(`held: ${command}`, () => {
      expect(held(command).map(h => h.kind)).toEqual([KIND])
    })
  }

  for (const command of [
    'docker run --cpus 4 img',
    'docker run --cpus=4 img',
    'docker run --rm -it --cpus 2 img sh',
    'docker run -e A=b --cpus 2 img',
    'docker run --cpu-quota=200000 img',
    'docker run --cpuset-cpus 0-3 img',
    'podman run --cpus 2 img',
    'docker container run --cpus 1 img',
  ]) {
    test(`not held, a CPU cap is given: ${command}`, () => {
      expect(held(command)).toEqual([])
    })
  }

  for (const command of [
    'timeout 600 docker run img',
    'timeout -s KILL 600 docker run img',
    'env A=b timeout 5 docker run img',
    'timeout 600 docker compose run --rm app make test',
    "timeout 600 bash -c 'docker run img'",
    'gtimeout 30 docker run img',
  ]) {
    test(`not held, a timeout is in front: ${command}`, () => {
      expect(held(command)).toEqual([])
    })
  }

  for (const command of [
    '~/.claude/scripts/docker-heavy.sh -- docker run img',
    'docker-heavy.sh --cpus 4 --timeout 600 -- docker run --rm img',
    'docker-heavy.sh -- docker compose -p x run --rm svc',
    'timeout 5 ~/.claude/scripts/docker-heavy.sh -- docker run img',
    'nice ~/.claude/scripts/docker-heavy.sh -- docker run img',
    "bash -c '~/.claude/scripts/docker-heavy.sh -- docker run img'",
  ]) {
    test(`docker-heavy.sh is never held: ${command}`, () => {
      expect(held(command)).toEqual([])
    })
  }

  for (const command of [
    'docker ps',
    'docker ps -a',
    'docker logs -f web',
    'docker inspect web',
    'docker stats --no-stream',
    'docker update --cpus 4 web',
    'docker exec -it web sh',
    'docker compose exec app sh',
    'docker compose up -d',
    'docker compose ps',
    'docker build -t img .',
    'docker images',
    'echo docker run img',
    'git commit -m "docker run img"',
    "grep 'docker run' notes.md",
    'ls docker/run',
  ]) {
    test(`not held, not a run: ${command}`, () => {
      expect(held(command)).toEqual([])
    })
  }

  // review of #75: a flag that does not cap, or a timeout that does not time out, is not a fix
  for (const command of [
    'docker run --cpus 0 img',
    'docker run --cpus $X img',
    'docker run --cpus=abc img',
    'docker run --cpus -1 img',
    'docker run --cpu-quota -1 img',
    'docker run --cpu-quota=0 img',
    'docker run --cpuset-cpus x img',
    'timeout 0 docker run img',
    'timeout abc docker run img',
    'timeout 999999 docker run img',
    'timeout -s KILL docker run img',
    'timeout $T docker run img',
  ]) {
    test(`held, the value is no limit: ${command}`, () => {
      expect(held(command)).toHaveLength(1)
    })
  }
  for (const command of ['timeout --foreground 5 docker run img', 'timeout -s KILL 5 docker run img', 'timeout -k 5 60 docker run img', 'timeout 1.5m docker run img', 'timeout 1d docker run img', 'docker run --cpus 0.5 img', 'docker run --cpuset-cpus 0,2-3 img']) {
    test(`not held, a real limit: ${command}`, () => {
      expect(held(command)).toEqual([])
    })
  }

  test('the same service name in another folder is another key', () => {
    const [a] = composeRunsOf('cd /srv/a && docker compose run --rm web x')
    const [b] = composeRunsOf('cd /srv/b && docker compose run --rm web x')
    expect(a.key).not.toBe(b.key)
    expect(composeKey({ files: [], service: 'web' }, '/srv/a')).not.toBe(composeKey({ files: [], service: 'web' }, '/srv/b'))
    expect(held('cd /srv/b && docker compose run --rm web x', { home: '/home/t', composeLimited: [a.key] })).toHaveLength(1)
  })

  test('a compose service whose file gives it a cpus limit is capped, the same service with no limit is not', () => {
    const command = 'cd /srv/app && docker compose run --rm web npm test'
    const [run] = composeRunsOf(command)
    expect(run).toMatchObject({ service: 'web', cwd: '/srv/app', needsCwd: true, files: [] })
    expect(held(command)).toHaveLength(1)
    expect(held(command, { home: '/home/t', composeLimited: [run.key] })).toEqual([])
    // the key belongs to this service and these files, so another service or file is still held
    expect(held('cd /srv/app && docker compose run --rm db psql', { home: '/home/t', composeLimited: [run.key] })).toHaveLength(1)
    expect(held('cd /srv/app && docker compose -f other.yml run --rm web npm test', { home: '/home/t', composeLimited: [run.key] })).toHaveLength(1)
  })

  test('composeRunsOf skips a run with a timeout in front, reads -f, --project-directory and a cd it cannot follow', () => {
    expect(composeRunsOf('timeout 60 docker compose run --rm web x')).toEqual([])
    expect(composeRunsOf("timeout 60 bash -c 'docker compose run --rm web x'")).toEqual([])
    expect(composeRunsOf('docker compose -f a.yml --file=b.yml -fc.yml --project-directory /p run --rm web x')[0]).toMatchObject({ files: ['a.yml', 'b.yml', 'c.yml'], projectDir: '/p', service: 'web' })
    expect(composeRunsOf('docker-compose run -e A=b -v x:/y web x')[0]).toMatchObject({ service: 'web' })
    expect(composeRunsOf('cd "$X" && docker compose run web x')[0]).toMatchObject({ needsCwd: true, cwd: undefined })
    expect(composeRunsOf('docker compose run web x')[0]).toMatchObject({ needsCwd: true, cwd: undefined })
    expect(composeRunsOf('docker compose -f /abs/c.yml run web x')[0]).toMatchObject({ needsCwd: false })
    expect(composeRunsOf('docker compose -f rel.yml run web x')[0]).toMatchObject({ needsCwd: true })
    expect(composeRunsOf('docker compose exec web x')).toEqual([])
  })
})

describe('composeServiceCapped reads a compose file as text', () => {
  const file = `name: demo
services:
  web:
    image: node
    cpus: 4
    environment:
      A: b
  db:
    image: postgres
  heavy:
    image: x
    deploy:
      resources:
        limits:
          cpus: "2"
          memory: 4g
  commented:
    image: y
    # cpus: 4
  zero:
    image: z
    cpus: 0
  "quoted":
    image: q
    cpu_quota: 100000
volumes:
  web:
    cpus: 9
`
  for (const [service, expected] of [
    ['web', true],
    ['heavy', true],
    ['quoted', true],
    ['db', false],
    ['commented', false],
    ['zero', false],
    ['missing', false],
  ] as const) {
    test(`${service} capped: ${expected}`, () => {
      expect(composeServiceCapped(file, service)).toBe(expected)
    })
  }
  test('a key of the same name under another key is not the limit', () => {
    const odd = 'services:\n  web:\n    image: x\n    environment:\n      cpus: 4\n    labels:\n      cpu_quota: 9\n'
    expect(composeServiceLimit(odd, 'web')).toBe('absent')
    expect(composeServiceLimit('services:\n  web:\n    cpus: 0\n', 'web')).toBe('none')
    expect(composeServiceLimit('services:\n  web:\n    cpus: ${C:-4}\n', 'web')).toBe('none')
    expect(composeServiceLimit('include:\n  - x.yml\nservices:\n  web:\n    cpus: 4\n', 'web')).toBe('none')
    expect(composeServiceLimit('services:\n  web:\n    <<: *base\n    cpus: 4\n', 'web')).toBe('none')
    expect(composeServiceLimit('services:\n  web:\n    extends:\n      service: b\n    cpus: 4\n', 'web')).toBe('none')
    expect(composeServiceLimit('services:\n  web:\n    cpus: !reset 4\n', 'web')).toBe('none')
    expect(composeServiceLimit('services:\n  web:\n    cpus: ${C}\n', 'web')).toBe('none')
    expect(composeServiceLimit('services:\n  web:\n    cpu_count: 4\n    cpu_percent: 50\n', 'web')).toBe('absent')
    expect(composeServiceLimit('services:\n  web:\n    cpus: 4\n  db:\n    cpus: 0\n', 'db')).toBe('none')
    expect(composeServiceLimit('services:\n  web:\n    cpus: 4\n  db:\n    image: y\n', 'db')).toBe('absent')
  })
  test('no services, or text that is not a compose file, caps nothing', () => {
    expect(composeServiceCapped('', 'web')).toBe(false)
    expect(composeServiceCapped('cpus: 4\nweb:\n  cpus: 4', 'web')).toBe(false)
  })
})

describe('what the question and a refusal say about it', () => {
  test('the target and the one-line fix, naming the image', async () => {
    const [h] = held(INCIDENT)
    const text = await touches(probe, h, '/home/t')
    expect(text).toContain('image or service ada-stair-generator-site-tools')
    expect(text).toContain('no --cpus or compose cpus limit and no timeout')
    expect(text).toContain('fix: add --cpus N (and a timeout in front), or run it through ~/.claude/scripts/docker-heavy.sh -- docker run ...')
  })
  test('a compose run names its service, not a flag value', async () => {
    const [h] = held('docker compose run -e A=b -v x:/y --rm web npm test')
    expect(await touches(probe, h, '/home/t')).toContain('image or service web')
  })
  test('the rule is filed in RULE_SAMPLES with docker run and both compose spellings', () => {
    expect(RULE_SAMPLES[KIND].length).toBeGreaterThanOrEqual(3)
  })
})
