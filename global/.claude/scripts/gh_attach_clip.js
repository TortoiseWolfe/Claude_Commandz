// Put an approved, blurred clip on GitHub through GitHub's own attach control, and print the link it gives back.
// Usage: node gh_attach_clip.js <clip.mp4> [--repo OWNER/REPO] [--template tester-report.md] [--limit-mb 10] [--expect-user LOGIN] [--port N]
//
// The hub runs this, never a lane. It attaches to the fleet browser over its debugging port, opens its OWN new tab on the repo's
// new-issue page, hands the file to the comment box's attach control (setInputFiles, what the "attach files" button does), waits
// until the box holds the github.com/user-attachments link, prints that link, and closes only its own tab. It never presses
// "Submit new issue", never types, never touches another tab, never brings a tab to the front, never closes the browser.
// The issue draft is not saved anywhere: the hub pastes the printed link into the real tester-report issue ("Clip:" line).
//
// Refuses (exit 2) unless `tab_record.py ok` passes: an .mp4 made by `tab_record.py cut`, blurred, approved after `check`, and
// within the size limit. GitHub's limit for a video is 10 MB when the repo owner is on a free plan, 100 MB on a paid plan; the
// plan of PM-Beer is not readable, so the default is 10 MB (raise it with --limit-mb 100 only when the plan is known).
// Exit codes: 0 link printed | 2 file refused | 3 not signed in to GitHub in that browser | 4 no attach control found / no link came back
//             5 no browser on a debugging port
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')
const http = require('http')

const args = process.argv.slice(2)
const opt = (name, dflt) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : dflt }
const clip = args.find((a, i) => !a.startsWith('--') && !(i > 0 && args[i - 1].startsWith('--')))
const repo = opt('--repo', 'PM-Beer/scout-connector')
const template = opt('--template', 'tester-report.md')
const limitMb = opt('--limit-mb', '10')
const expectUser = opt('--expect-user', '')
const here = __dirname

const fail = (code, msg) => { console.error('gh_attach_clip: ' + msg); process.exit(code) }
if (!clip) fail(2, 'usage: node gh_attach_clip.js <clip.mp4> [--repo OWNER/REPO] [--limit-mb N]')

const ok = spawnSync('python3', [path.join(here, 'tab_record.py'), 'ok', clip, '--limit-mb', limitMb], { encoding: 'utf8' })
if (ok.status !== 0) fail(2, (ok.stdout || ok.stderr || 'refused').trim())

const portOk = (port) => new Promise((res) => {
  const r = http.get({ host: '127.0.0.1', port, path: '/json/version', timeout: 2000 }, (m) => { m.resume(); res(m.statusCode === 200) })
  r.on('error', () => res(false)); r.on('timeout', () => { r.destroy(); res(false) })
})
async function findPort() {
  const c = [opt('--port', ''), process.env.TAB_RECORD_PORT]
  try {
    const pid = fs.readlinkSync(path.join(os.homedir(), '.config/afa/browser/SingletonLock')).split('-').pop()
    for (const a of fs.readFileSync('/proc/' + pid + '/cmdline', 'utf8').split('\0')) if (a.startsWith('--remote-debugging-port=')) c.push(a.split('=')[1])
  } catch (e) { /* no lock: fall through to the usual ports */ }
  c.push('9334', '9333')
  for (const p of c) if (p && await portOk(p)) return p
  return null
}
const pwPath = [path.join(os.homedir(), '.cache/demo-video/node_modules/playwright'), path.join(os.homedir(), 'repos/consemble/node_modules/playwright')].find((p) => fs.existsSync(p)) || 'playwright'

;(async () => {
  const port = await findPort()
  if (!port) fail(5, 'no browser on a debugging port')
  const { chromium } = require(pwPath)
  const browser = await chromium.connectOverCDP('http://127.0.0.1:' + port)
  const ctx = browser.contexts()[0]
  const page = await ctx.newPage() // our own tab; the only one this script ever closes
  let code = 0
  try {
    await page.goto('https://github.com/' + repo + '/issues/new' + (template ? '?template=' + encodeURIComponent(template) : ''), { waitUntil: 'domcontentloaded', timeout: 45000 })
    const login = await page.evaluate(() => (document.querySelector('meta[name="user-login"]') || {}).content || '')
    if (!login || /\/login/.test(page.url())) { code = 3; throw new Error('not signed in to GitHub in this browser (' + page.url() + ')') }
    if (expectUser && login.toLowerCase() !== expectUser.toLowerCase()) { code = 3; throw new Error('signed in as ' + login + ', expected ' + expectUser) }
    console.error('gh_attach_clip: signed in as ' + login + ', tab on ' + page.url())
    await page.waitForSelector('textarea', { timeout: 30000 }).catch(() => {}) // the issue form draws after the page loads
    await page.waitForTimeout(2000)
    const area = page.locator('textarea[name="issue[body]"], textarea#issue_body, textarea').first()
    const before = await area.inputValue().catch(() => '')
    const file = path.resolve(clip)
    const input = page.locator('input[type="file"]').first()
    const button = page.getByRole('button', { name: /add files|attach/i }).first()
    if (await input.count()) await input.setInputFiles(file) // older form: a file input on the page
    else if (await button.count()) { // new form: the "Paste, drop, or click to add files" button opens the file chooser
      const [chooser] = await Promise.all([page.waitForEvent('filechooser', { timeout: 15000 }), button.click()])
      await chooser.setFiles(file)
    } else { code = 4; throw new Error('no attach control on the page ' + page.url()) }
    const re = /https:\/\/github\.com\/user-attachments\/(?:assets|files)\/[\w./-]+/
    let link = ''
    for (let i = 0; i < 90 && !link; i++) { // up to ~3 minutes for the upload
      await page.waitForTimeout(2000)
      const v = await area.inputValue().catch(() => '')
      const m = v.slice(before.length > 0 && v.startsWith(before) ? before.length : 0).match(re)
      if (m) link = m[0]
    }
    if (!link) { code = 4; throw new Error('the upload gave no user-attachments link in 3 minutes') }
    console.log(link)
  } catch (e) {
    if (!code) code = 4
    console.error('gh_attach_clip: ' + e.message)
  } finally {
    await page.close({ runBeforeUnload: false }).catch(() => {})
  }
  process.exit(code) // detach from the browser; it keeps running
})()
