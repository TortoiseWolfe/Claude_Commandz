"""ask-card-guard.py: a decision card needs one question, Hold first, named references with links,
and a live check of mail or pull-request state. Run as its own process: JSON on stdin, exit 2 with
the reasons on stderr to refuse. Transcripts are temporary files; all text is made up."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "ask-card-guard.py"
GMAIL = "mcp__claude_ai_Gmail__"
LONG_Q = "Should we go ahead and apply the shop's price list change to the public site today or wait?"
URL = "https://example.com/shop/pull/12"


def card(question=LONG_Q, labels=("Hold, ask me again", "Apply it (Recommended)", "Skip it"), descs=None):
    descs = descs or [""] * len(labels)
    return {"question": question, "header": "Decision", "multiSelect": False,
            "options": [{"label": l, "description": d} for l, d in zip(labels, descs)]}


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool_use(name, inp):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": name, "input": inp}]}}


def tool_result():
    return {"type": "user", "message": {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}


GMAIL_CALL = tool_use(GMAIL + "search_threads", {"query": "in:sent"})
PR_CALL = tool_use("Bash", {"command": "gh pr view 12 --json state"})


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def transcript(self, entries):
        fd, path = tempfile.mkstemp(suffix=".jsonl", dir=self.tmp.name)
        with os.fdopen(fd, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return path

    def run_hook(self, questions=None, tool="AskUserQuestion", transcript=None, env=None, raw=None):
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool,
                   "tool_input": {"questions": [card()] if questions is None else questions}}
        if transcript:
            payload["transcript_path"] = transcript
        e = dict(os.environ)
        e.pop("ASK_CARD_GUARD", None)
        e.update(env or {})
        r = subprocess.run([sys.executable, str(HOOK)], input=raw if raw is not None else json.dumps(payload),
                           capture_output=True, text=True, env=e)
        return r.returncode, r.stderr

    def refused(self, *args, **kw):
        code, err = self.run_hook(*args, **kw)
        self.assertEqual(code, 2, err)
        return err

    def allowed(self, *args, **kw):
        code, err = self.run_hook(*args, **kw)
        self.assertEqual(code, 0, err)


class Checks1to3(Base):
    def test_good_card_passes(self):
        self.allowed()

    def test_two_questions_refused_with_count(self):
        self.assertIn("holds 2 questions", self.refused([card(), card()]))

    def test_no_questions_refused(self):
        self.assertIn("holds 0 questions", self.refused([]))

    def test_recommended_first_refused(self):
        err = self.refused([card(labels=("Apply it", "Hold, ask me again"))])
        self.assertIn("Option 1", err)

    def test_recommended_marker_on_option_one_refused(self):
        self.refused([card(labels=("Hold, ask me again (Recommended)", "Apply it"))])

    def test_lowercase_hold_accepted(self):
        self.allowed([card(labels=("hold, ask me again", "Apply it (Recommended)"))])

    def test_empty_options_refused(self):
        self.assertIn("Option 1", self.refused([{"question": LONG_Q, "options": []}]))
        self.assertIn("Option 1", self.refused([{"question": LONG_Q}]))

    def test_odd_shapes_do_not_crash(self):
        self.refused([{"question": LONG_Q, "options": "nope"}])
        code, _ = self.run_hook(raw=json.dumps({"tool_name": "AskUserQuestion", "tool_input": {"questions": "x"}}))
        self.assertEqual(code, 2)

    def test_reference_without_link_refused(self):
        err = self.refused([card(question="Should we merge #12 now, given the shop's price list change is ready?")])
        self.assertIn("https://", err)

    def test_reference_with_link_but_short_question_refused(self):
        err = self.refused([card(question="Do we want #12?", descs=["", URL, ""])])
        self.assertIn("only 4 words", err)

    def test_reference_with_link_and_full_question_passes(self):
        q = "Should the price list change in #12, which updates the shop's weekly prices, go live today?"
        self.allowed([card(question=q, descs=["", URL, ""])])

    def test_word_reference_forms(self):
        for ref in ("PR 12", "pull request #12", "issue 7", "card 3", "ticket #4"):
            with self.subTest(ref=ref):
                self.refused([card(question="Do we want %s?" % ref)])

    def test_hex_colour_and_entity_are_not_references(self):
        self.allowed([card(question="Is the colour #ff0000 or foo#12 right, or &#39;this&#39; one for the banner?")])
        self.allowed([card(question="Is it the shop&#39;s sign or the &#39;1 price list&#39; heading we use today?")])

    def test_several_failures_in_one_message(self):
        err = self.refused([card(question="Do we want #12?", labels=("Apply it", "Hold")), card()])
        self.assertIn("holds 2 questions", err)
        self.assertIn("Option 1", err)
        self.assertIn("no link", err)
        self.assertIn("only 4 words", err)
        self.assertEqual(err.count("\n- "), 4)


class Check4(Base):
    MAIL_Q = "Should we send the shop's price list change draft to the supplier today or wait for them?"
    PR_Q = "Should we merge the shop's price list change into the main branch today or wait for review?"

    def test_mail_cue_without_lookup_refused(self):
        t = self.transcript([user("go"), tool_use("Bash", {"command": "ls"}), tool_result()])
        self.assertIn("no Gmail lookup", self.refused([card(question=self.MAIL_Q)], transcript=t))

    def test_mail_cue_with_lookup_this_turn_passes(self):
        t = self.transcript([user("go"), GMAIL_CALL, tool_result()])
        self.allowed([card(question=self.MAIL_Q)], transcript=t)

    def test_lookup_before_owner_message_does_not_count(self):
        t = self.transcript([user("first"), GMAIL_CALL, tool_result(), user("second message")])
        self.refused([card(question=self.MAIL_Q)], transcript=t)

    def test_pr_cue_without_gh_call_refused(self):
        t = self.transcript([user("go"), tool_use("Bash", {"command": "ls"}), tool_result()])
        self.assertIn("gh pr view <n> --json state", self.refused([card(question=self.PR_Q)], transcript=t))

    def test_pr_cue_with_gh_call_passes(self):
        t = self.transcript([user("go"), PR_CALL, tool_result()])
        self.allowed([card(question=self.PR_Q)], transcript=t)

    def test_gmail_lookup_does_not_satisfy_pr_cue(self):
        t = self.transcript([user("go"), GMAIL_CALL, tool_result()])
        self.refused([card(question=self.PR_Q)], transcript=t)

    def test_no_state_words_needs_no_transcript_calls(self):
        t = self.transcript([user("go")])
        self.allowed(transcript=t)

    def test_no_transcript_skips_check_4_only(self):
        self.allowed([card(question=self.MAIL_Q)])
        self.refused([card(question=self.MAIL_Q, labels=("Apply it", "Hold"))])

    def test_missing_transcript_file_skips_check_4(self):
        self.allowed([card(question=self.MAIL_Q)], transcript=str(Path(self.tmp.name) / "nope.jsonl"))


class Allowed(Base):
    def test_kill_switch(self):
        self.allowed([card(), card()], env={"ASK_CARD_GUARD": "off"})

    def test_other_tool(self):
        self.allowed([card(), card()], tool="Bash")

    def test_malformed_stdin(self):
        self.allowed(raw="not json")
        self.allowed(raw="")
        self.allowed(raw="[1, 2]")


if __name__ == "__main__":
    unittest.main()
