// usage: demo_video_record.cjs /out/<name>.run.json
// Runs inside the Playwright container that demo_video.py starts. Reads the normalized spec plus the length of each narration
// line, drives the app in a visible-cursor recording, and writes /out/<name>.webm and /out/<name>-timeline.json
// ({demoOffset, total, steps:[{start,end,dur,overran}], mRed, mBlue, checks:[{step,kind,msg,ok}], stalls:[{at,sec}]}).
// Nothing here signs in: a sign-in the app needs is the spec's own setup hook.
'use strict'
const fs = require('node:fs')
const path = require('node:path')

function loadPlaywright() {
  for (const m of ['playwright', '@playwright/test']) {
    try {
      return require(m)
    } catch (e) {
      if (e.code !== 'MODULE_NOT_FOUND') throw e
    }
  }
  throw new Error('playwright is not installed for this container: run demo_video.py doctor --install')
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ---- targets: {css|text|role(+name)|label|placeholder|testId, exact, within, nth}
const pat = (v) => (typeof v === 'string' ? v : new RegExp(v.regex, v.flags || ''))
function locate(page, t, all = false) {
  const root = t.within ? page.locator(t.within) : page
  let loc
  if (t.css) loc = root.locator(t.css)
  else if (t.text !== undefined) loc = root.getByText(pat(t.text), { exact: !!t.exact })
  else if (t.role) loc = root.getByRole(t.role, t.name !== undefined ? { name: pat(t.name), exact: !!t.exact } : {})
  else if (t.label !== undefined) loc = root.getByLabel(pat(t.label), { exact: !!t.exact })
  else if (t.placeholder !== undefined) loc = root.getByPlaceholder(pat(t.placeholder), { exact: !!t.exact })
  else if (t.testId) loc = root.getByTestId(t.testId)
  else throw new Error('target has no locator: ' + JSON.stringify(t))
  if (all) return loc
  return t.nth !== undefined ? loc.nth(t.nth) : loc.first()
}
const describe = (t) => JSON.stringify(t)

const CURSOR_JS = () => {
  const mk = () => {
    if (document.getElementById('__cur')) return
    const d = document.createElement('div')
    d.id = '__cur'
    d.style.cssText =
      'position:fixed;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;background:rgba(255,80,40,.85);border:3px solid #fff;box-shadow:0 0 8px rgba(0,0,0,.5);z-index:2147483647;pointer-events:none;'
    document.documentElement.appendChild(d)
    window.addEventListener(
      'mousemove',
      (e) => {
        d.style.left = e.clientX + 'px'
        d.style.top = e.clientY + 'px'
      },
      true
    )
    window.addEventListener(
      'mousedown',
      (e) => {
        const r = document.createElement('div')
        r.style.cssText = `position:fixed;left:${e.clientX}px;top:${e.clientY}px;width:10px;height:10px;margin:-5px 0 0 -5px;border-radius:50%;border:4px solid rgba(255,80,40,.9);z-index:2147483647;pointer-events:none;transition:all .5s ease-out;opacity:1;`
        document.documentElement.appendChild(r)
        requestAnimationFrame(() => {
          r.style.width = '70px'
          r.style.height = '70px'
          r.style.margin = '-35px 0 0 -35px'
          r.style.opacity = '0'
        })
        setTimeout(() => r.remove(), 600)
      },
      true
    )
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mk)
  else mk()
}

async function main() {
  const specFile = process.argv[2]
  if (!specFile) throw new Error('usage: demo_video_record.cjs <name>.run.json')
  const spec = JSON.parse(fs.readFileSync(specFile, 'utf8'))
  const NAME = spec.name
  const OUT = path.dirname(specFile)
  const BASE = spec.baseUrl
  const { chromium } = loadPlaywright()
  const checks = []
  let page
  let currentStep = 0
  let last = { x: 640, y: 400 }
  let t0 = 0
  const log = (...a) => console.log(((Date.now() - t0) / 1000).toFixed(1), ...a)

  const record = (kind, msg, ok) => {
    checks.push({ step: currentStep, kind, msg, ok })
    log(ok ? 'ok:' : 'FAIL:', msg)
    if (!ok) throw new Error('CHECK FAILED: ' + msg)
  }

  async function glide(x, y) {
    const n = 28
    const sx = last.x
    const sy = last.y
    for (let i = 1; i <= n; i++) {
      const t = i / n
      const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
      await page.mouse.move(sx + (x - sx) * e, sy + (y - sy) * e)
      await sleep(15)
    }
    last = { x, y }
  }
  async function pointAt(loc) {
    await loc.scrollIntoViewIfNeeded()
    await sleep(250)
    const b = await loc.boundingBox()
    await glide(b.x + b.width / 2, b.y + Math.min(b.height / 2, 40))
  }
  async function highlight(loc, ms = 1800) {
    await loc.evaluate((e, ms) => {
      const old = e.style.boxShadow
      e.style.transition = 'box-shadow .3s'
      e.style.boxShadow = '0 0 0 5px #ffb300'
      setTimeout(() => (e.style.boxShadow = old), ms)
    }, ms)
  }
  async function clickAt(loc) {
    await pointAt(loc)
    await sleep(200)
    await loc.click()
  }
  async function go(p) {
    await page.goto(/^https?:/.test(p) ? p : BASE + p)
    await page.mouse.move(last.x, last.y)
  }
  const helpers = { glide, pointAt, highlight, clickAt, go, locate: (t, all) => locate(page, t, all) }

  let hooks = {}
  for (const key of ['setup', 'cleanup']) {
    if (spec[key] && !hooks[spec[key].module]) hooks[spec[key].module] = require(spec[key].module)
  }
  const hookFn = (def, name) => {
    const mod = hooks[def.module]
    const fn = mod && mod[name]
    if (typeof fn !== 'function') throw new Error(`hook ${name} is not exported by ${def.module}`)
    return fn
  }
  let ctxObj
  const callHook = (name, args) => {
    const def = spec.setup || spec.cleanup
    return hookFn(def, name)(ctxObj, args || {})
  }

  async function doCheck(a) {
    const timeout = a.timeoutMs || 10000
    switch (a.check) {
      case 'visible': {
        const loc = locate(page, a.target)
        let ok = true
        try {
          await loc.waitFor({ state: 'visible', timeout })
        } catch {
          ok = false
        }
        return record('visible', a.msg || 'visible: ' + describe(a.target), ok)
      }
      case 'absent': {
        await sleep(a.settleMs ?? 500)
        const n = await locate(page, a.target, true).count()
        return record('absent', a.msg || 'absent: ' + describe(a.target), n === 0)
      }
      case 'count': {
        const n = await locate(page, a.target, true).count()
        const ok = a.equals !== undefined ? n === a.equals : n >= a.atLeast
        return record('count', a.msg || `count ${n} ${a.equals !== undefined ? '== ' + a.equals : '>= ' + a.atLeast}: ${describe(a.target)}`, ok)
      }
      case 'order': {
        const txt = await page.locator(a.within).first().innerText()
        const idx = a.texts.map((t) => txt.indexOf(t))
        const ok = idx.every((v, i) => v >= 0 && (i === 0 || v > idx[i - 1]))
        return record('order', a.msg || `${a.within} reads ${a.texts.join(', ')}`, ok)
      }
      case 'text': {
        const txt = await locate(page, a.target).innerText()
        return record('text', a.msg || `text of ${describe(a.target)} contains "${a.contains}"`, txt.includes(a.contains))
      }
      case 'url':
        return record('url', a.msg || `url matches ${a.matches}`, new RegExp(a.matches).test(page.url()))
      default:
        throw new Error('unknown check ' + a.check)
    }
  }

  async function doAction(a) {
    if (a.check) return doCheck(a)
    switch (a.do) {
      case 'goto':
        return go(a.path)
      case 'scroll_to':
        await locate(page, a.target).evaluate((e, block) => e.scrollIntoView({ behavior: 'smooth', block }), a.block || 'start')
        return sleep(a.settleMs ?? 1500)
      case 'scroll':
        await page.evaluate((to) => window.scrollTo({ top: to === 'top' ? 0 : document.body.scrollHeight, behavior: 'smooth' }), a.to)
        return sleep(a.settleMs ?? 1200)
      case 'point':
        return pointAt(locate(page, a.target))
      case 'highlight':
        return highlight(locate(page, a.target), a.ms ?? 1800)
      case 'click':
        return clickAt(locate(page, a.target))
      case 'fill': {
        const loc = locate(page, a.target)
        await pointAt(loc)
        if (a.clear !== false) await loc.fill('')
        return loc.pressSequentially(a.value, { delay: a.delay ?? 90 })
      }
      case 'press':
        return page.keyboard.press(a.key)
      case 'wait':
        return sleep(a.ms)
      case 'wait_for':
        return locate(page, a.target).waitFor({ state: a.state || 'visible', timeout: a.timeoutMs || 30000 })
      case 'hook':
        return callHook(a.name, a.args)
      default:
        throw new Error('unknown action ' + a.do)
    }
  }

  // a timer that fires seconds late shows up as a long pause in the picture; say so in the log
  const stalls = []
  let tick = Date.now()
  const beat = setInterval(() => {
    const lag = Date.now() - tick - 250
    if (lag > 800) {
      log('WARNING: this container stalled for', (lag / 1000).toFixed(1), 's')
      stalls.push({ at: (Date.now() - t0) / 1000, sec: lag / 1000 })
    }
    tick = Date.now()
  }, 250)
  beat.unref()
  const browser = await chromium.launch()
  const vp = spec.viewport
  const context = await browser.newContext({ viewport: vp, recordVideo: { dir: path.join(OUT, 'raw-' + NAME), size: vp } })
  await context.addInitScript(CURSOR_JS)
  page = await context.newPage()
  t0 = Date.now()
  ctxObj = { page, context, browser, baseUrl: BASE, log, sleep, helpers, spec }
  let failed = null
  let result = null
  try {
    // red frame marks the start of the recording clock for assemble's sync, blue the end
    await page.setContent('<body style="margin:0;background:#ff0000"></body>')
    const mRed = (Date.now() - t0) / 1000
    await sleep(1200)
    if (spec.setup) await hookFn(spec.setup, spec.setup.export)(ctxObj, {})
    const start = spec.start || {}
    if (start.path) await go(start.path)
    if (start.waitFor) await locate(page, start.waitFor).waitFor({ state: 'attached', timeout: 30000 })
    await sleep(start.settleMs ?? 1500)
    const demoOffset = (Date.now() - t0) / 1000
    log('demoOffset', demoOffset)
    const steps = []
    for (let i = 0; i < spec.steps.length; i++) {
      currentStep = i
      const startAt = (Date.now() - t0) / 1000
      const dur = spec.durations[i]
      log('step', i, 'start')
      const endAt = startAt + dur + spec.gap
      for (const a of spec.steps[i].actions) await doAction(a)
      const wait = endAt - (Date.now() - t0) / 1000
      if (wait > 0) await sleep(wait * 1000)
      else log('step', i, 'action overran by', (-wait).toFixed(1), 's')
      const end = (Date.now() - t0) / 1000
      steps.push({ start: startAt - demoOffset, end: end - demoOffset, dur, overran: wait < 0 ? -wait : 0 })
    }
    await sleep(1200)
    const total = (Date.now() - t0) / 1000 - demoOffset
    await page.setContent('<body style="margin:0;background:#0000ff"></body>')
    const mBlue = (Date.now() - t0) / 1000
    await sleep(1200)
    result = { demoOffset, total, steps, mRed, mBlue, checks, stalls: stalls.filter((x) => x.at > demoOffset) }
  } catch (e) {
    failed = e
  }
  if (failed && spec.cleanup) {
    try {
      await hookFn(spec.cleanup, spec.cleanup.export)(ctxObj, { failed: true })
    } catch (e) {
      console.error('cleanup hook failed:', e.message)
    }
  } else if (spec.cleanup && result) {
    await hookFn(spec.cleanup, spec.cleanup.export)(ctxObj, { failed: false })
  }
  const video = page.video()
  await context.close()
  if (failed) {
    await browser.close()
    throw failed
  }
  await video.saveAs(path.join(OUT, NAME + '.webm'))
  fs.writeFileSync(path.join(OUT, NAME + '-timeline.json'), JSON.stringify(result, null, 2))
  fs.rmSync(path.join(OUT, 'raw-' + NAME), { recursive: true, force: true })
  await browser.close()
  if (process.env.DV_CHOWN) {
    const [u, g] = process.env.DV_CHOWN.split(':').map(Number)
    for (const f of [NAME + '.webm', NAME + '-timeline.json', NAME + '.run.json']) {
      try {
        fs.chownSync(path.join(OUT, f), u, g)
      } catch {}
    }
  }
  log('done')
}

main().catch((e) => {
  console.error('FAILED', e.message)
  process.exit(1)
})
