// The one usage_calibrate.js case that runs the hub repo's model_quality_report.py (~/repos/hub/scripts) on a synthetic ledger.
// It needs that private repo on the machine, so CI cannot run it (see .github/ci_not_run.tsv); run it on the tower:
//   node --test ~/.claude/scripts/tests/test_usage_calibrate_report.js
// The rest of the usage_calibrate.js tests are in test_usage_calibrate.js and do run in CI.
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const fxPath = (n) => path.join(__dirname, 'fixtures', n);
function sandbox() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'usage-cal-'));
  return { dir, calFile: path.join(dir, 'limit-calibration.json') };
}
const run = (args) => spawnSync('node', [path.join(__dirname, '..', 'usage_calibrate.js'), ...args], { encoding: 'utf8' });

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
