"""machine_heartbeat.py: the one-line heartbeat, the once-a-day write, the hub-page report, and the publish check in
notes_check.py. Every fixture is invented (fake uptime, meminfo and tmux files in a temp folder); the real machine is never read."""

import os
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import machine_heartbeat as mh  # noqa: E402
import notes_check as nc  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

TZ = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 25, 11, 18, 2, tzinfo=TZ).timestamp()


def line_at(when, name="NX-01"):
    return "%s %s up_hours=42 lanes=8 mem_free_mb=5120\n" % (when.astimezone(TZ).isoformat(timespec="seconds"), name)


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(fresh_dir(self))
        (self.d / "uptime").write_text("152000.55 300000.10\n")  # 42 h
        (self.d / "meminfo").write_text("MemTotal: 16000000 kB\nMemAvailable:    5242880 kB\n")  # 5120 MB
        (self.d / "tmux").write_text("@1\n@2\n@3\n@4\n@5\n@6\n@7\n@8\n")
        env = {"HEARTBEAT_UPTIME_FILE": str(self.d / "uptime"), "HEARTBEAT_MEMINFO_FILE": str(self.d / "meminfo"),
               "HEARTBEAT_TMUX_OUT": str(self.d / "tmux"), "TZ": "America/New_York"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        time.tzset()  # a changed TZ is read only after this; a runner that is on UTC would otherwise print UTC
        self.addCleanup(time.tzset)
        self.addCleanup(p.stop)
        os.environ.pop("HEARTBEAT_MACHINE", None)


class Line(Base):
    def test_format_from_fake_files(self):
        out = mh.make_line("NX-01", NOW)
        self.assertRegex(out, r"^2026-11-25T11:18:02-05:00 NX-01 up_hours=42 lanes=8 mem_free_mb=5120$")

    def test_machine_name_from_the_env_var(self):
        with mock.patch.dict(os.environ, {"HEARTBEAT_MACHINE": "tower"}):
            self.assertIn(" tower ", mh.make_line(None, NOW))

    def test_missing_tmux_output_gives_zero_lanes_never_an_error(self):
        (self.d / "tmux").write_text("")
        self.assertIn(" lanes=0 ", mh.make_line("NX-01", NOW))
        with mock.patch.dict(os.environ, {"HEARTBEAT_TMUX_OUT": str(self.d / "nope")}):
            self.assertIn(" lanes=0 ", mh.make_line("NX-01", NOW))

    def test_the_command_prints_one_line(self):
        r = subprocess.run([sys.executable, "-I", str(HERE.parent / "machine_heartbeat.py"), "line", "--machine", "NX-01", "--now", str(NOW)],
                           capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout.splitlines()), 1)


class Write(Base):
    def test_a_fresh_file_is_left_alone_and_an_old_one_is_rewritten(self):
        f = self.d / "sub" / "nx-01.txt"
        self.assertTrue(mh.write_file(str(f), "NX-01", NOW - 19 * 3600 - 60 * 0))  # no file yet: written
        first = f.read_text()
        self.assertFalse(mh.write_file(str(f), "NX-01", NOW))  # 19 h old: under 20, skipped
        self.assertEqual(f.read_text(), first)
        self.assertTrue(mh.write_file(str(f), "NX-01", NOW + 3600))  # 20 h old: rewritten
        self.assertNotEqual(f.read_text(), first)
        self.assertEqual(len(f.read_text().splitlines()), 1)
        self.assertEqual([p.name for p in f.parent.iterdir()], ["nx-01.txt"])  # no temp file left behind


class Report(Base):
    def make(self, name, text):
        p = self.d / name
        p.write_text(text)
        return str(p)

    def test_fresh_stale_and_missing(self):
        fresh = self.make("nx-01.txt", line_at(datetime.fromtimestamp(NOW - 3600, TZ)))
        stale = self.make("tower.txt", "2026-11-22T11:00:00-05:00 tower up_hours=3 lanes=0 mem_free_mb=900\n")
        missing = str(self.d / "mace.txt")
        self.assertEqual(mh.report_line(fresh, NOW), "- NX-01: up 42 h, 8 lanes, 5120 MB free (seen 10:18 AM)")
        self.assertEqual(mh.report_line(stale, NOW), "- tower: LAST SEEN 3 days ago (Sun 22 Nov 11:00 AM), nothing since")
        self.assertEqual(mh.report_line(missing, NOW), "- mace: no heartbeat (nothing installed or never written)")

    def test_a_file_with_extra_text_counts_as_unreadable(self):
        junk = self.make("odd.txt", line_at(datetime.fromtimestamp(NOW, TZ)) + "more\n")
        self.assertIn("no heartbeat", mh.report_line(junk, NOW))

    def test_the_26_hour_limit_decides_fresh_against_stale(self):
        # Mutation check: with the limit at 26 h a 27-hour-old line is stale; a 25-hour-old one is fresh.
        old27 = self.make("a.txt", line_at(datetime.fromtimestamp(NOW - 27 * 3600, TZ)))
        old25 = self.make("b.txt", line_at(datetime.fromtimestamp(NOW - 25 * 3600, TZ)))
        self.assertEqual(mh.FRESH_HOURS, 26)
        self.assertIn("LAST SEEN", mh.report_line(old27, NOW))
        self.assertNotIn("LAST SEEN", mh.report_line(old25, NOW))
        with mock.patch.object(mh, "FRESH_HOURS", 28):
            self.assertNotIn("LAST SEEN", mh.report_line(old27, NOW))  # the stale case flips

    def test_the_report_command_prints_one_line_per_file(self):
        a = self.make("nx-01.txt", line_at(datetime.fromtimestamp(NOW, TZ)))
        r = subprocess.run([sys.executable, "-I", str(HERE.parent / "machine_heartbeat.py"), "report", a, str(self.d / "gone.txt"), "--now", str(NOW)],
                           capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(len(r.stdout.splitlines()), 2, r.stdout)


class PublishCheck(Base):
    def test_only_exactly_one_heartbeat_line_may_be_published(self):
        good = self.d / "good.txt"
        good.write_text(mh.make_line("tower", NOW) + "\n")
        bad = self.d / "bad.txt"
        bad.write_text(mh.make_line("tower", NOW) + "\nextra text\n")
        words = self.d / "words.txt"
        words.write_text("hello there\n")
        with mock.patch.object(nc.subprocess, "run") as run:
            for f in (bad, words):
                with self.assertRaises(nc.PublishReadError):
                    nc.publish_file(str(f), "hub/tower/heartbeat.txt")
            self.assertFalse(run.called)  # refused before any upload call
            run.return_value = mock.Mock(returncode=1, stdout=b"", stderr=b"404")
            nc.publish_file(str(good), "hub/tower/heartbeat.txt")  # passes the check and reaches the upload code
            self.assertTrue(run.called)


if __name__ == "__main__":
    unittest.main()
