import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HOOK = Path(__file__).resolve().parents[1] / "context-warn.py"


class ContextWarnTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.hub = self.root / "hub"
        self.hub.mkdir()
        self.state = self.root / "state"
        self.transcript = self.root / "transcript.jsonl"
        self.session_id = "fixture-session"

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_transcript(self, tokens=None, *, corrupt_last=False, empty=False, synthetic_last=False):
        rows = []
        if tokens is not None:
            rows.append({
                "type": "assistant",
                "message": {"usage": {
                    "input_tokens": tokens[0],
                    "cache_read_input_tokens": tokens[1],
                    "cache_creation_input_tokens": tokens[2],
                }},
            })
        if synthetic_last:
            rows.append({
                "type": "assistant",
                "message": {
                    "model": "<synthetic>",
                    "usage": {
                        "input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            })
        if corrupt_last:
            rows.append('{"type":')
        contents = "\n".join(row if isinstance(row, str) else json.dumps(row) for row in rows)
        if contents and not empty:
            contents += "\n"
        self.transcript.write_text(contents, encoding="utf-8")

    def call_hook(self, *, cwd=None, event="PostToolUse", session=None):
        env = os.environ.copy()
        env.update({
            "AFA_HUB_DIR": str(self.hub),
            "AFA_STATE_DIR": str(self.state),
            "AFA_COMPACT_WINDOW": "400000",
        })
        payload = {
            "session_id": session or self.session_id,
            "transcript_path": str(self.transcript),
            "cwd": str(cwd or self.hub),
            "hook_event_name": event,
        }
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        return result.stdout

    def warning(self, output):
        return json.loads(output)["hookSpecificOutput"]

    def test_below_50_percent_is_silent(self):
        self.write_transcript((199_999, 0, 0))
        self.assertEqual(self.call_hook(), "")

    def test_warns_at_50_percent_only_once_even_at_52(self):
        self.write_transcript((200_000, 0, 0))
        output = self.call_hook()
        self.assertIn("Context is at 50%", self.warning(output)["additionalContext"])
        self.write_transcript((208_000, 0, 0))
        self.assertEqual(self.call_hook(), "")

    def test_synthetic_last_entry_uses_prior_usage_without_resetting_stamp(self):
        self.write_transcript((200_000, 0, 0))
        output = self.call_hook()
        self.assertIn("Context is at 50%", self.warning(output)["additionalContext"])

        self.write_transcript((200_000, 0, 0), synthetic_last=True)
        self.assertEqual(self.call_hook(), "")
        self.assertEqual((self.state / ("context-warn." + self.session_id)).read_text(), "50")

    def test_warns_at_60_percent(self):
        self.write_transcript((200_000, 0, 40_000))
        output = self.call_hook()
        self.assertIn("Context is at 60%", self.warning(output)["additionalContext"])

    def test_70_percent_warning_is_overdue(self):
        self.write_transcript((200_000, 80_000, 0))
        output = self.call_hook()
        self.assertTrue(self.warning(output)["additionalContext"].startswith("Overdue: "))

    def test_other_cwd_is_silent(self):
        self.write_transcript((300_000, 0, 0))
        self.assertEqual(self.call_hook(cwd=self.root / "elsewhere"), "")

    def test_missing_transcript_is_silent(self):
        self.assertEqual(self.call_hook(), "")

    def test_empty_transcript_is_silent(self):
        self.transcript.write_text("", encoding="utf-8")
        self.assertEqual(self.call_hook(), "")

    def test_corrupt_last_line_falls_back_to_valid_usage(self):
        self.write_transcript((200_000, 0, 0), corrupt_last=True)
        output = self.call_hook()
        self.assertIn("Context is at 50%", self.warning(output)["additionalContext"])

    def test_below_40_percent_resets_warning_stamp(self):
        self.write_transcript((200_000, 0, 0))
        self.call_hook()
        self.write_transcript((159_999, 0, 0))
        self.assertEqual(self.call_hook(), "")
        self.write_transcript((200_000, 0, 0))
        output = self.call_hook()
        self.assertIn("Context is at 50%", self.warning(output)["additionalContext"])

    def test_hook_event_name_is_echoed(self):
        self.write_transcript((200_000, 0, 0))
        output = self.call_hook(event="UserPromptSubmit")
        self.assertEqual(self.warning(output)["hookEventName"], "UserPromptSubmit")


if __name__ == "__main__":
    unittest.main()
