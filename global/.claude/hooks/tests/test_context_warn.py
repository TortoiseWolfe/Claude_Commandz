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
        # Most tests are about the size tiers, so a fresh running hand-off file is there (issue 227); tests of the no-file warning remove it.
        self.state.mkdir()
        self.handoff = self.state / "hub-handoff-2026-11-05-0900.md"
        self.handoff.write_text("open items\n", encoding="utf-8")

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

    def call_hook(self, *, cwd=None, event="UserPromptSubmit", session=None, extra_env=None, real_defaults=False):
        env = os.environ.copy()
        env.update({
            "AFA_HUB_DIR": str(self.hub),
            "AFA_STATE_DIR": str(self.state),
        })
        for name in ("AFA_HANDOFF_K", "AFA_HANDOFF_START_K", "AFA_HANDOFF_FRESH_K"):
            env.pop(name, None)
        if not real_defaults:
            # The tier logic is tested at the old 100/250/200/330K sizes; the shipped defaults (150K line) have their own test below.
            env.update({"AFA_HANDOFF_K": "330", "AFA_HANDOFF_START_K": "250", "AFA_HANDOFF_FRESH_K": "200"})
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

    def test_line_prints_once_per_prompt_not_after_every_tool_call(self):
        # Issue 224: the line printed after every tool call; every print lands in the hub's own context.
        self.write_transcript((100_000, 0, 0))
        first = self.warning(self.call_hook(event="UserPromptSubmit"))["additionalContext"]
        self.assertEqual(first, "Hub context: 100K of 367K")
        self.assertEqual(self.call_hook(event="PostToolUse"), "")
        self.assertEqual(self.call_hook(event="PostToolUse"), "")
        self.write_transcript((101_000, 0, 0))
        second = self.warning(self.call_hook(event="UserPromptSubmit"))["additionalContext"]
        self.assertEqual(second, "Hub context: 101K of 367K")
        self.assertEqual(self.call_hook(event="PostToolUse"), "")

    def test_first_tool_call_with_no_prompt_event_prints_the_line_once(self):
        self.write_transcript((100_000, 0, 0))
        self.assertEqual(self.warning(self.call_hook(event="PostToolUse"))["additionalContext"], "Hub context: 100K of 367K")
        self.assertEqual(self.call_hook(event="PostToolUse"), "")

    def test_tier_warning_still_arrives_on_a_tool_call_without_the_plain_line_repeating(self):
        self.write_transcript((100_000, 0, 0))
        self.call_hook(event="UserPromptSubmit")
        self.write_transcript((250_000, 0, 0))
        text = self.warning(self.call_hook(event="PostToolUse"))["additionalContext"]
        self.assertIn("start the running hand-off file now", text)
        self.assertEqual(self.call_hook(event="PostToolUse"), "")

    HAND_OFF = "hand off and end, no new work"

    def tool_calls(self, count):
        """Outputs of that many tool calls in a row."""
        return [self.call_hook(event="PostToolUse") for _ in range(count)]

    def test_handoff_warning_arrives_on_the_tool_call_that_crosses_the_line_then_on_every_10th(self):
        # It printed after every tool call past the line, which adds text exactly when the window is fullest.
        self.write_transcript((100_000, 0, 0))
        self.call_hook(event="UserPromptSubmit")
        self.write_transcript((331_000, 0, 0))
        self.assertIn(self.HAND_OFF, self.warning(self.call_hook(event="PostToolUse"))["additionalContext"])
        self.assertEqual(self.tool_calls(9), [""] * 9)
        self.assertIn(self.HAND_OFF, self.warning(self.call_hook(event="PostToolUse"))["additionalContext"])
        self.assertEqual(self.tool_calls(9), [""] * 9)
        self.assertIn(self.HAND_OFF, self.warning(self.call_hook(event="PostToolUse"))["additionalContext"])

    def test_every_prompt_past_the_line_gets_the_handoff_warning_and_restarts_the_count(self):
        self.write_transcript((331_000, 0, 0))
        self.assertIn(self.HAND_OFF, self.warning(self.call_hook(event="UserPromptSubmit"))["additionalContext"])
        self.assertEqual(self.tool_calls(5), [""] * 5)
        self.assertIn(self.HAND_OFF, self.warning(self.call_hook(event="UserPromptSubmit"))["additionalContext"])
        self.assertEqual(self.tool_calls(9), [""] * 9)
        self.assertIn(self.HAND_OFF, self.warning(self.call_hook(event="PostToolUse"))["additionalContext"])

    def test_dropping_below_the_line_clears_the_count_so_the_next_crossing_warns_at_once(self):
        self.write_transcript((331_000, 0, 0))
        self.call_hook(event="UserPromptSubmit")
        self.assertEqual(self.tool_calls(3), [""] * 3)
        self.write_transcript((100_000, 0, 0))
        self.assertEqual(self.call_hook(event="PostToolUse"), "")
        self.write_transcript((331_000, 0, 0))
        self.assertIn(self.HAND_OFF, self.warning(self.call_hook(event="PostToolUse"))["additionalContext"])

    def test_sessions_keep_separate_handoff_counts(self):
        self.write_transcript((331_000, 0, 0))
        self.call_hook(event="UserPromptSubmit", session="a")
        self.assertEqual(self.call_hook(event="PostToolUse", session="a"), "")
        self.assertIn(self.HAND_OFF, self.call_hook(event="PostToolUse", session="b"))

    def test_an_unreadable_count_warns_rather_than_staying_quiet(self):
        self.write_transcript((331_000, 0, 0))
        self.call_hook(event="UserPromptSubmit")
        (self.state / ("context-handoff-calls." + self.session_id)).write_text("not a number", encoding="ascii")
        self.assertIn(self.HAND_OFF, self.warning(self.call_hook(event="PostToolUse"))["additionalContext"])
        self.assertEqual(self.call_hook(event="PostToolUse"), "")

    def test_the_capped_warning_holds_at_the_shipped_150k_line(self):
        self.write_transcript((151_000, 0, 0))
        self.assertIn(self.HAND_OFF, self.warning(self.call_hook(event="UserPromptSubmit", real_defaults=True))["additionalContext"])
        outputs = [self.call_hook(event="PostToolUse", real_defaults=True) for _ in range(10)]
        self.assertEqual(outputs[:9], [""] * 9)
        self.assertIn(self.HAND_OFF, self.warning(outputs[9])["additionalContext"])

    def test_sessions_do_not_share_the_mark(self):
        self.write_transcript((100_000, 0, 0))
        self.call_hook(event="UserPromptSubmit", session="a")
        self.assertEqual(self.call_hook(event="PostToolUse", session="a"), "")
        self.assertIn("Hub context", self.call_hook(event="PostToolUse", session="b"))

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

    def test_shipped_defaults_put_the_hand_off_line_at_150k(self):
        # Jonathan, 11 Nov: hand off at about 150K, at the first natural break (was 330K).
        self.handoff.unlink()
        self.write_transcript((99_000, 0, 0))
        self.assertEqual(self.warning(self.call_hook(real_defaults=True))["additionalContext"], "Hub context: 99K of 367K")
        self.write_transcript((100_000, 0, 0))
        out = self.warning(self.call_hook(real_defaults=True, session="s100"))["additionalContext"]
        self.assertIn("no hub-handoff", out)
        self.write_transcript((125_000, 0, 0))
        out = self.warning(self.call_hook(real_defaults=True, session="s125"))["additionalContext"]
        self.assertIn("start the running hand-off file now", out)
        self.write_transcript((130_000, 0, 0))
        out = self.warning(self.call_hook(real_defaults=True, session="s130"))["additionalContext"]
        self.assertIn("Hand-off line is 150K: start wrapping up", out)
        self.write_transcript((150_000, 0, 0))
        out = self.warning(self.call_hook(real_defaults=True, session="s150"))["additionalContext"]
        self.assertIn("hand off and end, no new work", out)

    def test_bad_setting_falls_back_to_defaults(self):
        self.write_transcript((330_000, 0, 0))
        out = self.warning(self.call_hook(extra_env={"AFA_HANDOFF_K": "lots"}))["additionalContext"]
        self.assertIn("hand off and end, no new work", out)

    def test_200k_with_no_fresh_handoff_file_warns(self):
        self.handoff.unlink()
        self.write_transcript((199_000, 0, 0))
        self.assertEqual(self.warning(self.call_hook())["additionalContext"], "Hub context: 199K of 367K")
        self.write_transcript((200_000, 0, 0))
        text = self.warning(self.call_hook())["additionalContext"]
        self.assertIn("no hub-handoff-*.md newer than 30 minutes", text)
        self.assertIn("start this session's running hand-off file now", text)

    def test_200k_with_a_fresh_handoff_file_is_silent(self):
        self.write_transcript((220_000, 0, 0))
        self.assertEqual(self.warning(self.call_hook())["additionalContext"], "Hub context: 220K of 367K")

    def test_a_handoff_file_older_than_30_minutes_does_not_count(self):
        old = self.handoff.stat().st_mtime - 31 * 60
        os.utime(self.handoff, (old, old))
        self.write_transcript((220_000, 0, 0))
        self.assertIn("no hub-handoff-*.md newer than 30 minutes", self.warning(self.call_hook())["additionalContext"])
        recent = self.handoff.stat().st_mtime + 40 * 60
        os.utime(self.handoff, (recent, recent))
        self.state.joinpath("context-nohandoff." + self.session_id).unlink()
        self.assertNotIn("no hub-handoff", self.warning(self.call_hook())["additionalContext"])

    def test_the_no_file_warning_repeats_at_most_every_10_minutes(self):
        self.handoff.unlink()
        self.write_transcript((220_000, 0, 0))
        self.assertIn("no hub-handoff", self.warning(self.call_hook())["additionalContext"])
        self.assertNotIn("no hub-handoff", self.warning(self.call_hook())["additionalContext"])
        mark = self.state / ("context-nohandoff." + self.session_id)
        earlier = mark.stat().st_mtime - 11 * 60
        os.utime(mark, (earlier, earlier))
        self.assertIn("no hub-handoff", self.warning(self.call_hook())["additionalContext"])

    def test_the_no_file_warning_rides_a_tool_call_even_when_the_line_was_shown(self):
        self.handoff.unlink()
        self.write_transcript((220_000, 0, 0))
        self.call_hook(event="UserPromptSubmit")
        # the prompt event used up the warning; remove its rate mark so the tool call is due again
        (self.state / ("context-nohandoff." + self.session_id)).unlink()
        out = self.warning(self.call_hook(event="PostToolUse"))["additionalContext"]
        self.assertIn("no hub-handoff", out)

    def test_AFA_HANDOFF_FRESH_K_setting_and_330k_tier_unchanged(self):
        self.handoff.unlink()
        self.write_transcript((100_000, 0, 0))
        out = self.warning(self.call_hook(extra_env={"AFA_HANDOFF_FRESH_K": "100"}))["additionalContext"]
        self.assertIn("no hub-handoff", out)
        self.write_transcript((331_000, 0, 0))
        out = self.warning(self.call_hook(session="late"))["additionalContext"]
        self.assertIn("hand off and end, no new work", out)
        self.assertNotIn("no hub-handoff", out)

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
