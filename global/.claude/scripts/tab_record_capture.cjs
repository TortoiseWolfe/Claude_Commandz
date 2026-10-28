// Read-only film of one browser tab. Started by tab_record.py (never by hand).
// Usage: node tab_record_capture.cjs <config.json>
//
// It attaches to the browser's debugging port, follows ONE tab (the same page object, through every navigation, including the
// submit and the confirmation page) and, about once a second, takes a screenshot of the whole page. A frame is kept only when the
// picture changed. Next to each frame it saves where personal data sits on the page (rectangles), so the blurred copy can be
// made later. It never clicks, scrolls, types, navigates, reloads or closes anything, and never closes the browser.
//
// Ends when: the stop file appears; a job flag file newer than the start appears and 25 s pass (the confirmation page);
// the tab leaves the form for a confirmation address and 25 s pass; the tab closes; or the cap in minutes runs out.
// Files written in the recording folder: frames/f<epoch ms>.png, rects.ndjson (one line per kept frame), capture.log.
const fs = require('fs')
const path = require('path')
const crypto = require('crypto')

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const { chromium } = require(cfg.playwright)
const log = (...a) => fs.appendFileSync(path.join(cfg.dir, 'capture.log'), new Date().toISOString() + ' ' + a.join(' ') + '\n')

// Words and shapes that are always blurred wherever they show as page text or inside a field.
const TEXT_RES = [
  '[\\w.+-]+@[\\w-]+\\.[\\w.-]+', // email address
  '\\(?\\d{3}\\)?[-. ]\\d{3}[-. ]\\d{4}', // phone number
  '\\+\\d[\\d ()-]{8,}\\d', // international phone number
  'linkedin\\.com/in/[\\w%-]+', // LinkedIn address
  '\\$\\s?\\d[\\d,]*(?:\\.\\d+)?\\s?[kK]?', // money (salary)
  'Mercor', 'Harmonic\\s*Egg', 'Tech\\s*Joy(?:\\s+Software)?', ...(cfg.extraPatterns || []),
]
// A field whose label, name or placeholder says one of these has its whole answer blurred.
const FIELD_RE = '(e-?mail|phone|mobile|linkedin|address|street|zip|postal|salary|compensation|pay\\b|ssn|social security|birth|gender|race|ethnic|veteran|disabilit|pronoun|sexual|hispanic|latino|resume|cv\\b|cover letter|github|portfolio|website|twitter)'

// Runs inside the page (and inside each frame). Read only: it measures, it changes nothing.
const COLLECT = ({ texts, fieldRe }) => {
  const re = new RegExp(texts.join('|'), 'gi')
  const fre = new RegExp(fieldRe, 'i')
  const sx = window.scrollX, sy = window.scrollY
  const out = []
  const add = (r, why) => { if (r.width > 1 && r.height > 1) out.push({ x: r.left + sx, y: r.top + sy, w: r.width, h: r.height, why }) }
  // 1. page text
  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT)
  let n
  while ((n = walker.nextNode())) {
    const p = n.parentElement
    if (!p || /^(SCRIPT|STYLE|NOSCRIPT|TEXTAREA)$/.test(p.tagName)) continue
    const t = n.nodeValue
    if (!t || t.length < 3) continue
    re.lastIndex = 0
    let m
    while ((m = re.exec(t))) {
      const rg = document.createRange(); rg.setStart(n, m.index); rg.setEnd(n, m.index + m[0].length)
      for (const r of rg.getClientRects()) add(r, 'text')
      if (m[0].length === 0) re.lastIndex++
    }
  }
  // 2. fields: values, sensitive labels, every free-text box, upload names
  const labelOf = (el) => {
    const bits = [el.name, el.id, el.placeholder, el.getAttribute('aria-label'), el.getAttribute('autocomplete'), el.type]
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) bits.push(l.innerText) }
    const wrap = el.closest('label, fieldset, [class*="field-entry"], .field-wrapper, .select__container, [role=group]')
    if (wrap) { const l = wrap.querySelector('label, legend, [class*="question-title"]'); if (l) bits.push(l.innerText) }
    return bits.filter(Boolean).join(' ')
  }
  for (const el of document.querySelectorAll('input, textarea, select')) {
    if (el.type === 'hidden' || el.type === 'checkbox' || el.type === 'radio') continue
    const lab = labelOf(el)
    const v = el.type === 'file' ? (el.files && el.files[0] ? el.files[0].name : '') : (el.value || '')
    const sensitive = el.tagName === 'TEXTAREA' || /^(email|tel|url|password)$/.test(el.type) || fre.test(lab) || (v && (re.lastIndex = 0, re.test(v)))
    if (sensitive && (v || el.tagName === 'TEXTAREA')) add(el.getBoundingClientRect(), 'field')
  }
  // 3. answers that are not typed text (radio groups, custom selects) under a sensitive question: blur the whole question block
  for (const w of document.querySelectorAll('fieldset, [class*="field-entry"], .field-wrapper, .select__container, [role=group], [role=radiogroup]')) {
    const l = w.querySelector('legend, label, [class*="question-title"]')
    if (l && fre.test(l.innerText) && w.getBoundingClientRect().height < 400) add(w.getBoundingClientRect(), 'question')
  }
  return out
}

const collectAll = async (page) => {
  const all = []
  const arg = { texts: TEXT_RES, fieldRe: FIELD_RE }
  for (const fr of page.frames()) {
    if (fr !== page.mainFrame() && /recaptcha|hcaptcha|doubleclick|google\.com\/(recaptcha|accounts)/.test(fr.url())) continue
    try {
      const rects = await fr.evaluate(COLLECT, arg)
      if (fr === page.mainFrame()) { all.push(...rects); continue }
      const box = await (await fr.frameElement()).boundingBox() // viewport coordinates
      const sc = await page.evaluate(() => [window.scrollX, window.scrollY])
      if (box) for (const r of rects) all.push({ x: r.x - 0 + box.x + sc[0], y: r.y + box.y + sc[1], w: r.w, h: r.h, why: 'frame-' + r.why })
    } catch (e) { /* a frame that went away mid-read: the next tick reads again */ }
  }
  return all
}

// Playwright's page.screenshot waits for the tab to paint and hangs on a tab that is not in front. The browser's own
// Page.captureScreenshot reads the page surface without bringing the tab forward, so it films a background tab too.
const sessions = new WeakMap()
const shoot = async (page) => {
  let s = sessions.get(page)
  if (!s) { s = await page.context().newCDPSession(page); sessions.set(page, s) }
  const r = await Promise.race([
    s.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: true }),
    new Promise((_, rej) => setTimeout(() => rej(new Error('screenshot timeout')), 10000)),
  ])
  return Buffer.from(r.data, 'base64')
}
const CONFIRM = /confirmation|thank-?you|thanks|submitted|success|applied/i
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const flagFresh = () => {
  for (const d of cfg.flagDirs || []) {
    let names = []
    try { names = fs.readdirSync(d) } catch (e) { continue }
    for (const f of names) {
      if (!cfg.flagPrefixes.some((pre) => f.startsWith(pre + '-') || f.startsWith(pre + '.')) || !/submitted.*\.flag$/.test(f)) continue
      try { if (fs.statSync(path.join(d, f)).mtimeMs >= cfg.startedAtMs - 2000) return path.join(d, f) } catch (e) { /* gone */ }
    }
  }
  return ''
}

;(async () => {
  fs.mkdirSync(path.join(cfg.dir, 'frames'), { recursive: true, mode: 0o700 })
  const capAt = Date.now() + (cfg.capMinutes || 90) * 60000
  let browser
  try { browser = await chromium.connectOverCDP('http://127.0.0.1:' + cfg.port) } catch (e) { log('cannot attach:', String(e).slice(0, 120)); process.exit(2) }
  const pages = () => browser.contexts().flatMap((c) => c.pages())
  // Pick the tab: wait up to 3 minutes for exactly one to match.
  let page = null
  for (let w = 0; !page && Date.now() < capAt && w < 150; w++) {
    if (fs.existsSync(path.join(cfg.dir, 'stop'))) { log('stopped before the tab appeared'); fs.writeFileSync(path.join(cfg.dir, 'ended.json'), JSON.stringify({ why: 'stop file', frames: 0 })); process.exit(0) }
    const m = pages().filter((p) => p.url().includes(cfg.tab))
    if (m.length >= 1) page = m[0]
    else await sleep(1200)
  }
  if (!page) { log('no tab matched', cfg.tab); process.exit(3) }
  log('following', page.url().split('?')[0], 'port', cfg.port)
  const startUrl = page.url()
  if (cfg.auto && CONFIRM.test(startUrl)) { log('tab is already on a confirmation address, nothing to film'); fs.writeFileSync(path.join(cfg.dir, 'ended.json'), JSON.stringify({ why: 'already confirmed', frames: 0 })); process.exit(0) }
  let last = '', n = 0, endAt = 0, why = ''
  const rectsFile = path.join(cfg.dir, 'rects.ndjson')
  while (Date.now() < capAt) {
    if (fs.existsSync(path.join(cfg.dir, 'stop'))) { why = 'stop file'; break }
    if (page.isClosed()) { why = 'tab closed'; break }
    if (!endAt) {
      const fl = flagFresh()
      if (fl) { endAt = Date.now() + 25000; why = 'flag ' + path.basename(fl); log('flag seen', path.basename(fl)) }
      else if (page.url() !== startUrl && CONFIRM.test(page.url()) && !CONFIRM.test(startUrl)) { endAt = Date.now() + 25000; why = 'confirmation page'; log('confirmation address', page.url().split('?')[0]) }
    }
    if (endAt && Date.now() > endAt) break
    try {
      const before = await collectAll(page)
      const buf = await shoot(page)
      const h = crypto.createHash('md5').update(buf).digest('hex')
      if (h !== last) {
        last = h
        const after = await collectAll(page) // page may have changed between the first read and the picture: keep both
        const ms = Date.now()
        const f = path.join(cfg.dir, 'frames', 'f' + ms + '.png')
        fs.writeFileSync(f, buf, { mode: 0o600 })
        fs.appendFileSync(rectsFile, JSON.stringify({ ms, url: page.url().split('?')[0], rects: before.concat(after) }) + '\n', { mode: 0o600 })
        n++
      }
    } catch (e) { log('tick failed', String(e).slice(0, 100)) }
    await sleep(cfg.intervalMs || 1000)
  }
  if (!why) why = 'cap'
  log('ended:', why, 'frames', n)
  fs.writeFileSync(path.join(cfg.dir, 'ended.json'), JSON.stringify({ why, frames: n, endedMs: Date.now() }))
  process.exit(0) // detach from the browser; it keeps running
})()
