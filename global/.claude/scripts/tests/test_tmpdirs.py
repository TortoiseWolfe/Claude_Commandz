#!/usr/bin/env python3
"""tmpdirs.fresh_dir: every test folder is named cc-test-* (a sweep may delete day-old cc-test-* and nothing else) and is gone
when its test ends."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tmpdirs  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402


class Named(unittest.TestCase):
    def test_every_folder_is_named_cc_test(self):
        for kw in ({}, {"prefix": "ledgertest"}, {"prefix": None}, {"suffix": "-x"}):
            name = os.path.basename(fresh_dir(self, **kw))
            self.assertTrue(name.startswith("cc-test-"), (kw, name))
        self.assertTrue(os.path.basename(fresh_dir(self, prefix="ledgertest")).startswith("cc-test-ledgertest"))

    def test_the_folder_is_made_where_asked(self):
        base = fresh_dir(self)
        self.assertEqual(os.path.dirname(fresh_dir(self, dir=base)), base)


class GoneWhenItsTestEnds(unittest.TestCase):
    def test_a_test_case_folder_is_removed_by_its_cleanups(self):
        case = unittest.TestCase()
        d = fresh_dir(case)
        Path(d, "f").write_text("x")
        case.doCleanups()
        self.assertFalse(os.path.exists(d))

    def test_a_class_folder_is_removed_by_its_class_cleanups(self):
        class C(unittest.TestCase):
            pass
        d = fresh_dir(C)
        C.doClassCleanups()
        self.assertFalse(os.path.exists(d))

    def test_a_disk_full_run_is_not_a_kill(self):
        class R:
            stdout, stderr = "", "OSError: [Errno 28] No space left on device: '/tmp/tmpv4g1t0we'"
        self.assertTrue(tmpdirs.disk_full(R()))
        R.stderr = "AssertionError: 3 != 2"
        self.assertFalse(tmpdirs.disk_full(R()))

    def test_a_mutant_run_keeps_its_temp_files_in_its_copy(self):
        copy = fresh_dir(self)
        env = tmpdirs.run_env(copy)
        self.assertEqual(env["TMPDIR"], os.path.join(copy, ".tmp"))
        self.assertTrue(os.path.isdir(env["TMPDIR"]))


if __name__ == "__main__":
    unittest.main()
