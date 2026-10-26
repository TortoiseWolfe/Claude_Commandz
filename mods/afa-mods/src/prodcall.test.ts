import { describe, expect, test } from 'claude-code/testing'

import { BYPASSES, CALL, DEPLOY, DEPLOY_DIR, DEPLOY_HELD, DEPLOY_READS, FORWARDERS, HOME, PLAIN, REXEC, SP, WRITES, prod } from './prodcases'
import { classify } from './lib'
import { type DeployPins, sha256Hex } from './prodread'

// The classifier with the deploy-script pin injected. hooks.test.ts cannot do this (the plugin keeps its own copy of the real pin list), so the
// passes and the refusals that depend on the pin are proved here; hooks.test.ts proves the dialog and the deny per mode.
const DEPLOY_PATH = `${HOME}/repos/Chattanooga-Digital/scripts/portainer-deploy.sh`
const PINS: DeployPins = { 'Chattanooga-Digital': [sha256Hex(DEPLOY) as string] }

// a missing file is read as '' by the hook, the same here
const ENV_PATH = `${HOME}/repos/Chattanooga-Digital/.env`
const KEYS = 'PROJECT_NAME\nDB_PASSWORD\nPORTAINER_PROD_URL\nPORTAINER_PROD_USER\nPORTAINER_PROD_PASSWORD'
const prodHolds = (command: string, script: string | null = REXEC, deploy: string | null = DEPLOY, pins: DeployPins | null = PINS, more: Record<string, string> = {}) =>
  classify(command, 0, { home: HOME, prodScripts: { [`${SP}/rexec.sh`]: script ?? '', [DEPLOY_PATH]: deploy ?? '', [ENV_PATH]: KEYS, ...more }, deployPins: pins ?? undefined }).filter(h => h.kind === 'production deploy').length

describe('read-only production proofs (classifier, deploy pin injected)', () => {
  for (const remote of ['drush status', 'composer audit', 'drush sqlq "SELECT COUNT(*) FROM users"', 'curl -s https://example.org/']) {
    test(`a read passes: ${remote.slice(0, 30)}`, () => {
      expect(prodHolds(prod(remote))).toBe(0)
    })
  }
  for (const command of PLAIN) {
    test(`a plain shape passes: ${command.slice(-50).replace(/\n/g, ' ')}`, () => {
      expect(prodHolds(command)).toBe(0)
    })
  }
  test('no bash in front and a bash shebang: still a read', () => {
    expect(prodHolds(`SP=${SP}; STACK_NAME=x $SP/rexec.sh --prod 'drush status'`)).toBe(0)
  })
  test('/bin/bash as the shebang is a read too', () => {
    expect(prodHolds(prod('drush status'), REXEC.replace('#!/usr/bin/env bash', '#!/bin/bash'))).toBe(0)
  })
  test('the same read with the real pin list is held: the stand-in is not a reviewed copy', () => {
    expect(prodHolds(prod('drush status'), REXEC, DEPLOY, null)).toBe(1)
  })
  for (const [what, command] of WRITES) {
    test(`still held: ${what}`, () => {
      expect(prodHolds(command)).toBeGreaterThan(0)
    })
  }
  for (const [what, command] of BYPASSES) {
    test(`bypass held: ${what}`, () => {
      expect(prodHolds(command)).toBeGreaterThan(0)
    })
  }
  for (const [what, command, script] of FORWARDERS) {
    test(`still held: ${what}`, () => {
      expect(prodHolds(command, script)).toBeGreaterThan(0)
    })
  }
  test('no bash in front and an env -S shebang: held', () => {
    expect(prodHolds(`SP=${SP}; STACK_NAME=x $SP/rexec.sh --prod 'drush status'`, REXEC.replace('#!/usr/bin/env bash', "#!/usr/bin/env -S sh -c 'anything'"))).toBeGreaterThan(0)
  })
  test('hostname NAME in the remote command is held', () => {
    expect(prodHolds(prod('hostname evil'))).toBeGreaterThan(0)
  })
  // portainer-deploy.sh is pinned by SHA-256: a copy that differs, an empty one or a missing one keeps the command held
  for (const [what, deploy] of [
    ['a changed copy', `${DEPLOY}curl -X POST https://example.org/\n`],
    ['a copy with one more blank line', `${DEPLOY}\n`],
    ['an empty copy', ''],
    ['a missing copy', null],
  ] as const) {
    test(`deploy script pin: ${what} is held`, () => {
      expect(prodHolds(prod('drush status'), REXEC, deploy)).toBe(1)
    })
  }
  test('deploy script pin: a lane-made repo folder with its own copy is held', () => {
    const evil = REXEC.replace('Chattanooga-Digital', 'evil')
    expect(prodHolds(prod('drush status'), evil, DEPLOY, PINS, { [`${HOME}/repos/evil/scripts/portainer-deploy.sh`]: `${DEPLOY}echo evil\n` })).toBe(1)
  })
  test('deploy script pin: a folder not on the list is held even with the pinned copy', () => {
    // the pin belongs to a reviewed repo folder; a lane-made folder may hold other files the script reads
    const other = REXEC.replace('Chattanooga-Digital', 'other')
    expect(prodHolds(prod('drush status'), other, DEPLOY, PINS, { [`${HOME}/repos/other/scripts/portainer-deploy.sh`]: DEPLOY })).toBe(1)
  })
  test('deploy script pin: a pin of one repo does not count in another listed repo', () => {
    const eduity = REXEC.replace('Chattanooga-Digital', 'Eduity')
    expect(prodHolds(prod('drush status'), eduity, DEPLOY, PINS, { [`${HOME}/repos/Eduity/scripts/portainer-deploy.sh`]: DEPLOY })).toBe(1)
  })
  // 23 Oct, second senior review: the pinned script exports every key of the repo's dotenv file, so a key that changes how python3 or curl start is a hold
  for (const key of ['PYTHONPATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'BASH_ENV', 'ENV', 'PERL5OPT', 'NODE_OPTIONS', 'RUBYOPT', 'PORTAINER_URL_OVERRIDE', 'PORTAINER_PASSWORD_OVERRIDE', 'PATH', 'IFS']) {
    test(`dotenv key ${key} is held`, () => {
      expect(prodHolds(prod('drush status'), REXEC, DEPLOY, PINS, { [ENV_PATH]: `${KEYS}\n${key}` })).toBe(1)
    })
  }
  test('a dotenv file that holds only the ordinary keys passes, and so does a missing one', () => {
    expect(prodHolds(prod('drush status'), REXEC, DEPLOY, PINS, { [ENV_PATH]: KEYS })).toBe(0)
    expect(prodHolds(prod('drush status'), REXEC, DEPLOY, PINS, { [ENV_PATH]: '' })).toBe(0)
  })
  test('an unreadable or oversize dotenv file (stored as a mark) is held', () => {
    expect(prodHolds(prod('drush status'), REXEC, DEPLOY, PINS, { [ENV_PATH]: '\u0000' })).toBe(1)
  })
  test('a dotenv file the hook has not read yet is asked for and the command is held meanwhile', () => {
    const wanted = new Set<string>()
    const wantedEnv = new Set<string>()
    const prodScripts = { [`${SP}/rexec.sh`]: REXEC, [DEPLOY_PATH]: DEPLOY }
    const holds = classify(prod('drush status'), 0, { home: HOME, prodScripts, deployPins: PINS, prodWanted: wanted, prodWantedEnv: wantedEnv }).filter(h => h.kind === 'production deploy')
    expect(holds.length).toBe(1)
    expect([...wanted]).toEqual([])
    expect([...wantedEnv]).toEqual([ENV_PATH])
  })
  test('an injected key list decides: a key on it passes', () => {
    const prodScripts = { [`${SP}/rexec.sh`]: REXEC, [DEPLOY_PATH]: DEPLOY, [ENV_PATH]: 'EXTRA_KEY' }
    const n = (keys: string[]) => classify(prod('drush status'), 0, { home: HOME, prodScripts, deployPins: PINS, deployEnvKeys: new Set(keys) }).filter(h => h.kind === 'production deploy').length
    expect(n(['EXTRA_KEY'])).toBe(0)
    expect(n(['OTHER'])).toBe(1)
  })
  test('a forwarder inside a hidden folder (a worktree under .worktrees) is still a read; only a hidden script name is held', () => {
    const sp = `${HOME}/repos/.worktrees/Chattanooga-Digital--x/scripts`
    const cmd = `SP=${sp}; STACK_NAME=x bash $SP/rexec.sh --prod 'drush status'`
    expect(prodHolds(cmd, REXEC, DEPLOY, PINS, { [`${sp}/rexec.sh`]: REXEC })).toBe(0)
    expect(prodHolds(cmd.replace('rexec.sh', '.rexec.sh'), REXEC, DEPLOY, PINS, { [`${sp}/.rexec.sh`]: REXEC })).toBe(1)
  })
  test('a forwarder that is a dotfile (the dotenv file as the script word) is held and never asked for', () => {
    const wanted = new Set<string>()
    const holds = classify(`SP=${SP}; STACK_NAME=x bash $SP/.env --prod 'drush status'`, 0, { home: HOME, prodScripts: {}, deployPins: PINS, prodWanted: wanted }).filter(h => h.kind === 'production deploy')
    expect(holds.length).toBe(1)
    expect([...wanted]).toEqual([])
  })
  test('a call with CALL and nothing around it needs its variables set', () => {
    expect(prodHolds(CALL)).toBeGreaterThan(0)
  })
})

// 26 Oct, coop-mail: the deploy script run straight, behind timeout / source / env / nice, is a read for the actions its own dispatcher marks read-only.
describe('the deploy script run directly (classifier, deploy pin injected)', () => {
  const deployHolds = (command: string, over: { cwd?: string | null; deploy?: string; pins?: DeployPins | null; keys?: string } = {}) =>
    classify(command, 0, {
      home: HOME,
      cwd: over.cwd === null ? undefined : (over.cwd ?? DEPLOY_DIR),
      prodScripts: { [DEPLOY_PATH]: over.deploy ?? DEPLOY, [ENV_PATH]: over.keys ?? KEYS },
      deployPins: over.pins === null ? undefined : (over.pins ?? PINS),
    }).filter(h => h.kind === 'production deploy').length
  for (const [what, command] of DEPLOY_READS) {
    test(`a read passes: ${what}`, () => {
      expect(deployHolds(command)).toBe(0)
    })
  }
  for (const [what, command] of DEPLOY_HELD) {
    test(`still held: ${what}`, () => {
      expect(deployHolds(command)).toBeGreaterThan(0)
    })
  }
  test('a relative script word with no folder known (no cd, no session folder) is held', () => {
    expect(deployHolds('timeout 120 scripts/portainer-deploy.sh status --prod', { cwd: null })).toBe(1)
  })
  test('a relative script word in a session folder that is not the repo is held', () => {
    expect(deployHolds('timeout 120 scripts/portainer-deploy.sh status --prod', { cwd: `${HOME}/repos/evil` })).toBe(1)
  })
  test('the real pin list does not know the stand-in: held', () => {
    expect(deployHolds('timeout 120 scripts/portainer-deploy.sh status --prod', { pins: null })).toBe(1)
  })
  test('a changed or empty copy of the script is held', () => {
    expect(deployHolds('timeout 120 scripts/portainer-deploy.sh status --prod', { deploy: `${DEPLOY}echo evil\n` })).toBe(1)
    expect(deployHolds('timeout 120 scripts/portainer-deploy.sh status --prod', { deploy: '' })).toBe(1)
  })
  test('a dotenv file with a key that changes how the script starts is held', () => {
    expect(deployHolds('timeout 120 scripts/portainer-deploy.sh status --prod', { keys: `${KEYS}\nBASH_ENV` })).toBe(1)
  })
  test('an action is a read only because the pinned script says so: a copy whose dispatcher marks nothing holds even status', () => {
    const bare = DEPLOY.replace(/\n\s+status\)[^\n]*/, '\n    # no status here')
    expect(deployHolds('timeout 120 scripts/portainer-deploy.sh status --prod', { deploy: bare, pins: { 'Chattanooga-Digital': [sha256Hex(bare) as string] } })).toBe(1)
    expect(deployHolds('timeout 120 scripts/portainer-deploy.sh drift --prod', { deploy: bare, pins: { 'Chattanooga-Digital': [sha256Hex(bare) as string] } })).toBe(0)
  })
  test('an action whose marker is removed from the script is held (the markers decide, not a fixed list)', () => {
    const unmarked = DEPLOY.replace(/cmd_drift ;;.*\n/, 'cmd_drift ;;\n')
    const pins = { 'Chattanooga-Digital': [sha256Hex(unmarked) as string] }
    expect(deployHolds('timeout 120 scripts/portainer-deploy.sh drift --prod', { deploy: unmarked, pins })).toBe(1)
  })
  test('a deploy script not read yet is asked for and the command is held meanwhile', () => {
    const wanted = new Set<string>()
    const holds = classify('timeout 120 scripts/portainer-deploy.sh status --prod', 0, { home: HOME, cwd: DEPLOY_DIR, prodScripts: {}, deployPins: PINS, prodWanted: wanted }).filter(h => h.kind === 'production deploy')
    expect(holds.length).toBe(1)
    expect([...wanted]).toEqual([DEPLOY_PATH])
  })
})
