"""19 Oct: the hub closed tigard-trace on "report:yes idle". Its report.md was from 12:20 PM, but at 12:40 PM Jonathan's complaint was forwarded
into the lane and by 1:05 PM it was running the test suite in the background. The report is the lane's last word only when it is newer than
every input the lane got and no background job is pending. The timeline below is that lane's (UTC stamps, Eastern + 4 h)."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hub_digest as hd  # noqa: E402
import lane_report_state as lrs  # noqa: E402
import openclaw_status_digest as osd  # noqa: E402

DAY = datetime(2026, 10, 19, tzinfo=timezone.utc)


def at(h, m):
    return DAY + timedelta(hours=h, minutes=m)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(t, text, **extra):
    return {"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": text}, **extra}


def tool_result(t):
    return {"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}


def bash(t, tool_id, background):
    inp = {"command": "docker compose run tests", **({"run_in_background": True} if background else {})}
    return {"type": "assistant", "timestamp": iso(t), "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tool_id, "name": "Bash", "input": inp}]}}


def notice(t, tool_id, op="enqueue"):
    return {"type": "queue-operation", "operation": op, "timestamp": iso(t),
            "content": f"<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>{tool_id}</tool-use-id>\n<status>completed</status>\n</task-notification>"}


class Tigard(unittest.TestCase):
    """12:20 PM report (16:20 UTC); 12:40 PM forwarded complaint; 12:43 PM compaction; 1:05 PM suite in the background; 1:15 PM it ends."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.report = Path(self.tmp.name) / "report.md"
        self.report.write_text("done\n")
        self.transcript = Path(self.tmp.name) / "s.jsonl"

    def state(self, rows, report_at=at(16, 20)):
        os.utime(self.report, (report_at.timestamp(), report_at.timestamp()))
        self.transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return lrs.report_state(self.report, self.transcript, now=at(17, 30).timestamp())

    base = [user(at(14, 37), "You are a lane in ~/repos/ada-stair-generator ..."), bash(at(15, 37), "t1", True), notice(at(15, 58), "t1"),
            tool_result(at(16, 19))]

    def test_a_report_with_nothing_after_it_is_the_last_word(self):
        self.assertEqual(self.state(self.base), "fresh")

    def test_input_after_the_report_makes_it_stale(self):
        rows = self.base + [user(at(16, 40), "Jonathan says (from his OpenClaw chat): the blue lines on the Trace tabs don't match the photo",
                                 origin={"kind": "human"})]
        self.assertEqual(self.state(rows), "input")

    def test_input_typed_while_the_lane_was_mid_turn_counts_too(self):
        queued = {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(at(16, 40)), "content": "one more thing, Jonathan"}
        self.assertEqual(self.state(self.base + [queued]), "input")

    def test_a_background_job_still_running_makes_it_stale(self):
        rows = self.base + [bash(at(17, 5), "t2", True)]
        self.assertEqual(self.state(rows, report_at=at(17, 6)), "job")

    def test_the_job_ending_clears_it(self):
        rows = self.base + [bash(at(17, 5), "t2", True), notice(at(17, 15), "t2")]
        self.assertEqual(self.state(rows, report_at=at(17, 6)), "fresh")

    def test_a_foreground_command_is_not_a_job(self):
        self.assertEqual(self.state(self.base + [bash(at(17, 5), "t3", False)], report_at=at(17, 6)), "fresh")

    def test_a_report_written_after_the_input_and_the_job_counts(self):
        rows = self.base + [user(at(16, 40), "forwarded complaint", origin={"kind": "human"}), bash(at(17, 5), "t2", True), notice(at(17, 15), "t2")]
        self.assertEqual(self.state(rows, report_at=at(17, 20)), "fresh")

    def test_what_is_not_input(self):
        rows = self.base + [user(at(16, 43), "This session is being continued from a previous conversation ..."),
                            user(at(16, 44), "<task-notification>done</task-notification>"), user(at(16, 45), "x", isMeta=True),
                            tool_result(at(16, 46)), notice(at(16, 47), "zz", "remove")]
        self.assertEqual(self.state(rows), "fresh")

    def test_a_job_with_no_word_back_for_half_a_day_is_lost_not_pending(self):
        rows = self.base + [bash(at(17, 5), "t2", True)]
        os.utime(self.report, (at(17, 6).timestamp(),) * 2)
        self.transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        self.assertEqual(lrs.report_state(self.report, self.transcript, now=at(17, 5).timestamp() + 13 * 3600), "fresh")

    def test_no_transcript_means_unknown_and_the_caller_keeps_the_old_meaning(self):
        self.assertEqual(lrs.report_state(self.report, Path(self.tmp.name) / "nope.jsonl"), "?")

    def test_the_command_line_prints_the_word(self):
        import subprocess
        self.state(self.base + [user(at(16, 40), "x")])
        out = subprocess.run([sys.executable, "-I", lrs.__file__, str(self.report), str(self.transcript)], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "input")


class TheStatusTextIsRead(unittest.TestCase):
    STATUS = ("tigard-trace       report:stale idle    ✻ Worked for 3m\n    report stale: the lane got input after report.md was written\n"
              "    dir:/x model:sonnet\nother-lane        report:yes  idle    done\n")

    def test_the_parsers_keep_a_stale_lane_live_and_not_done(self):
        live, _dead = osd.parse_lane_status(self.STATUS)
        self.assertEqual(live, [("tigard-trace", "stale", "idle"), ("other-lane", "yes", "idle")])

    def test_the_digest_says_report_stale(self):
        self.assertIn("stale", hd.REPORT_WORD["stale"])
        self.assertNotEqual(hd.REPORT_WORD["stale"], hd.REPORT_WORD["yes"])


if __name__ == "__main__":
    unittest.main()
