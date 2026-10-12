import { describe, expect, test } from 'claude-code/testing'

import { bandText, classify, countInbox, hasExitLine, recentLanes, weather } from './lib'

const INBOX = [
  '- [x] 2026-10-03 done thing',
  '- [ ] 2026-10-08 open one',
  '- [ ] 2026-10-09 Client mail: a reply is waiting',
  '- [ ] 2026-10-10 Client alert: a site is down',
  '  - [ ] indented detail is not an item',
  '- [ ] 2026-10-11 open two',
].join('\n')

const INDEX = [
  '2026-10-01\told\t/x\tsonnet\tsub\t-\t/nope/report.md\ts\t-',
  '2026-10-11\trecent\t/x\tsonnet\tsub\t-\t/nope/report.md\ts\t-',
  '2026-10-12\ttoday\t/x\tsonnet\tsub\t-\t-\ts\t-',
].join('\n')

describe('hub band', () => {
  test('counts open inbox lines and client lines apart', async () => {
    expect(countInbox(INBOX)).toEqual({ inbox: 2, client: 2 })
  })
  test('keeps lanes from the last 3 days', async () => {
    const lanes = recentLanes(INDEX, Date.parse('2026-10-12T12:00:00Z'))
    expect(lanes.map(l => l.name)).toEqual(['recent', 'today'])
  })
  test('out.log exit line', async () => {
    expect(hasExitLine('working\nexit 0\n')).toBe(true)
    expect(hasExitLine('working\n')).toBe(false)
  })
  test('band text', async () => {
    expect(bandText(false, { lanes: 2, inbox: 2, client: 2 })).toBe(
      '/prep not run | 2 lanes open | Muse inbox: 2 open | Client mail: 2 new',
    )
    expect(bandText(true, { lanes: 0, inbox: 0, client: 0 })).toBe('hub quiet')
  })
})

describe('token weather', () => {
  test('30 is clear, 50 showers', async () => {
    expect(weather(30, true).text).toBe('Weather: clear 30%')
    expect(weather(50, true).text).toBe('Weather: showers 50%')
  })
  test('70 is a storm: hand off in the hub only', async () => {
    expect(weather(70, true).text).toBe('Weather: storm 70% - hand off and end')
    expect(weather(70, false).text).toBe('Weather: storm 70%')
  })
  test('85 hands off everywhere', async () => {
    expect(weather(85, false).text).toBe('Weather: storm 85% - hand off and end')
  })
})

describe('blast radius classifier', () => {
  const kinds = (c: string) => classify(c).map(h => h.kind)
  test('holds the listed commands', async () => {
    expect(kinds('rm -rf /tmp/afa-mods-test')).toEqual(['rm -rf'])
    expect(kinds('rm -fr x && ls')).toEqual(['rm -rf'])
    expect(kinds('docker system prune -af')).toEqual(['docker system prune'])
    expect(kinds('docker volume prune')).toEqual(['docker volume prune'])
    expect(kinds('docker image prune')).toEqual(['docker image prune'])
    expect(kinds('docker compose -p x down -v')).toEqual(['docker compose down -v'])
    expect(kinds('docker volume rm a b')).toEqual(['docker volume rm'])
    expect(kinds('git push --force origin main')).toEqual(['git push --force'])
    expect(kinds('git push -f')).toEqual(['git push --force'])
    expect(kinds('vercel deploy --prod')).toEqual(['production deploy'])
    expect(kinds('app redeploy production')).toEqual(['production deploy'])
    expect(kinds('echo hi & git push origin +main')).toEqual(['git push --force'])
    expect(kinds('bash -c "cd x && rm -rf y"')).toEqual(['rm -rf'])
    expect(kinds('\\rm -rf y')).toEqual(['rm -rf'])
  })
  test('lets ordinary commands through', async () => {
    expect(kinds('rm -r build')).toEqual([])
    expect(kinds('rm file.txt')).toEqual([])
    expect(kinds('docker compose down')).toEqual([])
    expect(kinds('git push origin main')).toEqual([])
    expect(kinds('ls -la')).toEqual([])
  })
})
