// Tests for usage_calibrate.js: parsing, the guard, the Eastern time stamp, and the whole script against saved
// page text (--fixture) with a throwaway calibration file and database. No browser, no network.
// Run: node --test ~/.claude/scripts/tests/test_usage_calibrate.js
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const uc = require('../usage_calibrate.js');

const fx = (n) => fs.readFileSync(path.join(__dirname, 'fixtures', n), 'utf8');
const fxPath = (n) => path.join(__dirname, 'fixtures', n);

test('parses the weekly figure from the live page text, not the session figure', () => {
  const r = uc.parseUsage(fx('usage_page.txt'));
  assert.strictEqual(r.pct, 94);
  assert.strictEqual(r.resets, 'Wednesday 12:00 PM');
});

test('parses the All models row and a decimal percentage', () => {
  const r = uc.parseUsage(fx('usage_page_all_models.txt'));
  assert.strictEqual(r.pct, 41.5);
  assert.match(r.label, /all models/i);
});

test('returns null when the page shows no weekly percentage', () => {
  assert.strictEqual(uc.parseUsage(fx('login_page.txt')), null);
  assert.strictEqual(uc.parseUsage('Current session\nResets at 10:00 PM\n8% used\n'), null);
  assert.strictEqual(uc.parseUsage(''), null);
});

test('page state: usage, challenge, signed out, unknown', () => {
  assert.strictEqual(uc.pageState(fx('usage_page.txt'), 'https://claude.ai/new#settings/usage'), 'usage');
  assert.strictEqual(uc.pageState(fx('challenge_page.txt'), 'https://claude.ai/settings/usage'), 'challenge');
  assert.strictEqual(uc.pageState(fx('login_page.txt'), 'https://claude.ai/login'), 'signed-out');
  assert.strictEqual(uc.pageState('Hello', 'https://claude.ai/new'), 'unknown');
});

const cal = { pct: 91.0, at: '2026-10-27T14:21:00-04:00', week_start: '2026-10-21T12:00:00-04:00' };

test('guard accepts a higher or equal reading in the same week', () => {
  assert.ok(uc.checkReading(94, cal, new Date('2026-10-27T21:58:00-04:00')).ok);
  assert.ok(uc.checkReading(91, cal, new Date('2026-10-27T21:58:00-04:00')).ok);
});

test('guard refuses a reading lower than the last calibration of the same week', () => {
  const v = uc.checkReading(90, cal, new Date('2026-10-27T21:58:00-04:00'));
  assert.strictEqual(v.ok, false);
  assert.match(v.reason, /usage only rises/);
});

test('guard accepts a lower reading once a new week has begun (after the Wednesday reset)', () => {
  assert.ok(uc.checkReading(3, cal, new Date('2026-10-28T12:05:00-04:00')).ok);
  assert.ok(uc.checkReading(3, cal, new Date('2026-10-28T12:00:00-04:00')).ok);
});

test('guard refuses a reading outside 0 to 100, zero, and not-a-number', () => {
  const now = new Date('2026-10-27T21:58:00-04:00');
  for (const bad of [0, -1, 100.5, 250, NaN, undefined]) assert.strictEqual(uc.checkReading(bad, null, now).ok, false, String(bad));
  assert.ok(uc.checkReading(100, null, now).ok);
});

test('guard with no calibration file accepts a sane reading', () => {
  assert.ok(uc.checkReading(55, null, new Date()).ok);
});

test('Eastern time stamp carries the right offset in summer and winter', () => {
  assert.strictEqual(uc.easternIso(new Date('2026-10-28T01:58:00Z')), '2026-10-27T21:58:00-04:00');
  assert.strictEqual(uc.easternIso(new Date('2026-12-02T17:00:00Z')), '2026-12-02T12:00:00-05:00');
  assert.strictEqual(uc.easternIso(new Date('2026-11-01T05:30:00Z')), '2026-11-01T01:30:00-04:00');
});

// ---- the whole script against a fixture, with a throwaway ledger DB and calibration file ----

function sandbox() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'usage-cal-'));
  return { dir, calFile: path.join(dir, 'limit-calibration.json') };
}
const run = (args) => spawnSync('node', [path.join(__dirname, '..', 'usage_calibrate.js'), ...args], { encoding: 'utf8' });

test('--dry-run prints the command it would run and writes nothing', () => {
  const s = sandbox();
  const r = run(['--fixture', fxPath('usage_page.txt'), '--dry-run', '--calibration-file', s.calFile, '--now', '2026-10-27T21:58:00-04:00']);
  assert.strictEqual(r.status, 0, r.stderr);
  assert.match(r.stdout, /would run model_quality_report\.py --calibrate 94@2026-10-27T21:58:00-04:00/);
  assert.ok(!fs.existsSync(s.calFile));
});

test('exit 3 on the signed-out page, nothing written', () => {
  const s = sandbox();
  const r = run(['--fixture', fxPath('login_page.txt'), '--calibration-file', s.calFile]);
  assert.strictEqual(r.status, 3);
  assert.match(r.stderr, /claude\.ai not signed in on the fleet profile/);
  assert.ok(!fs.existsSync(s.calFile));
});

test('exit 6 on the Cloudflare page', () => {
  assert.strictEqual(run(['--fixture', fxPath('challenge_page.txt'), '--dry-run']).status, 6);
});

test('exit 5 when the page has no weekly figure', () => {
  assert.strictEqual(run(['--fixture', fxPath('usage_page.txt'), '--dry-run', '--now', 'garbage']).status, 1);
  const s = sandbox();
  const f = path.join(s.dir, 'odd.txt');
  fs.writeFileSync(f, 'Your usage\nCurrent session\n8% used\n');
  assert.strictEqual(run(['--fixture', f, '--dry-run']).status, 5);
});

test('exit 5 and the calibration file untouched when the reading is lower than this week\'s calibration', () => {
  const s = sandbox();
  const before = JSON.stringify({ version: 1, pct: 97.0, at: '2026-10-27T20:00:00-04:00', week_start: '2026-10-21T12:00:00-04:00', pct_per_unit: 1e-7 });
  fs.writeFileSync(s.calFile, before);
  const r = run(['--fixture', fxPath('usage_page.txt'), '--calibration-file', s.calFile, '--now', '2026-10-27T21:58:00-04:00']);
  assert.strictEqual(r.status, 5);
  assert.match(r.stderr, /usage only rises/);
  assert.strictEqual(fs.readFileSync(s.calFile, 'utf8'), before);
});

test('a calibration through model_quality_report.py on a synthetic ledger writes the file and the report reads the page figure', () => {
  const s = sandbox();
  const db = path.join(s.dir, 'ledger.db');
  const mk = spawnSync('python3', ['-c', `
import sqlite3, datetime as dt
c = sqlite3.connect(${JSON.stringify('PLACEHOLDER')})
`.replace('PLACEHOLDER', db) + `
c.execute("CREATE TABLE tokens(message_id TEXT PRIMARY KEY, ts INTEGER, session TEXT, agent_id TEXT, repo TEXT, model TEXT, sidechain INTEGER, input INTEGER, output INTEGER, cache_5m INTEGER, cache_1h INTEGER, cache_read INTEGER)")
ts = int(dt.datetime(2026, 10, 23, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=-4))).timestamp())
c.execute("INSERT INTO tokens VALUES('m1', ?, 's', NULL, 'r', 'claude-opus-5-5', 0, 5000000, 0, 0, 0, 0)", (ts,))
c.commit()
`], { encoding: 'utf8' });
  assert.strictEqual(mk.status, 0, mk.stderr);
  const r = run(['--fixture', fxPath('usage_page.txt'), '--calibration-file', s.calFile, '--db', db, '--now', '2026-10-27T21:58:00-04:00']);
  assert.strictEqual(r.status, 0, r.stderr);
  const c = JSON.parse(fs.readFileSync(s.calFile, 'utf8'));
  assert.strictEqual(c.pct, 94);
  assert.strictEqual(c.at, '2026-10-27T21:58:00-04:00');
  assert.ok(c.pct_per_unit > 0);
  assert.match(r.stdout, /the report now reads 94\.0% used so far/);
});

// ---- the status file the watcher reads (issue 273): every outcome leaves one line of evidence ----

const readStatus = (s) => JSON.parse(fs.readFileSync(path.join(s.dir, 'usage-calibrate-status.json'), 'utf8'));

test('a Cloudflare check writes a failed status next to the calibration file', () => {
  const s = sandbox();
  const r = run(['--fixture', fxPath('challenge_page.txt'), '--calibration-file', s.calFile, '--now', '2026-10-27T21:58:00-04:00']);
  assert.strictEqual(r.status, 6);
  const st = readStatus(s);
  assert.strictEqual(st.ok, false);
  assert.strictEqual(st.code, 6);
  assert.match(st.reason, /Cloudflare/);
  assert.strictEqual(st.at, '2026-10-27T21:58:00-04:00');
});

test('a signed-out page writes code 3 and a refused reading writes code 5', () => {
  const s = sandbox();
  run(['--fixture', fxPath('login_page.txt'), '--calibration-file', s.calFile]);
  assert.strictEqual(readStatus(s).code, 3);
  const f = path.join(s.dir, 'odd.txt');
  fs.writeFileSync(f, 'Your usage\nCurrent session\n8% used\n');
  run(['--fixture', f, '--calibration-file', s.calFile]);
  assert.strictEqual(readStatus(s).code, 5);
});

test('a dry run writes no status file', () => {
  const s = sandbox();
  run(['--fixture', fxPath('challenge_page.txt'), '--dry-run', '--calibration-file', s.calFile]);
  assert.ok(!fs.existsSync(path.join(s.dir, 'usage-calibrate-status.json')));
});
