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

    def test_below_250k_shows_per_turn_line(self):
        self.write_transcript((249_000, 0, 0))
        output = self.call_hook()
        self.assertEqual(self.warning(output)["additionalContext"], "Hub context: 249K of 367K")

    def test_250k_tier_shows_once(self):
        self.write_transcript((250_000, 0, 0))
        output = self.call_hook()
        self.assertIn("Hub context 250K: start the running hand-off file now", self.warning(output)["additionalContext"])
        self.write_transcript((251_000, 0, 0))
        output = self.call_hook()
        self.assertEqual(self.warning(output)["additionalContext"], "Hub context: 251K of 367K")

    def test_310k_tier_shows_once(self):
        # Move past 250K first to "consume" that warning
        self.write_transcript((250_000, 0, 0))
        self.call_hook()
        # Now hit 310K
        self.write_transcript((310_000, 0, 0))
        output = self.call_hook()
        self.assertIn("Hand-off line is 330K: start wrapping up", self.warning(output)["additionalContext"])
        self.write_transcript((311_000, 0, 0))
        output = self.call_hook()
        self.assertEqual(self.warning(output)["additionalContext"], "Hub context: 311K of 367K")

    def test_330k_tier_repeats_every_turn(self):
        self.write_transcript((331_000, 0, 0))
        output1 = self.call_hook()
        self.assertIn("hand off and end, no new work", self.warning(output1)["additionalContext"])
        self.assertIn("touch ~/.claude/state/hub-handed-off.fixture-session", self.warning(output1)["additionalContext"])

        output2 = self.call_hook()
        self.assertIn("hand off and end, no new work", self.warning(output2)["additionalContext"])

    def test_every_size_gets_the_per_turn_line_and_tiers_fire_as_specified(self):
        seen = []
        for tokens in (249_000, 250_000, 310_000, 331_000, 331_000):
            self.write_transcript((tokens, 0, 0))
            text = self.warning(self.call_hook())["additionalContext"]
            self.assertTrue(text.startswith(f"Hub context: {tokens // 1000}K of 367K"), text)
            seen.append(text)
        self.assertNotIn("\n", seen[0])
        self.assertIn("start the running hand-off file now", seen[1])
        self.assertIn("start wrapping up", seen[2])
        self.assertIn("hand off and end", seen[3])
        self.assertIn("hand off and end", seen[4])

    def test_AFA_HANDOFF_START_K_setting(self):
        self.write_transcript((200_000, 0, 0))
        output = self.call_hook(extra_env={"AFA_HANDOFF_START_K": "200"})
        self.assertIn("start the running hand-off file now", self.warning(output)["additionalContext"])

    def test_below_lowest_point_resets_warning_stamp(self):
        self.write_transcript((250_000, 0, 0))
        self.call_hook()
        self.write_transcript((240_000, 0, 0))
        self.call_hook()
        self.write_transcript((250_000, 0, 0))
        output = self.call_hook()
        self.assertIn("start the running hand-off file now", self.warning(output)["additionalContext"])

    def test_synthetic_last_entry_uses_prior_usage_without_resetting_stamp(self):
        self.write_transcript((250_000, 0, 0))
        output = self.call_hook()
        self.assertIn("Hub context 250K", self.warning(output)["additionalContext"])
        self.write_transcript((250_000, 0, 0), synthetic_last=True)
        self.assertEqual(self.warning(self.call_hook())["additionalContext"], "Hub context: 250K of 367K")
        self.assertEqual((self.state / ("context-warn." + self.session_id)).read_text(), "250")

    def test_hand_off_warning_at_330k(self):
        self.write_transcript((330_000, 0, 0))
        output = self.call_hook()
        self.assertIn("hand off and end, no new work", self.warning(output)["additionalContext"])

    def test_setting_moves_points(self):
        self.write_transcript((100_000, 0, 0))
        output = self.call_hook(extra_env={"AFA_HANDOFF_K": "100"})
        self.assertIn("hand off and end, no new work", self.warning(output)["additionalContext"])

    def test_bad_setting_falls_back_to_defaults(self):
        self.write_transcript((330_000, 0, 0))
        out = self.warning(self.call_hook(extra_env={"AFA_HANDOFF_K": "lots"}))["additionalContext"]
        self.assertIn("hand off and end, no new work", out)

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
        self.write_transcript((250_000, 0, 0), corrupt_last=True)
        output = self.call_hook()
        self.assertIn("Hub context 250K", self.warning(output)["additionalContext"])

    def test_hook_event_name_is_echoed(self):
        self.write_transcript((150_000, 0, 0))
        output = self.call_hook(event="UserPromptSubmit")
        self.assertEqual(self.warning(output)["hookEventName"], "UserPromptSubmit")


if __name__ == "__main__":
    unittest.main()
