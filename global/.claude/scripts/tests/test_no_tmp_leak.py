#!/usr/bin/env python3
"""A suite run leaves nothing in the temp folder.

20 Oct 2026, about 3:30 AM: the disk ran out of inodes. /tmp held 3.1 million folders the tests had made and never removed (889
per suite run, 16,633 files), multiplied by the mutant runners. This test runs the whole suite with the temp folder pointed at an
empty one and fails, naming what was left, if anything is still there afterwards. The inner run skips this test.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
INNER = "TMP_LEAK_INNER"


@unittest.skipIf(os.environ.get(INNER), "the inner run of the leak test")
class NothingLeftInTmp(unittest.TestCase):
    def test_a_whole_suite_run_leaves_the_temp_folder_empty(self):
        box = tempfile.mkdtemp(prefix="leakcheck-")
        try:
            env = dict(os.environ, TMPDIR=box, **{INNER: "1"})
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(HERE)], cwd=HERE.parent,
                               env=env, capture_output=True, text=True, timeout=900)
            self.assertIn(" passed", r.stdout, r.stdout[-2000:] + r.stderr[-2000:])
            left = sorted(os.listdir(box))
            shapes = ["%s: %s" % (n, ", ".join(sorted(os.listdir(os.path.join(box, n)))[:5]) if os.path.isdir(os.path.join(box, n)) else "file")
                      for n in left[:10]]
            self.assertEqual(len(left), 0, "a suite run left %d entries in the temp folder; make them with tmpdirs.fresh_dir(self):\n%s"
                             % (len(left), "\n".join(shapes)))
        finally:
            shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
