#!/usr/bin/env node
// usage_calibrate.js: keep the weekly-limit gauge honest. Reads the weekly percentage and reset time from
// Claude's own Usage page (claude.ai settings, Usage) in the fleet browser profile, then runs
//   python3 ~/repos/hub/scripts/model_quality_report.py --calibrate <pct>@<now, Eastern ISO time>
// so the dispatcher's budget gates, the hub digest and every lane-start decision read the real figure.
//
//   node usage_calibrate.js [--dry-run] [--fixture <page-text-file>] [--calibration-file <path>] [--db <ledger>] [--now <ISO>]
//
//   --dry-run   read and check the figure, print what would run, change nothing
//   --fixture   take the page text from a file instead of the browser (tests, rehearsals)
//
// Exit 0  calibrated (or, with --dry-run, would have)
// Exit 1  bad arguments, or Playwright not found
// Exit 2  the page was read but model_quality_report.py failed
// Exit 3  "claude.ai not signed in on the fleet profile": nothing changed
// Exit 4  another Chromium holds the fleet profile and offers no debugging port to attach to: nothing touched
// Exit 5  the reading was refused (outside 0 to 100, lower than the last calibration of the same week, or the
//         page no longer shows a weekly percentage): the calibration file is untouched, the reason is logged
// Exit 6  Cloudflare's "security verification" page did not clear: nothing changed
//
// Browser rules: if agent_browser.sh busy says a Chromium holds the profile, attach to it over its own
// --remote-debugging-port in a new tab and close only that tab; never kill or close the holder's window.
// Otherwise start a plain Chromium on the profile (no automation flags: Cloudflare challenged both Playwright's
// own launch and --headless=new on 27 Oct, and passed this one), read the page, and stop only the Chromium
// this script started. Never prints cookies, tokens or page text beyond the two figures.
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const { execFileSync, spawn, spawnSync } = require('child_process');

const HOME = os.homedir();
const SCRIPTS = path.join(HOME, '.claude/scripts');
const REPORT = path.join(HOME, 'repos/hub/scripts/model_quality_report.py');
const DEFAULT_CAL = path.join(HOME, '.config/ledger/limit-calibration.json');
const USAGE_URL = 'https://claude.ai/settings/usage';
const WEEK_MS = 7 * 24 * 3600 * 1000;

function log(m) { console.log('usage_calibrate: ' + m); }

// ---- pure parts (tested against saved page text) ---------------------------------------------------

const WEEK_LABELS = [/all models/i, /this week(?!\S)/i];

// The weekly figure: the text after "All models" or "This week" holds "Resets <when>" and "<N>% used".
// "Current session" (the five-hour window) comes first on the page and is never the one we want.
// Returns {pct, resets, label} or null.
function parseUsage(text) {
  const t = String(text || '').replace(/\r/g, '');
  for (const re of WEEK_LABELS) {
    const m = re.exec(t);
    if (!m) continue;
    const rest = t.slice(m.index + m[0].length, m.index + m[0].length + 240);
    const pct = /(\d{1,3}(?:\.\d+)?)\s*%\s*used/i.exec(rest);
    if (!pct) continue;
    const resets = /Resets\s+([^\n]+)/i.exec(rest);
    return { pct: parseFloat(pct[1]), resets: resets ? resets[1].trim() : null, label: m[0] };
  }
  return null;
}

function pageState(text, url) {
  const t = String(text || '');
  if (/security verification|verify you are (a )?human|just a moment/i.test(t) && !/Your usage/i.test(t)) return 'challenge';
  if (/\/(login|logout)\b/.test(url || '') || (/Continue with (Google|email)/i.test(t) && !/Your usage/i.test(t))) return 'signed-out';
  if (parseUsage(t)) return 'usage';
  return 'unknown';
}

// Usage only rises inside a week. `cal` is the parsed calibration file (or null). Returns {ok, reason}.
function checkReading(pct, cal, now) {
  if (!Number.isFinite(pct) || pct <= 0 || pct > 100) {
    return { ok: false, reason: 'reading ' + pct + '% is outside 0 to 100 (the report needs 0 < pct <= 100)' };
  }
  if (cal && Number.isFinite(cal.pct) && cal.week_start) {
    const ws = Date.parse(cal.week_start);
    const t = now.getTime();
    if (Number.isFinite(ws) && t >= ws && t < ws + WEEK_MS && pct < cal.pct) {
      return { ok: false, reason: 'reading ' + pct + '% is lower than the ' + cal.pct + '% calibration of ' + cal.at +
        ' in the same week (usage only rises); calibration file left alone' };
    }
  }
  return { ok: true, reason: null };
}

// "2026-10-27T21:58:00-04:00": the moment in Eastern time with its offset, the shape --calibrate was given by hand.
function easternIso(d) {
  const f = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', hourCycle: 'h23',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const p = {};
  for (const x of f.formatToParts(d)) p[x.type] = x.value;
  const asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
  const offMin = Math.round((asUtc - Math.floor(d.getTime() / 1000) * 1000) / 60000);
  const sign = offMin < 0 ? '-' : '+';
  const a = Math.abs(offMin);
  const pad = (n) => String(n).padStart(2, '0');
  return p.year + '-' + p.month + '-' + p.day + 'T' + p.hour + ':' + p.minute + ':' + p.second +
    sign + pad(Math.floor(a / 60)) + ':' + pad(a % 60);
}

function readCalibration(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { return null; }
}

// ---- browser ---------------------------------------------------------------------------------------

function loadPlaywright() {
  const tries = [process.env.PLAYWRIGHT_MODULE, 'playwright',
    path.join(HOME, 'repos/Chattanooga-Digital/node_modules/playwright'),
    path.join(HOME, 'repos/consemble/node_modules/playwright'),
    path.join(HOME, 'repos/Eduity/node_modules/playwright')].filter(Boolean);
  for (const t of tries) { try { return require(t); } catch (e) { /* next */ } }
  return null;
}

function fleet() {
  const out = execFileSync('bash', [path.join(SCRIPTS, 'agent_browser.sh'), 'path'], { encoding: 'utf8' }).trim().split('\n');
  return { exe: out[0], profile: out[1] };
}

function profileBusy() {
  return spawnSync('bash', [path.join(SCRIPTS, 'agent_browser.sh'), 'busy']).status === 0;
}

// The holder's debugging port: from its own command line, else the DevToolsActivePort file it wrote.
function holderPort(profile) {
  try {
    const pid = fs.readlinkSync(path.join(profile, 'SingletonLock')).split('-').pop();
    const args = fs.readFileSync('/proc/' + pid + '/cmdline', 'utf8').split('\0');
    for (const a of args) {
      const m = /^--remote-debugging-port=(\d+)$/.exec(a);
      if (m && +m[1] > 0) return +m[1];
    }
  } catch (e) { /* fall through */ }
  try {
    const n = parseInt(fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0], 10);
    if (n > 0) return n;
  } catch (e) { /* none */ }
  return null;
}

function cdpUp(port) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/json/version', timeout: 1500 }, (res) => { res.resume(); resolve(res.statusCode === 200); });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Open the Usage page in a new tab of a Chromium reachable at `port`, wait out Cloudflare's interstitial,
// and return {state, text, url}. Closes only the tab it opened.
async function readPage(pw, port) {
  const browser = await pw.chromium.connectOverCDP('http://127.0.0.1:' + port);
  let page;
  try {
    const ctx = browser.contexts()[0] || await browser.newContext();
    page = await ctx.newPage();
    await page.goto(USAGE_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
    let text = '', state = 'unknown';
    for (let i = 0; i < 20; i++) {
      await sleep(2500);
      try { text = await page.locator('body').innerText({ timeout: 4000 }); } catch (e) { text = ''; }
      state = pageState(text, page.url());
      if (state === 'usage' || state === 'signed-out') break;
    }
    // The page redirects to /new#settings/usage and draws a panel: give it a moment to fill in.
    return { state, text, url: page.url() };
  } finally {
    try { if (page) await page.close(); } catch (e) { /* tab already gone */ }
    try { await browser.close(); } catch (e) { /* detaches from the browser; the process keeps running */ }
  }
}

async function fromBrowser() {
  const pw = loadPlaywright();
  if (!pw) { console.error('usage_calibrate: Playwright not found'); return { code: 1 }; }
  const { exe, profile } = fleet();
  if (profileBusy()) {
    const port = holderPort(profile);
    if (!port || !(await cdpUp(port))) {
      console.error('usage_calibrate: a Chromium holds the fleet profile with no debugging port to attach to; nothing touched');
      return { code: 4 };
    }
    log('attaching to the Chromium that holds the profile (port ' + port + '), new tab only');
    return { page: await readPage(pw, port) };
  }
  try { fs.unlinkSync(path.join(profile, 'DevToolsActivePort')); } catch (e) { /* none */ }
  const env = Object.assign({}, process.env);
  if (!env.DISPLAY && !env.WAYLAND_DISPLAY) {       // started by Task Scheduler through wsl.exe: borrow WSLg's screen
    env.DISPLAY = ':0'; env.WAYLAND_DISPLAY = 'wayland-0'; env.XDG_RUNTIME_DIR = '/mnt/wslg/runtime-dir';
  }
  const child = spawn(exe, ['--user-data-dir=' + profile, '--no-first-run', '--remote-debugging-port=0', 'about:blank'],
    { env, stdio: 'ignore' });
  let port = null;
  for (let i = 0; i < 40 && !port; i++) {
    await sleep(500);
    try { port = parseInt(fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0], 10) || null; } catch (e) { /* not yet */ }
    if (child.exitCode !== null) break;
  }
  try {
    if (!port || !(await cdpUp(port))) { console.error('usage_calibrate: the Chromium we started never opened its debugging port'); return { code: 1 }; }
    return { page: await readPage(pw, port) };
  } finally {
    if (child.exitCode === null) {
      child.kill('SIGTERM');
      for (let i = 0; i < 20 && child.exitCode === null; i++) await sleep(250);
    }
  }
}

// ---- main ------------------------------------------------------------------------------------------

function parseArgs(argv) {
  const a = { dryRun: false, fixture: null, calFile: DEFAULT_CAL, now: null, db: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--dry-run') a.dryRun = true;
    else if (argv[i] === '--fixture' && argv[i + 1]) a.fixture = argv[++i];
    else if (argv[i] === '--calibration-file' && argv[i + 1]) a.calFile = argv[++i];
    else if (argv[i] === '--db' && argv[i + 1]) a.db = argv[++i];
    else if (argv[i] === '--now' && argv[i + 1]) a.now = new Date(argv[++i]);
    else return null;
  }
  return a;
}

async function main(argv) {
  const args = parseArgs(argv);
  if (!args || (args.now && isNaN(args.now))) {
    console.error('usage: usage_calibrate.js [--dry-run] [--fixture FILE] [--calibration-file FILE] [--db FILE] [--now ISO]');
    return 1;
  }
  let page;
  if (args.fixture) {
    const text = fs.readFileSync(args.fixture, 'utf8');
    page = { text, url: USAGE_URL, state: pageState(text, USAGE_URL) };
  } else {
    const r = await fromBrowser();
    if (r.code) return r.code;
    page = r.page;
  }
  if (page.state === 'challenge') { console.error('usage_calibrate: Cloudflare security verification did not clear; nothing changed'); return 6; }
  if (page.state === 'signed-out') { console.error('usage_calibrate: claude.ai not signed in on the fleet profile'); return 3; }
  const got = parseUsage(page.text);
  if (!got) { console.error('usage_calibrate: refused: the Usage page shows no weekly "% used" figure (page changed?)'); return 5; }
  const now = args.now || new Date();
  const verdict = checkReading(got.pct, readCalibration(args.calFile), now);
  if (!verdict.ok) { console.error('usage_calibrate: refused: ' + verdict.reason); return 5; }
  const when = easternIso(now);
  log('page reads ' + got.pct + '% used this week (' + got.label + '; resets ' + (got.resets || 'unread') + ') at ' + when);
  if (args.dryRun) { log('dry run: would run model_quality_report.py --calibrate ' + got.pct + '@' + when); return 0; }
  const cmd = [REPORT, '--calibrate', got.pct + '@' + when, '--calibration-file', args.calFile];
  if (args.db) cmd.push('--db', args.db);
  if (args.now) cmd.push('--now', when);
  const r = spawnSync('python3', cmd, { encoding: 'utf8', timeout: 180000 });
  if (r.status !== 0) {
    console.error('usage_calibrate: model_quality_report.py failed (exit ' + r.status + '): ' + String(r.stderr || '').trim().slice(-400));
    return 2;
  }
  const used = /\| Used so far \| ([\d.]+)%/.exec(r.stdout || '');
  log('calibrated: ' + got.pct + '%' + (used ? '; the report now reads ' + used[1] + '% used so far' : ''));
  return 0;
}

module.exports = { parseUsage, pageState, checkReading, easternIso, holderPort };

// The watcher (lane_watch.sh) reads this file: the Windows task runs through conhost --headless, which hides this
// script's exit code, so the outcome is also written where a person or script can read it (issue 273).
function writeStatus(file, code, reason, now) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const body = { at: easternIso(now), code, ok: code === 0, reason: code === 0 ? 'ok' : reason };
  fs.writeFileSync(file, JSON.stringify(body) + '\n');
}

async function runAndRecord(argv) {
  const args = parseArgs(argv);
  const lines = [];
  const realError = console.error;
  console.error = (...a) => { lines.push(a.join(' ')); realError(...a); };
  let code;
  try { code = await main(argv); }
  catch (e) { console.error('usage_calibrate: ' + e.message); code = 1; }
  finally { console.error = realError; }
  if (args && !args.dryRun) {
    const reason = (lines[lines.length - 1] || 'no message').replace(/^usage_calibrate: /, '').slice(0, 300);
    try { writeStatus(path.join(path.dirname(args.calFile), 'usage-calibrate-status.json'), code, reason, args.now || new Date()); }
    catch (e) { realError('usage_calibrate: could not write the status file: ' + e.message); }
  }
  return code;
}

if (require.main === module) {
  runAndRecord(process.argv.slice(2)).then((c) => process.exit(c));
}
