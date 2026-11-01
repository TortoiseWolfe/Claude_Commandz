import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scrub_known_secrets.py")
PW = "Pq7" * 6
QUOTED = 'Ab1"' + "Cd2" * 4          # a value JSON has to escape
HOOK_TOK = "Yz9" * 22
ENV = "." + "env"


class Scrub(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repos = os.path.join(self.tmp.name, "repos")
        os.makedirs(os.path.join(self.repos, "app"))
        with open(os.path.join(self.repos, "app", ENV), "w") as fh:
            fh.write("ADMIN_PASSWORD=%s\nOPS_HOOK=https://discord.com/api/webhooks/42/%s\n"
                     "SMTP_PASSWORD='%s'\nPORT=3000\n" % (PW, HOOK_TOK, QUOTED))
        self.logs = os.path.join(self.tmp.name, "logs")
        os.makedirs(self.logs)
        self.secrets = os.path.join(self.tmp.name, "secrets")
        os.makedirs(self.secrets)

    def log(self, name, records, age=600):
        p = os.path.join(self.logs, name)
        with open(p, "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def run_cli(self, *extra):
        return subprocess.run([sys.executable, SCRIPT, "--repos", self.repos, "--secrets-dir", self.secrets,
                               "--ignore", "/nonexistent",
                               "--scratch", self.tmp.name, *extra, self.logs], capture_output=True, text=True)

    def test_scrubs_raw_and_json_escaped_values_and_keeps_json_valid(self):
        p = self.log("s.jsonl", [{"out": "Login: jon / %s" % PW},
                                 {"out": "post https://discord.com/api/webhooks/42/%s" % HOOK_TOK},
                                 {"out": "smtp %s" % QUOTED}, {"out": "PORT=3000"}])
        mtime = os.stat(p).st_mtime
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        text = open(p).read()
        for leak in (PW, HOOK_TOK, json.dumps(QUOTED)[1:-1]):
            self.assertNotIn(leak, text)
            self.assertNotIn(leak, r.stdout + r.stderr)
        rows = [json.loads(l) for l in text.splitlines()]
        self.assertEqual(rows[0]["out"], "Login: jon / <redacted:ADMIN_PASSWORD>")
        self.assertIn("<redacted:OPS_HOOK>", rows[1]["out"])
        self.assertEqual(rows[2]["out"], "smtp <redacted:SMTP_PASSWORD>")
        self.assertEqual(rows[3]["out"], "PORT=3000")
        self.assertAlmostEqual(os.stat(p).st_mtime, mtime, places=3)
        self.assertIn("ADMIN_PASSWORD: 1", r.stdout)

    def test_recent_files_are_skipped_and_dry_run_changes_nothing(self):
        recent = self.log("live.jsonl", [{"out": PW}], age=0)
        old = self.log("old.jsonl", [{"out": PW}])
        r = self.run_cli("--dry-run")
        self.assertIn("would scrub 1 file(s)", r.stdout)
        self.assertIn("skipped 1 file(s)", r.stdout)
        self.assertIn(PW, open(old).read())
        self.run_cli()
        self.assertIn(PW, open(recent).read())
        self.assertNotIn(PW, open(old).read())
        self.assertEqual([f for f in os.listdir(self.tmp.name) if f.startswith(".scrub-patterns-")], [])

    def test_scrubs_secret_values_from_the_overridden_secrets_directory(self):
        value = "K1m3P5r7" + "T9v2X4z6" + "B8d0F2h5"
        with open(os.path.join(self.secrets, "stream.key"), "w") as fh:
            fh.write(value + "\n")
        path = self.log("process.jsonl", [{"out": "ffmpeg rtmp://host/app/" + value}])
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = open(path).read()
        self.assertNotIn(value, text)
        self.assertIn("<redacted:stream.key>", text)
        self.assertNotIn(value, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
