"""risk_ping.py: one tray ping per at-risk item, never twice, never silent on a failure.

The board, the ledger output and the notifier are all invented stand-ins in a throwaway folder; nothing touches the live
~/.claude/state or the real tray."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(HERE))
from tmpdirs import fresh_dir  # noqa: E402

PING = str(SCRIPTS / "risk_ping.py")
LEDGER = str(SCRIPTS / "notes_ledger.py")

RISK_A = "AT-RISK e-1111111 [hub] ear-check the sample take | due 1:00 PM, in 4 h | queued, no lane"
RISK_B = "AT-RISK e-2222222 [hub] send the invented draft | due 2:00 PM, in 5 h | queued, no lane"


class RiskPing(unittest.TestCase):
    def setUp(self):
        self.d = Path(fresh_dir(self))
        self.state = self.d / "pinged.txt"
        self.log = self.d / "notify.log"
        self.ledger_out = self.d / "ledger.out"
        # a fake ledger that prints a file, and a fake notifier that appends its title to a log
        self.ledger = self.d / "ledger.sh"
        self.ledger.write_text('#!/bin/sh\ncat "%s"\nexit "${LEDGER_RC:-0}"\n' % self.ledger_out)
        self.notify = self.d / "notify.sh"
        self.notify.write_text('#!/bin/sh\nif [ -n "$NOTIFY_FAIL" ]; then echo boom >&2; exit 3; fi\necho "$1" >> "%s"\n' % self.log)
        for f in (self.ledger, self.notify):
            f.chmod(0o755)

    def run_ping(self, *extra, env=None):
        e = dict(os.environ)
        e.update(env or {})
        return subprocess.run(
            [sys.executable, PING, "--state", str(self.state), "--ledger", str(self.ledger), "--notify", str(self.notify), *extra],
            capture_output=True, text=True, env=e,
        )

    def pings(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_pings_each_new_item_once(self):
        self.ledger_out.write_text(RISK_A + "\n" + RISK_B + "\n")
        r = self.run_ping()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.pings(), ["At risk: e-1111111", "At risk: e-2222222"])
        self.assertEqual(self.state.read_text().split(), ["e-1111111", "e-2222222"])

    def test_second_run_says_nothing_new(self):
        self.ledger_out.write_text(RISK_A + "\n")
        self.run_ping()
        r = self.run_ping()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(self.pings()), 1)

    def test_only_the_new_item_is_pinged_later(self):
        self.ledger_out.write_text(RISK_A + "\n")
        self.run_ping()
        self.ledger_out.write_text(RISK_A + "\n" + RISK_B + "\n")
        self.run_ping()
        self.assertEqual(self.pings(), ["At risk: e-1111111", "At risk: e-2222222"])

    def test_nothing_at_risk_pings_nothing(self):
        self.ledger_out.write_text("")
        r = self.run_ping()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.pings(), [])
        self.assertFalse(self.state.exists())

    def test_dry_run_changes_nothing(self):
        self.ledger_out.write_text(RISK_A + "\n")
        r = self.run_ping("--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertIn("would ping: " + RISK_A, r.stdout)
        self.assertEqual(self.pings(), [])
        self.assertFalse(self.state.exists())

    def test_ledger_failure_is_not_nothing_at_risk(self):
        self.ledger_out.write_text(RISK_A + "\n")
        r = self.run_ping(env={"LEDGER_RC": "2"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("ledger command failed", r.stderr)
        self.assertEqual(self.pings(), [])

    def test_failed_ping_is_not_saved_and_is_retried(self):
        self.ledger_out.write_text(RISK_A + "\n")
        r = self.run_ping(env={"NOTIFY_FAIL": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("notify failed for e-1111111", r.stderr)
        self.assertFalse(self.state.exists())
        r = self.run_ping()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.pings(), ["At risk: e-1111111"])

    def test_lines_that_are_not_at_risk_lines_are_ignored(self):
        self.ledger_out.write_text("some other output\n  AT-RISK indented " + "\n" + RISK_A + "\n")
        self.run_ping()
        self.assertEqual(self.pings(), ["At risk: e-1111111"])

    def test_end_to_end_with_the_real_ledger_on_an_invented_board(self):
        board = self.d / "board.md"
        board.write_text("- [ ] 2026-11-07 08:31 | Job: ear-check the sample take | due: 2026-11-07 1:00 PM | e-1111111\n")
        lanes = self.d / "lanes"
        lanes.mkdir()
        cmd = "%s %s risk --inbox %s --lanes-dir %s --now '2026-11-07 09:00' --no-net" % (sys.executable, LEDGER, board, lanes)
        r = subprocess.run(
            [sys.executable, PING, "--state", str(self.state), "--ledger", cmd, "--notify", str(self.notify)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        # the ledger names each item by its own id, so check the shape, then that a second run is quiet
        self.assertEqual(len(self.pings()), 1)
        self.assertTrue(self.pings()[0].startswith("At risk: "))
        self.assertEqual(r.stdout.strip(), "pinged: " + self.pings()[0][len("At risk: "):])
        r2 = subprocess.run(
            [sys.executable, PING, "--state", str(self.state), "--ledger", cmd, "--notify", str(self.notify)],
            capture_output=True, text=True,
        )
        self.assertEqual(r2.stdout, "")
        self.assertEqual(len(self.pings()), 1)


if __name__ == "__main__":
    unittest.main()
