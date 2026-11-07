"""Tests for skip_census.py"""

import json
import os
import tempfile
import unittest
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import skip_census as sc


def write_fixture(root: Path, rel: str, content: str):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


class SkipCensusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_explicit_skip_patterns(self):
        write_fixture(self.root, 'tests/foo.spec.ts', """
test.skip('skipped test', () => {})
describe.skip('skipped suite', () => {})
it.skip('skipped it', () => {})
test.fixme('fixme test', () => {})
""")
        result = sc.scan_repo(self.root)
        kinds = result['by_kind']
        self.assertEqual(kinds.get('explicit-skip'), 3)
        self.assertEqual(kinds.get('fixme'), 1)
        self.assertEqual(result['total_findings'], 4)

    def test_skipIf_and_conditional(self):
        write_fixture(self.root, 'tests/bar.spec.ts', """
test.skipIf(!process.env.CI, 'needs CI')
describe.skipIf(!hasFeature, 'needs feature')
test.skip(condition, 'reason')
test.skip(true)
""")
        result = sc.scan_repo(self.root)
        kinds = result['by_kind']
        self.assertEqual(kinds.get('skipIf'), 1)
        self.assertEqual(kinds.get('describe-skipIf'), 1)
        self.assertEqual(kinds.get('conditional-skip'), 1)
        self.assertEqual(kinds.get('bare-skip-true'), 1)
        self.assertEqual(result['total_findings'], 4)

    def test_playwright_testIgnore_and_grepInvert(self):
        write_fixture(self.root, 'playwright.config.ts', """
export default defineConfig({
  testIgnore: '**/flaky/**',
  grepInvert: '@slow',
})
""")
        result = sc.scan_repo(self.root)
        kinds = result['by_kind']
        self.assertEqual(kinds.get('testIgnore'), 1)
        self.assertEqual(kinds.get('grepInvert'), 1)

    def test_vitest_exclude(self):
        write_fixture(self.root, 'vitest.config.ts', """
export default defineConfig({
  test: {
    exclude: ['**/integration/**', '**/e2e/**'],
  }
})
""")
        result = sc.scan_repo(self.root)
        kinds = result['by_kind']
        self.assertEqual(kinds.get('vitest-exclude'), 1)

    def test_workflow_grep_invert(self):
        write_fixture(self.root, '.github/workflows/ci.yml', """
jobs:
  test:
    steps:
      - run: npx playwright test --grep-invert='@hosted'
""")
        result = sc.scan_repo(self.root)
        kinds = result['by_kind']
        self.assertEqual(kinds.get('workflow-grep-invert'), 1)

    def test_hidden_skip_early_return(self):
        write_fixture(self.root, 'tests/baz.spec.ts', """
test('hidden skip', () => {
  if (!process.env.TEST_DB) return
  expect(true).toBe(true)
})
""")
        result = sc.scan_repo(self.root)
        kinds = result['by_kind']
        self.assertEqual(kinds.get('possible-early-return-process-env'), 1)

    def test_hidden_skip_generic(self):
        write_fixture(self.root, 'tests/qux.spec.ts', """
test('hidden skip generic', () => {
  if (!someCondition) return
  expect(true).toBe(true)
})
""")
        result = sc.scan_repo(self.root)
        kinds = result['by_kind']
        self.assertEqual(kinds.get('possible-early-return-env'), 1)

    def test_json_output_structure(self):
        write_fixture(self.root, 'tests/sample.spec.ts', """
test.skip('skipped', () => {})
""")
        result = sc.scan_repo(self.root)
        self.assertIn('test_files_scanned', result)
        self.assertIn('total_findings', result)
        self.assertIn('by_kind', result)
        self.assertIn('findings', result)
        self.assertIsInstance(result['findings'], list)
        if result['findings']:
            f = result['findings'][0]
            self.assertIn('file', f)
            self.assertIn('line', f)
            self.assertIn('kind', f)
            self.assertIn('condition', f)
            self.assertIn('snippet', f)

    def test_parse_playwright_results_json(self):
        fixture = {
            "stats": {
                "expected": 2,
                "skipped": 1,
                "unexpected": 0,
                "flaky": 0
            },
            "suites": [{
                "file": "tests/e2e/foo.spec.ts",
                "specs": [{
                    "title": "test one",
                    "line": 10,
                    "tests": [{
                        "results": [{"status": "passed"}]
                    }]
                }, {
                    "title": "test two",
                    "line": 20,
                    "tests": [{
                        "results": [{"status": "skipped", "errors": [{"message": "fixture missing"}]}]
                    }]
                }]
            }]
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(fixture, f)
            f.flush()
            summary = sc.parse_playwright_json(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(summary.ran, 2)
        self.assertEqual(summary.passed, 1)
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(len(summary.skipped_details), 1)
        self.assertEqual(summary.skipped_details[0]['title'], 'test two')
        self.assertTrue(summary.has_json_report)

    def test_parse_vitest_results_json(self):
        fixture = {
            "tasks": [{
                "type": "suite",
                "tasks": [{
                    "type": "test",
                    "name": "passing test",
                    "state": "pass",
                    "file": "tests/unit/foo.test.ts",
                    "line": 5
                }, {
                    "type": "test",
                    "name": "skipped test",
                    "state": "skip",
                    "skipReason": "needs env",
                    "file": "tests/unit/foo.test.ts",
                    "line": 15
                }]
            }]
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(fixture, f)
            f.flush()
            summary = sc.parse_vitest_json(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(summary.ran, 2)
        self.assertEqual(summary.passed, 1)
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(len(summary.skipped_details), 1)
        self.assertEqual(summary.skipped_details[0]['title'], 'skipped test')
        self.assertTrue(summary.has_json_report)

    def test_scan_ignores_node_modules(self):
        write_fixture(self.root, 'tests/real.spec.ts', "test.skip('real', () => {})")
        write_fixture(self.root, 'node_modules/fake.spec.ts', "test.skip('fake', () => {})")
        result = sc.scan_repo(self.root)
        self.assertEqual(result['total_findings'], 1)
        self.assertEqual(result['findings'][0]['file'], 'tests/real.spec.ts')


if __name__ == '__main__':
    unittest.main()