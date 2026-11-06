import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "draft-recipient-guard.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "block-outbound.case.sh"
spec = importlib.util.spec_from_file_location("draft_recipient_guard", SRC)
dg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dg)

GM = "mcp__claude_ai_Gmail__"
SELF = "owner@example.com"


def call(tool, **args):
    return {"tool_name": GM + tool, "tool_input": args}


class Create(unittest.TestCase):
    def test_client_create_is_denied(self):
        r = dg.check(call("create_draft", to=["client@example.org"], subject="s", htmlBody="<a href='https://x.digital'>x</a>"))
        self.assertIn("client@example.org", r)
        self.assertIn("gmail_api.py draft-create", r)
        self.assertIn("--reply-to", r)

    def test_self_create_is_allowed(self):
        self.assertIsNone(dg.check(call("create_draft", to=[SELF], subject="[CC>MUSE] note", body="hi")))

    def test_self_with_display_name_and_case_is_allowed(self):
        self.assertIsNone(dg.check(call("create_draft", to=["Jonathan <owner@example.com>"], body="hi")))

    def test_client_in_cc_or_bcc_is_denied_even_when_to_is_self(self):
        for field in ("cc", "bcc"):
            with self.subTest(field=field):
                self.assertIn("is not to Jonathan", dg.check(call("create_draft", to=[SELF], **{field: ["client@example.org"]})))

    def test_lookalike_addresses_are_denied(self):
        for bad in ("owner@example.com.evil.io", "xowner@example.com", "owner@example.co", "a@b.co, owner@example.com"):
            with self.subTest(bad=bad):
                self.assertTrue(dg.check(call("create_draft", to=[bad])))

    def test_one_comma_string_is_split(self):
        self.assertTrue(dg.check(call("create_draft", to=SELF + ", client@example.org")))

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
        r = dg.check(call("update_draft", draftId="r1", to=["client@example.org"], htmlBody="<p>x</p>"))
        self.assertIn("client@example.org", r)
        self.assertIn("draft-create", r)

    def test_update_that_names_self_is_allowed(self):
        self.assertIsNone(dg.check(call("update_draft", draftId="r1", to=[SELF], body="x")))

    def test_update_without_recipients_is_denied_it_may_be_a_clients_draft(self):
        self.assertIn("existing recipients", dg.check(call("update_draft", draftId="r1", htmlBody="<p>x</p>")))


class Untouched(unittest.TestCase):
    def test_other_tools_pass(self):
        for p in (
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            call("list_drafts", query="to:client@example.org"),
            call("get_draft", draftId="r1"),
            call("search_threads", query="from:client@example.org"),
            {"tool_name": "Bash", "tool_input": {"command": "python3 ~/.claude/scripts/gmail_api.py draft-create --to client@example.org"}},
            {},
        ):
            with self.subTest(p=p):
                self.assertIsNone(dg.check(p))


class Cli(unittest.TestCase):
    def run_guard(self, stdin):
        return subprocess.run([sys.executable, str(SRC)], input=stdin, capture_output=True, text=True)

    def test_exit_codes(self):
        deny = self.run_guard(json.dumps(call("create_draft", to=["client@example.org"])))
        self.assertEqual(deny.returncode, 2)
        self.assertTrue(deny.stderr.startswith("BLOCKED:"))
        self.assertEqual(self.run_guard(json.dumps(call("create_draft", to=[SELF]))).returncode, 0)
        self.assertEqual(self.run_guard(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})).returncode, 0)

    def test_garbage_payload_fails_closed(self):
        for s in ("", "not json", "[1]"):
            with self.subTest(s=s):
                self.assertEqual(self.run_guard(s).returncode, 2)


def case_block(text):
    """The `case "$tool" in ... esac` arm list of a block-outbound hook: the part that hands the draft tools to the guard."""
    a = text.index('case "$tool" in')
    return text[a:text.index("esac", a) + 4]


class Wiring(unittest.TestCase):
    """The hook must hand the draft tools to the guard. A runner has no private ~/.claude/hooks/block-outbound.sh, so this runs the
    hook's draft/send arms from tests/fixtures/block-outbound.case.sh in a temporary HOME beside a copy of the guard. LiveHook below checks
    the fixture still matches the live hook."""

    def setUp(self):
        import shutil, tempfile
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        hooks = self.home / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy(SRC, hooks / "draft-recipient-guard.py")
        shutil.copy(FIXTURE, hooks / "block-outbound.sh")
        self.hook = hooks / "block-outbound.sh"

    def run_hook(self, payload):
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(["bash", str(self.hook)], input=json.dumps(payload), capture_output=True, text=True, env=env)

    def test_client_create_and_update_are_denied_by_the_hook(self):
        for tool, args in (("create_draft", {"to": ["client@example.org"]}), ("update_draft", {"draftId": "r1", "to": ["client@example.org"]})):
            with self.subTest(tool=tool):
                r = self.run_hook(call(tool, **args))
                self.assertEqual(r.returncode, 2)
                self.assertIn("gmail_api.py draft-create", r.stderr)

    def test_self_draft_and_other_tools_pass_the_hook(self):
        self.assertEqual(self.run_hook(call("create_draft", to=[SELF], body="x")).returncode, 0)
        self.assertEqual(self.run_hook(call("update_draft", draftId="r1", to=[SELF])).returncode, 0)
        self.assertEqual(self.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}).returncode, 0)

    def test_the_send_block_is_unchanged(self):
        for tool in ("send_message", "reply", "forward"):
            with self.subTest(tool=tool):
                self.assertEqual(self.run_hook(call(tool)).returncode, 2)

    def test_a_missing_guard_denies(self):
        (self.home / ".claude" / "hooks" / "draft-recipient-guard.py").unlink()
        r = self.run_hook(call("create_draft", to=[SELF]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("is missing", r.stderr)

    def test_a_crashing_guard_denies(self):
        (self.home / ".claude" / "hooks" / "draft-recipient-guard.py").write_text("raise SystemExit(1)\n")
        self.assertEqual(self.run_hook(call("create_draft", to=[SELF])).returncode, 2)


class LiveHook(unittest.TestCase):
    """Tower only (CI deselects this class: a runner has no live install). No skip: if the hook or the installed guard is missing,
    the wiring is broken and this fails. Run it on the tower: python3 -m pytest global/.claude/hooks/tests."""

    HOOK = Path.home() / ".claude" / "hooks" / "block-outbound.sh"

    def run_hook(self, payload):
        return subprocess.run(["bash", str(self.HOOK)], input=json.dumps(payload), capture_output=True, text=True)

    def test_installed_guard_is_this_guard(self):
        self.assertEqual((self.HOOK.parent / "draft-recipient-guard.py").read_text(), SRC.read_text())

    def test_the_fixture_has_the_live_hooks_draft_and_send_arms(self):
        self.assertEqual(case_block(self.HOOK.read_text()), case_block(FIXTURE.read_text()))

    def test_client_create_and_update_are_denied_by_the_live_hook(self):
        for tool, args in (("create_draft", {"to": ["client@example.org"]}), ("update_draft", {"draftId": "r1", "to": ["client@example.org"]})):
            with self.subTest(tool=tool):
                r = self.run_hook(call(tool, **args))
                self.assertEqual(r.returncode, 2)
                self.assertIn("gmail_api.py draft-create", r.stderr)

    def test_self_draft_and_bash_pass_the_live_hook(self):
        self.assertEqual(self.run_hook(call("create_draft", to=[SELF], body="x")).returncode, 0)
        self.assertEqual(self.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}).returncode, 0)

    def test_the_live_send_block_is_unchanged(self):
        for tool in ("send_message", "reply", "forward"):
            with self.subTest(tool=tool):
                self.assertEqual(self.run_hook(call(tool)).returncode, 2)


if __name__ == "__main__":
    unittest.main()
