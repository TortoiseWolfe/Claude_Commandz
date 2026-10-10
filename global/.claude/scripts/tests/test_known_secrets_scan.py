import base64
import hashlib
import importlib.util
import urllib.parse
import json
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

    def test_strict_walks_skipped_folders_that_the_default_mode_skips(self):
        self.put("build/out.js", "var k = '%s';\n" % PW)
        self.assertEqual(self.run_cli(self.out).returncode, 0)
        r = subprocess.run([sys.executable, SCRIPT, "--strict", "--repos", self.repos, "--ignore", self.ignore,
                            self.out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ADMIN_PASSWORD", r.stdout)
        self.assertNotIn(PW, r.stdout + r.stderr)

    def test_chunked_read_finds_a_value_across_a_chunk_boundary_and_json_escaped(self):
        p = self.put("big.bin", "x" * 13 + PW + "y" * 50)
        found = ks.contains(p, [(PW.encode(), ("app/" + ENV, "ADMIN_PASSWORD"))], chunk=16)
        self.assertEqual(found, [("app/" + ENV, "ADMIN_PASSWORD")])
        with open(os.path.join(self.repos, "app", ENV), "a") as fh:
            fh.write("SMTP_PASSWORD='Ab1\"Cd2Cd2Cd2Cd2'\n")
        self.put("log.jsonl", json.dumps({"out": 'Ab1"Cd2Cd2Cd2Cd2'}) + "\n")
        r = self.run_cli(self.out)
        self.assertIn("SMTP_PASSWORD", r.stdout)

    def test_every_dotenv_reading_is_collected(self):
        # Readers disagree on inline '#', quotes and multi-line values; each plausible reading counts.
        vals = {v for _, v in ks.env_values('A="Hx5 #Kq8Kq8Kq8Kq8"\nB=Jt3Jt3Jt3 #Wm6Wm6Wm6\n'
                                              'C="Rv2Rv2\nRv2Rv2Rv2"\n')}
        self.assertIn("Hx5 #Kq8Kq8Kq8Kq8", vals)
        self.assertIn("Jt3Jt3Jt3 #Wm6Wm6Wm6", vals)
        self.assertIn("Jt3Jt3Jt3", vals)
        self.assertIn("Rv2Rv2\nRv2Rv2Rv2", vals)

    def test_env_files_by_any_common_name_and_depth(self):
        for rel in ("d/stack.env", "d/api.env", "d/.env-prod", "d/.envrc", "a/b/c/d/e/f/.env.prod"):
            self.write(rel, "X=1\n")
        self.write("d/.env.example", "X=1\n")
        names = {os.path.relpath(p, self.repos) for p in ks.env_files(self.repos)}
        for rel in ("d/stack.env", "d/api.env", "d/.env-prod", "d/.envrc", "a/b/c/d/e/f/.env.prod"):
            self.assertIn(rel, names)
        self.assertNotIn("d/.env.example", names)

    def test_which_values_count_as_secrets(self):
        hi = base64.urlsafe_b64encode(hashlib.sha256(b"fixture").digest()).decode().rstrip("=")  # token-like
        sr = "eyJ" + "sR4" * 10                         # a service-role-shaped key
        self.write("k/" + ENV,
                   "SUPABASE_SERVICE_ROLE_KEY=%s\nNEXT_PUBLIC_SUPABASE_ANON_KEY=%s\nDB_PASSWORD=Gk2Gk2Gk\n"
                   "BLOB=%s\nJAZZ_WORKER_ACCOUNT=%s\nSITE_URL=https://example.org/page\n"
                   "CALLBACK_URL=https://x.example.org/cb?token=Tq4Tq4Tq4Tq4\n"
                   "PROJECT_NAME=my-project\n" % (sr, sr + "pub", hi, hi + "acct"))
        found = ks.collect(self.repos, set())
        for v in (sr, "Gk2Gk2Gk", hi, "Tq4Tq4Tq4Tq4"):
            self.assertIn(v, found)
        for v in (sr + "pub", hi + "acct", "https://example.org/page", "my-project"):
            self.assertNotIn(v, found)

    def test_names_domains_quoted_local_urls_and_cli_temp_keys_are_not_secrets(self):
        name = "mail_smtp_" + "password_v1"
        self.write("f/" + ENV, 'MAIL_SWARM_SECRET=%s\nAPP_COOKIE_DOMAIN=.example.org\n'
                               'DATABASE_URL="postgresql://postgres:postgres@localhost:5432/x"\n'
                               'SMTP_PASSWORD=%s\n' % (name, name))
        self.write("r/supabase/.temp/env/docker" + ENV, "INTERNAL_SECRET_KEY=Lc6Lc6Lc6Lc6Lc6Lc6\n")
        found = ks.collect(self.repos, set())
        self.assertIn(name, found)                              # under a password key it IS a secret
        self.assertEqual(found[name][1], "SMTP_PASSWORD")
        for v in (".example.org", '"postgresql://postgres:postgres@localhost:5432/x"',
                  "postgresql://postgres:postgres@localhost:5432/x", "Lc6Lc6Lc6Lc6Lc6Lc6"):
            self.assertNotIn(v, found)

    def test_encoded_forms_and_non_utf8_bytes_are_found(self):
        pw = "Pw5!Pw5@Pw5#Pw5"
        self.write("e/" + ENV, "SMTP_PASSWORD='%s'\n" % pw)
        b64 = base64.b64encode(("user:" + pw).encode()).decode()
        self.put("docker-config.json", '{"auths": {"r.io": {"auth": "%s"}}}\n' % b64)
        self.put("doc.md", "postgresql://u:%s@db/x\n" % urllib.parse.quote(pw, safe=""))
        r = self.run_cli(self.out)
        self.assertEqual(r.stdout.count("SMTP_PASSWORD"), 2, r.stdout)
        raw = b"Nb8\xffNb8Nb8Nb8"
        with open(os.path.join(self.repos, "n", ENV) if os.path.isdir(os.path.join(self.repos, "n"))
                  else self.write("n/" + ENV, ""), "ab") as fh:
            fh.write(b"API_TOKEN=" + raw + b"\n")
        with open(os.path.join(self.out, "bin.dat"), "wb") as fh:
            fh.write(b"xx" + raw + b"yy")
        r = self.run_cli(os.path.join(self.out, "bin.dat"))
        self.assertIn("API_TOKEN", r.stdout)

    def test_collect_units(self):
        found = ks.collect(self.repos, ks.load_ignore(self.ignore))
        self.assertIn(PW, found)
        self.assertIn(HOOK_TOK, found)
        self.assertNotIn(FIXTURE, found)
        self.assertNotIn("c" * 20, found)
        self.assertFalse(any("localhost" in v for v in found))


if __name__ == "__main__":
    unittest.main()
