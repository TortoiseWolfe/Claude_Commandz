"""openclaw_ask_agents.py: AGENTS.md may only promise what the hub-ask path does. The text edit is pure."""

import contextlib
import io
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
        self.assertIn("You have no tools", NOTLIVE)
        self.assertNotIn("You have no tools", LIVE)
        for sec in (NOTLIVE, LIVE):
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
        for form in ('"open the newest <site> model"', '"status"', '"start <repo>: <task>"', '"reply <lane>: <words>"', '"close <lane>"'):
            self.assertIn(form, LIVE)
        import openclaw_dispatch as dp
        self.assertEqual(dp.parse("start site-a: do it"), ("start", "site-a", "do it"))
        self.assertEqual(dp.parse("reply oc-x-1: more"), ("reply", "oc-x-1", "more"))
        self.assertEqual(dp.parse("close oc-x-1"), ("close", "oc-x-1"))
        self.assertIn("Main session", LIVE)
        # the marker line the poller looks for, in the format parse_menu takes after it; no yes anywhere
        self.assertIn("Sending to the hub: <one menu sentence>", LIVE)
        self.assertEqual(len(pk.MARKER_ANY.findall("Sending to the hub: <one menu sentence>")), 1)
        self.assertEqual(pk.MARKER_LINE.fullmatch("Sending to the hub: status").group("job"), "status")
        self.assertNotIn("Say yes", LIVE)
        self.assertNotIn("he says yes", LIVE.lower())
        self.assertIn("I don't know yet; the [hub] line will say.", LIVE)
        self.assertIn("Lanes close themselves", LIVE)
        self.assertIn("newest \"[hub]\" line about that job", LIVE)
        self.assertIn("Never offer; do it.", LIVE)
        self.assertIn("A failed job he asked for gets retried by you, not offered.", LIVE)
        self.assertIn("retry", LIVE)
        self.assertIn("If you want, I can dispatch", LIVE)
        self.assertIn('"workspace"', LIVE)
        self.assertNotIn("type start", LIVE)
        self.assertNotIn("--session-key", LIVE)
        self.assertIn('starts with "hub:"', LIVE)
        self.assertTrue(pk.HUB_PREFIX.match("hub: something"))


class Refusals(unittest.TestCase):
    def test_a_held_or_failed_job_is_answered_by_quoting_the_newest_hub_line_word_for_word(self):
        self.assertIn("A held or failed job is answered with the newest [hub] line, word for word.", LIVE)
        self.assertIn("into your answer exactly as written, in quotation marks", LIVE)
        self.assertIn("Never paraphrase it", LIVE)
        self.assertIn("never name a cause the line does not name", LIVE)
        self.assertIn("not a Gemini, API or usage limit", LIVE)
        self.assertNotIn("A held or failed job", NOTLIVE)

    def test_the_text_the_dispatcher_posts_for_a_hold_is_what_the_rule_tells_him_to_quote(self):
        import openclaw_dispatch as dp
        for held in (dp.held_day_text(1_800_000_000), dp.held_running_text(3)):
            self.assertTrue(held.startswith("Held by the lane dispatcher's own safety cap:"))
            self.assertIn("openclaw_dispatch.py MAX_", held)
            self.assertTrue(held.endswith("This is not a Gemini or API limit."))

    def test_the_live_text_stays_under_the_agents_file_cap(self):
        self.assertLess(len(LIVE), 8000)


class FrontdeskSeven(unittest.TestCase):
    """frontdesk-7: the receptionist lists, names and stops jobs through the hub, never from memory."""
    def test_the_menu_lists_lanes_and_stop(self):
        self.assertIn('- "lanes" lists the open workers', LIVE)
        self.assertIn('- "stop" stops the newest worker he started.', LIVE)

    def test_whats_running_is_answered_by_the_lanes_marker_and_quoted_word_for_word(self):
        self.assertIn('send "Sending to the hub: lanes"', LIVE)
        self.assertIn('quote the "[hub]" answer word for word', LIVE)
        self.assertIn("never from memory or from the status page", LIVE)
        for ask in ("what's running?", "what lanes are open?"):
            self.assertIn(ask, LIVE)

    def test_a_plain_stop_is_sent_unless_he_names_a_lane(self):
        self.assertIn('names no worker, the line is "Sending to the hub: stop"', LIVE)
        self.assertIn('When he names one, it is "close <lane>"', LIVE)
        self.assertNotIn("the hub handles it: do not write", LIVE)  # the old rule: no marker for a stop
        self.assertNotIn("he can say \"stop\" to cancel a start", LIVE)

    def test_reply_and_close_use_the_exact_name_from_a_hub_line(self):
        self.assertIn('Every reply or close line uses the exact lane name from a "[hub]" line.', LIVE)

    def test_the_two_new_marker_lines_run_through_the_poller(self):
        import openclaw_pickup as pk
        import openclaw_dispatch as dp
        self.assertEqual(pk.parse_menu("lanes", {}, {}), ("lanes",))
        self.assertEqual(pk.parse_menu("stop", {}, {}), ("stop",))
        self.assertEqual(dp.parse("lanes"), ("lanes",))
        self.assertEqual(dp.parse("stop"), ("stop",))

    def test_lanes_still_close_themselves(self):
        self.assertIn("Lanes close themselves; never tell him to close one.", LIVE)


class FrontdeskEleven(unittest.TestCase):
    """frontdesk-11: a check he asked for is a start job, not a question and not "your call"."""
    def test_a_diagnosis_is_a_start_job_and_his_asking_is_the_go(self):
        self.assertIn("A check (diagnose, triage, find out why) is a start too", LIVE)
        self.assertIn("his asking is the go", LIVE)
        self.assertIn("Only a write, refund, deploy or message to someone else needs his go.", LIVE)

    def test_client_email_is_not_listed_as_a_free_question(self):
        self.assertNotIn("client email", LIVE)


class Examples(unittest.TestCase):
    """The example replies in the live section: a menu action is never refused, and each one ends with a marker the poller can run."""
    def examples(self):
        body = LIVE.split("Examples of a whole reply", 1)[1].split("\n\n", 2)[1]
        lines = body.splitlines()
        pairs = [(lines[i][2:], lines[i + 1].strip()) for i in range(len(lines) - 1) if lines[i].startswith("- ") and lines[i + 1].startswith("  Sending to the hub:")]
        self.assertEqual(len(pairs), 7, body)
        return pairs

    def test_no_menu_action_example_says_it_cannot(self):
        import re
        for say, marker in self.examples():
            self.assertNotRegex(say + " " + marker, re.compile(r"can'?t|cannot|can not|unable|not able|sorry|won't be able", re.I), say)
            self.assertTrue(say.startswith("Asking the hub "), say)  # says what it asks for, not what happened
            self.assertNotRegex(say, re.compile(r"\b(now|started|opened|done)\b", re.I), say)

    def test_every_example_ends_with_a_marker_the_poller_accepts_and_the_menu_parses(self):
        import openclaw_pickup as pk
        sites = {"porch": Path("x")}
        verbs = set()
        for say, marker in self.examples():
            m = pk.MARKER_LINE.fullmatch(marker)
            self.assertIsNotNone(m, marker)
            menu = pk.parse_menu(m.group("job"), sites, {})
            self.assertIsNotNone(menu, marker)
            verbs.add(menu[0])
        self.assertEqual(verbs, {"open_model", "status", "start", "reply", "close", "stop", "lanes"})

    def test_open_the_model_with_no_site_is_on_the_menu_and_the_text_says_never_to_ask_which(self):
        import openclaw_pickup as pk
        self.assertEqual(pk.parse_menu("open the model", {"porch": Path("x")}, {}), ("open_model", None))
        self.assertIn("Never ask which one", LIVE)
        self.assertIn("Never tell him what you cannot do", LIVE)
        self.assertIn("Already in hand", LIVE)
        self.assertIn("reports/", LIVE)


class FrontdeskEight(unittest.TestCase):
    """frontdesk-8: a reply works on a finished worker, the receptionist waits for the [hub] line, and never names an engine."""
    ENGINE = __import__("re").compile(r"codex|copilot|sonnet|haiku|\bopus\b", __import__("re").I)

    def test_reply_works_for_running_and_finished_workers(self):
        self.assertIn("adds his words to a worker's job, running or finished", LIVE)
        self.assertIn("A closed one can still take a reply.", LIVE)

    def test_it_says_what_it_sent_and_waits_for_the_hub_line(self):
        self.assertIn('Then wait for the "[hub]" line.', LIVE)

    def test_no_engine_name_in_the_section_or_after_the_engine_line_edits(self):
        self.assertNotRegex(LIVE, self.ENGINE)
        self.assertIn("Never name the engine or model a worker runs on", LIVE)
        doc = DOC.replace("rest of the file", "".join(old for old, _ in ag.ENGINE_LINES))
        self.assertRegex(doc, self.ENGINE)
        self.assertNotRegex(ag.apply_section(doc, LIVE), self.ENGINE)

    def test_the_engine_line_edits_apply_twice_without_change(self):
        doc = DOC.replace("rest of the file", "".join(old for old, _ in ag.ENGINE_LINES))
        once = ag.apply_section(doc, LIVE)
        self.assertEqual(ag.apply_section(once, LIVE), once)


CARD_DOC = ("# AGENTS\n\n## Role\n\n" + "".join(old for old, _ in ag.STATUS_LINES) + "\n## Where things live\n\nrepos\n\n"
            "## Card rules\n\nAnswer with a card.\n\n```\nROUTE: own\nTO: hub worker\n```\n\n- Not on the map: ROUTE client, TO Claude.\n")


class ReplyRules(unittest.TestCase):
    """17 Oct (frontdesk-9): live only, the ROUTE card goes, clients get status, and what the digest lacks goes to the hub."""
    def live(self, doc=CARD_DOC):
        return ag.apply_reply_rules(ag.apply_section(doc, LIVE))

    def test_the_card_rules_become_the_reply_rules_and_the_card_is_gone(self):
        out = self.live()
        self.assertNotIn("## Card rules", out)
        self.assertIn("## Reply rules", out)
        self.assertNotIn("ROUTE:", out)
        self.assertNotIn("TO: ", out)
        self.assertNotIn("```", out)
        self.assertTrue(out.rstrip().endswith("Never invent one."))
        self.assertLess(out.index("hub-ask:end"), out.index("## Reply rules"))

    def test_the_status_and_red_lines_are_swapped(self):
        out = self.live()
        for old, new in ag.STATUS_LINES:
            self.assertNotIn(old, out)
            self.assertIn(new, out)
        self.assertIn("Client projects get status too", out)
        self.assertIn("Never repeat a client's email contents, client code or credentials.", out)
        self.assertNotIn("don't read it into your model", out)
        self.assertIn("Sunday afternoon, or Tuesday evening for the co-op's servers", out)
        self.assertIn("ask if he means now", out)

    def test_applying_twice_changes_nothing_and_the_block_still_lands_before_the_reply_rules(self):
        once = self.live()
        self.assertEqual(self.live(once), once)
        stripped = ag.BLOCK.sub("", once)
        again = ag.apply_section(stripped, LIVE)
        self.assertLess(again.index("hub-ask:begin"), again.index("## Reply rules"))

    def test_a_question_the_digest_lacks_goes_to_the_hub_and_the_marker_becomes_a_question(self):
        import openclaw_pickup as pk
        rules = ag.REPLY_RULES.read_text()
        self.assertIn('end the reply with "Sending to the hub: <his question, in one sentence>"', rules)
        self.assertIn("Never guess", rules)
        self.assertIn("Anything else the status digest does not answer: his question in one sentence.", LIVE)
        self.assertNotIn("with no marker line", LIVE)
        self.assertNotIn("the card", LIVE)
        # a question is not a menu sentence: the poller sends it on as a question instead of running anything
        self.assertIsNone(pk.parse_menu("how many open issues does own-app have?", {}, {}))
        self.assertIsNone(pk.parse_menu("are own-app PRs 12 and 13 ready to merge?", {}, {}))

    def test_the_reply_rules_example_ends_with_a_marker_the_menu_parses(self):
        import openclaw_dispatch as dp
        lines = [l for l in ag.REPLY_RULES.read_text().splitlines() if l.startswith("Sending to the hub: ")]
        self.assertEqual(len(lines), 1)
        self.assertEqual(dp.parse(lines[0][len("Sending to the hub: "):])[0], "start")

    def test_only_the_live_state_gets_the_reply_rules(self):
        for state, want in (("live", True), ("notlive", False)):
            box = {"text": CARD_DOC}

            def fake(args, stdin=None):
                if args[:1] == ["cat"]:
                    return 0, box["text"]
                box["text"] = stdin
                return 0, "written\n"
            real, ag._wsl = ag._wsl, fake
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(ag.main([state]), 0)
            finally:
                ag._wsl = real
            self.assertEqual("## Reply rules" in box["text"], want, state)

    def test_the_reply_rules_name_no_engine_and_no_card_field(self):
        rules = ag.REPLY_RULES.read_text()
        for word in ("Codex", "Copilot", "Gemini", "Haiku", "Sonnet", "ROUTE:", "FOLDER:", "UNSURE:"):
            self.assertNotIn(word, rules)


class Write(unittest.TestCase):
    def test_file_content_travels_only_on_stdin(self):
        hostile = DOC + "\n__OCASK_AGENTS_EOF__\ntouch /tmp/pwned\n"
        calls, state = [], {"text": hostile}

        def fake(args, stdin=None):
            calls.append((args, stdin))
            if args[:1] == ["cat"]:
                return 0, state["text"]
            state["text"] = stdin
            return 0, "written\n"

        real, ag._wsl = ag._wsl, fake
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ag.main(["notlive"]), 0)
        finally:
            ag._wsl = real
        writes = [c for c in calls if c[0][:1] == ["bash"]]
        self.assertEqual(len(writes), 1)
        args, stdin = writes[0]
        self.assertIn("touch /tmp/pwned", stdin)
        for a in args:
            self.assertNotIn("pwned", a)
            self.assertNotIn("__OCASK_AGENTS_EOF__", a)
        self.assertNotIn("<<", args[2])


if __name__ == "__main__":
    unittest.main()
