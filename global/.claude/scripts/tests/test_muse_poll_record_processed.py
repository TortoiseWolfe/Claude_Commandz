"""Tests for `muse_poll_record.sh processed`: it records the note id and, for a Gmail draft id, labels the
draft Label_31 and Label_32 through our own Gmail API tool. The tool is a stub (MUSE_GMAIL_API); no network,
and STATE/HOME are a temp dir."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "muse_poll_record.sh"

STUB = """#!/usr/bin/env python3
import os, sys
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
print("STUB-STDOUT-MARKER")
print("STUB-STDERR-MARKER", file=sys.stderr)
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
"""


class ProcessedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state = root / "state"
        self.state.mkdir()
        self.stub = root / "stub_gmail_api.py"
        self.stub.write_text(STUB)
        self.stub_log = root / "stub.log"

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, note_id, stub_exit=0):
        env = {**os.environ, "HOME": self.tmp.name, "MUSE_STATE": str(self.state), "HUB_INBOX": str(self.state / "hub-inbox.md"),
               "MUSE_GMAIL_API": str(self.stub), "STUB_LOG": str(self.stub_log), "STUB_EXIT": str(stub_exit)}
        return subprocess.run(["bash", str(SCRIPT), "processed", note_id], env=env, capture_output=True, text=True, timeout=60)

    def processed(self):
        path = self.state / "agent-notes-processed.txt"
        return path.read_text().splitlines() if path.exists() else []

    def calls(self):
        return self.stub_log.read_text().splitlines() if self.stub_log.exists() else []

    def test_draft_id_is_recorded_once_and_labelled_31_then_32(self):
        r = self.record("r-1234567890")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.processed(), ["r-1234567890"])
        self.assertEqual(self.calls(), ["draft-label r-1234567890 Label_31", "draft-label r-1234567890 Label_32"])

    def test_positive_draft_id_is_labelled_too(self):
        self.record("r8399774348393883238")
        self.assertEqual(self.calls(), ["draft-label r8399774348393883238 Label_31", "draft-label r8399774348393883238 Label_32"])

    def test_recording_again_does_not_duplicate_the_list_line(self):
        self.record("r-1234567890")
        self.record("r-1234567890")
        self.assertEqual(self.processed(), ["r-1234567890"])

    def test_sent_message_id_gets_no_label_call(self):
        r = self.record("19a1b2c3d4e5f607")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.processed(), ["19a1b2c3d4e5f607"])
        self.assertEqual(self.calls(), [])

    def test_failing_tool_still_exits_zero_and_writes_the_fail_log(self):
        r = self.record("r-1234567890", stub_exit=1)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.processed(), ["r-1234567890"])
        lines = (self.state / "agent-notes-label-fail.log").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith(" r-1234567890 Label_31 label failed"), lines[0])
        self.assertTrue(lines[1].endswith(" r-1234567890 Label_32 label failed"), lines[1])

    def test_tool_output_never_reaches_the_caller(self):
        r = self.record("r-1234567890")
        self.assertNotIn("STUB-", r.stdout + r.stderr)
        r = self.record("r-1234567891", stub_exit=1)
        self.assertNotIn("STUB-", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
