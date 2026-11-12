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
SELF = "owner@example.com"  # a placeholder; the guard reads the owner's real address at run time from its private config

_cfg_dir = None
_old_env = None


def setUpModule():
    """The guard reads the owner's address from a private file. These tests point it at a temporary one holding the placeholder."""
    global _cfg_dir, _old_env
    import tempfile
    _cfg_dir = tempfile.TemporaryDirectory()
    path = Path(_cfg_dir.name) / "identity-guard.json"
    path.write_text(json.dumps({"draft_recipient": SELF}))
    _old_env = os.environ.get("IDENTITY_GUARD_CONFIG")
    os.environ["IDENTITY_GUARD_CONFIG"] = str(path)


def tearDownModule():
    if _old_env is None:
        os.environ.pop("IDENTITY_GUARD_CONFIG", None)
    else:
        os.environ["IDENTITY_GUARD_CONFIG"] = _old_env
    _cfg_dir.cleanup()


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


class OwnerAddressAtRunTime(unittest.TestCase):
    """The owner's address lives in ~/.config/afa/identity-guard.json, never in the repo. Without it the guard cannot tell his drafts from a
    client's, so it denies every draft (fail closed)."""

    def with_config(self, text):
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "identity-guard.json"
        if text is not None:
            p.write_text(text)
        old = os.environ["IDENTITY_GUARD_CONFIG"]
        self.addCleanup(os.environ.__setitem__, "IDENTITY_GUARD_CONFIG", old)
        os.environ["IDENTITY_GUARD_CONFIG"] = str(p)

    def test_a_missing_file_denies_even_a_draft_to_the_placeholder(self):
        self.with_config(None)
        r = dg.check(call("create_draft", to=[SELF]))
        self.assertIn("identity-guard.json", r)

    def test_a_file_without_the_key_or_unreadable_denies(self):
        for text in ("{}", "not json", "[1]", '{"draft_recipient": 5}', '{"draft_recipient": "no-at-sign"}', '{"addresses": ["' + SELF + '"]}'):
            with self.subTest(text=text):
                self.with_config(text)
                self.assertTrue(dg.check(call("create_draft", to=[SELF])))

    def test_the_address_in_the_file_is_the_one_allowed(self):
        self.with_config(json.dumps({"draft_recipient": "Someone.Else@Example.NET"}))
        self.assertIsNone(dg.check(call("create_draft", to=["someone.else@example.net"])))
        self.assertIn("is not to Jonathan", dg.check(call("create_draft", to=[SELF])))

    def test_the_source_carries_no_address_of_its_own(self):
        import re
        found = set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", SRC.read_text()))
        self.assertEqual(found, set(), "the guard must not hold an address: it reads it from the private config")


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


class Wiring(unittest.TestCase):
    """The hook must hand the draft tools to the guard. A runner has no private ~/.claude/hooks/block-outbound.sh, so this runs the
    hook's draft/send arms from tests/fixtures/block-outbound.case.sh in a temporary HOME beside a copy of the guard. That the fixture still
    matches the live hook is checked where the live hook is: install_scripts.py refuses to install when the two case blocks differ."""

    def setUp(self):
        import shutil, tempfile
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        hooks = self.home / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy(SRC, hooks / "draft-recipient-guard.py")
        shutil.copy(FIXTURE, hooks / "block-outbound.sh")
        self.hook = hooks / "block-outbound.sh"
        cfg = self.home / ".config" / "afa"
        cfg.mkdir(parents=True)
        (cfg / "identity-guard.json").write_text(json.dumps({"draft_recipient": SELF}))

    def run_hook(self, payload):
        env = {**os.environ, "HOME": str(self.home)}
        env.pop("IDENTITY_GUARD_CONFIG", None)  # the guard must find the config under this HOME
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
    """Runs the hook installed at $HOME/.claude/hooks/block-outbound.sh. No skip: if the hook or the installed guard is missing, the wiring is
    broken and this fails. Where HOME is the real home (a run outside the closed room) this tests the live hook; the CI job installs the fixture and the repo's guard
    into its temporary HOME first, so there it runs the same behaviour against the stand-in. (That the fixture still matches the live hook, and
    that the hand copy of the guard is the repo's file, are checked by install_scripts.py, not here: on a runner the job's copy is made from
    the repo, so either comparison would be a file against itself.)

    Ticket 81: a pytest run on a developer's machine now happens inside the closed room, where the real home cannot be reached, so the live
    hook is not there to run. setUpClass then puts the same stand-in the CI job installs into the room's throwaway home. It writes only when
    the home is a proven room (the room's own marker file is in it); anywhere else a missing hook still fails, as before."""

    HOOK = Path.home() / ".claude" / "hooks" / "block-outbound.sh"

    @classmethod
    def setUpClass(cls):
        import pwd
        import shutil
        home = Path.home().resolve()
        account = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        in_room = os.environ.get("CLOSED_ROOM") == "1" and home == account and (home / ".closed-room").exists()
        if not in_room or cls.HOOK.exists():
            return
        cls.HOOK.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE, cls.HOOK)
        shutil.copy(SRC, cls.HOOK.parent / "draft-recipient-guard.py")
        cfg = home / ".config" / "afa" / "identity-guard.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"draft_recipient": SELF}) + "\n")

    def setUp(self):
        # The live guard reads the owner's address from this HOME's private config. No skip: a missing file means the wiring is broken.
        cfg = Path.home() / ".config" / "afa" / "identity-guard.json"
        self.assertTrue(cfg.is_file(), f"{cfg} is missing: the live draft guard has no owner address and denies every draft")
        self.me = json.loads(cfg.read_text())["draft_recipient"]

    def run_hook(self, payload):
        env = {k: v for k, v in os.environ.items() if k != "IDENTITY_GUARD_CONFIG"}
        return subprocess.run(["bash", str(self.HOOK)], input=json.dumps(payload), capture_output=True, text=True, env=env)

    def test_client_create_and_update_are_denied_by_the_live_hook(self):
        for tool, args in (("create_draft", {"to": ["client@example.org"]}), ("update_draft", {"draftId": "r1", "to": ["client@example.org"]})):
            with self.subTest(tool=tool):
                r = self.run_hook(call(tool, **args))
                self.assertEqual(r.returncode, 2)
                self.assertIn("gmail_api.py draft-create", r.stderr)

    def test_self_draft_and_bash_pass_the_live_hook(self):
        self.assertEqual(self.run_hook(call("create_draft", to=[self.me], body="x")).returncode, 0)
        self.assertEqual(self.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}).returncode, 0)

    def test_the_live_send_block_is_unchanged(self):
        for tool in ("send_message", "reply", "forward"):
            with self.subTest(tool=tool):
                self.assertEqual(self.run_hook(call(tool)).returncode, 2)


if __name__ == "__main__":
    unittest.main()
