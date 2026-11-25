import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HOOK = Path(__file__).resolve().parents[1] / "bare-reference.py"
_spec = importlib.util.spec_from_file_location("bare_reference", HOOK)
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

URL = "https://example.org/shop/pull/274"
GOOD = "**The change that moves the shop onto one price list** (pull request 274)"


class BareReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def transcript(self, reply):
        rows = [
            {"type": "user", "message": {"role": "user", "content": "show me the status"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}},
        ]
        path = self.root / "t.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return path

    def run_hook(self, reply=None, *, stdin=None, env_extra=None, **payload):
        env = {k: v for k, v in os.environ.items() if k not in ("BARE_REFERENCE", "AFA_LANE_UNATTENDED")}
        env.update(env_extra or {})
        if stdin is None:
            if reply is not None:
                payload.setdefault("transcript_path", str(self.transcript(reply)))
            stdin = json.dumps(payload)
        result = subprocess.run([sys.executable, str(HOOK)], input=stdin, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0)
        return result.stdout

    def blocked(self, reply, **kw):
        out = self.run_hook(reply, **kw)
        if not out:
            return None
        data = json.loads(out)
        self.assertEqual(data["decision"], "block")
        return data["reason"]

    def test_reply_without_references_passes(self):
        self.assertEqual(self.run_hook("All done. The shop now uses one price list."), "")

    def test_bare_pr_blocks_and_quotes_the_line(self):
        reason = self.blocked("PR 142")
        self.assertIn("names 1 item(s) by number", reason)
        self.assertIn("PR 142", reason)
        self.assertIn("full https:// address", reason)

    def test_words_and_address_on_same_line_pass(self):
        self.assertIsNone(self.blocked(GOOD + " " + URL))

    def test_address_on_next_line_passes(self):
        self.assertIsNone(self.blocked(GOOD + "\n" + URL))

    def test_address_two_lines_down_passes(self):
        self.assertIsNone(self.blocked(GOOD + "\nsome words\n" + URL))

    def test_address_three_lines_down_blocks(self):
        self.assertIsNotNone(self.blocked(GOOD + "\none\ntwo\n" + URL))

    def test_address_only_in_fence_blocks(self):
        self.assertIsNotNone(self.blocked(GOOD + "\n```\n" + URL + "\n```"))

    def test_address_only_indented_blocks(self):
        self.assertIsNotNone(self.blocked(GOOD + "\n    " + URL))
        self.assertIsNotNone(self.blocked(GOOD + "\n\t" + URL))

    def test_reference_in_fence_or_indent_is_ignored(self):
        self.assertIsNone(self.blocked("Notes follow.\n```\nPR 142\n#274\n```\n    card 166\n\tissue #91"))

    def test_other_forms_are_found(self):
        for line in ["#274", "issue #91", "card 166", "Ticket 12", "pull request 5"]:
            with self.subTest(line=line):
                self.assertEqual(br.scan_reply(line), [line])

    def test_non_references_are_ignored(self):
        for line in ["It&#39;s fine", "the colour #ff0000 is red", "## Heading", "# 3 things to know", "abc#12 here", "issue of the day"]:
            with self.subTest(line=line):
                self.assertEqual(br.scan_reply(line), [])

    def test_short_line_blocks_for_lack_of_words(self):
        reason = self.blocked("SpokeToWork pull request 142\n" + URL)
        self.assertIn("SpokeToWork pull request 142", reason)

    def test_stop_hook_active_passes(self):
        self.assertEqual(self.run_hook("PR 142", stop_hook_active=True), "")

    def test_kill_switch_passes(self):
        self.assertEqual(self.run_hook("PR 142", env_extra={"BARE_REFERENCE": "off"}), "")

    def test_unattended_lane_passes(self):
        self.assertEqual(self.run_hook("PR 142", env_extra={"AFA_LANE_UNATTENDED": "1"}), "")

    def test_missing_transcript_passes(self):
        self.assertEqual(self.run_hook(transcript_path=str(self.root / "nope.jsonl")), "")

    def test_malformed_stdin_passes(self):
        self.assertEqual(self.run_hook(stdin="not json"), "")

    def test_only_four_offenders_are_shown(self):
        reply = "\n".join("PR %d" % n for n in range(101, 107))
        reason = self.blocked(reply)
        self.assertIn("names 6 item(s)", reason)
        self.assertIn("PR 104", reason)
        self.assertNotIn("PR 105", reason)

    def test_long_line_is_cut_to_120(self):
        reason = self.blocked("PR 142 " + "x" * 300)
        shown = [ln for ln in reason.split("\n") if ln.startswith("  PR 142")]
        self.assertEqual(len(shown[0].strip()), 120)


if __name__ == "__main__":
    unittest.main()
