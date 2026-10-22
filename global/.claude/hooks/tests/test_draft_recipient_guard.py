import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "draft-recipient-guard.py"
spec = importlib.util.spec_from_file_location("draft_recipient_guard", SRC)
dg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dg)

GM = "mcp__claude_ai_Gmail__"
SELF = "jonpohlner@gmail.com"


def call(tool, **args):
    return {"tool_name": GM + tool, "tool_input": args}


class Create(unittest.TestCase):
    def test_client_create_is_denied(self):
        r = dg.check(call("create_draft", to=["greg@eduity.net"], subject="s", htmlBody="<a href='https://x.digital'>x</a>"))
        self.assertIn("greg@eduity.net", r)
        self.assertIn("gmail_api.py draft-create", r)
        self.assertIn("--reply-to", r)

    def test_self_create_is_allowed(self):
        self.assertIsNone(dg.check(call("create_draft", to=[SELF], subject="[CC>MUSE] note", body="hi")))

    def test_self_with_display_name_and_case_is_allowed(self):
        self.assertIsNone(dg.check(call("create_draft", to=["Jonathan <JonPohlner@Gmail.com>"], body="hi")))

    def test_client_in_cc_or_bcc_is_denied_even_when_to_is_self(self):
        for field in ("cc", "bcc"):
            with self.subTest(field=field):
                self.assertIn("is not to Jonathan", dg.check(call("create_draft", to=[SELF], **{field: ["greg@eduity.net"]})))

    def test_lookalike_addresses_are_denied(self):
        for bad in ("jonpohlner@gmail.com.evil.io", "xjonpohlner@gmail.com", "jonpohlner@gmail.co", "a@b.co, jonpohlner@gmail.com"):
            with self.subTest(bad=bad):
                self.assertTrue(dg.check(call("create_draft", to=[bad])))

    def test_one_comma_string_is_split(self):
        self.assertTrue(dg.check(call("create_draft", to=SELF + ", greg@eduity.net")))

    def test_unaddressed_draft_is_allowed(self):
        self.assertIsNone(dg.check(call("create_draft", subject="scratch", body="x")))

    def test_reply_without_a_named_to_is_denied(self):
        self.assertIn("original sender", dg.check(call("create_draft", replyToMessageId="m1", body="x")))

    def test_reply_to_self_is_allowed(self):
        self.assertIsNone(dg.check(call("create_draft", replyToMessageId="m1", to=[SELF], body="x")))

    def test_unreadable_recipients_are_denied(self):
        for bad in ({"to": [5]}, {"to": {"a": 1}}, {"to": ["not an address"], "cc": [" "]}):
            with self.subTest(bad=bad):
                self.assertTrue(dg.check(call("create_draft", **bad)))


class Update(unittest.TestCase):
    def test_client_update_is_denied(self):
        r = dg.check(call("update_draft", draftId="r1", to=["greg@eduity.net"], htmlBody="<p>x</p>"))
        self.assertIn("greg@eduity.net", r)
        self.assertIn("draft-create", r)

    def test_update_that_names_self_is_allowed(self):
        self.assertIsNone(dg.check(call("update_draft", draftId="r1", to=[SELF], body="x")))

    def test_update_without_recipients_is_denied_it_may_be_a_clients_draft(self):
        self.assertIn("existing recipients", dg.check(call("update_draft", draftId="r1", htmlBody="<p>x</p>")))


class Untouched(unittest.TestCase):
    def test_other_tools_pass(self):
        for p in (
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            call("list_drafts", query="to:greg@eduity.net"),
            call("get_draft", draftId="r1"),
            call("search_threads", query="from:greg@eduity.net"),
            {"tool_name": "Bash", "tool_input": {"command": "python3 ~/.claude/scripts/gmail_api.py draft-create --to greg@eduity.net"}},
            {},
        ):
            with self.subTest(p=p):
                self.assertIsNone(dg.check(p))


class Cli(unittest.TestCase):
    def run_guard(self, stdin):
        return subprocess.run([sys.executable, str(SRC)], input=stdin, capture_output=True, text=True)

    def test_exit_codes(self):
        deny = self.run_guard(json.dumps(call("create_draft", to=["greg@eduity.net"])))
        self.assertEqual(deny.returncode, 2)
        self.assertTrue(deny.stderr.startswith("BLOCKED:"))
        self.assertEqual(self.run_guard(json.dumps(call("create_draft", to=[SELF]))).returncode, 0)
        self.assertEqual(self.run_guard(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})).returncode, 0)

    def test_garbage_payload_fails_closed(self):
        for s in ("", "not json", "[1]"):
            with self.subTest(s=s):
                self.assertEqual(self.run_guard(s).returncode, 2)


class Wiring(unittest.TestCase):
    """The live ~/.claude/hooks/block-outbound.sh (private, so not in this repo) must hand the draft tools to the guard.
    No skip: if the hook or the installed guard is missing, the wiring is broken and this fails."""

    HOOK = Path.home() / ".claude" / "hooks" / "block-outbound.sh"

    def run_hook(self, payload):
        return subprocess.run(["bash", str(self.HOOK)], input=json.dumps(payload), capture_output=True, text=True)

    def test_installed_guard_is_this_guard(self):
        self.assertEqual((self.HOOK.parent / "draft-recipient-guard.py").read_text(), SRC.read_text())

    def test_client_create_and_update_are_denied_by_the_hook(self):
        for tool, args in (("create_draft", {"to": ["greg@eduity.net"]}), ("update_draft", {"draftId": "r1", "to": ["greg@eduity.net"]})):
            with self.subTest(tool=tool):
                r = self.run_hook(call(tool, **args))
                self.assertEqual(r.returncode, 2)
                self.assertIn("gmail_api.py draft-create", r.stderr)

    def test_self_draft_and_bash_pass_the_hook(self):
        self.assertEqual(self.run_hook(call("create_draft", to=[SELF], body="x")).returncode, 0)
        self.assertEqual(self.run_hook(call("update_draft", draftId="r1", to=[SELF])).returncode, 0)
        self.assertEqual(self.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}).returncode, 0)

    def test_the_send_block_is_unchanged(self):
        for tool in ("send_message", "reply", "forward"):
            with self.subTest(tool=tool):
                self.assertEqual(self.run_hook(call(tool)).returncode, 2)


if __name__ == "__main__":
    unittest.main()
