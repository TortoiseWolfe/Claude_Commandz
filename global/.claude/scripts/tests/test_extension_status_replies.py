"""/extension-status --draft-replies: classify each outside issue or pull request and draft a reply (issue 1).
The command pipes the open issues and pull requests it already fetched to extension_status_replies.py, which drops the
owner's own items, sorts each remaining one into question, bug-report, feature-request, install-issue or docs-unclear,
prints a suggested reply tailored to that class, and flags items where the last word is an outsider's and older than 48 hours.
It only prints. Every fixture here is invented."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "extension_status_replies.py"
sys.path.insert(0, str(HERE.parent))
import extension_status_replies as rep  # noqa: E402

OWNER = "TortoiseWolfe"
NOW = "2026-11-06 12:00"


def item(number, title, body="", author="visitor", created="2026-11-06T10:00:00Z", comments=(), url=None):
    return {
        "number": number, "title": title, "body": body, "author": {"login": author}, "createdAt": created,
        "url": url or f"https://github.com/o/r/issues/{number}",
        "comments": [{"author": {"login": a}, "createdAt": t, "body": b} for a, t, b in comments],
    }


def run(issues=(), prs=(), extra=(), now=NOW):
    env = dict(os.environ, EXTENSION_STATUS_NOW=now)
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--owner", OWNER, *extra],
        input=json.dumps({"issues": list(issues), "prs": list(prs)}), capture_output=True, text=True, env=env,
    )
    return p


class Classify(unittest.TestCase):
    def kind(self, title, body="", is_pr=False):
        return rep.classify(title, body, is_pr)

    def test_install_problem(self):
        self.assertEqual(self.kind("Can't install the extension", "pip install fails on my machine"), "install-issue")

    def test_install_wins_over_error_words(self):
        self.assertEqual(self.kind("Error during installation", "traceback when I run the install"), "install-issue")

    def test_bug_report(self):
        self.assertEqual(self.kind("Wireframe command crashes", "I get a traceback and it stops"), "bug-report")

    def test_feature_request(self):
        self.assertEqual(self.kind("Support for dark mode", "It would be nice if you could add this"), "feature-request")

    def test_docs_unclear(self):
        self.assertEqual(self.kind("README is confusing", "The usage section is unclear about flags"), "docs-unclear")

    def test_question(self):
        self.assertEqual(self.kind("How do I change the output folder?", ""), "question")

    def test_plain_default_is_question(self):
        self.assertEqual(self.kind("Hello", "just saying hi"), "question")

    def test_a_pull_request_about_a_typo_is_docs(self):
        self.assertEqual(self.kind("Fix typo in README", "", True), "docs-unclear")

    def test_a_pull_request_with_a_plain_title_is_a_feature(self):
        self.assertEqual(self.kind("Add export to PDF", "", True), "feature-request")

    def test_a_word_inside_another_word_does_not_count(self):
        # "debug" is not "bug", "errors" aside, "prerror" is nothing
        self.assertEqual(self.kind("Debugging tips", "any advice on debugging?"), "question")


class Waiting(unittest.TestCase):
    def test_outsider_last_over_48h_is_waiting(self):
        out = run([item(5, "Why slow?", created="2026-11-03T12:00:00Z")]).stdout
        self.assertIn("waiting 3d", out)

    def test_outsider_last_under_48h_is_not_flagged(self):
        out = run([item(5, "Why slow?", created="2026-11-05T13:00:00Z")]).stdout
        self.assertNotIn("⚠ waiting", out)

    def test_exactly_48h_is_not_yet_flagged(self):
        out = run([item(5, "Why slow?", created="2026-11-04T12:00:00Z")]).stdout
        self.assertNotIn("⚠ waiting", out)

    def test_last_comment_by_outsider_counts_even_if_owner_spoke_earlier(self):
        i = item(5, "Why slow?", created="2026-11-01T12:00:00Z", comments=[
            (OWNER, "2026-11-01T13:00:00Z", "Looking"), ("visitor", "2026-11-03T08:00:00Z", "Any news?")])
        self.assertIn("waiting 3d", run([i]).stdout)

    def test_owner_replied_last_is_not_waiting(self):
        i = item(5, "Why slow?", created="2026-10-20T12:00:00Z", comments=[(OWNER, "2026-10-21T12:00:00Z", "Fixed")])
        out = run([i]).stdout
        self.assertNotIn("⚠ waiting", out)
        self.assertIn("owner replied last", out)

    def test_comments_out_of_order_use_the_newest(self):
        i = item(5, "Why slow?", created="2026-10-20T12:00:00Z", comments=[
            (OWNER, "2026-11-05T12:00:00Z", "Done"), ("visitor", "2026-10-25T12:00:00Z", "ping")])
        self.assertNotIn("⚠ waiting", run([i]).stdout)

    def test_hours_under_three_days(self):
        out = run([item(5, "Why slow?", created="2026-11-04T06:00:00Z")]).stdout
        self.assertIn("waiting 54h", out)


class Output(unittest.TestCase):
    def test_owner_items_are_dropped(self):
        out = run([item(1, "mine", author=OWNER), item(2, "How do I use it?")]).stdout
        self.assertNotIn("#1 ", out)
        self.assertIn("#2 ", out)

    def test_owner_match_ignores_case(self):
        out = run([item(1, "mine", author=OWNER.lower())]).stdout
        self.assertIn("No inbound items", out)

    def test_empty_says_so(self):
        p = run([])
        self.assertEqual(p.returncode, 0)
        self.assertIn("No inbound items", p.stdout)

    def test_each_item_has_a_class_and_a_suggested_reply(self):
        out = run([item(7, "Can't install it", "pip install breaks")]).stdout
        self.assertIn("#7", out)
        self.assertIn("[install-issue]", out)
        self.assertIn("Suggested reply", out)

    def test_the_reply_is_tailored_to_the_class(self):
        bug = run([item(1, "It crashes", "traceback")]).stdout
        inst = run([item(2, "Install fails", "pip install breaks")]).stdout
        self.assertIn("steps to reproduce", bug.lower())
        self.assertIn("README", inst)
        self.assertNotIn("steps to reproduce", inst.lower())

    def test_pull_requests_get_a_pull_request_reply(self):
        out = run(prs=[item(9, "Add export to PDF", url="https://github.com/o/r/pull/9")]).stdout
        self.assertIn("pull request", out.lower())
        self.assertIn("[feature-request]", out)

    def test_the_url_is_printed(self):
        out = run([item(7, "How?", url="https://github.com/o/r/issues/7")]).stdout
        self.assertIn("https://github.com/o/r/issues/7", out)

    def test_oldest_waiting_first(self):
        out = run([item(2, "How b?", created="2026-11-05T00:00:00Z"), item(1, "How a?", created="2026-10-01T00:00:00Z")]).stdout
        self.assertLess(out.index("#1 "), out.index("#2 "))

    def test_never_prints_a_bare_at_handle(self):
        i = item(3, "Thanks @someone for the idea", "cc @other", author="visitor")
        out = run([i]).stdout
        self.assertNotRegex(out, r"@(?!​)\w")

    def test_the_draft_never_tags_the_author(self):
        out = run([item(3, "How do I?", author="someuser")]).stdout
        self.assertNotIn("@someuser", out)

    def test_body_text_is_not_copied_into_the_output(self):
        out = run([item(3, "How do I?", body="SECRET-LOOKING-BODY-TEXT")]).stdout
        self.assertNotIn("SECRET-LOOKING-BODY-TEXT", out)

    def test_items_with_missing_fields_do_not_crash(self):
        p = run([{"number": 4, "title": "No author or body"}])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("#4", p.stdout)

    def test_a_null_body_is_fine(self):
        i = item(4, "How?")
        i["body"] = None
        self.assertEqual(run([i]).returncode, 0)

    def test_bad_json_exits_2(self):
        p = subprocess.run([sys.executable, str(SCRIPT), "--owner", OWNER], input="not json", capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertNotIn("Traceback", p.stderr)

    def test_missing_owner_exits_2(self):
        p = subprocess.run([sys.executable, str(SCRIPT)], input="{}", capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)

    def test_summary_line_counts_waiting_items(self):
        out = run([item(1, "How a?", created="2026-10-01T00:00:00Z"), item(2, "How b?", created="2026-11-06T11:00:00Z")]).stdout
        self.assertIn("2 outside items, 1 waiting over 48h", out)


class CommandFile(unittest.TestCase):
    def test_the_command_documents_the_flag_and_the_helper(self):
        text = (HERE.parent.parent / "commands" / "extension-status.md").read_text()
        self.assertIn("--draft-replies", text)
        self.assertIn("extension_status_replies.py", text)
        self.assertIn("Suggested reply", text)


if __name__ == "__main__":
    unittest.main()
