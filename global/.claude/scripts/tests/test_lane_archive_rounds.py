"""lane_archive_rounds.py: at a recycle, finished rounds leave report.md and status.md for an archive file (issue 128).

A lane's own notes grow by one "## " section per round and nothing was ever archived, so a lane's re-read of them outgrew its recycle
line. Only invented text is used here."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from tmpdirs import fresh_dir  # noqa: E402
import lane_archive_rounds as lar  # noqa: E402


def rounds(n, body="x" * 400):
    return "".join("## Round %d\n%s\n\n" % (i, body) for i in range(1, n + 1))


class ArchiveRounds(unittest.TestCase):
    def folder(self, **files):
        d = Path(fresh_dir(self))
        for name, text in files.items():
            (d / name).write_text(text)
        return d

    def test_keeps_line_one_and_the_open_round(self):
        d = self.folder(**{"report.md": "QUEUE EMPTY\n\nintro line\n\n" + rounds(4)})
        lar.archive_folder(d, min_bytes=0)
        kept = (d / "report.md").read_text()
        self.assertTrue(kept.startswith("QUEUE EMPTY\n\nintro line\n"))
        self.assertIn("## Round 4", kept)
        for n in (1, 2, 3):
            self.assertNotIn("## Round %d\n" % n, kept)
        arch = (d / "report-archive.md").read_text()
        for n in (1, 2, 3):
            self.assertIn("## Round %d\n" % n, arch)
        self.assertNotIn("## Round 4", arch)

    def test_nothing_is_lost(self):
        text = "line one\n" + rounds(5)
        d = self.folder(**{"status.md": text})
        lar.archive_folder(d, min_bytes=0)
        both = (d / "status.md").read_text() + (d / "status-archive.md").read_text()
        for n in range(1, 6):
            self.assertEqual(both.count("## Round %d\n" % n), 1)
        self.assertEqual(len(both.replace("\n", "")), len(text.replace("\n", "")) )

    def test_a_second_pass_appends_and_does_not_repeat(self):
        d = self.folder(**{"report.md": "top\n" + rounds(3)})
        lar.archive_folder(d, min_bytes=0)
        with open(d / "report.md", "a") as f:
            f.write("## Round 4\nnew work\n\n")
        lar.archive_folder(d, min_bytes=0)
        arch = (d / "report-archive.md").read_text()
        self.assertEqual([arch.count("## Round %d\n" % n) for n in (1, 2, 3, 4)], [1, 1, 1, 0])
        self.assertNotIn("## Round 3\n", (d / "report.md").read_text())
        self.assertIn("## Round 4", (d / "report.md").read_text())

    def test_small_or_single_round_files_are_left_alone(self):
        small = "top\n" + rounds(3, body="y")
        one = "top\n" + rounds(1, body="z" * 9000)
        d = self.folder(**{"report.md": small, "status.md": one})
        lar.archive_folder(d)  # default threshold
        self.assertEqual((d / "report.md").read_text(), small)
        self.assertEqual((d / "status.md").read_text(), one)
        self.assertFalse((d / "report-archive.md").exists())
        self.assertFalse((d / "status-archive.md").exists())

    def test_modified_time_is_kept(self):
        d = self.folder(**{"report.md": "top\n" + rounds(4)})
        os.utime(d / "report.md", (1_700_000_000, 1_700_000_000))
        lar.archive_folder(d, min_bytes=0)
        self.assertEqual(int((d / "report.md").stat().st_mtime), 1_700_000_000)

    def test_a_heading_inside_a_code_fence_is_not_a_round(self):
        text = "top\n## Round 1\na\n\n```\n## not a round\n```\n\n## Round 2\nb\n"
        d = self.folder(**{"report.md": text})
        lar.archive_folder(d, min_bytes=0)
        arch = (d / "report-archive.md").read_text()
        self.assertIn("## not a round", arch)
        self.assertIn("```", arch)
        self.assertEqual((d / "report.md").read_text(), "top\n## Round 2\nb\n")

    def test_missing_files_are_fine_and_other_files_untouched(self):
        d = self.folder(**{"handoff.md": "keep me\n" + rounds(3)})
        before = (d / "handoff.md").read_text()
        self.assertEqual(lar.archive_folder(d, min_bytes=0), [])
        self.assertEqual((d / "handoff.md").read_text(), before)

    def test_cli_prints_one_line_per_file_and_exits_zero(self):
        d = self.folder(**{"report.md": "top\n" + rounds(4), "status.md": "top\n" + rounds(3)})
        out = subprocess.run([sys.executable, "-I", str(HERE / "lane_archive_rounds.py"), "--min-bytes", "0", str(d)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("report.md", lines[0])
        self.assertIn("3 rounds", lines[0])
        bad = subprocess.run([sys.executable, "-I", str(HERE / "lane_archive_rounds.py"), "/nonexistent-folder-for-test"],
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 0)
        self.assertEqual(bad.stdout.strip(), "")

    def test_a_link_in_place_of_the_archive_or_the_notes_file_is_never_written_through(self):
        # A lane can write in its own folder; the hub must not follow a link it left there (security review of issue 128).
        text = "QUEUE EMPTY\n\n" + rounds(4)
        d = self.folder(**{"report.md": text})
        target = Path(fresh_dir(self)) / "victim.txt"
        target.write_text("keep me")
        os.symlink(target, d / "report-archive.md")
        self.assertEqual(lar.archive_folder(d), [])
        self.assertEqual(target.read_text(), "keep me")
        self.assertEqual((d / "report.md").read_text(), text)
        d2 = self.folder(**{"real.md": text})
        os.symlink(d2 / "real.md", d2 / "report.md")
        self.assertEqual(lar.archive_folder(d2), [])
        self.assertEqual((d2 / "real.md").read_text(), text)
        self.assertFalse((d2 / "report-archive.md").exists())

    def test_a_link_in_place_of_the_temp_name_is_left_alone(self):
        text = "QUEUE EMPTY\n\n" + rounds(4)
        d = self.folder(**{"report.md": text})
        target = Path(fresh_dir(self)) / "victim.txt"
        target.write_text("keep me")
        os.symlink(target, d / ".report.md.tmp")
        self.assertEqual(lar.archive_folder(d), [])
        self.assertEqual(target.read_text(), "keep me")

    def test_a_second_hard_link_to_the_archive_is_never_written_through(self):
        # A hard link is a second name for the same bytes, so it passes a plain-file check; the archive must have one name only.
        text = "QUEUE EMPTY\n\n" + rounds(4)
        d = self.folder(**{"report.md": text})
        target = Path(fresh_dir(self)) / "victim.txt"
        target.write_text("keep me")
        os.link(target, d / "report-archive.md")
        self.assertEqual(lar.archive_folder(d), [])
        self.assertEqual(target.read_text(), "keep me")
        self.assertEqual((d / "report.md").read_text(), text)


if __name__ == "__main__":
    unittest.main()
