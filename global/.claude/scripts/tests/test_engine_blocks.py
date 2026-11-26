"""engine_blocks.py: the one machine-written file of engine usage blocks. Made-up engines and times; the live file is never touched."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine_blocks as eb  # noqa: E402

SCRIPT = Path(eb.__file__)
NOW = 1_800_000_000


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="cc-test-eb-")
        self.addCleanup(td.cleanup)
        self.dir = Path(td.name)
        self.file = self.dir / "sub" / "engine-blocks.json"
        p = mock.patch.dict(os.environ, {"AFA_ENGINE_BLOCKS": str(self.file)})
        p.start()
        self.addCleanup(p.stop)

    def cli(self, *args):
        env = dict(os.environ, AFA_ENGINE_BLOCKS=str(self.file))
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, env=env)


class Records(Base):
    def test_no_file_reads_clean(self):
        self.assertEqual(eb.until("codex", NOW), 0)
        self.assertEqual(eb.phrase("codex", NOW), "")
        self.assertEqual(eb.show(NOW), "no engine is blocked")

    def test_malformed_file_reads_clean_and_a_write_refuses(self):
        self.file.parent.mkdir(parents=True)
        for bad in ("{not json", "[1, 2]", '{"codex": 5}'):
            self.file.write_text(bad)
            self.assertEqual(eb.until("codex", NOW), 0)
            self.assertEqual(eb.phrase("codex", NOW), "")
            with self.assertRaises(eb.Malformed):
                eb.block("codex", NOW + 10, NOW, "t")
            with self.assertRaises(eb.Malformed):
                eb.clear("codex", NOW, "t")
            self.assertEqual(self.file.read_text(), bad)

    def test_block_then_until(self):
        self.assertTrue(eb.block("codex", NOW + 1000, NOW, "t", "a note"))
        self.assertEqual(eb.until("codex", NOW), NOW + 1000)
        self.assertEqual(eb.until("codex", NOW + 1000), 0)  # lapsed
        self.assertEqual(eb.until("agy", NOW), 0)
        rec = json.loads(self.file.read_text())["codex"]
        self.assertEqual(rec, {"until": NOW + 1000, "evidence": NOW, "source": "t", "note": "a note"})

    def test_older_evidence_does_not_replace_newer(self):
        eb.block("codex", NOW + 1000, NOW, "t")
        self.assertFalse(eb.block("codex", NOW + 9000, NOW - 1, "t"))
        self.assertEqual(eb.until("codex", NOW), NOW + 1000)

    def test_newer_evidence_may_move_the_block_earlier_or_later(self):
        eb.block("codex", NOW + 9000, NOW, "t")
        self.assertTrue(eb.block("codex", NOW + 1000, NOW + 1, "t"))
        self.assertEqual(eb.until("codex", NOW), NOW + 1000)
        self.assertTrue(eb.block("codex", NOW + 5000, NOW + 2, "t"))
        self.assertEqual(eb.until("codex", NOW), NOW + 5000)

    def test_clear_with_older_or_equal_evidence_does_nothing(self):
        eb.block("codex", NOW + 1000, NOW, "t")
        self.assertFalse(eb.clear("codex", NOW - 5, "t"))
        self.assertFalse(eb.clear("codex", NOW, "t"))
        self.assertEqual(eb.until("codex", NOW), NOW + 1000)

    def test_clear_with_newer_evidence_removes_only_that_engine(self):
        eb.block("codex", NOW + 1000, NOW, "t")
        eb.block("agy", NOW + 1000, NOW, "t")
        self.assertTrue(eb.clear("codex", NOW + 1, "t"))
        self.assertEqual(eb.until("codex", NOW), 0)
        self.assertEqual(eb.until("agy", NOW), NOW + 1000)
        self.assertFalse(eb.clear("codex", NOW + 2, "t"))  # no record: nothing to do

    def test_phrase_text(self):
        eb.block("codex", NOW + 1000, NOW, "t")
        ph = eb.phrase("codex", NOW)
        self.assertTrue(ph.startswith("until Codex returns on "), ph)
        self.assertRegex(ph, r"on \d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} \d{1,2}:\d{2} (AM|PM)$")
        self.assertEqual(eb.phrase("codex", NOW + 5000), "")

    def test_file_mode_600_folder_700_and_no_temp_file_left(self):
        eb.block("codex", NOW + 1000, NOW, "t")
        eb.block("codex", NOW + 2000, NOW + 1, "t")
        eb.clear("codex", NOW + 2, "t")
        eb.block("agy", NOW + 1000, NOW, "t")
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.file.parent.stat().st_mode), 0o700)
        self.assertEqual(sorted(p.name for p in self.file.parent.iterdir()), ["engine-blocks.json"])

    def test_a_failed_write_leaves_the_old_file_and_no_temp_file(self):
        eb.block("codex", NOW + 1000, NOW, "t")
        before = self.file.read_text()
        with mock.patch.object(eb.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                eb.block("codex", NOW + 2000, NOW + 1, "t")
        self.assertEqual(self.file.read_text(), before)
        self.assertEqual(sorted(p.name for p in self.file.parent.iterdir()), ["engine-blocks.json"])


class Cli(Base):
    def test_block_until_phrase_show_clear(self):
        r = self.cli("block", "codex", "--until", "1 Jan 2999 12:43 PM", "--evidence", "1 Jan 2026", "--source", "t", "--note", "n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreater(int(self.cli("until", "codex").stdout), NOW)
        self.assertEqual(self.cli("phrase", "codex").stdout.strip(), "until Codex returns on 1 Jan 2999 12:43 PM")
        self.assertIn("Codex is blocked until 1 Jan 2999 12:43 PM", self.cli("show").stdout)
        self.assertEqual(self.cli("until", "agy").stdout.strip(), "0")
        self.assertEqual(self.cli("phrase", "agy").stdout.strip(), "")
        r = self.cli("clear", "codex", "--evidence", "1 Jan 2025", "--source", "t")
        self.assertEqual(r.returncode, 0)
        self.assertIn("nothing to do", r.stdout)
        self.assertEqual(self.cli("phrase", "codex").stdout.strip(), "until Codex returns on 1 Jan 2999 12:43 PM")
        r = self.cli("clear", "codex", "--source", "t")  # evidence defaults to now: newer than 1 Jan 2026
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "cleared"))
        self.assertEqual(self.cli("phrase", "codex").stdout.strip(), "")

    def test_clear_force_removes_a_stale_block_with_newer_evidence_on_record(self):
        # Issue 26: a stale block (recorded with newer evidence than a clean run) must be clearable on purpose.
        self.cli("block", "codex", "--until", "1 Jan 2999", "--evidence", "1 Jan 2026", "--source", "t")
        r = self.cli("clear", "codex", "--evidence", "1 Jan 2025", "--source", "t")  # not newer: refused without --force
        self.assertIn("nothing to do", r.stdout)
        r = self.cli("clear", "codex", "--force", "--evidence", "1 Jan 2025", "--source", "t")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "cleared"), r.stderr)
        self.assertEqual(self.cli("phrase", "codex").stdout.strip(), "")

    def test_clear_force_with_no_record_is_nothing_to_do_and_exits_0(self):
        r = self.cli("clear", "codex", "--force", "--source", "t")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to do", r.stdout)

    def test_older_block_says_nothing_to_do_and_exits_0(self):
        self.cli("block", "codex", "--until", "1 Jan 2999", "--evidence", "1 Jan 2026", "--source", "t")
        r = self.cli("block", "codex", "--until", "1 Feb 2999", "--evidence", "1 Jan 2025", "--source", "t")
        self.assertEqual(r.returncode, 0)
        self.assertIn("nothing to do", r.stdout)

    def test_bad_usage_exits_2(self):
        for args in ((), ("bogus",), ("until",), ("block", "codex", "--source", "t"), ("block", "codex", "--until", "not a date", "--source", "t"),
                     ("clear", "codex"), ("clear", "codex", "--source", "t", "--evidence", "not a date")):
            self.assertEqual(self.cli(*args).returncode, 2, args)

    def test_a_malformed_file_exits_2_on_write_and_is_not_destroyed(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text("{oops")
        r = self.cli("block", "codex", "--until", "1 Jan 2999", "--source", "t")
        self.assertEqual(r.returncode, 2)
        self.assertIn("malformed", r.stderr)
        self.assertEqual(self.file.read_text(), "{oops")
        self.assertEqual(self.cli("until", "codex").returncode, 0)
        self.assertEqual(self.cli("phrase", "codex").stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
