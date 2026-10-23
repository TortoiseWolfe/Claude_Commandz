import { describe, expect, test } from 'claude-code/testing'

import { BYPASSES, CALL, DEPLOY, FORWARDERS, HOME, PLAIN, REXEC, SP, WRITES, prod } from './prodcases'
import { classify } from './lib'
import { sha256Hex } from './prodread'

// The classifier with the deploy-script pin injected. hooks.test.ts cannot do this (the plugin keeps its own copy of the real pin list), so the
// passes and the refusals that depend on the pin are proved here; hooks.test.ts proves the dialog and the deny per mode.
const DEPLOY_PATH = `${HOME}/repos/Chattanooga-Digital/scripts/portainer-deploy.sh`
const PINS = new Set([sha256Hex(DEPLOY) as string])

// a missing file is read as '' by the hook, the same here
const prodHolds = (command: string, script: string | null = REXEC, deploy: string | null = DEPLOY, pins: Set<string> | null = PINS, more: Record<string, string> = {}) =>
  classify(command, 0, { home: HOME, prodScripts: { [`${SP}/rexec.sh`]: script ?? '', [DEPLOY_PATH]: deploy ?? '', ...more }, deployPins: pins ?? undefined }).filter(h => h.kind === 'production deploy').length

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
  test('deploy script pin: a lane-made folder is read as it is when its copy is the pinned one', () => {
    // pinned content is what is reviewed; where it sits does not matter, the command in it is the same
    const other = REXEC.replace('Chattanooga-Digital', 'other')
    expect(prodHolds(prod('drush status'), other, DEPLOY, PINS, { [`${HOME}/repos/other/scripts/portainer-deploy.sh`]: DEPLOY })).toBe(0)
  })
  test('a call with CALL and nothing around it needs its variables set', () => {
    expect(prodHolds(CALL)).toBeGreaterThan(0)
  })
})
