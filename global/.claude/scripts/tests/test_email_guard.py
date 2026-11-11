"""The CI email guard (.github/scripts/email_guard.py): fails on any address outside the placeholder allowlist."""
import importlib.util
import subprocess
import sys
import unittest
from tmpdirs import fresh_dir
from pathlib import Path

GUARD = Path(__file__).resolve().parents[4] / ".github" / "scripts" / "email_guard.py"
spec = importlib.util.spec_from_file_location("email_guard", GUARD)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def make_repo(case, files):
    root = Path(fresh_dir(case, prefix="email-guard-"))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def run(root):
    return subprocess.run([sys.executable, "-I", str(GUARD), str(root)], capture_output=True, text=True)


class Allowed(unittest.TestCase):
    def test_placeholder_and_noreply_addresses_pass(self):
        for a in ("a@example.com", "a@mail.example.org", "a@shop.example.test", "x@y.invalid", "a@acme.example",
                  "noreply@anthropic.com", "12345+name@users.noreply.github.com", "unit@51900.service"):
            self.assertTrue(g.allowed(a), a)

    def test_lookalike_domains_do_not_pass(self):
        # Built at run time so this file itself holds no address outside the placeholder domains.
        for d in ("example.com.evil.net", "evil-example.com", "notexample.org", "provider-one.com", "provider-two.io"):
            self.assertFalse(g.allowed("a" + "@" + d), d)

    def test_the_allow_file_adds_domains_and_single_addresses_only(self):
        extra = {"domains": {"provider-two.io"}, "addresses": {"one" + "@" + "provider-one.com"}}
        self.assertTrue(g.allowed("a" + "@sub.provider-two.io", extra))
        self.assertTrue(g.allowed("one" + "@" + "provider-one.com", extra))
        self.assertFalse(g.allowed("two" + "@" + "provider-one.com", extra))


class Cli(unittest.TestCase):
    def test_clean_repo_exits_zero(self):
        r = run(make_repo(self, {"a.py": "ME = 'owner@example.org'\n"}))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_real_looking_address_fails_and_is_never_echoed(self):
        planted = "someone.real" + "@" + "mailbox-provider.net"  # built at run time so this file holds no address
        r = run(make_repo(self, {"docs/x.md": "line one\ncontact " + planted + "\n"}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/x.md:2", r.stdout)
        self.assertNotIn(planted, r.stdout + r.stderr)
        self.assertNotIn("mailbox-provider", r.stdout + r.stderr)

    def test_allow_file_lets_a_reviewed_fixture_through(self):
        files = {"t.py": "x = 'bob" + "@client.test.corp'\n", ".github/email-guard-allow.txt": "@client.test.corp\n"}
        self.assertEqual(run(make_repo(self, files)).returncode, 0)
        files[".github/email-guard-allow.txt"] = "# nothing allowed\n"
        self.assertEqual(run(make_repo(self, files)).returncode, 1)

    def test_untracked_files_are_not_scanned(self):
        root = make_repo(self, {"ok.py": "pass\n"})
        (root / "later.txt").write_text("someone" + "@" + "mailbox-provider.net\n")
        self.assertEqual(run(root).returncode, 0)

    def test_the_real_repo_is_clean(self):
        self.assertEqual(run(GUARD.parents[2]).returncode, 0, run(GUARD.parents[2]).stdout)


if __name__ == "__main__":
    unittest.main()
