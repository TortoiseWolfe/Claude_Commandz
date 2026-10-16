"""openclaw_ask_agents.py: AGENTS.md may only promise what the hub-ask path does. The text edit is pure."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_ask_agents as ag  # noqa: E402

DOC = "# AGENTS\n\nintro\n\n## Card rules\n\n" + ag.OLD_TOP + "\n\n" + ag.OLD_EX + "\n\nrest of the file\n"
NOTLIVE = ag.SECTIONS["notlive"].read_text()
LIVE = ag.SECTIONS["live"].read_text()


class Edit(unittest.TestCase):
    def test_inserts_before_card_rules_and_fixes_the_two_false_promises(self):
        out = ag.apply_section(DOC, NOTLIVE)
        self.assertLess(out.index("hub-ask:begin"), out.index("## Card rules"))
        self.assertNotIn("you will see it appear in the lane list", out)
        self.assertNotIn(ag.OLD_TOP, out)
        self.assertIn("I can't start this myself yet; ask the hub.", out)
        self.assertTrue(out.endswith("rest of the file\n"))

    def test_flipping_replaces_the_block_and_applying_twice_changes_nothing(self):
        a = ag.apply_section(DOC, NOTLIVE)
        b = ag.apply_section(a, LIVE)
        self.assertEqual(b.count("hub-ask:begin"), 1)
        self.assertIn("STATE: LIVE.", b)
        self.assertNotIn("STATE: NOT LIVE", b)
        self.assertEqual(ag.apply_section(b, LIVE), b)
        self.assertEqual(ag.apply_section(b, NOTLIVE), a)

    def test_neither_state_lets_the_model_promise_an_action_it_cannot_take(self):
        for sec in (NOTLIVE, LIVE):
            self.assertIn("You have no tools", sec)
            self.assertNotIn("lane list", sec)
            self.assertNotIn("I will start", sec)
        self.assertNotIn("I've put this on the hub's list", NOTLIVE)
        self.assertIn("I've put this on the hub's list", LIVE)

    def test_the_text_that_goes_live_matches_what_the_poller_does(self):
        # the menu sentences in the live section must be ones parse_ask accepts, and the prefix the one it files
        import openclaw_pickup as pk
        sites = {"site-a": Path("x")}
        self.assertEqual(pk.parse_ask("open the newest site-a model", sites, {}), ("open_model", "site-a"))
        self.assertEqual(pk.parse_ask("status", sites, {}), ("status", None))
        self.assertIn('"open the newest <site> model" or "status"', LIVE)
        self.assertIn('starts with "hub:"', LIVE)
        self.assertTrue(pk.HUB_PREFIX.match("hub: something"))


if __name__ == "__main__":
    unittest.main()
