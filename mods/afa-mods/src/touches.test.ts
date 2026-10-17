import { describe, expect, test } from 'claude-code/testing'

import { HELD_KINDS, classify, explain } from './lib'
import type { Hold } from './lib'
import { RULE_SAMPLES, cantTell, touches } from './touches'
import type { Probe } from './touches'

const HOME = '/home/t'

// A stand-in for the engine's read-only calls. `files` maps a path to [kind, size, entries]; `programs` maps
// "docker volume ls ..." (the argv joined by spaces, up to the first flag the test cares about) to stdout.
const probe = (
  files: Record<string, [string, number, number]> = {},
  programs: Record<string, string> = {},
  docker = true,
): Probe => ({
  fs: {
    stat: async path => {
      if (!(path in files)) throw new Error('not found')
      return { kind: files[path][0], size: files[path][1] }
    },
    list: async path => Array.from({ length: files[path]?.[2] ?? 0 }, (_, i) => ({ name: `e${i}` })),
    read: async path => (path === 'package.json' ? '{"scripts":{"deploy":"vercel --prod"}}' : ''),
    exists: async path => path === 'package.json',
  },
  process: {
    run: async argv => {
      const key = argv.join(' ')
      const hit = Object.keys(programs).find(k => key.startsWith(k))
      const dockerCall = argv[0] === 'docker'
      if (hit === undefined || (dockerCall && !docker)) return { exitCode: dockerCall && !docker ? 1 : 0, stdout: '' }
      return { exitCode: 0, stdout: programs[hit] }
    },
  },
})

const PROGRAMS: Record<string, string> = {
  'docker system df': 'Images 3 1.2GB\nContainers 2 10MB',
  'docker ps -a': 'old-db (postgres:16)\n',
  'docker image ls': 'repo:tag (200MB)\n',
  'docker volume ls --filter': 'dangling-vol\n',
  'docker volume ls --format': 'pgdata\nuploads\n',
  'docker compose': 'pgdata\nredis\n',
  'git branch --show-current': 'feat/x\n',
  'git config --get branch.feat/x.remote': 'origin\n',
  'git remote get-url origin': 'https://ghp_SECRET@github.com/Org/repo.git\n',
}

const hold = (command: string): Hold => {
  const found = classify(command)
  if (found.length === 0) throw new Error(`no rule holds: ${command}`)
  return found[0]
}

// a fragment of code: a regex literal, a quoted array of commands, an arrow function, a template placeholder
const CODE_FRAGMENT = /=>|\/\^|\\s\+|\$\{|\.test\(|\[\s*['"`]|\bexpect\(|\bconst\b|\bfunction\b/

describe('what a held command would touch, for every rule', () => {
  for (const [kind, samples] of Object.entries(RULE_SAMPLES)) {
    for (const command of samples) {
      test(`${kind}: ${command} says what it does and names targets or says it cannot tell`, async () => {
        const h = classify(command).find(x => x.kind === kind)
        expect(h).toBeDefined()
        const files = { '/home/t/build': ['directory', 4096, 3] as [string, number, number] }
        const text = await touches(probe(files, PROGRAMS), h as Hold, HOME)
        const [head, ...targets] = text.split('\n')
        // (a) the command in plain words
        expect(explain(h as Hold).what.length).toBeGreaterThan(10)
        expect(head.startsWith(kind)).toBe(true)
        // (b) at least one target line, concrete or the plain "can't tell" with a reason
        expect(targets.length).toBeGreaterThan(0)
        for (const line of targets) {
          expect(line.trim().length).toBeGreaterThan(0)
          expect(CODE_FRAGMENT.test(line)).toBe(false)
        }
        const said = targets.join('\n')
        const concrete = /\((dir|file|not found|volume)|\/|branch|container|volume|image|service|host|disk/.test(said)
        const cant = said.includes("can't tell what this touches: ")
        expect(concrete || cant).toBe(true)
        if (cant) expect(said.split("can't tell what this touches: ")[1].trim().length).toBeGreaterThan(5)
      })
    }
  }

  test('a rule missing from RULE_SAMPLES fails here: every kind classify or explain names is described', () => {
    for (const kind of HELD_KINDS) {
      expect(Object.keys(RULE_SAMPLES)).toContain(kind)
      // explain has plain words for it (its fallback answers with the bare kind)
      expect(explain({ kind, hard: false, segment: '' }).what).not.toBe(kind)
    }
    expect(Object.keys(RULE_SAMPLES).sort()).toEqual([...HELD_KINDS].sort())
    // and the other way: every sample command is held by the rule it is filed under
    for (const [kind, samples] of Object.entries(RULE_SAMPLES)) {
      for (const s of samples) expect(classify(s).map(x => x.kind)).toContain(kind)
    }
  })

  test('an unknown rule still says it cannot tell and why', async () => {
    const text = await touches(probe(), { kind: 'brand new rule', hard: false, segment: 'x y' }, HOME)
    expect(text).toContain("can't tell what this touches: ")
  })
})

describe('rm -rf names each path the way the prompt always did', () => {
  test('a directory shows its size and what is inside, a missing path says not found', async () => {
    const files = { '/home/t/build': ['directory', 4096, 3] as [string, number, number] }
    const text = await touches(probe(files), hold('rm -rf /home/t/build /home/t/missing'), HOME)
    expect(text).toContain('/home/t/build (dir, 4096 bytes, 3 entries at the top level, all removed)')
    expect(text).toContain('/home/t/missing (not found)')
  })
  test('a file shows its size', async () => {
    const files = { '/home/t/a.log': ['file', 12, 0] as [string, number, number] }
    expect(await touches(probe(files), hold('rm -rf /home/t/a.log'), HOME)).toContain('/home/t/a.log (file, 12 bytes)')
  })
  test('~ is read as the home folder', async () => {
    const files = { '/home/t/x': ['directory', 4096, 0] as [string, number, number] }
    expect(await touches(probe(files), hold('rm -rf ~/x'), HOME)).toContain('/home/t/x (dir, 4096 bytes')
  })
  test('a variable or a pattern says it cannot tell, and why', async () => {
    const v = await touches(probe(), hold('rm -rf "$BUILD_DIR"/old'), HOME)
    expect(v).toContain(cantTell('the path goes through $BUILD_DIR, which is only read when the command runs'))
    const g = await touches(probe(), hold('rm -rf dist/*'), HOME)
    expect(g).toContain("can't tell what this touches: the pattern only expands when the command runs")
  })
  test('lists at most 8 targets and says how many were left out', async () => {
    const many = Array.from({ length: 11 }, (_, i) => `/home/t/d${i}`).join(' ')
    const text = await touches(probe(), hold(`rm -rf ${many}`), HOME)
    expect(text).toContain('3 more targets not listed')
  })
})

describe('other rules name their real targets', () => {
  test('docker volume rm names each volume and whether it exists', async () => {
    const text = await touches(probe({}, PROGRAMS), hold('docker volume rm pgdata cache'), HOME)
    expect(text).toContain('pgdata (volume exists, its data goes)')
    expect(text).toContain('cache (volume not found)')
  })
  test('docker prune names the containers, images or volumes and the disk use; with docker down it says so', async () => {
    const sys = await touches(probe({}, PROGRAMS), hold('docker system prune -af'), HOME)
    expect(sys).toContain('old-db (postgres:16)')
    expect(sys).toContain('Images 3 1.2GB')
    const vol = await touches(probe({}, PROGRAMS), hold('docker volume prune -f'), HOME)
    expect(vol).toContain('dangling-vol')
    const down = await touches(probe({}, PROGRAMS, false), hold('docker volume prune -f'), HOME)
    expect(down).toContain("can't tell what this touches: docker did not answer")
  })
  test('compose down -v lists the volumes the stack declares', async () => {
    const text = await touches(probe({}, PROGRAMS), hold('docker compose down -v'), HOME)
    expect(text).toContain('pgdata')
    expect(text).toContain('redis')
  })
  test('git push --force names branch and remote host, and never a token in the address', async () => {
    const text = await touches(probe({}, PROGRAMS), hold('git push --force origin main'), HOME)
    expect(text).toContain('branch main on remote origin (github.com/Org/repo.git)')
    expect(text).not.toContain('ghp_SECRET')
  })
  test('a force push with no branch named uses the current branch and its remote', async () => {
    const text = await touches(probe({}, PROGRAMS), hold('git push -f'), HOME)
    expect(text).toContain('branch feat/x on remote origin')
  })
  test('a deploy tool names the service; an npm script shows what it runs; an unknown tool says it cannot tell', async () => {
    const v = await touches(probe(), hold('vercel --prod'), HOME)
    expect(v).toContain('service: Vercel')
    const n = await touches(probe(), hold('npm run deploy --production'), HOME)
    expect(n).toContain('script "deploy" in this folder\'s package.json runs: vercel --prod')
    const u = await touches(probe(), hold('mystery-tool --prod'), HOME)
    expect(u).toContain("can't tell what this touches: ")
  })
})

describe('the classifier judges the command itself, not text it only carries', () => {
  for (const command of [
    'claude plugin test mods/afa-mods',
    'cd ~/repos/Claude_Commandz/mods/afa-mods && claude plugin test . 2>&1 | tail -20',
    'bun test src/hooks.test.ts',
    'grep -n -- "--prod" mods/afa-mods/src/lib.ts',
    "cat > notes.txt <<'EOF'\nvercel --prod\nrm -rf /\nEOF",
    "tee out.txt <<EOF\nnetlify deploy --prod\nEOF",
    'tmux send-keys -t lane "I held vercel --prod and git push --force; try again" Enter',
    'echo "do not run vercel --prod yet"',
    "printf '%s\\n' 'git push --force origin main'",
    'git commit -m "document why we refuse rm -rf and vercel --prod"',
    'gh pr create --title "x" --body "never run docker volume prune"',
  ]) {
    test(`holds nothing for: ${command.split('\n')[0]}`, () => {
      expect(classify(command)).toEqual([])
    })
  }
  for (const [command, kind] of [
    ['bash <<EOF\nvercel --prod\nEOF', 'production deploy'],
    ['ssh host "vercel --prod"', 'production deploy'],
    ['bash -c "vercel --prod"', 'production deploy'],
    ['cd x && vercel --prod', 'production deploy'],
    ['git push --force origin main', 'git push --force'],
    ['echo hi; docker volume prune -f', 'docker volume prune'],
  ] as const) {
    test(`still holds: ${command.split('\n')[0]}`, () => {
      expect(classify(command).map(h => h.kind)).toContain(kind)
    })
  }
})
