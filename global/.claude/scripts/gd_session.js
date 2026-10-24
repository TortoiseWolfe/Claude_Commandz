#!/usr/bin/env node
// gd_session.js: make sure the fleet browser is signed in to GoDaddy as Gail Lynn, with no human step.
//
//   node gd_session.js [--profile <dir>] [--keep-open]
//
// Exit 0  the page reads "logged in as: Gail Lynn"
// Exit 1  bad arguments or Playwright not found
// Exit 3  needs a human; stderr carries the page's own words
// Exit 5  the Delegate Access step-up offers only an SMS code ("SMS step-up"): the hub decides
// Exit 4  another Chromium holds the profile (agent_browser.sh busy), nothing was touched
//
// Headed on purpose: Akamai answers headless loads with "Access Denied". Chrome fills the saved
// password itself and this script never reads it; the 2-step code comes from otp_code.py. It logs
// page changes (origin and path, no query) and never a field value. It never signs out and never
// signs in through GoDaddy's "Sign in with Google" when Chrome has no saved password, then reaches
// Gail's account by GoDaddy's own route (help article 12373): account.godaddy.com/access, then
// "Access Now" on the row that names Gail Lynn. A step-up is answered with the authenticator
// (otp_code.py godaddy) only when the page offers it; an SMS-only step-up exits 5.
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync, spawnSync } = require('child_process');

const HOME = os.homedir();
const TARGET = 'https://account.godaddy.com/products';
const WHO = /logged in as:?\s*Gail Lynn/i;
const DEADLINE_MS = 150000;

// Page words are logged only after digit runs of 4+ and email addresses are masked.
function scrub(t) { return String(t).replace(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, '[email]').replace(/\d{4,}/g, '[digits]'); }
function log(m) { console.log('gd_session: ' + m); }
function needHuman(why, page) {
  console.error('gd_session: NEEDS A HUMAN: ' + why + (page ? ' | page says: ' + scrub(page) : ''));
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
    let page = ctx.pages()[0] || await ctx.newPage();
    let last = '';
    const watch = (p) => p.on('framenavigated', (f) => {
      if (f !== p.mainFrame()) return;
      const u = new URL(f.url());
      const here = u.origin + u.pathname;
      if (here !== last) { last = here; log('page: ' + here); }
    });
    watch(page);
    // Access Now may open the delegate's account in a new window: follow the newest page.
    ctx.on('page', (p) => { watch(p); page = p; });
    const reached = async () => {
      for (const p of ctx.pages()) {
        const t = await p.locator('body').innerText({ timeout: 3000 }).catch(() => '');
        if (WHO.test(t) || (/exit access/i.test(t) && /Gail Lynn/.test(t))) return true;
      }
      return false;
    };
    await page.goto(TARGET, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});

    const start = Date.now();
    let methodChosen = false, levelupLogged = false, delegateOpened = false, accessTries = 0, stepUpSwitched = false, signedIn = false, submitted = false, otpDone = false, googleTried = false, chooserDone = false;
    while (Date.now() - start < DEADLINE_MS) {
      await page.waitForTimeout(1500);
      const url = page.url();
      const host = new URL(url).hostname;
      const text = await words(page);

      if (await reached()) { code = 0; break; }
      if (/access denied/i.test(text)) { code = needHuman('Akamai blocked the load', text); break; }

      if (host === 'accounts.google.com') {
        // Account chooser: pick the tile by its email attribute (an address is not a secret; no password is typed).
        const tile = page.locator('[data-identifier]').first();
        if (!chooserDone && await tile.isVisible().catch(() => false)) {
          chooserDone = true; await tile.click(); log('picked the Google account'); continue;
        }
        if (/password|verify it|2-step|captcha/i.test(text)) { code = needHuman('Google asks for more than the account tile', text); break; }
        continue;
      }

      // GoDaddy serves Delegate Access on sso.godaddy.com/access too: that page goes to the Access Now block below.
      if (host.startsWith('sso.') && !/^\/access/.test(new URL(url).pathname)) {
        // Once per run, what the step-up page first says (words only, 6-digit runs masked), so we can see
        // whether GoDaddy texts the phone before offering the choice.
        if (!levelupLogged && /levelup/.test(url)) {
          levelupLogged = true; log('levelup page says: ' + scrub(text));
        }
        // "Select a method": choose the authenticator named Claude fleet. Never the phone row and never
        // "Recover your account" (that removes the verification methods).
        if (/select a method/i.test(text)) {
          if (methodChosen) { code = needHuman('the method choice did not move on', text); break; }
          const fleet = page.getByText('Claude fleet', { exact: false }).first();
          if (!await fleet.isVisible().catch(() => false)) { code = needHuman('no Claude fleet method offered', text); break; }
          await fleet.click();
          await page.getByRole('button', { name: /^continue$/i }).first().click();
          methodChosen = true; log('chose the Claude fleet authenticator');
          continue;
        }
        // Step-up ("Verify it's you"): the authenticator if the page offers it, never an SMS we cannot read.
        if (/verify it.?s you|levelup/i.test(text + ' ' + url) && /text message|sent to \*|sms/i.test(text)) {
          const other = page.getByText(/another way|different (way|method)|other (way|method)|authenticator|can.?t (use|access)/i).first();
          if (!stepUpSwitched && await other.isVisible().catch(() => false)) {
            stepUpSwitched = true; await other.click().catch(() => {}); log('asked for another way to verify'); continue;
          }
          console.error('gd_session: SMS step-up | page says: ' + scrub(text));
          code = 5; break;
        }
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
          if (!filled) {
            // No saved password: GoDaddy's own Google button, answered by the profile's Google session.
            const g = page.locator('[data-eid*="google" i], button[aria-label*="Google" i], a[aria-label*="Google" i], button:has-text("Google")').first();
            if (!googleTried && await g.isVisible().catch(() => false)) {
              googleTried = true; await g.click(); log('chose Sign in with Google'); continue;
            }
            code = needHuman('Chrome has no saved GoDaddy password to fill and the Google button is not available', text); break;
          }
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
        // Signed in as Jonathan: reach Gail's account by GoDaddy's own route (help 12373).
        if (!/^\/access/.test(new URL(url).pathname)) {
          if (!delegateOpened) {
            delegateOpened = true;
            await page.goto('https://account.godaddy.com/access', { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
          }
          continue;
        }
        // Tag the smallest block that names Gail Lynn and holds exactly one Access Now, then click it.
        const tagged = await page.evaluate(() => {
          const btns = [...document.querySelectorAll('button, a, [role=button]')].filter((b) => /^\s*access now\s*$/i.test(b.textContent));
          for (const b of btns) {
            let n = b.parentElement;
            while (n && n !== document.body) {
              const inN = btns.filter((x) => n.contains(x)).length;
              if (inN > 1) break;
              if (/Gail Lynn/.test(n.textContent)) { b.setAttribute('data-gd-access', 'gail'); return true; }
              n = n.parentElement;
            }
          }
          return false;
        }).catch(() => false);
        if (tagged) {
          log('Access Now on the Gail Lynn row');
          await page.locator('[data-gd-access="gail"]').first().click().catch(() => {});
          await page.waitForTimeout(3000);
          continue;
        }
        if (++accessTries > 8) { code = needHuman('Delegate Access shows no Access Now row for Gail Lynn', text); break; }
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
