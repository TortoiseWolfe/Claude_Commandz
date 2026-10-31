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

    def call_hook(self, *, cwd=None, event="PostToolUse", session=None, extra_env=None):
        env = os.environ.copy()
        env.update({
            "AFA_HUB_DIR": str(self.hub),
            "AFA_STATE_DIR": str(self.state),
        })
        env.pop("AFA_HANDOFF_K", None)
        env.update(extra_env or {})
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

    def test_below_early_point_is_silent(self):
        self.write_transcript((79_999, 0, 0))
        self.assertEqual(self.call_hook(), "")

    def test_early_warning_at_80k_only_once(self):
        self.write_transcript((80_000, 0, 0))
        output = self.call_hook()
        self.assertIn("Context is at 80K tokens. Hand-off line is 100K", self.warning(output)["additionalContext"])
        self.write_transcript((90_000, 0, 0))
        self.assertEqual(self.call_hook(), "")

    def test_synthetic_last_entry_uses_prior_usage_without_resetting_stamp(self):
        self.write_transcript((80_000, 0, 0))
        output = self.call_hook()
        self.assertIn("Context is at 80K", self.warning(output)["additionalContext"])
        self.write_transcript((80_000, 0, 0), synthetic_last=True)
        self.assertEqual(self.call_hook(), "")
        self.assertEqual((self.state / ("context-warn." + self.session_id)).read_text(), "80")

    def test_hand_off_warning_at_100k(self):
        self.write_transcript((60_000, 0, 40_000))
        output = self.call_hook()
        self.assertIn("past the 100K hand-off line", self.warning(output)["additionalContext"])

    def test_setting_moves_both_points(self):
        self.write_transcript((79_999, 0, 0))
        self.assertEqual(self.call_hook(extra_env={"AFA_HANDOFF_K": "100"}), "")
        self.write_transcript((80_000, 0, 0))
        out = self.warning(self.call_hook(extra_env={"AFA_HANDOFF_K": "100"}))["additionalContext"]
        self.assertIn("Context is at 80K tokens. Hand-off line is 100K", out)
        self.write_transcript((100_000, 0, 0))
        out = self.warning(self.call_hook(extra_env={"AFA_HANDOFF_K": "100"}))["additionalContext"]
        self.assertIn("past the 100K hand-off line", out)

    def test_bad_setting_falls_back_to_100(self):
        self.write_transcript((100_000, 0, 0))
        out = self.warning(self.call_hook(extra_env={"AFA_HANDOFF_K": "lots"}))["additionalContext"]
        self.assertIn("past the 100K hand-off line", out)

    def test_size_mode_on_missing_file_exits_1_quietly(self):
        result = subprocess.run([sys.executable, str(HOOK), "--size", str(self.root / "nope.jsonl")],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)

    def test_other_cwd_is_silent(self):
        self.write_transcript((300_000, 0, 0))
        self.assertEqual(self.call_hook(cwd=self.root / "elsewhere"), "")

    def test_missing_transcript_is_silent(self):
        self.assertEqual(self.call_hook(), "")

    def test_empty_transcript_is_silent(self):
        self.transcript.write_text("", encoding="utf-8")
        self.assertEqual(self.call_hook(), "")

    def test_corrupt_last_line_falls_back_to_valid_usage(self):
        self.write_transcript((80_000, 0, 0), corrupt_last=True)
        output = self.call_hook()
        self.assertIn("Context is at 80K", self.warning(output)["additionalContext"])

    def test_below_early_point_resets_warning_stamp(self):
        self.write_transcript((80_000, 0, 0))
        self.call_hook()
        self.write_transcript((70_000, 0, 0))
        self.assertEqual(self.call_hook(), "")
        self.write_transcript((80_000, 0, 0))
        output = self.call_hook()
        self.assertIn("Context is at 80K", self.warning(output)["additionalContext"])

    def test_hook_event_name_is_echoed(self):
        self.write_transcript((150_000, 0, 0))
        output = self.call_hook(event="UserPromptSubmit")
        self.assertEqual(self.warning(output)["hookEventName"], "UserPromptSubmit")


if __name__ == "__main__":
    unittest.main()
