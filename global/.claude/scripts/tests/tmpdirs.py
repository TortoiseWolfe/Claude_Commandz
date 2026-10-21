"""Every test temp folder is removed when its test ends.

20 Oct 2026: tests made folders with tempfile.mkdtemp() and never removed them. One suite run left 889 folders (16,633 files) in
/tmp, the mutant runners repeat the suite hundreds of times, and the disk ran out of inodes. Make test folders with fresh_dir
instead; test_no_tmp_leak.py runs the whole suite and fails if anything is left behind.

Every folder made here is named cc-test-*, so a sweep can remove day-old cc-test-* folders and touch nothing else (20 Oct 2026).
"""
import atexit
import shutil
import tempfile
import unittest

PREFIX = "cc-test-"


def fresh_dir(case=None, **kw):
    """tempfile.mkdtemp(**kw) named cc-test-<prefix>..., removed when the test ends (a TestCase), when its class ends (a TestCase
    class), or at exit."""
    kw["prefix"] = PREFIX + (kw.get("prefix") or "")
    d = tempfile.mkdtemp(**kw)
    if isinstance(case, unittest.TestCase):
        case.addCleanup(shutil.rmtree, d, True)
    elif isinstance(case, type) and issubclass(case, unittest.TestCase):
        case.addClassCleanup(shutil.rmtree, d, True)
    else:
        atexit.register(shutil.rmtree, d, True)
    return d


DISK_FULL = ("No space left on device", "[Errno 28]")


def run_env(copy_dir):
    """The environment for a mutant's test run: its temp files go inside its own copy, so removing the copy removes them."""
    import os
    from pathlib import Path
    t = Path(copy_dir) / ".tmp"
    t.mkdir(exist_ok=True)
    return dict(os.environ, TMPDIR=str(t))


def disk_full(result):
    """True when a test run failed because the disk was full: that red is not a kill (20 Oct 2026, ENOSPC read as kills)."""
    out = (result.stdout or "") + (result.stderr or "")
    return any(w in out for w in DISK_FULL)
