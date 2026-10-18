import importlib.util
from pathlib import Path
import unittest

SRC = Path(__file__).resolve().parents[1] / "unverified-pending.py"
spec = importlib.util.spec_from_file_location("unverified_pending", SRC)
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)

GMAIL = "mcp__claude_ai_Gmail__"
OCT18 = ("Your one step is still the Gail reply in your drafts. "
         "After that, the next card is ctx-frontload...")
NOTES_CALL = GMAIL + 'list_drafts {"query": "subject:\\"MUSE>CC\\""}'


class CueTests(unittest.TestCase):
    def test_18_oct_line_with_only_an_agent_notes_lookup_is_flagged(self):
        self.assertTrue(up.scan(OCT18, [NOTES_CALL]))

    def test_18_oct_line_with_no_gmail_call_is_flagged(self):
        self.assertTrue(up.scan(OCT18, []))

    def test_other_ways_of_pointing_at_a_draft_are_flagged(self):
        for line in [
            "Gail's reply is waiting in drafts.",
            "It's in your drafts, read it, then send it.",
            "Your one step: the Greg email in Gmail.",
            "Still the Gail reply, then the card after.",
            "Read it, then send it.",
        ]:
            with self.subTest(line=line):
                self.assertTrue(up.scan(line, []))

    def test_a_line_that_only_reports_the_draft_was_sent_is_quiet(self):
        for line in [
            "Gail's reply went out at 1:25 PM.",
            "Gail's reply was sent at 1:25 PM, so it is no longer in your drafts.",
            "The reply in your drafts folder has been sent already.",
        ]:
            with self.subTest(line=line):
                self.assertEqual(up.scan(line, []), [])

    def test_a_draft_that_is_hatchs_note_is_quiet(self):
        for line in [
            "Hatch's note r-4178758221985557888 is in your drafts, nothing for you to do.",
            "The [CC>MUSE] note waits in drafts for Hatch to read.",
            "Muse left a note in drafts about the Twitch setup.",
        ]:
            with self.subTest(line=line):
                self.assertEqual(up.scan(line, []), [])

    def test_plain_lines_stay_quiet(self):
        for line in ["The hook now flags more phrasings.", "Drafts are only ever created, never sent, by me."]:
            with self.subTest(line=line):
                self.assertEqual(up.scan(line, []), [])


class CheckTests(unittest.TestCase):
    def flagged(self, *calls):
        return bool(up.scan("1. **Send Gail's email:** draft r8399774348393883238.", list(calls)))

    def test_agent_note_lookups_do_not_count(self):
        for q in ['subject:\\"MUSE>CC\\"', 'subject:\\"CC>MUSE\\" in:drafts', 'subject:NCC-74656',
                  'subject:\\"[NX-01>NCC-74656]\\"', 'in:drafts subject:\\"MUSE>CC\\" newer_than:2d']:
            for op in ("list_drafts", "search_threads"):
                with self.subTest(op=op, q=q):
                    self.assertTrue(self.flagged(GMAIL + op + ' {"query": "%s"}' % q))

    def test_lookups_that_could_see_the_draft_count(self):
        for call in [
            GMAIL + 'search_threads {"query": "in:sent to:gail@example.org"}',
            GMAIL + 'search_threads {"query": "to:gail@example.org newer_than:1d"}',
            GMAIL + 'list_drafts {"query": "gail"}',
            GMAIL + 'list_drafts {}',
            GMAIL + 'get_draft {"draftId": "r8399774348393883238"}',
            GMAIL + 'get_thread {"threadId": "abc"}',
        ]:
            with self.subTest(call=call):
                self.assertFalse(self.flagged(call))

    def test_a_notes_lookup_next_to_a_real_one_still_counts(self):
        self.assertFalse(self.flagged(NOTES_CALL, GMAIL + 'search_threads {"query": "in:sent"}'))

    def test_a_shell_command_that_merely_names_a_gmail_op_does_not_count(self):
        self.assertTrue(self.flagged('Bash {"command": "grep list_drafts hook.py"}'))


if __name__ == "__main__":
    unittest.main()
