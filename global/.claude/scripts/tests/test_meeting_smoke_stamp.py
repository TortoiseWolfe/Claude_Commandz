"""The default suite never skips the transcriber's smoke test: it checks the smoke passed on exactly this code instead.

meeting_transcribe/ changes, the hash in meeting_smoke.stamp no longer matches, and this fails naming run_meeting_smoke.sh.
"""
import os
import shutil
import unittest
from tmpdirs import fresh_dir  # noqa: E402
import meeting_smoke_stamp as ms

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def scratch_copy(case):
    """A copy of just the files the stamp covers, in a scratch scripts folder."""
    d = fresh_dir(case, prefix="smoke-stamp-")
    for rel in ms.covered(SCRIPTS):
        os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
        shutil.copy(os.path.join(SCRIPTS, rel), os.path.join(d, rel))
    return d


class SmokeStamp(unittest.TestCase):
    def test_smoke_passed_on_this_code(self):
        self.assertIsNone(ms.problem())

    def test_a_one_byte_change_is_caught(self):
        d = scratch_copy(self)
        stamp = {"hash": ms.digest(d), "date": "2026-01-01 00:00", "sha": "abc1234def"}
        self.assertEqual(ms.digest(d), ms.digest(SCRIPTS))
        self.assertIsNone(ms.problem(d, stamp))
        with open(os.path.join(d, "meeting_transcribe", "run.sh"), "ab") as f:
            f.write(b"#")
        msg = ms.problem(d, stamp)
        self.assertIsNotNone(msg)
        self.assertIn("run_meeting_smoke.sh", msg)

    def test_a_new_file_in_the_folder_is_caught(self):
        d = scratch_copy(self)
        stamp = {"hash": ms.digest(d)}
        open(os.path.join(d, "meeting_transcribe", "extra.py"), "w").close()
        self.assertIsNotNone(ms.problem(d, stamp))

    def test_no_stamp_fails_naming_the_command(self):
        self.assertIn("run_meeting_smoke.sh", ms.problem(SCRIPTS, {}))

    def test_cache_files_do_not_count(self):
        d = scratch_copy(self)
        before = ms.digest(d)
        os.makedirs(os.path.join(d, "meeting_transcribe", "__pycache__"))
        open(os.path.join(d, "meeting_transcribe", "__pycache__", "x.pyc"), "w").write("x")
        self.assertEqual(ms.digest(d), before)


if __name__ == "__main__":
    unittest.main()
