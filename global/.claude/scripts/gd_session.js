#!/usr/bin/env node
// gd_session.js: make sure the fleet browser is signed in to GoDaddy as Gail Lynn, with no human step.
//
//   node gd_session.js [--profile <dir>] [--keep-open]
//
// Exit 0  the page reads "logged in as: Gail Lynn"
// Exit 1  bad arguments or Playwright not found
// Exit 3  needs a human; stderr carries the page's own words
// Exit 4  another Chromium holds the profile (agent_browser.sh busy), nothing was touched
//
// Headed on purpose: Akamai answers headless loads with "Access Denied". Chrome fills the saved
// password itself and this script never reads it; the 2-step code comes from otp_code.py. It logs
// page changes (origin and path, no query) and never a field value. It never signs out and never
// opens Delegate Access (account.godaddy.com/access): that ended the login on 22 Oct, so Gail's
// account is reached from the account menu instead.
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync, spawnSync } = require('child_process');

const HOME = os.homedir();
const TARGET = 'https://account.godaddy.com/products';
const WHO = /logged in as:?\s*Gail Lynn/i;
const DEADLINE_MS = 150000;

function log(m) { console.log('gd_session: ' + m); }
function needHuman(why, page) {
  console.error('gd_session: NEEDS A HUMAN: ' + why + (page ? ' | page says: ' + page : ''));
  return 3;
}

function loadPlaywright() {
  const tries = [process.env.PLAYWRIGHT_MODULE, 'playwright',
    path.join(HOME, 'repos/consemble/node_modules/playwright'),
    path.join(HOME, 'repos/Eduity/node_modules/playwright')].filter(Boolean);
  for (const t of tries) { try { return require(t); } catch (e) { /* next */ } }
  return null;
}

function parseArgs(argv) {
  const a = { profile: null, keepOpen: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--profile' && argv[i + 1]) a.profile = argv[++i];
    else if (argv[i] === '--keep-open') a.keepOpen = true;
    else return null;
  }
  return a;
}

function otpPath() {
  const here = path.join(__dirname, 'otp_code.py');
  return fs.existsSync(here) ? here : path.join(HOME, '.claude/scripts/otp_code.py');
}

// A page's own words, trimmed, for the exit-3 message. Page text only, never a field value.
async function words(page) {
  try { return (await page.locator('body').innerText({ timeout: 3000 })).replace(/\s+/g, ' ').trim().slice(0, 300); }
  catch (e) { return ''; }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args) { console.error('usage: gd_session.js [--profile <dir>] [--keep-open]'); return 1; }
  const pw = loadPlaywright();
  if (!pw) { console.error('gd_session: Playwright not found (set PLAYWRIGHT_MODULE)'); return 1; }

  const browserSh = path.join(HOME, '.claude/scripts/agent_browser.sh');
  const [exe, fleetProfile] = execFileSync(browserSh, ['path'], { encoding: 'utf8' }).trim().split('\n');
  const profile = args.profile ? path.resolve(args.profile) : fleetProfile;
  if (!args.profile && spawnSync(browserSh, ['busy']).status === 0) {
    console.error('gd_session: a Chromium already holds the fleet profile; not touching it');
    return 4;
  }

  const ctx = await pw.chromium.launchPersistentContext(profile, {
    executablePath: exe, headless: false, viewport: null,
    ignoreDefaultArgs: ['--enable-automation'], args: ['--start-maximized'],
  });
  let code = 3;
  try {
    const page = ctx.pages()[0] || await ctx.newPage();
    let last = '';
    page.on('framenavigated', (f) => {
      if (f !== page.mainFrame()) return;
      const u = new URL(f.url());
      const here = u.origin + u.pathname;
      if (here !== last) { last = here; log('page: ' + here); }
    });
    await page.goto(TARGET, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});

    const start = Date.now();
    let triedAccountMenu = false, signedIn = false, submitted = false, otpDone = false;
    while (Date.now() - start < DEADLINE_MS) {
      await page.waitForTimeout(1500);
      const url = page.url();
      const host = new URL(url).hostname;
      const text = await words(page);

      if (WHO.test(await page.locator('body').innerText({ timeout: 3000 }).catch(() => ''))) { code = 0; break; }
      if (/access denied/i.test(text)) { code = needHuman('Akamai blocked the load', text); break; }

      if (host.startsWith('sso.')) {
        // 2-step prompt first: it can show on the same host as the password form.
        const otp = page.locator('input[autocomplete="one-time-code"], input[name*="code" i], input[inputmode="numeric"]').first();
        if (await otp.isVisible().catch(() => false)) {
          if (otpDone) { code = needHuman('the 2-step code was rejected', text); break; }
          const r = spawnSync('python3', [otpPath(), 'godaddy'], { encoding: 'utf8' });
          if (r.status !== 0) { code = needHuman('otp_code.py godaddy failed: ' + (r.stderr || '').trim()); break; }
          await otp.fill(r.stdout.trim());
          await page.keyboard.press('Enter');
          otpDone = true; log('2-step code submitted');
          continue;
        }
        const pass = page.locator('input[type="password"]').first();
        if (await pass.isVisible().catch(() => false)) {
          if (submitted) { code = needHuman('sign-in did not go through', text); break; }
          // Booleans only: whether autofill filled the fields, never what it put in them.
          let filled = await pass.evaluate((el) => el.value.length > 0).catch(() => false);
          if (!filled) {
            const user = page.locator('input[name="username"], input#username, input[autocomplete="username"]').first();
            await user.click().catch(() => {});
            await page.keyboard.press('ArrowDown').catch(() => {});
            await page.keyboard.press('Enter').catch(() => {});
            await page.waitForTimeout(1500);
            filled = await pass.evaluate((el) => el.value.length > 0).catch(() => false);
          }
          if (!filled) { code = needHuman('Chrome has no saved GoDaddy password to fill', text); break; }
          const keep = page.getByLabel(/keep me signed in/i).first();
          if (await keep.count()) await keep.check().catch(() => {});
          await page.locator('button[type="submit"]').first().click();
          submitted = true; log('sign-in submitted');
          continue;
        }
        // Username-only first step, then the password step: submit and loop.
        const userOnly = page.locator('input[name="username"], input#username').first();
        if (await userOnly.isVisible().catch(() => false)
            && await userOnly.evaluate((el) => el.value.length > 0).catch(() => false) && !submitted) {
          await page.locator('button[type="submit"]').first().click();
          continue;
        }
        continue;
      }

      if (host.endsWith('godaddy.com')) {
        signedIn = true;
        // Signed in as someone else (Jonathan): reach Gail's account from the account menu.
        if (!triedAccountMenu) {
          triedAccountMenu = true;
          const menu = page.locator('[data-eid*="account" i], [aria-label*="account" i], [aria-label*="profile" i], button:has-text("Account")').first();
          if (await menu.isVisible().catch(() => false)) {
            await menu.click().catch(() => {});
            await page.waitForTimeout(1000);
            const gail = page.getByText(/Gail Lynn/).first();
            if (await gail.isVisible().catch(() => false)) {
              await gail.click().catch(() => {});
              log('chose Gail Lynn from the account menu');
              continue;
            }
          }
        }
      }
    }
    if (code !== 0 && Date.now() - start >= DEADLINE_MS) {
      code = needHuman(signedIn ? 'signed in but the page never read "logged in as: Gail Lynn"' : 'no sign-in progress', await words(page));
    }
    if (code === 0) log('logged in as: Gail Lynn');
    if (args.keepOpen && code === 0) await new Promise((res) => ctx.on('close', res));
  } finally {
    await ctx.close().catch(() => {});
  }
  return code;
}

main().then((c) => process.exit(c), (e) => { console.error('gd_session: ' + e.message); process.exit(3); });
