import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "known_secrets_scan.py")
spec = importlib.util.spec_from_file_location("known_secrets_scan", SCRIPT)
ks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ks)

# Built at runtime so no scanner flags this file.
PW = "Pq7" * 6                      # 18 chars, no shape gitleaks knows
HOOK_TOK = "Yz9" * 22
FIXTURE = "Fx4" * 5
ENV = "." + "env"


class Scan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repos = os.path.join(self.tmp.name, "repos")
        self.write("app/" + ENV, "ADMIN_PASSWORD=%s\nPORT=3000\nAPP_CLIENT_ID=%s\n"
                   "OPS_HOOK=https://discord.com/api/webhooks/42/%s\n"
                   "DATABASE_URL=postgresql://postgres:postgres@localhost:5432/x\n"
                   "TEST_USER_PASSWORD=%s\n" % (PW, "c" * 20, HOOK_TOK, FIXTURE))
        self.ignore = self.write("ignore.txt", "app/%s TEST_USER_PASSWORD  # committed fixture\n" % ENV)
        self.out = os.path.join(self.tmp.name, "out")

    def write(self, rel, text):
        p = os.path.join(self.tmp.name, "repos", rel) if not rel.startswith("ignore") else os.path.join(self.tmp.name, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def put(self, rel, text):
        p = os.path.join(self.out, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def run_cli(self, *paths):
        return subprocess.run([sys.executable, SCRIPT, "--repos", self.repos, "--ignore", self.ignore, *paths],
                              capture_output=True, text=True)

    def test_finds_bare_password_and_webhook_token_without_printing_them(self):
        self.put("memory/note.md", "- Login: jon / %s\n" % PW)
        self.put("plans/p.md", "post to https://discord.com/api/webhooks/42/%s\n" % HOOK_TOK)
        r = self.run_cli(self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("app/%s ADMIN_PASSWORD: " % ENV, r.stdout)
        self.assertIn("app/%s OPS_HOOK: " % ENV, r.stdout)
        for leak in (PW, HOOK_TOK):
            self.assertNotIn(leak, r.stdout + r.stderr)

    def test_clean_tree_and_non_secrets(self):
        self.put("a.md", "PORT=3000 and the client id %s and postgres:postgres@localhost and %s\n" % ("c" * 20, FIXTURE))
        r = self.run_cli(self.out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "")

    def test_no_env_files_is_an_error_not_a_pass(self):
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        r = subprocess.run([sys.executable, SCRIPT, "--repos", empty, self.out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_an_ignored_value_is_ignored_in_every_env_file(self):
        self.write("other/" + ENV, "SEED_USER_PASSWORD=%s\n" % FIXTURE)
        found = ks.collect(self.repos, ks.load_ignore(self.ignore))
        self.assertNotIn(FIXTURE, found)

    def test_collect_units(self):
        found = ks.collect(self.repos, ks.load_ignore(self.ignore))
        self.assertIn(PW, found)
        self.assertIn(HOOK_TOK, found)
        self.assertNotIn(FIXTURE, found)
        self.assertNotIn("c" * 20, found)
        self.assertFalse(any("localhost" in v for v in found))


if __name__ == "__main__":
    unittest.main()
