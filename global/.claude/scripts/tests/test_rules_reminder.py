"""rules_reminder.py (the event-driven rules hook): end to end through stdin, as Claude Code calls it."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "rules_reminder.py"


class RulesReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {**os.environ, "AFA_STATE_DIR": self.tmp.name, "LANE_RULES_DIR": self.tmp.name}
        self.env.pop("AFA_LANE", None)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, payload, role="lane", **extra):
        r = subprocess.run([sys.executable, str(HOOK), "--role", role], input=json.dumps(payload),
                           capture_output=True, text=True, env={**self.env, **extra}, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else None

    def ok(self, i, sid="s1"):
        return {"session_id": sid, "hook_event_name": "PostToolUse", "tool_name": "Bash",
                "tool_input": {"command": "ls %d" % i}, "tool_response": {}}

    def fail(self, cmd="make test", sid="s2"):
        return {"session_id": sid, "hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_input": {"command": cmd}}

    def test_self_test_passes(self):
        r = subprocess.run([sys.executable, str(HOOK), "--self-test"], capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "self-test ok"), r.stderr)

    def test_every_thirtieth_call_for_a_lane_at_most_twice(self):
        shown = [i for i in range(1, 100) if self.call(self.ok(i))]
        self.assertEqual(shown, [30, 60])

    def test_lane_text_names_the_three_rules(self):
        for i in range(1, 30):
            self.call(self.ok(i))
        text = self.call(self.ok(30))
        self.assertIn("SUSPECTED-INJECTION", text)
        self.assertIn("redact_view.py", text)
        self.assertIn("BLOCKED", text)

    def test_second_failure_of_the_same_command_fires_once(self):
        got = [bool(self.call(self.fail())) for _ in range(5)]
        self.assertEqual(got, [False, True, False, False, False])
        self.assertIsNone(self.call(self.fail("another command")), "a different command's first failure is not a repeat")

    def test_hub_text_and_hub_role_is_silent_inside_a_lane(self):
        self.call(self.fail(), role="hub")
        text = self.call(self.fail(), role="hub")
        self.assertIn("One decision at a time", text)
        self.assertIn("lane_watch.sh", text)
        for _ in range(2):
            self.assertIsNone(self.call(self.fail(sid="s3"), role="hub", AFA_LANE="1"))

    def test_bad_input_never_fails_a_tool_call(self):
        for stdin in ("", "not json", "[]", json.dumps({"session_id": "../x"})):
            r = subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, env=self.env, timeout=20)
            self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_rule_text_comes_from_the_rules_folder_when_present(self):
        (Path(self.tmp.name) / "key-rules-lane.txt").write_text("## head\n- custom rule one\n- custom rule two\n")
        for i in range(1, 30):
            self.call(self.ok(i))
        text = self.call(self.ok(30))
        self.assertIn("custom rule one", text)
        self.assertNotIn("SUSPECTED-INJECTION", text)


if __name__ == "__main__":
    unittest.main()
